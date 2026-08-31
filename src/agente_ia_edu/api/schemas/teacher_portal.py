"""
Pydantic schemas for Teacher and Coordination Portal API endpoints (Phase 12B.2).
"""

from datetime import datetime
from typing import Any, Optional
from uuid import UUID

from pydantic import BaseModel, Field


class ClassroomSummaryItem(BaseModel):
    classroom_id: str
    name: str
    grade_level: Optional[str] = "3ª Série"
    segment: Optional[str] = "Ensino Médio"
    unit: Optional[str] = "Unidade Principal"
    academic_year: str = "2026"
    student_count: int
    average_mastery: float
    priority_contents: list[str] = Field(default_factory=list)


class ContentMasteryBreakdownItem(BaseModel):
    content_node_id: str
    content_name: str
    class_average_mastery: float
    students_struggling_count: int
    total_students: int


class StrengthItem(BaseModel):
    content_node_id: str
    content_name: str
    class_average_mastery: float
    reason: str


class ImprovementAreaItem(BaseModel):
    content_node_id: str
    content_name: str
    class_average_mastery: float
    students_struggling_count: int
    reason: str


class ActionPlanItem(BaseModel):
    priority: str  # HIGH, MEDIUM, LOW
    content_name: str
    class_average_mastery: float
    impacted_students_count: int
    evidence: str
    recommended_action: str


class RecentLessonItem(BaseModel):
    id: UUID
    classroom_id: str
    content_name: str
    lesson_date: datetime
    duration_minutes: Optional[int] = None
    title: Optional[str] = None
    summary_observation: Optional[str] = None


class TeacherDashboardResponse(BaseModel):
    teacher_id: str
    school_id: UUID
    academic_year: str
    classroom_id: Optional[str] = None
    authorized_classrooms: list[str] = Field(default_factory=list)
    time_period: str
    student_count: int
    active_students_count: int
    overall_class_average: float
    students_mastered_count: int
    students_mastered_percentage: float
    students_developing_count: int
    students_developing_percentage: float
    students_struggling_count: int
    students_struggling_percentage: float
    average_mastery_by_content: list[ContentMasteryBreakdownItem] = Field(default_factory=list)
    top_performing_contents: list[StrengthItem] = Field(default_factory=list)
    needs_attention_contents: list[ImprovementAreaItem] = Field(default_factory=list)
    recent_lessons: list[RecentLessonItem] = Field(default_factory=list)
    action_plan: list[ActionPlanItem] = Field(default_factory=list)


class StudentRosterItem(BaseModel):
    student_id: str
    name: str
    classroom_id: str
    average_mastery: float
    status_label: str  # "Precisa de atenção", "Em desenvolvimento", "Consolidado"


class RecentContentTaughtItem(BaseModel):
    content_node_id: str
    content_name: str
    last_lesson_date: datetime
    teacher_or_author: str
    class_average_mastery: float
    struggling_students_count: int
    recommended_action: str


class ClassroomDetailResponse(BaseModel):
    classroom_id: str
    school_id: UUID
    academic_year: str
    summary: dict[str, Any]
    mastery_distribution: dict[str, float]
    students: list[StudentRosterItem] = Field(default_factory=list)
    students_needing_attention: list[StudentRosterItem] = Field(default_factory=list)
    average_mastery_by_content: list[ContentMasteryBreakdownItem] = Field(default_factory=list)
    strengths: list[StrengthItem] = Field(default_factory=list)
    improvement_areas: list[ImprovementAreaItem] = Field(default_factory=list)
    recent_lessons: list[RecentLessonItem] = Field(default_factory=list)
    recent_contents_taught: list[RecentContentTaughtItem] = Field(default_factory=list)
    action_plan: list[ActionPlanItem] = Field(default_factory=list)


class StudentDetailForTeacherResponse(BaseModel):
    student_id: str
    school_id: UUID
    classroom_id: str
    accuracy_percentage: float
    total_questions_answered: int
    total_questions_correct: int
    content_masteries: list[dict[str, Any]] = Field(default_factory=list)
    priority_contents: list[dict[str, Any]] = Field(default_factory=list)
    current_recommendations: list[dict[str, Any]] = Field(default_factory=list)
    recent_learning_history: list[dict[str, Any]] = Field(default_factory=list)


class StudentSearchItem(BaseModel):
    student_id: str
    name: str
    school_id: UUID
    classroom_id: str
    average_mastery: float


class ReportExportResponse(BaseModel):
    export_format: str
    filename: str
    content_type: str
    title: str
    generated_at: datetime
    summary: dict[str, Any]
    mastery_distribution: Optional[dict[str, Any]] = None
    strengths: Optional[list[Any]] = None
    improvement_areas: Optional[list[Any]] = None
    recent_contents_taught: Optional[list[Any]] = None
    action_plan: Optional[list[Any]] = None
    students_roster: Optional[list[Any]] = None
