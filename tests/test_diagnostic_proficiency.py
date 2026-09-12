import asyncio
import unittest

from agente_ia_edu.services.proficiency import ContentCoverageState, DiagnosticConcentrationPolicy, DiagnosticCoveragePolicy, DiagnosticDecisionPolicy, DiagnosticTimePolicy, GlobalDiagnosticCoveragePolicy, PedagogicalEvidence, PrerequisiteHypothesisPolicy, SimpleProficiencyEstimator


class TestSimpleProficiencyEstimator(unittest.TestCase):
    def test_estimate_is_deterministic_and_difficulty_aware(self):
        estimator = SimpleProficiencyEstimator()
        evidence = [
            PedagogicalEvidence(is_correct=True, difficulty_level="EASY"),
            PedagogicalEvidence(is_correct=True, difficulty_level="HARD"),
            PedagogicalEvidence(is_correct=False, difficulty_level="MEDIUM"),
        ]
        first = estimator.estimate(evidence)
        second = estimator.estimate(evidence)
        self.assertEqual(first, second)
        self.assertEqual(first.evidence_count, 3)
        self.assertGreater(first.mastery_score, 60)
        self.assertLess(first.confidence, 1)

    def test_single_error_is_low_confidence_evidence_not_a_final_claim(self):
        estimate = SimpleProficiencyEstimator().estimate([
            PedagogicalEvidence(is_correct=False, difficulty_level="HARD")
        ])
        self.assertEqual(estimate.mastery_score, 0.0)
        self.assertEqual(estimate.confidence, 0.1)
        self.assertEqual(estimate.band, "NEEDS_DEVELOPMENT")

    def test_coverage_requires_diverse_consistent_evidence(self):
        estimator = SimpleProficiencyEstimator()
        policy = DiagnosticCoveragePolicy()
        sufficient = policy.assess([
            PedagogicalEvidence(True, "EASY"),
            PedagogicalEvidence(True, "MEDIUM"),
            PedagogicalEvidence(True, "HARD"),
        ], estimator)
        inconsistent = policy.assess([
            PedagogicalEvidence(True, "EASY"),
            PedagogicalEvidence(False, "MEDIUM"),
            PedagogicalEvidence(True, "HARD"),
        ], estimator)
        self.assertEqual(sufficient.status, "CONSOLIDATED")
        self.assertEqual(inconsistent.status, "IN_PROGRESS")
        self.assertTrue(inconsistent.is_inconsistent)

    def test_consistent_low_evidence_requests_prerequisite_investigation(self):
        state = DiagnosticCoveragePolicy().assess([
            PedagogicalEvidence(False, "EASY"),
            PedagogicalEvidence(False, "MEDIUM"),
            PedagogicalEvidence(False, "HARD"),
        ], SimpleProficiencyEstimator())
        self.assertEqual(state.status, "POSSIBLE_GAP")
        self.assertTrue(state.needs_prerequisite_investigation)

    def test_global_coverage_prioritizes_underrepresented_discipline(self):
        estimator = SimpleProficiencyEstimator()
        coverage = DiagnosticCoveragePolicy()
        global_policy = GlobalDiagnosticCoveragePolicy()
        mastered = coverage.assess([PedagogicalEvidence(True, level) for level in ("EASY", "MEDIUM", "HARD")], estimator)
        missing = coverage.assess([], estimator)
        summary = global_policy.summarize({"Matemática": [mastered], "Biologia": [missing]})
        self.assertEqual(global_policy.prioritize_disciplines(summary), ("Biologia", "Matemática"))
        self.assertEqual(summary.residual_uncertainty, ("Biologia",))

    def test_global_stop_requires_coverage_and_honors_maximum(self):
        estimator = SimpleProficiencyEstimator()
        coverage = DiagnosticCoveragePolicy()
        policy = GlobalDiagnosticCoveragePolicy(minimum_evidence=6, maximum_evidence=8, target_confidence=0.2)
        mastered = coverage.assess([PedagogicalEvidence(True, level) for level in ("EASY", "MEDIUM", "HARD")], estimator)
        incomplete = coverage.assess([], estimator)
        insufficient = policy.summarize({"Química": [mastered], "Física": [incomplete]})
        self.assertEqual(policy.should_stop_global_diagnostic(insufficient, 6), (False, "CONTINUE"))
        self.assertEqual(policy.should_stop_global_diagnostic(insufficient, 8), (True, "MAXIMUM_EVIDENCE_REACHED"))

    def test_global_summary_is_deterministic_and_tracks_critical_investigation(self):
        estimator = SimpleProficiencyEstimator()
        coverage = DiagnosticCoveragePolicy()
        policy = GlobalDiagnosticCoveragePolicy()
        gap = coverage.assess([PedagogicalEvidence(False, level) for level in ("EASY", "MEDIUM", "HARD")], estimator)
        summary_a = policy.summarize({"Física": [gap], "Química": [gap]})
        summary_b = policy.summarize({"Química": [gap], "Física": [gap]})
        self.assertEqual(summary_a, summary_b)
        self.assertEqual(policy.prioritize_disciplines(summary_a), ("Física", "Química"))

    def test_decision_policy_finishes_on_sufficiency_and_hard_time_limit(self):
        estimator = SimpleProficiencyEstimator()
        coverage = DiagnosticCoveragePolicy().assess([
            PedagogicalEvidence(True, "EASY"), PedagogicalEvidence(True, "MEDIUM"), PedagogicalEvidence(True, "HARD"),
        ], estimator)
        policy = DiagnosticDecisionPolicy(discipline=DiagnosticTimePolicy(10, 12, 15))
        self.assertEqual(policy.decide(mode="DISCIPLINE", elapsed_seconds=5, questions_asked=3, coverage=coverage).action, "FINISH_SUFFICIENT")
        self.assertEqual(policy.decide(mode="DISCIPLINE", elapsed_seconds=15, questions_asked=1, coverage=coverage).reason, "HARD_LIMIT_REACHED")

    def test_decision_policy_continues_at_soft_limit_for_high_value_evidence(self):
        estimator = SimpleProficiencyEstimator()
        coverage = DiagnosticCoveragePolicy().assess([PedagogicalEvidence(True, "EASY")], estimator)
        policy = DiagnosticDecisionPolicy(discipline=DiagnosticTimePolicy(10, 12, 15))
        decision = policy.decide(mode="DISCIPLINE", elapsed_seconds=12, questions_asked=1, coverage=coverage)
        self.assertEqual(decision.action, "CONTINUE")
        self.assertTrue(decision.high_information_only)

    def test_decision_policy_investigates_consistent_prerequisite_gap(self):
        estimator = SimpleProficiencyEstimator()
        coverage = DiagnosticCoveragePolicy().assess([
            PedagogicalEvidence(False, "EASY"), PedagogicalEvidence(False, "MEDIUM"), PedagogicalEvidence(False, "HARD"),
        ], estimator)
        decision = DiagnosticDecisionPolicy().decide(mode="GLOBAL", elapsed_seconds=0, questions_asked=3, coverage=coverage)
        self.assertEqual(decision.action, "CONTINUE_PREREQUISITE_INVESTIGATION")

    def test_prerequisite_hypothesis_requires_gradual_support_or_refutation(self):
        policy = PrerequisiteHypothesisPolicy()
        hypothesis = policy.create(target_content_id="target", prerequisite_content_id="prereq", timestamp="t1", evidence_id="target-evidence")
        self.assertEqual(hypothesis["status"], "SUSPECTED")
        hypothesis = policy.update(hypothesis, is_correct=False, is_unknown=False, timestamp="t2", evidence_id="p1")
        self.assertEqual(hypothesis["status"], "UNDER_INVESTIGATION")
        supported = policy.update(hypothesis, is_correct=False, is_unknown=False, timestamp="t3", evidence_id="p2")
        self.assertEqual(supported["status"], "SUPPORTED")
        contradictory = policy.update(hypothesis, is_correct=True, is_unknown=False, timestamp="t3", evidence_id="c1")
        refuted = policy.update(contradictory, is_correct=True, is_unknown=False, timestamp="t4", evidence_id="c2")
        self.assertEqual(refuted["status"], "REFUTED")

    def test_unknown_prerequisite_evidence_does_not_count_as_support_or_refutation(self):
        policy = PrerequisiteHypothesisPolicy()
        hypothesis = policy.create(target_content_id="target", prerequisite_content_id="prereq", timestamp="t1", evidence_id="e1")
        unknown = policy.update(hypothesis, is_correct=False, is_unknown=True, timestamp="t2", evidence_id="e2")
        self.assertEqual(unknown["status"], "INCONCLUSIVE")
        self.assertEqual(unknown["supporting_evidence_count"], 0)
        self.assertEqual(unknown["contradictory_evidence_count"], 0)
        self.assertEqual(unknown["unknown_evidence_count"], 1)

    def test_concentration_policy_switches_content_and_discipline_deterministically(self):
        policy = DiagnosticConcentrationPolicy(max_consecutive_same_content=2, max_consecutive_same_discipline=2)
        self.assertEqual(policy.decision(
            recent_content_ids=["content-a", "content-a"], recent_discipline_ids=["math", "math"],
            candidate_content_id="content-a", candidate_discipline_id="math",
            has_alternative_content=True, has_alternative_discipline=True,
        ), "SWITCH_CONTENT")
        self.assertEqual(policy.decision(
            recent_content_ids=["content-a", "content-b"], recent_discipline_ids=["math", "math"],
            candidate_content_id="content-c", candidate_discipline_id="math",
            has_alternative_content=True, has_alternative_discipline=True,
        ), "SWITCH_DISCIPLINE")
        self.assertEqual(policy.decision(
            recent_content_ids=["content-a", "content-a"], recent_discipline_ids=["math", "math"],
            candidate_content_id="content-a", candidate_discipline_id="math",
            has_alternative_content=False, has_alternative_discipline=False,
        ), "CONTINUE_SAME_CONTENT")