from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field, fields, is_dataclass
from pathlib import Path
from typing import Any

import yaml

from .exceptions import ConfigError

logger = logging.getLogger(__name__)


@dataclass
class DbConfig:
    host: str = "localhost"
    port: int = 5432
    name: str = "syswatch"
    user: str = "syswatch"
    password: str = ""
    min_pool: int = 2
    max_pool: int = 10


@dataclass
class GrpcTlsConfig:
    ca_cert: str = "/etc/syswatch/server/ca.crt"
    server_cert: str = "/etc/syswatch/server/server.crt"
    server_key: str = "/etc/syswatch/server/server.key"


@dataclass
class GrpcConfig:
    host: str = "0.0.0.0"
    port: int = 50051
    max_workers: int = 4
    tls: GrpcTlsConfig = field(default_factory=GrpcTlsConfig)


@dataclass
class WebConfig:
    host: str = "127.0.0.1"
    port: int = 8000


@dataclass
class AuthConfig:
    jwt_private_key: str = "/etc/syswatch/server/jwt.key"
    jwt_public_key: str = "/etc/syswatch/server/jwt.pub"
    credentials_file: str = "/etc/syswatch/server/credentials.yaml"


@dataclass
class MetricsConfig:
    port: int = 9091


@dataclass
class GrafanaConfig:
    url: str = "http://localhost:3000"
    dashboard_uid: str = "syswatch-main"


@dataclass
class AlertmanagerConfig:
    url: str = "http://localhost:9093"


@dataclass
class TracingConfig:
    enabled: bool = True
    service_name: str = "syswatch-server"
    otlp_endpoint: str = "http://localhost:4317"


@dataclass
class Config:
    db: DbConfig = field(default_factory=DbConfig)
    grpc: GrpcConfig = field(default_factory=GrpcConfig)
    web: WebConfig = field(default_factory=WebConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    metrics: MetricsConfig = field(default_factory=MetricsConfig)
    grafana: GrafanaConfig = field(default_factory=GrafanaConfig)
    alertmanager: AlertmanagerConfig = field(default_factory=AlertmanagerConfig)
    tracing: TracingConfig = field(default_factory=TracingConfig)


def _coerce(value: str, target_type: type) -> Any:
    if target_type is bool:
        return value.strip().lower() in ("true", "1", "yes", "on")
    if target_type is int:
        return int(value)
    return value


def _apply_env_overrides(section_obj: Any, section_name: str) -> None:
    for f in fields(section_obj):
        if is_dataclass(f.type) if isinstance(f.type, type) else False:
            continue

        env_key = f"SYSWATCH_{section_name.upper()}_{f.name.upper()}"
        env_val = os.environ.get(env_key)
        if env_val is not None:
            try:
                coerced = _coerce(env_val, f.type if isinstance(f.type, type) else str)
                setattr(section_obj, f.name, coerced)
                # Do not log secret values (e.g. password). Log only the key.
                logger.debug("Config override from %s", env_key)
            except (ValueError, TypeError) as exc:
                raise ConfigError(
                    f"Invalid value for {env_key}: {env_val!r} ({exc})"
                ) from exc


def _build_section(section_cls: type, raw: dict[str, Any]) -> Any:
    if not isinstance(raw, dict):
        return section_cls()

    known = {f.name: f for f in fields(section_cls)}
    kwargs: dict[str, Any] = {}

    for key, value in raw.items():
        if key not in known:
            logger.debug(
                "Ignoring unknown config key %r in %s", key, section_cls.__name__
            )
            continue
        f = known[key]
        if f.name == "tls" and section_cls is GrpcConfig:
            kwargs["tls"] = _build_section(GrpcTlsConfig, value)
        else:
            kwargs[key] = value

    return section_cls(**kwargs)


def load_config(path: str = "/etc/syswatch/server/config.yaml") -> Config:
    raw: dict[str, Any] = {}
    config_path = Path(path)

    if config_path.exists():
        try:
            with open(config_path) as f:
                loaded = yaml.safe_load(f)
            if loaded is not None:
                if not isinstance(loaded, dict):
                    raise ConfigError(f"Config at {path} is not a YAML mapping.")
                raw = loaded
        except yaml.YAMLError as exc:
            raise ConfigError(f"Failed to parse config at {path}: {exc}") from exc
        logger.info("Loaded config from %s", path)
    else:
        logger.warning(
            "Config file not found at %s — using defaults + environment overrides",
            path,
        )

    cfg = Config(
        db=_build_section(DbConfig, raw.get("db", {})),
        grpc=_build_section(GrpcConfig, raw.get("grpc", {})),
        web=_build_section(WebConfig, raw.get("web", {})),
        auth=_build_section(AuthConfig, raw.get("auth", {})),
        metrics=_build_section(MetricsConfig, raw.get("metrics", {})),
        grafana=_build_section(GrafanaConfig, raw.get("grafana", {})),
        alertmanager=_build_section(AlertmanagerConfig, raw.get("alertmanager", {})),
        tracing=_build_section(TracingConfig, raw.get("tracing", {})),
    )

    _apply_env_overrides(cfg.db, "db")
    _apply_env_overrides(cfg.grpc, "grpc")
    _apply_env_overrides(cfg.web, "web")
    _apply_env_overrides(cfg.auth, "auth")
    _apply_env_overrides(cfg.metrics, "metrics")
    _apply_env_overrides(cfg.grafana, "grafana")
    _apply_env_overrides(cfg.alertmanager, "alertmanager")
    _apply_env_overrides(cfg.tracing, "tracing")

    logger.debug(
        "Config resolved: db=%s:%d/%s grpc=%s:%d web=%s:%d metrics=%d tracing=%s",
        cfg.db.host,
        cfg.db.port,
        cfg.db.name,
        cfg.grpc.host,
        cfg.grpc.port,
        cfg.web.host,
        cfg.web.port,
        cfg.metrics.port,
        cfg.tracing.enabled,
    )

    return cfg
