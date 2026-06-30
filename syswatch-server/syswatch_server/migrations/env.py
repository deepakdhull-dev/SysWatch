from __future__ import annotations

import asyncio
import logging
import os
from pathlib import Path
from typing import Any

import yaml
from alembic import context
from sqlalchemy.ext.asyncio import create_async_engine

logger = logging.getLogger("alembic.env")
alembic_cfg = context.config


def _build_dsn_from_config(config_path: str) -> str:
    cfg_file = Path(config_path)
    if not cfg_file.exists():
        raise FileNotFoundError(
            f"syswatch config not found at {config_path}. "
            "Set SYSWATCH_CONFIG env var to the correct path, or set "
            "SYSWATCH_DB_DSN to a full connection string."
        )

    with open(cfg_file) as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    database = raw.get("database", {})
    url = database.get("url")
    if not url:
        raise FileNotFoundError(
            f"config at {config_path} has no database.url set. "
            "Set SYSWATCH_DB_DSN to a full connection string instead."
        )
    return url


def get_dsn() -> str:
    env_dsn = os.environ.get("SYSWATCH_DB_DSN")
    if env_dsn:
        logger.info("Using DSN from SYSWATCH_DB_DSN environment variable")
        return env_dsn

    config_path = os.environ.get(
        "SYSWATCH_CONFIG",
        "/etc/syswatch/server/config.yaml",
    )
    logger.info("Reading DSN from config file: %s", config_path)
    return _build_dsn_from_config(config_path)


def do_run_migrations(connection: Any) -> None:

    context.configure(
        connection=connection,
        target_metadata=None,
        compare_type=True,
        include_schemas=False,
        include_object=_include_object,
    )
    with context.begin_transaction():
        context.run_migrations()


def _include_object(
    obj: Any, name: str, type_: str, reflected: bool, compare_to: Any
) -> bool:
    if hasattr(obj, "schema") and obj.schema in (
        "_timescaledb_internal",
        "_timescaledb_catalog",
        "_timescaledb_config",
        "timescaledb_information",
    ):
        return False
    return True


def run_migrations_offline() -> None:
    url = get_dsn()

    context.configure(
        url=url,
        target_metadata=None,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
        include_object=_include_object,
    )

    with context.begin_transaction():
        context.run_migrations()


async def run_migrations_online() -> None:
    url = get_dsn()
    engine = create_async_engine(url, pool_pre_ping=True)

    logger.info("Running online migrations against: %s", _redact_dsn(url))

    async with engine.connect() as conn:
        await conn.run_sync(do_run_migrations)

    await engine.dispose()

    logger.info("Migrations complete")


def _redact_dsn(dsn: str) -> str:
    import re

    return re.sub(r"(?<=://[^:]+:)[^@]+(?=@)", "***", dsn)


if context.is_offline_mode():
    run_migrations_offline()
else:
    asyncio.run(run_migrations_online())
