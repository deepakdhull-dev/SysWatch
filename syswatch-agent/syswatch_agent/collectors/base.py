from dataclasses import dataclass, field


@dataclass
class HostInfo:
    hostname: str
    kernel: str
    cpu_model: str
    os_name: str
    cpu_cores: int
    cpu_threads: int
    agent_id: str


@dataclass
class NetworkInterface:
    interface_name: str
    bytes_in: int
    bytes_out: int
    packets_in: int
    packets_out: int
    errors_in: int
    errors_out: int


@dataclass
class ServiceStatus:
    name: str
    active: bool


@dataclass
class DiskUsage:
    mount_point: str
    pct: float
    used: float
    total: float


@dataclass
class MetricFrame:
    agent_id: str
    frame_id: str
    timestamp: int
    cpu_pct: float
    ram_pct: float
    ram_used: float
    ram_total: float
    swap_total: float
    swap_used: float
    swap_pct: float
    load_1m: float
    load_5m: float
    load_15m: float
    frames_dropped_since_last_connect: int
    interfaces: list[NetworkInterface] = field(default_factory=list)
    services: list[ServiceStatus] = field(default_factory=list)
    disks: list[DiskUsage] = field(default_factory=list)
