"""O QUE FAZ DE UM ITEM UM INSTRUMENTO DE SONDAGEM — e o que não faz.

O P0, MEDIDO NO NAVEGADOR EM 2026-10-07
========================================
A sondagem de Estequiometria serviu três itens, e um deles veio do banco
genérico, com cinco alternativas. Existiam cinco itens curados, publicados,
um por micro-habilidade — e eles não foram escolhidos.

A causa, auditada no repositório: `MicroDiagnosticService` pede
`create_practice(content_code=..., question_count=3)`. A seleção filtra por
CONTEÚDO e ordena por número oficial. A micro-habilidade nunca entra na
pergunta. Os itens curados competiam em pé de igualdade com 20 questões
comuns de Estequiometria, e perdiam por número.

O que o publicador JÁ GRAVAVA, e ninguém lia:

    Question.metadata_["purpose"]                     = "PROBE"
    PedagogicalClassification.metadata_["purpose"]    = "PROBE"

Medido no banco: `purpose = PROBE` aparece em exatamente 5 classificações —
as cinco curadas. `reasoning_type = DIAGNOSTIC`, que seria o candidato
natural, aparece em 39: as 5 curadas MAIS 34 do banco diagnóstico gerado por
IA. Usar `reasoning_type` promoveria 34 itens a instrumento deliberado.

Por isso o contrato é a FINALIDADE declarada, somada às invariantes de
publicação e validação — e não o prefixo `SOND-`, que é código e serve a
auditoria humana, não a arquitetura.

FINALIDADE NÃO É FORMATO
=========================
`purpose` diz PARA QUE o item existe. `question_type` diz COMO se responde.
São dimensões independentes, e este módulo não olha a segunda: um probe
conversacional futuro tem a mesma finalidade e outro formato. Há teste de
que nenhuma asserção daqui exige múltipla escolha.
"""

from __future__ import annotations

import ast
import pathlib
import unittest
from dataclasses import replace

from _fonte import codigo as _codigo

from agente_ia_edu.services.verificacao import FINALIDADE_VERIFICACAO
from agente_ia_edu.services.instrumento_de_sondagem import (
    FINALIDADE_SONDAGEM,
    ORIGEM_CURADA,
    ORIGEM_FALLBACK,
    Candidato,
    escolher,
    inelegibilidade,
)

CONTEUDO = "CHEMISTRY-PHYSICAL-STOICHIOMETRY"
HAB = "MASSA_MOLAR"


def _curado(**mudancas) -> Candidato:
    """Um candidato que cumpre TODOS os critérios. Os testes o estragam."""
    base = Candidato(
        question_version_id="11111111-1111-1111-1111-111111111111",
        conteudo=CONTEUDO,
        habilidade=HAB,
        finalidade=FINALIDADE_SONDAGEM,
        lifecycle="ACTIVE",
        provenance="HUMAN_VALIDATED",
        validado_por="nucleo_edu_360",
        status_da_questao="PUBLISHED",
        visibilidade="PUBLIC",
        escola_id=None,
        gabarito_definido=True,
        dependencia_visual=False,
        protegida=False,
        numero_oficial=1,
    )
    return replace(base, **mudancas)


def _generico(**mudancas) -> Candidato:
    """Questão comum do banco, classificada na mesma habilidade.

    `numero_oficial=0` de propósito: ela vence o curado em qualquer ordenação
    por número. Se o contrato priorizasse por número em vez de por
    finalidade, estes testes passariam por acidente.
    """
    padrao = {"question_version_id": "22222222-2222-2222-2222-222222222222",
              "finalidade": None, "provenance": "AI_VERIFIED",
              "validado_por": None, "numero_oficial": 0}
    padrao.update(mudancas)
    return _curado(**padrao)


class OCURADOELEGIVEL(unittest.TestCase):

    def test_o_item_completo_e_elegivel(self):
        self.assertIsNone(inelegibilidade(_curado(), habilidade=HAB,
                                          conteudo=CONTEUDO))

    def test_a_finalidade_declarada_e_o_que_o_distingue(self):
        """Sem `purpose`, ele é uma questão boa — não um instrumento."""
        motivo = inelegibilidade(_curado(finalidade=None), habilidade=HAB,
                                 conteudo=CONTEUDO)
        self.assertIsNotNone(motivo)
        self.assertIn("finalidade", motivo.lower())


class OCONTRATONAOEOPREFIXO(unittest.TestCase):
    """§4 do bloco: "SOND-" não pode ser o contrato arquitetural."""

    FONTE = (pathlib.Path(__file__).resolve().parent.parent
             / "src/agente_ia_edu/services/instrumento_de_sondagem.py")

    def test_o_modulo_nao_conhece_o_prefixo_sond(self):
        """A varredura é no CÓDIGO, não no texto.

        A primeira versão lia o arquivo inteiro e reprovou: a docstring cita
        "SOND-" justamente para explicar que ele NÃO é o contrato. Procurar a
        string no arquivo acusa a explicação. `_codigo()` remove docstrings e
        comentários, e é o mesmo motivo pelo qual `test_sinal_diagnostico`
        verifica imports pela AST.
        """
        self.assertNotIn("SOND-", _codigo(self.FONTE))

    def test_o_modulo_nao_nomeia_nenhuma_micro_habilidade(self):
        texto = self.FONTE.read_text(encoding="utf-8")
        for codigo in ("MASSA_MOLAR", "LEITURA_DE_FORMULA",
                       "RELACAO_MASSA_MOL", "PROPORCAO_ESTEQUIOMETRICA",
                       "ESTEQUIOMETRIA_INTEGRADA"):
            with self.subTest(codigo=codigo):
                self.assertNotIn(codigo, texto)

    def test_o_modulo_nao_nomeia_nenhum_conteudo(self):
        texto = self.FONTE.read_text(encoding="utf-8")
        for codigo in ("CHEMISTRY-PHYSICAL-STOICHIOMETRY",
                       "CHEMISTRY-GENERAL-BALANCING"):
            with self.subTest(codigo=codigo):
                self.assertNotIn(codigo, texto)

    def test_o_contrato_e_decidido_por_um_item_sem_codigo_nenhum(self):
        """A prova viva: um candidato cujo id não diz nada continua elegível."""
        anonimo = _curado(
            question_version_id="99999999-9999-9999-9999-999999999999")
        self.assertIsNone(inelegibilidade(anonimo, habilidade=HAB,
                                          conteudo=CONTEUDO))


class FALHAFECHADO(unittest.TestCase):
    """§10.C — metadata parcial não compra prioridade."""

    CASOS = {
        "rascunho": {"status_da_questao": "DRAFT"},
        "arquivada": {"status_da_questao": "ARCHIVED"},
        "classificacao_aposentada": {"lifecycle": "SUPERSEDED"},
        "nao_validada_por_humano": {"provenance": "AI_SUGGESTED"},
        "humano_sem_nome": {"validado_por": None},
        "humano_sem_nome_vazio": {"validado_por": "   "},
        "sem_gabarito": {"gabarito_definido": False},
        "depende_de_imagem": {"dependencia_visual": True},
        "protegida": {"protegida": True},
        "conteudo_errado": {"conteudo": "OUTRO-CONTEUDO"},
        "habilidade_errada": {"habilidade": "OUTRA_HABILIDADE"},
        "sem_habilidade": {"habilidade": None},
        "sem_conteudo": {"conteudo": None},
    }

    def test_cada_defeito_desqualifica(self):
        for nome, mudanca in self.CASOS.items():
            with self.subTest(caso=nome):
                motivo = inelegibilidade(_curado(**mudanca), habilidade=HAB,
                                         conteudo=CONTEUDO)
                self.assertIsNotNone(motivo, f"{nome} passou")
                self.assertTrue(motivo.strip())

    def test_o_motivo_e_legivel_e_diferente_por_defeito(self):
        """Um motivo genérico não serve à observabilidade (§16)."""
        motivos = {
            nome: inelegibilidade(_curado(**m), habilidade=HAB,
                                  conteudo=CONTEUDO)
            for nome, m in self.CASOS.items()
        }
        # Não exijo um motivo único por defeito - alguns são a mesma regra -
        # mas exijo mais de um motivo distinto, senão a mensagem não informa.
        self.assertGreater(len(set(motivos.values())), 4)


class AVISIBILIDADEEDOTENANT(unittest.TestCase):
    """§10.I — probe de outra instituição não vaza."""

    def test_publico_serve_a_qualquer_escola(self):
        self.assertIsNone(inelegibilidade(
            _curado(visibilidade="PUBLIC", escola_id=None),
            habilidade=HAB, conteudo=CONTEUDO, escola_do_aluno="escola-A"))

    def test_da_escola_serve_a_propria_escola(self):
        self.assertIsNone(inelegibilidade(
            _curado(visibilidade="SCHOOL", escola_id="escola-A"),
            habilidade=HAB, conteudo=CONTEUDO, escola_do_aluno="escola-A"))

    def test_da_escola_NAO_serve_a_outra(self):
        motivo = inelegibilidade(
            _curado(visibilidade="SCHOOL", escola_id="escola-B"),
            habilidade=HAB, conteudo=CONTEUDO, escola_do_aluno="escola-A")
        self.assertIsNotNone(motivo)
        self.assertIn("escopo", motivo.lower())

    def test_privada_nao_serve_a_sondagem(self):
        self.assertIsNotNone(inelegibilidade(
            _curado(visibilidade="PRIVATE"),
            habilidade=HAB, conteudo=CONTEUDO, escola_do_aluno="escola-A"))

    def test_escola_sem_dono_declarado_falha_fechado(self):
        """SCHOOL sem school_id é ambíguo - e ambiguidade não vira acesso."""
        self.assertIsNotNone(inelegibilidade(
            _curado(visibilidade="SCHOOL", escola_id=None),
            habilidade=HAB, conteudo=CONTEUDO, escola_do_aluno="escola-A"))


class CURADOVENCEGENERICO(unittest.TestCase):
    """§10.A — e o resultado não pode depender de sorte."""

    def test_entre_um_curado_e_varios_genericos_vence_o_curado(self):
        escolha = escolher([_generico(), _curado(), _generico(
            question_version_id="33333333-3333-3333-3333-333333333333")],
            habilidade=HAB, conteudo=CONTEUDO)
        self.assertEqual(ORIGEM_CURADA, escolha.origem)
        self.assertEqual(_curado().question_version_id,
                         escolha.question_version_id)

    def test_a_ordem_da_lista_nao_muda_a_escolha(self):
        c, g = _curado(), _generico()
        for ordem in ([c, g], [g, c]):
            with self.subTest(ordem=[x.question_version_id[:2] for x in ordem]):
                self.assertEqual(ORIGEM_CURADA,
                                 escolher(ordem, habilidade=HAB,
                                          conteudo=CONTEUDO).origem)

    def test_repetir_a_escolha_devolve_sempre_o_mesmo(self):
        """§10.G — determinismo, não sorte."""
        candidatos = [_generico(), _curado(), _generico(
            question_version_id="44444444-4444-4444-4444-444444444444")]
        escolhas = {escolher(candidatos, habilidade=HAB,
                             conteudo=CONTEUDO).question_version_id
                    for _ in range(50)}
        self.assertEqual(1, len(escolhas))

    def test_curado_INELEGIVEL_nao_vence_generico(self):
        """O ponto de §10.C: metadata parcial não compra prioridade."""
        escolha = escolher([_curado(status_da_questao="DRAFT"), _generico()],
                           habilidade=HAB, conteudo=CONTEUDO)
        self.assertEqual(ORIGEM_FALLBACK, escolha.origem)
        self.assertEqual(_generico().question_version_id,
                         escolha.question_version_id)


class OFALLBACKEEXPLICITO(unittest.TestCase):
    """§10.B."""

    def test_sem_curado_o_generico_e_escolhido_e_a_origem_aparece(self):
        escolha = escolher([_generico()], habilidade=HAB, conteudo=CONTEUDO)
        self.assertEqual(ORIGEM_FALLBACK, escolha.origem)

    def test_sem_candidato_nenhum_nao_se_inventa_instrumento(self):
        self.assertIsNone(escolher([], habilidade=HAB, conteudo=CONTEUDO))

    def test_so_candidatos_de_outra_habilidade_nao_produz_escolha(self):
        self.assertIsNone(escolher([_generico(habilidade="OUTRA")],
                                   habilidade=HAB, conteudo=CONTEUDO))

    def test_toda_escolha_diz_por_que_foi_escolhida(self):
        for candidatos in ([_curado()], [_generico()]):
            escolha = escolher(candidatos, habilidade=HAB, conteudo=CONTEUDO)
            with self.subTest(origem=escolha.origem):
                self.assertTrue(escolha.motivo.strip())

    def test_a_escolha_carrega_a_habilidade_sondada(self):
        self.assertEqual(HAB, escolher([_curado()], habilidade=HAB,
                                       conteudo=CONTEUDO).habilidade)


class HABILIDADEERRADANUNCAVAZA(unittest.TestCase):
    """§10.D."""

    def test_curado_de_outra_habilidade_nao_e_servido(self):
        outro = _curado(habilidade="LEITURA_DE_FORMULA")
        self.assertIsNone(escolher([outro], habilidade=HAB,
                                   conteudo=CONTEUDO))

    def test_nem_quando_e_o_unico_candidato_disponivel(self):
        outro = _curado(habilidade="LEITURA_DE_FORMULA")
        generico_certo = _generico()
        escolha = escolher([outro, generico_certo], habilidade=HAB,
                           conteudo=CONTEUDO)
        self.assertEqual(generico_certo.question_version_id,
                         escolha.question_version_id)

    def test_curado_de_outro_conteudo_nao_e_servido(self):
        self.assertIsNone(escolher([_curado(conteudo="OUTRO")],
                                   habilidade=HAB, conteudo=CONTEUDO))


class DESEMPATEENTRECURADOS(unittest.TestCase):
    """Hoje há um por habilidade. A política precisa existir mesmo assim."""

    def test_dois_curados_elegiveis_escolhem_sempre_o_mesmo(self):
        a = _curado(question_version_id="aaaaaaaa-0000-0000-0000-000000000000",
                    numero_oficial=2)
        b = _curado(question_version_id="bbbbbbbb-0000-0000-0000-000000000000",
                    numero_oficial=1)
        escolhas = {escolher([a, b], habilidade=HAB,
                             conteudo=CONTEUDO).question_version_id
                    for _ in range(20)}
        self.assertEqual(1, len(escolhas))

    def test_o_desempate_e_por_numero_oficial_e_depois_pelo_id(self):
        a = _curado(question_version_id="aaaaaaaa-0000-0000-0000-000000000000",
                    numero_oficial=2)
        b = _curado(question_version_id="bbbbbbbb-0000-0000-0000-000000000000",
                    numero_oficial=1)
        self.assertEqual(b.question_version_id,
                         escolher([a, b], habilidade=HAB,
                                  conteudo=CONTEUDO).question_version_id)

    def test_sem_numero_oficial_o_id_decide_e_nao_a_ordem_da_lista(self):
        a = _curado(question_version_id="aaaaaaaa-0000-0000-0000-000000000000",
                    numero_oficial=None)
        b = _curado(question_version_id="bbbbbbbb-0000-0000-0000-000000000000",
                    numero_oficial=None)
        self.assertEqual(escolher([a, b], habilidade=HAB,
                                  conteudo=CONTEUDO).question_version_id,
                         escolher([b, a], habilidade=HAB,
                                  conteudo=CONTEUDO).question_version_id)


class FINALIDADENAOEFORMATO(unittest.TestCase):
    """§9 — não acoplar probe a múltipla escolha.

    A próxima evolução é uma sondagem conversacional. Se o contrato exigisse
    alternativas, ela nasceria tendo de mentir sobre o próprio formato.
    """

    FONTE = (pathlib.Path(__file__).resolve().parent.parent
             / "src/agente_ia_edu/services/instrumento_de_sondagem.py")

    def test_o_contrato_nao_menciona_formato_de_resposta(self):
        """No CÓDIGO. A prosa cita os formatos para dizer que não os usa."""
        baixo = _codigo(self.FONTE).lower()
        for formato in ("multiple_choice", "question_type", "alternativa",
                        "option_key", "is_valid_option"):
            with self.subTest(formato=formato):
                self.assertNotIn(formato, baixo)

    def test_o_candidato_declara_gabarito_definido_e_nao_contagem_de_opcoes(self):
        campos = set(Candidato.__dataclass_fields__)
        self.assertIn("gabarito_definido", campos)
        for proibido in ("alternativas", "opcoes", "options", "question_type"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, campos)


class OCONTRATONAOTOCAEMDOMINIO(unittest.TestCase):
    """§7 e §10.F — selecionar não é medir."""

    FONTE = (pathlib.Path(__file__).resolve().parent.parent
             / "src/agente_ia_edu/services/instrumento_de_sondagem.py")

    def test_o_modulo_nao_toca_banco_nem_provedor(self):
        baixo = self.FONTE.read_text(encoding="utf-8").lower()
        for proibido in ("asyncsession", "sqlalchemy", "db.models",
                         "textgenerationprovider", "build_text_provider"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, baixo)

    def test_o_modulo_nao_importa_a_politica_de_dominio(self):
        arvore = ast.parse(self.FONTE.read_text(encoding="utf-8"))
        nomes: list[str] = []
        for no in ast.walk(arvore):
            if isinstance(no, ast.ImportFrom):
                nomes.append(no.module or "")
                nomes += [a.name for a in no.names]
            elif isinstance(no, ast.Import):
                nomes += [a.name for a in no.names]
        for proibido in ("pedagogical_analysis", "curriculum_domain_map",
                         "PerformanceThresholdPolicy"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, nomes)

    def test_a_escolha_nao_devolve_nada_parecido_com_nota(self):
        escolha = escolher([_curado()], habilidade=HAB, conteudo=CONTEUDO)
        campos = {c.lower() for c in type(escolha).__dataclass_fields__}
        for proibido in ("mastery", "score", "accuracy", "band", "evidence",
                         "dominio"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, campos)


if __name__ == "__main__":
    unittest.main()


class AFINALIDADEEUMPARAMETRO(unittest.TestCase):
    """O contrato serve a SONDAGEM e a VERIFICAÇÃO, sem duplicar-se.

    Até 2026-10-08 `inelegibilidade` comparava com `FINALIDADE_SONDAGEM`
    fixo. Verificar a micro-habilidade depois do ensino precisa do mesmo
    contrato inteiro — viva, validada por humano com nome, publicada,
    acessível, com gabarito, sem imagem, não protegida — e de uma finalidade
    DIFERENTE.

    Escrever um segundo módulo com os mesmos oito critérios criaria duas
    definições de "instrumento utilizável", e elas divergiriam no primeiro
    ajuste. Então a finalidade passa a ser parâmetro, e o padrão continua
    sendo a sondagem.

    POR QUE A FINALIDADE PRECISA SEPARAR OS DOIS
    =============================================
    O item de sondagem pergunta NH₃, e é a primeira coisa que o aluno vê. O
    de verificação não pode perguntar NH₃, porque ele acabou de ver a
    resolução do NH₃ passo a passo. Se as duas finalidades fossem
    intercambiáveis, o diagnóstico poderia medir com um item escrito para
    ser respondido DEPOIS do ensino — e a verificação, com o item que ele já
    errou.
    """

    def test_o_padrao_continua_sendo_sondagem(self):
        self.assertIsNone(inelegibilidade(_curado(), habilidade=HAB,
                                          conteudo=CONTEUDO))

    def test_um_item_de_verificacao_NAO_serve_de_sondagem(self):
        motivo = inelegibilidade(_curado(finalidade=FINALIDADE_VERIFICACAO),
                                 habilidade=HAB, conteudo=CONTEUDO)
        self.assertIsNotNone(motivo)
        self.assertIn("finalidade", motivo)

    def test_e_um_item_de_sondagem_NAO_serve_de_verificacao(self):
        motivo = inelegibilidade(_curado(), habilidade=HAB, conteudo=CONTEUDO,
                                 finalidade=FINALIDADE_VERIFICACAO)
        self.assertIsNotNone(motivo)
        self.assertIn("finalidade", motivo)

    def test_o_item_de_verificacao_e_elegivel_quando_pedido(self):
        self.assertIsNone(
            inelegibilidade(_curado(finalidade=FINALIDADE_VERIFICACAO),
                            habilidade=HAB, conteudo=CONTEUDO,
                            finalidade=FINALIDADE_VERIFICACAO))

    def test_os_outros_oito_critERIOS_continuam_valendo(self):
        """Mudar a finalidade não afrouxa nada mais.

        Um por um, porque é exatamente aqui que um atalho passaria: bastaria
        `inelegibilidade` devolver None cedo para a verificação e o item sem
        gabarito entraria.
        """
        estragos = (
            {"lifecycle": "SUPERSEDED"},
            {"provenance": "AI_SUGGESTED"},
            {"validado_por": ""},
            {"status_da_questao": "DRAFT"},
            {"visibilidade": "PRIVATE"},
            {"gabarito_definido": False},
            {"dependencia_visual": True},
            {"protegida": True},
        )
        for estrago in estragos:
            with self.subTest(**estrago):
                cand = _curado(finalidade=FINALIDADE_VERIFICACAO, **estrago)
                self.assertIsNotNone(
                    inelegibilidade(cand, habilidade=HAB, conteudo=CONTEUDO,
                                    finalidade=FINALIDADE_VERIFICACAO))

    def test_escolher_devolve_o_de_verificacao_quando_pede_verificacao(self):
        itens = [_curado(), _curado(
            question_version_id="33333333-3333-3333-3333-333333333333",
            finalidade=FINALIDADE_VERIFICACAO, numero_oficial=9)]
        e = escolher(itens, habilidade=HAB, conteudo=CONTEUDO,
                     finalidade=FINALIDADE_VERIFICACAO)
        self.assertEqual("33333333-3333-3333-3333-333333333333",
                         e.question_version_id)
        self.assertEqual(ORIGEM_CURADA, e.origem)

    def test_e_o_de_sondagem_quando_pede_sondagem(self):
        itens = [_curado(), _curado(
            question_version_id="33333333-3333-3333-3333-333333333333",
            finalidade=FINALIDADE_VERIFICACAO, numero_oficial=0)]
        e = escolher(itens, habilidade=HAB, conteudo=CONTEUDO)
        self.assertEqual("11111111-1111-1111-1111-111111111111",
                         e.question_version_id)

    def test_o_motivo_diz_QUAL_finalidade_foi_exigida(self):
        """§16: a decisão tem de ser auditável sem ler log humano."""
        e = escolher([_curado(finalidade=FINALIDADE_VERIFICACAO)],
                     habilidade=HAB, conteudo=CONTEUDO,
                     finalidade=FINALIDADE_VERIFICACAO)
        self.assertIn("verifica", e.motivo.lower())
