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
    MIN_USEFUL_CHARS,
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

        # Fase 5.1a: linha de sumario NAO origina secao. A causa, nao o
        # sintoma - deduplicar depois de criar deixaria a seccionacao errada
        # e apagaria texto ja persistido.
        ordenadas = sorted(parsed.sections, key=lambda s: s.position)
        reais = [s for s in ordenadas if not is_table_of_contents_title(s.title)]
        suprimidas = len(ordenadas) - len(reais)
        # FALHA SEGURA: um documento que seja SO sumario nao pode desaparecer.
        # Nesse caso o documento inteiro vira uma secao sintetica, marcada.
        if ordenadas and not reais:
            reais = ordenadas[:1]
        secoes_efetivas = reais
        #: Quanto de cada faixa de paginas ja foi consumido por secoes
        #: anteriores. Ver a nota do cursor em ``_section_raw_text``.
        cursores: dict[tuple, int] = {}

        for posicao, section in enumerate(secoes_efetivas):
            heading_base = _section_heading(section)
            # So as secoes SEGUINTES que ainda tocam a faixa desta: um titulo
            # de capitulo muito posterior nao deve cortar nada aqui.
            fim = section.page_end or section.page_start or 0
            seguintes = [
                outra.title
                for outra in secoes_efetivas[posicao + 1 :]
                if (outra.page_start or 0) <= fim
            ]
            faixa = (section.page_start, section.page_end)
            raw, first_page, approximate, consumido = self._section_raw_text(
                page_texts, section, next_titles=seguintes, cursor=cursores.get(faixa, 0)
            )
            cursores[faixa] = consumido
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
                            # Observabilidade da Fase 5.1a: quantas secoes de
                            # sumario foram suprimidas, e se este chunk saiu
                            # de um corte por tamanho.
                            "sections_suppressed": suprimidas,
                            "section_suppressed": bool(suprimidas),
                            "hard_split": bool(window.extra.get("hard_split")),
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

                # CORRECAO DE FRONTEIRA (Fase 5.1a). Ver
                # ``split_swallowed_statement``: o enunciado para na primeira
                # fronteira estrutural, e o que o parser engoliu depois dele
                # volta ao fluxo de prosa, com a mesma faixa de paginas e a
                # correcao registrada. Nada e truncado nem descartado.
                body, engolido = split_swallowed_statement(body, _MAX_CHARS)
                corrigido = bool(engolido)

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

                for pedaco in bound_indivisible(body):
                    drafts.append(
                        _draft(
                            ordinal=ordinal,
                            chunk_type=chunk_type,
                            heading_path=heading_base,
                            page_start=page_start,
                            page_end=page_end,
                            raw_text=pedaco,
                            extra={
                                "boundary_approximate": approximate,
                                "question_number": question.question_number,
                                "requires_review": question.requires_review,
                                "exercise_boundary_corrected": corrigido,
                                "indivisible_overflow": len(body) > INDIVISIBLE_CEILING_CHARS,
                                "structure": structure,
                            },
                        )
                    )
                    ordinal += 1

                # A cauda engolida passa pelo MESMO caminho de prosa que
                # qualquer outro texto: blocos, janelamento e classificacao.
                if engolido:
                    for janela in _window(
                        self._blocks(engolido, first_page, heading_base, section)
                    ):
                        for pedaco in bound_indivisible(janela.text):
                            drafts.append(
                                _draft(
                                    ordinal=ordinal,
                                    chunk_type=janela.chunk_type,
                                    heading_path=tuple(
                                        janela.extra.get("heading_path", heading_base)
                                    ),
                                    page_start=page_start,
                                    page_end=page_end,
                                    raw_text=pedaco,
                                    extra={
                                        "boundary_approximate": approximate,
                                        "exercise_boundary_corrected": True,
                                        "recovered_from_statement": question.question_number,
                                        "indivisible_overflow": bool(
                                            janela.extra.get("indivisible_overflow")
                                        ),
                                        "structure": {
                                            "classified_as": janela.chunk_type,
                                            "signals": list(janela.signals)
                                            + ["recovered_from_statement"],
                                            **describe_block(pedaco),
                                        },
                                    },
                                )
                            )
                            ordinal += 1

        # RESGATE DE PAGINA (Fase 5.1a). O corte na proxima secao e o cursor
        # eliminaram os sufixos aninhados, mas texto que nenhuma secao
        # reivindica deixaria de ser emitido - e isso seria DESCARTAR
        # conteudo, nao corrigir representacao. Conteudo editorial nao e
        # apagado: ele e representado e depois classificado por
        # ``editorial_role``.
        cobertas = {
            pagina
            for draft in drafts
            for pagina in range(draft.page_start, draft.page_end + 1)
        }
        for indice, texto in enumerate(page_texts, start=1):
            pagina = indice + page_offset
            corpo = (texto or "").strip()
            if pagina in cobertas or len(corpo) < MIN_USEFUL_CHARS:
                continue
            for parte in _hard_split(_WHITESPACE.sub(" ", corpo), _MAX_CHARS):
                verdict = classify_block(parte)
                drafts.append(
                    _draft(
                        ordinal=ordinal,
                        chunk_type=verdict.chunk_type,
                        heading_path=(),
                        page_start=pagina,
                        page_end=pagina,
                        raw_text=parte,
                        extra={
                            "boundary_approximate": True,
                            "residual_page": True,
                            "structure": {
                                "classified_as": verdict.chunk_type,
                                "signals": list(verdict.signals) + ["residual_page"],
                                **describe_block(parte),
                            },
                        },
                    )
                )
                ordinal += 1

        return drafts

    # -- alinhamento secao <-> texto cru ---------------------------------

    def _section_raw_text(
        self,
        page_texts: Sequence[str],
        section: ParsedSection,
        next_titles: Sequence[str] = (),
        cursor: int = 0,
    ) -> tuple[str, int, bool, int]:
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
        # CURSOR: secoes que compartilham a mesma faixa PARTICIONAM o texto,
        # em ordem. Sem isto, uma secao cujo titulo e curto demais para ser
        # localizado (``len(title) >= 12``) caia no fallback e levava a janela
        # INTEIRA - era assim que "MEIS, L" e "ABDALLA, M", nomes de autor da
        # bibliografia, geravam copias da pagina toda.
        base = min(max(cursor, 0), len(window))
        window = window[base:]

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
                body = _until_next_section(window[found.end() :], next_titles)
                return body, start, False, base + found.end() + len(body)
        body = _until_next_section(window, next_titles)
        return body, start, True, base + len(body)

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


def split_swallowed_statement(body: str, limit: int) -> tuple[str, str]:
    """Separa o ENUNCIADO do material que o parser engoliu depois dele.

    Diagnostico que motiva isto (Fase 5.1a): 100% dos chunks acima de 6.000
    caracteres vem do caminho de exercicio, e nenhum do janelamento. O
    ``statement_text`` do parser nao tem fronteira de FIM - quando nenhuma
    questao seguinte e detectada, ele estende ate o fim da secao. Os maiores
    casos medidos nao tem um unico item numerado interno e comecam com
    ``EMSLEY, J.`` (bibliografia), com o texto de competencias da BNCC ou com
    ``Espera-se que os estudantes...`` (gabarito).

    A correcao e de FRONTEIRA, nao de tamanho: o enunciado termina na
    primeira fronteira ESTRUTURAL do texto - marcador de abertura ou quebra de
    paragrafo -, e nao num ponto escolhido por contagem de caractere. O que
    vem depois nunca pertenceu a unidade, e volta ao fluxo de prosa.

    Devolve ``(cabeca, cauda)``. Cauda vazia significa que nada foi engolido.
    """
    if len(body) <= limit:
        return body, ""

    # 1a escolha: marcador estrutural de abertura - a mesma fronteira que a
    # Fase 3 usou para impedir um capitulo inteiro de herdar WORKED_EXAMPLE.
    segments = split_at_structural_markers(body)
    if len(segments) > 1 and len(segments[0]) <= limit:
        head = segments[0]
        return head, body[len(head) :].lstrip()

    # 2a escolha: a PRIMEIRA fronteira de paragrafo que produza uma cabeca de
    # tamanho plausivel para um enunciado. Primeira, nao a ultima que couber
    # no limite: o que vem depois da primeira quebra, num enunciado que ja
    # passou do maximo, foi engolido - nao faz parte da unidade. O piso evita
    # que uma quebra espuria no inicio produza um "enunciado" de duas
    # palavras.
    for separator in ("\n\n", "\n"):
        posicao = body.find(separator)
        while 0 < posicao <= limit:
            if posicao >= _MIN_EXERCISE_CHARS:
                return body[:posicao].strip(), body[posicao:].strip()
            posicao = body.find(separator, posicao + len(separator))

    # 3a escolha: fronteira de SENTENCA. O pypdf devolve paginas inteiras sem
    # uma unica linha em branco, entao a maioria dos enunciados engolidos nao
    # tem fronteira de paragrafo alguma - medido: todos os 130 maiores chunks
    # restantes caiam aqui. Sentenca ainda e estrutura do texto, e e o mesmo
    # ultimo recurso que a Fase 3 ja adota em ``_split_at_sentences``;
    # caractere nao seria.
    sentencas = _SENTENCE_SPLIT.split(body)
    if len(sentencas) > 1:
        head: list[str] = []
        for sentenca in sentencas:
            candidato = " ".join(head + [sentenca])
            if head and len(candidato) > limit:
                break
            head.append(sentenca)
        texto = " ".join(head).strip()
        if texto and len(texto) < len(body.strip()):
            return texto, body[len(texto) :].lstrip()

    # Nenhuma fronteira estrutural de especie alguma: a unidade e grande DE
    # VERDADE. Nao se divide aqui - quem decide e a politica explicita de
    # indivisibilidade, e ela marca o que fizer.
    return body, ""


def _until_next_section(text: str, next_titles: Sequence[str]) -> str:
    """Corta o texto onde a PROXIMA secao comeca.

    Esta e a correcao estrutural da Fase 5.1a, e a causa que ela ataca e
    maior que o sumario. ``_section_raw_text`` devolvia o sufixo da faixa de
    paginas INTEIRA a partir do titulo, ignorando onde a secao seguinte
    comeca. Quando varias secoes compartilham a mesma faixa - medido: 19
    faixas repetidas no Cotidiano v1, envolvendo 107 das 264 secoes, entre
    elas 12 secoes na pagina 9 cujos titulos sao nomes de autor de
    bibliografia - cada uma levava o sufixo inteiro, e o resultado eram
    SUFIXOS ANINHADOS: 16 a 23% do acervo com texto contido em outro chunk.

    O corte e no PRIMEIRO titulo seguinte que ocorrer, com o mesmo casamento
    tolerante a whitespace usado para localizar a propria secao. Nenhum
    caractere se perde: o que e cortado aqui pertence a secao seguinte, e e
    ela que o emite.
    """
    limite = len(text)
    for title in next_titles:
        flat = " ".join((title or "").split())
        if len(flat) < 12:
            continue
        probe = re.compile(r"\s+".join(re.escape(part) for part in flat[:60].split()))
        found = probe.search(text)
        if found is not None and found.start() < limite:
            limite = found.start()
    return text[:limite]


#: Corrida de pontos que so existe em linha de sumario. Quatro ou mais:
#: reticencias tem tres, e "E assim por diante..." nao e sumario.
_DOT_LEADER = re.compile(r"\.{4,}")


def is_table_of_contents_title(title: str | None) -> bool:
    """O titulo que o parser achou e, na verdade, uma LINHA DE SUMARIO?

    Medido no livro real: na pagina 452 do Cotidiano v1 - o sumario do manual
    do professor - o parser criou 24 secoes cujo titulo e a propria linha do
    sumario, pontilhados inclusive. Cada uma abria uma janela sobre a mesma
    pagina e cortava a partir do seu titulo, produzindo SUFIXOS ANINHADOS:
    40 chunks numa pagina de 8.614 caracteres.

    A funcao e pura e conservadora de proposito. Pontilhado e um sinal
    TIPOGRAFICO inequivoco - nenhum cabecalho real de capitulo carrega quatro
    pontos seguidos - e por isso basta sozinho. Sinais mais fracos (titulo
    terminado em numero de pagina, por exemplo) ficariam a um passo de
    suprimir capitulo legitimo, e suprimir conteudo e o erro caro aqui.

    Nao resolve o sumario de obra que nao usa pontilhado: medido, "Investigar
    e Conhecer" tem apenas 2 chunks com pontilhado. Para essas, o que remove o
    material da recuperacao e o ``editorial_role`` da Fase 5.1b, nao esta
    funcao.
    """
    return bool(_DOT_LEADER.search(title or ""))


#: Teto da excecao de indivisibilidade. Acima disto a alegacao deixa de ser
#: cridivel: medido no SuperAcao, um "exercicio" de 70.990 caracteres e
#: artefato da heuristica numerada do parser, nao uma unidade pedagogica.
INDIVISIBLE_CEILING_CHARS = MAX_CHUNK_TOKENS * CHARS_PER_TOKEN * 3


def bound_indivisible(text: str) -> list[str]:
    """Mantem a unidade indivisivel inteira, ate o teto.

    A excecao da Fase 3 continua valendo - cortar um exemplo resolvido ao meio
    produz dois chunks que nao sustentam afirmacao nenhuma -, mas ela tem
    limite. Alem do teto o texto e cortado, e o corte fica OBSERVAVEL.
    """
    if len(text) <= INDIVISIBLE_CEILING_CHARS:
        return [text]
    return _hard_split(text, INDIVISIBLE_CEILING_CHARS)


def _hard_split(text: str, limit: int) -> list[str]:
    """Ultimo recurso de tamanho, na melhor fronteira disponivel.

    ``_split_at_sentences`` devolve o paragrafo INTEIRO quando nao acha
    fronteira de sentenca - e foi assim que 12 paginas de sumario, sem um
    unico ponto final seguido de maiuscula, viraram um bloco de 45.040
    caracteres. Aqui a fronteira degrada: espaco em branco perto do limite e,
    se nem isso existir, corte seco. Nenhum caractere e descartado.
    """
    if len(text) <= limit:
        return [text]
    pieces: list[str] = []
    rest = text
    while len(rest) > limit:
        cut = rest.rfind(" ", limit // 2, limit)
        if cut <= 0:
            cut = limit
        pieces.append(rest[:cut].strip())
        rest = rest[cut:].lstrip()
    if rest:
        pieces.append(rest)
    return [piece for piece in pieces if piece]


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
            # Fundir repetidamente crescia sem teto: era dai que vinham os
            # chunks PROSE de 15 mil caracteres, que nenhum limite pegava
            # porque nunca passaram pelo caminho de bloco grande.
            and len(out[-1].text) + len(merged.text) + 2 <= _MAX_CHARS
        ):
            out[-1] = _merge([out[-1], merged])
        else:
            out.append(merged)
        buffer.clear()

    for block in blocks:
        if not block.divisible:
            flush()
            # Teto da excecao de indivisibilidade - ver ``bound_indivisible``.
            pieces = bound_indivisible(block.text)
            for piece in pieces:
                out.append(
                    _Block(
                        text=piece,
                        chunk_type=block.chunk_type,
                        page_start=block.page_start,
                        page_end=block.page_end,
                        signals=block.signals
                        + (("indivisible_overflow",) if len(pieces) > 1 else ()),
                        extra={
                            **block.extra,
                            "indivisible_overflow": len(pieces) > 1,
                        },
                    )
                )
            continue
        if buffer and buffer[-1].extra.get("heading_path") != block.extra.get("heading_path"):
            flush()
        # INVARIANTE: nada janelavel escapa do limite. O teste antigo era
        # ``if buffer and candidate > _MAX_CHARS`` - com o buffer VAZIO, um
        # bloco unico nunca era testado, e um bloco de 45.040 caracteres saia
        # inteiro. A excecao continua existindo so para unidade
        # comprovadamente indivisivel, acima, e la ela e marcada.
        if len(block.text) > _MAX_CHARS:
            flush()
            for index, piece in enumerate(_hard_split(block.text, _MAX_CHARS)):
                out.append(
                    _Block(
                        text=piece,
                        chunk_type=block.chunk_type,
                        page_start=block.page_start,
                        page_end=block.page_end,
                        signals=block.signals + ("hard_split",),
                        extra={**block.extra, "hard_split": True},
                    )
                )
            continue
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


class CurriculumFrameworkChunker:
    """Chunker de documento NORMATIVO - a BNCC.

    NAO e o ``ProseChunker``, e a diferenca nao e de parametro, e de tipo
    (spec 22.1). Um livro didatico e prosa continua que precisa de janelamento
    por tamanho; a BNCC e lista normativa codificada, cuja unidade natural e
    UMA HABILIDADE. Janelar habilidades por tamanho cortaria habilidade ao
    meio e perderia o codigo, que e a unica chave util.

    Uma habilidade = um chunk ``CURRICULUM_ITEM``, nunca dividido, nunca
    fundido, nunca com overlap. ``CURRICULUM_ITEM`` ja esta em
    ``INDIVISIBLE_CHUNK_TYPES`` desde a Fase 1.

    ``bncc_node_codes`` guarda so o codigo, porque sua funcao e recuperacao.
    ``metadata.bncc`` guarda a TRIPLA normativa completa (spec 22.3): o codigo
    isolado nao e identidade eterna.
    """

    def chunk(self, *, framework, page_offset: int = 0) -> list[ChunkDraft]:
        drafts: list[ChunkDraft] = []
        ordinal = 1
        for competency in framework.competencies:
            heading_path = (
                framework.area_name,
                f"Competência específica {competency.number}",
            )
            for skill in competency.skills:
                page = skill.page + page_offset
                drafts.append(
                    _draft(
                        ordinal=ordinal,
                        chunk_type="CURRICULUM_ITEM",
                        heading_path=heading_path,
                        page_start=page,
                        page_end=page,
                        raw_text=skill.statement,
                        extra={
                            "boundary_approximate": False,
                            "bncc_node_codes": [skill.code],
                            "bncc": {
                                "taxonomy_code": framework.taxonomy_code,
                                "taxonomy_version": framework.taxonomy_version,
                                "node_code": skill.code,
                                "urn": (
                                    f"{framework.taxonomy_code}:"
                                    f"{framework.taxonomy_version}:{skill.code}"
                                ),
                                "competency_code": competency.code,
                                "area_code": framework.area_code,
                                "page": skill.page,
                            },
                            "extractor_version": framework.extractor_version,
                            "dehyphenations": skill.dehyphenations,
                            "structure": {
                                "classified_as": "CURRICULUM_ITEM",
                                "signals": ["bncc_skill_code"],
                                **describe_block(skill.statement),
                            },
                        },
                    )
                )
                ordinal += 1
        return drafts
