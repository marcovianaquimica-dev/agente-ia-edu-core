"""Same bug, same fix, as tests/test_r0_question_classification_scope.py:
`_require_scope` in api/routes/question_extraction.py skipped its own check
entirely whenever an extraction run's/question's `school_id` was None -
combined with `_authorize`'s self-asserted-role fallback, this let anyone
claiming TEACHER/COORDINATOR/DIRECTOR manage any school-less extraction run
or question with no real relationship to it.
"""

import unittest

from agente_ia_edu.api.routes.question_extraction import _require_scope
from agente_ia_edu.identity import AuthenticatedUserContext
from fastapi import HTTPException


def _context(*, role: str, school_id=None, is_platform_admin: bool = False) -> AuthenticatedUserContext:
    return AuthenticatedUserContext(
        user_id="u", external_identity_id="u", role=role,
        school_id=school_id, is_platform_admin=is_platform_admin,
    )


class RequireScopeSchoolLessRunTests(unittest.TestCase):
    def test_self_asserted_teacher_denied_for_school_less_run(self):
        context = _context(role="TEACHER")
        with self.assertRaises(HTTPException) as cm:
            _require_scope(context, None)
        self.assertEqual(cm.exception.status_code, 403)

    def test_self_asserted_coordinator_denied_for_school_less_run(self):
        context = _context(role="COORDINATOR")
        with self.assertRaises(HTTPException) as cm:
            _require_scope(context, None)
        self.assertEqual(cm.exception.status_code, 403)

    def test_self_asserted_director_denied_for_school_less_run(self):
        context = _context(role="DIRECTOR")
        with self.assertRaises(HTTPException) as cm:
            _require_scope(context, None)
        self.assertEqual(cm.exception.status_code, 403)

    def test_real_platform_admin_allowed_for_school_less_run(self):
        context = _context(role="PLATFORM_ADMIN", is_platform_admin=True)
        _require_scope(context, None)  # must not raise

    def test_matching_school_still_allowed(self):
        context = _context(role="TEACHER", school_id="school-a")
        _require_scope(context, "school-a")  # must not raise

    def test_mismatched_school_still_denied(self):
        context = _context(role="TEACHER", school_id="school-a")
        with self.assertRaises(HTTPException) as cm:
            _require_scope(context, "school-b")
        self.assertEqual(cm.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
