import { h, icon, mount, fmt } from "../ui/dom.js";
import { get, post, del, waitTask } from "../api.js";
import { badge, callout, confirmDialog, errorState, gatesList, loading, scoreBreakdown, taskProgress, toast, withBusy, jobNextAction } from "../ui/components.js";
import { backUrl, pageHead, section } from "./_common.js";

const SOURCE_TRUST = {
  greenhouse: "Official Greenhouse job-board API (employer-operated board)", lever: "Official Lever postings API (employer-operated board)",
  ashby: "Official Ashby job-board API (employer-operated board)", remotive: "Remotive public API (third-party aggregator)",
  career_page: "Fetched from the URL you provided", manual: "Entered manually by you",
};

export async function render(ctx) {
  const id = ctx.params.id;
  const load = async () => {
    mount(ctx.root, loading(5));
    let j;
    try { j = await get(`/api/jobs/${id}`); } catch (e) { mount(ctx.root, errorState(e, e.status === 404 ? null : load)); return; }
    if (!ctx.isCurrent()) return;
    ctx.setTitle(j.title || "Job");
    const m = j.match, req = j.requirements || {};
    const threshold = (await get("/api/preferences").catch(() => ({ match_threshold: 85 }))).match_threshold;
    const act = async (btn, fn, okMsg) => withBusy(btn, async () => { try { await fn(); if (okMsg) toast(okMsg, "success"); ctx.refreshCounts(); load(); } catch (e) { toast(e.message, "error"); } });

    const actions = [];
    if (j.status === "qualified") actions.push(h("button", { class: "btn btn-primary", onclick: (e) => act(e.currentTarget, () => post(`/api/jobs/${id}/decision`, { decision: "shortlist" }), "Shortlisted") }, icon("bookmark", 16), "Shortlist"));
    if (["qualified", "shortlisted"].includes(j.status)) actions.push(h("button", { class: j.status === "shortlisted" ? "btn btn-primary" : "btn", onclick: (e) => withBusy(e.currentTarget, async () => {
      try {
        const t = await post(`/api/jobs/${id}/resumes`);
        ctx.trackTask(t.id, "Generating tailored resume");
        const prog = taskProgress("Generating a truthful tailored resume…", { onCancel: () => post(`/api/tasks/${t.id}/cancel`) });
        ctx.root.prepend(prog.el);
        const done = await waitTask(t.id, prog.update);
        prog.el.remove();
        if (done.status === "succeeded") { toast(`Draft ready — estimated ATS ${done.result.ats_score}/100`, "success"); ctx.navigate(`/resumes/${done.result.resume_id}`); }
        else toast(done.error || `Generation ${done.status}`, done.status === "cancelled" ? "info" : "error");
      } catch (er) { toast(er.message, "error"); }
    }) }, icon("file", 16), "Generate tailored resume"));
    if (!["dismissed"].includes(j.status)) actions.push(h("button", { class: "btn", onclick: (e) => act(e.currentTarget, () => post(`/api/jobs/${id}/decision`, { decision: "dismiss" }), "Dismissed") }, icon("x", 16), "Dismiss"));
    if (j.decision) actions.push(h("button", { class: "btn btn-ghost", onclick: (e) => act(e.currentTarget, () => post(`/api/jobs/${id}/decision`, { decision: "reset" }), "Decision reset") }, "Undo decision"));

    const more = [
      j.source_url && j.source !== "manual" ? h("button", { class: "btn btn-sm", onclick: (e) => act(e.currentTarget, () => post(`/api/jobs/${id}/recheck`), "Status re-checked") }, icon("refresh", 14), "Re-check status") : null,
      h("button", { class: "btn btn-sm", onclick: (e) => act(e.currentTarget, () => post(`/api/jobs/${id}/evaluate`), "Re-evaluated") }, icon("target", 14), "Re-evaluate"),
      h("a", { class: "btn btn-sm", href: `/api/jobs/${id}/report.md`, target: "_blank", rel: "noopener" }, icon("file", 14), "report.md"),
      h("button", { class: "btn btn-sm btn-danger", onclick: async () => {
        if (await confirmDialog({ title: "Delete this job?", message: "The job, its reports and any tailored resumes and application records for it will be permanently deleted.", confirmLabel: "Delete job", danger: true })) {
          try { await del(`/api/jobs/${id}`); toast("Job deleted", "success"); ctx.navigate(backUrl("jobs", "/discover")); } catch (e) { toast(e.message, "error"); }
        } } }, icon("trash", 14), "Delete"),
    ];

    const onConfirm = async (g, btn) => {
      const ok = await confirmDialog({ title: "Confirm this requirement?", message: `"${g.label}": ${g.detail}. Only confirm if this is genuinely true for you. Your confirmation is recorded in the audit log.`, confirmLabel: "I confirm" });
      if (ok) act(btn, () => post(`/api/jobs/${id}/confirm-gate`, { key: g.key }), "Confirmed and re-evaluated");
    };

    const fact = (label, value) => h("div", null, h("div", { class: "xs muted" }, label), h("div", { class: "small", style: { fontWeight: 550 } }, value || "Not stated"));
    const tags = (arr, cls, render = (x) => x) => arr?.length ? h("div", null, arr.map((x) => h("span", { class: `tag ${cls}` }, render(x)))) : h("span", { class: "small muted" }, "None");
    const jdMissing = new Set((m?.missing_required || []).map((x) => x.skill.toLowerCase()));

    mount(ctx.root,
      pageHead({ title: j.title || "Untitled role", desc: `${j.company || "Unknown company"} · ${j.location || "Location not stated"}`,
        crumbs: [{ label: "Discover Jobs", href: backUrl("jobs", "/discover") }, { label: j.title || "Job" }], actions }),
      h("div", { class: "row mb-4" }, badge(j.status, j.status_label), badge(j.page_status), j.eligibility_result ? badge(j.eligibility_result, `Eligibility: ${fmt.cap(j.eligibility_result).replace("Needs verification", "needs verification")}`) : null,
        (j.alt_urls || []).length ? h("span", { class: "badge b-outline" }, icon("layers", 12), `Deduplicated across ${(j.alt_urls || []).length + 1} sources`) : null),
      h("div", { class: "mb-4" }, callout(j.status === "qualified" || j.status === "shortlisted" ? "success" : j.status === "needs_review" ? "warn" : j.status === "new" ? "info" : "danger",
        h("div", null, h("strong", null, "Decision: "), j.decision_reason || "Not evaluated yet.", h("div", { class: "small mt-2" }, h("strong", null, "Next: "), jobNextAction(j))))),
      h("div", { class: "split-wide" },
        h("div", { class: "stack" },
          section("Hard eligibility gates", h("div", null, h("p", { class: "small text-2" }, "Every gate must pass. A high match score can never override a failed gate."), gatesList(j.eligibility?.gates, { onConfirm })), { id: "gates" }),
          section("Resume match score", scoreBreakdown(m, threshold), { id: "score" }),
          section("Evidence", m ? h("div", { class: "stack" },
            h("h3", null, "Matched requirements"),
            m.matched_skills.length ? h("ul", { class: "evidence-list small" }, m.matched_skills.map((s) => h("li", null, h("strong", null, s.skill), " — ", s.resume_evidence.join("; "),
              h("div", { class: "xs muted" }, `JD: "${s.jd_evidence.slice(0, 160)}"`)))) : h("p", { class: "small muted" }, "No matching skills found."),
            m.transferable.length ? [h("h3", null, "Transferable (partial credit, not claimed)"), h("ul", { class: "evidence-list small" }, m.transferable.map((t) => h("li", null, h("strong", null, t.skill), ` — related experience with ${t.via}`)))] : null,
            h("h3", null, "Missing requirements"),
            h("div", null, h("div", { class: "xs muted mb-2" }, "Required"), tags(m.missing_required, "miss", (x) => x.skill)),
            h("div", null, h("div", { class: "xs muted mb-2" }, "Preferred"), tags(m.missing_preferred, "partial", (x) => x.skill)),
            h("h3", null, "Keywords"),
            h("div", null, h("div", { class: "xs muted mb-2" }, "Found in your verified profile"), tags(m.matched_keywords, "ok")),
            h("div", null, h("div", { class: "xs muted mb-2" }, "Not found"), tags(m.missing_keywords, ""))) : h("p", { class: "muted" }, "Not evaluated.")),
          section("Original job description", h("div", { class: "stack" },
            req.injection_markers?.length ? callout("warn", "This posting contains instruction-like text aimed at automated tools. It was treated as plain data and ignored.") : null,
            h("div", { class: "jd-pane prewrap" }, highlight(j.description || "No description extracted.", (m?.matched_skills || []).map((s) => s.skill), [...jdMissing]))), { id: "jd" })),
        h("div", { class: "stack" },
          section("Role summary", h("div", { class: "grid grid-2" },
            fact("Company", j.company), fact("Role", j.title), fact("Location", j.location), fact("Work mode", fmt.cap(j.work_mode)),
            fact("Employment type", j.employment_type), fact("Seniority", j.seniority), fact("Salary", j.salary_text || "Not stated (never estimated)"),
            fact("Experience required", req.experience?.min_years != null ? `${req.experience.min_years}${req.experience.max_years ? "–" + req.experience.max_years : "+"} years${req.experience.mandatory ? " (mandatory)" : " (preferred)"}` : "Not stated"),
            fact("Posted", j.posted_at ? fmt.date(j.posted_at) : "Not stated by source"), fact("Last verified", j.verified_at ? fmt.dateTime(j.verified_at) : "Never"),
            fact("Extracted", fmt.dateTime(j.extracted_at)), fact("Extraction", j.extraction_complete ? "Complete" : `Incomplete — missing ${(j.missing_fields || []).join(", ")}`))),
          section("Source & company verification", h("div", { class: "stack small" },
            h("div", null, h("div", { class: "xs muted" }, "Source"), SOURCE_TRUST[j.source] || j.source),
            h("div", null, h("div", { class: "xs muted" }, "Posting status"), badge(j.page_status), " ", j.page_status_detail || ""),
            h("div", null, h("div", { class: "xs muted" }, "Employer"), j.company ? (["greenhouse", "lever", "ashby"].includes(j.source) ? "Identified from the employer's own job board" : "Identified from the posting; not independently verified") : "Could not be identified — treat with caution"),
            j.source_url ? h("a", { href: j.source_url, target: "_blank", rel: "noopener noreferrer", class: "btn btn-sm", style: { alignSelf: "flex-start" } }, icon("external", 14), "Open original posting") : null,
            j.apply_url && j.apply_url !== j.source_url ? h("a", { href: j.apply_url, target: "_blank", rel: "noopener noreferrer" }, "Application page") : null,
            (j.alt_urls || []).length ? h("div", null, h("div", { class: "xs muted" }, "Also found at"), h("ul", { class: "evidence-list" }, j.alt_urls.map((u) => h("li", null, h("a", { href: u, target: "_blank", rel: "noopener noreferrer" }, u))))) : null)),
          section("Documents & application", h("div", { class: "stack small" },
            j.resumes.length ? h("ul", { class: "evidence-list" }, j.resumes.map((r) => h("li", null, h("a", { href: `/resumes/${r.id}` }, `Resume v${r.version}`), " ", badge(r.status), ` ATS ${r.ats_score}`))) : h("span", { class: "muted" },
              ["qualified", "shortlisted"].includes(j.status) ? "No tailored resume yet." : "Tailoring is available only for qualifying jobs (no tokens or effort spent on others)."),
            !j.application && j.resumes.some((r) => r.status === "approved") ? h("a", { class: "btn btn-sm btn-primary", href: `/applications?prepare=${id}`, style: { alignSelf: "flex-start" } }, icon("briefcase", 14), "Prepare application") : null,
            j.application ? h("div", null, "Application: ", h("a", { href: `/applications?open=${j.application.id}` }, "open"), " ", badge(j.application.status)) : null)),
          section("Contacts", h("div", { class: "stack small" },
            j.contacts.length ? j.contacts.map((c) => h("div", null, h("strong", null, c.name), " ", badge(c.verification), c.role ? h("div", { class: "xs muted" }, c.role) : null)) : h("span", { class: "muted" }, "No contacts linked."),
            h("div", { class: "row" }, h("button", { class: "btn btn-sm", onclick: (e) => withBusy(e.currentTarget, async () => { try { const r = await post(`/api/jobs/${id}/find-contacts`); toast(r.message, r.found.length ? "success" : "info"); load(); } catch (er) { toast(er.message, "error"); } }) },
              icon("search", 14), "Find public contacts in posting"), h("a", { class: "btn btn-sm", href: `/contacts?job=${id}` }, icon("plus", 14), "Add / draft outreach")))),
          section("More actions", h("div", { class: "row" }, more)))));
  };
  await load();
}

/** Highlight matched (green) and missing-required (red) skills in the JD, safely (text nodes only). */
function highlight(text, ok, miss) {
  const terms = [...ok.map((t) => [t, "kw-ok"]), ...miss.map((t) => [t, "kw-miss"])].filter(([t]) => t && t.length > 1);
  if (!terms.length) return text;
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`(?<![A-Za-z0-9+#])(${terms.map(([t]) => esc(t)).join("|")})(?![A-Za-z0-9+#])`, "gi");
  const cls = new Map(terms.map(([t, c]) => [t.toLowerCase(), c]));
  const out = []; let last = 0;
  for (const mm of text.matchAll(re)) {
    out.push(text.slice(last, mm.index));
    out.push(h("mark", { class: cls.get(mm[0].toLowerCase()) || "kw-ok", title: cls.get(mm[0].toLowerCase()) === "kw-miss" ? "Required, not in your verified profile" : "Matched in your verified profile" }, mm[0]));
    last = mm.index + mm[0].length;
  }
  out.push(text.slice(last));
  return out;
}
