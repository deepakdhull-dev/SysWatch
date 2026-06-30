from __future__ import annotations

import logging
from typing import Any
from urllib.parse import urlsplit, urlunsplit

import asyncpg

logger = logging.getLogger(__name__)


async def _init_connection(conn: asyncpg.Connection) -> None:
    await conn.execute("SET timezone = 'UTC'")

    await conn.execute("SET search_path = public")

    logger.debug(
        "asyncpg connection initialised (pid=%s): timezone=UTC search_path=public",
        conn.get_server_pid(),
    )


def _build_dsn(database_cfg: Any) -> str:
    # config.yaml stores database.url as a single SQLAlchemy-style DSN
    # (postgresql+asyncpg://user:pass@host:port/db), used as-is by Alembic's
    # SQLAlchemy engine. asyncpg's own driver does not understand the
    # "+asyncpg" dialect suffix, so it must be stripped here before passing
    # the DSN to asyncpg.create_pool().
    parts = urlsplit(database_cfg.url)
    scheme = parts.scheme.split("+", 1)[0]  # "postgresql+asyncpg" -> "postgresql"
    dsn = urlunsplit((scheme, parts.netloc, parts.path, parts.query, parts.fragment))

    logger.debug("Built asyncpg DSN from database.url (driver suffix stripped)")
    return dsn


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
    db_cfg = cfg.database
    dsn = _build_dsn(db_cfg)

    logger.info(
        "Creating asyncpg pool: pool_size=%d max_overflow=%d pool_timeout=%ds",
        db_cfg.pool_size,
        db_cfg.max_overflow,
        db_cfg.pool_timeout,
    )

    pool: asyncpg.Pool = await asyncpg.create_pool(
        dsn=dsn,
        min_size=db_cfg.pool_size,
        max_size=db_cfg.pool_size + db_cfg.max_overflow,
        init=_init_connection,
        command_timeout=float(db_cfg.pool_timeout),
        max_inactive_connection_lifetime=300.0,
    )

    logger.info("asyncpg pool created (%d initial connections)", db_cfg.pool_size)

    await _check_timescaledb(pool)

    return pool


async def close_pool(pool: asyncpg.Pool) -> None:

    logger.info("Closing asyncpg pool...")
    await pool.close()
    logger.info("asyncpg pool closed")
