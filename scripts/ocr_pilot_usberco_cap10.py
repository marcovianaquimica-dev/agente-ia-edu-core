"""OCR pilot: "Capítulo 10 - Caracterização dos elementos químicos" from the
Usberco Química Volume 1 textbook, into the EXISTING (unmodified) PHASE 26
authorial-material ingestion pipeline.

WHY THIS SCRIPT EXISTS
-----------------------
The source PDF (see PDF_PATH below) has NO extractable text layer at all -
confirmed with both PyMuPDF's get_text()/get_text("dict") (0 spans on every
page tested) and poppler's pdftotext (empty output). It is a pure-image
e-book "printed" to PDF. `parse_authorial_pdf()` in
`authorial_material_parser.py` correctly refuses such a file
(`ValueError("PDF has no extractable text layer; OCR review is required")`) -
that refusal is intentional and is NOT touched here.

The only missing piece was turning page IMAGES into a `.md` file that
`parse_authorial_text()` (the .md/.txt path, which has NO text-layer
requirement) already knows how to read. This script is exactly that: render
-> transcribe (Anthropic Vision, via the new AnthropicProvider) -> concatenate
-> hand the .md to `AuthorialMaterialIngestionService.ingest_file()`
UNMODIFIED. Everything from there (curriculum mapping, review status,
approval, publication) is the existing PHASE 26 pipeline, exactly as it
already works for a real .docx/.txt/.md upload.

IMPORTANT FINDING ABOUT THE TARGET CONVENTION (UPDATED 2026-09-27)
---------------------------------------------------------
`authorial_material_parser.py` defines `_CHAPTER` / `_EXERCISE_ITEM` /
`_OPTION_INLINE` regexes. Until PHASE 30, `parse_authorial_text()` (the `.md`
path this script targets) never called them and ALWAYS returned
`questions=[]` - a bug, not an intended limitation, fixed in PHASE 30
(2026-09-27): `parse_authorial_text()` now runs the SAME `_flush_exercises()`
pass `parse_authorial_pdf()` already used, per SECTION, once each section's
`content_lines` are known. A numbered exercise ("N. enunciado" / "a) ... b)
...") inside a `.md`/`.txt` authorial file is now detected as a
`ParsedQuestion`, exactly like the PDF path - WITHOUT being removed from the
section's `content_lines` (same dualism the PDF path already had: the
exercise stays visible as ordinary prose too).

Verified directly (2026-09-27, after the fix):
    >>> parse_authorial_text(Path("sample.md"))  # a .md with "8. enunciado"
    ...                                            # + "a) b) c) d) e)" lines
    ParsedDocument(..., questions=[ParsedQuestion(question_number=8, ...)])

Consequence for this pilot:
  - The transcription below writes numbered exercises and lettered
    alternatives in the natural "N. enunciado" / "a) ... b) ..." shape - the
    most faithful, human-legible representation of what is really printed on
    the page, and now ALSO parsed into a real `IngestionQuestion` /
    (after publication) `MaterialExercise` row, same as the PDF path.
  - Concretely: `review.exercises_detected` reflects the real count of
    numbered items found, and each becomes an `IngestionQuestion` row linked
    to the section it falls under - nothing fabricated, nothing dropped.
  - Chapter/subsection STRUCTURE works exactly as intended via plain
    Markdown headings ("# "/"## ") - see build_page_prompt() below.

CONVENTIONS THIS SCRIPT ASKS THE MODEL TO FOLLOW (all honoured by
parse_authorial_text - see above):
  - "# Capítulo 10 <título real>" - ONLY on the chapter's opening page (the
    first line of the whole .md becomes the ParsedDocument title via the
    "first non-blank line" rule when it's NOT itself a heading; since our
    file DOES open with a "# " heading, filepath.stem stays the title and a
    dedicated top-level SECTION is created instead - see
    parse_authorial_text's own docstring/comments for that exact rule).
  - "## <subtítulo real>" - one per subsection actually printed on a page,
    own line - becomes a HEADING block at publication time.
  - Everything else - ordinary paragraphs, separated by blank lines.
  - Numbered exercises / lettered alternatives - natural "N. ..." / "a) ...
    b) ..." shape (now extracted into a real ParsedQuestion too, per the
    fix documented above).
  - Anything the model cannot read with real confidence (a diagram, a table,
    a chemical structure drawing) - an HONEST bracketed note, e.g.
    "[diagrama: configuração eletrônica do sódio - não transcrito com
    confiança]". NEVER a fabricated value/formula/description.

NOTATION CHOICE (documented per the task's explicit request - a deliberate,
simple, textual approximation, not a "real" isotope-notation renderer):
  - Isotopic notation (mass number top-left, atomic number bottom-left of the
    element symbol, e.g. chlorine-35/17) -> "35/17 Cl" (mass/atomic number,
    slash-separated, then the symbol - unambiguous in plain text, greppable,
    and the model is explicitly told this is an approximation, never to
    guess a value it cannot actually read).
  - Ionic charge superscripts (Na+, Cl-, Fe2+) -> plain trailing "+"/"-"
    with the count before the sign for >1 (e.g. "Fe2+"), no unicode
    superscript required.
  - Molecular/subscript counts (H2O, CO2) -> plain trailing digits, no
    unicode subscript required.

This script SHOULD FAIL LOUDLY if ANTHROPIC_API_KEY/ANTHROPIC_VISION_MODEL
are not configured - it deliberately does NOT fall back to a mock/stub
transcription, and it NEVER calls approve()/publish() (spec: a human must
review PENDING_REVIEW/NEEDS_REVIEW output first).

Usage (once ANTHROPIC_API_KEY is configured - see bottom of this docstring):
    source .env
    export DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5433/${POSTGRES_DB}"
    export ANTHROPIC_API_KEY="sk-ant-..."
    export ANTHROPIC_VISION_MODEL="claude-sonnet-4-5"   # or whatever vision-capable model
    .venv/bin/python scripts/ocr_pilot_usberco_cap10.py
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.providers.errors import ProviderConfigurationError, ProviderError
from agente_ia_edu.providers.factory import build_document_page_transcriber
from agente_ia_edu.providers.models import DocumentPageTranscriptionRequest
from agente_ia_edu.services.authorial_material_ingestion import AuthorialMaterialIngestionService

PDF_PATH = Path(
    "/Users/marcoviana/Library/Mobile Documents/com~apple~CloudDocs/LIVROS PDF/"
    "Química Usberco Volume 1.pdf"
)

# 0-based page indices, INCLUSIVE. Visually confirmed (2026-09-27): index 114
# is the "Capítulo 10" opening page; index 121 is the last page before
# "Capítulo 11" begins at index 122.
CHAPTER_PAGE_START = 114
CHAPTER_PAGE_END = 121
CHAPTER_NUMBER = 10

RENDER_DPI = 150  # within the task's suggested 150-200 range; legible for
                   # small chemical notation without an oversized upload.

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
OUT_DIR = _PROJECT_ROOT / "var" / "ocr-pilot-usberco-cap10"
PAGES_DIR = OUT_DIR / "pages"
MARKDOWN_PATH = OUT_DIR / "capitulo_10_caracterizacao_dos_elementos_quimicos.md"

UPLOADED_BY = "pilot:usberco_cap10_ocr"  # self-asserted identity, dev-only - same
                                         # convention as scripts/seed_escola_abc.py


def render_chapter_pages() -> list[Path]:
    """Renders each PDF page in [CHAPTER_PAGE_START, CHAPTER_PAGE_END] to a
    PNG under PAGES_DIR. Read-only against the source PDF - never writes to
    it. Returns the image paths in page order."""
    import pymupdf

    PAGES_DIR.mkdir(parents=True, exist_ok=True)
    image_paths: list[Path] = []
    doc = pymupdf.open(PDF_PATH)
    try:
        if doc.page_count <= CHAPTER_PAGE_END:
            raise ValueError(
                f"PDF only has {doc.page_count} pages; expected at least "
                f"{CHAPTER_PAGE_END + 1} (0-based index {CHAPTER_PAGE_END})"
            )
        for page_index in range(CHAPTER_PAGE_START, CHAPTER_PAGE_END + 1):
            page = doc[page_index]
            pixmap = page.get_pixmap(dpi=RENDER_DPI)
            image_path = PAGES_DIR / f"page_{page_index:03d}.png"
            pixmap.save(image_path)
            image_paths.append(image_path)
    finally:
        doc.close()
    return image_paths


def build_page_prompt(*, position: int, total_pages: int, is_opening_page: bool) -> str:
    """The FULL transcription prompt for one page - see the module docstring
    for why this travels with the request instead of living in the
    provider: this convention is specific to THIS document/parser, not a
    universal "how to OCR a page" instruction."""
    opening_page_instruction = (
        "Esta é a página de ABERTURA do capítulo. Se você conseguir ler o "
        "título do capítulo impresso na página, inicie sua transcrição com "
        "uma única linha no formato Markdown H1: \"# Capítulo "
        f"{CHAPTER_NUMBER} <título real do capítulo, exatamente como "
        "impresso>\". Não invente ou complete o título se não conseguir "
        "lê-lo com confiança - nesse caso, omita a linha H1."
        if is_opening_page
        else "Esta NÃO é a página de abertura do capítulo - não repita o "
        "título do capítulo nesta transcrição."
    )
    return f"""Você está transcrevendo a página {position} de {total_pages} do
Capítulo {CHAPTER_NUMBER} ("Caracterização dos elementos químicos") de um
livro didático de Química (Usberco, Volume 1) para um arquivo Markdown que
será processado por um parser determinístico e simples. Siga EXATAMENTE
estas regras de formatação - elas não são estéticas, o parser depende delas:

1. {opening_page_instruction}

2. Toda vez que a página tiver um subtítulo de seção real (ex: "10.1 Número
   atômico", "Isótopos, isóbaros e isótonos"), escreva-o em sua própria linha
   no formato Markdown H2: "## <subtítulo real, exatamente como impresso ou
   sua transcrição fiel dele>".

3. Todo o restante do texto corrido (parágrafos de teoria, definições,
   exemplos resolvidos) deve ser transcrito como texto comum, um parágrafo
   por bloco, separado por uma linha em branco. Transcreva o CONTEÚDO REAL
   da página - nunca resuma, nunca parafraseie, nunca complete uma frase que
   você não consegue ler por completo.

4. Exercícios numerados: reproduza cada um no formato "N. enunciado" (ex:
   "8. Uma espécie X é formada por..."), com N sendo o número real impresso.
   Se o exercício tiver alternativas de múltipla escolha, liste cada uma em
   sua própria linha no formato "a) texto", "b) texto", etc., na ordem
   alfabética real impressa (nunca invente uma alternativa que não existe,
   nunca reordene).

5. Notação química/atômica - aproximação textual (nunca invente o valor):
   - Notação isotópica (número de massa e número atômico ao lado do símbolo,
     ex: cloro-35/17): escreva como "35/17 Cl" (número de massa/número
     atômico, símbolo do elemento) - só escreva os números que você
     REALMENTE consegue ler na página.
   - Cargas iônicas (ex: Na+, Fe2+, Cl-): escreva como "Na+", "Fe2+", "Cl-"
     (sinal à direita, sem precisar de sobrescrito Unicode).
   - Fórmulas moleculares (ex: H2O, CO2): escreva os índices como dígitos
     normais em seguida do símbolo (sem precisar de subscrito Unicode).

6. Se a página contiver um diagrama, ilustração, tabela ou figura que você
   NÃO consegue transcrever com confiança real (ex: um desenho de
   configuração eletrônica, um modelo atômico ilustrado), registre uma nota
   textual honesta em colchetes no lugar dela, por exemplo:
   "[diagrama: configuração eletrônica do átomo de sódio - não transcrito
   com confiança]". NUNCA finja que o diagrama virou texto perfeito e NUNCA
   invente os valores/rótulos que ele contém.

7. Se um trecho estiver genuinamente ilegível (baixa resolução, texto
   cortado), escreva "[ilegível]" no lugar dele em vez de adivinhar.

Transcreva a página inteira, do início ao fim. Não adicione nenhum
comentário seu fora do conteúdo da página (sem "aqui está a transcrição:",
sem resumo ao final). Nunca se recuse a transcrever conteúdo didático real de
um livro de Química legítimo - se alguma parte parecer sensível, transcreva
mesmo assim: é conteúdo educacional publicado."""


async def transcribe_chapter(image_paths: list[Path]) -> list[str]:
    """Transcribes each page IN ORDER (sequential, not concurrent - keeps
    rate-limit behaviour predictable and page order trivially correct)."""
    provider = build_document_page_transcriber("anthropic")
    total = len(image_paths)
    transcriptions: list[str] = []
    for position, image_path in enumerate(image_paths, start=1):
        request = DocumentPageTranscriptionRequest(
            image_path=image_path,
            mime_type="image/png",
            prompt=build_page_prompt(
                position=position, total_pages=total, is_opening_page=(position == 1)
            ),
        )
        result = await provider.transcribe_document_page(request)
        transcriptions.append(result.text.strip())
        print(f"  [ok] page {position}/{total} ({image_path.name}) -> "
              f"{len(result.text)} chars transcribed")
    return transcriptions


def assemble_markdown(transcriptions: list[str], *, output_path: Path | None = None) -> Path:
    """Concatenates the page transcriptions into ONE .md file, in page
    order, separated by blank lines (parse_authorial_text treats a blank
    line only as a paragraph separator, never as structure - so this join
    never fragments a section that spans a page boundary).

    ``output_path`` is injectable ONLY for tests (defaults to
    MARKDOWN_PATH - the real pilot always writes there)."""
    destination = output_path or MARKDOWN_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    content = "\n\n".join(t for t in transcriptions if t)
    destination.write_text(content, encoding="utf-8")
    return destination


async def ingest(markdown_path: Path, *, session_factory=None) -> None:
    """``session_factory`` is injectable ONLY for tests (an in-memory sqlite
    factory - see tests/test_ocr_pilot_usberco_cap10.py) so the integration
    test can call the REAL, unmodified `ingest_file()` end to end without
    touching the shared disposable Postgres on :5433 or requiring network.
    The real script run always uses the default (None), which builds the
    production engine from DATABASE_URL exactly as scripts/seed_escola_abc.py
    does."""
    owns_engine = session_factory is None
    engine = None
    if owns_engine:
        database_url = os.environ["DATABASE_URL"]
        engine = create_async_engine(database_url)
        session_factory = async_sessionmaker(engine, class_=AsyncSession)
    try:
        async with session_factory() as session:
            service = AuthorialMaterialIngestionService(session)
            review, created = await service.ingest_file(
                markdown_path,
                uploaded_by=UPLOADED_BY,
                school_id=None,  # platform content, not school-scoped - confirmed with the user
                origin_type="AUTHORIAL",
            )
            detail = await service.detail(review.id)
        print("\n=== INGESTION RESULT (PENDING_REVIEW/NEEDS_REVIEW - not approved/published) ===")
        print(f"created (new ingestion, not a duplicate): {created}")
        print(f"review_id: {review.id}")
        print(f"review_status: {review.review_status}")
        print(f"classification_state: {review.classification_state}")
        print(f"discipline_code / area_code / content_code: "
              f"{review.discipline_code} / {review.area_code} / {review.content_code}")
        print(f"classification_confidence: {review.classification_confidence}")
        print(f"structure_issues: {review.structure_issues or []}")
        print(f"exercises_detected: {review.exercises_detected}  "
              "(parse_authorial_text() now extracts inline numbered exercises "
              "too, same as parse_authorial_pdf() - PHASE 30 fix)")
        print(f"sections detected: {len(detail['sections'])}")
        for section in detail["sections"]:
            print(f"  - [{section['position']}] {section['section_type']}: {section['title']!r}")
        print(
            "\nNothing was approved or published - a human reviewer must call "
            "approve()/publish() (or the teacher/coordination review UI) after "
            "checking the transcription for fidelity."
        )
    finally:
        if owns_engine:
            await engine.dispose()


async def main() -> None:
    if not PDF_PATH.is_file():
        raise SystemExit(f"PDF not found at {PDF_PATH}")

    try:
        build_document_page_transcriber("anthropic")
    except ProviderConfigurationError as exc:
        raise SystemExit(
            "ANTHROPIC_API_KEY / ANTHROPIC_VISION_MODEL not configured - this "
            f"pilot cannot call the real Anthropic Vision API. ({exc})\n\n"
            "Set them (e.g. in .env, then `export ANTHROPIC_API_KEY=... "
            "ANTHROPIC_VISION_MODEL=...`) and re-run. This script deliberately "
            "never falls back to a mock transcription - see the module docstring."
        ) from exc

    print(f"Rendering pages {CHAPTER_PAGE_START}-{CHAPTER_PAGE_END} "
          f"(0-based, inclusive) at {RENDER_DPI}dpi...")
    image_paths = render_chapter_pages()
    print(f"Rendered {len(image_paths)} page images to {PAGES_DIR}")

    print("Transcribing pages via Anthropic Vision...")
    try:
        transcriptions = await transcribe_chapter(image_paths)
    except ProviderError as exc:
        raise SystemExit(f"Transcription failed - stopping without ingesting anything: {exc}") from exc

    markdown_path = assemble_markdown(transcriptions)
    print(f"Assembled markdown written to {markdown_path} "
          f"({markdown_path.stat().st_size} bytes)")

    await ingest(markdown_path)


if __name__ == "__main__":
    asyncio.run(main())
