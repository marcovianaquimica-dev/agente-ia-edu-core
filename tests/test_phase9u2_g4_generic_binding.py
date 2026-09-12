"""PHASE 9U.2-G4 — the deterministic INITIAL binding is now registry-driven, not
hard-coded to kinetics.

Isolated: SQLite for the pure/service logic, plus migration 024's catalog and the
migration-025 partial unique index. No PostgreSQL, no OpenAI, no real provider,
no production data. The kinetics binding must keep behaving EXACTLY as before; a
freshly-registered non-kinetics binding must work through the same pipeline; and
every unregistered / mismatched / ambiguous case must fail closed with no silent
fallback to kinetics.
"""

from __future__ import annotations

import contextlib
import hashlib
import importlib.util
import json
import sys
import unittest
import uuid
from pathlib import Path

from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "src"))
sys.path.insert(0, str(ROOT / "tests" / "manual"))

from agente_ia_edu.db.base import Base  # noqa: E402
from agente_ia_edu.db.models import CatalogNode, PedagogicalClassification, Question, QuestionVersion  # noqa: E402
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult  # noqa: E402
from agente_ia_edu.providers.router import ProviderRouter  # noqa: E402
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService  # noqa: E402
import agente_ia_edu.services.curriculum_classification as cc  # noqa: E402
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    ClassificationProposalService,
    DeterministicInitialBinding,
    KINETICS_TAXONOMY_VERSION,
    RetrievalVocabularyEntry,
    registered_initial_taxonomy_versions,
    registered_initial_vocabularies,
    resolve_registered_initial_binding,
)

MIG = ROOT / "migrations" / "versions"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


migration_024 = _load(MIG / "024_chemistry_kinetics.py", "g4_mig024")
migration_025 = _load(MIG / "025_pedagogical_classification_lifecycle.py", "g4_mig025")

KIN_STATEMENT = "Nanomateriais catalíticos aumentam a velocidade da reação química."
KIN_EVIDENCE = "velocidade da reação"
KIN_CODE = "CHEMISTRY-PHYSICAL-KINETICS"

# --- a fresh, non-kinetics test binding -----------------------------------------
TEST_TAXO = "curriculum-g4-test"
TEST_DISC, TEST_AREA, TEST_CONTENT = "G4TEST", "G4TEST-AREA", "G4TEST-AREA-TOPIC"
TEST_PRIMARY = "conceito determinístico g4"
TEST_STATEMENT = f"Uma questão que avalia o {TEST_PRIMARY} de forma clara e objetiva."
TEST_VOCAB = RetrievalVocabularyEntry(
    canonical_code=TEST_CONTENT,
    primary_terms=(TEST_PRIMARY,),
    specific_terms=("termo específico g4",),
    contextual_expressions=("expressão contextual g4",),
    generic_terms=("questão", "conceito"),
    version="g4-test-v1",
)
TEST_BINDING = DeterministicInitialBinding(
    taxonomy_version=TEST_TAXO, canonical_code=TEST_CONTENT, parent_code=TEST_AREA, vocabulary=TEST_VOCAB
)


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


@contextlib.contextmanager
def registered(*bindings: DeterministicInitialBinding):
    """Temporarily add bindings to the single registry, then restore it exactly."""
    snapshot = dict(cc._DETERMINISTIC_INITIAL_BINDINGS)
    legacy = dict(cc._INITIAL_CONTROLLED_VOCABULARIES)
    try:
        for b in bindings:
            cc._DETERMINISTIC_INITIAL_BINDINGS[(b.taxonomy_version, b.canonical_code)] = b
        cc._INITIAL_CONTROLLED_VOCABULARIES.clear()
        cc._INITIAL_CONTROLLED_VOCABULARIES.update(
            {
                tv: resolve_registered_initial_binding(tv).vocabulary
                for tv in registered_initial_taxonomy_versions()
                if resolve_registered_initial_binding(tv) is not None
            }
        )
        yield
    finally:
        cc._DETERMINISTIC_INITIAL_BINDINGS.clear()
        cc._DETERMINISTIC_INITIAL_BINDINGS.update(snapshot)
        cc._INITIAL_CONTROLLED_VOCABULARIES.clear()
        cc._INITIAL_CONTROLLED_VOCABULARIES.update(legacy)


class FakeProvider:
    provider = "fake"

    def __init__(self, response, *, model="fake-model"):
        self._response = response
        self._model = model
        self.calls = 0

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        self.calls += 1
        return TextGenerationResult(json.dumps(self._response), self.provider, self._model)


def _response(candidate, evidence, *, status="PROPOSED", override=None):
    base = {
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
        "taxonomy_coverage_evidence": ["Determinístico."],
        "review_reason": None,
        "visual_dependency": False,
        "status": status,
    }
    if override:
        base.update(override)
    return base


async def _make_db(*, with_test_catalog: bool):
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
    if with_test_catalog:
        async with factory() as s:
            svc = CurriculumTaxonomyService(s)
            disc = await svc.create_node("G4 Test", "DISCIPLINE", TEST_DISC, None, 90)
            area = await svc.create_node("G4 Area", "AREA", TEST_AREA, disc.id, 1)
            await svc.create_node("G4 Topic", "CONTENT", TEST_CONTENT, area.id, 1)
            await s.commit()
    return engine, factory


async def _seed_question(session, statement, salt="", *, commit=False):
    q = Question(validation_status="validated", origin_type="IMPORTED", status="DRAFT", visibility_scope="PRIVATE")
    session.add(q)
    await session.flush()
    v = QuestionVersion(
        question_id=q.id, version_kind="official_original", canonical_text=statement,
        content_hash=hashlib.sha256(f"{statement}:{salt}:{uuid.uuid4()}".encode()).hexdigest(),
    )
    session.add(v)
    await session.flush()
    if commit:
        await session.commit()
    return v


async def _catalog(session):
    return list((await session.scalars(select(CatalogNode).where(CatalogNode.active.is_(True)))).all())


async def _kin_candidate(session):
    cands = ClassificationProposalService(session).recover_candidates(KIN_STATEMENT, await _catalog(session))
    return next(c for c in cands if c["content_code"] == KIN_CODE)


async def _test_candidate(session):
    cands = ClassificationProposalService(session).recover_candidates(TEST_STATEMENT, await _catalog(session))
    return next(c for c in cands if c["content_code"] == TEST_CONTENT)


class PureRegistryTests(unittest.TestCase):
    def test_dataclass_rejects_vocabulary_code_mismatch(self):
        with self.assertRaises(ValueError):
            DeterministicInitialBinding(
                taxonomy_version="x", canonical_code="A", parent_code="P",
                vocabulary=RetrievalVocabularyEntry("B", (), (), (), (), "v"),
            )

    def test_registry_has_kinetics_plus_curriculum_v2(self):
        # G5 added the curriculum-v2 generation; the kinetics binding is unchanged.
        self.assertIn(KINETICS_TAXONOMY_VERSION, registered_initial_taxonomy_versions())
        self.assertIn("curriculum-v2", registered_initial_taxonomy_versions())
        self.assertIn(KIN_CODE, [v.canonical_code for v in registered_initial_vocabularies()])
        kb = resolve_registered_initial_binding(KINETICS_TAXONOMY_VERSION)
        self.assertEqual((kb.canonical_code, kb.parent_code), (KIN_CODE, "CHEMISTRY-PHYSICAL"))
        cv2 = [cc for (tv, cc) in cc._DETERMINISTIC_INITIAL_BINDINGS if tv == "curriculum-v2"]
        self.assertEqual(len(cv2), 19)
        # N05 stays deferred while Q107 is HUMAN_REVIEW
        self.assertIsNone(resolve_registered_initial_binding("curriculum-v2", "CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS"))
        # every curriculum-v2 binding's vocabulary canonical_code matches its content code
        for (tv, code), b in cc._DETERMINISTIC_INITIAL_BINDINGS.items():
            if tv == "curriculum-v2":
                self.assertEqual(b.vocabulary.canonical_code, code)

    def test_15_no_silent_kinetics_fallback(self):
        # unregistered taxonomy -> None, never the kinetics binding
        self.assertIsNone(resolve_registered_initial_binding("totally-unregistered"))
        self.assertIsNone(resolve_registered_initial_binding("totally-unregistered", "WHATEVER"))
        with registered(TEST_BINDING):
            # a statement full of kinetics terms, asked under the TEST taxonomy,
            # must NOT resolve to the kinetics binding
            b = resolve_registered_initial_binding(TEST_TAXO, TEST_CONTENT)
            self.assertEqual(b.canonical_code, TEST_CONTENT)
            self.assertIsNone(resolve_registered_initial_binding(TEST_TAXO, KIN_CODE))

    def test_16_two_contents_coexist_under_one_taxonomy(self):
        second = DeterministicInitialBinding(
            taxonomy_version=TEST_TAXO, canonical_code="G4TEST-AREA-OTHER", parent_code=TEST_AREA,
            vocabulary=RetrievalVocabularyEntry("G4TEST-AREA-OTHER", ("outro conceito g4",), (), (), (), "g4-other-v1"),
        )
        with registered(TEST_BINDING, second):
            self.assertEqual(resolve_registered_initial_binding(TEST_TAXO, TEST_CONTENT).canonical_code, TEST_CONTENT)
            self.assertEqual(resolve_registered_initial_binding(TEST_TAXO, "G4TEST-AREA-OTHER").canonical_code, "G4TEST-AREA-OTHER")
            # ambiguous without a content code
            self.assertIsNone(resolve_registered_initial_binding(TEST_TAXO))

    def test_14_q107_content_has_no_binding(self):
        # nothing registers CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS -> engine cannot classify Q107
        self.assertIsNone(resolve_registered_initial_binding("curriculum-v2", "CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS"))

    def test_resolve_binding_disabled_vocabulary_returns_none(self):
        disabled = DeterministicInitialBinding(
            taxonomy_version="dis-taxo", canonical_code="DIS-CODE", parent_code="DIS-AREA",
            vocabulary=RetrievalVocabularyEntry("DIS-CODE", ("termo dis",), (), (), (), "dis-v1", enabled=False),
        )
        with registered(disabled):
            self.assertIsNone(
                ClassificationProposalService.resolve_initial_controlled_vocabulary_binding(
                    "um texto com termo dis presente", "dis-taxo", [], content_code="DIS-CODE"
                )
            )


class ServiceBindingTests(unittest.IsolatedAsyncioTestCase):
    async def test_01_kinetics_still_binds_via_generic_path(self):
        engine, factory = await _make_db(with_test_catalog=False)
        try:
            async with factory() as s:
                v = await _seed_question(s, KIN_STATEMENT, "kin1")
                cand = await _kin_candidate(s)
                provider = FakeProvider(_response(cand, KIN_EVIDENCE))
                rec = await ClassificationProposalService(s).classify_initial_with_provider(
                    v.id, ProviderRouter(text_providers=[provider], embedding_providers=[]),
                    target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                    classifier_version="g4-kin-v1", prompt_version="phase9t3-kinetics-v1",
                )
            self.assertEqual(rec.lifecycle, "ACTIVE")
            self.assertEqual(rec.content, KIN_CODE)
            md = rec.metadata_ or {}
            self.assertEqual(md["taxonomy_version"], KINETICS_TAXONOMY_VERSION)
            self.assertEqual(md["classification_mode"], "INITIAL")
        finally:
            await engine.dispose()

    async def test_02_generic_binding_bound_and_persisted(self):
        engine, factory = await _make_db(with_test_catalog=True)
        try:
            with registered(TEST_BINDING):
                async with factory() as s:
                    v = await _seed_question(s, TEST_STATEMENT, "t02")
                    cand = await _test_candidate(s)
                    provider = FakeProvider(_response(cand, TEST_PRIMARY))
                    rec = await ClassificationProposalService(s).classify_initial_with_provider(
                        v.id, ProviderRouter(text_providers=[provider], embedding_providers=[]),
                        target_taxonomy_version=TEST_TAXO, target_content_code=TEST_CONTENT,
                        classifier_version="g4-generic-v1", prompt_version="g4-prompt-v1",
                    )
            self.assertEqual(rec.lifecycle, "ACTIVE")
            self.assertEqual(rec.content, TEST_CONTENT)
            md = rec.metadata_ or {}
            self.assertEqual(md["taxonomy_version"], TEST_TAXO)
            self.assertEqual(md["classification_mode"], "INITIAL")
            self.assertEqual(rec.model_version, "g4-generic-v1")
            self.assertEqual(rec.prompt_version, "g4-prompt-v1")
            self.assertIsNone(rec.supersedes_id)
            for key in ("input_hash", "output_hash", "question_content_hash"):
                self.assertRegex(str(md[key]), r"^[0-9a-f]{64}$")
        finally:
            await engine.dispose()

    async def test_03_unregistered_taxonomy_raises(self):
        engine, factory = await _make_db(with_test_catalog=True)
        try:
            async with factory() as s:
                v = await _seed_question(s, TEST_STATEMENT, "u03")
                with self.assertRaises(ValueError):
                    await ClassificationProposalService(s).classify_initial_with_provider(
                        v.id, ProviderRouter(text_providers=[FakeProvider({})], embedding_providers=[]),
                        target_taxonomy_version="not-registered-taxo",
                        classifier_version="x", prompt_version="y",
                    )
        finally:
            await engine.dispose()

    async def test_04_unregistered_content_raises(self):
        engine, factory = await _make_db(with_test_catalog=True)
        try:
            with registered(TEST_BINDING):
                async with factory() as s:
                    v = await _seed_question(s, TEST_STATEMENT, "c04")
                    with self.assertRaises(ValueError):
                        await ClassificationProposalService(s).classify_initial_with_provider(
                            v.id, ProviderRouter(text_providers=[FakeProvider({})], embedding_providers=[]),
                            target_taxonomy_version=TEST_TAXO, target_content_code="G4TEST-AREA-NOPE",
                            classifier_version="x", prompt_version="y",
                        )
        finally:
            await engine.dispose()

    async def test_05_parent_mismatch_raises(self):
        engine, factory = await _make_db(with_test_catalog=True)
        try:
            bad_parent = DeterministicInitialBinding(
                taxonomy_version=TEST_TAXO, canonical_code=TEST_CONTENT,
                parent_code="WRONG-AREA-CODE", vocabulary=TEST_VOCAB,
            )
            with registered(bad_parent):
                async with factory() as s:
                    v = await _seed_question(s, TEST_STATEMENT, "p05")
                    with self.assertRaisesRegex(ValueError, "parent"):
                        await ClassificationProposalService(s).classify_initial_with_provider(
                            v.id, ProviderRouter(text_providers=[FakeProvider({})], embedding_providers=[]),
                            target_taxonomy_version=TEST_TAXO, target_content_code=TEST_CONTENT,
                            classifier_version="x", prompt_version="y",
                        )
        finally:
            await engine.dispose()

    async def test_06_missing_catalog_node_raises(self):
        engine, factory = await _make_db(with_test_catalog=False)  # no TEST catalog nodes
        try:
            with registered(TEST_BINDING):
                async with factory() as s:
                    v = await _seed_question(s, TEST_STATEMENT, "m06")
                    with self.assertRaisesRegex(ValueError, "node is unavailable"):
                        await ClassificationProposalService(s).classify_initial_with_provider(
                            v.id, ProviderRouter(text_providers=[FakeProvider({})], embedding_providers=[]),
                            target_taxonomy_version=TEST_TAXO, target_content_code=TEST_CONTENT,
                            classifier_version="x", prompt_version="y",
                        )
        finally:
            await engine.dispose()

    async def test_07_08_provider_cannot_change_taxonomy_or_content(self):
        engine, factory = await _make_db(with_test_catalog=True)
        try:
            with registered(TEST_BINDING):
                async with factory() as s:
                    v_bad = await _seed_question(s, TEST_STATEMENT, "o07", commit=True)
                    kin_cand = await _kin_candidate(s)
                    # provider answers with the KINETICS path instead of the bound one
                    provider = FakeProvider(_response(kin_cand, KIN_EVIDENCE))
                    with self.assertRaises(ValueError):  # fail closed: provider substituted a non-bound path
                        await ClassificationProposalService(s).classify_initial_with_provider(
                            v_bad.id, ProviderRouter(text_providers=[provider], embedding_providers=[]),
                            target_taxonomy_version=TEST_TAXO, target_content_code=TEST_CONTENT,
                            classifier_version="x", prompt_version="y",
                        )
                async with factory() as s:  # nothing persisted for that question
                    self.assertEqual(
                        (await s.scalars(select(PedagogicalClassification).where(
                            PedagogicalClassification.question_version_id == v_bad.id))).all(),
                        [],
                    )
                # when the provider DOES echo the bound candidate, the persisted
                # taxonomy_version / content are the target's, never the provider's
                async with factory() as s:
                    v_ok = await _seed_question(s, TEST_STATEMENT, "o08")
                    cand = await _test_candidate(s)
                    rec = await ClassificationProposalService(s).classify_initial_with_provider(
                        v_ok.id, ProviderRouter(text_providers=[FakeProvider(_response(cand, TEST_PRIMARY))], embedding_providers=[]),
                        target_taxonomy_version=TEST_TAXO, target_content_code=TEST_CONTENT,
                        classifier_version="x2", prompt_version="y",
                    )
                    self.assertEqual(rec.content, TEST_CONTENT)
                    self.assertEqual((rec.metadata_ or {})["taxonomy_version"], TEST_TAXO)
        finally:
            await engine.dispose()

    async def test_11_other_classifications_untouched(self):
        """The generic engine only writes a new row for the targeted question_version;
        a pre-existing classification on a different question is never read for change."""
        engine, factory = await _make_db(with_test_catalog=True)
        try:
            with registered(TEST_BINDING):
                async with factory() as s:
                    protected = await _seed_question(s, KIN_STATEMENT, "k11")
                    kc = await _kin_candidate(s)
                    await ClassificationProposalService(s).classify_initial_with_provider(
                        protected.id, ProviderRouter(text_providers=[FakeProvider(_response(kc, KIN_EVIDENCE))], embedding_providers=[]),
                        target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                        classifier_version="prot-v1", prompt_version="phase9t3-kinetics-v1",
                    )
                async with factory() as s:
                    before = (await s.scalars(select(PedagogicalClassification).where(
                        PedagogicalClassification.question_version_id == protected.id))).one()
                    before_key = (before.id, before.lifecycle, before.content, before.model_version)

                async with factory() as s:
                    target = await _seed_question(s, TEST_STATEMENT, "z11")
                    tc = await _test_candidate(s)
                    await ClassificationProposalService(s).classify_initial_with_provider(
                        target.id, ProviderRouter(text_providers=[FakeProvider(_response(tc, TEST_PRIMARY))], embedding_providers=[]),
                        target_taxonomy_version=TEST_TAXO, target_content_code=TEST_CONTENT,
                        classifier_version="tgt-v1", prompt_version="y",
                    )
                async with factory() as s:
                    after = (await s.scalars(select(PedagogicalClassification).where(
                        PedagogicalClassification.question_version_id == protected.id))).one()
                    self.assertEqual((after.id, after.lifecycle, after.content, after.model_version), before_key)
                    total = len((await s.scalars(select(PedagogicalClassification))).all())
                    self.assertEqual(total, 2)  # exactly the two we created
        finally:
            await engine.dispose()

    async def test_17_two_questions_share_the_same_binding(self):
        engine, factory = await _make_db(with_test_catalog=True)
        try:
            with registered(TEST_BINDING):
                ids = []
                for h in ("aa", "bb"):
                    async with factory() as s:
                        v = await _seed_question(s, TEST_STATEMENT, h)
                        c = await _test_candidate(s)
                        rec = await ClassificationProposalService(s).classify_initial_with_provider(
                            v.id, ProviderRouter(text_providers=[FakeProvider(_response(c, TEST_PRIMARY))], embedding_providers=[]),
                            target_taxonomy_version=TEST_TAXO, target_content_code=TEST_CONTENT,
                            classifier_version="share-v1", prompt_version="y",
                        )
                        ids.append(rec.id)
                        self.assertEqual(rec.lifecycle, "ACTIVE")
                        self.assertEqual(rec.content, TEST_CONTENT)
                self.assertNotEqual(ids[0], ids[1])
                async with factory() as s:
                    active = [r for r in (await s.scalars(select(PedagogicalClassification))).all() if r.lifecycle == "ACTIVE"]
                    self.assertEqual(len(active), 2)
        finally:
            await engine.dispose()


class Batch0StillProtectsTests(unittest.TestCase):
    def test_batch0_decision_and_write_set_unchanged_for_kinetics(self):
        import phase9u2_batch0 as b0

        # protected numbers still short-circuit
        self.assertEqual(
            b0.decide_question_state(
                b0.QuestionContext(93, b0.QVView("x", ""), (), frozenset(), True, None)
            ).state,
            "PROTECTED",
        )
        self.assertEqual(
            b0.decide_question_state(
                b0.QuestionContext(128, b0.QVView("x", ""), (), frozenset(), True, None)
            ).state,
            "PROTECTED",
        )
        # 18: a question with no binding-backed candidate -> OUT_OF_SCOPE, never READY
        d = b0.decide_question_state(
            b0.QuestionContext(
                500, b0.QVView("qv", "sem termos de vocabulário registrado"), (),
                frozenset({b0.CANONICAL_CONTENT_CODE}), True,
                b0.BindingProbe(recovered_has_canonical=False, binding_status=None,
                                bound_candidate=None, literal_evidence_term=None),
            )
        )
        self.assertEqual((d.state, d.reason_code), ("OUT_OF_SCOPE", "STATEMENT_DOES_NOT_MATCH_CONTROLLED_VOCABULARY"))
        self.assertIsNone(d.planned_write)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
