import { h, icon, mount, clear, fmt } from "../ui/dom.js";
import { get } from "../api.js";
import { badge, emptyState, errorState, filterBar, loading, modal, pagination } from "../ui/components.js";
import { pageHead } from "./_common.js";

export async function render(ctx) {
  ctx.setTitle("Activity & Logs");
  const st = { page: 1, f: { q: ctx.query.get("q") || "", category: ctx.query.get("category") || "", level: ctx.query.get("level") || "" } };
  const listEl = h("div");
  let categories = [];
  const filterHost = h("div", { class: "mb-4" });

  const LINK = { job: (id) => `/app/jobs/${id}`, resume: (id) => `/app/resumes/${id}`, application: (id) => `/app/applications?open=${id}`, outreach: (id) => `/app/contacts?draft=${id}`, profile_version: () => "/app/setup" };

  async function load() {
    mount(listEl, loading(4));
    const p = new URLSearchParams({ page: st.page, page_size: 50, ...st.f });
    let r;
    try { r = await get(`/api/activity?${p}`); } catch (e) { mount(listEl, errorState(e, load)); return; }
    if (!ctx.isCurrent()) return;
    if (!categories.length) {
      categories = r.categories;
      mount(filterHost, filterBar({ values: st.f, onChange: (v) => { st.f = v; st.page = 1; load(); }, filters: [
        { key: "q", label: "Search", placeholder: "Message, action or source URL", grow: true, width: "240px" },
        { key: "category", label: "Category", type: "select", options: ["discovery", "extraction", "scoring", "document", "decision", "automation", "failure", "cost", "profile", "settings", "system"].map((c) => [c, fmt.cap(c)]) },
        { key: "level", label: "Level", type: "select", options: [["info", "Info"], ["warning", "Warning"], ["error", "Error"]] }] }));
    }
    if (!r.items.length) { mount(listEl, h("div", { class: "card" }, emptyState({ icon: "activity", title: "No matching events", body: "Every discovery, extraction, scoring, document, decision, automation, failure and cost event is recorded here." }))); return; }
    mount(listEl, h("div", { class: "card" }, h("ul", { class: "timeline", style: { padding: "0 20px" } }, r.items.map((e) => h("li", null,
      h("span", { class: "when", title: e.at }, fmt.dateTime(e.at)),
      h("div", null,
        h("div", { class: "row", style: { gap: "6px" } },
          h("span", { class: "badge b-outline" }, fmt.cap(e.category)),
          e.level === "error" ? badge("error") : e.level === "warning" ? h("span", { class: "badge b-warning" }, icon("alert", 12), "Warning") : null,
          h("span", null, e.message)),
        h("div", { class: "row xs muted", style: { gap: "10px", marginTop: "2px" } },
          h("span", null, e.action),
          e.entity_type && LINK[e.entity_type] ? h("a", { href: LINK[e.entity_type](e.entity_id) }, `Open ${e.entity_type.replace("_", " ")}`) : null,
          e.source_ref ? h("a", { href: e.source_ref, target: "_blank", rel: "noopener noreferrer" }, icon("external", 11), " Source") : null,
          e.details ? h("button", { class: "btn btn-ghost btn-sm", style: { minHeight: "22px", padding: "0 6px" }, onclick: () => modal({ title: "Event details",
            body: h("pre", { class: "prewrap", style: { background: "var(--surface-2)", padding: "12px", borderRadius: "8px", maxHeight: "60vh", overflow: "auto" } }, JSON.stringify(e.details, null, 2)) }) }, "Details") : null)))))),
      pagination({ page: st.page, pageSize: 50, total: r.total, onPage: (p2) => { st.page = p2; load(); } }));
  }

  mount(ctx.root, pageHead({ title: "Activity & Logs", desc: "Searchable chronological audit trail. A human-readable copy is also written to data/audit/YYYY-MM-DD.jsonl. Secrets are always redacted." }), filterHost, listEl);
  void clear;
  await load();
}
