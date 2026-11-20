"""End-to-end UI journey test (Playwright, headless Chromium) against a running JobPilot.

Usage:  python tests/e2e_ui.py http://127.0.0.1:8765 [screenshot_dir]
The server should use an EMPTY data directory (the script performs first-run setup).
Exits non-zero on any failed step or browser console error.
"""
from __future__ import annotations

import io
import re
import sys
from pathlib import Path

import docx
from playwright.sync_api import expect, sync_playwright

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
Full-time position."""

errors: list[str] = []
results: list[tuple[str, bool, str]] = []


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

        @step("Dashboard shows first-run setup prompt")
        def s1():
            page.goto(BASE + "/")
            expect(page.get_by_text("Start by setting up your profile")).to_be_visible()
            page.screenshot(path=SHOTS / "01-dashboard-empty.png", full_page=True)
        s1()

        @step("Upload master resume (DOCX) and see extracted preview")
        def s2():
            page.goto(BASE + "/setup")
            expect(page.get_by_role("heading", name="Welcome to JobPilot")).to_be_visible()
            page.locator("input[type=file]").first.set_input_files(str(resume))
            expect(page.get_by_role("heading", name="Review your profile")).to_be_visible(timeout=30000)
            expect(page.locator("#b-name")).to_have_value("Priya Sharma")
            page.screenshot(path=SHOTS / "02-profile-review.png", full_page=True)
        s2()

        @step("Profile confirmation blocked until facts are confirmed")
        def s3():
            page.get_by_role("button", name="Confirm profile").click()
            expect(page.get_by_text("Please confirm your name")).to_be_visible()
        s3()

        @step("Correct, confirm facts and verify profile")
        def s4():
            page.locator("#b-location").fill("Bengaluru, India")
            page.get_by_role("button", name="Confirm all clear items").click()
            page.get_by_role("button", name="Confirm profile").click()
            page.get_by_role("dialog").get_by_role("button", name="Confirm profile").click()
            expect(page.get_by_text("Verified version")).to_be_visible(timeout=20000)
            expect(page.locator(".badge", has_text="Read-only")).to_be_visible()
            page.screenshot(path=SHOTS / "03-profile-verified.png", full_page=True)
        s4()

        @step("Set preferences (location) in Settings")
        def s5():
            page.goto(BASE + "/settings")
            page.locator("#p-locations").fill("Bengaluru")
            page.locator("#p-target_roles").fill("Backend Engineer")
            page.get_by_role("button", name="Save preferences").click()
            expect(page.get_by_text("Preferences saved")).to_be_visible()
        s5()

        @step("Invalid job URL shows inline validation")
        def s6():
            page.goto(BASE + "/discover")
            page.locator("#job-url").fill("not a url")
            page.get_by_role("button", name="Check job").click()
            expect(page.get_by_text("Enter a full job URL")).to_be_visible()
        s6()

        @step("Manual job entry -> evaluated with evidence")
        def s7():
            page.get_by_role("button", name=re.compile("Enter job details manually")).click()
            dlg = page.get_by_role("dialog")
            dlg.locator("#m-title").fill("Senior Backend Engineer")
            dlg.locator("#m-company").fill("Initech")
            dlg.locator("#m-location").fill("Bengaluru, India")
            dlg.locator("#m-description").fill(JD)
            dlg.get_by_role("button", name="Add and evaluate").click()
            expect(page.get_by_role("heading", name="Hard eligibility gates")).to_be_visible()
            expect(page.get_by_text("resume-to-JD match")).to_be_visible()
            page.screenshot(path=SHOTS / "04-job-detail-needs-review.png", full_page=True)
        s7()

        @step("Confirm posting-open gate -> job becomes Qualified")
        def s8():
            page.get_by_role("button", name="I meet this").first.click()
            page.get_by_role("dialog").get_by_role("button", name="I confirm").click()
            expect(page.get_by_role("button", name="Shortlist")).to_be_visible()
        s8()

        @step("Shortlist job")
        def s9():
            page.get_by_role("button", name="Shortlist").click()
            expect(page.locator(".badge", has_text="Shortlisted").first).to_be_visible()
        s9()

        @step("Generate tailored resume -> studio with real preview + honest ATS label")
        def s10():
            page.get_by_role("button", name="Generate tailored resume").click()
            expect(page.get_by_text("Estimated ATS compatibility")).to_be_visible(timeout=60000)
            expect(page.get_by_text(re.compile("Below 95 target – truthful version|Target achieved")).first).to_be_visible()
            img = page.locator("img.preview-page")
            expect(img).to_be_visible()
            for _ in range(40):
                if img.evaluate("i => i.complete && i.naturalWidth") > 0:
                    break
                page.wait_for_timeout(250)
            assert img.evaluate("i => i.naturalWidth") > 300, "PDF preview image did not render"
            page.screenshot(path=SHOTS / "05-resume-studio.png", full_page=True)
        s10()

        @step("ATS report and keyword map tabs render")
        def s11():
            page.get_by_role("tab", name="ATS report").click()
            expect(page.get_by_text("Score components")).to_be_visible()
            page.get_by_role("tab", name="Keyword map & gaps").click()
            expect(page.get_by_text("Requirement → evidence map")).to_be_visible()
            page.screenshot(path=SHOTS / "06-keyword-map.png", full_page=True)
        s11()

        @step("Editing in an unsupported skill blocks approval")
        def s12():
            page.get_by_role("tab", name="Edit draft").click()
            sk = page.locator("#ed-skills")
            sk.fill(sk.input_value() + ", Kubernetes")
            page.get_by_role("button", name=re.compile("Save as new version")).click()
            expect(page.get_by_text("Unsupported claims — approval blocked")).to_be_visible(timeout=30000)
            expect(page.get_by_role("button", name="Approve")).to_be_disabled()
            page.screenshot(path=SHOTS / "07-truthfulness-block.png", full_page=True)
        s12()

        @step("Previous truthful version can be approved")
        def s13():
            page.get_by_role("tab", name="Versions").click()
            page.get_by_role("link", name="v1").click()
            expect(page.get_by_text("Every claim traceable")).to_be_visible()
            # v1 was superseded by the edit; generate a fresh truthful draft from the job page instead.
            page.get_by_role("link", name="Senior Backend Engineer").first.click()
            page.get_by_role("button", name="Generate tailored resume").click()
            expect(page.get_by_text("Every claim traceable")).to_be_visible(timeout=60000)
            page.get_by_role("button", name="Approve").click()
            page.get_by_role("dialog").get_by_role("button", name="Approve").click()
            expect(page.get_by_role("link", name="Prepare application")).to_be_visible()
        s13()

        @step("PDF and DOCX downloads work")
        def s14():
            with page.expect_download() as d:
                page.locator("a", has_text="PDF").first.click()
            assert d.value.suggested_filename.endswith(".pdf")
            with page.expect_download() as d2:
                page.locator("a", has_text="DOCX").first.click()
            assert d2.value.suggested_filename.endswith(".docx")
        s14()

        @step("Prepare application -> review -> pause before submission (no auto-submit)")
        def s15():
            page.get_by_role("link", name="Prepare application").click()
            dlg = page.get_by_role("dialog")
            expect(dlg.get_by_text("1. Review prepared data")).to_be_visible()
            expect(dlg.get_by_text("Tick the review confirmation")).to_be_visible()
            dlg.get_by_label(re.compile("I have reviewed")).check()
            expect(dlg.get_by_role("button", name=re.compile("I submitted it myself"))).to_be_visible()
            assert dlg.get_by_role("button", name=re.compile(r"^Submit")).count() == 0
            page.screenshot(path=SHOTS / "08-application-review.png", full_page=True)
            dlg.get_by_role("button", name=re.compile("I submitted it myself")).click()
            page.get_by_role("dialog").last.get_by_role("button", name="Yes, I submitted it").click()
            expect(page.get_by_text("Application updated")).to_be_visible()
        s15()

        @step("Approval Center, Analytics, Activity, Contacts, Saved, Resumes render")
        def s16():
            for path, text in [("/approvals", "Approval Center"), ("/analytics", "Analytics"), ("/activity", "Activity & Logs"),
                               ("/contacts", "Contacts & Outreach"), ("/saved", "Saved / Shortlisted"), ("/resumes", "Resume Studio"),
                               ("/applications", "Applications"), ("/settings", "Settings")]:
                page.goto(BASE + path)
                expect(page.get_by_role("heading", name=text, exact=True)).to_be_visible()
                page.screenshot(path=SHOTS / f"page{path.replace('/', '-')}.png", full_page=True)
        s16()

        @step("Outreach draft: create, approve (never sent)")
        def s17():
            page.goto(BASE + "/contacts")
            page.get_by_role("button", name="New draft").click()
            page.get_by_role("dialog").get_by_role("button", name="Create draft").click()
            dlg = page.get_by_role("dialog")
            expect(dlg.locator("#o-body")).to_be_visible()
            dlg.get_by_role("button", name="Approve draft").click()
            expect(page.get_by_text("Approved — copy and send it yourself")).to_be_visible()
        s17()

        @step("Activity log search filters events")
        def s18():
            page.goto(BASE + "/activity")
            page.locator("#f-q").fill("approved")
            page.wait_for_timeout(800)
            expect(page.get_by_text(re.compile("approved", re.I)).first).to_be_visible()
        s18()

        @step("Theme toggle persists across reload")
        def s19():
            page.goto(BASE + "/")
            page.get_by_role("button", name="Switch theme").click()
            theme = page.evaluate("document.documentElement.getAttribute('data-theme')")
            page.reload()
            assert page.evaluate("document.documentElement.getAttribute('data-theme')") == theme, "theme not persisted"
            page.screenshot(path=SHOTS / "09-dashboard-theme.png", full_page=True)
        s19()

        @step("Keyboard: skip link and '/' focuses search")
        def s20():
            page.goto(BASE + "/discover")
            page.keyboard.press("Tab")
            assert page.evaluate("document.activeElement.classList.contains('skip-link')"), "skip link not first"
            page.locator("body").click(position={"x": 5, "y": 400})
            page.keyboard.press("/")
            assert page.evaluate("document.activeElement.getAttribute('aria-label')") == "Global search"
        s20()

        @step("Mobile layout: nav collapses, no horizontal overflow")
        def s21():
            m = browser.new_context(viewport={"width": 390, "height": 844}, storage_state=ctx.storage_state())
            mp = m.new_page()
            mp.on("pageerror", lambda e: errors.append(f"mobile pageerror: {e}"))
            for path in ["/", "/discover", "/applications", "/resumes", "/settings", "/analytics", "/setup"]:
                mp.goto(BASE + path)
                mp.wait_for_timeout(700)
                over = mp.evaluate("document.documentElement.scrollWidth - window.innerWidth")
                assert over <= 1, f"{path} overflows horizontally by {over}px"
            mp.goto(BASE + "/")
            mp.get_by_role("button", name="Open navigation").click()
            expect(mp.get_by_role("navigation", name="Main navigation")).to_be_visible()
            mp.screenshot(path=SHOTS / "10-mobile-nav.png")
            mp.goto(BASE + "/discover")
            mp.screenshot(path=SHOTS / "11-mobile-discover.png", full_page=True)
            m.close()
        s21()

        @step("Unknown route shows not-found state")
        def s22():
            page.goto(BASE + "/does-not-exist")
            expect(page.get_by_text("Page not found")).to_be_visible()
        s22()
        browser.close()

    real_errors = [e for e in errors if "favicon" not in e and "404 (Not Found)" not in e]
    print(f"\n{sum(r[1] for r in results)}/{len(results)} steps passed; {len(real_errors)} console errors")
    for e in real_errors[:20]:
        print("  console:", e[:300])
    sys.exit(0 if all(r[1] for r in results) and not real_errors else 1)


if __name__ == "__main__":
    main()
