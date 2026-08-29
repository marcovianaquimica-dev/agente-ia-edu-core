import asyncio
import unittest

from agente_ia_edu.identity import (
    ExternalIdentityContext,
    ExternalIdentityProvider,
    ExternalIdentityRequest,
)


class HostIdentityProvider:
    async def resolve(self, request: ExternalIdentityRequest) -> ExternalIdentityContext:
        return ExternalIdentityContext(
            provider=request.provider,
            external_user_id=request.external_user_id,
            student_id="student-0042",
            teacher_id=None,
            institution_id="inst-001",
            unit_id="unit-010",
            grade_level="9",
            classroom_id="class-202",
            roles=("student",),
        )


class IdentityContractsTests(unittest.TestCase):
    def test_identity_context_uses_external_ids_without_local_users(self) -> None:
        context = ExternalIdentityContext(
            provider="plataforma-hospedeira",
            external_user_id="host-user-123",
            student_id="host-student-456",
            teacher_id="host-teacher-789",
            institution_id="host-institution-001",
            unit_id="host-unit-010",
            grade_level="8",
            classroom_id="host-class-202",
            roles=("student", "teacher"),
        )

        self.assertEqual(context.provider, "plataforma-hospedeira")
        self.assertEqual(context.external_user_id, "host-user-123")
        self.assertEqual(context.student_id, "host-student-456")
        self.assertEqual(context.teacher_id, "host-teacher-789")
        self.assertEqual(context.institution_id, "host-institution-001")
        self.assertEqual(context.unit_id, "host-unit-010")
        self.assertEqual(context.grade_level, "8")
        self.assertEqual(context.classroom_id, "host-class-202")
        self.assertEqual(context.roles, ("student", "teacher"))

    def test_provider_protocol_accepts_host_authenticated_identity(self) -> None:
        provider = HostIdentityProvider()

        self.assertIsInstance(provider, ExternalIdentityProvider)

        result = asyncio.run(
            provider.resolve(
                ExternalIdentityRequest(
                    provider="plataforma-hospedeira",
                    external_user_id="host-user-123",
                    subject="student-0042",
                    roles=("student",),
                )
            )
        )

        self.assertEqual(result.provider, "plataforma-hospedeira")
        self.assertEqual(result.external_user_id, "host-user-123")
        self.assertEqual(result.student_id, "student-0042")
        self.assertEqual(result.institution_id, "inst-001")

    def test_identity_request_does_not_include_password_or_credentials(self) -> None:
        self.assertNotIn("password", ExternalIdentityRequest.__annotations__)
        self.assertNotIn("credentials", ExternalIdentityRequest.__annotations__)


if __name__ == "__main__":
    unittest.main()
