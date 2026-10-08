"""OS ITENS DA SONDAGEM DE ESTEQUIOMETRIA - curados, e conferiveis.

POR QUE CURADOS E NAO GERADOS
==============================
A sondagem e a primeira coisa que o aluno encontra. Se ela depender de um
provedor de IA, um timeout fecha a porta de entrada do produto. E ha um risco
pior que a indisponibilidade: uma questao diagnostica com duas alternativas
defensaveis nao falha ruidosamente - ela mede errado, em silencio, e o erro
entra no mapa de dominio como se fosse evidencia.

A IA podera, depois, gerar VARIANTES destes itens. O piloto funciona sem ela.

CADA ITEM MEDE UMA HABILIDADE, E SO UMA
========================================
O item de massa molar da as massas atomicas de proposito: se o aluno
precisasse lembra-las, errar poderia significar duas coisas, e o mapa nao
saberia qual. O de proporcao da a equacao ja balanceada pelo mesmo motivo.

O item integrado e a excecao deliberada - ele exige a cadeia inteira, e esta
ali justamente para distinguir quem sabe as partes de quem sabe junta-las.

O GABARITO E CONFERIDO POR CONTA
=================================
Onde ha verdade aritmetica, `conferencia` traz a conta. `conferir()` a refaz
e falha se o gabarito nao bater - entao um erro de digitacao em "17 g/mol"
quebra o teste, e nao a aprendizagem de alguem.
"""

from __future__ import annotations

from agente_ia_edu.services.grafo_estequiometria import (
    INTEGRADO,
    LEITURA_FORMULA,
    MASSA_MOL,
    MASSA_MOLAR,
    PROPORCAO,
)
from agente_ia_edu.services.sondagem import ItemDeSondagem

ITENS: tuple[ItemDeSondagem, ...] = (
    ItemDeSondagem(
        key="SOND-EST-FORMULA-1",
        habilidade=LEITURA_FORMULA,
        enunciado="Na fórmula NH₃, quantos átomos de hidrogênio estão "
                  "representados?",
        alternativas={"A": "1", "B": "2", "C": "3", "D": "4"},
        correta="C",
        conferencia="indice de H em NH3 = 3"),

    ItemDeSondagem(
        key="SOND-EST-MASSA-MOLAR-1",
        habilidade=MASSA_MOLAR,
        # As massas atomicas vem no enunciado: sem elas, errar poderia
        # significar "nao sei calcular" ou "nao lembro o valor do N", e o
        # mapa nao saberia qual.
        enunciado="Considere N = 14 g/mol e H = 1 g/mol. Qual é a massa molar "
                  "do NH₃?",
        alternativas={"A": "15 g/mol", "B": "16 g/mol", "C": "17 g/mol",
                      "D": "18 g/mol"},
        correta="C",
        conferencia="14 + 3*1 = 17"),

    ItemDeSondagem(
        key="SOND-EST-MASSA-MOL-1",
        habilidade=MASSA_MOL,
        enunciado="A massa molar da água é 18 g/mol. Quantos mol de H₂O "
                  "existem em 36 g de água?",
        alternativas={"A": "1 mol", "B": "2 mol", "C": "18 mol", "D": "36 mol"},
        correta="B",
        conferencia="36 / 18 = 2"),

    ItemDeSondagem(
        key="SOND-EST-PROPORCAO-1",
        habilidade=PROPORCAO,
        # A equacao ja vem balanceada: balancear e outra habilidade, e de
        # outro conteudo.
        enunciado="Na equação N₂ + 3 H₂ → 2 NH₃, para cada 2 mol de NH₃ "
                  "formados, quantos mol de H₂ são consumidos?",
        alternativas={"A": "1", "B": "2", "C": "3", "D": "6"},
        correta="C",
        conferencia="coeficiente de H2 = 3 para 2 de NH3"),

    ItemDeSondagem(
        key="SOND-EST-INTEGRADO-1",
        habilidade=INTEGRADO,
        enunciado="Na equação N₂ + 3 H₂ → 2 NH₃, considere N = 14 g/mol e "
                  "H = 1 g/mol. Quantos mol de H₂ são necessários para "
                  "produzir 34 g de NH₃?",
        alternativas={"A": "1 mol", "B": "2 mol", "C": "3 mol", "D": "6 mol"},
        correta="C",
        conferencia="34 / 17 = 2 mol de NH3; 2 * 3/2 = 3 mol de H2"),
)


def conferir() -> list[str]:
    """Refaz as contas dos gabaritos. Devolve os problemas, ou lista vazia.

    Nao e paranoia: o item de massa molar tem 17 como resposta porque
    14 + 3x1 = 17, e um erro de digitacao aqui ensinaria quimica errada a
    quem confiou no sistema. Esta funcao e chamada pelo teste.
    """
    problemas: list[str] = []
    por_chave = {i.key: i for i in ITENS}

    def valor(chave: str) -> str:
        item = por_chave[chave]
        return item.alternativas[item.correta]

    # NH3: 1 N (14) + 3 H (1) = 17 g/mol
    if not valor("SOND-EST-MASSA-MOLAR-1").startswith(str(14 + 3 * 1)):
        problemas.append("SOND-EST-MASSA-MOLAR-1: 14 + 3*1 nao bate com o gabarito")
    # 36 g / 18 g.mol-1 = 2 mol
    if not valor("SOND-EST-MASSA-MOL-1").startswith(str(36 // 18)):
        problemas.append("SOND-EST-MASSA-MOL-1: 36/18 nao bate com o gabarito")
    # N2 + 3 H2 -> 2 NH3: para 2 de NH3, 3 de H2
    if not valor("SOND-EST-PROPORCAO-1").startswith("3"):
        problemas.append("SOND-EST-PROPORCAO-1: a proporcao 3:2 nao bate")
    # 34 g de NH3 / 17 g.mol-1 = 2 mol; 2 mol x (3 H2 / 2 NH3) = 3 mol
    mols_nh3 = 34 / (14 + 3 * 1)
    mols_h2 = mols_nh3 * 3 / 2
    if not valor("SOND-EST-INTEGRADO-1").startswith(str(int(mols_h2))):
        problemas.append("SOND-EST-INTEGRADO-1: a cadeia nao bate com o gabarito")
    # NH3 tem indice 3 no H
    if valor("SOND-EST-FORMULA-1") != "3":
        problemas.append("SOND-EST-FORMULA-1: o indice de H em NH3 e 3")
    return problemas


__all__ = ["ITENS", "conferir"]
