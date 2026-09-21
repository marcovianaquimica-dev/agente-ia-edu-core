"""PHASE 29 perf audit - N+1 regression test for
QuestionPublicationService.publish_run().

publish_run() iterates every APPROVED ExtractedQuestion of a run and, for
each one, used to run TWO SELECTs inside the loop:
  - a QuestionVersion.content_hash duplicate check (spec s19), and
  - a fetch of that question's ExtractedQuestionOption rows,
instead of one batched `.in_()` query for the whole run. Confirmed LIVE
against real Postgres (port 5433, temporary IngestionDocument/
QuestionExtractionRun/ExtractedQuestion rows, cleaned up afterwards): 5
approved questions -> 43 SQL statements, 50 -> 403 (real O(n) growth, ~8.6
queries/question) before the fix; 35 / 305 (~6/question) after - the
remaining per-question cost is legitimate (each APPROVED question becomes a
genuinely new Question/QuestionVersion/QuestionOption row set, published
under its own isolated SAVEPOINT per spec s19's "nothing silently skipped"
error-isolation requirement - that part is real O(n) work, not N+1).

This test pins down the SELECT-side fix specifically: the number of SELECT
statements issued must stay FLAT as the number of approved questions grows,
since only the duplicate-hash batch and the options batch (plus the fixed
run/document/approved-list lookups) should ever run, regardless of N.
"""

from __future__ import annotations

import unittest
import uuid as _uuid

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    ExtractedQuestion,
    ExtractedQuestionOption,
    IngestionDocument,
    QuestionExtractionRun,
)
from agente_ia_edu.db.models.admin import School
from agente_ia_edu.services.question_publication_service import QuestionPublicationService

SCHOOL_ID = _uuid.uuid5(_uuid.NAMESPACE_DNS, "phase29-publish-n1-school")


class SelectCounter:
    """Counts only SELECT statements executed against `engine` for the
    duration of a `with` block - same `before_cursor_execute` technique as
    tests/test_phase24_study_session_n1.py, narrowed to SELECTs since the
    INSERT/UPDATE/SAVEPOINT side of publish_run is legitimately O(n) (see
    module docstring)."""

    def __init__(self, engine):
        self.engine = engine
        self.count = 0
        self._listener = None

    def __enter__(self):
        def _count(conn, cursor, statement, parameters, context, executemany):
            if statement.lstrip().upper().startswith("SELECT"):
                self.count += 1
        self._listener = _count
        event.listen(self.engine.sync_engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine.sync_engine, "before_cursor_execute", self._listener)


class PublishRunN1Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

        async with self.factory() as session:
            session.add(School(id=SCHOOL_ID, code="P29PUBN1", name="Escola Publish N1", status="ACTIVE"))
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_run(self, tag: str, n_questions: int) -> _uuid.UUID:
        async with self.factory() as session:
            doc = IngestionDocument(
                filename=f"{tag}.pdf", document_type="PDF", document_hash=f"hash-{tag}",
                storage_uri=f"var/{tag}.pdf", file_size_bytes=1, status="processed",
                ingested_by_external_identity="tester",
            )
            session.add(doc)
            await session.flush()
            run = QuestionExtractionRun(
                ingestion_document_id=doc.id, school_id=SCHOOL_ID, engine_version="test",
                document_hash=doc.document_hash, run_status="COMPLETED",
                started_by_external_identity="tester",
            )
            session.add(run)
            await session.flush()
            for i in range(1, n_questions + 1):
                q = ExtractedQuestion(
                    run_id=run.id, question_number=i, question_type="multiple_choice",
                    raw_text=f"Questao {i}", normalized_text=f"Questao {i}",
                    fingerprint=f"{tag}-fp-{i}",
                    reviewed_text=f"Enunciado unico revisado {tag}-{i}",
                    review_status="APPROVED", extraction_confidence=0.9,
                    source_page_start=1, source_page_end=1, school_id=SCHOOL_ID,
                    reviewed_by_external_identity="tester",
                )
                session.add(q)
                await session.flush()
                for pos, label in enumerate(["A", "B", "C", "D"], start=1):
                    session.add(ExtractedQuestionOption(
                        question_id=q.id, label=label, text=f"Alternativa {label} q{i}", position=pos))
            await session.commit()
            return run.id

    async def _publish_and_count_selects(self, run_id: _uuid.UUID, expected_published: int) -> int:
        async with self.factory() as session:
            pub = QuestionPublicationService(session)
            with SelectCounter(self.engine) as counter:
                result = await pub.publish_run(run_id, published_by="tester", school_id=SCHOOL_ID)
            self.assertEqual(result["published_count"], expected_published, result)
            return counter.count

    async def test_publish_run_select_count_does_not_scale_with_approved_question_count(self):
        run_small = await self._seed_run("small", 3)
        selects_small = await self._publish_and_count_selects(run_small, 3)

        run_big = await self._seed_run("big", 20)
        selects_big = await self._publish_and_count_selects(run_big, 20)

        # Before the fix this grew by 2 SELECTs per extra approved question
        # (17 extra questions -> +34 SELECTs). After the fix, the SELECT
        # count is identical regardless of how many questions are approved:
        # get(run), get(document), the approved-question list, one batched
        # content_hash .in_() query, one batched options .in_() query.
        self.assertEqual(
            selects_small, selects_big,
            f"SELECT count grew with N ({selects_small} -> {selects_big}) - "
            "publish_run's duplicate-hash/options lookups regressed to one query per question",
        )
        self.assertEqual(selects_small, 5)

    async def test_publish_run_still_detects_duplicate_within_same_batch(self):
        """Regression guard for the batched rewrite: two APPROVED questions
        in the SAME run with identical canonical_text must still collide -
        the original per-iteration query saw the first one's uncommitted
        flush; the batched version must replicate that via its in-memory
        existing_version_by_hash dict (see publish_run's comment)."""
        async with self.factory() as session:
            doc = IngestionDocument(
                filename="dup.pdf", document_type="PDF", document_hash="hash-dup",
                storage_uri="var/dup.pdf", file_size_bytes=1, status="processed",
                ingested_by_external_identity="tester",
            )
            session.add(doc)
            await session.flush()
            run = QuestionExtractionRun(
                ingestion_document_id=doc.id, school_id=SCHOOL_ID, engine_version="test",
                document_hash=doc.document_hash, run_status="COMPLETED",
                started_by_external_identity="tester",
            )
            session.add(run)
            await session.flush()
            for i in (1, 2):
                q = ExtractedQuestion(
                    run_id=run.id, question_number=i, question_type="multiple_choice",
                    raw_text=f"Questao {i}", normalized_text=f"Questao {i}",
                    fingerprint=f"dup-fp-{i}",
                    reviewed_text="Texto identico para as duas questoes",
                    review_status="APPROVED", extraction_confidence=0.9,
                    source_page_start=1, source_page_end=1, school_id=SCHOOL_ID,
                    reviewed_by_external_identity="tester",
                )
                session.add(q)
            await session.commit()
            run_id = run.id

        async with self.factory() as session:
            pub = QuestionPublicationService(session)
            result = await pub.publish_run(run_id, published_by="tester", school_id=SCHOOL_ID)
        self.assertEqual(result["published_count"], 1, result)
        self.assertEqual(result["duplicate_count"], 1, result)


if __name__ == "__main__":
    unittest.main()
