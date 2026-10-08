# tests/test_mass_correction_batch_scoring.py
"""Task 5: estagio 3 (pontuacao fase 2) da correcao em massa - montagem de
lote e aplicacao de resultado. Reaproveita a mesma fixture de
output_dict/rubric_file que tests/test_r3_essay_correction_service.py usa
para _score_competencies_from_evidence/_review_anula_redacao_alerts
(_happy_payload + load_rubric_file("enem_2025")), para garantir que o
formato exercitado aqui e o MESMO formato real que o caminho sincrono
produz (EssayCorrection.ai_output), nao um formato inventado.
"""
import json
import unittest
import uuid

from agente_ia_edu.essay_engine_contract.v5 import EssayEngineOutput
from agente_ia_edu.rubrics.loader import load_rubric_file
from agente_ia_edu.services.mass_correction_batch import (
    apply_scoring_batch_results,
    build_scoring_batch_requests,
)

from test_r3_essay_correction_service import _happy_payload

RUBRIC_FILE = load_rubric_file("enem_2025")
ESSAY_STATEMENT = "Disserte sobre X."


def _output_dict(**kwargs) -> dict:
    """Builds the SAME shape EssayCorrection.ai_output stores in production:
    a validated EssayEngineOutput, re-serialized with model_dump(mode="json").
    _happy_payload is test_r3's own fixture for a phase-1 AVALIATIVO payload;
    only "identification" is added here (never part of _happy_payload,
    since real code always overwrites it with the service's own values -
    see essay_correction.py's _run_ai)."""
    raw = json.loads(
        _happy_payload(
            anchor_mode="TEXT_OFFSET", text="Texto qualquer.",
            # This pipeline is still pinned to contract v5 (see this
            # module's own docstring and mass_correction_batch.py's) -
            # v5 has no input_reliability field and is extra="forbid", so
            # the Task 8/Fase B default on _happy_payload() must be
            # omitted here, not just left unused.
            input_reliability=None,
            **kwargs,
        )
    )
    raw["identification"] = {
        "essay_id": str(uuid.uuid4()),
        "essay_version_id": str(uuid.uuid4()),
        "rubric_version": RUBRIC_FILE.rubric_version,
        "model_version": "gpt-test",
        "prompt_version": "essay_correction_v15",
        "engine_version": "r3_correction_engine_v2",
        "contract_version": "essay_engine_output_v5",
        "anchor_mode": "TEXT_OFFSET",
    }
    output = EssayEngineOutput.model_validate(raw)
    return output.model_dump(mode="json")


def _competency_result_line(correction_id: str, code: str, *, points: int) -> dict:
    return {
        "custom_id": f"{correction_id}:{code}",
        "response": {
            "status_code": 200,
            "body": {
                "choices": [
                    {"message": {"content": json.dumps({"points": points, "reasoning": "stub"})}}
                ]
            },
        },
    }


def _alert_result_line(correction_id: str, *, confirmed_alert_codes: list) -> dict:
    return {
        "custom_id": f"{correction_id}:alert",
        "response": {
            "status_code": 200,
            "body": {
                "choices": [
                    {
                        "message": {
                            "content": json.dumps(
                                {"confirmed_alert_codes": confirmed_alert_codes, "reasoning": "stub"}
                            )
                        }
                    }
                ]
            },
        },
    }


class BuildScoringBatchRequestsTests(unittest.TestCase):
    def test_builds_six_lines_one_per_competency_plus_one_alert_review(self):
        correction_id = str(uuid.uuid4())
        lines = build_scoring_batch_requests(
            correction_id, output_dict=_output_dict(), rubric_file=RUBRIC_FILE,
            essay_statement=ESSAY_STATEMENT,
        )
        custom_ids = {line["custom_id"] for line in lines}
        self.assertEqual(
            custom_ids,
            {f"{correction_id}:{code}" for code in ("C1", "C2", "C3", "C4", "C5")}
            | {f"{correction_id}:alert"},
        )

    def test_every_line_has_the_batch_request_shape(self):
        correction_id = str(uuid.uuid4())
        lines = build_scoring_batch_requests(
            correction_id, output_dict=_output_dict(), rubric_file=RUBRIC_FILE,
            essay_statement=ESSAY_STATEMENT,
        )
        for line in lines:
            self.assertEqual(line["method"], "POST")
            self.assertEqual(line["url"], "/v1/chat/completions")
            self.assertIsNone(line["body"]["model"])
            self.assertEqual(len(line["body"]["messages"]), 1)
            self.assertEqual(line["body"]["messages"][0]["role"], "user")

    def test_competency_lines_carry_the_scoring_rules_marker_and_own_code(self):
        correction_id = str(uuid.uuid4())
        lines = build_scoring_batch_requests(
            correction_id, output_dict=_output_dict(), rubric_file=RUBRIC_FILE,
            essay_statement=ESSAY_STATEMENT,
        )
        by_custom_id = {line["custom_id"]: line for line in lines}
        for code in ("C1", "C2", "C3", "C4", "C5"):
            prompt = by_custom_id[f"{correction_id}:{code}"]["body"]["messages"][0]["content"]
            self.assertIn("SCORING_RULES:", prompt)
            self.assertIn(f"RUBRIC_{code}", prompt)

    def test_mechanical_review_only_sent_on_c1_line(self):
        correction_id = str(uuid.uuid4())
        output_dict = _output_dict()
        # Give the payload a mechanical_review occurrence so we can check it
        # only reaches C1's own batch line, mirroring
        # test_phase2_mechanical_review_only_sent_for_c1 in the sync suite.
        output_dict["mechanical_review"] = [
            {
                "category": "CRASE", "excerpt": "a partir a de agora",
                "suggested_form": "a partir de agora", "rule_explanation": "sem crase antes de artigo",
            }
        ]
        lines = build_scoring_batch_requests(
            correction_id, output_dict=output_dict, rubric_file=RUBRIC_FILE,
            essay_statement=ESSAY_STATEMENT,
        )
        by_custom_id = {line["custom_id"]: line for line in lines}
        c1_prompt = by_custom_id[f"{correction_id}:C1"]["body"]["messages"][0]["content"]
        c2_prompt = by_custom_id[f"{correction_id}:C2"]["body"]["messages"][0]["content"]
        self.assertIn("CRASE", c1_prompt)
        self.assertNotIn("ocorrencias mecanicas confirmadas", c2_prompt)

    def test_alert_line_carries_only_anula_redacao_candidates_and_the_statement(self):
        correction_id = str(uuid.uuid4())
        output_dict = _output_dict(
            alerts=[
                {"code": "FUGA_AO_TEMA", "detail": "Nao desenvolveu o tema."},
                {"code": "TANGENCIAMENTO_AO_TEMA", "detail": "So aborda o assunto amplo."},
            ]
        )
        lines = build_scoring_batch_requests(
            correction_id, output_dict=output_dict, rubric_file=RUBRIC_FILE,
            essay_statement=ESSAY_STATEMENT,
        )
        by_custom_id = {line["custom_id"]: line for line in lines}
        alert_prompt = by_custom_id[f"{correction_id}:alert"]["body"]["messages"][0]["content"]
        self.assertIn("ALERTS_TO_REVIEW:", alert_prompt)
        self.assertIn("FUGA_AO_TEMA", alert_prompt)
        # TANGENCIAMENTO_AO_TEMA is not an ANULA_REDACAO candidate - never
        # sent to the alert-review prompt (see essay_correction.py's
        # _review_anula_redacao_alerts docstring).
        self.assertNotIn("TANGENCIAMENTO_AO_TEMA", alert_prompt)
        self.assertIn(ESSAY_STATEMENT, alert_prompt)

    def test_alert_line_is_still_built_when_there_are_no_candidates(self):
        """Unlike the sync path (which skips the provider call entirely when
        there is no ANULA_REDACAO candidate), the batch builder always
        produces exactly 6 lines - a fixed line count per correction keeps
        the batch tracking table's own bookkeeping free of a per-correction
        special case."""
        correction_id = str(uuid.uuid4())
        lines = build_scoring_batch_requests(
            correction_id, output_dict=_output_dict(), rubric_file=RUBRIC_FILE,
            essay_statement=ESSAY_STATEMENT,
        )
        self.assertEqual(len(lines), 6)


class ApplyScoringBatchResultsTests(unittest.TestCase):
    def test_combines_all_six_results_into_final_scores(self):
        correction_id = str(uuid.uuid4())
        output_dict = _output_dict()
        points = {"C1": 120, "C2": 160, "C3": 80, "C4": 200, "C5": 40}
        result_lines_by_custom_id = {
            f"{correction_id}:{code}": _competency_result_line(correction_id, code, points=p)
            for code, p in points.items()
        }
        result_lines_by_custom_id[f"{correction_id}:alert"] = _alert_result_line(
            correction_id, confirmed_alert_codes=[]
        )

        result = apply_scoring_batch_results(
            correction_id, result_lines_by_custom_id,
            output_dict=output_dict, rubric_file=RUBRIC_FILE,
        )

        self.assertIn("final_scores", result)
        final_scores = result["final_scores"]
        self.assertEqual(set(final_scores["per_competency"]), {"C1", "C2", "C3", "C4", "C5"})
        for code, p in points.items():
            self.assertEqual(final_scores["per_competency"][code]["points"], p)
        self.assertEqual(final_scores["total"], sum(points.values()))

    def test_confirmed_anula_redacao_alert_zeroes_final_scores(self):
        correction_id = str(uuid.uuid4())
        output_dict = _output_dict(
            alerts=[{"code": "FUGA_AO_TEMA", "detail": "Nao desenvolveu o tema."}]
        )
        result_lines_by_custom_id = {
            f"{correction_id}:{code}": _competency_result_line(correction_id, code, points=160)
            for code in ("C1", "C2", "C3", "C4", "C5")
        }
        result_lines_by_custom_id[f"{correction_id}:alert"] = _alert_result_line(
            correction_id, confirmed_alert_codes=["FUGA_AO_TEMA"]
        )

        result = apply_scoring_batch_results(
            correction_id, result_lines_by_custom_id,
            output_dict=output_dict, rubric_file=RUBRIC_FILE,
        )

        self.assertEqual(result["final_scores"]["total"], 0)
        for code in ("C1", "C2", "C3", "C4", "C5"):
            self.assertEqual(result["final_scores"]["per_competency"][code]["points"], 0)

    def test_rejected_anula_redacao_alert_does_not_zero_final_scores(self):
        """The alert-review call may REJECT a phase-1 candidate (empty
        confirmed_alert_codes) - final_scores must then use phase2_points
        untouched, exactly like the sync path's
        test_alert_review_can_reject_a_false_positive_anula_redacao_alert."""
        correction_id = str(uuid.uuid4())
        output_dict = _output_dict(
            alerts=[{"code": "FUGA_AO_TEMA", "detail": "Nao desenvolveu o tema."}]
        )
        result_lines_by_custom_id = {
            f"{correction_id}:{code}": _competency_result_line(correction_id, code, points=160)
            for code in ("C1", "C2", "C3", "C4", "C5")
        }
        result_lines_by_custom_id[f"{correction_id}:alert"] = _alert_result_line(
            correction_id, confirmed_alert_codes=[]
        )

        result = apply_scoring_batch_results(
            correction_id, result_lines_by_custom_id,
            output_dict=output_dict, rubric_file=RUBRIC_FILE,
        )

        self.assertEqual(result["final_scores"]["total"], 800)

    def test_tangenciamento_alert_passes_through_without_being_a_candidate(self):
        """TANGENCIAMENTO_AO_TEMA is never sent to the alert-review call (see
        build_scoring_batch_requests), but must still reach
        _apply_deterministic_scoring_rules as a passthrough code, capping
        C3/C5 at 40 - mirrors the sync suite's
        test_alert_review_non_anula_redacao_alerts_pass_through_without_a_call."""
        correction_id = str(uuid.uuid4())
        output_dict = _output_dict(
            alerts=[{"code": "TANGENCIAMENTO_AO_TEMA", "detail": "So aborda o assunto amplo."}],
            per_competency_points={"C1": 160, "C2": 80, "C3": 200, "C4": 160, "C5": 200},
        )
        points = {"C1": 160, "C2": 80, "C3": 200, "C4": 160, "C5": 200}
        result_lines_by_custom_id = {
            f"{correction_id}:{code}": _competency_result_line(correction_id, code, points=p)
            for code, p in points.items()
        }
        result_lines_by_custom_id[f"{correction_id}:alert"] = _alert_result_line(
            correction_id, confirmed_alert_codes=[]
        )

        result = apply_scoring_batch_results(
            correction_id, result_lines_by_custom_id,
            output_dict=output_dict, rubric_file=RUBRIC_FILE,
        )

        self.assertEqual(result["final_scores"]["per_competency"]["C3"]["points"], 40)
        self.assertEqual(result["final_scores"]["per_competency"]["C5"]["points"], 40)
        self.assertEqual(result["final_scores"]["per_competency"]["C2"]["points"], 80)

    def test_invalid_points_value_raises_value_error(self):
        correction_id = str(uuid.uuid4())
        output_dict = _output_dict()
        result_lines_by_custom_id = {
            f"{correction_id}:{code}": _competency_result_line(correction_id, code, points=160)
            for code in ("C1", "C2", "C3", "C4", "C5")
        }
        result_lines_by_custom_id[f"{correction_id}:C1"] = _competency_result_line(
            correction_id, "C1", points=77
        )
        result_lines_by_custom_id[f"{correction_id}:alert"] = _alert_result_line(
            correction_id, confirmed_alert_codes=[]
        )

        with self.assertRaises(ValueError):
            apply_scoring_batch_results(
                correction_id, result_lines_by_custom_id,
                output_dict=output_dict, rubric_file=RUBRIC_FILE,
            )

    def test_batch_error_on_a_line_raises_value_error(self):
        correction_id = str(uuid.uuid4())
        output_dict = _output_dict()
        result_lines_by_custom_id = {
            f"{correction_id}:{code}": _competency_result_line(correction_id, code, points=160)
            for code in ("C1", "C2", "C3", "C4", "C5")
        }
        result_lines_by_custom_id[f"{correction_id}:alert"] = {
            "custom_id": f"{correction_id}:alert",
            "error": {"message": "boom"},
        }

        with self.assertRaises(ValueError):
            apply_scoring_batch_results(
                correction_id, result_lines_by_custom_id,
                output_dict=output_dict, rubric_file=RUBRIC_FILE,
            )

    def test_missing_competency_result_line_raises_value_error_not_key_error(self):
        """C3 do relatorio final: um custom_id que nao veio no arquivo de
        resultado (ex: a requisicao individual falhou na API e caiu no
        arquivo de erro, nao no de resultado) levantava KeyError sem
        tratamento - deve virar ValueError, como o docstring de
        apply_scoring_batch_results ja promete."""
        correction_id = str(uuid.uuid4())
        output_dict = _output_dict()
        result_lines_by_custom_id = {
            f"{correction_id}:{code}": _competency_result_line(correction_id, code, points=160)
            for code in ("C1", "C2", "C4", "C5")  # falta C3 de proposito
        }
        result_lines_by_custom_id[f"{correction_id}:alert"] = _alert_result_line(
            correction_id, confirmed_alert_codes=[]
        )

        with self.assertRaises(ValueError):
            apply_scoring_batch_results(
                correction_id, result_lines_by_custom_id,
                output_dict=output_dict, rubric_file=RUBRIC_FILE,
            )

    def test_missing_alert_result_line_raises_value_error_not_key_error(self):
        correction_id = str(uuid.uuid4())
        output_dict = _output_dict()
        result_lines_by_custom_id = {
            f"{correction_id}:{code}": _competency_result_line(correction_id, code, points=160)
            for code in ("C1", "C2", "C3", "C4", "C5")
        }
        # falta de proposito a entrada f"{correction_id}:alert"

        with self.assertRaises(ValueError):
            apply_scoring_batch_results(
                correction_id, result_lines_by_custom_id,
                output_dict=output_dict, rubric_file=RUBRIC_FILE,
            )

    def test_competency_payload_missing_points_key_raises_value_error_not_key_error(self):
        correction_id = str(uuid.uuid4())
        output_dict = _output_dict()
        result_lines_by_custom_id = {
            f"{correction_id}:{code}": _competency_result_line(correction_id, code, points=160)
            for code in ("C1", "C2", "C4", "C5")
        }
        # C3 vem no arquivo de resultado, mas o JSON devolvido pelo modelo
        # nao tem a chave "points" (ex: o modelo so devolveu "reasoning").
        result_lines_by_custom_id[f"{correction_id}:C3"] = {
            "custom_id": f"{correction_id}:C3",
            "response": {
                "status_code": 200,
                "body": {"choices": [{"message": {"content": json.dumps({"reasoning": "stub"})}}]},
            },
        }
        result_lines_by_custom_id[f"{correction_id}:alert"] = _alert_result_line(
            correction_id, confirmed_alert_codes=[]
        )

        with self.assertRaises(ValueError):
            apply_scoring_batch_results(
                correction_id, result_lines_by_custom_id,
                output_dict=output_dict, rubric_file=RUBRIC_FILE,
            )

    def test_null_content_raises_value_error_not_type_error(self):
        """Mesmo achado C4 do relatorio final, agora no estagio SCORING:
        content=None (recusa ou resposta vazia do modelo) tem que virar
        ValueError logo em _scoring_result_content (via
        _extract_message_content), nunca um TypeError sem tratamento em
        json.loads(None)."""
        correction_id = str(uuid.uuid4())
        output_dict = _output_dict()
        result_lines_by_custom_id = {
            f"{correction_id}:{code}": _competency_result_line(correction_id, code, points=160)
            for code in ("C1", "C2", "C4", "C5")
        }
        result_lines_by_custom_id[f"{correction_id}:C3"] = {
            "custom_id": f"{correction_id}:C3",
            "response": {
                "status_code": 200,
                "body": {"choices": [{"message": {"content": None}}]},
            },
        }
        result_lines_by_custom_id[f"{correction_id}:alert"] = _alert_result_line(
            correction_id, confirmed_alert_codes=[]
        )

        with self.assertRaises(ValueError):
            apply_scoring_batch_results(
                correction_id, result_lines_by_custom_id,
                output_dict=output_dict, rubric_file=RUBRIC_FILE,
            )

    def test_alert_payload_missing_confirmed_alert_codes_key_raises_value_error_not_key_error(self):
        correction_id = str(uuid.uuid4())
        output_dict = _output_dict()
        result_lines_by_custom_id = {
            f"{correction_id}:{code}": _competency_result_line(correction_id, code, points=160)
            for code in ("C1", "C2", "C3", "C4", "C5")
        }
        result_lines_by_custom_id[f"{correction_id}:alert"] = {
            "custom_id": f"{correction_id}:alert",
            "response": {
                "status_code": 200,
                "body": {"choices": [{"message": {"content": json.dumps({"reasoning": "stub"})}}]},
            },
        }

        with self.assertRaises(ValueError):
            apply_scoring_batch_results(
                correction_id, result_lines_by_custom_id,
                output_dict=output_dict, rubric_file=RUBRIC_FILE,
            )


if __name__ == "__main__":
    unittest.main()
