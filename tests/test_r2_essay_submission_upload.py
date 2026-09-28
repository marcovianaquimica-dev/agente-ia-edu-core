import asyncio
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayPrompt,
    GradeLevel,
    Person,
    PromptAssignment,
    School,
    SchoolModule,
    Segment,
    Student,
    StudentEnrollment,
    User,
    UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.providers.models import EssayOcrToken, EssayPageTranscriptionResult
from agente_ia_edu.services.essay_submission import (
    EssaySubmissionService,
    _extract_pdf_paragraphs,
)
from agente_ia_edu.services.material_storage import MaterialStorage


class _ScriptedTranscriber:
    """Test double: returns a fixed, controllable token list regardless of
    which image it's given - real image content is irrelevant to what these
    tests check (the service's own page/status bookkeeping)."""

    def __init__(self, tokens, *, input_tokens=None, output_tokens=None):
        self._tokens = tokens
        self._input_tokens = input_tokens
        self._output_tokens = output_tokens
        self.calls = 0

    async def transcribe_page(self, request):
        self.calls += 1
        return EssayPageTranscriptionResult(
            tokens=self._tokens, provider="scripted", model="v1",
            input_tokens=self._input_tokens, output_tokens=self._output_tokens,
        )


def _make_png(path: Path) -> None:
    import pymupdf
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
    pix.clear_with(255)
    path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(path))


def _make_jpg(path: Path) -> None:
    # Same pixmap-based approach as _make_png; pymupdf.Pixmap.save() infers
    # the encoder from the output extension, and it supports JPEG output
    # just as it supports PNG - the fixture just needs a real, decodable
    # image on disk because upload_page() measures it via
    # pymupdf.Pixmap(path) (see essay_submission.py's _measure_page_image).
    import pymupdf
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
    pix.clear_with(255)
    path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(path))


class _RefusingTranscriber:
    """Test double for a provider that refuses to transcribe (see
    providers/adapters/openai.py's refusal detection)."""

    async def transcribe_page(self, request):
        from agente_ia_edu.providers.errors import ProviderInvalidResponseError
        raise ProviderInvalidResponseError("OpenAI refused to transcribe the image: sorry")


class _ScriptedSequenceTranscriber:
    """Test double: returns a different, pre-scripted token list on each
    successive call - models a vision provider whose transcription QUALITY
    varies call to call for the exact same image, not just whether it
    refuses (see _MIN_AVERAGE_CONFIDENCE's docstring in essay_submission.py:
    confirmed live 2026-09-26, the same photo scored ~0.99 average
    confidence on one call and 0.427 on another)."""

    def __init__(self, token_sequences, *, usage_sequence=None):
        self._token_sequences = list(token_sequences)
        # Optional list of (input_tokens, output_tokens) pairs, one per call,
        # so a test can verify the SUM across every attempt is stored, not
        # just the winning attempt's own usage.
        self._usage_sequence = list(usage_sequence) if usage_sequence is not None else None
        self.calls = 0

    async def transcribe_page(self, request):
        index = min(self.calls, len(self._token_sequences) - 1)
        tokens = self._token_sequences[index]
        input_tokens = output_tokens = None
        if self._usage_sequence is not None:
            usage_index = min(self.calls, len(self._usage_sequence) - 1)
            input_tokens, output_tokens = self._usage_sequence[usage_index]
        self.calls += 1
        return EssayPageTranscriptionResult(
            tokens=tokens, provider="scripted", model="v1",
            input_tokens=input_tokens, output_tokens=output_tokens,
        )


class _FlakyThenSucceedsTranscriber:
    """Test double: refuses on the first N-1 calls, then succeeds - the
    same pattern confirmed live (2026-09-25): a retried upload of the exact
    same photo often succeeds on the second or third try."""

    def __init__(self, tokens, fail_times: int):
        self._tokens = tokens
        self._fail_times = fail_times
        self.calls = 0

    async def transcribe_page(self, request):
        from agente_ia_edu.providers.errors import ProviderInvalidResponseError
        self.calls += 1
        if self.calls <= self._fail_times:
            raise ProviderInvalidResponseError("OpenAI refused to transcribe the image: sorry")
        return EssayPageTranscriptionResult(tokens=self._tokens, provider="flaky", model="v1")


class _FakePdfPage:
    """Duck-types the one PyMuPDF Page method _extract_pdf_paragraphs calls,
    so its block-joining logic is tested without ever building a real PDF."""

    def __init__(self, blocks: list[str]):
        self._blocks = blocks

    def get_text(self, mode):
        assert mode == "blocks"
        # Real PyMuPDF blocks are 7-tuples (x0, y0, x1, y1, text, block_no,
        # block_type) - only index 4 (the text) is read.
        return [(0, 0, 0, 0, text, i, 0) for i, text in enumerate(self._blocks)]


class ExtractPdfParagraphsTests(unittest.TestCase):
    def test_joins_mid_paragraph_line_wraps_with_a_space(self):
        """Confirmed live (2026-09-27): PyMuPDF's plain get_text() ends every
        visual line with a single "\\n", including a line PyMuPDF only wrapped
        mid-sentence - indistinguishable from a real paragraph break by the
        text alone. A real correction of a real, cleanly-typed PDF essay
        failed on exactly this: canonical_text read "...cuidado\\nrealizado..."
        where the model's (correct) quote read "...cuidado realizado..." with
        a plain space, since that's how the sentence actually reads."""
        page = _FakePdfPage([
            "Portanto, torna-se primordial mitigar a marginalidade do trabalho de cuidado \nrealizado pelo gênero feminino.",
        ])
        text = _extract_pdf_paragraphs(page)
        self.assertEqual(
            text,
            "Portanto, torna-se primordial mitigar a marginalidade do trabalho de "
            "cuidado realizado pelo gênero feminino.",
        )

    def test_preserves_real_paragraph_breaks_between_blocks(self):
        """get_text("blocks") groups by the PDF's own paragraph structure,
        so separate blocks are separate paragraphs - unlike a mid-block
        "\\n", which is just a line wrap (see test above)."""
        page = _FakePdfPage([
            "Primeiro paragrafo, \ncom uma linha quebrada.",
            "Segundo paragrafo, \ntambem com quebra.",
        ])
        text = _extract_pdf_paragraphs(page)
        self.assertEqual(
            text,
            "Primeiro paragrafo, com uma linha quebrada.\n\n"
            "Segundo paragrafo, tambem com quebra.",
        )

    def test_skips_empty_or_whitespace_only_blocks(self):
        """PyMuPDF's block list often ends with a trailing near-empty block
        (page margin artifacts) - it must not become a stray blank paragraph."""
        page = _FakePdfPage(["Único parágrafo real.", "   \n", ""])
        text = _extract_pdf_paragraphs(page)
        self.assertEqual(text, "Único parágrafo real.")

    def test_empty_document_returns_empty_string(self):
        page = _FakePdfPage([])
        self.assertEqual(_extract_pdf_paragraphs(page), "")


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
            # Both tokens comfortably above the low-confidence-reconciliation
            # threshold (0.6, see _LOW_CONFIDENCE_TOKEN_THRESHOLD) - this
            # test's own concern is the synchronous OCR/page bookkeeping,
            # not reconciliation, so transcriber.calls must stay exactly 1.
            # The reconciliation extra-call behavior has its own dedicated
            # tests below.
            tokens = (
                EssayOcrToken(text="Ola", confidence=0.99, start=0, end=3),
                EssayOcrToken(text="mundo", confidence=0.9, start=4, end=9),
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
            self.assertEqual(page.ocr_tokens[1]["confidence"], 0.9)
            self.assertIsNone(page.reviewed_text)

            refreshed = await session.get(type(submission), submission.id)
            self.assertEqual(refreshed.status, "PENDING_CONFIRMATION")

    async def test_upload_page_stores_token_usage_from_the_transcriber(self):
        async with self.session_factory() as session:
            tokens = (EssayOcrToken(text="Ola", confidence=0.99, start=0, end=3),)
            transcriber = _ScriptedTranscriber(tokens, input_tokens=1500, output_tokens=80)
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=True,
            )
            source = self.tmp_dir / "usage_page1.png"
            _make_png(source)
            page = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1, source_path=source,
            )

            self.assertEqual(page.input_tokens, 1500)
            self.assertEqual(page.output_tokens, 80)

    async def test_upload_page_sums_token_usage_across_retries(self):
        """A page that scores low confidence and gets retried really did
        cost the sum of every attempt's tokens, not just the winning one's -
        see _ocr_page's docstring in essay_submission.py."""
        async with self.session_factory() as session:
            low_confidence_attempt = (
                EssayOcrToken(text="lorem", confidence=0.2, start=0, end=5),
            )
            high_confidence_attempt = (
                EssayOcrToken(text="Ola", confidence=0.95, start=0, end=3),
            )
            transcriber = _ScriptedSequenceTranscriber(
                [low_confidence_attempt, high_confidence_attempt],
                usage_sequence=[(1000, 50), (1200, 60)],
            )
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root),
                transcriber=transcriber,
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=True,
            )
            source = self.tmp_dir / "usage_retry_page1.png"
            _make_png(source)
            page = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1, source_path=source,
            )

            self.assertEqual(transcriber.calls, 2)
            self.assertEqual(page.input_tokens, 1000 + 1200)
            self.assertEqual(page.output_tokens, 50 + 60)

    async def test_upload_page_without_usage_data_leaves_tokens_none(self):
        # The default _ScriptedTranscriber (no usage kwargs) mirrors a
        # provider that never reports usage - must stay None, never 0.
        async with self.session_factory() as session:
            tokens = (EssayOcrToken(text="Ola", confidence=0.99, start=0, end=3),)
            transcriber = _ScriptedTranscriber(tokens)
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=True,
            )
            source = self.tmp_dir / "no_usage_page1.png"
            _make_png(source)
            page = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1, source_path=source,
            )

            self.assertIsNone(page.input_tokens)
            self.assertIsNone(page.output_tokens)

    async def test_upload_page_retries_a_refusal_and_succeeds(self):
        """Confirmed live (2026-09-25): the exact same photo, retried with
        no changes, frequently succeeds after 1-2 refusals. upload_page
        must retry internally (up to _OCR_ATTEMPTS) before giving up, so
        the student doesn't have to keep re-clicking upload themselves."""
        async with self.session_factory() as session:
            tokens = (EssayOcrToken(text="Ola", confidence=0.99, start=0, end=3),)
            transcriber = _FlakyThenSucceedsTranscriber(tokens, fail_times=2)
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root),
                transcriber=transcriber,
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=True,
            )
            source = self.tmp_dir / "page1.png"
            _make_png(source)
            page = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1, source_path=source,
            )

            self.assertEqual(transcriber.calls, 3)
            self.assertEqual(len(page.ocr_tokens), 1)
            refreshed = await session.get(type(submission), submission.id)
            self.assertEqual(refreshed.anchor_mode, "TEXT_OFFSET")
            self.assertEqual(refreshed.status, "PENDING_CONFIRMATION")

    async def test_upload_page_retries_a_low_confidence_transcription_and_keeps_the_best(self):
        """Confirmed live (2026-09-26): the exact same photo scored ~0.99
        average confidence on one transcription call and 0.427 on another -
        a well-formed, non-refusing response can still be mostly
        confabulated. A low-average-confidence result must be retried like a
        refusal, and the best-scoring attempt kept even if a later attempt
        scores worse."""
        async with self.session_factory() as session:
            low_confidence_attempt = (
                EssayOcrToken(text="lorem", confidence=0.2, start=0, end=5),
                EssayOcrToken(text="ipsum", confidence=0.3, start=6, end=11),
            )
            high_confidence_attempt = (
                EssayOcrToken(text="Ola", confidence=0.95, start=0, end=3),
                EssayOcrToken(text="mundo", confidence=0.9, start=4, end=9),
            )
            transcriber = _ScriptedSequenceTranscriber(
                [low_confidence_attempt, high_confidence_attempt]
            )
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root),
                transcriber=transcriber,
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=True,
            )
            source = self.tmp_dir / "page1.png"
            _make_png(source)
            page = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1, source_path=source,
            )

            self.assertEqual(transcriber.calls, 2)
            self.assertEqual([t["text"] for t in page.ocr_tokens], ["Ola", "mundo"])

    async def test_upload_page_keeps_the_best_attempt_when_all_score_low(self):
        """The product decision is to always transcribe regardless of
        quality, so exhausting every retry on low confidence must still
        produce a result (never an error) - specifically the best-scoring
        attempt, not whichever ran last."""
        async with self.session_factory() as session:
            worst = (EssayOcrToken(text="pior", confidence=0.1, start=0, end=4),)
            best = (EssayOcrToken(text="melhor", confidence=0.5, start=0, end=6),)
            middling = (EssayOcrToken(text="meio", confidence=0.3, start=0, end=4),)
            transcriber = _ScriptedSequenceTranscriber([worst, best, middling])
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root),
                transcriber=transcriber,
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=True,
            )
            source = self.tmp_dir / "page1.png"
            _make_png(source)
            page = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1, source_path=source,
            )

            # 3 retry attempts, plus a 4th reconciliation call: the kept
            # "melhor" attempt is itself below the low-confidence
            # reconciliation threshold (0.5 < 0.6), and the sequence's last
            # entry ("meio") repeats for any call beyond the 3 scripted ones
            # (see _ScriptedSequenceTranscriber) - it disagrees with
            # "melhor", so the reconciliation leaves it untouched.
            self.assertEqual(transcriber.calls, 4)
            self.assertEqual(page.ocr_tokens[0]["text"], "melhor")
            self.assertEqual(page.ocr_tokens[0]["confidence"], 0.5)

    async def test_upload_page_propagates_a_transcription_refusal(self):
        """Explicit product decision (2026-09-25): always transcribe,
        regardless of image quality - never silently fall back to
        IMAGE_REGION mode (its anchor precision is worse, and switching
        modes mid-flow without telling the student is confusing). A
        transcription failure must propagate as ProviderError so the route
        surfaces a clear error and the student retries with a better photo."""
        from agente_ia_edu.providers.errors import ProviderError

        async with self.session_factory() as session:
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root),
                transcriber=_RefusingTranscriber(),
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=True,
            )
            self.assertEqual(submission.anchor_mode, "TEXT_OFFSET")

            source = self.tmp_dir / "page1.png"
            _make_png(source)
            with self.assertRaises(ProviderError):
                await svc.upload_page(
                    essay_submission_id=submission.id, page_number=1, source_path=source,
                )

            # anchor_mode must NOT have been downgraded.
            refreshed = await session.get(type(submission), submission.id)
            self.assertEqual(refreshed.anchor_mode, "TEXT_OFFSET")

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

    async def test_reuploading_the_same_page_number_resets_token_usage(self):
        async with self.session_factory() as session:
            transcriber = _ScriptedTranscriber(
                (EssayOcrToken(text="v1", confidence=0.9, start=0, end=2),),
                input_tokens=100, output_tokens=10,
            )
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=True,
            )

            source1 = self.tmp_dir / "reupload_usage1.png"
            _make_png(source1)
            first = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1, source_path=source1,
            )
            self.assertEqual(first.input_tokens, 100)

            transcriber._input_tokens = 200
            transcriber._output_tokens = 20
            source2 = self.tmp_dir / "reupload_usage2.png"
            _make_png(source2)
            second = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1, source_path=source2,
            )

            self.assertEqual(second.id, first.id)
            self.assertEqual(second.input_tokens, 200)
            self.assertEqual(second.output_tokens, 20)

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

    async def test_upload_page_with_jpg_extension_succeeds(self):
        # No test in this class had exercised a .jpg/.jpeg page before -
        # _ALLOWED_PAGE_SUFFIXES in api/routes/essay_submissions.py accepts
        # .png, .jpg and .jpeg alike, and this confirms the service layer
        # (which this class calls directly, bypassing the route) handles a
        # .jpg source file exactly like the .png ones above: same
        # EssaySubmissionPage shape, OCR still runs.
        async with self.session_factory() as session:
            tokens = (EssayOcrToken(text="Ola", confidence=0.95, start=0, end=3),)
            transcriber = _ScriptedTranscriber(tokens)
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=True,
            )

            source = self.tmp_dir / "page1.jpg"
            _make_jpg(source)
            page = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1,
                source_path=source,
            )

            self.assertEqual(transcriber.calls, 1)
            self.assertEqual(len(page.ocr_tokens), 1)
            self.assertIsNone(page.reviewed_text)


class _SucceedsThenFailsTranscriber:
    """Test double: the FIRST call succeeds with fixed tokens (the normal
    OCR read), every call after that raises - models the reconciliation
    call itself failing, which must never crash the upload or discard the
    (already-successful) first reading."""

    def __init__(self, tokens):
        self._tokens = tokens
        self.calls = 0

    async def transcribe_page(self, request):
        from agente_ia_edu.providers.errors import ProviderInvalidResponseError
        self.calls += 1
        if self.calls == 1:
            return EssayPageTranscriptionResult(tokens=self._tokens, provider="scripted", model="v1")
        raise ProviderInvalidResponseError("OpenAI refused to transcribe the image: sorry")


class LowConfidenceReconciliationTests(unittest.IsolatedAsyncioTestCase):
    """Coverage for essay_submission.py's second-opinion reconciliation:
    _ocr_page requests one extra, fully independent transcribe_page call
    ONLY when the accepted reading has a low-confidence token, and only
    upgrades that word's confidence when the second read agrees with it."""

    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.storage_root = Path("/tmp/r2_upload_test_storage_reconciliation")
        self.tmp_dir = Path("/tmp/r2_upload_test_fixtures_reconciliation")
        self.tmp_dir.mkdir(exist_ok=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _upload_one_page(self, session, transcriber):
        svc = EssaySubmissionService(
            session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
        )
        school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        submission = await svc.start_photo_submission(
            school_id=school_id, prompt_assignment_id=assignment_id,
            student_id=student_id, mode="PHOTO", transcription_enabled=True,
        )
        source = self.tmp_dir / f"page-{uuid.uuid4()}.png"
        _make_png(source)
        return await svc.upload_page(
            essay_submission_id=submission.id, page_number=1, source_path=source,
        )

    async def test_agreeing_second_read_boosts_the_low_confidence_word(self):
        first_attempt = (
            EssayOcrToken(text="Ola", confidence=0.99, start=0, end=3),
            EssayOcrToken(text="mundo", confidence=0.4, start=4, end=9),
        )
        # Independent re-read of the exact same page agrees on "mundo".
        second_attempt = (
            EssayOcrToken(text="Ola", confidence=0.6, start=0, end=3),
            EssayOcrToken(text="mundo", confidence=0.55, start=4, end=9),
        )
        transcriber = _ScriptedSequenceTranscriber([first_attempt, second_attempt])
        async with self.session_factory() as session:
            page = await self._upload_one_page(session, transcriber)

        self.assertEqual(transcriber.calls, 2)
        by_text = {t["text"]: t["confidence"] for t in page.ocr_tokens}
        self.assertEqual(by_text["mundo"], 0.85)
        # "Ola" was never low-confidence, so reconciliation must never touch
        # it even though it's part of the same second-read response.
        self.assertEqual(by_text["Ola"], 0.99)

    async def test_reconciliation_calls_usage_is_added_to_the_page_total(self):
        """The reconciliation call is a REAL extra OpenAI call, not a free
        second opinion - its own usage must be summed into the page's
        input_tokens/output_tokens alongside the first (accepted) call's
        usage, the same way a low-average-confidence retry's usage already
        is (see _ocr_page's total_input_tokens/total_output_tokens)."""
        first_attempt = (
            EssayOcrToken(text="Ola", confidence=0.99, start=0, end=3),
            EssayOcrToken(text="mundo", confidence=0.4, start=4, end=9),
        )
        second_attempt = (
            EssayOcrToken(text="Ola", confidence=0.6, start=0, end=3),
            EssayOcrToken(text="mundo", confidence=0.55, start=4, end=9),
        )
        transcriber = _ScriptedSequenceTranscriber(
            [first_attempt, second_attempt],
            usage_sequence=[(1000, 50), (300, 20)],
        )
        async with self.session_factory() as session:
            page = await self._upload_one_page(session, transcriber)

        self.assertEqual(transcriber.calls, 2)
        self.assertEqual(page.input_tokens, 1000 + 300)
        self.assertEqual(page.output_tokens, 50 + 20)

    async def test_disagreeing_second_read_leaves_the_word_untouched(self):
        first_attempt = (
            EssayOcrToken(text="Ola", confidence=0.99, start=0, end=3),
            EssayOcrToken(text="mundo", confidence=0.4, start=4, end=9),
        )
        # Independent re-read disagrees on the uncertain word.
        second_attempt = (
            EssayOcrToken(text="Ola", confidence=0.9, start=0, end=3),
            EssayOcrToken(text="mundu", confidence=0.5, start=4, end=9),
        )
        transcriber = _ScriptedSequenceTranscriber([first_attempt, second_attempt])
        async with self.session_factory() as session:
            page = await self._upload_one_page(session, transcriber)

        self.assertEqual(transcriber.calls, 2)
        by_text = {t["text"]: t["confidence"] for t in page.ocr_tokens}
        self.assertEqual(by_text["mundo"], 0.4)

    async def test_no_low_confidence_tokens_never_triggers_the_extra_call(self):
        tokens = (
            EssayOcrToken(text="Ola", confidence=0.99, start=0, end=3),
            EssayOcrToken(text="mundo", confidence=0.95, start=4, end=9),
        )
        transcriber = _ScriptedTranscriber(tokens)
        async with self.session_factory() as session:
            await self._upload_one_page(session, transcriber)

        self.assertEqual(transcriber.calls, 1)

    async def test_reconciliation_call_failure_keeps_the_original_reading(self):
        tokens = (
            EssayOcrToken(text="Ola", confidence=0.99, start=0, end=3),
            EssayOcrToken(text="mundo", confidence=0.4, start=4, end=9),
        )
        transcriber = _SucceedsThenFailsTranscriber(tokens)
        async with self.session_factory() as session:
            page = await self._upload_one_page(session, transcriber)

        self.assertEqual(transcriber.calls, 2)
        by_text = {t["text"]: t["confidence"] for t in page.ocr_tokens}
        self.assertEqual(by_text["mundo"], 0.4)
        self.assertEqual(by_text["Ola"], 0.99)


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

    def _make_typed_pdf(self, text: str) -> Path:
        import pymupdf as fitz

        path = self.tmp_dir / "typed.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 72), text, fontsize=11)
        doc.save(str(path))
        doc.close()
        return path

    async def test_upload_document_skips_ocr_for_a_digitally_typed_pdf(self):
        """Confirmed live (2026-09-25): a typed PDF (real text layer, not a
        scan) ran through vision OCR anyway and got spurious low-confidence
        flags on perfectly typed words. When the PDF's own text layer is
        substantial, use it directly at full confidence instead of calling
        the transcriber at all."""
        async with self.session_factory() as session:
            transcriber = _ScriptedTranscriber(
                (EssayOcrToken(text="nao deveria ser chamado", confidence=0.5, start=0, end=10),)
            )
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PDF", transcription_enabled=True,
            )

            long_text = "Este e um texto digitado com bastante conteudo para passar do limite minimo."
            pdf_path = self._make_typed_pdf(long_text)
            pages = await svc.upload_document(
                essay_submission_id=submission.id, source_path=pdf_path,
            )

            self.assertEqual(transcriber.calls, 0)
            self.assertEqual(len(pages), 1)
            self.assertEqual(len(pages[0].ocr_tokens), 1)
            token = pages[0].ocr_tokens[0]
            self.assertEqual(token["confidence"], 1.0)
            self.assertIn("texto digitado", token["text"])


class PageFormatValidationRouteTests(unittest.TestCase):
    """Exercises the real HTTP route, not the service layer.

    The suffix allowlist (_ALLOWED_PAGE_SUFFIXES) and the "unsupported file
    format" 422 live only in upload_essay_submission_page
    (api/routes/essay_submissions.py) - EssaySubmissionService.upload_page,
    which PhotoUploadTests above calls directly, never checks the file
    extension at all. So a rejection test written against the service layer
    would be vacuous (it would still pass if the route-level check were
    deleted). Setup mirrors EssaySubmissionsRoutesTests in
    tests/test_r2_essay_submissions_routes.py.
    """

    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        cls.loop.run_until_complete(_prep())
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls.client = TestClient(cls.app)
        cls.tmp_dir = Path("/tmp/r2_upload_test_fixtures_format")
        cls.tmp_dir.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: ExternalIdentityContext(
            provider="test", external_user_id=user
        )

    def _seed_submission(self, code: str) -> str:
        """Seeds one enrolled student (module enabled, transcription off -
        irrelevant to a suffix check) and creates a PHOTO-mode submission for
        them, mirroring _seed_enrolled_student in
        test_r2_essay_submissions_routes.py."""

        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"FMT-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(SchoolModule(
                    id=uuid.uuid4(), school_id=school.id, module_key="REDACAO_IA", enabled=True,
                ))
                session.add(UserSchoolLink(
                    external_user_id=f"student_fmt_{code}", school_id=school.id, role="STUDENT",
                    scope_type="SCHOOL", active=True,
                ))
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
                person = Person(id=uuid.uuid4(), school_id=school.id, full_name=f"Aluno {code}")
                session.add(person)
                await session.flush()
                session.add(User(
                    id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                    external_identity_provider="test", external_user_id=f"student_fmt_{code}",
                ))
                student = Student(
                    id=uuid.uuid4(), school_id=school.id, person_id=person.id, student_code=f"ST-{code}"
                )
                session.add(student)
                await session.flush()
                session.add(StudentEnrollment(
                    id=uuid.uuid4(), school_id=school.id, student_id=student.id, class_id=klass.id,
                    status="ACTIVE",
                ))

                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
                    year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
                )
                session.add(prompt)
                await session.flush()
                assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                    class_id=klass.id, assigned_by_external_identity="teacher:t",
                )
                session.add(assignment)

                from agente_ia_edu.services.institution_settings import InstitutionSettingsService
                await InstitutionSettingsService(session).configure(
                    school.id, performed_by_external_id="admin:x",
                    transcription_enabled=False,
                )
                await session.commit()
                return assignment.id

        assignment_id = self.loop.run_until_complete(_seed())
        self._as(f"student_fmt_{code}")
        create_resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "PHOTO"},
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        return create_resp.json()["id"]

    def test_upload_page_with_jpg_extension_succeeds_via_route(self):
        submission_id = self._seed_submission("jpg")
        source = self.tmp_dir / "page1.jpg"
        _make_jpg(source)
        with open(source, "rb") as f:
            resp = self.client.post(
                f"/api/v1/student/essay-submissions/{submission_id}/pages",
                data={"page_number": "1"},
                files={"file": ("page1.jpg", f, "image/jpeg")},
            )
        self.assertEqual(resp.status_code, 201, resp.text)

    def test_upload_page_with_unsupported_extension_is_rejected(self):
        submission_id = self._seed_submission("gif")
        source = self.tmp_dir / "page1.gif"
        # The route rejects on the filename suffix before it ever reads/
        # decodes the file body (see upload_essay_submission_page), so the
        # bytes here don't need to be a real image.
        source.write_bytes(b"not a real gif")
        with open(source, "rb") as f:
            resp = self.client.post(
                f"/api/v1/student/essay-submissions/{submission_id}/pages",
                data={"page_number": "1"},
                files={"file": ("page1.gif", f, "image/gif")},
            )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("unsupported file format", resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
