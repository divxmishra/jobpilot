"""Career Roadmap: turn recurring missing requirements in the user's own recent jobs
into a ranked, evidence-based learning plan.

Every count comes from the user's stored job evaluations. "Unlock" figures are
estimates produced by re-running the real eligibility + match engine with the
skill hypothetically added to the verified profile; a job only counts if it would
then pass every other hard gate AND reach the threshold. Nothing is fabricated.
"""
from __future__ import annotations

import copy
from collections import defaultdict
from datetime import datetime, timezone

from .. import db
from ..context import uid
from . import evaluation, profile_service as ps, skills as sk
from .textutil import tokens

MAX_JOBS = 50
MIN_JOBS = 30
STATUSES = ("not_started", "learning", "practiced", "proficient")
EFFORT = {  # rough part-time estimates by skill category (clearly labelled as estimates in the UI)
    "language": "6–10 weeks", "frontend": "3–6 weeks", "backend": "3–6 weeks", "mobile": "6–10 weeks", "database": "2–4 weeks",
    "cloud": "4–8 weeks", "devops": "2–5 weeks", "data": "3–6 weeks", "ml": "8–12 weeks", "tools": "1–2 weeks",
    "practice": "3–6 weeks", "design": "3–6 weeks", "product": "4–8 weeks", "business": "3–6 weeks", "marketing": "3–6 weeks",
    "sales": "3–6 weeks", "finance": "4–8 weeks", "hr": "3–6 weeks", "operations": "3–6 weeks", "engineering": "6–10 weeks",
    "security": "6–10 weeks", "soft": "ongoing practice", "healthcare": "varies (may need formal training)",
    "education": "varies", "custom": "varies",
}
PROJECTS = {
    "language": "Rebuild one of your existing projects (or a small CLI tool) in {s}, with tests and a README.",
    "frontend": "Build a small responsive app in {s} that consumes a public API, then deploy it for free.",
    "backend": "Create a REST API in {s} with authentication, validation and automated tests.",
    "database": "Model a realistic schema in {s}, write the queries for 10 business questions, and add indexes.",
    "cloud": "Deploy one of your projects on {s} using its free tier, with logging and a budget alert.",
    "devops": "Add {s} to one of your existing projects (e.g. containerise it or automate its build/deploy).",
    "data": "Analyse a public dataset with {s} and publish the notebook/report with clear findings.",
    "ml": "Train and evaluate a small model with {s} on a public dataset; document metrics and limitations.",
    "tools": "Use {s} in your next project for a few weeks and note the workflow you adopted.",
    "practice": "Apply {s} deliberately in a small project and write up what you did.",
}
PREREQS = {
    "Kubernetes": ["Docker"], "Django": ["Python"], "Flask": ["Python"], "FastAPI": ["Python"], "Pandas": ["Python"],
    "NumPy": ["Python"], "Scikit-learn": ["Python", "Pandas"], "TensorFlow": ["Python"], "PyTorch": ["Python"],
    "React": ["JavaScript"], "Next.js": ["React"], "Vue.js": ["JavaScript"], "Angular": ["TypeScript"], "TypeScript": ["JavaScript"],
    "Node.js": ["JavaScript"], "Express": ["Node.js"], "Spring Boot": ["Java"], "Apache Spark": ["Python", "SQL"],
    "Airflow": ["Python"], "dbt": ["SQL"], "Terraform": ["AWS"], "Power BI": ["Excel"], "Tableau": ["Excel"],
    "Redux": ["React"], "Deep Learning": ["Machine Learning"], "Natural Language Processing": ["Machine Learning"],
}


def _key(name: str) -> str:
    return (sk.canonical(name) or name).strip().lower()


def _display(name: str) -> str:
    return sk.canonical(name) or name.strip()


def _recent_jobs(role: str | None) -> list[dict]:
    rows = db.query("SELECT id, title, company, status, match, requirements, eligibility, page_status, user_confirmations, "
                    "work_mode, employment_type, location, posted_at, salary_max, salary_currency, seniority, description, "
                    "extraction_complete, missing_fields FROM jobs WHERE user_id = ? AND match IS NOT NULL "
                    "ORDER BY created_at DESC LIMIT ?", (uid(), MAX_JOBS))
    out = []
    role_toks = {t for t in tokens(role or "")}
    for r in rows:
        if role_toks and not (role_toks & set(tokens(r["title"] or ""))):
            continue
        for k, d in (("match", {}), ("requirements", {}), ("eligibility", {}), ("user_confirmations", {}), ("missing_fields", [])):
            r[k] = db.jloads(r[k], d)
        out.append(r)
    return out


def progress_map() -> dict[str, dict]:
    return {r["skill"].lower(): r for r in db.query("SELECT * FROM skill_progress WHERE user_id = ?", (uid(),))}


def set_progress(skill: str, status: str) -> dict:
    if status not in STATUSES:
        raise ValueError("Status must be not_started, learning, practiced or proficient")
    name = _display(skill)[:60]
    if not name:
        raise ValueError("Skill is required")
    db.execute("INSERT INTO skill_progress(user_id, skill, status, updated_at) VALUES (?,?,?,?) "
               "ON CONFLICT(user_id, skill) DO UPDATE SET status = excluded.status, updated_at = excluded.updated_at",
               (uid(), name, status, db.now_iso()))
    return {"skill": name, "status": status}


def _simulate_unlocks(jobs: list[dict], skill_names: list[str], vp: dict, prefs: dict, thr: float) -> list[str]:
    """Job ids that would become qualifying if ALL skill_names were verified (other gates unchanged)."""
    sim = copy.deepcopy(vp)
    have = {_key(s["name"]) for s in sim.get("skills", [])}
    for n in skill_names:
        if _key(n) not in have:
            sim.setdefault("skills", []).append({"name": _display(n), "status": "verified", "evidence": ["hypothetical"]})
    unlocked = []
    for j in jobs:
        if j["status"] in ("qualified", "shortlisted", "expired", "dismissed") or j["page_status"] in ("closed", "expired", "not_found"):
            continue
        req = j["requirements"] or {}
        if not req:
            continue
        before = {s["skill"].lower() for s in (j["match"].get("missing_required", []) + j["match"].get("missing_preferred", []))}
        if not ({_key(n) for n in skill_names} & {_key(b) for b in before}):
            continue
        elig = evaluation.evaluate_gates(j, req, sim, prefs, j["user_confirmations"])
        match = evaluation.compute_match(j, req, sim, prefs)
        status, _ = evaluation.decide(j, elig, match, prefs)
        if status == "qualified" and match["score"] >= thr:
            unlocked.append(j["id"])
    return unlocked


def build(role: str | None = None, include_unlocks: bool = True) -> dict:
    jobs = _recent_jobs(role)
    profile = ps.active_profile()
    vp = ps.verified_view(profile) if profile else {}
    prefs = ps.get_preferences()
    thr = float(prefs.get("match_threshold", 85))
    target_roles = prefs.get("target_roles") or []
    target_toks = set()
    for r in target_roles:
        target_toks |= set(tokens(r))
    unverified = set()
    if profile:
        unverified = {_key(s["name"]) for s in profile["data"].get("skills", []) if s.get("status") != "verified"}
    verified = {_key(k) for k in evaluation.candidate_skill_evidence(vp)} if vp else set()

    agg: dict[str, dict] = {}
    for j in jobs:
        m = j["match"] or {}
        req = j["requirements"] or {}
        role_relevant = bool(target_toks & set(tokens(j["title"] or "")))
        not_qualifying = j["status"] not in ("qualified", "shortlisted")
        mentioned = {_key(x["skill"]) for x in req.get("required_skills", []) + req.get("preferred_skills", []) + req.get("mentioned_skills", [])}
        for kind, items in (("required", m.get("missing_required", [])), ("preferred", m.get("missing_preferred", []))):
            for it in items:
                k = _key(it["skill"])
                if k in verified:
                    continue
                a = agg.setdefault(k, {"skill": _display(it["skill"]), "required_in": set(), "preferred_in": set(), "jobs_missing": set(),
                                        "not_qualifying_jobs": set(), "role_relevant": set(), "titles": defaultdict(int),
                                        "evidence": [], "transferable_via": None})
                (a["required_in"] if kind == "required" else a["preferred_in"]).add(j["id"])
                a["jobs_missing"].add(j["id"])
                if not_qualifying:
                    a["not_qualifying_jobs"].add(j["id"])
                if role_relevant:
                    a["role_relevant"].add(j["id"])
                a["titles"][j["title"] or "Untitled role"] += 1
                if len(a["evidence"]) < 3 and it.get("jd_evidence"):
                    a["evidence"].append({"job": f"{j['title']} — {j['company'] or ''}", "quote": it["jd_evidence"][:160]})
        for t in m.get("transferable", []):
            k = _key(t["skill"])
            if k in agg:
                agg[k]["transferable_via"] = t.get("via")
        for k in mentioned:
            if k in agg:
                agg[k].setdefault("mentioned_in", set()).add(j["id"])

    progress = progress_map()
    items = []
    for k, a in agg.items():
        cat = sk.category(a["skill"])
        req_n, pref_n = len(a["required_in"]), len(a["preferred_in"])
        mention_n = len(a.get("mentioned_in", set()) | a["jobs_missing"])
        classification = "unverified" if k in unverified else ("transferable" if a["transferable_via"] else "missing")
        score = 3 * req_n + pref_n + 1.5 * len(a["role_relevant"]) + 0.5 * mention_n
        items.append({
            "skill": a["skill"], "key": k, "category": cat, "classification": classification,
            "jobs_mentioning": mention_n, "required_in": req_n, "preferred_in": pref_n,
            "jobs_not_qualifying": len(a["not_qualifying_jobs"]), "role_relevant_jobs": len(a["role_relevant"]),
            "relevant_roles": [t for t, _ in sorted(a["titles"].items(), key=lambda x: -x[1])[:3]],
            "transferable_via": a["transferable_via"], "evidence": a["evidence"], "score": round(score, 1),
            "status": (progress.get(a["skill"].lower()) or {}).get("status", "not_started"),
            "effort_estimate": EFFORT.get(cat, "varies"),
            "project": PROJECTS.get(cat, "Use {s} in a small, real project and document what you built.").format(s=a["skill"]),
        })
    items.sort(key=lambda x: (-x["score"], -x["jobs_mentioning"], x["skill"]))
    n = len(items)
    for i, it in enumerate(items):
        it["priority"] = "High" if i < max(1, n // 4) or it["required_in"] >= 3 else ("Medium" if i < max(2, n // 2) else "Low")
        parts = []
        if it["required_in"]:
            parts.append(f"required in {it['required_in']} of your {len(jobs)} recent jobs")
        if it["preferred_in"]:
            parts.append(f"preferred in {it['preferred_in']}")
        if it["role_relevant_jobs"] and target_roles:
            parts.append(f"{it['role_relevant_jobs']} of those match your target roles")
        it["why"] = f"{it['skill']} is " + ", ".join(parts) + "." if parts else f"{it['skill']} appears in your recent jobs."
        it["insight"] = (f"{it['skill']} appears in {it['jobs_not_qualifying']} relevant job(s) you currently do not qualify for."
                         if it["jobs_not_qualifying"] else None)

    # Learning order: priority order, but prerequisites (also in the list) come first.
    order, placed = [], set()
    by_name = {it["skill"]: it for it in items}

    def place(it):
        if it["skill"] in placed:
            return
        for pre in PREREQS.get(it["skill"], []):
            if pre in by_name:
                place(by_name[pre])
        placed.add(it["skill"])
        order.append(it["skill"])

    for it in items[:15]:
        place(it)
    for i, s in enumerate(order):
        by_name[s]["learning_order"] = i + 1

    unlock = {"available": include_unlocks, "per_skill": {}, "combined": None}
    if include_unlocks and jobs and vp:
        for it in items[:10]:
            ids = _simulate_unlocks(jobs, [it["skill"]], vp, prefs, thr)
            it["unlock_estimate"] = len(ids)
            unlock["per_skill"][it["skill"]] = len(ids)
        top = [it["skill"] for it in items[:3] if it["classification"] != "unverified"]
        if len(top) >= 2:
            ids = _simulate_unlocks(jobs, top, vp, prefs, thr)
            unlock["combined"] = {"skills": top, "jobs": len(ids)}

    status_counts = {s: sum(1 for it in items if it["status"] == s) for s in STATUSES}
    result = {"jobs_analyzed": len(jobs), "window": f"most recent {MAX_JOBS}" if len(jobs) >= MIN_JOBS else "all available",
              "threshold": thr, "target_roles": target_roles, "role_filter": role, "skills": items[:30],
              "unlock": unlock, "status_counts": status_counts, "has_profile": bool(profile),
              "generated_at": datetime.now(timezone.utc).replace(microsecond=0).isoformat(),
              "disclaimer": "Estimates based only on your recent scanned jobs. Learning a skill does not guarantee eligibility, "
                            "an interview or a job; other requirements may still apply. Effort estimates are rough."}
    _snapshot(result)
    return result


def _snapshot(result: dict) -> None:
    today = datetime.now(timezone.utc).date().isoformat()
    if db.query_one("SELECT id FROM roadmap_snapshots WHERE user_id = ? AND substr(at,1,10) = ?", (uid(), today)):
        db.execute("DELETE FROM roadmap_snapshots WHERE user_id = ? AND substr(at,1,10) = ?", (uid(), today))
    data = {"top": [{"skill": s["skill"], "jobs": s["jobs_mentioning"], "status": s["status"]} for s in result["skills"][:10]],
            "status_counts": result["status_counts"]}
    db.execute("INSERT INTO roadmap_snapshots(user_id, at, jobs_analyzed, data) VALUES (?,?,?,?)",
               (uid(), db.now_iso(), result["jobs_analyzed"], db.jdumps(data)))


def history(limit: int = 60) -> list[dict]:
    rows = db.query("SELECT at, jobs_analyzed, data FROM roadmap_snapshots WHERE user_id = ? ORDER BY at DESC LIMIT ?", (uid(), limit))
    for r in rows:
        r["data"] = db.jloads(r["data"], {})
    return list(reversed(rows))
