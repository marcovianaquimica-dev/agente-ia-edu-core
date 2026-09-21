"""Same bug, same fix, as tests/test_r0_question_classification_scope.py and
tests/test_r0_question_extraction_scope.py: `_require_review_access` in
api/routes/authorial_ingestion.py skipped its own check entirely whenever an
ingestion review's `school_id` was None - combined with `_authorize`'s
self-asserted-role fallback, this let anyone claiming TEACHER/COORDINATOR/
DIRECTOR approve/reject/publish any school-less ingestion with no real
relationship to it. This route file has NO other test coverage at all
(confirmed by grep) - the route/service test file
(test_phase26_authorial_material_ingestion.py) only exercises the service
layer directly, never this HTTP-level gate.
"""

import asyncio
import unittest

from agente_ia_edu.api.routes.authorial_ingestion import _require_review_access
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext


def _context(*, role: str, school_id=None, is_platform_admin: bool = False) -> AuthenticatedUserContext:
    return AuthenticatedUserContext(
        user_id="u", external_identity_id="u", role=role,
        school_id=school_id, is_platform_admin=is_platform_admin,
    )


class _FakeReview:
    def __init__(self, school_id=None):
        self.school_id = school_id


class RequireReviewAccessSchoolLessIngestionTests(unittest.TestCase):
    def _identity(self):
        return ExternalIdentityContext(provider="test", external_user_id="u", roles=("teacher",))

    def test_self_asserted_teacher_denied_for_school_less_ingestion(self):
        context = _context(role="TEACHER")
        with self.assertRaises(Exception) as cm:
            asyncio.run(_require_review_access(_FakeReview(school_id=None), self._identity(), context))
        self.assertEqual(cm.exception.status_code, 403)

    def test_self_asserted_coordinator_denied_for_school_less_ingestion(self):
        context = _context(role="COORDINATOR")
        with self.assertRaises(Exception) as cm:
            asyncio.run(_require_review_access(_FakeReview(school_id=None), self._identity(), context))
        self.assertEqual(cm.exception.status_code, 403)

    def test_real_platform_admin_allowed_for_school_less_ingestion(self):
        context = _context(role="PLATFORM_ADMIN", is_platform_admin=True)
        asyncio.run(_require_review_access(_FakeReview(school_id=None), self._identity(), context))  # must not raise

    def test_matching_school_still_allowed(self):
        context = _context(role="TEACHER", school_id="school-a")
        asyncio.run(_require_review_access(_FakeReview(school_id="school-a"), self._identity(), context))  # must not raise

    def test_mismatched_school_still_denied(self):
        context = _context(role="TEACHER", school_id="school-a")
        with self.assertRaises(Exception) as cm:
            asyncio.run(_require_review_access(_FakeReview(school_id="school-b"), self._identity(), context))
        self.assertEqual(cm.exception.status_code, 403)


if __name__ == "__main__":
    unittest.main()
