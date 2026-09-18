"""GET /api/v1/questions builds its tenant WHERE clause from context.role/
context.school_id: STUDENT -> PUBLIC only, any role with a real school_id ->
own school or PUBLIC. A TEACHER/COORDINATOR/DIRECTOR role is reachable with
school_id=None too (AuthorizationService.resolve_context's role_hint
fallback, when no active UserSchoolLink exists yet) - neither branch matched
that combination, so the WHERE clause got no tenant filter at all. Individual
rows still came back filtered by can_view_question, so no question content
leaked - but pagination.total/total_pages reflected the unfiltered,
cross-tenant count. Found auditing question_governance.py/questions.py for
the R0 Fase 3C authorization pattern (2026-09-18 session).
"""

import asyncio
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.questions import get_question_service, router as questions_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import Question, QuestionVersion, School
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.repositories.questions import QuestionRepository
from agente_ia_edu.services.questions import QuestionService


class QuestionListTenantCountHTTPTests(unittest.TestCase):
    def setUp(self):
        async def setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                school_a = School(code="QCNT-A", name="Escola Contagem A")
                school_b = School(code="QCNT-B", name="Escola Contagem B")
                session.add_all([school_a, school_b])
                await session.flush()
                public_question = Question(origin_type="PLATFORM", status="PUBLISHED", visibility_scope="PUBLIC",
                                            validation_status="approved")
                session.add_all([
                    public_question,
                    Question(origin_type="SCHOOL", status="PUBLISHED", visibility_scope="PRIVATE",
                              validation_status="approved", school_id=school_a.id),
                    Question(origin_type="SCHOOL", status="PUBLISHED", visibility_scope="PRIVATE",
                              validation_status="approved", school_id=school_b.id),
                ])
                await session.flush()
                session.add(QuestionVersion(
                    question_id=public_question.id, version_kind="official_original",
                    canonical_text="Enunciado publico.", statement="Enunciado publico.",
                    content_hash="h-tenant-count-public",
                ))
                await session.commit()
                return engine, factory

        self.engine, self.session_factory = asyncio.run(setup())
        self.identity = ExternalIdentityContext(
            # No UserSchoolLink seeded for this external_user_id at all - the
            # identity that reaches AuthorizationService.resolve_context's
            # role_hint fallback, landing on role="TEACHER", school_id=None.
            provider="test", external_user_id="teacher-no-school-link", roles=("teacher",),
        )
        app = FastAPI()
        app.include_router(questions_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory

        async def _fake_question_service():
            async with self.session_factory() as session:
                yield QuestionService(QuestionRepository(session))

        app.dependency_overrides[get_question_service] = _fake_question_service
        app.dependency_overrides[get_current_identity] = lambda: self.identity
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    def test_school_less_teacher_sees_only_public_count(self):
        resp = self.client.get("/api/v1/questions")
        self.assertEqual(resp.status_code, 200, resp.text)
        pagination = resp.json()["pagination"]
        # Only the PUBLIC question belongs in the count - the two PRIVATE,
        # school-scoped questions (neither school is this teacher's own,
        # since they have none) must not be counted.
        self.assertEqual(pagination["total"], 1)
        self.assertEqual(len(resp.json()["items"]), 1)


if __name__ == "__main__":
    unittest.main()
