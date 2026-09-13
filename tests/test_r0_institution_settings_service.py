import unittest

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AdminAuditLog,
    Person,
    School,
    SchoolIdentityVersion,
    User,
)
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

    async def _author(self, session, school) -> User:
        """Publishing an identity requires naming who published it."""
        person = Person(school_id=school.id, full_name="Diretora Marta")
        session.add(person)
        await session.flush()
        user = User(
            school_id=school.id,
            person_id=person.id,
            external_identity_provider="host",
            external_user_id="host:marta",
        )
        session.add(user)
        await session.flush()
        return user

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
            author = await self._author(session, school)

            first = await service.publish_identity(
                school.id,
                performed_by_external_id="admin:master",
                published_by_user_id=author.id,
                display_name="Colégio A",
            )
            second = await service.publish_identity(
                school.id,
                performed_by_external_id="admin:master",
                published_by_user_id=author.id,
                display_name="Colégio B",
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
            author = await self._author(session, school)
            published = await service.publish_identity(
                school.id,
                performed_by_external_id="admin:master",
                published_by_user_id=author.id,
                display_name="Colégio A",
            )

            with self.assertRaises(IdentityVersionImmutableError):
                await service.amend_identity(published.id, display_name="Outro nome")

    async def test_publishing_identity_writes_an_audit_entry(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            author = await self._author(session, school)
            await service.publish_identity(
                school.id,
                performed_by_external_id="admin:master",
                published_by_user_id=author.id,
                display_name="Colégio A",
            )
            count = await session.scalar(select(func.count()).select_from(AdminAuditLog))
            self.assertEqual(count, 1)

    async def test_the_previous_identity_version_survives(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            author = await self._author(session, school)
            await service.publish_identity(
                school.id,
                performed_by_external_id="admin:master",
                published_by_user_id=author.id,
                display_name="Colégio A",
            )
            await service.publish_identity(
                school.id,
                performed_by_external_id="admin:master",
                published_by_user_id=author.id,
                display_name="Colégio B",
            )

            versions = (await session.execute(
                select(SchoolIdentityVersion).order_by(SchoolIdentityVersion.version)
            )).scalars().all()
            self.assertEqual([v.display_name for v in versions], ["Colégio A", "Colégio B"])

    async def test_validate_threshold_points_below_range(self):
        """validation_threshold_points must be validated before it reaches the
        database. An out-of-range value should raise ValueError, not IntegrityError."""
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            with self.assertRaises(ValueError) as caught:
                await service.configure(
                    school.id,
                    performed_by_external_id="admin:master",
                    correction_mode="AVALIATIVO",
                    validation_threshold_points=-1,
                )
            self.assertIn("validation_threshold_points", str(caught.exception))
            # Confirm no audit row was written due to validation failure
            count = await session.scalar(select(func.count()).select_from(AdminAuditLog))
            self.assertEqual(count, 0)

    async def test_validate_threshold_points_above_range(self):
        """validation_threshold_points must not exceed 1000."""
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            with self.assertRaises(ValueError) as caught:
                await service.configure(
                    school.id,
                    performed_by_external_id="admin:master",
                    correction_mode="AVALIATIVO",
                    validation_threshold_points=5000,
                )
            self.assertIn("validation_threshold_points", str(caught.exception))
            # Confirm no audit row was written due to validation failure
            count = await session.scalar(select(func.count()).select_from(AdminAuditLog))
            self.assertEqual(count, 0)

    async def test_validate_threshold_points_boundary_zero(self):
        """validation_threshold_points = 0 is valid (boundary value)."""
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            settings = await service.configure(
                school.id,
                performed_by_external_id="admin:master",
                correction_mode="AVALIATIVO",
                validation_threshold_points=0,
            )
            self.assertEqual(settings.validation_threshold_points, 0)

    async def test_validate_threshold_points_boundary_1000(self):
        """validation_threshold_points = 1000 is valid (boundary value)."""
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            settings = await service.configure(
                school.id,
                performed_by_external_id="admin:master",
                correction_mode="AVALIATIVO",
                validation_threshold_points=1000,
            )
            self.assertEqual(settings.validation_threshold_points, 1000)

    async def test_configure_with_no_changes_writes_no_audit_row(self):
        """Calling configure with no changes should return the settings unchanged
        without writing an audit row."""
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)

            # Get the initial settings
            initial_settings = await service.get_settings(school.id)
            initial_id = initial_settings.id

            # Call configure with no changes
            result = await service.configure(
                school.id,
                performed_by_external_id="admin:master",
            )

            # Confirm the same settings object is returned
            self.assertEqual(result.id, initial_id)

            # Confirm no audit row was written
            count = await session.scalar(select(func.count()).select_from(AdminAuditLog))
            self.assertEqual(count, 0)

    async def test_publishing_records_who_published_it(self):
        """I3: the audit column used to be structurally unreachable. The only
        writer neither accepted nor set it, so it was NULL for ever."""
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            author = await self._author(session, school)

            version = await service.publish_identity(
                school.id,
                performed_by_external_id="admin:master",
                published_by_user_id=author.id,
                display_name="Colégio A",
            )
            self.assertEqual(version.published_by_user_id, author.id)

    async def test_publishing_without_an_author_is_a_type_error(self):
        """Required, not optional: an identity nobody published cannot be
        expressed at all."""
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            with self.assertRaises(TypeError):
                await service.publish_identity(
                    school.id,
                    performed_by_external_id="admin:master",
                    display_name="Colégio A",
                )

    async def test_a_colour_that_is_not_hexadecimal_raises_value_error(self):
        """I2: the service passed the value straight through, so 'banana' was
        persisted. The database refuses it too - this is so the caller reads a
        message naming the field instead of a constraint name."""
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            author = await self._author(session, school)

            with self.assertRaises(ValueError) as caught:
                await service.publish_identity(
                    school.id,
                    performed_by_external_id="admin:master",
                    published_by_user_id=author.id,
                    display_name="Colégio A",
                    primary_color="banana",
                )
            self.assertIn("primary_color", str(caught.exception))

            with self.assertRaises(ValueError) as caught:
                await service.publish_identity(
                    school.id,
                    performed_by_external_id="admin:master",
                    published_by_user_id=author.id,
                    display_name="Colégio A",
                    secondary_color="#GGHHII",
                )
            self.assertIn("secondary_color", str(caught.exception))

            # Nothing was written by either refusal.
            count = await session.scalar(
                select(func.count()).select_from(SchoolIdentityVersion)
            )
            self.assertEqual(count, 0)

    async def test_a_hexadecimal_colour_is_accepted(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            author = await self._author(session, school)

            version = await service.publish_identity(
                school.id,
                performed_by_external_id="admin:master",
                published_by_user_id=author.id,
                display_name="Colégio A",
                primary_color="#1A2B3C",
                secondary_color="#fff",
            )
            self.assertEqual(version.primary_color, "#1A2B3C")

    async def test_a_boolean_threshold_is_refused(self):
        """M1: bool subclasses int, so True passed the isinstance check and the
        0..1000 range, and was stored as 1. SQLite swallows it; PostgreSQL hands
        back a raw driver type error - the failure this validation exists to
        prevent."""
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            with self.assertRaises(ValueError) as caught:
                await service.configure(
                    school.id,
                    performed_by_external_id="admin:master",
                    correction_mode="AVALIATIVO",
                    validation_threshold_points=True,
                )
            self.assertIn("validation_threshold_points", str(caught.exception))
            count = await session.scalar(select(func.count()).select_from(AdminAuditLog))
            self.assertEqual(count, 0)
