"""Testes da EXTRACAO ENEM V2 (prototipo experimental).

Sao sinteticos de proposito: ``var/inep-pilot/`` esta no ``.gitignore``, entao
um teste que dependa dos PDFs nao roda em outra maquina. As formas usadas aqui
foram COPIADAS do levantamento sobre os 12 cadernos reais, em fragmentos
minimos - a distribuicao que as justifica esta em
``docs/qbank-diagnostico-enem-2020-2025.md``.

A metade adversarial e a que importa: aumentar o numero de alternativas
extraidas e facil, e errado se o texto capturado nao for alternativa.
"""

from __future__ import annotations

import pytest

from agente_ia_edu.services.enem_extraction_v2 import options
from agente_ia_edu.services.enem_extraction_v2.contracts import Caixa
from agente_ia_edu.services.enem_extraction_v2.segmentation import segmentar
from agente_ia_edu.services.enem_extraction_v2.text_layer import (
    PADRAO_CABECALHO,
    SEPARADOR_PAGINA,
    CamadaDeTexto,
    normalizar,
)


def camada(paginas: list[str]) -> CamadaDeTexto:
    paginas = [normalizar(p) for p in paginas]
    documento = SEPARADOR_PAGINA.join(paginas)
    deslocamentos, posicao = [], 0
    for texto in paginas:
        deslocamentos.append(posicao)
        posicao += len(texto) + len(SEPARADOR_PAGINA)
    return CamadaDeTexto("teste", tuple(paginas), documento, tuple(deslocamentos))


def corpo_com(linhas: list[str]) -> str:
    return "\n".join(linhas)


# ---------------------------------------------------------------- C1
class TestC1QuebraDePagina:
    """A V1 une paginas com "\\f"; ``^`` nao casa depois de ``\\f``."""

    def test_cabecalho_no_topo_da_pagina_e_encontrado(self):
        # ultima linha da pagina 1 e o slug do InDesign, SEM \n final -
        # exatamente o que 2024 D1/D2 produzem
        c = camada(["texto\n5AZ.indb   2 23/08/2024   16:46:43",
                    "QUESTÃO 04\ncorpo do item"])
        assert [int(m.group(1)) for m in PADRAO_CABECALHO.finditer(c.documento)] == [4]

    def test_a_juncao_da_v1_perderia_esse_cabecalho(self):
        """Caracteriza o defeito, para que ninguem o reintroduza."""
        documento_v1 = "\f".join(["texto\n5AZ.indb   2 23/08/2024   16:46:43",
                                  "QUESTÃO 04\ncorpo"])
        assert PADRAO_CABECALHO.findall(documento_v1) == []

    def test_separador_da_v2_e_quebra_mais_form_feed(self):
        assert SEPARADOR_PAGINA == "\n\f"

    def test_pagina_de_cada_cabecalho(self):
        c = camada(["QUESTÃO 01\na", "QUESTÃO 02\nb", "QUESTÃO 03\nc"])
        assert [s.pagina_inicio for s in segmentar(c)] == [1, 2, 3]


# ---------------------------------------------------------------- C2
class TestC2RotuloEstilhacado:
    """Em 2025 o pypdf entrega 'QU EST ãO 24'. A V2 NAO afrouxa a regex."""

    def test_rotulo_estilhacado_nao_e_aceito(self):
        c = camada(["QU EST ãO 24  \ncorpo"])
        assert PADRAO_CABECALHO.findall(c.documento) == []

    def test_rotulo_integro_e_aceito_em_qualquer_caixa(self):
        for rotulo in ("QUESTÃO 24", "Questão 24", "questão 24", "QUESTAO 24"):
            c = camada([f"{rotulo}\ncorpo"])
            assert PADRAO_CABECALHO.findall(c.documento) == ["24"], rotulo

    def test_numero_de_quatro_digitos_nao_vira_item(self):
        c = camada(["Questão 1234\ncorpo"])
        # casa os tres primeiros digitos? nao: \b impede
        assert PADRAO_CABECALHO.findall(c.documento) == []


# ---------------------------------------------------------------- C3
class TestC3FormasDeAlternativa:
    """Todas as formas abaixo foram medidas nos 12 cadernos."""

    @pytest.mark.parametrize("separador,forma", [
        (" ", "1ESPACO"),
        ("  ", "2+ESPACOS"),
        ("\t", "TAB"),
        ("\t ", "TAB+ESPACO"),
    ])
    def test_forma_real_e_reconhecida(self, separador, forma):
        corpo = corpo_com([f"{letra}{separador}texto da {letra}"
                           for letra in "ABCDE"])
        opcoes, predominante = options.extrair(corpo)
        assert [o.letra for o in opcoes] == list("ABCDE")
        assert predominante == forma
        assert opcoes[0].texto == "texto da A"

    def test_letra_sozinha_com_texto_na_linha_seguinte(self):
        """2024 D1 no pypdf: 437 linhas com a letra sozinha."""
        corpo = corpo_com(["A", "texto da A", "B", "texto da B", "C",
                           "texto da C", "D", "texto da D", "E", "texto da E"])
        opcoes, _ = options.extrair(corpo)
        assert [o.letra for o in opcoes] == list("ABCDE")
        assert opcoes[2].texto == "texto da C"

    def test_marcador_vazio_duplicado_e_descartado(self):
        """2022/2023 no PyMuPDF: 'A\\t' vazio seguido de 'A\\t texto'."""
        linhas = []
        for letra in "ABCDE":
            linhas += [f"{letra}\t", f"{letra}\t texto da {letra}"]
        opcoes, _ = options.extrair(corpo_com(linhas))
        assert [o.letra for o in opcoes] == list("ABCDE")
        assert all(o.texto.startswith("texto da") for o in opcoes)

    def test_espaco_inquebravel_vira_espaco(self):
        """2023 D1: 'os\\xa0lixões' dentro do texto da alternativa."""
        corpo = normalizar(corpo_com([f"{le}\t os\xa0itens de {le}" for le in "ABCDE"]))
        opcoes, _ = options.extrair(corpo)
        assert opcoes[0].texto == "os itens de A"

    def test_sem_a_corrida_completa_nao_devolve_nada(self):
        """Fidelidade antes de cobertura: tres alternativas nao sao um item."""
        corpo = corpo_com(["A texto", "B texto", "C texto"])
        assert options.extrair(corpo) == ([], "")

    def test_fora_de_ordem_nao_e_aceito(self):
        corpo = corpo_com(["A t", "C t", "B t", "D t", "E t"])
        assert options.extrair(corpo)[0] == []

    def test_toma_a_ultima_corrida(self):
        corpo = corpo_com(["A primeiro", "B primeiro", "C primeiro",
                           "D primeiro", "E primeiro", "meio",
                           "A ultimo", "B ultimo", "C ultimo",
                           "D ultimo", "E ultimo"])
        opcoes, _ = options.extrair(corpo)
        assert opcoes[0].texto == "ultimo"


# ---------------------------------------------------------------- adversarial
class TestC3Adversarial:
    """Nao confundir letra do corpo do texto com alternativa."""

    def test_prosa_iniciada_por_letra_nao_vira_alternativa(self):
        """Caso real, 2023 D1: 'A part of the main.'"""
        corpo = corpo_com(["No man is an island.", "A part of the main.",
                           "Continuacao do enunciado."])
        assert options.extrair(corpo)[0] == []

    def test_prosa_antes_das_alternativas_nao_desloca_a_corrida(self):
        corpo = corpo_com(["A part of the main.", "mais enunciado"]
                          + [f"{le}\t alternativa {le}" for le in "ABCDE"])
        opcoes, _ = options.extrair(corpo)
        assert opcoes[0].texto == "alternativa A"

    def test_prosa_depois_das_alternativas_nao_entra_na_corrida(self):
        corpo = corpo_com([f"{le}\t alternativa {le}" for le in "ABCDE"]
                          + ["E assim termina o item."])
        opcoes, _ = options.extrair(corpo)
        assert opcoes[-1].texto == "alternativa E"

    def test_letra_colada_na_palavra_nunca_e_marcador(self):
        """'Alguém', 'Bastante', 'Como', 'Dessa', 'Era' - inicio de frase."""
        corpo = corpo_com(["Alguém cortara o mato", "Bastante forte",
                           "Como se ali fosse", "Dessa maneira", "Era alegre"])
        assert options.extrair(corpo)[0] == []

    def test_letra_colada_nao_rouba_lugar_da_alternativa_verdadeira(self):
        corpo = corpo_com(["Alguém cortara o mato"]
                          + [f"{le} alternativa {le}" for le in "ABCDE"])
        opcoes, _ = options.extrair(corpo)
        assert len(opcoes) == 5
        assert opcoes[0].texto == "alternativa A"

    def test_alternativa_sem_texto_invalida_a_corrida(self):
        linhas = [f"{le}\t texto {le}" for le in "ABCD"] + ["E\t"]
        assert options.extrair(corpo_com(linhas))[0] == []

    def test_letra_isolada_do_alfabeto_em_lista_nao_forma_corrida(self):
        """Uma lista 'a) b) c)' minuscula nao e alternativa de ENEM."""
        corpo = corpo_com([f"{le}) item" for le in "abcde"])
        assert options.extrair(corpo)[0] == []

    def test_seis_marcadores_a_e_nao_viram_seis_alternativas(self):
        corpo = corpo_com([f"{le} t" for le in "ABCDE"] + ["A extra"])
        opcoes, _ = options.extrair(corpo)
        assert len(opcoes) == 5


# ---------------------------------------------------------------- C4
class TestC4Gabarito:
    def test_um_digito_e_lido(self):
        from agente_ia_edu.services.enem_extraction_v2.answer_key import PADRAO_SIMPLES
        assert PADRAO_SIMPLES.findall("1 A") == [("1", "A")]
        assert PADRAO_SIMPLES.findall("01 A") == [("01", "A")]
        assert PADRAO_SIMPLES.findall("180 E") == [("180", "E")]

    def test_a_regex_da_v1_perderia_o_um_digito(self):
        import re
        v1 = re.compile(r"(?m)^\s*(\d{2,3})\s+([A-E])\s*$")
        assert v1.findall("1 A") == []

    def test_duas_respostas_na_linha_nao_viram_uma(self):
        from agente_ia_edu.services.enem_extraction_v2.answer_key import (
            PADRAO_DUPLO,
            PADRAO_SIMPLES,
        )
        assert PADRAO_SIMPLES.findall("1 D B") == []
        assert PADRAO_DUPLO.findall("1 D B") == [("1", "D", "B")]

    def test_cabecalho_de_lingua_e_reconhecido(self):
        from agente_ia_edu.services.enem_extraction_v2.answer_key import (
            PADRAO_CABECALHO_LINGUA,
        )
        achado = PADRAO_CABECALHO_LINGUA.search("INGLÊS ESPANHOL")
        assert achado and achado.group(1).upper().startswith("INGL")


# ---------------------------------------------------------------- C5
class TestC5Geometria:
    def test_area_e_intersecao(self):
        a = Caixa(0, 0, 10, 10)
        b = Caixa(5, 5, 15, 15)
        assert a.area == 100
        assert a.area_intersecao(b) == 25
        assert a.intersecta(b)
        assert not a.intersecta(Caixa(20, 20, 30, 30))

    def test_uniao(self):
        u = Caixa(0, 0, 10, 10).uniao(Caixa(20, 5, 30, 25))
        assert (u.x0, u.y0, u.x1, u.y1) == (0, 0, 30, 25)

    def test_componentes_conexos_separam_dois_aglomerados(self):
        from agente_ia_edu.services.enem_extraction_v2.assets import _componentes
        celulas = {(0, 0), (0, 1), (1, 0), (10, 10), (10, 11)}
        grupos = sorted(_componentes(celulas), key=len, reverse=True)
        assert [len(g) for g in grupos] == [3, 2]

    def test_regiao_do_item_usa_o_centro_do_ativo(self):
        from agente_ia_edu.services.enem_extraction_v2.assets import RegiaoDoItem
        regiao = RegiaoDoItem(7, 1, (Caixa(0, 100, 280, 400),))
        assert regiao.contem_centro(Caixa(50, 150, 150, 250))
        assert not regiao.contem_centro(Caixa(300, 150, 400, 250))


# ---------------------------------------------------------------- C6
class TestC6NaoResolvido:
    """A V2 nao inventa solucao para o bloco de lingua: ela o declara."""

    def test_numero_repetido_e_marcado_como_ocorrencia_2(self):
        c = camada(["Questão 1\nA t\nB t\nC t\nD t\nE t",
                    "Questão 1\nA u\nB u\nC u\nD u\nE u"])
        segmentos = segmentar(c)
        assert [s.ocorrencia for s in segmentos] == [1, 2]

    def test_segmentacao_nao_descarta_enunciado_vazio(self):
        """A V1 faz ``if not statement: continue`` e some com o item."""
        c = camada(["Questão 7\n"])
        assert len(segmentar(c)) == 1


# ---------------------------------------------------------------- C7 / fidelidade
class TestTravaDeLegibilidade:
    """Cinco marcadores sobre lixo de codificacao nao sao cinco alternativas.

    Sem esta trava a V2 declarava COMPLETO o item 20 de 2021 D1, cuja
    alternativa B era ``'/g3 /g70/g85/g76/g68/g85/g3'``. A trava derruba a
    contagem de proposito: e disso que se trata preferir 800 corretas a 1.050
    com erro silencioso.
    """

    def test_codigo_de_glifo_cru_e_ilegivel(self):
        from agente_ia_edu.services.enem_extraction_v2.text_layer import texto_ilegivel
        assert texto_ilegivel("/g3 /g70/g85/g76/g68/g85/g3 /g81/g82/g89")
        assert texto_ilegivel("adotar uma perspectiva /g106/g3/g87/g85/")

    def test_texto_portugues_normal_e_legivel(self):
        from agente_ia_edu.services.enem_extraction_v2.text_layer import texto_ilegivel
        assert not texto_ilegivel("revelar a origem religiosa da linguagem.")
        assert not texto_ilegivel("preserva a ancestralidade africana.")

    def test_trecho_curto_nao_e_julgado(self):
        """Uma alternativa legitima pode ser '12 m/s'. Nao e lixo."""
        from agente_ia_edu.services.enem_extraction_v2.text_layer import texto_ilegivel
        assert not texto_ilegivel("12 m/s")
        assert not texto_ilegivel("2,5")

    def test_o_piso_e_o_mesmo_ja_registrado_na_fase_10_6(self):
        from agente_ia_edu.services.enem_extraction_v2.text_layer import (
            PISO_DE_LEGIBILIDADE,
        )
        assert PISO_DE_LEGIBILIDADE == 0.65

    def test_item_ilegivel_nao_e_declarado_completo(self):
        from agente_ia_edu.services.enem_extraction_v2.contracts import (
            TEXTO_ILEGIVEL,
            OptionCandidate,
            QuestionCandidate,
        )
        from agente_ia_edu.services.enem_extraction_v2.text_layer import texto_ilegivel
        opcoes = [OptionCandidate(le, "/g3 /g70/g85/g76/g68/g85/g3 /g81", i, "TAB")
                  for i, le in enumerate("ABCDE")]
        q = QuestionCandidate(numero=20, enunciado="texto legivel do enunciado",
                              opcoes=opcoes, gabarito="D")
        assert q.tem_cinco_opcoes          # estruturalmente, sim
        assert any(texto_ilegivel(o.texto) for o in q.opcoes)
        q.problemas.append(TEXTO_ILEGIVEL)
        assert not q.completo              # e mesmo assim, nao esta completo


class TestMobiliaDePagina:
    """A ultima alternativa absorve tudo que vier depois dela.

    Medido em 2024 D1: alternativa D com 71 caracteres, alternativa E do mesmo
    item com 1.631 - os 1.560 restantes eram codigo de barras, marca d'agua e
    cabecalho corrido. A remocao e por frequencia e por repeticao, sem citar
    "ENEM", para valer em qualquer prova.
    """

    def test_marca_dagua_colada_e_removida(self):
        from agente_ia_edu.services.enem_extraction_v2.text_layer import (
            _remover_marca_dagua,
        )
        texto, n = _remover_marca_dagua("informar os interessados. " + "ENEM2024" * 40)
        assert n == 1
        assert "ENEM2024ENEM2024" not in texto
        assert texto.startswith("informar os interessados.")

    def test_marca_dagua_com_espaco_e_removida(self):
        from agente_ia_edu.services.enem_extraction_v2.text_layer import (
            _remover_marca_dagua,
        )
        texto, n = _remover_marca_dagua("rejeicao ao novo tipo. " + "ENEM 2022 " * 22)
        assert n == 1
        assert "ENEM 2022 ENEM 2022" not in texto

    @pytest.mark.parametrize("legitimo", [
        "A taxa foi de 10 10 10 por cento",
        "as palavras nao nao nao se repetem assim",
        "os valores 1 1 1 1 1 1 da tabela",
    ])
    def test_repeticao_legitima_sobrevive(self, legitimo):
        from agente_ia_edu.services.enem_extraction_v2.text_layer import (
            _remover_marca_dagua,
        )
        assert _remover_marca_dagua(legitimo) == (legitimo, 0)

    def test_limitacao_conhecida_cinco_repeticoes_de_palavra_longa(self):
        """Caracteriza um falso positivo aceito, para que ele nao surpreenda.

        Cinco repeticoes consecutivas da mesma palavra de 4+ letras - possivel
        num poema - seriam removidas como marca d'agua. Nao foi observado nos
        12 cadernos; fica registrado porque pode acontecer.
        """
        from agente_ia_edu.services.enem_extraction_v2.text_layer import (
            _remover_marca_dagua,
        )
        texto, n = _remover_marca_dagua("bate bate bate bate bate o coracao")
        assert n == 1 and "bate" not in texto

    def test_codigo_de_barras_e_removido(self):
        from agente_ia_edu.services.enem_extraction_v2.text_layer import (
            PADRAO_CODIGO_DE_BARRAS,
        )
        assert PADRAO_CODIGO_DE_BARRAS.sub("", "texto *010175AZ2* fim") == "texto  fim"

    def test_linha_repetida_na_maioria_das_paginas_e_mobilia(self):
        from agente_ia_edu.services.enem_extraction_v2.text_layer import limpar_mobilia
        cabecalho = "PROVA OFICIAL - CADERNO UNICO - COR AZUL"   # 40 caracteres
        corpos = ["alvorada", "bergamota", "cafezinho", "dendezeiro", "eucalipto"]
        paginas = [f"{cabecalho}\n{corpos[i % 5]} unico {i*13}" for i in range(10)]
        limpas, relatorio = limpar_mobilia(paginas)
        assert relatorio["linhas_removidas"] == 10
        assert all(cabecalho not in p for p in limpas)
        assert all(corpos[i % 5] in limpas[i] for i in range(10))

    def test_linha_curta_repetida_e_PRESERVADA_de_proposito(self):
        """Mudanca deliberada no endurecimento, nao regressao.

        O piso de 25 caracteres no nucleo existe para proteger 'Questao 18'
        e os marcadores 'A'..'E', que repetem dezenas de vezes por caderno.
        O preco e que um cabecalho CURTO sobrevive. Medido nos 12 cadernos:
        os unicos nucleos de mobilia reais tem 39 e 73 caracteres, entao o
        preco nao foi cobrado em nenhum caderno real.
        """
        from agente_ia_edu.services.enem_extraction_v2.text_layer import limpar_mobilia
        paginas = [f"CABECALHO CURTO\ncorpo {i}" for i in range(10)]
        limpas, relatorio = limpar_mobilia(paginas)
        assert relatorio["linhas_removidas"] == 0
        assert all("CABECALHO CURTO" in p for p in limpas)

    def test_linha_que_aparece_em_poucas_paginas_nao_e_mobilia(self):
        from agente_ia_edu.services.enem_extraction_v2.text_layer import limpar_mobilia
        paginas = ["frase rara"] + [f"outra coisa {i}" for i in range(10)]
        limpas, _ = limpar_mobilia(paginas)
        assert "frase rara" in limpas[0]


# ============================================================ ENDURECIMENTO
# Os 10 erros silenciosos adjudicados viraram regressao permanente.
# As fixtures NAO carregam literal de prova alem do minimo diagnostico:
# o que esta aqui sao as FORMAS da mobilia, nao o conteudo dos itens.

class TestMobiliaResidual:
    """Os 7 casos em que a V2 importaria o item com texto estranho.

    Forense (ver docs/qbank-extracao-v2-endurecimento.md):
      modo 1 - marca d'agua com glifo corrompido no meio, que quebra a
               repeticao EXATA exigida pelo padrao antigo:
               'ENEM2024ENEM20E 4ENEM2024ENEM2024'
      modo 2 - cabecalho corrido com o numero da pagina no fim, que torna
               cada ocorrencia unica e escapa da contagem por frequencia:
               '... CADERNO 1 | AZUL 30'
      modo 3 - material entre itens (coletanea compartilhada) absorvido pela
               ultima alternativa, que nao tem marcador depois dela.
    """

    def test_marca_dagua_com_glifo_corrompido_e_removida(self):
        """modo 1: o padrao antigo exigia repeticao exata e falhava aqui."""
        from agente_ia_edu.services.enem_extraction_v2.text_layer import (
            tokens_de_fundo,
        )
        paginas = [f"conteudo {i}\nENEM2024ENEM20E 4ENEM2024ENEM2024" for i in range(32)]
        fundo = tokens_de_fundo(paginas)
        assert any("ENEM2024ENEM2024" in t for t in fundo)

    def test_marca_dagua_separada_por_espaco_e_removida(self):
        """modo 1 em 2022: 'ENEM 2022' repetido 63 vezes por pagina."""
        from agente_ia_edu.services.enem_extraction_v2.text_layer import (
            tokens_de_fundo,
        )
        paginas = [f"conteudo {i} " + "ENEM 2022 " * 63 for i in range(32)]
        fundo = tokens_de_fundo(paginas)
        assert "ENEM" in fundo and "2022" in fundo

    def test_palavra_comum_nunca_vira_token_de_fundo(self):
        """'para' mede mediana 4 por pagina nos 12 cadernos. Nao e fundo."""
        from agente_ia_edu.services.enem_extraction_v2.text_layer import (
            tokens_de_fundo,
        )
        paginas = [("para " * 4) + f"texto da pagina {i}" for i in range(32)]
        assert tokens_de_fundo(paginas) == set()

    def test_cabecalho_com_numero_de_pagina_e_removido(self):
        """modo 2: o numero no fim tornava cada linha unica."""
        from agente_ia_edu.services.enem_extraction_v2.text_layer import (
            linhas_de_mobilia,
        )
        corpos = ["alvorada", "bergamota", "cafezinho", "dendezeiro", "eucalipto"]
        paginas = [f"CIENCIAS HUMANAS E SUAS TECNOLOGIAS | 1o DIA | CADERNO 1 | AZUL {i}\n"
                   f"{corpos[i % len(corpos)]} {i} palavra unica {i*7}"
                   for i in range(1, 33)]
        mobilia = linhas_de_mobilia(paginas)
        assert len(mobilia) == 32, sorted(mobilia)[:3]
        assert all("CIENCIAS HUMANAS" in m for m in mobilia)

    def test_cabecalho_de_questao_jamais_e_tratado_como_mobilia(self):
        """GUARDA CRITICA. Normalizar digitos faz 'Questao 18' virar
        'Questao', que se repete 95 vezes. Sem piso de tamanho de nucleo a
        regra apagaria TODOS os cabecalhos de item."""
        from agente_ia_edu.services.enem_extraction_v2.text_layer import (
            linhas_de_mobilia,
        )
        paginas = [f"QUESTÃO {i}\ncorpo do item {i}" for i in range(1, 91)]
        assert linhas_de_mobilia(paginas) == set()

    def test_marcador_de_alternativa_jamais_e_tratado_como_mobilia(self):
        """GUARDA CRITICA. 'A', 'B', 'D' repetem 29 a 53 vezes por caderno."""
        from agente_ia_edu.services.enem_extraction_v2.text_layer import (
            linhas_de_mobilia,
        )
        paginas = ["\n".join(f"{le}\ttexto {le} da pagina {i}" for le in "ABCDE")
                   for i in range(32)]
        mobilia = linhas_de_mobilia(paginas)
        assert not any(len(m.strip()) <= 3 for m in mobilia), mobilia

    def test_linha_longa_e_rara_sobrevive(self):
        from agente_ia_edu.services.enem_extraction_v2.text_layer import (
            linhas_de_mobilia,
        )
        paginas = ["Uma citacao literaria longa que aparece uma unica vez no caderno"] \
            + [f"outra pagina {i}" for i in range(31)]
        assert linhas_de_mobilia(paginas) == set()


class TestAdversarialEndurecimento:
    """As seis armadilhas que o usuario nomeou."""

    def test_nao_corta_o_final_legitimo_da_alternativa_e(self):
        from agente_ia_edu.services.enem_extraction_v2 import options
        from agente_ia_edu.services.enem_extraction_v2.text_layer import limpar_mobilia
        corpo = "\n".join([f"{le}\ttexto curto da {le}" for le in "ABCD"]
                          + ["E\tuma alternativa legitimamente mais longa que as "
                             "outras, com oracao subordinada e um fecho que precisa "
                             "sobreviver inteiro ate o ponto final."])
        paginas, _ = limpar_mobilia([corpo] * 3)
        opcoes, _ = options.extrair(paginas[0])
        assert len(opcoes) == 5
        assert opcoes[4].texto.endswith("ate o ponto final.")

    def test_nao_remove_texto_valido_repetido_entre_paginas(self):
        """Uma frase curta pode reaparecer sem ser mobilia."""
        from agente_ia_edu.services.enem_extraction_v2.text_layer import limpar_mobilia
        paginas = ["O texto para a questao seguinte\nconteudo A",
                   "outra coisa", "mais outra"]
        limpas, _ = limpar_mobilia(paginas)
        assert "O texto para a questao seguinte" in limpas[0]

    def test_questao_sem_imagem_continua_funcionando(self):
        from agente_ia_edu.services.enem_extraction_v2 import options
        corpo = "\n".join([f"{le}\ttexto {le}" for le in "ABCDE"])
        assert len(options.extrair(corpo)[0]) == 5

    def test_logotipo_no_topo_nao_vira_ativo_pedagogico(self):
        """Ativo cujo centro cai fora de qualquer regiao de item fica orfao."""
        from agente_ia_edu.services.enem_extraction_v2.assets import (
            RegiaoDoItem,
            associar,
        )
        from agente_ia_edu.services.enem_extraction_v2.contracts import (
            ASSOC_NENHUMA,
            ATIVO_RASTER,
            AssetCandidate,
            Caixa,
        )
        logotipo = AssetCandidate(pagina=1, caixa=Caixa(20, 5, 120, 40),
                                  tipo=ATIVO_RASTER, metodo_deteccao="teste")
        regioes = [RegiaoDoItem(7, 1, (Caixa(0, 100, 280, 400),)),
                   RegiaoDoItem(8, 1, (Caixa(283, 100, 560, 400),))]
        assert associar([logotipo], regioes)[0].metodo_associacao == ASSOC_NENHUMA

    def test_nao_associa_imagem_da_questao_vizinha(self):
        from agente_ia_edu.services.enem_extraction_v2.assets import (
            RegiaoDoItem,
            associar,
        )
        from agente_ia_edu.services.enem_extraction_v2.contracts import (
            ATIVO_RASTER,
            AssetCandidate,
            Caixa,
        )
        regioes = [RegiaoDoItem(7, 1, (Caixa(0, 100, 280, 400),)),
                   RegiaoDoItem(8, 1, (Caixa(0, 400, 280, 700),))]
        ativo = AssetCandidate(pagina=1, caixa=Caixa(40, 420, 240, 600),
                               tipo=ATIVO_RASTER, metodo_deteccao="teste")
        assert associar([ativo], regioes)[0].questao == 8

    def test_dois_ativos_na_mesma_pagina_vao_para_itens_diferentes(self):
        from agente_ia_edu.services.enem_extraction_v2.assets import (
            RegiaoDoItem,
            associar,
        )
        from agente_ia_edu.services.enem_extraction_v2.contracts import (
            ATIVO_RASTER,
            AssetCandidate,
            Caixa,
        )
        regioes = [RegiaoDoItem(7, 1, (Caixa(0, 100, 280, 400),)),
                   RegiaoDoItem(8, 1, (Caixa(283, 100, 560, 400),))]
        ativos = [AssetCandidate(pagina=1, caixa=Caixa(40, 150, 240, 300),
                                 tipo=ATIVO_RASTER, metodo_deteccao="teste"),
                  AssetCandidate(pagina=1, caixa=Caixa(320, 150, 520, 300),
                                 tipo=ATIVO_RASTER, metodo_deteccao="teste")]
        resultado = associar(ativos, regioes)
        assert [a.questao for a in resultado] == [7, 8]


class TestOrdemDasRegrasDeLimpeza:
    """Defeito meu, achado na medicao: a ordem das duas limpezas importa.

    `_remover_marca_dagua` parte o token gigante em fragmentos. Os fragmentos
    deixam de casar com o conjunto `fundo`, que foi medido no texto cru.
    Resultado observado: a marca d'agua de 2024 sobrevivia dentro da
    alternativa E mesmo com as duas regras ligadas.
    """

    def test_fundo_sai_antes_da_marca_dagua(self):
        from agente_ia_edu.services.enem_extraction_v2.text_layer import limpar_mobilia
        marca = "ENEM2024" * 70
        paginas = [f"texto proprio {i}. {marca}" for i in range(32)]
        limpas, relatorio = limpar_mobilia(paginas)
        assert relatorio["tokens_de_fundo_removidos"] >= 32
        assert all("ENEM2024ENEM2024" not in p for p in limpas)
        assert all(f"texto proprio {i}." in limpas[i] for i in range(32))


class TestNumeroDePagina:
    """O numero fica em LINHA PROPRIA e escapava da remocao por n-grama."""

    def test_numeracao_progressiva_e_detectada_e_removida(self):
        from agente_ia_edu.services.enem_extraction_v2.text_layer import (
            deslocamento_da_numeracao,
            limpar_mobilia,
        )
        paginas = [f"conteudo distinto numero {i*31}\n{i}" for i in range(1, 33)]
        assert deslocamento_da_numeracao(paginas) == 0
        limpas, relatorio = limpar_mobilia(paginas)
        assert relatorio["numeros_de_pagina_removidos"] == 32
        assert all(f"conteudo distinto numero {i*31}" in limpas[i - 1]
                   for i in range(1, 33))

    def test_numeracao_com_deslocamento(self):
        from agente_ia_edu.services.enem_extraction_v2.text_layer import (
            deslocamento_da_numeracao,
        )
        paginas = [f"corpo {i}\n{i + 2}" for i in range(1, 33)]
        assert deslocamento_da_numeracao(paginas) == 2

    def test_numero_solto_que_NAO_progride_e_preservado(self):
        """GUARDA: uma resposta numerica legitima nao e numero de pagina."""
        from agente_ia_edu.services.enem_extraction_v2.text_layer import (
            deslocamento_da_numeracao,
            limpar_mobilia,
        )
        paginas = [f"corpo distinto {i*7}\n42" for i in range(1, 33)]
        assert deslocamento_da_numeracao(paginas) is None
        limpas, relatorio = limpar_mobilia(paginas)
        assert relatorio["numeros_de_pagina_removidos"] == 0
        assert all("42" in p for p in limpas)


class TestUltimaAlternativaAnomala:
    """Material ENTRE BLOCOS absorvido pela ultima alternativa.

    A V2 nao corta: cortar exigiria adivinhar onde a alternativa termina.
    Ela SINALIZA, e o item vai para revisao. Erro declarado, nao silencioso.
    """

    def test_alternativa_e_desproporcional_e_sinalizada(self):
        from agente_ia_edu.services.enem_extraction_v2 import options
        from agente_ia_edu.services.enem_extraction_v2.contracts import OptionCandidate
        opcoes = [OptionCandidate(le, "x" * 90, i, "TAB") for i, le in enumerate("ABCD")]
        opcoes.append(OptionCandidate("E", "x" * 1600, 9, "TAB"))
        assert options.ultima_alternativa_anomala(opcoes)

    def test_alternativa_e_um_pouco_maior_nao_e_sinalizada(self):
        from agente_ia_edu.services.enem_extraction_v2 import options
        from agente_ia_edu.services.enem_extraction_v2.contracts import OptionCandidate
        opcoes = [OptionCandidate(le, "x" * 90, i, "TAB") for i, le in enumerate("ABCD")]
        opcoes.append(OptionCandidate("E", "x" * 200, 9, "TAB"))
        assert not options.ultima_alternativa_anomala(opcoes)

    def test_o_limiar_e_o_declarado(self):
        from agente_ia_edu.services.enem_extraction_v2.options import (
            RAZAO_MAXIMA_DA_ULTIMA_ALTERNATIVA,
        )
        assert RAZAO_MAXIMA_DA_ULTIMA_ALTERNATIVA == 3.0

    def test_alternativas_todas_curtas_nao_quebram_a_regra(self):
        """'7.' '8.' '9.' '10.' '11.' - item de matematica."""
        from agente_ia_edu.services.enem_extraction_v2 import options
        from agente_ia_edu.services.enem_extraction_v2.contracts import OptionCandidate
        opcoes = [OptionCandidate(le, t, i, "TAB") for i, (le, t) in
                  enumerate(zip("ABCDE", ["7.", "8.", "9.", "10.", "11."]))]
        assert not options.ultima_alternativa_anomala(opcoes)


class TestCaractereDeControle:
    """Caractere de controle nunca e texto de questao.

    Achado varrendo as 665 aprovadas depois do endurecimento de
    fragmentacao: 25 itens carregavam caracteres C0, e 22 deles estao em
    2021 D2 - o caderno sem ToUnicode. O texto sai assim:

        '<17><02><0e><05> <07> <10> ... formato que se assemelha a um triangulo'

    A trava de legibilidade nao pegava: PADRAO_GLIFO_CRU procura '/gNN', e a
    fracao de letras fica perto do piso porque a parte legitima do enunciado
    dilui o lixo.

    Esta regra NAO tem limiar: e presenca ou ausencia, como a do '/gNN'.
    """

    @pytest.mark.parametrize("codigo", [0x02, 0x07, 0x08, 0x0e, 0x0f, 0x10, 0x17])
    def test_qualquer_controle_c0_torna_o_texto_ilegivel(self, codigo):
        from agente_ia_edu.services.enem_extraction_v2.text_layer import texto_ilegivel
        assert texto_ilegivel(f"formato que se assemelha{chr(codigo)} a um triangulo")

    def test_texto_limpo_continua_legivel(self):
        from agente_ia_edu.services.enem_extraction_v2.text_layer import texto_ilegivel
        assert not texto_ilegivel("formato que se assemelha a um triangulo")

    @pytest.mark.parametrize("branco", ["\t", "\n", " "])
    def test_espaco_em_branco_legitimo_nao_e_controle(self, branco):
        """Tab e quebra de linha sao normalizados antes, nao sao lixo."""
        from agente_ia_edu.services.enem_extraction_v2.text_layer import texto_ilegivel
        assert not texto_ilegivel(f"formato que{branco}se assemelha a um triangulo")

    def test_uma_unica_ocorrencia_ja_basta(self):
        """Nao ha limiar: um caractere de controle ja condena o texto."""
        from agente_ia_edu.services.enem_extraction_v2.text_layer import texto_ilegivel
        limpo = "a" * 400 + " texto legitimo e longo o suficiente para diluir"
        assert not texto_ilegivel(limpo)
        assert texto_ilegivel(limpo + "\x02")

    def test_simbolos_cientificos_nao_sao_controle(self):
        from agente_ia_edu.services.enem_extraction_v2.text_layer import texto_ilegivel
        for texto in ("a temperatura de 25 °C no ensaio realizado",
                      "o angulo θ e o coeficiente υ medidos",
                      "a concentracao de 10 μg por litro medida"):
            assert not texto_ilegivel(texto), texto
