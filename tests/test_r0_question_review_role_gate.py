"""POST /api/v1/questions/{id}/review let the owning TEACHER approve/reject/
archive their own question's validation_status - the same conceptual action
that the sibling POST /{id}/status route correctly restricts to
DIRECTOR/COORDINATOR/PLATFORM_ADMIN via can_transition_status. Both routes
gated through _load_question_for_manage's can_manage_question, which is
ownership-based (correct for "submit", wrong for approve/reject/archive),
so the owning author could self-approve, bypassing the four-eyes review
guarantee. Found auditing question_governance.py/questions.py for the R0
Fase 3C authorization pattern (2026-09-18 session).
"""

import asyncio
import unittest

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.questions import router as questions_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School, UserSchoolLink
from agente_ia_edu.identity import ExternalIdentityContext


class QuestionReviewRoleGateHTTPTests(unittest.TestCase):
    def setUp(self):
        async def setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                school = School(code="QREV1", name="Escola Review Gate")
                session.add(school)
                await session.flush()
                session.add_all([
                    UserSchoolLink(
                        external_user_id="teacher-owner-1", school_id=school.id, role="TEACHER",
                        scope_type="SCHOOL", active=True,
                    ),
                    UserSchoolLink(
                        external_user_id="director-1", school_id=school.id, role="DIRECTOR",
                        scope_type="SCHOOL", active=True,
                    ),
                ])
                await session.commit()
                return engine, factory

        self.engine, self.session_factory = asyncio.run(setup())
        self.identity = {"value": ExternalIdentityContext(
            provider="test", external_user_id="teacher-owner-1", roles=("teacher",),
        )}
        app = FastAPI()
        app.include_router(questions_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    def _as(self, external_user_id: str):
        self.identity["value"] = ExternalIdentityContext(
            provider="test", external_user_id=external_user_id, roles=("teacher",),
        )

    def _create_and_submit_question(self) -> str:
        self._as("teacher-owner-1")
        create_resp = self.client.post(
            "/api/v1/questions",
            json={
                "statement": "Qual e a capital da Franca?",
                "options": ["Paris", "Londres"],
                "correct_option": "Paris",
            },
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        question_id = create_resp.json()["question_id"]

        submit_resp = self.client.post(
            f"/api/v1/questions/{question_id}/review",
            json={"action": "submit"},
        )
        self.assertEqual(submit_resp.status_code, 200, submit_resp.text)
        return question_id

    def test_owning_teacher_cannot_self_approve(self):
        question_id = self._create_and_submit_question()
        # Still authenticated as teacher-owner-1 (the question's own author).
        resp = self.client.post(
            f"/api/v1/questions/{question_id}/review",
            json={"action": "approve"},
        )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_owning_teacher_cannot_self_reject(self):
        question_id = self._create_and_submit_question()
        resp = self.client.post(
            f"/api/v1/questions/{question_id}/review",
            json={"action": "reject", "reason": "not good enough"},
        )
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_director_can_approve(self):
        question_id = self._create_and_submit_question()
        self._as("director-1")
        resp = self.client.post(
            f"/api/v1/questions/{question_id}/review",
            json={"action": "approve"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "APPROVED")

    def test_owning_teacher_can_still_submit_their_own_draft(self):
        # "submit" stays ownership-gated, not role-gated - a teacher must
        # still be able to submit their own draft for review.
        question_id = self._create_and_submit_question()
        self.assertTrue(question_id)


if __name__ == "__main__":
    unittest.main()
