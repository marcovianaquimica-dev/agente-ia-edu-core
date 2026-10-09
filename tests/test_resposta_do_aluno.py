"""O QUE O ALUNO ESCREVEU, E O QUE DISSO SE PODE AFIRMAR.

A CAMADA QUE FALTAVA
=====================
Até aqui o aluno só podia clicar numa letra. Para o Edu conversar, ele
precisa aceitar "15", "15 g/mol", "três", "não sei" — e precisa fazer isso
sem inventar entendimento.

Entre o texto cru e qualquer decisão pedagógica entram duas etapas
explícitas:

    texto cru  →  RESPOSTA NORMALIZADA  →  OBSERVAÇÃO

A primeira pergunta "o que ele escreveu?". A segunda, "o que isso significa
para esta pergunta?". Pular direto do texto para "acertou" é o atalho que
transforma ambiguidade em conclusão.

FALHA FECHADO É A REGRA
========================
"3 ou 4" não vira 3. "não sei se é 3" não vira 3 nem vira "não sei". Um
texto que o sistema não entende com segurança vira AMBÍGUO, e o Edu
pergunta de novo — porque inventar um entendimento é pior que admitir que
não entendeu.

E É DETERMINÍSTICO
===================
Nada aqui chama modelo. "17" == 17 não é trabalho para uma LLM. A
interpretação semântica, quando vier, entra como camada DE CIMA — para o que
este módulo devolver como ambíguo, nunca para substituí-lo.
"""

from __future__ import annotations

import ast
import pathlib
import unittest

from _fonte import codigo, menciona

from agente_ia_edu.services.resposta_do_aluno import (
    ESPERA_ESCOLHA,
    ESPERA_NUMERO,
    ESPERA_TEXTO,
    OBS_AMBIGUA,
    OBS_CORRETA,
    OBS_INCORRETA,
    OBS_NAO_SEI,
    OBS_SEM_RESPOSTA,
    TIPO_AMBIGUO,
    TIPO_ESCOLHA,
    TIPO_NAO_SEI,
    TIPO_NUMERO,
    TIPO_TEXTO,
    TIPO_VAZIO,
    normalizar,
    observar,
)


class ONUMEROSAIDOTEXTO(unittest.TestCase):

    CASOS = {
        "15": 15,
        "17": 17,
        "44": 44,
        " 3 ": 3,
        "15 g/mol": 15,
        "15g/mol": 15,
        "17,0": 17,
        "17.0": 17,
        "0,5": 0.5,
        "44 g/mol.": 44,
        "R: 17": 17,
        "é 17": 17,
        "acho que 15": 15,
        "acho que são 3": 3,
        "acho que são 3 porque o número pequeno está no H": 3,
        "três": 3,
        "sao tres": 3,
        "dezessete": 17,
        "são três": 3,
    }

    def test_cada_um_normaliza_para_o_numero(self):
        for bruto, esperado in self.CASOS.items():
            with self.subTest(bruto=bruto):
                r = normalizar(bruto, espera=ESPERA_NUMERO)
                self.assertEqual(TIPO_NUMERO, r.tipo, f"{bruto!r} -> {r.tipo}")
                self.assertAlmostEqual(esperado, r.numero)

    def test_o_bruto_e_preservado(self):
        r = normalizar("  15 g/mol ", espera=ESPERA_NUMERO)
        self.assertEqual("  15 g/mol ", r.bruto)


class NAOSEIEINFORMACAO(unittest.TestCase):
    """§5 caminho C: não punir, não contar como acerto, não inventar."""

    CASOS = ("não sei", "nao sei", "não sei :(", "não lembro", "nao lembro",
             "sei lá", "não faço ideia", "nenhuma ideia", "me ajuda",
             "me ajuda por favor", "pode ajudar?", "não entendi a pergunta")

    def test_cada_um_e_reconhecido(self):
        for bruto in self.CASOS:
            with self.subTest(bruto=bruto):
                self.assertEqual(TIPO_NAO_SEI,
                                 normalizar(bruto, espera=ESPERA_NUMERO).tipo)

    def test_nao_sei_nao_vira_numero(self):
        self.assertIsNone(normalizar("não sei", espera=ESPERA_NUMERO).numero)


class AAMBIGUIDADEFALHAFECHADO(unittest.TestCase):

    def test_dois_numeros_nao_viram_um(self):
        r = normalizar("3 ou 4", espera=ESPERA_NUMERO)
        self.assertEqual(TIPO_AMBIGUO, r.tipo)
        self.assertIsNone(r.numero)

    def test_nao_sei_com_numero_e_ambiguo(self):
        """"não sei se é 3" não é uma resposta nem uma desistência."""
        r = normalizar("não sei se é 3", espera=ESPERA_NUMERO)
        self.assertEqual(TIPO_AMBIGUO, r.tipo)

    def test_texto_sem_numero_quando_se_espera_numero_e_ambiguo(self):
        r = normalizar("depende da fórmula", espera=ESPERA_NUMERO)
        self.assertEqual(TIPO_AMBIGUO, r.tipo)

    def test_uma_conta_nao_vira_o_primeiro_numero(self):
        """"14 + 3" tem dois números: lê-lo como 14 seria inventar."""
        self.assertEqual(TIPO_AMBIGUO,
                         normalizar("14 + 3", espera=ESPERA_NUMERO).tipo)

    def test_o_mesmo_numero_repetido_nao_e_ambiguo(self):
        """"3, três" diz a mesma coisa duas vezes."""
        r = normalizar("3, três", espera=ESPERA_NUMERO)
        self.assertEqual(TIPO_NUMERO, r.tipo)
        self.assertEqual(3, r.numero)

    def test_vazio_e_vazio_e_nao_ambiguo(self):
        for bruto in ("", "   ", "\n"):
            with self.subTest(bruto=repr(bruto)):
                self.assertEqual(TIPO_VAZIO,
                                 normalizar(bruto, espera=ESPERA_NUMERO).tipo)

    def test_nenhuma_entrada_estoura(self):
        self.assertEqual(TIPO_VAZIO, normalizar(None, espera=ESPERA_NUMERO).tipo)


class QUANDOSEESPERATEXTO(unittest.TestCase):
    """A mesma entrada significa coisas diferentes conforme o que se pede."""

    def test_uma_conta_e_texto_valido(self):
        r = normalizar("14 + 3", espera=ESPERA_TEXTO)
        self.assertEqual(TIPO_TEXTO, r.tipo)
        self.assertEqual("14 + 3", r.texto)

    def test_uma_justificativa_e_texto_valido(self):
        r = normalizar("somei o nitrogênio com os três hidrogênios",
                       espera=ESPERA_TEXTO)
        self.assertEqual(TIPO_TEXTO, r.tipo)

    def test_chutei_e_texto_e_nao_nao_sei(self):
        """"chutei" é uma informação pedagógica forte, e diferente de
        "não sei": ele respondeu, e está dizendo como."""
        self.assertEqual(TIPO_TEXTO,
                         normalizar("chutei", espera=ESPERA_TEXTO).tipo)

    def test_nao_sei_continua_sendo_nao_sei_mesmo_esperando_texto(self):
        self.assertEqual(TIPO_NAO_SEI,
                         normalizar("não sei", espera=ESPERA_TEXTO).tipo)

    def test_o_texto_e_aparado_mas_nao_reescrito(self):
        r = normalizar("  somei os dois  ", espera=ESPERA_TEXTO)
        self.assertEqual("somei os dois", r.texto)


class QUANDOSEESPERAESCOLHA(unittest.TestCase):

    def test_a_letra_e_reconhecida(self):
        for bruto in ("C", "c", " c "):
            with self.subTest(bruto=bruto):
                r = normalizar(bruto, espera=ESPERA_ESCOLHA)
                self.assertEqual(TIPO_ESCOLHA, r.tipo)
                self.assertEqual("C", r.texto)

    def test_algo_que_nao_e_letra_de_alternativa_e_ambiguo(self):
        self.assertEqual(TIPO_AMBIGUO,
                         normalizar("talvez a C", espera=ESPERA_ESCOLHA).tipo)

    def test_nao_sei_continua_funcionando(self):
        self.assertEqual(TIPO_NAO_SEI,
                         normalizar("não sei", espera=ESPERA_ESCOLHA).tipo)


class AOBSERVACAONAOEAEVIDENCIA(unittest.TestCase):
    """§9: o que se observou, e não o que o aluno sabe."""

    def test_numero_certo_e_observado_como_correto(self):
        r = normalizar("17", espera=ESPERA_NUMERO)
        self.assertEqual(OBS_CORRETA, observar(r, esperado_numero=17))

    def test_numero_errado_e_observado_como_incorreto(self):
        r = normalizar("15", espera=ESPERA_NUMERO)
        self.assertEqual(OBS_INCORRETA, observar(r, esperado_numero=17))

    def test_a_tolerancia_aceita_a_mesma_grandeza_escrita_de_outro_jeito(self):
        r = normalizar("17,0", espera=ESPERA_NUMERO)
        self.assertEqual(OBS_CORRETA, observar(r, esperado_numero=17))

    def test_nao_sei_tem_observacao_propria(self):
        r = normalizar("não sei", espera=ESPERA_NUMERO)
        self.assertEqual(OBS_NAO_SEI, observar(r, esperado_numero=17))

    def test_nao_sei_NAO_e_observado_como_incorreto(self):
        """Confundir os dois faria "não sei" alimentar a conta de erros."""
        r = normalizar("não sei", espera=ESPERA_NUMERO)
        self.assertNotEqual(OBS_INCORRETA, observar(r, esperado_numero=17))

    def test_ambiguo_tem_observacao_propria(self):
        r = normalizar("3 ou 4", espera=ESPERA_NUMERO)
        self.assertEqual(OBS_AMBIGUA, observar(r, esperado_numero=3))

    def test_ambiguo_NAO_e_correto_mesmo_contendo_o_numero_certo(self):
        """A armadilha: "3 ou 4" contém o 3. Aceitá-lo seria dar o ponto a
        quem listou as opções."""
        r = normalizar("3 ou 4", espera=ESPERA_NUMERO)
        self.assertNotEqual(OBS_CORRETA, observar(r, esperado_numero=3))

    def test_vazio_tem_observacao_propria(self):
        r = normalizar("", espera=ESPERA_NUMERO)
        self.assertEqual(OBS_SEM_RESPOSTA, observar(r, esperado_numero=17))

    def test_texto_sem_gabarito_esperado_nao_e_julgado_certo_nem_errado(self):
        """"Como você pensou?" não tem resposta certa."""
        r = normalizar("somei 14 com 3", espera=ESPERA_TEXTO)
        obs = observar(r)
        self.assertNotIn(obs, (OBS_CORRETA, OBS_INCORRETA))

    def test_escolha_certa_e_correta(self):
        r = normalizar("C", espera=ESPERA_ESCOLHA)
        self.assertEqual(OBS_CORRETA, observar(r, esperado_texto="C"))

    def test_escolha_errada_e_incorreta(self):
        r = normalizar("A", espera=ESPERA_ESCOLHA)
        self.assertEqual(OBS_INCORRETA, observar(r, esperado_texto="C"))


class ESTACAMADANAOCONHECEQUIMICANEMIA(unittest.TestCase):

    FONTE = (pathlib.Path(__file__).resolve().parent.parent
             / "src/agente_ia_edu/services/resposta_do_aluno.py")

    def test_o_modulo_nao_chama_modelo(self):
        baixo = codigo(self.FONTE).lower()
        for proibido in ("textgenerationprovider", "build_text_provider",
                         "openai", "provider"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, baixo)

    def test_o_modulo_nao_toca_banco(self):
        baixo = codigo(self.FONTE).lower()
        for proibido in ("asyncsession", "sqlalchemy", "db.models"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, baixo)

    def test_o_modulo_nao_conhece_quimica(self):
        """No CÓDIGO, e por palavra inteira.

        As duas coisas são necessárias: a docstring usa "15 g/mol" como
        exemplo, e "NH" está dentro de "CONHECE". Ver `tests/_fonte.py` —
        esta armadilha pegou quatro varreduras diferentes neste trabalho.
        """
        fonte = codigo(self.FONTE)
        for proibido in ("NH", "massa molar", "MASSA_MOLAR", "mol", "g/mol",
                         "NH3", "CO2", "atomo", "elemento"):
            with self.subTest(proibido=proibido):
                self.assertFalse(menciona(fonte, proibido),
                                 f"o código menciona {proibido!r}")

    def test_o_modulo_nao_importa_a_politica_de_dominio(self):
        arvore = ast.parse(self.FONTE.read_text(encoding="utf-8"))
        nomes: list[str] = []
        for no in ast.walk(arvore):
            if isinstance(no, ast.ImportFrom):
                nomes.append(no.module or "")
        for proibido in ("pedagogical_analysis", "curriculum_domain_map"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, nomes)

    def test_nenhuma_observacao_significa_dominio(self):
        import agente_ia_edu.services.resposta_do_aluno as mod
        for nome, valor in vars(mod).items():
            if nome.startswith("OBS_") and isinstance(valor, str):
                with self.subTest(obs=nome):
                    for proibido in ("MASTER", "DOMIN", "LEARNED", "CONFIRM"):
                        self.assertNotIn(proibido, valor.upper())


if __name__ == "__main__":
    unittest.main()
