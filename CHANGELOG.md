# Changelog

All notable changes to Spindle are documented here. Format loosely follows
[Keep a Changelog](https://keepachangelog.com/en/1.1.0/).

## [Unreleased]

### Added
- Optional single-user password auth (`SPINDLE_AUTH_ENABLED`): session cookie, 30-day
  sliding expiry, rate-limited login.
- Stall watchdog: a task with no progress for `behaviour.stall_timeout_minutes`
  (default 45) is failed instead of hanging forever.
- Startup self-check and a 60s disk-space monitor, both surfaced as `system.warning`
  events and a header banner in the UI.
- `POST /system/backup` and `spindle backup`/`restore`/`scan-library` CLI commands.
- `GET /metrics` (Prometheus text format, `SPINDLE_METRICS_ENABLED`).
- App version shown in the UI footer.

### Fixed
- A task cancelled by `Dispatcher.stop()` (graceful shutdown) or `cancel_task()` (user
  cancel) now actually runs `stage.cleanup()`, so its subprocess is torn down by
  Spindle itself instead of relying on Docker's cgroup kill on container stop.

## [0.1.0] - 2026-08-21
Initial phases 0-8: drive detection, rip/transcode pipeline, metadata and subtitle
enrichment, Jellyfin-compatible library organization, the full REST/SSE API, and the
web UI. See `docs/progress.md` for the phase-by-phase history.
