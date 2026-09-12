"""PHASE 9U.2-H3 — tests for the Q133 execution shim + the `expected_initial_count`
parametrization of the H2 executor. Isolated SQLite; no PostgreSQL, no OpenAI,
no external provider, no production data, no Alembic.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import inspect
import sys
import unittest
import uuid
from pathlib import Path

from sqlalchemy import func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool
from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "manual"))

from agente_ia_edu.db.base import Base  # noqa: E402
from agente_ia_edu.db.models import (  # noqa: E402
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
import agente_ia_edu.services.curriculum_classification as cc  # noqa: E402
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService  # noqa: E402
from agente_ia_edu.services._curriculum_v2_bindings import NEW_AREAS, NEW_CONTENTS  # noqa: E402

import phase9u2_batch0 as b0  # noqa: E402
import phase9u2h2_curriculum_v2 as h2  # noqa: E402
import phase9u2h3_execute as h3  # noqa: E402

MIG = ROOT / "migrations" / "versions"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


migration_024 = _load(MIG / "024_chemistry_kinetics.py", "h3_mig024")
migration_025 = _load(MIG / "025_pedagogical_classification_lifecycle.py", "h3_mig025")


def _apply(module, direction):
    def run(connection):
        ctx = MigrationContext.configure(connection)
        prev = module.op
        module.op = Operations(ctx)
        try:
            getattr(module, direction)()
        finally:
            module.op = prev

    return run


Q133_STMT = (
    "Os manuais de refrigerador apresentam a recomendação de que o equipamento não deve ser "
    "instalado próximo a fontes de calor, como fogão e aquecedores, ou em local onde incida "
    "diretamente a luz do sol. A instalação em local inadequado prejudica o funcionamento do "
    "refrigerador e aumenta o consumo de energia. O não atendimento dessa recomendação resulta "
    "em aumento do consumo de energia porque"
)


async def _make_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await CurriculumTaxonomyService(s).seed_reference_fixture()
    async with engine.begin() as conn:
        await conn.run_sync(_apply(migration_024, "upgrade"))
    te = migration_025._taxonomy_version_expr("sqlite")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                f"CREATE UNIQUE INDEX {migration_025.ACTIVE_UNIQUE_INDEX} "
                f"ON {migration_025.TABLE} (question_version_id, {te}) WHERE lifecycle = 'ACTIVE'"
            )
        )
    async with factory() as s:
        svc = CurriculumTaxonomyService(s)
        by = {n.code: n for n in (await s.scalars(select(CatalogNode))).all()}
        pos = 90
        for code, name, parent in NEW_AREAS:
            by[code] = await svc.create_node(name, "AREA", code, by[parent].id, pos)
            pos += 1
        for code, name, parent in NEW_CONTENTS:
            by[code] = await svc.create_node(name, "CONTENT", code, by[parent].id, 1)
        await s.commit()
    return engine, factory


async def _seed_q133(factory):
    async with factory() as s:
        inst = Institution(code=f"INEP-{uuid.uuid4().hex[:6]}", name="INEP")
        s.add(inst)
        await s.flush()
        ex = Exam(institution_id=inst.id, code=f"E-{uuid.uuid4().hex[:6]}", name="ENEM")
        s.add(ex)
        await s.flush()
        ap = ExamApplication(exam_id=ex.id, year=2020, application_type="REGULAR", day=2)
        s.add(ap)
        await s.flush()
        bk = ExamBooklet(exam_application_id=ap.id, code=f"B-{uuid.uuid4().hex[:6]}")
        s.add(bk)
        await s.flush()
        q = Question(
            validation_status="validated", origin_type="IMPORTED", status="DRAFT", visibility_scope="PRIVATE"
        )
        s.add(q)
        await s.flush()
        v = QuestionVersion(
            question_id=q.id,
            version_kind="official_original",
            statement=Q133_STMT,
            canonical_text=Q133_STMT,
            content_hash=hashlib.sha256(f"133:{uuid.uuid4()}".encode()).hexdigest(),
        )
        s.add(v)
        await s.flush()
        s.add(
            BookletQuestion(
                exam_booklet_id=bk.id, question_version_id=v.id, position=133, official_number=133
            )
        )
        await s.commit()
        return str(v.id)


async def _count(factory):
    async with factory() as s:
        return int(await s.scalar(select(func.count()).select_from(PedagogicalClassification)))


# --------------------------------------------------------------------------- #
# Parametrization — H2 default preserved
# --------------------------------------------------------------------------- #


class ParametrizationTests(unittest.TestCase):
    def test_preflight_default_expected_count_is_4(self):
        self.assertEqual(
            inspect.signature(h2.preflight).parameters["expected_initial_count"].default, 4
        )

    def test_run_default_expected_count_is_4(self):
        self.assertEqual(inspect.signature(h2.run).parameters["expected_initial_count"].default, 4)

    def test_module_constant_unchanged(self):
        self.assertEqual(h2.EXPECTED_INITIAL_CLASSIFICATION_COUNT, 4)

    def test_h2_cli_still_passes_no_override(self):
        src = (ROOT / "tests" / "manual" / "phase9u2h2_curriculum_v2.py").read_text()
        # _amain must not pass expected_initial_count -> H2 CLI keeps the 4 default
        amain = src.split("async def _amain")[1].split("\ndef main(")[0]
        self.assertIn("report = await run(factory, dry_run=dry_run, approval=approval", amain)
        self.assertNotIn("expected_initial_count", amain)


class RunThreadsExpectedCountTests(unittest.IsolatedAsyncioTestCase):
    async def test_run_forwards_expected_count_to_preflight(self):
        seen = {}
        orig = h2.preflight

        async def spy(factory, *, expected_initial_count=h2.EXPECTED_INITIAL_CLASSIFICATION_COUNT):
            seen["value"] = expected_initial_count
            return True, "OK(spy)", expected_initial_count

        h2.preflight = spy
        try:
            engine, factory = await _make_db()
            try:
                await h2.run(factory, dry_run=True, approval=False, only=[133])
                self.assertEqual(seen["value"], 4)  # H2 default
                await h2.run(
                    factory, dry_run=True, approval=False, only=[133], expected_initial_count=17
                )
                self.assertEqual(seen["value"], 17)  # explicit override
            finally:
                await engine.dispose()
        finally:
            h2.preflight = orig


# --------------------------------------------------------------------------- #
# H3 shim — gates + constants
# --------------------------------------------------------------------------- #


class H3GateTests(unittest.TestCase):
    def test_constants(self):
        self.assertEqual(h3.H3_TARGET_OFFICIAL_NUMBER, 133)
        self.assertEqual(h3.H3_TARGET_CONTENT_CODE, "PHYSICS-THERMAL-THERMODYNAMICS")
        self.assertEqual(h3.H3_REQUIRED_VOCAB_TERM, "refrigerador")
        self.assertEqual(h3.H3_EXPECTED_COUNT_BEFORE, 17)
        self.assertEqual(h3.H3_EXPECTED_COUNT_AFTER, 18)

    def test_dry_run_default_true_only_literal_false(self):
        self.assertTrue(h3.is_dry_run({}))
        self.assertFalse(h3.is_dry_run({"PHASE9U2_H3_DRY_RUN": "false"}))
        self.assertFalse(h3.is_dry_run({"PHASE9U2_H3_DRY_RUN": "FALSE"}))
        self.assertTrue(h3.is_dry_run({"PHASE9U2_H3_DRY_RUN": "0"}))

    def test_approval_requires_exact_h3_token(self):
        self.assertFalse(h3.approval_granted({}))
        self.assertTrue(
            h3.approval_granted({"PHASE9U2_H3_APPROVAL_TOKEN": "PHASE9U2-H3-VOCABULARY-REVIEWED"})
        )

    def test_rejects_other_phase_tokens_including_h2(self):
        for tok in (
            "PHASE9U1E",
            "PHASE9U2_G5_CATALOG_REVIEWED",
            "PHASE-9U2-BATCH0-INITIAL-REVIEWED",
            "PHASE9U2-H2-INITIAL-CLASSIFICATION-REVIEWED",
        ):
            self.assertFalse(h3.approval_granted({"PHASE9U2_H3_APPROVAL_TOKEN": tok}), tok)

    def test_vocabulary_has_refrigerador(self):
        # the approved change must be live in the registry for this build
        self.assertTrue(h3.vocabulary_has_refrigerador())
        b = cc.resolve_registered_initial_binding("curriculum-v2", "PHYSICS-THERMAL-THERMODYNAMICS")
        self.assertIn("refrigerador", b.vocabulary.specific_terms)


# --------------------------------------------------------------------------- #
# Helpers for the idempotency-gate tests
# --------------------------------------------------------------------------- #


async def _seed_fillers(factory, n, *, lifecycle="SUPERSEDED", taxonomy="legacy"):
    """Add ``n`` unrelated classification rows so the total count hits a target."""
    async with factory() as s:
        qid = (await s.scalars(select(Question.id))).first()
        for i in range(n):
            v = QuestionVersion(
                question_id=qid,
                version_kind="derived",
                canonical_text=f"filler {uuid.uuid4()}",
                content_hash=hashlib.sha256(f"f{uuid.uuid4()}".encode()).hexdigest(),
            )
            s.add(v)
            await s.flush()
            s.add(
                PedagogicalClassification(
                    question_version_id=v.id,
                    discipline="X",
                    content="X",
                    subcontent="X",
                    difficulty="UNKNOWN",
                    reasoning_type="UNSPECIFIED",
                    status="CLASSIFIED",
                    source="rule",
                    lifecycle=lifecycle,
                    model_version="m",
                    prompt_version="p",
                    metadata_={"taxonomy_version": taxonomy},
                )
            )
        await s.commit()


async def _seed_q133_v2_active(factory, qv_id, *, count=1, drop_index=False):
    """Add ``count`` ACTIVE curriculum-v2 rows for Q133's question_version.
    ``drop_index`` first removes the partial unique index so >1 ACTIVE is possible
    (to exercise the Q133_MULTIPLE_ACTIVE guard)."""
    if drop_index:
        async with factory() as s:
            await s.execute(text(f"DROP INDEX {migration_025.ACTIVE_UNIQUE_INDEX}"))
            await s.commit()
    async with factory() as s:
        for _ in range(count):
            s.add(
                PedagogicalClassification(
                    question_version_id=uuid.UUID(qv_id) if isinstance(qv_id, str) else qv_id,
                    discipline="PHYSICS",
                    content="PHYSICS-THERMAL-THERMODYNAMICS",
                    subcontent="X",
                    difficulty="UNKNOWN",
                    reasoning_type="UNSPECIFIED",
                    status="CLASSIFIED",
                    source="rule",
                    lifecycle="ACTIVE",
                    model_version="phase9u2h2-initial-v1",
                    prompt_version="phase9u2h2-curriculum-v2-v1",
                    metadata_={
                        "taxonomy_version": "curriculum-v2",
                        "classification_mode": "INITIAL",
                    },
                )
            )
        await s.commit()


# --------------------------------------------------------------------------- #
# H3 idempotency gate — detect_h3_state (standalone, read-only, no h2.preflight)
# --------------------------------------------------------------------------- #


class H3StateDetectionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()
        self.qv = await _seed_q133(self.factory)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_state_17_q133_zero_is_first_run(self):
        await _seed_fillers(self.factory, 17)
        state, expected, snap = await h3.detect_h3_state(self.factory)
        self.assertEqual(state, h3.H3_STATE_FIRST_RUN)
        self.assertEqual(expected, 17)
        self.assertEqual(snap, {"total": 17, "q133_v2_active": 0})

    async def test_state_18_q133_one_is_idempotent(self):
        await _seed_fillers(self.factory, 17)
        await _seed_q133_v2_active(self.factory, self.qv, count=1)
        state, expected, snap = await h3.detect_h3_state(self.factory)
        self.assertEqual(state, h3.H3_STATE_IDEMPOTENT)
        self.assertEqual(expected, 18)
        self.assertEqual(snap, {"total": 18, "q133_v2_active": 1})

    async def test_unexpected_count_raises(self):
        await _seed_fillers(self.factory, 5)
        with self.assertRaises(h3.H3PreconditionError) as ctx:
            await h3.detect_h3_state(self.factory)
        self.assertIn("UNEXPECTED_CLASSIFICATION_COUNT", str(ctx.exception))

    async def test_count_19_raises(self):
        await _seed_fillers(self.factory, 17)
        await _seed_q133_v2_active(self.factory, self.qv, count=1)
        await _seed_fillers(self.factory, 1)  # -> 19
        with self.assertRaises(h3.H3PreconditionError) as ctx:
            await h3.detect_h3_state(self.factory)
        self.assertIn("UNEXPECTED_CLASSIFICATION_COUNT", str(ctx.exception))

    async def test_count_17_but_q133_already_classified_raises(self):
        await _seed_fillers(self.factory, 16)
        await _seed_q133_v2_active(self.factory, self.qv, count=1)  # total 17, q133 active 1
        with self.assertRaises(h3.H3PreconditionError) as ctx:
            await h3.detect_h3_state(self.factory)
        self.assertIn("STATE_17_BUT_Q133_ALREADY_CLASSIFIED", str(ctx.exception))

    async def test_count_18_but_q133_zero_raises(self):
        await _seed_fillers(self.factory, 18)  # total 18, q133 active 0
        with self.assertRaises(h3.H3PreconditionError) as ctx:
            await h3.detect_h3_state(self.factory)
        self.assertIn("STATE_18_BUT_Q133_NOT_EXACTLY_ONE_ACTIVE", str(ctx.exception))

    async def test_q133_two_active_raises(self):
        await _seed_fillers(self.factory, 16)
        await _seed_q133_v2_active(self.factory, self.qv, count=2, drop_index=True)  # total 18, q133 active 2
        with self.assertRaises(h3.H3PreconditionError) as ctx:
            await h3.detect_h3_state(self.factory)
        self.assertIn("Q133_MULTIPLE_ACTIVE_CURRICULUM_V2", str(ctx.exception))


# --------------------------------------------------------------------------- #
# H3 shim — end-to-end against SQLite (h2.preflight env-check stubbed: it needs
# PostgreSQL + the full production shape; the count contract is exercised by
# RunThreadsExpectedCountTests and detect_h3_state above)
# --------------------------------------------------------------------------- #


class H3RunTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()
        self.qv = await _seed_q133(self.factory)
        self._orig = h2.preflight

        async def _stub_preflight(factory, *, expected_initial_count=h2.EXPECTED_INITIAL_CLASSIFICATION_COUNT):
            async with factory() as s:
                await b0._begin_read_only(s)
                n = int(await s.scalar(select(func.count()).select_from(PedagogicalClassification)))
            # honour the count contract the shim relies on
            return (n == expected_initial_count), f"count={n} exp={expected_initial_count}", n

        h2.preflight = _stub_preflight

    async def asyncTearDown(self):
        h2.preflight = self._orig
        await self.engine.dispose()

    async def test_precondition_missing_term_raises(self):
        key = ("curriculum-v2", "PHYSICS-THERMAL-THERMODYNAMICS")
        orig = cc._DETERMINISTIC_INITIAL_BINDINGS[key]
        ov = orig.vocabulary
        stripped = cc.RetrievalVocabularyEntry(
            canonical_code=ov.canonical_code,
            primary_terms=ov.primary_terms,
            specific_terms=tuple(t for t in ov.specific_terms if t != "refrigerador"),
            contextual_expressions=ov.contextual_expressions,
            generic_terms=ov.generic_terms,
            version=ov.version,
            enabled=ov.enabled,
        )
        cc._DETERMINISTIC_INITIAL_BINDINGS[key] = cc.DeterministicInitialBinding(
            taxonomy_version=orig.taxonomy_version,
            canonical_code=orig.canonical_code,
            parent_code=orig.parent_code,
            vocabulary=stripped,
        )
        try:
            with self.assertRaises(h3.H3PreconditionError):
                await h3.run_h3(self.factory, dry_run=True, approval=False)
        finally:
            cc._DETERMINISTIC_INITIAL_BINDINGS[key] = orig

    async def test_first_run_dry_run_targets_only_133(self):
        await _seed_fillers(self.factory, 17)
        report, state, snap = await h3.run_h3(self.factory, dry_run=True, approval=False)
        self.assertEqual(state, h3.H3_STATE_FIRST_RUN)
        self.assertEqual([r.number for r in report.results], [133])
        self.assertEqual(report.results[0].state, "READY_FOR_INITIAL")
        self.assertEqual(report.results[0].reason_code, "PLANNED_DRY_RUN")
        self.assertEqual(report.results[0].content, "PHYSICS-THERMAL-THERMODYNAMICS")
        self.assertEqual(report.writes_applied, 0)
        self.assertEqual(await _count(self.factory), 17)

    async def test_unexpected_state_blocks_before_h2(self):
        await _seed_fillers(self.factory, 3)  # neither 17 nor 18
        with self.assertRaises(h3.H3PreconditionError):
            await h3.run_h3(self.factory, dry_run=False, approval=True)
        self.assertEqual(await _count(self.factory), 3)  # nothing written

    async def test_write_then_idempotent(self):
        await _seed_fillers(self.factory, 17)
        self.assertEqual(await _count(self.factory), 17)

        # STATE A — first run
        r1, s1, snap1 = await h3.run_h3(self.factory, dry_run=False, approval=True)
        self.assertEqual(s1, h3.H3_STATE_FIRST_RUN)
        self.assertTrue(r1.preflight_ok, r1.preflight_detail)
        self.assertIsNone(r1.stopped_on_error)
        self.assertEqual(r1.writes_applied, 1)
        self.assertEqual(await _count(self.factory), 18)
        self.assertEqual(r1.final_count, 18)
        self.assertTrue(r1.protected_ok)
        self.assertTrue(r1.integrity_ok)
        self.assertEqual(r1.results[0].state, "READY_FOR_INITIAL")
        self.assertEqual(r1.results[0].reason_code, "INITIAL_CLASSIFICATION_CREATED")

        async with self.factory() as s:
            row = (
                await s.scalars(
                    select(PedagogicalClassification).where(
                        PedagogicalClassification.content == "PHYSICS-THERMAL-THERMODYNAMICS"
                    )
                )
            ).one()
            md = row.metadata_ or {}
            self.assertEqual(row.lifecycle, "ACTIVE")
            self.assertEqual(md["taxonomy_version"], "curriculum-v2")
            self.assertEqual(md["classification_mode"], "INITIAL")
            self.assertIsNone(row.supersedes_id)

        # STATE B — idempotent re-run (count is now 18, Q133 has 1 ACTIVE v2)
        r2, s2, snap2 = await h3.run_h3(self.factory, dry_run=False, approval=True)
        self.assertEqual(s2, h3.H3_STATE_IDEMPOTENT)
        self.assertEqual(snap2, {"total": 18, "q133_v2_active": 1})
        self.assertTrue(r2.preflight_ok, r2.preflight_detail)
        self.assertEqual(r2.writes_applied, 0)
        self.assertEqual(await _count(self.factory), 18)
        self.assertEqual(r2.final_count, 18)
        self.assertTrue(r2.protected_ok)
        self.assertTrue(r2.integrity_ok)
        self.assertEqual(r2.results[0].state, "ALREADY_CLASSIFIED")
        self.assertEqual(r2.results[0].reason_code, "ACTIVE_INITIAL_TARGET_TAXONOMY")

        # third run — still idempotent, still no write
        r3, s3, _ = await h3.run_h3(self.factory, dry_run=False, approval=True)
        self.assertEqual(s3, h3.H3_STATE_IDEMPOTENT)
        self.assertEqual(r3.writes_applied, 0)
        self.assertEqual(await _count(self.factory), 18)


# --------------------------------------------------------------------------- #
# Static safety — the shim
# --------------------------------------------------------------------------- #


class H3StaticSafetyTests(unittest.TestCase):
    FILE = ROOT / "tests" / "manual" / "phase9u2h3_execute.py"

    def _tree(self):
        return ast.parse(self.FILE.read_text(), filename=str(self.FILE))

    def test_no_banned_imports(self):
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.assertNotIn(a.name.split(".")[0], {"alembic", "openai"})
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or "").split(".")[0], {"alembic", "openai"})

    def test_no_write_primitives(self):
        banned = {
            "add",
            "add_all",
            "commit",
            "create_node",
            "supersede_initial_classification",
            "propose_with_provider",
            "classify_initial_with_provider",  # the shim delegates; it must not call it directly
            "upgrade",
            "downgrade",
            "stamp",
        }
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, banned, f".{node.func.attr}()")

    def test_delegates_to_h2_run(self):
        src = self.FILE.read_text()
        self.assertIn("h2.run(", src)
        self.assertIn("only=[H3_TARGET_OFFICIAL_NUMBER]", src)
        # the expected count is resolved by detect_h3_state (17 first run / 18 idempotent)
        self.assertIn("expected_initial_count=expected_count", src)
        self.assertIn("detect_h3_state", src)

    def test_no_manual_sql_writes(self):
        src = self.FILE.read_text()
        for frag in ("session.add(", "s.add(", ".commit()", "INSERT INTO", "UPDATE ", "DELETE FROM"):
            self.assertNotIn(frag, src, frag)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
