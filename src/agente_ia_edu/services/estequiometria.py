"""Estequiometria que PYTHON calcula - e que nenhum modelo precisa opinar.

    massa -> mol -> (proporcao dos coeficientes) -> mol -> massa

Em Balanceamento, a aritmetica dispensavel de LLM era a conservacao de atomos
(`chemistry_balance`). Em Estequiometria e mais: massa molar, proporcao molar
e o VALOR NUMERICO da resposta. Um item cuja conta o verificador consegue
refazer nao precisa de um segundo modelo concordando.

A REGRA E A MESMA
=================
FAIL-CLOSED. "Nao sei ler" nunca vira aprovacao. Um item cujo calculo este
modulo nao reproduz vai para REQUIRES_REVIEW, nao para AI_VERIFIED. Um banco
menor e confiavel vale mais que um grande que ninguem conferiu - e num
DIAGNOSTICO vale ainda mais, porque a saida nao e uma nota: e "o aluno esta
pronto".

O ERRO QUE ESTE MODULO EXISTE PARA PEGAR
=========================================
Equacao nao balanceada. A conta de regra de tres fecha perfeitamente sobre
coeficientes errados, e o resultado parece correto - inclusive para um segundo
LLM, que refaz a mesma regra de tres sobre a mesma equacao errada. Por isso
`proporcao_molar` RECUSA equacao desbalanceada em vez de so lê-la.
"""

from __future__ import annotations

import re

from agente_ia_edu.services.chemistry_balance import (
    FormulaInvalida,
    atomos_da_formula,
    equacao_balanceada,
)

SETAS = ("->", "→", "=>", "⟶")


class DadosInsuficientes(ValueError):
    """Falta dado para fechar a conta, ou o dado e impossivel.

    Separado de `FormulaInvalida` de proposito: uma coisa e nao entender a
    quimica, outra e entender e faltar numero. As duas rejeitam o item, mas o
    relatorio de rejeicao precisa dizer qual das duas foi.
    """


# Massas atomicas da IUPAC, arredondadas como o Ensino Medio usa. Nao e a
# tabela inteira: so os elementos que aparecem em estequiometria escolar. Um
# elemento fora desta tabela faz `massa_molar` RECUSAR - preferivel a chutar.
MASSAS_ATOMICAS = {
    "H": 1.008, "He": 4.003, "Li": 6.94, "Be": 9.012, "B": 10.81,
    "C": 12.011, "N": 14.007, "O": 15.999, "F": 18.998, "Ne": 20.180,
    "Na": 22.990, "Mg": 24.305, "Al": 26.982, "Si": 28.085, "P": 30.974,
    "S": 32.06, "Cl": 35.45, "Ar": 39.948, "K": 39.098, "Ca": 40.078,
    "Sc": 44.956, "Ti": 47.867, "V": 50.942, "Cr": 51.996, "Mn": 54.938,
    "Fe": 55.845, "Co": 58.933, "Ni": 58.693, "Cu": 63.546, "Zn": 65.38,
    "Br": 79.904, "Ag": 107.868, "I": 126.904, "Ba": 137.327,
    "Pt": 195.084, "Au": 196.967, "Hg": 200.592, "Pb": 207.2,
    "Sn": 118.710, "Li7": 7.016,
}


def massa_molar(formula: str) -> float:
    """Massa molar em g/mol, a partir da contagem de atomos.

    Reusa o parser de `chemistry_balance`, entao aceita parenteses, hidratos e
    subscrito Unicode, e recusa elemento inexistente pelo mesmo motivo: um
    atomo imaginario fecharia a conta com massa inventada.
    """
    atomos = atomos_da_formula(formula)
    if not atomos:
        raise FormulaInvalida(f"formula sem atomos: {formula!r}")
    total = 0.0
    for elemento, quantos in atomos.items():
        massa = MASSAS_ATOMICAS.get(elemento)
        if massa is None:
            raise FormulaInvalida(
                f"massa atomica desconhecida para {elemento!r} - nao vou chutar")
        total += massa * quantos
    return total


# Estado fisico: (s), (l), (g), (aq). Faz parte de como a quimica se escreve,
# e NAO identifica a especie - `O2(g)` e `O2` sao o mesmo gas. Sem remover
# isto, o verificador respondia "O2 nao esta na equacao: [..., 'O2(g)']" e
# rejeitava quimica correta: 4 dos 16 primeiros itens cairam assim. O
# fail-closed estava certo em recusar o que nao entendia; estreito demais era
# o parser.
_ESTADO_FISICO = re.compile(r"\((?:s|l|g|aq|aq\.)\)\s*$", re.IGNORECASE)


def especie_canonica(formula: str) -> str:
    """A formula sem estado fisico, para comparar especies."""
    return _ESTADO_FISICO.sub("", (formula or "").strip()).strip()


def _separar_termo(termo: str) -> tuple[int, str]:
    """"2 H2O(l)" -> (2, "H2O").  "H2O" -> (1, "H2O")."""
    t = (termo or "").strip()
    if not t:
        raise FormulaInvalida("termo vazio na equacao")
    m = re.match(r"^(\d+)\s*(.+)$", t)
    if m:
        return int(m.group(1)), especie_canonica(m.group(2))
    return 1, especie_canonica(t)


def proporcao_molar(equacao: str) -> dict[str, int]:
    """Coeficiente de cada especie. RECUSA equacao desbalanceada.

    Recusar e o ponto. A regra de tres fecha perfeitamente sobre coeficientes
    errados, e o numero que sai parece certo - para o gerador, para o aluno, e
    tambem para um segundo LLM, que refaria a mesma conta sobre a mesma
    equacao errada. Aqui a conta so comeca se a quimica fechar.
    """
    texto = (equacao or "").strip()
    seta = next((s for s in SETAS if s in texto), None)
    if seta is None:
        raise FormulaInvalida(f"equacao sem seta: {equacao!r}")

    if not equacao_balanceada(texto):
        raise FormulaInvalida(
            f"equacao nao balanceada - a proporcao molar dela nao vale: {equacao!r}")

    saida: dict[str, int] = {}
    for lado in texto.split(seta):
        for termo in lado.split("+"):
            if not termo.strip():
                continue
            coef, especie = _separar_termo(termo)
            # Especie repetida nos dois lados (raro, mas possivel): somar
            # esconderia o fato. Mantem a primeira e deixa o verificador
            # estrutural reclamar, se for o caso.
            saida.setdefault(especie, coef)
    if not saida:
        raise FormulaInvalida(f"nenhuma especie lida em {equacao!r}")
    return saida


def _coeficiente(proporcao: dict[str, int], especie: str) -> int:
    coef = proporcao.get(especie_canonica(especie))
    if coef is None:
        raise DadosInsuficientes(
            f"{especie!r} nao esta na equacao: {sorted(proporcao)}")
    if coef <= 0:
        raise DadosInsuficientes(f"coeficiente invalido para {especie!r}: {coef}")
    return coef


def resolver_mol_mol(equacao: str, *, de: str, para: str, mols: float) -> float:
    """n(para) = n(de) x coef(para) / coef(de)."""
    if mols is None or mols < 0:
        raise DadosInsuficientes(f"quantidade de materia invalida: {mols!r}")
    proporcao = proporcao_molar(equacao)
    return mols * _coeficiente(proporcao, para) / _coeficiente(proporcao, de)


def resolver_massa_massa(equacao: str, *, de: str, para: str, massa: float) -> float:
    """massa -> mol -> proporcao -> mol -> massa. O caminho completo."""
    if massa is None or massa < 0:
        raise DadosInsuficientes(f"massa invalida: {massa!r}")
    mm_de = massa_molar(de)
    if mm_de <= 0:
        raise DadosInsuficientes(f"massa molar nao positiva para {de!r}")
    mols_de = massa / mm_de
    mols_para = resolver_mol_mol(equacao, de=de, para=para, mols=mols_de)
    return mols_para * massa_molar(para)


__all__ = [
    "DadosInsuficientes",
    "especie_canonica",
    "MASSAS_ATOMICAS",
    "massa_molar",
    "proporcao_molar",
    "resolver_mol_mol",
    "resolver_massa_massa",
]
