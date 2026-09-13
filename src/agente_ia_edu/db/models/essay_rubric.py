"""R1 - Versioned ENEM essay rubric with per-descriptor provenance.

Five additive tables. Nothing here touches an existing table.

The normative text is NOT hardcoded (spec v1.0 §3): it is seeded from
``src/agente_ia_edu/rubrics/*.yaml``, which carries the official source page for
every descriptor and is reviewed in a pull request against the INEP PDF.

Why levels, signals and zero rules are three tables and not one: levels are
immutable official text scored on a fixed six-value scale; signals are the
observable vocabulary the engine reasons with, and each one declares where it
came from (spec §17); zero rules are short-circuit conditions that annul the
essay or a competency and therefore carry no points at all. Folding zero rules
into levels would break the points CHECK.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
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

COMPETENCY_CODES = ("C1", "C2", "C3", "C4", "C5")
OFFICIAL_LEVEL_POINTS = (0, 40, 80, 120, 160, 200)
PROVENANCES = ("OFICIAL_INEP", "INTERPRETACAO_PEDAGOGICA", "HEURISTICA_MOTOR")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EssayRubric(Base):
    """One version of a correction rubric. ENEM is the first, not the only one:
    schools run their own vestibular grids, so several rubrics stay ACTIVE at the
    same time and there is deliberately no single-ACTIVE constraint."""

    __tablename__ = "essay_rubrics"
    __table_args__ = (
        UniqueConstraint("rubric_version", name="uq_essay_rubrics_version"),
        CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED')", name="ck_essay_rubrics_status"
        ),
        Index("ix_essay_rubrics_status", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    rubric_version: Mapped[str] = mapped_column(String(50), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    effective_year: Mapped[int | None] = mapped_column(Integer)
    max_total_points: Mapped[int] = mapped_column(Integer, nullable=False, default=1000)
    official_source_title: Mapped[str | None] = mapped_column(Text)
    official_source_url: Mapped[str | None] = mapped_column(Text)
    official_source_sha256: Mapped[str | None] = mapped_column(String(64))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    published_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    superseded_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    competencies: Mapped[list["EssayRubricCompetency"]] = relationship(
        back_populates="rubric"
    )
    zero_rules: Mapped[list["EssayRubricZeroRule"]] = relationship(
        back_populates="rubric"
    )


class EssayRubricCompetency(Base):
    """C1-C5. ``official_title`` is the literal wording of the matrix."""

    __tablename__ = "essay_rubric_competencies"
    __table_args__ = (
        UniqueConstraint("rubric_id", "code", name="uq_essay_rubric_competencies_code"),
        CheckConstraint(
            "code IN ('C1', 'C2', 'C3', 'C4', 'C5')",
            name="ck_essay_rubric_competencies_code",
        ),
        Index("ix_essay_rubric_competencies_rubric_id", "rubric_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    rubric_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("essay_rubrics.id", ondelete="RESTRICT"), nullable=False
    )
    code: Mapped[str] = mapped_column(String(4), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    official_title: Mapped[str] = mapped_column(Text, nullable=False)
    max_points: Mapped[int] = mapped_column(Integer, nullable=False, default=200)
    source_page: Mapped[int | None] = mapped_column(Integer)

    rubric: Mapped["EssayRubric"] = relationship(back_populates="competencies")
    levels: Mapped[list["EssayRubricLevel"]] = relationship(back_populates="competency")
    signals: Mapped[list["EssayRubricSignal"]] = relationship(
        back_populates="competency"
    )


class EssayRubricLevel(Base):
    """One of the six official performance levels of a competency.

    ``descriptor`` is literal cartilha text; ``source_page`` is mandatory so any
    reviewer can open the PDF and check the wording."""

    __tablename__ = "essay_rubric_levels"
    __table_args__ = (
        UniqueConstraint(
            "competency_id", "points", name="uq_essay_rubric_levels_points"
        ),
        CheckConstraint(
            "points IN (0, 40, 80, 120, 160, 200)", name="ck_essay_rubric_levels_points"
        ),
        Index("ix_essay_rubric_levels_competency_id", "competency_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    competency_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("essay_rubric_competencies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    points: Mapped[int] = mapped_column(Integer, nullable=False)
    descriptor: Mapped[str] = mapped_column(Text, nullable=False)
    source_page: Mapped[int] = mapped_column(Integer, nullable=False)
    provenance: Mapped[str] = mapped_column(
        String(30), nullable=False, default="OFICIAL_INEP"
    )

    competency: Mapped["EssayRubricCompetency"] = relationship(back_populates="levels")


class EssayRubricSignal(Base):
    """The observable vocabulary the engine reasons with (spec §4 and §17).

    The two CHECK constraints are what make provenance a rule instead of a
    decoration: anything claiming an external source must name it, and anything
    that is our own heuristic must justify itself in writing."""

    __tablename__ = "essay_rubric_signals"
    __table_args__ = (
        UniqueConstraint("competency_id", "key", name="uq_essay_rubric_signals_key"),
        CheckConstraint(
            "provenance IN ('OFICIAL_INEP', 'INTERPRETACAO_PEDAGOGICA', 'HEURISTICA_MOTOR')",
            name="ck_essay_rubric_signals_provenance",
        ),
        CheckConstraint(
            "provenance = 'HEURISTICA_MOTOR' OR source_ref IS NOT NULL",
            name="ck_essay_rubric_signals_source_ref",
        ),
        CheckConstraint(
            "provenance <> 'HEURISTICA_MOTOR' OR rationale IS NOT NULL",
            name="ck_essay_rubric_signals_rationale",
        ),
        Index("ix_essay_rubric_signals_competency_id", "competency_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    competency_id: Mapped[uuid.UUID] = mapped_column(
        Uuid,
        ForeignKey("essay_rubric_competencies.id", ondelete="RESTRICT"),
        nullable=False,
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    provenance: Mapped[str] = mapped_column(String(30), nullable=False)
    source_ref: Mapped[str | None] = mapped_column(Text)
    rationale: Mapped[str | None] = mapped_column(Text)
    active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)

    competency: Mapped["EssayRubricCompetency"] = relationship(back_populates="signals")


class EssayRubricZeroRule(Base):
    """Annulment and zero conditions. ``competency_code`` is NULL when the rule
    annuls the whole essay rather than a single competency."""

    __tablename__ = "essay_rubric_zero_rules"
    __table_args__ = (
        UniqueConstraint("rubric_id", "key", name="uq_essay_rubric_zero_rules_key"),
        CheckConstraint(
            "effect IN ('ANULA_REDACAO', 'ZERA_COMPETENCIA')",
            name="ck_essay_rubric_zero_rules_effect",
        ),
        CheckConstraint(
            "competency_code IS NULL OR competency_code IN ('C1', 'C2', 'C3', 'C4', 'C5')",
            name="ck_essay_rubric_zero_rules_competency_code",
        ),
        Index("ix_essay_rubric_zero_rules_rubric_id", "rubric_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    rubric_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("essay_rubrics.id", ondelete="RESTRICT"), nullable=False
    )
    key: Mapped[str] = mapped_column(String(80), nullable=False)
    label: Mapped[str] = mapped_column(String(255), nullable=False)
    description: Mapped[str | None] = mapped_column(Text)
    effect: Mapped[str] = mapped_column(String(30), nullable=False)
    competency_code: Mapped[str | None] = mapped_column(String(4))
    source_page: Mapped[int | None] = mapped_column(Integer)
    provenance: Mapped[str] = mapped_column(
        String(30), nullable=False, default="OFICIAL_INEP"
    )

    rubric: Mapped["EssayRubric"] = relationship(back_populates="zero_rules")
