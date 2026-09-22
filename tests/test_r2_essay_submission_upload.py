import unittest
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.providers.models import EssayOcrToken, EssayPageTranscriptionResult
from agente_ia_edu.services.essay_submission import EssaySubmissionService
from agente_ia_edu.services.material_storage import MaterialStorage


class _ScriptedTranscriber:
    """Test double: returns a fixed, controllable token list regardless of
    which image it's given - real image content is irrelevant to what these
    tests check (the service's own page/status bookkeeping)."""

    def __init__(self, tokens):
        self._tokens = tokens
        self.calls = 0

    async def transcribe_page(self, request):
        self.calls += 1
        return EssayPageTranscriptionResult(tokens=self._tokens, provider="scripted", model="v1")


def _make_png(path: Path) -> None:
    import pymupdf
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
    pix.clear_with(255)
    path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(path))


class PhotoUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.storage_root = Path("/tmp/r2_upload_test_storage")
        self.tmp_dir = Path("/tmp/r2_upload_test_fixtures")
        self.tmp_dir.mkdir(exist_ok=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_upload_page_with_transcription_runs_ocr_synchronously(self):
        async with self.session_factory() as session:
            tokens = (
                EssayOcrToken(text="Ola", confidence=0.99, start=0, end=3),
                EssayOcrToken(text="mundo", confidence=0.4, start=4, end=9),
            )
            transcriber = _ScriptedTranscriber(tokens)
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=True,
            )
            self.assertEqual(submission.anchor_mode, "TEXT_OFFSET")
            self.assertEqual(submission.status, "PENDING_TRANSCRIPTION")

            source = self.tmp_dir / "page1.png"
            _make_png(source)
            page = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1,
                source_path=source,
            )

            self.assertEqual(transcriber.calls, 1)
            self.assertEqual(len(page.ocr_tokens), 2)
            self.assertEqual(page.ocr_tokens[0]["text"], "Ola")
            self.assertEqual(page.ocr_tokens[1]["confidence"], 0.4)
            self.assertIsNone(page.reviewed_text)

            refreshed = await session.get(type(submission), submission.id)
            self.assertEqual(refreshed.status, "PENDING_CONFIRMATION")

    async def test_reuploading_the_same_page_number_replaces_it(self):
        async with self.session_factory() as session:
            transcriber = _ScriptedTranscriber(
                (EssayOcrToken(text="v1", confidence=0.9, start=0, end=2),)
            )
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=True,
            )

            source1 = self.tmp_dir / "reupload1.png"
            _make_png(source1)
            page_first = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1,
                source_path=source1,
            )

            source2 = self.tmp_dir / "reupload2.png"
            _make_png(source2)
            page_second = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1,
                source_path=source2,
            )

            self.assertEqual(page_first.id, page_second.id)
            self.assertEqual(transcriber.calls, 2)

    async def test_upload_page_without_transcription_never_calls_the_transcriber(self):
        async with self.session_factory() as session:
            transcriber = _ScriptedTranscriber((EssayOcrToken(text="x", confidence=1.0, start=0, end=1),))
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=False,
            )
            self.assertEqual(submission.anchor_mode, "IMAGE_REGION")

            source = self.tmp_dir / "no_ocr.png"
            _make_png(source)
            page = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1,
                source_path=source,
            )
            self.assertEqual(transcriber.calls, 0)
            self.assertIsNone(page.ocr_tokens)


class PdfUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.storage_root = Path("/tmp/r2_upload_test_storage_pdf")
        self.tmp_dir = Path("/tmp/r2_upload_test_fixtures_pdf")
        self.tmp_dir.mkdir(exist_ok=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _make_two_page_pdf(self) -> Path:
        import pymupdf as fitz

        path = self.tmp_dir / "two_pages.pdf"
        doc = fitz.open()
        for _ in range(2):
            page = doc.new_page()
            page.insert_text((72, 72), "pagina de teste")
        doc.save(str(path))
        doc.close()
        return path

    async def test_upload_document_splits_a_pdf_into_one_page_per_call(self):
        async with self.session_factory() as session:
            transcriber = _ScriptedTranscriber(
                (EssayOcrToken(text="pagina", confidence=0.9, start=0, end=6),)
            )
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PDF", transcription_enabled=True,
            )

            pdf_path = self._make_two_page_pdf()
            pages = await svc.upload_document(
                essay_submission_id=submission.id, source_path=pdf_path,
            )

            self.assertEqual(len(pages), 2)
            self.assertEqual([p.page_number for p in pages], [1, 2])
            self.assertEqual(transcriber.calls, 2)
            for page in pages:
                self.assertEqual(len(page.ocr_tokens), 1)


if __name__ == "__main__":
    unittest.main()
