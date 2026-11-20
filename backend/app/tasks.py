"""Background task runner with progress reporting and cooperative cancellation.
Task state lives in SQLite so the UI can poll it and so it survives page reloads."""
from __future__ import annotations

import traceback
import uuid
from concurrent.futures import ThreadPoolExecutor
from typing import Callable

from . import audit, db

_executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="jobpilot-task")


class TaskHandle:
    def __init__(self, tid: str):
        self.id = tid

    def progress(self, fraction: float, message: str | None = None) -> None:
        db.execute("UPDATE tasks SET progress = ?, message = COALESCE(?, message), updated_at = ? WHERE id = ?",
                   (max(0.0, min(1.0, fraction)), message, db.now_iso(), self.id))

    def cancelled(self) -> bool:
        r = db.query_one("SELECT cancel_requested FROM tasks WHERE id = ?", (self.id,))
        return bool(r and r["cancel_requested"])


def get(tid: str) -> dict | None:
    t = db.query_one("SELECT * FROM tasks WHERE id = ?", (tid,))
    if t:
        t["result"] = db.jloads(t["result"], None)
    return t


def running(kind: str) -> dict | None:
    return db.query_one("SELECT * FROM tasks WHERE kind = ? AND status IN ('queued','running') ORDER BY created_at DESC LIMIT 1", (kind,))


def cancel(tid: str) -> dict | None:
    db.execute("UPDATE tasks SET cancel_requested = 1, updated_at = ? WHERE id = ? AND status IN ('queued','running')", (db.now_iso(), tid))
    return get(tid)


def submit(kind: str, fn: Callable[[TaskHandle], dict | None], *, label: str, unique: bool = True, sync: bool = False) -> dict:
    if unique:
        existing = running(kind)
        if existing:
            return get(existing["id"])
    tid = "t_" + uuid.uuid4().hex[:12]
    now = db.now_iso()
    db.execute("INSERT INTO tasks(id, kind, status, progress, message, created_at, updated_at) VALUES (?,?,?,?,?,?,?)",
               (tid, kind, "queued", 0, f"{label} queued", now, now))

    def run():
        h = TaskHandle(tid)
        db.execute("UPDATE tasks SET status = 'running', message = ?, updated_at = ? WHERE id = ?", (f"{label} started", db.now_iso(), tid))
        try:
            result = fn(h)
            final = "cancelled" if h.cancelled() else "succeeded"
            db.execute("UPDATE tasks SET status = ?, progress = 1, result = ?, message = ?, updated_at = ? WHERE id = ?",
                       (final, db.jdumps(result), f"{label} {final}", db.now_iso(), tid))
        except Exception as e:
            db.execute("UPDATE tasks SET status = 'failed', error = ?, message = ?, updated_at = ? WHERE id = ?",
                       (str(e)[:500], f"{label} failed", db.now_iso(), tid))
            audit.log("failure", f"{kind}_failed", f"{label} failed: {e}", level="error",
                      details={"trace": traceback.format_exc()[-1500:]})
        finally:
            db.close_thread_conn()

    if sync:
        run()
    else:
        _executor.submit(run)
    return get(tid)


def mark_interrupted() -> None:
    """On startup, tasks left running by a previous process can't continue."""
    db.execute("UPDATE tasks SET status = 'failed', error = 'Interrupted by application restart', updated_at = ? "
               "WHERE status IN ('queued','running')", (db.now_iso(),))
