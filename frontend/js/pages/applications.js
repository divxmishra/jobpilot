import { h, icon, mount, clear, fmt } from "../ui/dom.js";
import { get, post, patch, del } from "../api.js";
import { badge, callout, confirmDialog, copyButton, dataTable, emptyState, errorState, loading, modal, toast, withBusy } from "../ui/components.js";
import { pageHead } from "./_common.js";

export async function render(ctx) {
  ctx.setTitle("Applications");
  const st = { status: ctx.query.get("status") || "", q: ctx.query.get("q") || "", sort: "updated" };
  const listEl = h("div");
  const tabsEl = h("div", { class: "tabs", role: "tablist", "aria-label": "Application status" });

  async function loadList() {
    mount(listEl, loading(3));
    let r;
    try { r = await get(`/api/applications?status=${st.status}&q=${encodeURIComponent(st.q)}&sort=${st.sort}`); } catch (e) { mount(listEl, errorState(e, loadList)); return; }
    if (!ctx.isCurrent()) return;
    clear(tabsEl);
    const total = Object.values(r.counts).reduce((a, b) => a + b, 0);
    tabsEl.append(h("button", { role: "tab", "aria-selected": String(!st.status), onclick: () => { st.status = ""; loadList(); } }, "All", h("span", { class: "n" }, total)));
    for (const [k, label] of Object.entries(r.statuses)) tabsEl.append(h("button", { role: "tab", "aria-selected": String(st.status === k), onclick: () => { st.status = k; loadList(); } }, label, h("span", { class: "n" }, r.counts[k] || 0)));
    mount(listEl, h("div", { class: "card" }, dataTable({
      rows: r.items, onRow: (a) => openDrawer(a.id),
      empty: emptyState({ icon: "briefcase", title: st.status || st.q ? "No applications match" : "No applications yet",
        body: "Prepare an application from a shortlisted job once its tailored resume is approved. JobPilot never submits anything for you." }),
      columns: [
        { key: "company", label: "Company" },
        { key: "title", label: "Role", render: (a) => h("a", { href: `/jobs/${a.job_id}`, onclick: (e) => e.stopPropagation() }, a.title) },
        { key: "status", label: "Status", render: (a) => badge(a.status) },
        { key: "source", label: "Source" },
        { key: "created_at", label: "Prepared", render: (a) => fmt.date(a.created_at) },
        { key: "submitted_at", label: "Submitted", render: (a) => a.submitted_at ? fmt.date(a.submitted_at) : "—" },
        { key: "next_action", label: "Next action", render: (a) => h("span", null, a.next_action || "—", a.next_action_date ? h("div", { class: "xs muted" }, `by ${fmt.date(a.next_action_date)}`) : null) },
      ] })));
  }

  async function openDrawer(aid) {
    let a, browser;
    try { [a, browser] = await Promise.all([get(`/api/applications/${aid}`), get("/api/browser/status")]); } catch (e) { toast(e.message, "error"); return; }
    history.replaceState({}, "", `/applications?open=${aid}`);
    const answers = structuredClone(a.answers || []);
    const statusSel = h("select", { id: "app-status" }, ["prepared", "applied", "interview", "offer", "rejected", "withdrawn"].map((s) => h("option", { value: s, selected: a.status === s },
      { prepared: "Prepared", applied: "Applied (I submitted it myself)", interview: "Interview", offer: "Offer", rejected: "Rejected", withdrawn: "Withdrawn" }[s])));
    const nextAction = h("input", { type: "text", id: "app-next", value: a.next_action || "" });
    const nextDate = h("input", { type: "date", id: "app-next-date", value: a.next_action_date || "" });
    const notes = h("textarea", { id: "app-notes" }); notes.value = a.notes || "";
    const reason = h("input", { type: "text", id: "app-reason", value: a.rejection_reason || "", placeholder: "If known, e.g. 'Position filled'" });
    const reviewed = h("input", { type: "checkbox", id: "app-reviewed", checked: !!a.reviewed });
    const prefillEl = h("div", { class: "stack" });
    const answerEls = answers.map((x, i) => {
      const ta = h("textarea", { rows: 2, "aria-label": x.question, oninput: () => { x.answer = ta.value; x.edited = true; } }); ta.value = x.answer;
      return h("div", { class: "field" }, h("div", { class: "row-between" }, h("span", null, x.question), copyButton(() => ta.value, "Copy")),
        h("span", { class: "hint" }, x.source), ta);
    });
    const saveBtn = h("button", { class: "btn btn-primary" }, "Save changes");
    const m = modal({ title: `${a.title} — ${a.company}`, drawer: true, onClose: () => history.replaceState({}, "", "/applications"),
      body: h("div", { class: "stack" },
        h("div", { class: "row" }, badge(a.status), h("a", { href: `/jobs/${a.job_id}` }, "Job details"), a.resume_id ? h("a", { href: `/resumes/${a.resume_id}` }, `Resume v${a.resume_version} (ATS ${a.ats_score})`) : null),
        callout("info", "JobPilot prepares and prefills only. It never clicks Submit, Apply, Send or Confirm. You review and submit personally, then record the status here."),
        h("fieldset", null, h("legend", null, "1. Review prepared data"),
          h("div", { class: "stack" },
            h("div", { class: "small" }, h("strong", null, "Job: "), `${a.title} at ${a.company} (${a.location || "location not stated"})`),
            h("div", { class: "small" }, h("strong", null, "Selected resume: "), a.resume_id ? h("span", null, `v${a.resume_version} — `, h("a", { href: `/api/resumes/${a.resume_id}/download?format=pdf`, download: "" }, "PDF"), " · ",
              h("a", { href: `/api/resumes/${a.resume_id}/download?format=docx`, download: "" }, "DOCX")) : "none"),
            h("div", { class: "small" }, h("strong", null, "Prepared answers"), " (drafts from verified facts; placeholders in [brackets] need your input):"),
            answerEls,
            h("label", { class: "check", for: "app-reviewed" }, reviewed, "I have reviewed the job, the selected resume and every prepared answer"))),
        h("fieldset", null, h("legend", null, "2. Open the application page"), prefillEl),
        h("fieldset", null, h("legend", null, "3. Track status"),
          h("div", { class: "form-grid" },
            h("label", { class: "field", for: "app-status" }, "Status", statusSel),
            h("label", { class: "field", for: "app-next-date" }, "Next action date", nextDate),
            h("label", { class: "field full", for: "app-next" }, "Next action", nextAction),
            h("label", { class: "field full", for: "app-reason" }, "Rejection reason (if rejected)", reason),
            h("label", { class: "field full", for: "app-notes" }, "Notes", notes))),
        a.history?.length ? h("div", null, h("h3", { class: "mb-2" }, "History"), h("ul", { class: "timeline" }, a.history.map((x) => h("li", null, h("span", { class: "when" }, fmt.dateTime(x.at)),
          h("span", null, x.from_status ? `${x.from_status} → ` : "", h("strong", null, x.to_status), x.note ? ` — ${x.note}` : ""))))) : null),
      footer: [
        h("button", { class: "btn btn-danger", onclick: async () => {
          if (await confirmDialog({ title: "Delete this application record?", message: "This removes JobPilot's tracking record only. It does not withdraw anything you submitted to the employer.", confirmLabel: "Delete record", danger: true })) {
            try { await del(`/api/applications/${aid}`); m.close(); toast("Record deleted", "success"); loadList(); ctx.refreshCounts(); } catch (e) { toast(e.message, "error"); }
          } } }, "Delete"),
        h("span", { class: "grow" }), h("button", { class: "btn", onclick: () => m.close() }, "Close"), saveBtn] });

    const save = async () => {
      const body = { status: statusSel.value, next_action: nextAction.value, next_action_date: nextDate.value, notes: notes.value,
        rejection_reason: reason.value, reviewed: reviewed.checked, answers };
      if (body.status === "applied" && a.status !== "applied") {
        const ok = await confirmDialog({ title: "Mark as applied?", message: "Confirm that you personally submitted this application on the employer's site.", confirmLabel: "Yes, I submitted it" });
        if (!ok) return null;
      }
      return patch(`/api/applications/${aid}`, body);
    };
    saveBtn.addEventListener("click", () => withBusy(saveBtn, async () => {
      try { const r = await save(); if (r) { a = r; toast("Application updated", "success"); loadList(); ctx.refreshCounts(); renderPrefill(); } } catch (e) { toast(e.message, "error"); }
    }));
    reviewed.addEventListener("change", async () => {
      try { a = await patch(`/api/applications/${aid}`, { reviewed: reviewed.checked, answers }); renderPrefill(); ctx.refreshCounts(); } catch (e) { toast(e.message, "error"); reviewed.checked = !reviewed.checked; }
    });

    let poll;
    function renderPrefill(log) {
      clear(prefillEl);
      const url = a.apply_url || a.source_url;
      if (!a.reviewed) { prefillEl.append(callout("warn", "Tick the review confirmation above first. JobPilot shows you the job, resume and prepared data before opening the submission workflow.")); return; }
      if (browser.available) {
        prefillEl.append(h("p", { class: "small text-2" }, "Opens the page in a separate browser window using your persistent local browser profile. Sign in yourself if asked — JobPilot never asks for or stores portal passwords. Supported fields (name, email, phone, links, resume file) are filled; password, payment and CAPTCHA fields are never touched."),
          h("button", { class: "btn btn-primary", style: { alignSelf: "flex-start" }, onclick: (e) => withBusy(e.currentTarget, async () => {
            try { await post(`/api/applications/${aid}/launch-browser`); toast("Browser opening — prefilling supported fields", "info"); pollPrefill(); } catch (er) { toast(er.message, "error"); }
          }) }, icon("external", 16), "Open & prefill in local browser"));
      } else {
        prefillEl.append(callout("info", h("span", null, h("strong", null, "Manual workflow. "), browser.message)));
      }
      prefillEl.append(h("div", { class: "row" }, url ? h("a", { class: "btn", href: url, target: "_blank", rel: "noopener noreferrer" }, icon("external", 16), "Open application page") : null,
        copyButton(() => answers.map((x) => `${x.question}: ${x.answer}`).join("\n"), "Copy all answers")));
      const L = log || a.prefill_log;
      if (L && L.status && L.status !== "not_started") {
        const filled = (L.filled || []).filter((f) => f.action === "filled" || f.action === "attached");
        prefillEl.append(callout(L.status === "failed" ? "danger" : L.status === "paused_for_user" ? "success" : "info", h("div", null,
          h("strong", null, { launching: "Launching browser…", starting: "Starting…", prefilling: "Prefilling…", paused_for_user: "Paused before submission", browser_closed: "Browser closed", failed: "Browser preparation failed" }[L.status] || L.status),
          L.message ? h("div", { class: "small" }, L.message) : null, L.error ? h("div", { class: "small" }, L.error) : null,
          filled.length ? h("div", { class: "small mt-2" }, h("strong", null, "What was filled:"), h("ul", { class: "evidence-list" }, filled.map((f) => h("li", null, `${f.field} → ${f.value}`)))) : null,
          (L.filled || []).some((f) => f.action === "skipped") ? h("div", { class: "xs muted" }, `${(L.filled || []).filter((f) => f.action === "skipped").length} field(s) skipped (sensitive or already filled)`) : null)));
      }
      prefillEl.append(h("button", { class: "btn", style: { alignSelf: "flex-start" }, disabled: a.status !== "prepared", onclick: async () => {
        statusSel.value = "applied"; saveBtn.click();
      } }, icon("check", 16), "I submitted it myself — mark as Applied"));
    }
    function pollPrefill() {
      clearTimeout(poll);
      const tick = async () => {
        if (!m.el.isConnected) return;
        try { const L = await get(`/api/applications/${aid}/prefill`); renderPrefill(L); if (["starting", "launching", "prefilling"].includes(L.status)) poll = setTimeout(tick, 1500); } catch { /* ignore */ }
      };
      tick();
    }
    renderPrefill();
    if (["launching", "starting", "prefilling"].includes(a.prefill_log?.status)) pollPrefill();
  }

  const search = h("input", { type: "search", placeholder: "Search company or role", "aria-label": "Search applications", value: st.q });
  let t; search.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { st.q = search.value.trim(); loadList(); }, 300); });
  const sortSel = h("select", { "aria-label": "Sort applications", onchange: () => { st.sort = sortSel.value; loadList(); } },
    [["updated", "Recently updated"], ["created", "Recently prepared"], ["next", "Next action date"], ["company", "Company A–Z"]].map(([v, l]) => h("option", { value: v }, l)));

  mount(ctx.root, pageHead({ title: "Applications", desc: "Your application pipeline. Statuses are updated by you — “Applied” always means you submitted it personally." }),
    tabsEl, h("div", { class: "row mb-4" }, h("div", { class: "grow", style: { maxWidth: "380px" } }, search), h("label", { class: "row small" }, "Sort", sortSel)), listEl);
  await loadList();

  const prep = ctx.query.get("prepare");
  if (prep) {
    try { const a = await post("/api/applications", { job_id: prep }); toast("Application prepared — review it now", "success"); loadList(); ctx.refreshCounts(); openDrawer(a.id); }
    catch (e) { toast(e.message, "error"); history.replaceState({}, "", "/applications"); }
  } else if (ctx.query.get("open")) openDrawer(Number(ctx.query.get("open")));
}
