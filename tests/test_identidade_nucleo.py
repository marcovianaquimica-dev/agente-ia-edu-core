"""UMA PLATAFORMA, VÁRIOS MÓDULOS - verificado no CSS e no HTML reais.

O QUE A AUDITORIA ENCONTROU
============================
Os tokens de cor e a fonte JÁ eram os mesmos nos dois vocabulários do projeto:

    portal/aluno (pt)          styles.css (en)
    --fundo     #f4f6fb        --bg-app      #f4f6fb
    --papel     #ffffff        --bg-card     #ffffff
    --tinta     #1e293b        --text-main   #1e293b
    --primaria  #4f46e5        --primary     #4f46e5

Ou seja: não faltava um design system. Faltava TERMINAR de propagar o que já
existia. O que divergia era outra coisa:

1. a MARCA - um emoji 🎓 e o nome antigo "AGENTE IA EDU" nas telas de
   professor e coordenação, enquanto o Portal usa o símbolo oficial e
   "Núcleo Edu 360";
2. o CINZA DE TEXTO - `--text-muted: #64748b` reprova em AA para texto
   normal, e o Portal já tinha corrigido isso para #5b6b82 com um comentário
   dizendo exatamente por quê. A correção não tinha chegado ao outro lado;
3. o RODAPÉ - não havia identificação de versão em lugar nenhum.

Estes testes cuidam dos três, lendo os arquivos que o navegador carrega.
"""

from __future__ import annotations

import pathlib
import re
import unittest

WEB = pathlib.Path(__file__).resolve().parent.parent / "src/agente_ia_edu/web"

# As paginas que uma pessoa de escola abre. `index.html` (app antigo do aluno)
# fica de fora: o aluno de verdade entra por `aluno.html`.
PAGINAS_DO_PRODUTO = ("portal.html", "aluno.html", "redacao.html",
                      "teacher.html", "coordination.html")


def texto(nome: str) -> str:
    return (WEB / nome).read_text(encoding="utf-8")


def _luminancia(hexa: str) -> float:
    hexa = hexa.lstrip("#")
    canais = [int(hexa[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    ajustados = [c / 12.92 if c <= 0.03928 else ((c + 0.055) / 1.055) ** 2.4
                 for c in canais]
    return 0.2126 * ajustados[0] + 0.7152 * ajustados[1] + 0.0722 * ajustados[2]


def contraste(frente: str, fundo: str) -> float:
    a, b = _luminancia(frente), _luminancia(fundo)
    claro, escuro = max(a, b), min(a, b)
    return (claro + 0.05) / (escuro + 0.05)


def token(css: str, nome: str, _restante: int = 4) -> str | None:
    """O valor do token, seguindo os `var()` ate chegar a uma cor.

    Os dois vocabularios do projeto apontam para a mesma fonte por alias
    (`--text-muted: var(--nucleo-tinta-fraca)`), e e justamente isso que se
    quer verificar: o teste precisa enxergar a cor que o navegador enxerga,
    nao a indirecao.
    """
    m = re.search(rf"{re.escape(nome)}\s*:\s*([^;]+);", css)
    if not m:
        return None
    valor = m.group(1).strip()
    if valor.startswith("#"):
        return valor[:7]
    alias = re.fullmatch(r"var\(\s*(--[\w-]+)\s*\)", valor)
    if alias and _restante > 0:
        return token(css, alias.group(1), _restante - 1)
    return None


class AMarcaEAOficial(unittest.TestCase):
    """A logo oficial existe em `assets/`. Nenhuma tela deve inventar outra."""

    def test_nenhuma_pagina_usa_emoji_como_logotipo(self):
        for pagina in PAGINAS_DO_PRODUTO:
            with self.subTest(pagina=pagina):
                html = texto(pagina)
                # O emoji dentro de um elemento de MARCA é o caso: emoji solto
                # em outro lugar da página é conteúdo, não identidade.
                for marca in re.findall(r'class="brand-logo"[^>]*>(.*?)<', html):
                    self.assertNotIn("🎓", marca,
                                     "a marca virou emoji nesta página")

    def test_o_nome_antigo_nao_aparece_mais_na_marca(self):
        for pagina in PAGINAS_DO_PRODUTO:
            with self.subTest(pagina=pagina):
                html = texto(pagina)
                for marca in re.findall(r'class="brand-text"[^>]*>(.*?)</div>',
                                        html, re.S):
                    self.assertNotIn("AGENTE IA", marca,
                                     "a lateral ainda chama o produto pelo "
                                     "nome anterior")

    def test_a_marca_do_produto_e_dita(self):
        for pagina in PAGINAS_DO_PRODUTO:
            with self.subTest(pagina=pagina):
                self.assertIn("Núcleo Edu 360", texto(pagina))


class OCinzaDeTextoPassaEmAA(unittest.TestCase):
    """O Portal já tinha corrigido; o outro lado ficou para trás."""

    def test_o_cinza_compartilhado_passa_sobre_o_papel_e_sobre_o_fundo(self):
        css = texto("nucleo.css")
        muted = token(css, "--text-muted")
        self.assertIsNotNone(muted, "o token compartilhado sumiu")
        for fundo, nome in (("#ffffff", "papel"), ("#f4f6fb", "fundo")):
            with self.subTest(sobre=nome):
                self.assertGreaterEqual(
                    round(contraste(muted, fundo), 2), 4.5,
                    f"{muted} sobre {fundo} reprova em AA para texto normal")

    def test_nenhuma_folha_POSTERIOR_redefine_os_tokens_compartilhados(self):
        """O arquivo certo nao basta: ganha quem vem depois.

        Medido no navegador DEPOIS de `nucleo.css` entrar: `--text-muted`
        continuava #64748b, porque `styles.css` - carregada em seguida -
        tinha a sua propria `:root` com o valor antigo. O teste que so lia
        `nucleo.css` passava, e a tela continuava reprovando em AA.
        """
        compartilhados = ("--font-family", "--bg-app", "--bg-card",
                          "--text-main", "--text-muted", "--primary",
                          "--primary-hover", "--primary-light",
                          "--border-color")
        for folha in ("styles.css", "teacher.css", "coordination.css",
                      "redacao.css", "portal.css", "aluno.css"):
            css = texto(folha)
            raiz = re.findall(r":root\s*{([^}]*)}", css)
            for bloco in raiz:
                for nome in compartilhados:
                    with self.subTest(folha=folha, token=nome):
                        self.assertNotRegex(
                            bloco, rf"{re.escape(nome)}\s*:\s*#",
                            f"{folha} redefine {nome} e sobrescreve nucleo.css")

    def test_os_dois_vocabularios_apontam_para_a_mesma_cor(self):
        """`--tinta-fraca` e `--text-muted` sao o MESMO cinza.

        Duas definicoes do mesmo conceito divergem no primeiro ajuste - foi
        assim que o Portal corrigiu o contraste e o professor nao.
        """
        css = texto("nucleo.css")
        self.assertEqual(token(css, "--text-muted"), token(css, "--tinta-fraca"))


class ORodapeDizAVersao(unittest.TestCase):
    def test_todas_as_paginas_do_produto_tem_rodape(self):
        for pagina in PAGINAS_DO_PRODUTO:
            with self.subTest(pagina=pagina):
                self.assertIn("nucleo-rodape", texto(pagina),
                              "pagina do produto sem rodape institucional")

    def test_o_rodape_tras_marca_e_versao(self):
        for pagina in PAGINAS_DO_PRODUTO:
            with self.subTest(pagina=pagina):
                html = texto(pagina)
                trecho = html[html.index("nucleo-rodape"):][:400]
                self.assertIn("2026", trecho)
                self.assertIn("Núcleo Edu 360", trecho)
                self.assertIn("Alfa 1.0", trecho)

    def test_nao_diz_desenvolvido_por(self):
        for pagina in PAGINAS_DO_PRODUTO:
            with self.subTest(pagina=pagina):
                self.assertNotIn("Desenvolvido por", texto(pagina))


class ACamadaCompartilhadaEhCarregada(unittest.TestCase):
    def test_toda_pagina_do_produto_carrega_nucleo_css(self):
        for pagina in PAGINAS_DO_PRODUTO:
            with self.subTest(pagina=pagina):
                self.assertIn("nucleo.css", texto(pagina))

    def test_ela_vem_ANTES_do_css_da_pagina(self):
        """Tokens primeiro; a pagina pode especializar, nunca o contrario."""
        for pagina, proprio in (("portal.html", "portal.css"),
                                ("aluno.html", "aluno.css"),
                                ("teacher.html", "teacher.css")):
            with self.subTest(pagina=pagina):
                html = texto(pagina)
                self.assertLess(html.index("nucleo.css"), html.index(proprio))


if __name__ == "__main__":
    unittest.main()
