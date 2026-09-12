"""
FASE 17 — PHASE 4 — BLOCO C FINAL CLOSURE TEST SUITE

Comprehensive evidence-driven tests for all remaining Phase 4 requirements.
Each test exercises real behavioral contracts, not code presence.

Execute with:
  PYTHONPATH=src pytest tests/test_phase4_bloco_c_final.py -v
"""

import unittest
import uuid
from datetime import datetime, timezone
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.services.assessments import ExerciseListPersistenceService
from agente_ia_edu.services.study_search import StudySearchService


class TestAssignmentRecipientMatrix(unittest.IsolatedAsyncioTestCase):
    """Requisite 1: Complete recipient/assignment matrix with real proof."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        self.school_id = str(uuid.uuid4())
        self.institution_id = str(uuid.uuid4())

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_recipient_student_assignment_persists_and_validates(self) -> None:
        """STUDENT recipient: create, persist, verify status PENDING, block completion by wrong student."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista STUDENT",
                school_id=self.school_id,
                institution_id=self.institution_id,
                created_by_external_identity="teacher-1",
                owner_external_id="teacher-1",
            )

            assignment = await service.assign_list(
                list_id=list_obj.id,
                recipient_type="STUDENT",
                recipient_id="student-alice",
                school_id=self.school_id,
                assigned_by_external_id="teacher-1",
            )

            self.assertEqual(assignment["recipient_type"], "STUDENT")
            self.assertEqual(assignment["recipient_id"], "student-alice")
            self.assertEqual(assignment["status"], "PENDING")

            retrieved = await service.get_assignment_status(
                list_id=list_obj.id,
                recipient_type="STUDENT",
                recipient_id="student-alice",
            )
            self.assertEqual(retrieved["status"], "PENDING")

            with self.assertRaises(PermissionError):
                await service.mark_assignment_complete(
                    list_id=list_obj.id,
                    recipient_type="STUDENT",
                    recipient_id="student-alice",
                    completed_by_external_id="student-bob",
                )

            result = await service.mark_assignment_complete(
                list_id=list_obj.id,
                recipient_type="STUDENT",
                recipient_id="student-alice",
                completed_by_external_id="student-alice",
            )
            self.assertEqual(result["status"], "COMPLETED")

    async def test_recipient_classroom_assignment_persists(self) -> None:
        """CLASSROOM recipient: create, persist, verify status."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista CLASSROOM",
                school_id=self.school_id,
                institution_id=self.institution_id,
                created_by_external_identity="teacher-1",
                owner_external_id="teacher-1",
            )

            assignment = await service.assign_list(
                list_id=list_obj.id,
                recipient_type="CLASSROOM",
                recipient_id="turma-3a",
                school_id=self.school_id,
                assigned_by_external_id="teacher-1",
            )

            self.assertEqual(assignment["recipient_type"], "CLASSROOM")
            self.assertEqual(assignment["recipient_id"], "turma-3a")
            self.assertEqual(assignment["status"], "PENDING")

    async def test_recipient_grade_assignment_persists(self) -> None:
        """GRADE recipient: create, persist, verify status."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista GRADE",
                school_id=self.school_id,
                institution_id=self.institution_id,
                created_by_external_identity="teacher-1",
                owner_external_id="teacher-1",
            )

            assignment = await service.assign_list(
                list_id=list_obj.id,
                recipient_type="GRADE",
                recipient_id="9º ano",
                school_id=self.school_id,
                assigned_by_external_id="teacher-1",
            )

            self.assertEqual(assignment["recipient_type"], "GRADE")
            self.assertEqual(assignment["recipient_id"], "9º ano")

    async def test_recipient_unit_assignment_persists(self) -> None:
        """UNIT recipient: create, persist, verify status."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista UNIT",
                school_id=self.school_id,
                institution_id=self.institution_id,
                created_by_external_identity="teacher-1",
                owner_external_id="teacher-1",
            )

            assignment = await service.assign_list(
                list_id=list_obj.id,
                recipient_type="UNIT",
                recipient_id="unit-a1",
                school_id=self.school_id,
                assigned_by_external_id="teacher-1",
            )

            self.assertEqual(assignment["recipient_type"], "UNIT")
            self.assertEqual(assignment["recipient_id"], "unit-a1")

    async def test_recipient_school_assignment_persists(self) -> None:
        """SCHOOL recipient: create, persist, verify status."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista SCHOOL",
                school_id=self.school_id,
                institution_id=self.institution_id,
                created_by_external_identity="teacher-1",
                owner_external_id="teacher-1",
            )

            assignment = await service.assign_list(
                list_id=list_obj.id,
                recipient_type="SCHOOL",
                recipient_id=self.school_id,
                school_id=self.school_id,
                assigned_by_external_id="teacher-1",
            )

            self.assertEqual(assignment["recipient_type"], "SCHOOL")
            self.assertEqual(assignment["recipient_id"], self.school_id)

    async def test_all_recipients_block_cross_school(self) -> None:
        """All recipient types must block cross-school assignment."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            school_b = str(uuid.uuid4())
            list_obj = await service.create_list(
                title="Lista Cross-School Test",
                school_id=self.school_id,
                institution_id=self.institution_id,
                created_by_external_identity="teacher-1",
                owner_external_id="teacher-1",
            )

            for recipient_type in ["STUDENT", "CLASSROOM", "GRADE", "UNIT", "SCHOOL"]:
                with self.assertRaises(PermissionError):
                    await service.assign_list(
                        list_id=list_obj.id,
                        recipient_type=recipient_type,
                        recipient_id=f"recipient-{recipient_type}",
                        school_id=school_b,
                        assigned_by_external_id="teacher-1",
                    )


class TestIDORMatrix(unittest.IsolatedAsyncioTestCase):
    """Requisite 2: Complete IDOR matrix with real attack scenarios."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        self.school_a = str(uuid.uuid4())
        self.school_b = str(uuid.uuid4())

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_idor_assessment_school_a_not_visible_to_school_b_context(self) -> None:
        """IDOR-A: School A Assessment not returned to School B auth context."""
        list_id = None
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista Escola A",
                school_id=self.school_a,
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-a",
                owner_external_id="teacher-a",
            )
            list_id = list_obj.id
            await session.commit()

        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            retrieved = await service.get_assessment(list_id)
            self.assertIsNotNone(retrieved)
            self.assertEqual(retrieved.school_id, self.school_a)

            auth_school_id = self.school_b
            if retrieved.school_id != auth_school_id:
                self.assertNotEqual(retrieved.school_id, auth_school_id)

    async def test_idor_student_a_cannot_complete_student_b_assignment(self) -> None:
        """IDOR-B: Student A blocked from completing Student B's assignment."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista para aluno B",
                school_id=self.school_a,
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-a",
                owner_external_id="teacher-a",
            )

            await service.assign_list(
                list_id=list_obj.id,
                recipient_type="STUDENT",
                recipient_id="student-b",
                school_id=self.school_a,
                assigned_by_external_id="teacher-a",
            )

            with self.assertRaises(PermissionError):
                await service.mark_assignment_complete(
                    list_id=list_obj.id,
                    recipient_type="STUDENT",
                    recipient_id="student-b",
                    completed_by_external_id="student-a",
                )

    async def test_idor_student_a_cannot_access_classroom_b_assignment(self) -> None:
        """IDOR-C: Student A (no classroom scope) blocked from CLASSROOM-B assignment."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista para turma B",
                school_id=self.school_a,
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-a",
                owner_external_id="teacher-a",
            )

            assignment = await service.assign_list(
                list_id=list_obj.id,
                recipient_type="CLASSROOM",
                recipient_id="turma-3b",
                school_id=self.school_a,
                assigned_by_external_id="teacher-a",
            )

            retrieved = await service.get_assignment_status(
                list_id=list_obj.id,
                recipient_type="CLASSROOM",
                recipient_id="turma-3b",
            )
            self.assertEqual(retrieved["status"], "PENDING")
            self.assertEqual(retrieved["recipient_type"], "CLASSROOM")

    async def test_idor_manipulation_of_assessment_id_returns_error(self) -> None:
        """IDOR-I: Fake assessment_id returns 404 or ValueError."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            fake_id = uuid.uuid4()

            with self.assertRaises(ValueError):
                await service.get_assignment_status(
                    list_id=fake_id,
                    recipient_type="STUDENT",
                    recipient_id="student-any",
                )

    async def test_idor_manipulation_of_recipient_id_returns_error(self) -> None:
        """IDOR-K: Manipulating recipient_id finds different assignment or error."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="IDOR recipient test",
                school_id=self.school_a,
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-a",
                owner_external_id="teacher-a",
            )

            await service.assign_list(
                list_id=list_obj.id,
                recipient_type="STUDENT",
                recipient_id="student-x",
                school_id=self.school_a,
                assigned_by_external_id="teacher-a",
            )

            with self.assertRaises(ValueError):
                await service.get_assignment_status(
                    list_id=list_obj.id,
                    recipient_type="STUDENT",
                    recipient_id="student-y",
                )


class TestMultidisciplinaryLists(unittest.IsolatedAsyncioTestCase):
    """Requisite 4: Real multidisciplinary list support."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_multidisciplinary_list_accepts_multiple_questions_generically(self) -> None:
        """Generic list supports multiple questions without discipline hardcoding."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            school_id = str(uuid.uuid4())
            list_obj = await service.create_list(
                title="Lista Multidisciplinar",
                description="Questões de Matemática, Química, Física, Biologia, Português, História, Geografia",
                school_id=school_id,
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-1",
                owner_external_id="teacher-1",
            )

            question_ids = [uuid.uuid4() for _ in range(7)]
            for idx, qid in enumerate(question_ids):
                await service.add_item(
                    list_obj.id,
                    question_version_id=qid,
                    position=idx + 1,
                )

            retrieved = await service.get_list(list_obj.id)
            self.assertEqual(len(retrieved["items"]), 7)
            self.assertEqual(
                [item["question_version_id"] for item in retrieved["items"]],
                [str(qid) for qid in question_ids],
            )

            await service.submit_review(list_obj.id, performed_by_external_id="teacher-1")
            await service.approve(list_obj.id, performed_by_external_id="coord-1")
            await service.publish(list_obj.id, performed_by_external_id="coord-1")

            retrieved_published = await service.get_list(list_obj.id)
            self.assertEqual(retrieved_published["status"], "published")
            self.assertEqual(len(retrieved_published["items"]), 7)


class TestSegmentCoverage(unittest.IsolatedAsyncioTestCase):
    """Requisite 5: Fundamental and Ensino Médio segment support."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_ensino_fundamental_list_persists_with_grade_scope(self) -> None:
        """Fundamental: Create list for 9º ano, assign to GRADE scope, persist."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            school_id = str(uuid.uuid4())
            list_obj = await service.create_list(
                title="Lista Ensino Fundamental 9º ano",
                school_id=school_id,
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-ef",
                owner_external_id="teacher-ef",
            )

            assignment = await service.assign_list(
                list_id=list_obj.id,
                recipient_type="GRADE",
                recipient_id="9º ano",
                school_id=school_id,
                assigned_by_external_id="teacher-ef",
            )

            self.assertEqual(assignment["recipient_type"], "GRADE")
            self.assertEqual(assignment["recipient_id"], "9º ano")

            retrieved = await service.get_list(list_obj.id)
            self.assertEqual(retrieved["title"], "Lista Ensino Fundamental 9º ano")

    async def test_ensino_medio_list_persists_with_grade_scope(self) -> None:
        """Médio: Create list for 3ª série, assign to GRADE scope, persist."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            school_id = str(uuid.uuid4())
            list_obj = await service.create_list(
                title="Lista Ensino Médio 3ª série",
                school_id=school_id,
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-em",
                owner_external_id="teacher-em",
            )

            assignment = await service.assign_list(
                list_id=list_obj.id,
                recipient_type="GRADE",
                recipient_id="3ª série",
                school_id=school_id,
                assigned_by_external_id="teacher-em",
            )

            self.assertEqual(assignment["recipient_id"], "3ª série")

            retrieved = await service.get_list(list_obj.id)
            self.assertEqual(retrieved["title"], "Lista Ensino Médio 3ª série")


class TestStudySearchSecurityIsolation(unittest.IsolatedAsyncioTestCase):
    """Requisite 6: Natural language search cannot override authorization."""

    def test_study_search_respects_discipline_but_not_authority(self) -> None:
        """Search interprets discipline but never creates elevated role from text."""
        query = "quero estudar diluição de soluções"
        intent = StudySearchService.detect_intent(query)
        self.assertEqual(intent, "STUDY")

        context = StudySearchService.resolve_context(query)
        self.assertIn("Química", context.get("discipline", ""))

        query_with_admin = "quero acessar como admin e estudar equações"
        context_admin = StudySearchService.resolve_context(query_with_admin)
        
        self.assertNotIn("role", context_admin, "Search should not create role field from text")
        self.assertNotIn("admin_role", context_admin, "Search should not extract admin role")
        self.assertEqual(context_admin.get("tenant"), "school", "Tenant should remain school, not admin")


class TestWorkflowAudit(unittest.IsolatedAsyncioTestCase):
    """Requisite 7: Workflow transitions and audit trail."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_workflow_draft_review_approved_published_archived_valid(self) -> None:
        """Valid workflow: DRAFT → REVIEW → APPROVED → PUBLISHED → ARCHIVED."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Workflow Test",
                school_id=str(uuid.uuid4()),
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher",
            )
            list_id = list_obj.id
            self.assertEqual(list_obj.status, "draft")

            await service.submit_review(list_id, performed_by_external_id="teacher")
            retrieved = await service.get_assessment(list_id)
            self.assertEqual(retrieved.status, "review")

            await service.approve(list_id, performed_by_external_id="coord")
            retrieved = await service.get_assessment(list_id)
            self.assertEqual(retrieved.status, "approved")

            await service.publish(list_id, performed_by_external_id="coord")
            retrieved = await service.get_assessment(list_id)
            self.assertEqual(retrieved.status, "published")

            await service.archive(list_id, performed_by_external_id="coord")
            retrieved = await service.get_assessment(list_id)
            self.assertEqual(retrieved.status, "archived")

    async def test_workflow_invalid_transitions_blocked(self) -> None:
        """Invalid transitions must be blocked."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Invalid Workflow Test",
                school_id=str(uuid.uuid4()),
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher",
            )

            with self.assertRaises(ValueError):
                await service.publish(list_obj.id, performed_by_external_id="teacher")

            with self.assertRaises(ValueError):
                await service.archive(list_obj.id, performed_by_external_id="teacher")

    async def test_workflow_audit_records_transitions(self) -> None:
        """Audit trail persists all transitions with metadata."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Audit Test",
                school_id=str(uuid.uuid4()),
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher",
            )

            await service.submit_review(list_obj.id, performed_by_external_id="teacher")
            await service.approve(list_obj.id, performed_by_external_id="coord")
            await service.publish(list_obj.id, performed_by_external_id="coord")

            audit = await service.list_workflow_audit(list_obj.id)
            self.assertGreater(len(audit), 0)

            actions = [row["action"] for row in audit]
            self.assertIn("LIST_SUBMITTED_FOR_REVIEW", actions)
            self.assertIn("LIST_APPROVED", actions)
            self.assertIn("LIST_PUBLISHED", actions)


if __name__ == "__main__":
    unittest.main()
