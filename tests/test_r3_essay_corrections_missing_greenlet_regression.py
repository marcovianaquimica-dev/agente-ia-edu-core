"""Regression test: approve_essay_correction, reject_essay_correction,
retry_essay_correction, and bulk_approve_essay_corrections used to read ORM
attributes (via _correction_to_response) AFTER session.commit(). In
production the app's real session uses expire_on_commit=True (see
db/session.py), so that post-commit access triggers a synchronous lazy-load
and raises MissingGreenlet under the async engine - a 500 on every teacher
correction-review action. Every other test file for these routes (e.g.
test_r3_essay_corrections_routes.py) builds its session_factory with
expire_on_commit=False, which silently masks this. This file is the only
one that mirrors the production session configuration."""

import asyncio
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

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


async def _fake_retry(self, essay_correction_id):
    """Stands in for EssayCorrectionService.retry's real AI call - same
    reasoning and patch-target shape as _fake_retry in
    test_r3_essay_corrections_routes.py: without this patch, the route's
    plain EssayCorrectionService(session) would build a real provider via
    build_text_provider() and raise ProviderConfigurationError for missing
    OPENAI_API_KEY/OPENAI_MODEL. This file's own point is the response-
    building-before-commit fix, not the correction engine itself."""
    correction = await self.session.get(EssayCorrection, essay_correction_id)
    correction.status = "PENDING_REVIEW"
    correction.correction_key = correction.correction_key or "k" * 64
    correction.model_version = correction.model_version or "gpt-test"
    correction.ai_output = {"scores": {"per_competency": {}, "total": 600}}
    correction.final_scores = {
        "per_competency": {
            "C1": {"points": 120, "confidence": 0.8}, "C2": {"points": 120, "confidence": 0.8},
            "C3": {"points": 120, "confidence": 0.8}, "C4": {"points": 120, "confidence": 0.8},
            "C5": {"points": 120, "confidence": 0.8},
        },
        "total": 600,
    }
    correction.final_feedback = {"strengths": [], "improvements": [], "next_essay_strategy": "..."}
    correction.failure_reason = None
    await self.session.flush()
    return correction


class EssayCorrectionsRoutesExpireOnCommitTests(unittest.TestCase):
    """Same route exercise as test_r3_essay_corrections_routes.py, but with
    expire_on_commit=True to match production and actually catch the
    MissingGreenlet regression that expire_on_commit=False fixtures hide."""

    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=True)

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

    def _seed_correction(
        self, code: str, *, school_id: uuid.UUID | None = None, status: str = "PENDING_REVIEW",
    ) -> tuple[uuid.UUID, uuid.UUID]:
        """Seeds one correction (PENDING_REVIEW by default; pass
        status="NEEDS_REVIEW" for the /retry tests). Same shape as
        test_r3_essay_corrections_routes.py's _seed_pending_correction, but
        with every id captured into a plain local variable BEFORE commit -
        expire_on_commit=True expires client-side-assigned ids too, and
        reading correction.id after commit would itself raise
        MissingGreenlet, unrelated to what these tests actually exercise."""
        async def _seed():
            async with self.factory() as session:
                target_school_id = school_id
                if target_school_id is None:
                    school = School(id=uuid.uuid4(), code=f"CREOC-{code}", name=f"school-{code}")
                    session.add(school)
                    await session.flush()
                    session.add(UserSchoolLink(
                        external_user_id=f"teacher_eoc_{code}", school_id=school.id, role="TEACHER",
                        scope_type="SCHOOL", active=True,
                    ))
                    target_school_id = school.id
                person = Person(id=uuid.uuid4(), school_id=target_school_id, full_name=f"Aluno {code}")
                session.add(person)
                await session.flush()
                student = Student(
                    id=uuid.uuid4(), school_id=target_school_id, person_id=person.id,
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
                    submitted_at=datetime.now(timezone.utc),
                )
                session.add(submission)
                await session.flush()
                if status == "NEEDS_REVIEW":
                    correction = EssayCorrection(
                        id=uuid.uuid4(), school_id=target_school_id, essay_submission_id=submission.id,
                        correction_key=None, rubric_version="ENEM_2025", model_version=None,
                        prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                        ai_output=None, final_scores=None, final_feedback=None,
                        failure_reason="ProviderConfigurationError: stub seed failure",
                        status="NEEDS_REVIEW",
                    )
                else:
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
                        status=status,
                    )
                session.add(correction)
                await session.flush()
                correction_id, resolved_school_id = correction.id, target_school_id
                await session.commit()
                return correction_id, resolved_school_id

        return self.loop.run_until_complete(_seed())

    def test_approve_does_not_500_under_expire_on_commit(self):
        correction_id, _school_id = self._seed_correction("1")
        self._as("teacher_eoc_1")

        resp = self.client.post(
            f"/api/v1/teacher/essay-corrections/{correction_id}/approve", json={}
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "APPROVED")
        self.assertEqual(body["reviewed_by_external_identity"], "teacher_eoc_1")

    def test_reject_does_not_500_under_expire_on_commit(self):
        correction_id, _school_id = self._seed_correction("2")
        self._as("teacher_eoc_2")

        resp = self.client.post(f"/api/v1/teacher/essay-corrections/{correction_id}/reject")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "REJECTED")

    def test_retry_does_not_500_under_expire_on_commit(self):
        correction_id, _school_id = self._seed_correction("3", status="NEEDS_REVIEW")
        self._as("teacher_eoc_3")

        with patch(
            "agente_ia_edu.services.essay_correction.EssayCorrectionService.retry",
            new=_fake_retry,
        ):
            resp = self.client.post(f"/api/v1/teacher/essay-corrections/{correction_id}/retry")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "PENDING_REVIEW")

    def test_bulk_approve_does_not_500_under_expire_on_commit(self):
        first_id, school_id = self._seed_correction("4")
        second_id, _same_school_id = self._seed_correction("5", school_id=school_id)
        self._as("teacher_eoc_4")

        resp = self.client.post(
            "/api/v1/teacher/essay-corrections/bulk-approve",
            json={"essay_correction_ids": [str(first_id), str(second_id)]},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        approved_ids = {row["id"] for row in body["approved"]}
        self.assertEqual(approved_ids, {str(first_id), str(second_id)})
        self.assertEqual(body["failures"], {})


if __name__ == "__main__":
    unittest.main()
