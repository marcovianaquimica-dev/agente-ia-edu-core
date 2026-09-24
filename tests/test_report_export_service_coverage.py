"""Service-level coverage for services/report_export.py.

ReportExportService.export_classroom_report is called from the single HTTP
route GET /classrooms/{classroom_id}/export (teacher_portal.py) and already
has partial HTTP-level coverage in test_teacher_portal.py
(test_15_report_export_service: pdf/xlsx happy paths). This file closes the
remaining gaps directly at the service level:
  - the invalid-format ValueError branch (never hit by the route, which only
    ever passes "pdf" or the query's raw `format` string - but is real,
    reachable code any caller could trigger)
  - export_student_report, which currently has ZERO callers anywhere in
    src/ (grepped) and ZERO test coverage - it is nonetheless a public
    service method, so it is tested directly here the same way
    export_classroom_report's un-routed edge cases are.

Two genuine bugs were found and fixed here (see comments at each test):
  1. export_student_report crashed with AttributeError when student_data
     carried an explicit `"student_id": None` (as opposed to a missing key) -
     the same "key present with None value" trap that export_classroom_report
     already guards against for classroom_id (see the comment on
     ReportExportService.export_classroom_report).
  2. export_format="excel" (an explicitly-accepted alias per the validation
     `if fmt not in ("pdf", "xlsx", "excel")`) was never normalized to
     "xlsx", so both methods produced a nonsensical ".excel" file extension
     and export_format="excel" in the payload instead of "xlsx".
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.report_export import ReportExportService


class ExportClassroomReportTests(unittest.TestCase):
    def test_invalid_format_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            ReportExportService.export_classroom_report({"classroom_id": "T1"}, export_format="csv")
        self.assertIn("Unsupported export format", str(ctx.exception))

    def test_excel_alias_normalizes_to_xlsx_extension_and_field(self):
        # Bug: fmt="excel" is explicitly accepted by the validation
        # (`if fmt not in ("pdf", "xlsx", "excel")`) but was never mapped to
        # "xlsx" afterwards, so filename/export_format leaked the literal
        # "excel" instead of the real xlsx extension/mimetype family.
        result = ReportExportService.export_classroom_report({"classroom_id": "T1"}, export_format="excel")
        self.assertEqual(result["export_format"], "xlsx")
        self.assertTrue(result["filename"].endswith(".xlsx"), result["filename"])
        self.assertEqual(
            result["content_type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_classroom_id_explicit_none_falls_back_to_whole_school_title(self):
        # Documents the already-fixed behavior described in the source
        # comment: `.get("classroom_id") or None` (not `.get(key, default)`)
        # so a caller passing the key with value None still gets the
        # whole-school title/filename rather than the literal string "None".
        result = ReportExportService.export_classroom_report(
            {"classroom_id": None, "summary": {}}, export_format="pdf"
        )
        self.assertEqual(result["title"], "Relatório Pedagógico Geral da Escola")
        self.assertIn("Relatorio_Geral_", result["filename"])
        self.assertNotIn("None", result["filename"])

    def test_classroom_report_defaults_and_passthrough_fields(self):
        classroom_data = {
            "classroom_id": "3A",
            "summary": {"student_count": 10},
            "mastery_distribution": {"HIGH": 5},
            "strengths": [{"content_name": "X"}],
            "improvement_areas": [{"content_name": "Y"}],
            "recent_contents_taught": [{"content_name": "Z"}],
            "action_plan": [{"action": "revisar"}],
            "students": [{"id": "s1"}],
        }
        result = ReportExportService.export_classroom_report(classroom_data)
        self.assertEqual(result["export_format"], "pdf")
        self.assertEqual(result["content_type"], "application/pdf")
        self.assertEqual(result["title"], "Relatório Pedagógico da Turma 3A")
        self.assertEqual(result["summary"], {"student_count": 10})
        self.assertEqual(result["mastery_distribution"], {"HIGH": 5})
        self.assertEqual(result["strengths"], [{"content_name": "X"}])
        self.assertEqual(result["improvement_areas"], [{"content_name": "Y"}])
        self.assertEqual(result["recent_contents_taught"], [{"content_name": "Z"}])
        self.assertEqual(result["action_plan"], [{"action": "revisar"}])
        self.assertEqual(result["students_roster"], [{"id": "s1"}])

    def test_classroom_report_missing_optional_keys_default_to_empty(self):
        result = ReportExportService.export_classroom_report({"classroom_id": "3A"})
        self.assertEqual(result["summary"], {})
        self.assertEqual(result["mastery_distribution"], {})
        self.assertEqual(result["strengths"], [])
        self.assertEqual(result["improvement_areas"], [])
        self.assertEqual(result["recent_contents_taught"], [])
        self.assertEqual(result["action_plan"], [])
        self.assertEqual(result["students_roster"], [])


class ExportStudentReportTests(unittest.TestCase):
    def test_invalid_format_raises_value_error(self):
        with self.assertRaises(ValueError) as ctx:
            ReportExportService.export_student_report({"student_id": "aluno-1"}, export_format="doc")
        self.assertIn("Unsupported export format", str(ctx.exception))

    def test_pdf_happy_path_with_full_payload(self):
        student_data = {
            "student_id": "aluno-42",
            "accuracy_percentage": 87.5,
            "total_questions_answered": 120,
            "content_masteries": [{"content": "Estequiometria", "level": "HIGH"}],
            "priority_contents": [{"content": "Termoquímica"}],
            "current_recommendations": [{"action": "revisar"}],
        }
        result = ReportExportService.export_student_report(student_data, export_format="PDF")
        self.assertEqual(result["export_format"], "pdf")
        self.assertEqual(result["content_type"], "application/pdf")
        self.assertTrue(result["filename"].startswith("Relatorio_Aluno_aluno-42_"))
        self.assertTrue(result["filename"].endswith(".pdf"))
        self.assertIn("aluno-42", result["title"])
        self.assertEqual(result["accuracy_percentage"], 87.5)
        self.assertEqual(result["total_questions_answered"], 120)
        self.assertEqual(result["content_masteries"], [{"content": "Estequiometria", "level": "HIGH"}])
        self.assertEqual(result["priority_contents"], [{"content": "Termoquímica"}])
        self.assertEqual(result["current_recommendations"], [{"action": "revisar"}])

    def test_excel_alias_normalizes_to_xlsx_extension_and_field(self):
        result = ReportExportService.export_student_report({"student_id": "aluno-7"}, export_format="excel")
        self.assertEqual(result["export_format"], "xlsx")
        self.assertTrue(result["filename"].endswith(".xlsx"), result["filename"])
        self.assertEqual(
            result["content_type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )

    def test_xlsx_format_and_missing_optional_keys_default(self):
        result = ReportExportService.export_student_report({"student_id": "aluno-7"}, export_format="xlsx")
        self.assertEqual(result["export_format"], "xlsx")
        self.assertEqual(
            result["content_type"],
            "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
        )
        self.assertEqual(result["accuracy_percentage"], 0.0)
        self.assertEqual(result["total_questions_answered"], 0)
        self.assertEqual(result["content_masteries"], [])
        self.assertEqual(result["priority_contents"], [])
        self.assertEqual(result["current_recommendations"], [])

    def test_student_id_colon_is_sanitized_in_filename(self):
        # student_id values are frequently "school:student" composite keys
        # elsewhere in this codebase (e.g. "user:prof_mendes" identities) -
        # ':' is not filesystem/URL-safe in a filename, hence the explicit
        # sanitization.
        result = ReportExportService.export_student_report({"student_id": "school:42"})
        self.assertIn("Relatorio_Aluno_school_42_", result["filename"])
        self.assertNotIn(":", result["filename"])

    def test_missing_student_id_key_defaults_to_aluno(self):
        result = ReportExportService.export_student_report({})
        self.assertIn("Relatorio_Aluno_ALUNO_", result["filename"])

    def test_explicit_none_student_id_does_not_crash(self):
        # Bug: student_data.get("student_id", "ALUNO") only falls back for a
        # MISSING key - a caller passing {"student_id": None} (e.g. a
        # not-yet-resolved student identity) got back the literal None,
        # and `None.replace(":", "_")` raised an unhandled AttributeError.
        # Fixed to mirror export_classroom_report's `.get(key) or default`
        # pattern.
        result = ReportExportService.export_student_report({"student_id": None})
        self.assertIn("Relatorio_Aluno_ALUNO_", result["filename"])
        self.assertIn("ALUNO", result["title"])


if __name__ == "__main__":
    unittest.main()
