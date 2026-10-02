"""R4 lote - processamento em segundo plano, pagina a pagina.

Nenhuma chamada de IA: o transcritor e falso e devolve o cabecalho/corpo
combinados por numero de pagina, a partir de um roteiro montado no teste.
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
    AcademicYear, Class, EssayBatchPage, EssayBatchUpload, EssayPrompt, GradeLevel,
    Person, PromptAssignment, School, Segment, Student, StudentEnrollment,
)
from agente_ia_edu.providers.errors import ProviderError
from agente_ia_edu.providers.models import EssayOcrToken, EssayPageTranscriptionResult
from agente_ia_edu.services.essay_batch import EssayBatchService
from agente_ia_edu.services.material_storage import MaterialStorage


class ScriptedTranscriber:
    """``script`` mapeia o texto impresso na pagina -> (cabecalho, corpo).

    A imagem de cada pagina do lote carrega o proprio identificador impresso no
    topo E no corpo, entao o recorte de cabecalho e o de corpo continuam
    identificaveis depois de separados - e assim o transcritor falso sabe qual
    pagina esta lendo sem precisar de OCR de verdade.
    """

    def __init__(self, script: dict[str, tuple[str, str]], *, fail_pages: set[str] | None = None):
        self.script = script
        self.fail_pages = fail_pages or set()

    async def transcribe_page(self, request):
        import pymupdf

        doc = pymupdf.open(str(request.image_path))
        try:
            stamped = doc[0].get_text().strip().splitlines()
        finally:
            doc.close()
        key = next((line.strip() for line in stamped if line.strip()), "")
        # Se get_text() não retorna nada (PNG rasterizado), extrai da chave do nome do arquivo
        if not key:
            # Tenta extrair de padrões "page_N_header.png" ou "p1_header.png"
            stem = request.image_path.stem
            if "_" in stem:
                key = stem.rsplit("_", 1)[0]
        if key in self.fail_pages:
            raise ProviderError("transcricao indisponivel")
        header, body = self.script.get(key, ("", ""))
        text = header if "_header" in request.image_path.name else body
        return EssayPageTranscriptionResult(
            tokens=(EssayOcrToken(text=text, confidence=0.95, start=0, end=len(text)),),
            provider="fake", model="fake-1",
        )


def _write_stamped_page(path: Path, key: str) -> Path:
    """Uma pagina com ``key`` impresso duas vezes: no topo (regiao de cabecalho)
    e no meio (regiao de corpo), pra que as duas metades continuem rastreaveis."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595.44, height=842.40)
    page.insert_text((50, 60), key, fontsize=11)
    page.insert_text((50, 500), key, fontsize=11)
    page.get_pixmap(dpi=100).save(str(path))
    doc.close()
    return path


class ProcessBatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="r4_proc_"))
        self.storage = MaterialStorage(root=self.tmp_dir / "storage")

    async def asyncTearDown(self):
        await self.engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def _seed(self, session, student_names):
        school = School(id=uuid.uuid4(), code="PB-1", name="Escola")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-PB")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="3a", external_id="GRADE-PB",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-PB")
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="3A", external_id="TURMA-PB",
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
        for index, (full_name, document_number) in enumerate(student_names):
            person = Person(
                id=uuid.uuid4(), school_id=school.id, full_name=full_name,
                document_number=document_number, external_id=f"PER-{index}",
            )
            session.add(person)
            await session.flush()
            student = Student(
                id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                external_id=f"STU-{index}",
            )
            session.add(student)
            await session.flush()
            session.add(StudentEnrollment(
                id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                class_id=klass.id, status="ACTIVE", external_id=f"ENR-{index}",
            ))
            students[full_name] = student.id
        await session.commit()
        return school, klass, prompt, students

    async def _make_batch(self, session, school, klass, prompt, keys, *, transcriber):
        paths = [
            _write_stamped_page(self.tmp_dir / f"{key}.png", key) for key in keys
        ]
        service = EssayBatchService(session, storage=self.storage, transcriber=transcriber)
        created = await service.create_batch(
            school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
            uploaded_by_external_identity="prof", source_paths=paths,
        )
        await session.commit()
        return service, created

    async def _pages(self, session, batch_id):
        return (await session.execute(
            select(EssayBatchPage)
            .where(EssayBatchPage.batch_id == batch_id)
            .order_by(EssayBatchPage.page_number)
        )).scalars().all()

    async def test_class_roster_only_lists_active_enrollments(self):
        async with self.session_factory() as session:
            school, klass, _prompt, students = await self._seed(
                session, [("Ana Lúcia Ferreira", "11122233344"), ("João da Silva", None)]
            )
            enrollment = await session.scalar(
                select(StudentEnrollment).where(
                    StudentEnrollment.student_id == students["João da Silva"]
                )
            )
            enrollment.status = "EXITED"
            await session.commit()

            roster = await EssayBatchService(session, storage=self.storage).class_roster(
                school_id=school.id, class_id=klass.id
            )
            self.assertEqual(
                roster, [(students["Ana Lúcia Ferreira"], "Ana Lúcia Ferreira", "11122233344")]
            )

    async def test_matches_each_page_and_marks_the_batch_done(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(
                session, [("Ana Lúcia Ferreira", None), ("João da Silva", None)]
            )
            transcriber = ScriptedTranscriber({
                "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira CPF 111.222.333-44",
                       "Texto da Ana."),
                "p2": ("NOME COMPLETO DO PARTICIPANTE Joao da Silva", "Texto do Joao."),
            })
            service, created = await self._make_batch(
                session, school, klass, prompt, ["p1", "p2"], transcriber=transcriber
            )

            await service.process_batch(created["id"])

            batch = await session.get(EssayBatchUpload, created["id"])
            self.assertEqual(batch.status, "DONE")
            pages = await self._pages(session, created["id"])
            self.assertEqual(pages[0].matched_student_id, students["Ana Lúcia Ferreira"])
            self.assertEqual(pages[0].ocr_name_raw, "ANA LUCIA FERREIRA")
            self.assertEqual(pages[0].ocr_cpf_raw, "11122233344")
            self.assertEqual(pages[0].ocr_body_text, "Texto da Ana.")
            self.assertEqual(pages[1].matched_student_id, students["João da Silva"])
            self.assertIsNone(pages[1].ocr_cpf_raw)

    async def test_unknown_name_stays_needs_review_with_no_student(self):
        async with self.session_factory() as session:
            school, klass, prompt, _students = await self._seed(
                session, [("Ana Lúcia Ferreira", None)]
            )
            transcriber = ScriptedTranscriber({
                "p1": ("NOME COMPLETO DO PARTICIPANTE Carlos Mendes", "Texto do Carlos."),
            })
            service, created = await self._make_batch(
                session, school, klass, prompt, ["p1"], transcriber=transcriber
            )

            await service.process_batch(created["id"])

            page = (await self._pages(session, created["id"]))[0]
            self.assertIsNone(page.matched_student_id)
            self.assertEqual(page.status, "NEEDS_REVIEW")
            self.assertEqual(page.ocr_name_raw, "CARLOS MENDES")
            self.assertEqual(page.ocr_body_text, "Texto do Carlos.")

    async def test_homonyms_in_the_same_class_go_to_manual_review(self):
        async with self.session_factory() as session:
            school, klass, prompt, _students = await self._seed(
                session, [("João da Silva", "11111111111"), ("Joao Da Silva", "22222222222")]
            )
            transcriber = ScriptedTranscriber({
                "p1": ("NOME COMPLETO DO PARTICIPANTE Joao da Silva CPF 222.222.222-22",
                       "Texto ambiguo."),
            })
            service, created = await self._make_batch(
                session, school, klass, prompt, ["p1"], transcriber=transcriber
            )

            await service.process_batch(created["id"])

            page = (await self._pages(session, created["id"]))[0]
            self.assertIsNone(page.matched_student_id)
            self.assertEqual(page.status, "NEEDS_REVIEW")
            # O CPF foi lido e guardado: e a pista que desempata na tela do professor.
            self.assertEqual(page.ocr_cpf_raw, "22222222222")

    async def test_one_failing_page_never_takes_down_the_batch(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(
                session, [("Ana Lúcia Ferreira", None)]
            )
            transcriber = ScriptedTranscriber(
                {
                    "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Texto da Ana."),
                    "p2": ("", ""),
                },
                fail_pages={"p2"},
            )
            service, created = await self._make_batch(
                session, school, klass, prompt, ["p1", "p2"], transcriber=transcriber
            )

            await service.process_batch(created["id"])

            batch = await session.get(EssayBatchUpload, created["id"])
            self.assertEqual(batch.status, "DONE")
            pages = await self._pages(session, created["id"])
            self.assertEqual(pages[0].matched_student_id, students["Ana Lúcia Ferreira"])
            self.assertIsNone(pages[1].matched_student_id)
            self.assertIsNone(pages[1].ocr_name_raw)
            self.assertEqual(pages[1].status, "NEEDS_REVIEW")

    async def test_each_page_is_committed_as_soon_as_it_finishes(self):
        """Spec s5: o registro de cada pagina e commitado assim que aquela
        pagina termina, e nao so no fim do lote - e o que faz GET
        /essay-batches/{id} refletir o progresso real com o lote ainda rodando.

        Checado contando QUANTAS paginas ja tinham sido processadas no momento
        de cada commit (e nao lendo de uma segunda sessao): com SQLite
        in-memory + StaticPool todas as sessoes compartilham a MESMA conexao,
        entao uma segunda sessao enxergaria ate o que ainda nao foi commitado -
        o teste passaria sem provar nada.
        """
        async with self.session_factory() as session:
            school, klass, prompt, _students = await self._seed(
                session, [("Ana Lúcia Ferreira", None)]
            )
            transcriber = ScriptedTranscriber({
                "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Texto da Ana."),
                "p2": ("NOME COMPLETO DO PARTICIPANTE Joao da Silva", "Outro texto."),
            })
            service, created = await self._make_batch(
                session, school, klass, prompt, ["p1", "p2"], transcriber=transcriber
            )

            processed = {"count": 0}
            commits: list[int] = []
            original_process = service._process_page
            original_commit = service.session.commit

            async def _counting_process(page, roster):
                await original_process(page, roster)
                processed["count"] += 1

            async def _recording_commit():
                commits.append(processed["count"])
                await original_commit()

            service._process_page = _counting_process
            service.session.commit = _recording_commit
            await service.process_batch(created["id"])

            self.assertEqual(
                commits[:2], [1, 2],
                "houve um commit logo apos a primeira pagina e outro apos a segunda",
            )

    async def _seed_two_classes_same_grade(
        self, session, *, nome_aluno_a: str = "Aluno Turma A", nome_aluno_b: str = "Aluno Turma B"
    ) -> dict:
        """2 turmas (A e B) da mesma serie, 1 aluno em cada - pra testar o
        escopo de serie (grade_level_id) de create_batch/process_batch, que
        precisa enxergar alunos de QUALQUER turma da serie, nao so de uma."""
        school = School(id=uuid.uuid4(), code="PB-SERIE", name="Escola")
        session.add(school)
        await session.flush()
        segment = Segment(
            id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-PB-SERIE"
        )
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="Serie Unica", external_id="GRADE-PB-SERIE",
        )
        year = AcademicYear(
            id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-PB-SERIE"
        )
        session.add_all([grade, year])
        await session.flush()
        class_a = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="Turma A", external_id="TURMA-PB-A",
        )
        class_b = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="Turma B", external_id="TURMA-PB-B",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof",
        )
        session.add_all([class_a, class_b, prompt])
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
            session.add(PromptAssignment(
                id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                class_id=klass.id, assigned_by_external_identity="prof",
            ))
            return student.id

        student_a = _make_student(nome_aluno_a, class_a)
        student_b = _make_student(nome_aluno_b, class_b)
        await session.flush()
        return {
            "school": school, "prompt": prompt, "grade": grade,
            "class_a": class_a, "class_b": class_b,
            "student_a": student_a, "student_b": student_b,
        }

    async def test_escopo_serie_casa_aluno_de_qualquer_turma_da_serie_e_atribui_a_turma_real(self):
        """2 turmas (A e B) da mesma serie, 1 aluno com nome unico em cada.
        Lote com escopo = serie inteira (grade_level_id, sem class_id): as
        duas paginas casam automatico (nome unico NA SERIE INTEIRA), e cada
        submissao fica atribuida a turma REAL do aluno (A ou B, nunca a
        'turma do lote', que nem existe neste escopo) - confirma que
        _assignment_for_student continua correto sem nenhuma mudanca."""
        async with self.session_factory() as session:
            seed = await self._seed_two_classes_same_grade(session)
            transcriber = ScriptedTranscriber({
                "a": ("NOME COMPLETO DO PARTICIPANTE Aluno Turma A", "Texto da redacao A."),
                "b": ("NOME COMPLETO DO PARTICIPANTE Aluno Turma B", "Texto da redacao B."),
            })
            paths = [
                _write_stamped_page(self.tmp_dir / f"{key}.png", key) for key in ["a", "b"]
            ]
            service = EssayBatchService(session, storage=self.storage, transcriber=transcriber)
            created = await service.create_batch(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_id=None, grade_level_id=seed["grade"].id,
                uploaded_by_external_identity="prof",
                source_paths=paths,
            )
            await session.commit()
            await service.process_batch(created["id"])

            status = await service.get_batch_status(created["id"])
            self.assertEqual(status["matched_count"], 2)
            self.assertEqual(status["needs_review_count"], 0)

    async def test_escopo_serie_homonimos_em_turmas_diferentes_caem_em_revisao_manual(self):
        """Mesmo cenario, mas os dois alunos tem o MESMO nome normalizado
        (homonimos em turmas diferentes da mesma serie) - match_student ve
        2 candidatos no roster ampliado e NUNCA desempata sozinho (nem por
        CPF): as duas paginas devem ficar NEEDS_REVIEW, nunca um match
        errado. match_student em si nao muda nesta leva - este teste prova
        que o roster maior nao quebra essa garantia."""
        async with self.session_factory() as session:
            seed = await self._seed_two_classes_same_grade(
                session, nome_aluno_a="Maria Silva", nome_aluno_b="Maria Silva"
            )
            transcriber = ScriptedTranscriber({
                "a": ("NOME COMPLETO DO PARTICIPANTE Maria Silva", "Texto da redacao 1."),
            })
            paths = [_write_stamped_page(self.tmp_dir / "a.png", "a")]
            service = EssayBatchService(session, storage=self.storage, transcriber=transcriber)
            created = await service.create_batch(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_id=None, grade_level_id=seed["grade"].id,
                uploaded_by_external_identity="prof",
                source_paths=paths,
            )
            await session.commit()
            await service.process_batch(created["id"])

            status = await service.get_batch_status(created["id"])
            self.assertEqual(status["matched_count"], 0)
            self.assertEqual(status["needs_review_count"], 1)


if __name__ == "__main__":
    unittest.main()
