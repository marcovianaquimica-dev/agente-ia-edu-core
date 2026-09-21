# tests/test_r3_essay_correction_review.py
import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from sqlalchemy import select

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    AdminAuditLog,
    Class,
    EssayCorrection,
    EssayPrompt,
    EssaySubmission,
    GradeLevel,
    PromptAssignment,
    School,
    Segment,
    Student,
)
from agente_ia_edu.services.essay_correction import EssayCorrectionService


class EssayCorrectionReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _pending_correction(self, session, code) -> tuple[uuid.UUID, uuid.UUID]:
        school = School(id=uuid.uuid4(), code=f"REV-{code}", name=f"school-{code}")
        session.add(school)
        await session.flush()
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
        student = Student(id=uuid.uuid4(), school_id=school.id, person_id=uuid.uuid4(), student_code=f"ST-{code}")
        session.add(student)
        await session.flush()
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
        )
        session.add(prompt)
        await session.flush()
        assignment = PromptAssignment(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, assigned_by_external_identity="teacher:t",
        )
        session.add(assignment)
        await session.flush()
        submission = EssaySubmission(
            id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
            prompt_assignment_id=assignment.id, student_id=student.id,
            mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
            canonical_text="Redacao.", normalized_text_hash="a" * 64,
            submitted_at=datetime.now(timezone.utc),
        )
        session.add(submission)
        await session.flush()
        correction = EssayCorrection(
            id=uuid.uuid4(), school_id=school.id, essay_submission_id=submission.id,
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
        return correction.id, school.id

    async def test_approve_without_edits_publishes(self):
        async with self.session_factory() as session:
            correction_id, _school_id = await self._pending_correction(session, "1")
            service = EssayCorrectionService(session)
            approved = await service.approve(
                correction_id, reviewed_by_external_identity="teacher:maria"
            )
            self.assertEqual(approved.status, "APPROVED")
            self.assertEqual(approved.reviewed_by_external_identity, "teacher:maria")
            self.assertIsNotNone(approved.reviewed_at)
            self.assertIsNotNone(approved.published_at)

    async def test_approve_with_score_edit_overwrites_final_scores(self):
        async with self.session_factory() as session:
            correction_id, _school_id = await self._pending_correction(session, "2")
            service = EssayCorrectionService(session)
            new_scores = {
                "per_competency": {
                    "C1": {"points": 160, "confidence": 1.0}, "C2": {"points": 160, "confidence": 1.0},
                    "C3": {"points": 160, "confidence": 1.0}, "C4": {"points": 160, "confidence": 1.0},
                    "C5": {"points": 160, "confidence": 1.0},
                },
                "total": 800,
            }
            approved = await service.approve(
                correction_id, reviewed_by_external_identity="teacher:maria", final_scores=new_scores,
            )
            self.assertEqual(approved.final_scores["total"], 800)

    async def test_approve_rejects_an_invalid_score_edit(self):
        async with self.session_factory() as session:
            correction_id, _school_id = await self._pending_correction(session, "3")
            service = EssayCorrectionService(session)
            with self.assertRaises(ValueError):
                await service.approve(
                    correction_id, reviewed_by_external_identity="teacher:maria",
                    final_scores={"per_competency": {}, "total": 999},
                )

    async def test_approve_rejects_a_correction_that_is_not_pending(self):
        async with self.session_factory() as session:
            correction_id, _school_id = await self._pending_correction(session, "4")
            service = EssayCorrectionService(session)
            await service.approve(correction_id, reviewed_by_external_identity="teacher:maria")
            with self.assertRaises(ValueError):
                await service.approve(correction_id, reviewed_by_external_identity="teacher:maria")

    async def test_reject_marks_terminal_without_publishing(self):
        async with self.session_factory() as session:
            correction_id, _school_id = await self._pending_correction(session, "5")
            service = EssayCorrectionService(session)
            rejected = await service.reject(
                correction_id, reviewed_by_external_identity="teacher:maria"
            )
            self.assertEqual(rejected.status, "REJECTED")
            self.assertIsNotNone(rejected.reviewed_at)
            self.assertIsNone(rejected.published_at)

    async def test_list_by_status_scopes_to_school_and_status(self):
        async with self.session_factory() as session:
            correction_id, school_id = await self._pending_correction(session, "6")
            other_id, other_school_id = await self._pending_correction(session, "7")
            service = EssayCorrectionService(session)

            pending = await service.list_by_status(school_id, status="PENDING_REVIEW")
            self.assertEqual([c.id for c in pending], [correction_id])

            await service.approve(correction_id, reviewed_by_external_identity="teacher:maria")
            pending_after = await service.list_by_status(school_id, status="PENDING_REVIEW")
            self.assertEqual(pending_after, [])
            approved_after = await service.list_by_status(school_id, status="APPROVED")
            self.assertEqual([c.id for c in approved_after], [correction_id])

    async def test_approve_writes_an_audit_log_entry(self):
        async with self.session_factory() as session:
            correction_id, school_id = await self._pending_correction(session, "8")
            service = EssayCorrectionService(session)
            await service.approve(correction_id, reviewed_by_external_identity="teacher:maria")

            log = await session.scalar(
                select(AdminAuditLog).where(
                    AdminAuditLog.entity_type == "ESSAY_CORRECTION",
                    AdminAuditLog.entity_id == str(correction_id),
                )
            )
            self.assertIsNotNone(log)
            self.assertEqual(log.action, "ESSAY_CORRECTION_APPROVED")
            self.assertEqual(log.school_id, school_id)
            self.assertEqual(log.performed_by_external_id, "teacher:maria")

    async def test_approve_with_edit_records_before_after_in_the_audit_log(self):
        async with self.session_factory() as session:
            correction_id, _school_id = await self._pending_correction(session, "9")
            service = EssayCorrectionService(session)
            new_scores = {
                "per_competency": {
                    "C1": {"points": 200, "confidence": 1.0}, "C2": {"points": 200, "confidence": 1.0},
                    "C3": {"points": 200, "confidence": 1.0}, "C4": {"points": 200, "confidence": 1.0},
                    "C5": {"points": 200, "confidence": 1.0},
                },
                "total": 1000,
            }
            await service.approve(
                correction_id, reviewed_by_external_identity="teacher:maria", final_scores=new_scores,
            )

            log = await session.scalar(
                select(AdminAuditLog).where(
                    AdminAuditLog.entity_type == "ESSAY_CORRECTION",
                    AdminAuditLog.entity_id == str(correction_id),
                )
            )
            self.assertIn("final_scores", log.metadata_)
            self.assertEqual(log.metadata_["final_scores"]["after"]["total"], 1000)
            self.assertEqual(log.metadata_["final_scores"]["before"]["total"], 600)

    async def test_approve_with_feedback_edit_overwrites_final_feedback(self):
        async with self.session_factory() as session:
            correction_id, _school_id = await self._pending_correction(session, "13")
            service = EssayCorrectionService(session)
            new_feedback = {
                "strengths": ["Boa argumentacao"],
                "improvements": ["Revisar coesao"],
                "next_essay_strategy": "Praticar conectivos.",
            }
            approved = await service.approve(
                correction_id, reviewed_by_external_identity="teacher:maria",
                final_feedback=new_feedback,
            )
            self.assertEqual(approved.final_feedback, new_feedback)

    async def test_approve_rejects_an_invalid_feedback_edit(self):
        async with self.session_factory() as session:
            correction_id, _school_id = await self._pending_correction(session, "14")
            service = EssayCorrectionService(session)
            with self.assertRaises(ValueError):
                await service.approve(
                    correction_id, reviewed_by_external_identity="teacher:maria",
                    final_feedback={"strengths": [], "improvements": []},
                )

    async def test_approve_with_feedback_edit_records_before_after_in_the_audit_log(self):
        async with self.session_factory() as session:
            correction_id, _school_id = await self._pending_correction(session, "15")
            service = EssayCorrectionService(session)
            new_feedback = {
                "strengths": ["Boa argumentacao"],
                "improvements": ["Revisar coesao"],
                "next_essay_strategy": "Praticar conectivos.",
            }
            await service.approve(
                correction_id, reviewed_by_external_identity="teacher:maria",
                final_feedback=new_feedback,
            )

            log = await session.scalar(
                select(AdminAuditLog).where(
                    AdminAuditLog.entity_type == "ESSAY_CORRECTION",
                    AdminAuditLog.entity_id == str(correction_id),
                )
            )
            self.assertIn("final_feedback", log.metadata_)
            self.assertEqual(
                log.metadata_["final_feedback"]["after"]["next_essay_strategy"],
                "Praticar conectivos.",
            )
            self.assertEqual(
                log.metadata_["final_feedback"]["before"]["next_essay_strategy"], "...",
            )

    async def test_reject_writes_an_audit_log_entry(self):
        async with self.session_factory() as session:
            correction_id, school_id = await self._pending_correction(session, "10")
            service = EssayCorrectionService(session)
            await service.reject(correction_id, reviewed_by_external_identity="teacher:maria")

            log = await session.scalar(
                select(AdminAuditLog).where(
                    AdminAuditLog.entity_type == "ESSAY_CORRECTION",
                    AdminAuditLog.entity_id == str(correction_id),
                )
            )
            self.assertIsNotNone(log)
            self.assertEqual(log.action, "ESSAY_CORRECTION_REJECTED")

    async def test_bulk_approve_is_best_effort(self):
        async with self.session_factory() as session:
            ok_id, _school_id = await self._pending_correction(session, "11")
            already_done_id, _school_id2 = await self._pending_correction(session, "12")
            service = EssayCorrectionService(session)
            await service.reject(already_done_id, reviewed_by_external_identity="teacher:other")

            approved, failures = await service.bulk_approve(
                [ok_id, already_done_id], reviewed_by_external_identity="teacher:maria",
            )

            self.assertEqual([c.id for c in approved], [ok_id])
            self.assertIn(already_done_id, failures)
            refreshed_ok = await session.get(EssayCorrection, ok_id)
            self.assertEqual(refreshed_ok.status, "APPROVED")


if __name__ == "__main__":
    unittest.main()
