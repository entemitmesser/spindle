from __future__ import annotations

import logging
import sys
from pathlib import Path
from typing import cast

import structlog
from structlog.types import EventDict, WrappedLogger

from spindle.config import Settings

_REDACTED = "***"


def _redact_processor(secrets: list[str]) -> structlog.types.Processor:
    def processor(_logger: WrappedLogger, _method_name: str, event_dict: EventDict) -> EventDict:
        if not secrets:
            return event_dict
        for key, value in event_dict.items():
            if isinstance(value, str):
                for secret in secrets:
                    if secret and secret in value:
                        value = value.replace(secret, _REDACTED)
                event_dict[key] = value
        return event_dict

    return processor


def configure_logging(settings: Settings) -> None:
    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.processors.TimeStamper(fmt="iso"),
        _redact_processor(settings.secret_values()),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.format_exc_info,
    ]

    renderer: structlog.types.Processor = (
        structlog.processors.JSONRenderer()
        if settings.log_json
        else structlog.dev.ConsoleRenderer()
    )

    structlog.configure(
        processors=[*shared_processors, structlog.stdlib.ProcessorFormatter.wrap_for_formatter],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.make_filtering_bound_logger(
            logging.getLevelNamesMapping().get(settings.log_level.upper(), logging.INFO)
        ),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processor=renderer, foreign_pre_chain=shared_processors
    )
    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers = [handler]
    root_logger.setLevel(settings.log_level.upper())


def job_logger(job_id: int, stage: str, *, data_dir: Path) -> structlog.stdlib.BoundLogger:
    log_dir = data_dir / "logs" / f"job-{job_id:06d}"
    log_dir.mkdir(parents=True, exist_ok=True)

    stdlib_logger = logging.getLogger(f"spindle.job.{job_id}.{stage}")
    stdlib_logger.setLevel(logging.INFO)
    stdlib_logger.propagate = True

    if not any(
        isinstance(h, logging.FileHandler) and h.baseFilename == str(log_dir / f"{stage}.log")
        for h in stdlib_logger.handlers
    ):
        file_handler = logging.FileHandler(log_dir / f"{stage}.log", encoding="utf-8")
        file_handler.setFormatter(
            structlog.stdlib.ProcessorFormatter(processor=structlog.processors.JSONRenderer())
        )
        stdlib_logger.addHandler(file_handler)

    return cast(structlog.stdlib.BoundLogger, structlog.wrap_logger(stdlib_logger))
