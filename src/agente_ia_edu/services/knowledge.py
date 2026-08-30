from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy import select, and_, or_
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agente_ia_edu.db.models import (
    CatalogNode,
    ContentQuestionLink,
    ContentResourceLink,
    EducationalResource,
    PedagogicalClassification,
    Question,
    QuestionVersion,
    ResourceAccessGrant,
    Taxonomy,
    TaxonomyNode,
    VideoResourceDetail,
)

logger = logging.getLogger(__name__)


class KnowledgeService:
    """Knowledge Layer query and linkage service.

    Unifies relational queries across:
    - Pedagogical Catalog (CatalogNode, EducationalResource, ContentResourceLink, ContentQuestionLink)
    - AI Classifications (PedagogicalClassification)
    - Question Bank (Question, QuestionVersion)
    - Taxonomies (Taxonomy, TaxonomyNode)

    Enforces multi-tenancy visibility rules:
    - PUBLIC / PLATFORM resources are visible globally
    - PRIVATE / SCHOOL / INSTITUTION resources require matching owner or explicit grant
    """

    def __init__(self, session: AsyncSession):
        self.session = session

    # -------------------------------------------------------------------------
    # 1. QUESTION QUERIES
    # -------------------------------------------------------------------------

    async def find_questions_by_content(
        self,
        content_name_or_code: str,
        *,
        difficulty: str | None = None,
        institution_id: str | None = None,
        active_classification_only: bool = True,
    ) -> list[dict[str, Any]]:
        """Find questions associated with a content or subcontent name/code.

        Searches via:
        1. AI PedagogicalClassification (discipline/content/subcontent)
        2. CatalogNode & ContentQuestionLink
        3. TaxonomyNode (skills/competencies)
        """
        search_term = content_name_or_code.strip()

        # Query AI Classifications
        stmt = (
            select(PedagogicalClassification)
            .join(PedagogicalClassification.question_version)
            .options(selectinload(PedagogicalClassification.question_version))
            .where(
                or_(
                    PedagogicalClassification.content.ilike(f"%{search_term}%"),
                    PedagogicalClassification.subcontent.ilike(f"%{search_term}%"),
                    PedagogicalClassification.discipline.ilike(f"%{search_term}%"),
                )
            )
            .order_by(PedagogicalClassification.created_at.desc())
        )

        if active_classification_only:
            stmt = stmt.where(PedagogicalClassification.status == "CLASSIFIED")

        if difficulty:
            target_diff = difficulty.upper()
            stmt = stmt.where(
                or_(
                    PedagogicalClassification.difficulty == target_diff,
                    QuestionVersion.recommended_difficulty == target_diff,
                )
            )
            # Also filter CatalogNode links by QuestionVersion.recommended_difficulty if filtered by difficulty

        res = await self.session.execute(stmt)
        classifications = res.scalars().all()

        questions_list = []
        seen_qv_ids = set()

        for c in classifications:
            qv = c.question_version
            if qv and qv.id not in seen_qv_ids:
                seen_qv_ids.add(qv.id)
                questions_list.append({
                    "question_version_id": str(qv.id),
                    "statement": qv.statement or qv.canonical_text,
                    "difficulty_ai": c.difficulty,
                    "difficulty_learning_level": qv.recommended_difficulty,
                    "classification": {
                        "id": str(c.id),
                        "discipline": c.discipline,
                        "content": c.content,
                        "subcontent": c.subcontent,
                        "reasoning_type": c.reasoning_type,
                        "prerequisites": c.prerequisites,
                        "keywords": c.keywords,
                        "status": c.status,
                    },
                    "source_type": "ai_classification",
                })

        # Also search via CatalogNode -> ContentQuestionLink
        cat_stmt = (
            select(CatalogNode)
            .where(
                or_(
                    CatalogNode.name.ilike(f"%{search_term}%"),
                    CatalogNode.code.ilike(f"%{search_term}%"),
                )
            )
        )
        cat_res = await self.session.execute(cat_stmt)
        nodes = cat_res.scalars().all()

        if nodes:
            node_ids = [n.id for n in nodes]
            link_stmt = (
                select(ContentQuestionLink)
                .where(ContentQuestionLink.content_node_id.in_(node_ids))
                .options(selectinload(ContentQuestionLink.question_version))
            )
            if difficulty:
                link_stmt = link_stmt.join(QuestionVersion).where(
                    or_(
                        QuestionVersion.recommended_difficulty == difficulty.upper(),
                        QuestionVersion.id.in_(
                            select(PedagogicalClassification.question_version_id).where(
                                PedagogicalClassification.difficulty == difficulty.upper()
                            )
                        ),
                    )
                )

            link_res = await self.session.execute(link_stmt)
            links = link_res.scalars().all()

            for link in links:
                qv = link.question_version
                if qv and qv.id not in seen_qv_ids:
                    seen_qv_ids.add(qv.id)
                    questions_list.append({
                        "question_version_id": str(qv.id),
                        "statement": qv.statement or qv.canonical_text,
                        "difficulty_ai": None,
                        "difficulty_learning_level": qv.recommended_difficulty,
                        "classification": None,
                        "source_type": "catalog_link",
                    })

        return questions_list

    async def find_questions_by_difficulty(
        self,
        difficulty: str,
        *,
        content_name_or_code: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find questions filtered by difficulty (AI or learning level)."""
        target = difficulty.upper()

        if content_name_or_code:
            return await self.find_questions_by_content(
                content_name_or_code,
                difficulty=target,
            )

        stmt = select(QuestionVersion).where(
            or_(
                QuestionVersion.recommended_difficulty == target,
                QuestionVersion.id.in_(
                    select(PedagogicalClassification.question_version_id).where(
                        PedagogicalClassification.difficulty == target
                    )
                ),
            )
        )

        res = await self.session.execute(stmt)
        qvs = res.scalars().all()

        return [
            {
                "question_version_id": str(qv.id),
                "statement": qv.statement or qv.canonical_text,
                "difficulty_learning_level": qv.recommended_difficulty,
            }
            for qv in qvs
        ]

    # -------------------------------------------------------------------------
    # 2. EDUCATIONAL RESOURCE QUERIES (MATERIALS, VIDEOS, BOOKS)
    # -------------------------------------------------------------------------

    async def find_resources_by_content(
        self,
        content_name_or_code: str,
        *,
        resource_type: str | None = None,
        requester_institution_id: str | None = None,
    ) -> list[dict[str, Any]]:
        """Find educational resources linked to a content node.

        Applies multi-tenancy visibility filters.
        """
        search_term = content_name_or_code.strip()

        # Find matching catalog nodes
        cat_stmt = select(CatalogNode).where(
            or_(
                CatalogNode.name.ilike(f"%{search_term}%"),
                CatalogNode.code.ilike(f"%{search_term}%"),
            )
        )
        cat_res = await self.session.execute(cat_stmt)
        nodes = cat_res.scalars().all()
        if not nodes:
            return []

        node_ids = [n.id for n in nodes]

        link_stmt = (
            select(ContentResourceLink)
            .where(ContentResourceLink.content_node_id.in_(node_ids))
            .options(
                selectinload(ContentResourceLink.resource).selectinload(EducationalResource.video_detail),
                selectinload(ContentResourceLink.resource).selectinload(EducationalResource.access_grants),
            )
        )
        if resource_type:
            link_stmt = link_stmt.join(EducationalResource).where(
                EducationalResource.resource_type == resource_type.upper()
            )

        link_res = await self.session.execute(link_stmt)
        links = link_res.scalars().all()

        resources_list = []
        seen_res_ids = set()

        for link in links:
            res = link.resource
            if res and res.id not in seen_res_ids:
                if not self._is_resource_visible(res, requester_institution_id):
                    continue

                seen_res_ids.add(res.id)
                resources_list.append({
                    "resource_id": str(res.id),
                    "title": res.title,
                    "resource_type": res.resource_type,
                    "origin_type": res.origin_type,
                    "owner_external_id": res.owner_external_id,
                    "visibility_scope": res.visibility_scope,
                    "source_url": res.source_url,
                    "pedagogical_role": link.pedagogical_role,
                    "recommended_level": link.recommended_level,
                    "video_detail": {
                        "platform": res.video_detail.platform,
                        "external_video_id": res.video_detail.external_video_id,
                        "duration_seconds": res.video_detail.duration_seconds,
                    } if res.video_detail else None,
                })

        return resources_list

    # -------------------------------------------------------------------------
    # 3. LINKING HELPERS (RELATE QUESTION OR RESOURCE TO CONTENT)
    # -------------------------------------------------------------------------

    async def link_question_to_content(
        self,
        question_version_id: UUID,
        content_node_id: UUID,
    ) -> ContentQuestionLink:
        """Create link between question version and catalog content node."""
        stmt = select(ContentQuestionLink).where(
            ContentQuestionLink.question_version_id == question_version_id,
            ContentQuestionLink.content_node_id == content_node_id,
        )
        res = await self.session.execute(stmt)
        existing = res.scalar_one_or_none()
        if existing:
            return existing

        link = ContentQuestionLink(
            question_version_id=question_version_id,
            content_node_id=content_node_id,
        )
        self.session.add(link)
        await self.session.commit()
        return link

    async def link_resource_to_content(
        self,
        resource_id: UUID,
        content_node_id: UUID,
        pedagogical_role: str = "THEORY",
        *,
        recommended_level: str | None = None,
        priority: int = 1,
    ) -> ContentResourceLink:
        """Create link between educational resource and catalog content node."""
        stmt = select(ContentResourceLink).where(
            ContentResourceLink.resource_id == resource_id,
            ContentResourceLink.content_node_id == content_node_id,
            ContentResourceLink.pedagogical_role == pedagogical_role,
        )
        res = await self.session.execute(stmt)
        existing = res.scalar_one_or_none()
        if existing:
            return existing

        link = ContentResourceLink(
            resource_id=resource_id,
            content_node_id=content_node_id,
            pedagogical_role=pedagogical_role,
            recommended_level=recommended_level,
            priority=priority,
        )
        self.session.add(link)
        await self.session.commit()
        return link

    # -------------------------------------------------------------------------
    # 4. MULTI-TENANCY VISIBILITY CHECK
    # -------------------------------------------------------------------------

    @staticmethod
    def _is_resource_visible(
        resource: EducationalResource,
        requester_institution_id: str | None,
    ) -> bool:
        """Evaluates if requester_institution_id can view resource.

        Rules:
        1. PUBLIC or SHARED -> visible to all
        2. PLATFORM origin -> visible to all
        3. PRIVATE / SCHOOL / INSTITUTION -> visible if owner_external_id matches requester_institution_id
        4. Explicit ResourceAccessGrant matching requester_institution_id
        """
        if resource.visibility_scope in ("PUBLIC", "SHARED"):
            return True

        if resource.origin_type == "PLATFORM":
            return True

        if requester_institution_id and resource.owner_external_id == requester_institution_id:
            return True

        if requester_institution_id and resource.access_grants:
            for grant in resource.access_grants:
                if grant.grantee_external_id == requester_institution_id:
                    return True

        return False
