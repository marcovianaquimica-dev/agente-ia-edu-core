"""
Pydantic schemas for Platform Administration API endpoints (Phase 12A).
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class SchoolCreateRequest(BaseModel):
    code: str = Field(..., description="Unique short code/slug for school", min_length=2, max_length=50)
    name: str = Field(..., description="Official school name", min_length=2, max_length=255)
    short_name: Optional[str] = Field(None, max_length=100)
    external_identifier: Optional[str] = Field(None, max_length=255)
    status: str = Field("ACTIVE", description="ACTIVE, INACTIVE, SUSPENDED")
    metadata: Optional[dict[str, Any]] = None


class SchoolUpdateRequest(BaseModel):
    name: Optional[str] = Field(None, max_length=255)
    short_name: Optional[str] = Field(None, max_length=100)
    status: Optional[str] = Field(None, description="ACTIVE, INACTIVE, SUSPENDED")
    metadata: Optional[dict[str, Any]] = None


class SchoolModuleConfigureRequest(BaseModel):
    module_key: str = Field(..., description="AGENTE_IA_EDU, REDACAO_IA")
    enabled: bool = Field(True, description="Enable or disable module")
    metadata: Optional[dict[str, Any]] = None


class SchoolModuleResponse(BaseModel):
    id: UUID
    school_id: UUID
    module_key: str
    enabled: bool
    activated_at: datetime
    deactivated_at: Optional[datetime] = None


class SchoolResponse(BaseModel):
    id: UUID
    code: str
    name: str
    short_name: Optional[str] = None
    external_identifier: Optional[str] = None
    status: str
    modules: list[SchoolModuleResponse] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class UserLinkCreateRequest(BaseModel):
    external_user_id: str
    role: str = Field(..., description="PLATFORM_ADMIN, DIRECTOR, COORDINATOR, TEACHER, STUDENT")
    scope_type: str = Field("SCHOOL", description="PLATFORM, SCHOOL, UNIT, SEGMENT, GRADE_LEVEL, CLASSROOM")
    scope_external_id: Optional[str] = None
    metadata: Optional[dict[str, Any]] = None


class UserLinkResponse(BaseModel):
    id: UUID
    external_user_id: str
    school_id: Optional[UUID] = None
    role: str
    scope_type: str
    scope_external_id: Optional[str] = None
    active: bool
    created_at: datetime


class AdminAuditLogResponse(BaseModel):
    id: UUID
    performed_by_external_id: str
    action: str
    entity_type: str
    entity_id: str
    school_id: Optional[UUID] = None
    metadata: Optional[dict[str, Any]] = None
    created_at: datetime
