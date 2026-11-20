"""Application preparation with Playwright (local Windows use only).

SAFETY CONTRACT (enforced by tests/test_browser_safety.py):
- This module only navigates, fills text inputs and attaches the resume file.
- It never clicks, presses keys, submits forms, or calls any submit/send action.
- The user logs in personally in the opened window; passwords are never requested or stored.
- Password, payment and CAPTCHA fields are never touched.
- After prefilling it stops: the browser stays open for the user to review and submit.

The worker runs as a separate process (`python -m app.services.browser_prep <job-file>`)
so the browser window outlives the HTTP request and stays under the user's control.
"""
from __future__ import annotations

import json
import os
import re
import subprocess
import sys
from pathlib import Path

from ..config import ROOT, settings

FIELD_RULES = [
    ("first_name", re.compile(r"first\s*name|given\s*name|fname", re.I)),
    ("last_name", re.compile(r"last\s*name|surname|family\s*name|lname", re.I)),
    ("full_name", re.compile(r"^\s*(full\s*)?name\s*\*?$|your name|candidate name", re.I)),
    ("email", re.compile(r"e-?mail", re.I)),
    ("phone", re.compile(r"phone|mobile|contact number", re.I)),
    ("location", re.compile(r"current location|^location|city", re.I)),
    ("linkedin", re.compile(r"linkedin", re.I)),
    ("github", re.compile(r"github", re.I)),
    ("website", re.compile(r"website|portfolio|personal site", re.I)),
]
NEVER_TOUCH = re.compile(r"password|passcode|card|cvv|iban|account number|otp|captcha|ssn|aadhaar|social security", re.I)


def playwright_available() -> tuple[bool, str]:
    if settings.is_production:
        return False, ("Browser preparation runs only in the local Windows app (it opens a real browser window on your computer). "
                       "On the hosted version, use the manual workflow: open the application page and copy the prepared answers.")
    try:
        import playwright  # noqa: F401
    except ImportError:
        return False, "Playwright is not installed. Run scripts\\install_browser.ps1 (pip install playwright && playwright install chromium)."
    return True, "Available"


def build_values(vp: dict) -> dict:
    b = vp.get("basics", {})
    name = (b.get("name") or "").strip()
    parts = name.split()
    links = {l["label"].lower(): l["url"] for l in vp.get("links", [])}
    vals = {"first_name": parts[0] if parts else None, "last_name": " ".join(parts[1:]) if len(parts) > 1 else None,
            "full_name": name or None, "email": b.get("email"), "phone": b.get("phone"), "location": b.get("location"),
            "linkedin": links.get("linkedin"), "github": links.get("github"), "website": links.get("portfolio")}
    return {k: v for k, v in vals.items() if v}


def launch(job_url: str, values: dict, resume_path: Path | None, log_path: Path) -> dict:
    ok, why = playwright_available()
    if not ok:
        raise RuntimeError(why)
    spec = {"url": job_url, "values": values, "resume": str(resume_path) if resume_path else None, "log": str(log_path),
            "profile_dir": str(settings.data_dir / "browser-profile")}
    spec_path = log_path.with_suffix(".spec.json")
    spec_path.write_text(json.dumps(spec), encoding="utf-8")
    env = dict(os.environ)
    env["PYTHONPATH"] = str(ROOT / "backend")
    flags = 0x00000008 if os.name == "nt" else 0  # DETACHED_PROCESS on Windows
    subprocess.Popen([sys.executable, "-m", "app.services.browser_prep", str(spec_path)], env=env, cwd=str(ROOT / "backend"),
                     creationflags=flags, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    return {"launched": True, "log": str(log_path)}


def _label_text(page, el) -> str:
    """Accessible-ish label of an input: <label for>, aria-label, placeholder, name, id."""
    return page.evaluate(
        """(el) => {
            let t = [];
            if (el.id) { const l = document.querySelector(`label[for="${CSS.escape(el.id)}"]`); if (l) t.push(l.innerText); }
            const pl = el.closest('label'); if (pl) t.push(pl.innerText);
            t.push(el.getAttribute('aria-label') || '', el.placeholder || '', el.name || '', el.id || '');
            return t.join(' | ');
        }""", el)


def prefill(page, values: dict, resume: str | None) -> list[dict]:
    """Fill supported empty fields. Uses only fill() and set_input_files(); never clicks or submits."""
    log = []
    used = set()
    inputs = page.query_selector_all("input, textarea")
    for el in inputs:
        typ = (el.get_attribute("type") or "text").lower()
        if typ in ("hidden", "submit", "button", "image", "reset", "checkbox", "radio", "password"):
            continue
        label = _label_text(page, el)
        if NEVER_TOUCH.search(label):
            log.append({"field": label[:80], "action": "skipped", "reason": "sensitive field — never filled"})
            continue
        if typ == "file":
            if resume and re.search(r"resume|cv", label, re.I) and "resume" not in used:
                el.set_input_files(resume)
                used.add("resume")
                log.append({"field": label[:80], "action": "attached", "value": Path(resume).name})
            continue
        if not el.is_visible() or not el.is_editable():
            continue
        if (el.input_value() or "").strip():
            log.append({"field": label[:80], "action": "skipped", "reason": "already has a value"})
            continue
        for key, rx in FIELD_RULES:
            if key in used or key not in values:
                continue
            if rx.search(label):
                el.fill(values[key])
                used.add(key)
                log.append({"field": label[:80], "action": "filled", "key": key, "value": values[key]})
                break
    return log


def _worker(spec_path: str) -> None:
    from playwright.sync_api import sync_playwright
    spec = json.loads(Path(spec_path).read_text(encoding="utf-8"))
    out = {"status": "starting", "filled": [], "error": None}
    log_path = Path(spec["log"])

    def save():
        log_path.write_text(json.dumps(out, indent=2), encoding="utf-8")

    save()
    try:
        with sync_playwright() as p:
            ctx = p.chromium.launch_persistent_context(spec["profile_dir"], headless=False, no_viewport=True)
            page = ctx.pages[0] if ctx.pages else ctx.new_page()
            page.goto(spec["url"], wait_until="domcontentloaded", timeout=45000)
            page.wait_for_timeout(2500)
            out["status"] = "prefilling"
            save()
            try:
                out["filled"] = prefill(page, spec["values"], spec.get("resume"))
            except Exception as e:  # unsupported/ambiguous form: stop safely
                out["error"] = f"Stopped safely on an unsupported form: {type(e).__name__}"
            out["status"] = "paused_for_user"
            out["message"] = ("Paused before submission. Review every field in the browser window, sign in yourself if needed, "
                              "and submit personally. JobPilot will not click Submit.")
            save()
            # Keep the window open until the user closes it.
            while ctx.pages:
                try:
                    ctx.pages[0].wait_for_timeout(1000)
                except Exception:
                    break
            out["status"] = "browser_closed"
            save()
    except Exception as e:
        out["status"] = "failed"
        out["error"] = f"{type(e).__name__}: {str(e)[:200]}"
        save()


if __name__ == "__main__":
    _worker(sys.argv[1])
