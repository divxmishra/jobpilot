# Architecture & module guide

```
Browser (vanilla-JS SPA, no build step)
   │  JSON over HTTPS, same origin, signed session cookie, X-Requested-With CSRF header
   ▼
FastAPI app (backend/app/main.py) ── background task runner (threads, progress + cancel in SQLite)
   │                                 └─ daily discovery scheduler
   ├─ SQLite (authoritative live state, WAL, versioned migrations)
   └─ Human-readable companions (generated from validated records, atomic writes)
        cv.md · profile.yml · portals.yml · jobs/<id>/report.md · jobs/<id>/job.json
        resumes/<job>/resume_vN.pdf|docx · reports/<job>/ats_vN.md, skill_gap_vN.md, keyword_map_vN.json
        audit/YYYY-MM-DD.jsonl · uploads/ (immutable originals) · cv_history/
```

**Why this stack:** Python + FastAPI + SQLite + a dependency-free ES-module frontend runs on a
Windows laptop with 8 GB RAM without Node.js or a build pipeline, ships as one small container,
and keeps one source of truth. Pydantic models give typed request contracts; the frontend uses
JSDoc-typed API helpers.

## Backend modules (`backend/app`)

| Module | Responsibility |
|---|---|
| `config.py` | Settings from env / local `.env`; no secret ever reaches the browser |
| `db.py`, `migrations/` | SQLite connection per thread, serialized write transactions, migration runner |
| `security.py` | Single-user password login, signed HttpOnly cookies, login rate limiting |
| `audit.py` | Audit events to SQLite + daily JSONL; secret scrubbing; notifications |
| `files.py` | Atomic writes, path-traversal guard, YAML/JSON helpers |
| `tasks.py` | Background tasks with progress, cooperative cancellation, duplicate-run guard |
| `services/resume_parser.py` | PDF/DOCX text extraction and structured, unverified fact extraction |
| `services/profile_service.py` | Immutable uploads, profile versions, verified-fact view, career stage, cv.md, preferences (profile.yml), company registry (portals.yml) |
| `services/net.py` | Polite fetching: robots.txt, per-host rate limits, bot-protection detection, private-address block |
| `services/job_extract.py` | Greenhouse/Lever/Ashby APIs, schema.org JobPosting, page text; expiry/closed/404 detection; salary/work-mode parsing (never estimated) |
| `services/requirements.py` | Deterministic JD interpretation with evidence lines, red flags, prompt-injection markers |
| `services/evaluation.py` | Hard gates, `match-v1` rubric, decision |
| `services/pipeline.py` | Ingest → dedupe → evaluate → report.md/job.json; re-evaluation |
| `services/discovery.py` | Source connectors, relevance pre-filter, closed-posting detection, re-checks, scheduler |
| `services/resume_engine.py` | Truthful tailoring, keyword map, consistency check, `ats-v1`, versioning, approval, draft answers |
| `services/documents.py` | HTML/CSS template → PDF (xhtml2pdf), DOCX (python-docx), page PNG previews (pdfium) |
| `services/outreach.py` | Contacts with verification labels; referral/recruiter/connection/follow-up drafts (never sent) |
| `services/browser_prep.py` | Local-only Playwright prefill in a persistent profile; never clicks/submits |
| `services/ai.py` | Optional AI (Gemini/Groq/OpenAI-compatible) with consent, caps, cache, verbatim-quote validation |
| `routers/*` | REST API: profile, jobs, documents (resumes/applications/outreach), system (auth, dashboard, approvals, analytics, activity, settings, data) |

## Frontend (`frontend/`)

`js/main.js` (shell, router, header widgets) · `js/api.js` (fetch, upload with progress,
task polling) · `js/ui/dom.js` (safe element builder — no `innerHTML`, so untrusted job text
can't inject markup) · `js/ui/components.js` (badges, job card, score breakdown, gates, filter
bar, pagination, uploader, dialogs, toasts, tables) · `js/ui/charts.js` (accessible SVG bar
charts with table view and "not enough data" state) · `js/pages/*` (one module per page) ·
`css/app.css` (design tokens, light/dark themes, responsive layout).

## Key guarantees and where they are enforced

| Guarantee | Enforced in | Tested in |
|---|---|---|
| Only verified facts used | `profile_service.verified_view` | `test_unverified_facts_are_not_used` |
| Gates beat scores; < threshold never recommended | `evaluation.decide`, jobs router | `test_hard_gate_cannot_be_overridden…`, `test_below_threshold_never_recommended` |
| No tailoring for non-qualifying jobs | `resume_engine.generate`, documents router | `test_below_threshold_never_recommended` |
| Every claim traceable; edits can't add claims | `resume_engine.consistency_check`, `approve` | `test_tailored_resume_claims_trace…`, `test_editing_cannot_introduce…` |
| Honest ATS score | `resume_engine.ats_score` | `test_ats_score_honest_below_target` |
| Never submits / sends | `browser_prep.prefill` (fill/attach only), outreach statuses | `test_browser_safety.py`, `test_outreach_never_sent…` |
| Master CV immutable | read-only uploads, verified versions locked | `test_profile_requires_explicit_confirmation…`, `test_master_profile_unchanged…` |

## Multi-user SaaS layer (v2)

| Module | Responsibility |
|---|---|
| `context.py` | Per-request user context, set only by the auth middleware from the server-side session and carried into background tasks |
| `services/auth.py` | scrypt password hashing, sessions (hashed tokens, idle and absolute expiry), single-use tokens, TOTP for staff, brute-force limits, Google OAuth, one-time owner bootstrap |
| `services/entitlements.py` | System config (maintenance, flags, trial, rate limits, analytics level, backups), plans, trials, subscriptions, overrides, usage counters, coupons. `require_feature()` / `consume()` return HTTP 402 with an upgrade path. |
| `services/tracking.py` | Allow-listed product analytics (no sensitive content) and the admin audit log |
| `services/roadmap.py` | Career Roadmap aggregation and job-unlock estimates (from re-running the real evaluation engine) |
| `services/backups.py` | Encrypted backups to S3/R2 (SigV4, verified against AWS's test vector), debounced and scheduled, with retention and safe startup recovery |
| `services/emailer.py` | SMTP sender. Disabled until configured, and never pretends to send. |
| `routers/accounts.py` | Public endpoints, authentication, account, subscription/usage, support, roadmap |
| `routers/admin.py` | Admin API. Every route depends on staff role + TOTP-elevated session. Support role is read-only. |
| `migrations/002_multiuser_saas.sql` | Users, sessions, plans, subscriptions, trials, usage, payments, invoices, coupons, activity, admin audit, announcements, support, backups, roadmap. Adds `user_id` to all user-owned tables. Legacy rows are claimed by the owner at bootstrap. |

**Isolation rule:** every query on user-owned data includes `user_id = uid()`, and files live under
`data/users/<id>/`. A missing row for another user returns 404, so other users' data is never revealed.
`tests/test_saas.py::test_user_data_isolation` covers this.

**Frontend:** `index.html` + `js/main.js` serve the public site and the user app (`/app`).
`admin.html` + `js/admin/*` serve the admin app, which has its own layout and security gate.
