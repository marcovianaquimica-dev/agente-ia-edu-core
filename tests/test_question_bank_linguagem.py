"""A TELA DO BANCO DE QUESTÕES FALA COM O PROFESSOR, NÃO COM O SISTEMA.

Auditado em 2026-10-06, no HTML e no JavaScript reais:

    placeholder do filtro de Conteúdo      "código curriculum-v2"
    placeholder do filtro de Subconteúdo   "código curriculum-v2"
    identificador do usuário               "user:prof_mendes", aberto no topo
    dificuldade no filtro                  "Média"
    dificuldade no cartão                  "MEDIUM"

Os dois últimos são o mesmo enum escrito de dois jeitos em duas telas - a
divergência que uma fonte única existe para impedir.

`curriculum-v2` é o nome da VERSÃO DO ESQUEMA de taxonomia. Pedir a um
professor que digite "o código curriculum-v2" é pedir que ele conheça o
versionamento interno do produto.
"""

from __future__ import annotations

import pathlib
import re
import unittest

WEB = pathlib.Path(__file__).resolve().parent.parent / "src/agente_ia_edu/web"
HTML = (WEB / "question-bank.html").read_text(encoding="utf-8")
JS = (WEB / "question-bank.js").read_text(encoding="utf-8")


class NadaDeEsquemaInternoNaTela(unittest.TestCase):

    def test_nenhum_placeholder_cita_a_versao_do_esquema(self):
        placeholders = re.findall(r'placeholder="([^"]*)"', HTML)
        for p in placeholders:
            with self.subTest(placeholder=p):
                self.assertNotIn("curriculum-v2", p.lower())

    def test_os_filtros_de_conteudo_dizem_o_que_fazer(self):
        """Vazio também não serve: o campo precisa dizer o que aceita."""
        for campo in ("qb-filter-content", "qb-filter-subcontent"):
            with self.subTest(campo=campo):
                trecho = HTML[HTML.index(campo):][:300]
                achado = re.search(r'placeholder="([^"]*)"', trecho)
                self.assertTrue(achado, f"{campo} ficou sem placeholder")
                self.assertTrue(achado.group(1).strip())


class NemVersaoDEESQUEMANemEnumCruNoDetALHE(unittest.TestCase):
    """Medido no navegador: ao abrir uma questão o professor lia
    "Taxonomia: curriculum-v2" e, numa provisória, o enum entre parênteses."""

    def test_a_versao_do_esquema_nao_aparece_para_o_professor(self):
        self.assertNotIn("Taxonomia: ", JS,
                         "o professor continua lendo a versão do esquema")

    def test_o_aviso_de_provisoria_nao_imprime_o_enum(self):
        self.assertNotIn("${esc(q.classification_state)}", JS,
                         "o aviso continua mostrando NEEDS_REVIEW cru")

    def test_o_aviso_continua_existindo(self):
        """Esconder o enum não pode esconder o aviso: uma classificação
        provisória PRECISA ser sinalizada antes de virar prova."""
        self.assertIn("qb-provisional-banner", JS)
        self.assertIn("Revise antes de usar", JS)


class AIdentIDADEEDeGente(unittest.TestCase):
    """`user:prof_mendes` num cabeçalho diz "ferramenta interna"."""

    def test_o_identificador_tecnico_nao_fica_aberto_no_cabecalho(self):
        self.assertIn("dev-identidade", HTML,
                      "a entrada de identidade continua aberta na tela")
        inicio = HTML.index("qb-identity-id")
        antes = HTML[:inicio]
        self.assertIn("<details class=\"dev-identidade\">", antes,
                      "o campo de identidade não está dentro do bloco de "
                      "desenvolvimento")

    def test_a_entrada_de_desenvolvimento_continua_existindo(self):
        """Ela é o mecanismo real de troca de identidade neste ambiente:
        removê-la quebraria a tela. O que se corrige é ela estar exposta."""
        self.assertIn('id="qb-identity-id"', HTML)

    def test_ha_lugar_para_o_nome_da_pessoa(self):
        self.assertIn("contexto-humano", HTML)

    def test_o_nome_vem_da_rota_que_o_portal_ja_usa(self):
        """Sem endpoint novo e sem segunda fonte de verdade."""
        self.assertIn("/api/v1/portal/overview", JS)

    def test_o_nome_nao_e_escrito_a_mao_no_codigo(self):
        self.assertNotIn("Prof. Mendes", JS)
        self.assertNotIn("Prof. Mendes", HTML)


class UMAFonteSoParaADificuldade(unittest.TestCase):
    """O mesmo enum escrito de dois jeitos em duas telas é o problema."""

    def test_o_cartao_nao_imprime_o_enum_cru(self):
        self.assertNotIn("esc(q.recommended_difficulty)", JS,
                         "o cartão continua mostrando EASY/MEDIUM/HARD")

    def test_o_filtro_usa_o_mesmo_rotulo_do_backend(self):
        from agente_ia_edu.services.rotulos_da_taxonomia import DIFICULDADES

        for codigo, rotulo in DIFICULDADES.items():
            with self.subTest(codigo=codigo):
                achado = re.search(
                    rf'<option value="{codigo}">([^<]+)</option>', HTML)
                self.assertTrue(achado, f"o filtro perdeu a opção {codigo}")
                self.assertEqual(rotulo, achado.group(1).strip())

    def test_o_valor_enviado_continua_sendo_o_enum(self):
        """A tela mostra "Médio"; a requisição manda MEDIUM."""
        for codigo in ("EASY", "MEDIUM", "HARD"):
            with self.subTest(codigo=codigo):
                self.assertIn(f'<option value="{codigo}">', HTML)


class ONomeVemDoBackendENaoDeUmMapaNoJS(unittest.TestCase):
    """Um dicionário no JavaScript divergiria do catálogo no primeiro
    conteúdo novo - e seria uma segunda verdade sobre o currículo."""

    def test_nao_ha_traducao_escrita_a_mao_no_javascript(self):
        for codigo, nome in (("CHEMISTRY", "Química"), ("MATH", "Matemática"),
                             ("BIOLOGY", "Biologia"), ("PHYSICS", "Física")):
            with self.subTest(codigo=codigo):
                self.assertNotIn(f"'{codigo}': '{nome}'", JS)
                self.assertNotIn(f'"{codigo}": "{nome}"', JS)

    def test_o_rotulo_sai_do_que_o_backend_mandou(self):
        self.assertIn("state.labels", JS)
        self.assertIn("data.labels", JS)

    def test_codigo_sem_rotulo_volta_como_ele_mesmo(self):
        """Fallback honesto: nunca uma tela em branco onde havia um código."""
        self.assertRegex(JS, r"mapa\[codigo\]\s*\|\|\s*codigo")


if __name__ == "__main__":
    unittest.main()
