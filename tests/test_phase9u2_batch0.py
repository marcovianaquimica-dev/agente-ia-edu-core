"""PHASE 9U.2-C — isolated tests for the Batch-0 INITIAL classification executor.

ZERO production access, ZERO OpenAI, ZERO network. Pure fixtures for the
decision core; in-memory SQLite (current schema + migration 024 catalog + the
migration-025 partial unique index) for the IO shell / orchestrator.
"""

from __future__ import annotations

import hashlib
import importlib.util
import sys
import unittest
import uuid
from pathlib import Path

from sqlalchemy import event, func, select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "manual"))

from alembic.operations import Operations  # noqa: E402
from alembic.runtime.migration import MigrationContext  # noqa: E402

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
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    KINETICS_TAXONOMY_VERSION,
    ClassificationProposalService,
)
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService  # noqa: E402

import phase9u2_batch0 as b0  # noqa: E402

MIG_DIR = ROOT / "migrations" / "versions"
KINETICS_STATEMENT = "Nanomateriais catalíticos aumentam a velocidade da reação química."
KINETICS_EVIDENCE = "velocidade da reação"
CANON = "CHEMISTRY-PHYSICAL-KINETICS"


def _load(path: Path, name: str):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


migration_024 = _load(MIG_DIR / "024_chemistry_kinetics.py", "p9u2_mig024")
migration_025 = _load(MIG_DIR / "025_pedagogical_classification_lifecycle.py", "p9u2_mig025")


def _apply(module, direction: str):
    def run(connection):
        ctx = MigrationContext.configure(connection)
        prev = module.op
        module.op = Operations(ctx)
        try:
            getattr(module, direction)()
        finally:
            module.op = prev

    return run


# --------------------------------------------------------------------------- #
# Pure decision-core helpers
# --------------------------------------------------------------------------- #


def _row(lifecycle, taxo, mode, *, content="", status="NEEDS_REVIEW", model="m", prompt="p", rid=None, sup=None):
    return b0.RowView(
        id=rid or uuid.uuid4().hex,
        lifecycle=lifecycle,
        content=content,
        status=status,
        model_version=model,
        prompt_version=prompt,
        taxonomy_version=taxo,
        classification_mode=mode,
        supersedes_id=sup,
    )


def _ctx(number, *, statement="stmt", rows=(), catalog=frozenset({CANON}), vocab=True, binding=None):
    return b0.QuestionContext(
        official_number=number,
        question_version=b0.QVView(id="qv-x", statement_text=statement),
        classifications=tuple(rows),
        catalog_codes=catalog,
        target_vocabulary_registered=vocab,
        binding=binding,
    )


def _probe(status, *, canonical=True, term="cinético"):
    bound = None
    if status == "BOUND":
        bound = {
            "content_code": CANON,
            "rank": 0,
            "discipline_code": "CHEMISTRY",
            "area_code": "CHEMISTRY-PHYSICAL",
            "subcontent_code": None,
        }
    return b0.BindingProbe(
        recovered_has_canonical=canonical,
        binding_status=status,
        bound_candidate=bound,
        literal_evidence_term=term if status == "BOUND" else None,
    )


class DecisionCoreTests(unittest.TestCase):
    def test_A_no_classification_binding_bound_ready(self):
        d = b0.decide_question_state(_ctx(101, rows=(), binding=_probe("BOUND")))
        self.assertEqual(d.state, "READY_FOR_INITIAL")
        self.assertEqual(d.reason_code, "DETERMINISTIC_BINDING_BOUND")
        self.assertIsNotNone(d.planned_write)
        self.assertEqual(d.planned_write.classifier_version, "phase9u2-initial-v1")
        self.assertEqual(d.planned_write.prompt_version, "phase9t3-kinetics-v1")
        self.assertEqual(d.planned_write.target_taxonomy_version, "024_chemistry_kinetics")

    def test_B_active_target_initial_already_classified(self):
        d = b0.decide_question_state(_ctx(102, rows=(_row("ACTIVE", "024_chemistry_kinetics", "INITIAL"),)))
        self.assertEqual(d.state, "ALREADY_CLASSIFIED")
        self.assertIsNone(d.planned_write)

    def test_C_active_023_out_of_scope(self):
        d = b0.decide_question_state(_ctx(103, rows=(_row("ACTIVE", "023_curriculum_taxonomy", None),)))
        self.assertEqual(d.state, "OUT_OF_SCOPE")
        self.assertEqual(d.reason_code, "ACTIVE_CLASSIFICATION_IN_OTHER_TAXONOMY")

    def test_D_active_null_taxonomy_out_of_scope(self):
        d = b0.decide_question_state(_ctx(104, rows=(_row("ACTIVE", None, None),)))
        self.assertEqual(d.state, "OUT_OF_SCOPE")

    def test_Dprime_active_target_mode_null_needs_review(self):
        d = b0.decide_question_state(_ctx(105, rows=(_row("ACTIVE", "024_chemistry_kinetics", None),)))
        self.assertEqual(d.state, "NEEDS_REVIEW")
        self.assertEqual(d.reason_code, "ACTIVE_TARGET_TAXONOMY_NON_INITIAL_MODE")

    def test_E_only_superseded(self):
        d = b0.decide_question_state(_ctx(106, rows=(_row("SUPERSEDED", "024_chemistry_kinetics", "INITIAL"),)))
        self.assertEqual(d.state, "SUPERSEDED_ONLY")

    def test_F_q93_protected(self):
        d = b0.decide_question_state(_ctx(93, rows=(_row("ACTIVE", "024_chemistry_kinetics", "INITIAL"),)))
        self.assertEqual(d.state, "PROTECTED")

    def test_G_q128_protected(self):
        self.assertEqual(b0.decide_question_state(_ctx(128)).state, "PROTECTED")

    def test_H_no_controlled_vocabulary_blocked(self):
        d = b0.decide_question_state(_ctx(107, rows=(), vocab=False, binding=_probe("BOUND")))
        self.assertEqual((d.state, d.reason_code), ("BLOCKED", "NO_CONTROLLED_VOCABULARY_FOR_TARGET"))

    def test_Hprime_canonical_node_missing_blocked(self):
        d = b0.decide_question_state(_ctx(108, rows=(), catalog=frozenset(), binding=_probe("BOUND")))
        self.assertEqual((d.state, d.reason_code), ("BLOCKED", "CANONICAL_CATALOG_NODE_MISSING"))

    def test_Hsecond_no_text_blocked(self):
        d = b0.decide_question_state(_ctx(109, statement="   ", rows=(), binding=_probe("BOUND")))
        self.assertEqual((d.state, d.reason_code), ("BLOCKED", "NO_STATEMENT_TEXT"))

    def test_I_binding_needs_review(self):
        d = b0.decide_question_state(_ctx(110, rows=(), binding=_probe("NEEDS_REVIEW")))
        self.assertEqual(d.state, "NEEDS_REVIEW")
        self.assertEqual(d.reason_code, "CONTROLLED_VOCAB_MATCHED_CANONICAL_NOT_RECOVERED")

    def test_Iprime_binding_none_out_of_scope(self):
        d = b0.decide_question_state(_ctx(111, rows=(), binding=_probe(None)))
        self.assertEqual((d.state, d.reason_code), ("OUT_OF_SCOPE", "STATEMENT_DOES_NOT_MATCH_CONTROLLED_VOCABULARY"))

    def test_J_two_active_same_taxonomy_error(self):
        rows = (_row("ACTIVE", "023_curriculum_taxonomy", None), _row("ACTIVE", "023_curriculum_taxonomy", None))
        d = b0.decide_question_state(_ctx(112, rows=rows))
        self.assertEqual(d.state, "ERROR")
        self.assertEqual(d.reason_code, "ACTIVE_UNIQUENESS_ALREADY_VIOLATED")

    def test_Jprime_two_active_target_error(self):
        rows = (
            _row("ACTIVE", "024_chemistry_kinetics", "INITIAL"),
            _row("ACTIVE", "024_chemistry_kinetics", "INITIAL"),
        )
        self.assertEqual(b0.decide_question_state(_ctx(113, rows=rows)).state, "ERROR")

    def test_O_q91_fixture_out_of_scope(self):
        q91 = _row("ACTIVE", "023_curriculum_taxonomy", None, content="", status="NEEDS_REVIEW",
                   model="phase9e-question91-v1", prompt="phase9e-question91-prompt-v1")
        self.assertEqual(b0.decide_question_state(_ctx(91, rows=(q91,))).state, "OUT_OF_SCOPE")

    def test_P_q91_no_planned_write(self):
        q91 = _row("ACTIVE", "023_curriculum_taxonomy", None)
        d = b0.decide_question_state(_ctx(91, rows=(q91,)))
        self.assertIsNone(d.planned_write)

    # ---- section 22 ----

    def test_22_1_active_023_plus_superseded_023_out_of_scope(self):
        rows = (_row("ACTIVE", "023_curriculum_taxonomy", None), _row("SUPERSEDED", "023_curriculum_taxonomy", None))
        self.assertEqual(b0.decide_question_state(_ctx(114, rows=rows)).state, "OUT_OF_SCOPE")

    def test_22_2_active_target_null_plus_superseded_target_needs_review(self):
        rows = (
            _row("ACTIVE", "024_chemistry_kinetics", None),
            _row("SUPERSEDED", "024_chemistry_kinetics", "INITIAL"),
        )
        self.assertEqual(b0.decide_question_state(_ctx(115, rows=rows)).state, "NEEDS_REVIEW")

    def test_22_3_active_target_non_initial_mode_needs_review(self):
        d = b0.decide_question_state(_ctx(116, rows=(_row("ACTIVE", "024_chemistry_kinetics", "STANDARD"),)))
        self.assertEqual(d.state, "NEEDS_REVIEW")

    def test_22_4_active_target_initial_plus_active_other_is_already_classified(self):
        rows = (
            _row("ACTIVE", "024_chemistry_kinetics", "INITIAL"),
            _row("ACTIVE", "023_curriculum_taxonomy", None),
        )
        d = b0.decide_question_state(_ctx(117, rows=rows))
        self.assertEqual(d.state, "ALREADY_CLASSIFIED")  # documented precedence
        self.assertIn("co_active_other_taxonomy", d.detail)

    def test_22_5_taxonomy_null_in_metadata_out_of_scope(self):
        self.assertEqual(b0.decide_question_state(_ctx(118, rows=(_row("ACTIVE", None, "INITIAL"),))).state, "OUT_OF_SCOPE")

    def test_22_6_mode_absent_never_inferred_initial(self):
        d = b0.decide_question_state(_ctx(119, rows=(_row("ACTIVE", "024_chemistry_kinetics", None),)))
        self.assertNotEqual(d.state, "ALREADY_CLASSIFIED")
        self.assertEqual(d.state, "NEEDS_REVIEW")

    def test_22_7_metadata_none_treated_as_empty(self):
        class _FakeRow:
            id = uuid.uuid4()
            lifecycle = "ACTIVE"
            content = ""
            status = "NEEDS_REVIEW"
            model_version = "m"
            prompt_version = "p"
            supersedes_id = None
            metadata_ = None

        rv = b0.RowView.from_row(_FakeRow())
        self.assertIsNone(rv.taxonomy_version)
        self.assertIsNone(rv.classification_mode)

    def test_22_8_unexpected_lifecycle_error(self):
        d = b0.decide_question_state(_ctx(120, rows=(_row("BOGUS", "024_chemistry_kinetics", "INITIAL"),)))
        self.assertEqual((d.state, d.reason_code), ("ERROR", "UNKNOWN_LIFECYCLE_VALUE"))

    # ---- gates ----

    def test_is_dry_run_default_true_only_false_disables(self):
        self.assertTrue(b0.is_dry_run({}))
        self.assertTrue(b0.is_dry_run({"PHASE9U2_DRY_RUN": "true"}))
        self.assertTrue(b0.is_dry_run({"PHASE9U2_DRY_RUN": "0"}))
        self.assertTrue(b0.is_dry_run({"PHASE9U2_DRY_RUN": "no"}))
        self.assertFalse(b0.is_dry_run({"PHASE9U2_DRY_RUN": "false"}))
        self.assertFalse(b0.is_dry_run({"PHASE9U2_DRY_RUN": "FALSE"}))
        # never reads the 9U.1-E variable
        self.assertTrue(b0.is_dry_run({"PHASE9U1E_DRY_RUN": "false"}))

    def test_approval_requires_exact_9u2_token(self):
        self.assertFalse(b0.approval_granted({}))
        self.assertFalse(b0.approval_granted({"PHASE9U2_APPROVAL_TOKEN": "wrong"}))
        self.assertTrue(b0.approval_granted({"PHASE9U2_APPROVAL_TOKEN": "PHASE-9U2-BATCH0-INITIAL-REVIEWED"}))
        # never accepts a Phase 9U.1-E token
        self.assertFalse(b0.approval_granted({"PHASE9U1E_APPROVAL_TOKEN": "PHASE-9U1E-Q128-CORRECTION-REVIEWED"}))


# --------------------------------------------------------------------------- #
# SQLite integration harness
# --------------------------------------------------------------------------- #


async def _make_db():
    engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)
    factory = async_sessionmaker(engine, expire_on_commit=False)
    async with factory() as session:
        await CurriculumTaxonomyService(session).seed_reference_fixture()
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


async def _seed_official_question(session, booklet, official_number, *, statement=KINETICS_STATEMENT, position=None):
    q = Question(
        validation_status="validated", origin_type="IMPORTED", status="DRAFT", visibility_scope="PRIVATE"
    )
    session.add(q)
    await session.flush()
    v = QuestionVersion(
        question_id=q.id,
        version_kind="official_original",
        canonical_text=statement,
        content_hash=hashlib.sha256(f"{statement}:{official_number}".encode()).hexdigest(),
    )
    session.add(v)
    await session.flush()
    bq = BookletQuestion(
        exam_booklet_id=booklet.id,
        question_version_id=v.id,
        position=position or official_number,
        official_number=official_number,
    )
    session.add(bq)
    await session.flush()
    return q, v


async def _seed_active_initial(session, version, *, model_version="phase9u1-initial-v1"):
    """Seed a pre-existing ACTIVE INITIAL/target row via the real service."""
    catalog = list((await session.scalars(select(CatalogNode).where(CatalogNode.active.is_(True)))).all())
    candidate = next(
        c
        for c in ClassificationProposalService(session).recover_candidates(version.canonical_text, catalog)
        if c["content_code"] == CANON
    )
    provider = b0.DeterministicKineticsProvider(candidate=candidate, evidence_text=KINETICS_EVIDENCE)
    from agente_ia_edu.providers.router import ProviderRouter

    return await ClassificationProposalService(session).classify_initial_with_provider(
        version.id,
        ProviderRouter(text_providers=[provider], embedding_providers=[]),
        target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
        classifier_version=model_version,
        prompt_version="phase9t3-kinetics-v1",
    )


async def _count_pc(factory):
    async with factory() as s:
        return int(await s.scalar(select(func.count()).select_from(PedagogicalClassification)))


class OrchestratorTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_dry_run_default_plans_no_write(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            await _seed_official_question(s, bk, 95)
            await s.commit()
        report = await b0.run(self.factory, dry_run=True, approval=False)
        r = next(x for x in report.results if x.number == 95)
        self.assertEqual(r.state, "READY_FOR_INITIAL")
        self.assertEqual(r.reason_code, "PLANNED_DRY_RUN")
        self.assertEqual(report.writes_applied, 0)
        self.assertEqual(await _count_pc(self.factory), 0)

    async def test_write_mode_without_approval_no_write(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            await _seed_official_question(s, bk, 95)
            await s.commit()
        report = await b0.run(self.factory, dry_run=False, approval=False)
        r = next(x for x in report.results if x.number == 95)
        self.assertEqual(r.reason_code, "PLANNED_PENDING_APPROVAL")
        self.assertEqual(report.writes_applied, 0)
        self.assertEqual(await _count_pc(self.factory), 0)

    async def test_write_with_approval_creates_initial_and_audits(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            await _seed_official_question(s, bk, 95)
            await s.commit()
        report = await b0.run(self.factory, dry_run=False, approval=True)
        r = next(x for x in report.results if x.number == 95)
        self.assertEqual(r.reason_code, "INITIAL_CLASSIFICATION_CREATED")
        self.assertIsNotNone(r.new_id)
        self.assertEqual(report.writes_applied, 1)
        self.assertIsNone(report.stopped_on_error)
        async with self.factory() as s:
            row = (await s.scalars(select(PedagogicalClassification))).one()
            md = row.metadata_ or {}
        self.assertEqual(row.lifecycle, "ACTIVE")
        self.assertEqual(row.content, CANON)
        self.assertEqual(md["taxonomy_version"], "024_chemistry_kinetics")
        self.assertEqual(md["classification_mode"], "INITIAL")
        self.assertEqual(row.model_version, "phase9u2-initial-v1")
        self.assertEqual(row.prompt_version, "phase9t3-kinetics-v1")
        self.assertIsNone(row.supersedes_id)
        for key in ("input_hash", "output_hash", "question_content_hash"):
            self.assertRegex(str(md[key]), r"^[0-9a-f]{64}$")

    async def test_second_run_is_already_classified_no_third_row(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            await _seed_official_question(s, bk, 95)
            await s.commit()
        await b0.run(self.factory, dry_run=False, approval=True)
        report2 = await b0.run(self.factory, dry_run=False, approval=True)
        r = next(x for x in report2.results if x.number == 95)
        self.assertEqual(r.state, "ALREADY_CLASSIFIED")
        self.assertEqual(report2.writes_applied, 0)
        self.assertEqual(await _count_pc(self.factory), 1)

    async def test_protected_numbers_never_written(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            await _seed_official_question(s, bk, 93)
            await _seed_official_question(s, bk, 128)
            await s.commit()
        report = await b0.run(self.factory, dry_run=False, approval=True, only=[93, 128])
        for n in (93, 128):
            self.assertEqual(next(x for x in report.results if x.number == n).state, "PROTECTED")
        self.assertEqual(report.writes_applied, 0)
        self.assertEqual(await _count_pc(self.factory), 0)
        self.assertTrue(report.q93_unchanged and report.q128_unchanged)

    async def test_protected_short_circuits_before_snapshot_read(self):
        # #93 has a classification row; if the orchestrator read a per-question
        # snapshot before applying D0 it would populate `qv`. After the fix it
        # must not — PROTECTED is decided without touching #93's rows.
        async with self.factory() as s:
            bk = await _booklet(s)
            _, v93 = await _seed_official_question(s, bk, 93)
            await _seed_active_initial(s, v93, model_version="pre-existing-v1")
        report = await b0.run(self.factory, dry_run=False, approval=True, only=[93])
        r = next(x for x in report.results if x.number == 93)
        self.assertEqual((r.state, r.reason_code), ("PROTECTED", "PROTECTED_QUESTION"))
        self.assertIsNone(r.qv)
        self.assertEqual(report.writes_applied, 0)

    async def test_run_level_active_duplicate_forces_nonzero_exit(self):
        async with self.engine.begin() as conn:
            await conn.execute(text(f"DROP INDEX {migration_025.ACTIVE_UNIQUE_INDEX}"))
        async with self.factory() as s:
            bk = await _booklet(s)
            _, v = await _seed_official_question(s, bk, 300)
            await _seed_active_initial(s, v, model_version="dup-a-v1")
            await _seed_active_initial(s, v, model_version="dup-b-v1")  # 2nd ACTIVE, same (qv, taxo)
        report = await b0.run(self.factory, dry_run=True, approval=False, only=[300])
        self.assertGreaterEqual(report.active_duplicates, 1)
        self.assertFalse(report.run_level_ok)
        self.assertEqual(report.exit_code, 1)

    async def test_run_level_orphan_supersession_forces_nonzero_exit(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            _, v = await _seed_official_question(s, bk, 301)
            s.add(
                PedagogicalClassification(
                    question_version_id=v.id, discipline="", content="", subcontent="", difficulty="UNKNOWN",
                    reasoning_type="UNSPECIFIED", status="NEEDS_REVIEW", source="ai", lifecycle="SUPERSEDED",
                    model_version="x", prompt_version="y", supersedes_id=uuid.uuid4(),  # points nowhere
                    metadata_={"taxonomy_version": "023_curriculum_taxonomy"},
                )
            )
            await s.commit()
        report = await b0.run(self.factory, dry_run=True, approval=False, only=[301])
        self.assertEqual(report.orphan_supersessions, 1)
        self.assertFalse(report.run_level_ok)
        self.assertEqual(report.exit_code, 1)

    async def test_only_unknown_number_is_skipped_not_error(self):
        async with self.factory() as s:
            await _booklet(s)
            await s.commit()
        report = await b0.run(self.factory, dry_run=True, approval=False, only=[9999])
        r = next(x for x in report.results if x.number == 9999)
        self.assertEqual((r.state, r.reason_code), ("SKIPPED", "NOT_IN_OFFICIAL_INVENTORY"))
        self.assertIsNone(report.stopped_on_error)

    async def test_do_write_integrity_error_rolls_back(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            _, v = await _seed_official_question(s, bk, 96)
            await s.commit()
        async with self.factory() as s:
            await _seed_active_initial(s, v)  # pre-existing ACTIVE INITIAL/target row
        probe = b0.probe_binding(KINETICS_STATEMENT, [
            *(await self._catalog())
        ])
        planned = b0.PlannedWrite(
            question_version_id=str(v.id),
            target_taxonomy_version="024_chemistry_kinetics",
            classifier_version="phase9u2-initial-v1",
            prompt_version="phase9t3-kinetics-v1",
            bound_candidate=probe.bound_candidate,
            evidence_term=probe.literal_evidence_term,
        )
        async with self.factory() as s:
            with self.assertRaises(b0.UniqueCollision):
                await b0.do_write(s, planned)
        self.assertEqual(await _count_pc(self.factory), 1)  # no new row

    async def test_do_write_provider_validation_failure_rolls_back(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            _, v = await _seed_official_question(s, bk, 97)
            await s.commit()
        probe = b0.probe_binding(KINETICS_STATEMENT, [*(await self._catalog())])
        planned = b0.PlannedWrite(
            question_version_id=str(v.id),
            target_taxonomy_version="024_chemistry_kinetics",
            classifier_version="phase9u2-initial-v1",
            prompt_version="phase9t3-kinetics-v1",
            bound_candidate=probe.bound_candidate,
            evidence_term="ZZZ_NOT_A_LITERAL_EXCERPT",  # persist path rejects non-literal evidence
        )
        async with self.factory() as s:
            with self.assertRaises(b0.WriteFailed):
                await b0.do_write(s, planned)
        self.assertEqual(await _count_pc(self.factory), 0)

    async def test_audit_question_detects_historical_change(self):
        # drop the at-most-one-ACTIVE index so we can build an artificial 2-row fixture
        async with self.engine.begin() as conn:
            await conn.execute(text(f"DROP INDEX {migration_025.ACTIVE_UNIQUE_INDEX}"))
        async with self.factory() as s:
            bk = await _booklet(s)
            _, v = await _seed_official_question(s, bk, 98)
            hist = await _seed_active_initial(s, v, model_version="historical-v1")
            hist_id = str(hist.id)
            new = await _seed_active_initial(s, v, model_version="pretend-new-v1")
            new_id = str(new.id)
        tampered = {hist_id: ("different", "key", "tuple")}
        with self.assertRaises(b0.AuditFailure):
            await b0.audit_question(self.factory, 98, tampered, 1, str(v.id), new_id)

    async def test_run_level_audit_preserves_q91_q93_q128(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            _, v91 = await _seed_official_question(s, bk, 91)
            await _seed_official_question(s, bk, 93)
            await _seed_official_question(s, bk, 128)
            await _seed_official_question(s, bk, 95)
            await s.commit()
        async with self.factory() as s:
            row = PedagogicalClassification(
                question_version_id=v91.id, discipline="", content="", subcontent="", difficulty="UNKNOWN",
                reasoning_type="UNSPECIFIED", status="NEEDS_REVIEW", source="ai", lifecycle="ACTIVE",
                model_version="phase9e-question91-v1", prompt_version="phase9e-question91-prompt-v1",
                metadata_={"taxonomy_version": "023_curriculum_taxonomy"},
            )
            s.add(row)
            await s.commit()
        report = await b0.run(self.factory, dry_run=False, approval=True)
        self.assertEqual(next(x for x in report.results if x.number == 91).state, "OUT_OF_SCOPE")
        self.assertEqual(next(x for x in report.results if x.number == 93).state, "PROTECTED")
        self.assertEqual(next(x for x in report.results if x.number == 128).state, "PROTECTED")
        self.assertEqual(next(x for x in report.results if x.number == 95).reason_code, "INITIAL_CLASSIFICATION_CREATED")
        self.assertTrue(report.q91_unchanged)
        self.assertTrue(report.q93_unchanged)
        self.assertTrue(report.q128_unchanged)
        self.assertTrue(report.supersedes_unchanged)
        self.assertEqual(report.active_duplicates, 0)

    async def test_needs_review_and_out_of_scope_not_fatal(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            _, v_oos = await _seed_official_question(s, bk, 100)
            await _seed_official_question(s, bk, 95)  # READY
            await s.commit()
        async with self.factory() as s:
            s.add(PedagogicalClassification(
                question_version_id=v_oos.id, discipline="", content="", subcontent="", difficulty="UNKNOWN",
                reasoning_type="UNSPECIFIED", status="NEEDS_REVIEW", source="ai", lifecycle="ACTIVE",
                model_version="x", prompt_version="y",
                metadata_={"taxonomy_version": "023_curriculum_taxonomy"},
            ))
            await s.commit()
        report = await b0.run(self.factory, dry_run=True, approval=False)
        self.assertIsNone(report.stopped_on_error)
        self.assertEqual(report.exit_code, 0)
        self.assertTrue(report.has_pending)
        self.assertEqual(next(x for x in report.results if x.number == 100).state, "OUT_OF_SCOPE")
        self.assertEqual(next(x for x in report.results if x.number == 95).state, "READY_FOR_INITIAL")

    async def test_error_stops_batch_and_skips_rest(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            _, v = await _seed_official_question(s, bk, 200, position=200)
            # a SECOND booklet with the same official_number pointing to a different
            # question_version → official_number 200 resolves to two question_versions
            bk2 = await _booklet(s)
            _, v2 = await _seed_official_question(s, bk2, 201, position=1)
            s.add(
                BookletQuestion(
                    exam_booklet_id=bk2.id, question_version_id=v2.id, position=2, official_number=200
                )
            )
            await _seed_official_question(s, bk, 95, position=95)
            await s.commit()
        report = await b0.run(self.factory, dry_run=True, approval=False, only=[200, 95])
        r = next(x for x in report.results if x.number == 200)
        self.assertEqual((r.state, r.reason_code), ("ERROR", "AMBIGUOUS_OFFICIAL_NUMBER"))
        self.assertEqual(report.stopped_on_error, 200)
        self.assertFalse(any(x.number == 95 for x in report.results))
        self.assertEqual(report.exit_code, 1)

    async def test_one_write_per_transaction(self):
        async with self.factory() as s:
            bk = await _booklet(s)
            await _seed_official_question(s, bk, 95, position=95)
            await _seed_official_question(s, bk, 99, position=99)
            await s.commit()
        inserts: list[str] = []

        @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
        def _rec(conn, cursor, statement, parameters, context, executemany):  # noqa: ANN001
            if "insert into pedagogical_classifications" in statement.lower():
                inserts.append(statement)

        try:
            report = await b0.run(self.factory, dry_run=False, approval=True, only=[95, 99])
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", _rec)
        self.assertEqual(report.writes_applied, 2)
        self.assertEqual(len(inserts), 2)  # exactly one INSERT per written question

    async def _catalog(self):
        async with self.factory() as s:
            return list((await s.scalars(select(CatalogNode).where(CatalogNode.active.is_(True)))).all())


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
