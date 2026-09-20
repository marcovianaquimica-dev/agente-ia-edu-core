"""GET /api/v1/question-bank/questions/{id} gated on
item.classification.content_code - the RESOLVED CONTENT-level ancestor code
(QuestionBankService._resolve_curriculum_path walks a SUBCONTENT code up to
its parent CONTENT code), not the raw value list_questions' SQL gate compares
(pc.content directly, which can BE a SUBCONTENT code). A universe scoped to a
CONTENT node with include_descendants=False permits that CONTENT code but not
its SUBCONTENT children, so a SUBCONTENT-classified question - correctly
hidden by the list endpoint - was still reachable one id at a time through
the detail endpoint. Found auditing the whole Fase 3C diff (2026-09-20).
"""

import asyncio
import unittest
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.question_bank import question_bank_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    BookletQuestion,
    CatalogNode,
    Exam,
    ExamApplication,
    ExamBooklet,
    Institution,
    PedagogicalClassification,
    Question,
    QuestionOption,
    QuestionVersion,
    School,
    UserSchoolLink,
)
from agente_ia_edu.db.models.pedagogical_universe import (
    PedagogicalUniverse,
    PedagogicalUniverseCatalogScope,
)
from agente_ia_edu.identity import ExternalIdentityContext


class QuestionBankSubcontentGateHTTPTests(unittest.TestCase):
    def setUp(self):
        async def setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                school = School(code="SUBGATE1", name="Escola Subgate")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="teacher-subgate", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))

                # DISCIPLINE > AREA > CONTENT > SUBCONTENT
                discipline = CatalogNode(code="BIO", name="Biologia", node_type="DISCIPLINE", active=True)
                session.add(discipline)
                await session.flush()
                discipline.root_id = discipline.id
                area = CatalogNode(code="BIO-A", name="Area", node_type="AREA",
                                    parent_id=discipline.id, root_id=discipline.id, active=True)
                session.add(area)
                await session.flush()
                content = CatalogNode(code="BIO-A-C", name="Content", node_type="CONTENT",
                                        parent_id=area.id, root_id=discipline.id, active=True)
                session.add(content)
                await session.flush()
                subcontent = CatalogNode(code="BIO-A-C-SUB", name="Subcontent", node_type="SUBCONTENT",
                                           parent_id=content.id, root_id=discipline.id, active=True)
                session.add(subcontent)
                await session.flush()

                # Universe scoped to the CONTENT node, descendants NOT included -
                # the SUBCONTENT child is deliberately out of scope.
                universe = PedagogicalUniverse(
                    id=uuid.uuid4(), external_id="u-subgate", slug="u-subgate", name="u-subgate",
                    owner_type="SCHOOL", owner_external_id=str(school.id), status="ACTIVE",
                )
                session.add(universe)
                await session.flush()
                session.add(PedagogicalUniverseCatalogScope(
                    id=uuid.uuid4(), universe_id=universe.id,
                    catalog_node_id=content.id, scope_kind="CONTENT",
                    include_descendants=False,
                ))

                inst = Institution(code="INEP-SUB", name="INEP")
                session.add(inst)
                await session.flush()
                exam = Exam(institution_id=inst.id, code="ENEM-SUB", name="ENEM")
                session.add(exam)
                await session.flush()
                app = ExamApplication(exam_id=exam.id, year=2025, application_type="regular", day=2)
                session.add(app)
                await session.flush()
                booklet = ExamBooklet(exam_application_id=app.id, code="D2", color="AZUL")
                session.add(booklet)
                await session.flush()

                question = Question(validation_status="validated", origin_type="IMPORTED",
                                     status="PUBLISHED", visibility_scope="PUBLIC")
                session.add(question)
                await session.flush()
                version = QuestionVersion(
                    question_id=question.id, version_kind="official_original",
                    canonical_text="Enunciado subcontent.", statement="Enunciado subcontent.",
                    content_hash="h-subgate-1", is_immutable=True, recommended_difficulty="MEDIUM",
                )
                session.add(version)
                await session.flush()
                for pos, key in enumerate("ABCDE", start=1):
                    session.add(QuestionOption(
                        question_version_id=version.id, option_key=key, position=pos,
                        text=f"Alternativa {key}", is_valid_option=(key == "C"),
                    ))
                session.add(BookletQuestion(
                    exam_booklet_id=booklet.id, question_version_id=version.id,
                    position=1, official_number=1, page_number=1,
                ))
                await session.flush()
                # Classified at the SUBCONTENT code - out of the universe's scope.
                session.add(PedagogicalClassification(
                    question_version_id=version.id, discipline="CURRICULUM_PROPOSAL",
                    content="BIO-A-C-SUB", subcontent="BIO-A-C-SUB", difficulty="UNKNOWN",
                    reasoning_type="UNSPECIFIED", prerequisites=[], keywords=[],
                    competencies=[], skills=[], status="CLASSIFIED", source="rule",
                    lifecycle="ACTIVE",
                    metadata_={"taxonomy_version": "curriculum-v2",
                               "primary_content_code": "BIO-A-C-SUB"},
                ))
                await session.commit()
                return engine, factory, question.id

        self.engine, self.session_factory, self.question_id = asyncio.run(setup())
        self.identity = ExternalIdentityContext(
            provider="test", external_user_id="teacher-subgate", roles=("teacher",),
        )
        app = FastAPI()
        app.include_router(question_bank_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_identity] = lambda: self.identity
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    def test_list_excludes_the_subcontent_question(self):
        resp = self.client.get("/api/v1/question-bank/questions")
        self.assertEqual(resp.status_code, 200, resp.text)
        ids = {item["id"] for item in resp.json()["items"]}
        self.assertNotIn(str(self.question_id), ids)

    def test_detail_also_excludes_the_subcontent_question(self):
        # This is the actual regression: pre-fix, the list correctly hid the
        # question but the detail endpoint - gated on the wrong (resolved
        # CONTENT-ancestor) code - returned it anyway.
        resp = self.client.get(f"/api/v1/question-bank/questions/{self.question_id}")
        self.assertEqual(resp.status_code, 404, resp.text)


if __name__ == "__main__":
    unittest.main()
