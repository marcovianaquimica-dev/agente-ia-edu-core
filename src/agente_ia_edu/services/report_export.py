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
        if fmt == "excel":
            fmt = "xlsx"

        # `.get(key, default)` only falls back for a MISSING key - a caller
        # reporting "no specific classroom" (whole-school scope) passes the
        # key with value None, which `.get` returns as-is, leaking the
        # literal Python "None" into the title/filename.
        classroom_id = classroom_data.get("classroom_id") or None
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")

        filename = f"Relatorio_{classroom_id or 'Geral'}_{timestamp_str}.{fmt}"
        content_type = "application/pdf" if fmt == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"
        title = f"Relatório Pedagógico da Turma {classroom_id}" if classroom_id else "Relatório Pedagógico Geral da Escola"

        return {
            "export_format": fmt,
            "filename": filename,
            "content_type": content_type,
            "title": title,
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
        if fmt == "excel":
            fmt = "xlsx"

        # Same "key present with value None" trap as export_classroom_report's
        # classroom_id: `.get(key, default)` only falls back for a MISSING
        # key, so `.get("student_id") or "ALUNO"` is required to avoid
        # `None.replace(...)` blowing up below.
        student_id = student_data.get("student_id") or "ALUNO"
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

    @staticmethod
    def export_candidate_report(
        candidate_data: dict[str, Any],
        export_format: str = "pdf",
    ) -> dict[str, Any]:
        """Builds a structured export payload for a Reception Portal candidate
        ("ficha do candidato" / matrícula).

        `candidate_data` is expected to be the same shape as
        `ReceptionCandidateResponse` (routes/reception.py), plus a
        `status_label` key holding the Portuguese translation of `status`
        (the route owns that translation, matching reception.js's own
        `statusLabel` map - this service stays agnostic of reception's
        status vocabulary). Only fields that are actually present are
        rendered - no field is invented to "look complete".
        """
        fmt = export_format.lower()
        if fmt not in ("pdf", "xlsx", "excel"):
            raise ValueError(f"Unsupported export format: {export_format}. Supported: pdf, xlsx.")
        if fmt == "excel":
            fmt = "xlsx"

        candidate_id = candidate_data.get("id") or "CANDIDATO"
        now = datetime.now(timezone.utc)
        timestamp_str = now.strftime("%Y%m%d_%H%M%S")
        filename = f"Ficha_Candidato_{candidate_id}_{timestamp_str}.{fmt}"
        content_type = "application/pdf" if fmt == "pdf" else "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet"

        summary = {
            "full_name": candidate_data.get("full_name"),
            "preferred_name": candidate_data.get("preferred_name"),
            "birth_date": candidate_data.get("birth_date"),
            "guardian_name": candidate_data.get("guardian_name"),
            "phone": candidate_data.get("phone"),
            "email": candidate_data.get("email"),
            "academic_year": candidate_data.get("academic_year"),
            "unit_id": candidate_data.get("unit_id"),
            "segment_id": candidate_data.get("segment_id"),
            "grade_level": candidate_data.get("grade_level"),
            "classroom_id": candidate_data.get("classroom_id"),
            "status_label": candidate_data.get("status_label"),
            "created_at": candidate_data.get("created_at"),
            "released_at": candidate_data.get("released_at"),
            "diagnostic_started_at": candidate_data.get("diagnostic_started_at"),
            "diagnostic_completed_at": candidate_data.get("diagnostic_completed_at"),
            "questions_answered": candidate_data.get("questions_answered"),
        }

        result = candidate_data.get("result")
        if isinstance(result, dict):
            summary["overall_confidence_percent"] = round((result.get("overall_confidence") or 0.0) * 100, 1)
            summary["evidence_count"] = result.get("evidence_count")
            summary["total_questions_asked"] = result.get("total_questions_asked")
            summary["total_correct"] = result.get("total_correct")

        # Drop keys with no real value instead of rendering "None"/"-" rows
        # for data this candidate simply doesn't have yet (e.g. no diagnostic).
        summary = {key: value for key, value in summary.items() if value is not None}

        payload: dict[str, Any] = {
            "export_format": fmt,
            "filename": filename,
            "content_type": content_type,
            "title": f"Ficha do Candidato — {candidate_data.get('full_name') or candidate_id}",
            "generated_at": now.isoformat(),
            "summary": summary,
        }

        if isinstance(result, dict):
            payload["mastery_map"] = [
                {
                    "content_name": item.get("content_name"),
                    "estimated_mastery": item.get("estimated_mastery"),
                    "recommended_difficulty": item.get("recommended_difficulty"),
                }
                for item in (result.get("mastery_map") or [])
            ]
            payload["probable_gaps"] = [
                {
                    "content_name": item.get("content_name"),
                    "estimated_mastery": item.get("estimated_mastery"),
                    "prerequisite": (item.get("possible_prerequisite_gap") or {}).get("content_name"),
                }
                for item in (result.get("probable_gaps") or [])
            ]

        return payload
