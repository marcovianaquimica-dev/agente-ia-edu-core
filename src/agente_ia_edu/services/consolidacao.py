"""CONSOLIDACAO E RETENCAO - o que a banda nao via.

O QUE FALTAVA
==============
`PerformanceThresholdPolicy.band` tem cinco bandas, e todas respondem a mesma
pergunta: qual e o acerto dele? Nenhuma responde as duas que o §12 faz.

    Consolidacao: consistencia da aprendizagem em OPORTUNIDADES DIFERENTES.
    Retencao: recuperacao ou aplicacao APOS UM INTERVALO relevante.

Acertar 5 de 5 numa tarde e STRONG. Acertar 5 de 5 numa tarde e mais 3 de 3
duas semanas depois tambem e STRONG - e sao coisas pedagogicamente
diferentes. A banda nao ve o tempo nem as ocasioes, porque ela agrega.

ESTE MODULO NAO INVENTA CORTE NENHUM
=====================================
`min_sample_size` e `strong_accuracy` continuam vindo de
`PerformanceThresholdPolicy`, a fonte unica que o motor inteiro ja usa. O que
este modulo acrescenta nao e um numero novo - e duas DIMENSOES que a media
nao tem: quantas ocasioes distintas, e quanto tempo entre elas.

Trocar um corte aqui seria criar uma segunda politica de dominio. Ha teste
lendo a fonte e exigindo que a politica apareca.

UM ERRO NAO APAGA O QUE ELE JA MOSTROU
=======================================
O §12 e explicito: "revisao recomendada: necessidade de retomada, que NAO
apaga automaticamente dominio anterior". Entao o tropeco levanta uma
BANDEIRA ao lado do estado, e nao derruba o estado.

A diferenca importa para quem le a tela. "Voce ja mostrou isso, e vale
revisar" e verdade; "voce nao sabe mais isso" e uma conclusao que uma unica
resposta nao sustenta - e que o aluno sente como perda.

A REVISAO POR TEMPO E ADAPTATIVA
=================================
O §18 recusa "calendario rigido universal". O intervalo sugerido cresce com o
estado: quem so demonstrou uma vez volta a precisar antes de quem consolidou,
e quem reteve depois de tres semanas precisa ainda mais tarde. Nao e um
"revise a cada 7 dias" aplicado a todo mundo.

AUSENCIA NAO E DESCONHECIMENTO
===============================
Sem tentativa independente nenhuma, o estado e SEM_EVIDENCIA e o acerto e
`None` - nunca zero. O §12 proibe o percentual artificial, e "0%" lido por um
adolescente e uma nota, nao uma ausencia.
"""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime, timezone

ESTADO_SEM_EVIDENCIA = "NO_EVIDENCE"
ESTADO_EM_APRENDIZADO = "LEARNING"
ESTADO_DEMONSTRADO = "SHOWN"
ESTADO_CONSOLIDADO = "CONSOLIDATED"
ESTADO_RETIDO = "RETAINED"

# Quantas ocasioes DISTINTAS fazem consistencia.
#
# Duas, e nao tres: a pergunta que o §12 faz e se ele repetiu em outra
# ocasiao, e a segunda ja responde isso. Exigir tres atrasaria o
# reconhecimento sem responder melhor.
_OCASIOES_PARA_CONSOLIDAR = 2

# A partir de quantos dias um intervalo e "relevante" para retencao.
#
# Sete: menos que isso e a mesma semana de estudo, e lembrar dentro dela nao
# distingue memoria de curto prazo. Nao e um calendario de revisao - e o piso
# para CHAMAR de retencao o que aconteceu.
_DIAS_PARA_RETENCAO = 7

# O INTERVALO SUGERIDO ATE A PROXIMA REVISAO, por estado.
#
# Cresce com o estado, e e isso que o §18 chama de adaptativo: quem so
# demonstrou uma vez volta a precisar antes de quem consolidou. Nao se aplica
# a quem nao tem evidencia - revisar o que nunca foi visto nao e revisao.
_INTERVALOS = {
    ESTADO_DEMONSTRADO: 14,
    ESTADO_CONSOLIDADO: 30,
    ESTADO_RETIDO: 60,
}


def intervalo_sugerido(estado: str) -> int:
    """Dias ate a proxima revisao daquele estado. 0 = nao se aplica."""
    return _INTERVALOS.get(estado, 0)


def situacao(tentativas: Sequence[dict] | None, *,
             agora: datetime | None = None,
             politica=None) -> dict:
    """O estado de uma micro-habilidade, lido da linha do tempo dela.

    Cada tentativa e `{quando, correta, ocasiao, assistida?}`. `ocasiao`
    agrupa o que aconteceu junto - um lote de pratica corrigido de uma vez e
    UMA ocasiao, por mais questoes que tenha.

    SO TENTATIVA INDEPENDENTE CONTA. `assistida=True` e descartada antes de
    tudo: o §18 exige que acertar com ajuda nao seja reclassificado, e a
    forma mais segura de garantir isso e a evidencia assistida nao chegar ao
    calculo.
    """
    from agente_ia_edu.services.pedagogical_analysis import (
        PerformanceThresholdPolicy,
    )

    politica = politica or PerformanceThresholdPolicy.default()
    agora = _com_fuso(agora) or datetime.now(timezone.utc)

    independentes = [dict(t, quando=_com_fuso(t.get("quando")))
                     for t in (tentativas or ()) if not t.get("assistida")]
    independentes = [t for t in independentes if t["quando"] is not None]
    if not independentes:
        return _vazio()

    ocasioes = _por_ocasiao(independentes)
    respondidas = len(independentes)
    certas = sum(1 for t in independentes if t.get("correta"))
    acerto = round(certas / respondidas, 4) if respondidas else None

    fortes = [o for o in ocasioes if _forte(o, politica)]
    ultima = max(t["quando"] for t in independentes)
    intervalo = _intervalo_entre_fortes(fortes)

    estado = _estado(ocasioes, fortes, intervalo, respondidas, politica)
    recomendar, motivo = _revisao(estado, ocasioes, fortes, ultima, agora)

    return {
        "estado": estado,
        "acerto": acerto,
        "respondidas": respondidas,
        "oportunidades": len(ocasioes),
        "oportunidades_fortes": len(fortes),
        "intervalo_dias": intervalo,
        "ultima_em": ultima.isoformat(),
        "revisao_recomendada": recomendar,
        "motivo": motivo,
    }


def _com_fuso(quando):
    """Datetime com fuso, venha ele de onde vier.

    `corrected_at` e `DateTime(timezone=True)`, mas o driver decide o que
    devolve: o Postgres entrega com fuso e o SQLite - que os testes usam -
    entrega ingenuo. Subtrair um do outro levanta TypeError, e o calculo de
    intervalo inteiro depende dessa subtracao.

    Assume UTC para o ingenuo, que e como a aplicacao grava (`_utcnow`).
    """
    if quando is None:
        return None
    return quando if quando.tzinfo else quando.replace(tzinfo=timezone.utc)


def _vazio() -> dict:
    """Sem evidencia independente. Nao e zero, e nao pede revisao."""
    return {
        "estado": ESTADO_SEM_EVIDENCIA,
        # None, e nunca 0.0: o §12 proibe o percentual artificial, e "0%"
        # lido por um adolescente e uma nota, nao uma ausencia.
        "acerto": None,
        "respondidas": 0,
        "oportunidades": 0,
        "oportunidades_fortes": 0,
        "intervalo_dias": 0,
        "ultima_em": None,
        "revisao_recomendada": False,
        "motivo": "Ainda não há nada registrado sobre isso.",
    }


def _por_ocasiao(tentativas: Sequence[dict]) -> list[list[dict]]:
    """Agrupa por ocasiao, na ordem em que aconteceram."""
    grupos: dict[str, list[dict]] = {}
    for t in tentativas:
        grupos.setdefault(str(t.get("ocasiao") or t["quando"]), []).append(t)
    return sorted(grupos.values(), key=lambda g: min(t["quando"] for t in g))


def _forte(ocasiao: Sequence[dict], politica) -> bool:
    """Esta ocasiao, sozinha, mostra a habilidade de pe?

    Usa os MESMOS dois cortes do motor - amostra minima e acerto forte. Uma
    ocasiao com uma resposta so nao e forte, por mais certa que esteja: uma
    resposta nao distingue quem sabe de quem chutou.
    """
    respondidas = len(ocasiao)
    if respondidas < politica.min_sample_size:
        return False
    certas = sum(1 for t in ocasiao if t.get("correta"))
    return (certas / respondidas) >= politica.strong_accuracy


def _intervalo_entre_fortes(fortes: Sequence[Sequence[dict]]) -> int:
    """Dias entre a primeira e a ultima ocasiao forte."""
    if len(fortes) < 2:
        return 0
    primeira = min(t["quando"] for t in fortes[0])
    ultima = max(t["quando"] for t in fortes[-1])
    return max(0, int((ultima - primeira).total_seconds() // 86400))


def _estado(ocasioes, fortes, intervalo: int, respondidas: int,
            politica) -> str:
    """O estado, do mais exigente para o menos.

    A ordem e a propria definicao: retencao exige consolidacao, que exige
    demonstracao. Ler de cima para baixo e o que impede, por exemplo, que um
    intervalo longo sem consistencia vire retencao.
    """
    if len(fortes) >= _OCASIOES_PARA_CONSOLIDAR:
        if intervalo >= _DIAS_PARA_RETENCAO:
            return ESTADO_RETIDO
        return ESTADO_CONSOLIDADO
    if fortes:
        return ESTADO_DEMONSTRADO
    if respondidas < politica.min_sample_size:
        return ESTADO_EM_APRENDIZADO
    return ESTADO_EM_APRENDIZADO


def _revisao(estado: str, ocasioes, fortes, ultima: datetime,
             agora: datetime) -> tuple[bool, str]:
    """Vale retomar isto? E por que, em linguagem de aluno.

    DUAS RAZOES, e nenhuma delas derruba o estado:

    1. A ultima ocasiao nao ficou de pe, DEPOIS de ele ja ter mostrado. E o
       tropeco do §12 - levanta a bandeira, nao apaga o que passou.
    2. Faz tempo demais para aquele estado. Adaptativo: o limite vem de
       `intervalo_sugerido`, que cresce com o estado.
    """
    if estado in (ESTADO_SEM_EVIDENCIA, ESTADO_EM_APRENDIZADO):
        # Nao se "revisa" o que ainda esta sendo aprendido - isso e o
        # percurso normal, e chamar de revisao confundiria o aluno.
        return False, _MOTIVOS[estado]

    ultima_ocasiao = ocasioes[-1] if ocasioes else []
    if ultima_ocasiao and ultima_ocasiao is not fortes[-1]:
        return True, ("Da última vez não saiu como antes. Vale retomar — o "
                      "que você já mostrou continua valendo.")

    dias = max(0, int((agora - ultima).total_seconds() // 86400))
    limite = intervalo_sugerido(estado)
    if limite and dias >= limite:
        return True, ("Já faz um tempo desde a última vez. Vale uma passada "
                      "rápida para confirmar.")
    return False, _MOTIVOS[estado]


_MOTIVOS = {
    ESTADO_SEM_EVIDENCIA: "Ainda não há nada registrado sobre isso.",
    ESTADO_EM_APRENDIZADO: "Você está começando a trabalhar isso.",
    ESTADO_DEMONSTRADO: "Você mostrou isso uma vez, sozinho.",
    ESTADO_CONSOLIDADO: "Você mostrou isso em ocasiões diferentes.",
    ESTADO_RETIDO: "Você lembrou disso depois de um tempo.",
}


# O NOME CURTO DE CADA ESTADO, para rotulo de tela.
#
# As quatro distincoes do §12 - desempenho elevado, dominio demonstrado,
# consolidacao e retencao - precisam de QUATRO nomes. "Indo bem" e da faixa
# de desempenho, em `student_progress`; os tres daqui sao os estados reais.
# Ha teste exigindo que os quatro sejam distintos: se dois compartilhassem o
# nome, o aluno leria uma coisa e o sistema teria querido dizer outra.
_NOMES_CURTOS = {
    ESTADO_SEM_EVIDENCIA: "Sem registro ainda",
    ESTADO_EM_APRENDIZADO: "Em construção",
    ESTADO_DEMONSTRADO: "Você fez sozinho",
    ESTADO_CONSOLIDADO: "Consolidado",
    ESTADO_RETIDO: "Você lembrou depois",
}

ESTADOS_CONHECIDOS = tuple(_NOMES_CURTOS)


def nome_curto(estado: str) -> str:
    """O rotulo de uma palavra ou duas, para a tela."""
    return _NOMES_CURTOS.get(estado, "")


def rotulo_do_estado(estado: str) -> str:
    """O que o aluno le. Nunca o codigo, nunca percentual."""
    return _MOTIVOS.get(estado, "")


__all__ = [
    "ESTADOS_CONHECIDOS",
    "ESTADO_CONSOLIDADO",
    "ESTADO_DEMONSTRADO",
    "ESTADO_EM_APRENDIZADO",
    "ESTADO_RETIDO",
    "ESTADO_SEM_EVIDENCIA",
    "intervalo_sugerido",
    "nome_curto",
    "rotulo_do_estado",
    "situacao",
]
