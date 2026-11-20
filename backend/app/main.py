"""JobPilot FastAPI application: JSON API under /api, static SPA for everything else."""
from __future__ import annotations

import logging
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.exceptions import RequestValidationError
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

from . import audit, db, security, tasks
from .config import ensure_dirs, settings
from .routers import documents, jobs, profile, system
from .services import discovery, profile_service, resume_parser

log = logging.getLogger("jobpilot")


@asynccontextmanager
async def lifespan(app: FastAPI):
    ensure_dirs()
    db.migrate()
    tasks.mark_interrupted()
    profile_service.write_profile_yml()
    profile_service.write_portals_yml()
    if settings.is_production and not settings.password:
        log.error("JOBPILOT_PASSWORD is not set: the API will refuse all requests until it is configured.")
    if settings.scheduler_enabled and settings.env != "test":
        discovery.start_scheduler()
    yield
    discovery.stop_scheduler()


app = FastAPI(title="JobPilot", version="1.0.0", lifespan=lifespan, docs_url=None if settings.is_production else "/api/docs",
              redoc_url=None, openapi_url=None if settings.is_production else "/api/openapi.json")

OPEN_PATHS = {"/api/auth/login", "/api/session", "/api/health"}
CSP = ("default-src 'self'; img-src 'self' data: blob:; style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
       "font-src 'self' https://fonts.gstatic.com; script-src 'self'; connect-src 'self'; frame-ancestors 'none'; "
       "base-uri 'self'; form-action 'self'")


@app.middleware("http")
async def guard(request: Request, call_next):
    path = request.url.path
    if path.startswith("/api/"):
        if settings.is_production and not settings.password and path not in ("/api/health", "/api/session"):
            return JSONResponse({"detail": "Server is not configured: set JOBPILOT_PASSWORD in the hosting environment."}, 503)
        if path not in OPEN_PATHS and not security.is_authenticated(request):
            return JSONResponse({"detail": "Sign in required"}, 401)
        if request.method in ("POST", "PUT", "PATCH", "DELETE") and request.headers.get("x-requested-with") != "JobPilot":
            return JSONResponse({"detail": "Missing X-Requested-With header"}, 403)
    response = await call_next(request)
    response.headers["X-Content-Type-Options"] = "nosniff"
    response.headers["Referrer-Policy"] = "same-origin"
    response.headers["X-Frame-Options"] = "DENY"
    response.headers["Content-Security-Policy"] = CSP
    if path.startswith("/api/"):
        response.headers.setdefault("Cache-Control", "no-store")
    return response


def _err(status: int, msg: str):
    return JSONResponse({"detail": msg}, status)


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
    except Exception:
        pass
    return _err(500, "Unexpected server error. It has been recorded in Activity & Logs.")


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
    return FileResponse(FRONTEND / "index.html", headers={"Cache-Control": "no-cache"})
