import unittest
import uuid

from pydantic import ValidationError

from agente_ia_edu.essay_engine_contract.v2 import CONTRACT_VERSION, EssayEngineOutput


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
            "anchor_mode": "IMAGE_REGION",
        },
        "scores": {
            "per_competency": {
                code: {"points": 160, "confidence": 0.8}
                for code in ("C1", "C2", "C3", "C4", "C5")
            },
            "total": 800,
        },
        "rationales": [
            {
                "competency_code": c, "summary": "resumo",
                "strengths": "pontos fortes", "growth_area": "onde avançar",
                "signal_keys": [],
            }
            for c in ("C1", "C2", "C3", "C4", "C5")
        ],
        "annotations": [
            {
                "letter": "A",
                "competency_code": "C1",
                "kind": "MELHORIA",
                "evidence_kind": "LOCALIZED",
                "anchor": {
                    "type": "IMAGE_REGION", "page": 1, "line": 2, "total_lines": 30,
                    "read_text": "Texto",
                },
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
        "intro_message": "Olá! Vamos ver como foi sua redação.",
        "closing_message": "Continue praticando, você está no caminho certo.",
    }
    payload.update(overrides)
    return payload


class EngineContractV2Tests(unittest.TestCase):
    def test_contract_version_is_v2(self):
        self.assertEqual(CONTRACT_VERSION, "essay_engine_output_v2")

    def test_accepts_a_minimal_line_based_image_region_payload(self):
        EssayEngineOutput.model_validate(minimal_payload())

    def test_rejects_pixel_fields_on_an_image_region_anchor(self):
        """v1's x/y/width/height shape must not validate against v2 - a
        stray fallback to the old field names must fail loudly, not
        silently accept a payload the model never actually produced."""
        payload = minimal_payload()
        payload["annotations"][0]["anchor"] = {
            "type": "IMAGE_REGION", "page": 1, "x": 10.0, "y": 10.0,
            "width": 50.0, "height": 20.0, "read_text": "Texto",
        }
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)

    def test_rejects_a_line_number_beyond_total_lines(self):
        payload = minimal_payload()
        payload["annotations"][0]["anchor"] = {
            "type": "IMAGE_REGION", "page": 1, "line": 31, "total_lines": 30,
            "read_text": "Texto",
        }
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)

    def test_accepts_line_equal_to_total_lines(self):
        payload = minimal_payload()
        payload["annotations"][0]["anchor"] = {
            "type": "IMAGE_REGION", "page": 1, "line": 30, "total_lines": 30,
            "read_text": "Texto",
        }
        EssayEngineOutput.model_validate(payload)

    def test_rejects_an_unknown_contract_version(self):
        payload = minimal_payload()
        payload["identification"]["contract_version"] = "essay_engine_output_v1"
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)


if __name__ == "__main__":
    unittest.main()
