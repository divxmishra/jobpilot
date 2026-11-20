// Reusable UI components. Each returns DOM nodes; none uses innerHTML.
import { h, icon, fmt, clear, append } from "./dom.js";
import { upload as uploadFile } from "../api.js";

// ---------------------------------------------------------------- status badges
// Consistent labels + icon (status is never communicated by colour alone).
const STATUS = {
  new: ["New", "b-info", "info"], needs_review: ["Needs Review", "b-warning", "alert"], qualified: ["Qualified", "b-success", "checkCircle"],
  near_match: ["Near Match", "b-neutral", "target"], below_threshold: ["Below Threshold", "b-neutral", "xCircle"],
  not_eligible: ["Not Eligible", "b-danger", "xCircle"], shortlisted: ["Shortlisted", "b-brand", "bookmark"], dismissed: ["Dismissed", "b-neutral", "x"],
  expired: ["Expired", "b-neutral", "clock"], error: ["Error", "b-danger", "alert"],
  draft: ["Draft Ready", "b-info", "file"], approved: ["Approved", "b-success", "check"], superseded: ["Superseded", "b-neutral", "layers"],
  prepared: ["Prepared", "b-info", "briefcase"], applied: ["Applied (user-confirmed)", "b-brand", "check"], interview: ["Interview", "b-success", "users"],
  offer: ["Offer", "b-success", "checkCircle"], rejected: ["Rejected", "b-danger", "xCircle"], withdrawn: ["Withdrawn", "b-neutral", "x"],
  pass: ["Passed", "b-success", "check"], fail: ["Failed", "b-danger", "xCircle"], needs_verification: ["Needs verification", "b-warning", "help"],
  verified: ["Verified", "b-success", "check"], unverified: ["Unverified", "b-neutral", "help"], uncertain: ["Uncertain", "b-warning", "alert"],
  missing: ["Missing", "b-neutral", "help"], public_unverified: ["Publicly listed · unverified", "b-warning", "globe"], inferred: ["Inferred", "b-neutral", "help"],
  sent_by_user: ["Sent by you", "b-brand", "mail"],
  active: ["Open", "b-success", "check"], closed: ["Closed", "b-danger", "xCircle"], not_found: ["Removed (404)", "b-danger", "xCircle"],
  inaccessible: ["Unable to verify", "b-warning", "help"], unknown: ["Not verified", "b-neutral", "help"],
};

export function badge(status, labelOverride) {
  const [label, cls, ic] = STATUS[status] || [fmt.cap(status || "unknown"), "b-neutral", "info"];
  return h("span", { class: `badge ${cls}` }, icon(ic, 12), labelOverride || label);
}

export function scoreRing(score, { size = "", threshold = 85, label = "match" } = {}) {
  const s = score ?? 0;
  const color = score === null || score === undefined ? "var(--text-3)" : s >= threshold ? "var(--success)" : s >= threshold - 10 ? "var(--warning)" : "var(--danger)";
  const el = h("div", { class: `ring ${size}`, role: "img", "aria-label": score == null ? `No ${label} score` : `${s}% ${label}` }, h("span", null, score == null ? "—" : `${s}%`));
  el.style.setProperty("--p", s); el.style.setProperty("--c", color);
  return el;
}

export function meter(value, max = 100, kind = "") {
  const pct = Math.max(0, Math.min(100, (value / (max || 1)) * 100));
  return h("div", { class: `meter ${kind}`, role: "meter", "aria-valuenow": value, "aria-valuemin": 0, "aria-valuemax": max }, h("div", { style: { width: `${pct}%` } }));
}

// ---------------------------------------------------------------- states
export function loading(lines = 3) {
  return h("div", { class: "stack", "aria-busy": "true", "aria-label": "Loading" },
    Array.from({ length: lines }, (_, i) => h("div", { class: "skeleton", style: { height: i === 0 ? "22px" : "64px", width: i === 0 ? "40%" : "100%" } })));
}

export function emptyState({ icon: ic = "inbox", title, body, action }) {
  return h("div", { class: "empty" }, h("div", { class: "icon-wrap" }, icon(ic, 24)), h("h3", null, title), body ? h("p", null, body) : null, action || null);
}

export function errorState(err, retry) {
  const offline = err && err.status === 0;
  return h("div", { class: "empty error", role: "alert" }, h("div", { class: "icon-wrap" }, icon(offline ? "globe" : "alert", 24)),
    h("h3", null, offline ? "Server unavailable" : err?.status === 403 ? "Permission required" : "Something went wrong"),
    h("p", null, err?.message || String(err)),
    retry ? h("button", { class: "btn", onclick: retry }, icon("refresh", 16), "Try again") : null);
}

export function callout(kind, text, ic) {
  const icons = { info: "info", warn: "alert", danger: "xCircle", success: "checkCircle" };
  return h("div", { class: `callout ${kind}`, role: kind === "danger" ? "alert" : null }, icon(ic || icons[kind] || "info", 18), h("div", { class: "grow" }, text));
}

// ---------------------------------------------------------------- toasts
export function toast(message, kind = "info", { timeout = 4500 } = {}) {
  let prefs = { toasts: true };
  try { prefs = JSON.parse(localStorage.getItem("jobpilot.notif") || "{}"); } catch { /* ignore */ }
  if (prefs.toasts === false && kind !== "error") return;
  const host = document.getElementById("toasts");
  const ic = { success: "checkCircle", error: "alert", warning: "alert", info: "info" }[kind];
  const t = h("div", { class: `toast ${kind}`, role: kind === "error" ? "alert" : "status" }, icon(ic, 18), h("div", { class: "grow" }, message),
    h("button", { class: "btn btn-ghost btn-icon btn-sm", "aria-label": "Dismiss", onclick: () => t.remove() }, icon("x", 14)));
  host.append(t);
  if (timeout) setTimeout(() => t.remove(), kind === "error" ? timeout * 2 : timeout);
}

// ---------------------------------------------------------------- dialogs
/**
 * Accessible modal using <dialog>. Returns {el, close}.
 */
export function modal({ title, body, footer, wide = false, drawer = false, onClose }) {
  const dlg = h("dialog", { class: drawer ? "drawer" : "", "aria-labelledby": "dlg-title" });
  const close = () => { dlg.close(); };
  dlg.addEventListener("close", () => { dlg.remove(); onClose?.(); });
  dlg.addEventListener("click", (e) => { if (e.target === dlg) close(); });
  const box = h("div", { class: `modal ${wide ? "wide" : ""}` },
    h("div", { class: "modal-head" }, h("h2", { id: "dlg-title", style: { fontSize: "18px" } }, title),
      h("button", { class: "btn btn-ghost btn-icon btn-sm", "aria-label": "Close dialog", onclick: close }, icon("x", 16))),
    h("div", { class: "modal-body" }, body), footer ? h("div", { class: "modal-foot" }, footer) : null);
  dlg.append(box);
  document.body.append(dlg);
  dlg.showModal();
  return { el: dlg, box, close };
}

/** Promise<boolean>. Use for destructive / irreversible actions. */
export function confirmDialog({ title, message, confirmLabel = "Confirm", danger = false, typed }) {
  return new Promise((resolve) => {
    let done = false;
    const input = typed ? h("input", { type: "text", "aria-label": `Type ${typed} to confirm`, autocomplete: "off" }) : null;
    const okBtn = h("button", { class: `btn ${danger ? "btn-danger" : "btn-primary"}`, disabled: !!typed,
      onclick: () => { done = true; m.close(); resolve(true); } }, confirmLabel);
    if (input) input.addEventListener("input", () => { okBtn.disabled = input.value !== typed; });
    const m = modal({
      title, body: h("div", { class: "stack" }, h("p", null, message), typed ? h("label", { class: "field" }, `Type "${typed}" to confirm`, input) : null),
      footer: [h("button", { class: "btn", onclick: () => m.close() }, "Cancel"), okBtn],
      onClose: () => { if (!done) resolve(false); },
    });
    (input || okBtn).focus();
  });
}

// ---------------------------------------------------------------- busy buttons
/** Disables a button while an async action runs (prevents duplicate submissions). */
export async function withBusy(btn, fn) {
  if (btn.disabled) return;
  const original = Array.from(btn.childNodes);
  btn.disabled = true; btn.setAttribute("aria-busy", "true");
  clear(btn); append(btn, [h("span", { class: "spinner", "aria-hidden": "true" }), original.filter((n) => n.nodeType === 3).map((n) => n.textContent).join("") || "Working…"]);
  try { return await fn(); }
  finally { btn.disabled = false; btn.removeAttribute("aria-busy"); clear(btn); append(btn, original); }
}

// ---------------------------------------------------------------- task progress
export function taskProgress(label = "Working…", { onCancel } = {}) {
  const msg = h("span", { class: "small text-2" }, label);
  const bar = h("div", { class: "meter progress-indeterminate" }, h("div"));
  const cancel = onCancel ? h("button", { class: "btn btn-sm", onclick: async (e) => { e.currentTarget.disabled = true; await onCancel(); } }, "Cancel") : null;
  const el = h("div", { class: "card card-pad stack", role: "status", "aria-live": "polite" }, h("div", { class: "row-between" }, h("div", { class: "row" }, h("span", { class: "spinner" }), msg), cancel), bar);
  return {
    el,
    update(t) {
      msg.textContent = t.message || label;
      if (t.progress > 0) { bar.classList.remove("progress-indeterminate"); bar.firstChild.style.width = `${Math.round(t.progress * 100)}%`; }
    },
  };
}

// ---------------------------------------------------------------- filter bar
/**
 * @param {{filters: {key:string,label:string,type:'text'|'select',options?:[string,string][],placeholder?:string}[], values: Record<string,string>, onChange:(v:Record<string,string>)=>void}} cfg
 */
export function filterBar({ filters, values, onChange }) {
  const state = { ...values };
  const chips = h("div", { class: "row", "aria-label": "Active filters" });
  const inputs = {};
  const emit = () => { renderChips(); onChange({ ...state }); };
  const wrap = h("div", { class: "card card-pad stack", role: "search" });
  const grid = h("div", { class: "row", style: { gap: "10px", alignItems: "flex-end" } });
  for (const f of filters) {
    let input;
    if (f.type === "select") {
      input = h("select", { id: `f-${f.key}`, onchange: () => { state[f.key] = input.value; emit(); } },
        h("option", { value: "" }, f.placeholder || "Any"), f.options.map(([v, l]) => h("option", { value: v, selected: state[f.key] === v }, l)));
    } else {
      let t;
      input = h("input", { id: `f-${f.key}`, type: f.type === "number" ? "number" : "search", placeholder: f.placeholder || "", value: state[f.key] || "",
        oninput: () => { clearTimeout(t); t = setTimeout(() => { state[f.key] = input.value.trim(); emit(); }, 350); } });
    }
    inputs[f.key] = input;
    grid.append(h("label", { class: "field", style: { minWidth: f.width || "150px", flex: f.grow ? "1" : "0 1 auto" }, for: `f-${f.key}` }, f.label, input));
  }
  const reset = h("button", { class: "btn btn-ghost", onclick: () => { for (const f of filters) { state[f.key] = ""; inputs[f.key].value = ""; } emit(); } }, icon("x", 14), "Reset");
  grid.append(reset);
  function renderChips() {
    clear(chips);
    for (const f of filters) {
      const v = state[f.key];
      if (!v) continue;
      const label = f.type === "select" ? (f.options.find((o) => o[0] === v) || [v, v])[1] : v;
      chips.append(h("span", { class: "chip" }, `${f.label}: ${label}`, h("button", { "aria-label": `Remove filter ${f.label}`, onclick: () => { state[f.key] = ""; inputs[f.key].value = ""; emit(); } }, icon("x", 12))));
    }
    chips.hidden = !chips.childNodes.length;
  }
  renderChips();
  wrap.append(grid, chips);
  return wrap;
}

export function pagination({ page, pageSize, total, onPage }) {
  const pages = Math.max(1, Math.ceil(total / pageSize));
  if (pages <= 1) return h("div");
  return h("nav", { class: "row-between mt-4", "aria-label": "Pagination" },
    h("span", { class: "small muted" }, `Page ${page} of ${pages} · ${total} results`),
    h("div", { class: "row" },
      h("button", { class: "btn btn-sm", disabled: page <= 1, onclick: () => onPage(page - 1) }, icon("chevronLeft", 14), "Previous"),
      h("button", { class: "btn btn-sm", disabled: page >= pages, onclick: () => onPage(page + 1) }, "Next", icon("chevronRight", 14))));
}

// ---------------------------------------------------------------- job card
const MODE = { remote: "Remote", hybrid: "Hybrid", onsite: "On-site", unknown: "Mode not stated" };

export function jobNextAction(j) {
  return {
    qualified: "Review evidence, then shortlist", shortlisted: "Generate a tailored resume", needs_review: "Verify flagged requirements",
    near_match: "Below threshold — not recommended", new: "Waiting for evaluation", expired: "Posting closed — no action",
    not_eligible: "Failed a hard requirement", below_threshold: "Not a match", dismissed: "Dismissed by you",
  }[j.status] || "Open details";
}

export function jobCard(j, { threshold = 85, navigate, actions } = {}) {
  const open = (e) => { e.preventDefault(); navigate(`/jobs/${j.id}`); };
  const dupCount = (j.alt_urls || []).length;
  return h("article", { class: "card job-card", "aria-labelledby": `jt-${j.id}` },
    h("div", null,
      h("div", { class: "row", style: { gap: "6px", marginBottom: "4px" } }, badge(j.status, j.status_label), j.page_status && j.page_status !== "active" ? badge(j.page_status) : null,
        dupCount ? h("span", { class: "badge b-outline", title: "Same job found on multiple sources (deduplicated)" }, icon("layers", 12), `Seen on ${dupCount + 1} sources`) : null,
        !j.extraction_complete ? h("span", { class: "badge b-warning" }, icon("alert", 12), "Incomplete extraction") : null),
      h("a", { href: `/jobs/${j.id}`, id: `jt-${j.id}`, class: "title", onclick: open }, j.title || "Untitled role"),
      h("div", { class: "company" }, j.company || "Unknown company"),
      h("div", { class: "meta" },
        h("span", null, icon("map", 14), j.location || "Location not stated"),
        h("span", null, icon("briefcase", 14), MODE[j.work_mode] || "Mode not stated", j.employment_type ? ` · ${j.employment_type}` : ""),
        h("span", { title: "Posting date as stated by the source" }, icon("clock", 14), j.posted_at ? `Posted ${fmt.date(j.posted_at)}` : "Posting date not stated"),
        j.verified_at ? h("span", { title: "Last time JobPilot checked the posting" }, icon("check", 14), `Verified ${fmt.ago(j.verified_at)}`) : null,
        j.salary_text ? h("span", null, icon("rupee", 14), j.salary_text) : null,
        h("span", null, icon("globe", 14), `Source: ${j.source}`))),
    h("div", { class: "side" }, scoreRing(j.match_score, { threshold }), h("span", { class: "xs muted" }, "resume match")),
    h("div", { class: "actions" }, h("span", { class: "next-action" }, icon("chevronRight", 14), "Next: ", jobNextAction(j)),
      h("span", { class: "grow" }),
      j.source_url ? h("a", { class: "btn btn-sm btn-ghost", href: j.source_url, target: "_blank", rel: "noopener noreferrer" }, icon("external", 14), "Source") : null,
      actions || null,
      h("a", { class: "btn btn-sm", href: `/jobs/${j.id}`, onclick: open }, "View details")));
}

// ---------------------------------------------------------------- score breakdown
export function gatesList(gates, { onConfirm } = {}) {
  if (!gates?.length) return h("p", { class: "muted" }, "Not evaluated yet.");
  return h("div", null, gates.map((g) => h("div", { class: "gate" },
    badge(g.result),
    h("div", null, h("div", { style: { fontWeight: 600 } }, g.label, g.user_confirmed ? h("span", { class: "badge b-outline", style: { marginLeft: "6px" } }, "confirmed by you") : null),
      h("div", { class: "small text-2" }, g.detail), g.evidence ? h("div", { class: "ev" }, `JD: "${g.evidence.slice(0, 220)}"`) : null),
    g.result === "needs_verification" && onConfirm ? h("button", { class: "btn btn-sm", onclick: (e) => onConfirm(g, e.currentTarget) }, "I meet this") : h("span"))));
}

export function scoreBreakdown(match, threshold = 85) {
  if (!match) return h("p", { class: "muted" }, "Not scored yet.");
  const rows = match.components.map((c) => h("div", { class: "bd-row" },
    h("div", null, h("div", { style: { fontWeight: 600 } }, c.label), h("div", { class: "xs muted" }, c.detail)),
    c.applicable ? meter(c.points, c.weight, c.points / c.weight >= 0.85 ? "ok" : c.points / c.weight >= 0.5 ? "warn" : "bad") : h("span", { class: "xs muted" }, "not applicable"),
    h("span", { class: "pts" }, c.applicable ? `${c.points} / ${c.weight}` : "n/a")));
  return h("div", { class: "stack" },
    h("div", { class: "row" }, scoreRing(match.score, { size: "lg", threshold }),
      h("div", null, h("div", { style: { fontWeight: 650, fontSize: "16px" } }, `${match.score}% resume-to-JD match`),
        h("div", { class: "small text-2" }, `Threshold ${threshold}% · confidence ${match.confidence} · rubric ${match.rubric_version}`),
        h("div", { class: "xs muted" }, "Score uses only your verified profile facts and is never rounded up. Weights of non-applicable components are excluded."))),
    h("div", { class: "breakdown" }, rows),
    match.uncertainties?.length ? callout("warn", h("div", null, h("strong", null, "Uncertainty"), h("ul", { class: "evidence-list" }, match.uncertainties.map((u) => h("li", null, u))))) : null);
}

// ---------------------------------------------------------------- uploader
export function uploader({ endpoint, accept = ".pdf,.docx", maxMb = 8, label = "Drop your resume here", hint = "PDF or DOCX, up to 8 MB", onDone }) {
  const input = h("input", { type: "file", accept, class: "sr-only", id: `up-${Math.random().toString(36).slice(2)}` });
  const status = h("div", { class: "stack", style: { width: "100%", maxWidth: "420px" } });
  const zone = h("label", { class: "dropzone", for: input.id, tabindex: "0", role: "button", "aria-describedby": `${input.id}-hint` },
    h("div", { class: "icon-wrap", style: { color: "var(--brand-text)" } }, icon("upload", 28)),
    h("strong", null, label), h("span", { class: "small text-2", id: `${input.id}-hint` }, hint, " · or ", h("span", { style: { color: "var(--brand-text)", fontWeight: 600 } }, "browse files")), status);
  zone.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); input.click(); } });
  ["dragenter", "dragover"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.add("over"); }));
  ["dragleave", "drop"].forEach((ev) => zone.addEventListener(ev, (e) => { e.preventDefault(); zone.classList.remove("over"); }));
  zone.addEventListener("drop", (e) => { const f = e.dataTransfer.files[0]; if (f) start(f); });
  input.addEventListener("change", () => { if (input.files[0]) start(input.files[0]); input.value = ""; });
  let last = null;
  async function start(file) {
    last = file;
    clear(status);
    const exts = accept.split(",").map((s) => s.trim().toLowerCase());
    const ext = file.name.slice(file.name.lastIndexOf(".")).toLowerCase();
    if (!exts.includes(ext)) return showError(`"${file.name}" is not a supported file type (${accept}).`, false);
    if (file.size > maxMb * 1024 * 1024) return showError(`"${file.name}" is ${(file.size / 1048576).toFixed(1)} MB; the limit is ${maxMb} MB.`, false);
    const bar = meter(0, 100);
    const msg = h("span", { class: "small" }, `Uploading ${file.name}…`);
    status.append(msg, bar);
    try {
      const res = await uploadFile(endpoint, file, (p) => { bar.firstChild.style.width = `${Math.round(p * 100)}%`; if (p >= 1) msg.textContent = "Extracting information…"; });
      clear(status); status.append(h("span", { class: "badge b-success" }, icon("check", 12), `${file.name} processed`));
      onDone?.(res);
    } catch (e) { showError(e.message, true); }
  }
  function showError(message, canRetry) {
    clear(status);
    status.append(callout("danger", message), canRetry ? h("button", { type: "button", class: "btn btn-sm", onclick: (e) => { e.preventDefault(); start(last); } }, icon("refresh", 14), "Retry") : null);
  }
  return h("div", null, zone, input);
}

// ---------------------------------------------------------------- sortable table
/**
 * @param {{columns:{key:string,label:string,render?:(row:any)=>any,sort?:boolean}[], rows:any[], onRow?:(r:any)=>void, empty?:Node, sortKey?:string}} cfg
 */
export function dataTable({ columns, rows, onRow, empty, sortKey, sortDir = "asc" }) {
  let key = sortKey, dir = sortDir;
  const wrap = h("div", { class: "table-wrap" });
  function render() {
    clear(wrap);
    if (!rows.length) { wrap.append(empty || emptyState({ title: "Nothing here yet" })); return; }
    const sorted = key ? [...rows].sort((a, b) => {
      const x = a[key] ?? "", y = b[key] ?? "";
      const r = typeof x === "number" && typeof y === "number" ? x - y : String(x).localeCompare(String(y));
      return dir === "asc" ? r : -r;
    }) : rows;
    wrap.append(h("table", { class: "table responsive" },
      h("thead", null, h("tr", null, columns.map((c) => h("th", { scope: "col", "aria-sort": key === c.key ? (dir === "asc" ? "ascending" : "descending") : null },
        c.sort === false ? c.label : h("button", { onclick: () => { dir = key === c.key && dir === "asc" ? "desc" : "asc"; key = c.key; render(); } }, c.label,
          key === c.key ? icon(dir === "asc" ? "chevronUp" : "chevronDown", 12) : null))))),
      h("tbody", null, sorted.map((r) => h("tr", { class: onRow ? "clickable" : "", tabindex: onRow ? "0" : null,
        onclick: onRow ? () => onRow(r) : null, onkeydown: onRow ? (e) => { if (e.key === "Enter") onRow(r); } : null },
        columns.map((c) => h("td", { "data-label": c.label }, c.render ? c.render(r) : (r[c.key] ?? "—"))))))));
  }
  render();
  return wrap;
}

export function copyButton(getText, label = "Copy") {
  return h("button", { class: "btn btn-sm", onclick: async () => {
    try { await navigator.clipboard.writeText(getText()); toast("Copied to clipboard", "success"); }
    catch { toast("Clipboard unavailable — select the text and copy manually", "warning"); }
  } }, icon("copy", 14), label);
}
