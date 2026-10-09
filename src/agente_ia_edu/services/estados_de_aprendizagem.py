"""OS SETE ESTADOS DO §6 - e o unico que comprova dominio.

POR QUE ESTE MODULO EXISTE
===========================
As garantias ja existiam, espalhadas e provadas uma a uma:

    solved_unaided            separa resolver de resolver SOZINHO
    hints_used, help_requests registram a ajuda recebida
    produz_evidencia          libera so o degrau autonomo da escada
    ConversaDoAssessor        nao recebe sessao de banco

O que nao existia era a LISTA. O §6 pede que o sistema distinga sete estados,
e distinguir exige nomea-los num lugar so - senao a invariante ("entendi" nao
e dominio) e a soma de quatro garantias que ninguem le junto, e o oitavo
caminho que alguem escrever amanha nao encontra onde se encaixar.

Este modulo nao substitui nenhuma daquelas garantias. Ele as nomeia, e ha
teste cruzando com `escada_de_apoio` para que as duas nao divirjam.

OS SETE, NA ORDEM DA PROGRESSAO REAL
=====================================
    1. CONTEUDO_APRESENTADO     ele viu a explicacao
    2. COMPREENSAO_MANIFESTADA  ele disse que entendeu
    3. APLICACAO_ASSISTIDA      acertou COM ajuda
    4. APLICACAO_INDEPENDENTE   acertou SOZINHO            <- o unico que comprova
    5. DOMINIO_DEMONSTRADO      a politica concluiu dominio
    6. CONSOLIDACAO             consistencia em oportunidades diferentes
    7. RETENCAO                 recuperacao depois de um intervalo

OS TRES ULTIMOS NAO PRODUZEM EVIDENCIA, E ISSO NAO E DESCUIDO
==============================================================
Dominio, consolidacao e retencao sao CONCLUSOES sobre evidencia, nao
acontecimentos em que o aluno responde algo. Trata-los como produtores faria
a conclusao virar a propria prova - "ele domina, logo ha evidencia de que
domina". Quem conclui continua sendo `PerformanceThresholdPolicy`, a partir
do estado 4.

FAIL-CLOSED
============
Estado desconhecido nao produz evidencia. O custo dos dois erros nao e o
mesmo: nao reconhecer uma evidencia atrasa um aluno; reconhecer uma que nao
existe diz a ele que aprendeu algo que nao aprendeu.
"""

from __future__ import annotations

CONTEUDO_APRESENTADO = "CONTENT_PRESENTED"
COMPREENSAO_MANIFESTADA = "UNDERSTANDING_CLAIMED"
APLICACAO_ASSISTIDA = "ASSISTED_APPLICATION"
APLICACAO_INDEPENDENTE = "INDEPENDENT_APPLICATION"
DOMINIO_DEMONSTRADO = "MASTERY_SHOWN"
CONSOLIDACAO = "CONSOLIDATION"
RETENCAO = "RETENTION"

# A ORDEM E O CONTRATO. Ha teste percorrendo esta tupla e exigindo que ela
# seja a da progressao: um estado fora de lugar significaria que alguem o
# entendeu errado.
ESTADOS = (
    CONTEUDO_APRESENTADO,
    COMPREENSAO_MANIFESTADA,
    APLICACAO_ASSISTIDA,
    APLICACAO_INDEPENDENTE,
    DOMINIO_DEMONSTRADO,
    CONSOLIDACAO,
    RETENCAO,
)

# O UNICO. Uma constante, e nao uma comparacao espalhada, para que mudar isto
# seja uma decisao visivel - a mesma escolha de `escada_de_apoio`.
_O_QUE_COMPROVA = (APLICACAO_INDEPENDENTE,)

# O que o ALUNO le. Nunca o codigo do estado, e nunca jargao do sistema:
# "voce esta em ASSISTED_APPLICATION" nao quer dizer nada para alguem de 15
# anos, e ha teste varrendo estes textos atras de "evidencia" e "banda".
_ROTULOS = {
    CONTEUDO_APRESENTADO: "Você viu a explicação.",
    COMPREENSAO_MANIFESTADA: "Você disse que fez sentido.",
    APLICACAO_ASSISTIDA: "Você resolveu com ajuda.",
    APLICACAO_INDEPENDENTE: "Você resolveu sozinho.",
    DOMINIO_DEMONSTRADO: "Isso já está firme.",
    CONSOLIDACAO: "Você repetiu isso em ocasiões diferentes.",
    RETENCAO: "Você lembrou disso depois de um tempo.",
}


def produz_evidencia_de_dominio(estado: str | None) -> bool:
    """Este estado pode alimentar o mapa de dominio?

    Esta funcao e a invariante inteira do modulo, e e por isso que ela existe
    separada: ha teste exigindo que EXATAMENTE UM dos sete devolva True.
    Acrescentar um estado obriga a decidir.
    """
    return estado in _O_QUE_COMPROVA


def rotulo_do_estado(estado: str | None) -> str:
    """O que o aluno le sobre aquele estado."""
    return _ROTULOS.get(estado, "")


def anterior_a(estado: str | None, outro: str | None) -> bool:
    """`estado` vem antes de `outro` na progressao?

    Desconhecido nao ordena - nem antes, nem depois. Inventar uma posicao
    para o que o modulo nao conhece seria decidir pedagogia por descuido.
    """
    if estado not in ESTADOS or outro not in ESTADOS:
        return False
    return ESTADOS.index(estado) < ESTADOS.index(outro)


__all__ = [
    "APLICACAO_ASSISTIDA",
    "APLICACAO_INDEPENDENTE",
    "COMPREENSAO_MANIFESTADA",
    "CONSOLIDACAO",
    "CONTEUDO_APRESENTADO",
    "DOMINIO_DEMONSTRADO",
    "ESTADOS",
    "RETENCAO",
    "anterior_a",
    "produz_evidencia_de_dominio",
    "rotulo_do_estado",
]
