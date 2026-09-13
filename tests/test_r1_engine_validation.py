import unittest
import uuid

from agente_ia_edu.essay_engine_contract.v1 import CONTRACT_VERSION, EssayEngineOutput
from agente_ia_edu.services.essay_engine_validation import (
    EssayEngineOutputRejected,
    RubricView,
    validate_engine_output,
)

TEXT = "A valorização da cultura é um direito de todos os brasileiros."

RUBRIC = RubricView(
    rubric_version="ENEM_2025",
    levels={c: frozenset((0, 40, 80, 120, 160, 200)) for c in ("C1", "C2", "C3", "C4", "C5")},
    signal_keys=frozenset({"ortografia_acentuacao", "repertorio_pertinencia"}),
)


def build_output(**overrides) -> EssayEngineOutput:
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
                c: {"points": 160, "confidence": 0.8}
                for c in ("C1", "C2", "C3", "C4", "C5")
            },
            "total": 800,
        },
        "rationales": [
            {"competency_code": c, "summary": "resumo", "signal_keys": []}
            for c in ("C1", "C2", "C3", "C4", "C5")
        ],
        "annotations": [
            {
                "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
                "evidence_kind": "LOCALIZED",
                "anchor": {
                    "type": "TEXT_OFFSET", "start": 2, "end": 13, "quote": "valorização"
                },
                "short_comment": "curto", "long_comment": "longo",
                "signal_keys": ["ortografia_acentuacao"],
            }
        ],
        "rewrites": [],
        "feedback": {"strengths": [], "improvements": [], "next_essay_strategy": "..."},
        "intervention": {"respeita_direitos_humanos": True},
        "alerts": [],
    }
    for key, value in overrides.items():
        payload[key] = value
    return EssayEngineOutput.model_validate(payload)


class TestEngineValidation(unittest.TestCase):
    def test_accepts_a_verifiable_output(self):
        validate_engine_output(build_output(), rubric=RUBRIC, text=TEXT)

    def _assert_rejected(self, output, *, reason_code, **kwargs):
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(output, rubric=RUBRIC, text=TEXT, **kwargs)
        self.assertEqual(caught.exception.reason_code, reason_code)

    def test_rejects_a_score_not_in_the_rubric_levels(self):
        """Rejection 1: C3 only allows 0/40/80 in this rubric."""
        rubric = RubricView(
            rubric_version="ENEM_2025",
            levels={
                **{c: frozenset((0, 40, 80, 120, 160, 200)) for c in ("C1", "C2", "C4", "C5")},
                "C3": frozenset((0, 40, 80)),
            },
            signal_keys=RUBRIC.signal_keys,
        )
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(build_output(), rubric=rubric, text=TEXT)
        self.assertEqual(caught.exception.reason_code, "SCORE_NOT_IN_RUBRIC_LEVELS")

    def test_rejects_a_rubric_version_mismatch(self):
        rubric = RubricView(
            rubric_version="ENEM_2024", levels=RUBRIC.levels, signal_keys=RUBRIC.signal_keys
        )
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(build_output(), rubric=rubric, text=TEXT)
        self.assertEqual(caught.exception.reason_code, "RUBRIC_VERSION_MISMATCH")

    def test_rejects_an_annotation_on_a_competency_absent_from_the_rubric(self):
        """Rejection 6."""
        rubric = RubricView(
            rubric_version="ENEM_2025",
            levels={c: frozenset((0, 40, 80, 120, 160, 200)) for c in ("C2", "C3", "C4", "C5")},
            signal_keys=RUBRIC.signal_keys,
        )
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(build_output(scores=None), rubric=rubric, text=TEXT)
        self.assertEqual(caught.exception.reason_code, "UNKNOWN_COMPETENCY")

    def test_rejects_an_unknown_signal_key(self):
        """Rejection 7."""
        output = build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 2, "end": 13, "quote": "valorização"},
            "short_comment": "curto", "long_comment": "longo",
            "signal_keys": ["sinal_inventado"],
        }])
        self._assert_rejected(output, reason_code="UNKNOWN_SIGNAL_KEY")

    def test_rejects_a_quote_that_does_not_match_the_text(self):
        """Rejection 4: the anti-hallucination guard."""
        output = build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 2, "end": 13, "quote": "desvalorização"},
            "short_comment": "curto", "long_comment": "longo",
        }])
        self._assert_rejected(output, reason_code="QUOTE_DOES_NOT_MATCH_TEXT")

    def test_rejects_an_offset_beyond_the_text(self):
        """Rejection 5."""
        output = build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 5000, "end": 5010, "quote": "x"},
            "short_comment": "curto", "long_comment": "longo",
        }])
        self._assert_rejected(output, reason_code="OFFSET_OUT_OF_BOUNDS")

    def test_rejects_a_specific_critique_with_no_resolvable_evidence(self):
        """Rejection 8: spec §4's pedagogical safety rule, as a rejection condition.

        A MELHORIA annotation declaring itself LOCALIZED must point somewhere; if
        it has nothing to point at, it must declare itself GLOBAL instead."""
        output = build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 0, "end": 1, "quote": "A"},
            "short_comment": "curto", "long_comment": "longo",
        }])
        # This one is well-formed; the rejection comes from the empty-evidence case.
        validate_engine_output(output, rubric=RUBRIC, text=TEXT)

        # TEXT[1:2] is the space after "A", so the quote MATCHES the text and the
        # rejection can only come from the evidence rule - not from layer 3's
        # quote check, which would otherwise fire first and mask it.
        self.assertEqual(TEXT[1:2], " ")
        blank = build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 1, "end": 2, "quote": " "},
            "short_comment": "curto", "long_comment": "longo",
        }])
        self._assert_rejected(blank, reason_code="SPECIFIC_CRITIQUE_WITHOUT_EVIDENCE")

    def test_rejects_a_region_outside_the_page(self):
        """Rejection 9."""
        output = build_output(
            identification={
                "essay_id": str(uuid.uuid4()), "essay_version_id": str(uuid.uuid4()),
                "rubric_version": "ENEM_2025", "model_version": "fake-model-1",
                "prompt_version": "v1", "engine_version": "r1.0.0",
                "contract_version": CONTRACT_VERSION, "anchor_mode": "IMAGE_REGION",
            },
            annotations=[{
                "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
                "evidence_kind": "LOCALIZED",
                "anchor": {
                    "type": "IMAGE_REGION", "page": 1, "x": 500.0, "y": 10.0,
                    "width": 400.0, "height": 20.0, "read_text": "Texto",
                },
                "short_comment": "curto", "long_comment": "longo",
            }],
        )
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(
                output, rubric=RUBRIC, text=None, page_boxes={1: (600.0, 800.0)}
            )
        self.assertEqual(caught.exception.reason_code, "REGION_OUT_OF_PAGE")

    def test_the_rejection_carries_the_raw_output_and_input_hash(self):
        raw = {"whatever": "the model returned"}
        output = build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 2, "end": 13, "quote": "errado"},
            "short_comment": "curto", "long_comment": "longo",
        }])
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(
                output, rubric=RUBRIC, text=TEXT, raw_output=raw, input_hash="abc123"
            )
        self.assertEqual(caught.exception.raw_output, raw)
        self.assertEqual(caught.exception.input_hash, "abc123")
