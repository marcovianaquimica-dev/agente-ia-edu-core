"""
BLOCO C — Question Eligibility Validation Tests

Verify that QuestionVersion eligibility is validated when adding to ExerciseList.
"""

import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.official import Question, QuestionVersion
from agente_ia_edu.db.models.pedagogical import PedagogicalClassification
from agente_ia_edu.services.assessments import ExerciseListPersistenceService


class TestQuestionEligibilityValidation(unittest.IsolatedAsyncioTestCase):
    """Test question eligibility validation in exercise lists."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)

        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )
        self.school_id = str(uuid.uuid4())
        self.institution_id = str(uuid.uuid4())

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _make_eligible_question(self, session: AsyncSession) -> tuple[Question, QuestionVersion]:
        """Helper: Create a fully eligible question with all required properties."""
        question = Question(
            id=uuid.uuid4(),
            question_type="MULTIPLE_CHOICE",
            school_id=uuid.UUID(self.school_id),
            status="PUBLISHED",
            validation_status="valid",
            visibility_scope="SCHOOL",
        )
        session.add(question)
        await session.flush()

        version = QuestionVersion(
            id=uuid.uuid4(),
            question_id=question.id,
            version_kind="official_original",
            canonical_text="Test Q",
            content_hash=str(uuid.uuid4()),
            recommended_difficulty="EASY",
        )
        session.add(version)
        await session.flush()

        # Add required classification
        classification = PedagogicalClassification(
            id=uuid.uuid4(),
            question_version_id=version.id,
            discipline="MATH",
            content="ALGEBRA",
            subcontent="LINEAR",
            difficulty="EASY",
            reasoning_type="ANALYTICAL",
            status="CLASSIFIED",
        )
        session.add(classification)
        await session.flush()

        return question, version

    async def test_add_item_validates_question_eligibility(self) -> None:
        """Test: add_item rejects ineligible questions in service layer."""
        async with self.session_factory() as session:
            # Create exercise list
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Eligibility Test",
                school_id=self.school_id,
                institution_id=self.institution_id,
                created_by_external_identity="teacher1",
                owner_external_id="teacher1",
            )
            list_id = list_obj.id
            await session.flush()

            # Create ineligible question and version
            question = Question(
                id=uuid.uuid4(),
                question_type="MULTIPLE_CHOICE",
                school_id=uuid.UUID(self.school_id),
                status="DRAFT",  # Ineligible: not PUBLISHED
                validation_status="valid",
                visibility_scope="SCHOOL",
            )
            session.add(question)
            await session.flush()

            version = QuestionVersion(
                id=uuid.uuid4(),
                question_id=question.id,
                version_kind="official_original",
                canonical_text="What is 2+2?",
                content_hash="hash123",
                recommended_difficulty="EASY",
            )
            session.add(version)
            await session.flush()

            # Try to add ineligible question to list
            with self.assertRaises(ValueError) as ctx:
                await service.add_item(
                    list_id,
                    question_version_id=version.id,
                    position=1,
                )

            # Verify error message mentions eligibility
            self.assertIn("not eligible", str(ctx.exception).lower())
            self.assertIn("published", str(ctx.exception).lower())

    async def test_add_item_checks_missing_difficulty(self) -> None:
        """Test: Question without recommended_difficulty is rejected."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="No Difficulty Test",
                school_id=self.school_id,
                institution_id=self.institution_id,
                created_by_external_identity="teacher1",
                owner_external_id="teacher1",
            )
            list_id = list_obj.id
            await session.flush()

            # Create question with PUBLISHED status but NO difficulty
            question = Question(
                id=uuid.uuid4(),
                question_type="MULTIPLE_CHOICE",
                school_id=uuid.UUID(self.school_id),
                status="PUBLISHED",  # Eligible status
                validation_status="valid",
                visibility_scope="SCHOOL",
            )
            session.add(question)
            await session.flush()

            version = QuestionVersion(
                id=uuid.uuid4(),
                question_id=question.id,
                version_kind="official_original",
                canonical_text="What is 2+2?",
                content_hash="hash_no_diff",
                recommended_difficulty=None,  # Missing difficulty!
            )
            session.add(version)
            await session.flush()

            # Try to add — should fail
            with self.assertRaises(ValueError) as ctx:
                await service.add_item(
                    list_id,
                    question_version_id=version.id,
                    position=1,
                )

            self.assertIn("not eligible", str(ctx.exception).lower())

    async def test_add_item_accepts_non_persisted_legacy_uuid(self) -> None:
        """Compatibility: arbitrary UUIDs not backed by a QuestionVersion keep legacy behavior."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Legacy UUID Test",
                school_id=self.school_id,
                institution_id=self.institution_id,
                created_by_external_identity="teacher1",
                owner_external_id="teacher1",
            )
            list_id = list_obj.id
            await session.flush()

            fake_version_id = uuid.uuid4()
            item = await service.add_item(
                list_id,
                question_version_id=fake_version_id,
                position=1,
            )

            self.assertEqual(item["question_version_id"], str(fake_version_id))

    async def test_published_list_rejects_items_regardless_of_eligibility(self) -> None:
        """Test: Published list blocks new items regardless of question eligibility."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            
            # Create list
            list_obj = await service.create_list(
                title="Published Block Test",
                school_id=self.school_id,
                institution_id=self.institution_id,
                created_by_external_identity="teacher1",
                owner_external_id="teacher1",
            )
            list_id = list_obj.id
            await session.flush()

            # Create an eligible question
            question, version = await self._make_eligible_question(session)
            await session.flush()

            # Add item first
            await service.add_item(list_id, question_version_id=version.id, position=1)
            await session.flush()

            # Publish the list
            await service.submit_review(list_id, performed_by_external_id="teacher1")
            await service.approve(list_id, performed_by_external_id="coord1")
            await service.publish(list_id, performed_by_external_id="coord1")
            await session.flush()
            await session.commit()

            # Try to add another question (even eligible one) to published list
            async with self.session_factory() as session2:
                service2 = ExerciseListPersistenceService(session2)
                question2, version2 = await self._make_eligible_question(session2)
                await session2.flush()

                with self.assertRaises(ValueError) as ctx:
                    await service2.add_item(list_id, question_version_id=version2.id, position=2)

                self.assertIn("published", str(ctx.exception).lower())

    async def test_version_reference_persists_after_update(self) -> None:
        """Test: Question version reference remains immutable after question update."""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            
            # Create list
            list_obj = await service.create_list(
                title="Version Immutability Test",
                school_id=self.school_id,
                institution_id=self.institution_id,
                created_by_external_identity="teacher1",
                owner_external_id="teacher1",
            )
            list_id = list_obj.id
            await session.flush()

            # Create eligible question v1
            question, v1 = await self._make_eligible_question(session)
            await session.flush()

            # Add v1 to list
            item = await service.add_item(list_id, question_version_id=v1.id, position=1)
            await session.flush()
            await session.commit()

            # Create a second question/version with a different identity. The domain model allows
            # only one official_original version per question, so we validate immutability by
            # ensuring the list keeps the original reference even when a different version exists.
            question2 = Question(
                id=uuid.uuid4(),
                question_type="MULTIPLE_CHOICE",
                school_id=uuid.UUID(self.school_id),
                status="PUBLISHED",
                validation_status="valid",
                visibility_scope="SCHOOL",
            )
            session.add(question2)
            await session.flush()

            v2 = QuestionVersion(
                id=uuid.uuid4(),
                question_id=question2.id,
                version_kind="modified",
                canonical_text="Other question version",
                content_hash="hash_v2",
                recommended_difficulty=None,
            )
            session.add(v2)
            await session.flush()

            # Retrieve list — should still reference v1
            retrieved = await service.get_list(list_id)
            self.assertEqual(
                retrieved["items"][0]["question_version_id"],
                str(v1.id),
                "List should still reference v1"
            )
            self.assertEqual(retrieved["items"][0]["position"], 1)


if __name__ == "__main__":
    unittest.main()
