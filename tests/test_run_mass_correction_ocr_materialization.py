# tests/test_run_mass_correction_ocr_materialization.py
"""Task 11: liga o estagio OCR da correcao em massa a materializacao real de
EssaySubmission - achado CRITICO (C1) da revisao final de todo o branch.

_collect_ocr_pending_lines/_apply_ocr_results (scripts/run_mass_correction.py)
processavam a pagina inteira, de uma vez, sem recortar cabecalho/corpo, sem
casar o aluno e sem nunca chamar materialize_batch - nenhuma EssaySubmission
era criada e o estagio CORRECTION nunca encontrava trabalho pendente.

Reusa 100% do caminho sincrono ja testado (services/essay_batch.py):
_crop_regions, parse_header_text, match_student, class_roster,
materialize_batch - nunca duplica essa logica. Mesmo padrao de pagina PNG real
"carimbada" (via pymupdf) que tests/test_r4_essay_batch_processing.py usa,
porque _crop_regions de verdade precisa de uma imagem de verdade para
recortar.
"""
from __future__ import annotations

import shutil
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayBatchPage,
    EssayBatchUpload,
    EssayPrompt,
    EssaySubmission,
    GradeLevel,
    Person,
    PromptAssignment,
    School,
    Segment,
    Student,
    StudentEnrollment,
)

from scripts.run_mass_correction import _apply_ocr_results, _collect_ocr_pending_lines


def _write_stamped_page(path: Path, *, header_text: str, body_text: str) -> Path:
    """Uma pagina real com ``header_text`` no topo (regiao de cabecalho) e
    ``body_text`` no meio (regiao de corpo) - mesmo padrao de
    tests/test_r4_essay_batch_processing.py::_write_stamped_page, so que aqui
    cada regiao carrega um texto proprio em vez de repetir a mesma chave, pra
    simular o que o OCR de verdade devolveria de cada recorte."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595.44, height=842.40)
    page.insert_text((50, 60), header_text, fontsize=11)
    page.insert_text((50, 500), body_text, fontsize=11)
    page.get_pixmap(dpi=100).save(str(path))
    doc.close()
    return path


def _ocr_result_line(custom_id: str, content: str) -> dict:
    return {
        "custom_id": custom_id,
        "response": {
            "status_code": 200,
            "body": {"choices": [{"message": {"content": content}}]},
        },
        "error": None,
    }


class RunMassCorrectionOcrMaterializationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="t11_ocr_"))

    async def asyncTearDown(self):
        await self.engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def _seed(self, session, *, student_full_name: str = "Ana Lucia Ferreira"):
        """Escola + hierarquia academica minima + 1 aluno ativo na turma +
        proposta atribuida a turma + 1 EssayBatchUpload com 1 EssayBatchPage
        pendente de OCR - mesmo padrao de
        tests/test_run_mass_correction_scoring_sampling.py::_seed_correction
        (hierarquia academica) combinado com
        tests/test_r4_essay_batch_processing.py::_seed (lote/roster)."""
        school = School(id=uuid.uuid4(), code="T11", name="Escola T11")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-T11")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="3a", external_id="GRADE-T11",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-T11")
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="3A", external_id="TURMA-T11",
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
        await session.flush()

        person = Person(
            id=uuid.uuid4(), school_id=school.id, full_name=student_full_name,
            external_id="PER-T11",
        )
        session.add(person)
        await session.flush()
        student = Student(
            id=uuid.uuid4(), school_id=school.id, person_id=person.id, external_id="STU-T11",
        )
        session.add(student)
        await session.flush()
        session.add(StudentEnrollment(
            id=uuid.uuid4(), school_id=school.id, student_id=student.id,
            class_id=klass.id, status="ACTIVE", external_id="ENR-T11",
        ))
        await session.flush()

        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, uploaded_by_external_identity="prof",
            status="PROCESSING", total_pages=1,
        )
        session.add(batch)
        await session.flush()

        page_path = _write_stamped_page(
            self.tmp_dir / "p1.png",
            header_text="stub - substituido no teste", body_text="stub",
        )
        page = EssayBatchPage(
            id=uuid.uuid4(), batch_id=batch.id, page_number=1,
            storage_uri=str(page_path), status="NEEDS_REVIEW",
        )
        session.add(page)
        await session.flush()
        await session.commit()
        return school, batch, page, student

    async def test_collect_ocr_pending_lines_crops_each_page_into_header_and_body(self):
        async with self.session_factory() as session:
            school, _batch, page, _student = await self._seed(session)

            with patch.dict("os.environ", {"OPENAI_VISION_MODEL": "gpt-vision-test"}):
                lines = await _collect_ocr_pending_lines(session, school_id=school.id)

            self.assertEqual(len(lines), 2)
            custom_ids = {line["custom_id"] for line in lines}
            self.assertEqual(custom_ids, {f"{page.id}:header", f"{page.id}:body"})
            for line in lines:
                self.assertEqual(line["method"], "POST")
                self.assertEqual(line["url"], "/v1/chat/completions")
                self.assertIn("messages", line["body"])

    async def test_apply_ocr_results_materializes_a_submission_end_to_end(self):
        async with self.session_factory() as session:
            school, batch, page, student = await self._seed(
                session, student_full_name="Ana Lúcia Ferreira"
            )
            result_lines = [
                _ocr_result_line(f"{page.id}:header", "NOME: ANA LUCIA FERREIRA"),
                _ocr_result_line(f"{page.id}:body", "Texto da redacao da Ana."),
            ]

            await _apply_ocr_results(session, result_lines)

            refreshed_page = await session.get(EssayBatchPage, page.id)
            self.assertEqual(refreshed_page.ocr_name_raw, "ANA LUCIA FERREIRA")
            self.assertIsNone(refreshed_page.ocr_cpf_raw)
            self.assertEqual(refreshed_page.ocr_body_text, "Texto da redacao da Ana.")
            self.assertEqual(refreshed_page.matched_student_id, student.id)
            self.assertEqual(refreshed_page.status, "MATCHED_AUTO")
            self.assertIsNotNone(refreshed_page.essay_submission_id)

            submission = await session.get(EssaySubmission, refreshed_page.essay_submission_id)
            self.assertIsNotNone(submission)
            self.assertEqual(submission.status, "SUBMITTED")
            self.assertEqual(submission.anchor_mode, "TEXT_OFFSET")
            self.assertTrue((submission.canonical_text or "").strip())
            self.assertEqual(submission.student_id, student.id)

            refreshed_batch = await session.get(EssayBatchUpload, batch.id)
            self.assertEqual(refreshed_batch.status, "DONE")

    async def test_apply_ocr_results_leaves_unmatched_page_pending_for_manual_review(self):
        async with self.session_factory() as session:
            _school, batch, page, _student = await self._seed(
                session, student_full_name="Ana Lúcia Ferreira"
            )
            result_lines = [
                _ocr_result_line(f"{page.id}:header", "NOME: CARLOS DESCONHECIDO"),
                _ocr_result_line(f"{page.id}:body", "Texto de um aluno nao cadastrado."),
            ]

            await _apply_ocr_results(session, result_lines)

            refreshed_page = await session.get(EssayBatchPage, page.id)
            self.assertIsNone(refreshed_page.matched_student_id)
            self.assertEqual(refreshed_page.status, "NEEDS_REVIEW")
            self.assertIsNone(refreshed_page.essay_submission_id)

            submissions = (await session.execute(select(EssaySubmission))).scalars().all()
            self.assertEqual(submissions, [])

    async def test_collect_ocr_pending_lines_caps_pages_read_per_call(self):
        """Fix round 2, achado 1: a coleta de OCR precisa ter um teto de
        PAGINAS lidas por chamada (_MAX_OCR_PAGES_PER_COLLECTION) - sem isso,
        a 50.000 paginas ela materializaria ~150-250GB de imagem em base64 na
        memoria antes de qualquer fracionamento/submissao acontecer. Reduz a
        constante para 2 via mock (em vez de gerar centenas de imagens reais)
        e semeia 5 paginas pendentes - confirma que so 2 delas (4 linhas)
        saem da coleta, nunca as 5."""
        async with self.session_factory() as session:
            school, batch, first_page, _student = await self._seed(session)

            extra_pages = []
            for page_number in range(2, 6):  # +4 paginas pendentes (total 5)
                page_path = _write_stamped_page(
                    self.tmp_dir / f"p{page_number}.png",
                    header_text="stub", body_text="stub",
                )
                page = EssayBatchPage(
                    id=uuid.uuid4(), batch_id=batch.id, page_number=page_number,
                    storage_uri=str(page_path), status="NEEDS_REVIEW",
                )
                session.add(page)
                extra_pages.append(page)
            await session.flush()
            await session.commit()

            all_pending_ids = {first_page.id, *(p.id for p in extra_pages)}
            self.assertEqual(len(all_pending_ids), 5)

            with (
                patch.dict("os.environ", {"OPENAI_VISION_MODEL": "gpt-vision-test"}),
                patch("scripts.run_mass_correction._MAX_OCR_PAGES_PER_COLLECTION", 2),
            ):
                lines = await _collect_ocr_pending_lines(session, school_id=school.id)

            # 2 paginas * 2 linhas (header + body) cada = 4, nunca 5*2=10
            self.assertEqual(len(lines), 4)
            page_ids_in_result = {
                uuid.UUID(line["custom_id"].rpartition(":")[0]) for line in lines
            }
            self.assertEqual(len(page_ids_in_result), 2)
            self.assertTrue(page_ids_in_result.issubset(all_pending_ids))

    async def test_an_unreadable_page_is_skipped_without_aborting_the_whole_collection(self):
        async with self.session_factory() as session:
            school, batch, good_page, _student = await self._seed(session)

            bad_page = EssayBatchPage(
                id=uuid.uuid4(), batch_id=batch.id, page_number=2,
                storage_uri="/tmp/nao-existe-de-verdade.png", status="NEEDS_REVIEW",
            )
            session.add(bad_page)
            await session.flush()
            await session.commit()

            with patch.dict("os.environ", {"OPENAI_VISION_MODEL": "gpt-vision-test"}):
                lines = await _collect_ocr_pending_lines(session, school_id=school.id)

            # (a) nao levanta excecao (chegamos aqui) - (b) so a pagina legivel aparece
            self.assertEqual(len(lines), 2)
            custom_ids = {line["custom_id"] for line in lines}
            self.assertEqual(custom_ids, {f"{good_page.id}:header", f"{good_page.id}:body"})

            # (c) a pagina ruim foi degradada, nao derrubou a coleta
            refreshed_bad_page = await session.get(EssayBatchPage, bad_page.id)
            self.assertEqual(refreshed_bad_page.status, "NEEDS_REVIEW")
            self.assertEqual(refreshed_bad_page.ocr_body_text, "")

            # (d) chamando de novo, a pagina ruim nao aparece mais (sem loop de retry
            # infinito) - a boa continua pendente e continua aparecendo normalmente
            with patch.dict("os.environ", {"OPENAI_VISION_MODEL": "gpt-vision-test"}):
                lines_again = await _collect_ocr_pending_lines(session, school_id=school.id)
            custom_ids_again = {line["custom_id"] for line in lines_again}
            self.assertEqual(custom_ids_again, {f"{good_page.id}:header", f"{good_page.id}:body"})


if __name__ == "__main__":
    unittest.main()
