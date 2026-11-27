// Admin pages. Every number shown comes from the admin API (real stored data); empty data shows "No data yet".
import { h, icon, mount, clear, fmt } from "../ui/dom.js";
import { api, get, post, put, del } from "../api.js";
import { badge, callout, confirmDialog, dataTable, emptyState, loading, meter, modal, pagination, toast, withBusy } from "../ui/components.js";
import { barChart } from "../ui/charts.js";

const head = (title, desc, actions) => h("div", { class: "page-head" }, h("div", null, h("h1", null, title), desc ? h("p", null, desc) : null),
  actions ? h("div", { class: "page-actions" }, actions) : null);
const card = (title, body, actions) => h("section", { class: "card" }, h("div", { class: "card-head" }, h("h2", null, title), actions ? h("div", { class: "row" }, actions) : null),
  h("div", { class: "card-body" }, body));
const num = (v) => (v === null || v === undefined ? "No data yet" : typeof v === "number" ? v.toLocaleString("en-IN") : v);
const inr = (v) => `₹${Number(v || 0).toLocaleString("en-IN", { maximumFractionDigits: 2 })}`;
const field = (label, el, hint) => { el.id = el.id || `f${Math.random().toString(36).slice(2, 8)}`; return h("label", { class: "field", for: el.id }, label, hint ? h("span", { class: "hint" }, hint) : null, el); };
const writeOK = (ctx) => ctx.role !== "support";
const act = (btn, fn, ok) => withBusy(btn, async () => { try { const r = await fn(); if (ok) toast(ok, "success"); return r; } catch (e) { toast(e.message, "error"); throw e; } });

function kpi(label, cur, prev, { money = false, pct = false } = {}) {
  const v = cur === null || cur === undefined ? null : cur;
  let delta = null, cls = "flat";
  if (typeof v === "number" && typeof prev === "number") {
    const d = v - prev;
    cls = d > 0 ? "up" : d < 0 ? "down" : "flat";
    delta = `${d > 0 ? "▲" : d < 0 ? "▼" : "•"} ${prev === 0 ? (d === 0 ? "no change" : "new") : `${Math.abs(Math.round((d / prev) * 100))}%`} vs previous period`;
  }
  return h("div", { class: "card stat kpi" }, h("span", { class: "label" }, label),
    h("span", { class: "value" }, v === null ? "—" : money ? inr(v) : pct ? `${v}%` : num(v)),
    h("span", { class: `delta ${cls}` }, v === null ? "No data yet" : delta || ""));
}

// ================================================================ overview
async function overview(ctx) {
  const st = { from: ctx.query.get("from") || "", to: ctx.query.get("to") || "", g: ctx.query.get("g") || "day" };
  const body = h("div");
  const fromI = h("input", { type: "date", value: st.from, "aria-label": "From" });
  const toI = h("input", { type: "date", value: st.to, "aria-label": "To" });
  const gS = h("select", { "aria-label": "Granularity" }, ["day", "week", "month", "year"].map((g) => h("option", { value: g, selected: st.g === g }, fmt.cap(g))));
  const apply = () => { st.from = fromI.value; st.to = toI.value; st.g = gS.value; history.replaceState({}, "", `?from=${st.from}&to=${st.to}&g=${st.g}`); load(); };
  [fromI, toI, gS].forEach((el) => el.addEventListener("change", apply));
  async function load() {
    mount(body, loading(4));
    const r = await get(`/api/admin/overview?${new URLSearchParams({ ...(st.from && { from: st.from }), ...(st.to && { to: st.to }), granularity: st.g })}`);
    if (!ctx.isCurrent()) return;
    const c = r.current, p = r.previous, s = r.snapshot;
    const series = (rows) => rows.map((x) => ({ label: x.k, value: x.n || 0 }));
    mount(body,
      h("p", { class: "small text-2 mb-2" }, `Showing ${r.range.from} → ${r.range.to.slice(0, 10)} (exclusive), compared with ${r.range.previous_from} → ${r.range.previous_to}.`),
      h("div", { class: "grid grid-4 mb-4" }, kpi("Total users", s.total_users), kpi("New users", c.new_users, p.new_users), kpi("Active users", c.active_users, p.active_users), kpi("Suspended", s.suspended_users)),
      h("div", { class: "grid grid-4 mb-4" }, kpi("In trial", s.users_in_trial), kpi("Trial expirations", c.trial_expirations, p.trial_expirations),
        kpi("Paid subscribers", s.paid_subscribers), kpi("Free users", s.free_users)),
      h("div", { class: "grid grid-4 mb-4" }, kpi("Upgrades", c.upgrades, p.upgrades), kpi("Cancellations", c.cancellations, p.cancellations),
        kpi("Trial conversion", c.trial_conversion_rate, p.trial_conversion_rate, { pct: true }), kpi("Revenue collected", c.revenue_collected, p.revenue_collected, { money: true })),
      h("div", { class: "grid grid-4 mb-4" }, kpi("Refunds", c.refunds, p.refunds, { money: true }), kpi("Failed payments", c.failed_payments, p.failed_payments),
        h("div", { class: "card stat kpi" }, h("span", { class: "label" }, "MRR (projected)"), h("span", { class: "value" }, inr(r.mrr_projected)), h("span", { class: "delta flat" }, "From locked prices — not collected revenue")),
        kpi("System errors", c.system_errors, p.system_errors)),
      h("div", { class: "grid grid-4 mb-4" }, kpi("Job searches", c.job_searches, p.job_searches), kpi("Jobs viewed", c.jobs_viewed, p.jobs_viewed),
        kpi("Resume analyses", c.resume_analyses, p.resume_analyses), kpi("Resume generations", c.resume_generations, p.resume_generations)),
      h("div", { class: "grid grid-4 mb-4" }, kpi("Applications prepared", c.applications_prepared, p.applications_prepared), kpi("Interviews reported", c.interviews_reported, p.interviews_reported),
        kpi("Offers reported", c.offers_reported, p.offers_reported), kpi("AI calls · cost", c.api_calls, p.api_calls)),
      h("div", { class: "grid grid-2 mb-4" },
        barChart({ title: "Registrations", data: series(r.series.registrations), valueLabel: "Users" }),
        barChart({ title: "Active users", data: series(r.series.active_users), valueLabel: "Users" }),
        barChart({ title: "Job searches", data: series(r.series.job_searches), valueLabel: "Searches" }),
        barChart({ title: "Verified revenue (₹)", data: series(r.series.revenue), valueLabel: "₹", emptyText: "No verified payments yet — payment processing is disabled." })),
      h("div", { class: "grid grid-2" },
        barChart({ title: "Feature usage", horizontal: true, data: r.feature_usage.map((f) => ({ label: f.feature, value: f.n })), valueLabel: "Events" }),
        card("System health", h("div", { class: "stack small" }, h("div", null, "Database: ", badge("pass", "OK")), h("div", null, `Uptime: ${Math.round(r.health.uptime_seconds / 3600)} h`),
          h("div", null, `Errors (24 h): ${r.health.errors_24h}`), h("div", null, `Last backup: ${r.health.last_backup ? fmt.dateTime(r.health.last_backup) : "never"}`),
          h("a", { href: "/admin/health" }, "Full health report")))));
  }
  mount(ctx.root, head("Overview", "Platform metrics computed from stored data. Revenue counts only verified payments.",
    [h("label", { class: "row small" }, "From", fromI), h("label", { class: "row small" }, "To", toI), gS,
      h("a", { class: "btn btn-sm", href: "/api/admin/users/export.csv", download: "" }, icon("download", 14), "Export users")]), body);
  await load();
}

// ================================================================ users
async function users(ctx) {
  if (ctx.params[0]) return userDetail(ctx, Number(ctx.params[0]));
  const st = { q: "", status: "", role: "", plan: "", sort: "created", page: 1 };
  const listEl = h("div");
  const q = h("input", { type: "search", placeholder: "Email, name or ID", "aria-label": "Search users" });
  const status = h("select", { "aria-label": "Status" }, [["", "Any status"], ["active", "Active"], ["suspended", "Suspended"]].map(([v, l]) => h("option", { value: v }, l)));
  const role = h("select", { "aria-label": "Role" }, [["", "Any role"], ["user", "User"], ["support", "Support"], ["admin", "Admin"], ["owner", "Owner"]].map(([v, l]) => h("option", { value: v }, l)));
  const plan = h("select", { "aria-label": "Plan" }, h("option", { value: "" }, "Any plan"));
  const sort = h("select", { "aria-label": "Sort" }, [["created", "Newest"], ["last_login", "Last login"], ["email", "Email"], ["name", "Name"]].map(([v, l]) => h("option", { value: v }, l)));
  let t;
  q.addEventListener("input", () => { clearTimeout(t); t = setTimeout(() => { st.q = q.value.trim(); st.page = 1; load(); }, 300); });
  [[status, "status"], [role, "role"], [plan, "plan"], [sort, "sort"]].forEach(([el, k]) => el.addEventListener("change", () => { st[k] = el.value; st.page = 1; load(); }));
  async function load() {
    mount(listEl, loading(3));
    const r = await get(`/api/admin/users?${new URLSearchParams({ q: st.q, status: st.status, role: st.role, plan: st.plan, sort: st.sort, page: st.page })}`);
    if (plan.options.length === 1) r.plans.concat(["Trial", "Staff"]).forEach((p) => plan.append(h("option", { value: p }, p)));
    mount(listEl, h("div", { class: "card" }, dataTable({ rows: r.items, onRow: (u) => ctx.navigate(`/admin/users/${u.id}`),
      empty: emptyState({ icon: "users", title: "No users match", body: "Try different filters." }),
      columns: [{ key: "id", label: "ID" }, { key: "email", label: "Email", render: (u) => h("div", null, h("strong", null, u.email), u.name ? h("div", { class: "xs muted" }, u.name) : null) },
        { key: "role", label: "Role" }, { key: "status", label: "Status", render: (u) => badge(u.status === "active" ? "pass" : "fail", fmt.cap(u.status)) },
        { key: "plan", label: "Plan" }, { key: "trial_ends_at", label: "Trial ends", render: (u) => u.trial_status === "active" ? fmt.date(u.trial_ends_at) : "—" },
        { key: "created_at", label: "Registered", render: (u) => fmt.date(u.created_at) }, { key: "last_login_at", label: "Last login", render: (u) => u.last_login_at ? fmt.ago(u.last_login_at) : "Never" },
        { key: "job_searches", label: "Searches" }, { key: "resume_generations", label: "Resumes" }, { key: "applications", label: "Apps" }, { key: "offers", label: "Offers" }] })),
      pagination({ page: st.page, pageSize: 25, total: r.total, onPage: (p) => { st.page = p; load(); } }));
  }
  mount(ctx.root, head("Users", "Search, filter and manage accounts. Passwords and tokens are never shown.", [h("a", { class: "btn btn-sm", href: "/api/admin/users/export.csv", download: "" }, icon("download", 14), "CSV")]),
    h("div", { class: "card card-pad row mb-4" }, h("div", { class: "grow", style: { minWidth: "200px" } }, q), status, role, plan, sort), listEl);
  await load();
}

async function userDetail(ctx, id) {
  const r = await get(`/api/admin/users/${id}`);
  const u = r.user;
  const plans = (await get("/api/admin/plans")).plans;
  const action = async (btn, body, confirmMsg, typed) => {
    if (confirmMsg && !(await confirmDialog({ title: "Please confirm", message: confirmMsg, confirmLabel: "Confirm", danger: true, typed }))) return;
    const res = await act(btn, () => post(`/api/admin/users/${id}/action`, typed ? { ...body, confirm: typed } : body), "Done");
    if (res?.result?.link) modal({ title: "Password reset link", body: h("div", { class: "stack" }, callout("warn", res.result.note), h("code", { style: { wordBreak: "break-all" } }, res.result.link)) });
    if (body.action === "delete") ctx.navigate("/admin/users"); else userDetail(ctx, id);
  };
  const planSel = h("select", null, plans.map((p) => h("option", { value: p.code }, p.name)));
  const days = h("input", { type: "number", min: 1, max: 3650, value: 30, style: { width: "90px" } });
  const roleSel = h("select", null, ["user", "support", "admin"].map((x) => h("option", { value: x, selected: u.role === x }, x)));
  const ovKey = h("select", null, r.usage.limits.map((l) => h("option", { value: l.key }, l.label)), r.usage.features.map((f) => h("option", { value: f.key }, `Feature: ${f.label}`)));
  const ovVal = h("input", { type: "text", placeholder: "number, unlimited, true/false", style: { width: "190px" } });
  const ovReason = h("input", { type: "text", placeholder: "Reason (required for audit)" });
  const W = writeOK(ctx);
  mount(ctx.root,
    h("nav", { class: "breadcrumbs" }, h("a", { href: "/admin/users" }, "Users"), icon("chevronRight", 12), h("span", null, u.email)),
    head(u.name || u.email, `${u.email} · ID ${u.id} · ${u.role} · ${u.status}`),
    h("div", { class: "grid grid-4 mb-4" }, h("div", { class: "card stat" }, h("span", { class: "label" }, "Plan"), h("span", { class: "value", style: { fontSize: "20px" } }, u.plan)),
      h("div", { class: "card stat" }, h("span", { class: "label" }, "Trial"), h("span", { class: "value", style: { fontSize: "20px" } }, r.trial ? `${fmt.cap(r.trial.status)}${r.trial.status === "active" ? ` · ${r.trial.days_remaining}d` : ""}` : "Not started"),
        r.trial ? h("span", { class: "hint" }, `${fmt.date(r.trial.started_at)} → ${fmt.date(r.trial.ends_at)}`) : null),
      h("div", { class: "card stat" }, h("span", { class: "label" }, "Registered"), h("span", { class: "value", style: { fontSize: "20px" } }, fmt.date(u.created_at)), h("span", { class: "hint" }, `Last login ${u.last_login_at ? fmt.ago(u.last_login_at) : "never"}`)),
      h("div", { class: "card stat" }, h("span", { class: "label" }, "Outcomes (user-reported)"), h("span", { class: "value", style: { fontSize: "20px" } }, `${r.stats.interviews} interviews · ${r.stats.offers} offers`))),
    h("div", { class: "split-wide" },
      h("div", { class: "stack" },
        card("Usage", h("div", null, r.usage.limits.map((l) => h("div", { class: "usage-row" }, h("strong", { class: "small" }, l.label),
          l.limit === null ? h("span", { class: "xs muted" }, "Unlimited") : meter(l.used, l.limit || 1), h("span", { class: "small" }, l.limit === null ? `${l.used}` : `${l.used}/${l.limit}`))))),
        card("Activity timeline", r.timeline.length ? h("ul", { class: "timeline" }, r.timeline.map((e) => h("li", null, h("span", { class: "when" }, fmt.dateTime(e.at)),
          h("span", null, h("strong", null, e.event), e.feature ? ` · ${e.feature}` : "", ` · ${e.actor === "system" ? "JobPilot (automatic)" : "user"}`, e.success ? "" : h("span", { class: "badge b-danger", style: { marginLeft: "6px" } }, e.error_category || "failed")))))
          : h("p", { class: "muted" }, "No activity recorded yet.")),
        card("Subscriptions", r.subscriptions.length ? dataTable({ rows: r.subscriptions, columns: [{ key: "plan_name", label: "Plan" }, { key: "status", label: "Status" }, { key: "source", label: "Source" },
          { key: "started_at", label: "Started", render: (s) => fmt.date(s.started_at) }, { key: "ends_at", label: "Ends", render: (s) => s.ends_at ? fmt.date(s.ends_at) : "No end" }] }) : h("p", { class: "muted" }, "No subscriptions — uses trial or Free plan."))),
      h("div", { class: "stack" },
        W ? card("Account actions", h("div", { class: "stack" },
          h("div", { class: "row" }, u.status === "active" ? h("button", { class: "btn btn-sm btn-danger", onclick: (e) => action(e.currentTarget, { action: "suspend" }, `Suspend ${u.email}? They'll be signed out immediately.`) }, "Suspend")
            : h("button", { class: "btn btn-sm", onclick: (e) => action(e.currentTarget, { action: "reactivate" }) }, "Reactivate"),
            h("button", { class: "btn btn-sm", onclick: (e) => action(e.currentTarget, { action: "reset_sessions" }, "Sign this user out everywhere?") }, "Sign out everywhere"),
            h("button", { class: "btn btn-sm", onclick: (e) => action(e.currentTarget, { action: "password_reset_link" }) }, "Password reset link"),
            h("button", { class: "btn btn-sm", onclick: (e) => action(e.currentTarget, { action: "reset_usage" }, "Reset this user's usage counters?") }, "Reset usage")),
          h("div", { class: "row" }, h("span", { class: "small" }, "Days"), days,
            h("button", { class: "btn btn-sm", onclick: (e) => action(e.currentTarget, { action: "extend_trial", days: Number(days.value) }) }, "Extend trial"),
            h("button", { class: "btn btn-sm", onclick: (e) => action(e.currentTarget, { action: "revoke_trial" }, "End this user's trial now? They move to the Free plan.") }, "End trial")),
          h("div", { class: "row" }, planSel,
            h("button", { class: "btn btn-sm", onclick: (e) => action(e.currentTarget, { action: "change_plan", plan_code: planSel.value, days: Number(days.value) }, `Grant ${planSel.value} for ${days.value} days (no payment)?`) }, "Grant plan for days"),
            h("button", { class: "btn btn-sm", onclick: (e) => action(e.currentTarget, { action: "grant_free", plan_code: planSel.value }, `Grant ${planSel.value} free with no end date?`) }, "Grant free access"),
            h("button", { class: "btn btn-sm", onclick: (e) => action(e.currentTarget, { action: "grant_lifetime", plan_code: planSel.value }, `Grant lifetime ${planSel.value}?`) }, "Grant lifetime"),
            h("button", { class: "btn btn-sm btn-danger", onclick: (e) => action(e.currentTarget, { action: "revoke_access" }, "Revoke this user's paid/granted access? They fall back to trial or Free.") }, "Revoke access")),
          ctx.role === "owner" ? h("div", { class: "row" }, "Role", roleSel, h("button", { class: "btn btn-sm", onclick: (e) => action(e.currentTarget, { action: "set_role", role: roleSel.value }, `Change role to ${roleSel.value}? Grants/removes staff access.`) }, "Set role"),
            h("button", { class: "btn btn-sm", onclick: (e) => action(e.currentTarget, { action: "reset_2fa" }, "Reset this user's two-factor authentication?") }, "Reset 2FA")) : null,
          h("button", { class: "btn btn-sm btn-danger", style: { alignSelf: "flex-start" }, onclick: (e) => action(e.currentTarget, { action: "delete" }, `Permanently delete ${u.email} and all their data? This cannot be undone.`, `DELETE ${u.email}`) }, icon("trash", 14), "Delete user"))) : null,
        card("Usage overrides", h("div", { class: "stack" },
          r.overrides.length ? r.overrides.map((o) => h("div", { class: "row-between small" }, h("span", null, `${o.key} = ${o.value}`, h("span", { class: "muted" }, ` (${o.reason || "no reason"})`)),
            W ? h("button", { class: "btn btn-sm btn-ghost", onclick: (e) => act(e.currentTarget, () => del(`/api/admin/users/${id}/overrides/${o.key}`), "Removed").then(() => userDetail(ctx, id)) }, "Remove") : null)) : h("p", { class: "muted small" }, "No overrides."),
          W ? h("div", { class: "row" }, ovKey, ovVal, ovReason, h("button", { class: "btn btn-sm", onclick: (e) => {
            const raw = ovVal.value.trim(); const v = raw === "unlimited" || raw === "" ? null : raw === "true" ? true : raw === "false" ? false : Number(raw);
            if (!ovReason.value.trim()) { toast("Add a reason", "warning"); return; }
            act(e.currentTarget, () => put(`/api/admin/users/${id}/overrides`, { key: ovKey.value, value: v, reason: ovReason.value.trim() }), "Override saved").then(() => userDetail(ctx, id));
          } }, "Set override")) : null)),
        card("Sessions", h("ul", { class: "evidence-list small" }, r.sessions.map((s) => h("li", null, `${fmt.dateTime(s.last_seen_at)}${s.revoked ? " (revoked)" : ""}`)))),
        r.payments.length ? card("Payments", h("ul", { class: "evidence-list small" }, r.payments.map((p) => h("li", null, `${fmt.date(p.created_at)} ${p.kind} ${inr(p.amount)} ${p.status}${p.is_manual ? " · manual" : ""}`)))) : null)));
}

// ================================================================ activity
async function activity(ctx) {
  const st = { user_id: ctx.query.get("user") || "", event: "", actor: "", success: "", page: 1 };
  const listEl = h("div");
  const ev = h("select", { "aria-label": "Event" }, h("option", { value: "" }, "All events"));
  const actor = h("select", { "aria-label": "Actor" }, [["", "Any actor"], ["user", "User actions"], ["system", "JobPilot (automatic)"]].map(([v, l]) => h("option", { value: v }, l)));
  const ok = h("select", { "aria-label": "Result" }, [["", "Success & failure"], ["1", "Success"], ["0", "Failure"]].map(([v, l]) => h("option", { value: v }, l)));
  const uid = h("input", { type: "number", placeholder: "User ID", value: st.user_id, style: { width: "120px" }, "aria-label": "User ID" });
  [[ev, "event"], [actor, "actor"], [ok, "success"], [uid, "user_id"]].forEach(([el, k]) => el.addEventListener("change", () => { st[k] = el.value; st.page = 1; load(); }));
  async function load() {
    mount(listEl, loading(3));
    const r = await get(`/api/admin/activity?${new URLSearchParams(Object.fromEntries(Object.entries(st).filter(([, v]) => v !== "")))}`);
    if (ev.options.length === 1) r.events.forEach((e) => ev.append(h("option", { value: e }, e)));
    mount(listEl, h("div", { class: "card" }, dataTable({ rows: r.items, empty: emptyState({ icon: "activity", title: "No events yet" }),
      columns: [{ key: "at", label: "Time", render: (e) => fmt.dateTime(e.at) }, { key: "email", label: "User", render: (e) => e.user_id ? h("a", { href: `/admin/users/${e.user_id}` }, e.email || `#${e.user_id}`) : "—" },
        { key: "event", label: "Event" }, { key: "feature", label: "Feature" }, { key: "actor", label: "Actor", render: (e) => e.actor === "system" ? "JobPilot" : "User" },
        { key: "success", label: "Result", render: (e) => e.success ? badge("pass", "OK") : badge("fail", e.error_category || "Failed") }, { key: "metadata", label: "Details", render: (e) => e.metadata || "" }] })),
      pagination({ page: st.page, pageSize: 50, total: r.total, onPage: (p) => { st.page = p; load(); } }));
  }
  mount(ctx.root, head("User activity", "Privacy-conscious events only — no resume text, answers, messages or keystrokes."), h("div", { class: "card card-pad row mb-4" }, uid, ev, actor, ok), listEl);
  await load();
}

// ================================================================ subscriptions & revenue
async function subscriptions(ctx) {
  const r = await get("/api/admin/subscriptions");
  mount(ctx.root, head("Subscriptions", "Paid, granted and lifetime subscriptions. Trials and Free users are not subscriptions."),
    h("div", { class: "grid grid-2 mb-4" }, barChart({ title: "Active subscriptions by plan", horizontal: true, data: r.plan_breakdown.map((p) => ({ label: p.name, value: p.n })), valueLabel: "Subscriptions" })),
    h("div", { class: "card" }, dataTable({ rows: r.items, empty: emptyState({ icon: "layers", title: "No subscriptions yet" }),
      columns: [{ key: "email", label: "User", render: (s) => h("a", { href: `/admin/users/${s.user_id}` }, s.email || `#${s.user_id}`) }, { key: "plan_name", label: "Plan" }, { key: "status", label: "Status" },
        { key: "source", label: "Source" }, { key: "locked_price", label: "Locked price", render: (s) => s.locked_price ? inr(s.locked_price) : "—" },
        { key: "started_at", label: "Started", render: (s) => fmt.date(s.started_at) }, { key: "ends_at", label: "Ends", render: (s) => s.ends_at ? fmt.date(s.ends_at) : "No end" }] })));
}

async function revenue(ctx) {
  const r = await get("/api/admin/revenue");
  const owner = ctx.role === "owner";
  const form = () => {
    const userId = h("input", { type: "number", min: 1 }); const amount = h("input", { type: "number", min: 1, step: "0.01" });
    const ref = h("input", { type: "text", placeholder: "Bank/UPI reference" }); const method = h("select", null, ["upi", "card", "netbanking", "wallet", "other"].map((m) => h("option", { value: m }, m)));
    const plan = h("select", null, h("option", { value: "" }, "No plan change"), ["basic", "pro"].map((p) => h("option", { value: p }, p)));
    const period = h("select", null, h("option", { value: "monthly" }, "Monthly"), h("option", { value: "annual" }, "Annual"));
    const attest = h("input", { type: "checkbox" }); const note = h("input", { type: "text" });
    const btn = h("button", { class: "btn btn-primary" }, "Record payment");
    const m = modal({ title: "Record a verified manual payment", wide: true, body: h("div", { class: "stack" },
      callout("warn", "Only record money you have actually received and verified. Manual records are labelled as such everywhere."),
      h("div", { class: "form-grid" }, field("User ID", userId), field("Amount (₹)", amount), field("Reference", ref), field("Method", method), field("Plan to grant", plan), field("Period", period), field("Note", note)),
      h("label", { class: "check" }, attest, "I verified this payment was received")), footer: [btn] });
    btn.addEventListener("click", () => act(btn, () => post("/api/admin/payments/manual", { user_id: Number(userId.value), amount: Number(amount.value), reference: ref.value, method: method.value,
      plan_code: plan.value || null, period: period.value, note: note.value, attest: attest.checked }), "Payment recorded").then(() => { m.close(); revenue(ctx); }).catch(() => {}));
  };
  mount(ctx.root, head("Revenue", "Actual revenue counts only verified payments. Online payment processing is disabled.", owner ? [h("button", { class: "btn btn-sm", onclick: form }, icon("plus", 14), "Record manual payment")] : null),
    callout("info", "Payment gateway: not enabled. Razorpay/Cashfree can be added later only with your credentials and explicit approval. No transactions are simulated."),
    h("div", { class: "grid grid-4 mt-4 mb-4" }, kpi("Gross collected", r.actual.gross, undefined, { money: true }), kpi("Refunds", r.actual.refunds, undefined, { money: true }),
      kpi("Net revenue", r.actual.net, undefined, { money: true }), h("div", { class: "card stat kpi" }, h("span", { class: "label" }, "MRR (projected)"), h("span", { class: "value" }, inr(r.projected.mrr)), h("span", { class: "delta flat" }, r.projected.label))),
    h("div", { class: "grid grid-2 mb-4" }, barChart({ title: "Net revenue by month", data: r.by_month.map((x) => ({ label: x.month, value: x.net })), valueLabel: "₹", emptyText: "No verified payments yet." }),
      barChart({ title: "Revenue by plan", horizontal: true, data: r.by_plan.map((x) => ({ label: x.plan, value: x.amount })), valueLabel: "₹", emptyText: "No verified payments yet." })),
    h("p", { class: "small text-2" }, `Failed payments: ${r.actual.failed_payments} · Manual records: ${r.actual.manual_records} · Cancellations (30 d): ${r.churn.cancellations_30d} · Renewals: ${r.churn.renewals}`),
    card("Payment records", dataTable({ rows: r.payments, empty: emptyState({ icon: "rupee", title: "No payment records", body: "Nothing has been collected or recorded." }),
      columns: [{ key: "created_at", label: "Date", render: (p) => fmt.date(p.created_at) }, { key: "email", label: "User" }, { key: "kind", label: "Kind" }, { key: "status", label: "Status" },
        { key: "amount", label: "Amount", render: (p) => inr(p.amount) }, { key: "is_manual", label: "Type", render: (p) => p.is_manual ? "Manual record" : p.gateway }, { key: "gateway_txn_id", label: "Reference" },
        { key: "id", label: "", sort: false, render: (p) => owner && p.kind === "charge" ? h("button", { class: "btn btn-sm btn-ghost", onclick: async (e) => {
          const amount = h("input", { type: "number", value: p.amount, min: 1 }); const ref = h("input", { type: "text" }); const attest = h("input", { type: "checkbox" });
          const b = h("button", { class: "btn btn-danger" }, "Record refund");
          const m = modal({ title: "Record a refund you issued", body: h("div", { class: "stack" }, field("Amount (₹)", amount), field("Refund reference", ref), h("label", { class: "check" }, attest, "I issued this refund")), footer: [b] });
          b.addEventListener("click", () => act(b, () => post(`/api/admin/payments/${p.id}/refund`, { amount: Number(amount.value), reference: ref.value, attest: attest.checked }), "Refund recorded").then(() => { m.close(); revenue(ctx); }).catch(() => {}));
          void e;
        } }, "Refund") : "" }] })));
}

// ================================================================ plans, trials, limits
function planEditor(ctx, p, keys) {
  const f = {
    code: h("input", { type: "text", value: p?.code || "", disabled: !!p }), name: h("input", { type: "text", value: p?.name || "" }),
    description: h("input", { type: "text", value: p?.description || "" }), monthly_price: h("input", { type: "number", min: 0, step: "1", value: p?.monthly_price ?? "", placeholder: "Leave empty = to be announced" }),
    annual_price: h("input", { type: "number", min: 0, step: "1", value: p?.annual_price ?? "" }), discount_pct: h("input", { type: "number", min: 0, max: 100, value: p?.discount_pct || 0 }),
    promo_label: h("input", { type: "text", value: p?.promo_label || "" }), sort_order: h("input", { type: "number", value: p?.sort_order || 0 }),
    features: h("textarea", { rows: 4 }),
  };
  f.features.value = (p?.features || []).join("\n");
  const flags = ["annual_enabled", "trial_eligible", "is_free_default", "available"].map((k) => [k, h("input", { type: "checkbox", checked: p ? (k === "available" ? p[k] !== 0 : !!p[k]) : k === "available" })]);
  const ent = Object.keys(keys.limit_keys).map((k) => [k, h("input", { type: "text", value: p?.entitlements?.[k] ?? "", placeholder: "unlimited" })]);
  const feats = Object.keys(keys.feature_keys).map((k) => [k, h("input", { type: "checkbox", checked: !!p?.entitlements?.[k] })]);
  const btn = h("button", { class: "btn btn-primary" }, "Save plan");
  const m = modal({ title: p ? `Edit ${p.name}` : "Create plan", wide: true, body: h("div", { class: "stack" },
    callout("info", "Price changes apply to new subscriptions only. Existing subscriptions keep their locked price."),
    h("div", { class: "form-grid" }, field("Code", f.code), field("Name", f.name), field("Description", f.description), field("Monthly price (₹)", f.monthly_price),
      field("Annual price (₹)", f.annual_price), field("Discount %", f.discount_pct), field("Promo label", f.promo_label), field("Display order", f.sort_order),
      h("div", { class: "full" }, field("Marketing features (one per line)", f.features))),
    h("div", { class: "row" }, flags.map(([k, el]) => h("label", { class: "check" }, el, fmt.cap(k)))),
    h("h3", null, "Limits (empty = unlimited, 0 = not allowed)"), h("div", { class: "form-grid" }, ent.map(([k, el]) => field(keys.limit_keys[k], el))),
    h("h3", null, "Features"), h("div", { class: "row" }, feats.map(([k, el]) => h("label", { class: "check" }, el, keys.feature_keys[k])))), footer: [btn] });
  btn.addEventListener("click", () => {
    const body = { code: f.code.value, name: f.name.value, description: f.description.value, monthly_price: f.monthly_price.value, annual_price: f.annual_price.value,
      discount_pct: f.discount_pct.value, promo_label: f.promo_label.value, sort_order: f.sort_order.value, features: f.features.value,
      entitlements: Object.fromEntries([...ent.map(([k, el]) => [k, el.value.trim() === "" ? null : el.value.trim()]), ...feats.map(([k, el]) => [k, el.checked])]),
      ...Object.fromEntries(flags.map(([k, el]) => [k, el.checked])) };
    act(btn, () => (p ? put(`/api/admin/plans/${p.id}`, body) : post("/api/admin/plans", body)), "Plan saved").then(() => { m.close(); plans(ctx); }).catch(() => {});
  });
}

async function plans(ctx) {
  const r = await get("/api/admin/plans");
  const W = writeOK(ctx);
  const couponForm = () => {
    const code = h("input", { type: "text" }), pct = h("input", { type: "number", min: 1, max: 100 }), max = h("input", { type: "number", min: 1 }), until = h("input", { type: "date" });
    const pl = h("input", { type: "text", placeholder: "e.g. basic, pro (empty = all)" }), desc = h("input", { type: "text" });
    const btn = h("button", { class: "btn btn-primary" }, "Create coupon");
    const m = modal({ title: "New coupon", body: h("div", { class: "form-grid" }, field("Code", code), field("Discount %", pct), field("Max redemptions", max), field("Valid until", until), field("Plans", pl), field("Description", desc)), footer: [btn] });
    btn.addEventListener("click", () => act(btn, () => post("/api/admin/coupons", { code: code.value.trim(), discount_pct: Number(pct.value), max_redemptions: max.value ? Number(max.value) : null,
      valid_until: until.value || null, plan_codes: pl.value.split(",").map((x) => x.trim()).filter(Boolean), description: desc.value }), "Coupon created").then(() => { m.close(); plans(ctx); }).catch(() => {}));
  };
  mount(ctx.root, head("Plans & pricing", "Configure plans, prices, features, limits and coupons. Prices shown everywhere come from here.", W ? [h("button", { class: "btn btn-primary btn-sm", onclick: () => planEditor(ctx, null, r) }, icon("plus", 14), "New plan")] : null),
    h("div", { class: "pricing mb-4" }, r.plans.map((p) => h("article", { class: `card price-card ${p.available ? "" : "muted"}` },
      h("div", { class: "row-between" }, h("h3", null, p.name), h("span", { class: "badge b-outline" }, p.code)),
      h("div", { class: "price" }, p.monthly_price === null ? "Not set" : inr(p.monthly_price), h("small", null, p.monthly_price === null ? "" : " / month")),
      h("div", { class: "row" }, p.is_free_default ? badge("pass", "Free default") : null, p.trial_eligible ? h("span", { class: "badge b-accent" }, "Trial plan") : null, p.available ? null : badge("fail", "Hidden")),
      h("ul", null, p.features.map((f) => h("li", null, icon("check", 14), f))), h("span", { class: "grow" }),
      W ? h("button", { class: "btn", onclick: () => planEditor(ctx, p, r) }, icon("edit", 14), "Edit") : null))),
    card("Coupons", dataTable({ rows: r.coupons, empty: emptyState({ icon: "rupee", title: "No coupons", body: "Coupons apply once payments are enabled." }),
      columns: [{ key: "code", label: "Code" }, { key: "discount_pct", label: "Discount", render: (c) => `${c.discount_pct}%` }, { key: "redeemed_count", label: "Used", render: (c) => `${c.redeemed_count}${c.max_redemptions ? "/" + c.max_redemptions : ""}` },
        { key: "valid_until", label: "Valid until", render: (c) => c.valid_until || "No end" }, { key: "active", label: "Active", render: (c) => W ? h("input", { type: "checkbox", checked: !!c.active, "aria-label": "Active",
          onchange: (e) => put(`/api/admin/coupons/${c.id}`, { active: e.target.checked }).then(() => toast("Coupon updated", "success")) }) : (c.active ? "Yes" : "No") }] }),
    W ? [h("button", { class: "btn btn-sm", onclick: couponForm }, icon("plus", 14), "New coupon")] : null));
}

async function trials(ctx) {
  const r = await get("/api/admin/trials");
  const days = h("input", { type: "number", min: 1, max: 365, value: r.config.trial_days });
  const plan = h("select", null, r.plans.map((p) => h("option", { value: p.code, selected: p.code === r.config.trial_plan_code, disabled: !p.trial_eligible }, `${p.name}${p.trial_eligible ? "" : " (not trial-eligible)"}`)));
  const remind = h("input", { type: "number", min: 1, max: 30, value: r.config.trial_reminder_days });
  const btn = h("button", { class: "btn btn-primary" }, "Save trial settings");
  btn.addEventListener("click", () => act(btn, () => put("/api/admin/settings", { trial_days: Number(days.value), trial_plan_code: plan.value, trial_reminder_days: Number(remind.value) }), "Saved — applies to future trials"));
  mount(ctx.root, head("Trials", "Trials start when a user completes profile setup and end on the Free plan. Server time only."),
    h("div", { class: "grid grid-3 mb-4" }, kpi("Active trials", r.active.length), kpi("Expiring in 7 days", r.expiring_7d), kpi("Expired", r.expired)),
    h("div", { class: "split-wide" }, card("Active trials", dataTable({ rows: r.active, onRow: (t) => ctx.navigate(`/admin/users/${t.user_id}`), empty: emptyState({ icon: "clock", title: "No active trials" }),
      columns: [{ key: "email", label: "User" }, { key: "started_at", label: "Started", render: (t) => fmt.date(t.started_at) }, { key: "ends_at", label: "Ends", render: (t) => fmt.date(t.ends_at) },
        { key: "extended_days", label: "Extended" }] })),
      writeOK(ctx) ? card("Configuration", h("div", { class: "stack" }, field("Trial duration (days)", days, "Applies to users who start a trial after saving"), field("Trial plan", plan), field("Reminder before expiry (days)", remind), btn)) : null));
}

async function limits(ctx) {
  const r = await get("/api/admin/limits");
  const full = await get("/api/admin/plans");
  const inputs = {};
  const rows = [...Object.entries(r.limit_keys).map(([k, l]) => ({ k, l, type: "limit" })), ...Object.entries(r.feature_keys).map(([k, l]) => ({ k, l, type: "feature" }))];
  const table = h("div", { class: "table-wrap matrix" }, h("table", { class: "table" }, h("thead", null, h("tr", null, h("th", null, "Limit / feature"), r.plans.map((p) => h("th", null, p.name)))),
    h("tbody", null, rows.map((row) => h("tr", null, h("td", null, row.l), r.plans.map((p) => {
      const v = p.entitlements[row.k];
      const el = row.type === "limit" ? h("input", { type: "text", value: v ?? "", placeholder: "∞", "aria-label": `${row.l} for ${p.name}` }) : h("input", { type: "checkbox", checked: !!v, "aria-label": `${row.l} for ${p.name}` });
      inputs[`${p.id}|${row.k}`] = el;
      return h("td", null, el);
    }))))));
  const save = h("button", { class: "btn btn-primary" }, "Save all limits");
  save.addEventListener("click", () => act(save, async () => {
    for (const p of full.plans) {
      const ent = { ...p.entitlements };
      rows.forEach((row) => { const el = inputs[`${p.id}|${row.k}`]; ent[row.k] = row.type === "limit" ? (el.value.trim() === "" ? null : el.value.trim()) : el.checked; });
      await put(`/api/admin/plans/${p.id}`, { ...p, entitlements: ent });
    }
  }, "Limits saved"));
  mount(ctx.root, head("Feature limits", "Central entitlement matrix. Empty = unlimited, 0 = not allowed. Enforced server-side for every request."),
    card("Plan limits", h("div", { class: "stack" }, table, writeOK(ctx) ? h("div", null, save) : null)),
    h("div", { class: "mt-4" }, card("User-specific overrides", dataTable({ rows: r.overrides, onRow: (o) => ctx.navigate(`/admin/users/${o.user_id}`), empty: emptyState({ icon: "filter", title: "No overrides", body: "Set overrides from a user's page." }),
      columns: [{ key: "email", label: "User" }, { key: "key", label: "Key" }, { key: "value", label: "Value" }, { key: "reason", label: "Reason" }, { key: "expires_at", label: "Expires", render: (o) => o.expires_at || "Never" }] }))));
}

// ================================================================ analytics
async function analytics(ctx) {
  const r = await get("/api/admin/analytics");
  const pct = (v) => (v === null ? "No data yet" : `${v}%`);
  mount(ctx.root, head("Outcome analytics", r.note),
    h("div", { class: "grid grid-4 mb-4" }, kpi("Search → shortlist", r.conversions.search_to_shortlist, undefined, { pct: true }), kpi("Shortlist → application", r.conversions.shortlist_to_application, undefined, { pct: true }),
      kpi("Application → interview", r.conversions.application_to_interview, undefined, { pct: true }), kpi("Interview → offer", r.conversions.interview_to_offer, undefined, { pct: true })),
    h("div", { class: "split-wide" },
      card("Application funnel", h("div", { class: "stack" }, r.funnel.map((f) => h("div", { class: "bd-row" }, h("div", null, h("strong", null, f.stage), h("div", { class: "xs muted" }, f.actor)),
        meter(f.n, Math.max(1, r.funnel[0].n)), h("span", { class: "pts" }, num(f.n)))),
        h("p", { class: "xs muted" }, `Rejections reported: ${r.rejections}. Independently verified outcomes: ${r.verified_outcomes} (verification isn't available).`))),
      h("div", { class: "stack" }, card("Averages", h("div", { class: "small" }, `Average match score: ${r.avg_match ?? "No data yet"}`, h("br"), `Trials: ${r.subscription_conversion.trials_total} · converted to paid: ${r.subscription_conversion.converted_to_paid}`)),
        barChart({ title: "Feature usage (events)", horizontal: true, data: r.feature_usage.slice(0, 12).map((x) => ({ label: x.event, value: x.n, note: `${x.failures} failures` })), valueLabel: "Events" }))),
    h("div", { class: "grid grid-2 mt-4" },
      card("Job source effectiveness", dataTable({ rows: r.sources, empty: emptyState({ icon: "globe", title: "No jobs yet" }), columns: [{ key: "source", label: "Source" }, { key: "jobs", label: "Jobs" },
        { key: "qualifying", label: "Qualifying" }, { key: "avg_match", label: "Avg match" }, { key: "applications", label: "Applications" }] })),
      barChart({ title: "Reported outcomes by month (applied)", data: r.outcome_trend.map((x) => ({ label: x.month, value: x.applied, note: `${x.interviews} interviews · ${x.offers} offers · ${x.rejections} rejections` })), valueLabel: "Applied" })));
  void pct;
}

// ================================================================ health, AI, sources
async function health(ctx) {
  const r = await get("/api/admin/health");
  const yes = (b) => (b ? badge("pass", "Configured") : badge("unknown", "Not configured"));
  mount(ctx.root, head("System health", "Live status of this deployment."),
    r.storage.warning ? callout("danger", r.storage.warning) : callout("success", "Persistent storage is configured."),
    h("div", { class: "grid grid-4 mt-4 mb-4" }, kpi("Uptime (hours)", Math.round(r.uptime_seconds / 3600)), kpi("Database size (MB)", r.database.size_mb), kpi("Users", r.database.users), kpi("Failed tasks (24 h)", r.failed_tasks_24h)),
    h("div", { class: "split" },
      card("Deployment", h("div", { class: "stack small" }, h("div", null, `Environment: ${r.environment}`), h("div", null, `Commit: ${r.deployment.commit || "unknown (local)"}`),
        h("div", null, `Service: ${r.deployment.service || "local"}`), h("div", null, `Python ${r.python}`))),
      card("Integrations", h("div", { class: "stack small" }, Object.entries(r.integrations).map(([k, v]) => h("div", { class: "row-between" }, h("span", null, fmt.cap(k)), typeof v === "boolean" ? yes(v) : h("span", null, String(v)))))),
      card("Errors in the last 24 hours", r.errors_24h.length ? h("ul", { class: "evidence-list small" }, r.errors_24h.map((e) => h("li", null, `${e.action}: ${e.n}`))) : h("p", { class: "muted" }, "No errors.")),
      card("Backups", h("div", { class: "stack small" }, h("div", null, `Backend: ${r.backups.backend}`), h("div", null, `Last success: ${r.backups.last_success ? fmt.dateTime(r.backups.last_success.started_at) : "never"}`),
        h("div", null, `Recovery point: ${r.backups.rpo}`), h("a", { href: "/admin/settings#backups" }, "Backup details")))));
}

async function ai(ctx) {
  const r = await get("/api/admin/ai-usage");
  mount(ctx.root, head("AI usage & costs", `Provider: ${r.provider} (${r.key_configured ? "key configured" : "no key — AI disabled; deterministic processing in use"}).`),
    h("div", { class: "grid grid-3 mb-4" }, kpi("Cost this month", r.month_cost_inr, undefined, { money: true }), kpi("Global monthly budget", r.global_budget_inr, undefined, { money: true }),
      kpi("Calls (60 days)", r.by_day.reduce((a, d) => a + d.calls, 0))),
    h("div", { class: "grid grid-2" }, barChart({ title: "AI calls per day", data: r.by_day.map((d) => ({ label: d.day.slice(5), value: d.calls, note: `₹${d.cost} · ${d.cached} cached` })), valueLabel: "Calls" }),
      card("Top users (30 days)", dataTable({ rows: r.top_users, empty: emptyState({ icon: "target", title: "No AI usage" }), columns: [{ key: "email", label: "User" }, { key: "calls", label: "Calls" }, { key: "cost", label: "Cost (₹)" }] }))));
}

async function sources(ctx) {
  const r = await get("/api/admin/sources");
  const max = h("input", { type: "number", min: 1, max: 500, value: r.config.max_jobs_per_run });
  const types = ["greenhouse", "lever", "ashby", "remotive", "career_page"].map((t) => [t, h("input", { type: "checkbox", checked: r.config.enabled_source_types.includes(t) })]);
  const btn = h("button", { class: "btn btn-primary" }, "Save discovery settings");
  btn.addEventListener("click", () => act(btn, () => put("/api/admin/settings", { discovery: { max_jobs_per_run: Number(max.value), enabled_source_types: types.filter(([, el]) => el.checked).map(([t]) => t) } }), "Saved"));
  mount(ctx.root, head("Job sources", "Discovery uses official public job-board APIs and user-registered company boards."),
    h("div", { class: "split" }, writeOK(ctx) ? card("Discovery settings", h("div", { class: "stack" }, field("Max jobs processed per run", max), h("div", { class: "row" }, types.map(([t, el]) => h("label", { class: "check" }, el, t))), btn)) : null,
      card("Registered company boards", dataTable({ rows: r.registry, empty: emptyState({ icon: "globe", title: "No boards registered" }), columns: [{ key: "ats", label: "Type" }, { key: "companies", label: "Companies" }, { key: "enabled", label: "Enabled" }, { key: "users", label: "Users" }] }))),
    h("div", { class: "mt-4" }, card("Source effectiveness", dataTable({ rows: r.effectiveness, empty: emptyState({ icon: "chart", title: "No jobs yet" }), columns: [{ key: "source", label: "Source" }, { key: "jobs", label: "Jobs" }, { key: "qualifying", label: "Qualifying" }, { key: "avg_match", label: "Avg match" }] }))));
}

// ================================================================ announcements, support
async function announcements(ctx) {
  const rows = await get("/api/admin/announcements");
  const edit = (a) => {
    const title = h("input", { type: "text", value: a?.title || "" }), body = h("textarea", { rows: 3 }), level = h("select", null, ["info", "warning", "success"].map((x) => h("option", { value: x, selected: a?.level === x }, x)));
    const aud = h("select", null, [["all", "Everyone"], ["users", "Signed-in users"], ["public", "Public visitors"]].map(([v, l]) => h("option", { value: v, selected: a?.audience === v }, l)));
    const active = h("input", { type: "checkbox", checked: a ? !!a.active : true }), from = h("input", { type: "date", value: (a?.starts_at || "").slice(0, 10) }), to = h("input", { type: "date", value: (a?.ends_at || "").slice(0, 10) });
    body.value = a?.body || "";
    const btn = h("button", { class: "btn btn-primary" }, "Save");
    const m = modal({ title: a ? "Edit announcement" : "New announcement", body: h("div", { class: "form-grid" }, h("div", { class: "full" }, field("Title", title)), h("div", { class: "full" }, field("Message", body)),
      field("Style", level), field("Audience", aud), field("Starts", from), field("Ends", to), h("label", { class: "check" }, active, "Active")), footer: [btn] });
    btn.addEventListener("click", () => act(btn, () => (a ? put(`/api/admin/announcements/${a.id}`, payload()) : post("/api/admin/announcements", payload())), "Saved").then(() => { m.close(); announcements(ctx); }).catch(() => {}));
    function payload() { return { title: title.value, body: body.value || null, level: level.value, audience: aud.value, active: active.checked, starts_at: from.value || null, ends_at: to.value || null }; }
  };
  mount(ctx.root, head("Announcements", "Banners shown on the public site and/or inside the app.", writeOK(ctx) ? [h("button", { class: "btn btn-primary btn-sm", onclick: () => edit(null) }, icon("plus", 14), "New")] : null),
    h("div", { class: "card" }, dataTable({ rows, onRow: writeOK(ctx) ? edit : null, empty: emptyState({ icon: "bell", title: "No announcements" }),
      columns: [{ key: "title", label: "Title" }, { key: "level", label: "Style" }, { key: "audience", label: "Audience" }, { key: "active", label: "Active", render: (a) => a.active ? "Yes" : "No" },
        { key: "created_at", label: "Created", render: (a) => fmt.date(a.created_at) }, { key: "id", label: "", sort: false, render: (a) => writeOK(ctx) ? h("button", { class: "btn btn-sm btn-ghost", onclick: async (e) => {
          e.stopPropagation(); if (await confirmDialog({ title: "Delete announcement?", message: a.title, confirmLabel: "Delete", danger: true })) { await del(`/api/admin/announcements/${a.id}`); announcements(ctx); }
        } }, icon("trash", 14)) : "" }] })));
}

async function support(ctx) {
  const r = await get("/api/admin/support");
  const open = async (t) => {
    const full = await get(`/api/admin/support/${t.id}`);
    const msg = h("textarea", { rows: 4, "aria-label": "Reply" }); const status = h("select", null, ["open", "answered", "closed"].map((s) => h("option", { value: s, selected: full.status === s }, s)));
    const btn = h("button", { class: "btn btn-primary" }, "Send / update");
    const m = modal({ title: full.subject, wide: true, body: h("div", { class: "stack" }, h("div", { class: "small muted" }, `From ${full.email || "user #" + full.user_id}`),
      full.messages.map((x) => h("div", { class: "card card-pad", style: { background: x.from_staff ? "var(--brand-soft)" : "var(--surface)" } }, h("div", { class: "xs muted mb-2" }, `${x.from_staff ? "Staff" : "User"} · ${fmt.dateTime(x.created_at)}`), h("div", { class: "prewrap small" }, x.body))),
      field("Reply (shown to the user)", msg), field("Status", status)), footer: [btn] });
    btn.addEventListener("click", () => act(btn, () => post(`/api/admin/support/${t.id}`, { message: msg.value.trim() || null, status: status.value }), "Updated").then(() => { m.close(); support(ctx); }).catch(() => {}));
  };
  mount(ctx.root, head("Support", "Requests from users and the public contact form."),
    h("div", { class: "card" }, dataTable({ rows: r.items, onRow: open, empty: emptyState({ icon: "help", title: "No support requests" }),
      columns: [{ key: "subject", label: "Subject" }, { key: "email", label: "From" }, { key: "status", label: "Status" }, { key: "messages", label: "Messages" }, { key: "updated_at", label: "Updated", render: (t) => fmt.ago(t.updated_at) }] })));
}

// ================================================================ audit, security, settings
async function audit(ctx) {
  const r = await get("/api/admin/audit");
  mount(ctx.root, head("Audit log", "Every administrative action, with actor and hashed IP."),
    h("div", { class: "card" }, dataTable({ rows: r.items, empty: emptyState({ icon: "shield", title: "No admin actions yet" }),
      columns: [{ key: "at", label: "Time", render: (a) => fmt.dateTime(a.at) }, { key: "actor_email", label: "Actor" }, { key: "action", label: "Action" },
        { key: "target_user_id", label: "Target", render: (a) => a.target_user_id ? h("a", { href: `/admin/users/${a.target_user_id}` }, `#${a.target_user_id}`) : "—" },
        { key: "details", label: "Details", render: (a) => a.details ? h("code", { class: "xs" }, JSON.stringify(a.details).slice(0, 160)) : "" }] })));
}

async function security(ctx) {
  const r = await get("/api/admin/security");
  mount(ctx.root, head("Security", "Staff accounts, admin sessions and authentication policy."),
    h("div", { class: "grid grid-3 mb-4" }, kpi("Staff accounts", r.staff.length), kpi("Active staff sessions", r.staff_sessions.length), kpi("Failed sign-ins (24 h)", r.failed_logins_24h)),
    h("div", { class: "split" },
      card("Staff", dataTable({ rows: r.staff, onRow: (u) => ctx.navigate(`/admin/users/${u.id}`), columns: [{ key: "email", label: "Email" }, { key: "role", label: "Role" },
        { key: "totp_enabled", label: "2FA", render: (u) => u.totp_enabled ? badge("pass", "On") : badge("fail", "Off") }, { key: "last_login_at", label: "Last login", render: (u) => u.last_login_at ? fmt.ago(u.last_login_at) : "Never" }] })),
      card("Policies", h("ul", { class: "evidence-list small" }, Object.entries(r.policies).map(([k, v]) => h("li", null, h("strong", null, fmt.cap(k)), `: ${v}`))))),
    h("div", { class: "mt-4" }, card("Active staff sessions", dataTable({ rows: r.staff_sessions, columns: [{ key: "email", label: "User" }, { key: "last_seen_at", label: "Last seen", render: (s) => fmt.ago(s.last_seen_at) },
      { key: "elevated_until", label: "2FA verified until", render: (s) => s.elevated_until ? fmt.dateTime(s.elevated_until) : "—" },
      { key: "id", label: "", sort: false, render: (s) => ctx.role === "owner" ? h("button", { class: "btn btn-sm btn-ghost", onclick: (e) => act(e.currentTarget, () => post(`/api/admin/security/sessions/${s.id}/revoke`), "Session revoked").then(() => security(ctx)) }, "Revoke") : "" }] }))));
}

async function settings(ctx) {
  const [r, b] = await Promise.all([get("/api/admin/settings"), get("/api/admin/backups")]);
  const s = r.system;
  const W = writeOK(ctx);
  const maint = h("input", { type: "checkbox", checked: s.maintenance_mode }); const maintMsg = h("input", { type: "text", value: s.maintenance_message });
  const reg = h("select", null, [["auto", `Automatic (currently ${r.registration_effective ? "open" : "closed"})`], ["open", "Open"], ["closed", "Closed"]].map(([v, l]) =>
    h("option", { value: v, selected: (s.registration_open === null ? "auto" : s.registration_open ? "open" : "closed") === v }, l)));
  const flags = Object.entries(s.feature_flags).map(([k, v]) => [k, h("input", { type: "checkbox", checked: v })]);
  const level = h("select", null, [["standard", "Standard"], ["minimal", "Minimal (event names only)"], ["off", "Off (critical events only)"]].map(([v, l]) => h("option", { value: v, selected: s.analytics_level === v }, l)));
  const rate = h("input", { type: "number", min: 10, max: 10000, value: s.api_rate_limit_per_minute });
  const aiBudget = h("input", { type: "number", min: 0, step: "1", value: s.global_ai_monthly_budget_inr });
  const supportEmail = h("input", { type: "email", value: s.support_email || "" });
  const bInt = h("input", { type: "number", min: 1, max: 168, value: s.backup.interval_hours }); const bDeb = h("input", { type: "number", min: 1, max: 120, value: s.backup.debounce_minutes });
  const bRet = h("input", { type: "number", min: 1, max: 365, value: s.backup.retention });
  const save = h("button", { class: "btn btn-primary" }, "Save settings");
  save.addEventListener("click", async () => {
    if (maint.checked !== s.maintenance_mode && maint.checked && !(await confirmDialog({ title: "Enable maintenance mode?", message: "Non-staff users will be blocked from the app until you turn it off.", confirmLabel: "Enable", danger: true }))) return;
    act(save, () => put("/api/admin/settings", { maintenance_mode: maint.checked, maintenance_message: maintMsg.value, registration_open: reg.value === "auto" ? null : reg.value === "open",
      feature_flags: Object.fromEntries(flags.map(([k, el]) => [k, el.checked])), analytics_level: level.value, api_rate_limit_per_minute: Number(rate.value),
      global_ai_monthly_budget_inr: Number(aiBudget.value), support_email: supportEmail.value.trim(),
      backup: { interval_hours: Number(bInt.value), debounce_minutes: Number(bDeb.value), retention: Number(bRet.value) } }), "Settings saved");
  });
  const runBtn = h("button", { class: "btn" }, icon("refresh", 14), "Run backup now");
  runBtn.addEventListener("click", () => act(runBtn, () => post("/api/admin/backups/run"), null).then((x) => { toast(x.status === "succeeded" ? `Backup stored (${Math.round(x.size / 1024)} KB, ${x.backend})` : `Backup ${x.status}: ${x.error || x.reason}`, x.status === "succeeded" ? "success" : "error"); settings(ctx); }).catch(() => {}));
  mount(ctx.root, head("Settings", "System control panel. Secret values are never displayed — only whether they're configured."),
    h("div", { class: "split" },
      card("Access & maintenance", h("div", { class: "stack" }, h("label", { class: "check" }, maint, "Maintenance mode"), field("Maintenance message", maintMsg),
        field("Registrations", reg, "Automatic keeps registrations closed in production until storage is persistent"), field("Support email (public)", supportEmail))),
      card("Feature flags", h("div", { class: "stack" }, flags.map(([k, el]) => h("label", { class: "check" }, el, fmt.cap(k))))),
      card("Analytics, rate limits & AI", h("div", { class: "stack" }, field("Analytics tracking level", level), field("API rate limit (requests / minute / user)", rate), field("Global AI budget (₹ / month)", aiBudget))),
      card("Secrets & integrations", h("div", { class: "stack small" }, Object.entries(r.secrets).map(([k, v]) => h("div", { class: "row-between" }, h("code", null, k), v ? badge("pass", "Configured") : badge("unknown", "Not set"))),
        h("p", { class: "xs muted" }, r.note)))),
    W ? h("div", { class: "mt-4 mb-4" }, save) : null,
    h("div", { id: "backups" }, card("Backups", h("div", { class: "stack" },
      b.persistent_storage ? null : callout("warn", "Live storage on this host is not persistent. Backups are disaster recovery — they don't replace persistent storage."),
      h("div", { class: "small" }, `Destination: ${b.backend === "s3" ? "S3-compatible object storage" : "local folder (development)"} · encryption ${b.encryption_configured ? "on" : "off"} · ${b.rpo}`),
      b.startup_recovery ? h("div", { class: "small muted" }, `Last startup check: ${b.startup_recovery.reason || b.startup_recovery.local_state} (${fmt.dateTime(b.startup_recovery.at)})`) : null,
      h("div", { class: "form-grid" }, field("Scheduled every (hours)", bInt), field("After changes, wait (minutes)", bDeb), field("Keep versions", bRet)),
      dataTable({ rows: b.recent, empty: emptyState({ icon: "layers", title: "No backups yet" }), columns: [{ key: "started_at", label: "Started", render: (x) => fmt.dateTime(x.started_at) },
        { key: "trigger", label: "Trigger" }, { key: "status", label: "Status", render: (x) => badge(x.status === "succeeded" ? "pass" : x.status === "failed" ? "fail" : "unknown", x.status) },
        { key: "size_bytes", label: "Size", render: (x) => x.size_bytes ? `${Math.round(x.size_bytes / 1024)} KB` : "—" }, { key: "verified", label: "Verified", render: (x) => x.verified ? "Yes" : "No" }, { key: "error", label: "Error" }] }),
      W ? h("div", null, runBtn) : null))));
}

export const PAGES = { overview, users, activity, subscriptions, revenue, plans, trials, limits, analytics, health, ai, sources, announcements, support, audit, security, settings };
void api; void clear;
