import asyncio
import unittest
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.api.routes.diagnostic import get_current_diagnostic_identity
from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.routes.reception import reception_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AdminAuditLog,
    CatalogNode,
    ContentQuestionLink,
    Question,
    QuestionOption,
    QuestionVersion,
    School,
    UserSchoolLink,
)
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext
from agente_ia_edu.auth.token import TestTokenValidator as ReceptionTokenValidator
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.authorization import AuthorizationService
from agente_ia_edu.services.initial_diagnostic import InitialDiagnosticService
from agente_ia_edu.services.knowledge import KnowledgeService


class TestReceptionPortalHTTP(unittest.TestCase):
    def setUp(self):
        async def setup_database():
            engine = create_async_engine(
                "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
            )
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            school_a = School(code="REC_A", name="Escola Atendimento A")
            school_b = School(code="REC_B", name="Escola Atendimento B")
            async with factory() as session:
                session.add_all([school_a, school_b])
                await session.commit()
                return engine, factory, school_a.id, school_b.id

        self.engine, self.session_factory, self.school_a, self.school_b = asyncio.run(
            setup_database()
        )
        self.context = {
            "value": AuthenticatedUserContext(
                user_id="secretary-a",
                external_identity_id="secretary-a",
                role="SECRETARY",
                school_id=self.school_a,
                scope_type="UNIT",
                scope_external_id="UNIDADE_CENTRO",
            )
        }
        self.identity = {
            "value": ExternalIdentityContext(
                provider="test", external_user_id="secretary-a", roles=("secretary",)
            )
        }
        app = FastAPI()
        app.include_router(reception_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_authenticated_context] = (
            lambda: self.context["value"]
        )
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
            "full_name": "Marina Oliveira Costa",
            "preferred_name": "Marina",
            "birth_date": "2000-04-12",
            "guardian_name": None,
            "phone": "(11) 98888-7766",
            "email": "marina@example.com",
            "academic_year": "2026",
            "unit_id": "UNIDADE_CENTRO",
            "segment_id": "ENSINO_MEDIO",
            "grade_level": "3_SERIE",
            "classroom_id": "TURMA_3A",
        }
        payload.update(overrides)
        return payload

    def create_candidate(self):
        response = self.client.post(
            "/api/v1/reception/candidates", json=self.candidate_payload()
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    def test_create_search_validation_and_duplicate_protection(self):
        created = self.create_candidate()
        self.assertEqual(created["status"], "PRE_REGISTRATION")
        self.assertIsNone(created["progress_percent"])

        search = self.client.get(
            "/api/v1/reception/candidates",
            params={"school_id": str(self.school_a), "q": "98888"},
        )
        self.assertEqual(search.status_code, 200)
        self.assertEqual([item["id"] for item in search.json()], [created["id"]])

        duplicate = self.client.post(
            "/api/v1/reception/candidates",
            json=self.candidate_payload(phone="(21) 97777-6655"),
        )
        self.assertEqual(duplicate.status_code, 409)

        formatted_phone_duplicate = self.client.post(
            "/api/v1/reception/candidates",
            json=self.candidate_payload(
                email="another@example.com", phone="11 98888 7766"
            ),
        )
        self.assertEqual(formatted_phone_duplicate.status_code, 409)

        minor = self.client.post(
            "/api/v1/reception/candidates",
            json=self.candidate_payload(
                email="minor@example.com",
                phone="11911112222",
                birth_date="2012-01-01",
                guardian_name=None,
            ),
        )
        self.assertEqual(minor.status_code, 422)

    def test_school_isolation_release_activation_and_audit(self):
        created = self.create_candidate()
        self.context["value"] = AuthenticatedUserContext(
            user_id="secretary-b",
            external_identity_id="secretary-b",
            role="SECRETARY",
            school_id=self.school_b,
            scope_type="UNIT",
            scope_external_id="UNIDADE_CENTRO",
        )
        denied = self.client.get(f"/api/v1/reception/candidates/{created['id']}")
        self.assertEqual(denied.status_code, 403)

        self.context["value"] = AuthenticatedUserContext(
            user_id="secretary-a",
            external_identity_id="secretary-a",
            role="SECRETARY",
            school_id=self.school_a,
            scope_type="UNIT",
            scope_external_id="UNIDADE_CENTRO",
        )
        release = self.client.post(
            f"/api/v1/reception/candidates/{created['id']}/diagnostic-release"
        )
        self.assertEqual(release.status_code, 200, release.text)
        released = release.json()
        self.assertEqual(released["candidate"]["status"], "DIAGNOSTIC_RELEASED")
        self.assertGreater(len(released["activation_token"]), 20)

        self.identity["value"] = ExternalIdentityContext(
            provider="test",
            external_user_id="student-marina",
            student_id="student-marina",
            roles=("student",),
        )
        activated = self.client.post(
            "/api/v1/reception/diagnostic-access/activate",
            json={"token": released["activation_token"]},
        )
        self.assertEqual(activated.status_code, 200, activated.text)
        self.assertEqual(activated.json()["external_student_id"], "student-marina")

        async def verify_records():
            async with self.session_factory() as session:
                link = await session.scalar(
                    select(UserSchoolLink).where(
                        UserSchoolLink.external_user_id == "student-marina"
                    )
                )
                actions = set((await session.scalars(select(AdminAuditLog.action))).all())
                return link, actions

        link, actions = asyncio.run(verify_records())
        self.assertEqual(link.school_id, self.school_a)
        self.assertEqual(link.role, "STUDENT")
        self.assertEqual(link.scope_external_id, "TURMA_3A")
        self.assertEqual(link.metadata_["grade_level"], "3_SERIE")
        self.assertTrue({
            "RECEPTION_CANDIDATE_CREATED",
            "INITIAL_DIAGNOSTIC_RELEASED",
            "DIAGNOSTIC_ACCESS_ACTIVATED",
        }.issubset(actions))

        resolved = asyncio.run(
            get_current_diagnostic_identity(
                identity=self.identity["value"], session_factory=self.session_factory
            )
        )
        self.assertEqual(resolved.institution_id, str(self.school_a))
        self.assertEqual(resolved.unit_id, "UNIDADE_CENTRO")
        self.assertEqual(resolved.grade_level, "3_SERIE")
        self.assertEqual(resolved.classroom_id, "TURMA_3A")
        self.assertEqual(resolved.metadata["segment"], "ENSINO_MEDIO")

    def test_secretary_unit_isolation_and_administrative_audit(self):
        created = self.create_candidate()
        outside_create = self.client.post(
            "/api/v1/reception/candidates",
            json=self.candidate_payload(
                email="outside@example.com",
                phone="11922223333",
                unit_id="UNIDADE_NORTE",
            ),
        )
        self.assertEqual(outside_create.status_code, 403)

        self.context["value"] = AuthenticatedUserContext(
            user_id="secretary-north",
            external_identity_id="secretary-north",
            role="SECRETARY",
            school_id=self.school_a,
            scope_type="UNIT",
            scope_external_id="UNIDADE_NORTE",
        )
        self.assertEqual(
            self.client.get(f"/api/v1/reception/candidates/{created['id']}").status_code,
            403,
        )
        listed = self.client.get(
            "/api/v1/reception/candidates", params={"school_id": str(self.school_a)}
        )
        self.assertEqual(listed.status_code, 200)
        self.assertEqual(listed.json(), [])

        async def link_secretary():
            async with self.session_factory() as session:
                link = await PlatformAdminService(session).link_user_to_school(
                    performed_by_external_id="platform-admin",
                    external_user_id="secretary-linked",
                    role=AdminRole.SECRETARY,
                    scope_type=AdminScopeType.UNIT,
                    school_id=self.school_a,
                    scope_external_id="UNIDADE_CENTRO",
                )
                audit = await session.scalar(
                    select(AdminAuditLog).where(
                        AdminAuditLog.action == "USER_LINKED",
                        AdminAuditLog.entity_id == str(link.id),
                    )
                )
                return link, audit

        link, audit = asyncio.run(link_secretary())
        self.assertEqual(link.role, "SECRETARY")
        self.assertEqual(link.scope_type, "UNIT")
        self.assertIsNotNone(audit)
        self.assertEqual(
            asyncio.run(
                ReceptionTokenValidator().validate("test:secretary:front-desk")
            ).roles,
            ("SECRETARY",),
        )
        self.assertLess(
            AuthorizationService._role_rank("SECRETARY"),
            AuthorizationService._role_rank("TEACHER"),
        )

    def test_secretary_is_denied_from_non_reception_domains(self):
        guarded_app = create_app()
        guarded_app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        guarded_app.dependency_overrides[get_current_identity] = (
            lambda: ExternalIdentityContext(
                provider="test",
                external_user_id="secretary-a",
                roles=("SECRETARY",),
            )
        )
        with TestClient(guarded_app) as client:
            cases = [
                ("get", "/api/v1/admin/schools", None),
                ("get", f"/api/v1/coordination/dashboard?school_id={self.school_a}", None),
                ("get", "/api/v1/questions", None),
                ("post", "/api/v1/student/diagnostic/start", {}),
            ]
            for method, path, payload in cases:
                response = client.request(method, path, json=payload)
                self.assertEqual(response.status_code, 403, (path, response.text))
                self.assertIn("reception domain", response.json()["detail"])

    def test_complete_secretary_candidate_diagnostic_feedback_flow(self):
        created = self.create_candidate()
        released = self.client.post(
            f"/api/v1/reception/candidates/{created['id']}/diagnostic-release"
        ).json()
        self.identity["value"] = ExternalIdentityContext(
            provider="test",
            external_user_id="student-complete",
            student_id="student-complete",
            roles=("student",),
        )
        activated = self.client.post(
            "/api/v1/reception/diagnostic-access/activate",
            json={"token": released["activation_token"]},
        )
        self.assertEqual(activated.status_code, 200, activated.text)

        async def execute_diagnostic():
            identity = await get_current_diagnostic_identity(
                identity=self.identity["value"], session_factory=self.session_factory
            )
            async with self.session_factory() as session:
                root = CatalogNode(
                    node_type="DISCIPLINE", name="Química", position=1, active=True
                )
                session.add(root)
                await session.flush()
                root.root_id = root.id
                content = CatalogNode(
                    parent_id=root.id,
                    root_id=root.id,
                    node_type="CONTENT",
                    code="REC-DIAG",
                    name="Conceitos iniciais",
                    position=1,
                    active=True,
                )
                session.add(content)
                await session.flush()
                question = Question(
                    validation_status="approved",
                    status="PUBLISHED",
                    visibility_scope="PUBLIC",
                )
                session.add(question)
                await session.flush()
                version = QuestionVersion(
                    question_id=question.id,
                    version_kind="official_original",
                    canonical_text="Questão diagnóstica",
                    statement="Questão diagnóstica",
                    content_hash="reception-complete-flow",
                    recommended_difficulty="EASY",
                )
                session.add(version)
                await session.flush()
                correct = QuestionOption(
                    question_version_id=version.id,
                    option_key="A",
                    position=1,
                    text="Resposta",
                    is_valid_option=True,
                )
                session.add_all([
                    correct,
                    ContentQuestionLink(
                        content_node_id=content.id, question_version_id=version.id
                    ),
                ])
                await session.commit()
                service = InitialDiagnosticService(session, KnowledgeService(session))
                diagnostic, selection = await service.start_diagnostic(
                    student_id=identity.external_user_id,
                    school_id=self.school_a,
                    classroom_id=identity.classroom_id,
                    academic_year="2026",
                    grade_level=identity.grade_level,
                    discipline="Química",
                    metadata={"context_snapshot": {"unit_id": identity.unit_id}},
                )
                diagnostic, _, complete, _ = await service.answer_question(
                    diagnostic_id=diagnostic.id,
                    selection_id=selection.id,
                    selected_option_id=correct.id,
                    authorized_student_id=identity.external_user_id,
                    authorized_school_id=self.school_a,
                )
                return diagnostic.id, complete

        diagnostic_id, complete = asyncio.run(execute_diagnostic())
        self.assertTrue(complete)
        self.context["value"] = AuthenticatedUserContext(
            user_id="secretary-a",
            external_identity_id="secretary-a",
            role="SECRETARY",
            school_id=self.school_a,
            scope_type="UNIT",
            scope_external_id="UNIDADE_CENTRO",
        )
        detail = self.client.get(f"/api/v1/reception/candidates/{created['id']}")
        self.assertEqual(detail.status_code, 200, detail.text)
        payload = detail.json()
        self.assertEqual(payload["status"], "FEEDBACK_AVAILABLE")
        self.assertEqual(payload["diagnostic_id"], str(diagnostic_id))
        self.assertEqual(payload["result"]["status"], "COMPLETED")
        self.assertEqual(payload["result"]["evidence_count"], 1)

    def test_diagnostic_access_activation_errors_are_localized(self):
        # reception.js has no way to translate an error message it has never
        # seen: InvitationService raises internal English strings ("Invalid
        # invitation token", "Invitation has already been activated") that
        # must not reach the atendente's screen verbatim.
        created = self.create_candidate()
        released = self.client.post(
            f"/api/v1/reception/candidates/{created['id']}/diagnostic-release"
        ).json()
        token = released["activation_token"]

        unknown_token = self.client.post(
            "/api/v1/reception/diagnostic-access/activate",
            json={"token": "x" * 32},
        )
        self.assertEqual(unknown_token.status_code, 400)
        unknown_detail = unknown_token.json()["detail"]
        self.assertNotEqual(unknown_detail, "Invalid invitation token")
        self.assertIn("inválido", unknown_detail.lower())

        self.identity["value"] = ExternalIdentityContext(
            provider="test",
            external_user_id="student-reuse",
            roles=("student",),
        )
        first_activation = self.client.post(
            "/api/v1/reception/diagnostic-access/activate", json={"token": token}
        )
        self.assertEqual(first_activation.status_code, 200, first_activation.text)

        reused_token = self.client.post(
            "/api/v1/reception/diagnostic-access/activate", json={"token": token}
        )
        self.assertEqual(reused_token.status_code, 400)
        reused_detail = reused_token.json()["detail"]
        self.assertNotEqual(reused_detail, "Invitation has already been activated")
        self.assertIn("ativado", reused_detail.lower())


if __name__ == "__main__":
    unittest.main()