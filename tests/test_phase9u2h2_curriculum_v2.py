"""PHASE 9U.2-H2-BUILD — tests for the curriculum-v2 INITIAL classification executor.

Isolated: in-memory SQLite for the state machine + the write path, pure unit
tests for the Layer-A map and the environment gates, and an AST static-safety
scan of the executor + shared decision core. No PostgreSQL, no OpenAI, no
external provider, no production data, no Alembic. Nothing here classifies a
real ENEM question against production.
"""

from __future__ import annotations

import ast
import hashlib
import importlib.util
import json
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
from agente_ia_edu.providers.router import ProviderRouter  # noqa: E402
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService  # noqa: E402
import agente_ia_edu.services.curriculum_classification as cc  # noqa: E402
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    ClassificationProposalService,
    KINETICS_TAXONOMY_VERSION,
)
from agente_ia_edu.services._curriculum_v2_bindings import (  # noqa: E402
    DEFERRED_CONTENTS,
    NEW_AREAS,
    NEW_CONTENTS,
)

import phase9u2_batch0 as b0  # noqa: E402
import phase9u2h2_curriculum_v2 as h2  # noqa: E402
import _phase9u2_decision as dc  # noqa: E402

MIG = ROOT / "migrations" / "versions"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


migration_024 = _load(MIG / "024_chemistry_kinetics.py", "h2_mig024")
migration_025 = _load(MIG / "025_pedagogical_classification_lifecycle.py", "h2_mig025")


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


# curriculum-v2 statements carrying a literal primary term -> deterministic BOUND
WAVES_STMT = "A tecnologia analisada baseia-se no fenômeno ondulatório de interferência destrutiva entre ondas."
WAVES_CODE = "PHYSICS-WAVES-PHENOMENA"
POLY_STMT = "A vantagem ambiental de um polímero biodegradável frente ao polímero convencional é a degradação rápida."
POLY_CODE = "CHEMISTRY-ORGANIC-POLYMERS"
INDUCTION_STMT = "O aumento do fluxo magnético variável através da bobina eleva a força eletromotriz induzida."
INDUCTION_CODE = "PHYSICS-EM-INDUCTION"
NO_MATCH_STMT = "Uma frase sem qualquer termo de vocabulário controlado sobre um assunto irrelevante."


async def _make_db(*, with_v2_catalog: bool = True):
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as s:
        await CurriculumTaxonomyService(s).seed_reference_fixture()
    async with engine.begin() as conn:
        await conn.run_sync(_apply(migration_024, "upgrade"))
    taxo_expr = migration_025._taxonomy_version_expr("sqlite")
    async with engine.begin() as conn:
        await conn.execute(
            text(
                f"CREATE UNIQUE INDEX {migration_025.ACTIVE_UNIQUE_INDEX} "
                f"ON {migration_025.TABLE} (question_version_id, {taxo_expr}) WHERE lifecycle = 'ACTIVE'"
            )
        )
    if with_v2_catalog:
        async with factory() as s:
            svc = CurriculumTaxonomyService(s)
            by_code = {n.code: n for n in (await s.scalars(select(CatalogNode))).all()}
            pos = 90
            for code, name, parent_code in NEW_AREAS:
                by_code[code] = await svc.create_node(name, "AREA", code, by_code[parent_code].id, pos)
                pos += 1
            for code, name, parent_code in NEW_CONTENTS:
                by_code[code] = await svc.create_node(name, "CONTENT", code, by_code[parent_code].id, 1)
            await s.commit()
    return engine, factory


async def _booklet(session):
    tag = uuid.uuid4().hex[:8]
    inst = Institution(code=f"INEP-{tag}", name="INEP")
    session.add(inst)
    await session.flush()
    exam = Exam(institution_id=inst.id, code=f"ENEM-{tag}", name="ENEM")
    session.add(exam)
    await session.flush()
    app = ExamApplication(exam_id=exam.id, year=2020, application_type="REGULAR", day=2)
    session.add(app)
    await session.flush()
    bk = ExamBooklet(exam_application_id=app.id, code=f"CD5-{tag}")
    session.add(bk)
    await session.flush()
    return bk


async def _seed_official(session, booklet, official_number, statement):
    q = Question(
        validation_status="validated", origin_type="IMPORTED", status="DRAFT", visibility_scope="PRIVATE"
    )
    session.add(q)
    await session.flush()
    v = QuestionVersion(
        question_id=q.id,
        version_kind="official_original",
        canonical_text=statement,
        content_hash=hashlib.sha256(f"{statement}:{official_number}:{uuid.uuid4()}".encode()).hexdigest(),
    )
    session.add(v)
    await session.flush()
    bq = BookletQuestion(
        exam_booklet_id=booklet.id,
        question_version_id=v.id,
        position=official_number,
        official_number=official_number,
    )
    session.add(bq)
    await session.flush()
    return q, v


async def _count_pc(factory):
    async with factory() as s:
        return int(await s.scalar(select(func.count()).select_from(PedagogicalClassification)))


async def _catalog(session):
    return list((await session.scalars(select(CatalogNode).where(CatalogNode.active.is_(True)))).all())


KINETICS_STMT = "Nanomateriais catalíticos aumentam a velocidade da reação química."


async def _seed_kinetics_active(session, version):
    """A pre-existing ACTIVE INITIAL row on the kinetics taxonomy (other taxonomy).
    The version's text must carry a kinetics vocabulary term."""
    catalog = await _catalog(session)
    cand = next(
        c
        for c in ClassificationProposalService(session).recover_candidates(version.canonical_text, catalog)
        if c["content_code"] == "CHEMISTRY-PHYSICAL-KINETICS"
    )
    provider = b0.DeterministicKineticsProvider(candidate=cand, evidence_text="velocidade da reação")
    return await ClassificationProposalService(session).classify_initial_with_provider(
        version.id,
        ProviderRouter(text_providers=[provider], embedding_providers=[]),
        target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
        classifier_version="phase9u1-initial-v1",
        prompt_version="phase9t3-kinetics-v1",
    )


async def _seed_v2_active(session, version, code, evidence, *, classifier_version, prompt_version):
    """A pre-existing ACTIVE INITIAL row on curriculum-v2 with a DIFFERENT
    (classifier_version, prompt_version) than H2 uses — so a later H2 write has a
    distinct idempotency key and collides on the partial unique index."""
    catalog = await _catalog(session)
    cand = next(
        c
        for c in ClassificationProposalService(session).recover_candidates(version.canonical_text, catalog)
        if c["content_code"] == code
    )
    provider = b0.DeterministicKineticsProvider(candidate=cand, evidence_text=evidence)
    return await ClassificationProposalService(session).classify_initial_with_provider(
        version.id,
        ProviderRouter(text_providers=[provider], embedding_providers=[]),
        target_taxonomy_version="curriculum-v2",
        classifier_version=classifier_version,
        prompt_version=prompt_version,
        target_content_code=code,
    )


# --------------------------------------------------------------------------- #
# Layer A — the map
# --------------------------------------------------------------------------- #


class MapTests(unittest.TestCase):
    EXPECTED = {
        92: "PHYSICS-THERMAL-PHASE-CHANGE",
        94: "PHYSICS-WAVES-PHENOMENA",
        95: "CHEMISTRY-PHYSICAL-EQUILIBRIUM",
        103: "CHEMISTRY-GENERAL-POLARITY-IMF",
        104: "CHEMISTRY-PHYSICAL-ACID-BASE",
        105: "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS",
        111: "CHEMISTRY-ENVIRONMENTAL-AIR-POLLUTION",
        112: "PHYSICS-EM-ELECTROSTATICS",
        113: "CHEMISTRY-GENERAL-MATTER-PROPERTIES",
        125: "PHYSICS-EM-ELECTROSTATICS",
        126: "CHEMISTRY-ORGANIC-POLYMERS",
        127: "BIOLOGY-ECOLOGY-POPULATIONS-CONSERVATION",
        129: "BIOLOGY-ECOLOGY-BIOGEOCHEMICAL-CYCLES",
        130: "PHYSICS-EM-INDUCTION",
        133: "PHYSICS-THERMAL-THERMODYNAMICS",
        134: "PHYSICS-MECHANICS-HYDROSTATICS",
        135: "PHYSICS-MECHANICS-PRESSURE-SCALE",
        152: "MATH-MEASUREMENT-PLANE-AREA",
        153: "MATH-COMBINATORICS-COUNTING",
        172: "MATH-STATISTICS-CENTRAL-TENDENCY",
    }

    def test_01_map_has_exactly_20_expected_entries(self):
        self.assertEqual(h2.OFFICIAL_TO_CONTENT, self.EXPECTED)
        self.assertEqual(len(h2.OFFICIAL_TO_CONTENT), 20)

    def test_02_no_protected_in_write_set(self):
        self.assertEqual(set(h2.OFFICIAL_TO_CONTENT) & {91, 93, 107, 128}, set())

    def test_03_q107_not_in_write_set(self):
        self.assertNotIn(107, h2.OFFICIAL_TO_CONTENT)
        self.assertIn(107, h2.HUMAN_REVIEW_OFFICIAL_NUMBERS)
        self.assertIn(107, h2.PROTECTED_OFFICIAL_NUMBERS)

    def test_04_taxonomy_is_curriculum_v2(self):
        self.assertEqual(h2.TARGET_TAXONOMY, "curriculum-v2")
        self.assertIn("curriculum-v2", cc.registered_initial_taxonomy_versions())

    def test_05_every_content_code_exists_in_bindings(self):
        for number, code in h2.OFFICIAL_TO_CONTENT.items():
            with self.subTest(number=number):
                b = cc.resolve_registered_initial_binding("curriculum-v2", code)
                self.assertIsNotNone(b)
                self.assertEqual(b.canonical_code, code)

    def test_06_every_content_code_matches_expected_catalog(self):
        content_codes = {c for c, _n, _p in NEW_CONTENTS}
        for number, code in h2.OFFICIAL_TO_CONTENT.items():
            with self.subTest(number=number):
                self.assertIn(code, content_codes)
                self.assertNotIn(code, DEFERRED_CONTENTS)
        self.assertNotIn("CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS", h2.OFFICIAL_TO_CONTENT.values())

    def test_07_validate_map_passes(self):
        h2.validate_map()  # no raise

    def test_08_validate_map_rejects_protected(self):
        orig = dict(h2.OFFICIAL_TO_CONTENT)
        try:
            h2.OFFICIAL_TO_CONTENT[93] = "PHYSICS-WAVES-PHENOMENA"
            with self.assertRaises(h2.MapError):
                h2.validate_map()
        finally:
            h2.OFFICIAL_TO_CONTENT.clear()
            h2.OFFICIAL_TO_CONTENT.update(orig)

    def test_09_validate_map_rejects_deferred_content(self):
        orig = dict(h2.OFFICIAL_TO_CONTENT)
        try:
            h2.OFFICIAL_TO_CONTENT[92] = "CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS"
            with self.assertRaises(h2.MapError):
                h2.validate_map()
        finally:
            h2.OFFICIAL_TO_CONTENT.clear()
            h2.OFFICIAL_TO_CONTENT.update(orig)

    def test_10_validate_map_rejects_unknown_content(self):
        orig = dict(h2.OFFICIAL_TO_CONTENT)
        try:
            h2.OFFICIAL_TO_CONTENT[92] = "NOT-A-REAL-CODE"
            with self.assertRaises(h2.MapError):
                h2.validate_map()
        finally:
            h2.OFFICIAL_TO_CONTENT.clear()
            h2.OFFICIAL_TO_CONTENT.update(orig)

    def test_11_validate_map_rejects_size_change(self):
        orig = dict(h2.OFFICIAL_TO_CONTENT)
        try:
            del h2.OFFICIAL_TO_CONTENT[172]
            with self.assertRaises(h2.MapError):
                h2.validate_map()
        finally:
            h2.OFFICIAL_TO_CONTENT.clear()
            h2.OFFICIAL_TO_CONTENT.update(orig)


# --------------------------------------------------------------------------- #
# Environment gates
# --------------------------------------------------------------------------- #


class GateTests(unittest.TestCase):
    def test_dry_run_default_true(self):
        self.assertTrue(h2.is_dry_run({}))

    def test_dry_run_only_literal_false_disables(self):
        self.assertFalse(h2.is_dry_run({"PHASE9U2_H2_DRY_RUN": "false"}))
        self.assertFalse(h2.is_dry_run({"PHASE9U2_H2_DRY_RUN": "FALSE"}))
        self.assertFalse(h2.is_dry_run({"PHASE9U2_H2_DRY_RUN": "  False "}))
        for v in ("0", "no", "", "true", "yes", "off"):
            self.assertTrue(h2.is_dry_run({"PHASE9U2_H2_DRY_RUN": v}), v)

    def test_approval_requires_exact_token(self):
        self.assertFalse(h2.approval_granted({}))
        self.assertFalse(h2.approval_granted({"PHASE9U2_H2_APPROVAL_TOKEN": "wrong"}))
        self.assertTrue(
            h2.approval_granted(
                {"PHASE9U2_H2_APPROVAL_TOKEN": "PHASE9U2-H2-INITIAL-CLASSIFICATION-REVIEWED"}
            )
        )

    def test_rejects_prior_phase_tokens(self):
        for tok in (
            "PHASE9U1E",
            "PHASE9U1E-Q128-CORRECTION-REVIEWED",
            "PHASE9U2_G5_CATALOG_REVIEWED",
            "PHASE-9U2-BATCH0-INITIAL-REVIEWED",
            "PHASE9U2-BATCH0-INITIAL-REVIEWED",
        ):
            self.assertFalse(h2.approval_granted({"PHASE9U2_H2_APPROVAL_TOKEN": tok}), tok)

    def test_batch0_token_is_not_h2_token(self):
        self.assertNotEqual(h2.APPROVAL_TOKEN, b0.APPROVAL_TOKEN)
        self.assertNotEqual(h2.APPROVAL_ENV_VAR, b0.APPROVAL_ENV_VAR)


# --------------------------------------------------------------------------- #
# Shared decision core reused for curriculum-v2
# --------------------------------------------------------------------------- #


def _probe(status, *, code=WAVES_CODE, canonical=True, term="fenômeno ondulatório"):
    bound = None
    if status == "BOUND":
        bound = {
            "content_code": code,
            "rank": 0,
            "discipline_code": "PHYSICS",
            "area_code": "PHYSICS-WAVES",
            "subcontent_code": None,
        }
    return dc.BindingProbe(
        recovered_has_canonical=canonical,
        binding_status=status,
        bound_candidate=bound,
        literal_evidence_term=term if status == "BOUND" else None,
    )


def _ctx(number, code, *, rows=(), binding=None, statement="stmt", vocab=True):
    return dc.QuestionContext(
        official_number=number,
        question_version=dc.QVView(id="qv-x", statement_text=statement),
        classifications=tuple(rows),
        catalog_codes=frozenset({code}),
        target_vocabulary_registered=vocab,
        binding=binding,
    )


def _row(lifecycle, taxo, mode, *, content="", rid=None):
    return dc.RowView(
        id=rid or uuid.uuid4().hex,
        lifecycle=lifecycle,
        content=content,
        status="NEEDS_REVIEW",
        model_version="m",
        prompt_version="p",
        taxonomy_version=taxo,
        classification_mode=mode,
        supersedes_id=None,
    )


class DecisionCoreReuseTests(unittest.TestCase):
    def _decide(self, ctx, code=WAVES_CODE):
        return dc.decide_question_state(ctx, h2.profile_for(code))

    def test_ready_for_initial_accepted(self):
        d = self._decide(_ctx(94, WAVES_CODE, binding=_probe("BOUND")))
        self.assertEqual(d.state, "READY_FOR_INITIAL")
        self.assertEqual(d.reason_code, "DETERMINISTIC_BINDING_BOUND")
        self.assertEqual(d.planned_write.target_taxonomy_version, "curriculum-v2")
        self.assertEqual(d.planned_write.classifier_version, "phase9u2h2-initial-v1")
        self.assertEqual(d.planned_write.prompt_version, "phase9u2h2-curriculum-v2-v1")

    def test_binding_none_is_out_of_scope(self):
        d = self._decide(_ctx(95, "CHEMISTRY-PHYSICAL-EQUILIBRIUM", binding=_probe(None)),
                         code="CHEMISTRY-PHYSICAL-EQUILIBRIUM")
        self.assertEqual(d.state, "OUT_OF_SCOPE")
        self.assertEqual(d.reason_code, "STATEMENT_DOES_NOT_MATCH_CONTROLLED_VOCABULARY")

    def test_binding_needs_review(self):
        d = self._decide(_ctx(104, "CHEMISTRY-PHYSICAL-ACID-BASE", binding=_probe("NEEDS_REVIEW")),
                         code="CHEMISTRY-PHYSICAL-ACID-BASE")
        self.assertEqual(d.state, "NEEDS_REVIEW")

    def test_bound_without_literal_evidence_needs_review(self):
        p = _probe("BOUND", code="PHYSICS-MECHANICS-HYDROSTATICS")
        p = dc.BindingProbe(p.recovered_has_canonical, "BOUND", p.bound_candidate, None)
        d = self._decide(_ctx(134, "PHYSICS-MECHANICS-HYDROSTATICS", binding=p),
                         code="PHYSICS-MECHANICS-HYDROSTATICS")
        self.assertEqual(d.state, "NEEDS_REVIEW")
        self.assertEqual(d.reason_code, "BINDING_BOUND_BUT_NO_LITERAL_EVIDENCE")

    def test_already_classified_when_active_v2_initial_present(self):
        d = self._decide(
            _ctx(94, WAVES_CODE, rows=(_row("ACTIVE", "curriculum-v2", "INITIAL", content=WAVES_CODE),))
        )
        self.assertEqual(d.state, "ALREADY_CLASSIFIED")

    def test_active_other_taxonomy_is_out_of_scope(self):
        d = self._decide(
            _ctx(94, WAVES_CODE, rows=(_row("ACTIVE", "024_chemistry_kinetics", "INITIAL"),))
        )
        self.assertEqual(d.state, "OUT_OF_SCOPE")
        self.assertEqual(d.reason_code, "ACTIVE_CLASSIFICATION_IN_OTHER_TAXONOMY")

    def test_two_active_same_taxonomy_is_error(self):
        d = self._decide(
            _ctx(
                94,
                WAVES_CODE,
                rows=(
                    _row("ACTIVE", "curriculum-v2", "INITIAL", content=WAVES_CODE),
                    _row("ACTIVE", "curriculum-v2", "INITIAL", content=WAVES_CODE),
                ),
            )
        )
        self.assertEqual(d.state, "ERROR")
        self.assertEqual(d.reason_code, "ACTIVE_UNIQUENESS_ALREADY_VIOLATED")

    def test_batch0_kinetics_decision_unchanged_after_extraction(self):
        # Regression: the shared core with the kinetics profile still behaves
        # exactly as Batch-0's public decide_question_state.
        ctx = b0.QuestionContext(
            official_number=101,
            question_version=b0.QVView(id="qv-k", statement_text="stmt"),
            classifications=(),
            catalog_codes=frozenset({"CHEMISTRY-PHYSICAL-KINETICS"}),
            target_vocabulary_registered=True,
            binding=b0.BindingProbe(True, "BOUND", {
                "content_code": "CHEMISTRY-PHYSICAL-KINETICS", "rank": 0,
                "discipline_code": "CHEMISTRY", "area_code": "CHEMISTRY-PHYSICAL",
                "subcontent_code": None,
            }, "cinético"),
        )
        d = b0.decide_question_state(ctx)
        self.assertEqual(d.state, "READY_FOR_INITIAL")
        self.assertEqual(d.planned_write.target_taxonomy_version, "024_chemistry_kinetics")
        self.assertEqual(d.planned_write.classifier_version, "phase9u2-initial-v1")


# --------------------------------------------------------------------------- #
# Integration — Layers B/C/D against SQLite
# --------------------------------------------------------------------------- #


class WritePathTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _snapshot(self, number, code_map=None):
        async with self.factory() as s:
            await b0._begin_read_only(s)
            nodes = await _catalog(s)
            codes = frozenset(n.code for n in nodes)
            return await h2.read_snapshot(s, number, nodes, codes)

    async def test_ready_then_write_creates_exactly_one_row(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            _, v = await _seed_official(s, bk, 94, WAVES_STMT)
            await s.commit()
        snap = await self._snapshot(94)
        decision = h2.decide(snap)
        self.assertEqual(decision.state, "READY_FOR_INITIAL")
        new_id = await h2.apply_initial(self.factory, snap, decision.planned_write)
        self.assertEqual(await _count_pc(self.factory), 1)
        await h2.audit_question(self.factory, WAVES_CODE, snap.pre_rows, snap.pre_count, str(v.id), new_id)
        async with self.factory() as s:
            row = (await s.scalars(select(PedagogicalClassification))).one()
            md = row.metadata_ or {}
            self.assertEqual(row.lifecycle, "ACTIVE")
            self.assertEqual(row.content, WAVES_CODE)
            self.assertEqual(md["taxonomy_version"], "curriculum-v2")
            self.assertEqual(md["classification_mode"], "INITIAL")
            self.assertEqual(row.model_version, "phase9u2h2-initial-v1")
            self.assertEqual(row.prompt_version, "phase9u2h2-curriculum-v2-v1")
            self.assertIsNone(row.supersedes_id)
            for k in ("input_hash", "output_hash", "question_content_hash"):
                self.assertRegex(str(md[k]), r"^[0-9a-f]{64}$")

    async def test_already_classified_second_snapshot_no_write(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            _, v = await _seed_official(s, bk, 130, INDUCTION_STMT)
            await s.commit()
        snap = await self._snapshot(130)
        d = h2.decide(snap)
        self.assertEqual(d.state, "READY_FOR_INITIAL")
        await h2.apply_initial(self.factory, snap, d.planned_write)
        self.assertEqual(await _count_pc(self.factory), 1)
        snap2 = await self._snapshot(130)
        d2 = h2.decide(snap2)
        self.assertEqual(d2.state, "ALREADY_CLASSIFIED")
        with self.assertRaises(b0.ReassertMismatch):
            await h2.apply_initial(self.factory, snap2, d.planned_write)
        self.assertEqual(await _count_pc(self.factory), 1)

    async def test_needs_review_or_out_of_scope_no_write(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            await _seed_official(s, bk, 95, NO_MATCH_STMT)
            await s.commit()
        snap = await self._snapshot(95)
        d = h2.decide(snap)
        self.assertIn(d.state, {"NEEDS_REVIEW", "OUT_OF_SCOPE"})
        self.assertIsNone(d.planned_write)
        self.assertEqual(await _count_pc(self.factory), 0)

    async def test_active_other_taxonomy_blocks_write(self):
        # D6 fires on any ACTIVE row in another taxonomy, before binding logic —
        # so the seeded text just needs a kinetics term for the kinetics seed.
        async with self.factory() as s:
            bk = await _booklet(s)
            _, v = await _seed_official(s, bk, 94, KINETICS_STMT)
            await s.commit()
        async with self.factory() as s:
            v2 = await s.get(QuestionVersion, v.id)
            await _seed_kinetics_active(s, v2)
        snap = await self._snapshot(94)
        d = h2.decide(snap)
        self.assertEqual(d.state, "OUT_OF_SCOPE")
        self.assertEqual(d.reason_code, "ACTIVE_CLASSIFICATION_IN_OTHER_TAXONOMY")
        self.assertEqual(await _count_pc(self.factory), 1)  # only the kinetics row

    async def test_integrity_error_rolls_back(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            _, v = await _seed_official(s, bk, 94, WAVES_STMT)
            await s.commit()
        # Pre-existing ACTIVE curriculum-v2 row with a DIFFERENT idempotency key.
        async with self.factory() as s:
            v2 = await s.get(QuestionVersion, v.id)
            await _seed_v2_active(
                s, v2, WAVES_CODE, "fenômeno ondulatório",
                classifier_version="phase9u1-initial-v1", prompt_version="legacy-v1",
            )
        self.assertEqual(await _count_pc(self.factory), 1)
        snap = await self._snapshot(94)
        d = h2.decide(snap)  # ALREADY_CLASSIFIED at decision level
        self.assertEqual(d.state, "ALREADY_CLASSIFIED")
        # A raw do_write with the H2 versions must hit the partial unique index
        # (an ACTIVE row already exists for (qv, curriculum-v2)) and roll back.
        async with self.factory() as s:
            cand = next(
                c
                for c in ClassificationProposalService(s).recover_candidates(
                    WAVES_STMT, await _catalog(s)
                )
                if c["content_code"] == WAVES_CODE
            )
        planned = h2.PlannedWrite(
            question_version_id=str(v.id),
            target_taxonomy_version="curriculum-v2",
            classifier_version=h2.H2_CLASSIFIER_VERSION,
            prompt_version=h2.H2_PROMPT_VERSION,
            bound_candidate=cand,
            evidence_term="fenômeno ondulatório",
        )
        async with self.factory() as s:
            with self.assertRaises(b0.UniqueCollision):
                await h2.do_write(s, planned, WAVES_CODE)
        self.assertEqual(await _count_pc(self.factory), 1)

    async def test_provider_validation_failure_rolls_back(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            _, v = await _seed_official(s, bk, 94, WAVES_STMT)
            await s.commit()
        snap = await self._snapshot(94)
        d = h2.decide(snap)
        bad = h2.PlannedWrite(
            question_version_id=d.planned_write.question_version_id,
            target_taxonomy_version=d.planned_write.target_taxonomy_version,
            classifier_version=d.planned_write.classifier_version,
            prompt_version=d.planned_write.prompt_version,
            bound_candidate=d.planned_write.bound_candidate,
            evidence_term="ZZZ_NOT_A_LITERAL_EXCERPT",
        )
        async with self.factory() as s:
            with self.assertRaises(b0.WriteFailed):
                await h2.do_write(s, bad, WAVES_CODE)
        self.assertEqual(await _count_pc(self.factory), 0)

    async def test_q107_never_reads_or_writes(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            # even if a booklet row for 107 exists, it must not be classified
            await _seed_official(s, bk, 107, POLY_STMT)
            await s.commit()
        self.assertNotIn(107, h2.OFFICIAL_TO_CONTENT)
        # read_snapshot for 107 yields no content_code -> decide -> BLOCKED
        snap = await self._snapshot(107)
        self.assertIsNone(snap.content_code)
        d = h2.decide(snap)
        self.assertEqual(d.state, "BLOCKED")
        self.assertEqual(await _count_pc(self.factory), 0)


# --------------------------------------------------------------------------- #
# Integration — orchestrator run(), idempotency, protected fingerprints
# --------------------------------------------------------------------------- #


class RunTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()
        # preflight expects production shape (48 nodes / 4 classifications /
        # PostgreSQL) — bypass it for the SQLite orchestrator tests.
        self._orig_preflight = h2.preflight

        async def _fake_preflight(
            factory, *, expected_initial_count=h2.EXPECTED_INITIAL_CLASSIFICATION_COUNT
        ):
            async with factory() as s:
                await b0._begin_read_only(s)
                n = int(await s.scalar(select(func.count()).select_from(PedagogicalClassification)))
            return True, "OK(test)", n

        h2.preflight = _fake_preflight

    async def asyncTearDown(self):
        h2.preflight = self._orig_preflight
        await self.engine.dispose()

    async def _seed_three(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            await _seed_official(s, bk, 94, WAVES_STMT)
            await _seed_official(s, bk, 126, POLY_STMT)
            await _seed_official(s, bk, 130, INDUCTION_STMT)
            await s.commit()

    async def test_dry_run_default_writes_nothing(self):
        await self._seed_three()
        report = await h2.run(self.factory, dry_run=True, approval=False, only=[94, 126, 130])
        self.assertEqual(report.writes_applied, 0)
        self.assertEqual(await _count_pc(self.factory), 0)
        self.assertTrue(all(r.reason_code == "PLANNED_DRY_RUN" for r in report.results))

    async def test_dryrun_false_without_token_writes_nothing(self):
        await self._seed_three()
        report = await h2.run(self.factory, dry_run=False, approval=False, only=[94, 126, 130])
        self.assertEqual(report.writes_applied, 0)
        self.assertEqual(await _count_pc(self.factory), 0)

    async def test_write_then_idempotent_second_pass(self):
        await self._seed_three()
        r1 = await h2.run(self.factory, dry_run=False, approval=True, only=[94, 126, 130])
        self.assertEqual(r1.writes_applied, 3)
        self.assertEqual(await _count_pc(self.factory), 3)
        self.assertIsNone(r1.stopped_on_error)
        self.assertTrue(r1.protected_ok)
        self.assertTrue(r1.integrity_ok)
        r2 = await h2.run(self.factory, dry_run=False, approval=True, only=[94, 126, 130])
        self.assertEqual(r2.writes_applied, 0)
        self.assertEqual(await _count_pc(self.factory), 3)
        self.assertTrue(
            all(r.reason_code == "ACTIVE_INITIAL_TARGET_TAXONOMY" for r in r2.results),
            [r.reason_code for r in r2.results],
        )

    async def test_one_question_at_most_one_insert(self):
        await self._seed_three()
        await h2.run(self.factory, dry_run=False, approval=True, only=[94])
        await h2.run(self.factory, dry_run=False, approval=True, only=[94])
        await h2.run(self.factory, dry_run=False, approval=True, only=[94])
        async with self.factory() as s:
            n = int(
                await s.scalar(
                    select(func.count()).select_from(PedagogicalClassification)
                )
            )
        self.assertEqual(n, 1)

    async def test_protected_questions_untouched(self):
        await self._seed_three()
        async with self.factory() as s:
            bk = await _booklet(s)
            _, v93 = await _seed_official(s, bk, 93, "Reação com velocidade da reação química acelerada.")
            await _seed_kinetics_active(s, v93)
            _, v91 = await _seed_official(s, bk, 91, WAVES_STMT)
            await s.commit()
        async with self.factory() as s:
            await b0._begin_read_only(s)
            before = await h2._protected_fingerprint(s)
        report = await h2.run(self.factory, dry_run=False, approval=True, only=[94, 126, 130, 91, 93, 107, 128])
        async with self.factory() as s:
            await b0._begin_read_only(s)
            after = await h2._protected_fingerprint(s)
        self.assertEqual(before, after)
        self.assertTrue(report.protected_ok)
        states = {r.number: r.state for r in report.results}
        self.assertEqual(states[91], "PROTECTED")
        self.assertEqual(states[93], "PROTECTED")
        self.assertEqual(states[128], "PROTECTED")
        self.assertEqual(states[107], "HUMAN_REVIEW")

    async def test_q107_reported_human_review_pending(self):
        await self._seed_three()
        report = await h2.run(self.factory, dry_run=False, approval=True, only=[107])
        self.assertEqual(len(report.results), 1)
        self.assertEqual(report.results[0].state, "HUMAN_REVIEW")
        self.assertEqual(report.results[0].reason_code, "HUMAN_REVIEW_PENDING")
        self.assertEqual(await _count_pc(self.factory), 0)


# --------------------------------------------------------------------------- #
# Static safety
# --------------------------------------------------------------------------- #


class StaticSafetyTests(unittest.TestCase):
    FILES = [
        ROOT / "tests" / "manual" / "phase9u2h2_curriculum_v2.py",
        ROOT / "tests" / "manual" / "_phase9u2_decision.py",
    ]

    def _tree(self, path):
        return ast.parse(path.read_text(), filename=str(path))

    def test_no_banned_imports(self):
        banned = {"alembic", "openai"}
        for path in self.FILES:
            for node in ast.walk(self._tree(path)):
                if isinstance(node, ast.Import):
                    for a in node.names:
                        self.assertNotIn(a.name.split(".")[0], banned, f"{path}: import {a.name}")
                elif isinstance(node, ast.ImportFrom):
                    root = (node.module or "").split(".")[0]
                    self.assertNotIn(root, banned, f"{path}: from {node.module}")

    def test_no_banned_calls(self):
        banned_attrs = {
            "add",
            "add_all",
            "upgrade",
            "downgrade",
            "stamp",
            "create_node",
            "seed_reference_fixture",
            "supersede_initial_classification",
            "propose_with_provider",
            "execute_sql",
        }
        for path in self.FILES:
            for node in ast.walk(self._tree(path)):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotIn(
                        node.func.attr, banned_attrs, f"{path}: call .{node.func.attr}()"
                    )

    def test_no_pedagogical_classification_constructor(self):
        for path in self.FILES:
            for node in ast.walk(self._tree(path)):
                if isinstance(node, ast.Call):
                    name = None
                    if isinstance(node.func, ast.Name):
                        name = node.func.id
                    elif isinstance(node.func, ast.Attribute):
                        name = node.func.attr
                    self.assertNotEqual(name, "PedagogicalClassification", str(path))

    def test_no_manual_commit_or_insert_sql(self):
        for path in self.FILES:
            tree = self._tree(path)
            # no `.commit(` calls (the service owns commit)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                    self.assertNotEqual(node.func.attr, "commit", f"{path}: manual commit")
            # no INSERT/UPDATE/DELETE string literals (docstrings excluded)
            docstrings = set()
            for node in ast.walk(tree):
                if isinstance(node, (ast.Module, ast.FunctionDef, ast.AsyncFunctionDef, ast.ClassDef)):
                    ds = ast.get_docstring(node, clean=False)
                    if ds:
                        docstrings.add(ds)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant) and isinstance(node.value, str):
                    if node.value in docstrings:
                        continue
                    low = node.value.lower()
                    for kw in ("insert into", "update ", "delete from"):
                        self.assertNotIn(kw, low, f"{path}: SQL literal {kw!r}")

    def test_only_allowed_write_service_method(self):
        """The executor's single write entrypoint is
        ClassificationProposalService.classify_initial_with_provider."""
        path = ROOT / "tests" / "manual" / "phase9u2h2_curriculum_v2.py"
        src = path.read_text()
        self.assertIn("classify_initial_with_provider", src)
        for forbidden in (
            "supersede_initial_classification(",
            "propose_with_provider(",
            "session.add(",
            "session.add_all(",
            ".commit()",
        ):
            self.assertNotIn(forbidden, src, forbidden)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
