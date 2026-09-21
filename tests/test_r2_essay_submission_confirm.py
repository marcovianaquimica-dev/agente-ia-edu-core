import unittest
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.providers.models import EssayOcrToken, EssayPageTranscriptionResult
from agente_ia_edu.services.essay_correction_key import essay_text_hash, normalize_essay_text
from agente_ia_edu.services.essay_submission import EssaySubmissionService
from agente_ia_edu.services.material_storage import MaterialStorage


class _ScriptedTranscriber:
    def __init__(self, tokens):
        self._tokens = tokens

    async def transcribe_page(self, request):
        return EssayPageTranscriptionResult(tokens=self._tokens, provider="scripted", model="v1")


def _make_png(path: Path) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)


class ConfirmSubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.storage_root = Path("/tmp/r2_confirm_test_storage")
        self.tmp_dir = Path("/tmp/r2_confirm_test_fixtures")
        self.tmp_dir.mkdir(exist_ok=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_confirm_with_transcription_concatenates_reviewed_text_in_order(self):
        async with self.session_factory() as session:
            transcriber = _ScriptedTranscriber(
                (EssayOcrToken(text="x", confidence=0.9, start=0, end=1),)
            )
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            submission = await svc.start_photo_submission(
                school_id=uuid.uuid4(), prompt_assignment_id=uuid.uuid4(),
                student_id=uuid.uuid4(), mode="PHOTO", transcription_enabled=True,
            )
            for number in (1, 2):
                source = self.tmp_dir / f"confirm_page{number}.png"
                _make_png(source)
                await svc.upload_page(
                    essay_submission_id=submission.id, page_number=number,
                    source_path=source,
                )

            await svc.review_page(
                essay_submission_id=submission.id, page_number=1, reviewed_text="Primeira pagina."
            )
            with self.assertRaises(ValueError):
                await svc.confirm_submission(submission.id)

            await svc.review_page(
                essay_submission_id=submission.id, page_number=2, reviewed_text="Segunda pagina."
            )
            confirmed = await svc.confirm_submission(submission.id)

            expected_text = "Primeira pagina.\n\nSegunda pagina."
            self.assertEqual(confirmed.status, "SUBMITTED")
            self.assertEqual(confirmed.canonical_text, normalize_essay_text(expected_text))
            self.assertEqual(confirmed.normalized_text_hash, essay_text_hash(expected_text))
            self.assertIsNotNone(confirmed.submitted_at)

    async def test_confirm_without_transcription_needs_no_review(self):
        async with self.session_factory() as session:
            svc = EssaySubmissionService(session, storage=MaterialStorage(root=self.storage_root))
            submission = await svc.start_photo_submission(
                school_id=uuid.uuid4(), prompt_assignment_id=uuid.uuid4(),
                student_id=uuid.uuid4(), mode="PDF", transcription_enabled=False,
            )
            source = self.tmp_dir / "no_transcription.png"
            _make_png(source)
            await svc.upload_page(
                essay_submission_id=submission.id, page_number=1,
                source_path=source,
            )

            confirmed = await svc.confirm_submission(submission.id)
            self.assertEqual(confirmed.status, "SUBMITTED")
            self.assertIsNone(confirmed.canonical_text)
            self.assertIsNone(confirmed.normalized_text_hash)
            self.assertIsNotNone(confirmed.submitted_at)

    async def test_confirm_rejects_a_submission_with_zero_pages(self):
        async with self.session_factory() as session:
            svc = EssaySubmissionService(session, storage=MaterialStorage(root=self.storage_root))
            submission = await svc.start_photo_submission(
                school_id=uuid.uuid4(), prompt_assignment_id=uuid.uuid4(),
                student_id=uuid.uuid4(), mode="PDF", transcription_enabled=False,
            )
            with self.assertRaises(ValueError):
                await svc.confirm_submission(submission.id)

    async def test_confirming_an_already_submitted_essay_is_rejected(self):
        """Locks in the final-review fix: a SUBMITTED essay's canonical_text
        must never be recomputed - confirm_submission must refuse to run
        again on a row that already reached SUBMITTED."""
        async with self.session_factory() as session:
            svc = EssaySubmissionService(session, storage=MaterialStorage(root=self.storage_root))
            submission = await svc.start_photo_submission(
                school_id=uuid.uuid4(), prompt_assignment_id=uuid.uuid4(),
                student_id=uuid.uuid4(), mode="PDF", transcription_enabled=False,
            )
            source = self.tmp_dir / "confirm_twice.png"
            _make_png(source)
            await svc.upload_page(
                essay_submission_id=submission.id, page_number=1, source_path=source,
            )

            first = await svc.confirm_submission(submission.id)
            self.assertEqual(first.status, "SUBMITTED")

            with self.assertRaises(ValueError):
                await svc.confirm_submission(submission.id)

    async def test_uploading_a_page_after_submitted_is_rejected(self):
        """Locks in the final-review fix: no route lets a student rewrite an
        already-SUBMITTED essay - upload_page must refuse once status has
        moved past PENDING_CONFIRMATION."""
        async with self.session_factory() as session:
            svc = EssaySubmissionService(session, storage=MaterialStorage(root=self.storage_root))
            submission = await svc.start_photo_submission(
                school_id=uuid.uuid4(), prompt_assignment_id=uuid.uuid4(),
                student_id=uuid.uuid4(), mode="PDF", transcription_enabled=False,
            )
            source = self.tmp_dir / "upload_after_submit.png"
            _make_png(source)
            await svc.upload_page(
                essay_submission_id=submission.id, page_number=1, source_path=source,
            )
            await svc.confirm_submission(submission.id)

            with self.assertRaises(ValueError):
                await svc.upload_page(
                    essay_submission_id=submission.id, page_number=1, source_path=source,
                )

    async def test_photo_resubmission_supersedes_only_at_confirm_not_at_start(self):
        """Locks in the final-review fix: a photo/PDF resubmission the
        student never finishes must never leave the essay with zero
        SUBMITTED versions - the old row stays SUBMITTED right up until the
        new one is actually confirmed."""
        async with self.session_factory() as session:
            svc = EssaySubmissionService(session, storage=MaterialStorage(root=self.storage_root))
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            first = await svc.start_typed_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, text="Primeira versao.",
            )
            self.assertEqual(first.status, "SUBMITTED")

            second = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PDF", transcription_enabled=False,
                essay_id=first.essay_id, correction_mode="FORMATIVO",
            )

            # Abandoned here (never uploads/confirms): the first version must
            # still be the current SUBMITTED one.
            still_first = await session.get(type(first), first.id)
            self.assertEqual(still_first.status, "SUBMITTED")

            source = self.tmp_dir / "resubmission_confirm.png"
            _make_png(source)
            await svc.upload_page(
                essay_submission_id=second.id, page_number=1, source_path=source,
            )
            confirmed_second = await svc.confirm_submission(second.id)
            self.assertEqual(confirmed_second.status, "SUBMITTED")

            now_superseded_first = await session.get(type(first), first.id)
            self.assertEqual(now_superseded_first.status, "SUPERSEDED")

    async def test_reviewing_a_page_after_submitted_is_rejected(self):
        async with self.session_factory() as session:
            svc = EssaySubmissionService(session, storage=MaterialStorage(root=self.storage_root))
            submission = await svc.start_photo_submission(
                school_id=uuid.uuid4(), prompt_assignment_id=uuid.uuid4(),
                student_id=uuid.uuid4(), mode="PHOTO", transcription_enabled=False,
            )
            source = self.tmp_dir / "review_after_submit.png"
            _make_png(source)
            await svc.upload_page(
                essay_submission_id=submission.id, page_number=1, source_path=source,
            )
            await svc.confirm_submission(submission.id)

            with self.assertRaises(ValueError):
                await svc.review_page(
                    essay_submission_id=submission.id, page_number=1, reviewed_text="tentativa tardia",
                )


if __name__ == "__main__":
    unittest.main()
