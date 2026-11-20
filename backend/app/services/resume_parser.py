"""Master-resume ingestion: PDF/DOCX -> text -> structured, *unverified* facts.

Rules:
- Never invent anything. Missing dates/metrics stay missing.
- Ambiguous parses are marked status="uncertain" with an explanation in `issues`.
- Everything starts unverified; the user must confirm facts in the UI.
"""
from __future__ import annotations

import io
import re
import uuid

from . import skills as sk
from .textutil import DATE_RANGE_RE, BULLET_RE, clean_text, normalize_date, strip_bullet

SECTION_ALIASES = {
    "summary": ["summary", "professional summary", "profile", "about me", "objective", "career objective",
                "professional profile", "about", "overview", "career summary"],
    "experience": ["experience", "work experience", "professional experience", "employment", "employment history",
                   "work history", "internships", "internship", "internship experience", "experience & internships",
                   "relevant experience", "career history"],
    "education": ["education", "academic background", "academics", "educational qualifications", "qualifications",
                  "academic qualifications", "education & training"],
    "skills": ["skills", "technical skills", "key skills", "core skills", "core competencies", "competencies",
               "skills & tools", "tools", "technologies", "tech stack", "skill set", "areas of expertise", "expertise"],
    "projects": ["projects", "academic projects", "personal projects", "key projects", "selected projects", "project"],
    "certifications": ["certifications", "certificates", "certification", "licenses", "licenses & certifications",
                       "courses", "training", "trainings"],
    "achievements": ["achievements", "awards", "honors", "honours", "accomplishments", "awards & achievements",
                     "extracurricular", "extra-curricular activities", "activities", "leadership", "publications"],
    "links": ["links", "profiles", "online profiles", "social"],
    "languages": ["languages", "languages known"],
    "contact": ["contact", "contact information", "personal details", "personal information"],
}
_HEADING_LOOKUP = {a: k for k, al in SECTION_ALIASES.items() for a in al}

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")
PHONE_RE = re.compile(r"(?:\+?\d{1,3}[\s\-.]?)?(?:\(?\d{2,5}\)?[\s\-.]?)?\d{3,5}[\s\-.]?\d{4,5}")
URL_RE = re.compile(r"(?:https?://|www\.)[^\s,;|<>()]+|(?:linkedin\.com|github\.com|gitlab\.com|behance\.net|dribbble\.com)/[^\s,;|<>()]+", re.I)

ROLE_WORDS = re.compile(
    r"\b(engineer|developer|analyst|manager|intern|internship|designer|consultant|associate|executive|specialist|lead|"
    r"officer|assistant|teacher|accountant|architect|scientist|administrator|coordinator|director|head|trainee|"
    r"representative|advisor|technician|researcher|programmer|tester|writer|editor|marketer|recruiter|nurse|"
    r"founder|co-founder|owner|freelance|freelancer|volunteer|fellow|apprentice|tutor|clerk|supervisor|president)\b",
    re.I)
ORG_WORDS = re.compile(r"\b(inc|ltd|llc|llp|pvt|private|limited|corp|corporation|technologies|solutions|labs|"
                       r"systems|services|group|company|co\.|university|college|institute|bank|foundation|agency|studio)\b",
                       re.I)
DEGREE_RE = re.compile(
    r"\b(b\.?\s?tech|m\.?\s?tech|b\.?\s?e\b|m\.?\s?e\b|b\.?\s?sc|m\.?\s?sc|bca|mca|bba|mba|b\.?\s?com|m\.?\s?com|"
    r"b\.?\s?a\b|m\.?\s?a\b|ph\.?\s?d|bachelor(?:'s)?|master(?:'s)?|doctorate|diploma|associate degree|"
    r"higher secondary|senior secondary|secondary school|hsc|ssc|class\s?(?:x|xii|10|12)(?:th)?|12th|10th|"
    r"b\.?\s?s\b|m\.?\s?s\b|b\.?\s?pharm|mbbs|llb|b\.?\s?ed)", re.I)
INSTITUTION_RE = re.compile(r"\b(university|college|institute|school|academy|iit|nit|iiit|iim|vidyalaya|polytechnic)\b", re.I)
GRADE_RE = re.compile(r"\b(?:c?gpa|cpi|sgpa|grade|percentage|score)\s*[:\-]?\s*(\d{1,2}(?:\.\d{1,2})?\s*(?:/\s*\d{1,2}(?:\.\d)?)?)|(\d{2}(?:\.\d{1,2})?\s*%)", re.I)
YEAR_RE = re.compile(r"\b(19[5-9]\d|20\d\d)\b")


class ParseError(Exception):
    pass


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:8]}"


def extract_text(filename: str, data: bytes) -> str:
    name = filename.lower()
    if name.endswith(".pdf"):
        if not data.startswith(b"%PDF"):
            raise ParseError("The file does not look like a valid PDF.")
        from pypdf import PdfReader
        try:
            reader = PdfReader(io.BytesIO(data))
            if reader.is_encrypted:
                raise ParseError("The PDF is password-protected. Please upload an unprotected copy.")
            text = "\n".join((p.extract_text() or "") for p in reader.pages)
        except ParseError:
            raise
        except Exception as e:  # pypdf raises many types
            raise ParseError(f"Could not read the PDF ({type(e).__name__}).") from e
        if len(text.strip()) < 30:
            raise ParseError("No selectable text found in this PDF (it may be a scanned image). "
                             "OCR is not supported in V1 — please upload a text-based PDF or a DOCX.")
        return clean_text(text)
    if name.endswith(".docx"):
        if not data.startswith(b"PK"):
            raise ParseError("The file does not look like a valid DOCX.")
        import docx
        try:
            d = docx.Document(io.BytesIO(data))
        except Exception as e:
            raise ParseError(f"Could not read the DOCX ({type(e).__name__}).") from e
        parts = [p.text for p in d.paragraphs]
        for t in d.tables:
            for row in t.rows:
                seen = []
                for c in row.cells:
                    if c.text not in seen:
                        seen.append(c.text)
                parts.append(" | ".join(seen))
        text = "\n".join(parts)
        if len(text.strip()) < 30:
            raise ParseError("The DOCX appears to be empty.")
        return clean_text(text)
    raise ParseError("Unsupported file type. Please upload a PDF or DOCX.")


def _heading_key(line: str) -> str | None:
    s = line.strip().strip(":").strip("-–—|•*#").strip().lower()
    s = re.sub(r"\s+", " ", s)
    if not s or len(s) > 40:
        return None
    return _HEADING_LOOKUP.get(s)


def split_sections(text: str) -> dict[str, list[str]]:
    sections: dict[str, list[str]] = {"header": []}
    current = "header"
    for raw in text.split("\n"):
        line = raw.strip()
        if not line:
            continue
        key = _heading_key(line)
        if key:
            current = key
            sections.setdefault(current, [])
            continue
        # "Skills: Python, SQL" style inline heading
        m = re.match(r"^([A-Za-z &]{3,30}):\s+(.+)$", line)
        if current == "skills":
            sections["skills"].append(line)
            continue
        if m and _heading_key(m.group(1)) in {"skills", "links", "languages"} and current in {"header", "links", "languages"}:
            k = _heading_key(m.group(1))
            sections.setdefault(k, []).append(m.group(2))
            continue
        sections.setdefault(current, []).append(line)
    return sections


def _fact(value, status="unverified", **extra):
    d = {"value": value, "status": status if value not in (None, "") else "missing"}
    d.update(extra)
    return d


def parse_basics(header: list[str], full_text: str) -> dict:
    email = EMAIL_RE.search(full_text)
    phone = None
    for m in PHONE_RE.finditer("\n".join(header[:8]) or full_text[:500]):
        digits = re.sub(r"\D", "", m.group(0))
        if 10 <= len(digits) <= 13:
            phone = m.group(0).strip()
            break
    name = None
    for line in header[:4]:
        clean = line.strip()
        if EMAIL_RE.search(clean) or URL_RE.search(clean) or re.search(r"\d{4,}", clean):
            continue
        words = clean.split()
        if 1 < len(words) <= 5 and all(w[:1].isalpha() for w in words) and len(clean) <= 50:
            name = clean.title() if clean.isupper() else clean
            break
    location = None
    for line in header[:8]:
        for part in re.split(r"[|•·]", line):
            p = part.strip()
            if EMAIL_RE.search(p) or URL_RE.search(p) or PHONE_RE.fullmatch(p) or p == name:
                continue
            if re.fullmatch(r"[A-Z][A-Za-z .]+,\s*[A-Z][A-Za-z .]+(?:,\s*[A-Z][A-Za-z .]+)?", p) and len(p) < 60:
                location = p
                break
        if location:
            break
    headline = None
    for line in header[1:4]:
        if line != name and ROLE_WORDS.search(line) and len(line) < 80 and not EMAIL_RE.search(line):
            headline = line
            break
    return {
        "name": _fact(name, "unverified" if name else "missing"),
        "email": _fact(email.group(0) if email else None),
        "phone": _fact(phone),
        "location": _fact(location),
        "headline": _fact(headline),
    }


def parse_links(text: str) -> list[dict]:
    out, seen = [], set()
    for m in URL_RE.finditer(text):
        url = m.group(0).rstrip(".")
        if EMAIL_RE.fullmatch(url):
            continue
        if not url.lower().startswith("http"):
            url = "https://" + url
        key = url.lower().rstrip("/")
        if key in seen:
            continue
        seen.add(key)
        label = ("LinkedIn" if "linkedin" in key else "GitHub" if "github" in key else
                 "GitLab" if "gitlab" in key else "Portfolio")
        out.append({"id": _new_id("lnk"), "label": label, "url": url, "status": "unverified"})
    return out


def _split_header(header: str) -> tuple[str | None, str | None, str | None, list[str]]:
    """Return (title, company, location, issues) from an entry header line."""
    issues: list[str] = []
    parts = [p.strip(" ,") for p in re.split(r"\s+[|—–]\s+|\s+-\s+|\s+at\s+|\s*\|\s*|,\s+(?=[A-Z])", header) if p.strip(" ,")]
    title = company = location = None
    for p in parts:
        if title is None and ROLE_WORDS.search(p) and not ORG_WORDS.search(p):
            title = p
        elif company is None and (ORG_WORDS.search(p) or (title is not None and not ROLE_WORDS.search(p))):
            company = p
        elif location is None and re.fullmatch(r"[A-Z][A-Za-z .]+(?:,\s*[A-Z][A-Za-z .]+)?|remote|Remote", p):
            location = p
    leftovers = [p for p in parts if p not in (title, company, location)]
    if title is None and leftovers:
        title = leftovers.pop(0)
        issues.append("Job title could not be identified with confidence")
    if company is None and leftovers:
        company = leftovers.pop(0)
        issues.append("Employer name could not be identified with confidence")
    if company is None:
        issues.append("Employer missing")
    if title is None:
        issues.append("Job title missing")
    return title, company, location, issues


def parse_experience(lines: list[str]) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    pending_header: list[str] = []
    for line in lines:
        is_bullet = bool(BULLET_RE.match(line))
        m = DATE_RANGE_RE.search(line) if not is_bullet else None
        if m:
            header_text = (line[:m.start()] + " " + line[m.end():]).strip(" |,–—-()")
            headers = [h for h in pending_header if h] + ([header_text] if header_text else [])
            title, company, location, issues = _split_header(" | ".join(headers)) if headers else (None, None, None, ["Title and employer missing"])
            start, end = normalize_date(m.group("start")), normalize_date(m.group("end"))
            if not start or not end:
                issues.append("Dates could not be parsed")
            elif "-" not in start or (end != "present" and "-" not in end):
                issues.append("Only the year is given; months unknown")
            current = {"id": _new_id("exp"), "title": title, "company": company, "location": location,
                       "start": start, "end": end, "bullets": [],
                       "is_internship": bool(re.search(r"\bintern(ship)?\b|\btrainee\b|\bapprentice\b", " ".join(headers), re.I)),
                       "status": "uncertain" if issues else "unverified", "issues": issues}
            entries.append(current)
            pending_header = []
            continue
        if current is not None and (is_bullet or (current["bullets"] and line[:1].islower())):
            text = strip_bullet(line)
            if not is_bullet and current["bullets"]:
                current["bullets"][-1] += " " + text
            else:
                current["bullets"].append(text)
            continue
        if current is not None and not is_bullet and len(line) > 90:
            current["bullets"].append(line)  # paragraph-style description
            continue
        pending_header.append(line)
        if len(pending_header) > 2:
            # Too many header-ish lines without dates: attach to current entry as text.
            if current is not None:
                current["bullets"].append(pending_header.pop(0))
            else:
                pending_header.pop(0)
    if pending_header and current is not None:
        current["bullets"].extend(pending_header)
    if not entries and lines:
        # Section present but no parseable dated entries: keep raw text as one uncertain entry.
        entries.append({"id": _new_id("exp"), "title": None, "company": None, "location": None, "start": None,
                        "end": None, "bullets": [strip_bullet(l) for l in lines], "is_internship": False,
                        "status": "uncertain", "issues": ["Could not identify dated positions; please edit"]})
    return entries


def parse_education(lines: list[str]) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    for line in lines:
        text = strip_bullet(line)
        deg = DEGREE_RE.search(text)
        if deg and (current is None or current.get("degree")):
            current = {"id": _new_id("edu"), "degree": None, "field": None, "institution": None, "start": None,
                       "end": None, "grade": None, "status": "unverified", "issues": []}
            entries.append(current)
        if current is None:
            current = {"id": _new_id("edu"), "degree": None, "field": None, "institution": None, "start": None,
                       "end": None, "grade": None, "status": "unverified", "issues": []}
            entries.append(current)
        rng = DATE_RANGE_RE.search(text)
        if rng:
            current["start"] = current["start"] or normalize_date(rng.group("start"))
            current["end"] = current["end"] or normalize_date(rng.group("end"))
            text_wo = text[:rng.start()] + text[rng.end():]
        else:
            text_wo = text
            years = YEAR_RE.findall(text)
            if years and not current["end"]:
                current["end"] = years[-1]
                if len(years) > 1:
                    current["start"] = years[0]
        g = GRADE_RE.search(text)
        if g and not current["grade"]:
            current["grade"] = (g.group(1) or g.group(2)).strip()
        for part in re.split(r"\s*[|,–—]\s*|\s+-\s+", text_wo):
            p = part.strip(" ()")
            if not p or YEAR_RE.fullmatch(p) or GRADE_RE.search(p):
                continue
            if DEGREE_RE.search(p) and not current["degree"]:
                dm = re.match(r"(.+?)\s+(?:in|of)\s+(.+)", p, re.I)
                if dm and DEGREE_RE.search(dm.group(1)):
                    current["degree"], current["field"] = dm.group(1).strip(), dm.group(2).strip()
                else:
                    current["degree"] = p
            elif INSTITUTION_RE.search(p) and not current["institution"]:
                current["institution"] = p
    for e in entries:
        if not e["degree"]:
            e["issues"].append("Degree not identified")
        if not e["institution"]:
            e["issues"].append("Institution not identified")
        if not e["end"]:
            e["issues"].append("Completion year missing")
        if e["issues"]:
            e["status"] = "uncertain"
    return [e for e in entries if e["degree"] or e["institution"]]


def parse_projects(lines: list[str]) -> list[dict]:
    entries: list[dict] = []
    current: dict | None = None
    for line in lines:
        is_bullet = bool(BULLET_RE.match(line))
        text = strip_bullet(line)
        if not is_bullet and (current is None or (len(text) < 90 and not text[:1].islower())):
            name_part = re.split(r"\s+[|—–]\s+|\s+-\s+|:\s+", text, maxsplit=1)
            current = {"id": _new_id("prj"), "name": name_part[0].strip(), "bullets": [], "link": None,
                       "technologies": [], "status": "unverified", "issues": []}
            if len(name_part) > 1 and name_part[1].strip():
                current["bullets"].append(name_part[1].strip())
            entries.append(current)
        elif current is not None:
            if not is_bullet and current["bullets"] and text[:1].islower():
                current["bullets"][-1] += " " + text
            else:
                current["bullets"].append(text)
    for e in entries:
        blob = e["name"] + " " + " ".join(e["bullets"])
        u = URL_RE.search(blob)
        if u:
            e["link"] = u.group(0) if u.group(0).startswith("http") else "https://" + u.group(0)
        e["technologies"] = list(sk.find_skills(blob).keys())
        if not e["bullets"]:
            e["issues"].append("No description found")
            e["status"] = "uncertain"
    return entries


def parse_skill_lines(lines: list[str]) -> list[str]:
    items: list[str] = []
    for line in lines:
        text = strip_bullet(line)
        text = re.sub(r"^[A-Za-z /&]{2,30}:\s*", "", text)  # drop "Languages:" style labels
        for part in re.split(r"\s*[,|•·;/]\s*|\s{2,}", text):
            p = part.strip(" .()")
            if p and len(p) <= 40 and not re.fullmatch(r"\d+", p):
                items.append(p)
    return sk.normalize_skill_list(items)


def parse_resume_text(text: str) -> dict:
    sections = split_sections(text)
    header = sections.get("header", [])
    basics = parse_basics(header, text)
    experience = parse_experience(sections.get("experience", []))
    education = parse_education(sections.get("education", []))
    projects = parse_projects(sections.get("projects", []))
    certs = [{"id": _new_id("crt"), "name": strip_bullet(l), "status": "unverified"}
             for l in sections.get("certifications", []) if len(strip_bullet(l)) > 2]
    achievements = [{"id": _new_id("ach"), "text": strip_bullet(l), "status": "unverified"}
                    for l in sections.get("achievements", []) if len(strip_bullet(l)) > 3]
    summary_lines = sections.get("summary", [])
    summary = " ".join(strip_bullet(l) for l in summary_lines) or None

    # Skills: explicit Skills section first, then skills evidenced in experience/projects text.
    listed = parse_skill_lines(sections.get("skills", []))
    skill_items: dict[str, dict] = {}
    for s in listed:
        skill_items[s.lower()] = {"name": s, "category": sk.category(s), "status": "unverified",
                                  "evidence": ["Listed in the Skills section of the master resume"]}
    for e in experience:
        blob = " ".join(e["bullets"]) + " " + (e["title"] or "")
        for s in sk.find_skills(blob, listed):
            item = skill_items.setdefault(s.lower(), {"name": s, "category": sk.category(s), "status": "unverified", "evidence": []})
            item["evidence"].append(f"Mentioned in experience: {e['title'] or 'role'} at {e['company'] or 'employer'}")
    for p in projects:
        for s in p["technologies"]:
            item = skill_items.setdefault(s.lower(), {"name": s, "category": sk.category(s), "status": "unverified", "evidence": []})
            item["evidence"].append(f"Used in project: {p['name']}")
    skills_list = list(skill_items.values())
    for s in skills_list:
        s["id"] = _new_id("skl")
        s["evidence"] = s["evidence"][:4]

    warnings = []
    for key in ("experience", "education", "skills"):
        if key not in sections:
            warnings.append(f"No '{key}' section heading was detected; add details manually if needed.")
    return {
        "basics": basics,
        "summary": _fact(summary),
        "skills": skills_list,
        "experience": experience,
        "education": education,
        "projects": projects,
        "certifications": certs,
        "achievements": achievements,
        "links": parse_links(text),
        "work_authorization": _fact(None),
        "extraction": {"method": "deterministic-parser-v1", "warnings": warnings,
                       "sections_found": [k for k in sections if k != "header"]},
    }
