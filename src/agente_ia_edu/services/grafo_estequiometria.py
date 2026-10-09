"""O MAPA PEDAGOGICO DE ESTEQUIOMETRIA - o piloto vertical.

Isto e CONTEUDO, nao arquitetura. O motor esta em `grafo_pedagogico`, que nao
sabe nada de Quimica; aqui estao as nove micro-habilidades deste conteudo e o
que sustenta o que.

O QUE O MAPA DIZ, E O QUE ELE NAO DIZ
======================================
Ele diz que converter massa em mol exige saber massa molar E o conceito de
mol. Nao diz quanto o aluno sabe de cada uma - isso e medida, e vem da
evidencia.

Tambem nao diz quais questoes medem o que: a ligacao questao -> habilidade ja
existe em `pedagogical_classifications.subcontent`, e os itens da sondagem
estao em `sondagem_estequiometria`.

OS CODIGOS SAO OS QUE O BANCO JA USA, QUANDO JA EXISTEM
========================================================
Quatro subconteudos ja viviam nas classificacoes deste conteudo:

    PROPORCAO_ESTEQUIOMETRICA   6 questoes
    RELACAO_MASSA_MASSA         5
    RELACAO_MASSA_MOL           5
    RELACAO_MOL_MOL             4

Eles entram com o nome que ja tinham. Renomea-los para um padrao mais bonito
desligaria 20 questoes ja classificadas do mapa - e seria trocar dado por
estetica. As habilidades que faltavam (ler a formula, massa molar, conceito
de mol, ler coeficiente) entram com codigos novos.

A ultima - INTEGRATED_STOICHIOMETRY - e o problema completo: ela nao e uma
habilidade a mais, e a que so existe quando as outras ja existem.
"""

from __future__ import annotations

from agente_ia_edu.services.grafo_pedagogico import (
    GrafoPedagogico,
    MicroHabilidade,
)

CONTEUDO = "CHEMISTRY-PHYSICAL-STOICHIOMETRY"

# Codigos que JA viviam em `pedagogical_classifications.subcontent`.
PROPORCAO = "PROPORCAO_ESTEQUIOMETRICA"
MASSA_MASSA = "RELACAO_MASSA_MASSA"
MASSA_MOL = "RELACAO_MASSA_MOL"
MOL_MOL = "RELACAO_MOL_MOL"

# Codigos novos, para as habilidades que ninguem tinha nomeado ainda.
LEITURA_FORMULA = "LEITURA_DE_FORMULA"
MASSA_MOLAR = "MASSA_MOLAR"
CONCEITO_MOL = "CONCEITO_DE_MOL"
LEITURA_COEFICIENTE = "LEITURA_DE_COEFICIENTE"
INTEGRADO = "ESTEQUIOMETRIA_INTEGRADA"

GRAFO = GrafoPedagogico(
    conteudo=CONTEUDO,
    habilidades=[
        MicroHabilidade(
            code=LEITURA_FORMULA,
            label="Leitura de fórmulas químicas",
            objetivo="Ler numa fórmula quantos átomos de cada elemento ela "
                     "representa."),
        MicroHabilidade(
            code=CONCEITO_MOL,
            label="O que é um mol",
            objetivo="Entender mol como uma contagem de partículas, e não "
                     "como uma unidade de massa."),
        MicroHabilidade(
            code=LEITURA_COEFICIENTE,
            label="Leitura dos coeficientes da equação",
            objetivo="Ler numa equação balanceada a proporção entre as "
                     "substâncias."),
        MicroHabilidade(
            code=MASSA_MOLAR,
            label="Massa molar",
            objetivo="Calcular a massa molar de uma substância a partir da "
                     "fórmula e das massas atômicas.",
            prerequisitos=(LEITURA_FORMULA,)),
        MicroHabilidade(
            code=MASSA_MOL,
            label="Conversão entre massa e quantidade de matéria",
            objetivo="Converter massa em mol, e mol em massa, usando a massa "
                     "molar.",
            prerequisitos=(MASSA_MOLAR, CONCEITO_MOL)),
        MicroHabilidade(
            code=PROPORCAO,
            label="Proporção estequiométrica",
            objetivo="Usar os coeficientes da equação como proporção entre "
                     "as quantidades de matéria.",
            prerequisitos=(LEITURA_COEFICIENTE,)),
        MicroHabilidade(
            code=MOL_MOL,
            label="Relação mol a mol",
            objetivo="Descobrir a quantidade de matéria de uma substância a "
                     "partir da de outra.",
            prerequisitos=(PROPORCAO,)),
        MicroHabilidade(
            code=MASSA_MASSA,
            label="Relação massa a massa",
            objetivo="Ir da massa de uma substância à massa de outra, "
                     "passando por mol.",
            prerequisitos=(MASSA_MOL, PROPORCAO)),
        MicroHabilidade(
            code=INTEGRADO,
            label="Problema completo de estequiometria",
            objetivo="Resolver um problema que exige a cadeia inteira, do "
                     "enunciado ao resultado.",
            prerequisitos=(MASSA_MASSA, MOL_MOL)),
    ],
)


__all__ = ["CONTEUDO", "GRAFO", "INTEGRADO", "LEITURA_COEFICIENTE",
           "LEITURA_FORMULA", "MASSA_MASSA", "MASSA_MOL", "MASSA_MOLAR",
           "CONCEITO_MOL", "MOL_MOL", "PROPORCAO"]
