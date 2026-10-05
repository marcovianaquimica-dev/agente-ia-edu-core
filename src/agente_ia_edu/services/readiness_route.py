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
        passo = passo_para(detalhes)
        rota = passo["readiness_route"]

        # O ASSESSOR PEDAGOGICO. Quando o proximo passo e praticar um conteudo
        # em que o aluno ACABOU de ir mal, oferecer mais questoes e o que o
        # teste humano chamou de "responder questoes para sempre". Aqui o
        # passo pode virar ENSINO - uma explicacao, um exemplo - antes da
        # proxima tentativa.
        #
        # Quem decide e `assessor_pedagogico`, que e deterministico e nao
        # conhece provider de IA. Esta camada so junta o que ele precisa saber.
        if passo["kind"] == PASSO_PRATICA:
            passo = await self._com_intervencao(
                passo, detalhes, student_external_id, requester=requester)
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
            ACAO_ENSINAR, decidir_intervencao,
        )
        from agente_ia_edu.services.proximo_passo import PASSO_ENSINO

        codigo = passo.get("content_code")
        if not codigo:
            return passo

        evidencia = self._evidencia_de(codigo, detalhes)
        habilidades = await self._habilidades_de(codigo, aluno, requester=requester)
        material = await self._material_de(codigo, requester=requester)
        ja_ensinado = bool(material) and await self._ja_estudou(
            material["material_id"], aluno, requester=requester)

        intervencao = decidir_intervencao(
            habilidades=habilidades,
            banda_do_conteudo=self._banda(evidencia),
            ja_ensinado=ja_ensinado,
            praticas_concluidas=await self._praticas_concluidas(
                codigo, aluno, requester=requester),
            ha_material=bool(material),
            objetivo_nome=passo.get("for_content_name"),
            conteudo_nome=passo.get("content_name"),
        )
        passo["intervention"] = intervencao
        if intervencao.get("action") == ACAO_ENSINAR and material:
            passo["kind"] = PASSO_ENSINO
            passo["material_id"] = material["material_id"]
            passo["material_title"] = material["title"]
            # Ensinar e preparar: a jornada precisa acender "Preparacao", e
            # sem isto a rota continuaria dizendo que o passo e diagnostico.
            passo["readiness_route"] = ROTA_PREPARACAO
        return passo

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

    async def _ja_estudou(self, material_id: str, aluno: str, *, requester) -> bool:
        """O aluno CONCLUIU aquela explicacao?

        `MaterialProgress` existe desde a PHASE 25 e guarda POSICAO - nada de
        dominio. E justamente por nao ser evidencia que serve aqui: diz o que
        ele ja viu, sem dizer que ele aprendeu.

        COMPLETED, nao `started`: quem apenas abriu e saiu precisa VOLTAR para
        a explicacao, no ponto onde parou - mandar esse aluno para a pratica
        seria perder o ensino no meio. COMPLETED e ele dizendo "entendi".
        """
        return await self._estado_do_estudo(material_id, aluno,
                                            requester=requester) == "COMPLETED"

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
        return sum(
            1 for p in (praticas.get("items") or [])
            if p.get("content_code") == codigo
            and any(x in (p.get("state") or "") for x in ("COMPLETED", "CORRECTED"))
        )

    async def _habilidades_de(self, codigo: str, aluno: str, *, requester) -> dict:
        """Qual MICRO-habilidade falhou, quando a amostra sustenta dizer.

        Sem isto a intervencao fala do conteudo inteiro ("balanceamento"); com
        isto ela fala do ponto ("a conservacao dos atomos"). O modulo que
        agrega ja cala sozinho quando a amostra nao sustenta - nao se inventa
        granularidade.
        """
        from sqlalchemy import select as _sel

        from agente_ia_edu.db.models import ActivityResult, ActivityResultItem
        from agente_ia_edu.db.models.pedagogical import (
            PedagogicalClassification as _PC,
        )
        from agente_ia_edu.services.adaptive_practice import AdaptivePracticeService
        from agente_ia_edu.services.diagnostico_por_habilidade import (
            diagnostico_por_habilidade,
        )

        vazio = {"por_habilidade": {}, "suficiente": False, "texto": None}
        try:
            praticas = await AdaptivePracticeService(self._session).list_practices(
                aluno, requester=requester)
        except Exception:  # noqa: BLE001
            return vazio
        ids = [p.get("assignment_id") for p in (praticas.get("items") or [])
               if p.get("content_code") == codigo and p.get("assignment_id")]
        if not ids:
            return vazio

        from uuid import UUID as _U

        try:
            alvos = [_U(str(i)) for i in ids]
        except (TypeError, ValueError):  # pragma: no cover - id improvavel
            return vazio

        resultados = (await self._session.execute(
            _sel(ActivityResult.id).where(
                ActivityResult.assignment_id.in_(alvos),
                ActivityResult.student_external_id == aluno))).scalars().all()
        if not resultados:
            return vazio
        itens = (await self._session.execute(
            _sel(ActivityResultItem).where(
                ActivityResultItem.result_id.in_(list(resultados))))).scalars().all()
        if not itens:
            return vazio
        vids = {i.question_version_id for i in itens}
        skills = dict((await self._session.execute(
            _sel(_PC.question_version_id, _PC.subcontent).where(
                _PC.question_version_id.in_(vids),
                _PC.lifecycle == "ACTIVE"))).all())
        return diagnostico_por_habilidade(
            [{"diagnostic_skill": skills.get(i.question_version_id),
              "is_correct": bool(i.is_correct)} for i in itens])

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
            return ({"IN_PROGRESS": ESTADO_EM_ANDAMENTO,
                     "COMPLETED": ESTADO_CONCLUIDO}.get(
                        estado, ESTADO_NAO_INICIADO), None)

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
