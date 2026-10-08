"""HIPÓTESE NÃO É DIAGNÓSTICO — §4 e §10.

O caso que o bloco nomeia: o aluno responde 15 para "qual é a massa molar
do NH₃?". É plausível que tenha feito 14 + 1. Mas um aluno que chutou, um
que errou a soma e um que ignorou o índice escrevem o mesmo 15.

Dizer ao primeiro "você esqueceu de multiplicar o hidrogênio por 3" é
inventar sobre ele. O distrator SUGERE; a micropergunta DECIDE.

O QUE ESTE ARQUIVO TRAVA
=========================
1. a máquina de estados só se move com observação real;
2. ambiguidade não move hipótese;
3. acertar a discriminante ENFRAQUECE — não rejeita;
4. a frase que chega ao aluno é suposição, nunca afirmação;
5. nada daqui conhece química nem toca domínio.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from _fonte import codigo, menciona

from agente_ia_edu.services.hipotese_pedagogica import (
    ABERTA,
    APOIADA,
    ENFRAQUECIDA,
    ESTADOS,
    REJEITADA,
    Hipotese,
    aponta_para_prerequisito,
    atualizar,
)

H = Hipotese(codigo="INDEX_OMISSION", habilidade_suspeita="BASE",
             como_dizer="Esse resultado pode indicar que vale olhar o índice.",
             discriminante=1)


class AMAQUINASOANDACOMOBSERVACAO(unittest.TestCase):

    def test_nasce_aberta_e_continua_aberta_sem_resposta(self):
        self.assertEqual(ABERTA, atualizar(ABERTA, discriminante_correta=None))

    def test_errar_a_discriminante_apoia(self):
        self.assertEqual(APOIADA, atualizar(ABERTA,
                                            discriminante_correta=False))

    def test_acertar_a_discriminante_ENFRAQUECE_e_nao_rejeita(self):
        """Contar os três hidrogênios quando perguntado diretamente não
        prova que ele os contou ao fazer a conta sozinho. O que se aprendeu
        é que a leitura não é o problema BÁSICO."""
        novo = atualizar(ABERTA, discriminante_correta=True)
        self.assertEqual(ENFRAQUECIDA, novo)
        self.assertNotEqual(REJEITADA, novo)

    def test_ambiguidade_nao_move_a_hipotese(self):
        self.assertEqual(ABERTA, atualizar(ABERTA, discriminante_correta=None))

    def test_estado_ja_resolvido_nao_volta_atras(self):
        for resolvido in (APOIADA, ENFRAQUECIDA, REJEITADA):
            for resposta in (True, False, None):
                with self.subTest(estado=resolvido, resposta=resposta):
                    self.assertEqual(
                        resolvido,
                        atualizar(resolvido, discriminante_correta=resposta))

    def test_estado_desconhecido_cai_em_aberta(self):
        self.assertEqual(ABERTA, atualizar("QUALQUER_COISA",
                                           discriminante_correta=True))

    def test_sao_quatro_estados_e_nenhum_significa_dominio(self):
        self.assertEqual(4, len(ESTADOS))
        for e in ESTADOS:
            with self.subTest(estado=e):
                for proibido in ("MASTER", "DOMIN", "LEARNED", "PROVEN"):
                    self.assertNotIn(proibido, e.upper())


class ADESCIDANOGRAFO(unittest.TestCase):
    """Caminho B do §5: a hipótese apoiada aponta um degrau abaixo."""

    def test_hipotese_sobre_outra_habilidade_aponta_para_pre_requisito(self):
        self.assertTrue(aponta_para_prerequisito(H, alvo="ALVO"))

    def test_hipotese_sobre_o_proprio_alvo_nao_desce(self):
        mesma = Hipotese(codigo="X", habilidade_suspeita="ALVO",
                         como_dizer="...", discriminante=1)
        self.assertFalse(aponta_para_prerequisito(mesma, alvo="ALVO"))

    def test_hipotese_sem_habilidade_nao_desce(self):
        vazia = Hipotese(codigo="X", habilidade_suspeita="",
                         como_dizer="...", discriminante=1)
        self.assertFalse(aponta_para_prerequisito(vazia, alvo="ALVO"))


class AHIPOTESEPRECISADETESTE(unittest.TestCase):
    """Palpite sem como ser testado vira afirmação."""

    def test_toda_hipotese_declara_a_discriminante(self):
        self.assertIsNotNone(H.discriminante)
        self.assertGreater(H.discriminante, 0)

    def test_o_contrato_exige_a_discriminante(self):
        campos = Hipotese.__dataclass_fields__
        self.assertIn("discriminante", campos)
        # Sem default: esquecê-la tem de ser erro, não silêncio.
        import dataclasses
        self.assertIs(dataclasses.MISSING,
                      campos["discriminante"].default)


class NAOHAPSEUDOESTATISTICA(unittest.TestCase):
    """§10: não inventar confiança numérica."""

    FONTE = (pathlib.Path(__file__).resolve().parent.parent
             / "src/agente_ia_edu/services/hipotese_pedagogica.py")

    def test_o_contrato_nao_tem_campo_de_confianca(self):
        campos = set(Hipotese.__dataclass_fields__)
        for proibido in ("confianca", "confidence", "probabilidade",
                         "score", "peso"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, campos)

    def test_o_modulo_nao_escreve_literal_float(self):
        for no in ast.walk(ast.parse(self.FONTE.read_text(encoding="utf-8"))):
            if isinstance(no, ast.Constant) and isinstance(no.value, float):
                self.fail(f"literal float na hipotese: {no.value}")


class OMODULOEGENERICO(unittest.TestCase):

    FONTE = (pathlib.Path(__file__).resolve().parent.parent
             / "src/agente_ia_edu/services/hipotese_pedagogica.py")

    def test_nao_conhece_quimica(self):
        fonte = codigo(self.FONTE)
        for proibido in ("NH", "NH3", "CO2", "MASSA_MOLAR", "mol",
                         "LEITURA_DE_FORMULA", "indice", "atomo"):
            with self.subTest(proibido=proibido):
                self.assertFalse(menciona(fonte, proibido))

    def test_nao_toca_banco_nem_provedor(self):
        baixo = codigo(self.FONTE).lower()
        for proibido in ("asyncsession", "sqlalchemy", "provider", "openai"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, baixo)

    def test_nao_importa_a_politica_de_dominio(self):
        arvore = ast.parse(self.FONTE.read_text(encoding="utf-8"))
        nomes = [no.module or "" for no in ast.walk(arvore)
                 if isinstance(no, ast.ImportFrom)]
        for proibido in ("pedagogical_analysis", "curriculum_domain_map"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, nomes)


if __name__ == "__main__":
    unittest.main()
