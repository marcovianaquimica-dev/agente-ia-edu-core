"""O que o Assessor Pedagogico ENSINA sobre balanceamento de equacoes.

POR QUE ISTO E CODIGO, E NAO UMA LINHA NO BANCO
================================================
Ate aqui o sistema so PERGUNTAVA quimica. A partir deste bloco ele ensina - e
ensinar errado e pior que nao ensinar: um enunciado errado o aluno contesta,
uma explicacao errada ele decora.

Sendo dado estruturado no codigo, o conteudo entra na suite: cada equacao
citada passa pelo mesmo verificador deterministico que ja aprova os itens do
banco diagnostico (`chemistry_balance`, contagem de atomos). Um erro de
quimica aqui derruba a suite, nao chega ao aluno.

NENHUMA ARQUITETURA NOVA DE CONTEUDO
=====================================
Isto nao e um segundo modelo de material. O destino e
TheoryMaterial / TheoryMaterialVersion / MaterialSection / MaterialBlock, da
PHASE 23, servidos por `GET /student/materials` da PHASE 25 - que ja garante,
por projeto, que LER UM MATERIAL NAO E EVIDENCIA DE DOMINIO. `block_type` e
livre por decisao daquela fase, e `SOLVED_EXAMPLE` ja estava na lista de
exemplos da propria docstring do modelo.

O QUE ESTA EXPLICACAO PRECISA RESOLVER
=======================================
A lacuna medida no Piloto Zero foi CONSERVACAO_DE_ATOMOS: 3 respostas, 1
certa. Quem erra conservacao nao erra "uma conta" - nao viu ainda que atomos
nao somem nem aparecem numa reacao. Por isso a ordem e esta:

    1. o que nao pode mudar numa reacao (conservacao)
    2. o que PODE mudar (coeficiente) e o que NAO pode (indice)
    3. como conferir sozinho
    4. um exemplo ate o fim

O item 2 nao e decoracao: trocar H2O por H2O2 "para fechar o oxigenio" e o
erro classico, e transforma agua em agua oxigenada. Ha teste exigindo que as
especies do ultimo passo do exemplo sejam as mesmas do primeiro.

SEM IA
=======
Nada aqui e gerado por modelo. O piloto prova a arquitetura pedagogica; a
adaptacao de linguagem por IA, se vier, vem depois e por cima disto - nunca
como unica fonte de verdade quimica.
"""

from __future__ import annotations

from agente_ia_edu.services.chemistry_balance import ler_equacao

CONTENT_CODE = "CHEMISTRY-GENERAL-BALANCING"

MATERIAL = {
    "title": "Balanceamento: por que os átomos têm de fechar",
    "description": "A ideia que sustenta todo cálculo com equações químicas: "
                   "numa reação, nenhum átomo some e nenhum aparece.",
    "material_kind": "THEORY",
    "content_code": CONTENT_CODE,
    "introduction": "Antes de calcular quantidades, a equação precisa estar "
                    "balanceada. Isto aqui é curto e vai direto ao ponto.",
    "summary": "Átomos se conservam. Para fechar a conta mudamos os números "
               "da frente (coeficientes), nunca os de baixo (índices).",
}


# -- o exemplo resolvido ----------------------------------------------------
#
# Cada passo e uma EQUACAO; a contagem mostrada ao aluno e derivada dela por
# `ler_equacao`, nao escrita a mao - texto escrito a mao envelhece sem ninguem
# notar, e aqui ele seria lido como verdade.

EXEMPLO_RESOLVIDO = {
    "titulo": "Vamos balancear esta: H₂ + O₂ → H₂O",
    "passos": [
        {
            "equacao": "H2 + O2 -> H2O",
            "fala": "Primeiro, conte os átomos dos dois lados. O hidrogênio "
                    "fecha: 2 de cada lado. O oxigênio não: entram 2 e sai 1. "
                    "Um átomo de oxigênio não pode simplesmente sumir.",
        },
        {
            "equacao": "H2 + O2 -> 2 H2O",
            "fala": "Para ter 2 oxigênios do lado direito, colocamos o 2 na "
                    "frente da água. Repare: escrevemos 2 H₂O, e não H₂O₂ — "
                    "H₂O₂ é água oxigenada, outra substância. O número da "
                    "frente diz QUANTAS moléculas; o de baixo diz do que a "
                    "molécula é feita, e esse não se mexe.",
        },
        {
            "equacao": "2 H2 + O2 -> 2 H2O",
            "fala": "Agora o oxigênio fecha, mas o hidrogênio desandou: 2 de "
                    "um lado, 4 do outro. Colocamos 2 na frente do H₂ e "
                    "conferimos de novo: 4 e 4, 2 e 2. Fechou.",
        },
    ],
}


def _contagem(equacao: str) -> dict[str, list[int]]:
    """Quantos átomos de cada elemento, de cada lado - pela contagem real."""
    esquerda, direita = ler_equacao(equacao)
    return {el: [esquerda.get(el, 0), direita.get(el, 0)]
            for el in sorted(set(esquerda) | set(direita))}


def blocos_do_exemplo() -> list[dict]:
    """O exemplo resolvido como UM bloco, com os passos no `metadata`.

    Um bloco por passo pareceria quatro exemplos; a tela precisa revelar os
    passos em sequencia, e para isso eles tem de viajar juntos.
    """
    passos = [
        {
            "equacao": p["equacao"],
            "fala": p["fala"],
            "contagem": _contagem(p["equacao"]),
        }
        for p in EXEMPLO_RESOLVIDO["passos"]
    ]
    return [{
        "block_type": "SOLVED_EXAMPLE",
        "position": 1,
        "title": EXEMPLO_RESOLVIDO["titulo"],
        "body": None,
        "metadata": {"passos": passos},
    }]


# Anexar a contagem real aos passos, para quem le EXEMPLO_RESOLVIDO direto.
for _p in EXEMPLO_RESOLVIDO["passos"]:
    _p["contagem"] = _contagem(_p["equacao"])


SECOES = [
    {
        "position": 1,
        "section_type": "SECTION",
        "title": "Numa reação, nenhum átomo some",
        "body": None,
        "content_code": CONTENT_CODE,
        "blocks": [
            {
                "block_type": "DEFINITION",
                "position": 1,
                "title": "A regra que vale sempre",
                "body": "Os átomos que entram numa reação são exatamente os "
                        "que saem. Eles se reorganizam em substâncias novas, "
                        "mas não somem nem aparecem do nada. Por isso toda "
                        "equação química precisa ter o mesmo número de átomos "
                        "de cada elemento dos dois lados da seta.",
                "metadata": None,
            },
            {
                "block_type": "CALLOUT",
                "position": 2,
                "title": "O número da frente e o número de baixo",
                "body": "Em 2 H₂O, o 2 da frente é o COEFICIENTE: são duas "
                        "moléculas de água. O 2 pequeno embaixo é o ÍNDICE: "
                        "cada molécula tem dois hidrogênios.\n\n"
                        "Para balancear, só o coeficiente muda. Mexer no "
                        "índice troca a substância: H₂O é água, H₂O₂ é água "
                        "oxigenada. São coisas diferentes, e a reação passaria "
                        "a ser outra.",
                "metadata": None,
            },
            {
                "block_type": "TEXT",
                "position": 3,
                "title": "Como conferir sozinho",
                "body": "Escolha um elemento. Conte quantos átomos dele há do "
                        "lado esquerdo e quantos há do direito, lembrando de "
                        "multiplicar o índice pelo coeficiente. Se os dois "
                        "números forem iguais para TODOS os elementos, a "
                        "equação está balanceada. Se um só não fechar, ainda "
                        "não está.",
                "metadata": None,
            },
        ],
    },
    {
        "position": 2,
        "section_type": "SECTION",
        "title": "Um exemplo até o fim",
        "body": None,
        "content_code": CONTENT_CODE,
        "blocks": blocos_do_exemplo(),
    },
]


def equacoes_citadas() -> list[tuple[str, bool]]:
    """Toda equacao do conteudo, com o estado que o conteudo AFIRMA dela.

    E esta lista que a suite confere por contagem de atomos. Uma equacao que
    nao aparecer aqui e uma que ninguem conferiu - por isso o exemplo nao
    escreve equacao no corpo do texto, so nos passos.
    """
    saida: list[tuple[str, bool]] = []
    passos = EXEMPLO_RESOLVIDO["passos"]
    for i, p in enumerate(passos):
        # So o ultimo passo esta balanceado - essa e a historia do exemplo.
        saida.append((p["equacao"], i == len(passos) - 1))
    return saida
