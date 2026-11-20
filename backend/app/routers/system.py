from __future__ import annotations

import io
import shutil
import zipfile
from datetime import datetime, timezone

from fastapi import APIRouter, HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from .. import audit, db, security, tasks
from ..config import settings
from ..files import data_path
from ..services import ai, discovery, pipeline, profile_service as ps

router = APIRouter(prefix="/api", tags=["system"])


class Login(BaseModel):
    password: str = Field(max_length=200)


@router.post("/auth/login")
def login(body: Login, request: Request, response: Response):
    ip = request.client.host if request.client else "?"
    if security.rate_limited(ip):
        raise HTTPException(429, "Too many attempts. Try again in 15 minutes.")
    if not settings.password:
        raise HTTPException(503, "Access password is not configured on the server (JOBPILOT_PASSWORD).")
    if not security.check_password(body.password):
        security.record_attempt(ip)
        audit.log("system", "login_failed", "Failed sign-in attempt", level="warning")
        raise HTTPException(401, "Incorrect password")
    response.set_cookie(security.COOKIE, security.issue_token(), max_age=security.MAX_AGE, httponly=True,
                        secure=settings.cookie_secure, samesite="lax", path="/")
    audit.log("system", "login", "Signed in")
    return {"ok": True}


@router.post("/auth/logout")
def logout(response: Response):
    response.delete_cookie(security.COOKIE, path="/")
    return {"ok": True}


@router.get("/session")
def session(request: Request):
    return {"authenticated": security.is_authenticated(request), "auth_required": settings.auth_required,
            "password_configured": bool(settings.password), "environment": settings.env}


@router.get("/health")
def health():
    try:
        db.query_one("SELECT 1 AS ok")
        ok = True
    except Exception:
        ok = False
    return {"status": "ok" if ok else "degraded", "database": ok, "time": db.now_iso()}


def _system_health() -> dict:
    last = db.get_setting("last_discovery", None)
    failures = db.query_one("SELECT COUNT(*) n FROM audit_events WHERE level = 'error' AND at >= datetime('now','-1 day')")["n"]
    st = ai.status()
    from ..services.browser_prep import playwright_available
    pw, pw_msg = playwright_available()
    return {"database": "ok", "last_discovery": last, "errors_24h": failures, "ai": st,
            "scheduler": discovery.get_schedule(), "browser_automation": {"available": pw, "message": pw_msg},
            "storage_note": "Ephemeral storage: data resets when the hosted service restarts or redeploys. Export regularly." if settings.is_production and not settings.persistent_storage else None}


@router.get("/dashboard")
def dashboard():
    prof = ps.active_profile()
    draft = ps.latest_draft()
    c = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM jobs GROUP BY status")}
    rec = db.query("SELECT id, title, company, location, work_mode, match_score, status, posted_at, source, salary_text, verified_at, "
                   "employment_type FROM jobs WHERE status IN ('qualified','shortlisted') ORDER BY match_score DESC LIMIT 6")
    for r in rec:
        r["status_label"] = pipeline.STATUS_LABELS[r["status"]]
    docs_review = db.query("SELECT r.id, r.version, r.ats_score, r.created_at, j.title, j.company FROM resumes r JOIN jobs j ON j.id = r.job_id "
                           "WHERE r.status = 'draft' ORDER BY r.created_at DESC LIMIT 5")
    apps = {r["status"]: r["n"] for r in db.query("SELECT status, COUNT(*) n FROM applications GROUP BY status")}
    recent = db.query("SELECT id, at, category, action, level, message, entity_type, entity_id FROM audit_events ORDER BY id DESC LIMIT 8")
    return {
        "profile": {"verified": bool(prof), "version": prof["version"] if prof else None, "draft_pending": bool(draft),
                    "name": ((prof or {}).get("data", {}).get("basics", {}).get("name") or {}).get("value") if prof else None},
        "sections": {"recommended": c.get("qualified", 0) + c.get("shortlisted", 0), "shortlisted": c.get("shortlisted", 0),
                     "near_match": c.get("near_match", 0), "rejected": c.get("not_eligible", 0) + c.get("below_threshold", 0) + c.get("dismissed", 0),
                     "expired": c.get("expired", 0), "pending_verification": c.get("needs_review", 0) + c.get("new", 0),
                     "documents_awaiting_review": db.query_one("SELECT COUNT(*) n FROM resumes WHERE status = 'draft'")["n"],
                     "user_submitted": apps.get("applied", 0) + apps.get("interview", 0) + apps.get("offer", 0)},
        "recommended": rec, "documents_review": docs_review, "applications": apps, "recent_activity": recent,
        "pending_approvals": len(_approvals()), "usage": ai.usage_summary(), "health": _system_health(),
        "threshold": ps.get_preferences()["match_threshold"],
    }


def _approvals() -> list[dict]:
    items = []
    for j in db.query("SELECT id, title, company, match_score, created_at FROM jobs WHERE status = 'qualified' AND decision IS NULL ORDER BY match_score DESC"):
        items.append({"type": "job_shortlist", "id": j["id"], "title": f"{j['title']} — {j['company']}", "created_at": j["created_at"],
                      "what": f"Shortlist this qualifying job (match {j['match_score']}%)",
                      "next": "Shortlisting lets you generate a truthful tailored resume. Nothing is sent or submitted.",
                      "link": f"/jobs/{j['id']}"})
    for j in db.query("SELECT id, title, company, match_score, decision_reason, created_at FROM jobs WHERE status = 'needs_review' ORDER BY match_score DESC"):
        items.append({"type": "job_verification", "id": j["id"], "title": f"{j['title']} — {j['company']}", "created_at": j["created_at"],
                      "what": j["decision_reason"] or "Requirements need your confirmation",
                      "next": "Confirm only requirements you genuinely meet; the job is then re-evaluated against the threshold.",
                      "link": f"/jobs/{j['id']}"})
    for r in db.query("SELECT r.id, r.version, r.ats_score, r.consistency, r.created_at, j.title, j.company FROM resumes r JOIN jobs j ON j.id = r.job_id "
                      "WHERE r.status = 'draft' ORDER BY r.created_at DESC"):
        ok = db.jloads(r["consistency"], {}).get("ok")
        items.append({"type": "resume_approval", "id": r["id"], "title": f"Resume v{r['version']} for {r['title']} — {r['company']}",
                      "created_at": r["created_at"],
                      "what": f"Approve tailored resume (estimated ATS {r['ats_score']}/100{', truthfulness check passed' if ok else ', HAS UNSUPPORTED CLAIMS'})",
                      "next": "Approved resumes can be used to prepare an application. Approval never submits anything.",
                      "link": f"/resumes/{r['id']}"})
    for a in db.query("SELECT a.id, a.created_at, j.title, j.company FROM applications a JOIN jobs j ON j.id = a.job_id "
                      "WHERE a.status = 'prepared' AND a.reviewed = 0"):
        items.append({"type": "application_review", "id": a["id"], "title": f"Application: {a['title']} — {a['company']}",
                      "created_at": a["created_at"], "what": "Review the job, selected resume and prepared answers",
                      "next": "After review you can open the application page (prefill only). You submit personally.",
                      "link": f"/applications?open={a['id']}"})
    for d in db.query("SELECT d.id, d.kind, d.created_at, c.name, j.company FROM outreach_drafts d LEFT JOIN contacts c ON c.id = d.contact_id "
                      "LEFT JOIN jobs j ON j.id = d.job_id WHERE d.status = 'draft'"):
        items.append({"type": "outreach_approval", "id": d["id"], "title": f"{d['kind'].replace('_', ' ').title()} draft"
                      + (f" to {d['name']}" if d["name"] else "") + (f" ({d['company']})" if d["company"] else ""),
                      "created_at": d["created_at"], "what": "Edit and approve this message draft",
                      "next": "Approval does not send it. You copy and send it yourself, then mark it as sent.",
                      "link": f"/contacts?draft={d['id']}"})
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
    total = db.query_one("SELECT COUNT(*) n FROM jobs")["n"]
    by_status = db.query("SELECT status, COUNT(*) n FROM jobs GROUP BY status")
    for r in by_status:
        r["label"] = pipeline.STATUS_LABELS.get(r["status"], r["status"])
    by_source = db.query("SELECT source, COUNT(*) total, SUM(CASE WHEN status IN ('qualified','shortlisted') THEN 1 ELSE 0 END) qualifying, "
                         "ROUND(AVG(match_score),1) avg_match FROM jobs GROUP BY source ORDER BY total DESC")
    buckets = db.query("SELECT CAST(match_score / 10 AS INT) * 10 AS bucket, COUNT(*) n FROM jobs WHERE match_score IS NOT NULL GROUP BY bucket ORDER BY bucket")
    apps = db.query("SELECT status, COUNT(*) n FROM applications GROUP BY status")
    applied = db.query_one("SELECT COUNT(*) n FROM application_history WHERE to_status = 'applied'")["n"]
    interviews = db.query_one("SELECT COUNT(DISTINCT application_id) n FROM application_history WHERE to_status = 'interview'")["n"]
    gate_fails = {}
    for r in db.query("SELECT eligibility FROM jobs WHERE eligibility_result = 'fail'"):
        for g in db.jloads(r["eligibility"], {}).get("gates", []):
            if g["result"] == "fail":
                gate_fails[g["label"]] = gate_fails.get(g["label"], 0) + 1
    rejection = db.query("SELECT COALESCE(rejection_reason, 'Not recorded') reason, COUNT(*) n FROM applications WHERE status = 'rejected' GROUP BY reason")
    per_day = db.query("SELECT substr(created_at,1,10) day, COUNT(*) discovered, SUM(CASE WHEN status IN ('qualified','shortlisted') THEN 1 ELSE 0 END) qualifying "
                       "FROM jobs WHERE created_at >= datetime('now','-30 days') GROUP BY day ORDER BY day")
    cost = db.query("SELECT substr(at,1,10) day, ROUND(SUM(cost_inr),4) cost, COUNT(*) calls, SUM(cached) cached FROM ai_usage "
                    "WHERE at >= datetime('now','-30 days') GROUP BY day ORDER BY day")
    return {"jobs_total": total, "by_status": by_status, "by_source": by_source, "match_distribution": buckets,
            "applications": apps, "applied": applied, "interviews": interviews,
            "interview_rate": round(100 * interviews / applied, 1) if applied >= 3 else None,
            "gate_failures": sorted(({"label": k, "n": v} for k, v in gate_fails.items()), key=lambda x: -x["n"]),
            "rejection_reasons": rejection, "per_day": per_day, "ai_cost": cost, "usage": ai.usage_summary()}


@router.get("/activity")
def activity(q: str = "", category: str = "", level: str = "", entity_id: str = "", page: int = 1, page_size: int = 50):
    where, params = ["1=1"], []
    if q:
        where.append("(message LIKE ? OR action LIKE ? OR source_ref LIKE ?)")
        params += [f"%{q}%"] * 3
    if category:
        where.append("category = ?")
        params.append(category)
    if level:
        where.append("level = ?")
        params.append(level)
    if entity_id:
        where.append("entity_id = ?")
        params.append(entity_id)
    w = " AND ".join(where)
    page_size = max(1, min(200, page_size))
    total = db.query_one(f"SELECT COUNT(*) n FROM audit_events WHERE {w}", params)["n"]
    rows = db.query(f"SELECT * FROM audit_events WHERE {w} ORDER BY id DESC LIMIT ? OFFSET ?", params + [page_size, (max(1, page) - 1) * page_size])
    for r in rows:
        r["details"] = db.jloads(r["details"], None)
    cats = [r["category"] for r in db.query("SELECT DISTINCT category FROM audit_events ORDER BY category")]
    return {"items": rows, "total": total, "page": page, "page_size": page_size, "categories": cats}


@router.get("/notifications")
def notifications():
    rows = db.query("SELECT * FROM notifications ORDER BY id DESC LIMIT 30")
    unread = db.query_one("SELECT COUNT(*) n FROM notifications WHERE read = 0")["n"]
    return {"items": rows, "unread": unread}


@router.post("/notifications/read")
def mark_read():
    db.execute("UPDATE notifications SET read = 1 WHERE read = 0")
    return {"ok": True}


@router.get("/tasks/{tid}")
def task(tid: str):
    t = tasks.get(tid)
    if not t:
        raise HTTPException(404, "Task not found")
    return t


@router.post("/tasks/{tid}/cancel")
def cancel(tid: str):
    t = tasks.cancel(tid)
    if not t:
        raise HTTPException(404, "Task not found")
    return t


@router.get("/search")
def search(q: str):
    q = q.strip()
    if len(q) < 2:
        return {"jobs": [], "applications": [], "contacts": []}
    like = f"%{q}%"
    jobs = db.query("SELECT id, title, company, status FROM jobs WHERE title LIKE ? OR company LIKE ? ORDER BY updated_at DESC LIMIT 8", (like, like))
    for j in jobs:
        j["status_label"] = pipeline.STATUS_LABELS.get(j["status"], j["status"])
    apps = db.query("SELECT a.id, j.title, j.company, a.status FROM applications a JOIN jobs j ON j.id = a.job_id "
                    "WHERE j.title LIKE ? OR j.company LIKE ? LIMIT 5", (like, like))
    contacts = db.query("SELECT id, name, company, role FROM contacts WHERE name LIKE ? OR company LIKE ? LIMIT 5", (like, like))
    return {"jobs": jobs, "applications": apps, "contacts": contacts}


# ---------------------------------------------------------------- settings
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
            "notifications": db.get_setting("notifications", {"in_app": True, "toasts": True}),
            "providers": {"gemini": {"env": "GEMINI_API_KEY", "configured": bool(settings.gemini_api_key)},
                          "groq": {"env": "GROQ_API_KEY", "configured": bool(settings.groq_api_key)},
                          "openai_compatible": {"env": "AI_API_KEY + AI_BASE_URL", "configured": bool(settings.openai_compat_key and settings.openai_compat_base)}},
            "storage": {"data_dir": str(settings.data_dir) if not settings.is_production else "server data directory",
                        "environment": settings.env, "files": ["cv.md", "profile.yml", "portals.yml", "jobs/<id>/report.md",
                                                               "jobs/<id>/job.json", "resumes/", "reports/", "audit/*.jsonl"]}}


@router.put("/settings/ai")
def put_ai(body: AISettings):
    return ai.save_ai_settings(body.model_dump(exclude_none=True))


@router.post("/settings/ai/test")
def test_ai():
    return ai.test_connection()


@router.put("/settings/schedule")
def put_schedule(body: Schedule):
    db.set_setting("schedule", body.model_dump())
    audit.log("settings", "schedule", f"Daily discovery {'enabled' if body.enabled else 'disabled'} at {body.hour:02d}:00")
    return discovery.get_schedule()


@router.put("/settings/notifications")
def put_notifs(body: NotifSettings):
    db.set_setting("notifications", body.model_dump())
    return body.model_dump()


@router.get("/data/export")
def export():
    buf = io.BytesIO()
    root = data_path()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for p in root.rglob("*"):
            rel = p.relative_to(root).as_posix()
            if p.is_dir() or rel.startswith(("browser-profile", "tmp")) or rel.endswith(("-wal", "-shm")):
                continue
            if rel == "jobpilot.db":
                # consistent snapshot via SQLite backup API
                import sqlite3
                snap = sqlite3.connect(":memory:")
                db.get_conn().backup(snap)
                tmp = data_path("tmp", "export.db")
                disk = sqlite3.connect(tmp)
                snap.backup(disk)
                disk.close()
                snap.close()
                z.write(tmp, "jobpilot.db")
                tmp.unlink(missing_ok=True)
                continue
            z.write(p, rel)
    buf.seek(0)
    audit.log("system", "export", "User exported all data")
    stamp = datetime.now(timezone.utc).strftime("%Y%m%d-%H%M")
    return StreamingResponse(buf, media_type="application/zip", headers={"Content-Disposition": f'attachment; filename="jobpilot-export-{stamp}.zip"'})


class DeleteAll(BaseModel):
    confirm: str


@router.post("/data/delete-all")
def delete_all(body: DeleteAll):
    if body.confirm != "DELETE ALL MY DATA":
        raise HTTPException(422, "Type DELETE ALL MY DATA exactly to confirm")
    with db.tx() as conn:
        for t in ("outreach_drafts", "contacts", "application_history", "applications", "resumes", "jobs", "profile_versions", "uploads",
                  "notifications", "tasks", "discovery_runs", "ai_cache", "ai_usage", "companies", "settings", "audit_events"):
            conn.execute(f"DELETE FROM {t}")
    root = data_path()
    for sub in ("uploads", "jobs", "resumes", "reports", "cv_history", "audit", "browser-profile", "tmp"):
        shutil.rmtree(root / sub, ignore_errors=True)
        (root / sub).mkdir(exist_ok=True)
    for f in ("cv.md", "profile.yml", "portals.yml"):
        (root / f).unlink(missing_ok=True)
    audit.log("system", "data_deleted", "All personal data was deleted at the user's request")
    return {"ok": True}
