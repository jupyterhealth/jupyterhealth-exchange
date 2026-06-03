from __future__ import annotations

from contextvars import ContextVar, Token
from dataclasses import dataclass


@dataclass(frozen=True)
class AuthContext:
    bearer_token: str
    subject: str
    expires_at: int


_current: ContextVar[AuthContext | None] = ContextVar("jhe_mcp_auth", default=None)


def current_auth() -> AuthContext | None:
    return _current.get()


def current_auth_required() -> AuthContext:
    ctx = _current.get()
    if ctx is None:
        raise RuntimeError("No authenticated context for this MCP request")
    return ctx


def set_current_auth(ctx: AuthContext) -> Token[AuthContext | None]:
    return _current.set(ctx)
