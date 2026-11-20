from __future__ import annotations

from typing import Any

from fastapi import APIRouter, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse, PlainTextResponse
from pydantic import BaseModel

from .. import audit, db, tasks
from ..config import settings
from ..files import data_path
from ..services import pipeline, profile_service as ps, resume_parser

router = APIRouter(prefix="/api", tags=["profile"])
ALLOWED = {".pdf": "application/pdf", ".docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}
SUPPORTING = {".pdf", ".docx", ".png", ".jpg", ".jpeg"}


class ProfileData(BaseModel):
    data: dict[str, Any]


class Preferences(BaseModel):
    model_config = {"extra": "allow"}


class Company(BaseModel):
    name: str
    ats: str
    token: str | None = None
    careers_url: str | None = None
    enabled: bool = True
    notes: str | None = None


def _summary():
    active = ps.active_profile()
    draft = ps.latest_draft()
    vp = ps.verified_view(active) if active else None
    return {
        "active": active, "draft": draft, "versions": ps.list_versions(),
        "experience": ps.experience_summary(vp) if vp else None,
        "uploads": db.query("SELECT id, kind, filename, size, sha256, created_at FROM uploads ORDER BY created_at DESC"),
    }


@router.get("/profile")
def get_profile():
    return _summary()


async def _read_upload(file: UploadFile, allowed: set[str]) -> bytes:
    name = (file.filename or "").lower()
    ext = name[name.rfind("."):] if "." in name else ""
    if ext not in allowed:
        raise HTTPException(422, f"Unsupported file type '{ext or 'none'}'. Allowed: {', '.join(sorted(allowed))}")
    data = await file.read(settings.max_upload_mb * 1024 * 1024 + 1)
    if len(data) > settings.max_upload_mb * 1024 * 1024:
        raise HTTPException(413, f"File is larger than {settings.max_upload_mb} MB")
    if not data:
        raise HTTPException(422, "The file is empty")
    return data


@router.post("/profile/upload")
async def upload_resume(file: UploadFile = File(...)):
    data = await _read_upload(file, set(ALLOWED))
    try:
        up = ps.save_upload("master_resume", file.filename, data, file.content_type)
    except resume_parser.ParseError as e:
        audit.log("failure", "resume_parse_failed", f"Could not read '{file.filename}': {e}", level="warning")
        raise HTTPException(422, str(e))
    draft = ps.create_draft_from_upload(up["id"])
    return {"upload": {k: up[k] for k in ("id", "filename", "size", "sha256", "created_at")}, "draft": draft}


@router.post("/profile/supporting")
async def upload_supporting(file: UploadFile = File(...), note: str = Form("")):
    data = await _read_upload(file, SUPPORTING)
    up = ps.save_upload("supporting", file.filename, data, file.content_type)
    return {k: up[k] for k in ("id", "filename", "size", "created_at")}


@router.get("/profile/uploads/{uid}/download")
def download_upload(uid: int):
    up = db.query_one("SELECT * FROM uploads WHERE id = ?", (uid,))
    if not up:
        raise HTTPException(404, "Upload not found")
    return FileResponse(data_path(up["stored_path"]), filename=up["filename"])


@router.post("/profile/blank")
def blank_draft():
    """Start a profile without a resume (manual entry)."""
    if ps.latest_draft():
        return ps.latest_draft()
    empty = {"basics": {k: {"value": None, "status": "missing"} for k in ("name", "email", "phone", "location", "headline")},
             "summary": {"value": None, "status": "missing"}, "skills": [], "experience": [], "education": [], "projects": [],
             "certifications": [], "achievements": [], "links": [], "work_authorization": {"value": None, "status": "missing"},
             "extraction": {"method": "manual", "warnings": [], "sections_found": []}}
    active = ps.active_profile()
    vid = db.execute("INSERT INTO profile_versions(version, status, data, parent_id, note, created_at) VALUES (?,?,?,?,?,?)",
                     (ps._next_version(), "draft", db.jdumps(active["data"] if active else empty), active["id"] if active else None,
                      "Manual entry", db.now_iso()))
    return ps.get_version(vid)


@router.post("/profile/update")
def start_update():
    return ps.new_draft_from_active()


@router.put("/profile/versions/{vid}")
def save_draft(vid: int, body: ProfileData):
    return ps.update_draft(vid, body.data)


@router.delete("/profile/versions/{vid}")
def discard_draft(vid: int):
    v = ps.get_version(vid)
    if not v:
        raise HTTPException(404, "Version not found")
    if v["status"] != "draft":
        raise HTTPException(409, "Only drafts can be discarded; verified versions are kept for history")
    db.execute("DELETE FROM profile_versions WHERE id = ?", (vid,))
    audit.log("profile", "draft_discarded", f"Discarded profile draft v{v['version']}")
    return {"ok": True}


@router.post("/profile/versions/{vid}/confirm")
def confirm(vid: int):
    v = ps.confirm_draft(vid)
    t = None
    if db.query_one("SELECT COUNT(*) n FROM jobs")["n"]:
        t = tasks.submit("reevaluate", pipeline.reevaluate_all, label="Re-evaluating jobs against your updated profile")
    return {"version": v, "task": t}


@router.get("/profile/cv.md", response_class=PlainTextResponse)
def cv_md():
    p = data_path("cv.md")
    if not p.exists():
        raise HTTPException(404, "No verified profile yet")
    return p.read_text(encoding="utf-8")


# ---------------------------------------------------------------- preferences
@router.get("/preferences")
def get_prefs():
    return ps.get_preferences()


@router.put("/preferences")
def put_prefs(body: Preferences):
    old = ps.get_preferences()
    new = ps.save_preferences(body.model_dump())
    t = None
    if db.query_one("SELECT COUNT(*) n FROM jobs")["n"] and old != new:
        t = tasks.submit("reevaluate", pipeline.reevaluate_all, label="Re-evaluating jobs against your preferences")
    return {"preferences": new, "task": t}


@router.post("/preferences/import")
def import_prefs():
    return ps.import_profile_yml()


@router.get("/preferences/profile.yml", response_class=PlainTextResponse)
def prefs_yml():
    ps.write_profile_yml()
    return data_path("profile.yml").read_text(encoding="utf-8")


# ---------------------------------------------------------------- company registry
@router.get("/companies")
def companies():
    return ps.list_companies()


@router.post("/companies")
def add_company(c: Company):
    return ps.upsert_company(c.model_dump())


@router.put("/companies/{cid}")
def edit_company(cid: int, c: Company):
    if not db.query_one("SELECT id FROM companies WHERE id = ?", (cid,)):
        raise HTTPException(404, "Company not found")
    return ps.upsert_company(c.model_dump(), cid)


@router.delete("/companies/{cid}")
def del_company(cid: int):
    ps.delete_company(cid)
    return {"ok": True}


@router.post("/companies/import")
def import_companies():
    return {"imported": ps.import_portals_yml()}


@router.get("/companies/portals.yml", response_class=PlainTextResponse)
def portals_yml():
    ps.write_portals_yml()
    return data_path("portals.yml").read_text(encoding="utf-8")
