import unittest

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School, SchoolIdentityVersion, SchoolSetting


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
            session.add(SchoolIdentityVersion(
                school_id=school.id, version=1, display_name="Colégio Exemplo"
            ))
            await session.flush()
            session.add(SchoolIdentityVersion(
                school_id=school.id, version=1, display_name="Colégio Exemplo 2"
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()
