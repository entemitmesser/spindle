"""Job-completion notifications, per phase-06-library-jellyfin.md task 4.

A single plain webhook (POST of a JSON payload) rather than Apprise's
multi-provider fan-out (Discord/Telegram/ntfy/Gotify/email/...) — the phase
doc's stretch goal. `NotificationConfig` (`spindle/config.py`) exposes one
`webhook_url` today; adding Apprise is a separate, larger decision (a new
dependency, plus a config shape change to a list of provider URLs) left for
whoever wires multi-provider support in.

Always best-effort: an unconfigured or failing webhook returns `False`
rather than raising, so `NotifyStage` can never fail a job over this —
matching `SubtitleStage`'s skip-not-fail policy.
"""

from __future__ import annotations

from typing import Any, Literal

import httpx
import structlog

from spindle.config import NotificationConfig
from spindle.models.job import Job

logger = structlog.get_logger(__name__)

NotifyEvent = Literal["needs_review", "completed", "failed", "system_warning"]

_EVENT_LABELS: dict[str, str] = {
    "needs_review": "Needs review",
    "completed": "Completed",
    "failed": "Failed",
    "system_warning": "Warning",
}


class NotificationService:
    def __init__(self, client: httpx.AsyncClient) -> None:
        self._client = client

    async def notify_job(
        self,
        job: Job,
        *,
        event: NotifyEvent,
        config: NotificationConfig,
        duration_seconds: int | None = None,
        output_path: str | None = None,
    ) -> bool:
        if not self._should_send(event, config):
            return False

        title = f"{job.title} ({job.year})" if job.title and job.year else (job.title or "Job")
        lines = [f"{title} — {_EVENT_LABELS[event]}"]
        if duration_seconds is not None:
            lines.append(f"Duration: {duration_seconds // 60} min")
        if output_path:
            lines.append(f"Output: {output_path}")
        if job.error:
            lines.append(f"Error: {job.error}")

        payload = {
            "event": event,
            "job_id": job.id,
            "title": title,
            "message": "\n".join(lines),
        }
        return await self._send(config.webhook_url, payload)

    async def notify_warning(self, *, code: str, message: str, config: NotificationConfig) -> bool:
        if not self._should_send("system_warning", config):
            return False
        payload = {"event": "system_warning", "code": code, "message": message}
        return await self._send(config.webhook_url, payload)

    async def send_test(self, webhook_url: str | None) -> bool:
        """`POST /settings/test/notify`: a direct connectivity check that
        bypasses `enabled`/`notify_on` filtering, since the user is testing
        the webhook URL itself, not asking whether a real event would fire."""
        payload = {"event": "test", "message": "Spindle test notification"}
        return await self._send(webhook_url, payload)

    def _should_send(self, event: str, config: NotificationConfig) -> bool:
        return config.enabled and bool(config.webhook_url) and event in config.notify_on

    async def _send(self, webhook_url: str | None, payload: dict[str, Any]) -> bool:
        if not webhook_url:
            return False
        try:
            response = await self._client.post(webhook_url, json=payload)
            response.raise_for_status()
        except httpx.HTTPError:
            logger.warning("notify.webhook_failed", notify_event=payload.get("event"))
            return False
        return True
