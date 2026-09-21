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

    def test_classroom_question_denies_teacher_with_no_classroom_scope(self):
        # Regression: (question.metadata_ or {}).get("classroom_id") ==
        # context.scope_external_id evaluated None == None -> True whenever
        # neither side was set - a SCHOOL-scoped (or fallback, no-link)
        # TEACHER with scope_external_id=None could view ANY CLASSROOM
        # question that carries no classroom_id in its metadata, with no
        # real classroom relationship at all.
        school_id = uuid4()
        question = Question(
            id=uuid4(), school_id=school_id, visibility_scope="CLASSROOM", status="PUBLISHED",
        )
        context = AuthenticatedUserContext(
            user_id="teacher-noclass", external_identity_id="teacher-noclass", role="TEACHER",
            school_id=str(school_id), scope_type="SCHOOL", scope_external_id=None,
        )
        self.assertFalse(QuestionAuthorizationService.can_view_question(context, question))

    def test_classroom_question_allows_teacher_with_matching_classroom_scope(self):
        school_id = uuid4()
        question = Question(
            id=uuid4(), school_id=school_id, visibility_scope="CLASSROOM", status="PUBLISHED",
            metadata_={"classroom_id": "CLASS-7"},
        )
        context = AuthenticatedUserContext(
            user_id="teacher-class7", external_identity_id="teacher-class7", role="TEACHER",
            school_id=str(school_id), scope_type="CLASSROOM", scope_external_id="CLASS-7",
        )
        self.assertTrue(QuestionAuthorizationService.can_view_question(context, question))

    def test_classroom_question_denies_teacher_with_different_classroom_scope(self):
        school_id = uuid4()
        question = Question(
            id=uuid4(), school_id=school_id, visibility_scope="CLASSROOM", status="PUBLISHED",
            metadata_={"classroom_id": "CLASS-7"},
        )
        context = AuthenticatedUserContext(
            user_id="teacher-class8", external_identity_id="teacher-class8", role="TEACHER",
            school_id=str(school_id), scope_type="CLASSROOM", scope_external_id="CLASS-8",
        )
        self.assertFalse(QuestionAuthorizationService.can_view_question(context, question))

    def test_school_less_platform_question_denies_self_declared_director(self):
        # A DIRECTOR/COORDINATOR role is reachable with no real UserSchoolLink
        # via AuthorizationService.resolve_context's fallback path - this must
        # not be enough to view a school-less, non-PUBLIC question. Only a
        # real platform admin (is_platform_admin=True, checked earlier in
        # can_view_question) may.
        question = Question(id=uuid4(), school_id=None, visibility_scope="SCHOOL", status="PUBLISHED")
        context = AuthenticatedUserContext(
            user_id="self-declared-director", external_identity_id="self-declared-director",
            role="DIRECTOR", school_id=None, scope_type="PLATFORM", is_platform_admin=False,
        )
        self.assertFalse(QuestionAuthorizationService.can_view_question(context, question))

    def test_school_less_platform_question_allows_real_platform_admin(self):
        question = Question(id=uuid4(), school_id=None, visibility_scope="SCHOOL", status="PUBLISHED")
        context = AuthenticatedUserContext(
            user_id="real-admin", external_identity_id="real-admin",
            role="PLATFORM_ADMIN", school_id=None, scope_type="PLATFORM", is_platform_admin=True,
        )
        self.assertTrue(QuestionAuthorizationService.can_view_question(context, question))


if __name__ == "__main__":
    unittest.main()
