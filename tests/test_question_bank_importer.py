import asyncio
import unittest
from pathlib import Path

from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AnswerKeyEntry, AnswerKeyRevision, BookletQuestion, Exam, ExamApplication, ExamBooklet, IngestionAsset, IngestionDocument, IngestionQuestion, Institution, Question, QuestionOption, QuestionVersion, SourceDocument
from agente_ia_edu.services.question_bank_importer import QuestionBankImporter


class QueryCounter:
    """Counts SQL statements executed against `engine` for the duration of a
    `with` block, via SQLAlchemy's `before_cursor_execute` event - same
    technique used in test_portal_n1_queries.py / test_phase20_manager_view_n1.py
    and, live, against the real dev Postgres (see this phase's N+1 audit)."""

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

    async def ingestion_questions_for_one_booklet(self, session, count: int):
        """N distinct IngestionQuestion rows staged under the SAME
        IngestionDocument/booklet - the real shape of a batch ingestion run
        (see tests/manual/phase10_ingest_batch.py, which reuses ONE
        QuestionBankImporter across every staged question of a booklet)."""
        document = IngestionDocument(
            filename="enem2020.pdf", document_type="PDF",
            document_hash=f"document-hash-batch-{count}", storage_uri="var/inep-pilot/enem2020.pdf",
            file_size_bytes=1, status="processed",
            metadata_={
                "source_url": "https://download.inep.gov.br/enem.pdf",
                "answer_key_source_url": "https://download.inep.gov.br/enem-key.pdf",
                "exam_year": 2020, "exam_day": 2, "booklet": f"D2_CD5_BATCH{count}",
                "booklet_color": "AMARELO",
            },
        )
        session.add(document)
        await session.flush()
        items = []
        for i in range(count):
            question = IngestionQuestion(
                document_id=document.id, question_number=91 + i, question_type="MULTIPLE_CHOICE",
                statement_text=f"Questao oficial numero {i} do lote {count}",
                alternatives_text="A) A\nB) B\nC) C\nD) D\nE) E", correct_answer="C",
                position=i, page_start=2, page_end=2, status="extracted", metadata_={},
            )
            session.add(question)
            items.append(question)
        await session.flush()
        return document, items

    # SELECTs against these tables are exactly what `_official_context`
    # resolves: institution/exam/application/booklet/the two source
    # documents/answer-key-revision. Every one of them is identical across
    # every question of the SAME booklet - a real N+1 re-issues all of them
    # on EVERY `import_question` call instead of resolving once.
    _CONTEXT_TABLES = (
        "institutions", "exams", "exam_applications", "exam_booklets",
        "source_documents", "answer_key_revisions",
    )

    def _count_context_selects(self, statements: list[str]) -> int:
        return sum(
            1 for s in statements
            if s.strip().upper().startswith("SELECT")
            and any(table in s for table in self._CONTEXT_TABLES)
        )

    async def _context_selects_for_batch(self, count: int) -> int:
        async with self.factory() as session:
            _, items = await self.ingestion_questions_for_one_booklet(session, count)
            ids = [item.id for item in items]
            await session.commit()
            importer = QuestionBankImporter(session)
            statements: list[str] = []

            def _capture(conn, cursor, statement, parameters, context, executemany):
                statements.append(statement)

            event.listen(self.engine.sync_engine, "before_cursor_execute", _capture)
            try:
                for qid in ids:
                    result = await importer.import_question(qid)
                    self.assertTrue(result.created)
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", _capture)
            return self._count_context_selects(statements)

    async def test_batch_import_same_booklet_does_not_reresolve_official_context_per_question(self):
        """N+1 regression (this phase's audit): QuestionBankImporter.import_question
        used to re-run 7 SELECTs (institution, exam, application, booklet, the
        two source_documents, the answer_key_revision) on EVERY question of a
        batch import, even though a batch always shares the SAME document/
        booklet - confirmed live against real Postgres (N=5 -> 90 queries total
        / 18 per question, N=45 -> 769 total / 17.1 per question: the 7
        redundant context SELECTs alone accounted for 7*(N-1) of the total,
        growing linearly with the batch). The fix memoizes the resolved
        (booklet_id, answer_key_revision_id) on the importer instance after
        each question's commit (see `_context_cache` in
        `QuestionBankImporter.__init__`), so `_official_context` is queried
        only once per booklet no matter how many questions follow.

        This test pins that shape directly: the number of SELECTs touching
        the official-context tables must NOT grow with the batch size (RED
        without the fix: grows to roughly 6-7x between N=3 and N=12; GREEN
        with the fix: identical, small, constant count for both)."""
        small = await self._context_selects_for_batch(3)
        large = await self._context_selects_for_batch(12)
        self.assertGreater(small, 0, "expected the first question to resolve the context at least once")
        self.assertEqual(
            small, large,
            f"official-context SELECTs grew with batch size ({small} for N=3 vs "
            f"{large} for N=12) - the shared institution/exam/application/booklet/"
            "source-document/answer-key-revision context is being re-resolved per "
            "question instead of memoized (N+1 regression).",
        )
        # and it should be a small, fixed number (one resolution's worth),
        # not proportional to either batch - not just "equal to each other".
        self.assertLessEqual(small, 7, f"expected roughly one resolution's worth of SELECTs, got {small}")


class QuestionBankImporterProductionSessionTests(unittest.IsolatedAsyncioTestCase):
    """Same behaviour as `QuestionBankImporterTests`, but against a session
    factory that leaves `expire_on_commit` at SQLAlchemy's default (True) -
    the same default production uses (see `db/session.py`, which passes no
    override). `import_question` commits mid-method and then used to read
    attributes (`question.id`/`version.id`/`document.id`/`existing.id`/
    `existing.question_id`) straight off the just-committed ORM objects for
    its return value and cache key - under `expire_on_commit=True` those
    attributes are expired by the commit, and a bare synchronous attribute
    access to trigger their reload raises MissingGreenlet against a real
    async driver (invisible under `expire_on_commit=False`, which is why the
    fixture above never caught it). Fixed by capturing every value needed
    after the commit into local variables BEFORE the commit."""

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession)  # expire_on_commit defaults to True

    async def asyncTearDown(self):
        await self.engine.dispose()

    @staticmethod
    def _doc_kwargs(booklet: str, *, document_hash: str) -> dict:
        return dict(
            filename="enem2020.pdf", document_type="PDF", document_hash=document_hash,
            storage_uri="var/inep-pilot/enem2020.pdf", file_size_bytes=1, status="processed",
            metadata_={
                "source_url": "https://download.inep.gov.br/enem.pdf",
                "answer_key_source_url": "https://download.inep.gov.br/enem-key.pdf",
                "exam_year": 2020, "exam_day": 2, "booklet": booklet, "booklet_color": "AMARELO",
            },
        )

    async def test_newly_created_import_survives_expire_on_commit(self):
        """RED before the fix: MissingGreenlet reading `question.id`/
        `version.id`/`document.id` right after `import_question`'s own
        internal commit, under the same expire_on_commit default production
        uses."""
        async with self.factory() as session:
            document = IngestionDocument(**self._doc_kwargs("D2_CD5", document_hash="doc-hash-new"))
            session.add(document)
            await session.flush()
            item = IngestionQuestion(
                document_id=document.id, question_number=91, question_type="MULTIPLE_CHOICE",
                statement_text="Questao oficial de producao", alternatives_text="A) A\nB) B\nC) C\nD) D\nE) E",
                correct_answer="C", position=1, page_start=2, page_end=2, status="extracted", metadata_={},
            )
            session.add(item)
            await session.flush()
            item_id = item.id
            await session.commit()

            result = await QuestionBankImporter(session).import_question(item_id)

        self.assertTrue(result.created)
        self.assertIsNotNone(result.question_id)
        self.assertIsNotNone(result.question_version_id)
        self.assertEqual(result.validation_status, "VALIDATED")

    async def test_duplicate_content_hash_reuses_existing_version_survives_expire_on_commit(self):
        """RED before the fix: the `existing` branch's return value read
        `existing.question_id`/`existing.id` after its own commit - same
        MissingGreenlet shape as the newly-created path above, on the
        already-imported / duplicate-statement path instead."""
        async with self.factory() as session:
            d1 = IngestionDocument(**self._doc_kwargs("D2_CD5", document_hash="doc-hash-dup-1"))
            session.add(d1)
            await session.flush()
            q1 = IngestionQuestion(
                document_id=d1.id, question_number=91, question_type="MULTIPLE_CHOICE",
                statement_text="Mesmo enunciado duplicado entre cadernos",
                alternatives_text="A) A\nB) B\nC) C\nD) D\nE) E", correct_answer="C",
                position=1, page_start=2, page_end=2, status="extracted", metadata_={},
            )
            session.add(q1)
            d2 = IngestionDocument(**self._doc_kwargs("D2_CD6", document_hash="doc-hash-dup-2"))
            session.add(d2)
            await session.flush()
            q2 = IngestionQuestion(
                document_id=d2.id, question_number=92, question_type="MULTIPLE_CHOICE",
                statement_text="Mesmo enunciado duplicado entre cadernos",
                alternatives_text="A) A\nB) B\nC) C\nD) D\nE) E", correct_answer="C",
                position=1, page_start=2, page_end=2, status="extracted", metadata_={},
            )
            session.add(q2)
            await session.flush()
            q1_id, q2_id = q1.id, q2.id
            await session.commit()

            importer = QuestionBankImporter(session)
            first = await importer.import_question(q1_id)
            second = await importer.import_question(q2_id)

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(second.validation_status, "IMPORTED")
        self.assertEqual(second.question_id, first.question_id)
        self.assertEqual(second.question_version_id, first.question_version_id)


class QuestionBankImporterBranchCoverageTests(unittest.IsolatedAsyncioTestCase):
    """Direct coverage of the remaining validation/lookup branches that the
    two fixtures above don't otherwise reach through a normal import."""

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

    async def test_unknown_ingestion_question_id_raises_value_error(self):
        import uuid
        async with self.factory() as session:
            with self.assertRaises(ValueError):
                await QuestionBankImporter(session).import_question(uuid.uuid4())

    async def test_missing_document_provenance_is_review_required(self):
        """`_validate`'s first guard: a document with no hash or no storage
        URI (or no document at all) can never be imported - it is flagged
        for review instead, never silently dropped. `document_hash` is a
        NOT NULL column, so the empty string (still falsy for `_validate`'s
        `not document.document_hash`) is what a real extraction failure
        would actually persist - `None` is not a reachable DB state here."""
        async with self.factory() as session:
            document, item = await self.ingestion_question(session)
            document.document_hash = ""
            item_id = item.id
            await session.commit()
            result = await QuestionBankImporter(session).import_question(item_id)
        self.assertTrue(result.review_required)
        self.assertEqual(result.reason, "Missing source document provenance")

    async def test_missing_question_number_is_review_required(self):
        """`question_number` is a NOT NULL column, so `0` (still falsy for
        `_validate`'s `not item.question_number`) is the reachable
        real-world stand-in for "no question number was extracted"."""
        async with self.factory() as session:
            _, item = await self.ingestion_question(session, question_number=0)
            item_id = item.id
            await session.commit()
            result = await QuestionBankImporter(session).import_question(item_id)
        self.assertTrue(result.review_required)
        self.assertEqual(result.reason, "Missing question number or statement")

    async def test_alternative_with_letter_outside_a_to_e_is_review_required(self):
        """The per-line regex only accepts A-E: a stray `F)` (or any other
        malformed line) fails the match and is reported as incomplete,
        rather than silently parsed into a wrong-shaped option set."""
        async with self.factory() as session:
            _, item = await self.ingestion_question(
                session, alternatives_text="A) A\nB) B\nC) C\nD) D\nF) F")
            await session.commit()
            result = await QuestionBankImporter(session).import_question(item.id)
        self.assertTrue(result.review_required)
        self.assertEqual(result.reason, "Alternatives are incomplete")

    async def test_correct_answer_not_resolving_to_an_option_raises(self):
        """Defensive guard (line 114): if the persisted options ever fail to
        contain the validated correct-answer key - a data race or a future
        change to `_validate` that stops guaranteeing this - the import must
        fail loudly (and roll back) instead of silently linking a wrong
        answer key. Simulated here by making the option-resolution SELECT
        return None, since `_validate`'s own guarantees make this
        unreachable through a legitimate DB state today."""
        from unittest.mock import patch
        async with self.factory() as session:
            _, item = await self.ingestion_question(session)
            item_id = item.id
            await session.commit()
            importer = QuestionBankImporter(session)
            real_scalar = session.scalar

            async def fake_scalar(stmt, *a, **kw):
                compiled = str(stmt)
                if "question_options" in compiled.lower() and "option_key" in compiled.lower():
                    return None
                return await real_scalar(stmt, *a, **kw)

            with patch.object(session, "scalar", side_effect=fake_scalar):
                with self.assertRaises(ValueError):
                    await importer.import_question(item_id)
            # a rollback (unlike a commit) always expires every tracked
            # object regardless of expire_on_commit - `item_id` (captured
            # earlier) is used here rather than `item.id` for that reason.
            restored = await session.get(IngestionQuestion, item_id)
            self.assertIsNone(restored.question_version_id)  # rolled back, never partially linked