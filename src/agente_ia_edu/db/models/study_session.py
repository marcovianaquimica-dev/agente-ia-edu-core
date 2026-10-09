"""PHASE 24 - Study Session / Momento de Aprendizado.

A persisted ORCHESTRATION record. It never stores questions, corrections, domain
mastery or a second learning path - only the plan the StudySessionPlanner
produced (as JSON) plus enough progress state to resume after a disconnect.
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
    Uuid,
    text,
)
from sqlalchemy.ext.mutable import MutableDict, MutableList
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..types import JSONBCompatible

_MutJSONDict = MutableDict.as_mutable(JSONBCompatible)
_MutJSONList = MutableList.as_mutable(JSONBCompatible)


class StudySession(Base):
    __tablename__ = "study_sessions"
    __table_args__ = (
        CheckConstraint("source IN ('STUDENT_DEFINED', 'SCHOOL_DEFINED')",
                        name="ck_study_sessions_source"),
        CheckConstraint(
            "status IN ('SCHEDULED', 'READY', 'IN_PROGRESS', 'COMPLETED', 'CANCELLED')",
            name="ck_study_sessions_status"),
        CheckConstraint("timer_mode IN ('TIMED', 'UNTIMED')", name="ck_study_sessions_timer_mode"),
        # migration 064 - a rota pedagogica que levou o aluno ate a tarefa.
        # Conjunto fechado: sao os tres caminhos de prontidao do planejador.
        CheckConstraint(
            "readiness_route IS NULL OR readiness_route IN "
            "('DIRECT', 'DIAGNOSTIC', 'PREREQUISITE_PREPARATION')",
            name="ck_study_sessions_readiness_route"),
        Index("ix_study_sessions_student_date", "student_external_id", "session_date"),
        Index("ix_study_sessions_school_scope_date", "school_id", "scope_external_id", "session_date"),
        Index("ix_study_sessions_status", "status"),
        # migration 049 - list_coordination_sessions() without a
        # classroom_id filter (scope_external_id unconstrained) can't use
        # the composite above past its first column; this serves that shape.
        Index("ix_study_sessions_school_source_date", "school_id", "source", "session_date"),
        # migration 064 - "desta atividade, quem chegou por qual rota e quem
        # concluiu": a consulta que Professor/Coordenacao farao.
        Index("ix_study_sessions_objective_route",
              "objective_assignment_id", "readiness_route", "objective_completed",
              postgresql_where=text("objective_assignment_id IS NOT NULL"),
              sqlite_where=text("objective_assignment_id IS NOT NULL")),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_external_id: Mapped[str] = mapped_column(String(255), nullable=False)
    source: Mapped[str] = mapped_column(String(20), nullable=False)  # STUDENT_DEFINED | SCHOOL_DEFINED
    school_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=True
    )
    scope_type: Mapped[str | None] = mapped_column(String(20))          # CLASSROOM | STUDENT
    scope_external_id: Mapped[str | None] = mapped_column(String(255))
    created_by_external_id: Mapped[str | None] = mapped_column(String(255))
    session_date: Mapped[str] = mapped_column(String(10), nullable=False)   # ISO YYYY-MM-DD
    timer_mode: Mapped[str] = mapped_column(String(20), nullable=False, default="TIMED")
    start_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    end_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    available_minutes: Mapped[int | None] = mapped_column(Integer)
    break_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    effective_study_minutes: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    target_content_codes: Mapped[list[str] | None] = mapped_column(_MutJSONList)
    config: Mapped[dict[str, Any] | None] = mapped_column(_MutJSONDict)
    plan: Mapped[dict[str, Any] | None] = mapped_column(_MutJSONDict)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="SCHEDULED")

    # --- migration 064: como o aluno chegou ate a tarefa da escola ---------
    # Sem isto, "nao realizou a tarefa" e um balde unico que mistura quem nao
    # abriu a atividade com quem abriu, encontrou um pre-requisito faltando e
    # esta estudando ele. Pedagogicamente sao situacoes opostas.
    # DIRECT                   - o aluno ja tinha prontidao, foi direto
    # DIAGNOSTIC               - evidencia insuficiente, passou por um check curto
    # PREREQUISITE_PREPARATION - lacuna conhecida, estudou o pre-requisito antes
    readiness_route: Mapped[str | None] = mapped_column(String(30))
    # Sem ForeignKey: a sessao e registro historico. Apagar a atividade nao
    # pode apagar nem travar o fato de que o aluno estudou para ela.
    objective_assignment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    # False com objective_assignment_id preenchido = "esta se preparando",
    # que e justamente o estado que hoje nao se consegue distinguir.
    objective_completed: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False, server_default=text("false"))

    current_block_index: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    started_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", _MutJSONDict)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=lambda: datetime.now(timezone.utc)
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False,
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
    )


__all__ = ["StudySession"]
