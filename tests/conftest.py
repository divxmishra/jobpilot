import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "backend"))
os.environ["JOBPILOT_ENV"] = "test"
os.environ["JOBPILOT_SCHEDULER"] = "0"
os.environ["JOBPILOT_PASSWORD"] = ""
os.environ["AI_PROVIDER"] = "none"
os.environ["JOBPILOT_DATA_DIR"] = tempfile.mkdtemp(prefix="jobpilot-test-")
os.environ["BACKUP_LOCAL_DIR"] = tempfile.mkdtemp(prefix="jobpilot-test-backups-")

import pytest  # noqa: E402

H = {"X-Requested-With": "JobPilot"}


@pytest.fixture()
def app_env(tmp_path, monkeypatch):
    """Empty data dir + fresh database for one test."""
    from app import db
    from app.config import settings, ensure_dirs
    monkeypatch.setattr(settings, "data_dir", tmp_path / "data")
    monkeypatch.setenv("BACKUP_LOCAL_DIR", str(tmp_path / "backups"))
    db.close_thread_conn()
    ensure_dirs()
    db.migrate()
    from app.services import entitlements
    entitlements.seed_plans()
    yield tmp_path
    db.close_thread_conn()


def new_client():
    from fastapi.testclient import TestClient
    from app.main import app
    return TestClient(app, headers=H)


def register(c, email, password="test-pass-12345", name=None):
    r = c.post("/api/auth/register", json={"email": email, "password": password, "name": name or email.split("@")[0], "accept_terms": True})
    assert r.status_code == 200, r.text
    return r.json()["user"]


@pytest.fixture()
def fresh(app_env):
    """Client signed in as the owner (staff: unrestricted plan) — used by functional tests."""
    with new_client() as c:
        r = c.post("/api/auth/bootstrap-owner", json={"setup_secret": "dev", "email": "owner@example.com", "password": "owner-pass-12345", "name": "Owner"})
        assert r.status_code == 200, r.text
        yield c


@pytest.fixture()
def client_factory(app_env):
    clients = []

    def make():
        c = new_client()
        c.__enter__()
        clients.append(c)
        return c
    yield make
    for c in clients:
        c.__exit__(None, None, None)
