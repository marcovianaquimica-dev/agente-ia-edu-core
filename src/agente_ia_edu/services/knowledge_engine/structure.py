"""Heuristicas de estrutura pedagogica de um bloco de texto.

PURAS: sem I/O, sem sessao, sem estado. Entra texto, sai um veredito.

AMBICAO DELIBERADAMENTE LIMITADA (spec 20.4). A prioridade da Fase 3 e, nesta
ordem: texto, paginas, origem, hierarquia, hash, rastreabilidade. Essas seis
precisam estar certas. A classificacao estrutural NAO tenta resolver todo
caso extremo de formula, exemplo, tabela ou exercicio.

O raciocinio por tras disso: uma classificacao errada degrada o RANKING de
uma busca futura; nao corrompe o corpus, porque ``raw_text``, pagina e
proveniencia seguem corretos. Ja um ``raw_text`` errado nao tem conserto
depois.

Por isso todo veredito carrega os ``signals`` que o dispararam: um falso
positivo precisa ser observavel para poder ser refinado numa fase posterior,
em vez de virar folclore sobre "o chunker as vezes erra".
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# -- exemplo resolvido --------------------------------------------------
# A unidade de maior valor pedagogico do livro, e a que mais sofre com corte.
_WORKED_EXAMPLE = re.compile(
    r"(?:^|\n)\s*(exemplo\s+resolvido|exerc[íi]cio\s+resolvido|exemplo\s+\d+|"
    r"resolu[çc][ãa]o\s*:|solu[çc][ãa]o\s*:)",
    re.IGNORECASE,
)

# -- definicao ----------------------------------------------------------
_DEFINITION = re.compile(
    r"\b(é\s+definid[oa]\s+como|chama-se|denomina-se|define-se|"
    r"recebe\s+o\s+nome\s+de|defini[çc][ãa]o\s*:)",
    re.IGNORECASE,
)

# -- resumo -------------------------------------------------------------
_SUMMARY = re.compile(
    r"(?:^|\n)\s*(resumo|s[íi]ntese|retomando|em\s+resumo|revis[ãa]o\s+do\s+cap)",
    re.IGNORECASE,
)

# -- tabela markdown (so os caminhos .md/.docx produzem isto) -----------
_MD_TABLE_ROW = re.compile(r"^\s*\|.*\|\s*$")
_MD_TABLE_RULE = re.compile(r"^\s*\|[\s:|-]+\|\s*$")

# -- formula ------------------------------------------------------------
# Simbolos de equacao quimica/matematica. NAO inclui '-' nem '.', comuns
# demais em prosa.
_FORMULA_CHARS = re.compile(r"[=→←⇌⇄≈≤≥±×÷∑∫√·]")
_SUBSCRIPT_FORMULA = re.compile(r"\b[A-Z][a-z]?\d+(?:[A-Z][a-z]?\d*)*\b")
_SENTENCE_END = re.compile(r"[.!?]\s")

# -- figura e tabela referenciadas --------------------------------------
_FIGURE_CAPTION = re.compile(
    r"(?:^|\n)\s*(figura|fig\.|gr[áa]fico|esquema|quadro)\s*\d+\s*[-–—:.]",
    re.IGNORECASE,
)
_TABLE_REFERENCE = re.compile(r"\btabela\s+\d+", re.IGNORECASE)

#: Marcadores estruturais sao marcadores de ABERTURA. Procura-los no texto
#: inteiro faria um capitulo que contem um exemplo resolvido virar, por
#: completo, um exemplo resolvido - que foi exatamente o que aconteceu antes
#: desta constante existir. A classificacao olha so o inicio do bloco; quem
#: corta o bloco no marcador e ``split_at_structural_markers``.
_MARKER_HEAD_CHARS = 160

#: Acima disso, uma linha curta cheia de simbolos e provavelmente formula.
_FORMULA_SYMBOL_DENSITY = 0.02
#: Formula destacada e curta por natureza; um paragrafo longo com um '=' e
#: prosa que menciona uma equacao, nao uma equacao.
_DISPLAYED_FORMULA_MAX_CHARS = 240
#: Sinal de glifo corrompido na origem - o projeto ja tem um caso real e
#: irreparavel disso (FUVEST/CambriaMath). Nao se conserta aqui; marca-se.
_REPLACEMENT_CHARS = re.compile(r"[�\x00-\x08\x0b\x0c\x0e-\x1f]")


@dataclass(frozen=True)
class StructureVerdict:
    """O tipo decidido e POR QUE. ``signals`` existe para que um falso
    positivo seja observavel e refinavel depois."""

    chunk_type: str
    signals: tuple[str, ...]


def is_markdown_table(text: str) -> bool:
    lines = [line for line in text.splitlines() if line.strip()]
    if len(lines) < 2:
        return False
    rows = sum(1 for line in lines if _MD_TABLE_ROW.match(line))
    has_rule = any(_MD_TABLE_RULE.match(line) for line in lines)
    return has_rule and rows >= 2


def formula_symbol_density(text: str) -> float:
    stripped = text.strip()
    if not stripped:
        return 0.0
    return len(_FORMULA_CHARS.findall(stripped)) / len(stripped)


def is_displayed_formula(text: str) -> bool:
    """Formula DESTACADA, nao formula mencionada no meio de um paragrafo."""
    stripped = text.strip()
    if not stripped or len(stripped) > _DISPLAYED_FORMULA_MAX_CHARS:
        return False
    if _SENTENCE_END.search(stripped):
        return False
    if formula_symbol_density(stripped) >= _FORMULA_SYMBOL_DENSITY:
        return True
    return bool(_SUBSCRIPT_FORMULA.search(stripped)) and len(stripped.split()) <= 12


def has_inline_formula(text: str) -> bool:
    return bool(_FORMULA_CHARS.search(text)) or bool(_SUBSCRIPT_FORMULA.search(text))


def is_formula_suspect(text: str) -> bool:
    """Sinal de corrupcao de glifo na origem.

    Nao se conserta nada aqui. Este projeto ja tem um caso real e irreparavel
    (fonte CambriaMath em prova da FUVEST): o defeito esta no PDF de origem.
    Marcar e util; fingir que se consertou nao e.
    """
    return bool(_REPLACEMENT_CHARS.search(text))


def has_figure_caption(text: str) -> bool:
    return bool(_FIGURE_CAPTION.search(text))


def references_table(text: str) -> bool:
    return bool(_TABLE_REFERENCE.search(text))


_OPENING_MARKERS = re.compile(
    r"(?:^|\n)\s*(?=(?:exemplo\s+resolvido|exerc[íi]cio\s+resolvido|exemplo\s+\d+|"
    r"resolu[çc][ãa]o\s*:|solu[çc][ãa]o\s*:|resumo\b|s[íi]ntese\b))",
    re.IGNORECASE,
)


def split_at_structural_markers(text: str) -> list[str]:
    """Corta um bloco nos marcadores de abertura que ele contiver.

    Marcador estrutural e FRONTEIRA, nao so rotulo. Em PDF real o pypdf
    costuma devolver a pagina inteira sem nenhuma linha em branco, entao um
    capitulo e um "paragrafo" so - e sem este corte o capitulo todo herdaria
    o tipo do primeiro marcador que aparecesse nele.

    E o que faz "estrutura antes de tamanho" valer tambem quando o texto nao
    traz paragrafos.
    """
    pieces = [piece for piece in _OPENING_MARKERS.split(text) if piece and piece.strip()]
    return pieces if len(pieces) > 1 else [text]


def classify_block(text: str) -> StructureVerdict:
    """Classifica um bloco de texto ja delimitado.

    Ordem do mais especifico ao mais generico. O primeiro que casa vence -
    um exemplo resolvido que contem uma formula e um exemplo resolvido, nao
    uma formula.

    Marcadores sao procurados so no INICIO do bloco (``_MARKER_HEAD_CHARS``),
    porque sao marcadores de abertura. Quem parte o bloco quando ha um
    marcador no meio e ``split_at_structural_markers``.

    NAO decide ``EXERCISE``: exercicio vem do parser, que ja o detecta com o
    numero do item e a pagina, e nao de heuristica textual aqui.
    """
    signals: list[str] = []

    if is_markdown_table(text):
        return StructureVerdict("TABLE", ("markdown_table",))

    head = text[:_MARKER_HEAD_CHARS]

    if _WORKED_EXAMPLE.search(head):
        signals.append("worked_example_marker")
        return StructureVerdict("WORKED_EXAMPLE", tuple(signals))

    if is_displayed_formula(text):
        signals.append("displayed_formula")
        return StructureVerdict("FORMULA", tuple(signals))

    if _SUMMARY.search(head):
        return StructureVerdict("SUMMARY", ("summary_marker",))

    if _DEFINITION.search(head):
        return StructureVerdict("DEFINITION", ("definition_phrase",))

    return StructureVerdict("PROSE", ())


def describe_block(text: str) -> dict[str, bool]:
    """Sinais auxiliares que acompanham qualquer chunk, independentemente do
    tipo. Vao para ``metadata.structure`` e tornam o refinamento posterior
    possivel sem reprocessar o corpus."""
    return {
        "has_formula": has_inline_formula(text),
        "formula_suspect": is_formula_suspect(text),
        "has_figure_caption": has_figure_caption(text),
        "references_table": references_table(text),
    }
