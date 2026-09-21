"""PHASE 21 - Adaptive Learning Path (deterministic decision layer, DERIVED).

    correction  = ActivityResult + ActivityResultItem        (PHASE 18, immutable)
    analysis    = PedagogicalAnalysisService                 (PHASE 19, READ-ONLY)
    domain map  = domain_content_mastery                     (PHASE 20, DERIVED cache)
    learning path = this service                             (this phase, DERIVED VIEW)
    authored material / practice generation / videos / LLM   = FUTURE - NOT here

"O que este aluno deve estudar agora, considerando o que ele já demonstrou saber
e os pré-requisitos necessários?"

This is a pure decision layer. It NEVER writes anything (no table, no migration),
never re-runs correction, never reads the official answer key, never mutates the
Domain Map or the catalog. It consumes the PHASE 20 curriculum-v2 Domain Map +
``catalog_node_prerequisites`` + curriculum-v2 + (when it exists) the pedagogical
context tables, and produces an explainable, deterministically ordered list of
"next best pedagogical actions".

It is NOT "the list of contents with the lowest accuracy": a 0%/1-question
content is not automatically top priority, and an 80% content that is a
prerequisite for two not-yet-mastered contents can be. Every recommendation
carries a ``priority_reason`` and the raw ``priority_factors`` behind it. The
weights live in one configurable ``LearningPathWeights`` - no numbers scattered
through the code. MIN_SAMPLE_SIZE / mastery threshold are reused from
``PerformanceThresholdPolicy``.

AI-agnostic: imports no provider / OpenAI SDK. A future
``LearningPathExplanationProvider`` may enrich the prose, but the decision is the
system's.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models.admin import UserSchoolLink
from agente_ia_edu.db.models.catalog import CatalogNode, CatalogNodePrerequisite
from agente_ia_edu.services.activity_assignment_store import (
    ActivityAssignmentStore,
    AssignmentAuthError,
    AssignmentNotFound,
)
from agente_ia_edu.services.curriculum_domain_map import (
    CurriculumDomainMapService,
    STATE_INSUFFICIENT,
    STATE_OBSERVED,
    TAXONOMY_VERSION,
)
from agente_ia_edu.services.material_availability import MaterialAvailabilityService
from agente_ia_edu.services.pedagogical_analysis import PerformanceThresholdPolicy
from agente_ia_edu.services.question_bank import QuestionBankService
from agente_ia_edu.services.question_list_store import (
    ListAuthorizationError,
    Requester,
)

# ---- path / content states -------------------------------------------------
PATH_READY = "READY"
PATH_NO_EVIDENCE = "NO_EVIDENCE"
PATH_GRAPH_INVALID = "PREREQUISITE_GRAPH_INVALID"

C_MASTERED = "MASTERED"
C_BLOCKED = "BLOCKED_BY_PREREQUISITE"
C_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
C_NEEDS_REVIEW = "NEEDS_REVIEW"
C_RECOMMENDED = "RECOMMENDED"
C_READY = "READY"

# ---- pedagogical actions (none are wired downstream yet - action_available=False)
ACTION_STUDY = "STUDY"
ACTION_PRACTICE = "PRACTICE"
ACTION_REVIEW = "REVIEW"
ACTION_DIAGNOSE = "DIAGNOSE"
ACTION_NONE = "NONE"

_PROVISIONAL_NOTE = ("Parte das questões usadas nesta análise possui classificação pedagógica "
                     "provisória.")
_TRANSPARENCY = ("Esta trilha é baseada no seu desempenho e nos pré-requisitos dos conteúdos. "
                 "É uma orientação de estudo, não uma nota.")


class LearningPathError(ValueError):
    """422."""


class LearningPathNotFound(LookupError):
    """404."""


class LearningPathAuthError(PermissionError):
    """403."""


class PrerequisiteGraphInvalid(RuntimeError):
    """409 - a cycle / invalid reference in catalog_node_prerequisites."""

    def __init__(self, message: str, *, cycle: list[str] | None = None) -> None:
        super().__init__(message)
        self.cycle = cycle or []


@dataclass(frozen=True)
class LearningPathWeights:
    """Single, documented, configurable source of the ordering factors.

    priority_score =
        w_low_evidence   * low_evidence_factor
      + w_weak           * weak_performance_factor * (1 - 0.5*provisional_ratio)
      + w_blocker        * blocker_factor
      + w_context        * context_factor
      + w_stale_review   * stale_review_factor
      - w_blocked_penalty* blocked_penalty_factor
    clamped to [0, 1].
    """

    w_low_evidence: float = 0.35
    w_weak: float = 0.30
    w_blocker: float = 0.25
    w_context: float = 0.15
    w_stale_review: float = 0.10
    w_blocked_penalty: float = 0.50
    stale_days: int = 30
    blocker_saturation: int = 3          # this many unmastered dependents -> full blocker weight

    @classmethod
    def default(cls) -> "LearningPathWeights":
        return cls()

    def as_dict(self) -> dict:
        return {
            "W_LOW_EVIDENCE": self.w_low_evidence, "W_WEAK_PERFORMANCE": self.w_weak,
            "W_BLOCKER": self.w_blocker, "W_CONTEXT": self.w_context,
            "W_STALE_REVIEW": self.w_stale_review, "W_BLOCKED_PENALTY": self.w_blocked_penalty,
            "STALE_DAYS": self.stale_days, "BLOCKER_SATURATION": self.blocker_saturation,
        }


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _parse_dt(v) -> datetime | None:
    if not v:
        return None
    if isinstance(v, datetime):
        return v if v.tzinfo else v.replace(tzinfo=timezone.utc)
    try:
        dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
    except ValueError:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


class AdaptiveLearningPathService:
    """Deterministic. AI-agnostic. Idempotent (writes nothing). No N+1."""

    def __init__(self, session: AsyncSession,
                 policy: PerformanceThresholdPolicy | None = None,
                 weights: LearningPathWeights | None = None) -> None:
        self._session = session
        self._domain = CurriculumDomainMapService(session, policy=policy)
        self._bank = QuestionBankService(session)
        self._material = MaterialAvailabilityService(session)
        self.policy = policy or PerformanceThresholdPolicy.default()
        self.weights = weights or LearningPathWeights.default()
        # perf: the prerequisite graph is catalog-only (never student-specific),
        # so it is safe to compute once per service instance and reuse across
        # every `_build()` call made through it. Without this, `manager_view`
        # re-runs the same `catalog_node_prerequisites` query once per student
        # in the assignment (measured: 2 students -> 2 queries, 6 -> 6) even
        # though the graph never changes within one request.
        self._prereq_graph_cache: tuple | None = None

    # ---- public entry points -----------------------------------------

    async def build_path(self, student_external_id: str, *, requester: Requester,
                         use_context: bool = True) -> dict:
        self._authz_self(student_external_id, requester)
        return await self._build(student_external_id, requester=requester, use_context=use_context)

    async def get_next_actions(self, student_external_id: str, *, requester: Requester,
                               limit: int = 3) -> dict:
        path = await self.build_path(student_external_id, requester=requester)
        path = dict(path)
        path["steps"] = path["steps"][: max(1, min(int(limit), 20))]
        path["is_next_slice"] = True
        return path

    async def get_content_recommendation(self, student_external_id: str, content_code: str, *,
                                         requester: Requester) -> dict:
        path = await self._build(student_external_id, requester=requester, use_context=True)
        for step in path["steps"]:
            if step["content_code"] == content_code:
                return {"student_external_id": student_external_id,
                        "taxonomy_version": TAXONOMY_VERSION,
                        "path_state": path["state"], "recommendation": step,
                        "transparency": _TRANSPARENCY}
        for m in path["mastered"]:
            if m["content_code"] == content_code:
                return {"student_external_id": student_external_id,
                        "taxonomy_version": TAXONOMY_VERSION,
                        "path_state": path["state"], "recommendation": m,
                        "transparency": _TRANSPARENCY}
        raise LearningPathNotFound(content_code)

    async def resolve_prerequisites(self, content_code: str, *, requester: Requester) -> dict:
        # catalog-only (no student data) - the transitive prerequisite chain.
        graph, names, positions, _dep = await self._prereq_graph()
        self._detect_cycle(graph)
        seen: list[str] = []
        stack = list(graph.get(content_code, []))
        visited = set()
        while stack:
            code = stack.pop(0)
            if code in visited:
                continue
            visited.add(code)
            seen.append(code)
            stack.extend(graph.get(code, []))
        seen.sort(key=lambda c: (positions.get(c, 1_000_000), c))
        return {
            "content_code": content_code,
            "taxonomy_version": TAXONOMY_VERSION,
            "prerequisites": [{"code": c, "name": names.get(c)} for c in seen],
        }

    async def manager_view(self, assignment_id: UUID, *, requester: Requester,
                           student_external_id: str | None = None) -> dict:
        """Owner / same-school-privileged view (the exact PHASE 16 assignment
        rule, reused). Returns the learning path of every student with a
        corrected result under this assignment. NO new authz rule; no automatic
        intervention recommendation for the teacher."""
        try:
            await self._domain._assignments.get(assignment_id, requester=requester)
        except AssignmentNotFound as exc:
            raise LearningPathNotFound(str(assignment_id)) from exc
        except (AssignmentAuthError, ListAuthorizationError) as exc:
            raise LearningPathAuthError(str(exc)) from exc
        from agente_ia_edu.db.models.assessments import ActivityResult
        students = list((await self._session.execute(
            select(ActivityResult.student_external_id)
            .where(ActivityResult.assignment_id == assignment_id).distinct()
        )).scalars().all())
        if student_external_id is not None:
            students = [s for s in students if s == student_external_id]
        paths = []
        for sid in sorted(students):
            paths.append(await self._build(sid, requester=requester, use_context=True,
                                           manager=True))
        return {"assignment_id": str(assignment_id), "taxonomy_version": TAXONOMY_VERSION,
                "student_count": len(paths), "students": paths}

    # ---- authz -----------------------------------------------------

    @staticmethod
    def _authz_self(student_external_id: str, requester: Requester) -> None:
        if requester.external_user_id != student_external_id and not requester.is_platform_admin:
            raise LearningPathAuthError("a student can only read their own learning path")

    # ---- prerequisite graph -------------------------------------

    async def _prereq_graph(self):
        """content_code -> [direct prerequisite codes] (+ names, positions, dependents).
        One query over catalog_node_prerequisites, resolved through the cached
        catalog. Invalid references are dropped (documented). Memoized on the
        instance: the graph is catalog-only, never student-specific, so a
        caller that builds several paths through the same service instance
        (e.g. ``manager_view`` looping over students) reuses it instead of
        re-querying once per student."""
        if self._prereq_graph_cache is not None:
            return self._prereq_graph_cache
        await self._bank._load_catalog()
        catalog = self._bank._catalog_cache or {}
        by_id = self._bank._catalog_by_id or {}
        names = {code: node.name for code, node in catalog.items()}
        positions = {code: (node.position or 0) for code, node in catalog.items()}
        id_to_code = {node.id: code for code, node in catalog.items()}
        graph: dict[str, list[str]] = {}
        dependents: dict[str, list[str]] = {}
        rows = (await self._session.execute(
            select(CatalogNodePrerequisite.content_node_id,
                   CatalogNodePrerequisite.prerequisite_node_id)
        )).all()
        for cnid, pnid in rows:
            cc = id_to_code.get(cnid)
            pc = id_to_code.get(pnid)
            if not cc or not pc or cc == pc:          # invalid / self reference -> drop
                continue
            if pc not in graph.setdefault(cc, []):
                graph[cc].append(pc)
            if cc not in dependents.setdefault(pc, []):
                dependents[pc].append(cc)
        for cc in graph:
            graph[cc].sort(key=lambda c: (positions.get(c, 1_000_000), c))
        self._prereq_graph_cache = (graph, names, positions, dependents)
        return self._prereq_graph_cache

    @staticmethod
    def _detect_cycle(graph: dict[str, list[str]]) -> None:
        WHITE, GRAY, BLACK = 0, 1, 2
        color = {n: WHITE for n in graph}
        for n in list(graph):
            color.setdefault(n, WHITE)

        def visit(node: str, trail: list[str]) -> None:
            color[node] = GRAY
            for nxt in graph.get(node, []):
                c = color.get(nxt, WHITE)
                if c == GRAY:
                    raise PrerequisiteGraphInvalid(
                        "catalog_node_prerequisites contém um ciclo",
                        cycle=trail + [node, nxt])
                if c == WHITE:
                    visit(nxt, trail + [node])
            color[node] = BLACK

        for n in list(color):
            if color[n] == WHITE:
                visit(n, [])

    # ---- context (optional) ------------------------------------

    async def _school_context(self, student_external_id: str) -> dict[str, list[str]]:
        """{content_code: [sources]} the student's classroom currently has an
        ACTIVE PedagogicalContext for (TEACHER / COORDINATION / SCHOOL_PLAN).
        Empty for an independent student or when the table is empty - the path
        never requires school context."""
        try:
            from agente_ia_edu.db.models.recommendations import PedagogicalContext
        except Exception:  # pragma: no cover - table not present
            return {}
        links = (await self._session.execute(
            select(UserSchoolLink.scope_external_id).where(
                UserSchoolLink.external_user_id == student_external_id,
                UserSchoolLink.active.is_(True),
                UserSchoolLink.scope_type == "CLASSROOM",
            )
        )).scalars().all()
        classrooms = [c for c in links if c]
        if not classrooms:
            return {}
        await self._bank._load_catalog()
        by_id = self._bank._catalog_by_id or {}
        id_to_code = {node.id: code for code, node in (self._bank._catalog_cache or {}).items()}
        rows = (await self._session.execute(
            select(PedagogicalContext.content_node_id, PedagogicalContext.source)
            .where(PedagogicalContext.active.is_(True),
                   or_(PedagogicalContext.classroom_id.in_(classrooms),
                       PedagogicalContext.classroom_id.is_(None)))
        )).all()
        out: dict[str, list[str]] = {}
        for cnid, source in rows:
            code = id_to_code.get(cnid)
            if code:
                out.setdefault(code, [])
                if source not in out[code]:
                    out[code].append(source)
        return out

    # ---- the deterministic build ------------------------------

    async def _build(self, student_external_id: str, *, requester: Requester,
                     use_context: bool, manager: bool = False) -> dict:
        try:
            domain = await self._domain.get_map(student_external_id, requester=requester)
        except Exception:
            # a manager reading another student's map goes through the assignment
            # gate already; fall back to a self-requester domain read.
            self_req = Requester(external_user_id=student_external_id,
                                 school_id=requester.school_id, role="STUDENT")
            domain = await self._domain.get_map(student_external_id, requester=self_req)

        # flatten the domain map -> {content_code: fields}
        cstate: dict[str, dict] = {}
        for disc in domain["disciplines"]:
            for c in disc["contents"]:
                cstate[c["content_code"]] = {
                    **c,
                    "discipline_code": disc["discipline_code"],
                    "discipline_name": disc["discipline_name"],
                }

        graph, names, positions, dependents = await self._prereq_graph()
        try:
            self._detect_cycle(graph)
        except PrerequisiteGraphInvalid as exc:
            return {
                "student_external_id": student_external_id,
                "taxonomy_version": TAXONOMY_VERSION,
                "generated_at": _iso(_now()),
                "state": PATH_GRAPH_INVALID,
                "detail": {"message": str(exc), "cycle": exc.cycle},
                "thresholds": self.policy.as_dict(),
                "weights": self.weights.as_dict(),
                "transparency": _TRANSPARENCY,
                "steps": [], "mastered": [], "ai_used": False,
            }

        context = await self._school_context(student_external_id) if use_context else {}

        # candidate set: observed contents + their (transitive) prerequisites + context codes
        candidates: set[str] = set(cstate)
        for cc in list(cstate):
            candidates.update(self._transitive_prereqs(cc, graph))
        candidates.update(context)
        candidates = {c for c in candidates if c in names}   # must be a real catalog node

        ms = self.policy.min_sample_size
        strong = self.policy.strong_accuracy
        improvement = self.policy.improvement_accuracy
        stale_before = _now() - timedelta(days=self.weights.stale_days)

        # PHASE 25 - one batched, tenant-aware material lookup for every
        # candidate (never per-content queries). Priority/ordering/action_type
        # are UNCHANGED - this only adds an honest "Estudar agora" signal.
        material_by_code = await self._material.resolve_for_content(
            list(candidates), requester_school_id=requester.school_id)

        def mastered(code: str) -> bool:
            c = cstate.get(code)
            return bool(c and c["questions_answered"] >= ms
                        and c["accuracy"] is not None and c["accuracy"] >= strong)

        steps: list[dict] = []
        mastered_list: list[dict] = []
        for code in candidates:
            c = cstate.get(code) or _zero_content(code, names.get(code))
            answered = c["questions_answered"]
            accuracy = c["accuracy"]
            observed = c["evidence_state"] == STATE_OBSERVED
            fc_count = c.get("forced_closure_evidence_count", 0)
            prov_count = c.get("provisional_evidence_count", 0)
            prov_ratio = round(prov_count / answered, 4) if answered else 0.0

            unsatisfied = [p for p in graph.get(code, []) if not mastered(p)]
            blocked = bool(unsatisfied)
            unmastered_dependents = [d for d in dependents.get(code, [])
                                     if d in candidates and not mastered(d)]
            is_mastered = mastered(code)
            is_stale = bool(is_mastered and _parse_dt(c.get("last_activity_at"))
                            and _parse_dt(c["last_activity_at"]) < stale_before)

            # -- deterministic factors (each 0..1) --
            f_low_evidence = 1.0 if (not observed and not is_mastered) else 0.0
            f_weak = 0.0
            if observed and accuracy is not None and accuracy < improvement:
                f_weak = min(1.0, (improvement - accuracy) / improvement)
            f_blocker = min(1.0, len(unmastered_dependents) / max(1, self.weights.blocker_saturation))
            f_context = 0.5 if code in context else 0.0
            f_stale_review = 1.0 if is_stale else 0.0
            f_blocked_penalty = 1.0 if (blocked and not is_mastered) else 0.0

            score = (
                self.weights.w_low_evidence * f_low_evidence
                + self.weights.w_weak * f_weak * (1 - 0.5 * prov_ratio)
                + self.weights.w_blocker * f_blocker
                + self.weights.w_context * f_context
                + self.weights.w_stale_review * f_stale_review
                - self.weights.w_blocked_penalty * f_blocked_penalty
            )
            score = round(max(0.0, min(1.0, score)), 4)

            if is_mastered and is_stale:
                state, action = C_NEEDS_REVIEW, ACTION_REVIEW
            elif is_mastered:
                state, action = C_MASTERED, ACTION_NONE
            elif blocked:
                state, action = C_BLOCKED, ACTION_STUDY
            elif not observed:
                state, action = C_INSUFFICIENT, ACTION_DIAGNOSE   # diagnostic is a separate flow (s30)
            elif f_weak > 0:
                state, action = C_RECOMMENDED, ACTION_PRACTICE
            else:
                state, action = C_READY, ACTION_PRACTICE
            # PHASE 22 - the practice flow is now REAL for an observed, non-blocked
            # content (RECOMMENDED / READY / NEEDS_REVIEW). DIAGNOSE / STUDY stay
            # unavailable (diagnostic + authored material are later phases).
            practice_available = bool(
                code and not blocked
                and state in (C_RECOMMENDED, C_READY, C_NEEDS_REVIEW))

            reason = self._reason(state, answered, accuracy, unmastered_dependents,
                                  unsatisfied, names, fc_count, is_stale, code in context)
            path = self._domain._bank._resolve_curriculum_path(code)
            entry = {
                "content_code": code,
                "content_name": names.get(code) or code,
                "discipline_code": path.get("discipline_code"),
                "area_code": path.get("area_code"),
                "subcontent_of": None,
                "content_state": state,
                "evidence_state": c["evidence_state"],
                "questions_answered": answered,
                "questions_correct": c["questions_correct"],
                "accuracy": accuracy,
                "definitive_evidence_count": c.get("definitive_evidence_count", 0),
                "provisional_evidence_count": prov_count,
                "forced_closure_evidence_count": fc_count,
                "visual_dependency_evidence_count": c.get("visual_dependency_evidence_count", 0),
                "last_activity_at": c.get("last_activity_at"),
                "prerequisites": [{"code": p, "name": names.get(p), "mastered": mastered(p)}
                                  for p in graph.get(code, [])],
                "unsatisfied_prerequisites": [{"code": p, "name": names.get(p)} for p in unsatisfied],
                "blocks_contents": [{"code": d, "name": names.get(d)} for d in unmastered_dependents],
                "school_context_sources": context.get(code, []),
                "priority_score": score,
                "priority_reason": reason,
                "priority_factors": {
                    "low_evidence": round(f_low_evidence, 4),
                    "weak_performance": round(f_weak, 4),
                    "blocker": round(f_blocker, 4),
                    "context": round(f_context, 4),
                    "stale_review": round(f_stale_review, 4),
                    "blocked_penalty": round(f_blocked_penalty, 4),
                    "provisional_ratio": prov_ratio,
                },
                "action_type": action,
                "action_available": practice_available,
                "practice_available": practice_available,
                "action_note": ("Pratique este conteúdo agora." if practice_available
                                else "Ação ainda não disponível nesta fase."),
            }
            mat = material_by_code.get(code)
            material_available = bool(mat and mat.material_available)
            entry["material_available"] = material_available
            entry["material_id"] = mat.material_id if mat else None
            entry["material_title"] = mat.material_title if mat else None
            entry["material_note"] = ("Estudar agora" if material_available
                                      else "Material de estudo em breve.")
            if state == C_MASTERED:
                mastered_list.append(entry)
            else:
                steps.append(entry)

        steps.sort(key=lambda e: (-e["priority_score"], positions.get(e["content_code"], 1_000_000),
                                  e["content_code"]))
        for i, e in enumerate(steps, start=1):
            e["recommended_order"] = i
        mastered_list.sort(key=lambda e: (positions.get(e["content_code"], 1_000_000), e["content_code"]))

        has_provisional = any(e["forced_closure_evidence_count"] or e["provisional_evidence_count"]
                              for e in steps + mastered_list)
        overall = PATH_READY if (steps or mastered_list) else PATH_NO_EVIDENCE
        return {
            "student_external_id": student_external_id,
            "taxonomy_version": TAXONOMY_VERSION,
            "generated_at": _iso(_now()),
            "state": overall,
            "has_school_context": bool(context),
            "school_context_sources": sorted({s for v in context.values() for s in v}),
            "thresholds": self.policy.as_dict(),
            "weights": self.weights.as_dict(),
            "transparency": _TRANSPARENCY,
            "provisional_note": _PROVISIONAL_NOTE if has_provisional else None,
            "summary": {
                "step_count": len(steps),
                "mastered_count": len(mastered_list),
                "blocked_count": sum(1 for e in steps if e["content_state"] == C_BLOCKED),
                "insufficient_evidence_count": sum(
                    1 for e in steps if e["content_state"] == C_INSUFFICIENT),
                "needs_review_count": sum(
                    1 for e in steps if e["content_state"] == C_NEEDS_REVIEW),
            },
            "steps": steps,
            "mastered": mastered_list,
            "ai_used": False,
        }

    # ---- helpers ---------------------------------------------

    @staticmethod
    def _transitive_prereqs(code: str, graph: dict[str, list[str]]) -> set[str]:
        out: set[str] = set()
        stack = list(graph.get(code, []))
        while stack:
            c = stack.pop()
            if c in out:
                continue
            out.add(c)
            stack.extend(graph.get(c, []))
        return out

    @staticmethod
    def _reason(state, answered, accuracy, blocks, unsatisfied, names, fc_count,
                is_stale, in_context) -> str:
        # `blocks` and `unsatisfied` are lists of content_code strings.
        acc_txt = (f"{round((accuracy or 0) * 100)}% em {answered} quest"
                   f"{'ão' if answered == 1 else 'ões'}")
        if state == C_BLOCKED:
            miss = ", ".join(names.get(p, p) for p in unsatisfied)
            return (f"Bloqueado: depende de {miss}, que ainda não foi dominado. "
                    f"Estude o pré-requisito primeiro.")
        if state == C_INSUFFICIENT:
            base = f"Evidência insuficiente ({answered} quest"
            base += "ão" if answered == 1 else "ões"
            base += " respondida" + ("" if answered == 1 else "s") + ")."
            if blocks:
                base += (f" É pré-requisito de {len(blocks)} conteúdo(s) ainda não dominado(s) — "
                         f"vale um diagnóstico.")
            elif in_context:
                base += " Conteúdo em foco pela turma — vale um diagnóstico."
            return base
        if state == C_NEEDS_REVIEW:
            return ("Revisão recomendada: o conteúdo foi dominado, mas a última evidência já tem "
                    "algum tempo.")
        if state == C_RECOMMENDED:
            extra = ""
            if blocks:
                extra = (f" Além disso, é pré-requisito de {len(blocks)} conteúdo(s) ainda não "
                         f"dominado(s).")
            if fc_count:
                extra += (f" Observação: {fc_count} questão(ões) tinha(m) classificação por "
                          f"encerramento forçado.")
            return f"Prioridade de estudo: desempenho observado {acc_txt}." + extra
        if state == C_READY:
            if blocks:
                return (f"Continue praticando: desempenho {acc_txt}; é pré-requisito de "
                        f"{len(blocks)} conteúdo(s) ainda não dominado(s).")
            return f"Pronto para praticar mais: desempenho observado {acc_txt}."
        return f"Conteúdo dominado ({acc_txt})."


def _zero_content(code: str, name: str | None) -> dict:
    return {
        "content_code": code, "content_name": name or code,
        "questions_seen": 0, "questions_answered": 0, "questions_correct": 0,
        "questions_incorrect": 0, "accuracy": None,
        "evidence_count": 0, "evidence_state": STATE_INSUFFICIENT,
        "definitive_evidence_count": 0, "provisional_evidence_count": 0,
        "forced_closure_evidence_count": 0, "visual_dependency_evidence_count": 0,
        "last_activity_at": None, "discipline_code": None, "discipline_name": None,
    }


__all__ = [
    "AdaptiveLearningPathService",
    "LearningPathAuthError",
    "LearningPathError",
    "LearningPathNotFound",
    "LearningPathWeights",
    "PrerequisiteGraphInvalid",
    "PATH_GRAPH_INVALID",
    "PATH_NO_EVIDENCE",
    "PATH_READY",
]
