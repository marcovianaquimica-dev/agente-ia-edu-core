"""API contracts for the school reception pre-registration workflow."""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator, model_validator


class ReceptionCandidateCreate(BaseModel):
    school_id: UUID
    full_name: str = Field(min_length=3, max_length=255)
    preferred_name: str | None = Field(None, max_length=255)
    birth_date: date | None = None
    guardian_name: str | None = Field(None, max_length=255)
    phone: str = Field(min_length=8, max_length=30)
    email: str = Field(min_length=5, max_length=255)
    academic_year: str = Field(min_length=4, max_length=10)
    unit_id: str = Field(min_length=1, max_length=255)
    segment_id: str = Field(min_length=1, max_length=255)
    grade_level: str = Field(min_length=1, max_length=255)
    classroom_id: str | None = Field(None, max_length=255)

    @field_validator(
        "full_name", "preferred_name", "guardian_name", "unit_id",
        "segment_id", "grade_level", "classroom_id", mode="before"
    )
    @classmethod
    def strip_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("email")
    @classmethod
    def validate_email(cls, value: str) -> str:
        clean = value.strip().lower()
        if not re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", clean):
            raise ValueError("Informe um e-mail válido.")
        return clean

    @field_validator("phone")
    @classmethod
    def validate_phone(cls, value: str) -> str:
        clean = re.sub(r"\D", "", value)
        if len(clean) < 8:
            raise ValueError("Informe um telefone válido.")
        return clean

    @field_validator("academic_year")
    @classmethod
    def validate_academic_year(cls, value: str) -> str:
        clean = value.strip()
        if not re.fullmatch(r"\d{4}", clean):
            raise ValueError("O ano letivo deve ter quatro dígitos.")
        return clean

    @model_validator(mode="after")
    def validate_person_data(self):
        if self.birth_date and self.birth_date > date.today():
            raise ValueError("A data de nascimento não pode estar no futuro.")
        if self.birth_date:
            age = date.today().year - self.birth_date.year - (
                (date.today().month, date.today().day)
                < (self.birth_date.month, self.birth_date.day)
            )
            if age < 18 and not self.guardian_name:
                raise ValueError("Informe o responsável para candidatos menores de 18 anos.")
        return self


class ReceptionCandidateResponse(BaseModel):
    id: UUID
    school_id: UUID
    full_name: str
    preferred_name: str | None = None
    birth_date: date | None = None
    guardian_name: str | None = None
    phone: str
    email: str
    academic_year: str
    unit_id: str
    segment_id: str
    grade_level: str
    classroom_id: str | None = None
    status: str
    released_by_external_id: str | None = None
    released_at: datetime | None = None
    external_student_id: str | None = None
    diagnostic_id: UUID | None = None
    diagnostic_status: str | None = None
    diagnostic_started_at: datetime | None = None
    diagnostic_completed_at: datetime | None = None
    questions_answered: int | None = None
    progress_percent: float | None = None
    result: dict[str, Any] | None = None
    created_at: datetime
    updated_at: datetime


class DiagnosticReleaseResponse(BaseModel):
    candidate: ReceptionCandidateResponse
    activation_token: str
    activation_expires_at: datetime | None = None


class DiagnosticAccessActivationRequest(BaseModel):
    token: str = Field(min_length=20, max_length=255)


class DiagnosticAccessActivationResponse(BaseModel):
    candidate_id: UUID
    school_id: UUID
    external_student_id: str
    diagnostic_start_path: str = "/student/#diagnostic"
