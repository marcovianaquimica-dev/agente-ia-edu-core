"""Direct unit tests for services/answer_key.py.

Small, self-contained module: "what is the official correct option for a
given question version". No dedicated test file existed before - all prior
coverage came indirectly through HTTP-level tests (e.g. the N+1 regression
test tests/test_practice_session_complete_answer_key_n1.py). This file tests
both functions directly against an in-memory sqlite engine, following the
same model-seeding pattern as that N+1 test.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    BookletQuestion,
    Exam,
    ExamApplication,
    ExamBooklet,
    Institution,
    Question,
    QuestionOption,
    QuestionVersion,
    SourceDocument,
)
from agente_ia_edu.services.answer_key import (
    resolve_official_answer_key_snapshots,
    resolve_official_correct_option_id,
)


class AnswerKeyResolutionTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_async_engine(
            "sqlite+aiosqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        cls.session_factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _init():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(_init())

    @classmethod
    def tearDownClass(cls):
        asyncio.run(cls.engine.dispose())

    async def _seed_question_with_answer_key(
        self,
        *,
        correct_key: str = "A",
        extra_revision: bool = False,
    ):
        """One question version, on a booklet, with an official answer-key
        entry resolving to its option `correct_key`. Returns
        (question_version_id, option_ids_by_key, revision_ids_newest_first)."""
        async with self.session_factory() as session:
            institution = Institution(code=f"AK-{uuid.uuid4().hex[:8]}", name="AK Institution")
            session.add(institution)
            await session.flush()
            exam = Exam(institution_id=institution.id, code=f"AKEX-{uuid.uuid4().hex[:8]}", name="AK Exam")
            session.add(exam)
            await session.flush()
            application = ExamApplication(exam_id=exam.id, year=2024, application_type="regular")
            session.add(application)
            await session.flush()
            booklet = ExamBooklet(exam_application_id=application.id, code=f"AKBK-{uuid.uuid4().hex[:6]}")
            session.add(booklet)
            await session.flush()
            source_document = SourceDocument(
                exam_application_id=application.id,
                document_type="proof",
                source_url="https://example.com/ak.pdf",
                acquired_at=datetime.now(timezone.utc),
                content_hash=uuid.uuid4().hex,
            )
            session.add(source_document)
            await session.flush()

            question = Question(validation_status="validated")
            session.add(question)
            await session.flush()
            version = QuestionVersion(
                question_id=question.id,
                version_kind="official_original",
                canonical_text=f"AK Question {uuid.uuid4().hex[:6]}?",
                content_hash=uuid.uuid4().hex,
            )
            session.add(version)
            await session.flush()

            option_ids = {}
            for j, key in enumerate(["A", "B", "C", "D"], start=1):
                option = QuestionOption(
                    question_version_id=version.id, option_key=key, position=j, text=f"Option {key}"
                )
                session.add(option)
                await session.flush()
                option_ids[key] = option.id

            booklet_question = BookletQuestion(
                exam_booklet_id=booklet.id, question_version_id=version.id, position=1
            )
            session.add(booklet_question)
            await session.flush()

            revision_ids: list[uuid.UUID] = []
            revision1 = AnswerKeyRevision(
                source_document_id=source_document.id, revision_number=1, is_official=True
            )
            session.add(revision1)
            await session.flush()
            entry1 = AnswerKeyEntry(
                answer_key_revision_id=revision1.id,
                booklet_question_id=booklet_question.id,
                official_answer_label=correct_key,
                resolved_option_id=option_ids[correct_key],
            )
            session.add(entry1)
            await session.flush()
            revision_ids.append(revision1.id)

            if extra_revision:
                # A later, superseding official revision that corrects the
                # answer to a different option - resolution must pick THIS
                # one (highest revision_number), not revision 1.
                revision2 = AnswerKeyRevision(
                    source_document_id=source_document.id,
                    revision_number=2,
                    is_official=True,
                    supersedes_id=revision1.id,
                )
                session.add(revision2)
                await session.flush()
                corrected_key = "B" if correct_key != "B" else "C"
                entry2 = AnswerKeyEntry(
                    answer_key_revision_id=revision2.id,
                    booklet_question_id=booklet_question.id,
                    official_answer_label=corrected_key,
                    resolved_option_id=option_ids[corrected_key],
                )
                session.add(entry2)
                await session.flush()
                revision_ids.insert(0, revision2.id)

            await session.commit()
            return version.id, option_ids, revision_ids

    # -- resolve_official_correct_option_id -------------------------------

    def test_resolve_official_correct_option_id_returns_none_when_no_answer_key(self):
        async def run():
            async with self.session_factory() as session:
                question = Question(validation_status="validated")
                session.add(question)
                await session.flush()
                version = QuestionVersion(
                    question_id=question.id,
                    version_kind="official_original",
                    canonical_text="No answer key yet?",
                    content_hash=uuid.uuid4().hex,
                )
                session.add(version)
                await session.commit()

                result = await resolve_official_correct_option_id(session, version.id)
                self.assertIsNone(result)

        asyncio.run(run())

    def test_resolve_official_correct_option_id_returns_the_resolved_option(self):
        version_id, option_ids, _ = asyncio.run(
            self._seed_question_with_answer_key(correct_key="C")
        )

        async def run():
            async with self.session_factory() as session:
                return await resolve_official_correct_option_id(session, version_id)

        result = asyncio.run(run())
        self.assertEqual(result, option_ids["C"])

    def test_resolve_official_correct_option_id_picks_latest_revision(self):
        version_id, option_ids, revision_ids = asyncio.run(
            self._seed_question_with_answer_key(correct_key="A", extra_revision=True)
        )

        async def run():
            async with self.session_factory() as session:
                return await resolve_official_correct_option_id(session, version_id)

        result = asyncio.run(run())
        # revision 2 (the superseding, higher revision_number) must win, not
        # revision 1's original "A".
        self.assertEqual(result, option_ids["B"])
        self.assertNotEqual(result, option_ids["A"])

    # -- resolve_official_answer_key_snapshots -----------------------------

    def test_resolve_official_answer_key_snapshots_empty_list_returns_empty_dict(self):
        async def run():
            async with self.session_factory() as session:
                return await resolve_official_answer_key_snapshots(session, [])

        result = asyncio.run(run())
        self.assertEqual(result, {})

    def test_resolve_official_answer_key_snapshots_resolves_latest_revision_per_version(self):
        version_id_1, option_ids_1, _ = asyncio.run(
            self._seed_question_with_answer_key(correct_key="D")
        )
        version_id_2, option_ids_2, revision_ids_2 = asyncio.run(
            self._seed_question_with_answer_key(correct_key="A", extra_revision=True)
        )

        async def run():
            async with self.session_factory() as session:
                return await resolve_official_answer_key_snapshots(
                    session, [version_id_1, version_id_2]
                )

        snapshots = asyncio.run(run())
        self.assertEqual(set(snapshots.keys()), {version_id_1, version_id_2})

        rev1_id, opt1_id = snapshots[version_id_1]
        self.assertEqual(opt1_id, option_ids_1["D"])

        rev2_id, opt2_id = snapshots[version_id_2]
        self.assertEqual(opt2_id, option_ids_2["B"])  # the superseding revision's answer
        self.assertEqual(rev2_id, revision_ids_2[0])  # revision_ids_2[0] is the newest


if __name__ == "__main__":
    unittest.main()
