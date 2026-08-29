"""
Learning path domain policies and rules.

Defines deterministic, configurable rules for mastery calculation, confidence, and progression.
"""

from dataclasses import dataclass, field
from enum import Enum
from typing import Optional


class DifficultyLevel(str, Enum):
    """Difficulty levels for practice and assessment."""

    EASY = "EASY"
    MEDIUM = "MEDIUM"
    HARD = "HARD"


class ActivityType(str, Enum):
    """Types of learning activities."""

    OFFICIAL_ASSESSMENT = "OFFICIAL_ASSESSMENT"
    INDIVIDUAL_PRACTICE = "INDIVIDUAL_PRACTICE"


@dataclass
class MasteryThresholds:
    """Configurable thresholds for difficulty progression."""

    # Score thresholds (0-100 percentage)
    # If mastery_score >= threshold and confidence >= min_confidence, can progress
    min_easy_score: float = 70.0
    min_medium_score: float = 70.0
    min_hard_score: float = 60.0  # Higher difficulty needs lower threshold

    # Minimum confidence required for progression
    # Confidence increases with number of questions answered
    min_confidence_for_progression: float = 0.6

    # Question count thresholds for confidence calculation
    low_evidence_threshold: int = 3  # < 3 questions = low confidence
    medium_evidence_threshold: int = 10  # 3-10 = medium confidence
    high_evidence_threshold: int = 20  # >= 20 = high confidence

    def get_min_score_for_level(self, level: DifficultyLevel) -> float:
        """Get minimum required score for progression from this level."""
        if level == DifficultyLevel.EASY:
            return self.min_easy_score
        elif level == DifficultyLevel.MEDIUM:
            return self.min_medium_score
        elif level == DifficultyLevel.HARD:
            return self.min_hard_score
        return 70.0


@dataclass
class ConfidencePolicy:
    """Policy for calculating confidence based on evidence."""

    # Confidence is based on number of questions answered
    # Low evidence (< 3 questions): confidence increases slowly
    # Medium evidence (3-20): confidence increases faster
    # High evidence (>= 20): confidence plateaus

    def calculate_confidence(self, questions_answered: int, questions_correct: int) -> float:
        """
        Calculate confidence (0-1) based on number of questions answered.

        Confidence represents "how sure are we about this mastery assessment?"

        Logic:
        - Very few questions answered: low confidence
        - More questions: confidence increases
        - Plateaus at high number of questions

        Performance (correct/total) influences starting confidence,
        but quantity of evidence is primary driver.
        """
        if questions_answered == 0:
            return 0.0

        if questions_answered < 3:
            # Very low evidence: 0.0 to 0.3
            base_confidence = min(0.3, questions_answered * 0.1)
        elif questions_answered < 10:
            # Low evidence: 0.3 to 0.6
            base_confidence = 0.3 + ((questions_answered - 3) / 7) * 0.3
        elif questions_answered < 20:
            # Medium evidence: 0.6 to 0.85
            base_confidence = 0.6 + ((questions_answered - 10) / 10) * 0.25
        else:
            # High evidence: 0.85 to 1.0
            base_confidence = min(1.0, 0.85 + ((questions_answered - 20) / 50) * 0.15)

        return min(1.0, base_confidence)


@dataclass
class MasteryCalculationPolicy:
    """Policy for calculating mastery score from performance history."""

    # Recent performance weighted more heavily
    # Older performances fade in relevance
    recent_weight: float = 0.6  # Weight for last performance
    historical_weight: float = 0.4  # Weight for older performances

    def calculate_mastery_score(
        self,
        questions_answered: int,
        questions_correct: int,
        recent_correct_count: Optional[int] = None,
        recent_answered_count: Optional[int] = None,
    ) -> float:
        """
        Calculate mastery score (0-100) based on performance history.

        Simple approach:
        - If no recent data specified: use overall average
        - If recent data provided: weight recent more heavily

        Mastery score = (questions_correct / questions_answered) * 100
        """
        if questions_answered == 0:
            return 0.0

        if recent_correct_count is None or recent_answered_count is None:
            # Use overall average
            return (questions_correct / questions_answered) * 100.0

        # Weight recent vs historical
        overall_average = (questions_correct / questions_answered) * 100.0
        recent_average = (
            (recent_correct_count / recent_answered_count * 100.0)
            if recent_answered_count > 0
            else 0.0
        )

        weighted_score = (
            self.recent_weight * recent_average + self.historical_weight * overall_average
        )

        return min(100.0, max(0.0, weighted_score))


@dataclass
class DifficultyProgressionPolicy:
    """Policy for recommending difficulty progression."""

    thresholds: MasteryThresholds = field(default_factory=MasteryThresholds)
    confidence_policy: ConfidencePolicy = field(default_factory=ConfidencePolicy)

    def recommend_next_level(
        self,
        current_level: DifficultyLevel,
        mastery_score: float,
        confidence: float,
        questions_answered: int,
    ) -> DifficultyLevel:
        """
        Recommend difficulty level for next practice.

        Logic:
        1. If no history: start EASY
        2. If in EASY: can progress to MEDIUM if score >= threshold AND confidence sufficient
        3. If in MEDIUM: can progress to HARD if score >= threshold AND confidence sufficient
        4. If in HARD: stay HARD if score >= threshold, else go to MEDIUM
        5. If insufficient performance: stay current or go back one level

        Returns recommended level (may stay same as current).
        """
        # Not enough evidence yet: stay current
        if confidence < self.thresholds.min_confidence_for_progression:
            return current_level

        min_score = self.thresholds.get_min_score_for_level(current_level)

        if current_level == DifficultyLevel.EASY:
            if mastery_score >= min_score:
                return DifficultyLevel.MEDIUM
            return DifficultyLevel.EASY

        elif current_level == DifficultyLevel.MEDIUM:
            if mastery_score >= min_score:
                return DifficultyLevel.HARD
            elif mastery_score < 50.0:  # Performance dropped significantly
                return DifficultyLevel.EASY
            return DifficultyLevel.MEDIUM

        elif current_level == DifficultyLevel.HARD:
            if mastery_score >= min_score:
                return DifficultyLevel.HARD  # Stay hard
            elif mastery_score < 40.0:
                return DifficultyLevel.MEDIUM  # Go back to medium
            return DifficultyLevel.HARD  # Stay hard but struggling

        return current_level

    def get_initial_level(self) -> DifficultyLevel:
        """Get initial difficulty level for student with no history."""
        return DifficultyLevel.EASY

    def recommend_initial_level_from_other_content(
        self,
        existing_masteries: list[tuple["DifficultyLevel", float, float]],
    ) -> DifficultyLevel:
        """
        Recommend an initial difficulty for a content the student has never
        practiced, based on evidence of mastery demonstrated in OTHER contents.

        A student who already demonstrates solid, well-evidenced mastery
        elsewhere should not be forced to restart at EASY for every new
        content. Only qualifying evidence counts:
        - confidence must meet min_confidence_for_progression
        - mastery_score must meet the threshold required for that level

        Args:
            existing_masteries: list of (current_level, mastery_score, confidence)
                for the student's other content mastery records.

        Returns:
            The highest qualifying level found, or EASY if no sufficient
            evidence exists.
        """
        rank = {DifficultyLevel.EASY: 0, DifficultyLevel.MEDIUM: 1, DifficultyLevel.HARD: 2}
        best = DifficultyLevel.EASY

        for level, score, confidence in existing_masteries:
            if confidence < self.thresholds.min_confidence_for_progression:
                continue
            min_score = self.thresholds.get_min_score_for_level(level)
            if score >= min_score and rank[level] > rank[best]:
                best = level

        return best
