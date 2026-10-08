"""RELATÓRIO DE APOIO À APRENDIZAGEM — do estudante, e só dele.

O §17 é a regra mais enfática da especificação, e a única marcada como
"obrigatória e não pode ser reinterpretada". Ela tem duas metades, e a
segunda é a que este arquivo guarda com mais cuidado.

A PRIMEIRA METADE — O RELATÓRIO EXISTE
=======================================
Gerar, visualizar, baixar em PDF. Baseado em evidências, conciso, e sem
inventar o que não houve: se não há dificuldade registrada, o relatório diz
isso em vez de preencher a seção.

A SEGUNDA METADE — ELE NÃO É ENCAMINHADO
=========================================
Proibido: enviar ao professor, encaminhar à Coordenação, compartilhar
automaticamente, notificar terceiros, integrar a um mecanismo de
distribuição. Nem botão.

A ausência de código é uma garantia frágil — alguém acrescenta a rota amanhã
sem saber que não podia. Então ela é VARRIDA: há teste percorrendo as rotas
registradas do app e a fonte do serviço atrás de qualquer forma de envio.

E a varredura precisa distinguir o que é legítimo. O portal do professor tem
relatórios próprios, e o §17 diz para não eliminá-los — o que não pode haver
é o EDU encaminhando ESTE relatório.

O QUE ELE NÃO É
================
Diagnóstico clínico. O §17 proíbe a palavra "deficiência" como diagnóstico
de dificuldade escolar, e há teste varrendo o texto gerado.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone

from agente_ia_edu.services.relatorio_de_apoio import (
    SECOES,
    montar_relatorio,
)

HOJE = datetime(2026, 10, 8, 12, 0, tzinfo=timezone.utc)


def _situacao(estado: str, *, revisao=False, oportunidades=1):
    return {"estado": estado, "revisao_recomendada": revisao,
            "oportunidades": oportunidades, "acerto": 0.5,
            "respondidas": 4, "motivo": "x", "ultima_em": HOJE.isoformat()}


def _apoio(skill: str, *, dicas=0, ajuda=0, sozinho=False, concluido=True):
    return {"skill": skill, "hints_used": dicas, "help_requests": ajuda,
            "solved_unaided": sozinho, "completed": concluido}


class ORELATORIOEXISTE(unittest.TestCase):

    def test_tem_as_secoes_que_a_spec_pede(self):
        r = montar_relatorio(aluno_nome="Ana", conteudo="Estequiometria",
                             habilidades={}, apoios=[], agora=HOJE)
        self.assertTrue(r["secoes"])
        for s in r["secoes"]:
            with self.subTest(s["chave"]):
                self.assertIn(s["chave"], SECOES)

    def test_nomeia_a_disciplina_e_o_conteudo(self):
        r = montar_relatorio(aluno_nome="Ana", conteudo="Estequiometria",
                             habilidades={}, apoios=[], agora=HOJE)
        self.assertIn("Estequiometria", r["titulo"] + r["subtitulo"])

    def test_e_o_aluno(self):
        r = montar_relatorio(aluno_nome="Ana", conteudo="Estequiometria",
                             habilidades={}, apoios=[], agora=HOJE)
        self.assertIn("Ana", r["titulo"] + r["subtitulo"])


class SEMEVIDENCIANAOINVENTA(unittest.TestCase):
    """O §17: "quando houver evidências". Sem elas, dizer isso."""

    def test_sem_nada_registrado_o_relatorio_diz_isso(self):
        r = montar_relatorio(aluno_nome="Ana", conteudo="Estequiometria",
                             habilidades={}, apoios=[], agora=HOJE)
        texto = " ".join(i for s in r["secoes"] for i in s["itens"]).lower()
        self.assertTrue("ainda não" in texto or "não há" in texto, texto)

    def test_e_nao_lista_dificuldade_que_nao_houve(self):
        r = montar_relatorio(aluno_nome="Ana", conteudo="Estequiometria",
                             habilidades={}, apoios=[], agora=HOJE)
        dificuldades = next(s for s in r["secoes"]
                            if s["chave"] == "dificuldades")
        for item in dificuldades["itens"]:
            with self.subTest(item):
                self.assertNotIn("não domina", item.lower())


class SOZINHOEOMAJUDASAOSEPARADOS(unittest.TestCase):
    """O §17 pede as duas listas, e elas não podem se misturar."""

    def test_o_que_fez_sozinho_entra_na_sua_secao(self):
        from agente_ia_edu.services.consolidacao import ESTADO_DEMONSTRADO

        r = montar_relatorio(
            aluno_nome="Ana", conteudo="Estequiometria",
            habilidades={"MASSA_MOLAR": _situacao(ESTADO_DEMONSTRADO)},
            apoios=[], agora=HOJE)
        sozinho = next(s for s in r["secoes"] if s["chave"] == "sozinho")
        self.assertTrue(any("MASSA_MOLAR" in i or "massa molar" in i.lower()
                            for i in sozinho["itens"]))

    def test_o_que_fez_COM_AJUDA_entra_na_outra(self):
        r = montar_relatorio(
            aluno_nome="Ana", conteudo="Estequiometria", habilidades={},
            apoios=[_apoio("MASSA_MOLAR", dicas=3, concluido=True)],
            agora=HOJE)
        com_apoio = next(s for s in r["secoes"] if s["chave"] == "com_apoio")
        self.assertTrue(com_apoio["itens"])

    def test_e_quem_resolveu_COM_DICA_nao_aparece_como_sozinho(self):
        """A invariante do produto inteiro, agora no papel que o aluno leva."""
        r = montar_relatorio(
            aluno_nome="Ana", conteudo="Estequiometria", habilidades={},
            apoios=[_apoio("MASSA_MOLAR", dicas=3, sozinho=False)],
            agora=HOJE)
        sozinho = next(s for s in r["secoes"] if s["chave"] == "sozinho")
        self.assertFalse(
            any("MASSA_MOLAR" in i for i in sozinho["itens"]),
            "acertar com dica virou 'fez sozinho' no relatório")


class NAOEDIAGNOSTICOCLINICO(unittest.TestCase):

    def test_a_palavra_deficiencia_nunca_aparece(self):
        from agente_ia_edu.services.consolidacao import ESTADO_EM_APRENDIZADO

        r = montar_relatorio(
            aluno_nome="Ana", conteudo="Estequiometria",
            habilidades={"X": _situacao(ESTADO_EM_APRENDIZADO, revisao=True)},
            apoios=[_apoio("X", dicas=4, ajuda=2)], agora=HOJE)
        bruto = _texto_inteiro(r).lower()
        for proibida in ("deficiên", "deficien", "transtorno", "distúrbio",
                         "disturbio", "diagnóstic", "diagnostic", "laudo"):
            with self.subTest(proibida):
                self.assertNotIn(proibida, bruto)

    def test_e_ha_uma_ressalva_explicita(self):
        r = montar_relatorio(aluno_nome="Ana", conteudo="Estequiometria",
                             habilidades={}, apoios=[], agora=HOJE)
        self.assertTrue(r["ressalva"])
        self.assertIn("não", r["ressalva"].lower())


class ESCRITOPARAOALUNO(unittest.TestCase):

    def test_sem_jargao_do_sistema(self):
        from agente_ia_edu.services.consolidacao import ESTADO_RETIDO

        r = montar_relatorio(
            aluno_nome="Ana", conteudo="Estequiometria",
            habilidades={"MASSA_MOLAR": _situacao(ESTADO_RETIDO)},
            apoios=[_apoio("MASSA_MOLAR", dicas=1)], agora=HOJE)
        bruto = _texto_inteiro(r).lower()
        for jargao in ("band", "accuracy", "evidence_state", "mastery",
                       "threshold", "readiness", "solved_unaided"):
            with self.subTest(jargao):
                self.assertNotIn(jargao, bruto)

    def test_e_sem_percentual_artificial(self):
        """O §12 proíbe, e um papel que o aluno leva é onde isso mais pesa."""
        from agente_ia_edu.services.consolidacao import ESTADO_DEMONSTRADO

        r = montar_relatorio(
            aluno_nome="Ana", conteudo="Estequiometria",
            habilidades={"MASSA_MOLAR": _situacao(ESTADO_DEMONSTRADO)},
            apoios=[], agora=HOJE)
        self.assertNotIn("%", _texto_inteiro(r))


class CONCISO(unittest.TestCase):
    """O §17: "conciso, compreensível e baseado em evidências"."""

    def test_nenhuma_secao_vazia_e_impressa(self):
        r = montar_relatorio(
            aluno_nome="Ana", conteudo="Estequiometria", habilidades={},
            apoios=[], agora=HOJE)
        for s in r["secoes"]:
            with self.subTest(s["chave"]):
                self.assertTrue(s["itens"], "seção sem item nenhum foi impressa")


class NENHUMASECAOVIRAPAREDE(unittest.TestCase):
    """O §17 pede CONCISO, e o QA de 2026-10-08 mostrou o oposto.

    Com o histórico real de `aluno_qa_jornada_est`, "O que ainda está
    custando" saiu com SETE frases idênticas — uma por micro-habilidade — e
    "Para conversar com seu professor" repetiu as mesmas sete numa linha só.
    Isso não é um resumo: é uma lista de veredictos, e é o que faz um papel
    sobre aprendizagem parecer um laudo.

    O corte é honesto: os primeiros, e depois a CONTAGEM do que ficou de
    fora. Nada desaparece sem ser dito, e o quadro completo continua em
    "Meu progresso".
    """

    def _muitas(self, quantas: int, estado=None, revisao=False):
        from agente_ia_edu.services.consolidacao import ESTADO_EM_APRENDIZADO

        estado = estado or ESTADO_EM_APRENDIZADO
        return {f"HABILIDADE_{i:02d}": _situacao(estado, revisao=revisao)
                for i in range(quantas)}

    def _secao(self, relatorio, chave):
        return next(s for s in relatorio["secoes"] if s["chave"] == chave)

    def test_uma_secao_nao_passa_do_limite(self):
        from agente_ia_edu.services.relatorio_de_apoio import ITENS_POR_SECAO

        r = montar_relatorio(aluno_nome="Ana", conteudo="Estequiometria",
                             habilidades=self._muitas(9), apoios=[], agora=HOJE)
        for s in r["secoes"]:
            with self.subTest(s["chave"]):
                self.assertLessEqual(len(s["itens"]), ITENS_POR_SECAO + 1,
                                     "seção virou parede de texto")

    def test_e_o_que_ficou_de_fora_e_CONTADO_em_vez_de_sumir(self):
        from agente_ia_edu.services.relatorio_de_apoio import ITENS_POR_SECAO

        r = montar_relatorio(aluno_nome="Ana", conteudo="Estequiometria",
                             habilidades=self._muitas(9), apoios=[], agora=HOJE)
        dificuldades = self._secao(r, "dificuldades")
        ultimo = dificuldades["itens"][-1]
        self.assertIn(str(9 - ITENS_POR_SECAO), ultimo,
                      f"a sobra não foi contada: {ultimo!r}")

    def test_com_pouca_coisa_nao_ha_linha_de_sobra(self):
        """Dois itens não precisam de "e mais 0"."""
        r = montar_relatorio(aluno_nome="Ana", conteudo="Estequiometria",
                             habilidades=self._muitas(2), apoios=[], agora=HOJE)
        for item in self._secao(r, "dificuldades")["itens"]:
            with self.subTest(item):
                self.assertNotIn("e mais", item.lower())

    def test_o_que_ele_JA_MOSTROU_e_escorregou_vem_primeiro(self):
        """A ordem não é alfabética: é o que dá para agir.

        Quem demonstrou antes e errou agora tem recuperação possível. Quem
        nunca saiu sem ajuda precisa de ensino. A primeira informação é mais
        acionável, e é a que cabe nas quatro linhas que o papel tem.
        """
        from agente_ia_edu.services.consolidacao import (
            ESTADO_DEMONSTRADO,
            ESTADO_EM_APRENDIZADO,
        )

        habilidades = {f"ZZ_TRAVADA_{i}": _situacao(ESTADO_EM_APRENDIZADO)
                       for i in range(8)}
        habilidades["AA_ESCORREGOU"] = _situacao(ESTADO_DEMONSTRADO,
                                                 revisao=True)
        r = montar_relatorio(aluno_nome="Ana", conteudo="Estequiometria",
                             habilidades=habilidades, apoios=[], agora=HOJE)
        primeiro = self._secao(r, "dificuldades")["itens"][0]
        self.assertIn("ESCORREGOU", primeiro,
                      f"o que dá para recuperar ficou fora da lista: "
                      f"{primeiro!r}")

    def test_a_sugestao_nao_enumera_tudo_numa_linha_so(self):
        r = montar_relatorio(aluno_nome="Ana", conteudo="Estequiometria",
                             habilidades=self._muitas(9), apoios=[], agora=HOJE)
        for item in self._secao(r, "sugestoes")["itens"]:
            with self.subTest(item):
                self.assertLessEqual(item.count(","), 6,
                                     "a sugestão virou um inventário")

    def test_e_o_que_trabalhamos_tambem_nao(self):
        r = montar_relatorio(aluno_nome="Ana", conteudo="Estequiometria",
                             habilidades=self._muitas(12), apoios=[],
                             agora=HOJE)
        for item in self._secao(r, "trabalhado")["itens"]:
            with self.subTest(item):
                self.assertLessEqual(item.count(","), 8)

    def test_a_sobra_de_UM_nao_sai_em_plural_errado(self):
        """"e outros 1" foi o que saiu no QA. Concordância é parte do texto."""
        from agente_ia_edu.services.relatorio_de_apoio import (
            ITENS_POR_SECAO,
            NOMES_POR_LINHA,
        )

        # Uma sobra de exatamente um, nos dois cortes: o de itens e o de nomes.
        r = montar_relatorio(aluno_nome="Ana", conteudo="X",
                             habilidades=self._muitas(NOMES_POR_LINHA + 1),
                             apoios=[], agora=HOJE)
        inteiro = " ".join(i for s in r["secoes"] for i in s["itens"])
        self.assertIn("e mais 1", inteiro)
        self.assertNotIn("outros 1", inteiro)

        sobra_de_um = montar_relatorio(
            aluno_nome="Ana", conteudo="X",
            habilidades=self._muitas(ITENS_POR_SECAO + 1), apoios=[],
            agora=HOJE)
        dificuldades = " ".join(
            self._secao(sobra_de_um, "dificuldades")["itens"])
        self.assertIn("1 ponto", dificuldades)
        self.assertNotIn("1 pontos", dificuldades)

    def test_o_corte_e_deterministico(self):
        """Duas chamadas iguais dão o mesmo papel. Sem isto não é relatório."""
        habilidades = self._muitas(9)
        a = montar_relatorio(aluno_nome="Ana", conteudo="X",
                             habilidades=habilidades, apoios=[], agora=HOJE)
        b = montar_relatorio(aluno_nome="Ana", conteudo="X",
                             habilidades=habilidades, apoios=[], agora=HOJE)
        self.assertEqual(a["secoes"], b["secoes"])


def _texto_inteiro(relatorio: dict) -> str:
    partes = [relatorio.get("titulo", ""), relatorio.get("subtitulo", ""),
              relatorio.get("ressalva", "")]
    for s in relatorio.get("secoes", []):
        partes.append(s.get("titulo", ""))
        partes.extend(s.get("itens", []))
    return " ".join(partes)


if __name__ == "__main__":
    unittest.main()
