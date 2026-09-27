"""Tests for scripts/ocr_pilot_usberco_cap10.py.

The Anthropic provider is ALWAYS a stub here - no network/API key is used.
The DB-touching test uses an in-memory sqlite engine (same pattern already
used elsewhere in this codebase, e.g. tests/manual/phase26_authorial_material
_ingestion_report.py's `_perf_probe`), never the shared disposable Postgres
on :5433 - so this file is safe to run alongside any other concurrent test
session.

WHY an integration test at all, given the task said it's optional: it is the
only way to actually prove two things that a pure mock cannot: (1) the
markdown this script assembles is real input `parse_authorial_text()`
accepts and structures as expected (chapter/subsection headings recognized,
nothing dropped), and (2) `ingest_file()` - completely unmodified - really
does accept it end to end and lands at a PENDING_REVIEW/NEEDS_REVIEW state
without raising. A mocked `ingest_file()` would only prove "the script calls
a function with these arguments", which is a much weaker claim.
"""

import importlib
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.providers.models import DocumentPageTranscriptionResult
from agente_ia_edu.services.authorial_material_parser import parse_authorial_text

pilot = importlib.import_module("scripts.ocr_pilot_usberco_cap10")


class BuildPagePromptTests(unittest.TestCase):
    def test_opening_page_instructs_the_model_to_emit_the_chapter_h1(self):
        prompt = pilot.build_page_prompt(position=1, total_pages=8, is_opening_page=True)
        self.assertIn("ABERTURA do capítulo", prompt)
        self.assertIn("# Capítulo 10", prompt)

    def test_non_opening_page_instructs_the_model_not_to_repeat_the_title(self):
        prompt = pilot.build_page_prompt(position=2, total_pages=8, is_opening_page=False)
        self.assertIn("não repita o título do capítulo", prompt)
        self.assertNotIn("ABERTURA do capítulo", prompt)

    def test_prompt_documents_the_never_fabricate_rule(self):
        prompt = pilot.build_page_prompt(position=3, total_pages=8, is_opening_page=False)
        self.assertIn("nunca invente", prompt)
        self.assertIn("[ilegível]", prompt)
        self.assertIn("diagrama", prompt)

    def test_prompt_documents_the_isotope_and_ion_notation_convention(self):
        prompt = pilot.build_page_prompt(position=1, total_pages=8, is_opening_page=True)
        self.assertIn("35/17 Cl", prompt)
        self.assertIn("Fe2+", prompt)


class AssembleMarkdownStructureTests(unittest.TestCase):
    """Feeds a realistic set of page transcriptions (following the exact
    convention build_page_prompt() asks for) through the REAL, unmodified
    parse_authorial_text() and checks the resulting structure - not a mock."""

    def test_assembled_markdown_is_structured_as_expected_by_the_real_parser(self):
        page1 = (
            "# Capítulo 10 Caracterização dos elementos químicos\n\n"
            "Neste capítulo estudaremos como caracterizar um elemento químico "
            "a partir de seu número atômico e número de massa."
        )
        page2 = (
            "## Número atômico\n\n"
            "O número atômico (Z) representa a quantidade de prótons no "
            "núcleo de um átomo. Exemplo de notação isotópica: 35/17 Cl.\n\n"
            "8. Uma espécie X é formada por 20 prótons, 20 elétrons e 22 "
            "nêutrons. Determine Z e A dessa espécie.\n"
            "a) Z=18, A=38\n"
            "b) Z=20, A=42\n"
            "c) Z=20, A=40\n"
            "d) Z=22, A=42\n"
            "e) Z=18, A=40"
        )
        page3 = (
            "## Isótopos, isóbaros e isótonos\n\n"
            "Isótopos são átomos de um mesmo elemento com números de massa "
            "diferentes.\n\n"
            "[diagrama: configuração eletrônica do átomo de sódio - não "
            "transcrito com confiança]"
        )

        with TemporaryDirectory() as tmp:
            out_path = Path(tmp) / "capitulo_10.md"
            written = pilot.assemble_markdown([page1, page2, page3], output_path=out_path)
            self.assertEqual(written, out_path)

            parsed = parse_authorial_text(written)

        # 3 headings ("#"/"##" - parse_authorial_text has no concept of
        # heading LEVEL, every "#"-prefixed line starts a new flat section)
        self.assertEqual(len(parsed.sections), 3)
        self.assertEqual(
            parsed.sections[0].title, "Capítulo 10 Caracterização dos elementos químicos"
        )
        self.assertEqual(parsed.sections[1].title, "Número atômico")
        self.assertEqual(parsed.sections[2].title, "Isótopos, isóbaros e isótonos")

        # nothing was dropped: the exercise text and the honest diagram note
        # both survive as ordinary paragraph content.
        section2_text = "\n".join(parsed.sections[1].content_lines)
        self.assertIn("8. Uma espécie X", section2_text)
        self.assertIn("a) Z=18, A=38", section2_text)
        section3_text = "\n".join(parsed.sections[2].content_lines)
        self.assertIn("[diagrama: configuração eletrônica do átomo de sódio", section3_text)

        # PHASE 30 fix (2026-09-27): parse_authorial_text() now applies the
        # SAME _flush_exercises() pass parse_authorial_pdf() already used, so
        # the "8. Uma espécie X ..." item with its 5 inline a)-e) options is
        # now detected as a ParsedQuestion too - WITHOUT removing it from
        # section2_text above (same dualism as the PDF path, asserted there).
        self.assertEqual(len(parsed.questions), 1)
        question = parsed.questions[0]
        self.assertEqual(question.question_number, 8)
        self.assertIn("Uma espécie X é formada por 20 prótons", question.statement_text)
        self.assertEqual(
            question.alternatives_text,
            "a) Z=18, A=38\nb) Z=20, A=42\nc) Z=20, A=40\nd) Z=22, A=42\ne) Z=18, A=40",
        )
        self.assertEqual(question.section_index, parsed.sections[1].position)


class TranscribeChapterTests(unittest.TestCase):
    """The Anthropic client is never touched here - build_document_page_
    transcriber() itself is replaced by a stub provider."""

    def test_calls_the_provider_once_per_image_in_order_with_the_right_prompts(self):
        recorded_requests = []

        class StubProvider:
            async def transcribe_document_page(self, request):
                recorded_requests.append(request)
                return DocumentPageTranscriptionResult(
                    text=f"conteúdo da página {len(recorded_requests)}",
                    provider="stub", model="stub-model",
                )

        image_paths = [Path(f"/tmp/does_not_need_to_exist_{i}.png") for i in range(3)]

        with patch.object(pilot, "build_document_page_transcriber", return_value=StubProvider()):
            import asyncio
            transcriptions = asyncio.run(pilot.transcribe_chapter(image_paths))

        self.assertEqual(len(recorded_requests), 3)
        self.assertEqual([r.image_path for r in recorded_requests], image_paths)
        self.assertTrue(all(r.mime_type == "image/png" for r in recorded_requests))
        # page order preserved in the returned transcriptions
        self.assertEqual(
            transcriptions,
            ["conteúdo da página 1", "conteúdo da página 2", "conteúdo da página 3"],
        )
        # page 1's prompt is the opening-page variant, others are not
        self.assertIn("ABERTURA do capítulo", recorded_requests[0].prompt)
        self.assertNotIn("ABERTURA do capítulo", recorded_requests[1].prompt)
        self.assertNotIn("ABERTURA do capítulo", recorded_requests[2].prompt)


class IngestCallsTheRealUnmodifiedPipelineTests(unittest.TestCase):
    """In-memory sqlite - proves ingest_file() (untouched) really accepts
    the assembled markdown end to end, without needing the shared Postgres
    on :5433 or any network access."""

    def test_ingest_lands_at_a_review_status_without_raising(self):
        import asyncio

        async def _run():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

            with TemporaryDirectory() as tmp:
                md_path = Path(tmp) / "capitulo_10.md"
                md_path.write_text(
                    "# Capítulo 10 Caracterização dos elementos químicos\n\n"
                    "Texto de teoria real o suficiente para formar uma seção "
                    "com conteúdo não vazio.\n\n"
                    "## Número atômico\n\n"
                    "Mais um parágrafo de conteúdo para esta subseção.\n\n"
                    "8. Uma espécie X é formada por 20 prótons, 20 elétrons e 22 "
                    "nêutrons. Determine Z e A dessa espécie.\n"
                    "a) Z=18, A=38\n"
                    "b) Z=20, A=42\n"
                    "c) Z=20, A=40\n"
                    "d) Z=22, A=42\n"
                    "e) Z=18, A=40",
                    encoding="utf-8",
                )
                await pilot.ingest(md_path, session_factory=factory)

            async with factory() as session:
                from sqlalchemy import select

                from agente_ia_edu.db.models import IngestionMaterialReview

                reviews = (
                    await session.execute(select(IngestionMaterialReview))
                ).scalars().all()
            await engine.dispose()
            return reviews

        reviews = asyncio.run(_run())

        self.assertEqual(len(reviews), 1)
        review = reviews[0]
        self.assertIn(review.review_status, ("PENDING_REVIEW", "NEEDS_REVIEW"))
        self.assertEqual(review.school_id, None)
        self.assertEqual(review.origin_type, "AUTHORIAL")
        # PHASE 30 fix, proven end to end this time: the numbered exercise
        # embedded in "## Número atômico" is now detected and counted.
        self.assertEqual(review.exercises_detected, 1)


if __name__ == "__main__":
    unittest.main()
