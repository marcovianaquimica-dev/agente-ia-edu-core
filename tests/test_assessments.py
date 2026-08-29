import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from agente_ia_edu.services.assessments import (
    AssessmentFactory,
    AssessmentPublicationService,
    AssessmentService,
)


class AssessmentDomainTests(unittest.TestCase):
    def setUp(self) -> None:
        self.factory = AssessmentFactory()
        self.assessment = self.factory.create_assessment(
            title="Simulado de Matemática",
            description="Avaliação inicial",
            created_by_external_identity="ext-user-001",
            institution_id="inst-001",
        )
        self.version = self.factory.create_version(
            assessment=self.assessment,
            version_number=1,
            title="Versão 1",
            status="draft",
        )

    def test_assessment_creation_tracks_external_identity(self) -> None:
        self.assertEqual(self.assessment.title, "Simulado de Matemática")
        self.assertEqual(self.assessment.created_by_external_identity, "ext-user-001")
        self.assertEqual(self.assessment.status, "draft")

    def test_versioning_generates_new_version_for_published_instances(self) -> None:
        self.version.publish()
        next_version = self.factory.create_version(
            assessment=self.assessment,
            version_number=2,
            title="Versão 2",
            status="draft",
        )

        self.assertNotEqual(self.version.id, next_version.id)
        self.assertEqual(self.version.status, "published")
        self.assertEqual(next_version.version_number, 2)

    def test_items_require_unique_position_per_version(self) -> None:
        q1 = uuid4()
        q2 = uuid4()
        self.factory.add_item(self.version, question_version_id=q1, position=1, points=2)
        self.factory.add_item(self.version, question_version_id=q2, position=2, points=3)

        with self.assertRaises(ValueError):
            self.factory.add_item(self.version, question_version_id=uuid4(), position=1, points=1)

    def test_publication_supports_immediate_release(self) -> None:
        self.factory.add_item(self.version, question_version_id=uuid4(), position=1, points=2)
        pub = self.factory.publish(
            self.version,
            publication_type="immediate",
            released_immediately=True,
            time_limit_seconds=1200,
            attempts_allowed=2,
        )

        self.assertEqual(pub.publication_type, "immediate")
        self.assertTrue(pub.released_immediately)
        self.assertEqual(pub.time_limit_seconds, 1200)
        self.assertEqual(pub.attempts_allowed, 2)

    def test_scheduled_publication_sets_window_and_expires_at(self) -> None:
        started_at = datetime.now(timezone.utc)
        publication = AssessmentPublicationService.build_publication(
            assessment_version=self.version,
            publication_type="scheduled",
            started_at=started_at,
            ends_at=started_at + timedelta(hours=2),
            time_limit_seconds=900,
            attempts_allowed=1,
        )

        self.assertEqual(publication.publication_type, "scheduled")
        self.assertFalse(publication.released_immediately)
        self.assertEqual(publication.time_limit_seconds, 900)
        self.assertIsNotNone(publication.starts_at)
        self.assertIsNotNone(publication.ends_at)

    def test_attempt_expires_at_uses_time_limit_and_deadline(self) -> None:
        started_at = datetime(2026, 8, 29, 8, 0, tzinfo=timezone.utc)
        publication = AssessmentPublicationService.build_publication(
            assessment_version=self.version,
            publication_type="immediate",
            released_immediately=True,
            starts_at=started_at,
            ends_at=started_at + timedelta(hours=2),
            time_limit_seconds=1800,
            attempts_allowed=1,
        )

        attempt = AssessmentService.start_attempt(
            publication=publication,
            external_identity_id="ext-student-42",
            attempt_number=1,
            started_at=started_at,
        )

        self.assertEqual(attempt.expires_at, started_at + timedelta(seconds=1800))

    def test_objective_answer_is_correctly_evaluated(self) -> None:
        question_version_id = uuid4()
        option_a = uuid4()
        option_b = uuid4()
        self.factory.add_item(self.version, question_version_id=question_version_id, position=1, points=5)

        service = AssessmentService()
        attempt = service.start_attempt(
            publication=self.factory.publish(
                self.version,
                publication_type="immediate",
                released_immediately=True,
                time_limit_seconds=600,
                attempts_allowed=1,
            ),
            external_identity_id="ext-student-42",
            attempt_number=1,
            started_at=datetime.now(timezone.utc),
        )

        answer = service.register_answer(
            attempt=attempt,
            assessment_item=self.version.items[0],
            selected_option_id=option_a,
            response_text=None,
            first_answered_at=datetime.now(timezone.utc),
            submitted_at=datetime.now(timezone.utc),
            response_time_ms=200,
            is_final=True,
            question_correct_option_id=option_a,
            question_points=5,
        )

        self.assertTrue(answer.is_correct)
        self.assertEqual(answer.points_awarded, 5)

    def test_note_is_calculated_deterministically(self) -> None:
        q1 = uuid4()
        q2 = uuid4()
        self.factory.add_item(self.version, question_version_id=q1, position=1, points=3)
        self.factory.add_item(self.version, question_version_id=q2, position=2, points=7)
        publication = self.factory.publish(
            self.version,
            publication_type="immediate",
            released_immediately=True,
            time_limit_seconds=600,
            attempts_allowed=1,
        )

        service = AssessmentService()
        attempt = service.start_attempt(
            publication=publication,
            external_identity_id="ext-student-42",
            attempt_number=1,
            started_at=datetime.now(timezone.utc),
        )

        service.register_answer(
            attempt=attempt,
            assessment_item=self.version.items[0],
            selected_option_id=uuid4(),
            response_text=None,
            first_answered_at=datetime.now(timezone.utc),
            submitted_at=datetime.now(timezone.utc),
            response_time_ms=150,
            is_final=True,
            question_correct_option_id=uuid4(),
            question_points=3,
        )
        service.register_answer(
            attempt=attempt,
            assessment_item=self.version.items[1],
            selected_option_id=uuid4(),
            response_text=None,
            first_answered_at=datetime.now(timezone.utc),
            submitted_at=datetime.now(timezone.utc),
            response_time_ms=120,
            is_final=True,
            question_correct_option_id=uuid4(),
            question_points=7,
        )

        self.assertEqual(service.calculate_score(attempt), 0)

    def test_invalid_state_rejected_for_published_version_mutation(self) -> None:
        self.version.publish()
        with self.assertRaises(ValueError):
            self.version.status = "draft"

    def test_external_identity_is_used_without_local_user_record(self) -> None:
        assessment = self.factory.create_assessment(
            title="Avaliação externa",
            description="Sem usuário local",
            created_by_external_identity="ext-prof-7",
            institution_id="inst-9",
        )
        self.assertEqual(assessment.created_by_external_identity, "ext-prof-7")
        self.assertNotIn("password", assessment.__dict__)


if __name__ == "__main__":
    unittest.main()
