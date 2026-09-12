"""Local-only tests for the Phase 9U.1 INITIAL production executor.

Nothing here touches PostgreSQL, Alembic-on-a-real-database, or OpenAI. The
executor is loaded as a module; its pure decision functions are exercised against
hand-built snapshots, and its database-facing helpers run against in-memory
SQLite with the real ``024_chemistry_kinetics`` migration applied via an Alembic
``MigrationContext`` (the same pattern the existing migration tests use).
"""

from __future__ import annotations

import contextlib
import importlib.util
import io
import json
import re
import sys
import tempfile
import unittest
import uuid
from pathlib import Path
from unittest.mock import AsyncMock, patch

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    BookletQuestion,
    CatalogNode,
    ContentQuestionLink,
    Exam,
    ExamApplication,
    ExamBooklet,
    Institution,
    PedagogicalClassification,
    Question,
    QuestionVersion,
)
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult
from agente_ia_edu.services.curriculum_classification import ClassificationProposalService
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService


ROOT = Path(__file__).resolve().parents[1]
EXECUTOR_PATH = ROOT / "tests" / "manual" / "phase9u1_production.py"
MIGRATION_PATH = ROOT / "migrations" / "versions" / "024_chemistry_kinetics.py"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


executor = _load(EXECUTOR_PATH, "phase9u1_production_under_test")
migration = _load(MIGRATION_PATH, "phase9u1_migration_under_test")

TEXT_93 = "Nanomateriais catalíticos aumentam a velocidade da reação química."
TEXT_128 = "Em um estudo cinético, a concentração de sacarose foi reduzida à metade."


# --------------------------------------------------------------------------- #
# Snapshot fixtures for the pure validators
# --------------------------------------------------------------------------- #


def _node(code, node_type, parent_code, *, active=True, name=None, description=None, position=1):
    return {
        "code": code,
        "name": name or code,
        "description": description,
        "node_type": node_type,
        "active": active,
        "position": position,
        "parent_code": parent_code,
        "root_code": "CHEMISTRY",
    }


def _parent_node(**overrides):
    base = _node(executor.PARENT_CODE, "AREA", "CHEMISTRY", name="Fisico-Quimica")
    base.update(overrides)
    return base


def _kinetics_node(**overrides):
    base = _node(
        executor.NODE_CODE,
        "CONTENT",
        executor.PARENT_CODE,
        name="Cinética química",
        description="Estudo da velocidade das reações químicas.",
        position=2,
    )
    base.update(overrides)
    return base


def _ok_question(number):
    return {
        "present": True,
        "question_id": f"q{number}",
        "question_version_id": f"v{number}",
        "version_count": 1,
        "content_hash": f"h{number}",
        "content_available": True,
        "classifications": [],
        "historical_023": [],
        "target_024": [],
    }


def _snapshot(*, revision="023_curriculum_taxonomy", nodes=None, questions=None,
              classification_count=0, link_count=0):
    if nodes is None:
        nodes = [_node("CHEMISTRY", "DISCIPLINE", None), _parent_node()]
    if questions is None:
        questions = {number: _ok_question(number) for number in executor.TARGET_QUESTIONS}
    return executor.Snapshot(
        revision, tuple(nodes), questions, classification_count, link_count
    )


# --------------------------------------------------------------------------- #
# SQLite helpers
# --------------------------------------------------------------------------- #


def _apply(engine, direction):
    def run(connection):
        context = MigrationContext.configure(connection)
        previous = migration.op
        migration.op = Operations(context)
        try:
            getattr(migration, direction)()
        finally:
            migration.op = previous

    return run


async def _upgrade(engine):
    async with engine.begin() as connection:
        await connection.run_sync(_apply(engine, "upgrade"))


async def _downgrade(engine):
    async with engine.begin() as connection:
        await connection.run_sync(_apply(engine, "downgrade"))


async def _make_db(*, apply_migration=True):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await CurriculumTaxonomyService(session).seed_reference_fixture()
    if apply_migration:
        await _upgrade(engine)
    return engine, factory


async def _seed_question(session, number, canonical_text, content_hash, *, version_id=None):
    institution = Institution(code=f"INST{number}", name="Instituicao")
    session.add(institution)
    await session.flush()
    exam = Exam(institution_id=institution.id, code=f"EX{number}", name="ENEM")
    session.add(exam)
    await session.flush()
    application = ExamApplication(
        exam_id=exam.id, year=2020, application_type="REGULAR", day=2
    )
    session.add(application)
    await session.flush()
    booklet = ExamBooklet(exam_application_id=application.id, code=f"CD{number}")
    session.add(booklet)
    await session.flush()
    question = Question(
        validation_status="validated",
        origin_type="IMPORTED",
        status="DRAFT",
        visibility_scope="PRIVATE",
    )
    session.add(question)
    await session.flush()
    version = QuestionVersion(
        question_id=question.id,
        version_kind="official_original",
        canonical_text=canonical_text,
        content_hash=content_hash,
    )
    if version_id is not None:
        version.id = version_id
    session.add(version)
    await session.flush()
    booklet_question = BookletQuestion(
        exam_booklet_id=booklet.id,
        question_version_id=version.id,
        position=number,
        official_number=number,
    )
    session.add(booklet_question)
    await session.flush()
    await session.commit()
    return question, version


class FakeInitialProvider:
    provider = "openai"

    def __init__(self, response):
        self._response = response
        self.calls = 0

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        self.calls += 1
        return TextGenerationResult(json.dumps(self._response), self.provider, "fake-model")


async def _kinetics_response(session, statement, evidence):
    service = ClassificationProposalService(session)
    catalog = list((await session.scalars(select(CatalogNode))).all())
    candidate = next(
        item
        for item in service.recover_candidates(statement, catalog)
        if item["content_code"] == migration.NODE_CODE
    )
    return {
        "selected_candidate_rank": candidate["rank"],
        "discipline_code": candidate["discipline_code"],
        "area_code": candidate["area_code"],
        "content_code": candidate["content_code"],
        "subcontent_code": candidate["subcontent_code"],
        "confidence": "HIGH",
        "evidence": [{"text": evidence, "reason": "Evidencia de cinetica quimica."}],
        "candidate_classifications": [{**candidate, "rationale": "Candidato controlado."}],
        "complementary_contents": [],
        "catalog_gap": False,
        "gap_type": None,
        "taxonomy_coverage_evidence": ["Vocabulario controlado recuperou o candidato."],
        "review_reason": None,
        "visual_dependency": False,
        "status": "PROPOSED",
    }


# --------------------------------------------------------------------------- #
# Environment / scope / approval / migration chain
# --------------------------------------------------------------------------- #


class EnvironmentScopeApprovalTests(unittest.TestCase):
    def test_environment_absent_fails_closed(self):
        with self.assertRaises(executor.PreFlightError):
            executor.validate_environment({})

    def test_environment_partial_fails_closed(self):
        with self.assertRaises(executor.PreFlightError):
            executor.validate_environment(
                {"DATABASE_URL": "postgresql+psycopg://x/y", "OPENAI_API_KEY": "k"}
            )

    def test_environment_non_postgresql_fails_closed(self):
        with self.assertRaises(executor.PreFlightError):
            executor.validate_environment(
                {
                    "DATABASE_URL": "sqlite://",
                    "OPENAI_API_KEY": "k",
                    "OPENAI_MODEL": "m",
                }
            )

    def test_environment_complete_postgresql_passes(self):
        executor.validate_environment(
            {
                "DATABASE_URL": "postgresql+psycopg://u:p@h:5432/db",
                "OPENAI_API_KEY": "k",
                "OPENAI_MODEL": "m",
            }
        )

    def test_question_scope_rejects_anything_but_93_128(self):
        executor.enforce_question_scope([93, 128])
        for numbers in ([94], [1], [93, 200], [127, 129]):
            with self.subTest(numbers=numbers):
                with self.assertRaises(executor.QuestionScopeError):
                    executor.enforce_question_scope(numbers)

    def test_approval_gate_is_fail_closed(self):
        self.assertFalse(executor.approval_granted({}, interactive=False))
        self.assertFalse(
            executor.approval_granted({executor.APPROVAL_ENV_VAR: "nope"}, interactive=False)
        )
        self.assertTrue(
            executor.approval_granted(
                {executor.APPROVAL_ENV_VAR: executor.APPROVAL_TOKEN}, interactive=False
            )
        )
        self.assertFalse(
            executor.approval_granted({}, interactive=True, prompt=lambda _: "wrong")
        )
        self.assertTrue(
            executor.approval_granted(
                {}, interactive=True, prompt=lambda _: executor.APPROVAL_TOKEN
            )
        )

    def test_local_migration_chain_is_valid(self):
        info = executor.validate_local_migration_chain()
        # The chain's true single head is now 025 (Phase 9U.1-D); this
        # executor's own classification target stays 024, unaffected.
        self.assertEqual(info["heads"], (executor.LIFECYCLE_REVISION,))
        self.assertEqual(info["target_down_revision"], executor.SOURCE_REVISION)
        self.assertEqual(info["lifecycle_down_revision"], executor.TARGET_REVISION)
        self.assertEqual(executor.TARGET_REVISION, "024_chemistry_kinetics")

    def test_local_migration_chain_rejects_broken_down_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "024_bad.py"
            bad.write_text(
                'revision = "024_chemistry_kinetics"\n'
                'down_revision = "999_not_023"\n'
                'PARENT_CODE = "CHEMISTRY-PHYSICAL"\n'
                'NODE_CODE = "CHEMISTRY-PHYSICAL-KINETICS"\n'
                'NODE_NAME = "Cinética química"\n'
                'NODE_DESCRIPTION = "Estudo da velocidade das reações químicas."\n'
                'NODE_TYPE = "CONTENT"\n'
            )
            with patch.object(executor, "MIGRATION_024_FILE", bad):
                with self.assertRaises(executor.PreFlightError):
                    executor.validate_local_migration_chain()

    def test_local_migration_chain_rejects_broken_lifecycle_down_revision(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "025_bad.py"
            bad.write_text(
                'revision = "025_classification_lifecycle"\n'
                'down_revision = "999_not_024"\n'
                'TABLE = "pedagogical_classifications"\n'
                'ACTIVE_UNIQUE_INDEX = "uq_pedagogical_classifications_active_taxonomy"\n'
                'LIFECYCLE_CHECK = "ck_pedagogical_classifications_lifecycle"\n'
                'SUPERSEDES_FK = "fk_pedagogical_classifications_supersedes_id"\n'
            )
            with patch.object(executor, "MIGRATION_025_FILE", bad):
                with self.assertRaises(executor.PreFlightError):
                    executor.validate_local_migration_chain()

    def test_local_migration_chain_rejects_wrong_lifecycle_configuration(self):
        with tempfile.TemporaryDirectory() as tmp:
            bad = Path(tmp) / "025_bad.py"
            bad.write_text(
                'revision = "025_classification_lifecycle"\n'
                'down_revision = "024_chemistry_kinetics"\n'
                'TABLE = "pedagogical_classifications"\n'
                'ACTIVE_UNIQUE_INDEX = "wrong_index_name"\n'
                'LIFECYCLE_CHECK = "ck_pedagogical_classifications_lifecycle"\n'
                'SUPERSEDES_FK = "fk_pedagogical_classifications_supersedes_id"\n'
            )
            with patch.object(executor, "MIGRATION_025_FILE", bad):
                with self.assertRaisesRegex(executor.PreFlightError, "025 is not the approved configuration"):
                    executor.validate_local_migration_chain()

    def test_executor_source_never_references_reclassification(self):
        source = EXECUTOR_PATH.read_text(encoding="utf-8")
        self.assertNotIn("reclassify_with_provider", source)
        self.assertIn("classify_initial_with_provider", source)


# --------------------------------------------------------------------------- #
# Pure pre-flight / post-migration / rollback validators
# --------------------------------------------------------------------------- #


class PreflightValidatorTests(unittest.TestCase):
    def test_expected_state_passes(self):
        executor.validate_preflight(_snapshot())

    def test_expected_state_with_compatible_existing_kinetics_passes(self):
        executor.validate_preflight(
            _snapshot(nodes=[_node("CHEMISTRY", "DISCIPLINE", None), _parent_node(), _kinetics_node()])
        )

    def test_unexpected_revision_fails(self):
        with self.assertRaises(executor.PreFlightError):
            executor.validate_preflight(_snapshot(revision="022_modification_proposals"))

    def test_missing_parent_fails(self):
        with self.assertRaises(executor.PreFlightError):
            executor.validate_preflight(
                _snapshot(nodes=[_node("CHEMISTRY", "DISCIPLINE", None)])
            )

    def test_parent_wrong_type_fails(self):
        with self.assertRaises(executor.PreFlightError):
            executor.validate_preflight(
                _snapshot(nodes=[_node("CHEMISTRY", "DISCIPLINE", None), _parent_node(node_type="CONTENT")])
            )

    def test_parent_inactive_fails(self):
        with self.assertRaises(executor.PreFlightError):
            executor.validate_preflight(
                _snapshot(nodes=[_node("CHEMISTRY", "DISCIPLINE", None), _parent_node(active=False)])
            )

    def test_parent_not_under_chemistry_fails(self):
        with self.assertRaises(executor.PreFlightError):
            executor.validate_preflight(
                _snapshot(nodes=[_node("CHEMISTRY", "DISCIPLINE", None), _parent_node(parent_code="PHYSICS")])
            )

    def test_incompatible_existing_kinetics_fails(self):
        with self.assertRaises(executor.PreFlightError):
            executor.validate_preflight(
                _snapshot(
                    nodes=[
                        _node("CHEMISTRY", "DISCIPLINE", None),
                        _parent_node(),
                        _kinetics_node(node_type="SUBCONTENT"),
                    ]
                )
            )

    def test_missing_question_fails(self):
        questions = {93: {"present": False}, 128: _ok_question(128)}
        with self.assertRaises(executor.PreFlightError):
            executor.validate_preflight(_snapshot(questions=questions))

    def test_question_without_content_fails(self):
        broken = _ok_question(128)
        broken["content_available"] = False
        with self.assertRaises(executor.PreFlightError):
            executor.validate_preflight(_snapshot(questions={93: _ok_question(93), 128: broken}))

    def test_preexisting_target_proposal_fails(self):
        dirty = _ok_question(93)
        dirty["target_024"] = [{"id": "x", "taxonomy_version": "024_chemistry_kinetics"}]
        with self.assertRaises(executor.PreFlightError):
            executor.validate_preflight(_snapshot(questions={93: dirty, 128: _ok_question(128)}))

    def test_historical_023_proposal_routes_to_review(self):
        dirty = _ok_question(128)
        dirty["historical_023"] = [{"id": "y", "taxonomy_version": "023_curriculum_taxonomy"}]
        with self.assertRaises(executor.PreFlightError):
            executor.validate_preflight(_snapshot(questions={93: _ok_question(93), 128: dirty}))

    def test_absent_history_is_accepted_for_initial(self):
        # Every target question has empty historical_023 -> must pass.
        executor.validate_preflight(_snapshot())


class PostMigrationValidatorTests(unittest.TestCase):
    def _before(self):
        return _snapshot()

    def _after_ok(self):
        return _snapshot(
            revision="024_chemistry_kinetics",
            nodes=[_node("CHEMISTRY", "DISCIPLINE", None), _parent_node(), _kinetics_node()],
        )

    def test_single_node_delta_passes(self):
        executor.validate_post_migration(self._before(), self._after_ok())

    def test_revision_not_advanced_fails(self):
        after = _snapshot(
            nodes=[_node("CHEMISTRY", "DISCIPLINE", None), _parent_node(), _kinetics_node()]
        )
        with self.assertRaises(executor.PostMigrationError):
            executor.validate_post_migration(self._before(), after)

    def test_missing_new_node_fails(self):
        after = _snapshot(revision="024_chemistry_kinetics")
        with self.assertRaises(executor.PostMigrationError):
            executor.validate_post_migration(self._before(), after)

    def test_preexisting_node_mutated_fails(self):
        after = _snapshot(
            revision="024_chemistry_kinetics",
            nodes=[_node("CHEMISTRY", "DISCIPLINE", None), _parent_node(active=False), _kinetics_node()],
        )
        with self.assertRaises(executor.PostMigrationError):
            executor.validate_post_migration(self._before(), after)

    def test_extra_unexpected_node_fails(self):
        after = _snapshot(
            revision="024_chemistry_kinetics",
            nodes=[
                _node("CHEMISTRY", "DISCIPLINE", None),
                _parent_node(),
                _kinetics_node(),
                _node("CHEMISTRY-EXTRA", "CONTENT", "CHEMISTRY-PHYSICAL"),
            ],
        )
        with self.assertRaises(executor.PostMigrationError):
            executor.validate_post_migration(self._before(), after)

    def test_link_count_change_fails(self):
        after = _snapshot(
            revision="024_chemistry_kinetics",
            nodes=[_node("CHEMISTRY", "DISCIPLINE", None), _parent_node(), _kinetics_node()],
            link_count=1,
        )
        with self.assertRaises(executor.PostMigrationError):
            executor.validate_post_migration(self._before(), after)

    def test_classification_count_change_fails(self):
        after = _snapshot(
            revision="024_chemistry_kinetics",
            nodes=[_node("CHEMISTRY", "DISCIPLINE", None), _parent_node(), _kinetics_node()],
            classification_count=5,
        )
        with self.assertRaises(executor.PostMigrationError):
            executor.validate_post_migration(self._before(), after)


class RollbackValidatorTests(unittest.TestCase):
    def test_kinetics_dependency_detection(self):
        self.assertFalse(executor.kinetics_has_dependencies(_snapshot(link_count=0)))
        self.assertTrue(executor.kinetics_has_dependencies(_snapshot(link_count=1)))

    def test_clean_rollback_passes(self):
        before = _snapshot()
        reverted = _snapshot()
        executor.validate_rollback(before, reverted)

    def test_rollback_still_on_target_fails(self):
        before = _snapshot()
        reverted = _snapshot(
            revision="024_chemistry_kinetics",
            nodes=[_node("CHEMISTRY", "DISCIPLINE", None), _parent_node(), _kinetics_node()],
        )
        with self.assertRaises(executor.RollbackError):
            executor.validate_rollback(before, reverted)

    def test_rollback_leaves_kinetics_fails(self):
        before = _snapshot()
        reverted = _snapshot(nodes=[_node("CHEMISTRY", "DISCIPLINE", None), _parent_node(), _kinetics_node()])
        with self.assertRaises(executor.RollbackError):
            executor.validate_rollback(before, reverted)

    def test_rollback_mutated_preexisting_node_fails(self):
        before = _snapshot()
        reverted = _snapshot(nodes=[_node("CHEMISTRY", "DISCIPLINE", None), _parent_node(active=False)])
        with self.assertRaises(executor.RollbackError):
            executor.validate_rollback(before, reverted)


class _QueuedReader:
    def __init__(self, snapshots):
        self._snapshots = list(snapshots)
        self.calls = 0

    async def __call__(self):
        snapshot = self._snapshots[min(self.calls, len(self._snapshots) - 1)]
        self.calls += 1
        return snapshot


class PerformRollbackTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.before = _snapshot()
        self.after_upgrade = _snapshot(
            revision="024_chemistry_kinetics",
            nodes=[_node("CHEMISTRY", "DISCIPLINE", None), _parent_node(), _kinetics_node()],
        )
        self.downgrade_calls = []

    def _run_downgrade(self):
        self.downgrade_calls.append(1)

    async def test_not_applied_by_executor_skips_downgrade(self):
        report = await executor.perform_rollback(
            migration_applied_by_executor=False,
            before=self.before,
            run_downgrade=self._run_downgrade,
            snapshot_reader=_QueuedReader([self.before]),
        )
        self.assertEqual(report.status, "NOT_APPLIED")
        self.assertEqual(self.downgrade_calls, [])

    async def test_dependencies_block_downgrade(self):
        dependent = _snapshot(
            revision="024_chemistry_kinetics",
            nodes=[_node("CHEMISTRY", "DISCIPLINE", None), _parent_node(), _kinetics_node()],
            link_count=1,
        )
        report = await executor.perform_rollback(
            migration_applied_by_executor=True,
            before=self.before,
            run_downgrade=self._run_downgrade,
            snapshot_reader=_QueuedReader([dependent]),
        )
        self.assertEqual(report.status, "BLOCKED_DEPENDENCIES")
        self.assertEqual(self.downgrade_calls, [])

    async def test_clean_rollback_completes(self):
        report = await executor.perform_rollback(
            migration_applied_by_executor=True,
            before=self.before,
            run_downgrade=self._run_downgrade,
            snapshot_reader=_QueuedReader([self.after_upgrade, self.before]),
        )
        self.assertEqual(report.status, "COMPLETED")
        self.assertEqual(self.downgrade_calls, [1])

    async def test_downgrade_raising_is_reported_not_masked(self):
        def _boom():
            raise RuntimeError("Cannot downgrade taxonomy node with dependencies")

        report = await executor.perform_rollback(
            migration_applied_by_executor=True,
            before=self.before,
            run_downgrade=_boom,
            snapshot_reader=_QueuedReader([self.after_upgrade]),
        )
        self.assertEqual(report.status, "BLOCKED_DEPENDENCIES")

    async def test_unsafe_residual_state_is_reported(self):
        report = await executor.perform_rollback(
            migration_applied_by_executor=True,
            before=self.before,
            run_downgrade=self._run_downgrade,
            snapshot_reader=_QueuedReader([self.after_upgrade, self.after_upgrade]),
        )
        self.assertEqual(report.status, "UNSAFE")


# --------------------------------------------------------------------------- #
# INITIAL classification against SQLite + real 024 migration
# --------------------------------------------------------------------------- #


class InitialClassificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db(apply_migration=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _provider_factory(self, statement, evidence):
        async with self.factory() as session:
            response = await _kinetics_response(session, statement, evidence)
        return lambda: FakeInitialProvider(response)

    async def test_question_93_initial_classification_passes(self):
        async with self.factory() as session:
            await _seed_question(session, 93, TEXT_93, "hash-93")
        factory = await self._provider_factory(TEXT_93, "velocidade da reação")
        async with self.factory() as session:
            summary = await executor.classify_initial(session, 93, provider_factory=factory)
        self.assertEqual(summary["classification_mode"], "INITIAL")
        self.assertEqual(summary["taxonomy_version"], "024_chemistry_kinetics")
        self.assertEqual(summary["content_code"], migration.NODE_CODE)
        self.assertEqual(summary["provider_calls"], 1)
        self.assertFalse(summary["already_existed"])
        async with self.factory() as session:
            total = await session.scalar(select(func.count()).select_from(PedagogicalClassification))
        self.assertEqual(total, 1)

    async def test_question_128_initial_classification_passes(self):
        async with self.factory() as session:
            await _seed_question(session, 128, TEXT_128, "hash-128")
        factory = await self._provider_factory(TEXT_128, "estudo cinético")
        async with self.factory() as session:
            summary = await executor.classify_initial(session, 128, provider_factory=factory)
        self.assertEqual(summary["classification_mode"], "INITIAL")
        self.assertEqual(summary["content_code"], migration.NODE_CODE)
        self.assertEqual(summary["provider_calls"], 1)

    async def test_initial_does_not_require_historical_proposal(self):
        async with self.factory() as session:
            _, version = await _seed_question(session, 93, TEXT_93, "hash-93")
            existing = await session.scalar(
                select(func.count())
                .select_from(PedagogicalClassification)
                .where(PedagogicalClassification.question_version_id == version.id)
            )
        self.assertEqual(existing, 0)
        factory = await self._provider_factory(TEXT_93, "velocidade da reação")
        async with self.factory() as session:
            summary = await executor.classify_initial(session, 93, provider_factory=factory)
        self.assertEqual(summary["classification_mode"], "INITIAL")

    async def test_initial_requires_target_taxonomy_024(self):
        engine, factory = await _make_db(apply_migration=False)
        try:
            async with factory() as session:
                await _seed_question(session, 93, TEXT_93, "hash-93")
            provider = FakeInitialProvider({})
            async with factory() as session:
                with self.assertRaises(ValueError):
                    await executor.classify_initial(
                        session, 93, provider_factory=lambda: provider
                    )
            self.assertEqual(provider.calls, 0)
        finally:
            await engine.dispose()

    async def test_idempotent_second_run_reuses_row_without_provider(self):
        async with self.factory() as session:
            await _seed_question(session, 93, TEXT_93, "hash-93")
        factory = await self._provider_factory(TEXT_93, "velocidade da reação")
        async with self.factory() as session:
            first = await executor.classify_initial(session, 93, provider_factory=factory)
        async with self.factory() as session:
            second = await executor.classify_initial(session, 93, provider_factory=factory)
        self.assertEqual(first["classification_id"], second["classification_id"])
        self.assertEqual(first["input_hash"], second["input_hash"])
        self.assertEqual(first["output_hash"], second["output_hash"])
        self.assertTrue(second["already_existed"])
        async with self.factory() as session:
            total = await session.scalar(select(func.count()).select_from(PedagogicalClassification))
        self.assertEqual(total, 1)

    async def test_hashes_are_deterministic(self):
        # 1. the hash primitive is order-independent and stable.
        left = ClassificationProposalService._hash({"a": 1, "b": [2, 3], "c": "x"})
        right = ClassificationProposalService._hash({"c": "x", "b": [2, 3], "a": 1})
        self.assertEqual(left, right)
        self.assertEqual(left, ClassificationProposalService._hash({"a": 1, "b": [2, 3], "c": "x"}))

        # 2. the persisted decision hash is stable across independent databases
        #    seeded identically; the input hash is stable on repeat within a database.
        fixed = uuid.UUID(int=0x9051)
        output_hashes = []
        for _ in range(2):
            engine, factory = await _make_db(apply_migration=True)
            try:
                async with factory() as session:
                    await _seed_question(session, 93, TEXT_93, "stable-hash", version_id=fixed)
                async with factory() as session:
                    response = await _kinetics_response(session, TEXT_93, "velocidade da reação")
                async with factory() as session:
                    first = await executor.classify_initial(
                        session, 93, provider_factory=lambda r=response: FakeInitialProvider(r)
                    )
                async with factory() as session:
                    second = await executor.classify_initial(
                        session, 93, provider_factory=lambda r=response: FakeInitialProvider(r)
                    )
                self.assertEqual(first["input_hash"], second["input_hash"])
                self.assertEqual(first["output_hash"], second["output_hash"])
                self.assertTrue(first["input_hash"] and first["output_hash"])
                output_hashes.append(first["output_hash"])
            finally:
                await engine.dispose()
        self.assertEqual(output_hashes[0], output_hashes[1])

    async def test_non_recovered_candidate_is_rejected(self):
        async with self.factory() as session:
            await _seed_question(session, 93, TEXT_93, "hash-93")
        async with self.factory() as session:
            response = await _kinetics_response(session, TEXT_93, "velocidade da reação")
        response["content_code"] = "CHEMISTRY-SOLUTIONS"
        async with self.factory() as session:
            with self.assertRaisesRegex(ValueError, "was not recovered"):
                await executor.classify_initial(
                    session, 93, provider_factory=lambda: FakeInitialProvider(response)
                )
        async with self.factory() as session:
            total = await session.scalar(select(func.count()).select_from(PedagogicalClassification))
        self.assertEqual(total, 0)

    async def test_invalid_evidence_is_rejected(self):
        async with self.factory() as session:
            await _seed_question(session, 128, TEXT_128, "hash-128")
        async with self.factory() as session:
            response = await _kinetics_response(session, TEXT_128, "estudo cinético")
        response["evidence"] = []
        async with self.factory() as session:
            with self.assertRaisesRegex(ValueError, "evidence"):
                await executor.classify_initial(
                    session, 128, provider_factory=lambda: FakeInitialProvider(response)
                )
        async with self.factory() as session:
            total = await session.scalar(select(func.count()).select_from(PedagogicalClassification))
        self.assertEqual(total, 0)

    async def test_question_scope_enforced_before_any_work(self):
        async with self.factory() as session:
            with self.assertRaises(executor.QuestionScopeError):
                await executor.classify_initial(
                    session, 94, provider_factory=lambda: FakeInitialProvider({})
                )

    async def test_executor_never_invokes_reclassification(self):
        async with self.factory() as session:
            await _seed_question(session, 93, TEXT_93, "hash-93")
            await _seed_question(session, 128, TEXT_128, "hash-128")
        guard = AsyncMock(side_effect=AssertionError("reclassify_with_provider was called"))
        with patch.object(ClassificationProposalService, "reclassify_with_provider", guard):
            for number, statement, evidence in (
                (93, TEXT_93, "velocidade da reação"),
                (128, TEXT_128, "estudo cinético"),
            ):
                factory = await self._provider_factory(statement, evidence)
                async with self.factory() as session:
                    summary = await executor.classify_initial(
                        session, number, provider_factory=factory
                    )
                self.assertEqual(summary["classification_mode"], "INITIAL")
        guard.assert_not_called()


class MigrationRollbackOnSqliteTests(unittest.IsolatedAsyncioTestCase):
    async def test_end_to_end_upgrade_then_downgrade_snapshots_are_consistent(self):
        engine, factory = await _make_db(apply_migration=False)
        try:
            async with factory() as session:
                await _seed_question(session, 93, TEXT_93, "hash-93")
                await _seed_question(session, 128, TEXT_128, "hash-128")
            async with factory() as session:
                before = await executor.read_snapshot(
                    session, revision="023_curriculum_taxonomy"
                )
            await _upgrade(engine)
            async with factory() as session:
                after_upgrade = await executor.read_snapshot(
                    session, revision="024_chemistry_kinetics"
                )
            executor.validate_post_migration(before, after_upgrade)
            await _downgrade(engine)
            async with factory() as session:
                reverted = await executor.read_snapshot(
                    session, revision="023_curriculum_taxonomy"
                )
            executor.validate_rollback(before, reverted)
        finally:
            await engine.dispose()

    async def test_downgrade_refuses_to_remove_node_with_dependency(self):
        engine, factory = await _make_db(apply_migration=True)
        try:
            async with factory() as session:
                _, version = await _seed_question(session, 93, TEXT_93, "hash-93")
                kinetics = await session.scalar(
                    select(CatalogNode).where(CatalogNode.code == migration.NODE_CODE)
                )
                session.add(
                    ContentQuestionLink(
                        content_node_id=kinetics.id, question_version_id=version.id
                    )
                )
                await session.commit()
            with self.assertRaisesRegex(RuntimeError, "with dependencies"):
                await _downgrade(engine)
            async with factory() as session:
                still_there = await session.scalar(
                    select(CatalogNode).where(CatalogNode.code == migration.NODE_CODE)
                )
            self.assertIsNotNone(still_there)
        finally:
            await engine.dispose()


# --------------------------------------------------------------------------- #
# Sanitised PRE-FLIGHT report shown before the approval gate
# --------------------------------------------------------------------------- #


REQUIRED_REPORT_FIELDS = (
    "PRE-FLIGHT",
    "SOURCE_REVISION",
    "TARGET_REVISION",
    "CATALOG_NODE_COUNT",
    "PARENT_EXISTS",
    "PARENT_CODE",
    "PARENT_TYPE",
    "PARENT_ACTIVE",
    "KINETICS_NODE_EXISTS",
    "KINETICS_NODE_STATE",
    "QUESTION_93",
    "QUESTION_128",
    "QUESTION_VERSION_COUNTS",
    "EXISTING_CLASSIFICATIONS",
    "HISTORICAL_INTEGRITY_STATUS",
    "MIGRATION_LOCAL_CHAIN",
    "OPENAI_CALLS",
    "POSTGRESQL_READS",
    "POSTGRESQL_WRITES",
    "MIGRATION_EXECUTION",
    "DATABASE_WRITES",
    "UNEXPECTED_CHANGES",
)

_FULL_HASH_A = "a" * 64
_FULL_HASH_B = "b" * 64


def _classification_row(**overrides):
    base = {
        "id": "11111111-1111-1111-1111-111111111111",
        "question_version_id": "22222222-2222-2222-2222-222222222222",
        "status": "CLASSIFIED",
        "source": "ai",
        "discipline": "CHEMISTRY",
        "content": "CHEMISTRY-PHYSICAL-KINETICS",
        "subcontent": "",
        "model_version": "phase9u1-initial-v1",
        "prompt_version": "phase9t3-kinetics-v1",
        "taxonomy_version": "024_chemistry_kinetics",
        "classification_mode": "INITIAL",
        "input_hash": _FULL_HASH_A,
        "output_hash": _FULL_HASH_B,
        "selected_candidate_rank": 1,
        "recovered_candidate_count": 2,
        "has_reclassification_audit": False,
        "created_at": "2026-09-04T00:00:00+00:00",
    }
    base.update(overrides)
    return base


def _question_with(**overrides):
    entry = _ok_question(93)
    entry.update(overrides)
    return entry


def _report_snapshot(*, questions=None):
    nodes = [
        _node("CHEMISTRY", "DISCIPLINE", None),
        _parent_node(),
        _kinetics_node(),
    ]
    if questions is None:
        questions = {93: _ok_question(93), 128: _ok_question(128)}
    return executor.Snapshot(
        "023_curriculum_taxonomy", tuple(nodes), questions,
        classification_count=0, content_question_link_count=0,
    )


CHAIN_INFO = {"heads": ("024_chemistry_kinetics",), "target_down_revision": "023_curriculum_taxonomy"}


def _walk_strings(value):
    if isinstance(value, str):
        yield value
    elif isinstance(value, dict):
        for item in value.values():
            yield from _walk_strings(item)
    elif isinstance(value, (list, tuple)):
        for item in value:
            yield from _walk_strings(item)


class PreflightReportTests(unittest.TestCase):
    def test_report_has_every_required_field(self):
        report = executor.build_preflight_report(
            _report_snapshot(), status="PASS", chain_info=CHAIN_INFO, postgresql_reads=1
        )
        self.assertEqual(tuple(report.keys()), REQUIRED_REPORT_FIELDS)

    def test_status_field_reflects_argument(self):
        for status in ("PASS", "FAIL"):
            report = executor.build_preflight_report(
                _report_snapshot(), status=status, chain_info=CHAIN_INFO, postgresql_reads=1
            )
            self.assertEqual(report["PRE-FLIGHT"], status)

    def test_fixed_counters_are_zero_and_reads_passthrough(self):
        report = executor.build_preflight_report(
            _report_snapshot(), status="PASS", chain_info=CHAIN_INFO, postgresql_reads=3
        )
        self.assertEqual(report["OPENAI_CALLS"], 0)
        self.assertEqual(report["POSTGRESQL_WRITES"], 0)
        self.assertEqual(report["MIGRATION_EXECUTION"], 0)
        self.assertEqual(report["DATABASE_WRITES"], 0)
        self.assertEqual(report["POSTGRESQL_READS"], 3)
        self.assertEqual(report["UNEXPECTED_CHANGES"], [])

    def test_structural_fields_are_populated(self):
        report = executor.build_preflight_report(
            _report_snapshot(), status="PASS", chain_info=CHAIN_INFO, postgresql_reads=1
        )
        self.assertEqual(report["SOURCE_REVISION"], "023_curriculum_taxonomy")
        self.assertEqual(report["TARGET_REVISION"], "024_chemistry_kinetics")
        self.assertEqual(report["CATALOG_NODE_COUNT"], 3)
        self.assertTrue(report["PARENT_EXISTS"])
        self.assertEqual(report["PARENT_CODE"], "CHEMISTRY-PHYSICAL")
        self.assertEqual(report["PARENT_TYPE"], "AREA")
        self.assertTrue(report["PARENT_ACTIVE"])
        self.assertTrue(report["KINETICS_NODE_EXISTS"])
        self.assertEqual(report["KINETICS_NODE_STATE"]["code"], "CHEMISTRY-PHYSICAL-KINETICS")
        self.assertTrue(report["QUESTION_93"]["present"])
        self.assertTrue(report["QUESTION_128"]["present"])
        self.assertEqual(report["QUESTION_VERSION_COUNTS"], {"93": 1, "128": 1})
        self.assertEqual(report["MIGRATION_LOCAL_CHAIN"]["heads"], ["024_chemistry_kinetics"])
        self.assertEqual(
            report["MIGRATION_LOCAL_CHAIN"]["target_down_revision"], "023_curriculum_taxonomy"
        )
        self.assertEqual(report["HISTORICAL_INTEGRITY_STATUS"]["state"], "BASELINE_RECORDED")

    def test_report_never_contains_secrets_or_full_hashes(self):
        questions = {
            93: _question_with(
                content_hash="c" * 64,
                classifications=[_classification_row()],
                historical_023=[],
                target_024=[],
            ),
            128: _ok_question(128),
        }
        report = executor.build_preflight_report(
            _report_snapshot(questions=questions),
            status="PASS",
            chain_info=CHAIN_INFO,
            postgresql_reads=1,
        )
        blob = json.dumps(report, ensure_ascii=False)
        for forbidden in ("DATABASE_URL", "OPENAI_API_KEY", "password", "canonical_text", "statement"):
            self.assertNotIn(forbidden, blob)
        for full_hash in (_FULL_HASH_A, _FULL_HASH_B, "c" * 64):
            self.assertNotIn(full_hash, blob)
        for text_value in _walk_strings(report):
            self.assertIsNone(
                re.fullmatch(r"[0-9a-f]{64}", text_value),
                msg=f"a full sha256 leaked into the report: {text_value!r}",
            )

    def test_classification_and_content_hashes_are_truncated(self):
        questions = {
            93: _question_with(
                content_hash="d" * 40,
                classifications=[_classification_row()],
            ),
            128: _ok_question(128),
        }
        report = executor.build_preflight_report(
            _report_snapshot(questions=questions),
            status="PASS",
            chain_info=CHAIN_INFO,
            postgresql_reads=1,
        )
        row = report["EXISTING_CLASSIFICATIONS"]["93"][0]
        self.assertEqual(row["input_hash_prefix"], "a" * 12 + "…")
        self.assertEqual(row["output_hash_prefix"], "b" * 12 + "…")
        self.assertNotIn("input_hash", row)
        self.assertNotIn("output_hash", row)
        self.assertEqual(report["QUESTION_93"]["content_hash_prefix"], "d" * 12 + "…")

    def test_emit_prints_sanitised_report_before_prompt(self):
        report = executor.build_preflight_report(
            _report_snapshot(
                questions={
                    93: _question_with(classifications=[_classification_row()]),
                    128: _ok_question(128),
                }
            ),
            status="PASS",
            chain_info=CHAIN_INFO,
            postgresql_reads=1,
        )
        buffer = io.StringIO()
        with contextlib.redirect_stdout(buffer):
            executor.emit_preflight_report(report)
        printed = buffer.getvalue()
        self.assertIn("PHASE 9U.1 — PRODUCTION INITIAL CLASSIFICATION PRE-FLIGHT REPORT", printed)
        self.assertIn('PRE-FLIGHT: "PASS"', printed)
        self.assertIn("SOURCE_REVISION:", printed)
        self.assertIn("UNEXPECTED_CHANGES:", printed)
        self.assertNotIn(_FULL_HASH_A, printed)
        self.assertNotIn("OPENAI_API_KEY", printed)

    def test_source_emits_report_before_approval_gate(self):
        source = EXECUTOR_PATH.read_text(encoding="utf-8")
        main_src = source[source.index("async def main"):]
        idx_build = main_src.index("build_preflight_report(")
        idx_emit = main_src.index("emit_preflight_report(")
        idx_status_guard = main_src.index('if preflight_status != "PASS":')
        idx_approval = main_src.index("approval_granted(")
        self.assertLess(idx_build, idx_approval)
        self.assertLess(idx_emit, idx_approval)
        self.assertLess(idx_status_guard, idx_approval)

    def test_source_blocks_approval_until_preflight_pass(self):
        source = EXECUTOR_PATH.read_text(encoding="utf-8")
        main_src = source[source.index("async def main"):]
        # validate_preflight runs (and raises on failure) before the gate, and an
        # explicit status guard raises before approval_granted is ever called.
        self.assertLess(
            main_src.index("validate_preflight(before)"), main_src.index("approval_granted(")
        )
        guard = main_src.index('if preflight_status != "PASS":')
        raise_stmt = main_src.index("the approval gate is not offered", guard)
        self.assertLess(raise_stmt, main_src.index("approval_granted("))


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
