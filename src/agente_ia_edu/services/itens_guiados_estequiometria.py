"""OS ITENS DA PRATICA GUIADA DE ESTEQUIOMETRIA.

POR QUE ELES PRECISARAM EXISTIR
================================
Medido em 2026-10-06: `item_para("CHEMISTRY-PHYSICAL-STOICHIOMETRY", ...)`
devolvia `None`. Havia pratica guiada escrita so para balanceamento, entao o
aluno de Estequiometria saia da sondagem e caia direto em cinco questoes
sozinho - o degrau de baixo da escada, sem ter passado pelos de cima.

ELES VEM DEPOIS DA INVESTIGACAO, E ISSO MUDA O NIVEL
=====================================================
A investigacao pergunta NH3 e Ca(OH)2; estes itens perguntam Al2(SO4)3 e
CO2. Repetir a mesma substancia faria o aluno reconhecer a resposta em vez
de refazer a conta - e o sistema leria reconhecimento como aprendizagem.

A DIFERENCA DE FORMATO
=======================
Os itens de balanceamento declaram `{equacao, balanceada}`, porque la a
verdade e contagem de atomos e o verificador reconta. Aqui a alternativa e um
numero com unidade, e a verdade e aritmetica: cada gabarito e refeito por
`massa_molar`.

A UNIDADE ENTRA NA ALTERNATIVA de proposito. "2" e "2 mol" nao sao a mesma
coisa, e quem acerta o numero com a unidade errada nao fez a conta inteira.

OS QUATRO NIVEIS DE AJUDA
==========================
Os mesmos de `itens_guiados`, e cada um reduz uma dificuldade DIFERENTE:
lembrar a ideia, dizer por onde comecar, dizer que conta fazer, descrever o
alvo. Ate o ultimo o aluno continua tendo de identificar a alternativa - ha
teste exigindo que nenhum nivel cite a letra nem reproduza o texto dela.
"""

from __future__ import annotations

from agente_ia_edu.services.grafo_estequiometria import (
    CONTEUDO,
    LEITURA_FORMULA,
    MASSA_MOL,
    MASSA_MOLAR,
    PROPORCAO,
)
from agente_ia_edu.services.itens_guiados_contrato import (
    AJUDA_ASSISTIDA,
    AJUDA_CONCEITO,
    AJUDA_ONDE_OLHAR,
    AJUDA_OPERACAO,
)

CONTENT_CODE = CONTEUDO


ITENS: list[dict] = [
    {
        "key": "GPG-EST-FORMULA-1",
        "content_code": CONTENT_CODE,
        "skill": LEITURA_FORMULA,
        "pergunta": "Quantos átomos de oxigênio há na fórmula Al₂(SO₄)₃?",
        "alternativas": {
            "A": {"texto": "4"},
            "B": {"texto": "7"},
            "C": {"texto": "12"},
            "D": {"texto": "24"},
        },
        "correta": "C",
        "ajudas": [
            {"nivel": 1, "tipo": AJUDA_CONCEITO,
             "texto": "O índice que vem depois de um parêntese multiplica "
                      "tudo o que está dentro dele — não só o último "
                      "elemento."},
            {"nivel": 2, "tipo": AJUDA_ONDE_OLHAR,
             "texto": "Olhe primeiro só o que está dentro do parêntese: "
                      "(SO₄). Quantos oxigênios há aí?"},
            {"nivel": 3, "tipo": AJUDA_OPERACAO,
             "texto": "São 4 oxigênios dentro do parêntese, e o índice de "
                      "fora é 3. A conta é uma multiplicação — e o alumínio "
                      "não entra nela."},
            {"nivel": 4, "tipo": AJUDA_ASSISTIDA,
             "texto": "Multiplique os 4 oxigênios de dentro pelo 3 que está "
                      "fora do parêntese, e procure esse número entre as "
                      "alternativas."},
        ],
        "fecho": "A ideia é essa: o índice de fora do parêntese vale para "
                 "cada elemento de dentro.",
    },
    {
        "key": "GPG-EST-MASSA-MOLAR-1",
        "content_code": CONTENT_CODE,
        "skill": MASSA_MOLAR,
        "pergunta": "Com C = 12 g/mol e O = 16 g/mol, qual é a massa molar "
                    "do CO₂?",
        "alternativas": {
            "A": {"texto": "28 g/mol"},
            "B": {"texto": "32 g/mol"},
            "C": {"texto": "44 g/mol"},
            "D": {"texto": "56 g/mol"},
        },
        "correta": "C",
        "ajudas": [
            {"nivel": 1, "tipo": AJUDA_CONCEITO,
             "texto": "Massa molar é a soma das contribuições de cada "
                      "elemento, e a contribuição de um elemento é a massa "
                      "atômica dele vezes o índice."},
            {"nivel": 2, "tipo": AJUDA_ONDE_OLHAR,
             "texto": "Comece contando os átomos: quantos carbonos e quantos "
                      "oxigênios a fórmula CO₂ tem?"},
            {"nivel": 3, "tipo": AJUDA_OPERACAO,
             "texto": "É 1 carbono e 2 oxigênios. Multiplique cada massa "
                      "atômica pelo seu índice antes de somar — o oxigênio "
                      "entra duas vezes."},
            {"nivel": 4, "tipo": AJUDA_ASSISTIDA,
             "texto": "Some a contribuição do carbono com a contribuição dos "
                      "dois oxigênios, e procure esse total entre as "
                      "alternativas.",
             },
        ],
        "fecho": "Nenhum elemento fica de fora, e nenhum entra uma vez só "
                 "quando a fórmula pede duas.",
    },
    {
        "key": "GPG-EST-MASSA-MOL-1",
        "content_code": CONTENT_CODE,
        "skill": MASSA_MOL,
        "pergunta": "A massa molar do CO₂ é 44 g/mol. Quantos mol de CO₂ "
                    "existem em 88 g?",
        "alternativas": {
            "A": {"texto": "0,5 mol"},
            "B": {"texto": "2 mol"},
            "C": {"texto": "44 mol"},
            "D": {"texto": "88 mol"},
        },
        "correta": "B",
        "ajudas": [
            {"nivel": 1, "tipo": AJUDA_CONCEITO,
             "texto": "A massa molar diz quanto pesa UM mol. Ela é a ponte "
                      "entre gramas e mol, e funciona nos dois sentidos."},
            {"nivel": 2, "tipo": AJUDA_ONDE_OLHAR,
             "texto": "Compare os dois números: 88 g é maior ou menor do que "
                      "o que um mol inteiro pesaria?"},
            {"nivel": 3, "tipo": AJUDA_OPERACAO,
             "texto": "Indo de grama para mol, o caminho é dividir pela "
                      "massa molar. Confira também a unidade: o resultado "
                      "tem de sair em mol."},
            {"nivel": 4, "tipo": AJUDA_ASSISTIDA,
             "texto": "Divida a massa dada pela massa molar e procure o "
                      "resultado, com a unidade certa, entre as alternativas."},
        ],
        "fecho": "De grama para mol, divide. De mol para grama, multiplica. "
                 "A unidade do resultado denuncia quando o sentido inverteu.",
    },
    {
        "key": "GPG-EST-PROPORCAO-1",
        "content_code": CONTENT_CODE,
        "skill": PROPORCAO,
        "pergunta": "Na equação N₂ + 3 H₂ → 2 NH₃, quantos mol de NH₃ se "
                    "formam a partir de 1,5 mol de H₂?",
        "alternativas": {
            "A": {"texto": "0,5 mol"},
            "B": {"texto": "1,0 mol"},
            "C": {"texto": "1,5 mol"},
            "D": {"texto": "3,0 mol"},
        },
        "correta": "B",
        "ajudas": [
            {"nivel": 1, "tipo": AJUDA_CONCEITO,
             "texto": "Os números na frente das fórmulas são a proporção "
                      "entre as quantidades de matéria — e quase nunca são "
                      "iguais entre si."},
            {"nivel": 2, "tipo": AJUDA_ONDE_OLHAR,
             "texto": "Olhe só os dois coeficientes que importam aqui: o do "
                      "H₂ e o do NH₃. Quanto sai de amônia para cada "
                      "hidrogênio consumido?"},
            {"nivel": 3, "tipo": AJUDA_OPERACAO,
             "texto": "A proporção é 3 de H₂ para 2 de NH₃. Monte a regra de "
                      "três com a quantidade que o enunciado deu — o "
                      "resultado é menor que ela."},
            {"nivel": 4, "tipo": AJUDA_ASSISTIDA,
             "texto": "Multiplique a quantidade dada por 2 e divida por 3, e "
                      "procure esse valor entre as alternativas."},
        ],
        "fecho": "A proporção vale entre quantidades de matéria, nunca entre "
                 "massas — é por isso que o cálculo passa pelo mol.",
    },
]


def conferir() -> list[str]:
    """Refaz os gabaritos. Devolve os problemas, ou lista vazia.

    Mesmo papel de `conferir` na sondagem e no conteudo: um erro de digitacao
    ensinaria quimica errada a quem confiou no sistema. As contas vem de
    `massa_molar` e `chemistry_balance`, nunca de um literal repetido aqui.
    """
    from agente_ia_edu.services.chemistry_balance import atomos_da_formula
    from agente_ia_edu.services.massa_molar import (
        massa_molar,
        massa_para_mol,
        por_proporcao,
    )

    problemas: list[str] = []
    por_chave = {i["key"]: i for i in ITENS}

    def exige(key: str, esperado: str) -> None:
        item = por_chave[key]
        valor = item["alternativas"][item["correta"]]["texto"]
        if not valor.startswith(esperado):
            problemas.append(
                f"{key}: gabarito {valor!r} nao bate com {esperado!r}")

    exige("GPG-EST-FORMULA-1", str(atomos_da_formula("Al2(SO4)3")["O"]))
    exige("GPG-EST-MASSA-MOLAR-1", _g(massa_molar("CO2")))
    exige("GPG-EST-MASSA-MOL-1", _g(massa_para_mol(88.0, "CO2")))
    # 3 H2 -> 2 NH3: de 1,5 mol de H2 saem 1,0 mol de NH3.
    exige("GPG-EST-PROPORCAO-1", _virgula(por_proporcao(1.5, 3, 2)))
    return problemas


def _g(valor: float) -> str:
    if float(valor).is_integer():
        return str(int(valor))
    return f"{valor:g}".replace(".", ",")


def _virgula(valor: float) -> str:
    return f"{valor:.1f}".replace(".", ",")


__all__ = ["CONTENT_CODE", "ITENS", "conferir"]
