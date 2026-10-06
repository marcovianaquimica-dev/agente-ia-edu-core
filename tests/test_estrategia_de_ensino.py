"""ESTRATÉGIA E APOIO — o que fazer quando a explicação não funcionou.

O PROBLEMA QUE ESTA CAMADA RESOLVE
===================================
O ciclo anterior sabia mudar o JEITO de explicar: `explicacao_do_erro` tem
seis abordagens em escada e nunca repete a anterior. O que faltava era saber
três outras coisas:

    qual micro-habilidade está sendo ensinada;
    quanto apoio o aluno está recebendo agora;
    quando o apoio deve sair.

Sem a terceira, o aluno fica eternamente assistido — e acerto com ajuda não é
evidência de que ele resolve sozinho.

AS DUAS REGRAS QUE ESTE ARQUIVO TRAVA
======================================
1. a estratégia nunca repete a que acabou de falhar;
2. a evidência forte só vem de L0, sem apoio nenhum.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.estrategia_de_ensino import (
    ANALOGIA,
    DECOMPOSICAO,
    ESTRATEGIAS,
    EXEMPLO_RESOLVIDO,
    L0_AUTONOMO,
    L1_DICA,
    L2_PERGUNTAS_GUIADAS,
    L3_EXEMPLO_RESOLVIDO,
    NIVEIS,
    PERGUNTAS_GUIADAS,
    REVISAO_DE_PREREQUISITO,
    REPRESENTACAO_VISUAL,
    esgotou_as_estrategias,
    nivel_apos,
    proxima_estrategia,
    produz_evidencia_autonoma,
)


class AESTRATEGIAMUDA(unittest.TestCase):

    def test_sem_nada_usado_comeca_pela_primeira(self):
        self.assertEqual(ESTRATEGIAS[0], proxima_estrategia(usadas=()))

    def test_nunca_devolve_uma_ja_usada(self):
        usadas: list[str] = []
        for _ in range(len(ESTRATEGIAS)):
            proxima = proxima_estrategia(usadas=tuple(usadas))
            self.assertIsNotNone(proxima)
            self.assertNotIn(proxima, usadas)
            usadas.append(proxima)

    def test_quando_acabam_devolve_None(self):
        """Nenhuma estratégia a mais é o sinal de ESCALATE - não um loop."""
        self.assertIsNone(proxima_estrategia(usadas=ESTRATEGIAS))

    def test_a_revisao_de_prerequisito_so_entra_se_houver_prerequisito(self):
        """Mandar revisar o pré-requisito de quem não tem nenhum é mandar o
        aluno para lugar nenhum."""
        usadas = tuple(e for e in ESTRATEGIAS if e != REVISAO_DE_PREREQUISITO)
        self.assertIsNone(proxima_estrategia(usadas=usadas, tem_prerequisito=False))
        self.assertEqual(REVISAO_DE_PREREQUISITO,
                         proxima_estrategia(usadas=usadas, tem_prerequisito=True))

    def test_estrategia_desconhecida_na_lista_nao_quebra(self):
        """Uma versão antiga da tela pode mandar um valor que não existe mais."""
        self.assertIn(proxima_estrategia(usadas=("INVENTADA",)), ESTRATEGIAS)

    def test_a_habilidade_pode_restringir_as_estrategias(self):
        """Nem toda abordagem serve a toda habilidade."""
        permitidas = (EXEMPLO_RESOLVIDO, ANALOGIA)
        self.assertEqual(EXEMPLO_RESOLVIDO,
                         proxima_estrategia(usadas=(), permitidas=permitidas))
        self.assertEqual(ANALOGIA,
                         proxima_estrategia(usadas=(EXEMPLO_RESOLVIDO,),
                                            permitidas=permitidas))
        self.assertIsNone(
            proxima_estrategia(usadas=permitidas, permitidas=permitidas))

    def test_a_ordem_e_estavel(self):
        self.assertEqual(proxima_estrategia(usadas=(DECOMPOSICAO,)),
                         proxima_estrategia(usadas=(DECOMPOSICAO,)))


class OLOOPTEMFIM(unittest.TestCase):

    def test_esgotar_e_o_que_leva_a_escalar(self):
        self.assertFalse(esgotou_as_estrategias(usadas=(DECOMPOSICAO,)))
        self.assertTrue(esgotou_as_estrategias(usadas=ESTRATEGIAS))

    def test_sem_prerequisito_esgota_uma_estrategia_antes(self):
        usadas = tuple(e for e in ESTRATEGIAS if e != REVISAO_DE_PREREQUISITO)
        self.assertTrue(esgotou_as_estrategias(usadas=usadas,
                                               tem_prerequisito=False))
        self.assertFalse(esgotou_as_estrategias(usadas=usadas,
                                                tem_prerequisito=True))


class OAPOIOSAIAOSPOUCOS(unittest.TestCase):
    """L3 → L2 → L1 → L0. Quem acerta sobe um degrau; quem erra desce."""

    def test_a_escada_vai_do_mais_apoio_ao_autonomo(self):
        self.assertEqual((L3_EXEMPLO_RESOLVIDO, L2_PERGUNTAS_GUIADAS,
                          L1_DICA, L0_AUTONOMO), NIVEIS)

    def test_acertar_retira_apoio(self):
        self.assertEqual(L2_PERGUNTAS_GUIADAS,
                         nivel_apos(L3_EXEMPLO_RESOLVIDO, acertou=True))
        self.assertEqual(L1_DICA,
                         nivel_apos(L2_PERGUNTAS_GUIADAS, acertou=True))
        self.assertEqual(L0_AUTONOMO, nivel_apos(L1_DICA, acertou=True))

    def test_do_autonomo_acertando_nao_ha_para_onde_subir(self):
        self.assertEqual(L0_AUTONOMO, nivel_apos(L0_AUTONOMO, acertou=True))

    def test_errar_devolve_apoio(self):
        self.assertEqual(L1_DICA, nivel_apos(L0_AUTONOMO, acertou=False))
        self.assertEqual(L2_PERGUNTAS_GUIADAS,
                         nivel_apos(L1_DICA, acertou=False))

    def test_o_fundo_da_escada_nao_afunda(self):
        self.assertEqual(L3_EXEMPLO_RESOLVIDO,
                         nivel_apos(L3_EXEMPLO_RESOLVIDO, acertou=False))

    def test_nivel_desconhecido_volta_ao_mais_apoiado(self):
        """Na dúvida, apoiar mais - nunca menos."""
        self.assertEqual(L3_EXEMPLO_RESOLVIDO,
                         nivel_apos("INVENTADO", acertou=False))


class EVIDENCIAFORTESOVEMDEL0(unittest.TestCase):
    """Acerto com ajuda não é acerto sozinho. Esta é a regra que impede o
    sistema de dar por aprendido quem foi conduzido até a resposta."""

    def test_so_o_autonomo_produz_evidencia(self):
        self.assertTrue(produz_evidencia_autonoma(L0_AUTONOMO))

    def test_todos_os_niveis_com_apoio_nao_produzem(self):
        for nivel in (L1_DICA, L2_PERGUNTAS_GUIADAS, L3_EXEMPLO_RESOLVIDO):
            with self.subTest(nivel=nivel):
                self.assertFalse(produz_evidencia_autonoma(nivel))

    def test_nivel_desconhecido_nao_produz(self):
        """Fail closed: na dúvida sobre o apoio, não conta como evidência."""
        self.assertFalse(produz_evidencia_autonoma("INVENTADO"))
        self.assertFalse(produz_evidencia_autonoma(None))


class ESTACAMADANAOSABEDEQUEDISCIPLINASETRATA(unittest.TestCase):

    def test_o_modulo_nao_cita_assunto_nem_provedor(self):
        import pathlib

        fonte = (pathlib.Path(__file__).resolve().parent.parent
                 / "src/agente_ia_edu/services/estrategia_de_ensino.py"
                 ).read_text(encoding="utf-8").lower()
        for proibido in ("chemistry", "quimica", "química", "estequiometria",
                         "molar", "provider", "openai"):
            with self.subTest(proibido=proibido):
                self.assertNotIn(proibido, fonte)


if __name__ == "__main__":
    unittest.main()
