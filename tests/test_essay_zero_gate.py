# tests/test_essay_zero_gate.py
"""Tests for services/essay_zero_gate.py (Fase C, spec 2026-10-06).

Two layers, matching the module's own split:

* ``_aggregate_zero_gate_runs`` - the pure aggregation core. No provider, no
  asyncio: these are the tests worth being exhaustive about, since this is
  where the ZERAR/NAO_ZERAR/ENCAMINHAR_REVISAO decision actually lives.
* ``evaluate_zero_gate`` - the thin async wrapper. A smaller set of
  IsolatedAsyncioTestCase-style tests with a minimal fake provider (no DB
  needed here - this module has no DB dependency at all, unlike
  EssayCorrectionService), proving the 3-call self-consistency mechanism
  and error propagation work end to end.
"""
import json
import unittest

from agente_ia_edu.providers.models import TextGenerationResult
from agente_ia_edu.services.essay_zero_gate import (
    ZERO_GATE_CODES,
    ZeroGateDecision,
    _aggregate_zero_gate_runs,
    evaluate_zero_gate,
)


def _run(applies_codes=()):
    """One sample run's assessments list: every code in ZERO_GATE_CODES,
    applies=True only for the given subset."""
    applies_codes = set(applies_codes)
    return [
        {
            "code": code, "applies": code in applies_codes,
            "evidence": "trecho citado" if code in applies_codes else "",
            "reasoning": "stub",
        }
        for code in ZERO_GATE_CODES
    ]


class AggregateZeroGateRunsTests(unittest.TestCase):
    def test_unanimous_majority_zeroes_with_full_confidence(self):
        """3/3 runs agree on the same code - the clearest possible ZERAR."""
        runs = [_run(["FUGA_AO_TEMA"]) for _ in range(3)]
        decision = _aggregate_zero_gate_runs(runs)
        self.assertEqual(decision.decision, "ZERAR")
        self.assertEqual(decision.rule_code, "FUGA_AO_TEMA")
        self.assertEqual(decision.confidence, 1.0)
        self.assertFalse(decision.requires_human_review)
        self.assertEqual(decision.evidence, "trecho citado")

    def test_two_of_three_majority_still_zeroes(self):
        """2/3 is already >= 2/3 - a majority, even though not unanimous."""
        runs = [_run(["TEXTO_INSUFICIENTE"]), _run(["TEXTO_INSUFICIENTE"]), _run([])]
        decision = _aggregate_zero_gate_runs(runs)
        self.assertEqual(decision.decision, "ZERAR")
        self.assertEqual(decision.rule_code, "TEXTO_INSUFICIENTE")
        self.assertAlmostEqual(decision.confidence, 2 / 3)
        self.assertFalse(decision.requires_human_review)

    def test_zero_votes_everywhere_is_a_clean_nao_zerar(self):
        runs = [_run([]), _run([]), _run([])]
        decision = _aggregate_zero_gate_runs(runs)
        self.assertEqual(decision.decision, "NAO_ZERAR")
        self.assertIsNone(decision.rule_code)
        self.assertEqual(decision.confidence, 1.0)
        self.assertFalse(decision.requires_human_review)
        self.assertIn("3 avaliações independentes", decision.evidence)

    def test_single_isolated_dissent_is_still_nao_zerar(self):
        """Exactly one code gets exactly one vote (1/3) - too weak on its
        own to doubt the other two clean runs, per the brief's own
        "nothing is contentious" framing for this branch."""
        runs = [_run(["TEXTO_ILEGIVEL"]), _run([]), _run([])]
        decision = _aggregate_zero_gate_runs(runs)
        self.assertEqual(decision.decision, "NAO_ZERAR")
        self.assertIsNone(decision.rule_code)
        self.assertAlmostEqual(decision.confidence, 2 / 3)
        self.assertFalse(decision.requires_human_review)

    def test_split_across_distinct_codes_escalates_to_human_review(self):
        """The brief's own worked example: a 1/3-vs-1/3-vs-1/3 split across
        THREE DIFFERENT codes, each appearing in exactly one run. No code
        reaches a majority, but the disagreement is spread across multiple
        distinct codes - genuinely ambiguous, must escalate rather than
        silently resolve to NAO_ZERAR (see this task's report for why the
        brief's literal branch conditions for outcomes 2 and 3 overlap, and
        how this implementation resolves that: outcome 2 requires AT MOST
        ONE code with any vote at all; two or more distinct codes with a
        vote is outcome 3)."""
        runs = [
            _run(["FUGA_AO_TEMA"]),
            _run(["TEXTO_INSUFICIENTE"]),
            _run(["TEXTO_ILEGIVEL"]),
        ]
        decision = _aggregate_zero_gate_runs(runs)
        self.assertEqual(decision.decision, "ENCAMINHAR_REVISAO")
        self.assertTrue(decision.requires_human_review)
        # Deterministic tie-break among equally-voted codes: earliest in
        # ZERO_GATE_CODES' own fixed order wins - FUGA_AO_TEMA is first.
        self.assertEqual(decision.rule_code, "FUGA_AO_TEMA")
        self.assertAlmostEqual(decision.confidence, 1 / 3)
        self.assertIn("FUGA_AO_TEMA", decision.evidence)
        self.assertIn("TEXTO_INSUFICIENTE", decision.evidence)
        self.assertIn("TEXTO_ILEGIVEL", decision.evidence)

    def test_two_codes_tied_at_majority_breaks_tie_by_fixed_order(self):
        """Two DIFFERENT codes both reach a 2/3 majority simultaneously -
        the deterministic tie-break documented in
        _aggregate_zero_gate_runs' docstring (earliest in ZERO_GATE_CODES'
        own order wins) must pick the same one every time, regardless of
        dict/set iteration order."""
        runs = [
            _run(["TIPO_TEXTUAL_PREDOMINANTE", "LINGUA_ESTRANGEIRA"]),
            _run(["TIPO_TEXTUAL_PREDOMINANTE", "LINGUA_ESTRANGEIRA"]),
            _run([]),
        ]
        decision = _aggregate_zero_gate_runs(runs)
        self.assertEqual(decision.decision, "ZERAR")
        # TIPO_TEXTUAL_PREDOMINANTE precedes LINGUA_ESTRANGEIRA in
        # ZERO_GATE_CODES.
        self.assertEqual(decision.rule_code, "TIPO_TEXTUAL_PREDOMINANTE")
        self.assertAlmostEqual(decision.confidence, 2 / 3)

    def test_higher_count_wins_over_an_earlier_but_less_voted_code(self):
        """The tie-break only applies among EQUALLY-voted codes - a code
        with more votes always wins outright, even if it is later in
        ZERO_GATE_CODES' fixed order."""
        runs = [
            _run(["FUGA_AO_TEMA", "TEXTO_ILEGIVEL"]),
            _run(["TEXTO_ILEGIVEL"]),
            _run(["TEXTO_ILEGIVEL"]),
        ]
        decision = _aggregate_zero_gate_runs(runs)
        self.assertEqual(decision.decision, "ZERAR")
        self.assertEqual(decision.rule_code, "TEXTO_ILEGIVEL")
        self.assertAlmostEqual(decision.confidence, 1.0)

    def test_applies_as_quoted_json_string_false_does_not_zero(self):
        """Live bug (Task 13's benchmark, 2026-10-07): the real model
        returned ``applies`` as the STRING ``"false"`` (not the JSON
        boolean ``false``) for every one of the 8 codes, on a clean,
        on-topic essay (aluno_01/Mariana). Bare truthiness
        (``if item.get("applies"):``) treats any non-empty string -
        including the string ``"false"`` - as truthy, so every code was
        wrongly counted as a 3/3 "applies" vote, and the ZERAR tie-break
        (earliest in ZERO_GATE_CODES) always landed on FUGA_AO_TEMA - 11 of
        30 real essays were incorrectly zeroed by exactly this. This exact
        reproduction (the string "false", not the bool False) is what must
        resolve to NAO_ZERAR, not ZERAR."""
        runs = [
            [{"code": code, "applies": "false", "evidence": "", "reasoning": "stub"} for code in ZERO_GATE_CODES]
            for _ in range(3)
        ]
        decision = _aggregate_zero_gate_runs(runs)
        self.assertEqual(decision.decision, "NAO_ZERAR")
        self.assertIsNone(decision.rule_code)
        self.assertEqual(decision.confidence, 1.0)
        self.assertFalse(decision.requires_human_review)

    def test_applies_as_quoted_json_string_true_still_reaches_majority(self):
        """The mirror case of the bug reproduction above - a quoted JSON
        string "true" (not the bool True) must still correctly count as a
        positive vote, so a genuine majority expressed as strings still
        triggers ZERAR. Also exercises mixed casing/whitespace
        ("True"/" true ") to confirm the coercion is robust, not just a
        literal "true" match."""
        runs = [
            [
                {"code": code, "applies": ("True" if code == "FUGA_AO_TEMA" else "false"), "evidence": "x", "reasoning": "stub"}
                for code in ZERO_GATE_CODES
            ],
            [
                {"code": code, "applies": (" true " if code == "FUGA_AO_TEMA" else "false"), "evidence": "x", "reasoning": "stub"}
                for code in ZERO_GATE_CODES
            ],
            [{"code": code, "applies": "false", "evidence": "", "reasoning": "stub"} for code in ZERO_GATE_CODES],
        ]
        decision = _aggregate_zero_gate_runs(runs)
        self.assertEqual(decision.decision, "ZERAR")
        self.assertEqual(decision.rule_code, "FUGA_AO_TEMA")
        self.assertAlmostEqual(decision.confidence, 2 / 3)

    def test_unknown_code_in_a_response_is_ignored_defensively(self):
        """A response that includes a code outside ZERO_GATE_CODES (should
        never happen if the provider respects RESPONSE_SCHEMA, but this
        function never trusts that blindly) must not crash and must not be
        counted toward anything."""
        runs = [
            _run(["FUGA_AO_TEMA"]) + [{"code": "NOT_A_REAL_CODE", "applies": True, "evidence": "x"}],
            _run(["FUGA_AO_TEMA"]),
            _run([]),
        ]
        decision = _aggregate_zero_gate_runs(runs)
        self.assertEqual(decision.decision, "ZERAR")
        self.assertEqual(decision.rule_code, "FUGA_AO_TEMA")

    def test_non_dict_item_in_a_response_is_ignored_defensively(self):
        """Same bug CLASS as _applies()'s own string-vs-boolean coercion
        (see that function's docstring), one level up: the call site only
        checks ``isinstance(assessments, list)``, never that each ELEMENT
        of that list is itself a mapping. If the model ever returns a list
        of non-dict items (e.g. a list of bare strings), ``item.get(...)``
        used to raise an uncaught AttributeError - not caught by the
        (json.JSONDecodeError, KeyError, ValueError) tuple at the call site
        in essay_correction.py, so it escaped as an unhandled 500 instead
        of becoming NEEDS_REVIEW like every other malformed-model-output
        case. A non-dict item must be skipped, exactly like an
        out-of-schema code, and must not crash."""
        runs = [
            _run(["FUGA_AO_TEMA"]) + ["not-a-dict-assessment"],
            _run(["FUGA_AO_TEMA"]),
            _run([]),
        ]
        decision = _aggregate_zero_gate_runs(runs)
        self.assertEqual(decision.decision, "ZERAR")
        self.assertEqual(decision.rule_code, "FUGA_AO_TEMA")

    def test_run_made_entirely_of_non_dict_items_contributes_no_votes(self):
        """The whole assessments list for one run is non-dict items (e.g.
        the model returned a list of strings instead of objects) - that
        run must contribute zero valid assessments, same as a run whose
        assessments list is simply empty, never a crash."""
        runs = [["oops", "not", "a", "dict"], _run([]), _run([])]
        decision = _aggregate_zero_gate_runs(runs)
        self.assertEqual(decision.decision, "NAO_ZERAR")
        self.assertIsNone(decision.rule_code)

    def test_duplicate_code_within_one_run_is_counted_once(self):
        """Defensive: if a single run's assessments list repeats the same
        code twice, it must contribute at most one vote, not two."""
        run_with_duplicate = _run(["FUGA_AO_TEMA"]) + [
            {"code": "FUGA_AO_TEMA", "applies": True, "evidence": "trecho citado de novo"}
        ]
        runs = [run_with_duplicate, _run([]), _run([])]
        decision = _aggregate_zero_gate_runs(runs)
        # Only 1/3 overall (the duplicate must not inflate it to 2/3) -
        # a single isolated dissent, so NAO_ZERAR.
        self.assertEqual(decision.decision, "NAO_ZERAR")

    def test_empty_runs_sequence_raises(self):
        with self.assertRaises(ValueError):
            _aggregate_zero_gate_runs([])


class EvaluateZeroGateAsyncTests(unittest.IsolatedAsyncioTestCase):
    """The thin async wrapper: fires 3 calls, parses each response's
    ``assessments``, feeds them to the pure aggregator above. No DB
    involved - essay_zero_gate.py has none."""

    class _FakeProvider:
        def __init__(self, *, responses=None, raise_error=None, malformed_text=None):
            self._responses = responses
            self._raise_error = raise_error
            self._malformed_text = malformed_text
            self.requests: list[object] = []

        async def generate(self, request):
            self.requests.append(request)
            if self._raise_error is not None:
                raise self._raise_error
            if self._malformed_text is not None:
                return TextGenerationResult(text=self._malformed_text, provider="fake", model="fake-model")
            idx = len(self.requests) - 1
            payload = self._responses[idx % len(self._responses)]
            return TextGenerationResult(text=json.dumps(payload), provider="fake", model="fake-model")

    async def test_majority_across_three_real_calls_zeroes(self):
        responses = [
            {"assessments": _run(["FUGA_AO_TEMA"])},
            {"assessments": _run(["FUGA_AO_TEMA"])},
            {"assessments": _run([])},
        ]
        provider = self._FakeProvider(responses=responses)

        decision = await evaluate_zero_gate(
            canonical_text="texto qualquer da redacao",
            essay_statement="tema qualquer",
            rubric_payload={"rubric_version": "test", "competencies": []},
            text_provider=provider,
        )

        self.assertIsInstance(decision, ZeroGateDecision)
        self.assertEqual(decision.decision, "ZERAR")
        self.assertEqual(decision.rule_code, "FUGA_AO_TEMA")
        self.assertEqual(len(provider.requests), 3)
        # Deliberately no seed on any of the 3 calls - see the module
        # docstring: a fixed seed would hide exactly the sampling variance
        # this design exists to average out.
        for request in provider.requests:
            self.assertIsNone(request.seed)
            self.assertIn("ZERO_GATE_RULES:", request.prompt)

    async def test_nothing_applies_across_three_real_calls_does_not_zero(self):
        responses = [{"assessments": _run([])}]
        provider = self._FakeProvider(responses=responses)

        decision = await evaluate_zero_gate(
            canonical_text="uma redacao normal e completa sobre o tema",
            essay_statement="tema qualquer",
            rubric_payload={"rubric_version": "test", "competencies": []},
            text_provider=provider,
        )

        self.assertEqual(decision.decision, "NAO_ZERAR")
        self.assertFalse(decision.requires_human_review)

    async def test_provider_error_propagates(self):
        provider = self._FakeProvider(raise_error=RuntimeError("boom"))
        with self.assertRaises(RuntimeError):
            await evaluate_zero_gate(
                canonical_text="x", essay_statement="y",
                rubric_payload={}, text_provider=provider,
            )

    async def test_malformed_json_propagates_as_json_decode_error(self):
        provider = self._FakeProvider(malformed_text="not json at all")
        with self.assertRaises(json.JSONDecodeError):
            await evaluate_zero_gate(
                canonical_text="x", essay_statement="y",
                rubric_payload={}, text_provider=provider,
            )

    async def test_non_list_assessments_raises_value_error(self):
        provider = self._FakeProvider(responses=[{"assessments": "not-a-list"}])
        with self.assertRaises(ValueError):
            await evaluate_zero_gate(
                canonical_text="x", essay_statement="y",
                rubric_payload={}, text_provider=provider,
            )

    async def test_missing_assessments_key_raises_key_error(self):
        provider = self._FakeProvider(responses=[{"not_assessments": []}])
        with self.assertRaises(KeyError):
            await evaluate_zero_gate(
                canonical_text="x", essay_statement="y",
                rubric_payload={}, text_provider=provider,
            )


if __name__ == "__main__":
    unittest.main()
