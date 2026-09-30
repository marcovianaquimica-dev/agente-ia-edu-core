import unittest
import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import MassCorrectionRun, School
from agente_ia_edu.services.mass_correction_driver import advance_run


class AdvanceRunTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_run(self, session, *, status):
        school = School(id=uuid.uuid4(), code="EST", name="Rede")
        session.add(school)
        await session.flush()
        run = MassCorrectionRun(
            id=uuid.uuid4(), school_id=school.id, stage="OCR",
            sequence_number=1, request_count=10, status=status,
        )
        session.add(run)
        await session.commit()
        return run

    @patch("agente_ia_edu.services.mass_correction_driver.upload_batch_file", new_callable=AsyncMock)
    @patch("agente_ia_edu.services.mass_correction_driver.create_batch", new_callable=AsyncMock)
    async def test_a_pending_run_gets_submitted(self, mock_create_batch, mock_upload):
        mock_upload.return_value = "file-in-123"
        mock_create_batch.return_value = {"id": "batch-abc", "status": "validating"}
        async with self.session_factory() as session:
            run = await self._seed_run(session, status="PENDING")
            await advance_run(session, run.id, api_key="sk-test", pending_lines=[{"custom_id": "x"}])
            await session.refresh(run)
            self.assertEqual(run.openai_batch_id, "batch-abc")
            self.assertEqual(run.status, "validating")
            self.assertEqual(run.input_file_id, "file-in-123")

    @patch("agente_ia_edu.services.mass_correction_driver.get_batch", new_callable=AsyncMock)
    async def test_an_in_progress_run_just_polls_and_updates_status(self, mock_get_batch):
        mock_get_batch.return_value = {"id": "batch-abc", "status": "in_progress"}
        async with self.session_factory() as session:
            run = await self._seed_run(session, status="validating")
            run.openai_batch_id = "batch-abc"
            await session.commit()
            await advance_run(session, run.id, api_key="sk-test")
            await session.refresh(run)
            self.assertEqual(run.status, "in_progress")
            self.assertIsNone(run.completed_at)

    @patch("agente_ia_edu.services.mass_correction_driver.get_batch", new_callable=AsyncMock)
    async def test_a_completed_run_records_the_output_file_and_completed_at(self, mock_get_batch):
        mock_get_batch.return_value = {"id": "batch-abc", "status": "completed", "output_file_id": "file-out"}
        async with self.session_factory() as session:
            run = await self._seed_run(session, status="in_progress")
            run.openai_batch_id = "batch-abc"
            await session.commit()
            await advance_run(session, run.id, api_key="sk-test")
            await session.refresh(run)
            self.assertEqual(run.status, "completed")
            self.assertEqual(run.output_file_id, "file-out")
            self.assertIsNotNone(run.completed_at)

    @patch("agente_ia_edu.services.mass_correction_driver.upload_batch_file", new_callable=AsyncMock)
    @patch("agente_ia_edu.services.mass_correction_driver.create_batch", new_callable=AsyncMock)
    async def test_resuming_a_run_that_already_has_a_batch_id_never_submits_again(
        self, mock_create_batch, mock_upload,
    ):
        """A resumibilidade do driver: um PENDING sem openai_batch_id ainda
        submete; qualquer status != PENDING (já tem batch_id) nunca chama
        create_batch de novo, mesmo se advance_run for chamado de novo depois
        de o processo cair no meio."""
        async with self.session_factory() as session:
            run = await self._seed_run(session, status="validating")
            run.openai_batch_id = "batch-ja-existe"
            await session.commit()
            with patch(
                "agente_ia_edu.services.mass_correction_driver.get_batch", new_callable=AsyncMock
            ) as mock_get_batch:
                mock_get_batch.return_value = {"id": "batch-ja-existe", "status": "validating"}
                await advance_run(session, run.id, api_key="sk-test")
            mock_create_batch.assert_not_called()
            mock_upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
