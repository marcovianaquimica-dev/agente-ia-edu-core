import unittest
from unittest import mock

from agente_ia_edu import essay_prompts


class _StubArtifact:
    VERSION = "v1"
    RESPONSE_SCHEMA = {"scores": "object"}

    @staticmethod
    def build_prompt(**kwargs) -> str:
        return "prompt text"


class TestEssayPromptRegistry(unittest.TestCase):
    def test_an_unknown_version_fails_loudly(self):
        """No silent default: R3 registers the real artifact, and until then
        asking for one is an error, not a fallback."""
        with self.assertRaises(ValueError):
            essay_prompts.get_essay_prompt("v99")

    def test_resolves_a_registered_artifact(self):
        with mock.patch.dict(
            essay_prompts._ARTIFACTS, {"v1": _StubArtifact}, clear=True
        ):
            prompt = essay_prompts.get_essay_prompt("v1")
            self.assertEqual(prompt.version, "v1")
            self.assertEqual(prompt.response_schema, {"scores": "object"})
            self.assertEqual(prompt.build(), "prompt text")

    def test_available_versions_reflects_the_registry(self):
        with mock.patch.dict(
            essay_prompts._ARTIFACTS, {"v1": _StubArtifact}, clear=True
        ):
            self.assertEqual(essay_prompts.available_versions(), ("v1",))
