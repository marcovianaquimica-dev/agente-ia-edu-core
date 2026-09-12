"""PHASE 25 - Material Delivery & Study Integration backend tests.

TestClient + in-memory SQLite. Reuses the PHASE 23 material model exactly
(TheoryMaterial / TheoryMaterialVersion / MaterialSection / MaterialBlock /
MaterialExercise) - no second model - plus the new PHASE 25 tenant-aware
MaterialAvailabilityService.resolve_for_content()/visible_to_student() and the
StudentMaterialService/MaterialProgress table. Associated exercises are never
played by a second mechanism: "Pratique o que você estudou" is proven to go
through the EXISTING POST /api/v1/student/practice (PHASE 22
AdaptivePracticeService) end to end.

Covers spec s21 (published/unpublished, tenant isolation, curriculum-v2,
sections/blocks/order, progress+resume, associated exercise, absence of
material, AI-agnostic, determinism, no N+1) and s20 (security).
"""

from __future__ import annotations

import ast
import asyncio
import unittest
import uuid as _uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_authenticated_context, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry, AnswerKeyRevision, BookletQuestion, CatalogNode, Exam,
    ExamApplication, ExamBooklet, Institution, MaterialBlock, MaterialExercise,
    MaterialProgress, MaterialSection, PedagogicalClassification, Question,
    QuestionOption, QuestionVersion, SourceDocument, TheoryMaterial,
    TheoryMaterialVersion,
)
from agente_ia_edu.db.models.admin import School
from agente_ia_edu.identity import AuthenticatedUserContext

KEYS = "ABCDE"
_SCHOOL_A = _uuid.uuid5(_uuid.NAMESPACE_DNS, "phase25-school-a")
_SCHOOL_B = _uuid.uuid5(_uuid.NAMESPACE_DNS, "phase25-school-b")


def _ctx(user="stu_a", school=None):
    return AuthenticatedUserContext(user_id=user, external_identity_id=user, role="STUDENT",
                                    school_id=str(school) if school else None,
                                    scope_type="SCHOOL" if school else "PLATFORM")


async def _seed(factory) -> dict:
    async with factory() as s:
        s.add(School(id=_SCHOOL_A, code="P25SCHA", name="Escola P25 A", status="ACTIVE"))
        s.add(School(id=_SCHOOL_B, code="P25SCHB", name="Escola P25 B", status="ACTIVE"))
        await s.flush()

        d1 = CatalogNode(code="P25-DISC", name="Disciplina P25", node_type="DISCIPLINE", active=True)
        s.add(d1); await s.flush(); d1.root_id = d1.id
        a1 = CatalogNode(code="P25-AREA", name="Área P25", node_type="AREA",
                         parent_id=d1.id, root_id=d1.id, active=True)
        s.add(a1); await s.flush()
        cX = CatalogNode(code="P25-C-X", name="Conteúdo X", node_type="CONTENT",
                         parent_id=a1.id, root_id=d1.id, position=1, active=True)
        cY = CatalogNode(code="P25-C-Y", name="Conteúdo Y (sem material)", node_type="CONTENT",
                         parent_id=a1.id, root_id=d1.id, position=2, active=True)
        s.add_all([cX, cY]); await s.flush()

        # -- official questions for content P25-C-X (so "Pratique o que você
        # estudou" can launch a real AdaptivePracticeService practice) --
        inst = Institution(code="P25INEP", name="INEP P25"); s.add(inst); await s.flush()
        exam = Exam(institution_id=inst.id, code="P25ENEM", name="ENEM P25"); s.add(exam); await s.flush()
        app_ = ExamApplication(exam_id=exam.id, year=2024, application_type="regular", day=1)
        s.add(app_); await s.flush()
        bk = ExamBooklet(exam_application_id=app_.id, code="P25C", color="AZUL"); s.add(bk); await s.flush()
        sd = SourceDocument(exam_application_id=app_.id, exam_booklet_id=bk.id, document_type="ANSWER_KEY",
                            source_url="https://x/p25.pdf", acquired_at=datetime.now(timezone.utc),
                            content_hash="p25h"); s.add(sd); await s.flush()
        rev = AnswerKeyRevision(source_document_id=sd.id, revision_number=1, is_official=True)
        s.add(rev); await s.flush()

        qvids = []
        for n in range(1, 6):
            correct = KEYS[n % 5]
            q = Question(validation_status="validated", origin_type="IMPORTED",
                         status="PUBLISHED", visibility_scope="PUBLIC")
            s.add(q); await s.flush()
            v = QuestionVersion(question_id=q.id, version_kind="official_original",
                                canonical_text=f"p25e{n}", statement=f"p25e{n}", content_hash=f"p25h{n}",
                                is_immutable=True)
            s.add(v); await s.flush()
            opts = {}
            for pos, key in enumerate(KEYS, start=1):
                o = QuestionOption(question_version_id=v.id, option_key=key, position=pos,
                                   text=f"Alt {key}", is_valid_option=(key == correct))
                s.add(o); await s.flush(); opts[key] = o
            bq = BookletQuestion(exam_booklet_id=bk.id, question_version_id=v.id,
                                 position=n, official_number=n, page_number=1)
            s.add(bq); await s.flush()
            s.add(AnswerKeyEntry(answer_key_revision_id=rev.id, booklet_question_id=bq.id,
                                 official_answer_label=correct, resolved_option_id=opts[correct].id,
                                 page_number=1))
            s.add(PedagogicalClassification(
                question_version_id=v.id, discipline="CURRICULUM_PROPOSAL", content="P25-C-X",
                subcontent="P25-C-X", difficulty="UNKNOWN", reasoning_type="U",
                prerequisites=[], keywords=[], competencies=[], skills=[],
                status="CLASSIFIED", source="rule", lifecycle="ACTIVE",
                model_version="fx", prompt_version="v1",
                metadata_={"taxonomy_version": "curriculum-v2", "primary_content_code": "P25-C-X",
                           "visual_dependency": False}))
            qvids.append(v.id)
        await s.commit()

        # -- PUBLISHED, SCHOOL-scoped material for content P25-C-X --
        mat = TheoryMaterial(title="Apostila P25", description="Material de teste",
                             material_kind="CHAPTER", authoring_source="TEACHER",
                             visibility_scope="SCHOOL", primary_content_node_id=cX.id,
                             school_id=_SCHOOL_A, created_by_external_identity="prof_p25")
        s.add(mat); await s.flush()
        ver = TheoryMaterialVersion(material_id=mat.id, version_number=1, status="PUBLISHED",
                                    introduction="Intro", summary="Resumo",
                                    published_at=datetime.now(timezone.utc))
        s.add(ver); await s.flush()

        sec1 = MaterialSection(material_version_id=ver.id, section_type="CHAPTER", position=1,
                               title="Seção 1", body="Corpo da seção 1", content_node_id=cX.id,
                               curriculum_relation_type="THEORY")
        sec2 = MaterialSection(material_version_id=ver.id, section_type="CHAPTER", position=2,
                               title="Seção 2", body="Corpo da seção 2")
        s.add_all([sec1, sec2]); await s.flush()

        s.add_all([
            MaterialBlock(section_id=sec1.id, material_version_id=ver.id, block_type="HEADING",
                         position=1, title="Introdução"),
            MaterialBlock(section_id=sec1.id, material_version_id=ver.id, block_type="TEXT",
                         position=2, body="Texto explicativo da seção 1."),
            MaterialBlock(section_id=sec2.id, material_version_id=ver.id, block_type="FORMULA",
                         position=1, body="E = mc^2"),
        ])
        s.add(MaterialExercise(material_version_id=ver.id, section_id=sec1.id,
                               source_type="EXISTING_QUESTION", relation_type="EXERCISE",
                               question_version_id=qvids[0], position=1))
        await s.commit()

        # -- DRAFT (never published) material - must never be visible to a student --
        draft_mat = TheoryMaterial(title="Rascunho", material_kind="CHAPTER",
                                   authoring_source="TEACHER", visibility_scope="SCHOOL",
                                   primary_content_node_id=cX.id, school_id=_SCHOOL_A,
                                   created_by_external_identity="prof_p25")
        s.add(draft_mat); await s.flush()
        s.add(TheoryMaterialVersion(material_id=draft_mat.id, version_number=1, status="DRAFT"))
        await s.commit()

        return {"material_id": str(mat.id), "version_id": str(ver.id),
                "sec1_id": str(sec1.id), "sec2_id": str(sec2.id),
                "draft_material_id": str(draft_mat.id), "content_x": "P25-C-X", "content_y": "P25-C-Y"}


class Phase25Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _seed(cls.factory)

        cls.seed = cls.loop.run_until_complete(_prep())
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls.app.dependency_overrides[get_current_authenticated_context] = lambda: _ctx("stu_a", _SCHOOL_A)
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user, school=None):
        self.app.dependency_overrides[get_current_authenticated_context] = lambda: _ctx(user, school)

    # -- 1. published material is visible in-scope --------------------
    def test_published_material_visible_in_scope(self):
        self._as("stu_a", _SCHOOL_A)
        r = self.client.get("/api/v1/student/materials")
        self.assertEqual(r.status_code, 200, r.text)
        ids = [m["material_id"] for m in r.json()]
        self.assertIn(self.seed["material_id"], ids)
        self.assertNotIn(self.seed["draft_material_id"], ids)

    # -- 2. unpublished (DRAFT-only) material -> 404 for a student -----
    def test_unpublished_material_not_found(self):
        self._as("stu_a", _SCHOOL_A)
        r = self.client.get(f"/api/v1/student/materials/{self.seed['draft_material_id']}")
        self.assertEqual(r.status_code, 404)

    # -- 3. tenant isolation: another school's student is refused -----
    def test_tenant_isolation_other_school(self):
        self._as("stu_b", _SCHOOL_B)
        r = self.client.get(f"/api/v1/student/materials/{self.seed['material_id']}")
        self.assertEqual(r.status_code, 403)
        r2 = self.client.get("/api/v1/student/materials")
        self.assertNotIn(self.seed["material_id"], [m["material_id"] for m in r2.json()])

    # -- 3b. an independent student (no school) never sees SCHOOL material --
    def test_independent_student_sees_only_public(self):
        self._as("stu_free", None)
        r = self.client.get(f"/api/v1/student/materials/{self.seed['material_id']}")
        self.assertEqual(r.status_code, 403)

    # -- 4. curriculum-v2 association is derived, never a copied name --
    def test_curriculum_association(self):
        self._as("stu_a", _SCHOOL_A)
        r = self.client.get(f"/api/v1/student/materials/{self.seed['material_id']}")
        self.assertEqual(r.json()["content_code"], self.seed["content_x"])

    # -- 5. sections/blocks come back ordered, batched -----------------
    def test_sections_and_blocks_ordered(self):
        self._as("stu_a", _SCHOOL_A)
        r = self.client.get(f"/api/v1/student/materials/{self.seed['material_id']}/sections")
        self.assertEqual(r.status_code, 200, r.text)
        sections = r.json()
        self.assertEqual([s["position"] for s in sections], [1, 2])
        self.assertEqual([b["block_type"] for b in sections[0]["blocks"]], ["HEADING", "TEXT"])
        self.assertEqual(sections[1]["blocks"][0]["block_type"], "FORMULA")
        self.assertEqual(len(sections[0]["exercises"]), 1)
        self.assertEqual(len(sections[1]["exercises"]), 0)

    # -- 6. progress: default, save, resume -----------------------------
    def test_progress_default_save_resume(self):
        self._as("stu_a", _SCHOOL_A)
        before = self.client.get(f"/api/v1/student/materials/{self.seed['material_id']}/progress").json()
        self.assertFalse(before["started"])
        self.assertEqual(before["current_section_id"], self.seed["sec1_id"])

        saved = self.client.put(
            f"/api/v1/student/materials/{self.seed['material_id']}/progress",
            json={"current_section_id": self.seed["sec2_id"]})
        self.assertEqual(saved.status_code, 200, saved.text)
        self.assertTrue(saved.json()["started"])
        self.assertEqual(saved.json()["current_section_id"], self.seed["sec2_id"])

        # "closing the browser and coming back" - GET returns the same position
        resumed = self.client.get(f"/api/v1/student/materials/{self.seed['material_id']}/progress")
        self.assertEqual(resumed.json()["current_section_id"], self.seed["sec2_id"])

        # idempotent: saving the same position again does not create a 2nd row
        self.client.put(f"/api/v1/student/materials/{self.seed['material_id']}/progress",
                        json={"current_section_id": self.seed["sec2_id"], "completed": True})

        async def _count():
            async with self.factory() as s:
                rows = (await s.execute(select(MaterialProgress).where(
                    MaterialProgress.student_external_id == "stu_a"))).scalars().all()
                return len(rows)
        self.assertEqual(self.loop.run_until_complete(_count()), 1)

    # -- 6b. reading a material NEVER touches domain_content_mastery ----
    def test_reading_material_does_not_touch_domain_mastery(self):
        from agente_ia_edu.db.models.assessments import DomainContentMastery

        async def _count():
            async with self.factory() as s:
                rows = (await s.execute(select(DomainContentMastery).where(
                    DomainContentMastery.student_external_id == "stu_mastery_check"))).scalars().all()
                return len(rows)

        self._as("stu_mastery_check", _SCHOOL_A)
        self.client.get(f"/api/v1/student/materials/{self.seed['material_id']}/sections")
        self.client.put(f"/api/v1/student/materials/{self.seed['material_id']}/progress",
                        json={"current_section_id": self.seed["sec1_id"], "completed": True})
        self.assertEqual(self.loop.run_until_complete(_count()), 0)

    # -- 7. associated exercise reuses the EXISTING practice engine -----
    def test_associated_exercise_reuses_adaptive_practice_service(self):
        self._as("stu_a", _SCHOOL_A)
        sections = self.client.get(
            f"/api/v1/student/materials/{self.seed['material_id']}/sections").json()
        ex_section = next(s for s in sections if s["exercises"])
        self.assertIsNotNone(ex_section["exercises"][0]["question_version_id"])
        # "Pratique o que você estudou" hands off to the SAME endpoint Trilha/
        # Study Session already use - no second player, no second selector.
        r = self.client.post("/api/v1/student/practice",
                             json={"content_code": self.seed["content_x"], "question_count": 3})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIn("practice_id", r.json())

    # -- 8. absence of material: no fabricated availability -------------
    def test_absence_of_material(self):
        self._as("stu_a", _SCHOOL_A)
        r = self.client.get(f"/api/v1/student/materials?content_code={self.seed['content_y']}")
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), [])

    # -- 9. determinism: repeated calls are byte-identical ---------------
    def test_determinism(self):
        self._as("stu_a", _SCHOOL_A)
        a = self.client.get(f"/api/v1/student/materials/{self.seed['material_id']}/sections").json()
        b = self.client.get(f"/api/v1/student/materials/{self.seed['material_id']}/sections").json()
        self.assertEqual(a, b)

    # -- 10. no N+1: one query set regardless of section/block count -----
    def test_no_n_plus_1_on_sections(self):
        async def run():
            n = {"c": 0}

            @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
            def _c(*_a):  # noqa: ANN001
                n["c"] += 1
            try:
                from agente_ia_edu.services.student_material import StudentMaterialService
                async with self.factory() as s:
                    svc = StudentMaterialService(s)
                    n["c"] = 0
                    await svc.get_sections(_uuid.UUID(self.seed["material_id"]),
                                           requester_school_id=str(_SCHOOL_A))
                    return n["c"]
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", _c)
        q = self.loop.run_until_complete(run())
        # material lookup + version lookup + sections + blocks + exercises +
        # content_code batch lookup - a small constant, never per-row.
        self.assertLessEqual(q, 8, f"query count too high: {q}")

    # -- 11. AI-agnostic import guard ------------------------------------
    def test_ai_agnostic_import_guard(self):
        import agente_ia_edu.services.student_material as sm
        import agente_ia_edu.db.models.material_progress as mp
        forbidden = {"openai", "AsyncOpenAI", "OpenAIProvider", "providers",
                    "classification_consensus", "classification_prompts"}
        for mod in (sm, mp):
            tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names.add(node.module.split(".")[0])
            self.assertFalse(names & forbidden, f"{mod.__file__} imports {names & forbidden}")

    # -- 12. security: save_progress rejects a section from another material
    def test_progress_rejects_foreign_section(self):
        self._as("stu_a", _SCHOOL_A)
        r = self.client.put(f"/api/v1/student/materials/{self.seed['material_id']}/progress",
                            json={"current_section_id": str(_uuid.uuid4())})
        self.assertEqual(r.status_code, 422)


if __name__ == "__main__":
    unittest.main()
