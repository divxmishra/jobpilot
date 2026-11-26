# JobPilot

A candidate-profile-driven job discovery and application-preparation assistant. It recommends
only jobs you genuinely qualify for, tailors resumes truthfully, and always leaves final
submission to you.

- **Works for any career stage.** Freshers and experienced candidates are evaluated against their own verified resume and preferences. Nothing is fresher-only.
- **Recommends only genuine matches.** Every hard requirement must pass **and** the resume-to-JD match must reach your threshold (default 85%). There is no daily quota, so zero results is a valid outcome.
- **Tailors truthfully.** Tailored resumes only select and order facts you verified. JobPilot aims for an estimated ATS compatibility of 95+ and reports the real score when that isn't achievable honestly.
- **Keeps you in control.** JobPilot never submits an application or sends a message for you. It prepares, prefills (locally) and pauses.

> Read alongside the two specifications provided with the project: *Updated Claude Code
> Implementation Specification (Revised V1)* and *Complete Frontend & UI/UX Specification (V1)*.
> The original handoff package was not available in this workspace, so these two documents
> served as the requirements.

## Live deployment

**https://jobpilot-xg5p.onrender.com** (Render free plan, auto-deploys from `main`; sign in with the access password you set in Render).
The free plan sleeps after ~15 minutes idle (first request takes ~1 minute) and has **no persistent disk** — export your data from Settings regularly, or use the EC2 path below for persistence.

## Features

| Area | What you get |
|---|---|
| First-run setup / Profile | Upload a PDF/DOCX master resume (drag-and-drop, progress, retry). The extracted preview has editable fields and marks uncertain items. You confirm each fact explicitly. The master profile is versioned and read-only, the original file is stored immutably, and `cv.md` is kept in sync. You can also add supporting documents and links. |
| Dashboard | Recommended, pending verification, near match, rejected, expired, documents awaiting review and user-submitted applications. Also shows recent activity, system health, and API usage against budget. |
| Discover Jobs | Paste a job URL (same pipeline as discovery), or enter a job manually when a site blocks automation. Run discovery with progress and cancel. Filter by source, date, location, work mode, employment type, experience and salary, with sorting, active-filter chips and reset. Duplicates are merged with their alternate sources kept. |
| Job detail | Role summary, original JD with matched/missing skills highlighted, posted and verified dates, open/closed status, every hard gate with JD evidence, score breakdown, matched skills with resume evidence, gaps, keywords, and company/source verification. |
| Resume Studio | Side-by-side JD and real PDF preview (rendered pages with page controls), a draft editor with truthfulness checks, PDF/DOCX export, ATS report, keyword map, skill-gap report and version history. |
| Applications | Pipeline with filters, search and sorting. Review prepared data and answers, then prefill locally via Playwright (pauses before submit) or follow the manual workflow. Statuses are updated by you, with history. |
| Contacts & Outreach | Contacts carry source and verification labels (verified / publicly listed / inferred). Draft referral, recruiter-email, connection (≤300 chars) and one follow-up message. Drafts are approved and then sent by you. |
| Approval Center | One queue for shortlist decisions, requirement verification, resume approvals, application reviews and outreach drafts. Each item states what is being approved and what happens next. |
| Analytics | Discovered vs qualifying jobs, source quality, match distribution, outcomes, gate failures, rejection reasons and AI usage. Charts say "Not enough data" instead of inventing values. |
| Activity & Logs | Searchable audit trail of discovery, extraction, scoring, documents, decisions, automation, failures and cost, with source links. Also written to `data/audit/*.jsonl`. |
| Settings | Preferences and criteria (threshold, exclusions, work authorisation), sources (`portals.yml`) and schedule, AI provider status and budget caps, theme, notifications, export, and delete-all. |

## Quick start (Windows)

```powershell
git clone https://github.com/divxmishra/jobpilot.git
cd jobpilot
powershell -ExecutionPolicy Bypass -File scripts\setup.ps1   # venv, dependencies, Chromium, .env
powershell -ExecutionPolicy Bypass -File scripts\run.ps1     # http://127.0.0.1:8000
```

Locally, no password is needed: the app binds only to 127.0.0.1. All data lives in `data/`, which git ignores.

Run the tests: `powershell -ExecutionPolicy Bypass -File scripts\test.ps1`

## Docker

```bash
docker build -t jobpilot .
docker run -p 8000:8000 -v jobpilot-data:/data \
  -e JOBPILOT_PASSWORD=<choose-one> -e JOBPILOT_SECRET_KEY=<random> \
  -e JOBPILOT_COOKIE_SECURE=0 jobpilot     # COOKIE_SECURE=0 only for plain-HTTP local testing
```

## Deployment

### AWS EC2 (primary): Docker Compose with Caddy for automatic HTTPS

1. Launch an Ubuntu 24.04 `t3.micro` (or `t2.micro`) instance using a key pair you keep on your PC. Its Security Group should allow inbound 22 (from your IP only), 80 and 443.
2. SSH in and run:
   ```bash
   curl -fsSL https://raw.githubusercontent.com/divxmishra/jobpilot/main/deploy/ec2-setup.sh -o ec2-setup.sh
   bash ec2-setup.sh https://github.com/divxmishra/jobpilot.git
   sudo grep JOBPILOT_PASSWORD /opt/jobpilot/.env     # your access password
   ```
3. Open `https://<public-ip-with-dashes>.sslip.io`. The certificate comes from Let's Encrypt via `sslip.io`, so you don't need to buy a domain.
4. **Auto-deploy:** add the repository secrets `EC2_HOST` (public IP) and `EC2_SSH_KEY` (the private key contents). Each push to `main` then runs CI, and once CI passes it runs `deploy/update.sh` on the server. Data is kept in the `jobpilot-data` Docker volume.

### Render (fallback)

Connect the repository as a **Blueprint** (`render.yaml`) and set `JOBPILOT_PASSWORD` in the dashboard. Auto-deploy on push is enabled. The free plan has no persistent disk, so data resets on redeploy or restart. Use **Settings → Export** to keep a copy.

### Hosted vs local

Browser prefill (Playwright) opens a real window on your computer, so it works only in the local Windows app. The hosted version offers the manual workflow instead: open the application page and copy the prepared answers.

## Architecture

The architecture and module guide is in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md), and the scoring rubrics are in [docs/SCORING_RUBRIC.md](docs/SCORING_RUBRIC.md).

**Stack choice.** The backend is Python 3.13 + FastAPI + SQLite. The frontend is a dependency-free vanilla-JS ES-module SPA. There is no Node.js or build step, so it runs comfortably on an 8 GB Windows laptop. It ships as one container serving the API and static UI from the same origin, which keeps auth simple and leaves no CORS surface. SQLite is the authoritative state, and the Markdown/JSON/YAML files are generated companions written atomically.

## Security & privacy

- Secrets live only in `.env` or the host's secret settings. `.env.example` lists variable names only. `.env`, `data/`, databases, browser profiles, cookies and resumes are ignored by git.
- Production requires `JOBPILOT_PASSWORD`. Sessions use signed HttpOnly, SameSite=Lax, Secure cookies, mutating requests need a CSRF header, logins are rate-limited, and a strict CSP is set (no inline or eval scripts).
- Job pages are untrusted data. They are parsed, never executed. Instruction-like text is flagged and ignored, and AI output is accepted only if it quotes the JD verbatim.
- JobPilot honours robots.txt for HTML pages, rate-limits requests per host, and never bypasses CAPTCHAs, logins or bot protection.
- AI is off by default. Only JD text is sent, never your resume. A consent toggle, budget caps, call limits, caching and bounded retries are built in.
- **Delete all my data** is in Settings, behind a typed confirmation.

## Troubleshooting

| Problem | Fix |
|---|---|
| "No selectable text found in this PDF" | The PDF is a scanned image. OCR isn't supported in V1, so upload a DOCX or a text-based PDF. |
| A job URL shows "Unable to verify" | The site blocks automation or requires login. Use **Enter job details manually**, then confirm the posting is open. |
| A job is in *Needs Review* | Open it and confirm only the requirements you genuinely meet. It's then re-evaluated. |
| Hosted app says "Server is not configured" | Set `JOBPILOT_PASSWORD` in the server's environment. |
| Login works locally in Docker but the session drops | Over plain HTTP, set `JOBPILOT_COOKIE_SECURE=0`. Keep it on with HTTPS. |
| "Browser preparation runs only in the local Windows app" | Expected on the hosted version. Use the manual workflow, or run JobPilot locally. |
| Discovery finds nothing | Add target companies (Settings → Sources) and target roles (Preferences). Zero qualifying jobs is a valid result. |

## Known limitations

- No OCR for scanned PDFs. Resume extraction is heuristic, which is why you review every fact.
- JD interpretation is keyword/section based. Unusual JD formats may lower confidence, and those jobs go to review rather than being recommended.
- Single user. No email monitoring, which would require explicit account permission and is not implemented.
- Discovery covers Greenhouse, Lever, Ashby, Remotive and career pages that link to those boards. Other ATS platforms work through manual URLs (schema.org JobPosting) or manual entry.
- The scheduler runs only while the server is awake. Render's free plan sleeps when idle.

See [REQUIRED_USER_ACTIONS.md](REQUIRED_USER_ACTIONS.md) for accounts, keys and costs, and [TEST_RESULTS.md](TEST_RESULTS.md) for the latest test results.
