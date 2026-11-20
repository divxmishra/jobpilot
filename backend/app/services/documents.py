"""HTML/CSS resume template engine -> PDF (xhtml2pdf) and editable DOCX (python-docx).

The template is deliberately ATS-conscious: single column, standard headings,
selectable text, consistent dates, no images, icons, text boxes or tables.
"""
from __future__ import annotations

import html
import io

MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]

CSS = """
@page { size: A4; margin: 1.6cm 1.7cm 1.6cm 1.7cm; }
body { font-family: Helvetica; font-size: 10pt; color: #111111; line-height: 1.35; }
h1 { font-size: 18pt; margin: 0 0 2pt 0; padding: 0; }
.headline { font-size: 11pt; color: #333333; margin: 0 0 3pt 0; }
.contact { font-size: 9pt; color: #333333; margin: 0 0 8pt 0; }
h2 { font-size: 10.5pt; text-transform: uppercase; letter-spacing: 1pt; border-bottom: 0.8pt solid #444444;
     margin: 10pt 0 4pt 0; padding: 0 0 2pt 0; }
.item { margin: 0 0 6pt 0; }
.item-head { font-weight: bold; margin: 0; }
.item-sub { color: #333333; margin: 0 0 2pt 0; font-size: 9.5pt; }
ul { margin: 0 0 0 12pt; padding: 0; }
li { margin: 0 0 1.5pt 0; }
p { margin: 0 0 3pt 0; }
"""


def fmt_date(d: str | None) -> str:
    if not d:
        return ""
    if d == "present":
        return "Present"
    parts = d.split("-")
    if len(parts) == 2:
        return f"{MONTHS[int(parts[1]) - 1]} {parts[0]}"
    return parts[0]


def date_range(s: str | None, e: str | None) -> str:
    a, b = fmt_date(s), fmt_date(e)
    return f"{a} – {b}" if a and b else a or b


def _e(s) -> str:
    return html.escape(str(s or ""))


def section_titles(c: dict) -> list[tuple[str, str]]:
    order = c.get("section_order") or ["summary", "skills", "experience", "projects", "education", "certifications", "achievements"]
    names = {"summary": "Summary", "skills": "Skills", "experience": "Experience", "projects": "Projects",
             "education": "Education", "certifications": "Certifications", "achievements": "Achievements"}
    return [(k, names[k]) for k in order if c.get(k)]


def render_html(c: dict) -> str:
    b = c.get("basics", {})
    contact = " | ".join(_e(x) for x in [b.get("email"), b.get("phone"), b.get("location")] + [l.get("url") for l in b.get("links", [])] if x)
    out = [f"<html><head><meta charset='utf-8'><style>{CSS}</style></head><body>",
           f"<h1>{_e(b.get('name'))}</h1>"]
    if b.get("headline"):
        out.append(f"<div class='headline'>{_e(b['headline'])}</div>")
    out.append(f"<div class='contact'>{contact}</div>")
    for key, title in section_titles(c):
        out.append(f"<h2>{title}</h2>")
        if key == "summary":
            out.append(f"<p>{_e(c['summary'])}</p>")
        elif key == "skills":
            out.append(f"<p>{_e(', '.join(c['skills']))}</p>")
        elif key == "experience":
            for e in c["experience"]:
                head = " — ".join(x for x in [_e(e.get("title")), _e(e.get("company"))] if x)
                sub = " | ".join(x for x in [date_range(e.get("start"), e.get("end")), _e(e.get("location"))] if x)
                out.append(f"<div class='item'><p class='item-head'>{head}</p><p class='item-sub'>{sub}</p>")
                if e.get("bullets"):
                    out.append("<ul>" + "".join(f"<li>{_e(x['text'])}</li>" for x in e["bullets"]) + "</ul>")
                out.append("</div>")
        elif key == "projects":
            for p in c["projects"]:
                link = f" | {_e(p['link'])}" if p.get("link") else ""
                out.append(f"<div class='item'><p class='item-head'>{_e(p.get('name'))}{link}</p>")
                if p.get("bullets"):
                    out.append("<ul>" + "".join(f"<li>{_e(x['text'])}</li>" for x in p["bullets"]) + "</ul>")
                out.append("</div>")
        elif key == "education":
            for e in c["education"]:
                deg = _e(e.get("degree")) + (f" in {_e(e['field'])}" if e.get("field") else "")
                sub = " | ".join(x for x in [_e(e.get("institution")), date_range(e.get("start"), e.get("end")),
                                             (f"Grade: {_e(e['grade'])}" if e.get("grade") else "")] if x)
                out.append(f"<div class='item'><p class='item-head'>{deg}</p><p class='item-sub'>{sub}</p></div>")
        elif key == "certifications":
            out.append("<ul>" + "".join(f"<li>{_e(x['name'])}</li>" for x in c["certifications"]) + "</ul>")
        elif key == "achievements":
            out.append("<ul>" + "".join(f"<li>{_e(x['text'])}</li>" for x in c["achievements"]) + "</ul>")
    out.append("</body></html>")
    return "".join(out)


def render_pdf(c: dict) -> tuple[bytes, int]:
    from pypdf import PdfReader
    from xhtml2pdf import pisa
    buf = io.BytesIO()
    res = pisa.CreatePDF(render_html(c), dest=buf, encoding="utf-8")
    if res.err:
        raise RuntimeError("PDF rendering failed")
    data = buf.getvalue()
    pages = len(PdfReader(io.BytesIO(data)).pages)
    return data, pages


def render_docx(c: dict) -> bytes:
    import docx
    from docx.shared import Pt
    d = docx.Document()
    st = d.styles["Normal"]
    st.font.name = "Calibri"
    st.font.size = Pt(10.5)
    b = c.get("basics", {})
    d.add_heading(b.get("name") or "", level=0)
    if b.get("headline"):
        d.add_paragraph(b["headline"])
    contact = " | ".join(x for x in [b.get("email"), b.get("phone"), b.get("location")] + [l.get("url") for l in b.get("links", [])] if x)
    d.add_paragraph(contact)
    for key, title in section_titles(c):
        d.add_heading(title, level=1)
        if key == "summary":
            d.add_paragraph(c["summary"])
        elif key == "skills":
            d.add_paragraph(", ".join(c["skills"]))
        elif key == "experience":
            for e in c["experience"]:
                p = d.add_paragraph()
                p.add_run(" — ".join(x for x in [e.get("title"), e.get("company")] if x)).bold = True
                d.add_paragraph(" | ".join(x for x in [date_range(e.get("start"), e.get("end")), e.get("location")] if x))
                for x in e.get("bullets", []):
                    d.add_paragraph(x["text"], style="List Bullet")
        elif key == "projects":
            for pj in c["projects"]:
                p = d.add_paragraph()
                p.add_run(pj.get("name") or "").bold = True
                if pj.get("link"):
                    p.add_run(f" | {pj['link']}")
                for x in pj.get("bullets", []):
                    d.add_paragraph(x["text"], style="List Bullet")
        elif key == "education":
            for e in c["education"]:
                p = d.add_paragraph()
                p.add_run((e.get("degree") or "") + (f" in {e['field']}" if e.get("field") else "")).bold = True
                d.add_paragraph(" | ".join(x for x in [e.get("institution"), date_range(e.get("start"), e.get("end")),
                                                       (f"Grade: {e['grade']}" if e.get("grade") else "")] if x))
        elif key == "certifications":
            for x in c["certifications"]:
                d.add_paragraph(x["name"], style="List Bullet")
        elif key == "achievements":
            for x in c["achievements"]:
                d.add_paragraph(x["text"], style="List Bullet")
    buf = io.BytesIO()
    d.save(buf)
    return buf.getvalue()


def pdf_text(data: bytes) -> str:
    from pypdf import PdfReader
    return "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(data)).pages)


def docx_text(data: bytes) -> str:
    import docx
    return "\n".join(p.text for p in docx.Document(io.BytesIO(data)).paragraphs)


def render_page_png(pdf_bytes: bytes, page: int, scale: float = 1.6) -> tuple[bytes, int]:
    import pypdfium2 as pdfium
    doc = pdfium.PdfDocument(pdf_bytes)
    n = len(doc)
    if page < 1 or page > n:
        raise IndexError("Page out of range")
    img = doc[page - 1].render(scale=scale).to_pil()
    buf = io.BytesIO()
    img.save(buf, format="PNG", optimize=True)
    doc.close()
    return buf.getvalue(), n
