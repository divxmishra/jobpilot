import { h, icon, mount, fmt } from "../ui/dom.js";
import { api, get, patch, post } from "../api.js";
import { callout, confirmDialog, errorState, loading, toast, withBusy } from "../ui/components.js";
import { pageHead, section } from "./_common.js";

export async function render(ctx) {
  ctx.setTitle("Account & privacy");
  mount(ctx.root, loading(3));
  let a;
  try { a = await get("/api/account"); } catch (e) { mount(ctx.root, errorState(e)); return; }
  const u = a.user;
  const name = h("input", { type: "text", id: "a-name", value: u.name || "" });
  const optOut = h("input", { type: "checkbox", id: "a-opt", checked: !!u.analytics_opt_out });
  const cur = h("input", { type: "password", id: "a-cur", autocomplete: "current-password" });
  const nw = h("input", { type: "password", id: "a-new", autocomplete: "new-password" });
  const pwOut = h("div", { role: "alert" });
  mount(ctx.root, pageHead({ title: "Account & privacy", desc: "Your account details, password, analytics choices, data export and deletion." }),
    h("div", { class: "split" },
      section("Account", h("div", { class: "stack" },
        h("div", { class: "small" }, h("strong", null, "Email: "), u.email, " ", u.email_verified ? h("span", { class: "badge b-success" }, "verified") : h("span", { class: "badge b-neutral" }, a.email_enabled ? "not verified" : "verification unavailable")),
        h("div", { class: "small" }, h("strong", null, "Member since: "), fmt.date(u.created_at), u.google_linked ? " · Google sign-in linked" : ""),
        h("label", { class: "field", for: "a-name" }, "Name", name),
        h("button", { class: "btn btn-primary", style: { alignSelf: "flex-start" }, onclick: (e) => withBusy(e.currentTarget, async () => {
          try { await patch("/api/account", { name: name.value }); toast("Saved", "success"); await ctx.refreshSession(); } catch (er) { toast(er.message, "error"); } }) }, "Save"))),
      section("Password", h("div", { class: "stack" },
        u.has_password ? h("label", { class: "field", for: "a-cur" }, "Current password", cur) : h("p", { class: "small text-2" }, "You sign in with Google. You can also set a password."),
        h("label", { class: "field", for: "a-new" }, "New password", h("span", { class: "hint" }, "At least 10 characters; other devices will be signed out"), nw), pwOut,
        h("button", { class: "btn", style: { alignSelf: "flex-start" }, onclick: (e) => withBusy(e.currentTarget, async () => {
          try { await post("/api/account/password", { current_password: cur.value || null, new_password: nw.value }); cur.value = nw.value = ""; mount(pwOut, callout("success", "Password changed")); }
          catch (er) { mount(pwOut, callout("danger", er.message)); } }) }, "Change password"))),
      section("Analytics & privacy", h("div", { class: "stack" },
        h("p", { class: "small text-2" }, "JobPilot records privacy-conscious usage events (never resume text, answers, messages or keystrokes). ", h("a", { href: "/privacy" }, "Read the disclosure")),
        h("label", { class: "check", for: "a-opt" }, optOut, "Opt out of non-essential usage analytics"),
        h("button", { class: "btn", style: { alignSelf: "flex-start" }, onclick: (e) => withBusy(e.currentTarget, async () => {
          try { await patch("/api/account", { analytics_opt_out: optOut.checked }); toast("Privacy preference saved", "success"); } catch (er) { toast(er.message, "error"); } }) }, "Save preference"))),
      section("Your data", h("div", { class: "stack" },
        h("a", { class: "btn", href: "/api/data/export", download: "", style: { alignSelf: "flex-start" } }, icon("download", 16), "Export all my data"),
        h("hr", { style: { border: 0, borderTop: "1px solid var(--border)", width: "100%" } }),
        h("p", { class: "small text-2" }, "Delete your account and all personal data: profile, resumes, jobs, applications, contacts and settings. This cannot be undone."),
        h("button", { class: "btn btn-danger", style: { alignSelf: "flex-start" }, onclick: async () => {
          const ok = await confirmDialog({ title: "Delete your account?", message: "Everything in your account is permanently deleted. Export first if you want a copy.", confirmLabel: "Delete my account", danger: true, typed: "DELETE MY ACCOUNT" });
          if (!ok) return;
          const pw = u.has_password ? await prompt_password() : null;
          if (u.has_password && pw === null) return;
          try { await api("/api/account/delete", { method: "POST", body: { confirm: "DELETE MY ACCOUNT", password: pw } }); location.href = "/"; } catch (er) { toast(er.message, "error"); }
        } }, icon("trash", 16), "Delete account")))));

  function prompt_password() {
    return new Promise((resolve) => {
      const pw = h("input", { type: "password", autocomplete: "current-password", "aria-label": "Password" });
      const dlg = h("dialog", { class: "" }, h("div", { class: "modal" }, h("div", { class: "modal-head" }, h("h2", { style: { fontSize: "18px" } }, "Confirm with your password")),
        h("div", { class: "modal-body" }, pw), h("div", { class: "modal-foot" }, h("button", { class: "btn", onclick: () => { dlg.close(); resolve(null); } }, "Cancel"),
          h("button", { class: "btn btn-danger", onclick: () => { dlg.close(); resolve(pw.value); } }, "Delete permanently"))));
      document.body.append(dlg); dlg.showModal(); pw.focus();
      dlg.addEventListener("close", () => dlg.remove());
    });
  }
}
