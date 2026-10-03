"""Folha de resposta de redacao em branco, gerada pelo sistema pra impressao.

Spec: docs/superpowers/specs/2026-09-28-envio-lote-redacao-design.md s6.

Este modulo e o DONO UNICO da geometria da folha. ``HEADER_REGION_FRACTION``
e a fracao da altura da pagina que o cabecalho ocupa, e services/essay_batch.py
importa essa MESMA constante daqui pra recortar a regiao de cabecalho da foto
antes de rodar OCR nela. Mexer na geometria sem mexer na fracao (ou vice-versa)
faz o OCR passar a ler o lugar errado - as duas coisas sao um acoplamento real,
por isso moram juntas em vez de cada modulo ter a sua copia.

Por que NAO o motor ``Story`` de essay_pdf_export.py (que a spec s6 sugeria):
Story faz layout de FLUXO e nao da garantia nenhuma sobre em que altura da
pagina um elemento cai. Uma folha de resposta e um formulario de geometria
FIXA cuja unica razao de existir e ser previsivel o suficiente pra ser lida
por maquina depois. Usamos entao as primitivas de baixo nivel do mesmo modulo
PyMuPDF que essay_pdf_export.py::render_pdf ja usa pras paginas de imagem
(draw_rect / draw_line / insert_text / insert_image / tobytes(deflate, garbage)).
"""

from __future__ import annotations

import logging
from pathlib import Path

logger = logging.getLogger(__name__)

# Logo da propria plataforma - cai no espaco reservado da logo quando a
# escola nao tem uma cadastrada (ou a dela nao pode ser lida), pra esse
# espaco nunca sair em branco (decisao do usuario, 2026-10-03).
_FALLBACK_LOGO_PATH = Path(__file__).parent / "assets" / "nucleo_edu_logo.png"

# Fracao da ALTURA da pagina ocupada pelo cabecalho (logo + titulo + caixas de
# NOME e CPF). Ajustada pra sobrar so um respiro pequeno abaixo da caixa de
# CPF (nao a folga grande que a versao com subtitulo de tema precisava).
# services/essay_batch.py importa esta constante, entao mudar o valor aqui
# ja mantem o recorte de OCR em sincronia - so NAO deixe o conteudo real do
# cabecalho (caixas de NOME/CPF) ultrapassar essa fracao.
HEADER_REGION_FRACTION = 0.18

# Linhas numeradas pra redacao. 30 e o que cabe confortavelmente numa A4 abaixo
# do cabecalho (passo de ~20pt, validado renderizando de verdade).
LINE_COUNT = 30

# Caixinhas de um caractere, estilo ENEM - guiam a letra do aluno e melhoram
# muito a leitura do nome por OCR.
_NAME_BOXES = 40
_CPF_BOXES = 11

_MARGIN = 36.0
_BOX_HEIGHT = 16.0
_GRID_COLOR = (0.4, 0.4, 0.4)
_FRAME_COLOR = (0.6, 0.6, 0.6)
_RULE_COLOR = (0.75, 0.75, 0.75)
_NUMBER_COLOR = (0.5, 0.5, 0.5)


def answer_sheet_available() -> bool:
    """Mesma convencao de essay_pdf_export.pdf_available(): pymupdf e um extra
    opcional (``recovery`` em pyproject.toml), entao a rota devolve 503 em vez
    de 500 quando ele nao esta instalado."""
    try:
        import pymupdf  # noqa: F401

        return True
    except Exception:
        return False


def _draw_char_boxes(page, pymupdf, *, x: float, y: float, width: float, count: int) -> None:
    box_width = width / count
    for index in range(count):
        page.draw_rect(
            pymupdf.Rect(x + index * box_width, y, x + (index + 1) * box_width, y + _BOX_HEIGHT),
            color=_GRID_COLOR, width=0.5,
        )


def _draw_logo(page, pymupdf, *, logo_rect, logo_path: str | None) -> None:
    """Desenha a logo da escola se houver e for legivel; senao cai pra logo
    do Nucleo Edu 360 - o espaco reservado nunca fica vazio. Arquivo sumido
    ou corrompido tem o mesmo fallback, nunca bloqueia a geracao da folha
    (spec s7)."""
    for candidate in (logo_path, str(_FALLBACK_LOGO_PATH)):
        if not candidate:
            continue
        try:
            page.insert_image(logo_rect, filename=candidate, keep_proportion=True)
            return
        except Exception:
            if candidate == logo_path:
                logger.warning("logo da escola nao pode ser desenhada: %s", logo_path)
    logger.warning("nem a logo da escola nem o fallback do Nucleo Edu puderam ser desenhadas")


def _draw_sheet(page, pymupdf, *, logo_path: str | None) -> None:
    width, height = page.rect.width, page.rect.height
    header_bottom = height * HEADER_REGION_FRACTION
    inner_left = _MARGIN + 6

    page.draw_rect(
        pymupdf.Rect(_MARGIN, _MARGIN, width - _MARGIN, header_bottom),
        color=_FRAME_COLOR, width=0.8,
    )

    logo_rect = pymupdf.Rect(inner_left, _MARGIN + 6, inner_left + 100, _MARGIN + 40)
    _draw_logo(page, pymupdf, logo_rect=logo_rect, logo_path=logo_path)

    title_rect = pymupdf.Rect(_MARGIN, _MARGIN + 8, width - _MARGIN, _MARGIN + 34)
    page.insert_textbox(
        title_rect, "FOLHA DE REDAÇÃO", fontsize=14, fontname="hebo",
        align=pymupdf.TEXT_ALIGN_CENTER,
    )

    usable = width - 2 * _MARGIN - 12
    name_top = _MARGIN + 52
    page.insert_text((inner_left, name_top - 4), "NOME COMPLETO DO PARTICIPANTE", fontsize=7)
    _draw_char_boxes(page, pymupdf, x=inner_left, y=name_top, width=usable, count=_NAME_BOXES)

    cpf_top = name_top + 30
    page.insert_text((inner_left, cpf_top - 4), "CPF", fontsize=7)
    _draw_char_boxes(page, pymupdf, x=inner_left, y=cpf_top, width=usable * 0.35, count=_CPF_BOXES)

    rules_top = header_bottom + 18
    rules_bottom = height - _MARGIN
    step = (rules_bottom - rules_top) / LINE_COUNT
    for index in range(LINE_COUNT):
        line_y = rules_top + (index + 1) * step
        page.insert_text(
            (_MARGIN, line_y - 2), f"{index + 1:02d}", fontsize=7, color=_NUMBER_COLOR
        )
        page.draw_line(
            pymupdf.Point(_MARGIN + 18, line_y), pymupdf.Point(width - _MARGIN, line_y),
            color=_RULE_COLOR, width=0.6,
        )


def render_answer_sheet_pdf(*, logo_path: str | None = None, copies: int = 1) -> bytes:
    """``copies`` folhas identicas, uma por pagina do PDF gerado. Folha padrao
    e generica - nao menciona o tema da proposta (decisao do usuario,
    2026-10-03: uma unica folha serve pra qualquer proposta)."""
    if copies < 1:
        raise ValueError(f"copies must be at least 1, got {copies}")

    import pymupdf

    mediabox = pymupdf.paper_rect("a4")
    doc = pymupdf.open()
    try:
        for _ in range(copies):
            page = doc.new_page(width=mediabox.width, height=mediabox.height)
            _draw_sheet(page, pymupdf, logo_path=logo_path)
        # deflate+garbage pelo mesmo motivo documentado em
        # essay_pdf_export.render_pdf: sem eles o stream da logo embutida vai
        # sem compressao nenhuma.
        return doc.tobytes(deflate=True, garbage=3)
    finally:
        doc.close()


__all__ = [
    "HEADER_REGION_FRACTION",
    "LINE_COUNT",
    "answer_sheet_available",
    "render_answer_sheet_pdf",
]
