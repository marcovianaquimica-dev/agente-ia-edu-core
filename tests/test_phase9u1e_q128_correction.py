"""Phase 9U.1-E — local-only tests for the Q128 correction routine.

Nothing here touches PostgreSQL, Alembic-on-a-real-db, or OpenAI, and
``main()`` (the real orchestration entrypoint) is never invoked - only its
composable, pure/DB-local pieces are exercised directly against in-memory
SQLite, exactly like every other manual-script test suite in this project.
The production Q128 row is never referenced by anything other than its
already-known id constants; no live database is contacted.
"""

from __future__ import annotations

import importlib.util
import sys
import unittest
import uuid
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    BookletQuestion,
    CatalogNode,
    Exam,
    ExamApplication,
    ExamBooklet,
    Institution,
    PedagogicalClassification,
    Question,
    QuestionVersion,
)
from agente_ia_edu.services.curriculum_classification import ClassificationProposalService
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService


ROOT = Path(__file__).resolve().parents[1]
CORRECTION_PATH = ROOT / "tests" / "manual" / "phase9u1e_q128_correction.py"
PRODUCTION_PATH = ROOT / "tests" / "manual" / "phase9u1_production.py"
MIGRATION_024_PATH = ROOT / "migrations" / "versions" / "024_chemistry_kinetics.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


routine = _load(CORRECTION_PATH, "phase9u1e_q128_correction_under_test")
production_executor = _load(PRODUCTION_PATH, "phase9u1e_production_under_test")
migration_024 = _load(MIGRATION_024_PATH, "phase9u1e_migration_024_under_test")

TEXT_128 = "Em um estudo cinético, a concentração de sacarose foi reduzida à metade."
TEXT_93 = "Nanomateriais catalíticos aumentam a velocidade da reação química."

KNOWN_QV_UUID = uuid.UUID(routine.KNOWN_QUESTION_VERSION_ID)
KNOWN_CLS_UUID = uuid.UUID(routine.KNOWN_EXISTING_CLASSIFICATION_ID)


def _apply_024(connection):
    context = MigrationContext.configure(connection)
    previous = migration_024.op
    migration_024.op = Operations(context)
    try:
        migration_024.upgrade()
    finally:
        migration_024.op = previous


# --------------------------------------------------------------------------- #
# 1. Pure plan_correction() tests — no database
# --------------------------------------------------------------------------- #


def _row(**over):
    base = {
        "id": routine.KNOWN_EXISTING_CLASSIFICATION_ID,
        "question_version_id": routine.KNOWN_QUESTION_VERSION_ID,
        "status": "CLASSIFIED",
        "lifecycle": "ACTIVE",
        "supersedes_id": None,
        "classifier_version": routine.EXPECTED_ORIGINAL_CLASSIFIER_VERSION,
        "prompt_version": routine.EXPECTED_ORIGINAL_PROMPT_VERSION,
        "content_code": routine.WRONG_CONTENT_CODE,
        "taxonomy_version": routine.TAXONOMY_VERSION,
        "classification_mode": routine.CLASSIFICATION_MODE,
        "input_hash_prefix": "abc…",
        "output_hash_prefix": "def…",
        "created_at": "2026-09-04T11:14:59+00:00",
    }
    base.update(over)
    return base


def _snapshot(**over):
    base = dict(
        alembic_revision=routine.REQUIRED_ALEMBIC_REVISION,
        question_found=True,
        question_version_id=routine.KNOWN_QUESTION_VERSION_ID,
        content_available=True,
        all_classifications=(_row(),),
        binding_status="BOUND",
        binding_content_code=routine.EXPECTED_CONTENT_CODE,
        kinetics_candidate_recovered=True,
        other_rows_with_correction_tag=0,
    )
    base.update(over)
    return routine.CorrectionSnapshot(**base)


class PlanCorrectionPureTests(unittest.TestCase):
    def test_happy_path_is_ready(self):
        plan = routine.plan_correction(_snapshot())
        self.assertEqual(plan.action, "READY")
        self.assertEqual(plan.superseded_id, routine.KNOWN_EXISTING_CLASSIFICATION_ID)

    def test_already_correct_is_idempotent_no_action(self):
        snap = _snapshot(all_classifications=(_row(content_code=routine.EXPECTED_CONTENT_CODE),))
        plan = routine.plan_correction(snap)
        self.assertEqual(plan.action, "ALREADY_CORRECT")

    def test_wrong_alembic_revision_fails_closed(self):
        with self.assertRaisesRegex(routine.PreFlightError, "Lifecycle infrastructure"):
            routine.plan_correction(_snapshot(alembic_revision="024_chemistry_kinetics"))

    def test_question_not_found_fails_closed(self):
        with self.assertRaisesRegex(routine.PreFlightError, "was not found"):
            routine.plan_correction(_snapshot(question_found=False, question_version_id=None, all_classifications=()))

    def test_wrong_question_version_id_fails_closed(self):
        with self.assertRaisesRegex(routine.PreFlightError, "does not match the known Q128 id"):
            routine.plan_correction(_snapshot(question_version_id=str(uuid.uuid4())))

    def test_zero_active_initial_024_fails_closed(self):
        with self.assertRaisesRegex(routine.PreFlightError, "No ACTIVE INITIAL/024"):
            routine.plan_correction(_snapshot(all_classifications=(_row(lifecycle="SUPERSEDED"),)))

    def test_multiple_active_initial_024_fails_closed(self):
        rows = (_row(id="a" * 32), _row(id="b" * 32))
        with self.assertRaisesRegex(routine.PreFlightError, "Multiple"):
            routine.plan_correction(_snapshot(all_classifications=rows))

    def test_non_initial_and_wrong_taxonomy_rows_are_excluded_not_counted(self):
        # A STANDARD-mode row and a 023-taxonomy row coexist; neither should
        # count toward "how many ACTIVE INITIAL/024 rows exist", so the one
        # genuine INITIAL/024 row is still unambiguous.
        rows = (
            _row(),
            _row(id="c" * 32, classification_mode="STANDARD"),
            _row(id="d" * 32, taxonomy_version="023_curriculum_taxonomy"),
        )
        plan = routine.plan_correction(_snapshot(all_classifications=rows))
        self.assertEqual(plan.action, "READY")

    def test_classification_id_mismatch_fails_closed(self):
        with self.assertRaisesRegex(routine.PreFlightError, "does not match the known Q128 classification id"):
            routine.plan_correction(_snapshot(all_classifications=(_row(id=str(uuid.uuid4())),)))

    def test_classification_not_from_phase9u1_flow_fails_closed(self):
        with self.assertRaisesRegex(routine.PreFlightError, "Phase 9U.1 executor's tag"):
            routine.plan_correction(_snapshot(all_classifications=(_row(classifier_version="some-other-pipeline-v1"),)))

    def test_content_code_neither_wrong_nor_expected_fails_closed(self):
        with self.assertRaisesRegex(routine.PreFlightError, "matches neither the known-incorrect"):
            routine.plan_correction(_snapshot(all_classifications=(_row(content_code="CHEMISTRY-SOMETHING-ELSE"),)))

    def test_kinetics_not_recovered_fails_closed(self):
        with self.assertRaisesRegex(routine.PreFlightError, "not among the recovered candidates"):
            routine.plan_correction(_snapshot(kinetics_candidate_recovered=False))

    def test_binding_not_bound_fails_closed(self):
        with self.assertRaisesRegex(routine.PreFlightError, "not deterministically BOUND"):
            routine.plan_correction(_snapshot(binding_status="NEEDS_REVIEW", binding_content_code=None))

    def test_binding_bound_to_wrong_code_fails_closed(self):
        with self.assertRaisesRegex(routine.PreFlightError, "not deterministically BOUND"):
            routine.plan_correction(_snapshot(binding_content_code="CHEMISTRY-SOLUTIONS"))

    def test_prior_partial_run_tag_present_fails_closed(self):
        with self.assertRaisesRegex(routine.PreFlightError, "already carries the correction tag"):
            routine.plan_correction(_snapshot(other_rows_with_correction_tag=1))

    def test_content_unavailable_fails_closed(self):
        with self.assertRaisesRegex(routine.PreFlightError, "no available content"):
            routine.plan_correction(_snapshot(content_available=False))


# --------------------------------------------------------------------------- #
# 2. End-to-end: capture_snapshot + plan_correction + real supersede write,
#    against local SQLite only.
# --------------------------------------------------------------------------- #


async def _make_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
        await connection.execute(
            # VARCHAR(32) mirrors Alembic's real alembic_version.version_num
            # width - the exact column that overflowed the original 40-char id.
            text("CREATE TABLE alembic_version (version_num VARCHAR(32) NOT NULL PRIMARY KEY)")
        )
        await connection.execute(
            text("INSERT INTO alembic_version (version_num) VALUES (:rev)"),
            {"rev": routine.REQUIRED_ALEMBIC_REVISION},
        )
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await CurriculumTaxonomyService(session).seed_reference_fixture()
    async with engine.begin() as connection:
        await connection.run_sync(_apply_024)
    taxonomy_expr = "(metadata ->> '$.taxonomy_version')"
    async with engine.begin() as connection:
        await connection.execute(
            text(
                "CREATE UNIQUE INDEX uq_pedagogical_classifications_active_taxonomy "
                f"ON pedagogical_classifications (question_version_id, {taxonomy_expr}) "
                "WHERE lifecycle = 'ACTIVE'"
            )
        )
    return engine, factory


async def _seed_booklet_question(session, number, version):
    institution = Institution(code=f"INST{number}", name="Instituicao")
    session.add(institution)
    await session.flush()
    exam = Exam(institution_id=institution.id, code=f"EX{number}", name="ENEM")
    session.add(exam)
    await session.flush()
    application = ExamApplication(exam_id=exam.id, year=2020, application_type="REGULAR", day=2)
    session.add(application)
    await session.flush()
    booklet = ExamBooklet(exam_application_id=application.id, code=f"CD{number}")
    session.add(booklet)
    await session.flush()
    bq = BookletQuestion(
        exam_booklet_id=booklet.id, question_version_id=version.id, position=number, official_number=number
    )
    session.add(bq)
    await session.flush()


async def _seed_known_q128(session, *, content_code=routine.WRONG_CONTENT_CODE, **overrides):
    question = Question(
        validation_status="validated", origin_type="IMPORTED", status="DRAFT", visibility_scope="PRIVATE"
    )
    session.add(question)
    await session.flush()
    version = QuestionVersion(
        id=KNOWN_QV_UUID, question_id=question.id, version_kind="official_original",
        canonical_text=TEXT_128, content_hash="q128-hash",
    )
    session.add(version)
    await session.flush()
    await _seed_booklet_question(session, 128, version)

    record = None
    if content_code is not None:
        fields = {
            "id": KNOWN_CLS_UUID, "question_version_id": version.id, "discipline": "CHEMISTRY",
            "content": content_code, "subcontent": "", "difficulty": "UNKNOWN",
            "reasoning_type": "UNSPECIFIED", "status": "CLASSIFIED", "source": "ai", "lifecycle": "ACTIVE",
            "model_version": routine.EXPECTED_ORIGINAL_CLASSIFIER_VERSION,
            "prompt_version": routine.EXPECTED_ORIGINAL_PROMPT_VERSION,
            "metadata_": {
                "taxonomy_version": routine.TAXONOMY_VERSION,
                "classification_mode": routine.CLASSIFICATION_MODE,
                "content_code": content_code, "input_hash": "seed-input", "output_hash": "seed-output",
            },
        }
        fields.update(overrides)
        record = PedagogicalClassification(**fields)
        session.add(record)
        await session.flush()
    await session.commit()
    return question, version, record


async def _seed_q93_untouched(session):
    question = Question(
        validation_status="validated", origin_type="IMPORTED", status="DRAFT", visibility_scope="PRIVATE"
    )
    session.add(question)
    await session.flush()
    version = QuestionVersion(
        question_id=question.id, version_kind="official_original", canonical_text=TEXT_93, content_hash="q93-hash"
    )
    session.add(version)
    await session.flush()
    await _seed_booklet_question(session, 93, version)
    classification = PedagogicalClassification(
        question_version_id=version.id, discipline="CHEMISTRY", content=routine.EXPECTED_CONTENT_CODE,
        subcontent="", difficulty="UNKNOWN", reasoning_type="UNSPECIFIED", status="CLASSIFIED", source="ai",
        lifecycle="ACTIVE", model_version=routine.EXPECTED_ORIGINAL_CLASSIFIER_VERSION,
        prompt_version=routine.EXPECTED_ORIGINAL_PROMPT_VERSION,
        metadata_={
            "taxonomy_version": routine.TAXONOMY_VERSION, "classification_mode": routine.CLASSIFICATION_MODE,
            "content_code": routine.EXPECTED_CONTENT_CODE, "input_hash": "q93-input", "output_hash": "q93-output",
        },
    )
    session.add(classification)
    await session.commit()
    return version, classification


class EndToEndCorrectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _snapshot_now(self):
        # _read_snapshot is the dialect-portable core of capture_snapshot
        # (which additionally issues SET TRANSACTION READ ONLY - PostgreSQL
        # syntax not supported by SQLite, exercised only in the static/
        # structural guard below).
        async with self.factory() as session:
            async with session.begin():
                return await routine._read_snapshot(session)

    async def _apply_correction(self, plan):
        async with self.factory() as session:
            provider = await routine._build_deterministic_provider(session)
            from agente_ia_edu.providers.router import ProviderRouter

            new = await ClassificationProposalService(session).supersede_initial_classification(
                superseded_id=uuid.UUID(plan.superseded_id),
                question_version_id=KNOWN_QV_UUID,
                provider=ProviderRouter(text_providers=[provider], embedding_providers=[]),
                target_taxonomy_version=routine.TAXONOMY_VERSION,
                classifier_version=routine.CORRECTION_CLASSIFIER_VERSION,
                prompt_version=routine.CORRECTION_PROMPT_VERSION,
            )
            return new, provider

    async def test_correct_q128_correction_is_executable_end_to_end(self):
        async with self.factory() as session:
            _, _, old = await _seed_known_q128(session)
        snapshot = await self._snapshot_now()
        plan = routine.plan_correction(snapshot)
        self.assertEqual(plan.action, "READY")

        new, provider = await self._apply_correction(plan)

        self.assertEqual(provider.calls, 1)  # deterministic local call only, never OpenAI
        self.assertEqual(new.lifecycle, "ACTIVE")
        self.assertEqual(new.metadata_["classification_mode"], "INITIAL")
        self.assertEqual(new.metadata_["taxonomy_version"], routine.TAXONOMY_VERSION)
        self.assertEqual(new.metadata_["content_code"], routine.EXPECTED_CONTENT_CODE)
        self.assertEqual(str(new.supersedes_id), routine.KNOWN_EXISTING_CLASSIFICATION_ID)

        async with self.factory() as session:
            refreshed_old = await session.get(PedagogicalClassification, KNOWN_CLS_UUID)
        self.assertEqual(refreshed_old.lifecycle, "SUPERSEDED")
        self.assertEqual(refreshed_old.content, routine.WRONG_CONTENT_CODE)  # substantive data untouched
        self.assertEqual(refreshed_old.model_version, routine.EXPECTED_ORIGINAL_CLASSIFIER_VERSION)

    async def test_already_corrected_is_idempotent_no_duplicate(self):
        async with self.factory() as session:
            await _seed_known_q128(session, content_code=routine.EXPECTED_CONTENT_CODE)
        snapshot = await self._snapshot_now()
        plan = routine.plan_correction(snapshot)
        self.assertEqual(plan.action, "ALREADY_CORRECT")
        async with self.factory() as session:
            total = await session.scalar(
                select(func.count()).select_from(PedagogicalClassification)
                .where(PedagogicalClassification.question_version_id == KNOWN_QV_UUID)
            )
        self.assertEqual(total, 1)  # nothing added

    async def test_running_twice_does_not_duplicate(self):
        async with self.factory() as session:
            await _seed_known_q128(session)
        plan1 = routine.plan_correction(await self._snapshot_now())
        await self._apply_correction(plan1)

        plan2 = routine.plan_correction(await self._snapshot_now())
        self.assertEqual(plan2.action, "ALREADY_CORRECT")
        async with self.factory() as session:
            total = await session.scalar(
                select(func.count()).select_from(PedagogicalClassification)
                .where(PedagogicalClassification.question_version_id == KNOWN_QV_UUID)
            )
        self.assertEqual(total, 2)  # old (superseded) + new (active) - never 3

    async def test_no_other_question_is_affected(self):
        async with self.factory() as session:
            await _seed_known_q128(session)
            q93_version, q93_classification = await _seed_q93_untouched(session)
        plan = routine.plan_correction(await self._snapshot_now())
        await self._apply_correction(plan)

        async with self.factory() as session:
            refreshed_q93 = await session.get(PedagogicalClassification, q93_classification.id)
        self.assertEqual(refreshed_q93.lifecycle, "ACTIVE")
        self.assertEqual(refreshed_q93.content, routine.EXPECTED_CONTENT_CODE)
        self.assertIsNone(refreshed_q93.supersedes_id)

    async def test_failed_supersede_persists_nothing(self):
        async with self.factory() as session:
            await _seed_known_q128(session)
        # Corrupt the plan to point at an id that is not ACTIVE (simulates a
        # precondition violated between pre-flight and write) -> the service
        # itself raises before any commit.
        async with self.factory() as session:
            existing = await session.get(PedagogicalClassification, KNOWN_CLS_UUID)
            existing.lifecycle = "SUPERSEDED"
            await session.commit()
        plan = routine.CorrectionPlan(action="READY", superseded_id=routine.KNOWN_EXISTING_CLASSIFICATION_ID, reason="test")
        with self.assertRaises(ValueError):
            await self._apply_correction(plan)
        async with self.factory() as session:
            total = await session.scalar(
                select(func.count()).select_from(PedagogicalClassification)
                .where(PedagogicalClassification.question_version_id == KNOWN_QV_UUID)
            )
        self.assertEqual(total, 1)  # no new row was persisted by the failed attempt

    async def test_concurrent_active_insert_rejected_by_database_constraint(self):
        async with self.factory() as session:
            await _seed_known_q128(session, content_code=routine.EXPECTED_CONTENT_CODE, lifecycle="SUPERSEDED")
        async with self.factory() as session_a:
            row_a = PedagogicalClassification(
                question_version_id=KNOWN_QV_UUID, discipline="CHEMISTRY", content=routine.EXPECTED_CONTENT_CODE,
                subcontent="", difficulty="UNKNOWN", reasoning_type="UNSPECIFIED", status="CLASSIFIED",
                source="ai", lifecycle="ACTIVE",
                metadata_={"taxonomy_version": routine.TAXONOMY_VERSION, "input_hash": "race-a"},
            )
            session_a.add(row_a)
            await session_a.commit()
        async with self.factory() as session_b:
            row_b = PedagogicalClassification(
                question_version_id=KNOWN_QV_UUID, discipline="CHEMISTRY", content=routine.EXPECTED_CONTENT_CODE,
                subcontent="", difficulty="UNKNOWN", reasoning_type="UNSPECIFIED", status="CLASSIFIED",
                source="ai", lifecycle="ACTIVE",
                metadata_={"taxonomy_version": routine.TAXONOMY_VERSION, "input_hash": "race-b"},
            )
            session_b.add(row_b)
            with self.assertRaises(Exception) as ctx:
                await session_b.commit()
            self.assertIn("Integrity", type(ctx.exception).__name__)


# --------------------------------------------------------------------------- #
# 3. DRY-RUN gating and approval (pure)
# --------------------------------------------------------------------------- #


class DryRunAndApprovalTests(unittest.TestCase):
    def test_dry_run_defaults_true(self):
        self.assertTrue(routine.is_dry_run({}))

    def test_dry_run_disabled_only_by_explicit_false(self):
        # Case-insensitive exact match on "false" is the only way to disable
        # DRY-RUN; anything else (including "no", "0", "") leaves it enabled.
        self.assertFalse(routine.is_dry_run({routine.DRY_RUN_ENV_VAR: "false"}))
        self.assertFalse(routine.is_dry_run({routine.DRY_RUN_ENV_VAR: "False"}))
        self.assertFalse(routine.is_dry_run({routine.DRY_RUN_ENV_VAR: "FALSE"}))
        self.assertTrue(routine.is_dry_run({routine.DRY_RUN_ENV_VAR: "no"}))
        self.assertTrue(routine.is_dry_run({routine.DRY_RUN_ENV_VAR: "0"}))
        self.assertTrue(routine.is_dry_run({routine.DRY_RUN_ENV_VAR: ""}))

    def test_approval_is_fail_closed(self):
        self.assertFalse(routine.approval_granted({}, interactive=False))
        self.assertFalse(routine.approval_granted({routine.APPROVAL_ENV_VAR: "nope"}, interactive=False))
        self.assertTrue(
            routine.approval_granted({routine.APPROVAL_ENV_VAR: routine.APPROVAL_TOKEN}, interactive=False)
        )

    def test_source_never_writes_before_approval_or_while_dry_run(self):
        source = CORRECTION_PATH.read_text(encoding="utf-8")
        main_src = source[source.index("async def main"):]
        idx_dry_run_check = main_src.index("if dry_run:")
        idx_approval = main_src.index("approval_granted(")
        idx_supersede = main_src.index("supersede_initial_classification(")
        self.assertLess(idx_dry_run_check, idx_approval)
        self.assertLess(idx_approval, idx_supersede)


# --------------------------------------------------------------------------- #
# 4. Static source guards
# --------------------------------------------------------------------------- #


class StaticSourceGuardTests(unittest.TestCase):
    def test_no_manual_write_statements_or_reclassification(self):
        source = CORRECTION_PATH.read_text(encoding="utf-8")
        self.assertIn("supersede_initial_classification", source)
        for forbidden in (
            "reclassify_with_provider",
            "INSERT INTO",
            "UPDATE pedagogical_classifications",
            "DELETE FROM",
            ".delete(",
        ):
            self.assertNotIn(forbidden, source, msg=f"forbidden token found: {forbidden}")

    def test_preflight_is_read_only(self):
        source = CORRECTION_PATH.read_text(encoding="utf-8")
        self.assertIn("SET TRANSACTION READ ONLY", source)

    def test_scope_is_hardcoded_to_128_only(self):
        source = CORRECTION_PATH.read_text(encoding="utf-8")
        self.assertIn("KNOWN_OFFICIAL_NUMBER = 128", source)
        self.assertNotIn("for number in", source)  # no iteration over questions anywhere


# --------------------------------------------------------------------------- #
# 5. Cross-check: the production executor's own migration-chain validation
#    (023 -> 024 -> 025) still passes, independent of this new routine.
# --------------------------------------------------------------------------- #


class MigrationChainCrossCheckTests(unittest.TestCase):
    def test_production_executor_chain_still_valid(self):
        info = production_executor.validate_local_migration_chain()
        self.assertEqual(info["heads"], (production_executor.LIFECYCLE_REVISION,))
        self.assertEqual(info["target_down_revision"], production_executor.SOURCE_REVISION)
        self.assertEqual(info["lifecycle_down_revision"], production_executor.TARGET_REVISION)

    def test_correction_routine_requires_lifecycle_revision(self):
        self.assertEqual(routine.REQUIRED_ALEMBIC_REVISION, production_executor.LIFECYCLE_REVISION)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
