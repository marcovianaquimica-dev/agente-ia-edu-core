"""
Video Discovery Service and Provider Abstraction (Phase 10).

Responsible for:
- Transforming pedagogical context into search queries deterministically.
- Querying configured discovery providers (YouTube, YouTube EDU, Internal, Mock).
- Deduplicating video candidates deterministically by (source, external_id).
- Classifying candidates and managing state transitions:
  DISCOVERED -> PENDING_REVIEW -> CLASSIFIED -> APPROVED / REJECTED -> AVAILABLE
- Converting approved candidates into EducationalResource + VideoResourceDetail + ContentResourceLink
  so they can be safely indexed by Knowledge Layer and recommended by VideoRecommendationEngine.
"""

from __future__ import annotations

import logging
import uuid
from decimal import Decimal
from typing import Any, Protocol

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agente_ia_edu.db.models import (
    CatalogNode,
    ContentResourceLink,
    EducationalResource,
    ExternalVideoCandidate,
    VideoResourceDetail,
)
from agente_ia_edu.services.pedagogical_classifier import PedagogicalClassifierProvider

logger = logging.getLogger(__name__)

DEFAULT_CONFIDENCE_THRESHOLD = Decimal("0.70")


class CandidateStatus:
    DISCOVERED = "DISCOVERED"
    PENDING_REVIEW = "PENDING_REVIEW"
    CLASSIFIED = "CLASSIFIED"
    APPROVED = "APPROVED"
    REJECTED = "REJECTED"
    AVAILABLE = "AVAILABLE"


class VideoDiscoveryProvider(Protocol):
    """Protocol for external/internal video discovery providers."""

    name: str

    async def search(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        ...


class MockVideoDiscoveryProvider:
    """Mock discovery provider returning realistic candidate metadata deterministically."""

    def __init__(
        self,
        name: str = "YOUTUBE",
        candidates: list[dict[str, Any]] | None = None,
        should_fail: bool = False,
    ):
        self.name = name.upper()
        self.candidates = candidates
        self.should_fail = should_fail

    async def search(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        if self.should_fail:
            raise RuntimeError(f"Provider '{self.name}' failed to execute query: {query}")

        if self.candidates is not None:
            return self.candidates[:limit]

        # Default realistic mock candidates for query
        clean_q = query.strip()
        return [
            {
                "source": self.name,
                "external_id": f"yt_mock_{clean_q.lower().replace(' ', '_')}_1",
                "title": f"{clean_q} - Aula Completa",
                "description": f"Videoaula explicativa sobre {clean_q} com exercícios resolvidos.",
                "channel_or_author": "Canal Química do ENEM",
                "url": f"https://youtube.com/watch?v=yt_mock_{clean_q.lower().replace(' ', '_')}_1",
                "thumbnail_url": f"https://img.youtube.com/vi/yt_mock_1/0.jpg",
                "duration_seconds": 600,
                "language": "pt-BR",
            },
            {
                "source": self.name,
                "external_id": f"yt_mock_{clean_q.lower().replace(' ', '_')}_2",
                "title": f"{clean_q} - Exemplos Práticos",
                "description": f"Exemplos e exercícios práticos de {clean_q}.",
                "channel_or_author": "Professor Mendes",
                "url": f"https://youtube.com/watch?v=yt_mock_{clean_q.lower().replace(' ', '_')}_2",
                "thumbnail_url": f"https://img.youtube.com/vi/yt_mock_2/0.jpg",
                "duration_seconds": 450,
                "language": "pt-BR",
            },
        ][:limit]


class YouTubeDiscoveryProvider:
    """YouTube discovery provider adapter structure (unconfigured external calls return empty/mock)."""

    name = "YOUTUBE"

    def __init__(self, api_key: str | None = None):
        self.api_key = api_key

    async def search(
        self,
        query: str,
        *,
        context: dict[str, Any] | None = None,
        limit: int = 10,
    ) -> list[dict[str, Any]]:
        if not self.api_key:
            logger.info("YouTube API key not configured. Returning empty search result.")
            return []
        # Structural stub for future API HTTP client integration
        return []


class VideoDiscoveryService:
    """Service orchestrating discovery, query generation, classification, review, and conversion."""

    def __init__(
        self,
        session: AsyncSession,
        confidence_threshold: Decimal = DEFAULT_CONFIDENCE_THRESHOLD,
    ):
        self.session = session
        self.confidence_threshold = confidence_threshold

    @staticmethod
    def generate_search_query(
        content_name: str,
        discipline: str | None = None,
        language: str = "pt-BR",
    ) -> str:
        """Transforms pedagogical context into a clean, deterministic search query."""
        clean_content = content_name.strip()
        if discipline and discipline.strip() and discipline.strip().lower() not in clean_content.lower():
            return f"{clean_content} {discipline.strip()}"
        return clean_content

    async def discover_candidates(
        self,
        *,
        content_node_id: uuid.UUID | None = None,
        query: str | None = None,
        discipline: str | None = None,
        providers: list[VideoDiscoveryProvider] | None = None,
        classifier: PedagogicalClassifierProvider | None = None,
        limit_per_provider: int = 5,
    ) -> list[ExternalVideoCandidate]:
        """Queries configured providers, deduplicates candidates, classifies, and persists candidates."""
        if not providers:
            logger.info("No video discovery providers configured.")
            return []

        search_query = query
        node = None
        if content_node_id:
            node = await self.session.get(CatalogNode, content_node_id)
            if node and not search_query:
                search_query = self.generate_search_query(node.name, discipline=discipline)

        if not search_query:
            raise ValueError("Must provide either a content_node_id or a search query.")

        raw_candidates: list[dict[str, Any]] = []
        for provider in providers:
            try:
                found = await provider.search(search_query, limit=limit_per_provider)
                for f in found:
                    f.setdefault("source", getattr(provider, "name", "UNKNOWN").upper())
                    raw_candidates.append(f)
            except Exception as exc:
                logger.error("Provider '%s' error: %s", getattr(provider, "name", "UNKNOWN"), exc)
                continue

        if not raw_candidates:
            return []

        # Deduplicate raw candidates deterministically by (source, external_id)
        dedup_map: dict[tuple[str, str], dict[str, Any]] = {}
        for cand in raw_candidates:
            source = str(cand.get("source", "EXTERNAL")).upper()
            ext_id = str(cand.get("external_id", "")).strip()
            if not ext_id:
                continue
            key = (source, ext_id)
            if key not in dedup_map:
                dedup_map[key] = cand

        persisted_candidates: list[ExternalVideoCandidate] = []

        for (source, ext_id), raw in dedup_map.items():
            # Check DB for existing candidate
            stmt_existing = select(ExternalVideoCandidate).where(
                ExternalVideoCandidate.source == source,
                ExternalVideoCandidate.external_id == ext_id,
            )
            res_existing = await self.session.execute(stmt_existing)
            existing = res_existing.scalar_one_or_none()

            if existing:
                persisted_candidates.append(existing)
                continue

            # Classify candidate if classifier provided
            confidence = Decimal("0.0000")
            recommended_difficulty = "EASY"
            status = CandidateStatus.DISCOVERED

            if classifier:
                try:
                    classification = await classifier.classify(
                        question_text=f"{raw.get('title', '')}\n{raw.get('description', '')}",
                        model_name="mock-classifier",
                    )
                    confidence = Decimal(str(classification.get("classification_confidence", 0.0)))
                    recommended_difficulty = str(classification.get("difficulty", "EASY")).upper()
                    if recommended_difficulty not in ("EASY", "MEDIUM", "HARD"):
                        recommended_difficulty = "EASY"

                    if confidence >= self.confidence_threshold:
                        status = CandidateStatus.CLASSIFIED
                    else:
                        status = CandidateStatus.PENDING_REVIEW
                except Exception as exc:
                    logger.warning("Failed to classify candidate (%s, %s): %s", source, ext_id, exc)
                    status = CandidateStatus.PENDING_REVIEW
            else:
                status = CandidateStatus.PENDING_REVIEW

            candidate_record = ExternalVideoCandidate(
                source=source,
                external_id=ext_id,
                title=raw.get("title", "Sem título")[:500],
                description=raw.get("description"),
                channel_or_author=raw.get("channel_or_author"),
                url=raw.get("url", f"https://{source.lower()}.com/watch?v={ext_id}"),
                thumbnail_url=raw.get("thumbnail_url"),
                duration_seconds=raw.get("duration_seconds"),
                language=raw.get("language", "pt-BR"),
                status=status,
                classification_confidence=confidence,
                recommended_difficulty=recommended_difficulty,
                content_node_id=content_node_id,
                metadata_={"query": search_query, "raw_payload": raw},
            )
            self.session.add(candidate_record)
            persisted_candidates.append(candidate_record)

        await self.session.commit()
        for cand in persisted_candidates:
            await self.session.refresh(cand)

        # Deterministic sort by (source, external_id)
        persisted_candidates.sort(key=lambda c: (c.source, c.external_id))
        return persisted_candidates

    async def review_candidate(
        self,
        candidate_id: uuid.UUID,
        action: str,
        reasoning: str | None = None,
    ) -> ExternalVideoCandidate:
        """Approve or reject a discovered video candidate."""
        candidate = await self.session.get(ExternalVideoCandidate, candidate_id)
        if not candidate:
            raise ValueError(f"ExternalVideoCandidate not found: {candidate_id}")

        norm_action = action.upper()
        if norm_action == "APPROVE":
            candidate.status = CandidateStatus.APPROVED
        elif norm_action == "REJECT":
            candidate.status = CandidateStatus.REJECTED
        else:
            raise ValueError(f"Invalid review action: {action}. Must be APPROVE or REJECT.")

        meta = candidate.metadata_ or {}
        meta["review_action"] = norm_action
        meta["review_reasoning"] = reasoning
        candidate.metadata_ = dict(meta)

        await self.session.commit()
        await self.session.refresh(candidate)
        return candidate

    async def approve_and_convert_candidate(
        self,
        candidate_id: uuid.UUID,
        content_node_id: uuid.UUID | None = None,
        *,
        origin_type: str = "EXTERNAL",
        visibility_scope: str = "PUBLIC",
        owner_external_id: str | None = None,
        recommended_level: str | None = None,
    ) -> tuple[EducationalResource, ContentResourceLink]:
        """Converts an approved candidate into an EducationalResource, VideoResourceDetail, and ContentResourceLink."""
        candidate = await self.session.get(ExternalVideoCandidate, candidate_id)
        if not candidate:
            raise ValueError(f"ExternalVideoCandidate not found: {candidate_id}")

        if candidate.status == CandidateStatus.REJECTED:
            raise ValueError("Cannot convert a REJECTED video candidate to catalog resource.")

        target_node_id = content_node_id or candidate.content_node_id
        if not target_node_id:
            raise ValueError("Must specify a content_node_id to convert video candidate to catalog resource.")

        node = await self.session.get(CatalogNode, target_node_id)
        if not node:
            raise ValueError(f"CatalogNode not found: {target_node_id}")

        # If already converted, return existing
        if candidate.converted_resource_id:
            res = await self.session.get(EducationalResource, candidate.converted_resource_id)
            stmt_link = select(ContentResourceLink).where(
                ContentResourceLink.resource_id == candidate.converted_resource_id,
                ContentResourceLink.content_node_id == target_node_id,
            )
            res_link = await self.session.execute(stmt_link)
            link = res_link.scalar_one_or_none()
            if res and link:
                return res, link

        # Create EducationalResource
        res = EducationalResource(
            title=candidate.title,
            description=candidate.description,
            resource_type="VIDEO",
            origin_type=origin_type.upper(),
            owner_external_id=owner_external_id,
            visibility_scope=visibility_scope.upper(),
            source_url=candidate.url,
            author=candidate.channel_or_author,
            status="active",
            metadata_={"candidate_id": str(candidate.id), "source": candidate.source},
        )
        self.session.add(res)
        await self.session.flush()

        # Create VideoResourceDetail
        video_detail = VideoResourceDetail(
            resource_id=res.id,
            platform=candidate.source.upper() if candidate.source.upper() in ("YOUTUBE", "OWN", "PLATFORM", "LICENSED", "EXTERNAL") else "EXTERNAL",
            external_video_id=candidate.external_id,
            duration_seconds=candidate.duration_seconds,
        )
        self.session.add(video_detail)

        # Create ContentResourceLink
        rec_diff = recommended_level or candidate.recommended_difficulty or "EASY"
        link = ContentResourceLink(
            content_node_id=target_node_id,
            resource_id=res.id,
            pedagogical_role="VIDEO",
            recommended_level=rec_diff,
        )
        self.session.add(link)

        # Update Candidate Status
        candidate.status = CandidateStatus.AVAILABLE
        candidate.converted_resource_id = res.id

        await self.session.commit()

        # Reload EducationalResource with video_detail loaded
        stmt_res = (
            select(EducationalResource)
            .where(EducationalResource.id == res.id)
            .options(selectinload(EducationalResource.video_detail))
        )
        res_loaded = (await self.session.execute(stmt_res)).scalar_one()
        await self.session.refresh(link)

        return res_loaded, link
