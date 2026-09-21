import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.services.institution_settings import InstitutionSettingsService


class TranscriptionEnabledSettingTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_defaults_to_disabled(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="TR1", name="school-tr1")
            session.add(school)
            await session.commit()

            settings = await InstitutionSettingsService(session).get_settings(school.id)
            self.assertFalse(settings.transcription_enabled)

    async def test_configure_can_enable_it(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="TR2", name="school-tr2")
            session.add(school)
            await session.commit()

            svc = InstitutionSettingsService(session)
            updated = await svc.configure(
                school.id, performed_by_external_id="admin:x", transcription_enabled=True
            )
            self.assertTrue(updated.transcription_enabled)

            fetched = await svc.get_settings(school.id)
            self.assertTrue(fetched.transcription_enabled)


if __name__ == "__main__":
    unittest.main()
