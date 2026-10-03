import unittest
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear, Class, EssayPrompt, GradeLevel, Person, PromptAssignment,
    School, Segment, Student, StudentEnrollment,
)
from agente_ia_edu.services.essay_proposal import EssayProposalService


class EssayProposalServiceTests(unittest.IsolatedAsyncioTestCase):
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

    async def _school_and_class(self, session, code):
        school = School(id=uuid.uuid4(), code=f"PR-{code}", name=f"school-{code}")
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
        await session.commit()
        return school, klass

    async def test_create_prompt_starts_as_draft(self):
        async with self.session_factory() as session:
            school, _ = await self._school_and_class(session, "1")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p1",
            )
            self.assertEqual(prompt.status, "DRAFT")

    async def test_add_material_requires_draft(self):
        async with self.session_factory() as session:
            school, klass = await self._school_and_class(session, "2")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p2",
            )
            material = await svc.add_material(
                school_id=school.id, essay_prompt_id=prompt.id,
                material_type="TEXT", content="Apoio.", position=0,
            )
            self.assertEqual(material.material_type, "TEXT")

            await svc.create_assignment(
                school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
                assigned_by_external_identity="teacher:p2",
            )
            with self.assertRaises(ValueError):
                await svc.add_material(
                    school_id=school.id, essay_prompt_id=prompt.id,
                    material_type="TEXT", content="Tarde demais.", position=1,
                )

    async def test_add_material_rejects_type_content_mismatch(self):
        async with self.session_factory() as session:
            school, _ = await self._school_and_class(session, "3")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p3",
            )
            with self.assertRaises(ValueError):
                await svc.add_material(
                    school_id=school.id, essay_prompt_id=prompt.id,
                    material_type="TEXT", content=None, position=0,
                )
            with self.assertRaises(ValueError):
                await svc.add_material(
                    school_id=school.id, essay_prompt_id=prompt.id,
                    material_type="IMAGE", storage_uri=None, position=0,
                )

    async def test_add_material_rejects_foreign_school(self):
        async with self.session_factory() as session:
            school_a, _ = await self._school_and_class(session, "3a")
            school_b, _ = await self._school_and_class(session, "3b")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school_a.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p3a",
            )
            with self.assertRaises(ValueError):
                await svc.add_material(
                    school_id=school_b.id, essay_prompt_id=prompt.id,
                    material_type="TEXT", content="Invasao.", position=0,
                )

    async def test_create_assignment_activates_prompt_and_rejects_foreign_class(self):
        async with self.session_factory() as session:
            school_a, class_a = await self._school_and_class(session, "4a")
            school_b, class_b = await self._school_and_class(session, "4b")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school_a.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p4",
            )

            with self.assertRaises(ValueError):
                await svc.create_assignment(
                    school_id=school_a.id, essay_prompt_id=prompt.id, class_id=class_b.id,
                    assigned_by_external_identity="teacher:p4",
                )

            assignment = await svc.create_assignment(
                school_id=school_a.id, essay_prompt_id=prompt.id, class_id=class_a.id,
                assigned_by_external_identity="teacher:p4",
            )
            self.assertEqual(assignment.status, "OPEN")

            refreshed_prompt = await session.get(type(prompt), prompt.id)
            self.assertEqual(refreshed_prompt.status, "ACTIVE")

            with self.assertRaises(ValueError):
                await svc.create_assignment(
                    school_id=school_a.id, essay_prompt_id=prompt.id, class_id=class_a.id,
                    assigned_by_external_identity="teacher:p4",
                )

    async def test_create_prompt_defaults_to_not_free_theme(self):
        async with self.session_factory() as session:
            school, _ = await self._school_and_class(session, "5")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p5",
            )
            self.assertFalse(prompt.is_free_theme)

    async def test_add_material_accepts_file_type_with_storage_uri(self):
        async with self.session_factory() as session:
            school, _ = await self._school_and_class(session, "10")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p10",
            )
            material = await svc.add_material(
                school_id=school.id, essay_prompt_id=prompt.id,
                material_type="FILE", storage_uri="/var/material_storage/ab/abcd/reportagem.pdf",
                position=0,
            )
            self.assertEqual(material.material_type, "FILE")
            self.assertIsNone(material.content)

    async def test_add_material_file_type_requires_storage_uri(self):
        async with self.session_factory() as session:
            school, _ = await self._school_and_class(session, "11")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p11",
            )
            with self.assertRaises(ValueError):
                await svc.add_material(
                    school_id=school.id, essay_prompt_id=prompt.id,
                    material_type="FILE", position=0,
                )

    async def test_create_prompt_can_be_marked_free_theme(self):
        async with self.session_factory() as session:
            school, _ = await self._school_and_class(session, "6")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema livre", statement="Escolha seu tema.",
                year=2026, created_by_external_identity="teacher:p6", is_free_theme=True,
            )
            self.assertTrue(prompt.is_free_theme)

    async def _school_and_classes(self, session, code, count):
        school = School(id=uuid.uuid4(), code=f"PRB-{code}", name=f"school-{code}")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id=f"SEGB-{code}")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="grade", external_id=f"GRADEB-{code}",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id=f"YEARB-{code}")
        session.add_all([grade, year])
        await session.flush()
        classes = []
        for i in range(count):
            klass = Class(
                id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
                grade_level_id=grade.id, name=f"turma-{i}", external_id=f"TURMAB-{code}-{i}",
            )
            session.add(klass)
            classes.append(klass)
        await session.commit()
        return school, classes

    async def test_create_assignments_bulk_assigns_every_class(self):
        async with self.session_factory() as session:
            school, classes = await self._school_and_classes(session, "7", 3)
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p7",
            )
            assigned, failures = await svc.create_assignments_bulk(
                school_id=school.id, essay_prompt_id=prompt.id,
                class_ids=[c.id for c in classes],
                assigned_by_external_identity="teacher:p7",
            )
            self.assertEqual(len(assigned), 3)
            self.assertEqual(failures, {})
            self.assertEqual({a["class_id"] for a in assigned}, {c.id for c in classes})
            for a in assigned:
                self.assertEqual(a["status"], "OPEN")

            refreshed_prompt = await session.get(type(prompt), prompt.id)
            self.assertEqual(refreshed_prompt.status, "ACTIVE")

    async def test_create_assignments_bulk_one_bad_class_does_not_block_the_others(self):
        async with self.session_factory() as session:
            school, classes = await self._school_and_classes(session, "8", 2)
            other_school, other_classes = await self._school_and_classes(session, "8b", 1)
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p8",
            )
            bad_class_id = other_classes[0].id  # belongs to a different school
            assigned, failures = await svc.create_assignments_bulk(
                school_id=school.id, essay_prompt_id=prompt.id,
                class_ids=[classes[0].id, bad_class_id, classes[1].id],
                assigned_by_external_identity="teacher:p8",
            )
            self.assertEqual(len(assigned), 2)
            self.assertEqual({a["class_id"] for a in assigned}, {classes[0].id, classes[1].id})
            self.assertEqual(list(failures.keys()), [bad_class_id])

    async def test_create_assignments_bulk_duplicate_class_becomes_a_failure_not_a_lost_batch(self):
        """Confirmed by design: create_assignment's own IntegrityError path
        calls session.rollback() - committing after each individual success
        (see create_assignments_bulk's docstring) is what keeps an EARLIER
        class in the same batch from being wiped out by a LATER class's
        conflict, rather than just moving the crash later."""
        async with self.session_factory() as session:
            school, classes = await self._school_and_classes(session, "9", 2)
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p9",
            )
            # Captured BEFORE the bulk call: create_assignment's own
            # IntegrityError path calls session.rollback(), which expires
            # every object this session is tracking - including `classes`
            # here, unrelated as they are - so reading their attributes
            # AFTER the call would trigger a lazy re-select outside an
            # async-safe context (MissingGreenlet). Confirmed live: this
            # is exactly the failure mode, not a hypothetical.
            class0_id, class1_id = classes[0].id, classes[1].id
            assigned, failures = await svc.create_assignments_bulk(
                school_id=school.id, essay_prompt_id=prompt.id,
                class_ids=[class0_id, class0_id, class1_id],
                assigned_by_external_identity="teacher:p9",
            )
            self.assertEqual({a["class_id"] for a in assigned}, {class0_id, class1_id})
            self.assertEqual(len(assigned), 2)
            self.assertIn(class0_id, failures)

    async def test_soft_delete_sets_deleted_at_and_restore_clears_it(self):
        async with self.session_factory() as session:
            school, _ = await self._school_and_class(session, "12")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p12",
            )
            deleted = await svc.soft_delete_prompt(school_id=school.id, essay_prompt_id=prompt.id)
            self.assertIsNotNone(deleted.deleted_at)

            restored = await svc.restore_prompt(school_id=school.id, essay_prompt_id=prompt.id)
            self.assertIsNone(restored.deleted_at)

    async def test_soft_delete_rejects_foreign_school_and_already_deleted(self):
        async with self.session_factory() as session:
            school_a, _ = await self._school_and_class(session, "13a")
            school_b, _ = await self._school_and_class(session, "13b")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school_a.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p13",
            )
            with self.assertRaises(ValueError):
                await svc.soft_delete_prompt(school_id=school_b.id, essay_prompt_id=prompt.id)

            await svc.soft_delete_prompt(school_id=school_a.id, essay_prompt_id=prompt.id)
            with self.assertRaises(ValueError):
                await svc.soft_delete_prompt(school_id=school_a.id, essay_prompt_id=prompt.id)

    async def test_restore_rejects_prompt_not_in_trash(self):
        async with self.session_factory() as session:
            school, _ = await self._school_and_class(session, "14")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p14",
            )
            with self.assertRaises(ValueError):
                await svc.restore_prompt(school_id=school.id, essay_prompt_id=prompt.id)

    async def test_restore_rejects_prompt_past_retention_window(self):
        async with self.session_factory() as session:
            school, _ = await self._school_and_class(session, "15")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p15",
            )
            await svc.soft_delete_prompt(school_id=school.id, essay_prompt_id=prompt.id)
            # Back-date deleted_at past the retention window directly - the
            # 30-day clock is measured from deleted_at, not from a separate
            # counter, so this is the real way to simulate "aged out".
            prompt.deleted_at = datetime.now(timezone.utc) - timedelta(
                days=EssayProposalService.TRASH_RETENTION_DAYS + 1
            )
            await session.flush()
            with self.assertRaises(ValueError):
                await svc.restore_prompt(school_id=school.id, essay_prompt_id=prompt.id)

    async def test_list_trash_only_returns_prompts_within_retention_window(self):
        async with self.session_factory() as session:
            school, _ = await self._school_and_class(session, "16")
            svc = EssayProposalService(session)
            fresh = await svc.create_prompt(
                school_id=school.id, title="Recente", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p16",
            )
            aged_out = await svc.create_prompt(
                school_id=school.id, title="Antigo", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p16",
            )
            active = await svc.create_prompt(
                school_id=school.id, title="Ativo", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p16",
            )
            await svc.soft_delete_prompt(school_id=school.id, essay_prompt_id=fresh.id)
            await svc.soft_delete_prompt(school_id=school.id, essay_prompt_id=aged_out.id)
            aged_out.deleted_at = datetime.now(timezone.utc) - timedelta(
                days=EssayProposalService.TRASH_RETENTION_DAYS + 1
            )
            await session.flush()

            trash = await svc.list_trash(school_id=school.id)
            trash_ids = {p.id for p in trash}
            self.assertIn(fresh.id, trash_ids)
            self.assertNotIn(aged_out.id, trash_ids)
            self.assertNotIn(active.id, trash_ids)

    async def test_deleted_prompt_rejects_new_material_and_assignment(self):
        async with self.session_factory() as session:
            school, klass = await self._school_and_class(session, "17")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p17",
            )
            await svc.soft_delete_prompt(school_id=school.id, essay_prompt_id=prompt.id)

            with self.assertRaises(ValueError):
                await svc.add_material(
                    school_id=school.id, essay_prompt_id=prompt.id,
                    material_type="TEXT", content="Tarde demais.", position=0,
                )
            with self.assertRaises(ValueError):
                await svc.create_assignment(
                    school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
                    assigned_by_external_identity="teacher:p17",
                )

    async def _seed_two_classes_same_grade(self, session):
        """2 turmas da mesma serie (A, B) + 1 turma de outra serie (C),
        1 aluno ativo em cada - mesmo padrao do envio em lote, pra testar
        expansao de serie->turmas."""
        school = School(id=uuid.uuid4(), code="PR-SERIE", name="Escola")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-PR")
        session.add(segment)
        await session.flush()
        grade_x = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="Serie X", external_id="GRADE-PR-X",
        )
        grade_y = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="Serie Y", external_id="GRADE-PR-Y",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-PR")
        session.add_all([grade_x, grade_y, year])
        await session.flush()
        class_a = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade_x.id, name="Turma A", external_id="TURMA-PR-A",
        )
        class_b = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade_x.id, name="Turma B", external_id="TURMA-PR-B",
        )
        class_c = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade_y.id, name="Turma C", external_id="TURMA-PR-C",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof",
        )
        session.add_all([class_a, class_b, class_c, prompt])
        await session.flush()

        def _make_student(nome, klass):
            person = Person(id=uuid.uuid4(), school_id=school.id, full_name=nome)
            session.add(person)
            student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id)
            session.add(student)
            session.add(StudentEnrollment(
                id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                class_id=klass.id, status="ACTIVE",
            ))
            return student.id

        student_a = _make_student("Aluno Turma A", class_a)
        student_c = _make_student("Aluno Turma C", class_c)
        await session.flush()
        return {
            "school": school, "prompt": prompt, "grade_x": grade_x,
            "class_a": class_a, "class_b": class_b, "class_c": class_c,
            "student_a": student_a, "student_c": student_c,
        }

    async def test_resolve_assignment_targets_expands_grade_into_its_classes(self):
        async with self.session_factory() as session:
            seed = await self._seed_two_classes_same_grade(session)
            service = EssayProposalService(session)
            result = await service.resolve_assignment_targets(
                school_id=seed["school"].id, class_ids=[], grade_level_ids=[seed["grade_x"].id],
                student_ids=[],
            )
            self.assertEqual(result["class_ids"], {seed["class_a"].id, seed["class_b"].id})

    async def test_resolve_assignment_targets_does_not_duplicate_class_in_both_sets(self):
        async with self.session_factory() as session:
            seed = await self._seed_two_classes_same_grade(session)
            service = EssayProposalService(session)
            result = await service.resolve_assignment_targets(
                school_id=seed["school"].id, class_ids=[seed["class_a"].id],
                grade_level_ids=[seed["grade_x"].id], student_ids=[],
            )
            self.assertEqual(result["class_ids"], {seed["class_a"].id, seed["class_b"].id})

    async def test_create_assignments_combined_creates_class_and_student_targets(self):
        async with self.session_factory() as session:
            seed = await self._seed_two_classes_same_grade(session)
            service = EssayProposalService(session)
            result = await service.create_assignments_combined(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_ids=[seed["class_b"].id], grade_level_ids=[],
                student_ids=[seed["student_c"]],
                assigned_by_external_identity="prof",
            )
            self.assertEqual(result["created_count"], 2)
            self.assertEqual(result["already_assigned_count"], 0)

            rows = (await session.execute(
                select(PromptAssignment).where(PromptAssignment.essay_prompt_id == seed["prompt"].id)
            )).scalars().all()
            self.assertEqual(
                {(r.class_id, r.student_id) for r in rows},
                {(seed["class_b"].id, None), (None, seed["student_c"])},
            )

    async def test_create_assignments_combined_is_idempotent_for_already_assigned_targets(self):
        async with self.session_factory() as session:
            seed = await self._seed_two_classes_same_grade(session)
            service = EssayProposalService(session)
            await service.create_assignments_combined(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_ids=[seed["class_a"].id], grade_level_ids=[], student_ids=[],
                assigned_by_external_identity="prof",
            )
            result = await service.create_assignments_combined(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_ids=[seed["class_a"].id], grade_level_ids=[], student_ids=[seed["student_c"]],
                assigned_by_external_identity="prof",
            )
            self.assertEqual(result["created_count"], 1)
            self.assertEqual(result["already_assigned_count"], 1)
            rows = (await session.execute(
                select(PromptAssignment).where(
                    PromptAssignment.essay_prompt_id == seed["prompt"].id,
                    PromptAssignment.class_id == seed["class_a"].id,
                )
            )).scalars().all()
            self.assertEqual(len(rows), 1)

    async def test_create_assignments_combined_writes_one_log_per_call(self):
        async with self.session_factory() as session:
            seed = await self._seed_two_classes_same_grade(session)
            service = EssayProposalService(session)
            await service.create_assignments_combined(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_ids=[seed["class_a"].id, seed["class_b"].id], grade_level_ids=[],
                student_ids=[seed["student_c"]],
                assigned_by_external_identity="prof",
            )
            logs = await service.list_assignment_log(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id
            )
            self.assertEqual(len(logs), 1)
            self.assertEqual(len(logs[0].target_summary["turmas"]), 2)
            self.assertEqual(len(logs[0].target_summary["alunos"]), 1)

    async def test_search_students_for_assignment_restricted_to_given_class_ids(self):
        async with self.session_factory() as session:
            seed = await self._seed_two_classes_same_grade(session)
            service = EssayProposalService(session)
            results = await service.search_students_for_assignment(
                school_id=seed["school"].id, class_ids=[seed["class_a"].id], query="Aluno"
            )
            ids = {r[0] for r in results}
            self.assertEqual(ids, {seed["student_a"]})
            self.assertNotIn(seed["student_c"], ids)


if __name__ == "__main__":
    unittest.main()
