"""Service-layer coverage for agente_ia_edu.services.learning_path_policies.

Pure, dependency-free dataclasses/functions (no DB, no async) - the module
already gets substantial indirect coverage through test_learning_path.py
(ConfidencePolicy/MasteryCalculationPolicy/most of
DifficultyProgressionPolicy.recommend_next_level) and test_phase6_domain_map.py
(NextBestActionPolicy, via domain_map.py's real caller), which is why
baseline coverage was already 90%. This file targets exactly what those two
leave out:

- NextBestActionPolicy.decide() with an empty candidate list (line 79).
- NextBestActionPolicy._evaluate's STUDY_PREREQUISITE branch (114-120): a
  relevant prerequisite with enough evidence/confidence to clear the first
  ("COMPLETE_MISSING_EVIDENCE") branch, but whose mastery_score is still
  below gap_score.
- The DECLINING-trend REVIEW_CONTENT branch (159-162): reached only when
  mastery is already >= developing_score (so REINFORCE_CONTENT doesn't fire
  first) and trend == "DECLINING".
- The MASTERY_MAINTENANCE fallback else branch (178-181): mastery_score is
  high (>= mastered_score) but confidence is below 0.6, so the
  WELL_EVIDENCED_MASTERY/ADVANCE_CONTENT branch's `and confidence >= 0.6`
  clause fails and execution falls through to the final `else`.
- DifficultyProgressionPolicy.recommend_next_level's two "stay at this
  level while struggling" branches: MEDIUM with score in [50, min_score)
  (376) and HARD with score in [40, min_score) (383).

Left deliberately uncovered, because they're unreachable through any real
caller:
- MasteryThresholds.get_min_score_for_level's trailing `return 70.0`
  (around line 242) and DifficultyProgressionPolicy.recommend_next_level's
  trailing `return current_level` (line 385): both are fallbacks for a
  `level` that matches none of EASY/MEDIUM/HARD in an if/elif chain, but
  `DifficultyLevel` is a 3-member enum and the chain already handles all
  three - same "enum exhaustiveness makes the fallback dead" shape as
  content_authoring.py's MaterialWorkflowStatus fallback (leva 5). Passing
  anything else would require a value that isn't a DifficultyLevel member,
  which no caller in this codebase does or type-checks would allow.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.learning_path_policies import (
    DifficultyLevel,
    DifficultyProgressionPolicy,
    NextBestActionCandidate,
    NextBestActionPolicy,
)


class NextBestActionPolicyDecideTests(unittest.TestCase):
    def test_decide_with_no_candidates_returns_none(self):
        policy = NextBestActionPolicy()
        self.assertIsNone(policy.decide([]))


class NextBestActionPolicyEvaluateBranchTests(unittest.TestCase):
    def setUp(self):
        self.policy = NextBestActionPolicy()

    def test_prerequisite_with_evidence_but_low_mastery_triggers_study_prerequisite(self):
        candidate = NextBestActionCandidate(
            content_node_id="content-1",
            content_name="Equações do 2º grau",
            mastery_score=None,
            confidence=0.0,
            evidence_count=0,
            prerequisite_node_id="prereq-1",
            prerequisite_name="Equações do 1º grau",
            prerequisite_mastery_score=25.0,  # < default gap_score (40.0)
            prerequisite_confidence=0.8,  # >= minimum_confidence: 1st branch doesn't fire
            prerequisite_evidence_count=5,  # > 0: 1st branch doesn't fire
            prerequisite_hypothesis_status="CONFIRMED",
        )
        decision = self.policy.decide([candidate])
        self.assertEqual(decision.action, "STUDY_PREREQUISITE")
        self.assertEqual(decision.target_content_node_id, "prereq-1")
        self.assertEqual(decision.target_content_name, "Equações do 1º grau")
        self.assertEqual(decision.related_content_node_id, "content-1")
        self.assertIn("PREREQUISITE_GAP", decision.factors)
        self.assertIn("Equações do 1º grau", decision.reason)

    def test_declining_trend_with_solid_mastery_triggers_review_content(self):
        candidate = NextBestActionCandidate(
            content_node_id="content-2",
            content_name="Estequiometria",
            mastery_score=75.0,  # >= developing_score (70.0): REINFORCE doesn't fire
            confidence=0.8,  # >= minimum_confidence
            evidence_count=12,
            trend="DECLINING",
        )
        decision = self.policy.decide([candidate])
        self.assertEqual(decision.action, "REVIEW_CONTENT")
        self.assertIn("DECLINING_TREND", decision.factors)
        self.assertIn("em queda", decision.reason)

    def test_high_mastery_low_confidence_falls_to_maintenance_review(self):
        candidate = NextBestActionCandidate(
            content_node_id="content-3",
            content_name="Termoquímica",
            mastery_score=90.0,  # >= mastered_score (85.0)
            confidence=0.4,  # < 0.6: ADVANCE_CONTENT's `and confidence >= 0.6` fails
            evidence_count=8,
            trend="STABLE",
        )
        decision = self.policy.decide([candidate])
        self.assertEqual(decision.action, "REVIEW_CONTENT")
        self.assertIn("MASTERY_MAINTENANCE", decision.factors)
        self.assertIn("revisão breve", decision.reason)


class DifficultyProgressionStayStrugglingTests(unittest.TestCase):
    def setUp(self):
        self.policy = DifficultyProgressionPolicy()

    def test_medium_stays_medium_when_struggling_but_not_regressing(self):
        # score in [50, min_medium_score=70): not enough to advance to HARD,
        # not low enough (< 50) to regress to EASY - stays MEDIUM.
        next_level = self.policy.recommend_next_level(
            DifficultyLevel.MEDIUM,
            mastery_score=55.0,
            confidence=0.7,
            questions_answered=12,
        )
        self.assertEqual(next_level, DifficultyLevel.MEDIUM)

    def test_hard_stays_hard_when_struggling_but_not_regressing(self):
        # score in [40, min_hard_score=60): not enough to confirm HARD
        # mastery, not low enough (< 40) to regress to MEDIUM - stays HARD.
        next_level = self.policy.recommend_next_level(
            DifficultyLevel.HARD,
            mastery_score=45.0,
            confidence=0.8,
            questions_answered=20,
        )
        self.assertEqual(next_level, DifficultyLevel.HARD)


if __name__ == "__main__":
    unittest.main()
