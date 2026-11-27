"""Private admin API. Every route requires a staff role AND a session verified with the
second factor (TOTP). Roles: owner (everything), admin (everything except roles/security
ownership), support (read-only + support tickets). All mutations are audit-logged."""
from __future__ import annotations

import csv
import io
import os
import platform
import time
from datetime import date, datetime, timedelta, timezone

from fastapi import APIRouter, Depends, HTTPException, Query, Request
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import db
from ..config import settings
from ..context import current_user
from ..services import auth, backups, emailer, entitlements as ent, tracking

router = APIRouter(prefix="/api/admin", tags=["admin"])
STARTED = time.time()


def staff(write: bool = False, owner_only: bool = False):
    def dep(request: Request):
        u = current_user()
        if not u or u["role"] not in auth.STAFF_ROLES:
            raise HTTPException(403, "Not permitted")
        if not u.get("totp_enabled"):
            raise HTTPException(428, "Set up two-factor authentication to use the admin panel")
        if not auth.is_elevated(getattr(request.state, "session", None)):
            raise HTTPException(428, "Verify your two-factor code to continue")
        if owner_only and u["role"] != "owner":
            raise HTTPException(403, "Only the owner can do this")
        if write and u["role"] == "support":
            raise HTTPException(403, "Support staff have read-only access")
        return u
    return dep


READ = Depends(staff())
WRITE = Depends(staff(write=True))
OWNER = Depends(staff(write=True, owner_only=True))


def _ip(request: Request) -> str | None:
    fwd = request.headers.get("x-forwarded-for")
    return fwd.split(",")[0].strip() if fwd else (request.client.host if request.client else None)


def _log(request: Request, action: str, target: int | None = None, details: dict | None = None):
    tracking.admin_action(action, target_user_id=target, details=details, ip_hash=auth.ip_hash(_ip(request)))


# ------------------------------------------------------------------ date ranges
def _range(frm: str | None, to: str | None) -> tuple[str, str, str, str]:
    today = datetime.now(timezone.utc).date()
    d_to = date.fromisoformat(to) if to else today
    d_from = date.fromisoformat(frm) if frm else d_to - timedelta(days=29)
    if d_from > d_to:
        raise HTTPException(422, "Start date must be before end date")
    span = (d_to - d_from).days + 1
    p_to = d_from - timedelta(days=1)
    p_from = p_to - timedelta(days=span - 1)
    return d_from.isoformat(), (d_to + timedelta(days=1)).isoformat(), p_from.isoformat(), (p_to + timedelta(days=1)).isoformat()


def _bucket(granularity: str) -> str:
    return {"day": "substr({c},1,10)", "week": "strftime('%Y-W%W', {c})", "month": "substr({c},1,7)", "year": "substr({c},1,4)"}.get(
        granularity, "substr({c},1,10)")


def _metrics(a: str, b: str) -> dict:
    q1 = lambda sql, p=(): db.query_one(sql, p)["n"]  # noqa: E731
    ev = lambda name: q1("SELECT COUNT(*) n FROM activity_events WHERE event = ? AND at >= ? AND at < ?", (name, a, b))  # noqa: E731
    paid_src = "('payment','manual_payment')"
    revenue = db.query_one("SELECT COALESCE(SUM(amount),0) n FROM payments WHERE kind='charge' AND status='captured' AND verified=1 "
                           "AND created_at >= ? AND created_at < ?", (a, b))["n"]
    refunds = db.query_one("SELECT COALESCE(SUM(amount),0) n FROM payments WHERE kind='refund' AND verified=1 AND created_at >= ? AND created_at < ?",
                           (a, b))["n"]
    trials_ended = q1("SELECT COUNT(*) n FROM trials WHERE ends_at >= ? AND ends_at < ? AND status IN ('expired','active')", (a, b))
    converted = q1(f"SELECT COUNT(DISTINCT t.user_id) n FROM trials t JOIN subscriptions s ON s.user_id = t.user_id AND s.source IN {paid_src} "
                   "WHERE t.ends_at >= ? AND t.ends_at < ?", (a, b))
    return {
        "new_users": q1("SELECT COUNT(*) n FROM users WHERE created_at >= ? AND created_at < ? AND status != 'deleted'", (a, b)),
        "active_users": q1("SELECT COUNT(DISTINCT user_id) n FROM sessions WHERE last_seen_at >= ? AND last_seen_at < ?", (a, b)),
        "trial_expirations": trials_ended,
        "upgrades": q1(f"SELECT COUNT(*) n FROM subscriptions WHERE source IN {paid_src} AND started_at >= ? AND started_at < ?", (a, b)),
        "cancellations": q1("SELECT COUNT(*) n FROM subscriptions WHERE cancelled_at >= ? AND cancelled_at < ? AND status IN ('cancelled','revoked')", (a, b)),
        "trial_conversion_rate": round(100 * converted / trials_ended, 1) if trials_ended else None,
        "revenue_collected": round(revenue, 2), "refunds": round(refunds, 2), "net_revenue": round(revenue - refunds, 2),
        "failed_payments": q1("SELECT COUNT(*) n FROM payments WHERE status='failed' AND created_at >= ? AND created_at < ?", (a, b)),
        "job_searches": ev("job_search"), "jobs_viewed": ev("job_viewed"), "resume_analyses": ev("resume_analyzed"),
        "resume_generations": ev("resume_tailored"), "applications_prepared": ev("application_prepared"),
        "interviews_reported": ev("interview_reported"), "offers_reported": ev("offer_reported"),
        "system_errors": q1("SELECT COUNT(*) n FROM audit_events WHERE level='error' AND at >= ? AND at < ?", (a, b)),
        "api_calls": q1("SELECT COUNT(*) n FROM ai_usage WHERE cached = 0 AND at >= ? AND at < ?", (a, b)),
        "ai_cost_inr": round(db.query_one("SELECT COALESCE(SUM(cost_inr),0) n FROM ai_usage WHERE at >= ? AND at < ?", (a, b))["n"], 4),
    }


@router.get("/overview", dependencies=[READ])
def overview(frm: str | None = Query(None, alias="from"), to: str | None = None, granularity: str = "day"):
    a, b, pa, pb = _range(frm, to)
    now = datetime.now(timezone.utc).isoformat()
    q1 = lambda sql, p=(): db.query_one(sql, p)["n"]  # noqa: E731
    snapshot = {
        "total_users": q1("SELECT COUNT(*) n FROM users WHERE status != 'deleted'"),
        "suspended_users": q1("SELECT COUNT(*) n FROM users WHERE status = 'suspended'"),
        "users_in_trial": q1("SELECT COUNT(*) n FROM trials WHERE status = 'active' AND ends_at > ?", (now,)),
        "paid_subscribers": q1("SELECT COUNT(DISTINCT user_id) n FROM subscriptions WHERE source IN ('payment','manual_payment') "
                               "AND status IN ('active','lifetime') AND (ends_at IS NULL OR ends_at > ?)", (now,)),
        "granted_access": q1("SELECT COUNT(DISTINCT user_id) n FROM subscriptions WHERE source IN ('admin_grant','free_grant','lifetime') "
                             "AND status IN ('active','lifetime') AND (ends_at IS NULL OR ends_at > ?)", (now,)),
    }
    snapshot["free_users"] = max(0, snapshot["total_users"] - snapshot["users_in_trial"] - snapshot["paid_subscribers"] - snapshot["granted_access"])
    mrr = db.query_one("SELECT COALESCE(SUM(CASE WHEN billing_period='annual' THEN locked_price/12.0 ELSE locked_price END),0) n FROM subscriptions "
                       "WHERE source IN ('payment','manual_payment') AND status = 'active' AND (ends_at IS NULL OR ends_at > ?)", (now,))["n"]
    cur, prev = _metrics(a, b), _metrics(pa, pb)
    bk = _bucket(granularity)
    series = {
        "registrations": db.query(f"SELECT {bk.format(c='created_at')} k, COUNT(*) n FROM users WHERE created_at >= ? AND created_at < ? GROUP BY k ORDER BY k", (a, b)),
        "job_searches": db.query(f"SELECT {bk.format(c='at')} k, COUNT(*) n FROM activity_events WHERE event='job_search' AND at >= ? AND at < ? GROUP BY k ORDER BY k", (a, b)),
        "active_users": db.query(f"SELECT {bk.format(c='at')} k, COUNT(DISTINCT user_id) n FROM activity_events WHERE at >= ? AND at < ? GROUP BY k ORDER BY k", (a, b)),
        "revenue": db.query(f"SELECT {bk.format(c='created_at')} k, ROUND(SUM(amount),2) n FROM payments WHERE kind='charge' AND status='captured' AND verified=1 "
                            "AND created_at >= ? AND created_at < ? GROUP BY k ORDER BY k", (a, b)),
    }
    features = db.query("SELECT COALESCE(feature,'other') feature, COUNT(*) n FROM activity_events WHERE at >= ? AND at < ? GROUP BY feature ORDER BY n DESC", (a, b))
    return {"range": {"from": a, "to": b, "previous_from": pa, "previous_to": pb}, "snapshot": snapshot, "current": cur, "previous": prev,
            "mrr_projected": round(mrr, 2), "series": series, "feature_usage": features, "health": _health_brief()}


def _health_brief() -> dict:
    return {"database": "ok", "uptime_seconds": int(time.time() - STARTED),
            "errors_24h": db.query_one("SELECT COUNT(*) n FROM audit_events WHERE level='error' AND at >= datetime('now','-1 day')")["n"],
            "last_backup": (backups.status().get("last_success") or {}).get("started_at")}


# ------------------------------------------------------------------ users
USER_SORT = {"created": "u.created_at DESC", "last_login": "COALESCE(u.last_login_at,'') DESC", "email": "u.email", "name": "COALESCE(u.name,'')"}


def _user_rows(where: str, params: list, sort: str, limit: int, offset: int) -> list[dict]:
    now = datetime.now(timezone.utc).isoformat()
    rows = db.query(
        "SELECT u.id, u.email, u.name, u.role, u.status, u.created_at, u.last_login_at, u.email_verified, u.profile_completed_at, "
        "t.status AS trial_status, t.started_at AS trial_started_at, t.ends_at AS trial_ends_at, "
        "(SELECT p.name FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.user_id = u.id AND s.status IN ('active','lifetime') "
        " AND (s.ends_at IS NULL OR s.ends_at > ?) ORDER BY p.sort_order DESC LIMIT 1) AS sub_plan, "
        "(SELECT s.ends_at FROM subscriptions s WHERE s.user_id = u.id AND s.status IN ('active','lifetime') ORDER BY s.id DESC LIMIT 1) AS sub_ends_at, "
        "(SELECT COUNT(*) FROM activity_events e WHERE e.user_id = u.id AND e.event = 'job_search') AS job_searches, "
        "(SELECT COUNT(*) FROM activity_events e WHERE e.user_id = u.id AND e.event = 'job_viewed') AS jobs_viewed, "
        "(SELECT COUNT(*) FROM activity_events e WHERE e.user_id = u.id AND e.event = 'resume_analyzed') AS resume_analyses, "
        "(SELECT COUNT(*) FROM activity_events e WHERE e.user_id = u.id AND e.event = 'resume_tailored') AS resume_generations, "
        "(SELECT COUNT(*) FROM applications a WHERE a.user_id = u.id) AS applications, "
        "(SELECT COUNT(*) FROM applications a WHERE a.user_id = u.id AND a.status IN ('interview','offer')) AS interviews, "
        "(SELECT COUNT(*) FROM applications a WHERE a.user_id = u.id AND a.status = 'offer') AS offers "
        f"FROM users u LEFT JOIN trials t ON t.user_id = u.id WHERE {where} ORDER BY {USER_SORT.get(sort, USER_SORT['created'])} LIMIT ? OFFSET ?",
        [now] + params + [limit, offset])
    for r in rows:
        if r["role"] in ("owner", "admin"):
            r["plan"] = "Staff"
        elif r["sub_plan"]:
            r["plan"] = r["sub_plan"]
        elif r["trial_status"] == "active" and r["trial_ends_at"] and r["trial_ends_at"] > now:
            r["plan"] = "Trial"
        else:
            r["plan"] = ent.free_plan()["name"]
    return rows


@router.get("/users", dependencies=[READ])
def users(q: str = "", status: str = "", role: str = "", plan: str = "", sort: str = "created", page: int = 1, page_size: int = 25):
    where, params = ["u.status != 'deleted'"], []
    if q:
        where.append("(u.email LIKE ? OR u.name LIKE ? OR CAST(u.id AS TEXT) = ?)")
        params += [f"%{q}%", f"%{q}%", q]
    if status:
        where.append("u.status = ?")
        params.append(status)
    if role:
        where.append("u.role = ?")
        params.append(role)
    w = " AND ".join(where)
    page_size = max(1, min(100, page_size))
    rows = _user_rows(w, params, sort, 1000 if plan else page_size, 0 if plan else (max(1, page) - 1) * page_size)
    if plan:
        rows = [r for r in rows if r["plan"].lower() == plan.lower()]
        total = len(rows)
        rows = rows[(max(1, page) - 1) * page_size:][:page_size]
    else:
        total = db.query_one(f"SELECT COUNT(*) n FROM users u WHERE {w}", params)["n"]
    return {"items": rows, "total": total, "page": page, "page_size": page_size, "plans": [p["name"] for p in ent.list_plans()]}


@router.get("/users/export.csv", dependencies=[READ])
def users_csv():
    rows = _user_rows("u.status != 'deleted'", [], "created", 100000, 0)
    buf = io.StringIO()
    cols = ["id", "email", "name", "role", "status", "plan", "created_at", "last_login_at", "trial_status", "trial_ends_at", "job_searches",
            "jobs_viewed", "resume_analyses", "resume_generations", "applications", "interviews", "offers"]
    w = csv.DictWriter(buf, fieldnames=cols, extrasaction="ignore")
    w.writeheader()
    w.writerows(rows)
    return StreamingResponse(io.BytesIO(buf.getvalue().encode()), media_type="text/csv",
                             headers={"Content-Disposition": 'attachment; filename="jobpilot-users.csv"'})


@router.get("/users/{user_id}", dependencies=[READ])
def user_detail(user_id: int):
    u = auth.get_user(user_id)
    if not u or u["status"] == "deleted":
        raise HTTPException(404, "User not found")
    row = _user_rows("u.id = ?", [user_id], "created", 1, 0)[0]
    return {"user": auth.public_user(u) | {"plan": row["plan"]}, "stats": row, "usage": ent.usage_summary(u),
            "trial": ent.trial_info(user_id),
            "subscriptions": db.query("SELECT s.*, p.name AS plan_name FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.user_id = ? ORDER BY s.id DESC", (user_id,)),
            "overrides": db.query("SELECT key, value, reason, expires_at, created_at FROM user_overrides WHERE user_id = ?", (user_id,)),
            "timeline": db.query("SELECT event, feature, actor, success, error_category, metadata, at FROM activity_events WHERE user_id = ? ORDER BY id DESC LIMIT 200", (user_id,)),
            "payments": db.query("SELECT * FROM payments WHERE user_id = ? ORDER BY id DESC", (user_id,)),
            "sessions": db.query("SELECT substr(id,1,10) AS id, created_at, last_seen_at, expires_at, revoked FROM sessions WHERE user_id = ? ORDER BY last_seen_at DESC LIMIT 10", (user_id,))}


class UserAction(BaseModel):
    action: str
    days: int | None = None
    plan_code: str | None = None
    role: str | None = None
    note: str | None = Field(default=None, max_length=300)
    confirm: str | None = None


@router.post("/users/{user_id}/action")
def user_action(user_id: int, body: UserAction, request: Request, actor=WRITE):
    u = auth.get_user(user_id)
    if not u or u["status"] == "deleted":
        raise HTTPException(404, "User not found")
    if u["role"] == "owner" and body.action not in ("extend_trial",) and actor["id"] != u["id"]:
        raise HTTPException(403, "The owner account can't be modified by other staff")
    a, result = body.action, {}
    if a == "suspend":
        if u["id"] == actor["id"]:
            raise HTTPException(409, "You can't suspend yourself")
        db.execute("UPDATE users SET status = 'suspended', updated_at = ? WHERE id = ?", (db.now_iso(), user_id))
        auth.revoke_user_sessions(user_id)
    elif a == "reactivate":
        db.execute("UPDATE users SET status = 'active', updated_at = ? WHERE id = ?", (db.now_iso(), user_id))
    elif a == "delete":
        if body.confirm != f"DELETE {u['email']}":
            raise HTTPException(422, f"Type DELETE {u['email']} to confirm")
        if u["role"] in auth.STAFF_ROLES:
            raise HTTPException(409, "Remove the staff role before deleting this account")
        from .accounts import delete_user_data
        delete_user_data(user_id)
    elif a == "extend_trial":
        result = ent.extend_trial(user_id, int(body.days or 0))
    elif a == "revoke_trial":
        ent.revoke_trial(user_id)
    elif a in ("change_plan", "grant_free", "grant_lifetime"):
        plan = ent.get_plan(code=body.plan_code) if body.plan_code else None
        if not plan:
            raise HTTPException(422, "Choose a plan")
        source = {"change_plan": "admin_grant", "grant_free": "free_grant", "grant_lifetime": "lifetime"}[a]
        result = ent.grant_subscription(user_id, plan["id"], source=source, days=body.days, actor_id=actor["id"], notes=body.note or "",
                                        lifetime=a == "grant_lifetime")
        tracking.event("subscription_changed", feature="subscription", actor="system", user_id=user_id, meta={"plan": plan["code"], "source": source})
    elif a in ("revoke_access", "suspend_subscription", "reactivate_subscription"):
        st = {"revoke_access": "revoked", "suspend_subscription": "suspended", "reactivate_subscription": "active"}[a]
        result = {"updated": ent.set_subscription_status(user_id, st)}
        tracking.event("subscription_changed", feature="subscription", actor="system", user_id=user_id, meta={"status": st})
    elif a == "reset_sessions":
        result = {"revoked": auth.revoke_user_sessions(user_id)}
    elif a == "reset_2fa":
        if actor["role"] != "owner":
            raise HTTPException(403, "Only the owner can reset two-factor authentication")
        db.execute("UPDATE users SET totp_secret = NULL, totp_enabled = 0 WHERE id = ?", (user_id,))
        auth.revoke_user_sessions(user_id)
    elif a == "reset_usage":
        db.execute("DELETE FROM usage_counters WHERE user_id = ?", (user_id,))
    elif a == "password_reset_link":
        tok = auth.issue_token(user_id, "reset_password", 60)
        result = {"link": str(request.base_url).rstrip("/") + f"/reset-password?token={tok}", "expires_minutes": 60,
                  "note": "Share this link only with the account holder through a channel you trust. It works once."}
    elif a == "set_role":
        if actor["role"] != "owner":
            raise HTTPException(403, "Only the owner can change roles")
        if body.role not in ("admin", "support", "user"):
            raise HTTPException(422, "Role must be admin, support or user")
        if u["id"] == actor["id"]:
            raise HTTPException(409, "You can't change your own role")
        db.execute("UPDATE users SET role = ?, updated_at = ? WHERE id = ?", (body.role, db.now_iso(), user_id))
        auth.revoke_user_sessions(user_id)
    else:
        raise HTTPException(422, "Unknown action")
    _log(request, f"user.{a}", user_id, {k: v for k, v in body.model_dump().items() if v is not None and k != "confirm"})
    return {"ok": True, "result": result}


class OverrideIn(BaseModel):
    key: str
    value: int | bool | str | None = None
    reason: str | None = Field(default=None, max_length=300)
    expires_at: str | None = None


@router.put("/users/{user_id}/overrides")
def put_override(user_id: int, body: OverrideIn, request: Request, actor=WRITE):
    if not auth.get_user(user_id):
        raise HTTPException(404, "User not found")
    ent.set_override(user_id, body.key, body.value, body.reason or "", body.expires_at, actor["id"])
    _log(request, "user.override", user_id, body.model_dump())
    return {"ok": True}


@router.delete("/users/{user_id}/overrides/{key}")
def del_override(user_id: int, key: str, request: Request, actor=WRITE):
    ent.remove_override(user_id, key)
    _log(request, "user.override_removed", user_id, {"key": key})
    return {"ok": True}


# ------------------------------------------------------------------ activity
@router.get("/activity", dependencies=[READ])
def activity(user_id: int | None = None, event: str = "", actor: str = "", success: str = "", page: int = 1, page_size: int = 50):
    where, params = ["1=1"], []
    if user_id:
        where.append("e.user_id = ?")
        params.append(user_id)
    if event:
        where.append("e.event = ?")
        params.append(event)
    if actor in ("user", "system"):
        where.append("e.actor = ?")
        params.append(actor)
    if success in ("0", "1"):
        where.append("e.success = ?")
        params.append(int(success))
    w = " AND ".join(where)
    total = db.query_one(f"SELECT COUNT(*) n FROM activity_events e WHERE {w}", params)["n"]
    rows = db.query(f"SELECT e.*, u.email FROM activity_events e LEFT JOIN users u ON u.id = e.user_id WHERE {w} ORDER BY e.id DESC LIMIT ? OFFSET ?",
                    params + [min(200, page_size), (max(1, page) - 1) * min(200, page_size)])
    events = [r["event"] for r in db.query("SELECT DISTINCT event FROM activity_events ORDER BY event")]
    return {"items": rows, "total": total, "events": events}


# ------------------------------------------------------------------ subscriptions & revenue
@router.get("/subscriptions", dependencies=[READ])
def subscriptions(status: str = "", source: str = ""):
    where, params = ["1=1"], []
    if status:
        where.append("s.status = ?")
        params.append(status)
    if source:
        where.append("s.source = ?")
        params.append(source)
    rows = db.query(f"SELECT s.*, p.name AS plan_name, u.email FROM subscriptions s JOIN plans p ON p.id = s.plan_id "
                    f"LEFT JOIN users u ON u.id = s.user_id WHERE {' AND '.join(where)} ORDER BY s.id DESC LIMIT 500", params)
    plan_breakdown = db.query("SELECT p.name, COUNT(*) n FROM subscriptions s JOIN plans p ON p.id = s.plan_id WHERE s.status IN ('active','lifetime') GROUP BY p.name")
    return {"items": rows, "plan_breakdown": plan_breakdown}


@router.get("/revenue", dependencies=[READ])
def revenue():
    now = datetime.now(timezone.utc).isoformat()
    q = lambda sql, p=(): db.query_one(sql, p)["n"]  # noqa: E731
    gross = q("SELECT COALESCE(SUM(amount),0) n FROM payments WHERE kind='charge' AND status='captured' AND verified=1")
    refunds = q("SELECT COALESCE(SUM(amount),0) n FROM payments WHERE kind='refund' AND verified=1")
    mrr = q("SELECT COALESCE(SUM(CASE WHEN billing_period='annual' THEN locked_price/12.0 ELSE locked_price END),0) n FROM subscriptions "
            "WHERE source IN ('payment','manual_payment') AND status='active' AND (ends_at IS NULL OR ends_at > ?)", (now,))
    return {
        "payments_enabled": False,
        "actual": {"gross": round(gross, 2), "refunds": round(refunds, 2), "net": round(gross - refunds, 2),
                   "failed_payments": q("SELECT COUNT(*) n FROM payments WHERE status='failed'"),
                   "manual_records": q("SELECT COUNT(*) n FROM payments WHERE is_manual = 1")},
        "projected": {"mrr": round(mrr, 2), "label": "Projected from active paid subscriptions' locked prices — not collected revenue"},
        "by_month": db.query("SELECT substr(created_at,1,7) month, ROUND(SUM(CASE WHEN kind='charge' THEN amount ELSE -amount END),2) net "
                             "FROM payments WHERE verified=1 AND status IN ('captured','refunded') GROUP BY month ORDER BY month"),
        "by_plan": db.query("SELECT p.name plan, ROUND(SUM(pay.amount),2) amount FROM payments pay JOIN subscriptions s ON s.id = pay.subscription_id "
                            "JOIN plans p ON p.id = s.plan_id WHERE pay.kind='charge' AND pay.verified=1 AND pay.status='captured' GROUP BY p.name"),
        "churn": {"cancellations_30d": q("SELECT COUNT(*) n FROM subscriptions WHERE cancelled_at >= datetime('now','-30 days')"),
                  "renewals": "Not tracked until payments are enabled"},
        "payments": db.query("SELECT pay.*, u.email FROM payments pay LEFT JOIN users u ON u.id = pay.user_id ORDER BY pay.id DESC LIMIT 200"),
        "invoices": db.query("SELECT * FROM invoices ORDER BY id DESC LIMIT 200"),
    }


class ManualPayment(BaseModel):
    user_id: int
    amount: float = Field(gt=0, le=1_000_000)
    currency: str = "INR"
    method: str = "other"
    reference: str = Field(min_length=3, max_length=120)
    plan_code: str | None = None
    period: str = "monthly"
    note: str | None = Field(default=None, max_length=300)
    attest: bool = False


@router.post("/payments/manual")
def record_manual_payment(body: ManualPayment, request: Request, actor=Depends(staff(write=True, owner_only=True))):
    if not body.attest:
        raise HTTPException(422, "Confirm that you verified this payment was actually received")
    if not auth.get_user(body.user_id):
        raise HTTPException(404, "User not found")
    sub_id = None
    if body.plan_code:
        plan = ent.get_plan(code=body.plan_code)
        if not plan:
            raise HTTPException(422, "Plan not found")
        sub = ent.grant_subscription(body.user_id, plan["id"], source="manual_payment", days=365 if body.period == "annual" else 30,
                                     actor_id=actor["id"], notes=body.note or "")
        db.execute("UPDATE subscriptions SET billing_period = ?, locked_price = ? WHERE id = ?", (body.period, body.amount, sub["id"]))
        sub_id = sub["id"]
    pid = db.execute("INSERT INTO payments(user_id, subscription_id, kind, status, amount, currency, gateway, gateway_txn_id, method, is_manual, "
                     "verified, recorded_by, notes, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                     (body.user_id, sub_id, "charge", "captured", body.amount, body.currency.upper()[:3], "manual", body.reference, body.method,
                      1, 1, actor["id"], body.note, db.now_iso()))
    number = f"JP-{datetime.now(timezone.utc):%Y%m}-{pid:05d}"
    db.execute("INSERT INTO invoices(number, user_id, payment_id, amount, currency, issued_at) VALUES (?,?,?,?,?,?)",
               (number, body.user_id, pid, body.amount, body.currency.upper()[:3], db.now_iso()))
    _log(request, "payment.manual_recorded", body.user_id, {"amount": body.amount, "reference": body.reference, "plan": body.plan_code})
    return {"ok": True, "payment_id": pid, "invoice": number}


class RefundIn(BaseModel):
    amount: float = Field(gt=0)
    reference: str = Field(min_length=3, max_length=120)
    attest: bool = False


@router.post("/payments/{pid}/refund")
def record_refund(pid: int, body: RefundIn, request: Request, actor=Depends(staff(write=True, owner_only=True))):
    p = db.query_one("SELECT * FROM payments WHERE id = ? AND kind = 'charge'", (pid,))
    if not p:
        raise HTTPException(404, "Payment not found")
    if not body.attest or body.amount > p["amount"]:
        raise HTTPException(422, "Confirm the refund was issued, and keep the amount within the original payment")
    db.execute("INSERT INTO payments(user_id, subscription_id, kind, status, amount, currency, gateway, gateway_txn_id, is_manual, verified, "
               "recorded_by, created_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
               (p["user_id"], p["subscription_id"], "refund", "refunded", body.amount, p["currency"], p["gateway"], body.reference, 1, 1,
                actor["id"], db.now_iso()))
    _log(request, "payment.refund_recorded", p["user_id"], {"payment_id": pid, "amount": body.amount})
    return {"ok": True}


# ------------------------------------------------------------------ plans, coupons, trials, limits
@router.get("/plans", dependencies=[READ])
def plans():
    return {"plans": ent.list_plans(), "limit_keys": {k: v["label"] for k, v in ent.LIMITS.items()}, "feature_keys": ent.FEATURES,
            "coupons": db.query("SELECT * FROM coupons ORDER BY id DESC")}


@router.post("/plans")
def create_plan(body: dict, request: Request, actor=WRITE):
    p = ent.save_plan(body)
    _log(request, "plan.created", None, {"code": p["code"]})
    return p


@router.put("/plans/{plan_id}")
def update_plan(plan_id: int, body: dict, request: Request, actor=WRITE):
    if not ent.get_plan(plan_id):
        raise HTTPException(404, "Plan not found")
    before = ent.get_plan(plan_id)
    p = ent.save_plan(body, plan_id)
    _log(request, "plan.updated", None, {"code": p["code"], "price_before": before["monthly_price"], "price_after": p["monthly_price"]})
    return p


class CouponIn(BaseModel):
    code: str = Field(min_length=3, max_length=40, pattern=r"^[A-Za-z0-9_-]+$")
    description: str | None = Field(default=None, max_length=200)
    discount_pct: float = Field(gt=0, le=100)
    plan_codes: list[str] = []
    max_redemptions: int | None = Field(default=None, ge=1)
    valid_from: str | None = None
    valid_until: str | None = None
    active: bool = True


@router.post("/coupons")
def create_coupon(body: CouponIn, request: Request, actor=WRITE):
    if db.query_one("SELECT id FROM coupons WHERE code = ? COLLATE NOCASE", (body.code,)):
        raise HTTPException(409, "Coupon code already exists")
    db.execute("INSERT INTO coupons(code, description, discount_pct, plan_codes, max_redemptions, valid_from, valid_until, active, created_at) "
               "VALUES (?,?,?,?,?,?,?,?,?)", (body.code.upper(), body.description, body.discount_pct, db.jdumps(body.plan_codes),
                                              body.max_redemptions, body.valid_from, body.valid_until, 1 if body.active else 0, db.now_iso()))
    _log(request, "coupon.created", None, {"code": body.code.upper(), "discount": body.discount_pct})
    return {"ok": True}


@router.put("/coupons/{cid}")
def toggle_coupon(cid: int, body: dict, request: Request, actor=WRITE):
    db.execute("UPDATE coupons SET active = ? WHERE id = ?", (1 if body.get("active") else 0, cid))
    _log(request, "coupon.updated", None, {"id": cid, "active": bool(body.get("active"))})
    return {"ok": True}


@router.get("/trials", dependencies=[READ])
def trials():
    now = datetime.now(timezone.utc).isoformat()
    soon = (datetime.now(timezone.utc) + timedelta(days=7)).isoformat()
    cfg = ent.system()
    return {"config": {"trial_days": cfg["trial_days"], "trial_plan_code": cfg["trial_plan_code"], "trial_reminder_days": cfg["trial_reminder_days"]},
            "active": db.query("SELECT t.*, u.email FROM trials t JOIN users u ON u.id = t.user_id WHERE t.status = 'active' AND t.ends_at > ? ORDER BY t.ends_at", (now,)),
            "expiring_7d": db.query_one("SELECT COUNT(*) n FROM trials WHERE status='active' AND ends_at > ? AND ends_at <= ?", (now, soon))["n"],
            "expired": db.query_one("SELECT COUNT(*) n FROM trials WHERE status='expired' OR (status='active' AND ends_at <= ?)", (now,))["n"],
            "plans": [{"code": p["code"], "name": p["name"], "trial_eligible": p["trial_eligible"]} for p in ent.list_plans()]}


@router.get("/limits", dependencies=[READ])
def limits():
    return {"plans": [{"id": p["id"], "code": p["code"], "name": p["name"], "entitlements": p["entitlements"]} for p in ent.list_plans()],
            "limit_keys": {k: v["label"] for k, v in ent.LIMITS.items()}, "feature_keys": ent.FEATURES,
            "overrides": db.query("SELECT o.*, u.email FROM user_overrides o JOIN users u ON u.id = o.user_id ORDER BY o.created_at DESC LIMIT 200")}


# ------------------------------------------------------------------ outcome analytics
@router.get("/analytics", dependencies=[READ])
def analytics():
    q = lambda sql, p=(): db.query_one(sql, p)["n"]  # noqa: E731
    discovered = q("SELECT COUNT(*) n FROM jobs")
    eligible = q("SELECT COUNT(*) n FROM jobs WHERE eligibility_result = 'pass'")
    matching = q("SELECT COUNT(*) n FROM jobs WHERE status IN ('qualified','shortlisted')")
    shortlisted = q("SELECT COUNT(*) n FROM jobs WHERE decision = 'shortlisted'")
    tailored = q("SELECT COUNT(DISTINCT job_id) n FROM resumes")
    prepared = q("SELECT COUNT(*) n FROM applications")
    submitted = q("SELECT COUNT(DISTINCT application_id) n FROM application_history WHERE to_status = 'applied'")
    interviews = q("SELECT COUNT(DISTINCT application_id) n FROM application_history WHERE to_status = 'interview'")
    offers = q("SELECT COUNT(DISTINCT application_id) n FROM application_history WHERE to_status = 'offer'")
    rejections = q("SELECT COUNT(DISTINCT application_id) n FROM application_history WHERE to_status = 'rejected'")
    rate = lambda a, b: round(100 * a / b, 1) if b else None  # noqa: E731
    return {
        "funnel": [
            {"stage": "Jobs discovered", "n": discovered, "actor": "JobPilot (automatic)"},
            {"stage": "Passed hard eligibility", "n": eligible, "actor": "JobPilot (automatic)"},
            {"stage": "Matched user criteria (≥ threshold)", "n": matching, "actor": "JobPilot (automatic)"},
            {"stage": "Shortlisted", "n": shortlisted, "actor": "User action"},
            {"stage": "Resume tailored", "n": tailored, "actor": "JobPilot, requested by user"},
            {"stage": "Applications prepared", "n": prepared, "actor": "JobPilot, requested by user"},
            {"stage": "Applications submitted", "n": submitted, "actor": "User-reported"},
            {"stage": "Interviews", "n": interviews, "actor": "User-reported"},
            {"stage": "Offers", "n": offers, "actor": "User-reported"},
        ],
        "rejections": rejections, "verified_outcomes": 0,
        "conversions": {"search_to_shortlist": rate(shortlisted, discovered), "shortlist_to_application": rate(prepared, shortlisted),
                        "application_to_interview": rate(interviews, submitted), "interview_to_offer": rate(offers, interviews)},
        "sources": db.query("SELECT source, COUNT(*) jobs, SUM(CASE WHEN status IN ('qualified','shortlisted') THEN 1 ELSE 0 END) qualifying, "
                            "ROUND(AVG(match_score),1) avg_match, (SELECT COUNT(*) FROM applications a JOIN jobs j2 ON j2.id = a.job_id "
                            "WHERE j2.source = jobs.source) applications FROM jobs GROUP BY source ORDER BY jobs DESC"),
        "avg_match": db.query_one("SELECT ROUND(AVG(match_score),1) n FROM jobs WHERE match_score IS NOT NULL")["n"],
        "outcome_trend": db.query("SELECT substr(at,1,7) month, SUM(to_status='applied') applied, SUM(to_status='interview') interviews, "
                                  "SUM(to_status='offer') offers, SUM(to_status='rejected') rejections FROM application_history GROUP BY month ORDER BY month"),
        "feature_usage": db.query("SELECT event, COUNT(*) n, SUM(success=0) failures FROM activity_events GROUP BY event ORDER BY n DESC"),
        "subscription_conversion": {
            "trials_total": q("SELECT COUNT(*) n FROM trials"),
            "converted_to_paid": q("SELECT COUNT(DISTINCT t.user_id) n FROM trials t JOIN subscriptions s ON s.user_id = t.user_id AND s.source IN ('payment','manual_payment')")},
        "note": "Outcomes are reported by users and not independently verified. Correlation with JobPilot use does not show causation.",
    }


# ------------------------------------------------------------------ health, AI usage, sources
@router.get("/health", dependencies=[READ])
def health():
    dbsize = settings.db_path.stat().st_size if settings.db_path.exists() else 0
    return {
        "status": "ok", "uptime_seconds": int(time.time() - STARTED), "python": platform.python_version(), "environment": settings.env,
        "deployment": {"commit": os.getenv("RENDER_GIT_COMMIT", "")[:12] or None, "service": os.getenv("RENDER_SERVICE_NAME") or None,
                       "instance": os.getenv("RENDER_INSTANCE_ID", "")[:12] or None},
        "database": {"size_mb": round(dbsize / 1048576, 2), "users": db.query_one("SELECT COUNT(*) n FROM users")["n"],
                     "jobs": db.query_one("SELECT COUNT(*) n FROM jobs")["n"]},
        "storage": {"persistent": settings.persistent_storage,
                    "warning": None if settings.persistent_storage else "Storage is ephemeral: data is lost on restart/redeploy unless restored from backup."},
        "errors_24h": db.query("SELECT action, COUNT(*) n FROM audit_events WHERE level='error' AND at >= datetime('now','-1 day') GROUP BY action ORDER BY n DESC"),
        "failed_tasks_24h": db.query_one("SELECT COUNT(*) n FROM tasks WHERE status='failed' AND updated_at >= datetime('now','-1 day')")["n"],
        "integrations": {"email": emailer.configured(), "google_oauth": auth.google_configured(), "backups_s3": backups.s3_configured(),
                         "backup_encryption": bool(backups.encryption_key()), "ai_provider": settings.ai_provider,
                         "ai_key_configured": settings.ai_key_present(), "payments": False},
        "backups": {k: v for k, v in backups.status().items() if k != "recent"},
    }


@router.get("/ai-usage", dependencies=[READ])
def ai_usage():
    return {"by_day": db.query("SELECT substr(at,1,10) day, COUNT(*) calls, SUM(cached) cached, SUM(input_tokens+output_tokens) tokens, "
                               "ROUND(SUM(cost_inr),4) cost FROM ai_usage WHERE at >= datetime('now','-60 days') GROUP BY day ORDER BY day"),
            "by_model": db.query("SELECT provider, model, COUNT(*) calls, ROUND(SUM(cost_inr),4) cost FROM ai_usage GROUP BY provider, model"),
            "top_users": db.query("SELECT u.email, COUNT(*) calls, ROUND(SUM(a.cost_inr),4) cost FROM ai_usage a JOIN users u ON u.id = a.user_id "
                                  "WHERE a.at >= datetime('now','-30 days') GROUP BY u.email ORDER BY calls DESC LIMIT 20"),
            "global_budget_inr": ent.system()["global_ai_monthly_budget_inr"],
            "month_cost_inr": round(db.query_one("SELECT COALESCE(SUM(cost_inr),0) n FROM ai_usage WHERE substr(at,1,7) = ?",
                                                 (datetime.now(timezone.utc).strftime("%Y-%m"),))["n"], 4),
            "provider": settings.ai_provider, "key_configured": settings.ai_key_present()}


@router.get("/sources", dependencies=[READ])
def sources():
    return {"config": ent.system()["discovery"],
            "registry": db.query("SELECT ats, COUNT(*) companies, SUM(enabled) enabled, COUNT(DISTINCT user_id) users FROM companies GROUP BY ats"),
            "effectiveness": db.query("SELECT source, COUNT(*) jobs, SUM(CASE WHEN status IN ('qualified','shortlisted') THEN 1 ELSE 0 END) qualifying, "
                                      "ROUND(AVG(match_score),1) avg_match FROM jobs GROUP BY source ORDER BY jobs DESC"),
            "runs": db.query("SELECT status, COUNT(*) n FROM discovery_runs WHERE started_at >= datetime('now','-30 days') GROUP BY status")}


# ------------------------------------------------------------------ announcements & support
class AnnouncementIn(BaseModel):
    title: str = Field(min_length=3, max_length=120)
    body: str | None = Field(default=None, max_length=1000)
    level: str = Field(default="info", pattern="^(info|warning|success)$")
    audience: str = Field(default="all", pattern="^(all|users|public)$")
    active: bool = True
    starts_at: str | None = None
    ends_at: str | None = None


@router.get("/announcements", dependencies=[READ])
def announcements():
    return db.query("SELECT * FROM announcements ORDER BY id DESC")


@router.post("/announcements")
def create_announcement(body: AnnouncementIn, request: Request, actor=WRITE):
    aid = db.execute("INSERT INTO announcements(title, body, level, audience, active, starts_at, ends_at, created_by, created_at) VALUES (?,?,?,?,?,?,?,?,?)",
                     (body.title, body.body, body.level, body.audience, 1 if body.active else 0, body.starts_at, body.ends_at, actor["id"], db.now_iso()))
    _log(request, "announcement.created", None, {"id": aid, "title": body.title})
    return {"ok": True, "id": aid}


@router.put("/announcements/{aid}")
def update_announcement(aid: int, body: AnnouncementIn, request: Request, actor=WRITE):
    db.execute("UPDATE announcements SET title=?, body=?, level=?, audience=?, active=?, starts_at=?, ends_at=? WHERE id=?",
               (body.title, body.body, body.level, body.audience, 1 if body.active else 0, body.starts_at, body.ends_at, aid))
    _log(request, "announcement.updated", None, {"id": aid})
    return {"ok": True}


@router.delete("/announcements/{aid}")
def delete_announcement(aid: int, request: Request, actor=WRITE):
    db.execute("DELETE FROM announcements WHERE id = ?", (aid,))
    _log(request, "announcement.deleted", None, {"id": aid})
    return {"ok": True}


@router.get("/support", dependencies=[READ])
def support(status: str = ""):
    rows = db.query("SELECT t.*, (SELECT COUNT(*) FROM support_messages m WHERE m.ticket_id = t.id) messages FROM support_tickets t "
                    + ("WHERE t.status = ? " if status else "") + "ORDER BY t.updated_at DESC LIMIT 300", (status,) if status else ())
    return {"items": rows}


@router.get("/support/{tid}", dependencies=[READ])
def support_ticket(tid: int):
    t = db.query_one("SELECT * FROM support_tickets WHERE id = ?", (tid,))
    if not t:
        raise HTTPException(404, "Ticket not found")
    t["messages"] = db.query("SELECT m.*, u.email AS author_email FROM support_messages m LEFT JOIN users u ON u.id = m.author_id WHERE ticket_id = ? ORDER BY m.id", (tid,))
    return t


class SupportReply(BaseModel):
    message: str | None = Field(default=None, max_length=4000)
    status: str | None = Field(default=None, pattern="^(open|answered|closed)$")


@router.post("/support/{tid}")
def support_reply(tid: int, body: SupportReply, request: Request, actor=Depends(staff())):
    t = support_ticket(tid)
    now = db.now_iso()
    if body.message:
        db.execute("INSERT INTO support_messages(ticket_id, author_id, from_staff, body, created_at) VALUES (?,?,?,?,?)", (tid, actor["id"], 1, body.message, now))
        if t["user_id"]:
            from .. import audit
            audit.notify_user("info", f"Support replied: {t['subject']}", link="/app/support", user_id=t["user_id"])
    db.execute("UPDATE support_tickets SET status = ?, updated_at = ? WHERE id = ?", (body.status or ("answered" if body.message else t["status"]), now, tid))
    _log(request, "support.reply", t["user_id"], {"ticket": tid, "status": body.status})
    return support_ticket(tid)


# ------------------------------------------------------------------ audit, security, settings, backups
@router.get("/audit", dependencies=[READ])
def audit_logs(action: str = "", page: int = 1):
    where, params = ("WHERE a.action LIKE ?", [f"%{action}%"]) if action else ("", [])
    rows = db.query(f"SELECT a.*, u.email AS actor_email FROM admin_audit a LEFT JOIN users u ON u.id = a.actor_id {where} ORDER BY a.id DESC LIMIT 100 OFFSET ?",
                    params + [(max(1, page) - 1) * 100])
    for r in rows:
        r["details"] = db.jloads(r["details"], None)
    return {"items": rows, "total": db.query_one(f"SELECT COUNT(*) n FROM admin_audit a {where}", params)["n"]}


@router.get("/security", dependencies=[READ])
def security():
    return {"staff": db.query("SELECT id, email, name, role, totp_enabled, last_login_at, status FROM users WHERE role IN ('owner','admin','support') ORDER BY role"),
            "staff_sessions": db.query("SELECT substr(s.id,1,10) id, u.email, s.created_at, s.last_seen_at, s.elevated_until FROM sessions s JOIN users u ON u.id = s.user_id "
                                       "WHERE u.role IN ('owner','admin','support') AND s.revoked = 0 AND s.expires_at > ? ORDER BY s.last_seen_at DESC",
                                       (datetime.now(timezone.utc).isoformat(),)),
            "failed_logins_24h": db.query_one("SELECT COUNT(*) n FROM login_attempts WHERE success = 0 AND at >= datetime('now','-1 day')")["n"],
            "policies": {"password_hashing": "scrypt (N=16384, r=8, p=1)", "session_idle_days": auth.SESSION_IDLE_DAYS,
                         "session_absolute_days": auth.SESSION_ABSOLUTE_DAYS, "admin_2fa": "TOTP required", "admin_elevation_hours": auth.ELEVATION_HOURS,
                         "cookies": "HttpOnly, SameSite=Lax" + (", Secure" if settings.cookie_secure else "")}}


@router.post("/security/sessions/{sid_prefix}/revoke")
def revoke_staff_session(sid_prefix: str, request: Request, actor=OWNER):
    n = db.execute("UPDATE sessions SET revoked = 1 WHERE substr(id,1,10) = ?", (sid_prefix[:10],))
    _log(request, "security.session_revoked", None, {"session": sid_prefix[:10]})
    return {"ok": True}


@router.get("/settings", dependencies=[READ])
def get_system_settings():
    return {"system": ent.system(), "registration_effective": ent.registration_open(),
            "secrets": {"JOBPILOT_SECRET_KEY": not settings.secret_key_ephemeral, "OWNER_SETUP_TOKEN": bool(os.getenv("OWNER_SETUP_TOKEN")),
                        "GOOGLE_CLIENT_ID/SECRET": auth.google_configured(), "SMTP_*": emailer.configured(),
                        "BACKUP_S3_*": backups.s3_configured(), "BACKUP_ENCRYPTION_KEY": bool(backups.encryption_key()),
                        "GEMINI_API_KEY": bool(settings.gemini_api_key), "GROQ_API_KEY": bool(settings.groq_api_key),
                        "RAZORPAY_* / CASHFREE_*": False},
            "note": "Secret values are never shown — only whether each one is configured."}


@router.put("/settings")
def put_system_settings(body: dict, request: Request, actor=WRITE):
    before = ent.system()
    after = ent.save_system(body)
    changed = {k: after[k] for k in body if before.get(k) != after.get(k)}
    _log(request, "settings.updated", None, changed)
    return {"system": after, "registration_effective": ent.registration_open()}


@router.get("/backups", dependencies=[READ])
def backup_status():
    return backups.status()


@router.post("/backups/run")
def backup_run(request: Request, actor=WRITE):
    r = backups.run_backup("manual")
    _log(request, "backup.manual", None, {"status": r.get("status")})
    return r
