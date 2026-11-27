import { h, icon, mount, clear, fmt } from "../ui/dom.js";
import { get, post, put, del } from "../api.js";
import { badge, callout, confirmDialog, errorState, loading, toast, uploader, withBusy, modal } from "../ui/components.js";
import { pageHead, section } from "./_common.js";

const LISTS = {
  skills: { title: "Skills", fields: [["name", "Skill", "text"]], blank: { name: "" } },
  experience: { title: "Experience", fields: [["title", "Job title", "text"], ["company", "Employer", "text"], ["location", "Location", "text"],
    ["start", "Start (YYYY-MM)", "text"], ["end", "End (YYYY-MM or present)", "text"], ["is_internship", "Internship / traineeship", "check"], ["bullets", "Responsibilities & achievements (one per line)", "lines"]],
    blank: { title: "", company: "", location: "", start: "", end: "", is_internship: false, bullets: [] } },
  education: { title: "Education", fields: [["degree", "Degree", "text"], ["field", "Field of study", "text"], ["institution", "Institution", "text"],
    ["start", "Start year", "text"], ["end", "Completion year", "text"], ["grade", "Grade / CGPA", "text"]], blank: { degree: "", field: "", institution: "", start: "", end: "", grade: "" } },
  projects: { title: "Projects", fields: [["name", "Project name", "text"], ["link", "Link", "text"], ["bullets", "Description (one point per line)", "lines"]], blank: { name: "", link: "", bullets: [] } },
  certifications: { title: "Certifications & licences", fields: [["name", "Certification", "text"]], blank: { name: "" } },
  achievements: { title: "Achievements", fields: [["text", "Achievement", "text"]], blank: { text: "" } },
  links: { title: "Links (GitHub, LinkedIn, portfolio)", fields: [["label", "Label", "text"], ["url", "URL", "text"]], blank: { label: "Portfolio", url: "" } },
};
const BASICS = [["name", "Full name"], ["email", "Email"], ["phone", "Phone"], ["location", "Location"], ["headline", "Headline / current title"]];

function noticeCtcCard() {
  const box = h("div", { class: "stack" }, h("span", { class: "small muted" }, "Loading…"));
  get("/api/preferences").then((p) => {
    const np = h("select", { id: "np" }, [["", "Not set"], ["immediate", "Immediate"], ["15", "15 days"], ["30", "30 days"], ["60", "60 days"], ["90", "90 days"], ["negotiable", "Negotiable"]]
      .map(([v, l]) => h("option", { value: v, selected: (p.notice_period || "") === v }, l)));
    const exp = h("input", { type: "number", id: "exp-ctc", min: 0, step: "0.1", value: p.expected_ctc_lpa ?? "" });
    const min = h("input", { type: "number", id: "min-ctc", min: 0, step: "0.1", value: p.min_ctc_lpa ?? "" });
    const neg = h("input", { type: "checkbox", id: "neg", checked: !!p.ctc_negotiable });
    const btn = h("button", { class: "btn btn-sm", style: { alignSelf: "flex-start" } }, "Save");
    btn.addEventListener("click", () => withBusy(btn, async () => {
      try { await put("/api/preferences", { notice_period: np.value || null, expected_ctc_lpa: exp.value === "" ? null : exp.value, min_ctc_lpa: min.value === "" ? null : min.value, ctc_negotiable: neg.checked }); toast("Notice period & CTC saved", "success"); }
      catch (e) { toast(e.message, "error"); }
    }));
    mount(box, h("div", { class: "form-grid" }, h("label", { class: "field", for: "np" }, "Notice period", np), h("label", { class: "field", for: "exp-ctc" }, "Expected CTC (LPA)", exp),
      h("label", { class: "field", for: "min-ctc" }, "Minimum acceptable CTC (LPA)", min), h("label", { class: "check", for: "neg" }, neg, "Negotiable")), btn,
      h("span", { class: "xs muted" }, "Used only against requirements a job states explicitly. More options in Settings → Preferences."));
  }).catch((e) => mount(box, callout("danger", e.message)));
  return box;
}

export async function render(ctx) {
  ctx.setTitle("Profile");
  let dirty = false;
  const onBeforeUnload = (e) => { if (dirty) { e.preventDefault(); e.returnValue = ""; } };
  window.addEventListener("beforeunload", onBeforeUnload);

  async function load() {
    mount(ctx.root, loading(4));
    let p;
    try { p = await get("/api/profile"); } catch (e) { mount(ctx.root, errorState(e, load)); return; }
    if (!ctx.isCurrent()) return;
    if (p.draft) return renderReview(p);
    if (p.active) return renderActive(p);
    return renderWelcome(p);
  }

  function steps(current) {
    const names = ["Upload master resume", "Review & correct", "Confirm verified profile"];
    return h("ol", { class: "steps", "aria-label": "Setup progress", style: { listStyle: "none", padding: 0 } }, names.map((n, i) => [
      i ? h("li", { class: "step-sep", "aria-hidden": "true" }) : null,
      h("li", { class: `step ${i < current ? "done" : i === current ? "current" : ""}`, "aria-current": i === current ? "step" : null },
        h("span", { class: "n" }, i < current ? icon("check", 14) : i + 1), n)]));
  }

  function renderWelcome() {
    mount(ctx.root,
      pageHead({ title: "Welcome to JobPilot", desc: "Set up your verified master profile. JobPilot recommends only jobs you genuinely qualify for, and tailors resumes using only facts you confirm." }),
      steps(0),
      h("div", { class: "split" },
        section("Upload your master resume", h("div", { class: "stack" },
          uploader({ endpoint: "/api/profile/upload", onDone: () => { toast("Resume extracted — please review every item", "success"); load(); } }),
          h("p", { class: "small text-2" }, "Your original file is stored unchanged (immutable) and fingerprinted. Nothing is sent to any external service."),
          h("button", { class: "btn btn-ghost", onclick: async (e) => withBusy(e.currentTarget, async () => { await post("/api/profile/blank"); load(); }) }, icon("edit", 16), "Enter details manually instead"))),
        section("How it works", h("ol", { class: "evidence-list" },
          h("li", null, "JobPilot extracts skills, experience, education, projects, certifications and links."),
          h("li", null, "You correct anything wrong. Unclear items are flagged as uncertain — nothing is guessed."),
          h("li", null, "You confirm the facts that are accurate. Unconfirmed items stay unverified and are never used."),
          h("li", null, "Later changes go through a versioned update; your original resume is never overwritten.")))));
  }

  function renderActive(p) {
    const v = p.active;
    const d = v.data;
    const exp = p.experience;
    const count = (k) => (d[k] || []).filter((i) => i.status === "verified").length;
    mount(ctx.root,
      pageHead({ title: "Profile", desc: "Your verified master profile is read-only. Changes go through a controlled, versioned update.",
        actions: [
          h("a", { class: "btn", href: "/api/profile/cv.md", target: "_blank", rel: "noopener" }, icon("file", 16), "View cv.md"),
          h("button", { class: "btn btn-primary", onclick: async (e) => withBusy(e.currentTarget, async () => { await post("/api/profile/update"); toast("Draft update created from your verified profile", "success"); load(); }) }, icon("edit", 16), "Start profile update")] }),
      h("div", { class: "grid grid-4 mb-4" },
        h("div", { class: "card stat" }, h("span", { class: "label" }, icon("shield", 15), "Verified version"), h("span", { class: "value" }, `v${v.version}`), h("span", { class: "hint" }, `Confirmed ${fmt.date(v.confirmed_at)}`)),
        h("div", { class: "card stat" }, h("span", { class: "label" }, icon("briefcase", 15), "Verified experience"), h("span", { class: "value" }, `${exp.years} yrs`),
          h("span", { class: "hint" }, `${exp.stage}${exp.precise ? "" : " · approximate (year-only dates)"}`)),
        h("div", { class: "card stat" }, h("span", { class: "label" }, icon("target", 15), "Verified skills"), h("span", { class: "value" }, count("skills"))),
        h("div", { class: "card stat" }, h("span", { class: "label" }, icon("layers", 15), "Verified entries"), h("span", { class: "value" }, count("experience") + count("education") + count("projects")))),
      h("div", { class: "split-wide" },
        h("div", { class: "stack" },
          section("Verified facts", readOnlyProfile(d), { actions: [h("span", { class: "badge b-success" }, icon("lock", 12), "Read-only")] })),
        h("div", { class: "stack" },
          section("Replace master resume", h("div", { class: "stack" },
            h("p", { class: "small text-2" }, "Uploading a new resume creates a new draft for review. Your current verified profile stays active until you confirm the new one."),
            uploader({ endpoint: "/api/profile/upload", label: "Upload a newer resume", onDone: () => load() }))),
          section("Notice period & expected CTC", noticeCtcCard()),
          section("Supporting documents", supporting(p)),
          section("Version history", h("ul", { class: "timeline" }, p.versions.map((x) => h("li", null, h("span", { class: "when" }, fmt.date(x.confirmed_at || x.created_at)),
            h("span", null, `v${x.version} `, badge(x.status === "superseded" ? "superseded" : x.status === "verified" ? "verified" : "draft", fmt.cap(x.status)), h("span", { class: "xs muted" }, ` ${x.note || ""}`)))))),
          section("Original uploads", h("ul", { class: "timeline" }, p.uploads.map((u) => h("li", null, h("span", { class: "when" }, fmt.date(u.created_at)),
            h("span", null, h("a", { href: `/api/profile/uploads/${u.id}/download` }, u.filename), h("div", { class: "xs muted" }, `${u.kind.replace("_", " ")} · ${(u.size / 1024).toFixed(0)} KB · sha256 ${u.sha256.slice(0, 12)}…`)))))))));
  }

  function supporting(p) {
    const list = p.uploads.filter((u) => u.kind === "supporting");
    return h("div", { class: "stack" },
      h("p", { class: "small text-2" }, "Optional certificates or portfolio files (PDF, DOCX, PNG, JPG). They're stored privately as evidence; add links (GitHub, LinkedIn) in the profile."),
      uploader({ endpoint: "/api/profile/supporting", accept: ".pdf,.docx,.png,.jpg,.jpeg", label: "Add a supporting document", hint: "PDF, DOCX or image, up to 8 MB", onDone: () => { toast("Document stored", "success"); load(); } }),
      list.length ? h("ul", { class: "evidence-list small" }, list.map((u) => h("li", null, h("a", { href: `/api/profile/uploads/${u.id}/download` }, u.filename)))) : null);
  }

  function readOnlyProfile(d) {
    const b = d.basics || {};
    const vb = (k) => (b[k]?.status === "verified" ? b[k].value : null);
    const ver = (k) => (d[k] || []).filter((i) => i.status === "verified");
    const unver = Object.keys(LISTS).reduce((n, k) => n + (d[k] || []).filter((i) => i.status !== "verified").length, 0);
    return h("div", { class: "stack" },
      h("div", null, h("h3", null, vb("name") || "Name not verified"), h("div", { class: "text-2 small" }, [vb("headline"), vb("email"), vb("phone"), vb("location")].filter(Boolean).join(" · "))),
      d.summary?.status === "verified" ? h("p", null, d.summary.value) : null,
      ver("skills").length ? h("div", null, h("h4", { class: "mb-2" }, "Skills"), ver("skills").map((s) => h("span", { class: "tag ok" }, s.name))) : null,
      ver("experience").length ? h("div", null, h("h4", { class: "mb-2" }, "Experience"), ver("experience").map((e) => h("div", { class: "mb-2" },
        h("strong", null, `${e.title || ""} — ${e.company || ""}`), h("div", { class: "xs muted" }, `${e.start || "?"} – ${e.end || "?"}${e.is_internship ? " · internship" : ""}`),
        h("ul", { class: "evidence-list small" }, (e.bullets || []).map((x) => h("li", null, x)))))) : null,
      ver("education").length ? h("div", null, h("h4", { class: "mb-2" }, "Education"), ver("education").map((e) => h("div", { class: "small" }, `${e.degree || ""}${e.field ? " in " + e.field : ""}, ${e.institution || ""} (${e.end || "?"})`))) : null,
      ver("projects").length ? h("div", null, h("h4", { class: "mb-2" }, "Projects"), ver("projects").map((p) => h("div", { class: "small" }, h("strong", null, p.name), " — ", (p.bullets || []).join(" ")))) : null,
      ver("certifications").length ? h("div", null, h("h4", { class: "mb-2" }, "Certifications"), ver("certifications").map((c) => h("div", { class: "small" }, c.name))) : null,
      unver ? callout("info", `${unver} item(s) remain unverified and are excluded from matching and tailoring. Start a profile update to review them.`) : null);
  }

  // ---------------------------------------------------------------- review / edit draft
  function renderReview(p) {
    const draft = p.draft;
    const data = structuredClone(draft.data);
    for (const k of Object.keys(LISTS)) data[k] = data[k] || [];
    data.basics = data.basics || {};
    for (const [k] of BASICS) data.basics[k] = data.basics[k] || { value: null, status: "missing" };
    data.summary = data.summary || { value: null, status: "missing" };
    data.work_authorization = data.work_authorization || { value: null, status: "missing" };
    const warnings = data.extraction?.warnings || [];
    const body = h("div", { class: "stack" });
    const counter = h("span", { class: "small text-2", role: "status" });
    const markDirty = () => { dirty = true; updateCounter(); };

    function updateCounter() {
      let ver = 0, total = 0, unc = 0;
      for (const [k] of BASICS) { if (data.basics[k].value) { total++; if (data.basics[k].status === "verified") ver++; } }
      for (const k of Object.keys(LISTS)) for (const i of data[k]) { total++; if (i.status === "verified") ver++; if (i.status === "uncertain") unc++; }
      counter.textContent = `${ver} of ${total} items confirmed · ${unc} uncertain${dirty ? " · unsaved changes" : ""}`;
    }

    function verifyToggle(obj, label) {
      const orig = obj._orig || (obj.status === "verified" ? "unverified" : obj.status);
      obj._orig = orig;
      const cb = h("input", { type: "checkbox", checked: obj.status === "verified", "aria-label": `Confirm ${label} is accurate`,
        onchange: () => { obj.status = cb.checked ? "verified" : (orig === "missing" ? "unverified" : orig); markDirty(); renderSections(); } });
      return h("label", { class: "check", title: "Only confirm facts that are accurate" }, cb, "Confirmed");
    }

    function basicsEditor() {
      const rows = BASICS.map(([k, label]) => {
        const f = data.basics[k];
        const inp = h("input", { type: k === "email" ? "email" : "text", value: f.value || "", id: `b-${k}`,
          oninput: () => { f.value = inp.value.trim() || null; if (f.status === "missing" && f.value) { f.status = "unverified"; f._orig = "unverified"; } markDirty(); } });
        return h("div", { class: "fact" + (f.status === "verified" ? " verified" : "") },
          h("span"), h("label", { class: "field", for: `b-${k}` }, label, inp), h("div", null, badge(f.status), h("div", { class: "mt-2" }, verifyToggle(f, label))));
      });
      const sum = h("textarea", { id: "b-summary", oninput: () => { data.summary.value = sum.value.trim() || null; if (data.summary.status === "missing" && data.summary.value) data.summary.status = "unverified"; markDirty(); } });
      sum.value = data.summary.value || "";
      const wa = h("input", { type: "text", id: "b-wa", placeholder: "e.g. Indian citizen; authorised to work in India", value: data.work_authorization.value || "",
        oninput: () => { data.work_authorization.value = wa.value.trim() || null; if (data.work_authorization.status === "missing" && wa.value) data.work_authorization.status = "unverified"; markDirty(); } });
      return h("div", { class: "stack" }, rows,
        h("div", { class: "fact" + (data.summary.status === "verified" ? " verified" : "") }, h("span"),
          h("label", { class: "field", for: "b-summary" }, "Professional summary", h("span", { class: "hint" }, "Used unchanged on tailored resumes when verified"), sum),
          h("div", null, badge(data.summary.status), h("div", { class: "mt-2" }, verifyToggle(data.summary, "summary")))),
        h("div", { class: "fact" + (data.work_authorization.status === "verified" ? " verified" : "") }, h("span"),
          h("label", { class: "field", for: "b-wa" }, "Work authorisation (optional)", h("span", { class: "hint" }, "Also set countries in Settings → Preferences for eligibility checks"), wa),
          h("div", null, badge(data.work_authorization.status), h("div", { class: "mt-2" }, verifyToggle(data.work_authorization, "work authorisation")))));
    }

    function listEditor(key) {
      const spec = LISTS[key];
      const items = data[key];
      const wrap = h("div", { class: "stack" });
      if (key === "skills") {
        const chips = items.map((s, idx) => h("div", { class: `fact ${s.status}`, style: { gridTemplateColumns: "1fr auto auto", padding: "8px 12px" } },
          h("div", null, h("strong", null, s.name || "(empty)"), s.evidence?.length ? h("div", { class: "xs muted" }, s.evidence.join("; ")) : null),
          verifyToggle(s, s.name),
          h("button", { class: "btn btn-ghost btn-icon btn-sm", "aria-label": `Remove ${s.name}`, onclick: () => { items.splice(idx, 1); markDirty(); renderSections(); } }, icon("trash", 14))));
        const add = h("input", { type: "text", placeholder: "Add a skill you genuinely have, e.g. PostgreSQL", "aria-label": "New skill" });
        const addBtn = h("button", { class: "btn", type: "button", onclick: () => {
          const v = add.value.trim(); if (!v) return;
          if (items.some((s) => s.name.toLowerCase() === v.toLowerCase())) { toast("That skill is already listed", "warning"); return; }
          items.push({ name: v, status: "unverified", evidence: ["Added by you"], category: "custom" }); add.value = ""; markDirty(); renderSections();
        } }, icon("plus", 14), "Add");
        add.addEventListener("keydown", (e) => { if (e.key === "Enter") { e.preventDefault(); addBtn.click(); } });
        wrap.append(h("div", { class: "grid grid-2" }, chips), h("div", { class: "row" }, h("div", { class: "grow" }, add), addBtn));
        return wrap;
      }
      items.forEach((item, idx) => {
        const grid = h("div", { class: "form-grid" });
        for (const [f, label, type] of spec.fields) {
          const id = `${key}-${idx}-${f}`;
          if (type === "check") {
            const cb = h("input", { type: "checkbox", id, checked: !!item[f], onchange: () => { item[f] = cb.checked; markDirty(); } });
            grid.append(h("label", { class: "check full", for: id }, cb, label));
          } else if (type === "lines") {
            const ta = h("textarea", { id, oninput: () => { item[f] = ta.value.split("\n").map((x) => x.trim()).filter(Boolean); markDirty(); } });
            ta.value = (item[f] || []).join("\n");
            grid.append(h("label", { class: "field full", for: id }, label, ta));
          } else {
            const inp = h("input", { type: "text", id, value: item[f] || "", oninput: () => { item[f] = inp.value.trim() || null; markDirty(); } });
            grid.append(h("label", { class: "field", for: id }, label, inp));
          }
        }
        wrap.append(h("div", { class: `fact ${item.status}`, style: { gridTemplateColumns: "1fr" } },
          h("div", { class: "row-between" }, h("div", { class: "row" }, badge(item.status), item.issues?.length ? h("span", { class: "issues" }, icon("alert", 12), " ", item.issues.join("; ")) : null),
            h("div", { class: "row" }, verifyToggle(item, spec.title),
              h("button", { class: "btn btn-ghost btn-sm", onclick: () => { items.splice(idx, 1); markDirty(); renderSections(); } }, icon("trash", 14), "Remove"))),
          grid));
      });
      wrap.append(h("div", null, h("button", { class: "btn btn-sm", onclick: () => { items.push({ ...structuredClone(spec.blank), status: "unverified", id: `${key.slice(0, 3)}_${Math.random().toString(36).slice(2, 10)}` }); markDirty(); renderSections(); } },
        icon("plus", 14), `Add ${spec.title.toLowerCase().replace(/s$/, "").replace(/ \(.*/, "")}`)));
      return wrap;
    }

    function renderSections() {
      const scroll = window.scrollY;
      mount(body,
        section("Contact & summary", basicsEditor()),
        ...Object.keys(LISTS).map((k) => section(`${LISTS[k].title} (${data[k].length})`, listEditor(k))));
      updateCounter();
      window.scrollTo(0, scroll);
    }

    const save = async () => {
      const payload = structuredClone(data);
      const strip = (o) => { if (o && typeof o === "object") { delete o._orig; Object.values(o).forEach(strip); } };
      strip(payload);
      await put(`/api/profile/versions/${draft.id}`, { data: payload });
      dirty = false; updateCounter();
    };

    const verifyAllClear = () => {
      let n = 0;
      for (const [k] of BASICS) if (data.basics[k].value && data.basics[k].status === "unverified") { data.basics[k]._orig = "unverified"; data.basics[k].status = "verified"; n++; }
      for (const k of Object.keys(LISTS)) for (const i of data[k]) if (i.status === "unverified") { i._orig = "unverified"; i.status = "verified"; n++; }
      markDirty(); renderSections();
      toast(`${n} clear item(s) marked confirmed. Uncertain items still need your individual review.`, "info");
    };

    mount(ctx.root,
      pageHead({ title: draft.note === "Profile update" ? "Update your profile" : "Review your profile",
        desc: "Correct anything wrong, then confirm the facts that are accurate. Only confirmed facts are used for matching and tailoring.",
        crumbs: [{ label: "Profile", href: "/app/setup" }, { label: `Draft v${draft.version}` }] }),
      steps(1),
      h("div", { class: "mb-4" }, section("Notice period & expected CTC (optional)", noticeCtcCard())),
      warnings.length ? h("div", { class: "mb-4" }, callout("warn", h("div", null, h("strong", null, "Extraction notes"), h("ul", { class: "evidence-list" }, warnings.map((w) => h("li", null, w)))))) : null,
      h("div", { class: "card card-pad mb-4", style: { position: "sticky", top: "66px", zIndex: 5 } },
        h("div", { class: "row-between" }, counter,
          h("div", { class: "row" },
            h("button", { class: "btn btn-ghost", onclick: async () => {
              if (await confirmDialog({ title: "Discard this draft?", message: "Your edits in this draft will be lost. Your original uploaded file and any verified profile are kept.", confirmLabel: "Discard draft", danger: true })) {
                await del(`/api/profile/versions/${draft.id}`); dirty = false; toast("Draft discarded", "info"); load();
              } } }, "Discard draft"),
            h("button", { class: "btn", onclick: verifyAllClear, title: "Confirms items that aren't flagged as uncertain" }, icon("check", 16), "Confirm all clear items"),
            h("button", { class: "btn", onclick: (e) => withBusy(e.currentTarget, async () => { try { await save(); toast("Draft saved", "success"); } catch (er) { toast(er.message, "error"); } }) }, "Save draft"),
            h("button", { class: "btn btn-primary", onclick: (e) => withBusy(e.currentTarget, async () => {
              try {
                await save();
                if (!data.basics.name.value || data.basics.name.status !== "verified") {
                  toast("Please confirm your name before verifying the profile.", "error"); document.getElementById("b-name")?.focus(); return;
                }
                if (!Object.keys(LISTS).some((k) => data[k].some((i) => i.status === "verified"))) {
                  toast("Confirm at least one skill, position, education or project entry.", "error"); return;
                }
                const ok = await confirmDialog({ title: "Confirm verified profile?", message: "These confirmed facts become your verified master profile (a new version). Unconfirmed items stay unverified and won't be used. Jobs will be re-evaluated.", confirmLabel: "Confirm profile" });
                if (!ok) return;
                const r = await post(`/api/profile/versions/${draft.id}/confirm`);
                if (r.task) ctx.trackTask(r.task.id, "Re-evaluating jobs");
                toast(`Profile v${r.version.version} verified`, "success"); dirty = false; load(); ctx.refreshCounts();
              } catch (er) { toast(er.message, "error"); }
            }) }, icon("shield", 16), "Confirm profile")))),
      body);
    renderSections();
  }

  await load();
  return () => window.removeEventListener("beforeunload", onBeforeUnload);
}
