"""
Repository-layer coverage for src/agente_ia_edu/repositories/assessments.py.

Wave 8 of the overnight bug-hunting campaign - first sweep of the REPOSITORY
layer (previous waves covered HTTP routes and services). This file was the
single biggest coverage gap in the whole campaign (43%, 113/199 statements
missing), so every repository class and every public method is exercised
here directly against a real async SQLAlchemy session backed by SQLite,
using the exact `expire_on_commit=True` default that
`create_session_factory()` uses in production (see
`src/agente_ia_edu/db/session.py`) - mirroring the fixture pattern in
`tests/test_assessments_route_coverage_http.py::setUp`.

Caller audit (grep across src/agente_ia_edu/services and
src/agente_ia_edu/api/routes):
  - Only `AssessmentAttemptRepository` (.create/.get/.list_by_publication/
    .finalize) and `AssessmentAnswerRepository`
    (.get_by_attempt_and_item/.create/.list_by_attempt) are actually
    instantiated and called in production, from
    `src/agente_ia_edu/api/routes/attempts.py`.
  - `AssessmentItemRepository` and `AssessmentPublicationRepository` are
    imported in attempts.py but never instantiated there (dead imports).
  - `AssessmentRepository`, `AssessmentVersionRepository`,
    `AssessmentSelectionRequestRepository`,
    `AssessmentAttemptRepository.get_next_attempt_number`/
    `.get_with_publication`, and `AssessmentAnswerRepository.correct_objective`/
    `.update` are not called from anywhere in `src/` at all right now -
    `src/agente_ia_edu/services/assessments.py`'s `AssessmentPersistenceService`
    re-implements the create/version/item/publication flows inline with its
    own (slightly different, in places more defensive) validation instead of
    delegating to this repository module. That duplication - not something
    this test file's zone permits fixing - is flagged separately below.
  - None of this repository file's own methods ever call `session.commit()`
    or `session.rollback()` (grep confirms only `.flush()`), so the
    commit-then-read MissingGreenlet bug class the campaign is watching for
    cannot originate *inside* this file; it would only show up at a caller's
    post-commit attribute access, which is out of this zone. Verified this
    file's methods behave correctly across a caller-shaped
    create -> commit -> refresh -> read sequence anyway (see
    `test_create_then_commit_then_refresh_reads_cleanly`).

Two real bugs were found by direct comparison with the equivalent, working
logic that already exists elsewhere in the codebase (services/assessments.py)
for the *same* operations - both are "missing pre-insert existence/uniqueness
check, so a real constraint violation surfaces as a raw unhandled
IntegrityError from flush() instead of a graceful ValueError" (the same bug
shape called out in the campaign brief). Both are fixed in
repositories/assessments.py; see the two `..._bug_is_fixed` tests below for
the failing-first proof.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    Assessment,
    AssessmentAttempt,
    AssessmentItem,
    AssessmentPublication,
    AssessmentVersion,
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
from agente_ia_edu.repositories.assessments import (
    AssessmentAnswerRepository,
    AssessmentAttemptRepository,
    AssessmentItemRepository,
    AssessmentPublicationRepository,
    AssessmentRepository,
    AssessmentSelectionRequestRepository,
    AssessmentVersionRepository,
)


class AssessmentRepositoryCoverageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
        )

        @event.listens_for(self.engine.sync_engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        # expire_on_commit=True mirrors create_session_factory()'s default
        # (api/dependencies.py calls it with no override) - deliberately not
        # relaxed, per the campaign brief.
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    # ------------------------------------------------------------------
    # seeding helpers
    # ------------------------------------------------------------------

    async def _seed_question_version(self, session) -> tuple:
        """Create a bare Question/QuestionVersion with two options."""
        question = Question(validation_status="approved", status="PUBLISHED")
        session.add(question)
        await session.flush()
        version = QuestionVersion(
            question_id=question.id,
            version_kind="official_original",
            canonical_text="Enunciado",
            content_hash=uuid4().hex,
        )
        session.add(version)
        await session.flush()
        option_a = QuestionOption(
            question_version_id=version.id, option_key="A", position=1, text="Certa"
        )
        option_b = QuestionOption(
            question_version_id=version.id, option_key="B", position=2, text="Errada"
        )
        session.add_all([option_a, option_b])
        await session.flush()
        return version, option_a, option_b

    async def _seed_official_answer_key(self, session, version, correct_option) -> AnswerKeyRevision:
        """Build the Institution -> ... -> AnswerKeyEntry chain resolving to
        `correct_option` for `version`, mirroring
        test_assessments_route_coverage_http.py's `_seed_question` helper."""
        institution = Institution(code=f"I-{uuid4().hex[:8]}", name="Instituicao")
        session.add(institution)
        await session.flush()
        exam = Exam(institution_id=institution.id, code=f"E-{uuid4().hex[:8]}", name="Exame")
        session.add(exam)
        await session.flush()
        application = ExamApplication(exam_id=exam.id, year=2026, application_type="regular")
        session.add(application)
        await session.flush()
        booklet = ExamBooklet(exam_application_id=application.id, code=f"B-{uuid4().hex[:8]}")
        source = SourceDocument(
            exam_application_id=application.id,
            document_type="proof",
            source_url="https://example.test/repo-coverage.pdf",
            acquired_at=datetime.now(timezone.utc),
            content_hash=uuid4().hex,
        )
        session.add_all([booklet, source])
        await session.flush()
        revision = AnswerKeyRevision(
            source_document_id=source.id, revision_number=1, is_official=True
        )
        session.add(revision)
        await session.flush()
        occurrence = BookletQuestion(
            exam_booklet_id=booklet.id, question_version_id=version.id, position=1
        )
        session.add(occurrence)
        await session.flush()
        session.add(
            AnswerKeyEntry(
                answer_key_revision_id=revision.id,
                booklet_question_id=occurrence.id,
                official_answer_label="A",
                resolved_option_id=correct_option.id,
            )
        )
        await session.flush()
        return revision

    async def _seed_publishable_version(self, session) -> tuple:
        """Assessment -> published AssessmentVersion -> one AssessmentItem
        pointing at a fresh question version (which already has two options,
        A and B - see _seed_question_version). Returns
        (assessment, version, item, option_a, option_b, question_version).

        `item.question_version` (the relationship, as opposed to the
        `item.question_version_id` FK column) is intentionally NOT touched
        here - it is returned separately as `question_version` so callers
        don't trigger an out-of-greenlet lazy load by accessing
        `item.question_version` directly from plain test code.
        """
        assessment = Assessment(title="Simulado", status="draft")
        session.add(assessment)
        await session.flush()
        version = AssessmentVersion(
            assessment_id=assessment.id, version_number=1, title="V1", status="published"
        )
        session.add(version)
        await session.flush()
        question_version, option_a, option_b = await self._seed_question_version(session)
        item = AssessmentItem(
            assessment_version_id=version.id,
            question_version_id=question_version.id,
            position=1,
            points=5,
        )
        session.add(item)
        await session.flush()
        return assessment, version, item, option_a, option_b, question_version

    # ------------------------------------------------------------------
    # AssessmentRepository
    # ------------------------------------------------------------------

    async def test_assessment_create_persists_defaults(self):
        async with self.session_factory() as session:
            repo = AssessmentRepository(session)
            assessment = await repo.create(title="Prova 1", description="Desc")
            self.assertIsNotNone(assessment.id)
            self.assertEqual(assessment.status, "draft")
            self.assertIsNone(assessment.institution_id)

    async def test_assessment_create_with_institution_id_as_string(self):
        async with self.session_factory() as session:
            institution = Institution(code=f"I-{uuid4().hex[:8]}", name="Inst")
            session.add(institution)
            await session.flush()
            institution_id = institution.id
            repo = AssessmentRepository(session)
            assessment = await repo.create(
                title="Prova 2", institution_id=str(institution_id)
            )
            self.assertEqual(assessment.institution_id, institution_id)

    async def test_assessment_get_found_and_not_found(self):
        async with self.session_factory() as session:
            repo = AssessmentRepository(session)
            created = await repo.create(title="Prova 3")
            created_id = created.id
            await session.commit()

            fetched = await repo.get(created_id)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.id, created_id)
            # selectinload(Assessment.versions) is requested by get(); reading
            # it must not need a lazy load.
            self.assertEqual(fetched.versions, [])

            missing = await repo.get(uuid4())
            self.assertIsNone(missing)

    async def test_assessment_list_orders_by_created_at_desc_with_limit_offset(self):
        async with self.session_factory() as session:
            repo = AssessmentRepository(session)
            first = await repo.create(title="Primeira")
            await session.flush()
            second = await repo.create(title="Segunda")
            await session.commit()

            page1 = await repo.list(limit=1, offset=0)
            self.assertEqual(len(page1), 1)
            self.assertEqual(page1[0].id, second.id)

            page2 = await repo.list(limit=1, offset=1)
            self.assertEqual(len(page2), 1)
            self.assertEqual(page2[0].id, first.id)

    # ------------------------------------------------------------------
    # AssessmentVersionRepository
    # ------------------------------------------------------------------

    async def test_version_create_persists_and_defaults_to_draft(self):
        async with self.session_factory() as session:
            assessment = Assessment(title="A", status="draft")
            session.add(assessment)
            await session.flush()
            repo = AssessmentVersionRepository(session)
            version = await repo.create(
                assessment_id=assessment.id, version_number=1, title="V1"
            )
            self.assertEqual(version.status, "draft")
            self.assertEqual(version.assessment_id, assessment.id)

    async def test_version_create_for_nonexistent_assessment_bug_is_fixed(self):
        """BUG (fixed): unlike AssessmentItemRepository.add (which validates
        its parent AssessmentVersion exists before inserting) and unlike the
        equivalent AssessmentPersistenceService.create_version in
        services/assessments.py (which does `if assessment is None: raise
        ValueError(...)`), this repository's create() inserted an
        AssessmentVersion row with a dangling assessment_id with no
        existence check at all - a bogus ID would only be caught by the DB's
        FK constraint (raw IntegrityError, not the graceful ValueError every
        sibling method in this class raises). Root cause fixed by adding the
        same existence check AssessmentItemRepository.add already has for
        its parent version lookup.
        """
        async with self.session_factory() as session:
            repo = AssessmentVersionRepository(session)
            with self.assertRaises(ValueError):
                await repo.create(
                    assessment_id=uuid4(), version_number=1, title="Orfa"
                )
            # Session must still be usable afterwards (ValueError raised
            # before flush, no dirty transaction left behind).
            await session.rollback()

    async def test_version_get_found_and_not_found(self):
        async with self.session_factory() as session:
            assessment = Assessment(title="A", status="draft")
            session.add(assessment)
            await session.flush()
            repo = AssessmentVersionRepository(session)
            created = await repo.create(
                assessment_id=assessment.id, version_number=1, title="V1"
            )
            created_id = created.id
            await session.commit()

            fetched = await repo.get(created_id)
            self.assertIsNotNone(fetched)
            self.assertEqual(fetched.items, [])

            self.assertIsNone(await repo.get(uuid4()))

    async def test_version_list_by_assessment_orders_by_version_number(self):
        async with self.session_factory() as session:
            assessment = Assessment(title="A", status="draft")
            session.add(assessment)
            await session.flush()
            assessment_id = assessment.id
            repo = AssessmentVersionRepository(session)
            v2 = await repo.create(
                assessment_id=assessment.id, version_number=2, title="V2"
            )
            v1 = await repo.create(
                assessment_id=assessment.id, version_number=1, title="V1"
            )
            v1_id, v2_id = v1.id, v2.id
            await session.commit()

            versions = await repo.list_by_assessment(assessment_id)
            self.assertEqual([v.id for v in versions], [v1_id, v2_id])

    async def test_ensure_mutable_raises_when_published_and_passes_otherwise(self):
        async with self.session_factory() as session:
            assessment = Assessment(title="A", status="draft")
            session.add(assessment)
            await session.flush()
            repo = AssessmentVersionRepository(session)
            draft = await repo.create(
                assessment_id=assessment.id, version_number=1, title="V1", status="draft"
            )
            published = await repo.create(
                assessment_id=assessment.id,
                version_number=2,
                title="V2",
                status="published",
            )

            await repo.ensure_mutable(draft)  # should not raise

            with self.assertRaises(ValueError):
                await repo.ensure_mutable(published)

    # ------------------------------------------------------------------
    # AssessmentItemRepository
    # ------------------------------------------------------------------

    async def test_item_add_success(self):
        async with self.session_factory() as session:
            assessment = Assessment(title="A", status="draft")
            session.add(assessment)
            await session.flush()
            version = AssessmentVersion(
                assessment_id=assessment.id, version_number=1, title="V1", status="draft"
            )
            session.add(version)
            await session.flush()
            question_version, _a, _b = await self._seed_question_version(session)

            repo = AssessmentItemRepository(session)
            item = await repo.add(
                assessment_version_id=version.id,
                question_version_id=question_version.id,
                position=1,
                points=3,
            )
            self.assertEqual(item.points, 3)
            self.assertTrue(item.is_required)

    async def test_item_add_version_not_found(self):
        async with self.session_factory() as session:
            repo = AssessmentItemRepository(session)
            with self.assertRaises(ValueError):
                await repo.add(
                    assessment_version_id=uuid4(),
                    question_version_id=uuid4(),
                    position=1,
                )

    async def test_item_add_rejected_when_version_published(self):
        async with self.session_factory() as session:
            assessment = Assessment(title="A", status="draft")
            session.add(assessment)
            await session.flush()
            version = AssessmentVersion(
                assessment_id=assessment.id,
                version_number=1,
                title="V1",
                status="published",
            )
            session.add(version)
            await session.flush()
            question_version, _a, _b = await self._seed_question_version(session)

            repo = AssessmentItemRepository(session)
            with self.assertRaises(ValueError):
                await repo.add(
                    assessment_version_id=version.id,
                    question_version_id=question_version.id,
                    position=1,
                )

    async def test_item_add_rejects_duplicate_position(self):
        async with self.session_factory() as session:
            assessment = Assessment(title="A", status="draft")
            session.add(assessment)
            await session.flush()
            version = AssessmentVersion(
                assessment_id=assessment.id, version_number=1, title="V1", status="draft"
            )
            session.add(version)
            await session.flush()
            qv1, _a, _b = await self._seed_question_version(session)
            qv2, _c, _d = await self._seed_question_version(session)

            repo = AssessmentItemRepository(session)
            await repo.add(
                assessment_version_id=version.id,
                question_version_id=qv1.id,
                position=1,
            )
            with self.assertRaises(ValueError):
                await repo.add(
                    assessment_version_id=version.id,
                    question_version_id=qv2.id,
                    position=1,
                )

    async def test_item_add_question_version_not_found(self):
        async with self.session_factory() as session:
            assessment = Assessment(title="A", status="draft")
            session.add(assessment)
            await session.flush()
            version = AssessmentVersion(
                assessment_id=assessment.id, version_number=1, title="V1", status="draft"
            )
            session.add(version)
            await session.flush()

            repo = AssessmentItemRepository(session)
            with self.assertRaises(ValueError):
                await repo.add(
                    assessment_version_id=version.id,
                    question_version_id=uuid4(),
                    position=1,
                )

    async def test_item_add_rejects_duplicate_question_version_bug_is_fixed(self):
        """BUG (fixed): the assessment_items table has
        uq_assessment_items_version_question_version (assessment_version_id,
        question_version_id) in addition to the position uniqueness this
        method already checks. add() checked position uniqueness explicitly
        but had no equivalent check for question_version_id, so adding the
        same question twice at two different positions hit the DB's unique
        index and surfaced as a raw IntegrityError from flush() instead of
        the graceful ValueError every other validation branch in this method
        raises. Root cause fixed by adding the same kind of existing_stmt
        pre-check for question_version_id.
        """
        async with self.session_factory() as session:
            assessment = Assessment(title="A", status="draft")
            session.add(assessment)
            await session.flush()
            version = AssessmentVersion(
                assessment_id=assessment.id, version_number=1, title="V1", status="draft"
            )
            session.add(version)
            await session.flush()
            question_version, _a, _b = await self._seed_question_version(session)

            repo = AssessmentItemRepository(session)
            await repo.add(
                assessment_version_id=version.id,
                question_version_id=question_version.id,
                position=1,
            )
            with self.assertRaises(ValueError):
                await repo.add(
                    assessment_version_id=version.id,
                    question_version_id=question_version.id,
                    position=2,
                )
            # And the session must still be usable (no leaked failed flush).
            await session.rollback()

    async def test_item_list_by_version_orders_by_position(self):
        async with self.session_factory() as session:
            assessment = Assessment(title="A", status="draft")
            session.add(assessment)
            await session.flush()
            version = AssessmentVersion(
                assessment_id=assessment.id, version_number=1, title="V1", status="draft"
            )
            session.add(version)
            await session.flush()
            qv1, _a, _b = await self._seed_question_version(session)
            qv2, _c, _d = await self._seed_question_version(session)
            repo = AssessmentItemRepository(session)
            second = await repo.add(
                assessment_version_id=version.id, question_version_id=qv2.id, position=2
            )
            first = await repo.add(
                assessment_version_id=version.id, question_version_id=qv1.id, position=1
            )

            items = await repo.list_by_version(version.id)
            self.assertEqual([i.id for i in items], [first.id, second.id])

    async def test_item_get_found_and_not_found(self):
        async with self.session_factory() as session:
            assessment = Assessment(title="A", status="draft")
            session.add(assessment)
            await session.flush()
            version = AssessmentVersion(
                assessment_id=assessment.id, version_number=1, title="V1", status="draft"
            )
            session.add(version)
            await session.flush()
            qv, _a, _b = await self._seed_question_version(session)
            repo = AssessmentItemRepository(session)
            item = await repo.add(
                assessment_version_id=version.id, question_version_id=qv.id, position=1
            )

            self.assertEqual((await repo.get(item.id)).id, item.id)
            self.assertIsNone(await repo.get(uuid4()))

    # ------------------------------------------------------------------
    # AssessmentSelectionRequestRepository
    # ------------------------------------------------------------------

    async def test_selection_request_create_defaults_criteria_to_empty_dict(self):
        async with self.session_factory() as session:
            assessment = Assessment(title="A", status="draft")
            session.add(assessment)
            await session.flush()
            version = AssessmentVersion(
                assessment_id=assessment.id, version_number=1, title="V1", status="draft"
            )
            session.add(version)
            await session.flush()

            repo = AssessmentSelectionRequestRepository(session)
            request = await repo.create(
                assessment_version_id=version.id, selection_type="manual"
            )
            self.assertEqual(request.criteria_, {})
            self.assertEqual(request.status, "pending")

    async def test_selection_request_create_with_criteria_and_prompt(self):
        async with self.session_factory() as session:
            assessment = Assessment(title="A", status="draft")
            session.add(assessment)
            await session.flush()
            version = AssessmentVersion(
                assessment_id=assessment.id, version_number=1, title="V1", status="draft"
            )
            session.add(version)
            await session.flush()

            repo = AssessmentSelectionRequestRepository(session)
            request = await repo.create(
                assessment_version_id=version.id,
                selection_type="prompt",
                original_prompt="10 questoes de quimica organica",
                requested_count=10,
                criteria={"difficulty": "medium"},
                status="completed",
            )
            self.assertEqual(request.requested_count, 10)
            self.assertEqual(request.criteria_, {"difficulty": "medium"})
            self.assertEqual(request.status, "completed")

    # ------------------------------------------------------------------
    # AssessmentPublicationRepository
    # ------------------------------------------------------------------

    async def _draft_and_published_versions(self, session):
        assessment = Assessment(title="A", status="draft")
        session.add(assessment)
        await session.flush()
        draft = AssessmentVersion(
            assessment_id=assessment.id, version_number=1, title="V1", status="draft"
        )
        published = AssessmentVersion(
            assessment_id=assessment.id, version_number=2, title="V2", status="published"
        )
        session.add_all([draft, published])
        await session.flush()
        return draft, published

    async def test_publication_create_success_with_defaults(self):
        # NOTE: create()'s guard is `if version.status == "published": raise
        # ValueError("Published versions cannot be republished")`. That is
        # the OPPOSITE of the domain rule the real (only) production
        # publication-creation path enforces - api/routes/assessments.py's
        # create_publication route requires `version.status == "published"`
        # before it will create a publication at all (a version's content
        # must be finalized/published before it can be released to
        # students), and services/assessments.py's
        # AssessmentPersistenceService.create_publication doesn't check
        # version.status a second time either. This repository method's
        # check is therefore inconsistent with the working implementation -
        # flagged via spawn_task rather than "fixed" here, since inverting a
        # domain guard's polarity based on inference risks being wrong
        # without a domain-owner's confirmation of intent. Tests below
        # exercise the code AS WRITTEN: a draft-status version succeeds, a
        # published-status version is rejected.
        async with self.session_factory() as session:
            draft, _published = await self._draft_and_published_versions(session)
            repo = AssessmentPublicationRepository(session)
            publication = await repo.create(
                assessment_version_id=draft.id, publication_type="immediate"
            )
            self.assertEqual(publication.status, "draft")
            self.assertEqual(publication.source_display, "none")
            self.assertEqual(publication.bncc_display, "none")
            self.assertFalse(publication.show_difficulty)

    async def test_publication_create_version_not_found(self):
        async with self.session_factory() as session:
            repo = AssessmentPublicationRepository(session)
            with self.assertRaises(ValueError):
                await repo.create(
                    assessment_version_id=uuid4(), publication_type="immediate"
                )

    async def test_publication_create_rejected_for_already_published_version(self):
        # See the note on test_publication_create_success_with_defaults: as
        # written, create() rejects a version whose status is "published".
        async with self.session_factory() as session:
            _draft, published = await self._draft_and_published_versions(session)
            repo = AssessmentPublicationRepository(session)
            with self.assertRaises(ValueError):
                await repo.create(
                    assessment_version_id=published.id, publication_type="immediate"
                )

    async def test_publication_create_rejects_unsupported_publication_type(self):
        async with self.session_factory() as session:
            draft, _published = await self._draft_and_published_versions(session)
            repo = AssessmentPublicationRepository(session)
            with self.assertRaises(ValueError):
                await repo.create(
                    assessment_version_id=draft.id, publication_type="weekly"
                )

    async def test_publication_create_rejects_end_before_start(self):
        async with self.session_factory() as session:
            draft, _published = await self._draft_and_published_versions(session)
            now = datetime.now(timezone.utc)
            repo = AssessmentPublicationRepository(session)
            with self.assertRaises(ValueError):
                await repo.create(
                    assessment_version_id=draft.id,
                    publication_type="scheduled",
                    starts_at=now,
                    ends_at=now - timedelta(hours=1),
                )

    async def test_publication_create_rejects_nonpositive_time_limit(self):
        async with self.session_factory() as session:
            draft, _published = await self._draft_and_published_versions(session)
            repo = AssessmentPublicationRepository(session)
            with self.assertRaises(ValueError):
                await repo.create(
                    assessment_version_id=draft.id,
                    publication_type="immediate",
                    time_limit_seconds=0,
                )

    async def test_publication_create_rejects_nonpositive_attempts_allowed(self):
        async with self.session_factory() as session:
            draft, _published = await self._draft_and_published_versions(session)
            repo = AssessmentPublicationRepository(session)
            with self.assertRaises(ValueError):
                await repo.create(
                    assessment_version_id=draft.id,
                    publication_type="immediate",
                    attempts_allowed=-1,
                )

    async def test_publication_get_found_and_not_found(self):
        async with self.session_factory() as session:
            draft, _published = await self._draft_and_published_versions(session)
            repo = AssessmentPublicationRepository(session)
            created = await repo.create(
                assessment_version_id=draft.id, publication_type="immediate"
            )
            created_id = created.id
            await session.commit()

            fetched = await repo.get(created_id)
            self.assertEqual(fetched.id, created_id)
            self.assertIsNone(await repo.get(uuid4()))

    async def test_publication_list_by_version_orders_by_created_at_desc(self):
        async with self.session_factory() as session:
            assessment = Assessment(title="A", status="draft")
            session.add(assessment)
            await session.flush()
            version = AssessmentVersion(
                assessment_id=assessment.id, version_number=1, title="V1", status="draft"
            )
            session.add(version)
            await session.flush()
            version_id = version.id
            first = AssessmentPublication(
                assessment_version_id=version.id,
                publication_type="immediate",
                status="draft",
                created_at=datetime.now(timezone.utc) - timedelta(minutes=5),
            )
            second = AssessmentPublication(
                assessment_version_id=version.id,
                publication_type="immediate",
                status="draft",
                created_at=datetime.now(timezone.utc),
            )
            session.add_all([first, second])
            await session.flush()
            first_id, second_id = first.id, second.id
            await session.commit()

            repo = AssessmentPublicationRepository(session)
            items = await repo.list_by_version(version_id)
            self.assertEqual([p.id for p in items], [second_id, first_id])

    # ------------------------------------------------------------------
    # AssessmentAttemptRepository
    # ------------------------------------------------------------------

    async def _seed_publication(self, session, **overrides):
        assessment = Assessment(title="A", status="draft")
        session.add(assessment)
        await session.flush()
        version = AssessmentVersion(
            assessment_id=assessment.id, version_number=1, title="V1", status="published"
        )
        session.add(version)
        await session.flush()
        payload = dict(
            assessment_version_id=version.id,
            publication_type="immediate",
            status="active",
        )
        payload.update(overrides)
        publication = AssessmentPublication(**payload)
        session.add(publication)
        await session.flush()
        return publication

    async def test_attempt_create_success(self):
        async with self.session_factory() as session:
            publication = await self._seed_publication(session)
            repo = AssessmentAttemptRepository(session)
            attempt = await repo.create(
                publication_id=publication.id,
                external_identity_id="student-1",
                attempt_number=1,
            )
            self.assertEqual(attempt.status, "in_progress")
            self.assertEqual(attempt.score, 0)

    async def test_attempt_create_publication_not_found(self):
        async with self.session_factory() as session:
            repo = AssessmentAttemptRepository(session)
            with self.assertRaises(ValueError):
                await repo.create(
                    publication_id=uuid4(),
                    external_identity_id="student-1",
                    attempt_number=1,
                )

    async def test_attempt_create_rejects_attempt_number_over_limit(self):
        async with self.session_factory() as session:
            publication = await self._seed_publication(session, attempts_allowed=1)
            repo = AssessmentAttemptRepository(session)
            await repo.create(
                publication_id=publication.id,
                external_identity_id="student-1",
                attempt_number=1,
            )
            with self.assertRaises(ValueError):
                await repo.create(
                    publication_id=publication.id,
                    external_identity_id="student-1",
                    attempt_number=2,
                )

    async def test_attempt_create_allows_unlimited_attempts_when_not_capped(self):
        async with self.session_factory() as session:
            publication = await self._seed_publication(session, attempts_allowed=None)
            repo = AssessmentAttemptRepository(session)
            attempt = await repo.create(
                publication_id=publication.id,
                external_identity_id="student-1",
                attempt_number=5,
            )
            self.assertEqual(attempt.attempt_number, 5)

    async def test_attempt_get_found_with_answers_eager_loaded_and_not_found(self):
        async with self.session_factory() as session:
            publication = await self._seed_publication(session)
            attempt_repo = AssessmentAttemptRepository(session)
            attempt = await attempt_repo.create(
                publication_id=publication.id,
                external_identity_id="student-1",
                attempt_number=1,
            )
            attempt_id = attempt.id
            await session.commit()

            fetched = await attempt_repo.get(attempt_id)
            self.assertIsNotNone(fetched)
            # selectinload(AssessmentAttempt.answers) means this must not
            # trigger a lazy load.
            self.assertEqual(fetched.answers, [])

            self.assertIsNone(await attempt_repo.get(uuid4()))

    async def test_attempt_list_by_publication_filters_by_identity_and_orders(self):
        async with self.session_factory() as session:
            publication = await self._seed_publication(session)
            publication_id = publication.id
            repo = AssessmentAttemptRepository(session)
            await repo.create(
                publication_id=publication_id,
                external_identity_id="student-1",
                attempt_number=1,
            )
            await repo.create(
                publication_id=publication_id,
                external_identity_id="student-2",
                attempt_number=1,
            )
            await repo.create(
                publication_id=publication_id,
                external_identity_id="student-1",
                attempt_number=2,
            )
            await session.commit()

            all_attempts = await repo.list_by_publication(publication_id=publication_id)
            self.assertEqual(len(all_attempts), 3)

            student1_only = await repo.list_by_publication(
                publication_id=publication_id, external_identity_id="student-1"
            )
            self.assertEqual([a.attempt_number for a in student1_only], [1, 2])

    async def test_get_next_attempt_number_starts_at_one_and_increments(self):
        async with self.session_factory() as session:
            publication = await self._seed_publication(session)
            publication_id = publication.id
            repo = AssessmentAttemptRepository(session)

            first = await repo.get_next_attempt_number(
                publication_id=publication_id, external_identity_id="student-1"
            )
            self.assertEqual(first, 1)

            await repo.create(
                publication_id=publication_id,
                external_identity_id="student-1",
                attempt_number=1,
            )
            await session.commit()

            second = await repo.get_next_attempt_number(
                publication_id=publication_id, external_identity_id="student-1"
            )
            self.assertEqual(second, 2)

            # A different student's attempts must not bleed into the count.
            other_student = await repo.get_next_attempt_number(
                publication_id=publication_id, external_identity_id="student-2"
            )
            self.assertEqual(other_student, 1)

    async def test_get_with_publication_found_and_not_found(self):
        async with self.session_factory() as session:
            publication = await self._seed_publication(session)
            publication_id = publication.id
            repo = AssessmentAttemptRepository(session)
            attempt = await repo.create(
                publication_id=publication_id,
                external_identity_id="student-1",
                attempt_number=1,
            )
            attempt_id = attempt.id
            await session.commit()

            result = await repo.get_with_publication(attempt_id)
            self.assertIsNotNone(result)
            fetched_attempt, fetched_publication = result
            self.assertEqual(fetched_attempt.id, attempt_id)
            self.assertEqual(fetched_publication.id, publication_id)

            self.assertIsNone(await repo.get_with_publication(uuid4()))

    async def test_finalize_marks_submitted_and_survives_flush(self):
        async with self.session_factory() as session:
            publication = await self._seed_publication(session)
            repo = AssessmentAttemptRepository(session)
            attempt = await repo.create(
                publication_id=publication.id,
                external_identity_id="student-1",
                attempt_number=1,
            )
            submitted_at = datetime.now(timezone.utc)
            finalized = await repo.finalize(attempt, submitted_at=submitted_at)
            self.assertEqual(finalized.status, "submitted")
            self.assertEqual(finalized.submitted_at, submitted_at)
            # flush() (not commit()) must not expire the object - reading
            # attributes again must not need a lazy load.
            self.assertEqual(attempt.status, "submitted")

    async def test_finalize_defaults_submitted_at_to_now(self):
        async with self.session_factory() as session:
            publication = await self._seed_publication(session)
            repo = AssessmentAttemptRepository(session)
            attempt = await repo.create(
                publication_id=publication.id,
                external_identity_id="student-1",
                attempt_number=1,
            )
            before = datetime.now(timezone.utc)
            finalized = await repo.finalize(attempt)
            after = datetime.now(timezone.utc)
            self.assertLessEqual(before, finalized.submitted_at)
            self.assertLessEqual(finalized.submitted_at, after)

    async def test_create_then_commit_then_refresh_reads_cleanly(self):
        """Mirrors the caller-shaped sequence attempts.py uses (create ->
        commit -> refresh -> read attributes) to confirm nothing in this
        repository module's returned objects fights that pattern under
        expire_on_commit=True."""
        async with self.session_factory() as session:
            publication = await self._seed_publication(session)
            repo = AssessmentAttemptRepository(session)
            attempt = await repo.create(
                publication_id=publication.id,
                external_identity_id="student-1",
                attempt_number=1,
            )
            await session.commit()
            await session.refresh(attempt)
            self.assertEqual(attempt.status, "in_progress")
            self.assertEqual(attempt.attempt_number, 1)

    # ------------------------------------------------------------------
    # AssessmentAnswerRepository
    # ------------------------------------------------------------------

    async def _seed_attempt(self, session):
        _assessment, version, item, option_a, _option_b, _qv = await self._seed_publishable_version(
            session
        )
        publication = AssessmentPublication(
            assessment_version_id=version.id,
            publication_type="immediate",
            status="active",
        )
        session.add(publication)
        await session.flush()
        attempt = AssessmentAttempt(
            publication_id=publication.id,
            external_identity_id="student-1",
            attempt_number=1,
            status="in_progress",
        )
        session.add(attempt)
        await session.flush()
        return attempt, item, option_a

    async def test_answer_create_success_without_option(self):
        async with self.session_factory() as session:
            attempt, item, _option_a = await self._seed_attempt(session)
            repo = AssessmentAnswerRepository(session)
            answer = await repo.create(
                attempt_id=attempt.id,
                assessment_item_id=item.id,
                response_text="minha resposta",
            )
            self.assertEqual(answer.correction_status, "pending")
            self.assertIsNone(answer.is_correct)

    async def test_answer_create_success_with_valid_option(self):
        async with self.session_factory() as session:
            attempt, item, option_a = await self._seed_attempt(session)
            repo = AssessmentAnswerRepository(session)
            answer = await repo.create(
                attempt_id=attempt.id,
                assessment_item_id=item.id,
                selected_option_id=option_a.id,
            )
            self.assertEqual(answer.selected_option_id, option_a.id)

    async def test_answer_create_item_not_found(self):
        async with self.session_factory() as session:
            attempt, _item, _option_a = await self._seed_attempt(session)
            repo = AssessmentAnswerRepository(session)
            with self.assertRaises(ValueError):
                await repo.create(attempt_id=attempt.id, assessment_item_id=uuid4())

    async def test_answer_create_selected_option_not_found(self):
        async with self.session_factory() as session:
            attempt, item, _option_a = await self._seed_attempt(session)
            repo = AssessmentAnswerRepository(session)
            with self.assertRaises(ValueError):
                await repo.create(
                    attempt_id=attempt.id,
                    assessment_item_id=item.id,
                    selected_option_id=uuid4(),
                )

    async def test_answer_create_selected_option_from_different_question_version_rejected(self):
        async with self.session_factory() as session:
            attempt, item, _option_a = await self._seed_attempt(session)
            other_version, other_option, _b = await self._seed_question_version(session)
            repo = AssessmentAnswerRepository(session)
            with self.assertRaises(ValueError):
                await repo.create(
                    attempt_id=attempt.id,
                    assessment_item_id=item.id,
                    selected_option_id=other_option.id,
                )

    async def test_answer_get_by_attempt_and_item_found_and_not_found(self):
        async with self.session_factory() as session:
            attempt, item, option_a = await self._seed_attempt(session)
            repo = AssessmentAnswerRepository(session)
            created = await repo.create(
                attempt_id=attempt.id, assessment_item_id=item.id, selected_option_id=option_a.id
            )

            found = await repo.get_by_attempt_and_item(
                attempt_id=attempt.id, assessment_item_id=item.id
            )
            self.assertEqual(found.id, created.id)

            self.assertIsNone(
                await repo.get_by_attempt_and_item(
                    attempt_id=attempt.id, assessment_item_id=uuid4()
                )
            )

    async def test_answer_list_by_attempt(self):
        async with self.session_factory() as session:
            (
                _assessment,
                version,
                item1,
                option_a,
                _option_b,
                _qv1,
            ) = await self._seed_publishable_version(session)
            qv2, option_c, _d = await self._seed_question_version(session)
            item2 = AssessmentItem(
                assessment_version_id=version.id,
                question_version_id=qv2.id,
                position=2,
                points=2,
            )
            session.add(item2)
            await session.flush()
            publication = AssessmentPublication(
                assessment_version_id=version.id, publication_type="immediate", status="active"
            )
            session.add(publication)
            await session.flush()
            attempt = AssessmentAttempt(
                publication_id=publication.id,
                external_identity_id="student-1",
                attempt_number=1,
                status="in_progress",
            )
            session.add(attempt)
            await session.flush()

            repo = AssessmentAnswerRepository(session)
            await repo.create(
                attempt_id=attempt.id, assessment_item_id=item1.id, selected_option_id=option_a.id
            )
            await repo.create(
                attempt_id=attempt.id, assessment_item_id=item2.id, selected_option_id=option_c.id
            )

            answers = await repo.list_by_attempt(attempt.id)
            self.assertEqual(len(answers), 2)

    async def test_correct_objective_item_not_found(self):
        # A transient (never persisted) AssessmentAnswer pointing at a bogus
        # assessment_item_id: correct_objective only reads
        # answer.assessment_item_id/selected_option_id off the object it is
        # given and looks the item up itself, so this exercises the
        # "Assessment item does not exist" branch without touching the FK
        # (assessment_answers.assessment_item_id is ondelete=RESTRICT, so a
        # persisted row could never actually end up pointing at a deleted
        # item this way in production either).
        from agente_ia_edu.db.models import AssessmentAnswer as AssessmentAnswerModel

        async with self.session_factory() as session:
            answer = AssessmentAnswerModel(
                attempt_id=uuid4(),
                assessment_item_id=uuid4(),
                selected_option_id=uuid4(),
            )
            repo = AssessmentAnswerRepository(session)
            with self.assertRaises(ValueError):
                await repo.correct_objective(answer)

    async def test_correct_objective_no_selected_option_marks_incorrect(self):
        async with self.session_factory() as session:
            attempt, item, _option_a = await self._seed_attempt(session)
            repo = AssessmentAnswerRepository(session)
            answer = await repo.create(
                attempt_id=attempt.id, assessment_item_id=item.id, response_text="UNKNOWN"
            )
            corrected = await repo.correct_objective(answer)
            self.assertFalse(corrected.is_correct)
            self.assertEqual(corrected.points_awarded, 0)
            self.assertEqual(corrected.correction_status, "incorrect")

    async def test_correct_objective_no_official_answer_key_raises(self):
        async with self.session_factory() as session:
            attempt, item, option_a = await self._seed_attempt(session)
            repo = AssessmentAnswerRepository(session)
            answer = await repo.create(
                attempt_id=attempt.id, assessment_item_id=item.id, selected_option_id=option_a.id
            )
            with self.assertRaises(ValueError):
                await repo.correct_objective(answer)

    async def test_correct_objective_official_key_unresolved_option_raises(self):
        async with self.session_factory() as session:
            (
                _assessment,
                version,
                item,
                option_a,
                _option_b,
                question_version,
            ) = await self._seed_publishable_version(session)
            # Build an official answer key entry with resolved_option_id=None
            # (matches the "no official winner resolved yet" real-world case).
            institution = Institution(code=f"I-{uuid4().hex[:8]}", name="Inst")
            session.add(institution)
            await session.flush()
            exam = Exam(institution_id=institution.id, code=f"E-{uuid4().hex[:8]}", name="Exame")
            session.add(exam)
            await session.flush()
            application = ExamApplication(exam_id=exam.id, year=2026, application_type="regular")
            session.add(application)
            await session.flush()
            booklet = ExamBooklet(exam_application_id=application.id, code=f"B-{uuid4().hex[:8]}")
            source = SourceDocument(
                exam_application_id=application.id,
                document_type="proof",
                source_url="https://example.test/unresolved.pdf",
                acquired_at=datetime.now(timezone.utc),
                content_hash=uuid4().hex,
            )
            session.add_all([booklet, source])
            await session.flush()
            revision = AnswerKeyRevision(
                source_document_id=source.id, revision_number=1, is_official=True
            )
            session.add(revision)
            await session.flush()
            occurrence = BookletQuestion(
                exam_booklet_id=booklet.id, question_version_id=question_version.id, position=1
            )
            session.add(occurrence)
            await session.flush()
            session.add(
                AnswerKeyEntry(
                    answer_key_revision_id=revision.id,
                    booklet_question_id=occurrence.id,
                    official_answer_label="?",
                    resolved_option_id=None,
                )
            )
            await session.flush()

            publication = AssessmentPublication(
                assessment_version_id=version.id, publication_type="immediate", status="active"
            )
            session.add(publication)
            await session.flush()
            attempt = AssessmentAttempt(
                publication_id=publication.id,
                external_identity_id="student-1",
                attempt_number=1,
                status="in_progress",
            )
            session.add(attempt)
            await session.flush()

            repo = AssessmentAnswerRepository(session)
            answer = await repo.create(
                attempt_id=attempt.id, assessment_item_id=item.id, selected_option_id=option_a.id
            )
            with self.assertRaises(ValueError):
                await repo.correct_objective(answer)

    async def test_correct_objective_correct_and_incorrect_selection(self):
        async with self.session_factory() as session:
            (
                _assessment,
                version,
                item,
                option_a,
                _option_b,
                question_version,
            ) = await self._seed_publishable_version(session)
            await self._seed_official_answer_key(session, question_version, option_a)

            publication = AssessmentPublication(
                assessment_version_id=version.id, publication_type="immediate", status="active"
            )
            session.add(publication)
            await session.flush()
            attempt = AssessmentAttempt(
                publication_id=publication.id,
                external_identity_id="student-1",
                attempt_number=1,
                status="in_progress",
            )
            session.add(attempt)
            await session.flush()

            repo = AssessmentAnswerRepository(session)

            correct_answer = await repo.create(
                attempt_id=attempt.id, assessment_item_id=item.id, selected_option_id=option_a.id
            )
            corrected = await repo.correct_objective(correct_answer)
            self.assertTrue(corrected.is_correct)
            self.assertEqual(corrected.points_awarded, item.points)
            self.assertEqual(corrected.correction_status, "correct")

    async def test_correct_objective_incorrect_selection(self):
        async with self.session_factory() as session:
            (
                _assessment,
                version,
                item,
                option_a,
                option_b,
                question_version,
            ) = await self._seed_publishable_version(session)
            await self._seed_official_answer_key(session, question_version, option_a)

            publication = AssessmentPublication(
                assessment_version_id=version.id, publication_type="immediate", status="active"
            )
            session.add(publication)
            await session.flush()
            attempt = AssessmentAttempt(
                publication_id=publication.id,
                external_identity_id="student-1",
                attempt_number=1,
                status="in_progress",
            )
            session.add(attempt)
            await session.flush()

            repo = AssessmentAnswerRepository(session)
            wrong_answer = await repo.create(
                attempt_id=attempt.id, assessment_item_id=item.id, selected_option_id=option_b.id
            )
            corrected = await repo.correct_objective(wrong_answer)
            self.assertFalse(corrected.is_correct)
            self.assertEqual(corrected.points_awarded, 0)
            self.assertEqual(corrected.correction_status, "incorrect")

    async def test_answer_update_selected_option_and_response_text(self):
        async with self.session_factory() as session:
            attempt, item, option_a = await self._seed_attempt(session)
            repo = AssessmentAnswerRepository(session)
            answer = await repo.create(attempt_id=attempt.id, assessment_item_id=item.id)

            updated = await repo.update(answer, response_text="primeira resposta")
            self.assertEqual(updated.response_text, "primeira resposta")
            self.assertIsNone(updated.selected_option_id)

            updated = await repo.update(answer, selected_option_id=option_a.id)
            self.assertEqual(updated.selected_option_id, option_a.id)
            # response_text from the previous call must be preserved since
            # this call didn't pass response_text.
            self.assertEqual(updated.response_text, "primeira resposta")

    async def test_answer_update_with_no_fields_only_touches_updated_at(self):
        async with self.session_factory() as session:
            attempt, item, option_a = await self._seed_attempt(session)
            repo = AssessmentAnswerRepository(session)
            answer = await repo.create(
                attempt_id=attempt.id, assessment_item_id=item.id, selected_option_id=option_a.id
            )
            original_updated_at = answer.updated_at

            updated = await repo.update(answer)
            self.assertEqual(updated.selected_option_id, option_a.id)
            self.assertGreaterEqual(updated.updated_at, original_updated_at)


if __name__ == "__main__":
    unittest.main()
