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
class ServerConfig:
    grpc_host: str = "0.0.0.0"
    grpc_port: int = 50051
    http_host: str = "0.0.0.0"
    http_port: int = 8080
    metrics_port: int = 9091


@dataclass
class DatabaseConfig:
    url: str = "postgresql+asyncpg://syswatch:changeme@localhost:5432/syswatch"
    pool_size: int = 10
    max_overflow: int = 5
    pool_timeout: int = 30


@dataclass
class TlsConfig:
    ca_cert: str = "/etc/syswatch/pki/ca/ca.crt"
    server_cert: str = "/etc/syswatch/pki/server/server.crt"
    server_key: str = "/etc/syswatch/pki/server/server.key"


@dataclass
class JwtConfig:
    private_key: str = "/etc/syswatch/server/jwt/jwt.key"
    public_key: str = "/etc/syswatch/server/jwt/jwt.pub"
    algorithm: str = "RS256"
    access_token_expire_minutes: int = 15
    refresh_token_expire_days: int = 7


@dataclass
class AuthConfig:
    admin_username: str = "admin"
    admin_password_hash: str = ""


@dataclass
class PkiConfig:
    ca_key: str = "/etc/syswatch/pki/ca/ca.key"
    ca_cert: str = "/etc/syswatch/pki/ca/ca.crt"
    agent_cert_validity_days: int = 365


@dataclass
class PrometheusConfig:
    url: str = "http://localhost:9090"


@dataclass
class GrafanaConfig:
    url: str = "http://localhost:3000"
    admin_user: str = "admin"
    admin_password: str = "syswatch"


@dataclass
class AlertmanagerConfig:
    url: str = "http://localhost:9093"


@dataclass
class TracingConfig:
    enabled: bool = True
    service_name: str = "syswatch-server"
    otlp_endpoint: str = "http://localhost:4317"


@dataclass
class LoggingConfig:
    level: str = "INFO"
    format: str = "json"


@dataclass
class Config:
    server: ServerConfig = field(default_factory=ServerConfig)
    database: DatabaseConfig = field(default_factory=DatabaseConfig)
    tls: TlsConfig = field(default_factory=TlsConfig)
    jwt: JwtConfig = field(default_factory=JwtConfig)
    auth: AuthConfig = field(default_factory=AuthConfig)
    pki: PkiConfig = field(default_factory=PkiConfig)
    prometheus: PrometheusConfig = field(default_factory=PrometheusConfig)
    grafana: GrafanaConfig = field(default_factory=GrafanaConfig)
    alertmanager: AlertmanagerConfig = field(default_factory=AlertmanagerConfig)
    tracing: TracingConfig = field(default_factory=TracingConfig)
    logging: LoggingConfig = field(default_factory=LoggingConfig)


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
                # Do not log secret values (e.g. password hashes, DB URLs with
                # embedded credentials). Log only the key.
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
        server=_build_section(ServerConfig, raw.get("server", {})),
        database=_build_section(DatabaseConfig, raw.get("database", {})),
        tls=_build_section(TlsConfig, raw.get("tls", {})),
        jwt=_build_section(JwtConfig, raw.get("jwt", {})),
        auth=_build_section(AuthConfig, raw.get("auth", {})),
        pki=_build_section(PkiConfig, raw.get("pki", {})),
        prometheus=_build_section(PrometheusConfig, raw.get("prometheus", {})),
        grafana=_build_section(GrafanaConfig, raw.get("grafana", {})),
        alertmanager=_build_section(AlertmanagerConfig, raw.get("alertmanager", {})),
        tracing=_build_section(TracingConfig, raw.get("tracing", {})),
        logging=_build_section(LoggingConfig, raw.get("logging", {})),
    )

    _apply_env_overrides(cfg.server, "server")
    _apply_env_overrides(cfg.database, "database")
    _apply_env_overrides(cfg.tls, "tls")
    _apply_env_overrides(cfg.jwt, "jwt")
    _apply_env_overrides(cfg.auth, "auth")
    _apply_env_overrides(cfg.pki, "pki")
    _apply_env_overrides(cfg.prometheus, "prometheus")
    _apply_env_overrides(cfg.grafana, "grafana")
    _apply_env_overrides(cfg.alertmanager, "alertmanager")
    _apply_env_overrides(cfg.tracing, "tracing")
    _apply_env_overrides(cfg.logging, "logging")

    logger.debug(
        "Config resolved: grpc=%s:%d http=%s:%d metrics=%d tracing=%s",
        cfg.server.grpc_host,
        cfg.server.grpc_port,
        cfg.server.http_host,
        cfg.server.http_port,
        cfg.server.metrics_port,
        cfg.tracing.enabled,
    )

    return cfg
