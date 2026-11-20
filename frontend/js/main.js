// App bootstrap: session check, shell, router, global header widgets.
import { h, icon, mount, clear, fmt, debounce } from "./ui/dom.js";
import { api, get, post, setUnauthorizedHandler } from "./api.js";
import { errorState, loading, toast } from "./ui/components.js";

const NAV = [
  { group: "Workspace" },
  { path: "/", label: "Dashboard", icon: "dashboard" },
  { path: "/discover", label: "Discover Jobs", icon: "search" },
  { path: "/saved", label: "Saved / Shortlisted", icon: "bookmark" },
  { path: "/resumes", label: "Resume Studio", icon: "file" },
  { path: "/applications", label: "Applications", icon: "briefcase" },
  { path: "/contacts", label: "Contacts & Outreach", icon: "users" },
  { path: "/approvals", label: "Approval Center", icon: "checkCircle", countKey: "approvals" },
  { group: "Insights" },
  { path: "/analytics", label: "Analytics", icon: "chart" },
  { path: "/activity", label: "Activity & Logs", icon: "activity" },
  { group: "Account" },
  { path: "/setup", label: "Profile", icon: "user" },
  { path: "/settings", label: "Settings", icon: "settings" },
];

const ROUTES = [
  { re: /^\/$/, page: "dashboard" },
  { re: /^\/setup$/, page: "setup" },
  { re: /^\/discover$/, page: "discover" },
  { re: /^\/saved$/, page: "saved" },
  { re: /^\/jobs\/(?<id>[\w-]+)$/, page: "job" },
  { re: /^\/resumes$/, page: "resumes" },
  { re: /^\/resumes\/(?<id>\d+)$/, page: "resume" },
  { re: /^\/applications$/, page: "applications" },
  { re: /^\/contacts$/, page: "contacts" },
  { re: /^\/approvals$/, page: "approvals" },
  { re: /^\/analytics$/, page: "analytics" },
  { re: /^\/activity$/, page: "activity" },
  { re: /^\/settings$/, page: "settings" },
];

const app = document.getElementById("app");
const state = { session: null, cleanup: null, counts: {}, navToken: 0 };

// ---------------------------------------------------------------- theme
export function getTheme() { try { return localStorage.getItem("jobpilot.theme") || "system"; } catch { return "system"; } }
export function setTheme(t) {
  try { if (t === "system") localStorage.removeItem("jobpilot.theme"); else localStorage.setItem("jobpilot.theme", t); } catch { /* ignore */ }
  if (t === "system") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", t);
  window.dispatchEvent(new CustomEvent("themechange"));
}
function effectiveDark() {
  const t = getTheme();
  return t === "dark" || (t === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
}

// ---------------------------------------------------------------- navigation
export function navigate(path, { replace = false } = {}) {
  if (replace) history.replaceState({}, "", path); else history.pushState({}, "", path);
  route();
}
window.addEventListener("popstate", () => route());
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href]");
  if (!a || a.target === "_blank" || a.hasAttribute("download") || e.ctrlKey || e.metaKey || e.shiftKey || e.button !== 0) return;
  const href = a.getAttribute("href");
  if (href.startsWith("/") && !href.startsWith("/api/") && !href.startsWith("/static/")) { e.preventDefault(); navigate(href); }
});

// ---------------------------------------------------------------- shell
let shell, contentEl, navEl, statusEl, bellBtn;

function buildShell() {
  const collapsed = (() => { try { return localStorage.getItem("jobpilot.sidebar") === "collapsed"; } catch { return false; } })();
  navEl = h("nav", { class: "nav", "aria-label": "Main navigation" });
  const collapseBtn = h("button", { class: "btn btn-ghost desktop-only", style: { width: "100%", justifyContent: "flex-start", gap: "12px" },
    "aria-label": "Collapse or expand sidebar", onclick: () => {
      shell.classList.toggle("collapsed");
      try { localStorage.setItem("jobpilot.sidebar", shell.classList.contains("collapsed") ? "collapsed" : "open"); } catch { /* ignore */ }
    } }, icon("sidebar", 18), h("span", { class: "sidebar-foot-label" }, "Collapse sidebar"));
  const sidebar = h("aside", { class: "sidebar", id: "sidebar" },
    h("a", { class: "brand", href: "/", "aria-label": "JobPilot home" }, h("span", { class: "brand-mark" }, icon("check", 16)), h("span", { class: "brand-name" }, "JobPilot")),
    navEl, h("div", { class: "sidebar-foot" }, collapseBtn));
  statusEl = h("span", { class: "status-pill", role: "status", "aria-live": "polite" }, h("span", { class: "dot" }), "Ready");
  const header = h("header", { class: "header" },
    h("button", { class: "btn btn-ghost btn-icon mobile-only", "aria-label": "Open navigation", "aria-controls": "sidebar", onclick: () => toggleMobileNav(true) }, icon("menu")),
    searchBox(), statusEl,
    h("div", { class: "header-actions" }, notificationsButton(), themeButton(), profileMenu()));
  contentEl = h("main", { class: "content", id: "main", tabindex: "-1" });
  shell = h("div", { class: `shell ${collapsed ? "collapsed" : ""}` }, sidebar, h("div", { class: "main-col" }, banner(), header, contentEl));
  mount(app, shell);
  renderNav();
}

function banner() {
  const b = h("div", { id: "global-banner" });
  window.addEventListener("offline", () => mount(b, h("div", { class: "banner warn", role: "alert" }, icon("globe", 16), "You are offline. Changes can't be saved until the connection returns.")));
  window.addEventListener("online", () => clear(b));
  return b;
}

function toggleMobileNav(open) {
  shell.classList.toggle("nav-open", open);
  const existing = document.querySelector(".backdrop");
  if (open && !existing) document.body.append(h("div", { class: "backdrop", onclick: () => toggleMobileNav(false) }));
  if (!open && existing) existing.remove();
}

function renderNav() {
  clear(navEl);
  const path = location.pathname;
  for (const item of NAV) {
    if (item.group) { navEl.append(h("div", { class: "nav-group" }, item.group)); continue; }
    const active = item.path === "/" ? path === "/" : path.startsWith(item.path) || (item.path === "/discover" && path.startsWith("/jobs/")) ;
    const count = item.countKey ? state.counts[item.countKey] : 0;
    navEl.append(h("a", { href: item.path, "aria-current": active ? "page" : null, title: item.label, onclick: () => toggleMobileNav(false) },
      icon(item.icon, 18), h("span", { class: "nav-label" }, item.label), count ? h("span", { class: "count", "aria-label": `${count} pending` }, count) : null));
  }
}

function searchBox() {
  const input = h("input", { type: "search", placeholder: "Search jobs, applications, contacts…", "aria-label": "Global search", autocomplete: "off" });
  const results = h("div", { class: "search-results", hidden: true, role: "listbox" });
  const wrap = h("div", { class: "search" }, icon("search", 16), input, results);
  const run = debounce(async () => {
    const q = input.value.trim();
    if (q.length < 2) { results.hidden = true; return; }
    try {
      const r = await get(`/api/search?q=${encodeURIComponent(q)}`);
      clear(results);
      const add = (label, items, mk) => { if (!items.length) return; results.append(h("div", { class: "group" }, label)); items.forEach((i) => results.append(mk(i))); };
      add("Jobs", r.jobs, (j) => h("a", { href: `/jobs/${j.id}`, role: "option" }, `${j.title} — ${j.company || ""}`, h("span", { class: "xs muted" }, ` · ${j.status_label}`)));
      add("Applications", r.applications, (a) => h("a", { href: `/applications?open=${a.id}`, role: "option" }, `${a.title} — ${a.company}`));
      add("Contacts", r.contacts, (c) => h("a", { href: `/contacts?q=${encodeURIComponent(c.name)}`, role: "option" }, `${c.name}${c.company ? " · " + c.company : ""}`));
      if (!results.childNodes.length) results.append(h("div", { class: "group" }, "No matches"));
      results.hidden = false;
    } catch { results.hidden = true; }
  }, 250);
  input.addEventListener("input", run);
  input.addEventListener("keydown", (e) => {
    const links = [...results.querySelectorAll("a")];
    const i = links.indexOf(document.activeElement);
    if (e.key === "ArrowDown" && links.length) { e.preventDefault(); links[0].focus(); }
    if (e.key === "Escape") { results.hidden = true; input.blur(); }
    void i;
  });
  results.addEventListener("keydown", (e) => {
    const links = [...results.querySelectorAll("a")]; const i = links.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); links[Math.min(i + 1, links.length - 1)]?.focus(); }
    if (e.key === "ArrowUp") { e.preventDefault(); if (i <= 0) input.focus(); else links[i - 1].focus(); }
    if (e.key === "Escape") { results.hidden = true; input.focus(); }
  });
  results.addEventListener("click", () => { results.hidden = true; input.value = ""; });
  document.addEventListener("click", (e) => { if (!wrap.contains(e.target)) results.hidden = true; });
  document.addEventListener("keydown", (e) => { if (e.key === "/" && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) { e.preventDefault(); input.focus(); } });
  return wrap;
}

function popoverButton(btn, buildContent) {
  const wrap = h("div", { style: { position: "relative" } }, btn);
  let pop = null;
  const close = () => { pop?.remove(); pop = null; btn.setAttribute("aria-expanded", "false"); };
  btn.setAttribute("aria-haspopup", "true"); btn.setAttribute("aria-expanded", "false");
  btn.addEventListener("click", async (e) => {
    e.stopPropagation();
    if (pop) return close();
    pop = h("div", { class: "popover", role: "dialog" }, loading(1));
    wrap.append(pop); btn.setAttribute("aria-expanded", "true");
    const content = await buildContent(close);
    if (pop) mount(pop, content);
  });
  document.addEventListener("click", (e) => { if (pop && !wrap.contains(e.target)) close(); });
  document.addEventListener("keydown", (e) => { if (e.key === "Escape" && pop) { close(); btn.focus(); } });
  return wrap;
}

function notificationsButton() {
  bellBtn = h("button", { class: "btn btn-ghost btn-icon", "aria-label": "Notifications" }, icon("bell"));
  return popoverButton(bellBtn, async (close) => {
    try {
      const r = await get("/api/notifications");
      const list = h("div", { class: "list" }, r.items.length ? r.items.map((n) => h(n.link ? "a" : "div", { class: `notif ${n.read ? "" : "unread"}`, href: n.link || null, onclick: close },
        h("div", { class: "row", style: { gap: "6px" } }, icon(n.level === "error" ? "alert" : n.level === "warning" ? "alert" : "info", 14), h("strong", { class: "small" }, n.title)),
        h("div", { class: "xs muted" }, fmt.dateTime(n.at)))) : h("div", { class: "empty", style: { padding: "24px" } }, h("p", null, "No notifications yet.")));
      if (r.unread) { post("/api/notifications/read").then(refreshCounts).catch(() => {}); }
      return h("div", null, h("div", { class: "card-head" }, h("h3", null, "Notifications"), h("a", { href: "/activity", class: "small", onclick: close }, "Activity & Logs")), list);
    } catch (e) { return errorState(e); }
  });
}

function themeButton() {
  const btn = h("button", { class: "btn btn-ghost btn-icon", "aria-label": "Switch theme" });
  const paint = () => { mount(btn, icon(effectiveDark() ? "sun" : "moon")); btn.title = `Theme: ${getTheme()} (click to switch to ${effectiveDark() ? "light" : "dark"})`; };
  btn.addEventListener("click", () => { setTheme(effectiveDark() ? "light" : "dark"); paint(); toast(`Theme set to ${getTheme()}`, "info", { timeout: 1500 }); });
  window.addEventListener("themechange", paint);
  paint();
  return btn;
}

function profileMenu() {
  const btn = h("button", { class: "btn btn-ghost btn-icon", "aria-label": "Profile menu" }, icon("user"));
  return popoverButton(btn, (close) => h("div", { class: "menu" },
    h("a", { href: "/setup", onclick: close }, icon("user", 16), "Profile & master resume"),
    h("a", { href: "/settings", onclick: close }, icon("settings", 16), "Settings"),
    h("a", { href: "/activity", onclick: close }, icon("activity", 16), "Activity & Logs"),
    state.session?.auth_required ? h("button", { onclick: async () => { await post("/api/auth/logout"); location.href = "/"; } }, icon("logout", 16), "Sign out") : null));
}

// ---------------------------------------------------------------- polling: counts + running tasks
export async function refreshCounts() {
  try {
    const [a, n] = await Promise.all([get("/api/approvals"), get("/api/notifications")]);
    state.counts.approvals = a.items.length;
    renderNav();
    const dot = bellBtn.querySelector(".badge-dot");
    if (n.unread && !dot) bellBtn.append(h("span", { class: "badge-dot", "aria-label": `${n.unread} unread` }, n.unread > 9 ? "9+" : n.unread));
    else if (dot && !n.unread) dot.remove();
    else if (dot) dot.textContent = n.unread > 9 ? "9+" : n.unread;
  } catch { /* header widgets are best-effort */ }
}

const activeTasks = new Map();
/** Pages register long-running tasks so the header shows workflow status. */
export function trackTask(id, label) {
  activeTasks.set(id, label);
  paintStatus();
  const poll = async () => {
    try {
      const t = await get(`/api/tasks/${id}`);
      if (["queued", "running"].includes(t.status)) { activeTasks.set(id, t.message || label); paintStatus(); setTimeout(poll, 1500); return; }
      activeTasks.delete(id); paintStatus(); refreshCounts();
    } catch { activeTasks.delete(id); paintStatus(); }
  };
  setTimeout(poll, 1200);
}
function paintStatus() {
  if (!statusEl) return;
  statusEl.classList.toggle("running", activeTasks.size > 0);
  const label = activeTasks.size ? [...activeTasks.values()][0] + (activeTasks.size > 1 ? ` (+${activeTasks.size - 1})` : "") : "Ready";
  statusEl.lastChild.textContent = label;
}

// ---------------------------------------------------------------- router
export function setTitle(t) { document.title = t ? `${t} · JobPilot` : "JobPilot"; }

async function route() {
  if (!state.session) return;
  if (state.session.auth_required && !state.session.authenticated) return renderLogin();
  if (!shell) buildShell();
  const token = ++state.navToken;
  state.cleanup?.(); state.cleanup = null;
  renderNav();
  const path = location.pathname.replace(/\/+$/, "") || "/";
  const match = ROUTES.map((r) => ({ r, m: path.match(r.re) })).find((x) => x.m);
  mount(contentEl, loading(4));
  if (!match) {
    setTitle("Not found");
    mount(contentEl, h("div", { class: "empty" }, h("div", { class: "icon-wrap" }, icon("help", 24)), h("h3", null, "Page not found"),
      h("p", null, "This page doesn't exist."), h("a", { class: "btn btn-primary", href: "/" }, "Go to dashboard")));
    return;
  }
  try {
    const mod = await import(`./pages/${match.r.page}.js`);
    if (token !== state.navToken) return;
    const ctx = { root: contentEl, params: match.m.groups || {}, query: new URLSearchParams(location.search), navigate, setTitle, refreshCounts, trackTask,
      isCurrent: () => token === state.navToken };
    const cleanup = await mod.render(ctx);
    if (token === state.navToken) state.cleanup = typeof cleanup === "function" ? cleanup : null;
    contentEl.focus({ preventScroll: true });
    window.scrollTo(0, 0);
  } catch (e) {
    if (token !== state.navToken) return;
    console.error(e);
    mount(contentEl, errorState(e, () => route()));
  }
}

// ---------------------------------------------------------------- login
function renderLogin() {
  shell = null;
  setTitle("Sign in");
  const pw = h("input", { type: "password", id: "pw", autocomplete: "current-password", required: true });
  const err = h("div", { class: "field" });
  const btn = h("button", { class: "btn btn-primary", type: "submit", style: { width: "100%" } }, "Sign in");
  const form = h("form", { class: "card card-pad stack login-card", onsubmit: async (e) => {
    e.preventDefault(); clear(err);
    btn.disabled = true;
    try { await api("/api/auth/login", { method: "POST", body: { password: pw.value } }); state.session = await get("/api/session"); await route(); refreshCounts(); }
    catch (ex) { err.append(h("span", { class: "error", role: "alert" }, ex.message)); pw.setAttribute("aria-invalid", "true"); pw.focus(); }
    finally { btn.disabled = false; }
  } },
  h("div", { class: "row" }, h("span", { class: "brand-mark" }, icon("check", 16)), h("h1", { style: { fontSize: "22px" } }, "JobPilot")),
  h("p", { class: "text-2 small" }, "This private workspace holds your resume and job-search data. Sign in with the access password configured for this deployment."),
  !state.session.password_configured ? h("div", { class: "callout danger" }, icon("alert"), h("div", null, "The server has no access password configured (JOBPILOT_PASSWORD). The administrator must set it in the hosting environment.")) : null,
  h("label", { class: "field", for: "pw" }, "Access password", pw), err, btn);
  mount(app, h("main", { class: "login-wrap", id: "main" }, form));
  pw.focus();
}

// ---------------------------------------------------------------- boot
setUnauthorizedHandler(() => { if (state.session) { state.session.authenticated = false; renderLogin(); } });

(async function boot() {
  try {
    state.session = await get("/api/session");
  } catch (e) {
    mount(app, h("div", { class: "login-wrap" }, errorState(e, () => location.reload())));
    return;
  }
  route();
  if (!state.session.auth_required || state.session.authenticated) {
    refreshCounts();
    setInterval(() => { if (shell && document.visibilityState === "visible") refreshCounts(); }, 30000);
  }
})();
