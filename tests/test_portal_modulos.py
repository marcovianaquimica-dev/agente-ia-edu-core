"""O catálogo de módulos do Núcleo Edu 360, e o que cada pessoa vê dele.

O QUE ESTE ARQUIVO PROTEGE
===========================
A Home do Portal é a primeira coisa que alguém vê do produto. Duas falhas
seriam caras ali:

  1. mostrar "Acessar" num módulo que a pessoa não pode abrir — o clique
     levaria a um 403, e numa apresentação isso é pior que não mostrar;
  2. mostrar um módulo futuro como se fosse usável.

Então o catálogo diz o que EXISTE, a autorização diz o que está LIBERADO, e
o cruzamento é testado aqui.

O FRONTEND NÃO É A AUTORIDADE
==============================
Esconder um card não protege nada: as rotas de cada módulo continuam com os
seus próprios guards. O que este cruzamento evita é oferecer uma porta que
vai bater na cara da pessoa.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.modulos_do_portal import (
    EM_BREVE,
    DISPONIVEL,
    MODULOS,
    modulos_para,
)


class OCatalogoTests(unittest.TestCase):

    def test_os_seis_modulos_do_ecossistema_estao_declarados(self):
        chaves = [m["key"] for m in MODULOS]
        self.assertEqual(
            set(chaves),
            {"REDACAO_IA", "AGENTE_IA_EDU", "SAEB", "ACADEMICO",
             "FORMACAO", "SIMULADOS"})

    def test_cada_modulo_tem_titulo_e_descricao_curta(self):
        for m in MODULOS:
            with self.subTest(modulo=m["key"]):
                self.assertTrue(m["title"].strip())
                self.assertTrue(m["description"].strip())
                self.assertLessEqual(
                    len(m["description"]), 120,
                    "descricao longa demais - a Home nao e landing page")

    def test_so_os_dois_modulos_prontos_sao_DISPONIVEL(self):
        prontos = [m["key"] for m in MODULOS if m["status"] == DISPONIVEL]
        self.assertEqual(set(prontos), {"REDACAO_IA", "AGENTE_IA_EDU"})

    def test_modulo_EM_BREVE_nao_tem_rota(self):
        """Uma rota num módulo que não existe é uma porta para lugar nenhum."""
        for m in MODULOS:
            if m["status"] == EM_BREVE:
                with self.subTest(modulo=m["key"]):
                    self.assertIsNone(m["route"])

    def test_modulo_DISPONIVEL_tem_rota_absoluta(self):
        for m in MODULOS:
            if m["status"] == DISPONIVEL:
                with self.subTest(modulo=m["key"]):
                    self.assertTrue((m["route"] or "").startswith("/"))

    def test_cada_modulo_declara_quem_pode_entrar(self):
        for m in MODULOS:
            with self.subTest(modulo=m["key"]):
                self.assertIsInstance(m["roles"], tuple)


class OQueCadaPessoaVeTests(unittest.TestCase):
    """`modulos_para` cruza catálogo × módulos contratados × papel."""

    def _ver(self, **kw):
        base = dict(role="STUDENT", contratados=("AGENTE_IA_EDU", "REDACAO_IA"),
                    is_platform_admin=False)
        base.update(kw)
        return {m["key"]: m for m in modulos_para(**base)}

    def test_os_seis_sempre_aparecem(self):
        """O ecossistema inteiro é visível: é isso que a Home comunica."""
        self.assertEqual(len(self._ver()), 6)

    def test_modulo_contratado_e_permitido_fica_acessivel(self):
        v = self._ver()
        self.assertTrue(v["AGENTE_IA_EDU"]["can_access"])
        self.assertTrue(v["REDACAO_IA"]["can_access"])

    def test_modulo_NAO_contratado_nao_vira_acesso(self):
        """A escola que não contratou Redação não ganha um botão para ela."""
        v = self._ver(contratados=("AGENTE_IA_EDU",))
        self.assertFalse(v["REDACAO_IA"]["can_access"])
        self.assertTrue(v["AGENTE_IA_EDU"]["can_access"])

    def test_modulo_nao_contratado_e_dito_como_tal_nao_como_em_breve(self):
        """"Em breve" e "sua escola não contratou" são coisas diferentes, e
        confundi-las faria o Portal mentir sobre o produto."""
        v = self._ver(contratados=("AGENTE_IA_EDU",))
        self.assertEqual(v["REDACAO_IA"]["status"], DISPONIVEL)
        self.assertFalse(v["REDACAO_IA"]["contracted"])

    def test_EM_BREVE_nunca_e_acessivel_nem_contratado(self):
        for chave, m in self._ver().items():
            if m["status"] == EM_BREVE:
                with self.subTest(modulo=chave):
                    self.assertFalse(m["can_access"])
                    self.assertIsNone(m["route"])

    def test_papel_sem_permissao_nao_recebe_acesso(self):
        """A secretaria (recepção) não entra no Assessor do aluno."""
        v = self._ver(role="SECRETARY")
        self.assertFalse(v["AGENTE_IA_EDU"]["can_access"])

    def test_admin_da_plataforma_nao_perde_acesso_por_papel(self):
        v = self._ver(role="SECRETARY", is_platform_admin=True)
        self.assertTrue(v["AGENTE_IA_EDU"]["can_access"])

    def test_sem_contrato_nenhum_ninguem_acessa_nada(self):
        v = self._ver(contratados=())
        self.assertEqual([k for k, m in v.items() if m["can_access"]], [])

    def test_a_ordem_poe_os_disponiveis_primeiro(self):
        """Quem entra precisa ver o que PODE usar antes do que ainda não
        existe."""
        lista = modulos_para(role="STUDENT",
                             contratados=("AGENTE_IA_EDU", "REDACAO_IA"),
                             is_platform_admin=False)
        status = [m["status"] for m in lista]
        self.assertEqual(status, sorted(status, key=lambda s: s != DISPONIVEL))

    def test_nenhum_modulo_inventa_funcionalidade(self):
        """Descrições dos módulos futuros não prometem dashboards, relatórios
        nem números que ninguém construiu."""
        proibido = ("dashboard", "relatório", "relatorios", "gráfico",
                    "grafico", "ranking", "nota média", "indicadores")
        for m in MODULOS:
            if m["status"] != EM_BREVE:
                continue
            texto = (m["description"] or "").lower()
            for palavra in proibido:
                with self.subTest(modulo=m["key"], palavra=palavra):
                    self.assertNotIn(palavra, texto)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
