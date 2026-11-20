// Minimal accessible SVG charts. Single-series bar charts only (one hue = no
// colour-identity problem), with hover tooltips, an accessible table view and an
// honest "not enough data" state instead of invented values.
import { h, icon, clear } from "./dom.js";

const NS = "http://www.w3.org/2000/svg";
const s = (tag, attrs = {}) => { const el = document.createElementNS(NS, tag); for (const [k, v] of Object.entries(attrs)) el.setAttribute(k, v); return el; };

let tip;
function showTip(e, text) {
  if (!tip) { tip = h("div", { class: "chart-tip", role: "tooltip" }); document.body.append(tip); }
  tip.textContent = text; tip.hidden = false;
  tip.style.left = `${Math.min(e.clientX + 12, window.innerWidth - 180)}px`; tip.style.top = `${e.clientY - 34}px`;
}
function hideTip() { if (tip) tip.hidden = true; }

/**
 * @param {{title:string, data:{label:string,value:number,note?:string}[], valueLabel?:string, minPoints?:number, horizontal?:boolean, emptyText?:string, fmt?:(n:number)=>string}} cfg
 */
export function barChart({ title, data, valueLabel = "Count", minPoints = 1, horizontal = false, emptyText, fmt = (n) => String(n) }) {
  const card = h("section", { class: "card" });
  const body = h("div", { class: "card-body" });
  let tableMode = false;
  const toggle = h("button", { class: "btn btn-sm btn-ghost", "aria-pressed": "false", onclick: () => { tableMode = !tableMode; toggle.setAttribute("aria-pressed", String(tableMode)); toggle.lastChild.textContent = tableMode ? "Chart view" : "Table view"; render(); } },
    icon("layers", 14), "Table view");
  card.append(h("div", { class: "card-head" }, h("h3", null, title), data.length ? toggle : null), body);
  const total = data.reduce((a, d) => a + (d.value || 0), 0);

  function render() {
    clear(body);
    if (data.length < minPoints || total === 0) {
      body.append(h("div", { class: "empty", style: { padding: "28px 10px" } }, h("div", { class: "icon-wrap" }, icon("chart", 22)),
        h("strong", null, "Not enough data yet"), h("p", { class: "small" }, emptyText || "This chart appears once there is real data. Nothing is estimated or filled in.")));
      return;
    }
    if (tableMode) {
      body.append(h("div", { class: "table-wrap" }, h("table", { class: "table" }, h("caption", { class: "sr-only" }, title),
        h("thead", null, h("tr", null, h("th", { scope: "col" }, "Category"), h("th", { scope: "col" }, valueLabel))),
        h("tbody", null, data.map((d) => h("tr", null, h("td", null, d.label), h("td", null, fmt(d.value), d.note ? ` (${d.note})` : "")))))));
      return;
    }
    const max = Math.max(...data.map((d) => d.value), 1);
    if (horizontal) {
      const rowH = 30, labelW = 150, W = 560, H = data.length * rowH + 8;
      const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": `${title}: ${data.map((d) => `${d.label} ${fmt(d.value)}`).join(", ")}` });
      data.forEach((d, i) => {
        const y = i * rowH + 4, bw = Math.max(2, ((W - labelW - 60) * d.value) / max);
        const lbl = s("text", { x: labelW - 8, y: y + 17, "text-anchor": "end" }); lbl.textContent = d.label.length > 22 ? d.label.slice(0, 21) + "…" : d.label;
        const bar = s("rect", { x: labelW, y: y + 4, width: bw, height: rowH - 10, rx: 4, fill: "var(--chart-1)" });
        const val = s("text", { x: labelW + bw + 6, y: y + 17 }); val.textContent = fmt(d.value);
        const hit = s("rect", { x: 0, y, width: W, height: rowH, fill: "transparent" });
        hit.addEventListener("mousemove", (e) => showTip(e, `${d.label}: ${fmt(d.value)}${d.note ? ` · ${d.note}` : ""}`)); hit.addEventListener("mouseleave", hideTip);
        svg.append(lbl, bar, val, hit);
      });
      body.append(svg);
      return;
    }
    const W = 560, H = 220, pl = 34, pb = 34, pt = 10, pr = 8;
    const iw = W - pl - pr, ih = H - pt - pb, n = data.length, slot = iw / n, bw = Math.max(4, Math.min(42, slot - 2));
    const svg = s("svg", { viewBox: `0 0 ${W} ${H}`, class: "chart", role: "img", "aria-label": `${title}: ${data.map((d) => `${d.label} ${fmt(d.value)}`).join(", ")}` });
    const ticks = 4;
    for (let i = 0; i <= ticks; i++) {
      const v = (max * i) / ticks, y = pt + ih - (ih * i) / ticks;
      svg.append(s("line", { x1: pl, x2: W - pr, y1: y, y2: y, class: i === 0 ? "axis" : "grid-line" }));
      const t = s("text", { x: pl - 6, y: y + 4, "text-anchor": "end" }); t.textContent = Number.isInteger(max) ? Math.round(v) : v.toFixed(1); svg.append(t);
    }
    const every = Math.ceil(n / 10);
    data.forEach((d, i) => {
      const x = pl + i * slot + (slot - bw) / 2, bh = (ih * d.value) / max, y = pt + ih - bh;
      // Rounded data end (top), square at baseline.
      const r = Math.min(4, bw / 2, bh);
      const path = s("path", { d: `M${x},${pt + ih} V${y + r} Q${x},${y} ${x + r},${y} H${x + bw - r} Q${x + bw},${y} ${x + bw},${y + r} V${pt + ih} Z`, fill: "var(--chart-1)" });
      const hit = s("rect", { x: pl + i * slot, y: pt, width: slot, height: ih, fill: "transparent" });
      hit.addEventListener("mousemove", (e) => showTip(e, `${d.label}: ${fmt(d.value)}${d.note ? ` · ${d.note}` : ""}`)); hit.addEventListener("mouseleave", hideTip);
      svg.append(path);
      if (i % every === 0) { const t = s("text", { x: x + bw / 2, y: H - pb + 16, "text-anchor": "middle" }); t.textContent = d.label; svg.append(t); }
      svg.append(hit);
    });
    body.append(svg);
  }
  render();
  return card;
}
