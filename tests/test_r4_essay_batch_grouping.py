"""R4 lote - agrupamento de paginas consecutivas em uma EssaySubmission.

Regra (spec s4.4 reconciliada com s7): uma CORRIDA de paginas consecutivas com
o mesmo matched_student_id vira UMA submissao; uma segunda corrida do mesmo
aluno, separada por paginas de outra pessoa, vira OUTRA submissao.
"""

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
    AcademicYear, Class, EssayBatchPage, EssayPrompt, EssaySubmission,
    EssaySubmissionPage, GradeLevel, Person, PromptAssignment, School, Segment,
    Student, StudentEnrollment,
)
from agente_ia_edu.providers.models import EssayOcrToken, EssayPageTranscriptionResult
from agente_ia_edu.services.essay_batch import EssayBatchService, consecutive_runs
from agente_ia_edu.services.material_storage import MaterialStorage


class ScriptedTranscriber:
    def __init__(self, script: dict[str, tuple[str, str]]):
        self.script = script

    async def transcribe_page(self, request):
        import pymupdf

        doc = pymupdf.open(str(request.image_path))
        try:
            stamped = doc[0].get_text().strip().splitlines()
        finally:
            doc.close()
        # Workaround (from Task 6): PNG rasterized pages have no text layer,
        # so get_text() always returns empty. Extract key from filename instead.
        key = ""
        if stamped:
            key = next((line.strip() for line in stamped if line.strip()), "")
        if not key:
            # Fallback to filename stem (before the underscore)
            stem = request.image_path.stem
            if "_header" in stem or "_body" in stem:
                key = stem.rsplit("_", 1)[0]
            else:
                key = stem
        header, body = self.script.get(key, ("", ""))
        text = header if "_header" in request.image_path.name else body
        return EssayPageTranscriptionResult(
            tokens=(EssayOcrToken(text=text, confidence=0.95, start=0, end=len(text)),),
            provider="fake", model="fake-1",
        )


class RecordingCorrectionService:
    corrected: list[uuid.UUID] = []

    def __init__(self, session):
        self.session = session

    async def correct(self, essay_submission_id):
        RecordingCorrectionService.corrected.append(essay_submission_id)


def _write_stamped_page(path: Path, key: str) -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595.44, height=842.40)
    page.insert_text((50, 60), key, fontsize=11)
    page.insert_text((50, 500), key, fontsize=11)
    page.get_pixmap(dpi=100).save(str(path))
    doc.close()
    return path


class ConsecutiveRunsTests(unittest.TestCase):
    def _page(self, number, student_id):
        return EssayBatchPage(
            id=uuid.uuid4(), batch_id=uuid.uuid4(), page_number=number,
            storage_uri=f"/p{number}.png", matched_student_id=student_id,
        )

    def test_groups_consecutive_pages_of_the_same_student(self):
        ana, joao = uuid.uuid4(), uuid.uuid4()
        pages = [self._page(1, ana), self._page(2, ana), self._page(3, joao)]
        runs = consecutive_runs(pages)
        self.assertEqual([[p.page_number for p in run] for run in runs], [[1, 2], [3]])

    def test_a_second_run_of_the_same_student_is_a_separate_group(self):
        ana, joao = uuid.uuid4(), uuid.uuid4()
        pages = [self._page(1, ana), self._page(2, joao), self._page(3, ana)]
        runs = consecutive_runs(pages)
        self.assertEqual([[p.page_number for p in run] for run in runs], [[1], [2], [3]])
        self.assertEqual(runs[0][0].matched_student_id, ana)
        self.assertEqual(runs[2][0].matched_student_id, ana)

    def test_unmatched_pages_break_a_run_and_are_never_grouped(self):
        ana = uuid.uuid4()
        pages = [self._page(1, ana), self._page(2, None), self._page(3, ana)]
        runs = consecutive_runs(pages)
        self.assertEqual([[p.page_number for p in run] for run in runs], [[1], [3]])

    def test_a_gap_in_page_numbers_breaks_a_run(self):
        ana = uuid.uuid4()
        pages = [self._page(1, ana), self._page(3, ana)]
        runs = consecutive_runs(pages)
        self.assertEqual([[p.page_number for p in run] for run in runs], [[1], [3]])

    def test_no_pages_means_no_runs(self):
        self.assertEqual(consecutive_runs([]), [])


class MaterializeBatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        RecordingCorrectionService.corrected = []
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="r4_group_"))
        self.storage = MaterialStorage(root=self.tmp_dir / "storage")
        # Pausa entre paginas e pra producao (rajada na API de visao) - sem
        # serventia aqui (transcritor falso) e so deixaria a suite lenta.
        self._original_pacing = EssayBatchService._PAGE_PACING_SECONDS
        EssayBatchService._PAGE_PACING_SECONDS = 0.0

    async def asyncTearDown(self):
        EssayBatchService._PAGE_PACING_SECONDS = self._original_pacing
        await self.engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def _seed(self, session, student_names):
        school = School(id=uuid.uuid4(), code="GR-1", name="Escola")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-GR")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="3a", external_id="GRADE-GR",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-GR")
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="3A", external_id="TURMA-GR",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof",
        )
        session.add_all([klass, prompt])
        await session.flush()
        assignment = PromptAssignment(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, assigned_by_external_identity="prof",
        )
        session.add(assignment)
        students = {}
        for index, full_name in enumerate(student_names):
            person = Person(
                id=uuid.uuid4(), school_id=school.id, full_name=full_name,
                external_id=f"PER-G{index}",
            )
            session.add(person)
            await session.flush()
            student = Student(
                id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                external_id=f"STU-G{index}",
            )
            session.add(student)
            await session.flush()
            session.add(StudentEnrollment(
                id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                class_id=klass.id, status="ACTIVE", external_id=f"ENR-G{index}",
            ))
            students[full_name] = student.id
        await session.commit()
        return school, klass, prompt, assignment, students

    async def _run(self, session, school, klass, prompt, keys, script):
        paths = [_write_stamped_page(self.tmp_dir / f"{key}.png", key) for key in keys]
        service = EssayBatchService(
            session, storage=self.storage, transcriber=ScriptedTranscriber(script),
            correction_factory=RecordingCorrectionService,
        )
        created = await service.create_batch(
            school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
            uploaded_by_external_identity="prof", source_paths=paths,
        )
        await session.commit()
        await service.process_batch(created["id"])
        return service, created

    async def _pages(self, session, batch_id):
        return (await session.execute(
            select(EssayBatchPage)
            .where(EssayBatchPage.batch_id == batch_id)
            .order_by(EssayBatchPage.page_number)
        )).scalars().all()

    async def test_two_consecutive_pages_become_one_submission(self):
        async with self.session_factory() as session:
            school, klass, prompt, assignment, students = await self._seed(
                session, ["Ana Lúcia Ferreira"]
            )
            header = "NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira"
            await self._run(session, school, klass, prompt, ["p1", "p2"], {
                "p1": (header, "Primeira parte."),
                "p2": (header, "Segunda parte."),
            })

            submissions = (await session.execute(select(EssaySubmission))).scalars().all()
            self.assertEqual(len(submissions), 1)
            submission = submissions[0]
            self.assertEqual(submission.student_id, students["Ana Lúcia Ferreira"])
            self.assertEqual(submission.prompt_assignment_id, assignment.id)
            self.assertEqual(submission.status, "SUBMITTED")
            self.assertEqual(submission.anchor_mode, "TEXT_OFFSET")
            self.assertEqual(submission.mode, "PHOTO")
            self.assertIsNotNone(submission.submitted_at)
            self.assertIn("Primeira parte.", submission.canonical_text)
            self.assertIn("Segunda parte.", submission.canonical_text)
            self.assertLess(
                submission.canonical_text.index("Primeira parte."),
                submission.canonical_text.index("Segunda parte."),
            )
            self.assertIsNotNone(submission.normalized_text_hash)

    async def test_the_batch_never_creates_essay_submission_pages(self):
        async with self.session_factory() as session:
            school, klass, prompt, _assignment, _students = await self._seed(
                session, ["Ana Lúcia Ferreira"]
            )
            await self._run(session, school, klass, prompt, ["p1"], {
                "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Texto."),
            })
            pages = (await session.execute(select(EssaySubmissionPage))).scalars().all()
            self.assertEqual(pages, [])

    async def test_pages_are_marked_matched_auto_and_point_at_the_submission(self):
        async with self.session_factory() as session:
            school, klass, prompt, _assignment, _students = await self._seed(
                session, ["Ana Lúcia Ferreira"]
            )
            _service, created = await self._run(session, school, klass, prompt, ["p1", "p2"], {
                "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Um."),
                "p2": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Dois."),
            })
            submission = (await session.execute(select(EssaySubmission))).scalars().one()
            pages = await self._pages(session, created["id"])
            self.assertEqual([p.status for p in pages], ["MATCHED_AUTO", "MATCHED_AUTO"])
            self.assertEqual({p.essay_submission_id for p in pages}, {submission.id})

    async def test_a_second_run_of_the_same_student_creates_a_second_submission(self):
        async with self.session_factory() as session:
            school, klass, prompt, _assignment, students = await self._seed(
                session, ["Ana Lúcia Ferreira", "João da Silva"]
            )
            ana = "NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira"
            joao = "NOME COMPLETO DO PARTICIPANTE Joao da Silva"
            await self._run(session, school, klass, prompt, ["p1", "p2", "p3"], {
                "p1": (ana, "Primeira redacao da Ana."),
                "p2": (joao, "Redacao do Joao."),
                "p3": (ana, "Segunda redacao da Ana."),
            })
            ana_submissions = (await session.execute(
                select(EssaySubmission).where(
                    EssaySubmission.student_id == students["Ana Lúcia Ferreira"]
                )
            )).scalars().all()
            self.assertEqual(len(ana_submissions), 2)
            self.assertEqual(
                len({s.essay_id for s in ana_submissions}), 2,
                "cada corrida e uma redacao propria, nunca versoes do mesmo essay_id",
            )

    async def test_a_run_with_no_recognized_text_never_becomes_a_submission(self):
        async with self.session_factory() as session:
            school, klass, prompt, _assignment, _students = await self._seed(
                session, ["Ana Lúcia Ferreira"]
            )
            _service, created = await self._run(session, school, klass, prompt, ["p1"], {
                "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "   "),
            })
            self.assertEqual((await session.execute(select(EssaySubmission))).scalars().all(), [])
            page = (await self._pages(session, created["id"]))[0]
            self.assertEqual(page.status, "NEEDS_REVIEW")
            self.assertIsNone(page.essay_submission_id)
            # O nome lido continua guardado - e a pista que o professor ve.
            self.assertEqual(page.ocr_name_raw, "ANA LUCIA FERREIRA")

    async def test_each_created_submission_is_sent_to_correction_once(self):
        async with self.session_factory() as session:
            school, klass, prompt, _assignment, _students = await self._seed(
                session, ["Ana Lúcia Ferreira", "João da Silva"]
            )
            await self._run(session, school, klass, prompt, ["p1", "p2"], {
                "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Texto da Ana."),
                "p2": ("NOME COMPLETO DO PARTICIPANTE Joao da Silva", "Texto do Joao."),
            })
            submissions = (await session.execute(select(EssaySubmission))).scalars().all()
            self.assertEqual(len(submissions), 2)
            self.assertEqual(
                sorted(str(i) for i in RecordingCorrectionService.corrected),
                sorted(str(s.id) for s in submissions),
            )

    async def test_a_failing_correction_never_rolls_back_the_submission(self):
        class ExplodingCorrectionService:
            def __init__(self, session):
                self.session = session

            async def correct(self, essay_submission_id):
                raise RuntimeError("provedor fora do ar")

        async with self.session_factory() as session:
            school, klass, prompt, _assignment, _students = await self._seed(
                session, ["Ana Lúcia Ferreira"]
            )
            paths = [_write_stamped_page(self.tmp_dir / "p1.png", "p1")]
            service = EssayBatchService(
                session, storage=self.storage,
                transcriber=ScriptedTranscriber({
                    "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Texto."),
                }),
                correction_factory=ExplodingCorrectionService,
            )
            created = await service.create_batch(
                school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
                uploaded_by_external_identity="prof", source_paths=paths,
            )
            await session.commit()
            await service.process_batch(created["id"])

            submission = (await session.execute(select(EssaySubmission))).scalars().one()
            self.assertEqual(submission.status, "SUBMITTED")


if __name__ == "__main__":
    unittest.main()
