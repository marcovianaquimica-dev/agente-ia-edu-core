"""PHASE 30 - Authorial Question Pedagogical Classification Engine: tests.

In-memory SQLite + ``CurriculumTaxonomyService.seed_reference_fixture()``
(the SAME reference catalog fixture already used by the official
classification test suite - reused verbatim, never re-derived) + a
deterministic fake ``TextGenerationProvider`` (the same pattern
``tests/test_ai_classification_service.py`` already uses). Real golden
(T11/EX) coverage lives in the manual acceptance walk
(``tests/manual/phase30_authorial_question_classification_report.py``) -
this file covers the deterministic/AI-boundary contract in isolation.
"""

from __future__ import annotations

import ast
import asyncio
import json
import unittest
import uuid as _uuid
from decimal import Decimal
from pathlib import Path

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    PedagogicalClassification,
    Question,
    QuestionOption,
    QuestionVersion,
)
from agente_ia_edu.db.models.admin import School, UserSchoolLink
from agente_ia_edu.providers.errors import (
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult
from agente_ia_edu.services.authorial_classification_policy import confidence_band
from agente_ia_edu.services.authorial_question_classification_service import (
    CLASSIFIER_VERSION,
    TAXONOMY_VERSION,
    AuthorialQuestionClassificationService,
    ClassificationValidationError,
)
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService

_SCHOOL_A = _uuid.uuid5(_uuid.NAMESPACE_DNS, "phase30-school-a")
_SCHOOL_B = _uuid.uuid5(_uuid.NAMESPACE_DNS, "phase30-school-b")

_DILUTION_STATEMENT = (
    "diluicao: 200 mL de uma solucao aquosa foram misturados com agua "
    "destilada para reduzir a concentracao final da mistura."
)
_DILUTION_RESPONSE = {
    "selected_candidate_rank": 1,
    "discipline_code": "CHEMISTRY", "area_code": "CHEMISTRY-PHYSICAL",
    "content_code": "CHEMISTRY-SOLUTIONS", "subcontent_code": "CHEMISTRY-SOLUTIONS-DILUTION",
    "confidence": "HIGH", "evidence": [{"text": "diluicao", "reason": "Exige calculo de diluicao."}],
    "candidate_classifications": [{
        "discipline_code": "CHEMISTRY", "area_code": "CHEMISTRY-PHYSICAL",
        "content_code": "CHEMISTRY-SOLUTIONS", "subcontent_code": "CHEMISTRY-SOLUTIONS-DILUTION",
        "rank": 1, "rationale": "Caminho completo do catalogo.",
    }],
    "complementary_contents": [], "catalog_gap": False, "gap_type": None,
    "taxonomy_coverage_evidence": [], "review_reason": None, "visual_dependency": False, "status": "PROPOSED",
}
_DIFFICULTY_RESPONSE = {"difficulty": "MEDIUM", "confidence": 0.8, "reasoning": "duas etapas de raciocinio"}


def _make_response(text: str) -> TextGenerationResult:
    return TextGenerationResult(text=text, provider="fake", model="fake-model")


class ScriptedProvider:
    """Returns queued responses/exceptions in order; auto-answers the
    difficulty sub-call (recognised by its own prompt marker) with a fixed
    valid response unless a difficulty override is queued too."""

    def __init__(self, content_queue: list, *, difficulty_queue: list | None = None):
        self.content_queue = list(content_queue)
        self.difficulty_queue = list(difficulty_queue) if difficulty_queue is not None else None
        self.calls = 0
        self.prompts: list[str] = []

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        self.calls += 1
        self.prompts.append(request.prompt)
        if "Avalie a dificuldade" in request.prompt:
            if self.difficulty_queue is not None:
                item = self.difficulty_queue.pop(0)
            else:
                item = dict(_DIFFICULTY_RESPONSE)
            if isinstance(item, Exception):
                raise item
            return _make_response(json.dumps(item) if not isinstance(item, str) else item)
        item = self.content_queue.pop(0)
        if isinstance(item, Exception):
            raise item
        return _make_response(json.dumps(item) if not isinstance(item, str) else item)


class Phase30ClassificationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        async with self.factory() as s:
            await CurriculumTaxonomyService(s).seed_reference_fixture()
            s.add(School(id=_SCHOOL_A, code="P30SCHA", name="Escola P30 A", status="ACTIVE"))
            s.add(School(id=_SCHOOL_B, code="P30SCHB", name="Escola P30 B", status="ACTIVE"))
            await s.flush()
            s.add(UserSchoolLink(external_user_id="prof_a", school_id=_SCHOOL_A, role="TEACHER",
                                 scope_type="SCHOOL", scope_external_id=str(_SCHOOL_A), active=True))
            await s.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _make_question_version(
        self, statement: str, *, school_id=_SCHOOL_A, options: list[str] | None = None,
    ) -> _uuid.UUID:
        async with self.factory() as s:
            q = Question(validation_status="validated", origin_type="AUTHORIAL", status="PUBLISHED",
                         visibility_scope="SCHOOL", school_id=school_id)
            s.add(q)
            await s.flush()
            v = QuestionVersion(question_id=q.id, version_kind="official_original",
                                canonical_text=statement, content_hash=f"hash-{_uuid.uuid4().hex}")
            s.add(v)
            await s.flush()
            for i, text in enumerate(options or [], start=1):
                s.add(QuestionOption(question_version_id=v.id, option_key=chr(64 + i), position=i, text=text))
            await s.commit()
            return v.id

    # -- 1/2. classificação válida / candidato válido -----------------------
    async def test_valid_classification_end_to_end(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            provider = ScriptedProvider([_DILUTION_RESPONSE])
            outcome = await svc.classify_question_version(vid, provider, actor="prof_a")
        self.assertEqual(outcome.status, "CLASSIFIED")
        self.assertIsNone(outcome.review_reason)
        self.assertEqual(outcome.classification.content, "CHEMISTRY-SOLUTIONS")
        self.assertEqual(outcome.classification.subcontent, "CHEMISTRY-SOLUTIONS-DILUTION")
        self.assertEqual(outcome.classification.difficulty, "MEDIUM")

    # -- 3/4. candidato inexistente / código inventado -----------------------
    async def test_ai_inventing_a_nonexistent_code_is_rejected(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            bad = {**_DILUTION_RESPONSE, "content_code": "CHEMISTRY-INVENTED-NONEXISTENT",
                  "subcontent_code": None, "selected_candidate_rank": None,
                  "candidate_classifications": []}
            provider = ScriptedProvider([bad])
            outcome = await svc.classify_question_version(vid, provider, actor="prof_a")
        self.assertEqual(outcome.status, "NEEDS_REVIEW")
        self.assertEqual(outcome.review_reason, "INVALID_AI_OUTPUT")
        # never silently discarded - the question still has a persisted row
        self.assertIsNotNone(outcome.classification.id)
        # the validator raises a rich, secret-free diagnostic_output (fields,
        # primary codes, candidate count/ranks, confidence, etc) alongside
        # the short message - it must survive into persisted metadata, or
        # every future INVALID_AI_OUTPUT requires an expensive live re-call
        # against the real provider just to see what the AI actually sent.
        diagnostic = outcome.classification.metadata_.get("diagnostic_output")
        self.assertIsNotNone(diagnostic)
        self.assertIn("primary_codes", diagnostic)
        self.assertEqual(diagnostic["primary_codes"]["content_code"], "CHEMISTRY-INVENTED-NONEXISTENT")

    # -- 5/6/7. disciplina/content/subcontent incompatíveis ------------------
    async def test_ai_returning_valid_codes_from_incompatible_hierarchy_is_rejected(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            # PHYSICS-MECHANICS-KINEMATICS-UNIFORM is a REAL, active catalog
            # code - but it does not descend from CHEMISTRY/CHEMISTRY-PHYSICAL,
            # so pairing it under a chemistry discipline/area is an invalid path.
            bad = {**_DILUTION_RESPONSE, "subcontent_code": "PHYSICS-MECHANICS-KINEMATICS-UNIFORM",
                  "candidate_classifications": [{
                      **_DILUTION_RESPONSE["candidate_classifications"][0],
                      "subcontent_code": "PHYSICS-MECHANICS-KINEMATICS-UNIFORM",
                  }]}
            provider = ScriptedProvider([bad])
            outcome = await svc.classify_question_version(vid, provider, actor="prof_a")
        self.assertEqual(outcome.status, "NEEDS_REVIEW")
        self.assertEqual(outcome.review_reason, "INVALID_AI_OUTPUT")

    # -- 8/9. confidence banding + LOW -> NEEDS_REVIEW -----------------------
    async def test_low_confidence_routes_to_needs_review(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            low = {**_DILUTION_RESPONSE, "confidence": "LOW", "status": "NEEDS_REVIEW", "review_reason": "LOW_CONFIDENCE"}
            provider = ScriptedProvider([low])
            outcome = await svc.classify_question_version(vid, provider, actor="prof_a")
        self.assertEqual(outcome.status, "NEEDS_REVIEW")
        # the deterministic engine's own review-reason priority (never
        # invented by this phase) may report MULTIPLE_CANDIDATES instead of
        # a bare LOW_CONFIDENCE when this statement's lexical recovery also
        # surfaces a second plausible candidate - both are a correct,
        # non-silent LOW-confidence routing to a human.
        self.assertIn(outcome.review_reason, ("LOW_CONFIDENCE", "MULTIPLE_CANDIDATES"))
        self.assertEqual(confidence_band(outcome.classification.classification_confidence), "LOW")

    def test_confidence_band_never_invents_a_band_for_none(self):
        self.assertIsNone(confidence_band(None))
        self.assertEqual(confidence_band(Decimal("0.9")), "HIGH")

    # -- 10. ambiguidade (múltiplos candidatos + baixa confiança) -----------
    async def test_ambiguous_multiple_candidates_low_confidence_needs_review(self):
        async with self.factory() as s:
            vid = await self._make_question_version(
                "solucoes e diluicao e concentracao envolvem calculos variados de misturas.")
            svc = AuthorialQuestionClassificationService(s)
            ambiguous = {
                **_DILUTION_RESPONSE, "confidence": "LOW",
                "status": "NEEDS_REVIEW", "review_reason": "MULTIPLE_CANDIDATES",
                "candidate_classifications": [
                    _DILUTION_RESPONSE["candidate_classifications"][0],
                    {**_DILUTION_RESPONSE["candidate_classifications"][0],
                     "subcontent_code": "CHEMISTRY-SOLUTIONS-CONCENTRATION", "rank": 2,
                     "rationale": "Caminho alternativo."},
                ],
            }
            provider = ScriptedProvider([ambiguous])
            outcome = await svc.classify_question_version(vid, provider, actor="prof_a")
        self.assertEqual(outcome.status, "NEEDS_REVIEW")
        self.assertIn(outcome.review_reason, ("LOW_CONFIDENCE", "MULTIPLE_CANDIDATES"))

    # -- 11/17 (part). manual classification (dropdown-validated) -----------
    async def test_manual_classification_valid_codes(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            record = await svc.manual_classify(
                vid, discipline_code="CHEMISTRY", area_code="CHEMISTRY-PHYSICAL",
                content_code="CHEMISTRY-SOLUTIONS", subcontent_code="CHEMISTRY-SOLUTIONS-DILUTION",
                difficulty="EASY", reason="revisao inicial", actor="prof_a", actor_type="TEACHER",
            )
        self.assertEqual(record.status, "CLASSIFIED")
        self.assertEqual(record.source, "human")
        self.assertEqual(record.difficulty, "EASY")

    async def test_manual_classification_rejects_nonexistent_code(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            with self.assertRaises(ClassificationValidationError):
                await svc.manual_classify(
                    vid, discipline_code="CHEMISTRY", area_code="CHEMISTRY-PHYSICAL",
                    content_code="CHEMISTRY-DOES-NOT-EXIST", subcontent_code=None,
                    difficulty="EASY", reason="x", actor="prof_a", actor_type="TEACHER",
                )

    async def test_manual_classification_rejects_incompatible_hierarchy(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            with self.assertRaises(ClassificationValidationError):
                await svc.manual_classify(
                    # a real CONTENT paired with a SUBCONTENT that is not its child
                    vid, discipline_code="CHEMISTRY", area_code="CHEMISTRY-PHYSICAL",
                    content_code="CHEMISTRY-SOLUTIONS", subcontent_code="PHYSICS-MECHANICS-KINEMATICS-UNIFORM",
                    difficulty="EASY", reason="x", actor="prof_a", actor_type="TEACHER",
                )

    async def test_manual_classification_rejects_invalid_difficulty(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            with self.assertRaises(ClassificationValidationError):
                await svc.manual_classify(
                    vid, discipline_code="CHEMISTRY", area_code="CHEMISTRY-PHYSICAL",
                    content_code="CHEMISTRY-SOLUTIONS", subcontent_code="CHEMISTRY-SOLUTIONS-DILUTION",
                    difficulty="IMPOSSIBLE", reason="x", actor="prof_a", actor_type="TEACHER",
                )

    # -- 12/13. aprovação + auditoria ----------------------------------------
    async def test_approval_is_recorded_in_audit_history(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            provider = ScriptedProvider([dict(_DILUTION_RESPONSE)])
            outcome = await svc.classify_question_version(vid, provider, actor="prof_a")
            approved = await svc.approve_classification(
                outcome.classification.id, actor="coord_a", actor_type="COORDINATOR", reason="confirmado")
            history = await svc.get_history(vid)
        self.assertTrue(approved.metadata_.get("human_approved"))
        self.assertEqual(approved.metadata_.get("approved_by"), "coord_a")
        actions = [h.action for h in history]
        self.assertIn("AI_CLASSIFY", actions)
        self.assertIn("APPROVE", actions)
        self.assertTrue(all(h.actor and h.created_at for h in history))  # never anonymous/undated

    # -- 14/27 (versioning). reclassification --------------------------------
    async def test_reclassify_supersedes_previous_and_records_audit(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            first = await svc.manual_classify(
                vid, discipline_code="CHEMISTRY", area_code="CHEMISTRY-PHYSICAL",
                content_code="CHEMISTRY-SOLUTIONS", subcontent_code="CHEMISTRY-SOLUTIONS-CONCENTRATION",
                difficulty="EASY", reason="classificacao inicial manual", actor="prof_a", actor_type="TEACHER",
            )
            provider = ScriptedProvider([dict(_DILUTION_RESPONSE)])
            reclassified = await svc.reclassify(
                vid, provider, actor="coord_a", actor_type="COORDINATOR",
                reason="professor discordou da classificacao inicial")
            refreshed_first = await s.get(PedagogicalClassification, first.id)
            active = await svc.get_active_classification(vid)
            history = await svc.get_history(vid)
        self.assertEqual(refreshed_first.lifecycle, "SUPERSEDED")
        self.assertEqual(reclassified.lifecycle, "ACTIVE")
        self.assertEqual(reclassified.supersedes_id, first.id)
        self.assertEqual(active.id, reclassified.id)
        self.assertEqual(reclassified.subcontent, "CHEMISTRY-SOLUTIONS-DILUTION")  # genuinely different result
        reclassify_events = [h for h in history if h.action == "RECLASSIFY"]
        self.assertEqual(len(reclassify_events), 1)
        self.assertIsNotNone(reclassify_events[0].previous_value)
        self.assertEqual(reclassify_events[0].reason, "professor discordou da classificacao inicial")

    async def test_reclassify_without_existing_classification_raises(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            with self.assertRaises(ValueError):
                await svc.reclassify(vid, ScriptedProvider([]), actor="prof_a", actor_type="TEACHER", reason="x")

    # -- 16. cache: an already-classified question never re-calls the AI ----
    async def test_cache_avoids_a_second_ai_call(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            provider = ScriptedProvider([dict(_DILUTION_RESPONSE)])
            first = await svc.classify_question_version(vid, provider, actor="prof_a")
            second = await svc.classify_question_version(vid, provider, actor="prof_a")
        self.assertFalse(first.cache_hit)
        self.assertTrue(second.cache_hit)
        self.assertEqual(provider.calls, 2)  # content + difficulty, exactly once total
        self.assertEqual(first.classification.id, second.classification.id)

    # -- 17/22. batch classification -----------------------------------------
    async def test_batch_classify_multiple_questions(self):
        async with self.factory() as s:
            vids = [await self._make_question_version(_DILUTION_STATEMENT) for _ in range(3)]
            svc = AuthorialQuestionClassificationService(s)
            provider = ScriptedProvider([dict(_DILUTION_RESPONSE) for _ in range(3)])
            result = await svc.batch_classify(vids, provider, actor="prof_a")
        self.assertEqual(result.questions_processed, 3)
        self.assertEqual(result.classified, 3)
        self.assertEqual(result.cache_misses, 3)
        self.assertEqual(len(result.outcomes), 3)

    # -- 18. no N+1 as batch size grows ---------------------------------------
    async def test_batch_classify_query_growth_is_linear_not_quadratic(self):
        counts = {}
        for n in (2, 10):
            async with self.factory() as s:
                vids = [await self._make_question_version(_DILUTION_STATEMENT) for _ in range(n)]
                svc = AuthorialQuestionClassificationService(s)
                provider = ScriptedProvider([dict(_DILUTION_RESPONSE) for _ in range(n)])
                q = {"c": 0}

                @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
                def _c(*_a):  # noqa: ANN001
                    q["c"] += 1
                try:
                    await svc.batch_classify(vids, provider, actor="prof_a")
                finally:
                    event.remove(self.engine.sync_engine, "before_cursor_execute", _c)
                counts[n] = q["c"]
        # linear scaling: 10 questions should cost roughly 5x 2 questions'
        # queries, never anywhere near 25x (quadratic).
        self.assertLess(counts[10], counts[2] * 8, f"query counts: {counts}")

    # -- N+1 audit (2026-09): a batch re-run over already-classified
    #    questions (exactly what scripts/classify_remaining_questions.py
    #    does on every restart - "already-classified questions are skipped
    #    automatically by the service's own cache check ... safe to re-run")
    #    used to cost one SELECT per question just to discover it was a
    #    cache hit, instead of one SELECT for the whole batch.
    async def test_batch_classify_cache_hits_use_one_query_not_one_per_question(self):
        counts = {}
        for n in (2, 10):
            async with self.factory() as s:
                vids = [await self._make_question_version(_DILUTION_STATEMENT) for _ in range(n)]
                svc = AuthorialQuestionClassificationService(s)
                # pre-classify every question so the instrumented batch run
                # below is a pure cache-hit re-run.
                seed_provider = ScriptedProvider([dict(_DILUTION_RESPONSE) for _ in range(n)])
                for vid in vids:
                    await svc.classify_question_version(vid, seed_provider, actor="prof_a")

                q = {"c": 0}

                @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
                def _c(*_a):  # noqa: ANN001
                    q["c"] += 1
                try:
                    result = await svc.batch_classify(vids, ScriptedProvider([]), actor="prof_a")
                finally:
                    event.remove(self.engine.sync_engine, "before_cursor_execute", _c)
                self.assertEqual(result.cache_hits, n)
                self.assertEqual(result.ai_calls, 0)  # never re-calls the AI for a cache hit
                counts[n] = q["c"]
        # a real N+1 re-issues the cache-check SELECT once per question (10
        # would cost ~5x the queries of 2); a batched cache check costs the
        # SAME single query regardless of how many questions are in the batch.
        self.assertEqual(counts[10], counts[2], f"query counts: {counts}")

    # -- 19/20/21. tenant isolation + professor/coordinator scope ------------
    async def test_tenant_isolation_review_queue(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT, school_id=_SCHOOL_A)
            svc = AuthorialQuestionClassificationService(s)
            bad = {**_DILUTION_RESPONSE, "confidence": "LOW"}
            await svc.classify_question_version(vid, ScriptedProvider([bad]), actor="prof_a")
            own_school = await svc.list_needs_review(school_id=_SCHOOL_A)
            other_school = await svc.list_needs_review(school_id=_SCHOOL_B)
        self.assertEqual(len(own_school), 1)
        self.assertEqual(other_school, [])

    async def test_teacher_and_coordinator_can_both_act_within_scope(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT, school_id=_SCHOOL_A)
            svc = AuthorialQuestionClassificationService(s)
            by_teacher = await svc.manual_classify(
                vid, discipline_code="CHEMISTRY", area_code="CHEMISTRY-PHYSICAL",
                content_code="CHEMISTRY-SOLUTIONS", subcontent_code="CHEMISTRY-SOLUTIONS-DILUTION",
                difficulty="EASY", reason="revisao do professor", actor="prof_a", actor_type="TEACHER",
            )
            approved_by_coordinator = await svc.approve_classification(
                by_teacher.id, actor="coord_a", actor_type="COORDINATOR", reason="ok")
        self.assertEqual(approved_by_coordinator.metadata_.get("approved_by"), "coord_a")

    # -- 22/23. discursiva / dependência visual -------------------------------
    async def test_discursive_question_without_options_can_be_classified(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT, options=[])
            svc = AuthorialQuestionClassificationService(s)
            outcome = await svc.classify_question_version(vid, ScriptedProvider([dict(_DILUTION_RESPONSE)]), actor="prof_a")
        self.assertEqual(outcome.status, "CLASSIFIED")

    async def test_visual_dependency_forces_needs_review(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            visual = {**_DILUTION_RESPONSE, "visual_dependency": True, "status": "NEEDS_REVIEW",
                     "review_reason": "VISUAL_DEPENDENCY"}
            outcome = await svc.classify_question_version(vid, ScriptedProvider([visual]), actor="prof_a")
        self.assertEqual(outcome.status, "NEEDS_REVIEW")
        self.assertEqual(outcome.review_reason, "VISUAL_DEPENDENCY")

    # -- 25/26/27 (provider failure modes) -----------------------------------
    async def test_provider_timeout_never_crashes_and_routes_to_needs_review(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            provider = ScriptedProvider([ProviderTimeoutError("timed out")])
            outcome = await svc.classify_question_version(vid, provider, actor="prof_a")
        self.assertEqual(outcome.status, "NEEDS_REVIEW")
        self.assertEqual(outcome.review_reason, "CLASSIFIER_UNAVAILABLE")
        self.assertIsNotNone(outcome.error)

    async def test_provider_malformed_json_never_crashes(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            provider = ScriptedProvider(["not valid json at all {{{"])
            outcome = await svc.classify_question_version(vid, provider, actor="prof_a")
        self.assertEqual(outcome.status, "NEEDS_REVIEW")
        self.assertEqual(outcome.review_reason, "INVALID_AI_OUTPUT")

    async def test_provider_unavailable_never_crashes(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            provider = ScriptedProvider([ProviderUnavailableError("down")])
            outcome = await svc.classify_question_version(vid, provider, actor="prof_a")
        self.assertEqual(outcome.status, "NEEDS_REVIEW")
        self.assertEqual(outcome.review_reason, "CLASSIFIER_UNAVAILABLE")

    # -- 28/29. classifier_version / curriculum_version ----------------------
    async def test_classifier_and_curriculum_version_are_recorded(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            outcome = await svc.classify_question_version(vid, ScriptedProvider([dict(_DILUTION_RESPONSE)]), actor="prof_a")
        self.assertEqual(outcome.classification.model_version, CLASSIFIER_VERSION)
        self.assertEqual((outcome.classification.metadata_ or {}).get("taxonomy_version"), TAXONOMY_VERSION)

    # -- 30. official questions untouched ------------------------------------
    async def test_official_question_bank_rows_are_never_touched(self):
        async with self.factory() as s:
            before = len((await s.execute(select(Question))).scalars().all())
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            await svc.classify_question_version(vid, ScriptedProvider([dict(_DILUTION_RESPONSE)]), actor="prof_a")
        async with self.factory() as s:
            after = len((await s.execute(select(Question))).scalars().all())
        self.assertEqual(after, before + 1)  # only the ONE question this test itself created

    # -- 32. deterministic validation -----------------------------------------
    async def test_same_ai_output_validates_identically_twice(self):
        from agente_ia_edu.services.curriculum_classification import ClassificationProposalService
        from agente_ia_edu.db.models import CatalogNode
        async with self.factory() as s:
            catalog = list((await s.scalars(select(CatalogNode).where(CatalogNode.active.is_(True)))).all())
            code_map = {n.code: n for n in catalog}
            path = {"discipline_code": "CHEMISTRY", "area_code": "CHEMISTRY-PHYSICAL",
                   "content_code": "CHEMISTRY-SOLUTIONS", "subcontent_code": "CHEMISTRY-SOLUTIONS-DILUTION"}
            result1 = ClassificationProposalService._valid_path(path, code_map)
            result2 = ClassificationProposalService._valid_path(path, code_map)
        self.assertTrue(result1)
        self.assertEqual(result1, result2)

    # -- 33. difficulty is independent and confidence-tracked ----------------
    async def test_difficulty_confidence_recorded_independently_of_content_confidence(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            provider = ScriptedProvider([dict(_DILUTION_RESPONSE)], difficulty_queue=[{"difficulty": "HARD", "confidence": 0.65, "reasoning": "multiplas etapas de calculo"}])
            outcome = await svc.classify_question_version(vid, provider, actor="prof_a")
        self.assertEqual(outcome.classification.difficulty, "HARD")
        self.assertEqual(float(outcome.classification.difficulty_confidence), 0.65)
        self.assertNotEqual(outcome.classification.difficulty_confidence, outcome.classification.classification_confidence)

    async def test_difficulty_never_uses_statement_length_as_sole_proxy(self):
        # a long AND a short statement can both plausibly be MEDIUM/HARD -
        # this test only asserts the service never derives difficulty from
        # len(statement) itself (it always defers to the AI's own judgement
        # call, a SEPARATE prompt from content classification).
        import inspect
        from agente_ia_edu.services import authorial_question_classification_service as mod
        source = inspect.getsource(mod._build_difficulty_prompt)
        self.assertIn("NÃO use o tamanho do enunciado", source)

    async def test_unparseable_difficulty_response_falls_back_to_unknown_and_needs_review(self):
        async with self.factory() as s:
            vid = await self._make_question_version(_DILUTION_STATEMENT)
            svc = AuthorialQuestionClassificationService(s)
            provider = ScriptedProvider([dict(_DILUTION_RESPONSE)], difficulty_queue=["not json"])
            outcome = await svc.classify_question_version(vid, provider, actor="prof_a")
        self.assertEqual(outcome.classification.difficulty, "UNKNOWN")
        self.assertIsNone(outcome.classification.difficulty_confidence)
        self.assertEqual(outcome.status, "NEEDS_REVIEW")
        self.assertEqual(outcome.review_reason, "DIFFICULTY_UNCERTAIN")


class AIGuardTests(unittest.TestCase):
    _FORBIDDEN = {"openai", "AsyncOpenAI", "OpenAIProvider"}

    def _assert_clean(self, path: Path) -> None:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], self._FORBIDDEN, f"{path}: {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0], self._FORBIDDEN, f"{path}: {node.module}")

    def test_new_phase30_modules_have_no_vendor_specific_ai_imports(self):
        root = Path(__file__).resolve().parents[1] / "src" / "agente_ia_edu"
        self._assert_clean(root / "services" / "authorial_question_classification_service.py")
        self._assert_clean(root / "services" / "authorial_classification_policy.py")
        self._assert_clean(root / "api" / "routes" / "question_classification.py")

    def test_no_ai_import_in_unrelated_platform_modules(self):
        """spec s42 - AI stays inside the classifier; player/activity/result/
        domain-map/learning-path/study-session/material-player/question-
        extraction must never import classification AI machinery."""
        root = Path(__file__).resolve().parents[1] / "src" / "agente_ia_edu"
        forbidden_modules = {
            "curriculum_classification", "classification_consensus",
            "ai_classification_service", "classification_prompts",
            "authorial_question_classification_service",
        }
        guarded_files = [
            root / "services" / "question_extraction_service.py",
            root / "services" / "question_publication_service.py",
            root / "services" / "attempt_execution.py",
            root / "services" / "learning_path.py",
        ]
        for path in guarded_files:
            if not path.exists():
                continue
            tree = ast.parse(path.read_text(), filename=str(path))
            for node in ast.walk(tree):
                if isinstance(node, ast.ImportFrom) and node.module:
                    self.assertNotIn(
                        node.module.split(".")[-1], forbidden_modules,
                        f"{path} must never import classification AI machinery ({node.module})")


if __name__ == "__main__":
    unittest.main()
