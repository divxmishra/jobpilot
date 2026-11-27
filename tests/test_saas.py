"""Multi-user SaaS acceptance tests: auth, isolation, admin, trials, plans/limits,
analytics, notice/CTC, Career Roadmap, backups/recovery, health, maintenance."""
from __future__ import annotations

import sqlite3
from datetime import datetime, timedelta, timezone

import pytest

from conftest import register
from helpers import FRESHER_RESUME, JD_SENIOR, add_job, experienced_text, setup_profile


def owner_with_2fa(c):
    from app.services import auth
    r = c.post("/api/auth/bootstrap-owner", json={"setup_secret": "dev", "email": "owner@example.com", "password": "owner-pass-12345", "name": "Owner"})
    assert r.status_code == 200, r.text
    sec = c.post("/api/auth/2fa/setup").json()["secret"]
    assert c.post("/api/auth/2fa/enable", json={"code": auth.totp_code(sec)}).status_code == 200
    return sec


# ------------------------------------------------------------------ authentication
def test_register_login_logout_and_password_rules(client_factory):
    c = client_factory()
    assert c.post("/api/auth/register", json={"email": "a@example.com", "password": "short", "accept_terms": True}).status_code == 422
    assert c.post("/api/auth/register", json={"email": "a@example.com", "password": "long-enough-1", "accept_terms": False}).status_code == 422
    register(c, "a@example.com")
    assert c.get("/api/dashboard").status_code == 200
    assert c.post("/api/auth/register", json={"email": "A@EXAMPLE.com", "password": "long-enough-1", "accept_terms": True}).status_code == 422
    assert c.post("/api/auth/logout").status_code == 200
    assert c.get("/api/dashboard").status_code == 401
    assert c.post("/api/auth/login", json={"email": "a@example.com", "password": "wrong-pass-123"}).status_code == 401
    assert c.post("/api/auth/login", json={"email": "a@example.com", "password": "test-pass-12345"}).status_code == 200
    from app import db
    row = db.query_one("SELECT password_hash FROM users WHERE email = 'a@example.com'")
    assert row["password_hash"].startswith("scrypt$") and "test-pass" not in row["password_hash"]


def test_brute_force_lockout(client_factory):
    c = client_factory()
    register(c, "b@example.com")
    c.post("/api/auth/logout")
    codes = [c.post("/api/auth/login", json={"email": "b@example.com", "password": f"bad-{i}-xxxxx"}).status_code for i in range(10)]
    assert 429 in codes
    assert c.post("/api/auth/login", json={"email": "b@example.com", "password": "test-pass-12345"}).status_code == 429


def test_session_expiry_and_revocation(client_factory):
    from app import db
    c = client_factory()
    register(c, "s@example.com")
    db.execute("UPDATE sessions SET expires_at = ?", ((datetime.now(timezone.utc) - timedelta(minutes=1)).isoformat(),))
    assert c.get("/api/dashboard").status_code == 401


def test_password_reset_via_admin_link(client_factory):
    o, u = client_factory(), client_factory()
    owner_with_2fa(o)
    user = register(u, "reset@example.com")
    link = o.post(f"/api/admin/users/{user['id']}/action", json={"action": "password_reset_link"}).json()["result"]["link"]
    token = link.split("token=")[1]
    assert u.post("/api/auth/reset-password", json={"token": token, "password": "brand-new-pass-1"}).status_code == 200
    assert u.post("/api/auth/reset-password", json={"token": token, "password": "another-pass-12"}).status_code == 400  # single use
    assert u.get("/api/dashboard").status_code == 401  # sessions revoked
    assert u.post("/api/auth/login", json={"email": "reset@example.com", "password": "brand-new-pass-1"}).status_code == 200


def test_forgot_password_without_email_service_is_honest(client_factory):
    c = client_factory()
    r = c.post("/api/auth/forgot-password", json={"email": "nobody@example.com"}).json()
    assert r["email_enabled"] is False and "support" in r["message"].lower()


def test_owner_bootstrap_once_and_no_self_promotion(client_factory):
    o, u = client_factory(), client_factory()
    owner_with_2fa(o)
    assert o.post("/api/auth/bootstrap-owner", json={"setup_secret": "dev", "email": "x@example.com", "password": "owner-pass-12345"}).status_code == 409
    register(u, "plain@example.com")
    u.patch("/api/account", json={"name": "x", "role": "owner"})
    from app import db
    assert db.query_one("SELECT role FROM users WHERE email = 'plain@example.com'")["role"] == "user"


# ------------------------------------------------------------------ admin access control
def test_admin_requires_staff_role_and_2fa(client_factory):
    from app.services import auth
    o, u = client_factory(), client_factory()
    o.post("/api/auth/bootstrap-owner", json={"setup_secret": "dev", "email": "owner@example.com", "password": "owner-pass-12345"})
    register(u, "user@example.com")
    assert u.get("/api/admin/overview").status_code == 403
    assert u.get("/api/admin/users").status_code == 403
    assert o.get("/api/admin/overview").status_code == 428  # 2FA not set up
    sec = o.post("/api/auth/2fa/setup").json()["secret"]
    assert o.post("/api/auth/2fa/enable", json={"code": "000000"}).status_code == 401
    assert o.post("/api/auth/2fa/enable", json={"code": auth.totp_code(sec)}).status_code == 200
    assert o.get("/api/admin/overview").status_code == 200


def test_support_role_is_read_only(client_factory):
    from app.services import auth
    o, s = client_factory(), client_factory()
    owner_with_2fa(o)
    sup = register(s, "support@example.com")
    assert o.post(f"/api/admin/users/{sup['id']}/action", json={"action": "set_role", "role": "support"}).status_code == 200
    s.post("/api/auth/login", json={"email": "support@example.com", "password": "test-pass-12345"})
    sec = s.post("/api/auth/2fa/setup").json()["secret"]
    s.post("/api/auth/2fa/enable", json={"code": auth.totp_code(sec)})
    assert s.get("/api/admin/users").status_code == 200
    assert s.post("/api/admin/plans", json={"code": "x1", "name": "X"}).status_code == 403
    assert s.put("/api/admin/settings", json={"maintenance_mode": True}).status_code == 403


# ------------------------------------------------------------------ isolation
def test_user_data_isolation(client_factory):
    a, b = client_factory(), client_factory()
    register(a, "alice@example.com")
    register(b, "bob@example.com")
    setup_profile(a, experienced_text())
    job = add_job(a, "Senior Backend Engineer", "Initech", JD_SENIOR)
    from helpers import wait_task
    t = wait_task(a, a.post(f"/api/jobs/{job['id']}/resumes").json()["id"])
    rid = t["result"]["resume_id"]
    a.post(f"/api/resumes/{rid}/approve")
    app_id = a.post("/api/applications", json={"job_id": job["id"]}).json()["id"]
    assert b.get(f"/api/jobs/{job['id']}").status_code == 404
    assert b.get(f"/api/resumes/{rid}").status_code == 404
    assert b.get(f"/api/resumes/{rid}/download?format=pdf").status_code == 404
    assert b.get(f"/api/applications/{app_id}").status_code == 404
    assert b.patch(f"/api/applications/{app_id}", json={"status": "withdrawn"}).status_code == 404
    assert b.get("/api/profile").json()["active"] is None
    assert b.get("/api/jobs?view=all").json()["total"] == 0
    assert b.get("/api/activity").json()["total"] == 0 or all(e["user_id"] != 1 for e in b.get("/api/activity").json()["items"])
    assert b.get("/api/applications").json()["items"] == []
    assert b.get("/api/subscription").json()["current"]["source"] == "free"
    upl = a.get("/api/profile").json()["uploads"][0]["id"]
    assert b.get(f"/api/profile/uploads/{upl}/download").status_code == 404
    assert b.get("/api/admin/users").status_code == 403


# ------------------------------------------------------------------ trials
def test_trial_lifecycle(client_factory):
    from app import db
    o, u = client_factory(), client_factory()
    owner_with_2fa(o)
    user = register(u, "trial@example.com")
    assert u.get("/api/subscription").json()["trial"] is None
    setup_profile(u, FRESHER_RESUME)  # completing profile starts the trial
    s = u.get("/api/subscription").json()
    t = s["trial"]
    assert t["status"] == "active" and s["current"]["source"] == "trial" and s["current"]["plan"]["code"] == "pro"
    start, end = datetime.fromisoformat(t["started_at"]), datetime.fromisoformat(t["ends_at"])
    assert (end - start).days == 30
    o.post(f"/api/admin/users/{user['id']}/action", json={"action": "extend_trial", "days": 5})
    assert (datetime.fromisoformat(u.get("/api/subscription").json()["trial"]["ends_at"]) - start).days == 35
    # expire (server-side timestamps only)
    db.execute("UPDATE trials SET ends_at = ? WHERE user_id = ?", ((datetime.now(timezone.utc) - timedelta(seconds=1)).isoformat(), user["id"]))
    s = u.get("/api/subscription").json()
    assert s["trial"]["status"] == "expired" and s["current"]["source"] == "free" and s["current"]["plan"]["code"] == "free"
    assert u.get("/api/profile").json()["active"] is not None  # data kept after trial
    assert u.get("/api/notifications").json()["unread"] >= 1


def test_trial_duration_configurable_for_future_users(client_factory):
    o, u = client_factory(), client_factory()
    owner_with_2fa(o)
    assert o.put("/api/admin/settings", json={"trial_days": 14}).status_code == 200
    register(u, "t14@example.com")
    setup_profile(u, FRESHER_RESUME)
    t = u.get("/api/subscription").json()["trial"]
    assert (datetime.fromisoformat(t["ends_at"]) - datetime.fromisoformat(t["started_at"])).days == 14


# ------------------------------------------------------------------ plans, limits, overrides, grants
def test_limits_enforced_and_overrides(client_factory):
    o, u = client_factory(), client_factory()
    owner_with_2fa(o)
    user = register(u, "lim@example.com")  # Free plan (no trial yet)
    for i in range(5):
        assert u.post("/api/jobs/manual", json={"title": f"Role {i}", "company": f"C{i}", "description": JD_SENIOR}).status_code == 200
    r = u.post("/api/jobs/manual", json={"title": "Role 6", "company": "C6", "description": JD_SENIOR})
    assert r.status_code == 402 and r.json()["limit"]["key"] == "job_searches_per_day"
    assert o.put(f"/api/admin/users/{user['id']}/overrides", json={"key": "job_searches_per_day", "value": None, "reason": "support"}).status_code == 200
    assert u.post("/api/jobs/manual", json={"title": "Role 7", "company": "C7", "description": JD_SENIOR}).status_code == 200
    assert u.post("/api/discovery/run").status_code == 402  # automation not on Free
    usage = {l["key"]: l for l in u.get("/api/subscription").json()["usage"]["limits"]}
    assert usage["job_searches_per_day"]["limit"] is None and usage["job_searches_per_month"]["used"] == 6


def test_grants_lifetime_and_revoke(client_factory):
    o, u = client_factory(), client_factory()
    owner_with_2fa(o)
    user = register(u, "grant@example.com")
    assert o.post(f"/api/admin/users/{user['id']}/action", json={"action": "grant_lifetime", "plan_code": "basic"}).status_code == 200
    cur = u.get("/api/subscription").json()["current"]
    assert cur["plan"]["code"] == "basic" and cur["source"] == "lifetime"
    assert u.post("/api/discovery/run").status_code == 200  # automation on Basic
    o.post(f"/api/admin/users/{user['id']}/action", json={"action": "revoke_access"})
    assert u.get("/api/subscription").json()["current"]["source"] == "free"


def test_plan_pricing_configurable_and_payments_disabled(client_factory):
    o, u = client_factory(), client_factory()
    owner_with_2fa(o)
    register(u, "price@example.com")
    plans = o.get("/api/admin/plans").json()["plans"]
    basic = next(p for p in plans if p["code"] == "basic")
    assert u.post("/api/subscription/quote", json={"plan_code": "basic"}).status_code == 409  # price not set yet
    body = dict(basic, monthly_price=199, features="More searches\nAutomation")
    assert o.put(f"/api/admin/plans/{basic['id']}", json=body).status_code == 200
    pub = {p["code"]: p for p in u.get("/api/public/plans").json()["plans"]}
    assert pub["basic"]["monthly_price"] == 199 and pub["basic"]["price_set"]
    o.post("/api/admin/coupons", json={"code": "LAUNCH10", "discount_pct": 10})
    q = u.post("/api/subscription/quote", json={"plan_code": "basic", "coupon": "launch10"}).json()
    assert q["amount"] == 179.1 and q["payments_enabled"] is False
    rev = o.get("/api/admin/revenue").json()
    assert rev["actual"]["gross"] == 0 and rev["payments_enabled"] is False


def test_manual_payment_is_labelled_and_counted(client_factory):
    o, u = client_factory(), client_factory()
    owner_with_2fa(o)
    user = register(u, "pay@example.com")
    assert o.post("/api/admin/payments/manual", json={"user_id": user["id"], "amount": 499, "reference": "UPI-123", "plan_code": "pro"}).status_code == 422  # needs attestation
    assert o.post("/api/admin/payments/manual", json={"user_id": user["id"], "amount": 499, "reference": "UPI-123", "plan_code": "pro", "attest": True}).status_code == 200
    rev = o.get("/api/admin/revenue").json()
    assert rev["actual"]["gross"] == 499 and rev["actual"]["manual_records"] == 1 and rev["payments"][0]["is_manual"] == 1
    assert o.get("/api/admin/overview").json()["snapshot"]["paid_subscribers"] == 1


# ------------------------------------------------------------------ analytics
def test_events_recorded_and_dashboard_matches_db(client_factory):
    from app import db
    o, u = client_factory(), client_factory()
    owner_with_2fa(o)
    register(u, "ev@example.com")
    setup_profile(u, experienced_text())
    add_job(u, "Senior Backend Engineer", "Initech", JD_SENIOR)
    ov = o.get("/api/admin/overview").json()
    assert ov["current"]["job_searches"] == db.query_one("SELECT COUNT(*) n FROM activity_events WHERE event='job_search'")["n"] == 1
    assert ov["snapshot"]["total_users"] == db.query_one("SELECT COUNT(*) n FROM users")["n"]
    rows = db.query("SELECT metadata FROM activity_events")
    blob = " ".join(r["metadata"] or "" for r in rows)
    assert "Priya" not in blob and "Designed REST APIs" not in blob and "password" not in blob.lower()
    events = {r["event"] for r in db.query("SELECT event FROM activity_events")}
    assert {"registration", "resume_uploaded", "profile_setup_completed", "trial_started", "job_search"} <= events
    tl = o.get(f"/api/admin/users/{db.query_one('SELECT id FROM users WHERE email = ?', ('ev@example.com',))['id']}").json()["timeline"]
    assert tl and tl[0]["at"] >= tl[-1]["at"]


def test_analytics_opt_out_and_level_off(client_factory):
    from app import db
    o, u = client_factory(), client_factory()
    owner_with_2fa(o)
    register(u, "opt@example.com")
    u.patch("/api/account", json={"analytics_opt_out": True})
    u.post("/api/jobs/manual", json={"title": "R", "company": "C", "description": JD_SENIOR})
    assert db.query_one("SELECT COUNT(*) n FROM activity_events WHERE event = 'job_search'")["n"] == 0


def test_outcomes_funnel_distinguishes_user_reported(client_factory):
    o = client_factory()
    owner_with_2fa(o)
    f = o.get("/api/admin/analytics").json()
    actors = {x["stage"]: x["actor"] for x in f["funnel"]}
    assert actors["Applications submitted"] == "User-reported" and actors["Jobs discovered"].startswith("JobPilot")
    assert f["verified_outcomes"] == 0 and "causation" in f["note"]


# ------------------------------------------------------------------ notice period & CTC
def test_notice_and_ctc_gates(fresh):
    setup_profile(fresh, experienced_text())
    fresh.put("/api/preferences", json={"notice_period": "60", "min_ctc_lpa": 20, "salary_mismatch_mode": "hard"})
    jd = JD_SENIOR + "\nImmediate joiners only.\nCTC: 10 - 15 LPA."
    job = add_job(fresh, "Senior Backend Engineer", "LowPay", jd)
    gates = {g["key"]: g for g in job["eligibility"]["gates"]}
    assert gates["notice_period"]["result"] == "fail" and "Immediate joiners" in gates["notice_period"]["evidence"]
    assert gates["ctc"]["result"] == "fail" and job["status"] == "not_eligible"
    fresh.put("/api/preferences", json={"notice_period": "immediate", "salary_mismatch_mode": "warning"})
    job = fresh.post(f"/api/jobs/{job['id']}/evaluate").json()
    gates = {g["key"]: g for g in job["eligibility"]["gates"]}
    assert gates["notice_period"]["result"] == "pass" and gates["ctc"]["result"] == "warning"
    plain = add_job(fresh, "Senior Backend Engineer", "Quiet Co", JD_SENIOR)
    g2 = {g["key"]: g for g in plain["eligibility"]["gates"]}
    assert g2["notice_period"]["result"] == "unknown" and g2["ctc"]["result"] == "unknown"  # missing ≠ failure
    assert plain["status"] == "qualified"
    assert fresh.put("/api/preferences", json={"notice_period": "45"}).status_code == 422
    assert fresh.put("/api/preferences", json={"min_ctc_lpa": 30, "expected_ctc_lpa": 20}).status_code == 422


# ------------------------------------------------------------------ career roadmap
def test_career_roadmap_real_aggregation(fresh):
    from helpers import JD_ANDROID
    setup_profile(fresh, experienced_text())
    for i in range(3):
        add_job(fresh, f"Android Developer {i}", f"App{i}", JD_ANDROID)
    add_job(fresh, "Senior Backend Engineer", "Initech", JD_SENIOR)
    r = fresh.get("/api/roadmap").json()
    assert r["jobs_analyzed"] == 4
    skills = {s["skill"]: s for s in r["skills"]}
    assert "Kotlin" in skills and skills["Kotlin"]["required_in"] == 3
    assert skills["Kotlin"]["classification"] == "missing"
    assert "Python" not in skills  # verified skills never appear as gaps
    assert all(isinstance(s.get("unlock_estimate", 0), int) for s in r["skills"])
    assert "not guarantee" in r["disclaimer"]
    assert fresh.put("/api/roadmap/skills", json={"skill": "Kotlin", "status": "learning"}).status_code == 200
    assert {s["skill"]: s for s in fresh.get("/api/roadmap").json()["skills"]}["Kotlin"]["status"] == "learning"
    assert fresh.put("/api/roadmap/skills", json={"skill": "Kotlin", "status": "expert"}).status_code == 422
    assert fresh.get("/api/roadmap/history").json()


def test_roadmap_plan_entitlement(client_factory):
    o, u = client_factory(), client_factory()
    owner_with_2fa(o)
    user = register(u, "rm@example.com")
    o.put(f"/api/admin/users/{user['id']}/overrides", json={"key": "career_roadmap", "value": False, "reason": "test"})
    assert u.get("/api/roadmap").status_code == 402


# ------------------------------------------------------------------ backups & recovery
def test_backup_create_verify_and_restore_missing_db(fresh, tmp_path):
    from app import db
    from app.config import settings
    from app.services import backups
    setup_profile(fresh, experienced_text())
    r = backups.run_backup("manual")
    assert r["status"] == "succeeded"
    assert db.query_one("SELECT verified FROM backups WHERE id = ?", (r["id"],))["verified"] == 1
    cv = settings.data_dir / "users" / "1" / "cv.md"
    assert cv.exists()
    db.close_all_connections()
    settings.db_path.unlink()
    for s in ("-wal", "-shm"):
        (settings.data_dir / f"jobpilot.db{s}").unlink(missing_ok=True)
    cv.unlink()
    res = backups.startup_recover()
    assert res["restored"] and res["local_state"] == "missing", res
    assert cv.exists()
    assert sqlite3.connect(settings.db_path).execute("SELECT COUNT(*) FROM users").fetchone()[0] == 1


def test_restore_corrupted_db(fresh):
    from app import db
    from app.config import settings
    from app.services import backups
    setup_profile(fresh, experienced_text())
    assert backups.run_backup("manual")["status"] == "succeeded"
    db.close_all_connections()
    for s in ("-wal", "-shm"):
        (settings.data_dir / f"jobpilot.db{s}").unlink(missing_ok=True)
    settings.db_path.write_bytes(b"this is not a sqlite database" * 100)
    res = backups.startup_recover()
    assert res["restored"] and res["local_state"] == "corrupt", res
    assert list(settings.data_dir.glob("jobpilot.corrupt-*.db"))


def test_no_restore_over_valid_or_newer_db(fresh):
    from app import db
    from app.services import backups
    setup_profile(fresh, experienced_text())
    assert backups.run_backup("manual")["status"] == "succeeded"
    db.close_thread_conn()
    res = backups.startup_recover()
    assert res["restored"] is False and res["local_state"] == "ok"


def test_backup_retention_and_change_debounce(fresh):
    from app.services import backups, entitlements
    entitlements.save_system({"backup": {"retention": 2}})
    for _ in range(3):
        assert backups.run_backup("scheduled")["status"] == "succeeded"
    assert len(backups.list_remote()) == 2
    before = backups._last_change
    fresh.put("/api/preferences", json={"locations": ["Pune"]})
    assert backups._last_change > before


def test_backup_encryption_required_for_cloud(monkeypatch):
    from app.services import backups
    monkeypatch.setenv("BACKUP_ENCRYPTION_KEY", "unit-test-key")
    blob = backups.encrypt(b"secret data")
    assert blob[:4] == b"JPB1" and b"secret data" not in blob and backups.decrypt(blob) == b"secret data"
    for k in ("BACKUP_S3_ENDPOINT", "BACKUP_S3_BUCKET", "BACKUP_S3_ACCESS_KEY_ID", "BACKUP_S3_SECRET_ACCESS_KEY"):
        monkeypatch.setenv(k, "https://x" if k.endswith("ENDPOINT") else "x")
    monkeypatch.delenv("BACKUP_ENCRYPTION_KEY")
    with pytest.raises(RuntimeError):
        backups.encrypt(b"data")


def test_sigv4_matches_aws_vector():
    from app.services.backups import sigv4_headers
    h = sigv4_headers("GET", "https://examplebucket.s3.amazonaws.com/test.txt", "us-east-1", "AKIAIOSFODNN7EXAMPLE",
                      "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY", "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855",
                      {"Range": "bytes=0-9"}, amz_date="20130524T000000Z")
    assert h["Authorization"].endswith("Signature=f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41")


# ------------------------------------------------------------------ health & maintenance
def test_health_minimal_and_public(client_factory):
    c = client_factory()
    r = c.get("/health")
    assert r.status_code == 200 and r.json() == {"status": "ok"}


def test_maintenance_mode_blocks_users_not_staff(client_factory):
    o, u = client_factory(), client_factory()
    owner_with_2fa(o)
    register(u, "m@example.com")
    o.put("/api/admin/settings", json={"maintenance_mode": True})
    assert u.get("/api/dashboard").status_code == 503
    assert o.get("/api/dashboard").status_code == 200
    assert u.get("/api/public/config").json()["maintenance"] is True


def test_account_deletion_removes_data(client_factory):
    from app import db
    u = client_factory()
    user = register(u, "del@example.com")
    setup_profile(u, FRESHER_RESUME)
    assert u.post("/api/account/delete", json={"confirm": "DELETE MY ACCOUNT", "password": "wrong-password1"}).status_code == 401
    assert u.post("/api/account/delete", json={"confirm": "DELETE MY ACCOUNT", "password": "test-pass-12345"}).status_code == 200
    assert db.query_one("SELECT id FROM users WHERE id = ?", (user["id"],)) is None
    assert db.query_one("SELECT COUNT(*) n FROM profile_versions WHERE user_id = ?", (user["id"],))["n"] == 0
