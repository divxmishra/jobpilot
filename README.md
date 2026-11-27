# JobPilot

JobPilot is a multi-user job-search SaaS app. It recommends only jobs a user genuinely qualifies for. It tailors resumes truthfully. Final submission always stays with the user.

- **Live:** https://jobpilot-xg5p.onrender.com (Render, auto-deploys from `main`)
- **Admin:** `/admin`. Owner only, with two-factor authentication required.
- **Repository:** https://github.com/divxmishra/jobpilot

## Three experiences
| | Where | Who |
|---|---|---|
| Public site | `/` (landing, features, pricing, FAQ, contact), `/demo` (synthetic data only), `/privacy`, `/terms`, `/login`, `/register` | Anyone |
| User app | `/app/...` | Signed-in users. Access depends on plan, trial and limits. |
| Admin panel | `/admin/...` (separate app) | Owner, admin and support roles. Requires a TOTP second factor. Every permission is enforced on the server. |

## Features
**Job search core**
- Verified resume profile.
- Hard eligibility gates plus an 85% match threshold, with evidence for each decision.
- Discovery from Greenhouse, Lever, Ashby, Remotive and career pages, or from a pasted job URL.
- Truthful tailored resumes (PDF/DOCX) with an honest ATS estimate.
- Application tracking with a pause before submit.
- Outreach drafts, never sent automatically.
- Approval Center, analytics and audit log.

**India-specific checks**
- Notice period: immediate, 15, 30, 60 or 90 days, or negotiable.
- Expected and minimum CTC in LPA, with a choice of "hard exclusion" or "warning" on mismatch.
- These requirements are extracted from the job description and labelled explicit, inferred, missing or unverified. Missing information is shown as "not stated" and is never treated as a failure.

**Career Roadmap**
- Ranks the skills that keep coming up as missing across a user's 30–50 most recent jobs.
- For each skill it shows evidence, priority, learning order, a practice project and an effort estimate.
- It also estimates how many jobs a skill could unlock. These counts come from re-running the real eligibility engine with the skill added, and are labelled as estimates.
- Users can track progress and history.

**Accounts**
- Email and password registration (scrypt hashing), plus optional Google OAuth.
- Secure sessions: HttpOnly, SameSite and Secure cookies with idle and absolute expiry. CSRF header and rate limiting.
- Brute-force lockout, single-use reset and verification tokens, data export and account deletion.

**Plans and trials**
- Free, Basic and Pro plans. Prices, features and limits are configured in Admin, never hardcoded.
- A 30-day trial (configurable) starts when profile setup is complete. When it ends, the user moves to the Free plan automatically, with data kept and no charge.
- Server-side enforcement of limits and features, with per-user overrides, coupons, and admin grants (free, lifetime or custom).

**Payments**
- The architecture is in place: payment and invoice records, manual verified payments, refunds and revenue analytics.
- **Online payment processing is disabled.** No gateway is connected and nothing is ever charged.

**Admin**
- Overview with date ranges and comparison against the previous period.
- Users (search, filters, details, timeline, actions), activity, subscriptions, revenue (actual vs projected), plans and pricing, trials, feature limits.
- Outcome analytics (funnel, conversions, source effectiveness), system health, AI usage, job sources.
- Announcements, support tickets, audit log, security, settings (maintenance mode, feature flags, analytics level, rate limits, backups).

**Operations**
- Encrypted automated backups with safe startup recovery. See [docs/BACKUP_AND_RESTORE.md](docs/BACKUP_AND_RESTORE.md).
- Minimal public `/health` endpoint for uptime monitors.

## Quick start (Windows)
```powershell
git clone https://github.com/divxmishra/jobpilot.git
cd jobpilot
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1
powershell -ExecutionPolicy Bypass -File scripts\run.ps1        # http://127.0.0.1:8000
```
Open `http://127.0.0.1:8000/setup-owner` to create the owner account. Locally, without `OWNER_SETUP_TOKEN`, this works from the same machine only. Then register normal users at `/register`.

Run the tests: `scripts\test.ps1`. This runs the backend, safety and SaaS suites, then the Playwright UI journeys.

## Deployment
- **Render (current):** a Blueprint built from [`render.yaml`](render.yaml). The free plan has no persistent disk, so read [REQUIRED_USER_ACTIONS.md](REQUIRED_USER_ACTIONS.md) §B before accepting real users. Registrations stay closed automatically in production until storage is persistent or an admin opens them.
- **Docker / EC2:** use `docker-compose.yml`, which includes Caddy for HTTPS. Instructions are in `deploy/ec2-setup.sh`.

## Stack and architecture
Python 3.13 + FastAPI + SQLite (WAL, one writer, versioned migrations). The frontend is a vanilla-JS ES-module SPA with no build step. Everything ships as one container.

Multi-tenancy uses a `user_id` on every user-owned table. All queries are scoped through a server-side request context set from the session, never from client input. Shared logic stays in services. See [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) and [docs/SCORING_RUBRIC.md](docs/SCORING_RUBRIC.md).

## Security and privacy
- Server-side role-based access (owner, admin, support, user). Admins need TOTP. Support staff have read-only access.
- Strict CSP, no `innerHTML` with untrusted data, parameterised SQL, upload type and size validation, and private per-user file storage.
- Analytics are privacy-conscious: an allow-listed set of events, never resume text, answers, messages or keystrokes. Users can opt out, and admins choose the tracking level.
- Secrets live only in environment variables. Admin shows only whether each one is configured.

## Known limitations
- Render's free plan sleeps when idle and has ephemeral storage. Backups mitigate this but don't replace persistent storage.
- Email and Google sign-in stay inactive until credentials are configured. Until email is set up, admins can generate password-reset links.
- Payments are disabled until you explicitly approve them.
- There is no OCR for scanned resumes.
- Browser prefill runs only in the local Windows app.
- SQLite supports a single backend instance. See OPTIONAL_RECOMMENDATIONS.md #2.

Further reading: [REQUIRED_USER_ACTIONS.md](REQUIRED_USER_ACTIONS.md), [OPTIONAL_RECOMMENDATIONS.md](OPTIONAL_RECOMMENDATIONS.md), [TEST_RESULTS.md](TEST_RESULTS.md).
