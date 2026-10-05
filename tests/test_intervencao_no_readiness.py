"""O caminho inteiro: errar, ser ENSINADO, praticar, e só então avançar.

Este arquivo cobre as regras pedagógicas do macrobloco de ponta a ponta,
pela API real — não pela função isolada:

    A. aluno não medido            -> diagnóstico (não intervenção)
    B. aluno medido e suficiente   -> nenhuma intervenção desnecessária
    C. aluno medido e insuficiente -> ENSINO antes de mais questões
    D. ver a explicação            -> NÃO altera domínio
    E. concluir a etapa de ensino  -> NÃO significa domínio
    F. a prática produz evidência pelo contrato de sempre
    G. verificação negativa        -> não libera a atividade
    H. a atividade-alvo continua visível durante toda a preparação

A REGRA QUE SUSTENTA AS OUTRAS
===============================
Ler não é aprender. A PHASE 25 já garantia que ler um material não escreve em
`domain_content_mastery`; aqui isso vira asserção do fluxo do Assessor, porque
é exatamente a tentação que uma etapa de ensino cria — dar por aprendido quem
clicou em "Entendi".
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_session_factory,
)
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode
from agente_ia_edu.db.models.catalog import (
    MaterialBlock,
    MaterialSection,
    TheoryMaterial,
    TheoryMaterialVersion,
)
from agente_ia_edu.services.conteudo_balanceamento import MATERIAL, SECOES
from agente_ia_edu.services.itens_guiados import ITENS
from agente_ia_edu.services.proximo_passo import (
    PASSO_ATIVIDADE,
    PASSO_DIAGNOSTICO,
    PASSO_ENSINO,
    PASSO_GUIADA,
    PASSO_PRATICA,
)

from test_invariantes_piloto_zero import BALANC, KEYS, _ctx, _seed


async def _publicar_material(factory) -> str:
    """O mesmo conteúdo que o script publica, posto direto pelos modelos."""
    async with factory() as s:
        no = (await s.scalars(
            select(CatalogNode).where(CatalogNode.code == BALANC))).first()
        assert no is not None, "o cenario nao tem o conteudo da base"
        material = TheoryMaterial(
            title=MATERIAL["title"], description=MATERIAL["description"],
            material_kind=MATERIAL["material_kind"], authoring_source="PLATFORM",
            visibility_scope="PUBLIC", primary_content_node_id=no.id,
            created_by_external_identity="nucleo_edu_360")
        s.add(material)
        await s.flush()
        versao = TheoryMaterialVersion(
            material_id=material.id, version_number=1, status="PUBLISHED",
            introduction=MATERIAL["introduction"], summary=MATERIAL["summary"],
            created_by_external_identity="nucleo_edu_360")
        s.add(versao)
        await s.flush()
        for secao in SECOES:
            linha = MaterialSection(
                material_version_id=versao.id, section_type=secao["section_type"],
                position=secao["position"], title=secao["title"],
                content_node_id=no.id, curriculum_relation_type="PRIMARY")
            s.add(linha)
            await s.flush()
            for bloco in secao["blocks"]:
                s.add(MaterialBlock(
                    section_id=linha.id, material_version_id=versao.id,
                    block_type=bloco["block_type"], position=bloco["position"],
                    title=bloco.get("title"), body=bloco.get("body"),
                    metadata_=bloco.get("metadata")))
        await s.commit()
        return str(material.id)


class IntervencaoNoReadinessTests(unittest.TestCase):

    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=False)

        async def prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            fx = await _seed(self.factory)
            fx["material_id"] = await _publicar_material(self.factory)
            return fx

        # O CENARIO USA CODIGOS PROPRIOS.
        #
        # `_seed` monta um catalogo ficticio (QUIM-*), enquanto os itens
        # guiados declaram o conteudo real do Piloto Zero (CHEMISTRY-*). Sem
        # um item para o codigo DESTE cenario, `item_para` devolve None e a
        # rota responde 404 - o que esta certo: ela nao inventa pratica
        # guiada generica.
        #
        # Entao o cenario registra o seu, e desfaz no fim. Nao e um atalho:
        # e o mesmo que o conteudo real faz, so que para outro codigo.
        # `SKILL_B` e a micro-habilidade que as questoes da base declaram
        # neste cenario (ver `_seed`). O item precisa ser DELA para o teste
        # provar que a intervencao segue a lacuna medida, e nao outra.
        self._item_do_cenario = dict(
            ITENS[0], key="CENARIO-GUIADA", content_code=BALANC, skill="SKILL_B")
        ITENS.append(self._item_do_cenario)

        self.fx = self.loop.run_until_complete(prep())
        self.gabarito = self.fx["gabarito"]
        self.atividade = self.fx["atividade"]
        self.material = self.fx["material_id"]
        self.app = create_app()
        self.app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.app.dependency_overrides[get_current_authenticated_context] = _ctx
        self.client = TestClient(self.app)

    def tearDown(self):
        if self._item_do_cenario in ITENS:
            ITENS.remove(self._item_do_cenario)
        self.app.dependency_overrides.clear()
        self.client.close()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    # -- utilidades --------------------------------------------------------

    def _readiness(self) -> dict:
        r = self.client.get(
            f"/api/v1/student/activities/{self.atividade}/readiness")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _passo(self) -> dict:
        return self._readiness()["next_step"]

    def _responder(self, conteudo: str, *, quantas: int, acertos: int) -> str:
        r = self.client.post("/api/v1/student/practice",
                             json={"content_code": conteudo,
                                   "question_count": quantas})
        self.assertEqual(r.status_code, 200, r.text)
        pid = r.json()["assignment_id"]
        estado = self.client.post(
            f"/api/v1/student/activities/{pid}/attempt").json()
        for pos, q in enumerate(estado["questions"]):
            vid = q["question_version_id"]
            certa = self.gabarito[vid]
            escolha = certa if pos < acertos else next(k for k in KEYS if k != certa)
            self.client.put(
                f"/api/v1/student/activities/{pid}/attempt/answers/{vid}",
                json={"selected_option": escolha})
        self.client.post(f"/api/v1/student/activities/{pid}/attempt/complete")
        self.client.post(f"/api/v1/student/activities/{pid}/attempt/correct")
        self.client.post("/api/v1/student/domain/rebuild")
        return pid

    def _dominio(self, codigo=BALANC) -> dict:
        r = self.client.get(f"/api/v1/student/domain/content/{codigo}")
        return (r.json() or {}).get("content") or {}

    def _abrir_explicacao(self) -> list[dict]:
        r = self.client.get(f"/api/v1/student/materials/{self.material}/sections")
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def _estudar(self, *, ate_o_fim: bool = True) -> None:
        """Lê a explicação como o aluno lê, e marca onde parou.

        `completed` é o aluno dizendo "entendi" — é o que distingue quem
        terminou de quem abriu e saiu.
        """
        secoes = self._abrir_explicacao()
        alvo = secoes[-1] if ate_o_fim else secoes[0]
        r = self.client.put(
            f"/api/v1/student/materials/{self.material}/progress",
            json={"current_section_id": alvo["section_id"],
                  "completed": ate_o_fim})
        self.assertIn(r.status_code, (200, 201), r.text)

    # -- A e B: quando NAO intervir ----------------------------------------

    def test_A_aluno_nunca_medido_recebe_diagnostico_nao_intervencao(self):
        passo = self._passo()
        self.assertEqual(passo["kind"], PASSO_DIAGNOSTICO)
        self.assertIsNone((passo.get("intervention") or {}).get("action"),
                          "interveio sobre uma lacuna que ninguem mediu")

    def test_B_quem_vai_bem_na_base_nao_recebe_intervencao(self):
        self._responder(BALANC, quantas=3, acertos=3)
        passo = self._passo()
        self.assertNotEqual(passo["kind"], PASSO_ENSINO,
                            "mandou estudar quem acabou de acertar tudo")

    # -- C: a correcao central ---------------------------------------------

    def test_C_quem_vai_mal_e_ENSINADO_antes_de_mais_questoes(self):
        self._responder(BALANC, quantas=3, acertos=0)
        passo = self._passo()
        self.assertEqual(passo["kind"], PASSO_ENSINO,
                         "ofereceu mais questoes a quem acabou de errar tudo")
        self.assertEqual(passo["content_code"], BALANC)

    def test_C_a_intervencao_diz_por_que_e_para_onde(self):
        self._responder(BALANC, quantas=3, acertos=0)
        passo = self._passo()
        inter = passo.get("intervention") or {}
        self.assertTrue((inter.get("reason") or "").strip())
        self.assertTrue((inter.get("learning_objective") or "").strip())
        self.assertTrue((inter.get("next_check") or "").strip())

    def test_C_a_tela_recebe_o_material_a_abrir(self):
        """Mandar estudar sem dizer o quê deixaria a tela adivinhando."""
        self._responder(BALANC, quantas=3, acertos=0)
        self.assertEqual(self._passo().get("material_id"), self.material)

    # -- H: o objetivo nunca desaparece ------------------------------------

    def test_H_a_atividade_alvo_continua_visivel_durante_a_preparacao(self):
        self._responder(BALANC, quantas=3, acertos=0)
        d = self._readiness()
        passo = d["next_step"]
        self.assertEqual(d["assignment_id"], str(self.atividade))
        self.assertTrue(d.get("title"), "a atividade perdeu o nome")
        self.assertTrue(passo.get("for_content_name") or passo.get("for_content_code"),
                        "o passo esqueceu para onde o aluno estava indo")

    # -- D e E: ler nao e aprender -----------------------------------------

    def test_D_abrir_a_explicacao_nao_altera_dominio(self):
        self._responder(BALANC, quantas=3, acertos=0)
        antes = self._dominio()
        self._estudar()
        depois = self._dominio()
        for campo in ("questions_answered", "questions_correct", "accuracy",
                      "evidence_count", "content_state"):
            with self.subTest(campo=campo):
                self.assertEqual(antes.get(campo), depois.get(campo),
                                 f"ler a explicacao mexeu em {campo}")

    def test_E_concluir_o_ensino_nao_libera_a_atividade(self):
        """Depois de estudar o passo e TENTAR.

        Ate 2026-10-05 esse "tentar" era a pratica autonoma; com a PRATICA
        GUIADA no meio, e ela. O que o teste protege e o mesmo: clicar
        "entendi" nao libera a atividade.
        """
        self._responder(BALANC, quantas=3, acertos=0)
        self._estudar()
        passo = self._passo()
        self.assertNotEqual(passo["kind"], PASSO_ATIVIDADE,
                            "deu por aprendido quem so clicou em 'entendi'")
        self.assertIn(passo["kind"], (PASSO_GUIADA, PASSO_PRATICA))

    # -- a pratica guiada entra entre o ensino e a pratica ------------------

    def _guiada(self, item_key: str, acao: str, **corpo) -> dict:
        r = self.client.post(f"/api/v1/student/guided-practice/{item_key}/{acao}",
                             json=corpo or None)
        self.assertEqual(r.status_code, 200, r.text)
        return r.json()

    def test_depois_de_estudar_o_passo_e_a_PRATICA_GUIADA(self):
        """Antes deste bloco, estudar levava direto à prática autônoma —
        pulando a etapa em que o aluno tenta com ajuda."""
        self._responder(BALANC, quantas=3, acertos=0)
        self._estudar()
        passo = self._passo()
        self.assertEqual(passo["kind"], PASSO_GUIADA)
        self.assertTrue(passo.get("item_key"), "a tela nao sabe qual item abrir")

    def test_a_guiada_ataca_a_micro_habilidade_medida(self):
        self._responder(BALANC, quantas=3, acertos=0)
        self._estudar()
        passo = self._passo()
        inter = passo.get("intervention") or {}
        if inter.get("skill"):
            r = self.client.get(
                f"/api/v1/student/guided-practice?content_code={BALANC}"
                f"&skill={inter['skill']}")
            self.assertEqual(r.status_code, 200, r.text)
            self.assertEqual(r.json()["skill"], inter["skill"])

    def test_concluir_a_guiada_com_ajuda_leva_a_pratica_AUTONOMA(self):
        """O ciclo que o bloco pede: tentou com ajuda, agora tenta sozinho."""
        self._responder(BALANC, quantas=3, acertos=0)
        self._estudar()
        item = self._passo()["item_key"]
        self._guiada(item, "hint")
        estado = self.client.get(
            f"/api/v1/student/guided-practice?content_code={BALANC}").json()
        # acerta pela forca bruta, sem gabarito - o servidor e quem confere
        for letra in [o["key"] for o in estado["options"]]:
            r = self._guiada(item, "answer", selected_option=letra)
            if r["correct"]:
                break
        self.assertTrue(r["completed"])
        self.assertFalse(r["solved_unaided"], "usou ajuda e saiu como sozinho")
        self.assertEqual(self._passo()["kind"], PASSO_PRATICA)

    def test_a_guiada_concluida_NAO_produz_evidencia_de_dominio(self):
        """A asserção central do bloco, pela API real."""
        self._responder(BALANC, quantas=3, acertos=0)
        self._estudar()
        antes = self._dominio()
        item = self._passo()["item_key"]
        self._guiada(item, "hint")
        estado = self.client.get(
            f"/api/v1/student/guided-practice?content_code={BALANC}").json()
        for letra in [o["key"] for o in estado["options"]]:
            if self._guiada(item, "answer", selected_option=letra)["correct"]:
                break
        depois = self._dominio()
        for campo in ("questions_answered", "questions_correct", "accuracy",
                      "evidence_count", "origin_breakdown"):
            with self.subTest(campo=campo):
                self.assertEqual(antes.get(campo), depois.get(campo),
                                 f"a pratica guiada mexeu em {campo}")

    def test_a_pratica_autonoma_DEPOIS_da_guiada_produz_evidencia(self):
        self._responder(BALANC, quantas=3, acertos=0)
        self._estudar()
        item = self._passo()["item_key"]
        estado = self.client.get(
            f"/api/v1/student/guided-practice?content_code={BALANC}").json()
        for letra in [o["key"] for o in estado["options"]]:
            if self._guiada(item, "answer", selected_option=letra)["correct"]:
                break
        antes = (self._dominio().get("questions_answered") or 0)
        self._responder(BALANC, quantas=3, acertos=3)
        self.assertGreater(self._dominio().get("questions_answered") or 0, antes)

    def test_a_correta_nao_vaza_no_payload_da_rota(self):
        self._responder(BALANC, quantas=3, acertos=0)
        self._estudar()
        r = self.client.get(
            f"/api/v1/student/guided-practice?content_code={BALANC}")
        self.assertEqual(r.status_code, 200, r.text)
        corpo = r.json()
        self.assertNotIn("correct_option", corpo)
        self.assertNotIn("correta", r.text)
        self.assertNotIn("balanceada", r.text)

    # -- o ciclo conta PRATICAS, nao tudo ----------------------------------

    def test_o_diagnostico_nao_conta_como_ciclo_de_intervencao(self):
        """`list_practices` devolve práticas E microdiagnósticos — os dois são
        assignments do mesmo tipo, e o diagnóstico sai de lá com
        `PRACTICE_CORRECTED`. Contá-lo como ciclo faria o aluno chegar ao teto
        de intervenções sem ter recebido nenhuma: o primeiro ensino já
        começaria no ciclo 2.
        """
        r = self.client.post("/api/v1/student/micro-diagnostic",
                             json={"content_code": BALANC,
                                   "objective_assignment_id": str(self.atividade)})
        self.assertEqual(r.status_code, 200, r.text)
        did = r.json()["assignment_id"]
        estado = self.client.post(
            f"/api/v1/student/activities/{did}/attempt").json()
        for q in estado["questions"]:
            vid = q["question_version_id"]
            errada = next(k for k in KEYS if k != self.gabarito[vid])
            self.client.put(
                f"/api/v1/student/activities/{did}/attempt/answers/{vid}",
                json={"selected_option": errada})
        self.client.post(f"/api/v1/student/activities/{did}/attempt/complete")
        self.client.post(f"/api/v1/student/activities/{did}/attempt/correct")
        self.client.post("/api/v1/student/domain/rebuild")

        inter = self._passo().get("intervention") or {}
        self.assertEqual(inter.get("cycle"), 1,
                         "o diagnostico foi contado como ciclo de intervencao")

    # -- o ciclo fecha -----------------------------------------------------

    def test_praticar_bem_depois_do_ensino_tira_o_aluno_da_preparacao(self):
        """E2E 2, a asserção final: errar, ser ensinado, praticar, avançar.

        O passo deixa de ser sobre a base. Para onde ele vai depois — a
        atividade, ou um diagnóstico do próprio conteúdo que ninguém mediu
        ainda — é decisão da política; o que este teste fixa é que o aluno
        não fica preso praticando a mesma base.
        """
        self._responder(BALANC, quantas=3, acertos=0)
        self.assertEqual(self._passo()["kind"], PASSO_ENSINO)
        self._estudar()
        self._responder(BALANC, quantas=5, acertos=5)
        passo = self._passo()
        self.assertNotEqual(passo.get("content_code"), BALANC,
                            "continuou mandando praticar a base ja firmada")

    def test_a_base_firmada_aparece_no_dominio_com_as_duas_origens(self):
        """A evidência do ensino não existe; a da prática, sim — e some com a
        do diagnóstico no mesmo conteúdo."""
        self._responder(BALANC, quantas=3, acertos=0)
        self._estudar()
        self._responder(BALANC, quantas=5, acertos=5)
        d = self._dominio()
        self.assertEqual(d.get("questions_answered"), 8)
        self.assertEqual(set(d.get("origin_breakdown") or {}), {"PRACTICE"})

    # -- retomada: abrir e sair nao e ter entendido ------------------------

    def test_abrir_a_explicacao_e_sair_devolve_a_explicacao(self):
        """E2E 4. Quem parou no meio volta para o meio, não para a prática."""
        self._responder(BALANC, quantas=3, acertos=0)
        self._estudar(ate_o_fim=False)
        passo = self._passo()
        self.assertEqual(passo["kind"], PASSO_ENSINO,
                         "mandou praticar quem parou no meio da explicacao")
        self.assertEqual(passo["state"], "IN_PROGRESS")
        self.assertIn("Continuar", passo["cta"])

    def test_sem_ter_aberto_o_rotulo_nao_diz_continuar(self):
        self._responder(BALANC, quantas=3, acertos=0)
        passo = self._passo()
        self.assertEqual(passo["state"], "NOT_STARTED")
        self.assertNotIn("Continuar", passo["cta"])

    def test_concluida_a_explicacao_o_rotulo_leva_a_TENTATIVA(self):
        """"Praticar agora" ate 2026-10-05; agora "Tentar com ajuda", porque
        e isso que vem depois da explicacao."""
        self._responder(BALANC, quantas=3, acertos=0)
        self._estudar()
        self.assertIn("entar", self._passo()["cta"])

    # -- F e G: so a evidencia avanca --------------------------------------

    def test_F_a_pratica_produz_evidencia_pelo_contrato_de_sempre(self):
        self._responder(BALANC, quantas=3, acertos=0)
        antes = self._dominio().get("questions_answered") or 0
        self._estudar()
        self._responder(BALANC, quantas=3, acertos=3)
        depois = self._dominio()
        self.assertGreater(depois.get("questions_answered") or 0, antes)
        self.assertIn("PRACTICE", depois.get("origin_breakdown") or {})

    def test_G_continuar_errando_nao_libera_a_atividade(self):
        self._responder(BALANC, quantas=3, acertos=0)
        self._estudar()
        self._responder(BALANC, quantas=3, acertos=0)
        self.assertNotEqual(self._passo()["kind"], PASSO_ATIVIDADE,
                            "liberou a atividade a quem continua errando")

    def test_G_praticar_mal_DEPOIS_de_estudar_devolve_a_explicacao(self):
        """A alternância é o que impede o loop.

        Medido no navegador: o aluno estudou, praticou 1 de 5, e o passo
        seguinte era praticar de novo — e de novo, e de novo. `ja_ensinado`
        vinha de `MaterialProgress`, que fica COMPLETED para sempre, então a
        explicação nunca mais voltava e só restavam questões. É exatamente o
        "responder questões para sempre" que motivou o macrobloco, agora com
        uma aula no início.

        Um estudo é "recente" enquanto nada foi tentado depois dele. Praticou
        e continuou mal? Então aquela leitura não bastou, e rever vale mais
        que repetir.
        """
        self._responder(BALANC, quantas=3, acertos=0)
        self._estudar()
        self.assertIn(self._passo()["kind"], (PASSO_GUIADA, PASSO_PRATICA))
        self._responder(BALANC, quantas=3, acertos=0)
        self.assertEqual(self._passo()["kind"], PASSO_ENSINO,
                         "so ofereceu mais questoes a quem ja tinha estudado "
                         "e errado de novo")

    def test_G_ao_devolver_a_explicacao_o_rotulo_nao_manda_praticar(self):
        """O estado do passo de ensino segue a mesma regra de envelhecimento.

        Sem isso o passo voltava a ser ENSINO e o botão dizia "Praticar
        agora" — lido do progresso antigo, que continua COMPLETED. Botão e
        destino apontando para lados diferentes é a família de bug que este
        projeto já corrigiu duas vezes.
        """
        self._responder(BALANC, quantas=3, acertos=0)
        self._estudar()
        self._responder(BALANC, quantas=3, acertos=0)
        passo = self._passo()
        self.assertEqual(passo["kind"], PASSO_ENSINO)
        self.assertNotIn("ratic", passo["cta"],
                         "o passo e estudar e o botao manda praticar")

    def test_G_e_tambem_nao_entra_em_loop_repetindo_a_mesma_coisa(self):
        """Depois de estudar e praticar mal, o passo não pode ser de novo
        'estude a mesma explicação' sem nada ter mudado — e também não pode
        ser só mais questões para sempre."""
        self._responder(BALANC, quantas=3, acertos=0)
        self._estudar()
        passo_1 = self._passo()["kind"]
        self._responder(BALANC, quantas=3, acertos=0)
        passo_2 = self._passo()["kind"]
        self.assertIn(passo_1, (PASSO_PRATICA, PASSO_ENSINO, PASSO_GUIADA))
        self.assertIn(passo_2, (PASSO_PRATICA, PASSO_ENSINO, PASSO_GUIADA))
        inter = self._passo().get("intervention") or {}
        self.assertGreaterEqual(inter.get("cycle") or 0, 2,
                                "o ciclo nao avancou - o sistema nao percebeu "
                                "que ja tinha tentado")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
