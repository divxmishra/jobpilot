"""Single-user access control for the hosted deployment.

- Locally (development) with no JOBPILOT_PASSWORD set, the app is open on 127.0.0.1.
- In production, JOBPILOT_PASSWORD is mandatory; without it every API call is refused.
- Sessions are signed, HttpOnly, SameSite=Lax cookies (Secure in production).
- Mutating API requests must carry the X-Requested-With header (CSRF defence).
- Login attempts are rate-limited per client IP.
"""
from __future__ import annotations

import hmac
import time
from collections import defaultdict, deque

from fastapi import Request
from itsdangerous import BadSignature, SignatureExpired, URLSafeTimedSerializer

from .config import settings

COOKIE = "jobpilot_session"
MAX_AGE = 7 * 24 * 3600
_serializer = URLSafeTimedSerializer(settings.secret_key, salt="jobpilot-session")
_attempts: dict[str, deque] = defaultdict(deque)


def issue_token() -> str:
    return _serializer.dumps({"u": "owner", "t": int(time.time())})


def valid_token(token: str | None) -> bool:
    if not token:
        return False
    try:
        _serializer.loads(token, max_age=MAX_AGE)
        return True
    except (BadSignature, SignatureExpired):
        return False


def is_authenticated(request: Request) -> bool:
    if not settings.auth_required:
        return True
    return valid_token(request.cookies.get(COOKIE))


def check_password(pw: str) -> bool:
    if not settings.password:
        return False
    return hmac.compare_digest(pw.encode(), settings.password.encode())


def rate_limited(ip: str) -> bool:
    q = _attempts[ip]
    now = time.time()
    while q and now - q[0] > 900:
        q.popleft()
    return len(q) >= 10


def record_attempt(ip: str) -> None:
    _attempts[ip].append(time.time())
