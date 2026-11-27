# Test results — v2 (multi-user SaaS upgrade)

Run on Windows 11 with Python 3.13 on 2026-11-28. CI (`.github/workflows/ci.yml`) runs the same suites on Ubuntu for every push.
Re-run locally with `scripts\test.ps1`.

## Automated backend, safety & SaaS tests (`pytest`): 65 passed, 0 failed

| Area | Tests |
|---|---|
| Original V1 business rules (unchanged) | 33 tests in `test_core.py`: parsing, verification, fresher vs experienced, hard gates, 85% threshold, zero results, truthfulness, honest ATS, dedupe, expiry, AI budget, red flags, injection, reports, CSRF, no secrets in frontend |
| Never auto-submits | `test_browser_safety.py` (static + behavioural Playwright) |
| Authentication | register/login/logout, password rules, scrypt storage, brute-force lockout, session expiry, admin-issued single-use reset link, honest "email not configured" reset, owner bootstrap only once, no self-promotion to admin |
| Admin access control | user → 403; owner without 2FA → 428; wrong TOTP rejected; support role read-only |
| User isolation | User B can't view or modify User A's job, resume, PDF, application, profile, upload, activity, subscription or admin APIs |
| Trials | Starts at profile completion; exactly 30 days; admin extension; expiry by server time → Free plan with data kept and notification sent; trial length configurable for future users |
| Plans & limits | Daily search limit returns 402 with details; admin override lifts it; automation locked on Free; lifetime grant / revoke; prices set in admin appear publicly; coupon maths; payments disabled; manual verified payment labelled and counted; production closes registration when storage isn't persistent |
| Analytics | Admin counts match database rows; required events recorded; no resume text or secrets in events; analytics opt-out honoured; funnel separates JobPilot / user / user-reported, with 0 verified outcomes and a causation disclaimer |
| Notice period & CTC | Immediate-joiner and CTC-below-minimum fail with evidence; warning mode; negotiable handling; missing info → "not stated" (never a failure); validation errors |
| Career Roadmap | Real aggregation (Kotlin required in 3 of 4 jobs); verified skills never listed as gaps; status updates and validation; history; plan entitlement → 402 |
| Backups & recovery | Backup created and verified; restore after missing DB (DB + `cv.md` restored); restore after corrupted DB (corrupt copy kept aside); no restore over a valid DB; retention pruning; change-triggered debounce; AES-256-GCM encryption required for cloud; SigV4 matches AWS's published test vector |
| Health & maintenance | `/health` is minimal and public; maintenance mode blocks users but not staff; account deletion removes data |

## Automated UI journeys (Playwright, headless Chromium): 18/18 steps, 0 console errors

1. Landing page (hero, features, pricing showing "To be announced", FAQ, contact)
2. Synthetic demo, labelled on every tab
3. `/app` redirects to login
4. Owner setup → mandatory TOTP enrolment (real codes) → admin overview
5. All 16 admin sections render; manual backup runs
6. Admin sets the Basic price and creates an announcement, which shows on the public site
7. User registers → profile setup
8. Upload → confirm facts → trial badge appears
9. Notice period & CTC preferences
10. Manual job with CTC evidence → gate confirmation → shortlist
11. Tailored resume with honest ATS label, approve, PDF download
12. Prepare application → pause before submit → user marks applied
13. Career Roadmap, Plan & usage, Support ticket, Account
14. Remaining app pages
15. A normal user is refused `/admin`
16. Theme persistence, skip link, `/` search
17. No horizontal overflow at **340 px** (mobile) and **820 px** (tablet) on 9 pages
18. 404 page

## Not verified automatically / pending
- **Google OAuth & SMTP email:** code paths are implemented but not exercised live, because no credentials are configured.
- **Cloud backup upload to R2/S3:** request signing is verified against AWS's test vector; a live upload is pending credentials. The local backup backend is fully tested.
- **Render persistence across redeploys:** **not satisfied** on the free plan (no persistent disk). See REQUIRED_USER_ACTIONS §B.
- **Local Docker run:** the Docker daemon wasn't running on the dev machine. The image build is verified by the CI `docker` job and by Render.
- **Live production checks:** see the deployment section of the final report.
