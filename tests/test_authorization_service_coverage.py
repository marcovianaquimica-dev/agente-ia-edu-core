"""Service-layer coverage for agente_ia_edu.services.authorization.

AuthorizationService is the shared authz/scoping core nearly every route
depends on (via resolve_context + the require_* gates), so this targets the
branches left uncovered by test_identity_authorization.py,
test_r0_authorization_discipline.py and test_r0_fase2_gate.py:

- resolve_context's role_hint branch when the user HAS active links (picks
  an explicit link among several, or falls back safely when role_hint
  matches none of the user's real links - it never fabricates a role/link
  the user doesn't actually hold).
- resolve_context's fallback branch (no active links at all): the "ADMIN"
  alias normalizing to "PLATFORM_ADMIN", and an unrecognized role collapsing
  to the safe "STUDENT" default.
- require_school_access / require_module / require_scope: these are pure
  functions of an already-resolved AuthenticatedUserContext with no DB
  access (verified by reading authorization.py - none of the three touch
  self.session), so they're tested directly against constructed contexts,
  matching the no-session convention used by
  test_question_governance_service_coverage.py for similar pure functions.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.db.base import Base
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.authorization import AuthorizationService
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool


class TestResolveContextRoleHintAmongRealLinks(unittest.IsolatedAsyncioTestCase):
    """role_hint (lines 97-101) only ever *selects among the user's own real
    UserSchoolLink rows* - it cannot be used to assert a role/scope the user
    doesn't actually hold. That's the property worth locking down given this
    exact bug shape (self-asserted role bypassing a real relationship check)
    has already been found and fixed twice tonight in other files.
    """

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_role_hint_selects_the_explicit_lower_ranked_link(self):
        async with self.session_factory() as session:
            admin = PlatformAdminService(session)
            school = await admin.create_school(
                performed_by_external_id="admin:master", code="RH_SCHOOL_1", name="Role Hint School 1",
            )
            await admin.link_user_to_school(
                performed_by_external_id="admin:master", external_user_id="dual-role-user",
                role=AdminRole.TEACHER, scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id, scope_external_id="TURMA_T1",
            )
            await admin.link_user_to_school(
                performed_by_external_id="admin:master", external_user_id="dual-role-user",
                role=AdminRole.COORDINATOR, scope_type=AdminScopeType.SCHOOL, school_id=school.id,
            )

            authz = AuthorizationService(session)
            identity = ExternalIdentityContext(
                provider="TestProvider", external_user_id="dual-role-user", roles=("TEACHER",),
            )

            # No role_hint: COORDINATOR outranks TEACHER, selected by default.
            default_context = await authz.resolve_context(identity)
            self.assertEqual(default_context.role, "COORDINATOR")

            # role_hint explicitly picks the user's own (lower-ranked) TEACHER
            # link - a real relationship, just not the highest-priority one.
            hinted_context = await authz.resolve_context(identity, role_hint="teacher")
            self.assertEqual(hinted_context.role, "TEACHER")
            self.assertEqual(hinted_context.scope_external_id, "TURMA_T1")

    async def test_role_hint_matching_no_real_link_falls_back_to_highest_ranked(self):
        async with self.session_factory() as session:
            admin = PlatformAdminService(session)
            school = await admin.create_school(
                performed_by_external_id="admin:master", code="RH_SCHOOL_2", name="Role Hint School 2",
            )
            await admin.link_user_to_school(
                performed_by_external_id="admin:master", external_user_id="single-role-user",
                role=AdminRole.TEACHER, scope_type=AdminScopeType.SCHOOL, school_id=school.id,
            )

            authz = AuthorizationService(session)
            identity = ExternalIdentityContext(
                provider="TestProvider", external_user_id="single-role-user", roles=("TEACHER",),
            )

            # role_hint asserts a role ("DIRECTOR") this user has no real
            # link for at all. It must NOT fabricate a DIRECTOR context -
            # the explicit-match search finds nothing, so `selected` stays
            # the user's actual (TEACHER) link.
            context = await authz.resolve_context(identity, role_hint="director")
            self.assertEqual(context.role, "TEACHER")
            self.assertEqual(str(context.school_id), str(school.id))


class TestResolveContextFallbackRoleNormalization(unittest.IsolatedAsyncioTestCase):
    """No active links at all (lines 142-146): fallback_role derivation and
    normalization, independent of the (already separately tested in
    test_identity_authorization.py / test_phase16_security.py)
    is_platform_admin=True-for-PLATFORM_ADMIN behavior.
    """

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_admin_alias_normalizes_to_platform_admin(self):
        async with self.session_factory() as session:
            authz = AuthorizationService(session)
            identity = ExternalIdentityContext(
                provider="TestProvider", external_user_id="raw-admin-claim", roles=("admin",),
            )
            context = await authz.resolve_context(identity)
            self.assertEqual(context.role, "PLATFORM_ADMIN")
            self.assertTrue(context.is_platform_admin)

    async def test_unrecognized_role_collapses_to_safe_student_default(self):
        async with self.session_factory() as session:
            authz = AuthorizationService(session)
            identity = ExternalIdentityContext(
                provider="TestProvider", external_user_id="garbled-role-claim", roles=("SUPERUSER",),
            )
            context = await authz.resolve_context(identity)
            self.assertEqual(context.role, "STUDENT")
            self.assertFalse(context.is_platform_admin)


class TestRequireSchoolAccess(unittest.IsolatedAsyncioTestCase):
    """Pure function of an already-resolved context - no DB access."""

    async def test_inactive_user_is_refused(self):
        result = await AuthorizationService(session=None).require_school_access(
            AuthenticatedUserContext(
                user_id="u1", external_identity_id="u1", role="TEACHER",
                school_id="school-1", is_active=False,
            ),
            "school-1",
        )
        self.assertFalse(result.allowed)
        self.assertIn("inactive", result.reason)

    async def test_context_without_school_is_refused(self):
        result = await AuthorizationService(session=None).require_school_access(
            AuthenticatedUserContext(
                user_id="u2", external_identity_id="u2", role="STUDENT", school_id=None,
            ),
            "school-1",
        )
        self.assertFalse(result.allowed)
        self.assertIn("does not belong to a school", result.reason)

    async def test_mismatched_school_is_refused(self):
        result = await AuthorizationService(session=None).require_school_access(
            AuthenticatedUserContext(
                user_id="u3", external_identity_id="u3", role="TEACHER", school_id="school-1",
            ),
            "school-2",
        )
        self.assertFalse(result.allowed)
        self.assertIn("does not have access", result.reason)

    async def test_matching_school_is_allowed(self):
        result = await AuthorizationService(session=None).require_school_access(
            AuthenticatedUserContext(
                user_id="u4", external_identity_id="u4", role="TEACHER", school_id="school-1",
            ),
            "school-1",
        )
        self.assertTrue(result.allowed)
        self.assertIsNone(result.reason)


class TestRequireRole(unittest.IsolatedAsyncioTestCase):
    async def test_role_not_in_allowed_set_is_refused(self):
        context = AuthenticatedUserContext(
            user_id="r1", external_identity_id="r1", role="STUDENT",
        )
        result = await AuthorizationService(session=None).require_role(context, "TEACHER", "DIRECTOR")
        self.assertFalse(result.allowed)
        self.assertEqual(result.reason, "Role required: DIRECTOR, TEACHER")


class TestRequireModule(unittest.IsolatedAsyncioTestCase):
    async def test_independent_student_without_school_is_always_allowed(self):
        result = await AuthorizationService(session=None).require_module(
            AuthenticatedUserContext(
                user_id="s1", external_identity_id="s1", role="STUDENT", school_id=None,
            ),
            "ANY_MODULE",
        )
        self.assertTrue(result.allowed)

    async def test_school_bound_user_needs_the_module_enabled(self):
        context = AuthenticatedUserContext(
            user_id="t1", external_identity_id="t1", role="TEACHER",
            school_id="school-1", modules=("ESSAY",),
        )
        allowed = await AuthorizationService(session=None).require_module(context, "essay")
        self.assertTrue(allowed.allowed)

        refused = await AuthorizationService(session=None).require_module(context, "DIAGNOSTIC")
        self.assertFalse(refused.allowed)
        self.assertIn("not enabled", refused.reason)


class TestRequireScope(unittest.IsolatedAsyncioTestCase):
    async def test_scope_type_mismatch_is_refused(self):
        context = AuthenticatedUserContext(
            user_id="c1", external_identity_id="c1", role="TEACHER",
            scope_type="CLASSROOM", scope_external_id="TURMA_A",
        )
        result = await AuthorizationService(session=None).require_scope(context, scope_type="SCHOOL")
        self.assertFalse(result.allowed)
        self.assertIn("Scope type mismatch", result.reason)

    async def test_scope_external_id_mismatch_is_refused(self):
        context = AuthenticatedUserContext(
            user_id="c2", external_identity_id="c2", role="TEACHER",
            scope_type="CLASSROOM", scope_external_id="TURMA_A",
        )
        result = await AuthorizationService(session=None).require_scope(
            context, scope_type="CLASSROOM", scope_external_id="TURMA_B",
        )
        self.assertFalse(result.allowed)
        self.assertIn("Scope mismatch", result.reason)

    async def test_matching_scope_is_allowed(self):
        context = AuthenticatedUserContext(
            user_id="c3", external_identity_id="c3", role="TEACHER",
            scope_type="CLASSROOM", scope_external_id="TURMA_A",
        )
        result = await AuthorizationService(session=None).require_scope(
            context, scope_type="CLASSROOM", scope_external_id="TURMA_A",
        )
        self.assertTrue(result.allowed)
        self.assertIsNone(result.reason)

    async def test_no_scope_filters_always_allowed(self):
        context = AuthenticatedUserContext(
            user_id="c4", external_identity_id="c4", role="TEACHER",
        )
        result = await AuthorizationService(session=None).require_scope(context)
        self.assertTrue(result.allowed)


if __name__ == "__main__":
    unittest.main()
