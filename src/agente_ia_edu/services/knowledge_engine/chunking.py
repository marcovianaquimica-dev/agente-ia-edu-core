"""Transforma um documento em unidades RECUPERAVEIS.

A DECISAO CENTRAL DA FASE (spec 20.1): **o parser da o esqueleto, o texto cru
das paginas da a substancia.**

``parse_authorial_pdf`` colapsa whitespace nos ``content_lines``
(``" ".join(lead.split())``), entao no caminho PDF as fronteiras de paragrafo
ja foram destruidas antes de o chunker ver o texto - um capitulo vira meia
duzia de linhas enormes. Chunking por paragrafo sobre ``content_lines`` e
impossivel. Mas o parser sabe o que o texto cru nao sabe: onde cada secao
comeca, qual o titulo, em que paginas ela esta, e onde estao os exercicios.

Entao usa-se cada um para o que ele sabe:

  parser      -> fronteiras, titulos, faixa de paginas, exercicios, assets
  texto cru   -> paragrafos, com as quebras preservadas

O alinhamento dos dois e feito LOCALIZANDO o titulo da secao no texto cru.
Achou, corte exato. Nao achou, granularidade de pagina e
``metadata.boundary_approximate`` - a aproximacao fica visivel, nunca
silenciosa.

ESTRUTURA ANTES DE TAMANHO. O tamanho so decide DENTRO de uma unidade
estrutural, nunca atravessa uma. Exercicio, tabela, formula destacada e
exemplo resolvido nao sao cortados nem que passem do maximo - cortar um
exemplo resolvido ao meio produz dois chunks que nao sustentam afirmacao
nenhuma.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Sequence

from ...knowledge_chunking_policy.v1 import (
    INDIVISIBLE_CHUNK_TYPES,
    MAX_CHUNK_TOKENS,
    MIN_CHUNK_TOKENS,
    OVERLAP_TOKENS,
    POLICY_VERSION,
    TARGET_CHUNK_TOKENS,
    CHARS_PER_TOKEN,
    estimate_tokens,
    retrieval_text_hash,
    source_text_hash,
)
from ..ingestion_parser import ParsedDocument, ParsedQuestion, ParsedSection
from .structure import (
    classify_block,
    describe_block,
    judge_exercise_candidate,
    split_at_structural_markers,
)

_PARAGRAPH_SPLIT = re.compile(r"\n\s*\n+|\f")
_WHITESPACE = re.compile(r"[ \t]+")
#: Fronteira de sentenca - o corte de ultimo recurso, usado SO quando um
#: bloco divisivel passa do maximo e nao tem fronteira de paragrafo onde
#: cortar. Em PDF real isso e comum: o pypdf devolve paginas inteiras sem
#: nenhuma linha em branco, e um "paragrafo" pode ser uma pagina toda.
#: Sentenca ainda e estrutura do texto; caractere nao seria.
_SENTENCE_SPLIT = re.compile(r"(?<=[.!?])\s+(?=[A-ZÀ-Ú(])")

_TARGET_CHARS = TARGET_CHUNK_TOKENS * CHARS_PER_TOKEN
_MAX_CHARS = MAX_CHUNK_TOKENS * CHARS_PER_TOKEN
_MIN_CHARS = MIN_CHUNK_TOKENS * CHARS_PER_TOKEN
_OVERLAP_CHARS = OVERLAP_TOKENS * CHARS_PER_TOKEN

_DIVISIBLE = ("PROSE", "DEFINITION", "SUMMARY")

#: Um titulo de capitulo tem algumas palavras, nao um paragrafo. O
#: ``_clip_heading_title`` do parser pega ate 140 chars do que vier DEPOIS do
#: "Capitulo N", e num livro real isso e quase sempre corpo de texto. Herdar
#: esse texto como hierarquia e pior que nao ter hierarquia: ele entra no
#: ``retrieval_text`` e contamina hash e embedding de todos os chunks da
#: secao. Verificado em amostra real: 90% dos titulos vinham assim.
_MAX_HEADING_CHARS = 80
#: Titulo que comeca em minuscula ou pontuacao e continuacao de frase, nao
#: titulo.
_HEADING_START = re.compile(r"^[A-ZÀ-Ú0-9]")
#: Abaixo disto nao e exercicio: e numero de figura, item de lista ou ruido
#: da heuristica numerada do parser. Verificado em amostra real: havia
#: "exercicios" de 1, 26 e 44 caracteres.
_MIN_EXERCISE_CHARS = 60
#: Fragmento abaixo disto nao e conteudo: e numero de pagina, glifo solto ou
#: artefato de extracao. Verificado em amostra real: 99 "chunks" de ate 19
#: caracteres, 75 deles classificados FORMULA por densidade de simbolo.
#: Eles sao FUNDIDOS ao bloco vizinho, nunca descartados - um chunk de um
#: caractere e ruido no indice, mas jogar texto fora e pior que ruido.
_MIN_CONTENT_CHARS = 20


@dataclass(frozen=True)
class ChunkDraft:
    """Um chunk pronto para persistir, ainda sem id.

    ``raw_text`` e LITERAL da fonte - nada que o sistema acrescentou. O
    contexto estrutural vive em ``heading_path``, e so se junta ao texto no
    ``retrieval_text`` derivado (spec 20.2). Nao ha campo
    ``retrieval_text`` aqui de proposito: deriva-se, nao se persiste.
    """

    ordinal: int
    chunk_type: str
    heading_path: tuple[str, ...]
    page_start: int
    page_end: int
    raw_text: str
    text_hash: str
    char_count: int
    token_estimate: int
    metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class _Block:
    """Unidade estrutural ja delimitada, antes do janelamento."""

    text: str
    chunk_type: str
    page_start: int
    page_end: int
    signals: tuple[str, ...]
    extra: dict[str, Any] = field(default_factory=dict)

    @property
    def divisible(self) -> bool:
        return self.chunk_type not in INDIVISIBLE_CHUNK_TYPES


class ProseChunker:
    """Chunker de prosa - livro didatico, apostila, artigo.

    NAO serve para ``CURRICULUM_FRAMEWORK``: a BNCC e lista normativa
    codificada, nao prosa, e janelar habilidades por tamanho produziria
    chunks plausiveis e errados. Quem recusa esse caso e ``documents.py``.
    """

    def chunk(
        self,
        *,
        page_texts: Sequence[str],
        parsed: ParsedDocument,
        page_offset: int = 0,
    ) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        ordinal = 1

        for section in sorted(parsed.sections, key=lambda s: s.position):
            heading_base = _section_heading(section)
            raw, first_page, approximate = self._section_raw_text(page_texts, section)
            if not raw.strip():
                continue

            exercises = [
                question
                for question in parsed.questions
                if question.section_index == section.position
            ]
            prose_source, removed = _strip_exercise_spans(raw, exercises)

            blocks = self._blocks(prose_source, first_page, heading_base, section)
            windows = _window(blocks)

            previous_text: str | None = None
            for window in windows:
                overlap_chars = 0
                text = window.text
                if (
                    previous_text is not None
                    and window.chunk_type in _DIVISIBLE
                    and window.extra.get("same_section", True)
                ):
                    tail = _tail_paragraph(previous_text)
                    if tail and len(tail) <= _OVERLAP_CHARS * 2:
                        text = f"{tail}\n\n{window.text}"
                        overlap_chars = len(tail) + 2

                heading_path = tuple(window.extra.get("heading_path", heading_base))
                drafts.append(
                    _draft(
                        ordinal=ordinal,
                        chunk_type=window.chunk_type,
                        heading_path=heading_path,
                        page_start=window.page_start + page_offset,
                        page_end=window.page_end + page_offset,
                        raw_text=text,
                        extra={
                            "boundary_approximate": approximate,
                            "overlap_prefix_chars": overlap_chars,
                            "overlap_from_same_section": bool(overlap_chars),
                            "exercise_overlap": bool(exercises) and not removed,
                            "structure": {
                                "classified_as": window.chunk_type,
                                "signals": list(window.signals),
                                **describe_block(text),
                            },
                        },
                    )
                )
                ordinal += 1
                previous_text = window.text if window.chunk_type in _DIVISIBLE else None

            for question in exercises:
                statement = (question.statement_text or "").strip()
                if len(statement) < _MIN_EXERCISE_CHARS:
                    # Ruido da heuristica numerada do parser. Nao vira chunk,
                    # e tambem nao foi retirado da prosa (mesmo limiar em
                    # _strip_exercise_spans), entao nada se perde.
                    continue
                body = statement
                if question.alternatives_text:
                    body = f"{statement}\n{question.alternatives_text.strip()}"
                page_start = (question.page_start or first_page) + page_offset
                page_end = (question.page_end or question.page_start or first_page) + page_offset

                # PORTAO DE PROMOCAO (Fase 3.1, spec 21). O parser entrega
                # candidatos pela heuristica numerada, cuja precisao medida no
                # livro real foi de ~25%. Promover exige evidencia positiva;
                # veto prevalece; gabarito vira SOLUTION; e o que nao passa e
                # RECLASSIFICADO, nunca descartado.
                verdict = judge_exercise_candidate(body)
                chunk_type = verdict.chunk_type or classify_block(body).chunk_type
                if chunk_type == "EXERCISE" and verdict.chunk_type is None:
                    # Defesa contra reentrada: o classificador normal nunca
                    # decide EXERCISE, mas se um dia decidir, o portao nao
                    # pode ser contornado por ele.
                    chunk_type = "PROSE"

                structure = {
                    "classified_as": chunk_type,
                    "signals": (
                        ["parser_detected"]
                        if verdict.chunk_type == "EXERCISE"
                        else ["parser_detected", "gated"]
                    ),
                    "exercise_evidence": list(verdict.evidence),
                    "exercise_vetoes": list(verdict.vetoes),
                    "decision_reason": verdict.decision_reason,
                    **describe_block(body),
                }
                if verdict.demoted_from:
                    structure["demoted_from"] = verdict.demoted_from

                drafts.append(
                    _draft(
                        ordinal=ordinal,
                        chunk_type=chunk_type,
                        heading_path=heading_base,
                        page_start=page_start,
                        page_end=page_end,
                        raw_text=body,
                        extra={
                            "boundary_approximate": approximate,
                            "question_number": question.question_number,
                            "requires_review": question.requires_review,
                            "structure": structure,
                        },
                    )
                )
                ordinal += 1

        return drafts

    # -- alinhamento secao <-> texto cru ---------------------------------

    def _section_raw_text(
        self, page_texts: Sequence[str], section: ParsedSection
    ) -> tuple[str, int, bool]:
        """Devolve ``(texto, primeira_pagina, aproximado)``.

        O parser nao expoe offsets de caractere, so faixa de paginas. Para
        cortar com precisao, localiza-se o TITULO da secao dentro das paginas
        dessa faixa. Nao localizou, cai para granularidade de pagina e
        sinaliza - uma fronteira aproximada e aceitavel; uma fronteira
        aproximada e silenciosa nao e.
        """
        start = max(1, section.page_start or 1)
        end = min(len(page_texts), section.page_end or len(page_texts))
        if end < start:
            end = start
        window = "\f".join(page_texts[start - 1 : end])

        # Localiza pelo titulo CRU do parser, nao pelo limpo: o cru e
        # literalmente um pedaco do texto, entao e ele que da o corte exato.
        title = " ".join((section.title or "").split())
        if len(title) >= 12:
            # Casamento TOLERANTE a whitespace: o parser juntou com espaco o
            # que o pypdf quebrou em linha. Procurar literalmente falharia na
            # maioria das secoes reais.
            #
            # O corte e feito sobre o texto ORIGINAL, nunca sobre uma versao
            # achatada: achatar destruiria as quebras de linha, e com elas os
            # marcadores estruturais de abertura - foi exatamente o que uma
            # tentativa anterior fez, zerando a deteccao de exemplo resolvido.
            probe = re.compile(
                r"\s+".join(re.escape(part) for part in title[:60].split())
            )
            found = probe.search(window)
            if found is not None:
                return window[found.end() :], start, False
        return window, start, True

    # -- blocos estruturais ----------------------------------------------

    def _blocks(
        self,
        text: str,
        first_page: int,
        heading_base: tuple[str, ...],
        section: ParsedSection,
    ) -> list[_Block]:
        subtitles = _subsection_titles(section)
        blocks: list[_Block] = []
        heading_path = heading_base
        cursor = 0

        for raw_paragraph in _PARAGRAPH_SPLIT.split(text):
            paragraph = _WHITESPACE.sub(" ", raw_paragraph).strip()
            offset = text.find(raw_paragraph, cursor)
            if offset >= 0:
                cursor = offset + len(raw_paragraph)
            page = first_page + text.count("\f", 0, max(offset, 0))
            if not paragraph:
                continue

            # Um paragrafo que E um subtitulo nao vira chunk: vira contexto
            # dos chunks seguintes.
            matched = _matching_subtitle(paragraph, subtitles)
            if matched is not None:
                heading_path = heading_base + (matched,)
                continue

            # ESTRUTURA ANTES DE TAMANHO, e tambem antes de classificar: um
            # marcador de abertura no meio do texto e uma FRONTEIRA. Sem este
            # corte, um capitulo inteiro que contenha um exemplo resolvido
            # herdaria o tipo WORKED_EXAMPLE por completo.
            for segment in split_at_structural_markers(paragraph):
                verdict = classify_block(segment)
                pieces = (
                    _split_at_sentences(segment)
                    if verdict.chunk_type in _DIVISIBLE and len(segment) > _MAX_CHARS
                    else [segment]
                )
                for piece in pieces:
                    body = piece.strip()
                    if not body:
                        continue
                    # Fragmento minusculo nao vira chunk proprio: gruda no
                    # vizinho. Nada de texto se perde, e o indice nao ganha
                    # uma entrada de um caractere.
                    if len(body) < _MIN_CONTENT_CHARS and blocks:
                        previous = blocks[-1]
                        previous.text = f"{previous.text} {body}".strip()
                        previous.page_end = max(previous.page_end, page)
                        continue
                    blocks.append(
                        _Block(
                            text=body,
                            chunk_type=verdict.chunk_type,
                            page_start=page,
                            page_end=page,
                            signals=verdict.signals
                            + (("sentence_split",) if len(pieces) > 1 else ()),
                            extra={"heading_path": heading_path, "same_section": True},
                        )
                    )
        return blocks


# -- janelamento --------------------------------------------------------


def _window(blocks: Sequence[_Block]) -> list[_Block]:
    """Agrupa blocos divisiveis ate o alvo; emite indivisiveis sozinhos.

    Nao atravessa mudanca de ``heading_path``: subsecao e fronteira
    estrutural tanto quanto capitulo.
    """
    out: list[_Block] = []
    buffer: list[_Block] = []

    def flush() -> None:
        if not buffer:
            return
        merged = _merge(buffer)
        # Abaixo do minimo o chunk e ruido no indice: funde com o anterior
        # quando ele e divisivel e da mesma subsecao.
        if (
            out
            and len(merged.text) < _MIN_CHARS
            and out[-1].divisible
            and out[-1].extra.get("heading_path") == merged.extra.get("heading_path")
        ):
            out[-1] = _merge([out[-1], merged])
        else:
            out.append(merged)
        buffer.clear()

    for block in blocks:
        if not block.divisible:
            flush()
            out.append(block)
            continue
        if buffer and buffer[-1].extra.get("heading_path") != block.extra.get("heading_path"):
            flush()
        candidate = len(_merge(buffer + [block]).text) if buffer else len(block.text)
        if buffer and candidate > _MAX_CHARS:
            flush()
        buffer.append(block)
        if len(_merge(buffer).text) >= _TARGET_CHARS:
            flush()

    flush()
    return out


def _merge(blocks: Sequence[_Block]) -> _Block:
    first = blocks[0]
    return _Block(
        text="\n\n".join(block.text for block in blocks),
        chunk_type=_dominant_type(blocks),
        page_start=min(block.page_start for block in blocks),
        page_end=max(block.page_end for block in blocks),
        signals=tuple(dict.fromkeys(signal for block in blocks for signal in block.signals)),
        extra=dict(first.extra),
    )


def _dominant_type(blocks: Sequence[_Block]) -> str:
    """Um grupo que contem uma definicao e, no conjunto, uma definicao -
    informacao mais especifica nao deve desaparecer na fusao."""
    types = [block.chunk_type for block in blocks]
    for specific in ("DEFINITION", "SUMMARY"):
        if specific in types:
            return specific
    return "PROSE"


def _split_at_sentences(paragraph: str) -> list[str]:
    """Quebra um paragrafo grande demais em pedacos de ~alvo, em fronteira de
    SENTENCA.

    Usado so quando nao ha fronteira de paragrafo disponivel. Uma sentenca
    partida ao meio nao sustenta afirmacao nenhuma, entao o ultimo recurso
    ainda respeita a estrutura do texto - nunca corta por contagem de
    caractere. Uma sentenca sozinha acima do maximo e emitida inteira: parti-la
    seria pior que um chunk grande.
    """
    sentences = [s for s in _SENTENCE_SPLIT.split(paragraph) if s.strip()]
    if len(sentences) <= 1:
        return [paragraph]
    pieces: list[str] = []
    current: list[str] = []
    for sentence in sentences:
        candidate = " ".join(current + [sentence])
        if current and len(candidate) > _TARGET_CHARS:
            pieces.append(" ".join(current))
            current = [sentence]
        else:
            current.append(sentence)
    if current:
        pieces.append(" ".join(current))
    return pieces


def _tail_paragraph(text: str) -> str:
    parts = [part for part in text.split("\n\n") if part.strip()]
    return parts[-1].strip() if parts else ""


# -- exercicios ---------------------------------------------------------


def _strip_exercise_spans(
    text: str, exercises: Sequence[ParsedQuestion]
) -> tuple[str, bool]:
    """Remove do fluxo de prosa o texto que ja virara chunk de EXERCISE.

    O parser mantem o exercicio TAMBEM em ``content_lines`` - dualismo
    deliberado da PHASE 26, documentado la. Indexar os dois duplicaria o
    texto e enviesaria o BM25 da Fase 5.

    Devolve ``(texto, removeu_algo)``. Nao conseguiu localizar, nao remove e
    quem chama marca ``exercise_overlap`` - o problema fica visivel em vez de
    virar contagem dupla silenciosa.
    """
    if not exercises:
        return text, False
    stripped = text
    removed = False
    for question in exercises:
        statement = (question.statement_text or "").strip()
        if len(statement) < _MIN_EXERCISE_CHARS:
            # Mesmo limiar do emissor: o que nao vira chunk de exercicio tem
            # de PERMANECER na prosa, senao o texto desaparece do corpus.
            continue
        probe = statement[:120]
        index = stripped.find(probe)
        if index >= 0:
            stripped = stripped[:index] + stripped[index + len(statement) :]
            removed = True
    return stripped, removed


# -- helpers de titulo ---------------------------------------------------


def _clean_heading(raw: str) -> str:
    """Devolve um titulo utilizavel, ou vazio.

    Vazio e melhor que errado: um "titulo" que na verdade e corpo de texto
    entra no ``retrieval_text`` e contamina o hash e o embedding de todos os
    chunks daquela secao. Sem titulo, o chunk ainda tem pagina, tipo e texto
    corretos.
    """
    title = " ".join((raw or "").split())
    if not title or not _HEADING_START.match(title):
        return ""
    # Corta na primeira fronteira de frase: titulo nao tem duas frases.
    cut = re.split(r"(?<=[.!?;])\s", title, maxsplit=1)[0]
    if len(cut) > _MAX_HEADING_CHARS:
        truncated = cut[:_MAX_HEADING_CHARS]
        space = truncated.rfind(" ")
        cut = truncated[:space] if space > 20 else truncated
    return cut.strip(" -–—:")


def _section_heading(section: ParsedSection) -> tuple[str, ...]:
    label = _clean_heading(section.title or "")
    if section.section_number and label:
        label = f"{section.section_type.title()} {section.section_number} - {label}"
    elif section.section_number:
        label = f"{section.section_type.title()} {section.section_number}"
    return (label,) if label else ()


def _subsection_titles(section: ParsedSection) -> tuple[str, ...]:
    """Os ``## `` que o parser ja injetou para subtitulo numerado."""
    return tuple(
        line[3:].strip()
        for line in section.content_lines
        if line.startswith("## ") and line[3:].strip()
    )


def _matching_subtitle(paragraph: str, subtitles: Sequence[str]) -> str | None:
    for subtitle in subtitles:
        if paragraph == subtitle or (
            len(paragraph) <= len(subtitle) + 8 and paragraph.startswith(subtitle[:40])
        ):
            return subtitle
    return None


def _draft(
    *,
    ordinal: int,
    chunk_type: str,
    heading_path: tuple[str, ...],
    page_start: int,
    page_end: int,
    raw_text: str,
    extra: dict[str, Any],
) -> ChunkDraft:
    body = raw_text.strip()
    char_count = len(body)
    metadata = {
        "chunking_policy_version": POLICY_VERSION,
        "source_text_sha256": source_text_hash(body),
        **extra,
    }
    if chunk_type in INDIVISIBLE_CHUNK_TYPES and estimate_tokens(char_count) > MAX_CHUNK_TOKENS:
        # Emitida INTEIRA, com a marca. Cortar um exemplo resolvido ao meio
        # produz dois chunks que nao sustentam afirmacao nenhuma.
        metadata["oversized"] = True
    return ChunkDraft(
        ordinal=ordinal,
        chunk_type=chunk_type,
        heading_path=heading_path,
        page_start=page_start,
        page_end=page_end,
        raw_text=body,
        text_hash=retrieval_text_hash(raw_text=body, heading_path=heading_path),
        char_count=char_count,
        token_estimate=estimate_tokens(char_count),
        metadata=metadata,
    )
