"""
Coverage sweep for src/agente_ia_edu/services/assessments.py (AssessmentPersistenceService
and ExerciseListPersistenceService, plus the pure-Python domain/factory layer at the top
of the module).

This targets the 69 statements the file was missing at 86% coverage: not-found/invalid-state
branches in the persistence services that no existing test exercised directly, and the
legacy in-memory dataclass/factory layer (ExerciseList.items/add_item, ExerciseListFactory,
AssessmentFactory.create_version's publish path, AssessmentPublicationService.build_publication's
validation branches, AssessmentService.start_attempt's attempts-exceeded guard, and
_compute_expires_at's ends_at-earlier-than-time-limit branch) that had zero coverage at all.

DB-backed tests use `async_sessionmaker(engine, class_=AsyncSession)` with no
`expire_on_commit` override, matching production's `create_session_factory()`
(see src/agente_ia_edu/db/session.py) per the campaign's MissingGreenlet-after-commit
concern - even though none of the methods under test here call session.commit()
themselves (they only flush(), leaving commit to the HTTP route layer).

Real bug found and fixed (see services/assessments.py `assign_list`): the school-id
mismatch check compared `str(assessment.school_id)` (canonical lowercase UUID string)
against the raw, un-normalized `str(school_id)` argument. A caller passing the *same*
school UUID with different casing (e.g. uppercase) was wrongly rejected with a
PermissionError. A second check further down did normalize correctly via uuid.UUID(),
but was provably unreachable (the first, buggy check already raises for any real
mismatch before the second one runs). Fixed by normalizing once via uuid.UUID() before
the (now single) comparison, which also removes the dead code.
"""

import unittest
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.assessments import (
    AssessmentVersion as AssessmentVersionModel,
)
from agente_ia_edu.db.models.official import QuestionVersion
from agente_ia_edu.services.assessments import (
    AssessmentFactory,
    AssessmentItem,
    AssessmentPersistenceService,
    AssessmentPublication,
    AssessmentPublicationService,
    AssessmentService,
    AssessmentVersion,
    ExerciseList,
    ExerciseListFactory,
    ExerciseListPersistenceService,
)


class _AsyncDBTestCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # No expire_on_commit override: mirrors production's create_session_factory().
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()


class AssessmentPersistenceServiceGapsTests(_AsyncDBTestCase):
    """Base-class AssessmentPersistenceService branches no existing test hit."""

    async def test_get_assessment_returns_none_for_missing_id(self) -> None:
        async with self.session_factory() as session:
            service = AssessmentPersistenceService(session)
            result = await service.get_assessment(uuid.uuid4())
            self.assertIsNone(result)

    async def test_create_version_raises_when_assessment_missing(self) -> None:
        async with self.session_factory() as session:
            service = AssessmentPersistenceService(session)
            with self.assertRaises(ValueError) as ctx:
                await service.create_version(
                    assessment_id=uuid.uuid4(),
                    version_number=1,
                    title="V1",
                )
            self.assertIn("does not exist", str(ctx.exception))

    async def test_add_item_raises_when_version_missing(self) -> None:
        async with self.session_factory() as session:
            service = AssessmentPersistenceService(session)
            with self.assertRaises(ValueError) as ctx:
                await service.add_item(
                    assessment_version_id=uuid.uuid4(),
                    question_version_id=uuid.uuid4(),
                    position=1,
                )
            self.assertIn("does not exist", str(ctx.exception))

    async def test_add_item_raises_when_version_published(self) -> None:
        async with self.session_factory() as session:
            service = AssessmentPersistenceService(session)
            assessment = await service.create_assessment(title="A1")
            version = await service.create_version(
                assessment_id=assessment.id, version_number=1, title="V1"
            )
            # Flip the version to published directly on the model - the service
            # itself never exposes a "publish a bare AssessmentVersion" op.
            version_model = await session.get(AssessmentVersionModel, version.id)
            version_model.status = "published"
            await session.flush()

            with self.assertRaises(ValueError) as ctx:
                await service.add_item(
                    assessment_version_id=version.id,
                    question_version_id=uuid.uuid4(),
                    position=1,
                )
            self.assertIn("Published versions cannot receive new items", str(ctx.exception))

    async def test_add_item_raises_on_duplicate_position(self) -> None:
        async with self.session_factory() as session:
            service = AssessmentPersistenceService(session)
            assessment = await service.create_assessment(title="A1")
            version = await service.create_version(
                assessment_id=assessment.id, version_number=1, title="V1"
            )
            await service.add_item(
                assessment_version_id=version.id,
                question_version_id=uuid.uuid4(),
                position=1,
            )
            with self.assertRaises(ValueError) as ctx:
                await service.add_item(
                    assessment_version_id=version.id,
                    question_version_id=uuid.uuid4(),
                    position=1,
                )
            self.assertIn("must be unique", str(ctx.exception))

    async def test_create_publication_raises_when_version_missing(self) -> None:
        async with self.session_factory() as session:
            service = AssessmentPersistenceService(session)
            with self.assertRaises(ValueError) as ctx:
                await service.create_publication(
                    assessment_version_id=uuid.uuid4(),
                    publication_type="immediate",
                )
            self.assertIn("does not exist", str(ctx.exception))

    async def test_list_publications_returns_them_newest_first(self) -> None:
        async with self.session_factory() as session:
            service = AssessmentPersistenceService(session)
            assessment = await service.create_assessment(title="A1")
            version = await service.create_version(
                assessment_id=assessment.id, version_number=1, title="V1"
            )
            first = await service.create_publication(
                assessment_version_id=version.id, publication_type="scheduled"
            )
            second = await service.create_publication(
                assessment_version_id=version.id, publication_type="immediate"
            )

            publications = await service.list_publications(assessment.id)

            self.assertEqual(len(publications), 2)
            ids = {p.id for p in publications}
            self.assertEqual(ids, {first.id, second.id})
            # created_at desc: the more-recently-created (second) publication
            # comes first, unless the two flushes landed in the same
            # microsecond tick (SQLite/aiosqlite resolution), in which case
            # ordering is implementation-defined but both rows must still be
            # present - already asserted above.
            if publications[0].created_at != publications[1].created_at:
                self.assertEqual(publications[0].id, second.id)


class ExerciseListPersistenceServiceGapsTests(_AsyncDBTestCase):
    """ExerciseListPersistenceService branches no existing test hit."""

    async def test_assign_list_raises_when_list_missing(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            with self.assertRaises(ValueError) as ctx:
                await service.assign_list(
                    list_id=uuid.uuid4(),
                    recipient_type="STUDENT",
                    recipient_id="student-1",
                )
            self.assertIn("not found", str(ctx.exception))

    async def test_assign_list_rejects_unsupported_recipient_type(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista", created_by_external_identity="teacher-a"
            )
            with self.assertRaises(ValueError) as ctx:
                await service.assign_list(
                    list_id=list_obj.id,
                    recipient_type="PLANET",
                    recipient_id="mars",
                )
            self.assertIn("Unsupported assignment recipient type", str(ctx.exception))

    async def test_assign_list_accepts_same_school_id_regardless_of_case(self) -> None:
        """Regression test for the case-sensitivity bug fixed in assign_list.

        Before the fix, passing the same school UUID with different casing than
        the canonical form stored on the model raised a false PermissionError -
        the raw-string comparison ran before the value was normalized through
        uuid.UUID(). See the module-level docstring above for the full story.
        """
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            school_id = str(uuid.uuid4())
            list_obj = await service.create_list(
                title="Lista",
                school_id=school_id,
                created_by_external_identity="teacher-a",
            )

            assignment = await service.assign_list(
                list_id=list_obj.id,
                recipient_type="STUDENT",
                recipient_id="student-1",
                school_id=school_id.upper(),
                assigned_by_external_id="teacher-a",
            )

            self.assertEqual(assignment["school_id"], school_id)

    async def test_assign_list_updates_existing_assignment_idempotently(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            school_id = str(uuid.uuid4())
            list_obj = await service.create_list(
                title="Lista",
                school_id=school_id,
                created_by_external_identity="teacher-a",
            )

            first = await service.assign_list(
                list_id=list_obj.id,
                recipient_type="STUDENT",
                recipient_id="student-1",
                school_id=school_id,
                assigned_by_external_id="teacher-a",
            )
            second = await service.assign_list(
                list_id=list_obj.id,
                recipient_type="STUDENT",
                recipient_id="student-1",
                school_id=school_id,
                assigned_by_external_id="teacher-b",
            )

            # Same underlying row (re-assignment, not a duplicate), with the
            # assigning teacher updated to the second call's value.
            self.assertEqual(first["id"], second["id"])
            self.assertEqual(second["assigned_by_external_id"], "teacher-b")

    async def test_mark_assignment_complete_raises_when_assignment_missing(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista", created_by_external_identity="teacher-a"
            )
            with self.assertRaises(ValueError) as ctx:
                await service.mark_assignment_complete(
                    list_id=list_obj.id,
                    recipient_type="STUDENT",
                    recipient_id="ghost-student",
                )
            self.assertIn("not found", str(ctx.exception))

    async def test_update_list_raises_when_list_missing(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            with self.assertRaises(ValueError) as ctx:
                await service.update_list(uuid.uuid4(), title="new title")
            self.assertIn("not found", str(ctx.exception))

    async def test_update_list_converts_school_id_field_to_uuid(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista", created_by_external_identity="teacher-a"
            )
            new_school_id = str(uuid.uuid4())

            updated = await service.update_list(list_obj.id, school_id=new_school_id)

            self.assertEqual(updated.school_id, new_school_id)

    async def test_get_list_raises_when_list_missing(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            with self.assertRaises(ValueError) as ctx:
                await service.get_list(uuid.uuid4())
            self.assertIn("not found", str(ctx.exception))

    async def test_add_item_raises_when_list_has_no_versions(self) -> None:
        """Assessment and AssessmentVersion are separate tables with no DB
        constraint forcing every assessment to have a version - create_list()
        always creates one, but add_item() must still defend against an
        assessment row that exists without any version (e.g. a partially
        provisioned row, or a future creation path that skips it)."""
        async with self.session_factory() as session:
            from agente_ia_edu.db.models.assessments import Assessment as AssessmentModel

            service = ExerciseListPersistenceService(session)
            bare_assessment = AssessmentModel(
                title="Lista sem versão",
                status="draft",
                visibility_scope="SCHOOL",
                origin_type="SCHOOL",
            )
            session.add(bare_assessment)
            await session.flush()

            with self.assertRaises(ValueError) as ctx:
                await service.add_item(
                    bare_assessment.id, question_version_id=uuid.uuid4(), position=1
                )
            self.assertIn("no versions", str(ctx.exception))

    async def test_add_item_raises_on_published_list(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista", created_by_external_identity="teacher-a"
            )
            await service.add_item(list_obj.id, question_version_id=uuid.uuid4(), position=1)
            await service.submit_review(list_obj.id, performed_by_external_id="teacher-a")
            await service.approve(list_obj.id, performed_by_external_id="coord-a")
            await service.publish(list_obj.id, performed_by_external_id="coord-a")

            with self.assertRaises(ValueError) as ctx:
                await service.add_item(
                    list_obj.id, question_version_id=uuid.uuid4(), position=2
                )
            self.assertIn("Published exercise list cannot receive new items", str(ctx.exception))

    async def test_add_item_raises_when_question_version_is_orphaned(self) -> None:
        """A QuestionVersion row whose question_id points nowhere (FK not enforced
        by SQLite without a PRAGMA) must be rejected, not silently accepted."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista", created_by_external_identity="teacher-a"
            )
            orphan_version = QuestionVersion(
                id=uuid.uuid4(),
                question_id=uuid.uuid4(),  # no matching Question row
                version_kind="official_original",
                canonical_text="Orphan",
                content_hash=str(uuid.uuid4()),
            )
            session.add(orphan_version)
            await session.flush()

            with self.assertRaises(ValueError) as ctx:
                await service.add_item(
                    list_obj.id, question_version_id=orphan_version.id, position=1
                )
            self.assertIn("not found", str(ctx.exception))

    async def test_remove_item_raises_when_item_missing(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista", created_by_external_identity="teacher-a"
            )
            with self.assertRaises(ValueError) as ctx:
                await service.remove_item(list_obj.id, uuid.uuid4())
            self.assertIn("not found", str(ctx.exception))

    async def test_remove_item_raises_when_item_belongs_to_different_list(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_a = await service.create_list(
                title="Lista A", created_by_external_identity="teacher-a"
            )
            list_b = await service.create_list(
                title="Lista B", created_by_external_identity="teacher-a"
            )
            item = await service.add_item(
                list_a.id, question_version_id=uuid.uuid4(), position=1
            )

            with self.assertRaises(ValueError) as ctx:
                await service.remove_item(list_b.id, item["id"])
            self.assertIn("does not belong", str(ctx.exception))

    async def test_remove_item_raises_on_published_list(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista", created_by_external_identity="teacher-a"
            )
            item = await service.add_item(
                list_obj.id, question_version_id=uuid.uuid4(), position=1
            )
            await service.submit_review(list_obj.id, performed_by_external_id="teacher-a")
            await service.approve(list_obj.id, performed_by_external_id="coord-a")
            await service.publish(list_obj.id, performed_by_external_id="coord-a")

            with self.assertRaises(ValueError) as ctx:
                await service.remove_item(list_obj.id, item["id"])
            self.assertIn("Published exercise list cannot be modified", str(ctx.exception))

    async def test_submit_review_raises_when_list_missing(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            with self.assertRaises(ValueError) as ctx:
                await service.submit_review(uuid.uuid4())
            self.assertIn("not found", str(ctx.exception))

    async def test_approve_raises_when_list_missing(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            with self.assertRaises(ValueError) as ctx:
                await service.approve(uuid.uuid4())
            self.assertIn("not found", str(ctx.exception))

    async def test_approve_raises_when_list_not_under_review(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista", created_by_external_identity="teacher-a"
            )
            with self.assertRaises(ValueError) as ctx:
                await service.approve(list_obj.id, performed_by_external_id="coord-a")
            self.assertIn("Only lists under review can be approved", str(ctx.exception))

    async def test_reject_raises_when_list_missing(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            with self.assertRaises(ValueError) as ctx:
                await service.reject(uuid.uuid4())
            self.assertIn("not found", str(ctx.exception))

    async def test_reject_raises_when_list_not_under_review(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista", created_by_external_identity="teacher-a"
            )
            with self.assertRaises(ValueError) as ctx:
                await service.reject(list_obj.id, performed_by_external_id="coord-a")
            self.assertIn("Only lists under review can be rejected", str(ctx.exception))

    async def test_reject_moves_list_and_version_to_rejected_and_records_audit(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista", created_by_external_identity="teacher-a"
            )
            await service.submit_review(list_obj.id, performed_by_external_id="teacher-a")

            rejected = await service.reject(
                list_obj.id,
                reason="Faltam questões",
                performed_by_external_id="coord-a",
            )

            self.assertEqual(rejected.status, "rejected")
            reloaded = await service.get_list(list_obj.id)
            self.assertEqual(reloaded["status"], "rejected")

            audit_rows = await service.list_workflow_audit(list_obj.id)
            self.assertTrue(any(row["action"] == "LIST_REJECTED" for row in audit_rows))
            rejected_row = next(row for row in audit_rows if row["action"] == "LIST_REJECTED")
            self.assertEqual(rejected_row["reason"], "Faltam questões")
            self.assertEqual(rejected_row["previous_status"], "review")
            self.assertEqual(rejected_row["new_status"], "rejected")

            # The version itself moves to "archived" (not "rejected") on reject -
            # verify directly against the model since get_list() doesn't surface
            # version status.
            version_model = await session.scalar(
                select(AssessmentVersionModel).where(
                    AssessmentVersionModel.assessment_id == list_obj.id
                )
            )
            self.assertEqual(version_model.status, "archived")

    async def test_reject_allows_resubmission_after_fix(self) -> None:
        """A rejected list can be submitted for review again (draft-or-rejected gate)."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista", created_by_external_identity="teacher-a"
            )
            await service.submit_review(list_obj.id, performed_by_external_id="teacher-a")
            await service.reject(list_obj.id, performed_by_external_id="coord-a")

            resubmitted = await service.submit_review(
                list_obj.id, performed_by_external_id="teacher-a"
            )
            self.assertEqual(resubmitted.status, "review")

    async def test_publish_raises_when_list_missing(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            with self.assertRaises(ValueError) as ctx:
                await service.publish(uuid.uuid4())
            self.assertIn("not found", str(ctx.exception))

    async def test_archive_raises_when_list_missing(self) -> None:
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            with self.assertRaises(ValueError) as ctx:
                await service.archive(uuid.uuid4())
            self.assertIn("not found", str(ctx.exception))


class ExerciseListDomainLayerTests(unittest.TestCase):
    """Pure in-memory dataclass/factory layer (no DB): ExerciseList.items/add_item
    and ExerciseListFactory, which had zero coverage - nothing in the codebase
    currently exercises them outside AssessmentFactory's parallel (DB-agnostic)
    path, but they are public API of this module and reachable by any caller."""

    def test_items_property_returns_empty_list_when_no_versions(self) -> None:
        exercise_list = ExerciseList(title="Sem versão")
        self.assertEqual(exercise_list.items, [])

    def test_items_property_delegates_to_first_version(self) -> None:
        list_obj = ExerciseListFactory().create_list(
            title="Lista", created_by_external_identity="teacher-a"
        )
        question_id = uuid.uuid4()
        added = list_obj.add_item(question_version_id=question_id, position=1)

        self.assertEqual(list_obj.items, [added])

    def test_add_item_raises_without_a_version(self) -> None:
        exercise_list = ExerciseList(title="Sem versão")
        with self.assertRaises(ValueError) as ctx:
            exercise_list.add_item(question_version_id=uuid.uuid4(), position=1)
        self.assertIn("requires at least one version", str(ctx.exception))

    def test_assessment_item_is_immutable_after_creation(self) -> None:
        item = AssessmentItem(question_version_id=uuid.uuid4(), position=1, points=2)
        with self.assertRaises(ValueError):
            item.points = 99
        with self.assertRaises(ValueError):
            item.position = 2
        with self.assertRaises(ValueError):
            item.question_version_id = uuid.uuid4()

    def test_exercise_list_factory_create_list_and_add_item(self) -> None:
        factory = ExerciseListFactory()
        list_obj = factory.create_list(
            title="Lista via factory", created_by_external_identity="teacher-a"
        )
        question_id = uuid.uuid4()

        item = factory.add_item(list_obj, question_version_id=question_id, position=1, points=4)

        self.assertEqual(item.question_version_id, question_id)
        self.assertEqual(list_obj.versions[0].items, [item])

    def test_exercise_list_factory_add_item_raises_without_version(self) -> None:
        factory = ExerciseListFactory()
        bare_list = ExerciseList(title="Sem versão")
        with self.assertRaises(ValueError) as ctx:
            factory.add_item(bare_list, question_version_id=uuid.uuid4(), position=1)
        self.assertIn("requires at least one version", str(ctx.exception))

    def test_exercise_list_factory_create_version_published_publishes_immediately(self) -> None:
        factory = ExerciseListFactory()
        list_obj = factory.create_list(
            title="Lista", created_by_external_identity="teacher-a"
        )
        version = factory.create_version(
            assessment=list_obj,
            version_number=2,
            title="V2",
            status="published",
        )
        self.assertEqual(version.status, "published")
        self.assertIsNotNone(version.published_at)

    def test_exercise_list_factory_publish_builds_publication(self) -> None:
        factory = ExerciseListFactory()
        list_obj = factory.create_list(
            title="Lista", created_by_external_identity="teacher-a"
        )
        factory.add_item(list_obj, question_version_id=uuid.uuid4(), position=1)

        publication = factory.publish(
            list_obj,
            publication_type="immediate",
            released_immediately=True,
            time_limit_seconds=600,
        )

        self.assertEqual(publication.publication_type, "immediate")
        self.assertTrue(publication.released_immediately)
        self.assertEqual(list_obj.versions[0].publications, [publication])


class AssessmentFactoryAndPublicationDomainTests(unittest.TestCase):
    """AssessmentFactory.create_version's publish path and
    AssessmentPublicationService.build_publication's validation branches -
    both pure in-memory, no DB required."""

    def test_create_version_with_published_status_publishes_immediately(self) -> None:
        factory = AssessmentFactory()
        assessment = factory.create_assessment(title="A1")

        version = factory.create_version(
            assessment=assessment,
            version_number=2,
            title="V2",
            status="published",
        )

        self.assertEqual(version.status, "published")
        self.assertIsNotNone(version.published_at)

    def test_build_publication_rejects_unsupported_type(self) -> None:
        version = AssessmentVersion(title="V1")
        with self.assertRaises(ValueError) as ctx:
            AssessmentPublicationService.build_publication(
                assessment_version=version, publication_type="carrier_pigeon"
            )
        self.assertIn("Unsupported publication type", str(ctx.exception))

    def test_build_publication_rejects_ends_before_starts(self) -> None:
        version = AssessmentVersion(title="V1")
        starts_at = datetime(2026, 1, 1, tzinfo=timezone.utc)
        ends_at = starts_at - timedelta(hours=1)
        with self.assertRaises(ValueError) as ctx:
            AssessmentPublicationService.build_publication(
                assessment_version=version,
                publication_type="scheduled",
                starts_at=starts_at,
                ends_at=ends_at,
            )
        self.assertIn("Publication end time must be after start time", str(ctx.exception))


class AssessmentServiceDomainTests(unittest.TestCase):
    """AssessmentService branches with zero coverage: the attempts-exceeded
    guard in start_attempt, and _compute_expires_at's branch where ends_at is
    earlier than the time-limit-computed deadline."""

    def test_start_attempt_raises_when_attempt_number_exceeds_allowed(self) -> None:
        publication = AssessmentPublication(attempts_allowed=1)
        with self.assertRaises(ValueError) as ctx:
            AssessmentService.start_attempt(
                publication=publication,
                external_identity_id="ext-student-1",
                attempt_number=2,
            )
        self.assertIn("Attempt number exceeds attempts allowed", str(ctx.exception))

    def test_expires_at_uses_ends_at_when_earlier_than_time_limit_deadline(self) -> None:
        started_at = datetime(2026, 1, 1, 8, 0, tzinfo=timezone.utc)
        ends_at = started_at + timedelta(minutes=30)
        publication = AssessmentPublication(
            time_limit_seconds=7200,  # 2h - would push the deadline past ends_at
            ends_at=ends_at,
        )

        attempt = AssessmentService.start_attempt(
            publication=publication,
            external_identity_id="ext-student-1",
            attempt_number=1,
            started_at=started_at,
        )

        self.assertEqual(attempt.expires_at, ends_at)


if __name__ == "__main__":
    unittest.main()
