"""Start JobPilot on a free port with an empty temporary data directory, run the
UI journey test against it, then stop the server.  Usage: python tests/run_e2e.py [shots_dir]"""
from __future__ import annotations

import os
import socket
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


def free_port() -> int:
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def main() -> int:
    shots = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "test-results" / "e2e-shots")
    port = free_port()
    data = tempfile.mkdtemp(prefix="jobpilot-e2e-")
    env = dict(os.environ, JOBPILOT_DATA_DIR=data, JOBPILOT_SCHEDULER="0", JOBPILOT_ENV="development", JOBPILOT_PASSWORD="")
    srv = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(port)],
                           cwd=ROOT / "backend", env=env, stdout=subprocess.DEVNULL, stderr=subprocess.STDOUT)
    try:
        base = f"http://127.0.0.1:{port}"
        for _ in range(60):
            try:
                urllib.request.urlopen(base + "/api/health", timeout=1)
                break
            except OSError:
                time.sleep(0.5)
        return subprocess.call([sys.executable, str(ROOT / "tests" / "e2e_ui.py"), base, shots], env=dict(os.environ, PYTHONIOENCODING="utf-8"))
    finally:
        srv.terminate()
        srv.wait(10)


if __name__ == "__main__":
    sys.exit(main())
