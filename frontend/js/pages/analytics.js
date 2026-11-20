import { h, icon, mount, fmt } from "../ui/dom.js";
import { get } from "../api.js";
import { errorState, loading } from "../ui/components.js";
import { barChart } from "../ui/charts.js";
import { pageHead } from "./_common.js";

export async function render(ctx) {
  ctx.setTitle("Analytics");
  const load = async () => {
    mount(ctx.root, loading(4));
    let a;
    try { a = await get("/api/analytics"); } catch (e) { mount(ctx.root, errorState(e, load)); return; }
    if (!ctx.isCurrent()) return;
    const qualifying = a.by_status.filter((s) => ["qualified", "shortlisted"].includes(s.status)).reduce((x, s) => x + s.n, 0);
    const stat = (label, value, hint, ic) => h("div", { class: "card stat" }, h("span", { class: "label" }, icon(ic, 15), label), h("span", { class: "value" }, value), hint ? h("span", { class: "hint" }, hint) : null);
    const day = (d) => d.slice(5);
    mount(ctx.root,
      pageHead({ title: "Analytics", desc: "Computed only from your real data. Where there isn't enough data, charts say so instead of showing estimates." }),
      h("div", { class: "grid grid-4 mb-4" },
        stat("Jobs processed", a.jobs_total, "All sources", "layers"),
        stat("Qualifying", qualifying, a.jobs_total ? `${((100 * qualifying) / a.jobs_total).toFixed(1)}% of processed` : "—", "checkCircle"),
        stat("Applied (by you)", a.applied, "User-confirmed submissions", "briefcase"),
        stat("Interview rate", a.interview_rate === null ? "—" : `${a.interview_rate}%`, a.interview_rate === null ? "Shown after 3+ applications" : `${a.interviews} interview(s)`, "users")),
      h("div", { class: "grid grid-2" },
        barChart({ title: "Jobs discovered per day (30 days)", data: a.per_day.map((d) => ({ label: day(d.day), value: d.discovered, note: `${d.qualifying} qualifying` })), valueLabel: "Jobs" }),
        barChart({ title: "Qualifying jobs per day (30 days)", data: a.per_day.map((d) => ({ label: day(d.day), value: d.qualifying })), valueLabel: "Qualifying jobs",
          emptyText: "No job has qualified yet in this period — that's a valid outcome, not missing data." }),
        barChart({ title: "Match score distribution", data: a.match_distribution.map((b) => ({ label: `${b.bucket}–${Math.min(100, b.bucket + 9)}`, value: b.n })), valueLabel: "Jobs" }),
        barChart({ title: "Jobs by outcome", horizontal: true, data: a.by_status.map((s) => ({ label: s.label, value: s.n })), valueLabel: "Jobs" }),
        barChart({ title: "Source quality (qualifying jobs per source)", horizontal: true, data: a.by_source.map((s) => ({ label: s.source, value: s.qualifying, note: `${s.total} processed, avg match ${s.avg_match ?? "—"}%` })), valueLabel: "Qualifying" }),
        barChart({ title: "Most common hard-gate failures", horizontal: true, data: a.gate_failures.map((g) => ({ label: g.label, value: g.n })), valueLabel: "Jobs" }),
        barChart({ title: "Application outcomes", horizontal: true, data: a.applications.map((s) => ({ label: fmt.cap(s.status), value: s.n })), valueLabel: "Applications" }),
        barChart({ title: "Rejection reasons (from your records)", horizontal: true, data: a.rejection_reasons.map((r) => ({ label: r.reason, value: r.n })), valueLabel: "Applications",
          emptyText: "No rejections recorded. Add a reason when you mark an application as rejected." }),
        barChart({ title: "AI calls per day (30 days)", data: a.ai_cost.map((d) => ({ label: day(d.day), value: d.calls, note: `₹${d.cost} · ${d.cached} cached` })), valueLabel: "Calls",
          emptyText: "No AI calls made. JobPilot works fully without AI." }),
        h("section", { class: "card card-pad stack" }, h("h3", null, "Cost this month"),
          h("div", { class: "value", style: { fontSize: "28px", fontWeight: 700 } }, fmt.inr(a.usage.month_cost_inr)),
          h("div", { class: "small text-2" }, `${a.usage.month_calls} AI calls · ${a.usage.month_tokens} tokens · cap ${fmt.inr(a.usage.monthly_cap_inr)}/month`),
          h("div", { class: "xs muted" }, "Costs are estimated from token counts using the per-token prices in your configuration (₹0 on free tiers)."))));
  };
  await load();
}
