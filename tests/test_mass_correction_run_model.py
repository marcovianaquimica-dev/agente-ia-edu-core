import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import MassCorrectionRun, School


class MassCorrectionRunModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_creates_a_pending_run_with_defaults(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="EST-1", name="Rede Estadual")
            session.add(school)
            await session.flush()
            run = MassCorrectionRun(
                id=uuid.uuid4(), school_id=school.id, stage="OCR",
                sequence_number=1, request_count=40000, status="PENDING",
            )
            session.add(run)
            await session.commit()

            fetched = (await session.execute(
                select(MassCorrectionRun).where(MassCorrectionRun.id == run.id)
            )).scalar_one()
            self.assertEqual(fetched.stage, "OCR")
            self.assertEqual(fetched.status, "PENDING")
            self.assertIsNone(fetched.openai_batch_id)
            self.assertIsNone(fetched.completed_at)
            self.assertIsInstance(fetched.created_at, datetime)

    async def test_rejects_an_unknown_stage(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="EST-2", name="Rede Estadual")
            session.add(school)
            await session.flush()
            session.add(MassCorrectionRun(
                id=uuid.uuid4(), school_id=school.id, stage="TRANSCODE",
                sequence_number=1, request_count=1, status="PENDING",
            ))
            with self.assertRaises(Exception):
                await session.commit()

    async def test_rejects_an_unknown_status(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="EST-3", name="Rede Estadual")
            session.add(school)
            await session.flush()
            session.add(MassCorrectionRun(
                id=uuid.uuid4(), school_id=school.id, stage="OCR",
                sequence_number=1, request_count=1, status="RUNNING",
            ))
            with self.assertRaises(Exception):
                await session.commit()

    async def test_accepts_cancelling_status(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="EST-4", name="Rede Estadual")
            session.add(school)
            await session.flush()
            run = MassCorrectionRun(
                id=uuid.uuid4(), school_id=school.id, stage="OCR",
                sequence_number=1, request_count=1, status="cancelling",
            )
            session.add(run)
            await session.commit()

            fetched = (await session.execute(
                select(MassCorrectionRun).where(MassCorrectionRun.id == run.id)
            )).scalar_one()
            self.assertEqual(fetched.status, "cancelling")


if __name__ == "__main__":
    unittest.main()
