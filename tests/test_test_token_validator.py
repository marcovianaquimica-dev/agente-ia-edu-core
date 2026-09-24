"""TestTokenValidator (dev/test-only token parser) and TokenPayload defaults.

Companion to test_jwt_token_validator.py: closes out the branches in
agente_ia_edu.auth.token that JWTTokenValidator's tests don't exercise -
TestTokenValidator's format-parsing error paths and TokenPayload's
metadata default. TestTokenValidator is explicitly NOT a security boundary
(see its docstring: "MUST NOT be used in production"), so these tests are
about correct parsing/error-messaging, not attack resistance.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.auth.token import TestTokenValidator, TokenPayload


class TestTokenPayloadDefaults(unittest.TestCase):
    def test_metadata_defaults_to_empty_dict_when_omitted(self):
        payload = TokenPayload(subject="alice")
        self.assertEqual(payload.metadata, {})

    def test_metadata_defaults_independently_per_instance(self):
        # Guards against the classic mutable-default-argument trap: each
        # instance must get its OWN dict, not a single dict shared/mutated
        # across every TokenPayload built with no explicit metadata.
        first = TokenPayload(subject="alice")
        second = TokenPayload(subject="bob")
        first.metadata["leaked"] = True
        self.assertNotIn("leaked", second.metadata)


class TestTestTokenValidator(unittest.IsolatedAsyncioTestCase):
    async def test_rejects_empty_string_token(self):
        validator = TestTokenValidator()
        with self.assertRaises(ValueError):
            await validator.validate("")

    async def test_rejects_non_string_token(self):
        validator = TestTokenValidator()
        with self.assertRaises(ValueError):
            await validator.validate(None)  # type: ignore[arg-type]

    async def test_rejects_test_prefixed_token_missing_the_role_colon(self):
        """"test:<role>:<subject>" requires two parts after the prefix; a
        token with only a role and no subject segment (no second colon) must
        be rejected with a clear format error, not silently misparsed."""
        validator = TestTokenValidator()
        with self.assertRaises(ValueError) as ctx:
            await validator.validate("test:teacher")
        self.assertIn("Invalid test token format", str(ctx.exception))

    async def test_plain_subject_without_test_prefix_uses_default_role(self):
        validator = TestTokenValidator(default_role="student")
        payload = await validator.validate("alice")
        self.assertEqual(payload.subject, "alice")
        self.assertEqual(payload.roles, ("STUDENT",))

    async def test_rejects_test_prefixed_token_with_empty_subject(self):
        validator = TestTokenValidator()
        with self.assertRaises(ValueError) as ctx:
            await validator.validate("test:teacher:")
        self.assertIn("subject cannot be empty", str(ctx.exception))

    async def test_unknown_role_falls_back_to_default_role(self):
        validator = TestTokenValidator(default_role="student")
        payload = await validator.validate("test:bogus_role:alice")
        self.assertEqual(payload.subject, "alice")
        self.assertEqual(payload.roles, ("STUDENT",))

    async def test_admin_role_normalizes_to_platform_admin(self):
        validator = TestTokenValidator()
        payload = await validator.validate("test:admin:master")
        self.assertEqual(payload.roles, ("PLATFORM_ADMIN",))


if __name__ == "__main__":
    unittest.main()
