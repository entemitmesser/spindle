from __future__ import annotations

from pydantic import BaseModel, Field


class AuthStatus(BaseModel):
    auth_enabled: bool
    needs_setup: bool
    authenticated: bool


class SetupRequest(BaseModel):
    password: str = Field(min_length=8)


class LoginRequest(BaseModel):
    password: str
