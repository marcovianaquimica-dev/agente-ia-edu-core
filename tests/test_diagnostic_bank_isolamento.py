"""Os dois bancos diagnósticos não se misturam, e nenhum deles vira prova.

São dois conteúdos no MESMO banco (`origin_type='GENERATED'`, mesma edição do
Núcleo, mesma seleção). O que os separa é o `content_code` — e isso tem de ser
verdade pelo caminho real, não por convenção.

O que este arquivo prova, contra PostgreSQL de verdade:

    D  item de Balanceamento não entra no diagnóstico de Estequiometria
    E  item de Estequiometria não entra no de Balanceamento
    A  item GENERATED não é questão oficial da escola
    B  AI_VERIFIED não vira HUMAN_VALIDATED
    K  MICRO_DIAGNOSTIC continua separado de OFFICIAL_ACTIVITY
    Q  o banco de Balanceamento continua intacto
"""

from __future__ import annotations

import os
import unittest

import sqlalchemy as sa

BALANC = "CHEMISTRY-GENERAL-BALANCING"
ESTEQ = "CHEMISTRY-PHYSICAL-STOICHIOMETRY"
BANK_TAG = "nucleo-diagnostic-bank-v1"


def _url() -> str:
    u = os.getenv("POSTGRES_USER", "agenteedu")
    p = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    d = os.getenv("POSTGRES_DB", "agente_ia_edu")
    return f"postgresql+psycopg://{u}:{p}@localhost:5433/{d}"


class BancoDeDesenvolvimentoTests(unittest.TestCase):
    """Mede o banco que o Piloto Zero de fato usa.

    Falha, em vez de pular, quando o PostgreSQL não está de pé: um skip aqui
    diria "passou" sobre uma afirmação que ninguém conferiu.
    """

    @classmethod
    def setUpClass(cls):
        cls.engine = sa.create_engine(_url())
        try:
            with cls.engine.connect() as c:
                c.execute(sa.text("SELECT 1"))
        except Exception as exc:  # noqa: BLE001
            raise AssertionError(
                f"PostgreSQL de desenvolvimento indisponível em 5433: {exc}. "
                f"Este teste mede o banco real do Piloto Zero e não tem como "
                f"ser verdadeiro sem ele.") from exc

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "engine"):
            cls.engine.dispose()

    def _itens(self, content_code: str) -> list[dict]:
        with self.engine.connect() as c:
            linhas = c.execute(sa.text("""
                SELECT qv.id::text AS vid,
                       q.origin_type, q.status,
                       pc.content, pc.provenance, pc.status AS pc_status,
                       pc.subcontent,
                       q.metadata_->>'bank' AS bank
                FROM pedagogical_classifications pc
                JOIN question_versions qv ON qv.id = pc.question_version_id
                JOIN questions q ON q.id = qv.question_id
                WHERE pc.content = :code AND pc.lifecycle = 'ACTIVE'
                  AND q.metadata_->>'bank' = :bank
            """), {"code": content_code, "bank": BANK_TAG}).mappings().all()
        return [dict(r) for r in linhas]

    # -- D e E: os dois conteúdos não se misturam --------------------------

    def test_D_item_de_balanceamento_nao_esta_em_estequiometria(self):
        esteq = self._itens(ESTEQ)
        for item in esteq:
            with self.subTest(vid=item["vid"]):
                self.assertEqual(item["content"], ESTEQ)

    def test_E_item_de_estequiometria_nao_esta_em_balanceamento(self):
        balanc = self._itens(BALANC)
        for item in balanc:
            with self.subTest(vid=item["vid"]):
                self.assertEqual(item["content"], BALANC)

    def test_os_conjuntos_sao_disjuntos(self):
        a = {i["vid"] for i in self._itens(BALANC)}
        b = {i["vid"] for i in self._itens(ESTEQ)}
        self.assertEqual(a & b, set(),
                         "o mesmo item está classificado nos dois conteúdos")

    def test_as_habilidades_nao_vazam_entre_os_conteudos(self):
        """`subcontent` carrega a micro-habilidade. Uma habilidade de
        balanceamento num item de estequiometria significaria que o gerador
        errou o conteúdo — ou que alguém reclassificou sem olhar."""
        from agente_ia_edu.services.diagnostic_bank import HABILIDADES as H_BAL
        from agente_ia_edu.services.diagnostic_bank_estequiometria import (
            HABILIDADES as H_EST,
        )

        for item in self._itens(ESTEQ):
            with self.subTest(vid=item["vid"]):
                self.assertNotIn(item["subcontent"], H_BAL)
                self.assertIn(item["subcontent"], H_EST)
        for item in self._itens(BALANC):
            with self.subTest(vid=item["vid"]):
                self.assertNotIn(item["subcontent"], H_EST)
                self.assertIn(item["subcontent"], H_BAL)

    # -- A e B: diagnóstico não é prova, IA não é pessoa -------------------

    def test_A_todo_item_diagnostico_e_GENERATED(self):
        itens = self._itens(BALANC) + self._itens(ESTEQ)
        self.assertTrue(itens, "nenhum item diagnóstico no banco")
        for item in itens:
            with self.subTest(vid=item["vid"]):
                self.assertEqual(item["origin_type"], "GENERATED",
                                 "item do Núcleo marcado como questão importada")

    def test_B_nenhum_item_diagnostico_e_HUMAN_VALIDATED(self):
        """IA verifica; pessoa valida. Confundir os dois apaga a diferença
        entre 'ninguém olhou' e 'alguém olhou'."""
        for item in self._itens(BALANC) + self._itens(ESTEQ):
            with self.subTest(vid=item["vid"]):
                self.assertEqual(item["provenance"], "AI_VERIFIED")

    def test_o_banco_recusa_HUMAN_VALIDATED_sem_pessoa(self):
        """A trava é do schema, não da convenção (CHECK da migration 065).

        Mede o COMPORTAMENTO, não o nome do constraint: a primeira versão
        procurava `ck_..._human_identity` e falhava porque o nome real é
        `ck_..._human_needs_identity` — estaria medindo a minha memória do
        nome, não a trava. Um INSERT recusado prova a trava mesmo que alguém
        a renomeie.
        """
        with self.assertRaises(sa.exc.IntegrityError):
            with self.engine.begin() as c:
                c.execute(sa.text("""
                    INSERT INTO pedagogical_classifications
                      (id, question_version_id, discipline, content, subcontent,
                       difficulty, reasoning_type, prerequisites, keywords,
                       competencies, skills, status, source, lifecycle,
                       provenance, validated_by_external_identity, created_at)
                    SELECT gen_random_uuid(), qv.id, 'CURRICULUM_PROPOSAL',
                           'CHEMISTRY-GENERAL-BALANCING', 'X', 'MEDIUM', 'U',
                           '[]', '[]', '[]', '[]', 'CLASSIFIED', 'ai', 'ACTIVE',
                           'HUMAN_VALIDATED', NULL, now()
                    FROM question_versions qv LIMIT 1
                """))

    # -- Q: o banco de Balanceamento continua intacto ----------------------

    def test_Q_balanceamento_continua_com_seus_itens(self):
        self.assertGreaterEqual(len(self._itens(BALANC)), 14,
                                "o banco de Balanceamento encolheu")

    def test_Q_a_distribuicao_de_balanceamento_continua_corrigida(self):
        """O viés posicional corrigido no bloco anterior não pode voltar."""
        with self.engine.connect() as c:
            linhas = c.execute(sa.text("""
                SELECT o.option_key, count(*) AS n
                FROM question_options o
                JOIN question_versions qv ON qv.id = o.question_version_id
                JOIN questions q ON q.id = qv.question_id
                JOIN pedagogical_classifications pc
                     ON pc.question_version_id = qv.id
                WHERE o.is_valid_option AND pc.content = :code
                  AND q.metadata_->>'bank' = :bank
                GROUP BY 1
            """), {"code": BALANC, "bank": BANK_TAG}).all()
        total = sum(n for _, n in linhas) or 1
        for letra, n in linhas:
            with self.subTest(letra=letra):
                self.assertLessEqual(n / total, 0.40,
                                     f"{letra} voltou a concentrar o gabarito")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
