"""Model-agnostic pedagogical proficiency estimation contracts."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol, Sequence


@dataclass(frozen=True)
class PedagogicalEvidence:
    is_correct: bool
    difficulty_level: str


@dataclass(frozen=True)
class ProficiencyEstimate:
    mastery_score: float
    confidence: float
    band: str
    evidence_count: int


class ProficiencyEstimator(Protocol):
    def estimate(self, evidence: Sequence[PedagogicalEvidence]) -> ProficiencyEstimate: ...


class SimpleProficiencyEstimator:
    """Deterministic baseline estimator; TRI/MIRT can implement this protocol later."""

    _WEIGHTS = {"EASY": 1.0, "MEDIUM": 1.2, "HARD": 1.4}

    def estimate(self, evidence: Sequence[PedagogicalEvidence]) -> ProficiencyEstimate:
        if not evidence:
            return ProficiencyEstimate(0.0, 0.0, "NEEDS_DEVELOPMENT", 0)
        total_weight = sum(self._WEIGHTS.get(item.difficulty_level, 1.0) for item in evidence)
        correct_weight = sum(self._WEIGHTS.get(item.difficulty_level, 1.0) for item in evidence if item.is_correct)
        score = round((correct_weight / total_weight) * 100, 2)
        confidence = round(min(1.0, len(evidence) / 10), 4)
        band = (
            "NEEDS_DEVELOPMENT" if score < 40 else "INITIAL_DEVELOPMENT" if score < 60
            else "DEVELOPING" if score < 75 else "CONSISTENT_MASTERY" if score < 90
            else "CONSOLIDATED_MASTERY"
        )
        return ProficiencyEstimate(score, confidence, band, len(evidence))


@dataclass(frozen=True)
class ContentCoverageState:
    status: str
    estimate: ProficiencyEstimate
    is_inconsistent: bool
    needs_prerequisite_investigation: bool


@dataclass(frozen=True)
class DiagnosticCoveragePolicy:
    """Configurable sufficiency policy, separate from any scoring model."""

    minimum_evidence: int = 3
    minimum_difficulty_diversity: int = 2

    def assess(
        self,
        evidence: Sequence[PedagogicalEvidence],
        estimator: ProficiencyEstimator,
    ) -> ContentCoverageState:
        estimate = estimator.estimate(evidence)
        difficulties = {item.difficulty_level for item in evidence}
        outcomes = {item.is_correct for item in evidence}
        inconsistent = len(outcomes) > 1
        enough = (
            len(evidence) >= self.minimum_evidence
            and len(difficulties) >= self.minimum_difficulty_diversity
            and not inconsistent
        )
        if not evidence:
            status = "NOT_EVALUATED"
        elif not enough:
            status = "IN_PROGRESS" if inconsistent else "INSUFFICIENT_EVIDENCE"
        elif estimate.mastery_score >= 90:
            status = "CONSOLIDATED"
        elif estimate.mastery_score <= 40:
            status = "POSSIBLE_GAP"
        else:
            status = "SUFFICIENT_EVIDENCE"
        return ContentCoverageState(
            status=status,
            estimate=estimate,
            is_inconsistent=inconsistent,
            needs_prerequisite_investigation=(
                len(evidence) >= self.minimum_evidence
                and estimate.mastery_score <= 40
            ),
        )


@dataclass(frozen=True)
class DisciplineCoverage:
    discipline: str
    evidence_count: int
    sufficient_content_count: int
    content_count: int
    confidence: float
    coverage_percent: float
    has_critical_investigation: bool


@dataclass(frozen=True)
class GlobalDiagnosticSummary:
    overall_coverage: float
    overall_confidence: float
    disciplines: tuple[DisciplineCoverage, ...]
    residual_uncertainty: tuple[str, ...]


@dataclass(frozen=True)
class GlobalDiagnosticCoveragePolicy:
    """Pure policy for global balance and stopping; values are explicit defaults."""

    minimum_evidence: int = 6
    maximum_evidence: int = 20
    minimum_discipline_coverage: float = 50.0
    target_confidence: float = 0.6

    def summarize(self, content_states: dict[str, Sequence[ContentCoverageState]]) -> GlobalDiagnosticSummary:
        disciplines: list[DisciplineCoverage] = []
        uncertainty: list[str] = []
        for discipline in sorted(content_states):
            states = content_states[discipline]
            count = len(states)
            sufficient = sum(state.status in {"SUFFICIENT_EVIDENCE", "CONSOLIDATED", "POSSIBLE_GAP"} for state in states)
            evidence = sum(state.estimate.evidence_count for state in states)
            confidence = round(sum(state.estimate.confidence for state in states) / count, 4) if count else 0.0
            coverage = round((sufficient / count) * 100, 2) if count else 0.0
            critical = any(state.needs_prerequisite_investigation for state in states)
            disciplines.append(DisciplineCoverage(discipline, evidence, sufficient, count, confidence, coverage, critical))
            if coverage < self.minimum_discipline_coverage:
                uncertainty.append(discipline)
        overall_coverage = round(sum(item.coverage_percent for item in disciplines) / len(disciplines), 2) if disciplines else 0.0
        overall_confidence = round(sum(item.confidence for item in disciplines) / len(disciplines), 4) if disciplines else 0.0
        return GlobalDiagnosticSummary(overall_coverage, overall_confidence, tuple(disciplines), tuple(uncertainty))

    def prioritize_disciplines(self, summary: GlobalDiagnosticSummary) -> tuple[str, ...]:
        """Lowest coverage first; critical investigations temporarily take precedence."""
        ordered = sorted(
            summary.disciplines,
            key=lambda item: (not item.has_critical_investigation, item.coverage_percent, item.confidence, item.discipline),
        )
        return tuple(item.discipline for item in ordered)

    def should_stop_global_diagnostic(self, summary: GlobalDiagnosticSummary, total_evidence: int) -> tuple[bool, str]:
        if total_evidence >= self.maximum_evidence:
            return True, "MAXIMUM_EVIDENCE_REACHED"
        all_covered = all(item.coverage_percent >= self.minimum_discipline_coverage for item in summary.disciplines)
        no_critical = not any(item.has_critical_investigation for item in summary.disciplines)
        if (
            total_evidence >= self.minimum_evidence
            and all_covered
            and no_critical
            and summary.overall_confidence >= self.target_confidence
        ):
            return True, "GLOBAL_SUFFICIENCY_REACHED"
        return False, "CONTINUE"


@dataclass(frozen=True)
class DiagnosticTimePolicy:
    target_duration_seconds: int
    soft_limit_seconds: int
    hard_limit_seconds: int


@dataclass(frozen=True)
class DiagnosticDecision:
    action: str
    reason: str
    high_information_only: bool = False


@dataclass(frozen=True)
class DiagnosticDecisionPolicy:
    """Pure session decision layer; it never selects questions or changes authorization."""

    discipline: DiagnosticTimePolicy = DiagnosticTimePolicy(1200, 1320, 1500)
    global_mode: DiagnosticTimePolicy = DiagnosticTimePolicy(1800, 2100, 2400)
    enem: DiagnosticTimePolicy = DiagnosticTimePolicy(2100, 2400, 2700)

    def decide(
        self,
        *,
        mode: str,
        elapsed_seconds: int,
        questions_asked: int,
        coverage: ContentCoverageState,
        global_stop: bool = False,
        global_mode: bool = False,
    ) -> DiagnosticDecision:
        policy = self.enem if mode == "ENEM" else self.global_mode if mode in {"GLOBAL", "UNSPECIFIED"} else self.discipline
        if elapsed_seconds >= policy.hard_limit_seconds:
            return DiagnosticDecision("FINISH_TIME", "HARD_LIMIT_REACHED")
        if global_stop:
            return DiagnosticDecision("FINISH_SUFFICIENT", "GLOBAL_SUFFICIENCY_REACHED")
        if global_mode:
            return DiagnosticDecision("CONTINUE", "GLOBAL_COVERAGE_INCOMPLETE", elapsed_seconds >= policy.soft_limit_seconds)
        if coverage.needs_prerequisite_investigation:
            return DiagnosticDecision("CONTINUE_PREREQUISITE_INVESTIGATION", "POSSIBLE_PREREQUISITE_GAP", elapsed_seconds >= policy.soft_limit_seconds)
        if coverage.status in {"SUFFICIENT_EVIDENCE", "CONSOLIDATED"}:
            return DiagnosticDecision("FINISH_SUFFICIENT", "CONTENT_SUFFICIENCY_REACHED")
        if elapsed_seconds >= policy.soft_limit_seconds:
            return DiagnosticDecision("CONTINUE", "SOFT_LIMIT_HIGH_INFORMATION_ONLY", True)
        return DiagnosticDecision("CONTINUE", "INSUFFICIENT_EVIDENCE")


@dataclass(frozen=True)
class PrerequisiteHypothesisPolicy:
    """Pure, gradual hypothesis updates; UNKNOWN changes uncertainty only."""

    support_threshold: int = 2
    refute_threshold: int = 2

    def create(self, *, target_content_id: str, prerequisite_content_id: str, timestamp: str, evidence_id: str) -> dict:
        return {
            "target_content_id": target_content_id,
            "prerequisite_content_id": prerequisite_content_id,
            "status": "SUSPECTED",
            "supporting_evidence_count": 0,
            "contradictory_evidence_count": 0,
            "unknown_evidence_count": 0,
            "confidence": 0.0,
            "first_detected_at": timestamp,
            "last_updated_at": timestamp,
            "evidence_ids": [evidence_id],
        }

    def update(self, hypothesis: dict, *, is_correct: bool, is_unknown: bool, timestamp: str, evidence_id: str) -> dict:
        current = {**hypothesis, "evidence_ids": list(hypothesis["evidence_ids"])}
        if is_unknown:
            current["unknown_evidence_count"] += 1
            current["status"] = "INCONCLUSIVE"
        elif is_correct:
            current["contradictory_evidence_count"] += 1
            current["status"] = "REFUTED" if current["contradictory_evidence_count"] >= self.refute_threshold else "UNDER_INVESTIGATION"
        else:
            current["supporting_evidence_count"] += 1
            current["status"] = "SUPPORTED" if current["supporting_evidence_count"] >= self.support_threshold else "UNDER_INVESTIGATION"
        evidence_total = current["supporting_evidence_count"] + current["contradictory_evidence_count"]
        current["confidence"] = round(abs(current["supporting_evidence_count"] - current["contradictory_evidence_count"]) / evidence_total, 4) if evidence_total else 0.0
        current["last_updated_at"] = timestamp
        current["evidence_ids"].append(evidence_id)
        return current


@dataclass(frozen=True)
class DiagnosticConcentrationPolicy:
    """Bound repeated focus without overriding authorization or candidate eligibility."""

    max_consecutive_same_content: int = 3
    max_consecutive_same_discipline: int = 2
    minimum_alternative_candidates: int = 1

    def decision(
        self,
        *,
        recent_content_ids: Sequence[str],
        recent_discipline_ids: Sequence[str],
        candidate_content_id: str,
        candidate_discipline_id: str,
        has_alternative_content: bool,
        has_alternative_discipline: bool,
    ) -> str:
        same_content = len(recent_content_ids) >= self.max_consecutive_same_content and all(
            item == candidate_content_id for item in recent_content_ids[-self.max_consecutive_same_content:]
        )
        if same_content and has_alternative_content:
            return "SWITCH_CONTENT"
        same_discipline = len(recent_discipline_ids) >= self.max_consecutive_same_discipline and all(
            item == candidate_discipline_id for item in recent_discipline_ids[-self.max_consecutive_same_discipline:]
        )
        if same_discipline and has_alternative_discipline:
            return "SWITCH_DISCIPLINE"
        return "CONTINUE_SAME_CONTENT"