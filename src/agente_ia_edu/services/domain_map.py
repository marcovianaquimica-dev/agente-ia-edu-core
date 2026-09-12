"""Read-only projection from learning evidence to an explainable domain map."""

from __future__ import annotations

import unicodedata
from collections import defaultdict
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

from sqlalchemy import and_, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import (
    CatalogNode,
    InitialDiagnostic,
    LearningHistory,
    PedagogicalContext,
    PedagogicalUniverseCatalogScope,
    StudentContentMastery,
)
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.learning_path_policies import (
    NextBestActionCandidate,
    NextBestActionPolicy,
)
from agente_ia_edu.services.pedagogical_universe import PedagogicalUniverseService
from agente_ia_edu.services.teaching_context_policies import (
    ContextPriorityPolicy,
    RecencyPolicy,
)


class DomainMapService:
    """Combine existing evidence, mastery, catalog, and context without rescoring mastery."""

    def __init__(
        self,
        session: AsyncSession,
        *,
        policy: NextBestActionPolicy | None = None,
        recency_policy: RecencyPolicy | None = None,
        context_priority_policy: ContextPriorityPolicy | None = None,
        now_provider: Callable[[], datetime] | None = None,
    ):
        self.session = session
        self.policy = policy or NextBestActionPolicy()
        self.recency_policy = recency_policy or RecencyPolicy()
        self.context_priority_policy = context_priority_policy or ContextPriorityPolicy()
        self.now_provider = now_provider or (lambda: datetime.now(timezone.utc))

    async def build(
        self,
        *,
        student_id: str,
        identity: ExternalIdentityContext,
    ) -> dict[str, Any]:
        nodes = list((await self.session.scalars(
            select(CatalogNode).where(CatalogNode.active.is_(True)).order_by(
                CatalogNode.position, CatalogNode.name, CatalogNode.id
            )
        )).all())
        node_by_id = {node.id: node for node in nodes}
        actionable_nodes = [
            node for node in nodes
            if node.parent_id is not None and node.node_type.upper() not in {"AREA", "DISCIPLINE"}
        ]

        universe = await self._authorized_universe(identity)
        if universe:
            actionable_nodes = await self._filter_universe_nodes(
                actionable_nodes, node_by_id, universe.id
            )

        masteries = list((await self.session.scalars(
            select(StudentContentMastery).where(
                StudentContentMastery.external_identity_id == student_id
            )
        )).all())
        mastery_by_node = {item.content_node_id: item for item in masteries}

        history = list((await self.session.scalars(
            select(LearningHistory).where(
                LearningHistory.external_identity_id == student_id
            ).order_by(LearningHistory.created_at, LearningHistory.id)
        )).all())
        history_by_node: dict[Any, list[LearningHistory]] = defaultdict(list)
        for item in history:
            if item.content_node_id is not None:
                history_by_node[item.content_node_id].append(item)

        latest_diagnostic = await self.session.scalar(
            select(InitialDiagnostic).where(
                InitialDiagnostic.student_id == student_id
            ).order_by(InitialDiagnostic.created_at.desc(), InitialDiagnostic.id).limit(1)
        )
        diagnostic_metadata = latest_diagnostic.metadata_ if latest_diagnostic else {}
        objective_terms, objective_node_ids = self._objective_data(diagnostic_metadata or {})
        hypotheses = self._hypotheses_by_target(diagnostic_metadata or {})
        contexts = await self._active_contexts(identity)
        contexts_by_node: dict[Any, list[PedagogicalContext]] = defaultdict(list)
        for context in contexts:
            contexts_by_node[context.content_node_id].append(context)

        contents: list[dict[str, Any]] = []
        now = self._as_utc(self.now_provider())
        for node in actionable_nodes:
            entries = history_by_node.get(node.id, [])
            mastery = mastery_by_node.get(node.id)
            score = float(mastery.mastery_score) if mastery else None
            confidence = float(mastery.confidence) if mastery else 0.0
            unknown_count = sum(item.response_text == "UNKNOWN" for item in entries)
            correct_count = sum(item.is_correct is True for item in entries)
            error_count = sum(
                item.is_correct is False and item.response_text != "UNKNOWN"
                for item in entries
            )
            trend, trend_delta = self._trend(entries)
            origins = {
                origin: sum(item.activity_type == origin for item in entries)
                for origin in (
                    "INITIAL_DIAGNOSTIC",
                    "OFFICIAL_ASSESSMENT",
                    "INDIVIDUAL_PRACTICE",
                )
            }
            evidence_contexts = self._evidence_contexts(entries)
            node_contexts = sorted(
                contexts_by_node.get(node.id, []),
                key=lambda item: (
                    self.context_priority_policy.get_rank(item.source),
                    -self._as_utc(item.recorded_at).timestamp(),
                    str(item.id),
                ),
            )
            context_payload = [
                {
                    "source": item.source,
                    "title": item.title,
                    "recorded_at": item.recorded_at,
                }
                for item in node_contexts
            ]
            objective_aligned = self._is_objective_aligned(
                node, objective_terms, objective_node_ids
            )
            prerequisite = self._prerequisite_payload(
                hypotheses.get(str(node.id)), node_by_id, mastery_by_node, history_by_node
            )
            evidence_count = len(entries)
            state = self._state(score, confidence, evidence_count)
            recent_practice_count = sum(
                item.activity_type == "INDIVIDUAL_PRACTICE"
                and self._as_utc(item.created_at) >= now - timedelta(days=30)
                for item in entries
            )
            discipline, area = self._ancestry(node, node_by_id)
            contents.append({
                "content_node_id": node.id,
                "content_name": node.name,
                "node_type": node.node_type,
                "discipline_id": discipline.id if discipline else None,
                "discipline_name": discipline.name if discipline else None,
                "area_id": area.id if area else None,
                "area_name": area.name if area else None,
                "mastery_score": score,
                "confidence": confidence,
                "evidence_count": evidence_count,
                "correct_count": correct_count,
                "error_count": error_count,
                "unknown_count": unknown_count,
                "evidence_origins": origins,
                "evidence_contexts": evidence_contexts,
                "trend": trend,
                "trend_delta": trend_delta,
                "state": state,
                "is_gap": state == "POSSIBLE_GAP",
                "last_evidence_at": entries[-1].created_at if entries else None,
                "pedagogical_contexts": context_payload,
                "objective_aligned": objective_aligned,
                "prerequisites": [prerequisite] if prerequisite else [],
                "recent_practice_count": recent_practice_count,
            })

        decision = self.policy.decide([
            self._policy_candidate(item) for item in contents
        ])
        return {
            "student_id": student_id,
            "school_id": identity.institution_id,
            "is_independent": identity.institution_id is None,
            "universe": ({
                "id": universe.id,
                "name": universe.name,
                "configuration_version": universe.configuration_version,
            } if universe else None),
            "generated_at": now,
            "summary": {
                "content_count": len(contents),
                "mastered_count": sum(item["state"] == "MASTERED" for item in contents),
                "gap_count": sum(item["state"] == "POSSIBLE_GAP" for item in contents),
                "low_evidence_count": sum(
                    item["state"] in {"NOT_EVALUATED", "LOW_EVIDENCE"}
                    for item in contents
                ),
            },
            "contents": contents,
            "next_best_action": decision,
        }

    async def _authorized_universe(self, identity: ExternalIdentityContext):
        universes = await PedagogicalUniverseService(self.session).authorized_universes(identity)
        return universes[0] if universes else None

    async def _filter_universe_nodes(self, nodes, node_by_id, universe_id):
        scopes = list((await self.session.scalars(
            select(PedagogicalUniverseCatalogScope).where(
                PedagogicalUniverseCatalogScope.universe_id == universe_id
            )
        )).all())
        allowed = []
        for node in nodes:
            ancestors = {node.id}
            current = node
            while current.parent_id and current.parent_id in node_by_id:
                ancestors.add(current.parent_id)
                current = node_by_id[current.parent_id]
            if any(
                scope.catalog_node_id == node.id
                or (scope.include_descendants and scope.catalog_node_id in ancestors)
                for scope in scopes
            ):
                allowed.append(node)
        return allowed

    async def _active_contexts(self, identity: ExternalIdentityContext):
        if identity.institution_id is None:
            return []
        conditions = [
            PedagogicalContext.active.is_(True),
            PedagogicalContext.institution_id == identity.institution_id,
        ]
        if identity.classroom_id:
            conditions.append(or_(
                PedagogicalContext.classroom_id == identity.classroom_id,
                PedagogicalContext.classroom_id.is_(None),
            ))
        else:
            conditions.append(PedagogicalContext.classroom_id.is_(None))
        contexts = list((await self.session.scalars(
            select(PedagogicalContext).where(and_(*conditions))
        )).all())
        return [
            context for context in contexts
            if self.recency_policy.is_recent(context.recorded_at, self.now_provider())
        ]

    @staticmethod
    def _objective_data(metadata):
        profile = metadata.get("entry_profile", {})
        terms = tuple(
            DomainMapService._normalize(str(item))
            for item in profile.get("study_objectives", [])
            if item
        )
        resolved = metadata.get("preferred_content_resolution", {})
        node_ids = {str(resolved["node_id"])} if resolved.get("node_id") else set()
        return terms, node_ids

    @staticmethod
    def _hypotheses_by_target(metadata):
        priority = {"SUPPORTED": 0, "UNDER_INVESTIGATION": 1, "SUSPECTED": 2, "INCONCLUSIVE": 3, "REFUTED": 4}
        result = {}
        for item in metadata.get("prerequisite_hypotheses", []):
            target = item.get("target_content_id")
            if not target:
                continue
            current = result.get(target)
            if current is None or priority.get(item.get("status"), 9) < priority.get(current.get("status"), 9):
                result[target] = item
        return result

    @staticmethod
    def _evidence_contexts(entries):
        grouped = {}
        for item in entries:
            key = (
                item.activity_type,
                str(item.assessment_attempt_id) if item.assessment_attempt_id else None,
                str(item.practice_session_id) if item.practice_session_id else None,
            )
            payload = grouped.setdefault(key, {
                "activity_type": item.activity_type,
                "assessment_attempt_id": item.assessment_attempt_id,
                "practice_session_id": item.practice_session_id,
                "evidence_count": 0,
                "last_evidence_at": item.created_at,
            })
            payload["evidence_count"] += 1
            payload["last_evidence_at"] = max(
                DomainMapService._as_utc(payload["last_evidence_at"]),
                DomainMapService._as_utc(item.created_at),
            )
        return [grouped[key] for key in sorted(grouped, key=lambda value: tuple(str(part or "") for part in value))]

    @staticmethod
    def _trend(entries):
        """Compare chronological halves of at least four non-UNKNOWN outcomes."""
        outcomes = [item.is_correct for item in entries if item.response_text != "UNKNOWN" and item.is_correct is not None]
        if len(outcomes) < 4:
            return "INSUFFICIENT_EVIDENCE", None
        midpoint = len(outcomes) // 2
        previous = sum(outcomes[:midpoint]) / midpoint * 100
        recent = sum(outcomes[midpoint:]) / len(outcomes[midpoint:]) * 100
        delta = round(recent - previous, 1)
        return ("IMPROVING" if delta > 10 else "DECLINING" if delta < -10 else "STABLE"), delta

    @staticmethod
    def _state(score, confidence, evidence_count):
        if evidence_count == 0 or score is None:
            return "NOT_EVALUATED"
        if confidence < 0.3:
            return "LOW_EVIDENCE"
        if score < 40:
            return "POSSIBLE_GAP"
        if score >= 85 and confidence >= 0.6:
            return "MASTERED"
        return "DEVELOPING"

    @staticmethod
    def _prerequisite_payload(hypothesis, node_by_id, mastery_by_node, history_by_node):
        if not hypothesis:
            return None
        prerequisite_id = hypothesis.get("prerequisite_content_id")
        if not prerequisite_id:
            return None
        node = next((item for key, item in node_by_id.items() if str(key) == str(prerequisite_id)), None)
        if node is None:
            return None
        mastery = mastery_by_node.get(node.id)
        return {
            "content_node_id": node.id,
            "content_name": node.name,
            "hypothesis_status": hypothesis.get("status", "SUSPECTED"),
            "hypothesis_confidence": float(hypothesis.get("confidence", 0.0)),
            "mastery_score": float(mastery.mastery_score) if mastery else None,
            "confidence": float(mastery.confidence) if mastery else 0.0,
            "evidence_count": len(history_by_node.get(node.id, [])),
        }

    @staticmethod
    def _policy_candidate(item):
        prerequisite = item["prerequisites"][0] if item["prerequisites"] else None
        return NextBestActionCandidate(
            content_node_id=str(item["content_node_id"]),
            content_name=item["content_name"],
            mastery_score=item["mastery_score"],
            confidence=item["confidence"],
            evidence_count=item["evidence_count"],
            trend=item["trend"],
            context_sources=tuple(ctx["source"] for ctx in item["pedagogical_contexts"]),
            objective_aligned=item["objective_aligned"],
            recent_practice_count=item["recent_practice_count"],
            prerequisite_node_id=str(prerequisite["content_node_id"]) if prerequisite else None,
            prerequisite_name=prerequisite["content_name"] if prerequisite else None,
            prerequisite_mastery_score=prerequisite["mastery_score"] if prerequisite else None,
            prerequisite_confidence=prerequisite["confidence"] if prerequisite else 0.0,
            prerequisite_evidence_count=prerequisite["evidence_count"] if prerequisite else 0,
            prerequisite_hypothesis_status=prerequisite["hypothesis_status"] if prerequisite else None,
        )

    @staticmethod
    def _ancestry(node, node_by_id):
        discipline = node_by_id.get(node.root_id)
        area = None
        current = node
        while current.parent_id and current.parent_id in node_by_id:
            current = node_by_id[current.parent_id]
            if current.node_type.upper() == "AREA":
                area = current
        return discipline, area

    @staticmethod
    def _is_objective_aligned(node, terms, node_ids):
        if str(node.id) in node_ids:
            return True
        name = DomainMapService._normalize(node.name or "")
        return any(name in term or term in name for term in terms)

    @staticmethod
    def _normalize(value):
        normalized = unicodedata.normalize("NFKD", value.strip().lower())
        return "".join(char for char in normalized if unicodedata.category(char) != "Mn")

    @staticmethod
    def _as_utc(value):
        return value.replace(tzinfo=timezone.utc) if value.tzinfo is None else value


__all__ = ["DomainMapService"]