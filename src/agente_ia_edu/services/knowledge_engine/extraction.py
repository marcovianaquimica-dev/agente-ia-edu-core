"""Extracao do texto de um documento, e o diagnostico do que se perdeu.

Duas responsabilidades, separadas de proposito:

  * LER - abrir o arquivo e tirar dele o texto de cada pagina, registrando
    QUAL leitor produziu o resultado;
  * MEDIR - quantos caracteres cada pagina rendeu e se ela tem imagem
    embutida.

Quem DECIDE o que essas medidas significam e
``knowledge_chunking_policy.v1.classify_extraction_status`` - funcao pura,
sem I/O. A separacao importa: os criterios de PARTIAL sao politica
versionada, e politica nao deve morar junto do codigo que abre arquivo.

OCR esta fora da Fase 3. Um PDF sem texto em nenhum leitor termina em
``FAILED`` com ``extraction_error`` comecando por ``OCR_REQUIRED``, que e um
estado CONSULTAVEL - da para listar exatamente quais documentos do acervo
esperam OCR, em vez de descobrir um a um.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from ...knowledge_chunking_policy.v1 import PageTextStats, classify_extraction_status
from ..authorial_material_parser import _import_pymupdf, read_pdf_page_texts
from ..ingestion_parser import DocxParser

#: Prefixo de ``extraction_error`` que marca o caso "so OCR resolve".
OCR_REQUIRED = "OCR_REQUIRED"


class DocumentExtractionError(ValueError):
    """Nao foi possivel extrair texto. ``code`` e estavel e consultavel."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class ExtractionResult:
    page_texts: list[str]
    method: str
    page_count: int
    page_stats: tuple[PageTextStats, ...]
    status: str
    reasons: tuple[str, ...]

    @property
    def pages_without_text(self) -> list[int]:
        """Registrado SEMPRE, inclusive quando o status e EXTRACTED - pagina
        vazia nao e, por si, perda, mas saber onde elas estao e util."""
        return [page.page for page in self.page_stats if not page.has_useful_text]


def extract_document(path: Path) -> ExtractionResult:
    """Extrai o texto de ``path``, despachando pela extensao."""
    suffix = path.suffix.lower()
    if suffix == ".pdf":
        return _extract_pdf(path)
    if suffix == ".docx":
        # DocxParser nao expoe um "texto cru"; expoe estrutura. Reconstituir o
        # texto a partir dela e suficiente aqui, porque o chunker vai pedir a
        # estrutura ao parser de qualquer modo.
        parsed = DocxParser.parse_file(path)
        body = "\n\n".join(
            line
            for section in parsed.sections
            for line in ([section.title or ""] + list(section.content_lines))
            if line and line.strip()
        )
        return _single_page(body, method="DOCX")
    if suffix in (".md", ".txt"):
        return _single_page(path.read_text(encoding="utf-8", errors="replace"), method="MARKDOWN")
    raise DocumentExtractionError(
        "UNSUPPORTED_FILE_TYPE", f"extensao nao suportada para extracao: {suffix or '(nenhuma)'}"
    )


def _extract_pdf(path: Path) -> ExtractionResult:
    try:
        layer = read_pdf_page_texts(path)
    except ValueError as exc:
        message = str(exc)
        if "OCR" in message:
            raise DocumentExtractionError(
                OCR_REQUIRED,
                f"{OCR_REQUIRED}: nenhum leitor encontrou camada de texto; "
                "este documento precisa de OCR por visao, que esta fora da Fase 3",
            ) from exc
        raise DocumentExtractionError("UNREADABLE_PDF", message) from exc

    images = _pages_with_images(path, len(layer.page_texts))
    stats = tuple(
        PageTextStats(
            page=index,
            char_count=len((text or "").strip()),
            has_image=index in images,
        )
        for index, text in enumerate(layer.page_texts, start=1)
    )
    status, reasons = classify_extraction_status(stats)
    return ExtractionResult(
        page_texts=list(layer.page_texts),
        method=layer.method,
        page_count=len(layer.page_texts),
        page_stats=stats,
        status=status,
        reasons=tuple(reasons),
    )


def _pages_with_images(path: Path, page_count: int) -> set[int]:
    """Quais paginas tem imagem embutida.

    E o sinal que distingue "pagina de fato branca" de "pagina escaneada cujo
    texto nao saiu" - a assimetria dos criterios de PARTIAL (spec 20.3)
    depende disto. Qualquer falha na inspecao devolve conjunto vazio: a
    ausencia do sinal nunca deve virar uma acusacao de perda.
    """
    pages: set[int] = set()
    try:
        from pypdf import PdfReader

        reader = PdfReader(path)
        for index, page in enumerate(reader.pages, start=1):
            try:
                if list(page.images):
                    pages.add(index)
            except Exception:
                continue
        return pages
    except Exception:
        pass

    pymupdf = _import_pymupdf()
    if pymupdf is None:
        return pages
    try:
        with pymupdf.open(str(path)) as document:
            for index, page in enumerate(document, start=1):
                if page.get_images():
                    pages.add(index)
    except Exception:
        return pages
    return pages


def _single_page(text: str, *, method: str) -> ExtractionResult:
    """``.docx``, ``.md`` e ``.txt`` nao tem paginacao recuperavel.

    Tratados como uma pagina so, de forma explicita: inventar numero de
    pagina para um arquivo que nao tem paginas produziria citacao falsa, que
    e pior que citacao grossa.
    """
    body = (text or "").strip()
    if not body:
        raise DocumentExtractionError(
            "NO_TEXT_EXTRACTED", "o arquivo nao rendeu nenhum texto"
        )
    stats = (PageTextStats(page=1, char_count=len(body), has_image=False),)
    status, reasons = classify_extraction_status(stats)
    return ExtractionResult(
        page_texts=[body],
        method=method,
        page_count=1,
        page_stats=stats,
        status=status,
        reasons=tuple(reasons),
    )
