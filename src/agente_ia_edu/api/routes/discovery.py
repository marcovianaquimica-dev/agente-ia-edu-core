"""
API routes for Video Discovery & Candidate Management.
"""

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException

from ..dependencies import get_current_identity, get_session_factory
from ..schemas.discovery import (
    ExternalVideoCandidateResponse,
    VideoCandidateConvertRequest,
    VideoCandidateReviewRequest,
    VideoDiscoveryRequest,
)
from ...identity import ExternalIdentityContext
from ...services.video_discovery import (
    MockVideoDiscoveryProvider,
    VideoDiscoveryService,
)

discovery_router = APIRouter(
    prefix="/api/v1/discovery",
    tags=["discovery"],
)


@discovery_router.post(
    "/search",
    response_model=list[ExternalVideoCandidateResponse],
    summary="Discover video candidates for a content node or query",
    description="Queries configured providers, deduplicates candidates, classifies, and persists discovery candidates.",
)
async def discover_videos(
    request: VideoDiscoveryRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        discovery_service = VideoDiscoveryService(session)
        # Use default mock provider for discovery API
        mock_provider = MockVideoDiscoveryProvider()
        candidates = await discovery_service.discover_candidates(
            content_node_id=request.content_node_id,
            query=request.query,
            discipline=request.discipline,
            providers=[mock_provider],
            limit_per_provider=request.limit_per_provider,
        )
        return [
            ExternalVideoCandidateResponse(
                id=c.id,
                source=c.source,
                external_id=c.external_id,
                title=c.title,
                description=c.description,
                channel_or_author=c.channel_or_author,
                url=c.url,
                thumbnail_url=c.thumbnail_url,
                duration_seconds=c.duration_seconds,
                status=c.status,
                classification_confidence=float(c.classification_confidence) if c.classification_confidence is not None else None,
                recommended_difficulty=c.recommended_difficulty,
                content_node_id=c.content_node_id,
                converted_resource_id=c.converted_resource_id,
                created_at=c.created_at,
            )
            for c in candidates
        ]


@discovery_router.post(
    "/review",
    response_model=ExternalVideoCandidateResponse,
    summary="Review (approve/reject) a video candidate",
)
async def review_candidate(
    request: VideoCandidateReviewRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        discovery_service = VideoDiscoveryService(session)
        cand = await discovery_service.review_candidate(
            candidate_id=request.candidate_id,
            action=request.action,
            reasoning=request.reasoning,
        )
        return ExternalVideoCandidateResponse(
            id=cand.id,
            source=cand.source,
            external_id=cand.external_id,
            title=cand.title,
            description=cand.description,
            channel_or_author=cand.channel_or_author,
            url=cand.url,
            thumbnail_url=cand.thumbnail_url,
            duration_seconds=cand.duration_seconds,
            status=cand.status,
            classification_confidence=float(cand.classification_confidence) if cand.classification_confidence is not None else None,
            recommended_difficulty=cand.recommended_difficulty,
            content_node_id=cand.content_node_id,
            converted_resource_id=cand.converted_resource_id,
            created_at=cand.created_at,
        )


@discovery_router.post(
    "/convert",
    summary="Convert approved candidate to catalog resource",
)
async def convert_candidate(
    request: VideoCandidateConvertRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        discovery_service = VideoDiscoveryService(session)
        res, link = await discovery_service.approve_and_convert_candidate(
            candidate_id=request.candidate_id,
            content_node_id=request.content_node_id,
            origin_type=request.origin_type,
            visibility_scope=request.visibility_scope,
            owner_external_id=request.owner_external_id,
            recommended_level=request.recommended_level,
        )
        return {
            "resource_id": str(res.id),
            "link_id": str(link.id),
            "content_node_id": str(link.content_node_id),
            "title": res.title,
            "status": "AVAILABLE",
        }
