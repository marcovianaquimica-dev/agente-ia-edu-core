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
    PromptAssignment,
    School,
    Student,
    UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class EssayCorrectionsRoutesTests(unittest.TestCase):
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

    def _seed_pending_correction(
        self, code: str, *, school_id: uuid.UUID | None = None,
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """Seeds one PENDING_REVIEW correction. Pass an existing ``school_id``
        to add a second correction to a school an earlier call already
        created (needed for tests that must prove two corrections in the
        SAME school behave independently, as opposed to one being rejected
        merely for belonging to a different school)."""
        async def _seed():
            async with self.factory() as session:
                target_school_id = school_id
                if target_school_id is None:
                    school = School(id=uuid.uuid4(), code=f"REVR-{code}", name=f"school-{code}")
                    session.add(school)
                    await session.flush()
                    session.add(UserSchoolLink(
                        external_user_id=f"teacher_{code}", school_id=school.id, role="TEACHER",
                        scope_type="SCHOOL", active=True,
                    ))
                    target_school_id = school.id
                student = Student(
                    id=uuid.uuid4(), school_id=target_school_id, person_id=uuid.uuid4(),
                    student_code=f"ST-{code}",
                )
                session.add(student)
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=target_school_id, title="Tema", statement="Disserte.",
                    year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
                )
                session.add(prompt)
                await session.flush()
                assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=target_school_id, essay_prompt_id=prompt.id,
                    class_id=uuid.uuid4(), assigned_by_external_identity="teacher:t",
                )
                session.add(assignment)
                await session.flush()
                submission = EssaySubmission(
                    id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=target_school_id,
                    prompt_assignment_id=assignment.id, student_id=student.id,
                    mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                    canonical_text="Redacao.", normalized_text_hash="a" * 64,
                    # NOTE (fixture gap fixed here): the brief's own Step-1 code
                    # omitted submitted_at. ck_essay_submissions_submitted_at_presence
                    # requires it whenever status='SUBMITTED' - mirrored from the
                    # real sibling fixtures in test_r3_essay_correction_model.py /
                    # test_r3_essay_correction_service.py, which already seed it.
                    submitted_at=datetime.now(timezone.utc),
                )
                session.add(submission)
                await session.flush()
                correction = EssayCorrection(
                    id=uuid.uuid4(), school_id=target_school_id, essay_submission_id=submission.id,
                    correction_key="k" * 64, rubric_version="ENEM_2025", model_version="gpt-test",
                    prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                    ai_output={"scores": {"per_competency": {}, "total": 600}},
                    final_scores={
                        "per_competency": {
                            "C1": {"points": 120, "confidence": 0.8}, "C2": {"points": 120, "confidence": 0.8},
                            "C3": {"points": 120, "confidence": 0.8}, "C4": {"points": 120, "confidence": 0.8},
                            "C5": {"points": 120, "confidence": 0.8},
                        },
                        "total": 600,
                    },
                    final_feedback={"strengths": [], "improvements": [], "next_essay_strategy": "..."},
                    status="PENDING_REVIEW",
                )
                session.add(correction)
                await session.commit()
                return correction.id, target_school_id

        return self.loop.run_until_complete(_seed())

    def test_list_returns_pending_reviews_for_own_school(self):
        correction_id, _school_id = self._seed_pending_correction("1")
        self._as("teacher_1")
        resp = self.client.get("/api/v1/teacher/essay-corrections")
        self.assertEqual(resp.status_code, 200, resp.text)
        ids = [row["id"] for row in resp.json()]
        self.assertEqual(ids, [str(correction_id)])

    def test_approve_publishes(self):
        correction_id, _school_id = self._seed_pending_correction("2")
        self._as("teacher_2")
        resp = self.client.post(f"/api/v1/teacher/essay-corrections/{correction_id}/approve", json={})
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "APPROVED")
        self.assertEqual(body["reviewed_by_external_identity"], "teacher_2")

    def test_approve_with_score_edit(self):
        correction_id, _school_id = self._seed_pending_correction("3")
        self._as("teacher_3")
        new_scores = {
            "per_competency": {
                "C1": {"points": 200, "confidence": 1.0}, "C2": {"points": 200, "confidence": 1.0},
                "C3": {"points": 200, "confidence": 1.0}, "C4": {"points": 200, "confidence": 1.0},
                "C5": {"points": 200, "confidence": 1.0},
            },
            "total": 1000,
        }
        resp = self.client.post(
            f"/api/v1/teacher/essay-corrections/{correction_id}/approve",
            json={"final_scores": new_scores},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["final_scores"]["total"], 1000)

    def test_reject(self):
        correction_id, _school_id = self._seed_pending_correction("4")
        self._as("teacher_4")
        resp = self.client.post(f"/api/v1/teacher/essay-corrections/{correction_id}/reject")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "REJECTED")

    def test_a_correction_from_another_school_is_403(self):
        correction_id, _school_id = self._seed_pending_correction("5")
        self._seed_pending_correction("6")
        self._as("teacher_6")
        resp = self.client.post(f"/api/v1/teacher/essay-corrections/{correction_id}/reject")
        self.assertEqual(resp.status_code, 403)

    def test_a_student_cannot_approve(self):
        correction_id, school_id = self._seed_pending_correction("7")

        async def _add_student_link():
            async with self.factory() as session:
                session.add(UserSchoolLink(
                    external_user_id="student_7", school_id=school_id, role="STUDENT",
                    scope_type="SCHOOL", active=True,
                ))
                await session.commit()

        self.loop.run_until_complete(_add_student_link())
        self._as("student_7")
        resp = self.client.post(f"/api/v1/teacher/essay-corrections/{correction_id}/approve", json={})
        self.assertEqual(resp.status_code, 403)

    def test_bulk_approve_is_best_effort(self):
        ok_id, school_id = self._seed_pending_correction("8")
        already_rejected_id, _same_school_id = self._seed_pending_correction("9", school_id=school_id)

        async def _reject_one():
            async with self.factory() as session:
                from agente_ia_edu.services.essay_correction import EssayCorrectionService
                await EssayCorrectionService(session).reject(
                    already_rejected_id, reviewed_by_external_identity="teacher:other"
                )
                await session.commit()

        self.loop.run_until_complete(_reject_one())
        self._as("teacher_8")
        resp = self.client.post(
            "/api/v1/teacher/essay-corrections/bulk-approve",
            json={"essay_correction_ids": [str(ok_id), str(already_rejected_id)]},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual([row["id"] for row in body["approved"]], [str(ok_id)])
        self.assertIn(str(already_rejected_id), body["failures"])

    def test_bulk_approve_rejects_an_id_from_another_school(self):
        ok_id, school_id = self._seed_pending_correction("13")
        other_id, _other_school_id = self._seed_pending_correction("14")
        self._as("teacher_13")
        resp = self.client.post(
            "/api/v1/teacher/essay-corrections/bulk-approve",
            json={"essay_correction_ids": [str(ok_id), str(other_id)]},
        )
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
