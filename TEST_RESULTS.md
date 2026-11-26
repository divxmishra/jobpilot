# Test results

Run on Windows 11, Python 3.13.7, 2026-11-20. Re-run with `scripts\test.ps1`.
CI (`.github/workflows/ci.yml`) runs the same suites on every push.

## Automated — backend & safety (`pytest`): **35 passed, 0 failed**

| Area | Tests |
|---|---|
| Resume parsing (PDF + DOCX), bad-upload rejection, no invented dates | `test_parse_docx_and_pdf_resume`, `test_rejects_bad_uploads`, `test_never_invents_missing_dates` |
| Profile verification, immutable master CV, versioning, unverified facts unused | `test_profile_requires_explicit_confirmation_and_master_is_immutable`, `test_profile_versioning`, `test_unverified_facts_are_not_used` |
| Fresher **and** experienced profiles; no fresher-only hardcoding | `test_experienced_candidate_qualifies_for_senior_role`, `test_fresher_qualifies_for_fresher_role_but_not_senior`, `test_no_fresher_hardcoding_in_source` |
| Hard gates beat scores; < 85% never recommended; tailoring refused | `test_hard_gate_cannot_be_overridden_by_high_score`, `test_below_threshold_never_recommended` |
| Threshold change is user-only, logged, re-evaluates | `test_threshold_change_is_user_only_and_reevaluates` |
| Zero qualifying jobs is valid | `test_zero_qualifying_jobs_is_valid` |
| Fee red flags + prompt-injection flagged | `test_red_flags_and_injection` |
| Scores truncated, never rounded up | `test_score_never_rounded_up` |
| Every resume claim traceable; gaps not claimed | `test_tailored_resume_claims_trace_to_verified_evidence` |
| ATS score honest (< 95 labelled, components sum) | `test_ats_score_honest_below_target` |
| Edits can't add skills/metrics; approval blocked | `test_editing_cannot_introduce_unsupported_claims` |
| PDF/DOCX text extraction + page preview | `test_pdf_docx_text_extraction` |
| Master profile unchanged by tailoring | `test_master_profile_unchanged_by_tailoring` |
| Approval → application → status tracking | `test_approval_and_application_tracking` |
| Outreach never sent; one follow-up; verified needs source | `test_outreach_never_sent_and_single_follow_up` |
| Extraction: active / expired / closed / 404 / malformed / unreachable | `test_extraction_expiry_and_404` |
| Manual URL pipeline + dedupe (URL and company/title/location) | `test_manual_url_pipeline_and_dedupe`, `test_dedupe_by_company_title_location` |
| Missing AI key, budget caps, auto-pause; AI output must quote JD | `test_ai_disabled_without_key_and_budget_caps`, `test_ai_output_must_quote_jd` |
| Secret redaction, CSRF, production password, no secrets in frontend | `test_secrets_redacted`, `test_csrf_header_required`, `test_production_requires_password`, `test_no_secrets_in_frontend` |
| Discovery with no sources; data export excludes browser profile; reports written | `test_discovery_with_no_sources_and_export`, `test_reports_written` |
| **Never auto-submits** (static + behavioural Playwright test on a local form) | `test_browser_safety.py` (2 tests) |

## Automated — UI end-to-end (Playwright, headless Chromium): **22/22 steps, 0 console errors**

`tests/run_e2e.py` starts a fresh server with an empty data directory and walks the journeys:
first-run setup → upload DOCX → extraction preview → blocked confirmation → correct & confirm →
preferences → invalid URL validation → manual job → gate confirmation → qualified → shortlist →
generate resume (real PDF preview, honest ATS label) → ATS report & keyword map → unsupported edit
blocks approval → approve truthful version → PDF/DOCX downloads → prepare application → review →
pause before submission (no submit control) → mark applied by user → all pages render → outreach
draft approve → activity search → theme persists across reload → keyboard (skip link, `/` search)
→ mobile 390 px: no horizontal overflow on 7 pages, nav drawer → not-found route.

## Container

`docker build` succeeded. Running container verified: healthy health-check, 401 without login,
wrong password rejected, Secure session cookie, profile upload → evaluation → tailored resume →
PDF + PNG preview rendered inside Linux, browser-prep correctly disabled server-side, CSP present.

## Live integrations (manual verification, read-only public APIs)

| Source | Result |
|---|---|
| Greenhouse board API | 246 postings listed; single-posting extraction complete; bogus ID → "not_found (404)" |
| Lever postings API | 307 postings listed |
| Ashby job-board API | 79 postings listed |
| Remotive API | 15 postings listed |
| AI providers | Not tested live — no API key configured (optional). Deterministic mode verified. |

## CI (GitHub Actions)

`CI` workflow green on `main` (commit 528ad62): backend + safety tests, UI end-to-end journeys
(Ubuntu runner), Docker build. Earlier CI runs exposed two real defects (first-load focus order,
mobile button overflow with wider Linux fonts) which were fixed; the UI test now runs at 340 px.

## Live deployment (Render) — `tests/verify_live.py`: **14/14 checks passed**

https://jobpilot-xg5p.onrender.com — health OK, production mode with sign-in required, access
password configured, API and profile data return 401 without a session, CSRF header enforced, wrong
password rejected, SPA + static assets + deep links served, CSP / nosniff / frame-deny headers,
API docs hidden, HTTP→HTTPS redirect. Live sign-in page verified in headless Chromium (no page errors).
Signed-in journeys on the live site were not automated because the access password is known only to you.

## AWS EC2 attempt (not completed)

Instance `JobPilot-Server` (t3.micro, ap-south-1) was running and answered on ports 80/443, but
SSH (22) timed out on 5 attempts even with the rule opened to 0.0.0.0/0 — consistent with a subnet
network ACL or the rule being on a different security group. Per your instruction, deployment
fell back to Render. The EC2 path (`docker-compose.yml`, `deploy/ec2-setup.sh`, Caddy HTTPS,
`deploy-ec2.yml` workflow) is ready for a later retry.

## Pending / not automatically verifiable

- Real Playwright prefill against third-party application portals (deliberately not exercised against live employer sites; verified on a local test form).
