"""Contacts and outreach drafts. JobPilot never sends anything: drafts are
edited/approved in the app and the user sends them personally."""
from __future__ import annotations

import re

from .. import audit, db
from . import profile_service as ps

VERIFICATION = {"verified": "Verified", "public_unverified": "Publicly listed (unverified)", "inferred": "Inferred"}
KINDS = {"referral": "Referral request", "recruiter_email": "Recruiter / HR email", "connection": "Connection message",
         "follow_up": "Follow-up"}
EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{2,}")


def validate_contact(c: dict) -> dict:
    name = (c.get("name") or "").strip()
    if not name:
        raise ValueError("Contact name is required")
    ver = c.get("verification") or "public_unverified"
    if ver not in VERIFICATION:
        raise ValueError("Verification must be verified, public_unverified or inferred")
    email = (c.get("email") or "").strip() or None
    if email and not EMAIL_RE.fullmatch(email):
        raise ValueError("Email address looks invalid")
    for f in ("source_url", "profile_url"):
        v = (c.get(f) or "").strip()
        if v and not re.match(r"https?://", v):
            raise ValueError(f"{f.replace('_', ' ').capitalize()} must start with http:// or https://")
    if ver == "verified" and not (c.get("source_url") or "").strip():
        raise ValueError("A 'verified' contact needs a source URL showing where it was verified")
    return {"name": name[:120], "role": (c.get("role") or "").strip()[:120] or None, "company": (c.get("company") or "").strip()[:120] or None,
            "email": email, "profile_url": (c.get("profile_url") or "").strip() or None, "source_url": (c.get("source_url") or "").strip() or None,
            "source_label": (c.get("source_label") or "").strip()[:120] or None, "verification": ver,
            "relationship": (c.get("relationship") or "").strip()[:300] or None, "notes": (c.get("notes") or "").strip()[:1000] or None,
            "job_id": c.get("job_id") or None}


def save_contact(c: dict, cid: int | None = None) -> dict:
    v = validate_contact(c)
    if v["job_id"] and not db.query_one("SELECT id FROM jobs WHERE id = ?", (v["job_id"],)):
        raise ValueError("Linked job not found")
    cols = list(v)
    if cid:
        db.execute(f"UPDATE contacts SET {', '.join(f'{k} = ?' for k in cols)} WHERE id = ?", (*v.values(), cid))
    else:
        cid = db.execute(f"INSERT INTO contacts({', '.join(cols)}, created_at) VALUES ({', '.join('?' for _ in cols)}, ?)",
                         (*v.values(), db.now_iso()))
    audit.log("decision", "contact_saved", f"Contact saved: {v['name']} ({VERIFICATION[v['verification']]})", entity_type="contact",
              entity_id=cid, source_ref=v["source_url"])
    return db.query_one("SELECT * FROM contacts WHERE id = ?", (cid,))


def contacts_from_job(job: dict) -> list[dict]:
    """Public contact details explicitly present in the job posting (labelled unverified)."""
    found = []
    for email in sorted(set(EMAIL_RE.findall(job.get("description") or "")))[:5]:
        if db.query_one("SELECT id FROM contacts WHERE email = ? AND (job_id = ? OR job_id IS NULL)", (email, job["id"])):
            continue
        local = email.split("@")[0]
        found.append(save_contact({"name": local.replace(".", " ").title() if "." in local else "Hiring contact",
                                   "email": email, "company": job.get("company"), "role": "Listed in job posting",
                                   "source_url": job.get("source_url"), "source_label": "Job posting text",
                                   "verification": "public_unverified", "job_id": job["id"],
                                   "notes": "Email address appears in the job description. Name is derived from the address and may be wrong."}))
    return found


def generate_draft(kind: str, job_id: str | None, contact_id: int | None) -> dict:
    if kind not in KINDS:
        raise ValueError("Unknown draft type")
    profile = ps.active_profile()
    if not profile:
        raise PermissionError("Verify your profile first")
    vp = ps.verified_view(profile)
    prefs = ps.get_preferences()
    job = db.query_one("SELECT * FROM jobs WHERE id = ?", (job_id,)) if job_id else None
    contact = db.query_one("SELECT * FROM contacts WHERE id = ?", (contact_id,)) if contact_id else None
    if job_id and not job:
        raise ValueError("Job not found")
    if contact_id and not contact:
        raise ValueError("Contact not found")
    if kind == "follow_up":
        if not prefs.get("allow_follow_up", True):
            raise PermissionError("Follow-ups are disabled in your preferences")
        if db.query_one("SELECT id FROM outreach_drafts WHERE kind = 'follow_up' AND IFNULL(contact_id,0) = IFNULL(?,0) "
                        "AND IFNULL(job_id,'') = IFNULL(?, '')", (contact_id, job_id)):
            raise PermissionError("At most one follow-up per contact and job is allowed")
    name = vp["basics"].get("name") or "[Your name]"
    first = contact["name"].split(" ")[0] if contact and contact.get("name") else ""
    greet = f"Hi {first}," if first and contact.get("verification") != "inferred" else "Hello,"
    role = job["title"] if job else "[role]"
    company = (job or {}).get("company") or (contact or {}).get("company") or "[company]"
    match = db.jloads(job["match"], {}) if job else {}
    skills = [s["skill"] for s in (match or {}).get("matched_skills", [])][:3] or [s["name"] for s in vp.get("skills", [])][:3]
    exp = ps.experience_summary(vp)
    bg = (f"I have {exp['years']:g} years of experience" if exp["years"] >= 1 else "I'm early in my career") + \
         (f" working with {', '.join(skills)}" if skills else "")
    link = (job or {}).get("source_url") or ""
    rel = (contact or {}).get("relationship")
    link_part = f" ({link})" if link else ""
    if kind == "referral":
        subject = f"Referral request: {role} at {company}"
        intro = f"We are connected through: {rel}. " if rel else ""
        body = (f"{greet}\n\n{intro}I'm applying for the {role} role at {company}{link_part}. {bg}. "
                f"Would you be open to referring me, or sharing any advice about the team?\n\n"
                f"I'm happy to send my resume and answer any questions. Thank you for considering it.\n\nBest regards,\n{name}")
    elif kind == "recruiter_email":
        subject = f"Application for {role} — {name}"
        body = (f"{greet}\n\nI'm interested in the {role} position at {company}{link_part}. {bg}.\n\n"
                f"I've attached my resume and would welcome the chance to discuss how I could contribute.\n\nKind regards,\n{name}"
                f"\n{vp['basics'].get('email') or ''}").rstrip()
    elif kind == "connection":
        subject = None
        body = f"{greet} I'm interested in the {role} role at {company}. {bg}. I'd value connecting and learning about the team. — {name}"
        if len(body) > 300:
            body = f"{greet} I'm interested in the {role} role at {company} and would value connecting. — {name}"
        body = body[:300]
    else:
        subject = f"Following up: {role} application"
        body = (f"{greet}\n\nI wanted to follow up on my application for the {role} role at {company}. I remain very interested and "
                f"would be glad to provide anything else you need.\n\nThank you for your time,\n{name}")
    now = db.now_iso()
    did = db.execute("INSERT INTO outreach_drafts(contact_id, job_id, kind, subject, body, status, created_at, updated_at) VALUES (?,?,?,?,?,?,?,?)",
                     (contact_id, job_id, kind, subject, body, "draft", now, now))
    audit.log("document", "outreach_drafted", f"{KINDS[kind]} draft created for {company}. Not sent — drafts are never sent automatically.",
              entity_type="outreach", entity_id=did)
    return get_draft(did)


def get_draft(did: int) -> dict | None:
    return db.query_one("SELECT d.*, c.name AS contact_name, c.email AS contact_email, c.verification AS contact_verification, "
                        "j.title AS job_title, j.company AS job_company FROM outreach_drafts d "
                        "LEFT JOIN contacts c ON c.id = d.contact_id LEFT JOIN jobs j ON j.id = d.job_id WHERE d.id = ?", (did,))


def update_draft(did: int, fields: dict) -> dict:
    d = get_draft(did)
    if not d:
        raise LookupError("Draft not found")
    if d["status"] == "sent_by_user":
        raise PermissionError("This message was already marked as sent")
    body = (fields.get("body") if fields.get("body") is not None else d["body"]).strip()
    if not body:
        raise ValueError("Message body cannot be empty")
    if d["kind"] == "connection" and len(body) > 300:
        raise ValueError("Connection messages must be 300 characters or fewer")
    subject = fields.get("subject", d["subject"])
    status = d["status"]
    action = fields.get("action")
    if action == "approve":
        status = "approved"
    elif action == "mark_sent":
        if d["status"] != "approved":
            raise PermissionError("Approve the draft before marking it as sent by you")
        status = "sent_by_user"
    elif body != d["body"] or subject != d["subject"]:
        status = "draft"  # edits require re-approval
    now = db.now_iso()
    db.execute("UPDATE outreach_drafts SET subject = ?, body = ?, status = ?, updated_at = ?, "
               "approved_at = CASE WHEN ? = 'approved' THEN ? ELSE approved_at END WHERE id = ?",
               (subject, body, status, now, status, now, did))
    if action in ("approve", "mark_sent"):
        audit.log("decision", f"outreach_{action}",
                  f"{KINDS[d['kind']]} draft {'approved (not sent)' if action == 'approve' else 'marked as sent by you'}",
                  entity_type="outreach", entity_id=did)
    return get_draft(did)


def delete_draft(did: int) -> None:
    d = get_draft(did)
    if not d:
        raise LookupError("Draft not found")
    db.execute("DELETE FROM outreach_drafts WHERE id = ?", (did,))
    audit.log("decision", "outreach_deleted", f"Deleted {KINDS[d['kind']]} draft", entity_type="outreach", entity_id=did)
