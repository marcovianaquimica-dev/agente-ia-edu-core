"""OS QUATRO MODOS — e o único em que errar interrompe a sequência.

POR QUE ESTE ARQUIVO EXISTE
============================
A validação manual de 2026-10-08 mostrou o estudante errando questões numa
prática e a plataforma servindo a próxima, e a próxima, até "5 de 5". A
correção é fazer o erro produzir uma decisão pedagógica — mas ela não pode
valer em todo lugar:

    DIAGNÓSTICO       ensinar no meio contamina as observações seguintes
    PRÁTICA FORMATIVA é exatamente aqui que o Edu precisa intervir
    VERIFICAÇÃO L0    dica durante a tentativa destrói a independência
    AVALIAÇÃO FORMAL  ensinar durante a prova viola a política da avaliação

Então o modo é o PORTÃO, e ele vem antes de qualquer decisão.

O MODO NÃO É UM CAMPO NOVO
===========================
Ele já viaja no `metadata` da atribuição desde que a prática existe:
`origin` e `purpose`, gravados na criação. Inventar um quinto campo criaria
uma segunda verdade sobre o que aquele lote é — e as duas divergiriam no
primeiro lote criado por um caminho que esquecesse de preencher o novo.

FAIL-CLOSED
============
Metadata ausente, vazio ou desconhecido NÃO é prática formativa. Uma
atribuição que este módulo não reconhece é tratada como avaliação formal, que
é o modo mais restritivo: errar por excesso de cuidado deixa o aluno sem uma
intervenção; errar por falta dela interrompe uma prova.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.modo_pedagogico import (
    MODO_AVALIACAO,
    MODO_DIAGNOSTICO,
    MODO_FORMATIVO,
    MODO_VERIFICACAO,
    intervem_no_erro,
    modo_de,
)


class OSQUATROMODOS(unittest.TestCase):
    """Lidos do metadata REAL, copiado do banco de desenvolvimento."""

    def test_o_microdiagnostico_e_diagnostico(self):
        self.assertEqual(MODO_DIAGNOSTICO, modo_de({
            "mode": "PRACTICE_CONTENT", "origin": "MICRO_DIAGNOSTIC",
            "purpose": "PRACTICE", "practice": True,
            "content_code": "CHEMISTRY-PHYSICAL-STOICHIOMETRY"}))

    def test_a_pratica_e_formativa(self):
        self.assertEqual(MODO_FORMATIVO, modo_de({
            "mode": "PRACTICE_CONTENT", "origin": "PRACTICE",
            "purpose": "PRACTICE", "practice": True,
            "content_code": "CHEMISTRY-PHYSICAL-STOICHIOMETRY"}))

    def test_a_verificacao_e_verificacao(self):
        self.assertEqual(MODO_VERIFICACAO, modo_de({
            "mode": "PRACTICE_CONTENT", "origin": "PRACTICE",
            "purpose": "VERIFY", "practice": True,
            "content_code": "CHEMISTRY-PHYSICAL-STOICHIOMETRY"}))

    def test_a_tarefa_da_escola_e_avaliacao_formal(self):
        """Atribuição de professor: sem a marca `practice`."""
        self.assertEqual(MODO_AVALIACAO, modo_de({}))


class SOOFORMATIVOINTERROMPE(unittest.TestCase):
    """A invariante inteira deste módulo."""

    def test_so_a_pratica_formativa_intervem_no_erro(self):
        self.assertTrue(intervem_no_erro(MODO_FORMATIVO))

    def test_o_diagnostico_NAO_intervem(self):
        """Ensinar no meio contamina a observação seguinte."""
        self.assertFalse(intervem_no_erro(MODO_DIAGNOSTICO))

    def test_a_verificacao_NAO_intervem(self):
        """Dica durante a tentativa destrói a independência da evidência."""
        self.assertFalse(intervem_no_erro(MODO_VERIFICACAO))

    def test_a_avaliacao_formal_NAO_intervem(self):
        self.assertFalse(intervem_no_erro(MODO_AVALIACAO))

    def test_exatamente_UM_modo_intervem(self):
        """Se alguém acrescentar um modo, tem de decidir conscientemente."""
        todos = (MODO_DIAGNOSTICO, MODO_FORMATIVO, MODO_VERIFICACAO,
                 MODO_AVALIACAO)
        self.assertEqual(1, sum(1 for m in todos if intervem_no_erro(m)))


class FALHAFECHADO(unittest.TestCase):
    """O desconhecido vira o modo mais restritivo, nunca o mais permissivo."""

    def test_metadata_ausente(self):
        self.assertEqual(MODO_AVALIACAO, modo_de(None))

    def test_metadata_vazio(self):
        self.assertEqual(MODO_AVALIACAO, modo_de({}))

    def test_origin_desconhecido_nao_vira_formativo(self):
        self.assertNotEqual(MODO_FORMATIVO, modo_de({
            "practice": True, "origin": "ALGO_QUE_NAO_EXISTE",
            "purpose": "PRACTICE"}))

    def test_purpose_desconhecido_nao_vira_formativo(self):
        self.assertNotEqual(MODO_FORMATIVO, modo_de({
            "practice": True, "origin": "PRACTICE",
            "purpose": "ALGO_QUE_NAO_EXISTE"}))

    def test_sem_a_marca_practice_nunca_e_formativo(self):
        """Mesmo com origin e purpose certos: é tarefa de professor."""
        self.assertEqual(MODO_AVALIACAO, modo_de({
            "origin": "PRACTICE", "purpose": "PRACTICE"}))

    def test_e_nenhum_desconhecido_intervem(self):
        for meta in (None, {}, {"practice": True, "origin": "X"},
                     {"origin": "PRACTICE"}):
            with self.subTest(meta=meta):
                self.assertFalse(intervem_no_erro(modo_de(meta)))


class ONOMEDOMODOCHEGAAOCLIENTE(unittest.TestCase):
    """§16: sem o modo viajando como dado, depurar exige reler o código."""

    def test_os_nomes_sao_estaveis_e_distintos(self):
        nomes = {MODO_DIAGNOSTICO, MODO_FORMATIVO, MODO_VERIFICACAO,
                 MODO_AVALIACAO}
        self.assertEqual(4, len(nomes))
        for n in nomes:
            with self.subTest(n):
                self.assertTrue(n.isupper())


if __name__ == "__main__":
    unittest.main()
