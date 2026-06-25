from __future__ import annotations

import asyncio
import logging
import time
import uuid

from .collectors import (
    CPUCollector,
    DiskCollector,
    DiskUsage,
    LoadCollector,
    MetricFrame,
    NetworkCollector,
    NetworkInterface,
    RAMCollector,
    ServiceCollector,
    ServiceStatus,
)
from .config import Config, detect_default_interface

logger = logging.getLogger(__name__)


class Assembler:
    def __init__(self, cfg: Config) -> None:
        self.cfg = cfg

        self._cpu = CPUCollector()
        self._ram = RAMCollector()
        self._disk = DiskCollector()
        self._load = LoadCollector()
        self._service = ServiceCollector(cfg.agent.services)
        self._network: NetworkCollector | None = None

        self._latest: dict[str, object] = {
            "cpu": None,
            "ram": None,
            "disk": None,
            "load": None,
            "service": None,
            "network": None,
        }

        self._tasks: list[asyncio.Task[None]] = []
        self._network_iface: str | None = None

    async def start(self) -> None:
        self._tasks = [
            asyncio.create_task(
                self.collect_loop(
                    "cpu", self._cpu_collect, self.cfg.collector.cpu_interval
                ),
                name="cpu-collector",
            ),
            asyncio.create_task(
                self.collect_loop(
                    "disk", self._disk_collect, self.cfg.collector.disk_interval
                ),
                name="disk-collector",
            ),
            asyncio.create_task(
                self.collect_loop(
                    "ram", self._ram_collect, self.cfg.collector.ram_interval
                ),
                name="ram-collector",
            ),
            asyncio.create_task(
                self.collect_loop(
                    "load", self._load_collect, self.cfg.collector.load_interval
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
        logger.info("Assembler started %d collector tasks", len(self._tasks))

    async def stop(self) -> None:
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)
        logger.info("Assembler stopped all collector tasks")

    async def collect_loop(
        self,
        key: str,
        collect_fn: object,
        interval: float,
    ) -> None:
        logger.debug(
            "Starting collection loop for '%s' (interval=%.1fs)", key, interval
        )
        while True:
            try:
                result = collect_fn()  # type: ignore[operator]
                if result is not None:
                    self._latest[key] = result
            except Exception as exc:
                logger.warning(
                    "Collector %s raised %s: %s", key, type(exc).__name__, exc
                )
            await asyncio.sleep(interval)

    def _cpu_collect(self) -> object:
        return self._cpu.collect()

    def _ram_collect(self) -> object:
        return self._ram.collect()

    def _disk_collect(self) -> object:
        return self._disk.collect()

    def _load_collect(self) -> object:
        return self._load.collect()

    def _service_collect(self) -> object:
        return self._service.collect()

    def _network_collect(self) -> object:
        current_iface = detect_default_interface()
        if current_iface is None:
            return None
        if current_iface != self._network_iface:
            logger.info(
                "Network interface changed: %s → %s", self._network_iface, current_iface
            )
            self._network_iface = current_iface
            self._network = NetworkCollector(current_iface)
        if self._network is None:
            return None
        return self._network.collect()

    async def assemble_frame(self, frames_dropped: int = 0) -> MetricFrame | None:
        cpu = self._latest["cpu"]
        ram = self._latest["ram"]
        dsk = self._latest["disk"]
        load = self._latest["load"]
        svc = self._latest["service"]
        net = self._latest["network"]

        if ram is None or cpu is None:
            return None

        services: list[ServiceStatus] = []
        if svc is not None:
            for name, active in svc.items():  # type: ignore[union-attr]
                services.append(ServiceStatus(name=name, active=active))

        disks: list[DiskUsage] = []
        if dsk is not None:
            disks.append(
                DiskUsage(
                    mount_point=dsk["mount_point"],  # type: ignore[index]
                    pct=dsk["pct"],  # type: ignore[index]
                    used=dsk["used"],  # type: ignore[index]
                    total=dsk["total"],  # type: ignore[index]
                )
            )

        interfaces: list[NetworkInterface] = []
        if net is not None:
            interfaces.append(
                NetworkInterface(
                    interface_name=net["interface_name"],  # type: ignore[index]
                    packets_in=net["packets_in"],  # type: ignore[index]
                    packets_out=net["packets_out"],  # type: ignore[index]
                    bytes_in=net["bytes_in"],  # type: ignore[index]
                    bytes_out=net["bytes_out"],  # type: ignore[index]
                    errors_in=net["errors_in"],  # type: ignore[index]
                    errors_out=net["errors_out"],  # type: ignore[index]
                )
            )

        load_1m = load["load_1m"] if load else 0.0  # type: ignore[index]
        load_5m = load["load_5m"] if load else 0.0  # type: ignore[index]
        load_15m = load["load_15m"] if load else 0.0  # type: ignore[index]

        return MetricFrame(
            agent_id=self.cfg.agent.agent_id,
            frame_id=str(uuid.uuid4()),
            timestamp=int(time.time() * 1000),
            cpu_pct=cpu,  # type: ignore[arg-type]
            ram_pct=ram["ram_pct"],  # type: ignore[index]
            ram_used=ram["ram_used"],  # type: ignore[index]
            ram_total=ram["ram_total"],  # type: ignore[index]
            swap_pct=ram["swap_pct"],  # type: ignore[index]
            swap_used=ram["swap_used"],  # type: ignore[index]
            swap_total=ram["swap_total"],  # type: ignore[index]
            load_1m=load_1m,
            load_5m=load_5m,
            load_15m=load_15m,
            frames_dropped_since_last_connect=frames_dropped,
            disks=disks,
            interfaces=interfaces,
            services=services,
        )
