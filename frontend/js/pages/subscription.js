import { h, icon, mount, fmt } from "../ui/dom.js";
import { get, post } from "../api.js";
import { callout, errorState, loading, meter, modal, withBusy } from "../ui/components.js";
import { formatPrice } from "./public/_layout.js";
import { pageHead, section } from "./_common.js";

export async function render(ctx) {
  ctx.setTitle("Plan & usage");
  const load = async () => {
    mount(ctx.root, loading(4));
    let s;
    try { s = await get("/api/subscription"); } catch (e) { mount(ctx.root, errorState(e, load)); return; }
    if (!ctx.isCurrent()) return;
    const t = s.trial, cur = s.current;
    const trialWindow = t && t.status === "active"
      ? h("section", { class: "card trial-window" }, h("div", { class: "row-between" },
        h("div", null, h("div", { class: "small text-2" }, `${t.plan_name} trial`), h("div", { class: "trial-days" }, t.days_remaining, h("span", { style: { fontSize: "16px" } }, " day(s) left")),
          h("div", { class: "small mt-2" }, `Started ${fmt.date(t.started_at)} · ends ${fmt.date(t.ends_at)}`)),
        h("div", { class: "small", style: { maxWidth: "420px" } }, h("strong", null, "After the trial: "), s.after_trial)))
      : t && t.status !== "active"
        ? callout("info", `Your ${t.plan_name || ""} trial ${t.status === "revoked" ? "was ended" : "has ended"}. ${s.after_trial}`)
        : callout("info", `Your ${s.trial_config.days}-day ${s.trial_config.plan} trial starts ${s.trial_config.starts}.`);
    const usage = s.usage;
    const quote = (p) => {
      const period = h("select", { id: "q-period" }, h("option", { value: "monthly" }, "Monthly"), p.annual_enabled ? h("option", { value: "annual" }, "Annual") : null);
      const coupon = h("input", { type: "text", id: "q-coupon", placeholder: "Optional" });
      const out = h("div");
      const btn = h("button", { class: "btn btn-primary" }, "Check price");
      const m = modal({ title: `Upgrade to ${p.name}`, body: h("div", { class: "stack" },
        h("label", { class: "field", for: "q-period" }, "Billing period", period), h("label", { class: "field", for: "q-coupon" }, "Coupon code", coupon), out,
        callout("info", "Online payments aren't enabled yet, so nothing can be charged. Contact support if you'd like to upgrade.")),
      footer: [h("a", { class: "btn", href: "/app/support" }, "Contact support"), btn] });
      btn.addEventListener("click", () => withBusy(btn, async () => {
        try { const q = await post("/api/subscription/quote", { plan_code: p.code, period: period.value, coupon: coupon.value.trim() || null });
          mount(out, callout("success", `${q.plan}, ${q.period}: ₹${q.amount.toLocaleString("en-IN")}${q.coupon ? ` (coupon ${q.coupon.code} −${q.coupon.discount_pct}%)` : ""}. ${q.message}`)); }
        catch (e) { mount(out, callout("danger", e.message)); }
      }));
      void m;
    };
    mount(ctx.root,
      pageHead({ title: "Plan & usage", desc: "Your plan, trial status and remaining usage. Limits are enforced by the server and reset on the dates shown." }),
      h("div", { class: "grid grid-3 mb-4" },
        h("div", { class: "card stat accent" }, h("span", { class: "label" }, icon("layers", 15), "Current plan"), h("span", { class: "value" }, cur.plan.name),
          h("span", { class: "hint" }, { trial: "Free trial", free: "Free plan", staff: "Staff access", admin_grant: "Granted by admin", free_grant: "Complimentary access", lifetime: "Lifetime access", manual_payment: "Paid (recorded)", payment: "Paid" }[cur.source] || cur.source)),
        h("div", { class: "card stat" }, h("span", { class: "label" }, icon("clock", 15), "Plan ends"), h("span", { class: "value", style: { fontSize: "20px" } },
          cur.subscription?.ends_at ? fmt.date(cur.subscription.ends_at) : cur.source === "trial" && t ? fmt.date(t.ends_at) : "No end date")),
        h("div", { class: "card stat" }, h("span", { class: "label" }, icon("rupee", 15), "Payments"), h("span", { class: "value", style: { fontSize: "20px" } }, "Not enabled"), h("span", { class: "hint" }, "You'll never be charged automatically"))),
      h("div", { class: "mb-4" }, trialWindow),
      h("div", { class: "split-wide" },
        section("Usage", h("div", null, usage.limits.map((l) => h("div", { class: "usage-row" },
          h("div", null, h("strong", { class: "small" }, l.label), l.resets_on ? h("div", { class: "xs muted" }, `Resets ${fmt.date(l.resets_on)}`) : null),
          l.limit === null ? h("span", { class: "xs muted" }, "Unlimited") : meter(l.used, l.limit || 1, l.limit && l.used >= l.limit ? "bad" : l.used / (l.limit || 1) > .8 ? "warn" : "ok"),
          h("span", { class: "small nowrap" }, l.limit === null ? `${l.used} used` : `${l.used} / ${l.limit}` + (l.limit ? ` · ${l.remaining} left` : "")))))),
        section("Features", h("ul", { class: "evidence-list" }, usage.features.map((f) => h("li", null, f.enabled ? "✓ " : "✗ ", f.label, f.enabled ? "" : " — not included"))))),
      h("h2", { class: "mt-4 mb-4", style: { fontSize: "20px" } }, "Plans"),
      h("div", { class: "pricing" }, s.plans.map((p) => {
        const price = formatPrice(p);
        const isCurrent = p.code === cur.plan.code;
        return h("article", { class: `card price-card ${isCurrent ? "featured" : ""}` }, isCurrent ? h("span", { class: "ribbon" }, "Current") : null,
          h("h3", null, p.name), h("div", { class: "price" }, price.main, " ", h("small", null, price.sub)),
          h("ul", null, p.features.map((f) => h("li", null, icon("check", 14), f))), h("span", { class: "grow" }),
          !isCurrent && !p.is_free_default ? h("button", { class: "btn btn-accent", disabled: !p.price_set, onclick: () => quote(p) }, p.price_set ? `Upgrade to ${p.name}` : "Pricing to be announced") : null);
      })),
      s.billing_history.length ? section("Billing history", h("ul", { class: "evidence-list" }, s.billing_history.map((b) => h("li", null, `${fmt.date(b.created_at)} — ${b.kind} ₹${b.amount} (${b.status})${b.is_manual ? " · recorded manually" : ""}`)))) : null);
  };
  await load();
}
