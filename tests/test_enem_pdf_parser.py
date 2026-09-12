import unittest
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import IngestionAsset, IngestionDocument, IngestionQuestion
from agente_ia_edu.services.ingestion import IngestionService
from agente_ia_edu.services.ingestion_parser import PdfParser


PILOT_DIR = Path("var/inep-pilot")
PILOT_PDF = PILOT_DIR / "2020_PV_impresso_D2_CD5.pdf"
PILOT_KEY = PILOT_DIR / "2020_GB_impresso_D2_CD5.pdf"


@unittest.skipUnless(PILOT_PDF.exists() and PILOT_KEY.exists(), "Official ENEM pilot PDFs are local-only")
class Enem2020PdfParserTests(unittest.TestCase):
    def test_extracts_nature_booklet_questions_in_source_order(self):
        parsed = PdfParser.parse_file(PILOT_PDF)
        self.assertEqual(parsed.page_count, 32)
        self.assertEqual([question.question_number for question in parsed.questions], list(range(91, 181)))
        self.assertTrue(all(question.page_start for question in parsed.questions))
        self.assertEqual([question.position for question in parsed.questions], list(range(90)))

    def test_preserves_objective_options_and_scientific_text(self):
        parsed = PdfParser.parse_file(PILOT_PDF)
        complete = [question for question in parsed.questions if question.alternatives_text and len(question.alternatives_text.split("\n")) == 5]
        self.assertGreaterEqual(len(complete), 60)
        self.assertTrue(all(len(question.alternatives_text.split("\n")) == 5 for question in complete))
        self.assertTrue(any(question.requires_review for question in parsed.questions if question not in complete))
        text = " ".join(question.statement_text for question in parsed.questions)
        self.assertIn("soluções", text.lower())

    def test_detects_page_assets_without_claiming_visual_completeness(self):
        parsed = PdfParser.parse_file(PILOT_PDF)
        self.assertGreater(parsed.total_images, 0)
        self.assertTrue(all(asset.page >= 1 for asset in parsed.assets))
        self.assertTrue(all(asset.source_hash == parsed.document_hash for asset in parsed.assets if asset.asset_type == "PAGE_REGION"))
        self.assertTrue(all(not asset.association_confident for asset in parsed.assets if asset.asset_type == "PAGE_REGION"))
        self.assertTrue(any(question.requires_review for question in parsed.questions))

    def test_extracts_matching_official_answer_key_and_annulments(self):
        answer_key = PdfParser.parse_answer_key(PILOT_KEY)
        self.assertEqual(answer_key[91], "C")
        self.assertEqual(answer_key[135], "C")
        self.assertNotIn(114, answer_key)
        self.assertEqual(len(answer_key), 88)
        parsed = PdfParser.apply_answer_key(PdfParser.parse_file(PILOT_PDF), answer_key)
        self.assertEqual(sum(question.correct_answer is not None for question in parsed.questions), 88)
        self.assertTrue(next(question for question in parsed.questions if question.question_number == 114).requires_review)

    def test_hash_is_deterministic_and_invalid_pdf_is_rejected(self):
        self.assertEqual(PdfParser.file_hash(PILOT_PDF), PdfParser.file_hash(PILOT_PDF))
        with self.assertRaises(ValueError):
            PdfParser.parse_file(Path("tests/fixtures/ingestion_materials/create_pilot.py"))

    def test_persists_only_intermediate_records_idempotently(self):
        async def run():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                service = IngestionService()
                answer_key = PdfParser.parse_answer_key(PILOT_KEY)
                first, _ = await service.ingest_document(
                    session, PILOT_PDF, answer_key=answer_key,
                    source_metadata={"source_url": "https://download.inep.gov.br/enem/provas_e_gabaritos/2020_PV_impresso_D2_CD5.pdf", "exam_year": 2020, "booklet": "D2_CD5"},
                )
                second, _ = await service.ingest_document(session, PILOT_PDF)
                documents = list((await session.scalars(select(IngestionDocument))).all())
                questions = list((await session.scalars(select(IngestionQuestion))).all())
                assets = list((await session.scalars(select(IngestionAsset))).all())
            await engine.dispose()
            return first, second, documents, questions, assets
        first, second, documents, questions, assets = __import__("asyncio").run(run())
        self.assertEqual(first.id, second.id)
        self.assertEqual(len(documents), 1)
        self.assertEqual(len(questions), 90)
        self.assertGreater(len(assets), 0)
        self.assertEqual(sum(question.correct_answer is not None for question in questions), 88)
        self.assertTrue(any(question.metadata_["requires_review"] for question in questions))