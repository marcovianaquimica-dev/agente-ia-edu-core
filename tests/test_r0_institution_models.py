import unittest

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    Person,
    School,
    SchoolIdentityVersion,
    SchoolSetting,
    User,
)


class TestInstitutionModels(unittest.IsolatedAsyncioTestCase):
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

    async def _school(self, session, code="ESCOLA_R0") -> School:
        school = School(code=code, name="Escola R0")
        session.add(school)
        await session.flush()
        return school

    async def _author(self, session, school) -> User:
        """A user of this school, to sign a published identity.

        ``published_by_user_id`` is NOT NULL: an identity nobody published is
        not a thing this table can record any more (spec §5.2, §5.3)."""
        person = Person(school_id=school.id, full_name="Diretora Marta")
        session.add(person)
        await session.flush()
        user = User(
            school_id=school.id,
            person_id=person.id,
            external_identity_provider="host",
            external_user_id=f"host:{school.code}:marta",
        )
        session.add(user)
        await session.flush()
        return user

    async def test_formative_mode_accepts_no_validation_policy(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            session.add(SchoolSetting(school_id=school.id, correction_mode="FORMATIVO"))
            await session.flush()

    async def test_formative_mode_rejects_a_validation_policy(self):
        """Spec §5.1: the validation policy only means something when scores
        exist. A formative school with a validation default is a configuration
        nobody can act on."""
        async with self.session_factory() as session:
            school = await self._school(session)
            session.add(SchoolSetting(
                school_id=school.id,
                correction_mode="FORMATIVO",
                validation_default="EM_LOTE",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_formative_mode_rejects_a_threshold(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            session.add(SchoolSetting(
                school_id=school.id,
                correction_mode="FORMATIVO",
                validation_threshold_points=500,
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_evaluative_mode_accepts_the_full_policy(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            setting = SchoolSetting(
                school_id=school.id,
                correction_mode="AVALIATIVO",
                validation_default="UMA_A_UMA",
                validation_teacher_can_disable=True,
                validation_threshold_points=500,
            )
            session.add(setting)
            await session.flush()
            self.assertTrue(setting.validation_teacher_can_disable)

    async def test_correction_mode_is_constrained(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            session.add(SchoolSetting(school_id=school.id, correction_mode="NAO_EXISTE"))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_validation_default_is_constrained(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            session.add(SchoolSetting(
                school_id=school.id,
                correction_mode="AVALIATIVO",
                validation_default="NAO_EXISTE",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_a_school_has_at_most_one_settings_row(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            session.add(SchoolSetting(school_id=school.id, correction_mode="FORMATIVO"))
            await session.flush()
            session.add(SchoolSetting(school_id=school.id, correction_mode="AVALIATIVO"))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_identity_version_is_unique_per_school(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            author = await self._author(session, school)
            session.add(SchoolIdentityVersion(
                school_id=school.id,
                version=1,
                display_name="Colégio Exemplo",
                published_by_user_id=author.id,
            ))
            await session.flush()
            session.add(SchoolIdentityVersion(
                school_id=school.id,
                version=1,
                display_name="Colégio Exemplo 2",
                published_by_user_id=author.id,
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_an_identity_version_must_name_who_published_it(self):
        """I3: the column was declared and never written, so it was always NULL
        - an audit column that cannot be populated is worse than none."""
        async with self.session_factory() as session:
            school = await self._school(session)
            session.add(SchoolIdentityVersion(
                school_id=school.id, version=1, display_name="Colégio Exemplo"
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_a_colour_that_is_not_hexadecimal_is_refused(self):
        """Spec §5.2 requires a hexadecimal format CHECK. It was missing from
        the plan, the model, the migration and the service at once, so
        primary_color='banana' was accepted and persisted - in the module whose
        first paragraph says what has a rule, the database refuses (§3.5)."""
        for colour in ("banana", "#12345", "1A2B3C", "#GGHHII", "#1a2b3c4d5e"):
            with self.subTest(colour=colour):
                async with self.session_factory() as session:
                    school = await self._school(session, code=f"ESCOLA_{abs(hash(colour))}")
                    author = await self._author(session, school)
                    session.add(SchoolIdentityVersion(
                        school_id=school.id,
                        version=1,
                        display_name="Colégio Exemplo",
                        primary_color=colour,
                        published_by_user_id=author.id,
                    ))
                    with self.assertRaises(IntegrityError):
                        await session.flush()

    async def test_the_hexadecimal_forms_are_accepted(self):
        for colour in ("#fff", "#1A2B3C", "#1a2b3c", "#1A2B3C4D", None):
            with self.subTest(colour=colour):
                async with self.session_factory() as session:
                    school = await self._school(session, code=f"ESCOLA_OK_{colour}")
                    author = await self._author(session, school)
                    session.add(SchoolIdentityVersion(
                        school_id=school.id,
                        version=1,
                        display_name="Colégio Exemplo",
                        primary_color=colour,
                        secondary_color=colour,
                        published_by_user_id=author.id,
                    ))
                    await session.flush()
