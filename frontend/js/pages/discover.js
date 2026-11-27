import { h, icon, mount, clear, fmt } from "../ui/dom.js";
import { get, post, waitTask } from "../api.js";
import { callout, emptyState, errorState, filterBar, jobCard, loading, modal, pagination, taskProgress, toast, withBusy } from "../ui/components.js";
import { pageHead, rememberListUrl } from "./_common.js";

const VIEWS = [
  ["recommended", "Recommended", "Passed every hard gate and met your match threshold."],
  ["needs_review", "Pending verification", "Could qualify, but something needs your confirmation first."],
  ["near_match", "Near match", "All gates passed, but the match is below your threshold. Not recommended."],
  ["rejected", "Rejected", "Failed a hard requirement, scored well below threshold, or dismissed by you."],
  ["expired", "Expired / closed", "Postings that are closed, expired or removed."],
  ["all", "All jobs", "Everything JobPilot has processed."],
];

export async function render(ctx, { fixedView } = {}) {
  ctx.setTitle(fixedView ? "Saved / Shortlisted" : "Discover Jobs");
  const q = ctx.query;
  const st = {
    view: fixedView || q.get("view") || "recommended", page: Number(q.get("page") || 1), sort: q.get("sort") || "match",
    f: { q: q.get("q") || "", source: q.get("source") || "", work_mode: q.get("work_mode") || "", employment_type: q.get("employment_type") || "",
      location: q.get("location") || "", posted_within: q.get("posted_within") || "", min_salary: q.get("min_salary") || "", max_experience: q.get("max_experience") || "" },
  };
  const listEl = h("div");
  const tabsEl = h("div", { class: "tabs", role: "tablist", "aria-label": "Job views" });
  const descEl = h("p", { class: "small text-2 mb-2" });
  let sources = [];

  function syncUrl() {
    const p = new URLSearchParams();
    if (!fixedView && st.view !== "recommended") p.set("view", st.view);
    if (st.page > 1) p.set("page", st.page);
    if (st.sort !== "match") p.set("sort", st.sort);
    for (const [k, v] of Object.entries(st.f)) if (v) p.set(k, v);
    history.replaceState({}, "", location.pathname + (p.toString() ? `?${p}` : ""));
    rememberListUrl("jobs");
  }

  async function loadList() {
    syncUrl();
    mount(listEl, loading(3));
    const p = new URLSearchParams({ view: st.view, page: st.page, page_size: 15, sort: st.sort });
    for (const [k, v] of Object.entries(st.f)) if (v) p.set(k, v);
    let r;
    try { r = await get(`/api/jobs?${p}`); } catch (e) { mount(listEl, errorState(e, loadList)); return; }
    if (!ctx.isCurrent()) return;
    sources = r.sources;
    if (!fixedView) {
      clear(tabsEl);
      for (const [key, label] of VIEWS) {
        tabsEl.append(h("button", { role: "tab", "aria-selected": String(st.view === key), onclick: () => { st.view = key; st.page = 1; loadList(); } },
          label, h("span", { class: "n" }, r.counts[key] ?? 0)));
      }
      descEl.textContent = VIEWS.find((v) => v[0] === st.view)[2];
    }
    const items = r.items;
    if (!items.length) {
      const filtersOn = Object.values(st.f).some(Boolean);
      mount(listEl, h("div", { class: "card" }, filtersOn
        ? emptyState({ icon: "filter", title: "No jobs match these filters", body: "Clear or change filters to see more." })
        : st.view === "recommended" || fixedView
          ? emptyState({ icon: "target", title: fixedView ? "No shortlisted jobs" : "No qualifying jobs yet",
            body: fixedView ? "Shortlist a recommended job to start tailoring a resume." : "Zero is a valid result — JobPilot never lowers your criteria to fill this list. Paste a job URL above or run discovery." })
          : emptyState({ title: "Nothing in this view" })));
      return;
    }
    mount(listEl, h("div", { class: "job-list" }, items.map((j) => {
      const card = jobCard(j, { threshold: r.threshold, navigate: ctx.navigate });
      if (["near_match", "rejected", "needs_review", "expired"].includes(st.view) || (st.view === "all" && !["qualified", "shortlisted"].includes(j.status))) {
        card.querySelector("div").append(h("div", { class: "small text-2 mt-2" }, icon("info", 13), " Why: ", j.decision_reason || "—"));
      }
      return card;
    })), pagination({ page: st.page, pageSize: 15, total: r.total, onPage: (pg) => { st.page = pg; loadList(); window.scrollTo(0, 0); } }));
  }

  // ---------------- add by URL
  const urlInput = h("input", { type: "url", id: "job-url", placeholder: "https://boards.greenhouse.io/company/jobs/123…", required: true, autocomplete: "off" });
  const urlErr = h("div", { class: "error", role: "alert" });
  const urlStatus = h("div");
  const urlBtn = h("button", { class: "btn btn-primary", type: "submit" }, icon("plus", 16), "Check job");
  const urlForm = h("form", { class: "stack", novalidate: true, onsubmit: async (e) => {
    e.preventDefault(); urlErr.textContent = "";
    const v = urlInput.value.trim();
    if (!/^https?:\/\/.+\..+/.test(v)) { urlErr.textContent = "Enter a full job URL starting with https://"; urlInput.setAttribute("aria-invalid", "true"); return; }
    urlInput.removeAttribute("aria-invalid");
    await withBusy(urlBtn, async () => {
      try {
        const t = await post("/api/jobs/url", { url: v });
        ctx.trackTask(t.id, "Processing job URL");
        const prog = taskProgress("Fetching and validating the posting…");
        mount(urlStatus, prog.el);
        const done = await waitTask(t.id, prog.update);
        if (done.status !== "succeeded") throw new Error(done.error || "Processing failed");
        const res = done.result;
        urlInput.value = "";
        mount(urlStatus, callout(res.status === "qualified" ? "success" : "info", h("div", { class: "row-between" },
          h("span", null, res.duplicate ? "Already known (duplicate) — updated its sources. " : "Processed. ", "Result: ", h("strong", null, res.status_label)),
          h("a", { class: "btn btn-sm", href: `/app/jobs/${res.job_id}` }, "Open job"))));
        loadList();
      } catch (er) { mount(urlStatus, callout("danger", `${er.message} You can enter the job details manually instead.`)); }
    });
  } },
  h("label", { class: "field", for: "job-url" }, "Paste a job posting URL", h("span", { class: "hint" }, "Processed through the same validation, eligibility and scoring pipeline as discovered jobs"),
    h("div", { class: "row" }, h("div", { class: "grow" }, urlInput), urlBtn)), urlErr, urlStatus,
  h("button", { class: "btn btn-ghost btn-sm", type: "button", style: { alignSelf: "flex-start" }, onclick: manualDialog }, icon("edit", 14), "Site blocks automation? Enter job details manually"));

  function manualDialog() {
    const f = {};
    const field = (key, label, el, hint) => { f[key] = el; el.id = `m-${key}`; return h("label", { class: `field ${key === "description" ? "full" : ""}`, for: el.id }, label, hint ? h("span", { class: "hint" }, hint) : null, el, h("span", { class: "error", id: `m-${key}-err` })); };
    const form = h("form", { class: "form-grid" },
      field("title", "Job title *", h("input", { type: "text", required: true })),
      field("company", "Company *", h("input", { type: "text", required: true })),
      field("location", "Location", h("input", { type: "text" })),
      field("url", "Job URL", h("input", { type: "url", placeholder: "https://" })),
      field("employment_type", "Employment type", h("select", null, ["", "full-time", "part-time", "contract", "internship", "temporary"].map((v) => h("option", { value: v }, v || "Not stated")))),
      field("posted_at", "Posting date (if shown)", h("input", { type: "date" })),
      field("description", "Full job description *", h("textarea", { rows: 10, required: true }), "Paste the complete text, including requirements"));
    const submit = h("button", { class: "btn btn-primary" }, "Add and evaluate");
    const m = modal({ title: "Enter job details manually", wide: true, body: h("div", { class: "stack" },
      callout("info", "Use this when a site disallows automated access. JobPilot can't independently verify the posting is open, so you'll be asked to confirm it."), form),
      footer: [h("button", { class: "btn", onclick: () => m.close() }, "Cancel"), submit] });
    submit.addEventListener("click", () => withBusy(submit, async () => {
      form.querySelectorAll(".error").forEach((e) => (e.textContent = ""));
      const body = Object.fromEntries(Object.entries(f).map(([k, el]) => [k, el.value.trim() || null]));
      let bad = false;
      for (const [k, min] of [["title", 2], ["company", 1], ["description", 50]]) {
        if (!body[k] || body[k].length < min) { document.getElementById(`m-${k}-err`).textContent = k === "description" ? "Paste at least 50 characters of the description" : "Required"; f[k].setAttribute("aria-invalid", "true"); bad = true; }
      }
      if (bad) return;
      try {
        const r = await post("/api/jobs/manual", body);
        m.close(); toast(r.duplicate ? "This job was already known" : `Job added: ${r.job.status_label}`, "success");
        ctx.navigate(`/app/jobs/${r.job.id}`);
      } catch (er) { toast(er.message, "error"); }
    }));
  }

  // ---------------- discovery run
  const runStatus = h("div", { class: "stack" });
  const runBtn = h("button", { class: "btn" }, icon("refresh", 16), "Run discovery now");
  async function loadRuns() {
    try {
      const r = await get("/api/discovery/runs");
      const last = r.runs[0];
      mount(runStatus,
        h("div", { class: "small text-2" }, r.schedule.enabled ? `Scheduled daily at ${String(r.schedule.hour).padStart(2, "0")}:00 (while JobPilot is running).` : "Scheduled discovery is off.",
          " ", h("a", { href: "/app/settings#sources" }, "Manage sources")),
        last ? h("div", { class: "small" }, `Last run ${fmt.ago(last.started_at)} (${last.trigger}): `, h("strong", null, last.status), last.stats ? ` · ${last.stats.listed ?? 0} listed, ${last.stats.new ?? 0} new, ${last.stats.qualified ?? 0} newly qualifying, ${last.stats.duplicates ?? 0} duplicates` : "",
          last.stats?.errors?.length ? h("div", { class: "xs", style: { color: "var(--danger)" } }, `${last.stats.errors.length} source error(s) — see Activity & Logs`) : null) : h("div", { class: "small muted" }, "Discovery has never run."));
      if (r.running) follow(r.running.id);
    } catch (e) { mount(runStatus, callout("danger", e.message)); }
  }
  async function follow(id) {
    runBtn.disabled = true;
    const prog = taskProgress("Discovery running…", { onCancel: async () => { await post(`/api/tasks/${id}/cancel`); toast("Cancelling after the current step…", "info"); } });
    runStatus.prepend(prog.el);
    try {
      const t = await waitTask(id, prog.update, { interval: 1500 });
      toast(t.status === "succeeded" ? "Discovery finished" : `Discovery ${t.status}`, t.status === "succeeded" ? "success" : "warning");
    } catch (e) { toast(e.message, "error"); }
    runBtn.disabled = false;
    if (ctx.isCurrent()) { loadRuns(); loadList(); }
  }
  runBtn.addEventListener("click", async () => {
    try { const t = await post("/api/discovery/run"); ctx.trackTask(t.id, "Discovery run"); follow(t.id); } catch (e) { toast(e.message, "error"); }
  });

  const sortSel = h("select", { "aria-label": "Sort jobs", onchange: () => { st.sort = sortSel.value; st.page = 1; loadList(); } },
    [["match", "Best match"], ["posted", "Newest posting"], ["newest", "Recently added"], ["company", "Company A–Z"], ["salary", "Highest salary"]].map(([v, l]) => h("option", { value: v, selected: st.sort === v }, l)));

  const filters = () => filterBar({
    values: st.f,
    onChange: (v) => { st.f = v; st.page = 1; loadList(); },
    filters: [
      { key: "q", label: "Search", placeholder: "Role, company, keyword", grow: true, width: "220px" },
      { key: "location", label: "Location", placeholder: "City or country" },
      { key: "work_mode", label: "Work mode", type: "select", options: [["remote", "Remote"], ["hybrid", "Hybrid"], ["onsite", "On-site"], ["unknown", "Not stated"]] },
      { key: "employment_type", label: "Employment", type: "select", options: [["full-time", "Full-time"], ["part-time", "Part-time"], ["contract", "Contract"], ["internship", "Internship"], ["temporary", "Temporary"]] },
      { key: "source", label: "Source", type: "select", options: [["manual", "Manual entry"], ["career_page", "Career page URL"], ["greenhouse", "Greenhouse"], ["lever", "Lever"], ["ashby", "Ashby"], ["remotive", "Remotive"]] },
      { key: "posted_within", label: "Posted within", type: "select", options: [["1", "24 hours"], ["7", "7 days"], ["14", "14 days"], ["30", "30 days"]] },
      { key: "max_experience", label: "Max years required", type: "select", options: [["0", "0 (fresher)"], ["1", "≤ 1"], ["2", "≤ 2"], ["3", "≤ 3"], ["5", "≤ 5"], ["8", "≤ 8"]] },
      { key: "min_salary", label: "Min salary", placeholder: "e.g. 1200000", type: "number" },
    ] });

  mount(ctx.root,
    pageHead(fixedView ? { title: "Saved / Shortlisted", desc: "Jobs you shortlisted. Next step: generate a truthful tailored resume, approve it, then prepare the application." }
      : { title: "Discover Jobs", desc: "Paste a job URL or run discovery over your target companies. Only jobs that pass every hard requirement and your match threshold are recommended." }),
    fixedView ? null : h("div", { class: "split mb-4" }, h("section", { class: "card card-pad" }, urlForm),
      h("section", { class: "card card-pad stack" }, h("div", { class: "row-between" }, h("h2", { style: { fontSize: "16px" } }, "Discovery"), runBtn), runStatus)),
    fixedView ? null : tabsEl, fixedView ? null : descEl,
    h("div", { class: "mb-4" }, filters()),
    h("div", { class: "row-between mb-2" }, h("span", { class: "small muted" }, "Duplicate postings are merged; their extra sources are shown on the card."), h("label", { class: "row small" }, "Sort", sortSel)),
    listEl);
  if (!fixedView) loadRuns();
  await loadList();
}
