// Public demo: 100% synthetic, hard-coded sample data. Makes no API calls for user data,
// never touches real accounts and never runs AI. Clearly labelled throughout.
import { h, icon, mount } from "../../ui/dom.js";
import { badge, gatesList, jobCard, meter, scoreBreakdown } from "../../ui/components.js";
import { barChart } from "../../ui/charts.js";
import { page } from "./_layout.js";

const SAMPLE_JOBS = [
  { id: "demo1", title: "Backend Engineer", company: "Example Fintech (sample)", location: "Bengaluru, India", work_mode: "hybrid", employment_type: "full-time",
    posted_at: null, source: "sample", source_url: null, match_score: 91.6, status: "qualified", status_label: "Qualified", extraction_complete: 1, alt_urls: [] },
  { id: "demo2", title: "Python Developer", company: "Sample Analytics Co.", location: "Remote, India", work_mode: "remote", employment_type: "full-time",
    posted_at: null, source: "sample", source_url: null, match_score: 87.2, status: "shortlisted", status_label: "Shortlisted", extraction_complete: 1, alt_urls: [] },
  { id: "demo3", title: "Platform Engineer", company: "Demo Cloud Ltd.", location: "Pune, India", work_mode: "onsite", employment_type: "full-time",
    posted_at: null, source: "sample", source_url: null, match_score: 78.4, status: "near_match", status_label: "Near Match", extraction_complete: 1, alt_urls: [] },
];
const SAMPLE_MATCH = {
  score: 91.6, confidence: "high", rubric_version: "match-v1", uncertainties: [],
  components: [
    { label: "Required skills", weight: 35, points: 35, applicable: true, detail: "7 of 7 matched" },
    { label: "Preferred skills", weight: 10, points: 6.67, applicable: true, detail: "2 of 3 matched" },
    { label: "Responsibilities & keyword relevance", weight: 15, points: 10, applicable: true, detail: "10 of 15 key JD terms appear in the profile" },
    { label: "Experience", weight: 15, points: 15, applicable: true, detail: "6.2 verified years; the posting asks for 4+" },
    { label: "Seniority fit", weight: 10, points: 10, applicable: true, detail: "Role level 'senior' vs stage 'senior'" },
    { label: "Education & certifications", weight: 5, points: 5, applicable: true, detail: "Bachelor's required; bachelor's held" },
    { label: "Role / domain relevance", weight: 5, points: 5, applicable: true, detail: "Closest match: 'Backend Engineer'" },
    { label: "Preference fit", weight: 5, points: 5, applicable: true, detail: "3 of 3 preferences satisfied" },
  ],
};
const SAMPLE_GATES = [
  { label: "Posting is open", result: "pass", detail: "Listed as open on the employer's job board" },
  { label: "At least 4 years' experience", result: "pass", detail: "Verified experience: 6.2 years", evidence: "4+ years of backend development experience" },
  { label: "Notice period", result: "pass", detail: "Posting asks for notice of at most 30 days; yours is 30 days", evidence: "Notice period up to 30 days" },
  { label: "Minimum CTC 16 LPA", result: "pass", detail: "Stated range reaches 24 LPA", evidence: "CTC: 18 - 24 LPA (fixed + variable)" },
  { label: "Location preference", result: "pass", detail: "Bengaluru, India" },
];

export async function render(ctx) {
  ctx.setTitle("Demo");
  const tab = (ctx.path.split("/")[2] || "dashboard");
  const tabs = [["dashboard", "Dashboard"], ["matching", "Job matching"], ["resume", "Resume analysis"], ["applications", "Applications"], ["analytics", "Analytics"]];
  const nav = h("div", { class: "tabs", role: "tablist" }, tabs.map(([k, l]) => h("a", { href: `/demo/${k}`, role: "tab", "aria-selected": String(tab === k) }, l)));
  const body = {
    dashboard: () => h("div", { class: "stack" },
      h("div", { class: "grid grid-4" }, [["Recommended", 2, "checkCircle"], ["Pending verification", 1, "help"], ["Documents awaiting review", 1, "file"], ["Submitted by you", 3, "briefcase"]]
        .map(([l, v, ic]) => h("div", { class: "card stat" }, h("span", { class: "label" }, icon(ic, 15), l), h("span", { class: "value" }, v)))),
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, "Recommended jobs (sample)")),
        h("div", { class: "card-body job-list" }, SAMPLE_JOBS.slice(0, 2).map((j) => jobCard(j, { navigate: () => ctx.navigate("/demo/matching") }))))),
    matching: () => h("div", { class: "split-wide" },
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, "Score breakdown (sample)")), h("div", { class: "card-body" }, scoreBreakdown(SAMPLE_MATCH))),
      h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, "Hard eligibility gates (sample)")), h("div", { class: "card-body" }, gatesList(SAMPLE_GATES)))),
    resume: () => h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, "ATS report (sample)"), h("span", { class: "badge b-warning" }, icon("alert", 12), "Below 95 target – truthful version")),
      h("div", { class: "card-body stack" },
        h("div", { style: { fontSize: "34px", fontWeight: 800 } }, "92.7", h("small", { class: "muted" }, " / 100")),
        [["Keyword coverage (in rendered PDF)", 34.1, 40], ["Standard section headings", 10, 10], ["Parsing & file integrity", 15, 15], ["Chronology & dates", 10, 10],
          ["ATS-safe formatting", 10, 10], ["Bullet relevance", 8.6, 10], ["Contact details", 5, 5]]
          .map(([l, p, w]) => h("div", { class: "bd-row" }, h("strong", null, l), meter(p, w, p / w >= .95 ? "ok" : "warn"), h("span", { class: "pts" }, `${p} / ${w}`))),
        h("p", { class: "small text-2" }, "Kubernetes is preferred by this job but isn't in the sample profile — it's listed as a skill gap, never added to the resume."))),
    applications: () => h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, "Application pipeline (sample)")),
      h("div", { class: "card-body" }, h("table", { class: "table responsive" }, h("thead", null, h("tr", null, ["Company", "Role", "Status", "Next action"].map((t) => h("th", null, t)))),
        h("tbody", null, [["Example Fintech (sample)", "Backend Engineer", "prepared", "Review prepared answers"], ["Sample Analytics Co.", "Python Developer", "applied", "Follow up on 5 Dec"],
          ["Demo Retail", "Data Engineer", "interview", "Prepare for technical round"]]
          .map((r) => h("tr", null, h("td", { "data-label": "Company" }, r[0]), h("td", { "data-label": "Role" }, r[1]), h("td", { "data-label": "Status" }, badge(r[2])), h("td", { "data-label": "Next" }, r[3]))))))),
    analytics: () => h("div", { class: "grid grid-2" },
      barChart({ title: "Match score distribution (sample)", data: [["50–59", 3], ["60–69", 6], ["70–79", 9], ["80–89", 5], ["90–99", 2]].map(([label, value]) => ({ label, value })), valueLabel: "Jobs" }),
      barChart({ title: "Most common hard-gate failures (sample)", horizontal: true, data: [["Experience requirement", 7], ["Location preference", 4], ["Notice period", 3], ["Minimum CTC", 2]].map(([label, value]) => ({ label, value })), valueLabel: "Jobs" })),
  }[tab] || (() => h("p", null, "Unknown demo page"));

  mount(ctx.root, page(ctx.session,
    h("div", { class: "demo-banner", role: "note" }, icon("info", 14), " Demo mode — everything below is synthetic sample data. No real people, companies or accounts."),
    h("main", { class: "content", id: "main" },
      h("div", { class: "page-head" }, h("div", null, h("h1", null, "JobPilot demo"), h("p", null, "Explore how matching, resume analysis and tracking work. Create an account to use it with your own resume.")),
        h("div", { class: "page-actions" }, h("a", { class: "btn btn-accent", href: "/register" }, "Create your account"))),
      nav, body())));
}
