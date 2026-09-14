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

    @staticmethod
    @staticmethod
    def _scope_values(scope_value: str | tuple[str, ...] | None) -> tuple[str, ...]:
        if scope_value is None:
            return ()
        if isinstance(scope_value, tuple):
            return tuple(v for v in scope_value if v)
        return (scope_value,)

    @staticmethod
    def _is_question_visible(
        question: Question,
        requester_institution_id: str | None = None,
        requester_scope_type: str | None = None,
        requester_scope_external_id: str | tuple[str, ...] | None = None,
    ) -> bool:
        """Visibility rules for school-scoped and classroom-scoped questions."""
        if question.visibility_scope == "PUBLIC":
            return True

        # Legacy/default platform questions may exist without an explicit school owner or
        # public visibility flag. They should remain visible in the generic discovery layer
        # unless a stricter tenant or classroom scope is actually applied.
        if question.school_id is None and not question.owner_external_id:
            if question.origin_type in {"PLATFORM", "IMPORTED", "GENERATED"}:
                return True

        if question.visibility_scope == "PRIVATE":
            if question.owner_external_id and requester_institution_id and question.owner_external_id == requester_institution_id:
                return True
            if question.school_id and requester_institution_id and str(question.school_id) == str(requester_institution_id):
                return True
            if question.school_id is None and not question.owner_external_id:
                return True
            return False

        if question.school_id is None:
            return question.origin_type == "PLATFORM" and question.visibility_scope == "PUBLIC"

        if requester_institution_id is None:
            return False

        if str(question.school_id) != str(requester_institution_id):
            return False

        if question.visibility_scope == "SCHOOL":
            return True

        if question.visibility_scope == "CLASSROOM":
            classroom_id = (question.metadata_ or {}).get("classroom_id") if isinstance(question.metadata_, dict) else None
            scope_values = KnowledgeService._scope_values(requester_scope_external_id)
            return bool(
                requester_scope_type and requester_scope_type.upper() == "CLASSROOM"
                and classroom_id
                and classroom_id in scope_values
            )

        return False

    async def find_questions_by_content(
        self,
        content_name_or_code: str,
        *,
        difficulty: str | None = None,
        institution_id: str | None = None,
        requester_institution_id: str | None = None,
        requester_scope_type: str | None = None,
        requester_scope_external_id: str | None = None,
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
        # `lifecycle == "ACTIVE"` excludes superseded (corrected-away) rows so
        # a stale, incorrect classification can never resurface here just
        # because its old content/subcontent text still matches the search
        # term - this is an explicit selector, not an artifact of ordering.
        stmt = (
            select(PedagogicalClassification)
            .join(PedagogicalClassification.question_version)
            .options(
                selectinload(PedagogicalClassification.question_version).selectinload(QuestionVersion.question)
            )
            .where(
                PedagogicalClassification.lifecycle == "ACTIVE",
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
                question = qv.question
                if not self._is_question_visible(
                    question,
                    requester_institution_id=requester_institution_id or institution_id,
                    requester_scope_type=requester_scope_type,
                    requester_scope_external_id=requester_scope_external_id,
                ):
                    continue
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
                .options(
                    selectinload(ContentQuestionLink.question_version).selectinload(QuestionVersion.question)
                )
            )
            if difficulty:
                link_stmt = link_stmt.join(QuestionVersion).where(
                    or_(
                        QuestionVersion.recommended_difficulty == difficulty.upper(),
                        QuestionVersion.id.in_(
                            select(PedagogicalClassification.question_version_id).where(
                                PedagogicalClassification.difficulty == difficulty.upper(),
                                PedagogicalClassification.lifecycle == "ACTIVE",
                            )
                        ),
                    )
                )

            link_res = await self.session.execute(link_stmt)
            links = link_res.scalars().all()

            # A question version can be linked to several catalog nodes, and this
            # query is unordered. Emitting the row once with whichever link came
            # back first would hand a content gate one arbitrary node and hide
            # the question from a school entitled to it through another - a
            # denial decided by row order. Every matched node is collected onto
            # the single projected row instead.
            rows_by_qv: dict[Any, dict[str, Any]] = {}

            for link in links:
                qv = link.question_version
                if not qv:
                    continue
                node_id = str(link.content_node_id)
                existing = rows_by_qv.get(qv.id)
                if existing is not None:
                    if node_id not in existing["gate_content_node_ids"]:
                        existing["gate_content_node_ids"].append(node_id)
                    continue
                if qv.id in seen_qv_ids:
                    continue
                question = qv.question
                if not self._is_question_visible(
                    question,
                    requester_institution_id=requester_institution_id or institution_id,
                    requester_scope_type=requester_scope_type,
                    requester_scope_external_id=requester_scope_external_id,
                ):
                    continue
                seen_qv_ids.add(qv.id)
                row = {
                    "question_version_id": str(qv.id),
                    "statement": qv.statement or qv.canonical_text,
                    "difficulty_ai": None,
                    "difficulty_learning_level": qv.recommended_difficulty,
                    "classification": None,
                    # These rows are selected *by* their catalog nodes, so the
                    # nodes are known here. Dropping them made the row read as
                    # unclassified to any consumer that gates on content, which
                    # is the opposite of the truth: it is classified content
                    # whose classification lives on the links.
                    #
                    # The key is deliberately named for the gate rather than
                    # ``content_node_id``: that name already means "the single
                    # node this row is about" to the ranking engines, and
                    # introducing it here would silently switch on scoring
                    # branches that have never fired.
                    "gate_content_node_ids": [node_id],
                    "source_type": "catalog_link",
                }
                rows_by_qv[qv.id] = row
                questions_list.append(row)

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
                        PedagogicalClassification.difficulty == target,
                        PedagogicalClassification.lifecycle == "ACTIVE",
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
        requester_scope_type: str | None = None,
        requester_scope_external_id: str | None = None,
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
        # Same reason as the catalog-link questions above: one resource can be
        # linked to several matched nodes, and first-link-wins would let an
        # unordered query decide which discipline the row appears to belong to.
        rows_by_res: dict[Any, dict[str, Any]] = {}

        for link in links:
            res = link.resource
            if not res:
                continue
            node_id = str(link.content_node_id)
            existing = rows_by_res.get(res.id)
            if existing is not None:
                if node_id not in existing["gate_content_node_ids"]:
                    existing["gate_content_node_ids"].append(node_id)
                continue
            if res.id in seen_res_ids:
                continue
            if not self._is_resource_visible(
                res,
                requester_institution_id=requester_institution_id,
                requester_scope_type=requester_scope_type,
                requester_scope_external_id=requester_scope_external_id,
            ):
                continue

            seen_res_ids.add(res.id)
            row = {
                "resource_id": str(res.id),
                # The link rows this projection is built from already carry the
                # content nodes they were selected by, and consumers that gate
                # on content have no other way to know them. Named for the gate
                # on purpose - ``content_node_id`` is the ranking engines' key
                # and means something narrower there.
                "gate_content_node_ids": [node_id],
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
            }
            rows_by_res[res.id] = row
            resources_list.append(row)

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
    @staticmethod
    def _is_resource_visible(
        resource: EducationalResource,
        requester_institution_id: str | None = None,
        requester_scope_type: str | None = None,
        requester_scope_external_id: str | tuple[str, ...] | None = None,
    ) -> bool:
        """Evaluates if the current requester can view a published resource.

        The project already models audience control through EducationalResource.visibility_scope
        plus ResourceAccessGrant. This keeps distribution semantics consistent with the real
        multi-tenant context (school, segment, grade, classroom) without introducing a parallel catalog.
        """
        if resource.visibility_scope in ("PUBLIC", "SHARED"):
            return True

        if resource.origin_type == "PLATFORM":
            return True

        requester_school = (requester_institution_id or "").strip()
        requester_scope = (requester_scope_type or "").upper()
        scope_values = KnowledgeService._scope_values(requester_scope_external_id)
        requester_scope_ids = tuple((v or "").strip() for v in scope_values)

        if (
            resource.visibility_scope not in {"CLASSROOM"}
            and requester_school
            and resource.owner_external_id == requester_school
        ):
            return True

        if (
            resource.visibility_scope not in {"CLASSROOM"}
            and resource.owner_external_id
            and resource.owner_external_id in requester_scope_ids
        ):
            return True

        grants = resource.__dict__.get("access_grants") or []
        if not isinstance(grants, list):
            try:
                grants = list(grants)
            except Exception:
                grants = []

        if grants:
            for grant in grants:
                grant_type = (grant.grantee_type or "").upper()
                grant_id = (grant.grantee_external_id or "").strip()

                if requester_school and grant_id == requester_school:
                    if grant_type in {"INSTITUTION", "SCHOOL", "SCHOOL_UNIT"}:
                        return True

                if requester_scope_ids and grant_id in requester_scope_ids:
                    if grant_type in {"CLASSROOM", "GRADE_LEVEL", "SEGMENT", "UNIT", "SCHOOL", "INSTITUTION"}:
                        return True

                if requester_scope and grant_type == requester_scope:
                    if grant_id and requester_scope_ids and grant_id in requester_scope_ids:
                        return True

                if resource.visibility_scope == "CLASSROOM" and grant_type == "CLASSROOM":
                    if requester_scope == "CLASSROOM" and grant_id in requester_scope_ids:
                        return True

        # Legacy behavior: private/school resources are visible to their owning school even when
        # the caller only supplies the institutional id and no scope metadata.
        if resource.visibility_scope in {"PRIVATE", "SCHOOL", "INSTITUTION"} and requester_school:
            if resource.owner_external_id == requester_school:
                return True

        return False
