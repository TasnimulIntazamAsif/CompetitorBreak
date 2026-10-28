"""Export the keyword index (keyword -> URLs + frequency) to CSV / XLSX / DOCX /
PDF. Each row of the index is {keyword, total, pages, urls:[{url, count}]}; the
files flatten it to (Keyword, URL, Frequency), keyword repeated on each URL row
so the output is usable as data."""

from __future__ import annotations

import csv
import io


def _flat(rows: list[dict]):
    for r in rows:
        for u in r["urls"]:
            yield r["keyword"], u["url"], u["count"]


def index_to_csv(domain: str, rows: list[dict]) -> bytes:
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Competitor", domain])
    w.writerow(["Keyword", "URL", "Frequency"])
    for kw, url, count in _flat(rows):
        w.writerow([kw, url, count])
    # utf-8-sig so Excel opens non-ASCII keywords correctly.
    return buf.getvalue().encode("utf-8-sig")


def index_to_xlsx(domain: str, rows: list[dict]) -> bytes:
    from openpyxl import Workbook

    wb = Workbook()
    ws = wb.active
    ws.title = "Keywords"
    ws.append(["Competitor", domain])
    ws.append([])
    ws.append(["Keyword", "URL", "Frequency"])
    for kw, url, count in _flat(rows):
        ws.append([kw, url, count])
    ws.column_dimensions["A"].width = 34
    ws.column_dimensions["B"].width = 70
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
    for kw, url, count in _flat(rows):
        cells = table.add_row().cells
        cells[0].text, cells[1].text, cells[2].text = kw, url, str(count)
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
    for kw, url, count in _flat(rows):
        pdf.cell(wk, 6, _latin(kw[:42]), border=1)
        pdf.cell(wu, 6, _latin(url[:118]), border=1)
        pdf.cell(wf, 6, str(count), border=1, ln=1)

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
