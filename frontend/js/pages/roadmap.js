import { h, icon, mount, clear, fmt } from "../ui/dom.js";
import { get, put } from "../api.js";
import { callout, emptyState, errorState, loading, toast } from "../ui/components.js";
import { barChart } from "../ui/charts.js";
import { pageHead, section } from "./_common.js";

const STATUS = [["not_started", "Not started"], ["learning", "Learning"], ["practiced", "Practiced"], ["proficient", "Proficient"]];
const CLASS = { missing: ["Missing", "b-danger"], transferable: ["Partially transferable", "b-warning"], unverified: ["In resume, unverified", "b-info"] };

export async function render(ctx) {
  ctx.setTitle("Career Roadmap");
  let role = ctx.query.get("role") || "";
  const body = h("div");
  async function load() {
    mount(body, loading(4));
    let r, hist;
    try { [r, hist] = await Promise.all([get(`/api/roadmap?role=${encodeURIComponent(role)}`), get("/api/roadmap/history")]); }
    catch (e) {
      if (e.status === 402) { mount(body, h("div", { class: "upgrade-cta" }, h("div", null, h("strong", null, "Career Roadmap isn't included in your plan."), h("div", { class: "small" }, e.message)), h("a", { class: "btn btn-accent", href: "/app/subscription" }, "See plans"))); return; }
      mount(body, errorState(e, load)); return;
    }
    if (!ctx.isCurrent()) return;
    if (!r.has_profile) { mount(body, emptyState({ icon: "user", title: "Verify your profile first", body: "The roadmap compares your verified skills with the jobs you scan.", action: h("a", { class: "btn btn-primary", href: "/app/setup" }, "Set up profile") })); return; }
    if (!r.jobs_analyzed) { mount(body, emptyState({ icon: "search", title: "No scanned jobs yet", body: "Add or discover a few jobs. The roadmap is built only from your real job data — nothing is estimated without it.", action: h("a", { class: "btn btn-primary", href: "/app/discover" }, "Discover jobs") })); return; }

    const roleSel = h("select", { "aria-label": "Filter by target role", onchange: () => { role = roleSel.value; history.replaceState({}, "", role ? `?role=${encodeURIComponent(role)}` : location.pathname); load(); } },
      h("option", { value: "" }, "All recent jobs"), (r.target_roles || []).map((t) => h("option", { value: t, selected: role === t }, t)));
    const skills = r.skills;
    const statusSelect = (s) => {
      const sel = h("select", { "aria-label": `Learning status for ${s.skill}`, onchange: async () => {
        try { await put("/api/roadmap/skills", { skill: s.skill, status: sel.value }); toast(`${s.skill}: ${sel.selectedOptions[0].textContent}`, "success", { timeout: 1500 }); }
        catch (e) { toast(e.message, "error"); }
      } }, STATUS.map(([v, l]) => h("option", { value: v, selected: s.status === v }, l)));
      return sel;
    };
    const unlockNote = r.unlock.available
      ? (r.unlock.combined && r.unlock.combined.jobs ? callout("info", `Estimate: learning ${r.unlock.combined.skills.join(" and ")} could help you meet the threshold for up to ${r.unlock.combined.jobs} job(s) in your recent scan history — only where every other requirement already passes. Not a guarantee.`) : null)
      : callout("info", r.unlock.locked_reason || "Job-unlock estimates aren't available on your plan.");

    mount(body,
      h("div", { class: "row-between mb-4" }, h("div", { class: "small text-2" }, `Based on ${r.jobs_analyzed} job(s) — ${r.window} — analysed ${fmt.ago(r.generated_at)}.`),
        h("label", { class: "row small" }, "Target role", roleSel)),
      h("div", { class: "grid grid-4 mb-4" },
        h("div", { class: "card stat" }, h("span", { class: "label" }, icon("target", 15), "Skills to consider"), h("span", { class: "value" }, skills.length)),
        ...STATUS.slice(1).map(([k, l]) => h("div", { class: "card stat" }, h("span", { class: "label" }, l), h("span", { class: "value" }, r.status_counts[k] || 0)))),
      unlockNote ? h("div", { class: "mb-4" }, unlockNote) : null,
      skills.length ? h("div", { class: "split-wide" },
        section("Prioritised skills", h("div", null, skills.map((s, i) => h("div", { class: "skill-row" },
          h("span", { class: "skill-rank" }, i + 1),
          h("div", { class: "stack", style: { gap: "6px" } },
            h("div", { class: "row" }, h("strong", { style: { fontSize: "16px" } }, s.skill), h("span", { class: `badge prio-${s.priority}` }, `${s.priority} priority`),
              h("span", { class: `badge ${CLASS[s.classification][1]}` }, CLASS[s.classification][0]),
              s.learning_order ? h("span", { class: "badge b-outline" }, `Learn #${s.learning_order}`) : null),
            h("div", { class: "small" }, s.why), s.insight ? h("div", { class: "small text-accent" }, s.insight) : null,
            h("div", { class: "xs muted" }, `Mentioned in ${s.jobs_mentioning} job(s) · roles: ${s.relevant_roles.join(", ")}`
              + (s.transferable_via ? ` · related experience: ${s.transferable_via}` : "")
              + (typeof s.unlock_estimate === "number" ? ` · could help meet the threshold for up to ${s.unlock_estimate} job(s) (estimate)` : "")),
            h("details", null, h("summary", { class: "small" }, "Practice project & evidence"),
              h("div", { class: "small stack mt-2" }, h("div", null, h("strong", null, "Suggested project: "), s.project),
                h("div", null, h("strong", null, "Estimated effort: "), s.effort_estimate, h("span", { class: "muted" }, " (rough estimate, part-time)")),
                s.evidence.map((e) => h("div", { class: "xs muted" }, `${e.job}: “${e.quote}”`))))),
          h("div", null, statusSelect(s)))))),
        h("div", { class: "stack" },
          barChart({ title: "Skill frequency in your recent jobs", horizontal: true, data: skills.slice(0, 10).map((s) => ({ label: s.skill, value: s.jobs_mentioning, note: `${s.required_in} required` })), valueLabel: "Jobs" }),
          section("Suggested learning sequence", h("ol", { class: "evidence-list" }, skills.filter((s) => s.learning_order).sort((a, b) => a.learning_order - b.learning_order).map((s) => h("li", null, s.skill)))),
          barChart({ title: "Roadmap history (jobs analysed)", data: hist.map((x) => ({ label: x.at.slice(5, 10), value: x.jobs_analyzed, note: `${(x.data.status_counts || {}).proficient || 0} proficient` })), valueLabel: "Jobs",
            emptyText: "History builds up as you revisit the roadmap on different days." }),
          callout("info", "Marking a skill “Proficient” tracks your learning only. To use it in matching and resumes, add it to your profile with evidence through a profile update.")))
        : emptyState({ icon: "checkCircle", title: "No recurring gaps found", body: "Your verified skills cover the requirements of your recent jobs." }),
      h("p", { class: "xs muted mt-4" }, r.disclaimer));
  }
  mount(ctx.root, pageHead({ title: "Career Roadmap", desc: "Skills that keep appearing in jobs you don't yet qualify for — ranked by evidence from your own recent searches." }), body);
  await load();
  void clear;
}
