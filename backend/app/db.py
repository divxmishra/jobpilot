"""SQLite access layer and migration runner."""
from __future__ import annotations

import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterator

from .config import settings

_local = threading.local()
_write_lock = threading.RLock()
MIGRATIONS_DIR = Path(__file__).parent / "migrations"


def now_iso() -> str:
    return datetime.now(timezone.utc).replace(microsecond=0).isoformat()


def _connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(path), timeout=30, check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    conn.execute("PRAGMA busy_timeout = 30000")
    return conn


def get_conn() -> sqlite3.Connection:
    conn = getattr(_local, "conn", None)
    path = getattr(_local, "path", None)
    if conn is None or path != settings.db_path:
        if conn is not None:
            conn.close()
        settings.db_path.parent.mkdir(parents=True, exist_ok=True)
        conn = _connect(settings.db_path)
        _local.conn = conn
        _local.path = settings.db_path
    return conn


def close_thread_conn() -> None:
    conn = getattr(_local, "conn", None)
    if conn is not None:
        conn.close()
        _local.conn = None


@contextmanager
def tx() -> Iterator[sqlite3.Connection]:
    """Serialized write transaction (atomic)."""
    conn = get_conn()
    with _write_lock:
        try:
            conn.execute("BEGIN IMMEDIATE")
            yield conn
            conn.execute("COMMIT")
        except Exception:
            conn.execute("ROLLBACK")
            raise


def query(sql: str, params: tuple | list = ()) -> list[dict]:
    return [dict(r) for r in get_conn().execute(sql, params).fetchall()]


def query_one(sql: str, params: tuple | list = ()) -> dict | None:
    r = get_conn().execute(sql, params).fetchone()
    return dict(r) if r else None


def execute(sql: str, params: tuple | list = ()) -> int:
    with tx() as conn:
        cur = conn.execute(sql, params)
        return cur.lastrowid


def migrate() -> list[str]:
    conn = get_conn()
    conn.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version TEXT PRIMARY KEY, applied_at TEXT NOT NULL)")
    applied = {r[0] for r in conn.execute("SELECT version FROM schema_migrations")}
    done = []
    for f in sorted(MIGRATIONS_DIR.glob("*.sql")):
        if f.stem in applied:
            continue
        with _write_lock:
            conn.executescript(f.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO schema_migrations(version, applied_at) VALUES (?, ?)", (f.stem, now_iso()))
            conn.commit()
        done.append(f.stem)
    return done


def jloads(v: Any, default: Any = None) -> Any:
    if v is None or v == "":
        return default
    if isinstance(v, (dict, list)):
        return v
    try:
        return json.loads(v)
    except (TypeError, ValueError):
        return default


def jdumps(v: Any) -> str:
    return json.dumps(v, ensure_ascii=False, default=str)


# ---------------------------------------------------------------- settings KV
def get_setting(key: str, default: Any = None) -> Any:
    r = query_one("SELECT value FROM settings WHERE key = ?", (key,))
    return jloads(r["value"], default) if r else default


def set_setting(key: str, value: Any) -> None:
    execute(
        "INSERT INTO settings(key, value, updated_at) VALUES (?, ?, ?) "
        "ON CONFLICT(key) DO UPDATE SET value = excluded.value, updated_at = excluded.updated_at",
        (key, jdumps(value), now_iso()),
    )
