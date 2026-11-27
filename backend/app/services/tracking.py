"""Privacy-conscious product analytics and the admin audit log.

Never record resume text, application answers, messages, passwords, tokens,
keystrokes or clipboard data. Metadata is restricted to an allow-list of short,
non-sensitive fields. The admin-configurable analytics level controls detail:
standard (events + allow-listed metadata + session), minimal (event name only),
off (nothing except security/billing-critical events).
"""
from __future__ import annotations

from .. import db
from ..context import current_user, session_id

ALLOWED_META = {"source", "status", "from_status", "to_status", "score", "plan", "count", "format", "view", "kind", "method",
                "duplicate", "reason_code", "days", "ats_score", "feature", "result"}
CRITICAL = {"registration", "login", "logout", "subscription_changed", "trial_started", "trial_expired", "account_deleted",
            "password_reset", "limit_reached"}
EVENTS = {
    "registration", "login", "logout", "profile_setup_completed", "subscription_changed", "trial_started", "trial_expired",
    "feature_used", "job_search", "job_discovered", "job_viewed", "job_shortlisted", "resume_uploaded", "resume_analyzed",
    "resume_tailored", "resume_downloaded", "application_prepared", "application_status_updated", "interview_reported",
    "offer_reported", "error", "operation_failed", "limit_reached", "account_deleted", "password_reset", "roadmap_viewed",
    "skill_progress_updated", "support_ticket", "data_exported",
}


def _level() -> str:
    try:
        from .entitlements import system
        return system()["analytics_level"]
    except Exception:
        return "standard"


def event(name: str, *, feature: str | None = None, success: bool = True, error_category: str | None = None,
          actor: str = "user", meta: dict | None = None, user_id: int | None = None) -> None:
    if name not in EVENTS:
        name = "feature_used"
    u = current_user()
    user_id = user_id if user_id is not None else (int(u["id"]) if u else None)
    level = _level()
    if level == "off" and name not in CRITICAL:
        return
    if u and u.get("analytics_opt_out") and name not in CRITICAL:
        return
    clean = None
    sid = None
    if level == "standard":
        clean = {k: (str(v)[:40] if isinstance(v, str) else v) for k, v in (meta or {}).items()
                 if k in ALLOWED_META and isinstance(v, (str, int, float, bool))} or None
        sid = (session_id() or "")[:12] or None
    try:
        db.execute("INSERT INTO activity_events(user_id, session_id, event, feature, actor, success, error_category, metadata, at) "
                   "VALUES (?,?,?,?,?,?,?,?,?)",
                   (user_id, sid, name, feature, actor if actor in ("user", "system") else "user", 1 if success else 0,
                    (error_category or None) and error_category[:40], db.jdumps(clean) if clean else None, db.now_iso()))
    except Exception:
        pass  # analytics must never break a user action


def admin_action(action: str, *, target_user_id: int | None = None, details: dict | None = None, ip_hash: str | None = None) -> None:
    from ..audit import scrub
    u = current_user()
    db.execute("INSERT INTO admin_audit(actor_id, action, target_user_id, details, ip_hash, at) VALUES (?,?,?,?,?,?)",
               (int(u["id"]) if u else None, action, target_user_id, scrub(db.jdumps(details)) if details else None, ip_hash,
                db.now_iso()))
