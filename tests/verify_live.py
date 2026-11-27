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


with httpx.Client(timeout=90, follow_redirects=False) as c:
    r = c.get(BASE + "/health")
    check("minimal /health", r.status_code == 200 and r.json() == {"status": "ok"}, r.text[:80])
    s = c.get(BASE + "/api/session").json()
    check("anonymous session", s.get("authenticated") is False and "config" in s)
    cfg = s["config"]
    check("payments disabled", cfg.get("payments_enabled") is False)
    check("registration closed while storage is ephemeral", cfg.get("registration_open") is False, str(cfg.get("registration_open")))
    check("user API protected (401)", c.get(BASE + "/api/dashboard").status_code == 401)
    check("profile data protected (401)", c.get(BASE + "/api/profile").status_code == 401)
    check("admin API protected", c.get(BASE + "/api/admin/overview").status_code in (401, 403))
    check("CSRF header enforced", c.post(BASE + "/api/auth/logout").status_code == 403)
    bad = c.post(BASE + "/api/auth/login", json={"email": "nobody@example.com", "password": "definitely-wrong"}, headers={"X-Requested-With": "JobPilot"})
    check("wrong credentials rejected", bad.status_code in (401, 429), str(bad.status_code))
    plans = c.get(BASE + "/api/public/plans").json()["plans"]
    check("public plans from database", {p["code"] for p in plans} >= {"free", "basic", "pro"})
    page = c.get(BASE + "/")
    h = page.headers
    check("landing served", page.status_code == 200 and "/static/js/main.js" in page.text)
    check("admin app served separately", "/static/js/admin/main.js" in c.get(BASE + "/admin").text)
    check("CSP + HSTS headers", "script-src 'self'" in h.get("content-security-policy", "") and "max-age" in h.get("strict-transport-security", ""))
    check("nosniff + frame deny", h.get("x-content-type-options") == "nosniff" and h.get("x-frame-options") == "DENY")
    check("static assets", c.get(BASE + "/static/js/main.js").status_code == 200 and c.get(BASE + "/static/css/app.css").status_code == 200)
    check("API docs hidden in production", c.get(BASE + "/api/docs").status_code in (401, 404))
    http = c.get(BASE.replace("https://", "http://") + "/")
    check("HTTP redirects to HTTPS", http.status_code in (301, 302, 307, 308) and http.headers.get("location", "").startswith("https://"))

failed = [n for n, ok, _ in checks if not ok]
print(f"\n{len(checks) - len(failed)}/{len(checks)} live checks passed")
sys.exit(1 if failed else 0)
