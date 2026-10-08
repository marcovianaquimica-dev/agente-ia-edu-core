"""CONSOLIDAÇÃO E RETENÇÃO — os dois estados que o §12 pede e não existiam.

O QUE A AUDITORIA DE 2026-10-08 ENCONTROU
==========================================
`PerformanceThresholdPolicy.band` tem cinco bandas: INSUFFICIENT, NO_DATA,
STRONG, INTERMEDIATE, IMPROVEMENT. Todas respondem a mesma pergunta — "qual
é o acerto dele?" — e nenhuma responde as duas que o §12 faz:

    Consolidação: consistência da aprendizagem em OPORTUNIDADES DIFERENTES.
    Retenção: recuperação ou aplicação APÓS UM INTERVALO relevante.

Acertar 5 de 5 numa tarde é STRONG. Acertar 5 de 5 numa tarde e mais 3 de 3
duas semanas depois também é STRONG — e são coisas pedagogicamente
diferentes. A banda não vê o tempo nem as ocasiões.

O QUE ESTE ARQUIVO TRAVA
=========================
1. Os dois estados existem e saem da MESMA evidência que já é gravada.
2. Nenhum corte novo: `min_sample_size` e `strong_accuracy` continuam vindo
   de `PerformanceThresholdPolicy`. Este módulo não decide quanto é "bom".
3. **Uma dificuldade isolada NÃO apaga o domínio anterior.** O §12 é
   explícito, e é a asserção mais importante daqui: o erro levanta a bandeira
   de revisão, não derruba o estado.
4. Ausência de evidência não é desconhecimento.
5. Só evidência INDEPENDENTE conta — acertar com ajuda não consolida.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agente_ia_edu.services.consolidacao import (
    ESTADO_CONSOLIDADO,
    ESTADO_DEMONSTRADO,
    ESTADO_EM_APRENDIZADO,
    ESTADO_RETIDO,
    ESTADO_SEM_EVIDENCIA,
    intervalo_sugerido,
    situacao,
)

HOJE = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)
ALUNO = "aluno_qa_consolidacao"
CONTEUDO_FIXO = "CHEMISTRY-PHYSICAL-STOICHIOMETRY"


def _tentativa(dias_atras: float, correta: bool, ocasiao: str):
    return {"quando": HOJE - timedelta(days=dias_atras),
            "correta": correta, "ocasiao": ocasiao}


def _ocasiao(dias_atras: float, acertos: int, erros: int = 0, nome: str = ""):
    """Uma ocasião inteira — como um lote de prática corrigido de uma vez."""
    nome = nome or f"oc-{dias_atras}"
    return ([_tentativa(dias_atras, True, nome) for _ in range(acertos)]
            + [_tentativa(dias_atras, False, nome) for _ in range(erros)])


class AUSENCIANAOEDESCONHECIMENTO(unittest.TestCase):

    def test_sem_tentativa_nenhuma_e_SEM_EVIDENCIA(self):
        s = situacao([], agora=HOJE)
        self.assertEqual(ESTADO_SEM_EVIDENCIA, s["estado"])

    def test_e_isso_NAO_e_revisao_recomendada(self):
        """Não se recomenda revisar o que nunca foi visto."""
        self.assertFalse(situacao([], agora=HOJE)["revisao_recomendada"])

    def test_nem_vira_zero_por_cento(self):
        """O §12 proíbe percentual artificial."""
        s = situacao([], agora=HOJE)
        self.assertIsNone(s["acerto"])


class AMOSTRAPEQUENANAOCONCLUI(unittest.TestCase):

    def test_duas_respostas_ainda_e_EM_APRENDIZADO(self):
        s = situacao(_ocasiao(0, acertos=2), agora=HOJE)
        self.assertEqual(ESTADO_EM_APRENDIZADO, s["estado"])

    def test_o_corte_vem_da_POLITICA_existente(self):
        """Nenhum número novo: `min_sample_size` é de quem sempre foi."""
        from agente_ia_edu.services.pedagogical_analysis import (
            PerformanceThresholdPolicy,
        )

        minimo = PerformanceThresholdPolicy.default().min_sample_size
        abaixo = situacao(_ocasiao(0, acertos=minimo - 1), agora=HOJE)
        exato = situacao(_ocasiao(0, acertos=minimo), agora=HOJE)
        self.assertEqual(ESTADO_EM_APRENDIZADO, abaixo["estado"])
        self.assertNotEqual(ESTADO_EM_APRENDIZADO, exato["estado"])


class DEMONSTRADOEUMAOCASIAO(unittest.TestCase):

    def test_acertar_o_bastante_numa_ocasiao_e_DEMONSTRADO(self):
        s = situacao(_ocasiao(0, acertos=3), agora=HOJE)
        self.assertEqual(ESTADO_DEMONSTRADO, s["estado"])

    def test_e_uma_ocasiao_so_NAO_consolida(self):
        s = situacao(_ocasiao(0, acertos=10), agora=HOJE)
        self.assertEqual(ESTADO_DEMONSTRADO, s["estado"],
                         "dez acertos numa tarde não são consistência")

    def test_acerto_fraco_nao_demonstra(self):
        s = situacao(_ocasiao(0, acertos=1, erros=3), agora=HOJE)
        self.assertEqual(ESTADO_EM_APRENDIZADO, s["estado"])


class CONSOLIDADOSAOOCASIOESDIFERENTES(unittest.TestCase):

    def test_duas_ocasioes_fortes_consolidam(self):
        linha = _ocasiao(5, acertos=3, nome="a") + _ocasiao(0, acertos=3, nome="b")
        self.assertEqual(ESTADO_CONSOLIDADO, situacao(linha, agora=HOJE)["estado"])

    def test_a_contagem_de_ocasioes_viaja(self):
        linha = _ocasiao(5, acertos=3, nome="a") + _ocasiao(0, acertos=3, nome="b")
        self.assertEqual(2, situacao(linha, agora=HOJE)["oportunidades"])

    def test_uma_ocasiao_fraca_no_meio_nao_consolida_sozinha(self):
        linha = (_ocasiao(5, acertos=3, nome="a")
                 + _ocasiao(2, acertos=1, erros=3, nome="b"))
        self.assertNotEqual(ESTADO_CONSOLIDADO,
                            situacao(linha, agora=HOJE)["estado"])


class RETIDOEDEPOISDEUMINTERVALO(unittest.TestCase):

    def test_duas_ocasioes_no_MESMO_dia_nao_retem(self):
        linha = _ocasiao(0.1, acertos=3, nome="a") + _ocasiao(0, acertos=3, nome="b")
        self.assertEqual(ESTADO_CONSOLIDADO, situacao(linha, agora=HOJE)["estado"])

    def test_duas_ocasioes_separadas_por_semanas_retem(self):
        linha = _ocasiao(21, acertos=3, nome="a") + _ocasiao(0, acertos=3, nome="b")
        self.assertEqual(ESTADO_RETIDO, situacao(linha, agora=HOJE)["estado"])

    def test_o_intervalo_medido_viaja(self):
        linha = _ocasiao(21, acertos=3, nome="a") + _ocasiao(0, acertos=3, nome="b")
        self.assertGreaterEqual(situacao(linha, agora=HOJE)["intervalo_dias"], 20)

    def test_um_intervalo_longo_com_ERRO_no_fim_nao_retem(self):
        """Lembrar errado não é lembrar."""
        linha = (_ocasiao(21, acertos=3, nome="a")
                 + _ocasiao(0, acertos=1, erros=3, nome="b"))
        self.assertNotEqual(ESTADO_RETIDO, situacao(linha, agora=HOJE)["estado"])


class UMERRONAOAPAGAODOMINIO(unittest.TestCase):
    """A asserção mais importante do arquivo — o §12 é explícito.

    "Revisão recomendada: necessidade de retomada, que NÃO apaga
    automaticamente domínio anterior."
    """

    def _consolidado_e_depois_um_erro(self):
        return (_ocasiao(20, acertos=3, nome="a")
                + _ocasiao(10, acertos=3, nome="b")
                + _ocasiao(0, acertos=0, erros=1, nome="c"))

    def test_o_estado_NAO_cai(self):
        s = situacao(self._consolidado_e_depois_um_erro(), agora=HOJE)
        self.assertIn(s["estado"], (ESTADO_CONSOLIDADO, ESTADO_RETIDO),
                      "um tropeço apagou o que ele já havia demonstrado")

    def test_mas_a_revisao_e_RECOMENDADA(self):
        s = situacao(self._consolidado_e_depois_um_erro(), agora=HOJE)
        self.assertTrue(s["revisao_recomendada"])

    def test_e_o_motivo_e_dito_em_linguagem_de_aluno(self):
        s = situacao(self._consolidado_e_depois_um_erro(), agora=HOJE)
        motivo = s["motivo"].lower()
        self.assertTrue(motivo)
        for jargao in ("band", "accuracy", "evidence", "mastery", "threshold"):
            self.assertNotIn(jargao, motivo)

    def test_quem_nunca_demonstrou_nao_tem_o_que_preservar(self):
        s = situacao(_ocasiao(0, acertos=0, erros=3), agora=HOJE)
        self.assertEqual(ESTADO_EM_APRENDIZADO, s["estado"])


class AREVISAOPORTEMPOEADAPTATIVA(unittest.TestCase):
    """O §18: "sem calendário rígido universal"."""

    def test_o_intervalo_sugerido_cresce_com_o_estado(self):
        self.assertLess(intervalo_sugerido(ESTADO_DEMONSTRADO),
                        intervalo_sugerido(ESTADO_CONSOLIDADO))
        self.assertLess(intervalo_sugerido(ESTADO_CONSOLIDADO),
                        intervalo_sugerido(ESTADO_RETIDO))

    def test_quem_so_demonstrou_pede_revisao_antes(self):
        """Mesmo intervalo, estados diferentes, respostas diferentes."""
        demonstrado = situacao(_ocasiao(20, acertos=3), agora=HOJE)
        consolidado = situacao(
            _ocasiao(25, acertos=3, nome="a") + _ocasiao(20, acertos=3, nome="b"),
            agora=HOJE)
        self.assertTrue(demonstrado["revisao_recomendada"])
        self.assertFalse(consolidado["revisao_recomendada"])

    def test_recem_demonstrado_nao_pede_revisao(self):
        self.assertFalse(
            situacao(_ocasiao(0, acertos=3), agora=HOJE)["revisao_recomendada"])

    def test_sem_evidencia_nunca_pede_revisao_por_tempo(self):
        self.assertFalse(situacao([], agora=HOJE)["revisao_recomendada"])


class SOEVIDENCIAINDEPENDENTECONTA(unittest.TestCase):
    """Acertar com ajuda não consolida — §18."""

    def test_tentativa_assistida_e_ignorada(self):
        linha = [{"quando": HOJE, "correta": True, "ocasiao": "a",
                  "assistida": True} for _ in range(5)]
        self.assertEqual(ESTADO_SEM_EVIDENCIA,
                         situacao(linha, agora=HOJE)["estado"])

    def test_e_nao_entra_na_contagem_de_oportunidades(self):
        linha = ([{"quando": HOJE, "correta": True, "ocasiao": "x",
                   "assistida": True}]
                 + _ocasiao(0, acertos=3, nome="b"))
        self.assertEqual(1, situacao(linha, agora=HOJE)["oportunidades"])


class OMODULOEPURO(unittest.TestCase):
    """Projeção dos dados existentes — §12: não é segunda fonte de verdade."""

    def _fonte(self) -> str:
        import pathlib

        from _fonte import codigo

        raiz = pathlib.Path(__file__).resolve().parent.parent
        return codigo(raiz / "src/agente_ia_edu/services/consolidacao.py")

    def test_nao_le_banco(self):
        for proibido in ("sqlalchemy", "AsyncSession", "select("):
            with self.subTest(proibido):
                self.assertNotIn(proibido, self._fonte())

    def test_e_nao_inventa_corte_proprio(self):
        """Os números de acerto e amostra são da política, não daqui."""
        self.assertIn("PerformanceThresholdPolicy", self._fonte())


if __name__ == "__main__":
    unittest.main()


class OLEITORMONTAALINHADOTEMPO(unittest.TestCase):
    """A ponte entre o histórico gravado e o módulo puro.

    O cálculo tem 27 testes e não toca banco. O que falta provar é que a
    linha do tempo que chega até ele é a certa — e é aqui que um erro passa
    calado: agrupar por questão em vez de por resultado faria cinco acertos
    numa tarde virarem cinco oportunidades, e a consolidação sairia de graça.
    """

    def setUp(self):
        import asyncio
        import uuid as _uuid

        from sqlalchemy.ext.asyncio import (
            AsyncSession, async_sessionmaker, create_async_engine,
        )
        from sqlalchemy.pool import StaticPool

        from agente_ia_edu.db.base import Base

        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:",
                                          poolclass=StaticPool)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession,
                                          expire_on_commit=False)
        self._uuid = _uuid

        async def prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        self.loop.run_until_complete(prep())

    def tearDown(self):
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _gravar(self, ocasioes):
        """Grava ocasiões reais: [(dias_atras, [(skill, certa), ...]), ...]."""
        from agente_ia_edu.db.models import (
            ActivityResult, ActivityResultItem, PedagogicalClassification,
            Question, QuestionVersion,
        )

        async def nova_versao(s, skill):
            """UMA questão nova por resposta.

            `activity_result_items` tem UNIQUE(result_id, question_version_id)
            — e com razão: um lote NÃO repete a mesma questão. A primeira
            versão desta fixture reusava a questão por habilidade e a
            constraint a recusou, o que é o banco defendendo a realidade.
            """
            q = Question(validation_status="valid", origin_type="GENERATED",
                         status="PUBLISHED", visibility_scope="PUBLIC",
                         question_type="MULTIPLE_CHOICE")
            s.add(q)
            await s.flush()
            vid = self._uuid.uuid4()
            s.add(QuestionVersion(
                id=vid, question_id=q.id, version_kind="official_original",
                canonical_text=skill, statement=skill,
                content_hash=str(self._uuid.uuid4())))
            await s.flush()
            s.add(PedagogicalClassification(
                question_version_id=vid, discipline="CURRICULUM_PROPOSAL",
                content=CONTEUDO_FIXO, subcontent=skill, difficulty="EASY",
                reasoning_type="DIAGNOSTIC", prerequisites=[], keywords=[],
                competencies=[], skills=[skill], status="CLASSIFIED",
                source="rule", lifecycle="ACTIVE", prompt_version="t"))
            return vid

        async def run():
            async with self.factory() as s:
                for dias, respostas in ocasioes:
                    quando = HOJE - timedelta(days=dias)
                    rid = self._uuid.uuid4()
                    s.add(ActivityResult(
                        id=rid, attempt_id=self._uuid.uuid4(),
                        assignment_id=self._uuid.uuid4(),
                        student_external_id=ALUNO,
                        assessment_version_id=self._uuid.uuid4(),
                        question_count=len(respostas),
                        answered_count=len(respostas),
                        correct_count=sum(1 for _s, c in respostas if c),
                        incorrect_count=sum(1 for _s, c in respostas if not c),
                        unanswered_count=0, corrected_at=quando))
                    await s.flush()
                    for pos, (skill, certa) in enumerate(respostas, start=1):
                        s.add(ActivityResultItem(
                            id=self._uuid.uuid4(), result_id=rid,
                            question_version_id=await nova_versao(s, skill),
                            position=pos, is_correct=certa, answered=True))
                await s.commit()
        self.loop.run_until_complete(run())

    def _situacoes(self):
        from agente_ia_edu.services.consolidacao_do_aluno import (
            situacao_por_habilidade,
        )

        async def run():
            async with self.factory() as s:
                return await situacao_por_habilidade(
                    s, aluno=ALUNO, conteudo=CONTEUDO_FIXO, agora=HOJE)
        return self.loop.run_until_complete(run())

    # -- os testes ---------------------------------------------------------

    def test_sem_historico_nao_ha_habilidade_nenhuma(self):
        self.assertEqual({}, self._situacoes())

    def test_uma_ocasiao_forte_e_DEMONSTRADO(self):
        self._gravar([(0, [("MASSA_MOLAR", True)] * 3)])
        self.assertEqual(ESTADO_DEMONSTRADO,
                         self._situacoes()["MASSA_MOLAR"]["estado"])

    def test_CINCO_acertos_numa_tarde_sao_UMA_oportunidade(self):
        """O erro que agrupar por questão produziria."""
        self._gravar([(0, [("MASSA_MOLAR", True)] * 5)])
        s = self._situacoes()["MASSA_MOLAR"]
        self.assertEqual(1, s["oportunidades"])
        self.assertEqual(ESTADO_DEMONSTRADO, s["estado"])

    def test_duas_ocasioes_separadas_por_semanas_retem(self):
        self._gravar([(21, [("MASSA_MOLAR", True)] * 3),
                      (0, [("MASSA_MOLAR", True)] * 3)])
        s = self._situacoes()["MASSA_MOLAR"]
        self.assertEqual(ESTADO_RETIDO, s["estado"])
        self.assertEqual(2, s["oportunidades"])

    def test_cada_habilidade_tem_a_sua_linha(self):
        self._gravar([(0, [("MASSA_MOLAR", True)] * 3
                       + [("LEITURA_DE_FORMULA", False)] * 3)])
        s = self._situacoes()
        self.assertEqual(ESTADO_DEMONSTRADO, s["MASSA_MOLAR"]["estado"])
        self.assertEqual(ESTADO_EM_APRENDIZADO,
                         s["LEITURA_DE_FORMULA"]["estado"])

    def test_um_erro_depois_de_consolidado_NAO_derruba(self):
        """A invariante do §12, agora com banco de verdade."""
        self._gravar([(20, [("MASSA_MOLAR", True)] * 3),
                      (10, [("MASSA_MOLAR", True)] * 3),
                      (0, [("MASSA_MOLAR", False)])])
        s = self._situacoes()["MASSA_MOLAR"]
        self.assertIn(s["estado"], (ESTADO_CONSOLIDADO, ESTADO_RETIDO))
        self.assertTrue(s["revisao_recomendada"])

    def test_o_historico_de_OUTRO_aluno_nao_entra(self):
        from agente_ia_edu.services.consolidacao_do_aluno import (
            situacao_por_habilidade,
        )

        self._gravar([(0, [("MASSA_MOLAR", True)] * 3)])

        async def run():
            async with self.factory() as s:
                return await situacao_por_habilidade(
                    s, aluno="outro_aluno", conteudo=CONTEUDO_FIXO, agora=HOJE)
        self.assertEqual({}, self.loop.run_until_complete(run()))

    def test_a_pratica_guiada_NAO_entra(self):
        """Ela mora noutra tabela — a garantia é estrutural, não um filtro."""
        from agente_ia_edu.db.models.guided_practice import GuidedPracticeItem

        async def guiada():
            async with self.factory() as s:
                s.add(GuidedPracticeItem(
                    student_external_id=ALUNO, item_key="GPG-1",
                    content_code=CONTEUDO_FIXO, skill="MASSA_MOLAR",
                    attempts=1, completed=True, solved_unaided=True))
                await s.commit()
        self.loop.run_until_complete(guiada())
        self.assertEqual({}, self._situacoes())
