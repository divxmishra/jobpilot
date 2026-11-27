import { h, mount } from "../../ui/dom.js";
import { callout } from "../../ui/components.js";
import { page } from "./_layout.js";

export async function render(ctx) {
  const privacy = ctx.match[1] === "privacy";
  ctx.setTitle(privacy ? "Privacy & analytics" : "Terms");
  const S = (title, ...items) => h("section", { class: "mb-4" }, h("h2", { style: { fontSize: "18px", marginBottom: "8px" } }, title), ...items);
  const ul = (...li) => h("ul", { class: "evidence-list" }, li.map((t) => h("li", null, t)));
  const body = privacy ? [
    S("What we store", ul("Your account (email, name, hashed password or Google identity).",
      "Your uploaded resume and the profile facts you confirm, your preferences (including notice period and expected CTC), saved jobs, generated documents, applications and contacts.",
      "Security records: sessions and failed sign-in attempts (IP addresses are stored only as one-way hashes).")),
    S("How your data is used", ul("To evaluate jobs against your verified profile and prepare documents you request.",
      "Your data is isolated to your account. Staff can see account and usage information to run the service, but admin analytics never include resume contents, application answers or messages.",
      "Optional AI assistance only sends job-description text, never your resume, and only when enabled.")),
    S("Product analytics", ul("We record privacy-conscious usage events (for example: 'job search', 'resume tailored', success or failure) to understand whether JobPilot helps and to fix problems.",
      "We never record keystrokes, screen contents, clipboard data, passwords, resume text or message bodies.",
      "You can opt out of non-essential analytics at any time in Account & privacy. Security, billing and account-lifecycle events are still recorded.")),
    S("Your controls", ul("Export all your data (Account & privacy → Export).", "Delete your account and personal data permanently (Account & privacy → Delete account).",
      "Payment records, if any, are kept for accounting with your identity removed.")),
    S("Backups & security", ul("Data is backed up in encrypted form. Passwords are hashed with scrypt; sessions use secure, HTTP-only cookies.")),
  ] : [
    S("Using JobPilot", ul("JobPilot helps you evaluate jobs and prepare applications. It never submits applications or sends messages for you; you remain responsible for what you submit.",
      "Scores (including the ATS estimate) are JobPilot's own estimates, not employer decisions or guarantees of interviews or offers.",
      "Don't upload content you don't have the right to use, and keep your profile truthful.")),
    S("Plans & trials", ul("New accounts can receive a free trial that starts when profile setup is completed. When it ends, the account moves to the Free plan automatically — no automatic charges.",
      "Online payments are not enabled. If paid plans are introduced, prices and terms will be shown before any purchase.")),
    S("Availability", ul("The service may be unavailable during maintenance. We may suspend accounts that abuse the service.")),
  ];
  mount(ctx.root, page(ctx.session, h("main", { class: "content", id: "main", style: { maxWidth: "820px" } },
    h("h1", { class: "mb-4" }, privacy ? "Privacy & analytics disclosure" : "Terms of use"),
    callout("warn", "Plain-language summary of how this JobPilot deployment works. The service owner should review it with legal advice before public launch."),
    h("div", { class: "card card-pad mt-4" }, body))));
}
