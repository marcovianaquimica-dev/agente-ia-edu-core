"""Envio em lote de redacoes fisicas pelo professor (spec 2026-09-28 s3).

Duas tabelas aditivas. Nada em EssaySubmission/EssayCorrection/PromptAssignment
muda: uma vez identificada, uma pagina (ou uma corrida de paginas consecutivas
do mesmo aluno) produz uma EssaySubmission comum, indistinguivel de uma enviada
pelo proprio aluno, e segue o pipeline de correcao existente sem alteracao
nenhuma.

``school_id`` aqui carrega FKs COMPOSTAS para essay_prompts e classes, e nao uma
FK simples para schools - mesma convencao de isolamento multi-tenant que
PromptAssignment (essay_proposal.py) ja usa. Sem isso o banco nao teria como
recusar um lote cuja proposta e da escola A e cuja turma e da escola B.

``essay_batch_pages`` NUNCA gera uma EssaySubmissionPage: a submissao criada pelo
lote nasce com anchor_mode="TEXT_OFFSET" e canonical_text ja preenchido, e
services/essay_correction.py so ramifica em anchor_mode (nunca em mode), entao
nao ha nada no pipeline de correcao que va procurar paginas. A imagem de cada
pagina fisica vive so em ``storage_uri`` aqui.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    DateTime,
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


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EssayBatchUpload(Base):
    """Um envio em lote: N arquivos (fotos e/ou PDFs) de uma turma que escreveu
    a mesma proposta em papel. ``status`` e PROCESSING ate a ultima pagina
    terminar; ``total_pages`` e fixado na criacao (ja com os PDFs separados em
    paginas), entao o progresso e sempre legivel como
    paginas_processadas/total_pages."""

    __tablename__ = "essay_batch_uploads"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "essay_prompt_id"],
            ["essay_prompts.school_id", "essay_prompts.id"],
            ondelete="RESTRICT",
            name="fk_essay_batch_uploads_school_prompt",
        ),
        ForeignKeyConstraint(
            ["school_id", "class_id"],
            ["classes.school_id", "classes.id"],
            ondelete="RESTRICT",
            name="fk_essay_batch_uploads_school_class",
        ),
        UniqueConstraint("school_id", "id", name="uq_essay_batch_uploads_school_id_id"),
        CheckConstraint(
            "status IN ('PROCESSING', 'DONE')", name="ck_essay_batch_uploads_status"
        ),
        CheckConstraint(
            "total_pages >= 0", name="ck_essay_batch_uploads_total_pages_non_negative"
        ),
        Index("ix_essay_batch_uploads_school_id", "school_id"),
        Index("ix_essay_batch_uploads_essay_prompt_id", "essay_prompt_id"),
        Index("ix_essay_batch_uploads_class_id", "class_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    essay_prompt_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    class_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    uploaded_by_external_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PROCESSING")
    total_pages: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    pages: Mapped[list["EssayBatchPage"]] = relationship(back_populates="batch")


class EssayBatchPage(Base):
    """Uma pagina fisica do lote, na ordem em que os arquivos foram enviados
    (1-based, continua entre arquivos: um PDF de 3 paginas seguido de 2 fotos
    produz as paginas 1..5).

    Nasce NEEDS_REVIEW e so vira MATCHED_AUTO quando o OCR leu um nome que bate
    com exatamente um aluno ATIVO da turma E a corrida de paginas desse aluno
    produziu texto de corpo nao vazio. Enquanto o lote esta PROCESSING as paginas
    ainda nao processadas aparecem como NEEDS_REVIEW - o status do lote e o que
    diz se a contagem ja e final.

    ``ocr_name_raw`` guarda o nome JA NORMALIZADO (maiusculas, sem acento,
    espacos colapsados): e o que o matching compara e tambem o que o professor
    precisa ver como pista na tela de resolucao manual, e guardar as duas formas
    nao acrescentaria informacao nenhuma. ``ocr_cpf_raw`` guarda so os digitos.
    """

    __tablename__ = "essay_batch_pages"
    __table_args__ = (
        UniqueConstraint("batch_id", "page_number", name="uq_essay_batch_pages_number"),
        CheckConstraint("page_number >= 1", name="ck_essay_batch_pages_page_number_positive"),
        CheckConstraint(
            "status IN ('MATCHED_AUTO', 'NEEDS_REVIEW', 'RESOLVED_MANUAL')",
            name="ck_essay_batch_pages_status",
        ),
        Index("ix_essay_batch_pages_batch_id", "batch_id"),
        Index("ix_essay_batch_pages_matched_student_id", "matched_student_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    batch_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("essay_batch_uploads.id", ondelete="CASCADE"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(500), nullable=False)
    ocr_name_raw: Mapped[str | None] = mapped_column(String(255))
    ocr_cpf_raw: Mapped[str | None] = mapped_column(String(20))
    ocr_body_text: Mapped[str | None] = mapped_column(Text)
    matched_student_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("students.id", ondelete="RESTRICT")
    )
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="NEEDS_REVIEW")
    essay_submission_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("essay_submissions.id", ondelete="RESTRICT")
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    batch: Mapped["EssayBatchUpload"] = relationship(back_populates="pages")
