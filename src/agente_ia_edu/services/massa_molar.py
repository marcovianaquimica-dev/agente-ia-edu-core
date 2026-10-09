"""MASSA MOLAR - a aritmetica que o conteudo de Estequiometria AFIRMA.

POR QUE ISTO EXISTE
====================
`chemistry_balance` ja impede o sistema de ensinar uma equacao desbalanceada:
a suite reconta os atomos. Estequiometria afirma outra classe de coisa -
"a massa molar do NH3 e 17 g/mol", "14 g de N2 sao 0,5 mol" - e essas
afirmacoes precisavam do mesmo tratamento.

Enquanto o sistema so PERGUNTA quimica, um enunciado errado o aluno contesta.
Quando ele ENSINA, uma conta errada o aluno decora.

Entao nenhum numero do conteudo curado e escrito a mao: ele sai daqui, e a
suite refaz a conta. Um erro de digitacao em "17" derruba o teste, nao a
aprendizagem de alguem.

A TABELA E CURTA DE PROPOSITO
==============================
So os elementos que o conteudo-piloto usa. Uma tabela periodica inteira com
valores copiados as pressas daria a impressao de rigor sem a conferencia que
o rigor exige - e um elemento fora da lista levanta KeyError, que e o
comportamento certo: devolver zero ensinaria que o xenonio nao pesa.

Os valores sao os arredondamentos de livro didatico, que sao tambem os que os
enunciados do acervo usam (N = 14, H = 1, O = 16, C = 12). Usar 14,007 aqui e
14 no enunciado faria a conferencia reprovar conteudo correto.
"""

from __future__ import annotations

from agente_ia_edu.services.chemistry_balance import atomos_da_formula

# g/mol, nos arredondamentos que os enunciados do acervo usam.
MASSAS_ATOMICAS: dict[str, float] = {
    "H": 1.0,
    "C": 12.0,
    "N": 14.0,
    "O": 16.0,
    "Na": 23.0,
    "Mg": 24.0,
    "S": 32.0,
    "Cl": 35.5,
    "Ca": 40.0,
    "Fe": 56.0,
    "Cu": 63.5,
    "Zn": 65.0,
}


def contribuicoes(formula: str) -> list[tuple[str, int, float, float]]:
    """(elemento, quantos atomos, massa atomica, contribuicao), na ordem da formula.

    E esta lista - e nao o total - que o aluno precisa ver. "A massa molar do
    NH3 e 17" nao ensina nada; "1 N vale 14, 3 H valem 3, total 17" mostra de
    onde o numero vem, e e isso que ele vai conseguir refazer com outra
    substancia.

    A ORDEM E A DA FORMULA, nao a alfabetica: em H2O o aluno le o H primeiro,
    e listar o O antes faria a explicacao discordar do que esta na tela.
    """
    saida: list[tuple[str, int, float, float]] = []
    for elemento, quantos in atomos_da_formula(formula).items():
        if elemento not in MASSAS_ATOMICAS:
            raise KeyError(
                f"massa atomica de {elemento!r} nao esta na tabela deste "
                f"conteudo - acrescenta-la e decisao consciente, com teste")
        atomica = MASSAS_ATOMICAS[elemento]
        saida.append((elemento, quantos, atomica, quantos * atomica))
    return saida


def massa_molar(formula: str) -> float:
    """g/mol, pela contagem de atomos e pela tabela. Nunca por um literal."""
    return sum(c[3] for c in contribuicoes(formula))


def massa_para_mol(gramas: float, formula: str) -> float:
    """Quantos mol ha naquela massa."""
    return float(gramas) / massa_molar(formula)


def mol_para_massa(mols: float, formula: str) -> float:
    """Quanto pesa aquela quantidade de materia."""
    return float(mols) * massa_molar(formula)


def por_proporcao(mols: float, coef_de: int, coef_para: int) -> float:
    """A regra de tres dos coeficientes: de X mol de A para quantos de B.

    Esta e a etapa do meio da cadeia - a que o distrator de 8,50 g sugere que
    ficou de fora. Ela nao sabe de quimica: recebe dois coeficientes ja lidos
    da equacao balanceada, o que mantem a leitura da equacao sendo uma
    habilidade separada, que e como o grafo a descreve.
    """
    if int(coef_de) <= 0:
        raise ValueError("coeficiente de partida precisa ser positivo")
    return float(mols) * int(coef_para) / int(coef_de)


__all__ = [
    "MASSAS_ATOMICAS",
    "contribuicoes",
    "massa_molar",
    "massa_para_mol",
    "mol_para_massa",
    "por_proporcao",
]
