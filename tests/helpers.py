"""Test helpers: build resumes, verify profiles, add jobs. Clearly labelled test fixtures only."""
from __future__ import annotations

import io
import time
from pathlib import Path

import docx

FIX = Path(__file__).parent / "fixtures"

FRESHER_RESUME = """ANANYA RAO
Bengaluru, India | ananya.rao@example.com | +91 91234 56789 | github.com/ananya-example

EDUCATION
BCA in Computer Applications, Christ University, 2022 – 2025, CGPA 8.9/10

SKILLS
Python, SQL, HTML, CSS, JavaScript, Git, Data Analysis, Excel

PROJECTS
Student Grade Dashboard — web app
• Built a dashboard in Python and Flask with a SQLite database
• Visualised results using JavaScript charts
Sales Analysis
• Cleaned and analysed sales data with Python, Pandas and Excel

CERTIFICATIONS
Google Data Analytics Certificate (2024)
"""

JD_SENIOR = """Senior Backend Engineer
Responsibilities
- Design and build REST APIs with Python and FastAPI
- Own PostgreSQL data models and Docker based deployments on AWS
Requirements
- 4+ years of experience in backend development
- Strong Python, Django or FastAPI, PostgreSQL
- Experience with Docker, AWS and CI/CD
- Bachelor's degree in Computer Science or equivalent experience
Nice to have
- Kubernetes, Redis, React
Full-time position."""

JD_JUNIOR_DATA = """Junior Data Analyst (Freshers welcome)
What you'll do
- Analyse sales data and build reports in Excel and Python
- Write SQL queries and create dashboards
Requirements
- 0-1 years of experience; freshers welcome
- Python, SQL and Excel
- Data Analysis skills
- Bachelor's degree (BCA, B.Sc or similar)
Nice to have
- Pandas, Tableau
Full-time role in Bengaluru."""

JD_ANDROID = """Android Developer
Requirements
- 3+ years of experience building Android apps
- Kotlin, Java, Android SDK
- Experience with Firebase and REST APIs
Nice to have
- Flutter, Swift
Full-time."""


def docx_bytes(text: str) -> bytes:
    d = docx.Document()
    for line in text.split("\n"):
        d.add_paragraph(line)
    b = io.BytesIO()
    d.save(b)
    return b.getvalue()


def experienced_text() -> str:
    return (FIX / "experienced_resume.txt").read_text(encoding="utf-8")


def setup_profile(c, text: str, *, verify_all: bool = True) -> dict:
    r = c.post("/api/profile/upload", files={"file": ("resume.docx", docx_bytes(text), "application/octet-stream")})
    assert r.status_code == 200, r.text
    draft = r.json()["draft"]
    data = draft["data"]
    for k in ("name", "email", "phone", "location", "headline"):
        if data["basics"][k]["value"]:
            data["basics"][k]["status"] = "verified"
    if verify_all:
        for k in ("skills", "experience", "education", "projects", "certifications", "links", "achievements"):
            for i in data[k]:
                i["status"] = "verified"
    else:
        for i in data["education"]:  # minimum needed to confirm; skills/experience stay unverified
            i["status"] = "verified"
    assert c.put(f"/api/profile/versions/{draft['id']}", json={"data": data}).status_code == 200
    r = c.post(f"/api/profile/versions/{draft['id']}/confirm")
    assert r.status_code == 200, r.text
    return r.json()["version"]


def add_job(c, title, company, desc, location="Bengaluru, India", confirm_open=True) -> dict:
    r = c.post("/api/jobs/manual", json={"title": title, "company": company, "location": location, "description": desc})
    assert r.status_code == 200, r.text
    job = r.json()["job"]
    if confirm_open and any(g["key"] == "posting_status" and g["result"] == "needs_verification" for g in job["eligibility"]["gates"]):
        r = c.post(f"/api/jobs/{job['id']}/confirm-gate", json={"key": "posting_status"})
        assert r.status_code == 200, r.text
        job = r.json()
    return job


def wait_task(c, tid, timeout=90):
    end = time.time() + timeout
    while time.time() < end:
        t = c.get(f"/api/tasks/{tid}").json()
        if t["status"] not in ("queued", "running"):
            return t
        time.sleep(0.3)
    raise TimeoutError(tid)
