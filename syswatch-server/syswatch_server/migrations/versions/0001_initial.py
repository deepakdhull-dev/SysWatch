from alembic import op

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None


def upgrade() -> None:
    """
    Create the full syswatch schema from scratch.
    Safe to re-run: all statements use IF NOT EXISTS guards.
    """
    op.execute(
        """
        CREATE TABLE IF NOT EXISTS agents (
            agent_id      TEXT        PRIMARY KEY,
            hostname      TEXT        NOT NULL,
            kernel        TEXT        NOT NULL DEFAULT '',
            os_name       TEXT        NOT NULL DEFAULT '',
            cpu_model     TEXT        NOT NULL DEFAULT '',
            cpu_cores     INTEGER     NOT NULL DEFAULT 0,
            cpu_threads   INTEGER     NOT NULL DEFAULT 0,
            registered_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen     TIMESTAMPTZ NOT NULL DEFAULT NOW()
        )
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS metrics (
            agent_id        TEXT        NOT NULL,
            time            TIMESTAMPTZ NOT NULL,
            frame_id        TEXT        NOT NULL,
            cpu_pct         REAL        NOT NULL,
            ram_pct         REAL        NOT NULL,
            ram_used        BIGINT      NOT NULL,
            ram_total       BIGINT      NOT NULL,
            swap_pct        REAL        NOT NULL,
            swap_used       BIGINT      NOT NULL,
            swap_total      BIGINT      NOT NULL,
            load_1m         REAL        NOT NULL,
            load_5m         REAL        NOT NULL,
            load_15m        REAL        NOT NULL,
            frames_dropped  INTEGER     NOT NULL DEFAULT 0
        )
        """
    )

    op.execute(
        """
        SELECT create_hypertable(
            'metrics',
            'time',
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists       => TRUE,
            migrate_data        => TRUE
        )
        """
    )
    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_metrics_agent_time
        ON metrics (agent_id, time DESC)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS metrics_disk (
            agent_id    TEXT        NOT NULL,
            time        TIMESTAMPTZ NOT NULL,
            frame_id    TEXT        NOT NULL,
            mount_point TEXT        NOT NULL,
            pct         REAL        NOT NULL,
            used        BIGINT      NOT NULL,
            total       BIGINT      NOT NULL
        )
        """
    )

    op.execute(
        """
        SELECT create_hypertable(
            'metrics_disk',
            'time',
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists       => TRUE
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_metrics_disk_agent_mount_time
        ON metrics_disk (agent_id, mount_point, time DESC)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS metrics_network (
            agent_id       TEXT        NOT NULL,
            time           TIMESTAMPTZ NOT NULL,
            frame_id       TEXT        NOT NULL,
            interface_name TEXT        NOT NULL,
            bytes_in       BIGINT      NOT NULL,
            bytes_out      BIGINT      NOT NULL,
            packets_in     BIGINT      NOT NULL,
            packets_out    BIGINT      NOT NULL,
            errors_in      BIGINT      NOT NULL,
            errors_out     BIGINT      NOT NULL
        )
        """
    )

    op.execute(
        """
        SELECT create_hypertable(
            'metrics_network',
            'time',
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists       => TRUE
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_metrics_network_agent_iface_time
        ON metrics_network (agent_id, interface_name, time DESC)
        """
    )

    op.execute(
        """
        CREATE TABLE IF NOT EXISTS metrics_service (
            agent_id     TEXT        NOT NULL,
            time         TIMESTAMPTZ NOT NULL,
            frame_id     TEXT        NOT NULL,
            service_name TEXT        NOT NULL,
            active       BOOLEAN     NOT NULL
        )
        """
    )

    op.execute(
        """
        SELECT create_hypertable(
            'metrics_service',
            'time',
            chunk_time_interval => INTERVAL '1 day',
            if_not_exists       => TRUE
        )
        """
    )

    op.execute(
        """
        CREATE INDEX IF NOT EXISTS idx_metrics_service_agent_name_time
        ON metrics_service (agent_id, service_name, time DESC)
        """
    )

    op.execute(
        """
        -- NOTE: TimescaleDB continuous aggregates (timescaledb.continuous)
        -- require the Timescale/Community license tier. Debian's own
        -- postgresql-*-timescaledb package ships the Apache 2.0 edition,
        -- which does not include this feature — attempting to create one
        -- raises asyncpg.exceptions.FeatureNotSupportedError. A plain view
        -- achieves the same query (hourly rollup via time_bucket, which IS
        -- Apache-licensed) computed live at query time instead of
        -- incrementally materialized in the background. For this dataset's
        -- scale this is an acceptable tradeoff; if a Timescale-licensed
        -- install becomes available later, this can be swapped back to a
        -- real continuous aggregate.
        CREATE OR REPLACE VIEW metrics_hourly AS
        SELECT
            time_bucket('1 hour', time)  AS bucket,
            agent_id,
            AVG(cpu_pct)                 AS avg_cpu,
            MAX(cpu_pct)                 AS max_cpu,
            AVG(ram_pct)                 AS avg_ram,
            MAX(ram_pct)                 AS max_ram,
            AVG(load_1m)                 AS avg_load_1m,
            AVG(swap_pct)                AS avg_swap,
            SUM(frames_dropped)          AS total_dropped
        FROM metrics
        GROUP BY bucket, agent_id
        """
    )

    for table in ("metrics", "metrics_disk", "metrics_network", "metrics_service"):
        op.execute(
            f"""
            ALTER TABLE {table} SET (
                timescaledb.compress,
                timescaledb.compress_segmentby = 'agent_id',
                timescaledb.compress_orderby   = 'time DESC'
            )
            """
        )
        op.execute(
            f"""
            SELECT add_compression_policy(
                '{table}',
                compress_after => INTERVAL '7 days',
                if_not_exists  => TRUE
            )
            """
        )

    for table in ("metrics", "metrics_disk", "metrics_network", "metrics_service"):
        op.execute(
            f"""
            SELECT add_retention_policy(
                '{table}',
                drop_after    => INTERVAL '30 days',
                if_not_exists => TRUE
            )
            """
        )


def downgrade() -> None:

    op.execute("DROP VIEW IF EXISTS metrics_hourly CASCADE")

    for table in ("metrics_service", "metrics_network", "metrics_disk", "metrics"):
        op.execute(f"DROP TABLE IF EXISTS {table} CASCADE")

    op.execute("DROP TABLE IF EXISTS agents CASCADE")
