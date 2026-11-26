"""Smoke-test a deployed JobPilot without credentials.
Usage: python tests/verify_live.py https://jobpilot-xg5p.onrender.com"""
from __future__ import annotations

import sys

import httpx

BASE = sys.argv[1].rstrip("/") if len(sys.argv) > 1 else "https://jobpilot-xg5p.onrender.com"
checks: list[tuple[str, bool, str]] = []


def check(name, cond, detail=""):
    checks.append((name, bool(cond), detail))
    print(f"{'PASS' if cond else 'FAIL'}  {name}  {detail}")


with httpx.Client(timeout=60, follow_redirects=False) as c:
    r = c.get(BASE + "/api/health")
    check("health endpoint OK", r.status_code == 200 and r.json().get("status") == "ok", r.text[:80])
    s = c.get(BASE + "/api/session").json()
    check("production requires sign-in", s.get("auth_required") is True and s.get("environment") == "production", str(s))
    check("access password configured", s.get("password_configured") is True)
    check("API protected (401 without session)", c.get(BASE + "/api/dashboard").status_code == 401)
    check("profile data protected", c.get(BASE + "/api/profile").status_code == 401)
    check("CSRF header enforced", c.post(BASE + "/api/discovery/run").status_code in (401, 403))
    bad = c.post(BASE + "/api/auth/login", json={"password": "definitely-wrong"}, headers={"X-Requested-With": "JobPilot"})
    check("wrong password rejected", bad.status_code == 401, str(bad.status_code))
    page = c.get(BASE + "/")
    h = page.headers
    check("SPA served", page.status_code == 200 and "/static/js/main.js" in page.text)
    check("CSP header", "script-src 'self'" in h.get("content-security-policy", ""))
    check("nosniff + frame deny", h.get("x-content-type-options") == "nosniff" and h.get("x-frame-options") == "DENY")
    check("static assets", c.get(BASE + "/static/js/main.js").status_code == 200 and c.get(BASE + "/static/css/app.css").status_code == 200)
    check("deep link served by SPA", c.get(BASE + "/discover").status_code == 200)
    check("API docs hidden in production", c.get(BASE + "/api/docs").status_code in (401, 404))
    http = c.get(BASE.replace("https://", "http://") + "/")
    check("HTTP redirects to HTTPS", http.status_code in (301, 302, 307, 308) and http.headers.get("location", "").startswith("https://"))

failed = [n for n, ok, _ in checks if not ok]
print(f"\n{len(checks) - len(failed)}/{len(checks)} live checks passed")
sys.exit(1 if failed else 0)
