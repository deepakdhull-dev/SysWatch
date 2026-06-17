"""
assembler.py — Collects raw data from all collectors, assembles MetricFrame
=====================================================================

WHAT THIS FILE DOES:
    Runs all collectors on their individual schedules (CPU every 1s, disk
    every 10s, etc.), holds the latest reading from each collector in memory,
    and on demand produces a single complete MetricFrame dataclass combining
    all of them.

    Think of it as a "latest value cache" with a periodic refresh loop
    per collector. The sampler asks "give me a frame" and the assembler
    says "here's the most recent data I have for every metric."

WHY ASSEMBLER IS SEPARATE FROM SAMPLER:
    Assembler's job: "gather and combine data from multiple sources"
    Sampler's job:   "decide when to emit a frame downstream"

    These are different concerns. If you mix them:
        - Testing becomes hard: you can't test frame shape without also
          testing timing logic
        - Adding a new collector means editing timing logic
        - The class becomes a god object

    Separate = each is independently testable and modifiable.

WHY PER-COLLECTOR INTERVALS INSTEAD OF ONE GLOBAL LOOP:
    A single global loop at 1s would collect CPU (cheap) and disk (cheap but
    pointless — disk doesn't change per second) at the same rate. With
    individual intervals, disk runs every 10s, CPU every 1s. This is how
    real monitoring agents (Telegraf, Node Exporter) work.

HOW THE LATEST-VALUE CACHE WORKS:
    Each collector has its own asyncio task running in the background.
    The task runs collector.collect(), stores the result in a dict
    (self._latest), sleeps for the collector's interval, repeats.

    When assemble_frame() is called, it reads from self._latest —
    whatever the most recent value is for each metric. No blocking,
    no waiting for collectors to finish.

    This means if disk hasn't been collected yet (first 10s), the frame
    has None for disk data. The assembler handles this gracefully.

REUSE IN FUTURE SERVICES (infrawatch, logai):
    infrawatch's agent would have an assembler that collects:
        - Application metrics from /metrics endpoints (HTTP poll)
        - Container stats from Docker socket
        - K8s pod status from kube-apiserver
    Same pattern: per-source tasks → latest-value cache → assemble on demand.

    logai's shipper would have something similar but simpler:
        - File tail positions (one task per watched file)
        - Assembled into a LogBatch instead of MetricFrame

    The assembler pattern = "fan-in from multiple async sources into one output"
    This is fundamental to any agent/shipper/collector service.

INTERFACE CONTRACT WITH SAMPLER:
    Sampler calls: frame = await assembler.assemble_frame()
    Returns: MetricFrame dataclass, or None if insufficient data yet
    Sampler does not care HOW the frame was assembled — only what it gets back.
    This interface isolation means you can swap the assembler implementation
    without touching the sampler.
"""

from __future__ import annotations

import asyncio
import logging
import time
import uuid
from typing import Optional

# Import collector classes — these do the actual /proc reading
from collectors import (
    CPUCollector,
    DiskCollector,
    LoadCollector,
    NetworkCollector,
    RAMCollector,
    ServiceCollector,
)

# Import config types — assembler needs intervals and agent_id
# detect_default_interface is imported from config because it's a
# pure utility function that doesn't belong to any class.
# It's in config.py because config is the "startup" module that
# deals with system-level detection at initialization time.
from config import Config, detect_default_interface

# Import the dataclasses that define our internal data shapes.
# These are the Python-side types — NOT protobuf types.
# The encoder (encoder.py) converts these to protobuf.
# Keeping Python types separate from protobuf types means:
#   - You can test assembler without protobuf installed
#   - You can change proto schema without touching assembler
#   - The dataclasses are readable Python; proto generated code is not
#
# WHY collectors/base.py AND NOT A SEPARATE common PACKAGE:
#   The dataclasses describe exactly what the collectors produce.
#   They live next to the collectors because that's where their shape
#   is defined and owned. The assembler, sampler, and encoder all
#   import from here — they consume what collectors produce.
#   No separate shared package needed: agent owns these types end-to-end.
#   The server never imports these — it works directly with proto types.
from syswatch_agent.collectors.base import (
    DiskUsage,
    MetricFrame,
    NetworkInterface,
    ServiceStatus,
)

logger = logging.getLogger(__name__)


class Assembler:
    """
    Manages all collectors, maintains latest readings, produces MetricFrames.

    LIFECYCLE:
        1. __init__: creates collector instances, initializes state
        2. start(): launches one asyncio task per collector
        3. assemble_frame(): called by sampler, returns current MetricFrame
        4. stop(): cancels all tasks on shutdown

    WHY NOT __aenter__/__aexit__ (async context manager):
        You could use `async with Assembler(cfg) as a:` but then the sampler
        would have to be nested inside the context. Using explicit start/stop
        gives main.py cleaner control over startup/shutdown order.
        Both patterns are valid — this is a deliberate choice.

    THREAD SAFETY:
        All access to self._latest happens in the same event loop thread
        (asyncio is single-threaded). No locks needed. If you ever move
        collectors to a ThreadPoolExecutor (e.g. for blocking /proc reads
        that take >1ms), you'd need asyncio.Lock() around self._latest writes.
        For now, pure asyncio — no locks needed.
    """

    def __init__(self, cfg: Config) -> None:
        """
        cfg: The fully loaded Config dataclass from config.py.
             Assembler reads cfg.agent.agent_id, cfg.collector.* intervals,
             and cfg.agent.services list.
        """
        self._cfg = cfg

        # Latest-value cache.
        # Keys are metric names (strings), values are the most recent reading.
        # Initialized to None — signals "not yet collected".
        # assemble_frame() handles None values gracefully.
        self._latest: dict = {
            "cpu_pct": None,  # float | None
            "ram": None,  # dict | None  (keys: mem_total, mem_used, etc.)
            "disk": None,  # dict | None  (keys: mount_point, used, total, pct)
            "load": None,  # dict | None  (keys: load_1m, load_5m, load_15m)
            "network": None,  # dict | None  (keys: bytes_in, bytes_out, etc.)
            "services": None,  # dict | None  (keys: service_name → bool)
        }

        # Instantiate collectors.
        # NetworkCollector is initialized without an interface name here —
        # we detect the interface per-collection-cycle (see _collect_network).
        # WHY: interface can change (eth0 → wlan0). We re-detect each cycle.
        self._cpu = CPUCollector()
        self._ram = RAMCollector()
        self._disk = DiskCollector()
        self._load = LoadCollector()
        self._service = ServiceCollector(cfg.agent.services)

        # NetworkCollector is stateful (tracks prev values for delta).
        # When interface changes, prev values are for the OLD interface —
        # the delta would be wrong for one cycle. This is acceptable:
        # one bad sample on interface switch, then correct delta resumes.
        # Alternative: recreate NetworkCollector on each interface change.
        # Chosen approach: simpler, one bad sample is not a crisis.
        self._network_iface: Optional[str] = None
        self._network_collector: Optional[NetworkCollector] = None

        # asyncio tasks for background collection loops.
        # Stored so we can cancel them on shutdown.
        self._tasks: list[asyncio.Task] = []

    # -----------------------------------------------------------------------
    # Lifecycle
    # -----------------------------------------------------------------------

    async def start(self) -> None:
        """
        Launches one asyncio background task per collector.
        Each task loops forever: collect → store → sleep → repeat.

        WHAT IS AN asyncio TASK:
            asyncio.create_task() schedules a coroutine to run concurrently
            with other coroutines on the same event loop thread. It's not
            a thread — there's no parallelism. The event loop switches
            between tasks at every `await` point (like `await asyncio.sleep()`).

            This means all collector loops run "simultaneously" but never
            truly in parallel. For /proc reads (fast, non-blocking), this
            is fine. If a collector called a blocking function (no await),
            it would freeze all other tasks until it returned.

        FUTURE SERVICE NOTE:
            Any agent with multiple independent data sources uses this pattern.
            infrawatch might have tasks for: HTTP scrape loop, Docker stats loop,
            kube-api poll loop. Each task is independent, has its own interval,
            writes to the shared latest-value cache.
        """
        self._tasks = [
            asyncio.create_task(
                self._collect_loop(
                    "cpu", self._collect_cpu, self._cfg.collector.cpu_interval
                ),
                name="collector-cpu",
            ),
            asyncio.create_task(
                self._collect_loop(
                    "ram", self._collect_ram, self._cfg.collector.ram_interval
                ),
                name="collector-ram",
            ),
            asyncio.create_task(
                self._collect_loop(
                    "disk", self._collect_disk, self._cfg.collector.disk_interval
                ),
                name="collector-disk",
            ),
            asyncio.create_task(
                self._collect_loop(
                    "load", self._collect_load, self._cfg.collector.load_interval
                ),
                name="collector-load",
            ),
            asyncio.create_task(
                self._collect_loop(
                    "network",
                    self._collect_network,
                    self._cfg.collector.network_interval,
                ),
                name="collector-network",
            ),
            asyncio.create_task(
                self._collect_loop(
                    "services",
                    self._collect_services,
                    self._cfg.collector.service_interval,
                ),
                name="collector-services",
            ),
        ]
        logger.info("Assembler started %d collector tasks", len(self._tasks))

    async def stop(self) -> None:
        """
        Cancels all background collector tasks.
        Called by main.py on SIGTERM/SIGINT before process exits.

        WHY CANCEL AND NOT JUST LET THE PROCESS DIE:
            Uncancelled tasks on process exit can leave /proc file handles open,
            or (in more complex cases) leave network sockets in TIME_WAIT.
            Explicit cancellation is clean. asyncio.CancelledError is raised
            at the next `await` point inside the task, which unwinds the stack.
        """
        for task in self._tasks:
            task.cancel()
        # Wait for all tasks to acknowledge cancellation
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("Assembler stopped all collector tasks")

    # -----------------------------------------------------------------------
    # Generic collection loop
    # -----------------------------------------------------------------------

    async def _collect_loop(
        self,
        key: str,
        collect_fn,
        interval: float,
    ) -> None:
        """
        Generic background loop: call collect_fn(), store in self._latest[key],
        sleep for interval seconds, repeat.

        WHY GENERIC:
            All six collectors have the same loop structure. A generic loop
            function means we write the error handling, sleep logic, and
            logging once. If we fix a bug here, all six collectors benefit.

        WHY try/except AROUND collect_fn():
            If CPUCollector.collect() raises an unexpected exception
            (e.g. /proc/stat format changed in a kernel update), we don't
            want the entire agent to crash. We log the error and continue.
            The latest value stays at whatever it was before — stale but
            not catastrophic. The next cycle will try again.

            This is the "let it be wrong briefly rather than crash" philosophy
            of resilient agents. For a monitoring agent, being down is worse
            than having one stale metric for one cycle.

        key: which key in self._latest to update
        collect_fn: async or sync callable that returns new data
        interval: seconds to sleep between calls
        """
        logger.debug(
            "Starting collection loop for '%s' (interval=%.1fs)", key, interval
        )
        while True:
            try:
                # collect_fn may be sync (all current collectors are sync)
                # We run sync collectors directly — they're fast /proc reads.
                # If you ever add a collector that does network I/O (e.g. HTTP
                # scrape), make it async and await it here:
                #   result = await collect_fn()
                result = collect_fn()

                # CPUCollector returns None on the first call (needs two
                # samples to compute a delta). Don't overwrite a valid
                # previous reading with None.
                if result is not None:
                    self._latest[key] = result

            except Exception as exc:
                # Log but don't crash. Monitoring agents must be resilient.
                logger.warning(
                    "Collector '%s' raised %s: %s", key, type(exc).__name__, exc
                )

            # asyncio.sleep yields control back to the event loop,
            # allowing other tasks (other collector loops, the sampler,
            # the streamer) to run while we wait.
            await asyncio.sleep(interval)

    # -----------------------------------------------------------------------
    # Per-collector collection functions
    # Called by _collect_loop — each returns the raw data for its metric.
    # -----------------------------------------------------------------------

    def _collect_cpu(self):
        """Returns float (CPU%) or None if first sample."""
        return self._cpu.collect()

    def _collect_ram(self):
        """Returns dict with mem_total, mem_used, mem_pct, swap_* keys."""
        return self._ram.collect()

    def _collect_disk(self):
        """
        Returns dict with mount_point, used, total, pct for root filesystem.

        FUTURE EXTENSION — MULTIPLE MOUNT POINTS:
            To add more filesystems, replace DiskCollector with a
            MultiDiskCollector that iterates /proc/mounts, filters out
            virtual filesystems (tmpfs, devtmpfs, sysfs), and returns
            a list of dicts. assemble_frame() already expects a list
            (repeated DiskUsage in proto). Change here only.
        """
        return self._disk.collect()

    def _collect_load(self):
        """Returns dict with load_1m, load_5m, load_15m."""
        return self._load.collect()

    def _collect_network(self):
        """
        Detects current default interface, collects network deltas.

        WHY RE-DETECT INTERFACE HERE (not just at startup):
            If the machine switches from eth0 to wlan0 mid-run, we want
            to start collecting from wlan0 automatically. Detecting once
            at startup would mean zero network metrics after the switch.

            Cost: one subprocess.run() per network_interval (default 1s).
            `ip route show default` is fast (<5ms). Acceptable for 1s interval.
            If you're concerned about the cost, cache the result for 30s and
            re-detect only on cache expiry.

        WHEN INTERFACE CHANGES:
            We recreate NetworkCollector for the new interface. The old
            collector's prev_values are discarded. Next sample from the new
            collector returns None (first sample needs baseline). The cycle
            after that produces correct deltas for the new interface.
        """
        current_iface = detect_default_interface()

        # Interface changed or first run — recreate the stateful collector
        if current_iface != self._network_iface:
            logger.info(
                "Network interface changed: %s → %s", self._network_iface, current_iface
            )
            self._network_iface = current_iface
            self._network_collector = NetworkCollector(current_iface)

        result = self._network_collector.collect()
        if result is not None:
            # Attach interface name so assemble_frame knows which iface this is
            result["interface_name"] = current_iface
        return result

    def _collect_services(self):
        """Returns dict: {service_name: bool (active/inactive)}."""
        return self._service.collect()

    # -----------------------------------------------------------------------
    # Frame assembly — called by Sampler
    # -----------------------------------------------------------------------

    async def assemble_frame(self, frames_dropped: int = 0) -> Optional[MetricFrame]:
        """
        Reads from the latest-value cache and constructs a complete MetricFrame.

        CALLED BY: Sampler, once per frame_interval
        RETURNS: MetricFrame dataclass, or None if critical data is missing

        WHY async:
            Currently nothing awaited inside. Marked async because:
            1. Future extension might need to await something (e.g. async lock)
            2. Caller (sampler) is async and `await assembler.assemble_frame()`
               reads more clearly than mixing sync/async calls

        frames_dropped: counter from the streamer tracking how many frames
            were lost because the gRPC connection was down. Injected here
            so it becomes part of the frame the server receives.
            Server uses this to detect data gaps.

        WHAT "CRITICAL DATA MISSING" MEANS:
            CPU and RAM are core metrics. If both are None, we have nothing
            useful to send. Return None — sampler will skip this cycle.
            Network/disk/services being None is acceptable: those collectors
            may not have run yet (long intervals). Send frame with empty lists.

        FUTURE SERVICE NOTE:
            infrawatch's assembler would have an assemble_frame() that builds
            an AppMetricFrame combining HTTP latency, container CPU, pod count.
            Same pattern: read from latest cache, build typed dataclass, return.
        """
        cpu = self._latest["cpu_pct"]
        ram = self._latest["ram"]
        disk = self._latest["disk"]
        load = self._latest["load"]
        net = self._latest["network"]
        svcs = self._latest["services"]

        # Can't build a useful frame without at least CPU and RAM
        if cpu is None or ram is None:
            logger.debug("assemble_frame: waiting for first CPU/RAM sample")
            return None

        # Build NetworkInterface list
        # Currently one interface (the default route interface).
        # Future: iterate self._latest["network"] if MultiNetworkCollector
        # returns a list of dicts.
        interfaces: list[NetworkInterface] = []
        if net is not None:
            interfaces.append(
                NetworkInterface(
                    interface_name=net.get("interface_name", "unknown"),
                    bytes_in=net.get("bytes_in", 0),
                    bytes_out=net.get("bytes_out", 0),
                    packets_in=net.get("packets_in", 0),
                    packets_out=net.get("packets_out", 0),
                    errors_in=net.get("errors_in", 0),
                    errors_out=net.get("errors_out", 0),
                )
            )

        # Build ServiceStatus list
        services: list[ServiceStatus] = []
        if svcs is not None:
            for name, active in svcs.items():
                services.append(ServiceStatus(name=name, active=active))

        # Build DiskUsage list
        # Currently one entry (root filesystem).
        # Future: disk collector returns list[dict] → iterate here.
        disks: list[DiskUsage] = []
        if disk is not None:
            disks.append(
                DiskUsage(
                    mount_point=disk.get("mount_point", "/"),
                    pct=disk.get("pct", 0.0),
                    used=disk.get("used", 0.0),
                    total=disk.get("total", 0.0),
                )
            )

        # Load averages — default 0.0 if load collector hasn't run yet
        load_1m = load["load_1m"] if load else 0.0
        load_5m = load["load_5m"] if load else 0.0
        load_15m = load["load_15m"] if load else 0.0

        # RAM keys from RAMCollector.collect():
        #   mem_total, mem_used, mem_pct, swap_total, swap_used, swap_pct
        frame = MetricFrame(
            agent_id=self._cfg.agent.agent_id,
            # uuid4 gives a unique ID per frame.
            # Server uses frame_id for deduplication: if the same frame
            # arrives twice (retry after partial send), the server
            # can detect and ignore the duplicate.
            frame_id=str(uuid.uuid4()),
            # Unix timestamp in milliseconds.
            # Milliseconds because sub-second precision is useful for
            # correlating metrics with logs (which also use ms timestamps).
            timestamp=int(time.time() * 1000),
            cpu_pct=cpu,
            ram_pct=ram["mem_pct"],
            ram_used=ram["mem_used"],
            ram_total=ram["mem_total"],
            swap_pct=ram["swap_pct"],
            swap_used=ram["swap_used"],
            swap_total=ram["swap_total"],
            load_1m=load_1m,
            load_5m=load_5m,
            load_15m=load_15m,
            frames_dropped_since_last_connect=frames_dropped,
            interfaces=interfaces,
            services=services,
            disks=disks,
        )

        return frame
