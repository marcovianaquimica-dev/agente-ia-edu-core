import unittest
import uuid

from sqlalchemy import func, select, update
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import EssayRubric, EssayRubricSignal
from agente_ia_edu.essay_engine_contract.v1 import CONTRACT_VERSION, EssayEngineOutput
from agente_ia_edu.essay_engine_contract.v2 import (
    CONTRACT_VERSION as CONTRACT_VERSION_V2,
    EssayEngineOutput as EssayEngineOutputV2,
)
from agente_ia_edu.rubrics.loader import load_rubric_file
from agente_ia_edu.services.essay_rubric_seed import EssayRubricSeeder
from agente_ia_edu.services.essay_engine_validation import (
    EssayEngineOutputRejected,
    RubricHasNoLevelsError,
    RubricView,
    load_rubric_view,
    validate_engine_output,
    validate_engine_output_from_payload,
)

TEXT = "A valorização da cultura é um direito de todos os brasileiros."

#: Shape of a real transcribed handwritten essay (2026-09-25): one numbered
#: line per physical line of the answer sheet, words hyphenated across the line
#: break, accents everywhere. The line-number prefixes are inconsistent ("1 "
#: vs "3. ") exactly as the live transcription had them. The two passages the
#: drift tests below use - "4. sas principais" and the 101-character line 1 -
#: are the ones from the two live QUOTE_DOES_NOT_MATCH_TEXT rejections.
LIVE_TRANSCRIPTION = "\n".join([
    "1 O trabalho de cuidado se mostra necessário na medida em que é responsável pela gestão da crianção e",
    "2 idosos, sendo um serviço essencial para a manutenção da sociedade brasileira. Entretanto, esse",
    "3. a invisibilidade desse trabalho no Brasil decorre de duas cau-",
    "4. sas principais: a herança histórica da escravidão e a desvalorização social do cuidado.",
    "5 Em primeiro lugar, é importante ressaltar que o período escravocrata deixou marcas",
    "6 profundas na organização do trabalho no país, delegando às mulheres negras as tare-",
    "7 fas domésticas sem qualquer reconhecimento formal ou remuneração adequada.",
    "8 Além disso, a ausência de políticas públicas voltadas ao cuidado agrava o problema,",
    "9 uma vez que sobrecarrega as famílias mais pobres e perpetua a desigualdade de gênero.",
])

RUBRIC = RubricView(
    rubric_version="ENEM_2025",
    levels={c: frozenset((0, 40, 80, 120, 160, 200)) for c in ("C1", "C2", "C3", "C4", "C5")},
    signal_keys=frozenset({"ortografia_acentuacao", "repertorio_pertinencia"}),
)


def build_payload(**overrides) -> dict:
    """Shared by build_output() (validates against v1's EssayEngineOutput -
    contract_version must be v1's) and the validate_engine_output_from_payload
    tests below (validates against v2 internally - those override
    identification with CONTRACT_VERSION_V2 at the call site)."""
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
            {
                "competency_code": c, "summary": "resumo",
                "strengths": "pontos fortes", "growth_area": "onde avançar",
                "signal_keys": [],
            }
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
        "intro_message": "Olá! Vamos ver como foi sua redação.",
        "closing_message": "Continue praticando, você está no caminho certo.",
    }
    for key, value in overrides.items():
        payload[key] = value
    return payload


def build_output(**overrides) -> EssayEngineOutput:
    return EssayEngineOutput.model_validate(build_payload(**overrides))


def build_payload_v2(**overrides) -> dict:
    """Like build_payload(), but for validate_engine_output_from_payload
    calls, which parse against essay_engine_contract.v2 internally."""
    payload = build_payload(**overrides)
    payload["identification"] = {**payload["identification"], "contract_version": CONTRACT_VERSION_V2}
    return payload


class TestEngineValidation(unittest.TestCase):
    def test_accepts_a_verifiable_output(self):
        validate_engine_output(build_output(), rubric=RUBRIC, text=TEXT)

    def test_rejects_a_rewrite_referencing_an_unknown_letter(self):
        output = build_output(rewrites=[
            {
                "letter": "Z", "competency_code": "C1",
                "original": "x", "suggestion": "y", "pedagogical_goal": "z",
            }
        ])
        self._assert_rejected(output, reason_code="REWRITE_LETTER_NOT_FOUND")

    def test_accepts_a_rewrite_referencing_a_real_letter(self):
        # build_payload()'s single annotation uses letter "A" (see fixture).
        output = build_output(rewrites=[
            {
                "letter": "A", "competency_code": "C1",
                "original": "x", "suggestion": "y", "pedagogical_goal": "z",
            }
        ])
        validate_engine_output(output, rubric=RUBRIC, text=TEXT)

    def test_rejects_a_rewrite_whose_competency_does_not_match_its_annotation(self):
        # build_payload()'s single annotation uses letter "A" with
        # competency_code "C1" (see fixture) - a rewrite referencing that
        # same letter but declaring a different (still rubric-valid)
        # competency must be rejected, not silently accepted.
        output = build_output(rewrites=[
            {
                "letter": "A", "competency_code": "C2",
                "original": "x", "suggestion": "y", "pedagogical_goal": "z",
            }
        ])
        self._assert_rejected(output, reason_code="REWRITE_COMPETENCY_MISMATCH")

    def test_rejects_a_rewrite_with_unknown_competency(self):
        rubric = RubricView(
            rubric_version="ENEM_2025",
            levels={c: frozenset((0, 40, 80, 120, 160, 200)) for c in ("C1", "C2", "C3", "C4")},
            signal_keys=RUBRIC.signal_keys,
        )
        output = build_output(rewrites=[
            {
                "letter": "A", "competency_code": "C5",
                "original": "x", "suggestion": "y", "pedagogical_goal": "z",
            }
        ])
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(output, rubric=rubric, text=TEXT)
        self.assertEqual(caught.exception.reason_code, "UNKNOWN_COMPETENCY")

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

    def test_rejects_a_rationale_on_a_competency_absent_from_the_rubric(self):
        """Rejection 6, rationales path.

        The rationales loop runs before the annotations loop in
        ``validate_engine_output``. ``scores=None`` keeps the scores loop
        (which would otherwise fire first) out of the way, and the default
        rationales cover all five competencies - including C1, which this
        rubric lacks - so the rejection can only come from the rationales
        loop."""
        rubric = RubricView(
            rubric_version="ENEM_2025",
            levels={c: frozenset((0, 40, 80, 120, 160, 200)) for c in ("C2", "C3", "C4", "C5")},
            signal_keys=RUBRIC.signal_keys,
        )
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(build_output(scores=None), rubric=rubric, text=TEXT)
        self.assertEqual(caught.exception.reason_code, "UNKNOWN_COMPETENCY")
        self.assertIn("C1", str(caught.exception))

    def test_rejects_an_annotation_on_a_competency_absent_from_the_rubric(self):
        """Rejection 6, annotations path.

        Isolated from the rationales loop that precedes it: ``scores=None``
        removes the scores loop, and the rationales here reference only C2-C5
        - all present in this rubric - so they raise nothing. C1 appears
        ONLY on the annotation, so an UNKNOWN_COMPETENCY naming C1 can only
        have been raised by the annotations loop; asserting the message
        contains C1 pins that, since no other loop in this payload can ever
        produce it.

        Verified by isolation: commenting out the annotations-loop
        UNKNOWN_COMPETENCY check in essay_engine_validation.py makes this
        test fail (no exception is raised, because nothing else in this
        payload can trigger one); restoring it makes it pass again. See
        task-7-report.md for the RED/GREEN run."""
        rubric = RubricView(
            rubric_version="ENEM_2025",
            levels={c: frozenset((0, 40, 80, 120, 160, 200)) for c in ("C2", "C3", "C4", "C5")},
            signal_keys=RUBRIC.signal_keys,
        )
        output = build_output(
            scores=None,
            rationales=[
                {
                    "competency_code": c, "summary": "resumo",
                    "strengths": "pontos fortes", "growth_area": "onde avançar",
                    "signal_keys": [],
                }
                for c in ("C2", "C3", "C4", "C5")
            ],
        )
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(output, rubric=rubric, text=TEXT)
        self.assertEqual(caught.exception.reason_code, "UNKNOWN_COMPETENCY")
        self.assertIn("C1", str(caught.exception))

    def test_tolerates_an_unknown_signal_key_on_an_annotation(self):
        """signal_keys are auxiliary tags nothing downstream reads, and the
        engine prompt never enumerates the rubric's real registered keys -
        the model has no way to know which ones are valid, so a made-up tag
        must not sink an otherwise-sound annotation's real content."""
        output = build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 2, "end": 13, "quote": "valorização"},
            "short_comment": "curto", "long_comment": "longo",
            "signal_keys": ["sinal_inventado"],
        }])
        validate_engine_output(output, rubric=RUBRIC, text=TEXT)

    def test_tolerates_an_unknown_signal_key_on_a_rationale(self):
        output = build_output(rationales=[
            {
                "competency_code": c, "summary": "resumo",
                "strengths": "pontos fortes", "growth_area": "onde avançar",
                "signal_keys": ["sinal_inventado"] if c == "C1" else [],
            }
            for c in ("C1", "C2", "C3", "C4", "C5")
        ])
        validate_engine_output(output, rubric=RUBRIC, text=TEXT)

    def test_rejects_a_quote_that_does_not_match_the_text(self):
        """Rejection 4: the anti-hallucination guard."""
        output = build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 2, "end": 13, "quote": "desvalorização"},
            "short_comment": "curto", "long_comment": "longo",
        }])
        self._assert_rejected(output, reason_code="QUOTE_DOES_NOT_MATCH_TEXT")

    def test_tolerates_a_quote_offset_by_one_leading_whitespace_character(self):
        """Confirmed live (2026-09-25): the model repeatedly anchored one
        character into the "\\n\\n" separating paragraphs while quoting the
        clean phrase - a real, working correction with 5+ annotations was
        rejected outright over this single-character padding. The anchor
        still points at real, correct content, so it must not reject."""
        text = "Primeiro paragrafo.\n\nSegundo paragrafo com um trecho relevante."
        quote = "Segundo"
        end = text.index(quote) + len(quote)
        start_with_stray_newline = text.index(quote) - 1
        output = build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {
                "type": "TEXT_OFFSET", "start": start_with_stray_newline,
                "end": end, "quote": quote,
            },
            "short_comment": "curto", "long_comment": "longo",
        }])
        validate_engine_output(output, rubric=RUBRIC, text=text)

    def test_still_rejects_a_genuinely_wrong_quote_despite_whitespace_tolerance(self):
        text = "Primeiro paragrafo.\n\nSegundo paragrafo com um trecho relevante."
        start = text.index("Segundo") - 1
        end = start + 1 + len("Terceiro")
        output = build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": start, "end": end, "quote": "Terceiro"},
            "short_comment": "curto", "long_comment": "longo",
        }])
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(output, rubric=RUBRIC, text=text)
        self.assertEqual(caught.exception.reason_code, "QUOTE_DOES_NOT_MATCH_TEXT")

    # --- live offset-drift cases (2026-09-25) ------------------------------
    #
    # Both cases below are real model outputs on the same real handwritten
    # essay, rejected with QUOTE_DOES_NOT_MATCH_TEXT although the quote was a
    # verbatim, correctly-chosen passage - the offsets simply drifted. See
    # _resolve_text_offset's docstring in essay_engine_validation.py for the
    # measurements that ruled out UTF-8-byte and JSON-escape miscounting as
    # the cause.

    def _anchored(self, *, start, end, quote):
        return build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {
                "type": "TEXT_OFFSET", "start": start, "end": end, "quote": quote,
            },
            "short_comment": "curto", "long_comment": "longo",
        }])

    def test_reanchors_a_quote_whose_offsets_landed_one_line_early(self):
        """Live case 1: the model quoted the start of transcription line 4 -
        which continues "cau-", the word hyphenated at the end of line 3 - and
        its offsets landed one whole transcription line early, on the start of
        line 3 (82 characters early in the live essay; one line of this
        fixture, which has shorter lines). The quote is verbatim and occurs
        once, so the annotation must be re-anchored onto it instead of sinking
        the whole correction."""
        text = LIVE_TRANSCRIPTION
        quote = "4. sas principais"
        real_start = text.index(quote)
        drifted_start = text.index("3. a invisibilida")
        # This is exactly what the live log reported as the mismatch.
        self.assertEqual(text[drifted_start : drifted_start + len(quote)],
                         "3. a invisibilida")
        # Off by exactly one transcription line, as in the live rejection
        # (there the gap was 82 characters).
        self.assertEqual(
            real_start - drifted_start, len(text.split("\n")[2]) + 1
        )

        output = self._anchored(
            start=drifted_start, end=drifted_start + len(quote), quote=quote
        )
        validate_engine_output(output, rubric=RUBRIC, text=text)

        anchor = output.annotations[0].anchor
        self.assertEqual(anchor.start, real_start)
        self.assertEqual(anchor.end, real_start + len(quote))
        self.assertEqual(text[anchor.start : anchor.end], quote)

    def test_reanchors_a_quote_whose_end_was_one_character_too_low(self):
        """Live case 2: the model quoted a whole 101-character transcription
        line and reported an ``end`` one character too low, cutting off the
        line's last character (the "e" of "e idosos", wrapping to line 2)."""
        text = LIVE_TRANSCRIPTION
        quote = text.split("\n")[0]
        drifted_end = len(quote) - 1
        self.assertTrue(text[0:drifted_end].endswith("crianção "))
        self.assertTrue(quote.endswith("crianção e"))

        output = self._anchored(start=0, end=drifted_end, quote=quote)
        validate_engine_output(output, rubric=RUBRIC, text=text)

        anchor = output.annotations[0].anchor
        self.assertEqual((anchor.start, anchor.end), (0, len(quote)))
        self.assertEqual(text[anchor.start : anchor.end], quote)

    def test_the_corrected_offsets_are_what_the_caller_persists(self):
        """The service stores ``output.model_dump()`` as ai_output and the
        student's highlighted text is rendered from those offsets, so the
        repair has to be visible on the returned output, not just internal."""
        text = LIVE_TRANSCRIPTION
        quote = "4. sas principais"
        drifted_start = text.index("3. a invisibilida")
        payload = build_payload_v2(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "LOCALIZED",
            "anchor": {
                "type": "TEXT_OFFSET", "start": drifted_start,
                "end": drifted_start + len(quote), "quote": quote,
            },
            "short_comment": "curto", "long_comment": "longo",
        }])
        output = validate_engine_output_from_payload(payload, rubric=RUBRIC, text=text)
        dumped = output.model_dump(mode="json")["annotations"][0]["anchor"]
        self.assertEqual(dumped["start"], text.index(quote))
        self.assertEqual(text[dumped["start"] : dumped["end"]], quote)

    def test_reanchors_an_anchor_whose_drift_ran_past_the_end_of_the_text(self):
        """Drift is as likely on the last paragraph as on the first, and there
        the same miscount produces offsets beyond the text. The quote is still
        verbatim and unique, so this must be repaired rather than rejected as
        OFFSET_OUT_OF_BOUNDS."""
        text = LIVE_TRANSCRIPTION
        quote = "desvalorização social do cuidado."
        real_start = text.index(quote)
        output = self._anchored(
            start=len(text) - 3, end=len(text) + len(quote) - 3, quote=quote
        )
        validate_engine_output(output, rubric=RUBRIC, text=text)
        anchor = output.annotations[0].anchor
        self.assertEqual((anchor.start, anchor.end), (real_start, real_start + len(quote)))

    def test_reanchors_a_long_quote_that_occurs_exactly_once_however_far_away(self):
        """A quote found exactly once in the whole text is unambiguous wherever
        it sits, so a drift wider than the local window is still safe to fix -
        as long as the quote is long enough for that single occurrence to mean
        something (_MIN_UNIQUE_REANCHOR_CHARS)."""
        text = LIVE_TRANSCRIPTION
        quote = "sobrecarrega as famílias mais pobres"
        real_start = text.index(quote)
        self.assertGreater(real_start, 400)  # beyond _ANCHOR_DRIFT_WINDOW from 0
        output = self._anchored(start=0, end=len(quote), quote=quote)
        validate_engine_output(output, rubric=RUBRIC, text=text)
        self.assertEqual(output.annotations[0].anchor.start, real_start)

    def test_still_rejects_a_quote_that_is_only_approximately_in_the_text(self):
        """The repair is an EXACT substring search, never a fuzzy match: a
        quote that differs from the text by a single character (here a missing
        accent) is not the same passage and must still be rejected, otherwise
        the guard against invented evidence is gone."""
        text = LIVE_TRANSCRIPTION
        quote = "a heranca historica da escravidao"  # accents dropped
        self.assertNotIn(quote, text)
        start = text.index("a herança")
        output = self._anchored(start=start, end=start + len(quote), quote=quote)
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(output, rubric=RUBRIC, text=text)
        self.assertEqual(caught.exception.reason_code, "QUOTE_DOES_NOT_MATCH_TEXT")

    def test_still_rejects_a_quote_whose_position_is_ambiguous(self):
        """Two candidate positions near the claimed offsets means we cannot
        tell which passage the model meant. Guessing would risk anchoring a
        criticism on the wrong sentence, so this stays a rejection."""
        text = "1 o trabalho de cuidado é essencial.\n2 o trabalho de cuidado é essencial."
        quote = "o trabalho de cuidado"
        self.assertEqual(text.count(quote), 2)
        output = self._anchored(start=0, end=len(quote), quote=quote)
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(output, rubric=RUBRIC, text=text)
        self.assertEqual(caught.exception.reason_code, "QUOTE_DOES_NOT_MATCH_TEXT")

    def test_still_rejects_a_short_unique_quote_claimed_far_from_where_it_is(self):
        """The whole-text uniqueness fallback requires a quote long enough for
        its single occurrence to be meaningful; a handful of characters found
        once, hundreds of characters away from where the model said it was, is
        not evidence of a mere miscount."""
        text = LIVE_TRANSCRIPTION
        quote = "crianção"  # 8 characters, below the uniqueness floor
        self.assertEqual(text.count(quote), 1)
        far = len(text) - len(quote)
        self.assertGreater(abs(far - text.index(quote)), 400)
        output = self._anchored(start=far, end=far + len(quote), quote=quote)
        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output(output, rubric=RUBRIC, text=text)
        self.assertEqual(caught.exception.reason_code, "QUOTE_DOES_NOT_MATCH_TEXT")

    def test_offset_drift_is_not_explained_by_utf8_byte_counting(self):
        """Pins the root-cause finding, so nobody re-opens the investigation
        on the tempting hypothesis. If the model were counting UTF-8 bytes (or
        the JSON-escaped form the prompt shows it) its offsets would come back
        HIGHER than the real ones, by one per multi-byte character before the
        anchor. Both live cases drifted the other way and by amounts unrelated
        to the accented-character count - so this is plain arithmetic error,
        and only an exact re-anchor on the quote can repair it."""
        text = LIVE_TRANSCRIPTION
        real_start = text.index("4. sas principais")
        byte_delta = len(text[:real_start].encode("utf-8")) - real_start
        self.assertEqual(byte_delta, sum(1 for c in text[:real_start] if ord(c) > 127))
        self.assertGreater(byte_delta, 0)  # byte counting drifts POSITIVE...
        drift = text.index("3. a invisibilida") - real_start
        self.assertLess(drift, 0)  # ...but the observed drift was negative
        self.assertNotEqual(abs(drift), byte_delta)

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

    def test_accepts_a_global_annotation_without_anchor_in_text_offset_mode(self):
        """The exact regression this fix targets: a GLOBAL annotation with no
        anchor must survive layer 3 (anchoring) in TEXT_OFFSET mode without
        raising AttributeError - the loop must skip it, not dereference
        ``annotation.anchor.end``/``.start``/``.quote`` on a None."""
        output = build_output(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "GLOBAL",
            "short_comment": "curto", "long_comment": "longo",
        }])
        validate_engine_output(output, rubric=RUBRIC, text=TEXT)

    def test_accepts_a_global_annotation_without_anchor_in_image_region_mode(self):
        """Same regression, IMAGE_REGION mode: the loop must skip a
        GLOBAL/anchor=None annotation rather than dereferencing
        ``annotation.anchor.page``/``.x``/``.y``/etc on a None."""
        output = build_output(
            identification={
                "essay_id": str(uuid.uuid4()), "essay_version_id": str(uuid.uuid4()),
                "rubric_version": "ENEM_2025", "model_version": "fake-model-1",
                "prompt_version": "v1", "engine_version": "r1.0.0",
                "contract_version": CONTRACT_VERSION, "anchor_mode": "IMAGE_REGION",
            },
            annotations=[{
                "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
                "evidence_kind": "GLOBAL",
                "short_comment": "curto", "long_comment": "longo",
            }],
        )
        validate_engine_output(
            output, rubric=RUBRIC, text=None, page_boxes={1: (600.0, 800.0)}
        )

    def test_from_payload_accepts_a_global_annotation_without_anchor(self):
        """End-to-end through the single entry point spec §7 promises: a
        payload with a GLOBAL, anchor-omitted annotation must validate clean
        through all three layers, not just layer 1."""
        raw_payload = build_payload_v2(annotations=[{
            "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
            "evidence_kind": "GLOBAL",
            "short_comment": "curto", "long_comment": "longo",
        }])
        output = validate_engine_output_from_payload(raw_payload, rubric=RUBRIC, text=TEXT)
        self.assertIsNone(output.annotations[0].anchor)

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


class TestLoadRubricView(unittest.IsolatedAsyncioTestCase):
    """``load_rubric_view`` is the only part of the module that touches the
    database, and it sits directly on the path from the seeded tables to the
    validator. These tests seed a real in-memory database with the actual
    ENEM_2025 rubric file - via the real ``EssayRubricSeeder`` and
    ``load_rubric_file``, not hand-built fixtures - so a join typo, a wrong
    ``active`` filter, or a missed competency shows up here rather than as a
    confusing UNKNOWN_COMPETENCY/UNKNOWN_SIGNAL_KEY downstream."""

    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.rubric_file = load_rubric_file("enem_2025")
        async with self.session_factory() as session:
            await EssayRubricSeeder(session).seed(self.rubric_file)
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_the_view_has_all_five_competencies_with_the_six_official_levels(self):
        async with self.session_factory() as session:
            view = await load_rubric_view(session, "ENEM_2025")
        self.assertEqual(set(view.levels), {"C1", "C2", "C3", "C4", "C5"})
        for code, levels in view.levels.items():
            self.assertEqual(levels, frozenset((0, 40, 80, 120, 160, 200)), code)

    async def test_the_signal_keys_match_the_active_signals_actually_seeded(self):
        async with self.session_factory() as session:
            view = await load_rubric_view(session, "ENEM_2025")
            active_keys = (
                await session.scalars(
                    select(EssayRubricSignal.key).where(EssayRubricSignal.active.is_(True))
                )
            ).all()
        self.assertTrue(active_keys)  # sanity: the rubric file actually declares signals
        self.assertEqual(view.signal_keys, frozenset(active_keys))

    async def test_a_signal_flipped_to_inactive_is_excluded_from_the_view(self):
        async with self.session_factory() as session:
            before = await load_rubric_view(session, "ENEM_2025")
            target_key = next(iter(before.signal_keys))

            await session.execute(
                update(EssayRubricSignal)
                .where(EssayRubricSignal.key == target_key)
                .values(active=False)
            )
            await session.commit()

            after = await load_rubric_view(session, "ENEM_2025")

        self.assertIn(target_key, before.signal_keys)
        self.assertNotIn(target_key, after.signal_keys)
        self.assertEqual(len(after.signal_keys), len(before.signal_keys) - 1)

    async def test_an_unknown_rubric_version_raises_value_error(self):
        async with self.session_factory() as session:
            with self.assertRaises(ValueError):
                await load_rubric_view(session, "ENEM_1999")

    async def test_a_bare_header_row_without_children_raises_rubric_has_no_levels(self):
        """The reader's counterpart to EssayRubricSeeder's
        IncompleteRubricSeedError: a rubric row written without its
        competencies/levels (bypassing the seeder entirely, as a manual insert
        or a future migration might) must not be silently treated as a usable,
        empty rubric."""
        async with self.session_factory() as session:
            session.add(EssayRubric(rubric_version="BARE_HEADER_ONLY", label="sem filhos"))
            await session.commit()

            with self.assertRaises(RubricHasNoLevelsError) as caught:
                await load_rubric_view(session, "BARE_HEADER_ONLY")
            self.assertIn("BARE_HEADER_ONLY", str(caught.exception))

    async def test_the_returned_view_carries_the_requested_rubric_version(self):
        async with self.session_factory() as session:
            view = await load_rubric_view(session, "ENEM_2025")
        self.assertEqual(view.rubric_version, "ENEM_2025")

    async def test_a_view_loaded_from_the_seeded_database_accepts_a_real_valid_output(self):
        """Closes the loop the module exists for: a view built by
        ``load_rubric_view`` from what ``EssayRubricSeeder`` actually wrote
        must accept an output that is genuinely valid under the real
        ENEM_2025 rubric. This is the only test in the suite that would
        catch a mismatch between what the seeder writes and what the
        validator expects."""
        async with self.session_factory() as session:
            view = await load_rubric_view(session, "ENEM_2025")

        output = build_output(
            annotations=[{
                "letter": "A", "competency_code": "C1", "kind": "MELHORIA",
                "evidence_kind": "LOCALIZED",
                "anchor": {
                    "type": "TEXT_OFFSET", "start": 2, "end": 13, "quote": "valorização"
                },
                "short_comment": "curto", "long_comment": "longo",
                # A real signal key from enem_2025.yaml's C1 block, not the
                # fictional key used by the module-level RUBRIC fixture.
                "signal_keys": ["convencoes_da_escrita"],
            }],
        )
        validate_engine_output(output, rubric=view, text=TEXT)


class TestValidateEngineOutputFromPayload(unittest.TestCase):
    """``validate_engine_output_from_payload`` is the single entry point spec §7
    promises: one exception type, carrying reason code, raw output and input
    hash, whichever of the three layers rejects the payload - including layer 1
    (shape), which ``validate_engine_output`` alone cannot guard because it only
    ever receives an already-parsed ``EssayEngineOutput``."""

    def test_a_shape_invalid_payload_raises_contract_shape_invalid(self):
        raw_payload = build_payload()
        del raw_payload["identification"]  # required field, missing -> ValidationError

        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output_from_payload(
                raw_payload, rubric=RUBRIC, text=TEXT, input_hash="abc123"
            )

        self.assertEqual(caught.exception.reason_code, "CONTRACT_SHAPE_INVALID")
        self.assertEqual(caught.exception.raw_output, raw_payload)
        self.assertEqual(caught.exception.input_hash, "abc123")

    def test_a_rubric_invalid_payload_still_raises_its_own_layer_2_code(self):
        """Shape is fine; the rejection must come from layer 2, not layer 1."""
        raw_payload = build_payload(
            identification={
                "essay_id": str(uuid.uuid4()),
                "essay_version_id": str(uuid.uuid4()),
                "rubric_version": "ENEM_2024",  # RUBRIC below is ENEM_2025
                "model_version": "fake-model-1",
                "prompt_version": "v1",
                "engine_version": "r1.0.0",
                "contract_version": CONTRACT_VERSION_V2,
                "anchor_mode": "TEXT_OFFSET",
            }
        )

        with self.assertRaises(EssayEngineOutputRejected) as caught:
            validate_engine_output_from_payload(raw_payload, rubric=RUBRIC, text=TEXT)

        self.assertEqual(caught.exception.reason_code, "RUBRIC_VERSION_MISMATCH")

    def test_a_valid_payload_returns_the_parsed_output(self):
        raw_payload = build_payload_v2()

        output = validate_engine_output_from_payload(raw_payload, rubric=RUBRIC, text=TEXT)

        self.assertIsInstance(output, EssayEngineOutputV2)
        self.assertEqual(output.identification.rubric_version, "ENEM_2025")
