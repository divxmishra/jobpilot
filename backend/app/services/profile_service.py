"""Candidate profile: immutable uploads, versioned profile documents,
verified-fact views, preferences (profile.yml) and company registry (portals.yml)."""
from __future__ import annotations

import copy
import hashlib
import os
import re
import stat
import uuid
from datetime import date

from .. import audit, db
from ..context import uid
from ..files import atomic_write, data_path, read_yaml, user_path, write_yaml
from . import resume_parser
from .textutil import merge_intervals_months

FACT_LISTS = ["skills", "experience", "education", "projects", "certifications", "achievements", "links"]
VALID_STATUS = {"verified", "unverified", "uncertain", "missing"}

DEFAULT_PREFERENCES = {
    "target_roles": [],
    "industries": [],
    "locations": [],
    "work_modes": ["remote", "hybrid", "onsite"],
    "willing_to_relocate": False,
    "salary_min": None,
    "salary_currency": "INR",
    "seniority": [],
    "employment_types": ["full-time"],
    "excluded_companies": [],
    "excluded_keywords": [],
    "work_authorization_countries": [],
    "exclusions": {"fees": True, "unpaid": True, "commission_only": True, "suspicious_data": True, "unverifiable": True},
    "match_threshold": 85,
    "near_match_margin": 10,
    "max_job_age_days": 45,
    "allow_follow_up": True,
    # India-specific: notice period & CTC (LPA = lakh rupees per annum)
    "notice_period": None,              # immediate | 15 | 30 | 60 | 90 | negotiable | None (not set)
    "expected_ctc_lpa": None,
    "min_ctc_lpa": None,
    "ctc_negotiable": False,
    "salary_mismatch_mode": "hard",     # hard = exclude job | warning = flag only
}
NOTICE_OPTIONS = {"immediate": 0, "15": 15, "30": 30, "60": 60, "90": 90, "negotiable": None}


# ------------------------------------------------------------------ uploads
def _safe_name(name: str) -> str:
    base = os.path.basename(name or "file")
    return re.sub(r"[^A-Za-z0-9._\-]", "_", base)[:80]


def save_upload(kind: str, filename: str, data: bytes, mime: str | None) -> dict:
    sha = hashlib.sha256(data).hexdigest()
    existing = db.query_one("SELECT * FROM uploads WHERE sha256 = ? AND kind = ? AND user_id = ?", (sha, kind, uid()))
    if existing:
        return existing
    path = user_path("uploads", f"{sha[:16]}_{_safe_name(filename)}")
    if not path.exists():
        atomic_write(path, data)
        try:
            os.chmod(path, stat.S_IREAD | stat.S_IRGRP)  # immutable original
        except OSError:
            pass
    text = None
    if kind == "master_resume":
        text = resume_parser.extract_text(filename, data)
    upload_row_id = db.execute(
        "INSERT INTO uploads(kind, filename, stored_path, mime, size, sha256, extracted_text, created_at, user_id) VALUES (?,?,?,?,?,?,?,?,?)",
        (kind, _safe_name(filename), str(path.relative_to(data_path())), mime, len(data), sha, text, db.now_iso(), uid()))
    audit.log("profile", "upload", f"Stored immutable {kind.replace('_', ' ')} '{_safe_name(filename)}' ({len(data)} bytes)",
              entity_type="upload", entity_id=upload_row_id, details={"sha256": sha})
    return db.query_one("SELECT * FROM uploads WHERE id = ?", (upload_row_id,))


# ------------------------------------------------------------------ versions
def _row(r: dict | None) -> dict | None:
    if not r:
        return None
    r = dict(r)
    r["data"] = db.jloads(r["data"], {})
    return r


def active_profile() -> dict | None:
    return _row(db.query_one("SELECT * FROM profile_versions WHERE status = 'verified' AND user_id = ? ORDER BY version DESC LIMIT 1", (uid(),)))


def latest_draft() -> dict | None:
    return _row(db.query_one("SELECT * FROM profile_versions WHERE status = 'draft' AND user_id = ? ORDER BY version DESC LIMIT 1", (uid(),)))


def get_version(vid: int) -> dict | None:
    return _row(db.query_one("SELECT * FROM profile_versions WHERE id = ? AND user_id = ?", (vid, uid())))


def list_versions() -> list[dict]:
    return db.query("SELECT id, version, status, note, created_at, confirmed_at, upload_id FROM profile_versions WHERE user_id = ? ORDER BY version DESC", (uid(),))


def _next_version() -> int:
    r = db.query_one("SELECT MAX(version) AS v FROM profile_versions WHERE user_id = ?", (uid(),))
    return (r["v"] or 0) + 1


def _discard_open_drafts() -> None:
    db.execute("DELETE FROM profile_versions WHERE status = 'draft' AND user_id = ?", (uid(),))


def create_draft_from_upload(upload_id: int) -> dict:
    up = db.query_one("SELECT * FROM uploads WHERE id = ? AND user_id = ?", (upload_id, uid()))
    if not up or up["kind"] != "master_resume":
        raise ValueError("Upload not found")
    data = resume_parser.parse_resume_text(up["extracted_text"] or "")
    data["source"] = {"upload_id": upload_id, "filename": up["filename"], "sha256": up["sha256"]}
    active = active_profile()
    _discard_open_drafts()
    vid = db.execute(
        "INSERT INTO profile_versions(version, status, data, upload_id, parent_id, note, created_at, user_id) VALUES (?,?,?,?,?,?,?,?)",
        (_next_version(), "draft", db.jdumps(data), upload_id, active["id"] if active else None,
         "Extracted from master resume", db.now_iso(), uid()))
    n_unc = sum(1 for k in FACT_LISTS for i in data.get(k, []) if i.get("status") == "uncertain")
    audit.log("extraction", "resume_parsed",
              f"Extracted profile draft from '{up['filename']}': {len(data['experience'])} positions, "
              f"{len(data['education'])} education entries, {len(data['skills'])} skills, {n_unc} uncertain items",
              entity_type="profile_version", entity_id=vid, details={"warnings": data["extraction"]["warnings"]})
    return get_version(vid)


def new_draft_from_active(note: str = "Profile update") -> dict:
    active = active_profile()
    if not active:
        raise ValueError("No verified profile to update")
    existing = latest_draft()
    if existing:
        return existing
    vid = db.execute(
        "INSERT INTO profile_versions(version, status, data, upload_id, parent_id, note, created_at, user_id) VALUES (?,?,?,?,?,?,?,?)",
        (_next_version(), "draft", db.jdumps(active["data"]), active["upload_id"], active["id"], note, db.now_iso(), uid()))
    audit.log("profile", "draft_created", f"Started controlled profile update (draft v{_next_version() - 1})",
              entity_type="profile_version", entity_id=vid)
    return get_version(vid)


def _validate(data: dict) -> list[str]:
    errors = []
    if not isinstance(data, dict):
        return ["Profile must be an object"]
    for k in FACT_LISTS:
        v = data.get(k, [])
        if not isinstance(v, list):
            errors.append(f"'{k}' must be a list")
            continue
        for item in v:
            if not isinstance(item, dict):
                errors.append(f"Items in '{k}' must be objects")
                break
            if item.get("status", "unverified") not in VALID_STATUS:
                errors.append(f"Invalid status in '{k}'")
                break
            item.setdefault("id", f"{k[:3]}_{uuid.uuid4().hex[:8]}")
    for e in data.get("experience", []):
        for f in ("start", "end"):
            val = e.get(f)
            if val and val != "present" and not re.fullmatch(r"\d{4}(-\d{2})?", str(val)):
                errors.append(f"Experience date '{val}' must be YYYY or YYYY-MM or 'present'")
    basics = data.get("basics", {})
    email = (basics.get("email") or {}).get("value")
    if email and not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        errors.append("Email address looks invalid")
    return errors


def update_draft(vid: int, data: dict) -> dict:
    v = get_version(vid)
    if not v:
        raise LookupError("Profile version not found")
    if v["status"] != "draft":
        raise PermissionError("Verified profile versions are read-only. Start a profile update to make changes.")
    errors = _validate(data)
    if errors:
        raise ValueError("; ".join(errors))
    for k in ("source", "extraction"):
        if k in v["data"]:
            data[k] = v["data"][k]
    db.execute("UPDATE profile_versions SET data = ? WHERE id = ?", (db.jdumps(data), vid))
    return get_version(vid)


def confirm_draft(vid: int) -> dict:
    v = get_version(vid)
    if not v or v["status"] != "draft":
        raise ValueError("Only drafts can be confirmed")
    data = v["data"]
    errors = _validate(data)
    if errors:
        raise ValueError("; ".join(errors))
    name = (data.get("basics", {}).get("name") or {})
    if not name.get("value") or name.get("status") != "verified":
        raise ValueError("Please confirm your name before verifying the profile.")
    if not any(i.get("status") == "verified" for k in FACT_LISTS for i in data.get(k, [])):
        raise ValueError("Confirm at least one skill, position, education or project entry.")
    with db.tx() as conn:
        conn.execute("UPDATE profile_versions SET status = 'superseded' WHERE status = 'verified' AND user_id = ?", (uid(),))
        conn.execute("UPDATE profile_versions SET status = 'verified', confirmed_at = ? WHERE id = ? AND user_id = ?",
                     (db.now_iso(), vid, uid()))
        first = conn.execute("UPDATE users SET profile_completed_at = ? WHERE id = ? AND profile_completed_at IS NULL",
                             (db.now_iso(), uid())).rowcount
    write_cv_md()
    if first:
        from . import entitlements, tracking
        tracking.event("profile_setup_completed", feature="profile")
        trial = entitlements.start_trial_if_eligible(uid())
        if trial and trial["status"] == "active":
            days = entitlements.system()["trial_days"]
            tracking.event("trial_started", feature="subscription", meta={"days": days})
            audit.notify_user("success", f"Your {days}-day free trial has started.", link="/app/subscription")
    v = get_version(vid)
    unverified = sum(1 for k in FACT_LISTS for i in data.get(k, []) if i.get("status") != "verified")
    audit.log("decision", "profile_confirmed",
              f"User confirmed profile v{v['version']}. {unverified} item(s) remain unverified and will not be used.",
              entity_type="profile_version", entity_id=vid, notify=True, link="/app/setup")
    return v


# ------------------------------------------------------------------ views
def verified_view(profile: dict | None) -> dict:
    """Only facts the user explicitly verified. This is what matching and tailoring may use."""
    if not profile:
        return {}
    d = profile["data"] if "data" in profile else profile
    out = {"basics": {k: v.get("value") for k, v in d.get("basics", {}).items()
                      if isinstance(v, dict) and v.get("status") == "verified"}}
    s = d.get("summary") or {}
    out["summary"] = s.get("value") if s.get("status") == "verified" else None
    wa = d.get("work_authorization") or {}
    out["work_authorization"] = wa.get("value") if wa.get("status") == "verified" else None
    for k in FACT_LISTS:
        out[k] = [copy.deepcopy(i) for i in d.get(k, []) if i.get("status") == "verified"]
    return out


def experience_summary(vp: dict, today: date | None = None) -> dict:
    """Derive career stage from verified facts (never assumed)."""
    jobs = [e for e in vp.get("experience", []) if e.get("start") and e.get("end")]
    undated = [e for e in vp.get("experience", []) if not (e.get("start") and e.get("end"))]
    full = [(e["start"], e["end"]) for e in jobs if not e.get("is_internship")]
    intern = [(e["start"], e["end"]) for e in jobs if e.get("is_internship")]
    m_full, p1 = merge_intervals_months(full, today)
    m_int, p2 = merge_intervals_months(intern, today)
    years = round(m_full / 12, 1)
    if m_full < 12:
        stage = "fresher / early career"
    elif m_full < 36:
        stage = "junior"
    elif m_full < 72:
        stage = "mid-level"
    elif m_full < 120:
        stage = "senior"
    else:
        stage = "lead / principal"
    level = 0 if m_full < 12 else 1 if m_full < 36 else 2 if m_full < 72 else 3 if m_full < 120 else 4
    return {"months": m_full, "years": years, "internship_months": m_int, "precise": p1 and p2 and not undated,
            "undated_positions": len(undated), "stage": stage, "level": level,
            "titles": [e.get("title") for e in vp.get("experience", []) if e.get("title")]}


EDU_LEVELS = [
    (5, re.compile(r"ph\.?\s?d|doctor", re.I)),
    (4, re.compile(r"master|m\.?\s?tech|m\.?\s?e\b|m\.?\s?sc|mca|mba|m\.?\s?com|m\.?\s?a\b|m\.?\s?s\b|llm|pgdm|post\s?graduate", re.I)),
    (3, re.compile(r"bachelor|b\.?\s?tech|b\.?\s?e\b|b\.?\s?sc|bca|bba|b\.?\s?com|b\.?\s?a\b|b\.?\s?s\b|b\.?\s?pharm|mbbs|llb|b\.?\s?ed|undergraduate|graduate", re.I)),
    (2, re.compile(r"diploma|associate", re.I)),
    (1, re.compile(r"12th|xii|higher secondary|senior secondary|hsc|high school", re.I)),
]


def education_level(text: str | None) -> int:
    for lvl, rx in EDU_LEVELS:
        if text and rx.search(text):
            return lvl
    return 0


def highest_education(vp: dict) -> int:
    return max([education_level(f"{e.get('degree') or ''} {e.get('field') or ''}") for e in vp.get("education", [])] or [0])


# ------------------------------------------------------------------ cv.md
def render_cv_md(profile: dict) -> str:
    d = profile["data"]
    vp = verified_view(profile)
    b = vp["basics"]
    lines = [f"# {b.get('name', 'Candidate')}", "",
             f"> Canonical master profile v{profile['version']} — confirmed {profile.get('confirmed_at') or ''}.",
             "> Generated from verified records in SQLite. Never overwritten by resume tailoring.", ""]
    contact = " | ".join(x for x in [b.get("email"), b.get("phone"), b.get("location")] if x)
    if contact:
        lines += [contact, ""]
    if b.get("headline"):
        lines += [f"**{b['headline']}**", ""]
    if vp.get("summary"):
        lines += ["## Summary", vp["summary"], ""]
    if vp["skills"]:
        lines += ["## Skills", ", ".join(s["name"] for s in vp["skills"]), ""]
    if vp["experience"]:
        lines.append("## Experience")
        for e in vp["experience"]:
            lines.append(f"### {e.get('title') or ''} — {e.get('company') or ''} ({e.get('start') or '?'} – {e.get('end') or '?'})")
            lines += [f"- {x}" for x in e.get("bullets", [])]
            lines.append("")
    if vp["projects"]:
        lines.append("## Projects")
        for p in vp["projects"]:
            lines.append(f"### {p.get('name')}")
            lines += [f"- {x}" for x in p.get("bullets", [])]
            lines.append("")
    if vp["education"]:
        lines.append("## Education")
        for e in vp["education"]:
            lines.append(f"- {e.get('degree') or ''} {('in ' + e['field']) if e.get('field') else ''}, "
                         f"{e.get('institution') or ''} ({e.get('start') or ''}–{e.get('end') or ''})"
                         f"{', ' + e['grade'] if e.get('grade') else ''}")
        lines.append("")
    if vp["certifications"]:
        lines += ["## Certifications"] + [f"- {c['name']}" for c in vp["certifications"]] + [""]
    if vp["achievements"]:
        lines += ["## Achievements"] + [f"- {a['text']}" for a in vp["achievements"]] + [""]
    if vp["links"]:
        lines += ["## Links"] + [f"- {l['label']}: {l['url']}" for l in vp["links"]] + [""]
    unver = [(k, i) for k in FACT_LISTS for i in d.get(k, []) if i.get("status") != "verified"]
    if unver:
        lines += ["## Unverified items (excluded from matching and tailoring)"]
        for k, i in unver:
            label = i.get("name") or i.get("title") or i.get("degree") or i.get("text") or i.get("url") or "item"
            lines.append(f"- [{k}] {label} — {i.get('status')}")
        lines.append("")
    return "\n".join(lines)


def write_cv_md() -> None:
    p = active_profile()
    if not p:
        return
    md = render_cv_md(p)
    hist = user_path("cv_history", f"cv.v{p['version']}.md")
    if not hist.exists():
        atomic_write(hist, md)
    atomic_write(user_path("cv.md"), md)


# ------------------------------------------------------------------ preferences
def get_preferences() -> dict:
    prefs = copy.deepcopy(DEFAULT_PREFERENCES)
    stored = db.get_user_setting("preferences", {}) or {}
    prefs.update({k: v for k, v in stored.items() if k in DEFAULT_PREFERENCES})
    ex = copy.deepcopy(DEFAULT_PREFERENCES["exclusions"])
    ex.update(stored.get("exclusions", {}) or {})
    prefs["exclusions"] = ex
    return prefs


def _str_list(v) -> list[str]:
    if isinstance(v, str):
        v = [x for x in re.split(r"[,\n]", v)]
    return [str(x).strip() for x in (v or []) if str(x).strip()][:50]


def save_preferences(new: dict) -> dict:
    cur = get_preferences()
    errors = []
    for k in ("target_roles", "industries", "locations", "excluded_companies", "excluded_keywords",
              "work_authorization_countries", "seniority", "employment_types", "work_modes"):
        if k in new:
            cur[k] = _str_list(new[k])
    if "work_modes" in new and not set(cur["work_modes"]) <= {"remote", "hybrid", "onsite"}:
        errors.append("Work modes must be remote, hybrid or onsite")
    if "salary_min" in new:
        v = new["salary_min"]
        if v in (None, ""):
            cur["salary_min"] = None
        else:
            try:
                cur["salary_min"] = float(v)
                if cur["salary_min"] < 0:
                    errors.append("Minimum salary cannot be negative")
            except (TypeError, ValueError):
                errors.append("Minimum salary must be a number")
    if "salary_currency" in new:
        cur["salary_currency"] = str(new["salary_currency"] or "INR").upper()[:3]
    for k in ("willing_to_relocate", "allow_follow_up"):
        if k in new:
            cur[k] = bool(new[k])
    if "match_threshold" in new:
        try:
            t = float(new["match_threshold"])
            if not 50 <= t <= 100:
                errors.append("Match threshold must be between 50 and 100")
            cur["match_threshold"] = t
        except (TypeError, ValueError):
            errors.append("Match threshold must be a number")
    for k, lo, hi in (("near_match_margin", 0, 30), ("max_job_age_days", 1, 365)):
        if k in new:
            try:
                cur[k] = int(new[k])
                if not lo <= cur[k] <= hi:
                    errors.append(f"{k.replace('_', ' ')} must be between {lo} and {hi}")
            except (TypeError, ValueError):
                errors.append(f"{k.replace('_', ' ')} must be a whole number")
    if "notice_period" in new:
        v = new["notice_period"]
        v = None if v in (None, "") else str(v).lower()
        if v is not None and v not in NOTICE_OPTIONS:
            errors.append("Notice period must be immediate, 15, 30, 60, 90 days or negotiable")
        cur["notice_period"] = v
    for k in ("expected_ctc_lpa", "min_ctc_lpa"):
        if k in new:
            v = new[k]
            if v in (None, ""):
                cur[k] = None
            else:
                try:
                    v = round(float(v), 2)
                    if not 0 < v <= 1000:
                        errors.append(f"{'Expected' if k.startswith('expected') else 'Minimum'} CTC must be between 0 and 1000 LPA")
                    cur[k] = v
                except (TypeError, ValueError):
                    errors.append("CTC must be a number in LPA (e.g. 12.5)")
    if cur.get("min_ctc_lpa") and cur.get("expected_ctc_lpa") and cur["min_ctc_lpa"] > cur["expected_ctc_lpa"]:
        errors.append("Minimum acceptable CTC cannot be higher than expected CTC")
    if "ctc_negotiable" in new:
        cur["ctc_negotiable"] = bool(new["ctc_negotiable"])
    if "salary_mismatch_mode" in new:
        if new["salary_mismatch_mode"] not in ("hard", "warning"):
            errors.append("Salary mismatch handling must be 'hard' or 'warning'")
        else:
            cur["salary_mismatch_mode"] = new["salary_mismatch_mode"]
    if "exclusions" in new and isinstance(new["exclusions"], dict):
        for k in cur["exclusions"]:
            if k in new["exclusions"]:
                cur["exclusions"][k] = bool(new["exclusions"][k])
    if errors:
        raise ValueError("; ".join(errors))
    old = get_preferences()
    db.set_user_setting("preferences", cur)
    write_profile_yml()
    if old.get("match_threshold") != cur["match_threshold"]:
        audit.log("settings", "threshold_changed",
                  f"User changed match threshold from {old.get('match_threshold')}% to {cur['match_threshold']}%")
    audit.log("settings", "preferences_saved", "Preferences updated by user", details={k: cur[k] for k in cur if k != "exclusions"})
    return cur


def write_profile_yml() -> None:
    write_yaml(user_path("profile.yml"), get_preferences(),
               header="# JobPilot candidate preferences (generated from SQLite; edit in the app or import via Settings)\n")


def import_profile_yml() -> dict:
    y = read_yaml(user_path("profile.yml"))
    if not isinstance(y, dict):
        raise ValueError("profile.yml is missing or not a mapping")
    return save_preferences(y)


# ------------------------------------------------------------------ portals / company registry
ATS_TYPES = {"greenhouse", "lever", "ashby", "remotive", "career_page"}


def list_companies() -> list[dict]:
    return db.query("SELECT * FROM companies WHERE user_id = ? ORDER BY name COLLATE NOCASE", (uid(),))


def validate_company(c: dict) -> dict:
    name = (c.get("name") or "").strip()
    ats = (c.get("ats") or "").strip().lower()
    token = (c.get("token") or "").strip()
    url = (c.get("careers_url") or "").strip()
    if not name:
        raise ValueError("Company name is required")
    if ats not in ATS_TYPES:
        raise ValueError(f"Source type must be one of: {', '.join(sorted(ATS_TYPES))}")
    if ats in {"greenhouse", "lever", "ashby", "remotive"} and not re.fullmatch(r"[A-Za-z0-9_.\- ]{1,80}", token):
        raise ValueError("A board token / search term is required for this source type (letters, numbers, - _ .)")
    if ats == "career_page" and not re.match(r"https?://", url):
        raise ValueError("Career page URL must start with http:// or https://")
    if url and not re.match(r"https?://", url):
        raise ValueError("Career page URL must start with http:// or https://")
    return {"name": name[:120], "ats": ats, "token": token or None, "careers_url": url or None,
            "enabled": 1 if c.get("enabled", True) else 0, "notes": (c.get("notes") or "")[:500]}


def upsert_company(c: dict, cid: int | None = None) -> dict:
    v = validate_company(c)
    if cid:
        db.execute("UPDATE companies SET name=?, ats=?, token=?, careers_url=?, enabled=?, notes=? WHERE id=? AND user_id=?",
                   (v["name"], v["ats"], v["token"], v["careers_url"], v["enabled"], v["notes"], cid, uid()))
    else:
        cid = db.execute("INSERT INTO companies(name, ats, token, careers_url, enabled, notes, created_at, user_id) VALUES (?,?,?,?,?,?,?,?)",
                         (v["name"], v["ats"], v["token"], v["careers_url"], v["enabled"], v["notes"], db.now_iso(), uid()))
    write_portals_yml()
    audit.log("settings", "company_saved", f"Company registry entry saved: {v['name']} ({v['ats']})", entity_type="company", entity_id=cid)
    return db.query_one("SELECT * FROM companies WHERE id = ? AND user_id = ?", (cid, uid()))


def delete_company(cid: int) -> None:
    c = db.query_one("SELECT * FROM companies WHERE id = ? AND user_id = ?", (cid, uid()))
    if not c:
        raise LookupError("Company not found")
    db.execute("DELETE FROM companies WHERE id = ? AND user_id = ?", (cid, uid()))
    write_portals_yml()
    audit.log("settings", "company_deleted", f"Removed {c['name']} from company registry", entity_type="company", entity_id=cid)


def write_portals_yml() -> None:
    items = [{"name": c["name"], "ats": c["ats"], "token": c["token"], "careers_url": c["careers_url"],
              "enabled": bool(c["enabled"]), "notes": c["notes"] or ""} for c in list_companies()]
    write_yaml(user_path("portals.yml"), {"companies": items},
               header="# JobPilot target-company registry (generated from SQLite; edit in the app or import via Settings)\n")


def import_portals_yml() -> int:
    y = read_yaml(user_path("portals.yml"))
    if not isinstance(y, dict) or not isinstance(y.get("companies"), list):
        raise ValueError("portals.yml must contain a 'companies' list")
    validated = [validate_company(c) for c in y["companies"]]
    with db.tx() as conn:
        conn.execute("DELETE FROM companies WHERE user_id = ?", (uid(),))
        for v in validated:
            conn.execute("INSERT INTO companies(name, ats, token, careers_url, enabled, notes, created_at, user_id) VALUES (?,?,?,?,?,?,?,?)",
                         (v["name"], v["ats"], v["token"], v["careers_url"], v["enabled"], v["notes"], db.now_iso(), uid()))
    audit.log("settings", "portals_imported", f"Imported {len(validated)} companies from portals.yml")
    return len(validated)
