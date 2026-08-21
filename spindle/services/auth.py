"""Single-user password auth, per phase-09-hardening.md task 1.

There's exactly one account, so there's no user table: the password's
argon2id hash lives in the `setting` table under key `"auth"` (reusing the
existing key/value store rather than a new migration), and the session is a
signed, stateless cookie -- no server-side session store to expire or clean
up. `AuthMiddleware` in `main.py` is the only thing that calls
`verify_session_cookie`; routes in `api/routes/auth.py` are the only thing
that calls everything else.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass, field

from argon2 import PasswordHasher
from argon2.exceptions import Argon2Error
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from spindle.models.setting import Setting

SESSION_COOKIE_NAME = "spindle_session"
SESSION_TTL_SECONDS = 30 * 24 * 3600  # "30-day sliding expiry" (phase doc task 1)

_SETTING_KEY = "auth"
_RATE_LIMIT_ATTEMPTS = 5
_RATE_LIMIT_WINDOW_SECONDS = 15 * 60

_hasher = PasswordHasher()


class AuthService:
    def __init__(
        self, session_factory: async_sessionmaker[AsyncSession], secret_key: bytes
    ) -> None:
        self._session_factory = session_factory
        self._secret_key = secret_key

    async def is_configured(self) -> bool:
        return await self._get_hash() is not None

    async def set_password(self, password: str) -> None:
        password_hash = _hasher.hash(password)
        async with self._session_factory() as session:
            row = await session.get(Setting, _SETTING_KEY)
            if row is None:
                session.add(Setting(key=_SETTING_KEY, value={"password_hash": password_hash}))
            else:
                row.value = {"password_hash": password_hash}
            await session.commit()

    async def verify_password(self, password: str) -> bool:
        stored = await self._get_hash()
        if stored is None:
            return False
        try:
            _hasher.verify(stored, password)
        except Argon2Error:
            return False
        return True

    def create_session_cookie(self) -> str:
        expires_at = int(time.time()) + SESSION_TTL_SECONDS
        return self._sign(expires_at)

    def verify_session_cookie(self, cookie_value: str) -> bool:
        """Constant-time signature check (task 1) via `hmac.compare_digest`."""
        try:
            expires_str, signature = cookie_value.rsplit(".", 1)
            expires_at = int(expires_str)
        except ValueError:
            return False
        if not hmac.compare_digest(signature, self._signature(expires_str)):
            return False
        return time.time() < expires_at

    async def _get_hash(self) -> str | None:
        async with self._session_factory() as session:
            row = await session.get(Setting, _SETTING_KEY)
        if row is None:
            return None
        value = row.value.get("password_hash")
        return value if isinstance(value, str) else None

    def _sign(self, expires_at: int) -> str:
        payload = str(expires_at)
        return f"{payload}.{self._signature(payload)}"

    def _signature(self, payload: str) -> str:
        digest = hmac.new(self._secret_key, payload.encode(), hashlib.sha256).digest()
        return base64.urlsafe_b64encode(digest).decode().rstrip("=")


@dataclass
class LoginRateLimiter:
    """5 attempts / 15 min / IP (task 1). In-memory is fine -- Spindle is a
    single process with no shared state across instances."""

    _attempts: dict[str, list[float]] = field(default_factory=dict)

    def check(self, key: str) -> bool:
        now = time.time()
        recent = [t for t in self._attempts.get(key, []) if now - t < _RATE_LIMIT_WINDOW_SECONDS]
        self._attempts[key] = recent
        return len(recent) < _RATE_LIMIT_ATTEMPTS

    def record(self, key: str) -> None:
        self._attempts.setdefault(key, []).append(time.time())
