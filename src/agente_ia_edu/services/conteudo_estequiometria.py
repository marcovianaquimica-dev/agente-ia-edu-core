"""O QUE O ASSESSOR ENSINA SOBRE ESTEQUIOMETRIA.

POR QUE ISTO E CODIGO
======================
Mesmo motivo de `conteudo_balanceamento`: sendo dado estruturado no
repositorio, cada numero entra na suite. A diferenca e o verificador - la e
contagem de atomos (`chemistry_balance`), aqui e aritmetica de massas
(`massa_molar`). Nenhum valor abaixo foi escrito a mao duas vezes: a suite
refaz todas as contas e derruba o conteudo antes do aluno.

Enquanto o sistema so PERGUNTA quimica, um enunciado errado o aluno contesta.
Quando ele ENSINA, uma conta errada o aluno decora.

O QUE ESTA AQUI, E O QUE NAO ESTA
==================================
Por micro-habilidade: objetivo, pre-requisitos, erro esperado, explicacao,
exemplo resolvido e verificacao.

As PERGUNTAS GUIADAS nao estao aqui - sao `investigacao_do_erro`. A PRATICA
COM APOIO tambem nao - e `itens_guiados`. O CRITERIO DE RETIRADA DE APOIO e
`escada_de_apoio`. Duplicar qualquer um deles no material criaria duas
versoes da mesma coisa, divergindo na primeira correcao.

MICROINTERVENCOES, NAO CAPITULOS
=================================
Cada secao e curta de proposito. Quem errou a leitura de uma formula nao
precisa de uma aula de estequiometria: precisa de tres frases e de uma conta
feita ate o fim. Texto longo numa tela de recuperacao nao e lido.

A ESTRUTURA DO EXEMPLO E SEQUENCIAL
====================================
`STEP_SEQUENCE` em vez de um paragrafo com a conta no meio: rotulo, conta,
resultado, e a fala que diz por que aquele passo existe. Nada essencial
depende de imagem, e os subscritos sao os de verdade (NH3 escrito com digito
comum ensina a notacao errada - ha teste).

NENHUM TEXTO ACUSA O ALUNO
===========================
"Erro esperado" descreve o ERRO, nunca a pessoa: "confunde indice com
coeficiente" fala do erro, "o aluno e confuso" fala de quem o cometeu. Ha
teste varrendo "voce falhou", "deficiencia", "nao conseguiu".

SEM IA
=======
Nada aqui e gerado por modelo. A adaptacao de linguagem por IA, se vier, vem
por cima disto - nunca como unica fonte de verdade quimica.
"""

from __future__ import annotations

from agente_ia_edu.services.grafo_estequiometria import (
    CONTEUDO,
    GRAFO,
    LEITURA_FORMULA,
    MASSA_MOL,
    MASSA_MOLAR,
    PROPORCAO,
)

CONTENT_CODE = CONTEUDO

MATERIAL = {
    "title": "Estequiometria: da fórmula até a massa",
    "description": "As quatro etapas que todo cálculo estequiométrico "
                   "atravessa — ler a fórmula, calcular a massa molar, "
                   "converter massa em mol e usar a proporção da equação.",
    "material_kind": "THEORY",
    "content_code": CONTENT_CODE,
    "introduction": "Um cálculo de estequiometria quase nunca trava inteiro. "
                    "Ele trava numa etapa. Cada parte aqui é curta e resolve "
                    "uma delas — comece pela que estiver travando.",
    "summary": "Fórmula → massa molar → mol → proporção → massa. Cada seta é "
               "uma conta só, e saber qual delas está falhando vale mais do "
               "que refazer o problema inteiro.",
}


# ---------------------------------------------------------------------------
# AS MICRO-HABILIDADES
#
# A ordem e a do grafo: ler a formula sustenta massa molar, que sustenta a
# conversao massa-mol. Ensinar massa molar a quem le 1 H no NH3 e falar do
# telhado com quem ainda nao tem parede.
# ---------------------------------------------------------------------------

MICRO_HABILIDADES: dict[str, dict] = {
    LEITURA_FORMULA: {
        "objetivo": "Ler numa fórmula quantos átomos de cada elemento ela "
                    "representa.",
        "erro_esperado": "Confundir o índice com o coeficiente, ou ignorar o "
                         "índice que vem depois de um parêntese — em Ca(OH)₂ "
                         "são dois oxigênios, e não um.",
        "explicacao": "O número pequeno depois de um símbolo é o ÍNDICE: ele "
                      "conta os átomos daquele elemento dentro da molécula. "
                      "Símbolo sem índice quer dizer um átomo.\n\n"
                      "Quando o índice vem depois de um parêntese, ele "
                      "multiplica tudo o que está dentro dele.",
        "exemplo": {
            "titulo": "Lendo NH₃ e Ca(OH)₂",
            "passos": [
                {"rotulo": "Em NH₃, o nitrogênio",
                 "conta": "N sem índice",
                 "resultado": "1 átomo de N",
                 "fala": "Símbolo sem número embaixo significa um átomo só."},
                {"rotulo": "Em NH₃, o hidrogênio",
                 "conta": "H com índice 3",
                 "resultado": "3 átomos de H",
                 "fala": "O 3 está colado no H, então ele conta hidrogênios — "
                         "não moléculas."},
                {"rotulo": "Em Ca(OH)₂, o oxigênio",
                 "conta": "1 O dentro do parêntese × 2",
                 "resultado": "2 átomos de O",
                 "fala": "O índice fora do parêntese multiplica tudo o que "
                         "está dentro: dois O e dois H."},
            ],
        },
        "verificacao": "Dada uma fórmula nova, dizer quantos átomos de cada "
                       "elemento ela tem — inclusive com parêntese.",
    },

    MASSA_MOLAR: {
        "objetivo": "Calcular a massa molar de uma substância a partir da "
                    "fórmula e das massas atômicas.",
        "erro_esperado": "Somar as massas atômicas sem multiplicar pelo "
                         "índice — em NH₃, usar 14 + 1 em vez de 14 + 3×1.",
        "explicacao": "Massa molar é quanto pesa um mol da substância, em "
                      "g/mol. Ela é a soma das contribuições de cada "
                      "elemento, e a contribuição de um elemento é a massa "
                      "atômica dele VEZES o índice.\n\n"
                      "Nenhum elemento fica de fora, e nenhum entra uma vez "
                      "só quando a fórmula pede três.",
        "exemplo": {
            "titulo": "Massa molar do NH₃, com N = 14 g/mol e H = 1 g/mol",
            "passos": [
                {"rotulo": "Passo 1 — ler a fórmula",
                 "conta": "NH₃",
                 "resultado": "1 N e 3 H",
                 "fala": "Antes de somar massas é preciso saber quantos "
                         "átomos de cada elemento existem."},
                {"rotulo": "Passo 2 — contribuição do nitrogênio",
                 "conta": "1 × 14",
                 "resultado": "14 g/mol",
                 "fala": "Um átomo de nitrogênio, massa atômica 14."},
                {"rotulo": "Passo 3 — contribuição dos hidrogênios",
                 "conta": "3 × 1",
                 "resultado": "3 g/mol",
                 "fala": "Três hidrogênios de 1 g/mol cada. É aqui que o "
                         "índice entra na conta."},
                {"rotulo": "Passo 4 — somar",
                 "conta": "14 + 3",
                 "resultado": "17 g/mol",
                 "fala": "Essa é a massa de um mol de NH₃."},
            ],
        },
        "verificacao": "Calcular a massa molar de uma substância nova com as "
                       "massas atômicas dadas, multiplicando cada uma pelo "
                       "seu índice.",
    },

    MASSA_MOL: {
        "objetivo": "Converter massa em quantidade de matéria, e quantidade "
                    "de matéria em massa.",
        "erro_esperado": "Inverter a conta — multiplicar pela massa molar "
                         "quando o caminho pedia dividir, ou o contrário.",
        "explicacao": "A massa molar é a ponte entre gramas e mol, e ela "
                      "funciona nos dois sentidos.\n\n"
                      "De massa para mol: DIVIDA pela massa molar. De mol "
                      "para massa: MULTIPLIQUE. Uma forma de conferir é olhar "
                      "a unidade do resultado — se a conta devia dar mol e "
                      "deu grama, o caminho foi o contrário.",
        "exemplo": {
            "titulo": "Os dois sentidos, com a água (M = 18 g/mol)",
            "passos": [
                {"rotulo": "Passo 1 — massa molar da água",
                 "conta": "2 × 1 + 16",
                 "resultado": "18 g/mol",
                 "fala": "Dois hidrogênios e um oxigênio."},
                {"rotulo": "Passo 2 — de grama para mol",
                 "conta": "36 ÷ 18",
                 "resultado": "2 mol",
                 "fala": "36 g é o dobro do que um mol pesa, então são dois "
                         "mol."},
                {"rotulo": "Passo 3 — de mol para grama",
                 "conta": "0,5 × 18",
                 "resultado": "9 g",
                 "fala": "No sentido contrário a conta se inverte: metade de "
                         "um mol pesa metade."},
            ],
        },
        "verificacao": "Ir de gramas a mol e de mol a gramas numa substância "
                       "nova, sem trocar o sentido da conta.",
    },

    PROPORCAO: {
        "objetivo": "Usar os coeficientes da equação como proporção entre as "
                    "quantidades de matéria.",
        "erro_esperado": "Levar a quantidade de uma substância direto para a "
                         "outra, sem aplicar a proporção dos coeficientes — "
                         "em N₂ + 3 H₂ → 2 NH₃, tratar 0,5 mol de N₂ como "
                         "0,5 mol de NH₃.",
        "explicacao": "Os números na frente das fórmulas dizem em que "
                      "PROPORÇÃO as substâncias participam — e essa "
                      "proporção vale entre quantidades de matéria, não "
                      "entre massas.\n\n"
                      "Por isso o caminho de um problema de massa a massa "
                      "tem três etapas, e a do meio é a que costuma "
                      "desaparecer: massa → mol → proporção → massa.",
        "exemplo": {
            "titulo": "N₂ + 3 H₂ → 2 NH₃: quanta amônia sai de 14,0 g de N₂?",
            "passos": [
                {"rotulo": "Passo 1 — massa para mol",
                 "conta": "14,0 ÷ 28,0",
                 "resultado": "0,5 mol de N₂",
                 "fala": "A massa molar do N₂ é 28,0 g/mol, então 14,0 g são "
                         "meio mol."},
                {"rotulo": "Passo 2 — proporção da equação",
                 "conta": "0,5 × 2 ÷ 1",
                 "resultado": "1,0 mol de NH₃",
                 "fala": "Saem 2 mol de NH₃ para cada 1 mol de N₂. Esta é a "
                         "etapa que muda o número — pular aqui levaria a "
                         "8,50 g no fim."},
                {"rotulo": "Passo 3 — mol para massa",
                 "conta": "1,0 × 17,0",
                 "resultado": "17,0 g de NH₃",
                 "fala": "A massa molar do NH₃ é 17,0 g/mol."},
            ],
        },
        "verificacao": "Resolver um problema de massa a massa passando pelas "
                       "três etapas, sem saltar a proporção.",
    },
}

# Os pre-requisitos vem do GRAFO, nao sao redigitados: duas listas da mesma
# coisa divergiriam na primeira correcao, e a do material seria a errada,
# porque e a que ninguem consulta.
for _skill, _dados in MICRO_HABILIDADES.items():
    _dados["prerequisitos"] = tuple(GRAFO.prerequisitos(_skill))


def passos_do_exemplo(skill: str) -> list[dict]:
    """O exemplo daquela habilidade como UM bloco sequencial.

    Um bloco por passo pareceria quatro exemplos; a tela precisa revelar os
    passos em ordem, e para isso eles tem de viajar juntos. Mesma escolha que
    `conteudo_balanceamento.blocos_do_exemplo` ja fez.

    `STEP_SEQUENCE` e um tipo novo porque o exemplo de balanceamento mostra
    contagem de atomos dos dois lados da seta, e este mostra uma conta por
    etapa. Reaproveitar `SOLVED_EXAMPLE` faria a tela desenhar uma tabela de
    atomos vazia debaixo de cada passo.
    """
    exemplo = MICRO_HABILIDADES[skill]["exemplo"]
    return [{
        "block_type": "STEP_SEQUENCE",
        "position": 2,
        "title": exemplo["titulo"],
        "body": None,
        "metadata": {"skill": skill, "passos": list(exemplo["passos"])},
    }]


def _secao(posicao: int, skill: str) -> dict:
    dados = MICRO_HABILIDADES[skill]
    return {
        "position": posicao,
        "section_type": "SECTION",
        "skill": skill,
        "title": GRAFO.rotulo(skill),
        "body": None,
        "content_code": CONTENT_CODE,
        "blocks": [
            {
                "block_type": "DEFINITION",
                "position": 1,
                "title": dados["objetivo"],
                "body": dados["explicacao"],
                "metadata": {"skill": skill},
            },
            *passos_do_exemplo(skill),
            {
                "block_type": "CALLOUT",
                "position": 3,
                "title": "O ponto onde isso costuma escapar",
                "body": dados["erro_esperado"],
                "metadata": {"skill": skill},
            },
        ],
    }


# A ORDEM E A DO GRAFO, e nao a de escrita: uma secao de massa molar antes da
# leitura de formula seria o telhado antes da parede. Ha teste conferindo que
# todo pre-requisito presente aparece antes de quem depende dele.
_ORDEM = (LEITURA_FORMULA, MASSA_MOLAR, MASSA_MOL, PROPORCAO)

SECOES = [_secao(i, skill) for i, skill in enumerate(_ORDEM, start=1)]


def conferir() -> list[str]:
    """Refaz TODA conta afirmada pelo conteudo. Problemas, ou lista vazia.

    As contas vem de `massa_molar` e `chemistry_balance`, nunca de um literal
    repetido aqui - repetir o numero dos dois lados faria o teste conferir a
    digitacao consigo mesma.
    """
    from agente_ia_edu.services.chemistry_balance import atomos_da_formula
    from agente_ia_edu.services.massa_molar import (
        contribuicoes,
        massa_molar,
        massa_para_mol,
        mol_para_massa,
        por_proporcao,
    )

    problemas: list[str] = []

    def passo(skill: str, rotulo_parcial: str) -> dict:
        for p in MICRO_HABILIDADES[skill]["exemplo"]["passos"]:
            if rotulo_parcial.lower() in p["rotulo"].lower():
                return p
        raise KeyError(f"{skill}: nenhum passo com {rotulo_parcial!r}")

    def exige(skill: str, rotulo_parcial: str, esperado: str) -> None:
        p = passo(skill, rotulo_parcial)
        if not p["resultado"].startswith(esperado):
            problemas.append(
                f"{skill}/{p['rotulo']}: resultado {p['resultado']!r} nao "
                f"bate com {esperado!r}")

    nh3 = atomos_da_formula("NH3")
    exige(LEITURA_FORMULA, "nitrogênio", str(nh3["N"]))
    exige(LEITURA_FORMULA, "hidrogênio", str(nh3["H"]))
    exige(LEITURA_FORMULA, "Ca(OH)", str(atomos_da_formula("Ca(OH)2")["O"]))

    por_elemento = {c[0]: c[3] for c in contribuicoes("NH3")}
    exige(MASSA_MOLAR, "ler a fórmula", str(nh3["N"]))
    exige(MASSA_MOLAR, "contribuição do nitrogênio", _g(por_elemento["N"]))
    exige(MASSA_MOLAR, "contribuição dos hidrogênios", _g(por_elemento["H"]))
    exige(MASSA_MOLAR, "somar", _g(massa_molar("NH3")))

    exige(MASSA_MOL, "massa molar da água", _g(massa_molar("H2O")))
    exige(MASSA_MOL, "grama para mol", _g(massa_para_mol(36.0, "H2O")))
    exige(MASSA_MOL, "mol para grama", _g(mol_para_massa(0.5, "H2O")))

    mols_n2 = massa_para_mol(14.0, "N2")
    mols_nh3 = por_proporcao(mols_n2, 1, 2)
    exige(PROPORCAO, "massa para mol", _virgula(mols_n2))
    exige(PROPORCAO, "proporção da equação", _virgula(mols_nh3))
    exige(PROPORCAO, "mol para massa", _virgula(mol_para_massa(mols_nh3, "NH3")))

    # A conta que o distrator sugere, para que o texto que a cita nao
    # envelheca: 8,50 g e o que sai de pular a etapa da proporcao.
    sem_proporcao = mol_para_massa(mols_n2, "NH3")
    fala = passo(PROPORCAO, "proporção da equação")["fala"]
    if f"{sem_proporcao:.2f}".replace(".", ",") not in fala:
        problemas.append(
            "PROPORCAO: a fala cita um valor que nao e o de pular a etapa")

    return problemas


def _g(valor: float) -> str:
    """18.0 -> "18"; 0.5 -> "0,5"."""
    if float(valor).is_integer():
        return str(int(valor))
    return f"{valor:g}".replace(".", ",")


def _virgula(valor: float) -> str:
    return f"{valor:.1f}".replace(".", ",")


__all__ = ["CONTENT_CODE", "MATERIAL", "MICRO_HABILIDADES", "SECOES",
           "conferir", "passos_do_exemplo"]
