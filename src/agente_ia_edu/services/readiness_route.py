"""A PRONTIDAO para a tarefa da escola, e o que fazer quando ela falta.

    tarefa -> conteudos exigidos -> estado do aluno -> ROTA -> proximo passo

A regra pedagogica que este modulo implementa e uma so:

    A TAREFA DA ESCOLA TEM PRIORIDADE, NAO EXCLUSIVIDADE.

Se o aluno nao tem condicao de fazer a atividade, obriga-lo a fazer mesmo
assim nao ensina nada - ele erra, conclui que nao sabe quimica, e a escola
conclui que ele nao estudou. A atividade continua sendo o OBJETIVO; o que
muda e o primeiro passo.

POR QUE ISTO E BACKEND
=======================
Esta decisao vivia em quatro linhas de JavaScript lendo um MOCK
(`aluno.js`, `rotaDeProntidao`). Enquanto foi prototipo, tudo bem. No Piloto
Zero deixa de ser: a rota e gravada em `study_sessions.readiness_route` e
determina se o aluno responde um diagnostico ou abre a prova. Regra que
decide isso nao pode morar onde o console do navegador alcanca.

NAO HA MOTOR NOVO AQUI
=======================
Quem sabe o estado de cada conteudo e `AdaptiveLearningPathService`; quem
sabe o que a atividade exige e `ActivityAssignmentStore.content_codes_by_version`
(que ja agregava `content_question_links`); quem conduz o diagnostico e
`MicroDiagnosticService`. Este modulo so os costura e escolhe uma das tres
rotas.

FAIL-CLOSED
===========
Lista de conteudos vazia -> DIAGNOSTIC, nao DIRECT. Atividade com questoes
ainda nao classificadas e o estado NORMAL de uma atividade recem-criada, e
"nao sei o que isto exige" nunca pode virar "pode entrar".
"""

from __future__ import annotations

from typing import Iterable, Sequence
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.services.study_session import (
    READINESS_DIAGNOSTIC,
    READINESS_DIRECT,
    READINESS_PREREQUISITE,
)

# Nomes em portugues no modulo, valores identicos aos do CheckConstraint da
# migration 064 - importados, nunca redigitados.
ROTA_DIRETA = READINESS_DIRECT
ROTA_DIAGNOSTICO = READINESS_DIAGNOSTIC
ROTA_PREPARACAO = READINESS_PREREQUISITE

# Os unicos estados do planejador que autorizam entrar na atividade. Lista de
# PERMISSAO, nao de bloqueio: um estado novo que ninguem mapeou aqui cai fora
# dela e manda diagnosticar, que e o erro barato.
ESTADOS_QUE_LIBERAM = frozenset({"READY", "MASTERED", "RECOMMENDED"})
ESTADO_BLOQUEADO = "BLOCKED_BY_PREREQUISITE"

# Quantas questoes tem uma VERIFICACAO. Curta de proposito: ela confirma uma
# recuperacao que acabou de acontecer, nao reabre a medicao. Tres e o minimo
# de amostra da propria PerformanceThresholdPolicy - abaixo disso a politica
# se recusa a concluir, e uma verificacao que nao conclui nao verifica nada.
QUESTOES_DA_VERIFICACAO = 3


def rota_de_estados(estados: Iterable[str]) -> str:
    """Dos estados dos conteudos exigidos para UMA das tres rotas.

    Precedencia: bloqueio > falta de evidencia > liberado. Saber que falta o
    pre-requisito e mais informativo que nao saber nada - diagnosticar o
    conteudo final de quem nem tem a base seria perguntar a coisa errada.
    """
    estados = list(estados)
    if not estados:
        return ROTA_DIAGNOSTICO
    if any(e == ESTADO_BLOQUEADO for e in estados):
        return ROTA_PREPARACAO
    if all(e in ESTADOS_QUE_LIBERAM for e in estados):
        return ROTA_DIRETA
    return ROTA_DIAGNOSTICO



def com_evidencia(prereqs, por_conteudo: dict) -> list[dict]:
    """Completa cada pre-requisito com o que ja se sabe sobre ele.

    POR QUE E UMA ETAPA PROPRIA
    ============================
    O `/readiness` montava os pre-requisitos assim:

        c.get("prerequisites") or await _prereqs_do_catalogo(...)

    A lista do planejador traz so `{code, name}`. Quem cruzava com a
    evidencia era o caminho ALTERNATIVO - entao o pre-requisito so chegava
    completo quando o planejador NAO o conhecia, que e o contrario do
    necessario. Medido: a base com 3 respostas e 0,667 de acerto chegava com
    `answered: None`, e o sistema mandava o aluno diagnosticar de novo o que
    acabara de responder.

    `answered` e `accuracy` viajam junto porque e com eles que
    `proximo_passo` distingue "ainda nao sei" de "ja medi, e falta".

    MASTERED, e so MASTERED, conta como dominado: READY quer dizer "da para
    seguir", nao "ja sabe" - foi confundir os dois que liberou, uma vez, um
    aluno com 0 de 5.
    """
    saida = []
    for p in (prereqs or []):
        codigo = (p or {}).get("code")
        if not codigo:
            # sem codigo nao da para cruzar com nada, e seguiria adiante como
            # pre-requisito fantasma
            continue
        c = por_conteudo.get(codigo) or {}
        saida.append({
            "code": codigo,
            "name": p.get("name") or c.get("content_name"),
            "mastered": c.get("content_state") == "MASTERED",
            "content_state": c.get("content_state"),
            "answered": c.get("questions_answered") or 0,
            "accuracy": c.get("accuracy"),
            # a jornada precisa saber se JA houve diagnostico da base - para
            # o aluno, e a mesma etapa
            "origin_breakdown": c.get("origin_breakdown") or {},
        })
    return saida


class AtividadeNaoVisivel(LookupError):
    """A atividade nao existe, ou nao e desta pessoa - a rota nao distingue os
    dois casos de proposito: dizer "existe, mas nao e sua" ja vaza algo."""


class ReadinessRouteService:
    """Responde, para uma atividade concreta: o aluno pode comecar?"""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def para_atividade(self, assignment_id: UUID, student_external_id: str, *,
                             requester) -> dict:
        """A rota, os conteudos exigidos e o alvo do proximo passo.

        ``target_content_code`` e o que o microdiagnostico deve perguntar: o
        pre-requisito em falta quando ha um, senao o proprio conteudo da
        atividade. E None quando nao ha o que perguntar - caso em que a tela
        diz isso, em vez de oferecer um botao que nao leva a lugar nenhum.
        """
        # importados aqui para nao criar ciclo com adaptive_practice
        from agente_ia_edu.services.activity_assignment_store import (
            ActivityAssignmentStore,
        )
        from agente_ia_edu.services.adaptive_learning_path import (
            AdaptiveLearningPathService,
        )
        from agente_ia_edu.services.proximo_passo import (
            PASSO_PRATICA, cta_para, jornada_de, passo_para,
        )
        from agente_ia_edu.services.trajetoria_do_aluno import (
            TENDENCIA_CONFIRMADA,
        )

        # `student_activities` ja resolve as duas coisas de que precisamos: a
        # AUTORIZACAO (so devolve o que e desta pessoa) e os `content_codes`
        # agregados. Buscar o assignment direto pularia a primeira.
        atividades = ActivityAssignmentStore(self._session)
        visiveis = await atividades.student_activities(requester=requester)
        atividade = next((a for a in visiveis
                          if str(a["assignment_id"]) == str(assignment_id)), None)
        if atividade is None:
            raise AtividadeNaoVisivel(str(assignment_id))
        exigidos: list[str] = list(atividade.get("content_codes") or [])

        caminho = await AdaptiveLearningPathService(self._session).build_path(
            student_external_id, requester=requester)
        por_conteudo = {c["content_code"]: c
                        for c in (caminho.get("steps", []) + caminho.get("mastered", []))}
        # O planejador so nomeia o que ja observou. Um conteudo sobre o qual
        # nao ha nada ainda sai dele sem nome, e a tela mostraria
        # "CHEMISTRY-PHYSICAL-STOICHIOMETRY" para o aluno - justamente no
        # primeiro acesso, que e quando isso acontece.
        nomes = await self._nomes_do_catalogo(exigidos)
        # Idem para os PRE-REQUISITOS: um conteudo que o planejador nunca
        # observou sai dele sem grafo nenhum, e `prerequisites` chega vazio
        # justamente no primeiro acesso - o momento em que mais importa saber
        # o que vem antes. O arco esta no catalogo o tempo todo.
        planejador = AdaptiveLearningPathService(self._session)

        detalhes = []
        for codigo in exigidos:
            c = por_conteudo.get(codigo)
            # Conteudo que o planejador nao devolveu e conteudo sobre o qual
            # nao ha nada observado - INSUFFICIENT_EVIDENCE, nao "liberado".
            detalhes.append({
                "content_code": codigo,
                "content_name": ((c or {}).get("content_name")
                                 or nomes.get(codigo) or codigo),
                "content_state": (c or {}).get("content_state") or "INSUFFICIENT_EVIDENCE",
                "answered": (c or {}).get("questions_answered") or 0,
                "accuracy": (c or {}).get("accuracy"),
                "origin_breakdown": (c or {}).get("origin_breakdown") or {},
                "unsatisfied_prerequisites": (c or {}).get("unsatisfied_prerequisites", []),
                # A evidencia e aplicada venha a lista de onde vier - do
                # planejador ou do catalogo. Era so no caminho do catalogo.
                "prerequisites": com_evidencia(
                    (c or {}).get("prerequisites")
                    or await self._prereqs_do_catalogo(
                        planejador, codigo, requester),
                    por_conteudo),
            })

        # A ROTA SAI DO PROXIMO PASSO, nao o contrario.
        #
        # Antes a rota era calculada dos estados e o alvo escolhido depois, o
        # que deixava um buraco: com evidencia FRACA sobre a base, a rota dava
        # DIAGNOSTIC e o alvo voltava a ser a base - o aluno rediagnosticava o
        # que ja tinha sido medido, para sempre. Agora quem decide e
        # `proximo_passo`, que olha a evidencia, e a rota e consequencia.
        # ONDE HA GRAFO, A SONDAGEM COMECA NO ALVO.
        #
        # Ver o comentario em `passo_para`: o grafo do alvo ja carrega as
        # habilidades basicas, entao subir ao conteudo ancestral vira uma
        # segunda volta na mesma pergunta - e foi assim que um aluno de QA
        # ficou 29 tentativas preso em Balanceamento sem nunca ser perguntado
        # sobre Estequiometria.
        from agente_ia_edu.services.grafos_pedagogicos import tem_contrato_v2

        passo = passo_para(detalhes, tem_grafo=tem_contrato_v2)

        # O ASSESSOR PEDAGOGICO. Quando o proximo passo e praticar um conteudo
        # em que o aluno ACABOU de ir mal, oferecer mais questoes e o que o
        # teste humano chamou de "responder questoes para sempre". Aqui o
        # passo pode virar ENSINO, GUIADA, VERIFICACAO ou ESCALONAMENTO.
        #
        # Quem decide e `assessor_pedagogico`, que e deterministico e nao
        # conhece provider de IA. Esta camada so junta o que ele precisa saber.
        #
        # O LACO EXISTE POR CAUSA DA RECUPERACAO CONFIRMADA. Quando a
        # trajetoria confirma que o aluno aprendeu o pre-requisito, ele deixa
        # de ser obstaculo - e o proximo passo passa a ser sobre o conteudo
        # SEGUINTE, que pode ter obstaculo proprio. Sem reperguntar, o aluno
        # ficaria parado num passo que ja nao existe. O limite e o numero de
        # conteudos: cada volta confirma um, e eles acabam.
        confirmados: set[str] = set()
        for _ in range(len(detalhes) + 1):
            if passo["kind"] != PASSO_PRATICA:
                break
            passo = await self._com_intervencao(
                passo, detalhes, student_external_id, requester=requester)
            intervencao = passo.get("intervention") or {}
            if (intervencao.get("action") is not None
                    or intervencao.get("trend") != TENDENCIA_CONFIRMADA):
                break
            confirmados.add(passo.get("content_code"))
            confirmado_agora = {"content_name": passo.get("content_name"),
                                "for_content_name": passo.get("for_content_name"),
                                "feedback": passo.get("feedback")}
            passo = passo_para(detalhes, confirmados=confirmados,
                               tem_grafo=tem_contrato_v2)
            # A CONFIRMACAO PRECISA SER DITA.
            #
            # Medido no navegador: ao confirmar, o aluno via "Voce acertou 3
            # de 3. Suas respostas foram registradas." - o texto de reserva.
            # O `feedback` so era montado para passos de intervencao, e
            # confirmar e justamente deixar de ter um. A frase do momento que
            # ele esperou o ciclo inteiro vinha vazia.
            passo.setdefault("feedback", confirmado_agora["feedback"])
        rota = passo["readiness_route"]

        # EM QUE PE ESTA O PASSO. Sem isto o CTA so sabia o TIPO da proxima
        # acao, e dizia "Responder" a quem acabara de responder.
        passo["state"], retomar = await self._estado_do_passo(
            passo, assignment_id, student_external_id, requester=requester)
        passo["cta"] = cta_para(passo["kind"], passo["state"])
        # QUAL retomar. Dizer "Continuar pratica" sem dizer qual levava a tela
        # a criar uma pratica NOVA, da questao 1, e a anterior - com as
        # respostas dentro - ficava inalcancavel. Nulo quando nao ha nada
        # aberto: oferecer retomar o inexistente e o mesmo erro ao contrario.
        passo["resume_assignment_id"] = retomar

        # A JORNADA que o aluno ve. Derivada do que ja esta gravado - as
        # origens de evidencia e o estado da tentativa - nunca da navegacao.
        # Agrega as origens do conteudo exigido E dos pre-requisitos: o
        # microdiagnostico do pre-requisito e evidencia da etapa "Diagnostico"
        # tanto quanto o do conteudo final - para o aluno e o mesmo passo.
        #
        # As origens vem do MAPA DE DOMINIO, nao do planejador: ele nao expoe
        # `origin_breakdown` (conferido), e sem isso a etapa "Diagnostico"
        # ficava cinza mesmo depois de o aluno ter feito dois diagnosticos.
        codigos = {d["content_code"] for d in detalhes}
        for d in detalhes:
            codigos.update(p["code"] for p in (d.get("prerequisites") or [])
                           if p.get("code"))
        origens = await self._origens_de(codigos, student_external_id,
                                         requester=requester)
        estado_atividade = await self._estado_da_atividade(
            assignment_id, requester=requester)
        jornada = jornada_de(origens=origens, estado_atividade=estado_atividade,
                             rota=rota, kind=passo["kind"])

        return {
            "assignment_id": str(assignment_id),
            "title": atividade.get("title"),
            "student_external_id": student_external_id,
            "readiness_route": rota,
            "required_contents": detalhes,
            "target_content_code": passo.get("content_code"),
            "target_content_name": passo.get("content_name"),
            # O passo concreto. O frontend TRADUZ isto; nao decide nada.
            "next_step": passo,
            "journey": jornada,
            # O ESTADO DA ATIVIDADE, independente do proximo passo. O selo
            # "Entregue" da Home vinha de `next_step.state`, e so havia
            # `next_step.state` de atividade enquanto o passo fosse ACTIVITY:
            # quem ia mal era mandado de volta ao diagnostico, o passo mudava
            # de tipo e a Home parava de dizer que a atividade fora entregue -
            # enquanto "Minhas atividades" dizia "Concluida". Duas telas, a
            # mesma entrega, respostas diferentes.
            "activity_state": estado_atividade,
            # a atividade NAO foi concluida por preparar-se para ela
            "objective_assignment_id": str(assignment_id),
            "objective_completed": False,
        }

    async def _com_intervencao(self, passo: dict, detalhes: list[dict],
                               aluno: str, *, requester) -> dict:
        """Transforma PRATICA em ENSINO quando o aluno precisa aprender antes.

        Tudo o que o assessor consulta ja existe e ja e gravado por outro
        motivo: o material publicado daquele conteudo (PHASE 23), a posicao de
        leitura do aluno (PHASE 25) e as praticas concluidas (PHASE 22). Nao
        ha persistencia nova nesta decisao.
        """
        from agente_ia_edu.services.assessor_pedagogico import (
            ACAO_ENSINAR, ACAO_ESCALAR, ACAO_GUIADA, ACAO_INVESTIGAR,
            ACAO_VERIFICAR, decidir_intervencao, habilidade_que_trava,
        )
        from agente_ia_edu.services.feedback_pedagogico import feedback_do_passo
        from agente_ia_edu.services.proximo_passo import (
            PASSO_ENSINO, PASSO_ESCALONAMENTO, PASSO_GUIADA,
            PASSO_INVESTIGACAO, PASSO_VERIFICACAO,
        )

        codigo = passo.get("content_code")
        if not codigo:
            return passo

        evidencia = self._evidencia_de(codigo, detalhes)
        habilidades = await self._habilidades_de(codigo, aluno, requester=requester)
        material = await self._material_de(codigo, requester=requester)
        ja_ensinado = bool(material) and await self._ja_estudou(
            material["material_id"], aluno, codigo, requester=requester)

        # A GUIADA E DECIDIDA PELO ASSESSOR, nao depois dele.
        #
        # Ate 2026-10-05 a guiada era escolhida AQUI, depois da decisao: a
        # maquina dizia PRATICAR e esta camada trocava por GUIADA se houvesse
        # item. Com a estrategia variando por ciclo, essa troca por fora
        # escondia da propria maquina uma das opcoes que ela precisa pesar.
        # O ALVO PASSA A SER O PRIMEIRO GARGALO, ONDE HA GRAFO.
        #
        # Sem grafo, `habilidade_que_trava` escolhe pelo MENOR ACERTO - e para
        # quem vai mal na leitura da formula (0/3) e pior no problema completo
        # (0/5), isso aponta o problema completo. O sistema entao ensina a
        # cadeia inteira a quem nao le o indice do NH3.
        #
        # Com grafo a pergunta muda: nao "qual esta pior", e sim "em qual
        # delas ele esta PRONTO para aprender agora". Conteudo sem contrato V2
        # segue exatamente como antes - 36 dos 37 do catalogo, hoje.
        from agente_ia_edu.services.grafos_pedagogicos import grafo_de

        grafo = grafo_de(codigo)
        alvo = habilidade_que_trava(habilidades, grafo=grafo)
        guiado = await self._guiada_pendente(
            codigo, alvo, aluno, requester=requester)
        # A INVESTIGACAO E O DEGRAU MAIS ALTO DA ESCADA DE APOIO.
        #
        # Ela e perguntada aqui, e nao depois da decisao, pelo mesmo motivo
        # que a guiada passou a ser: esconder uma opcao da maquina faz a
        # maquina decidir sem ela.
        investigando = await self._investigacao_pendente(
            codigo, alvo, aluno, requester=requester)

        intervencao = decidir_intervencao(
            alvo=alvo,
            alvo_nome=(grafo.rotulo(alvo) if (grafo and alvo) else None),
            habilidades=habilidades,
            banda_do_conteudo=self._banda(evidencia),
            ja_ensinado=ja_ensinado,
            praticas_concluidas=await self._praticas_concluidas(
                codigo, aluno, requester=requester),
            ha_material=bool(material),
            ha_guiada_pendente=guiado is not None,
            ha_investigacao_pendente=investigando,
            tentativas=await self._tentativas_de(
                codigo, aluno, requester=requester),
            objetivo_nome=passo.get("for_content_name"),
            conteudo_nome=passo.get("content_name"),
            ultima_foi_verificacao=await self._ultima_foi_verificacao(
                codigo, aluno, requester=requester),
        )
        passo["intervention"] = intervencao
        acao = intervencao.get("action")

        # O QUE O ALUNO LE, decidido aqui e nao no navegador.
        #
        # Ate 2026-10-05 a tela de resultado da pratica dizia "Voce acertou 1
        # de 5. Isso entra no seu progresso e ajusta o proximo passo." - duas
        # frases sobre o sistema, montadas no JavaScript. O aluno nao ficava
        # sabendo o que foi observado nem por que o proximo passo ajuda.
        passo["feedback"] = feedback_do_passo(
            action=acao,
            trend=intervencao.get("trend"),
            cycle=intervencao.get("cycle") or 1,
            skill_name=intervencao.get("skill_name"),
            content_name=passo.get("content_name"),
            objective_name=passo.get("for_content_name"),
            approach=intervencao.get("approach"))

        # INVESTIGACAO: uma micropergunta de cada vez, antes de a explicacao
        # ser gasta. O alvo viaja junto porque e ele que escolhe a cadeia -
        # a tela nao decide qual investigacao abrir.
        if acao == ACAO_INVESTIGAR:
            passo["kind"] = PASSO_INVESTIGACAO
            passo["skill"] = intervencao.get("skill")
            passo["readiness_route"] = ROTA_PREPARACAO
            return passo

        if acao == ACAO_ENSINAR and material:
            passo["kind"] = PASSO_ENSINO
            passo["material_id"] = material["material_id"]
            passo["material_title"] = material["title"]
            # Ensinar e preparar: a jornada precisa acender "Preparacao", e
            # sem isto a rota continuaria dizendo que o passo e diagnostico.
            passo["readiness_route"] = ROTA_PREPARACAO
            return passo

        # PRATICA GUIADA, entre o ensino e a pratica autonoma.
        #
        # Quem acabou de estudar nao deve ir direto para cinco questoes
        # sozinho: ha uma etapa em que ele TENTA e a ajuda chega quando
        # precisa. Ela so entra se houver item guiado para a lacuna - e se o
        # aluno ainda nao a concluiu, porque concluir a guiada e justamente
        # o sinal de "agora tente sozinho".
        if acao == ACAO_GUIADA and guiado is not None:
            passo["kind"] = PASSO_GUIADA
            passo["item_key"] = guiado["item_key"]
            passo["readiness_route"] = ROTA_PREPARACAO
            return passo

        # VERIFICACAO: curta, e e ela que decide se ele avanca.
        #
        # Nao e "mais pratica com outro nome": sao poucas questoes, pedidas
        # porque ele acabou de ir BEM depois de ir mal, e o unico jeito
        # honesto de confirmar isso e ele responder de novo.
        if acao == ACAO_VERIFICAR:
            passo["kind"] = PASSO_VERIFICACAO
            passo["readiness_route"] = ROTA_PREPARACAO
            passo["question_count"] = QUESTOES_DA_VERIFICACAO
            return passo

        # ESCALONAMENTO: acabaram os ciclos, e o proximo movimento nao e do
        # sistema. Continuar oferecendo lotes de questoes aqui seria repetir
        # pela quarta vez o que ja nao funcionou tres.
        if acao == ACAO_ESCALAR:
            passo["kind"] = PASSO_ESCALONAMENTO
            passo["readiness_route"] = ROTA_PREPARACAO
            return passo

        return passo

    async def _investigacao_pendente(self, codigo: str, skill: str | None,
                                     aluno: str, *, requester) -> bool:
        """A cadeia de microperguntas daquela lacuna ainda esta de pe?

        Lacuna sem cadeia escrita devolve False e o aluno segue para o degrau
        de baixo: inventar uma investigacao generica seria perguntar coisas
        cujo resultado o sistema nao saberia interpretar.
        """
        from agente_ia_edu.services.servico_de_investigacao import (
            InvestigacaoService,
        )

        if not skill:
            return False
        try:
            return await InvestigacaoService(self._session).pendente(
                aluno, codigo, skill, requester=requester)
        except Exception:  # noqa: BLE001 - na duvida, nao prender o aluno
            return False

    async def _guiada_pendente(self, codigo: str, skill: str | None, aluno: str,
                               *, requester) -> dict | None:
        """O item guiado daquela lacuna, se houver um e ele ainda nao foi feito.

        Conteudo sem item guiado devolve None e o aluno segue para a pratica
        comum: inventar uma pratica guiada generica seria pior que nao ter.
        """
        from agente_ia_edu.services.itens_guiados import item_para
        from agente_ia_edu.services.pratica_guiada import PraticaGuiadaService

        item = item_para(codigo, skill)
        if item is None:
            return None
        try:
            estado = await PraticaGuiadaService(self._session).estado(
                aluno, item["key"], requester=requester)
        except Exception:  # noqa: BLE001 - sem registro: ainda nao fez
            return {"item_key": item["key"]}
        if estado.get("completed"):
            return None
        return {"item_key": item["key"]}

    def _banda(self, evidencia: dict) -> str:
        from agente_ia_edu.services.pedagogical_analysis import (
            PerformanceThresholdPolicy,
        )
        return PerformanceThresholdPolicy.default().band(
            answered=int(evidencia.get("answered") or 0),
            accuracy=evidencia.get("accuracy"))

    @staticmethod
    def _evidencia_de(codigo: str, detalhes: list[dict]) -> dict:
        """A evidencia daquele codigo, seja ele o conteudo exigido ou um
        pre-requisito dele - os dois ja chegam enriquecidos."""
        for c in detalhes:
            if c.get("content_code") == codigo:
                return c
            for p in c.get("prerequisites") or []:
                if p.get("code") == codigo:
                    return p
        return {}

    async def _material_de(self, codigo: str, *, requester) -> dict | None:
        """O material publicado daquele conteudo, se houver.

        Nao havendo, o assessor manda praticar: prometer uma explicacao que
        nao existe seria pior que a pratica.
        """
        from agente_ia_edu.services.student_material import StudentMaterialService

        try:
            materiais = await StudentMaterialService(self._session).list_materials(
                requester_school_id=getattr(requester, "school_id", None),
                content_code=codigo)
        except Exception:  # noqa: BLE001 - sem material: segue para a pratica
            return None
        return materiais[0] if materiais else None

    async def _ja_estudou(self, material_id: str, aluno: str, codigo: str, *,
                          requester) -> bool:
        """O aluno concluiu aquela explicacao, E AINDA NAO TENTOU DESDE ENTAO?

        `MaterialProgress` existe desde a PHASE 25 e guarda POSICAO - nada de
        dominio. E justamente por nao ser evidencia que serve aqui: diz o que
        ele ja viu, sem dizer que ele aprendeu.

        COMPLETED, nao `started`: quem apenas abriu e saiu precisa VOLTAR para
        a explicacao, no ponto onde parou - mandar esse aluno para a pratica
        seria perder o ensino no meio. COMPLETED e ele dizendo "entendi".

        E O ESTUDO ENVELHECE. `MaterialProgress` fica COMPLETED para sempre,
        entao a explicacao nunca mais voltava e, depois da primeira aula, so
        restavam questoes - o "responder questoes para sempre" de novo, agora
        com uma aula no inicio. Medido no navegador: estudou, praticou 1 de 5,
        e o passo seguinte era praticar, e praticar, e praticar.

        Um estudo vale enquanto nada foi tentado depois dele. Praticou e
        continuou mal? Entao aquela leitura nao bastou, e rever vale mais que
        repetir - e e isso que faz ENSINAR e PRATICAR alternarem em vez de uma
        das duas virar um lacete.
        """
        if await self._estado_do_estudo(material_id, aluno,
                                        requester=requester) != "COMPLETED":
            return False
        lido = await self._quando_estudou(material_id, aluno, requester=requester)
        tentou = await self._ultima_pratica(codigo, aluno, requester=requester)
        if lido is None or tentou is None:
            return True
        return lido >= tentou

    async def _quando_estudou(self, material_id: str, aluno: str, *, requester):
        from uuid import UUID as _U

        from agente_ia_edu.services.student_material import StudentMaterialService

        try:
            progresso = await StudentMaterialService(self._session).get_progress(
                _U(material_id), aluno,
                requester_school_id=getattr(requester, "school_id", None))
        except Exception:  # noqa: BLE001
            return None
        return self._quando(progresso.get("updated_at"))

    async def _ultima_pratica(self, codigo: str, aluno: str, *, requester):
        """Quando foi a ultima pratica CONCLUIDA daquele conteudo."""
        from agente_ia_edu.services.adaptive_practice import AdaptivePracticeService
        from agente_ia_edu.services.curriculum_domain_map import ORIGIN_PRACTICE

        try:
            praticas = await AdaptivePracticeService(self._session).list_practices(
                aluno, requester=requester)
        except Exception:  # noqa: BLE001
            return None
        datas = [
            self._quando(p.get("created_at"))
            for p in (praticas.get("items") or [])
            if p.get("content_code") == codigo
            and (p.get("origin") or ORIGIN_PRACTICE) == ORIGIN_PRACTICE
            and any(x in (p.get("state") or "") for x in ("COMPLETED", "CORRECTED"))
        ]
        validas = [d for d in datas if d is not None]
        return max(validas) if validas else None

    @staticmethod
    def _quando(iso: str | None):
        from datetime import datetime, timezone

        if not iso:
            return None
        try:
            d = datetime.fromisoformat(iso)
        except ValueError:  # pragma: no cover - formato inesperado
            return None
        # Sem fuso nao da para comparar com um que tem: assume UTC, que e o
        # que o resto do sistema grava.
        return d if d.tzinfo else d.replace(tzinfo=timezone.utc)

    async def _estado_do_estudo(self, material_id: str, aluno: str, *,
                                requester) -> str:
        """NOT_STARTED / IN_PROGRESS / COMPLETED da leitura."""
        from uuid import UUID as _U

        from agente_ia_edu.services.student_material import StudentMaterialService

        try:
            progresso = await StudentMaterialService(self._session).get_progress(
                _U(material_id), aluno,
                requester_school_id=getattr(requester, "school_id", None))
        except Exception:  # noqa: BLE001 - sem progresso: ainda nao estudou
            return "NOT_STARTED"
        return (progresso or {}).get("status") or "NOT_STARTED"

    async def _praticas_concluidas(self, codigo: str, aluno: str, *, requester) -> int:
        from agente_ia_edu.services.adaptive_practice import AdaptivePracticeService

        try:
            praticas = await AdaptivePracticeService(self._session).list_practices(
                aluno, requester=requester)
        except Exception:  # noqa: BLE001 - sem praticas: ciclo zero
            return 0
        # SO PRATICAS. `list_practices` devolve praticas E microdiagnosticos -
        # os dois sao assignments do mesmo tipo, e o diagnostico sai de la com
        # `PRACTICE_CORRECTED`. Conta-lo como ciclo fazia o primeiro ensino ja
        # comecar no ciclo 2, e aproximava o teto de intervencoes sem o aluno
        # ter recebido nenhuma.
        from agente_ia_edu.services.curriculum_domain_map import ORIGIN_PRACTICE

        return sum(
            1 for p in (praticas.get("items") or [])
            if p.get("content_code") == codigo
            and (p.get("origin") or ORIGIN_PRACTICE) == ORIGIN_PRACTICE
            and any(x in (p.get("state") or "") for x in ("COMPLETED", "CORRECTED"))
        )

    async def _ultima_foi_verificacao(self, codigo: str, aluno: str, *,
                                      requester) -> bool:
        """A ÚLTIMA tentativa concluída daquele conteúdo era uma verificação?

        Importa porque uma verificação que falha não diz "treine mais": diz
        que a intervenção anterior não bastou. Sem este sinal o assessor lia
        o 0/3 como mais uma prática fraca e oferecia outro lote - medido em
        2026-10-06 no caminho real da decisão.

        O propósito vem dos metadados que o assignment já carrega
        (`purpose`), gravados no momento da criação. Quem não informou
        continua sendo prática, então o histórico antigo não muda de
        significado retroativamente.
        """
        from agente_ia_edu.services.adaptive_practice import (
            PROPOSITO_PRATICA,
            PROPOSITO_VERIFICACAO,
            AdaptivePracticeService,
        )
        from agente_ia_edu.services.curriculum_domain_map import ORIGIN_PRACTICE

        try:
            praticas = await AdaptivePracticeService(self._session).list_practices(
                aluno, requester=requester)
        except Exception:  # noqa: BLE001 - sem praticas: nao houve verificacao
            return False
        concluidas = [
            p for p in (praticas.get("items") or [])
            if p.get("content_code") == codigo
            and (p.get("origin") or ORIGIN_PRACTICE) == ORIGIN_PRACTICE
            and any(x in (p.get("state") or "") for x in ("COMPLETED", "CORRECTED"))
        ]
        if not concluidas:
            return False
        ultima = max(concluidas, key=lambda p: str(p.get("created_at") or ""))
        return (ultima.get("purpose") or PROPOSITO_PRATICA) == PROPOSITO_VERIFICACAO

    async def _itens_respondidos(self, codigo: str, aluno: str, *,
                                 requester) -> list:
        """Toda resposta ja corrigida daquele conteudo, com o resultado a que
        pertence e quando ele foi corrigido.

        UMA consulta, dois consumidores: a micro-habilidade que falhou
        (`_habilidades_de`) e a trajetoria (`_tentativas_de`). Antes so havia o
        primeiro, e ele jogava fora a ordem - que e justamente o que faltava
        para o assessor perceber que o aluno estava melhorando.
        """
        from uuid import UUID as _U

        from sqlalchemy import select as _sel

        from agente_ia_edu.db.models import ActivityResult, ActivityResultItem
        from agente_ia_edu.services.adaptive_practice import AdaptivePracticeService

        try:
            praticas = await AdaptivePracticeService(self._session).list_practices(
                aluno, requester=requester)
        except Exception:  # noqa: BLE001
            return []
        ids = [p.get("assignment_id") for p in (praticas.get("items") or [])
               if p.get("content_code") == codigo and p.get("assignment_id")]
        if not ids:
            return []
        try:
            alvos = [_U(str(i)) for i in ids]
        except (TypeError, ValueError):  # pragma: no cover - id improvavel
            return []

        resultados = (await self._session.execute(
            _sel(ActivityResult.id, ActivityResult.corrected_at).where(
                ActivityResult.assignment_id.in_(alvos),
                ActivityResult.student_external_id == aluno))).all()
        if not resultados:
            return []
        quando = {r[0]: r[1] for r in resultados}
        itens = (await self._session.execute(
            _sel(ActivityResultItem).where(
                ActivityResultItem.result_id.in_(list(quando))))).scalars().all()
        return [{"result_id": str(i.result_id),
                 "corrected_at": quando.get(i.result_id),
                 "question_version_id": i.question_version_id,
                 "is_correct": bool(i.is_correct)} for i in itens]

    async def _tentativas_de(self, codigo: str, aluno: str, *,
                             requester) -> list[dict]:
        """A trajetoria: uma entrada por tentativa, em ordem cronologica.

        E isto que permite distinguir "nunca soube" de "esta aprendendo". A
        media acumulada nao distingue: medido em 2026-10-05, um aluno com 0/3,
        1/5, 4/5 e 5/5 ficava em 0,556 e continuava recebendo a mesma
        explicacao.
        """
        from agente_ia_edu.services.trajetoria_do_aluno import (
            tentativas_de_resultados,
        )

        return tentativas_de_resultados(
            await self._itens_respondidos(codigo, aluno, requester=requester))

    async def _habilidades_de(self, codigo: str, aluno: str, *, requester) -> dict:
        """Qual MICRO-habilidade falhou, quando a amostra sustenta dizer.

        Sem isto a intervencao fala do conteudo inteiro ("balanceamento"); com
        isto ela fala do ponto ("a conservacao dos atomos"). O modulo que
        agrega ja cala sozinho quando a amostra nao sustenta - nao se inventa
        granularidade.
        """
        from sqlalchemy import select as _sel

        from agente_ia_edu.db.models.pedagogical import (
            PedagogicalClassification as _PC,
        )
        from agente_ia_edu.services.diagnostico_por_habilidade import (
            diagnostico_por_habilidade,
        )

        vazio = {"por_habilidade": {}, "suficiente": False, "texto": None}
        itens = await self._itens_respondidos(codigo, aluno, requester=requester)
        if not itens:
            return vazio
        vids = {i["question_version_id"] for i in itens}
        skills = dict((await self._session.execute(
            _sel(_PC.question_version_id, _PC.subcontent).where(
                _PC.question_version_id.in_(vids),
                _PC.lifecycle == "ACTIVE"))).all())
        return diagnostico_por_habilidade(
            [{"diagnostic_skill": skills.get(i["question_version_id"]),
              "is_correct": i["is_correct"]} for i in itens])

    async def _origens_de(self, codigos, aluno: str, *, requester) -> dict[str, int]:
        """Quantas evidencias de cada ORIGEM existem nestes conteudos.

        Uma consulta ao mapa de dominio, nao uma por conteudo: a jornada
        aparece em toda abertura de sessao.
        """
        from agente_ia_edu.services.curriculum_domain_map import (
            CurriculumDomainMapService,
        )

        if not codigos:
            return {}
        try:
            mapa = await CurriculumDomainMapService(self._session).get_map(
                aluno, requester=requester)
        except Exception:  # noqa: BLE001 - sem mapa: jornada sem etapa verde
            return {}
        total: dict[str, int] = {}
        for disciplina in (mapa.get("disciplines") or []):
            for c in (disciplina.get("contents") or []):
                if c.get("content_code") not in codigos:
                    continue
                for origem, n in (c.get("origin_breakdown") or {}).items():
                    total[origem] = total.get(origem, 0) + int(n or 0)
        return total

    async def _estado_da_atividade(self, assignment_id, *, requester) -> str:
        """So o estado da tentativa da ATIVIDADE, para a jornada - que precisa
        dele mesmo quando o proximo passo e outro."""
        from agente_ia_edu.services.activity_player_store import (
            ActivityPlayerStore, PlayerError,
        )
        from agente_ia_edu.services.proximo_passo import ESTADO_NAO_INICIADO

        try:
            estado = await ActivityPlayerStore(self._session).get_state(
                assignment_id, requester=requester)
        except (PlayerError, LookupError, PermissionError):
            return ESTADO_NAO_INICIADO
        return estado.get("status") or ESTADO_NAO_INICIADO

    async def _estado_do_passo(self, passo: dict, assignment_id, aluno: str, *,
                               requester) -> tuple[str, str | None]:
        """(estado, o que retomar) para o passo em questao.

        Estado e NAO_INICIADO / EM_ANDAMENTO / CONCLUIDO. Para a ATIVIDADE, e
        o da tentativa dela. Para diagnostico e pratica, e o da ultima pratica
        ABERTA daquele conteudo - se nao houver nenhuma, o aluno ainda nao
        comecou.

        O segundo valor so existe quando ha algo EM ANDAMENTO, e e a atividade
        que o aluno deve reabrir. Este metodo ja localizava essa pratica para
        responder EM_ANDAMENTO e descartava o identificador; quem chamava
        ficava sabendo que havia o que continuar, mas nao o que.

        Nada aqui decide pedagogia: so reporta o que ja esta gravado, para que
        o botao possa dizer a verdade - e levar aonde diz.
        """
        from agente_ia_edu.services.activity_player_store import (
            ActivityPlayerStore, PlayerError,
        )
        from agente_ia_edu.services.proximo_passo import (
            ESTADO_CONCLUIDO, ESTADO_EM_ANDAMENTO, ESTADO_NAO_INICIADO,
            PASSO_ATIVIDADE, PASSO_DIAGNOSTICO, PASSO_ENSINO, PASSO_PRATICA,
        )

        kind = passo.get("kind")
        if kind == PASSO_ENSINO:
            # O estado do ENSINO e o da leitura. Sem isto o passo cairia na
            # regra da pratica e diria "Entender o conceito" a quem esta no
            # meio da explicacao.
            material = passo.get("material_id")
            if not material:
                return ESTADO_NAO_INICIADO, None
            estado = await self._estado_do_estudo(material, aluno,
                                                  requester=requester)
            if estado == "COMPLETED":
                # Um estudo CONCLUIDO mas ja superado por uma tentativa nao e
                # mais "concluido" para este ciclo - o passo voltou a ser
                # ensinar justamente porque aquela leitura nao bastou. Sem
                # esta linha o botao dizia "Praticar agora" sobre um passo de
                # estudar: botao e destino apontando para lados diferentes.
                vale = await self._ja_estudou(
                    material, aluno, passo.get("content_code") or "",
                    requester=requester)
                return (ESTADO_CONCLUIDO if vale else ESTADO_NAO_INICIADO), None
            return (ESTADO_EM_ANDAMENTO if estado == "IN_PROGRESS"
                    else ESTADO_NAO_INICIADO), None

        if kind == PASSO_ATIVIDADE:
            try:
                estado = await ActivityPlayerStore(self._session).get_state(
                    assignment_id, requester=requester)
            except (PlayerError, LookupError, PermissionError):
                return ESTADO_NAO_INICIADO, None
            # A atividade e a que o aluno ja esta olhando: nao ha "qual".
            return estado.get("status") or ESTADO_NAO_INICIADO, None

        if kind not in (PASSO_DIAGNOSTICO, PASSO_PRATICA):
            return ESTADO_NAO_INICIADO, None

        from agente_ia_edu.services.adaptive_practice import AdaptivePracticeService

        try:
            praticas = await AdaptivePracticeService(self._session).list_practices(
                aluno, requester=requester)
        except Exception:  # noqa: BLE001 - sem praticas: nao comecou
            return ESTADO_NAO_INICIADO, None

        alvo = passo.get("content_code")
        minhas = [p for p in (praticas.get("items") or [])
                  if p.get("content_code") == alvo]
        if not minhas:
            return ESTADO_NAO_INICIADO, None
        # A mais recente primeiro (list_practices ordena por created_at desc).
        estado = (minhas[0].get("state") or "")
        if "IN_PROGRESS" in estado:
            return ESTADO_EM_ANDAMENTO, str(minhas[0].get("assignment_id") or "") or None
        if "COMPLETED" in estado or "CORRECTED" in estado:
            # PRATICA CONCLUIDA NAO CONCLUI O PASSO DE PRATICAR.
            #
            # Se o passo AINDA e praticar, e porque a evidencia nao bastou: o
            # sistema esta pedindo outra pratica, nao a mesma. Dizer CONCLUIDO
            # fazia o botao sair "Continuar" logo depois de o aluno terminar a
            # explicacao - vago justamente onde ele precisa saber para onde
            # vai. Para o DIAGNOSTICO continua valendo o contrario: "voce ja
            # fez este diagnostico" e um fato sobre o que ele fez.
            if kind == PASSO_PRATICA:
                return ESTADO_NAO_INICIADO, None
            return ESTADO_CONCLUIDO, None
        # PRACTICE_CREATED: existe, mas o aluno nao abriu - para ele, nao
        # comecou. "Continuar" sobre algo que ele nunca viu seria mentira.
        return ESTADO_NAO_INICIADO, None

    @staticmethod
    async def _prereqs_do_catalogo(planejador, codigo, requester) -> list[dict]:
        """Os pre-requisitos diretos pelo catalogo - so os arcos, crus.

        A evidencia nao entra aqui: ela entra em `com_evidencia`, que e
        aplicada tambem a lista que vem do planejador.
        """
        try:
            resolvido = await planejador.resolve_prerequisites(codigo, requester=requester)
        except Exception:  # noqa: BLE001 - conteudo fora do catalogo
            return []
        return list(resolvido.get("prerequisites") or [])

    async def _nomes_do_catalogo(self, codigos: Sequence[str]) -> dict[str, str]:
        if not codigos:
            return {}
        from sqlalchemy import select

        from agente_ia_edu.db.models.catalog import CatalogNode

        linhas = (await self._session.execute(
            select(CatalogNode.code, CatalogNode.name)
            .where(CatalogNode.code.in_(list(codigos))))).all()
        return {c: n for c, n in linhas if n}

    @staticmethod
    def _alvo(rota: str, detalhes: Sequence[dict]) -> tuple[str | None, str | None]:
        if rota == ROTA_PREPARACAO:
            for d in detalhes:
                for p in d.get("unsatisfied_prerequisites") or []:
                    return p.get("code"), p.get("name") or p.get("code")
            return None, None
        if rota == ROTA_DIAGNOSTICO:
            for d in detalhes:
                if d["content_state"] in ESTADOS_QUE_LIBERAM:
                    continue
                # COMECAR PELA BASE.
                #
                # O planejador so diz BLOCKED_BY_PREREQUISITE quando ja tem
                # evidencia de que o pre-requisito e fraco. No primeiro
                # acesso nao ha evidencia de NADA, e o conteudo final sai
                # como INSUFFICIENT_EVIDENCE sem bloqueio - o que mandaria
                # diagnosticar Estequiometria em quem talvez nao saiba
                # balancear uma equacao.
                #
                # Perguntar primeiro o que vem antes serve aos dois casos: se
                # ele souber, a conversa sobe sozinha; se nao souber, a
                # resposta ja e a que precisavamos. Entre dois conteudos
                # igualmente desconhecidos, o de baixo informa mais.
                base = next((p for p in (d.get("prerequisites") or [])
                             if not p.get("mastered")), None)
                if base and base.get("code"):
                    return base["code"], base.get("name") or base["code"]
                return d["content_code"], d["content_name"]
            return None, None
        return None, None


__all__ = [
    "AtividadeNaoVisivel",
    "ReadinessRouteService",
    "rota_de_estados",
    "ROTA_DIRETA",
    "ROTA_DIAGNOSTICO",
    "ROTA_PREPARACAO",
]
