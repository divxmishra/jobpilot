import { h, icon, mount, fmt } from "../ui/dom.js";
import { get } from "../api.js";
import { badge, callout, dataTable, emptyState, errorState, loading } from "../ui/components.js";
import { pageHead, section } from "./_common.js";

export async function render(ctx) {
  ctx.setTitle("Resume Studio");
  const load = async () => {
    mount(ctx.root, loading(3));
    let rows, prof;
    try { [rows, prof] = await Promise.all([get("/api/resumes"), get("/api/profile")]); } catch (e) { mount(ctx.root, errorState(e, load)); return; }
    if (!ctx.isCurrent()) return;
    const active = prof.active;
    mount(ctx.root,
      pageHead({ title: "Resume Studio", desc: "Tailored resumes are separate, versioned derivatives of your verified master profile. They only select and order facts you confirmed — never invent them." }),
      h("div", { class: "split-wide" },
        section("Tailored resumes", dataTable({
          rows: rows.map((r) => ({ ...r, label: `${r.title} — ${r.company}` })),
          sortKey: "created_at", sortDir: "desc",
          onRow: (r) => ctx.navigate(`/app/resumes/${r.id}`),
          empty: emptyState({ icon: "file", title: "No tailored resumes yet", body: "Open a qualifying job and choose “Generate tailored resume”. Tailoring is only offered for jobs that pass every hard gate and your match threshold.",
            action: h("a", { class: "btn btn-primary", href: "/app/saved" }, "Go to shortlisted jobs") }),
          columns: [
            { key: "label", label: "Job", render: (r) => h("a", { href: `/app/resumes/${r.id}` }, r.label) },
            { key: "version", label: "Version", render: (r) => `v${r.version}${r.edited ? " (edited)" : ""}` },
            { key: "status", label: "State", render: (r) => badge(r.status) },
            { key: "ats_score", label: "Est. ATS", render: (r) => h("span", { class: `badge ${r.ats_score >= 95 ? "b-success" : r.ats_score >= 90 ? "b-warning" : "b-neutral"}` }, `${r.ats_score}/100`) },
            { key: "truthful", label: "Truthfulness", render: (r) => r.truthful ? badge("pass", "Traceable") : badge("fail", "Unsupported claims") },
            { key: "created_at", label: "Created", render: (r) => fmt.date(r.created_at) },
          ] })),
        h("div", { class: "stack" },
          section("Master profile", active ? h("div", { class: "stack small" },
            h("div", { class: "row" }, h("span", { class: "badge b-success" }, icon("lock", 12), "Read-only"), `Version v${active.version}`),
            h("p", { class: "text-2" }, "Tailoring never edits your master profile or original resume. To change facts, start a versioned profile update."),
            h("div", { class: "row" }, h("a", { class: "btn btn-sm", href: "/app/setup" }, "Open profile"), h("a", { class: "btn btn-sm", href: "/api/profile/cv.md", target: "_blank", rel: "noopener" }, "cv.md")))
            : callout("info", h("span", null, "No verified profile yet. ", h("a", { href: "/app/setup" }, "Set it up first.")))),
          section("About the ATS score", h("ul", { class: "evidence-list small" },
            h("li", null, "JobPilot's own estimated compatibility score (ats-v1), computed from the text of the actual rendered PDF/DOCX."),
            h("li", null, "Not an employer's ATS score and not a guarantee of passing screening."),
            h("li", null, "95+ is a target. If it can't be reached truthfully, the real score is shown, labelled “Below 95 target – truthful version”, with reasons."),
            h("li", null, "Bands: 95–100 target achieved · 90–94 close · below 90 significant gap."))))));
  };
  await load();
}
