"""JobPilot multi-user SaaS: public site, user app (/app), private admin (/admin), JSON API (/api)."""
from __future__ import annotations

import logging
import threading
import time
from collections import defaultdict, deque
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import audit, db, tasks
from .config import ensure_dirs, settings
from .context import NoUserContext, reset, set_user
from .routers import accounts, admin, documents, jobs, profile, system
from .services import auth, backups, discovery, emailer, entitlements, resume_parser

log = logging.getLogger("jobpilot")
_maint_stop = threading.Event()


def _maintenance_loop():
    """Trial expiry + reminder notifications (server time only)."""
    while not _maint_stop.wait(900):
        try:
            cfg = entitlements.system()
            now = datetime.now(timezone.utc)
            soon = (now + timedelta(days=int(cfg["trial_reminder_days"]))).isoformat()
            for t in db.query("SELECT t.user_id, t.ends_at, u.email FROM trials t JOIN users u ON u.id = t.user_id WHERE t.status = 'active' "
                              "AND t.ends_at <= ? AND t.reminder_sent_at IS NULL AND u.status = 'active'", (soon,)):
                if t["ends_at"] > now.isoformat():
                    days = max(1, (datetime.fromisoformat(t["ends_at"]) - now).days)
                    audit.notify_user("warning", f"Your free trial ends in {days} day(s). You'll move to the Free plan — no charge, data kept.",
                                      link="/app/subscription", user_id=t["user_id"])
                    emailer.send(t["email"], "Your JobPilot trial is ending soon",
                                 f"Your free trial ends in {days} day(s). Afterwards your account moves to the Free plan automatically. "
                                 "Nothing is charged and your data is kept.")
                db.execute("UPDATE trials SET reminder_sent_at = ? WHERE user_id = ?", (now.isoformat(), t["user_id"]))
            for t in db.query("SELECT user_id FROM trials WHERE status = 'active' AND ends_at <= ?", (now.isoformat(),)):
                entitlements.trial_info(t["user_id"])  # marks expired + notifies
        except Exception:
            log.exception("maintenance loop error")
        finally:
            db.close_thread_conn()


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    recovery = backups.startup_recover() if settings.env != "test" else {"local_state": "skipped (test)", "restored": False}
    db.migrate()
    entitlements.seed_plans()
    tasks.mark_interrupted()
    db.set_setting("last_startup_recovery", recovery | {"at": db.now_iso()})
    if recovery.get("restored") or recovery.get("errors"):
        audit.log("system", "startup_recovery", f"Startup recovery: {recovery.get('reason')}",
                  level="warning" if recovery.get("errors") else "info", details=recovery)
    if settings.is_production and not settings.persistent_storage:
        log.warning("Storage is not persistent; registrations stay closed unless an admin opens them explicitly.")
    if settings.env != "test":
        if settings.scheduler_enabled:
            discovery.start_scheduler()
        backups.start()
        threading.Thread(target=_maintenance_loop, name="jobpilot-maintenance", daemon=True).start()
    yield
    discovery.stop_scheduler()
    backups.stop()
    _maint_stop.set()


app = FastAPI(title="JobPilot", version="2.0.0", lifespan=lifespan, docs_url=None if settings.is_production else "/api/docs",
              redoc_url=None, openapi_url=None if settings.is_production else "/api/openapi.json")

PUBLIC_API = ("/api/health", "/api/session", "/api/public/", "/api/auth/", "/api/demo/")
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'self'; form-action 'self' https://accounts.google.com")
_rate: dict[str, deque] = defaultdict(deque)
_rate_cfg = {"t": 0.0, "limit": 300}


def _rate_limited(key: str) -> bool:
    now = time.time()
    if now - _rate_cfg["t"] > 30:
        try:
            _rate_cfg.update(t=now, limit=int(entitlements.system()["api_rate_limit_per_minute"]))
        except Exception:
            pass
    q = _rate[key]
    while q and now - q[0] > 60:
        q.popleft()
    if len(q) >= _rate_cfg["limit"]:
        return True
    q.append(now)
    return False


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    request.state.session = None
    user = None
    found = auth.session_from_token(request.cookies.get(accounts.COOKIE)) if path.startswith("/api/") else None
    if found:
        request.state.session, user = found
    tokens = set_user(user, request.state.session["id"] if request.state.session else None)
    try:
        if path.startswith("/api/"):
            is_public = any(path == p or path.startswith(p) for p in PUBLIC_API)
            if not is_public and not user:
                return JSONResponse({"detail": "Sign in required"}, 401)
            if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get("x-requested-with") != "JobPilot":
                return JSONResponse({"detail": "Missing X-Requested-With header"}, 403)
            is_staff = bool(user and user["role"] in auth.STAFF_ROLES)
            if not is_public and not is_staff and entitlements.system()["maintenance_mode"]:
                return JSONResponse({"detail": entitlements.system()["maintenance_message"], "maintenance": True}, 503)
            ip = request.headers.get("x-forwarded-for", "").split(",")[0].strip() or (request.client.host if request.client else "?")
            if settings.env != "test" and _rate_limited(f"u{user['id']}" if user else f"ip{auth.ip_hash(ip)}"):
                return JSONResponse({"detail": "Too many requests. Please slow down."}, 429)
        response = await call_next(request)
    finally:
        reset(tokens)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = CSP
    if settings.is_production:
        response.headers["Strict-Transport-Security"] = "max-age=31536000"
    if path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _err(status: int, msg: str, **extra):
    return JSONResponse({"detail": msg, **extra}, status)


@app.exception_handler(entitlements.LimitReached)
async def limit_reached(_, exc):
    from .services import tracking
    tracking.event("limit_reached", feature=exc.info.get("key"), success=False, error_category="limit")
    return _err(402, exc.info["message"], limit=exc.info, upgrade_url="/app/subscription")


@app.exception_handler(entitlements.FeatureLocked)
async def feature_locked(_, exc):
    return _err(402, exc.info["message"], feature=exc.info, upgrade_url="/app/subscription")


@app.exception_handler(NoUserContext)
async def no_user(_, exc):
    return _err(401, "Sign in required")


@app.exception_handler(ValueError)
async def value_error(_, exc: ValueError):
    return _err(422, str(exc))


@app.exception_handler(LookupError)
async def lookup_error(_, exc: LookupError):
    return _err(404, str(exc).strip("'\""))


@app.exception_handler(PermissionError)
async def permission_error(_, exc: PermissionError):
    return _err(409, str(exc))


@app.exception_handler(resume_parser.ParseError)
async def parse_error(_, exc):
    return _err(422, str(exc))


@app.exception_handler(RequestValidationError)
async def validation_error(_, exc: RequestValidationError):
    msgs = []
    for e in exc.errors():
        loc = ".".join(str(x) for x in e.get("loc", []) if x not in ("body", "query"))
        msgs.append(f"{loc}: {e.get('msg')}" if loc else e.get("msg"))
    return _err(422, "; ".join(msgs))


@app.exception_handler(Exception)
async def unhandled(request: Request, exc: Exception):
    log.exception("Unhandled error on %s", request.url.path)
    try:
        audit.log("failure", "server_error", f"Unexpected error on {request.method} {request.url.path}: {type(exc).__name__}", level="error")
        from .services import tracking
        tracking.event("error", feature=request.url.path.split("/")[2] if request.url.path.startswith("/api/") else None,
                       success=False, error_category=type(exc).__name__)
    except Exception:
        pass
    return _err(500, "Unexpected server error. It has been recorded.")


app.include_router(accounts.router)
app.include_router(admin.router)
app.include_router(system.router)
app.include_router(profile.router)
app.include_router(jobs.router)
app.include_router(documents.router)

FRONTEND = settings.frontend_dir
app.mount("/static", StaticFiles(directory=FRONTEND), name="static")


@app.get("/{full_path:path}", include_in_schema=False)
def spa(full_path: str):
    if full_path.startswith("api/"):
        raise HTTPException(404, "Not found")
    page = "admin.html" if full_path == "admin" or full_path.startswith("admin/") else "index.html"
    return FileResponse(FRONTEND / page, headers={"Cache-Control": "no-cache"})
