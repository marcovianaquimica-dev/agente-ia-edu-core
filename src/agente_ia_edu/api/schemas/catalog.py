"""
Pydantic schemas for the Pedagogical Catalog API.
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


# ============================================================================
# Catalog Node (taxonomy tree) Schemas
# ============================================================================


class CatalogNodeCreateRequest(BaseModel):
    """Request to create a catalog node (discipline or any descendant)."""

    name: str = Field(..., min_length=1, max_length=255)
    node_type: str = Field(..., description="e.g. DISCIPLINE, LEARNING_AREA, LEARNING_UNIT, CONTENT, SUBCONTENT")
    parent_id: Optional[UUID] = Field(None, description="Null for a root/discipline node")
    code: Optional[str] = None
    description: Optional[str] = None
    position: int = 0
    metadata: Optional[dict[str, Any]] = None


class CatalogNodeResponse(BaseModel):
    """A single catalog node."""

    id: UUID
    parent_id: Optional[UUID] = None
    root_id: Optional[UUID] = None
    node_type: str
    code: Optional[str] = None
    name: str
    description: Optional[str] = None
    position: int
    active: bool
    created_at: datetime
    updated_at: datetime


class CatalogNodeTreeResponse(BaseModel):
    """A discipline root with all its descendants (flat list, parent_id links form the tree)."""

    root: CatalogNodeResponse
    nodes: list[CatalogNodeResponse]


# ============================================================================
# Educational Resource Schemas
# ============================================================================


class EducationalResourceCreateRequest(BaseModel):
    """Request to create a generic educational resource."""

    title: str = Field(..., min_length=1, max_length=500)
    resource_type: str = Field(
        ..., description="THEORY_MATERIAL, BOOK, PDF, VIDEO, QUESTION_SET, EXTERNAL_RESOURCE, OTHER"
    )
    origin_type: str = Field(..., description="AUTHOR, SCHOOL, PLATFORM, LICENSED, EXTERNAL")
    description: Optional[str] = None
    author: Optional[str] = None
    owner_external_id: Optional[str] = None
    license_reference: Optional[str] = None
    source_url: Optional[str] = None
    storage_uri: Optional[str] = None
    status: str = "draft"
    visibility_scope: str = "PRIVATE"
    metadata: Optional[dict[str, Any]] = None


class EducationalResourceResponse(BaseModel):
    """A single educational resource."""

    id: UUID
    title: str
    description: Optional[str] = None
    resource_type: str
    author: Optional[str] = None
    origin_type: str
    owner_external_id: Optional[str] = None
    license_reference: Optional[str] = None
    source_url: Optional[str] = None
    storage_uri: Optional[str] = None
    status: str
    visibility_scope: str
    created_by_external_identity: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class ResourceAccessGrantCreateRequest(BaseModel):
    """Request to grant a resource to a school/classroom/user audience."""

    grantee_type: str = Field(..., description="INSTITUTION, SCHOOL, UNIT, SEGMENT, GRADE_LEVEL, CLASSROOM or EXTERNAL_IDENTITY")
    grantee_external_id: str = Field(..., min_length=1)


class ResourceAccessGrantResponse(BaseModel):
    """A concrete audience grant for a published resource."""

    id: UUID
    resource_id: UUID
    grantee_type: str
    grantee_external_id: str
    created_at: datetime


# ============================================================================
# Content <-> Resource Link Schemas
# ============================================================================


class ContentResourceLinkCreateRequest(BaseModel):
    """Request to associate a resource with a content node."""

    content_node_id: UUID
    resource_id: UUID
    pedagogical_role: str = Field(
        ..., description="THEORY, EXPLANATION, PRACTICE, REVIEW, VIDEO, REFERENCE"
    )
    relevance: Optional[float] = None
    priority: Optional[int] = None
    position: Optional[int] = None
    recommended_level: Optional[str] = Field(None, description="EASY, MEDIUM, HARD")
    metadata: Optional[dict[str, Any]] = None


class ContentResourceLinkResponse(BaseModel):
    """A single content<->resource association."""

    id: UUID
    content_node_id: UUID
    resource_id: UUID
    pedagogical_role: str
    relevance: Optional[float] = None
    priority: Optional[int] = None
    position: Optional[int] = None
    recommended_level: Optional[str] = None
    created_at: datetime


class ContentResourcesResponse(BaseModel):
    """Resources available for a given content node."""

    content_node_id: UUID
    links: list[ContentResourceLinkResponse]


# ============================================================================
# Theory Material Schemas
# ============================================================================


class TheoryMaterialCreateRequest(BaseModel):
    """Request to create a new authored theory material."""

    title: str = Field(..., min_length=1, max_length=500)
    description: Optional[str] = Field(None, max_length=20000)
    # PHASE 23 - free-form identity (validated by the client's picklists, not a DB
    # CheckConstraint, so new kinds/sources need no migration).
    material_kind: Optional[str] = Field(None, max_length=30)
    authoring_source: Optional[str] = Field(None, max_length=20)
    visibility_scope: str = Field("PRIVATE", max_length=20)
    primary_content_node_id: Optional[UUID] = None


class TheoryMaterialUpdateRequest(BaseModel):
    """PATCH a DRAFT material's identity fields."""

    title: Optional[str] = Field(None, min_length=1, max_length=500)
    description: Optional[str] = Field(None, max_length=20000)
    material_kind: Optional[str] = Field(None, max_length=30)
    authoring_source: Optional[str] = Field(None, max_length=20)
    visibility_scope: Optional[str] = Field(None, max_length=20)
    primary_content_node_id: Optional[UUID] = None
    unset_primary_content_node: bool = False


class TheoryMaterialResponse(BaseModel):
    """A theory material (parent entity, stable across versions)."""

    id: UUID
    title: str
    description: Optional[str] = None
    material_kind: Optional[str] = None
    authoring_source: Optional[str] = None
    visibility_scope: str = "PRIVATE"
    primary_content_node_id: Optional[UUID] = None
    primary_content_code: Optional[str] = None
    curriculum_status: str = "UNMAPPED"
    school_id: Optional[UUID] = None
    created_by_external_identity: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    latest_version_status: Optional[str] = None
    latest_version_number: Optional[int] = None
    section_count: int = 0
    block_count: int = 0
    question_count: int = 0


class MaterialSectionCreateRequest(BaseModel):
    section_type: str = Field(..., min_length=1, max_length=50)
    position: int = Field(..., gt=0)
    title: Optional[str] = Field(None, max_length=500)
    body: Optional[str] = None
    content_code: Optional[str] = Field(None, max_length=100)
    curriculum_relation_type: Optional[str] = Field(None, max_length=30)
    metadata: Optional[dict[str, Any]] = None


class MaterialSectionResponse(BaseModel):
    id: UUID
    material_version_id: UUID
    section_type: str
    position: int
    title: Optional[str] = None
    body: Optional[str] = None
    content_node_id: Optional[UUID] = None
    content_code: Optional[str] = None
    curriculum_relation_type: Optional[str] = None
    curriculum_status: str = "UNMAPPED"
    block_count: int = 0


class MaterialBlockCreateRequest(BaseModel):
    block_type: str = Field(..., min_length=1, max_length=50)
    position: int = Field(..., gt=0)
    title: Optional[str] = Field(None, max_length=500)
    body: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class MaterialBlockResponse(BaseModel):
    id: UUID
    section_id: UUID
    material_version_id: UUID
    block_type: str
    position: int
    title: Optional[str] = None
    body: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class MaterialQuestionLinkRequest(BaseModel):
    question_version_id: UUID
    relation_type: str = Field("EXERCISE", max_length=30)
    section_id: Optional[UUID] = None
    position: Optional[int] = Field(None, gt=0)


class MaterialQuestionResponse(BaseModel):
    id: UUID
    material_version_id: UUID
    question_version_id: UUID
    relation_type: Optional[str] = None
    section_id: Optional[UUID] = None
    position: int
    official_number: Optional[int] = None
    statement: Optional[str] = None


class MaterialContentAvailabilityResponse(BaseModel):
    content_code: str
    material_available: bool
    material_count: int


class TheoryMaterialVersionCreateRequest(BaseModel):
    """Request to create a new draft version of a material."""

    introduction: Optional[str] = None
    summary: Optional[str] = None


class TheoryMaterialVersionResponse(BaseModel):
    """A single version of a theory material."""

    id: UUID
    material_id: UUID
    version_number: int
    status: str
    resource_id: Optional[UUID] = None
    introduction: Optional[str] = None
    summary: Optional[str] = None
    created_at: datetime
    updated_at: datetime
    published_at: Optional[datetime] = None


class TheoryMaterialDetailResponse(BaseModel):
    """A material with its versions."""

    material: TheoryMaterialResponse
    versions: list[TheoryMaterialVersionResponse]


class MaterialReviewRequest(BaseModel):
    """Request payload for material review actions."""

    action: str = Field(..., description="submit, approve, reject, publish, archive")
    reason: Optional[str] = None
