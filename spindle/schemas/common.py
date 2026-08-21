from __future__ import annotations

from pydantic import BaseModel

DEFAULT_LIMIT = 50
MAX_LIMIT = 200


class Page[T](BaseModel):
    items: list[T]
    total: int
    limit: int
    offset: int
