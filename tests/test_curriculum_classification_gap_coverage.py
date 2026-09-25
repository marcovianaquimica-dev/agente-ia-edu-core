"""Targeted coverage for curriculum_classification.py branches left untested by
tests/test_curriculum_classification_proposals.py and the other services that
exercise this module indirectly (authorial_question_classification_service,
classification_consensus, ai_classification_service, question_bank tests).

Two things this file specifically hunts for, per the campaign's recurring bug
shape: every existing test suite that touches this service (including the
sibling file above) builds its session factory with expire_on_commit=False,
which never matches production (agente_ia_edu.db.session.create_session_factory
has no override, so prod defaults to expire_on_commit=True). Anything that
reads an ORM attribute off an object loaded earlier in the SAME session after
an internal `await session.commit()`/`rollback()` elsewhere in that session can
silently misbehave in prod while looking fine under every existing test. All
new tests in this file therefore use expire_on_commit=True.
"""

import json
import unittest
from uuid import uuid4

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, PedagogicalClassification, Question, QuestionVersion
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult
from agente_ia_edu.services.curriculum_classification import (
    KINETICS_RETRIEVAL_VOCABULARY,
    KINETICS_TAXONOMY_VERSION,
    ClassificationProposal,
    ClassificationProposalService,
    DeterministicInitialBinding,
    RetrievalVocabularyEntry,
)
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService


class GapFakeProvider:
    provider = "gap-fake"

    def __init__(self, response=None):
        self.requests = []
        self.response = response or {
            "selected_candidate_rank": 1,
            "discipline_code": "CHEMISTRY", "area_code": "CHEMISTRY-PHYSICAL",
            "content_code": "CHEMISTRY-SOLUTIONS", "subcontent_code": "CHEMISTRY-SOLUTIONS-DILUTION",
            "confidence": "HIGH", "evidence": [{"text": "Diluição", "reason": "Exige concentracao de solucoes."}],
            "candidate_classifications": [{"discipline_code": "CHEMISTRY", "area_code": "CHEMISTRY-PHYSICAL", "content_code": "CHEMISTRY-SOLUTIONS", "subcontent_code": "CHEMISTRY-SOLUTIONS-DILUTION", "rank": 1, "rationale": "Caminho completo do catálogo."}],
            "complementary_contents": [], "catalog_gap": False,
            "gap_type": None, "taxonomy_coverage_evidence": [], "review_reason": None,
            "visual_dependency": False, "status": "PROPOSED",
        }

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        self.requests.append(request)
        return TextGenerationResult(text=json.dumps(self.response), provider=self.provider, model="fake-model")


class NonDictProvider:
    provider = "non-dict"

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        return TextGenerationResult(text="[]", provider=self.provider, model="fake-model")


class CurriculumClassificationGapCoverageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        # Production default (db/session.py create_session_factory has no
        # override) - deliberately NOT expire_on_commit=False like the sibling
        # test file, so any post-commit/rollback attribute read that would
        # blow up in prod blows up here too.
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _fixture(self, session, statement="Diluição de soluções exige concentração."):
        nodes = await CurriculumTaxonomyService(session).seed_reference_fixture()
        question = Question(validation_status="validated", origin_type="IMPORTED", status="DRAFT", visibility_scope="PRIVATE")
        session.add(question)
        await session.flush()
        version = QuestionVersion(question_id=question.id, version_kind="official_original", canonical_text=statement, content_hash=f"gap-{uuid4()}")
        session.add(version)
        await session.commit()
        await session.refresh(version)
        return nodes, version

    # ---- DeterministicInitialBinding.__post_init__ (line ~150) ----

    def test_deterministic_initial_binding_requires_taxonomy_version(self):
        with self.assertRaisesRegex(ValueError, "requires taxonomy_version, canonical_code and parent_code"):
            DeterministicInitialBinding(
                taxonomy_version="",
                canonical_code=KINETICS_RETRIEVAL_VOCABULARY.canonical_code,
                parent_code="CHEMISTRY-PHYSICAL",
                vocabulary=KINETICS_RETRIEVAL_VOCABULARY,
            )

    # ---- propose_with_provider (lines ~254, ~291) ----

    async def test_propose_with_provider_rejects_missing_question_version(self):
        async with self.factory() as session:
            with self.assertRaisesRegex(ValueError, "Question version must contain text"):
                await ClassificationProposalService(session).propose_with_provider(
                    uuid4(), GapFakeProvider(), classifier_version="v1", taxonomy_version="reference-v1", prompt_version="p1",
                )

    async def test_propose_with_provider_rejects_non_object_provider_response(self):
        async with self.factory() as session:
            _, version = await self._fixture(session)
            with self.assertRaisesRegex(ValueError, "Provider response must be an object"):
                await ClassificationProposalService(session).propose_with_provider(
                    version.id, NonDictProvider(), classifier_version="v1", taxonomy_version="reference-v1", prompt_version="p1",
                )

    # ---- _validate_target_taxonomy ambiguous taxonomy (line ~433) ----

    async def test_validate_target_taxonomy_rejects_ambiguous_taxonomy_without_content_code(self):
        async with self.factory() as session:
            with self.assertRaisesRegex(ValueError, "multiple bindings; a target content code is required"):
                await ClassificationProposalService(session)._validate_target_taxonomy("curriculum-v2")

    # ---- supersede_initial_classification: real end-to-end MissingGreenlet hypothesis ----

    async def _seed_kinetics_binding(self, session):
        await CurriculumTaxonomyService(session).seed_reference_fixture()
        # seed_reference_fixture() commits internally; under expire_on_commit=True
        # (prod's default, used throughout this file) the returned nodes are
        # expired the instant it returns, so re-fetch the parent id through an
        # awaited query instead of touching a bare attribute on that dict.
        chemistry_physical_id = await session.scalar(
            select(CatalogNode.id).where(CatalogNode.code == "CHEMISTRY-PHYSICAL")
        )
        await CurriculumTaxonomyService(session).create_node(
            "Cinetica quimica", "CONTENT", KINETICS_RETRIEVAL_VOCABULARY.canonical_code,
            chemistry_physical_id, 2,
        )
        await session.commit()

    @staticmethod
    def _kinetics_response():
        return {
            "selected_candidate_rank": 1,
            "discipline_code": "CHEMISTRY", "area_code": "CHEMISTRY-PHYSICAL",
            "content_code": "CHEMISTRY-PHYSICAL-KINETICS", "subcontent_code": None,
            "confidence": "HIGH",
            "evidence": [{"text": "cinética química", "reason": "Termo cinético citado explicitamente."}],
            "candidate_classifications": [{
                "discipline_code": "CHEMISTRY", "area_code": "CHEMISTRY-PHYSICAL",
                "content_code": "CHEMISTRY-PHYSICAL-KINETICS", "subcontent_code": None,
                "rank": 1, "rationale": "Vocabulario controlado de cinetica.",
            }],
            "complementary_contents": [], "catalog_gap": False, "gap_type": None,
            "taxonomy_coverage_evidence": [], "review_reason": None,
            "visual_dependency": False, "status": "PROPOSED",
        }

    async def test_supersede_initial_classification_success_does_not_crash_on_expired_old_row(self):
        # Real hypothesis: `old` is loaded via session.get() at the top of
        # supersede_initial_classification, its .lifecycle is mutated
        # in-memory, and then classify_initial_with_provider() runs the
        # ordinary propose_with_provider() pipeline for the NEW row, which
        # calls `await self.session.commit()` internally
        # (_persist_provider_output). Under prod's expire_on_commit=True that
        # commit expires EVERY object tracked by the session - including
        # `old`, which is never refreshed before the very next line reads
        # `old.id` (`if new.id == old.id:`). Reading an expired attribute
        # outside of an awaited SQLAlchemy call needs the async-greenlet
        # bridge; a bare attribute read does not provide it, so this should
        # raise MissingGreenlet in prod's session config if the hypothesis is
        # correct.
        async with self.factory() as session:
            await self._seed_kinetics_binding(session)
            _, version = await self._fixture(session, statement=(
                "O estudo de cinética química investiga a velocidade da reação "
                "e o efeito de catalisador na velocidade da reação."
            ))
            version_id = version.id
            service = ClassificationProposalService(session)
            old = await service.classify_initial_with_provider(
                version_id, GapFakeProvider(self._kinetics_response()),
                target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                classifier_version="kinetics-v1", prompt_version="kinetics-p1",
            )
            old_id = old.id
            new = await service.supersede_initial_classification(
                superseded_id=old_id,
                question_version_id=version_id,
                provider=GapFakeProvider(self._kinetics_response()),
                target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                classifier_version="kinetics-v2",
                prompt_version="kinetics-p2",
            )
            self.assertNotEqual(new.id, old_id)
            self.assertEqual(new.supersedes_id, old_id)
            reloaded_old = await session.get(PedagogicalClassification, old_id)
            self.assertEqual(reloaded_old.lifecycle, "SUPERSEDED")
            self.assertEqual(new.lifecycle, "ACTIVE")

    async def test_supersede_initial_classification_rejects_missing_superseded_row(self):
        async with self.factory() as session:
            await self._seed_kinetics_binding(session)
            _, version = await self._fixture(session)
            version_id = version.id
            with self.assertRaisesRegex(ValueError, "Superseded classification was not found"):
                await ClassificationProposalService(session).supersede_initial_classification(
                    superseded_id=uuid4(),
                    question_version_id=version_id,
                    provider=GapFakeProvider(self._kinetics_response()),
                    target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                    classifier_version="kinetics-v3", prompt_version="kinetics-p3",
                )

    async def test_supersede_initial_classification_rejects_taxonomy_version_mismatch(self):
        async with self.factory() as session:
            await self._seed_kinetics_binding(session)
            _, version = await self._fixture(session, statement=(
                "O estudo de cinética química investiga a velocidade da reação "
                "e o efeito de catalisador na velocidade da reação."
            ))
            version_id = version.id
            service = ClassificationProposalService(session)
            old = await service.classify_initial_with_provider(
                version_id, GapFakeProvider(self._kinetics_response()),
                target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                classifier_version="kinetics-v4", prompt_version="kinetics-p4",
            )
            old_id = old.id
            with self.assertRaisesRegex(ValueError, "must target the same taxonomy_version"):
                await service.supersede_initial_classification(
                    superseded_id=old_id,
                    question_version_id=version_id,
                    provider=GapFakeProvider(self._kinetics_response()),
                    target_taxonomy_version="some-other-taxonomy-version",
                    classifier_version="kinetics-v5", prompt_version="kinetics-p5",
                )

    async def test_supersede_initial_classification_rejects_resolving_to_the_same_row(self):
        # Same classifier_version/prompt_version as the original -> propose_with_provider's
        # idempotency check (same input_hash) returns the EXACT same row back
        # as "new", so new.id == old.id must be rejected instead of silently
        # "succeeding" as a no-op supersession.
        async with self.factory() as session:
            await self._seed_kinetics_binding(session)
            _, version = await self._fixture(session, statement=(
                "O estudo de cinética química investiga a velocidade da reação "
                "e o efeito de catalisador na velocidade da reação."
            ))
            version_id = version.id
            service = ClassificationProposalService(session)
            old = await service.classify_initial_with_provider(
                version_id, GapFakeProvider(self._kinetics_response()),
                target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                classifier_version="kinetics-v6", prompt_version="kinetics-p6",
            )
            old_id = old.id
            with self.assertRaisesRegex(ValueError, "resolved to the same row being superseded"):
                await service.supersede_initial_classification(
                    superseded_id=old_id,
                    question_version_id=version_id,
                    provider=GapFakeProvider(self._kinetics_response()),
                    target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                    classifier_version="kinetics-v6", prompt_version="kinetics-p6",  # identical -> idempotent match
                )
            # The lifecycle mutation made before the failed attempt must be
            # rolled back, not left dangling as SUPERSEDED with no successor.
            reloaded = await session.get(PedagogicalClassification, old_id)
            self.assertEqual(reloaded.lifecycle, "ACTIVE")

    async def test_supersede_initial_classification_rolls_back_lifecycle_flip_on_failure(self):
        # classify_initial_with_provider() fails deep inside the nested
        # propose_with_provider() call (invalid provider contract) BEFORE any
        # commit happens there. supersede_initial_classification must roll
        # back its own uncommitted `old.lifecycle = "SUPERSEDED"` mutation
        # rather than leaving a half-applied lifecycle change hanging off the
        # session, and must propagate the original error.
        async with self.factory() as session:
            await self._seed_kinetics_binding(session)
            _, version = await self._fixture(session, statement=(
                "O estudo de cinética química investiga a velocidade da reação "
                "e o efeito de catalisador na velocidade da reação."
            ))
            version_id = version.id
            service = ClassificationProposalService(session)
            old = await service.classify_initial_with_provider(
                version_id, GapFakeProvider(self._kinetics_response()),
                target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                classifier_version="kinetics-v7", prompt_version="kinetics-p7",
            )
            old_id = old.id
            broken_response = {"discipline_code": "CHEMISTRY"}  # missing required fields
            with self.assertRaisesRegex(ValueError, "missing required classification fields"):
                await service.supersede_initial_classification(
                    superseded_id=old_id,
                    question_version_id=version_id,
                    provider=GapFakeProvider(broken_response),
                    target_taxonomy_version=KINETICS_TAXONOMY_VERSION,
                    classifier_version="kinetics-v8", prompt_version="kinetics-p8",
                )
            reloaded = await session.get(PedagogicalClassification, old_id)
            self.assertEqual(reloaded.lifecycle, "ACTIVE")

    # ---- diagnose_coverage branches (lines ~728, ~732) ----

    def test_diagnose_coverage_reports_no_candidate_for_area_only_match(self):
        discipline = CatalogNode(id=uuid4(), code="GAP-DISC", name="Disciplina Gap", node_type="DISCIPLINE", parent_id=None, description=None, active=True, position=1)
        area = CatalogNode(id=uuid4(), code="GAP-AREA", name="Astro", node_type="AREA", parent_id=discipline.id, description=None, active=True, position=1)
        catalog = [discipline, area]
        # diagnose_coverage/retrieve_candidate_classifications are plain instance
        # methods that never touch self.session - a lightweight service bound to
        # any session works, but we don't even need one here.
        service = ClassificationProposalService.__new__(ClassificationProposalService)
        diagnosis = service.diagnose_coverage("astro", catalog)
        self.assertEqual(diagnosis.diagnosis, "NO_CANDIDATE")
        self.assertGreater(diagnosis.retrieved_candidate_count, 0)

    def test_diagnose_coverage_reports_retrieval_limitation_for_morphological_near_miss(self):
        node = CatalogNode(id=uuid4(), code="GAP-GAS", name="Gases", node_type="DISCIPLINE", parent_id=None, description=None, active=True, position=1)
        service = ClassificationProposalService.__new__(ClassificationProposalService)
        diagnosis = service.diagnose_coverage("gas", [node])
        self.assertEqual(diagnosis.diagnosis, "RETRIEVAL_LIMITATION")
        self.assertTrue(diagnosis.retrieval_limitation)
        self.assertEqual(diagnosis.limitation_type, "MORPHOLOGY")
        self.assertTrue(diagnosis.evidence)

    # ---- match_retrieval_vocabulary / _validate_retrieval_vocabulary ----

    def test_match_retrieval_vocabulary_disabled_returns_none(self):
        disabled = RetrievalVocabularyEntry(
            canonical_code="TEST-DISABLED",
            primary_terms=("termo primario",),
            specific_terms=(), contextual_expressions=(), generic_terms=(),
            version="test-v1", enabled=False,
        )
        self.assertIsNone(ClassificationProposalService.match_retrieval_vocabulary("termo primario", disabled))

    def test_match_retrieval_vocabulary_rejects_blank_terms(self):
        invalid = RetrievalVocabularyEntry(
            canonical_code="TEST-INVALID",
            primary_terms=("",),
            specific_terms=(), contextual_expressions=(), generic_terms=(),
            version="test-v2",
        )
        with self.assertRaisesRegex(ValueError, "non-empty tuples of strings"):
            ClassificationProposalService.match_retrieval_vocabulary("qualquer texto", invalid)

    # ---- _persist_provider_output validation branches ----

    async def test_rejects_too_many_complementary_contents(self):
        async with self.factory() as session:
            _, version = await self._fixture(session)
            response = GapFakeProvider().response
            response["complementary_contents"] = ["MATH-ALGEBRA-RATIO", "PHYSICS-MECHANICS-KINEMATICS-UNIFORM", "BIOLOGY-CYTOLOGY-BIOCHEMISTRY-PROTEINS", "CHEMISTRY-SOLUTIONS-CONCENTRATION"]
            with self.assertRaisesRegex(ValueError, "Invalid complementary contents"):
                await ClassificationProposalService(session).propose_with_provider(
                    version.id, GapFakeProvider(response), classifier_version="gap-v1", taxonomy_version="reference-v1", prompt_version="gap-p1",
                )

    async def _two_candidate_fixture(self, session):
        _, version = await self._fixture(session, statement="Diluição e concentração de soluções.")
        return version

    async def test_rejects_valid_but_unrecovered_candidate_alongside_a_recovered_primary(self):
        async with self.factory() as session:
            version = await self._two_candidate_fixture(session)
            response = GapFakeProvider().response
            response["candidate_classifications"].append({
                "discipline_code": "MATH", "area_code": "MATH-ALGEBRA",
                "content_code": "MATH-ALGEBRA-FUNCTIONS", "subcontent_code": "MATH-ALGEBRA-RATIO",
                "rank": 9, "rationale": "Caminho valido do catalogo mas nunca recuperado para este enunciado.",
            })
            with self.assertRaisesRegex(ValueError, "Curriculum candidate was not recovered"):
                await ClassificationProposalService(session).propose_with_provider(
                    version.id, GapFakeProvider(response), classifier_version="gap-v2", taxonomy_version="reference-v1", prompt_version="gap-p2",
                )

    async def test_rejects_candidate_rank_not_matching_its_recovered_rank(self):
        async with self.factory() as session:
            version = await self._two_candidate_fixture(session)
            response = GapFakeProvider().response
            response["candidate_classifications"].append({
                "discipline_code": "CHEMISTRY", "area_code": "CHEMISTRY-PHYSICAL",
                "content_code": "CHEMISTRY-SOLUTIONS", "subcontent_code": "CHEMISTRY-SOLUTIONS-CONCENTRATION",
                "rank": 5, "rationale": "Mesmo caminho recuperado, rank incorreto.",
            })
            with self.assertRaisesRegex(ValueError, "Candidate rank does not match recovered candidate"):
                await ClassificationProposalService(session).propose_with_provider(
                    version.id, GapFakeProvider(response), classifier_version="gap-v3", taxonomy_version="reference-v1", prompt_version="gap-p3",
                )

    async def test_rejects_selected_candidate_absent_from_candidate_list(self):
        async with self.factory() as session:
            version = await self._two_candidate_fixture(session)
            response = GapFakeProvider().response
            # selected_candidate_rank=1 (DILUTION, the default primary path),
            # but candidate_classifications only lists the OTHER recovered
            # candidate (CONCENTRATION, rank 2) - the selection is valid on
            # its own, but absent from the disclosed candidate list.
            response["candidate_classifications"] = [{
                "discipline_code": "CHEMISTRY", "area_code": "CHEMISTRY-PHYSICAL",
                "content_code": "CHEMISTRY-SOLUTIONS", "subcontent_code": "CHEMISTRY-SOLUTIONS-CONCENTRATION",
                "rank": 2, "rationale": "Segundo candidato recuperado.",
            }]
            with self.assertRaisesRegex(ValueError, "Selected candidate must be present in candidate classifications"):
                await ClassificationProposalService(session).propose_with_provider(
                    version.id, GapFakeProvider(response), classifier_version="gap-v4", taxonomy_version="reference-v1", prompt_version="gap-p4",
                )

    async def test_rejects_unknown_review_reason(self):
        async with self.factory() as session:
            _, version = await self._fixture(session)
            response = GapFakeProvider().response
            response["review_reason"] = "NOT_A_REAL_REASON"
            with self.assertRaisesRegex(ValueError, "Invalid review reason"):
                await ClassificationProposalService(session).propose_with_provider(
                    version.id, GapFakeProvider(response), classifier_version="gap-v5", taxonomy_version="reference-v1", prompt_version="gap-p5",
                )

    async def test_rejects_missing_subcontent_gap_with_a_full_path(self):
        async with self.factory() as session:
            _, version = await self._fixture(session)
            response = GapFakeProvider().response
            response.update({
                "catalog_gap": True,
                "gap_type": "MISSING_SUBCONTENT",
                "review_reason": "TAXONOMY_GRANULARITY_GAP",
                "status": "NEEDS_REVIEW",
                "taxonomy_coverage_evidence": ["Catalogo termina antes do nivel necessario."],
                # subcontent_code stays populated (CHEMISTRY-SOLUTIONS-DILUTION from the default
                # response) - MISSING_SUBCONTENT requires it to be null.
            })
            with self.assertRaisesRegex(ValueError, "MISSING_SUBCONTENT requires a partial content path"):
                await ClassificationProposalService(session).propose_with_provider(
                    version.id, GapFakeProvider(response), classifier_version="gap-v6", taxonomy_version="reference-v1", prompt_version="gap-p6",
                )

    async def test_rejects_unsupported_proposal_status(self):
        async with self.factory() as session:
            _, version = await self._fixture(session)
            response = GapFakeProvider().response
            response["status"] = "BOGUS_STATUS"
            with self.assertRaisesRegex(ValueError, "Invalid proposal status"):
                await ClassificationProposalService(session).propose_with_provider(
                    version.id, GapFakeProvider(response), classifier_version="gap-v7", taxonomy_version="reference-v1", prompt_version="gap-p7",
                )

    # ---- _valid_path gap-in-hierarchy branch ----

    async def test_valid_path_rejects_a_gap_in_the_hierarchy(self):
        async with self.factory() as session:
            await CurriculumTaxonomyService(session).seed_reference_fixture()
            # seed_reference_fixture() commits internally; under expire_on_commit=True
            # re-fetch through an awaited query rather than touching the (now expired)
            # returned dict's attributes directly.
            catalog = list((await session.scalars(select(CatalogNode))).all())
            code_map = {node.code: node for node in catalog}
            gapped_path = {
                "discipline_code": "CHEMISTRY", "area_code": None,
                "content_code": "CHEMISTRY-SOLUTIONS", "subcontent_code": None,
            }
            self.assertFalse(ClassificationProposalService._valid_path(gapped_path, code_map))

    # ---- _valid_candidates defensive branches ----

    async def test_valid_candidates_returns_empty_for_non_list_input(self):
        async with self.factory() as session:
            service = ClassificationProposalService(session)
            self.assertEqual(service._valid_candidates("not-a-list", {}), [])

    async def test_rejects_candidate_with_unique_rank_but_blank_rationale(self):
        async with self.factory() as session:
            version = await self._two_candidate_fixture(session)
            response = GapFakeProvider().response
            response["candidate_classifications"].append({
                "discipline_code": "CHEMISTRY", "area_code": "CHEMISTRY-PHYSICAL",
                "content_code": "CHEMISTRY-SOLUTIONS", "subcontent_code": "CHEMISTRY-SOLUTIONS-CONCENTRATION",
                "rank": 2, "rationale": "   ",  # blank after strip, unique rank/path
            })
            with self.assertRaisesRegex(ValueError, "Invalid candidate classifications"):
                await ClassificationProposalService(session).propose_with_provider(
                    version.id, GapFakeProvider(response), classifier_version="gap-v8", taxonomy_version="reference-v1", prompt_version="gap-p8",
                )

    # ---- validate_candidate_selection branches ----

    async def test_validate_candidate_selection_rejects_primary_codes_with_no_recovered_candidates(self):
        async with self.factory() as session:
            service = ClassificationProposalService(session)
            output = {
                "selected_candidate_rank": None,
                "discipline_code": "CHEMISTRY", "area_code": None, "content_code": None, "subcontent_code": None,
            }
            with self.assertRaisesRegex(ValueError, "Primary curriculum codes must be null when no candidates were recovered"):
                service.validate_candidate_selection(output, [], "hash")

    async def test_rejects_null_rank_without_catalog_gap_when_candidates_were_recovered(self):
        async with self.factory() as session:
            _, version = await self._fixture(session)
            response = GapFakeProvider().response
            response.update({
                "selected_candidate_rank": None,
                "discipline_code": None, "area_code": None, "content_code": None, "subcontent_code": None,
                "candidate_classifications": [],
                # catalog_gap left False (default) even though recovery found candidates -
                # a bare omission is not a declared gap.
            })
            with self.assertRaisesRegex(ValueError, "Selected candidate rank is invalid"):
                await ClassificationProposalService(session).propose_with_provider(
                    version.id, GapFakeProvider(response), classifier_version="gap-v9", taxonomy_version="reference-v1", prompt_version="gap-p9",
                )

    async def test_rejects_boolean_selected_candidate_rank(self):
        # JSON `true`/`false` decode to Python bool, and bool is a subclass of
        # int - isinstance(True, int) is True. A naive `isinstance(rank, int)`
        # check would silently accept a provider that returns a boolean where
        # an integer rank belongs.
        async with self.factory() as session:
            _, version = await self._fixture(session)
            response = GapFakeProvider().response
            response["selected_candidate_rank"] = True
            with self.assertRaisesRegex(ValueError, "Selected candidate rank is invalid"):
                await ClassificationProposalService(session).propose_with_provider(
                    version.id, GapFakeProvider(response), classifier_version="gap-v10", taxonomy_version="reference-v1", prompt_version="gap-p10",
                )

    async def test_rejects_selected_rank_that_was_never_recovered(self):
        async with self.factory() as session:
            _, version = await self._fixture(session)
            response = GapFakeProvider().response
            response["selected_candidate_rank"] = 99
            with self.assertRaisesRegex(ValueError, "Selected candidate rank was not recovered"):
                await ClassificationProposalService(session).propose_with_provider(
                    version.id, GapFakeProvider(response), classifier_version="gap-v11", taxonomy_version="reference-v1", prompt_version="gap-p11",
                )

    # ---- propose() validation branches ----

    async def test_propose_rejects_missing_question_version(self):
        async with self.factory() as session:
            proposal = ClassificationProposal(
                primary_content_code="CHEMISTRY-SOLUTIONS-DILUTION", complementary_content_codes=[], concepts=[],
                prerequisites=[], cognitive_operations=[], context="", difficulty="UNKNOWN", confidence=0.9, evidence=[],
            )
            with self.assertRaisesRegex(ValueError, "Question version must contain text"):
                await ClassificationProposalService(session).propose(
                    uuid4(), proposal, classifier_version="v1", taxonomy_version="reference-v1", provider="rule", model="deterministic", prompt_version="p1",
                )

    async def test_propose_rejects_unsupported_difficulty(self):
        async with self.factory() as session:
            _, version = await self._fixture(session)
            proposal = ClassificationProposal(
                primary_content_code="CHEMISTRY-SOLUTIONS-DILUTION", complementary_content_codes=[], concepts=[],
                prerequisites=[], cognitive_operations=[], context="", difficulty="IMPOSSIBLE", confidence=0.9, evidence=[],
            )
            with self.assertRaisesRegex(ValueError, "Unsupported proposed difficulty"):
                await ClassificationProposalService(session).propose(
                    version.id, proposal, classifier_version="v1", taxonomy_version="reference-v1", provider="rule", model="deterministic", prompt_version="p1",
                )

    async def test_propose_rejects_out_of_range_confidence(self):
        async with self.factory() as session:
            _, version = await self._fixture(session)
            proposal = ClassificationProposal(
                primary_content_code="CHEMISTRY-SOLUTIONS-DILUTION", complementary_content_codes=[], concepts=[],
                prerequisites=[], cognitive_operations=[], context="", difficulty="UNKNOWN", confidence=1.5, evidence=[],
            )
            with self.assertRaisesRegex(ValueError, "Confidence must be between zero and one"):
                await ClassificationProposalService(session).propose(
                    version.id, proposal, classifier_version="v1", taxonomy_version="reference-v1", provider="rule", model="deterministic", prompt_version="p1",
                )

    async def test_propose_rejects_too_many_complementary_contents(self):
        async with self.factory() as session:
            _, version = await self._fixture(session)
            proposal = ClassificationProposal(
                primary_content_code="CHEMISTRY-SOLUTIONS-DILUTION",
                complementary_content_codes=["MATH-ALGEBRA-RATIO", "PHYSICS-MECHANICS-KINEMATICS-UNIFORM", "BIOLOGY-CYTOLOGY-BIOCHEMISTRY-PROTEINS", "CHEMISTRY-SOLUTIONS-CONCENTRATION"],
                concepts=[], prerequisites=[], cognitive_operations=[], context="", difficulty="UNKNOWN", confidence=0.9, evidence=[],
            )
            with self.assertRaisesRegex(ValueError, "Too many complementary contents"):
                await ClassificationProposalService(session).propose(
                    version.id, proposal, classifier_version="v1", taxonomy_version="reference-v1", provider="rule", model="deterministic", prompt_version="p1",
                )


if __name__ == "__main__":
    unittest.main()
