from __future__ import annotations

import json
from typing import Any, Literal

from fastapi import APIRouter, HTTPException
from fastapi.responses import FileResponse, Response
from pydantic import BaseModel, Field

from .. import audit, db, tasks
from ..context import uid
from ..services import entitlements, tracking
from ..files import data_path
from ..services import browser_prep, documents, outreach, pipeline, profile_service as ps, resume_engine as re_

router = APIRouter(prefix="/api", tags=["documents"])

APP_STATUSES = {"prepared": "Prepared", "applied": "Applied (user-confirmed)", "interview": "Interview", "offer": "Offer",
                "rejected": "Rejected", "withdrawn": "Withdrawn"}


class ResumeEdit(BaseModel):
    content: dict[str, Any]


# ---------------------------------------------------------------- resumes
@router.post("/jobs/{job_id}/resumes")
def generate(job_id: str):
    j = pipeline.get_job(job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    if j["status"] not in ("qualified", "shortlisted"):
        raise HTTPException(409, f"Tailoring is only available for qualifying jobs. This job is '{j['status_label']}'.")

    entitlements.check("resumes_count")
    entitlements.consume("resume_generation")

    def work(t):
        r = re_.generate(job_id, t)
        tracking.event("resume_tailored", feature="resume", actor="system", meta={"ats_score": r["ats_score"]})
        return {"resume_id": r["id"], "ats_score": r["ats_score"]}
    return tasks.submit(f"resume:{job_id}", work, label="Generating tailored resume")


@router.get("/resumes")
def resumes(job_id: str | None = None):
    return re_.list_resumes(job_id)


@router.get("/resumes/{rid}")
def resume(rid: int):
    r = re_.get_resume(rid)
    if not r:
        raise HTTPException(404, "Resume not found")
    job = pipeline.get_job(r["job_id"])
    r["job"] = {k: job.get(k) for k in ("id", "title", "company", "description", "source_url", "match_score", "status", "status_label")}
    r["requirements"] = job.get("requirements")
    r["versions"] = db.query("SELECT id, version, status, ats_score, edited, created_at FROM resumes WHERE job_id = ? AND user_id = ? ORDER BY version DESC",
                             (r["job_id"], uid()))
    prof = ps.get_version(r["profile_version_id"])
    r["profile_version"] = prof["version"] if prof else None
    return r


@router.put("/resumes/{rid}")
def edit(rid: int, body: ResumeEdit):
    return re_.save_edit(rid, body.content)


@router.post("/resumes/{rid}/approve")
def approve(rid: int):
    return re_.approve(rid)


@router.post("/resumes/{rid}/discard")
def discard(rid: int):
    return re_.reject(rid)


@router.get("/resumes/{rid}/download")
def download(rid: int, format: Literal["pdf", "docx"] = "pdf"):
    r = re_.get_resume(rid)
    if not r:
        raise HTTPException(404, "Resume not found")
    path = data_path(r["pdf_path"] if format == "pdf" else r["docx_path"])
    if not path.exists():
        raise HTTPException(404, "File missing on disk; regenerate the resume")
    name = (r["content"].get("basics", {}).get("name") or "resume").replace(" ", "_")
    tracking.event("resume_downloaded", feature="resume", meta={"format": format})
    audit.log("document", "resume_downloaded", f"Downloaded resume v{r['version']} as {format.upper()}", entity_type="resume", entity_id=rid)
    media = "application/pdf" if format == "pdf" else "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return FileResponse(path, media_type=media, filename=f"{name}_v{r['version']}.{format}")


@router.get("/resumes/{rid}/pages/{page}")
def page_png(rid: int, page: int):
    r = re_.get_resume(rid)
    if not r:
        raise HTTPException(404, "Resume not found")
    path = data_path(r["pdf_path"])
    if not path.exists():
        raise HTTPException(404, "File missing on disk")
    try:
        png, n = documents.render_page_png(path.read_bytes(), page)
    except IndexError:
        raise HTTPException(404, "Page out of range")
    return Response(png, media_type="image/png", headers={"X-Page-Count": str(n), "Cache-Control": "private, max-age=300"})


# ---------------------------------------------------------------- applications
class AppCreate(BaseModel):
    job_id: str


class AppPatch(BaseModel):
    status: str | None = None
    next_action: str | None = Field(default=None, max_length=300)
    next_action_date: str | None = None
    notes: str | None = Field(default=None, max_length=5000)
    rejection_reason: str | None = Field(default=None, max_length=300)
    reviewed: bool | None = None
    answers: list[dict] | None = None


def _app(aid: int) -> dict:
    a = db.query_one("SELECT a.*, j.title, j.company, j.location, j.source, j.source_url, j.apply_url, r.version AS resume_version, "
                     "r.ats_score FROM applications a JOIN jobs j ON j.id = a.job_id LEFT JOIN resumes r ON r.id = a.resume_id WHERE a.id = ? AND a.user_id = ?", (aid, uid()))
    if not a:
        raise HTTPException(404, "Application not found")
    a["answers"] = db.jloads(a["answers"], [])
    a["prefill_log"] = db.jloads(a["prefill_log"], None)
    a["status_label"] = APP_STATUSES.get(a["status"], a["status"])
    a["history"] = db.query("SELECT * FROM application_history WHERE application_id = ? ORDER BY id", (aid,))
    return a


@router.get("/applications")
def list_apps(status: str = "", q: str = "", sort: str = "updated"):
    where, params = ["a.user_id = ?"], [uid()]
    if status:
        vals = status.split(",")
        where.append(f"a.status IN ({','.join('?' for _ in vals)})")
        params += vals
    if q:
        where.append("(j.title LIKE ? OR j.company LIKE ?)")
        params += [f"%{q}%"] * 2
    order = {"updated": "a.updated_at DESC", "company": "j.company COLLATE NOCASE", "next": "COALESCE(a.next_action_date,'9999')",
             "created": "a.created_at DESC"}.get(sort, "a.updated_at DESC")
    rows = db.query(f"SELECT a.id, a.status, a.next_action, a.next_action_date, a.created_at, a.updated_at, a.submitted_at, a.reviewed, "
                    f"j.id AS job_id, j.title, j.company, j.source, j.location FROM applications a JOIN jobs j ON j.id = a.job_id "
                    f"WHERE {' AND '.join(where)} ORDER BY {order}", params)
    for r in rows:
        r["status_label"] = APP_STATUSES.get(r["status"], r["status"])
    counts = {s: db.query_one("SELECT COUNT(*) n FROM applications WHERE status = ? AND user_id = ?", (s, uid()))["n"] for s in APP_STATUSES}
    return {"items": rows, "counts": counts, "statuses": APP_STATUSES}


@router.post("/applications")
def create_app(body: AppCreate):
    j = pipeline.get_job(body.job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    if j["status"] not in ("qualified", "shortlisted"):
        raise HTTPException(409, "Applications can only be prepared for qualifying jobs.")
    resume = db.query_one("SELECT id FROM resumes WHERE job_id = ? AND user_id = ? AND status = 'approved' ORDER BY version DESC LIMIT 1", (body.job_id, uid()))
    if not resume:
        raise HTTPException(409, "Approve a tailored resume for this job first (Resume Studio).")
    existing = db.query_one("SELECT id FROM applications WHERE job_id = ? AND user_id = ? AND status NOT IN ('withdrawn')", (body.job_id, uid()))
    if existing:
        return _app(existing["id"])
    vp = ps.verified_view(ps.active_profile())
    answers = re_.draft_answers(j, vp)
    now = db.now_iso()
    entitlements.check("applications_tracked")
    aid = db.execute("INSERT INTO applications(job_id, resume_id, status, next_action, answers, created_at, updated_at, user_id) VALUES (?,?,?,?,?,?,?,?)",
                     (body.job_id, resume["id"], "prepared", "Review prepared data, then open the application page", db.jdumps(answers), now, now, uid()))
    tracking.event("application_prepared", feature="applications", actor="system")
    db.execute("INSERT INTO application_history(application_id, from_status, to_status, note, at) VALUES (?,?,?,?,?)",
               (aid, None, "prepared", "Application prepared", now))
    audit.log("decision", "application_prepared", f"Application prepared for '{j['title']}' at {j['company']} (not submitted)",
              entity_type="application", entity_id=aid)
    return _app(aid)


@router.get("/applications/{aid}")
def get_app(aid: int):
    return _app(aid)


@router.patch("/applications/{aid}")
def patch_app(aid: int, body: AppPatch):
    a = _app(aid)
    fields, now = {}, db.now_iso()
    if body.status and body.status != a["status"]:
        if body.status not in APP_STATUSES:
            raise HTTPException(422, "Unknown status")
        fields["status"] = body.status
        if body.status == "applied":
            fields["submitted_at"] = now
        db.execute("INSERT INTO application_history(application_id, from_status, to_status, note, at) VALUES (?,?,?,?,?)",
                   (aid, a["status"], body.status, body.notes if body.notes and body.notes != a["notes"] else None, now))
        fields["outcome_source"] = "user_reported"
        tracking.event({"interview": "interview_reported", "offer": "offer_reported"}.get(body.status, "application_status_updated"),
                       feature="applications", meta={"from_status": a["status"], "to_status": body.status})
        audit.log("decision", "application_status", f"'{a['title']}' at {a['company']}: {APP_STATUSES[a['status']]} → "
                  f"{APP_STATUSES[body.status]} (updated by you)", entity_type="application", entity_id=aid)
    for f in ("next_action", "next_action_date", "notes", "rejection_reason"):
        v = getattr(body, f)
        if v is not None:
            fields[f] = v or None
    if body.reviewed is not None:
        fields["reviewed"] = 1 if body.reviewed else 0
        if body.reviewed:
            audit.log("decision", "application_reviewed", f"User reviewed prepared data for '{a['title']}'", entity_type="application", entity_id=aid)
    if body.answers is not None:
        fields["answers"] = db.jdumps([{"question": str(x.get("question", ""))[:300], "answer": str(x.get("answer", ""))[:3000],
                                        "source": "Edited by you" if x.get("edited") else str(x.get("source", ""))[:100]} for x in body.answers][:40])
    if fields:
        fields["updated_at"] = now
        db.execute(f"UPDATE applications SET {', '.join(f'{k} = ?' for k in fields)} WHERE id = ? AND user_id = ?", (*fields.values(), aid, uid()))
    return _app(aid)


@router.delete("/applications/{aid}")
def delete_app(aid: int):
    a = _app(aid)
    db.execute("DELETE FROM applications WHERE id = ? AND user_id = ?", (aid, uid()))
    audit.log("decision", "application_deleted", f"Deleted application record for '{a['title']}'", entity_type="application", entity_id=aid)
    return {"ok": True}


@router.get("/browser/status")
def browser_available():
    ok, why = browser_prep.playwright_available()
    return {"available": ok, "message": why}


@router.post("/applications/{aid}/launch-browser")
def launch(aid: int):
    if not entitlements.flag("browser_prep"):
        raise HTTPException(409, "Browser preparation is disabled by the administrator.")
    entitlements.require_feature("automation")
    a = _app(aid)
    if not a["reviewed"]:
        raise HTTPException(409, "Review the job, selected resume and prepared form data first.")
    ok, why = browser_prep.playwright_available()
    if not ok:
        raise HTTPException(409, why)
    r = re_.get_resume(a["resume_id"])
    vp = ps.verified_view(ps.get_version(r["profile_version_id"]))
    log = data_path("tmp", f"prefill_u{uid()}_app{aid}.json")
    browser_prep.launch(a["apply_url"] or a["source_url"], browser_prep.build_values(vp), data_path(r["pdf_path"]), log)
    db.execute("UPDATE applications SET prefill_log = ?, updated_at = ? WHERE id = ? AND user_id = ?",
               (db.jdumps({"status": "launching", "log_file": str(log.name)}), db.now_iso(), aid))
    audit.log("automation", "browser_launched", f"Opened application page for '{a['title']}' in your local browser profile. "
              "JobPilot fills supported fields only and never submits.", entity_type="application", entity_id=aid,
              source_ref=a["apply_url"])
    return {"ok": True}


@router.get("/applications/{aid}/prefill")
def prefill_status(aid: int):
    a = _app(aid)
    log = data_path("tmp", f"prefill_u{uid()}_app{aid}.json")
    if not log.exists():
        return a["prefill_log"] or {"status": "not_started"}
    try:
        data = json.loads(log.read_text(encoding="utf-8"))
    except ValueError:
        return {"status": "starting"}
    if data.get("status") in ("paused_for_user", "browser_closed", "failed") and (a["prefill_log"] or {}).get("status") != data["status"]:
        db.execute("UPDATE applications SET prefill_log = ? WHERE id = ? AND user_id = ?", (db.jdumps(data), aid, uid()))
        audit.log("automation", "prefill_" + data["status"], f"Prefill for '{a['title']}': {data['status']} — "
                  f"{len([f for f in data.get('filled', []) if f.get('action') in ('filled', 'attached')])} field(s) filled",
                  level="warning" if data["status"] == "failed" else "info", entity_type="application", entity_id=aid,
                  details={"filled": data.get("filled"), "error": data.get("error")})
    return data


# ---------------------------------------------------------------- contacts & outreach
class ContactIn(BaseModel):
    name: str
    role: str | None = None
    company: str | None = None
    email: str | None = None
    profile_url: str | None = None
    source_url: str | None = None
    source_label: str | None = None
    verification: Literal["verified", "public_unverified", "inferred"] = "public_unverified"
    relationship: str | None = None
    notes: str | None = None
    job_id: str | None = None


class DraftCreate(BaseModel):
    kind: Literal["referral", "recruiter_email", "connection", "follow_up"]
    job_id: str | None = None
    contact_id: int | None = None


class DraftPatch(BaseModel):
    subject: str | None = None
    body: str | None = None
    action: Literal["approve", "mark_sent"] | None = None


@router.get("/contacts")
def contacts(q: str = "", job_id: str = ""):
    where, params = ["c.user_id = ?"], [uid()]
    if q:
        where.append("(c.name LIKE ? OR c.company LIKE ? OR c.role LIKE ?)")
        params += [f"%{q}%"] * 3
    if job_id:
        where.append("c.job_id = ?")
        params.append(job_id)
    rows = db.query(f"SELECT c.*, j.title AS job_title FROM contacts c LEFT JOIN jobs j ON j.id = c.job_id WHERE {' AND '.join(where)} "
                    f"ORDER BY c.created_at DESC", params)
    drafts = db.query("SELECT d.*, c.name AS contact_name, j.title AS job_title, j.company AS job_company FROM outreach_drafts d "
                      "LEFT JOIN contacts c ON c.id = d.contact_id LEFT JOIN jobs j ON j.id = d.job_id WHERE d.user_id = ? ORDER BY d.updated_at DESC", (uid(),))
    return {"contacts": rows, "drafts": drafts, "verification_labels": outreach.VERIFICATION, "kinds": outreach.KINDS}


@router.post("/contacts")
def add_contact(c: ContactIn):
    return outreach.save_contact(c.model_dump())


@router.put("/contacts/{cid}")
def edit_contact(cid: int, c: ContactIn):
    if not db.query_one("SELECT id FROM contacts WHERE id = ? AND user_id = ?", (cid, uid())):
        raise HTTPException(404, "Contact not found")
    return outreach.save_contact(c.model_dump(), cid)


@router.delete("/contacts/{cid}")
def del_contact(cid: int):
    c = db.query_one("SELECT * FROM contacts WHERE id = ? AND user_id = ?", (cid, uid()))
    if not c:
        raise HTTPException(404, "Contact not found")
    db.execute("DELETE FROM contacts WHERE id = ? AND user_id = ?", (cid, uid()))
    audit.log("decision", "contact_deleted", f"Deleted contact {c['name']}", entity_type="contact", entity_id=cid)
    return {"ok": True}


@router.post("/outreach")
def new_draft(body: DraftCreate):
    entitlements.require_feature("outreach")
    return outreach.generate_draft(body.kind, body.job_id, body.contact_id)


@router.patch("/outreach/{did}")
def patch_draft(did: int, body: DraftPatch):
    return outreach.update_draft(did, body.model_dump(exclude_unset=True))


@router.delete("/outreach/{did}")
def del_draft(did: int):
    outreach.delete_draft(did)
    return {"ok": True}
