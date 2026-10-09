"""A LATERAL DO PROFESSOR - o que ela promete, e se o destino existe.

A auditoria encontrou catorze itens numa lista plana, com emoji em cada um e
dois rótulos diferentes para o MESMO painel. Uma lista plana de catorze itens
não é a arquitetura mental de ninguém: é o inventário do que o sistema sabe
fazer.

E faltava a saída: nenhuma das telas de professor ou coordenação tinha link de
volta ao Portal. Quem entrava no módulo ficava dentro dele.

Estes testes verificam o HTML real - cada item aponta para um painel que
existe, nenhum painel é prometido por dois rótulos, e há volta ao ecossistema.
"""

from __future__ import annotations

import pathlib
import re
import unittest

WEB = pathlib.Path(__file__).resolve().parent.parent / "src/agente_ia_edu/web"


def html(nome: str) -> str:
    return (WEB / nome).read_text(encoding="utf-8")


def texto_css(nome: str) -> str:
    return (WEB / nome).read_text(encoding="utf-8")


def itens_da_lateral(pagina: str) -> list[dict]:
    """Cada item da nav: o destino (view ou href) e o rótulo visível."""
    marcacao = html(pagina)
    inicio = marcacao.index('class="nav-menu"')
    fim = marcacao.index("</nav>", inicio)
    bloco = marcacao[inicio:fim]
    itens = []
    for tag in re.findall(r"<(?:button|a)[^>]*>.*?</(?:button|a)>", bloco, re.S):
        view = re.search(r'data-view="([a-z-]+)"', tag)
        href = re.search(r'href="([^"]+)"', tag)
        rotulo = re.sub(r"<[^>]+>", " ", tag)
        itens.append({"view": view.group(1) if view else None,
                      "href": href.group(1) if href else None,
                      "rotulo": " ".join(rotulo.split())})
    return itens


class TodoItemTemDestinoReal(unittest.TestCase):
    def test_cada_view_da_lateral_existe_no_html(self):
        marcacao = html("teacher.html")
        # `performance` divide o painel com `contents` de propósito - está
        # escrito em teacher.js. Qualquer OUTRA view precisa do próprio.
        compartilham = {"performance": "contents"}
        for item in itens_da_lateral("teacher.html"):
            if not item["view"]:
                continue
            alvo = compartilham.get(item["view"], item["view"])
            with self.subTest(item=item["rotulo"]):
                self.assertIn(f'id="view-{alvo}"', marcacao,
                              "item da lateral sem painel de destino")

    def test_nenhum_item_sem_destino_nenhum(self):
        for item in itens_da_lateral("teacher.html"):
            with self.subTest(item=item["rotulo"]):
                self.assertTrue(item["view"] or item["href"],
                                "botão que não leva a lugar nenhum")


class NenhumPainelEProMETIDOPorDoisRotulos(unittest.TestCase):
    """"Conteúdos" e "Desempenho" levavam ao MESMO painel.

    Dois nomes para o mesmo lugar não dão ao usuário duas funções: dão a ele a
    dúvida de qual é a certa, e a descoberta de que são iguais.
    """

    def test_cada_destino_aparece_uma_vez_so(self):
        destinos = [i["view"] or i["href"] for i in itens_da_lateral("teacher.html")]
        repetidos = {d for d in destinos if destinos.count(d) > 1}
        self.assertFalse(repetidos, f"destinos repetidos na lateral: {repetidos}")


class ALateralEAGRUPADA(unittest.TestCase):
    """Catorze itens numa lista plana é um inventário, não uma navegação."""

    def test_ha_grupos_nomeados(self):
        marcacao = html("teacher.html")
        grupos = re.findall(r'class="nav-grupo"[^>]*>([^<]+)<', marcacao)
        self.assertGreaterEqual(len(grupos), 3,
                                "a lateral continua sendo uma lista plana")

    def test_os_grupos_tem_nome_de_gente(self):
        marcacao = html("teacher.html")
        grupos = [g.strip() for g in
                  re.findall(r'class="nav-grupo"[^>]*>([^<]+)<', marcacao)]
        for nome in grupos:
            with self.subTest(grupo=nome):
                self.assertTrue(nome)
                self.assertNotIn("_", nome, "nome de grupo com código interno")


class HaVoltaAoEcossistema(unittest.TestCase):
    """Quem entra num módulo precisa saber como volta ao Núcleo."""

    def test_professor_e_coordenacao_voltam_ao_portal(self):
        for pagina in ("teacher.html", "coordination.html"):
            with self.subTest(pagina=pagina):
                self.assertIn('href="/portal"', html(pagina),
                              "módulo sem saída de volta ao Portal")


class NadaDeEmojiNaNavegacao(unittest.TestCase):
    """Emoji em cada item é ruído, e num produto institucional lê como
    template genérico. Ícone decorativo some; o rótulo basta."""

    def test_os_itens_nao_carregam_emoji(self):
        proibidos = "🏠🏫📚👨‍🎓📝📋🗂️📖📥📊🎯📤👤🏛️"
        for item in itens_da_lateral("teacher.html"):
            with self.subTest(item=item["rotulo"]):
                for e in proibidos:
                    self.assertNotIn(e, item["rotulo"])


class ODrawerTemOContratoCompleto(unittest.TestCase):
    """O que o drawer PRECISA ter para ser usável — travado no HTML.

    O comportamento (abre, fecha, ESC, clique fora, body livre) foi validado
    no navegador a 390px; este teste guarda as peças de que ele depende, que
    são as que alguém poderia remover sem perceber.
    """

    def test_as_tres_pecas_existem(self):
        marcacao = html("teacher.html")
        for peca, oque in (
                ("teacher-mobile-menu-toggle", "botão que abre"),
                ("teacher-mobile-menu-close", "botão que fecha"),
                ("teacher-sidebar-backdrop", "fundo que fecha ao clicar")):
            with self.subTest(peca=oque):
                self.assertIn(peca, marcacao, f"drawer sem {oque}")

    def test_o_botao_declara_o_estado_para_quem_nao_ve(self):
        marcacao = html("teacher.html")
        trecho = marcacao[marcacao.index("teacher-mobile-menu-toggle") - 300:]
        trecho = trecho[:600]
        self.assertIn("aria-expanded", trecho,
                      "o botão não diz se o menu está aberto")
        self.assertIn("aria-label", trecho, "botão de ícone sem nome")

    def test_o_alvo_de_toque_nao_regride(self):
        """36x36 foi medido e corrigido. O token existe para não voltar."""
        css = texto_css("styles.css")
        inicio = css.index(".mobile-menu-toggle")
        bloco = css[inicio:inicio + 420]
        self.assertIn("--nucleo-toque", bloco,
                      "o alvo de toque voltou a ser um número solto")
        self.assertNotIn("36px", bloco)


if __name__ == "__main__":
    unittest.main()
