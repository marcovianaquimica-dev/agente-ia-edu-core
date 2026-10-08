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

import json
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



class OQUECHEGAAOALUNONAOTEMCODIGONEMNUMERO(unittest.TestCase):
    """O vazamento que `test_aluno_ciclo_pedro` pegou em 2026-10-08.

    `/student/progress` passou a devolver `habilidades` com o estado de
    consolidação de cada micro-habilidade — e devolvia o dicionário cru de
    `situacao`: a CHAVE era o código curricular, e os valores traziam
    `acerto: 1.0` e `respondidas: 5`.

    O próprio `student_progress` diz, no docstring de `panorama_do_aluno`,
    que "o código curricular não atravessa: ele é vocabulário do motor, não
    do aluno" — e o §12 proíbe o percentual. A tradução estava faltando, e
    o teste de ponta a ponta do ciclo do aluno a cobrou.

    Esta classe fixa a tradução no lugar onde ela pertence: a função que
    prepara o que o aluno vê.
    """

    def _entrada(self, estado, *, revisao=False, oportunidades=2):
        return {"MASSA_MOLAR": {
            "estado": estado, "acerto": 1.0, "respondidas": 5,
            "oportunidades": oportunidades, "oportunidades_fortes": oportunidades,
            "intervalo_dias": 14, "ultima_em": "2026-10-08T12:00:00+00:00",
            "revisao_recomendada": revisao,
            "motivo": "Você mostrou isso uma vez, sozinho."}}

    def test_o_codigo_da_habilidade_nao_atravessa_cru(self):
        from agente_ia_edu.services.consolidacao import ESTADO_DEMONSTRADO
        from agente_ia_edu.services.student_progress import (
            habilidades_para_o_aluno,
        )

        saida = habilidades_para_o_aluno(self._entrada(ESTADO_DEMONSTRADO))
        bruto = json.dumps(saida, ensure_ascii=False)
        self.assertNotIn("MASSA_MOLAR", bruto)
        self.assertIn("Massa molar", bruto)

    def test_e_nenhum_numero_de_desempenho_vai_junto(self):
        from agente_ia_edu.services.consolidacao import ESTADO_DEMONSTRADO
        from agente_ia_edu.services.student_progress import (
            habilidades_para_o_aluno,
        )

        bruto = json.dumps(habilidades_para_o_aluno(
            self._entrada(ESTADO_DEMONSTRADO)), ensure_ascii=False)
        for proibido in ("acerto", "accuracy", "respondidas", "1.0", "%",
                         "oportunidades_fortes"):
            with self.subTest(proibido):
                self.assertNotIn(proibido, bruto)

    def test_nem_o_nome_interno_do_estado(self):
        from agente_ia_edu.services.consolidacao import ESTADO_DEMONSTRADO
        from agente_ia_edu.services.student_progress import (
            habilidades_para_o_aluno,
        )

        bruto = json.dumps(habilidades_para_o_aluno(
            self._entrada(ESTADO_DEMONSTRADO)), ensure_ascii=False)
        self.assertNotIn(ESTADO_DEMONSTRADO, bruto)

    def test_o_estado_vem_do_nome_curto_da_consolidacao(self):
        """Uma tradução só. Duas tabelas de nome divergiriam."""
        from agente_ia_edu.services.consolidacao import (
            ESTADO_CONSOLIDADO,
            nome_curto,
        )
        from agente_ia_edu.services.student_progress import (
            habilidades_para_o_aluno,
        )

        saida = habilidades_para_o_aluno(self._entrada(ESTADO_CONSOLIDADO))
        self.assertEqual(nome_curto(ESTADO_CONSOLIDADO), saida[0]["estado"])

    def test_a_revisao_recomendada_continua_chegando(self):
        """Ela é o que o aluno PODE AGIR sobre — não pode cair na tradução."""
        from agente_ia_edu.services.consolidacao import ESTADO_DEMONSTRADO
        from agente_ia_edu.services.student_progress import (
            habilidades_para_o_aluno,
        )

        saida = habilidades_para_o_aluno(
            self._entrada(ESTADO_DEMONSTRADO, revisao=True))
        self.assertTrue(saida[0]["revisar"])

    def test_sem_habilidade_nenhuma_devolve_lista_vazia(self):
        from agente_ia_edu.services.student_progress import (
            habilidades_para_o_aluno,
        )

        self.assertEqual([], habilidades_para_o_aluno({}))
        self.assertEqual([], habilidades_para_o_aluno(None))


if __name__ == "__main__":
    unittest.main()
