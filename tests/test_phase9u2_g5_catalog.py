"""PHASE 9U.2-G5 — the curriculum-v2 catalog + bindings.

Isolated SQLite. Proves that once the 11 new AREAs + 19 new CONTENTs exist in the
catalog, every registered ``curriculum-v2`` deterministic INITIAL binding resolves
and classifies through the unchanged pipeline; that N05 stays deferred; and that a
binding whose node is absent still fails closed. No PostgreSQL, no OpenAI, no
provider, no production data, no classification of the real 21 questions.
"""

from __future__ import annotations

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

from agente_ia_edu.db.base import Base  # noqa: E402
from agente_ia_edu.db.models import CatalogNode, PedagogicalClassification, Question, QuestionVersion  # noqa: E402
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult  # noqa: E402
from agente_ia_edu.providers.router import ProviderRouter  # noqa: E402
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService  # noqa: E402
import agente_ia_edu.services.curriculum_classification as cc  # noqa: E402
from agente_ia_edu.services.curriculum_classification import (  # noqa: E402
    ClassificationProposalService,
    KINETICS_TAXONOMY_VERSION,
    resolve_registered_initial_binding,
)
from agente_ia_edu.services._curriculum_v2_bindings import (  # noqa: E402
    DEFERRED_CONTENTS,
    NEW_AREAS,
    NEW_CONTENTS,
    TAXONOMY_VERSION,
    build_curriculum_v2_bindings,
)

MIG = ROOT / "migrations" / "versions"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod


migration_024 = _load(MIG / "024_chemistry_kinetics.py", "g5_mig024")
migration_025 = _load(MIG / "025_pedagogical_classification_lifecycle.py", "g5_mig025")

KIN_STATEMENT = "Nanomateriais catalíticos aumentam a velocidade da reação química."
KIN_EVIDENCE = "velocidade da reação"
KIN_CODE = "CHEMISTRY-PHYSICAL-KINETICS"

_BINDINGS = {b.canonical_code: b for b in build_curriculum_v2_bindings(cc.RetrievalVocabularyEntry, cc.DeterministicInitialBinding)}


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


class FakeProvider:
    provider = "fake"

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        return TextGenerationResult(json.dumps(self._response), self.provider, "fake-model")

    def __init__(self, response):
        self._response = response


def _response(candidate, evidence):
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
        "taxonomy_coverage_evidence": ["Determinístico."],
        "review_reason": None,
        "visual_dependency": False,
        "status": "PROPOSED",
    }


async def _make_db(*, with_v2_catalog: bool):
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
                node = await svc.create_node(name, "AREA", code, by_code[parent_code].id, pos)
                by_code[code] = node
                pos += 1
            for code, name, parent_code in NEW_CONTENTS:
                node = await svc.create_node(name, "CONTENT", code, by_code[parent_code].id, 1)
                by_code[code] = node
            await s.commit()
    return engine, factory


async def _seed_question(session, statement, salt=""):
    q = Question(validation_status="validated", origin_type="IMPORTED", status="DRAFT", visibility_scope="PRIVATE")
    session.add(q)
    await session.flush()
    v = QuestionVersion(
        question_id=q.id, version_kind="official_original", canonical_text=statement,
        content_hash=hashlib.sha256(f"{statement}:{salt}:{uuid.uuid4()}".encode()).hexdigest(),
    )
    session.add(v)
    await session.flush()
    return v


async def _catalog(session):
    return list((await session.scalars(select(CatalogNode).where(CatalogNode.active.is_(True)))).all())


class DataConsistencyTests(unittest.TestCase):
    def test_counts_and_deferral(self):
        self.assertEqual(len(NEW_AREAS), 11)
        self.assertEqual(len(NEW_CONTENTS), 19)
        self.assertEqual(DEFERRED_CONTENTS, ("CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS",))
        # Snapshot updated 19 -> 25 in PHASE 11.5-A2: six robust, false-positive-swept
        # curriculum-v2 vocabulary bindings were added for PHASE 11.4 CONTENT nodes
        # (MATH-PROBABILITY-BASICS, MATH-GEOMETRY-SPATIAL, MATH-ALGEBRA-PERCENTAGE,
        # CHEMISTRY-PHYSICAL-STOICHIOMETRY, CHEMISTRY-ORGANIC-REACTIONS,
        # BIOLOGY-IMMUNOLOGY-MICROBIOLOGY-DISEASES). The original 19 are unchanged.
        # Snapshot updated 25 -> 26 in PHASE 11.10: one binding added for the
        # pre-existing PHYSICS-MECHANICS-KINEMATICS node (two other PHASE 11.10
        # bindings only extended existing entries and did not change the count).
        # Snapshot updated 26 -> 29 in PHASE 11.14: three bindings added for the
        # pre-existing MATH-ALGEBRA-LOGARITHMS, BIOLOGY-EVOLUTION-MECHANISMS and
        # BIOLOGY-CYTOLOGY-ORGANELLES content nodes.
        # All false-positive swept over all 332 statements (0 losses, targets only).
        self.assertEqual(len(_BINDINGS), 29)
        self.assertNotIn("CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS", _BINDINGS)

    def test_every_content_parent_is_known(self):
        area_codes = {c for c, _n, _p in NEW_AREAS}
        existing = {
            "CHEMISTRY", "CHEMISTRY-PHYSICAL", "PHYSICS", "PHYSICS-MECHANICS",
            "BIOLOGY", "BIOLOGY-CYTOLOGY", "MATH", "MATH-ALGEBRA",
        }
        for code, _name, parent in NEW_CONTENTS:
            self.assertIn(parent, area_codes | existing, f"{code} parent {parent} unknown")

    def test_every_area_parent_is_an_existing_discipline(self):
        for code, _name, parent in NEW_AREAS:
            self.assertIn(parent, {"CHEMISTRY", "PHYSICS", "BIOLOGY", "MATH"}, code)

    def test_bindings_taxonomy_and_canonical(self):
        for code, b in _BINDINGS.items():
            self.assertEqual(b.taxonomy_version, TAXONOMY_VERSION)
            self.assertEqual(b.canonical_code, code)
            self.assertEqual(b.vocabulary.canonical_code, code)
            self.assertEqual(b.vocabulary.generic_terms, ())
            self.assertTrue(b.vocabulary.primary_terms)  # non-empty


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_kinetics_unaffected_by_curriculum_v2(self):
        engine, factory = await _make_db(with_v2_catalog=False)
        try:
            async with factory() as s:
                v = await _seed_question(s, KIN_STATEMENT, "kin")
                cands = ClassificationProposalService(s).recover_candidates(KIN_STATEMENT, await _catalog(s))
                cand = next(c for c in cands if c["content_code"] == KIN_CODE)
                rec = await ClassificationProposalService(s).classify_initial_with_provider(
                    v.id, ProviderRouter(text_providers=[FakeProvider(_response(cand, KIN_EVIDENCE))], embedding_providers=[]),
                    target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                    classifier_version="g5-kin", prompt_version="phase9t3-kinetics-v1",
                )
            self.assertEqual((rec.lifecycle, rec.content), ("ACTIVE", KIN_CODE))
            self.assertEqual((rec.metadata_ or {})["taxonomy_version"], KINETICS_TAXONOMY_VERSION)
        finally:
            await engine.dispose()

    async def test_curriculum_v2_binding_fails_closed_without_catalog_node(self):
        engine, factory = await _make_db(with_v2_catalog=False)  # no v2 nodes
        try:
            async with factory() as s:
                v = await _seed_question(s, "texto irrelevante", "nc")
                with self.assertRaises(ValueError):
                    await ClassificationProposalService(s).classify_initial_with_provider(
                        v.id, ProviderRouter(text_providers=[FakeProvider({})], embedding_providers=[]),
                        target_taxonomy_version=TAXONOMY_VERSION,
                        target_content_code="PHYSICS-WAVES-PHENOMENA",
                        classifier_version="x", prompt_version="y",
                    )
        finally:
            await engine.dispose()

    async def test_sample_curriculum_v2_contents_classify_when_catalog_present(self):
        # a representative slice across disciplines; primary term drives the bind
        samples = {
            "PHYSICS-WAVES-PHENOMENA": "A questão trata de fenômeno ondulatório e interferência destrutiva no cancelamento de ruído.",
            "CHEMISTRY-PHYSICAL-EQUILIBRIUM": "A questão aborda o equilíbrio químico e o deslocamento de equilíbrio ao aquecer o sistema.",
            "BIOLOGY-ECOLOGY-BIOGEOCHEMICAL-CYCLES": "A questão discute o ciclo do carbono e o ciclo biogeoquímico afetado pela queima de combustíveis.",
            "MATH-STATISTICS-CENTRAL-TENDENCY": "A questão exige o cálculo da média aritmética e da medida de tendência central de um conjunto.",
        }
        engine, factory = await _make_db(with_v2_catalog=True)
        try:
            for code, stmt in samples.items():
                async with factory() as s:
                    v = await _seed_question(s, stmt, code)
                    cands = ClassificationProposalService(s).recover_candidates(stmt, await _catalog(s))
                    cand = next((c for c in cands if c["content_code"] == code), None)
                    self.assertIsNotNone(cand, f"{code}: canonical candidate not recovered")
                    primary_term = _BINDINGS[code].vocabulary.primary_terms[0]
                    self.assertIn(primary_term, stmt)  # literal evidence available
                    rec = await ClassificationProposalService(s).classify_initial_with_provider(
                        v.id, ProviderRouter(text_providers=[FakeProvider(_response(cand, primary_term))], embedding_providers=[]),
                        target_taxonomy_version=TAXONOMY_VERSION, target_content_code=code,
                        classifier_version="g5-generic", prompt_version="curriculum-v2-prompt-v1",
                    )
                    md = rec.metadata_ or {}
                    self.assertEqual(rec.lifecycle, "ACTIVE")
                    self.assertEqual(rec.content, code)
                    self.assertEqual(md["taxonomy_version"], TAXONOMY_VERSION)
                    self.assertEqual(md["classification_mode"], "INITIAL")
                    self.assertIsNone(rec.supersedes_id)
                    for k in ("input_hash", "output_hash", "question_content_hash"):
                        self.assertRegex(str(md[k]), r"^[0-9a-f]{64}$")
        finally:
            await engine.dispose()

    async def test_n05_deferred_content_has_no_binding(self):
        engine, factory = await _make_db(with_v2_catalog=True)
        try:
            self.assertIsNone(resolve_registered_initial_binding(TAXONOMY_VERSION, "CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS"))
            async with factory() as s:
                v = await _seed_question(s, "óleo de rosas com cadeia poli-insaturada e hidroxila em carbono terminal", "n05")
                with self.assertRaises(ValueError):
                    await ClassificationProposalService(s).classify_initial_with_provider(
                        v.id, ProviderRouter(text_providers=[FakeProvider({})], embedding_providers=[]),
                        target_taxonomy_version=TAXONOMY_VERSION,
                        target_content_code="CHEMISTRY-ORGANIC-FUNCTIONAL-GROUPS",
                        classifier_version="x", prompt_version="y",
                    )
        finally:
            await engine.dispose()


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
