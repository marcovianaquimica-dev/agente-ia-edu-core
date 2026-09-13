import unittest

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AdminAuditLog, School, SchoolIdentityVersion
from agente_ia_edu.services.institution_settings import (
    IdentityVersionImmutableError,
    InstitutionSettingsService,
)


class TestInstitutionSettingsService(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _school(self, session) -> School:
        school = School(code="ESCOLA_R0", name="Escola R0")
        session.add(school)
        await session.flush()
        return school

    async def test_first_read_creates_a_formative_default(self):
        """A school with no settings row is formative until someone says
        otherwise. Formative is the configuration that produces no score at all,
        and therefore the safest default to fall into."""
        async with self.session_factory() as session:
            school = await self._school(session)
            settings = await InstitutionSettingsService(session).get_settings(school.id)
            self.assertEqual(settings.correction_mode, "FORMATIVO")
            self.assertIsNone(settings.validation_default)

    async def test_configure_writes_an_audit_entry(self):
        """Spec §5.3: who changed a school's mode, when, and from what to what,
        has to be answerable."""
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)

            await service.configure(
                school.id,
                performed_by_external_id="admin:master",
                correction_mode="AVALIATIVO",
                validation_default="EM_LOTE",
            )

            logs = (await session.execute(select(AdminAuditLog))).scalars().all()
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0].performed_by_external_id, "admin:master")
            self.assertIn("correction_mode", str(logs[0].metadata_))
            self.assertIn("FORMATIVO", str(logs[0].metadata_))
            self.assertIn("AVALIATIVO", str(logs[0].metadata_))

    async def test_configure_rejects_a_policy_in_formative_mode(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            with self.assertRaises(ValueError) as caught:
                await service.configure(
                    school.id,
                    performed_by_external_id="admin:master",
                    correction_mode="FORMATIVO",
                    validation_default="EM_LOTE",
                )
            self.assertIn("FORMATIVO", str(caught.exception))

    async def test_publishing_identity_increments_the_version(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)

            first = await service.publish_identity(
                school.id, performed_by_external_id="admin:master", display_name="Colégio A"
            )
            second = await service.publish_identity(
                school.id, performed_by_external_id="admin:master", display_name="Colégio B"
            )

            self.assertEqual(first.version, 1)
            self.assertEqual(second.version, 2)

            settings = await service.get_settings(school.id)
            self.assertEqual(settings.current_identity_version_id, second.id)

    async def test_a_published_identity_is_immutable(self):
        """Spec §5.2: February's devolutiva keeps February's mark. Editing a
        published version would rewrite a document someone already received."""
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            published = await service.publish_identity(
                school.id, performed_by_external_id="admin:master", display_name="Colégio A"
            )

            with self.assertRaises(IdentityVersionImmutableError):
                await service.amend_identity(published.id, display_name="Outro nome")

    async def test_publishing_identity_writes_an_audit_entry(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            await service.publish_identity(
                school.id, performed_by_external_id="admin:master", display_name="Colégio A"
            )
            count = await session.scalar(select(func.count()).select_from(AdminAuditLog))
            self.assertEqual(count, 1)

    async def test_the_previous_identity_version_survives(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            await service.publish_identity(
                school.id, performed_by_external_id="admin:master", display_name="Colégio A"
            )
            await service.publish_identity(
                school.id, performed_by_external_id="admin:master", display_name="Colégio B"
            )

            versions = (await session.execute(
                select(SchoolIdentityVersion).order_by(SchoolIdentityVersion.version)
            )).scalars().all()
            self.assertEqual([v.display_name for v in versions], ["Colégio A", "Colégio B"])
