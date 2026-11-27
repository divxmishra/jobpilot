"""Hard eligibility gates + reproducible resume-to-JD match rubric (match-v1).

See docs/SCORING_RUBRIC.md. Key rules:
- Gates are evaluated first; a failed gate can never be overridden by a high score.
- The match score uses only *verified* profile facts.
- Scores are truncated (never rounded up) to one decimal.
- Jobs whose JD is too thin to score reliably are sent to review, never recommended.
"""
from __future__ import annotations

import math
import re
from datetime import date, datetime

from . import skills as sk
from .profile_service import experience_summary, highest_education
from .textutil import tokens

RUBRIC_VERSION = "match-v1"
WEIGHTS = {"required_skills": 35, "preferred_skills": 10, "keywords": 15, "experience": 15,
           "seniority": 10, "education": 5, "domain": 5, "preferences": 5}
SENIORITY_LEVEL = {"intern": 0, "entry": 0, "mid": 2, "senior": 3, "lead": 4, "director": 4}
TRANSFERABLE_CREDIT = 0.25
EDU_NAMES = {0: "none stated", 1: "higher secondary", 2: "diploma", 3: "bachelor's degree", 4: "master's degree", 5: "doctorate"}


def trunc1(x: float) -> float:
    return math.floor(x * 10 + 1e-9) / 10


def _stem(w: str) -> str:
    for suf in ("ing", "ers", "er", "ed", "es", "s"):
        if len(w) > 5 and w.endswith(suf):
            return w[: -len(suf)]
    return w


# ------------------------------------------------------------------ candidate evidence
def candidate_skill_evidence(vp: dict) -> dict[str, list[str]]:
    """Map canonical skill -> evidence strings, from verified facts only."""
    ev: dict[str, list[str]] = {}
    custom = [s["name"] for s in vp.get("skills", [])]
    for s in vp.get("skills", []):
        name = sk.canonical(s["name"]) or s["name"]
        ev.setdefault(name, []).append("Verified skill: " + "; ".join(s.get("evidence") or ["confirmed by you"])[:160])
    for e in vp.get("experience", []):
        blob = " ".join(e.get("bullets", [])) + " " + (e.get("title") or "")
        for s in sk.find_skills(blob, custom):
            ev.setdefault(s, []).append(f"Used at {e.get('company') or 'employer'} ({e.get('title') or 'role'})")
    for p in vp.get("projects", []):
        for s in sk.find_skills((p.get("name") or "") + " " + " ".join(p.get("bullets", [])), custom):
            ev.setdefault(s, []).append(f"Project: {p.get('name')}")
    for c in vp.get("certifications", []):
        for s in sk.find_skills(c.get("name", ""), custom):
            ev.setdefault(s, []).append(f"Certification: {c.get('name')}")
    return ev


def candidate_text(vp: dict) -> str:
    parts = [vp.get("summary") or "", vp.get("basics", {}).get("headline") or ""]
    parts += [s["name"] for s in vp.get("skills", [])]
    for e in vp.get("experience", []):
        parts += [e.get("title") or "", *e.get("bullets", [])]
    for p in vp.get("projects", []):
        parts += [p.get("name") or "", *p.get("bullets", [])]
    parts += [c.get("name", "") for c in vp.get("certifications", [])]
    parts += [a.get("text", "") for a in vp.get("achievements", [])]
    parts += [f"{e.get('degree') or ''} {e.get('field') or ''}" for e in vp.get("education", [])]
    return "\n".join(parts)


# ------------------------------------------------------------------ hard gates
def _gate(gid, label, result, detail, evidence=None):
    return {"id": gid, "label": label, "result": result, "detail": detail, "evidence": evidence}


def _loc_match(job_loc: str, prefs_locs: list[str]) -> bool:
    jl = (job_loc or "").lower()
    return any(p.lower() in jl or (jl and jl.split(",")[0].strip() in p.lower()) for p in prefs_locs if p.strip())


def evaluate_gates(job: dict, req: dict, vp: dict, prefs: dict, confirmations: dict | None = None) -> dict:
    confirmations = confirmations or {}
    gates = []
    exp = experience_summary(vp)
    ex = prefs.get("exclusions", {})

    # 1. Posting status
    ps = job.get("page_status")
    if ps == "active":
        gates.append(_gate("posting_status", "Posting is open", "pass", job.get("page_status_detail") or "Verified open"))
    elif ps in ("closed", "expired", "not_found"):
        gates.append(_gate("posting_status", "Posting is open", "fail", job.get("page_status_detail") or f"Posting is {ps}"))
    else:
        gates.append(_gate("posting_status", "Posting is open", "needs_verification",
                           job.get("page_status_detail") or "Could not verify that the posting is open"))

    # 2. Freshness
    max_age = prefs.get("max_job_age_days", 45)
    if job.get("posted_at"):
        try:
            posted = datetime.fromisoformat(job["posted_at"].replace("Z", "+00:00")).date()
            age = (date.today() - posted).days
            gates.append(_gate("freshness", f"Posted within {max_age} days", "pass" if age <= max_age else "fail",
                               f"Posted {age} day(s) ago ({posted.isoformat()})"))
        except ValueError:
            gates.append(_gate("freshness", f"Posted within {max_age} days", "pass", "Posting date format not recognised; age unknown"))
    else:
        gates.append(_gate("freshness", f"Posted within {max_age} days", "pass", "The source did not state a posting date (not assumed)"))

    # 3. Experience requirement
    e = req.get("experience", {})
    if e.get("min_years") is not None and e.get("mandatory"):
        need = e["min_years"]
        if exp["months"] == 0 and exp["undated_positions"]:
            gates.append(_gate("experience", f"At least {need:g} years' experience", "needs_verification",
                               "Some positions have no verified dates, so total experience is uncertain", e.get("evidence")))
        elif exp["years"] >= need:
            gates.append(_gate("experience", f"At least {need:g} years' experience", "pass",
                               f"Verified experience: {exp['years']} years{'' if exp['precise'] else ' (some dates are year-only)'}", e.get("evidence")))
        elif not exp["precise"] and exp["years"] + 1 >= need:
            gates.append(_gate("experience", f"At least {need:g} years' experience", "needs_verification",
                               f"Verified experience ≈ {exp['years']} years but some dates are year-only", e.get("evidence")))
        else:
            gates.append(_gate("experience", f"At least {need:g} years' experience", "fail",
                               f"Verified experience is {exp['years']} years; the posting requires {need:g}+", e.get("evidence")))
    else:
        gates.append(_gate("experience", "Mandatory experience requirement", "pass",
                           "No mandatory minimum experience stated" if e.get("min_years") is None
                           else f"{e['min_years']:g}+ years mentioned as a preference, not a requirement", e.get("evidence")))

    # 4. Education requirement
    ed = req.get("education", {})
    if ed.get("min_level") and ed.get("mandatory"):
        have = highest_education(vp)
        label = f"Requires {EDU_NAMES.get(ed['min_level'], 'degree')}"
        if not vp.get("education"):
            gates.append(_gate("education", label, "needs_verification", "No verified education entries in your profile", ed.get("evidence")))
        elif have >= ed["min_level"]:
            gates.append(_gate("education", label, "pass", f"Your highest verified qualification: {EDU_NAMES[have]}", ed.get("evidence")))
        elif ed.get("equivalent_ok") and exp["years"] >= 2:
            gates.append(_gate("education", label, "pass",
                               f"The posting accepts equivalent experience; you have {exp['years']} verified years", ed.get("evidence")))
        else:
            gates.append(_gate("education", label, "fail", f"Your highest verified qualification is {EDU_NAMES[have]}", ed.get("evidence")))

    # 5. Work authorisation
    wa = req.get("work_authorization", {})
    auth_countries = [c.lower() for c in prefs.get("work_authorization_countries", [])]
    if wa.get("required") or wa.get("no_sponsorship"):
        countries = wa.get("countries") or []
        if not auth_countries:
            gates.append(_gate("work_authorization", "Work authorisation", "needs_verification",
                               "The posting requires work authorisation; add the countries you are authorised to work in (Settings → Preferences)",
                               wa.get("evidence")))
        elif countries and not any(any(a in c.lower() or c.lower() in a for a in auth_countries) for c in countries):
            gates.append(_gate("work_authorization", "Work authorisation", "fail",
                               f"Requires authorisation for {', '.join(countries)}; you listed {', '.join(prefs['work_authorization_countries'])}",
                               wa.get("evidence")))
        elif not countries:
            loc = (job.get("location") or "").lower()
            ok = any(a in loc for a in auth_countries)
            gates.append(_gate("work_authorization", "Work authorisation", "pass" if ok else "needs_verification",
                               "No sponsorship offered; " + ("job country matches your authorisation" if ok else "confirm you can work in the job's country"),
                               wa.get("evidence")))
        else:
            gates.append(_gate("work_authorization", "Work authorisation", "pass", "Matches the countries you are authorised to work in", wa.get("evidence")))

    # 6. Licences / clearances
    certs = " ".join(c.get("name", "") for c in vp.get("certifications", [])).lower()
    for lic in req.get("licenses", []):
        has = lic["text"].lower() in certs
        gates.append(_gate("license", f"Licence/credential: {lic['text']}", "pass" if has else "needs_verification",
                           "Found in your verified certifications" if has else "Not in your verified profile; confirm if you hold it",
                           lic["evidence"]))

    # 7. Location & work mode preference
    wm = job.get("work_mode") or "unknown"
    modes = prefs.get("work_modes") or ["remote", "hybrid", "onsite"]
    if wm != "unknown" and wm not in modes:
        gates.append(_gate("work_mode", "Work mode preference", "fail", f"Job is {wm}; your preferences allow {', '.join(modes)}"))
    else:
        gates.append(_gate("work_mode", "Work mode preference", "pass",
                           f"Job is {wm}" if wm != "unknown" else "Work mode not stated in the posting"))
    locs = prefs.get("locations") or []
    if wm != "remote" and locs:
        if not job.get("location"):
            gates.append(_gate("location", "Location preference", "needs_verification", "The posting does not state a location"))
        elif _loc_match(job["location"], locs) or prefs.get("willing_to_relocate"):
            gates.append(_gate("location", "Location preference", "pass",
                               f"{job['location']}" + ("" if _loc_match(job["location"], locs) else " (you are willing to relocate)")))
        else:
            gates.append(_gate("location", "Location preference", "fail", f"{job['location']} is not in your preferred locations ({', '.join(locs)})"))

    # 8. Employment type
    types = [t.lower() for t in prefs.get("employment_types") or []]
    et = (job.get("employment_type") or "").lower()
    if et and types and et not in types:
        gates.append(_gate("employment_type", "Employment type", "fail", f"Job is {et}; you want {', '.join(types)}"))
    elif types:
        gates.append(_gate("employment_type", "Employment type", "pass", et or "Not stated in the posting"))

    # 9. Seniority preference
    sen_pref = [s.lower() for s in prefs.get("seniority") or []]
    if sen_pref and job.get("seniority") and job["seniority"] not in sen_pref:
        gates.append(_gate("seniority_pref", "Seniority preference", "fail", f"Job seniority '{job['seniority']}' is outside your preference"))

    # 10a. Notice period (India): explicit employer requirement vs the user's notice period
    from .profile_service import NOTICE_OPTIONS
    nreq = req.get("notice") or {"status": "missing"}
    user_np = prefs.get("notice_period")
    label_np = "Notice period"
    if nreq.get("status") == "explicit":
        need = nreq["max_days"]
        need_txt = "immediate joining" if need == 0 else f"notice of at most {need} days"
        if user_np is None:
            gates.append(_gate("notice_period", label_np, "needs_verification",
                               f"The posting asks for {need_txt}; set your notice period in Preferences", nreq.get("evidence")))
        elif user_np == "negotiable":
            gates.append(_gate("notice_period", label_np, "needs_verification",
                               f"The posting asks for {need_txt}; your notice period is negotiable — confirm you can meet it",
                               nreq.get("evidence")))
        else:
            have = NOTICE_OPTIONS[user_np]
            ok = have <= need
            res = "pass" if ok else ("fail" if nreq.get("mandatory") else "warning")
            gates.append(_gate("notice_period", label_np, res,
                               f"Posting asks for {need_txt}; yours is {'immediate' if have == 0 else str(have) + ' days'}"
                               + ("" if ok or nreq.get("mandatory") else " (stated as a preference, not a requirement)"),
                               nreq.get("evidence")))
    elif nreq.get("status") == "inferred":
        gates.append(_gate("notice_period", label_np, "warning",
                           "The posting suggests a preference for short notice but states no number", nreq.get("evidence")))
    else:
        gates.append(_gate("notice_period", label_np, "unknown", "The posting states no notice-period requirement (not assumed)"))

    # 10b. Expected / minimum CTC (India)
    comp = req.get("compensation") or {"status": "missing"}
    min_lpa = prefs.get("min_ctc_lpa")
    mode = prefs.get("salary_mismatch_mode", "hard")
    if min_lpa:
        label_ctc = f"Minimum CTC {min_lpa:g} LPA"
        if comp.get("max_inr"):
            max_lpa = comp["max_inr"] / 1e5
            if max_lpa >= min_lpa:
                gates.append(_gate("ctc", label_ctc, "pass", f"Stated range reaches {max_lpa:g} LPA", comp.get("evidence")))
            elif comp.get("negotiable") and prefs.get("ctc_negotiable"):
                gates.append(_gate("ctc", label_ctc, "warning",
                                   f"Stated maximum {max_lpa:g} LPA is below your minimum, but the posting says pay is negotiable",
                                   comp.get("evidence")))
            else:
                gates.append(_gate("ctc", label_ctc, "fail" if mode == "hard" else "warning",
                                   f"Stated maximum {max_lpa:g} LPA is below your minimum {min_lpa:g} LPA"
                                   + ("" if mode == "hard" else " (you chose to treat this as a warning)"), comp.get("evidence")))
        elif comp.get("status") == "unverified":
            gates.append(_gate("ctc", label_ctc, "unknown", "Pay is described only vaguely (no figure stated)", comp.get("evidence")))
        else:
            gates.append(_gate("ctc", label_ctc, "unknown", "The posting states no salary/CTC (never estimated)", comp.get("evidence")))

    # 10. Salary (non-INR or when no CTC preference is set)
    smin = prefs.get("salary_min")
    if smin and job.get("salary_max") and not (min_lpa and (job.get("salary_currency") or "").upper() == "INR"):
        if (job.get("salary_currency") or "").upper() != (prefs.get("salary_currency") or "INR").upper():
            gates.append(_gate("salary", "Minimum salary", "pass", "Salary is in a different currency; not compared automatically"))
        elif job["salary_max"] >= smin:
            gates.append(_gate("salary", "Minimum salary", "pass", f"Stated range reaches {job['salary_max']:,.0f}"))
        elif job["salary_max"] * 12 >= smin:
            gates.append(_gate("salary", "Minimum salary", "needs_verification", "Stated salary may be monthly; please check"))
        else:
            gates.append(_gate("salary", "Minimum salary", "fail", f"Stated maximum {job['salary_max']:,.0f} is below your minimum {smin:,.0f}"))

    # 11. Exclusions
    comp = (job.get("company") or "").lower()
    for xc in prefs.get("excluded_companies") or []:
        if xc.lower() and xc.lower() in comp:
            gates.append(_gate("excluded_company", "Excluded employer", "fail", f"'{job.get('company')}' matches your exclusion '{xc}'"))
    blob = f"{job.get('title') or ''}\n{job.get('description') or ''}".lower()
    for kw in prefs.get("excluded_keywords") or []:
        if kw.strip() and re.search(r"(?<![a-z0-9])" + re.escape(kw.lower()) + r"(?![a-z0-9])", blob):
            gates.append(_gate("excluded_keyword", "Excluded keyword", "fail", f"Posting mentions excluded keyword '{kw}'"))
    labels = {"fees": "Asks candidates to pay a fee", "unpaid": "Unpaid work", "commission_only": "Commission-only pay",
              "suspicious_data": "Requests sensitive personal data"}
    for rf in req.get("red_flags", []):
        if ex.get(rf["type"], True):
            gates.append(_gate(f"red_flag_{rf['type']}", labels[rf["type"]], "fail", "Excluded by your safety settings", rf["evidence"]))
    if not job.get("company") and ex.get("unverifiable", True):
        gates.append(_gate("company_verification", "Employer identified", "needs_verification",
                           "The employer could not be identified from the posting"))
    if req.get("injection_markers"):
        gates.append(_gate("suspicious_content", "Posting content looks normal", "needs_verification",
                           "The posting contains instruction-like text aimed at automated tools; it was ignored. Review manually.",
                           "; ".join(req["injection_markers"])))

    # User confirmations convert needs_verification -> pass (recorded as user-confirmed).
    for g in gates:
        key = g["id"] if g["id"] != "license" else f"license:{g['label']}"
        if g["result"] == "needs_verification" and key in confirmations:
            g["result"] = "pass"
            g["detail"] += f" — confirmed by you on {confirmations[key][:10]}"
            g["user_confirmed"] = True
        g["key"] = key

    if any(g["result"] == "fail" for g in gates):
        result = "fail"
    elif any(g["result"] == "needs_verification" for g in gates):
        result = "needs_verification"
    else:
        result = "pass"
    return {"result": result, "gates": gates, "candidate_experience": exp}


# ------------------------------------------------------------------ match score
def compute_match(job: dict, req: dict, vp: dict, prefs: dict) -> dict:
    ev = candidate_skill_evidence(vp)
    have = {k.lower(): k for k in ev}
    comps = []
    uncertainties = []

    def skill_status(skill: str):
        if skill.lower() in have:
            return "matched", ev[have[skill.lower()]][:3], None
        rel = [r for r in sk.related_to(skill) if r.lower() in have]
        if rel:
            return "transferable", ev[have[rel[0].lower()]][:2], rel[0]
        return "missing", [], None

    def skill_component(cid, label, items):
        matched, missing, transferable = [], [], []
        pts = 0.0
        for it in items:
            st, evid, via = skill_status(it["skill"])
            if st == "matched":
                matched.append({"skill": it["skill"], "resume_evidence": evid, "jd_evidence": it["evidence"]})
                pts += 1
            elif st == "transferable":
                transferable.append({"skill": it["skill"], "via": via, "resume_evidence": evid, "jd_evidence": it["evidence"]})
                pts += TRANSFERABLE_CREDIT
            else:
                missing.append({"skill": it["skill"], "jd_evidence": it["evidence"]})
        w = WEIGHTS[cid]
        applicable = bool(items)
        points = w * pts / len(items) if items else 0
        detail = (f"{len(matched)} of {len(items)} matched" + (f", {len(transferable)} transferable (partial credit)" if transferable else "")
                  if items else "No skills of this kind were identified in the posting")
        comps.append({"id": cid, "label": label, "weight": w, "points": round(points, 2), "applicable": applicable,
                      "detail": detail, "matched": matched, "missing": missing, "transferable": transferable})
        return matched, missing, transferable

    m_req, miss_req, tr_req = skill_component("required_skills", "Required skills", req.get("required_skills", []))
    m_pref, miss_pref, tr_pref = skill_component("preferred_skills", "Preferred skills", req.get("preferred_skills", []))

    # Keywords / responsibilities relevance
    kws = req.get("keywords", [])
    ctoks = {_stem(t.strip(".-")) for t in tokens(candidate_text(vp))}
    kw_hit = [k for k in kws if _stem(k) in ctoks]
    kw_miss = [k for k in kws if _stem(k) not in ctoks]
    comps.append({"id": "keywords", "label": "Responsibilities & keyword relevance", "weight": WEIGHTS["keywords"],
                  "points": round(WEIGHTS["keywords"] * len(kw_hit) / len(kws), 2) if kws else 0, "applicable": bool(kws),
                  "detail": f"{len(kw_hit)} of {len(kws)} key JD terms appear in your verified profile" if kws else "No key terms extracted",
                  "matched_keywords": kw_hit, "missing_keywords": kw_miss})

    # Experience
    exp = experience_summary(vp)
    e = req.get("experience", {})
    if e.get("min_years") is not None:
        need = e["min_years"]
        ratio = 1.0 if need == 0 else min(1.0, exp["years"] / need)
        comps.append({"id": "experience", "label": "Experience", "weight": WEIGHTS["experience"],
                      "points": round(WEIGHTS["experience"] * ratio, 2), "applicable": True,
                      "detail": f"You have {exp['years']} verified years; the posting asks for {need:g}"
                                + (f"–{e['max_years']:g}" if e.get("max_years") else "+"),
                      "evidence": e.get("evidence")})
        if not exp["precise"]:
            uncertainties.append("Some experience dates are year-only, so total experience is approximate")
    else:
        comps.append({"id": "experience", "label": "Experience", "weight": WEIGHTS["experience"], "points": 0, "applicable": False,
                      "detail": "The posting states no experience requirement"})

    # Seniority fit
    js = job.get("seniority")
    if not js and e.get("min_years") is not None:
        y = e["min_years"]
        js = "entry" if y < 1 else "mid" if y < 5 else "senior"
    if js:
        jl = SENIORITY_LEVEL.get(js, 2)
        diff = abs(jl - exp["level"])
        pts = WEIGHTS["seniority"] * (1.0 if diff == 0 else 0.6 if diff == 1 else 0.0)
        comps.append({"id": "seniority", "label": "Seniority fit", "weight": WEIGHTS["seniority"], "points": pts, "applicable": True,
                      "detail": f"Role level '{js}' vs your verified stage '{exp['stage']}'"})
    else:
        comps.append({"id": "seniority", "label": "Seniority fit", "weight": WEIGHTS["seniority"], "points": 0, "applicable": False,
                      "detail": "Role seniority could not be determined"})

    # Education
    ed = req.get("education", {})
    if ed.get("min_level"):
        hv = highest_education(vp)
        ok = hv >= ed["min_level"] or (ed.get("equivalent_ok") and exp["years"] >= 2)
        comps.append({"id": "education", "label": "Education & certifications", "weight": WEIGHTS["education"],
                      "points": WEIGHTS["education"] if ok else 0, "applicable": True,
                      "detail": f"Requires {EDU_NAMES.get(ed['min_level'])}; you have {EDU_NAMES.get(hv)}", "evidence": ed.get("evidence")})
    else:
        comps.append({"id": "education", "label": "Education & certifications", "weight": WEIGHTS["education"], "points": 0,
                      "applicable": False, "detail": "No education requirement stated"})

    # Domain / title relevance
    title_toks = {_stem(t) for t in tokens(job.get("title") or "")}
    refs = [t for t in exp["titles"] + (prefs.get("target_roles") or []) + [vp.get("basics", {}).get("headline") or ""] if t]
    if title_toks and refs:
        best = 0.0
        best_ref = None
        for r in refs:
            rt = {_stem(t) for t in tokens(r)}
            if rt:
                j = len(title_toks & rt) / len(title_toks)
                if j > best:
                    best, best_ref = j, r
        comps.append({"id": "domain", "label": "Role / domain relevance", "weight": WEIGHTS["domain"],
                      "points": round(WEIGHTS["domain"] * min(1.0, best), 2), "applicable": True,
                      "detail": f"Closest match to the job title: '{best_ref}'" if best_ref else "Job title does not resemble your roles or target roles"})
    else:
        comps.append({"id": "domain", "label": "Role / domain relevance", "weight": WEIGHTS["domain"], "points": 0, "applicable": False,
                      "detail": "No past titles or target roles to compare"})

    # Preference fit
    checks = []
    wm = job.get("work_mode")
    if wm and wm != "unknown":
        checks.append(wm in (prefs.get("work_modes") or [wm]))
    if job.get("employment_type") and prefs.get("employment_types"):
        checks.append(job["employment_type"] in prefs["employment_types"])
    if prefs.get("locations") and job.get("location") and wm != "remote":
        checks.append(_loc_match(job["location"], prefs["locations"]) or prefs.get("willing_to_relocate", False))
    comps.append({"id": "preferences", "label": "Preference fit", "weight": WEIGHTS["preferences"],
                  "points": round(WEIGHTS["preferences"] * sum(checks) / len(checks), 2) if checks else 0, "applicable": bool(checks),
                  "detail": f"{sum(checks)} of {len(checks)} stated preferences satisfied" if checks else "Not enough job details to compare preferences"})

    applicable_w = sum(c["weight"] for c in comps if c["applicable"])
    raw = sum(c["points"] for c in comps if c["applicable"])
    score = trunc1(100 * raw / applicable_w) if applicable_w else 0.0

    n_skills = len(req.get("required_skills", [])) + len(req.get("preferred_skills", []))
    reliable = n_skills >= 2 and applicable_w >= 50
    confidence = req.get("confidence", "low")
    if not reliable:
        confidence = "low"
        uncertainties.append("The posting names too few concrete requirements to score reliably; it will not be auto-recommended")
    if not job.get("extraction_complete"):
        uncertainties.append("Extraction incomplete: missing " + ", ".join(job.get("missing_fields") or []))
        if confidence == "high":
            confidence = "medium"
    if req.get("core_from_mentions"):
        uncertainties.append("No explicit requirements list; skills mentioned in the description were treated as core")
    return {
        "rubric_version": RUBRIC_VERSION,
        "score": score,
        "reliable": reliable,
        "applicable_weight": applicable_w,
        "components": comps,
        "matched_skills": m_req + m_pref,
        "missing_required": miss_req,
        "missing_preferred": miss_pref,
        "transferable": tr_req + tr_pref,
        "matched_keywords": kw_hit,
        "missing_keywords": kw_miss,
        "confidence": confidence,
        "uncertainties": uncertainties,
    }


def decide(job: dict, elig: dict, match: dict, prefs: dict) -> tuple[str, str]:
    """Return (status, explanation)."""
    thr = float(prefs.get("match_threshold", 85))
    margin = float(prefs.get("near_match_margin", 10))
    if job.get("page_status") in ("closed", "expired", "not_found"):
        return "expired", "The posting is closed, expired or removed."
    fails = [g for g in elig["gates"] if g["result"] == "fail"]
    if fails:
        return "not_eligible", "Failed hard gate(s): " + "; ".join(f"{g['label']} — {g['detail']}" for g in fails)
    if not match.get("reliable"):
        return "needs_review", "Not enough detail in the posting to score reliably."
    s = match["score"]
    if elig["result"] == "needs_verification" and s >= thr - margin:
        pend = [g for g in elig["gates"] if g["result"] == "needs_verification"]
        return "needs_review", f"Match {s}%. Needs your verification before it can be recommended: " + "; ".join(g["label"] for g in pend)
    if s >= thr:
        return "qualified", f"All hard gates passed and match {s}% ≥ {thr:g}% threshold."
    if s >= thr - margin:
        return "near_match", f"All hard gates passed but match {s}% is below the {thr:g}% threshold."
    return "below_threshold", f"Match {s}% is well below the {thr:g}% threshold."
