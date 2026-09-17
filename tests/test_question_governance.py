import unittest
from uuid import uuid4

from agente_ia_edu.db.models import (
    Question,
    QuestionVersion,
    PedagogicalClassification,
)
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.services import (
    QuestionAuthorizationService,
    QuestionEligibilityCalculator,
    QuestionStatusWorkflow,
)


class TestQuestionGovernancePhase2(unittest.TestCase):
    def test_status_workflow_allows_valid_transition_and_records_audit(self):
        q = Question(status="DRAFT", validation_status="draft")
        result = QuestionStatusWorkflow.transition(
            q,
            "REVIEW",
            performed_by_external_id="teacher-9",
            reason="Ready for moderation",
        )

        self.assertEqual(q.status, "REVIEW")
        self.assertEqual(result.to_status, "REVIEW")
        self.assertEqual(len(q.status_transitions), 1)
        self.assertEqual(q.status_transitions[0].to_status, "REVIEW")

    def test_status_workflow_rejects_invalid_transition(self):
        q = Question(status="DRAFT", validation_status="draft")

        with self.assertRaises(Exception):
            QuestionStatusWorkflow.transition(q, "PUBLISHED")

    def test_eligibility_calculator_requires_published_status(self):
        q = Question(
            status="DRAFT",
            visibility_scope="SCHOOL",
            school_id=uuid4(),
            origin_type="SCHOOL",
        )
        result = QuestionEligibilityCalculator.calculate(q)

        self.assertFalse(result.is_eligible)
        self.assertIn("status", " ".join(result.reasons).lower())

    def test_eligibility_calculator_accepts_published_school_question(self):
        """PUBLISHED with official version and classification is eligible."""
        school_id = uuid4()
        version_id = uuid4()
        
        # Create version with classification
        version = QuestionVersion(
            id=version_id,
            question_id=uuid4(),
            version_kind="official_original",
            canonical_text="What is 2+2?",
            content_hash="abc",
            recommended_difficulty="MEDIUM",
            pedagogical_classifications=[
                PedagogicalClassification(
                    id=uuid4(),
                    question_version_id=version_id,
                    discipline="Math",
                    content="Arithmetic",
                    subcontent="Basic",
                    difficulty="MEDIUM",
                    reasoning_type="recall",
                    status="CLASSIFIED",
                )
            ],
        )
        
        q = Question(
            status="PUBLISHED",
            visibility_scope="SCHOOL",
            school_id=school_id,
            origin_type="SCHOOL",
            validation_status="valid",
            versions=[version],
        )
        result = QuestionEligibilityCalculator.calculate(q)

        self.assertTrue(result.is_eligible)
        self.assertEqual(result.reasons, [])

    def test_authorization_service_denies_other_school_access(self):
        question = Question(id=uuid4(), school_id=uuid4(), visibility_scope="SCHOOL", status="PUBLISHED")
        context = AuthenticatedUserContext(
            user_id="teacher-2",
            external_identity_id="teacher-2",
            role="TEACHER",
            school_id=str(uuid4()),
            scope_type="CLASSROOM",
            scope_external_id="CLASS-9",
        )

        allowed = QuestionAuthorizationService.can_view_question(context, question)
        self.assertFalse(allowed)

    def test_authorization_service_allows_school_admin_for_school_question(self):
        school_id = uuid4()
        question = Question(id=uuid4(), school_id=school_id, visibility_scope="SCHOOL", status="PUBLISHED")
        context = AuthenticatedUserContext(
            user_id="director-1",
            external_identity_id="director-1",
            role="DIRECTOR",
            school_id=str(school_id),
            scope_type="SCHOOL",
            scope_external_id="SCHOOL-1",
        )

        allowed = QuestionAuthorizationService.can_view_question(context, question)
        self.assertTrue(allowed)

    def test_authorization_service_does_not_crash_on_classroom_question_with_no_metadata(self):
        """Regression test for a real bug found auditing question_governance.py:
        can_view_question's CLASSROOM branch (TEACHER and STUDENT roles) did
        ``question.metadata_.get("classroom_id")`` guarded only by
        ``hasattr(question, "metadata_")`` - which is always True for a mapped
        column regardless of its value. Question.metadata_ is nullable with no
        default (db/models/official.py), so any CLASSROOM-visibility question
        created without an explicit metadata_ dict (e.g. via
        ``Question(validation_status="extracted")`` as ingestion_classifier.py
        does) has metadata_=None, and this raised
        ``AttributeError: 'NoneType' object has no attribute 'get'`` -
        uncaught by routes/questions.py's list/detail/eligibility endpoints,
        turning a routine visibility check into a 500. This reproduces on
        SQLite/plain objects (no DB needed), unlike the MissingGreenlet class
        of bugs in this session."""
        school_id = uuid4()
        question = Question(
            id=uuid4(),
            school_id=school_id,
            visibility_scope="CLASSROOM",
            status="PUBLISHED",
            metadata_=None,
        )
        for role in ("TEACHER", "STUDENT"):
            context = AuthenticatedUserContext(
                user_id="u-1",
                external_identity_id="u-1",
                role=role,
                school_id=str(school_id),
                scope_type="CLASSROOM",
                scope_external_id="CLASS-9",
            )
            # Must not raise, and must deny (no classroom_id in metadata to match).
            allowed = QuestionAuthorizationService.can_view_question(context, question)
            self.assertFalse(allowed)


if __name__ == "__main__":
    unittest.main()
