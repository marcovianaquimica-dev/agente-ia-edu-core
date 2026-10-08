"""ERRAR NA PRÁTICA FORMATIVA PRODUZ UMA DECISÃO — e não a próxima questão.

O DEFEITO, MEDIDO NA VALIDAÇÃO MANUAL DE 2026-10-08
====================================================
O estudante respondeu errado e a plataforma serviu a próxima questão, e a
próxima, até "5 de 5". Rastreando o caminho real:

    save_answer  grava `selected_option_key` e devolve um RECIBO
                 (saved, position, answered_count) — nunca consulta o
                 gabarito
    correct      só existe depois do lote inteiro

Então o backend RECEBEU a resposta errada e não tinha como classificá-la
naquele momento: a prática estava modelada como lote rígido, corrigido no
fim. O frontend não ignorou decisão nenhuma — não havia decisão. A
intervenção estava ausente por falta de integração, não por regra.

E havia uma assimetria indevida: `servico_de_investigacao.responder` confere
NA HORA e devolve `correct`, `observacao` e o próximo passo. A prática, não.

O QUE ESTE ARQUIVO TRAVA
=========================
1. errar na prática formativa produz decisão, e `may_advance` é False;
2. acertar não produz decisão nenhuma;
3. o diagnóstico NÃO é interrompido;
4. a verificação L0 NÃO é interrompida;
5. a avaliação formal NÃO é interrompida;
6. a decisão vem do motor que já existe, não de um segundo;
7. investigação já percorrida não reabre em laço;
8. nada aqui escreve evidência.
"""

from __future__ import annotations

import asyncio
import importlib.util
import pathlib
import unittest
import uuid as _uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, Question, QuestionVersion
from agente_ia_edu.services.grafo_estequiometria import (
    CONTEUDO,
    LEITURA_FORMULA,
    MASSA_MOLAR,
)
from agente_ia_edu.services.intervencao_formativa import (
    ACAO_ENSINAR,
    ACAO_INVESTIGAR,
    decidir_apos_resposta,
)
from agente_ia_edu.services.modo_pedagogico import (
    MODO_AVALIACAO,
    MODO_DIAGNOSTICO,
    MODO_FORMATIVO,
    MODO_VERIFICACAO,
)

_RAIZ = pathlib.Path(__file__).resolve().parent.parent
ALUNO = "aluno_qa_formativa"

META_FORMATIVA = {"practice": True, "origin": "PRACTICE",
                  "purpose": "PRACTICE", "content_code": CONTEUDO}
META_DIAGNOSTICO = {"practice": True, "origin": "MICRO_DIAGNOSTIC",
                    "purpose": "PRACTICE", "content_code": CONTEUDO}
META_VERIFICACAO = {"practice": True, "origin": "PRACTICE",
                    "purpose": "VERIFY", "content_code": CONTEUDO}
META_AVALIACAO: dict = {}


def _carregar(nome: str, caminho: str):
    spec = importlib.util.spec_from_file_location(nome, _RAIZ / caminho)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


pub_sondagem = _carregar("pub_sond_if",
                         "scripts/publicar_sondagem_estequiometria.py")


def _requester(aluno: str = ALUNO):
    from agente_ia_edu.services.question_list_store import Requester

    return Requester(external_user_id=aluno, school_id=None, role="STUDENT",
                     is_platform_admin=False)


class _Base(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        # `expire_on_commit=True` COMO A APLICACAO.
        #
        # Com False, `save_answer` podia ler `row.metadata_` depois do commit
        # e o teste passava; no navegador o mesmo acesso levantava
        # MissingGreenlet, a decisao era engolida pelo `except` e a resposta
        # errada voltava com `may_advance: true`. Medido em 2026-10-08.
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=True)
        self.req = _requester()

        async def prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with self.factory() as s:
                disc = CatalogNode(id=_uuid.uuid4(), code="CHEMISTRY",
                                   name="Quimica", node_type="DISCIPLINE",
                                   position=0, active=True)
                s.add(disc)
                await s.flush()
                disc.root_id = disc.id
                s.add(CatalogNode(id=_uuid.uuid4(), code=CONTEUDO,
                                  name="Estequiometria", node_type="CONTENT",
                                  position=0, parent_id=disc.id,
                                  root_id=disc.id, active=True))
                await s.commit()
            async with self.factory() as s:
                await pub_sondagem.publicar(s)

        self.loop.run_until_complete(prep())

    def tearDown(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _rodar(self, corrotina_de):
        async def run():
            async with self.factory() as s:
                return await corrotina_de(s)
        return self.loop.run_until_complete(run())

    def _versao(self, chave: str) -> str:
        """O question_version_id de um item publicado, pela chave curada."""
        async def run(s):
            for q in (await s.execute(select(Question))).scalars().all():
                if (q.metadata_ or {}).get("sondagem_key") != chave:
                    continue
                v = (await s.execute(select(QuestionVersion).where(
                    QuestionVersion.question_id == q.id))).scalars().first()
                return str(v.id)
            return None
        return self._rodar(run)

    def _decidir(self, chave: str, marcou: str, metadata=META_FORMATIVA,
                 aluno: str = ALUNO):
        vid = self._versao(chave)
        self.assertIsNotNone(vid, f"item {chave} nao publicado")

        async def run(s):
            return await decidir_apos_resposta(
                s, aluno=aluno, metadata=metadata, question_version_id=vid,
                selected_option_key=marcou, requester=_requester(aluno))
        return self._rodar(run)


class ERRARNAPRATICAPRODUZDECISAO(_Base):
    """P0: o erro deixa de ser só um `selected_option_key` gravado."""

    def test_errar_produz_uma_decisao(self):
        d = self._decidir("SOND-EST-FORMULA-1", "A")   # correta e C
        self.assertTrue(d, "errar na prática formativa não produziu decisão")

    def test_e_a_decisao_proibe_avancar(self):
        d = self._decidir("SOND-EST-FORMULA-1", "A")
        self.assertFalse(d["may_advance"])

    def test_a_decisao_nomeia_a_micro_habilidade_do_ITEM_errado(self):
        """Não a do conteúdo: a intervenção precisa de alvo."""
        d = self._decidir("SOND-EST-FORMULA-1", "A")
        self.assertEqual(LEITURA_FORMULA, d["skill"])

    def test_item_de_outra_habilidade_aponta_outra_habilidade(self):
        d = self._decidir("SOND-EST-MASSA-MOLAR-1", "A")
        self.assertEqual(MASSA_MOLAR, d["skill"])

    def test_a_acao_e_INVESTIGAR_quando_ha_cadeia_curada(self):
        d = self._decidir("SOND-EST-FORMULA-1", "A")
        self.assertEqual(ACAO_INVESTIGAR, d["action"])

    def test_e_a_cadeia_viaja_junto(self):
        """Sem ela a tela teria de adivinhar qual investigação abrir."""
        d = self._decidir("SOND-EST-FORMULA-1", "A")
        self.assertEqual(CONTEUDO, d["content_code"])
        self.assertTrue(d.get("reason"))

    def test_o_modo_viaja_como_dado(self):
        d = self._decidir("SOND-EST-FORMULA-1", "A")
        self.assertEqual(MODO_FORMATIVO, d["mode"])


class ONOMEDAACAOEODOASSESSOR(unittest.TestCase):
    """Um vocabulário só para a mesma coisa.

    `intervencao_formativa` não importa de `assessor_pedagogico` para não
    criar ciclo — e redefinir a constante com outro texto criaria dois
    vocabulários, com a tela tratando "INVESTIGATE" e "INVESTIGAR" como
    ações diferentes.
    """

    def test_investigar_e_a_mesma_constante(self):
        from agente_ia_edu.services.assessor_pedagogico import ACAO_INVESTIGAR as A
        self.assertEqual(A, ACAO_INVESTIGAR)

    def test_ensinar_e_a_mesma_constante(self):
        from agente_ia_edu.services.assessor_pedagogico import ACAO_ENSINAR as A
        self.assertEqual(A, ACAO_ENSINAR)


class ACERTARNAOINTERROMPE(_Base):

    def test_acertar_nao_produz_decisao(self):
        self.assertEqual({}, self._decidir("SOND-EST-FORMULA-1", "C"))

    def test_acertar_em_minuscula_tambem_nao(self):
        self.assertEqual({}, self._decidir("SOND-EST-FORMULA-1", "c"))

    def test_nao_responder_nao_produz_decisao(self):
        """Pular não é errar — e o lote ainda não acabou."""
        self.assertEqual({}, self._decidir("SOND-EST-FORMULA-1", None))
        self.assertEqual({}, self._decidir("SOND-EST-FORMULA-1", ""))


class OSOUTROSTRESMODOSNAOSAOINTERROMPIDOS(_Base):
    """A proteção do §4, medida com a MESMA resposta errada."""

    def test_o_diagnostico_segue(self):
        d = self._decidir("SOND-EST-FORMULA-1", "A", META_DIAGNOSTICO)
        self.assertEqual({}, d, "ensinar no meio contamina a próxima observação")

    def test_a_verificacao_L0_segue(self):
        d = self._decidir("SOND-EST-FORMULA-1", "A", META_VERIFICACAO)
        self.assertEqual({}, d, "dica durante a tentativa destrói a evidência")

    def test_a_avaliacao_formal_segue(self):
        d = self._decidir("SOND-EST-FORMULA-1", "A", META_AVALIACAO)
        self.assertEqual({}, d, "ensinar durante a prova viola a avaliação")

    def test_metadata_desconhecido_segue(self):
        d = self._decidir("SOND-EST-FORMULA-1", "A",
                          {"practice": True, "origin": "ALGO"})
        self.assertEqual({}, d)


class AINVESTIGACAONAOREABRE_EM_LACO(_Base):
    """Percorrida a cadeia, o erro seguinte pede outra coisa."""

    def _percorrer_a_investigacao(self):
        from agente_ia_edu.services.investigacao_do_erro import (
            investigacao_para,
        )
        from agente_ia_edu.services.servico_de_investigacao import (
            InvestigacaoService,
        )

        inv = investigacao_para(CONTEUDO, LEITURA_FORMULA)

        async def run(s):
            sv = InvestigacaoService(s)
            for etapa in inv.etapas:
                await sv.responder(ALUNO, inv.key, etapa.ordem,
                                   etapa.alternativas[etapa.correta],
                                   requester=self.req)
        # Uma sessão nova por etapa, como a aplicação faz.
        for etapa in inv.etapas:
            async def uma(s, e=etapa):
                return await InvestigacaoService(s).responder(
                    ALUNO, inv.key, e.ordem, e.alternativas[e.correta],
                    requester=self.req)
            self._rodar(uma)

    def test_antes_de_percorrer_a_acao_e_investigar(self):
        d = self._decidir("SOND-EST-FORMULA-1", "A")
        self.assertEqual(ACAO_INVESTIGAR, d["action"])

    def test_depois_de_percorrer_a_acao_MUDA(self):
        self._percorrer_a_investigacao()
        d = self._decidir("SOND-EST-FORMULA-1", "A")
        self.assertNotEqual(ACAO_INVESTIGAR, (d or {}).get("action"),
                            "a mesma investigação foi oferecida de novo")

    def test_e_sem_material_publicado_ela_libera_o_avanco(self):
        """Honesto: não há com que intervir, e prender o aluno seria pior."""
        self._percorrer_a_investigacao()
        d = self._decidir("SOND-EST-FORMULA-1", "A")
        if d:
            self.assertEqual(ACAO_ENSINAR, d["action"])
        else:
            self.assertEqual({}, d)


class NADAAQUIESCREVEEVIDENCIA(_Base):
    """A decisão é LEITURA. Quem escreve domínio continua sendo o mesmo."""

    def test_decidir_nao_toca_em_activity_nem_em_dominio(self):
        from agente_ia_edu.db.models import (
            ActivityResult,
            ActivityResultItem,
            DomainContentMastery,
        )

        self._decidir("SOND-EST-FORMULA-1", "A")

        async def contar(s):
            saida = {}
            for modelo in (ActivityResult, ActivityResultItem,
                           DomainContentMastery):
                saida[modelo.__tablename__] = len(
                    (await s.execute(select(modelo))).scalars().all())
            return saida

        self.assertEqual({"activity_results": 0, "activity_result_items": 0,
                          "domain_content_mastery": 0},
                         self._rodar(contar))

    def test_decidir_duas_vezes_devolve_a_mesma_decisao(self):
        """Idempotente: reenvio não duplica transição nem muda o rumo."""
        a = self._decidir("SOND-EST-FORMULA-1", "A")
        b = self._decidir("SOND-EST-FORMULA-1", "A")
        self.assertEqual(a["action"], b["action"])
        self.assertEqual(a["skill"], b["skill"])


class QUESTAOSEMHABILIDADENAOPRENDEOALUNO(_Base):
    """Sem alvo não há intervenção — e inventar um seria pior."""

    def test_questao_sem_classificacao_ativa_libera_o_avanco(self):
        async def orfa(s):
            q = Question(validation_status="valid", origin_type="GENERATED",
                         status="PUBLISHED", visibility_scope="PUBLIC",
                         question_type="MULTIPLE_CHOICE")
            s.add(q)
            await s.flush()
            vid = _uuid.uuid4()
            s.add(QuestionVersion(id=vid, question_id=q.id,
                                  version_kind="official_original",
                                  canonical_text="x", statement="x",
                                  content_hash=str(_uuid.uuid4())))
            # O id é escolhido ANTES do commit: lê-lo depois tentaria
            # recarregar o objeto expirado fora do contexto async.
            await s.commit()
            return str(vid)

        vid = self._rodar(orfa)

        async def run(s):
            return await decidir_apos_resposta(
                s, aluno=ALUNO, metadata=META_FORMATIVA,
                question_version_id=vid, selected_option_key="A",
                requester=self.req)

        self.assertEqual({}, self._rodar(run))


if __name__ == "__main__":
    unittest.main()


class OPERCURSOREALDAPRATICA(_Base):
    """O caminho inteiro: criar prática → iniciar → responder errado.

    Os testes acima provam a DECISÃO. Este prova a INTEGRAÇÃO — que
    `save_answer`, que era um recibo, agora devolve a decisão, e que ela
    sobrevive ao recarregar. Sem ele, o módulo poderia estar perfeito e a
    plataforma continuar servindo a próxima questão.
    """

    def _pratica(self, purpose="PRACTICE", origin="PRACTICE"):
        from agente_ia_edu.services.adaptive_practice import (
            AdaptivePracticeService,
        )

        async def run(s):
            return await AdaptivePracticeService(s).create_practice(
                ALUNO, requester=self.req, content_code=CONTEUDO,
                question_count=3, purpose=purpose, origin=origin)
        return self._rodar(run)

    def _iniciar(self, assignment_id):
        from agente_ia_edu.services.activity_player_store import (
            ActivityPlayerStore,
        )
        import uuid

        async def run(s):
            return await ActivityPlayerStore(s).start(
                uuid.UUID(str(assignment_id)), requester=self.req)
        return self._rodar(run)

    def _responder(self, assignment_id, question_version_id, opcao):
        from agente_ia_edu.services.activity_player_store import (
            ActivityPlayerStore,
        )
        import uuid

        async def run(s):
            return await ActivityPlayerStore(s).save_answer(
                uuid.UUID(str(assignment_id)),
                uuid.UUID(str(question_version_id)),
                requester=self.req, selected_option=opcao)
        return self._rodar(run)

    def _estado(self, assignment_id):
        from agente_ia_edu.services.activity_player_store import (
            ActivityPlayerStore,
        )
        import uuid

        async def run(s):
            return await ActivityPlayerStore(s).get_state(
                uuid.UUID(str(assignment_id)), requester=self.req)
        return self._rodar(run)

    def _errada(self, questao) -> str:
        """Uma alternativa que NÃO é a correta, lida do banco."""
        from agente_ia_edu.db.models import QuestionOption
        import uuid

        async def run(s):
            linhas = (await s.execute(select(QuestionOption).where(
                QuestionOption.question_version_id
                == uuid.UUID(str(questao["question_version_id"]))))
            ).scalars().all()
            return next(o.option_key for o in linhas if not o.is_valid_option)
        return self._rodar(run)

    def _certa(self, questao) -> str:
        from agente_ia_edu.db.models import QuestionOption
        import uuid

        async def run(s):
            linhas = (await s.execute(select(QuestionOption).where(
                QuestionOption.question_version_id
                == uuid.UUID(str(questao["question_version_id"]))))
            ).scalars().all()
            return next(o.option_key for o in linhas if o.is_valid_option)
        return self._rodar(run)

    # -- os testes ---------------------------------------------------------

    def test_errar_na_pratica_devolve_may_advance_FALSE(self):
        p = self._pratica()
        estado = self._iniciar(p["assignment_id"])
        q = estado["questions"][0]
        r = self._responder(p["assignment_id"], q["question_version_id"],
                            self._errada(q))
        self.assertFalse(r["may_advance"],
                         "o backend continuou autorizando o avanço cego")
        self.assertIsNotNone(r["intervention"])

    def test_e_a_intervencao_nomeia_acao_e_habilidade(self):
        p = self._pratica()
        estado = self._iniciar(p["assignment_id"])
        q = estado["questions"][0]
        r = self._responder(p["assignment_id"], q["question_version_id"],
                            self._errada(q))
        self.assertIn(r["intervention"]["action"],
                      (ACAO_INVESTIGAR, ACAO_ENSINAR))
        self.assertTrue(r["intervention"]["skill"])

    def test_acertar_devolve_may_advance_TRUE(self):
        p = self._pratica()
        estado = self._iniciar(p["assignment_id"])
        q = estado["questions"][0]
        r = self._responder(p["assignment_id"], q["question_version_id"],
                            self._certa(q))
        self.assertTrue(r["may_advance"])
        self.assertIsNone(r["intervention"])

    def test_a_resposta_CONTINUA_sendo_gravada(self):
        """Interromper não pode custar a resposta dele."""
        p = self._pratica()
        estado = self._iniciar(p["assignment_id"])
        q = estado["questions"][0]
        errada = self._errada(q)
        self._responder(p["assignment_id"], q["question_version_id"], errada)
        depois = self._estado(p["assignment_id"])
        primeira = depois["questions"][0]
        self.assertTrue(primeira["answered"])
        self.assertEqual(errada, primeira["selected_option"])

    def test_o_DIAGNOSTICO_continua_avancando(self):
        """§4-A: ensinar no meio contamina a observação seguinte."""
        p = self._pratica(origin="MICRO_DIAGNOSTIC")
        estado = self._iniciar(p["assignment_id"])
        q = estado["questions"][0]
        r = self._responder(p["assignment_id"], q["question_version_id"],
                            self._errada(q))
        self.assertTrue(r["may_advance"])
        self.assertIsNone(r["intervention"])

    def test_a_VERIFICACAO_continua_avancando(self):
        """§4-C: dica durante a tentativa destrói a independência."""
        p = self._pratica(purpose="VERIFY")
        estado = self._iniciar(p["assignment_id"])
        q = estado["questions"][0]
        r = self._responder(p["assignment_id"], q["question_version_id"],
                            self._errada(q))
        self.assertTrue(r["may_advance"])
        self.assertIsNone(r["intervention"])

    def test_depois_do_F5_a_intervencao_VOLTA(self):
        """§13: o estudante não pode receber questão nova por um refresh."""
        p = self._pratica()
        estado = self._iniciar(p["assignment_id"])
        q = estado["questions"][0]
        self._responder(p["assignment_id"], q["question_version_id"],
                        self._errada(q))
        # Sessão nova, como a página recarregada faz.
        depois = self._estado(p["assignment_id"])
        self.assertIsNotNone(depois.get("pending_intervention"),
                             "a intervenção sumiu no recarregar")
        self.assertEqual(q["question_version_id"],
                         depois["pending_intervention"]["question_version_id"])

    def test_e_depois_de_acertar_nao_ha_intervencao_pendente(self):
        p = self._pratica()
        estado = self._iniciar(p["assignment_id"])
        q = estado["questions"][0]
        self._responder(p["assignment_id"], q["question_version_id"],
                        self._certa(q))
        self.assertIsNone(self._estado(p["assignment_id"])
                          .get("pending_intervention"))

    def test_enviar_duas_vezes_nao_duplica_a_transicao(self):
        """§15-L: reenvio é idempotente."""
        p = self._pratica()
        estado = self._iniciar(p["assignment_id"])
        q = estado["questions"][0]
        errada = self._errada(q)
        a = self._responder(p["assignment_id"], q["question_version_id"], errada)
        b = self._responder(p["assignment_id"], q["question_version_id"], errada)
        self.assertEqual(a["intervention"]["action"],
                         b["intervention"]["action"])
        self.assertEqual(a["answered_count"], b["answered_count"])

    def test_corrigir_a_resposta_para_a_certa_libera_o_avanco(self):
        p = self._pratica()
        estado = self._iniciar(p["assignment_id"])
        q = estado["questions"][0]
        self._responder(p["assignment_id"], q["question_version_id"],
                        self._errada(q))
        r = self._responder(p["assignment_id"], q["question_version_id"],
                            self._certa(q))
        self.assertTrue(r["may_advance"])
        self.assertIsNone(self._estado(p["assignment_id"])
                          .get("pending_intervention"))
