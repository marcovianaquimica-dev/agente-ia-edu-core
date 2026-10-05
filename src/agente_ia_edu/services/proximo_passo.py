"""O PROXIMO PASSO do aluno. Sempre existe um, e sempre e dito.

    conteudos exigidos -> evidencia -> UM passo concreto

O QUE ISTO CONSERTA
===================
O teste humano de 2026-10-04 encontrou o aluno preso. Ele errou as tres
perguntas de Balanceamento; o backend decidiu `PREPARE_PREREQUISITE`, que esta
certo; e a prontidao da atividade continuou `DIAGNOSTIC` apontando para o
MESMO conteudo. "Continuar" abria outro microdiagnostico de Balanceamento, e
depois outro.

O microdiagnostico COLETA EVIDENCIA PARA DECIDIR. Depois que decidiu,
repeti-lo nao acrescenta nada - a decisao ja foi tomada, e o que falta e
estudar. A distincao que faltava:

    sem evidencia sobre a base       -> DIAGNOSTICAR  (ainda nao sei)
    evidencia, e a base esta fraca   -> PRATICAR      (ja sei, e falta)
    evidencia, e a base esta boa     -> deixa de ser obstaculo

NENHUM CORTE NOVO MORA AQUI
============================
As tres faixas vem de `PerformanceThresholdPolicy`, a mesma que o
microdiagnostico usa para decidir. Ha teste lendo a AST deste arquivo que
falha se alguem escrever um numero de corte aqui: uma segunda copia de "quanto
e saber o suficiente" e uma segunda definicao, e elas divergem.

NENHUM MOTOR NOVO, TAMBEM
==========================
`PASSO_PRATICA` aponta para `AdaptivePracticeService`, que ja existe e ja e o
que o microdiagnostico usa por baixo. O que faltava nao era maquina de
pratica: era alguem DIZER ao aluno que o proximo passo e praticar.
"""

from __future__ import annotations

from typing import Sequence

from agente_ia_edu.services.pedagogical_analysis import (
    BAND_INSUFFICIENT,
    BAND_IMPROVEMENT,
    BAND_NO_DATA,
    PerformanceThresholdPolicy,
)
from agente_ia_edu.services.readiness_route import (
    ESTADOS_QUE_LIBERAM,
    ROTA_DIAGNOSTICO,
    ROTA_DIRETA,
    ROTA_PREPARACAO,
)

# O que o aluno faz AGORA. Quatro, e o quarto e honesto.
PASSO_DIAGNOSTICO = "DIAGNOSTIC"   # responder o microdiagnostico
PASSO_PRATICA = "PRACTICE"         # praticar o conteudo (AdaptivePracticeService)
PASSO_ATIVIDADE = "ACTIVITY"       # abrir a tarefa da escola
PASSO_NENHUM = "NONE"              # nao ha o que oferecer, e a tela diz isso

# Em que pe esta ESSE passo. Importados do player, nao redigitados: uma
# segunda lista de estados divergiria da primeira no dia em que uma delas
# mudasse.
from agente_ia_edu.services.activity_player_store import (  # noqa: E402
    STATUS_COMPLETED as ESTADO_CONCLUIDO,
    STATUS_IN_PROGRESS as ESTADO_EM_ANDAMENTO,
    STATUS_NOT_STARTED as ESTADO_NAO_INICIADO,
)

# O CTA SAI DA MATRIZ (tipo x estado), NAO DE UM MAPA POR TIPO.
#
# Antes o rotulo vinha de um dicionario `kind -> texto` no JavaScript:
# DIAGNOSTIC era sempre "Responder". O aluno acertava as tres perguntas, a
# tela dizia "DIAGNOSTICO CONCLUIDO - voce demonstrou bom dominio", e o botao
# continuava convidando a RESPONDER o que ele acabara de responder.
#
# Trocar a string resolveria aquela tela e deixaria as outras seis
# combinacoes erradas. O rotulo precisa saber em que pe a etapa esta.
_CTA = {
    (PASSO_DIAGNOSTICO, ESTADO_NAO_INICIADO): "Responder diagnóstico",
    (PASSO_DIAGNOSTICO, ESTADO_EM_ANDAMENTO): "Continuar diagnóstico",
    (PASSO_DIAGNOSTICO, ESTADO_CONCLUIDO): "Continuar",
    (PASSO_PRATICA, ESTADO_NAO_INICIADO): "Praticar agora",
    (PASSO_PRATICA, ESTADO_EM_ANDAMENTO): "Continuar prática",
    (PASSO_PRATICA, ESTADO_CONCLUIDO): "Continuar",
    (PASSO_ATIVIDADE, ESTADO_NAO_INICIADO): "Começar atividade",
    (PASSO_ATIVIDADE, ESTADO_EM_ANDAMENTO): "Continuar atividade",
    (PASSO_ATIVIDADE, ESTADO_CONCLUIDO): "Ver resultado",
}


def cta_para(kind: str, estado: str) -> str | None:
    """O texto do botao, a partir do tipo do passo e de como ele esta.

    None quando nao ha passo: botao sem destino e pior que nenhum botao.

    Um estado desconhecido cai no "nao iniciado" em vez de deixar a tela sem
    botao - errar o verbo e recuperavel, deixar o aluno parado nao e.
    """
    if kind == PASSO_NENHUM or kind not in (
            PASSO_DIAGNOSTICO, PASSO_PRATICA, PASSO_ATIVIDADE):
        return None
    return _CTA.get((kind, estado)) or _CTA[(kind, ESTADO_NAO_INICIADO)]


def _faixa(thresholds: PerformanceThresholdPolicy, evidencia: dict) -> str:
    return thresholds.band(answered=int(evidencia.get("answered") or 0),
                           accuracy=evidencia.get("accuracy"))


def _passo_para_um(thresholds, codigo: str, nome: str, evidencia: dict) -> dict | None:
    """Diagnosticar, praticar, ou nada a fazer com este conteudo.

    Devolve None quando a evidencia ja e boa o bastante - ou seja, quando este
    conteudo deixou de ser obstaculo e quem pergunta deve seguir adiante.
    """
    banda = _faixa(thresholds, evidencia)
    if banda in (BAND_INSUFFICIENT, BAND_NO_DATA):
        return {"kind": PASSO_DIAGNOSTICO, "content_code": codigo,
                "content_name": nome, "band": banda}
    if banda == BAND_IMPROVEMENT:
        return {"kind": PASSO_PRATICA, "content_code": codigo,
                "content_name": nome, "band": banda}
    return None


def passo_para(conteudos: Sequence[dict], *,
               thresholds: PerformanceThresholdPolicy | None = None) -> dict:
    """O unico proximo passo, a partir dos conteudos que a atividade exige.

    Cada conteudo traz seu estado e, quando se sabe, seus pre-requisitos com a
    evidencia que ja existe sobre eles. A base vem primeiro: entre dois
    conteudos igualmente desconhecidos, o de baixo informa mais.
    """
    thresholds = thresholds or PerformanceThresholdPolicy.default()

    if not conteudos:
        # Fail-closed. "Nao sei o que esta atividade exige" nao e "pode
        # entrar", e tambem nao ha o que diagnosticar - a tela precisa poder
        # dizer isso em vez de oferecer um botao que nao leva a lugar nenhum.
        return {"kind": PASSO_NENHUM, "content_code": None, "content_name": None,
                "readiness_route": ROTA_DIAGNOSTICO,
                "reason": "a atividade ainda nao declara os conteudos que exige"}

    # O ESTADO DO PLANEJADOR NAO BASTA.
    #
    # Ele marca o conteudo como RECOMMENDED assim que ha qualquer evidencia -
    # RECOMMENDED quer dizer "pratique isto", nao "esta pronto". No Piloto Zero
    # de 2026-10-04 isso liberou para a atividade um aluno que errou as TRES
    # perguntas de Estequiometria: acerto 0,0, rota DIRECT.
    #
    # O estado diz se HA evidencia. Quem diz se ela e BOA e a politica - a
    # mesma que ja decide o pre-requisito, logo abaixo.
    def _liberado(c: dict) -> bool:
        if c.get("content_state") not in ESTADOS_QUE_LIBERAM:
            return False
        respondidas = int(c.get("answered") or 0)
        if respondidas <= 0:
            # Sem evidencia propria, respeitamos o planejador: READY pode vir
            # de pre-requisitos dominados ou do contexto da escola, e inventar
            # bloqueio ai seria tao errado quanto liberar sem olhar.
            return True
        banda = thresholds.band(answered=respondidas, accuracy=c.get("accuracy"))
        return banda not in (BAND_IMPROVEMENT, BAND_INSUFFICIENT, BAND_NO_DATA)

    pendentes = [c for c in conteudos if not _liberado(c)]
    if not pendentes:
        primeiro = conteudos[0]
        return {"kind": PASSO_ATIVIDADE,
                "content_code": primeiro.get("content_code"),
                "content_name": primeiro.get("content_name"),
                "readiness_route": ROTA_DIRETA,
                "reason": "o aluno tem evidencia suficiente nos conteudos exigidos"}

    alvo = pendentes[0]

    # A BASE PRIMEIRO. Um pre-requisito ainda nao resolvido vale mais que o
    # conteudo final: perguntar Estequiometria a quem nao sabe balancear uma
    # equacao e perguntar a coisa errada.
    for pre in alvo.get("prerequisites") or []:
        if pre.get("mastered"):
            continue
        passo = _passo_para_um(thresholds, pre.get("code"),
                               pre.get("name") or pre.get("code"), pre)
        if passo is None:
            continue                      # base ja suficiente: segue adiante
        passo["readiness_route"] = (ROTA_PREPARACAO
                                    if passo["kind"] == PASSO_PRATICA
                                    else ROTA_DIAGNOSTICO)
        passo["reason"] = ("a base precisa ser firmada antes"
                           if passo["kind"] == PASSO_PRATICA
                           else "ainda nao ha evidencia sobre a base")
        passo["for_content_code"] = alvo.get("content_code")
        passo["for_content_name"] = alvo.get("content_name")
        return passo

    # Nenhum pre-requisito atrapalha: o passo e sobre o proprio conteudo.
    passo = _passo_para_um(thresholds, alvo.get("content_code"),
                           alvo.get("content_name"), alvo)
    if passo is None:
        # Evidencia boa, mas o planejador nao liberou o estado. Nao inventamos
        # liberacao: quem decide o estado e o planejador.
        return {"kind": PASSO_DIAGNOSTICO,
                "content_code": alvo.get("content_code"),
                "content_name": alvo.get("content_name"),
                "readiness_route": ROTA_DIAGNOSTICO,
                "reason": "falta evidencia sobre o conteudo da atividade"}
    passo["readiness_route"] = (ROTA_PREPARACAO if passo["kind"] == PASSO_PRATICA
                                else ROTA_DIAGNOSTICO)
    passo["reason"] = ("este conteudo precisa de pratica"
                       if passo["kind"] == PASSO_PRATICA
                       else "ainda nao ha evidencia sobre este conteudo")
    passo["for_content_code"] = alvo.get("content_code")
    passo["for_content_name"] = alvo.get("content_name")
    return passo


__all__ = [
    "cta_para",
    "ESTADO_NAO_INICIADO",
    "ESTADO_EM_ANDAMENTO",
    "ESTADO_CONCLUIDO",
    "passo_para",
    "PASSO_DIAGNOSTICO",
    "PASSO_PRATICA",
    "PASSO_ATIVIDADE",
    "PASSO_NENHUM",
]
