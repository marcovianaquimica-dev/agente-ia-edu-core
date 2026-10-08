# src/agente_ia_edu/services/essay_zero_gate.py
"""Zero Gate: evaluates the eight ANULA_REDACAO (whole-essay-zero) codes
BEFORE C1-C5 scoring, with its own responsibility - never depends only on
phase 1 having already raised a candidate (spec Fase C, 2026-10-06).

Replaces EssayCorrectionService._review_anula_redacao_alerts (phase 2b,
alert_review_v1-based): that call could only CONFIRM or REJECT a candidate
phase 1 already raised - it could never discover a zero-situation phase 1
missed. This module calls a new, independent prompt
(essay_prompts/zero_gate_v1.py) that reads the FULL essay text and judges
all eight codes from scratch every time.

The mechanism - 3 independent samples, majority vote per code - is a direct
answer to Task 10's live diagnosis
(docs/superpowers/plans/2026-10-06-motor-redacao-quality-zero-gate.diagnostico-c1.md):
alert_review_v1, called 5 times live against two real essays with identical
input, confirmed the SAME FUGA_AO_TEMA evidence in some runs and rejected it
in others (Sabrina 2/5 confirmed, Henrique 4/5) - genuine LLM sampling
variance around an inherently graded piece of evidence ("trata
PRINCIPALMENTE de X"), not a directional calibration bug. Narrowing the
decision into one small, focused call (the same fix that worked for
competency_scoring_v1/v2) does NOT fix this on its own - alert_review_v1 IS
already that narrowing, and it still wasn't consistent. What this module
adds on top is self-consistency via repeated sampling: call the SAME
single-decision prompt 3 times with no fixed seed (a fixed seed would hide
exactly the variance the diagnosis found - see zero_gate_v1.py's docstring),
then aggregate. A clear per-code majority (>= 2 of 3 runs) across the three
independent reads is far less likely to be sampling noise than any single
run's verdict; a genuine 3-way split that never happened before (because
there was never more than one sample to disagree with itself) becomes its
own first-class, auditable outcome - ENCAMINHAR_REVISAO - instead of
silently falling through to a normal pedagogical grade.

Public contract: ZeroGateDecision (decision/rule_code/evidence/confidence/
requires_human_review/rule_version) and the async `evaluate_zero_gate`.
`_aggregate_zero_gate_runs` is the pure, provider-free aggregation core -
kept separate so the three-way decision logic (the part most worth testing
exhaustively) never needs a mocked provider or asyncio to exercise.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

from ..essay_prompts import zero_gate_v1
from ..providers.models import TextGenerationRequest

#: Always 3 - see module docstring. Not a tunable knob: the aggregation
#: thresholds in _aggregate_zero_gate_runs (">= 2 of N", "<= 1 code with any
#: vote") are the ones Task 11's design specified for exactly this N and
#: would need re-deriving (not merely re-parameterizing) for a different N.
_NUM_SAMPLES = 3

_ZERO_GATE_VERSION = "zero_gate_v1"

#: Same eight codes, same order, as zero_gate_v1.ZERO_GATE_CODES (re-exported
#: here so callers that only import this module - not the prompt module -
#: still have the canonical order available).
ZERO_GATE_CODES: tuple[str, ...] = zero_gate_v1.ZERO_GATE_CODES


@dataclass(frozen=True)
class ZeroGateDecision:
    decision: str  # "ZERAR" | "NAO_ZERAR" | "ENCAMINHAR_REVISAO"
    rule_code: str | None
    evidence: str
    confidence: float | None
    requires_human_review: bool
    rule_version: str = _ZERO_GATE_VERSION


def _applies(value: object) -> bool:
    """Coerce an ``assessments[i]["applies"]`` value to a real bool.

    Live bug (found by Task 13's benchmark, 2026-10-07): the real model does
    NOT reliably return ``applies`` as a native JSON boolean - against a
    real essay (aluno_01/Mariana, clearly on-topic), it returned the
    STRING ``"false"`` for every one of the 8 codes. Python's bare
    truthiness (``if item.get("applies"):``) treats any non-empty string -
    including the string ``"false"`` - as truthy, so every code was being
    counted as a 3/3 "applies" vote on every essay that reached this
    function, and the ZERAR tie-break (earliest in :data:`ZERO_GATE_CODES`)
    always landed on FUGA_AO_TEMA. 11 of 30 real essays in that benchmark -
    all previously clean, on-topic, correctly-scored - were zeroed by this.
    Handles the three shapes seen or plausible from an LLM's JSON output:
    a real bool, a string ("true"/"false", any case/whitespace), or
    anything else via plain ``bool()`` as a last-resort fallback."""
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        return value.strip().lower() == "true"
    return bool(value)


def _meets_majority(count: int, n_runs: int) -> bool:
    """``count`` out of ``n_runs`` runs is a majority when it is at least
    2/3 - computed as ``count * 3 >= n_runs * 2`` to stay in integers. For
    the fixed ``n_runs == 3`` this module always uses, that is exactly
    ``count >= 2``."""
    return count * 3 >= n_runs * 2


def _aggregate_zero_gate_runs(
    runs: Sequence[Sequence[Mapping[str, Any]]],
) -> ZeroGateDecision:
    """Pure aggregation core - no provider, no asyncio. ``runs`` is a
    sequence of already-parsed assessment lists (one per sample call), each
    entry shaped like ``{"code": str, "applies": bool, "evidence": str,
    "reasoning": str}`` - except ``applies`` is read through :func:`_applies`
    rather than trusted to actually BE a bool (live bug, see that
    function's docstring: the real model sometimes returns the JSON string
    ``"false"`` instead of the boolean ``false``, which bare truthiness
    would misread as true). Any code outside :data:`ZERO_GATE_CODES`, or a
    second entry for a code already seen within the same run, is ignored
    defensively (the prompt asks for exactly the eight known codes once
    each, but this function never trusts that blindly).

    Three outcomes, by how many of the ``n_runs`` samples said
    ``applies=true`` for each of the eight codes (``n_runs`` is always 3 in
    production - see ``_NUM_SAMPLES`` - but this function stays generic over
    ``len(runs)`` since that is what makes it independently testable):

    1. **ZERAR** - at least one code reaches a majority (``_meets_majority``,
       >= 2/3). When more than one code clears the bar, the one with the
       highest agreement count wins; ties are broken by :data:`ZERO_GATE_CODES`'s
       own fixed order (earlier code wins) - a simple, fully deterministic
       tie-break, documented here instead of the brief's vaguer "first
       position in a run" suggestion, since the brief itself says this
       should rarely matter in practice.

    2. **NAO_ZERAR** - no code reaches a majority, AND at most one code has
       ANY positive vote at all (0 codes voted at all, or exactly one code
       got exactly one vote out of three). Both are a clean "nothing
       applies": either unanimous rejection, or a single isolated dissenting
       sample too weak on its own to doubt the other two.

    3. **ENCAMINHAR_REVISAO** - no code reaches a majority, but two or more
       DIFFERENT codes each received at least one vote. This is the one
       genuinely ambiguous shape the brief's own worked example describes
       ("a 1/3-vs-1/3-vs-1/3 split across DIFFERENT codes each only
       appearing once") - the three independent samples did not converge on
       "nothing applies" cleanly, they each raised a different suspicion.
       Note: read literally, the brief's own text for outcomes 2 and 3
       overlaps ("every code has 0/3 or 1/3" is also true of the branch-3
       example, since every code there also sits at 0 or 1 out of 3) - this
       implementation resolves that overlap by making the split condition
       about the COUNT OF DISTINCT CODES with any vote (<=1 -> outcome 2,
       >=2 -> outcome 3), which is consistent with the branch-3 worked
       example and keeps the three outcomes mutually exclusive and jointly
       exhaustive. Flagged in Task 11's report for the design owner to
       confirm or correct.
    """
    n_runs = len(runs)
    if n_runs == 0:
        raise ValueError("_aggregate_zero_gate_runs requires at least one run")

    counts: dict[str, int] = {code: 0 for code in ZERO_GATE_CODES}
    evidence_by_code: dict[str, str] = {}
    for assessments in runs:
        seen_this_run: set[str] = set()
        for item in assessments:
            code = item.get("code")
            if code not in counts or code in seen_this_run:
                continue
            seen_this_run.add(code)
            if _applies(item.get("applies")):
                counts[code] += 1
                evidence = item.get("evidence")
                if code not in evidence_by_code and evidence:
                    evidence_by_code[code] = evidence

    majority_codes = [code for code in ZERO_GATE_CODES if _meets_majority(counts[code], n_runs)]
    if majority_codes:
        best = sorted(majority_codes, key=lambda c: (-counts[c], ZERO_GATE_CODES.index(c)))[0]
        return ZeroGateDecision(
            decision="ZERAR",
            rule_code=best,
            evidence=evidence_by_code.get(best, ""),
            confidence=counts[best] / n_runs,
            requires_human_review=False,
        )

    codes_with_any_vote = [code for code in ZERO_GATE_CODES if counts[code] >= 1]
    if len(codes_with_any_vote) <= 1:
        if not codes_with_any_vote:
            confidence = 1.0
        else:
            confidence = 1.0 - (counts[codes_with_any_vote[0]] / n_runs)
        return ZeroGateDecision(
            decision="NAO_ZERAR",
            rule_code=None,
            evidence=(
                f"nenhuma situação especial identificada em {n_runs} "
                "avaliações independentes"
            ),
            confidence=confidence,
            requires_human_review=False,
        )

    max_count = max(counts[code] for code in codes_with_any_vote)
    tied_at_max = [code for code in codes_with_any_vote if counts[code] == max_count]
    closest = sorted(tied_at_max, key=lambda c: ZERO_GATE_CODES.index(c))[0]
    summary = ", ".join(f"{code} ({counts[code]}/{n_runs})" for code in codes_with_any_vote)
    return ZeroGateDecision(
        decision="ENCAMINHAR_REVISAO",
        rule_code=closest,
        evidence=f"avaliações independentes divergiram entre si: {summary}",
        confidence=max_count / n_runs,
        requires_human_review=True,
    )


async def evaluate_zero_gate(
    *, canonical_text: str, essay_statement: str, rubric_payload: Mapping[str, Any],
    text_provider,
) -> ZeroGateDecision:
    """Thin async wrapper: fires :data:`_NUM_SAMPLES` independent calls to
    ``zero_gate_v1.build_prompt`` (deliberately no ``seed`` - see module
    docstring), parses each response's ``assessments`` list, and feeds them
    to :func:`_aggregate_zero_gate_runs`.

    Raises whatever the provider or ``json.loads``/shape-checking raises
    (``ProviderError``, ``json.JSONDecodeError``, ``KeyError``,
    ``ValueError``) - never swallowed here. The caller
    (EssayCorrectionService._run_ai) already has the same error handling
    around the other phase-2 calls and turns any of these into the same
    NEEDS_REVIEW failure_reason shape every other AI-side failure uses.
    """

    async def _run_once() -> list[dict[str, Any]]:
        prompt_text = zero_gate_v1.build_prompt(
            essay_statement=essay_statement, canonical_text=canonical_text,
            rubric=rubric_payload,
        )
        result = await text_provider.generate(TextGenerationRequest(prompt=prompt_text))
        payload = json.loads(result.text)
        assessments = payload["assessments"]
        if not isinstance(assessments, list):
            raise ValueError(
                f"zero gate returned a non-list assessments: {assessments!r}"
            )
        return assessments

    runs = await asyncio.gather(*(_run_once() for _ in range(_NUM_SAMPLES)))
    return _aggregate_zero_gate_runs(runs)


__all__ = ["ZeroGateDecision", "ZERO_GATE_CODES", "evaluate_zero_gate"]
