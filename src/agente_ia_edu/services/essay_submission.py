"""R2 - EssaySubmission/EssaySubmissionPage: the "enviar redação" side of R2
(spec §5). One class, built up across this plan's Tasks 7-10:

  Task 7:  TYPED (§5.1) + resubmission plumbing shared by every mode (§5.4)
  Task 8:  PHOTO/PDF upload + synchronous OCR trigger (§5.2 steps 1-2, §5.3)
  Task 9:  page listing + review (§5.2 step 3)
  Task 10: confirm (§5.2 step 4, §5.3's direct-to-SUBMITTED path)

Authorization (role, module gate, enrollment, prompt_assignment ownership)
lives in the route layer (Task 11) - this service only enforces rules the
database can't express as a CHECK constraint.
"""

from __future__ import annotations

import asyncio
import difflib
import logging
import mimetypes
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import EssayPrompt, EssaySubmission, EssaySubmissionPage, PromptAssignment
from ..providers.contracts import EssayTranscriptionProvider
from ..providers.errors import ProviderError, ProviderRateLimitError
from ..providers.factory import build_essay_transcriber
from ..providers.models import EssayOcrToken, EssayPageTranscriptionRequest
from .essay_correction_key import essay_text_hash, normalize_essay_text
from .material_storage import MaterialStorage

logger = logging.getLogger(__name__)


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _reconstruct_text_from_tokens(tokens: tuple) -> str:
    """Rebuilds the original transcribed text from its own tokens' start/end
    offsets - NOT plain concatenation of `t.text` for every token, which
    silently glues adjacent words together whenever a gap (almost always a
    space) between two tokens wasn't itself captured as its own token (see
    adapters/openai.py::_tokens_from_logprobs, which can skip a piece that
    doesn't `str.find()` cleanly). An uncovered gap defaults to a single
    space, which is the overwhelmingly common case and, worst case, only
    costs the reconciliation step a missed word-boundary rather than a
    corrupted one."""
    if not tokens:
        return ""
    length = max(t.end for t in tokens)
    buf = [" "] * length
    for t in tokens:
        for i, ch in enumerate(t.text):
            pos = t.start + i
            if pos < length:
                buf[pos] = ch
    return "".join(buf)


def _prepare_reconciliation_precheck(tokens: tuple, threshold: float) -> tuple | None:
    """Runs on the FIRST reading alone (no second call needed yet) to decide
    whether reconciliation is worth attempting at all - returns None to skip
    it entirely (no word spans, or no word actually below `threshold`).
    Split out from `_apply_reconciliation` so `_ocr_page`'s hot path never
    calls the transcriber a second time only to discover there was nothing
    to check."""
    if not any(t.confidence < threshold for t in tokens):
        return None
    text1 = _reconstruct_text_from_tokens(tokens)
    word_spans1 = [m.span() for m in re.finditer(r"\S+", text1)]
    if not word_spans1:
        return None

    def _is_low_confidence(span: tuple[int, int]) -> bool:
        start, end = span
        return any(
            t.confidence < threshold and t.start < end and t.end > start for t in tokens
        )

    low_confidence_word_indices = {
        i for i, span in enumerate(word_spans1) if _is_low_confidence(span)
    }
    if not low_confidence_word_indices:
        return None
    return text1, word_spans1, low_confidence_word_indices


def _apply_reconciliation(
    tokens: tuple, precheck: tuple, second_tokens: tuple, reconciled_confidence: float
) -> tuple:
    """Pure CPU work (word alignment + rebuild), run off the event loop via
    asyncio.to_thread by the caller - see essay_submission.py's own
    _measure_page_image/_split_pdf_pages for the established pattern this
    follows.

    Comparison happens at WORD granularity, not raw token, because `tokens`
    are raw model generation tokens (sub-word BPE pieces from OpenAI's own
    tokenizer - see adapters/openai.py::_tokens_from_logprobs), which can
    split differently between two calls even for the same word; text-level
    word alignment via difflib is robust to that, where token-index
    alignment would not be.

    A word only counts as confirmed when it sits inside a matching block of
    at least 2 consecutive words, never a single isolated word matching in
    both readings alone - confirmed live 2026-09-27: two genuinely
    UNRELATED readings agree on short/common words (articles, prepositions)
    often enough by pure chance that a single-word match is weak evidence,
    while a real independent re-read reproducing two or more words in a row
    is not. Where the two reads disagree (or never get a long-enough
    matching block), that word's original confidence is left untouched -
    the explicit product decision (see OCR_DUVIDOSO in essay_prompts) is
    that a genuine ambiguity is surfaced to a human, never silently
    resolved by picking whichever reading "sounds more correct"."""
    text1, word_spans1, low_confidence_word_indices = precheck
    text2 = _reconstruct_text_from_tokens(second_tokens)
    words1 = [text1[s:e] for s, e in word_spans1]
    words2 = [m.group(0) for m in re.finditer(r"\S+", text2)]

    agreeing_word_indices: set[int] = set()
    matcher = difflib.SequenceMatcher(None, words1, words2, autojunk=False)
    for tag, i1, i2, _j1, _j2 in matcher.get_opcodes():
        if tag == "equal" and (i2 - i1) >= 2:
            agreeing_word_indices.update(range(i1, i2))

    reconciled_confirmed_spans = [
        word_spans1[i] for i in (low_confidence_word_indices & agreeing_word_indices)
    ]
    if not reconciled_confirmed_spans:
        return tokens

    def _in_confirmed_span(t: EssayOcrToken) -> bool:
        return any(t.start < end and t.end > start for start, end in reconciled_confirmed_spans)

    return tuple(
        EssayOcrToken(
            text=t.text,
            # max(): a token that was already above the reconciled floor
            # (e.g. a confident token sharing a word with a low-confidence
            # neighbor) must never be pulled DOWN by this - only ever up.
            confidence=max(t.confidence, reconciled_confidence) if _in_confirmed_span(t) else t.confidence,
            start=t.start, end=t.end,
        )
        for t in tokens
    )


class EssayResubmissionBlockedError(RuntimeError):
    """AVALIATIVO: a SUBMITTED essay is definitive and cannot be resubmitted
    (spec §5.4). Mapped to 409 in the route, not 422 - this is a conflict
    with existing state, not a malformed request."""


class EssaySubmissionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: MaterialStorage | None = None,
        transcriber: EssayTranscriptionProvider | None = None,
    ) -> None:
        self.session = session
        self._storage = storage or MaterialStorage()
        # Lazily built (Task 8) so TYPED submissions and schools with
        # transcription disabled never require OPENAI_API_KEY to be set.
        self._transcriber = transcriber

    async def _resolve_student_declared_theme(
        self, *, prompt_assignment_id: uuid.UUID, student_declared_theme: str | None,
    ) -> str | None:
        """"Tema livre" (EssayPrompt.is_free_theme) requires the student to
        type their own theme; any other assignment ignores whatever was
        sent here, since its theme is the prompt's own fixed statement.

        prompt_assignment_id existing at all is the route layer's job
        (_assignment_for_own_class_or_403 runs before this service is ever
        called) - a missing assignment/prompt here is simply treated as
        "not tema livre" rather than re-raised as a separate error."""
        assignment = await self.session.get(PromptAssignment, prompt_assignment_id)
        if assignment is None:
            return None
        prompt = await self.session.get(EssayPrompt, assignment.essay_prompt_id)
        if prompt is not None and prompt.is_free_theme:
            if not student_declared_theme or not student_declared_theme.strip():
                raise ValueError(
                    "This is a tema livre assignment - student_declared_theme is required"
                )
            return student_declared_theme.strip()
        return None

    async def start_typed_submission(
        self,
        *,
        school_id: uuid.UUID,
        prompt_assignment_id: uuid.UUID,
        student_id: uuid.UUID,
        text: str,
        essay_id: uuid.UUID | None = None,
        correction_mode: str | None = None,
        student_declared_theme: str | None = None,
    ) -> EssaySubmission:
        student_declared_theme = await self._resolve_student_declared_theme(
            prompt_assignment_id=prompt_assignment_id,
            student_declared_theme=student_declared_theme,
        )
        previous = None
        if essay_id is not None:
            # TYPED submits atomically (the new row is SUBMITTED in this
            # same call), so unlike start_photo_submission it's safe to
            # supersede the old row right here rather than deferring to
            # confirm_submission - there's no in-between state where neither
            # row is SUBMITTED.
            previous = await self._validate_resubmission(
                essay_id, correction_mode=correction_mode or "FORMATIVO"
            )

        submission = EssaySubmission(
            id=uuid.uuid4(),
            essay_id=essay_id or uuid.uuid4(),
            school_id=school_id,
            prompt_assignment_id=prompt_assignment_id,
            student_id=student_id,
            mode="TYPED",
            anchor_mode="TEXT_OFFSET",
            status="SUBMITTED",
            canonical_text=normalize_essay_text(text),
            normalized_text_hash=essay_text_hash(text),
            submitted_at=_utcnow(),
            student_declared_theme=student_declared_theme,
        )
        self.session.add(submission)
        if previous is not None:
            previous.status = "SUPERSEDED"
        await self.session.flush()
        return submission

    async def _validate_resubmission(
        self, essay_id: uuid.UUID, *, correction_mode: str
    ) -> EssaySubmission:
        """Confirms essay_id is eligible for a new version, WITHOUT
        mutating anything - actually superseding the previous row is the
        caller's job, at whatever point the new version is guaranteed to
        replace it (start_typed_submission does it immediately since TYPED
        submits atomically; start_photo_submission defers it to
        confirm_submission, since photo/PDF submissions can be abandoned
        mid-upload)."""
        previous = await self.session.scalar(
            select(EssaySubmission).where(
                EssaySubmission.essay_id == essay_id, EssaySubmission.status == "SUBMITTED"
            )
        )
        if previous is None:
            raise ValueError(f"No SUBMITTED version exists for essay_id={essay_id} to resubmit")
        if correction_mode == "AVALIATIVO":
            raise EssayResubmissionBlockedError(
                f"AVALIATIVO: essay_id={essay_id} is already SUBMITTED and cannot be resubmitted"
            )
        return previous

    async def start_photo_submission(
        self,
        *,
        school_id: uuid.UUID,
        prompt_assignment_id: uuid.UUID,
        student_id: uuid.UUID,
        mode: str,
        transcription_enabled: bool,
        essay_id: uuid.UUID | None = None,
        correction_mode: str | None = None,
        student_declared_theme: str | None = None,
    ) -> EssaySubmission:
        if mode not in ("PHOTO", "PDF"):
            raise ValueError(f"start_photo_submission requires mode PHOTO or PDF, got {mode!r}")
        student_declared_theme = await self._resolve_student_declared_theme(
            prompt_assignment_id=prompt_assignment_id,
            student_declared_theme=student_declared_theme,
        )
        if essay_id is not None:
            # Validate eligibility only - do NOT supersede the previous
            # version yet. That happens in confirm_submission, once this new
            # version is actually about to become SUBMITTED. Superseding here
            # would leave the essay with zero SUBMITTED versions if the
            # student never finishes uploading/reviewing (spec §5.4).
            await self._validate_resubmission(essay_id, correction_mode=correction_mode or "FORMATIVO")

        submission = EssaySubmission(
            id=uuid.uuid4(),
            essay_id=essay_id or uuid.uuid4(),
            school_id=school_id,
            prompt_assignment_id=prompt_assignment_id,
            student_id=student_id,
            mode=mode,
            anchor_mode="TEXT_OFFSET" if transcription_enabled else "IMAGE_REGION",
            status="PENDING_TRANSCRIPTION",
            student_declared_theme=student_declared_theme,
        )
        self.session.add(submission)
        await self.session.flush()
        return submission

    _UPLOADABLE_STATUSES = ("PENDING_TRANSCRIPTION", "PENDING_CONFIRMATION")

    # Below this, a PDF page's extracted text layer is treated as too sparse
    # to trust on its own (e.g. a mostly-blank page, or a scanned PDF whose
    # "text layer" is just a stray OCR artifact from the scanner) and the
    # page still goes through vision transcription instead.
    _MIN_EXTRACTED_TEXT_CHARS = 30

    async def upload_page(
        self,
        *,
        essay_submission_id: uuid.UUID,
        page_number: int,
        source_path: Path,
        extracted_text: str | None = None,
    ) -> EssaySubmissionPage:
        submission = await self.session.get(EssaySubmission, essay_submission_id)
        if submission is None:
            raise ValueError(f"EssaySubmission not found: {essay_submission_id}")
        if submission.status not in self._UPLOADABLE_STATUSES:
            raise ValueError(
                f"EssaySubmission {essay_submission_id} is {submission.status} - pages can "
                "only be uploaded while a submission is pending confirmation (spec §5.2 step 3)."
            )
        # Derived from the submission's own frozen anchor_mode, never from a
        # live settings read - a school toggling transcription_enabled mid-
        # flow must not change how an in-progress submission behaves.
        transcription_enabled = submission.anchor_mode == "TEXT_OFFSET"

        dest, _digest = self._storage.store(source_path)
        # Offloaded like _split_pdf_pages below - image decoding is CPU-bound.
        width, height = await asyncio.to_thread(self._measure_page_image, dest)

        existing = await self.session.scalar(
            select(EssaySubmissionPage).where(
                EssaySubmissionPage.essay_submission_id == essay_submission_id,
                EssaySubmissionPage.page_number == page_number,
            )
        )
        if existing is not None:
            existing.storage_uri = str(dest)
            existing.width = width
            existing.height = height
            existing.ocr_tokens = None
            existing.reviewed_text = None
            existing.input_tokens = None
            existing.output_tokens = None
            page = existing
        else:
            page = EssaySubmissionPage(
                id=uuid.uuid4(),
                essay_submission_id=essay_submission_id,
                page_number=page_number,
                storage_uri=str(dest),
                width=width,
                height=height,
            )
            self.session.add(page)
        await self.session.flush()

        if transcription_enabled:
            if extracted_text is not None and len(extracted_text.strip()) >= self._MIN_EXTRACTED_TEXT_CHARS:
                # A digitally-typed PDF already has an exact text layer -
                # confirmed live (2026-09-25), running it through vision OCR
                # anyway produced spurious low-confidence flags on perfectly
                # typed words (there is nothing uncertain to read) and cost a
                # needless round trip to the vision model. Use the extracted
                # text directly, at full confidence, the same shape
                # _tokens_from_logprobs falls back to when a provider has no
                # per-token signal of its own.
                page.ocr_tokens = [{
                    "text": extracted_text, "confidence": 1.0,
                    "start": 0, "end": len(extracted_text),
                }]
            else:
                # Explicit product decision (2026-09-25): always transcribe,
                # regardless of image quality - never silently fall back to
                # IMAGE_REGION (that mode's own anchor precision is worse, and
                # switching modes mid-flow surprised the student). A refusal or
                # any other transcription failure must surface as a clear error
                # (ProviderError propagates to the route's 502 handler below) so
                # the student retries with a better photo - it must not
                # silently downgrade to a less precise correction.
                await self._ocr_page(page, dest)
            submission.status = "PENDING_CONFIRMATION"
            await self.session.flush()
        return page

    async def upload_document(
        self,
        *,
        essay_submission_id: uuid.UUID,
        source_path: Path,
    ) -> list[EssaySubmissionPage]:
        """PDF mode: split ``source_path`` into one page-image per PDF page
        (off the event loop - rasterization is CPU-bound) and upload each
        through the same path :meth:`upload_page` uses. Also extracts each
        page's embedded text layer, when it has one, so upload_page can skip
        vision OCR entirely for a digitally-typed PDF."""
        split_pages = await asyncio.to_thread(self._split_pdf_pages, source_path)
        pages = []
        for index, (image_path, extracted_text) in enumerate(split_pages, start=1):
            page = await self.upload_page(
                essay_submission_id=essay_submission_id, page_number=index,
                source_path=image_path, extracted_text=extracted_text,
            )
            pages.append(page)
        return pages

    # Confirmed live (2026-09-25): a transcription refusal/failure is often
    # transient - the same photo frequently succeeds on a second or third
    # attempt with no change at all. Retrying here, inside one upload call,
    # spares the student from re-clicking upload themselves.
    _OCR_ATTEMPTS = 3

    # Confirmed live (2026-10-06): OpenAI's TPM rate limit is a ROLLING
    # window that refills within roughly 30-90s - retrying immediately into
    # an already-exhausted bucket (the previous behavior) means all 3
    # _OCR_ATTEMPTS fail together almost every time, confirmed in real
    # batch logs (3/3 identical "rate limit reached" within milliseconds of
    # each other). Only ProviderRateLimitError gets this pause - a timeout
    # or a malformed response isn't fixed by waiting, so those still retry
    # immediately as before.
    _RATE_LIMIT_BACKOFF_SECONDS = 15.0

    # Confirmed live (2026-09-26): the SAME photo that transcribed at ~99%
    # average confidence in one call came back at 0.427 average confidence
    # (159 of 243 tokens below 60%) in another, unrelated call - a vision
    # model can return a well-formed, non-refusing response that is mostly
    # confabulated rather than actually read. A refusal check never catches
    # this: the call succeeds, the JSON is fine, the words are just wrong.
    # Average confidence is the only signal already available that flags it,
    # so a page-average below this floor is treated like a failed attempt
    # and retried, instead of being accepted and handed to the student as a
    # near-unreadable wall of red text.
    _MIN_AVERAGE_CONFIDENCE = 0.6

    # Same threshold essay.js already uses to highlight a token red for the
    # student to hand-fix - kept identical so "would this get reconciled"
    # and "would this show up red" never disagree.
    _LOW_CONFIDENCE_TOKEN_THRESHOLD = 0.6

    # Confirmed live (2026-09-27): a token's low confidence often just means
    # the model itself was unsure, not that it was wrong - a second, fully
    # independent read of the SAME image frequently reproduces the exact
    # same word. That agreement is real evidence, so an agreeing word is
    # promoted comfortably above the highlight threshold rather than to 1.0
    # (this was still a guess the model wasn't sure about, twice - not a
    # certainty).
    _RECONCILED_TOKEN_CONFIDENCE = 0.85

    async def _ocr_page(
        self, page: EssaySubmissionPage, image_path: Path, *, system_prompt: str | None = None
    ) -> None:
        last_error: ProviderError | None = None
        best_tokens: tuple | None = None
        best_average_confidence = -1.0
        # Summed across every actual OpenAI call this page triggers - a
        # retried or low-confidence-reattempted page really did cost the sum
        # of all those attempts, not just whichever one is finally kept.
        # None until at least one attempt reports real usage, so a provider
        # that never reports usage never masquerades as a real zero-cost page.
        total_input_tokens: int | None = None
        total_output_tokens: int | None = None
        for attempt in range(1, self._OCR_ATTEMPTS + 1):
            try:
                result = await self._get_transcriber().transcribe_page(
                    EssayPageTranscriptionRequest(
                        image_path=image_path, mime_type=_guess_mime(image_path),
                        system_prompt=system_prompt,
                    )
                )
                if result.input_tokens is not None:
                    total_input_tokens = (total_input_tokens or 0) + result.input_tokens
                if result.output_tokens is not None:
                    total_output_tokens = (total_output_tokens or 0) + result.output_tokens
                tokens = result.tokens
                average_confidence = (
                    sum(t.confidence for t in tokens) / len(tokens) if tokens else 0.0
                )
                if average_confidence > best_average_confidence:
                    best_tokens = tokens
                    best_average_confidence = average_confidence
                if average_confidence >= self._MIN_AVERAGE_CONFIDENCE:
                    await self._finalize_page_tokens(
                        page, tokens, image_path, total_input_tokens, total_output_tokens,
                        allow_reconciliation=True, system_prompt=system_prompt,
                    )
                    return
                logger.warning(
                    "transcription attempt %d/%d for page %s scored low average "
                    "confidence %.3f, retrying",
                    attempt, self._OCR_ATTEMPTS, page.id, average_confidence,
                )
            except ProviderError as exc:
                last_error = exc
                logger.warning(
                    "transcription attempt %d/%d failed for page %s: %s",
                    attempt, self._OCR_ATTEMPTS, page.id, exc,
                )
                if isinstance(exc, ProviderRateLimitError) and attempt < self._OCR_ATTEMPTS:
                    await asyncio.sleep(self._RATE_LIMIT_BACKOFF_SECONDS)
        # Every attempt scored below the floor (or errored): the explicit
        # product decision is to always transcribe regardless of quality, so
        # a low-confidence result still beats no result at all - use
        # whichever attempt scored best rather than whichever ran last. The
        # token totals still reflect EVERY attempt made, not just the kept one.
        # Reconciliation is skipped here (allow_reconciliation=False): every
        # attempt already failed the page-average floor, so the whole page
        # is already headed for the student's hand-review screen regardless
        # - paying a 4th vision call to fix one or two words on an
        # already-unreliable page is not worth the extra cost/latency
        # (confirmed live 2026-09-27: this path can already mean 3 calls for
        # ONE page; a 20-page PDF hitting it repeatedly risks the whole
        # upload_document request timing out).
        if best_tokens is not None:
            await self._finalize_page_tokens(
                page, best_tokens, image_path, total_input_tokens, total_output_tokens,
                allow_reconciliation=False, system_prompt=system_prompt,
            )
            return
        raise last_error

    async def _finalize_page_tokens(
        self, page: EssaySubmissionPage, tokens: tuple, image_path: Path,
        total_input_tokens: int | None, total_output_tokens: int | None,
        *, allow_reconciliation: bool, system_prompt: str | None = None,
    ) -> None:
        # Persisted from the FIRST reading immediately, before attempting
        # reconciliation - if reconciliation raises anything unexpected
        # (only ProviderError is caught below; a genuine bug or a
        # cancellation is not), a perfectly good, already-paid-for reading
        # is never lost along with it.
        page.ocr_tokens = [
            {"text": t.text, "confidence": t.confidence, "start": t.start, "end": t.end}
            for t in tokens
        ]
        page.input_tokens = total_input_tokens
        page.output_tokens = total_output_tokens
        if not allow_reconciliation:
            return

        reconciled, extra_input_tokens, extra_output_tokens = (
            await self._reconcile_low_confidence_tokens(
                tokens, image_path, system_prompt=system_prompt
            )
        )
        if extra_input_tokens is not None:
            page.input_tokens = (page.input_tokens or 0) + extra_input_tokens
        if extra_output_tokens is not None:
            page.output_tokens = (page.output_tokens or 0) + extra_output_tokens
        if reconciled is not tokens:
            page.ocr_tokens = [
                {"text": t.text, "confidence": t.confidence, "start": t.start, "end": t.end}
                for t in reconciled
            ]

    async def _reconcile_low_confidence_tokens(
        self, tokens: tuple, image_path: Path, *, system_prompt: str | None = None
    ) -> tuple[tuple, int | None, int | None]:
        """A second, fully independent transcription of the SAME page,
        requested ONLY when `tokens` has at least one low-confidence entry -
        every other page never pays this extra call. The second reading is
        never shown the first one's text (no anchoring: it is exactly the
        same request `_ocr_page` already makes), so an agreement between the
        two is real independent evidence, not the second model just
        confirming what it was told.

        Returns (tokens, input_tokens, output_tokens): the last two are the
        SECOND call's own usage (None when no second call was made, e.g. no
        low-confidence tokens at all) - the caller adds this to the page's
        running total, since a reconciliation attempt costs real tokens
        whether or not it ends up confirming anything."""
        if not any(t.confidence < self._LOW_CONFIDENCE_TOKEN_THRESHOLD for t in tokens):
            return tokens, None, None

        # The word-splitting/diff/rebuild below is pure CPU work (confirmed
        # live 2026-09-27: 126-510ms for a dense handwritten page) - run off
        # the event loop, matching _measure_page_image/_split_pdf_pages'
        # existing asyncio.to_thread pattern, so one slow page never stalls
        # every other concurrent request this server is handling.
        precheck = await asyncio.to_thread(
            _prepare_reconciliation_precheck, tokens, self._LOW_CONFIDENCE_TOKEN_THRESHOLD
        )
        if precheck is None:
            return tokens, None, None

        try:
            second_result = await self._get_transcriber().transcribe_page(
                EssayPageTranscriptionRequest(
                    image_path=image_path, mime_type=_guess_mime(image_path),
                    system_prompt=system_prompt,
                )
            )
        except ProviderError as exc:
            logger.warning(
                "low-confidence reconciliation read failed for %s, keeping "
                "original tokens: %s", image_path, exc,
            )
            return tokens, None, None

        reconciled = await asyncio.to_thread(
            _apply_reconciliation, tokens, precheck, second_result.tokens,
            self._RECONCILED_TOKEN_CONFIDENCE,
        )
        return reconciled, second_result.input_tokens, second_result.output_tokens

    def _get_transcriber(self) -> EssayTranscriptionProvider:
        if self._transcriber is None:
            self._transcriber = build_essay_transcriber()
        return self._transcriber

    # Teto do ENVIO INDIVIDUAL do aluno (upload_document) - esse caminho roda
    # SINCRONO, dentro da mesma requisicao HTTP, e cada pagina pode disparar
    # ate 3-4 chamadas de visao (OCR + reconciliacao de baixa confianca,
    # ver _reconcile_low_confidence_tokens) - um PDF grande aqui arrisca a
    # propria requisicao estourar o tempo limite (confirmado ao vivo
    # 2026-09-27). O envio em lote do professor (services/essay_batch.py)
    # roda em background (background_tasks.add_task) e por isso usa seu
    # proprio teto, maior - ver _MAX_PDF_PAGES_BATCH la.
    _MAX_PDF_PAGES = 20

    @classmethod
    def _split_pdf_pages(
        cls, pdf_path: Path, *, max_pages: int | None = None
    ) -> list[tuple[Path, str | None]]:
        try:
            import pymupdf as _mu
        except ImportError:
            import fitz as _mu  # type: ignore

        limit = cls._MAX_PDF_PAGES if max_pages is None else max_pages
        dest_dir = pdf_path.parent / f"{pdf_path.stem}_pages"
        dest_dir.mkdir(exist_ok=True)
        doc = _mu.open(str(pdf_path))
        try:
            if len(doc) > limit:
                raise ValueError(
                    f"PDF has {len(doc)} pages, more than the {limit}-page limit"
                )
            results: list[tuple[Path, str | None]] = []
            for index in range(len(doc)):
                page = doc[index]
                pix = page.get_pixmap(dpi=200)
                page_path = dest_dir / f"page_{index + 1}.png"
                pix.save(str(page_path))
                # A digitally-typed PDF has an exact embedded text layer;
                # _extract_pdf_paragraphs returns "" (never raises) for a
                # purely scanned page with no such layer, so this is a safe,
                # cheap probe - upload_page decides whether the result is
                # substantial enough to trust over running vision OCR on the
                # rasterized image above.
                extracted_text = _extract_pdf_paragraphs(page) or None
                results.append((page_path, extracted_text))
            return results
        finally:
            doc.close()

    @staticmethod
    def _measure_page_image(image_path: Path) -> tuple[float, float]:
        """Pixel dimensions of an already-stored page image, via pymupdf -
        never Pillow (see this plan's Global Constraints). Needed so R3 can
        validate IMAGE_REGION annotations against real page bounds instead
        of leaving width/height permanently NULL, as R2 did."""
        try:
            import pymupdf as _mu
        except ImportError:
            import fitz as _mu  # type: ignore

        try:
            pix = _mu.Pixmap(str(image_path))
        except Exception as exc:
            # pymupdf raises its own exception hierarchy (e.g.
            # pymupdf.mupdf.FzErrorFormat), not ValueError, for an
            # undecodable image - normalize it so callers that only catch
            # ValueError (the route layer) still get a handled 422 instead
            # of an unhandled 500.
            raise ValueError(f"{image_path.name} is not a readable image file") from exc
        return float(pix.width), float(pix.height)

    async def list_pages(self, essay_submission_id: uuid.UUID) -> list[EssaySubmissionPage]:
        result = await self.session.execute(
            select(EssaySubmissionPage)
            .where(EssaySubmissionPage.essay_submission_id == essay_submission_id)
            .order_by(EssaySubmissionPage.page_number)
        )
        return list(result.scalars().all())

    async def review_page(
        self, *, essay_submission_id: uuid.UUID, page_number: int, reviewed_text: str
    ) -> EssaySubmissionPage:
        submission = await self.session.get(EssaySubmission, essay_submission_id)
        if submission is None:
            raise ValueError(f"EssaySubmission not found: {essay_submission_id}")
        if submission.status not in self._UPLOADABLE_STATUSES:
            raise ValueError(
                f"EssaySubmission {essay_submission_id} is {submission.status} - pages can "
                "only be reviewed while a submission is pending confirmation (spec §5.2 step 3)."
            )

        page = await self.session.scalar(
            select(EssaySubmissionPage).where(
                EssaySubmissionPage.essay_submission_id == essay_submission_id,
                EssaySubmissionPage.page_number == page_number,
            )
        )
        if page is None:
            raise ValueError(
                f"No page {page_number} on submission {essay_submission_id}"
            )
        page.reviewed_text = reviewed_text
        await self.session.flush()
        return page

    async def confirm_submission(self, essay_submission_id: uuid.UUID) -> EssaySubmission:
        submission = await self.session.get(EssaySubmission, essay_submission_id)
        if submission is None:
            raise ValueError(f"EssaySubmission not found: {essay_submission_id}")
        if submission.status not in self._UPLOADABLE_STATUSES:
            raise ValueError(
                f"EssaySubmission {essay_submission_id} is {submission.status}, not pending "
                "confirmation - a SUBMITTED or SUPERSEDED essay cannot be confirmed again."
            )

        pages = await self.list_pages(essay_submission_id)
        if not pages:
            raise ValueError(f"EssaySubmission {essay_submission_id} has no pages to confirm")

        if submission.anchor_mode == "TEXT_OFFSET":
            missing = [p.page_number for p in pages if p.reviewed_text is None]
            if missing:
                raise ValueError(
                    f"Pages not yet reviewed: {missing} - every page needs reviewed_text "
                    "before a transcribed submission can be confirmed"
                )
            full_text = "\n\n".join(p.reviewed_text for p in pages)
            submission.canonical_text = normalize_essay_text(full_text)
            submission.normalized_text_hash = essay_text_hash(full_text)
        # anchor_mode == "IMAGE_REGION": no transcription ran, canonical_text/
        # normalized_text_hash stay NULL - spec §5.3's documented consequence.

        # Resubmission (photo/PDF only - TYPED supersedes immediately in
        # start_typed_submission since it submits atomically): supersede the
        # previous SUBMITTED version of this essay_id now, in the same flush
        # as this one becoming SUBMITTED. start_photo_submission validated
        # eligibility but deliberately never superseded, so a resubmission
        # the student never finishes never leaves the essay with zero
        # SUBMITTED versions (spec §5.4).
        previous = await self.session.scalar(
            select(EssaySubmission).where(
                EssaySubmission.essay_id == submission.essay_id,
                EssaySubmission.status == "SUBMITTED",
                EssaySubmission.id != submission.id,
            )
        )
        if previous is not None:
            previous.status = "SUPERSEDED"

        submission.status = "SUBMITTED"
        submission.submitted_at = _utcnow()
        await self.session.flush()
        return submission


def _guess_mime(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"


def _extract_pdf_paragraphs(page) -> str:
    """Extracts a PDF page's embedded text as natural prose, paragraph
    breaks preserved, mid-paragraph line wraps joined into spaces.

    Confirmed live (2026-09-27): PyMuPDF's plain ``page.get_text()`` ends
    EVERY visual line with a single "\\n" - both a genuine paragraph break
    and a line the PDF just happened to wrap mid-sentence get the exact
    same single "\\n", with no way to tell them apart from the text alone.
    Feeding that straight into canonical_text meant the AI corrector - which
    naturally quotes a wrapped sentence as continuous prose, the way any
    reader would say it aloud - had its quote rejected as
    QUOTE_DOES_NOT_MATCH_TEXT (validated live: a real correction of a
    real, cleanly-typed essay failed on this exact mismatch, e.g. the
    canonical text read "...cuidado\\nrealizado..." where the model quoted
    "...cuidado realizado..." with a plain space). ``page.get_text("blocks")``
    groups by the PDF's own paragraph structure instead of by visual line,
    so paragraph boundaries survive as separate blocks while the wraps
    inside one paragraph get joined here.

    Clipped to below HEADER_REGION_FRACTION (essay_answer_sheet.py - the
    SAME constant essay_batch.py uses to crop the header out of the OCR
    image, see that module's docstring) - confirmed live 2026-10-05: without
    this clip, the header's own printed labels ("FOLHA DE REDACAO", "NOME
    COMPLETO DO PARTICIPANTE", "CPF", the 1-30 line-number column) and the
    student's own name/CPF spelled out letter-per-box all land at the START
    of canonical_text, ahead of the real essay. The corrector then
    (correctly, given what it was shown) flags IDENTIFICACAO_INDEVIDA - the
    student's full name genuinely appears inside the text sent for grading -
    and whether phase-2's alert re-review confirms or drops that alert
    decides a swing between 0 and a clean score for the SAME essay, which
    was the real cause of a batch's suspiciously bimodal 0/1000 scores, not
    model unreliability. The body never legitimately starts above this
    boundary (see essay_answer_sheet.py's own geometry), so clipping it away
    loses nothing of the real essay.
    """
    from .essay_answer_sheet import HEADER_REGION_FRACTION

    header_bottom = page.rect.height * HEADER_REGION_FRACTION
    body_clip = (0, header_bottom, page.rect.width, page.rect.height)
    blocks = page.get_text("blocks", clip=body_clip)
    paragraphs = []
    for block in blocks:
        text = block[4]
        # A block's own "\n"s are mid-paragraph line wraps, not paragraph
        # breaks (blocks ARE the paragraph boundaries) - join them with a
        # space, the way the sentence actually reads.
        joined = " ".join(text.split())
        if not joined:
            continue
        # The 01..30 line-number column (essay_answer_sheet.py's own ruled
        # lines, drawn down the left margin the whole length of the page -
        # LINE_COUNT/the loop that calls insert_text with f"{index+1:02d}")
        # sits inside the body clip and PyMuPDF gives each number its own
        # block. A real paragraph is never JUST 1-2 digits, so this can only
        # ever drop line-number noise, never real essay content.
        if joined.isdigit() and len(joined) <= 2:
            continue
        paragraphs.append(joined)
    return "\n\n".join(paragraphs)


__all__ = ["EssayResubmissionBlockedError", "EssaySubmissionService"]
