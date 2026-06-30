from __future__ import annotations

import datetime
import logging
from dataclasses import dataclass, field
from typing import Any

import httpx

logger = logging.getLogger(__name__)

_ZERO_TIME: str = "0001-01-01T00:00:00Z"

_JOB_LABEL: str = "syswatch-server"


def _utc_now_iso() -> str:
    """Return the current UTC time as an ISO 8601 string with Z suffix."""
    return datetime.datetime.now(datetime.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


@dataclass
class AlertPayload:
    labels: dict[str, str]
    annotations: dict[str, str]
    starts_at: str = field(default_factory=_utc_now_iso)
    ends_at: str = _ZERO_TIME
    generator_url: str = "http://localhost:8000"

    def to_dict(self) -> dict[str, Any]:
        """Serialise to the dict structure Alertmanager's API expects."""
        return {
            "labels": self.labels,
            "annotations": self.annotations,
            "startsAt": self.starts_at,
            "endsAt": self.ends_at,
            "generatorURL": self.generator_url,
        }


def _agent_disconnected_payload(
    agent_id: str,
    peer: str,
    error: str,
    firing: bool = True,
) -> AlertPayload:
    return AlertPayload(
        labels={
            "alertname": "AgentDisconnected",
            "agent_id": agent_id,
            "severity": "warning",
            "job": _JOB_LABEL,
        },
        annotations={
            "summary": f"syswatch agent '{agent_id}' disconnected unexpectedly",
            "description": (
                f"Agent '{agent_id}' (peer: {peer}) gRPC stream ended with error: {error}. "
                "The agent will attempt to reconnect automatically via exponential backoff."
            ),
        },
        ends_at=_ZERO_TIME if firing else _utc_now_iso(),
        generator_url=f"http://localhost:8000/agents/{agent_id}",
    )


def _db_flush_error_payload(
    error: str,
    frames_lost: int,
    firing: bool = True,
) -> AlertPayload:
    return AlertPayload(
        labels={
            "alertname": "DBFlushError",
            "severity": "critical",
            "job": _JOB_LABEL,
        },
        annotations={
            "summary": "syswatch-server TimescaleDB flush failed — metric data lost",
            "description": (
                f"BufferedWriter COPY to TimescaleDB failed. "
                f"Frames lost in this batch: {frames_lost}. "
                f"Error: {error}. "
                "Check PostgreSQL logs and disk space. "
                "Data from the failed batch cannot be recovered."
            ),
        },
        ends_at=_ZERO_TIME if firing else _utc_now_iso(),
        generator_url="http://localhost:8000/dashboard",
    )


def _ca_expiry_warning_payload(
    days_remaining: int,
    firing: bool = True,
) -> AlertPayload:
    severity = "critical" if days_remaining <= 7 else "warning"
    return AlertPayload(
        labels={
            "alertname": "CACertExpiringSoon",
            "severity": severity,
            "job": _JOB_LABEL,
        },
        annotations={
            "summary": f"syswatch CA certificate expires in {days_remaining} days",
            "description": (
                f"The syswatch root CA certificate will expire in {days_remaining} days. "
                "When it expires, all mTLS connections (agent → server) will fail. "
                "Run install.sh on the server to regenerate the CA, then re-provision "
                "all agents (new bundle.zip for each agent)."
            ),
        },
        ends_at=_ZERO_TIME if firing else _utc_now_iso(),
        generator_url="http://localhost:8000/dashboard",
    )


class AlertSender:
    def __init__(self, alertmanager_url: str, timeout: float = 5.0) -> None:
        self._base_url = alertmanager_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

        self._firing: dict[str, str] = {}

    async def start(self) -> None:
        self._client = httpx.AsyncClient(
            timeout=self._timeout,
            follow_redirects=True,
        )
        logger.info("AlertSender started (Alertmanager URL: %s)", self._base_url)

    async def stop(self) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None
        logger.info("AlertSender stopped")

    async def _send(self, payloads: list[AlertPayload]) -> None:
        if self._client is None:
            logger.error(
                "AlertSender._send() called before start(). Call start() in main.py."
            )
            return

        url = f"{self._base_url}/api/v2/alerts"
        body = [p.to_dict() for p in payloads]

        try:
            response = await self._client.post(url, json=body)

            if response.status_code == 200:
                logger.debug(
                    "Alert(s) sent to Alertmanager: %s",
                    [p.labels.get("alertname") for p in payloads],
                )
            else:
                logger.error(
                    "Alertmanager returned HTTP %d for alerts %s: %s",
                    response.status_code,
                    [p.labels.get("alertname") for p in payloads],
                    response.text[:200],
                )

        except httpx.TimeoutException:
            logger.warning(
                "Alertmanager request timed out after %.1fs (URL: %s). "
                "Is Alertmanager running on port 9093?",
                self._timeout,
                url,
            )

        except httpx.ConnectError:
            logger.warning(
                "Cannot connect to Alertmanager at %s. "
                "Is Alertmanager installed and running? (systemctl status alertmanager)",
                url,
            )

        except Exception as exc:
            logger.error(
                "Unexpected error sending alert to Alertmanager: %s",
                exc,
                exc_info=True,
            )

    async def fire_agent_disconnected(
        self,
        agent_id: str,
        peer: str,
        error: str,
    ) -> None:
        payload = _agent_disconnected_payload(agent_id, peer, error, firing=True)
        fingerprint = _fingerprint(payload.labels)
        self._firing[fingerprint] = payload.starts_at
        logger.info("Firing AgentDisconnected alert for agent_id=%r", agent_id)
        await self._send([payload])

    async def resolve_agent_disconnected(self, agent_id: str) -> None:
        payload = _agent_disconnected_payload(agent_id, peer="", error="", firing=False)
        fingerprint = _fingerprint(payload.labels)
        self._firing.pop(fingerprint, None)
        logger.info("Resolving AgentDisconnected alert for agent_id=%r", agent_id)
        await self._send([payload])

    async def fire_db_flush_error(self, error: str, frames_lost: int) -> None:
        payload = _db_flush_error_payload(error, frames_lost, firing=True)
        fingerprint = _fingerprint(payload.labels)
        self._firing[fingerprint] = payload.starts_at
        logger.info("Firing DBFlushError alert (frames_lost=%d)", frames_lost)
        await self._send([payload])

    async def resolve_db_flush_error(self) -> None:
        payload = _db_flush_error_payload(error="", frames_lost=0, firing=False)
        fingerprint = _fingerprint(payload.labels)
        self._firing.pop(fingerprint, None)
        logger.info("Resolving DBFlushError alert")
        await self._send([payload])

    async def fire_ca_expiry_warning(self, days_remaining: int) -> None:
        payload = _ca_expiry_warning_payload(days_remaining, firing=True)
        fingerprint = _fingerprint(payload.labels)
        self._firing[fingerprint] = payload.starts_at
        logger.warning(
            "Firing CACertExpiringSoon alert (days_remaining=%d)", days_remaining
        )
        await self._send([payload])

    def currently_firing(self) -> list[str]:
        return list(self._firing.keys())


def _fingerprint(labels: dict[str, str]) -> str:
    return ",".join(f"{k}={v}" for k, v in sorted(labels.items()))
