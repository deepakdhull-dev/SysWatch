from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)


async def get_all_agents(pool: Any) -> list[dict[str, Any]]:

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                agent_id,
                hostname,
                os_name,
                cpu_model,
                cpu_cores,
                cpu_threads,
                registered_at,
                last_seen
            FROM agents
            ORDER BY hostname ASC
            """
        )
    # Convert asyncpg.Record → plain dict for clean serialisation in routes.
    return [dict(row) for row in rows]


async def get_agent(pool: Any, agent_id: str) -> dict[str, Any] | None:
    async with pool.acquire() as conn:
        # fetchrow() returns None (not an empty list) when no row matches.
        # This makes it easy to check: `if agent is None: raise 404`.
        row = await conn.fetchrow(
            """
            SELECT
                agent_id,
                hostname,
                kernel,
                os_name,
                cpu_model,
                cpu_cores,
                cpu_threads,
                registered_at,
                last_seen
            FROM agents
            WHERE agent_id = $1
            """,
            agent_id,
        )
    return dict(row) if row is not None else None


async def get_agent_count(pool: Any) -> int:
    async with pool.acquire() as conn:
        return await conn.fetchval("SELECT COUNT(*)::INT FROM agents")


async def get_agent_metrics(
    pool: Any,
    agent_id: str,
    hours: int = 24,
    bucket_minutes: int = 5,
) -> list[dict[str, Any]]:
    look_back = f"{hours} hours"
    bucket_size = f"{bucket_minutes} minutes"

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                time_bucket($1::INTERVAL, time) AS bucket,
                AVG(cpu_pct)   AS avg_cpu,
                AVG(ram_pct)   AS avg_ram,
                AVG(load_1m)   AS avg_load_1m,
                AVG(swap_pct)  AS avg_swap,
                MAX(frames_dropped) AS max_dropped
            FROM metrics
            WHERE
                agent_id = $2
                AND time > NOW() - $3::INTERVAL
            GROUP BY bucket
            ORDER BY bucket ASC
            """,
            bucket_size,
            agent_id,
            look_back,
        )
    return [dict(row) for row in rows]


async def get_agent_latest(pool: Any, agent_id: str) -> dict[str, Any] | None:

    async with pool.acquire() as conn:
        row = await conn.fetchrow(
            """
            SELECT
                time,
                cpu_pct,
                ram_pct,
                ram_used,
                ram_total,
                swap_pct,
                swap_used,
                swap_total,
                load_1m,
                load_5m,
                load_15m,
                frames_dropped
            FROM metrics
            WHERE agent_id = $1
            ORDER BY time DESC
            LIMIT 1
            """,
            agent_id,
        )
    return dict(row) if row is not None else None


async def get_dashboard_summary(pool: Any) -> list[dict[str, Any]]:

    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (agent_id)
                agent_id,
                time,
                cpu_pct,
                ram_pct,
                load_1m,
                swap_pct
            FROM metrics
            WHERE time > NOW() - INTERVAL '10 minutes'
            ORDER BY agent_id, time DESC
            """
        )
    return [dict(row) for row in rows]


async def get_agent_disks(
    pool: Any,
    agent_id: str,
    hours: int = 1,
    bucket_minutes: int = 5,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                time_bucket($1::INTERVAL, time) AS bucket,
                mount_point,
                AVG(pct)   AS avg_pct,
                AVG(used)  AS avg_used,
                AVG(total) AS avg_total
            FROM metrics_disk
            WHERE
                agent_id = $2
                AND time > NOW() - $3::INTERVAL
            GROUP BY bucket, mount_point
            ORDER BY bucket ASC, mount_point ASC
            """,
            f"{bucket_minutes} minutes",
            agent_id,
            f"{hours} hours",
        )
    return [dict(row) for row in rows]


async def get_agent_disk_latest(pool: Any, agent_id: str) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (mount_point)
                mount_point,
                pct,
                used,
                total,
                time
            FROM metrics_disk
            WHERE agent_id = $1
            ORDER BY mount_point, time DESC
            """,
            agent_id,
        )
    return [dict(row) for row in rows]


async def get_agent_network(
    pool: Any,
    agent_id: str,
    hours: int = 1,
    bucket_minutes: int = 5,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                time_bucket($1::INTERVAL, time) AS bucket,
                interface_name,
                AVG(bytes_in)    AS avg_bytes_in,
                AVG(bytes_out)   AS avg_bytes_out,
                AVG(packets_in)  AS avg_packets_in,
                AVG(packets_out) AS avg_packets_out,
                SUM(errors_in)   AS sum_errors_in,
                SUM(errors_out)  AS sum_errors_out
            FROM metrics_network
            WHERE
                agent_id = $2
                AND time > NOW() - $3::INTERVAL
            GROUP BY bucket, interface_name
            ORDER BY bucket ASC, interface_name ASC
            """,
            f"{bucket_minutes} minutes",
            agent_id,
            f"{hours} hours",
        )
    return [dict(row) for row in rows]


async def get_agent_services_latest(pool: Any, agent_id: str) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT DISTINCT ON (service_name)
                service_name,
                active,
                time
            FROM metrics_service
            WHERE agent_id = $1
            ORDER BY service_name, time DESC
            """,
            agent_id,
        )
    return [dict(row) for row in rows]


async def get_service_history(
    pool: Any,
    agent_id: str,
    service_name: str,
    hours: int = 24,
) -> list[dict[str, Any]]:
    async with pool.acquire() as conn:
        rows = await conn.fetch(
            """
            SELECT
                time,
                active
            FROM metrics_service
            WHERE
                agent_id = $1
                AND service_name = $2
                AND time > NOW() - $3::INTERVAL
            ORDER BY time ASC
            """,
            agent_id,
            service_name,
            f"{hours} hours",
        )
    return [dict(row) for row in rows]
