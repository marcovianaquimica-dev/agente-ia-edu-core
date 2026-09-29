"""Acompanhamento de um lote de processamento em massa via Batch API da
OpenAI (correcao-em-massa-rede-estadual, spec 2026-09-29). Uma linha por
lote de fato submetido a OpenAI - um estagio (OCR/CORRECTION/SCORING) pode
precisar de mais de um lote quando passa de 50.000 requisicoes (o teto da
Batch API), daí sequence_number.

Guarda so o PROGRESSO junto a OpenAI - os dados em si (texto transcrito,
nota, feedback) continuam vivendo em EssayBatchPage/EssaySubmission/
EssayCorrection, ja existentes, sem nenhuma coluna nova la.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import CheckConstraint, DateTime, Index, Integer, String, Uuid
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class MassCorrectionRun(Base):
    __tablename__ = "mass_correction_runs"
    __table_args__ = (
        CheckConstraint(
            "stage IN ('OCR', 'CORRECTION', 'SCORING')",
            name="ck_mass_correction_runs_stage",
        ),
        CheckConstraint(
            "status IN ('PENDING', 'validating', 'in_progress', 'finalizing', "
            "'completed', 'failed', 'expired', 'cancelled')",
            name="ck_mass_correction_runs_status",
        ),
        CheckConstraint("request_count >= 0", name="ck_mass_correction_runs_request_count_non_negative"),
        Index("ix_mass_correction_runs_school_id", "school_id"),
        Index("ix_mass_correction_runs_stage_status", "stage", "status"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    stage: Mapped[str] = mapped_column(String(20), nullable=False)
    sequence_number: Mapped[int] = mapped_column(Integer, nullable=False)
    openai_batch_id: Mapped[str | None] = mapped_column(String(64))
    input_file_id: Mapped[str | None] = mapped_column(String(64))
    output_file_id: Mapped[str | None] = mapped_column(String(64))
    request_count: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PENDING")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
    completed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
