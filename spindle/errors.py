from __future__ import annotations

import re

_CAMEL_BOUNDARY = re.compile(r"(?<!^)(?=[A-Z])")


def error_code(exc_type: type[Exception]) -> str:
    return _CAMEL_BOUNDARY.sub("_", exc_type.__name__).lower()


class SpindleError(Exception):
    status_code: int = 500

    def __init__(self, message: str, detail: dict[str, object] | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.detail = detail or {}


class AdapterError(SpindleError):
    """An external tool (makemkvcon, HandBrakeCLI, ffmpeg, ...) failed."""

    status_code = 502


class ConfigurationError(SpindleError):
    status_code = 500


class NotFoundError(SpindleError):
    status_code = 404


class ConflictError(SpindleError):
    status_code = 409
