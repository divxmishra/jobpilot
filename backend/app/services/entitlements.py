"""System configuration, plans, trials, subscriptions and usage entitlements.

All plan limits/features live in the `plans` table (admin-editable). Nothing about
plan restrictions is hardcoded elsewhere: features call `require_feature()` /
`consume()` with a key from LIMITS / FEATURES. Trial and subscription validity are
computed from server timestamps only.
"""
from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

from .. import db
from ..context import current_user, uid

# ------------------------------------------------------------------ catalogue of keys
# period: "day"/"month" = counted per period; "count" = live count from the database; None = feature
LIMITS = {
    "job_searches_per_day": {"label": "Job searches per day", "period": "day", "counter": "job_search"},
    "job_searches_per_month": {"label": "Job searches per month", "period": "month", "counter": "job_search"},
    "jobs_viewed_per_month": {"label": "Jobs viewed per month", "period": "month", "counter": "job_view"},
    "resume_analyses_per_month": {"label": "Resume analyses per month", "period": "month", "counter": "resume_analysis"},
    "resume_generations_per_month": {"label": "Tailored resume generations per month", "period": "month", "counter": "resume_generation"},
    "ai_requests_per_month": {"label": "AI requests per month", "period": "month", "counter": "ai_request"},
    "saved_jobs": {"label": "Saved (shortlisted) jobs", "period": "count"},
    "applications_tracked": {"label": "Applications tracked", "period": "count"},
    "resumes_count": {"label": "Tailored resume versions stored", "period": "count"},
    "storage_mb": {"label": "Storage (MB)", "period": "count"},
}
FEATURES = {
    "career_roadmap": "Career Roadmap",
    "roadmap_unlock_estimates": "Career Roadmap job-unlock estimates",
    "advanced_analytics": "Advanced analytics",
    "automation": "Automated discovery & browser prefill",
    "outreach": "Contacts & outreach drafts",
}

DEFAULT_PLANS = [
    {"code": "free", "name": "Free", "description": "Core truthful job matching for getting started.", "monthly_price": 0,
     "is_free_default": 1, "trial_eligible": 0, "sort_order": 1,
     "features": ["Verified profile from your resume", "Job matching with evidence", "Truthful tailored resumes (limited)",
                  "Application tracking"],
     "entitlements": {"job_searches_per_day": 5, "job_searches_per_month": 50, "jobs_viewed_per_month": 200,
                      "resume_analyses_per_month": 3, "resume_generations_per_month": 3, "ai_requests_per_month": 0,
                      "saved_jobs": 10, "applications_tracked": 10, "resumes_count": 10, "storage_mb": 25,
                      "career_roadmap": True, "roadmap_unlock_estimates": False, "advanced_analytics": False,
                      "automation": False, "outreach": True}},
    {"code": "basic", "name": "Basic", "description": "More searches and tailoring for an active job search.", "monthly_price": None,
     "is_free_default": 0, "trial_eligible": 0, "sort_order": 2,
     "features": ["Everything in Free", "Higher monthly limits", "Automated daily discovery", "Career Roadmap unlock estimates"],
     "entitlements": {"job_searches_per_day": 25, "job_searches_per_month": 300, "jobs_viewed_per_month": 2000,
                      "resume_analyses_per_month": 10, "resume_generations_per_month": 30, "ai_requests_per_month": 200,
                      "saved_jobs": 100, "applications_tracked": 100, "resumes_count": 100, "storage_mb": 100,
                      "career_roadmap": True, "roadmap_unlock_estimates": True, "advanced_analytics": True,
                      "automation": True, "outreach": True}},
    {"code": "pro", "name": "Pro", "description": "The full toolkit for serious, high-volume searches.", "monthly_price": None,
     "is_free_default": 0, "trial_eligible": 1, "sort_order": 3,
     "features": ["Everything in Basic", "Unlimited searches and tailoring", "Highest AI allowance", "Priority support"],
     "entitlements": {"job_searches_per_day": None, "job_searches_per_month": None, "jobs_viewed_per_month": None,
                      "resume_analyses_per_month": 30, "resume_generations_per_month": None, "ai_requests_per_month": 1000,
                      "saved_jobs": None, "applications_tracked": None, "resumes_count": None, "storage_mb": 500,
                      "career_roadmap": True, "roadmap_unlock_estimates": True, "advanced_analytics": True,
                      "automation": True, "outreach": True}},
]

DEFAULT_SYSTEM = {
    "maintenance_mode": False,
    "maintenance_message": "JobPilot is undergoing maintenance. Please check back shortly.",
    "registration_open": None,          # None = automatic (closed in production until storage is persistent)
    "trial_days": 30,
    "trial_plan_code": "pro",
    "trial_reminder_days": 3,
    "analytics_level": "standard",      # standard | minimal | off
    "api_rate_limit_per_minute": 300,
    "global_ai_monthly_budget_inr": 300.0,
    "feature_flags": {"discovery": True, "outreach": True, "browser_prep": True, "ai": True, "demo": True,
                      "google_login": True, "career_roadmap": True, "support": True},
    "discovery": {"max_jobs_per_run": 80, "enabled_source_types": ["greenhouse", "lever", "ashby", "remotive", "career_page"]},
    "backup": {"interval_hours": 6, "debounce_minutes": 5, "retention": 14},
    "support_email": "",
}


class LimitReached(Exception):
    def __init__(self, info: dict):
        super().__init__(info.get("message", "Limit reached"))
        self.info = info


class FeatureLocked(Exception):
    def __init__(self, info: dict):
        super().__init__(info.get("message", "Not available on your plan"))
        self.info = info


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _parse(s):
    if not s:
        return None
    d = datetime.fromisoformat(s.replace("Z", "+00:00"))
    return d if d.tzinfo else d.replace(tzinfo=timezone.utc)


# ------------------------------------------------------------------ system config
def system() -> dict:
    cfg = copy.deepcopy(DEFAULT_SYSTEM)
    stored = db.get_setting("system", {}) or {}
    for k, v in stored.items():
        if isinstance(v, dict) and isinstance(cfg.get(k), dict):
            cfg[k].update(v)
        else:
            cfg[k] = v
    return cfg


def save_system(patch: dict) -> dict:
    cur = system()
    for k, v in patch.items():
        if k not in DEFAULT_SYSTEM:
            raise ValueError(f"Unknown setting '{k}'")
        if isinstance(DEFAULT_SYSTEM[k], dict):
            if not isinstance(v, dict):
                raise ValueError(f"'{k}' must be an object")
            cur[k].update({kk: vv for kk, vv in v.items() if kk in DEFAULT_SYSTEM[k]})
        else:
            cur[k] = v
    if not 1 <= int(cur["trial_days"]) <= 365:
        raise ValueError("Trial duration must be 1–365 days")
    if cur["analytics_level"] not in ("standard", "minimal", "off"):
        raise ValueError("Analytics level must be standard, minimal or off")
    if not 10 <= int(cur["api_rate_limit_per_minute"]) <= 10000:
        raise ValueError("API rate limit must be between 10 and 10000 per minute")
    if not db.query_one("SELECT id FROM plans WHERE code = ?", (cur["trial_plan_code"],)):
        raise ValueError("Trial plan does not exist")
    db.set_setting("system", cur)
    return cur


def flag(name: str) -> bool:
    return bool(system()["feature_flags"].get(name, True))


def registration_open() -> bool:
    from ..config import settings
    v = system()["registration_open"]
    if v is None:
        return (not settings.is_production) or settings.persistent_storage
    return bool(v)


# ------------------------------------------------------------------ plans
def seed_plans() -> None:
    if db.query_one("SELECT id FROM plans LIMIT 1"):
        return
    now = db.now_iso()
    for p in DEFAULT_PLANS:
        db.execute("INSERT INTO plans(code, name, description, monthly_price, features, entitlements, trial_eligible, is_free_default, "
                   "sort_order, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                   (p["code"], p["name"], p["description"], p["monthly_price"], db.jdumps(p["features"]), db.jdumps(p["entitlements"]),
                    p["trial_eligible"], p["is_free_default"], p["sort_order"], now, now))


def _plan_row(r: dict | None) -> dict | None:
    if not r:
        return None
    r = dict(r)
    r["features"] = db.jloads(r["features"], [])
    ent = {k: None for k in LIMITS} | {k: False for k in FEATURES}
    ent.update(db.jloads(r["entitlements"], {}))
    r["entitlements"] = ent
    r["price_set"] = r["monthly_price"] is not None
    if r["monthly_price"] is not None and r["discount_pct"]:
        r["effective_monthly_price"] = round(r["monthly_price"] * (1 - r["discount_pct"] / 100), 2)
    else:
        r["effective_monthly_price"] = r["monthly_price"]
    return r


def list_plans(public_only: bool = False) -> list[dict]:
    rows = db.query("SELECT * FROM plans" + (" WHERE available = 1" if public_only else "") + " ORDER BY sort_order, id")
    return [_plan_row(r) for r in rows]


def get_plan(plan_id: int | None = None, code: str | None = None) -> dict | None:
    if code:
        return _plan_row(db.query_one("SELECT * FROM plans WHERE code = ?", (code,)))
    return _plan_row(db.query_one("SELECT * FROM plans WHERE id = ?", (plan_id,)))


def free_plan() -> dict:
    p = db.query_one("SELECT * FROM plans WHERE is_free_default = 1 ORDER BY sort_order LIMIT 1") or \
        db.query_one("SELECT * FROM plans ORDER BY sort_order LIMIT 1")
    return _plan_row(p)


def validate_plan(data: dict, existing: dict | None = None) -> dict:
    import re
    out = {}
    code = (data.get("code") or (existing or {}).get("code") or "").strip().lower()
    if not re.fullmatch(r"[a-z0-9_]{2,30}", code):
        raise ValueError("Plan code must be 2–30 lowercase letters, digits or _")
    out["code"] = code
    out["name"] = (data.get("name") or "").strip()[:60]
    if not out["name"]:
        raise ValueError("Plan name is required")
    out["description"] = (data.get("description") or "").strip()[:400]
    for k in ("monthly_price", "annual_price"):
        v = data.get(k)
        if v in (None, ""):
            out[k] = None
        else:
            v = float(v)
            if v < 0 or v > 1_000_000:
                raise ValueError(f"{k.replace('_', ' ')} must be between 0 and 1,000,000")
            out[k] = round(v, 2)
    out["annual_enabled"] = 1 if data.get("annual_enabled") else 0
    if out["annual_enabled"] and out["annual_price"] is None:
        raise ValueError("Set an annual price or disable annual billing")
    d = float(data.get("discount_pct") or 0)
    if not 0 <= d <= 100:
        raise ValueError("Discount must be 0–100%")
    out["discount_pct"] = d
    out["promo_label"] = (data.get("promo_label") or "").strip()[:60] or None
    feats = data.get("features") or []
    if isinstance(feats, str):
        feats = [x.strip() for x in feats.split("\n")]
    out["features"] = [str(f)[:120] for f in feats if str(f).strip()][:20]
    ent = {}
    raw = data.get("entitlements") or {}
    for k in LIMITS:
        v = raw.get(k)
        if v in (None, "", "unlimited"):
            ent[k] = None
        else:
            v = int(v)
            if v < 0:
                raise ValueError(f"{LIMITS[k]['label']} cannot be negative")
            ent[k] = v
    for k in FEATURES:
        ent[k] = bool(raw.get(k))
    out["entitlements"] = ent
    out["trial_eligible"] = 1 if data.get("trial_eligible") else 0
    out["is_free_default"] = 1 if data.get("is_free_default") else 0
    out["available"] = 0 if data.get("available") is False else 1
    out["sort_order"] = int(data.get("sort_order") or 0)
    return out


def save_plan(data: dict, plan_id: int | None = None) -> dict:
    existing = get_plan(plan_id) if plan_id else None
    v = validate_plan(data, existing)
    now = db.now_iso()
    clash = db.query_one("SELECT id FROM plans WHERE code = ? AND id != ?", (v["code"], plan_id or 0))
    if clash:
        raise ValueError("Another plan already uses this code")
    with db.tx() as conn:
        if v["is_free_default"]:
            conn.execute("UPDATE plans SET is_free_default = 0 WHERE id != ?", (plan_id or 0,))
        cols = ["code", "name", "description", "monthly_price", "annual_price", "annual_enabled", "discount_pct", "promo_label",
                "features", "entitlements", "trial_eligible", "is_free_default", "available", "sort_order"]
        vals = [db.jdumps(v[c]) if c in ("features", "entitlements") else v[c] for c in cols]
        if plan_id:
            conn.execute(f"UPDATE plans SET {', '.join(c + ' = ?' for c in cols)}, updated_at = ? WHERE id = ?", (*vals, now, plan_id))
        else:
            cur = conn.execute(f"INSERT INTO plans({', '.join(cols)}, created_at, updated_at) VALUES ({', '.join('?' for _ in cols)}, ?, ?)",
                               (*vals, now, now))
            plan_id = cur.lastrowid
    if not db.query_one("SELECT id FROM plans WHERE is_free_default = 1"):
        raise ValueError("At least one plan must be the free default")
    return get_plan(plan_id)


# ------------------------------------------------------------------ trials
def start_trial_if_eligible(user_id: int) -> dict | None:
    """Called when the user completes profile setup (first verified profile)."""
    t = db.query_one("SELECT * FROM trials WHERE user_id = ?", (user_id,))
    if t and t["status"] in ("active", "expired", "revoked"):
        return t
    cfg = system()
    plan = get_plan(code=cfg["trial_plan_code"])
    if not plan or not plan["trial_eligible"]:
        return None
    now = _now()
    ends = now + timedelta(days=int(cfg["trial_days"]))
    db.execute("INSERT INTO trials(user_id, plan_id, started_at, ends_at, status, updated_at) VALUES (?,?,?,?,?,?) "
               "ON CONFLICT(user_id) DO UPDATE SET plan_id=excluded.plan_id, started_at=excluded.started_at, ends_at=excluded.ends_at, "
               "status='active', updated_at=excluded.updated_at",
               (user_id, plan["id"], now.isoformat(), ends.isoformat(), "active", now.isoformat()))
    return db.query_one("SELECT * FROM trials WHERE user_id = ?", (user_id,))


def trial_info(user_id: int) -> dict | None:
    t = db.query_one("SELECT t.*, p.name AS plan_name, p.code AS plan_code FROM trials t LEFT JOIN plans p ON p.id = t.plan_id "
                     "WHERE t.user_id = ?", (user_id,))
    if not t:
        return None
    now = _now()
    if t["status"] == "active" and _parse(t["ends_at"]) and _parse(t["ends_at"]) <= now:
        db.execute("UPDATE trials SET status = 'expired', updated_at = ? WHERE user_id = ?", (now.isoformat(), user_id))
        t["status"] = "expired"
        from . import tracking
        tracking.event("trial_expired", feature="subscription", actor="system", user_id=user_id)
        from .. import audit
        audit.notify_user("warning", "Your free trial has ended. You're now on the Free plan — your data is kept.",
                          link="/app/subscription", user_id=user_id)
    if t["status"] == "active":
        remaining = _parse(t["ends_at"]) - now
        t["days_remaining"] = max(0, remaining.days + (1 if remaining.seconds else 0))
    else:
        t["days_remaining"] = 0
    return t


def extend_trial(user_id: int, days: int) -> dict:
    if not 1 <= days <= 365:
        raise ValueError("Extension must be 1–365 days")
    t = db.query_one("SELECT * FROM trials WHERE user_id = ?", (user_id,))
    now = _now()
    if not t:
        plan = get_plan(code=system()["trial_plan_code"])
        db.execute("INSERT INTO trials(user_id, plan_id, started_at, ends_at, status, extended_days, updated_at) VALUES (?,?,?,?,?,?,?)",
                   (user_id, plan["id"], now.isoformat(), (now + timedelta(days=days)).isoformat(), "active", days, now.isoformat()))
    else:
        base = max(now, _parse(t["ends_at"]) or now)
        db.execute("UPDATE trials SET ends_at = ?, status = 'active', extended_days = extended_days + ?, reminder_sent_at = NULL, "
                   "updated_at = ? WHERE user_id = ?", ((base + timedelta(days=days)).isoformat(), days, now.isoformat(), user_id))
    return trial_info(user_id)


def revoke_trial(user_id: int) -> None:
    db.execute("UPDATE trials SET status = 'revoked', ends_at = ?, updated_at = ? WHERE user_id = ?",
               (_now().isoformat(), _now().isoformat(), user_id))


# ------------------------------------------------------------------ subscriptions
def active_subscription(user_id: int) -> dict | None:
    now = _now().isoformat()
    rows = db.query("SELECT s.*, p.sort_order, p.code AS plan_code, p.name AS plan_name FROM subscriptions s JOIN plans p ON p.id = s.plan_id "
                    "WHERE s.user_id = ? AND s.status IN ('active','lifetime') AND (s.ends_at IS NULL OR s.ends_at > ?) "
                    "ORDER BY p.sort_order DESC, s.id DESC", (user_id, now))
    expired = db.query("SELECT id FROM subscriptions WHERE user_id = ? AND status = 'active' AND ends_at IS NOT NULL AND ends_at <= ?",
                       (user_id, now))
    for e in expired:
        db.execute("UPDATE subscriptions SET status = 'expired', updated_at = ? WHERE id = ?", (now, e["id"]))
    return rows[0] if rows else None


def grant_subscription(user_id: int, plan_id: int, *, source: str, days: int | None, actor_id: int | None, notes: str = "",
                       lifetime: bool = False) -> dict:
    plan = get_plan(plan_id)
    if not plan:
        raise ValueError("Plan not found")
    if source not in ("admin_grant", "free_grant", "lifetime", "manual_payment", "payment"):
        raise ValueError("Invalid source")
    now = _now()
    ends = None if (lifetime or days is None) else (now + timedelta(days=days)).isoformat()
    with db.tx() as conn:
        conn.execute("UPDATE subscriptions SET status = 'cancelled', cancelled_at = ?, updated_at = ? "
                     "WHERE user_id = ? AND status IN ('active','lifetime','suspended')", (now.isoformat(), now.isoformat(), user_id))
        conn.execute("INSERT INTO subscriptions(user_id, plan_id, status, source, locked_price, currency, started_at, ends_at, notes, "
                     "created_by, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                     (user_id, plan_id, "lifetime" if lifetime else "active", "lifetime" if lifetime else source,
                      plan["effective_monthly_price"] if source in ("payment", "manual_payment") else 0,
                      plan["currency"], now.isoformat(), ends, notes[:300], actor_id, now.isoformat(), now.isoformat()))
    return active_subscription(user_id)


def set_subscription_status(user_id: int, status: str) -> int:
    if status not in ("suspended", "revoked", "cancelled", "active"):
        raise ValueError("Invalid status")
    now = _now().isoformat()
    src = ("active", "lifetime") if status != "active" else ("suspended",)
    with db.tx() as conn:
        return conn.execute(f"UPDATE subscriptions SET status = ?, updated_at = ?, cancelled_at = CASE WHEN ? IN ('cancelled','revoked') "
                            f"THEN ? ELSE cancelled_at END WHERE user_id = ? AND status IN ({','.join('?' for _ in src)})",
                            (status, now, status, now, user_id, *src)).rowcount


# ------------------------------------------------------------------ effective entitlements
def overrides(user_id: int) -> dict:
    now = _now().isoformat()
    rows = db.query("SELECT key, value FROM user_overrides WHERE user_id = ? AND (expires_at IS NULL OR expires_at > ?)", (user_id, now))
    return {r["key"]: db.jloads(r["value"], None) for r in rows}


def effective(user: dict | None = None) -> dict:
    user = user or current_user()
    user_id = int(user["id"])
    if user.get("role") in ("owner", "admin"):
        ent = {k: None for k in LIMITS} | {k: True for k in FEATURES}
        return {"plan": {"code": "staff", "name": "Staff (unrestricted)"}, "source": "staff", "entitlements": ent,
                "trial": trial_info(user_id), "subscription": None}
    sub = active_subscription(user_id)
    trial = trial_info(user_id)
    if sub:
        plan, source = get_plan(sub["plan_id"]), sub["source"]
    elif trial and trial["status"] == "active":
        plan, source = get_plan(trial["plan_id"]), "trial"
    else:
        plan, source = free_plan(), "free"
    ent = dict(plan["entitlements"])
    ov = overrides(user_id)
    ent.update({k: v for k, v in ov.items() if k in LIMITS or k in FEATURES})
    return {"plan": {k: plan[k] for k in ("id", "code", "name")}, "source": source, "entitlements": ent, "trial": trial,
            "subscription": sub, "overrides": ov}


def _period(kind: str, now: datetime | None = None) -> tuple[str, str]:
    now = now or _now()
    if kind == "day":
        return "d:" + now.date().isoformat(), (now + timedelta(days=1)).date().isoformat()
    nxt = (now.replace(day=28) + timedelta(days=4)).replace(day=1)
    return "m:" + now.strftime("%Y-%m"), nxt.date().isoformat()


def _count_now(key: str, user_id: int) -> int:
    if key == "saved_jobs":
        return db.query_one("SELECT COUNT(*) n FROM jobs WHERE user_id = ? AND decision = 'shortlisted'", (user_id,))["n"]
    if key == "applications_tracked":
        return db.query_one("SELECT COUNT(*) n FROM applications WHERE user_id = ?", (user_id,))["n"]
    if key == "resumes_count":
        return db.query_one("SELECT COUNT(*) n FROM resumes WHERE user_id = ?", (user_id,))["n"]
    if key == "storage_mb":
        r = db.query_one("SELECT COALESCE(SUM(size),0) s FROM uploads WHERE user_id = ?", (user_id,))
        return int(r["s"] / (1024 * 1024))
    return 0


def used(key: str, user_id: int) -> int:
    spec = LIMITS[key]
    if spec["period"] == "count":
        return _count_now(key, user_id)
    period, _ = _period(spec["period"])
    r = db.query_one("SELECT count FROM usage_counters WHERE user_id = ? AND key = ? AND period = ?", (user_id, spec["counter"], period))
    return r["count"] if r else 0


def usage_summary(user: dict | None = None) -> dict:
    user = user or current_user()
    eff = effective(user)
    out = []
    for key, spec in LIMITS.items():
        limit = eff["entitlements"].get(key)
        u = used(key, int(user["id"]))
        reset = _period(spec["period"])[1] if spec["period"] in ("day", "month") else None
        out.append({"key": key, "label": spec["label"], "used": u, "limit": limit,
                    "remaining": None if limit is None else max(0, limit - u), "resets_on": reset})
    feats = [{"key": k, "label": v, "enabled": bool(eff["entitlements"].get(k))} for k, v in FEATURES.items()]
    return {"plan": eff["plan"], "source": eff["source"], "limits": out, "features": feats, "trial": eff["trial"]}


def _upgrade_hint(eff: dict) -> str:
    return "Upgrade your plan to continue." if eff["source"] != "staff" else ""


def require_feature(key: str) -> None:
    user = current_user()
    if not user:
        raise FeatureLocked({"message": "Sign in required"})
    if key in ("career_roadmap", "automation", "outreach") and not flag({"automation": "discovery"}.get(key, key)):
        raise FeatureLocked({"code": "feature_disabled", "feature": key, "message": f"{FEATURES[key]} is currently disabled by the administrator."})
    eff = effective(user)
    if not eff["entitlements"].get(key):
        raise FeatureLocked({"code": "feature_locked", "feature": key, "plan": eff["plan"]["name"],
                             "message": f"{FEATURES[key]} isn't included in your {eff['plan']['name']} plan. {_upgrade_hint(eff)}"})


def check(key: str, amount: int = 1, user: dict | None = None) -> None:
    user = user or current_user()
    if not user:
        return
    eff = effective(user)
    limit = eff["entitlements"].get(key)
    if limit is None:
        return
    u = used(key, int(user["id"]))
    if u + amount > limit:
        spec = LIMITS[key]
        reset = _period(spec["period"])[1] if spec["period"] in ("day", "month") else None
        raise LimitReached({"code": "limit_reached", "key": key, "label": spec["label"], "limit": limit, "used": u,
                            "resets_on": reset, "plan": eff["plan"]["name"],
                            "message": f"You've reached your {spec['label'].lower()} limit ({u}/{limit}) on the {eff['plan']['name']} plan."
                                       + (f" It resets on {reset}." if reset else "") + f" {_upgrade_hint(eff)}"})


def consume(counter: str, amount: int = 1, user: dict | None = None) -> None:
    """Check every limit that uses this counter, then record the usage atomically."""
    user = user or current_user()
    if not user:
        return
    keys = [k for k, s in LIMITS.items() if s.get("counter") == counter]
    for k in keys:
        check(k, amount, user)
    with db.tx() as conn:
        for kind in {LIMITS[k]["period"] for k in keys} or {"month"}:
            period, _ = _period(kind)
            conn.execute("INSERT INTO usage_counters(user_id, key, period, count) VALUES (?,?,?,?) "
                         "ON CONFLICT(user_id, key, period) DO UPDATE SET count = count + excluded.count",
                         (int(user["id"]), counter, period, amount))


def set_override(user_id: int, key: str, value, reason: str, expires_at: str | None, actor_id: int) -> None:
    if key not in LIMITS and key not in FEATURES:
        raise ValueError("Unknown limit or feature")
    if key in LIMITS and value not in (None, "unlimited"):
        value = int(value)
        if value < 0:
            raise ValueError("Limit cannot be negative")
    elif key in LIMITS:
        value = None
    else:
        value = bool(value)
    db.execute("INSERT INTO user_overrides(user_id, key, value, reason, expires_at, created_by, created_at) VALUES (?,?,?,?,?,?,?) "
               "ON CONFLICT(user_id, key) DO UPDATE SET value=excluded.value, reason=excluded.reason, expires_at=excluded.expires_at, "
               "created_by=excluded.created_by, created_at=excluded.created_at",
               (user_id, key, db.jdumps(value), (reason or "")[:300], expires_at, actor_id, db.now_iso()))


def remove_override(user_id: int, key: str) -> None:
    db.execute("DELETE FROM user_overrides WHERE user_id = ? AND key = ?", (user_id, key))


# ------------------------------------------------------------------ coupons (validated; applied only once payments are enabled)
def validate_coupon(code: str, plan_code: str) -> dict:
    c = db.query_one("SELECT * FROM coupons WHERE code = ? COLLATE NOCASE", (code.strip(),))
    now = _now().isoformat()
    if not c or not c["active"]:
        raise ValueError("Coupon not found or inactive")
    if c["valid_from"] and c["valid_from"] > now:
        raise ValueError("Coupon is not active yet")
    if c["valid_until"] and c["valid_until"] < now:
        raise ValueError("Coupon has expired")
    if c["max_redemptions"] is not None and c["redeemed_count"] >= c["max_redemptions"]:
        raise ValueError("Coupon has reached its redemption limit")
    plans = db.jloads(c["plan_codes"], [])
    if plans and plan_code not in plans:
        raise ValueError("Coupon does not apply to this plan")
    return c


def uid_safe() -> int:
    return uid()
