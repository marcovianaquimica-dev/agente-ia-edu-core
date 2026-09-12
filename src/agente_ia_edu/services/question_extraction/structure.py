"""PHASE 27 - Question Extraction Engine: PDF structural analysis.

STEP 1-4 of the pipeline (spec s3):
    PDF -> structural analysis -> page segmentation -> text+coordinates ->
    image/graphic/table detection -> reading-order reconstruction

Does NOT confide in the raw linear text a PDF library hands back (spec s4):
every line is extracted with its page number and bounding box, kept
available for asset-association and source-traceability regardless of
column layout.

Two-column reordering (``detect_two_column_layout`` / column-major
ordering) is implemented and unit-tested in isolation, but is NOT enabled
by default: real educational PDFs routinely mix single-column prose with
multi-cell TABLES and multi-line FORMULAS whose fragments land at many
different x-offsets on the same page (verified against both real pilot
PDFs) - a naive "largest x-gap => two columns" split misreads a table row
as a second column and would silently corrupt reading order, which is far
worse than not reordering at all. The default reading order is therefore
the safe one: top-to-bottom, left-to-right ties broken by x - exactly what
both real golden PDFs need, and never wrong for genuinely single-column
text. ``extract_structure(..., use_column_detection=True)`` opts a caller
into column-major ordering for a document already known to be two-column.

This is a SEPARATE module from ``ingestion_parser.py`` - it shares no code
with, and never touches, the ENEM-specific ``PdfParser``.

Pure, deterministic, no I/O side effects beyond reading the given file.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from pathlib import Path

# Minimum horizontal gap (in PDF points) between two candidate column bands
# for a page to be treated as multi-column. Two US-letter/A4 columns are
# typically separated by 15-40pt of gutter; body text words rarely leave a
# gap this wide within a single column.
_COLUMN_GAP_THRESHOLD = 30.0
_MIN_LINES_PER_COLUMN = 3
_MIN_Y_OVERLAP_RATIO = 0.3

# PHASE 28 s9 - repeated_page_artifact_detection: a line sitting in the top/
# bottom margin band, with IDENTICAL text, on this many or more DISTINCT
# pages is a header/footer/running-title, never real question content.
_MARGIN_BAND_RATIO = 0.12
_MIN_ARTIFACT_PAGES = 3
_MIN_ARTIFACT_TEXT_LEN = 4


@dataclass(frozen=True)
class TextLine:
    """One reconstructed line of text, with its page and bounding box."""

    page: int  # 1-indexed
    x0: float
    y0: float
    x1: float
    y1: float
    text: str


@dataclass(frozen=True)
class PageImage:
    """One embedded raster image, kept as evidence - never reconstructed
    into text (spec s9/s10: never invent content for a visual element)."""

    page: int
    index: int
    bbox: tuple[float, float, float, float] | None
    width: int
    height: int
    digest: str


@dataclass
class DocumentStructure:
    document_hash: str
    page_count: int
    lines: list[TextLine] = field(default_factory=list)  # document reading order
    images: list[PageImage] = field(default_factory=list)
    pages_multicolumn: set[int] = field(default_factory=set)
    stripped_artifact_texts: list[str] = field(default_factory=list)

    def _joiners(self) -> list[str]:
        """The separator BEFORE each line (empty for the first). A
        vertical gap much larger than this page's typical line spacing -
        or a page break - becomes an extra blank line, i.e. a paragraph
        break. pymupdf's own ``get_text()`` represents the exact same
        signal as a blank output line; the bbox-based dict extraction
        this module uses instead has no such marker on its own, so it is
        reconstructed here from real coordinates rather than lost (the
        boundary detector's "preceded by a paragraph break" signal - spec
        s5's own "usar contexto, não apenas o número" - depends on it)."""
        if not self.lines:
            return []
        gaps = [
            b.y0 - a.y1 for a, b in zip(self.lines, self.lines[1:])
            if b.page == a.page and b.y0 >= a.y1
        ]
        typical = sorted(gaps)[len(gaps) // 2] if gaps else 2.0
        threshold = max(typical * 1.8, typical + 4.0)
        joiners = [""]
        for a, b in zip(self.lines, self.lines[1:]):
            if b.page != a.page or (b.y0 - a.y1) > threshold:
                joiners.append("\n\n")
            else:
                joiners.append("\n")
        return joiners

    def text(self) -> str:
        """The reconstructed, reading-order document text - one real '\\n'
        per line (two for a detected paragraph/page break), no
        whitespace-collapsing (PHASE 26's authorial parser bug: collapsing
        '\\n'/'\\f' into spaces destroyed every line-start anchor a
        boundary detector needs)."""
        joiners = self._joiners()
        return "".join(j + line.text for j, line in zip(joiners, self.lines))

    def offset_to_page(self, offset: int) -> int:
        """Map a character offset in ``text()`` back to a 1-indexed page."""
        cursor = 0
        page = self.lines[0].page if self.lines else 1
        for j, line in zip(self._joiners(), self.lines):
            line_len = len(j) + len(line.text)
            if cursor <= offset < cursor + line_len:
                return line.page
            cursor += line_len
            page = line.page
        return page

    def lines_in_range(self, start: int, end: int) -> list[TextLine]:
        """PHASE 28 - the TextLine objects (with real bbox/page) whose text
        contributed to ``text()[start:end]``. Used by
        ``reconstruction.py`` to attempt a LOCAL, per-question column fix
        without touching PHASE 27's proven, document-wide boundary
        detection (spec s16: acts strictly between EXTRACTION and
        VALIDATION)."""
        out: list[TextLine] = []
        cursor = 0
        for j, line in zip(self._joiners(), self.lines):
            line_start = cursor + len(j)
            line_end = line_start + len(line.text)
            if line_end > start and line_start < end:
                out.append(line)
            cursor = line_end
            if cursor >= end:
                break
        return out


def _file_hash(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def detect_two_column_layout(
    page_lines: list[TextLine], *, page_width: float | None = None,
) -> float | None:
    """Return the split x-coordinate if ``page_lines`` cleanly forms two
    side-by-side text columns, else None. Deliberately strict (spec s4
    demands correct column ordering, not a plausible guess): requires
    - a single dominant x-gap, not the largest of many scattered gaps -
      every line must fall on one side or the other of ONE split point
      (a table's many cell x-offsets fail this: they straddle several gaps);
    - both sides individually span most of the page's vertical extent
      (a genuine column runs top-to-bottom; a table row's stray fragment
      does not);
    - the split sits away from either edge (a real gutter is near the
      middle, not a margin artifact)."""
    if len(page_lines) < 2 * _MIN_LINES_PER_COLUMN:
        return None
    xs = sorted(ln.x0 for ln in page_lines)
    gaps = [(xs[i] - xs[i - 1], (xs[i] + xs[i - 1]) / 2) for i in range(1, len(xs))]
    gaps.sort(reverse=True)
    if not gaps or gaps[0][0] < _COLUMN_GAP_THRESHOLD:
        return None
    best_gap, split_x = gaps[0]
    # a genuine 2-column page has exactly ONE gap this wide; a table/formula
    # page has several comparably-wide gaps between its many cell x-offsets.
    if len(gaps) > 1 and gaps[1][0] > best_gap * 0.6:
        return None
    if page_width and not (page_width * 0.25 < split_x < page_width * 0.75):
        return None

    left = [ln for ln in page_lines if ln.x0 < split_x]
    right = [ln for ln in page_lines if ln.x0 >= split_x]
    if len(left) < _MIN_LINES_PER_COLUMN or len(right) < _MIN_LINES_PER_COLUMN:
        return None
    left_y = (min(ln.y0 for ln in left), max(ln.y1 for ln in left))
    right_y = (min(ln.y0 for ln in right), max(ln.y1 for ln in right))
    overlap = max(0.0, min(left_y[1], right_y[1]) - max(left_y[0], right_y[0]))
    span = max(left_y[1], right_y[1]) - min(left_y[0], right_y[0])
    if span <= 0 or (overlap / span) < _MIN_Y_OVERLAP_RATIO:
        return None
    return split_x


def detect_repeated_page_artifacts(
    pages: list[tuple[int, float, list[TextLine]]],
) -> set[str]:
    """PHASE 28 s9 - ``pages``: [(page_no, page_height, page_lines), ...].
    Returns the set of exact line texts that behave like a running header/
    footer: identical text, sitting in the top/bottom margin band, on at
    least ``_MIN_ARTIFACT_PAGES`` distinct pages. Deterministic, no
    heuristic beyond exact-text + position repetition - never strips a
    line that merely LOOKS similar (spec s9's own caution)."""
    margin_hits: dict[str, set[int]] = {}
    for page_no, page_height, lines in pages:
        top_cut = page_height * _MARGIN_BAND_RATIO
        bottom_cut = page_height * (1 - _MARGIN_BAND_RATIO)
        for ln in lines:
            if len(ln.text) < _MIN_ARTIFACT_TEXT_LEN:
                continue
            if ln.y1 <= top_cut or ln.y0 >= bottom_cut:
                margin_hits.setdefault(ln.text, set()).add(page_no)
    return {text for text, pages_seen in margin_hits.items() if len(pages_seen) >= _MIN_ARTIFACT_PAGES}


def _reading_order(page_lines: list[TextLine], *, use_column_detection: bool,
                   page_width: float | None = None) -> tuple[list[TextLine], bool]:
    """Default: top-to-bottom, x as tie-break - safe for both single-column
    prose and pages containing tables/formulas (see module docstring). Only
    when ``use_column_detection`` is explicitly requested AND the strict
    two-column test passes does this reorder column-major."""
    if use_column_detection:
        split_x = detect_two_column_layout(page_lines, page_width=page_width)
        if split_x is not None:
            left = sorted((ln for ln in page_lines if ln.x0 < split_x), key=lambda ln: (ln.y0, ln.x0))
            right = sorted((ln for ln in page_lines if ln.x0 >= split_x), key=lambda ln: (ln.y0, ln.x0))
            return left + right, True
    return sorted(page_lines, key=lambda ln: (ln.y0, ln.x0)), False


def extract_structure(pdf_path: Path, *, use_column_detection: bool = False) -> DocumentStructure:
    """Open the PDF read-only and build a DocumentStructure. Never writes,
    moves, or mutates the source file. ``use_column_detection`` opts into
    column-major reordering for pages that strictly pass the two-column
    test (see module docstring for why this defaults to off)."""
    import fitz  # PyMuPDF - already a project dependency

    doc = fitz.open(str(pdf_path))
    document_hash = _file_hash(pdf_path)
    all_lines: list[TextLine] = []
    images: list[PageImage] = []
    multicolumn_pages: set[int] = set()

    try:
        # -- pass 1: read every page's raw lines (unordered) so repeated
        #    header/footer artifacts can be identified DOCUMENT-WIDE before
        #    any page is finalised (spec s9) --
        raw_pages: list[tuple[int, float, list[TextLine]]] = []
        for page_index in range(len(doc)):
            page = doc[page_index]
            page_no = page_index + 1
            raw = page.get_text("dict")
            page_lines: list[TextLine] = []
            for block in raw.get("blocks", []):
                if block.get("type") != 0:  # 0 = text block
                    continue
                for line in block.get("lines", []):
                    spans = line.get("spans", [])
                    text = "".join(s.get("text", "") for s in spans)
                    stripped = text.strip()
                    if not stripped:
                        continue
                    bbox = line.get("bbox", (0.0, 0.0, 0.0, 0.0))
                    page_lines.append(TextLine(
                        page=page_no, x0=bbox[0], y0=bbox[1], x1=bbox[2], y1=bbox[3],
                        text=stripped,
                    ))
            raw_pages.append((page_no, page.rect.height, page_lines))

        artifact_texts = detect_repeated_page_artifacts(raw_pages)

        # -- pass 2: strip artifacts, then reconstruct reading order --
        for page_index, (page_no, _page_height, page_lines) in enumerate(raw_pages):
            page = doc[page_index]
            page_lines = [ln for ln in page_lines if ln.text not in artifact_texts]
            ordered, is_multi = _reading_order(
                page_lines, use_column_detection=use_column_detection, page_width=page.rect.width)
            all_lines.extend(ordered)
            if is_multi:
                multicolumn_pages.add(page_no)

            for img_index, img in enumerate(page.get_images(full=True)):
                xref = img[0]
                try:
                    bbox = tuple(page.get_image_bbox(img))
                except Exception:
                    bbox = None
                try:
                    pix = fitz.Pixmap(doc, xref)
                    width, height = pix.width, pix.height
                    digest = hashlib.sha256(pix.samples).hexdigest()
                    pix = None
                except Exception:
                    width = height = 0
                    digest = f"page{page_no}-img{img_index}-{xref}"
                images.append(PageImage(
                    page=page_no, index=img_index, bbox=bbox,
                    width=width, height=height, digest=digest,
                ))

        return DocumentStructure(
            document_hash=document_hash, page_count=len(doc),
            lines=all_lines, images=images, pages_multicolumn=multicolumn_pages,
            stripped_artifact_texts=sorted(artifact_texts),
        )
    finally:
        doc.close()
