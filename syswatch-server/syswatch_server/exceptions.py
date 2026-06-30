from __future__ import annotations


class SyswatchServerError(Exception):
    """Base exception for all syswatch-server errors."""


class ConfigError(SyswatchServerError):
    """Configuration file missing, unreadable, or structurally invalid."""


class CertificateError(SyswatchServerError):
    """A required certificate, key, or CA file is missing or invalid."""


class DatabaseError(SyswatchServerError):
    """Database connection or query failure at startup."""


class MigrationError(SyswatchServerError):
    """Alembic migration failed to apply."""
