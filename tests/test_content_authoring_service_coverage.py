"""Service-level coverage for agente_ia_edu.services.content_authoring.

content_authoring.py was, at the time this file was written, the
worst-covered service in the app (48%, 126/242 statements missing). The one
live HTTP caller (POST /api/v1/questions/{id}/review in
src/agente_ia_edu/api/routes/questions.py) only reaches
QuestionAuthoringService.submit_for_review/approve/reject/archive_question,
and even for those it only reaches the *success* and *invalid-status*
branches - `_load_question_for_manage` already 404s before the service's own
"question not found" branch can ever run, so that branch is only reachable
by calling the service directly (which is a legitimate use of its public
API - nothing here is a fabricated hypothetical).

QuestionAuthoringService.create_question / create_new_version /
suggest_classification / publish_question and the entire
MaterialAuthoringService class have NO caller anywhere in src/ (grepped
across api/routes/) - they are only exercised through direct service-level
tests, here and in test_pedagogical_classification_lifecycle.py (which
covers publish_question's success path via
ConsumerCompatibilityTests.test_I_publish_question_works_after_supersession
and is intentionally not duplicated here).

Convention (StaticPool in-memory SQLite + async_sessionmaker) matches
test_content_authoring_status_case_mismatch.py and
test_pedagogical_classification_lifecycle.py in this same directory.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    PedagogicalClassification,
    Question,
    QuestionOption,
    TheoryMaterial,
    TheoryMaterialVersion,
)
from agente_ia_edu.services.content_authoring import (
    MaterialAuthoringService,
    MaterialWorkflowStatus,
    QuestionAuthoringResult,
    QuestionAuthoringService,
    QuestionWorkflowStatus,
)


async def _make_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    return engine, factory


# --------------------------------------------------------------------------- #
# QuestionAuthoringService.create_question
# --------------------------------------------------------------------------- #


class CreateQuestionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_happy_path_persists_draft_question_version_and_options(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            school_id = uuid.uuid4()
            result = await service.create_question(
                created_by_external_identity="prof-1",
                statement="  Qual e a capital do Brasil?  ",
                options=["Brasilia", "Rio de Janeiro", "Sao Paulo"],
                correct_option="Brasilia",
                school_id=school_id,
                metadata={"source": "unit-test"},
            )
            self.assertIsInstance(result, QuestionAuthoringResult)
            self.assertEqual(result.status, QuestionWorkflowStatus.DRAFT.value)

            question = await session.get(Question, result.question_id)
            self.assertEqual(question.validation_status, "DRAFT")

            version = await service.get_current_version(result.question_id)
            self.assertEqual(version.id, result.version_id)
            # leading/trailing whitespace must be stripped from the canonical text
            self.assertEqual(version.canonical_text, "Qual e a capital do Brasil?")
            self.assertEqual(version.metadata_["status"], QuestionWorkflowStatus.DRAFT.value)
            self.assertEqual(version.metadata_["school_id"], str(school_id))
            self.assertEqual(version.metadata_["origin_type"], "AUTHOR")
            self.assertEqual(version.metadata_["visibility_scope"], "PRIVATE")
            self.assertEqual(version.metadata_["source"], "unit-test")

            options = (
                await session.scalars(
                    select(QuestionOption).where(QuestionOption.question_version_id == version.id)
                )
            ).all()
            options_by_text = {o.text: o.is_valid_option for o in options}
            self.assertEqual(
                options_by_text,
                {"Brasilia": True, "Rio de Janeiro": False, "Sao Paulo": False},
            )

    async def test_default_author_type_and_no_school_id(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            result = await service.create_question(
                created_by_external_identity="prof-2",
                statement="Question without a school",
                options=["A", "B"],
                correct_option="A",
            )
            version = await service.get_current_version(result.question_id)
            self.assertEqual(version.created_by_type, "TEACHER")
            self.assertIsNone(version.metadata_["school_id"])

    async def test_rejects_blank_statement(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "statement is required"):
                await service.create_question(
                    created_by_external_identity="prof-1",
                    statement="   ",
                    options=["A", "B"],
                    correct_option="A",
                )

    async def test_rejects_empty_statement(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "statement is required"):
                await service.create_question(
                    created_by_external_identity="prof-1",
                    statement="",
                    options=["A", "B"],
                    correct_option="A",
                )

    async def test_requires_at_least_two_options(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "At least two answer options"):
                await service.create_question(
                    created_by_external_identity="prof-1",
                    statement="Only one option?",
                    options=["A"],
                    correct_option="A",
                )

    async def test_requires_correct_option_to_be_among_options(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "correct option must exist"):
                await service.create_question(
                    created_by_external_identity="prof-1",
                    statement="Mismatched correct option",
                    options=["A", "B"],
                    correct_option="C",
                )


# --------------------------------------------------------------------------- #
# QuestionAuthoringService.get_current_version
# --------------------------------------------------------------------------- #


class GetCurrentVersionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_raises_when_question_has_no_version(self):
        async with self.factory() as session:
            question = Question(validation_status=QuestionWorkflowStatus.DRAFT.value)
            session.add(question)
            await session.flush()
            service = QuestionAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "does not have a version"):
                await service.get_current_version(question.id)


# --------------------------------------------------------------------------- #
# QuestionAuthoringService.create_new_version
# --------------------------------------------------------------------------- #


class CreateNewVersionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_question(self, session):
        service = QuestionAuthoringService(session)
        result = await service.create_question(
            created_by_external_identity="prof-1",
            statement="Original statement",
            options=["A", "B"],
            correct_option="A",
        )
        return service, result

    async def test_not_found_raises(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "Question not found"):
                await service.create_new_version(
                    question_id=uuid.uuid4(),
                    statement="new",
                    options=["A", "B"],
                    correct_option="A",
                    created_by_external_identity="prof-1",
                )

    async def test_published_question_is_immutable(self):
        async with self.factory() as session:
            service, result = await self._seed_question(session)
            question = await session.get(Question, result.question_id)
            question.validation_status = QuestionWorkflowStatus.PUBLISHED.value
            await session.flush()
            with self.assertRaisesRegex(ValueError, "immutable"):
                await service.create_new_version(
                    question_id=result.question_id,
                    statement="revised",
                    options=["A", "B"],
                    correct_option="A",
                    created_by_external_identity="prof-1",
                )

    async def test_correct_option_must_exist_in_new_options(self):
        async with self.factory() as session:
            service, result = await self._seed_question(session)
            with self.assertRaisesRegex(ValueError, "corrected answer must exist"):
                await service.create_new_version(
                    question_id=result.question_id,
                    statement="revised",
                    options=["A", "B"],
                    correct_option="Z",
                    created_by_external_identity="prof-1",
                )

    async def test_creates_a_new_version_linked_to_the_parent_and_resets_status_to_draft(self):
        async with self.factory() as session:
            service, result = await self._seed_question(session)
            # move the question forward so we can prove create_new_version resets it
            await service.submit_for_review(result.question_id)
            new_result = await service.create_new_version(
                question_id=result.question_id,
                statement="Revised statement",
                options=["X", "Y", "Z"],
                correct_option="Y",
                created_by_external_identity="prof-2",
                reason="typo fix",
            )
            self.assertNotEqual(new_result.version_id, result.version_id)
            self.assertEqual(new_result.status, QuestionWorkflowStatus.DRAFT.value)

            new_version = await service.get_current_version(result.question_id)
            self.assertEqual(new_version.id, new_result.version_id)
            self.assertEqual(new_version.parent_version_id, result.version_id)
            self.assertEqual(new_version.change_reason, "typo fix")
            self.assertEqual(new_version.metadata_["parent_version_id"], str(result.version_id))
            new_options = (
                await session.scalars(
                    select(QuestionOption).where(QuestionOption.question_version_id == new_version.id)
                )
            ).all()
            options_by_text = {o.text: o.is_valid_option for o in new_options}
            self.assertEqual(options_by_text, {"X": False, "Y": True, "Z": False})


# --------------------------------------------------------------------------- #
# submit_for_review / approve / reject - "not found" branches unreachable via
# the HTTP route (it 404s earlier) but part of the service's own contract.
# --------------------------------------------------------------------------- #


class NotFoundBranchesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_submit_for_review_not_found(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "Question not found"):
                await service.submit_for_review(uuid.uuid4())

    async def test_submit_for_review_rejects_invalid_source_status(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            result = await service.create_question(
                created_by_external_identity="prof-1",
                statement="s",
                options=["A", "B"],
                correct_option="A",
            )
            await service.submit_for_review(result.question_id)
            await service.approve(result.question_id)
            # APPROVED is neither DRAFT nor REJECTED
            with self.assertRaisesRegex(ValueError, "Only draft or rejected"):
                await service.submit_for_review(result.question_id)

    async def test_approve_not_found(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "Question not found"):
                await service.approve(uuid.uuid4())

    async def test_reject_not_found(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "Question not found"):
                await service.reject(uuid.uuid4())

    async def test_reject_rejects_invalid_source_status(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            result = await service.create_question(
                created_by_external_identity="prof-1",
                statement="s",
                options=["A", "B"],
                correct_option="A",
            )
            # still DRAFT - reject() only accepts PENDING_REVIEW
            with self.assertRaisesRegex(ValueError, "Only questions under review"):
                await service.reject(result.question_id)

    async def test_archive_not_found(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "Question not found"):
                await service.archive_question(uuid.uuid4())


# --------------------------------------------------------------------------- #
# QuestionAuthoringService.suggest_classification
# --------------------------------------------------------------------------- #


class SuggestClassificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_not_found_raises(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "Question not found"):
                await service.suggest_classification(
                    question_id=uuid.uuid4(),
                    discipline="CHEMISTRY",
                    content="KINETICS",
                    subcontent="RATE_LAW",
                    difficulty="easy",
                )

    async def test_high_confidence_is_marked_classified(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            result = await service.create_question(
                created_by_external_identity="prof-1",
                statement="s",
                options=["A", "B"],
                correct_option="A",
            )
            classification = await service.suggest_classification(
                question_id=result.question_id,
                discipline="CHEMISTRY",
                content="KINETICS",
                subcontent="RATE_LAW",
                difficulty="easy",
                confidence=0.9,
            )
            self.assertEqual(classification.status, "CLASSIFIED")
            self.assertEqual(classification.difficulty, "EASY")
            self.assertEqual(classification.question_version_id, result.version_id)
            self.assertEqual(classification.keywords, ["CHEMISTRY", "KINETICS", "RATE_LAW"])
            self.assertEqual(classification.metadata_["suggested_for_question"], str(result.question_id))

    async def test_low_confidence_needs_review(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            result = await service.create_question(
                created_by_external_identity="prof-1",
                statement="s",
                options=["A", "B"],
                correct_option="A",
            )
            classification = await service.suggest_classification(
                question_id=result.question_id,
                discipline="CHEMISTRY",
                content="KINETICS",
                subcontent="RATE_LAW",
                difficulty="hard",
                confidence=0.4,
            )
            self.assertEqual(classification.status, "NEEDS_REVIEW")


# --------------------------------------------------------------------------- #
# QuestionAuthoringService.publish_question - the "not found" / "not
# approved" / "no usable classification" guard branches. The success path is
# already covered by
# test_pedagogical_classification_lifecycle.py::ConsumerCompatibilityTests.
# --------------------------------------------------------------------------- #


class PublishQuestionGuardTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_not_found_raises(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "Question not found"):
                await service.publish_question(uuid.uuid4())

    async def test_requires_approved_status(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            result = await service.create_question(
                created_by_external_identity="prof-1",
                statement="s",
                options=["A", "B"],
                correct_option="A",
            )
            # still DRAFT
            with self.assertRaisesRegex(ValueError, "Only approved questions"):
                await service.publish_question(result.question_id)

    async def test_requires_a_classified_active_classification(self):
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            result = await service.create_question(
                created_by_external_identity="prof-1",
                statement="s",
                options=["A", "B"],
                correct_option="A",
            )
            await service.submit_for_review(result.question_id)
            await service.approve(result.question_id)
            # no classification at all yet
            with self.assertRaisesRegex(ValueError, "reviewed classification"):
                await service.publish_question(result.question_id)

    async def test_classification_with_null_confidence_blocks_publication(self):
        # classification_confidence is nullable in the schema, so a
        # CLASSIFIED/ACTIVE row with no confidence recorded is a real,
        # schema-permitted state (not a fabricated edge case) and must
        # still block publication rather than raising a TypeError from
        # float(None).
        async with self.factory() as session:
            service = QuestionAuthoringService(session)
            result = await service.create_question(
                created_by_external_identity="prof-1",
                statement="s",
                options=["A", "B"],
                correct_option="A",
            )
            await service.submit_for_review(result.question_id)
            await service.approve(result.question_id)
            session.add(
                PedagogicalClassification(
                    question_version_id=result.version_id,
                    discipline="CHEMISTRY",
                    content="KINETICS",
                    subcontent="RATE_LAW",
                    difficulty="EASY",
                    classification_confidence=None,
                    reasoning_type="application",
                    status="CLASSIFIED",
                    source="ai",
                    lifecycle="ACTIVE",
                )
            )
            await session.flush()
            with self.assertRaisesRegex(ValueError, "reviewed classification"):
                await service.publish_question(result.question_id)


# --------------------------------------------------------------------------- #
# MaterialAuthoringService - no caller anywhere in src/, only reachable
# through direct service-level tests.
# --------------------------------------------------------------------------- #


class MaterialGetCurrentVersionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_raises_when_material_has_no_version(self):
        async with self.factory() as session:
            material = TheoryMaterial(title="Empty material")
            session.add(material)
            await session.flush()
            service = MaterialAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "does not have a version"):
                await service.get_current_version(material.id)


class CreateMaterialAndVersionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_create_material_also_creates_a_first_draft_version(self):
        async with self.factory() as session:
            service = MaterialAuthoringService(session)
            material = await service.create_material(
                title="Cinetica Quimica",
                created_by_external_identity="prof-1",
                visibility_scope="SCHOOL",
                origin_type="AUTHOR",
            )
            self.assertEqual(material.title, "Cinetica Quimica")
            version = await service.get_current_version(material.id)
            self.assertEqual(version.version_number, 1)
            self.assertEqual(version.status, MaterialWorkflowStatus.DRAFT.value)

    async def test_create_version_increments_version_number_from_latest(self):
        async with self.factory() as session:
            service = MaterialAuthoringService(session)
            material = await service.create_material(
                title="Cinetica Quimica",
                created_by_external_identity="prof-1",
            )
            second = await service.create_version(
                material_id=material.id,
                created_by_external_identity="prof-1",
                introduction="intro",
                summary="resumo",
            )
            self.assertEqual(second.version_number, 2)
            self.assertEqual(second.introduction, "intro")
            self.assertEqual(second.summary, "resumo")
            current = await service.get_current_version(material.id)
            self.assertEqual(current.id, second.id)


class MaterialSubmitApproveRejectTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_version(self, session):
        service = MaterialAuthoringService(session)
        material = await service.create_material(
            title="Material", created_by_external_identity="prof-1"
        )
        version = await service.get_current_version(material.id)
        return service, version

    async def test_submit_for_review_not_found(self):
        async with self.factory() as session:
            service = MaterialAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "Material version not found"):
                await service.submit_for_review(uuid.uuid4())

    async def test_submit_for_review_rejects_invalid_status(self):
        async with self.factory() as session:
            service, version = await self._seed_version(session)
            version.status = MaterialWorkflowStatus.APPROVED.value
            await session.flush()
            with self.assertRaisesRegex(ValueError, "Only draft or rejected"):
                await service.submit_for_review(version.id)

    async def test_submit_for_review_accepts_draft_and_rejected(self):
        async with self.factory() as session:
            service, version = await self._seed_version(session)
            submitted = await service.submit_for_review(version.id)
            self.assertEqual(submitted.status, MaterialWorkflowStatus.PENDING_REVIEW.value)

            submitted.status = MaterialWorkflowStatus.REJECTED.value
            await session.flush()
            resubmitted = await service.submit_for_review(version.id)
            self.assertEqual(resubmitted.status, MaterialWorkflowStatus.PENDING_REVIEW.value)

    async def test_approve_material_not_found(self):
        async with self.factory() as session:
            service = MaterialAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "Material version not found"):
                await service.approve_material(uuid.uuid4())

    async def test_approve_material_rejects_invalid_status(self):
        async with self.factory() as session:
            service, version = await self._seed_version(session)
            # still DRAFT
            with self.assertRaisesRegex(ValueError, "Only materials under review"):
                await service.approve_material(version.id)

    async def test_approve_material_success(self):
        async with self.factory() as session:
            service, version = await self._seed_version(session)
            await service.submit_for_review(version.id)
            approved = await service.approve_material(version.id)
            self.assertEqual(approved.status, MaterialWorkflowStatus.APPROVED.value)

    async def test_reject_material_not_found(self):
        async with self.factory() as session:
            service = MaterialAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "Material version not found"):
                await service.reject_material(uuid.uuid4())

    async def test_reject_material_rejects_invalid_status(self):
        async with self.factory() as session:
            service, version = await self._seed_version(session)
            with self.assertRaisesRegex(ValueError, "Only materials under review"):
                await service.reject_material(version.id, reason="incompleto")

    async def test_reject_material_appends_reason_to_existing_summary(self):
        async with self.factory() as session:
            service, version = await self._seed_version(session)
            version.summary = "Resumo original"
            await session.flush()
            await service.submit_for_review(version.id)
            rejected = await service.reject_material(version.id, reason="faltam referencias")
            self.assertEqual(rejected.status, MaterialWorkflowStatus.REJECTED.value)
            self.assertEqual(
                rejected.summary, "Resumo original\nRejeição: faltam referencias"
            )

    async def test_reject_material_sets_summary_when_none_existed(self):
        async with self.factory() as session:
            service, version = await self._seed_version(session)
            await service.submit_for_review(version.id)
            rejected = await service.reject_material(version.id, reason="incompleto")
            self.assertEqual(rejected.summary, "Rejeição: incompleto")

    async def test_reject_material_without_reason_leaves_summary_untouched(self):
        async with self.factory() as session:
            service, version = await self._seed_version(session)
            await service.submit_for_review(version.id)
            rejected = await service.reject_material(version.id)
            self.assertIsNone(rejected.summary)


class MaterialPublishArchiveTests(unittest.IsolatedAsyncioTestCase):
    # NOTE: archive_material()'s final `raise ValueError("This material
    # version cannot be archived in its current state.")` (the branch after
    # the ARCHIVED / PUBLISHED / {DRAFT, PENDING_REVIEW, APPROVED, REJECTED}
    # checks) is intentionally NOT exercised here. MaterialWorkflowStatus
    # only has 6 members and the DB's own CheckConstraint
    # (ck_theory_material_versions_status) rejects any other value at
    # flush/commit time, so every value the three preceding branches don't
    # already handle is unreachable through legitimate use of this service -
    # reaching it would require writing an invalid status to the row while
    # bypassing the ORM's own commit path, which isn't a real trigger.
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _approved_version(self, session):
        service = MaterialAuthoringService(session)
        material = await service.create_material(title="M", created_by_external_identity="prof-1")
        version = await service.get_current_version(material.id)
        await service.submit_for_review(version.id)
        await service.approve_material(version.id)
        return service, version

    async def test_publish_material_not_found(self):
        async with self.factory() as session:
            service = MaterialAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "Material version not found"):
                await service.publish_material(uuid.uuid4())

    async def test_publish_material_requires_approved_status(self):
        async with self.factory() as session:
            service = MaterialAuthoringService(session)
            material = await service.create_material(title="M", created_by_external_identity="prof-1")
            version = await service.get_current_version(material.id)
            # still DRAFT
            with self.assertRaisesRegex(ValueError, "Only approved materials"):
                await service.publish_material(version.id)

    async def test_publish_material_already_published_raises(self):
        async with self.factory() as session:
            service, version = await self._approved_version(session)
            published = await service.publish_material(version.id)
            self.assertEqual(published.status, MaterialWorkflowStatus.PUBLISHED.value)
            self.assertIsNotNone(published.published_at)
            with self.assertRaisesRegex(ValueError, "already published"):
                await service.publish_material(version.id)

    async def test_archive_material_not_found(self):
        async with self.factory() as session:
            service = MaterialAuthoringService(session)
            with self.assertRaisesRegex(ValueError, "Material version not found"):
                await service.archive_material(uuid.uuid4())

    async def test_archive_material_already_archived_raises(self):
        async with self.factory() as session:
            service, version = await self._approved_version(session)
            first = await service.archive_material(version.id)
            self.assertEqual(first.status, MaterialWorkflowStatus.ARCHIVED.value)
            with self.assertRaisesRegex(ValueError, "already archived"):
                await service.archive_material(version.id)

    async def test_archive_material_from_published(self):
        async with self.factory() as session:
            service, version = await self._approved_version(session)
            await service.publish_material(version.id)
            archived = await service.archive_material(version.id)
            self.assertEqual(archived.status, MaterialWorkflowStatus.ARCHIVED.value)

    async def test_archive_material_from_draft(self):
        async with self.factory() as session:
            service = MaterialAuthoringService(session)
            material = await service.create_material(title="M", created_by_external_identity="prof-1")
            version = await service.get_current_version(material.id)
            archived = await service.archive_material(version.id)
            self.assertEqual(archived.status, MaterialWorkflowStatus.ARCHIVED.value)


if __name__ == "__main__":
    unittest.main()
