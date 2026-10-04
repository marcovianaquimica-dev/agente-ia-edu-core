"""NUCLEO DIAGNOSTIC BANK v1 - itens proprios para microdiagnostico.

Por que existe: o acervo oficial (ENEM + extraidas) tem 5 questoes utilizaveis
de Estequiometria e NENHUMA que avalie balanceamento de equacoes. Sem o
pre-requisito nao ha microdiagnostico, e sem microdiagnostico nao ha Piloto
Zero. Confirmado lexical e semanticamente em 112 questoes.

O QUE ESTE MODULO NAO FAZ
==========================
Nao cria um segundo banco de questoes. O item diagnostico e uma
``Question``/``QuestionVersion``/``QuestionOption`` comum, com
``origin_type='GENERATED'`` - valor que ja existia no CHECK da tabela e nao
tinha nenhuma linha. Player, correcao, mapa de dominio e selecao sao os
mesmos. O que muda e a ORIGEM do item, nao a maquinaria.

ORIGEM DO ITEM x ORIGEM DA EVIDENCIA
=====================================
Sao dois eixos, e confundi-los seria um erro caro:

    origin_type = GENERATED        de onde veio o ITEM (conteudo)
    origin = MICRO_DIAGNOSTIC      de onde veio a RESPOSTA (evidencia)

Um item do Nucleo respondido numa pratica gera evidencia PRACTICE; o mesmo
item num microdiagnostico gera MICRO_DIAGNOSTIC. O banco de itens nao e o
banco de evidencia.

A MATRIZ DIAGNOSTICA
====================
Nao sao dez questoes parecidas. Cada habilidade descobre uma coisa diferente
sobre POR QUE o aluno nao esta pronto para Estequiometria:

    CONSERVACAO     ele sabe que atomos nao somem nem aparecem?
    RECONHECER      ele distingue uma equacao balanceada de uma que nao esta?
    COEFICIENTE     ele consegue achar UM coeficiente faltando?
    BALANCEAR       ele balanceia uma equacao do zero?
    MULTIPLAS       ele balanceia quando ha varias especies envolvidas?

Quem erra CONSERVACAO tem um buraco conceitual; quem acerta tudo menos
MULTIPLAS so precisa de pratica. A decisao pedagogica e diferente.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from agente_ia_edu.services.chemistry_balance import (
    FormulaInvalida,
    diferenca_de_atomos,
    equacao_balanceada,
)

CONTENT_CODE = "CHEMISTRY-GENERAL-BALANCING"
ORIGIN_TYPE = "GENERATED"
BANK_TAG = "NUCLEO_DIAGNOSTIC"

SKILL_CONSERVACAO = "CONSERVACAO_DE_ATOMOS"
SKILL_RECONHECER = "RECONHECER_BALANCEADA"
SKILL_COEFICIENTE = "COEFICIENTE_AUSENTE"
SKILL_BALANCEAR = "BALANCEAR_SIMPLES"
SKILL_MULTIPLAS = "BALANCEAR_MULTIPLAS_ESPECIES"

HABILIDADES = {
    SKILL_CONSERVACAO: "O aluno reconhece que o numero de atomos de cada "
                       "elemento se conserva numa reacao?",
    SKILL_RECONHECER: "O aluno distingue uma equacao corretamente balanceada "
                      "de uma que nao esta?",
    SKILL_COEFICIENTE: "O aluno determina um coeficiente faltante numa equacao "
                       "quase balanceada?",
    SKILL_BALANCEAR: "O aluno balanceia uma equacao simples do zero?",
    SKILL_MULTIPLAS: "O aluno balanceia uma equacao com varias especies?",
}

LETRAS = ("A", "B", "C", "D", "E")


@dataclass(frozen=True)
class ItemCandidato:
    """O que o gerador produziu. Sem a justificativa do gerador: ela nao pode
    chegar ao verificador (mesmo motivo do contrato de classificacao)."""

    diagnostic_skill: str
    diagnostic_objective: str
    difficulty: str
    stem: str
    options: dict[str, str]
    correct_answer: str
    rationale: str
    equacoes: list[str] = field(default_factory=list)
    equacoes_balanceadas: list[bool] = field(default_factory=list)
    generator_version: str = ""


@dataclass(frozen=True)
class VerificacaoDeterministica:
    """O que a aritmetica diz, sem opiniao de modelo."""

    conferiu: bool
    problemas: list[str] = field(default_factory=list)
    detalhes: dict[str, dict] = field(default_factory=dict)


def conferir_quimica(item: ItemCandidato) -> VerificacaoDeterministica:
    """Confere as equacoes do item por conservacao de atomos.

    O gerador DECLARA, para cada equacao, se ela deveria estar balanceada.
    Aqui a declaracao e conferida por contagem. Um item cuja quimica nao bate
    com o que ele mesmo afirma esta errado, por mais convincente que o texto
    seja - e e esse o tipo de erro que um segundo LLM tambem erraria.
    """
    problemas: list[str] = []
    detalhes: dict[str, dict] = {}

    if not item.equacoes:
        return VerificacaoDeterministica(
            conferiu=False,
            problemas=["o item nao declarou nenhuma equacao para conferir"])
    if len(item.equacoes) != len(item.equacoes_balanceadas):
        return VerificacaoDeterministica(
            conferiu=False,
            problemas=["o item declarou equacoes e estados em quantidades "
                       "diferentes"])

    for eq, deveria in zip(item.equacoes, item.equacoes_balanceadas):
        try:
            de_fato = equacao_balanceada(eq)
        except FormulaInvalida as exc:
            # Falha fechada: nao sei ler nao e "esta errada", e nem uma nem
            # outra pode virar aprovacao.
            problemas.append(f"nao consegui conferir {eq!r}: {exc}")
            detalhes[eq] = {"erro": str(exc)}
            continue
        detalhes[eq] = {"declarado": deveria, "de_fato": de_fato}
        if de_fato != deveria:
            falta = {}
            try:
                falta = diferenca_de_atomos(eq)
            except FormulaInvalida:
                pass
            detalhes[eq]["diferenca"] = falta
            problemas.append(
                f"{eq!r}: o item diz balanceada={deveria}, a contagem de "
                f"atomos diz {de_fato}"
                + (f" (difere em {falta})" if falta else ""))

    return VerificacaoDeterministica(conferiu=not problemas,
                                     problemas=problemas, detalhes=detalhes)


def conferir_estrutura(item: ItemCandidato) -> list[str]:
    """Checagens que nao precisam de quimica nem de modelo nenhum."""
    problemas: list[str] = []
    if item.diagnostic_skill not in HABILIDADES:
        problemas.append(f"habilidade desconhecida: {item.diagnostic_skill!r}")
    if len(item.options) != len(LETRAS):
        problemas.append(f"o item tem {len(item.options)} alternativas, "
                         f"esperado {len(LETRAS)}")
    if set(item.options) - set(LETRAS):
        problemas.append("rotulos de alternativa fora de A-E")
    if item.correct_answer not in item.options:
        problemas.append(
            f"a resposta {item.correct_answer!r} nao esta entre as alternativas")
    textos = [t.strip().lower() for t in item.options.values()]
    if len(set(textos)) != len(textos):
        problemas.append("ha alternativas repetidas - mais de uma poderia "
                         "estar certa")
    if any(not t.strip() for t in item.options.values()):
        problemas.append("ha alternativa vazia")
    if not item.stem.strip():
        problemas.append("enunciado vazio")
    # O Player nao entrega imagem. Um item proprio nunca deveria depender de
    # figura - se depende, foi erro de geracao.
    gatilhos = ("figura", "imagem", "gráfico", "grafico", "esquema abaixo",
                "tabela abaixo", "ilustra")
    baixo = item.stem.lower()
    for g in gatilhos:
        if g in baixo:
            problemas.append(f"o enunciado remete a um recurso visual: {g!r}")
            break
    return problemas


__all__ = [
    "ItemCandidato", "VerificacaoDeterministica", "conferir_quimica",
    "conferir_estrutura", "HABILIDADES", "CONTENT_CODE", "ORIGIN_TYPE",
    "BANK_TAG", "LETRAS",
    "SKILL_CONSERVACAO", "SKILL_RECONHECER", "SKILL_COEFICIENTE",
    "SKILL_BALANCEAR", "SKILL_MULTIPLAS",
]


# ---------------------------------------------------------------- vies ----

def redistribuir_gabaritos(itens: list[ItemCandidato]) -> list[ItemCandidato]:
    """Espalha a posicao da resposta correta pelas cinco letras.

    POR QUE ISTO E NECESSARIO
    =========================
    Na primeira geracao, 9 dos 14 itens aprovados tinham a resposta em "B".
    Cada item, isolado, estava correto - a quimica foi conferida por contagem
    de atomos. O defeito e do CONJUNTO: um aluno que marcasse "B" em tudo
    acertaria 64% sem saber nada de balanceamento.

    Num instrumento de diagnostico isso e pior que num teste comum, porque a
    conclusao nao e uma nota: e "o aluno esta pronto para Estequiometria".
    Vies posicional vira falso-pronto.

    A correcao nao toca na quimica. Reordena as alternativas - o mesmo item,
    com as mesmas opcoes, em outra ordem - de forma deterministica, para o
    resultado ser reproduzivel e auditavel.
    """
    saida: list[ItemCandidato] = []
    for posicao, item in enumerate(itens):
        alvo = LETRAS[posicao % len(LETRAS)]
        if item.correct_answer == alvo:
            saida.append(item)
            continue
        novas = dict(item.options)
        novas[alvo], novas[item.correct_answer] = (
            item.options[item.correct_answer], item.options[alvo])
        saida.append(ItemCandidato(
            diagnostic_skill=item.diagnostic_skill,
            diagnostic_objective=item.diagnostic_objective,
            difficulty=item.difficulty, stem=item.stem, options=novas,
            correct_answer=alvo, rationale=item.rationale,
            equacoes=item.equacoes,
            equacoes_balanceadas=item.equacoes_balanceadas,
            generator_version=item.generator_version))
    return saida


def vies_posicional(itens: list[ItemCandidato]) -> dict[str, float]:
    """Fracao de itens cuja resposta esta em cada letra."""
    if not itens:
        return {}
    total = len(itens)
    return {letra: sum(1 for i in itens if i.correct_answer == letra) / total
            for letra in LETRAS}
