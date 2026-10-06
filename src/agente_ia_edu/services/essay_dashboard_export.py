"""XLSX/PDF export of the teacher essay dashboard (see
essay_teacher_dashboard.py for the aggregation the export renders). Mirrors
list_export.py's shape - pure bytes in, `Response(content=..., media_type=...)`
out at the route layer. XLSX via openpyxl (this repo has no existing
spreadsheet export to follow, confirmed: grep for csv.writer/openpyxl/
StreamingResponse across the codebase found none before this). PDF via
PyMuPDF, same library essay_answer_sheet.py already uses to draw PDFs with
low-level primitives (draw_rect/draw_line/insert_text) - ReportLab is
deliberately kept out of the core (user decision), so a second PDF renderer
never enters the dependency tree just for this export.
"""

from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Font

from .essay_teacher_dashboard import EssayDashboardResponse

_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
_PDF_MEDIA_TYPE = "application/pdf"

_REPORT_TYPES = ("grades_total", "grades_per_competency", "submission_list")

_REPORT_TITLES = {
    "grades_total": "Nota total",
    "grades_per_competency": "Nota por competência",
    "submission_list": "Lista de entrega",
}


def xlsx_media_type() -> str:
    return _MEDIA_TYPE


def pdf_media_type() -> str:
    return _PDF_MEDIA_TYPE


def pdf_export_available() -> bool:
    """Same optional-dependency guard essay_answer_sheet.py already uses
    (pymupdf is an optional extra, 'recovery' in pyproject.toml) - the route
    checks this before calling build_essay_dashboard_pdf, same 503 pattern
    as GET .../answer-sheet.pdf."""
    try:
        import pymupdf  # noqa: F401

        return True
    except Exception:
        return False


def _report_rows(dashboard: EssayDashboardResponse, *, report_type: str) -> tuple[list[str], list[list[str]]]:
    """Header + rows shared by both the XLSX and the PDF export, so the two
    formats never drift apart on what a report_type actually contains."""
    if report_type == "submission_list":
        header = ["Aluno", "Entregou"]
        rows = [[row.student_name, "Sim" if row.submitted else "Não"] for row in dashboard.students]
    elif report_type == "grades_total":
        header = ["Aluno", "Entregou", "Nota total"]
        rows = [
            [row.student_name, "Sim" if row.submitted else "Não",
             str(row.total_score) if row.total_score is not None else "-"]
            for row in dashboard.students
        ]
    else:  # grades_per_competency
        header = ["Aluno", "Entregou", "C1", "C2", "C3", "C4", "C5", "Nota total"]
        rows = []
        for row in dashboard.students:
            per_competency = row.per_competency or {}
            rows.append([
                row.student_name, "Sim" if row.submitted else "Não",
                *(str(per_competency.get(c)) if per_competency.get(c) is not None else "-"
                  for c in ("C1", "C2", "C3", "C4", "C5")),
                str(row.total_score) if row.total_score is not None else "-",
            ])
    return header, rows


def build_essay_dashboard_pdf(dashboard: EssayDashboardResponse, *, report_type: str) -> bytes:
    if report_type not in _REPORT_TYPES:
        raise ValueError(
            f"Unknown report_type: {report_type!r} - must be one of {_REPORT_TYPES}"
        )
    import pymupdf

    header, rows = _report_rows(dashboard, report_type=report_type)

    margin = 36.0
    row_height = 18.0
    title_area = 54.0
    doc = pymupdf.open()
    page = None
    y = 0.0
    col_width = 0.0
    page_width, page_height = 841.89, 595.44  # A4 paisagem - cabe ate 8 colunas sem espremer

    def new_page() -> float:
        nonlocal page, col_width
        page = doc.new_page(width=page_width, height=page_height)
        page.insert_textbox(
            pymupdf.Rect(margin, margin, page_width - margin, margin + 24),
            f"{_REPORT_TITLES[report_type]} - {dashboard.essay_prompt_title}",
            fontsize=13, fontname="hebo",
        )
        header_y = margin + title_area
        col_width = (page_width - 2 * margin) / len(header)
        for index, label in enumerate(header):
            page.insert_text(
                (margin + index * col_width + 2, header_y - 4), label,
                fontsize=9, fontname="hebo",
            )
        page.draw_line(
            pymupdf.Point(margin, header_y), pymupdf.Point(page_width - margin, header_y),
            color=(0.6, 0.6, 0.6), width=0.6,
        )
        return header_y + row_height

    y = new_page()
    for row in rows:
        if y > page_height - margin:
            y = new_page()
        for index, value in enumerate(row):
            page.insert_text((margin + index * col_width + 2, y - 4), value, fontsize=9)
        y += row_height

    buf = io.BytesIO()
    doc.save(buf)
    doc.close()
    return buf.getvalue()


def build_essay_dashboard_xlsx(dashboard: EssayDashboardResponse, *, report_type: str) -> bytes:
    if report_type not in _REPORT_TYPES:
        raise ValueError(
            f"Unknown report_type: {report_type!r} - must be one of {_REPORT_TYPES}"
        )

    workbook = Workbook()
    sheet = workbook.active
    bold = Font(bold=True)

    if report_type == "submission_list":
        sheet.title = "Entregas"
        header = ["Aluno", "Entregou"]
        sheet.append(header)
        for row in dashboard.students:
            sheet.append([row.student_name, "Sim" if row.submitted else "Não"])
    elif report_type == "grades_total":
        sheet.title = "Notas"
        header = ["Aluno", "Entregou", "Nota total"]
        sheet.append(header)
        for row in dashboard.students:
            sheet.append([
                row.student_name, "Sim" if row.submitted else "Não", row.total_score,
            ])
    else:  # grades_per_competency
        sheet.title = "Notas por competência"
        header = ["Aluno", "Entregou", "C1", "C2", "C3", "C4", "C5", "Nota total"]
        sheet.append(header)
        for row in dashboard.students:
            per_competency = row.per_competency or {}
            sheet.append([
                row.student_name, "Sim" if row.submitted else "Não",
                per_competency.get("C1"), per_competency.get("C2"), per_competency.get("C3"),
                per_competency.get("C4"), per_competency.get("C5"), row.total_score,
            ])

    for cell in sheet[1]:
        cell.font = bold
    for column_cells in sheet.columns:
        width = max(len(str(cell.value)) if cell.value is not None else 0 for cell in column_cells)
        sheet.column_dimensions[column_cells[0].column_letter].width = min(max(width + 2, 10), 40)

    buf = io.BytesIO()
    workbook.save(buf)
    return buf.getvalue()


__all__ = [
    "build_essay_dashboard_xlsx", "xlsx_media_type",
    "build_essay_dashboard_pdf", "pdf_media_type", "pdf_export_available",
]
