// App bootstrap: session, public site, signed-in app shell (/app), router, header widgets.
import { h, icon, mount, clear, fmt, debounce } from "./ui/dom.js";
import { get, post, setLimitHandler, setUnauthorizedHandler } from "./api.js";
import { errorState, loading, toast } from "./ui/components.js";

const NAV = [
  { group: "Workspace" },
  { path: "/app", label: "Dashboard", icon: "dashboard", exact: true },
  { path: "/app/discover", label: "Discover Jobs", icon: "search" },
  { path: "/app/saved", label: "Saved / Shortlisted", icon: "bookmark" },
  { path: "/app/resumes", label: "Resume Studio", icon: "file" },
  { path: "/app/applications", label: "Applications", icon: "briefcase" },
  { path: "/app/contacts", label: "Contacts & Outreach", icon: "users" },
  { path: "/app/approvals", label: "Approval Center", icon: "checkCircle", countKey: "approvals" },
  { group: "Insights" },
  { path: "/app/analytics", label: "Analytics", icon: "chart" },
  { path: "/app/roadmap", label: "Career Roadmap", icon: "target" },
  { path: "/app/activity", label: "Activity & Logs", icon: "activity" },
  { group: "Account" },
  { path: "/app/setup", label: "Profile", icon: "user" },
  { path: "/app/subscription", label: "Plan & Usage", icon: "layers" },
  { path: "/app/support", label: "Support", icon: "help" },
  { path: "/app/settings", label: "Settings", icon: "settings" },
];

const PUBLIC_ROUTES = [
  { re: /^\/$/, page: "public/landing" },
  { re: /^\/(login|register|forgot-password|reset-password|verify-email|setup-owner)$/, page: "public/auth" },
  { re: /^\/demo(\/.*)?$/, page: "public/demo" },
  { re: /^\/(privacy|terms)$/, page: "public/legal" },
];
const APP_ROUTES = [
  { re: /^\/app$/, page: "dashboard" },
  { re: /^\/app\/setup$/, page: "setup" },
  { re: /^\/app\/discover$/, page: "discover" },
  { re: /^\/app\/saved$/, page: "saved" },
  { re: /^\/app\/jobs\/(?<id>[\w-]+)$/, page: "job" },
  { re: /^\/app\/resumes$/, page: "resumes" },
  { re: /^\/app\/resumes\/(?<id>\d+)$/, page: "resume" },
  { re: /^\/app\/applications$/, page: "applications" },
  { re: /^\/app\/contacts$/, page: "contacts" },
  { re: /^\/app\/approvals$/, page: "approvals" },
  { re: /^\/app\/analytics$/, page: "analytics" },
  { re: /^\/app\/roadmap$/, page: "roadmap" },
  { re: /^\/app\/activity$/, page: "activity" },
  { re: /^\/app\/settings$/, page: "settings" },
  { re: /^\/app\/(subscription|usage)$/, page: "subscription" },
  { re: /^\/app\/support$/, page: "support" },
  { re: /^\/app\/account$/, page: "account" },
];

const app = document.getElementById("app");
export const state = { session: null, cleanup: null, counts: {}, navToken: 0, navigated: false };

// ---------------------------------------------------------------- theme
export function getTheme() { try { return localStorage.getItem("jobpilot.theme") || "system"; } catch { return "system"; } }
export function setTheme(t) {
  try { if (t === "system") localStorage.removeItem("jobpilot.theme"); else localStorage.setItem("jobpilot.theme", t); } catch { /* ignore */ }
  if (t === "system") document.documentElement.removeAttribute("data-theme"); else document.documentElement.setAttribute("data-theme", t);
  window.dispatchEvent(new CustomEvent("themechange"));
}
export function effectiveDark() {
  const t = getTheme();
  return t === "dark" || (t === "system" && window.matchMedia("(prefers-color-scheme: dark)").matches);
}
export function themeButton() {
  const btn = h("button", { class: "btn btn-ghost btn-icon", "aria-label": "Switch theme" });
  const paint = () => { mount(btn, icon(effectiveDark() ? "sun" : "moon")); btn.title = `Theme: ${getTheme()}`; };
  btn.addEventListener("click", () => { setTheme(effectiveDark() ? "light" : "dark"); paint(); });
  window.addEventListener("themechange", paint);
  paint();
  return btn;
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
  if (href.startsWith("/admin")) return; // separate admin application
  if (href.startsWith("/") && !href.startsWith("/api/") && !href.startsWith("/static/")) { e.preventDefault(); navigate(href); }
});

export async function refreshSession() {
  state.session = await get("/api/session");
  return state.session;
}

// ---------------------------------------------------------------- app shell
let shell, contentEl, navEl, statusEl, bellBtn, planEl, bannerEl;

function buildShell() {
  const collapsed = (() => { try { return localStorage.getItem("jobpilot.sidebar") === "collapsed"; } catch { return false; } })();
  navEl = h("nav", { class: "nav", "aria-label": "Main navigation" });
  const collapseBtn = h("button", { class: "btn btn-ghost desktop-only", style: { width: "100%", justifyContent: "flex-start", gap: "12px" },
    "aria-label": "Collapse or expand sidebar", onclick: () => {
      shell.classList.toggle("collapsed");
      try { localStorage.setItem("jobpilot.sidebar", shell.classList.contains("collapsed") ? "collapsed" : "open"); } catch { /* ignore */ }
    } }, icon("sidebar", 18), h("span", { class: "sidebar-foot-label" }, "Collapse sidebar"));
  const sidebar = h("aside", { class: "sidebar", id: "sidebar" },
    h("a", { class: "brand", href: "/app", "aria-label": "JobPilot dashboard" }, h("span", { class: "brand-mark" }, icon("check", 16)), h("span", { class: "brand-name" }, "JobPilot")),
    navEl, h("div", { class: "sidebar-foot" }, collapseBtn));
  statusEl = h("span", { class: "status-pill", role: "status", "aria-live": "polite" }, h("span", { class: "dot" }), "Ready");
  planEl = h("a", { class: "badge b-accent desktop-only", href: "/app/subscription", style: { textDecoration: "none" } });
  const header = h("header", { class: "header" },
    h("button", { class: "btn btn-ghost btn-icon mobile-only", "aria-label": "Open navigation", "aria-controls": "sidebar", onclick: () => toggleMobileNav(true) }, icon("menu")),
    searchBox(), statusEl, planEl,
    h("div", { class: "header-actions" }, notificationsButton(), themeButton(), profileMenu()));
  contentEl = h("main", { class: "content", id: "main", tabindex: "-1" });
  bannerEl = h("div", { id: "global-banner" });
  shell = h("div", { class: `shell ${collapsed ? "collapsed" : ""}` }, sidebar, h("div", { class: "main-col" }, bannerEl, header, contentEl));
  mount(app, shell);
  window.addEventListener("offline", () => mount(bannerEl, h("div", { class: "banner warn", role: "alert" }, icon("globe", 16), "You are offline. Changes can't be saved until the connection returns.")));
  window.addEventListener("online", () => paintBanners());
  renderNav();
  paintPlan();
  paintBanners();
}

async function paintBanners() {
  if (!bannerEl) return;
  clear(bannerEl);
  try {
    const anns = await get("/api/public/announcements");
    anns.forEach((a) => bannerEl.append(h("div", { class: `announcement ${a.level}`, role: "status" }, icon(a.level === "warning" ? "alert" : "info", 16),
      h("strong", null, a.title), a.body ? h("span", null, " — ", a.body) : null)));
  } catch { /* banners are best-effort */ }
  const u = state.session?.user;
  if (u && !u.email_verified && state.session.config?.email_enabled) {
    bannerEl.append(h("div", { class: "banner warn" }, icon("mail", 16), "Please verify your email address.",
      h("button", { class: "btn btn-sm", onclick: async () => { try { await post("/api/auth/resend-verification"); toast("Verification email sent", "success"); } catch (e) { toast(e.message, "error"); } } }, "Resend email")));
  }
}

function paintPlan() {
  if (!planEl) return;
  const usage = state.session?.usage;
  if (!usage) { planEl.hidden = true; return; }
  planEl.hidden = false;
  const t = usage.trial;
  clear(planEl);
  if (usage.source === "trial" && t) planEl.append(icon("clock", 12), `${usage.plan.name} trial · ${t.days_remaining} day(s) left`);
  else planEl.append(icon("layers", 12), `${usage.plan.name} plan`);
}

function toggleMobileNav(open) {
  shell.classList.toggle("nav-open", open);
  const existing = document.querySelector(".backdrop");
  if (open && !existing) document.body.append(h("div", { class: "backdrop", onclick: () => toggleMobileNav(false) }));
  if (!open && existing) existing.remove();
}

function renderNav() {
  if (!navEl) return;
  clear(navEl);
  const path = location.pathname;
  for (const item of NAV) {
    if (item.group) { navEl.append(h("div", { class: "nav-group" }, item.group)); continue; }
    const active = item.exact ? path === item.path : (path.startsWith(item.path) || (item.path === "/app/discover" && path.startsWith("/app/jobs/")));
    const count = item.countKey ? state.counts[item.countKey] : 0;
    navEl.append(h("a", { href: item.path, "aria-current": active ? "page" : null, title: item.label, onclick: () => toggleMobileNav(false) },
      icon(item.icon, 18), h("span", { class: "nav-label" }, item.label), count ? h("span", { class: "count", "aria-label": `${count} pending` }, count) : null));
  }
  if (state.session?.is_staff) {
    navEl.append(h("div", { class: "nav-group" }, "Staff"), h("a", { href: "/admin", title: "Admin panel" }, icon("shield", 18), h("span", { class: "nav-label" }, "Admin panel")));
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
      add("Jobs", r.jobs, (j) => h("a", { href: `/app/jobs/${j.id}`, role: "option" }, `${j.title} — ${j.company || ""}`, h("span", { class: "xs muted" }, ` · ${j.status_label}`)));
      add("Applications", r.applications, (a) => h("a", { href: `/app/applications?open=${a.id}`, role: "option" }, `${a.title} — ${a.company}`));
      add("Contacts", r.contacts, (c) => h("a", { href: `/app/contacts?q=${encodeURIComponent(c.name)}`, role: "option" }, `${c.name}${c.company ? " · " + c.company : ""}`));
      if (!results.childNodes.length) results.append(h("div", { class: "group" }, "No matches"));
      results.hidden = false;
    } catch { results.hidden = true; }
  }, 250);
  input.addEventListener("input", run);
  input.addEventListener("keydown", (e) => {
    const links = [...results.querySelectorAll("a")];
    if (e.key === "ArrowDown" && links.length) { e.preventDefault(); links[0].focus(); }
    if (e.key === "Escape") { results.hidden = true; input.blur(); }
  });
  results.addEventListener("keydown", (e) => {
    const links = [...results.querySelectorAll("a")]; const i = links.indexOf(document.activeElement);
    if (e.key === "ArrowDown") { e.preventDefault(); links[Math.min(i + 1, links.length - 1)]?.focus(); }
    if (e.key === "ArrowUp") { e.preventDefault(); if (i <= 0) input.focus(); else links[i - 1].focus(); }
    if (e.key === "Escape") { results.hidden = true; input.focus(); }
  });
  results.addEventListener("click", () => { results.hidden = true; input.value = ""; });
  document.addEventListener("click", (e) => { if (!wrap.contains(e.target)) results.hidden = true; });
  document.addEventListener("keydown", (e) => { if (e.key === "/" && shell && !["INPUT", "TEXTAREA", "SELECT"].includes(document.activeElement.tagName)) { e.preventDefault(); input.focus(); } });
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
        h("div", { class: "row", style: { gap: "6px" } }, icon(n.level === "error" || n.level === "warning" ? "alert" : "info", 14), h("strong", { class: "small" }, n.title)),
        h("div", { class: "xs muted" }, fmt.dateTime(n.at)))) : h("div", { class: "empty", style: { padding: "24px" } }, h("p", null, "No notifications yet.")));
      if (r.unread) { post("/api/notifications/read").then(refreshCounts).catch(() => {}); }
      return h("div", null, h("div", { class: "card-head" }, h("h3", null, "Notifications"), h("a", { href: "/app/activity", class: "small", onclick: close }, "Activity & Logs")), list);
    } catch (e) { return errorState(e); }
  });
}

function profileMenu() {
  const btn = h("button", { class: "btn btn-ghost btn-icon", "aria-label": "Account menu" }, icon("user"));
  return popoverButton(btn, (close) => h("div", { class: "menu" },
    h("div", { class: "small", style: { padding: "8px 10px" } }, h("strong", null, state.session?.user?.name || "Your account"), h("div", { class: "xs muted" }, state.session?.user?.email)),
    h("a", { href: "/app/account", onclick: close }, icon("user", 16), "Account & privacy"),
    h("a", { href: "/app/subscription", onclick: close }, icon("layers", 16), "Plan & usage"),
    h("a", { href: "/app/setup", onclick: close }, icon("file", 16), "Profile & master resume"),
    h("a", { href: "/app/settings", onclick: close }, icon("settings", 16), "Settings"),
    state.session?.is_staff ? h("a", { href: "/admin" }, icon("shield", 16), "Admin panel") : null,
    h("button", { onclick: async () => { await post("/api/auth/logout"); location.href = "/"; } }, icon("logout", 16), "Sign out")));
}

// ---------------------------------------------------------------- header polling & tasks
export async function refreshCounts() {
  if (!shell) return;
  try {
    const [a, n] = await Promise.all([get("/api/approvals"), get("/api/notifications")]);
    state.counts.approvals = a.items.length;
    renderNav();
    const dot = bellBtn.querySelector(".badge-dot");
    if (n.unread && !dot) bellBtn.append(h("span", { class: "badge-dot", "aria-label": `${n.unread} unread` }, n.unread > 9 ? "9+" : n.unread));
    else if (dot && !n.unread) dot.remove();
    else if (dot) dot.textContent = n.unread > 9 ? "9+" : n.unread;
  } catch { /* best-effort */ }
}

const activeTasks = new Map();
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
  statusEl.lastChild.textContent = activeTasks.size ? [...activeTasks.values()][0] + (activeTasks.size > 1 ? ` (+${activeTasks.size - 1})` : "") : "Ready";
}

// ---------------------------------------------------------------- router
export function setTitle(t) { document.title = t ? `${t} · JobPilot` : "JobPilot — truthful job search"; }

async function route() {
  if (!state.session) return;
  const token = ++state.navToken;
  state.cleanup?.(); state.cleanup = null;
  const path = location.pathname.replace(/\/+$/, "") || "/";
  const isApp = path === "/app" || path.startsWith("/app/");
  if (isApp && !state.session.authenticated) {
    navigate(`/login?next=${encodeURIComponent(location.pathname + location.search)}`, { replace: true });
    return;
  }
  const table = isApp ? APP_ROUTES : PUBLIC_ROUTES;
  const match = table.map((r) => ({ r, m: path.match(r.re) })).find((x) => x.m);
  let root;
  if (isApp) {
    if (!shell) { buildShell(); refreshCounts(); }
    renderNav(); paintPlan();
    root = contentEl;
    mount(root, loading(4));
  } else {
    shell = null; contentEl = null; navEl = null; planEl = null; bannerEl = null;
    root = app;
  }
  if (!match) {
    setTitle("Not found");
    mount(root, h("div", { class: "empty" }, h("div", { class: "icon-wrap" }, icon("help", 24)), h("h3", null, "Page not found"),
      h("p", null, "This page doesn't exist."), h("a", { class: "btn btn-primary", href: state.session.authenticated ? "/app" : "/" }, "Go home")));
    return;
  }
  try {
    const mod = await import(`./pages/${match.r.page}.js`);
    if (token !== state.navToken) return;
    const ctx = { root, params: match.m.groups || {}, query: new URLSearchParams(location.search), navigate, setTitle, refreshCounts, trackTask,
      isCurrent: () => token === state.navToken, session: state.session, refreshSession, path, match: match.m };
    const cleanup = await mod.render(ctx);
    if (token === state.navToken) state.cleanup = typeof cleanup === "function" ? cleanup : null;
    if (state.navigated && isApp) contentEl.focus({ preventScroll: true });
    state.navigated = true;
    window.scrollTo(0, 0);
  } catch (e) {
    if (token !== state.navToken) return;
    console.error(e);
    mount(root, errorState(e, () => route()));
  }
}

// ---------------------------------------------------------------- boot
setUnauthorizedHandler(async () => {
  await refreshSession().catch(() => {});
  if (location.pathname.startsWith("/app")) navigate(`/login?next=${encodeURIComponent(location.pathname)}`, { replace: true });
});
setLimitHandler((data) => {
  const msg = data?.detail || "This isn't included in your current plan.";
  const host = document.getElementById("toasts");
  const t = h("div", { class: "toast warning", role: "alert" }, icon("layers", 18),
    h("div", { class: "grow" }, h("div", null, msg), h("a", { href: "/app/subscription", class: "btn btn-sm btn-accent mt-2", onclick: () => t.remove() }, "See plans & usage")),
    h("button", { class: "btn btn-ghost btn-icon btn-sm", "aria-label": "Dismiss", onclick: () => t.remove() }, icon("x", 14)));
  host.append(t);
  setTimeout(() => t.remove(), 12000);
});

(async function boot() {
  try {
    await refreshSession();
  } catch (e) {
    mount(app, h("div", { class: "login-wrap" }, errorState(e, () => location.reload())));
    return;
  }
  route();
  setInterval(async () => {
    if (shell && document.visibilityState === "visible") {
      refreshCounts();
      try { await refreshSession(); paintPlan(); } catch { /* ignore */ }
    }
  }, 60000);
})();
