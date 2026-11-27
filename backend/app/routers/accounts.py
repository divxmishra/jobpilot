"""Public endpoints, authentication, account management, subscription & usage views,
support tickets and Career Roadmap."""
from __future__ import annotations

import shutil

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import JSONResponse, RedirectResponse
from pydantic import BaseModel, Field

from .. import audit, db
from ..config import settings
from ..context import current_user, uid
from ..files import data_path
from ..services import auth, emailer, entitlements, roadmap, tracking

router = APIRouter(tags=["accounts"])
COOKIE = "jp_session"


def _ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else None)


def _set_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(COOKIE, token, max_age=auth.SESSION_IDLE_DAYS * 86400, httponly=True, secure=settings.cookie_secure,
                    samesite="lax", path="/")


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/").replace("http://", "https://") if settings.is_production else str(request.base_url).rstrip("/")


# ================================================================ public
@router.get("/health", include_in_schema=False)
def health_min():
    """Lightweight liveness probe for uptime monitors: no DB query, no internal details."""
    return {"status": "ok"}


@router.get("/api/health")
def health():
    return {"status": "ok"}


@router.get("/api/public/config")
def public_config():
    cfg = entitlements.system()
    return {"registration_open": entitlements.registration_open(), "google_login": auth.google_configured() and entitlements.flag("google_login"),
            "email_enabled": emailer.configured(), "demo_enabled": entitlements.flag("demo"), "maintenance": cfg["maintenance_mode"],
            "maintenance_message": cfg["maintenance_message"] if cfg["maintenance_mode"] else None,
            "support_email": cfg.get("support_email") or None, "trial_days": cfg["trial_days"],
            "trial_plan": (entitlements.get_plan(code=cfg["trial_plan_code"]) or {}).get("name"),
            "payments_enabled": False, "owner_exists": auth.owner_exists()}


@router.get("/api/public/plans")
def public_plans():
    out = []
    for p in entitlements.list_plans(public_only=True):
        out.append({k: p[k] for k in ("code", "name", "description", "monthly_price", "annual_price", "annual_enabled", "currency",
                                      "discount_pct", "promo_label", "features", "trial_eligible", "is_free_default", "price_set",
                                      "effective_monthly_price")}
                   | {"limits": {k: p["entitlements"].get(k) for k in entitlements.LIMITS},
                      "feature_flags": {k: p["entitlements"].get(k) for k in entitlements.FEATURES}})
    return {"plans": out, "limit_labels": {k: v["label"] for k, v in entitlements.LIMITS.items()}, "feature_labels": entitlements.FEATURES,
            "payments_enabled": False}


@router.get("/api/public/announcements")
def public_announcements(request: Request):
    now = db.now_iso()
    audiences = ("all", "users") if current_user() else ("all", "public")
    return db.query("SELECT id, title, body, level FROM announcements WHERE active = 1 AND audience IN (?, ?) AND "
                    "(starts_at IS NULL OR starts_at <= ?) AND (ends_at IS NULL OR ends_at >= ?) ORDER BY id DESC LIMIT 3",
                    (*audiences, now, now))


class ContactIn(BaseModel):
    email: str = Field(max_length=200)
    subject: str = Field(min_length=3, max_length=150)
    message: str = Field(min_length=10, max_length=4000)


@router.post("/api/public/contact")
def public_contact(body: ContactIn, request: Request):
    if not entitlements.flag("support"):
        raise HTTPException(409, "Support requests are temporarily unavailable")
    if not auth.EMAIL_RE.match(body.email.strip()):
        raise HTTPException(422, "Enter a valid email address")
    if auth.too_many_attempts(_ip(request), None, limit_ip=5, window_min=60):
        raise HTTPException(429, "Too many messages. Please try again later.")
    auth.record_attempt(_ip(request), None, False)  # counts toward the contact-form rate limit
    now = db.now_iso()
    tid = db.execute("INSERT INTO support_tickets(email, subject, status, created_at, updated_at) VALUES (?,?,?,?,?)",
                     (body.email.strip().lower(), body.subject.strip(), "open", now, now))
    db.execute("INSERT INTO support_messages(ticket_id, body, created_at) VALUES (?,?,?)", (tid, body.message.strip(), now))
    return {"ok": True, "message": "Thanks — your message reached the JobPilot team."}


# ================================================================ session & auth
@router.get("/api/session")
def session(request: Request):
    u = current_user()
    sess = getattr(request.state, "session", None)
    out = {"authenticated": bool(u), "user": auth.public_user(u), "config": public_config()}
    if u:
        out["is_staff"] = u["role"] in auth.STAFF_ROLES
        out["elevated"] = auth.is_elevated(sess)
        out["usage"] = entitlements.usage_summary()
    return out


class RegisterIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)
    name: str | None = Field(default=None, max_length=120)
    accept_terms: bool = False


@router.post("/api/auth/register")
def register(body: RegisterIn, request: Request, response: Response):
    if not entitlements.registration_open():
        raise HTTPException(403, "New registrations are currently closed.")
    if not body.accept_terms:
        raise HTTPException(422, "Please accept the terms and privacy policy to create an account")
    ip = _ip(request)
    if auth.too_many_attempts(ip, None, limit_ip=10, window_min=60):
        raise HTTPException(429, "Too many attempts. Please try again later.")
    auth.record_attempt(ip, None, False)
    user = auth.create_user(body.email, body.password, body.name)
    token = auth.create_session(user["id"], request.headers.get("user-agent"), ip)
    _set_cookie(response, token)
    from ..context import as_user
    with as_user(user):
        tracking.event("registration", feature="auth", meta={"method": "email"})
        _send_verification(user, request)
    return {"ok": True, "user": auth.public_user(auth.get_user(user["id"]))}


def _send_verification(user: dict, request: Request) -> bool:
    if not emailer.configured() or user.get("email_verified"):
        return False
    tok = auth.issue_token(user["id"], "verify_email", 60 * 48)
    return emailer.send(user["email"], "Verify your JobPilot email",
                        f"Hi{(' ' + user['name']) if user.get('name') else ''},\n\nConfirm your email address:\n"
                        f"{_base_url(request)}/verify-email?token={tok}\n\nThis link expires in 48 hours.")


class LoginIn(BaseModel):
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)


@router.post("/api/auth/login")
def login(body: LoginIn, request: Request, response: Response):
    ip, email = _ip(request), body.email.strip().lower()
    if auth.too_many_attempts(ip, email):
        raise HTTPException(429, "Too many failed attempts. Please wait 15 minutes or reset your password.")
    user = auth.get_user_by_email(email)
    if not user or not auth.verify_password(body.password, user.get("password_hash")) or user["status"] == "deleted":
        auth.record_attempt(ip, email, False)
        raise HTTPException(401, "Incorrect email or password")
    if user["status"] == "suspended":
        auth.record_attempt(ip, email, False)
        raise HTTPException(403, "This account is suspended. Contact support if you think this is a mistake.")
    auth.record_attempt(ip, email, True)
    token = auth.create_session(user["id"], request.headers.get("user-agent"), ip)
    _set_cookie(response, token)
    from ..context import as_user
    with as_user(user):
        tracking.event("login", feature="auth", meta={"method": "password"})
    return {"ok": True, "user": auth.public_user(user), "is_staff": user["role"] in auth.STAFF_ROLES}


@router.post("/api/auth/logout")
def logout(request: Request, response: Response):
    if current_user():
        tracking.event("logout", feature="auth")
    auth.revoke_session(request.cookies.get(COOKIE))
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


class TokenIn(BaseModel):
    token: str = Field(max_length=200)


@router.post("/api/auth/verify-email")
def verify_email(body: TokenIn):
    user_id = auth.consume_token(body.token, "verify_email")
    if not user_id:
        raise HTTPException(400, "This verification link is invalid or has expired.")
    db.execute("UPDATE users SET email_verified = 1, updated_at = ? WHERE id = ?", (db.now_iso(), user_id))
    return {"ok": True}


@router.post("/api/auth/resend-verification")
def resend_verification(request: Request):
    u = current_user()
    if not u:
        raise HTTPException(401, "Sign in required")
    if not emailer.configured():
        raise HTTPException(409, "Email delivery isn't configured on this server yet, so verification emails can't be sent.")
    _send_verification(u, request)
    return {"ok": True}


class ForgotIn(BaseModel):
    email: str = Field(max_length=200)


@router.post("/api/auth/forgot-password")
def forgot(body: ForgotIn, request: Request):
    ip = _ip(request)
    if auth.too_many_attempts(ip, None, limit_ip=10, window_min=60):
        raise HTTPException(429, "Too many requests. Please try again later.")
    auth.record_attempt(ip, None, False)
    if not emailer.configured():
        return {"ok": False, "email_enabled": False,
                "message": "Password reset by email isn't available yet. Contact support and an administrator can send you a secure reset link."}
    user = auth.get_user_by_email(body.email)
    if user and user["status"] == "active":
        tok = auth.issue_token(user["id"], "reset_password", 60)
        emailer.send(user["email"], "Reset your JobPilot password",
                     f"Use this link within 60 minutes to set a new password:\n{_base_url(request)}/reset-password?token={tok}\n\n"
                     "If you didn't ask for this, ignore this email.")
    return {"ok": True, "email_enabled": True, "message": "If an account exists for that email, a reset link has been sent."}


class ResetIn(BaseModel):
    token: str = Field(max_length=200)
    password: str = Field(max_length=200)


@router.post("/api/auth/reset-password")
def reset_password(body: ResetIn):
    probs = auth.password_problems(body.password)
    if probs:
        raise HTTPException(422, "; ".join(probs))
    user_id = auth.consume_token(body.token, "reset_password")
    if not user_id:
        raise HTTPException(400, "This reset link is invalid or has expired.")
    db.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?", (auth.hash_password(body.password), db.now_iso(), user_id))
    auth.revoke_user_sessions(user_id)
    tracking.event("password_reset", feature="auth", user_id=user_id)
    return {"ok": True}


# ---------------------------------------------------------------- Google OAuth
@router.get("/api/auth/google/start")
def google_start(request: Request):
    if not (auth.google_configured() and entitlements.flag("google_login")):
        raise HTTPException(404, "Google sign-in is not configured")
    import secrets
    state = secrets.token_urlsafe(24)
    resp = RedirectResponse(auth.google_auth_url(state, auth.google_redirect_uri(_base_url(request))))
    resp.set_cookie("jp_oauth_state", state, max_age=600, httponly=True, secure=settings.cookie_secure, samesite="lax", path="/api/auth/google")
    return resp


@router.get("/api/auth/google/callback")
def google_callback(request: Request, code: str = "", state: str = ""):
    if not auth.google_configured():
        raise HTTPException(404, "Google sign-in is not configured")
    import hmac as _hmac
    expected = request.cookies.get("jp_oauth_state") or ""
    if not code or not state or not _hmac.compare_digest(state, expected):
        return RedirectResponse("/login?error=oauth_state")
    try:
        ident = auth.google_exchange(code, auth.google_redirect_uri(_base_url(request)))
    except Exception:
        return RedirectResponse("/login?error=oauth_failed")
    if not ident["email_verified"]:
        return RedirectResponse("/login?error=email_unverified")
    user = db.query_one("SELECT * FROM users WHERE google_sub = ?", (ident["sub"],)) or auth.get_user_by_email(ident["email"])
    if user:
        if user["status"] != "active":
            return RedirectResponse("/login?error=suspended")
        if not user.get("google_sub"):
            db.execute("UPDATE users SET google_sub = ?, email_verified = 1 WHERE id = ?", (ident["sub"], user["id"]))
    else:
        if not entitlements.registration_open():
            return RedirectResponse("/login?error=registration_closed")
        user = auth.create_user(ident["email"], None, ident["name"], google_sub=ident["sub"], email_verified=True)
        from ..context import as_user
        with as_user(user):
            tracking.event("registration", feature="auth", meta={"method": "google"})
    token = auth.create_session(user["id"], request.headers.get("user-agent"), _ip(request))
    resp = RedirectResponse("/app")
    _set_cookie(resp, token)
    resp.delete_cookie("jp_oauth_state", path="/api/auth/google")
    return resp


# ---------------------------------------------------------------- owner bootstrap (one time)
class BootstrapIn(BaseModel):
    setup_secret: str = Field(max_length=300)
    email: str = Field(max_length=200)
    password: str = Field(max_length=200)
    name: str | None = Field(default=None, max_length=120)


@router.post("/api/auth/bootstrap-owner")
def bootstrap_owner(body: BootstrapIn, request: Request, response: Response):
    if auth.owner_exists():
        raise HTTPException(409, "The owner account already exists.")
    ip = _ip(request)
    if auth.too_many_attempts(ip, None, limit_ip=5, window_min=60):
        raise HTTPException(429, "Too many attempts. Please try again later.")
    if not auth.check_bootstrap(body.setup_secret, ip):
        auth.record_attempt(ip, None, False)
        raise HTTPException(401, "The setup secret is incorrect.")
    existing = auth.get_user_by_email(body.email)
    if existing:
        if not auth.verify_password(body.password, existing.get("password_hash")):
            raise HTTPException(409, "An account with this email exists; enter its password to promote it to owner.")
        db.execute("UPDATE users SET role = 'owner', updated_at = ? WHERE id = ?", (db.now_iso(), existing["id"]))
        owner = auth.get_user(existing["id"])
    else:
        owner = auth.create_user(body.email, body.password, body.name, role="owner")
    claimed = auth.claim_legacy_data(owner["id"])
    token = auth.create_session(owner["id"], request.headers.get("user-agent"), ip)
    _set_cookie(response, token)
    from ..context import as_user
    with as_user(owner):
        tracking.admin_action("owner_bootstrap", target_user_id=owner["id"], details={"claimed_rows": claimed}, ip_hash=auth.ip_hash(ip))
    return {"ok": True, "user": auth.public_user(owner), "claimed": claimed, "next": "Set up two-factor authentication to open the admin panel."}


# ---------------------------------------------------------------- admin second factor (TOTP)
def _require_staff() -> dict:
    u = current_user()
    if not u or u["role"] not in auth.STAFF_ROLES:
        raise HTTPException(403, "Not permitted")
    return u


@router.post("/api/auth/2fa/setup")
def twofa_setup():
    u = _require_staff()
    if u["totp_enabled"]:
        raise HTTPException(409, "Two-factor authentication is already enabled")
    secret = auth.new_totp_secret()
    db.execute("UPDATE users SET totp_secret = ?, updated_at = ? WHERE id = ?", (secret, db.now_iso(), u["id"]))
    return {"secret": secret, "otpauth_uri": auth.totp_uri(secret, u["email"]),
            "instructions": "Add this key to an authenticator app (Google Authenticator, Microsoft Authenticator, 1Password…), then enter the 6-digit code."}


class CodeIn(BaseModel):
    code: str = Field(max_length=10)


@router.post("/api/auth/2fa/enable")
def twofa_enable(body: CodeIn, request: Request):
    u = _require_staff()
    fresh = auth.get_user(u["id"])
    if not auth.verify_totp(fresh["totp_secret"], body.code):
        raise HTTPException(401, "That code is not valid. Check the time on your phone and try again.")
    db.execute("UPDATE users SET totp_enabled = 1, updated_at = ? WHERE id = ?", (db.now_iso(), u["id"]))
    auth.elevate(request.state.session["id"])
    tracking.admin_action("2fa_enabled", target_user_id=u["id"], ip_hash=auth.ip_hash(_ip(request)))
    return {"ok": True}


@router.post("/api/auth/2fa/verify")
def twofa_verify(body: CodeIn, request: Request):
    u = _require_staff()
    ip = _ip(request)
    if auth.too_many_attempts(ip, u["email"] + "#2fa", limit_email=6):
        raise HTTPException(429, "Too many attempts. Wait 15 minutes.")
    fresh = auth.get_user(u["id"])
    if not fresh["totp_enabled"] or not auth.verify_totp(fresh["totp_secret"], body.code):
        auth.record_attempt(ip, u["email"] + "#2fa", False)
        raise HTTPException(401, "Invalid code")
    auth.record_attempt(ip, u["email"] + "#2fa", True)
    auth.elevate(request.state.session["id"])
    tracking.admin_action("admin_session_verified", target_user_id=u["id"], ip_hash=auth.ip_hash(ip))
    return {"ok": True}


# ================================================================ account
class AccountPatch(BaseModel):
    name: str | None = Field(default=None, max_length=120)
    analytics_opt_out: bool | None = None


@router.get("/api/account")
def account():
    u = auth.get_user(uid())
    return {"user": auth.public_user(u), "usage": entitlements.usage_summary(), "email_enabled": emailer.configured()}


@router.patch("/api/account")
def patch_account(body: AccountPatch):
    fields = {}
    if body.name is not None:
        fields["name"] = body.name.strip() or None
    if body.analytics_opt_out is not None:
        fields["analytics_opt_out"] = 1 if body.analytics_opt_out else 0
    if fields:
        fields["updated_at"] = db.now_iso()
        db.execute(f"UPDATE users SET {', '.join(k + ' = ?' for k in fields)} WHERE id = ?", (*fields.values(), uid()))
    return {"user": auth.public_user(auth.get_user(uid()))}


class PasswordChange(BaseModel):
    current_password: str | None = Field(default=None, max_length=200)
    new_password: str = Field(max_length=200)


@router.post("/api/account/password")
def change_password(body: PasswordChange, request: Request):
    u = auth.get_user(uid())
    if u["password_hash"] and not auth.verify_password(body.current_password or "", u["password_hash"]):
        raise HTTPException(401, "Your current password is incorrect")
    probs = auth.password_problems(body.new_password, u["email"])
    if probs:
        raise HTTPException(422, "; ".join(probs))
    db.execute("UPDATE users SET password_hash = ?, updated_at = ? WHERE id = ?", (auth.hash_password(body.new_password), db.now_iso(), u["id"]))
    auth.revoke_user_sessions(u["id"], except_sid=request.state.session["id"])
    return {"ok": True}


USER_TABLES = ["outreach_drafts", "contacts", "applications", "resumes", "jobs", "profile_versions", "uploads", "companies",
               "notifications", "tasks", "discovery_runs", "ai_usage", "audit_events", "user_settings", "skill_progress",
               "roadmap_snapshots", "usage_counters", "user_overrides", "trials", "sessions", "auth_tokens"]


def delete_user_data(user_id: int, keep_account_row: bool = False) -> None:
    """Remove a user's personal data. Payment records are retained (user reference nulled) for accounting."""
    with db.tx() as conn:
        conn.execute("DELETE FROM application_history WHERE application_id IN (SELECT id FROM applications WHERE user_id = ?)", (user_id,))
        for t in USER_TABLES:
            conn.execute(f"DELETE FROM {t} WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM activity_events WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE payments SET user_id = NULL WHERE user_id = ?", (user_id,))
        conn.execute("UPDATE support_tickets SET user_id = NULL, email = NULL WHERE user_id = ?", (user_id,))
        conn.execute("DELETE FROM subscriptions WHERE user_id = ?", (user_id,))
        if keep_account_row:
            conn.execute("UPDATE users SET status = 'deleted', email = ?, name = NULL, password_hash = NULL, google_sub = NULL, "
                         "totp_secret = NULL, deleted_at = ? WHERE id = ?", (f"deleted-{user_id}@deleted.invalid", db.now_iso(), user_id))
        else:
            conn.execute("DELETE FROM users WHERE id = ?", (user_id,))
    from ..services.backups import _force_rmtree
    _force_rmtree(data_path("users", str(user_id)))


class DeleteAccountIn(BaseModel):
    confirm: str
    password: str | None = Field(default=None, max_length=200)


@router.post("/api/account/delete")
def delete_account(body: DeleteAccountIn, response: Response):
    u = auth.get_user(uid())
    if body.confirm != "DELETE MY ACCOUNT":
        raise HTTPException(422, "Type DELETE MY ACCOUNT exactly to confirm")
    if u["password_hash"] and not auth.verify_password(body.password or "", u["password_hash"]):
        raise HTTPException(401, "Password is incorrect")
    if u["role"] == "owner":
        raise HTTPException(409, "The owner account can't be deleted from here. Transfer ownership first.")
    tracking.event("account_deleted", feature="privacy", user_id=None)
    delete_user_data(u["id"])
    response.delete_cookie(COOKIE, path="/")
    return {"ok": True}


# ---------------------------------------------------------------- subscription & usage
@router.get("/api/subscription")
def subscription():
    u = auth.get_user(uid())
    eff = entitlements.effective(u)
    cfg = entitlements.system()
    sub = eff["subscription"]
    return {"current": {"plan": eff["plan"], "source": eff["source"], "subscription": sub}, "trial": eff["trial"],
            "trial_config": {"days": cfg["trial_days"], "plan": (entitlements.get_plan(code=cfg["trial_plan_code"]) or {}).get("name"),
                             "starts": "when you complete profile setup"},
            "plans": public_plans()["plans"], "usage": entitlements.usage_summary(u), "payments_enabled": False,
            "after_trial": "When the trial ends you move to the Free plan automatically. Nothing is charged and your data is kept.",
            "billing_history": db.query("SELECT id, kind, status, amount, currency, method, is_manual, created_at FROM payments "
                                        "WHERE user_id = ? ORDER BY id DESC", (u["id"],))}


class CheckoutIn(BaseModel):
    plan_code: str
    period: str = "monthly"
    coupon: str | None = None


@router.post("/api/subscription/quote")
def quote(body: CheckoutIn):
    p = entitlements.get_plan(code=body.plan_code)
    if not p or not p["available"]:
        raise HTTPException(404, "Plan not found")
    if not p["price_set"] and not p["is_free_default"]:
        raise HTTPException(409, "Pricing for this plan hasn't been announced yet.")
    price = (p["annual_price"] if body.period == "annual" and p["annual_enabled"] else p["monthly_price"]) or 0
    price = price * (1 - (p["discount_pct"] or 0) / 100)
    coupon = None
    if body.coupon:
        c = entitlements.validate_coupon(body.coupon, p["code"])
        price = price * (1 - c["discount_pct"] / 100)
        coupon = {"code": c["code"], "discount_pct": c["discount_pct"]}
    return {"plan": p["name"], "period": body.period, "amount": round(price, 2), "currency": p["currency"], "coupon": coupon,
            "payments_enabled": False,
            "message": "Online payments aren't enabled yet. No charge has been made. Contact support if you'd like to upgrade."}


# ---------------------------------------------------------------- support
class TicketIn(BaseModel):
    subject: str = Field(min_length=3, max_length=150)
    message: str = Field(min_length=5, max_length=4000)


class MessageIn(BaseModel):
    message: str = Field(min_length=1, max_length=4000)


@router.get("/api/support/tickets")
def my_tickets():
    return db.query("SELECT * FROM support_tickets WHERE user_id = ? ORDER BY updated_at DESC", (uid(),))


@router.post("/api/support/tickets")
def new_ticket(body: TicketIn):
    if not entitlements.flag("support"):
        raise HTTPException(409, "Support is temporarily unavailable")
    now = db.now_iso()
    u = current_user()
    tid = db.execute("INSERT INTO support_tickets(user_id, email, subject, status, created_at, updated_at) VALUES (?,?,?,?,?,?)",
                     (u["id"], u["email"], body.subject.strip(), "open", now, now))
    db.execute("INSERT INTO support_messages(ticket_id, author_id, body, created_at) VALUES (?,?,?,?)", (tid, u["id"], body.message.strip(), now))
    tracking.event("support_ticket", feature="support")
    return ticket(tid)


@router.get("/api/support/tickets/{tid}")
def ticket(tid: int):
    t = db.query_one("SELECT * FROM support_tickets WHERE id = ? AND user_id = ?", (tid, uid()))
    if not t:
        raise HTTPException(404, "Ticket not found")
    t["messages"] = db.query("SELECT id, from_staff, body, created_at FROM support_messages WHERE ticket_id = ? ORDER BY id", (tid,))
    return t


@router.post("/api/support/tickets/{tid}/messages")
def reply(tid: int, body: MessageIn):
    t = ticket(tid)
    if t["status"] == "closed":
        raise HTTPException(409, "This ticket is closed; open a new one")
    now = db.now_iso()
    db.execute("INSERT INTO support_messages(ticket_id, author_id, body, created_at) VALUES (?,?,?,?)", (tid, uid(), body.message.strip(), now))
    db.execute("UPDATE support_tickets SET status = 'open', updated_at = ? WHERE id = ?", (now, tid))
    return ticket(tid)


# ---------------------------------------------------------------- Career Roadmap
@router.get("/api/roadmap")
def get_roadmap(role: str = ""):
    entitlements.require_feature("career_roadmap")
    unlocks = bool(entitlements.effective()["entitlements"].get("roadmap_unlock_estimates"))
    data = roadmap.build(role or None, include_unlocks=unlocks)
    if not unlocks:
        data["unlock"]["locked_reason"] = "Job-unlock estimates aren't included in your plan."
    tracking.event("roadmap_viewed", feature="career_roadmap")
    return data


class SkillStatus(BaseModel):
    skill: str = Field(min_length=1, max_length=60)
    status: str


@router.put("/api/roadmap/skills")
def set_skill(body: SkillStatus):
    entitlements.require_feature("career_roadmap")
    r = roadmap.set_progress(body.skill, body.status)
    tracking.event("skill_progress_updated", feature="career_roadmap", meta={"status": body.status})
    return r


@router.get("/api/roadmap/history")
def roadmap_history():
    entitlements.require_feature("career_roadmap")
    return roadmap.history()


def json_error(status: int, info: dict) -> JSONResponse:
    return JSONResponse({"detail": info.get("message"), "limit": info}, status)


__all__ = ["router", "COOKIE", "delete_user_data", "json_error", "audit"]
