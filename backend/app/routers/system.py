"""User-scoped dashboard, approvals, analytics, activity, notifications, tasks, search,
personal settings and personal data export."""
from __future__ import annotations

import io
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import audit, db, tasks
from ..config import settings
from ..context import current_user, uid
from ..files import user_path
from ..services import ai, discovery, entitlements, pipeline, profile_service as ps, tracking

router = APIRouter(prefix="/api", tags=["system"])


def _system_health() -> dict:
    last = db.get_user_setting("last_discovery", None)
    failures = db.query_one("SELECT COUNT(*) n FROM audit_events WHERE user_id = ? AND level = 'error' AND at >= datetime('now','-1 day')",
                            (uid(),))["n"]
    from ..services.browser_prep import playwright_available
    pw, pw_msg = playwright_available()
    return {"database": "ok", "last_discovery": last, "errors_24h": failures, "ai": ai.status(),
            "scheduler": discovery.get_schedule(), "browser_automation": {"available": pw and entitlements.flag("browser_prep"), "message": pw_msg},
            "storage_note": None if settings.persistent_storage or not settings.is_production else
            "Hosted storage is not yet persistent: export your data regularly."}


@router.get("/dashboard")
def dashboard():
    u = uid()
    prof = ps.active_profile()
    draft = ps.latest_draft()
    c = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM jobs WHERE user_id = ? GROUP BY status", (u,))}
    rec = db.query("SELECT id, title, company, location, work_mode, match_score, status, posted_at, source, salary_text, verified_at, "
                   "employment_type, alt_urls, page_status, extraction_complete FROM jobs WHERE user_id = ? AND status IN ('qualified','shortlisted') "
                   "ORDER BY match_score DESC LIMIT 6", (u,))
    for r in rec:
        r["status_label"] = pipeline.STATUS_LABELS[r["status"]]
        r["alt_urls"] = db.jloads(r["alt_urls"], [])
    docs_review = db.query("SELECT r.id, r.version, r.ats_score, r.created_at, j.title, j.company FROM resumes r JOIN jobs j ON j.id = r.job_id "
                           "WHERE r.user_id = ? AND r.status = 'draft' ORDER BY r.created_at DESC LIMIT 5", (u,))
    apps = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM applications WHERE user_id = ? GROUP BY status", (u,))}
    recent = db.query("SELECT id, at, category, action, level, message, entity_type, entity_id FROM audit_events WHERE user_id = ? "
                      "ORDER BY id DESC LIMIT 8", (u,))
    usage = entitlements.usage_summary()
    alerts = []
    t = usage["trial"]
    if t and t["status"] == "active" and t["days_remaining"] <= entitlements.system()["trial_reminder_days"]:
        alerts.append({"level": "warning", "text": f"Your free trial ends in {t['days_remaining']} day(s). You'll move to the Free plan — no charge, data kept.",
                       "link": "/app/subscription"})
    for lim in usage["limits"]:
        if lim["limit"] and lim["used"] >= lim["limit"]:
            alerts.append({"level": "warning", "text": f"{lim['label']} limit reached ({lim['used']}/{lim['limit']}).", "link": "/app/usage"})
    return {
        "profile": {"verified": bool(prof), "version": prof["version"] if prof else None, "draft_pending": bool(draft),
                    "name": (((prof or {}).get("data", {}).get("basics", {}).get("name") or {}).get("value") if prof else None)
                    or (current_user() or {}).get("name")},
        "sections": {"recommended": c.get("qualified", 0) + c.get("shortlisted", 0), "shortlisted": c.get("shortlisted", 0),
                     "near_match": c.get("near_match", 0), "rejected": c.get("not_eligible", 0) + c.get("below_threshold", 0) + c.get("dismissed", 0),
                     "expired": c.get("expired", 0), "pending_verification": c.get("needs_review", 0) + c.get("new", 0),
                     "documents_awaiting_review": db.query_one("SELECT COUNT(*) n FROM resumes WHERE user_id = ? AND status = 'draft'", (u,))["n"],
                     "user_submitted": apps.get("applied", 0) + apps.get("interview", 0) + apps.get("offer", 0)},
        "recommended": rec, "documents_review": docs_review, "applications": apps, "recent_activity": recent,
        "pending_approvals": len(_approvals()), "usage": ai.usage_summary(), "plan_usage": usage, "alerts": alerts,
        "health": _system_health(), "threshold": ps.get_preferences()["match_threshold"],
    }


def _approvals() -> list[dict]:
    u = uid()
    items = []
    for j in db.query("SELECT id, title, company, match_score, created_at FROM jobs WHERE user_id = ? AND status = 'qualified' AND decision IS NULL "
                      "ORDER BY match_score DESC", (u,)):
        items.append({"type": "job_shortlist", "id": j["id"], "title": f"{j['title']} — {j['company']}", "created_at": j["created_at"],
                      "what": f"Shortlist this qualifying job (match {j['match_score']}%)",
                      "next": "Shortlisting lets you generate a truthful tailored resume. Nothing is sent or submitted.", "link": f"/app/jobs/{j['id']}"})
    for j in db.query("SELECT id, title, company, match_score, decision_reason, created_at FROM jobs WHERE user_id = ? AND status = 'needs_review' "
                      "ORDER BY match_score DESC", (u,)):
        items.append({"type": "job_verification", "id": j["id"], "title": f"{j['title']} — {j['company']}", "created_at": j["created_at"],
                      "what": j["decision_reason"] or "Requirements need your confirmation",
                      "next": "Confirm only requirements you genuinely meet; the job is then re-evaluated against the threshold.", "link": f"/app/jobs/{j['id']}"})
    for r in db.query("SELECT r.id, r.version, r.ats_score, r.consistency, r.created_at, j.title, j.company FROM resumes r JOIN jobs j ON j.id = r.job_id "
                      "WHERE r.user_id = ? AND r.status = 'draft' ORDER BY r.created_at DESC", (u,)):
        ok = db.jloads(r["consistency"], {}).get("ok")
        items.append({"type": "resume_approval", "id": r["id"], "title": f"Resume v{r['version']} for {r['title']} — {r['company']}",
                      "created_at": r["created_at"],
                      "what": f"Approve tailored resume (estimated ATS {r['ats_score']}/100{', truthfulness check passed' if ok else ', HAS UNSUPPORTED CLAIMS'})",
                      "next": "Approved resumes can be used to prepare an application. Approval never submits anything.", "link": f"/app/resumes/{r['id']}"})
    for a in db.query("SELECT a.id, a.created_at, j.title, j.company FROM applications a JOIN jobs j ON j.id = a.job_id "
                      "WHERE a.user_id = ? AND a.status = 'prepared' AND a.reviewed = 0", (u,)):
        items.append({"type": "application_review", "id": a["id"], "title": f"Application: {a['title']} — {a['company']}",
                      "created_at": a["created_at"], "what": "Review the job, selected resume and prepared answers",
                      "next": "After review you can open the application page (prefill only). You submit personally.", "link": f"/app/applications?open={a['id']}"})
    for d in db.query("SELECT d.id, d.kind, d.created_at, c.name, j.company FROM outreach_drafts d LEFT JOIN contacts c ON c.id = d.contact_id "
                      "LEFT JOIN jobs j ON j.id = d.job_id WHERE d.user_id = ? AND d.status = 'draft'", (u,)):
        items.append({"type": "outreach_approval", "id": d["id"], "title": f"{d['kind'].replace('_', ' ').title()} draft"
                      + (f" to {d['name']}" if d["name"] else "") + (f" ({d['company']})" if d["company"] else ""),
                      "created_at": d["created_at"], "what": "Edit and approve this message draft",
                      "next": "Approval does not send it. You copy and send it yourself, then mark it as sent.", "link": f"/app/contacts?draft={d['id']}"})
    return items


@router.get("/approvals")
def approvals():
    items = _approvals()
    counts = {}
    for i in items:
        counts[i["type"]] = counts.get(i["type"], 0) + 1
    return {"items": items, "counts": counts}


@router.get("/analytics")
def analytics():
    u = uid()
    advanced = bool(entitlements.effective()["entitlements"].get("advanced_analytics"))
    total = db.query_one("SELECT COUNT(*) n FROM jobs WHERE user_id = ?", (u,))["n"]
    by_status = db.query("SELECT status, COUNT(*) n FROM jobs WHERE user_id = ? GROUP BY status", (u,))
    for r in by_status:
        r["label"] = pipeline.STATUS_LABELS.get(r["status"], r["status"])
    apps = db.query("SELECT status, COUNT(*) n FROM applications WHERE user_id = ? GROUP BY status", (u,))
    applied = db.query_one("SELECT COUNT(*) n FROM application_history h JOIN applications a ON a.id = h.application_id "
                           "WHERE a.user_id = ? AND h.to_status = 'applied'", (u,))["n"]
    interviews = db.query_one("SELECT COUNT(DISTINCT h.application_id) n FROM application_history h JOIN applications a ON a.id = h.application_id "
                              "WHERE a.user_id = ? AND h.to_status = 'interview'", (u,))["n"]
    per_day = db.query("SELECT substr(created_at,1,10) day, COUNT(*) discovered, SUM(CASE WHEN status IN ('qualified','shortlisted') THEN 1 ELSE 0 END) "
                       "qualifying FROM jobs WHERE user_id = ? AND created_at >= datetime('now','-30 days') GROUP BY day ORDER BY day", (u,))
    out = {"jobs_total": total, "by_status": by_status, "applications": apps, "applied": applied, "interviews": interviews,
           "interview_rate": round(100 * interviews / applied, 1) if applied >= 3 else None, "per_day": per_day,
           "usage": ai.usage_summary(), "advanced": advanced}
    if advanced:
        gate_fails = {}
        for r in db.query("SELECT eligibility FROM jobs WHERE user_id = ? AND eligibility_result = 'fail'", (u,)):
            for g in db.jloads(r["eligibility"], {}).get("gates", []):
                if g["result"] == "fail":
                    gate_fails[g["label"]] = gate_fails.get(g["label"], 0) + 1
        out.update(
            by_source=db.query("SELECT source, COUNT(*) total, SUM(CASE WHEN status IN ('qualified','shortlisted') THEN 1 ELSE 0 END) qualifying, "
                               "ROUND(AVG(match_score),1) avg_match FROM jobs WHERE user_id = ? GROUP BY source ORDER BY total DESC", (u,)),
            match_distribution=db.query("SELECT CAST(match_score / 10 AS INT) * 10 AS bucket, COUNT(*) n FROM jobs WHERE user_id = ? "
                                        "AND match_score IS NOT NULL GROUP BY bucket ORDER BY bucket", (u,)),
            gate_failures=sorted(({"label": k, "n": v} for k, v in gate_fails.items()), key=lambda x: -x["n"]),
            rejection_reasons=db.query("SELECT COALESCE(rejection_reason, 'Not recorded') reason, COUNT(*) n FROM applications "
                                       "WHERE user_id = ? AND status = 'rejected' GROUP BY reason", (u,)),
            ai_cost=db.query("SELECT substr(at,1,10) day, ROUND(SUM(cost_inr),4) cost, COUNT(*) calls, SUM(cached) cached FROM ai_usage "
                             "WHERE user_id = ? AND at >= datetime('now','-30 days') GROUP BY day ORDER BY day", (u,)))
    return out


@router.get("/activity")
def activity(q: str = "", category: str = "", level: str = "", entity_id: str = "", page: int = 1, page_size: int = 50):
    where, params = ["user_id = ?"], [uid()]
    if q:
        where.append("(message LIKE ? OR action LIKE ? OR source_ref LIKE ?)")
        params += [f"%{q}%"] * 3
    for col, val in (("category", category), ("level", level), ("entity_id", entity_id)):
        if val:
            where.append(f"{col} = ?")
            params.append(val)
    w = " AND ".join(where)
    page_size = max(1, min(200, page_size))
    total = db.query_one(f"SELECT COUNT(*) n FROM audit_events WHERE {w}", params)["n"]
    rows = db.query(f"SELECT * FROM audit_events WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?", params + [page_size, (max(1, page) - 1) * page_size])
    for r in rows:
        r["details"] = db.jloads(r["details"], None)
    return {"items": rows, "total": total, "page": page, "page_size": page_size}


@router.get("/notifications")
def notifications():
    rows = db.query("SELECT * FROM notifications WHERE user_id = ? ORDER BY id DESC LIMIT 30", (uid(),))
    unread = db.query_one("SELECT COUNT(*) n FROM notifications WHERE user_id = ? AND read = 0", (uid(),))["n"]
    return {"items": rows, "unread": unread}


@router.post("/notifications/read")
def mark_read():
    db.execute("UPDATE notifications SET read = 1 WHERE user_id = ? AND read = 0", (uid(),))
    return {"ok": True}


@router.get("/tasks/{tid}")
def task(tid: str):
    t = tasks.get(tid, uid())
    if not t:
        raise HTTPException(404, "Task not found")
    return t


@router.post("/tasks/{tid}/cancel")
def cancel(tid: str):
    if not tasks.get(tid, uid()):
        raise HTTPException(404, "Task not found")
    return tasks.cancel(tid)


@router.get("/search")
def search(q: str):
    q = q.strip()
    if len(q) < 2:
        return {"jobs": [], "applications": [], "contacts": []}
    like, u = f"%{q}%", uid()
    jobs = db.query("SELECT id, title, company, status FROM jobs WHERE user_id = ? AND (title LIKE ? OR company LIKE ?) ORDER BY updated_at DESC LIMIT 8",
                    (u, like, like))
    for j in jobs:
        j["status_label"] = pipeline.STATUS_LABELS.get(j["status"], j["status"])
    apps = db.query("SELECT a.id, j.title, j.company, a.status FROM applications a JOIN jobs j ON j.id = a.job_id "
                    "WHERE a.user_id = ? AND (j.title LIKE ? OR j.company LIKE ?) LIMIT 5", (u, like, like))
    contacts = db.query("SELECT id, name, company, role FROM contacts WHERE user_id = ? AND (name LIKE ? OR company LIKE ?) LIMIT 5", (u, like, like))
    return {"jobs": jobs, "applications": apps, "contacts": contacts}


# ---------------------------------------------------------------- personal settings
class AISettings(BaseModel):
    enabled: bool | None = None
    consent_profile_data: bool | None = None
    daily_cap_inr: float | None = None
    monthly_cap_inr: float | None = None
    daily_call_limit: int | None = None
    resume: bool | None = None


class Schedule(BaseModel):
    enabled: bool
    hour: int = Field(ge=0, le=23)


class NotifSettings(BaseModel):
    in_app: bool = True
    toasts: bool = True


@router.get("/settings")
def get_settings():
    return {"ai": ai.get_ai_settings(), "ai_status": ai.status(), "usage": ai.usage_summary(), "schedule": discovery.get_schedule(),
            "notifications": db.get_user_setting("notifications", {"in_app": True, "toasts": True}),
            "providers": {"gemini": {"env": "GEMINI_API_KEY", "configured": bool(settings.gemini_api_key)},
                          "groq": {"env": "GROQ_API_KEY", "configured": bool(settings.groq_api_key)},
                          "openai_compatible": {"env": "AI_API_KEY + AI_BASE_URL", "configured": bool(settings.openai_compat_key and settings.openai_compat_base)}},
            "storage": {"data_dir": "your private account storage", "environment": settings.env,
                        "files": ["cv.md", "profile.yml", "portals.yml", "jobs/<id>/report.md", "jobs/<id>/job.json", "resumes/", "reports/"]}}


@router.put("/settings/ai")
def put_ai(body: AISettings):
    return ai.save_ai_settings(body.model_dump(exclude_none=True))


@router.post("/settings/ai/test")
def test_ai():
    return ai.test_connection()


@router.put("/settings/schedule")
def put_schedule(body: Schedule):
    if body.enabled:
        entitlements.require_feature("automation")
    db.set_user_setting("schedule", body.model_dump())
    audit.log("settings", "schedule", f"Daily discovery {'enabled' if body.enabled else 'disabled'} at {body.hour:02d}:00")
    return discovery.get_schedule()


@router.put("/settings/notifications")
def put_notifs(body: NotifSettings):
    db.set_user_setting("notifications", body.model_dump())
    return body.model_dump()


@router.get("/data/export")
def export():
    """Everything belonging to the signed-in user: JSON records + their private files."""
    u = uid()
    buf = io.BytesIO()
    tables = {"profile_versions": "user_id", "uploads": "user_id", "jobs": "user_id", "resumes": "user_id", "applications": "user_id",
              "contacts": "user_id", "outreach_drafts": "user_id", "companies": "user_id", "notifications": "user_id",
              "audit_events": "user_id", "user_settings": "user_id", "skill_progress": "user_id", "subscriptions": "user_id",
              "activity_events": "user_id"}
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        me = db.query_one("SELECT id, email, name, role, created_at, last_login_at, profile_completed_at FROM users WHERE id = ?", (u,))
        z.writestr("account.json", db.jdumps(me))
        for t, col in tables.items():
            z.writestr(f"records/{t}.json", db.jdumps(db.query(f"SELECT * FROM {t} WHERE {col} = ?", (u,))))
        z.writestr("records/application_history.json", db.jdumps(db.query(
            "SELECT h.* FROM application_history h JOIN applications a ON a.id = h.application_id WHERE a.user_id = ?", (u,))))
        root = user_path()
        if root.exists():
            for p in root.rglob("*"):
                if p.is_file():
                    z.write(p, "files/" + p.relative_to(root).as_posix())
    buf.seek(0)
    tracking.event("data_exported", feature="privacy")
    audit.log("system", "export", "You exported your data")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="jobpilot-export-{stamp}.zip"'})
