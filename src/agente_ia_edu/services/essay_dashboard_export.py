"""XLSX export of the teacher essay dashboard (see essay_teacher_dashboard.py
for the aggregation the export renders). Mirrors list_export.py's shape -
pure bytes in, `Response(content=..., media_type=...)` out at the route
layer - but via openpyxl instead of python-docx/PyMuPDF, since this repo has
no existing spreadsheet export to follow (confirmed: grep for csv.writer/
openpyxl/StreamingResponse across the codebase found none before this).
"""

from __future__ import annotations

import io

from openpyxl import Workbook
from openpyxl.styles import Font

from .essay_teacher_dashboard import EssayDashboardResponse

_MEDIA_TYPE = "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

_REPORT_TYPES = ("grades_total", "grades_per_competency", "submission_list")


def xlsx_media_type() -> str:
    return _MEDIA_TYPE


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


__all__ = ["build_essay_dashboard_xlsx", "xlsx_media_type"]
