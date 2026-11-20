"""Audit trail: every important event goes to SQLite *and* a human-readable
JSONL file per day (data/audit/YYYY-MM-DD.jsonl). Secrets are scrubbed."""
from __future__ import annotations

import json
import re
from typing import Any

from . import db
from .config import settings
from .files import append_line, data_path

_SECRET_PATTERNS = [
    re.compile(r"(?i)(api[_-]?key|token|secret|password|authorization)(\"?\s*[:=]\s*\"?)([^\s\",]+)"),
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),
    re.compile(r"gsk_[0-9A-Za-z]{20,}"),
    re.compile(r"sk-[0-9A-Za-z_\-]{20,}"),
]


def scrub(text: str) -> str:
    if not text:
        return text
    out = text
    for p in _SECRET_PATTERNS:
        if p.groups >= 3:
            out = p.sub(lambda m: m.group(1) + m.group(2) + "[REDACTED]", out)
        else:
            out = p.sub("[REDACTED]", out)
    for secret in (settings.gemini_api_key, settings.groq_api_key, settings.openai_compat_key, settings.password):
        if secret and len(secret) > 4:
            out = out.replace(secret, "[REDACTED]")
    return out


def log(category: str, action: str, message: str, *, level: str = "info", entity_type: str | None = None,
        entity_id: Any = None, details: dict | None = None, source_ref: str | None = None,
        notify: bool = False, link: str | None = None) -> int:
    message = scrub(message)
    details_s = scrub(db.jdumps(details)) if details is not None else None
    at = db.now_iso()
    eid = db.execute(
        "INSERT INTO audit_events(at, category, action, level, entity_type, entity_id, message, details, source_ref) "
        "VALUES (?,?,?,?,?,?,?,?,?)",
        (at, category, action, level, entity_type, None if entity_id is None else str(entity_id), message, details_s, source_ref),
    )
    try:
        append_line(
            data_path("audit", f"{at[:10]}.jsonl"),
            json.dumps({"id": eid, "at": at, "category": category, "action": action, "level": level,
                        "entity_type": entity_type, "entity_id": entity_id, "message": message,
                        "source_ref": source_ref, "details": json.loads(details_s) if details_s else None},
                       ensure_ascii=False, default=str),
        )
    except OSError:
        pass
    if notify or level == "error":
        notify_user(level, message if len(message) < 120 else message[:117] + "...", None, link)
    return eid


def notify_user(level: str, title: str, body: str | None = None, link: str | None = None) -> None:
    prefs = db.get_setting("notifications", {"in_app": True})
    if not prefs.get("in_app", True) and level != "error":
        return
    db.execute("INSERT INTO notifications(at, level, title, body, link) VALUES (?,?,?,?,?)",
               (db.now_iso(), level, scrub(title), scrub(body) if body else None, link))
