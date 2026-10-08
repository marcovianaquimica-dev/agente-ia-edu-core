"""ORIGEM DA EVIDENCIA - de onde veio cada resposta do aluno.

O mapa de dominio guarda, por conteudo, um ``origin_breakdown``: quantas
respostas vieram de atividade oficial da escola, quantas de pratica, e assim
por diante. Essa separacao e o que impede que estudo por conta propria seja
lido como desempenho escolar.

O PERIGO QUE ESTE ARQUIVO EXISTE PARA FECHAR
=============================================

``_Grain.add()`` resolvia um origin desconhecido assim:

    key = origin if origin in _KNOWN_ORIGINS else ORIGIN_OFFICIAL_ACTIVITY

Repare no que acontece quando alguem cria uma origem nova e esquece de
registra-la: o dado dizia explicitamente "nao sou atividade oficial", e o
codigo o convertia em atividade oficial. Em silencio. Sem log, sem erro.

Isso e exatamente o que um microdiagnostico nao pode fazer. Ele existe para
descobrir se o aluno esta pronto; se as respostas dele entrarem como
avaliacao oficial da escola, o Nucleo passa a reportar ao professor um
desempenho que a escola nunca mediu.

DOIS DEFAULTS DIFERENTES, SO UM E O PROBLEMA
=============================================

Ha outro default parecido, e ele e LEGITIMO:

    origin_by_assignment[aid] = (md or {}).get("origin") or ORIGIN_OFFICIAL_ACTIVITY

Esse trata assignment SEM a chave `origin` no metadata - toda atividade
distribuida por professor, e todas as anteriores a PHASE 22. Essas sao
atividade oficial de fato. Esse default fica, e ha teste abaixo que trava
isso, para que o endurecimento nao quebre o acervo existente.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.curriculum_domain_map import (
    ORIGIN_OFFICIAL_ACTIVITY,
    ORIGIN_PRACTICE,
    _Grain,
)

MICRO_DIAGNOSTIC = "MICRO_DIAGNOSTIC"


def _grao() -> _Grain:
    return _Grain(content_code="CHEMISTRY-PHYSICAL-STOICHIOMETRY", subcontent_code=None)


def _responde(grao: _Grain, origin: str, *, correta: bool = True) -> None:
    grao.add(answered=True, is_correct=correta, provisional=False,
             forced_closure=False, visual=False, at=None, origin=origin)


class OrigemDaEvidenciaTests(unittest.TestCase):

    # -- CASO E -------------------------------------------------------------

    def test_microdiagnostico_nao_e_contado_como_atividade_oficial(self):
        grao = _grao()
        _responde(grao, MICRO_DIAGNOSTIC)
        _responde(grao, MICRO_DIAGNOSTIC)
        _responde(grao, MICRO_DIAGNOSTIC)

        self.assertEqual(grao.origin.get(ORIGIN_OFFICIAL_ACTIVITY, 0), 0,
                         "respostas de microdiagnostico viraram avaliacao oficial "
                         "da escola - o professor passaria a ver um desempenho "
                         "que a escola nunca mediu")
        self.assertEqual(grao.origin.get(MICRO_DIAGNOSTIC), 3)

    def test_microdiagnostico_e_pratica_sao_baldes_distintos(self):
        grao = _grao()
        _responde(grao, MICRO_DIAGNOSTIC)
        _responde(grao, ORIGIN_PRACTICE)

        self.assertEqual(grao.origin.get(MICRO_DIAGNOSTIC), 1)
        self.assertEqual(grao.origin.get(ORIGIN_PRACTICE), 1)

    def test_a_evidencia_do_microdiagnostico_conta_para_o_total(self):
        """Separar origem nao e descartar: a resposta e evidencia valida."""
        grao = _grao()
        _responde(grao, MICRO_DIAGNOSTIC, correta=True)
        _responde(grao, MICRO_DIAGNOSTIC, correta=False)

        self.assertEqual(grao.answered, 2)
        self.assertEqual(grao.correct, 1)
        self.assertEqual(grao.accuracy(), 0.5)

    # -- CASO F -------------------------------------------------------------

    def test_origin_desconhecido_nao_contamina_atividade_oficial(self):
        grao = _grao()
        _responde(grao, "ORIGEM_QUE_NINGUEM_REGISTROU")

        self.assertEqual(grao.origin.get(ORIGIN_OFFICIAL_ACTIVITY, 0), 0,
                         "um origin que o codigo nao reconhece foi convertido em "
                         "atividade oficial - o dado dizia o contrario")

    def test_origin_desconhecido_fica_visivel_em_quarentena(self):
        """Fail-closed nao e fail-silent: o dado estranho tem de aparecer.

        Se ele sumisse, a soma dos baldes deixaria de bater com `answered` e
        ninguem notaria que ha evidencia de procedencia duvidosa no acervo.
        """
        from agente_ia_edu.services.curriculum_domain_map import ORIGIN_UNKNOWN

        grao = _grao()
        _responde(grao, "ORIGEM_QUE_NINGUEM_REGISTROU")
        _responde(grao, "OUTRA_ORIGEM_ESTRANHA")

        self.assertEqual(grao.origin.get(ORIGIN_UNKNOWN), 2)
        self.assertEqual(sum(grao.origin.values()), grao.answered,
                         "os baldes de origem nao somam o total de respostas")

    def test_origin_vazio_ou_None_tambem_nao_vira_oficial_silenciosamente(self):
        from agente_ia_edu.services.curriculum_domain_map import ORIGIN_UNKNOWN

        for ruim in ("", "   ", None):
            grao = _grao()
            _responde(grao, ruim)
            self.assertEqual(grao.origin.get(ORIGIN_OFFICIAL_ACTIVITY, 0), 0,
                             f"origin {ruim!r} virou atividade oficial")
            self.assertEqual(grao.origin.get(ORIGIN_UNKNOWN), 1)

    # -- o default LEGITIMO, que nao pode quebrar ---------------------------

    def test_quem_nao_passa_origin_continua_sendo_atividade_oficial(self):
        """Compatibilidade: assignment sem `origin` no metadata E oficial.

        Toda atividade distribuida por professor cai aqui, e todas as
        anteriores a PHASE 22. Endurecer o origin DESCONHECIDO nao pode
        reclassificar o acervo existente.
        """
        grao = _grao()
        grao.add(answered=True, is_correct=True, provisional=False,
                 forced_closure=False, visual=False, at=None)   # sem origin=

        self.assertEqual(grao.origin.get(ORIGIN_OFFICIAL_ACTIVITY), 1)

    def test_os_origins_ja_registrados_continuam_funcionando(self):
        for conhecido in (ORIGIN_OFFICIAL_ACTIVITY, ORIGIN_PRACTICE,
                          "INITIAL_DIAGNOSTIC", "SIMULADO"):
            grao = _grao()
            _responde(grao, conhecido)
            self.assertEqual(grao.origin.get(conhecido), 1,
                             f"origin {conhecido} deixou de ser reconhecido")

    # -- o contrato ---------------------------------------------------------

    def test_o_microdiagnostico_tem_constante_propria_e_esta_registrado(self):
        from agente_ia_edu.services.curriculum_domain_map import (
            ORIGIN_MICRO_DIAGNOSTIC,
            _KNOWN_ORIGINS,
        )

        self.assertEqual(ORIGIN_MICRO_DIAGNOSTIC, MICRO_DIAGNOSTIC)
        self.assertIn(ORIGIN_MICRO_DIAGNOSTIC, _KNOWN_ORIGINS)

    def test_a_quarentena_nao_entra_na_lista_de_origens_validas(self):
        """ORIGIN_UNKNOWN e um diagnostico do sistema, nao uma origem que
        alguem possa declarar. Se entrasse em _KNOWN_ORIGINS, passaria a ser
        aceita como se fosse legitima."""
        from agente_ia_edu.services.curriculum_domain_map import (
            ORIGIN_UNKNOWN,
            _KNOWN_ORIGINS,
        )

        self.assertNotIn(ORIGIN_UNKNOWN, _KNOWN_ORIGINS)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
