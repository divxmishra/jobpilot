import { h, icon, mount } from "../../ui/dom.js";
import { get, post } from "../../api.js";
import { callout, toast, withBusy, scoreRing, badge } from "../../ui/components.js";
import { formatPrice, page } from "./_layout.js";

const FEATURES = [
  ["target", "blue", "Only genuine matches", "Every mandatory requirement must pass and your resume must match at least 85% before a job is recommended. Zero results is a valid answer."],
  ["shield", "green", "Truthful tailored resumes", "Resumes are built only from facts you verified. Anything unsupported is flagged and blocked — no keyword stuffing."],
  ["file", "orange", "Honest ATS estimate", "See the real compatibility score of the actual PDF. If 95+ isn't possible truthfully, JobPilot says so and explains why."],
  ["search", "blue", "Evidence for every score", "Matched skills with resume evidence, missing requirements, notice-period and CTC checks — all quoted from the job description."],
  ["chart", "orange", "Career Roadmap", "Recurring gaps across your recent jobs become a ranked learning plan, with clearly labelled estimates of what each skill could unlock."],
  ["briefcase", "green", "You stay in control", "JobPilot prepares and pre-fills, then pauses. It never clicks Submit and never sends messages on your behalf."],
];
const FAQ = [
  ["Does JobPilot apply to jobs for me?", "No. It finds, evaluates and prepares. You review everything and submit personally."],
  ["Will it add skills I don't have to my resume?", "No. Tailoring only selects and orders facts you confirmed. Edits that introduce unverified skills or numbers are blocked from approval."],
  ["Is the ATS score what employers see?", "No. It's JobPilot's own estimate for the specific job, calculated from your actual generated PDF. It is not an employer's score or a guarantee."],
  ["What happens after the free trial?", "You move to the Free plan automatically. Nothing is charged and your data is kept."],
  ["Who can see my resume?", "Only you. Accounts are isolated, and admin analytics never include resume contents."],
  ["Is it only for freshers?", "No. Evaluation adapts to your verified profile — freshers and experienced professionals alike."],
];

export async function render(ctx) {
  ctx.setTitle("");
  let plans = { plans: [] }, anns = [];
  try { [plans, anns] = await Promise.all([get("/api/public/plans"), get("/api/public/announcements")]); } catch { /* shown as unavailable */ }
  const cfg = ctx.session.config;
  const trialPlan = cfg.trial_plan || "Pro";

  const heroVisual = h("div", { class: "hero-visual", "aria-label": "Illustrative example with sample data" },
    h("div", { class: "row-between mb-2" }, h("span", { class: "badge b-accent" }, icon("info", 12), "Illustration · sample data"), badge("qualified")),
    h("div", { class: "row", style: { gap: "14px" } }, scoreRing(91.6, { size: "lg" }),
      h("div", null, h("div", { style: { fontWeight: 700, fontSize: "17px" } }, "Backend Engineer"), h("div", { class: "text-2 small" }, "Sample Company · Bengaluru · Hybrid"))),
    h("div", { class: "stack mt-4 small" },
      ...[["pass", "4+ years' experience — you have 6.2"], ["pass", "Notice ≤ 30 days — yours is 30"], ["pass", "CTC 18–24 LPA ≥ your minimum 16"], ["warning", "Kubernetes preferred — skill gap, not claimed"]]
        .map(([r, t]) => h("div", { class: "row" }, badge(r), h("span", null, t)))));

  const priceCards = plans.plans.length ? plans.plans.map((p, i) => {
    const price = formatPrice(p);
    const featured = p.trial_eligible;
    return h("article", { class: `card price-card ${featured ? "featured" : ""}` },
      featured ? h("span", { class: "ribbon" }, `${cfg.trial_days}-day free trial`) : null,
      h("h3", null, p.name), h("p", { class: "text-2 small", style: { margin: 0 } }, p.description),
      h("div", { class: "price" }, price.main, " ", h("small", null, price.sub)),
      p.promo_label ? h("span", { class: "badge b-accent" }, p.promo_label) : null,
      h("ul", null, p.features.map((f) => h("li", null, icon("check", 14), f))),
      h("span", { class: "grow" }),
      h("a", { class: `btn ${featured ? "btn-accent" : i === 0 ? "btn-primary" : ""}`, href: ctx.session.authenticated ? "/app/subscription" : "/register" },
        p.is_free_default ? "Start free" : featured ? "Start free trial" : "Choose " + p.name));
  }) : [callout("info", "Plans are being set up. Please check back soon.")];

  const contactForm = (() => {
    const email = h("input", { type: "email", id: "c-email", required: true, autocomplete: "email" });
    const subject = h("input", { type: "text", id: "c-subject", required: true });
    const msg = h("textarea", { id: "c-msg", required: true, rows: 4 });
    const btn = h("button", { class: "btn btn-primary", type: "submit" }, "Send message");
    const out = h("div", { role: "status" });
    return h("form", { class: "card card-pad stack", style: { maxWidth: "640px", margin: "0 auto" }, novalidate: true, onsubmit: (e) => {
      e.preventDefault();
      withBusy(btn, async () => {
        try { const r = await post("/api/public/contact", { email: email.value.trim(), subject: subject.value.trim(), message: msg.value.trim() }); mount(out, callout("success", r.message)); e.target.reset(); }
        catch (er) { mount(out, callout("danger", er.message)); }
      });
    } }, h("div", { class: "form-grid" }, h("label", { class: "field", for: "c-email" }, "Your email", email), h("label", { class: "field", for: "c-subject" }, "Subject", subject),
      h("label", { class: "field full", for: "c-msg" }, "Message", msg)), out, h("div", null, btn),
      cfg.support_email ? h("p", { class: "xs muted" }, "Or email ", h("a", { href: `mailto:${cfg.support_email}` }, cfg.support_email)) : null);
  })();

  mount(ctx.root, page(ctx.session,
    cfg.maintenance ? h("div", { class: "announcement warning" }, icon("alert", 16), cfg.maintenance_message) : null,
    anns.map((a) => h("div", { class: `announcement ${a.level}`, role: "status" }, icon(a.level === "warning" ? "alert" : "info", 16), h("strong", null, a.title), a.body ? h("span", null, " — ", a.body) : null)),
    h("section", { class: "hero" }, h("div", { class: "inner" },
      h("div", { class: "fade-in" },
        h("span", { class: "pill" }, icon("shield", 14), "Truthful by design · you always submit"),
        h("h1", null, "Find jobs you ", h("span", { class: "hl" }, "genuinely qualify for"), ". Apply with resumes that ", h("span", { class: "hl2" }, "stay honest"), "."),
        h("p", { class: "lead" }, "JobPilot reads your verified resume, checks every job's hard requirements, explains each match with evidence, and tailors ATS-friendly resumes without inventing anything."),
        h("div", { class: "row" },
          h("a", { class: "btn btn-accent btn-lg", href: ctx.session.authenticated ? "/app" : "/register" }, ctx.session.authenticated ? "Open your dashboard" : `Start your ${cfg.trial_days}-day ${trialPlan} trial`),
          cfg.demo_enabled ? h("a", { class: "btn btn-lg", href: "/demo" }, icon("play", 16), "Explore the demo") : null),
        h("p", { class: "xs muted mt-2" }, "No card needed. The trial starts when you finish profile setup and ends on the Free plan — never an unexpected charge.")),
      heroVisual)),
    h("section", { class: "site-section", id: "features" }, h("div", { class: "inner" },
      h("h2", null, "Built around the truth about your experience"), h("p", { class: "sub" }, "Less noise, fewer wasted applications, and resumes you can stand behind in an interview."),
      h("div", { class: "feature-grid" }, FEATURES.map(([ic, col, t, d]) => h("div", { class: "card feature" }, h("div", { class: `ic ${col}` }, icon(ic, 20)), h("h3", null, t), h("p", null, d)))))),
    h("section", { class: "site-section alt", id: "how" }, h("div", { class: "inner" },
      h("h2", null, "How JobPilot works"), h("p", { class: "sub" }, "Four steps from resume to a reviewed application."),
      h("div", { class: "steps-row" },
        [["Verify your profile", "Upload your resume; confirm each extracted fact. Unclear items stay unverified."],
          ["Find real matches", "Paste job links or connect company job boards. Hard requirements are checked first."],
          ["Tailor truthfully", "Generate a resume for a qualifying job and see the honest ATS estimate."],
          ["Apply yourself", "JobPilot prepares answers and pauses. You submit and track the outcome."]]
          .map(([t, d]) => h("div", { class: "card st" }, h("h3", null, t), h("p", { class: "text-2 small", style: { margin: "6px 0 0" } }, d)))))),
    h("section", { class: "site-section", id: "benefits" }, h("div", { class: "inner" },
      h("h2", null, "Why it helps"), h("p", { class: "sub" }, "JobPilot is designed to save time and keep your applications credible. It does not promise interviews or offers."),
      h("div", { class: "feature-grid" },
        [["Skip unsuitable jobs", "Mandatory-requirement checks catch mismatches before you spend time on them."],
          ["Understand every decision", "Each recommendation shows the evidence and the gaps, so you can decide quickly."],
          ["Plan your growth", "See which missing skills keep appearing in the jobs you want."]]
          .map(([t, d]) => h("div", { class: "card feature" }, h("h3", null, t), h("p", null, d)))))),
    h("section", { class: "site-section alt", id: "pricing" }, h("div", { class: "inner" },
      h("h2", null, "Simple plans"), h("p", { class: "sub" }, `Start free. Your ${cfg.trial_days}-day ${trialPlan} trial begins when you complete profile setup. Online payments are not enabled yet — nobody is charged.`),
      h("div", { class: "pricing" }, priceCards))),
    h("section", { class: "site-section faq", id: "faq" }, h("div", { class: "inner", style: { maxWidth: "760px" } },
      h("h2", { class: "mb-4" }, "Frequently asked questions"),
      FAQ.map(([q, a]) => h("details", null, h("summary", null, q), h("p", null, a))))),
    h("section", { class: "site-section alt", id: "contact" }, h("div", { class: "inner" },
      h("h2", null, "Contact & support"), h("p", { class: "sub" }, "Questions, feedback or account help — we read every message."), contactForm)),
  ));
}
