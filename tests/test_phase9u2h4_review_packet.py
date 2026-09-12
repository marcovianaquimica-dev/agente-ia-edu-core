"""PHASE 9U.2-H4 STEP 1 — tests for the review-packet builder.

Pure / offline. No DB, no OpenAI, no Alembic, no writes. Also an AST static-safety
scan of the builder.
"""

from __future__ import annotations

import ast
import json
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "manual"))

from agente_ia_edu.services._curriculum_v2_bindings import NEW_CONTENTS, DEFERRED_CONTENTS  # noqa: E402
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    resolve_registered_initial_binding,
)

import phase9u2h4_review_packet as p  # noqa: E402

PROTECTED = {91, 93, 107, 128}
CLASSIFIED_14 = {92, 94, 103, 111, 113, 125, 126, 127, 130, 133, 135, 152, 153, 172}
EXPECTED_6 = {95, 104, 105, 112, 129, 134}


class PacketShapeTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.packet = p.build_packet()
        cls.template = p.build_decision_template()

    def test_01_exactly_the_six_questions(self):
        nums = [q["official_number"] for q in self.packet["questions"]]
        self.assertEqual(nums, [95, 104, 105, 112, 129, 134])
        self.assertEqual(set(nums), EXPECTED_6)

    def test_02_no_protected_question_present(self):
        nums = {q["official_number"] for q in self.packet["questions"]}
        self.assertEqual(nums & PROTECTED, set())
        self.assertEqual(set(self.packet["review_official_numbers"]) & PROTECTED, set())

    def test_03_no_already_classified_question_present(self):
        nums = {q["official_number"] for q in self.packet["questions"]}
        self.assertEqual(nums & CLASSIFIED_14, set())
        self.assertNotIn(133, nums)

    def test_04_declares_zero_side_effects(self):
        self.assertEqual(self.packet["database_writes"], 0)
        self.assertEqual(self.packet["openai_calls"], 0)
        self.assertEqual(self.packet["alembic_execution"], 0)

    def test_05_every_candidate_content_code_is_registered_curriculum_v2(self):
        content_codes = {c for c, _n, _a in NEW_CONTENTS}
        for q in self.packet["questions"]:
            code = q["candidate_content_code"]
            with self.subTest(q=q["official_number"]):
                self.assertEqual(q["taxonomy_version"], "curriculum-v2")
                self.assertIn(code, content_codes)
                self.assertNotIn(code, DEFERRED_CONTENTS)
                self.assertNotEqual(code, "CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS")
                b = resolve_registered_initial_binding("curriculum-v2", code)
                self.assertIsNotNone(b)
                self.assertEqual(b.canonical_code, code)
                self.assertTrue(q["binding_registered"])

    def test_06_statement_and_alternatives_kept_separate(self):
        for q in self.packet["questions"]:
            with self.subTest(q=q["official_number"]):
                self.assertIsInstance(q["statement"], str)
                self.assertIsInstance(q["alternatives"], list)
                self.assertEqual(len(q["alternatives"]), 5)
                # option-only terms must NOT appear as statement evidence or the matched term
                option_terms = {t for oh in q["option_only_evidence"] for t in oh["terms"]}
                self.assertEqual(option_terms & set(q["statement_evidence"]), set())
                if q["matched_term"] is not None:
                    self.assertNotIn(q["matched_term"], option_terms)
                    self.assertIn(q["matched_term"], q["statement_evidence"])
                # a statement-only match yields match_source STATEMENT; otherwise never
                if q["statement_evidence"]:
                    self.assertEqual(q["match_source"], "STATEMENT")
                else:
                    self.assertIn(q["match_source"], {"NONE", "NONE_GENUINE"})

    def test_07_all_current_decisions_are_needs_review(self):
        for q in self.packet["questions"]:
            self.assertEqual(q["current_decision"], "NEEDS_REVIEW", q["official_number"])
            self.assertIn(
                q["binding_status"], {"NONE", "SPURIOUS_BOUND_DEGENERATE_TERM"}
            )

    def test_08_decision_template_all_pending_no_content(self):
        self.assertEqual(len(self.template["decisions"]), 6)
        for d in self.template["decisions"]:
            self.assertEqual(d["decision"], "PENDING")
            self.assertIsNone(d["content_code"])
            self.assertEqual(d["curator_id"], "")
            self.assertEqual(d["evidence_note"], "")
            self.assertIn(d["official_number"], EXPECTED_6)
        self.assertEqual(
            [d["official_number"] for d in self.template["decisions"]],
            [95, 104, 105, 112, 129, 134],
        )

    def test_09_deterministic_between_two_builds(self):
        a = json.dumps(p.build_packet(), sort_keys=True, ensure_ascii=False)
        b = json.dumps(p.build_packet(), sort_keys=True, ensure_ascii=False)
        self.assertEqual(a, b)
        ta = json.dumps(p.build_decision_template(), sort_keys=True, ensure_ascii=False)
        tb = json.dumps(p.build_decision_template(), sort_keys=True, ensure_ascii=False)
        self.assertEqual(ta, tb)

    def test_10_g0_fiche_present_for_each(self):
        for q in self.packet["questions"]:
            with self.subTest(q=q["official_number"]):
                self.assertTrue(q["g0_fiche"]["found"], q["official_number"])
                self.assertIsInstance(q["g0_fiche"]["raw"], str)
                self.assertIn(f"#{q['official_number']}", q["g0_fiche"]["raw"])

    def test_11_question_version_id_and_hash_populated(self):
        for q in self.packet["questions"]:
            with self.subTest(q=q["official_number"]):
                self.assertRegex(
                    q["question_version_id"] or "",
                    r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$",
                )
                self.assertRegex(q["content_hash"] or "", r"^[0-9a-f]{16,64}$")

    def test_12_degenerate_term_warning_where_expected(self):
        by_num = {q["official_number"]: q for q in self.packet["questions"]}
        # Q104 (pH) and Q134 (ρ g h) currently expose the empty-normalizing defect
        for n in (104, 134):
            self.assertEqual(by_num[n]["binding_status"], "SPURIOUS_BOUND_DEGENERATE_TERM")
            self.assertTrue(any("empty string" in w for w in by_num[n]["warnings"]))
            self.assertEqual(by_num[n]["deterministic_vocabulary_probe"]["statement_real_terms"], [])
            self.assertNotEqual(
                by_num[n]["deterministic_vocabulary_probe"]["statement_degenerate_terms"], []
            )

    def test_13_option_only_evidence_flagged_for_104_and_105(self):
        by_num = {q["official_number"]: q for q in self.packet["questions"]}
        self.assertIn("potencial hidrogeniônico",
                      {t for oh in by_num[104]["option_only_evidence"] for t in oh["terms"]})
        self.assertIn("camada de cera",
                      {t for oh in by_num[105]["option_only_evidence"] for t in oh["terms"]})
        for n in (104, 105):
            self.assertTrue(
                any("ALTERNATIVES" in w or "OPTION_ONLY" in w for w in by_num[n]["warnings"])
            )


class ValidationTests(unittest.TestCase):
    def test_validate_targets_ok(self):
        p.validate_targets()  # no raise

    def test_rejects_protected_in_set(self):
        orig = p.H4_REVIEW_OFFICIAL_NUMBERS
        try:
            p.H4_REVIEW_OFFICIAL_NUMBERS = (95, 104, 105, 112, 129, 93)
            with self.assertRaises(p.PacketError):
                p.validate_targets()
        finally:
            p.H4_REVIEW_OFFICIAL_NUMBERS = orig

    def test_rejects_already_classified_in_set(self):
        orig = p.H4_REVIEW_OFFICIAL_NUMBERS
        try:
            p.H4_REVIEW_OFFICIAL_NUMBERS = (95, 104, 105, 112, 129, 133)
            with self.assertRaises(p.PacketError):
                p.validate_targets()
        finally:
            p.H4_REVIEW_OFFICIAL_NUMBERS = orig

    def test_rejects_deferred_candidate(self):
        orig = dict(p._H4_CANDIDATE_CONTENT)
        try:
            p._H4_CANDIDATE_CONTENT[95] = "CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS"
            with self.assertRaises(p.PacketError):
                p.validate_targets()
        finally:
            p._H4_CANDIDATE_CONTENT.clear()
            p._H4_CANDIDATE_CONTENT.update(orig)

    def test_rejects_unknown_candidate(self):
        orig = dict(p._H4_CANDIDATE_CONTENT)
        try:
            p._H4_CANDIDATE_CONTENT[95] = "NOT-A-REAL-CONTENT"
            with self.assertRaises(p.PacketError):
                p.validate_targets()
        finally:
            p._H4_CANDIDATE_CONTENT.clear()
            p._H4_CANDIDATE_CONTENT.update(orig)


class StaticSafetyTests(unittest.TestCase):
    FILE = ROOT / "tests" / "manual" / "phase9u2h4_review_packet.py"

    def _tree(self):
        return ast.parse(self.FILE.read_text(), filename=str(self.FILE))

    def test_no_banned_imports(self):
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.assertNotIn(a.name.split(".")[0], {"alembic", "openai"})
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or "").split(".")[0], {"alembic", "openai"})

    def test_no_write_or_classify_calls(self):
        banned = {
            "add",
            "add_all",
            "commit",
            "flush",
            "create_node",
            "seed_reference_fixture",
            "classify_initial_with_provider",
            "propose_with_provider",
            "supersede_initial_classification",
            "upgrade",
            "downgrade",
            "stamp",
        }
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, banned, f".{node.func.attr}()")

    def test_no_manual_sql_or_constructor(self):
        src = self.FILE.read_text()
        docstrings = set()
        for node in ast.walk(self._tree()):
            if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                d = ast.get_docstring(node, clean=False)
                if d:
                    docstrings.add(d)
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Constant) and isinstance(node.value, str) and node.value not in docstrings:
                low = node.value.lower()
                for kw in ("insert into", "update ", "delete from"):
                    self.assertNotIn(kw, low)
            if isinstance(node, ast.Call):
                name = (
                    node.func.id
                    if isinstance(node.func, ast.Name)
                    else (node.func.attr if isinstance(node.func, ast.Attribute) else None)
                )
                self.assertNotEqual(name, "PedagogicalClassification")

    def test_only_read_only_db_path_is_guarded(self):
        src = self.FILE.read_text()
        # the sole DB helper is augment_from_db and it must issue the RO guard
        self.assertIn("_begin_read_only(session)", src)
        self.assertIn("async def augment_from_db", src)
        # and it must not appear in build_packet / build_decision_template
        pre_db = src.split("async def augment_from_db")[0]
        self.assertNotIn("create_engine", pre_db)
        self.assertNotIn("session", pre_db.split("def build_packet")[1].split("def build_decision_template")[0])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
