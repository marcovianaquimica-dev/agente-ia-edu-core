"""Service-level coverage for src/agente_ia_edu/services/admin.py (PlatformAdminService).

Wave 7 of the overnight bug-hunting campaign. tests/test_platform_administration.py
and tests/test_admin_http.py already cover the happy paths (create/update a
school, enable/disable a NEW module, link/deactivate a user, list logs). This
file targets the still-uncovered validation branches (invalid status/scope/role,
"not found" ValueErrors), the get_school(code) string-lookup branch (never
exercised anywhere - every existing caller passes a uuid.UUID), the
RE-enable-an-existing-module and update-existing-module-metadata branches, the
action_filter branch of list_audit_logs, and - since admin.py is explicitly
flagged as security-sensitive - two direct behavioral checks of the
deactivate_user_link -> PedagogicalUniverseBinding cascade (the same rule
tests/test_admin_link_deactivation_binding_cascade_postgresql_e2e.py proves
against real PostgreSQL, reproduced here on SQLite so it runs without infra).

Uses expire_on_commit=True (the production default in
agente_ia_edu.db.session.create_session_factory()) rather than the
expire_on_commit=False used by the older admin test files, per the campaign's
MissingGreenlet-after-commit bug-class focus - PlatformAdminService methods
that `commit()` then `refresh()` an object are exercised under the exact
setting that would expose a missed refresh.
"""

from __future__ import annotations

import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    PedagogicalUniverse,
    PedagogicalUniverseBinding,
)
from agente_ia_edu.services.admin import (
    AdminRole,
    AdminScopeType,
    PlatformAdminService,
    PlatformModuleKey,
    SchoolStatus,
)


class AdminServiceCoverage(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # expire_on_commit=True mirrors production's create_session_factory()
        # (no override) - deliberately NOT relaxed, per campaign convention.
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession)

    async def asyncTearDown(self):
        await self.engine.dispose()

    # ---- create_school: invalid status (line 98) ------------------------

    async def test_create_school_invalid_status_raises(self):
        async with self.session_factory() as session:
            svc = PlatformAdminService(session)
            with self.assertRaises(ValueError):
                await svc.create_school(
                    performed_by_external_id="admin:master",
                    code="BAD-STATUS",
                    name="Escola Status Ruim",
                    status="NOT_A_STATUS",
                )

    # ---- update_school: short_name / metadata / invalid status ---------
    # (lines 160-161, 166, 171-172)

    async def test_update_school_short_name_and_metadata_updated(self):
        async with self.session_factory() as session:
            svc = PlatformAdminService(session)
            school = await svc.create_school(
                performed_by_external_id="admin:master", code="UPD-META", name="Escola Update",
            )
            school_id = school.id

            updated = await svc.update_school(
                performed_by_external_id="admin:master",
                school_id=school_id,
                short_name="Nova Sigla",
                metadata={"plan": "premium"},
            )
            self.assertEqual(updated.short_name, "Nova Sigla")
            self.assertEqual(updated.metadata_, {"plan": "premium"})

    async def test_update_school_short_name_cleared_by_empty_string(self):
        # short_name is not None (an empty string still goes through the
        # `short_name is not None` branch) but is falsy -> stored as None.
        async with self.session_factory() as session:
            svc = PlatformAdminService(session)
            school = await svc.create_school(
                performed_by_external_id="admin:master", code="UPD-CLEAR", name="Escola Clear",
                short_name="Original",
            )
            school_id = school.id

            updated = await svc.update_school(
                performed_by_external_id="admin:master", school_id=school_id, short_name="",
            )
            self.assertIsNone(updated.short_name)

    async def test_update_school_invalid_status_raises(self):
        async with self.session_factory() as session:
            svc = PlatformAdminService(session)
            school = await svc.create_school(
                performed_by_external_id="admin:master", code="UPD-BADSTATUS", name="Escola X",
            )
            school_id = school.id
            with self.assertRaises(ValueError):
                await svc.update_school(
                    performed_by_external_id="admin:master", school_id=school_id,
                    status="NOT_A_STATUS",
                )

    # ---- get_school: string-code / non-UUID lookup (lines 193-197) -----
    # No existing caller (route or test) ever passes get_school() a plain
    # string - api/routes/admin.py's path param is typed uuid.UUID by
    # FastAPI, so the `else` branch of get_school() was entirely dead.

    async def test_get_school_by_uuid_string(self):
        async with self.session_factory() as session:
            svc = PlatformAdminService(session)
            school = await svc.create_school(
                performed_by_external_id="admin:master", code="BY-UUID-STR", name="Escola UUID Str",
            )
            school_id = school.id

            fetched = await svc.get_school(str(school_id))
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.code, "BY-UUID-STR")

    async def test_get_school_by_code_string(self):
        async with self.session_factory() as session:
            svc = PlatformAdminService(session)
            await svc.create_school(
                performed_by_external_id="admin:master", code="by-code-str", name="Escola Codigo",
            )

            fetched = await svc.get_school("by-code-str")  # lowercase - code is normalized upper
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.name, "Escola Codigo")

    async def test_get_school_by_unknown_code_string_returns_none(self):
        async with self.session_factory() as session:
            svc = PlatformAdminService(session)
            fetched = await svc.get_school("NO-SUCH-SCHOOL-CODE")
            self.assertIsNone(fetched)

    # ---- configure_school_module: re-enable + metadata-on-existing -----
    # (lines 251, 255). test_platform_administration.py's test_03 only
    # exercises: module doesn't exist yet -> create enabled; module exists
    # -> disable. It never re-enables an EXISTING (already-disabled) module,
    # nor ever passes metadata while the module row already exists.

    async def test_configure_school_module_reenable_with_metadata(self):
        async with self.session_factory() as session:
            svc = PlatformAdminService(session)
            school = await svc.create_school(
                performed_by_external_id="admin:master", code="MOD-REENABLE", name="Escola Modulo",
            )
            school_id = school.id
            # create_school already enabled AGENTE_IA_EDU by default - disable it first
            disabled = await svc.configure_school_module(
                performed_by_external_id="admin:master", school_id=school_id,
                module_key=PlatformModuleKey.AGENTE_IA_EDU, enabled=False,
            )
            self.assertFalse(disabled.enabled)
            self.assertIsNotNone(disabled.deactivated_at)

            reenabled = await svc.configure_school_module(
                performed_by_external_id="admin:master", school_id=school_id,
                module_key=PlatformModuleKey.AGENTE_IA_EDU, enabled=True,
                metadata={"tier": "gold"},
            )
            self.assertTrue(reenabled.enabled)
            self.assertIsNone(reenabled.deactivated_at)
            self.assertEqual(reenabled.metadata_, {"tier": "gold"})

    # ---- link_user_to_school validation branches (lines 317, 320, 325) -

    async def test_link_user_invalid_scope_type_raises(self):
        async with self.session_factory() as session:
            svc = PlatformAdminService(session)
            with self.assertRaises(ValueError):
                await svc.link_user_to_school(
                    performed_by_external_id="admin:master", external_user_id="u1",
                    role=AdminRole.TEACHER, scope_type="NOT_A_SCOPE",
                    school_id=uuid.uuid4(),
                )

    async def test_link_user_missing_school_id_for_non_admin_role_raises(self):
        async with self.session_factory() as session:
            svc = PlatformAdminService(session)
            with self.assertRaises(ValueError):
                await svc.link_user_to_school(
                    performed_by_external_id="admin:master", external_user_id="u2",
                    role=AdminRole.TEACHER, scope_type=AdminScopeType.SCHOOL,
                    school_id=None,
                )

    async def test_link_user_nonexistent_school_raises(self):
        async with self.session_factory() as session:
            svc = PlatformAdminService(session)
            with self.assertRaises(ValueError):
                await svc.link_user_to_school(
                    performed_by_external_id="admin:master", external_user_id="u3",
                    role=AdminRole.TEACHER, scope_type=AdminScopeType.SCHOOL,
                    school_id=uuid.uuid4(),
                )

    async def test_link_user_platform_admin_role_needs_no_school_id(self):
        # sanity: the ONLY role allowed to skip school_id
        async with self.session_factory() as session:
            svc = PlatformAdminService(session)
            link = await svc.link_user_to_school(
                performed_by_external_id="admin:master", external_user_id="u4",
                role=AdminRole.PLATFORM_ADMIN, scope_type=AdminScopeType.PLATFORM,
                school_id=None,
            )
            self.assertIsNone(link.school_id)
            self.assertTrue(await svc.is_platform_admin("u4"))

    # ---- deactivate_user_link: not-found (line 379) --------------------

    async def test_deactivate_user_link_not_found_raises(self):
        async with self.session_factory() as session:
            svc = PlatformAdminService(session)
            with self.assertRaises(ValueError):
                await svc.deactivate_user_link(
                    performed_by_external_id="admin:master", link_id=uuid.uuid4(),
                )

    # ---- list_audit_logs: action_filter (line 501) ----------------------

    async def test_list_audit_logs_action_filter(self):
        async with self.session_factory() as session:
            svc = PlatformAdminService(session)
            school = await svc.create_school(
                performed_by_external_id="admin:master", code="AUDIT-FILTER", name="Escola Audit",
            )
            school_id = school.id
            await svc.update_school(
                performed_by_external_id="admin:master", school_id=school_id, name="Escola Audit 2",
            )

            all_logs = await svc.list_audit_logs(school_id=school_id)
            self.assertGreaterEqual(len(all_logs), 2)  # SCHOOL_CREATED + SCHOOL_UPDATED

            filtered = await svc.list_audit_logs(school_id=school_id, action_filter="school_updated")
            self.assertTrue(filtered)
            self.assertTrue(all(log.action == "SCHOOL_UPDATED" for log in filtered))

    # ---- security-sensitive: deactivate_user_link's binding cascade ----
    # Direct SQLite re-proof of the rule
    # test_admin_link_deactivation_binding_cascade_postgresql_e2e.py verifies
    # against real PostgreSQL (which is skipped whenever the local PG test
    # instance on port 5433 is unavailable, e.g. in a plain checkout with no
    # docker running). This adds a lightweight, always-runs re-check of the
    # same two hypotheses: (1) the binding IS deactivated when the
    # deactivated link was the user's last active tie to that school, and
    # (2) the binding must NOT be touched while another active link to the
    # same school still exists.

    async def _seed_binding(self, session, *, school_id, external_user_id) -> uuid.UUID:
        universe = PedagogicalUniverse(
            external_id=f"univ-{uuid.uuid4().hex[:8]}", slug=f"slug-{uuid.uuid4().hex[:8]}",
            name="Universo Restrito", status="ACTIVE", owner_type="SCHOOL",
            owner_external_id=str(school_id),
        )
        session.add(universe)
        await session.flush()
        binding = PedagogicalUniverseBinding(
            universe_id=universe.id, subject_type="EXTERNAL_IDENTITY",
            subject_external_id=external_user_id, active=True,
        )
        session.add(binding)
        await session.flush()
        binding_id = binding.id
        await session.commit()
        return binding_id

    async def test_deactivate_last_link_cascades_binding_inactive(self):
        async with self.session_factory() as session:
            svc = PlatformAdminService(session)
            school = await svc.create_school(
                performed_by_external_id="admin:master", code="CASCADE-LAST", name="Escola Cascade",
            )
            school_id = school.id
            binding_id = await self._seed_binding(
                session, school_id=school_id, external_user_id="teacher-last-link",
            )
            link = await svc.link_user_to_school(
                performed_by_external_id="admin:master", external_user_id="teacher-last-link",
                role=AdminRole.TEACHER, scope_type=AdminScopeType.CLASSROOM,
                school_id=school_id, scope_external_id="CLASS-A",
            )
            link_id = link.id

            deactivated = await svc.deactivate_user_link(
                performed_by_external_id="admin:master", link_id=link_id,
            )
            self.assertFalse(deactivated.active)

            binding = await session.get(PedagogicalUniverseBinding, binding_id)
            self.assertFalse(binding.active)

    async def test_deactivate_one_of_two_links_preserves_binding(self):
        async with self.session_factory() as session:
            svc = PlatformAdminService(session)
            school = await svc.create_school(
                performed_by_external_id="admin:master", code="CASCADE-MULTI", name="Escola Multi",
            )
            school_id = school.id
            binding_id = await self._seed_binding(
                session, school_id=school_id, external_user_id="teacher-two-links",
            )
            link_a = await svc.link_user_to_school(
                performed_by_external_id="admin:master", external_user_id="teacher-two-links",
                role=AdminRole.TEACHER, scope_type=AdminScopeType.CLASSROOM,
                school_id=school_id, scope_external_id="CLASS-A",
            )
            # NOTE: capture link_a's id NOW. link_user_to_school() commits
            # internally (expire_on_commit=True), so the SECOND call below
            # expires this already-returned `link_a` instance the moment it
            # commits - reading link_a.id afterwards would need a fresh
            # lazy-load with no async context available and raise
            # MissingGreenlet. This is the same after-commit-expiry hazard
            # the campaign targets, just triggered by the TEST holding two
            # objects across two commits on one shared session rather than
            # by admin.py itself - the real HTTP route only ever calls
            # link_user_to_school() once per request/session.
            link_a_id = link_a.id

            link_b = await svc.link_user_to_school(
                performed_by_external_id="admin:master", external_user_id="teacher-two-links",
                role=AdminRole.COORDINATOR, scope_type=AdminScopeType.SCHOOL,
                school_id=school_id,
            )
            link_b_id = link_b.id

            await svc.deactivate_user_link(
                performed_by_external_id="admin:master", link_id=link_a_id,
            )

            binding = await session.get(PedagogicalUniverseBinding, binding_id)
            self.assertTrue(
                binding.active,
                "the binding must stay alive while another active link to the same "
                "school (link_b, COORDINATOR/SCHOOL) still ties the user to it",
            )
            # sanity: link_b itself is untouched
            refreshed_b = await session.get(type(link_a), link_b_id)
            self.assertTrue(refreshed_b.active)


if __name__ == "__main__":
    unittest.main()
