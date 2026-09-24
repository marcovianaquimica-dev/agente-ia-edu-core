"""Service-layer coverage for agente_ia_edu.services.question_governance.

Targets branches left uncovered by test_question_governance.py and
test_question_governance_comprehensive.py: the append-failure fallback in
QuestionStatusWorkflow.transition, the STUDENT and unknown-role dispatch in
QuestionAuthorizationService.can_view_question, the PLATFORM_ADMIN/PLATFORM-
origin shortcuts in can_manage_question, the almost entirely untested
can_transition_status target matrix, and the owner/admin branches of the
private _is_owner_or_admin helper.

All of these are plain synchronous functions of (context, question) with no
DB access, so - matching test_question_governance.py's own convention - these
are direct unit tests against transient (never-flushed) ORM objects, no
session required.
"""

from __future__ import annotations

import unittest
import unittest.mock
from uuid import uuid4

from agente_ia_edu.db.models import Question
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.services import (
    QuestionAuthorizationService,
    QuestionStatusWorkflow,
)


class TestStatusWorkflowAppendFallback(unittest.TestCase):
    def test_append_failure_falls_back_to_replacing_status_transitions(self):
        """`hasattr(question, "status_transitions")` is true and `.append(...)`
        is tried first (the common path, already covered elsewhere); the
        `except Exception: setattr(...)` fallback exists for whatever reason
        that append might itself raise (e.g. an event listener on the
        collection). A real SQLAlchemy Question's status_transitions is
        list-typed and rejects a non-list assignment outright (TypeError at
        assignment time, not inside transition()), so the only way to make
        `.append` itself raise without violating that typing is to break the
        bound method on the already-initialized InstrumentedList - which is
        exactly what an external event listener misbehaving would look like
        from transition()'s point of view.
        """
        q = Question(status="DRAFT", validation_status="draft")
        # Force initialization of the InstrumentedList, then sabotage append.
        transitions = q.status_transitions
        self.assertEqual(list(transitions), [])
        transitions.append = unittest.mock.MagicMock(side_effect=RuntimeError("boom"))

        result = QuestionStatusWorkflow.transition(q, "REVIEW", performed_by_external_id="teacher-1")

        self.assertEqual(q.status, "REVIEW")
        # Fallback replaced status_transitions outright with a fresh list
        # containing just the new record, per the `except` branch.
        self.assertEqual(q.status_transitions, [result])


class TestCanViewQuestionStudentAndUnknownRoleDispatch(unittest.TestCase):
    def test_student_can_view_school_scoped_question_in_own_school(self):
        school_id = uuid4()
        question = Question(id=uuid4(), school_id=school_id, visibility_scope="SCHOOL", status="PUBLISHED")
        context = AuthenticatedUserContext(
            user_id="student-1", external_identity_id="student-1", role="STUDENT",
            school_id=str(school_id),
        )
        self.assertTrue(QuestionAuthorizationService.can_view_question(context, question))

    def test_student_can_view_matching_classroom_question(self):
        school_id = uuid4()
        question = Question(
            id=uuid4(), school_id=school_id, visibility_scope="CLASSROOM", status="PUBLISHED",
            metadata_={"classroom_id": "CLASS-3"},
        )
        context = AuthenticatedUserContext(
            user_id="student-3", external_identity_id="student-3", role="STUDENT",
            school_id=str(school_id), scope_type="CLASSROOM", scope_external_id="CLASS-3",
        )
        self.assertTrue(QuestionAuthorizationService.can_view_question(context, question))

    def test_student_cannot_view_different_classroom_question(self):
        school_id = uuid4()
        question = Question(
            id=uuid4(), school_id=school_id, visibility_scope="CLASSROOM", status="PUBLISHED",
            metadata_={"classroom_id": "CLASS-3"},
        )
        context = AuthenticatedUserContext(
            user_id="student-4", external_identity_id="student-4", role="STUDENT",
            school_id=str(school_id), scope_type="CLASSROOM", scope_external_id="CLASS-4",
        )
        self.assertFalse(QuestionAuthorizationService.can_view_question(context, question))

    def test_role_not_in_the_explicit_dispatch_denied_by_default(self):
        """A role like SECRETARY (a real role elsewhere in the platform, see
        api/dependencies.py's SECRETARY check) isn't TEACHER/STUDENT/DIRECTOR/
        COORDINATOR/PLATFORM_ADMIN, so it falls through every explicit branch
        to the final catch-all `return False` - default-deny for any role
        this function doesn't know about, even within the user's own school.
        """
        school_id = uuid4()
        question = Question(id=uuid4(), school_id=school_id, visibility_scope="SCHOOL", status="PUBLISHED")
        context = AuthenticatedUserContext(
            user_id="secretary-1", external_identity_id="secretary-1", role="SECRETARY",
            school_id=str(school_id),
        )
        self.assertFalse(QuestionAuthorizationService.can_view_question(context, question))

    def test_self_declared_platform_admin_role_without_flag_same_school(self):
        """Defense in depth, not a currently-reachable path: every real
        AuthorizationService.resolve_context() branch keeps `role ==
        "PLATFORM_ADMIN"` and `is_platform_admin` consistent (fallback path
        sets `is_platform_admin = fallback_role == "PLATFORM_ADMIN"`; the
        linked-role path only ever sets role="PLATFORM_ADMIN" from a real
        PLATFORM_ADMIN link, which also makes is_platform_admin True via the
        same `any(...)` check) - so this exact combination cannot arise from
        that service today. The same-school PLATFORM_ADMIN role check at line
        232-233 is additional, currently-redundant coverage against a future
        context constructor breaking that invariant; this test exercises it
        directly the same way test_question_governance.py's
        `test_school_less_platform_question_denies_self_declared_director`
        already does for the analogous DIRECTOR/COORDINATOR case.
        """
        school_id = uuid4()
        question = Question(id=uuid4(), school_id=school_id, visibility_scope="SCHOOL", status="PUBLISHED")
        context = AuthenticatedUserContext(
            user_id="fake-admin", external_identity_id="fake-admin", role="PLATFORM_ADMIN",
            school_id=str(school_id), is_platform_admin=False,
        )
        self.assertTrue(QuestionAuthorizationService.can_view_question(context, question))


class TestCanViewQuestionPrivateOwnerAndAdminHelper(unittest.TestCase):
    def test_private_question_visible_to_its_author(self):
        school_id = uuid4()
        question = Question(
            id=uuid4(), school_id=school_id, visibility_scope="PRIVATE", status="DRAFT",
            author_external_id="teacher-author",
        )
        context = AuthenticatedUserContext(
            user_id="teacher-author", external_identity_id="teacher-author", role="TEACHER",
            school_id=str(school_id),
        )
        self.assertTrue(QuestionAuthorizationService.can_view_question(context, question))

    def test_private_question_visible_to_its_owner(self):
        school_id = uuid4()
        question = Question(
            id=uuid4(), school_id=school_id, visibility_scope="PRIVATE", status="DRAFT",
            author_external_id="someone-else", owner_external_id="teacher-owner",
        )
        context = AuthenticatedUserContext(
            user_id="teacher-owner", external_identity_id="teacher-owner", role="TEACHER",
            school_id=str(school_id),
        )
        self.assertTrue(QuestionAuthorizationService.can_view_question(context, question))

    def test_is_owner_or_admin_platform_admin_short_circuit(self):
        """can_view_question's own `if context.is_platform_admin: return
        True` (line 199) always fires before a PRIVATE question ever reaches
        `_is_owner_or_admin`, so that helper's OWN is_platform_admin check
        (line 356-357) is dead code on the only path that calls it today.
        Testing the staticmethod directly (it carries no leading underscore
        protection at runtime) still documents and locks in its standalone
        contract, in case it's ever reused elsewhere for a callsite that
        doesn't already gate on is_platform_admin.
        """
        school_id = uuid4()
        question = Question(
            id=uuid4(), school_id=school_id, visibility_scope="PRIVATE", status="DRAFT",
            author_external_id="someone-else",
        )
        context = AuthenticatedUserContext(
            user_id="admin-1", external_identity_id="admin-1", role="PLATFORM_ADMIN",
            school_id=str(school_id), is_platform_admin=True,
        )
        self.assertTrue(QuestionAuthorizationService._is_owner_or_admin(context, question))


class TestCanManageQuestionShortCircuits(unittest.TestCase):
    def test_platform_admin_can_manage_any_question(self):
        question = Question(id=uuid4(), school_id=uuid4(), status="PUBLISHED", origin_type="SCHOOL")
        context = AuthenticatedUserContext(
            user_id="admin-1", external_identity_id="admin-1", role="PLATFORM_ADMIN",
            school_id=None, is_platform_admin=True,
        )
        self.assertTrue(QuestionAuthorizationService.can_manage_question(context, question))

    def test_platform_owned_question_denied_to_non_platform_admin(self):
        question = Question(id=uuid4(), school_id=None, origin_type="PLATFORM", status="PUBLISHED")
        context = AuthenticatedUserContext(
            user_id="dir-1", external_identity_id="dir-1", role="DIRECTOR",
            school_id=None, is_platform_admin=False,
        )
        self.assertFalse(QuestionAuthorizationService.can_manage_question(context, question))


class TestCanTransitionStatusMatrix(unittest.TestCase):
    def test_platform_admin_bypasses_manage_and_business_rule_checks(self):
        """The is_platform_admin shortcut is the FIRST check in
        can_transition_status - it returns True before even calling
        can_manage_question, and before checking that the current status
        matches the target's precondition. QuestionStatusWorkflow's own
        ALLOWED_TRANSITIONS state machine is the real backstop against an
        actually-invalid transition (it still raises on DRAFT -> PUBLISHED
        even for an admin); this authorization gate alone does not enforce
        it.
        """
        question = Question(id=uuid4(), school_id=uuid4(), status="DRAFT", origin_type="SCHOOL")
        context = AuthenticatedUserContext(
            user_id="admin-1", external_identity_id="admin-1", role="PLATFORM_ADMIN",
            school_id=None, is_platform_admin=True,
        )
        self.assertTrue(
            QuestionAuthorizationService.can_transition_status(context, question, "PUBLISHED")
        )

    def test_denied_outright_when_caller_cannot_manage_the_question(self):
        school_id = uuid4()
        question = Question(
            id=uuid4(), school_id=school_id, status="DRAFT",
            author_external_id="teacher-owner", owner_external_id="teacher-owner",
        )
        context = AuthenticatedUserContext(
            user_id="teacher-other", external_identity_id="teacher-other", role="TEACHER",
            school_id=str(school_id),
        )
        self.assertFalse(
            QuestionAuthorizationService.can_transition_status(context, question, "REVIEW")
        )

    def test_approve_and_reject_require_review_status(self):
        school_id = uuid4()
        question = Question(id=uuid4(), school_id=school_id, status="DRAFT")
        director = AuthenticatedUserContext(
            user_id="dir-1", external_identity_id="dir-1", role="DIRECTOR", school_id=str(school_id),
        )
        self.assertFalse(
            QuestionAuthorizationService.can_transition_status(director, question, "APPROVED")
        )
        self.assertFalse(
            QuestionAuthorizationService.can_transition_status(director, question, "REJECTED")
        )

    def test_draft_target_requires_review_status_then_author_or_owner(self):
        school_id = uuid4()
        director = AuthenticatedUserContext(
            user_id="dir-1", external_identity_id="dir-1", role="DIRECTOR", school_id=str(school_id),
        )

        # Wrong current status -> denied regardless of who's asking.
        wrong_status_question = Question(id=uuid4(), school_id=school_id, status="APPROVED")
        self.assertFalse(
            QuestionAuthorizationService.can_transition_status(director, wrong_status_question, "DRAFT")
        )

        # REVIEW -> DRAFT, author requesting changes back: allowed.
        author_question = Question(
            id=uuid4(), school_id=school_id, status="REVIEW", author_external_id="teacher-a",
        )
        author_ctx = AuthenticatedUserContext(
            user_id="teacher-a", external_identity_id="teacher-a", role="TEACHER", school_id=str(school_id),
        )
        self.assertTrue(
            QuestionAuthorizationService.can_transition_status(author_ctx, author_question, "DRAFT")
        )

        # REVIEW -> DRAFT, owner (not author) requesting changes back: allowed.
        owner_question = Question(
            id=uuid4(), school_id=school_id, status="REVIEW",
            author_external_id="teacher-a", owner_external_id="teacher-b",
        )
        owner_ctx = AuthenticatedUserContext(
            user_id="teacher-b", external_identity_id="teacher-b", role="TEACHER", school_id=str(school_id),
        )
        self.assertTrue(
            QuestionAuthorizationService.can_transition_status(owner_ctx, owner_question, "DRAFT")
        )

        # REVIEW -> DRAFT, neither author nor owner (director can_manage via
        # role, but isn't the author/owner this specific transition requires).
        neither_question = Question(
            id=uuid4(), school_id=school_id, status="REVIEW",
            author_external_id="teacher-a", owner_external_id="teacher-b",
        )
        self.assertFalse(
            QuestionAuthorizationService.can_transition_status(director, neither_question, "DRAFT")
        )

    def test_published_target_requires_approved_status_and_admin_role(self):
        school_id = uuid4()
        director = AuthenticatedUserContext(
            user_id="dir-1", external_identity_id="dir-1", role="DIRECTOR", school_id=str(school_id),
        )

        wrong_status_question = Question(id=uuid4(), school_id=school_id, status="REVIEW")
        self.assertFalse(
            QuestionAuthorizationService.can_transition_status(director, wrong_status_question, "PUBLISHED")
        )

        approved_question = Question(id=uuid4(), school_id=school_id, status="APPROVED")
        self.assertTrue(
            QuestionAuthorizationService.can_transition_status(director, approved_question, "PUBLISHED")
        )

        # can_manage_question grants a TEACHER-author manage rights, but
        # PUBLISHED still requires DIRECTOR/COORDINATOR/PLATFORM_ADMIN.
        teacher_question = Question(
            id=uuid4(), school_id=school_id, status="APPROVED", author_external_id="teacher-a",
        )
        teacher_ctx = AuthenticatedUserContext(
            user_id="teacher-a", external_identity_id="teacher-a", role="TEACHER", school_id=str(school_id),
        )
        self.assertFalse(
            QuestionAuthorizationService.can_transition_status(teacher_ctx, teacher_question, "PUBLISHED")
        )

    def test_archived_target_requires_published_status_and_admin_role(self):
        school_id = uuid4()
        director = AuthenticatedUserContext(
            user_id="dir-1", external_identity_id="dir-1", role="DIRECTOR", school_id=str(school_id),
        )

        wrong_status_question = Question(id=uuid4(), school_id=school_id, status="APPROVED")
        self.assertFalse(
            QuestionAuthorizationService.can_transition_status(director, wrong_status_question, "ARCHIVED")
        )

        published_question = Question(id=uuid4(), school_id=school_id, status="PUBLISHED")
        self.assertTrue(
            QuestionAuthorizationService.can_transition_status(director, published_question, "ARCHIVED")
        )

    def test_unrecognized_target_status_denied(self):
        school_id = uuid4()
        question = Question(id=uuid4(), school_id=school_id, status="REVIEW")
        director = AuthenticatedUserContext(
            user_id="dir-1", external_identity_id="dir-1", role="DIRECTOR", school_id=str(school_id),
        )
        self.assertFalse(
            QuestionAuthorizationService.can_transition_status(director, question, "NOT_A_REAL_STATUS")
        )


if __name__ == "__main__":
    unittest.main()
