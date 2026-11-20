"""Proves the application-preparation automation never performs a final submit/send.

1. Static check: browser_prep.py contains no click/press/submit/tap/check/dispatch calls.
2. Behavioural check: run prefill() on a local test form whose submit handler records
   any submission; assert fields were filled and the form was never submitted.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "backend" / "app" / "services" / "browser_prep.py"

FORM = """<!doctype html><html><body>
<form id="f" action="/never" onsubmit="window.__submitted = true; return false;">
  <label for="fn">First name</label><input id="fn" name="first_name">
  <label for="ln">Last name</label><input id="ln" name="last_name">
  <label for="em">Email</label><input id="em" type="email" name="email">
  <label for="ph">Phone</label><input id="ph" name="phone">
  <label for="pw">Password</label><input id="pw" type="password" name="password">
  <label for="li">LinkedIn profile</label><input id="li" name="linkedin">
  <label for="cv">Resume/CV</label><input id="cv" type="file" name="resume">
  <label for="card">Card number</label><input id="card" name="card">
  <button id="submit" type="submit" onclick="window.__clicked = true">Submit application</button>
</form></body></html>"""


def test_static_no_interaction_calls():
    code = SRC.read_text(encoding="utf-8")
    body = "\n".join(l for l in code.splitlines() if not l.strip().startswith(("#", '"', "-")))
    forbidden = re.findall(r"\.(click|dblclick|press|tap|check|submit|dispatch_event|select_option|type)\(|keyboard\.|requestSubmit", body)
    assert forbidden == [], f"Forbidden interaction calls found: {forbidden}"


def test_prefill_never_submits(tmp_path):
    pw = pytest.importorskip("playwright.sync_api")
    from app.services.browser_prep import prefill
    resume = tmp_path / "resume.pdf"
    resume.write_bytes(b"%PDF-1.4 test")
    page_file = tmp_path / "form.html"
    page_file.write_text(FORM, encoding="utf-8")
    with pw.sync_playwright() as p:
        try:
            b = p.chromium.launch()
        except Exception as e:  # browser binaries not installed
            pytest.skip(f"Chromium not available: {e}")
        page = b.new_page()
        page.goto(page_file.as_uri())
        log = prefill(page, {"first_name": "Priya", "last_name": "Sharma", "email": "p@example.com", "phone": "+91 98765 43210",
                             "linkedin": "https://linkedin.com/in/x"}, str(resume))
        assert page.input_value("#fn") == "Priya"
        assert page.input_value("#em") == "p@example.com"
        assert page.input_value("#pw") == ""          # password never touched
        assert page.input_value("#card") == ""        # payment never touched
        assert page.evaluate("document.getElementById('cv').files.length") == 1
        assert page.evaluate("window.__submitted === true") is False
        assert page.evaluate("window.__clicked === true") is False
        assert any(x["action"] == "skipped" and "sensitive" in x.get("reason", "") for x in log)
        b.close()
