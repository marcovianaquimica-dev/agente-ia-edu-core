"""PHASE 20 - curriculum-v2 Domain Map (persistent, DERIVED, recomputable).

    correction  = ActivityResult + ActivityResultItem       (PHASE 18, immutable source)
    analysis    = PedagogicalAnalysisService                 (PHASE 19, READ-ONLY)
    domain map  = domain_content_mastery + this service      (this phase, DERIVED cache)
    adaptive learning path / recommendation / TRI            = FUTURE - NOT here

The Domain Map is a deterministic projection of the immutable ActivityResult /
ActivityResultItem history onto the ACTIVE curriculum-v2 catalog. It is a cache,
never a source of truth: ``rebuild_student`` reconstructs every row from the
results and a second run produces the same content. Correction is never re-run,
the official answer key is never re-read, and no official / result row is
written. It is separate from the legacy taxonomy_nodes ``student_content_mastery``
(diagnostic/practice) - official-activity and practice evidence are not merged.

Only OBSERVED evidence (answered questions) with an ACTIVE curriculum-v2
classification is aggregated. UNCLASSIFIED questions are never attributed.
Provisional classifications (NEEDS_REVIEW / FORCED_CLOSURE / confidence LOW) are
kept and counted separately - uncertainty is not hidden. The MIN_SAMPLE_SIZE
from PerformanceThresholdPolicy gates the INSUFFICIENT_EVIDENCE / OBSERVED state;
no "strong point" / "improvement" verdict is produced here (that stays in
PedagogicalAnalysisService).

AI-agnostic: imports no provider / OpenAI SDK.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from decimal import Decimal
from uuid import UUID

from sqlalchemy import and_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models.assessments import (
    ActivityAssignment,
    ActivityResult,
    ActivityResultItem,
    Assessment,
    DomainContentMastery,
)
from agente_ia_edu.db.models.catalog import CatalogNode, CatalogNodePrerequisite
from agente_ia_edu.services.activity_assignment_store import (
    ActivityAssignmentStore,
    AssignmentAuthError,
    AssignmentNotFound,
)
from agente_ia_edu.services.pedagogical_analysis import PerformanceThresholdPolicy
from agente_ia_edu.services.question_bank import QuestionBankService
from agente_ia_edu.services.question_list_store import (
    ListAuthorizationError,
    Requester,
)

TAXONOMY_VERSION = "curriculum-v2"
STATE_INSUFFICIENT = "INSUFFICIENT_EVIDENCE"
STATE_OBSERVED = "OBSERVED"
ORIGIN_OFFICIAL_ACTIVITY = "OFFICIAL_ACTIVITY"
ORIGIN_PRACTICE = "PRACTICE"
# INITIAL_DIAGNOSTIC / SIMULADO are reserved for future evidence origins - the
# column shape (origin_breakdown JSON) already keeps them separable.
_KNOWN_ORIGINS = (ORIGIN_OFFICIAL_ACTIVITY, ORIGIN_PRACTICE,
                  "INITIAL_DIAGNOSTIC", "SIMULADO")

_CLS_NEEDS_REVIEW = "NEEDS_REVIEW"
_CLS_FORCED_CLOSURE = "FORCED_CLOSURE"


class DomainMapError(ValueError):
    """422."""


class DomainMapNotFound(LookupError):
    """404."""


class DomainMapAuthError(PermissionError):
    """403."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


def _as_aware(dt: datetime | None) -> datetime | None:
    if dt is None:
        return None
    return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)


def _f(value) -> float | None:
    if value is None:
        return None
    return float(value) if not isinstance(value, Decimal) else float(value)


@dataclass
class _Grain:
    content_code: str
    subcontent_code: str | None
    seen: int = 0
    answered: int = 0
    correct: int = 0
    incorrect: int = 0
    definitive: int = 0
    provisional: int = 0
    forced_closure: int = 0
    visual: int = 0
    first_at: datetime | None = None
    last_at: datetime | None = None
    origin: dict | None = None

    def add(self, *, answered: bool, is_correct: bool, provisional: bool,
            forced_closure: bool, visual: bool, at: datetime | None,
            origin: str = ORIGIN_OFFICIAL_ACTIVITY) -> None:
        self.seen += 1
        if answered:
            self.answered += 1
            if is_correct:
                self.correct += 1
            else:
                self.incorrect += 1
            if provisional:
                self.provisional += 1
            else:
                self.definitive += 1
            if forced_closure:
                self.forced_closure += 1
            if visual:
                self.visual += 1
            self.origin = self.origin or {}
            key = origin if origin in _KNOWN_ORIGINS else ORIGIN_OFFICIAL_ACTIVITY
            self.origin[key] = self.origin.get(key, 0) + 1
        at = _as_aware(at)
        if at is not None:
            self.first_at = at if self.first_at is None else min(self.first_at, at)
            self.last_at = at if self.last_at is None else max(self.last_at, at)

    def accuracy(self) -> float | None:
        return round(self.correct / self.answered, 4) if self.answered else None

    def state(self, min_sample: int) -> str:
        return STATE_OBSERVED if self.answered >= min_sample else STATE_INSUFFICIENT


class CurriculumDomainMapService:
    """Deterministic. AI-agnostic. The persisted table is a rebuildable cache."""

    def __init__(self, session: AsyncSession,
                 policy: PerformanceThresholdPolicy | None = None) -> None:
        self._session = session
        self._assignments = ActivityAssignmentStore(session)
        self._bank = QuestionBankService(session)
        self.policy = policy or PerformanceThresholdPolicy.default()

    # ---- public entry points ---------------------------------------

    async def get_map(self, student_external_id: str, *, requester: Requester,
                      since: str | datetime | None = None,
                      until: str | datetime | None = None,
                      rebuild_if_missing: bool = True) -> dict:
        self._authz_self(student_external_id, requester)
        s, u = _parse_range(since, until)
        if s is not None or u is not None:
            # period queries are always derived on the fly (the cache holds the
            # all-time current state only) - spec s5 / s18
            grains = await self._aggregate(student_external_id, s, u)
            return await self._present(student_external_id, grains, period=(s, u), persisted=False)
        rows = await self._load_rows(student_external_id)
        if not rows and rebuild_if_missing:
            return await self.rebuild_student(student_external_id, requester=requester)
        return await self._present_rows(student_external_id, rows)

    async def rebuild_student(self, student_external_id: str, *, requester: Requester) -> dict:
        """Recompute the ALL-TIME map from ActivityResult history and replace the
        persisted rows in ONE transaction. Idempotent: a second call yields the
        same rows (ignoring surrogate id / timestamps)."""
        self._authz_self(student_external_id, requester)
        grains = await self._aggregate(student_external_id, None, None)
        now = _now()
        await self._session.execute(
            DomainContentMastery.__table__.delete().where(
                and_(DomainContentMastery.student_external_id == student_external_id,
                     DomainContentMastery.taxonomy_version == TAXONOMY_VERSION))
        )
        for g in _sorted_grains(grains):
            self._session.add(DomainContentMastery(
                student_external_id=student_external_id,
                taxonomy_version=TAXONOMY_VERSION,
                content_code=g.content_code,
                subcontent_code=g.subcontent_code,
                questions_seen=g.seen,
                questions_answered=g.answered,
                questions_correct=g.correct,
                questions_incorrect=g.incorrect,
                accuracy=g.accuracy(),
                evidence_count=g.answered,
                evidence_state=g.state(self.policy.min_sample_size),
                definitive_evidence_count=g.definitive,
                provisional_evidence_count=g.provisional,
                forced_closure_evidence_count=g.forced_closure,
                visual_dependency_evidence_count=g.visual,
                origin_breakdown=g.origin or {},
                first_activity_at=g.first_at,
                last_activity_at=g.last_at,
                last_evaluated_at=now,
            ))
        await self._session.commit()
        # commit() expired every ORM instance in the session, including the
        # QuestionBankService catalog cache - force a fresh reload before we
        # touch CatalogNode attributes again.
        self._bank._catalog_cache = None
        self._bank._catalog_by_id = None
        rows = await self._load_rows(student_external_id)
        return await self._present_rows(student_external_id, rows)

    async def get_content(self, student_external_id: str, content_code: str, *,
                          requester: Requester) -> dict:
        m = await self.get_map(student_external_id, requester=requester)
        for disc in m["disciplines"]:
            for c in disc["contents"]:
                if c["content_code"] == content_code:
                    return {"student_external_id": student_external_id,
                            "taxonomy_version": TAXONOMY_VERSION,
                            "discipline": {k: disc[k] for k in
                                           ("discipline_code", "discipline_name")},
                            "content": c}
        raise DomainMapNotFound(content_code)

    async def get_discipline(self, student_external_id: str, discipline_code: str, *,
                             requester: Requester) -> dict:
        m = await self.get_map(student_external_id, requester=requester)
        for disc in m["disciplines"]:
            if disc["discipline_code"] == discipline_code:
                return {"student_external_id": student_external_id,
                        "taxonomy_version": TAXONOMY_VERSION, "discipline": disc}
        raise DomainMapNotFound(discipline_code)

    async def get_evidence(self, student_external_id: str, content_code: str, *,
                           requester: Requester) -> dict:
        """The individual observations behind one content - traceable to
        activity_result -> activity_result_item -> question_version ->
        curriculum classification. Re-derived (not persisted)."""
        self._authz_self(student_external_id, requester)
        results = (await self._session.execute(
            select(ActivityResult.id, ActivityResult.assignment_id, ActivityResult.completed_at)
            .where(ActivityResult.student_external_id == student_external_id)
        )).all()
        if not results:
            return _empty_evidence(student_external_id, content_code)
        result_ids = [r[0] for r in results]
        completed_by = {r[0]: _as_aware(r[2]) for r in results}
        assignment_by = {r[0]: r[1] for r in results}
        items = (await self._session.execute(
            select(ActivityResultItem).where(ActivityResultItem.result_id.in_(result_ids))
        )).scalars().all()
        vids = list({it.question_version_id for it in items})
        bank = {bi.question_version_id: bi
                for bi in await self._bank.get_questions_by_version_ids(vids)}
        titles = await self._assignment_titles(set(assignment_by.values()))

        obs = []
        for it in items:
            bi = bank.get(it.question_version_id)
            cls = bi.classification if bi is not None else None
            if cls is None or cls.content_code != content_code:
                continue
            state = bi.classification_state if bi is not None else "UNCLASSIFIED"
            obs.append({
                "activity_assignment_id": str(assignment_by[it.result_id]),
                "activity_title": titles.get(assignment_by[it.result_id]),
                "activity_completed_at": _iso(completed_by[it.result_id]),
                "question_version_id": str(it.question_version_id),
                "official_number": it.official_number,
                "answered": bool(it.answered),
                "is_correct": bool(it.is_correct),
                "selected_option_key": it.selected_option_key,
                "subcontent_code": cls.subcontent_code,
                "classification_status": state,
                "provisional": bool(state in (_CLS_NEEDS_REVIEW, _CLS_FORCED_CLOSURE)
                                    or (cls.confidence or "").upper() == "LOW"),
                "visual_dependency": bool(bi.has_visual_dependency) if bi is not None else False,
            })
        obs.sort(key=lambda o: (o["activity_completed_at"] or "", o["official_number"] or 0,
                                o["question_version_id"]))
        answered = [o for o in obs if o["answered"]]
        correct = sum(1 for o in answered if o["is_correct"])
        return {
            "student_external_id": student_external_id,
            "taxonomy_version": TAXONOMY_VERSION,
            "content_code": content_code,
            "evidence_count": len(answered),
            "questions_seen": len(obs),
            "questions_correct": correct,
            "accuracy": round(correct / len(answered), 4) if answered else None,
            "observations": obs,
        }

    async def manager_view(self, assignment_id: UUID, *, requester: Requester,
                           student_external_id: str | None = None) -> dict:
        """Owner / same-school-privileged view (the exact PHASE 16 assignment
        rule, reused). Returns the curriculum domain map of every student who has
        a corrected result UNDER THIS ASSIGNMENT (optionally one student). No new
        authorisation rule."""
        try:
            await self._assignments.get(assignment_id, requester=requester)
        except AssignmentNotFound as exc:
            raise DomainMapNotFound(str(assignment_id)) from exc
        except (AssignmentAuthError, ListAuthorizationError) as exc:
            raise DomainMapAuthError(str(exc)) from exc

        students = list((await self._session.execute(
            select(ActivityResult.student_external_id)
            .where(ActivityResult.assignment_id == assignment_id)
            .distinct()
        )).scalars().all())
        if student_external_id is not None:
            students = [s for s in students if s == student_external_id]
        students = sorted(students)
        # batched: one _aggregate_many() call for every student in the
        # classroom instead of one _aggregate() per student (was an N+1 -
        # see test_phase20_manager_view_n1.py), then one shared catalog/
        # prerequisite lookup for the union of content codes across all of
        # them, instead of one per student inside _present().
        grains_by_student = await self._aggregate_many(students, None, None)
        all_content_codes: set[str] = set()
        for grains in grains_by_student.values():
            all_content_codes.update(cc for (cc, scc) in grains if scc is None)
        paths, names, prereqs = await self._catalog_paths(all_content_codes)
        maps = [
            self._present_with_catalog(
                sid, grains_by_student.get(sid, {}), paths, names, prereqs,
                period=None, persisted=False,
            )
            for sid in students
        ]
        return {
            "assignment_id": str(assignment_id),
            "taxonomy_version": TAXONOMY_VERSION,
            "student_count": len(maps),
            "students": maps,
        }

    # ---- authz --------------------------------------------------

    @staticmethod
    def _authz_self(student_external_id: str, requester: Requester) -> None:
        if requester.external_user_id != student_external_id and not requester.is_platform_admin:
            raise DomainMapAuthError("a student can only read their own domain map")

    # ---- aggregation (constant query count) -------------------

    async def _aggregate(self, student_external_id: str,
                         since: datetime | None, until: datetime | None) -> dict[tuple, _Grain]:
        grains_by_student = await self._aggregate_many([student_external_id], since, until)
        return grains_by_student.get(student_external_id, {})

    async def _aggregate_many(self, student_external_ids: list[str],
                              since: datetime | None,
                              until: datetime | None) -> dict[str, dict[tuple, _Grain]]:
        """Same aggregation as ``_aggregate``, batched over MANY students in a
        bounded number of queries (independent of student count) instead of
        one ``_aggregate()`` call per student - used by ``manager_view`` for a
        whole classroom/assignment. A single-student call goes through this
        too (``student_external_ids`` of length 1), so the query shape stays
        identical to before for every other caller."""
        empty: dict[str, dict[tuple, _Grain]] = {sid: {} for sid in student_external_ids}
        if not student_external_ids:
            return {}
        conds = [ActivityResult.student_external_id.in_(student_external_ids)]
        if since is not None:
            conds.append(ActivityResult.completed_at >= since)
        if until is not None:
            conds.append(ActivityResult.completed_at < until)
        results = (await self._session.execute(
            select(ActivityResult.id, ActivityResult.completed_at, ActivityResult.assignment_id,
                   ActivityResult.student_external_id)
            .where(and_(*conds))
        )).all()
        if not results:
            return empty
        result_ids = [r[0] for r in results]
        completed_by = {r[0]: _as_aware(r[1]) for r in results}
        student_by_result = {r[0]: r[3] for r in results}
        # resolve the evidence ORIGIN per result from the assignment metadata
        # (OFFICIAL_ACTIVITY by default; PRACTICE for adaptive-practice activities)
        assignment_ids = {r[2] for r in results if r[2] is not None}
        origin_by_assignment = {}
        if assignment_ids:
            for aid_, md in (await self._session.execute(
                select(ActivityAssignment.id, ActivityAssignment.metadata_)
                .where(ActivityAssignment.id.in_(assignment_ids))
            )).all():
                origin_by_assignment[aid_] = ((md or {}).get("origin") or ORIGIN_OFFICIAL_ACTIVITY)
        origin_by_result = {r[0]: origin_by_assignment.get(r[2], ORIGIN_OFFICIAL_ACTIVITY)
                            for r in results}
        items = (await self._session.execute(
            select(ActivityResultItem).where(ActivityResultItem.result_id.in_(result_ids))
        )).scalars().all()
        if not items:
            return empty
        vids = list({it.question_version_id for it in items})
        await self._bank._load_catalog()
        bank = {bi.question_version_id: bi
                for bi in await self._bank.get_questions_by_version_ids(vids)}

        grains_by_student: dict[str, dict[tuple, _Grain]] = {sid: {} for sid in student_external_ids}

        def grain(sid: str, cc: str, scc: str | None) -> _Grain:
            student_grains = grains_by_student.setdefault(sid, {})
            key = (cc, scc)
            g = student_grains.get(key)
            if g is None:
                g = _Grain(content_code=cc, subcontent_code=scc)
                student_grains[key] = g
            return g

        for it in items:
            sid = student_by_result.get(it.result_id)
            if sid is None:
                continue
            bi = bank.get(it.question_version_id)
            cls = bi.classification if bi is not None else None
            if cls is None or not cls.content_code:
                continue                                   # UNCLASSIFIED - never attributed (s10)
            state = bi.classification_state if bi is not None else "UNCLASSIFIED"
            provisional = bool(state in (_CLS_NEEDS_REVIEW, _CLS_FORCED_CLOSURE)
                               or (cls.confidence or "").upper() == "LOW")
            forced = state == _CLS_FORCED_CLOSURE
            visual = bool(bi.has_visual_dependency) if bi is not None else False
            at = completed_by.get(it.result_id)
            common = dict(answered=bool(it.answered), is_correct=bool(it.is_correct),
                          provisional=provisional, forced_closure=forced, visual=visual, at=at,
                          origin=origin_by_result.get(it.result_id, ORIGIN_OFFICIAL_ACTIVITY))
            grain(sid, cls.content_code, None).add(**common)
            if cls.subcontent_code:
                grain(sid, cls.content_code, cls.subcontent_code).add(**common)
        return grains_by_student

    # ---- persistence read ------------------------------------

    async def _load_rows(self, student_external_id: str) -> list[DomainContentMastery]:
        return list((await self._session.execute(
            select(DomainContentMastery).where(
                DomainContentMastery.student_external_id == student_external_id,
                DomainContentMastery.taxonomy_version == TAXONOMY_VERSION,
            )
        )).scalars().all())

    # ---- presentation --------------------------------------

    async def _catalog_paths(self, content_codes: set[str]) -> tuple[dict, dict, dict]:
        """(path_by_content, name_by_code, prereqs_by_content) resolved from the
        catalog (names/hierarchy are NOT persisted - spec s4)."""
        await self._bank._load_catalog()
        catalog = self._bank._catalog_cache or {}
        name_by_code = {code: node.name for code, node in catalog.items()}
        path_by_content = {c: self._bank._resolve_curriculum_path(c) for c in content_codes}
        prereqs: dict[str, list[dict]] = {}
        code_to_id = {code: node.id for code, node in catalog.items()}
        id_to_code = {node.id: code for code, node in catalog.items()}
        target_ids = [code_to_id[c] for c in content_codes if c in code_to_id]
        if target_ids:
            rows = (await self._session.execute(
                select(CatalogNodePrerequisite.content_node_id,
                       CatalogNodePrerequisite.prerequisite_node_id)
                .where(CatalogNodePrerequisite.content_node_id.in_(target_ids))
            )).all()
            for cnid, pnid in rows:
                cc = id_to_code.get(cnid)
                pc = id_to_code.get(pnid)
                if cc and pc:
                    prereqs.setdefault(cc, []).append({"code": pc, "name": name_by_code.get(pc)})
            for cc in prereqs:
                prereqs[cc].sort(key=lambda p: p["code"])
        return path_by_content, name_by_code, prereqs

    async def _present_rows(self, student_external_id: str,
                            rows: list[DomainContentMastery]) -> dict:
        content_codes = {r.content_code for r in rows}
        paths, names, prereqs = await self._catalog_paths(content_codes)
        content_rows = [r for r in rows if r.subcontent_code is None]
        sub_rows: dict[str, list[DomainContentMastery]] = {}
        for r in rows:
            if r.subcontent_code is not None:
                sub_rows.setdefault(r.content_code, []).append(r)

        def content_dict(r: DomainContentMastery) -> dict:
            p = paths.get(r.content_code, {})
            subs = sorted(sub_rows.get(r.content_code, []), key=lambda x: x.subcontent_code)
            return {
                "content_code": r.content_code,
                "content_name": names.get(r.content_code) or r.content_code,
                "discipline_code": p.get("discipline_code"),
                "area_code": p.get("area_code"),
                "area_name": names.get(p.get("area_code")),
                "questions_seen": r.questions_seen,
                "questions_answered": r.questions_answered,
                "questions_correct": r.questions_correct,
                "questions_incorrect": r.questions_incorrect,
                "accuracy": _f(r.accuracy),
                "evidence_count": r.evidence_count,
                "evidence_state": r.evidence_state,
                "definitive_evidence_count": r.definitive_evidence_count,
                "provisional_evidence_count": r.provisional_evidence_count,
                "forced_closure_evidence_count": r.forced_closure_evidence_count,
                "visual_dependency_evidence_count": r.visual_dependency_evidence_count,
                "origin_breakdown": r.origin_breakdown or {},
                "first_activity_at": _iso(r.first_activity_at),
                "last_activity_at": _iso(r.last_activity_at),
                "last_evaluated_at": _iso(r.last_evaluated_at),
                "prerequisites": prereqs.get(r.content_code, []),
                "subcontents": [{
                    "subcontent_code": s.subcontent_code,
                    "subcontent_name": names.get(s.subcontent_code) or s.subcontent_code,
                    "questions_seen": s.questions_seen,
                    "questions_answered": s.questions_answered,
                    "questions_correct": s.questions_correct,
                    "questions_incorrect": s.questions_incorrect,
                    "accuracy": _f(s.accuracy),
                    "evidence_count": s.evidence_count,
                    "evidence_state": s.evidence_state,
                    "provisional_evidence_count": s.provisional_evidence_count,
                } for s in subs],
            }

        return self._assemble(student_external_id, [content_dict(r) for r in content_rows],
                              paths, names, period=None, persisted=True)

    async def _present(self, student_external_id: str, grains: dict[tuple, _Grain], *,
                       period, persisted: bool) -> dict:
        content_grains = [g for (cc, scc), g in grains.items() if scc is None]
        content_codes = {g.content_code for g in content_grains}
        paths, names, prereqs = await self._catalog_paths(content_codes)
        return self._present_with_catalog(
            student_external_id, grains, paths, names, prereqs,
            period=period, persisted=persisted,
        )

    def _present_with_catalog(self, student_external_id: str, grains: dict[tuple, _Grain],
                              paths: dict, names: dict, prereqs: dict, *,
                              period, persisted: bool) -> dict:
        """Same rendering as ``_present``, but takes an already-resolved
        catalog (paths/names/prereqs) instead of querying for it - lets
        ``manager_view`` resolve the catalog ONCE for a whole classroom
        instead of once per student."""
        content_grains = [g for (cc, scc), g in grains.items() if scc is None]
        ms = self.policy.min_sample_size

        def content_dict(g: _Grain) -> dict:
            p = paths.get(g.content_code, {})
            subs = sorted((sg for (cc, scc), sg in grains.items()
                           if cc == g.content_code and scc is not None),
                          key=lambda x: x.subcontent_code or "")
            return {
                "content_code": g.content_code,
                "content_name": names.get(g.content_code) or g.content_code,
                "discipline_code": p.get("discipline_code"),
                "area_code": p.get("area_code"),
                "area_name": names.get(p.get("area_code")),
                "questions_seen": g.seen,
                "questions_answered": g.answered,
                "questions_correct": g.correct,
                "questions_incorrect": g.incorrect,
                "accuracy": g.accuracy(),
                "evidence_count": g.answered,
                "evidence_state": g.state(ms),
                "definitive_evidence_count": g.definitive,
                "provisional_evidence_count": g.provisional,
                "forced_closure_evidence_count": g.forced_closure,
                "visual_dependency_evidence_count": g.visual,
                "origin_breakdown": g.origin or {},
                "first_activity_at": _iso(g.first_at),
                "last_activity_at": _iso(g.last_at),
                "last_evaluated_at": None,
                "prerequisites": prereqs.get(g.content_code, []),
                "subcontents": [{
                    "subcontent_code": s.subcontent_code,
                    "subcontent_name": names.get(s.subcontent_code) or s.subcontent_code,
                    "questions_seen": s.seen,
                    "questions_answered": s.answered,
                    "questions_correct": s.correct,
                    "questions_incorrect": s.incorrect,
                    "accuracy": s.accuracy(),
                    "evidence_count": s.answered,
                    "evidence_state": s.state(ms),
                    "provisional_evidence_count": s.provisional,
                } for s in subs],
            }

        return self._assemble(student_external_id,
                              [content_dict(g) for g in _sorted_content(content_grains)],
                              paths, names, period=period, persisted=persisted)

    def _assemble(self, student_external_id, contents: list[dict], paths, names, *,
                  period, persisted: bool) -> dict:
        by_discipline: dict[str, dict] = {}
        for c in contents:
            dc = c["discipline_code"] or "(sem disciplina)"
            d = by_discipline.setdefault(dc, {
                "discipline_code": c["discipline_code"],
                "discipline_name": names.get(c["discipline_code"]) if c["discipline_code"] else None,
                "questions_answered": 0, "questions_correct": 0, "questions_incorrect": 0,
                "contents": [],
            })
            d["contents"].append(c)
            d["questions_answered"] += c["questions_answered"]
            d["questions_correct"] += c["questions_correct"]
            d["questions_incorrect"] += c["questions_incorrect"]
        disciplines = []
        for key in sorted(by_discipline):
            d = by_discipline[key]
            d["contents"].sort(key=lambda x: x["content_code"])
            ans = d["questions_answered"]
            d["accuracy"] = round(d["questions_correct"] / ans, 4) if ans else None
            disciplines.append(d)

        total_ans = sum(c["questions_answered"] for c in contents)
        total_cor = sum(c["questions_correct"] for c in contents)
        return {
            "student_external_id": student_external_id,
            "taxonomy_version": TAXONOMY_VERSION,
            "generated_at": _iso(_now()),
            "period": ({"since": _iso(period[0]), "until": _iso(period[1])} if period else None),
            "persisted": persisted,
            "thresholds": {"MIN_SAMPLE_SIZE": self.policy.min_sample_size},
            "summary": {
                "content_count": len(contents),
                "observed_count": sum(1 for c in contents if c["evidence_state"] == STATE_OBSERVED),
                "insufficient_evidence_count": sum(
                    1 for c in contents if c["evidence_state"] == STATE_INSUFFICIENT),
                "questions_answered": total_ans,
                "questions_correct": total_cor,
                "accuracy": round(total_cor / total_ans, 4) if total_ans else None,
                "provisional_evidence_count": sum(c["provisional_evidence_count"] for c in contents),
                "forced_closure_evidence_count": sum(
                    c["forced_closure_evidence_count"] for c in contents),
                "visual_dependency_evidence_count": sum(
                    c["visual_dependency_evidence_count"] for c in contents),
                "disciplines_covered": sorted(
                    {c["discipline_code"] for c in contents if c["discipline_code"]}),
            },
            "disciplines": disciplines,
            "unclassified_note": ("Questões sem classificação curricular entram no resultado da "
                                  "atividade, mas não no mapa de domínio por conteúdo."),
            "ai_used": False,
        }

    async def _assignment_titles(self, assignment_ids: set) -> dict:
        ids = [a for a in assignment_ids if a is not None]
        if not ids:
            return {}
        rows = (await self._session.execute(
            select(ActivityAssignment.id, Assessment.title)
            .join(Assessment, Assessment.id == ActivityAssignment.assessment_id)
            .where(ActivityAssignment.id.in_(ids))
        )).all()
        return {aid: title for aid, title in rows}


# ---------------------------------------------------------------------------
# module helpers
# ---------------------------------------------------------------------------


def _parse_range(since, until):
    def one(v):
        if v is None:
            return None
        if isinstance(v, datetime):
            return _as_aware(v)
        try:
            dt = datetime.fromisoformat(str(v).replace("Z", "+00:00"))
        except ValueError as exc:
            raise DomainMapError(f"data inválida: {v!r}") from exc
        return _as_aware(dt)
    s, u = one(since), one(until)
    if s is not None and u is not None and u < s:
        raise DomainMapError("until não pode ser anterior a since")
    return s, u


def _sorted_grains(grains: dict[tuple, _Grain]) -> list[_Grain]:
    return [grains[k] for k in sorted(grains, key=lambda k: (k[0], k[1] or ""))]


def _sorted_content(content_grains: list[_Grain]) -> list[_Grain]:
    return sorted(content_grains, key=lambda g: g.content_code)


def _empty_evidence(student_external_id: str, content_code: str) -> dict:
    return {"student_external_id": student_external_id, "taxonomy_version": TAXONOMY_VERSION,
            "content_code": content_code, "evidence_count": 0, "questions_seen": 0,
            "questions_correct": 0, "accuracy": None, "observations": []}


__all__ = [
    "CurriculumDomainMapService",
    "DomainMapAuthError",
    "DomainMapError",
    "DomainMapNotFound",
    "ORIGIN_OFFICIAL_ACTIVITY",
    "ORIGIN_PRACTICE",
    "STATE_INSUFFICIENT",
    "STATE_OBSERVED",
    "TAXONOMY_VERSION",
]
