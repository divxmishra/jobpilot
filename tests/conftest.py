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

import pytest  # noqa: E402


@pytest.fixture()
def fresh(tmp_path, monkeypatch):
    """Point the app at an empty data dir and return a TestClient."""
    from app import db
    from app.config import settings, ensure_dirs
    monkeypatch.setattr(settings, "data_dir", tmp_path)
    db.close_thread_conn()
    ensure_dirs()
    db.migrate()
    from fastapi.testclient import TestClient
    from app.main import app
    with TestClient(app, headers={"X-Requested-With": "JobPilot"}) as c:
        yield c
    db.close_thread_conn()
