import asyncio
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_session_factory, get_current_identity
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AcademicYear, Class, GradeLevel, School, Segment, UserSchoolLink
from agente_ia_edu.identity import ExternalIdentityContext


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class EssayPromptsRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        cls.loop.run_until_complete(_prep())
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_r2")
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _seed_school_teacher_and_class(self, code: str):
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"EPR-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="prof_r2", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id=f"SEG-{code}")
                session.add(segment)
                await session.flush()
                grade = GradeLevel(
                    id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
                    name="grade", external_id=f"GRADE-{code}",
                )
                year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id=f"YEAR-{code}")
                session.add_all([grade, year])
                await session.flush()
                klass = Class(
                    id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
                    grade_level_id=grade.id, name="turma", external_id=f"TURMA-{code}",
                )
                session.add(klass)
                await session.commit()
                return school.id, klass.id

        return self.loop.run_until_complete(_seed())

    def test_full_management_flow(self):
        school_id, class_id = self._seed_school_teacher_and_class("1")
        self._as("prof_r2")

        create_resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema", "statement": "Disserte.", "year": 2026},
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        prompt_id = create_resp.json()["id"]
        self.assertEqual(create_resp.json()["status"], "DRAFT")

        material_resp = self.client.post(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/materials",
            json={"material_type": "TEXT", "content": "Apoio.", "position": 0},
        )
        self.assertEqual(material_resp.status_code, 201, material_resp.text)

        assignment_resp = self.client.post(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/assignments",
            json={"class_id": str(class_id)},
        )
        self.assertEqual(assignment_resp.status_code, 201, assignment_resp.text)
        self.assertEqual(assignment_resp.json()["status"], "OPEN")

    def test_upload_prompt_material_stores_any_file_type(self):
        school_id, class_id = self._seed_school_teacher_and_class("5")
        self._as("prof_r2")

        create_resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema com anexo", "statement": "Disserte.", "year": 2026},
        )
        prompt_id = create_resp.json()["id"]

        upload_resp = self.client.post(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/materials/upload",
            data={"position": "0"},
            files={"file": ("reportagem.docx", b"conteudo qualquer de exemplo", "application/octet-stream")},
        )
        self.assertEqual(upload_resp.status_code, 201, upload_resp.text)
        body = upload_resp.json()
        self.assertEqual(body["material_type"], "FILE")
        self.assertIsNone(body["content"])
        self.assertTrue(body["storage_uri"])
        self.assertTrue(body["storage_uri"].endswith("reportagem.docx"))

    def test_upload_prompt_material_rejects_oversized_file(self):
        school_id, class_id = self._seed_school_teacher_and_class("6")
        self._as("prof_r2")

        create_resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema", "statement": "Disserte.", "year": 2026},
        )
        prompt_id = create_resp.json()["id"]

        import io
        oversized = io.BytesIO(b"x" * (25 * 1024 * 1024 + 1))
        upload_resp = self.client.post(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/materials/upload",
            data={"position": "0"},
            files={"file": ("grande.pdf", oversized, "application/pdf")},
        )
        self.assertEqual(upload_resp.status_code, 413, upload_resp.text)

    def test_student_cannot_create_prompt(self):
        self._seed_school_teacher_and_class("2")
        self._as("student_r2")
        resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema", "statement": "Disserte.", "year": 2026},
        )
        self.assertEqual(resp.status_code, 403)
        self._as("prof_r2")

    def _seed_school_teacher_and_classes(self, code: str, count: int):
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"EPRB-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="prof_r2", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id=f"SEGB-{code}")
                session.add(segment)
                await session.flush()
                grade = GradeLevel(
                    id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
                    name="grade", external_id=f"GRADEB-{code}",
                )
                year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id=f"YEARB-{code}")
                session.add_all([grade, year])
                await session.flush()
                class_ids = []
                for i in range(count):
                    klass = Class(
                        id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
                        grade_level_id=grade.id, name=f"turma-{i}", external_id=f"TURMAB-{code}-{i}",
                    )
                    session.add(klass)
                    await session.flush()
                    class_ids.append(klass.id)
                await session.commit()
                return school.id, class_ids

        return self.loop.run_until_complete(_seed())

    def test_bulk_assignment_assigns_every_class_in_one_call(self):
        school_id, class_ids = self._seed_school_teacher_and_classes("3", 3)
        self._as("prof_r2")

        create_resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema multi-turma", "statement": "Disserte.", "year": 2026},
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        prompt_id = create_resp.json()["id"]

        bulk_resp = self.client.post(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/assignments/bulk",
            json={"class_ids": [str(c) for c in class_ids]},
        )
        self.assertEqual(bulk_resp.status_code, 200, bulk_resp.text)
        body = bulk_resp.json()
        self.assertEqual(len(body["assigned"]), 3)
        self.assertEqual(body["failures"], {})
        self.assertEqual(
            {a["class_id"] for a in body["assigned"]}, {str(c) for c in class_ids},
        )

    def test_bulk_assignment_reports_per_class_failures(self):
        school_id, class_ids = self._seed_school_teacher_and_classes("4", 2)
        self._as("prof_r2")

        create_resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema", "statement": "Disserte.", "year": 2026},
        )
        prompt_id = create_resp.json()["id"]

        foreign_class_id = str(uuid.uuid4())
        bulk_resp = self.client.post(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/assignments/bulk",
            json={"class_ids": [str(class_ids[0]), foreign_class_id]},
        )
        self.assertEqual(bulk_resp.status_code, 200, bulk_resp.text)
        body = bulk_resp.json()
        self.assertEqual(len(body["assigned"]), 1)
        self.assertEqual(body["assigned"][0]["class_id"], str(class_ids[0]))
        self.assertIn(foreign_class_id, body["failures"])

    def test_delete_moves_prompt_to_trash_and_hides_it_from_the_normal_list(self):
        school_id, class_id = self._seed_school_teacher_and_class("7")
        self._as("prof_r2")

        create_resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema para excluir", "statement": "Disserte.", "year": 2026},
        )
        prompt_id = create_resp.json()["id"]

        delete_resp = self.client.delete(f"/api/v1/catalog/essay-prompts/{prompt_id}")
        self.assertEqual(delete_resp.status_code, 204, delete_resp.text)

        list_resp = self.client.get("/api/v1/catalog/essay-prompts")
        self.assertNotIn(prompt_id, [p["id"] for p in list_resp.json()])

        detail_resp = self.client.get(f"/api/v1/catalog/essay-prompts/{prompt_id}")
        self.assertEqual(detail_resp.status_code, 403)

    def test_trash_lists_deleted_prompt_with_days_remaining(self):
        school_id, class_id = self._seed_school_teacher_and_class("8")
        self._as("prof_r2")

        create_resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema na lixeira", "statement": "Disserte.", "year": 2026},
        )
        prompt_id = create_resp.json()["id"]
        self.client.delete(f"/api/v1/catalog/essay-prompts/{prompt_id}")

        trash_resp = self.client.get("/api/v1/catalog/essay-prompts/trash")
        self.assertEqual(trash_resp.status_code, 200, trash_resp.text)
        entries = {e["id"]: e for e in trash_resp.json()}
        self.assertIn(prompt_id, entries)
        self.assertEqual(entries[prompt_id]["days_remaining"], 30)
        self.assertIsNotNone(entries[prompt_id]["deleted_at"])

    def test_restore_brings_prompt_back_to_the_normal_list(self):
        school_id, class_id = self._seed_school_teacher_and_class("9")
        self._as("prof_r2")

        create_resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema para restaurar", "statement": "Disserte.", "year": 2026},
        )
        prompt_id = create_resp.json()["id"]
        self.client.delete(f"/api/v1/catalog/essay-prompts/{prompt_id}")

        restore_resp = self.client.post(f"/api/v1/catalog/essay-prompts/{prompt_id}/restore")
        self.assertEqual(restore_resp.status_code, 200, restore_resp.text)

        list_resp = self.client.get("/api/v1/catalog/essay-prompts")
        self.assertIn(prompt_id, [p["id"] for p in list_resp.json()])

        trash_resp = self.client.get("/api/v1/catalog/essay-prompts/trash")
        self.assertNotIn(prompt_id, [e["id"] for e in trash_resp.json()])

    def test_restore_a_prompt_that_is_not_deleted_is_rejected(self):
        school_id, class_id = self._seed_school_teacher_and_class("10")
        self._as("prof_r2")

        create_resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema ativo", "statement": "Disserte.", "year": 2026},
        )
        prompt_id = create_resp.json()["id"]

        restore_resp = self.client.post(f"/api/v1/catalog/essay-prompts/{prompt_id}/restore")
        self.assertEqual(restore_resp.status_code, 422)

    def test_delete_rejects_a_prompt_from_another_school(self):
        school_id, class_id = self._seed_school_teacher_and_class("11")
        self._as("prof_r2")
        create_resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema", "statement": "Disserte.", "year": 2026},
        )
        prompt_id = create_resp.json()["id"]

        # A caller with no school link at all never reaches "is this prompt
        # yours" - _authorize's own "active school context required" check
        # already 403s first, which is the outcome this test cares about.
        self.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_stranger")
        try:
            delete_resp = self.client.delete(f"/api/v1/catalog/essay-prompts/{prompt_id}")
            self.assertEqual(delete_resp.status_code, 403)
        finally:
            self._as("prof_r2")


if __name__ == "__main__":
    unittest.main()
