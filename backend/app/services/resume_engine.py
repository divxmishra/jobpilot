"""Truthful resume tailoring + ATS compatibility scoring (ats-v1).

Tailoring may only SELECT, ORDER and use canonical names for facts the user
verified. It never writes new claims. Every bullet keeps a reference to its
source fact. The ATS score is computed on text extracted from the actually
rendered PDF/DOCX, truncated (never rounded up), and reported honestly; a
result below 95 is labelled "Below 95 target – truthful version".
"""
from __future__ import annotations

import math
import re

from .. import audit, db
from ..files import atomic_write, data_path, write_json
from . import documents, evaluation, profile_service as ps, skills as sk
from .textutil import tokens

ATS_VERSION = "ats-v1"
ATS_TARGET = 95.0
ATS_WEIGHTS = {"keywords": 40, "headings": 10, "parseability": 15, "chronology": 10, "formatting": 10, "relevance": 10, "contact": 5}
MAX_ITERATIONS = 3
NUM_RE = re.compile(r"\d+(?:[.,]\d+)?\s*(?:%|x|k|m|\+)?", re.I)


def trunc1(x: float) -> float:
    return math.floor(x * 10 + 1e-9) / 10


def band(score: float) -> str:
    if score >= 95:
        return "Target achieved"
    if score >= 90:
        return "Close to target"
    return "Significant gap"


# ------------------------------------------------------------------ keyword map
def keyword_map(req: dict, vp: dict) -> list[dict]:
    """Classify every JD requirement: directly_evidenced / transferable / not_evidenced / uncertain."""
    ev = evaluation.candidate_skill_evidence(vp)
    have = {k.lower(): k for k in ev}
    all_profile = {}
    out = []
    for kind in ("required_skills", "preferred_skills"):
        for it in req.get(kind, []):
            s = it["skill"]
            row = {"requirement": s, "type": kind.split("_")[0], "jd_evidence": it["evidence"]}
            if s.lower() in have:
                row.update(status="directly_evidenced", resume_evidence=ev[have[s.lower()]][:3])
            else:
                rel = [r for r in sk.related_to(s) if r.lower() in have]
                if rel:
                    row.update(status="transferable", via=rel[0], resume_evidence=ev[have[rel[0].lower()]][:2],
                               note=f"Related experience with {rel[0]}; NOT claimed as {s}")
                else:
                    row.update(status="not_evidenced", resume_evidence=[])
            out.append(row)
    return out


def mark_uncertain(km: list[dict], profile: dict) -> list[dict]:
    """Requirements that appear only among the user's *unverified/uncertain* facts are 'uncertain'."""
    d = profile["data"]
    unverified_skills = {(sk.canonical(s["name"]) or s["name"]).lower() for s in d.get("skills", []) if s.get("status") != "verified"}
    for row in km:
        if row["status"] == "not_evidenced" and row["requirement"].lower() in unverified_skills:
            row["status"] = "uncertain"
            row["note"] = "Present in your resume but not yet verified in your profile"
    return km


# ------------------------------------------------------------------ tailoring
def _relevance(text: str, terms: set[str]) -> int:
    low = text.lower()
    toks = set(tokens(text))
    return sum(1 for t in terms if (t in toks) or (len(t) > 3 and t in low))


def build_content(vp: dict, job: dict, req: dict, *, level: int = 0) -> dict:
    """level 0 = focused selection; higher levels include more verified material (safe improvements)."""
    b = vp.get("basics", {})
    links = [{"label": l["label"], "url": l["url"]} for l in vp.get("links", [])]
    jd_skills = [s["skill"] for s in req.get("required_skills", []) + req.get("preferred_skills", [])]
    jd_terms = {s.lower() for s in jd_skills} | set(req.get("keywords", []))
    ev = evaluation.candidate_skill_evidence(vp)
    have = {k.lower(): k for k in ev}

    # Skills: verified skills only, JD-matching first (using canonical/JD naming for the same skill).
    verified_skills = []
    for s in vp.get("skills", []):
        name = sk.canonical(s["name"]) or s["name"]
        if name.lower() not in [x.lower() for x in verified_skills]:
            verified_skills.append(name)
    # Skills evidenced in verified bullets are also verified facts (e.g. "Docker" used at a job).
    for name in ev:
        if name.lower() not in [x.lower() for x in verified_skills]:
            verified_skills.append(name)
    jd_order = {s.lower(): i for i, s in enumerate(jd_skills)}
    skills_sorted = sorted(verified_skills, key=lambda s: (0 if s.lower() in jd_order else 1, jd_order.get(s.lower(), 999)))
    max_skills = 18 if level == 0 else 26
    skills_out = skills_sorted[:max_skills]

    exp_info = ps.experience_summary(vp)
    matched = [s for s in jd_skills if s.lower() in have][:5]
    if vp.get("summary"):
        summary, summary_source = vp["summary"], "Your verified summary (unchanged)"
    else:
        role = b.get("headline") or (exp_info["titles"][0] if exp_info["titles"] else None)
        parts = []
        if role and exp_info["years"] >= 1:
            parts.append(f"{role} with {exp_info['years']:g} years of professional experience")
        elif role:
            parts.append(role)
        else:
            edu = vp.get("education", [])
            if edu and edu[0].get("degree"):
                parts.append(f"{edu[0]['degree']}{' in ' + edu[0]['field'] if edu[0].get('field') else ''} graduate")
        if matched:
            parts.append(f"skilled in {', '.join(matched)}")
        summary = (" ".join(parts) + ".") if parts else ""
        summary_source = "Composed only from verified facts (title, verified years, verified skills)"

    def pick_bullets(bullets: list[str], ref: str, limit: int) -> list[dict]:
        scored = [(i, _relevance(t, jd_terms), t) for i, t in enumerate(bullets)]
        keep = sorted(scored, key=lambda x: (-x[1], x[0]))[:limit]
        keep.sort(key=lambda x: x[0])  # keep original order for readability
        return [{"text": t, "ref": f"{ref}#{i}"} for i, _, t in keep]

    def exp_key(e):
        end = e.get("end") or ""
        return ("9999" if end == "present" else end, e.get("start") or "")

    exps = sorted(vp.get("experience", []), key=exp_key, reverse=True)
    per_role = 5 if level == 0 else 8
    experience = [{"ref": e["id"], "title": e.get("title"), "company": e.get("company"), "location": e.get("location"),
                   "start": e.get("start"), "end": e.get("end"),
                   "bullets": pick_bullets(e.get("bullets", []), e["id"], per_role)} for e in exps]

    projs = vp.get("projects", [])
    ranked = sorted(projs, key=lambda p: -_relevance(p.get("name", "") + " " + " ".join(p.get("bullets", [])), jd_terms))
    nproj = (3 if exp_info["months"] >= 24 else 4) if level == 0 else 6
    projects = [{"ref": p["id"], "name": p.get("name"), "link": p.get("link"),
                 "bullets": pick_bullets(p.get("bullets", []), p["id"], 4 if level == 0 else 6)} for p in ranked[:nproj]]

    education = [{"ref": e["id"], "degree": e.get("degree"), "field": e.get("field"), "institution": e.get("institution"),
                  "start": e.get("start"), "end": e.get("end"), "grade": e.get("grade")} for e in vp.get("education", [])]
    certs = [{"ref": c["id"], "name": c["name"]} for c in vp.get("certifications", [])]
    achievements = [{"ref": a["id"], "text": a["text"]} for a in vp.get("achievements", [])][: (3 if level == 0 else 8)]

    fresher = exp_info["months"] < 12
    order = ["summary", "skills"] + (["projects", "experience"] if fresher and len(projects) > len(experience) else ["experience", "projects"]) \
        + ["education", "certifications", "achievements"]
    return {"basics": {"name": b.get("name"), "email": b.get("email"), "phone": b.get("phone"), "location": b.get("location"),
                       "headline": b.get("headline"), "links": links},
            "summary": summary, "summary_source": summary_source, "skills": skills_out, "experience": experience,
            "projects": projects, "education": education, "certifications": certs, "achievements": achievements,
            "section_order": order, "level": level}


# ------------------------------------------------------------------ truthfulness / consistency
def consistency_check(content: dict, vp: dict) -> dict:
    """Every claim must trace to a verified fact. Returns {ok, issues[], checked}."""
    issues = []
    ev = evaluation.candidate_skill_evidence(vp)
    allowed_skills = {k.lower() for k in ev} | {(sk.canonical(s["name"]) or s["name"]).lower() for s in vp.get("skills", [])}
    for s in content.get("skills", []):
        if (sk.canonical(s) or s).lower() not in allowed_skills:
            issues.append({"type": "unsupported_skill", "item": s, "message": f"Skill '{s}' is not in your verified profile"})
    facts = {}
    for k in ("experience", "projects"):
        for f in vp.get(k, []):
            facts[f["id"]] = f
    for k in ("experience", "projects"):
        for item in content.get(k, []):
            src = facts.get(item.get("ref"))
            if not src:
                issues.append({"type": "unknown_entry", "item": item.get("title") or item.get("name"),
                               "message": "Entry does not correspond to a verified profile entry"})
                continue
            if k == "experience":
                for f in ("title", "company", "start", "end"):
                    if (item.get(f) or None) != (src.get(f) or None):
                        issues.append({"type": "changed_fact", "item": f"{src.get('title')} @ {src.get('company')}",
                                       "message": f"{f.capitalize()} differs from your verified profile ('{item.get(f)}' vs '{src.get(f)}')"})
            src_bullets = src.get("bullets", [])
            for bl in item.get("bullets", []):
                if bl["text"] in src_bullets:
                    continue
                # Edited bullet: allow wording changes but no new skills, numbers or metrics.
                ref_i = bl.get("ref", "#").split("#")[-1]
                orig = src_bullets[int(ref_i)] if ref_i.isdigit() and int(ref_i) < len(src_bullets) else ""
                new_sk = set(sk.find_skills(bl["text"])) - set(sk.find_skills(orig)) - {x for x in ev}
                new_nums = set(n.strip() for n in NUM_RE.findall(bl["text"])) - set(n.strip() for n in NUM_RE.findall(orig))
                if not orig:
                    issues.append({"type": "new_claim", "item": bl["text"][:80], "message": "New bullet with no source fact in your verified profile"})
                if new_sk:
                    issues.append({"type": "unsupported_skill", "item": bl["text"][:80],
                                   "message": f"Edited text introduces unverified skill(s): {', '.join(sorted(new_sk))}"})
                if new_nums:
                    issues.append({"type": "new_metric", "item": bl["text"][:80],
                                   "message": f"Edited text introduces number(s)/metric(s) not in the source: {', '.join(sorted(new_nums))}"})
    edu_ids = {e["id"]: e for e in vp.get("education", [])}
    for e in content.get("education", []):
        src = edu_ids.get(e.get("ref"))
        if not src:
            issues.append({"type": "unknown_entry", "item": e.get("degree"), "message": "Education entry is not in your verified profile"})
        else:
            for f in ("degree", "institution", "end", "grade"):
                if (e.get(f) or None) != (src.get(f) or None):
                    issues.append({"type": "changed_fact", "item": e.get("degree"), "message": f"Education {f} differs from verified profile"})
    cert_names = {c["name"] for c in vp.get("certifications", [])}
    for c in content.get("certifications", []):
        if c["name"] not in cert_names:
            issues.append({"type": "unsupported_cert", "item": c["name"], "message": "Certification not in your verified profile"})
    vb = vp.get("basics", {})
    for f in ("name", "email", "phone"):
        if content.get("basics", {}).get(f) and content["basics"][f] != vb.get(f):
            issues.append({"type": "changed_fact", "item": f, "message": f"Contact {f} differs from your verified profile"})
    # Summary: numbers/skills in the summary must be supported.
    summ = content.get("summary") or ""
    if summ and summ != (vp.get("summary") or ""):
        bad = set(sk.find_skills(summ)) - {x for x in ev} - {s.lower() for s in []}
        bad = {b for b in bad if b.lower() not in allowed_skills}
        if bad:
            issues.append({"type": "unsupported_skill", "item": "summary", "message": f"Summary mentions unverified skill(s): {', '.join(sorted(bad))}"})
        years = re.findall(r"(\d+(?:\.\d)?)\+?\s*years", summ)
        exp_y = ps.experience_summary(vp)["years"]
        for y in years:
            if float(y) > exp_y + 0.05:
                issues.append({"type": "inflated_experience", "item": "summary",
                               "message": f"Summary claims {y} years; verified experience is {exp_y:g} years"})
    return {"ok": not issues, "issues": issues}


# ------------------------------------------------------------------ ATS scoring
def ats_score(content: dict, req: dict, pdf_bytes: bytes, docx_bytes: bytes, page_count: int) -> dict:
    text = documents.pdf_text(pdf_bytes)
    low = text.lower()
    comps = []
    # 1. Keyword coverage on the rendered text.
    items = [(s["skill"], 2.0, "required") for s in req.get("required_skills", [])] + \
            [(s["skill"], 1.0, "preferred") for s in req.get("preferred_skills", [])]
    skill_names = {s.lower() for s, _, _ in items}
    items += [(k, 0.5, "keyword") for k in req.get("keywords", [])[:15] if k.lower() not in skill_names]
    found_items, missing_items, tot, got = [], [], 0.0, 0.0
    ttoks = set(tokens(text))
    for name, w, kind in items:
        tot += w
        present = bool(sk.find_skills(text, [name]).get(sk.canonical(name) or name)) if kind != "keyword" else \
            (name.lower() in ttoks or name.lower() in low)
        if present:
            got += w
            found_items.append({"term": name, "kind": kind})
        else:
            missing_items.append({"term": name, "kind": kind})
    kw_pts = ATS_WEIGHTS["keywords"] * (got / tot) if tot else ATS_WEIGHTS["keywords"] * 0.5
    comps.append({"id": "keywords", "label": "Keyword coverage (in rendered PDF)", "weight": 40, "points": round(kw_pts, 2),
                  "detail": f"{len(found_items)} of {len(items)} JD terms present" if items else "JD has no identifiable terms; half credit"})
    # 2. Standard headings
    heads = {"summary": "SUMMARY", "skills": "SKILLS", "experience": "EXPERIENCE", "projects": "PROJECTS", "education": "EDUCATION"}
    needed = ["summary", "skills", "education"]
    present = [h for h in needed if heads[h].lower() in low]
    work = "experience" in low or "projects" in low
    hpts = ATS_WEIGHTS["headings"] * (len(present) + (1 if work else 0)) / 4
    comps.append({"id": "headings", "label": "Standard section headings", "weight": 10, "points": round(hpts, 2),
                  "detail": f"Found: {', '.join(p.capitalize() for p in present)}{', Experience/Projects' if work else ''}"})
    # 3. Parseability: compare intended content tokens to extracted tokens; DOCX integrity.
    intended = " ".join([content.get("summary") or "", " ".join(content.get("skills", []))] +
                        [x["text"] for e in content.get("experience", []) for x in e.get("bullets", [])] +
                        [x["text"] for p in content.get("projects", []) for x in p.get("bullets", [])])
    it = set(tokens(intended))
    recovered = len(it & ttoks) / len(it) if it else 1.0
    try:
        dt = set(tokens(documents.docx_text(docx_bytes)))
        docx_ok = len(it & dt) / len(it) >= 0.95 if it else True
    except Exception:
        docx_ok = False
    b = content.get("basics", {})
    name_ok = bool(b.get("name")) and b["name"].lower() in low
    p_pts = ATS_WEIGHTS["parseability"] * (0.6 * min(1.0, recovered / 0.97) + 0.2 * docx_ok + 0.2 * name_ok)
    comps.append({"id": "parseability", "label": "Parsing & file integrity", "weight": 15, "points": round(p_pts, 2),
                  "detail": f"{recovered:.0%} of content recovered from PDF text; DOCX {'readable' if docx_ok else 'problem'}; "
                            f"name {'found' if name_ok else 'not found'} in extracted text"})
    # 4. Chronology
    issues = []
    exps = content.get("experience", [])
    ends = ["9999-99" if e.get("end") == "present" else (e.get("end") or "") for e in exps]
    if ends != sorted(ends, reverse=True):
        issues.append("Experience is not in reverse-chronological order")
    for e in exps:
        if not e.get("start") or not e.get("end"):
            issues.append(f"Missing dates for {e.get('title')}")
        elif e["end"] != "present" and e["end"] < e["start"]:
            issues.append(f"End date before start date for {e.get('title')}")
    fmts = {len((e.get("start") or "").split("-")) for e in exps if e.get("start")}
    if len(fmts) > 1:
        issues.append("Mixed date precision (some month-year, some year-only)")
    c_pts = max(0.0, ATS_WEIGHTS["chronology"] - 4 * len(issues)) if exps else ATS_WEIGHTS["chronology"]
    comps.append({"id": "chronology", "label": "Chronology & date consistency", "weight": 10, "points": c_pts,
                  "detail": "; ".join(issues) if issues else ("Consistent" if exps else "No experience entries (not penalised)")})
    # 5. Formatting
    f_pts = 6 + (4 if page_count <= 2 else 0)
    comps.append({"id": "formatting", "label": "ATS-safe formatting", "weight": 10, "points": f_pts,
                  "detail": f"Single column, no tables/images/icons; {page_count} page(s)" + ("" if page_count <= 2 else " — longer than 2 pages")})
    # 6. Relevance of included bullets
    jd_terms = {s["skill"].lower() for s in req.get("required_skills", []) + req.get("preferred_skills", [])} | set(req.get("keywords", []))
    bullets = [x["text"] for e in exps for x in e.get("bullets", [])] + [x["text"] for p in content.get("projects", []) for x in p.get("bullets", [])]
    rel = sum(1 for t in bullets if _relevance(t, jd_terms) > 0)
    r_pts = ATS_WEIGHTS["relevance"] * (rel / len(bullets)) if bullets else 0
    comps.append({"id": "relevance", "label": "Bullet relevance to this JD", "weight": 10, "points": round(r_pts, 2),
                  "detail": f"{rel} of {len(bullets)} bullets relate to the job's requirements" if bullets else "No bullets"})
    # 7. Contact info
    ct = (2.5 if b.get("email") and b["email"].lower() in low else 0) + (2.5 if (b.get("phone") or b.get("location")) else 0)
    comps.append({"id": "contact", "label": "Contact details", "weight": 5, "points": ct,
                  "detail": "Email and phone/location present" if ct == 5 else "Missing email or phone/location"})
    score = trunc1(sum(c["points"] for c in comps))
    return {"version": ATS_VERSION, "score": score, "band": band(score), "target": ATS_TARGET,
            "target_met": score >= ATS_TARGET,
            "label": "Target achieved" if score >= ATS_TARGET else "Below 95 target – truthful version",
            "components": comps, "found_terms": found_items, "missing_terms": missing_items,
            "disclaimer": "JobPilot's estimated compatibility score — not an employer's ATS score and not a guarantee of passing screening."}


def improvement_advice(ats: dict, km: list[dict]) -> list[str]:
    tips = []
    for row in km:
        if row["status"] == "not_evidenced" and row["type"] == "required":
            tips.append(f"'{row['requirement']}' is required but not in your verified profile. Only if you genuinely have it, add it "
                        f"to your profile with evidence (Profile → Start update), then regenerate.")
        elif row["status"] == "not_evidenced":
            tips.append(f"'{row['requirement']}' (preferred) is not in your verified profile. It is listed in the skill-gap report, "
                        f"not on the resume.")
        elif row["status"] == "uncertain":
            tips.append(f"'{row['requirement']}' appears in your resume but is unverified. Verify it in your profile if accurate.")
        elif row["status"] == "transferable":
            tips.append(f"'{row['requirement']}': you have related experience ({row['via']}). Mention it in your cover letter or "
                        f"interview; it is not claimed on the resume.")
    for c in ats["components"]:
        if c["id"] == "chronology" and c["points"] < c["weight"]:
            tips.append("Fix date issues in your profile (e.g. add months to year-only dates) — " + c["detail"])
        if c["id"] == "formatting" and c["points"] < c["weight"]:
            tips.append("The resume exceeds 2 pages; consider removing older or less relevant bullets in the editor.")
    return tips[:12]


# ------------------------------------------------------------------ orchestration
def _render_and_score(content: dict, req: dict) -> tuple[bytes, bytes, int, dict]:
    pdf, pages = documents.render_pdf(content)
    docx_b = documents.render_docx(content)
    return pdf, docx_b, pages, ats_score(content, req, pdf, docx_b, pages)


def _store(job: dict, profile: dict, content: dict, km: list, ats: dict, cons: dict, pdf: bytes, docx_b: bytes, pages: int,
           parent_id: int | None, edited: bool) -> dict:
    jid = job["id"]
    v = (db.query_one("SELECT MAX(version) v FROM resumes WHERE job_id = ?", (jid,))["v"] or 0) + 1
    base = data_path("resumes", jid)
    pdf_p, docx_p = base / f"resume_v{v}.pdf", base / f"resume_v{v}.docx"
    atomic_write(pdf_p, pdf)
    atomic_write(docx_p, docx_b)
    now = db.now_iso()
    with db.tx() as conn:
        conn.execute("UPDATE resumes SET status = 'superseded', updated_at = ? WHERE job_id = ? AND status = 'draft'", (now, jid))
        cur = conn.execute(
            "INSERT INTO resumes(job_id, profile_version_id, version, status, content, keyword_map, ats, ats_score, consistency, "
            "pdf_path, docx_path, page_count, edited, parent_id, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (jid, profile["id"], v, "draft", db.jdumps(content), db.jdumps(km), db.jdumps(ats), ats["score"], db.jdumps(cons),
             str(pdf_p.relative_to(data_path())), str(docx_p.relative_to(data_path())), pages, 1 if edited else 0, parent_id, now, now))
        rid = cur.lastrowid
    write_reports(job, v, km, ats, cons)
    return get_resume(rid)


def write_reports(job: dict, v: int, km: list, ats: dict, cons: dict) -> None:
    d = data_path("reports", job["id"])
    L = [f"# ATS report v{v} — {job.get('title')} at {job.get('company')}", "", f"**Score:** {ats['score']}/100 — {ats['label']} ({ats['band']})",
         "", f"> {ats['disclaimer']}", "", "| Component | Weight | Points | Detail |", "|---|---|---|---|"]
    L += [f"| {c['label']} | {c['weight']} | {c['points']} | {c['detail']} |" for c in ats["components"]]
    L += ["", "## Missing JD terms"] + [f"- {m['term']} ({m['kind']})" for m in ats["missing_terms"]]
    L += ["", "## Truthfulness check", "Passed — every claim traces to a verified fact." if cons["ok"] else "Issues found:"]
    L += [f"- {i['message']}" for i in cons["issues"]]
    atomic_write(d / f"ats_v{v}.md", "\n".join(L) + "\n")
    G = [f"# Skill-gap report v{v} — {job.get('title')} at {job.get('company')}", "",
         "These are NOT claimed on the resume.", ""]
    for row in km:
        if row["status"] != "directly_evidenced":
            G.append(f"- **{row['requirement']}** ({row['type']}): {row['status'].replace('_', ' ')}"
                     + (f" — {row.get('note')}" if row.get("note") else ""))
    atomic_write(d / f"skill_gap_v{v}.md", "\n".join(G) + "\n")
    write_json(d / f"keyword_map_v{v}.json", km)


def generate(job_id: str, task=None) -> dict:
    from .pipeline import get_job
    job = get_job(job_id)
    if not job:
        raise LookupError("Job not found")
    # Early decision gate: never spend effort tailoring for ineligible / below-threshold jobs.
    if job["status"] not in ("qualified", "shortlisted"):
        raise PermissionError(f"Tailoring is only available for qualifying jobs (hard gates passed and match ≥ threshold). "
                              f"This job is '{job['status_label']}'.")
    profile = ps.active_profile()
    if not profile or profile["id"] != job["profile_version_id"]:
        raise PermissionError("The job was evaluated against a different profile version. Re-evaluate it first.")
    vp = ps.verified_view(profile)
    req = job["requirements"]
    km = mark_uncertain(keyword_map(req, vp), profile)
    best = None
    for level in range(MAX_ITERATIONS):
        if task and task.cancelled():
            break
        if task:
            task.progress(0.15 + 0.25 * level, f"Generating truthful draft (pass {level + 1})")
        content = build_content(vp, job, req, level=level)
        cons = consistency_check(content, vp)
        if not cons["ok"]:  # should be impossible for generated content; never store unsupported output
            audit.log("failure", "truthfulness_violation", "Generated content failed truthfulness check; discarded",
                      level="error", entity_type="job", entity_id=job_id, details=cons)
            continue
        pdf, docx_b, pages, ats = _render_and_score(content, req)
        if best is None or ats["score"] > best[4]["score"]:
            best = (content, pdf, docx_b, pages, ats, cons)
        if ats["score"] >= ATS_TARGET:
            break
    if best is None:
        raise RuntimeError("Could not generate a truthful resume draft")
    content, pdf, docx_b, pages, ats, cons = best
    ats["advice"] = improvement_advice(ats, km)
    r = _store(job, profile, content, km, ats, cons, pdf, docx_b, pages, None, False)
    audit.log("document", "resume_generated",
              f"Tailored resume v{r['version']} for '{job['title']}' — ATS {ats['score']}/100 ({ats['label']})",
              entity_type="resume", entity_id=r["id"], details={"job_id": job_id, "iterations": content["level"] + 1},
              notify=True, link=f"/resumes/{r['id']}")
    return r


def save_edit(resume_id: int, content: dict) -> dict:
    from .pipeline import get_job
    r = get_resume(resume_id)
    if not r:
        raise LookupError("Resume not found")
    if r["status"] != "draft":
        raise PermissionError("Only drafts can be edited. Approved versions are kept unchanged.")
    profile = ps.get_version(r["profile_version_id"])
    vp = ps.verified_view(profile)
    job = get_job(r["job_id"])
    clean = _sanitize_content(content, r["content"])
    cons = consistency_check(clean, vp)
    pdf, docx_b, pages, ats = _render_and_score(clean, job["requirements"])
    km = r["keyword_map"]
    ats["advice"] = improvement_advice(ats, km)
    new = _store(job, profile, clean, km, ats, cons, pdf, docx_b, pages, resume_id, True)
    audit.log("document", "resume_edited", f"User edited resume draft → v{new['version']} (ATS {ats['score']}); "
              f"truthfulness {'passed' if cons['ok'] else 'FAILED: ' + str(len(cons['issues'])) + ' issue(s)'}",
              entity_type="resume", entity_id=new["id"], level="info" if cons["ok"] else "warning")
    return new


def _sanitize_content(new: dict, old: dict) -> dict:
    """Accept only known structure from the editor; keep refs from the original."""
    out = dict(old)
    if isinstance(new.get("summary"), str):
        out["summary"] = new["summary"].strip()[:1500]
    if isinstance(new.get("skills"), list):
        out["skills"] = [str(s).strip()[:60] for s in new["skills"] if str(s).strip()][:40]
    for k in ("experience", "projects"):
        if isinstance(new.get(k), list):
            items = []
            for it in new[k]:
                if not isinstance(it, dict):
                    continue
                clean = {f: it.get(f) for f in ("ref", "title", "company", "location", "start", "end", "name", "link") if f in it}
                clean["bullets"] = [{"text": str(b.get("text", "")).strip()[:600], "ref": str(b.get("ref", ""))}
                                    for b in it.get("bullets", []) if isinstance(b, dict) and str(b.get("text", "")).strip()][:12]
                items.append(clean)
            out[k] = items
    if isinstance(new.get("section_order"), list):
        allowed = {"summary", "skills", "experience", "projects", "education", "certifications", "achievements"}
        order = [s for s in new["section_order"] if s in allowed]
        out["section_order"] = order + [s for s in old.get("section_order", []) if s not in order]
    return out


def approve(resume_id: int) -> dict:
    r = get_resume(resume_id)
    if not r:
        raise LookupError("Resume not found")
    if r["status"] != "draft":
        raise ValueError("Only drafts can be approved")
    if not r["consistency"]["ok"]:
        raise PermissionError("This draft contains claims not supported by your verified profile. Fix them, or add the facts to "
                              "your profile with evidence through a profile update, before approving.")
    now = db.now_iso()
    with db.tx() as conn:
        conn.execute("UPDATE resumes SET status = 'superseded', updated_at = ? WHERE job_id = ? AND status = 'approved'", (now, r["job_id"]))
        conn.execute("UPDATE resumes SET status = 'approved', approved_at = ?, updated_at = ? WHERE id = ?", (now, now, resume_id))
    audit.log("decision", "resume_approved", f"User approved resume v{r['version']} (ATS {r['ats_score']}). Approval does not submit anything.",
              entity_type="resume", entity_id=resume_id)
    return get_resume(resume_id)


def reject(resume_id: int) -> dict:
    r = get_resume(resume_id)
    if not r or r["status"] != "draft":
        raise ValueError("Only drafts can be discarded")
    db.execute("UPDATE resumes SET status = 'superseded', updated_at = ? WHERE id = ?", (db.now_iso(), resume_id))
    audit.log("decision", "resume_discarded", f"User discarded resume draft v{r['version']}", entity_type="resume", entity_id=resume_id)
    return get_resume(resume_id)


def get_resume(rid: int) -> dict | None:
    r = db.query_one("SELECT * FROM resumes WHERE id = ?", (rid,))
    if not r:
        return None
    for k in ("content", "keyword_map", "ats", "consistency"):
        r[k] = db.jloads(r[k], {} if k != "keyword_map" else [])
    return r


def list_resumes(job_id: str | None = None) -> list[dict]:
    sql = ("SELECT r.id, r.job_id, r.version, r.status, r.ats_score, r.page_count, r.edited, r.created_at, r.approved_at, "
           "r.consistency, j.title, j.company FROM resumes r JOIN jobs j ON j.id = r.job_id")
    params: tuple = ()
    if job_id:
        sql += " WHERE r.job_id = ?"
        params = (job_id,)
    rows = db.query(sql + " ORDER BY r.created_at DESC, r.id DESC", params)
    for r in rows:
        r["truthful"] = db.jloads(r.pop("consistency"), {}).get("ok", False)
    return rows


# ------------------------------------------------------------------ draft application answers
def draft_answers(job: dict, vp: dict) -> list[dict]:
    exp = ps.experience_summary(vp)
    b = vp.get("basics", {})
    m = job.get("match") or {}
    matched = [s["skill"] for s in m.get("matched_skills", [])][:6]
    links = {l["label"].lower(): l["url"] for l in vp.get("links", [])}
    edu = vp.get("education", [])
    return [
        {"question": "Full name", "answer": b.get("name") or "", "source": "Verified profile"},
        {"question": "Email", "answer": b.get("email") or "", "source": "Verified profile"},
        {"question": "Phone", "answer": b.get("phone") or "", "source": "Verified profile"},
        {"question": "Current location", "answer": b.get("location") or "", "source": "Verified profile"},
        {"question": "LinkedIn", "answer": links.get("linkedin", ""), "source": "Verified profile"},
        {"question": "GitHub / portfolio", "answer": links.get("github", links.get("portfolio", "")), "source": "Verified profile"},
        {"question": "Total professional experience (years)", "answer": f"{exp['years']:g}", "source": "Computed from verified dated positions"},
        {"question": "Highest qualification", "answer": (f"{edu[0].get('degree') or ''} {('in ' + edu[0]['field']) if edu[0].get('field') else ''}, "
                                                         f"{edu[0].get('institution') or ''}").strip(", ") if edu else "",
         "source": "Verified profile"},
        {"question": "Relevant skills for this role", "answer": ", ".join(matched), "source": "Verified skills matching this JD"},
        {"question": "Why are you interested in this role?",
         "answer": (f"My verified experience with {', '.join(matched[:3])} aligns with this {job.get('title')} role. "
                    "[Add your genuine personal motivation for joining " + (job.get("company") or "this company") + "]") if matched else
         "[Write your own answer]", "source": "Draft — needs your personal input"},
        {"question": "Notice period / earliest start date", "answer": "[You to fill]", "source": "Not in profile — never guessed"},
        {"question": "Expected salary", "answer": "[You to fill]", "source": "Not in profile — never guessed"},
    ]
