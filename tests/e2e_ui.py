"""End-to-end UI journeys (Playwright, headless Chromium) against a running JobPilot with an EMPTY data dir.

Usage:  python tests/e2e_ui.py http://127.0.0.1:8765 [screenshot_dir]   (or tests/run_e2e.py)
Exits non-zero on any failed step or browser console error.
"""
from __future__ import annotations

import io
import os
import re
import sys
from pathlib import Path

import docx
from playwright.sync_api import expect, sync_playwright

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "backend"))
from app.services.auth import totp_code  # noqa: E402

BASE = sys.argv[1] if len(sys.argv) > 1 else "http://127.0.0.1:8765"
SHOTS = Path(sys.argv[2]) if len(sys.argv) > 2 else Path("e2e-shots")
SHOTS.mkdir(parents=True, exist_ok=True)
FIX = Path(__file__).parent / "fixtures"

JD = """We are hiring a Senior Backend Engineer in Bengaluru (hybrid).
Responsibilities
- Design and build REST APIs with Python and FastAPI
- Own PostgreSQL data models and Docker based deployments on AWS
Requirements
- 4+ years of experience in backend development
- Strong Python, Django or FastAPI, PostgreSQL
- Experience with Docker, AWS and CI/CD
- Bachelor's degree in Computer Science or equivalent experience
Nice to have
- Kubernetes, Redis, React
Full-time position. CTC: 18 - 26 LPA."""

errors: list[str] = []
results: list[tuple[str, bool, str]] = []
state: dict = {}


def step(name):
    def deco(fn):
        def run(*a, **k):
            try:
                fn(*a, **k)
                results.append((name, True, ""))
                print(f"PASS  {name}")
            except Exception as e:
                results.append((name, False, str(e)[:300]))
                print(f"FAIL  {name}: {str(e)[:300]}")
                try:
                    pg = state.get("page")
                    print("      at", pg.url, "|", pg.inner_text("body")[:300].replace(chr(10), " "))
                except Exception:
                    pass
                if os.environ.get("GITHUB_ACTIONS"):
                    print(f"::error title=E2E {name}::{str(e)[:400].replace(chr(10), ' ').replace('%', '%25')}")
        return run
    return deco


def make_resume_docx(path: Path) -> None:
    d = docx.Document()
    for line in (FIX / "experienced_resume.txt").read_text(encoding="utf-8").split("\n"):
        d.add_paragraph(line)
    buf = io.BytesIO()
    d.save(buf)
    path.write_bytes(buf.getvalue())


def main():
    resume = SHOTS / "resume_fixture.docx"
    make_resume_docx(resume)
    with sync_playwright() as p:
        browser = p.chromium.launch()
        ctx = browser.new_context(viewport={"width": 1366, "height": 900}, accept_downloads=True)
        page = ctx.new_page()
        page.on("console", lambda m: errors.append(f"{m.type}: {m.text}") if m.type == "error" else None)
        page.on("pageerror", lambda e: errors.append(f"pageerror: {e}"))
        page.set_default_timeout(20000)
        state["page"] = page

        @step("Public landing page: hero, features, pricing, FAQ, contact")
        def s_landing():
            page.goto(BASE + "/")
            expect(page.get_by_role("heading", level=1)).to_contain_text("genuinely qualify for")
            expect(page.locator("#pricing .price-card")).to_have_count(3)
            expect(page.get_by_text("To be announced").first).to_be_visible()  # paid prices not set → honest
            expect(page.get_by_text("Does JobPilot apply to jobs for me?")).to_be_visible()
            page.screenshot(path=SHOTS / "01-landing.png", full_page=True)
        s_landing()

        @step("Public demo with synthetic data, clearly labelled")
        def s_demo():
            page.goto(BASE + "/demo")
            expect(page.get_by_text("Demo mode — everything below is synthetic sample data")).to_be_visible()
            for tab in ("Job matching", "Resume analysis", "Applications", "Analytics"):
                page.get_by_role("tab", name=tab).click()
                expect(page.locator(".card").first).to_be_visible()
            page.screenshot(path=SHOTS / "02-demo.png", full_page=True)
        s_demo()

        @step("App routes require sign-in (redirect to login)")
        def s_guard():
            page.goto(BASE + "/app/discover")
            expect(page).to_have_url(re.compile(r"/login\?next="))
            expect(page.get_by_role("heading", name="Welcome back")).to_be_visible()
        s_guard()

        @step("Owner setup + mandatory two-factor + admin overview")
        def s_owner():
            page.goto(BASE + "/setup-owner")
            page.locator("#secret").fill("local-dev")
            page.locator("#name").fill("Owner")
            page.locator("#email").fill("owner@example.com")
            page.locator("#pw").fill("owner-pass-12345")
            page.get_by_role("button", name="Create owner account").click()
            expect(page.get_by_role("heading", name="Set up two-factor authentication")).to_be_visible()
            secret = page.locator("code").first.inner_text().replace(" ", "")
            state["totp"] = secret
            page.locator("input[autocomplete=one-time-code]").fill(totp_code(secret))
            page.get_by_role("button", name="Enable two-factor").click()
            expect(page.get_by_role("heading", name="Overview")).to_be_visible()
            page.screenshot(path=SHOTS / "03-admin-overview.png", full_page=True)
        s_owner()

        @step("Every admin section renders without errors")
        def s_admin_pages():
            for key, title in [("users", "Users"), ("activity", "User activity"), ("subscriptions", "Subscriptions"), ("revenue", "Revenue"),
                               ("plans", "Plans & pricing"), ("trials", "Trials"), ("limits", "Feature limits"), ("analytics", "Outcome analytics"),
                               ("health", "System health"), ("ai", "AI usage & costs"), ("sources", "Job sources"), ("announcements", "Announcements"),
                               ("support", "Support"), ("audit", "Audit log"), ("security", "Security"), ("settings", "Settings")]:
                page.goto(BASE + f"/admin/{key}")
                expect(page.get_by_role("heading", name=title, exact=True)).to_be_visible()
                assert page.locator(".empty.error").count() == 0, f"error state on /admin/{key}"
            page.goto(BASE + "/admin/settings")
            page.get_by_role("button", name="Run backup now").click()
            expect(page.get_by_text(re.compile("Backup stored"))).to_be_visible(timeout=30000)
            page.screenshot(path=SHOTS / "04-admin-settings.png", full_page=True)
        s_admin_pages()

        @step("Admin: set Basic price and create an announcement")
        def s_admin_edit():
            page.goto(BASE + "/admin/plans")
            page.locator(".price-card", has=page.get_by_role("heading", name="Basic", exact=True)).get_by_role("button", name="Edit").click()
            dlg = page.get_by_role("dialog")
            dlg.get_by_label("Monthly price (₹)").fill("199")
            dlg.get_by_role("button", name="Save plan").click()
            expect(page.get_by_text("Plan saved")).to_be_visible()
            page.goto(BASE + "/admin/announcements")
            page.get_by_role("button", name="New").click()
            dlg = page.get_by_role("dialog")
            dlg.get_by_label("Title").fill("Welcome to JobPilot")
            dlg.get_by_role("button", name="Save").click()
            expect(page.get_by_role("cell", name="Welcome to JobPilot")).to_be_visible()
            page.get_by_role("button", name="Sign out").click()
            expect(page.get_by_role("heading", level=1)).to_contain_text("genuinely qualify for")
            expect(page.get_by_text("Welcome to JobPilot").first).to_be_visible()  # announcement (audience: everyone) not shown on landing banner necessarily
        s_admin_edit()

        @step("Register a user → lands on profile setup")
        def s_register():
            page.goto(BASE + "/register")
            page.locator("#name").fill("Priya")
            page.locator("#email").fill("priya@example.com")
            page.locator("#pw").fill("priya-pass-12345")
            page.locator("#terms").check()
            page.get_by_role("button", name="Create account").click()
            expect(page.get_by_role("heading", name="Welcome to JobPilot")).to_be_visible()
        s_register()

        @step("Upload resume, verify facts, confirm profile → trial starts")
        def s_profile():
            page.locator("input[type=file]").first.set_input_files(str(resume))
            expect(page.get_by_role("heading", name="Review your profile")).to_be_visible(timeout=30000)
            page.get_by_role("button", name="Confirm profile").click()
            expect(page.get_by_text("Please confirm your name")).to_be_visible()
            page.get_by_role("button", name="Confirm all clear items").click()
            page.get_by_role("button", name="Confirm profile").click()
            page.get_by_role("dialog").get_by_role("button", name="Confirm profile").click()
            expect(page.get_by_text("Verified version")).to_be_visible(timeout=20000)
            page.reload()
            expect(page.locator("a.badge", has_text="trial")).to_be_visible()
            page.screenshot(path=SHOTS / "05-profile.png", full_page=True)
        s_profile()

        @step("Preferences incl. notice period & CTC")
        def s_prefs():
            page.goto(BASE + "/app/settings")
            page.locator("#p-locations").fill("Bengaluru")
            page.locator("#p-notice_period").select_option("30")
            page.locator("#p-min_ctc_lpa").fill("16")
            page.get_by_role("button", name="Save preferences").click()
            expect(page.get_by_text("Preferences saved")).to_be_visible()
        s_prefs()

        @step("Manual job → evaluated with CTC evidence → confirm gate → shortlist")
        def s_job():
            page.goto(BASE + "/app/discover")
            page.get_by_role("button", name=re.compile("Enter job details manually")).click()
            dlg = page.get_by_role("dialog")
            dlg.locator("#m-title").fill("Senior Backend Engineer")
            dlg.locator("#m-company").fill("Initech")
            dlg.locator("#m-location").fill("Bengaluru, India")
            dlg.locator("#m-description").fill(JD)
            dlg.get_by_role("button", name="Add and evaluate").click()
            expect(page.get_by_role("heading", name="Hard eligibility gates")).to_be_visible()
            expect(page.get_by_text("Minimum CTC 16 LPA")).to_be_visible()
            page.get_by_role("button", name="I meet this").first.click()
            page.get_by_role("dialog").get_by_role("button", name="I confirm").click()
            page.get_by_role("button", name="Shortlist").click()
            expect(page.locator(".badge", has_text="Shortlisted").first).to_be_visible()
            page.screenshot(path=SHOTS / "06-job-detail.png", full_page=True)
        s_job()

        @step("Tailored resume, honest ATS label, approve, downloads")
        def s_resume():
            page.get_by_role("button", name="Generate tailored resume").click()
            expect(page.get_by_text("Estimated ATS compatibility")).to_be_visible(timeout=60000)
            expect(page.get_by_text(re.compile("Below 95 target – truthful version|Target achieved")).first).to_be_visible()
            img = page.locator("img.preview-page")
            for _ in range(40):
                if img.evaluate("i => i.complete && i.naturalWidth") > 0:
                    break
                page.wait_for_timeout(250)
            assert img.evaluate("i => i.naturalWidth") > 300
            page.get_by_role("button", name="Approve").click()
            page.get_by_role("dialog").get_by_role("button", name="Approve").click()
            expect(page.get_by_role("link", name="Prepare application")).to_be_visible()
            with page.expect_download() as d:
                page.locator("a", has_text="PDF").first.click()
            assert d.value.suggested_filename.endswith(".pdf")
            page.screenshot(path=SHOTS / "07-resume-studio.png", full_page=True)
        s_resume()

        @step("Prepare application → pause before submit → user marks applied")
        def s_apply():
            page.get_by_role("link", name="Prepare application").click()
            dlg = page.get_by_role("dialog")
            expect(dlg.get_by_text("1. Review prepared data")).to_be_visible()
            dlg.get_by_label(re.compile("I have reviewed")).check()
            assert dlg.get_by_role("button", name=re.compile(r"^Submit")).count() == 0
            dlg.get_by_role("button", name=re.compile("I submitted it myself")).click()
            page.get_by_role("dialog").last.get_by_role("button", name="Yes, I submitted it").click()
            expect(page.get_by_text("Application updated")).to_be_visible()
        s_apply()

        @step("Career Roadmap, Plan & usage, Support, Account pages")
        def s_new_pages():
            page.goto(BASE + "/app/roadmap")
            expect(page.get_by_role("heading", name="Career Roadmap", exact=True)).to_be_visible()
            expect(page.get_by_text(re.compile("Based on 1 job"))).to_be_visible()
            page.screenshot(path=SHOTS / "08-roadmap.png", full_page=True)
            page.goto(BASE + "/app/subscription")
            expect(page.get_by_text("day(s) left")).to_be_visible()
            expect(page.get_by_text("Job searches per day")).to_be_visible()
            page.screenshot(path=SHOTS / "09-subscription.png", full_page=True)
            page.goto(BASE + "/app/support")
            page.locator("#s-subject").fill("Question about plans")
            page.locator("#s-msg").fill("When will paid plans be available?")
            page.get_by_role("button", name="Send request").click()
            expect(page.get_by_text("Question about plans")).to_be_visible()
            page.goto(BASE + "/app/account")
            expect(page.get_by_role("heading", name="Account & privacy")).to_be_visible()
        s_new_pages()

        @step("Other app pages render")
        def s_pages():
            for path, text in [("/app", None), ("/app/approvals", "Approval Center"), ("/app/analytics", "Analytics"), ("/app/activity", "Activity & Logs"),
                               ("/app/contacts", "Contacts & Outreach"), ("/app/saved", "Saved / Shortlisted"), ("/app/resumes", "Resume Studio"),
                               ("/app/applications", "Applications")]:
                page.goto(BASE + path)
                if text:
                    expect(page.get_by_role("heading", name=text, exact=True)).to_be_visible()
                else:
                    expect(page.get_by_role("heading", name=re.compile("Welcome back"))).to_be_visible()
                assert page.locator(".empty.error").count() == 0, path
            page.screenshot(path=SHOTS / "10-dashboard.png", full_page=True)
        s_pages()

        @step("Normal user cannot open admin")
        def s_admin_denied():
            page.goto(BASE + "/admin")
            expect(page.get_by_role("heading", name="Not permitted")).to_be_visible()
        s_admin_denied()

        @step("Theme persists; keyboard skip link; '/' search")
        def s_a11y():
            page.goto(BASE + "/app")
            page.get_by_role("button", name="Switch theme").first.click()
            theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
            page.reload()
            assert page.evaluate("document.documentElement.getAttribute('data-theme')") == theme
            page.keyboard.press("Tab")
            assert page.evaluate("document.activeElement.classList.contains('skip-link')")
            page.locator("body").click(position={"x": 5, "y": 400})
            page.keyboard.press("/")
            assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "Global search"
            page.screenshot(path=SHOTS / "11-dark.png", full_page=True)
        s_a11y()

        @step("Mobile & tablet: no horizontal overflow; nav drawer")
        def s_mobile():
            for vw, vh in ((340, 740), (820, 1180)):
                m = browser.new_context(viewport={"width": vw, "height": vh}, storage_state=ctx.storage_state())
                mp = m.new_page()
                mp.on("pageerror", lambda e: errors.append(f"mobile pageerror: {e}"))
                for path in ["/", "/demo", "/app", "/app/discover", "/app/applications", "/app/roadmap", "/app/subscription", "/app/settings", "/app/setup"]:
                    mp.goto(BASE + path)
                    mp.wait_for_timeout(600)
                    over = mp.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                    assert over <= 1, f"{vw}px {path} overflows by {over}px"
                if vw < 400:
                    mp.goto(BASE + "/app")
                    mp.get_by_role("button", name="Open navigation").click()
                    expect(mp.get_by_role("navigation", name="Main navigation")).to_be_visible()
                    mp.screenshot(path=SHOTS / "12-mobile-nav.png")
                    mp.goto(BASE + "/")
                    mp.screenshot(path=SHOTS / "13-mobile-landing.png", full_page=True)
                m.close()
        s_mobile()

        @step("Unknown route shows not-found state")
        def s_404():
            page.goto(BASE + "/app/does-not-exist")
            expect(page.get_by_text("Page not found")).to_be_visible()
        s_404()
        browser.close()

    real = [e for e in errors if "favicon" not in e and "404 (Not Found)" not in e and "401" not in e and "403" not in e and "402" not in e]
    print(f"\n{sum(r[1] for r in results)}/{len(results)} steps passed; {len(real)} console errors")
    for e in real[:20]:
        print("  console:", e[:300])
        if os.environ.get("GITHUB_ACTIONS"):
            print(f"::error title=E2E console error::{e[:300]}")
    sys.exit(0 if all(r[1] for r in results) and not real else 1)


if __name__ == "__main__":
    main()
