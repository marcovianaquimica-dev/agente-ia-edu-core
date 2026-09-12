"""Isolated PDF text-recovery layer (PHASE 10.6).

Why this exists
---------------
PHASE 10.5 dry-ran the ENEM 2016-2025 batch and found booklets whose embedded
text layer is not usable as-is. Two distinct failure modes:

1. Illegible text layer (needs OCR, not fixable deterministically)
   * ENEM 2021 D1/D2 - the printed provas embed subsetted fonts with no
     ToUnicode CMap. ``pypdf`` emits ``/gNN`` glyph codes; MuPDF emits symbol
     soup ("] ^ & ^ 8 8"). Neither is real text (legibility ~0.17-0.56 vs
     ~0.72-0.94 for a clean booklet). This module marks them ``OCR_PENDING``.

2. Legible text, broken structure (MuPDF recovers it)
   * ENEM 2025 D1 - ``pypdf`` splits/reorders the stylised "QUESTAO NN" heading
     ("Q\\nU\\nESTaO 01"), so the parser's ``^\\s*Questao\\s+(\\d+)`` header
     regex matches only 4 of 90 questions. MuPDF reads "Questao 01" on one line.
   * ENEM 2024 D1/D2 and 2025 D2 - ``pypdf`` glues a top-of-page heading to the
     previous page number across the form feed ("27\\x0cQUESTAO 95"); ``(?m)^``
     does not anchor after ``\\f`` so ~1/3 of headings are dropped. MuPDF's
     per-page text keeps each heading line-anchored.

This module provides a *separate, read-only* recovery path. It does NOT change
``PdfParser`` and is NOT wired into the ingestion pipeline: adopting recovered
text for real ingestion is a later phase and needs its own re-ingestion /
content-hash plan. Here it only measures and exposes both texts side by side.

Guarantees
----------
* Pure function of the input PDF bytes: no database, no network, no OpenAI, no
  filesystem writes, no mutation of the source.
* The native ``pypdf`` text is always preserved verbatim as ``native_text`` -
  recovery never overwrites it silently.
* ``pymupdf`` is an OPTIONAL import. When it is absent the result degrades to
  ``recovery_status = "RECOVERY_PENDING"`` instead of raising.
* Deterministic: identical input -> identical ``TextExtractionResult``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

# Header / option regexes kept byte-for-byte in sync with PdfParser so the
# diagnostics here describe exactly what the real parser would see.
_QUESTION_PATTERN = re.compile(r"(?mi)^\s*Questão\s+(\d+)\s*")
_OPTION_PATTERN = re.compile(
    r"(?ms)^\s*([A-E])\s{2,}(.+?)(?=^\s*[A-E]\s{2,}|\f|\Z)"
)
_GLYPH_TOKEN = re.compile(r"/g\d+")
_WORD_TOKEN = re.compile(r"[A-Za-zÀ-ÿ]{2,}")

NATIVE_PYPDF = "native_pypdf"
RECOVERED_PYMUPDF = "recovered_pymupdf"
METHOD_NONE = "none"

STATUS_OK = "OK"                       # native text is fine, no recovery needed
STATUS_RECOVERED = "RECOVERED"         # a recovery method produced usable text
STATUS_OCR_PENDING = "OCR_PENDING"     # text layer unusable, needs OCR/HTR (not done here)
STATUS_RECOVERY_PENDING = "RECOVERY_PENDING"  # degraded, no recovery backend available


@dataclass
class ExtractionScore:
    """Cheap, deterministic quality signals for one extraction of one PDF.

    ``legibility_ratio`` and ``word_like_ratio`` - not ``glyph_tokens`` - are the
    real quality gate. A font with no ToUnicode CMap can extract *without* any
    literal ``/gNN`` token yet still be unreadable symbol soup ("] ^ & ^ 8 8"),
    so counting ``/gNN`` alone is not enough. ``legibility_ratio`` is the share
    of non-space characters that are alphabetic; clean ENEM text sits at ~0.72
    to 0.94, the broken 2021 provas at ~0.17 to 0.56.
    """

    pages: int
    chars: int
    letters: int
    non_space_chars: int
    glyph_tokens: int             # literal "/gNN" occurrences (diagnostic only)
    legibility_ratio: float       # alpha chars / non-space chars
    word_like_ratio: float        # whitespace tokens that are 2+ letters, no digits/symbols
    question_headers: int          # raw _QUESTION_PATTERN matches
    distinct_numbers: int          # distinct question numbers seen
    number_min: int | None
    number_max: int | None
    five_option_runs: int          # questions with a clean A..E run (parser's own rule)

    def as_dict(self) -> dict:
        return {
            "pages": self.pages,
            "chars": self.chars,
            "letters": self.letters,
            "non_space_chars": self.non_space_chars,
            "glyph_tokens": self.glyph_tokens,
            "legibility_ratio": round(self.legibility_ratio, 4),
            "word_like_ratio": round(self.word_like_ratio, 4),
            "question_headers": self.question_headers,
            "distinct_numbers": self.distinct_numbers,
            "number_min": self.number_min,
            "number_max": self.number_max,
            "five_option_runs": self.five_option_runs,
        }


@dataclass
class TextExtractionResult:
    """Both texts for a PDF plus the recommendation. ``native_text`` is always set."""

    source_path: str
    native_text: str
    native_pages: list[str]
    recovered_text: str | None
    recovered_pages: list[str] | None
    extraction_method: str            # NATIVE_PYPDF | RECOVERED_PYMUPDF | METHOD_NONE
    confidence: float                 # 0..1 for the chosen text
    page_sources: list[str]           # per page: which method backs the chosen text
    recovery_status: str              # STATUS_*
    native_score: ExtractionScore
    recovered_score: ExtractionScore | None
    expected_questions: int | None
    notes: list[str] = field(default_factory=list)

    @property
    def chosen_text(self) -> str:
        """Best available text. Never mutates; callers still validate downstream."""
        if self.extraction_method == RECOVERED_PYMUPDF and self.recovered_text is not None:
            return self.recovered_text
        return self.native_text

    @property
    def chosen_pages(self) -> list[str]:
        if self.extraction_method == RECOVERED_PYMUPDF and self.recovered_pages is not None:
            return self.recovered_pages
        return self.native_pages

    def as_dict(self) -> dict:
        return {
            "source_path": self.source_path,
            "extraction_method": self.extraction_method,
            "recovery_status": self.recovery_status,
            "confidence": round(self.confidence, 4),
            "expected_questions": self.expected_questions,
            "native_score": self.native_score.as_dict(),
            "recovered_score": self.recovered_score.as_dict() if self.recovered_score else None,
            "page_sources": self.page_sources,
            "notes": list(self.notes),
        }


# --------------------------------------------------------------------------- #
# Extraction backends
# --------------------------------------------------------------------------- #


def extract_native_pages(path: Path | str) -> list[str]:
    """``pypdf`` per-page text - byte-for-byte what ``PdfParser.parse_file`` reads."""
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    return [page.extract_text() or "" for page in reader.pages]


def pymupdf_available() -> bool:
    try:
        import pymupdf  # noqa: F401
    except Exception:
        try:
            import fitz  # noqa: F401
        except Exception:
            return False
    return True


def extract_pymupdf_pages(path: Path | str) -> list[str] | None:
    """``pymupdf`` (MuPDF) per-page text, or ``None`` when the backend is absent.

    MuPDF resolves subsetted-font glyph ids through the embedded font's own cmap,
    which is why it recovers the 2021 provas without OCR or character guessing.
    """
    try:
        import pymupdf as _mu
    except Exception:
        try:
            import fitz as _mu  # type: ignore
        except Exception:
            return None
    doc = _mu.open(str(path))
    try:
        return [page.get_text("text") for page in doc]
    finally:
        doc.close()


# --------------------------------------------------------------------------- #
# Scoring
# --------------------------------------------------------------------------- #


def score_pages(pages: list[str]) -> ExtractionScore:
    text = "\f".join(pages)
    letters = sum(c.isalpha() for c in text)
    non_space = sum(1 for c in text if not c.isspace())
    glyphs = len(_GLYPH_TOKEN.findall(text))
    legibility = 1.0 if non_space == 0 else letters / non_space

    tokens = text.split()
    word_like = sum(1 for t in tokens if _WORD_TOKEN.fullmatch(t))
    word_ratio = 1.0 if not tokens else word_like / len(tokens)

    headers = list(_QUESTION_PATTERN.finditer(text))
    numbers = sorted({int(m.group(1)) for m in headers})

    five = 0
    for i, h in enumerate(headers):
        end = headers[i + 1].start() if i + 1 < len(headers) else len(text)
        body = text[h.end():end]
        opts = list(_OPTION_PATTERN.finditer(body))
        for j in range(max(0, len(opts) - 4)):
            if [m.group(1) for m in opts[j:j + 5]] == ["A", "B", "C", "D", "E"]:
                five += 1
                break

    return ExtractionScore(
        pages=len(pages),
        chars=len(text),
        letters=letters,
        non_space_chars=non_space,
        glyph_tokens=glyphs,
        legibility_ratio=legibility,
        word_like_ratio=word_ratio,
        question_headers=len(headers),
        distinct_numbers=len(numbers),
        number_min=numbers[0] if numbers else None,
        number_max=numbers[-1] if numbers else None,
        five_option_runs=five,
    )


# --------------------------------------------------------------------------- #
# Thresholds (deterministic, documented)
# --------------------------------------------------------------------------- #

# Calibrated on the ENEM 2016-2025 pilot provas. Clean booklets: legibility
# 0.72-0.94, word_like 0.59-0.68. The font-broken 2021 provas: legibility
# 0.17-0.56, word_like 0.09-0.44 - true whether extracted by pypdf or MuPDF.
LEGIBILITY_MIN = 0.65          # native below this == text layer is symbol soup
WORD_LIKE_MIN = 0.50           # secondary "encoding broken" signal
# Native text is "header-degraded" when it recovers fewer than this fraction of
# the expected question count (2025 D1: 4/90; 2024/2025 D2: 35-66/90).
HEADER_COVERAGE_MIN = 0.90
# With no expected count, fewer than this many distinct questions in a full
# booklet is itself a red flag.
DISTINCT_FLOOR = 60
# A recovery is accepted only if the recovered text is genuinely legible AND
# reaches header coverage (or clearly beats native when expected is unknown).
RECOVERED_LEGIBILITY_MIN = 0.68
RECOVERED_WORD_LIKE_MIN = 0.50
RECOVERED_COVERAGE_MIN = 0.90
# If the best text available is still below this legibility, no deterministic
# method will help - the booklet needs OCR/HTR (out of scope here).
OCR_LEGIBILITY_CEILING = 0.65


def _coverage(score: ExtractionScore, expected: int | None) -> float:
    if expected and expected > 0:
        return score.distinct_numbers / expected
    return 1.0


def _text_is_illegible(score: ExtractionScore) -> bool:
    return score.legibility_ratio < LEGIBILITY_MIN or score.word_like_ratio < WORD_LIKE_MIN


def _native_is_degraded(score: ExtractionScore, expected: int | None) -> list[str]:
    reasons: list[str] = []
    if _text_is_illegible(score):
        reasons.append(
            f"illegible text layer: legibility={score.legibility_ratio:.3f} "
            f"(min {LEGIBILITY_MIN}), word_like={score.word_like_ratio:.3f} "
            f"(min {WORD_LIKE_MIN})"
        )
    cov = _coverage(score, expected)
    if expected and cov < HEADER_COVERAGE_MIN:
        reasons.append(
            f"header_coverage={cov:.2f} ({score.distinct_numbers}/{expected}) < {HEADER_COVERAGE_MIN}"
        )
    if not expected and score.distinct_numbers < DISTINCT_FLOOR:
        reasons.append(
            f"distinct_numbers={score.distinct_numbers} < {DISTINCT_FLOOR} (no expected count given)"
        )
    return reasons


def recover_pdf_text(
    path: Path | str,
    *,
    expected_questions: int | None = None,
) -> TextExtractionResult:
    """Extract native text, judge it, and attach a recovery only if it helps.

    Never raises for a readable PDF with a degraded text layer: it returns a
    result whose ``recovery_status`` says what a later ingestion phase must do.
    """
    path = Path(path)
    native_pages = extract_native_pages(path)
    native_score = score_pages(native_pages)
    notes: list[str] = []

    degraded_reasons = _native_is_degraded(native_score, expected_questions)

    if not degraded_reasons:
        return TextExtractionResult(
            source_path=str(path),
            native_text="\f".join(native_pages),
            native_pages=native_pages,
            recovered_text=None,
            recovered_pages=None,
            extraction_method=NATIVE_PYPDF,
            confidence=min(1.0, native_score.legibility_ratio),
            page_sources=[NATIVE_PYPDF] * len(native_pages),
            recovery_status=STATUS_OK,
            native_score=native_score,
            recovered_score=None,
            expected_questions=expected_questions,
            notes=["native text passed all quality gates"],
        )

    notes.append("native degraded: " + "; ".join(degraded_reasons))

    recovered_pages = extract_pymupdf_pages(path)
    native_illegible = _text_is_illegible(native_score)

    if recovered_pages is None:
        notes.append("pymupdf backend not installed - cannot attempt recovery")
        status = (
            STATUS_OCR_PENDING
            if native_illegible and native_score.legibility_ratio < OCR_LEGIBILITY_CEILING
            else STATUS_RECOVERY_PENDING
        )
        return TextExtractionResult(
            source_path=str(path),
            native_text="\f".join(native_pages),
            native_pages=native_pages,
            recovered_text=None,
            recovered_pages=None,
            extraction_method=NATIVE_PYPDF,
            confidence=min(1.0, native_score.legibility_ratio),
            page_sources=[NATIVE_PYPDF] * len(native_pages),
            recovery_status=status,
            native_score=native_score,
            recovered_score=None,
            expected_questions=expected_questions,
            notes=notes,
        )

    recovered_score = score_pages(recovered_pages)
    rec_cov = _coverage(recovered_score, expected_questions)

    recovered_legible = (
        recovered_score.legibility_ratio >= RECOVERED_LEGIBILITY_MIN
        and recovered_score.word_like_ratio >= RECOVERED_WORD_LIKE_MIN
    )
    coverage_ok = (
        rec_cov >= RECOVERED_COVERAGE_MIN
        if expected_questions
        else recovered_score.distinct_numbers >= max(
            native_score.distinct_numbers + 1, DISTINCT_FLOOR
        )
    )
    recovery_good = recovered_legible and coverage_ok

    if recovery_good:
        confidence = min(
            1.0,
            recovered_score.legibility_ratio,
            rec_cov if expected_questions else 1.0,
        )
        notes.append(
            "pymupdf recovery accepted "
            f"(legibility={recovered_score.legibility_ratio:.3f}, "
            f"word_like={recovered_score.word_like_ratio:.3f}, "
            f"distinct={recovered_score.distinct_numbers}"
            + (f"/{expected_questions}" if expected_questions else "")
            + f"; native was legibility={native_score.legibility_ratio:.3f}, "
            f"distinct={native_score.distinct_numbers})"
        )
        return TextExtractionResult(
            source_path=str(path),
            native_text="\f".join(native_pages),
            native_pages=native_pages,
            recovered_text="\f".join(recovered_pages),
            recovered_pages=recovered_pages,
            extraction_method=RECOVERED_PYMUPDF,
            confidence=confidence,
            page_sources=[RECOVERED_PYMUPDF] * len(recovered_pages),
            recovery_status=STATUS_RECOVERED,
            native_score=native_score,
            recovered_score=recovered_score,
            expected_questions=expected_questions,
            notes=notes,
        )

    # Recovery attempted but not good enough.
    best_legibility = max(native_score.legibility_ratio, recovered_score.legibility_ratio)
    if not recovered_legible and best_legibility < OCR_LEGIBILITY_CEILING:
        status = STATUS_OCR_PENDING
    else:
        status = STATUS_RECOVERY_PENDING
    notes.append(
        "pymupdf recovery rejected "
        f"(legibility={recovered_score.legibility_ratio:.3f}, "
        f"word_like={recovered_score.word_like_ratio:.3f}, "
        f"distinct={recovered_score.distinct_numbers}"
        + (f"/{expected_questions}" if expected_questions else "")
        + f") -> {status}"
    )
    return TextExtractionResult(
        source_path=str(path),
        native_text="\f".join(native_pages),
        native_pages=native_pages,
        recovered_text="\f".join(recovered_pages),
        recovered_pages=recovered_pages,
        extraction_method=NATIVE_PYPDF,
        confidence=min(1.0, native_score.legibility_ratio),
        page_sources=[NATIVE_PYPDF] * len(native_pages),
        recovery_status=status,
        native_score=native_score,
        recovered_score=recovered_score,
        expected_questions=expected_questions,
        notes=notes,
    )
