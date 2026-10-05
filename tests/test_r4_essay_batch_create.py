"""R4 lote - criacao do lote: intake dos arquivos, limites e ordem das paginas."""

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
    PromptAssignment, School, Segment,
)
from agente_ia_edu.services.essay_batch import MAX_BATCH_PAGES, EssayBatchService
from agente_ia_edu.services.material_storage import MaterialStorage


def _write_image(path: Path, text: str = "pagina") -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595.44, height=842.40)
    page.insert_text((50, 60), text, fontsize=11)
    page.get_pixmap(dpi=100).save(str(path))
    doc.close()
    return path


def _write_pdf(path: Path, pages: int) -> Path:
    import pymupdf

    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page(width=595.44, height=842.40)
        page.insert_text((50, 60), f"folha {index + 1}", fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


class CreateBatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="r4_create_"))
        self.storage = MaterialStorage(root=self.tmp_dir / "storage")

    async def asyncTearDown(self):
        await self.engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def _seed(self, session, *, assign: bool = True):
        school = School(id=uuid.uuid4(), code="CB-1", name="Escola")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-CB")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="3a", external_id="GRADE-CB",
        )
        year = AcademicYear(
            id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-CB"
        )
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="3A", external_id="TURMA-CB",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof",
        )
        session.add_all([klass, prompt])
        await session.flush()
        if assign:
            session.add(PromptAssignment(
                id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                class_id=klass.id, assigned_by_external_identity="prof",
            ))
        await session.commit()
        return {
            "school": school,
            "class": klass,
            "grade": grade,
            "prompt": prompt,
        }

    def _service(self, session):
        return EssayBatchService(session, storage=self.storage)

    async def test_creates_one_page_per_image_in_upload_order(self):
        async with self.session_factory() as session:
            seed = await self._seed(session)
            paths = [
                _write_image(self.tmp_dir / "a.png", "aluno a"),
                _write_image(self.tmp_dir / "b.jpg", "aluno b"),
            ]
            created = await self._service(session).create_batch(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id, class_id=seed["class"].id,
                uploaded_by_external_identity="prof", source_paths=paths,
            )
            await session.commit()

            self.assertEqual(created["status"], "PROCESSING")
            self.assertEqual(created["total_pages"], 2)
            pages = (await session.execute(
                select(EssayBatchPage)
                .where(EssayBatchPage.batch_id == created["id"])
                .order_by(EssayBatchPage.page_number)
            )).scalars().all()
            self.assertEqual([p.page_number for p in pages], [1, 2])
            self.assertEqual([p.status for p in pages], ["NEEDS_REVIEW", "NEEDS_REVIEW"])
            for page in pages:
                self.assertTrue(Path(page.storage_uri).exists())

    async def test_extracted_pdf_text_is_persisted_per_page_images_get_none(self):
        """_expand_to_page_images captura a camada de texto digital do PDF
        (quando substancial) na INTAKE, porque o PDF original e apagado
        antes de process_batch rodar (limpeza do tmp_dir da rota, spec s7) -
        decisao do usuario 2026-10-05, pra pular OCR por visao no corpo
        quando o texto ja existe digitalmente. Uma imagem (foto) nunca tem
        essa camada - extracted_pdf_text fica None pra ela sempre."""
        async with self.session_factory() as session:
            seed = await self._seed(session)
            pdf_path = self.tmp_dir / "digitado.pdf"
            doc_lines = [
                "Este e um paragrafo digitado de verdade,",
                "bem mais longo que o teto de 30 caracteres",
                "que separa texto substancial de ruido.",
            ]
            doc_text = " ".join(doc_lines)
            import pymupdf
            doc = pymupdf.open()
            page = doc.new_page(width=595.44, height=842.40)
            for index, line in enumerate(doc_lines):
                page.insert_text((50, 60 + index * 20), line, fontsize=11)
            doc.save(str(pdf_path))
            doc.close()
            image_path = _write_image(self.tmp_dir / "foto.png", "aluno foto")

            created = await self._service(session).create_batch(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id, class_id=seed["class"].id,
                uploaded_by_external_identity="prof", source_paths=[pdf_path, image_path],
            )
            await session.commit()

            pages = (await session.execute(
                select(EssayBatchPage)
                .where(EssayBatchPage.batch_id == created["id"])
                .order_by(EssayBatchPage.page_number)
            )).scalars().all()
            self.assertIn("mais longo que o teto de 30 caracteres", pages[0].extracted_pdf_text)
            self.assertGreaterEqual(len(pages[0].extracted_pdf_text), 30)
            self.assertIsNone(pages[1].extracted_pdf_text)

    async def test_pdf_pages_are_expanded_and_numbering_continues_across_files(self):
        async with self.session_factory() as session:
            seed = await self._seed(session)
            paths = [
                _write_pdf(self.tmp_dir / "turma.pdf", 3),
                _write_image(self.tmp_dir / "extra.png", "aluno extra"),
            ]
            created = await self._service(session).create_batch(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id, class_id=seed["class"].id,
                uploaded_by_external_identity="prof", source_paths=paths,
            )
            await session.commit()

            self.assertEqual(created["total_pages"], 4)
            pages = (await session.execute(
                select(EssayBatchPage)
                .where(EssayBatchPage.batch_id == created["id"])
                .order_by(EssayBatchPage.page_number)
            )).scalars().all()
            self.assertEqual([p.page_number for p in pages], [1, 2, 3, 4])

    async def test_rejects_a_pdf_above_the_per_file_page_limit(self):
        # Teto do LOTE (_MAX_PDF_PAGES_BATCH, 200) - deliberadamente maior que
        # o do envio individual do aluno (_MAX_PDF_PAGES, 20), porque o lote
        # roda em background e nao arrisca timeout de requisicao.
        async with self.session_factory() as session:
            seed = await self._seed(session)
            path = _write_pdf(self.tmp_dir / "grande.pdf", 201)
            with self.assertRaises(ValueError) as ctx:
                await self._service(session).create_batch(
                    school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id, class_id=seed["class"].id,
                    uploaded_by_external_identity="prof", source_paths=[path],
                )
            self.assertIn("200", str(ctx.exception))

    async def test_rejects_a_batch_above_the_total_page_limit(self):
        async with self.session_factory() as session:
            seed = await self._seed(session)
            paths = [
                _write_pdf(self.tmp_dir / f"parte{index}.pdf", 20) for index in range(11)
            ]
            with self.assertRaises(ValueError) as ctx:
                await self._service(session).create_batch(
                    school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id, class_id=seed["class"].id,
                    uploaded_by_external_identity="prof", source_paths=paths,
                )
            self.assertIn(str(MAX_BATCH_PAGES), str(ctx.exception))

    async def test_rejects_an_unsupported_file_type(self):
        async with self.session_factory() as session:
            seed = await self._seed(session)
            path = self.tmp_dir / "planilha.xlsx"
            path.write_bytes(b"x")
            with self.assertRaises(ValueError):
                await self._service(session).create_batch(
                    school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id, class_id=seed["class"].id,
                    uploaded_by_external_identity="prof", source_paths=[path],
                )

    async def test_rejects_an_empty_upload(self):
        async with self.session_factory() as session:
            seed = await self._seed(session)
            with self.assertRaises(ValueError):
                await self._service(session).create_batch(
                    school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id, class_id=seed["class"].id,
                    uploaded_by_external_identity="prof", source_paths=[],
                )

    async def test_rejects_a_class_the_proposal_was_never_assigned_to(self):
        async with self.session_factory() as session:
            seed = await self._seed(session, assign=False)
            path = _write_image(self.tmp_dir / "c.png")
            with self.assertRaises(ValueError) as ctx:
                await self._service(session).create_batch(
                    school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id, class_id=seed["class"].id,
                    uploaded_by_external_identity="prof", source_paths=[path],
                )
            self.assertIn("atribu", str(ctx.exception).lower())

    async def test_nothing_is_persisted_when_the_limit_is_exceeded(self):
        async with self.session_factory() as session:
            seed = await self._seed(session)
            paths = [_write_pdf(self.tmp_dir / f"p{index}.pdf", 20) for index in range(11)]
            with self.assertRaises(ValueError):
                await self._service(session).create_batch(
                    school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id, class_id=seed["class"].id,
                    uploaded_by_external_identity="prof", source_paths=paths,
                )
            await session.rollback()
            remaining = (await session.execute(select(EssayBatchUpload))).scalars().all()
            self.assertEqual(remaining, [])

    async def test_cria_lote_com_escopo_serie_sem_checar_atribuicao_antecipada(self):
        async with self.session_factory() as session:
            seed = await self._seed(session, assign=False)
            service = EssayBatchService(session, transcriber=None, storage=self.storage)
            source = _write_image(self.tmp_dir / "folha.png")
            created = await service.create_batch(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_id=None, grade_level_id=seed["grade"].id,
                uploaded_by_external_identity="prof", source_paths=[source],
            )
            self.assertIsNone(created["class_id"])
            self.assertEqual(created["grade_level_id"], seed["grade"].id)

    async def test_cria_lote_com_escopo_escola_inteira(self):
        async with self.session_factory() as session:
            seed = await self._seed(session, assign=False)
            service = EssayBatchService(session, transcriber=None, storage=self.storage)
            source = _write_image(self.tmp_dir / "folha.png")
            created = await service.create_batch(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_id=None, grade_level_id=None,
                uploaded_by_external_identity="prof", source_paths=[source],
            )
            self.assertIsNone(created["class_id"])
            self.assertIsNone(created["grade_level_id"])

    async def test_class_id_e_grade_level_id_juntos_e_erro(self):
        async with self.session_factory() as session:
            seed = await self._seed(session, assign=True)
            service = EssayBatchService(session, transcriber=None, storage=self.storage)
            source = _write_image(self.tmp_dir / "folha.png")
            with self.assertRaises(ValueError):
                await service.create_batch(
                    school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                    class_id=seed["class"].id, grade_level_id=seed["grade"].id,
                    uploaded_by_external_identity="prof", source_paths=[source],
                )


if __name__ == "__main__":
    unittest.main()
