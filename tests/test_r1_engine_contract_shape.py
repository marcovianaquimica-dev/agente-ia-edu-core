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
        "intro_message": "Olá! Vamos ver como foi sua redação.",
        "closing_message": "Continue praticando, você está no caminho certo.",
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

    def test_rewrite_requires_letter_and_competency_code(self):
        payload = minimal_payload(rewrites=[
            {"original": "x", "suggestion": "y", "pedagogical_goal": "z"}
        ])
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)

    def test_accepts_a_rewrite_with_letter_and_competency_code(self):
        payload = minimal_payload(rewrites=[
            {
                "letter": "A", "competency_code": "C1",
                "original": "x", "suggestion": "y", "pedagogical_goal": "z",
            }
        ])
        output = EssayEngineOutput.model_validate(payload)
        self.assertEqual(output.rewrites[0].letter, "A")

    def test_missing_intro_message_rejected(self):
        payload = minimal_payload()
        del payload["intro_message"]
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)

    def test_missing_closing_message_rejected(self):
        payload = minimal_payload()
        del payload["closing_message"]
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)

    def test_mechanical_review_defaults_to_empty_tuple(self):
        output = EssayEngineOutput.model_validate(minimal_payload())
        self.assertEqual(output.mechanical_review, ())

    def test_mechanical_review_accepts_occurrences(self):
        payload = minimal_payload(mechanical_review=[
            {
                "category": "CRASE", "excerpt": "a ela",
                "suggested_form": "à ela", "rule_explanation": "fusão de a + a",
            }
        ])
        output = EssayEngineOutput.model_validate(payload)
        self.assertEqual(output.mechanical_review[0].category, "CRASE")

    def test_rationale_requires_strengths_and_growth_area(self):
        payload = minimal_payload()
        del payload["rationales"][0]["strengths"]
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)

    def test_accepts_a_global_annotation_with_anchor_explicitly_none(self):
        """A GLOBAL critique is a judgement of the competency as a whole, not
        a pointer to a passage; the engine is explicitly instructed not to
        invent an anchor for it (essay_prompts/v2.py), so anchor=None must
        validate."""
        payload = minimal_payload()
        payload["annotations"][0] = {
            "letter": "A",
            "competency_code": "C1",
            "kind": "MELHORIA",
            "evidence_kind": "GLOBAL",
            "anchor": None,
            "short_comment": "curto",
            "long_comment": "longo",
        }
        output = EssayEngineOutput.model_validate(payload)
        self.assertIsNone(output.annotations[0].anchor)

    def test_accepts_a_global_annotation_with_anchor_field_omitted(self):
        """Same case as above, but the engine simply omits the ``anchor``
        field entirely rather than sending an explicit null."""
        payload = minimal_payload()
        payload["annotations"][0] = {
            "letter": "A",
            "competency_code": "C1",
            "kind": "MELHORIA",
            "evidence_kind": "GLOBAL",
            "short_comment": "curto",
            "long_comment": "longo",
        }
        output = EssayEngineOutput.model_validate(payload)
        self.assertIsNone(output.annotations[0].anchor)

    def test_rejects_a_localized_annotation_with_no_anchor(self):
        """The other half of the ruling: LOCALIZED without an anchor is
        still nonsensical (a "specific" critique with no pointer to where in
        the text) and must keep being rejected."""
        payload = minimal_payload()
        payload["annotations"][0] = {
            "letter": "A",
            "competency_code": "C1",
            "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": None,
            "short_comment": "curto",
            "long_comment": "longo",
        }
        with self.assertRaises(ValidationError):
            EssayEngineOutput.model_validate(payload)
