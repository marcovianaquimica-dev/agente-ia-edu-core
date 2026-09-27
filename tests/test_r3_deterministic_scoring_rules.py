"""Unit tests for essay_correction._apply_deterministic_scoring_rules - the
ENEM 2025 rubric's own normative scoring_rules (rubrics/enem_2025.yaml),
enforced deterministically instead of trusted to the model's own arithmetic.
Pure function, no DB/provider needed - see essay_correction.py's docstring
on the function and essay_engine_contract v3's module docstring for why."""
import unittest
import uuid

from agente_ia_edu.essay_engine_contract.v4 import CONTRACT_VERSION, EssayEngineOutput
from agente_ia_edu.services.essay_correction import _apply_deterministic_scoring_rules


def _payload(*, alerts=(), respeita_direitos_humanos=True, points=None):
    points = points or {c: 160 for c in ("C1", "C2", "C3", "C4", "C5")}
    total = sum(points.values())
    return {
        "identification": {
            "essay_id": str(uuid.uuid4()), "essay_version_id": str(uuid.uuid4()),
            "rubric_version": "ENEM_2025", "model_version": "fake-model-1",
            "prompt_version": "v1", "engine_version": "r1.0.0",
            "contract_version": CONTRACT_VERSION, "anchor_mode": "TEXT_OFFSET",
        },
        "scores": {
            "per_competency": {c: {"points": points[c], "confidence": 0.9} for c in points},
            "total": total,
        },
        "rationales": [
            {"competency_code": c, "summary": "r", "strengths": "s", "growth_area": "g",
             "signal_keys": []}
            for c in ("C1", "C2", "C3", "C4", "C5")
        ],
        "annotations": [],
        "rewrites": [],
        "feedback": {"strengths": [], "improvements": [], "next_essay_strategy": "..."},
        "intervention": {
            "agente": "a", "acao": "a", "meio_modo": "a", "finalidade": "a",
            "detalhamento": "a", "respeita_direitos_humanos": respeita_direitos_humanos,
        },
        "alerts": [{"code": code, "detail": None} for code in alerts],
        "intro_message": "Olá.",
        "closing_message": "Continue.",
    }


def _output(**kwargs) -> EssayEngineOutput:
    return EssayEngineOutput.model_validate(_payload(**kwargs))


class DeterministicScoringRulesTests(unittest.TestCase):
    def test_returns_none_when_scores_is_none(self):
        payload = _payload()
        payload["scores"] = None
        output = EssayEngineOutput.model_validate(payload)
        self.assertIsNone(_apply_deterministic_scoring_rules(output))

    def test_no_alerts_and_respects_rights_leaves_scores_untouched(self):
        output = _output(points={"C1": 200, "C2": 160, "C3": 120, "C4": 80, "C5": 40})
        result = _apply_deterministic_scoring_rules(output)
        self.assertEqual(result["per_competency"]["C1"]["points"], 200)
        self.assertEqual(result["per_competency"]["C2"]["points"], 160)
        self.assertEqual(result["per_competency"]["C3"]["points"], 120)
        self.assertEqual(result["per_competency"]["C4"]["points"], 80)
        self.assertEqual(result["per_competency"]["C5"]["points"], 40)
        self.assertEqual(result["total"], 600)

    def test_fuga_ao_tema_zeroes_the_whole_essay(self):
        """Cartilha p. 9: fuga total ao tema is ANULA_REDACAO - a whole-essay
        zero, regardless of how the model scored the five competencies."""
        output = _output(
            alerts=["FUGA_AO_TEMA"],
            points={"C1": 200, "C2": 200, "C3": 200, "C4": 200, "C5": 200},
        )
        result = _apply_deterministic_scoring_rules(output)
        for code in ("C1", "C2", "C3", "C4", "C5"):
            self.assertEqual(result["per_competency"][code]["points"], 0)
        self.assertEqual(result["total"], 0)

    def test_tipo_textual_predominante_zeroes_the_whole_essay(self):
        """Cartilha p. 28: predominância de outro tipo textual is ANULA_REDACAO."""
        output = _output(alerts=["TIPO_TEXTUAL_PREDOMINANTE"])
        result = _apply_deterministic_scoring_rules(output)
        self.assertEqual(result["total"], 0)

    def test_texto_insuficiente_zeroes_the_whole_essay(self):
        """Cartilha p. 9: texto insuficiente (≤7 linhas manuscritas) is
        ANULA_REDACAO."""
        output = _output(alerts=["TEXTO_INSUFICIENTE"])
        result = _apply_deterministic_scoring_rules(output)
        self.assertEqual(result["total"], 0)

    def test_plain_tipo_textual_alert_does_not_zero(self):
        """TIPO_TEXTUAL (some characteristics of another type, still
        predominantly dissertative) is NOT a zero condition - the cartilha
        says this is "penalizada na Competência II" through C2's own
        descriptor, which the model already sees and should reflect in its
        own C2 score."""
        output = _output(alerts=["TIPO_TEXTUAL"], points={c: 160 for c in "C1 C2 C3 C4 C5".split()})
        result = _apply_deterministic_scoring_rules(output)
        self.assertEqual(result["total"], 800)

    def test_tangenciamento_caps_c3_and_c5_at_40(self):
        """Cartilha p. 27, quadro ATENCAO!: tangenciamento caps III and V at
        40 points each - C2 is NOT touched here (its own descriptor already
        covers tangenciamento, so the model's C2 score is trusted as-is)."""
        output = _output(
            alerts=["TANGENCIAMENTO_AO_TEMA"],
            points={"C1": 160, "C2": 80, "C3": 200, "C4": 160, "C5": 200},
        )
        result = _apply_deterministic_scoring_rules(output)
        self.assertEqual(result["per_competency"]["C1"]["points"], 160)
        self.assertEqual(result["per_competency"]["C2"]["points"], 80)
        self.assertEqual(result["per_competency"]["C3"]["points"], 40)
        self.assertEqual(result["per_competency"]["C4"]["points"], 160)
        self.assertEqual(result["per_competency"]["C5"]["points"], 40)
        self.assertEqual(result["total"], 160 + 80 + 40 + 160 + 40)

    def test_tangenciamento_never_raises_an_already_lower_score(self):
        """The cap is a ceiling, not a floor - a model that already scored
        C3/C5 below 40 must not have those points raised to 40."""
        output = _output(
            alerts=["TANGENCIAMENTO_AO_TEMA"],
            points={"C1": 160, "C2": 80, "C3": 0, "C4": 160, "C5": 0},
        )
        result = _apply_deterministic_scoring_rules(output)
        self.assertEqual(result["per_competency"]["C3"]["points"], 0)
        self.assertEqual(result["per_competency"]["C5"]["points"], 0)

    def test_disrespecting_human_rights_zeroes_only_c5(self):
        """Cartilha p. 39, quadro ATENCAO!: this zeroes Competência V alone,
        never the whole essay."""
        output = _output(
            respeita_direitos_humanos=False,
            points={"C1": 200, "C2": 200, "C3": 200, "C4": 200, "C5": 200},
        )
        result = _apply_deterministic_scoring_rules(output)
        self.assertEqual(result["per_competency"]["C1"]["points"], 200)
        self.assertEqual(result["per_competency"]["C2"]["points"], 200)
        self.assertEqual(result["per_competency"]["C3"]["points"], 200)
        self.assertEqual(result["per_competency"]["C4"]["points"], 200)
        self.assertEqual(result["per_competency"]["C5"]["points"], 0)
        self.assertEqual(result["total"], 800)

    def test_human_rights_violation_combined_with_tangenciamento(self):
        """Both rules can fire together: tangenciamento caps C5 at 40, then
        the human-rights violation drops it further to 0."""
        output = _output(
            alerts=["TANGENCIAMENTO_AO_TEMA"], respeita_direitos_humanos=False,
            points={"C1": 160, "C2": 80, "C3": 160, "C4": 160, "C5": 200},
        )
        result = _apply_deterministic_scoring_rules(output)
        self.assertEqual(result["per_competency"]["C3"]["points"], 40)
        self.assertEqual(result["per_competency"]["C5"]["points"], 0)

    def test_anula_redacao_takes_precedence_over_human_rights_flag(self):
        """A whole-essay zero already implies C5=0 - the human-rights branch
        is skipped, not applied redundantly, when an ANULA_REDACAO alert
        already fired."""
        output = _output(
            alerts=["FUGA_AO_TEMA"], respeita_direitos_humanos=False,
            points={c: 200 for c in ("C1", "C2", "C3", "C4", "C5")},
        )
        result = _apply_deterministic_scoring_rules(output)
        self.assertEqual(result["total"], 0)

    def test_confidence_values_are_preserved(self):
        """Only points/total are ever adjusted - the model's own confidence
        readings must survive untouched, they're informational, not a score."""
        payload = _payload(alerts=["TANGENCIAMENTO_AO_TEMA"])
        payload["scores"]["per_competency"]["C3"]["confidence"] = 0.42
        output = EssayEngineOutput.model_validate(payload)
        result = _apply_deterministic_scoring_rules(output)
        self.assertEqual(result["per_competency"]["C3"]["confidence"], 0.42)

    def test_anulacao_proposital_zeroes_the_whole_essay(self):
        """Cartilha p. 9: impropérios, desenhos e outras formas propositais
        de anulação is ANULA_REDACAO."""
        output = _output(alerts=["ANULACAO_PROPOSITAL"],
                          points={c: 200 for c in ("C1", "C2", "C3", "C4", "C5")})
        result = _apply_deterministic_scoring_rules(output)
        self.assertEqual(result["total"], 0)

    def test_parte_desconectada_do_tema_zeroes_the_whole_essay(self):
        """Cartilha p. 9-10: reflexões sobre a prova, bilhetes à banca,
        mensagens políticas/religiosas ou frases sem relação com o tema."""
        output = _output(alerts=["PARTE_DESCONECTADA_DO_TEMA"],
                          points={c: 160 for c in ("C1", "C2", "C3", "C4", "C5")})
        result = _apply_deterministic_scoring_rules(output)
        self.assertEqual(result["total"], 0)

    def test_identificacao_indevida_zeroes_the_whole_essay(self):
        """Cartilha p. 9: nome, assinatura ou rubrica fora do espaço
        destinado, em qualquer parte da folha de redação."""
        output = _output(alerts=["IDENTIFICACAO_INDEVIDA"],
                          points={c: 200 for c in ("C1", "C2", "C3", "C4", "C5")})
        result = _apply_deterministic_scoring_rules(output)
        self.assertEqual(result["total"], 0)

    def test_lingua_estrangeira_zeroes_the_whole_essay(self):
        """Cartilha p. 10: texto predominante ou integralmente em língua
        estrangeira."""
        output = _output(alerts=["LINGUA_ESTRANGEIRA"],
                          points={c: 200 for c in ("C1", "C2", "C3", "C4", "C5")})
        result = _apply_deterministic_scoring_rules(output)
        self.assertEqual(result["total"], 0)

    def test_texto_ilegivel_zeroes_the_whole_essay(self):
        """Cartilha p. 10: texto que impossibilita a leitura por
        avaliadores independentes - distinct from OCR_DUVIDOSO, which has
        no scoring effect at all (see test below)."""
        output = _output(alerts=["TEXTO_ILEGIVEL"],
                          points={c: 200 for c in ("C1", "C2", "C3", "C4", "C5")})
        result = _apply_deterministic_scoring_rules(output)
        self.assertEqual(result["total"], 0)

    def test_ocr_duvidoso_and_possivel_duplicidade_never_zero(self):
        """Both are informational-only flags for a human reviewer - neither
        is an official INEP scoring rule, so neither has any automatic
        scoring consequence."""
        for code in ("OCR_DUVIDOSO", "POSSIVEL_DUPLICIDADE"):
            with self.subTest(code=code):
                output = _output(alerts=[code],
                                  points={c: 160 for c in ("C1", "C2", "C3", "C4", "C5")})
                result = _apply_deterministic_scoring_rules(output)
                self.assertEqual(result["total"], 800)


if __name__ == "__main__":
    unittest.main()
