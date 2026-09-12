"""PHASE 19 - pedagogical analysis engine & result aggregation.

    correction  = ActivityResult + ActivityResultItem        (PHASE 18)
    analysis    = this module (READ-ONLY aggregation)          (this phase)
    domain map / adaptive trilha / recommendation / TRI        = FUTURE - NOT here

"Primeiro medir corretamente. Depois interpretar. Só depois recomendar."

This layer is 100% deterministic and READ-ONLY. It writes nothing. It NEVER
re-runs the correction, re-reads the official answer key, or compares the
student's answer to a key - ``ActivityResultItem.is_correct`` (frozen in
PHASE 18) is the sole source of per-question outcome. Classification is resolved
only from the ACTIVE curriculum-v2 ``PedagogicalClassification`` (SUPERSEDED rows
are never used) via the existing ``QuestionBankService`` (one batched load - no
N+1). Nothing is inferred from question text and no LLM is involved.

Aggregation grain: per question -> per discipline -> per content -> per
subcontent (when a valid subcontent code exists). UNCLASSIFIED questions stay in
the overall totals but are NEVER attributed to a discipline/content. LOW /
NEEDS_REVIEW / FORCED_CLOSURE classifications are counted but flagged provisional
- never promoted, never silently dropped. ``visual_dependency`` is preserved.

Strengths / improvements use a configurable ``PerformanceThresholdPolicy`` (no
hardcoded thresholds spread through the codebase) and a minimum sample size so a
single question can never produce a pedagogical verdict.

AI-agnostic: imports no provider / OpenAI SDK.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from typing import Iterable
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models.assessments import (
    ActivityResult,
    ActivityResultItem,
)
from agente_ia_edu.services.activity_assignment_store import (
    ActivityAssignmentStore,
    AssignmentAuthError,
    AssignmentNotFound,
)
from agente_ia_edu.services.question_bank import QuestionBankService
from agente_ia_edu.services.question_list_store import (
    ListAuthorizationError,
    ListNotFoundError,
    ListStateError,
    Requester,
)

# temporal grouping windows a later phase will implement; declared here so the
# shape is stable for the frontend/consumers.
TEMPORAL_WINDOWS = (
    "last_7_days", "last_30_days", "bimester", "semester", "academic_year", "custom",
)

BAND_STRONG = "PONTO_FORTE"
BAND_IMPROVEMENT = "PONTO_MELHORIA"
BAND_INTERMEDIATE = "DESEMPENHO_INTERMEDIARIO"
BAND_INSUFFICIENT = "INSUFFICIENT_SAMPLE"
BAND_NO_DATA = "SEM_DADOS"

# per-question classification status (mirrors QuestionBankService.classification_state)
CLS_UNCLASSIFIED = "UNCLASSIFIED"
CLS_CLASSIFIED = "CLASSIFIED"
CLS_NEEDS_REVIEW = "NEEDS_REVIEW"
CLS_FORCED_CLOSURE = "FORCED_CLOSURE"


class AnalysisError(ValueError):
    """422."""


class AnalysisNotFound(LookupError):
    """404 - no corrected result to analyse."""


class AnalysisAuthError(PermissionError):
    """403."""


@dataclass(frozen=True)
class PerformanceThresholdPolicy:
    """Configurable, single source of truth for the strong/improvement bands.

    Change these here (or pass a custom policy to the service) without touching
    the aggregation engine.
    """

    min_sample_size: int = 3
    strong_accuracy: float = 0.80
    improvement_accuracy: float = 0.60

    @classmethod
    def default(cls) -> "PerformanceThresholdPolicy":
        return cls()

    def as_dict(self) -> dict:
        return {
            "MIN_SAMPLE_SIZE": self.min_sample_size,
            "STRONG_ACCURACY": self.strong_accuracy,
            "IMPROVEMENT_ACCURACY": self.improvement_accuracy,
        }

    def band(self, *, answered: int, accuracy: float | None) -> str:
        if answered < self.min_sample_size:
            return BAND_INSUFFICIENT
        if accuracy is None:
            return BAND_NO_DATA
        if accuracy >= self.strong_accuracy:
            return BAND_STRONG
        if accuracy < self.improvement_accuracy:
            return BAND_IMPROVEMENT
        return BAND_INTERMEDIATE


def _accuracy(correct: int, answered: int) -> float | None:
    if answered <= 0:                       # spec s5/s20: no division by zero
        return None
    return round(correct / answered, 4)


def _iso(dt: datetime | None) -> str | None:
    return dt.isoformat() if dt else None


class _Bucket:
    """Mutable accumulator used only inside a single _build() call."""

    __slots__ = ("total", "answered", "correct", "incorrect", "unanswered",
                 "classified", "provisional", "forced_closure", "visual_dependency",
                 "conf_high", "conf_medium", "conf_low", "conf_none")

    def __init__(self) -> None:
        self.total = self.answered = self.correct = self.incorrect = self.unanswered = 0
        self.classified = self.provisional = self.forced_closure = self.visual_dependency = 0
        self.conf_high = self.conf_medium = self.conf_low = self.conf_none = 0

    def add(self, q: dict) -> None:
        self.total += 1
        if q["answered"]:
            self.answered += 1
            if q["is_correct"]:
                self.correct += 1
            else:
                self.incorrect += 1
        else:
            self.unanswered += 1
        if q["classification_status"] != CLS_UNCLASSIFIED:
            self.classified += 1
        if q["provisional"]:
            self.provisional += 1
        if q["classification_status"] == CLS_FORCED_CLOSURE:
            self.forced_closure += 1
        if q["visual_dependency"]:
            self.visual_dependency += 1
        c = (q["confidence"] or "").upper()
        if c == "HIGH":
            self.conf_high += 1
        elif c == "MEDIUM":
            self.conf_medium += 1
        elif c == "LOW":
            self.conf_low += 1
        else:
            self.conf_none += 1

    def core(self, policy: PerformanceThresholdPolicy) -> dict:
        acc = _accuracy(self.correct, self.answered)
        return {
            "total_questions": self.total,
            "answered": self.answered,
            "correct": self.correct,
            "incorrect": self.incorrect,
            "unanswered": self.unanswered,
            "accuracy": acc,
            "band": policy.band(answered=self.answered, accuracy=acc),
        }

    def confidence_breakdown(self) -> dict:
        return {"HIGH": self.conf_high, "MEDIUM": self.conf_medium,
                "LOW": self.conf_low, "NONE": self.conf_none}


class PedagogicalAnalysisService:
    """READ-ONLY. Deterministic. AI-agnostic."""

    def __init__(self, session: AsyncSession,
                 policy: PerformanceThresholdPolicy | None = None) -> None:
        self._session = session
        self._assignments = ActivityAssignmentStore(session)
        self._bank = QuestionBankService(session)
        self.policy = policy or PerformanceThresholdPolicy.default()

    # ---- entry points -------------------------------------------------

    async def analyze_for_student(self, assignment_id: UUID, *, requester: Requester) -> dict:
        try:
            assignment = await self._assignments.resolve_student_assignment(
                assignment_id, requester=requester)
        except AssignmentNotFound as exc:
            raise AnalysisNotFound(str(assignment_id)) from exc
        except AssignmentAuthError as exc:
            raise AnalysisAuthError(str(exc)) from exc
        result = await self._load_student_result(assignment_id, requester.external_user_id)
        if result is None:
            raise AnalysisNotFound(str(assignment_id))
        return await self._build(result)

    async def analyze_for_manager(self, assignment_id: UUID, *, requester: Requester,
                                  student_external_id: str | None = None) -> dict:
        """Manager (owner / same-school privileged) view. Reuses the PHASE 16
        ActivityAssignmentStore.get() authorisation - NO new authz rule. Returns
        one analysis per student result under this assignment (optionally
        filtered to one student), plus a class aggregate."""
        try:
            await self._assignments.get(assignment_id, requester=requester)   # authz gate
        except AssignmentNotFound as exc:
            raise AnalysisNotFound(str(assignment_id)) from exc
        except AssignmentAuthError as exc:
            raise AnalysisAuthError(str(exc)) from exc
        except (ListNotFoundError,) as exc:  # pragma: no cover
            raise AnalysisNotFound(str(assignment_id)) from exc
        except ListAuthorizationError as exc:
            raise AnalysisAuthError(str(exc)) from exc

        rows = (await self._session.execute(
            select(ActivityResult)
            .where(ActivityResult.assignment_id == assignment_id)
            .order_by(ActivityResult.student_external_id)
        )).scalars().all()
        if student_external_id is not None:
            rows = [r for r in rows if r.student_external_id == student_external_id]
        analyses = [await self._build(r) for r in rows]
        return {
            "assignment_id": str(assignment_id),
            "thresholds": self.policy.as_dict(),
            "student_count": len(analyses),
            "students": analyses,
            "class_aggregate": _class_aggregate(analyses, self.policy),
        }

    # ---- loading ---------------------------------------------------

    async def _load_student_result(self, assignment_id: UUID, student_id: str) -> ActivityResult | None:
        return (await self._session.execute(
            select(ActivityResult).where(
                ActivityResult.assignment_id == assignment_id,
                ActivityResult.student_external_id == student_id,
            )
        )).scalar_one_or_none()

    async def _load_items(self, result_id: UUID) -> list[ActivityResultItem]:
        return list((await self._session.execute(
            select(ActivityResultItem)
            .where(ActivityResultItem.result_id == result_id)
            .order_by(ActivityResultItem.position)
        )).scalars().all())

    # ---- the deterministic build --------------------------------

    async def _build(self, result: ActivityResult) -> dict:
        items = await self._load_items(result.id)
        vids = [it.question_version_id for it in items]

        # ONE batched classification+catalog load (no per-question query)
        await self._bank._load_catalog()
        bank_items = await self._bank.get_questions_by_version_ids(vids)
        bank_by_vid = {bi.question_version_id: bi for bi in bank_items}
        catalog = self._bank._catalog_cache or {}

        def name_of(code: str | None) -> str | None:
            node = catalog.get(code) if code else None
            return node.name if node is not None else None

        questions: list[dict] = []
        for it in items:
            bi = bank_by_vid.get(it.question_version_id)
            cls = bi.classification if bi is not None else None
            state = (bi.classification_state if bi is not None else CLS_UNCLASSIFIED) or CLS_UNCLASSIFIED
            confidence = cls.confidence if cls is not None else None
            provisional = bool(
                state in (CLS_NEEDS_REVIEW, CLS_FORCED_CLOSURE)
                or (confidence or "").upper() == "LOW"
            )
            questions.append({
                "question_version_id": str(it.question_version_id),
                "position": it.position,
                "official_number": it.official_number,
                "selected_option_key": it.selected_option_key,
                "is_correct": bool(it.is_correct),
                "answered": bool(it.answered),
                "discipline_code": cls.discipline_code if cls else None,
                "area_code": cls.area_code if cls else None,
                "content_code": cls.content_code if cls else None,
                "content_name": name_of(cls.content_code) if cls else None,
                "subcontent_code": cls.subcontent_code if cls else None,
                "subcontent_name": name_of(cls.subcontent_code) if cls else None,
                "discipline_name": name_of(cls.discipline_code) if cls else None,
                "classification_status": state,
                "confidence": confidence,
                "numeric_confidence": cls.numeric_confidence if cls else None,
                "classification_mode": cls.classification_mode if cls else None,
                "review_reason": cls.review_reason if cls else None,
                "visual_dependency": bool(bi.has_visual_dependency) if bi is not None else False,
                "provisional": provisional,
            })

        summary = _summary(questions, self.policy)
        by_discipline = _by_discipline(questions, self.policy, name_of)
        by_content = _by_content(questions, self.policy, name_of)
        strengths, improvements, intermediate, insufficient = _verdicts(by_content, by_discipline)

        return {
            "result_id": str(result.id),
            "attempt_id": str(result.attempt_id),
            "assignment_id": str(result.assignment_id),
            "assessment_version_id": str(result.assessment_version_id),
            "student_external_id": result.student_external_id,
            "completion_status": result.completion_status,
            "thresholds": self.policy.as_dict(),
            "temporal": {
                "activity_date": (result.completed_at.date().isoformat()
                                  if result.completed_at else None),
                "completed_at": _iso(result.completed_at),
                "corrected_at": _iso(result.corrected_at),
                "grouping_windows": list(TEMPORAL_WINDOWS),
            },
            "summary": summary,
            "by_discipline": by_discipline,
            "by_content": by_content,
            "strengths": strengths,
            "improvements": improvements,
            "intermediate": intermediate,
            "insufficient_sample": insufficient,
            "questions": questions,
            "ai_used": False,
        }


# ---------------------------------------------------------------------------
# pure helpers (module-level, deterministic)
# ---------------------------------------------------------------------------


def _summary(questions: list[dict], policy: PerformanceThresholdPolicy) -> dict:
    b = _Bucket()
    disciplines: set[str] = set()
    contents: set[str] = set()
    subcontents: set[str] = set()
    for q in questions:
        b.add(q)
        if q["discipline_code"]:
            disciplines.add(q["discipline_code"])
        if q["content_code"]:
            contents.add(q["content_code"])
        if q["subcontent_code"]:
            subcontents.add(q["subcontent_code"])
    core = b.core(policy)
    core.update({
        "classified_questions": b.classified,
        "unclassified_questions": b.total - b.classified,
        "provisional_questions": b.provisional,
        "forced_closure_questions": b.forced_closure,
        "visual_dependency_questions": b.visual_dependency,
        "confidence_breakdown": b.confidence_breakdown(),
        "disciplines_covered": sorted(disciplines),
        "contents_covered": sorted(contents),
        "subcontents_covered": sorted(subcontents),
    })
    return core


def _by_discipline(questions: list[dict], policy: PerformanceThresholdPolicy,
                   name_of) -> list[dict]:
    buckets: dict[str, _Bucket] = {}
    for q in questions:
        code = q["discipline_code"]
        if not code:
            continue                       # UNCLASSIFIED never attributed (s11)
        buckets.setdefault(code, _Bucket()).add(q)
    out = []
    for code in sorted(buckets):
        b = buckets[code]
        row = b.core(policy)
        row.update({
            "discipline_code": code,
            "discipline_name": name_of(code),
            "classified_questions": b.classified,
            "unclassified_questions": b.total - b.classified,
            "provisional_questions": b.provisional,
            "forced_closure_questions": b.forced_closure,
            "visual_dependency_questions": b.visual_dependency,
            "confidence_breakdown": b.confidence_breakdown(),
        })
        out.append(row)
    return out


def _by_content(questions: list[dict], policy: PerformanceThresholdPolicy,
                name_of) -> list[dict]:
    # group by (discipline, area, content); nest subcontents
    content_buckets: dict[tuple, _Bucket] = {}
    sub_buckets: dict[tuple, dict[str, _Bucket]] = {}
    for q in questions:
        c = q["content_code"]
        if not c:
            continue                       # only ACTIVE curriculum-v2 content (s6/s11)
        key = (q["discipline_code"], q["area_code"], c)
        content_buckets.setdefault(key, _Bucket()).add(q)
        sc = q["subcontent_code"]
        if sc:                             # only when a valid subcontent code exists (s7)
            sub_buckets.setdefault(key, {}).setdefault(sc, _Bucket()).add(q)
    out = []
    for key in sorted(content_buckets, key=lambda k: tuple("" if x is None else x for x in k)):
        disc, area, content = key
        b = content_buckets[key]
        row = b.core(policy)
        subs = []
        for sc in sorted(sub_buckets.get(key, {})):
            sb = sub_buckets[key][sc]
            srow = sb.core(policy)
            srow.update({"subcontent_code": sc, "subcontent_name": name_of(sc),
                         "provisional_questions": sb.provisional})
            subs.append(srow)
        row.update({
            "discipline_code": disc,
            "area_code": area,
            "content_code": content,
            "content_name": name_of(content),
            "classification_confidence": b.confidence_breakdown(),
            "provisional_count": b.provisional,
            "forced_closure_count": b.forced_closure,
            "visual_dependency_count": b.visual_dependency,
            "classified_questions": b.classified,
            "subcontents": subs,
        })
        out.append(row)
    return out


def _verdicts(by_content: list[dict], by_discipline: list[dict]):
    """Deterministic strengths / improvements / intermediate / insufficient,
    from the finest reliable grain (content) plus discipline-level rollups.
    A single question can never yield a verdict (INSUFFICIENT_SAMPLE)."""
    strengths, improvements, intermediate, insufficient = [], [], [], []

    def entry(scope: str, row: dict) -> dict:
        return {
            "scope": scope,
            "discipline_code": row.get("discipline_code"),
            "area_code": row.get("area_code"),
            "content_code": row.get("content_code"),
            "content_name": row.get("content_name"),
            "discipline_name": row.get("discipline_name"),
            "accuracy": row["accuracy"],
            "answered": row["answered"],
            "total_questions": row["total_questions"],
            "band": row["band"],
        }

    for row in by_content:
        e = entry("content", row)
        if row["band"] == BAND_STRONG:
            strengths.append(e)
        elif row["band"] == BAND_IMPROVEMENT:
            improvements.append(e)
        elif row["band"] == BAND_INTERMEDIATE:
            intermediate.append(e)
        elif row["band"] == BAND_INSUFFICIENT:
            insufficient.append(e)
    for row in by_discipline:
        e = entry("discipline", row)
        if row["band"] == BAND_STRONG:
            strengths.append(e)
        elif row["band"] == BAND_IMPROVEMENT:
            improvements.append(e)
        elif row["band"] == BAND_INTERMEDIATE:
            intermediate.append(e)
        elif row["band"] == BAND_INSUFFICIENT:
            insufficient.append(e)

    strengths.sort(key=lambda x: (-(x["accuracy"] or 0), x["scope"], x["content_code"] or "", x["discipline_code"] or ""))
    improvements.sort(key=lambda x: ((x["accuracy"] or 0), x["scope"], x["content_code"] or "", x["discipline_code"] or ""))
    intermediate.sort(key=lambda x: (x["scope"], x["content_code"] or "", x["discipline_code"] or ""))
    insufficient.sort(key=lambda x: (x["scope"], x["content_code"] or "", x["discipline_code"] or ""))
    return strengths, improvements, intermediate, insufficient


def _class_aggregate(analyses: list[dict], policy: PerformanceThresholdPolicy) -> dict:
    """Deterministic roll-up of every student's per-question rows for one
    assignment. No ranking, no per-student comparison - just the same
    aggregation applied to the pooled questions."""
    pooled: list[dict] = []
    for a in analyses:
        pooled.extend(a["questions"])
    if not pooled:
        return {"summary": _summary([], policy), "by_discipline": [], "by_content": []}
    # name_of is embedded in each pooled question already (content_name etc.);
    # rebuild a name lookup from the rows themselves for the aggregate helpers.
    names = {}
    for q in pooled:
        for code_k, name_k in (("discipline_code", "discipline_name"),
                               ("content_code", "content_name"),
                               ("subcontent_code", "subcontent_name")):
            if q.get(code_k):
                names[q[code_k]] = q.get(name_k)
    name_of = lambda code: names.get(code)  # noqa: E731
    return {
        "student_count": len(analyses),
        "summary": _summary(pooled, policy),
        "by_discipline": _by_discipline(pooled, policy, name_of),
        "by_content": _by_content(pooled, policy, name_of),
    }


__all__ = [
    "AnalysisAuthError",
    "AnalysisError",
    "AnalysisNotFound",
    "PedagogicalAnalysisService",
    "PerformanceThresholdPolicy",
    "TEMPORAL_WINDOWS",
]
