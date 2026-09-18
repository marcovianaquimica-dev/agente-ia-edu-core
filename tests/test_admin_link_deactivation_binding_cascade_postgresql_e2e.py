"""PostgreSQL proof: deactivating a UserSchoolLink must not leave a live
PedagogicalUniverseBinding granting the unlinked user access to a school's
restricted catalog.

A platform admin can bind a specific person (subject_type=EXTERNAL_IDENTITY)
to a pedagogical universe owned by a school - e.g. granting a teacher access
to a restricted-subject catalog for that school. That binding is created
through the ``/admin`` pedagogical-universe routes, entirely independent of
the ``UserSchoolLink`` CRUD. When the admin later deactivates that person's
link to the school (``DELETE /schools/{id}/users/{link_id}``), nothing
touched the binding: ``PedagogicalUniverseService.authorized_universes``
matches EXTERNAL_IDENTITY bindings purely by external_user_id, with no
cross-check against the school link, so the unlinked person keeps resolving
the school's restricted universe forever.
"""

import asyncio
import os
import unittest

from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    PedagogicalUniverse,
    PedagogicalUniverseBinding,
    School,
    UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.pedagogical_universe import PedagogicalUniverseService


class AdminLinkDeactivationBindingCascadePostgreSQLE2E(unittest.TestCase):
    database_name = "agente_ia_edu_link_binding_cascade_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "LINK_BINDING_CASCADE_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    async_database_url = os.getenv(
        "LINK_BINDING_CASCADE_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin_execute("SELECT 1")
        except Exception as exc:
            raise unittest.SkipTest(
                "PostgreSQL de teste indisponivel para o cascade de desvinculacao"
            ) from exc
        cls._drop_database()
        cls._admin_execute(f"CREATE DATABASE {cls.database_name}")
        cls.engine = create_async_engine(cls.async_database_url)
        cls.session_factory = async_sessionmaker(
            cls.engine, class_=AsyncSession, expire_on_commit=False
        )

        async def create_schema():
            async with cls.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)

        asyncio.run(create_schema())

    @classmethod
    def tearDownClass(cls):
        asyncio.run(cls.engine.dispose())
        cls._drop_database()

    @classmethod
    def _admin_execute(cls, statement):
        engine = create_engine(
            cls.admin_url,
            connect_args={"autocommit": True},
            execution_options={"isolation_level": "AUTOCOMMIT"},
        )
        try:
            with engine.connect() as connection:
                return connection.execute(text(statement))
        finally:
            engine.dispose()

    @classmethod
    def _drop_database(cls):
        try:
            cls._admin_execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{cls.database_name}' AND pid <> pg_backend_pid()"
            )
            cls._admin_execute(f"DROP DATABASE IF EXISTS {cls.database_name}")
        except Exception:
            pass

    async def _binding_active(self, binding_id) -> bool:
        async with self.session_factory() as session:
            binding = await session.get(PedagogicalUniverseBinding, binding_id)
            return bool(binding.active)

    async def _authorized_universe_ids(self, identity: ExternalIdentityContext) -> set:
        async with self.session_factory() as session:
            universes = await PedagogicalUniverseService(session).authorized_universes(identity)
            return {universe.id for universe in universes}

    def test_unlinking_user_deactivates_their_binding_to_the_schools_universe(self):
        """The core leak: a restricted school catalog stays reachable after unlink."""

        async def scenario():
            async with self.session_factory() as session:
                admin_service = PlatformAdminService(session)
                school = await admin_service.create_school(
                    performed_by_external_id="admin:master",
                    code="ESCOLA_CASCADE_1",
                    name="Escola Cascade 1",
                )
                link = await admin_service.link_user_to_school(
                    performed_by_external_id="admin:master",
                    external_user_id="user:teacher_cascade_1",
                    role=AdminRole.TEACHER,
                    scope_type=AdminScopeType.SCHOOL,
                    school_id=school.id,
                )

                universe = PedagogicalUniverse(
                    external_id="cascade-1",
                    slug="cascade-1",
                    name="Universo Restrito Cascade 1",
                    status="ACTIVE",
                    owner_type="SCHOOL",
                    owner_external_id=str(school.id),
                )
                session.add(universe)
                await session.flush()
                binding = PedagogicalUniverseBinding(
                    universe_id=universe.id,
                    subject_type="EXTERNAL_IDENTITY",
                    subject_external_id="user:teacher_cascade_1",
                    active=True,
                )
                session.add(binding)
                await session.commit()
                await session.refresh(binding)

                identity = ExternalIdentityContext(
                    provider="test",
                    external_user_id="user:teacher_cascade_1",
                    roles=("teacher",),
                )

                # Sanity check: before deactivation, the binding does grant access.
                before = await PedagogicalUniverseService(session).authorized_universes(identity)
                self.assertIn(universe.id, {item.id for item in before})

                return link.id, binding.id, universe.id, identity

        link_id, binding_id, universe_id, identity = asyncio.run(scenario())

        async def deactivate():
            async with self.session_factory() as session:
                await PlatformAdminService(session).deactivate_user_link(
                    performed_by_external_id="admin:master",
                    link_id=link_id,
                )

        asyncio.run(deactivate())

        self.assertFalse(
            asyncio.run(self._binding_active(binding_id)),
            "Binding EXTERNAL_IDENTITY para o universo da escola deveria ter sido "
            "desativado junto com o vinculo do usuario.",
        )
        self.assertNotIn(
            universe_id,
            asyncio.run(self._authorized_universe_ids(identity)),
            "Usuario desvinculado nao deveria mais resolver o universo pedagogico "
            "restrito da escola.",
        )

    def test_binding_survives_when_another_active_link_to_same_school_remains(self):
        """Deactivating one of two links to the same school must not touch the binding."""

        async def scenario():
            async with self.session_factory() as session:
                admin_service = PlatformAdminService(session)
                school = await admin_service.create_school(
                    performed_by_external_id="admin:master",
                    code="ESCOLA_CASCADE_2",
                    name="Escola Cascade 2",
                )
                link_teacher = await admin_service.link_user_to_school(
                    performed_by_external_id="admin:master",
                    external_user_id="user:teacher_cascade_2",
                    role=AdminRole.TEACHER,
                    scope_type=AdminScopeType.SCHOOL,
                    school_id=school.id,
                )
                link_coordinator = await admin_service.link_user_to_school(
                    performed_by_external_id="admin:master",
                    external_user_id="user:teacher_cascade_2",
                    role=AdminRole.COORDINATOR,
                    scope_type=AdminScopeType.SCHOOL,
                    school_id=school.id,
                )

                universe = PedagogicalUniverse(
                    external_id="cascade-2",
                    slug="cascade-2",
                    name="Universo Restrito Cascade 2",
                    status="ACTIVE",
                    owner_type="SCHOOL",
                    owner_external_id=str(school.id),
                )
                session.add(universe)
                await session.flush()
                binding = PedagogicalUniverseBinding(
                    universe_id=universe.id,
                    subject_type="EXTERNAL_IDENTITY",
                    subject_external_id="user:teacher_cascade_2",
                    active=True,
                )
                session.add(binding)
                await session.commit()
                await session.refresh(binding)
                return link_teacher.id, binding.id

        link_teacher_id, binding_id = asyncio.run(scenario())

        async def deactivate():
            async with self.session_factory() as session:
                await PlatformAdminService(session).deactivate_user_link(
                    performed_by_external_id="admin:master",
                    link_id=link_teacher_id,
                )

        asyncio.run(deactivate())

        self.assertTrue(
            asyncio.run(self._binding_active(binding_id)),
            "Binding nao deveria ser desativado enquanto o usuario ainda tem outro "
            "vinculo ativo com a mesma escola.",
        )

    def test_platform_owned_binding_survives_school_unlink(self):
        """A binding to a PLATFORM-owned universe is not the school's to revoke."""

        async def scenario():
            async with self.session_factory() as session:
                admin_service = PlatformAdminService(session)
                school = await admin_service.create_school(
                    performed_by_external_id="admin:master",
                    code="ESCOLA_CASCADE_3",
                    name="Escola Cascade 3",
                )
                link = await admin_service.link_user_to_school(
                    performed_by_external_id="admin:master",
                    external_user_id="user:teacher_cascade_3",
                    role=AdminRole.TEACHER,
                    scope_type=AdminScopeType.SCHOOL,
                    school_id=school.id,
                )

                universe = PedagogicalUniverse(
                    external_id="cascade-3-platform",
                    slug="cascade-3-platform",
                    name="Universo Plataforma Cascade 3",
                    status="ACTIVE",
                    owner_type="PLATFORM",
                )
                session.add(universe)
                await session.flush()
                binding = PedagogicalUniverseBinding(
                    universe_id=universe.id,
                    subject_type="EXTERNAL_IDENTITY",
                    subject_external_id="user:teacher_cascade_3",
                    active=True,
                )
                session.add(binding)
                await session.commit()
                await session.refresh(binding)
                return link.id, binding.id

        link_id, binding_id = asyncio.run(scenario())

        async def deactivate():
            async with self.session_factory() as session:
                await PlatformAdminService(session).deactivate_user_link(
                    performed_by_external_id="admin:master",
                    link_id=link_id,
                )

        asyncio.run(deactivate())

        self.assertTrue(
            asyncio.run(self._binding_active(binding_id)),
            "Binding para universo de plataforma (owner_type=PLATFORM) nao "
            "pertence a essa escola e nao deveria ser desativado.",
        )


if __name__ == "__main__":
    unittest.main()
