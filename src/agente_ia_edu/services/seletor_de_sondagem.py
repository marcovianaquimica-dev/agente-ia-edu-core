"""O SELETOR DE INSTRUMENTOS - a ponte entre o cerebro e o acervo.

ONDE ELE FICA
==============
    plano_de_sondagem        QUAIS micro-habilidades investigar  (o grafo)
    instrumento_de_sondagem  O QUE faz de um item um instrumento (o contrato)
    este modulo              CARREGA o acervo e aplica o contrato
    micro_diagnostic         usa o resultado para montar a sondagem

As tres primeiras responsabilidades nao se misturam de proposito. Este
modulo e o unico que conhece banco, e ele nao decide pedagogia nenhuma: ele
recebe a habilidade ja escolhida e devolve o instrumento, com a origem.

POR QUE ELE REUSA O QUESTION BANK
==================================
`QuestionBank.list_questions` ja sabe o que uma questao e: numero oficial,
dependencia visual, item protegido, alternativas, estado da classificacao.
Refazer essa leitura aqui criaria uma segunda definicao de "questao
utilizavel", e as duas divergiriam no primeiro ajuste.

O que o banco NAO expoe e o que o contrato precisa. Medido em 2026-10-07,
publicando os cinco itens num banco limpo e lendo pelo
`CurriculumClassificationView`:

    content_code      CHEMISTRY-PHYSICAL-STOICHIOMETRY   ok
    subcontent_code   None                               <-
    options           []                                 <-

`subcontent_code` vem nulo porque a view resolve o subconteudo contra o
CATALOGO, e micro-habilidade nao e no do curriculo - `MASSA_MOLAR` vive no
grafo pedagogico, nao na arvore curricular. E as alternativas nao sao
carregadas nessa listagem.

Entao a micro-habilidade, a finalidade, a proveniencia, quem validou e a
contagem de alternativas validas saem de uma consulta propria, por
`question_version_id`. Do banco ficam as tres coisas que so ele sabe:
numero oficial, item protegido e dependencia visual.

Nao mexi no `CurriculumClassificationView`: ele e lido por muita coisa, e
mudar a forma dele por causa deste bloco custaria mais do que a consulta.

O QUE ELE DEVOLVE, E POR QUE ISSO IMPORTA
==========================================
Uma `Escolha` por habilidade, com `origem` (CURATED/FALLBACK) e `motivo`.
Isso e o §16: sem a origem viajando como DADO, descobrir qual item foi
servido e por que exigiria ler log humano ou inspecionar a tela - e nenhuma
das duas coisas e verificavel.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import PedagogicalClassification
from agente_ia_edu.services.instrumento_de_sondagem import (
    Candidato,
    Escolha,
    compativel,
    escolher,
)
from agente_ia_edu.services.question_bank import (
    QuestionBankFilters,
    QuestionBankService,
)

# Quantas questoes do conteudo olhar. O mesmo teto que a pratica ja usa -
# um conteudo do acervo nao chega perto disso (Estequiometria tem 25).
_TETO_DE_LEITURA = 200


class SeletorDeSondagem:
    """Escolhe o instrumento de cada micro-habilidade, deterministicamente."""

    def __init__(self, session: AsyncSession, *,
                 bank: QuestionBankService | None = None) -> None:
        self._session = session
        self._bank = bank or QuestionBankService(session)

    # -- carga -------------------------------------------------------------

    async def candidatos(self, conteudo: str, *,
                         escola_do_aluno: str | None = None
                         ) -> list[Candidato]:
        """Todo item do conteudo, traduzido para o que o contrato julga.

        Nao filtra por habilidade: quem filtra e o contrato, e assim uma so
        leitura do acervo serve a todas as habilidades do plano.
        """
        await self._bank._load_catalog()
        pagina = await self._bank.list_questions(
            QuestionBankFilters(content_code=conteudo,
                                classification_state="ANY_CLASSIFIED"),
            page=1, page_size=_TETO_DE_LEITURA,
            order_by="official_number", order_direction="asc")
        itens = list(pagina.items)
        if not itens:
            return []

        extras = await self._extras({it.question_version_id for it in itens})
        saida: list[Candidato] = []
        for it in itens:
            cls = it.classification
            extra = extras.get(str(it.question_version_id), {})
            saida.append(Candidato(
                question_version_id=str(it.question_version_id),
                # Conteudo e habilidade vem da CLASSIFICACAO crua, nao da
                # view: ver o cabecalho sobre `subcontent_code` nulo.
                conteudo=extra.get("conteudo"),
                habilidade=extra.get("habilidade"),
                finalidade=extra.get("finalidade"),
                lifecycle=extra.get("lifecycle"),
                provenance=extra.get("provenance"),
                validado_por=extra.get("validado_por"),
                status_da_questao=extra.get("status_da_questao"),
                visibilidade=extra.get("visibilidade"),
                escola_id=extra.get("escola_id"),
                # O que "gabarito definido" quer dizer depende do FORMATO, e
                # e aqui - no carregador, que conhece o formato - que isso e
                # decidido. Para um item de alternativas, e haver exatamente
                # uma valida: nenhuma nao da para conferir, e duas tornam a
                # conferencia ambigua. O contrato recebe so o booleano.
                gabarito_definido=extra.get("alternativas_validas") == 1,
                dependencia_visual=bool(it.has_visual_dependency
                                        or (cls.visual_dependency if cls else False)),
                protegida=bool(it.is_protected),
                numero_oficial=it.official_number,
            ))
        return saida

    async def _extras(self, version_ids: Iterable) -> dict[str, dict]:
        """Tudo o que o contrato precisa e a listagem do banco nao devolve.

        Vem da classificacao ATIVA de cada versao: uma classificacao
        aposentada nao pode qualificar um item que foi reclassificado desde
        entao. Sem classificacao ativa, a versao simplesmente nao aparece no
        dicionario - e o candidato nasce sem habilidade, o que o contrato ja
        rejeita.
        """
        ids = list(version_ids)
        if not ids:
            return {}
        from agente_ia_edu.db.models import Question, QuestionVersion

        linhas = (await self._session.execute(
            select(PedagogicalClassification.question_version_id,
                   PedagogicalClassification.content,
                   PedagogicalClassification.subcontent,
                   PedagogicalClassification.lifecycle,
                   PedagogicalClassification.metadata_,
                   PedagogicalClassification.provenance,
                   PedagogicalClassification.validated_by_external_identity,
                   Question.status,
                   Question.visibility_scope,
                   Question.school_id)
            .join(QuestionVersion,
                  QuestionVersion.id == PedagogicalClassification.question_version_id)
            .join(Question, Question.id == QuestionVersion.question_id)
            .where(PedagogicalClassification.question_version_id.in_(ids),
                   PedagogicalClassification.lifecycle == "ACTIVE")
        )).all()

        validas = await self._alternativas_validas(ids)

        saida: dict[str, dict] = {}
        for (vid, conteudo, subconteudo, lifecycle, meta, proveniencia,
             validador, status, escopo, escola) in linhas:
            saida[str(vid)] = {
                "conteudo": conteudo,
                "habilidade": subconteudo,
                "lifecycle": lifecycle,
                "finalidade": (meta or {}).get("purpose"),
                "provenance": proveniencia,
                "validado_por": validador,
                "status_da_questao": status,
                "visibilidade": escopo,
                "escola_id": str(escola) if escola else None,
                "alternativas_validas": validas.get(str(vid), 0),
            }
        return saida

    async def _alternativas_validas(self, version_ids: list) -> dict[str, int]:
        """Quantas alternativas validas cada versao tem.

        Separado porque e aqui que mora o conhecimento de FORMATO: um item
        de multipla escolha tem gabarito quando exatamente uma alternativa e
        valida. Um instrumento de outro formato responderia a esta pergunta
        de outro jeito, e e por isso que o contrato recebe um booleano e nao
        esta contagem.
        """
        from sqlalchemy import func

        from agente_ia_edu.db.models import QuestionOption

        linhas = (await self._session.execute(
            select(QuestionOption.question_version_id,
                   func.count(QuestionOption.id))
            .where(QuestionOption.question_version_id.in_(version_ids),
                   QuestionOption.is_valid_option.is_(True))
            .group_by(QuestionOption.question_version_id)
        )).all()
        return {str(vid): int(n) for vid, n in linhas}

    # -- escolha -----------------------------------------------------------

    async def instrumentos(self, *, conteudo: str,
                           habilidades: Sequence[str],
                           escola_do_aluno: str | None = None
                           ) -> list[Escolha]:
        """Um instrumento por habilidade, na ordem em que o plano as pediu.

        Habilidade sem instrumento nenhum e OMITIDA, nao substituida: servir
        uma questao de outra habilidade registraria a lacuna errada. Quem
        chama ve a lista mais curta e decide.

        Um mesmo item nunca e servido duas vezes na mesma sondagem - duas
        copias da mesma pergunta nao medem duas coisas.
        """
        if not habilidades:
            return []
        disponiveis = await self.candidatos(conteudo,
                                            escola_do_aluno=escola_do_aluno)
        usados: set[str] = set()
        escolhas: list[Escolha] = []
        for habilidade in habilidades:
            restantes = [c for c in disponiveis
                         if c.question_version_id not in usados]
            escolha = escolher(restantes, habilidade=habilidade,
                               conteudo=conteudo,
                               escola_do_aluno=escola_do_aluno)
            if escolha is None:
                continue
            usados.add(escolha.question_version_id)
            escolhas.append(escolha)
        return escolhas

    async def habilidades_mensuraveis(self, conteudo: str, *,
                                      escola_do_aluno: str | None = None
                                      ) -> set[str]:
        """Para quais habilidades deste conteudo existe ALGUM instrumento.

        E o que o plano consulta para nao perguntar o que nao se pode medir.
        Usa o mesmo piso de compatibilidade da escolha - nem mais frouxo (o
        plano prometeria o que a escolha nao entrega) nem mais rigido (o
        plano pularia habilidade que tem fallback).
        """
        candidatos = await self.candidatos(conteudo,
                                           escola_do_aluno=escola_do_aluno)
        return {
            c.habilidade for c in candidatos
            if c.habilidade
            and compativel(c, habilidade=c.habilidade, conteudo=conteudo)
            and escolher([c], habilidade=c.habilidade, conteudo=conteudo,
                         escola_do_aluno=escola_do_aluno) is not None
        }


__all__ = ["SeletorDeSondagem"]
