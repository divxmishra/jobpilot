import { h, icon, mount, fmt } from "../ui/dom.js";
import { get, post } from "../api.js";
import { emptyState, errorState, loading, toast, withBusy, confirmDialog } from "../ui/components.js";
import { pageHead } from "./_common.js";

const TYPES = {
  job_shortlist: ["Job shortlist decisions", "bookmark"], job_verification: ["Requirements to verify", "help"],
  resume_approval: ["Tailored resume approvals", "file"], application_review: ["Prepared application reviews", "briefcase"],
  outreach_approval: ["Outreach drafts", "mail"],
};

export async function render(ctx) {
  ctx.setTitle("Approval Center");
  let filter = ctx.query.get("type") || "";
  const load = async () => {
    mount(ctx.root, loading(4));
    let r;
    try { r = await get("/api/approvals"); } catch (e) { mount(ctx.root, errorState(e, load)); return; }
    if (!ctx.isCurrent()) return;
    const items = filter ? r.items.filter((i) => i.type === filter) : r.items;
    const quick = (i) => {
      if (i.type === "job_shortlist") return [
        h("button", { class: "btn btn-sm", onclick: async (e) => withBusy(e.currentTarget, async () => { try { await post(`/api/jobs/${i.id}/decision`, { decision: "dismiss" }); toast("Dismissed", "info"); ctx.refreshCounts(); load(); } catch (er) { toast(er.message, "error"); } }) }, "Dismiss"),
        h("button", { class: "btn btn-sm btn-primary", onclick: async (e) => withBusy(e.currentTarget, async () => { try { await post(`/api/jobs/${i.id}/decision`, { decision: "shortlist" }); toast("Shortlisted", "success"); ctx.refreshCounts(); load(); } catch (er) { toast(er.message, "error"); } }) }, "Shortlist")];
      if (i.type === "resume_approval") return [h("a", { class: "btn btn-sm btn-primary", href: i.link }, "Review & approve")];
      return [h("a", { class: "btn btn-sm btn-primary", href: i.link }, "Review")];
    };
    mount(ctx.root,
      pageHead({ title: "Approval Center", desc: "Everything waiting for your decision. Each item says exactly what you're approving and what happens next. Approving never submits or sends anything." }),
      h("div", { class: "tabs", role: "tablist" },
        h("button", { role: "tab", "aria-selected": String(!filter), onclick: () => { filter = ""; load(); } }, "All", h("span", { class: "n" }, r.items.length)),
        Object.entries(TYPES).map(([k, [l]]) => h("button", { role: "tab", "aria-selected": String(filter === k), onclick: () => { filter = k; load(); } }, l, h("span", { class: "n" }, r.counts[k] || 0)))),
      items.length ? h("div", { class: "stack" }, items.map((i) => h("article", { class: "card card-pad" },
        h("div", { class: "row-between" }, h("div", { class: "row" }, h("span", { class: "badge b-brand" }, icon(TYPES[i.type][1], 12), TYPES[i.type][0].replace(/s$/, "")), h("strong", null, h("a", { href: i.link }, i.title))),
          h("span", { class: "xs muted" }, fmt.ago(i.created_at))),
        h("div", { class: "grid grid-2 mt-2" },
          h("div", { class: "small" }, h("div", { class: "xs muted" }, "What you're approving"), i.what),
          h("div", { class: "small" }, h("div", { class: "xs muted" }, "What happens next"), i.next)),
        h("div", { class: "row mt-2", style: { justifyContent: "flex-end" } }, quick(i)))))
        : h("div", { class: "card" }, emptyState({ icon: "checkCircle", title: "Nothing waiting for you", body: "New qualifying jobs, resume drafts, prepared applications and outreach drafts will appear here." })));
  };
  void confirmDialog;
  await load();
}
