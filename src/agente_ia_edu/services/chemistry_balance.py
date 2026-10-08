"""Conservacao de atomos, conferida por contagem.

Balanceamento tem uma propriedade rara em conteudo pedagogico: a resposta e
checavel deterministicamente. Se o numero de atomos de cada elemento bate dos
dois lados, a equacao esta balanceada - nao e opiniao, e aritmetica.

Por isso nenhum item de Balanceamento do Nucleo Diagnostic Bank depende de um
LLM afirmar que a equacao esta certa. O LLM gera o item e julga a pedagogia;
a QUIMICA passa por aqui.

ESCOPO PEQUENO, DE PROPOSITO
=============================
Isto nao e um CAS quimico e nao tenta ser. Suporta exatamente o que os itens
gerados usam:

    elementos                   H, O, Na, Cl, Fe
    indices                     H2O, C6H12O6
    parenteses e aninhamento    Ca(OH)2, Al2(SO4)3, Fe(NO3)3
    hidratos                    CuSO4.5H2O
    estado fisico               H2O(l), NaCl(aq)      - ignorado
    carga                       SO4^2-                - ignorada
    coeficientes                2 H2O  ou  2H2O

FALHA FECHADA
=============
Sintaxe fora disso levanta `FormulaInvalida` em vez de devolver um palpite.
Uma equacao que o parser nao entende NAO pode ser aprovada como balanceada -
devolver "False" seria quase tao ruim quanto devolver "True", porque esconde
a diferenca entre "desbalanceada" e "nao sei ler".

Em particular, um simbolo inexistente ("Xx") e recusado: aceita-lo faria o
balanco fechar com um atomo imaginario dos dois lados.
"""

from __future__ import annotations

import re
from collections import Counter

# Tabela periodica ate o que o Ensino Medio usa. Lista explicita, e nao um
# "duas letras, primeira maiuscula", porque e justamente isso que impede
# "Xx" de passar.
ELEMENTOS = frozenset("""
H He Li Be B C N O F Ne Na Mg Al Si P S Cl Ar K Ca Sc Ti V Cr Mn Fe Co Ni Cu
Zn Ga Ge As Se Br Kr Rb Sr Y Zr Nb Mo Tc Ru Rh Pd Ag Cd In Sn Sb Te I Xe Cs
Ba La Ce Pr Nd Pm Sm Eu Gd Tb Dy Ho Er Tm Yb Lu Hf Ta W Re Os Ir Pt Au Hg Tl
Pb Bi Po At Rn Fr Ra Ac Th Pa U Np Pu Am Cm
""".split())

SETAS = ("⟶", "→", "->", "=>", "=")
_ESTADO = re.compile(r"\((?:s|l|g|aq|aq\.)\)", re.IGNORECASE)
_CARGA = re.compile(r"\^?\d*[+-]$")
_TOKEN = re.compile(r"([A-Z][a-z]?)(\d*)|(\()|(\))(\d*)")


class FormulaInvalida(ValueError):
    """O parser nao entendeu. Nunca confundir com 'desbalanceada'."""


# H₂O e H2O sao a MESMA formula - subscrito Unicode e a notacao quimica
# corrente, nao sintaxe exotica. A primeira versao so lia digito ASCII e
# rejeitou 6 de 10 itens gerados por causa disso: o fail-closed estava certo
# (nao chutou), mas o parser e que era estreito demais.
_SUBSCRITOS = str.maketrans("₀₁₂₃₄₅₆₇₈₉", "0123456789")


def _normalizar(texto: str) -> str:
    return (texto or "").translate(_SUBSCRITOS)


def atomos_da_formula(formula: str) -> dict[str, int]:
    """Conta os atomos de cada elemento numa formula."""
    bruto = (formula or "").strip()
    if not bruto:
        raise FormulaInvalida("formula vazia")

    # hidrato: CuSO4.5H2O = CuSO4 + 5 * H2O
    if "." in bruto and not bruto.endswith("."):
        total: Counter[str] = Counter()
        for parte in bruto.split("."):
            m = re.match(r"^(\d*)(.*)$", parte.strip())
            fator = int(m.group(1)) if m.group(1) else 1
            if fator <= 0:
                raise FormulaInvalida(f"fator invalido em hidrato: {parte!r}")
            for el, n in atomos_da_formula(m.group(2)).items():
                total[el] += n * fator
        return dict(total)

    limpo = _normalizar(bruto)
    limpo = _ESTADO.sub("", limpo)
    limpo = _CARGA.sub("", limpo).strip()
    if not limpo:
        raise FormulaInvalida(f"nada sobrou de {formula!r}")
    if not re.fullmatch(r"[A-Za-z0-9()]+", limpo):
        raise FormulaInvalida(f"caractere nao suportado em {formula!r}")

    pilha: list[Counter[str]] = [Counter()]
    pos = 0
    while pos < len(limpo):
        m = _TOKEN.match(limpo, pos)
        if not m or m.end() == pos:
            raise FormulaInvalida(f"nao consegui ler {formula!r} na posicao {pos}")
        pos = m.end()
        elemento, indice, abre, fecha, mult = m.groups()
        if elemento:
            if elemento not in ELEMENTOS:
                raise FormulaInvalida(f"elemento desconhecido: {elemento!r}")
            pilha[-1][elemento] += int(indice) if indice else 1
        elif abre:
            pilha.append(Counter())
        elif fecha:
            if len(pilha) == 1:
                raise FormulaInvalida(f"parentese fechado sem abrir em {formula!r}")
            grupo = pilha.pop()
            fator = int(mult) if mult else 1
            for el, n in grupo.items():
                pilha[-1][el] += n * fator
    if len(pilha) != 1:
        raise FormulaInvalida(f"parentese aberto sem fechar em {formula!r}")
    if not pilha[0]:
        raise FormulaInvalida(f"nenhum atomo em {formula!r}")
    return dict(pilha[0])


def _lado(texto: str) -> dict[str, int]:
    partes = [p.strip() for p in texto.split("+") if p.strip()]
    if not partes:
        raise FormulaInvalida("lado da equacao vazio")
    total: Counter[str] = Counter()
    for parte in partes:
        m = re.match(r"^([+-]?\d+)?\s*(.+)$", parte)
        bruto_coef, formula = m.group(1), m.group(2)
        coeficiente = int(bruto_coef) if bruto_coef else 1
        if coeficiente <= 0:
            raise FormulaInvalida(f"coeficiente invalido: {bruto_coef!r}")
        for el, n in atomos_da_formula(formula).items():
            total[el] += n * coeficiente
    return dict(total)


def ler_equacao(equacao: str) -> tuple[dict[str, int], dict[str, int]]:
    """Devolve (atomos a esquerda, atomos a direita)."""
    texto = (equacao or "").strip()
    seta = next((s for s in SETAS if s in texto), None)
    if seta is None:
        raise FormulaInvalida(f"equacao sem seta de reacao: {equacao!r}")
    esquerda, _, direita = texto.partition(seta)
    return _lado(esquerda), _lado(direita)


def equacao_balanceada(equacao: str) -> bool:
    """True se cada elemento tem a mesma contagem dos dois lados.

    Levanta `FormulaInvalida` quando nao consegue ler - de proposito. Devolver
    False esconderia a diferenca entre "esta desbalanceada" e "nao sei ler
    isto", e a segunda nunca pode virar aprovacao.
    """
    esquerda, direita = ler_equacao(equacao)
    return esquerda == direita


def diferenca_de_atomos(equacao: str) -> dict[str, tuple[int, int]]:
    """Quais elementos nao fecham, e por quanto. Serve para a justificativa
    que o item mostra ao aluno depois - e para o relatorio de rejeicao."""
    esquerda, direita = ler_equacao(equacao)
    return {el: (esquerda.get(el, 0), direita.get(el, 0))
            for el in set(esquerda) | set(direita)
            if esquerda.get(el, 0) != direita.get(el, 0)}


__all__ = ["FormulaInvalida", "atomos_da_formula", "ler_equacao",
           "equacao_balanceada", "diferenca_de_atomos", "ELEMENTOS"]
