import { h, icon } from "../ui/dom.js";

/** Page header with optional breadcrumbs (nested pages) and actions. */
export function pageHead({ title, desc, actions, crumbs }) {
  return h("div", null,
    crumbs ? h("nav", { class: "breadcrumbs", "aria-label": "Breadcrumb" }, crumbs.map((c, i) => [
      i ? icon("chevronRight", 12) : null,
      c.href ? h("a", { href: c.href }, c.label) : h("span", { "aria-current": "page" }, c.label)])) : null,
    h("div", { class: "page-head" }, h("div", null, h("h1", null, title), desc ? h("p", null, desc) : null),
      actions ? h("div", { class: "page-actions" }, actions) : null));
}

export function section(title, body, { actions, id } = {}) {
  return h("section", { class: "card", id, "aria-labelledby": id ? `${id}-h` : null },
    h("div", { class: "card-head" }, h("h2", { id: id ? `${id}-h` : null }, title), actions ? h("div", { class: "row" }, actions) : null),
    h("div", { class: "card-body" }, body));
}

/** Remember list context (filters/tab) so returning from a detail page restores it. */
export function rememberListUrl(key) { try { sessionStorage.setItem(`jobpilot.back.${key}`, location.pathname + location.search); } catch { /* ignore */ } }
export function backUrl(key, fallback) { try { return sessionStorage.getItem(`jobpilot.back.${key}`) || fallback; } catch { return fallback; } }
