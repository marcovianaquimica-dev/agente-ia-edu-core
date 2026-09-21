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
    def __init__(self, tokens):
        self._tokens = tokens

    async def transcribe_page(self, request):
        return EssayPageTranscriptionResult(tokens=self._tokens, provider="scripted", model="v1")


def _make_png(path: Path) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)


class PageReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.storage_root = Path("/tmp/r2_review_test_storage")
        self.tmp_dir = Path("/tmp/r2_review_test_fixtures")
        self.tmp_dir.mkdir(exist_ok=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _submission_with_two_pages(self, session):
        transcriber = _ScriptedTranscriber(
            (EssayOcrToken(text="ola", confidence=0.9, start=0, end=3),)
        )
        svc = EssaySubmissionService(
            session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
        )
        submission = await svc.start_photo_submission(
            school_id=uuid.uuid4(), prompt_assignment_id=uuid.uuid4(),
            student_id=uuid.uuid4(), mode="PHOTO", transcription_enabled=True,
        )
        for number in (1, 2):
            source = self.tmp_dir / f"review_page{number}.png"
            _make_png(source)
            await svc.upload_page(
                essay_submission_id=submission.id, page_number=number,
                source_path=source, transcription_enabled=True,
            )
        return svc, submission

    async def test_list_pages_returns_them_in_order(self):
        async with self.session_factory() as session:
            svc, submission = await self._submission_with_two_pages(session)
            pages = await svc.list_pages(submission.id)
            self.assertEqual([p.page_number for p in pages], [1, 2])
            self.assertIsNone(pages[0].reviewed_text)

    async def test_review_page_sets_reviewed_text_and_is_idempotent(self):
        async with self.session_factory() as session:
            svc, submission = await self._submission_with_two_pages(session)

            page = await svc.review_page(
                essay_submission_id=submission.id, page_number=1, reviewed_text="ola mundo",
            )
            self.assertEqual(page.reviewed_text, "ola mundo")

            page_again = await svc.review_page(
                essay_submission_id=submission.id, page_number=1, reviewed_text="ola mundo revisado",
            )
            self.assertEqual(page_again.reviewed_text, "ola mundo revisado")

    async def test_review_page_rejects_unknown_page_number(self):
        async with self.session_factory() as session:
            svc, submission = await self._submission_with_two_pages(session)
            with self.assertRaises(ValueError):
                await svc.review_page(
                    essay_submission_id=submission.id, page_number=99, reviewed_text="x",
                )


if __name__ == "__main__":
    unittest.main()
