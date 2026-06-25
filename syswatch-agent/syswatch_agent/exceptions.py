from __future__ import annotations


class SyswatchError(Exception):
    """Base exception for all syswatch-agent errors."""


class ConfigError(SyswatchError):
    """Configuration file missing, unreadable, or structurally invalid."""


class CertificateError(SyswatchError):
    """mTLS certificate or key file missing or unreadable."""


class CollectorError(SyswatchError):
    """A kernel or system collector failed to read its data source."""


class StreamError(SyswatchError):
    """Fatal error on the gRPC streaming connection."""
