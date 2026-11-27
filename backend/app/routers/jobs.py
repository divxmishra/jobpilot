from __future__ import annotations

import shutil
from typing import Literal

from fastapi import APIRouter, HTTPException, Query
from fastapi.responses import PlainTextResponse
from pydantic import BaseModel, Field

from .. import audit, db, tasks
from ..context import uid
from ..services import entitlements, tracking
from ..files import job_dir
from ..services import discovery, job_extract, outreach, pipeline
from ..services import profile_service as ps

router = APIRouter(prefix="/api", tags=["jobs"])

VIEWS = {
    "recommended": "status IN ('qualified','shortlisted')",
    "shortlisted": "status = 'shortlisted'",
    "near_match": "status = 'near_match'",
    "needs_review": "status IN ('needs_review','new')",
    "rejected": "status IN ('not_eligible','below_threshold','dismissed')",
    "expired": "status = 'expired'",
    "all": "1=1",
}
SORTS = {"match": "COALESCE(match_score, -1) DESC, created_at DESC", "posted": "COALESCE(posted_at, '') DESC, created_at DESC",
         "company": "company COLLATE NOCASE ASC", "newest": "created_at DESC", "salary": "COALESCE(salary_max, -1) DESC"}
LIST_COLS = ("id, company, title, location, work_mode, employment_type, seniority, salary_text, salary_max, salary_currency, "
             "posted_at, verified_at, source, source_url, apply_url, alt_urls, page_status, match_score, eligibility_result, "
             "status, decision, decision_reason, extraction_complete, created_at, updated_at")


class UrlIn(BaseModel):
    url: str = Field(min_length=8, max_length=2000)


class ManualJob(BaseModel):
    title: str = Field(min_length=2, max_length=200)
    company: str = Field(min_length=1, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    url: str | None = Field(default=None, max_length=2000)
    description: str = Field(min_length=50, max_length=60000)
    employment_type: str | None = None
    posted_at: str | None = None


class Decision(BaseModel):
    decision: Literal["shortlist", "dismiss", "reset"]
    reason: str | None = Field(default=None, max_length=500)


class GateConfirm(BaseModel):
    key: str = Field(max_length=200)


@router.get("/jobs")
def list_jobs(view: str = "recommended", q: str = "", source: str = "", work_mode: str = "", employment_type: str = "",
              location: str = "", min_salary: float | None = None, posted_within: int | None = None, max_experience: float | None = None,
              sort: str = "match", page: int = Query(1, ge=1), page_size: int = Query(20, ge=1, le=100)):
    if view not in VIEWS:
        raise HTTPException(422, f"Unknown view '{view}'")
    where, params = [VIEWS[view], "user_id = ?"], [uid()]
    if q:
        where.append("(title LIKE ? OR company LIKE ? OR description LIKE ?)")
        params += [f"%{q}%"] * 3
    for col, val in (("source", source), ("work_mode", work_mode), ("employment_type", employment_type)):
        if val:
            vals = [v for v in val.split(",") if v]
            where.append(f"{col} IN ({','.join('?' for _ in vals)})")
            params += vals
    if location:
        where.append("location LIKE ?")
        params.append(f"%{location}%")
    if min_salary is not None:
        where.append("salary_max >= ?")
        params.append(min_salary)
    if posted_within:
        where.append("posted_at IS NOT NULL AND date(substr(posted_at,1,10)) >= date('now', ?)")
        params.append(f"-{int(posted_within)} days")
    if max_experience is not None:
        where.append("(json_extract(requirements, '$.experience.min_years') IS NULL OR json_extract(requirements, '$.experience.min_years') <= ?)")
        params.append(max_experience)
    w = " AND ".join(where)
    total = db.query_one(f"SELECT COUNT(*) n FROM jobs WHERE {w}", params)["n"]
    rows = db.query(f"SELECT {LIST_COLS} FROM jobs WHERE {w} ORDER BY {SORTS.get(sort, SORTS['match'])} LIMIT ? OFFSET ?",
                    params + [page_size, (page - 1) * page_size])
    for r in rows:
        r["alt_urls"] = db.jloads(r["alt_urls"], [])
        r["status_label"] = pipeline.STATUS_LABELS.get(r["status"], r["status"])
    counts = {k: db.query_one(f"SELECT COUNT(*) n FROM jobs WHERE {v} AND user_id = ?", (uid(),))["n"] for k, v in VIEWS.items()}
    sources = [r["source"] for r in db.query("SELECT DISTINCT source FROM jobs WHERE user_id = ? ORDER BY source", (uid(),))]
    return {"items": rows, "total": total, "page": page, "page_size": page_size, "counts": counts, "sources": sources,
            "threshold": ps.get_preferences()["match_threshold"]}


@router.post("/jobs/url")
def submit_url(body: UrlIn):
    url = body.url.strip()
    if not url.lower().startswith(("http://", "https://")):
        raise HTTPException(422, "Please enter a full URL starting with http:// or https://")
    entitlements.consume("job_search")
    tracking.event("job_search", feature="jobs", meta={"source": "manual_url"})

    def work(t):
        t.progress(0.2, "Fetching and extracting the posting")
        job, dup = pipeline.process_url(url)
        t.progress(0.9, "Evaluated")
        return {"job_id": job["id"], "duplicate": dup, "status": job["status"], "status_label": job["status_label"]}
    return tasks.submit("job_url", work, label="Processing job URL", unique=False)


@router.post("/jobs/manual")
def manual_job(body: ManualJob):
    if body.url and not body.url.lower().startswith(("http://", "https://")):
        raise HTTPException(422, "Job URL must start with http:// or https://")
    entitlements.consume("job_search")
    tracking.event("job_search", feature="jobs", meta={"source": "manual_entry"})
    ext = job_extract.from_manual(body.model_dump())
    job, dup = pipeline.ingest(ext, trigger="manual entry")
    return {"job": pipeline.evaluate(job["id"]), "duplicate": dup}


@router.get("/jobs/{job_id}")
def get_job(job_id: str):
    j = pipeline.get_job(job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    if not j.get("viewed_at"):
        entitlements.consume("job_view")
        db.execute("UPDATE jobs SET viewed_at = ? WHERE id = ? AND user_id = ?", (db.now_iso(), job_id, uid()))
        tracking.event("job_viewed", feature="jobs")
    j["resumes"] = db.query("SELECT id, version, status, ats_score, created_at FROM resumes WHERE job_id = ? AND user_id = ? ORDER BY version DESC", (job_id, uid()))
    j["application"] = db.query_one("SELECT id, status FROM applications WHERE job_id = ? AND user_id = ? ORDER BY id DESC LIMIT 1", (job_id, uid()))
    j["contacts"] = db.query("SELECT * FROM contacts WHERE job_id = ? AND user_id = ?", (job_id, uid()))
    return j


@router.post("/jobs/{job_id}/decision")
def decide(job_id: str, body: Decision):
    j = pipeline.get_job(job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    if body.decision == "shortlist":
        if j["status"] != "qualified":
            raise HTTPException(409, "Only qualifying jobs (all hard gates passed and match ≥ threshold) can be shortlisted.")
        entitlements.check("saved_jobs")
        db.execute("UPDATE jobs SET decision = 'shortlisted', status = 'shortlisted', updated_at = ? WHERE id = ? AND user_id = ?", (db.now_iso(), job_id, uid()))
        tracking.event("job_shortlisted", feature="jobs")
        msg = f"User shortlisted '{j['title']}' at {j['company']}"
    elif body.decision == "dismiss":
        db.execute("UPDATE jobs SET decision = 'dismissed', status = 'dismissed', updated_at = ? WHERE id = ? AND user_id = ?", (db.now_iso(), job_id, uid()))
        msg = f"User dismissed '{j['title']}' at {j['company']}" + (f": {body.reason}" if body.reason else "")
    else:
        db.execute("UPDATE jobs SET decision = NULL, updated_at = ? WHERE id = ? AND user_id = ?", (db.now_iso(), job_id, uid()))
        msg = f"User reset decision for '{j['title']}'"
    audit.log("decision", f"job_{body.decision}", msg, entity_type="job", entity_id=job_id, details={"reason": body.reason})
    if body.decision == "reset":
        return pipeline.evaluate(job_id)
    return pipeline.get_job(job_id)


@router.post("/jobs/{job_id}/confirm-gate")
def confirm_gate(job_id: str, body: GateConfirm):
    j = pipeline.get_job(job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    gates = {g.get("key"): g for g in (j["eligibility"] or {}).get("gates", [])}
    g = gates.get(body.key)
    if not g or g["result"] != "needs_verification":
        raise HTTPException(409, "This requirement does not need verification")
    if body.key == "posting_status" and j["source"] != "manual":
        raise HTTPException(409, "Use 'Re-check status' for postings JobPilot fetched; only manually entered jobs can be confirmed open by you.")
    conf = j["user_confirmations"]
    conf[body.key] = db.now_iso()
    db.execute("UPDATE jobs SET user_confirmations = ? WHERE id = ? AND user_id = ?", (db.jdumps(conf), job_id, uid()))
    audit.log("decision", "gate_confirmed", f"User confirmed requirement '{g['label']}' for '{j['title']}'", entity_type="job", entity_id=job_id)
    return pipeline.evaluate(job_id)


@router.post("/jobs/{job_id}/recheck")
def recheck(job_id: str):
    j = pipeline.get_job(job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    if j["source"] == "manual" and not j["source_url"]:
        raise HTTPException(409, "This job was entered manually without a URL, so its status cannot be re-checked automatically.")
    ext = job_extract.extract_url(j["source_url"])
    db.execute("UPDATE jobs SET page_status = ?, page_status_detail = ?, verified_at = ?, updated_at = ? WHERE id = ? AND user_id = ?",
               (ext["page_status"], ext["page_status_detail"], db.now_iso(), db.now_iso(), job_id, uid()))
    audit.log("extraction", "status_rechecked", f"Re-checked '{j['title']}': {ext['page_status']} — {ext['page_status_detail']}",
              entity_type="job", entity_id=job_id, source_ref=j["source_url"])
    return pipeline.evaluate(job_id)


@router.post("/jobs/{job_id}/evaluate")
def reevaluate(job_id: str):
    if not pipeline.get_job(job_id):
        raise HTTPException(404, "Job not found")
    return pipeline.evaluate(job_id)


@router.post("/jobs/{job_id}/find-contacts")
def find_contacts(job_id: str):
    j = pipeline.get_job(job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    found = outreach.contacts_from_job(j)
    return {"found": found, "message": f"{len(found)} public contact(s) found in the posting" if found else
            "No contact details are published in this posting. Add contacts you find on official sources manually."}


@router.delete("/jobs/{job_id}")
def delete_job(job_id: str):
    j = pipeline.get_job(job_id)
    if not j:
        raise HTTPException(404, "Job not found")
    db.execute("DELETE FROM jobs WHERE id = ? AND user_id = ?", (job_id, uid()))
    shutil.rmtree(job_dir(job_id), ignore_errors=True)
    audit.log("decision", "job_deleted", f"User deleted job '{j['title']}' at {j['company']}", entity_type="job", entity_id=job_id)
    return {"ok": True}


@router.get("/jobs/{job_id}/report.md", response_class=PlainTextResponse)
def report(job_id: str):
    p = job_dir(job_id) / "report.md"
    if not p.exists():
        raise HTTPException(404, "Report not generated yet")
    return p.read_text(encoding="utf-8")


@router.post("/discovery/run")
def run_discovery():
    if not entitlements.flag("discovery"):
        raise HTTPException(409, "Job discovery is temporarily disabled by the administrator.")
    entitlements.require_feature("automation")
    entitlements.consume("job_search")
    tracking.event("job_search", feature="discovery", meta={"source": "discovery"})
    return tasks.submit("discovery", lambda t: discovery.run(t, "manual"), label="Discovery run")


@router.get("/discovery/runs")
def runs():
    rows = db.query("SELECT * FROM discovery_runs WHERE user_id = ? ORDER BY id DESC LIMIT 20", (uid(),))
    for r in rows:
        r["stats"] = db.jloads(r["stats"], {})
    return {"runs": rows, "schedule": discovery.get_schedule(), "running": tasks.running("discovery", uid())}


@router.post("/jobs/reevaluate")
def reevaluate_all():
    return tasks.submit("reevaluate", pipeline.reevaluate_all, label="Re-evaluating all jobs")
