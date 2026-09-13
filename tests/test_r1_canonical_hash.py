import hashlib
import json
import unittest

from agente_ia_edu.services.canonical_hash import canonical_hash, canonical_json


class TestCanonicalHash(unittest.TestCase):
    def test_matches_the_legacy_inline_expression(self):
        """Characterization: rows already persisted carry digests produced by the
        inline expression in ClassificationProposalService._hash. Extraction must
        not change a single byte."""
        payload = {"b": 1, "a": "acentuação", "c": [3, 2, 1]}
        legacy = hashlib.sha256(
            json.dumps(
                payload, sort_keys=True, ensure_ascii=False, separators=(",", ":")
            ).encode("utf-8")
        ).hexdigest()

        self.assertEqual(canonical_hash(payload), legacy)

    def test_key_order_does_not_change_the_digest(self):
        self.assertEqual(
            canonical_hash({"a": 1, "b": 2}), canonical_hash({"b": 2, "a": 1})
        )

    def test_canonical_json_keeps_non_ascii_literal(self):
        self.assertEqual(canonical_json({"k": "ção"}), '{"k":"ção"}')
