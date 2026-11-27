import { h, icon, mount, fmt } from "../ui/dom.js";
import { get, post } from "../api.js";
import { badge, callout, emptyState, errorState, loading, modal, toast, withBusy } from "../ui/components.js";
import { pageHead, section } from "./_common.js";

export async function render(ctx) {
  ctx.setTitle("Support");
  const listEl = h("div");
  async function load() {
    mount(listEl, loading(2));
    let rows;
    try { rows = await get("/api/support/tickets"); } catch (e) { mount(listEl, errorState(e, load)); return; }
    mount(listEl, rows.length ? h("div", { class: "stack" }, rows.map((t) => h("button", { class: "card card-pad", style: { textAlign: "left", cursor: "pointer", font: "inherit", color: "inherit" }, onclick: () => open(t.id) },
      h("div", { class: "row-between" }, h("strong", null, t.subject), badge(t.status === "answered" ? "pass" : t.status === "closed" ? "superseded" : "needs_verification", { open: "Open", answered: "Answered", closed: "Closed" }[t.status])),
      h("div", { class: "xs muted" }, `Updated ${fmt.ago(t.updated_at)}`)))) : emptyState({ icon: "help", title: "No support requests", body: "Ask a question or report a problem and the JobPilot team will reply here." }));
  }
  async function open(id) {
    const t = await get(`/api/support/tickets/${id}`);
    const msg = h("textarea", { rows: 3, "aria-label": "Reply" });
    const send = h("button", { class: "btn btn-primary" }, "Send reply");
    const m = modal({ title: t.subject, wide: true, body: h("div", { class: "stack" },
      t.messages.map((x) => h("div", { class: "card card-pad", style: { background: x.from_staff ? "var(--brand-soft)" : "var(--surface)" } },
        h("div", { class: "xs muted mb-2" }, `${x.from_staff ? "JobPilot support" : "You"} · ${fmt.dateTime(x.created_at)}`), h("div", { class: "prewrap small" }, x.body))),
      t.status !== "closed" ? h("label", { class: "field" }, "Reply", msg) : callout("info", "This ticket is closed.")),
      footer: [h("button", { class: "btn", onclick: () => m.close() }, "Close"), t.status !== "closed" ? send : null] });
    send.addEventListener("click", () => withBusy(send, async () => {
      if (!msg.value.trim()) return;
      try { await post(`/api/support/tickets/${id}/messages`, { message: msg.value.trim() }); m.close(); toast("Reply sent", "success"); load(); } catch (e) { toast(e.message, "error"); }
    }));
  }
  const subject = h("input", { type: "text", id: "s-subject" });
  const message = h("textarea", { id: "s-msg", rows: 5 });
  const btn = h("button", { class: "btn btn-primary" }, icon("mail", 16), "Send request");
  const out = h("div", { role: "alert" });
  btn.addEventListener("click", () => withBusy(btn, async () => {
    try { await post("/api/support/tickets", { subject: subject.value.trim(), message: message.value.trim() }); subject.value = ""; message.value = ""; mount(out); toast("Request sent — we'll reply here", "success"); load(); }
    catch (e) { mount(out, callout("danger", e.message)); }
  }));
  mount(ctx.root, pageHead({ title: "Support", desc: "Questions, problems or feedback. Never include passwords or full resumes in messages." }),
    h("div", { class: "split" }, section("New request", h("div", { class: "stack" }, h("label", { class: "field", for: "s-subject" }, "Subject", subject),
      h("label", { class: "field", for: "s-msg" }, "Message", message), out, h("div", null, btn))), section("Your requests", listEl)));
  await load();
}
