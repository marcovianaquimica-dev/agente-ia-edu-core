"""Os itens da PRATICA GUIADA, e a ajuda que nao entrega a resposta.

A DIFERENCA ENTRE AJUDA E RESPOSTA
===================================
Uma "dica" que diz qual alternativa marcar nao ensina - encerra. O aluno sai
com a questao certa e a duvida intacta.

Cada nivel aqui reduz uma dificuldade DIFERENTE:

    1 CONCEITO     lembrar a ideia que vale
    2 ONDE_OLHAR   dizer por onde comecar
    3 OPERACAO     dizer que conta fazer
    4 ASSISTIDA    descrever o alvo - ainda sem apontar qual e

Ate o ultimo nivel o aluno continua tendo de identificar a alternativa. Ha
teste exigindo que nenhum nivel cite a letra correta nem reproduza o texto
dela, e que `para_o_aluno` nao deixe o gabarito viajar.

POR QUE ISTO E DETERMINISTICO
==============================
A ajuda poderia vir de um modelo. Nao precisa: num conteudo onde a verdade e
contagem de atomos, as dificuldades sao conhecidas e a boa dica tambem. Um
provider podera, depois, ADAPTAR a linguagem destes niveis - o contrato (o
que cada nivel faz) continua sendo do Nucleo.

NAO E ARQUITETURA DE QUIMICA
=============================
O formato - pergunta, alternativas, N niveis de ajuda, um fecho - nao sabe
nada de balanceamento. Matematica, Fisica ou Linguagens entram com os mesmos
campos; o que e de Quimica e a CONFERENCIA (cada alternativa declara se esta
balanceada, e a suite confere por contagem).
"""

from __future__ import annotations

from agente_ia_edu.services.conteudo_balanceamento import para_exibicao

CONTENT_CODE = "CHEMISTRY-GENERAL-BALANCING"

AJUDA_CONCEITO = "CONCEITO"
AJUDA_ONDE_OLHAR = "ONDE_OLHAR"
AJUDA_OPERACAO = "OPERACAO"
AJUDA_ASSISTIDA = "ASSISTIDA"

NIVEIS_DE_AJUDA = (AJUDA_CONCEITO, AJUDA_ONDE_OLHAR, AJUDA_OPERACAO,
                   AJUDA_ASSISTIDA)


ITENS: list[dict] = [
    {
        "key": "BAL-CONSERVACAO-1",
        "content_code": CONTENT_CODE,
        "skill": "CONSERVACAO_DE_ATOMOS",
        "pergunta": "Em qual destas equações os átomos fecham dos dois lados?",
        "alternativas": {
            "A": {"equacao": "H2 + O2 -> H2O", "balanceada": False},
            "B": {"equacao": "2 H2 + O2 -> 2 H2O", "balanceada": True},
            "C": {"equacao": "H2 + O2 -> 2 H2O", "balanceada": False},
            "D": {"equacao": "2 H2 + 2 O2 -> 2 H2O", "balanceada": False},
        },
        "correta": "B",
        "ajudas": [
            {
                "nivel": 1,
                "tipo": AJUDA_CONCEITO,
                "texto": "Numa reação nenhum átomo some nem aparece. Para a "
                         "equação estar certa, o número de átomos de cada "
                         "elemento tem de ser o mesmo antes e depois da seta.",
            },
            {
                "nivel": 2,
                "tipo": AJUDA_ONDE_OLHAR,
                "texto": "Comece pelo oxigênio, que aparece em menos lugares. "
                         "Em cada opção, conte quantos átomos de O entram e "
                         "quantos saem.",
            },
            {
                "nivel": 3,
                "tipo": AJUDA_OPERACAO,
                "texto": "Não esqueça de multiplicar o índice pelo número da "
                         "frente: 2 H₂O tem 4 hidrogênios, não 2. E o "
                         "hidrogênio também precisa fechar, não só o oxigênio.",
            },
            {
                "nivel": 4,
                "tipo": AJUDA_ASSISTIDA,
                "texto": "Procure a única opção em que os dois elementos "
                         "fecham ao mesmo tempo: 4 hidrogênios de cada lado e "
                         "2 oxigênios de cada lado.",
            },
        ],
        "fecho": "A ideia é essa: conferir TODOS os elementos, um por um, "
                 "multiplicando índice pelo coeficiente.",
    },
    {
        "key": "BAL-COEFICIENTE-1",
        "content_code": CONTENT_CODE,
        "skill": "COEFICIENTE_AUSENTE",
        "pergunta": "Falta um número na frente do O₂. Qual destas fecha a conta?",
        "alternativas": {
            "A": {"equacao": "CH4 + O2 -> CO2 + 2 H2O", "balanceada": False},
            "B": {"equacao": "CH4 + 2 O2 -> CO2 + 2 H2O", "balanceada": True},
            "C": {"equacao": "CH4 + 3 O2 -> CO2 + 2 H2O", "balanceada": False},
            "D": {"equacao": "CH4 + 4 O2 -> CO2 + 2 H2O", "balanceada": False},
        },
        "correta": "B",
        "ajudas": [
            {
                "nivel": 1,
                "tipo": AJUDA_CONCEITO,
                "texto": "O número da frente diz quantas moléculas entram. "
                         "Mudá-lo não muda a substância — muda só a "
                         "quantidade.",
            },
            {
                "nivel": 2,
                "tipo": AJUDA_ONDE_OLHAR,
                "texto": "O lado direito já está pronto. Conte quantos átomos "
                         "de oxigênio SAEM: some os do CO₂ com os das duas "
                         "moléculas de água.",
            },
            {
                "nivel": 3,
                "tipo": AJUDA_OPERACAO,
                "texto": "Saem 4 oxigênios ao todo. Cada molécula de O₂ traz "
                         "dois: quantas delas são necessárias para entregar "
                         "esses 4?",
            },
            {
                "nivel": 4,
                "tipo": AJUDA_ASSISTIDA,
                "texto": "4 oxigênios divididos por 2 em cada molécula dá o "
                         "número que falta na frente do O₂.",
            },
        ],
        "fecho": "Quando um lado já está fechado, ele diz quanto o outro "
                 "precisa entregar.",
    },
]


def item_para(content_code: str, skill: str | None) -> dict | None:
    """O item guiado para aquela lacuna, ou None se o conteudo nao tem nenhum.

    Havendo item da micro-habilidade medida, e ele. Nao havendo, cai no
    primeiro item do CONTEUDO - nunca no item de outra habilidade: mandar
    quem errou conservacao praticar coeficiente faz o aluno estudar o que ele
    ja sabe, e ele percebe.

    Conteudo sem item nenhum devolve None, e quem chama segue para a pratica
    comum. Inventar pratica guiada generica seria pior que nao ter.
    """
    do_conteudo = [i for i in ITENS if i["content_code"] == content_code]
    if not do_conteudo:
        return None
    if skill:
        certo = next((i for i in do_conteudo if i["skill"] == skill), None)
        if certo is not None:
            return certo
    return do_conteudo[0]


def para_o_aluno(item: dict, *, ajudas_liberadas: int) -> dict:
    """O item como ele pode chegar ao cliente.

    O QUE NAO ESTA AQUI NAO VAZA. `correta` fica de fora, e `balanceada`
    tambem - dizer quais equacoes fecham e dizer a resposta com outras
    palavras. As ajudas vem so ate o nivel ja liberado.
    """
    liberadas = max(0, int(ajudas_liberadas or 0))
    return {
        "item_key": item["key"],
        "content_code": item["content_code"],
        "skill": item["skill"],
        "question": item["pergunta"],
        "options": [
            {"key": letra, "text": para_exibicao(alt["equacao"])}
            for letra, alt in sorted(item["alternativas"].items())
        ],
        "ajudas": [
            {"nivel": a["nivel"], "tipo": a["tipo"], "texto": a["texto"]}
            for a in item["ajudas"][:liberadas]
        ],
        "ajudas_disponiveis": len(item["ajudas"]),
    }


def conferir(item: dict, escolha: str) -> bool:
    """Acertou? A conferencia e aqui, nunca no cliente."""
    return (escolha or "").strip().upper() == item["correta"]
