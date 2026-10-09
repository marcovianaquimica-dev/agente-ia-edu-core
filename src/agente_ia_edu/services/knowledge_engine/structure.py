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


# ----------------------------------------------------------------------
# Portao de promocao de EXERCISE (Fase 3.1, spec 21)
# ----------------------------------------------------------------------
#
# A auditoria do livro real mediu precisao de ~25% na classe EXERCISE: dos
# 1.535 chunks rotulados, ~1.150 eram falso positivo. A causa esta na
# heuristica numerada do parser, que casa item de lista, numeracao de figura e
# referencia bibliografica.
#
# ``authorial_material_parser.py`` NAO e alterado. O parser continua
# entregando candidatos como sempre, e a PHASE 26 nao muda de comportamento -
# o Knowledge Engine passa a DECIDIR se promove cada candidato.
#
# Candidato nao promovido e RECLASSIFICADO, nunca descartado: o texto volta ao
# fluxo e recebe o tipo que o classificador normal lhe der. A contagem total
# de chunks e invariante ao portao.

#: Alternativas de multipla escolha. Aceita ``a)``, ``a.`` e ``a -``: o livro
#: real usa a forma com ponto em varios exercicios, e exigir ")" perdia
#: exercicio verdadeiro - achado da auditoria.
_ALTERNATIVES = re.compile(
    r"(?:^|\s)a\s*[).\-]\s*\S.{0,400}?(?:^|\s)b\s*[).\-]\s*\S.{0,400}?(?:^|\s)c\s*[).\-]\s*\S",
    re.DOTALL | re.IGNORECASE | re.MULTILINE,
)

#: Atribuicao de vestibular entre parenteses.
_EXAM_SOURCE = re.compile(
    r"\((?:ENEM|UF[A-Z]{0,3}|U[A-Z]{2,4}|FUVEST|UNICAMP|UNESP|ITA|IME|"
    r"PUC[-\s]?[A-Z]{0,3}|CEFET|Vunesp|Mackenzie|FGV|[A-Z][a-zá-ú]+-[A-Z]{2})"
    r"[^)]{0,40}\)"
)

#: Imperativo NO INICIO. Ancorar importa: a primeira versao da auditoria
#: procurava em todo o texto e classificava "os algoritmos DETERMINAM o
#: trabalho" - prosa - como exercicio.
_COMMAND = re.compile(
    r"^\s*(?:\([^)]{0,40}\)\s*)?"
    r"(calcule|calculem|determine|determinem|explique|expliquem|justifique|"
    r"justifiquem|indique|indiquem|escreva|escrevam|assinale|assinalem|"
    r"classifique|classifiquem|descreva|descrevam|identifique|identifiquem|"
    r"compare|comparem|interprete|interpretem|represente|representem|"
    r"esboce|esbocem|complete|completem|relacione|relacionem|analise|"
    r"analisem|discuta|discutam|proponha|proponham|verifique|verifiquem|"
    r"mostre|mostrem|pesquise|pesquisem|responda|respondam|cite|citem|"
    r"d[eê]|fa[çc]a|fa[çc]am|qual|quais|por\s+que|quantos|quantas|quanto)\b",
    re.IGNORECASE,
)

#: Acima disso, um "?" no texto e mencao, nao pergunta de exercicio.
_SHORT_QUESTION_MAX_CHARS = 1200

#: GABARITO / RESOLUCAO. Este livro e edicao do professor: traz as respostas.
#: 261 chunks no livro real. Nao e rebaixado para PROSE - recebe o tipo
#: SOLUTION, porque resolucao comentada mostra o PROCEDIMENTO e e o material
#: mais util que um livro didatico oferece ao Knowledge Pack.
_ANSWER_OPENER = re.compile(
    r"^\s*(alternativa\s+[a-e]\b|resposta\s*(pessoal|correta|esperada)?\s*[:.]|"
    r"resolu[çc][aã]o\s*[:.]|gabarito|coment[áa]rio\s*[:.])",
    re.IGNORECASE,
)
_CAPTION_OPENER = re.compile(
    r"^\s*(figura|fig\.|gr[áa]fico|tabela|quadro|esquema)\s*\d+", re.IGNORECASE
)
_BOX_OPENER = re.compile(r"^\s*\((?:cole[çc][aã]o|s[eé]rie|col\.)", re.IGNORECASE)

#: O tipo que um gabarito recebe. NAO e WORKED_EXAMPLE: exemplo resolvido e
#: material de ensino DENTRO do capitulo, gabarito e a resposta de um
#: exercicio especifico, e colapsa-los perderia a distincao.
SOLUTION_CHUNK_TYPE = "SOLUTION"


@dataclass(frozen=True)
class ExerciseVerdict:
    """O que fazer com um candidato a exercicio, e POR QUE.

    ``chunk_type`` e ``None`` quando o candidato nao foi promovido nem
    identificado como gabarito: nesse caso quem chama reclassifica o texto
    pelo classificador normal. Nunca significa "descartar".
    """

    chunk_type: str | None
    evidence: tuple[str, ...]
    vetoes: tuple[str, ...]
    demoted_from: str | None
    decision_reason: str  # PROMOTED | ANSWER_KEY | VETOED | NO_EVIDENCE


def judge_exercise_candidate(text: str) -> ExerciseVerdict:
    """Decide se um candidato do parser merece o rotulo ``EXERCISE``.

    Vetos PREVALECEM sobre evidencia, e a ordem nao e arbitraria: um gabarito
    frequentemente CITA as alternativas e o comando do enunciado que resolve,
    entao avaliar evidencia primeiro promoveria justamente o pior caso.
    """
    body = (text or "").strip()

    vetoes: list[str] = []
    if _ANSWER_OPENER.match(body):
        vetoes.append("answer_opener")
    if _CAPTION_OPENER.match(body):
        vetoes.append("caption_opener")
    if _BOX_OPENER.match(body):
        vetoes.append("box_opener")

    evidence: list[str] = []
    if _ALTERNATIVES.search(body):
        evidence.append("alternatives")
    if _EXAM_SOURCE.search(body):
        evidence.append("exam_source")
    if _COMMAND.match(body):
        evidence.append("command")
    if "?" in body and len(body) <= _SHORT_QUESTION_MAX_CHARS:
        evidence.append("short_question")

    if "answer_opener" in vetoes:
        return ExerciseVerdict(
            chunk_type=SOLUTION_CHUNK_TYPE,
            evidence=tuple(evidence),
            vetoes=tuple(vetoes),
            demoted_from="EXERCISE",
            decision_reason="ANSWER_KEY",
        )
    if vetoes:
        return ExerciseVerdict(
            chunk_type=None,
            evidence=tuple(evidence),
            vetoes=tuple(vetoes),
            demoted_from="EXERCISE",
            decision_reason="VETOED",
        )
    if evidence:
        return ExerciseVerdict(
            chunk_type="EXERCISE",
            evidence=tuple(evidence),
            vetoes=(),
            demoted_from=None,
            decision_reason="PROMOTED",
        )
    return ExerciseVerdict(
        chunk_type=None,
        evidence=(),
        vetoes=(),
        demoted_from="EXERCISE",
        decision_reason="NO_EVIDENCE",
    )
