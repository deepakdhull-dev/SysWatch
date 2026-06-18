from __future__ import annotations

import asyncio
import logging
import subprocess
import time
import uuid

from .collectors import *
from .config import Config

logger = logging.getLogger(__name__)


class Assembler:
    def __init__(self, cfg):
        self.cfg = cfg

        self._cpu = CPUCollector()
        self._ram = RAMCollector()
        self._disk = DiskCollector()
        self._load = LoadCollector()
        self._service = ServiceCollector(cfg.agent.services)
        self._network = None

        self._latest = {
            "cpu": None,
            "ram": None,
            "disk": None,
            "load": None,
            "service": None,
            "network": None,
        }

        self._tasks = []
        self._interface = None

    async def start(self):
        self._tasks = [
            asyncio.create_task(
                self.collect_loop(
                    "cpu",
                    self._cpu_collect,
                    self.cfg.collector.cpu_interval,
                ),
                name="cpu-collector",
            ),
            asyncio.create_task(
                self.collect_loop(
                    "disk",
                    self._disk_collect,
                    self.cfg.collector.disk_interval,
                ),
                name="disk-collector",
            ),
            asyncio.create_task(
                self.collect_loop(
                    "ram",
                    self._ram_collect,
                    self.cfg.collector.ram_interval,
                ),
                name="ram-collector",
            ),
            asyncio.create_task(
                self.collect_loop(
                    "load",
                    self._load_collect,
                    self.cfg.collector.load_interval,
                ),
                name="load-collector",
            ),
            asyncio.create_task(
                self.collect_loop(
                    "network",
                    self._network_collect,
                    self.cfg.collector.network_interval,
                ),
                name="network-collector",
            ),
            asyncio.create_task(
                self.collect_loop(
                    "service",
                    self._service_collect,
                    self.cfg.collector.service_interval,
                ),
                name="service-collector",
            ),
        ]
        logger.info(f"Assembler starter {len(self._tasks)} collector tasks")

    async def stop(self):
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info(f"Assembler stopped all colector tasks")

    async def collect_loop(self, key, collect_fn, interval):
        while True:
            try:
                result = collect_fn()
                if result is not None:
                    self._latest[key] = result
            except Exception as e:
                logger.warning(f"Collector {key} raised {type(e).__name__}:{e}")
            await asyncio.sleep(interval)

    def _cpu_collect(self):
        return self._cpu.collect()

    def _ram_collect(self):
        return self._ram.collect()

    def _disk_collect(self):
        return self._disk.collect()

    def _load_collect(self):
        return self._load.collect()

    def _service_collect(self):
        return self._service.collect()

    def _network_collect(self):
        interface = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            check=True,
        )
        intf = interface.stdout.strip().split()[4]
        if self._network is None or self._interface != intf:
            self._network = NetworkCollector(intf)
            self._interface = intf
        return self._network.collect()

    async def assemble_frame(self, frames_dropped=0):
        cpu = self._latest["cpu"]
        ram = self._latest["ram"]
        dsk = self._latest["disk"]
        load = self._latest["load"]
        svc = self._latest["service"]
        net = self._latest["network"]

        if ram is None or cpu is None:
            return None

        service = []
        if svc is not None:
            for n, i in svc.items():
                service.append(ServiceStatus(name=n, active=i))

        disk = []
        if dsk is not None:
            disk.append(
                DiskUsage(
                    mount_point=dsk["mount_point"],
                    pct=dsk["pct"],
                    used=dsk["used"],
                    total=dsk["total"],
                )
            )

        network = []
        if net is not None:
            network.append(
                NetworkInterface(
                    interface_name=net["interface_name"],
                    packets_in=net["packets_in"],
                    packets_out=net["packets_out"],
                    bytes_in=net["bytes_in"],
                    bytes_out=net["bytes_out"],
                    errors_in=net["errors_in"],
                    errors_out=net["errors_out"],
                )
            )

        load_1m = load["load_1m"] if load else 0.0
        load_5m = load["load_5m"] if load else 0.0
        load_15m = load["load_15m"] if load else 0.0

        frame = MetricFrame(
            agent_id=self.cfg.agent.agent_id,
            frame_id=str(uuid.uuid4()),
            timestamp=int(time.time() * 1000),
            cpu_pct=cpu,
            ram_pct=ram["ram_pct"],
            ram_used=ram["ram_used"],
            ram_total=ram["ram_total"],
            swap_pct=ram["swap_pct"],
            swap_used=ram["swap_used"],
            swap_total=ram["swap_total"],
            load_1m=load_1m,
            load_5m=load_5m,
            load_15m=load_15m,
            frames_dropped_since_last_connect=frames_dropped,
            disks=disk,
            interfaces=network,
            services=service,
        )
        return frame
