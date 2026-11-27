"""Discovery from the user's target-company registry (portals.yml) using official,
public job-board APIs. Also re-validates previously found jobs (closed/expired)."""
from __future__ import annotations

import re
import threading
import time
from datetime import date, datetime, timezone

from .. import audit, db
from ..context import as_user, uid
from ..config import settings
from . import job_extract as jx
from . import net, pipeline, profile_service as ps
from .textutil import tokens

MAX_PROCESS_PER_RUN = 80  # default; admin-configurable via system settings
ROLE_STOP = {"senior", "junior", "lead", "staff", "principal", "intern", "associate", "manager", "remote", "hybrid", "india"}


def _role_terms(prefs: dict, vp: dict) -> set[str]:
    roles = list(prefs.get("target_roles") or [])
    roles += [e.get("title") for e in vp.get("experience", []) if e.get("title")]
    if vp.get("basics", {}).get("headline"):
        roles.append(vp["basics"]["headline"])
    terms = set()
    for r in roles:
        terms |= {t for t in tokens(r) if t not in ROLE_STOP}
    return terms


def _relevant(title: str, terms: set[str]) -> bool:
    if not terms:
        return True
    return bool({t for t in tokens(title)} & terms)


def _too_old(posted: str | None, max_days: int) -> bool:
    if not posted:
        return False
    try:
        d = datetime.fromisoformat(posted.replace("Z", "+00:00")).date()
        return (date.today() - d).days > max_days
    except ValueError:
        return False


def list_source(c: dict) -> tuple[list[dict], str | None]:
    """Return (list of extracted job dicts, error)."""
    ats, tok = c["ats"], (c.get("token") or "").strip()
    out: list[dict] = []
    if ats == "greenhouse":
        st, data, err = net.fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{tok}/jobs?content=true")
        if err:
            return [], err
        for j in (data or {}).get("jobs", []):
            out.append(jx.from_greenhouse(tok, str(j.get("id")), j.get("absolute_url") or "", data=dict(j, company_name=c["name"])))
    elif ats == "lever":
        st, data, err = net.fetch_json(f"https://api.lever.co/v0/postings/{tok}?mode=json")
        if err:
            return [], err
        for j in data or []:
            x = jx.from_lever(tok, j.get("id"), j.get("hostedUrl") or "", data=j)
            x["company"] = c["name"]
            out.append(x)
    elif ats == "ashby":
        st, data, err = net.fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{tok}?includeCompensation=true")
        if err:
            return [], err
        for j in (data or {}).get("jobs", []):
            if j.get("isListed") is False:
                continue
            x = jx.from_ashby(tok, j.get("id"), j.get("jobUrl") or "", data=j)
            x["company"] = c["name"]
            out.append(x)
    elif ats == "remotive":
        from urllib.parse import quote
        st, data, err = net.fetch_json(f"https://remotive.com/api/remote-jobs?search={quote(tok)}&limit=50")
        if err:
            return [], err
        for j in (data or {}).get("jobs", []):
            x = jx._empty("remotive", j.get("url") or "")
            x.update(title=j.get("title"), company=j.get("company_name"), location=j.get("candidate_required_location"),
                     work_mode="remote", employment_type=(j.get("job_type") or "").replace("_", "-") or None,
                     posted_at=(j.get("publication_date") or "")[:10] or None, external_id=str(j.get("id")),
                     description=jx.html_to_text(j.get("description") or ""), salary_text=j.get("salary") or None,
                     page_status="active", page_status_detail="Listed on the Remotive public API")
            out.append(jx.finalize(x))
    elif ats == "career_page":
        r = net.fetch(c["careers_url"])
        if r.error or (r.status or 0) >= 400:
            return [], r.error or f"HTTP {r.status}"
        links = set(re.findall(r"https?://(?:boards|job-boards)\.greenhouse\.io/[\w\-]+/jobs/\d+|https?://jobs\.lever\.co/[\w\-]+/[\w\-]{20,}|"
                               r"https?://jobs\.ashbyhq\.com/[\w\-]+/[\w\-]{20,}", r.text))
        for u in list(links)[:40]:
            out.append(jx.extract_url(u))
    return out, None


def run(task=None, trigger: str = "manual") -> dict:
    run_id = db.execute("INSERT INTO discovery_runs(trigger, status, started_at, user_id) VALUES (?,?,?,?)", (trigger, "running", db.now_iso(), uid()))
    stats = {"sources": 0, "source_errors": 0, "listed": 0, "skipped_irrelevant": 0, "skipped_old": 0, "new": 0,
             "duplicates": 0, "qualified": 0, "closed_detected": 0, "rechecked": 0, "errors": []}
    audit.log("discovery", "run_started", f"Discovery run started ({trigger})", entity_type="discovery_run", entity_id=run_id)
    try:
        from . import entitlements
        cfg = entitlements.system()["discovery"]
        allowed_types = set(cfg.get("enabled_source_types") or [])
        max_per_run = int(cfg.get("max_jobs_per_run") or MAX_PROCESS_PER_RUN)
        companies = [c for c in ps.list_companies() if c["enabled"] and c["ats"] in allowed_types]
        profile = ps.active_profile()
        vp = ps.verified_view(profile) if profile else {}
        prefs = ps.get_preferences()
        terms = _role_terms(prefs, vp)
        processed = 0
        for i, c in enumerate(companies):
            if task and task.cancelled():
                break
            if task:
                task.progress(i / max(1, len(companies)) * 0.8, f"Checking {c['name']} ({c['ats']})")
            stats["sources"] += 1
            listings, err = list_source(c)
            if err:
                stats["source_errors"] += 1
                stats["errors"].append(f"{c['name']}: {err}")
                audit.log("failure", "source_error", f"Source {c['name']} ({c['ats']}) failed: {err}", level="warning",
                          entity_type="company", entity_id=c["id"])
                continue
            stats["listed"] += len(listings)
            seen_ids = {x.get("external_id") for x in listings if x.get("external_id")}
            # Jobs previously found on this board but no longer listed -> closed.
            if c["ats"] in ("greenhouse", "lever", "ashby"):
                for old in db.query("SELECT id, external_id, title FROM jobs WHERE user_id = ? AND source = ? AND company = ? AND page_status = 'active'",
                                    (uid(), c["ats"], c["name"])):
                    if old["external_id"] and old["external_id"] not in seen_ids:
                        db.execute("UPDATE jobs SET page_status = 'closed', page_status_detail = ?, verified_at = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                                   ("No longer listed on the company's official job board", db.now_iso(), db.now_iso(), old["id"], uid()))
                        stats["closed_detected"] += 1
                        pipeline.evaluate(old["id"])
            for x in listings:
                if task and task.cancelled():
                    break
                if processed >= max_per_run:
                    break
                if not _relevant(x.get("title") or "", terms):
                    stats["skipped_irrelevant"] += 1
                    continue
                if _too_old(x.get("posted_at"), prefs.get("max_job_age_days", 45)):
                    stats["skipped_old"] += 1
                    continue
                try:
                    job, dup = pipeline.ingest(x, trigger=f"discovery:{c['name']}")
                    if dup:
                        stats["duplicates"] += 1
                    else:
                        stats["new"] += 1
                    job = pipeline.evaluate(job["id"])
                    if job["status"] == "qualified" and not dup:
                        stats["qualified"] += 1
                    processed += 1
                except Exception as e:
                    stats["errors"].append(f"{x.get('title')}: {e}")
                    audit.log("failure", "processing_error", f"Failed to process '{x.get('title')}': {e}", level="error")
        # Re-check status of recommended / in-review jobs from other sources before they stay recommended.
        if not (task and task.cancelled()):
            stale = db.query("SELECT id, source_url FROM jobs WHERE user_id = ? AND status IN ('qualified','shortlisted','needs_review') "
                             "AND source IN ('career_page','greenhouse','lever','ashby') "
                             "AND (verified_at IS NULL OR verified_at < datetime('now','-20 hours')) LIMIT 20", (uid(),))
            for j in stale:
                if task:
                    task.progress(0.85, "Re-checking recommended jobs are still open")
                ext = jx.extract_url(j["source_url"])
                db.execute("UPDATE jobs SET page_status = ?, page_status_detail = ?, verified_at = ?, updated_at = ? WHERE id = ? AND user_id = ?",
                           (ext["page_status"], ext["page_status_detail"], db.now_iso(), db.now_iso(), j["id"], uid()))
                pipeline.evaluate(j["id"])
                stats["rechecked"] += 1
        status = "cancelled" if task and task.cancelled() else "completed"
        stats["errors"] = stats["errors"][:20]
        db.execute("UPDATE discovery_runs SET status = ?, stats = ?, finished_at = ? WHERE id = ?",
                   (status, db.jdumps(stats), db.now_iso(), run_id))
        msg = (f"Discovery {status}: {stats['listed']} listed, {stats['new']} new, {stats['duplicates']} duplicates, "
               f"{stats['qualified']} newly qualifying, {stats['closed_detected']} closed detected")
        if not companies:
            msg = "Discovery ran but no sources are enabled. Add target companies in Settings → Sources."
        audit.log("discovery", "run_finished", msg, entity_type="discovery_run", entity_id=run_id, details=stats, notify=True, link="/app/discover")
        db.set_user_setting("last_discovery", {"at": db.now_iso(), "trigger": trigger, "stats": stats})
        return stats
    except Exception as e:
        db.execute("UPDATE discovery_runs SET status = 'failed', error = ?, finished_at = ? WHERE id = ?", (str(e)[:500], db.now_iso(), run_id))
        raise


# ------------------------------------------------------------------ scheduler
_stop = threading.Event()


def get_schedule() -> dict:
    s = {"enabled": settings.scheduler_enabled, "hour": settings.discovery_hour}
    s.update(db.get_user_setting("schedule", {}) or {})
    return s


def _scheduler_loop():
    from .. import tasks
    while not _stop.wait(600):
        try:
            now = datetime.now()
            from . import entitlements
            if not entitlements.flag("discovery"):
                continue
            for u in db.query("SELECT * FROM users WHERE status = 'active' AND profile_completed_at IS NOT NULL"):
                with as_user(u):
                    sch = get_schedule()
                    if not sch.get("enabled") or not entitlements.effective(u)["entitlements"].get("automation"):
                        continue
                    last = db.get_user_setting("last_scheduled_discovery", None)
                    if now.hour >= int(sch.get("hour", 7)) and last != now.date().isoformat():
                        db.set_user_setting("last_scheduled_discovery", now.date().isoformat())
                        tasks.submit("discovery", lambda t: run(t, "scheduled"), label="Scheduled discovery")
        except Exception as e:  # never kill the loop
            try:
                audit.log("failure", "scheduler_error", f"Scheduler error: {e}", level="error")
            except Exception:
                pass
        finally:
            db.close_thread_conn()


def start_scheduler() -> None:
    t = threading.Thread(target=_scheduler_loop, name="jobpilot-scheduler", daemon=True)
    t.start()


def stop_scheduler() -> None:
    _stop.set()
