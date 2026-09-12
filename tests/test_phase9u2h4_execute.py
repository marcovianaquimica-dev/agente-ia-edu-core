"""PHASE 9U.2-H4 STEP 2 — tests for the HUMAN REVIEW decision executor.

Isolated SQLite. No PostgreSQL, no OpenAI, no Alembic, no production data.
Covers spec items 1–17.
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
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService  # noqa: E402
from agente_ia_edu.services._curriculum_v2_bindings import NEW_AREAS, NEW_CONTENTS  # noqa: E402

import phase9u2h4_execute as h4  # noqa: E402

MIG = ROOT / "migrations" / "versions"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


migration_024 = _load(MIG / "024_chemistry_kinetics.py", "h4_mig024")
migration_025 = _load(MIG / "025_pedagogical_classification_lifecycle.py", "h4_mig025")


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


STATEMENTS = {
    95: "Sal de cobalto muda de cor em presença de água — reação com ∆H < 0.",
    104: "O dióxido de carbono é solubilizado em água e afeta os organismos aquáticos.",
    105: "Aves aquáticas atingidas por óleo precisam recuperar a capacidade de flutuação.",
    112: "Como se proteger de raios no interior de um automóvel não conversível.",
    129: "A utilização do petróleo do pré-sal acarreta um desequilíbrio no ciclo do carbono.",
    134: "Mergulhador preso; variação de pressão e densidade da água para o tempo de descompressão.",
}


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


async def _booklet(session):
    tag = uuid.uuid4().hex[:8]
    inst = Institution(code=f"I-{tag}", name="INEP")
    session.add(inst)
    await session.flush()
    ex = Exam(institution_id=inst.id, code=f"E-{tag}", name="ENEM")
    session.add(ex)
    await session.flush()
    ap = ExamApplication(exam_id=ex.id, year=2020, application_type="REGULAR", day=2)
    session.add(ap)
    await session.flush()
    bk = ExamBooklet(exam_application_id=ap.id, code=f"B-{tag}")
    session.add(bk)
    await session.flush()
    return bk


async def _seed_official(session, booklet, number, statement):
    q = Question(
        validation_status="validated", origin_type="IMPORTED", status="DRAFT", visibility_scope="PRIVATE"
    )
    session.add(q)
    await session.flush()
    v = QuestionVersion(
        question_id=q.id,
        version_kind="official_original",
        statement=statement,
        canonical_text=statement,
        content_hash=hashlib.sha256(f"{number}:{uuid.uuid4()}".encode()).hexdigest(),
    )
    session.add(v)
    await session.flush()
    session.add(
        BookletQuestion(
            exam_booklet_id=booklet.id, question_version_id=v.id, position=number, official_number=number
        )
    )
    await session.flush()
    return q, v


def _pc(qv_id, *, content="X", tv=None, mode=None, lifecycle="ACTIVE", status="CLASSIFIED", source="ai"):
    md = {}
    if tv is not None:
        md["taxonomy_version"] = tv
    if mode is not None:
        md["classification_mode"] = mode
    return PedagogicalClassification(
        question_version_id=qv_id,
        discipline="X",
        content=content,
        subcontent="",
        difficulty="UNKNOWN",
        reasoning_type="UNSPECIFIED",
        status=status,
        source=source,
        lifecycle=lifecycle,
        model_version="m",
        prompt_version="p",
        metadata_=md,
    )


async def _seed_baseline(factory):
    """Production-shaped pre-state: total=18, curriculum-v2 ACTIVE=14.
      * 13 curriculum-v2 ACTIVE INITIAL rows on dummy question_versions
      * 1  curriculum-v2 ACTIVE INITIAL row on Q133's own question_version
      * Q91  : 1 ACTIVE non-v2 (023)         Q93 : 1 ACTIVE INITIAL (024)
      * Q128 : 1 ACTIVE + 1 SUPERSEDED (024)  Q107: 0 rows
      * Q95/104/105/112/129/134 : 0 curriculum-v2 rows
    """
    async with factory() as s:
        q = Question(
            validation_status="validated", origin_type="IMPORTED", status="DRAFT",
            visibility_scope="PRIVATE",
        )
        s.add(q)
        await s.flush()
        for i in range(13):
            v = QuestionVersion(
                question_id=q.id, version_kind="derived",
                canonical_text=f"v2 filler {i}",
                content_hash=hashlib.sha256(f"v2{i}".encode()).hexdigest(),
            )
            s.add(v)
            await s.flush()
            s.add(_pc(v.id, content="PHYSICS-WAVES-PHENOMENA", tv="curriculum-v2", mode="INITIAL"))
        await s.commit()

    async with factory() as s:
        bk = await _booklet(s)
        residual = {}
        for n, stmt in STATEMENTS.items():
            _, v = await _seed_official(s, bk, n, stmt)
            residual[n] = str(v.id)

        _, v91 = await _seed_official(s, bk, 91, "protected 91")
        s.add(_pc(v91.id, content="CHEMISTRY-SOLUTIONS", tv="023_curriculum_taxonomy"))

        _, v93 = await _seed_official(s, bk, 93, "protected 93")
        s.add(_pc(v93.id, content="CHEMISTRY-PHYSICAL-KINETICS", tv="024_chemistry_kinetics", mode="INITIAL"))

        _, v107 = await _seed_official(s, bk, 107, "protected 107")  # no classification row

        _, v128 = await _seed_official(s, bk, 128, "protected 128")
        s.add(_pc(v128.id, content="CHEMISTRY-SOLUTIONS", tv="024_chemistry_kinetics",
                 mode="INITIAL", lifecycle="SUPERSEDED"))
        s.add(_pc(v128.id, content="CHEMISTRY-PHYSICAL-KINETICS", tv="024_chemistry_kinetics", mode="INITIAL"))

        _, v133 = await _seed_official(s, bk, 133, "protected 133")
        s.add(_pc(v133.id, content="PHYSICS-THERMAL-THERMODYNAMICS", tv="curriculum-v2", mode="INITIAL"))
        residual[133] = str(v133.id)
        await s.commit()
    return residual


async def _count(factory):
    async with factory() as s:
        return int(await s.scalar(select(func.count()).select_from(PedagogicalClassification)))


def _decisions_doc(entries):
    return {
        "phase": "9U.2-H4",
        "taxonomy_version": "curriculum-v2",
        "decisions": entries,
    }


def _confirm_entry(number, code=None, curator="c1", note="human-confirmed"):
    resolved = code or h4.H4_EXPECTED_DECISIONS.get(number, "PLACEHOLDER-CODE")
    return {
        "official_number": number,
        "decision": "CONFIRM",
        "content_code": resolved,
        "candidate_content_code": resolved,
        "curator_id": curator,
        "evidence_note": note,
    }


def _all_confirm():
    return [_confirm_entry(n) for n in h4.H4_REVIEW_OFFICIAL_NUMBERS]


def _write_json(tmp: Path, doc) -> str:
    p = tmp / f"dec_{uuid.uuid4().hex[:8]}.json"
    p.write_text(json.dumps(doc, ensure_ascii=False, indent=2), encoding="utf-8")
    return str(p)


# --------------------------------------------------------------------------- #
# Decision-file validation (spec 1–8)
# --------------------------------------------------------------------------- #


class DecisionFileTests(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(__file__).resolve().parent / "_h4tmp"
        self.tmp.mkdir(exist_ok=True)

    def tearDown(self):
        for f in self.tmp.glob("*.json"):
            f.unlink()

    def test_01_valid_six_confirm_accepted(self):
        ds = h4.load_decisions(_write_json(self.tmp, _decisions_doc(_all_confirm())))
        self.assertEqual([d.official_number for d in ds], list(h4.H4_REVIEW_OFFICIAL_NUMBERS))
        for d in ds:
            self.assertEqual(d.content_code, h4.H4_EXPECTED_DECISIONS[d.official_number])
        h4.validate_decision_semantics(ds)

    def test_02_extra_question_rejected(self):
        doc = _decisions_doc(_all_confirm() + [_confirm_entry(94, "PHYSICS-WAVES-PHENOMENA")])
        with self.assertRaises(h4.H4Error):
            h4.load_decisions(_write_json(self.tmp, doc))

    def test_03_missing_question_rejected(self):
        doc = _decisions_doc([_confirm_entry(n) for n in (95, 104, 105, 112, 129)])  # no 134
        with self.assertRaises(h4.H4Error):
            h4.load_decisions(_write_json(self.tmp, doc))

    def test_04_invalid_content_code_rejected(self):
        e = _all_confirm()
        e[0]["content_code"] = "NOT-A-REAL-CODE"
        with self.assertRaises(h4.H4Error):
            h4.load_decisions(_write_json(self.tmp, _decisions_doc(e)))

    def test_04b_content_code_not_matching_approved_rejected(self):
        e = _all_confirm()
        e[0]["content_code"] = "PHYSICS-WAVES-PHENOMENA"  # valid code, wrong for Q95
        with self.assertRaises(h4.H4Error):
            h4.load_decisions(_write_json(self.tmp, _decisions_doc(e)))

    def test_05_reject_decision_rejected_by_confirm_only_executor(self):
        e = _all_confirm()
        e[0]["decision"] = "REJECT"
        with self.assertRaises(h4.H4Error):
            h4.load_decisions(_write_json(self.tmp, _decisions_doc(e)))

    def test_05b_pending_decision_rejected(self):
        e = _all_confirm()
        e[2]["decision"] = "PENDING"
        with self.assertRaises(h4.H4Error):
            h4.load_decisions(_write_json(self.tmp, _decisions_doc(e)))

    def test_06_protected_number_rejected(self):
        e = [_confirm_entry(n) for n in (104, 105, 112, 129, 134)] + [
            {"official_number": 93, "decision": "CONFIRM", "content_code": "CHEMISTRY-PHYSICAL-ACID-BASE",
             "curator_id": "c", "evidence_note": "x"}
        ]
        with self.assertRaises(h4.H4Error):
            h4.load_decisions(_write_json(self.tmp, _decisions_doc(e)))

    def test_07_q133_rejected(self):
        e = [_confirm_entry(n) for n in (95, 104, 105, 112, 129)] + [
            {"official_number": 133, "decision": "CONFIRM", "content_code": "PHYSICS-MECHANICS-HYDROSTATICS",
             "curator_id": "c", "evidence_note": "x"}
        ]
        with self.assertRaises(h4.H4Error):
            h4.load_decisions(_write_json(self.tmp, _decisions_doc(e)))

    def test_08_n05_content_code_rejected(self):
        e = _all_confirm()
        e[0]["content_code"] = "CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS"
        with self.assertRaises(h4.H4Error):
            h4.load_decisions(_write_json(self.tmp, _decisions_doc(e)))

    def test_08b_real_repo_decision_file_holds_the_six_approved_confirms(self):
        # STEP 1 created the file all-PENDING; the curator filled the six approved
        # CONFIRM decisions before the write phase. It must now load cleanly to
        # exactly those six, and pass the structural semantics guard.
        ds = h4.load_decisions(h4.DECISION_FILE)
        self.assertEqual([d.official_number for d in ds], list(h4.H4_REVIEW_OFFICIAL_NUMBERS))
        for d in ds:
            self.assertEqual(d.content_code, h4.H4_EXPECTED_DECISIONS[d.official_number])
        h4.validate_decision_semantics(ds)

    def test_gates(self):
        self.assertTrue(h4.is_dry_run({}))
        self.assertFalse(h4.is_dry_run({"PHASE9U2_H4_DRY_RUN": "false"}))
        self.assertFalse(h4.is_dry_run({"PHASE9U2_H4_DRY_RUN": "FALSE"}))
        self.assertTrue(h4.is_dry_run({"PHASE9U2_H4_DRY_RUN": "0"}))
        self.assertFalse(h4.approval_granted({}))
        self.assertTrue(
            h4.approval_granted({"PHASE9U2_H4_APPROVAL_TOKEN": "PHASE9U2-H4-HUMAN-DECISIONS-APPROVED"})
        )
        for tok in (
            "PHASE9U2-H2-INITIAL-CLASSIFICATION-REVIEWED",
            "PHASE9U2-H3-VOCABULARY-REVIEWED",
            "PHASE9U2_G5_CATALOG_REVIEWED",
            "PHASE-9U2-BATCH0-INITIAL-REVIEWED",
            "PHASE9U1E",
        ):
            self.assertFalse(h4.approval_granted({"PHASE9U2_H4_APPROVAL_TOKEN": tok}), tok)


# --------------------------------------------------------------------------- #
# Pre-flight + state (spec 9–13, 11, 12)
# --------------------------------------------------------------------------- #


class PreflightTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()
        self.residual = await _seed_baseline(self.factory)
        self.tmp = Path(__file__).resolve().parent / "_h4tmp"
        self.tmp.mkdir(exist_ok=True)

    async def asyncTearDown(self):
        for f in self.tmp.glob("*.json"):
            f.unlink()
        await self.engine.dispose()

    async def _pf(self, entries=None):
        ds = h4.load_decisions(_write_json(self.tmp, _decisions_doc(entries or _all_confirm())))
        return await h4.preflight(self.factory, ds, require_postgresql=False)

    async def test_09_first_run_state_all_six_planned(self):
        self.assertEqual(await _count(self.factory), 18)
        pf = await self._pf()
        self.assertTrue(pf.ok, pf.detail)
        self.assertEqual(pf.classification_count, 18)
        self.assertEqual(pf.v2_active_count, 14)
        self.assertEqual(pf.catalog_node_count, 48)
        self.assertEqual(pf.n05_present, 0)
        self.assertEqual(sorted(d.official_number for d in pf.to_write), [95, 104, 105, 112, 129, 134])
        self.assertEqual(pf.already_classified, [])
        for st in pf.q_states.values():
            self.assertIsNotNone(st.question_version_id)
            self.assertTrue(st.catalog_node_active)

    async def test_10_already_classified_recognised(self):
        # give Q95 a correct ACTIVE curriculum-v2 HUMAN_REVIEW row -> count 19
        async with self.factory() as s:
            s.add(
                _pc(uuid.UUID(self.residual[95]), content="CHEMISTRY-PHYSICAL-EQUILIBRIUM",
                    tv="curriculum-v2", mode="HUMAN_REVIEW")
            )
            await s.commit()
        pf = await self._pf()
        self.assertTrue(pf.ok, pf.detail)
        self.assertIn(95, pf.already_classified)
        self.assertEqual(sorted(d.official_number for d in pf.to_write), [104, 105, 112, 129, 134])
        self.assertEqual(pf.classification_count, 19)

    async def test_11_duplicate_active_blocks(self):
        async with self.factory() as s:
            await s.execute(text(f"DROP INDEX {migration_025.ACTIVE_UNIQUE_INDEX}"))
            for _ in range(2):
                s.add(_pc(uuid.UUID(self.residual[104]), content="CHEMISTRY-PHYSICAL-ACID-BASE",
                         tv="curriculum-v2", mode="HUMAN_REVIEW"))
            await s.commit()
        pf = await self._pf()
        self.assertFalse(pf.ok)
        self.assertIn("has_2_ACTIVE", pf.detail)

    async def test_12_conflicting_content_blocks(self):
        async with self.factory() as s:
            s.add(_pc(uuid.UUID(self.residual[112]), content="PHYSICS-EM-INDUCTION",  # wrong for Q112
                     tv="curriculum-v2", mode="HUMAN_REVIEW"))
            await s.commit()
        pf = await self._pf()
        self.assertFalse(pf.ok)
        self.assertIn("HUMAN_REVIEW_REQUIRED", pf.detail)

    async def test_13_integrity_defect_blocks(self):
        async with self.factory() as s:
            await s.execute(text(f"DROP INDEX {migration_025.ACTIVE_UNIQUE_INDEX}"))
            v = (await s.scalars(select(QuestionVersion.id))).first()
            for _ in range(2):
                s.add(_pc(v, content="DUP", tv="dup-tax", mode="X"))
            await s.commit()
        pf = await self._pf()
        self.assertFalse(pf.ok)
        self.assertIn("active_duplicates", pf.detail)


# --------------------------------------------------------------------------- #
# Dry-run + write + idempotency (spec 12–16)
# --------------------------------------------------------------------------- #


class RunTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine, self.factory = await _make_db()
        self.residual = await _seed_baseline(self.factory)
        self.tmp = Path(__file__).resolve().parent / "_h4tmp"
        self.tmp.mkdir(exist_ok=True)
        self.decfile = _write_json(self.tmp, _decisions_doc(_all_confirm()))

    async def asyncTearDown(self):
        for f in self.tmp.glob("*.json"):
            f.unlink()
        await self.engine.dispose()

    async def test_12_dry_run_six_planned_writes(self):
        report = await h4.run(
            self.factory, dry_run=True, approval=False, decision_path=self.decfile,
            require_postgresql=False,
        )
        self.assertTrue(report.decision_file_valid)
        self.assertTrue(report.preflight.ok, report.preflight.detail)
        self.assertEqual(report.planned_writes, 6)
        self.assertEqual([r.state for r in report.results], ["PLANNED_WRITE"] * 6)

    async def test_13_dry_run_writes_nothing(self):
        await h4.run(self.factory, dry_run=True, approval=False, decision_path=self.decfile,
                     require_postgresql=False)
        self.assertEqual(await _count(self.factory), 18)

    async def test_13b_dryrun_false_without_token_writes_nothing(self):
        report = await h4.run(self.factory, dry_run=False, approval=False, decision_path=self.decfile,
                              require_postgresql=False)
        self.assertEqual(report.writes_applied, 0)
        self.assertEqual(await _count(self.factory), 18)

    async def test_14_write_creates_six_classifications(self):
        report = await h4.run(self.factory, dry_run=False, approval=True, decision_path=self.decfile,
                              require_postgresql=False)
        self.assertTrue(report.ok, report.decision_file_detail + " | " + (report.preflight.detail if report.preflight else ""))
        self.assertEqual(report.writes_applied, 6)
        self.assertIsNone(report.stopped_on_error)
        self.assertEqual(await _count(self.factory), 24)
        self.assertEqual(report.final_count, 24)
        self.assertEqual(report.final_v2_active, 20)
        self.assertTrue(report.integrity_ok)
        self.assertTrue(report.protected_ok)
        for r in report.results:
            self.assertEqual(r.state, "CLASSIFIED")

        async with self.factory() as s:
            for n, code in h4.H4_EXPECTED_DECISIONS.items():
                rows = (
                    await s.scalars(
                        select(PedagogicalClassification)
                        .join(QuestionVersion, QuestionVersion.id == PedagogicalClassification.question_version_id)
                        .join(BookletQuestion, BookletQuestion.question_version_id == QuestionVersion.id)
                        .where(BookletQuestion.official_number == n)
                    )
                ).all()
                self.assertEqual(len(rows), 1, n)
                row = rows[0]
                md = row.metadata_ or {}
                self.assertEqual(row.lifecycle, "ACTIVE")
                self.assertEqual(row.status, "CLASSIFIED")
                self.assertEqual(row.content, code)
                self.assertEqual(row.source, "human")
                self.assertIsNone(row.supersedes_id)
                self.assertEqual(md["taxonomy_version"], "curriculum-v2")
                self.assertEqual(md["classification_mode"], "HUMAN_REVIEW")
                self.assertEqual(md["decision_source"], "HUMAN_REVIEW")
                self.assertEqual(md["review_phase"], "PHASE_9U2_H4")
                self.assertEqual(md["content_code"], code)
                self.assertEqual(md["curator_id"], "c1")

    async def test_15_second_run_is_idempotent(self):
        r1 = await h4.run(self.factory, dry_run=False, approval=True, decision_path=self.decfile,
                          require_postgresql=False)
        self.assertEqual(r1.writes_applied, 6)
        self.assertEqual(await _count(self.factory), 24)

        r2 = await h4.run(self.factory, dry_run=False, approval=True, decision_path=self.decfile,
                          require_postgresql=False)
        self.assertEqual(r2.writes_applied, 0)
        self.assertEqual(r2.planned_writes, 0)
        self.assertEqual(await _count(self.factory), 24)
        self.assertEqual(sorted(r2.preflight.already_classified), [95, 104, 105, 112, 129, 134])
        self.assertEqual([r.state for r in r2.results], ["ALREADY_CLASSIFIED"] * 6)
        self.assertTrue(r2.preflight.ok, r2.preflight.detail)

        # third run — still nothing
        r3 = await h4.run(self.factory, dry_run=False, approval=True, decision_path=self.decfile,
                          require_postgresql=False)
        self.assertEqual(r3.writes_applied, 0)
        self.assertEqual(await _count(self.factory), 24)

    async def test_16_protected_fingerprints_preserved(self):
        async with self.factory() as s:
            await h4._begin_read_only(s) if False else None
        # capture via the executor's own helper
        async with self.factory() as s:
            before = await h4._protected_fingerprint(s)
        await h4.run(self.factory, dry_run=False, approval=True, decision_path=self.decfile,
                     require_postgresql=False)
        async with self.factory() as s:
            after = await h4._protected_fingerprint(s)
        self.assertEqual(before, after)
        for n in (91, 93, 107, 128, 133):
            self.assertIn(n, before)

    async def test_16b_only_the_six_are_written(self):
        before_ids = set()
        async with self.factory() as s:
            before_ids = {str(i) for i in (await s.scalars(select(PedagogicalClassification.id))).all()}
        await h4.run(self.factory, dry_run=False, approval=True, decision_path=self.decfile,
                     require_postgresql=False)
        async with self.factory() as s:
            rows = (await s.scalars(select(PedagogicalClassification))).all()
        new_rows = [r for r in rows if str(r.id) not in before_ids]
        self.assertEqual(len(new_rows), 6)
        new_numbers = set()
        async with self.factory() as s:
            for r in new_rows:
                bq = (
                    await s.scalars(
                        select(BookletQuestion)
                        .join(QuestionVersion, QuestionVersion.id == BookletQuestion.question_version_id)
                        .where(QuestionVersion.id == r.question_version_id)
                    )
                ).first()
                new_numbers.add(bq.official_number)
        self.assertEqual(new_numbers, set(h4.H4_REVIEW_OFFICIAL_NUMBERS))


# --------------------------------------------------------------------------- #
# Static safety (spec 17)
# --------------------------------------------------------------------------- #


class StaticSafetyTests(unittest.TestCase):
    FILE = ROOT / "tests" / "manual" / "phase9u2h4_execute.py"

    def _tree(self):
        return ast.parse(self.FILE.read_text(), filename=str(self.FILE))

    def test_no_banned_imports(self):
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Import):
                for a in node.names:
                    self.assertNotIn(a.name.split(".")[0], {"alembic", "openai"})
            elif isinstance(node, ast.ImportFrom):
                self.assertNotIn((node.module or "").split(".")[0], {"alembic", "openai"})

    def test_no_classifier_or_matcher_calls(self):
        banned = {
            "classify_initial_with_provider",
            "propose_with_provider",
            "supersede_initial_classification",
            "match_retrieval_vocabulary",
            "recover_candidates",
            "resolve_initial_controlled_vocabulary_binding",
            "decide_question_state",
            "create_node",
            "seed_reference_fixture",
            "upgrade",
            "downgrade",
            "stamp",
        }
        for node in ast.walk(self._tree()):
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute):
                self.assertNotIn(node.func.attr, banned, f".{node.func.attr}()")
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name):
                self.assertNotIn(node.func.id, banned, f"{node.func.id}()")

    def test_no_raw_sql_write(self):
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
                    self.assertNotIn(kw, low, kw)

    def test_persistence_is_orm_add_plus_single_commit(self):
        src = self.FILE.read_text()
        # exactly one ORM add of the human classification row + exactly one commit
        self.assertEqual(src.count("session.add(row)"), 1)
        self.assertEqual(src.count("await session.commit()"), 1)
        self.assertNotIn("add_all(", src)
        # no session.add of anything else in code (docstring mention is allowed)
        code_adds = [
            n
            for n in ast.walk(self._tree())
            if isinstance(n, ast.Call)
            and isinstance(n.func, ast.Attribute)
            and n.func.attr == "add"
        ]
        self.assertEqual(len(code_adds), 1)

    def test_read_only_guard_used_in_preflight_and_audit(self):
        src = self.FILE.read_text()
        self.assertGreaterEqual(src.count("_begin_read_only(session)"), 2)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
