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
import re
from dataclasses import dataclass, field, replace
from pathlib import Path

# Minimum horizontal gap (in PDF points) between two candidate column bands
# for a page to be treated as multi-column. Two US-letter/A4 columns are
# typically separated by 15-40pt of gutter; body text words rarely leave a
# gap this wide within a single column.
_COLUMN_GAP_THRESHOLD = 30.0
_MIN_LINES_PER_COLUMN = 3
_MIN_Y_OVERLAP_RATIO = 0.3

# Minimum fraction (of the SHORTER line's own height) that two lines' y
# extents must overlap by to be treated as the same visual row when
# breaking reading-order ties. Real PDFs routinely draw two side-by-side
# fragments (e.g. two answer-option labels) as independently-positioned
# text spans that are visually on the exact same baseline but whose y0
# differs by a sub-pixel amount (found live on a real UERJ exam page:
# "(A)" at y0=239.9367 vs "(B)" at y0=239.9316, both ~13pt tall - a
# ~0.005pt difference, invisible to the eye, but enough to flip a naive
# (y0, x0) sort and swap the two labels' order). A plain numeric y0
# tolerance would need re-tuning per document (font size varies), so this
# compares actual bounding-box OVERLAP instead, which scales with each
# line's own height. 0.5 safely covers same-row jitter (near-total
# overlap, as in the UERJ case) while never merging two genuinely
# different physical lines, whose y-extents normally do not overlap at
# all once separated by real line spacing.
_ROW_OVERLAP_RATIO = 0.5

# A genuine column gutter is a vertical strip no line of running text ever
# crosses - real prose wraps within its own column, never past the gutter.
# How much a genuine gutter's own lines overshoot past its midpoint varies
# a lot by document (measured 8% of a page's lines on one real PUC-Rio
# page, 35% on a real FUVEST page - both single, correct gutters), so
# there is no absolute crossing-rate cutoff safe to apply the same way
# across documents (see detect_two_column_layout's own docstring for the
# RELATIVE comparison used instead: how much cleaner one candidate gap is
# than another, on the SAME page, rather than against a fixed number).

# A real column is made of MANY lines sharing close x0s. A ONE-OFF line at
# some x-position (a source citation under a graph, a running page banner)
# is not a column - but if left in the gap search on equal footing with
# real columns, a lone line sitting between two genuine column clusters
# splits the one true gutter into two smaller, comparably-wide gaps and
# the "single dominant gap" test below rejects the page outright (found on
# a real FUVEST page: two independent side-by-side questions, each a
# dense column, plus a single-line citation caption sitting almost exactly
# between them). x0s are bucketed at this tolerance and a bucket must
# recur at least this many times to count as a real column edge; grouping
# absorbs ordinary indentation variance (a wrapped continuation line vs.
# its own marker) without merging two genuinely different columns, whose
# gutter is always much wider than this.
_X0_CLUSTER_TOLERANCE = 5.0
_MIN_X0_CLUSTER_OCCURRENCES = 2

# A cluster this small is never itself a candidate COLUMN BOUNDARY, even
# though it is real, legitimate content once a side has already been
# decided (found on a real FUVEST exam: an inline image caption - "(1)",
# "(2)", "(3)", labelling three stacked photos referenced by the LEFT
# question's own "Observe as imagens:" - sits at an x-position between two
# real questions, only 3 short lines. Both ways of splitting around it
# pass every other structural check equally well, an ambiguity no other
# signal here safely resolves - so it must never be allowed to split gap
# search into two contenders in the first place). Every confirmed-bad tiny
# cluster measured so far (this caption, a 2-line "Note e adote:" aside, a
# 2-line compact answer sub-grid) has 3 or fewer lines; every real
# column - including a real embedded table's own sub-columns - has been
# measured with 6 or more. Set with margin below the latter and above the
# former.
_MIN_GAP_CANDIDATE_CLUSTER_SIZE = 4

# A vertical strip that NO line's [x0, x1) interval ever crosses at all is
# stronger evidence of a real column gutter than any x0-clustering gap
# below - "no line straddles it" is exactly what a column boundary means,
# by definition, not merely a heuristic proxy for it (see
# detect_two_column_layout's own docstring for the real regression this
# was measured against: a genuine FUVEST gutter of ~26pt, invisible to
# the x0-clustering approach because the LEFT column's own internal
# content produced closer, competing x0 gaps). Measured 24-26pt across
# the real corpus pages that have one; set with margin below that and
# comfortably above ordinary inter-line noise (this is an x1-to-x0
# CONTENT gap, narrower by nature than an x0-to-x0 gap between two
# columns' own left margins, so reusing _COLUMN_GAP_THRESHOLD here would
# have missed the very case this constant exists to catch).
_ZERO_CROSSING_GAP_THRESHOLD = 20.0

# A genuine wrapped-prose column uses most of its own width on most lines.
# A short item-marker list ("I.", "II.", "III." ...) sitting beside
# unrelated content produces a band that LOOKS like a column geometrically
# (one consistent x0, spans most of the page vertically) but is not one -
# reordering it column-major destroys the per-row marker/content pairing
# (found on a real UECE exam). A line under this fraction of its own
# side's widest line counts as "narrow".
#
# The limit below is about how MANY narrow lines a real column tolerates,
# not whether it has any: a real UECE marker column is ~89% narrow lines
# (every marker plus one genuine wide line, the sole exception) - the
# marker IS the column. A real FUVEST physics question, by contrast, is a
# genuine wrapped-prose column that also happens to carry a handful of
# short structural lines - its own number marker, a "Note e adote:"
# aside, a source citation tail, and five short NUMERIC options ("(A)
# 0degC" .. "(E) 275degC") instead of the sentence-length options seen
# elsewhere in the same exam - measured at ~39% narrow on the real page.
#
# A second real FUVEST page pushed this further: a "choose the correct
# GRAPH" question (the five options are each just a bare letter - the
# actual graphs are IMAGES, never text) beside a second question whose own
# options are short numeric/chemical fragments (superscripts, ionic
# half-reactions, voltage readings) - measured at 67% and 69% narrow on
# the two sides of that real page, a clear MAJORITY on BOTH sides, yet
# still two genuine questions, not marker columns: the narrow lines are
# heterogeneous, structurally-legitimate content belonging to those SAME
# questions, not one repeated short label paired row-by-row with unrelated
# content on the other side (which is what makes UECE's case, at 89%,
# still worth catching). Set with real margin below UECE's ratio and
# above this page's own.
_NARROW_LINE_WIDTH_RATIO = 0.25
_MAX_NARROW_LINE_FRACTION = 0.75

# A side whose narrow fraction exceeds the limit above is still not a
# marker/label list if it has a real backbone of substantial content: the
# UECE marker-column regression the limit exists to catch has exactly ONE
# non-narrow line (89% narrow) - a single outlier, not a backbone. A real
# FUVEST question with two embedded tables (mostly short cells: option
# values, single-word row labels) measured 83% narrow - between the
# documented legitimate max (69%, a graph-choice question beside a
# fragment-heavy one) and UECE's 89% - yet has 11 genuinely full-width
# statement lines, nowhere close to a one-line outlier. Only reject when
# BOTH the fraction is high AND there is no such backbone.
_MIN_SUBSTANTIAL_LINES = 4

# Some real exam layouts (found on a real FUVEST booklet) print the question
# number ALONE on its own line - no "." or ")", no content until the next
# line - a convention _MARKER (boundary.py) cannot see: it deliberately
# requires the delimiter and body on the SAME line, so a blank line before a
# bare number stays a meaningful paragraph-break signal rather than being
# swallowed. A bare digit-only line is trusted as a real marker only when
# BOTH:
#  - its x-position recurs at least this many times across the WHOLE
#    document - a genuine column start reused by many questions, not a
#    one-off (a chart axis value, a footnote number) that happens to sit
#    alone on its line;
#  - consecutive occurrences at that x-position are spaced FAR apart
#    vertically - a real question is a whole block of content, never
#    packed line-tight. Column position alone is NOT enough: a real UECE
#    exam has a densely NUMBERED-LINE passage (every ~11pt, ordinary
#    single-line spacing) at one consistent x-position, which recurs far
#    more often than any real per-question marker column ever would but
#    is not one - only real question markers on a real FUVEST exam were
#    found spaced 130pt+ apart (a whole question's worth of content);
#  - the vertical position VARIES meaningfully from page to page - a real
#    marker's y0 depends on how much content came before it, so it moves
#    around a lot across the document (stdev 150pt+ on both a real FUVEST
#    and a real PUC-Rio exam). A printed running page number sits at
#    practically the SAME y0 on every page (stdev ~5pt on a real PUC-Rio
#    exam) and increments with the page - detect_repeated_page_artifacts
#    above can never catch it (that check requires IDENTICAL repeated
#    text; a page number's text changes every page by design).
_STANDALONE_NUMBER = re.compile(r"^\d{1,3}$")
_MIN_MARKER_COLUMN_OCCURRENCES = 6
_MIN_MARKER_VERTICAL_GAP = 40.0
_MIN_MARKER_Y_STDEV = 20.0

# A reference sheet handed out WITH an exam (found on a real PUC-Rio exam: a
# full periodic table of elements, one page) can independently satisfy every
# check above: each of its ~10 group COLUMNS is its own x-position, recurring
# many times (one row per period), spaced 40pt+ apart, at a high-variance y -
# geometrically indistinguishable, column by column, from a real marker. The
# giveaway a real exam page never produces is DENSITY: a periodic table page
# has over a hundred free-standing bare numbers at once (141, measured on the
# real page); a genuine exam page has at most a handful of question markers,
# never a page flooded with isolated digits. Any bare number sitting on a
# page whose own bare-number count exceeds this is excluded before
# clustering even starts - a flooded page is reference material, not
# candidate marker evidence, no matter how cleanly one x-position on it
# clusters.
_MAX_BARE_NUMBERS_PER_PAGE = 30
# Sentinel appended to a CONFIRMED standalone marker's own text, instead of
# a plain "." - a real "N.\n<content>" pattern already occurs naturally
# elsewhere in some real documents (found on a real UECE exam: an
# uncut answer-bubble grid template, "01.\n02.\n03. ...", that a plain "."
# would have let boundary.py's marker regex match as if it were real
# questions, colliding with the genuine question numbers). A Private Use
# Area character cannot occur in real extracted PDF text, so boundary.py's
# marker for this convention can ONLY ever match a line THIS function
# itself confirmed via geometry - never a coincidental pattern already
# present in the document.
STANDALONE_MARKER_SENTINEL = ""


def _is_sparse_enough(same_x0_candidates: list[TextLine]) -> bool:
    """True when consecutive same-page occurrences are spaced far enough
    apart to plausibly each be a whole question - false for a densely
    packed numbered-line list (ordinary single-line spacing)."""
    by_page: dict[int, list[float]] = {}
    for ln in same_x0_candidates:
        by_page.setdefault(ln.page, []).append(ln.y0)
    for y0s in by_page.values():
        y0s.sort()
        for a, b in zip(y0s, y0s[1:]):
            if (b - a) < _MIN_MARKER_VERTICAL_GAP:
                return False
    return True


def _has_content_dependent_position(same_x0_candidates: list[TextLine]) -> bool:
    """True when y0 varies meaningfully across the group - false for a
    printed running page number, which sits at practically the same y0 on
    every page regardless of content (spec: see the constant's own
    docstring above)."""
    if len(same_x0_candidates) < 2:
        return False
    y0s = [ln.y0 for ln in same_x0_candidates]
    mean = sum(y0s) / len(y0s)
    variance = sum((y - mean) ** 2 for y in y0s) / len(y0s)
    return variance ** 0.5 >= _MIN_MARKER_Y_STDEV


def _normalize_standalone_number_markers(lines: list[TextLine]) -> list[TextLine]:
    """Confirmed standalone-marker lines get ``STANDALONE_MARKER_SENTINEL``
    appended to their OWN text - never merged with a neighbouring line, so
    line count/geometry/paragraph-break gaps are completely untouched."""
    candidates = [ln for ln in lines if _STANDALONE_NUMBER.match(ln.text)]
    if not candidates:
        return lines
    bare_count_by_page: dict[int, int] = {}
    for ln in candidates:
        bare_count_by_page[ln.page] = bare_count_by_page.get(ln.page, 0) + 1
    candidates = [ln for ln in candidates if bare_count_by_page[ln.page] <= _MAX_BARE_NUMBERS_PER_PAGE]
    if not candidates:
        return lines
    by_x0: dict[int, list[TextLine]] = {}
    for ln in candidates:
        by_x0.setdefault(round(ln.x0), []).append(ln)
    confirmed_x0 = {
        x0 for x0, group in by_x0.items()
        if len(group) >= _MIN_MARKER_COLUMN_OCCURRENCES
        and _is_sparse_enough(group)
        and _has_content_dependent_position(group)
    }
    if not confirmed_x0:
        return lines
    return [
        replace(ln, text=ln.text + STANDALONE_MARKER_SENTINEL)
        if _STANDALONE_NUMBER.match(ln.text) and round(ln.x0) in confirmed_x0
        else ln
        for ln in lines
    ]

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


def _recurring_lines(page_lines: list[TextLine]) -> list[TextLine]:
    """The lines whose x-position (bucketed at ``_X0_CLUSTER_TOLERANCE``)
    recurs at least ``_MIN_X0_CLUSTER_OCCURRENCES`` times - i.e. the lines
    that plausibly belong to a real column, as opposed to a one-off caption
    or banner line (see the constants' own docstring above)."""
    buckets: dict[float, int] = {}
    for ln in page_lines:
        bucket = round(ln.x0 / _X0_CLUSTER_TOLERANCE)
        buckets[bucket] = buckets.get(bucket, 0) + 1
    return [
        ln for ln in page_lines
        if buckets[round(ln.x0 / _X0_CLUSTER_TOLERANCE)] >= _MIN_X0_CLUSTER_OCCURRENCES
    ]


def _large_clusters(lines: list[TextLine], min_size: int) -> list[TextLine]:
    """Lines belonging to an x0 cluster (bucketed the same way as
    ``_recurring_lines``) with at least ``min_size`` members - the lines
    substantial enough to plausibly DEFINE a column boundary, as opposed
    to a small aside or inline caption that is real content once a side
    has been decided, but must never itself split gap search into
    contenders (see ``_MIN_GAP_CANDIDATE_CLUSTER_SIZE``'s own docstring)."""
    buckets: dict[float, list[TextLine]] = {}
    for ln in lines:
        buckets.setdefault(round(ln.x0 / _X0_CLUSTER_TOLERANCE), []).append(ln)
    return [ln for group in buckets.values() if len(group) >= min_size for ln in group]


def _zero_crossing_gaps(page_lines: list[TextLine]) -> list[tuple[float, float]]:
    """(gap_width, split_x) for every vertical strip on the page that no
    line's [x0, x1) interval crosses at all, widest first. Computed by
    merging every line's own horizontal extent into disjoint covered
    intervals and reading the empty space between them - never invents a
    boundary a real line's content actually spans."""
    intervals = sorted((ln.x0, ln.x1) for ln in page_lines if ln.x1 > ln.x0)
    if not intervals:
        return []
    merged = [list(intervals[0])]
    for x0, x1 in intervals[1:]:
        if x0 <= merged[-1][1]:
            merged[-1][1] = max(merged[-1][1], x1)
        else:
            merged.append([x0, x1])
    gaps = [
        (start - end, (start + end) / 2)
        for (_, end), (start, _) in zip(merged, merged[1:])
        if start - end >= _ZERO_CROSSING_GAP_THRESHOLD
    ]
    return sorted(gaps, reverse=True)


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
    # The "one-off line corrupts the gap search" failure mode (see the
    # constants' own docstring) is a PAGE-level phenomenon - a stray
    # citation caption or running footer only exists at page scope. This
    # same function is also called by ``reconstruction.py`` on just the
    # ~5-40 lines of ONE question, with no ``page_width`` (its own
    # existing signal for "this is a local, not a page-level, call" - see
    # the edge-proximity check below, which is skipped the same way for
    # the same reason). At that small a scope a genuine local column can
    # legitimately be evidenced by as few as 2-3 lines, and requiring
    # recurrence there does more harm than good (regression measured on a
    # real ITA exam: filtering singleton x0s changed which LOCAL splits
    # reconstruction.py finds, not just the page-level cases this was
    # meant to fix) - so only apply the recurring-x0 filter when acting at
    # real page scope.
    recurring = _recurring_lines(page_lines) if page_width else page_lines

    def _verified_split(candidates: list[tuple[float, float]]) -> float | None:
        for _gap, split_x in candidates:
            if page_width and not (page_width * 0.25 < split_x < page_width * 0.75):
                continue
            left = [ln for ln in page_lines if ln.x0 < split_x]
            right = [ln for ln in page_lines if ln.x0 >= split_x]
            if len(left) < _MIN_LINES_PER_COLUMN or len(right) < _MIN_LINES_PER_COLUMN:
                continue
            # The "spans most of the page" evidence must come from the column's
            # own recurring lines, never from a one-off line (a running page-
            # number footer, say) that happens to land on this side of the
            # split purely by x-coordinate - such a stray line can otherwise
            # stretch a side's apparent range far past its real content and
            # let a compact same-question answer grid (e.g. options D/E of one
            # question set beside A/B/C, just 2 lines tall) masquerade as a
            # genuine full-height column (found on a real ITA exam page).
            left_recurring = [ln for ln in left if ln in recurring]
            right_recurring = [ln for ln in right if ln in recurring]
            if not left_recurring or not right_recurring:
                continue
            left_y = (min(ln.y0 for ln in left_recurring), max(ln.y1 for ln in left_recurring))
            right_y = (min(ln.y0 for ln in right_recurring), max(ln.y1 for ln in right_recurring))
            overlap = max(0.0, min(left_y[1], right_y[1]) - max(left_y[0], right_y[0]))
            span = max(left_y[1], right_y[1]) - min(left_y[0], right_y[0])
            if span <= 0 or (overlap / span) < _MIN_Y_OVERLAP_RATIO:
                continue
            ok = True
            for side in (left, right):
                widths = [ln.x1 - ln.x0 for ln in side]
                max_w = max(widths)
                if max_w <= 0:
                    ok = False
                    break
                non_narrow_count = sum(1 for w in widths if w >= _NARROW_LINE_WIDTH_RATIO * max_w)
                narrow_fraction = 1 - non_narrow_count / len(widths)
                if narrow_fraction > _MAX_NARROW_LINE_FRACTION and non_narrow_count < _MIN_SUBSTANTIAL_LINES:
                    ok = False
                    break
            if not ok:
                continue
            return split_x
        return None

    # Same page-level-only scoping as the recurring-x0 filter above, and
    # for the same reason: at local (per-question) scope a genuine column
    # can legitimately be evidenced by as few as 2-3 lines, so only a
    # page-level call restricts gap-search candidates to substantial
    # clusters (see _MIN_GAP_CANDIDATE_CLUSTER_SIZE's own docstring).
    gap_search_lines = _large_clusters(recurring, _MIN_GAP_CANDIDATE_CLUSTER_SIZE) if page_width else recurring

    if page_width:
        # A page-wide vertical strip that NO line's own extent crosses at
        # all is strictly stronger evidence of a real gutter than any
        # width-based x0-clustering candidate below - tried first, and
        # wins outright when it also passes the same structural checks
        # (see _zero_crossing_gaps and _ZERO_CROSSING_GAP_THRESHOLD's own
        # docstrings for the real FUVEST regression this fixes). Computed
        # from the SAME substantial-cluster-filtered lines as the legacy
        # gap search below, for the same reason: a synthetic multi-cell
        # table whose every cell recurs only 2-3 times (never a real
        # column) must not manufacture a spurious empty strip between two
        # of its own small, filtered-out clusters (regression caught by
        # this exact real fixture: without the filter, a gap between two
        # groups of 3-recurring cells passed every remaining check purely
        # because their fabricated uniform widths never trip the narrow-
        # line guard either). A table embedded inside one column still has
        # some line spanning across its own internal sub-gaps (a header
        # row, a border) - it is never truly page-wide empty - so this
        # never fires for the embedded-table case the rest of this
        # function exists to guard against; only a real second column
        # produces one.
        zero_crossing_split = _verified_split(_zero_crossing_gaps(gap_search_lines))
        if zero_crossing_split is not None:
            return zero_crossing_split

    xs = sorted(ln.x0 for ln in gap_search_lines)
    if len(xs) < 2:
        return None
    gaps = [(xs[i] - xs[i - 1], (xs[i] + xs[i - 1]) / 2) for i in range(1, len(xs))]
    gaps = [g for g in gaps if g[0] >= _COLUMN_GAP_THRESHOLD]
    if page_width:
        # A gap whose split point could never itself pass the basic
        # minimum-lines-per-column floor (below) is not a real competing
        # column hypothesis - it is noise (found on a real FUVEST exam: a
        # 2-line "Note e adote:" instruction aside, indented well past the
        # right column's own text start, produced a gap almost as wide as
        # the TRUE gutter between two real questions). Such a gap must
        # never be allowed to trigger the ambiguity guard below and reject
        # a page that otherwise has exactly one genuine candidate.
        gaps = [
            (g, x) for g, x in gaps
            if sum(1 for ln in page_lines if ln.x0 < x) >= _MIN_LINES_PER_COLUMN
            and sum(1 for ln in page_lines if ln.x0 >= x) >= _MIN_LINES_PER_COLUMN
        ]
    if not gaps:
        return None
    gaps.sort(reverse=True)

    # The legacy rule - trust only the single widest gap, and only if no
    # second gap comes within 0.6x of it (spec: a multi-cell table has
    # several comparably-wide gaps between its many cell x-offsets) - is
    # the DEFAULT in every case, including page-level calls: it is the one
    # already verified safe across the whole real-exam corpus, and how
    # much a genuine gutter's own lines naturally overshoot past its
    # midpoint varies a lot by document (measured 8% on one real PUC-Rio
    # page, 35% on a real FUVEST page. with equally single, correct
    # gutters) - there is no one absolute "too much crossing" cutoff safe
    # to apply the same way across documents.
    best_gap, split_x = gaps[0]
    legacy_ambiguous = len(gaps) > 1 and gaps[1][0] > best_gap * 0.6
    candidates = [(best_gap, split_x)]

    if page_width and len(gaps) > 1:
        # A real page can instead have an embedded TABLE sitting entirely
        # inside one column, indenting its own cells well past that
        # column's usual text start - producing an x0-gap (between the
        # table's own columns) that rivals or even exceeds the true
        # gutter's width (found on a real PUC-Rio exam: a comparison table
        # inside the left column, beside a genuine second question on the
        # right). Unlike varying overshoot rates across different
        # documents, a RELATIVE contrast on the SAME page is trustworthy:
        # a genuine gutter is a region no line of running text ever
        # crosses, while an indentation gap INSIDE one wrapped-prose
        # column is crossed by every one of that column's own full-width
        # lines running right over it - so on the one page that actually
        # has both, the true gutter's own crossing rate sits far below the
        # table gap's, not just somewhat below it. Only ever override the
        # legacy widest-gap choice when some other candidate is
        # DRAMATICALLY (2x+) cleaner than it - never merely a little
        # cleaner, which is exactly the ordinary page-to-page variance the
        # legacy rule already has to tolerate.
        def _straddle_fraction(x: float) -> float:
            return sum(1 for ln in page_lines if ln.x0 < x < ln.x1) / len(page_lines)

        best_straddle = _straddle_fraction(split_x)
        runner_up_straddle = _straddle_fraction(gaps[1][1])
        if best_straddle < runner_up_straddle:
            # The legacy-widest candidate is ALSO less-crossed than its one
            # comparably-wide competitor - no need for a dramatic margin
            # here, since it isn't switching to anything: this simply
            # confirms the width-based leader is already the more
            # plausible gutter (found on a real UNICAMP exam: two genuine
            # questions, each with its OWN compact multi-line answer grid,
            # producing two internal grid gaps comparably wide to the true
            # gutter between the questions - the true gutter still wins on
            # crossings, just not by the dramatic margin the switch below
            # requires).
            legacy_ambiguous = False
        cleaner = [
            (g, x) for g, x in gaps[1:]
            if _straddle_fraction(x) <= best_straddle / 2
        ]
        if cleaner:
            # among the dramatically-cleaner alternatives, the widest one
            # (gaps is already sorted descending, so the first match) is
            # still the most plausible real gutter - tried FIRST, but the
            # legacy widest-gap choice stays in the running as a fallback
            # rather than being discarded outright: it was good enough to
            # win on its own before this check existed, and a "dramatically
            # cleaner" candidate that turns out not to satisfy the other
            # structural checks below (min lines, vertical span, narrow-
            # line fraction) must not cost the page a split it already had
            # (found on a real FUVEST page: a citation caption's own
            # leftover indentation looked cleaner by this measure than the
            # real gutter, but is not itself a valid column split).
            candidates = [cleaner[0], (best_gap, split_x)]
            legacy_ambiguous = False

    if legacy_ambiguous:
        return None

    return _verified_split(candidates)


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


def _row_sorted(lines: list[TextLine]) -> list[TextLine]:
    """Top-to-bottom, x as tie-break - but the tie-break is ROW-aware, not
    a raw y0 comparison: lines whose vertical extents overlap by at least
    ``_ROW_OVERLAP_RATIO`` of the shorter one's height are the same visual
    row and are ordered by x0 amongst themselves, even when their exact
    y0 values differ by a sub-pixel jitter (see ``_ROW_OVERLAP_RATIO``)."""
    by_y0 = sorted(lines, key=lambda ln: (ln.y0, ln.x0))
    rows: list[list[TextLine]] = []
    for ln in by_y0:
        height = ln.y1 - ln.y0
        placed = False
        if rows:
            row = rows[-1]
            row_top = min(other.y0 for other in row)
            row_bottom = max(other.y1 for other in row)
            overlap = min(ln.y1, row_bottom) - max(ln.y0, row_top)
            shortest = min(height, min(other.y1 - other.y0 for other in row))
            if shortest > 0 and overlap / shortest >= _ROW_OVERLAP_RATIO:
                row.append(ln)
                placed = True
        if not placed:
            rows.append([ln])
    ordered: list[TextLine] = []
    for row in rows:
        ordered.extend(sorted(row, key=lambda ln: ln.x0))
    return ordered


def _reading_order(page_lines: list[TextLine], *, use_column_detection: bool,
                   page_width: float | None = None) -> tuple[list[TextLine], bool]:
    """Default: top-to-bottom, x as tie-break - safe for both single-column
    prose and pages containing tables/formulas (see module docstring). Only
    when ``use_column_detection`` is explicitly requested AND the strict
    two-column test passes does this reorder column-major."""
    if use_column_detection:
        split_x = detect_two_column_layout(page_lines, page_width=page_width)
        if split_x is not None:
            left = _row_sorted([ln for ln in page_lines if ln.x0 < split_x])
            right = _row_sorted([ln for ln in page_lines if ln.x0 >= split_x])
            return left + right, True
    return _row_sorted(page_lines), False


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
            lines=_normalize_standalone_number_markers(all_lines), images=images,
            pages_multicolumn=multicolumn_pages, stripped_artifact_texts=sorted(artifact_texts),
        )
    finally:
        doc.close()
