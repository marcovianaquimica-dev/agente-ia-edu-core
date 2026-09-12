"""PHASE 11.18 - focused tests for the versioned classification prompt artifact."""

import inspect
import json
import pathlib
import unittest

from agente_ia_edu import classification_prompts
from agente_ia_edu.classification_prompts import (
    DEFAULT_VERSION,
    ClassificationPrompt,
    available_versions,
    get_classification_prompt,
)
from agente_ia_edu.classification_prompts import v1 as prompt_v1
from agente_ia_edu.services import curriculum_classification

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_GOLDEN = _ROOT / "tests" / "fixtures" / "phase11_18_golden_classification_prompt_v1.txt"

# The fixed fixture the golden file was generated from (pre-PHASE-11.18 inline code).
_FIXTURE_RECOVERED = [{
    "discipline_code": "MATH", "area_code": "MATH-ALGEBRA",
    "content_code": "MATH-ALGEBRA-PERCENTAGE", "subcontent_code": None,
    "rank": 1, "score": 90, "candidate_type": "CONTROLLED_VOCABULARY",
    "matched_terms": ["porcentagem de acertos"],
}]
_FIXTURE_QDATA = {
    "statement": "João acertou 75% das questões.",
    "options": [{"key": "A", "text": "muito bom"}, {"key": "B", "text": "bom"}],
    "question_content_hash": "abc123",
}

_SECRET_MARKERS = (
    "sk-", "sk-proj-", "OPENAI_API_KEY", "OPENAI_MODEL", "AsyncOpenAI",
    "authorization:", "bearer ", "postgres://", "postgresql://", "DATABASE_URL", "password",
)
_VENDOR_MARKERS = ("openai", "anthropic", "gpt-", "claude", "AsyncOpenAI", "ProviderRouter",
                   "api_key", "temperature", "response_format", "max_tokens")


class ClassificationPromptArtifactTests(unittest.TestCase):
    # 1 - version resolves
    def test_version_resolves(self):
        default = get_classification_prompt()
        self.assertIsInstance(default, ClassificationPrompt)
        self.assertEqual(default.version, "v1")
        self.assertEqual(default.version, DEFAULT_VERSION)
        self.assertEqual(get_classification_prompt("v1").version, "v1")
        self.assertIn("v1", available_versions())

    # 2 - unknown version fails clearly
    def test_unknown_version_fails_clearly(self):
        with self.assertRaises(ValueError) as ctx:
            get_classification_prompt("v99")
        message = str(ctx.exception)
        self.assertIn("Unknown classification prompt version", message)
        self.assertIn("v99", message)
        self.assertIn("v1", message)  # available list is shown

    # 3 - no secret / no vendor coupling in the artifact
    def test_artifact_contains_no_secret_or_vendor_reference(self):
        source = pathlib.Path(prompt_v1.__file__).read_text()
        built = get_classification_prompt().build(
            recovered_candidates=_FIXTURE_RECOVERED, question_data=_FIXTURE_QDATA)
        schema_text = json.dumps(prompt_v1.RESPONSE_SCHEMA, ensure_ascii=False)
        for blob_name, blob in (("v1.py source", source), ("built prompt", built),
                                ("RESPONSE_SCHEMA", schema_text)):
            low = blob.lower()
            for marker in _SECRET_MARKERS:
                self.assertNotIn(marker.lower(), low, f"{blob_name} contains {marker!r}")
            for marker in _VENDOR_MARKERS:
                self.assertNotIn(marker.lower(), low, f"{blob_name} contains vendor marker {marker!r}")

    # 4 - current prompt content / behavior is preserved byte-for-byte
    def test_prompt_is_byte_identical_to_pre_1118_construction(self):
        golden = _GOLDEN.read_text()
        built = get_classification_prompt().build(
            recovered_candidates=_FIXTURE_RECOVERED, question_data=_FIXTURE_QDATA)
        self.assertEqual(built, golden)

    def test_response_schema_is_unchanged(self):
        schema = get_classification_prompt().response_schema
        self.assertEqual(list(schema.keys()), [
            "selected_candidate_rank", "discipline_code", "area_code", "content_code",
            "subcontent_code", "confidence", "evidence", "candidate_classifications",
            "complementary_contents", "catalog_gap", "gap_type", "taxonomy_coverage_evidence",
            "review_reason", "visual_dependency", "status",
        ])
        self.assertEqual(schema["selected_candidate_rank"], "integer|null")
        self.assertEqual(schema["confidence"], "HIGH|MEDIUM|LOW")
        self.assertEqual(schema["catalog_gap"], False)
        self.assertEqual(schema["visual_dependency"], False)
        self.assertEqual(schema["status"], "PROPOSED|NEEDS_REVIEW")
        self.assertIn("MISSING_SUBCONTENT|null", schema["gap_type"])
        # the golden's RESPONSE_SCHEMA line matches json.dumps of this dict exactly
        golden_schema_line = _GOLDEN.read_text().split("\n")[1]
        self.assertEqual(golden_schema_line,
                         "RESPONSE_SCHEMA: " + json.dumps(schema, ensure_ascii=False))

    # 5 - the classification service uses the versioned prompt (not an inline literal)
    def test_service_uses_versioned_prompt_artifact(self):
        src = inspect.getsource(curriculum_classification.ClassificationProposalService.propose_with_provider)
        self.assertIn("get_classification_prompt()", src)
        self.assertNotIn("SYSTEM_POLICY:", src)
        self.assertNotIn("RESPONSE_SCHEMA: ", src)
        self.assertNotIn("response_schema = {", src)
        self.assertIn(
            "get_classification_prompt",
            [n.name for n in _module_imports(curriculum_classification)],
        )

    # 6 - provider layer is untouched by the prompt artifact
    def test_provider_layer_does_not_depend_on_prompt_artifact(self):
        providers_dir = _ROOT / "src" / "agente_ia_edu" / "providers"
        for path in providers_dir.rglob("*.py"):
            text = path.read_text()
            self.assertNotIn("classification_prompt", text, f"{path} references the prompt artifact")

    def test_artifact_is_provider_independent_object(self):
        prompt = get_classification_prompt()
        # a plain frozen dataclass: version + schema + a build() method, nothing else
        self.assertTrue(hasattr(prompt, "build"))
        self.assertTrue(hasattr(prompt, "version"))
        self.assertTrue(hasattr(prompt, "response_schema"))
        self.assertFalse(hasattr(prompt, "provider"))
        self.assertFalse(hasattr(prompt, "model"))
        self.assertFalse(hasattr(prompt, "api_key"))


def _module_imports(module):
    import ast
    tree = ast.parse(pathlib.Path(module.__file__).read_text())
    names = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            names.extend(node.names)
        elif isinstance(node, ast.Import):
            names.extend(node.names)
    return names


if __name__ == "__main__":
    unittest.main()
