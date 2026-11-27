// Private admin application (/admin). Server enforces every permission; the UI only reflects it.
import { h, icon, mount, clear } from "../ui/dom.js";
import { get, post } from "../api.js";
import { callout, errorState, loading, toast, withBusy } from "../ui/components.js";
import { PAGES } from "./pages.js";

const NAV = [
  ["Platform", null], ["overview", "Overview", "dashboard"], ["users", "Users", "users"], ["activity", "User Activity", "activity"],
  ["Business", null], ["subscriptions", "Subscriptions", "layers"], ["revenue", "Revenue", "rupee"], ["plans", "Plans & Pricing", "file"],
  ["trials", "Trials", "clock"], ["limits", "Feature Limits", "filter"], ["analytics", "Analytics", "chart"],
  ["Operations", null], ["health", "System Health", "checkCircle"], ["ai", "AI Usage & Costs", "target"], ["sources", "Job Sources", "globe"],
  ["announcements", "Announcements", "bell"], ["support", "Support", "help"],
  ["Governance", null], ["audit", "Audit Logs", "shield"], ["security", "Security", "lock"], ["settings", "Settings", "settings"],
];
const app = document.getElementById("app");
const state = { session: null, token: 0 };

export function navigate(path) { history.pushState({}, "", path); route(); }
window.addEventListener("popstate", () => route());
document.addEventListener("click", (e) => {
  const a = e.target.closest("a[href]");
  if (!a || a.target === "_blank" || a.hasAttribute("download") || e.ctrlKey || e.metaKey || e.button !== 0) return;
  const href = a.getAttribute("href");
  if (href.startsWith("/admin")) { e.preventDefault(); navigate(href); }
});

function gate(title, body) {
  mount(app, h("main", { class: "login-wrap", id: "main" }, h("div", { class: "card card-pad stack login-card", style: { width: "440px" } },
    h("div", { class: "row" }, h("span", { class: "brand-mark" }, icon("shield", 16)), h("h1", { style: { fontSize: "20px" } }, title)), body)));
}

async function securityGate() {
  const s = state.session;
  if (!s.authenticated) { location.href = "/login?next=/admin"; return false; }
  if (!s.is_staff) { gate("Not permitted", h("div", { class: "stack" }, h("p", { class: "text-2" }, "This area is restricted to JobPilot staff."), h("a", { class: "btn", href: "/app" }, "Back to the app"))); return false; }
  if (!s.user.totp_enabled) {
    let setup;
    try { setup = await post("/api/auth/2fa/setup"); } catch (e) { gate("Two-factor setup", errorState(e)); return false; }
    const code = h("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 6, "aria-label": "6-digit code" });
    const out = h("div", { role: "alert" });
    const btn = h("button", { class: "btn btn-primary" }, "Enable two-factor");
    btn.addEventListener("click", () => withBusy(btn, async () => {
      try { await post("/api/auth/2fa/enable", { code: code.value.trim() }); location.reload(); } catch (e) { mount(out, callout("danger", e.message)); }
    }));
    gate("Set up two-factor authentication", h("div", { class: "stack" },
      h("p", { class: "small text-2" }, "Admin access requires an authenticator app. Add this key manually (it's shown only now):"),
      h("code", { style: { fontSize: "15px", letterSpacing: ".08em", wordBreak: "break-all", padding: "10px", background: "var(--surface-3)", borderRadius: "8px" } }, setup.secret.replace(/(.{4})/g, "$1 ").trim()),
      h("details", null, h("summary", { class: "small" }, "Setup URI (for apps that accept links)"), h("code", { class: "xs", style: { wordBreak: "break-all" } }, setup.otpauth_uri)),
      h("label", { class: "field" }, "Enter the 6-digit code", code), out, btn));
    code.focus();
    return false;
  }
  if (!s.elevated) {
    const code = h("input", { type: "text", inputmode: "numeric", autocomplete: "one-time-code", maxlength: 6, "aria-label": "6-digit code" });
    const out = h("div", { role: "alert" });
    const btn = h("button", { class: "btn btn-primary", type: "submit" }, "Verify");
    gate("Verify it's you", h("form", { class: "stack", onsubmit: (e) => { e.preventDefault(); withBusy(btn, async () => {
      try { await post("/api/auth/2fa/verify", { code: code.value.trim() }); location.reload(); } catch (er) { mount(out, callout("danger", er.message)); }
    }); } }, h("p", { class: "small text-2" }, "Enter the code from your authenticator app to open the admin panel."), h("label", { class: "field" }, "6-digit code", code), out, btn));
    code.focus();
    return false;
  }
  return true;
}

let shell, content, nav;
function buildShell() {
  nav = h("nav", { class: "nav", "aria-label": "Admin navigation" });
  content = h("main", { class: "content", id: "main", tabindex: "-1" });
  shell = h("div", { class: "shell" },
    h("aside", { class: "sidebar", id: "sidebar" },
      h("a", { class: "brand", href: "/admin" }, h("span", { class: "brand-mark" }, icon("shield", 16)), h("span", { class: "brand-name" }, "JobPilot Admin")), nav,
      h("div", { class: "sidebar-foot" }, h("a", { class: "btn btn-ghost", href: "/app", style: { width: "100%", justifyContent: "flex-start" } }, icon("chevronLeft", 16), h("span", { class: "sidebar-foot-label" }, "Back to app")))),
    h("div", { class: "main-col" },
      h("header", { class: "header" },
        h("button", { class: "btn btn-ghost btn-icon mobile-only", "aria-label": "Open navigation", onclick: () => shell.classList.toggle("nav-open") }, icon("menu")),
        h("strong", null, "Admin"), h("span", { class: "badge b-accent" }, icon("lock", 12), `${state.session.user.role} · 2FA verified`),
        h("div", { class: "header-actions" }, h("span", { class: "small text-2 desktop-only" }, state.session.user.email),
          h("button", { class: "btn btn-ghost btn-sm", onclick: async () => { await post("/api/auth/logout"); location.href = "/"; } }, icon("logout", 14), "Sign out"))),
      content));
  mount(app, shell);
}

function renderNav(key) {
  clear(nav);
  for (const [k, label, ic] of NAV) {
    if (!label) { nav.append(h("div", { class: "nav-group" }, k)); continue; }
    nav.append(h("a", { href: `/admin/${k}`, "aria-current": k === key ? "page" : null, onclick: () => shell.classList.remove("nav-open") }, icon(ic, 18), h("span", { class: "nav-label" }, label)));
  }
}

async function route() {
  const token = ++state.token;
  const parts = location.pathname.replace(/\/+$/, "").split("/").slice(2);
  const key = parts[0] || "overview";
  if (!shell) buildShell();
  renderNav(key);
  const pageFn = PAGES[key];
  document.title = `${(NAV.find((n) => n[0] === key) || [0, "Admin"])[1]} · JobPilot Admin`;
  mount(content, loading(4));
  if (!pageFn) { mount(content, callout("info", "Unknown admin page.")); return; }
  try {
    await pageFn({ root: content, params: parts.slice(1), query: new URLSearchParams(location.search), navigate, role: state.session.user.role,
      isCurrent: () => token === state.token, me: state.session.user });
  } catch (e) {
    if (token !== state.token) return;
    if (e.status === 428) { location.reload(); return; }
    mount(content, errorState(e, () => route()));
  }
  window.scrollTo(0, 0);
}

(async function boot() {
  try { state.session = await get("/api/session"); } catch (e) { mount(app, errorState(e, () => location.reload())); return; }
  if (await securityGate()) route();
})();
export { toast };
