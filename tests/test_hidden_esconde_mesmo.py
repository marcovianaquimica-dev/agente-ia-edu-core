"""`hidden` TEM QUE ESCONDER - inclusive onde outra regra deu `display`.

O atributo `hidden` só esconde pela folha do NAVEGADOR, e qualquer `display`
do autor vence essa folha. O repositório já tropeçou nisso três vezes e
corrigiu caso a caso (`.activity-player-dialog-backdrop[hidden]`,
`.activity-result-questions[hidden]`, `.ss-time-custom[hidden]`).

Tropeçou de novo nos filtros globais do cabeçalho: `switchView` marcava
`g.hidden = true` na view da Redação, o JS decidia certo - o teste em Node
disso passava - e os filtros continuavam na tela, porque `.filter-group` tem
`display: flex`. Teste verde, produto errado.

Este teste não procura texto em folha de estilo: ele RESOLVE a cascata para o
elemento real, na ordem em que a página carrega as folhas, e pergunta qual
`display` sobra. Remova a regra e ele fica vermelho.
"""

from __future__ import annotations

import pathlib
import re
import unittest

WEB = pathlib.Path(__file__).resolve().parent.parent / "src/agente_ia_edu/web"


def _folhas_da_pagina(pagina: str) -> list[str]:
    """As folhas que a página carrega, na ordem em que ela as carrega."""
    html = (WEB / pagina).read_text(encoding="utf-8")
    nomes = re.findall(r'<link rel="stylesheet" href="([^":]+\.css)"', html)
    return [(WEB / n).read_text(encoding="utf-8") for n in nomes]


def _regras(css: str):
    """(seletor, declarações) de cada regra simples, fora de @media."""
    css = re.sub(r"/\*.*?\*/", "", css, flags=re.S)
    for bloco in re.finditer(r"([^{}@]+)\{([^{}]*)\}", css):
        for seletor in bloco.group(1).split(","):
            seletor = seletor.strip()
            if seletor:
                yield seletor, bloco.group(2)


def _especificidade(seletor: str) -> tuple[int, int, int]:
    ids = len(re.findall(r"#[\w-]+", seletor))
    classes = (len(re.findall(r"\.[\w-]+", seletor))
               + len(re.findall(r"\[[^\]]+\]", seletor))
               + len(re.findall(r":(?!:)[\w-]+", seletor)))
    tags = len(re.findall(r"(?:^|[\s>+~])([a-z][\w-]*)", seletor))
    return ids, classes, tags


def _casa(seletor: str, *, tag: str, classes: set[str], atributos: set[str]) -> bool:
    """O seletor (simples, sem combinadores) casa com este elemento?"""
    resto = seletor.strip()
    if re.search(r"[\s>+~]", resto):
        return False
    if re.search(r"[:(]", resto):
        return False
    for c in re.findall(r"\.([\w-]+)", resto):
        if c not in classes:
            return False
    for a in re.findall(r"\[([\w-]+)[^\]]*\]", resto):
        if a not in atributos:
            return False
    nome = re.match(r"^([a-z][\w-]*)", resto)
    if nome and nome.group(1) != tag:
        return False
    corpo = re.sub(r"(\.[\w-]+|\[[^\]]+\]|^[a-z][\w-]*|\*)", "", resto)
    return corpo.strip() == ""


def display_resolvido(pagina: str, *, tag: str, classes: set[str],
                      atributos: set[str]) -> str:
    """O `display` que sobra depois da cascata, para este elemento."""
    melhor = (False, (-1, -1, -1), -1)
    valor = "block"
    for ordem, folha in enumerate(_folhas_da_pagina(pagina)):
        for seletor, decls in _regras(folha):
            if not _casa(seletor, tag=tag, classes=classes, atributos=atributos):
                continue
            m = re.search(r"(?:^|;)\s*display\s*:\s*([^;!]+?)\s*(!important)?\s*(?:;|$)",
                          decls)
            if not m:
                continue
            peso = (bool(m.group(2)), _especificidade(seletor), ordem)
            if peso >= melhor:
                melhor = peso
                valor = m.group(1).strip()
    return valor


class HiddenEscondeDeVerdade(unittest.TestCase):
    def test_filtro_global_marcado_hidden_some_da_tela(self):
        """O caso medido: `switchView` marca `hidden` e o filtro ficava lá."""
        for pagina in ("teacher.html", "coordination.html"):
            with self.subTest(pagina=pagina):
                self.assertEqual(
                    "none",
                    display_resolvido(
                        pagina, tag="div",
                        classes={"filter-group", "filter-group-global"},
                        atributos={"hidden"}),
                    "o filtro global marcado `hidden` continua ocupando a tela",
                )

    def test_o_mesmo_elemento_sem_hidden_continua_aparecendo(self):
        """A regra não pode esconder o filtro nas views onde ele vale."""
        self.assertNotEqual(
            "none",
            display_resolvido("teacher.html", tag="div",
                              classes={"filter-group", "filter-group-global"},
                              atributos=set()),
            "o filtro sumiu também onde ele funciona",
        )


if __name__ == "__main__":
    unittest.main()
