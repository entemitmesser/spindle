from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy.engine import Dialect
from sqlalchemy.ext.mutable import MutableDict
from sqlalchemy.types import TypeDecorator
from sqlmodel import JSON, DateTime

MutableJSON = MutableDict.as_mutable(JSON)


def utc_now() -> datetime:
    return datetime.now(UTC)


class UTCDateTime(TypeDecorator[datetime]):
    """Stores datetimes as naive UTC, returns them tz-aware.

    SQLite drops tzinfo on write; this type normalises to UTC on the way in
    and re-attaches ``UTC`` on the way out so callers never see naive values.
    """

    impl = DateTime
    cache_ok = True

    def process_bind_param(self, value: datetime | None, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        if value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value.astimezone(UTC).replace(tzinfo=None)

    def process_result_value(self, value: Any, dialect: Dialect) -> datetime | None:
        if value is None:
            return None
        assert isinstance(value, datetime)  # noqa: S101 -- narrows the driver's Any for mypy
        return value.replace(tzinfo=UTC)
