import { h, icon, mount } from "../../ui/dom.js";
import { api, post } from "../../api.js";
import { callout, withBusy } from "../../ui/components.js";
import { page } from "./_layout.js";

const ERRORS = {
  oauth_state: "Google sign-in expired. Please try again.", oauth_failed: "Google sign-in failed. Please try again.",
  email_unverified: "Your Google email isn't verified.", suspended: "This account is suspended.", registration_closed: "New registrations are currently closed.",
};

export async function render(ctx) {
  const mode = ctx.match[1];
  const cfg = ctx.session.config;
  const next = ctx.query.get("next");
  const goNext = (staff) => { location.href = next && next.startsWith("/") ? next : staff && mode === "setup-owner" ? "/admin" : "/app"; };
  if (ctx.session.authenticated && ["login", "register"].includes(mode)) { ctx.navigate(next || "/app", { replace: true }); return; }
  const out = h("div", { role: "alert" });
  const field = (id, label, type = "text", attrs = {}) => {
    const inp = h("input", { id, type, ...attrs });
    return [h("label", { class: "field", for: id }, label, inp, attrs.hint ? h("span", { class: "hint" }, attrs.hint) : null), inp];
  };
  const fail = (e) => mount(out, callout("danger", e.message));
  const googleBtn = cfg.google_login ? [h("a", { class: "btn google-btn", href: "/api/auth/google/start" }, icon("globe", 16), "Continue with Google"),
    h("div", { class: "divider" }, "or")] : [];
  let card;

  if (mode === "login") {
    ctx.setTitle("Sign in");
    const [fe, email] = field("email", "Email", "email", { autocomplete: "email", required: true });
    const [fp, pw] = field("pw", "Password", "password", { autocomplete: "current-password", required: true });
    const btn = h("button", { class: "btn btn-primary", type: "submit", style: { width: "100%" } }, "Sign in");
    card = h("form", { class: "card auth-card stack", novalidate: true, onsubmit: (e) => { e.preventDefault(); withBusy(btn, async () => {
      try { const r = await post("/api/auth/login", { email: email.value.trim(), password: pw.value }); goNext(r.is_staff); } catch (er) { fail(er); pw.select(); }
    }); } },
    h("h1", null, "Welcome back"), h("p", { class: "text-2 small" }, "Sign in to your JobPilot account."),
    ctx.query.get("error") ? callout("danger", ERRORS[ctx.query.get("error")] || "Sign-in failed.") : null,
    ...googleBtn, fe, fp, out, btn,
    h("div", { class: "row-between small" }, h("a", { href: "/forgot-password" }, "Forgot password?"),
      cfg.registration_open ? h("a", { href: "/register" }, "Create an account") : null));
  } else if (mode === "register") {
    ctx.setTitle("Create account");
    if (!cfg.registration_open) {
      card = h("div", { class: "card auth-card stack" }, h("h1", null, "Registrations are closed"),
        h("p", { class: "text-2" }, "New accounts aren't being accepted right now. You can still explore the demo."), h("a", { class: "btn btn-primary", href: "/demo" }, "Explore the demo"));
    } else {
      const [fn, name] = field("name", "Your name", "text", { autocomplete: "name" });
      const [fe, email] = field("email", "Email", "email", { autocomplete: "email", required: true });
      const [fp, pw] = field("pw", "Password", "password", { autocomplete: "new-password", required: true, hint: "At least 10 characters, mixing letters with numbers or symbols" });
      const terms = h("input", { type: "checkbox", id: "terms" });
      const btn = h("button", { class: "btn btn-accent", type: "submit", style: { width: "100%" } }, "Create account");
      card = h("form", { class: "card auth-card stack", novalidate: true, onsubmit: (e) => { e.preventDefault(); withBusy(btn, async () => {
        try { await post("/api/auth/register", { name: name.value.trim(), email: email.value.trim(), password: pw.value, accept_terms: terms.checked }); location.href = "/app/setup"; }
        catch (er) { fail(er); }
      }); } },
      h("h1", null, "Create your account"),
      h("p", { class: "text-2 small" }, `Free to start. Your ${cfg.trial_days}-day ${cfg.trial_plan || "Pro"} trial begins when you finish profile setup.`),
      ...googleBtn, fn, fe, fp,
      h("label", { class: "check", for: "terms" }, terms, h("span", null, "I agree to the ", h("a", { href: "/terms", target: "_blank" }, "terms"), " and ", h("a", { href: "/privacy", target: "_blank" }, "privacy policy"))),
      out, btn, h("p", { class: "small" }, "Already have an account? ", h("a", { href: "/login" }, "Sign in")));
    }
  } else if (mode === "forgot-password") {
    ctx.setTitle("Reset password");
    const [fe, email] = field("email", "Email", "email", { autocomplete: "email" });
    const btn = h("button", { class: "btn btn-primary", type: "submit" }, "Send reset link");
    card = h("form", { class: "card auth-card stack", novalidate: true, onsubmit: (e) => { e.preventDefault(); withBusy(btn, async () => {
      try { const r = await post("/api/auth/forgot-password", { email: email.value.trim() }); mount(out, callout(r.email_enabled ? "success" : "info", r.message)); } catch (er) { fail(er); }
    }); } }, h("h1", null, "Forgot your password?"), h("p", { class: "text-2 small" }, "We'll email you a single-use link if an account exists."), fe, out, btn,
    h("a", { class: "small", href: "/login" }, "Back to sign in"));
  } else if (mode === "reset-password") {
    ctx.setTitle("Choose a new password");
    const [fp, pw] = field("pw", "New password", "password", { autocomplete: "new-password", hint: "At least 10 characters" });
    const btn = h("button", { class: "btn btn-primary", type: "submit" }, "Set password");
    card = h("form", { class: "card auth-card stack", novalidate: true, onsubmit: (e) => { e.preventDefault(); withBusy(btn, async () => {
      try { await post("/api/auth/reset-password", { token: ctx.query.get("token") || "", password: pw.value }); mount(out, callout("success", "Password updated. You can sign in now.")); }
      catch (er) { fail(er); }
    }); } }, h("h1", null, "Choose a new password"), fp, out, btn, h("a", { class: "small", href: "/login" }, "Sign in"));
  } else if (mode === "verify-email") {
    ctx.setTitle("Verify email");
    card = h("div", { class: "card auth-card stack" }, h("h1", null, "Verifying your email…"), out);
    api("/api/auth/verify-email", { method: "POST", body: { token: ctx.query.get("token") || "" } })
      .then(() => mount(out, callout("success", h("span", null, "Email verified. ", h("a", { href: "/app" }, "Continue to JobPilot")))))
      .catch(fail);
  } else {
    ctx.setTitle("Owner setup");
    if (cfg.owner_exists) {
      card = h("div", { class: "card auth-card stack" }, h("h1", null, "Setup complete"), h("p", { class: "text-2" }, "The owner account already exists."), h("a", { class: "btn btn-primary", href: "/login" }, "Sign in"));
    } else {
      const [fs, secret] = field("secret", "Owner setup secret", "password", { autocomplete: "off", hint: "The value of OWNER_SETUP_TOKEN (or JOBPILOT_PASSWORD) set in your hosting environment" });
      const [fn, name] = field("name", "Your name", "text");
      const [fe, email] = field("email", "Owner email", "email", { autocomplete: "email" });
      const [fp, pw] = field("pw", "Owner password", "password", { autocomplete: "new-password", hint: "At least 10 characters" });
      const btn = h("button", { class: "btn btn-primary", type: "submit" }, "Create owner account");
      card = h("form", { class: "card auth-card stack", novalidate: true, onsubmit: (e) => { e.preventDefault(); withBusy(btn, async () => {
        try { await post("/api/auth/bootstrap-owner", { setup_secret: secret.value, name: name.value.trim(), email: email.value.trim(), password: pw.value }); location.href = "/admin"; }
        catch (er) { fail(er); }
      }); } }, h("h1", null, "Create the owner account"),
      h("p", { class: "text-2 small" }, "One-time setup. The owner is the only account with unrestricted admin access. Two-factor authentication is set up next."),
      fs, fn, fe, fp, out, btn);
    }
  }
  mount(ctx.root, page(ctx.session, h("main", { class: "auth-wrap", id: "main" }, card)));
  card.querySelector("input")?.focus();
}
