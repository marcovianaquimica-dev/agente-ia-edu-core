"""Marcador de alternativa duplicado pelo extrator de texto.

Achado pela adjudicacao humana da amostra aleatoria (60 linhas): 24 das 26
reprovas tinham a mesma observacao - "alternativas contem duplicacao do
marcador/letra". Nos cadernos de 2022 e 2023, onde o portao escolhe `pypdf`,
a camada de texto emite a letra DUAS vezes:

    'A A desenho cru da realidade dramatica dos retirantes.'
    'B B indefinicao dos espacos para efeito de generalizacao.'

O marcador e consumido pela regex e a segunda letra fica dentro do texto.

NAO da para remover a letra repetida cegamente: em portugues 'A' e artigo e
'E' e conjuncao, entao 'A A casa e bonita' pode ser marcador + artigo.

O discriminador e ESTRUTURAL: a duplicacao e do renderizador, logo atinge
TODAS AS CINCO alternativas do item. Um artigo atinge uma ou duas. A regra so
remove quando as cinco repetem a propria letra.
"""

from __future__ import annotations

from agente_ia_edu.services.enem_extraction_v2 import options


def corpo(linhas: list[str]) -> str:
    return "\n".join(linhas)


class TestMarcadorDuplicado:
    def test_as_cinco_repetem_entao_remove(self):
        texto = corpo([
            "A A desenho cru da realidade dramatica dos retirantes.",
            "B B indefinicao dos espacos para efeito de generalizacao.",
            "C C analise psicologica da reacao dos personagens a seca.",
            "D D engajamento politico do narrador ante as desigualdades.",
            "E E contemplacao lirica da paisagem transformada.",
        ])
        opcoes, _ = options.extrair(texto)
        assert [o.letra for o in opcoes] == list("ABCDE")
        assert opcoes[0].texto == "desenho cru da realidade dramatica dos retirantes."
        assert opcoes[4].texto == "contemplacao lirica da paisagem transformada."

    def test_so_uma_repete_entao_preserva(self):
        """'A' e artigo. 'A A casa' e marcador + artigo, nao duplicacao."""
        texto = corpo([
            "A A casa dos retirantes estava vazia.",
            "B indefinicao dos espacos.",
            "C analise psicologica.",
            "D engajamento politico.",
            "E contemplacao lirica.",
        ])
        opcoes, _ = options.extrair(texto)
        assert opcoes[0].texto == "A casa dos retirantes estava vazia."

    def test_duas_repetem_entao_preserva(self):
        """Artigo em A e conjuncao em E ao mesmo tempo. Ainda e coincidencia."""
        texto = corpo([
            "A A casa estava vazia.",
            "B indefinicao dos espacos.",
            "C analise psicologica.",
            "D engajamento politico.",
            "E E tambem a paisagem mudou.",
        ])
        opcoes, _ = options.extrair(texto)
        assert opcoes[0].texto == "A casa estava vazia."
        assert opcoes[4].texto == "E tambem a paisagem mudou."

    def test_quatro_repetem_entao_preserva(self):
        """A regra exige as CINCO. Quatro nao basta."""
        texto = corpo([
            "A A primeira opcao.",
            "B B segunda opcao.",
            "C C terceira opcao.",
            "D D quarta opcao.",
            "E quinta opcao sem repeticao.",
        ])
        opcoes, _ = options.extrair(texto)
        assert opcoes[0].texto == "A primeira opcao."

    def test_repeticao_sem_separador_nao_conta(self):
        """'A Alguem' nao e duplicacao: 'Alguem' e uma palavra."""
        texto = corpo([
            "A Alguem cortara o mato.",
            "B Bastante forte.",
            "C Como se ali fosse.",
            "D Dessa maneira.",
            "E Era alegre.",
        ])
        opcoes, _ = options.extrair(texto)
        assert opcoes[0].texto == "Alguem cortara o mato."
        assert opcoes[1].texto == "Bastante forte."

    def test_funciona_com_tabulacao(self):
        texto = corpo([f"{le}\t{le}\ttexto da {le}" for le in "ABCDE"])
        opcoes, _ = options.extrair(texto)
        assert all(o.texto == f"texto da {o.letra}" for o in opcoes)

    def test_nao_remove_quando_a_letra_repetida_e_outra(self):
        """'A B texto' nao e duplicacao do marcador A."""
        texto = corpo([f"{le} X texto da {le}" for le in "ABCDE"])
        opcoes, _ = options.extrair(texto)
        assert opcoes[0].texto == "X texto da A"

    def test_alternativa_que_ficaria_vazia_nao_e_mutilada(self):
        """Se o texto for so a letra repetida, remover deixaria vazio."""
        texto = corpo([f"{le} {le}" for le in "ABCDE"])
        opcoes, _ = options.extrair(texto)
        # sem texto util, a corrida nem deveria existir
        assert opcoes == [] or all(o.texto for o in opcoes)
