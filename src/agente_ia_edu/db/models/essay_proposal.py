"""R2 - essay proposal (topic) and submission.

Five additive tables. See docs/superpowers/specs/2026-09-21-r2-propostas-
envio-redacao-design.md for the full design.

NAMING NOTE: ``agente_ia_edu.essay_prompts.EssayPrompt`` is a different,
unrelated thing - a frozen dataclass for an AI prompt-template artifact
(R1). The ``EssayPrompt`` here is the essay TOPIC/proposal ORM model. Never
import both into the same module without an alias.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from ..base import Base
from ..types import JSONBCompatible


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EssayPrompt(Base):
    """A redação proposal/topic. Immutable once it leaves DRAFT (i.e. once the
    first PromptAssignment exists) - same "never edit, always supersede"
    family as EssayRubric, though R2 never actually produces a SUPERSEDED row
    (see this plan's Global Constraints)."""

    __tablename__ = "essay_prompts"
    __table_args__ = (
        UniqueConstraint("school_id", "id", name="uq_essay_prompts_school_id_id"),
        # Uma escola materializa a mesma proposta da plataforma no maximo uma
        # vez. UNIQUE comum basta: as propostas normais tem NULL nessa coluna
        # e NULL nunca e igual a NULL para fins de unicidade, entao elas nunca
        # colidem entre si (spec 2026-09-29 s2).
        UniqueConstraint(
            "school_id",
            "materialized_from_platform_prompt_id",
            name="uq_essay_prompts_school_materialized_from",
        ),
        CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED')", name="ck_essay_prompts_status"
        ),
        Index("ix_essay_prompts_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    # "Tema livre": the student writes their own theme instead of answering
    # this prompt's fixed `statement` - pinned first in the student's list
    # (a different card color, see web/essay.js) and its submissions require
    # EssaySubmission.student_declared_theme, which then stands in for
    # `statement` when grading (see essay_correction._effective_essay_statement).
    is_free_theme: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_by_external_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    # "Lixeira": a teacher-initiated soft delete. NULL = active/normal. Set =
    # in the trash, restorable for EssayProposalService.TRASH_RETENTION_DAYS
    # (30) from this timestamp - the prompt row, its materials, assignments,
    # submissions and corrections are never touched by delete/restore, only
    # this column, so restoring brings back the exact same student data.
    deleted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)
    # Proveniencia: preenchida SO quando esta linha e a copia por escola de
    # uma proposta da plataforma (platform_essay_prompts), criada pelo
    # backend na primeira vez que um professor desta escola a atribuiu a uma
    # turma. NULL = proposta criada normalmente por um professor, o caso de
    # hoje, sem nenhuma mudanca de comportamento. RESTRICT: uma proposta da
    # plataforma nunca pode ser apagada enquanto alguma escola tiver copia
    # dela (defensivo - o admin arquiva, nunca apaga).
    materialized_from_platform_prompt_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("platform_essay_prompts.id", ondelete="RESTRICT"), nullable=True
    )

    materials: Mapped[list["PromptMaterial"]] = relationship(back_populates="prompt")


class PromptMaterial(Base):
    """Support material for a proposal. Single parent (EssayPrompt), so a
    plain FK is enough - no separate school_id, matching
    EssayRubricCompetency -> EssayRubric."""

    __tablename__ = "prompt_materials"
    __table_args__ = (
        UniqueConstraint("essay_prompt_id", "position", name="uq_prompt_materials_position"),
        CheckConstraint("material_type IN ('TEXT', 'IMAGE', 'FILE')", name="ck_prompt_materials_type"),
        CheckConstraint(
            "(material_type = 'TEXT') = (content IS NOT NULL)",
            name="ck_prompt_materials_text_has_content",
        ),
        CheckConstraint(
            "(material_type IN ('IMAGE', 'FILE')) = (storage_uri IS NOT NULL)",
            name="ck_prompt_materials_image_has_storage_uri",
        ),
        Index("ix_prompt_materials_essay_prompt_id", "essay_prompt_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    essay_prompt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("essay_prompts.id", ondelete="RESTRICT"), nullable=False
    )
    material_type: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    storage_uri: Mapped[str | None] = mapped_column(String(1024))
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    prompt: Mapped["EssayPrompt"] = relationship(back_populates="materials")


class PromptAssignment(Base):
    """Assigns a proposal to a class. Authorization for essay submission
    checks against this table directly - a student only submits to a
    proposal their own class actually received."""

    __tablename__ = "prompt_assignments"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "essay_prompt_id"],
            ["essay_prompts.school_id", "essay_prompts.id"],
            ondelete="RESTRICT",
            name="fk_prompt_assignments_school_prompt",
        ),
        ForeignKeyConstraint(
            ["school_id", "class_id"],
            ["classes.school_id", "classes.id"],
            ondelete="RESTRICT",
            name="fk_prompt_assignments_school_class",
        ),
        UniqueConstraint("school_id", "id", name="uq_prompt_assignments_school_id_id"),
        UniqueConstraint(
            "essay_prompt_id", "class_id", name="uq_prompt_assignments_prompt_class"
        ),
        CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_prompt_assignments_status"),
        Index("ix_prompt_assignments_school_id", "school_id"),
        Index("ix_prompt_assignments_essay_prompt_id", "essay_prompt_id"),
        Index("ix_prompt_assignments_class_id", "class_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    essay_prompt_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    class_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    assigned_by_external_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class EssaySubmission(Base):
    """One submission/version of a student's essay for a given assignment.
    ``essay_id`` groups reenvios of the same (student, assignment); each row
    is an ``essay_version_id`` - the exact two names
    essay_engine_contract.v1.Identification already reserves."""

    __tablename__ = "essay_submissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "prompt_assignment_id"],
            ["prompt_assignments.school_id", "prompt_assignments.id"],
            ondelete="RESTRICT",
            name="fk_essay_submissions_school_assignment",
        ),
        ForeignKeyConstraint(
            ["school_id", "student_id"],
            ["students.school_id", "students.id"],
            ondelete="RESTRICT",
            name="fk_essay_submissions_school_student",
        ),
        CheckConstraint("mode IN ('TYPED', 'PHOTO', 'PDF')", name="ck_essay_submissions_mode"),
        CheckConstraint(
            "anchor_mode IN ('TEXT_OFFSET', 'IMAGE_REGION')",
            name="ck_essay_submissions_anchor_mode",
        ),
        CheckConstraint(
            "status IN ('PENDING_TRANSCRIPTION', 'PENDING_CONFIRMATION', 'SUBMITTED', 'SUPERSEDED')",
            name="ck_essay_submissions_status",
        ),
        CheckConstraint(
            "(canonical_text IS NULL) = (normalized_text_hash IS NULL)",
            name="ck_essay_submissions_canonical_text_hash_paired",
        ),
        CheckConstraint(
            "canonical_text IS NULL OR anchor_mode = 'TEXT_OFFSET'",
            name="ck_essay_submissions_canonical_text_requires_text_offset",
        ),
        CheckConstraint(
            "(status IN ('SUBMITTED', 'SUPERSEDED')) = (submitted_at IS NOT NULL)",
            name="ck_essay_submissions_submitted_at_presence",
        ),
        Index("ix_essay_submissions_school_id", "school_id"),
        Index("ix_essay_submissions_prompt_assignment_id", "prompt_assignment_id"),
        Index("ix_essay_submissions_student_id", "student_id"),
        Index("ix_essay_submissions_essay_id", "essay_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    essay_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    prompt_assignment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    anchor_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="PENDING_TRANSCRIPTION"
    )
    canonical_text: Mapped[str | None] = mapped_column(Text)
    normalized_text_hash: Mapped[str | None] = mapped_column(String(64))
    # Set only for a "tema livre" prompt's submission (EssayPrompt.is_free_theme):
    # the theme the student themselves chose to write about, typed at
    # submission time. When set, essay_correction._effective_essay_statement
    # uses this instead of the prompt's own (generic) statement, so FUGA_AO_TEMA
    # is judged against what the student declared, not a fixed topic.
    student_declared_theme: Mapped[str | None] = mapped_column(Text)
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    pages: Mapped[list["EssaySubmissionPage"]] = relationship(back_populates="submission")


class EssaySubmissionPage(Base):
    """One page, for PHOTO/PDF modes. Single parent (EssaySubmission), plain
    FK - same reasoning as PromptMaterial."""

    __tablename__ = "essay_submission_pages"
    __table_args__ = (
        UniqueConstraint(
            "essay_submission_id", "page_number", name="uq_essay_submission_pages_number"
        ),
        CheckConstraint(
            "page_number >= 1", name="ck_essay_submission_pages_page_number_positive"
        ),
        Index("ix_essay_submission_pages_essay_submission_id", "essay_submission_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    essay_submission_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("essay_submissions.id", ondelete="RESTRICT"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    width: Mapped[float | None] = mapped_column(Float)
    height: Mapped[float | None] = mapped_column(Float)
    ocr_tokens: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONBCompatible)
    reviewed_text: Mapped[str | None] = mapped_column(Text)
    # Real LLM token usage summed across every transcribe_page call this page
    # actually triggered (retries and low-confidence re-attempts included -
    # see _ocr_page in services/essay_submission.py). Nullable: never set for
    # a digitally-typed PDF page that skipped OCR entirely, and never
    # fabricated when the provider doesn't report usage.
    input_tokens: Mapped[int | None] = mapped_column(Integer)
    output_tokens: Mapped[int | None] = mapped_column(Integer)

    submission: Mapped["EssaySubmission"] = relationship(back_populates="pages")
