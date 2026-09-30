"""TDD coverage for the Reception Portal candidate PDF export.

Gap: reception.js:206 rendered the candidate's diagnostic result section
("Devolutiva do diagnóstico") with a PDF button hard-coded
`disabled title="Contrato de exportação ainda não disponível"` -
GET /api/v1/reception/candidates/{id}/export never existed (confirmed by
grep across reception.py / reception's service before this file was
written). This file drives that route into existence the same way
test_teacher_portal_http.py's test_export_classroom_report_pdf_returns_200
already proved out the pattern for the Teacher Portal: real PDF bytes
(`%PDF-` magic number, openable with pymupdf, containing the actual
candidate text), not a JSON body mislabelled as "application/pdf".

Two scenarios are covered end-to-end against real service-computed data
(no invented fields):
  - a PRE_REGISTRATION candidate with no diagnostic yet (the ficha only
    has identity/status/timeline data - the export must still work and
    must NOT claim a diagnostic result it doesn't have).
  - a candidate with a COMPLETED InitialDiagnostic (built through the real
    InitialDiagnosticService.start_diagnostic/answer_question flow, exactly
    like tests/test_reception_portal.py's
    test_complete_secretary_candidate_diagnostic_feedback_flow) - the PDF
    must include the real mastery data InitialDiagnosticService.
    get_diagnostic_result() computed.

Also covers the service-level payload builder
(ReportExportService.export_candidate_report) directly, and the 404/403
guard branches on the export route (reusing _require_reception_access,
already covered generically by test_reception_http_gaps.py for the other
routes in this file).
"""

from __future__ import annotations

import asyncio
import unittest
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.api.routes.reception import reception_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    ContentQuestionLink,
    Question,
    QuestionOption,
    QuestionVersion,
    School,
)
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext
from agente_ia_edu.services.initial_diagnostic import InitialDiagnosticService
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.report_export import ReportExportService


class ExportCandidateReportServiceTests(unittest.TestCase):
    """Direct unit coverage of the new payload builder - no DB, no HTTP."""

    def test_basic_candidate_without_diagnostic_has_identity_summary_only(self):
        payload = ReportExportService.export_candidate_report({
            "id": "11111111-1111-1111-1111-111111111111",
            "full_name": "Bruno Alves Ferreira",
            "preferred_name": "Bruno",
            "phone": "11977771122",
            "email": "bruno@example.com",
            "academic_year": "2026",
            "unit_id": "UNIDADE_CENTRO",
            "segment_id": "ENSINO_MEDIO",
            "grade_level": "3_SERIE",
            "classroom_id": "TURMA_3A",
            "status_label": "Pré-cadastro",
            "result": None,
        })
        self.assertEqual(payload["export_format"], "pdf")
        self.assertEqual(payload["content_type"], "application/pdf")
        self.assertTrue(payload["filename"].endswith(".pdf"))
        self.assertIn("Bruno Alves Ferreira", payload["title"])
        self.assertEqual(payload["summary"]["full_name"], "Bruno Alves Ferreira")
        self.assertEqual(payload["summary"]["status_label"], "Pré-cadastro")
        # No diagnostic was ever run - the payload must not invent mastery data.
        self.assertNotIn("mastery_map", payload)
        self.assertNotIn("probable_gaps", payload)

    def test_candidate_with_diagnostic_result_includes_mastery_sections(self):
        payload = ReportExportService.export_candidate_report({
            "id": "22222222-2222-2222-2222-222222222222",
            "full_name": "Carla Souza",
            "phone": "11988882233",
            "email": "carla@example.com",
            "academic_year": "2026",
            "unit_id": "UNIDADE_CENTRO",
            "segment_id": "ENSINO_MEDIO",
            "grade_level": "3_SERIE",
            "status_label": "Devolutiva disponível",
            "result": {
                "overall_confidence": 0.82,
                "evidence_count": 1,
                "total_questions_asked": 1,
                "total_correct": 1,
                "mastery_map": [
                    {"content_name": "Conceitos iniciais", "estimated_mastery": 85.0,
                     "recommended_difficulty": "HARD"},
                ],
                "probable_gaps": [],
            },
        })
        self.assertIn("Conceitos iniciais", str(payload["mastery_map"]))
        self.assertEqual(payload["summary"]["overall_confidence_percent"], 82.0)
        self.assertEqual(payload["probable_gaps"], [])

    def test_invalid_format_raises_value_error(self):
        with self.assertRaises(ValueError):
            ReportExportService.export_candidate_report({"id": "x", "full_name": "X"}, export_format="csv")


class ReceptionExportHttpTests(unittest.TestCase):
    def setUp(self):
        async def setup_database():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            school = School(code="RECEXP_A", name="Escola Export A")
            async with factory() as session:
                session.add(school)
                await session.commit()
                return engine, factory, school.id

        self.engine, self.session_factory, self.school_a = asyncio.run(setup_database())
        self.context = {
            "value": AuthenticatedUserContext(
                user_id="secretary-a", external_identity_id="secretary-a", role="SECRETARY",
                school_id=self.school_a, scope_type="UNIT", scope_external_id="UNIDADE_CENTRO",
            )
        }
        self.identity = {
            "value": ExternalIdentityContext(provider="test", external_user_id="secretary-a", roles=("secretary",))
        }
        app = FastAPI()
        app.include_router(reception_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_authenticated_context] = lambda: self.context["value"]
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    def candidate_payload(self, **overrides):
        payload = {
            "school_id": str(self.school_a),
            "full_name": "Bruno Alves Ferreira",
            "preferred_name": "Bruno",
            "birth_date": "2000-04-12",
            "guardian_name": None,
            "phone": "(11) 97777-1122",
            "email": "bruno@example.com",
            "academic_year": "2026",
            "unit_id": "UNIDADE_CENTRO",
            "segment_id": "ENSINO_MEDIO",
            "grade_level": "3_SERIE",
            "classroom_id": "TURMA_3A",
        }
        payload.update(overrides)
        return payload

    def create_candidate(self, **overrides):
        response = self.client.post(
            "/api/v1/reception/candidates", json=self.candidate_payload(**overrides)
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def _assert_valid_pdf_response(self, response):
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertIn("attachment", response.headers["content-disposition"])
        self.assertTrue(response.content.startswith(b"%PDF-"))
        self.assertGreater(len(response.content), 200)
        import pymupdf
        doc = pymupdf.open(stream=response.content, filetype="pdf")
        try:
            return "".join(page.get_text() for page in doc)
        finally:
            doc.close()

    def test_export_candidate_without_diagnostic_returns_pdf_with_identity_data(self):
        created = self.create_candidate(email="export-basic@example.com", phone="11911119991")
        response = self.client.get(f"/api/v1/reception/candidates/{created['id']}/export")
        full_text = self._assert_valid_pdf_response(response)
        self.assertIn("Bruno Alves Ferreira", full_text)
        self.assertIn("Pré-cadastro", full_text)

    def test_export_candidate_not_found_returns_404(self):
        response = self.client.get(f"/api/v1/reception/candidates/{uuid4()}/export")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Atendimento não encontrado.")

    def test_export_candidate_outside_secretary_scope_returns_403(self):
        # Created while the secretary's own scope is UNIDADE_CENTRO (matches
        # candidate_payload's default unit_id, so creation itself succeeds);
        # the secretary is then reassigned to a different unit before calling
        # export, so _require_reception_access's scope check must 403 it.
        created = self.create_candidate(email="other-scope@example.com", phone="11911119992")
        self.context["value"] = AuthenticatedUserContext(
            user_id="secretary-a", external_identity_id="secretary-a", role="SECRETARY",
            school_id=self.school_a, scope_type="UNIT", scope_external_id="UNIDADE_NORTE",
        )
        response = self.client.get(f"/api/v1/reception/candidates/{created['id']}/export")
        self.assertEqual(response.status_code, 403, response.text)

    def test_export_candidate_with_completed_diagnostic_includes_real_mastery_data(self):
        created = self.create_candidate(email="export-complete@example.com", phone="11911119993")
        released = self.client.post(
            f"/api/v1/reception/candidates/{created['id']}/diagnostic-release"
        ).json()
        self.identity["value"] = ExternalIdentityContext(
            provider="test", external_user_id="student-export-complete", roles=("student",)
        )
        activated = self.client.post(
            "/api/v1/reception/diagnostic-access/activate",
            json={"token": released["activation_token"]},
        )
        self.assertEqual(activated.status_code, 200, activated.text)

        async def execute_diagnostic():
            async with self.session_factory() as session:
                root = CatalogNode(node_type="DISCIPLINE", name="Química", position=1, active=True)
                session.add(root)
                await session.flush()
                root.root_id = root.id
                content = CatalogNode(
                    parent_id=root.id, root_id=root.id, node_type="CONTENT",
                    code="RECEXP-DIAG", name="Conceitos iniciais", position=1, active=True,
                )
                session.add(content)
                await session.flush()
                question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
                session.add(question)
                await session.flush()
                version = QuestionVersion(
                    question_id=question.id, version_kind="official_original",
                    canonical_text="Questão diagnóstica", statement="Questão diagnóstica",
                    content_hash="reception-export-flow", recommended_difficulty="EASY",
                )
                session.add(version)
                await session.flush()
                correct = QuestionOption(
                    question_version_id=version.id, option_key="A", position=1,
                    text="Resposta", is_valid_option=True,
                )
                session.add_all([
                    correct,
                    ContentQuestionLink(content_node_id=content.id, question_version_id=version.id),
                ])
                await session.commit()
                service = InitialDiagnosticService(session, KnowledgeService(session))
                diagnostic, selection = await service.start_diagnostic(
                    student_id="student-export-complete",
                    school_id=self.school_a,
                    classroom_id="TURMA_3A",
                    academic_year="2026",
                    grade_level="3_SERIE",
                    discipline="Química",
                    metadata={"context_snapshot": {"unit_id": "UNIDADE_CENTRO"}},
                )
                diagnostic, _, complete, _ = await service.answer_question(
                    diagnostic_id=diagnostic.id,
                    selection_id=selection.id,
                    selected_option_id=correct.id,
                    authorized_student_id="student-export-complete",
                    authorized_school_id=self.school_a,
                )
                return complete

        complete = asyncio.run(execute_diagnostic())
        self.assertTrue(complete)

        self.context["value"] = AuthenticatedUserContext(
            user_id="secretary-a", external_identity_id="secretary-a", role="SECRETARY",
            school_id=self.school_a, scope_type="UNIT", scope_external_id="UNIDADE_CENTRO",
        )
        response = self.client.get(f"/api/v1/reception/candidates/{created['id']}/export")
        full_text = self._assert_valid_pdf_response(response)
        self.assertIn("Bruno Alves Ferreira", full_text)
        self.assertIn("Conceitos iniciais", full_text)


if __name__ == "__main__":
    unittest.main()
