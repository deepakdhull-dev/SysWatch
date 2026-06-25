from __future__ import annotations

import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .exceptions import ConfigError


@dataclass
class ServerConfig:
    host: str
    port: int
    tls_enabled: bool = True


@dataclass
class CertsConfig:
    ca_cert: str = "/etc/syswatch/certs/ca.crt"
    client_cert: str = "/etc/syswatch/certs/client.crt"
    client_key: str = "/etc/syswatch/certs/client.key"


@dataclass
class CollectorConfig:
    cpu_interval: float = 1.0
    ram_interval: float = 2.0
    disk_interval: float = 12.0
    network_interval: float = 1.0
    load_interval: float = 5.0
    service_interval: float = 10.0


@dataclass
class SamplerConfig:
    frame_interval: float = 5.0


@dataclass
class StreamerConfig:
    backoff_base: float = 1.0
    backoff_cap: float = 60.0
    backoff_jitter: float = 2.0
    send_queue_size: int = 512  # was incorrectly typed as float


@dataclass
class AgentConfig:
    agent_id: str
    services: list[str] = field(default_factory=list)


@dataclass
class Config:
    agent: AgentConfig
    server: ServerConfig
    certs: CertsConfig
    collector: CollectorConfig
    sampler: SamplerConfig
    streamer: StreamerConfig


def detect_default_interface() -> str:
    try:
        result = subprocess.run(
            ["ip", "route", "show", "default"],
            capture_output=True,
            text=True,
            timeout=2.0,
        )
        tokens = result.stdout.strip().split()
        if "dev" in tokens:
            return tokens[tokens.index("dev") + 1]
    except (subprocess.SubprocessError, FileNotFoundError, ValueError, IndexError):
        pass
    return "eth0"


def load_config(path: str = "/etc/syswatch/agent.yaml") -> Config:
    config_path = Path(path)
    if not config_path.exists():
        raise ConfigError(
            f"Config file not found at {path}. "
            "Has the agent been installed? Run install.sh with server-provided zip."
        )

    try:
        with open(config_path) as f:
            raw = yaml.safe_load(f)
    except yaml.YAMLError as exc:
        raise ConfigError(f"Failed to parse config at {path}: {exc}") from exc

    if not isinstance(raw, dict):
        raise ConfigError(f"Config at {path} is empty or not a YAML mapping.")

    try:
        agent_section = raw.get("agent", {})
        agent_cfg = AgentConfig(
            agent_id=agent_section["agent_id"],
            services=agent_section.get("services", []),
        )

        server_section = raw.get("server", {})
        server_cfg = ServerConfig(
            host=server_section["host"],
            port=int(server_section.get("port", 50051)),
            tls_enabled=bool(server_section.get("tls_enabled", True)),
        )

        certs_section = raw.get("certs", {})
        certs_cfg = CertsConfig(
            ca_cert=certs_section.get("ca_cert", "/etc/syswatch/certs/ca.crt"),
            client_cert=certs_section.get(
                "client_cert", "/etc/syswatch/certs/client.crt"
            ),
            client_key=certs_section.get(
                "client_key", "/etc/syswatch/certs/client.key"
            ),
        )

        col_section = raw.get("collector", {})
        collector_cfg = CollectorConfig(
            cpu_interval=float(col_section.get("cpu_interval", 1.0)),
            ram_interval=float(col_section.get("ram_interval", 2.0)),
            disk_interval=float(col_section.get("disk_interval", 12.0)),
            network_interval=float(col_section.get("network_interval", 1.0)),
            load_interval=float(col_section.get("load_interval", 5.0)),
            service_interval=float(col_section.get("service_interval", 10.0)),
        )

        sampler_section = raw.get("sampler", {})
        sampler_cfg = SamplerConfig(
            frame_interval=float(sampler_section.get("frame_interval", 5.0)),
        )

        streamer_section = raw.get("streamer", {})
        streamer_cfg = StreamerConfig(
            backoff_base=float(streamer_section.get("backoff_base", 1.0)),
            backoff_cap=float(streamer_section.get("backoff_cap", 60.0)),
            backoff_jitter=float(streamer_section.get("backoff_jitter", 2.0)),
            send_queue_size=int(streamer_section.get("send_queue_size", 512)),
        )

    except KeyError as exc:
        raise ConfigError(f"Missing required config key: {exc}") from exc

    return Config(
        agent=agent_cfg,
        server=server_cfg,
        certs=certs_cfg,
        collector=collector_cfg,
        sampler=sampler_cfg,
        streamer=streamer_cfg,
    )
