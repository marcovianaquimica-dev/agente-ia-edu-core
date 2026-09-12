import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import Assessment, AssessmentAssignment, AssessmentItem, AssessmentVersion
from agente_ia_edu.services.assessments import ExerciseListPersistenceService


class ExerciseListPersistenceTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_create_list_persists_and_reads_items_in_order(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            question_a = uuid.uuid4()
            question_b = uuid.uuid4()
            question_c = uuid.uuid4()

            list_obj = await service.create_list(
                title="Lista de revisão",
                description="Química",
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-a",
            )

            await service.add_item(list_obj.id, question_version_id=question_a, position=3)
            await service.add_item(list_obj.id, question_version_id=question_b, position=1)
            await service.add_item(list_obj.id, question_version_id=question_c, position=2)

            retrieved = await service.get_list(list_obj.id)
            self.assertEqual([item["question_version_id"] for item in retrieved["items"]], [
                str(question_b),
                str(question_c),
                str(question_a),
            ])

    async def test_list_version_is_preserved_after_question_version_changes(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            question_v1 = uuid.uuid4()
            list_obj = await service.create_list(
                title="Lista com versão fixa",
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-a",
            )
            await service.add_item(list_obj.id, question_version_id=question_v1, position=1)
            await service.submit_review(list_obj.id, performed_by_external_id="teacher-a")
            await service.approve(list_obj.id, performed_by_external_id="coord-a")
            await service.publish(list_obj.id, performed_by_external_id="coord-a")

            reloaded = await service.get_list(list_obj.id)
            self.assertEqual(reloaded["items"][0]["question_version_id"], str(question_v1))
            self.assertEqual(reloaded["status"], "published")

    async def test_status_workflow_rejects_invalid_transitions(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista workflow",
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-a",
            )

            await service.submit_review(list_obj.id, performed_by_external_id="teacher-a")
            await service.approve(list_obj.id, performed_by_external_id="coord-a")
            with self.assertRaises(ValueError):
                await service.submit_review(list_obj.id, performed_by_external_id="teacher-a")
            await service.publish(list_obj.id, performed_by_external_id="coord-a")
            with self.assertRaises(ValueError):
                await service.publish(list_obj.id, performed_by_external_id="coord-a")

    async def test_delete_and_update_item_and_list_persist(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            q1 = uuid.uuid4()
            q2 = uuid.uuid4()

            list_obj = await service.create_list(
                title="Lista para atualizar",
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-a",
            )
            await service.add_item(list_obj.id, question_version_id=q1, position=1)
            await service.add_item(list_obj.id, question_version_id=q2, position=2)

            item_id = (await service.get_list(list_obj.id))["items"][0]["id"]
            await service.remove_item(list_obj.id, item_id)
            await service.update_list(list_obj.id, title="Lista atualizada")

            reloaded = await service.get_list(list_obj.id)
            self.assertEqual(reloaded["title"], "Lista atualizada")
            self.assertEqual(len(reloaded["items"]), 1)

    async def test_assignment_persists_and_tracks_pending_completion(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            school_id = str(uuid.uuid4())
            list_obj = await service.create_list(
                title="Lista atribuída",
                school_id=school_id,
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-a",
                owner_external_id="teacher-a",
            )
            assignment = await service.assign_list(
                list_id=list_obj.id,
                recipient_type="STUDENT",
                recipient_id="student-001",
                school_id=school_id,
                assigned_by_external_id="teacher-a",
            )
            self.assertEqual(assignment["recipient_type"], "STUDENT")
            self.assertEqual(assignment["status"], "PENDING")

            await service.mark_assignment_complete(
                list_id=list_obj.id,
                recipient_type="STUDENT",
                recipient_id="student-001",
                completed_by_external_id="student-001",
            )
            refreshed = await service.get_assignment_status(
                list_id=list_obj.id,
                recipient_type="STUDENT",
                recipient_id="student-001",
            )
            self.assertEqual(refreshed["status"], "COMPLETED")

    async def test_workflow_transition_audit_is_recorded(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            school_id = str(uuid.uuid4())
            list_obj = await service.create_list(
                title="Lista com auditoria",
                school_id=school_id,
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-a",
                owner_external_id="teacher-a",
            )
            await service.submit_review(list_obj.id, performed_by_external_id="teacher-a")
            await service.approve(list_obj.id, performed_by_external_id="coord-a")
            audit_rows = await service.list_workflow_audit(list_obj.id)
            self.assertGreaterEqual(len(audit_rows), 2)
            self.assertTrue(any(row["action"] == "LIST_APPROVED" for row in audit_rows))

    async def test_assignment_rejects_cross_school_assignment(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            school_a = str(uuid.uuid4())
            school_b = str(uuid.uuid4())
            list_obj = await service.create_list(
                title="Lista escola A",
                school_id=school_a,
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-a",
                owner_external_id="teacher-a",
            )

            with self.assertRaises(PermissionError):
                await service.assign_list(
                    list_id=list_obj.id,
                    recipient_type="STUDENT",
                    recipient_id="student-b",
                    school_id=school_b,
                    assigned_by_external_id="teacher-b",
                )

    async def test_assignment_completion_requires_matching_recipient(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            school_id = str(uuid.uuid4())
            list_obj = await service.create_list(
                title="Lista de aluno",
                school_id=school_id,
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-a",
                owner_external_id="teacher-a",
            )
            await service.assign_list(
                list_id=list_obj.id,
                recipient_type="STUDENT",
                recipient_id="student-a",
                school_id=school_id,
                assigned_by_external_id="teacher-a",
            )

            with self.assertRaises(PermissionError):
                await service.mark_assignment_complete(
                    list_id=list_obj.id,
                    recipient_type="STUDENT",
                    recipient_id="student-a",
                    completed_by_external_id="student-b",
                )

    async def test_assignment_matrix_includes_unit_recipient(self) -> None:
        """The persisted assignment contract must accept UNIT recipients in real use."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            school_id = str(uuid.uuid4())
            list_obj = await service.create_list(
                title="Lista com unidade",
                school_id=school_id,
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-a",
                owner_external_id="teacher-a",
            )

            assignment = await service.assign_list(
                list_id=list_obj.id,
                recipient_type="UNIT",
                recipient_id="unit-a1",
                school_id=school_id,
                assigned_by_external_id="teacher-a",
            )

            self.assertEqual(assignment["recipient_type"], "UNIT")
            self.assertEqual(assignment["recipient_id"], "unit-a1")
            self.assertEqual(assignment["school_id"], school_id)

    async def test_multidisciplinary_lists_remain_generic_and_ordered(self) -> None:
        """A generic exercise list should accept multiple disciplines without hardcoded rules."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            school_id = str(uuid.uuid4())
            list_obj = await service.create_list(
                title="Lista multidisciplinar",
                school_id=school_id,
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-a",
                owner_external_id="teacher-a",
            )

            question_ids = [uuid.uuid4() for _ in range(7)]
            for idx, question_id in enumerate(question_ids):
                await service.add_item(list_obj.id, question_version_id=question_id, position=idx + 1)

            assignment = await service.assign_list(
                list_id=list_obj.id,
                recipient_type="UNIT",
                recipient_id="unit-a1",
                school_id=school_id,
                assigned_by_external_id="teacher-a",
            )

            self.assertEqual(assignment["recipient_type"], "UNIT")
            self.assertEqual(assignment["recipient_id"], "unit-a1")

            retrieved = await service.get_list(list_obj.id)
            self.assertEqual(
                [item["question_version_id"] for item in retrieved["items"]],
                [str(qid) for qid in question_ids],
            )

    async def test_unit_assignment_rejects_cross_school_scope(self) -> None:
        """UNIT recipients remain tenant-scoped even when the school payload is mismatched."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            school_a = str(uuid.uuid4())
            school_b = str(uuid.uuid4())
            list_obj = await service.create_list(
                title="Lista de unidade",
                school_id=school_a,
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-a",
                owner_external_id="teacher-a",
            )

            with self.assertRaises(PermissionError):
                await service.assign_list(
                    list_id=list_obj.id,
                    recipient_type="UNIT",
                    recipient_id="unit-a1",
                    school_id=school_b,
                    assigned_by_external_id="teacher-a",
                )


if __name__ == "__main__":
    unittest.main()
