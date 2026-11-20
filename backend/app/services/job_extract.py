"""Job extraction from URLs and official job-board APIs, plus expiry validation.

Order of preference: official public APIs (Greenhouse, Lever, Ashby) -> schema.org
JobPosting JSON-LD embedded in the page -> plain page text (marked incomplete).
Job-page content is untrusted data; it is only ever parsed, never executed or
treated as instructions.
"""
from __future__ import annotations

import html as htmlmod
import json
import re
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlparse

from bs4 import BeautifulSoup

from . import net
from .textutil import clean_text

CLOSED_PHRASES = [
    "no longer accepting applications", "this job has expired", "job has expired", "position has been filled",
    "this position is no longer available", "job is no longer available", "this job is closed", "job posting has closed",
    "applications are closed", "posting is closed", "no longer open", "this role has been filled",
    "the job you are looking for is no longer", "job not found", "this job posting is no longer active",
]

REQUIRED_FIELDS = ["company", "title", "description", "location"]


def html_to_text(html: str) -> str:
    if not html:
        return ""
    soup = BeautifulSoup(html, "html.parser")
    for t in soup(["script", "style", "noscript", "svg", "iframe", "form"]):
        t.decompose()
    for li in soup.find_all("li"):
        li.insert_before("\n• ")
    for br in soup.find_all("br"):
        br.replace_with("\n")
    for blk in soup.find_all(["p", "div", "h1", "h2", "h3", "h4", "h5", "h6", "ul", "ol", "tr", "section"]):
        blk.insert_after("\n")
    return clean_text(soup.get_text())


def detect_work_mode(*texts: str) -> str:
    blob = " ".join(t or "" for t in texts).lower()
    if re.search(r"\bhybrid\b", blob):
        return "hybrid"
    if re.search(r"\b(fully remote|100% remote|remote[- ]first|remote\b|work from home|wfh|telecommute|anywhere)", blob):
        return "remote"
    if re.search(r"\b(on-?site|in[- ]office|work from office|wfo)\b", blob):
        return "onsite"
    return "unknown"


def detect_employment_type(*texts: str) -> str | None:
    blob = " ".join(t or "" for t in texts).lower()
    for pat, label in [(r"\bintern(ship)?\b", "internship"), (r"\bpart[- ]time\b", "part-time"),
                       (r"\b(contract|contractor|fixed[- ]term)\b", "contract"), (r"\btemporary\b", "temporary"),
                       (r"\bfreelance\b", "freelance"), (r"\bfull[- ]time\b|\bpermanent\b|fulltime", "full-time")]:
        if re.search(pat, blob):
            return label
    return None


def detect_seniority(title: str) -> str | None:
    t = (title or "").lower()
    for pat, label in [(r"\bintern\b|\binternship\b|\btrainee\b|\bapprentice\b", "intern"),
                       (r"\b(principal|staff|distinguished)\b", "lead"), (r"\b(director|head of|vp|vice president)\b", "director"),
                       (r"\b(lead|manager)\b", "lead"), (r"\b(senior|sr\.?)\b|\biii\b", "senior"),
                       (r"\b(junior|jr\.?|entry[- ]level|graduate|fresher|associate)\b|\bi\b", "entry"),
                       (r"\bii\b|\bmid\b", "mid")]:
        if re.search(pat, t):
            return label
    return None


SAL_RE = re.compile(
    r"(?P<cur>₹|rs\.?|inr|\$|usd|€|eur|£|gbp)\s?(?P<a>\d[\d,]*(?:\.\d+)?)\s?(?P<ua>k|l|lpa|lakh|lakhs|cr|m)?"
    r"(?:\s?(?:-|–|to)\s?(?:₹|rs\.?|inr|\$|usd|€|eur|£|gbp)?\s?(?P<b>\d[\d,]*(?:\.\d+)?)\s?(?P<ub>k|l|lpa|lakh|lakhs|cr|m)?)?",
    re.I)
LPA_RE = re.compile(r"(?P<a>\d+(?:\.\d+)?)\s?(?:-|–|to)\s?(?P<b>\d+(?:\.\d+)?)\s?(?:lpa|lakhs? per annum|lakhs?)\b", re.I)


def _unit(v: float, u: str | None) -> float:
    u = (u or "").lower()
    return v * {"k": 1e3, "l": 1e5, "lpa": 1e5, "lakh": 1e5, "lakhs": 1e5, "cr": 1e7, "m": 1e6}.get(u, 1)


def parse_salary(text: str) -> dict:
    """Extract an explicitly stated salary. Never estimates."""
    if not text:
        return {}
    m = LPA_RE.search(text)
    if m:
        return {"salary_text": m.group(0), "salary_min": float(m.group("a")) * 1e5, "salary_max": float(m.group("b")) * 1e5,
                "salary_currency": "INR"}
    for m in SAL_RE.finditer(text):
        a = float(m.group("a").replace(",", ""))
        b = float(m.group("b").replace(",", "")) if m.group("b") else None
        ua, ub = m.group("ua"), m.group("ub") or m.group("ua")
        a, b = _unit(a, ua or (ub if b else None)), (_unit(b, ub) if b else None)
        if a < 1000 and not ua:  # e.g. "$5" bonus or "$1 billion" fragments
            continue
        cur = m.group("cur").lower()
        cur = {"₹": "INR", "rs": "INR", "rs.": "INR", "inr": "INR", "$": "USD", "usd": "USD", "€": "EUR", "eur": "EUR",
               "£": "GBP", "gbp": "GBP"}.get(cur, cur.upper())
        return {"salary_text": m.group(0).strip(), "salary_min": a, "salary_max": b or a, "salary_currency": cur}
    return {}


def _empty(source: str, url: str) -> dict:
    return {"company": None, "title": None, "location": None, "work_mode": "unknown", "employment_type": None,
            "seniority": None, "salary_text": None, "salary_min": None, "salary_max": None, "salary_currency": None,
            "posted_at": None, "valid_through": None, "source": source, "source_url": url, "apply_url": url,
            "external_id": None, "description": "", "page_status": "unknown", "page_status_detail": None}


def finalize(job: dict) -> dict:
    desc = job.get("description") or ""
    job["work_mode"] = job.get("work_mode") if job.get("work_mode") not in (None, "unknown") else \
        detect_work_mode(job.get("location") or "", job.get("title") or "", desc[:3000])
    job["employment_type"] = job.get("employment_type") or detect_employment_type(job.get("title") or "", desc[:3000])
    job["seniority"] = job.get("seniority") or detect_seniority(job.get("title") or "")
    if not job.get("salary_min"):
        job.update(parse_salary(desc))
    if job.get("valid_through") and job["page_status"] == "active":
        try:
            vt = datetime.fromisoformat(job["valid_through"].replace("Z", "+00:00"))
            if vt.tzinfo is None:
                vt = vt.replace(tzinfo=timezone.utc)
            if vt < datetime.now(timezone.utc):
                job["page_status"] = "expired"
                job["page_status_detail"] = f"Posting validThrough date {job['valid_through'][:10]} has passed"
        except ValueError:
            pass
    low = desc.lower()
    if job["page_status"] == "active":
        for ph in CLOSED_PHRASES:
            if ph in low[:4000] and len(desc) < 6000:
                job["page_status"] = "closed"
                job["page_status_detail"] = f"Page says: \"{ph}\""
                break
    missing = [f for f in REQUIRED_FIELDS if not job.get(f)]
    if len(desc) < 200:
        missing.append("full description")
    job["missing_fields"] = missing
    job["extraction_complete"] = 0 if missing else 1
    job["extracted_at"] = datetime.now(timezone.utc).replace(microsecond=0).isoformat()
    return job


# ---------------------------------------------------------------- ATS detection
def detect_ats(url: str) -> tuple[str, dict]:
    p = urlparse(url)
    host = (p.hostname or "").lower()
    parts = [x for x in p.path.split("/") if x]
    qs = parse_qs(p.query)
    if "greenhouse.io" in host:
        if len(parts) >= 3 and parts[1] == "jobs":
            return "greenhouse", {"token": parts[0], "id": parts[2]}
        if qs.get("for") and qs.get("token"):
            return "greenhouse", {"token": qs["for"][0], "id": qs["token"][0]}
    if "gh_jid" in qs and parts:
        return "greenhouse_embed", {"id": qs["gh_jid"][0]}
    if host == "jobs.lever.co" and len(parts) >= 2:
        return "lever", {"token": parts[0], "id": parts[1]}
    if host == "jobs.ashbyhq.com" and len(parts) >= 2:
        return "ashby", {"token": parts[0], "id": parts[1]}
    return "career_page", {}


def from_greenhouse(token: str, jid: str, url: str, data: dict | None = None) -> dict:
    job = _empty("greenhouse", url)
    if data is None:
        status, data, err = net.fetch_json(f"https://boards-api.greenhouse.io/v1/boards/{token}/jobs/{jid}")
        if status == 404:
            job.update(page_status="not_found", page_status_detail="Greenhouse API reports the job no longer exists (404)")
            return finalize(job)
        if err:
            job.update(page_status="inaccessible", page_status_detail=f"Could not verify via Greenhouse API: {err}")
            return finalize(job)
    content = htmlmod.unescape(data.get("content") or "")
    job.update(title=data.get("title"), location=(data.get("location") or {}).get("name"),
               description=html_to_text(content), external_id=str(data.get("id")),
               apply_url=data.get("absolute_url") or url, source_url=data.get("absolute_url") or url,
               posted_at=data.get("first_published") or None, page_status="active",
               page_status_detail="Listed as open on the official Greenhouse job board API")
    job["company"] = data.get("company_name") or token.replace("-", " ").title()
    return finalize(job)


def from_lever(token: str, jid: str, url: str, data: dict | None = None) -> dict:
    job = _empty("lever", url)
    if data is None:
        status, data, err = net.fetch_json(f"https://api.lever.co/v0/postings/{token}/{jid}")
        if status == 404:
            job.update(page_status="not_found", page_status_detail="Lever API reports the posting no longer exists (404)")
            return finalize(job)
        if err:
            job.update(page_status="inaccessible", page_status_detail=f"Could not verify via Lever API: {err}")
            return finalize(job)
    cats = data.get("categories") or {}
    parts = [data.get("descriptionPlain") or html_to_text(data.get("description") or "")]
    for lst in data.get("lists") or []:
        parts.append(lst.get("text", ""))
        parts.append(html_to_text("<ul>" + (lst.get("content") or "") + "</ul>"))
    parts.append(data.get("additionalPlain") or "")
    created = data.get("createdAt")
    job.update(title=data.get("text"), location=cats.get("location"), employment_type=(cats.get("commitment") or "").lower() or None,
               description=clean_text("\n".join(p for p in parts if p)), external_id=data.get("id"),
               apply_url=data.get("applyUrl") or data.get("hostedUrl") or url, source_url=data.get("hostedUrl") or url,
               posted_at=datetime.fromtimestamp(created / 1000, timezone.utc).date().isoformat() if created else None,
               page_status="active", page_status_detail="Listed as open on the official Lever postings API",
               company=token.replace("-", " ").title())
    wt = (data.get("workplaceType") or "").lower()
    if wt in ("remote", "hybrid", "onsite"):
        job["work_mode"] = wt
    sr = data.get("salaryRange") or {}
    if sr.get("min"):
        job.update(salary_min=float(sr["min"]), salary_max=float(sr.get("max") or sr["min"]),
                   salary_currency=sr.get("currency"), salary_text=f"{sr.get('currency', '')} {sr['min']}–{sr.get('max', '')} {sr.get('interval', '')}".strip())
    return finalize(job)


def from_ashby(token: str, jid: str, url: str, data: dict | None = None) -> dict:
    job = _empty("ashby", url)
    if data is None:
        status, board, err = net.fetch_json(f"https://api.ashbyhq.com/posting-api/job-board/{token}?includeCompensation=true")
        if err:
            job.update(page_status="inaccessible", page_status_detail=f"Could not verify via Ashby API: {err}")
            return finalize(job)
        data = next((j for j in (board or {}).get("jobs", []) if j.get("id") == jid), None)
        if data is None:
            job.update(page_status="closed", page_status_detail="Job is not listed on the company's Ashby board any more")
            return finalize(job)
    comp = data.get("compensation") or {}
    job.update(title=data.get("title"), location=data.get("location"), external_id=data.get("id"),
               employment_type=(data.get("employmentType") or "").lower().replace("fulltime", "full-time").replace("parttime", "part-time") or None,
               description=data.get("descriptionPlain") or html_to_text(data.get("descriptionHtml") or ""),
               apply_url=data.get("applyUrl") or data.get("jobUrl") or url, source_url=data.get("jobUrl") or url,
               posted_at=(data.get("publishedAt") or "")[:10] or None, page_status="active",
               page_status_detail="Listed as open on the official Ashby job board API",
               company=token.replace("-", " ").title(),
               salary_text=comp.get("compensationTierSummary") or None)
    if data.get("isRemote"):
        job["work_mode"] = "remote"
    return finalize(job)


def _jsonld_jobposting(soup: BeautifulSoup) -> dict | None:
    for s in soup.find_all("script", type="application/ld+json"):
        try:
            obj = json.loads(s.string or s.get_text() or "")
        except ValueError:
            continue
        stack = obj if isinstance(obj, list) else [obj]
        while stack:
            o = stack.pop()
            if isinstance(o, dict):
                t = o.get("@type")
                if t == "JobPosting" or (isinstance(t, list) and "JobPosting" in t):
                    return o
                if "@graph" in o:
                    stack.extend(o["@graph"] if isinstance(o["@graph"], list) else [o["@graph"]])
            elif isinstance(o, list):
                stack.extend(o)
    return None


def _ld_location(jp: dict) -> str | None:
    locs = jp.get("jobLocation")
    locs = locs if isinstance(locs, list) else [locs] if locs else []
    out = []
    for l in locs:
        addr = (l or {}).get("address") if isinstance(l, dict) else None
        if isinstance(addr, dict):
            parts = [addr.get("addressLocality"), addr.get("addressRegion"), (addr.get("addressCountry") or {}).get("name")
                     if isinstance(addr.get("addressCountry"), dict) else addr.get("addressCountry")]
            out.append(", ".join(p for p in parts if p))
        elif isinstance(addr, str):
            out.append(addr)
    s = "; ".join(o for o in out if o)
    if jp.get("jobLocationType") == "TELECOMMUTE":
        s = (s + " (Remote)").strip() if s else "Remote"
    return s or None


def from_page(url: str) -> dict:
    job = _empty("career_page", url)
    r = net.fetch(url)
    if r.robots_disallowed:
        job.update(page_status="inaccessible", page_status_detail=r.error + ". Use 'Enter job details manually' instead.")
        return finalize(job)
    if r.error:
        job.update(page_status="inaccessible", page_status_detail=r.error)
        return finalize(job)
    if r.status in (404, 410):
        job.update(page_status="not_found", page_status_detail=f"Page returned HTTP {r.status} (removed or never existed)")
        return finalize(job)
    if r.blocked:
        job.update(page_status="inaccessible", page_status_detail="The site uses bot protection/CAPTCHA. JobPilot does not bypass it — "
                                                                  "open the page yourself and paste the details manually.")
        return finalize(job)
    if r.status in (401, 403):
        job.update(page_status="inaccessible", page_status_detail=f"Access denied (HTTP {r.status}); login or permission required")
        return finalize(job)
    if r.status is None or r.status >= 400:
        job.update(page_status="inaccessible", page_status_detail=f"Page returned HTTP {r.status}")
        return finalize(job)
    job["source_url"] = url
    # The career page might redirect to a known ATS; use the official API then.
    if r.final_url != url:
        ats, info = detect_ats(r.final_url)
        if ats in ("greenhouse", "lever", "ashby"):
            return extract_url(r.final_url)
    soup = BeautifulSoup(r.text, "html.parser")
    jp = _jsonld_jobposting(soup)
    if jp:
        org = jp.get("hiringOrganization")
        company = org.get("name") if isinstance(org, dict) else org if isinstance(org, str) else None
        et = jp.get("employmentType")
        et = (et[0] if isinstance(et, list) and et else et) or None
        bs = jp.get("baseSalary") or {}
        val = bs.get("value") if isinstance(bs, dict) else None
        job.update(title=jp.get("title"), company=company, location=_ld_location(jp),
                   description=html_to_text(htmlmod.unescape(jp.get("description") or "")),
                   posted_at=(jp.get("datePosted") or "")[:10] or None, valid_through=jp.get("validThrough"),
                   employment_type=str(et).lower().replace("_", "-") if et else None,
                   external_id=str((jp.get("identifier") or {}).get("value")) if isinstance(jp.get("identifier"), dict) else None,
                   page_status="active", page_status_detail="Page available; structured JobPosting data found")
        if isinstance(val, dict) and (val.get("minValue") or val.get("value")):
            lo = float(val.get("minValue") or val.get("value"))
            hi = float(val.get("maxValue") or lo)
            job.update(salary_min=lo, salary_max=hi, salary_currency=bs.get("currency"),
                       salary_text=f"{bs.get('currency', '')} {lo:,.0f}–{hi:,.0f} per {(val.get('unitText') or 'year').lower()}")
        if jp.get("jobLocationType") == "TELECOMMUTE":
            job["work_mode"] = "remote"
    else:
        title = (soup.find("meta", property="og:title") or {}).get("content") if soup.find("meta", property="og:title") else None
        if not title and soup.find("h1"):
            title = soup.find("h1").get_text(" ", strip=True)
        site = soup.find("meta", property="og:site_name")
        main = soup.find("main") or soup.find("article") or soup.body or soup
        job.update(title=title, company=site.get("content") if site else None, description=html_to_text(str(main))[:20000],
                   page_status="active", page_status_detail="Page available but no structured job data; extraction may be incomplete")
    return finalize(job)


def extract_url(url: str) -> dict:
    url = url.strip()
    ats, info = detect_ats(url)
    if ats == "greenhouse":
        return from_greenhouse(info["token"], info["id"], url)
    if ats == "lever":
        return from_lever(info["token"], info["id"], url)
    if ats == "ashby":
        return from_ashby(info["token"], info["id"], url)
    return from_page(url)


def from_manual(fields: dict) -> dict:
    """User-pasted job details (manual workflow when a site disallows automation)."""
    url = (fields.get("url") or "").strip()
    job = _empty("manual", url)
    job.update(title=(fields.get("title") or "").strip() or None, company=(fields.get("company") or "").strip() or None,
               location=(fields.get("location") or "").strip() or None, description=clean_text(fields.get("description") or ""),
               posted_at=(fields.get("posted_at") or "").strip() or None, page_status="unknown",
               page_status_detail="Details entered manually by you; JobPilot could not independently verify that the posting is open")
    if fields.get("employment_type"):
        job["employment_type"] = fields["employment_type"]
    return finalize(job)
