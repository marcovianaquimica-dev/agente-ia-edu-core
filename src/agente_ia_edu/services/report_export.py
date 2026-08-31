"""
Report Export Service contract and structured exporter for Teacher and Coordination Portal (Phase 12B.2).
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any


class ReportExportService:
    """Service providing structured export payload data for PDF and XLSX exports."""

    @staticmethod
    def export_classroom_report(
        classroom_data: dict[str, Any],
        export_format: str = "pdf",
    ) -> dict[str, Any]:
        """Builds a structured export payload for classroom dashboard reports."""
        fmt = export_format.lower()
        if fmt not in ("pdf", "xlsx", "excel"):
            raise ValueError(f"Unsupported export format: {export_format}. Supported: pdf, xlsx.")

        classroom_id = classroom_data.get("classroom_id", "TURMA")
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")

        filename = f"Relatorio_{classroom_id}_{timestamp_str}.{fmt}"
        content_type = "application/pdf" if fmt == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

        return {
            "export_format": fmt,
            "filename": filename,
            "content_type": content_type,
            "title": f"Relatório Pedagógico da Turma {classroom_id}",
            "generated_at": now.isoformat(),
            "summary": classroom_data.get("summary", {}),
            "mastery_distribution": classroom_data.get("mastery_distribution", {}),
            "strengths": classroom_data.get("strengths", []),
            "improvement_areas": classroom_data.get("improvement_areas", []),
            "recent_contents_taught": classroom_data.get("recent_contents_taught", []),
            "action_plan": classroom_data.get("action_plan", []),
            "students_roster": classroom_data.get("students", []),
        }

    @staticmethod
    def export_student_report(
        student_data: dict[str, Any],
        export_format: str = "pdf",
    ) -> dict[str, Any]:
        """Builds a structured export payload for individual student performance reports."""
        fmt = export_format.lower()
        if fmt not in ("pdf", "xlsx", "excel"):
            raise ValueError(f"Unsupported export format: {export_format}. Supported: pdf, xlsx.")

        student_id = student_data.get("student_id", "ALUNO")
        clean_sid = student_id.replace(":", "_")
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")

        filename = f"Relatorio_Aluno_{clean_sid}_{timestamp_str}.{fmt}"
        content_type = "application/pdf" if fmt == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

        return {
            "export_format": fmt,
            "filename": filename,
            "content_type": content_type,
            "title": f"Relatório Desempenho Individual — {student_id}",
            "generated_at": now.isoformat(),
            "accuracy_percentage": student_data.get("accuracy_percentage", 0.0),
            "total_questions_answered": student_data.get("total_questions_answered", 0),
            "content_masteries": student_data.get("content_masteries", []),
            "priority_contents": student_data.get("priority_contents", []),
            "current_recommendations": student_data.get("current_recommendations", []),
        }
