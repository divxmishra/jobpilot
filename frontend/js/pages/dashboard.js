import { h, icon, mount, fmt } from "../ui/dom.js";
import { get } from "../api.js";
import { badge, callout, emptyState, errorState, jobCard, loading, meter } from "../ui/components.js";
import { pageHead, section } from "./_common.js";

export async function render(ctx) {
  ctx.setTitle("Dashboard");
  const load = async () => {
    mount(ctx.root, loading(5));
    let d;
    try { d = await get("/api/dashboard"); } catch (e) { mount(ctx.root, errorState(e, load)); return; }
    if (!ctx.isCurrent()) return;
    const s = d.sections;
    const stat = (label, value, href, ic, hint) => h("a", { class: "card stat", href }, h("span", { class: "label" }, icon(ic, 15), label), h("span", { class: "value" }, value), hint ? h("span", { class: "hint" }, hint) : null);

    const setup = !d.profile.verified ? callout("info", h("div", { class: "row-between" },
      h("div", null, h("strong", null, d.profile.draft_pending ? "Finish verifying your profile" : "Start by setting up your profile"),
        h("div", null, "JobPilot evaluates jobs only against facts you've verified from your master resume.")),
      h("a", { class: "btn btn-primary", href: "/setup" }, d.profile.draft_pending ? "Continue setup" : "Upload resume")), "user") : null;

    const usage = d.usage;
    const health = d.health;
    mount(ctx.root,
      pageHead({ title: d.profile.name ? `Welcome back, ${d.profile.name.split(" ")[0]}` : "Dashboard",
        desc: "What JobPilot found, what needs your decision, and what happens next. Only jobs passing every hard requirement and your match threshold are recommended — zero is a valid result.",
        actions: [h("a", { class: "btn", href: "/discover" }, icon("search", 16), "Discover jobs"), h("a", { class: "btn btn-primary", href: "/approvals" }, icon("checkCircle", 16), `Approvals (${d.pending_approvals})`)] }),
      setup ? h("div", { class: "mb-4" }, setup) : null,
      h("div", { class: "grid grid-4 mb-4" },
        stat("Recommended", s.recommended, "/discover?view=recommended", "checkCircle", `≥ ${d.threshold}% match, all gates passed`),
        stat("Pending verification", s.pending_verification, "/discover?view=needs_review", "help", "Needs your confirmation"),
        stat("Documents awaiting review", s.documents_awaiting_review, "/resumes", "file", "Tailored resume drafts"),
        stat("Submitted by you", s.user_submitted, "/applications", "briefcase", "Applied, interviewing or offers")),
      h("div", { class: "grid grid-4 mb-4" },
        stat("Shortlisted", s.shortlisted, "/saved", "bookmark"),
        stat("Near match", s.near_match, "/discover?view=near_match", "target", "Below threshold; not recommended"),
        stat("Rejected / not eligible", s.rejected, "/discover?view=rejected", "xCircle"),
        stat("Expired / closed", s.expired, "/discover?view=expired", "clock")),
      h("div", { class: "split-wide" },
        h("div", { class: "stack" },
          section("Recommended jobs", d.recommended.length ? h("div", { class: "job-list" }, d.recommended.map((j) => jobCard(j, { threshold: d.threshold, navigate: ctx.navigate })))
            : emptyState({ icon: "target", title: "No qualifying jobs right now", body: "That's a valid result — JobPilot never pads the list with weak matches. Add target companies or paste a job URL to check a specific posting.",
              action: h("a", { class: "btn btn-primary", href: "/discover" }, "Go to Discover Jobs") }),
          { actions: [h("a", { class: "btn btn-sm btn-ghost", href: "/discover" }, "View all")] }),
          section("Resume drafts awaiting review", d.documents_review.length ? h("ul", { class: "timeline" }, d.documents_review.map((r) => h("li", null,
            h("span", { class: "when" }, fmt.ago(r.created_at)), h("a", { href: `/resumes/${r.id}` }, `v${r.version} · ${r.title} — ${r.company}`, " ", h("span", { class: "badge b-info" }, `ATS ${r.ats_score}`)))))
            : h("p", { class: "muted" }, "No drafts waiting."))),
        h("div", { class: "stack" },
          section("Application status", Object.keys(d.applications).length ? h("div", { class: "stack" }, Object.entries(d.applications).map(([k, v]) => h("div", { class: "row-between" }, badge(k), h("strong", null, v))))
            : h("p", { class: "muted" }, "No applications yet. Prepare one from a shortlisted job with an approved resume."), { actions: [h("a", { class: "btn btn-sm btn-ghost", href: "/applications" }, "Open")] }),
          section("System health", h("div", { class: "stack small" },
            h("div", { class: "row-between" }, h("span", null, "Database"), badge("pass", "OK")),
            h("div", { class: "row-between" }, h("span", null, "Last discovery"), h("span", null, health.last_discovery ? fmt.ago(health.last_discovery.at) : "Never run")),
            h("div", { class: "row-between" }, h("span", null, "Daily discovery"), h("span", null, health.scheduler.enabled ? `On, ${String(health.scheduler.hour).padStart(2, "0")}:00` : "Off")),
            h("div", { class: "row-between" }, h("span", null, "Errors (24 h)"), health.errors_24h ? h("a", { href: "/activity?level=error", class: "badge b-danger" }, icon("alert", 12), health.errors_24h) : badge("pass", "None")),
            h("div", { class: "row-between" }, h("span", null, "AI assistance"), health.ai.active ? badge("pass", `On (${health.ai.provider})`) : h("span", { class: "badge b-neutral" }, health.ai.paused ? "Paused" : health.ai.key_configured ? "Off" : "Not configured")),
            h("div", { class: "row-between" }, h("span", null, "Browser prep"), health.browser_automation.available ? badge("pass", "Available") : h("span", { class: "badge b-neutral", title: health.browser_automation.message }, "Manual workflow")),
            health.storage_note ? callout("warn", health.storage_note) : null)),
          section("API usage & budget", h("div", { class: "stack small" },
            h("div", { class: "row-between" }, h("span", null, "Today"), h("span", null, `${fmt.inr(usage.today_cost_inr)} of ${fmt.inr(usage.daily_cap_inr)} · ${usage.today_calls} calls`)),
            meter(usage.today_cost_inr, usage.daily_cap_inr || 1),
            h("div", { class: "row-between" }, h("span", null, "This month"), h("span", null, `${fmt.inr(usage.month_cost_inr)} of ${fmt.inr(usage.monthly_cap_inr)}`)),
            meter(usage.month_cost_inr, usage.monthly_cap_inr || 1),
            h("span", { class: "xs muted" }, `${usage.month_cache_hits} cached responses reused this month. AI is optional; all core features work without it.`))),
          section("Recent activity", d.recent_activity.length ? h("ul", { class: "timeline" }, d.recent_activity.map((a) => h("li", null, h("span", { class: "when" }, fmt.ago(a.at)),
            h("span", null, a.level !== "info" ? badge(a.level === "error" ? "error" : "needs_review", fmt.cap(a.level)) : null, " ", a.message))))
            : h("p", { class: "muted" }, "Nothing yet."), { actions: [h("a", { class: "btn btn-sm btn-ghost", href: "/activity" }, "All activity")] }))));
  };
  await load();
}
