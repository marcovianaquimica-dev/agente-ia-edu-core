"""O navegador nao pode servir uma tela velha sem perguntar.

ENCONTRADO DUAS VEZES NO ENSAIO DA APRESENTACAO
================================================
Uma correcao de CSS e, depois, uma de JS foram dadas como "nao funcionou"
quando ja estavam no disco: o navegador reusou a copia em cache sem sequer
perguntar ao servidor se havia outra. O diagnostico custou tempo nas duas
vezes - e, numa demonstracao, custaria a propria demonstracao: a tela do
telao mostrando o build de ontem, com o bug que foi corrigido de manha.

A CAUSA
=======
`StaticFiles` manda `etag` e `last-modified`, mas nao manda `cache-control`.
Sem ele o navegador aplica cache HEURISTICO: escolhe sozinho por quanto tempo
reusar o arquivo, sem revalidar.

O QUE SE PEDE AQUI
==================
`no-cache` - que NAO quer dizer "nao guarde". Quer dizer "guarde, mas
pergunte antes de usar". A resposta continua sendo 304 quando nada mudou,
entao nao se troca cache por banda: troca-se silencio por uma pergunta.
"""

from __future__ import annotations

import unittest

from fastapi.testclient import TestClient

from agente_ia_edu.api.app import create_app

# Um por familia de mount: a pagina do aluno (html=True) e um asset de outro
# portal (html=False). Se o cabecalho viesse de um mount so, este segundo
# caso denunciaria.
CAMINHOS = ("/student/aluno.js", "/redacao/assets/essay-report.js")


class StaticRevalidaAntesDeReusar(unittest.TestCase):
    def setUp(self):
        self.client = TestClient(create_app())

    def test_arquivo_estatico_pede_revalidacao(self):
        for caminho in CAMINHOS:
            with self.subTest(caminho=caminho):
                r = self.client.get(caminho)
                self.assertEqual(r.status_code, 200)
                self.assertIn("no-cache", r.headers.get("cache-control", ""))

    def test_a_pagina_tambem_revalida(self):
        """De nada adianta o JS novo se o HTML velho nem o cita."""
        for caminho in ("/portal", "/redacao", "/student/aluno.html"):
            with self.subTest(caminho=caminho):
                r = self.client.get(caminho)
                self.assertEqual(r.status_code, 200)
                self.assertIn("no-cache", r.headers.get("cache-control", ""))

    def test_a_validacao_continua_barata(self):
        """Com etag preservado, revalidar custa um 304 - nao o arquivo inteiro."""
        primeira = self.client.get("/student/aluno.js")
        etag = primeira.headers.get("etag")
        self.assertIsNotNone(etag, "sem etag, revalidar passa a baixar tudo")
        segunda = self.client.get("/student/aluno.js",
                                  headers={"if-none-match": etag})
        self.assertEqual(segunda.status_code, 304)

    def test_a_api_nao_e_afetada(self):
        """O cabecalho e dos estaticos. Respostas de API nao mudam por isto."""
        r = self.client.get("/health")
        self.assertNotIn("no-cache", r.headers.get("cache-control", ""))


if __name__ == "__main__":
    unittest.main()
