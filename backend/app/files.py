"""Human-readable companion files (cv.md, profile.yml, portals.yml, reports, audit logs).

SQLite owns live state. Files here are *generated from validated records* and
written atomically (write temp file, fsync, rename) so a crash never leaves a
half-written file. Edits to profile.yml / portals.yml made through the UI go to
SQLite first, then the file is regenerated. A manual edit to a YAML file can be
imported explicitly via the Settings page ("Import from file").
"""
from __future__ import annotations

import json
import os
import re
import tempfile
from pathlib import Path
from typing import Any

import yaml

from .config import settings

SAFE_ID = re.compile(r"^[A-Za-z0-9_\-]+$")


def atomic_write(path: Path, data: str | bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    mode = "wb" if isinstance(data, bytes) else "w"
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), prefix=".tmp-", suffix=path.suffix)
    try:
        with os.fdopen(fd, mode, **({} if isinstance(data, bytes) else {"encoding": "utf-8", "newline": "\n"})) as f:
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    except Exception:
        try:
            os.unlink(tmp)
        except OSError:
            pass
        raise


def data_path(*parts: str) -> Path:
    p = (settings.data_dir.joinpath(*parts)).resolve()
    if settings.data_dir not in p.parents and p != settings.data_dir:
        raise ValueError("Path escapes data directory")
    return p


def job_dir(job_id: str) -> Path:
    if not SAFE_ID.match(job_id):
        raise ValueError("Invalid job id")
    return data_path("jobs", job_id)


def write_yaml(path: Path, obj: Any, header: str = "") -> None:
    body = yaml.safe_dump(obj, sort_keys=False, allow_unicode=True)
    atomic_write(path, (header + body) if header else body)


def read_yaml(path: Path) -> Any:
    if not path.exists():
        return None
    return yaml.safe_load(path.read_text(encoding="utf-8"))


def write_json(path: Path, obj: Any) -> None:
    atomic_write(path, json.dumps(obj, indent=2, ensure_ascii=False, default=str))


def append_line(path: Path, line: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with open(path, "a", encoding="utf-8", newline="\n") as f:
        f.write(line.rstrip("\n") + "\n")
