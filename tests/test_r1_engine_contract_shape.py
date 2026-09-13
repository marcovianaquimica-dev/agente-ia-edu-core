import unittest
import uuid

from pydantic import ValidationError

from agente_ia_edu.essay_engine_contract.v1 import CONTRACT_VERSION, EssayEngineOutput


def minimal_payload(**overrides):
    payload = {
        "identification": {
            "essay_id": str(uuid.uuid4()),
            "essay_version_id": str(uuid.uuid4()),
            "rubric_version": "ENEM_2025",
            "model_version": "fake-model-1",
            "prompt_version": "v1",
            "engine_version": "r1.0.0",
            "contract_version": CONTRACT_VERSION,
            "anchor_mode": "TEXT_OFFSET",
        },
        "scores": {
            "per_competency": {
                code: {"points": 160, "confidence": 0.8}
                for code in ("C1", "C2", "C3", "C4", "C5")
            },
            "total": 800,
        },
        "rationales": [
            {"competency_code": c, "summary": "resumo", "signal_keys": []}
            for c in ("C1", "C2", "C3", "C4", "C5")
        ],
        "annotations": [
            {
                "letter": "A",
                "competency_code": "C1",
                "kind": "MELHORIA",
                "evidence_kind": "LOCALIZED",
                "anchor": {"type": "TEXT_OFFSET", "start": 0, "end": 5, "quote": "Texto"},
                "short_comment": "curto",
                "long_comment": "longo",
            }
        ],
        "rewrites": [],
        "feedback": {"strengths": [], "improvements": [], "next_essay_strategy": "..."},
        "intervention": {
            "agente": "Ministério da Educação", "acao": "ampliar formação",
            "meio_modo": "por programa federal", "finalidade": "reduzir a evasão",
            "detalhamento": "com metas anuais", "respeita_direitos_humanos": True,
        },
        "alerts": [],
    }
    payload.update(overrides)
    return payload


class TestEngineContractShape(unittest.TestCase):
    def test_accepts_a_complete_valid_output(self):
        output = EssayEngineOutput.model_validate(minimal_payload())
        self.assertEqual(output.scores.total, 800)

    def test_accepts_an_output_with_no_scores_formative_mode(self):
        """Rejection case: none. In FORMATIVO the engine produces no score at all
        and the output must still be valid (spec §3)."""
        output = EssayEngineOutput.model_validate(minimal_payload(scores=None))
        self.assertIsNone(output.scores)

    def test_rejects_partial_scores(self):
        """Rejection 3: three competencies out of five."""
        partial = {
            "per_competency": {
                c: {"points": 160, "confidence": 0.8} for c in ("C1", "C2", "C3")
            },
            "total": 480,
        }
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(minimal_payload(scores=partial))

    def test_rejects_a_competency_score_off_the_official_scale(self):
        """Rejection: a competency score not in [0, 40, 80, 120, 160, 200]."""
        scores = {
            "per_competency": {
                "C1": {"points": 50, "confidence": 0.8},
                "C2": {"points": 160, "confidence": 0.8},
                "C3": {"points": 160, "confidence": 0.8},
                "C4": {"points": 160, "confidence": 0.8},
                "C5": {"points": 160, "confidence": 0.8},
            },
            "total": 690,
        }
        with self.assertRaises(ValidationError) as cm:
            EssayEngineOutput.model_validate(minimal_payload(scores=scores))
        self.assertIn("points must be one of", str(cm.exception))

    def test_rejects_a_total_that_is_not_the_sum(self):
        """Rejection 2."""
        scores = {
            "per_competency": {
                c: {"points": 160, "confidence": 0.8}
                for c in ("C1", "C2", "C3", "C4", "C5")
            },
            "total": 999,
        }
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(minimal_payload(scores=scores))

    def test_rejects_an_unknown_contract_version(self):
        """Rejection 12."""
        payload = minimal_payload()
        payload["identification"]["contract_version"] = "essay_engine_output_v99"
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)

    def test_rejects_an_anchor_that_disagrees_with_anchor_mode(self):
        """Rejection 10: the output declares TEXT_OFFSET but anchors on an image."""
        payload = minimal_payload()
        payload["annotations"][0]["anchor"] = {
            "type": "IMAGE_REGION", "page": 1, "x": 10.0, "y": 10.0,
            "width": 50.0, "height": 20.0, "read_text": "Texto",
        }
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)

    def test_rejects_mixed_anchors_in_one_output(self):
        """Rejection 11."""
        payload = minimal_payload()
        payload["identification"]["anchor_mode"] = "IMAGE_REGION"
        payload["annotations"][0]["anchor"] = {
            "type": "IMAGE_REGION", "page": 1, "x": 10.0, "y": 10.0,
            "width": 50.0, "height": 20.0, "read_text": "Texto",
        }
        payload["annotations"].append({
            "letter": "B", "competency_code": "C2", "kind": "ACERTO",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 0, "end": 5, "quote": "Texto"},
            "short_comment": "curto", "long_comment": "longo",
        })
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)

    def test_rejects_an_offset_anchor_whose_end_precedes_start(self):
        payload = minimal_payload()
        payload["annotations"][0]["anchor"] = {
            "type": "TEXT_OFFSET", "start": 10, "end": 3, "quote": "x"
        }
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)
