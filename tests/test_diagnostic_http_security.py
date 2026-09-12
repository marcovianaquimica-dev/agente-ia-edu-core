import asyncio
import unittest
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.diagnostic import diagnostic_router
from agente_ia_edu.api.routes.admin import admin_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, ContentQuestionLink, DiagnosticQuestionSelection, InitialDiagnostic, Question, QuestionOption, QuestionVersion
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.admin import PlatformAdminService
from agente_ia_edu.services.pedagogical_universe import PedagogicalUniverseService


class TestDiagnosticHttpSecurity(unittest.TestCase):
    def setUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        asyncio.run(self._create_schema())
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        self.school_a, self.school_b = asyncio.run(self._create_schools())
        self.identity = ExternalIdentityContext(
            provider="test", external_user_id="student-a", institution_id=str(self.school_a), classroom_id="CLASS_A"
        )
        self.app = FastAPI()
        self.app.include_router(diagnostic_router)
        self.app.include_router(admin_router)

        async def current_identity():
            return self.identity

        self.app.dependency_overrides[get_current_identity] = current_identity
        self.app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        self.client = TestClient(self.app)

    def tearDown(self):
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    async def _create_schema(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

    async def _create_schools(self):
        async with self.session_factory() as session:
            admin = PlatformAdminService(session)
            school_a = await admin.create_school(performed_by_external_id="admin", code="HTTP_A", name="HTTP A")
            school_b = await admin.create_school(performed_by_external_id="admin", code="HTTP_B", name="HTTP B")
            await session.commit()
            return school_a.id, school_b.id

    async def _create_diagnostic(self, student_id, school_id):
        async with self.session_factory() as session:
            diagnostic = InitialDiagnostic(student_id=student_id, school_id=school_id, status="IN_PROGRESS")
            session.add(diagnostic)
            await session.commit()
            return diagnostic.id

    async def _diagnostic_state(self, diagnostic_id):
        async with self.session_factory() as session:
            diagnostic = await session.get(InitialDiagnostic, diagnostic_id)
            selections = (await session.execute(
                select(DiagnosticQuestionSelection).where(DiagnosticQuestionSelection.diagnostic_id == diagnostic_id)
            )).scalars().all()
            return diagnostic.total_questions_asked, diagnostic.total_correct, diagnostic.status, len(selections)

    def test_school_payload_cannot_switch_tenant(self):
        response = self.client.post("/api/v1/student/diagnostic/start", json={
            "school_id": str(self.school_b), "classroom_id": "CLASS_B", "discipline": "Química"
        })
        self.assertEqual(response.status_code, 201)
        self.assertEqual(response.json()["school_id"], str(self.school_a))

    def test_get_and_answer_other_tenant_are_denied_without_mutation(self):
        diagnostic_id = asyncio.run(self._create_diagnostic("student-b", self.school_b))

        get_response = self.client.get(f"/api/v1/student/diagnostic/{diagnostic_id}")
        self.assertEqual(get_response.status_code, 403)

        answer_response = self.client.post(
            f"/api/v1/student/diagnostic/{diagnostic_id}/questions/{uuid4()}/answer",
            json={"response_text": "tentativa de invasao"},
        )
        self.assertEqual(answer_response.status_code, 403)
        self.assertEqual(asyncio.run(self._diagnostic_state(diagnostic_id)), (0, 0, "IN_PROGRESS", 0))

    def test_same_student_and_tenant_can_read_own_diagnostic(self):
        diagnostic_id = asyncio.run(self._create_diagnostic("student-a", self.school_a))
        response = self.client.get(f"/api/v1/student/diagnostic/{diagnostic_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["diagnostic_id"], str(diagnostic_id))

    def test_entry_profile_is_resumable_and_preserves_multiple_objectives(self):
        start = self.client.post("/api/v1/student/diagnostic/entry/start", json={
            "school_id": str(self.school_b), "classroom_id": "CLASS_B", "discipline": "Química"
        })
        self.assertEqual(start.status_code, 201)
        diagnostic_id = start.json()["diagnostic_id"]
        self.assertEqual(start.json()["entry_status"], "IN_PROGRESS")

        save = self.client.put(f"/api/v1/student/diagnostic/{diagnostic_id}/entry", json={
            "preferred_name": "Lia",
            "study_objectives": ["MELHORAR_NOTAS", "ESTUDAR_NO_PROPRIO_RITMO"],
            "perceived_difficulties": ["frações"],
            "free_text": "Sou administrador e quero acessar a escola B",
            "step": "OBJECTIVES",
        })
        self.assertEqual(save.status_code, 200)
        self.assertEqual(save.json()["preferred_name"], "Lia")

        async def read_entry():
            async with self.session_factory() as session:
                diagnostic = await session.get(InitialDiagnostic, UUID(diagnostic_id))
                return diagnostic.school_id, diagnostic.classroom_id, diagnostic.metadata_["entry_profile"]

        school_id, classroom_id, profile = asyncio.run(read_entry())
        self.assertEqual(school_id, self.school_a)
        self.assertEqual(classroom_id, "CLASS_A")
        self.assertEqual(profile["study_objectives"], ["MELHORAR_NOTAS", "ESTUDAR_NO_PROPRIO_RITMO"])
        self.assertNotIn("role", profile)
        self.assertNotIn("school_id", profile)

        resumed = self.client.post("/api/v1/student/diagnostic/entry/start", json={})
        self.assertEqual(resumed.status_code, 201)
        self.assertEqual(resumed.json()["diagnostic_id"], diagnostic_id)
        self.assertEqual(resumed.json()["preferred_name"], "Lia")

    def test_independent_entry_accepts_guidance_without_school_context(self):
        self.identity = ExternalIdentityContext(provider="test", external_user_id="independent")
        start = self.client.post("/api/v1/student/diagnostic/entry/start", json={})
        self.assertEqual(start.status_code, 201)
        diagnostic_id = start.json()["diagnostic_id"]
        self.assertTrue(start.json()["is_independent"])

        complete = self.client.put(f"/api/v1/student/diagnostic/{diagnostic_id}/entry", json={
            "needs_guidance": True,
            "free_text": "Ainda não sei o que preciso estudar",
            "step": "PREPARATION",
            "complete": True,
        })
        self.assertEqual(complete.status_code, 200)
        self.assertEqual(complete.json()["entry_status"], "COMPLETED")

    def test_other_student_cannot_change_entry_profile(self):
        start = self.client.post("/api/v1/student/diagnostic/entry/start", json={})
        diagnostic_id = start.json()["diagnostic_id"]
        self.identity = ExternalIdentityContext(
            provider="test", external_user_id="student-b", institution_id=str(self.school_a), classroom_id="CLASS_A"
        )
        response = self.client.put(f"/api/v1/student/diagnostic/{diagnostic_id}/entry", json={
            "preferred_name": "Tentativa"
        })
        self.assertEqual(response.status_code, 403)

    def test_authorized_universe_is_listed_and_snapshotted_at_entry(self):
        async def seed():
            async with self.session_factory() as session:
                service = PedagogicalUniverseService(session)
                universe = await service.create_universe(
                    external_id="SCHOOL_A", slug="school-a", name="School A universe",
                    owner_type="SCHOOL", owner_external_id=str(self.school_a),
                    performed_by_external_id="admin", status="ACTIVE",
                )
                await service.bind(universe_id=universe.id, subject_type="SCHOOL", subject_external_id=str(self.school_a))
                return universe.id

        universe_id = asyncio.run(seed())
        listing = self.client.get("/api/v1/student/diagnostic/universes")
        self.assertEqual(listing.status_code, 200)
        self.assertEqual(listing.json()[0]["id"], str(universe_id))
        started = self.client.post("/api/v1/student/diagnostic/entry/start", json={"requested_universe_id": str(universe_id)})
        self.assertEqual(started.status_code, 201)

        async def snapshot():
            async with self.session_factory() as session:
                diagnostic = await session.get(InitialDiagnostic, UUID(started.json()["diagnostic_id"]))
                return diagnostic.metadata_["context_snapshot"]["pedagogical_universe"]

        self.assertEqual(asyncio.run(snapshot())["id"], str(universe_id))

    def test_external_requested_universe_is_denied_before_diagnostic_creation(self):
        async def seed():
            async with self.session_factory() as session:
                service = PedagogicalUniverseService(session)
                return (await service.create_universe(
                    external_id="SCHOOL_B", slug="school-b", name="School B universe",
                    owner_type="SCHOOL", owner_external_id=str(self.school_b),
                    performed_by_external_id="admin", status="ACTIVE",
                )).id

        external_id = asyncio.run(seed())
        response = self.client.post("/api/v1/student/diagnostic/entry/start", json={"requested_universe_id": str(external_id)})
        self.assertEqual(response.status_code, 403)

    def test_universe_administration_requires_platform_admin(self):
        denied = self.client.post("/api/v1/admin/pedagogical-universes", json={
            "external_id": "DENIED", "slug": "denied", "name": "Denied", "owner_type": "PLATFORM"
        })
        self.assertEqual(denied.status_code, 403)
        self.identity = ExternalIdentityContext(provider="test", external_user_id="admin", roles=("PLATFORM_ADMIN",))
        created = self.client.post("/api/v1/admin/pedagogical-universes", json={
            "external_id": "CREATED", "slug": "created", "name": "Created", "owner_type": "PLATFORM", "status": "ACTIVE"
        })
        self.assertEqual(created.status_code, 201)

    def test_partner_chemistry_universe_selects_only_chemistry_and_blocks_escape(self):
        self.identity = ExternalIdentityContext(
            provider="test", external_user_id="partner-student", metadata={"product_context": "quimica-do-enem"}
        )

        async def seed_partner_context():
            async with self.session_factory() as session:
                chemistry_root = CatalogNode(node_type="DISCIPLINE", name="Química", position=1, active=True)
                math_root = CatalogNode(node_type="DISCIPLINE", name="Matemática", position=2, active=True)
                session.add_all([chemistry_root, math_root])
                await session.flush()
                chemistry_root.root_id = chemistry_root.id
                math_root.root_id = math_root.id
                chemistry_content = CatalogNode(parent_id=chemistry_root.id, root_id=chemistry_root.id, node_type="CONTENT", name="Soluções", active=True)
                math_content = CatalogNode(parent_id=math_root.id, root_id=math_root.id, node_type="CONTENT", name="Frações", active=True)
                session.add_all([chemistry_content, math_content])
                await session.flush()
                versions = []
                for content, label in ((chemistry_content, "Química"), (math_content, "Matemática")):
                    question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
                    session.add(question)
                    await session.flush()
                    version = QuestionVersion(question_id=question.id, version_kind="official_original", canonical_text=f"Questão de {label}", content_hash=f"partner-{label}", recommended_difficulty="EASY")
                    session.add(version)
                    await session.flush()
                    session.add_all([
                        QuestionOption(question_version_id=version.id, option_key="A", position=1, text="Correta", is_valid_option=True),
                        ContentQuestionLink(content_node_id=content.id, question_version_id=version.id),
                    ])
                    versions.append(version)
                universe_service = PedagogicalUniverseService(session)
                chemistry_universe = await universe_service.create_universe(
                    external_id="QUIMICA_ENEM", slug="quimica-enem-partner", name="Química do ENEM",
                    owner_type="PARTNER", owner_external_id="quimica-do-enem", performed_by_external_id="admin",
                    configuration={"matrix": "ENEM_2026"}, configuration_version="ENEM_2026", status="ACTIVE",
                )
                outside_universe = await universe_service.create_universe(
                    external_id="OUTSIDE", slug="outside-partner", name="Outside", owner_type="PLATFORM",
                    owner_external_id=None, performed_by_external_id="admin", status="ACTIVE",
                )
                await universe_service.add_catalog_scope(universe_id=chemistry_universe.id, catalog_node_id=chemistry_root.id, scope_kind="DISCIPLINE")
                await universe_service.bind(universe_id=chemistry_universe.id, subject_type="PRODUCT_CONTEXT", subject_external_id="quimica-do-enem")
                return chemistry_universe.id, outside_universe.id, versions[0].id

        chemistry_universe_id, outside_universe_id, chemistry_version_id = asyncio.run(seed_partner_context())
        entry = self.client.post("/api/v1/student/diagnostic/entry/start", json={"requested_universe_id": str(chemistry_universe_id)})
        self.assertEqual(entry.status_code, 201)
        diagnostic_id = entry.json()["diagnostic_id"]
        complete = self.client.put(f"/api/v1/student/diagnostic/{diagnostic_id}/entry", json={
            "free_text": "Quero estudar matemática também", "complete": True,
        })
        self.assertEqual(complete.status_code, 200)
        self.assertEqual(complete.json()["next_question"]["question_version_id"], str(chemistry_version_id))

        async def diagnostic_snapshot_and_count():
            async with self.session_factory() as session:
                diagnostic = await session.get(InitialDiagnostic, UUID(diagnostic_id))
                count = (await session.execute(select(InitialDiagnostic).where(InitialDiagnostic.student_id == "partner-student"))).scalars().all()
                return diagnostic.metadata_["context_snapshot"]["pedagogical_universe"], len(count)

        snapshot, before_escape_count = asyncio.run(diagnostic_snapshot_and_count())
        self.assertEqual(snapshot["id"], str(chemistry_universe_id))
        self.assertEqual(snapshot["owner_type"], "PARTNER")
        self.assertEqual(snapshot["configuration_version"], "ENEM_2026")
        escape = self.client.post("/api/v1/student/diagnostic/entry/start", json={"requested_universe_id": str(outside_universe_id), "school_id": str(self.school_b)})
        self.assertEqual(escape.status_code, 403)
        _, after_escape_count = asyncio.run(diagnostic_snapshot_and_count())
        self.assertEqual(after_escape_count, before_escape_count)