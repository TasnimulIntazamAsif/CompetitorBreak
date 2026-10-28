"""Export the keyword index (keyword -> URLs + frequency) to CSV / XLSX / DOCX /
PDF. Each row of the index is {keyword, total, pages, urls:[{url, count}]}; the
files flatten it to (Keyword, URL, Frequency), keyword repeated on each URL row
so the output is usable as data."""

from __future__ import annotations

import csv
import io


def _kw_label(r: dict) -> str:
    """Keyword shown once per group, with its total — like the on-screen view."""
    total = r.get("total")
    return f"{r['keyword']} · {total}×" if total is not None else r["keyword"]


def index_to_csv(domain: str, rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Competitor", domain])
    w.writerow(["Keyword", "URL", "Frequency"])
    for r in rows:
        for i, u in enumerate(r["urls"]):
            # Keyword only on the first row of its group (blank after), grouped.
            w.writerow([_kw_label(r) if i == 0 else "", u["url"], u["count"]])
    # utf-8-sig so Excel opens non-ASCII keywords correctly.
    return buf.getvalue().encode("utf-8-sig")


def index_to_xlsx(domain: str, rows: list[dict]) -> bytes:
    from openpyxl import Workbook
    from openpyxl.styles import Alignment, Font

    wb = Workbook()
    ws = wb.active
    ws.title = "Keywords"
    ws.append(["Competitor", domain])
    ws.append([])
    ws.append(["Keyword", "URL", "Frequency"])
    for c in ws[3]:
        c.font = Font(bold=True)

    row = 4
    for r in rows:
        urls = r["urls"] or []
        if not urls:
            continue
        start = row
        for i, u in enumerate(urls):
            ws.cell(row=row, column=1, value=_kw_label(r) if i == 0 else None)
            ws.cell(row=row, column=2, value=u["url"])
            ws.cell(row=row, column=3, value=u["count"])
            row += 1
        # Merge the keyword cell down its group's rows (the on-screen "rowspan").
        if len(urls) > 1:
            ws.merge_cells(start_row=start, start_column=1, end_row=row - 1, end_column=1)
        ws.cell(row=start, column=1).alignment = Alignment(vertical="top", wrap_text=True)

    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 72
    ws.column_dimensions["C"].width = 12
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def index_to_docx(domain: str, rows: list[dict]) -> bytes:
    from docx import Document

    doc = Document()
    doc.add_heading(f"Competitor keyword index: {domain}", level=1)
    table = doc.add_table(rows=1, cols=3)
    table.style = "Table Grid"
    hdr = table.rows[0].cells
    hdr[0].text, hdr[1].text, hdr[2].text = "Keyword", "URL", "Frequency"
    for r in rows:
        urls = r["urls"] or []
        anchor = None
        for i, u in enumerate(urls):
            cells = table.add_row().cells
            cells[1].text, cells[2].text = u["url"], str(u["count"])
            if i == 0:
                cells[0].text = _kw_label(r)
                anchor = cells[0]
            elif anchor is not None:
                anchor = anchor.merge(cells[0])  # vertical merge = grouped keyword
    buf = io.BytesIO()
    doc.save(buf)
    return buf.getvalue()


def _latin(s: str) -> str:
    """fpdf core fonts are latin-1 only; replace anything outside it."""
    return s.encode("latin-1", "replace").decode("latin-1")


def index_to_pdf(domain: str, rows: list[dict]) -> bytes:
    from fpdf import FPDF

    pdf = FPDF(orientation="L", unit="mm", format="A4")
    pdf.add_page()
    pdf.set_font("Helvetica", "B", 14)
    pdf.cell(0, 9, _latin(f"Competitor keyword index: {domain}"), ln=1)
    pdf.ln(1)

    wk, wu, wf = 70, 185, 22
    pdf.set_font("Helvetica", "B", 9)
    pdf.cell(wk, 7, "Keyword", border=1)
    pdf.cell(wu, 7, "URL", border=1)
    pdf.cell(wf, 7, "Frequency", border=1, ln=1)

    pdf.set_font("Helvetica", size=8)
    for r in rows:
        urls = r["urls"] or []
        label = _latin(_kw_label(r))
        for i, u in enumerate(urls):
            # Keyword only on the first row of its group (grouped like the UI).
            pdf.cell(wk, 6, label[:42] if i == 0 else "", border=1)
            pdf.cell(wu, 6, _latin(u["url"][:118]), border=1)
            pdf.cell(wf, 6, str(u["count"]), border=1, ln=1)

    out = pdf.output()  # fpdf2 returns a bytearray
    return bytes(out)


GENERATORS = {
    "csv": index_to_csv,
    "xlsx": index_to_xlsx,
    "docx": index_to_docx,
    "pdf": index_to_pdf,
}

MEDIA_TYPES = {
    "csv": "text/csv",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "pdf": "application/pdf",
}
