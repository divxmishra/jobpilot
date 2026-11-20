import { h, icon, mount, clear, fmt } from "../ui/dom.js";
import { api, get, post, put } from "../api.js";
import { badge, callout, confirmDialog, errorState, loading, meter, toast, withBusy } from "../ui/components.js";
import { pageHead, section } from "./_common.js";

const KM_LABEL = { directly_evidenced: ["Directly evidenced", "ok"], transferable: ["Transferable (context only)", "partial"],
  not_evidenced: ["Not evidenced", "miss"], uncertain: ["Uncertain / unverified", "partial"] };

export async function render(ctx) {
  const id = Number(ctx.params.id);
  let tab = ctx.query.get("tab") || "compare";
  let dirty = false;
  const before = (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", before);

  const load = async () => {
    mount(ctx.root, loading(5));
    let r;
    try { r = await get(`/api/resumes/${id}`); } catch (e) { mount(ctx.root, errorState(e, e.status === 404 ? null : load)); return; }
    if (!ctx.isCurrent()) return;
    ctx.setTitle(`Resume v${r.version}`);
    const ats = r.ats, cons = r.consistency;
    const body = h("div");
    const tabs = [["compare", "Side by side"], ["edit", "Edit draft"], ["ats", "ATS report"], ["keywords", "Keyword map & gaps"], ["versions", "Versions"]];
    const tabBar = h("div", { class: "tabs", role: "tablist" });
    const paintTabs = () => {
      clear(tabBar);
      tabs.forEach(([k, l]) => tabBar.append(h("button", { role: "tab", "aria-selected": String(tab === k), "aria-controls": "resume-panel",
        onclick: async () => {
          if (dirty && !(await confirmDialog({ title: "Discard unsaved edits?", message: "You have unsaved changes in the editor.", confirmLabel: "Discard", danger: true }))) return;
          dirty = false; tab = k; history.replaceState({}, "", `?tab=${k}`); paintTabs(); paint();
        } }, l)));
    };
    const paint = () => {
      const panel = { compare: comparePanel, edit: editPanel, ats: atsPanel, keywords: kwPanel, versions: versionsPanel }[tab] || comparePanel;
      mount(body, h("div", { id: "resume-panel", role: "tabpanel" }, panel(r)));
    };

    const isDraft = r.status === "draft";
    const actions = [
      h("a", { class: "btn", href: `/api/resumes/${id}/download?format=pdf`, download: "" }, icon("download", 16), "PDF"),
      h("a", { class: "btn", href: `/api/resumes/${id}/download?format=docx`, download: "" }, icon("download", 16), "DOCX"),
      isDraft ? h("button", { class: "btn btn-ghost", onclick: async (e) => {
        if (await confirmDialog({ title: "Discard this draft?", message: "The draft is kept in version history as superseded; it won't be usable for applications.", confirmLabel: "Discard draft", danger: true })) {
          withBusy(e.target, async () => { try { await post(`/api/resumes/${id}/discard`); toast("Draft discarded", "info"); ctx.refreshCounts(); load(); } catch (er) { toast(er.message, "error"); } });
        } } }, "Discard") : null,
      isDraft ? h("button", { class: "btn btn-primary", disabled: !cons.ok, title: cons.ok ? "" : "Fix unsupported claims first",
        onclick: async (e) => {
          if (!(await confirmDialog({ title: "Approve this resume?", message: `You're approving v${r.version} (estimated ATS ${ats.score}/100) for "${r.job.title}". Approval only marks it ready — nothing is sent or submitted.`, confirmLabel: "Approve" }))) return;
          withBusy(e.target, async () => { try { await post(`/api/resumes/${id}/approve`); toast("Resume approved", "success"); ctx.refreshCounts(); load(); } catch (er) { toast(er.message, "error"); } });
        } }, icon("check", 16), "Approve") : null,
      r.status === "approved" ? h("a", { class: "btn btn-primary", href: `/applications?prepare=${r.job.id}` }, icon("briefcase", 16), "Prepare application") : null,
    ];

    mount(ctx.root,
      pageHead({ title: `${r.job.title} — v${r.version}`, desc: `${r.job.company} · built from verified profile v${r.profile_version}`,
        crumbs: [{ label: "Resume Studio", href: "/resumes" }, { label: r.job.title, href: `/jobs/${r.job.id}` }, { label: `v${r.version}` }], actions }),
      h("div", { class: "grid grid-3 mb-4" },
        h("div", { class: "card stat" }, h("span", { class: "label" }, icon("target", 15), "Estimated ATS compatibility"),
          h("span", { class: "value" }, `${ats.score}`, h("span", { class: "small muted" }, " / 100")),
          h("span", { class: `badge ${ats.target_met ? "b-success" : "b-warning"}`, style: { alignSelf: "flex-start" } }, icon(ats.target_met ? "check" : "alert", 12), ats.label),
          h("span", { class: "hint" }, `${ats.band}. ${ats.disclaimer}`)),
        h("div", { class: "card stat" }, h("span", { class: "label" }, icon("shield", 15), "Truthfulness check"),
          h("span", { class: "value", style: { fontSize: "20px" } }, cons.ok ? "Every claim traceable" : `${cons.issues.length} issue(s)`),
          cons.ok ? badge("pass", "Passed") : badge("fail", "Blocks approval"), h("span", { class: "hint" }, "Each skill, bullet, date and title is checked against your verified profile.")),
        h("div", { class: "card stat" }, h("span", { class: "label" }, icon("layers", 15), "State"), h("span", { class: "value", style: { fontSize: "20px" } }, badge(r.status)),
          h("span", { class: "hint" }, `${r.page_count} page(s) · created ${fmt.dateTime(r.created_at)}${r.approved_at ? " · approved " + fmt.dateTime(r.approved_at) : ""}`))),
      !cons.ok ? h("div", { class: "mb-4" }, callout("danger", h("div", null, h("strong", null, "Unsupported claims — approval blocked"),
        h("ul", { class: "evidence-list" }, cons.issues.map((i) => h("li", null, `${i.message} (${i.item})`))),
        h("div", { class: "small" }, "Remove them in the editor, or — only if true — add the fact to your profile with evidence via a profile update, then regenerate.")))) : null,
      tabBar, body);
    paintTabs(); paint();

    // ---------------- panels
    function comparePanel() {
      const found = new Set(ats.found_terms.map((t) => t.term.toLowerCase()));
      const missing = ats.missing_terms.filter((t) => t.kind !== "keyword").map((t) => t.term);
      return h("div", { class: "split" },
        section("Job description", h("div", { class: "stack" },
          h("div", { class: "legend" }, h("span", null, h("i", { style: { background: "var(--success-soft)", border: "1px solid var(--success)" } }), "In your resume"),
            h("span", null, h("i", { style: { background: "var(--danger-soft)", border: "1px solid var(--danger)" } }), "Missing (skill gap — not claimed)")),
          h("div", { class: "jd-pane" }, highlight(r.job.description || "", [...found], missing)))),
        section("Tailored resume preview", preview(r), { actions: [h("span", { class: "xs muted" }, "Rendered from the actual PDF")] }));
    }

    function atsPanel() {
      return h("div", { class: "split" },
        section("Score components", h("div", { class: "breakdown" }, ats.components.map((c) => h("div", { class: "bd-row" },
          h("div", null, h("div", { style: { fontWeight: 600 } }, c.label), h("div", { class: "xs muted" }, c.detail)),
          meter(c.points, c.weight, c.points / c.weight >= 0.95 ? "ok" : c.points / c.weight >= 0.7 ? "warn" : "bad"), h("span", { class: "pts" }, `${c.points} / ${c.weight}`))))),
        h("div", { class: "stack" },
          section("Why not higher? Safe improvements", ats.advice?.length ? h("ul", { class: "evidence-list small" }, ats.advice.map((a) => h("li", null, a)))
            : h("p", { class: "small muted" }, ats.target_met ? "Target achieved truthfully." : "No further truthful improvements identified.")),
          section("JD terms", h("div", { class: "stack" },
            h("div", null, h("div", { class: "xs muted mb-2" }, "Present in rendered resume"), ats.found_terms.map((t) => h("span", { class: "tag ok" }, t.term))),
            h("div", null, h("div", { class: "xs muted mb-2" }, "Absent (never added just to raise the score)"), ats.missing_terms.length ? ats.missing_terms.map((t) => h("span", { class: `tag ${t.kind === "required" ? "miss" : ""}` }, `${t.term} · ${t.kind}`)) : h("span", { class: "small muted" }, "None"))))));
    }

    function kwPanel() {
      const km = r.keyword_map || [];
      if (!km.length) return section("Keyword map", h("p", { class: "muted" }, "No explicit skill requirements were found in this posting."));
      return section("Requirement → evidence map", h("div", { class: "table-wrap" }, h("table", { class: "table responsive" },
        h("thead", null, h("tr", null, ["Requirement", "Type", "Status", "Your evidence / note"].map((t) => h("th", { scope: "col" }, t)))),
        h("tbody", null, km.map((row) => h("tr", null,
          h("td", { "data-label": "Requirement" }, h("strong", null, row.requirement)),
          h("td", { "data-label": "Type" }, row.type),
          h("td", { "data-label": "Status" }, h("span", { class: `tag ${KM_LABEL[row.status][1]}` }, KM_LABEL[row.status][0])),
          h("td", { "data-label": "Evidence" }, (row.resume_evidence || []).join("; ") || row.note || "—", row.note && row.resume_evidence?.length ? h("div", { class: "xs muted" }, row.note) : null)))))),
        { actions: [h("span", { class: "xs muted" }, "Gaps are reported here, never added to the resume")] });
    }

    function versionsPanel() {
      return section("Version history", h("ul", { class: "timeline" }, r.versions.map((v) => h("li", null, h("span", { class: "when" }, fmt.dateTime(v.created_at)),
        h("span", { class: "row" }, v.id === r.id ? h("strong", null, `v${v.version} (viewing)`) : h("a", { href: `/resumes/${v.id}` }, `v${v.version}`),
          badge(v.status), h("span", { class: "small muted" }, `ATS ${v.ats_score}${v.edited ? " · edited by you" : " · generated"}`))))));
    }

    function editPanel() {
      if (!isDraft) return callout("info", "Approved and superseded versions are read-only. Generate a new draft from the job page to make changes.");
      const c = structuredClone(r.content);
      const mark = () => { dirty = true; };
      const summary = h("textarea", { id: "ed-summary", rows: 4, oninput: () => { c.summary = summary.value; mark(); } }); summary.value = c.summary || "";
      const skills = h("textarea", { id: "ed-skills", rows: 3, oninput: () => { c.skills = skills.value.split(",").map((s) => s.trim()).filter(Boolean); mark(); } }); skills.value = c.skills.join(", ");
      const entryEditor = (list, kind) => list.map((e, ei) => h("div", { class: "fact", style: { gridTemplateColumns: "1fr" } },
        h("div", { class: "row-between" }, h("strong", null, kind === "experience" ? `${e.title} — ${e.company}` : e.name),
          h("span", { class: "xs muted" }, kind === "experience" ? `${e.start} – ${e.end} (from verified profile, not editable here)` : "From verified profile")),
        h("div", { class: "stack" }, e.bullets.map((b, bi) => {
          const ta = h("textarea", { "aria-label": `Bullet ${bi + 1}`, oninput: () => { b.text = ta.value; mark(); } }); ta.value = b.text;
          return h("div", { class: "editor-bullet" }, ta, h("div", { class: "stack", style: { gap: "4px" } },
            h("button", { class: "btn btn-ghost btn-icon btn-sm", "aria-label": "Move up", disabled: bi === 0, onclick: () => { [e.bullets[bi - 1], e.bullets[bi]] = [e.bullets[bi], e.bullets[bi - 1]]; mark(); rerender(); } }, icon("chevronUp", 14)),
            h("button", { class: "btn btn-ghost btn-icon btn-sm", "aria-label": "Remove bullet", onclick: () => { e.bullets.splice(bi, 1); mark(); rerender(); } }, icon("trash", 14))));
        })),
        h("button", { class: "btn btn-ghost btn-sm", style: { alignSelf: "flex-start" }, onclick: () => { list.splice(ei, 1); mark(); rerender(); } }, icon("trash", 14), `Remove this ${kind === "experience" ? "position" : "project"} from this resume`)));
      const wrap = h("div");
      const rerender = () => mount(wrap, h("div", { class: "stack" },
        callout("info", "You can reword, reorder or remove content. Anything that adds a skill, number or metric not in your verified profile is flagged and blocks approval. Saving creates a new version; the previous one is kept."),
        section("Summary", h("label", { class: "field", for: "ed-summary" }, h("span", { class: "hint" }, c.summary_source || ""), summary)),
        section("Skills", h("label", { class: "field", for: "ed-skills" }, "Comma-separated", h("span", { class: "hint" }, "Only verified skills pass the truthfulness check"), skills)),
        c.experience.length ? section("Experience", h("div", { class: "stack" }, entryEditor(c.experience, "experience"))) : null,
        c.projects.length ? section("Projects", h("div", { class: "stack" }, entryEditor(c.projects, "projects"))) : null,
        h("div", { class: "row", style: { justifyContent: "flex-end" } },
          h("button", { class: "btn btn-primary", onclick: (e) => withBusy(e.currentTarget, async () => {
            try {
              const nv = await put(`/api/resumes/${id}`, { content: c });
              dirty = false;
              toast(nv.consistency.ok ? `Saved as v${nv.version} — ATS ${nv.ats_score}` : `Saved as v${nv.version}, but it contains unsupported claims`, nv.consistency.ok ? "success" : "warning");
              ctx.navigate(`/resumes/${nv.id}?tab=compare`);
            } catch (er) { toast(er.message, "error"); }
          }) }, icon("check", 16), "Save as new version & re-score"))));
      rerender();
      return wrap;
    }
  };

  await load();
  return () => window.removeEventListener("beforeunload", before);
}

/** Real preview: PNG renders of the actual generated PDF, with page controls. */
function preview(r) {
  let page = 1, pages = r.page_count || 1;
  const img = h("img", { class: "preview-page", alt: `Page 1 of the tailored resume PDF`, src: `/api/resumes/${r.id}/pages/1` });
  const label = h("span", { class: "small", "aria-live": "polite" });
  const prev = h("button", { class: "btn btn-sm", "aria-label": "Previous page", onclick: () => go(page - 1) }, icon("chevronLeft", 14));
  const next = h("button", { class: "btn btn-sm", "aria-label": "Next page", onclick: () => go(page + 1) }, icon("chevronRight", 14));
  const status = h("div");
  img.addEventListener("error", () => mount(status, callout("danger", "Preview could not be rendered. Download the PDF to view it.")));
  function go(p) {
    page = Math.max(1, Math.min(pages, p));
    img.src = `/api/resumes/${r.id}/pages/${page}`; img.alt = `Page ${page} of ${pages} of the tailored resume PDF`;
    paint();
  }
  function paint() { label.textContent = `Page ${page} of ${pages}`; prev.disabled = page <= 1; next.disabled = page >= pages; }
  paint();
  return h("div", { class: "stack" }, h("div", { class: "row-between" }, h("div", { class: "row" }, prev, label, next),
    h("div", { class: "row" }, h("a", { class: "btn btn-sm", href: `/api/resumes/${r.id}/download?format=pdf`, download: "" }, icon("download", 14), "PDF"),
      h("a", { class: "btn btn-sm", href: `/api/resumes/${r.id}/download?format=docx`, download: "" }, icon("download", 14), "DOCX"))), status, img);
}

function highlight(text, ok, miss) {
  const terms = [...ok.map((t) => [t, "kw-ok"]), ...miss.map((t) => [t, "kw-miss"])].filter(([t]) => t && t.length > 2);
  if (!terms.length) return text;
  const esc = (s) => s.replace(/[.*+?^${}()|[\]\\]/g, "\\$&");
  const re = new RegExp(`(?<![A-Za-z0-9+#])(${terms.map(([t]) => esc(t)).join("|")})(?![A-Za-z0-9+#])`, "gi");
  const cls = new Map(terms.map(([t, c]) => [t.toLowerCase(), c]));
  const out = []; let last = 0;
  for (const m of text.matchAll(re)) { out.push(text.slice(last, m.index)); out.push(h("mark", { class: cls.get(m[0].toLowerCase()) || "kw-ok" }, m[0])); last = m.index + m[0].length; }
  out.push(text.slice(last));
  return out;
}
void api;
