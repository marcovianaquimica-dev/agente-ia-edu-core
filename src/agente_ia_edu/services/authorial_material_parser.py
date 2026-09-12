"""PHASE 26 - Authorial Material Ingestion Engine: generic prose/exercise parser.

A SIBLING to ``ingestion_parser.py``'s ``PdfParser`` - it does NOT replace or
modify it. ``PdfParser`` is specialised for ENEM/official answer-key PDFs
(only recognises "Questão N" blocks with standalone A)-E) options, and is
exercised by the gated, approval-token-protected official ingestion pipeline
- touching it would risk that pipeline. Authorial teaching material (books,
apostilas, chapters) has a completely different shape: prose organised by
chapter/section headings, with exercises mixed in loosely, and pypdf's line
breaks do not reliably fall at paragraph/heading boundaries (a heading and
the paragraph right after it are frequently returned as one "line"). This
module therefore matches headings over the FULL joined document text (like
the existing ``PdfParser.QUESTION_PATTERN`` technique), not line-by-line.

Extracts the SAME ``ParsedDocument`` / ``ParsedSection`` / ``ParsedQuestion``
/ ``ParsedAsset`` dataclasses ``ingestion_parser.py`` already defines - so the
result can be handed to the EXISTING
``IngestionService.ingest_document(..., parsed_override=...)`` verbatim, with
no change to that service or its models.

A numbered subsection heading ("1.1 Título") does not become its own
IngestionSection (that granularity would fragment the Material Player's
"Seção X de Y" reading unit into dozens of tiny sections) - it becomes a
"## Título" marker line inside the enclosing chapter/episode section's
``content_lines``, using the SAME lightweight convention as the Markdown
path below. The PHASE 26 publication step (authorial_material_ingestion.py)
turns "## " lines into HEADING blocks and everything else into TEXT blocks.

No LLM. No provider import. Deterministic and repeatable for identical input.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

from .ingestion_parser import DocxParser, ParsedAsset, ParsedDocument, ParsedQuestion, ParsedSection

# -- heading heuristics (author's own convention, observed in real pilot
#    material: "TEMPORADA 11: ..." / "EPISÓDIO 01 – ..." / "1.1 Título") --
_SEASON_TITLE = re.compile(r"TEMPORADA\s+(\d+)\s*[:\-–]\s*([^\n\f]{2,120})", re.IGNORECASE)
_EPISODE = re.compile(r"(?:🧪|📘|📗|📙)?\s*EPISÓDIO\s+(\d+)\s*[–\-:]?\s*", re.IGNORECASE)
_CHAPTER = re.compile(r"Cap[íi]tulo\s+(\d+)\s*[–\-:]?\s*", re.IGNORECASE)
_NUMBERED_SUBSECTION = re.compile(r"(?<![\d.])(\d{1,2}\.\d{1,2})\s+(?=[A-ZÀ-Úa-zà-ú])")
_HEADING_LINE_END = re.compile(r"[.?!:]\s|$")

# -- exercise heuristics: "01." / "1)" style numbered items, lettered options
#    a)-e) anywhere in the body (looser than the ENEM-only pattern, which
#    requires 5 standalone-line options) --
_EXERCISE_ITEM = re.compile(r"(?m)(?:^|(?<=[.\f]))\s*(\d{1,3})\s*[.)]\s*(?=[A-ZÀ-Ú(])")
_OPTION_INLINE = re.compile(r"\b([a-eA-E])\)\s*")

_VISUAL_REFERENCE = re.compile(r"\b(figura|gráfico|grafico|tabela|esquema|diagrama)\b", re.IGNORECASE)

MIN_EXERCISE_BODY_CHARS = 15
MIN_HEADING_TITLE_CHARS = 3
MAX_HEADING_TITLE_CHARS = 140


def _clip_heading_title(text: str) -> str:
    """A heading title runs from right after the marker to the first sentence
    boundary or a sane max length - never the whole rest of the page blob."""
    text = text.strip(" \t–-:")
    m = _HEADING_LINE_END.search(text)
    end = m.start() if m and m.start() > MIN_HEADING_TITLE_CHARS else min(len(text), MAX_HEADING_TITLE_CHARS)
    end = min(end, MAX_HEADING_TITLE_CHARS)
    return " ".join(text[:end].split())


def _split_exercise_items(text: str) -> list[tuple[int, str]]:
    """Split a block of text into (number, body) numbered items. Deterministic,
    no fabrication: an item with no usable body is simply dropped."""
    matches = list(_EXERCISE_ITEM.finditer(text))
    items: list[tuple[int, str]] = []
    for i, m in enumerate(matches):
        start = m.end()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(text)
        body = text[start:end].strip()
        if len(body) >= MIN_EXERCISE_BODY_CHARS:
            items.append((int(m.group(1)), body))
    return items


def _extract_options(body: str) -> tuple[str, list[str]]:
    """Split a body into (statement, options) using inline a)-e) markers.
    Returns options=[] when the pattern is not a clean, confident split."""
    marks = list(_OPTION_INLINE.finditer(body))
    if len(marks) < 2:
        return body, []
    letters = [m.group(1).lower() for m in marks]
    dedup_seen: set[str] = set()
    ordered: list[str] = []
    for letter in letters:
        if letter not in dedup_seen:
            dedup_seen.add(letter)
            ordered.append(letter)
    expected = [chr(ord("a") + i) for i in range(len(ordered))]
    if ordered != expected:
        return body, []
    statement = body[: marks[0].start()].strip()
    options = []
    for i, m in enumerate(marks):
        end = marks[i + 1].start() if i + 1 < len(marks) else len(body)
        opt_text = " ".join(body[m.end():end].split())
        if opt_text:
            options.append(f"{m.group(1).lower()}) {opt_text}")
    return statement, options if len(options) == len(marks) else []


def _flush_exercises(body_text: str, section_index: int | None, questions: list[ParsedQuestion]) -> None:
    for number, body in _split_exercise_items(body_text):
        statement, options = _extract_options(body)
        statement = " ".join(statement.split())
        if not statement:
            continue
        questions.append(ParsedQuestion(
            question_number=number, statement_text=statement,
            alternatives_text="\n".join(options) if options else None,
            correct_answer=None, answer_explanation=None,
            page_start=None, page_end=None, position=len(questions),
            section_index=section_index, requires_review=len(options) not in (0, 4, 5),
        ))


def parse_authorial_pdf(filepath: Path) -> ParsedDocument:
    """Generic, deterministic PDF prose/exercise structuring for authorial
    material. Never OCRs, never reconstructs an image - visual elements are
    recorded as review-required evidence only (spec s10: 'não inventar
    conteúdo'). Headings are matched over the FULL joined document text
    (pypdf's own line breaks do not reliably fall at paragraph boundaries)."""
    from pypdf import PdfReader
    from pypdf.errors import PdfReadError

    try:
        reader = PdfReader(filepath)
    except (OSError, PdfReadError, ImportError) as exc:
        raise ValueError("Unable to read PDF text layer") from exc

    page_texts = [page.extract_text() or "" for page in reader.pages]
    if not any(page_texts):
        raise ValueError("PDF has no extractable text layer; OCR review is required")

    document_hash = DocxParser.file_hash(filepath)
    document_text = "\f".join(page_texts)
    page_offsets = []
    offset = 0
    for text in page_texts:
        page_offsets.append(offset)
        offset += len(text) + 1

    def _page_of(pos: int) -> int:
        for index in range(len(page_offsets) - 1, -1, -1):
            if page_offsets[index] <= pos:
                return index + 1
        return 1

    title_match = _SEASON_TITLE.search(document_text[:2000])
    title = " ".join(title_match.group(2).split()) if title_match else filepath.stem

    # -- chapter/episode boundaries over the WHOLE text --
    boundary_re = re.compile(f"(?:{_EPISODE.pattern})|(?:{_CHAPTER.pattern})", re.IGNORECASE)
    boundaries = list(boundary_re.finditer(document_text))

    sections: list[ParsedSection] = []
    questions: list[ParsedQuestion] = []

    def _make_section(kind: str, number: str, title_text: str, start: int, end: int, position: int) -> ParsedSection:
        body = document_text[start:end]
        sub_matches = list(_NUMBERED_SUBSECTION.finditer(body))
        content_lines: list[str] = []
        cursor = 0
        for sm in sub_matches:
            lead = body[cursor:sm.start()].strip()
            if lead:
                content_lines.append(" ".join(lead.split()))
            sub_title = _clip_heading_title(body[sm.end():sm.end() + 160])
            content_lines.append(f"## {sm.group(1)} {sub_title}".strip())
            cursor = sm.end() + len(sub_title)
        tail = body[cursor:].strip()
        if tail:
            content_lines.append(" ".join(tail.split()))
        # exercises: scan the WHOLE section body (numbered items may appear
        # anywhere inside a chapter's worked examples)
        _flush_exercises(re.sub(r"\s+", " ", body), position, questions)
        return ParsedSection(
            section_type=kind, title=title_text or f"{kind.title()} {number}",
            description=None, section_number=number,
            page_start=_page_of(start), page_end=_page_of(max(start, end - 1)),
            content_lines=content_lines, position=position,
        )

    _exercise_start = re.compile(r"^\s*\d{1,3}\s*[.)]\s")
    if boundaries:
        for i, b in enumerate(boundaries):
            is_episode = b.group(1) is not None
            number = b.group(1) if is_episode else b.group(2)
            after = document_text[b.end():b.end() + 200]
            if _exercise_start.match(after):
                # the "heading" is immediately followed by an exercise item
                # (an exercise appendix organised per episode/chapter, not a
                # real subtitle) - keep a generic title, consume nothing.
                heading_title, body_start = "", b.end()
            else:
                heading_title = _clip_heading_title(after)
                body_start = b.end() + len(heading_title)
            body_end = boundaries[i + 1].start() if i + 1 < len(boundaries) else len(document_text)
            sections.append(_make_section(
                "EPISODE" if is_episode else "CHAPTER", number, heading_title,
                body_start, body_end, len(sections) + 1,
            ))
        # content BEFORE the first boundary (objective/intro) - never dropped
        lead = document_text[:boundaries[0].start()]
        lead = re.sub(_SEASON_TITLE, "", lead).strip()
        if len(lead) > 20:
            sections.insert(0, ParsedSection(
                section_type="SECTION", title="Introdução", description=None,
                section_number=None, page_start=1, page_end=_page_of(boundaries[0].start()),
                content_lines=[" ".join(lead.split())], position=0,
            ))
            for s in sections[1:]:
                s.position += 1
            for q in questions:
                if q.section_index is not None:
                    q.section_index += 1
    else:
        # no chapter/episode headings found at all - likely a pure exercise
        # sheet (this pilot's 2nd file). Represent the whole document as one
        # section so no content/question is silently dropped.
        body = re.sub(_SEASON_TITLE, "", document_text)
        _flush_exercises(re.sub(r"\s+", " ", body), 0, questions)
        content_lines = [ln.strip() for ln in body.split("\f") if ln.strip()]
        sections.append(ParsedSection(
            section_type="SECTION", title=title, description=None, section_number=None,
            page_start=1, page_end=len(page_texts), content_lines=content_lines, position=0,
        ))

    # -- visual-asset evidence (same technique as the ENEM PdfParser: real
    #    embedded images counted; a bare textual reference to a figure/table
    #    with no embeddable image is flagged for review, never invented) --
    assets: list[ParsedAsset] = []
    for page_no, page in enumerate(reader.pages, start=1):
        try:
            images = list(page.images)
        except Exception:
            images = []
        for pos, image in enumerate(images):
            try:
                image_bytes = image.data
                assets.append(ParsedAsset(
                    "IMAGE", page_no, pos, None,
                    hashlib.sha256(image_bytes).hexdigest(), image.image_format,
                    len(image_bytes), None, False,
                ))
            except Exception:
                continue
        if not images and _VISUAL_REFERENCE.search(page_texts[page_no - 1]):
            assets.append(ParsedAsset("PAGE_REGION", page_no, 0, None, document_hash,
                                      "application/pdf", filepath.stat().st_size, None, False))

    return ParsedDocument(
        filename=filepath.name, document_hash=document_hash, title=title, author=None,
        page_count=len(page_texts), sections=sections, questions=questions,
        total_images=sum(1 for a in assets if a.asset_type == "IMAGE"),
        total_tables=0, assets=assets,
    )


def parse_authorial_text(filepath: Path) -> ParsedDocument:
    """TXT/MD: paragraphs separated by blank lines; '#'/'##' (MD) or a short
    ALL-CAPS line (TXT) is treated as a heading. No external dependency."""
    text = filepath.read_text(encoding="utf-8", errors="replace")
    document_hash = DocxParser.file_hash(filepath)
    is_md = filepath.suffix.lower() == ".md"

    sections: list[ParsedSection] = []
    current: ParsedSection | None = None
    position = 0
    title = filepath.stem
    first_line_used_as_title = False

    for raw_line in text.splitlines():
        stripped = raw_line.strip()
        if not stripped:
            continue
        is_heading = False
        heading_text = stripped
        if is_md and stripped.startswith("#"):
            heading_text = stripped.lstrip("#").strip()
            is_heading = bool(heading_text)
        elif not is_md and len(stripped) <= 80 and stripped.upper() == stripped and any(c.isalpha() for c in stripped):
            is_heading = True

        if not first_line_used_as_title and not is_heading:
            title = stripped[:200]
            first_line_used_as_title = True
            continue

        if is_heading:
            position += 1
            current = ParsedSection(
                section_type="SECTION", title=heading_text, description=None,
                section_number=None, page_start=None, page_end=None,
                content_lines=[], position=position,
            )
            sections.append(current)
        elif current is not None:
            current.content_lines.append(stripped)
        else:
            position += 1
            current = ParsedSection(
                section_type="SECTION", title=title, description=None,
                section_number=None, page_start=None, page_end=None,
                content_lines=[stripped], position=position,
            )
            sections.append(current)

    return ParsedDocument(
        filename=filepath.name, document_hash=document_hash, title=title, author=None,
        page_count=None, sections=sections, questions=[], total_images=0, total_tables=0, assets=[],
    )


def parse_authorial_document(filepath: Path) -> ParsedDocument:
    """Dispatch by extension. DOCX reuses the EXISTING general-purpose
    ``DocxParser`` verbatim (already suitable for prose); PDF/TXT/MD use the
    new parsers above. PPTX is explicitly NOT supported this phase (no
    existing dependency for it - documented as a limitation, not attempted)."""
    suffix = filepath.suffix.lower()
    if suffix == ".docx":
        return DocxParser.parse_file(filepath)
    if suffix == ".pdf":
        return parse_authorial_pdf(filepath)
    if suffix in (".txt", ".md"):
        return parse_authorial_text(filepath)
    raise ValueError(f"Unsupported authorial document format: {suffix}")
