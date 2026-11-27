"""Deterministic job-description interpretation.

Splits the JD into required / preferred / responsibilities / other sections and
extracts explicit requirements with the exact line that supports each one, so
every downstream decision can cite evidence. JD text is untrusted data: it is
only pattern-matched; instruction-like text is flagged, never followed.
"""
from __future__ import annotations

import re
from collections import Counter

from . import skills as sk
from .profile_service import EDU_LEVELS
from .textutil import STOPWORDS, strip_bullet, tokens

HEADINGS = {
    "required": r"requirements?|what you(?:'|’)?ll need|what we(?:'|’)?re looking for|who you are|minimum qualifications|"
                r"basic qualifications|required qualifications|qualifications|must[- ]haves?|you have|you bring|"
                r"skills (?:and|&) experience|key skills|eligibility( criteria)?|desired profile|candidate profile|"
                r"your profile|essential skills|required skills|about you|what you bring",
    "preferred": r"preferred qualifications|preferred skills|nice[- ]to[- ]haves?|bonus(?: points)?|good[- ]to[- ]have|"
                 r"pluses|it(?:'|’)?s a plus|additional qualifications|desirable|ideally|extra credit",
    "responsibilities": r"responsibilities|what you(?:'|’)?ll do|the role|role overview|your role|your impact|"
                        r"key responsibilities|job description|duties|day[- ]to[- ]day|what you will do|in this role",
    "other": r"about us|about the company|who we are|benefits|perks|what we offer|compensation|equal opportunity|"
             r"our values|why join|life at|eeo|diversity|how to apply|salary|location",
}
_HEAD_RE = {k: re.compile(rf"^\s*(?:{v})\s*[:\-–]?\s*$", re.I) for k, v in HEADINGS.items()}

EXP_RE = re.compile(
    r"(?:(?:minimum|min\.?|at least|over|more than)\s+(?:of\s+)?)?(?P<a>\d{1,2}(?:\.\d)?)\s*\+?\s*(?:(?:-|–|to)\s*(?P<b>\d{1,2}(?:\.\d)?)\s*\+?\s*)?"
    r"(?:years?|yrs?)(?:\s*(?:of|'))?\s*(?:[\w\-/,&\s]{0,50}?)\bexperience", re.I)
FRESHER_RE = re.compile(r"\b(freshers?|no experience required|0\s*(?:-|–|to)\s*\d\s*years|entry[- ]level|recent graduates?|new grads?)\b", re.I)
MANDATORY_WORDS = re.compile(r"\b(must|required|requirement|mandatory|minimum|at least|essential|need to have|should have)\b", re.I)
PREFERRED_WORDS = re.compile(r"\b(preferred|nice to have|good to have|bonus|a plus|is a plus|ideally|desirable|advantage|advantageous|optional)\b", re.I)
EQUIV_RE = re.compile(r"or equivalent|equivalent (?:practical |work )?experience|or relevant experience|or related experience", re.I)
DEGREE_REQ_RE = re.compile(
    r"\b(ph\.?\s?d|doctorate|master'?s?|m\.?\s?tech|m\.?\s?sc|mba|mca|bachelor'?s?|b\.?\s?tech|b\.?\s?e\b|b\.?\s?sc|bca|bba|b\.?\s?com|"
    r"degree|graduate|graduation|diploma|12th|higher secondary)\b", re.I)
AUTH_RE = re.compile(r"(?:authori[sz]ed|eligible|legally (?:able|permitted)|right) to work in (?:the )?(?P<c>[A-Z][A-Za-z .]{1,30}?)(?=[.,;)\n]| without| and|$)", re.I)
SPONSOR_RE = re.compile(r"(?:not|unable to|cannot|can't|won't|will not|do not|does not)\s+(?:be able to\s+)?(?:offer|provide|sponsor)\w*\s+(?:visa|work permit|sponsorship)|"
                        r"no (?:visa )?sponsorship|sponsorship (?:is )?not (?:available|provided|offered)", re.I)
LICENSE_RE = re.compile(r"\b(driver'?s licen[cs]e|driving licen[cs]e|valid licen[cs]e|security clearance|professional licen[cs]e|"
                        r"registered nurse|\bCPA\b|chartered accountant|bar admission|board certified|nursing licen[cs]e)\b", re.I)
RED_FLAGS = {
    "fees": re.compile(r"(?:registration|training|joining|processing|security|application|onboarding|kit)\s+(?:fees?|deposit|charges?)|"
                       r"pay (?:a |the |an )?(?:fee|deposit)|refundable deposit|investment required", re.I),
    "unpaid": re.compile(r"\bunpaid\b|no stipend|without (?:any )?(?:pay|stipend|salary)|volunteer(?:ing)? (?:position|role)", re.I),
    "commission_only": re.compile(r"commission[- ]only|100\s?% commission|only commission|purely commission|commission based only", re.I),
    "suspicious_data": re.compile(r"(?:send|share|submit|provide|upload)\s+(?:your\s+)?(?:aadhaar|pan card|passport|bank (?:account|details)|"
                                  r"ssn|social security|credit card|otp|password)", re.I),
}
INJECTION_RE = re.compile(r"ignore (?:all |any )?(?:previous|prior|above) instructions|you are (?:an? )?(?:ai|language model|assistant)|"
                          r"system prompt|rate this candidate|score this (?:resume|candidate)|disregard (?:the )?(?:rules|instructions)", re.I)
COUNTRIES = ["United States", "USA", "US", "India", "United Kingdom", "UK", "Canada", "Germany", "Australia", "Singapore",
             "Netherlands", "Ireland", "France", "Spain", "Japan", "UAE", "European Union", "EU"]


# ------------------------------------------------------------------ notice period & compensation (India)
NOTICE_IMMEDIATE = re.compile(r"\b(immediate joiners?|join(?:ing)? immediately|immediately available|0\s*days?\s*notice|"
                              r"notice period\s*(?:of\s*)?0\s*days)\b", re.I)
NOTICE_DAYS = [
    re.compile(r"notice period\s*(?:of|:|-)?\s*(?:up ?to|max(?:imum)?(?: of)?|less than|not more than|within|<=?)?\s*(\d{1,3})\s*days", re.I),
    re.compile(r"(\d{1,3})\s*days?(?:'|’)?\s*notice", re.I),
    re.compile(r"join(?:ing)?\s*(?:with)?in\s*(\d{1,3})\s*(days|weeks|months)", re.I),
    re.compile(r"(?:can|able to|should)\s*join\s*(?:in|within)\s*(\d{1,3})\s*(days|weeks|months)", re.I),
]
NOTICE_SOFT = re.compile(r"\b(early joiners?|short notice|serving notice period|currently serving notice)\b", re.I)
CTC_UPTO = re.compile(r"(?:ctc|salary|package|compensation)\s*(?:of|:|-)?\s*(?:up ?to|max(?:imum)?(?: of)?)\s*(?:₹|rs\.?|inr)?\s*"
                      r"(\d+(?:\.\d+)?)\s*(lpa|lakhs?|lacs?|l)\b", re.I)
CTC_RANGE = re.compile(r"(\d+(?:\.\d+)?)\s*(?:-|–|to)\s*(\d+(?:\.\d+)?)\s*(?:lpa|lakhs?|lacs?)\b", re.I)
NEGOTIABLE = re.compile(r"\b(salary|ctc|compensation|package|pay)\b[^.\n]{0,40}\bnegotiable\b|\bnegotiable\b[^.\n]{0,30}\b(salary|ctc|pay)\b", re.I)
VAGUE_PAY = re.compile(r"\b(best in (?:the )?industry|competitive (?:salary|pay|compensation)|attractive (?:salary|package)|as per industry standards?)\b", re.I)
STRUCTURE = [("fixed", r"\bfixed\b"), ("variable", r"\bvariable\b"), ("bonus", r"\bbonus\b"), ("ESOPs", r"\besops?\b|\bstock options?\b"),
             ("incentives", r"\bincentives?\b"), ("in-hand", r"\bin[- ]hand\b")]


def _line_of(desc: str, m: re.Match) -> str:
    start = desc.rfind("\n", 0, m.start()) + 1
    end = desc.find("\n", m.end())
    return desc[start:end if end != -1 else len(desc)].strip()[:240]


def extract_notice(desc: str) -> dict:
    """Notice-period requirement as stated. status: explicit | inferred | missing."""
    out = {"status": "missing", "max_days": None, "mandatory": False, "evidence": None}
    m = NOTICE_IMMEDIATE.search(desc)
    if m:
        line = _line_of(desc, m)
        out.update(status="explicit", max_days=0, evidence=line,
                   mandatory=not PREFERRED_WORDS.search(line) and not re.search(r"\bpreferred\b", line, re.I))
        return out
    for rx in NOTICE_DAYS:
        m = rx.search(desc)
        if m:
            n = int(m.group(1))
            unit = (m.group(2) if m.lastindex and m.lastindex >= 2 else "days").lower()
            days = n * (7 if unit.startswith("week") else 30 if unit.startswith("month") else 1)
            if days > 365:
                continue
            line = _line_of(desc, m)
            out.update(status="explicit", max_days=days, evidence=line,
                       mandatory=not PREFERRED_WORDS.search(line))
            return out
    m = NOTICE_SOFT.search(desc)
    if m:
        out.update(status="inferred", evidence=_line_of(desc, m), mandatory=False)
    return out


def extract_compensation(desc: str, job: dict) -> dict:
    """Compensation as stated (never estimated). Amounts in INR per year when the JD gives LPA/₹ figures."""
    out = {"status": "missing", "min_inr": None, "max_inr": None, "currency": None, "negotiable": False,
           "structure": [], "evidence": None}
    if job.get("salary_max") or job.get("salary_min"):
        out.update(status="explicit", min_inr=job.get("salary_min") if (job.get("salary_currency") or "").upper() == "INR" else None,
                   max_inr=job.get("salary_max") if (job.get("salary_currency") or "").upper() == "INR" else None,
                   currency=job.get("salary_currency"), evidence=job.get("salary_text"))
    m = CTC_RANGE.search(desc)
    if m and out["status"] == "missing":
        a, b = float(m.group(1)) * 1e5, float(m.group(2)) * 1e5
        out.update(status="explicit", min_inr=min(a, b), max_inr=max(a, b), currency="INR", evidence=_line_of(desc, m))
    m = CTC_UPTO.search(desc)
    if m and out["max_inr"] is None:
        out.update(status="explicit", max_inr=float(m.group(1)) * 1e5, currency="INR", evidence=_line_of(desc, m))
    nm = NEGOTIABLE.search(desc)
    if nm:
        out["negotiable"] = True
        out["evidence"] = out["evidence"] or _line_of(desc, nm)
    if out["status"] == "missing":
        vm = VAGUE_PAY.search(desc)
        if vm:
            out.update(status="unverified", evidence=_line_of(desc, vm))
        elif nm:
            out["status"] = "inferred"
    low = desc.lower()
    out["structure"] = [label for label, rx in STRUCTURE if re.search(rx, low)] if re.search(r"\b(ctc|salary|compensation|pay)\b", low) else []
    return out


def edu_level_of(text: str) -> int:
    for lvl, rx in EDU_LEVELS:
        if rx.search(text):
            return lvl
    if re.search(r"\bdegree\b|\bgraduat", text, re.I):
        return 3
    return 0


def split_jd(text: str) -> dict[str, list[str]]:
    out: dict[str, list[str]] = {"intro": [], "required": [], "preferred": [], "responsibilities": [], "other": []}
    cur = "intro"
    for raw in (text or "").split("\n"):
        line = raw.strip()
        if not line:
            continue
        short = strip_bullet(line)
        if len(short) <= 60:
            hit = next((k for k, rx in _HEAD_RE.items() if rx.match(short.rstrip(":"))), None)
            if hit:
                cur = hit
                continue
        out[cur].append(short)
    return out


def _skills_in(lines: list[str], extra: list[str]) -> dict[str, str]:
    found: dict[str, str] = {}
    for line in lines:
        for s in sk.find_skills(line, extra):
            found.setdefault(s, line)
    return found


def parse_requirements(job: dict, candidate_terms: list[str] | None = None) -> dict:
    desc = job.get("description") or ""
    secs = split_jd(desc)
    extra = candidate_terms or []
    structured = bool(secs["required"] or secs["preferred"])
    req_lines, pref_lines = list(secs["required"]), list(secs["preferred"])
    # Lines in the required section that say "preferred/nice to have" are preferred.
    for l in list(req_lines):
        if PREFERRED_WORDS.search(l) and not MANDATORY_WORDS.search(l):
            req_lines.remove(l)
            pref_lines.append(l)
    body_lines = secs["intro"] + secs["responsibilities"]
    if not structured:
        # Unstructured JD: classify line by line.
        for l in body_lines:
            if PREFERRED_WORDS.search(l):
                pref_lines.append(l)
            elif MANDATORY_WORDS.search(l):
                req_lines.append(l)
    required = _skills_in(req_lines, extra)
    preferred = {k: v for k, v in _skills_in(pref_lines, extra).items() if k not in required}
    mentioned = {k: v for k, v in _skills_in(body_lines + ([] if structured else req_lines), extra).items()
                 if k not in required and k not in preferred}
    core_from_mentions = False
    if not required and mentioned:
        # No explicit requirement list: skills named in the role description are the core skills.
        required, mentioned, core_from_mentions = mentioned, {}, True

    # Experience
    experience = {"min_years": None, "max_years": None, "mandatory": False, "evidence": None, "fresher_ok": False}
    for pool, mandatory in ((req_lines, True), (body_lines, None), (pref_lines, False)):
        for l in pool:
            m = EXP_RE.search(l)
            if m:
                a = float(m.group("a"))
                b = float(m.group("b")) if m.group("b") else None
                if a > 40:
                    continue
                experience.update(min_years=a, max_years=b, evidence=l,
                                  mandatory=bool(mandatory) if mandatory is not None else bool(MANDATORY_WORDS.search(l)))
                if PREFERRED_WORDS.search(l):
                    experience["mandatory"] = False
                break
        if experience["evidence"]:
            break
    fm = FRESHER_RE.search(desc)
    if fm:
        experience["fresher_ok"] = True
        experience.setdefault("fresher_evidence", fm.group(0))

    # Education
    education = {"min_level": 0, "label": None, "mandatory": False, "equivalent_ok": False, "evidence": None}
    for pool, mandatory in ((req_lines, True), (body_lines, None), (pref_lines, False)):
        for l in pool:
            if DEGREE_REQ_RE.search(l) and re.search(r"degree|bachelor|master|ph\.?d|b\.?tech|graduat|diploma|bca|mca|mba|b\.?sc|b\.?e\b", l, re.I):
                lvl = edu_level_of(l)
                if lvl:
                    education.update(min_level=lvl, label=DEGREE_REQ_RE.search(l).group(0), evidence=l,
                                     equivalent_ok=bool(EQUIV_RE.search(l)),
                                     mandatory=(bool(mandatory) if mandatory is not None else bool(MANDATORY_WORDS.search(l)))
                                     and not PREFERRED_WORDS.search(l))
                    break
        if education["evidence"]:
            break

    # Work authorisation / sponsorship / licences
    auth = {"required": False, "countries": [], "no_sponsorship": False, "evidence": None}
    for m in AUTH_RE.finditer(desc):
        c = m.group("c").strip()
        if any(c.lower().startswith(x.lower()) for x in COUNTRIES):
            auth["required"] = True
            auth["countries"].append(c)
            auth["evidence"] = m.group(0)
    sm = SPONSOR_RE.search(desc)
    if sm:
        auth["no_sponsorship"] = True
        auth["evidence"] = (auth["evidence"] + "; " if auth["evidence"] else "") + sm.group(0)
    licenses = []
    for l in req_lines + body_lines:
        for m in LICENSE_RE.finditer(l):
            if not PREFERRED_WORDS.search(l):
                licenses.append({"text": m.group(0), "evidence": l})

    red_flags = []
    for kind, rx in RED_FLAGS.items():
        m = rx.search(desc)
        if m:
            start = max(0, m.start() - 60)
            red_flags.append({"type": kind, "evidence": desc[start:m.end() + 60].replace("\n", " ").strip()})
    injection = [m.group(0) for m in INJECTION_RE.finditer(desc)][:5]

    # Keywords: most frequent meaningful JD terms in requirement/responsibility text.
    focus = " ".join(req_lines + secs["responsibilities"] + pref_lines) or desc
    counts = Counter(t.strip(".-") for t in tokens(focus))
    keywords = [w for w, _ in counts.most_common(40) if w not in STOPWORDS and len(w) > 3][:25]

    n_found = len(required) + len(preferred)
    confidence = "high" if structured and n_found >= 4 else "medium" if n_found >= 2 else "low"
    return {
        "structured": structured,
        "core_from_mentions": core_from_mentions,
        "required_skills": [{"skill": k, "evidence": v} for k, v in required.items()],
        "preferred_skills": [{"skill": k, "evidence": v} for k, v in preferred.items()],
        "mentioned_skills": [{"skill": k, "evidence": v} for k, v in mentioned.items()],
        "experience": experience,
        "education": education,
        "work_authorization": auth,
        "licenses": licenses,
        "red_flags": red_flags,
        "injection_markers": injection,
        "responsibilities": secs["responsibilities"][:30],
        "keywords": keywords,
        "notice": extract_notice(desc),
        "compensation": extract_compensation(desc, job),
        "confidence": confidence,
        "method": "deterministic-v1",
    }
