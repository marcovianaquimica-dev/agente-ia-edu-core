import asyncio
import unittest
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    EssayCorrection,
    EssayPrompt,
    EssaySubmission,
    Person,
    PromptAssignment,
    School,
    Student,
    UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class TeacherEssayCorrectionsEnrichedListTests(unittest.TestCase):
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
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _seed_pending_correction(self, code: str):
        async def _seed_async():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"ENR-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id=f"teacher_{code}", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                person = Person(id=uuid.uuid4(), school_id=school.id, full_name="Ana Beatriz")
                session.add(person)
                await session.flush()
                student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id, student_code=f"ST-{code}")
                session.add(student)
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Desafios da mobilidade urbana",
                    statement="Disserte.", year=2026, status="ACTIVE",
                    created_by_external_identity="teacher:t",
                )
                session.add(prompt)
                await session.flush()
                assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                    class_id=uuid.uuid4(), assigned_by_external_identity="teacher:t",
                )
                session.add(assignment)
                await session.flush()
                submitted_at = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
                submission = EssaySubmission(
                    id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                    prompt_assignment_id=assignment.id, student_id=student.id,
                    mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                    canonical_text="Redacao.", normalized_text_hash="a" * 64,
                    submitted_at=submitted_at,
                )
                session.add(submission)
                await session.flush()
                correction = EssayCorrection(
                    id=uuid.uuid4(), school_id=school.id, essay_submission_id=submission.id,
                    correction_key="k" * 64, rubric_version="ENEM_2025", model_version="gpt-test",
                    prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                    ai_output={"scores": None}, status="PENDING_REVIEW",
                )
                session.add(correction)
                await session.commit()
                return correction.id

        return self.loop.run_until_complete(_seed_async())

    def test_list_includes_student_name_prompt_title_and_submitted_at(self):
        self._seed_pending_correction("1")
        self._as("teacher_1")
        resp = self.client.get("/api/v1/teacher/essay-corrections")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["student_name"], "Ana Beatriz")
        self.assertEqual(body[0]["prompt_title"], "Desafios da mobilidade urbana")
        # Parse rather than compare the raw string: Pydantic's exact ISO
        # serialization format (trailing "Z" vs "+00:00") is not worth
        # pinning down here, only that the timestamp round-trips correctly.
        # Matches the fixed instant _seed_pending_correction hardcodes.
        returned = datetime.fromisoformat(body[0]["submitted_at"].replace("Z", "+00:00"))
        self.assertEqual(returned, datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc))

    def test_approve_still_works_with_the_new_optional_fields(self):
        correction_id = self._seed_pending_correction("2")
        self._as("teacher_2")
        resp = self.client.post(f"/api/v1/teacher/essay-corrections/{correction_id}/approve", json={})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "APPROVED")
        self.assertIsNone(resp.json()["student_name"])


if __name__ == "__main__":
    unittest.main()
