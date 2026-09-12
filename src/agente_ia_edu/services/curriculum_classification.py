"""Auditable curriculum classification proposals without automatic question mutation."""

import os
import hashlib
import json
import re
import unicodedata
from dataclasses import dataclass
from decimal import Decimal
from typing import Any
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agente_ia_edu.classification_prompts import get_classification_prompt
from agente_ia_edu.db.models import CatalogNode, PedagogicalClassification, QuestionOption, QuestionVersion
from agente_ia_edu.providers.contracts import TextGenerationProvider
from agente_ia_edu.providers.models import TextGenerationRequest


@dataclass(frozen=True)
class ClassificationProposal:
    primary_content_code: str
    complementary_content_codes: list[str]
    concepts: list[str]
    prerequisites: list[str]
    cognitive_operations: list[str]
    context: str
    difficulty: str
    confidence: float
    evidence: list[dict[str, Any]]


@dataclass(frozen=True)
class ClassificationDecision:
    selected_candidate_rank: int | None
    confidence: str
    candidate_type: str | None
    evidence: list[dict[str, Any]]
    taxonomy_coverage_evidence: list[str]
    catalog_gap: bool
    gap_type: str | None
    review_reason: str | None
    status: str


@dataclass(frozen=True)
class RetrievalDiagnosis:
    diagnosis: str
    retrieved_candidate_count: int
    retrieval_limitation: bool
    limitation_type: str | None
    evidence: list[dict[str, str]]
    reason: str


@dataclass(frozen=True)
class RetrievalVocabularyEntry:
    """Versioned lexical evidence for a future catalog node, kept outside CatalogNode."""

    canonical_code: str
    primary_terms: tuple[str, ...]
    specific_terms: tuple[str, ...]
    contextual_expressions: tuple[str, ...]
    generic_terms: tuple[str, ...]
    version: str
    enabled: bool = True


@dataclass(frozen=True)
class RetrievalVocabularyMatch:
    canonical_code: str
    matched_primary_terms: tuple[str, ...]
    matched_specific_terms: tuple[str, ...]
    matched_contextual_expressions: tuple[str, ...]
    generic_terms_ignored: tuple[str, ...]
    match_positions: tuple[int, ...]
    lexical_score: int
    primary_term_score: int
    specific_term_score: int
    phrase_match_score: int
    context_score: int
    candidate_type: str
    total_score: int


KINETICS_RETRIEVAL_VOCABULARY = RetrievalVocabularyEntry(
    canonical_code="CHEMISTRY-PHYSICAL-KINETICS",
    primary_terms=("cinética química", "estudo cinético"),
    specific_terms=(
        "velocidade da reação", "velocidade das reações", "taxa de reação",
        "fatores que influenciam a velocidade", "catalisador", "catalisadores",
        "catálise", "energia de ativação",
    ),
    contextual_expressions=(
        "estudo da velocidade da reação", "evolução da concentração ao longo do tempo",
        "efeito de catalisador na velocidade da reação", "velocidade das reações químicas",
    ),
    generic_terms=("química", "reação", "reações", "velocidade", "estudo", "fatores", "concentração"),
    version="phase9t3-kinetics-v1",
)
KINETICS_TAXONOMY_VERSION = "024_chemistry_kinetics"
KINETICS_PARENT_CODE = "CHEMISTRY-PHYSICAL"


@dataclass(frozen=True)
class ControlledVocabularyBinding:
    """A deterministic, provider-independent content binding for an INITIAL proposal.

    ``status == "BOUND"`` means the approved controlled vocabulary matched the
    statement and its canonical candidate was actually recovered, so the persisted
    content code MUST be ``canonical_code``. ``status == "NEEDS_REVIEW"`` means the
    vocabulary matched but its canonical candidate was not recovered - the proposal
    is forced to human review and no candidate is invented.
    """

    controlled_vocabulary_version: str
    canonical_code: str
    taxonomy_version: str
    status: str
    bound_candidate: dict[str, Any] | None
    matched_terms: tuple[str, ...]
    reason: str


@dataclass(frozen=True)
class DeterministicInitialBinding:
    """One approved deterministic INITIAL binding.

    Ties together a ``taxonomy_version`` (a curriculum generation tag), the
    ``canonical_code`` of the CONTENT node it targets, that node's expected AREA
    ``parent_code``, and the ``vocabulary`` that lexically binds it. Multiple
    bindings may share a ``taxonomy_version`` (a generation with many contents).

    The platform's deterministic flow — never an LLM — decides which binding is
    the target for a given question. This object only *is* the approved binding;
    it never chooses one.
    """

    taxonomy_version: str
    canonical_code: str
    parent_code: str
    vocabulary: RetrievalVocabularyEntry

    def __post_init__(self) -> None:
        if not self.taxonomy_version or not self.canonical_code or not self.parent_code:
            raise ValueError("DeterministicInitialBinding requires taxonomy_version, canonical_code and parent_code")
        if self.vocabulary.canonical_code != self.canonical_code:
            raise ValueError(
                "DeterministicInitialBinding.vocabulary.canonical_code must equal canonical_code"
            )


# The kinetics binding kept from Phase 9U.1 — now one entry of a general registry
# rather than a value hard-coded across three call sites.
_KINETICS_INITIAL_BINDING = DeterministicInitialBinding(
    taxonomy_version=KINETICS_TAXONOMY_VERSION,
    canonical_code=KINETICS_RETRIEVAL_VOCABULARY.canonical_code,
    parent_code=KINETICS_PARENT_CODE,
    vocabulary=KINETICS_RETRIEVAL_VOCABULARY,
)

# THE single registry of approved deterministic INITIAL bindings. Keyed by
# ``(taxonomy_version, canonical_code)`` so a generation may register many
# contents. Nothing outside this dict binds an INITIAL result; adding a binding
# here (plus its catalog node + vocabulary) is the ONLY structural change needed
# to enable a new deterministic INITIAL target.
_DETERMINISTIC_INITIAL_BINDINGS: dict[tuple[str, str], DeterministicInitialBinding] = {
    (_KINETICS_INITIAL_BINDING.taxonomy_version, _KINETICS_INITIAL_BINDING.canonical_code): _KINETICS_INITIAL_BINDING,
}

# Phase 9U.2-G5 — the ``curriculum-v2`` generation (19 approved contents). Each
# binding is INERT until its catalog CONTENT node exists (``_validate_target_taxonomy``
# fails closed otherwise), so registering them ahead of the catalog write is safe.
# The kinetics binding above is untouched.
from ._curriculum_v2_bindings import build_curriculum_v2_bindings as _build_curriculum_v2_bindings  # noqa: E402

for _cv2 in _build_curriculum_v2_bindings(RetrievalVocabularyEntry, DeterministicInitialBinding):
    _DETERMINISTIC_INITIAL_BINDINGS[(_cv2.taxonomy_version, _cv2.canonical_code)] = _cv2
del _cv2


def registered_initial_taxonomy_versions() -> tuple[str, ...]:
    """Deterministically ordered distinct taxonomy versions that have a binding."""
    return tuple(sorted({tv for (tv, _code) in _DETERMINISTIC_INITIAL_BINDINGS}))


def registered_initial_vocabularies() -> tuple[RetrievalVocabularyEntry, ...]:
    """Deterministically ordered distinct vocabularies across all bindings."""
    seen: dict[tuple[str, str], RetrievalVocabularyEntry] = {}
    for binding in _DETERMINISTIC_INITIAL_BINDINGS.values():
        seen[(binding.vocabulary.canonical_code, binding.vocabulary.version)] = binding.vocabulary
    return tuple(v for _key, v in sorted(seen.items()))


def resolve_registered_initial_binding(
    taxonomy_version: str, content_code: str | None = None
) -> DeterministicInitialBinding | None:
    """Resolve the approved binding.

    * ``content_code`` given  → exact ``(taxonomy_version, content_code)`` lookup.
    * ``content_code`` is None → if the ``taxonomy_version`` has exactly one
      binding, return it (preserves the single-target kinetics call); if it has
      more than one, return ``None`` (ambiguous — caller must disambiguate); if
      none, return ``None`` (unregistered).

    Never falls back to another taxonomy version.
    """
    if content_code is not None:
        return _DETERMINISTIC_INITIAL_BINDINGS.get((taxonomy_version, content_code))
    matches = [b for (tv, _code), b in _DETERMINISTIC_INITIAL_BINDINGS.items() if tv == taxonomy_version]
    return matches[0] if len(matches) == 1 else None


# Backward-compatible view: ``taxonomy_version -> vocabulary``. Valid only while a
# taxonomy version has exactly one binding (true for the kinetics generation).
# New multi-content generations must use the registry / resolver above.
_INITIAL_CONTROLLED_VOCABULARIES: dict[str, RetrievalVocabularyEntry] = {
    tv: resolve_registered_initial_binding(tv).vocabulary  # type: ignore[union-attr]
    for tv in registered_initial_taxonomy_versions()
    if resolve_registered_initial_binding(tv) is not None
}


class ClassificationProposalService:
    max_complementary_contents = int(os.getenv("CLASSIFICATION_MAX_COMPLEMENTARY_CONTENTS", "3"))
    _generic_lexical_terms = {"quimica", "celular", "funcao"}
    _gap_types = {
        "NO_COMPATIBLE_NODE",
        "MISSING_AREA",
        "MISSING_CONTENT",
        "MISSING_SUBCONTENT",
    }
    _review_reasons = {
        "LOW_CONFIDENCE",
        "CATALOG_GAP",
        "TAXONOMY_GRANULARITY_GAP",
        "MULTIPLE_CANDIDATES",
        "INVALID_EVIDENCE",
        "VISUAL_DEPENDENCY",
        "INVALID_HIERARCHY",
    }

    def __init__(self, session: AsyncSession, *, confidence_threshold: float = 0.8):
        self.session = session
        self.confidence_threshold = Decimal(str(confidence_threshold))

    async def propose_with_provider(self, question_version_id: UUID, provider: TextGenerationProvider, *, classifier_version: str, taxonomy_version: str, prompt_version: str, reclassification_audit: dict[str, Any] | None = None, classification_mode: str = "STANDARD", initial_binding: "DeterministicInitialBinding | None" = None) -> PedagogicalClassification:
        version = await self.session.scalar(select(QuestionVersion).where(QuestionVersion.id == question_version_id).options(selectinload(QuestionVersion.options)))
        if version is None or not (version.canonical_text or version.statement or "").strip():
            raise ValueError("Question version must contain text")
        catalog = list((await self.session.scalars(select(CatalogNode).where(CatalogNode.active.is_(True)).order_by(CatalogNode.code))).all())
        recovered_candidates = self.recover_candidates(
            version.statement or version.canonical_text, catalog
        )
        input_data = {
            "question_version_id": str(version.id), "statement": version.statement or version.canonical_text,
            "options": [{"key": option.option_key, "text": option.text} for option in sorted(version.options, key=lambda option: option.position)],
            "question_content_hash": version.content_hash, "catalog": [{"code": node.code, "type": node.node_type, "parent_id": str(node.parent_id) if node.parent_id else None} for node in catalog],
            "recovered_candidate_classifications": recovered_candidates,
            "classifier_version": classifier_version, "taxonomy_version": taxonomy_version, "prompt_version": prompt_version,
            "reclassification_audit": reclassification_audit,
            "classification_mode": classification_mode,
        }
        input_hash = self._hash(input_data)
        existing = await self.session.scalar(select(PedagogicalClassification).where(
            PedagogicalClassification.question_version_id == version.id,
            PedagogicalClassification.model_version == classifier_version,
            PedagogicalClassification.prompt_version == prompt_version,
            PedagogicalClassification.metadata_["input_hash"].as_string() == input_hash,
        ).order_by(PedagogicalClassification.created_at.desc()))
        if existing is not None:
            return existing
        # The prompt text and RESPONSE_SCHEMA are a system-owned, versioned,
        # provider-independent artifact (agente_ia_edu.classification_prompts).
        # PHASE 11.18: moved out of this literal; artifact "v1" is byte-identical
        # to the previous inline construction.
        prompt = get_classification_prompt().build(
            recovered_candidates=recovered_candidates,
            question_data={key: input_data[key] for key in ("statement", "options", "question_content_hash")},
        )
        result = await provider.generate(TextGenerationRequest(prompt=prompt))
        try:
            output = json.loads(result.text)
        except json.JSONDecodeError as exc:
            raise ValueError("Provider returned invalid classification JSON") from exc
        if not isinstance(output, dict):
            raise ValueError("Provider response must be an object")
        return await self._persist_provider_output(version, output, catalog, recovered_candidates, input_hash, classifier_version, taxonomy_version, prompt_version, result.provider, result.model, reclassification_audit, classification_mode, initial_binding)

    async def reclassify_with_provider(self, question_version_id: UUID, provider: TextGenerationProvider, *, source_taxonomy_version: str, target_taxonomy_version: str, classifier_version: str, prompt_version: str, reclassification_reason: str) -> PedagogicalClassification:
        """Create a versioned proposal without changing historical classification rows."""
        await self._validate_target_taxonomy(target_taxonomy_version)
        original = await self.session.scalar(
            select(PedagogicalClassification)
            .where(
                PedagogicalClassification.question_version_id == question_version_id,
                PedagogicalClassification.metadata_["taxonomy_version"].as_string()
                == source_taxonomy_version,
            )
            .order_by(PedagogicalClassification.created_at.desc())
        )
        if original is None:
            raise ValueError("Source taxonomy proposal is unavailable")
        audit = {
            "source_taxonomy_version": source_taxonomy_version,
            "target_taxonomy_version": target_taxonomy_version,
            "original_proposal_id": str(original.id),
            "controlled_vocabulary_version": KINETICS_RETRIEVAL_VOCABULARY.version,
            "reclassification_reason": reclassification_reason,
        }
        return await self.propose_with_provider(
            question_version_id,
            provider,
            classifier_version=classifier_version,
            taxonomy_version=target_taxonomy_version,
            prompt_version=prompt_version,
            reclassification_audit=audit,
            classification_mode="RECLASSIFICATION",
        )

    async def classify_initial_with_provider(self, question_version_id: UUID, provider: TextGenerationProvider, *, target_taxonomy_version: str, classifier_version: str, prompt_version: str, target_content_code: str | None = None) -> PedagogicalClassification:
        """Create an initial target-taxonomy proposal without requiring source history.

        ``target_content_code`` disambiguates when a taxonomy generation registers
        more than one deterministic INITIAL binding; it may be omitted when the
        taxonomy has exactly one binding (e.g. ``024_chemistry_kinetics``).
        """
        binding = await self._validate_target_taxonomy(target_taxonomy_version, target_content_code)
        return await self.propose_with_provider(
            question_version_id,
            provider,
            classifier_version=classifier_version,
            taxonomy_version=target_taxonomy_version,
            prompt_version=prompt_version,
            classification_mode="INITIAL",
            initial_binding=binding,
        )

    async def supersede_initial_classification(
        self,
        *,
        superseded_id: UUID,
        question_version_id: UUID,
        provider: TextGenerationProvider,
        target_taxonomy_version: str,
        classifier_version: str,
        prompt_version: str,
    ) -> PedagogicalClassification:
        """Create a new INITIAL classification that supersedes an existing ACTIVE one.

        Generic lifecycle infrastructure, not specific to any question: this
        is the only place in the service that ever changes an existing row,
        and it changes exactly one field on it (`lifecycle`, ACTIVE ->
        SUPERSEDED) - every substantive column of the superseded row (its
        id, hashes, classifier_version, prompt_version, taxonomy_version,
        metadata, status, created_at, ...) is left untouched.

        The new row is produced by the ordinary, unmodified
        classify_initial_with_provider() pipeline - the same controlled
        vocabulary binding, evidence gate, hash computation and idempotency
        behaviour apply exactly as for any other INITIAL classification; this
        method only adds the atomic lifecycle handoff around it. The old
        row's lifecycle flip and the new row's insert are flushed and
        committed together, so the at-most-one-ACTIVE database constraint
        never sees an intermediate state with zero or two ACTIVE rows for
        the same (question_version_id, taxonomy_version). If the underlying
        classification fails (fail-closed provider-override rejection,
        invalid evidence, etc.) the lifecycle flip is rolled back with it, so
        a failed correction attempt never leaves the old row without an
        active replacement.

        Fails closed unless `superseded_id` resolves to a currently ACTIVE
        row for the exact same (question_version_id, target_taxonomy_version)
        - a classification can only be superseded once, and never across a
        different question or taxonomy version. Concurrent callers racing to
        supersede the same row are serialised by the database itself: the
        partial unique index on (question_version_id, taxonomy_version) WHERE
        lifecycle = 'ACTIVE' rejects the second commit, it is not merely an
        application-level check.
        """
        old = await self.session.get(PedagogicalClassification, superseded_id)
        if old is None:
            raise ValueError("Superseded classification was not found")
        if old.question_version_id != question_version_id:
            raise ValueError("Supersession must target the same question_version_id")
        if (old.metadata_ or {}).get("taxonomy_version") != target_taxonomy_version:
            raise ValueError("Supersession must target the same taxonomy_version")
        if old.lifecycle != "ACTIVE":
            raise ValueError("Only an ACTIVE classification can be superseded")

        old.lifecycle = "SUPERSEDED"
        try:
            new = await self.classify_initial_with_provider(
                question_version_id,
                provider,
                target_taxonomy_version=target_taxonomy_version,
                classifier_version=classifier_version,
                prompt_version=prompt_version,
            )
        except Exception:
            await self.session.rollback()
            raise
        if new.id == old.id:
            await self.session.rollback()
            raise ValueError(
                "Superseding classification resolved to the same row being superseded; "
                "use a classifier_version/prompt_version distinct from the original"
            )
        new.supersedes_id = old.id
        await self.session.commit()
        await self.session.refresh(new)
        await self.session.refresh(old)
        return new

    async def _validate_target_taxonomy(
        self, target_taxonomy_version: str, target_content_code: str | None = None
    ) -> DeterministicInitialBinding:
        """Fail closed unless ``(target_taxonomy_version[, target_content_code])`` is a
        registered deterministic INITIAL binding whose CONTENT node and AREA parent
        are physically present in the catalog. Returns the resolved binding.

        No silent fallback: an unregistered taxonomy/content, or an ambiguous
        taxonomy with no content code, raises.
        """
        binding = resolve_registered_initial_binding(target_taxonomy_version, target_content_code)
        if binding is None:
            has_any = any(tv == target_taxonomy_version for (tv, _c) in _DETERMINISTIC_INITIAL_BINDINGS)
            if has_any and target_content_code is None:
                raise ValueError(
                    "Target taxonomy version has multiple bindings; a target content code is required"
                )
            raise ValueError("Target taxonomy version is unavailable")
        node = await self.session.scalar(
            select(CatalogNode).where(CatalogNode.code == binding.canonical_code)
        )
        if node is None or node.node_type != "CONTENT" or not node.active:
            raise ValueError("Target taxonomy node is unavailable or incompatible")
        parent = await self.session.get(CatalogNode, node.parent_id)
        if parent is None or parent.code != binding.parent_code or parent.node_type != "AREA":
            raise ValueError("Target taxonomy parent is unavailable or incompatible")
        return binding

    def recover_candidates(
        self,
        question_text: str,
        catalog: list[CatalogNode],
        *,
        vocabularies: "tuple[RetrievalVocabularyEntry, ...] | None" = None,
    ) -> list[dict[str, Any]]:
        """Merge current lexical candidates with every registered deterministic
        INITIAL vocabulary whose canonical node is present in the catalog.

        ``vocabularies`` overrides the registry (tests only). Behaviour is
        otherwise unchanged: only registered candidates are added, no match means
        no controlled-vocabulary candidate, and the deterministic merge/priority
        rules select among equal paths.
        """
        candidates = self.retrieve_candidate_classifications(question_text, catalog)
        code_map = {node.code: node for node in catalog}
        for vocabulary in (
            vocabularies if vocabularies is not None else registered_initial_vocabularies()
        ):
            if not vocabulary.enabled or vocabulary.canonical_code not in code_map:
                continue
            match = self.match_retrieval_vocabulary(
                question_text, vocabulary, known_codes=set(code_map)
            )
            if match is None:
                continue
            node = code_map[vocabulary.canonical_code]
            path = self._path_to_node(node, {item.id: item for item in catalog})
            values = {item.node_type.lower() + "_code": item.code for item in path}
            candidate = {
                "discipline_code": values.get("discipline_code"),
                "area_code": values.get("area_code"),
                "content_code": values.get("content_code"),
                "subcontent_code": values.get("subcontent_code"),
                "rank": 0,
                "score": match.total_score,
                "specificity_score": 0,
                "lexical_score": match.lexical_score,
                "position_score": 0,
                "context_score": match.context_score,
                "term_proximity_score": 0,
                "context_conflict_score": 0,
                "generic_term_penalty": 0,
                "ancestor_penalty": 0,
                "final_score": match.total_score,
                "candidate_type": match.candidate_type,
                "matched_terms": list(match.matched_primary_terms + match.matched_specific_terms + match.matched_contextual_expressions),
                "normalized_matched_terms": list(match.matched_primary_terms + match.matched_specific_terms + match.matched_contextual_expressions),
                "match_positions": list(match.match_positions),
                "retrieval_vocabulary_version": vocabulary.version,
                "rationale": "controlled retrieval vocabulary match",
            }
            existing_index = next(
                (
                    index
                    for index, existing in enumerate(candidates)
                    if self._same_path(candidate, existing)
                ),
                None,
            )
            if existing_index is None:
                candidates.append(candidate)
            elif self._candidate_merge_priority(candidate) > self._candidate_merge_priority(
                candidates[existing_index]
            ):
                # Same logical path recovered twice (e.g. a weak lexical ancestor and
                # the controlled-vocabulary match): keep the higher-scoring / more
                # informative candidate, deterministically and regardless of order.
                candidates[existing_index] = candidate
        candidates.sort(key=lambda item: (-item["score"], self._path_key(item)))
        for rank, candidate in enumerate(candidates, start=1):
            candidate["rank"] = rank
        return candidates

    @staticmethod
    def _candidate_merge_priority(candidate: dict[str, Any]) -> tuple[int, int, int]:
        """Deterministic tie-break when two candidates share the same logical path.

        Higher score wins; on an equal score a CONTROLLED_VOCABULARY candidate wins;
        the retrieval-vocabulary-version flag is the final, order-independent tie-break.
        """
        return (
            int(candidate.get("score", 0)),
            1 if candidate.get("candidate_type") == "CONTROLLED_VOCABULARY" else 0,
            1 if candidate.get("retrieval_vocabulary_version") else 0,
        )

    @classmethod
    def resolve_initial_controlled_vocabulary_binding(
        cls,
        statement: str,
        taxonomy_version: str,
        recovered_candidates: list[dict[str, Any]],
        *,
        content_code: str | None = None,
    ) -> ControlledVocabularyBinding | None:
        """Deterministically decide whether an approved controlled vocabulary binds
        the INITIAL content code for this ``(taxonomy_version[, content_code])``.
        Never calls a provider.

        Returns ``None`` when there is no approved binding (unregistered, or an
        ambiguous taxonomy with no ``content_code``) or the statement does not
        match its vocabulary — the normal provider flow then applies.
        """
        binding = resolve_registered_initial_binding(taxonomy_version, content_code)
        if binding is None:
            return None
        vocabulary = binding.vocabulary
        if not vocabulary.enabled:
            return None
        match = cls.match_retrieval_vocabulary(statement, vocabulary, known_codes=None)
        if match is None:
            return None
        matched_terms = tuple(
            match.matched_primary_terms
            + match.matched_specific_terms
            + match.matched_contextual_expressions
        )
        bound = next(
            (
                candidate
                for candidate in recovered_candidates
                if candidate.get("content_code") == vocabulary.canonical_code
                and candidate.get("subcontent_code") is None
            ),
            None,
        )
        if bound is None:
            return ControlledVocabularyBinding(
                controlled_vocabulary_version=vocabulary.version,
                canonical_code=vocabulary.canonical_code,
                taxonomy_version=taxonomy_version,
                status="NEEDS_REVIEW",
                bound_candidate=None,
                matched_terms=matched_terms,
                reason="controlled vocabulary matched but its canonical candidate was not recovered",
            )
        return ControlledVocabularyBinding(
            controlled_vocabulary_version=vocabulary.version,
            canonical_code=vocabulary.canonical_code,
            taxonomy_version=taxonomy_version,
            status="BOUND",
            bound_candidate=bound,
            matched_terms=matched_terms,
            reason="controlled vocabulary deterministically binds the INITIAL content code",
        )

    @staticmethod
    def apply_controlled_vocabulary_binding(
        output: dict[str, Any],
        selected_candidate: dict[str, Any] | None,
        binding: ControlledVocabularyBinding,
    ) -> tuple[dict[str, Any], dict[str, Any] | None, bool]:
        """Enforce a controlled-vocabulary binding on a provider output.

        Returns ``(output, selected_candidate, conflict)``. The provider can never
        replace a BOUND binding: the primary codes are forced to the bound
        candidate. A disagreement (or a missing canonical candidate) forces the
        result to NEEDS_REVIEW. No candidate is ever invented.
        """
        keys = ("discipline_code", "area_code", "content_code", "subcontent_code")
        if binding.status == "BOUND":
            bound = binding.bound_candidate
            conflict = selected_candidate is None or not ClassificationProposalService._same_path(
                selected_candidate, bound
            )
            merged = {
                **output,
                **{key: bound[key] for key in keys},
                "selected_candidate_rank": bound["rank"],
            }
            return merged, bound, conflict
        merged = {
            **output,
            **{key: None for key in keys},
            "selected_candidate_rank": None,
        }
        return merged, None, True

    def retrieve_candidate_classifications(self, question_text: str, catalog: list[CatalogNode]) -> list[dict[str, Any]]:
        """Return deterministic candidates from pedagogical labels and descriptions only."""
        normalized_question = self._normalized_terms(question_text)
        question_terms = self._normalized_term_sequence(question_text)
        by_id = {node.id: node for node in catalog}
        candidates = []
        for node in catalog:
            path = self._path_to_node(node, by_id)
            node_terms = self._normalized_terms(node.name)
            description_terms = self._normalized_terms(node.description or "")
            ancestor_terms = set().union(
                *(self._normalized_terms(f"{ancestor.name} {ancestor.description or ''}") for ancestor in path[:-1])
            )
            distinctive_terms = node_terms - ancestor_terms
            matched_label_terms = sorted(normalized_question.intersection(distinctive_terms))
            matched_description_terms = sorted(normalized_question.intersection(description_terms - ancestor_terms))
            if not matched_label_terms and not matched_description_terms:
                continue
            term_proximity_score = 0
            context_conflict_score = 0
            values = {item.node_type.lower() + "_code": item.code for item in path}
            depth = sum(value is not None for key, value in values.items() if key.endswith("_code"))
            first_match = min(
                question_terms.index(term)
                for term in matched_label_terms + matched_description_terms
            )
            specificity_score = 100 if node.node_type == "SUBCONTENT" else 0
            lexical_score = (
                20 * len(matched_label_terms)
                + 10 * len(matched_description_terms)
                + depth
            )
            position_score = max(0, 10 - first_match)
            generic_term_penalty = (
                40
                if node.node_type != "SUBCONTENT"
                and set(matched_label_terms + matched_description_terms).issubset(
                    self._generic_lexical_terms
                )
                else 0
            )
            ancestor_penalty = 2 if node.node_type != "SUBCONTENT" else 0
            context_score = 0
            score = (
                specificity_score
                + lexical_score
                + position_score
                + context_score
                + term_proximity_score
                - generic_term_penalty
                - ancestor_penalty
                - context_conflict_score
            )
            if score <= 0:
                continue
            candidates.append({
                "discipline_code": values.get("discipline_code"),
                "area_code": values.get("area_code"),
                "content_code": values.get("content_code"),
                "subcontent_code": values.get("subcontent_code"),
                "rank": 0,
                "score": score,
                "specificity_score": specificity_score,
                "lexical_score": lexical_score,
                "position_score": position_score,
                "context_score": context_score,
                "term_proximity_score": term_proximity_score,
                "context_conflict_score": context_conflict_score,
                "generic_term_penalty": generic_term_penalty,
                "ancestor_penalty": ancestor_penalty,
                "final_score": score,
                "candidate_type": "EXACT_CANDIDATE" if node.node_type == "SUBCONTENT" else "ANCESTOR_CANDIDATE",
                "matched_terms": sorted(set(matched_label_terms + matched_description_terms)),
                "normalized_matched_terms": sorted(set(matched_label_terms + matched_description_terms)),
                "rationale": "deterministic lexical match against CatalogNode name or description",
            })
        candidates.sort(
            key=lambda item: (
                -item["score"],
                self._path_key(item),
            )
        )
        for rank, candidate in enumerate(candidates, start=1):
            candidate["rank"] = rank
        return candidates

    def diagnose_coverage(
        self,
        question_text: str,
        catalog: list[CatalogNode],
        *,
        taxonomy_gap_confirmed: bool = False,
    ) -> RetrievalDiagnosis:
        """Diagnose retrieval coverage without treating every miss as a taxonomy gap."""
        candidates = self.retrieve_candidate_classifications(question_text, catalog)
        if candidates:
            top = candidates[0]
            if top["subcontent_code"] is not None:
                return RetrievalDiagnosis("COVERED", len(candidates), False, None, [], "A complete recovered path is available.")
            if top["content_code"] is not None:
                return RetrievalDiagnosis("PARTIAL_COVERAGE", len(candidates), False, None, [], "A recovered path lacks a specific lower level.")
            return RetrievalDiagnosis("NO_CANDIDATE", len(candidates), False, None, [], "Only a non-actionable high-level lexical match was recovered.")

        morphology_evidence = self._morphology_evidence(question_text, catalog)
        if morphology_evidence:
            return RetrievalDiagnosis("RETRIEVAL_LIMITATION", 0, True, "MORPHOLOGY", morphology_evidence, "A simple deterministic morphological variant matches catalog text.")
        if taxonomy_gap_confirmed:
            return RetrievalDiagnosis("CATALOG_GAP", 0, False, None, [], "A separate deterministic review confirmed missing taxonomy coverage.")
        return RetrievalDiagnosis("NO_CANDIDATE", 0, False, None, [], "No candidate was recovered; taxonomy coverage is not inferred.")

    @classmethod
    def match_retrieval_vocabulary(
        cls,
        text: str,
        vocabulary: RetrievalVocabularyEntry,
        *,
        known_codes: set[str] | None = None,
    ) -> RetrievalVocabularyMatch | None:
        """Match explicit lexical vocabulary; generic terms are audit-only, never triggers."""
        cls._validate_retrieval_vocabulary(vocabulary, known_codes)
        if not vocabulary.enabled:
            return None
        normalized_text = " ".join(cls._normalized_term_sequence(text))
        text_terms = set(normalized_text.split())
        primary = cls._matched_vocabulary_phrases(normalized_text, vocabulary.primary_terms)
        specific = cls._matched_vocabulary_phrases(normalized_text, vocabulary.specific_terms)
        contextual = cls._matched_vocabulary_phrases(normalized_text, vocabulary.contextual_expressions)
        generic = tuple(
            term for term in vocabulary.generic_terms
            if cls._normalize_vocabulary_phrase(term) in text_terms
        )
        if not primary and not specific and not contextual:
            return None
        matched = primary + specific + contextual
        positions = tuple(normalized_text.index(cls._normalize_vocabulary_phrase(term)) for term in matched)
        primary_score = 60 * len(primary)
        specific_score = 30 * len(specific)
        phrase_score = 50 * len(contextual)
        context_score = 10 if len(primary) + len(specific) + len(contextual) > 1 else 0
        lexical_score = primary_score + specific_score + phrase_score
        return RetrievalVocabularyMatch(
            canonical_code=vocabulary.canonical_code,
            matched_primary_terms=primary,
            matched_specific_terms=specific,
            matched_contextual_expressions=contextual,
            generic_terms_ignored=generic,
            match_positions=positions,
            lexical_score=lexical_score,
            primary_term_score=primary_score,
            specific_term_score=specific_score,
            phrase_match_score=phrase_score,
            context_score=context_score,
            candidate_type="CONTROLLED_VOCABULARY",
            total_score=lexical_score + context_score,
        )

    @classmethod
    def _validate_retrieval_vocabulary(
        cls, vocabulary: RetrievalVocabularyEntry, known_codes: set[str] | None
    ) -> None:
        if not vocabulary.canonical_code or not vocabulary.version:
            raise ValueError("Retrieval vocabulary requires canonical code and version")
        groups = (
            vocabulary.primary_terms,
            vocabulary.specific_terms,
            vocabulary.contextual_expressions,
            vocabulary.generic_terms,
        )
        if any(not isinstance(group, tuple) or any(not isinstance(term, str) or not term.strip() for term in group) for group in groups):
            raise ValueError("Retrieval vocabulary terms must be non-empty tuples of strings")
        literal_terms = [term.strip().casefold() for group in groups for term in group]
        if len(literal_terms) != len(set(literal_terms)):
            raise ValueError("Retrieval vocabulary terms must be unique")
        if known_codes is not None and vocabulary.canonical_code not in known_codes:
            raise ValueError("Retrieval vocabulary canonical code is unknown")

    @classmethod
    def _matched_vocabulary_phrases(cls, normalized_text: str, phrases: tuple[str, ...]) -> tuple[str, ...]:
        matched = []
        normalized_phrases = set()
        for phrase in phrases:
            normalized_phrase = cls._normalize_vocabulary_phrase(phrase)
            if normalized_phrase in normalized_text and normalized_phrase not in normalized_phrases:
                matched.append(phrase)
                normalized_phrases.add(normalized_phrase)
        return tuple(matched)

    @classmethod
    def _normalize_vocabulary_phrase(cls, phrase: str) -> str:
        return " ".join(cls._normalized_term_sequence(phrase))

    @staticmethod
    def _normalized_terms(value: str) -> set[str]:
        normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
        return {
            ClassificationProposalService._morphology_key(term)
            for term in re.findall(r"[a-z0-9]+", normalized)
            if len(term) >= 3
        }

    @staticmethod
    def _normalized_term_sequence(value: str) -> list[str]:
        normalized = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode().lower()
        return [
            ClassificationProposalService._morphology_key(term)
            for term in re.findall(r"[a-z0-9]+", normalized)
            if len(term) >= 3
        ]

    def _morphology_evidence(self, question_text: str, catalog: list[CatalogNode]) -> list[dict[str, str]]:
        question_terms = self._normalized_terms(question_text)
        catalog_terms = set().union(
            *(self._normalized_terms(f"{node.name} {node.description or ''}") for node in catalog)
        )
        evidence = []
        for question_term in sorted(question_terms):
            for catalog_term in sorted(catalog_terms):
                if question_term != catalog_term and self._morphology_key(question_term) == self._morphology_key(catalog_term):
                    evidence.append({"question_term": question_term, "catalog_term": catalog_term})
        return evidence

    @staticmethod
    def _morphology_key(term: str) -> str:
        if term.endswith("oes"):
            return term[:-3] + "ao"
        if term.endswith("es"):
            return term[:-2]
        if term.endswith("s"):
            return term[:-1]
        return term

    @staticmethod
    def _path_to_node(node: CatalogNode, by_id: dict) -> list[CatalogNode]:
        path = [node]
        while path[-1].parent_id is not None:
            path.append(by_id[path[-1].parent_id])
        return list(reversed(path))

    async def _persist_provider_output(self, version, output, catalog, recovered_candidates, input_hash, classifier_version, taxonomy_version, prompt_version, provider_name, model_name, reclassification_audit=None, classification_mode="STANDARD", initial_binding=None):
        required = {"selected_candidate_rank", "discipline_code", "area_code", "content_code", "subcontent_code", "confidence", "evidence", "candidate_classifications", "complementary_contents", "catalog_gap", "gap_type", "taxonomy_coverage_evidence", "review_reason", "visual_dependency", "status"}
        if required - output.keys():
            raise self._validation_error("Provider response is missing required classification fields", output, input_hash)
        confidence_band = str(output["confidence"]).upper()
        if confidence_band not in {"HIGH", "MEDIUM", "LOW"}:
            raise self._validation_error("Invalid confidence band", output, input_hash)
        code_map = {node.code: node for node in catalog}
        primary_codes = [
            output[key]
            for key in ("discipline_code", "area_code", "content_code", "subcontent_code")
        ]
        candidates = output["candidate_classifications"]
        if not isinstance(candidates, list):
            raise self._validation_error("Invalid candidate classifications", output, input_hash)
        if not recovered_candidates:
            if any(code is not None for code in primary_codes):
                raise self._validation_error("Primary curriculum codes must be null when no candidates were recovered", output, input_hash)
            if candidates:
                raise self._validation_error("Candidates must be empty when no candidates were recovered", output, input_hash)
        unknown_primary_codes = [code for code in primary_codes if code is not None and code not in code_map]
        if unknown_primary_codes:
            raise self._validation_error("Unknown curriculum code", output, input_hash)
        hierarchy_valid = self._valid_path(output, code_map)
        if any(code is not None for code in primary_codes) and not hierarchy_valid:
            raise self._validation_error("Invalid curriculum hierarchy", output, input_hash)
        if any(code is not None for code in primary_codes) and not self._is_recovered_path(
            output, recovered_candidates
        ):
            raise self._validation_error("Curriculum candidate was not recovered", output, input_hash)
        selected_candidate = self.validate_candidate_selection(
            output, recovered_candidates, input_hash
        )
        if selected_candidate is not None:
            output = {
                **output,
                **{
                    key: selected_candidate[key]
                    for key in ("discipline_code", "area_code", "content_code", "subcontent_code")
                },
            }
        # Deterministic controlled-vocabulary precedence for the INITIAL flow: when
        # an approved vocabulary binds this taxonomy version, the provider cannot
        # substitute the binding with a lexical candidate from another area.
        controlled_binding = None
        if classification_mode == "INITIAL":
            controlled_binding = self.resolve_initial_controlled_vocabulary_binding(
                version.statement or version.canonical_text,
                taxonomy_version,
                recovered_candidates,
                content_code=initial_binding.canonical_code if initial_binding is not None else None,
            )
            if controlled_binding is not None:
                output, selected_candidate, controlled_binding_conflict = (
                    self.apply_controlled_vocabulary_binding(
                        output, selected_candidate, controlled_binding
                    )
                )
                if controlled_binding_conflict:
                    # Fail closed: the provider tried to override a deterministic
                    # controlled-vocabulary binding, or the bound canonical candidate
                    # was not recovered. Nothing is persisted; the caller must review.
                    raise self._validation_error(
                        "Controlled vocabulary binding was not honoured: "
                        + controlled_binding.reason,
                        output,
                        input_hash,
                    )
                hierarchy_valid = self._valid_path(output, code_map)
        complements = output["complementary_contents"]
        if not isinstance(complements, list) or len(complements) > self.max_complementary_contents:
            raise self._validation_error("Invalid complementary contents", output, input_hash)
        evidence = output["evidence"]
        statement = version.statement or version.canonical_text
        evidence_valid = isinstance(evidence, list) and bool(evidence) and all(isinstance(item, dict) and item.get("text") and item.get("reason") and item["text"] in statement for item in evidence)
        valid_candidates = self._valid_candidates(candidates, code_map)
        candidate_valid = len(valid_candidates) == len(candidates)
        if not candidate_valid:
            raise self._validation_error("Invalid candidate classifications", output, input_hash)
        if any(not self._is_recovered_path(candidate, recovered_candidates) for candidate in valid_candidates):
            raise self._validation_error("Curriculum candidate was not recovered", output, input_hash)
        if any(
            candidate["rank"] != next(
                recovered["rank"]
                for recovered in recovered_candidates
                if self._same_path(candidate, recovered)
            )
            for candidate in valid_candidates
        ):
            raise self._validation_error("Candidate rank does not match recovered candidate", output, input_hash)
        if valid_candidates and selected_candidate is not None and not any(
            self._same_path(selected_candidate, candidate) for candidate in valid_candidates
        ):
            raise self._validation_error(
                "Selected candidate must be present in candidate classifications",
                output,
                input_hash,
            )
        gap_type = output["gap_type"]
        if gap_type is not None and gap_type not in self._gap_types:
            raise self._validation_error("Invalid gap type", output, input_hash)
        coverage_evidence = output["taxonomy_coverage_evidence"]
        if not isinstance(coverage_evidence, list) or not all(
            isinstance(item, str) and item.strip() for item in coverage_evidence
        ):
            raise self._validation_error("Invalid taxonomy coverage evidence", output, input_hash)
        declared_review_reason = output["review_reason"]
        if declared_review_reason is not None and declared_review_reason not in self._review_reasons:
            raise self._validation_error("Invalid review reason", output, input_hash)
        if bool(output["catalog_gap"]) != (gap_type is not None):
            raise self._validation_error("Catalog gap and gap type are incompatible", output, input_hash)
        no_candidate_catalog_gap = (
            selected_candidate is None
            and output["catalog_gap"]
            and gap_type == "NO_COMPATIBLE_NODE"
            and not candidates
            and output["selected_candidate_rank"] is None
            and all(code is None for code in primary_codes)
            and declared_review_reason == "CATALOG_GAP"
            and output["status"] == "NEEDS_REVIEW"
        )
        if no_candidate_catalog_gap and not coverage_evidence:
            raise self._validation_error(
                "CATALOG_GAP requires taxonomy coverage evidence", output, input_hash
            )
        if not evidence_valid and not no_candidate_catalog_gap:
            raise self._validation_error("Invalid classification evidence", output, input_hash)
        if gap_type is not None:
            expected_reason = (
                "CATALOG_GAP"
                if gap_type == "NO_COMPATIBLE_NODE"
                else "TAXONOMY_GRANULARITY_GAP"
            )
            # derive_review_reason's own priority order (below) already puts
            # visual_dependency ahead of any catalog/gap reason - the AI
            # naming that same, higher-priority reason is self-consistent,
            # not incompatible, whenever visual_dependency is genuinely set.
            acceptable_reasons = {None, expected_reason}
            if output["visual_dependency"]:
                acceptable_reasons.add("VISUAL_DEPENDENCY")
            if declared_review_reason not in acceptable_reasons:
                raise self._validation_error("Gap type and review reason are incompatible", output, input_hash)
            if output["status"] != "NEEDS_REVIEW":
                raise self._validation_error("Gap type requires NEEDS_REVIEW status", output, input_hash)
            if gap_type == "MISSING_SUBCONTENT" and (
                output["discipline_code"] is None
                or output["area_code"] is None
                or output["content_code"] is None
                or output["subcontent_code"] is not None
            ):
                raise self._validation_error(
                    "MISSING_SUBCONTENT requires a partial content path",
                    output,
                    input_hash,
                )
        catalog_gap = bool(output["catalog_gap"]) or not hierarchy_valid or not candidate_valid or any(code not in code_map for code in complements)
        derived_reason = self.derive_review_reason(
            evidence_valid=evidence_valid or no_candidate_catalog_gap,
            visual_dependency=bool(output["visual_dependency"]),
            catalog_gap=catalog_gap,
            gap_type=gap_type,
            multiple_candidates=len(recovered_candidates) > 1,
            confidence=confidence_band,
        )
        review_reason = derived_reason or declared_review_reason
        if output["status"] not in {"PROPOSED", "NEEDS_REVIEW"}:
            raise self._validation_error("Invalid proposal status", output, input_hash)
        proposal_status = "NEEDS_REVIEW" if review_reason or output["status"] == "NEEDS_REVIEW" else "PROPOSED"
        if output["status"] == "NEEDS_REVIEW" and review_reason is None:
            raise self._validation_error("NEEDS_REVIEW requires a review reason", output, input_hash)
        if output["status"] == "PROPOSED" and proposal_status != "PROPOSED":
            raise self._validation_error("Proposed status is incompatible with validation", output, input_hash)
        decision = ClassificationDecision(
            selected_candidate_rank=output["selected_candidate_rank"],
            confidence=confidence_band,
            candidate_type=selected_candidate["candidate_type"] if selected_candidate else None,
            evidence=evidence,
            taxonomy_coverage_evidence=coverage_evidence,
            catalog_gap=catalog_gap,
            gap_type=gap_type,
            review_reason=review_reason,
            status=proposal_status,
        )
        decision_metrics = self._decision_metrics(decision, selected_candidate, recovered_candidates)
        canonical_output = {key: output[key] for key in sorted(required)}
        # Legacy columns are non-null. Missing partial-path levels remain blank;
        # canonical nullable codes are preserved in metadata instead of invented.
        legacy = output if hierarchy_valid else {}
        record = PedagogicalClassification(
            question_version_id=version.id, discipline=legacy.get("discipline_code") or "", content=legacy.get("content_code") or "", subcontent=legacy.get("subcontent_code") or "", difficulty="UNKNOWN",
            classification_confidence={"HIGH": Decimal("0.90"), "MEDIUM": Decimal("0.70"), "LOW": Decimal("0.40")} [confidence_band], difficulty_confidence=None,
            reasoning_type="UNSPECIFIED", prerequisites=[], keywords=[], competencies=[], skills=[], model_name=model_name, model_version=classifier_version, prompt_version=prompt_version, provider_name=provider_name, input_tokens=0, output_tokens=0, total_tokens=0, status="CLASSIFIED" if proposal_status == "PROPOSED" else "NEEDS_REVIEW", source="ai",
            lifecycle="ACTIVE",
            metadata_={"proposal_status": proposal_status, "confidence_band": confidence_band, "discipline_code": output["discipline_code"], "area_code": output["area_code"], "content_code": output["content_code"], "subcontent_code": output["subcontent_code"], "primary_content_code": output["subcontent_code"], "selected_candidate_rank": output["selected_candidate_rank"], "recovered_candidates": recovered_candidates, "complementary_content_codes": complements, "evidence": evidence, "candidate_classifications": valid_candidates, "taxonomy_coverage_evidence": coverage_evidence, "catalog_gap": catalog_gap, "gap_type": gap_type, "review_reason": review_reason, "visual_dependency": bool(output["visual_dependency"]), "input_hash": input_hash, "output_hash": self._hash(canonical_output), "taxonomy_version": taxonomy_version, "question_content_hash": version.content_hash, "classification_mode": classification_mode, "reclassification": reclassification_audit, **decision_metrics},
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record

    @staticmethod
    def _valid_path(path: dict[str, Any], code_map: dict[str, CatalogNode]) -> bool:
        keys = ("discipline_code", "area_code", "content_code", "subcontent_code")
        codes = [path.get(key) for key in keys]
        if not codes[0] or any(code is not None and code not in code_map for code in codes):
            return False
        if any(codes[index] is None and codes[index + 1] is not None for index in range(len(codes) - 1)):
            return False
        nodes = [code_map[code] for code in codes if code is not None]
        expected_types = ["DISCIPLINE", "AREA", "CONTENT", "SUBCONTENT"][:len(nodes)]
        return [node.node_type for node in nodes] == expected_types and all(
            nodes[index].parent_id == nodes[index - 1].id
            for index in range(1, len(nodes))
        )

    def _valid_candidates(self, candidates: Any, code_map: dict[str, CatalogNode]) -> list[dict[str, Any]]:
        if not isinstance(candidates, list):
            return []
        valid = []
        ranks = set()
        paths = set()
        for candidate in candidates:
            rank = candidate.get("rank") if isinstance(candidate, dict) else None
            if not isinstance(rank, int) or rank <= 0 or rank in ranks:
                continue
            if not isinstance(candidate.get("rationale"), str) or not candidate["rationale"].strip():
                continue
            if self._valid_path(candidate, code_map):
                path = self._path_key(candidate)
                if path in paths:
                    continue
                ranks.add(rank)
                paths.add(path)
                valid.append(candidate)
        valid.sort(key=lambda candidate: candidate["rank"])
        return valid

    def validate_candidate_selection(
        self,
        output: dict[str, Any],
        recovered_candidates: list[dict[str, Any]],
        input_hash: str,
    ) -> dict[str, Any] | None:
        """Return the system-owned candidate selected by rank, never provider-owned codes."""
        selected_rank = output["selected_candidate_rank"]
        primary_codes = self._path_key(output)
        if not recovered_candidates:
            if selected_rank is not None:
                raise self._validation_error("Selected candidate rank must be null when no candidates were recovered", output, input_hash)
            if any(code is not None for code in primary_codes):
                raise self._validation_error("Primary curriculum codes must be null when no candidates were recovered", output, input_hash)
            return None
        if selected_rank is None:
            # The lexical pre-filter (recover_candidates) is a heuristic - it
            # can recover a candidate on a single incidental term (a
            # stopword, an object mentioned in passing) that is not actually
            # a good fit (found running a real provider: recovered two
            # CHEMISTRY candidates for a literature question). The AI is not
            # required to pick one just because recovery found something; a
            # null rank is trusted here too, but only when the AI is
            # unambiguous that this is a real gap - explicitly flagged
            # ``catalog_gap`` and no curriculum code smuggled in without a
            # selection backing it (still validated as a genuine gap, not a
            # bare omission, by the catalog_gap/gap_type/review_reason
            # compatibility checks already applied by the caller).
            if output.get("catalog_gap") is True and not any(code is not None for code in primary_codes):
                return None
            raise self._validation_error("Selected candidate rank is invalid", output, input_hash)
        if not isinstance(selected_rank, int) or isinstance(selected_rank, bool):
            raise self._validation_error("Selected candidate rank is invalid", output, input_hash)
        selected = next(
            (candidate for candidate in recovered_candidates if candidate["rank"] == selected_rank),
            None,
        )
        if selected is None:
            raise self._validation_error("Selected candidate rank was not recovered", output, input_hash)
        if primary_codes != self._path_key(selected):
            raise self._validation_error(
                "Primary curriculum codes do not match selected candidate",
                output,
                input_hash,
            )
        return selected

    @classmethod
    def _same_path(cls, left: dict[str, Any], right: dict[str, Any]) -> bool:
        return cls._path_key(left) == cls._path_key(right)

    @staticmethod
    def _path_key(path: dict[str, Any]) -> tuple[Any, Any, Any, Any]:
        return tuple(
            path.get(key)
            for key in ("discipline_code", "area_code", "content_code", "subcontent_code")
        )

    def _validation_error(self, message: str, output: dict[str, Any], input_hash: str) -> ValueError:
        """Expose a non-persistent, secret-free response summary for diagnostics."""
        error = ValueError(message)
        candidates = output.get("candidate_classifications")
        error.diagnostic_output = {
            "fields": sorted(output),
            "field_types": {key: type(value).__name__ for key, value in output.items()},
            "primary_codes": {
                key: output.get(key)
                for key in ("discipline_code", "area_code", "content_code", "subcontent_code")
            },
            "candidate_count": len(candidates) if isinstance(candidates, list) else None,
            "candidate_ranks": [item.get("rank") for item in candidates if isinstance(item, dict)] if isinstance(candidates, list) else [],
            "confidence": output.get("confidence"),
            "catalog_gap": output.get("catalog_gap"),
            "gap_type": output.get("gap_type"),
            "review_reason": output.get("review_reason"),
            "status": output.get("status"),
            "visual_dependency": output.get("visual_dependency"),
            "evidence_count": len(output.get("evidence")) if isinstance(output.get("evidence"), list) else None,
            "input_hash": input_hash,
            "output_hash": self._hash(output),
        }
        return error

    @staticmethod
    def _is_recovered_path(path: dict[str, Any], recovered_candidates: list[dict[str, Any]]) -> bool:
        keys = ("discipline_code", "area_code", "content_code", "subcontent_code")
        path_codes = tuple(path.get(key) for key in keys)
        return any(
            path_codes == tuple(candidate.get(key) for key in keys)
            for candidate in recovered_candidates
        )

    @staticmethod
    def derive_review_reason(*, evidence_valid: bool, visual_dependency: bool, catalog_gap: bool, gap_type: str | None, multiple_candidates: bool, confidence: str) -> str | None:
        """Apply deterministic review priority; provider suggestions never outrank it."""
        if not evidence_valid:
            return "INVALID_EVIDENCE"
        if visual_dependency:
            return "VISUAL_DEPENDENCY"
        if catalog_gap:
            return "TAXONOMY_GRANULARITY_GAP" if gap_type and gap_type != "NO_COMPATIBLE_NODE" else "CATALOG_GAP"
        if multiple_candidates and confidence == "LOW":
            return "MULTIPLE_CANDIDATES"
        if confidence == "LOW":
            return "LOW_CONFIDENCE"
        return None

    @staticmethod
    def _decision_metrics(decision: ClassificationDecision, selected_candidate: dict[str, Any] | None, recovered_candidates: list[dict[str, Any]]) -> dict[str, Any]:
        return {
            "candidate_count": len(recovered_candidates),
            "selected_candidate_score": selected_candidate["score"] if selected_candidate else None,
            "selected_candidate_type": decision.candidate_type,
            "is_top1_selected": decision.selected_candidate_rank == 1,
            "is_leaf_selected": decision.candidate_type == "EXACT_CANDIDATE",
            "is_ancestor_selected": decision.candidate_type == "ANCESTOR_CANDIDATE",
            "selected_less_specific_candidate": bool(selected_candidate and selected_candidate["rank"] != 1),
            "multiple_candidates": len(recovered_candidates) > 1,
            "taxonomy_granularity_gap": decision.review_reason == "TAXONOMY_GRANULARITY_GAP",
            "needs_review": decision.status == "NEEDS_REVIEW",
        }

    @staticmethod
    def _hash(value: Any) -> str:
        return hashlib.sha256(json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")).hexdigest()

    async def propose(self, question_version_id: UUID, proposal: ClassificationProposal, *, classifier_version: str, taxonomy_version: str, provider: str, model: str, prompt_version: str) -> PedagogicalClassification:
        version = await self.session.get(QuestionVersion, question_version_id)
        if version is None or not (version.canonical_text or version.statement or "").strip():
            raise ValueError("Question version must contain text")
        if proposal.difficulty not in {"EASY", "MEDIUM", "HARD", "UNKNOWN"}:
            raise ValueError("Unsupported proposed difficulty")
        if not 0 <= proposal.confidence <= 1:
            raise ValueError("Confidence must be between zero and one")
        if len(proposal.complementary_content_codes) > self.max_complementary_contents:
            raise ValueError("Too many complementary contents")
        codes = [proposal.primary_content_code, *proposal.complementary_content_codes, *proposal.prerequisites]
        found = set((await self.session.scalars(select(CatalogNode.code).where(CatalogNode.code.in_(codes), CatalogNode.active.is_(True)))).all())
        valid = proposal.primary_content_code in found and all(code in found for code in proposal.complementary_content_codes) and all(code in found for code in proposal.prerequisites)
        valid = valid and proposal.primary_content_code not in proposal.complementary_content_codes
        evidence_valid = all(item.get("content_code") in found and item.get("text") and item["text"] in (version.canonical_text or version.statement or "") for item in proposal.evidence)
        status = "CLASSIFIED" if valid and evidence_valid and Decimal(str(proposal.confidence)) >= self.confidence_threshold else "NEEDS_REVIEW"
        record = PedagogicalClassification(
            question_version_id=version.id,
            discipline="CURRICULUM_PROPOSAL", content=proposal.primary_content_code,
            subcontent=proposal.primary_content_code, difficulty=proposal.difficulty,
            classification_confidence=Decimal(str(proposal.confidence)), difficulty_confidence=None,
            reasoning_type=",".join(proposal.cognitive_operations) or "UNSPECIFIED",
            prerequisites=proposal.prerequisites, keywords=proposal.concepts, competencies=[], skills=[],
            model_name=model, model_version=classifier_version, prompt_version=prompt_version,
            provider_name=provider, input_tokens=0, output_tokens=0, total_tokens=0,
            status=status, source="rule", lifecycle="ACTIVE",
            metadata_={"proposal_status": "PROPOSED" if status == "CLASSIFIED" else "NEEDS_REVIEW", "primary_content_code": proposal.primary_content_code, "complementary_content_codes": proposal.complementary_content_codes, "cognitive_operations": proposal.cognitive_operations, "context": proposal.context, "evidence": proposal.evidence, "taxonomy_version": taxonomy_version, "question_content_hash": version.content_hash},
        )
        self.session.add(record)
        await self.session.commit()
        await self.session.refresh(record)
        return record