"""Service-level coverage for src/agente_ia_edu/services/adaptive_learning_path.py
(AdaptiveLearningPathService).

Wave 7 of the overnight bug-hunting campaign. tests/test_phase21_adaptive_learning_path.py
already gives this module deep HTTP-level coverage (44-question PLAN, full
attempt/correct flow) but leaves several branches untouched: the pure
``_parse_dt`` helper's edge cases, the never-called-from-any-route
``resolve_prerequisites`` method, ``get_content_recommendation``'s
"found in steps" and "not found" branches (only its "found in mastered"
branch is exercised elsewhere), ``manager_view``'s AssignmentNotFound and
student_external_id-filter branches, ``_authz_self``'s forbidden branch,
the malformed-prerequisite-row drop in ``_prereq_graph``, the diamond-shaped
dedup in ``_transitive_prereqs``, and three ``_reason`` text branches
(NEEDS_REVIEW / RECOMMENDED-with-forced-closure / READY-without-blockers).

Rather than re-running the full question/answer-key/correction pipeline for
every scenario, most tests here insert ``DomainContentMastery`` rows
directly - CurriculumDomainMapService.get_map() trusts that PHASE 20 cache
table as-is when rows already exist for the student (it only re-aggregates
from ActivityResult when the cache is empty), so this gives precise, fast
control over accuracy/answered-count/last_activity_at/forced-closure-count
without needing questions, an answer key, or an activity attempt at all.

Uses a session_factory with the production expire_on_commit=True default
throughout.
"""

from __future__ import annotations

import unittest
import uuid as _uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.admin import UserSchoolLink
from agente_ia_edu.db.models.assessments import (
    ActivityAssignment,
    ActivityAttempt,
    ActivityResult,
    Assessment,
    AssessmentVersion,
    DomainContentMastery,
)
from agente_ia_edu.db.models.catalog import CatalogNode, CatalogNodePrerequisite
from agente_ia_edu.db.models.recommendations import PedagogicalContext
from agente_ia_edu.services.adaptive_learning_path import (
    AdaptiveLearningPathService,
    LearningPathAuthError,
    LearningPathNotFound,
    _parse_dt,
)
from agente_ia_edu.services.question_list_store import Requester


class ParseDtPureFunctionTests(unittest.TestCase):
    """`_parse_dt` is a pure module-level helper - no DB needed."""

    def test_falsy_input_returns_none(self):
        self.assertIsNone(_parse_dt(None))
        self.assertIsNone(_parse_dt(""))

    def test_aware_datetime_passthrough(self):
        aware = datetime(2020, 1, 1, tzinfo=timezone.utc)
        self.assertEqual(_parse_dt(aware), aware)

    def test_naive_datetime_gets_utc_attached(self):
        naive = datetime(2020, 1, 1)
        got = _parse_dt(naive)
        self.assertEqual(got.tzinfo, timezone.utc)
        self.assertEqual(got.replace(tzinfo=None), naive)

    def test_unparseable_string_returns_none(self):
        self.assertIsNone(_parse_dt("not-a-date"))

    def test_iso_string_with_z_suffix_parses_as_utc(self):
        got = _parse_dt("2020-06-15T10:00:00Z")
        self.assertEqual(got, datetime(2020, 6, 15, 10, 0, 0, tzinfo=timezone.utc))


class AdaptiveLearningPathServiceCoverage(unittest.IsolatedAsyncioTestCase):
    """K_A <- K_B <- (nothing); K_B, K_C both <- K_A (diamond via K_D):

        K_D <- K_B <- K_A
        K_D <- K_C <- K_A

    plus a malformed self-referencing prerequisite row on K_A (content_node_id
    == prerequisite_node_id) that ``_prereq_graph`` must silently drop, and
    four independent, prereq-free contents (K_STALE / K_FC / K_READY / K_CTX)
    used to hit the remaining ``_build`` / ``_reason`` state branches.
    """

    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        # expire_on_commit=True mirrors production's create_session_factory().
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession)
        await self._seed_catalog()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_catalog(self):
        async with self.session_factory() as s:
            d1 = CatalogNode(code="MATH", name="Matematica", node_type="DISCIPLINE",
                             position=0, active=True)
            s.add(d1)
            await s.flush()
            d1.root_id = d1.id
            await s.flush()
            a1 = CatalogNode(code="MATH-A", name="Area M", node_type="AREA", position=0,
                             parent_id=d1.id, root_id=d1.id, active=True)
            s.add(a1)
            await s.flush()

            def content(code, name, pos):
                n = CatalogNode(code=code, name=name, node_type="CONTENT", position=pos,
                                parent_id=a1.id, root_id=d1.id, active=True)
                s.add(n)
                return n

            k_a = content("K_A", "Conteudo A", 1)
            k_b = content("K_B", "Conteudo B", 2)
            k_c = content("K_C", "Conteudo C", 3)
            k_d = content("K_D", "Conteudo D", 4)
            k_stale = content("K_STALE", "Conteudo Estagnado", 5)
            k_fc = content("K_FC", "Conteudo Fechamento Forcado", 6)
            k_ready = content("K_READY", "Conteudo Pronto", 7)
            k_ctx = content("K_CTX", "Conteudo Contexto", 8)
            # a structural node with NO code (legal per the schema - `code` is
            # nullable; e.g. an intermediate grouping node never meant to be
            # addressed directly). _prereq_graph()'s id_to_code lookup is built
            # from `_catalog_cache`, which is keyed by code and filters out any
            # node `if not node.code` - so a prerequisite row that references
            # THIS node's id can never resolve to a code.
            ghost = CatalogNode(code=None, name="No-code structural node",
                                node_type="SECTION", position=99, parent_id=a1.id,
                                root_id=d1.id, active=True)
            s.add(ghost)
            await s.flush()

            # real prerequisite edges: K_B->K_A, K_C->K_A, K_D->K_B, K_D->K_C
            for cn, pn in ((k_b, k_a), (k_c, k_a), (k_d, k_b), (k_d, k_c)):
                s.add(CatalogNodePrerequisite(content_node_id=cn.id, prerequisite_node_id=pn.id))
            # malformed row: the DB forbids a literal self-reference
            # (ck_catalog_node_prerequisite_not_self), so the realistic way
            # `_prereq_graph()`'s "invalid reference -> drop" branch gets hit
            # in production is a prerequisite pointing at a node with no
            # catalog code - id_to_code.get(...) misses it, `pc` is None, and
            # the row must be silently dropped rather than crash or corrupt
            # the graph with a None key.
            s.add(CatalogNodePrerequisite(content_node_id=k_a.id, prerequisite_node_id=ghost.id))
            await s.flush()

            # capture ids BEFORE commit: commit() expires every ORM instance
            # in this session (expire_on_commit=True, the production
            # default), so reading k_a.id etc. afterwards would need a fresh
            # lazy-load with no async context available -> MissingGreenlet.
            self.node_id = {
                "K_A": k_a.id, "K_B": k_b.id, "K_C": k_c.id, "K_D": k_d.id,
                "K_STALE": k_stale.id, "K_FC": k_fc.id, "K_READY": k_ready.id,
                "K_CTX": k_ctx.id,
            }
            await s.commit()

    async def _seed_dcm(self, student, content_code, *, answered, correct,
                        evidence_state, last_activity_at=None,
                        forced_closure=0, provisional=0):
        async with self.session_factory() as s:
            s.add(DomainContentMastery(
                student_external_id=student, taxonomy_version="curriculum-v2",
                content_code=content_code, subcontent_code=None,
                questions_seen=answered, questions_answered=answered,
                questions_correct=correct, questions_incorrect=answered - correct,
                accuracy=(correct / answered) if answered else None,
                evidence_count=answered, evidence_state=evidence_state,
                definitive_evidence_count=answered - provisional,
                provisional_evidence_count=provisional,
                forced_closure_evidence_count=forced_closure,
                visual_dependency_evidence_count=0, origin_breakdown={},
                first_activity_at=last_activity_at, last_activity_at=last_activity_at,
                last_evaluated_at=datetime.now(timezone.utc),
            ))
            await s.commit()

    def _step(self, path, code):
        return next((s for s in path["steps"] if s["content_code"] == code), None)

    # ---- _authz_self forbidden branch (build_path 403) ------------------

    async def test_build_path_forbidden_for_other_student(self):
        async with self.session_factory() as s:
            svc = AdaptiveLearningPathService(s)
            other = Requester(external_user_id="not-the-student", school_id=None,
                              role="TEACHER", is_platform_admin=False)
            with self.assertRaises(LearningPathAuthError):
                await svc.build_path("some_student", requester=other)

    # ---- _prereq_graph: malformed (no-code-target) row is dropped ------

    async def test_prereq_graph_drops_row_referencing_a_codeless_node(self):
        async with self.session_factory() as s:
            svc = AdaptiveLearningPathService(s)
            graph, names, positions, dependents = await svc._prereq_graph()
            # K_A's ONLY prerequisite row points at the code-less "ghost" node
            # -> dropped (pc resolves to None), so K_A must not appear as a
            # key in graph at all.
            self.assertNotIn("K_A", graph)
            # real edges survive untouched
            self.assertEqual(graph["K_B"], ["K_A"])
            self.assertEqual(graph["K_C"], ["K_A"])
            self.assertEqual(sorted(graph["K_D"]), ["K_B", "K_C"])

    # ---- _transitive_prereqs: diamond-shaped dedup ----------------------

    async def test_transitive_prereqs_dedups_diamond_shared_ancestor(self):
        async with self.session_factory() as s:
            svc = AdaptiveLearningPathService(s)
            graph, *_ = await svc._prereq_graph()
            result = AdaptiveLearningPathService._transitive_prereqs("K_D", graph)
            # K_A is reachable from K_D via BOTH K_B and K_C - must be deduped
            # to a single entry (a set), not double-counted or infinite-looped.
            self.assertEqual(result, {"K_A", "K_B", "K_C"})

    # ---- resolve_prerequisites: full transitive chain, catalog-only ----

    async def test_resolve_prerequisites_returns_transitive_chain_sorted_by_position(self):
        async with self.session_factory() as s:
            svc = AdaptiveLearningPathService(s)
            req = Requester(external_user_id="prof_x", school_id=None, role="TEACHER")
            result = await svc.resolve_prerequisites("K_D", requester=req)
            self.assertEqual(result["content_code"], "K_D")
            codes = [p["code"] for p in result["prerequisites"]]
            # deduped (K_A appears once despite two paths) and ordered by
            # catalog position: K_A(1) < K_B(2) < K_C(3)
            self.assertEqual(codes, ["K_A", "K_B", "K_C"])
            names = {p["code"]: p["name"] for p in result["prerequisites"]}
            self.assertEqual(names["K_A"], "Conteudo A")

    async def test_resolve_prerequisites_leaf_content_has_no_prerequisites(self):
        async with self.session_factory() as s:
            svc = AdaptiveLearningPathService(s)
            req = Requester(external_user_id="prof_x", school_id=None, role="TEACHER")
            result = await svc.resolve_prerequisites("K_A", requester=req)
            self.assertEqual(result["prerequisites"], [])

    # ---- manager_view: AssignmentNotFound + student_external_id filter -

    async def test_manager_view_unknown_assignment_raises_not_found(self):
        async with self.session_factory() as s:
            svc = AdaptiveLearningPathService(s)
            req = Requester(external_user_id="prof_x", school_id=None, role="TEACHER")
            with self.assertRaises(LearningPathNotFound):
                await svc.manager_view(_uuid.uuid4(), requester=req)

    async def _seed_assignment_with_results(self, owner, students):
        async with self.session_factory() as s:
            assessment = Assessment(owner_external_id=owner, school_id=None, title="T1",
                                    status="published")
            s.add(assessment)
            await s.flush()
            version = AssessmentVersion(assessment_id=assessment.id, version_number=1,
                                        title="V1", status="published")
            s.add(version)
            await s.flush()
            assignment = ActivityAssignment(
                assessment_id=assessment.id, assessment_version_id=version.id, school_id=None,
                target_type="CLASS", target_id="CLASS-1", status="ACTIVE", question_count=0,
            )
            s.add(assignment)
            await s.flush()
            assignment_id = assignment.id
            for student in students:
                attempt = ActivityAttempt(assignment_id=assignment_id, student_external_id=student,
                                          status="COMPLETED")
                s.add(attempt)
                await s.flush()
                s.add(ActivityResult(
                    attempt_id=attempt.id, assignment_id=assignment_id,
                    student_external_id=student, assessment_version_id=version.id,
                    question_count=0, answered_count=0, correct_count=0,
                    incorrect_count=0, unanswered_count=0, completion_status="COMPLETED",
                ))
            await s.commit()
            return assignment_id

    async def test_manager_view_filters_to_one_student(self):
        assignment_id = await self._seed_assignment_with_results(
            "prof_owner", ["stu_mv_a", "stu_mv_b"])
        async with self.session_factory() as s:
            svc = AdaptiveLearningPathService(s)
            req = Requester(external_user_id="prof_owner", school_id=None, role="TEACHER")
            result = await svc.manager_view(assignment_id, requester=req,
                                            student_external_id="stu_mv_a")
            self.assertEqual(result["student_count"], 1)
            self.assertEqual(result["students"][0]["student_external_id"], "stu_mv_a")

    async def test_manager_view_no_filter_returns_every_student(self):
        assignment_id = await self._seed_assignment_with_results(
            "prof_owner2", ["stu_mv_c", "stu_mv_d"])
        async with self.session_factory() as s:
            svc = AdaptiveLearningPathService(s)
            req = Requester(external_user_id="prof_owner2", school_id=None, role="TEACHER")
            result = await svc.manager_view(assignment_id, requester=req)
            self.assertEqual(result["student_count"], 2)
            self.assertEqual({p["student_external_id"] for p in result["students"]},
                             {"stu_mv_c", "stu_mv_d"})

    # ---- get_content_recommendation: step-match + not-found -------------

    async def test_get_content_recommendation_matches_a_step_not_just_mastered(self):
        await self._seed_dcm("stu_rec", "K_READY", answered=5, correct=3,
                             evidence_state="OBSERVED",
                             last_activity_at=datetime.now(timezone.utc))
        async with self.session_factory() as s:
            svc = AdaptiveLearningPathService(s)
            req = Requester(external_user_id="stu_rec", school_id=None, role="STUDENT")
            rec = await svc.get_content_recommendation("stu_rec", "K_READY", requester=req)
            self.assertEqual(rec["recommendation"]["content_code"], "K_READY")
            self.assertNotEqual(rec["recommendation"]["content_state"], "MASTERED")

    async def test_get_content_recommendation_unknown_code_raises_not_found(self):
        await self._seed_dcm("stu_rec2", "K_READY", answered=5, correct=3,
                             evidence_state="OBSERVED",
                             last_activity_at=datetime.now(timezone.utc))
        async with self.session_factory() as s:
            svc = AdaptiveLearningPathService(s)
            req = Requester(external_user_id="stu_rec2", school_id=None, role="STUDENT")
            with self.assertRaises(LearningPathNotFound):
                await svc.get_content_recommendation("stu_rec2", "NO_SUCH_CODE", requester=req)

    # ---- _build / _reason: NEEDS_REVIEW (stale mastered) ----------------

    async def test_mastered_but_stale_content_is_needs_review(self):
        stale_at = datetime.now(timezone.utc) - timedelta(days=60)
        await self._seed_dcm("stu_stale", "K_STALE", answered=5, correct=5,
                             evidence_state="OBSERVED", last_activity_at=stale_at)
        async with self.session_factory() as s:
            svc = AdaptiveLearningPathService(s)
            req = Requester(external_user_id="stu_stale", school_id=None, role="STUDENT")
            path = await svc.build_path("stu_stale", requester=req)
            step = self._step(path, "K_STALE")
            self.assertIsNotNone(step)
            self.assertEqual(step["content_state"], "NEEDS_REVIEW")
            self.assertEqual(step["action_type"], "REVIEW")
            self.assertIn("Revisão recomendada", step["priority_reason"])

    # ---- _build / _reason: RECOMMENDED with forced-closure note --------

    async def test_weak_unblocked_content_with_forced_closure_note(self):
        await self._seed_dcm("stu_fc", "K_FC", answered=5, correct=1,
                             evidence_state="OBSERVED",
                             last_activity_at=datetime.now(timezone.utc),
                             forced_closure=2)
        async with self.session_factory() as s:
            svc = AdaptiveLearningPathService(s)
            req = Requester(external_user_id="stu_fc", school_id=None, role="STUDENT")
            path = await svc.build_path("stu_fc", requester=req)
            step = self._step(path, "K_FC")
            self.assertIsNotNone(step)
            self.assertEqual(step["content_state"], "RECOMMENDED")
            self.assertEqual(step["action_type"], "PRACTICE")
            self.assertIn("encerramento forçado", step["priority_reason"])

    # ---- _build / _reason: READY without blockers ------------------------

    async def test_decent_unblocked_content_without_dependents_is_ready(self):
        await self._seed_dcm("stu_ready", "K_READY", answered=5, correct=3,  # 0.6 accuracy
                             evidence_state="OBSERVED",
                             last_activity_at=datetime.now(timezone.utc))
        async with self.session_factory() as s:
            svc = AdaptiveLearningPathService(s)
            req = Requester(external_user_id="stu_ready", school_id=None, role="STUDENT")
            path = await svc.build_path("stu_ready", requester=req)
            step = self._step(path, "K_READY")
            self.assertIsNotNone(step)
            self.assertEqual(step["content_state"], "READY")
            self.assertEqual(step["priority_factors"]["weak_performance"], 0)
            self.assertEqual(step["blocks_contents"], [])
            self.assertIn("Pronto para praticar mais", step["priority_reason"])

    # ---- _build / _reason: INSUFFICIENT_EVIDENCE + school context ------

    async def test_insufficient_evidence_content_in_school_context(self):
        await self._seed_dcm("stu_ctx", "K_CTX", answered=1, correct=0,
                             evidence_state="INSUFFICIENT_EVIDENCE",
                             last_activity_at=datetime.now(timezone.utc))
        async with self.session_factory() as s:
            s.add(UserSchoolLink(external_user_id="stu_ctx", school_id=_uuid.uuid4(),
                                 role="STUDENT", scope_type="CLASSROOM",
                                 scope_external_id="CLASS-CTX", active=True))
            s.add(PedagogicalContext(content_node_id=self.node_id["K_CTX"], source="TEACHER",
                                     classroom_id=None, active=True))
            await s.commit()

        async with self.session_factory() as s:
            svc = AdaptiveLearningPathService(s)
            req = Requester(external_user_id="stu_ctx", school_id=None, role="STUDENT")
            path = await svc.build_path("stu_ctx", requester=req)
            step = self._step(path, "K_CTX")
            self.assertIsNotNone(step)
            self.assertEqual(step["content_state"], "INSUFFICIENT_EVIDENCE")
            self.assertIn("TEACHER", step["school_context_sources"])
            self.assertIn("Conteúdo em foco pela turma", step["priority_reason"])


if __name__ == "__main__":
    unittest.main()
