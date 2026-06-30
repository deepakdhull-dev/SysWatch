-- syswatch retention script
-- Replaces TimescaleDB's native add_retention_policy(), which is a TSL
-- (Timescale Community License) feature unavailable on the Apache 2.0
-- edition shipped by Debian's postgresql-*-timescaledb package.
--
-- Run daily via the syswatch-retention.timer / syswatch-retention.service
-- systemd units installed by install.sh. Safe to re-run: DELETE with a
-- WHERE clause is idempotent past the cutoff.
--
-- Retention window: 30 days, matching the original native policy's
-- drop_after value. Adjust RETENTION_DAYS in install.sh if this changes.

DELETE FROM metrics        WHERE time < NOW() - INTERVAL '30 days';
DELETE FROM metrics_disk   WHERE time < NOW() - INTERVAL '30 days';
DELETE FROM metrics_network WHERE time < NOW() - INTERVAL '30 days';
DELETE FROM metrics_service WHERE time < NOW() - INTERVAL '30 days';
