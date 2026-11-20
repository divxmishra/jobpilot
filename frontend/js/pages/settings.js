import { h, icon, mount, clear, fmt } from "../ui/dom.js";
import { get, post, put, del } from "../api.js";
import { badge, callout, confirmDialog, dataTable, emptyState, errorState, loading, meter, modal, toast, withBusy } from "../ui/components.js";
import { getTheme, setTheme } from "../main.js";
import { pageHead, section } from "./_common.js";

const TABS = [["preferences", "Preferences & criteria"], ["sources", "Sources"], ["ai", "AI & budget"], ["appearance", "Appearance & notifications"], ["data", "Data & privacy"]];

export async function render(ctx) {
  ctx.setTitle("Settings");
  let tab = (location.hash || "").slice(1) || "preferences";
  if (!TABS.some(([k]) => k === tab)) tab = "preferences";
  const body = h("div");
  const tabBar = h("div", { class: "tabs", role: "tablist" });
  const paintTabs = () => { clear(tabBar); TABS.forEach(([k, l]) => tabBar.append(h("button", { role: "tab", "aria-selected": String(tab === k), onclick: () => { tab = k; history.replaceState({}, "", `/settings#${k}`); paintTabs(); paint(); } }, l))); };
  async function paint() {
    mount(body, loading(3));
    try { mount(body, await ({ preferences: prefsTab, sources: sourcesTab, ai: aiTab, appearance: appearanceTab, data: dataTab }[tab])()); }
    catch (e) { mount(body, errorState(e, paint)); }
  }
  mount(ctx.root, pageHead({ title: "Settings", desc: "Your criteria, sources, AI budget, appearance and data controls. Only you can change the match threshold — JobPilot never relaxes it." }), tabBar, body);
  paintTabs(); await paint();

  // ---------------------------------------------------------------- preferences
  async function prefsTab() {
    const p = await get("/api/preferences");
    const list = (v) => (v || []).join(", ");
    const F = {
      target_roles: h("input", { type: "text", value: list(p.target_roles), placeholder: "e.g. Backend Engineer, Data Analyst" }),
      industries: h("input", { type: "text", value: list(p.industries), placeholder: "e.g. Fintech, Healthcare" }),
      locations: h("input", { type: "text", value: list(p.locations), placeholder: "e.g. Bengaluru, Pune, India" }),
      work_authorization_countries: h("input", { type: "text", value: list(p.work_authorization_countries), placeholder: "Countries you can legally work in, e.g. India" }),
      salary_min: h("input", { type: "number", min: 0, value: p.salary_min ?? "", placeholder: "Annual, e.g. 1200000" }),
      salary_currency: h("select", null, ["INR", "USD", "EUR", "GBP"].map((c) => h("option", { value: c, selected: p.salary_currency === c }, c))),
      excluded_companies: h("input", { type: "text", value: list(p.excluded_companies) }),
      excluded_keywords: h("input", { type: "text", value: list(p.excluded_keywords), placeholder: "e.g. night shift, cold calling" }),
      match_threshold: h("input", { type: "number", min: 50, max: 100, step: 1, value: p.match_threshold }),
      near_match_margin: h("input", { type: "number", min: 0, max: 30, value: p.near_match_margin }),
      max_job_age_days: h("input", { type: "number", min: 1, max: 365, value: p.max_job_age_days }),
    };
    const checks = (key, opts) => h("div", { class: "row", role: "group", "aria-label": key }, opts.map(([v, l]) => h("label", { class: "check" },
      h("input", { type: "checkbox", value: v, "data-group": key, checked: (p[key] || []).includes(v) }), l)));
    const flag = (key, label, val) => h("label", { class: "check" }, h("input", { type: "checkbox", "data-flag": key, checked: !!val }), label);
    const lab = (k, label, hint) => { F[k].id = `p-${k}`; return h("label", { class: "field", for: F[k].id }, label, hint ? h("span", { class: "hint" }, hint) : null, F[k]); };
    const err = h("div", { role: "alert" });
    const form = h("div", { class: "stack" },
      section("What you're looking for", h("div", { class: "form-grid" },
        lab("target_roles", "Target roles", "Comma-separated. Used for discovery relevance and role match."), lab("industries", "Industries"),
        lab("locations", "Preferred locations", "Leave empty for no location restriction."), lab("work_authorization_countries", "Authorised to work in", "Used for work-authorisation hard gates."),
        h("div", { class: "field full" }, "Work modes", checks("work_modes", [["remote", "Remote"], ["hybrid", "Hybrid"], ["onsite", "On-site"]])),
        h("div", { class: "field full" }, "Employment types", checks("employment_types", [["full-time", "Full-time"], ["part-time", "Part-time"], ["contract", "Contract"], ["internship", "Internship"], ["temporary", "Temporary"], ["freelance", "Freelance"]])),
        h("div", { class: "field full" }, "Seniority (leave all unticked for any)", checks("seniority", [["intern", "Intern"], ["entry", "Entry"], ["mid", "Mid"], ["senior", "Senior"], ["lead", "Lead / staff"], ["director", "Director"]])),
        lab("salary_min", "Minimum salary", "Jobs stating a lower maximum fail the salary gate. Unstated salaries are never guessed."), lab("salary_currency", "Currency"),
        h("div", { class: "field full" }, flag("willing_to_relocate", "I'm willing to relocate", p.willing_to_relocate)))),
      section("Exclusions (hard gates)", h("div", { class: "form-grid" },
        lab("excluded_companies", "Excluded employers"), lab("excluded_keywords", "Excluded keywords"),
        h("div", { class: "field full" }, "Always reject postings that…",
          h("div", { class: "row" }, flag("ex_fees", "ask candidates to pay a fee", p.exclusions.fees), flag("ex_unpaid", "are unpaid", p.exclusions.unpaid),
            flag("ex_commission_only", "are commission-only", p.exclusions.commission_only), flag("ex_suspicious_data", "request sensitive personal data", p.exclusions.suspicious_data),
            flag("ex_unverifiable", "have an unidentifiable employer (→ needs verification)", p.exclusions.unverifiable))))),
      section("Recommendation criteria", h("div", { class: "stack" },
        callout("info", "A job is recommended only if every hard gate passes AND the resume match is at or above this threshold. There is no daily quota; zero results is valid."),
        h("div", { class: "form-grid" }, lab("match_threshold", "Match threshold (%)", "Default 85. Changing it is logged."), lab("near_match_margin", "Near-match band (points below threshold)"),
          lab("max_job_age_days", "Maximum posting age (days)"), h("div", { class: "field" }, "Outreach", flag("allow_follow_up", "Allow one follow-up draft per contact & job", p.allow_follow_up))))),
      err,
      h("div", { class: "row", style: { justifyContent: "flex-end" } },
        h("a", { class: "btn btn-ghost", href: "/api/preferences/profile.yml", target: "_blank", rel: "noopener" }, "View profile.yml"),
        h("button", { class: "btn", onclick: async (e) => withBusy(e.currentTarget, async () => { if (!(await confirmDialog({ title: "Import profile.yml?", message: "Replace current preferences with the contents of data/profile.yml (if you edited the file by hand).", confirmLabel: "Import" }))) return; try { await post("/api/preferences/import"); toast("Imported", "success"); paint(); } catch (er) { toast(er.message, "error"); } }) }, "Import from file"),
        h("button", { class: "btn btn-primary", onclick: async (e) => withBusy(e.currentTarget, async () => {
          clear(err);
          const groups = (k) => [...form.querySelectorAll(`input[data-group="${k}"]:checked`)].map((i) => i.value);
          const fl = (k) => form.querySelector(`input[data-flag="${k}"]`).checked;
          const body = {
            target_roles: F.target_roles.value, industries: F.industries.value, locations: F.locations.value, work_authorization_countries: F.work_authorization_countries.value,
            salary_min: F.salary_min.value === "" ? null : F.salary_min.value, salary_currency: F.salary_currency.value, excluded_companies: F.excluded_companies.value,
            excluded_keywords: F.excluded_keywords.value, match_threshold: F.match_threshold.value, near_match_margin: F.near_match_margin.value, max_job_age_days: F.max_job_age_days.value,
            work_modes: groups("work_modes"), employment_types: groups("employment_types"), seniority: groups("seniority"),
            willing_to_relocate: fl("willing_to_relocate"), allow_follow_up: fl("allow_follow_up"),
            exclusions: { fees: fl("ex_fees"), unpaid: fl("ex_unpaid"), commission_only: fl("ex_commission_only"), suspicious_data: fl("ex_suspicious_data"), unverifiable: fl("ex_unverifiable") },
          };
          if (!body.work_modes.length) { mount(err, callout("danger", "Select at least one work mode.")); return; }
          if (Number(body.match_threshold) !== Number(p.match_threshold)) {
            const lower = Number(body.match_threshold) < Number(p.match_threshold);
            if (!(await confirmDialog({ title: "Change the match threshold?", message: `From ${p.match_threshold}% to ${body.match_threshold}%. ${lower ? "Lowering it will recommend weaker matches. " : ""}All jobs will be re-evaluated and the change is recorded in the audit log.`, confirmLabel: "Change threshold", danger: lower }))) return;
          }
          try { const r = await put("/api/preferences", body); if (r.task) ctx.trackTask(r.task.id, "Re-evaluating jobs"); toast("Preferences saved" + (r.task ? " — re-evaluating jobs" : ""), "success"); ctx.refreshCounts(); }
          catch (er) { mount(err, callout("danger", er.message)); }
        }) }, "Save preferences")));
    return form;
  }

  // ---------------------------------------------------------------- sources
  async function sourcesTab() {
    const [companies, runs] = await Promise.all([get("/api/companies"), get("/api/discovery/runs")]);
    const sch = runs.schedule;
    const enabled = h("input", { type: "checkbox", id: "s-en", checked: sch.enabled });
    const hour = h("select", { id: "s-hour" }, Array.from({ length: 24 }, (_, i) => h("option", { value: i, selected: Number(sch.hour) === i }, `${String(i).padStart(2, "0")}:00`)));
    return h("div", { class: "stack" },
      section("Target companies (portals.yml)", h("div", { class: "stack" },
        h("p", { class: "small text-2" }, "Discovery uses official public job-board APIs where available (Greenhouse, Lever, Ashby), the Remotive API (search term), or links to those boards found on a career page. Sites' terms and rate limits are respected; logins and CAPTCHAs are never bypassed."),
        dataTable({ rows: companies, onRow: (c) => companyDialog(c),
          empty: emptyState({ icon: "globe", title: "No sources yet", body: "Add companies you want to work for. Find the board token in their careers URL, e.g. boards.greenhouse.io/<token>." }),
          columns: [{ key: "name", label: "Company" }, { key: "ats", label: "Type" }, { key: "token", label: "Board token / search", render: (c) => c.token || c.careers_url || "—" },
            { key: "enabled", label: "Status", render: (c) => c.enabled ? badge("pass", "Enabled") : h("span", { class: "badge b-neutral" }, "Disabled") }] }),
        h("div", { class: "row" }, h("button", { class: "btn btn-primary", onclick: () => companyDialog() }, icon("plus", 16), "Add company"),
          h("a", { class: "btn btn-ghost", href: "/api/companies/portals.yml", target: "_blank", rel: "noopener" }, "View portals.yml"),
          h("button", { class: "btn btn-ghost", onclick: async (e) => withBusy(e.currentTarget, async () => { if (!(await confirmDialog({ title: "Import portals.yml?", message: "Replace the registry with the contents of data/portals.yml.", confirmLabel: "Import" }))) return; try { const r = await post("/api/companies/import"); toast(`Imported ${r.imported} companies`, "success"); paint(); } catch (er) { toast(er.message, "error"); } }) }, "Import from file")))),
      section("Daily discovery schedule", h("div", { class: "stack" },
        h("label", { class: "check", for: "s-en" }, enabled, "Run discovery once a day"), h("label", { class: "field", for: "s-hour", style: { maxWidth: "200px" } }, "Time (server local time)", hour),
        h("p", { class: "xs muted" }, "Runs while JobPilot is running. On free hosting that sleeps when idle, the run happens the next time the app is awake after this hour."),
        h("button", { class: "btn btn-primary", style: { alignSelf: "flex-start" }, onclick: async (e) => withBusy(e.currentTarget, async () => { try { await put("/api/settings/schedule", { enabled: enabled.checked, hour: Number(hour.value) }); toast("Schedule saved", "success"); } catch (er) { toast(er.message, "error"); } }) }, "Save schedule"))));
  }

  function companyDialog(c = {}) {
    const name = h("input", { type: "text", id: "c-name", value: c.name || "" });
    const ats = h("select", { id: "c-ats" }, [["greenhouse", "Greenhouse board"], ["lever", "Lever board"], ["ashby", "Ashby board"], ["remotive", "Remotive (remote jobs search)"], ["career_page", "Career page URL"]].map(([v, l]) => h("option", { value: v, selected: c.ats === v }, l)));
    const token = h("input", { type: "text", id: "c-token", value: c.token || "", placeholder: "e.g. stripe (from boards.greenhouse.io/stripe)" });
    const url = h("input", { type: "url", id: "c-url", value: c.careers_url || "", placeholder: "https://company.com/careers" });
    const en = h("input", { type: "checkbox", id: "c-en", checked: c.enabled !== 0 });
    const notes = h("input", { type: "text", id: "c-notes", value: c.notes || "" });
    const err = h("div");
    const save = h("button", { class: "btn btn-primary" }, "Save");
    const m = modal({ title: c.id ? "Edit company" : "Add company", body: h("div", { class: "stack" },
      h("label", { class: "field", for: "c-name" }, "Company name *", name), h("label", { class: "field", for: "c-ats" }, "Source type", ats),
      h("label", { class: "field", for: "c-token" }, "Board token / search term", h("span", { class: "hint" }, "Required for Greenhouse, Lever, Ashby and Remotive"), token),
      h("label", { class: "field", for: "c-url" }, "Official careers page URL", url), h("label", { class: "field", for: "c-notes" }, "Notes", notes),
      h("label", { class: "check", for: "c-en" }, en, "Enabled for discovery"), err),
      footer: [c.id ? h("button", { class: "btn btn-danger", onclick: async () => { if (await confirmDialog({ title: `Remove ${c.name}?`, message: "Jobs already found are kept.", confirmLabel: "Remove", danger: true })) { try { await del(`/api/companies/${c.id}`); m.close(); paint(); } catch (e) { toast(e.message, "error"); } } } }, "Remove") : null,
        h("span", { class: "grow" }), h("button", { class: "btn", onclick: () => m.close() }, "Cancel"), save] });
    save.addEventListener("click", () => withBusy(save, async () => {
      const b = { name: name.value.trim(), ats: ats.value, token: token.value.trim() || null, careers_url: url.value.trim() || null, enabled: en.checked, notes: notes.value };
      try { c.id ? await put(`/api/companies/${c.id}`, b) : await post("/api/companies", b); m.close(); toast("Saved", "success"); paint(); } catch (e) { mount(err, callout("danger", e.message)); }
    }));
  }

  // ---------------------------------------------------------------- AI & budget
  async function aiTab() {
    const s = await get("/api/settings");
    const a = s.ai, st = s.ai_status, u = s.usage;
    const en = h("input", { type: "checkbox", id: "ai-en", checked: a.enabled, disabled: !st.key_configured });
    const consent = h("input", { type: "checkbox", id: "ai-consent", checked: a.consent_profile_data });
    const daily = h("input", { type: "number", id: "ai-daily", min: 0, max: 300, step: "0.5", value: a.daily_cap_inr });
    const monthly = h("input", { type: "number", id: "ai-monthly", min: 0, max: 300, step: "1", value: a.monthly_cap_inr });
    const calls = h("input", { type: "number", id: "ai-calls", min: 0, max: 5000, value: a.daily_call_limit });
    const testOut = h("div");
    return h("div", { class: "split" },
      h("div", { class: "stack" },
        section("Provider configuration", h("div", { class: "stack small" },
          h("div", { class: "row-between" }, h("span", null, "Selected provider (AI_PROVIDER)"), h("strong", null, st.provider)),
          h("div", { class: "row-between" }, h("span", null, "Model"), h("span", null, st.model || "—")),
          Object.entries(s.providers).map(([k, v]) => h("div", { class: "row-between" }, h("span", null, `${k} — `, h("code", null, v.env)), v.configured ? badge("pass", "Key present") : h("span", { class: "badge b-neutral" }, "Not set"))),
          callout("info", "API keys live only in your local .env file (or the hosting provider's secret settings). They are never shown here, sent to the browser, logged or committed. See REQUIRED_USER_ACTIONS.md."),
          h("button", { class: "btn", style: { alignSelf: "flex-start" }, disabled: !st.key_configured, onclick: async (e) => withBusy(e.currentTarget, async () => { const r = await post("/api/settings/ai/test"); mount(testOut, callout(r.ok ? "success" : "danger", r.message)); }) }, "Test connection"), testOut)),
        section("Usage", h("div", { class: "stack small" },
          h("div", { class: "row-between" }, "Today", `${fmt.inr(u.today_cost_inr)} / ${fmt.inr(u.daily_cap_inr)} · ${u.today_calls} / ${u.daily_call_limit} calls`), meter(u.today_calls, u.daily_call_limit || 1),
          h("div", { class: "row-between" }, "This month", `${fmt.inr(u.month_cost_inr)} / ${fmt.inr(u.monthly_cap_inr)}`), meter(u.month_cost_inr, u.monthly_cap_inr || 1),
          u.paused ? callout("warn", h("div", null, `AI paused: ${u.pause_reason}. `, h("button", { class: "btn btn-sm", onclick: async () => { await put("/api/settings/ai", { resume: true }); toast("AI resumed", "success"); paint(); } }, "Resume"))) : null))),
      section("Controls", h("div", { class: "stack" },
        h("label", { class: "check", for: "ai-en" }, en, "Use AI to refine job-requirement interpretation"),
        !st.key_configured ? h("p", { class: "xs muted" }, "Disabled: no provider key configured. Everything works without AI using the deterministic engine.") : null,
        h("label", { class: "check", for: "ai-consent" }, consent, "I consent to sending resume/profile data to the AI provider"),
        h("p", { class: "xs muted" }, "Current AI features only send job-description text — never your resume. This consent is recorded for future features and can be withdrawn any time."),
        h("div", { class: "form-grid" }, h("label", { class: "field", for: "ai-daily" }, "Daily cap (₹)", daily), h("label", { class: "field", for: "ai-monthly" }, "Monthly cap (₹, max 300)", monthly),
          h("label", { class: "field", for: "ai-calls" }, "Daily call limit", calls)),
        h("p", { class: "xs muted" }, "When a cap is reached, AI pauses automatically and processing continues deterministically. Responses are cached and retries are bounded."),
        h("button", { class: "btn btn-primary", style: { alignSelf: "flex-start" }, onclick: async (e) => withBusy(e.currentTarget, async () => {
          try { await put("/api/settings/ai", { enabled: en.checked, consent_profile_data: consent.checked, daily_cap_inr: Number(daily.value), monthly_cap_inr: Number(monthly.value), daily_call_limit: Number(calls.value) }); toast("AI & budget settings saved", "success"); paint(); }
          catch (er) { toast(er.message, "error"); }
        }) }, "Save"))));
  }

  // ---------------------------------------------------------------- appearance
  async function appearanceTab() {
    const s = await get("/api/settings");
    const cur = getTheme();
    const themeBtns = h("div", { class: "segmented", role: "group", "aria-label": "Theme" }, [["light", "Light"], ["dark", "Dark"], ["system", "System"]].map(([v, l]) =>
      h("button", { "aria-pressed": String(cur === v), onclick: () => { setTheme(v); toast(`Theme: ${l}`, "info", { timeout: 1500 }); paint(); } }, l)));
    const inApp = h("input", { type: "checkbox", id: "n-app", checked: s.notifications.in_app !== false });
    const toasts = h("input", { type: "checkbox", id: "n-toast", checked: s.notifications.toasts !== false });
    return h("div", { class: "split" },
      section("Theme", h("div", { class: "stack" }, themeBtns, h("p", { class: "xs muted" }, "Saved on this device."))),
      section("Notifications", h("div", { class: "stack" },
        h("label", { class: "check", for: "n-app" }, inApp, "In-app notifications (bell) for new qualifying jobs, finished runs and documents"),
        h("label", { class: "check", for: "n-toast" }, toasts, "Pop-up confirmations (errors are always shown)"),
        h("p", { class: "xs muted" }, "Important failures are always recorded in Activity & Logs."),
        h("button", { class: "btn btn-primary", style: { alignSelf: "flex-start" }, onclick: async (e) => withBusy(e.currentTarget, async () => {
          await put("/api/settings/notifications", { in_app: inApp.checked, toasts: toasts.checked });
          try { localStorage.setItem("jobpilot.notif", JSON.stringify({ toasts: toasts.checked })); } catch { /* ignore */ }
          toast("Notification settings saved", "success");
        }) }, "Save"))));
  }

  // ---------------------------------------------------------------- data & privacy
  async function dataTab() {
    const s = await get("/api/settings");
    return h("div", { class: "split" },
      section("Storage", h("div", { class: "stack small" },
        h("p", null, "SQLite is the authoritative store. Human-readable companions are generated from validated records:"),
        h("ul", { class: "evidence-list" }, s.storage.files.map((f) => h("li", null, h("code", null, f)))),
        h("div", null, h("span", { class: "muted" }, "Location: "), s.storage.data_dir),
        h("div", null, h("span", { class: "muted" }, "Environment: "), s.storage.environment))),
      section("Your data", h("div", { class: "stack" },
        h("p", { class: "small text-2" }, "Export a ZIP with the database snapshot, uploads, generated documents, reports and audit logs (browser profile excluded)."),
        h("a", { class: "btn", href: "/api/data/export", download: "", style: { alignSelf: "flex-start" } }, icon("download", 16), "Export all data"),
        h("hr", { style: { border: 0, borderTop: "1px solid var(--border)", width: "100%" } }),
        h("p", { class: "small text-2" }, "Permanently delete everything: profile, uploads, jobs, resumes, applications, contacts, settings, logs and the local browser profile."),
        h("button", { class: "btn btn-danger", style: { alignSelf: "flex-start" }, onclick: async () => {
          const ok = await confirmDialog({ title: "Delete all data?", message: "This cannot be undone. Export first if you want a copy.", confirmLabel: "Delete everything", danger: true, typed: "DELETE ALL MY DATA" });
          if (!ok) return;
          try { await post("/api/data/delete-all", { confirm: "DELETE ALL MY DATA" }); toast("All data deleted", "success"); ctx.navigate("/setup"); ctx.refreshCounts(); } catch (e) { toast(e.message, "error"); }
        } }, icon("trash", 16), "Delete all my data"))));
  }
}
