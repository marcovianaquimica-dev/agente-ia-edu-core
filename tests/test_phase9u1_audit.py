"""Local-only tests for the Phase 9U.1 read-only post-production audit.

Nothing here touches PostgreSQL, Alembic, or OpenAI. The audit module is loaded
as a module; ``run_audits`` and the sanitisers are exercised against hand-built
``AuditState`` objects and fake records.
"""

from __future__ import annotations

import importlib.util
import io
import json
import re
import sys
import unittest
import contextlib
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
AUDIT_PATH = ROOT / "tests" / "manual" / "phase9u1_audit.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


audit = _load(AUDIT_PATH, "phase9u1_audit_under_test")

_HEX64 = re.compile(r"\A[0-9a-f]{64}\Z")


# --------------------------------------------------------------------------- #
# Builders
# --------------------------------------------------------------------------- #


def _node(code, node_type, active=True, name=None, description=None, parent_code="CHEMISTRY", position=1):
    return {
        "code": code,
        "node_type": node_type,
        "active": active,
        "name": name,
        "description": description,
        "position": position,
        "parent_code": parent_code,
    }


def _parent_ok():
    return _node("CHEMISTRY-PHYSICAL", "AREA", True, "Fisico-Quimica", None, "CHEMISTRY", 1)


def _kinetics_ok():
    return _node(
        "CHEMISTRY-PHYSICAL-KINETICS",
        "CONTENT",
        True,
        "Cinética química",
        "Estudo da velocidade das reações químicas.",
        "CHEMISTRY-PHYSICAL",
        2,
    )


def _qview(number, present=True, versions=1, content=True):
    if not present:
        return {"present": False}
    return {
        "present": True,
        "question_id": f"q{number}",
        "anchor_question_version_id": f"v{number}-0",
        "version_count": versions,
        "version_ids": [f"v{number}-{i}" for i in range(versions)],
        "content_available": content,
        "content_hash_present": True,
        "content_hash_prefix": "aaaaaaaaaaaa…",
        "content_hash_is_sha256": True,
    }


def _crow(**over):
    base = {
        "classification_id": "c-1",
        "question_version_id": "v93-0",
        "status": "CLASSIFIED",
        "source": "ai",
        "classifier_version": "phase9u1-initial-v1",
        "prompt_version": "phase9t3-kinetics-v1",
        "created_at": "2026-09-04T10:00:00+00:00",
        "taxonomy_version": "024_chemistry_kinetics",
        "classification_mode": "INITIAL",
        "primary_discipline_code": "CHEMISTRY",
        "primary_area_code": "CHEMISTRY-PHYSICAL",
        "primary_content_code": "CHEMISTRY-PHYSICAL-KINETICS",
        "primary_subcontent_code": None,
        "legacy_discipline_column": "",
        "legacy_content_column": "CHEMISTRY-PHYSICAL-KINETICS",
        "legacy_subcontent_column": "",
        "metadata_keys": [
            "classification_mode",
            "content_code",
            "input_hash",
            "output_hash",
            "question_content_hash",
            "taxonomy_version",
        ],
        "has_input_hash": True,
        "has_output_hash": True,
        "has_question_content_hash": True,
        "input_hash_prefix": "abcdefabcdef…",
        "output_hash_prefix": "0f0f0f0f0f0f…",
        "question_content_hash_prefix": "123456123456…",
        "input_hash_is_sha256": True,
        "output_hash_is_sha256": True,
        "question_content_hash_is_sha256": True,
        "dedup_fingerprint": "fp-unique",
        "has_reclassification_audit": False,
    }
    base.update(over)
    return base


def _tagged(number, **over):
    row = _crow(**over)
    row.setdefault("resolved_official_numbers", [number])
    return row


def _mp(**over):
    base = {
        "id": "mp-1",
        "original_question_version_id": "v93-0",
        "status": "PENDING",
        "modification_type": "REWRITE",
        "created_at": "2026-08-01T00:00:00+00:00",
        "updated_at": "2026-08-01T00:00:00+00:00",
        "cancelled_at": None,
        "accepted_at": None,
        "updated_after_create": False,
    }
    base.update(over)
    return base


def _state(
    *,
    revision=("024_chemistry_kinetics",),
    parent=None,
    kinetics=None,
    kinetics_count=1,
    q93=None,
    q128=None,
    cls93=(),
    cls128=(),
    tagged=None,
    mp93=(),
    mp128=(),
    links=None,
):
    if tagged is None:
        tagged = (
            _tagged(93, dedup_fingerprint="fp-93"),
            _tagged(128, question_version_id="v128-0", dedup_fingerprint="fp-128"),
        )
    return audit.AuditState(
        alembic_revision_rows=tuple(revision),
        parent_node=_parent_ok() if parent is None else parent,
        kinetics_node=_kinetics_ok() if kinetics is None else kinetics,
        kinetics_code_count=kinetics_count,
        questions={93: q93 or _qview(93), 128: q128 or _qview(128)},
        classifications={93: tuple(cls93), 128: tuple(cls128)},
        tagged_9u1_rows=tuple(tagged),
        modification_proposals={93: tuple(mp93), 128: tuple(mp128)},
        content_link_counts=links
        or {"referencing_kinetics_node": 0, "referencing_target_versions": 0, "total": 0},
    )


def _healthy_state(**kw):
    kw.setdefault("cls93", (_crow(dedup_fingerprint="fp-93"),))
    kw.setdefault("cls128", (_crow(question_version_id="v128-0", dedup_fingerprint="fp-128"),))
    return _state(**kw)


BASELINE_OK = {
    "HISTORICAL_INTEGRITY_STATUS": {
        "historical_023_present": {"93": False, "128": False},
        "total_classification_count": 7,
    }
}


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_strings(item)


# --------------------------------------------------------------------------- #
# Model provenance
# --------------------------------------------------------------------------- #


class FieldSourceTests(unittest.TestCase):
    def test_sources_are_resolved_from_the_real_model(self):
        sources = audit.resolve_field_sources()
        self.assertEqual(sources["classifier_version"], "column:model_version")
        self.assertEqual(sources["prompt_version"], "column:prompt_version")
        self.assertEqual(sources["status"], "column:status")
        self.assertEqual(sources["created_at"], "column:created_at")
        self.assertEqual(sources["metadata_blob_column"], "column:metadata")
        self.assertEqual(sources["taxonomy_version"], "metadata_key:taxonomy_version")
        self.assertEqual(sources["classification_mode"], "metadata_key:classification_mode")
        self.assertEqual(sources["primary_content_code"], "metadata_key:content_code")
        self.assertEqual(sources["primary_area_code"], "metadata_key:area_code")
        self.assertEqual(sources["legacy_content_column"], "column:content")


# --------------------------------------------------------------------------- #
# Sanitisation
# --------------------------------------------------------------------------- #


class _FakeRecord:
    def __init__(self, metadata_):
        self.id = "11111111-1111-1111-1111-111111111111"
        self.question_version_id = "22222222-2222-2222-2222-222222222222"
        self.status = "CLASSIFIED"
        self.source = "ai"
        self.model_version = "phase9u1-initial-v1"
        self.prompt_version = "phase9t3-kinetics-v1"
        self.created_at = datetime(2026, 9, 4, tzinfo=timezone.utc)
        self.discipline = ""
        self.content = "CHEMISTRY-PHYSICAL-KINETICS"
        self.subcontent = ""
        self.metadata_ = metadata_


class SanitizeClassificationTests(unittest.TestCase):
    def test_hashes_truncated_and_question_content_never_leaks(self):
        record = _FakeRecord(
            {
                "input_hash": "a" * 64,
                "output_hash": "b" * 64,
                "question_content_hash": "c" * 64,
                "taxonomy_version": "024_chemistry_kinetics",
                "classification_mode": "INITIAL",
                "content_code": "CHEMISTRY-PHYSICAL-KINETICS",
                "evidence": [{"text": "SENSITIVE QUESTION EXCERPT", "reason": "x"}],
                "statement": "SENSITIVE STATEMENT",
            }
        )
        row = audit.sanitize_classification(record)
        blob = json.dumps(row, ensure_ascii=False)
        self.assertEqual(row["input_hash_prefix"], "a" * 12 + "…")
        self.assertEqual(row["output_hash_prefix"], "b" * 12 + "…")
        self.assertEqual(row["question_content_hash_prefix"], "c" * 12 + "…")
        self.assertTrue(row["input_hash_is_sha256"])
        self.assertNotIn("input_hash", row)
        self.assertNotIn("output_hash", row)
        self.assertNotIn("question_content_hash", row)
        for leak in ("a" * 64, "b" * 64, "c" * 64, "SENSITIVE QUESTION EXCERPT", "SENSITIVE STATEMENT"):
            self.assertNotIn(leak, blob)
        self.assertIn("evidence", row["metadata_keys"])  # key name only, no value
        self.assertEqual(row["primary_content_code"], "CHEMISTRY-PHYSICAL-KINETICS")
        self.assertRegex(row["dedup_fingerprint"], r"\A[0-9a-f]{16}\Z")

    def test_non_sha256_hash_is_flagged(self):
        record = _FakeRecord({"input_hash": "not-a-hash", "output_hash": None})
        row = audit.sanitize_classification(record)
        self.assertFalse(row["input_hash_is_sha256"])
        self.assertFalse(row["has_output_hash"])

    def test_scrub_removes_dsn_and_secrets(self):
        message = "connect to postgresql+psycopg://user:s3cr3t@host:5432/agente failed"
        scrubbed = audit._scrub(message)
        self.assertNotIn("s3cr3t", scrubbed)
        self.assertNotIn("user:s3cr3t", scrubbed)
        self.assertIn("[REDACTED_URL]", scrubbed)


# --------------------------------------------------------------------------- #
# run_audits — verdicts
# --------------------------------------------------------------------------- #


class RunAuditsTests(unittest.TestCase):
    def test_healthy_without_baseline_is_needs_review_but_no_unexpected(self):
        report = audit.run_audits(_healthy_state())
        for key in (
            "ALEMBIC_REVISION",
            "TAXONOMY_NODE",
            "TAXONOMY_PARENT",
            "QUESTION_93",
            "QUESTION_128",
            "INITIAL_CLASSIFICATION_93",
            "INITIAL_CLASSIFICATION_128",
            "CLASSIFICATION_DUPLICATION",
            "HASH_INTEGRITY",
        ):
            self.assertEqual(report[key], "PASS", key)
        self.assertEqual(report["HISTORICAL_INTEGRITY"], "NOT_PROVABLE")
        self.assertEqual(report["MODIFICATION_PROPOSAL_INTEGRITY"], "PASS")
        self.assertEqual(report["CONTENT_LINK_INTEGRITY"], "PASS")
        self.assertEqual(report["QUESTION_SCOPE"], "PASS")
        self.assertEqual(report["UNEXPECTED_CURRENT_STATE"], [])
        self.assertEqual(report["FINAL_DECISION"], audit.AUDIT_REVIEW)
        self.assertEqual(report["POSTGRESQL_WRITES"], 0)
        self.assertEqual(report["OPENAI_CALLS"], 0)
        self.assertEqual(report["MIGRATION_EXECUTION"], 0)
        self.assertEqual(report["DATABASE_WRITES"], 0)

    def test_healthy_with_consistent_baseline_is_pass(self):
        report = audit.run_audits(_healthy_state(), recorded_baseline=BASELINE_OK)
        self.assertEqual(report["HISTORICAL_INTEGRITY"], "PASS")
        self.assertEqual(report["FINAL_DECISION"], audit.AUDIT_PASS)
        self.assertEqual(report["UNEXPECTED_CURRENT_STATE"], [])

    def test_wrong_alembic_revision_fails(self):
        report = audit.run_audits(_healthy_state(revision=("023_curriculum_taxonomy",)))
        self.assertEqual(report["ALEMBIC_REVISION"], "FAIL")
        self.assertEqual(report["FINAL_DECISION"], audit.AUDIT_REVIEW)
        self.assertIn("ALEMBIC_REVISION=FAIL", report["UNEXPECTED_CURRENT_STATE"])

    def test_kinetics_node_wrong_type_fails(self):
        bad = _kinetics_ok()
        bad["node_type"] = "SUBCONTENT"
        report = audit.run_audits(_healthy_state(kinetics=bad))
        self.assertEqual(report["TAXONOMY_NODE"], "FAIL")

    def test_duplicate_kinetics_code_flagged(self):
        report = audit.run_audits(_healthy_state(kinetics_count=2))
        self.assertEqual(report["TAXONOMY_NODE"], "FAIL")
        self.assertIn("kinetics_code_count=2", report["UNEXPECTED_CURRENT_STATE"])

    def test_missing_initial_classification_fails(self):
        report = audit.run_audits(_state(cls93=(), cls128=(_crow(question_version_id="v128-0"),)))
        self.assertEqual(report["INITIAL_CLASSIFICATION_93"], "FAIL")
        self.assertEqual(report["INITIAL_CLASSIFICATION_128"], "PASS")
        self.assertEqual(report["FINAL_DECISION"], audit.AUDIT_REVIEW)
        self.assertIn("INITIAL_CLASSIFICATION_93=FAIL", report["UNEXPECTED_CURRENT_STATE"])

    def test_hash_integrity_fails_when_no_new_rows_exist_at_all(self):
        report = audit.run_audits(_state(cls93=(), cls128=()))
        self.assertEqual(report["HASH_INTEGRITY"], "FAIL")
        self.assertEqual(report["INITIAL_CLASSIFICATION_93"], "FAIL")
        self.assertEqual(report["INITIAL_CLASSIFICATION_128"], "FAIL")

    def test_duplicate_initial_classification_detected(self):
        dup = (
            _crow(classification_id="c-a", dedup_fingerprint="same"),
            _crow(classification_id="c-b", dedup_fingerprint="same"),
        )
        report = audit.run_audits(
            _state(cls93=dup, cls128=(_crow(question_version_id="v128-0", dedup_fingerprint="fp-128"),))
        )
        self.assertEqual(report["CLASSIFICATION_DUPLICATION"], "FAIL")
        self.assertEqual(report["INITIAL_CLASSIFICATION_93"], "FAIL")

    def test_historical_023_row_present_is_fail_not_not_provable(self):
        rows = (
            _crow(dedup_fingerprint="fp-93"),
            _crow(
                classification_id="old",
                taxonomy_version="023_curriculum_taxonomy",
                classifier_version="phase9c-v1",
                classification_mode=None,
                dedup_fingerprint="fp-023",
            ),
        )
        report = audit.run_audits(
            _state(cls93=rows, cls128=(_crow(question_version_id="v128-0", dedup_fingerprint="fp-128"),)),
            recorded_baseline=BASELINE_OK,
        )
        self.assertEqual(report["HISTORICAL_INTEGRITY"], "FAIL")
        self.assertIn("HISTORICAL_INTEGRITY=FAIL", report["UNEXPECTED_CURRENT_STATE"])
        self.assertEqual(report["FINAL_DECISION"], audit.AUDIT_REVIEW)

    def test_hash_not_sha256_shaped_fails(self):
        bad = _crow(dedup_fingerprint="fp-93", input_hash_is_sha256=False)
        report = audit.run_audits(
            _state(cls93=(bad,), cls128=(_crow(question_version_id="v128-0", dedup_fingerprint="fp-128"),))
        )
        self.assertEqual(report["HASH_INTEGRITY"], "FAIL")

    def test_missing_question_content_hash_fails(self):
        bad = _crow(dedup_fingerprint="fp-93", has_question_content_hash=False)
        report = audit.run_audits(
            _state(cls93=(bad,), cls128=(_crow(question_version_id="v128-0", dedup_fingerprint="fp-128"),))
        )
        self.assertEqual(report["HASH_INTEGRITY"], "FAIL")

    def test_classification_mode_absent_is_tolerated(self):
        row = _crow(dedup_fingerprint="fp-93", classification_mode=None)
        row["metadata_keys"] = ["content_code", "input_hash", "output_hash", "question_content_hash", "taxonomy_version"]
        report = audit.run_audits(
            _state(cls93=(row,), cls128=(_crow(question_version_id="v128-0", dedup_fingerprint="fp-128"),)),
            recorded_baseline=BASELINE_OK,
        )
        self.assertEqual(report["INITIAL_CLASSIFICATION_93"], "PASS")
        self.assertFalse(report["DETAIL"]["INITIAL_CLASSIFICATION_93"]["detail"]["classification_mode_present"])

    def test_primary_content_code_not_kinetics_fails(self):
        row = _crow(dedup_fingerprint="fp-93", primary_content_code="CHEMISTRY-SOLUTIONS")
        report = audit.run_audits(
            _state(cls93=(row,), cls128=(_crow(question_version_id="v128-0", dedup_fingerprint="fp-128"),))
        )
        self.assertEqual(report["INITIAL_CLASSIFICATION_93"], "FAIL")

    def test_scope_violation_detected(self):
        tagged = (
            _tagged(93, dedup_fingerprint="fp-93"),
            _tagged(128, dedup_fingerprint="fp-128"),
            _tagged(95, classification_id="c-bad", resolved_official_numbers=[95]),
        )
        report = audit.run_audits(_healthy_state(tagged=tagged))
        self.assertEqual(report["QUESTION_SCOPE"], "FAIL")
        self.assertIn("QUESTION_SCOPE=FAIL", report["UNEXPECTED_CURRENT_STATE"])

    def test_scope_unresolved_is_not_provable(self):
        tagged = (
            _tagged(93, dedup_fingerprint="fp-93"),
            _tagged(128, dedup_fingerprint="fp-128"),
            _crow(classification_id="c-x", resolved_official_numbers=[]),
        )
        report = audit.run_audits(_healthy_state(tagged=tagged))
        self.assertEqual(report["QUESTION_SCOPE"], "NOT_PROVABLE")
        self.assertEqual(report["FINAL_DECISION"], audit.AUDIT_REVIEW)

    def test_content_link_to_kinetics_node_fails(self):
        links = {"referencing_kinetics_node": 1, "referencing_target_versions": 1, "total": 4}
        report = audit.run_audits(_healthy_state(links=links), recorded_baseline=BASELINE_OK)
        self.assertEqual(report["CONTENT_LINK_INTEGRITY"], "FAIL")
        self.assertEqual(report["FINAL_DECISION"], audit.AUDIT_REVIEW)

    def test_modification_proposal_updated_is_not_provable(self):
        report = audit.run_audits(
            _healthy_state(mp93=(_mp(updated_after_create=True, updated_at="2026-09-04T10:00:01+00:00"),)),
            recorded_baseline=BASELINE_OK,
        )
        self.assertEqual(report["MODIFICATION_PROPOSAL_INTEGRITY"], "NOT_PROVABLE")
        self.assertEqual(report["FINAL_DECISION"], audit.AUDIT_REVIEW)

    def test_question_without_content_fails(self):
        report = audit.run_audits(_healthy_state(q93=_qview(93, content=False)))
        self.assertEqual(report["QUESTION_93"], "FAIL")


# --------------------------------------------------------------------------- #
# Report rendering / sanitisation / fail-closed
# --------------------------------------------------------------------------- #


class ReportRenderingTests(unittest.TestCase):
    def test_report_contains_no_secrets_or_full_hashes(self):
        report = audit.run_audits(_healthy_state(), recorded_baseline=BASELINE_OK)
        blob = json.dumps(report, ensure_ascii=False)
        for forbidden in ("DATABASE_URL", "OPENAI_API_KEY", "password", "canonical_text", "statement"):
            self.assertNotIn(forbidden, blob)
        for value in _walk_strings(report):
            self.assertIsNone(_HEX64.match(value), msg=f"full hash leaked: {value!r}")

    def test_emit_prints_every_top_line_field(self):
        report = audit.run_audits(_healthy_state(), recorded_baseline=BASELINE_OK)
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            audit.emit_audit_report(report)
        printed = buffer.getvalue()
        self.assertIn("PHASE 9U.1 — FINAL POST-PRODUCTION AUDIT", printed)
        for key in (
            "ALEMBIC_REVISION",
            "TAXONOMY_NODE",
            "TAXONOMY_PARENT",
            "QUESTION_93",
            "QUESTION_128",
            "INITIAL_CLASSIFICATION_93",
            "INITIAL_CLASSIFICATION_128",
            "CLASSIFICATION_DUPLICATION",
            "HASH_INTEGRITY",
            "HISTORICAL_INTEGRITY",
            "MODIFICATION_PROPOSAL_INTEGRITY",
            "CONTENT_LINK_INTEGRITY",
            "QUESTION_SCOPE",
            "UNEXPECTED_CURRENT_STATE",
            "POSTGRESQL_READS",
            "POSTGRESQL_WRITES",
            "DATABASE_WRITES",
            "OPENAI_CALLS",
            "MIGRATION_EXECUTION",
            "FINAL_DECISION",
        ):
            self.assertIn(f"{key}:", printed)
        self.assertIn('FINAL_DECISION: "PRODUCTION_AUDIT_PASS"', printed)
        self.assertNotIn("OPENAI_API_KEY", printed)

    def test_failure_report_is_fail_closed_and_scrubbed(self):
        report = audit.failure_report(
            RuntimeError("driver error for postgresql+psycopg://u:pw@h:5432/db")
        )
        self.assertEqual(report["FINAL_DECISION"], audit.AUDIT_REVIEW)
        for name in audit.PROVABLE_FIELDS:
            self.assertEqual(report[name], "FAIL")
        for name in audit.TRISTATE_FIELDS:
            self.assertEqual(report[name], "NOT_PROVABLE")
        self.assertEqual(report["POSTGRESQL_READS"], 0)
        self.assertNotIn("pw@h", report["FAILURE_MESSAGE"])
        self.assertIn("AUDIT_ABORTED:RuntimeError", report["UNEXPECTED_CURRENT_STATE"])

    def test_counters_are_read_only(self):
        report = audit.run_audits(_healthy_state())
        self.assertEqual(report["POSTGRESQL_READS"], 1)
        self.assertEqual(report["POSTGRESQL_WRITES"], 0)
        self.assertEqual(report["DATABASE_WRITES"], 0)
        self.assertEqual(report["OPENAI_CALLS"], 0)
        self.assertEqual(report["MIGRATION_EXECUTION"], 0)


class StaticSourceGuardTests(unittest.TestCase):
    def test_source_performs_no_writes_or_service_calls(self):
        src = AUDIT_PATH.read_text(encoding="utf-8")
        self.assertIn("SET TRANSACTION READ ONLY", src)
        for forbidden in (
            "classify_initial_with_provider",
            "reclassify_with_provider",
            "command.upgrade",
            "command.downgrade",
            "op.execute",
            "op.create",
            "op.drop",
            "INSERT INTO",
            "DELETE FROM",
            "session.add(",
            ".delete(",
        ):
            self.assertNotIn(forbidden, src, msg=f"forbidden token in audit source: {forbidden}")

    def test_source_only_reads_alembic_version(self):
        src = AUDIT_PATH.read_text(encoding="utf-8")
        self.assertIn("SELECT version_num FROM alembic_version", src)
        self.assertNotIn("alembic upgrade", src)
        self.assertNotIn("alembic downgrade", src)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
