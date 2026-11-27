"""Per-request / per-task user context.

Every user-owned query is scoped with `uid()`. The value is set by the auth
middleware from the server-side session (never from client input) and carried
into background tasks explicitly. Code paths with no user (public pages,
system jobs) must not call `uid()`.
"""
from __future__ import annotations

import contextvars
from contextlib import contextmanager

_user: contextvars.ContextVar[dict | None] = contextvars.ContextVar("jobpilot_user", default=None)
_session: contextvars.ContextVar[str | None] = contextvars.ContextVar("jobpilot_session", default=None)


class NoUserContext(RuntimeError):
    pass


def set_user(user: dict | None, session_id: str | None = None):
    return _user.set(user), _session.set(session_id)


def reset(tokens) -> None:
    _user.reset(tokens[0])
    _session.reset(tokens[1])


def current_user() -> dict | None:
    return _user.get()


def session_id() -> str | None:
    return _session.get()


def uid() -> int:
    u = _user.get()
    if not u:
        raise NoUserContext("No authenticated user in context")
    return int(u["id"])


def uid_or_none() -> int | None:
    u = _user.get()
    return int(u["id"]) if u else None


@contextmanager
def as_user(user: dict | None, session: str | None = None):
    tokens = set_user(user, session)
    try:
        yield
    finally:
        reset(tokens)
