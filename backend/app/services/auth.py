"""Accounts, password hashing, sessions, one-time tokens, admin second factor (TOTP),
brute-force protection and Google OAuth (enabled only when configured)."""
from __future__ import annotations

import base64
import hashlib
import hmac
import os
import re
import secrets
import struct
import time
from datetime import datetime, timedelta, timezone
from urllib.parse import urlencode

import httpx

from .. import db
from ..config import settings

ROLES = ("owner", "admin", "support", "user")
STAFF_ROLES = ("owner", "admin", "support")
SESSION_IDLE_DAYS = 7
SESSION_ABSOLUTE_DAYS = 30
ELEVATION_HOURS = 12
EMAIL_RE = re.compile(r"^[^@\s]{1,64}@[^@\s]+\.[A-Za-z]{2,}$")


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def iso(dt: datetime) -> str:
    return dt.isoformat()


def parse(s: str | None) -> datetime | None:
    if not s:
        return None
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


def _h(value: str) -> str:
    return hashlib.sha256((settings.secret_key + "|" + value).encode()).hexdigest()


def ip_hash(ip: str | None) -> str | None:
    return _h("ip:" + ip)[:24] if ip else None


# ------------------------------------------------------------------ passwords (scrypt)
def hash_password(pw: str) -> str:
    salt = os.urandom(16)
    dk = hashlib.scrypt(pw.encode(), salt=salt, n=2 ** 14, r=8, p=1, dklen=32)
    return "scrypt$16384$8$1$" + base64.b64encode(salt).decode() + "$" + base64.b64encode(dk).decode()


def verify_password(pw: str, stored: str | None) -> bool:
    if not stored or not stored.startswith("scrypt$"):
        return False
    _, n, r, p, salt, dk = stored.split("$")
    calc = hashlib.scrypt(pw.encode(), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
    return hmac.compare_digest(calc, base64.b64decode(dk))


def password_problems(pw: str, email: str = "") -> list[str]:
    probs = []
    if len(pw) < 10:
        probs.append("Use at least 10 characters")
    if len(pw) > 200:
        probs.append("Use at most 200 characters")
    if pw.lower() in {"password123", "1234567890", "qwertyuiop"} or (email and pw.lower() == email.lower()):
        probs.append("Choose a less guessable password")
    if pw.isdigit() or pw.isalpha():
        probs.append("Mix letters with numbers or symbols")
    return probs


# ------------------------------------------------------------------ users
PUBLIC_USER_FIELDS = ("id", "email", "name", "role", "status", "email_verified", "totp_enabled", "created_at", "last_login_at",
                      "profile_completed_at", "analytics_opt_out")


def public_user(u: dict | None) -> dict | None:
    if not u:
        return None
    return {k: u.get(k) for k in PUBLIC_USER_FIELDS} | {"has_password": bool(u.get("password_hash")),
                                                      "google_linked": bool(u.get("google_sub"))}


def get_user(user_id: int) -> dict | None:
    return db.query_one("SELECT * FROM users WHERE id = ?", (user_id,))


def get_user_by_email(email: str) -> dict | None:
    return db.query_one("SELECT * FROM users WHERE email = ? COLLATE NOCASE", (email.strip(),))


def create_user(email: str, password: str | None, name: str | None, *, role: str = "user", google_sub: str | None = None,
                email_verified: bool = False) -> dict:
    email = email.strip().lower()
    if not EMAIL_RE.match(email):
        raise ValueError("Enter a valid email address")
    if role not in ROLES:
        raise ValueError("Invalid role")
    if get_user_by_email(email):
        raise ValueError("An account with this email already exists")
    if password is not None:
        probs = password_problems(password, email)
        if probs:
            raise ValueError("; ".join(probs))
    now = db.now_iso()
    uid = db.execute(
        "INSERT INTO users(email, name, password_hash, google_sub, email_verified, role, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
        (email, (name or "").strip()[:120] or None, hash_password(password) if password else None, google_sub,
         1 if email_verified else 0, role, now, now))
    return get_user(uid)


def owner_exists() -> bool:
    return bool(db.query_one("SELECT id FROM users WHERE role = 'owner' AND status != 'deleted'"))


def claim_legacy_data(owner_id: int) -> dict:
    """Assign pre-multi-user rows (user_id NULL) to the owner so nothing is lost."""
    counts = {}
    with db.tx() as conn:
        for t in ("uploads", "profile_versions", "companies", "jobs", "resumes", "applications", "contacts", "outreach_drafts",
                  "audit_events", "ai_usage", "discovery_runs", "tasks", "notifications"):
            counts[t] = conn.execute(f"UPDATE {t} SET user_id = ? WHERE user_id IS NULL", (owner_id,)).rowcount
        legacy = {r["key"]: r["value"] for r in conn.execute(
            "SELECT key, value FROM settings WHERE key IN ('preferences','ai','notifications','schedule','last_discovery')")}
        for k, v in legacy.items():
            conn.execute("INSERT OR IGNORE INTO user_settings(user_id, key, value, updated_at) VALUES (?,?,?,?)",
                         (owner_id, k, v, db.now_iso()))
    return counts


# ------------------------------------------------------------------ brute-force protection
def _attempt_keys(ip: str | None, email: str | None) -> list[str]:
    keys = []
    if ip:
        keys.append("ip:" + (ip_hash(ip) or ""))
    if email:
        keys.append("em:" + _h("email:" + email.lower())[:24])
    return keys


def too_many_attempts(ip: str | None, email: str | None, limit_ip: int = 30, limit_email: int = 8, window_min: int = 15) -> bool:
    since = iso(_now() - timedelta(minutes=window_min))
    for k in _attempt_keys(ip, email):
        n = db.query_one("SELECT COUNT(*) n FROM login_attempts WHERE key = ? AND success = 0 AND at >= ?", (k, since))["n"]
        if n >= (limit_ip if k.startswith("ip:") else limit_email):
            return True
    return False


def record_attempt(ip: str | None, email: str | None, success: bool) -> None:
    for k in _attempt_keys(ip, email):
        db.execute("INSERT INTO login_attempts(key, success, at) VALUES (?,?,?)", (k, 1 if success else 0, db.now_iso()))
    if success and email:
        db.execute("DELETE FROM login_attempts WHERE key = ? AND success = 0", ("em:" + _h("email:" + email.lower())[:24],))


# ------------------------------------------------------------------ sessions
def create_session(user_id: int, user_agent: str | None, ip: str | None) -> str:
    token = secrets.token_urlsafe(32)
    now = _now()
    db.execute("INSERT INTO sessions(id, user_id, created_at, last_seen_at, expires_at, user_agent, ip_hash) VALUES (?,?,?,?,?,?,?)",
               (_h("session:" + token), user_id, iso(now), iso(now), iso(now + timedelta(days=SESSION_IDLE_DAYS)),
                (user_agent or "")[:200], ip_hash(ip)))
    db.execute("UPDATE users SET last_login_at = ?, updated_at = ? WHERE id = ?", (iso(now), iso(now), user_id))
    return token


def session_from_token(token: str | None) -> tuple[dict, dict] | None:
    """Return (session, user) for a valid cookie token; slides the idle expiry."""
    if not token or len(token) > 200:
        return None
    sid = _h("session:" + token)
    s = db.query_one("SELECT * FROM sessions WHERE id = ? AND revoked = 0", (sid,))
    if not s:
        return None
    now = _now()
    if parse(s["expires_at"]) < now or parse(s["created_at"]) + timedelta(days=SESSION_ABSOLUTE_DAYS) < now:
        db.execute("UPDATE sessions SET revoked = 1 WHERE id = ?", (sid,))
        return None
    u = get_user(s["user_id"])
    if not u or u["status"] != "active":
        return None
    if (now - parse(s["last_seen_at"])).total_seconds() > 300:
        db.execute("UPDATE sessions SET last_seen_at = ?, expires_at = ? WHERE id = ?",
                   (iso(now), iso(now + timedelta(days=SESSION_IDLE_DAYS)), sid))
    return s, u


def revoke_session(token: str | None) -> None:
    if token:
        db.execute("UPDATE sessions SET revoked = 1 WHERE id = ?", (_h("session:" + token),))


def revoke_user_sessions(user_id: int, except_sid: str | None = None) -> int:
    with db.tx() as conn:
        return conn.execute("UPDATE sessions SET revoked = 1 WHERE user_id = ? AND id != ?", (user_id, except_sid or "")).rowcount


def is_elevated(session: dict | None) -> bool:
    return bool(session and session.get("elevated_until") and parse(session["elevated_until"]) > _now())


def elevate(session_id: str) -> None:
    db.execute("UPDATE sessions SET elevated_until = ? WHERE id = ?", (iso(_now() + timedelta(hours=ELEVATION_HOURS)), session_id))


# ------------------------------------------------------------------ one-time tokens
def issue_token(user_id: int, purpose: str, minutes: int) -> str:
    token = secrets.token_urlsafe(32)
    db.execute("INSERT INTO auth_tokens(id, user_id, purpose, expires_at, created_at) VALUES (?,?,?,?,?)",
               (_h(f"{purpose}:{token}"), user_id, purpose, iso(_now() + timedelta(minutes=minutes)), db.now_iso()))
    return token


def consume_token(token: str, purpose: str) -> int | None:
    tid = _h(f"{purpose}:{token}")
    t = db.query_one("SELECT * FROM auth_tokens WHERE id = ? AND purpose = ?", (tid, purpose))
    if not t or t["used_at"] or parse(t["expires_at"]) < _now():
        return None
    db.execute("UPDATE auth_tokens SET used_at = ? WHERE id = ?", (db.now_iso(), tid))
    return t["user_id"]


# ------------------------------------------------------------------ TOTP (RFC 6238) for staff second factor
def new_totp_secret() -> str:
    return base64.b32encode(os.urandom(20)).decode().rstrip("=")


def totp_code(secret: str, t: float | None = None, step: int = 30, digits: int = 6) -> str:
    key = base64.b32decode(secret + "=" * (-len(secret) % 8), casefold=True)
    counter = int((t if t is not None else time.time()) // step)
    mac = hmac.new(key, struct.pack(">Q", counter), hashlib.sha1).digest()
    off = mac[-1] & 0x0F
    code = (struct.unpack(">I", mac[off:off + 4])[0] & 0x7FFFFFFF) % (10 ** digits)
    return str(code).zfill(digits)


def verify_totp(secret: str | None, code: str) -> bool:
    if not secret or not re.fullmatch(r"\d{6}", (code or "").strip()):
        return False
    now = time.time()
    return any(hmac.compare_digest(totp_code(secret, now + d * 30), code.strip()) for d in (-1, 0, 1))


def totp_uri(secret: str, email: str) -> str:
    return "otpauth://totp/JobPilot:" + email + "?" + urlencode({"secret": secret, "issuer": "JobPilot", "digits": 6, "period": 30})


# ------------------------------------------------------------------ owner bootstrap
def bootstrap_secret() -> str:
    """Secret the owner types once to create the owner account (set in the hosting env)."""
    return os.getenv("OWNER_SETUP_TOKEN") or settings.password or ""


def check_bootstrap(secret: str, client_ip: str | None) -> bool:
    expected = bootstrap_secret()
    if expected:
        return hmac.compare_digest(secret.encode(), expected.encode())
    # Local development without a configured secret: only from the same machine.
    return not settings.is_production and client_ip in ("127.0.0.1", "::1", "testclient")


# ------------------------------------------------------------------ Google OAuth (authorization code flow)
def google_configured() -> bool:
    return bool(os.getenv("GOOGLE_CLIENT_ID") and os.getenv("GOOGLE_CLIENT_SECRET"))


def google_redirect_uri(base_url: str) -> str:
    return os.getenv("GOOGLE_REDIRECT_URI") or base_url.rstrip("/") + "/api/auth/google/callback"


def google_auth_url(state: str, redirect_uri: str) -> str:
    return "https://accounts.google.com/o/oauth2/v2/auth?" + urlencode({
        "client_id": os.getenv("GOOGLE_CLIENT_ID"), "redirect_uri": redirect_uri, "response_type": "code",
        "scope": "openid email profile", "state": state, "prompt": "select_account"})


def google_exchange(code: str, redirect_uri: str) -> dict:
    """Exchange the code and return verified identity {sub, email, email_verified, name}."""
    with httpx.Client(timeout=20) as c:
        r = c.post("https://oauth2.googleapis.com/token", data={
            "code": code, "client_id": os.getenv("GOOGLE_CLIENT_ID"), "client_secret": os.getenv("GOOGLE_CLIENT_SECRET"),
            "redirect_uri": redirect_uri, "grant_type": "authorization_code"})
        r.raise_for_status()
        access = r.json()["access_token"]
        info = c.get("https://openidconnect.googleapis.com/v1/userinfo", headers={"Authorization": f"Bearer {access}"})
        info.raise_for_status()
        data = info.json()
    if not data.get("sub") or not data.get("email"):
        raise ValueError("Google did not return an identity")
    return {"sub": data["sub"], "email": data["email"].lower(), "email_verified": bool(data.get("email_verified")),
            "name": data.get("name")}
