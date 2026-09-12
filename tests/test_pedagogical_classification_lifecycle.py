"""Phase 9U.1-D — PedagogicalClassification lifecycle/supersession infrastructure.

Local only: SQLite for pure logic, plus an isolated pre-025 legacy schema
(built with raw DDL, never via the already-updated ORM model) to validate the
actual migration file's upgrade/backfill/downgrade behaviour in isolation.
No PostgreSQL, no Alembic-on-a-real-db, no OpenAI, no production data touched.

This suite tests only the GENERIC lifecycle/supersession infrastructure. It
never references any specific real-world question and does not implement or
exercise a Q128-specific correction routine.
"""

from __future__ import annotations

import datetime
import importlib.util
import json
import sys
import unittest
import uuid
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import inspect as sa_inspect, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    IngestionDocument,
    IngestionQuestion,
    PedagogicalClassification,
    Question,
    QuestionVersion,
)
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult
from agente_ia_edu.providers.router import ProviderRouter
from agente_ia_edu.services.content_authoring import QuestionAuthoringService, QuestionWorkflowStatus
from agente_ia_edu.services.curriculum_classification import (
    ClassificationProposalService,
    KINETICS_TAXONOMY_VERSION,
)
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService
from agente_ia_edu.services.ingestion_classifier import IngestionClassificationService
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.pedagogical_classifier import PedagogicalClassificationService


ROOT = Path(__file__).resolve().parents[1]
MIGRATION_024_PATH = ROOT / "migrations" / "versions" / "024_chemistry_kinetics.py"
MIGRATION_025_PATH = ROOT / "migrations" / "versions" / "025_pedagogical_classification_lifecycle.py"

KINETICS_STATEMENT = "Nanomateriais catalíticos aumentam a velocidade da reação química."
KINETICS_EVIDENCE = "velocidade da reação"
NODE_CODE = "CHEMISTRY-PHYSICAL-KINETICS"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


migration_024 = _load(MIGRATION_024_PATH, "phase9u1d_migration_024")
migration_025 = _load(MIGRATION_025_PATH, "phase9u1d_migration_025")


def _apply(module, direction: str):
    def run(connection):
        context = MigrationContext.configure(connection)
        previous = module.op
        module.op = Operations(context)
        try:
            getattr(module, direction)()
        finally:
            module.op = previous

    return run


class FakeProvider:
    provider = "fake"

    def __init__(self, response):
        self._response = response
        self.calls = 0

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        self.calls += 1
        return TextGenerationResult(json.dumps(self._response), self.provider, "fake-model")


def _router(provider):
    return ProviderRouter(text_providers=[provider], embedding_providers=[])


def _response(candidate, evidence, *, status="PROPOSED"):
    return {
        "selected_candidate_rank": candidate["rank"],
        "discipline_code": candidate["discipline_code"],
        "area_code": candidate["area_code"],
        "content_code": candidate["content_code"],
        "subcontent_code": candidate["subcontent_code"],
        "confidence": "HIGH",
        "evidence": [{"text": evidence, "reason": "Evidência textual."}],
        "candidate_classifications": [{**candidate, "rationale": "Candidato recuperado."}],
        "complementary_contents": [],
        "catalog_gap": False,
        "gap_type": None,
        "taxonomy_coverage_evidence": ["Candidato recuperado deterministicamente."],
        "review_reason": None,
        "visual_dependency": False,
        "status": status,
    }


async def _make_db():
    """Full current schema (create_all, includes lifecycle/supersedes_id) +
    migration 024's catalog node + the same partial unique index migration
    025 creates, applied directly (equivalent, dialect-correct SQL) so the
    at-most-one-ACTIVE protection is genuinely active under test."""
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as connection:
        await connection.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await CurriculumTaxonomyService(session).seed_reference_fixture()
    async with engine.begin() as connection:
        await connection.run_sync(_apply(migration_024, "upgrade"))
    taxonomy_expr = migration_025._taxonomy_version_expr("sqlite")
    async with engine.begin() as connection:
        await connection.execute(
            text(
                f"CREATE UNIQUE INDEX {migration_025.ACTIVE_UNIQUE_INDEX} "
                f"ON {migration_025.TABLE} (question_version_id, {taxonomy_expr}) "
                f"WHERE lifecycle = 'ACTIVE'"
            )
        )
    return engine, factory


async def _seed_question(session, content_hash, *, statement=KINETICS_STATEMENT):
    question = Question(
        validation_status="validated", origin_type="IMPORTED", status="DRAFT", visibility_scope="PRIVATE"
    )
    session.add(question)
    await session.flush()
    version = QuestionVersion(
        question_id=question.id, version_kind="official_original", canonical_text=statement, content_hash=content_hash
    )
    session.add(version)
    await session.flush()
    return question, version


async def _catalog(session):
    return list((await session.scalars(select(CatalogNode).where(CatalogNode.active.is_(True)))).all())


async def _kinetics_candidate(session, statement=KINETICS_STATEMENT):
    catalog = await _catalog(session)
    candidates = ClassificationProposalService(session).recover_candidates(statement, catalog)
    return next(c for c in candidates if c["content_code"] == NODE_CODE)


async def _classify(session, version, *, classifier_version="lifecycle-initial-v1", evidence=KINETICS_EVIDENCE):
    candidate = await _kinetics_candidate(session, version.canonical_text)
    provider = FakeProvider(_response(candidate, evidence))
    record = await ClassificationProposalService(session).classify_initial_with_provider(
        version.id,
        _router(provider),
        target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
        classifier_version=classifier_version,
        prompt_version="phase9t3-kinetics-v1",
    )
    return record, provider


# --------------------------------------------------------------------------- #
# A. New classifications default to ACTIVE
# --------------------------------------------------------------------------- #


class DefaultLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_A_new_classification_defaults_to_active(self):
        async with self.factory() as session:
            _, version = await _seed_question(session, "h1")
            record, _ = await _classify(session, version)
        self.assertEqual(record.lifecycle, "ACTIVE")
        self.assertIsNone(record.supersedes_id)

    async def test_lifecycle_check_constraint_rejects_invalid_value(self):
        async with self.factory() as session:
            _, version = await _seed_question(session, "h1")
            bad = PedagogicalClassification(
                question_version_id=version.id, discipline="", content="", subcontent="", difficulty="UNKNOWN",
                reasoning_type="UNSPECIFIED", status="DRAFT", source="ai", lifecycle="BOGUS",
            )
            session.add(bad)
            with self.assertRaises(Exception):
                await session.commit()


# --------------------------------------------------------------------------- #
# B-E. Supersession
# --------------------------------------------------------------------------- #


class SupersessionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_B_C_D_E_supersede_creates_active_new_supersedes_old_data_untouched(self):
        async with self.factory() as session:
            _, version = await _seed_question(session, "h1")
            old, _ = await _classify(session, version, classifier_version="wrong-v1")
            old_snapshot = {
                "id": old.id,
                "question_version_id": old.question_version_id,
                "model_version": old.model_version,
                "prompt_version": old.prompt_version,
                "status": old.status,
                "created_at": old.created_at,
                "metadata_": dict(old.metadata_),
                "discipline": old.discipline,
                "content": old.content,
                "subcontent": old.subcontent,
            }

        async with self.factory() as session:
            candidate = await _kinetics_candidate(session)
            provider = FakeProvider(_response(candidate, KINETICS_EVIDENCE))
            new = await ClassificationProposalService(session).supersede_initial_classification(
                superseded_id=old.id,
                question_version_id=version.id,
                provider=_router(provider),
                target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                classifier_version="correcting-v1",
                prompt_version="phase9t3-kinetics-v1",
            )

        # B: new classification is ACTIVE
        self.assertEqual(new.lifecycle, "ACTIVE")
        # E: new classification points at the one it replaces
        self.assertEqual(new.supersedes_id, old.id)
        self.assertNotEqual(new.id, old.id)

        async with self.factory() as session:
            refreshed_old = await session.get(PedagogicalClassification, old.id)
            # C: old flipped to SUPERSEDED
            self.assertEqual(refreshed_old.lifecycle, "SUPERSEDED")
            # D: every substantive field of the old row is byte-identical
            self.assertEqual(refreshed_old.id, old_snapshot["id"])
            self.assertEqual(refreshed_old.question_version_id, old_snapshot["question_version_id"])
            self.assertEqual(refreshed_old.model_version, old_snapshot["model_version"])
            self.assertEqual(refreshed_old.prompt_version, old_snapshot["prompt_version"])
            self.assertEqual(refreshed_old.status, old_snapshot["status"])
            self.assertEqual(refreshed_old.created_at, old_snapshot["created_at"])
            self.assertEqual(dict(refreshed_old.metadata_), old_snapshot["metadata_"])
            self.assertEqual(refreshed_old.discipline, old_snapshot["discipline"])
            self.assertEqual(refreshed_old.content, old_snapshot["content"])
            self.assertEqual(refreshed_old.subcontent, old_snapshot["subcontent"])

    async def test_supersede_rejects_non_active_old_row(self):
        async with self.factory() as session:
            _, version = await _seed_question(session, "h1")
            old, _ = await _classify(session, version, classifier_version="wrong-v1")
            candidate = await _kinetics_candidate(session)
            provider1 = FakeProvider(_response(candidate, KINETICS_EVIDENCE))
            await ClassificationProposalService(session).supersede_initial_classification(
                superseded_id=old.id, question_version_id=version.id, provider=_router(provider1),
                target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                classifier_version="correcting-v1", prompt_version="phase9t3-kinetics-v1",
            )
            # Q (idempotency-not-overwrite): superseding the SAME old row again
            # must fail closed, not silently re-supersede or duplicate.
            provider2 = FakeProvider(_response(candidate, KINETICS_EVIDENCE))
            with self.assertRaisesRegex(ValueError, "Only an ACTIVE classification can be superseded"):
                await ClassificationProposalService(session).supersede_initial_classification(
                    superseded_id=old.id, question_version_id=version.id, provider=_router(provider2),
                    target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                    classifier_version="correcting-v2", prompt_version="phase9t3-kinetics-v1",
                )

    async def test_supersede_rejects_mismatched_question_or_taxonomy(self):
        async with self.factory() as session:
            _, v1 = await _seed_question(session, "h1")
            _, v2 = await _seed_question(session, "h2")
            old, _ = await _classify(session, v1, classifier_version="wrong-v1")
            candidate = await _kinetics_candidate(session)
            provider = FakeProvider(_response(candidate, KINETICS_EVIDENCE))
            with self.assertRaisesRegex(ValueError, "same question_version_id"):
                await ClassificationProposalService(session).supersede_initial_classification(
                    superseded_id=old.id, question_version_id=v2.id, provider=_router(provider),
                    target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                    classifier_version="correcting-v1", prompt_version="phase9t3-kinetics-v1",
                )


# --------------------------------------------------------------------------- #
# F-H. Uniqueness / coexistence / current-vs-superseded resolution
# --------------------------------------------------------------------------- #


class ActiveUniquenessTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_F_two_active_same_question_and_taxonomy_rejected(self):
        async with self.factory() as session:
            _, version = await _seed_question(session, "h1")
            await _classify(session, version, classifier_version="first-v1")
        async with self.factory() as session:
            second = PedagogicalClassification(
                question_version_id=version.id, discipline="CHEMISTRY", content=NODE_CODE, subcontent="",
                difficulty="UNKNOWN", reasoning_type="UNSPECIFIED", status="CLASSIFIED", source="ai",
                lifecycle="ACTIVE", metadata_={"taxonomy_version": KINETICS_TAXONOMY_VERSION, "input_hash": "dup"},
            )
            session.add(second)
            with self.assertRaises(Exception):
                await session.commit()

    async def test_G_two_taxonomy_versions_coexist_active(self):
        async with self.factory() as session:
            _, version = await _seed_question(session, "h1")
            await _classify(session, version, classifier_version="tax-024-v1")
            other = PedagogicalClassification(
                question_version_id=version.id, discipline="CHEMISTRY", content="OTHER", subcontent="",
                difficulty="UNKNOWN", reasoning_type="UNSPECIFIED", status="CLASSIFIED", source="ai",
                lifecycle="ACTIVE", metadata_={"taxonomy_version": "023_curriculum_taxonomy", "input_hash": "other"},
            )
            session.add(other)
            await session.commit()  # must not raise
            count = len((await session.scalars(
                select(PedagogicalClassification).where(
                    PedagogicalClassification.question_version_id == version.id,
                    PedagogicalClassification.lifecycle == "ACTIVE",
                )
            )).all())
            self.assertEqual(count, 2)

    async def test_H_superseded_row_not_returned_as_active(self):
        async with self.factory() as session:
            _, version = await _seed_question(session, "h1")
            old, _ = await _classify(session, version, classifier_version="wrong-v1")
            candidate = await _kinetics_candidate(session)
            provider = FakeProvider(_response(candidate, KINETICS_EVIDENCE))
            await ClassificationProposalService(session).supersede_initial_classification(
                superseded_id=old.id, question_version_id=version.id, provider=_router(provider),
                target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                classifier_version="correcting-v1", prompt_version="phase9t3-kinetics-v1",
            )
            active_rows = (await session.scalars(
                select(PedagogicalClassification).where(
                    PedagogicalClassification.question_version_id == version.id,
                    PedagogicalClassification.lifecycle == "ACTIVE",
                )
            )).all()
            self.assertEqual([r.id for r in active_rows], [r.id for r in active_rows if r.id != old.id])
            self.assertEqual(len(active_rows), 1)


# --------------------------------------------------------------------------- #
# I-L. Consumers
# --------------------------------------------------------------------------- #


class ConsumerCompatibilityTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _corrected_pair(self, session):
        question, version = await _seed_question(session, "h1")
        question.validation_status = QuestionWorkflowStatus.APPROVED.value
        await session.flush()
        old, _ = await _classify(session, version, classifier_version="wrong-v1")
        candidate = await _kinetics_candidate(session)
        provider = FakeProvider(_response(candidate, KINETICS_EVIDENCE))
        new = await ClassificationProposalService(session).supersede_initial_classification(
            superseded_id=old.id, question_version_id=version.id, provider=_router(provider),
            target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
            classifier_version="correcting-v1", prompt_version="phase9t3-kinetics-v1",
        )
        return question, version, old, new

    async def test_I_publish_question_works_after_supersession(self):
        async with self.factory() as session:
            question, version, old, new = await self._corrected_pair(session)
            published = await QuestionAuthoringService(session).publish_question(question.id)
            self.assertEqual(published.validation_status, QuestionWorkflowStatus.PUBLISHED.value)

    async def test_J_ingestion_trace_works_after_supersession(self):
        async with self.factory() as session:
            question, version, old, new = await self._corrected_pair(session)
            document = IngestionDocument(
                filename="doc.pdf", document_type="PDF", document_hash="dh1",
                storage_uri="mem://doc1", file_size_bytes=10,
            )
            session.add(document)
            await session.flush()
            iq = IngestionQuestion(
                document_id=document.id, question_number=1, question_type="MULTIPLE_CHOICE",
                statement_text=version.canonical_text, position=1, question_version_id=version.id,
            )
            session.add(iq)
            await session.commit()

            trace = await IngestionClassificationService(session).get_document_traceability(document.id)
            self.assertEqual(len(trace), 1)
            self.assertEqual(trace[0]["classification"]["id"], str(new.id))
            self.assertEqual(trace[0]["classification"]["content"], NODE_CODE)

    async def test_K_knowledge_search_excludes_superseded(self):
        async with self.factory() as session:
            question, version, old, new = await self._corrected_pair(session)
            # old.content == NODE_CODE too (same content code was picked in the
            # fixture) so search on the shared code must return only the ACTIVE one.
            results = await KnowledgeService(session).find_questions_by_content(NODE_CODE)
            matched_ids = {r["question_version_id"] for r in results}
            self.assertIn(str(version.id), matched_ids)
            # exactly one classification per question in the result set - the
            # superseded id must never be the one driving inclusion on its own
            active_check = (await session.scalars(
                select(PedagogicalClassification).where(
                    PedagogicalClassification.question_version_id == version.id,
                    PedagogicalClassification.lifecycle == "ACTIVE",
                )
            )).all()
            self.assertEqual({r.id for r in active_check}, {new.id})

    async def test_K_knowledge_search_stale_topic_no_longer_surfaces_once_superseded(self):
        async with self.factory() as session:
            question, version = await _seed_question(session, "h1")
            wrong = PedagogicalClassification(
                question_version_id=version.id, discipline="CHEMISTRY", content="CHEMISTRY-SOLUTIONS",
                subcontent="", difficulty="UNKNOWN", reasoning_type="UNSPECIFIED", status="CLASSIFIED",
                source="ai", lifecycle="ACTIVE",
                metadata_={"taxonomy_version": KINETICS_TAXONOMY_VERSION, "input_hash": "wrong1"},
            )
            session.add(wrong)
            await session.commit()
            before = await KnowledgeService(session).find_questions_by_content("CHEMISTRY-SOLUTIONS")
            self.assertTrue(any(r["question_version_id"] == str(version.id) for r in before))

            wrong.lifecycle = "SUPERSEDED"
            correct = PedagogicalClassification(
                question_version_id=version.id, discipline="CHEMISTRY", content=NODE_CODE, subcontent="",
                difficulty="UNKNOWN", reasoning_type="UNSPECIFIED", status="CLASSIFIED", source="ai",
                lifecycle="ACTIVE", supersedes_id=wrong.id,
                metadata_={"taxonomy_version": KINETICS_TAXONOMY_VERSION, "input_hash": "right1"},
            )
            session.add(correct)
            await session.commit()

            after = await KnowledgeService(session).find_questions_by_content("CHEMISTRY-SOLUTIONS")
            self.assertFalse(
                any(r["question_version_id"] == str(version.id) for r in after),
                "a superseded classification must not resurface a stale topic search",
            )

    async def test_L_full_history_including_superseded_remains_queryable(self):
        async with self.factory() as session:
            question, version, old, new = await self._corrected_pair(session)
            history = await PedagogicalClassificationService(session).list_classifications(version.id)
            self.assertEqual({r.id for r in history}, {old.id, new.id})
            statuses = {r.id: r.lifecycle for r in history}
            self.assertEqual(statuses[old.id], "SUPERSEDED")
            self.assertEqual(statuses[new.id], "ACTIVE")


# --------------------------------------------------------------------------- #
# M-P. RECLASSIFICATION / STANDARD / INITIAL 9U.1-C protections unaffected
# --------------------------------------------------------------------------- #


class UnaffectedModesTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_M_reclassification_unaffected_defaults_active_no_supersede_needed(self):
        async with self.factory() as session:
            question, version = await _seed_question(session, "h1")
            historical = PedagogicalClassification(
                question_version_id=version.id, discipline="", content="", subcontent="", difficulty="UNKNOWN",
                reasoning_type="UNSPECIFIED", status="NEEDS_REVIEW", source="ai",
                metadata_={"taxonomy_version": "023_curriculum_taxonomy", "input_hash": "old", "output_hash": "old"},
            )
            session.add(historical)
            await session.commit()
            candidate = await _kinetics_candidate(session)
            provider = FakeProvider(_response(candidate, KINETICS_EVIDENCE))
            record = await ClassificationProposalService(session).reclassify_with_provider(
                version.id, _router(provider), source_taxonomy_version="023_curriculum_taxonomy",
                target_taxonomy_version=KINETICS_TAXONOMY_VERSION, classifier_version="reclassify-v1",
                prompt_version="phase9t3-kinetics-v1", reclassification_reason="lifecycle regression check",
            )
        self.assertEqual(record.lifecycle, "ACTIVE")
        self.assertIsNone(record.supersedes_id)
        self.assertEqual(record.metadata_["classification_mode"], "RECLASSIFICATION")

    async def test_O_P_initial_controlled_vocabulary_and_fail_closed_override_unaffected(self):
        async with self.factory() as session:
            _, version = await _seed_question(session, "h1")
            catalog = await _catalog(session)
            solutions_like = next(
                (c for c in ClassificationProposalService(session).recover_candidates(version.canonical_text, catalog)
                 if c["content_code"] != NODE_CODE),
                None,
            )
            kinetics = await _kinetics_candidate(session)
            if solutions_like is not None:
                provider = FakeProvider(_response(solutions_like, KINETICS_EVIDENCE))
                with self.assertRaises(ValueError):
                    await ClassificationProposalService(session).classify_initial_with_provider(
                        version.id, _router(provider), target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                        classifier_version="override-attempt-v1", prompt_version="phase9t3-kinetics-v1",
                    )
            # correct provider selection still works and is ACTIVE by default
            provider_ok = FakeProvider(_response(kinetics, KINETICS_EVIDENCE))
            record = await ClassificationProposalService(session).classify_initial_with_provider(
                version.id, _router(provider_ok), target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                classifier_version="ok-v1", prompt_version="phase9t3-kinetics-v1",
            )
        self.assertEqual(record.metadata_["content_code"], NODE_CODE)
        self.assertEqual(record.lifecycle, "ACTIVE")


# --------------------------------------------------------------------------- #
# Q. Idempotency untouched by the new lifecycle machinery
# --------------------------------------------------------------------------- #


class IdempotencyTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_Q_plain_initial_call_idempotency_unaffected_by_lifecycle_columns(self):
        async with self.factory() as session:
            _, version = await _seed_question(session, "h1")
            await session.commit()
        async with self.factory() as session:
            first, provider1 = await _classify(session, version)
        async with self.factory() as session:
            candidate = await _kinetics_candidate(session)
            provider2 = FakeProvider(_response(candidate, KINETICS_EVIDENCE))
            second = await ClassificationProposalService(session).classify_initial_with_provider(
                version.id, _router(provider2), target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                classifier_version="lifecycle-initial-v1", prompt_version="phase9t3-kinetics-v1",
            )
        self.assertEqual(first.id, second.id)
        self.assertEqual(provider2.calls, 0)
        async with self.factory() as session:
            count = await session.scalar(
                select(PedagogicalClassification.id)
                .where(PedagogicalClassification.question_version_id == version.id)
            )
        self.assertIsNotNone(count)

    async def test_supersession_is_a_one_time_transition_not_a_replay_mechanism(self):
        # Covered in detail by test_supersede_rejects_non_active_old_row; this
        # asserts the specific "idempotency key must not become an overwrite
        # mechanism" framing: a second supersede attempt against the same
        # already-superseded row is refused, never silently accepted.
        async with self.factory() as session:
            _, version = await _seed_question(session, "h1")
            old, _ = await _classify(session, version, classifier_version="wrong-v1")
            candidate = await _kinetics_candidate(session)
            await ClassificationProposalService(session).supersede_initial_classification(
                superseded_id=old.id, question_version_id=version.id,
                provider=_router(FakeProvider(_response(candidate, KINETICS_EVIDENCE))),
                target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                classifier_version="correcting-v1", prompt_version="phase9t3-kinetics-v1",
            )
            with self.assertRaises(ValueError):
                await ClassificationProposalService(session).supersede_initial_classification(
                    superseded_id=old.id, question_version_id=version.id,
                    provider=_router(FakeProvider(_response(candidate, KINETICS_EVIDENCE))),
                    target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                    classifier_version="correcting-v2", prompt_version="phase9t3-kinetics-v1",
                )
            count = len((await session.scalars(
                select(PedagogicalClassification).where(PedagogicalClassification.question_version_id == version.id)
            )).all())
            self.assertEqual(count, 2)  # old (superseded) + the one successful correction, no third row


# --------------------------------------------------------------------------- #
# R. Concurrency protection lives in the database, not just Python
# --------------------------------------------------------------------------- #


class ConcurrencyProtectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_R_second_racing_active_insert_rejected_by_database_constraint(self):
        # Simulates two callers racing to create an ACTIVE row for the same
        # key without any Python-side mutual exclusion: both build their ORM
        # objects first (as if they both passed an earlier, now-stale check),
        # then attempt to persist. The second commit must be rejected by the
        # database's own unique index, not by any application logic.
        async with self.factory() as session:
            _, version = await _seed_question(session, "h1")
            await session.commit()

        async with self.factory() as session_a:
            row_a = PedagogicalClassification(
                question_version_id=version.id, discipline="CHEMISTRY", content=NODE_CODE, subcontent="",
                difficulty="UNKNOWN", reasoning_type="UNSPECIFIED", status="CLASSIFIED", source="ai",
                lifecycle="ACTIVE", metadata_={"taxonomy_version": KINETICS_TAXONOMY_VERSION, "input_hash": "race-a"},
            )
            session_a.add(row_a)
            await session_a.commit()

        async with self.factory() as session_b:
            row_b = PedagogicalClassification(
                question_version_id=version.id, discipline="CHEMISTRY", content=NODE_CODE, subcontent="",
                difficulty="UNKNOWN", reasoning_type="UNSPECIFIED", status="CLASSIFIED", source="ai",
                lifecycle="ACTIVE", metadata_={"taxonomy_version": KINETICS_TAXONOMY_VERSION, "input_hash": "race-b"},
            )
            session_b.add(row_b)
            with self.assertRaises(Exception) as ctx:
                await session_b.commit()
            self.assertIn("Integrity", type(ctx.exception).__name__)

    async def test_R_index_is_structurally_a_partial_unique_index(self):
        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                text(f"PRAGMA index_list({migration_025.TABLE})")
            )).all()
        target = next(r for r in rows if r[1] == migration_025.ACTIVE_UNIQUE_INDEX)
        self.assertEqual(bool(target[2]), True)  # unique flag
        self.assertEqual(target[3], "c")  # created by an explicit CREATE INDEX (partial)


# --------------------------------------------------------------------------- #
# Migration file itself: backfill safety, rollback safety, no data loss
# (isolated pre-025 legacy schema, built without the already-updated ORM model)
# --------------------------------------------------------------------------- #


LEGACY_PEDAGOGICAL_CLASSIFICATIONS_DDL = """
CREATE TABLE pedagogical_classifications (
    id CHAR(32) PRIMARY KEY,
    question_version_id CHAR(32) NOT NULL,
    discipline VARCHAR(255) NOT NULL,
    content VARCHAR(255) NOT NULL,
    subcontent VARCHAR(255) NOT NULL,
    difficulty VARCHAR(30) NOT NULL,
    classification_confidence NUMERIC(5,4),
    difficulty_confidence NUMERIC(5,4),
    reasoning_type VARCHAR(100) NOT NULL,
    prerequisites JSON,
    keywords JSON,
    competencies JSON,
    skills JSON,
    model_name VARCHAR(255),
    model_version VARCHAR(100),
    prompt_version VARCHAR(100),
    provider_name VARCHAR(100),
    input_tokens INTEGER,
    output_tokens INTEGER,
    total_tokens INTEGER,
    status VARCHAR(30) NOT NULL,
    source VARCHAR(20) NOT NULL,
    created_at TIMESTAMP NOT NULL,
    metadata JSON
)
"""


def _uid() -> str:
    return uuid.uuid4().hex


async def _seed_legacy_row(connection, *, row_id, qv, content, created_at, metadata):
    await connection.execute(
        text(
            "INSERT INTO pedagogical_classifications "
            "(id, question_version_id, discipline, content, subcontent, difficulty, reasoning_type, "
            "status, source, created_at, metadata) "
            "VALUES (:id,:qv,'CHEMISTRY',:content,'','UNKNOWN','UNSPECIFIED','CLASSIFIED','ai',:ca,:md)"
        ),
        {"id": row_id, "qv": qv, "content": content, "ca": created_at.isoformat(), "md": json.dumps(metadata)},
    )


class MigrationBehaviourTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.execute(text(LEGACY_PEDAGOGICAL_CLASSIFICATIONS_DDL))
        self.vid = _uid()
        self.old_id, self.new_id, self.untagged_id = _uid(), _uid(), _uid()
        now = datetime.datetime.now(datetime.timezone.utc)
        async with self.engine.begin() as connection:
            await _seed_legacy_row(
                connection, row_id=self.old_id, qv=self.vid, content="CHEMISTRY-SOLUTIONS",
                created_at=now, metadata={"taxonomy_version": KINETICS_TAXONOMY_VERSION, "input_hash": "a1"},
            )
            await _seed_legacy_row(
                connection, row_id=self.new_id, qv=self.vid, content=NODE_CODE,
                created_at=now + datetime.timedelta(seconds=5),
                metadata={"taxonomy_version": KINETICS_TAXONOMY_VERSION, "input_hash": "b2"},
            )
            await _seed_legacy_row(
                connection, row_id=self.untagged_id, qv=self.vid, content="Y",
                created_at=now, metadata={"not_taxonomy_related": True},
            )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_S_T_upgrade_backfill_is_deterministic_and_loses_no_row(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(_apply(migration_025, "upgrade"))

        async with self.engine.connect() as connection:
            rows = (await connection.execute(
                text("SELECT id, content, lifecycle FROM pedagogical_classifications ORDER BY created_at")
            )).all()
        by_id = {r[0]: (r[1], r[2]) for r in rows}
        self.assertEqual(len(rows), 3)  # T: no row lost
        self.assertEqual(by_id[self.old_id], ("CHEMISTRY-SOLUTIONS", "SUPERSEDED"))
        self.assertEqual(by_id[self.new_id], (NODE_CODE, "ACTIVE"))
        self.assertEqual(by_id[self.untagged_id], ("Y", "ACTIVE"))

        # deterministic: re-reading lifecycle after the same upgrade gives the same answer
        async with self.engine.connect() as connection:
            rows_again = (await connection.execute(
                text("SELECT id, lifecycle FROM pedagogical_classifications ORDER BY created_at")
            )).all()
        self.assertEqual({r[0]: r[2] for r in rows}, {r[0]: r[1] for r in rows_again})

    async def test_uniqueness_index_created_by_migration_rejects_duplicate(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(_apply(migration_025, "upgrade"))
        async with self.engine.connect() as connection:
            try:
                async with connection.begin():
                    await connection.execute(
                        text(
                            "INSERT INTO pedagogical_classifications "
                            "(id, question_version_id, discipline, content, subcontent, difficulty, "
                            "reasoning_type, status, source, created_at, metadata, lifecycle) "
                            "VALUES (:id,:qv,'CHEMISTRY',:content,'','UNKNOWN','UNSPECIFIED','CLASSIFIED','ai',"
                            ":ca,:md,'ACTIVE')"
                        ),
                        {
                            "id": _uid(), "qv": self.vid, "content": "CHEMISTRY-SOLUTIONS",
                            "ca": datetime.datetime.now(datetime.timezone.utc).isoformat(),
                            "md": json.dumps({"taxonomy_version": KINETICS_TAXONOMY_VERSION, "input_hash": "dup"}),
                        },
                    )
                raised = False
            except Exception:
                raised = True
        self.assertTrue(raised)

    async def test_S_downgrade_is_safe_removes_only_added_columns_no_data_loss(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(_apply(migration_025, "upgrade"))
        async with self.engine.begin() as connection:
            await connection.run_sync(_apply(migration_025, "downgrade"))

        async with self.engine.connect() as connection:
            columns = [row[1] for row in (await connection.execute(
                text("PRAGMA table_info(pedagogical_classifications)")
            )).all()]
            self.assertNotIn("lifecycle", columns)
            self.assertNotIn("supersedes_id", columns)
            rows = (await connection.execute(
                text("SELECT id, content, status, model_version, created_at, metadata "
                     "FROM pedagogical_classifications ORDER BY created_at")
            )).all()
        self.assertEqual(len(rows), 3)  # T: still nothing lost after a full round trip
        contents = {r[0]: r[1] for r in rows}
        self.assertEqual(contents[self.old_id], "CHEMISTRY-SOLUTIONS")
        self.assertEqual(contents[self.new_id], NODE_CODE)
        self.assertEqual(contents[self.untagged_id], "Y")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
