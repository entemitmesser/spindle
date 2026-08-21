from __future__ import annotations

from fastapi import APIRouter, Request, Response

from spindle.config import Settings
from spindle.errors import ConflictError, RateLimitedError, UnauthorizedError
from spindle.schemas.auth import AuthStatus, LoginRequest, SetupRequest
from spindle.services.auth import SESSION_COOKIE_NAME, SESSION_TTL_SECONDS, AuthService

router = APIRouter()


def _client_key(request: Request) -> str:
    return request.client.host if request.client else "unknown"


def _set_session_cookie(request: Request, response: Response, auth: AuthService) -> None:
    response.set_cookie(
        SESSION_COOKIE_NAME,
        auth.create_session_cookie(),
        max_age=SESSION_TTL_SECONDS,
        httponly=True,
        samesite="lax",
        secure=request.url.scheme == "https",
        path="/",
    )


@router.get("/me", operation_id="get_auth_status")
async def get_auth_status(request: Request) -> AuthStatus:
    settings: Settings = request.app.state.settings
    if not settings.auth_enabled:
        return AuthStatus(auth_enabled=False, needs_setup=False, authenticated=True)

    auth: AuthService = request.app.state.auth
    token = request.cookies.get(SESSION_COOKIE_NAME)
    authenticated = token is not None and auth.verify_session_cookie(token)
    needs_setup = not await auth.is_configured()
    return AuthStatus(auth_enabled=True, needs_setup=needs_setup, authenticated=authenticated)


@router.post("/setup", operation_id="setup_auth")
async def setup_auth(request: Request, response: Response, body: SetupRequest) -> AuthStatus:
    auth: AuthService = request.app.state.auth
    if await auth.is_configured():
        raise ConflictError("A password is already configured.")
    await auth.set_password(body.password)
    _set_session_cookie(request, response, auth)
    return AuthStatus(auth_enabled=True, needs_setup=False, authenticated=True)


@router.post("/login", operation_id="login")
async def login(request: Request, response: Response, body: LoginRequest) -> AuthStatus:
    auth: AuthService = request.app.state.auth
    limiter = request.app.state.login_rate_limiter
    key = _client_key(request)

    if not limiter.check(key):
        raise RateLimitedError("Too many login attempts. Try again in a few minutes.")

    if await auth.is_configured() and await auth.verify_password(body.password):
        _set_session_cookie(request, response, auth)
        return AuthStatus(auth_enabled=True, needs_setup=False, authenticated=True)

    limiter.record(key)
    raise UnauthorizedError("Incorrect password.")


@router.post("/logout", operation_id="logout")
async def logout(response: Response) -> AuthStatus:
    response.delete_cookie(SESSION_COOKIE_NAME, path="/")
    return AuthStatus(auth_enabled=True, needs_setup=False, authenticated=False)
