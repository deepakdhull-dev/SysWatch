from __future__ import annotations

import logging
from typing import Any

import asyncpg

logger = logging.getLogger(__name__)


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.execute("SET timezone = 'UTC'")

    await conn.execute("SET search_path = public")

    logger.debug(
        "asyncpg connection initialised (pid=%s): timezone=UTC search_path=public",
        conn.get_server_pid(),
    )


def _build_dsn(cfg: Any) -> str:
    logger.debug(
        "Building DSN: host=%s port=%d db=%s user=%s",
        cfg.host,
        cfg.port,
        cfg.name,
        cfg.user,
    )

    if cfg.password:
        return (
            f"postgresql://{cfg.user}:{cfg.password}@{cfg.host}:{cfg.port}/{cfg.name}"
        )
    else:
        return f"postgresql://{cfg.user}@{cfg.host}:{cfg.port}/{cfg.name}"


async def _check_timescaledb(pool: asyncpg.Pool) -> None:
    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT extversion
            FROM pg_extension
            WHERE extname = 'timescaledb'
            """
        )

    if row is None:
        raise RuntimeError(
            "TimescaleDB extension is not installed in the database. "
            "Run install.sh (server path) which executes: "
            "CREATE EXTENSION IF NOT EXISTS timescaledb CASCADE;"
        )

    logger.info("TimescaleDB extension verified (version=%s)", row["extversion"])


async def create_pool(cfg: Any) -> asyncpg.Pool:
    db_cfg = cfg.db
    dsn = _build_dsn(db_cfg)

    logger.info(
        "Creating asyncpg pool: host=%s port=%d db=%s min=%d max=%d",
        db_cfg.host,
        db_cfg.port,
        db_cfg.name,
        db_cfg.min_pool,
        db_cfg.max_pool,
    )

    pool: asyncpg.Pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=db_cfg.min_pool,
        max_size=db_cfg.max_pool,
        init=_init_connection,
        command_timeout=30.0,
        max_inactive_connection_lifetime=300.0,
    )

    logger.info("asyncpg pool created (%d initial connections)", db_cfg.min_pool)

    await _check_timescaledb(pool)

    return pool


async def close_pool(pool: asyncpg.Pool) -> None:

    logger.info("Closing asyncpg pool...")
    await pool.close()
    logger.info("asyncpg pool closed")
