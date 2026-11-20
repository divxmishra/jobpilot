import { h, icon, mount, fmt } from "../ui/dom.js";
import { get, post, put, patch, del } from "../api.js";
import { badge, callout, confirmDialog, copyButton, dataTable, emptyState, errorState, loading, modal, toast, withBusy } from "../ui/components.js";
import { pageHead, section } from "./_common.js";

export async function render(ctx) {
  ctx.setTitle("Contacts & Outreach");
  const jobFilter = ctx.query.get("job") || "";
  let data, jobs = [];

  const load = async () => {
    mount(ctx.root, loading(4));
    try {
      [data, jobs] = await Promise.all([get(`/api/contacts?q=${encodeURIComponent(ctx.query.get("q") || "")}`),
        get("/api/jobs?view=all&page_size=100&sort=newest").then((r) => r.items)]);
    } catch (e) { mount(ctx.root, errorState(e, load)); return; }
    if (!ctx.isCurrent()) return;
    const kinds = data.kinds;
    mount(ctx.root,
      pageHead({ title: "Contacts & Outreach", desc: "Public professional contacts with their source and verification label, and message drafts you edit and approve. JobPilot never sends messages — you send them yourself.",
        actions: [h("button", { class: "btn", onclick: () => contactDialog() }, icon("plus", 16), "Add contact"), h("button", { class: "btn btn-primary", onclick: () => draftDialog() }, icon("mail", 16), "New draft")] }),
      h("div", { class: "mb-4" }, callout("info", "Only add people from credible public professional sources (company site, the job posting, a public profile). Never invent names, emails or relationships. Label how sure you are.")),
      h("div", { class: "split-wide" },
        section("Contacts", dataTable({
          rows: data.contacts, onRow: (c) => contactDialog(c),
          empty: emptyState({ icon: "users", title: "No contacts yet", body: "Use “Find public contacts in posting” on a job, or add a contact you found on an official source." }),
          columns: [
            { key: "name", label: "Name", render: (c) => h("div", null, h("strong", null, c.name), c.role ? h("div", { class: "xs muted" }, c.role) : null) },
            { key: "company", label: "Company" },
            { key: "verification", label: "Verification", render: (c) => badge(c.verification) },
            { key: "source_label", label: "Source", render: (c) => c.source_url ? h("a", { href: c.source_url, target: "_blank", rel: "noopener noreferrer", onclick: (e) => e.stopPropagation() }, c.source_label || "Link") : (c.source_label || "—") },
            { key: "job_title", label: "Job", render: (c) => c.job_id ? h("a", { href: `/jobs/${c.job_id}`, onclick: (e) => e.stopPropagation() }, c.job_title) : "—" },
          ] })),
        section("Message drafts", data.drafts.length ? h("div", { class: "stack" }, data.drafts.map((d) => h("button", { class: "card card-pad", style: { textAlign: "left", cursor: "pointer", font: "inherit", color: "inherit" }, onclick: () => draftEditor(d) },
          h("div", { class: "row-between" }, h("strong", null, kinds[d.kind]), badge(d.status === "draft" ? "draft" : d.status, d.status === "draft" ? "Draft — needs approval" : undefined)),
          h("div", { class: "small text-2" }, [d.contact_name, d.job_title && `${d.job_title} — ${d.job_company}`].filter(Boolean).join(" · ") || "General"),
          h("div", { class: "xs muted" }, `Updated ${fmt.ago(d.updated_at)}`))))
          : emptyState({ icon: "mail", title: "No drafts", body: "Create a referral request, recruiter email or connection message draft." }))));
    if (jobFilter) { draftDialog(jobFilter); history.replaceState({}, "", "/contacts"); }
    const openDraft = Number(ctx.query.get("draft"));
    if (openDraft) { const d = data.drafts.find((x) => x.id === openDraft); if (d) draftEditor(d); history.replaceState({}, "", "/contacts"); }
  };

  function contactDialog(c = {}) {
    const fields = {
      name: h("input", { type: "text", value: c.name || "", required: true }), role: h("input", { type: "text", value: c.role || "" }),
      company: h("input", { type: "text", value: c.company || "" }), email: h("input", { type: "email", value: c.email || "" }),
      profile_url: h("input", { type: "url", value: c.profile_url || "", placeholder: "https://" }), source_url: h("input", { type: "url", value: c.source_url || "", placeholder: "Where you found this person" }),
      source_label: h("input", { type: "text", value: c.source_label || "", placeholder: "e.g. Company team page" }),
      verification: h("select", null, Object.entries(data.verification_labels).map(([k, l]) => h("option", { value: k, selected: (c.verification || "public_unverified") === k }, l))),
      relationship: h("input", { type: "text", value: c.relationship || "", placeholder: "Only real connections, e.g. 'Same university, 2019'" }),
      job_id: h("select", null, h("option", { value: "" }, "Not linked"), jobs.map((j) => h("option", { value: j.id, selected: (c.job_id || jobFilter) === j.id }, `${j.title} — ${j.company}`))),
      notes: h("textarea", null),
    };
    fields.notes.value = c.notes || "";
    const labels = { name: "Name *", role: "Role", company: "Company", email: "Email", profile_url: "Public profile URL", source_url: "Source URL", source_label: "Source label",
      verification: "Verification", relationship: "Relationship / context", job_id: "Linked job", notes: "Notes" };
    const err = h("div", { role: "alert" });
    const save = h("button", { class: "btn btn-primary" }, "Save contact");
    const m = modal({ title: c.id ? "Edit contact" : "Add contact", wide: true,
      body: h("div", { class: "stack" }, h("div", { class: "form-grid" }, Object.entries(fields).map(([k, el]) => { el.id = `c-${k}`; return h("label", { class: `field ${k === "notes" ? "full" : ""}`, for: el.id }, labels[k], el); })),
        h("p", { class: "xs muted" }, "“Verified” requires a source URL. Use “Inferred” for anything you aren't sure about."), err),
      footer: [c.id ? h("button", { class: "btn btn-danger", onclick: async () => {
        if (await confirmDialog({ title: "Delete contact?", message: `Remove ${c.name}? Drafts addressed to them are kept but unlinked.`, confirmLabel: "Delete", danger: true })) {
          try { await del(`/api/contacts/${c.id}`); m.close(); toast("Contact deleted", "success"); load(); } catch (e) { toast(e.message, "error"); }
        } } }, "Delete") : null, h("span", { class: "grow" }), h("button", { class: "btn", onclick: () => m.close() }, "Cancel"), save] });
    save.addEventListener("click", () => withBusy(save, async () => {
      mount(err);
      const body = Object.fromEntries(Object.entries(fields).map(([k, el]) => [k, el.value.trim() || null]));
      body.verification = body.verification || "public_unverified";
      if (!body.name) { mount(err, h("span", { class: "error" }, "Name is required")); fields.name.setAttribute("aria-invalid", "true"); return; }
      try { c.id ? await put(`/api/contacts/${c.id}`, body) : await post("/api/contacts", body); m.close(); toast("Contact saved", "success"); load(); }
      catch (e) { mount(err, callout("danger", e.message)); }
    }));
  }

  function draftDialog(presetJob = "") {
    const kind = h("select", { id: "d-kind" }, Object.entries(data.kinds).map(([k, l]) => h("option", { value: k }, l)));
    const job = h("select", { id: "d-job" }, h("option", { value: "" }, "No specific job"), jobs.map((j) => h("option", { value: j.id, selected: presetJob === j.id }, `${j.title} — ${j.company}`)));
    const contact = h("select", { id: "d-contact" }, h("option", { value: "" }, "No specific contact"), data.contacts.map((c) => h("option", { value: c.id }, `${c.name}${c.company ? " · " + c.company : ""} (${data.verification_labels[c.verification]})`)));
    const go = h("button", { class: "btn btn-primary" }, "Create draft");
    const m = modal({ title: "New outreach draft", body: h("div", { class: "stack" },
      h("label", { class: "field", for: "d-kind" }, "Type", kind), h("label", { class: "field", for: "d-job" }, "Job", job), h("label", { class: "field", for: "d-contact" }, "Contact", contact),
      h("p", { class: "xs muted" }, "Drafts use only your verified profile facts and real contact details. At most one follow-up per contact and job.")),
      footer: [h("button", { class: "btn", onclick: () => m.close() }, "Cancel"), go] });
    go.addEventListener("click", () => withBusy(go, async () => {
      try { const d = await post("/api/outreach", { kind: kind.value, job_id: job.value || null, contact_id: contact.value ? Number(contact.value) : null }); m.close(); await load(); draftEditor(d); ctx.refreshCounts(); }
      catch (e) { toast(e.message, "error"); }
    }));
  }

  function draftEditor(d) {
    const subject = h("input", { type: "text", id: "o-subject", value: d.subject || "" });
    const body = h("textarea", { id: "o-body", rows: 12 }); body.value = d.body;
    const counter = h("span", { class: "xs muted", "aria-live": "polite" });
    const paintCount = () => { counter.textContent = d.kind === "connection" ? `${body.value.length} / 300 characters` : `${body.value.length} characters`; };
    body.addEventListener("input", paintCount); paintCount();
    const locked = d.status === "sent_by_user";
    if (locked) { subject.disabled = true; body.disabled = true; }
    const act = (action) => async (e) => withBusy(e.currentTarget, async () => {
      try { await patch(`/api/outreach/${d.id}`, { subject: subject.value || null, body: body.value, action }); toast(action === "approve" ? "Approved — copy and send it yourself" : action === "mark_sent" ? "Marked as sent by you" : "Saved", "success"); m.close(); load(); ctx.refreshCounts(); }
      catch (er) { toast(er.message, "error"); }
    });
    const mailto = d.contact_email && d.kind !== "connection" ? `mailto:${encodeURIComponent(d.contact_email)}?subject=${encodeURIComponent(subject.value || "")}&body=${encodeURIComponent(body.value)}` : null;
    const m = modal({ title: data.kinds[d.kind], wide: true,
      body: h("div", { class: "stack" },
        h("div", { class: "row" }, badge(d.status === "draft" ? "draft" : d.status, d.status === "draft" ? "Draft" : undefined), d.contact_name ? h("span", { class: "small" }, `To ${d.contact_name}`, " ", d.contact_verification ? badge(d.contact_verification) : null) : null),
        d.kind !== "connection" ? h("label", { class: "field", for: "o-subject" }, "Subject", subject) : null,
        h("label", { class: "field", for: "o-body" }, "Message", body, counter),
        callout("info", "Editing an approved draft returns it to draft status. JobPilot never sends: copy the text (or open your email app) and send it yourself, then mark it as sent.")),
      footer: [
        h("button", { class: "btn btn-danger", onclick: async () => { if (await confirmDialog({ title: "Delete draft?", message: "This draft will be deleted.", confirmLabel: "Delete", danger: true })) { try { await del(`/api/outreach/${d.id}`); m.close(); load(); ctx.refreshCounts(); } catch (e) { toast(e.message, "error"); } } } }, "Delete"),
        h("span", { class: "grow" }),
        copyButton(() => (subject.value ? `Subject: ${subject.value}\n\n` : "") + body.value),
        d.status === "approved" && mailto ? h("a", { class: "btn", href: mailto }, icon("mail", 14), "Open in my email app") : null,
        !locked ? h("button", { class: "btn", onclick: act(undefined) }, "Save") : null,
        d.status === "draft" ? h("button", { class: "btn btn-primary", onclick: act("approve") }, icon("check", 14), "Approve draft") : null,
        d.status === "approved" ? h("button", { class: "btn btn-primary", onclick: act("mark_sent") }, "I sent it — mark as sent") : null] });
  }

  await load();
}
