"""Automated, encrypted backups of the SQLite database and user files, with safe
startup recovery.

* Snapshot via SQLite's online backup API (never a raw copy of a live file),
  checked with PRAGMA integrity_check before upload.
* Archive (tar.gz) of the snapshot + data/users/** + audit logs, encrypted with
  AES-256-GCM (key from BACKUP_ENCRYPTION_KEY) before it leaves the server; object
  storage additionally encrypts at rest and all transfers use HTTPS.
* Triggers: debounced after important changes, scheduled every N hours, manual.
* Retention: newest N verified backups are kept.
* Startup: restore only when the local database is missing, corrupt or empty,
  only from a backup that passes hash + decryption + integrity checks, and never
  over a newer valid database.

Recovery point objective: at most `debounce_minutes` after the last change, plus
upload time, provided the process stays up long enough to run the debounced job.
A crash or restart before the debounce elapses can lose those last changes; the
scheduled backup bounds the worst case to `interval_hours`.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import io
import json
import os
import shutil
import sqlite3
import tarfile
import threading
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote, urlparse
from xml.etree import ElementTree

import httpx

from .. import db
from ..config import settings

MAGIC = b"JPB1"
_last_change = 0.0
_last_change_persist = 0.0
_lock = threading.Lock()
_stop = threading.Event()


# ------------------------------------------------------------------ config
def _cfg() -> dict:
    try:
        from .entitlements import system
        return system()["backup"]
    except Exception:
        return {"interval_hours": 6, "debounce_minutes": 5, "retention": 14}


def s3_configured() -> bool:
    return all(os.getenv(k) for k in ("BACKUP_S3_ENDPOINT", "BACKUP_S3_BUCKET", "BACKUP_S3_ACCESS_KEY_ID", "BACKUP_S3_SECRET_ACCESS_KEY"))


def encryption_key() -> bytes | None:
    raw = os.getenv("BACKUP_ENCRYPTION_KEY", "")
    if not raw:
        return None
    return hashlib.sha256(("jobpilot-backup|" + raw).encode()).digest()


def local_dir() -> Path:
    return Path(os.getenv("BACKUP_LOCAL_DIR") or (settings.data_dir.parent / f"{settings.data_dir.name}-backups"))


# ------------------------------------------------------------------ storage backends
class LocalBackend:
    name = "local"

    def __init__(self, root: Path):
        self.root = root
        root.mkdir(parents=True, exist_ok=True)

    def put(self, key: str, data: bytes) -> None:
        p = self.root / key
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(p.suffix + ".tmp")
        tmp.write_bytes(data)
        os.replace(tmp, p)

    def get(self, key: str) -> bytes:
        return (self.root / key).read_bytes()

    def list(self, prefix: str) -> list[str]:
        base = self.root / prefix
        if not base.exists():
            return []
        return sorted(str(p.relative_to(self.root)).replace("\\", "/") for p in base.rglob("*") if p.is_file() and not p.name.endswith(".tmp"))

    def delete(self, key: str) -> None:
        (self.root / key).unlink(missing_ok=True)


def sigv4_headers(method: str, url: str, region: str, access_key: str, secret_key: str, payload_hash: str,
                  extra_headers: dict | None = None, amz_date: str | None = None, service: str = "s3") -> dict:
    """AWS Signature Version 4 (works for Amazon S3 and Cloudflare R2)."""
    p = urlparse(url)
    amz_date = amz_date or datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
    datestamp = amz_date[:8]
    headers = {"host": p.netloc, "x-amz-content-sha256": payload_hash, "x-amz-date": amz_date}
    for k, v in (extra_headers or {}).items():
        headers[k.lower()] = v
    canonical_query = "&".join(sorted(
        f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, _, v in (q.partition("=") for q in p.query.split("&") if q)))
    signed = ";".join(sorted(headers))
    canonical_headers = "".join(f"{k}:{str(headers[k]).strip()}\n" for k in sorted(headers))
    canonical = "\n".join([method, quote(p.path or "/", safe="/-_.~"), canonical_query, canonical_headers, signed, payload_hash])
    scope = f"{datestamp}/{region}/{service}/aws4_request"
    to_sign = "\n".join(["AWS4-HMAC-SHA256", amz_date, scope, hashlib.sha256(canonical.encode()).hexdigest()])

    def _hm(key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()
    k = _hm(_hm(_hm(_hm(("AWS4" + secret_key).encode(), datestamp), region), service), "aws4_request")
    sig = hmac.new(k, to_sign.encode(), hashlib.sha256).hexdigest()
    out = {k2: v for k2, v in headers.items() if k2 != "host"}
    out["Authorization"] = f"AWS4-HMAC-SHA256 Credential={access_key}/{scope}, SignedHeaders={signed}, Signature={sig}"
    return out


class S3Backend:
    name = "s3"

    def __init__(self):
        self.endpoint = os.getenv("BACKUP_S3_ENDPOINT", "").rstrip("/")
        self.bucket = os.getenv("BACKUP_S3_BUCKET", "")
        self.key_id = os.getenv("BACKUP_S3_ACCESS_KEY_ID", "")
        self.secret = os.getenv("BACKUP_S3_SECRET_ACCESS_KEY", "")
        self.region = os.getenv("BACKUP_S3_REGION", "auto")
        if not self.endpoint.startswith("https://"):
            raise ValueError("BACKUP_S3_ENDPOINT must be an https:// URL")

    def _url(self, key: str = "", query: str = "") -> str:
        return f"{self.endpoint}/{self.bucket}" + (f"/{quote(key, safe='/-_.~')}" if key else "") + (f"?{query}" if query else "")

    def _req(self, method: str, url: str, body: bytes = b"", extra: dict | None = None) -> httpx.Response:
        h = sigv4_headers(method, url, self.region, self.key_id, self.secret, hashlib.sha256(body).hexdigest(), extra)
        with httpx.Client(timeout=120) as c:
            r = c.request(method, url, content=body if body else None, headers=h)
        if r.status_code >= 300:
            raise RuntimeError(f"Object storage {method} failed: HTTP {r.status_code}")
        return r

    def put(self, key: str, data: bytes) -> None:
        extra = {} if "r2.cloudflarestorage.com" in self.endpoint else {"x-amz-server-side-encryption": "AES256"}
        self._req("PUT", self._url(key), data, extra)

    def get(self, key: str) -> bytes:
        return self._req("GET", self._url(key)).content

    def list(self, prefix: str) -> list[str]:
        r = self._req("GET", self._url(query=f"list-type=2&prefix={quote(prefix, safe='')}"))
        root = ElementTree.fromstring(r.content)
        ns = {"s": root.tag.split("}")[0].strip("{")} if root.tag.startswith("{") else {}
        keys = [e.text for e in root.iter(("{%s}Key" % ns["s"]) if ns else "Key")]
        return sorted(k for k in keys if k)

    def delete(self, key: str) -> None:
        self._req("DELETE", self._url(key))


def backend():
    return S3Backend() if s3_configured() else LocalBackend(local_dir())


# ------------------------------------------------------------------ crypto
def encrypt(data: bytes) -> bytes:
    key = encryption_key()
    if not key:
        if s3_configured():
            raise RuntimeError("BACKUP_ENCRYPTION_KEY must be set before uploading backups to cloud storage")
        return b"JPB0" + data  # local-only, unencrypted (development)
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    nonce = os.urandom(12)
    return MAGIC + nonce + AESGCM(key).encrypt(nonce, data, MAGIC)


def decrypt(blob: bytes) -> bytes:
    if blob[:4] == b"JPB0":
        return blob[4:]
    if blob[:4] != MAGIC:
        raise ValueError("Not a JobPilot backup")
    key = encryption_key()
    if not key:
        raise ValueError("BACKUP_ENCRYPTION_KEY is required to decrypt this backup")
    from cryptography.hazmat.primitives.ciphers.aead import AESGCM
    return AESGCM(key).decrypt(blob[4:16], blob[16:], MAGIC)


# ------------------------------------------------------------------ change tracking (debounce)
def mark_changed() -> None:
    global _last_change, _last_change_persist
    _last_change = time.time()
    if _last_change - _last_change_persist > 30:
        _last_change_persist = _last_change
        try:
            db.set_setting("db_last_change_at", db.now_iso())
        except Exception:
            pass


# ------------------------------------------------------------------ snapshot & archive
EXCLUDE_TOP = {"browser-profile", "tmp", "jobpilot.db", "jobpilot.db-wal", "jobpilot.db-shm"}


def _snapshot_db(dest: Path) -> str:
    src = db.get_conn()
    out = sqlite3.connect(str(dest))
    with db._write_lock:
        src.backup(out)
    res = out.execute("PRAGMA integrity_check").fetchone()[0]
    out.close()
    if res != "ok":
        raise RuntimeError(f"Snapshot failed integrity check: {res}")
    return res


def build_archive() -> tuple[bytes, dict]:
    root = settings.data_dir
    tmpdir = root / "tmp"
    tmpdir.mkdir(exist_ok=True)
    snap = tmpdir / f"backup-{int(time.time() * 1000)}.db"
    try:
        _snapshot_db(snap)
        buf = io.BytesIO()
        with tarfile.open(fileobj=buf, mode="w:gz") as tar:
            tar.add(snap, arcname="jobpilot.db")
            for p in sorted(root.iterdir()):
                if p.name in EXCLUDE_TOP or p.name.startswith("."):
                    continue
                tar.add(p, arcname=p.name)
        data = buf.getvalue()
    finally:
        snap.unlink(missing_ok=True)
    users = db.query_one("SELECT COUNT(*) n FROM users")["n"] if _has_table("users") else 0
    manifest = {"version": 1, "created_at": db.now_iso(), "db_last_change_at": db.get_setting("db_last_change_at", None),
                "archive_sha256": hashlib.sha256(data).hexdigest(), "archive_size": len(data), "users": users,
                "commit": os.getenv("RENDER_GIT_COMMIT", "")[:12]}
    return data, manifest


def _has_table(name: str) -> bool:
    return bool(db.query_one("SELECT name FROM sqlite_master WHERE type='table' AND name = ?", (name,)))


# ------------------------------------------------------------------ run backup
def run_backup(trigger: str) -> dict:
    if not _lock.acquire(blocking=False):
        return {"status": "skipped", "reason": "A backup is already running"}
    bid = db.execute("INSERT INTO backups(started_at, trigger, status) VALUES (?,?,?)", (db.now_iso(), trigger, "running"))
    try:
        be = backend()
        data, manifest = build_archive()
        blob = encrypt(data)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ") + f"-{bid}"
        key = f"backups/{stamp}.jpb"
        manifest.update(object_key=key, blob_sha256=hashlib.sha256(blob).hexdigest(), blob_size=len(blob),
                        encrypted=blob[:4] == MAGIC)
        be.put(key, blob)
        # verify: read back and compare hash, then confirm it decrypts to the same archive
        back = be.get(key)
        ok = hashlib.sha256(back).hexdigest() == manifest["blob_sha256"] and \
            hashlib.sha256(decrypt(back)).hexdigest() == manifest["archive_sha256"]
        if not ok:
            raise RuntimeError("Verification failed: uploaded backup does not match")
        be.put(f"backups/{stamp}.json", json.dumps(manifest).encode())
        db.execute("UPDATE backups SET finished_at = ?, status = 'succeeded', backend = ?, object_key = ?, size_bytes = ?, sha256 = ?, "
                   "verified = 1 WHERE id = ?", (db.now_iso(), be.name, key, len(blob), manifest["blob_sha256"], bid))
        prune(be)
        return {"status": "succeeded", "id": bid, "key": key, "size": len(blob), "backend": be.name}
    except Exception as e:
        db.execute("UPDATE backups SET finished_at = ?, status = 'failed', error = ? WHERE id = ?", (db.now_iso(), str(e)[:400], bid))
        try:
            from .. import audit
            audit.log("failure", "backup_failed", f"Backup ({trigger}) failed: {e}", level="error")
        except Exception:
            pass
        return {"status": "failed", "id": bid, "error": str(e)[:400]}
    finally:
        _lock.release()


def list_remote(be=None) -> list[dict]:
    be = be or backend()
    out = []
    for k in be.list("backups/"):
        if k.endswith(".json"):
            try:
                out.append(json.loads(be.get(k)))
            except Exception:
                continue
    return sorted(out, key=lambda m: m["created_at"], reverse=True)


def prune(be=None) -> int:
    be = be or backend()
    keep = int(_cfg().get("retention", 14))
    manifests = list_remote(be)
    removed = 0
    for m in manifests[keep:]:
        be.delete(m["object_key"])
        be.delete(m["object_key"].replace(".jpb", ".json"))
        removed += 1
    return removed


# ------------------------------------------------------------------ restore
def _force_rmtree(path: Path) -> None:
    """rmtree that also removes read-only files (immutable uploads are stored read-only)."""
    import stat

    def onexc(func, p, _exc):
        os.chmod(p, stat.S_IWRITE | stat.S_IREAD)
        func(p)
    if path.exists():
        shutil.rmtree(path, onexc=onexc)


def _db_state(path: Path) -> str:
    """missing | corrupt | empty | ok"""
    if not path.exists() or path.stat().st_size == 0:
        return "missing"
    c = None
    try:
        c = sqlite3.connect(str(path))
        if c.execute("PRAGMA quick_check").fetchone()[0] != "ok":
            return "corrupt"
        tables = {t[0] for t in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}
        has_data = any(t in tables and c.execute(f"SELECT 1 FROM {t} LIMIT 1").fetchone() for t in ("users", "profile_versions", "jobs"))
        return "ok" if has_data else "empty"
    except sqlite3.DatabaseError:
        return "corrupt"
    finally:
        if c is not None:
            c.close()


def _local_change_marker(path: Path) -> str | None:
    try:
        c = sqlite3.connect(str(path))
        r = c.execute("SELECT value FROM settings WHERE key = 'db_last_change_at'").fetchone()
        c.close()
        return json.loads(r[0]) if r else None
    except Exception:
        return None


def startup_recover() -> dict:
    """Run BEFORE migrations, without the app's DB connection open."""
    path = settings.db_path
    state = _db_state(path)
    result = {"local_state": state, "restored": False}
    if state == "ok":
        result["reason"] = "Local database is valid; no recovery needed"
        return result
    try:
        be = backend()
        manifests = list_remote(be)
    except Exception as e:
        result["reason"] = f"Could not list backups: {type(e).__name__}"
        return result
    if not manifests:
        result["reason"] = "No backups available"
        return result
    local_marker = _local_change_marker(path) if state == "empty" else None
    for m in manifests:
        if local_marker and m.get("db_last_change_at") and m["db_last_change_at"] < local_marker:
            continue  # never restore an older backup over newer local state
        try:
            blob = be.get(m["object_key"])
            if hashlib.sha256(blob).hexdigest() != m["blob_sha256"]:
                continue
            archive = decrypt(blob)
            if hashlib.sha256(archive).hexdigest() != m["archive_sha256"]:
                continue
            staging = settings.data_dir.parent / f".restore-{int(time.time())}"
            _force_rmtree(staging)
            staging.mkdir(parents=True)
            with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as tar:
                tar.extractall(staging, filter="data")
            if _db_state(staging / "jobpilot.db") != "ok":
                _force_rmtree(staging)
                continue
            settings.data_dir.mkdir(parents=True, exist_ok=True)
            if state == "corrupt" and path.exists():
                os.replace(path, path.with_name(f"jobpilot.corrupt-{int(time.time())}.db"))
            for suffix in ("-wal", "-shm"):
                Path(str(path) + suffix).unlink(missing_ok=True)
            for item in staging.iterdir():
                target = settings.data_dir / item.name
                if item.is_dir():
                    _force_rmtree(target)
                    shutil.move(str(item), str(target))
                else:
                    os.replace(item, target)
            _force_rmtree(staging)
            result.update(restored=True, backup=m["object_key"], backup_created_at=m["created_at"],
                          reason=f"Local database was {state}; restored newest verified backup")
            return result
        except Exception as e:
            result.setdefault("errors", []).append(f"{m.get('object_key')}: {type(e).__name__}: {str(e)[:120]}")
            continue
    result["reason"] = result.get("reason") or "No backup passed verification; started with an empty database"
    return result


# ------------------------------------------------------------------ scheduler
def _loop():
    while not _stop.wait(30):
        try:
            cfg = _cfg()
            now = time.time()
            due_change = _last_change and now - _last_change >= int(cfg.get("debounce_minutes", 5)) * 60
            last = db.query_one("SELECT started_at FROM backups WHERE status = 'succeeded' ORDER BY id DESC LIMIT 1")
            last_ts = datetime.fromisoformat(last["started_at"]).timestamp() if last else 0
            due_sched = now - last_ts >= float(cfg.get("interval_hours", 6)) * 3600
            if due_change and _last_change > last_ts:
                run_backup("change")
            elif due_sched:
                run_backup("scheduled")
        except Exception:
            pass
        finally:
            db.close_thread_conn()


def start() -> None:
    threading.Thread(target=_loop, name="jobpilot-backups", daemon=True).start()


def stop() -> None:
    _stop.set()


def status() -> dict:
    cfg = _cfg()
    rows = db.query("SELECT * FROM backups ORDER BY id DESC LIMIT 30")
    last_ok = next((r for r in rows if r["status"] == "succeeded"), None)
    return {"backend": "s3" if s3_configured() else "local", "s3_configured": s3_configured(),
            "encryption_configured": bool(encryption_key()), "config": cfg, "recent": rows, "last_success": last_ok,
            "rpo": f"Up to {cfg['debounce_minutes']} minute(s) after the last change if the server stays up; "
                   f"at worst {cfg['interval_hours']} hour(s) (scheduled backup).",
            "persistent_storage": settings.persistent_storage,
            "startup_recovery": db.get_setting("last_startup_recovery", None)}
