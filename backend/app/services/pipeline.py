"""Job pipeline shared by manual URLs, manual entry and discovery sources:
extract -> dedupe -> validate -> hard gates -> match -> decision -> reports."""
from __future__ import annotations

import re
import uuid

from .. import audit, db
from ..context import uid
from ..files import atomic_write, job_dir, write_json
from . import ai, evaluation, profile_service as ps, requirements as rq

JOB_COLUMNS = ["company", "title", "location", "work_mode", "employment_type", "seniority", "salary_text", "salary_min",
               "salary_max", "salary_currency", "posted_at", "source", "source_url", "apply_url", "external_id",
               "description", "extracted_at", "page_status", "page_status_detail", "extraction_complete"]

STATUS_LABELS = {"new": "New", "needs_review": "Needs Review", "qualified": "Qualified", "near_match": "Near Match",
                 "below_threshold": "Below Threshold", "not_eligible": "Not Eligible", "shortlisted": "Shortlisted",
                 "dismissed": "Dismissed", "expired": "Expired", "error": "Error"}


def _norm(s: str | None) -> str:
    s = (s or "").lower()
    s = re.sub(r"\b(inc|ltd|llc|pvt|private|limited|corp|corporation|co|gmbh|technologies|technology)\b\.?", "", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def dedupe_key(job: dict) -> str:
    loc = _norm((job.get("location") or "").split(";")[0].split(",")[0])
    return f"{_norm(job.get('company'))}|{_norm(job.get('title'))}|{loc}"


def get_job(job_id: str) -> dict | None:
    j = db.query_one("SELECT * FROM jobs WHERE id = ? AND user_id = ?", (job_id, uid()))
    if not j:
        return None
    for k, d in (("alt_urls", []), ("missing_fields", []), ("requirements", None), ("eligibility", None), ("match", None),
                 ("user_confirmations", {})):
        j[k] = db.jloads(j[k], d)
    j["status_label"] = STATUS_LABELS.get(j["status"], j["status"])
    j["recommended"] = j["status"] in ("qualified", "shortlisted")
    return j


def find_duplicate(job: dict) -> dict | None:
    if job.get("external_id"):
        r = db.query_one("SELECT id FROM jobs WHERE source = ? AND external_id = ? AND user_id = ?", (job["source"], job["external_id"], uid()))
        if r:
            return r
    if job.get("source_url"):
        r = db.query_one("SELECT id FROM jobs WHERE user_id = ? AND (source_url = ? OR apply_url = ? OR alt_urls LIKE ?)",
                         (uid(), job["source_url"], job["source_url"], f'%"{job["source_url"]}"%'))
        if r:
            return r
    if job.get("company") and job.get("title"):
        return db.query_one("SELECT id FROM jobs WHERE dedupe_key = ? AND user_id = ?", (dedupe_key(job), uid()))
    return None


def ingest(extracted: dict, *, trigger: str) -> tuple[dict, bool]:
    """Store an extracted job (or merge into a duplicate). Returns (job, is_duplicate)."""
    dup = find_duplicate(extracted)
    if dup:
        existing = get_job(dup["id"])
        alts = existing["alt_urls"]
        changed = False
        for u in (extracted.get("source_url"), extracted.get("apply_url")):
            if u and u not in alts and u not in (existing["source_url"], existing["apply_url"]):
                alts.append(u)
                changed = True
        fields = {"alt_urls": db.jdumps(alts), "updated_at": db.now_iso()}
        # Refresh validation status from the newest check.
        if extracted.get("page_status") and extracted["page_status"] != "unknown":
            fields.update(page_status=extracted["page_status"], page_status_detail=extracted.get("page_status_detail"),
                          verified_at=db.now_iso())
        sets = ", ".join(f"{k} = ?" for k in fields)
        db.execute(f"UPDATE jobs SET {sets} WHERE id = ? AND user_id = ?", (*fields.values(), existing["id"], uid()))
        audit.log("discovery", "duplicate", f"Duplicate of existing job '{existing['title']}' at {existing['company']}"
                  + (" — alternate source URL recorded" if changed else ""), entity_type="job", entity_id=existing["id"],
                  source_ref=extracted.get("source_url"))
        return get_job(existing["id"]), True
    jid = "j_" + uuid.uuid4().hex[:10]
    now = db.now_iso()
    vals = {k: extracted.get(k) for k in JOB_COLUMNS}
    vals.update(id=jid, dedupe_key=dedupe_key(extracted), missing_fields=db.jdumps(extracted.get("missing_fields", [])),
                alt_urls="[]", created_at=now, updated_at=now, status="new", user_id=uid(),
                verified_at=now if extracted.get("page_status") not in (None, "unknown") else None)
    cols = ", ".join(vals)
    db.execute(f"INSERT INTO jobs({cols}) VALUES ({', '.join('?' for _ in vals)})", tuple(vals.values()))
    audit.log("extraction", "job_extracted",
              f"Extracted '{extracted.get('title') or 'untitled'}' at {extracted.get('company') or 'unknown company'} "
              f"via {trigger} ({extracted.get('source')}); page status: {extracted.get('page_status')}",
              entity_type="job", entity_id=jid, source_ref=extracted.get("source_url"),
              details={"missing_fields": extracted.get("missing_fields")})
    return get_job(jid), False


def evaluate(job_id: str) -> dict:
    job = get_job(job_id)
    if not job:
        raise LookupError("Job not found")
    profile = ps.active_profile()
    if not profile:
        db.execute("UPDATE jobs SET status = 'new', decision_reason = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                   ("Waiting for a verified profile before evaluation", db.now_iso(), job_id, uid()))
        return get_job(job_id)
    vp = ps.verified_view(profile)
    prefs = ps.get_preferences()
    custom_terms = [s["name"] for s in vp.get("skills", [])]
    req = rq.parse_requirements(job, custom_terms)
    req = ai.refine_requirements(job, req)  # optional, consent- and budget-gated; falls back to deterministic
    elig = evaluation.evaluate_gates(job, req, vp, prefs, job["user_confirmations"])
    match = evaluation.compute_match(job, req, vp, prefs)
    status, reason = evaluation.decide(job, elig, match, prefs)
    if job.get("decision") == "dismissed":
        status = "dismissed"
    elif job.get("decision") == "shortlisted":
        if status == "qualified":
            status = "shortlisted"
        else:
            # Re-evaluation made a shortlisted job ineligible: undo the shortlist, tell the user.
            db.execute("UPDATE jobs SET decision = NULL WHERE id = ? AND user_id = ?", (job_id, uid()))
            audit.log("scoring", "shortlist_revoked", f"'{job['title']}' no longer qualifies after re-evaluation: {reason}",
                      level="warning", entity_type="job", entity_id=job_id, notify=True, link=f"/app/jobs/{job_id}")
    db.execute(
        "UPDATE jobs SET requirements = ?, eligibility = ?, eligibility_result = ?, match = ?, match_score = ?, status = ?, "
        "decision_reason = ?, profile_version_id = ?, updated_at = ? WHERE id = ? AND user_id = ?",
        (db.jdumps(req), db.jdumps(elig), elig["result"], db.jdumps(match), match["score"], status, reason,
         profile["id"], db.now_iso(), job_id, uid()))
    audit.log("scoring", "job_evaluated",
              f"'{job['title']}' at {job['company']}: eligibility {elig['result']}, match {match['score']}% "
              f"({match['confidence']} confidence) → {STATUS_LABELS[status]}",
              entity_type="job", entity_id=job_id, source_ref=job["source_url"],
              details={"rubric": match["rubric_version"], "profile_version": profile["version"], "reason": reason},
              notify=status == "qualified" and job["status"] != "qualified", link=f"/app/jobs/{job_id}")
    j = get_job(job_id)
    write_job_files(j, profile["version"])
    return j


def write_job_files(job: dict, profile_version: int | None = None) -> None:
    d = job_dir(job["id"])
    data = {k: job.get(k) for k in ["id", "company", "title", "location", "work_mode", "employment_type", "seniority",
                                    "salary_text", "posted_at", "source", "source_url", "apply_url", "alt_urls",
                                    "external_id", "extracted_at", "verified_at", "page_status", "page_status_detail",
                                    "extraction_complete", "missing_fields", "description"]}
    write_json(d / "job.json", data)
    atomic_write(d / "report.md", render_report(job, profile_version))


def render_report(job: dict, profile_version: int | None) -> str:
    m = job.get("match") or {}
    e = job.get("eligibility") or {}
    L = [f"# {job.get('title') or 'Untitled role'} — {job.get('company') or 'Unknown company'}", "",
         f"- **Decision:** {STATUS_LABELS.get(job['status'], job['status'])}",
         f"- **Explanation:** {job.get('decision_reason') or ''}",
         f"- **Match score:** {m.get('score', '—')}% (rubric {m.get('rubric_version', '')}, confidence {m.get('confidence', '—')})",
         f"- **Eligibility:** {e.get('result', '—')}",
         f"- **Source:** {job.get('source')} — {job.get('source_url')}",
         f"- **Posted:** {job.get('posted_at') or 'not stated'} · **Verified:** {job.get('verified_at') or 'not verified'}",
         f"- **Page status:** {job.get('page_status')} — {job.get('page_status_detail') or ''}",
         f"- **Evaluated against profile version:** {profile_version or '—'}", "",
         "> JobPilot's estimate based on your verified profile. Not an employer decision.", "",
         "## Hard eligibility gates", "", "| Gate | Result | Detail | Evidence |", "|---|---|---|---|"]
    for g in e.get("gates", []):
        L.append(f"| {g['label']} | {g['result']} | {g['detail']} | {(g.get('evidence') or '').replace('|', '/')[:200]} |")
    L += ["", "## Score breakdown", "", "| Component | Weight | Points | Detail |", "|---|---|---|---|"]
    for c in m.get("components", []):
        L.append(f"| {c['label']} | {c['weight']} | {c['points'] if c['applicable'] else 'n/a'} | {c['detail']} |")
    L += ["", "## Matched skills (with resume evidence)"]
    for s in m.get("matched_skills", []):
        L.append(f"- **{s['skill']}** — {'; '.join(s['resume_evidence'])} (JD: \"{s['jd_evidence'][:120]}\")")
    if m.get("transferable"):
        L += ["", "## Transferable (partial credit, not claimed)"]
        L += [f"- {t['skill']} via {t['via']}" for t in m["transferable"]]
    L += ["", "## Missing requirements / skill gaps"]
    L += [f"- {s['skill']} (required) — JD: \"{s['jd_evidence'][:120]}\"" for s in m.get("missing_required", [])] or ["- None"]
    L += [f"- {s['skill']} (preferred)" for s in m.get("missing_preferred", [])]
    L += ["", "## Keywords", f"- Matched: {', '.join(m.get('matched_keywords', [])) or '—'}",
          f"- Missing: {', '.join(m.get('missing_keywords', [])) or '—'}"]
    if m.get("uncertainties"):
        L += ["", "## Uncertainty"] + [f"- {u}" for u in m["uncertainties"]]
    return "\n".join(L) + "\n"


def process_url(url: str, trigger: str = "manual URL") -> tuple[dict, bool]:
    from .job_extract import extract_url
    ext = extract_url(url)
    if ext["page_status"] in ("inaccessible",) and not ext.get("title"):
        audit.log("failure", "extraction_failed", f"Could not extract job from {url}: {ext['page_status_detail']}",
                  level="warning", source_ref=url)
    job, dup = ingest(ext, trigger=trigger)
    job = evaluate(job["id"])
    return job, dup


def reevaluate_all(task=None) -> dict:
    ids = [r["id"] for r in db.query("SELECT id FROM jobs WHERE user_id = ? ORDER BY created_at", (uid(),))]
    n = 0
    for i, jid in enumerate(ids):
        if task and task.cancelled():
            break
        try:
            evaluate(jid)
            n += 1
        except Exception as e:  # keep going; record failure
            audit.log("failure", "evaluation_failed", f"Re-evaluation failed for {jid}: {e}", level="error", entity_type="job", entity_id=jid)
        if task:
            task.progress((i + 1) / max(1, len(ids)), f"Re-evaluated {i + 1} of {len(ids)} jobs")
    return {"evaluated": n, "total": len(ids)}
