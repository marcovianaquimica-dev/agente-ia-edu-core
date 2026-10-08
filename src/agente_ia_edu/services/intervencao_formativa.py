"""A DECISAO QUE FALTAVA: o que fazer quando ele erra NA PRATICA.

O DEFEITO, MEDIDO NA VALIDACAO MANUAL DE 2026-10-08
====================================================
O estudante respondeu errado e a plataforma serviu a proxima questao, e a
proxima, ate "5 de 5". Rastreei o caminho inteiro:

    frontend  `avancar()` faz `d.pos += 1` e desenha a proxima
    API       POST .../attempt/answer
    servico   `save_answer` grava `selected_option_key` e devolve um
              RECIBO - saved, position, answered_count. NUNCA consulta o
              gabarito.
    correcao  `attempt/correct`, so depois do lote inteiro

Entao o backend RECEBEU a resposta errada e nao tinha como classifica-la
naquele momento: a pratica estava modelada como LOTE RIGIDO, corrigido no
fim. O frontend nao ignorou decisao nenhuma - nao havia decisao. A
intervencao estava ausente por falta de integracao, nao por regra explicita.

E havia uma assimetria indevida entre os dois caminhos do mesmo produto:
`servico_de_investigacao.responder` confere NA HORA e devolve `correct`,
`observacao`, o estado da hipotese e o proximo passo. A pratica, nao.

O QUE ESTE MODULO FAZ
======================
Uma coisa: depois de uma resposta gravada, decide se ha intervencao.

    RESPOSTA -> OBSERVACAO -> DECISAO -> (quem chama executa)

Nao grava nada. Nao corrige a tentativa. Nao escreve evidencia. Nao decide
dominio. E leitura e decisao - a escrita continua onde sempre esteve.

NAO E UM SEGUNDO MOTOR
=======================
A acao sai do que ja existe:

    ha cadeia curada para a micro-habilidade, e ela ainda esta de pe?
        -> INVESTIGAR, pela mesma `InvestigacaoService` do dialogo
    ja percorrida, mas ha material publicado do conteudo?
        -> ENSINAR, pelo mesmo `StudentMaterialService` da preparacao
    nenhum dos dois?
        -> nada. E prender o aluno numa questao sem ter com que ajuda-lo
           seria pior que deixa-lo seguir.

A escada de apoio continua sendo a mesma, e a ORDEM e a dela: investigar
antes de ensinar, porque a micropergunta e mais barata e descobre O QUE
explicar antes de gastar a explicacao.

O PORTAO VEM ANTES
===================
`modo_pedagogico.intervem_no_erro` decide se este modulo pode agir. So a
pratica formativa passa: no diagnostico ensinar contamina a observacao
seguinte, na verificacao L0 destroi a independencia da evidencia, e na
avaliacao formal viola a politica da prova.
"""

from __future__ import annotations

import uuid as _uuid
from collections.abc import Mapping

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import PedagogicalClassification, QuestionOption
from agente_ia_edu.services.modo_pedagogico import intervem_no_erro, modo_de

# As duas acoes que este modulo sabe decidir. Os nomes sao os que o
# assessor ja usa - importa-los de la criaria um ciclo, e redefini-los com
# outro texto criaria dois vocabularios para a mesma coisa. Ha teste
# exigindo que batam.
ACAO_INVESTIGAR = "INVESTIGATE"
ACAO_ENSINAR = "TEACH"


async def decidir_apos_resposta(
    session: AsyncSession, *, aluno: str, metadata: Mapping | None,
    question_version_id, selected_option_key: str | None, requester,
) -> dict:
    """A decisao pedagogica desta resposta, ou {} quando nao ha.

    `{}` nao e falha: e "siga". Tres caminhos chegam nele, e os tres sao
    respostas honestas - o modo nao autoriza intervir, o aluno acertou, ou
    nao ha com que intervir naquela habilidade.

    Devolve `may_advance: False` junto com a decisao para que quem chama
    nao precise reinterpreta-la: a presenca de uma decisao JA significa
    "nao avance", e repetir isso como campo evita que um cliente novo
    deduza o contrario.
    """
    modo = modo_de(metadata)
    if not intervem_no_erro(modo):
        return {}

    # A coluna e `Uuid`, e quem chama tanto pode trazer o objeto (a rota)
    # quanto a string (um teste, um script). Converter aqui evita que o
    # erro so apareca no dialeto que valida o tipo.
    qvid = _como_uuid(question_version_id)
    if qvid is None:
        return {}

    # -- OBSERVACAO: ele errou? ------------------------------------------
    #
    # Pular nao e errar. Uma questao sem resposta continua pendente, e o
    # lote ainda nao acabou - interromper aqui seria cobrar uma decisao
    # sobre algo que o aluno nao disse.
    marcada = (selected_option_key or "").strip().upper()
    if not marcada:
        return {}

    correta = (await session.execute(
        select(QuestionOption.option_key).where(
            QuestionOption.question_version_id == qvid,
            QuestionOption.is_valid_option.is_(True),
        ))).scalars().first()
    if correta is None:
        # Sem gabarito nao ha observacao possivel. Nao se conclui nada
        # sobre o aluno a partir de um item que ninguem sabe conferir.
        return {}
    if marcada == str(correta).strip().upper():
        return {}

    # -- ALVO: qual micro-habilidade este ITEM mede? ----------------------
    #
    # Do item, nao do conteudo: "errou estequiometria" nao diz onde
    # intervir, e intervir no conteudo inteiro e ensinar de novo o que ele
    # ja sabe.
    habilidade = (await session.execute(
        select(PedagogicalClassification.subcontent).where(
            PedagogicalClassification.question_version_id == qvid,
            PedagogicalClassification.lifecycle == "ACTIVE",
        ))).scalars().first()
    conteudo = (await session.execute(
        select(PedagogicalClassification.content).where(
            PedagogicalClassification.question_version_id == qvid,
            PedagogicalClassification.lifecycle == "ACTIVE",
        ))).scalars().first()
    if not habilidade or not conteudo:
        # SEM ALVO NAO HA INTERVENCAO, e inventar um seria pior: o aluno
        # ficaria preso numa questao para receber ajuda sobre outra coisa.
        return {}

    base = {"mode": modo, "skill": habilidade, "content_code": conteudo,
            "question_version_id": str(qvid),
            "selected_option": marcada, "may_advance": False}

    # -- DECISAO: investigar antes de ensinar -----------------------------
    if await _investigacao_pendente(session, aluno, conteudo, habilidade,
                                    requester=requester):
        return {**base, "action": ACAO_INVESTIGAR,
                "reason": ("ha uma cadeia de microperguntas escrita para esta "
                           "micro-habilidade, e o aluno ainda nao a percorreu")}

    material = await _material_de(session, conteudo, requester=requester)
    if material:
        return {**base, "action": ACAO_ENSINAR,
                "material_id": material.get("material_id"),
                "material_title": material.get("title"),
                "reason": ("a investigacao desta micro-habilidade ja foi "
                           "percorrida; o proximo degrau da escada e a "
                           "explicacao")}

    # NADA COM QUE INTERVIR. Prender o aluno numa questao sem ter o que
    # oferecer seria transformar a correcao num bloqueio.
    return {}


def _como_uuid(valor):
    """UUID, venha ele como objeto ou como texto. None se nao for um."""
    if isinstance(valor, _uuid.UUID):
        return valor
    try:
        return _uuid.UUID(str(valor))
    except (TypeError, ValueError):
        return None


async def _investigacao_pendente(session: AsyncSession, aluno: str,
                                 conteudo: str, habilidade: str, *,
                                 requester) -> bool:
    from agente_ia_edu.services.servico_de_investigacao import (
        InvestigacaoService,
    )

    try:
        return await InvestigacaoService(session).pendente(
            aluno, conteudo, habilidade, requester=requester)
    except Exception:  # noqa: BLE001 - na duvida, nao prender o aluno
        return False


async def _material_de(session: AsyncSession, conteudo: str, *,
                       requester) -> dict | None:
    """O material publicado daquele conteudo, se houver.

    Mesma leitura que `readiness_route._material_de` faz - prometer uma
    explicacao que nao existe seria pior que deixar seguir.
    """
    from agente_ia_edu.services.student_material import StudentMaterialService

    try:
        materiais = await StudentMaterialService(session).list_materials(
            requester_school_id=getattr(requester, "school_id", None),
            content_code=conteudo)
    except Exception:  # noqa: BLE001
        return None
    return materiais[0] if materiais else None


__all__ = ["ACAO_ENSINAR", "ACAO_INVESTIGAR", "decidir_apos_resposta"]
