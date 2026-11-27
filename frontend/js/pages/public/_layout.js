import { h, icon } from "../../ui/dom.js";
import { themeButton } from "../../main.js";

export function siteNav(session) {
  const authed = session?.authenticated;
  return h("header", { class: "site-nav" }, h("div", { class: "inner" },
    h("a", { class: "brand", href: "/", style: { border: 0, padding: 0, height: "auto" }, "aria-label": "JobPilot home" },
      h("span", { class: "brand-mark" }, icon("check", 16)), h("span", null, "JobPilot")),
    h("nav", { class: "links", "aria-label": "Site" },
      h("a", { href: "/#features" }, "Features"), h("a", { href: "/#how" }, "How it works"), h("a", { href: "/#pricing" }, "Pricing"),
      h("a", { href: "/#faq" }, "FAQ"), h("a", { href: "/demo" }, "Demo")),
    h("div", { class: "cta" }, themeButton(),
      authed ? h("a", { class: "btn btn-primary", href: "/app" }, "Open app")
        : [h("a", { class: "btn btn-ghost", href: "/login" }, "Sign in"), h("a", { class: "btn btn-primary", href: "/register" }, "Start free")])));
}

export function siteFooter() {
  return h("footer", { class: "site-footer" }, h("div", { class: "inner" },
    h("span", null, `© ${new Date().getFullYear()} JobPilot`),
    h("a", { href: "/privacy" }, "Privacy & analytics"), h("a", { href: "/terms" }, "Terms"), h("a", { href: "/demo" }, "Demo"),
    h("a", { href: "/#contact" }, "Contact"), h("span", { class: "right xs" }, "JobPilot never submits applications or sends messages for you.")));
}

export function page(session, ...children) {
  return h("div", { class: "site" }, siteNav(session), ...children, siteFooter());
}

export function formatPrice(p) {
  if (p.is_free_default || p.monthly_price === 0) return { main: "₹0", sub: "forever" };
  if (!p.price_set) return { main: "To be announced", sub: "pricing not set yet" };
  const v = p.effective_monthly_price ?? p.monthly_price;
  return { main: `₹${Number(v).toLocaleString("en-IN")}`, sub: "per month" + (p.discount_pct ? ` (${p.discount_pct}% off)` : "") };
}
