"""R4 lote - resolucao manual: o professor escolhe o aluno de uma pagina."""

import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear, Class, EssayBatchPage, EssayBatchUpload, EssayCorrection, EssayPrompt,
    EssaySubmission, GradeLevel, Person, PromptAssignment, School, Segment, Student,
    StudentEnrollment,
)
from agente_ia_edu.services.essay_batch import EssayBatchService


class DumbCorrectionService:
    """Reproduz SO o contrato de idempotencia real de
    EssayCorrectionService.correct() (essay_correction.py): existe uma
    EssayCorrection para a submissao? devolve ela sem "corrigir" de novo. Nao
    existe? cria uma nova gravando o canonical_text ATUAL da submissao - e
    exatamente esse "atual" que fica errado quando a corrida e
    re-materializada mas a correcao antiga nao e invalidada (Problema 1 do
    fix-round-1-brief.md): correct() encontraria a correcao velha e nunca
    chamaria a IA sobre o texto completo."""

    def __init__(self, session):
        self.session = session

    async def correct(self, essay_submission_id):
        existing = await self.session.scalar(
            select(EssayCorrection).where(
                EssayCorrection.essay_submission_id == essay_submission_id
            )
        )
        if existing is not None:
            return existing
        submission = await self.session.get(EssaySubmission, essay_submission_id)
        correction = EssayCorrection(
            id=uuid.uuid4(), school_id=submission.school_id,
            essay_submission_id=essay_submission_id,
            correction_key=f"key-{uuid.uuid4()}", rubric_version="v1",
            model_version="m1", prompt_version="p1", engine_version="e1",
            ai_output={"corrected_text": submission.canonical_text},
            final_scores={"total": len(submission.canonical_text)},
            final_feedback={}, status="PENDING_REVIEW",
        )
        self.session.add(correction)
        await self.session.flush()
        return correction


class ResolvePageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="r4_resolve_"))

    async def asyncTearDown(self):
        await self.engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def _seed(self, session, student_names):
        school = School(id=uuid.uuid4(), code="RS-1", name="Escola")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-RS")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="3a", external_id="GRADE-RS",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-RS")
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="3A", external_id="TURMA-RS",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof",
        )
        session.add_all([klass, prompt])
        await session.flush()
        session.add(PromptAssignment(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, assigned_by_external_identity="prof",
        ))
        students = {}
        for index, full_name in enumerate(student_names):
            person = Person(
                id=uuid.uuid4(), school_id=school.id, full_name=full_name,
                external_id=f"PER-R{index}",
            )
            session.add(person)
            await session.flush()
            student = Student(
                id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                external_id=f"STU-R{index}",
            )
            session.add(student)
            await session.flush()
            session.add(StudentEnrollment(
                id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                class_id=klass.id, status="ACTIVE", external_id=f"ENR-R{index}",
            ))
            students[full_name] = student.id
        await session.commit()
        return school, klass, prompt, students

    async def _batch_with_pages(self, session, school, klass, prompt, bodies):
        """Cria o lote e as paginas DIRETO no banco (sem OCR): esta tarefa testa
        so a resolucao manual, que parte de paginas ja lidas e nao identificadas."""
        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, uploaded_by_external_identity="prof",
            status="DONE", total_pages=len(bodies),
        )
        session.add(batch)
        await session.flush()
        pages = []
        for number, body in enumerate(bodies, start=1):
            page = EssayBatchPage(
                id=uuid.uuid4(), batch_id=batch.id, page_number=number,
                storage_uri=str(self.tmp_dir / f"p{number}.png"),
                ocr_body_text=body, status="NEEDS_REVIEW",
            )
            session.add(page)
            pages.append(page)
        await session.commit()
        return batch, pages

    async def test_resolving_a_page_creates_the_submission(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(session, ["Ana Lúcia Ferreira"])
            batch, pages = await self._batch_with_pages(
                session, school, klass, prompt, ["Texto ilegivel pro OCR mas legivel pro humano."]
            )
            service = EssayBatchService(session)

            created_ids = await service.resolve_page(
                batch_id=batch.id, page_id=pages[0].id,
                student_id=students["Ana Lúcia Ferreira"],
            )

            self.assertEqual(len(created_ids), 1)
            submission = await session.get(EssaySubmission, created_ids[0])
            self.assertEqual(submission.student_id, students["Ana Lúcia Ferreira"])
            self.assertEqual(submission.status, "SUBMITTED")
            self.assertEqual(submission.anchor_mode, "TEXT_OFFSET")
            refreshed = await session.get(EssayBatchPage, pages[0].id)
            self.assertEqual(refreshed.status, "RESOLVED_MANUAL")
            self.assertEqual(refreshed.matched_student_id, students["Ana Lúcia Ferreira"])
            self.assertEqual(refreshed.essay_submission_id, submission.id)

    async def test_resolving_a_neighbour_page_joins_the_existing_submission(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(session, ["Ana Lúcia Ferreira"])
            batch, pages = await self._batch_with_pages(
                session, school, klass, prompt, ["Primeira parte.", "Segunda parte."]
            )
            service = EssayBatchService(session)

            first = await service.resolve_page(
                batch_id=batch.id, page_id=pages[0].id,
                student_id=students["Ana Lúcia Ferreira"],
            )
            second = await service.resolve_page(
                batch_id=batch.id, page_id=pages[1].id,
                student_id=students["Ana Lúcia Ferreira"],
            )

            self.assertEqual(first, second, "a segunda pagina entra na MESMA submissao")
            submissions = (await session.execute(select(EssaySubmission))).scalars().all()
            self.assertEqual(len(submissions), 1)
            self.assertIn("Primeira parte.", submissions[0].canonical_text)
            self.assertIn("Segunda parte.", submissions[0].canonical_text)

    async def test_a_non_adjacent_page_of_the_same_student_gets_its_own_submission(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(
                session, ["Ana Lúcia Ferreira", "João da Silva"]
            )
            batch, pages = await self._batch_with_pages(
                session, school, klass, prompt, ["Ana um.", "Joao.", "Ana dois."]
            )
            service = EssayBatchService(session)

            first = await service.resolve_page(
                batch_id=batch.id, page_id=pages[0].id,
                student_id=students["Ana Lúcia Ferreira"],
            )
            third = await service.resolve_page(
                batch_id=batch.id, page_id=pages[2].id,
                student_id=students["Ana Lúcia Ferreira"],
            )

            self.assertNotEqual(first, third)
            submissions = (await session.execute(
                select(EssaySubmission).where(
                    EssaySubmission.student_id == students["Ana Lúcia Ferreira"]
                )
            )).scalars().all()
            self.assertEqual(len(submissions), 2)

    async def test_rejects_a_page_from_another_batch(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(session, ["Ana Lúcia Ferreira"])
            batch, _pages = await self._batch_with_pages(session, school, klass, prompt, ["x"])
            service = EssayBatchService(session)
            with self.assertRaises(ValueError):
                await service.resolve_page(
                    batch_id=batch.id, page_id=uuid.uuid4(),
                    student_id=students["Ana Lúcia Ferreira"],
                )

    async def test_rejects_a_student_from_another_school(self):
        async with self.session_factory() as session:
            school, klass, prompt, _students = await self._seed(session, ["Ana Lúcia Ferreira"])
            other_school = School(id=uuid.uuid4(), code="RS-2", name="Outra")
            session.add(other_school)
            await session.flush()
            other_person = Person(
                id=uuid.uuid4(), school_id=other_school.id, full_name="Fulano",
                external_id="PER-X",
            )
            session.add(other_person)
            await session.flush()
            outsider = Student(
                id=uuid.uuid4(), school_id=other_school.id, person_id=other_person.id,
                external_id="STU-X",
            )
            session.add(outsider)
            await session.commit()

            batch, pages = await self._batch_with_pages(session, school, klass, prompt, ["x"])
            service = EssayBatchService(session)
            with self.assertRaises(ValueError):
                await service.resolve_page(
                    batch_id=batch.id, page_id=pages[0].id, student_id=outsider.id
                )

    async def test_rejects_a_page_with_no_recognized_text(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(session, ["Ana Lúcia Ferreira"])
            batch, pages = await self._batch_with_pages(session, school, klass, prompt, ["   "])
            service = EssayBatchService(session)
            with self.assertRaises(ValueError) as ctx:
                await service.resolve_page(
                    batch_id=batch.id, page_id=pages[0].id,
                    student_id=students["Ana Lúcia Ferreira"],
                )
            self.assertIn("texto", str(ctx.exception).lower())
            refreshed = await session.get(EssayBatchPage, pages[0].id)
            self.assertEqual(refreshed.status, "NEEDS_REVIEW")
            self.assertIsNone(refreshed.matched_student_id)

    async def test_resolving_a_page_after_the_run_was_already_corrected_reruns_the_ai(self):
        """Problema 1 (CRITICAL) do fix-round-1-brief.md: folha 1 casa
        automaticamente e ja e corrigida; folha 2 so e resolvida pelo
        professor DEPOIS. A segunda correcao precisa refletir o texto
        completo (as duas folhas), nao a correcao antiga intacta sobre
        metade do texto."""
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(session, ["Ana Lúcia Ferreira"])
            student_id = students["Ana Lúcia Ferreira"]
            batch, pages = await self._batch_with_pages(
                session, school, klass, prompt,
                ["Primeira parte.", "Segunda parte."],
            )
            # Folha 1 "ja casou automaticamente" (como o process_batch real
            # deixaria); folha 2 fica NEEDS_REVIEW, sem aluno casado - exatamente
            # como cairia na fila de resolucao manual por cabecalho ilegivel.
            pages[0].matched_student_id = student_id
            await session.commit()
            service = EssayBatchService(session, correction_factory=DumbCorrectionService)

            # O lote materializa a corrida [folha 1] sozinha e ela e corrigida.
            first_submission_ids = await service.materialize_batch(batch.id)
            self.assertEqual(len(first_submission_ids), 1)
            await service.run_corrections(first_submission_ids)

            first_correction = await session.scalar(
                select(EssayCorrection).where(
                    EssayCorrection.essay_submission_id == first_submission_ids[0]
                )
            )
            self.assertIsNotNone(first_correction)
            self.assertEqual(
                first_correction.ai_output["corrected_text"], "Primeira parte.",
                "a primeira correcao so viu a folha 1 - e o comportamento correto ate aqui",
            )
            first_correction_id = first_correction.id

            # O professor resolve a folha 2 - agora as duas folhas formam uma
            # corrida consecutiva do mesmo aluno.
            second_submission_ids = await service.resolve_page(
                batch_id=batch.id, page_id=pages[1].id, student_id=student_id,
            )
            self.assertEqual(second_submission_ids, first_submission_ids)
            await service.run_corrections(second_submission_ids)

            submission = await session.get(EssaySubmission, first_submission_ids[0])
            self.assertIn("Primeira parte.", submission.canonical_text)
            self.assertIn("Segunda parte.", submission.canonical_text)

            corrections = (await session.execute(
                select(EssayCorrection).where(
                    EssayCorrection.essay_submission_id == first_submission_ids[0]
                )
            )).scalars().all()
            self.assertEqual(
                len(corrections), 1,
                "a correcao antiga precisa ser invalidada, nao duplicada ao lado da nova",
            )
            final_correction = corrections[0]
            self.assertNotEqual(
                final_correction.id, first_correction_id,
                "a correcao antiga (so com a folha 1) precisa ter sido substituida",
            )
            self.assertIn(
                "Segunda parte.", final_correction.ai_output["corrected_text"],
                "a correcao final precisa refletir o texto COMPLETO da submissao, "
                "nao a correcao antiga rodada so sobre a folha 1",
            )
            self.assertIn("Primeira parte.", final_correction.ai_output["corrected_text"])


if __name__ == "__main__":
    unittest.main()
