import asyncio
import unittest
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AnswerKeyEntry, AnswerKeyRevision, BookletQuestion, Exam, ExamApplication, ExamBooklet, IngestionAsset, IngestionDocument, IngestionQuestion, Institution, Question, QuestionOption, QuestionVersion, SourceDocument
from agente_ia_edu.services.question_bank_importer import QuestionBankImporter


class QuestionBankImporterTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def ingestion_question(self, session, **overrides):
        document = IngestionDocument(filename="enem2020.pdf", document_type="PDF", document_hash="document-hash", storage_uri="var/inep-pilot/enem2020.pdf", file_size_bytes=1, status="processed", metadata_={"source_url": "https://download.inep.gov.br/enem.pdf", "answer_key_source_url": "https://download.inep.gov.br/enem-key.pdf", "exam_year": 2020, "exam_day": 2, "booklet": "D2_CD5", "booklet_color": "AMARELO"})
        session.add(document)
        await session.flush()
        question = IngestionQuestion(document_id=document.id, question_number=91, question_type="MULTIPLE_CHOICE", statement_text="Questao oficial", alternatives_text="A) A\nB) B\nC) C\nD) D\nE) E", correct_answer="C", position=1, page_start=2, page_end=2, status="extracted", metadata_={})
        for key, value in overrides.items():
            setattr(question, key, value)
        session.add(question)
        await session.flush()
        return document, question

    async def test_imports_valid_question_with_options_provenance_and_assets_once(self):
        async with self.factory() as session:
            document, item = await self.ingestion_question(session)
            session.add(IngestionAsset(document_id=document.id, asset_type="OTHER", storage_uri=document.storage_uri, position=0, page=2, question_id=item.id, metadata_={"visual_asset_type": "PAGE_REGION", "source_hash": document.document_hash}))
            await session.commit()
            importer = QuestionBankImporter(session)
            first = await importer.import_question(item.id)
            second = await importer.import_question(item.id)
            third = await importer.import_question(item.id)
            self.assertTrue(first.created)
            self.assertFalse(second.created)
            self.assertEqual(second.question_version_id, third.question_version_id)
            version = await session.get(QuestionVersion, first.question_version_id)
            options = list((await session.scalars(select(QuestionOption).where(QuestionOption.question_version_id == version.id))).all())
            self.assertEqual(version.version_kind, "official_original")
            self.assertEqual([option.option_key for option in sorted(options, key=lambda option: option.position)], list("ABCDE"))
            self.assertEqual([option.option_key for option in options if option.is_valid_option], ["C"])
            self.assertEqual(version.metadata_["ingestion_question_id"], str(item.id))
            self.assertEqual((await session.get(IngestionQuestion, item.id)).status, "imported")
            self.assertEqual(await session.scalar(select(func.count()).select_from(Question)), 1)
            self.assertEqual(await session.scalar(select(func.count()).select_from(Institution)), 1)
            self.assertEqual(await session.scalar(select(func.count()).select_from(Exam)), 1)
            self.assertEqual(await session.scalar(select(func.count()).select_from(ExamApplication)), 1)
            self.assertEqual(await session.scalar(select(func.count()).select_from(ExamBooklet)), 1)
            booklet_question = await session.scalar(select(BookletQuestion).where(BookletQuestion.question_version_id == version.id))
            self.assertEqual(booklet_question.position, 2)
            entry = await session.scalar(select(AnswerKeyEntry).where(AnswerKeyEntry.booklet_question_id == booklet_question.id))
            self.assertEqual(entry.official_answer_label, "C")
            self.assertEqual(entry.resolved_option_id, next(option.id for option in options if option.option_key == "C"))
            self.assertEqual(await session.scalar(select(func.count()).select_from(AnswerKeyRevision)), 1)
            self.assertEqual(await session.scalar(select(func.count()).select_from(SourceDocument)), 2)

    async def test_invalid_annulled_missing_key_or_review_required_never_creates_question(self):
        cases = [
            {"alternatives_text": None},
            {"correct_answer": None},
            {"correct_answer": "F"},
            {"metadata_": {"requires_review": True}},
            {"metadata_": {"annulled": True}},
        ]
        for overrides in cases:
            async with self.factory() as session:
                _, item = await self.ingestion_question(session, **overrides)
                await session.commit()
                result = await QuestionBankImporter(session).import_question(item.id)
                self.assertTrue(result.review_required)
                self.assertIsNone(result.question_id)
                self.assertEqual(await session.scalar(select(func.count()).select_from(Question)), 0)

    async def test_import_rolls_back_when_option_persistence_fails(self):
        async with self.factory() as session:
            _, item = await self.ingestion_question(session)
            item_id = item.id
            await session.commit()
            with self.assertRaises(RuntimeError):
                await QuestionBankImporter(session, fail_after_question=True).import_question(item_id)
            self.assertEqual(await session.scalar(select(func.count()).select_from(Question)), 0)
            restored = await session.get(IngestionQuestion, item_id)
            await session.refresh(restored)
            self.assertEqual(restored.question_version_id, None)