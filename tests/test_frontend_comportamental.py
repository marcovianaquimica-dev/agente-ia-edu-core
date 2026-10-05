"""A ponte que faz o teste comportamental de JavaScript ENTRAR na suíte.

O PROBLEMA QUE ISTO RESOLVE
============================
Este projeto tem 23 arquivos `.js` em `tests/`. Nenhum é executado por nada:
não há `package.json`, não há runner, e `pytest tests/` os ignora. Eles
varrem o fonte com expressões regulares — provam que uma string existe, não
que a tela funciona. E um teste que ninguém roda não é um teste.

Esta ponte roda `node --test` sobre os testes de COMPORTAMENTO (os que
carregam o módulo e chamam suas funções) e falha junto com eles. Assim
`pytest tests/` passa a cobrir também essa camada.

POR QUE SÓ OS DE COMPORTAMENTO
===============================
Os 23 antigos continuam fora, de propósito: fazê-los entrar agora
transformaria este bloco num mutirão de reescrita de testes de frontend. A
dívida está registrada; o caminho para pagá-la é acrescentar arquivos a
`COMPORTAMENTAIS` conforme forem convertidos.

SOBRE O SKIP
=============
Sem `node` instalado o teste pula, dizendo isso. É ausência de runtime, não
falha mascarada — e a mensagem deixa claro que a cobertura não aconteceu.
"""

from __future__ import annotations

import pathlib
import shutil
import subprocess
import unittest

RAIZ = pathlib.Path(__file__).resolve().parent

# Os que EXECUTAM o codigo. Os de varredura de fonte nao entram - ver docstring.
COMPORTAMENTAIS = ("test_aluno_guiada_frontend.js", "test_portal_ui_frontend.js")


class FrontendComportamentalTests(unittest.TestCase):

    def test_os_testes_de_comportamento_do_frontend_passam(self):
        node = shutil.which("node")
        if node is None:  # pragma: no cover - depende do ambiente
            self.skipTest("node nao esta instalado: os testes de comportamento "
                          "do frontend NAO foram executados nesta rodada")

        alvos = [str(RAIZ / nome) for nome in COMPORTAMENTAIS]
        for alvo in alvos:
            self.assertTrue(pathlib.Path(alvo).exists(), f"sumiu: {alvo}")

        r = subprocess.run([node, "--test", *alvos],
                           capture_output=True, text=True, timeout=120)
        self.assertEqual(
            r.returncode, 0,
            "os testes de comportamento do frontend falharam:\n"
            f"{r.stdout}\n{r.stderr}")

    def test_os_modulos_testados_sao_os_que_as_paginas_carregam(self):
        """Um módulo testado que a página não carrega testa nada.

        Esta é a única asserção deste arquivo que olha o fonte — e ela não
        mede comportamento, mede ligação: confirma que os arquivos
        exercitados pelo `node --test` são os que os HTMLs incluem.
        """
        web = RAIZ.parent / "src" / "agente_ia_edu" / "web"
        for pagina, modulo in (("aluno.html", "aluno-guiada.js"),
                               ("portal.html", "portal.js")):
            with self.subTest(pagina=pagina):
                html = (web / pagina).read_text(encoding="utf-8")
                self.assertIn(modulo, html,
                              "a pagina nao carrega o modulo que o teste exercita")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
