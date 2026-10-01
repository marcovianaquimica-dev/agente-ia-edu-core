"""Normalizacao, tokenizacao e posicoes da busca lexical. Funcoes PURAS.

Sem sessao, sem I/O, sem banco. Isto e deliberado: a normalizacao define o que
o indice SIGNIFICA, e e a parte do subsistema que menos pode estar implicita
dentro de um service.

POR QUE NAO O STEMMER DO POSTGRES
=================================

Sonda real do PostgreSQL 16.15 com a configuracao ``portuguese``:

    concentração  -> concentr        concentracao -> concentraca
    solucao       -> soluca          solucoes     -> soluco
    mol           -> mol             mols         -> mols
    diluicao      -> diluica         diluir       -> dilu

Tres consequencias medidas: quem digita sem acento nao encontra nada;
singular e plural se separam sem acento; e ``molar`` colapsa em ``mol``
enquanto ``molaridade`` vira ``molar``. Alem disso o lexema vive dentro de uma
``TEXT SEARCH CONFIGURATION`` - mudar a regra muda silenciosamente o
significado de todo ``tsvector`` ja gravado, sem registro de qual regra
produziu qual lexema.

Aqui a regra e uma funcao versionada (``POLICY.normalizer_version``), gravada
linha por linha no indice. Trocar a regra e detectavel e reindexar e
verificavel.

A REGRA, E O QUE ELA DELIBERADAMENTE NAO FAZ
============================================

Unificamos a variacao DOMINANTE do vocabulario tecnico - numero gramatical:
``solucoes``/``solucao``, ``reagentes``/``reagente``, ``mols``/``mol``. Nao
unificamos derivacao: ``diluicao`` e ``diluir`` permanecem distintos. O
Snowball tambem nao entrega essa derivacao de forma consistente para entrada
sem acento, entao pagariamos o preco da caixa-preta sem receber o beneficio.

A regra ``es -> vazio`` do ``_morphology_key`` de ``curriculum_classification``
fica FORA: ela quebra toda palavra cujo singular termina em ``-e`` - classe
enorme em quimica (reagente, solvente, oxidante, constante). Medido no livro
real: sob ela ``reagente`` tem df 149; sem ela, 347. Metade das ocorrencias
ficava inalcancavel por uma consulta no singular.

POSICOES INTEGRAS
=================

A posicao conta TODO token do fluxo normalizado, inclusive stopword e token
curto. Stopword nao gera posting, mas OCUPA posicao:

    "concentracao das solucoes"  ->  concentracao@0 , solucao@2

Comprimir para @0/@1 destruiria no INDICE a diferenca entre "a de b" e "a b",
e nenhuma politica de frase posterior poderia recuperar essa informacao. A
politica decide como TRATAR a lacuna (``phrase_slack``); o indice nao decide
por ela.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Mapping, Sequence

from ...knowledge_retrieval_policy.v1 import (
    BODY_STOPWORDS,
    HEADING_STOPWORDS,
    POLICY,
)

_WORD = re.compile(r"[a-z0-9]+")

#: Deslocamento das posicoes do campo de CONTEXTO (``heading_path`` e codigos
#: BNCC) em relacao as do corpo.
#:
#: Sem isto os dois campos compartilhariam o espaco de coordenadas - ambos
#: comecando em 0 - e uma expressao poderia "casar" usando um termo do corpo e
#: outro do titulo, produzindo frase que nao existe em lugar nenhum. Com o
#: deslocamento, qualquer par cruzando os campos fica a ~10^6 posicoes de
#: distancia: muito alem de ``phrase_slack`` e de ``proximity_window``, logo
#: impossivel de casar por acidente. Dentro de cada campo a adjacencia
#: continua exata.
HEADING_POSITION_BASE = 1_000_000

#: Razoes de descarte, nomeadas. Saem na explicacao da consulta para que
#: "por que ``mols`` nao achou nada?" tenha resposta sem adivinhacao.
TOO_SHORT = "TOO_SHORT"
STOPWORD = "STOPWORD"
HEADING_BOILERPLATE = "HEADING_BOILERPLATE"
BARE_NUMBER = "BARE_NUMBER"


@dataclass(frozen=True)
class TokenStream:
    """Resultado da tokenizacao de um texto.

    ``positions`` guarda a posicao no fluxo INTEGRO (ver docstring do modulo).
    ``token_count`` conta POSTINGS - e o ``dl`` do BM25, e a stopword nao
    engorda o documento. ``stream_length`` conta o fluxo todo, e existe para
    que a semantica das posicoes seja verificavel.
    """

    terms: tuple[str, ...]
    positions: Mapping[str, tuple[int, ...]]
    frequencies: Mapping[str, int]
    token_count: int
    stream_length: int
    dropped: tuple[tuple[str, str], ...]


def normalize_text(value: str) -> str:
    """NFKD -> remove acento -> ascii -> minuscula."""
    folded = unicodedata.normalize("NFKD", value or "")
    return folded.encode("ascii", "ignore").decode("ascii").lower()


def normalize_term(term: str) -> str:
    """Numero gramatical apenas. Ver o docstring do modulo.

    As guardas de tamanho existem para nao mutilar termo curto: ``mol`` e o
    termo central da quimica e tem 3 letras; ``gas`` nao pode virar ``ga``.
    """
    if len(term) > 4 and term.endswith("oes"):
        return term[:-3] + "ao"
    if len(term) > 3 and term.endswith("s") and not term.endswith("ss"):
        return term[:-1]
    return term


def _tokenize(value: str, *, stopwords: frozenset[str], drop_numbers: bool) -> TokenStream:
    terms: list[str] = []
    positions: dict[str, list[int]] = {}
    dropped: list[tuple[str, str]] = []
    stream_length = 0

    for raw in _WORD.findall(normalize_text(value)):
        # A posicao e consumida ANTES de qualquer descarte: e isso que
        # preserva a lacuna da stopword.
        position = stream_length
        stream_length += 1

        if len(raw) < POLICY.min_term_length:
            dropped.append((raw, TOO_SHORT))
            continue
        if raw in stopwords:
            dropped.append(
                (raw, HEADING_BOILERPLATE if stopwords is HEADING_STOPWORDS else STOPWORD)
            )
            continue
        if drop_numbers and raw.isdigit():
            dropped.append((raw, BARE_NUMBER))
            continue

        term = normalize_term(raw)[: POLICY.max_term_length]
        terms.append(term)
        positions.setdefault(term, []).append(position)

    return TokenStream(
        terms=tuple(terms),
        positions={term: tuple(found) for term, found in positions.items()},
        frequencies={term: len(found) for term, found in positions.items()},
        token_count=len(terms),
        stream_length=stream_length,
        dropped=tuple(dropped),
    )


def tokenize(value: str) -> TokenStream:
    """Tokeniza CORPO de chunk, ou uma consulta."""
    return _tokenize(value, stopwords=BODY_STOPWORDS, drop_numbers=False)


def tokenize_heading(value: str, *, offset: int = HEADING_POSITION_BASE) -> TokenStream:
    """Tokeniza o campo de CONTEXTO, com a stoplist ESTRUTURAL.

    Numero nu tambem cai: ``Chapter 101`` nao tem conteudo topico nenhum, e
    ``101`` passaria pelo filtro de tamanho minimo.

    As posicoes saem deslocadas por ``HEADING_POSITION_BASE`` - ver a nota da
    constante. ``offset=0`` existe para testar a tokenizacao isolada.
    """
    stream = _tokenize(value, stopwords=HEADING_STOPWORDS, drop_numbers=True)
    if not offset:
        return stream
    return TokenStream(
        terms=stream.terms,
        positions={
            term: tuple(position + offset for position in found)
            for term, found in stream.positions.items()
        },
        frequencies=stream.frequencies,
        token_count=stream.token_count,
        stream_length=stream.stream_length,
        dropped=stream.dropped,
    )


def find_phrase_occurrences(
    document: TokenStream, query: TokenStream, *, slack: int | None = None
) -> tuple[int, ...]:
    """Posicoes iniciais em que a EXPRESSAO da consulta ocorre no documento.

    A consulta diz quantas palavras de funcao esperar entre os termos, porque
    as posicoes dela tambem sao integras: "concentracao das solucoes" tem
    delta 2 entre os dois postings. A politica da a folga ``slack``, e o
    documento casa com delta 1, 2 ou 3.

    Ordem importa: "limitante reagente" nao e a expressao "reagente
    limitante". Uma consulta de um termo so nao tem expressao - frase de uma
    palavra e apenas o termo, e dar bonus por isso seria contar o mesmo sinal
    duas vezes.
    """
    if slack is None:
        slack = POLICY.phrase_slack
    terms = query.terms
    if len(terms) < 2:
        return ()
    if any(term not in document.positions for term in terms):
        return ()

    query_positions = _first_positions(query)
    starts: list[int] = []
    for start in document.positions[terms[0]]:
        current = start
        for index in range(1, len(terms)):
            expected = query_positions[index] - query_positions[index - 1]
            nxt = _next_within(
                document.positions[terms[index]], current, expected, slack
            )
            if nxt is None:
                current = None  # type: ignore[assignment]
                break
            current = nxt
        if current is not None:
            starts.append(start)
    return tuple(starts)


def _first_positions(query: TokenStream) -> list[int]:
    """Posicao da PRIMEIRA ocorrencia de cada termo, na ordem da consulta.

    Uma consulta com termo repetido ("mol de mol") usa a ocorrencia que
    corresponde aquela posicao da expressao.
    """
    seen: dict[str, int] = {}
    out: list[int] = []
    for term in query.terms:
        index = seen.get(term, 0)
        out.append(query.positions[term][index])
        seen[term] = index + 1
    return out


def _next_within(
    candidates: Sequence[int], current: int, expected: int, slack: int
) -> int | None:
    for candidate in candidates:
        if candidate <= current:
            continue
        delta = candidate - current
        if abs(delta - expected) <= slack:
            return candidate
    return None


def best_proximity_span(
    document: TokenStream, terms: Sequence[str]
) -> int | None:
    """Menor janela, em posicoes, que contem ao menos uma ocorrencia de CADA termo.

    ``None`` quando algum termo nao ocorre. Janela de um termo so e 1.
    """
    wanted = tuple(dict.fromkeys(terms))
    if not wanted or any(term not in document.positions for term in wanted):
        return None

    occurrences = sorted(
        (position, term)
        for term in wanted
        for position in document.positions[term]
    )
    needed = len(wanted)
    counts: dict[str, int] = {}
    best = None
    left = 0
    for right, (position, term) in enumerate(occurrences):
        counts[term] = counts.get(term, 0) + 1
        while len(counts) == needed:
            span = position - occurrences[left][0] + 1
            best = span if best is None else min(best, span)
            leaving = occurrences[left][1]
            counts[leaving] -= 1
            if not counts[leaving]:
                del counts[leaving]
            left += 1
    return best
