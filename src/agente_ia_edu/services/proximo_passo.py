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
PASSO_ENSINO = "LEARN"             # ENTENDER antes de responder de novo
PASSO_GUIADA = "GUIDED_PRACTICE"   # TENTAR com ajuda progressiva do Assessor
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
    # ENSINO: uma quarta linha da MESMA matriz, nao um caso especial costurado
    # na tela. Caso especial na tela vira `if` no JavaScript - de onde este
    # projeto ja teve de tirar o rotulo "Responder" uma vez.
    #
    # No fim do ensino o rotulo diz PARA ONDE se vai ("Praticar agora"), e nao
    # "Continuar": e justamente a desorientacao que motivou este bloco.
    (PASSO_ENSINO, ESTADO_NAO_INICIADO): "Entender o conceito",
    (PASSO_ENSINO, ESTADO_EM_ANDAMENTO): "Continuar estudando",
    # Depois de entender vem TENTAR COM AJUDA, nao praticar sozinho - entre os
    # dois entrou a pratica guiada, e "Praticar agora" aqui pularia por cima
    # dela.
    (PASSO_ENSINO, ESTADO_CONCLUIDO): "Tentar com ajuda",

    # PRATICA GUIADA: o aluno tenta, e a ajuda chega quando precisa.
    #
    # O verbo e "tentar" de proposito: nao e ver nem estudar. E o rotulo do
    # fim anuncia a diferenca que este bloco inteiro existe para marcar -
    # conseguir com ajuda nao e dominar sozinho, entao o passo seguinte e
    # explicitamente SOZINHO.
    (PASSO_GUIADA, ESTADO_NAO_INICIADO): "Tentar com ajuda",
    (PASSO_GUIADA, ESTADO_EM_ANDAMENTO): "Continuar tentando",
    (PASSO_GUIADA, ESTADO_CONCLUIDO): "Agora tentar sozinho",
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


# ---------------------------------------------------- a jornada visivel ---
# "Onde estou?" - a pergunta que a barra de questoes nao respondia, porque ela
# media questoes DENTRO de uma etapa.
ETAPA_DIAGNOSTICO = "DIAGNOSTICO"
ETAPA_PREPARACAO = "PREPARACAO"
ETAPA_ATIVIDADE = "ATIVIDADE"
ETAPA_RESULTADO = "RESULTADO"

_ROTULOS = {
    ETAPA_DIAGNOSTICO: "Diagnóstico",
    ETAPA_PREPARACAO: "Preparação",
    ETAPA_ATIVIDADE: "Atividade",
    ETAPA_RESULTADO: "Resultado",
}


def jornada_de(*, origens: dict, estado_atividade: str, rota: str,
               kind: str | None = None) -> list[dict]:
    """As etapas da jornada: qual ja passou, onde o aluno esta, o que falta.

    A POSICAO VEM DO PROXIMO PASSO; A CONCLUSAO, DA EVIDENCIA.

    A primeira versao disto derivava tudo da evidencia, e errava o caso real:
    o aluno fazia o microdiagnostico do PRE-REQUISITO, cuja evidencia fica em
    outro conteudo, e a barra continuava dizendo "Diagnostico em andamento"
    depois de ele ter acabado de fazer um.

    Agora o `kind` do proximo passo diz em que etapa ele esta - e e a
    autoridade, porque e a mesma coisa que decide o botao. A evidencia
    confirma as etapas ANTERIORES: sem ela, nada fica verde. Navegar nunca
    conclui etapa; se bastasse abrir a tela, quem clicasse em tudo veria a
    jornada inteira verde sem ter aprendido nada.

    COM UMA EXCECAO, e e a unica: atividade ENTREGUE manda mais que o passo.
    O passo pode voltar (quem foi mal volta a ser mandado a diagnosticar a
    base), e a jornada nao pode voltar com ele - a entrega ja aconteceu.

    PREPARACAO so entra quando foi pedida ou quando ja houve pratica, e nao
    sai depois de entrar: a jornada nao pode encolher na frente do aluno.
    """
    def tem(origem: str) -> bool:
        return int((origens or {}).get(origem) or 0) > 0

    etapas = [ETAPA_DIAGNOSTICO]
    if rota == ROTA_PREPARACAO or tem("PRACTICE"):
        etapas.append(ETAPA_PREPARACAO)
    etapas += [ETAPA_ATIVIDADE, ETAPA_RESULTADO]

    # UMA ENTREGA E FATO CONSUMADO, e vem antes do passo.
    #
    # Com 2 de 5 na atividade o planejador volta a mandar diagnosticar a base
    # - ela ainda nao esta dominada, e isso e decisao pedagogica legitima. Mas
    # o passo voltando fazia a jornada voltar junto, e ela dizia "Diagnostico,
    # etapa atual / Atividade, a seguir" com a atividade JA ENTREGUE. A regra
    # "a posicao vem do passo" valia enquanto a jornada so avancava; nenhum
    # proximo passo desfaz o que o aluno entregou.
    if estado_atividade == ESTADO_CONCLUIDO:
        atual = ETAPA_RESULTADO
    else:
        # Onde o aluno esta AGORA, segundo o proximo passo.
        atual = {
            PASSO_DIAGNOSTICO: ETAPA_DIAGNOSTICO,
            # Ensinar acontece DENTRO de "Preparacao", junto com praticar. Uma
            # etapa "Ensino" ao lado dela dobraria a barra e contaria ao aluno
            # um detalhe de implementacao.
            PASSO_ENSINO: ETAPA_PREPARACAO,
            PASSO_GUIADA: ETAPA_PREPARACAO,
            PASSO_PRATICA: ETAPA_PREPARACAO,
            PASSO_ATIVIDADE: ETAPA_ATIVIDADE,
        }.get(kind)
    if atual is None or atual not in etapas:
        # Sem passo conhecido: cai na primeira etapa ainda nao evidenciada.
        atual = next((e for e in etapas if not _evidenciada(e, tem, estado_atividade)),
                     etapas[-1])

    corte = etapas.index(atual)
    saida = []
    for i, etapa in enumerate(etapas):
        if i < corte:
            # Anterior a atual: so fica verde se houver evidencia de verdade.
            estado = (ESTADO_CONCLUIDO if _evidenciada(etapa, tem, estado_atividade)
                      else ESTADO_NAO_INICIADO)
        elif i == corte:
            estado = ESTADO_EM_ANDAMENTO
        else:
            estado = ESTADO_NAO_INICIADO
        saida.append({"id": etapa, "label": _ROTULOS[etapa], "state": estado})
    return saida


def _evidenciada(etapa: str, tem, estado_atividade: str) -> bool:
    if etapa == ETAPA_DIAGNOSTICO:
        return tem("MICRO_DIAGNOSTIC")
    if etapa == ETAPA_PREPARACAO:
        return tem("PRACTICE")
    if etapa == ETAPA_ATIVIDADE:
        return estado_atividade == ESTADO_CONCLUIDO
    return False


def cta_para(kind: str, estado: str) -> str | None:
    """O texto do botao, a partir do tipo do passo e de como ele esta.

    None quando nao ha passo: botao sem destino e pior que nenhum botao.

    Um estado desconhecido cai no "nao iniciado" em vez de deixar a tela sem
    botao - errar o verbo e recuperavel, deixar o aluno parado nao e.
    """
    if kind == PASSO_NENHUM or kind not in (
            PASSO_DIAGNOSTICO, PASSO_ENSINO, PASSO_GUIADA, PASSO_PRATICA,
            PASSO_ATIVIDADE):
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


def _praticar_o_que_trava(thresholds, alvo: dict) -> dict:
    """O conteudo foi medido mas o estado nao libera: pratica-se.

    E pratica-se a BASE quando e ela que trava - e ela que precisa subir para
    destravar o resto. Praticar o conteudo final enquanto o pre-requisito
    continua frouxo e insistir na pergunta errada.

    Sem amostra suficiente em lugar nenhum, continua valendo o diagnostico:
    quem nunca foi medido precisa ser medido, e esta regra nao pode engolir o
    caso que o diagnostico existe para atender.
    """
    def medido(e: dict) -> bool:
        return int((e or {}).get("answered") or 0) >= thresholds.min_sample_size

    for pre in alvo.get("prerequisites") or []:
        if pre.get("mastered") or not medido(pre):
            continue
        return {"kind": PASSO_PRATICA,
                "content_code": pre.get("code"),
                "content_name": pre.get("name") or pre.get("code"),
                "readiness_route": ROTA_PREPARACAO,
                "reason": "a base ja foi medida e ainda nao esta firme",
                "for_content_code": alvo.get("content_code"),
                "for_content_name": alvo.get("content_name")}

    if medido(alvo):
        return {"kind": PASSO_PRATICA,
                "content_code": alvo.get("content_code"),
                "content_name": alvo.get("content_name"),
                "readiness_route": ROTA_PREPARACAO,
                "reason": "este conteudo ja foi medido e ainda nao esta firme",
                "for_content_code": alvo.get("content_code"),
                "for_content_name": alvo.get("content_name")}

    return {"kind": PASSO_DIAGNOSTICO,
            "content_code": alvo.get("content_code"),
            "content_name": alvo.get("content_name"),
            "readiness_route": ROTA_DIAGNOSTICO,
            "reason": "ainda nao ha evidencia sobre o conteudo da atividade",
            "for_content_code": alvo.get("content_code"),
            "for_content_name": alvo.get("content_name")}


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
        # Evidencia na faixa intermediaria - nem fraca o bastante para mandar
        # praticar, nem forte o bastante para o planejador liberar o estado.
        # Nao inventamos liberacao: quem decide o estado e o planejador.
        #
        # DIAGNOSTICAR SERVE PARA MEDIR QUEM NAO FOI MEDIDO. Este ramo
        # respondia DIAGNOSTIC com a razao "falta evidencia sobre o conteudo"
        # para um aluno com OITO respostas registradas - a frase era falsa, e
        # a acao mandava responder de novo o que ele ja tinha respondido.
        # Havendo amostra, o passo e PRATICAR.
        return _praticar_o_que_trava(thresholds, alvo)
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
    "jornada_de",
    "ETAPA_DIAGNOSTICO",
    "ETAPA_PREPARACAO",
    "ETAPA_ATIVIDADE",
    "ETAPA_RESULTADO",
    "ESTADO_NAO_INICIADO",
    "ESTADO_EM_ANDAMENTO",
    "ESTADO_CONCLUIDO",
    "passo_para",
    "PASSO_DIAGNOSTICO",
    "PASSO_PRATICA",
    "PASSO_ATIVIDADE",
    "PASSO_NENHUM",
]
