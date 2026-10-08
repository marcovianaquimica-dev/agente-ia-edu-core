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


def _texto_inteiro(relatorio: dict) -> str:
    partes = [relatorio.get("titulo", ""), relatorio.get("subtitulo", ""),
              relatorio.get("ressalva", "")]
    for s in relatorio.get("secoes", []):
        partes.append(s.get("titulo", ""))
        partes.extend(s.get("itens", []))
    return " ".join(partes)


if __name__ == "__main__":
    unittest.main()
