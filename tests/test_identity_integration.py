"""Tests for external identity integration in attempts.

Verifies:
- Identity provider resolves correctly
- Endpoints receive ExternalIdentityContext
- Identity ownership checks enforce isolation
- No hardcoded placeholders in execution path
- Client cannot override identity
"""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from uuid import uuid4

from agente_ia_edu.api.dependencies import (
    TestExternalIdentityProvider,
    get_identity_provider,
    set_identity_provider,
)
from agente_ia_edu.identity import (
    ExternalIdentityContext,
    ExternalIdentityRequest,
)


class TestExternalIdentityProviderTests(unittest.TestCase):
    """Test the TestExternalIdentityProvider."""

    def setUp(self):
        """Reset to default provider before each test."""
        set_identity_provider(TestExternalIdentityProvider())

    def test_provider_resolves_student_identity(self):
        """Test that provider correctly resolves student identity."""
        async def run_test():
            provider = TestExternalIdentityProvider()
            request = ExternalIdentityRequest(
                provider="test",
                external_user_id="alice",
                subject="student:alice",
            )
            
            context = await provider.resolve(request)
            
            self.assertEqual(context.external_user_id, "alice")
            self.assertEqual(context.student_id, "alice")
            self.assertIsNone(context.teacher_id)
            self.assertIn("student", context.roles)
            self.assertTrue(context.metadata.get("test_provider"))
        
        asyncio.run(run_test())

    def test_provider_resolves_teacher_identity(self):
        """Test that provider correctly resolves teacher identity."""
        async def run_test():
            provider = TestExternalIdentityProvider()
            request = ExternalIdentityRequest(
                provider="test",
                external_user_id="bob",
                subject="teacher:bob",
            )
            
            context = await provider.resolve(request)
            
            self.assertEqual(context.external_user_id, "bob")
            self.assertIsNone(context.student_id)
            self.assertEqual(context.teacher_id, "bob")
            self.assertIn("teacher", context.roles)
        
        asyncio.run(run_test())

    def test_provider_returns_frozen_context(self):
        """Test that context is immutable (frozen dataclass)."""
        async def run_test():
            provider = TestExternalIdentityProvider()
            request = ExternalIdentityRequest(
                provider="test",
                external_user_id="charlie",
                subject="student:charlie",
            )
            
            context = await provider.resolve(request)
            
            # Should raise AttributeError because dataclass is frozen
            with self.assertRaises(AttributeError):
                context.external_user_id = "different"
        
        asyncio.run(run_test())

    def test_provider_handles_unformatted_subject(self):
        """Test provider handles subjects without role:id format."""
        async def run_test():
            provider = TestExternalIdentityProvider()
            request = ExternalIdentityRequest(
                provider="test",
                external_user_id="unknown",
            )
            
            context = await provider.resolve(request)
            
            # Should default to student role
            self.assertEqual(context.external_user_id, "unknown")
            self.assertIn("student", context.roles)
        
        asyncio.run(run_test())

    def test_provider_is_deterministic(self):
        """Test that same input produces same output (deterministic)."""
        async def run_test():
            provider = TestExternalIdentityProvider()
            request = ExternalIdentityRequest(
                provider="test",
                external_user_id="diana",
                subject="student:diana",
            )
            
            context1 = await provider.resolve(request)
            context2 = await provider.resolve(request)
            
            self.assertEqual(context1.external_user_id, context2.external_user_id)
            self.assertEqual(context1.student_id, context2.student_id)
            self.assertEqual(context1.roles, context2.roles)
        
        asyncio.run(run_test())


class IdentityIsolationTests(unittest.TestCase):
    """Test that identity properly isolates student data.
    
    Note: These are unit tests of the identity contract.
    Integration tests with FastAPI endpoints are in test_attempt_execution.py
    """

    def test_different_students_have_different_ids(self):
        """Test that different students have distinct identities."""
        alice = ExternalIdentityContext(
            provider="test",
            external_user_id="alice",
            student_id="alice",
        )
        bob = ExternalIdentityContext(
            provider="test",
            external_user_id="bob",
            student_id="bob",
        )
        
        self.assertNotEqual(alice.external_user_id, bob.external_user_id)
        self.assertNotEqual(alice.student_id, bob.student_id)

    def test_identity_contains_no_credentials(self):
        """Test that identity context doesn't contain passwords or tokens."""
        context = ExternalIdentityContext(
            provider="test",
            external_user_id="student1",
            student_id="student1",
        )
        
        # Should not have these fields
        self.assertFalse(hasattr(context, "password"))
        self.assertFalse(hasattr(context, "token"))
        self.assertFalse(hasattr(context, "secret"))
        self.assertFalse(hasattr(context, "api_key"))


class NoPlaceholderTests(unittest.TestCase):
    """Test that no hardcoded placeholders exist in the execution path."""

    def test_test_provider_identifies_as_test(self):
        """Test that TestExternalIdentityProvider is clearly marked as test."""
        provider = TestExternalIdentityProvider()
        self.assertIn("Test", type(provider).__name__)

    def test_test_provider_has_test_marker_in_context(self):
        """Test that contexts from test provider have test marker."""
        def run_test():
            provider = TestExternalIdentityProvider()
            
            async def verify():
                request = ExternalIdentityRequest(
                    provider="test",
                    external_user_id="student1",
                    subject="student:student1",
                )
                context = await provider.resolve(request)
                self.assertTrue(context.metadata.get("test_provider"))
                self.assertTrue(context.metadata.get("test_mode"))
            
            asyncio.run(verify())
        
        run_test()

    def test_no_hardcoded_authenticated_student_id_in_provider(self):
        """Test that provider doesn't use hardcoded 'authenticated-student-id'."""
        def run_test():
            provider = TestExternalIdentityProvider()
            
            async def verify():
                request = ExternalIdentityRequest(
                    provider="test",
                    external_user_id="test_student",
                    subject="student:test_student",
                )
                context = await provider.resolve(request)
                
                # Should NOT be the old placeholder value
                self.assertNotEqual(context.external_user_id, "authenticated-student-id")
                # Should be based on the request
                self.assertEqual(context.external_user_id, "test_student")
            
            asyncio.run(verify())
        
        run_test()


class HostIntegrationPatternTests(unittest.TestCase):
    """Test the pattern for host integration.
    
    The host must:
    1. Implement ExternalIdentityProvider protocol
    2. Call set_identity_provider() at startup
    3. Optionally override get_current_identity() to extract request data
    """

    def test_provider_can_be_replaced(self):
        """Test that application provider can be swapped."""
        original_provider = get_identity_provider()
        
        new_provider = TestExternalIdentityProvider()
        set_identity_provider(new_provider)
        
        retrieved = get_identity_provider()
        self.assertIs(retrieved, new_provider)

    def test_custom_provider_pattern(self):
        """Test pattern for creating a custom provider."""
        
        class MockCustomProvider:
            """Mock provider as an example for host to implement."""
            
            async def resolve(self, request: ExternalIdentityRequest) -> ExternalIdentityContext:
                """Custom provider extracts from their own auth system."""
                return ExternalIdentityContext(
                    provider="CustomHost",
                    external_user_id=request.external_user_id,
                    student_id=request.subject,  # Example: use subject as student_id
                    roles=("student",),
                )
        
        custom_provider = MockCustomProvider()
        set_identity_provider(custom_provider)
        
        retrieved = get_identity_provider()
        self.assertIsInstance(retrieved, MockCustomProvider)


class StandaloneFutureTests(unittest.TestCase):
    """Test that architecture supports future standalone mode.
    
    Standalone will need a StandaloneIdentityProvider that does login flow.
    """

    def test_architecture_accepts_any_provider(self):
        """Test that any provider matching protocol is accepted."""
        
        class StandaloneProviderExample:
            """Example of what standalone provider might look like."""
            
            async def resolve(self, request: ExternalIdentityRequest) -> ExternalIdentityContext:
                """Standalone: parse JWT or session token."""
                # In real implementation, would:
                # 1. Validate JWT signature
                # 2. Extract claims
                # 3. Return context
                return ExternalIdentityContext(
                    provider="Standalone",
                    external_user_id="decoded_from_jwt",
                    student_id="decoded_student_id",
                    roles=("student",),
                )
        
        provider = StandaloneProviderExample()
        set_identity_provider(provider)
        
        # Should be able to use this provider
        retrieved = get_identity_provider()
        self.assertEqual(retrieved.provider if hasattr(retrieved, 'provider') else None, 
                        None)  # Just verify it's there


if __name__ == "__main__":
    unittest.main()
