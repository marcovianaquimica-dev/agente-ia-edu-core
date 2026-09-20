from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any
from uuid import UUID

from agente_ia_edu.db.models import Question
from agente_ia_edu.db.models.official import QuestionStatusTransition
from agente_ia_edu.identity import AuthenticatedUserContext


class InvalidQuestionStatusTransitionError(ValueError):
    """Raised when a status change is not allowed by the governed workflow."""


@dataclass(frozen=True, slots=True)
class QuestionEligibilityCheckResult:
    is_eligible: bool
    reasons: list[str] = field(default_factory=list)


class QuestionStatusWorkflow:
    """Deterministic question lifecycle and audit trail for Fase 17.
    
    Allowed transitions per FASE_17_DESIGN.md:
    - DRAFT → REVIEW
    - REVIEW → APPROVED | REJECTED | DRAFT (REQUEST CHANGES)
    - APPROVED → PUBLISHED
    - PUBLISHED → ARCHIVED
    - ARCHIVED → (terminal)
    - REJECTED → (terminal)
    """

    ALLOWED_TRANSITIONS: dict[str, set[str]] = {
        "DRAFT": {"REVIEW"},
        "REVIEW": {"APPROVED", "REJECTED", "DRAFT"},
        "APPROVED": {"PUBLISHED"},
        "PUBLISHED": {"ARCHIVED"},
        "ARCHIVED": set(),
        "REJECTED": set(),
    }

    @classmethod
    def transition(
        cls,
        question: Question,
        to_status: str,
        *,
        performed_by_external_id: str | None = None,
        reason: str | None = None,
        metadata: dict[str, Any] | None = None,
    ) -> QuestionStatusTransition:
        current = (question.status or "DRAFT").upper()
        target = (to_status or "").upper()

        if target not in cls.ALLOWED_TRANSITIONS.get(current, set()):
            allowed = ", ".join(sorted(cls.ALLOWED_TRANSITIONS.get(current, set()))) or "none"
            raise InvalidQuestionStatusTransitionError(
                f"Invalid transition from {current} to {target}. Allowed: {allowed}."
            )

        previous_status = question.status
        question.status = target

        transition_record = QuestionStatusTransition(
            question_id=getattr(question, "id", None),
            from_status=previous_status,
            to_status=target,
            performed_by_external_id=performed_by_external_id,
            reason=reason,
            metadata_=metadata or {},
            created_at=datetime.now(timezone.utc),
        )

        if hasattr(question, "status_transitions"):
            try:
                question.status_transitions.append(transition_record)
            except Exception:
                setattr(question, "status_transitions", [transition_record])

        return transition_record


class QuestionEligibilityCalculator:
    """Compute deterministic eligibility per FASE_17_DESIGN.md section 5.
    
    A question is eligible for practice if ALL of these are true:
    1. question.status == 'PUBLISHED'
    2. question has a version with version_kind == 'official_original'
    3. question.validation_status in ['valid', 'acceptable']
    4. the official_original version has recommended_difficulty set
    5. the official_original version has at least one active classification
    
    Note: Eligibility is deterministic and does NOT consider user authorization/scope.
    User authorization is handled separately by QuestionAuthorizationService.
    """

    @staticmethod
    def calculate(question: Question) -> QuestionEligibilityCheckResult:
        """Calculate eligibility status for a question.
        
        Returns:
            QuestionEligibilityCheckResult with is_eligible boolean and list of failure reasons.
        """
        reasons: list[str] = []

        # Requirement 1: Status must be PUBLISHED
        if question.status != "PUBLISHED":
            reasons.append(f"status is {question.status}, must be PUBLISHED")

        # Requirement 2: Must have official_original version
        official_version = None
        if hasattr(question, "versions") and question.versions:
            for v in question.versions:
                if v.version_kind == "official_original":
                    official_version = v
                    break

        if official_version is None:
            reasons.append("no official_original version exists")

        # Requirement 3: validation_status must be valid/acceptable
        validation_status = str(question.validation_status or "").lower()
        if validation_status not in {"valid", "acceptable", "approved"}:
            reasons.append(f"validation_status is {question.validation_status}, must be 'valid' or 'acceptable'")

        # Requirements 4 & 5 require the official version
        if official_version is not None:
            # Requirement 4: recommended_difficulty must be set
            if not official_version.recommended_difficulty:
                reasons.append("official_original version missing recommended_difficulty")

            # Requirement 5: must have at least one active classification
            has_classification = False
            if hasattr(official_version, "pedagogical_classifications"):
                active_classifications = [
                    c for c in official_version.pedagogical_classifications
                    if c.status == "CLASSIFIED"
                ]
                if active_classifications:
                    has_classification = True

            if not has_classification:
                reasons.append("official_original version has no active classifications")

        return QuestionEligibilityCheckResult(
            is_eligible=not reasons,
            reasons=reasons,
        )


class QuestionAuthorizationService:
    """Authorization service for question access control.
    
    Enforces:
    - Multi-tenant isolation (school_id)
    - Role-based access (ROLE in AuthenticatedUserContext)
    - Scope-based access (SCOPE in AuthenticatedUserContext)
    - Visibility scope rules (PRIVATE, CLASSROOM, SCHOOL, PUBLIC)
    - Ownership rules (author_external_id, owner_external_id)
    
    Does NOT provide eligibility checks (see QuestionEligibilityCalculator).
    """

    @staticmethod
    def _matches_classroom(context: AuthenticatedUserContext, question: Question) -> bool:
        """True only when the requester's own classroom scope is set AND
        equals the question's classroom_id.

        A prior form compared `(question.metadata_ or {}).get("classroom_id")`
        directly to `context.scope_external_id`: when a question carries no
        classroom_id (metadata_ is None, or the key is absent - the default
        shape) AND the requester's own scope_external_id is also None (any
        SCHOOL-scoped link, or the school-less fallback context), both sides
        evaluate to None and `None == None` granted access to every such
        CLASSROOM-visibility question, regardless of any real classroom
        relationship. Requiring scope_external_id to be truthy first closes
        that without reintroducing the AttributeError a `metadata_ or {}`
        idiom was added earlier to fix (question.metadata_ can be None).
        """
        if not context.scope_external_id:
            return False
        classroom_id = (question.metadata_ or {}).get("classroom_id")
        return classroom_id == context.scope_external_id

    @staticmethod
    def can_view_question(context: AuthenticatedUserContext, question: Question) -> bool:
        """Check if user can view this question.
        
        Args:
            context: Authenticated user context with role, school_id, scope
            question: Question to check access for
            
        Returns:
            True if user can view the question, False otherwise
        """
        # PLATFORM_ADMIN can view all questions
        if context.is_platform_admin:
            return True

        # PUBLIC questions visible to all authenticated users (including independent students)
        if question.visibility_scope == "PUBLIC":
            return True

        # PRIVATE questions: only owner and school admins
        if question.visibility_scope == "PRIVATE":
            return QuestionAuthorizationService._is_owner_or_admin(context, question)

        # SCHOOL, CLASSROOM questions require matching school
        if question.school_id is None:
            # Platform question with non-PUBLIC visibility: a real platform
            # admin already returned True above. DIRECTOR/COORDINATOR here
            # used to be granted from context.role alone - satisfiable by
            # AuthorizationService.resolve_context's self-asserted-role
            # fallback with no real UserSchoolLink at all, the same
            # "no real relationship = should deny" anti-pattern this phase
            # already fixed for the equivalent check in can_manage_question
            # (school_id is None -> return False, no role exception).
            return False

        # User must be in the same school
        if context.school_id is None:
            # Independent student cannot access school-scoped questions
            return False

        if str(context.school_id) != str(question.school_id):
            # Different school - denied
            return False

        # Same school - check role and scope
        if context.role == "PLATFORM_ADMIN":
            return True

        if context.role in {"DIRECTOR", "COORDINATOR"}:
            # Admins can view all school content
            return True

        if context.role == "TEACHER":
            # Teacher can view SCHOOL scope or CLASSROOM if in their scope
            if question.visibility_scope == "SCHOOL":
                return True
            if question.visibility_scope == "CLASSROOM":
                return QuestionAuthorizationService._matches_classroom(context, question)
            return False

        if context.role == "STUDENT":
            # Student can view SCHOOL or matching CLASSROOM
            if question.visibility_scope == "SCHOOL":
                return True
            if question.visibility_scope == "CLASSROOM":
                return QuestionAuthorizationService._matches_classroom(context, question)
            return False

        return False

    @staticmethod
    def can_manage_question(context: AuthenticatedUserContext, question: Question) -> bool:
        """Check if user can modify/delete this question (DRAFT, REVIEW state).
        
        Args:
            context: Authenticated user context
            question: Question to check management access for
            
        Returns:
            True if user can manage (edit/delete) the question
        """
        # PLATFORM_ADMIN can manage all questions
        if context.is_platform_admin:
            return True

        # Platform questions: only PLATFORM_ADMIN
        if question.school_id is None and question.origin_type == "PLATFORM":
            return False

        # School questions: director/coordinator of that school
        if question.school_id is not None:
            same_school = context.school_id is not None and str(context.school_id) == str(question.school_id)
            if not same_school:
                return False

            if context.role in {"DIRECTOR", "COORDINATOR"}:
                return True

            # Teacher can only manage their own questions
            if context.role == "TEACHER":
                is_author = question.author_external_id == context.user_id
                is_owner = question.owner_external_id == context.user_id
                return is_author or is_owner

            return False

        return False

    @staticmethod
    def can_transition_status(
        context: AuthenticatedUserContext,
        question: Question,
        target_status: str,
    ) -> bool:
        """Check if user can perform a status transition on this question.
        
        Args:
            context: Authenticated user context
            question: Question whose status to change
            target_status: Target status (REVIEW, APPROVED, REJECTED, PUBLISHED, ARCHIVED)
            
        Returns:
            True if user can perform the transition
        """
        target = (target_status or "").upper()

        # PLATFORM_ADMIN can transition any question to any valid state
        if context.is_platform_admin:
            return True

        # Can only transition questions you can manage
        if not QuestionAuthorizationService.can_manage_question(context, question):
            return False

        # DRAFT → REVIEW: author/owner/admin can submit for review
        if target == "REVIEW":
            return question.status == "DRAFT"

        # REVIEW → APPROVED/REJECTED: only DIRECTOR/COORDINATOR
        if target in {"APPROVED", "REJECTED"}:
            if question.status != "REVIEW":
                return False
            return context.role in {"DIRECTOR", "COORDINATOR", "PLATFORM_ADMIN"}

        # REVIEW → DRAFT: can only reject back by author/owner
        if target == "DRAFT":
            if question.status != "REVIEW":
                return False
            is_author = question.author_external_id == context.user_id
            is_owner = question.owner_external_id == context.user_id
            return is_author or is_owner

        # APPROVED → PUBLISHED: director/coordinator/admin
        if target == "PUBLISHED":
            if question.status != "APPROVED":
                return False
            return context.role in {"DIRECTOR", "COORDINATOR", "PLATFORM_ADMIN"}

        # PUBLISHED → ARCHIVED: director/coordinator/admin
        if target == "ARCHIVED":
            if question.status != "PUBLISHED":
                return False
            return context.role in {"DIRECTOR", "COORDINATOR", "PLATFORM_ADMIN"}

        return False

    @staticmethod
    def _is_owner_or_admin(context: AuthenticatedUserContext, question: Question) -> bool:
        """Helper: check if user is owner or school admin."""
        if context.is_platform_admin:
            return True

        is_author = question.author_external_id == context.user_id
        is_owner = question.owner_external_id == context.user_id

        if is_author or is_owner:
            return True

        # School admin
        if question.school_id is not None:
            same_school = context.school_id is not None and str(context.school_id) == str(question.school_id)
            if same_school and context.role in {"DIRECTOR", "COORDINATOR"}:
                return True

        return False


__all__ = [
    "InvalidQuestionStatusTransitionError",
    "QuestionAuthorizationService",
    "QuestionEligibilityCalculator",
    "QuestionEligibilityCheckResult",
    "QuestionStatusWorkflow",
]
