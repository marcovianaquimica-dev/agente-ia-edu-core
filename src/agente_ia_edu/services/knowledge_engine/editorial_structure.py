"""Deteccao de `editorial_role` - funcao editorial do chunk NA OBRA.

Funcoes PURAS. Sem banco, sem sessao, sem I/O.

ORTOGONAL A ``chunk_type``, e a distincao nao e sutil:

    chunk_type      forma e funcao pedagogica LOCAL  (prosa, exercicio, tabela)
    editorial_role  funcao EDITORIAL na obra         (conteudo, gabarito, sumario)

Um gabarito pode ser ``PROSE`` ou ``EXERCISE`` ou ``SOLUTION`` - e e
``ANSWER_KEY`` nos tres casos. ``SOLUTION`` + ``ANSWER_KEY`` e combinacao
legitima e esperada.

TRES CAMADAS, E A MEDICAO QUE AS IMPOE
======================================

**1. REGIAO.** O manual do professor e uma regiao CONTIGUA nas tres obras do
piloto: p477-543 em Investigar (pureza 99%), p467-543 em Cotidiano (97%),
p465-541 em SuperAcao (90%). A regiao e derivada de DENSIDADE DE MARCADORES,
nunca de posicao.

E e ela que resolve o problema central: medido, **34% a 66% dos chunks do
ultimo 15% de cada livro nao disparam sinal algum** - "Sim, pois faz parte da
ideia...", "Resposta pessoal.", "Possiveis respostas:". Nenhum detector por
chunk isolado os pegaria; dentro da regiao, eles sao gabarito.

**2. PERFIL DA OBRA.** Um detector unico seria errado, e a medicao e
categorica:

    marcador                 Investigar   Cotidiano   SuperAcao
    MPxxx                             0         188         161
    romano isolado (folio)          95p          10          14
    pontilhado >= 3                   2          91          32
    instrucao ao professor          129          49         113

``MPxxx`` nao existe em Investigar. O pontilhado quase nao existe la. O unico
sinal presente nas tres obras e a instrucao ao professor. Perfis sao ativados
por EVIDENCIA OBSERVADA no documento, nunca pelo nome da editora - um perfil
"Moderna" que ligasse por metadado erraria no dia em que outra editora
adotasse a mesma convencao, e vice-versa.

**3. SINAIS UNIVERSAIS por chunk.** Refinam dentro da regiao e pegam
ocorrencias fora dela.

A ASSIMETRIA QUE GOVERNA A INCERTEZA
====================================

Um falso positivo **esconde conteudo legitimo e ninguem percebe**. Um falso
negativo apenas mantem o estado atual, que e o de hoje. Logo:

- evidencia insuficiente -> ``UNKNOWN``, que e ELEGIVEL;
- papel desconhecido pela politica -> ELEGIVEL, por decisao explicita;
- nada e apagado: pagina, origem, literal restrito, hashes e rastreabilidade
  seguem intactos. ``editorial_role`` e um ROTULO, nao uma exclusao.

Medicao que justifica o rigor: o detector ingenuo da sondagem acertou **5 de
14** (~36%) numa amostra manual de ANSWER_KEY. Um detector de 36% de precisao
pioraria o sistema - esconderia conteudo real em troca de limpar ruido.
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass
from typing import Mapping, Sequence

#: Versao do DETECTOR, gravada por chunk. Coluna nula significa "nao
#: processado por esta versao"; ``UNKNOWN`` com versao preenchida significa
#: "classificado, e a evidencia nao bastou". Nao se confundem.
DETECTOR_VERSION = "v1"

# -- sinais ---------------------------------------------------------------

#: Pontilhado de sumario. Quatro pontos ja seriam indicio; seis evitam
#: qualquer confusao com reticencias.
_DOT_LEADERS = re.compile(r"\.{6,}")

#: Linha de sumario DE VERDADE: pontilhado seguido de folio - numero de
#: pagina ou marcador do tipo MP017 / romano.
#:
#: O pontilhado sozinho reprovou na amostra real: p422 e p427 de "Investigar
#: e Conhecer" sao texto corrido sobre polimeros e tinham 3+ corridas de
#: pontos. Uma linha de sumario APONTA para algum lugar; texto corrido nao.
_DOT_LEADER_LINE = re.compile(r"\.{4,}\s*(?:MP\s?\d{3}|[LXCVIM]{2,8}|\d{1,4})\s*(?:\n|$)")

#: Folio do manual docente na Moderna.
_MP_MARKER = re.compile(r"\bMP\s?\d{3}\b")

#: Folio romano em LINHA ISOLADA - o marcador de "Investigar e Conhecer".
#: Precisa estar sozinho na linha: "o capitulo XIV" nao e folio.
_ROMAN_FOLIO = re.compile(r"(?:^|\n)\s*[LXCVIM]{2,8}\s*(?:\n|$)")

#: Insensivel a caixa DE PROPOSITO: Investigar escreve "Alternativa d." e foi
#: por exigir maiuscula que a primeira sondagem deu zero naquela obra.
_ALTERNATIVE = re.compile(r"\bAlternativa\s+[A-Ea-e]\b")

#: Instrucao ao PROFESSOR. Deliberadamente restrito a frases em que o
#: destinatario e inequivocamente o docente.
#:
#: A primeira versao incluia "Enfatize", "Proponha" e "Convide" soltos, e a
#: amostra real reprovou: p17 ("quando lemos que sao necessarias de 5 a 7
#: toneladas de bauxita") e p45 ("Observe a tirinha e faca o que se pede")
#: sao texto e atividade DO ALUNO. Verbo no imperativo nao identifica
#: destinatario; "os estudantes" como objeto, sim.
_TEACHER = re.compile(
    r"Ajude os estudantes|Oriente (?:a turma|os estudantes)|"
    r"Espera-se que (?:a turma|os estudantes)|Comente com (?:a turma|os estudantes)|"
    r"Incentive os estudantes|Convide (?:a turma|os estudantes|um grupo)|"
    r"Proponha (?:a|aos) (?:turma|estudantes)|Sugest[oõ]es did[aá]ticas",
    re.IGNORECASE,
)

_ANSWER_OPENER = re.compile(
    r"Resposta pessoal|Poss[ií]veis respostas|Resolu[cç][oõ]es e coment[aá]rios|"
    r"\bGabarito\b|Atividades propostas\s*[-–]\s*P[aá]gina",
    re.IGNORECASE,
)

_REFERENCE = re.compile(
    r"\bISBN\b|Dispon[ií]vel em:|Acesso em:|Refer[eê]ncias bibliogr[aá]ficas",
    re.IGNORECASE,
)

#: Cabecalho proprio da secao de referencias - inequivoco, ao contrario de
#: uma citacao solta.
_REFERENCE_HEADING = re.compile(
    r"REFER[EÊ]NCIAS\s+BIBLIOGR[AÁ]FICAS|Refer[eê]ncias bibliogr[aá]ficas|"
    r"Refer[eê]ncias comentadas|Bibliografia\s*(?:\n|$)",
    re.IGNORECASE,
)

_INDEX = re.compile(r"[ÍI]ndice\s+remissivo", re.IGNORECASE)

_FRONT = re.compile(
    r"Dados Internacionais de Cataloga|Todos os direitos reservados|"
    r"Diretoria editorial|Coordena[cç][aã]o editorial|\bCIP\b",
    re.IGNORECASE,
)

_SIGNALS: Mapping[str, re.Pattern[str]] = {
    "dot_leaders": _DOT_LEADERS,
    "mp_marker": _MP_MARKER,
    "roman_folio": _ROMAN_FOLIO,
    "alternative_answer": _ALTERNATIVE,
    "teacher_instruction": _TEACHER,
    "answer_opener": _ANSWER_OPENER,
    "reference_apparatus": _REFERENCE,
    "index_heading": _INDEX,
    "front_matter_apparatus": _FRONT,
}

#: Sinais que, por si, indicam material NAO-conteudo com forca suficiente
#: para marcar uma PAGINA. "alternative_answer" entra porque uma pagina de
#: conteudo nao diz "Alternativa B".
_STRONG_PAGE_SIGNALS = frozenset(
    {
        "mp_marker",
        "roman_folio",
        "alternative_answer",
        "teacher_instruction",
        "answer_opener",
        "dot_leaders",
    }
)

#: Ocorrencias de "Alternativa X" que, numa pagina so, bastam para marca-la
#: sem precisar de segundo sinal. Uma pagina de respostas lista muitas em
#: sequencia; conteudo cita no maximo uma ou duas.
#:
#: Medido: sem isto, a secao de respostas de SuperAcao (~p432-440, o arquivo
#: "pdf_434_440_bquivu_finais") nao formava regiao, e os seus chunks caiam no
#: caminho frageil de sinal isolado.
_ANSWER_DENSITY_PER_PAGE = 3

#: Quantas paginas um marcador precisa cobrir para ATIVAR um perfil. Dez e
#: conservador: um marcador solto nao define convencao editorial.
_PROFILE_MIN_PAGES = 5

#: Uma pagina e "marcada" com dois sinais fortes. Com um so, texto de
#: conteudo que cite "Alternativa B" seria marcado.
_REGION_MIN_SIGNALS = 2

#: Lacuna tolerada dentro de uma regiao. O manual real tem paginas de
#: abertura sem marcador no meio.
_REGION_GAP_TOLERANCE = 2

#: Uma pagina solta nao faz regiao - falha para o lado de incluir.
_REGION_MIN_PAGES = 3

#: Fracao inicial do documento em que FRONT_MATTER se sustenta. Posicao e
#: sinal AUXILIAR: ela nunca decide sozinha, mas uma pagina de catalogacao no
#: meio do livro e outra coisa.
_FRONT_MATTER_MAX_FRACTION = 0.06


@dataclass(frozen=True)
class EditorialRegion:
    """Faixa contigua de paginas com densidade de marcadores editoriais."""

    region_id: str
    page_start: int
    page_end: int
    signals: tuple[str, ...]
    marked_pages: int

    def contains(self, page: int | None) -> bool:
        return page is not None and self.page_start <= page <= self.page_end


@dataclass(frozen=True)
class EditorialVerdict:
    role: str
    confidence: float
    signals: tuple[str, ...]
    vetoes: tuple[str, ...]
    decision_reason: str
    region_id: str | None
    profiles: tuple[str, ...]
    detector_version: str = DETECTOR_VERSION

    def as_metadata(self) -> dict[str, object]:
        return {
            "editorial_role": self.role,
            "confidence": self.confidence,
            "signals": list(self.signals),
            "vetoes": list(self.vetoes),
            "decision_reason": self.decision_reason,
            "region_id": self.region_id,
            "profiles": list(self.profiles),
            "detector_version": self.detector_version,
        }


def _fold(value: str) -> str:
    """Canonicaliza para a forma COMPOSTA (NFC).

    NFC, nao NFKD. NFKD **decompoe** o acento em caractere base mais marca
    combinante, e com isso ``[ÍI]ndice`` deixa de casar com ``Índice`` e
    ``Resolu[cç][oõ]es`` deixa de casar com ``Resolucoes`` acentuado - os dois
    padroes esperam a forma composta. Foi exatamente esse o erro na primeira
    versao deste modulo, e dois testes o pegaram.

    A insensibilidade a acento, onde e desejada, vem das classes de caractere
    dos proprios padroes.
    """
    return unicodedata.normalize("NFC", value or "")


def page_signals(text: str) -> frozenset[str]:
    """Sinais editoriais presentes num texto. Puro e sem estado."""
    body = _fold(text)
    return frozenset(name for name, pattern in _SIGNALS.items() if pattern.search(body))


def detect_profiles(page_texts: Sequence[str]) -> frozenset[str]:
    """Perfis documentais ATIVADOS POR EVIDENCIA observada no documento.

    Nunca pelo nome da editora: um perfil que ligasse por metadado erraria no
    dia em que outra editora adotasse a mesma convencao.
    """
    contagem: dict[str, int] = {}
    for text in page_texts:
        for name in page_signals(text):
            contagem[name] = contagem.get(name, 0) + 1

    perfis = set()
    if contagem.get("mp_marker", 0) >= _PROFILE_MIN_PAGES:
        perfis.add("moderna_mp")
    if contagem.get("roman_folio", 0) >= _PROFILE_MIN_PAGES:
        perfis.add("roman_folio")
    if contagem.get("dot_leaders", 0) >= _PROFILE_MIN_PAGES:
        perfis.add("dot_leader_toc")
    return frozenset(perfis)


def detect_regions(page_texts: Sequence[str]) -> tuple[EditorialRegion, ...]:
    """Faixas contiguas de paginas com densidade de marcadores.

    REGIOES, no plural: em Cotidiano o sumario do manual (p452-463) e uma
    regiao separada do corpo do manual (p467-543), e tratar so a maior
    deixaria a primeira de fora.

    A posicao da regiao no livro NAO participa da deteccao - ela e um
    resultado, nao um critario.
    """
    marcadas: list[frozenset[str]] = []
    for text in page_texts:
        found = page_signals(text)
        fortes = found & _STRONG_PAGE_SIGNALS
        denso = (
            len(_ALTERNATIVE.findall(_fold(text))) >= _ANSWER_DENSITY_PER_PAGE
            or len(_MP_MARKER.findall(_fold(text))) >= _ANSWER_DENSITY_PER_PAGE
        )
        basta = len(fortes) >= _REGION_MIN_SIGNALS or (denso and fortes)
        marcadas.append(fortes if basta else frozenset())

    regioes: list[EditorialRegion] = []
    inicio: int | None = None
    ultimo_marcado: int | None = None
    acumulado: set[str] = set()
    contagem = 0

    def fechar() -> None:
        nonlocal inicio, ultimo_marcado, acumulado, contagem
        if inicio is not None and ultimo_marcado is not None:
            paginas = ultimo_marcado - inicio + 1
            if paginas >= _REGION_MIN_PAGES:
                regioes.append(
                    EditorialRegion(
                        region_id=f"r{len(regioes) + 1}",
                        page_start=inicio + 1,
                        page_end=ultimo_marcado + 1,
                        signals=tuple(sorted(acumulado)),
                        marked_pages=contagem,
                    )
                )
        inicio = None
        ultimo_marcado = None
        acumulado = set()
        contagem = 0

    for index, fortes in enumerate(marcadas):
        if fortes:
            if inicio is None:
                inicio = index
            ultimo_marcado = index
            acumulado |= fortes
            contagem += 1
        elif inicio is not None and index - (ultimo_marcado or index) > _REGION_GAP_TOLERANCE:
            fechar()
    fechar()
    return tuple(regioes)


def classify_editorial(
    text: str,
    *,
    heading_path: Sequence[str] = (),
    page: int | None = None,
    page_count: int | None = None,
    regions: Sequence[EditorialRegion] = (),
    profiles: frozenset[str] = frozenset(),
    chunk_type: str = "PROSE",
) -> EditorialVerdict:
    """Papel editorial de um chunk. Determinista e idempotente.

    Precedencia, da evidencia mais especifica para a mais generica. Aparato de
    navegacao vem antes de gabarito porque o pontilhado e inequivoco; a regiao
    vem por ultimo porque e o sinal mais largo.
    """
    corpo = f"{' '.join(heading_path)} {text or ''}".strip()
    if not (text or "").strip():
        return EditorialVerdict(
            role="UNKNOWN",
            confidence=0.0,
            signals=(),
            vetoes=("empty_text",),
            decision_reason="EMPTY_TEXT",
            region_id=None,
            profiles=tuple(sorted(profiles)),
        )

    sinais = page_signals(corpo)
    regiao = next((r for r in regions if r.contains(page)), None)
    fracao = (page / page_count) if (page and page_count) else None

    def veredicto(role, confidence, reason, extra=()):
        return EditorialVerdict(
            role=role,
            confidence=confidence,
            signals=tuple(sorted(set(sinais) | set(extra))),
            vetoes=(),
            decision_reason=reason,
            region_id=regiao.region_id if regiao else None,
            profiles=tuple(sorted(profiles)),
        )

    # 1. Indice remissivo - cabecalho proprio, inequivoco.
    if "index_heading" in sinais:
        return veredicto("INDEX", 0.95, "INDEX_HEADING")

    # 2. Sumario - pontilhado APONTANDO para folio. O pontilhado sozinho
    #    nao basta: reprovou na amostra real em texto corrido sobre
    #    polimeros (p422 e p427 de Investigar).
    if len(_DOT_LEADER_LINE.findall(_fold(corpo))) >= 2:
        return veredicto("TABLE_OF_CONTENTS", 0.93, "DOT_LEADER_LINES", ("dot_leader_lines",))

    # 3. Aparato de referencia. Dois indicios NAO bastam - a amostra real
    #    reprovou com 185 falsos positivos numa obra so: conteudo legitimo
    #    cita fonte em legenda de figura e em atividade ("Disponivel em:",
    #    "Acesso em:"). Exige o CABECALHO proprio, ou densidade alta DENTRO
    #    de regiao editorial.
    ocorrencias = len(_REFERENCE.findall(_fold(corpo)))
    if _REFERENCE_HEADING.search(_fold(corpo)):
        return veredicto("REFERENCES", 0.90, "REFERENCE_HEADING")
    if ocorrencias >= 3 and regiao is not None:
        return veredicto("REFERENCES", 0.78, "REFERENCE_DENSITY_IN_REGION")

    # 4. Front matter - sinal de catalogacao MAIS estar no inicio. A posicao
    #    e auxiliar: sozinha nao decide nada, e sem ela o papel nao se
    #    sustenta (o mesmo texto no meio do livro e outra coisa).
    if "front_matter_apparatus" in sinais:
        if fracao is not None and fracao <= _FRONT_MATTER_MAX_FRACTION:
            return veredicto("FRONT_MATTER", 0.80, "CATALOGUING_AT_FRONT", ("position",))

    # 5. Gabarito.
    #
    #    DENTRO de regiao, qualquer um dos dois sinais basta.
    #
    #    FORA de regiao, so o abridor EXPLICITO conta ("Resposta pessoal",
    #    "Gabarito", "Resolucoes e comentarios"). "Alternativa X" sozinha
    #    reprovou na amostra real: dos 213 casos de SuperAcao fora de regiao,
    #    cerca de 2 em 8 eram gabarito - os outros eram conteudo e exercicio
    #    do aluno (p137 sobre PMMA, p373 sobre nanoparticulas, p388 sobre
    #    corrosao). O indicio fica REGISTRADO e o chunk permanece ELEGIVEL.
    if regiao is not None and {"answer_opener", "alternative_answer"} & sinais:
        return veredicto("ANSWER_KEY", 0.88, "ANSWER_SIGNAL_IN_REGION")
    if "answer_opener" in sinais:
        return veredicto("ANSWER_KEY", 0.74, "ANSWER_OPENER_OUTSIDE_REGION")
    if "alternative_answer" in sinais:
        return veredicto(
            "UNKNOWN", 0.40, "ALTERNATIVE_SIGNAL_OUTSIDE_REGION"
        )

    # 6. Orientacao ao professor - separada de gabarito de proposito: a
    #    regiao contem didatica que nao e resposta.
    #
    #    FORA de regiao o papel exclui demais: a amostra real mostrou
    #    atividade do aluno pegando o sinal. Entao fora de regiao a evidencia
    #    e registrada e o chunk fica UNKNOWN - que e ELEGIVEL. A assimetria
    #    manda: nao esconder conteudo por indicio fraco.
    if "teacher_instruction" in sinais:
        if regiao is not None:
            return veredicto("TEACHER_GUIDE", 0.82, "TEACHER_SIGNAL_IN_REGION")
        return veredicto("UNKNOWN", 0.40, "TEACHER_SIGNAL_OUTSIDE_REGION")

    # 7. SEM sinal proprio, mas DENTRO de regiao. E o caso que nenhum
    #    detector por chunk pegaria - 34% a 66% dos chunks do fim de cada
    #    livro. O papel vem da regiao, e a confianca e menor por isso.
    if regiao is not None:
        if "dot_leaders" in regiao.signals and len(regiao.signals) == 1:
            return veredicto("TABLE_OF_CONTENTS", 0.60, "region_only_toc")
        resposta = {"answer_opener", "alternative_answer"} & set(regiao.signals)
        if resposta:
            return veredicto("ANSWER_KEY", 0.62, "region_only_answer_key")
        return veredicto("BACK_MATTER", 0.55, "region_only_back_matter")

    return EditorialVerdict(
        role="CONTENT",
        confidence=0.70,
        signals=tuple(sorted(sinais)),
        vetoes=(),
        decision_reason="NO_EDITORIAL_SIGNAL",
        region_id=None,
        profiles=tuple(sorted(profiles)),
    )
