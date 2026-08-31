"""
Pydantic schemas for Coordination and Director Portal API endpoints (Phase 12C.2).
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field

from .teacher_portal import (
    ActionPlanItem,
    ContentMasteryBreakdownItem,
    ImprovementAreaItem,
    RecentLessonItem,
    StrengthItem,
)


class CoordinationDashboardResponse(BaseModel):
    coordinator_id: str
    school_id: UUID
    academic_year: str
    unit_id: Optional[str] = None
    segment_id: Optional[str] = None
    grade_level: Optional[str] = None
    classroom_id: Optional[str] = None
    time_period: str
    total_students: int
    total_teachers: int
    total_classrooms: int
    overall_mastery_average: float
    students_struggling_count: int
    students_struggling_percentage: float
    students_developing_count: int
    students_developing_percentage: float
    students_mastered_count: int
    students_mastered_percentage: float
    average_mastery_by_content: list[ContentMasteryBreakdownItem] = Field(default_factory=list)
    classrooms_needing_attention: list[dict[str, Any]] = Field(default_factory=list)
    recent_contexts: list[dict[str, Any]] = Field(default_factory=list)
    top_performing_contents: list[StrengthItem] = Field(default_factory=list)
    needs_attention_contents: list[ImprovementAreaItem] = Field(default_factory=list)
    action_plan: list[ActionPlanItem] = Field(default_factory=list)


class ClassroomComparisonItem(BaseModel):
    classroom_id: str
    name: str
    student_count: int
    average_mastery: float
    struggling_count: int
    developing_count: int
    mastered_count: int
    priority_contents: list[str] = Field(default_factory=list)


class TeacherOversightItem(BaseModel):
    teacher_id: str
    name: str
    school_id: UUID
    assigned_classrooms: list[str] = Field(default_factory=list)
    total_students: int
    classrooms_average_mastery: float
    recent_lessons_count: int


class CoordinationHierarchyGradeItem(BaseModel):
    grade_level: str
    student_count: int
    average_mastery: float
    classrooms: list[dict[str, Any]] = Field(default_factory=list)


class CoordinationHierarchySegmentItem(BaseModel):
    segment_id: str
    segment_name: str
    grades: list[CoordinationHierarchyGradeItem] = Field(default_factory=list)


class CoordinationHierarchyUnitItem(BaseModel):
    unit_id: str
    unit_name: str
    segments: list[CoordinationHierarchySegmentItem] = Field(default_factory=list)


class CoordinationHierarchyResponse(BaseModel):
    school_id: UUID
    school_name: str
    academic_year: str
    units: list[CoordinationHierarchyUnitItem] = Field(default_factory=list)
