"""Ingestion classification perf audit - N+1 regression test for
IngestionClassificationService.classify_document_questions().

Two per-question SELECTs used to run INSIDE the question loop, both
independent of the loop variable's content:
  - `_ensure_question_version`'s `session.get(QuestionVersion, ...)`, once
    PER already-versioned question (a reprocess/re-classify pass);
  - the "already classified?" duplicate check, run for EVERY question when
    reprocess=False (the default) - even for a version this very call was
    about to create for the first time, which can never have a prior
    classification.
And after the loop, `session.refresh(classification)` ran once PER
classification (needed because `expire_on_commit=True` in production - see
db/session.py - expires every attribute on commit).

Confirmed LIVE against real Postgres (port 5433, a fresh IngestionDocument
with N IngestionQuestion rows, no question_version_id set yet - the common
first-classification-pass shape, cleaned up afterwards): 5 questions -> 43
SQL statements, 50 -> 403 (real O(n) growth, ~8/question) before the fix; 34
/ 304 (~6/question) after - the residual O(n) is legitimate (creating a new
Question + QuestionVersion + PedagogicalClassification row set per question
is real, necessary work). A SECOND call over the SAME already-classified
document (every question hits the "already versioned" + "already classified"
paths) went from growing ~1 query/question (10 -> 55) to a FLAT 6 regardless
of N.

This test pins both down with a fast, in-process, disposable sqlite counter.
"""

from __future__ import annotations

import unittest
import uuid as _uuid

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import IngestionDocument, IngestionQuestion
from agente_ia_edu.services.ingestion_classifier import IngestionClassificationService
from agente_ia_edu.services.pedagogical_classifier import MockPedagogicalClassifierProvider


class QueryCounter:
    """Same `before_cursor_execute` technique as the other N+1 regression
    tests in this suite (test_phase24_study_session_n1.py,
    test_phase29_publish_run_n1.py, test_ingestion_n1.py)."""

    def __init__(self, engine):
        self.engine = engine
        self.count = 0
        self._listener = None

    def __enter__(self):
        def _count(conn, cursor, statement, parameters, context, executemany):
            self.count += 1
        self._listener = _count
        event.listen(self.engine.sync_engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine.sync_engine, "before_cursor_execute", self._listener)


class ClassifyDocumentQuestionsN1Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_document(self, tag: str, n_questions: int) -> _uuid.UUID:
        async with self.factory() as session:
            doc = IngestionDocument(
                filename=f"{tag}.pdf", document_type="PDF", document_hash=f"hash-{tag}",
                storage_uri=f"var/{tag}.pdf", file_size_bytes=1, status="processed",
                ingested_by_external_identity="tester",
            )
            session.add(doc)
            await session.flush()
            for i in range(1, n_questions + 1):
                session.add(IngestionQuestion(
                    document_id=doc.id, question_number=i, question_type="MULTIPLE_CHOICE",
                    statement_text=f"Enunciado {i} {tag} {_uuid.uuid4().hex}",
                    alternatives_text="a) x\nb) y\nc) z\nd) w",
                    position=i, page_start=1, page_end=1, status="extracted", metadata_={},
                ))
            await session.commit()
            return doc.id

    async def test_first_pass_query_count_does_not_scale_with_question_count(self):
        doc_small = await self._seed_document("small", 3)
        doc_big = await self._seed_document("big", 20)

        async with self.factory() as session:
            svc = IngestionClassificationService(session)
            with QueryCounter(self.engine) as counter:
                result = await svc.classify_document_questions(
                    doc_small, MockPedagogicalClassifierProvider(), model_name="n1-test")
            self.assertEqual(len(result), 3)
            count_small = counter.count

        async with self.factory() as session:
            svc = IngestionClassificationService(session)
            with QueryCounter(self.engine) as counter:
                result = await svc.classify_document_questions(
                    doc_big, MockPedagogicalClassifierProvider(), model_name="n1-test")
            self.assertEqual(len(result), 20)
            count_big = counter.count

        # Marginal cost per extra question - not the raw per-question average
        # (which drops with N either way, fix or no fix, as fixed overhead
        # dilutes): before the fix this measured 8.0 extra SQL statements per
        # extra question (27 -> 163 for 3 -> 20 questions, sqlite); after the
        # fix, 6.0 (22 -> 124) - the 2/question difference is exactly the two
        # per-question SELECTs this fix removes (duplicate-check + the
        # get()-per-already-versioned-question, see module docstring).
        marginal_cost = (count_big - count_small) / (20 - 3)
        self.assertLessEqual(
            marginal_cost, 6.5,
            f"marginal query cost per extra question is {marginal_cost:.2f} "
            f"({count_small} -> {count_big} for 3 -> 20 questions) - back to "
            "one SELECT per question instead of a batched .in_() query",
        )

    async def test_already_classified_pass_query_count_is_flat(self):
        """A SECOND classify_document_questions() call over an
        already-fully-classified document must hit ONLY the batched
        pre-fetch queries (versions + existing classifications), never one
        query per question - regardless of how many questions there are."""
        doc_small = await self._seed_document("reproc-small", 3)
        doc_big = await self._seed_document("reproc-big", 20)

        async def classify_twice(doc_id, n) -> int:
            async with self.factory() as session:
                svc = IngestionClassificationService(session)
                await svc.classify_document_questions(
                    doc_id, MockPedagogicalClassifierProvider(), model_name="n1-reproc")
            async with self.factory() as session:
                svc = IngestionClassificationService(session)
                with QueryCounter(self.engine) as counter:
                    result = await svc.classify_document_questions(
                        doc_id, MockPedagogicalClassifierProvider(), model_name="n1-reproc")
                self.assertEqual(len(result), n)
                return counter.count

        count_small = await classify_twice(doc_small, 3)
        count_big = await classify_twice(doc_big, 20)

        self.assertEqual(
            count_small, count_big,
            f"already-classified pass query count grew with N ({count_small} -> {count_big}) - "
            "the batched version/classification pre-fetch regressed to one query per question",
        )


if __name__ == "__main__":
    unittest.main()
