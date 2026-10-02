# Envio em lote de redações físicas pelo professor — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** O professor sobe fotos/PDFs de uma turma inteira que escreveu redação em papel; o sistema identifica cada aluno pelo cabeçalho da folha, agrupa as páginas consecutivas de cada um em uma `EssaySubmission` e manda direto pra correção por IA, em segundo plano, com uma fila de resolução manual só pras páginas não identificadas.

**Architecture:** Duas tabelas novas (`essay_batch_uploads`, `essay_batch_pages`) + uma coluna nova em `schools`. Um serviço novo (`services/essay_batch.py`) faz o trabalho: recorta a REGIÃO do cabeçalho de cada imagem de página por fração fixa de altura (a mesma fração que a folha de resposta gerada pelo próprio sistema usa), roda o OCR já existente (`EssaySubmissionService._ocr_page`, com suas 3 tentativas e piso de confiança) separadamente no cabeçalho e no corpo, casa o nome normalizado contra a turma e agrupa corridas consecutivas de páginas do mesmo aluno em uma submissão. O processamento roda em `fastapi.BackgroundTasks` (mesmo processo, sem fila externa), commitando página a página. Um módulo novo (`services/essay_answer_sheet.py`) gera a folha de resposta em branco e é o dono único da geometria do cabeçalho — `essay_batch.py` importa a fração dele, então a folha impressa e o recorte de OCR nunca podem divergir.

**Tech Stack:** Python 3.13 / FastAPI / SQLAlchemy 2 async / Alembic / PyMuPDF (`pymupdf`, extra `recovery`) / vanilla JS (sem framework, sem teste automatizado de frontend).

**Spec:** [docs/superpowers/specs/2026-09-28-envio-lote-redacao-design.md](../specs/2026-09-28-envio-lote-redacao-design.md)

## Global Constraints

- **Trabalhar sempre dentro da worktree** `/Users/marcoviana/agente-ia-edu-core/.claude/worktrees/integrate-redacao-round2` (branch `integrate-redacao-round2`), nunca no checkout principal. Todo caminho neste plano é relativo a essa raiz.
- **Nunca Pillow.** `pyproject.toml` documenta explicitamente que Pillow quebra o parser de PDF deste projeto ("Do NOT add pdfplumber here: it pulls in Pillow, which changes pypdf's page.images behaviour and breaks the parser"). Todo trabalho com imagem usa `pymupdf` (extra `recovery`, `pymupdf>=1.24,<2.0`), igual a `EssaySubmissionService._measure_page_image`.
- **Nenhuma dependência nova.** `pymupdf`, `python-multipart` (upload multipart) e `fastapi.BackgroundTasks` já existem. Nenhuma fila externa (Celery/RQ) — o projeto não tem nenhuma e esta leva não introduz uma.
- **Limites de tamanho:** 60 páginas no total por lote (todos os arquivos somados) → `422` na criação; 20 páginas por ARQUIVO PDF (`EssaySubmissionService._MAX_PDF_PAGES`, reaproveitado, não reescrito); 25MB por arquivo (`_MAX_UPLOAD_BYTES = 25 * 1024 * 1024`, mesmo valor das outras rotas de upload) → `413`.
- **Match automático:** nome normalizado batendo com EXATAMENTE UM aluno ACTIVE da turma. Zero ou dois ou mais → `NEEDS_REVIEW`. CPF nunca entra no match automático — é lido, guardado e mostrado só como pista na tela de resolução manual.
- **Sem confirmação humana do texto.** A submissão criada pelo lote já nasce `status="SUBMITTED"`, `anchor_mode="TEXT_OFFSET"`, com `canonical_text` preenchido — o padrão de `EssaySubmissionService.start_typed_submission` (`services/essay_submission.py:134-152`), NÃO o de `start_photo_submission`/`confirm_submission`. **Nenhuma `EssaySubmissionPage` é criada pelo lote**: verificado que `services/essay_correction.py` só ramifica em `submission.anchor_mode` (linhas 501, 552, 594, 627) e nunca em `submission.mode`, então um `anchor_mode="TEXT_OFFSET"` nunca faz o corretor procurar páginas. As imagens das páginas do lote vivem só em `essay_batch_pages.storage_uri`.
- **`mode="PHOTO"` em toda submissão do lote**, independente de o arquivo de origem ter sido PDF ou imagem: toda página do lote é a imagem rasterizada de uma folha manuscrita, e `mode` é puramente display (o motor de correção lê `anchor_mode`).
- **Padrão MissingGreenlet:** em produção `expire_on_commit=True` (`db/session.py`), então TODA resposta Pydantic e todo dict de retorno de serviço é montado ANTES do `await session.commit()`. Serviços que commitam em loop devolvem dicts simples, nunca objetos ORM. Mesma regra que `essay_prompts.py:161-171` e `essay_proposal.py::create_assignments_bulk` já seguem.
- **Best-effort por item:** um erro em uma página (OCR falhou, imagem corrompida) nunca derruba o lote — a página vira `NEEDS_REVIEW` com nome/CPF nulos e o lote continua. Mesmo padrão de `essay_proposal.py::create_assignments_bulk` (commit após cada sucesso individual).
- **Autorização:** TEACHER/COORDINATOR/DIRECTOR/PLATFORM_ADMIN, via a mesma helper `_authorize` que `essay_prompts.py:107-124` e `essay_corrections.py:103-114` já usam; `school_id` sempre vem do contexto resolvido, nunca do corpo da requisição; recurso de outra escola é `403`, nunca `404`.
- **Sem testes automatizados de frontend** (corte já estabelecido nas levas anteriores de redação). A tarefa de frontend traz passos de verificação manual no lugar.
- **TDD:** cada tarefa escreve o teste que falha primeiro, roda pra ver falhar, implementa o mínimo, roda pra ver passar, commita. Rodar a suíte completa (`.venv/bin/python -m pytest tests -q`) antes do commit final de cada tarefa que toque banco — migration aditiva não é pega por leitura, só por execução.
- **Comando de teste:** `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest <caminho> -q` a partir da raiz da worktree (o `.venv` da raiz do projeto tem `pymupdf 1.28.2` instalado; `/usr/bin/python3` NÃO tem).
- **Duas refinações conscientes sobre a spec**, ambas estritamente mais fortes que o que está escrito lá — se o revisor discordar, reverter pro texto da spec:
  1. **§3 escreve `school_id UUID FK -> schools.id` nas duas tabelas novas.** Este plano usa em vez disso as FKs COMPOSTAS `(school_id, essay_prompt_id) -> essay_prompts(school_id, id)` e `(school_id, class_id) -> classes(school_id, id)`, exatamente como `PromptAssignment` (`db/models/essay_proposal.py:126-147`) já faz. Motivo: é o que impede no banco um lote cuja proposta é da escola A e cuja turma é da escola B — a spec §3 descreve o shape lógico, não a convenção de isolamento multi-tenant que este repositório já adota em todas as tabelas com dois pais.
  2. **§6 diz "reaproveitando o motor PyMuPDF `Story`".** A folha de resposta é um formulário de GEOMETRIA FIXA: o cabeçalho tem que ocupar uma fração conhecida e exata da altura da página, porque é essa mesma fração que o OCR recorta depois. `Story` faz layout de fluxo e não garante fração nenhuma. Este plano usa então as primitivas de baixo nível do MESMO módulo PyMuPDF que `essay_pdf_export.py::render_pdf` já usa pras páginas de imagem (`page.draw_rect`, `page.draw_line`, `page.insert_text`, `page.insert_image`, `Document.tobytes(deflate=True, garbage=3)`), e NÃO `Story`. Geometria verificada rodando de verdade (A4 595.44x842.40pt, margem 36pt, cabeçalho até 185.24pt = 22%, campo de CPF terminando em 134pt, 30 linhas com passo de 20.09pt, 3 cópias = 3 páginas, 60KB).

---

### Task 1: Tabelas do lote + coluna de logo da escola (models + migration)

**Files:**
- Create: `src/agente_ia_edu/db/models/essay_batch.py`
- Modify: `src/agente_ia_edu/db/models/__init__.py` (imports + `__all__`)
- Modify: `src/agente_ia_edu/db/models/admin.py` (nova coluna em `School`, após `metadata_`, linha ~60)
- Create: `migrations/versions/056_essay_batch_upload.py`
- Test: `tests/test_r4_essay_batch_models.py`
- Test: `tests/test_r4_essay_batch_migration_postgresql.py`

**Interfaces:**
- Consumes: nada de outras tarefas — é a fundação.
- Produces: `EssayBatchUpload` (colunas `id: uuid.UUID`, `school_id: uuid.UUID`, `essay_prompt_id: uuid.UUID`, `class_id: uuid.UUID`, `uploaded_by_external_identity: str`, `status: str`, `total_pages: int`, `created_at: datetime`, `updated_at: datetime`) e `EssayBatchPage` (colunas `id: uuid.UUID`, `batch_id: uuid.UUID`, `page_number: int`, `storage_uri: str`, `ocr_name_raw: str | None`, `ocr_cpf_raw: str | None`, `ocr_body_text: str | None`, `matched_student_id: uuid.UUID | None`, `status: str`, `essay_submission_id: uuid.UUID | None`, `created_at: datetime`, `updated_at: datetime`), ambos importáveis de `agente_ia_edu.db.models`. Mais `School.logo_storage_uri: str | None`. Consumidos por TODAS as tarefas seguintes.

- [ ] **Step 1: Write the failing test**

Criar `tests/test_r4_essay_batch_models.py`:

```python
"""R4 lote - shape das duas tabelas novas e da coluna de logo da escola.

SQLite in-memory + create_all, mesmo padrão de tests/test_r2_essay_proposal_models.py.
As CHECK constraints sao validadas de verdade (SQLite aplica CHECK), as FKs
compostas so no teste de migration em PostgreSQL (SQLite nao aplica FK por padrao).
"""

import unittest
import uuid

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear, Class, EssayBatchPage, EssayBatchUpload, EssayPrompt,
    GradeLevel, School, Segment,
)


class EssayBatchModelsTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed(self, session):
        school = School(id=uuid.uuid4(), code="LOTE-1", name="Escola Lote")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-L1")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="3a serie", external_id="GRADE-L1",
        )
        year = AcademicYear(
            id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-L1"
        )
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="3A", external_id="TURMA-L1",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof_lote",
        )
        session.add_all([klass, prompt])
        await session.commit()
        return school, klass, prompt

    async def _batch(self, session, school, klass, prompt, *, status="PROCESSING", total_pages=0):
        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, uploaded_by_external_identity="prof_lote",
            status=status, total_pages=total_pages,
        )
        session.add(batch)
        await session.flush()
        return batch

    async def test_school_has_nullable_logo_storage_uri(self):
        async with self.session_factory() as session:
            school, _, _ = await self._seed(session)
            self.assertIsNone(school.logo_storage_uri)
            school.logo_storage_uri = "/var/material_storage/ab/abc/logo.png"
            await session.commit()
            refreshed = await session.get(School, school.id)
            self.assertEqual(
                refreshed.logo_storage_uri, "/var/material_storage/ab/abc/logo.png"
            )

    async def test_batch_upload_defaults_to_processing(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            batch = EssayBatchUpload(
                id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                class_id=klass.id, uploaded_by_external_identity="prof_lote",
                total_pages=3,
            )
            session.add(batch)
            await session.commit()
            self.assertEqual(batch.status, "PROCESSING")
            self.assertEqual(batch.total_pages, 3)

    async def test_batch_upload_rejects_unknown_status(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            with self.assertRaises(IntegrityError):
                await self._batch(session, school, klass, prompt, status="FINISHED")

    async def test_batch_page_defaults_to_needs_review(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            batch = await self._batch(session, school, klass, prompt, total_pages=1)
            page = EssayBatchPage(
                id=uuid.uuid4(), batch_id=batch.id, page_number=1,
                storage_uri="/var/material_storage/aa/aaa/page_1.png",
            )
            session.add(page)
            await session.commit()
            self.assertEqual(page.status, "NEEDS_REVIEW")
            self.assertIsNone(page.ocr_name_raw)
            self.assertIsNone(page.ocr_cpf_raw)
            self.assertIsNone(page.ocr_body_text)
            self.assertIsNone(page.matched_student_id)
            self.assertIsNone(page.essay_submission_id)

    async def test_batch_page_rejects_unknown_status(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            batch = await self._batch(session, school, klass, prompt, total_pages=1)
            session.add(EssayBatchPage(
                id=uuid.uuid4(), batch_id=batch.id, page_number=1,
                storage_uri="/x.png", status="PENDING",
            ))
            with self.assertRaises(IntegrityError):
                await session.commit()

    async def test_batch_page_rejects_page_number_zero(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            batch = await self._batch(session, school, klass, prompt, total_pages=1)
            session.add(EssayBatchPage(
                id=uuid.uuid4(), batch_id=batch.id, page_number=0, storage_uri="/x.png",
            ))
            with self.assertRaises(IntegrityError):
                await session.commit()

    async def test_batch_page_number_is_unique_per_batch(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            batch = await self._batch(session, school, klass, prompt, total_pages=2)
            session.add_all([
                EssayBatchPage(
                    id=uuid.uuid4(), batch_id=batch.id, page_number=1, storage_uri="/a.png"
                ),
                EssayBatchPage(
                    id=uuid.uuid4(), batch_id=batch.id, page_number=1, storage_uri="/b.png"
                ),
            ])
            with self.assertRaises(IntegrityError):
                await session.commit()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_models.py -q`
Expected: FAIL com `ImportError: cannot import name 'EssayBatchPage' from 'agente_ia_edu.db.models'`.

- [ ] **Step 3: Write the models**

Criar `src/agente_ia_edu/db/models/essay_batch.py`:

```python
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
```

- [ ] **Step 4: Add the School column**

Em `src/agente_ia_edu/db/models/admin.py`, na classe `School`, logo depois da linha `metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)` (linha ~60):

```python
    # Logo da escola, caminho em MaterialStorage (o mesmo storage local
    # content-addressed que os materiais de apoio de proposta ja usam).
    # Existe para estampar a folha de resposta de redacao gerada pelo sistema
    # (services/essay_answer_sheet.py); NULL = a folha sai sem logo, nunca
    # falha por causa disso (spec 2026-09-28 s7).
    logo_storage_uri: Mapped[str | None] = mapped_column(String(500))
```

- [ ] **Step 5: Export the new models**

Em `src/agente_ia_edu/db/models/__init__.py`, logo depois da linha `from .essay_correction import EssayCorrection`:

```python
from .essay_batch import EssayBatchPage, EssayBatchUpload
```

E em `__all__`, logo depois de `"EssayCorrection",`:

```python
    "EssayBatchUpload",
    "EssayBatchPage",
```

- [ ] **Step 6: Run test to verify it passes**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_models.py -q`
Expected: PASS (7 testes).

- [ ] **Step 7: Write the migration**

Criar `migrations/versions/056_essay_batch_upload.py`. O head atual e `055_essay_prompt_soft_delete` (confirmado varrendo `migrations/versions/*.py` por `revision`/`down_revision`) — reconfirmar antes de escrever, caso outra leva tenha entrado no meio:

```python
"""Envio em lote de redacoes fisicas: duas tabelas novas + logo da escola.

Revision ID: 056_essay_batch_upload
Revises: 055_essay_prompt_soft_delete

Puramente aditiva: duas tabelas que nao existiam e uma coluna nullable em
schools (default NULL = toda escola atual continua sem logo, e a folha de
resposta gerada sai sem logo em vez de falhar). Nenhuma linha existente e
tocada, nenhuma tabela existente muda de forma alem dessa coluna.

As FKs compostas (school_id, essay_prompt_id) e (school_id, class_id) sao o que
impede no banco um lote cuja proposta e de uma escola e cuja turma e de outra -
mesma convencao de prompt_assignments.
"""

from alembic import op
import sqlalchemy as sa

revision = "056_essay_batch_upload"
down_revision = "055_essay_prompt_soft_delete"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column("schools", sa.Column("logo_storage_uri", sa.String(length=500), nullable=True))

    op.create_table(
        "essay_batch_uploads",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("essay_prompt_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("uploaded_by_external_identity", sa.String(length=255), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("total_pages", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["school_id", "essay_prompt_id"],
            ["essay_prompts.school_id", "essay_prompts.id"],
            ondelete="RESTRICT",
            name="fk_essay_batch_uploads_school_prompt",
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "class_id"],
            ["classes.school_id", "classes.id"],
            ondelete="RESTRICT",
            name="fk_essay_batch_uploads_school_class",
        ),
        sa.UniqueConstraint("school_id", "id", name="uq_essay_batch_uploads_school_id_id"),
        sa.CheckConstraint("status IN ('PROCESSING', 'DONE')", name="ck_essay_batch_uploads_status"),
        sa.CheckConstraint(
            "total_pages >= 0", name="ck_essay_batch_uploads_total_pages_non_negative"
        ),
    )
    op.create_index("ix_essay_batch_uploads_school_id", "essay_batch_uploads", ["school_id"])
    op.create_index(
        "ix_essay_batch_uploads_essay_prompt_id", "essay_batch_uploads", ["essay_prompt_id"]
    )
    op.create_index("ix_essay_batch_uploads_class_id", "essay_batch_uploads", ["class_id"])

    op.create_table(
        "essay_batch_pages",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("batch_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("storage_uri", sa.String(length=500), nullable=False),
        sa.Column("ocr_name_raw", sa.String(length=255), nullable=True),
        sa.Column("ocr_cpf_raw", sa.String(length=20), nullable=True),
        sa.Column("ocr_body_text", sa.Text(), nullable=True),
        sa.Column("matched_student_id", sa.Uuid(), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("essay_submission_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.PrimaryKeyConstraint("id"),
        sa.ForeignKeyConstraint(
            ["batch_id"], ["essay_batch_uploads.id"], ondelete="CASCADE",
            name="fk_essay_batch_pages_batch",
        ),
        sa.ForeignKeyConstraint(
            ["matched_student_id"], ["students.id"], ondelete="RESTRICT",
            name="fk_essay_batch_pages_student",
        ),
        sa.ForeignKeyConstraint(
            ["essay_submission_id"], ["essay_submissions.id"], ondelete="RESTRICT",
            name="fk_essay_batch_pages_submission",
        ),
        sa.UniqueConstraint("batch_id", "page_number", name="uq_essay_batch_pages_number"),
        sa.CheckConstraint("page_number >= 1", name="ck_essay_batch_pages_page_number_positive"),
        sa.CheckConstraint(
            "status IN ('MATCHED_AUTO', 'NEEDS_REVIEW', 'RESOLVED_MANUAL')",
            name="ck_essay_batch_pages_status",
        ),
    )
    op.create_index("ix_essay_batch_pages_batch_id", "essay_batch_pages", ["batch_id"])
    op.create_index(
        "ix_essay_batch_pages_matched_student_id", "essay_batch_pages", ["matched_student_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_essay_batch_pages_matched_student_id", table_name="essay_batch_pages")
    op.drop_index("ix_essay_batch_pages_batch_id", table_name="essay_batch_pages")
    op.drop_table("essay_batch_pages")
    op.drop_index("ix_essay_batch_uploads_class_id", table_name="essay_batch_uploads")
    op.drop_index("ix_essay_batch_uploads_essay_prompt_id", table_name="essay_batch_uploads")
    op.drop_index("ix_essay_batch_uploads_school_id", table_name="essay_batch_uploads")
    op.drop_table("essay_batch_uploads")
    op.drop_column("schools", "logo_storage_uri")
```

- [ ] **Step 8: Write the PostgreSQL migration test**

Criar `tests/test_r4_essay_batch_migration_postgresql.py` (mesma forma de `tests/test_reception_migration_postgresql.py`, que pula quando o Postgres de teste na porta 5433 nao esta de pe):

```python
"""Validacao em PostgreSQL da migration 056 (envio em lote de redacao).

Ler a migration nao prova nada: uma coluna nova em tabela ja existente
(schools.logo_storage_uri) so aparece rodando o upgrade de verdade. Banco
descartavel na porta 5433, mesmo alvo dos outros testes de migration.
"""

from __future__ import annotations

import os
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from tests._postgres_test_db import create_database, drop_database


class TestEssayBatchMigrationPostgreSQL(unittest.TestCase):
    database_name = "agente_ia_edu_essay_batch_test"
    admin_url = os.getenv(
        "ESSAY_BATCH_TEST_ADMIN_DATABASE_URL",
        "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/postgres",
    )
    database_url = os.getenv(
        "ESSAY_BATCH_TEST_DATABASE_URL",
        f"postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            engine = create_engine(
                cls.admin_url,
                connect_args={"autocommit": True},
                execution_options={"isolation_level": "AUTOCOMMIT"},
            )
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            engine.dispose()
        except Exception as exc:
            raise unittest.SkipTest(
                "PostgreSQL de teste indisponivel; migration 056 nao validada."
            ) from exc

    def setUp(self):
        drop_database(self.admin_url, self.database_name)
        create_database(self.admin_url, self.database_name)

    def tearDown(self):
        drop_database(self.admin_url, self.database_name)

    def _alembic_config(self) -> Config:
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", self.database_url)
        return config

    def test_upgrade_056_creates_batch_tables_and_logo_column(self):
        config = self._alembic_config()
        command.upgrade(config, "056_essay_batch_upload")

        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            table_names = set(inspector.get_table_names())
            self.assertIn("essay_batch_uploads", table_names)
            self.assertIn("essay_batch_pages", table_names)

            school_columns = {c["name"] for c in inspector.get_columns("schools")}
            self.assertIn("logo_storage_uri", school_columns)

            upload_fks = {
                tuple(fk["constrained_columns"]): (fk["referred_table"], tuple(fk["referred_columns"]))
                for fk in inspector.get_foreign_keys("essay_batch_uploads")
            }
            self.assertEqual(
                upload_fks[("school_id", "essay_prompt_id")],
                ("essay_prompts", ("school_id", "id")),
            )
            self.assertEqual(
                upload_fks[("school_id", "class_id")], ("classes", ("school_id", "id"))
            )

            page_checks = {
                item["name"]: item["sqltext"]
                for item in inspector.get_check_constraints("essay_batch_pages")
            }
            self.assertIn("MATCHED_AUTO", page_checks["ck_essay_batch_pages_status"])
            self.assertIn("RESOLVED_MANUAL", page_checks["ck_essay_batch_pages_status"])

            page_fks = {
                tuple(fk["constrained_columns"]): fk
                for fk in inspector.get_foreign_keys("essay_batch_pages")
            }
            self.assertEqual(page_fks[("batch_id",)]["referred_table"], "essay_batch_uploads")
            self.assertEqual(page_fks[("batch_id",)]["options"]["ondelete"], "CASCADE")
            self.assertEqual(page_fks[("matched_student_id",)]["referred_table"], "students")
            self.assertEqual(
                page_fks[("essay_submission_id",)]["referred_table"], "essay_submissions"
            )
        finally:
            engine.dispose()

    def test_downgrade_056_is_clean(self):
        config = self._alembic_config()
        command.upgrade(config, "056_essay_batch_upload")
        command.downgrade(config, "055_essay_prompt_soft_delete")

        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            table_names = set(inspector.get_table_names())
            self.assertNotIn("essay_batch_uploads", table_names)
            self.assertNotIn("essay_batch_pages", table_names)
            school_columns = {c["name"] for c in inspector.get_columns("schools")}
            self.assertNotIn("logo_storage_uri", school_columns)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 9: Run both test files**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_models.py tests/test_r4_essay_batch_migration_postgresql.py -q`
Expected: PASS. Se o Postgres na 5433 nao estiver de pe, o segundo arquivo aparece como `skipped` — nesse caso SUBIR o banco (`docker compose up -d` a partir da raiz do projeto, ver `docker-compose.yml`) e rodar de novo, porque uma fase aditiva precisa da migration executada de verdade, nao só lida.

- [ ] **Step 10: Run the full suite**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests -q`
Expected: PASS (nenhuma regressão — a mudança é puramente aditiva).

- [ ] **Step 11: Commit**

```bash
git add src/agente_ia_edu/db/models/essay_batch.py src/agente_ia_edu/db/models/__init__.py src/agente_ia_edu/db/models/admin.py migrations/versions/056_essay_batch_upload.py tests/test_r4_essay_batch_models.py tests/test_r4_essay_batch_migration_postgresql.py
git commit -m "feat(redacao): tabelas de envio em lote + coluna de logo da escola

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 2: Folha de resposta padrão em PDF (`services/essay_answer_sheet.py`)

**Files:**
- Create: `src/agente_ia_edu/services/essay_answer_sheet.py`
- Test: `tests/test_r4_essay_answer_sheet.py`

**Interfaces:**
- Consumes: nada de outras tarefas.
- Produces:
  - `HEADER_REGION_FRACTION: float = 0.22` — a fração da ALTURA da página que o cabeçalho (logo + título + caixas de NOME e CPF) ocupa. **Este módulo é o dono único dessa constante**; `services/essay_batch.py` (Task 4) a importa daqui em vez de redeclarar, então a folha impressa e o recorte de OCR nunca podem divergir.
  - `LINE_COUNT: int = 30`
  - `answer_sheet_available() -> bool`
  - `render_answer_sheet_pdf(*, prompt_title: str, logo_path: str | None = None, copies: int = 1) -> bytes`
  - Consumidos por Task 4 (`HEADER_REGION_FRACTION`) e Task 11 (rota).

- [ ] **Step 1: Write the failing test**

Criar `tests/test_r4_essay_answer_sheet.py`:

```python
"""R4 lote - folha de resposta padrao em branco, gerada pelo sistema.

A geometria aqui NAO e cosmetica: o cabecalho ocupa exatamente
HEADER_REGION_FRACTION da altura da pagina, e e essa mesma fracao que
services/essay_batch.py recorta depois pra ler nome e CPF por OCR. Se a
geometria mudar sem a fracao mudar junto, o OCR passa a ler o lugar errado -
por isso os testes abaixo checam posicao, nao so "gerou algum PDF".
"""

import unittest

from agente_ia_edu.services.essay_answer_sheet import (
    HEADER_REGION_FRACTION,
    LINE_COUNT,
    answer_sheet_available,
    render_answer_sheet_pdf,
)


@unittest.skipUnless(answer_sheet_available(), "pymupdf nao instalado")
class AnswerSheetTests(unittest.TestCase):
    def _open(self, data: bytes):
        import pymupdf

        return pymupdf.open(stream=data, filetype="pdf")

    def test_one_page_per_copy(self):
        doc = self._open(render_answer_sheet_pdf(prompt_title="Tema X", copies=3))
        try:
            self.assertEqual(doc.page_count, 3)
        finally:
            doc.close()

    def test_defaults_to_one_copy(self):
        doc = self._open(render_answer_sheet_pdf(prompt_title="Tema X"))
        try:
            self.assertEqual(doc.page_count, 1)
        finally:
            doc.close()

    def test_rejects_non_positive_copies(self):
        with self.assertRaises(ValueError):
            render_answer_sheet_pdf(prompt_title="Tema X", copies=0)

    def test_header_labels_and_title_are_inside_the_header_region(self):
        data = render_answer_sheet_pdf(
            prompt_title="Os desafios da mobilidade urbana no Brasil"
        )
        doc = self._open(data)
        try:
            page = doc[0]
            header_bottom = page.rect.height * HEADER_REGION_FRACTION
            for needle in ("NOME COMPLETO DO PARTICIPANTE", "CPF", "FOLHA DE REDA"):
                hits = page.search_for(needle)
                self.assertTrue(hits, f"{needle!r} nao encontrado na folha")
                self.assertLess(
                    max(rect.y1 for rect in hits), header_bottom,
                    f"{needle!r} vazou para fora da regiao de cabecalho",
                )
            title_hits = page.search_for("Os desafios da mobilidade")
            self.assertTrue(title_hits)
            self.assertLess(max(r.y1 for r in title_hits), header_bottom)
        finally:
            doc.close()

    def test_numbered_lines_start_below_the_header_region(self):
        doc = self._open(render_answer_sheet_pdf(prompt_title="Tema X"))
        try:
            page = doc[0]
            header_bottom = page.rect.height * HEADER_REGION_FRACTION
            first = page.search_for("01")
            last = page.search_for(f"{LINE_COUNT:02d}")
            self.assertTrue(first, "numero de linha 01 nao encontrado")
            self.assertTrue(last, f"numero de linha {LINE_COUNT:02d} nao encontrado")
            self.assertGreater(min(r.y0 for r in first), header_bottom)
            self.assertLess(max(r.y1 for r in last), page.rect.height)
        finally:
            doc.close()

    def test_renders_without_a_logo(self):
        data = render_answer_sheet_pdf(prompt_title="Tema X", logo_path=None)
        doc = self._open(data)
        try:
            self.assertEqual(doc.page_count, 1)
            self.assertEqual(len(doc[0].get_images(full=True)), 0)
        finally:
            doc.close()

    def test_embeds_the_logo_when_one_is_given(self):
        import tempfile
        from pathlib import Path

        import pymupdf

        tmp_dir = Path(tempfile.mkdtemp(prefix="r4_logo_"))
        logo_path = tmp_dir / "logo.png"
        source = pymupdf.open()
        page = source.new_page(width=120, height=60)
        page.insert_text((10, 35), "LOGO", fontsize=20)
        page.get_pixmap(dpi=150).save(str(logo_path))
        source.close()

        doc = self._open(
            render_answer_sheet_pdf(prompt_title="Tema X", logo_path=str(logo_path))
        )
        try:
            self.assertEqual(len(doc[0].get_images(full=True)), 1)
        finally:
            doc.close()

    def test_a_missing_logo_file_never_blocks_generation(self):
        doc = self._open(
            render_answer_sheet_pdf(
                prompt_title="Tema X", logo_path="/caminho/que/nao/existe/logo.png"
            )
        )
        try:
            self.assertEqual(doc.page_count, 1)
            self.assertEqual(len(doc[0].get_images(full=True)), 0)
        finally:
            doc.close()

    def test_accented_title_survives(self):
        doc = self._open(render_answer_sheet_pdf(prompt_title="Educação e cidadania"))
        try:
            self.assertTrue(doc[0].search_for("Educação e cidadania"))
        finally:
            doc.close()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_answer_sheet.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.services.essay_answer_sheet'`.

- [ ] **Step 3: Write the module**

Criar `src/agente_ia_edu/services/essay_answer_sheet.py`:

```python
"""Folha de resposta de redacao em branco, gerada pelo sistema pra impressao.

Spec: docs/superpowers/specs/2026-09-28-envio-lote-redacao-design.md s6.

Este modulo e o DONO UNICO da geometria da folha. ``HEADER_REGION_FRACTION``
e a fracao da altura da pagina que o cabecalho ocupa, e services/essay_batch.py
importa essa MESMA constante daqui pra recortar a regiao de cabecalho da foto
antes de rodar OCR nela. Mexer na geometria sem mexer na fracao (ou vice-versa)
faz o OCR passar a ler o lugar errado - as duas coisas sao um acoplamento real,
por isso moram juntas em vez de cada modulo ter a sua copia.

Por que NAO o motor ``Story`` de essay_pdf_export.py (que a spec s6 sugeria):
Story faz layout de FLUXO e nao da garantia nenhuma sobre em que altura da
pagina um elemento cai. Uma folha de resposta e um formulario de geometria
FIXA cuja unica razao de existir e ser previsivel o suficiente pra ser lida
por maquina depois. Usamos entao as primitivas de baixo nivel do mesmo modulo
PyMuPDF que essay_pdf_export.py::render_pdf ja usa pras paginas de imagem
(draw_rect / draw_line / insert_text / insert_image / tobytes(deflate, garbage)).
"""

from __future__ import annotations

import logging

logger = logging.getLogger(__name__)

# Fracao da ALTURA da pagina ocupada pelo cabecalho (logo + titulo + caixas de
# NOME e CPF). Mesma proporcao da folha oficial do ENEM que serviu de
# referencia visual. services/essay_batch.py importa esta constante.
HEADER_REGION_FRACTION = 0.22

# Linhas numeradas pra redacao. 30 e o que cabe confortavelmente numa A4 abaixo
# do cabecalho (passo de ~20pt, validado renderizando de verdade).
LINE_COUNT = 30

# Caixinhas de um caractere, estilo ENEM - guiam a letra do aluno e melhoram
# muito a leitura do nome por OCR.
_NAME_BOXES = 40
_CPF_BOXES = 11

_MARGIN = 36.0
_BOX_HEIGHT = 16.0
_GRID_COLOR = (0.4, 0.4, 0.4)
_FRAME_COLOR = (0.6, 0.6, 0.6)
_RULE_COLOR = (0.75, 0.75, 0.75)
_NUMBER_COLOR = (0.5, 0.5, 0.5)


def answer_sheet_available() -> bool:
    """Mesma convencao de essay_pdf_export.pdf_available(): pymupdf e um extra
    opcional (``recovery`` em pyproject.toml), entao a rota devolve 503 em vez
    de 500 quando ele nao esta instalado."""
    try:
        import pymupdf  # noqa: F401

        return True
    except Exception:
        return False


def _draw_char_boxes(page, pymupdf, *, x: float, y: float, width: float, count: int) -> None:
    box_width = width / count
    for index in range(count):
        page.draw_rect(
            pymupdf.Rect(x + index * box_width, y, x + (index + 1) * box_width, y + _BOX_HEIGHT),
            color=_GRID_COLOR, width=0.5,
        )


def _draw_sheet(page, pymupdf, *, prompt_title: str, logo_path: str | None) -> None:
    width, height = page.rect.width, page.rect.height
    header_bottom = height * HEADER_REGION_FRACTION
    inner_left = _MARGIN + 6

    page.draw_rect(
        pymupdf.Rect(_MARGIN, _MARGIN, width - _MARGIN, header_bottom),
        color=_FRAME_COLOR, width=0.8,
    )

    if logo_path:
        try:
            page.insert_image(
                pymupdf.Rect(inner_left, _MARGIN + 6, inner_left + 64, _MARGIN + 40),
                filename=logo_path, keep_proportion=True,
            )
        except Exception:
            # Logo ilegivel/apagada do disco nunca bloqueia a geracao da folha
            # (spec s7) - a folha sai sem logo, igual a uma escola que nunca
            # cadastrou uma.
            logger.warning("logo da escola nao pode ser desenhada: %s", logo_path)

    page.insert_text((inner_left + 74, _MARGIN + 22), "FOLHA DE REDAÇÃO", fontsize=12, fontname="hebo")
    page.insert_text((inner_left + 74, _MARGIN + 36), prompt_title[:70], fontsize=9)

    usable = width - 2 * _MARGIN - 12
    name_top = _MARGIN + 52
    page.insert_text((inner_left, name_top - 4), "NOME COMPLETO DO PARTICIPANTE", fontsize=7)
    _draw_char_boxes(page, pymupdf, x=inner_left, y=name_top, width=usable, count=_NAME_BOXES)

    cpf_top = name_top + 30
    page.insert_text((inner_left, cpf_top - 4), "CPF", fontsize=7)
    _draw_char_boxes(page, pymupdf, x=inner_left, y=cpf_top, width=usable * 0.35, count=_CPF_BOXES)

    rules_top = header_bottom + 18
    rules_bottom = height - _MARGIN
    step = (rules_bottom - rules_top) / LINE_COUNT
    for index in range(LINE_COUNT):
        line_y = rules_top + (index + 1) * step
        page.insert_text(
            (_MARGIN, line_y - 2), f"{index + 1:02d}", fontsize=7, color=_NUMBER_COLOR
        )
        page.draw_line(
            pymupdf.Point(_MARGIN + 18, line_y), pymupdf.Point(width - _MARGIN, line_y),
            color=_RULE_COLOR, width=0.6,
        )


def render_answer_sheet_pdf(
    *, prompt_title: str, logo_path: str | None = None, copies: int = 1
) -> bytes:
    """``copies`` folhas identicas, uma por pagina do PDF gerado."""
    if copies < 1:
        raise ValueError(f"copies must be at least 1, got {copies}")

    import pymupdf

    mediabox = pymupdf.paper_rect("a4")
    doc = pymupdf.open()
    try:
        for _ in range(copies):
            page = doc.new_page(width=mediabox.width, height=mediabox.height)
            _draw_sheet(page, pymupdf, prompt_title=prompt_title or "", logo_path=logo_path)
        # deflate+garbage pelo mesmo motivo documentado em
        # essay_pdf_export.render_pdf: sem eles o stream da logo embutida vai
        # sem compressao nenhuma.
        return doc.tobytes(deflate=True, garbage=3)
    finally:
        doc.close()


__all__ = [
    "HEADER_REGION_FRACTION",
    "LINE_COUNT",
    "answer_sheet_available",
    "render_answer_sheet_pdf",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_answer_sheet.py -q`
Expected: PASS (9 testes).

- [ ] **Step 5: Eyeball the generated sheet once**

Run:
```bash
/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -c "
from agente_ia_edu.services.essay_answer_sheet import render_answer_sheet_pdf
open('/tmp/folha.pdf','wb').write(render_answer_sheet_pdf(prompt_title='Os desafios da mobilidade urbana no Brasil', copies=2))
print('ok')
"
open /tmp/folha.pdf
```
Expected: duas páginas A4 idênticas, com moldura de cabeçalho no topo, "FOLHA DE REDAÇÃO" + título, uma fileira de 40 caixinhas sob "NOME COMPLETO DO PARTICIPANTE", 11 caixinhas sob "CPF", e 30 linhas numeradas 01..30 abaixo. Se algum elemento do cabeçalho ficar visualmente ABAIXO da moldura, a geometria está errada — corrigir antes de seguir.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/essay_answer_sheet.py tests/test_r4_essay_answer_sheet.py
git commit -m "feat(redacao): folha de resposta padrao em PDF com geometria fixa de cabecalho

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 3: Normalização e matching de nome/CPF (funções puras)

**Files:**
- Create: `src/agente_ia_edu/services/essay_batch.py` (só as funções puras nesta tarefa; a classe de serviço entra na Task 4)
- Test: `tests/test_r4_essay_batch_matching.py`

**Interfaces:**
- Consumes: nada de outras tarefas (importa `HEADER_REGION_FRACTION` de `services/essay_answer_sheet.py`, Task 2, só pra reexportá-la implicitamente via uso na Task 4).
- Produces:
  - `normalize_person_name(value: str | None) -> str` — maiúsculas, sem acento, espaços colapsados; `""` pra entrada vazia/None.
  - `normalize_cpf(value: str | None) -> str` — só os dígitos; `""` pra entrada vazia/None.
  - `parse_header_text(header_text: str | None) -> tuple[str | None, str | None]` — `(nome_normalizado, cpf_digitos)`, cada um `None` quando não foi possível ler.
  - `match_student(name_raw: str | None, roster: Sequence[tuple[uuid.UUID, str]]) -> uuid.UUID | None` — o `student_id` sse exatamente um nome do roster bate; `None` pra zero ou 2+.
  - `text_from_ocr_tokens(tokens: list[dict] | None) -> str`
  - Consumidos pelas Tasks 4, 6, 7, 8.

- [ ] **Step 1: Write the failing test**

Criar `tests/test_r4_essay_batch_matching.py`:

```python
"""R4 lote - normalizacao de nome/CPF, leitura do cabecalho e match contra a turma.

Funcoes puras, sem banco e sem IA: sao a parte do sistema que decide se uma
folha vira redacao de alguem automaticamente ou cai na fila do professor, e por
isso e a parte que mais precisa ser deterministica e testada a fundo.
"""

import unittest
import uuid

from agente_ia_edu.services.essay_batch import (
    match_student,
    normalize_cpf,
    normalize_person_name,
    parse_header_text,
    text_from_ocr_tokens,
)


class NormalizePersonNameTests(unittest.TestCase):
    def test_uppercases(self):
        self.assertEqual(normalize_person_name("joao da silva"), "JOAO DA SILVA")

    def test_strips_accents(self):
        self.assertEqual(
            normalize_person_name("Antônio José Gonçalves"), "ANTONIO JOSE GONCALVES"
        )

    def test_collapses_whitespace(self):
        self.assertEqual(
            normalize_person_name("  Maria   Clara \t Souza \n"), "MARIA CLARA SOUZA"
        )

    def test_none_and_empty_become_empty_string(self):
        self.assertEqual(normalize_person_name(None), "")
        self.assertEqual(normalize_person_name("   "), "")

    def test_is_idempotent(self):
        once = normalize_person_name("Ana Lúcia  Ferreira")
        self.assertEqual(normalize_person_name(once), once)


class NormalizeCpfTests(unittest.TestCase):
    def test_keeps_digits_only(self):
        self.assertEqual(normalize_cpf("123.456.789-00"), "12345678900")

    def test_handles_spaces_and_none(self):
        self.assertEqual(normalize_cpf(" 111 222 333 44 "), "11122233344")
        self.assertEqual(normalize_cpf(None), "")


class ParseHeaderTextTests(unittest.TestCase):
    def test_reads_name_from_the_line_below_the_label(self):
        header = (
            "FOLHA DE REDACAO\n"
            "Os desafios da mobilidade urbana\n"
            "NOME COMPLETO DO PARTICIPANTE\n"
            "Joao da Silva Pereira\n"
            "CPF\n"
            "123.456.789-00\n"
        )
        name, cpf = parse_header_text(header)
        self.assertEqual(name, "JOAO DA SILVA PEREIRA")
        self.assertEqual(cpf, "12345678900")

    def test_reads_name_from_the_same_line_as_the_label(self):
        name, cpf = parse_header_text("NOME: Maria Clara Souza\nCPF: 98765432100")
        self.assertEqual(name, "MARIA CLARA SOUZA")
        self.assertEqual(cpf, "98765432100")

    def test_a_single_line_header_does_not_swallow_the_cpf_into_the_name(self):
        # O OCR de uma regiao pequena frequentemente devolve TUDO numa linha so -
        # sem cortar no rotulo CPF, o nome viraria "JOAO DA SILVA CPF 123..." e
        # nunca casaria com aluno nenhum.
        name, cpf = parse_header_text(
            "NOME COMPLETO DO PARTICIPANTE Joao da Silva CPF 123.456.789-00"
        )
        self.assertEqual(name, "JOAO DA SILVA")
        self.assertEqual(cpf, "12345678900")

    def test_a_single_line_header_without_the_cpf_label_still_cuts_at_the_digits(self):
        name, cpf = parse_header_text("NOME COMPLETO Ana Lucia Ferreira 111.222.333-44")
        self.assertEqual(name, "ANA LUCIA FERREIRA")
        self.assertEqual(cpf, "11122233344")

    def test_never_picks_the_prompt_title_as_the_name(self):
        header = (
            "FOLHA DE REDACAO\n"
            "Os desafios da mobilidade urbana no Brasil\n"
            "NOME COMPLETO DO PARTICIPANTE\n"
            "Pedro Alves\n"
        )
        name, _ = parse_header_text(header)
        self.assertEqual(name, "PEDRO ALVES")

    def test_falls_back_to_the_longest_wordy_line_when_the_label_was_misread(self):
        # OCR comeu a palavra NOME; ainda assim ha uma unica linha que parece
        # um nome de pessoa (duas ou mais palavras, sem digito).
        name, cpf = parse_header_text("N0ME C0MPLET0\nRoberto Carlos Nascimento\nCPF 11122233344")
        self.assertEqual(name, "ROBERTO CARLOS NASCIMENTO")
        self.assertEqual(cpf, "11122233344")

    def test_cpf_is_optional(self):
        name, cpf = parse_header_text("NOME COMPLETO DO PARTICIPANTE\nCarla Dias")
        self.assertEqual(name, "CARLA DIAS")
        self.assertIsNone(cpf)

    def test_empty_header_returns_two_nones(self):
        self.assertEqual(parse_header_text(""), (None, None))
        self.assertEqual(parse_header_text(None), (None, None))

    def test_header_with_no_name_like_line_returns_none_name(self):
        name, cpf = parse_header_text("CPF\n12345678900")
        self.assertIsNone(name)
        self.assertEqual(cpf, "12345678900")


class MatchStudentTests(unittest.TestCase):
    def setUp(self):
        self.ana = uuid.uuid4()
        self.joao = uuid.uuid4()
        self.joao2 = uuid.uuid4()
        self.roster = [
            (self.ana, "Ana Lúcia Ferreira"),
            (self.joao, "João da Silva"),
        ]

    def test_exactly_one_match_wins(self):
        self.assertEqual(match_student("ANA LUCIA FERREIRA", self.roster), self.ana)

    def test_match_is_accent_and_case_insensitive(self):
        self.assertEqual(match_student("joão da silva", self.roster), self.joao)

    def test_no_match_returns_none(self):
        self.assertIsNone(match_student("Carlos Mendes", self.roster))

    def test_homonyms_return_none(self):
        roster = self.roster + [(self.joao2, "Joao Da Silva")]
        self.assertIsNone(match_student("João da Silva", roster))

    def test_empty_name_returns_none(self):
        self.assertIsNone(match_student(None, self.roster))
        self.assertIsNone(match_student("   ", self.roster))

    def test_empty_roster_returns_none(self):
        self.assertIsNone(match_student("Ana Lúcia Ferreira", []))


class TextFromOcrTokensTests(unittest.TestCase):
    def test_rebuilds_text_from_offsets(self):
        tokens = [
            {"text": "Ola", "confidence": 0.9, "start": 0, "end": 3},
            {"text": "mundo", "confidence": 0.9, "start": 4, "end": 9},
        ]
        self.assertEqual(text_from_ocr_tokens(tokens), "Ola mundo")

    def test_uncovered_gap_defaults_to_a_space(self):
        tokens = [
            {"text": "a", "confidence": 1.0, "start": 0, "end": 1},
            {"text": "b", "confidence": 1.0, "start": 2, "end": 3},
        ]
        self.assertEqual(text_from_ocr_tokens(tokens), "a b")

    def test_empty_and_none_become_empty_string(self):
        self.assertEqual(text_from_ocr_tokens([]), "")
        self.assertEqual(text_from_ocr_tokens(None), "")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_matching.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.services.essay_batch'`.

- [ ] **Step 3: Write the module (pure functions only)**

Criar `src/agente_ia_edu/services/essay_batch.py`:

```python
"""Envio em lote de redacoes fisicas pelo professor.

Spec: docs/superpowers/specs/2026-09-28-envio-lote-redacao-design.md

Este arquivo comeca com as funcoes PURAS (normalizacao, leitura do cabecalho,
match contra a turma) porque sao elas que decidem se uma folha de papel vira
automaticamente a redacao de alguem ou cai na fila do professor - a parte do
sistema que mais precisa ser deterministica e auditavel. A classe de servico
(banco, OCR, storage) vem depois delas.
"""

from __future__ import annotations

import re
import unicodedata
import uuid
from collections.abc import Sequence


def normalize_person_name(value: str | None) -> str:
    """Maiusculas, sem acento, espacos colapsados (spec s4.2).

    NFKD + descarte de combining marks e a mesma tecnica que o resto do
    projeto usa pra comparar texto acentuado - decompoe "Ç" em "C" + cedilha e
    joga fora a cedilha, em vez de depender de uma tabela de substituicao
    manual que sempre esquece alguma letra.
    """
    decomposed = unicodedata.normalize("NFKD", value or "")
    without_marks = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return " ".join(without_marks.upper().split())


def normalize_cpf(value: str | None) -> str:
    """So os digitos - o aluno pode escrever com ponto, com traco ou sem nada."""
    return "".join(ch for ch in (value or "") if ch.isdigit())


# 11 digitos com ate 2 caracteres de pontuacao/espaco entre os grupos: pega
# "123.456.789-00", "123 456 789 00" e "12345678900" igualmente.
_CPF_PATTERN = re.compile(r"\d{3}\D{0,2}\d{3}\D{0,2}\d{3}\D{0,2}\d{2}")

# O rotulo impresso pela folha que o proprio sistema gera e "NOME COMPLETO DO
# PARTICIPANTE", mas o OCR pode comer parte dele, e um professor pode usar uma
# folha antiga onde esta so "NOME:" - as tres formas sao aceitas.
_NAME_LABEL_PATTERN = re.compile(r"^NOME(\s+COMPLETO)?(\s+DO\s+PARTICIPANTE)?\s*:?\s*")

_CPF_LABEL_PATTERN = re.compile(r"^CPF\b\s*:?\s*")

# Rotulos e cabecalhos impressos na propria folha, que nunca sao o nome de
# ninguem - descartados antes do fallback abaixo.
_PRINTED_LABELS = ("FOLHA DE REDACAO", "NOME", "CPF")


def _strip_trailing_cpf(normalized_line: str) -> str:
    """Corta a linha no primeiro sinal de CPF (o rotulo ou os digitos).

    Confirmado como necessario: o OCR de uma REGIAO pequena (o cabecalho) muito
    frequentemente devolve tudo numa unica linha - "NOME COMPLETO DO
    PARTICIPANTE JOAO DA SILVA CPF 123.456.789-00". Sem este corte o nome
    extraido seria "JOAO DA SILVA CPF 123.456.789-00", que nao casa com aluno
    nenhum e manda toda folha bem preenchida pra fila manual.
    """
    cut = len(normalized_line)
    digits = _CPF_PATTERN.search(normalized_line)
    if digits is not None:
        cut = min(cut, digits.start())
    label = re.search(r"\bCPF\b", normalized_line)
    if label is not None:
        cut = min(cut, label.start())
    return normalized_line[:cut].strip()


def _looks_like_a_person_name(normalized_line: str) -> bool:
    """Duas ou mais palavras, so letras e espaco. Deliberadamente estreito: e
    usado apenas no fallback (quando o rotulo NOME nao foi lido), e um falso
    positivo aqui vira uma redacao atribuida ao aluno errado, enquanto um falso
    negativo so manda a pagina pra fila do professor."""
    if any(ch.isdigit() for ch in normalized_line):
        return False
    words = normalized_line.split()
    return len(words) >= 2 and all(word.isalpha() for word in words)


def parse_header_text(header_text: str | None) -> tuple[str | None, str | None]:
    """Extrai (nome normalizado, CPF so-digitos) do texto lido por OCR na
    REGIAO de cabecalho da folha.

    Estrategia, em ordem:
      1. A linha que comeca com o rotulo NOME - se tiver conteudo depois do
         rotulo, e esse o nome, cortado no primeiro sinal de CPF (ver
         _strip_trailing_cpf: o OCR do cabecalho costuma devolver tudo numa
         linha so); senao, a proxima linha que nao seja outro rotulo nem um CPF.
      2. Fallback (rotulo ilegivel): a linha mais longa que "parece nome de
         pessoa" e nao e um dos rotulos impressos na propria folha. O titulo da
         proposta tambem e impresso no cabecalho, mas ele quase sempre tem
         digito, artigo ou pontuacao - e, quando nao tem, o caminho 1 ja
         resolveu. Se o fallback errar, a pagina cai na fila manual, que e o
         comportamento seguro.

    Devolve o nome JA NORMALIZADO (e o que vai tanto pro match quanto pra
    coluna ocr_name_raw - ver o docstring de EssayBatchPage).
    """
    lines = [line.strip() for line in (header_text or "").splitlines()]
    normalized_lines = [normalize_person_name(line) for line in lines]
    normalized_lines = [line for line in normalized_lines if line]

    cpf_match = _CPF_PATTERN.search(header_text or "")
    cpf = normalize_cpf(cpf_match.group(0)) if cpf_match else None
    if cpf is not None and len(cpf) != 11:
        cpf = None

    name: str | None = None
    for index, line in enumerate(normalized_lines):
        label = _NAME_LABEL_PATTERN.match(line)
        if label is None:
            continue
        remainder = _strip_trailing_cpf(line[label.end():].strip())
        if remainder:
            name = remainder
        else:
            for candidate in normalized_lines[index + 1:]:
                if _CPF_LABEL_PATTERN.match(candidate) or _CPF_PATTERN.search(candidate):
                    continue
                if _NAME_LABEL_PATTERN.match(candidate):
                    continue
                name = _strip_trailing_cpf(candidate)
                break
        break

    if not name:
        candidates = [
            line for line in normalized_lines
            if _looks_like_a_person_name(line)
            and not any(line.startswith(label) for label in _PRINTED_LABELS)
        ]
        if candidates:
            name = max(candidates, key=len)

    return (name or None), cpf


def match_student(
    name_raw: str | None, roster: Sequence[tuple[uuid.UUID, str]]
) -> uuid.UUID | None:
    """O student_id sse EXATAMENTE UM aluno do roster tem o mesmo nome
    normalizado (spec s4.3). Zero ou dois-ou-mais devolvem None, e a pagina vai
    pra fila de resolucao manual - homonimos na mesma turma nunca sao
    desempatados automaticamente, nem pelo CPF (decisao do brainstorm: o CPF e
    pista pro professor, nunca criterio de match).

    ``roster`` e uma sequencia de (student_id, full_name) - o nome vem de
    Person.full_name, montado pela query de alunos ativos da turma.
    """
    target = normalize_person_name(name_raw)
    if not target:
        return None
    matches = [
        student_id for student_id, full_name in roster
        if normalize_person_name(full_name) == target
    ]
    return matches[0] if len(matches) == 1 else None


def text_from_ocr_tokens(tokens: list[dict] | None) -> str:
    """Reconstroi o texto a partir dos offsets start/end dos proprios tokens.

    Mesma logica de essay_submission._reconstruct_text_from_tokens (e pelo mesmo
    motivo documentado la: concatenar t["text"] cola palavras vizinhas sempre
    que o espaco entre dois tokens nao virou token proprio), porem sobre os
    DICTS que EssaySubmissionPage.ocr_tokens guarda, e nao sobre os dataclasses
    EssayOcrToken. Duplicado de proposito em vez de importado: aquela funcao e
    privada do modulo de submissao e tipada pros dataclasses.
    """
    if not tokens:
        return ""
    length = max(token["end"] for token in tokens)
    buffer = [" "] * length
    for token in tokens:
        for offset, character in enumerate(token["text"]):
            position = token["start"] + offset
            if position < length:
                buffer[position] = character
    return "".join(buffer).strip()


__all__ = [
    "match_student",
    "normalize_cpf",
    "normalize_person_name",
    "parse_header_text",
    "text_from_ocr_tokens",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_matching.py -q`
Expected: PASS (21 testes).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/essay_batch.py tests/test_r4_essay_batch_matching.py
git commit -m "feat(redacao): normalizacao de nome/CPF e match de aluno para o envio em lote

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 4: OCR separado de cabeçalho e corpo (recorte por região)

**Files:**
- Modify: `src/agente_ia_edu/services/essay_batch.py` (acrescenta a classe `EssayBatchService` com o recorte e a leitura; as funções puras da Task 3 ficam como estão)
- Test: `tests/test_r4_essay_batch_ocr_regions.py`

**Interfaces:**
- Consumes: `HEADER_REGION_FRACTION` de `services/essay_answer_sheet.py` (Task 2); `text_from_ocr_tokens`, `parse_header_text` (Task 3); `EssaySubmissionService._ocr_page` e `EssaySubmissionPage` (já existentes).
- Produces:
  - `class EssayBatchService` com `__init__(self, session: AsyncSession, *, storage: MaterialStorage | None = None, transcriber: EssayTranscriptionProvider | None = None, correction_factory: Callable[[AsyncSession], Any] | None = None)`.
  - `EssayBatchService._crop_regions(image_path: Path, dest_dir: Path) -> tuple[Path, Path]` (staticmethod) → `(header_image_path, body_image_path)`.
  - `async EssayBatchService.read_page_regions(image_path: Path) -> tuple[str | None, str | None, str]` → `(nome_normalizado, cpf_digitos, texto_do_corpo)`. Levanta `ProviderError` se o OCR falhar em todas as tentativas.
  - Consumidos pela Task 6.

**Por que recortar por coordenada em vez de usar o OCR inteiro:** `EssayOcrToken` (`providers/models.py:59-64`) tem só `text/confidence/start/end` — **nenhuma região geométrica**. Não existe como separar cabeçalho de corpo por posição usando os tokens que o OCR devolve hoje. Como é o próprio sistema que gera a folha (layout fixo, conhecido), a saída é recortar a imagem por fração de altura conhecida ANTES de chamar o OCR, e chamá-lo duas vezes — uma por região.

- [ ] **Step 1: Write the failing test**

Criar `tests/test_r4_essay_batch_ocr_regions.py`:

```python
"""R4 lote - recorte da regiao de cabecalho e OCR separado de cabecalho/corpo.

O transcritor e falso (nenhuma chamada de IA nos testes): ele devolve tokens
diferentes conforme o nome do arquivo de imagem que recebe, que e exatamente o
que prova que o servico chamou o OCR DUAS vezes, uma por regiao, em vez de uma
so na pagina inteira.
"""

import shutil
import tempfile
import unittest
from pathlib import Path

from agente_ia_edu.providers.errors import ProviderError
from agente_ia_edu.providers.models import EssayOcrToken, EssayPageTranscriptionResult
from agente_ia_edu.services.essay_answer_sheet import HEADER_REGION_FRACTION
from agente_ia_edu.services.essay_batch import EssayBatchService


def _tokens(text: str, confidence: float = 0.95):
    return (EssayOcrToken(text=text, confidence=confidence, start=0, end=len(text)),)


class FakeTranscriber:
    """Devolve o texto de cabecalho pra imagem cujo nome termina em _header e o
    texto de corpo pra que termina em _body."""

    def __init__(self, header_text: str, body_text: str, *, fail_on: str | None = None):
        self.header_text = header_text
        self.body_text = body_text
        self.fail_on = fail_on
        self.calls: list[str] = []

    async def transcribe_page(self, request):
        name = request.image_path.name
        self.calls.append(name)
        if self.fail_on and self.fail_on in name:
            raise ProviderError("transcricao indisponivel")
        text = self.header_text if "_header" in name else self.body_text
        return EssayPageTranscriptionResult(
            tokens=_tokens(text), provider="fake", model="fake-1"
        )


def _write_page_image(dest: Path, *, header_text: str, body_text: str) -> Path:
    """Uma imagem de pagina A4 sintetica: o texto de cabecalho no topo, o de
    corpo bem abaixo da fronteira de HEADER_REGION_FRACTION."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595.44, height=842.40)
    page.insert_text((50, 60), header_text, fontsize=11)
    page.insert_text((50, 500), body_text, fontsize=11)
    page.get_pixmap(dpi=150).save(str(dest))
    doc.close()
    return dest


class CropRegionsTests(unittest.TestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="r4_crop_"))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    def test_splits_the_page_at_the_header_fraction_keeping_full_resolution(self):
        import pymupdf

        source = _write_page_image(
            self.tmp_dir / "page_1.png", header_text="NOME", body_text="corpo"
        )
        full = pymupdf.Pixmap(str(source))

        header_path, body_path = EssayBatchService._crop_regions(source, self.tmp_dir)

        header = pymupdf.Pixmap(str(header_path))
        body = pymupdf.Pixmap(str(body_path))
        # Mesma largura que o original: o recorte nao pode reduzir a resolucao,
        # senao o OCR le uma imagem pior do que a que recebemos.
        self.assertEqual(header.width, full.width)
        self.assertEqual(body.width, full.width)
        self.assertAlmostEqual(
            header.height, full.height * HEADER_REGION_FRACTION, delta=2
        )
        self.assertAlmostEqual(
            header.height + body.height, full.height, delta=2
        )

    def test_raises_value_error_for_an_unreadable_image(self):
        broken = self.tmp_dir / "broken.png"
        broken.write_bytes(b"nao sou uma imagem")
        with self.assertRaises(ValueError):
            EssayBatchService._crop_regions(broken, self.tmp_dir)


class ReadPageRegionsTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="r4_read_"))

    def tearDown(self):
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def test_reads_name_cpf_and_body_with_two_separate_ocr_calls(self):
        source = _write_page_image(
            self.tmp_dir / "page_1.png", header_text="cabecalho", body_text="corpo"
        )
        transcriber = FakeTranscriber(
            header_text="NOME COMPLETO DO PARTICIPANTE Joao da Silva CPF 123.456.789-00",
            body_text="A mobilidade urbana no Brasil enfrenta desafios.",
        )
        service = EssayBatchService(None, transcriber=transcriber)

        name, cpf, body = await service.read_page_regions(source)

        self.assertEqual(name, "JOAO DA SILVA")
        self.assertEqual(cpf, "12345678900")
        self.assertEqual(body, "A mobilidade urbana no Brasil enfrenta desafios.")
        self.assertEqual(len(transcriber.calls), 2)
        self.assertTrue(any("_header" in call for call in transcriber.calls))
        self.assertTrue(any("_body" in call for call in transcriber.calls))

    async def test_unreadable_header_still_returns_the_body(self):
        source = _write_page_image(
            self.tmp_dir / "page_2.png", header_text="cabecalho", body_text="corpo"
        )
        transcriber = FakeTranscriber(
            header_text="", body_text="Texto do corpo assim mesmo.", fail_on="_header"
        )
        service = EssayBatchService(None, transcriber=transcriber)

        name, cpf, body = await service.read_page_regions(source)

        self.assertIsNone(name)
        self.assertIsNone(cpf)
        self.assertEqual(body, "Texto do corpo assim mesmo.")

    async def test_body_ocr_failure_propagates(self):
        source = _write_page_image(
            self.tmp_dir / "page_3.png", header_text="cabecalho", body_text="corpo"
        )
        transcriber = FakeTranscriber(header_text="NOME: Ana", body_text="", fail_on="_body")
        service = EssayBatchService(None, transcriber=transcriber)

        with self.assertRaises(ProviderError):
            await service.read_page_regions(source)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_ocr_regions.py -q`
Expected: FAIL com `ImportError: cannot import name 'EssayBatchService'`.

- [ ] **Step 3: Add the service class**

Em `src/agente_ia_edu/services/essay_batch.py`, trocar o bloco de imports do topo por:

```python
from __future__ import annotations

import asyncio
import logging
import re
import shutil
import tempfile
import unicodedata
import uuid
from collections.abc import Callable, Sequence
from pathlib import Path
from typing import Any

from sqlalchemy.ext.asyncio import AsyncSession

from ..providers.contracts import EssayTranscriptionProvider
from ..providers.errors import ProviderError
from ..db.models import EssaySubmissionPage
from .essay_answer_sheet import HEADER_REGION_FRACTION
from .essay_submission import EssaySubmissionService
from .material_storage import MaterialStorage

logger = logging.getLogger(__name__)
```

E acrescentar, DEPOIS das funções puras (antes do `__all__`):

```python
class EssayBatchService:
    """Processa um lote de folhas de redacao fisicas.

    ``correction_factory`` existe pro teste: em producao e
    ``EssayCorrectionService``, e o servico chama
    ``correction_factory(session).correct(submission_id)`` quando uma submissao
    do lote fica pronta. Injetavel pra que nenhum teste deste modulo precise de
    provedor de IA configurado.
    """

    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: MaterialStorage | None = None,
        transcriber: EssayTranscriptionProvider | None = None,
        correction_factory: Callable[[AsyncSession], Any] | None = None,
    ) -> None:
        self.session = session
        self._storage = storage or MaterialStorage()
        self._transcriber = transcriber
        self._correction_factory = correction_factory

    def _submission_service(self) -> EssaySubmissionService:
        """Uma instancia de EssaySubmissionService usada SO pelo seu _ocr_page:
        as 3 tentativas, o piso de confianca media de 0.6 e a releitura de
        reconciliacao de tokens duvidosos ja estao resolvidos la (confirmados em
        producao ao longo de 2026-09), e reimplementar isso aqui seria copiar a
        parte mais delicada do sistema."""
        return EssaySubmissionService(self.session, transcriber=self._transcriber)

    @staticmethod
    def _crop_regions(image_path: Path, dest_dir: Path) -> tuple[Path, Path]:
        """Separa uma imagem de pagina em (cabecalho, corpo) pela fracao fixa de
        altura que a folha gerada por services/essay_answer_sheet.py usa.

        pymupdf abre um PNG/JPG como documento de uma pagina so; as coordenadas
        da pagina saem em PONTOS (72dpi), nao em pixels, entao a matriz de
        escala abaixo e o que preserva a resolucao original do arquivo - sem ela
        um PNG de 827px de largura sairia recortado com 596px, e o OCR passaria
        a ler uma imagem PIOR do que a que recebemos.
        """
        try:
            import pymupdf as _mu
        except ImportError:
            import fitz as _mu  # type: ignore

        try:
            full = _mu.Pixmap(str(image_path))
        except Exception as exc:
            raise ValueError(f"{image_path.name} is not a readable image file") from exc

        doc = _mu.open(str(image_path))
        try:
            page = doc[0]
            rect = page.rect
            scale = full.width / rect.width
            matrix = _mu.Matrix(scale, scale)
            split_y = rect.y0 + rect.height * HEADER_REGION_FRACTION
            header_path = dest_dir / f"{image_path.stem}_header.png"
            body_path = dest_dir / f"{image_path.stem}_body.png"
            page.get_pixmap(
                matrix=matrix, clip=_mu.Rect(rect.x0, rect.y0, rect.x1, split_y)
            ).save(str(header_path))
            page.get_pixmap(
                matrix=matrix, clip=_mu.Rect(rect.x0, split_y, rect.x1, rect.y1)
            ).save(str(body_path))
            return header_path, body_path
        finally:
            doc.close()

    async def _ocr_region(self, image_path: Path) -> str:
        """Texto de UMA regiao ja recortada. Usa uma EssaySubmissionPage
        TRANSITORIA, nunca adicionada a sessao: _ocr_page so escreve em
        ``page.ocr_tokens`` e nunca chama add()/flush(), entao o objeto serve
        aqui apenas como recipiente dos tokens e nada e persistido."""
        sink = EssaySubmissionPage(
            id=uuid.uuid4(), essay_submission_id=uuid.uuid4(),
            page_number=1, storage_uri=str(image_path),
        )
        await self._submission_service()._ocr_page(sink, image_path)
        return text_from_ocr_tokens(sink.ocr_tokens)

    async def read_page_regions(
        self, image_path: Path
    ) -> tuple[str | None, str | None, str]:
        """(nome normalizado, CPF so-digitos, texto do corpo) de uma pagina.

        Uma falha de OCR no CABECALHO nao e fatal: a pagina vai pra fila de
        resolucao manual e o professor identifica o aluno olhando a imagem, mas
        o texto que o aluno escreveu continua aproveitavel. Uma falha no CORPO
        propaga (ProviderError), porque sem texto nao ha redacao nenhuma pra
        corrigir - quem chama trata isso marcando a pagina NEEDS_REVIEW.

        Deliberadamente NAO usa a camada de texto embutida de um PDF digital
        (que _split_pdf_pages sabe extrair): a separacao cabecalho/corpo aqui e
        POSICIONAL, e a camada de texto nao carrega posicao util pra isso. Um
        lote e sempre papel escaneado de qualquer forma.
        """
        scratch_dir = Path(tempfile.mkdtemp(prefix="r4_batch_regions_"))
        try:
            header_path, body_path = await asyncio.to_thread(
                self._crop_regions, image_path, scratch_dir
            )
            try:
                header_text = await self._ocr_region(header_path)
            except ProviderError as exc:
                logger.warning(
                    "OCR do cabecalho falhou para %s, pagina ira para revisao manual: %s",
                    image_path, exc,
                )
                header_text = ""
            body_text = await self._ocr_region(body_path)
            name, cpf = parse_header_text(header_text)
            return name, cpf, body_text.strip()
        finally:
            shutil.rmtree(scratch_dir, ignore_errors=True)
```

E acrescentar `"EssayBatchService",` ao `__all__` (primeiro item, ordem alfabética).

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_ocr_regions.py tests/test_r4_essay_batch_matching.py -q`
Expected: PASS (26 testes).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/essay_batch.py tests/test_r4_essay_batch_ocr_regions.py
git commit -m "feat(redacao): OCR separado de cabecalho e corpo por recorte de regiao

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 5: Criação do lote (intake dos arquivos, limites, páginas)

**Files:**
- Modify: `src/agente_ia_edu/services/essay_batch.py` (acrescenta `MAX_BATCH_PAGES`, `ALLOWED_BATCH_SUFFIXES` e `EssayBatchService.create_batch`)
- Test: `tests/test_r4_essay_batch_create.py`

**Interfaces:**
- Consumes: `EssayBatchUpload`/`EssayBatchPage` (Task 1), `EssayBatchService` (Task 4), `MaterialStorage.store` e `EssaySubmissionService._split_pdf_pages` (já existentes).
- Produces:
  - `MAX_BATCH_PAGES: int = 60`
  - `ALLOWED_BATCH_SUFFIXES: tuple[str, ...] = (".png", ".jpg", ".jpeg", ".pdf")`
  - `async EssayBatchService.create_batch(self, *, school_id: uuid.UUID, essay_prompt_id: uuid.UUID, class_id: uuid.UUID, uploaded_by_external_identity: str, source_paths: list[Path]) -> dict` → `{"id": uuid.UUID, "school_id": uuid.UUID, "essay_prompt_id": uuid.UUID, "class_id": uuid.UUID, "status": str, "total_pages": int}`. Levanta `ValueError` (→ `422` na rota) pra: nenhum arquivo, extensão não suportada, PDF acima de 20 páginas, lote acima de 60 páginas, proposta não atribuída à turma.
  - Consumido pelas Tasks 6 e 10.

- [ ] **Step 1: Write the failing test**

Criar `tests/test_r4_essay_batch_create.py`:

```python
"""R4 lote - criacao do lote: intake dos arquivos, limites e ordem das paginas."""

import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear, Class, EssayBatchPage, EssayBatchUpload, EssayPrompt, GradeLevel,
    PromptAssignment, School, Segment,
)
from agente_ia_edu.services.essay_batch import MAX_BATCH_PAGES, EssayBatchService
from agente_ia_edu.services.material_storage import MaterialStorage


def _write_image(path: Path, text: str = "pagina") -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595.44, height=842.40)
    page.insert_text((50, 60), text, fontsize=11)
    page.get_pixmap(dpi=100).save(str(path))
    doc.close()
    return path


def _write_pdf(path: Path, pages: int) -> Path:
    import pymupdf

    doc = pymupdf.open()
    for index in range(pages):
        page = doc.new_page(width=595.44, height=842.40)
        page.insert_text((50, 60), f"folha {index + 1}", fontsize=11)
    doc.save(str(path))
    doc.close()
    return path


class CreateBatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="r4_create_"))
        self.storage = MaterialStorage(root=self.tmp_dir / "storage")

    async def asyncTearDown(self):
        await self.engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def _seed(self, session, *, assign: bool = True):
        school = School(id=uuid.uuid4(), code="CB-1", name="Escola")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-CB")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="3a", external_id="GRADE-CB",
        )
        year = AcademicYear(
            id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-CB"
        )
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="3A", external_id="TURMA-CB",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof",
        )
        session.add_all([klass, prompt])
        await session.flush()
        if assign:
            session.add(PromptAssignment(
                id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                class_id=klass.id, assigned_by_external_identity="prof",
            ))
        await session.commit()
        return school, klass, prompt

    def _service(self, session):
        return EssayBatchService(session, storage=self.storage)

    async def test_creates_one_page_per_image_in_upload_order(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            paths = [
                _write_image(self.tmp_dir / "a.png", "aluno a"),
                _write_image(self.tmp_dir / "b.jpg", "aluno b"),
            ]
            created = await self._service(session).create_batch(
                school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
                uploaded_by_external_identity="prof", source_paths=paths,
            )
            await session.commit()

            self.assertEqual(created["status"], "PROCESSING")
            self.assertEqual(created["total_pages"], 2)
            pages = (await session.execute(
                select(EssayBatchPage)
                .where(EssayBatchPage.batch_id == created["id"])
                .order_by(EssayBatchPage.page_number)
            )).scalars().all()
            self.assertEqual([p.page_number for p in pages], [1, 2])
            self.assertEqual([p.status for p in pages], ["NEEDS_REVIEW", "NEEDS_REVIEW"])
            for page in pages:
                self.assertTrue(Path(page.storage_uri).exists())

    async def test_pdf_pages_are_expanded_and_numbering_continues_across_files(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            paths = [
                _write_pdf(self.tmp_dir / "turma.pdf", 3),
                _write_image(self.tmp_dir / "extra.png", "aluno extra"),
            ]
            created = await self._service(session).create_batch(
                school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
                uploaded_by_external_identity="prof", source_paths=paths,
            )
            await session.commit()

            self.assertEqual(created["total_pages"], 4)
            pages = (await session.execute(
                select(EssayBatchPage)
                .where(EssayBatchPage.batch_id == created["id"])
                .order_by(EssayBatchPage.page_number)
            )).scalars().all()
            self.assertEqual([p.page_number for p in pages], [1, 2, 3, 4])

    async def test_rejects_a_pdf_above_the_per_file_page_limit(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            path = _write_pdf(self.tmp_dir / "grande.pdf", 21)
            with self.assertRaises(ValueError) as ctx:
                await self._service(session).create_batch(
                    school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
                    uploaded_by_external_identity="prof", source_paths=[path],
                )
            self.assertIn("20", str(ctx.exception))

    async def test_rejects_a_batch_above_the_total_page_limit(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            paths = [
                _write_pdf(self.tmp_dir / f"parte{index}.pdf", 20) for index in range(4)
            ]
            with self.assertRaises(ValueError) as ctx:
                await self._service(session).create_batch(
                    school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
                    uploaded_by_external_identity="prof", source_paths=paths,
                )
            self.assertIn(str(MAX_BATCH_PAGES), str(ctx.exception))

    async def test_rejects_an_unsupported_file_type(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            path = self.tmp_dir / "planilha.xlsx"
            path.write_bytes(b"x")
            with self.assertRaises(ValueError):
                await self._service(session).create_batch(
                    school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
                    uploaded_by_external_identity="prof", source_paths=[path],
                )

    async def test_rejects_an_empty_upload(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            with self.assertRaises(ValueError):
                await self._service(session).create_batch(
                    school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
                    uploaded_by_external_identity="prof", source_paths=[],
                )

    async def test_rejects_a_class_the_proposal_was_never_assigned_to(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session, assign=False)
            path = _write_image(self.tmp_dir / "c.png")
            with self.assertRaises(ValueError) as ctx:
                await self._service(session).create_batch(
                    school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
                    uploaded_by_external_identity="prof", source_paths=[path],
                )
            self.assertIn("atribu", str(ctx.exception).lower())

    async def test_nothing_is_persisted_when_the_limit_is_exceeded(self):
        async with self.session_factory() as session:
            school, klass, prompt = await self._seed(session)
            paths = [_write_pdf(self.tmp_dir / f"p{index}.pdf", 20) for index in range(4)]
            with self.assertRaises(ValueError):
                await self._service(session).create_batch(
                    school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
                    uploaded_by_external_identity="prof", source_paths=paths,
                )
            await session.rollback()
            remaining = (await session.execute(select(EssayBatchUpload))).scalars().all()
            self.assertEqual(remaining, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_create.py -q`
Expected: FAIL com `ImportError: cannot import name 'MAX_BATCH_PAGES'`.

- [ ] **Step 3: Implement create_batch**

Em `src/agente_ia_edu/services/essay_batch.py`, acrescentar aos imports do topo:

```python
from sqlalchemy import select

from ..db.models import EssayBatchPage, EssayBatchUpload, PromptAssignment
```

Acrescentar, logo abaixo das constantes de regex (antes das funções puras):

```python
# Teto do lote inteiro, somando todos os arquivos de um mesmo envio (spec s7).
# Folga sobre uma turma tipica de ~50 alunos, sem deixar o processamento em
# segundo plano crescer sem controle. O teto POR ARQUIVO PDF continua sendo o
# EssaySubmissionService._MAX_PDF_PAGES (20) que a submissao individual ja usa -
# reaproveitado, nao redeclarado, pra que os dois nunca divirjam.
MAX_BATCH_PAGES = 60

ALLOWED_BATCH_SUFFIXES = (".png", ".jpg", ".jpeg", ".pdf")
_PDF_SUFFIXES = (".pdf",)
```

E acrescentar à classe `EssayBatchService`:

```python
    @classmethod
    def _expand_to_page_images(cls, source_paths: Sequence[Path]) -> list[Path]:
        """Uma lista plana de imagens de pagina, na ordem dos arquivos enviados:
        um PDF vira N imagens (via o mesmo _split_pdf_pages que a submissao
        individual usa, com o mesmo teto de 20 paginas por arquivo), uma imagem
        continua sendo uma pagina so. Roda fora do event loop no chamador -
        rasterizar PDF e CPU-bound."""
        if not source_paths:
            raise ValueError("Envie ao menos um arquivo de redacao.")
        page_images: list[Path] = []
        for source_path in source_paths:
            suffix = source_path.suffix.lower()
            if suffix not in ALLOWED_BATCH_SUFFIXES:
                raise ValueError(f"Formato de arquivo nao suportado: {suffix!r}")
            if suffix in _PDF_SUFFIXES:
                # Levanta ValueError com a mensagem do proprio limite quando o
                # PDF passa de EssaySubmissionService._MAX_PDF_PAGES.
                split = EssaySubmissionService._split_pdf_pages(source_path)
                page_images.extend(image_path for image_path, _text in split)
            else:
                page_images.append(source_path)
        if len(page_images) > MAX_BATCH_PAGES:
            raise ValueError(
                f"Este envio tem {len(page_images)} paginas, acima do limite de "
                f"{MAX_BATCH_PAGES} por lote. Divida em mais de um envio."
            )
        return page_images

    async def _assignment_for_class_or_raise(
        self, *, school_id: uuid.UUID, essay_prompt_id: uuid.UUID, class_id: uuid.UUID
    ) -> PromptAssignment:
        assignment = await self.session.scalar(
            select(PromptAssignment).where(
                PromptAssignment.school_id == school_id,
                PromptAssignment.essay_prompt_id == essay_prompt_id,
                PromptAssignment.class_id == class_id,
            )
        )
        if assignment is None:
            raise ValueError(
                "Esta proposta nao esta atribuida a esta turma - atribua a proposta "
                "a turma antes de enviar as redacoes em lote."
            )
        return assignment

    async def create_batch(
        self,
        *,
        school_id: uuid.UUID,
        essay_prompt_id: uuid.UUID,
        class_id: uuid.UUID,
        uploaded_by_external_identity: str,
        source_paths: list[Path],
    ) -> dict:
        """Cria o lote em PROCESSING com TODAS as suas paginas ja gravadas em
        MaterialStorage, e devolve os campos como dict simples.

        Os limites sao checados ANTES de qualquer escrita, pra que um envio
        recusado nao deixe meio lote no banco. total_pages e fixado aqui (os
        PDFs ja vem separados em paginas), entao o progresso do processamento
        e sempre legivel como paginas_com_status_final/total_pages.

        Devolve dict e nao o objeto ORM pelo motivo de sempre neste projeto: a
        rota commita logo em seguida e expire_on_commit=True faria o proximo
        acesso a um atributo do objeto virar MissingGreenlet.
        """
        await self._assignment_for_class_or_raise(
            school_id=school_id, essay_prompt_id=essay_prompt_id, class_id=class_id
        )
        page_images = await asyncio.to_thread(self._expand_to_page_images, source_paths)

        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=school_id, essay_prompt_id=essay_prompt_id,
            class_id=class_id, uploaded_by_external_identity=uploaded_by_external_identity,
            status="PROCESSING", total_pages=len(page_images),
        )
        self.session.add(batch)
        await self.session.flush()

        for page_number, image_path in enumerate(page_images, start=1):
            managed_path, _digest = await asyncio.to_thread(self._storage.store, image_path)
            self.session.add(EssayBatchPage(
                id=uuid.uuid4(), batch_id=batch.id, page_number=page_number,
                storage_uri=str(managed_path), status="NEEDS_REVIEW",
            ))
        await self.session.flush()

        return {
            "id": batch.id, "school_id": batch.school_id,
            "essay_prompt_id": batch.essay_prompt_id, "class_id": batch.class_id,
            "status": batch.status, "total_pages": batch.total_pages,
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_create.py -q`
Expected: PASS (8 testes).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/essay_batch.py tests/test_r4_essay_batch_create.py
git commit -m "feat(redacao): criacao do lote com limites de 60 paginas e 20 por PDF

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 6: Processamento em segundo plano, página a página

**Files:**
- Modify: `src/agente_ia_edu/services/essay_batch.py` (acrescenta `class_roster` e `process_batch`)
- Test: `tests/test_r4_essay_batch_processing.py`

**Interfaces:**
- Consumes: `create_batch` (Task 5), `read_page_regions` (Task 4), `match_student` (Task 3).
- Produces:
  - `async EssayBatchService.class_roster(self, *, school_id: uuid.UUID, class_id: uuid.UUID) -> list[tuple[uuid.UUID, str, str | None]]` → `(student_id, full_name, document_number)` dos alunos com matrícula `ACTIVE` na turma.
  - `async EssayBatchService.process_batch(self, batch_id: uuid.UUID) -> None` — commita após cada página; ao fim marca o lote `DONE`.
  - Consumidos pelas Tasks 7, 9 e 10.

**Nota de escopo:** nesta tarefa `process_batch` só preenche `ocr_name_raw` / `ocr_cpf_raw` / `ocr_body_text` / `matched_student_id` de cada página. A criação da `EssaySubmission` (agrupamento) entra na Task 7, que acrescenta a chamada de materialização ao final deste mesmo método. Por isso, aqui, uma página que casou com um aluno continua `NEEDS_REVIEW`: ela só vira `MATCHED_AUTO` quando a corrida dela produzir uma submissão de verdade.

- [ ] **Step 1: Write the failing test**

Criar `tests/test_r4_essay_batch_processing.py`:

```python
"""R4 lote - processamento em segundo plano, pagina a pagina.

Nenhuma chamada de IA: o transcritor e falso e devolve o cabecalho/corpo
combinados por numero de pagina, a partir de um roteiro montado no teste.
"""

import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear, Class, EssayBatchPage, EssayBatchUpload, EssayPrompt, GradeLevel,
    Person, PromptAssignment, School, Segment, Student, StudentEnrollment,
)
from agente_ia_edu.providers.errors import ProviderError
from agente_ia_edu.providers.models import EssayOcrToken, EssayPageTranscriptionResult
from agente_ia_edu.services.essay_batch import EssayBatchService
from agente_ia_edu.services.material_storage import MaterialStorage


class ScriptedTranscriber:
    """``script`` mapeia o texto impresso na pagina -> (cabecalho, corpo).

    A imagem de cada pagina do lote carrega o proprio identificador impresso no
    topo E no corpo, entao o recorte de cabecalho e o de corpo continuam
    identificaveis depois de separados - e assim o transcritor falso sabe qual
    pagina esta lendo sem precisar de OCR de verdade.
    """

    def __init__(self, script: dict[str, tuple[str, str]], *, fail_pages: set[str] | None = None):
        self.script = script
        self.fail_pages = fail_pages or set()

    async def transcribe_page(self, request):
        import pymupdf

        doc = pymupdf.open(str(request.image_path))
        try:
            stamped = doc[0].get_text().strip().splitlines()
        finally:
            doc.close()
        key = next((line.strip() for line in stamped if line.strip()), "")
        if key in self.fail_pages:
            raise ProviderError("transcricao indisponivel")
        header, body = self.script.get(key, ("", ""))
        text = header if "_header" in request.image_path.name else body
        return EssayPageTranscriptionResult(
            tokens=(EssayOcrToken(text=text, confidence=0.95, start=0, end=len(text)),),
            provider="fake", model="fake-1",
        )


def _write_stamped_page(path: Path, key: str) -> Path:
    """Uma pagina com ``key`` impresso duas vezes: no topo (regiao de cabecalho)
    e no meio (regiao de corpo), pra que as duas metades continuem rastreaveis."""
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595.44, height=842.40)
    page.insert_text((50, 60), key, fontsize=11)
    page.insert_text((50, 500), key, fontsize=11)
    page.get_pixmap(dpi=100).save(str(path))
    doc.close()
    return path


class ProcessBatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="r4_proc_"))
        self.storage = MaterialStorage(root=self.tmp_dir / "storage")

    async def asyncTearDown(self):
        await self.engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def _seed(self, session, student_names):
        school = School(id=uuid.uuid4(), code="PB-1", name="Escola")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-PB")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="3a", external_id="GRADE-PB",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-PB")
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="3A", external_id="TURMA-PB",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof",
        )
        session.add_all([klass, prompt])
        await session.flush()
        session.add(PromptAssignment(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, assigned_by_external_identity="prof",
        ))
        students = {}
        for index, (full_name, document_number) in enumerate(student_names):
            person = Person(
                id=uuid.uuid4(), school_id=school.id, full_name=full_name,
                document_number=document_number, external_id=f"PER-{index}",
            )
            session.add(person)
            await session.flush()
            student = Student(
                id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                external_id=f"STU-{index}",
            )
            session.add(student)
            await session.flush()
            session.add(StudentEnrollment(
                id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                class_id=klass.id, status="ACTIVE", external_id=f"ENR-{index}",
            ))
            students[full_name] = student.id
        await session.commit()
        return school, klass, prompt, students

    async def _make_batch(self, session, school, klass, prompt, keys, *, transcriber):
        paths = [
            _write_stamped_page(self.tmp_dir / f"{key}.png", key) for key in keys
        ]
        service = EssayBatchService(session, storage=self.storage, transcriber=transcriber)
        created = await service.create_batch(
            school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
            uploaded_by_external_identity="prof", source_paths=paths,
        )
        await session.commit()
        return service, created

    async def _pages(self, session, batch_id):
        return (await session.execute(
            select(EssayBatchPage)
            .where(EssayBatchPage.batch_id == batch_id)
            .order_by(EssayBatchPage.page_number)
        )).scalars().all()

    async def test_class_roster_only_lists_active_enrollments(self):
        async with self.session_factory() as session:
            school, klass, _prompt, students = await self._seed(
                session, [("Ana Lúcia Ferreira", "11122233344"), ("João da Silva", None)]
            )
            enrollment = await session.scalar(
                select(StudentEnrollment).where(
                    StudentEnrollment.student_id == students["João da Silva"]
                )
            )
            enrollment.status = "EXITED"
            await session.commit()

            roster = await EssayBatchService(session, storage=self.storage).class_roster(
                school_id=school.id, class_id=klass.id
            )
            self.assertEqual(
                roster, [(students["Ana Lúcia Ferreira"], "Ana Lúcia Ferreira", "11122233344")]
            )

    async def test_matches_each_page_and_marks_the_batch_done(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(
                session, [("Ana Lúcia Ferreira", None), ("João da Silva", None)]
            )
            transcriber = ScriptedTranscriber({
                "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira CPF 111.222.333-44",
                       "Texto da Ana."),
                "p2": ("NOME COMPLETO DO PARTICIPANTE Joao da Silva", "Texto do Joao."),
            })
            service, created = await self._make_batch(
                session, school, klass, prompt, ["p1", "p2"], transcriber=transcriber
            )

            await service.process_batch(created["id"])

            batch = await session.get(EssayBatchUpload, created["id"])
            self.assertEqual(batch.status, "DONE")
            pages = await self._pages(session, created["id"])
            self.assertEqual(pages[0].matched_student_id, students["Ana Lúcia Ferreira"])
            self.assertEqual(pages[0].ocr_name_raw, "ANA LUCIA FERREIRA")
            self.assertEqual(pages[0].ocr_cpf_raw, "11122233344")
            self.assertEqual(pages[0].ocr_body_text, "Texto da Ana.")
            self.assertEqual(pages[1].matched_student_id, students["João da Silva"])
            self.assertIsNone(pages[1].ocr_cpf_raw)

    async def test_unknown_name_stays_needs_review_with_no_student(self):
        async with self.session_factory() as session:
            school, klass, prompt, _students = await self._seed(
                session, [("Ana Lúcia Ferreira", None)]
            )
            transcriber = ScriptedTranscriber({
                "p1": ("NOME COMPLETO DO PARTICIPANTE Carlos Mendes", "Texto do Carlos."),
            })
            service, created = await self._make_batch(
                session, school, klass, prompt, ["p1"], transcriber=transcriber
            )

            await service.process_batch(created["id"])

            page = (await self._pages(session, created["id"]))[0]
            self.assertIsNone(page.matched_student_id)
            self.assertEqual(page.status, "NEEDS_REVIEW")
            self.assertEqual(page.ocr_name_raw, "CARLOS MENDES")
            self.assertEqual(page.ocr_body_text, "Texto do Carlos.")

    async def test_homonyms_in_the_same_class_go_to_manual_review(self):
        async with self.session_factory() as session:
            school, klass, prompt, _students = await self._seed(
                session, [("João da Silva", "11111111111"), ("Joao Da Silva", "22222222222")]
            )
            transcriber = ScriptedTranscriber({
                "p1": ("NOME COMPLETO DO PARTICIPANTE Joao da Silva CPF 222.222.222-22",
                       "Texto ambiguo."),
            })
            service, created = await self._make_batch(
                session, school, klass, prompt, ["p1"], transcriber=transcriber
            )

            await service.process_batch(created["id"])

            page = (await self._pages(session, created["id"]))[0]
            self.assertIsNone(page.matched_student_id)
            self.assertEqual(page.status, "NEEDS_REVIEW")
            # O CPF foi lido e guardado: e a pista que desempata na tela do professor.
            self.assertEqual(page.ocr_cpf_raw, "22222222222")

    async def test_one_failing_page_never_takes_down_the_batch(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(
                session, [("Ana Lúcia Ferreira", None)]
            )
            transcriber = ScriptedTranscriber(
                {
                    "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Texto da Ana."),
                    "p2": ("", ""),
                },
                fail_pages={"p2"},
            )
            service, created = await self._make_batch(
                session, school, klass, prompt, ["p1", "p2"], transcriber=transcriber
            )

            await service.process_batch(created["id"])

            batch = await session.get(EssayBatchUpload, created["id"])
            self.assertEqual(batch.status, "DONE")
            pages = await self._pages(session, created["id"])
            self.assertEqual(pages[0].matched_student_id, students["Ana Lúcia Ferreira"])
            self.assertIsNone(pages[1].matched_student_id)
            self.assertIsNone(pages[1].ocr_name_raw)
            self.assertEqual(pages[1].status, "NEEDS_REVIEW")

    async def test_each_page_is_committed_as_soon_as_it_finishes(self):
        """Spec s5: o registro de cada pagina e commitado assim que aquela
        pagina termina, e nao so no fim do lote - e o que faz GET
        /essay-batches/{id} refletir o progresso real com o lote ainda rodando.

        Checado contando QUANTAS paginas ja tinham sido processadas no momento
        de cada commit (e nao lendo de uma segunda sessao): com SQLite
        in-memory + StaticPool todas as sessoes compartilham a MESMA conexao,
        entao uma segunda sessao enxergaria ate o que ainda nao foi commitado -
        o teste passaria sem provar nada.
        """
        async with self.session_factory() as session:
            school, klass, prompt, _students = await self._seed(
                session, [("Ana Lúcia Ferreira", None)]
            )
            transcriber = ScriptedTranscriber({
                "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Texto da Ana."),
                "p2": ("NOME COMPLETO DO PARTICIPANTE Joao da Silva", "Outro texto."),
            })
            service, created = await self._make_batch(
                session, school, klass, prompt, ["p1", "p2"], transcriber=transcriber
            )

            processed = {"count": 0}
            commits: list[int] = []
            original_process = service._process_page
            original_commit = service.session.commit

            async def _counting_process(page, roster):
                await original_process(page, roster)
                processed["count"] += 1

            async def _recording_commit():
                commits.append(processed["count"])
                await original_commit()

            service._process_page = _counting_process
            service.session.commit = _recording_commit
            await service.process_batch(created["id"])

            self.assertEqual(
                commits[:2], [1, 2],
                "houve um commit logo apos a primeira pagina e outro apos a segunda",
            )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_processing.py -q`
Expected: FAIL com `AttributeError: 'EssayBatchService' object has no attribute 'class_roster'`.

- [ ] **Step 3: Implement class_roster and process_batch**

Acrescentar aos imports de `src/agente_ia_edu/services/essay_batch.py`:

```python
from ..db.models import Person, Student, StudentEnrollment
```

E acrescentar à classe `EssayBatchService`:

```python
    async def class_roster(
        self, *, school_id: uuid.UUID, class_id: uuid.UUID
    ) -> list[tuple[uuid.UUID, str, str | None]]:
        """(student_id, full_name, document_number) de cada aluno com matricula
        ACTIVE nesta turma. Mesmo JOIN que essay_teacher_dashboard.py:198-203 ja
        usa pra montar a lista de alunos de uma turma, acrescido do
        document_number (o CPF que o professor ve como pista na tela de
        resolucao manual)."""
        rows = (await self.session.execute(
            select(StudentEnrollment.student_id, Person.full_name, Person.document_number)
            .join(Student, Student.id == StudentEnrollment.student_id)
            .join(Person, Person.id == Student.person_id)
            .where(
                StudentEnrollment.school_id == school_id,
                StudentEnrollment.class_id == class_id,
                StudentEnrollment.status == "ACTIVE",
            )
            .order_by(Person.full_name)
        )).all()
        return [(student_id, full_name, document_number) for student_id, full_name, document_number in rows]

    async def _process_page(
        self, page: EssayBatchPage, roster: Sequence[tuple[uuid.UUID, str, str | None]]
    ) -> None:
        """Le uma pagina e grava o que foi lido. Best-effort: qualquer falha
        (OCR esgotou as tentativas, imagem corrompida) deixa a pagina em
        NEEDS_REVIEW com os campos de leitura vazios, mesmo padrao por item que
        essay_proposal.create_assignments_bulk ja usa."""
        try:
            name, cpf, body_text = await self.read_page_regions(Path(page.storage_uri))
        except (ProviderError, ValueError) as exc:
            logger.warning(
                "pagina %s do lote %s nao pode ser lida, vai para revisao manual: %s",
                page.page_number, page.batch_id, exc,
            )
            page.status = "NEEDS_REVIEW"
            return
        page.ocr_name_raw = name
        page.ocr_cpf_raw = cpf
        page.ocr_body_text = body_text
        page.matched_student_id = match_student(
            name, [(student_id, full_name) for student_id, full_name, _document in roster]
        )

    async def process_batch(self, batch_id: uuid.UUID) -> None:
        """Processa TODAS as paginas do lote, em sequencia (nunca em paralelo -
        um lote de 60 paginas dispararia 120+ chamadas simultaneas ao provedor
        de OCR), commitando depois de cada uma, pra que uma leitura de status
        feita no meio do caminho sempre reflita o progresso real (spec s5).

        Ao fim marca o lote DONE. Nunca levanta por falha de uma pagina - ver
        _process_page.
        """
        batch = await self.session.get(EssayBatchUpload, batch_id)
        if batch is None:
            raise ValueError(f"EssayBatchUpload not found: {batch_id}")
        roster = await self.class_roster(school_id=batch.school_id, class_id=batch.class_id)
        pages = (await self.session.execute(
            select(EssayBatchPage)
            .where(EssayBatchPage.batch_id == batch_id)
            .order_by(EssayBatchPage.page_number)
        )).scalars().all()

        for page in pages:
            await self._process_page(page, roster)
            await self.session.commit()

        batch = await self.session.get(EssayBatchUpload, batch_id)
        batch.status = "DONE"
        await self.session.commit()
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_processing.py -q`
Expected: PASS (6 testes).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/essay_batch.py tests/test_r4_essay_batch_processing.py
git commit -m "feat(redacao): processamento do lote pagina a pagina com commit incremental

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 7: Agrupamento em submissão + disparo da correção

**Files:**
- Modify: `src/agente_ia_edu/services/essay_batch.py` (acrescenta `consecutive_runs`, `_assignment_for_student`, `_materialize_run`, `materialize_batch`, `run_corrections`; e chama as duas últimas ao final de `process_batch`)
- Test: `tests/test_r4_essay_batch_grouping.py`

**Interfaces:**
- Consumes: `process_batch` (Task 6), `EssaySubmission` + `normalize_essay_text`/`essay_text_hash` (`services/essay_correction_key.py`, já existentes).
- Produces:
  - `consecutive_runs(pages: Sequence[EssayBatchPage]) -> list[list[EssayBatchPage]]` (função de módulo, pura).
  - `async EssayBatchService.materialize_batch(self, batch_id: uuid.UUID) -> list[uuid.UUID]` → ids das submissões que precisam de correção.
  - `async EssayBatchService.run_corrections(self, submission_ids: Sequence[uuid.UUID]) -> None`.
  - Consumidos pelas Tasks 8 e 10.

**Reconciliação entre §4.4 e §7 da spec:** §4.4 diz "páginas com o mesmo `matched_student_id` ... ordenadas por `page_number` e concatenadas", e §7 diz que um aluno com duas redações no mesmo lote "gera uma segunda `EssaySubmission`". As duas só são verdadeiras ao mesmo tempo se o agrupamento for por **corrida consecutiva**: páginas com números consecutivos e o mesmo aluno viram uma submissão; uma segunda corrida do mesmo aluno, separada por páginas de outra pessoa, vira outra submissão. É o que "agrupa páginas consecutivas do mesmo aluno" quer dizer, e é o que este plano implementa.

- [ ] **Step 1: Write the failing test**

Criar `tests/test_r4_essay_batch_grouping.py`. Reaproveita os helpers de `tests/test_r4_essay_batch_processing.py` copiando-os (não importando de outro arquivo de teste — convenção do projeto: cada arquivo de teste é autossuficiente):

```python
"""R4 lote - agrupamento de paginas consecutivas em uma EssaySubmission.

Regra (spec s4.4 reconciliada com s7): uma CORRIDA de paginas consecutivas com
o mesmo matched_student_id vira UMA submissao; uma segunda corrida do mesmo
aluno, separada por paginas de outra pessoa, vira OUTRA submissao.
"""

import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear, Class, EssayBatchPage, EssayPrompt, EssaySubmission,
    EssaySubmissionPage, GradeLevel, Person, PromptAssignment, School, Segment,
    Student, StudentEnrollment,
)
from agente_ia_edu.providers.models import EssayOcrToken, EssayPageTranscriptionResult
from agente_ia_edu.services.essay_batch import EssayBatchService, consecutive_runs
from agente_ia_edu.services.material_storage import MaterialStorage


class ScriptedTranscriber:
    def __init__(self, script: dict[str, tuple[str, str]]):
        self.script = script

    async def transcribe_page(self, request):
        import pymupdf

        doc = pymupdf.open(str(request.image_path))
        try:
            stamped = doc[0].get_text().strip().splitlines()
        finally:
            doc.close()
        key = next((line.strip() for line in stamped if line.strip()), "")
        header, body = self.script.get(key, ("", ""))
        text = header if "_header" in request.image_path.name else body
        return EssayPageTranscriptionResult(
            tokens=(EssayOcrToken(text=text, confidence=0.95, start=0, end=len(text)),),
            provider="fake", model="fake-1",
        )


class RecordingCorrectionService:
    corrected: list[uuid.UUID] = []

    def __init__(self, session):
        self.session = session

    async def correct(self, essay_submission_id):
        RecordingCorrectionService.corrected.append(essay_submission_id)


def _write_stamped_page(path: Path, key: str) -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595.44, height=842.40)
    page.insert_text((50, 60), key, fontsize=11)
    page.insert_text((50, 500), key, fontsize=11)
    page.get_pixmap(dpi=100).save(str(path))
    doc.close()
    return path


class ConsecutiveRunsTests(unittest.TestCase):
    def _page(self, number, student_id):
        return EssayBatchPage(
            id=uuid.uuid4(), batch_id=uuid.uuid4(), page_number=number,
            storage_uri=f"/p{number}.png", matched_student_id=student_id,
        )

    def test_groups_consecutive_pages_of_the_same_student(self):
        ana, joao = uuid.uuid4(), uuid.uuid4()
        pages = [self._page(1, ana), self._page(2, ana), self._page(3, joao)]
        runs = consecutive_runs(pages)
        self.assertEqual([[p.page_number for p in run] for run in runs], [[1, 2], [3]])

    def test_a_second_run_of_the_same_student_is_a_separate_group(self):
        ana, joao = uuid.uuid4(), uuid.uuid4()
        pages = [self._page(1, ana), self._page(2, joao), self._page(3, ana)]
        runs = consecutive_runs(pages)
        self.assertEqual([[p.page_number for p in run] for run in runs], [[1], [2], [3]])
        self.assertEqual(runs[0][0].matched_student_id, ana)
        self.assertEqual(runs[2][0].matched_student_id, ana)

    def test_unmatched_pages_break_a_run_and_are_never_grouped(self):
        ana = uuid.uuid4()
        pages = [self._page(1, ana), self._page(2, None), self._page(3, ana)]
        runs = consecutive_runs(pages)
        self.assertEqual([[p.page_number for p in run] for run in runs], [[1], [3]])

    def test_a_gap_in_page_numbers_breaks_a_run(self):
        ana = uuid.uuid4()
        pages = [self._page(1, ana), self._page(3, ana)]
        runs = consecutive_runs(pages)
        self.assertEqual([[p.page_number for p in run] for run in runs], [[1], [3]])

    def test_no_pages_means_no_runs(self):
        self.assertEqual(consecutive_runs([]), [])


class MaterializeBatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        RecordingCorrectionService.corrected = []
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="r4_group_"))
        self.storage = MaterialStorage(root=self.tmp_dir / "storage")

    async def asyncTearDown(self):
        await self.engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def _seed(self, session, student_names):
        school = School(id=uuid.uuid4(), code="GR-1", name="Escola")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-GR")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="3a", external_id="GRADE-GR",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-GR")
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="3A", external_id="TURMA-GR",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof",
        )
        session.add_all([klass, prompt])
        await session.flush()
        assignment = PromptAssignment(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, assigned_by_external_identity="prof",
        )
        session.add(assignment)
        students = {}
        for index, full_name in enumerate(student_names):
            person = Person(
                id=uuid.uuid4(), school_id=school.id, full_name=full_name,
                external_id=f"PER-G{index}",
            )
            session.add(person)
            await session.flush()
            student = Student(
                id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                external_id=f"STU-G{index}",
            )
            session.add(student)
            await session.flush()
            session.add(StudentEnrollment(
                id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                class_id=klass.id, status="ACTIVE", external_id=f"ENR-G{index}",
            ))
            students[full_name] = student.id
        await session.commit()
        return school, klass, prompt, assignment, students

    async def _run(self, session, school, klass, prompt, keys, script):
        paths = [_write_stamped_page(self.tmp_dir / f"{key}.png", key) for key in keys]
        service = EssayBatchService(
            session, storage=self.storage, transcriber=ScriptedTranscriber(script),
            correction_factory=RecordingCorrectionService,
        )
        created = await service.create_batch(
            school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
            uploaded_by_external_identity="prof", source_paths=paths,
        )
        await session.commit()
        await service.process_batch(created["id"])
        return service, created

    async def _pages(self, session, batch_id):
        return (await session.execute(
            select(EssayBatchPage)
            .where(EssayBatchPage.batch_id == batch_id)
            .order_by(EssayBatchPage.page_number)
        )).scalars().all()

    async def test_two_consecutive_pages_become_one_submission(self):
        async with self.session_factory() as session:
            school, klass, prompt, assignment, students = await self._seed(
                session, ["Ana Lúcia Ferreira"]
            )
            header = "NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira"
            await self._run(session, school, klass, prompt, ["p1", "p2"], {
                "p1": (header, "Primeira parte."),
                "p2": (header, "Segunda parte."),
            })

            submissions = (await session.execute(select(EssaySubmission))).scalars().all()
            self.assertEqual(len(submissions), 1)
            submission = submissions[0]
            self.assertEqual(submission.student_id, students["Ana Lúcia Ferreira"])
            self.assertEqual(submission.prompt_assignment_id, assignment.id)
            self.assertEqual(submission.status, "SUBMITTED")
            self.assertEqual(submission.anchor_mode, "TEXT_OFFSET")
            self.assertEqual(submission.mode, "PHOTO")
            self.assertIsNotNone(submission.submitted_at)
            self.assertIn("Primeira parte.", submission.canonical_text)
            self.assertIn("Segunda parte.", submission.canonical_text)
            self.assertLess(
                submission.canonical_text.index("Primeira parte."),
                submission.canonical_text.index("Segunda parte."),
            )
            self.assertIsNotNone(submission.normalized_text_hash)

    async def test_the_batch_never_creates_essay_submission_pages(self):
        async with self.session_factory() as session:
            school, klass, prompt, _assignment, _students = await self._seed(
                session, ["Ana Lúcia Ferreira"]
            )
            await self._run(session, school, klass, prompt, ["p1"], {
                "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Texto."),
            })
            pages = (await session.execute(select(EssaySubmissionPage))).scalars().all()
            self.assertEqual(pages, [])

    async def test_pages_are_marked_matched_auto_and_point_at_the_submission(self):
        async with self.session_factory() as session:
            school, klass, prompt, _assignment, _students = await self._seed(
                session, ["Ana Lúcia Ferreira"]
            )
            _service, created = await self._run(session, school, klass, prompt, ["p1", "p2"], {
                "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Um."),
                "p2": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Dois."),
            })
            submission = (await session.execute(select(EssaySubmission))).scalars().one()
            pages = await self._pages(session, created["id"])
            self.assertEqual([p.status for p in pages], ["MATCHED_AUTO", "MATCHED_AUTO"])
            self.assertEqual({p.essay_submission_id for p in pages}, {submission.id})

    async def test_a_second_run_of_the_same_student_creates_a_second_submission(self):
        async with self.session_factory() as session:
            school, klass, prompt, _assignment, students = await self._seed(
                session, ["Ana Lúcia Ferreira", "João da Silva"]
            )
            ana = "NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira"
            joao = "NOME COMPLETO DO PARTICIPANTE Joao da Silva"
            await self._run(session, school, klass, prompt, ["p1", "p2", "p3"], {
                "p1": (ana, "Primeira redacao da Ana."),
                "p2": (joao, "Redacao do Joao."),
                "p3": (ana, "Segunda redacao da Ana."),
            })
            ana_submissions = (await session.execute(
                select(EssaySubmission).where(
                    EssaySubmission.student_id == students["Ana Lúcia Ferreira"]
                )
            )).scalars().all()
            self.assertEqual(len(ana_submissions), 2)
            self.assertEqual(
                len({s.essay_id for s in ana_submissions}), 2,
                "cada corrida e uma redacao propria, nunca versoes do mesmo essay_id",
            )

    async def test_a_run_with_no_recognized_text_never_becomes_a_submission(self):
        async with self.session_factory() as session:
            school, klass, prompt, _assignment, _students = await self._seed(
                session, ["Ana Lúcia Ferreira"]
            )
            _service, created = await self._run(session, school, klass, prompt, ["p1"], {
                "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "   "),
            })
            self.assertEqual((await session.execute(select(EssaySubmission))).scalars().all(), [])
            page = (await self._pages(session, created["id"]))[0]
            self.assertEqual(page.status, "NEEDS_REVIEW")
            self.assertIsNone(page.essay_submission_id)
            # O nome lido continua guardado - e a pista que o professor ve.
            self.assertEqual(page.ocr_name_raw, "ANA LUCIA FERREIRA")

    async def test_each_created_submission_is_sent_to_correction_once(self):
        async with self.session_factory() as session:
            school, klass, prompt, _assignment, _students = await self._seed(
                session, ["Ana Lúcia Ferreira", "João da Silva"]
            )
            await self._run(session, school, klass, prompt, ["p1", "p2"], {
                "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Texto da Ana."),
                "p2": ("NOME COMPLETO DO PARTICIPANTE Joao da Silva", "Texto do Joao."),
            })
            submissions = (await session.execute(select(EssaySubmission))).scalars().all()
            self.assertEqual(len(submissions), 2)
            self.assertEqual(
                sorted(str(i) for i in RecordingCorrectionService.corrected),
                sorted(str(s.id) for s in submissions),
            )

    async def test_a_failing_correction_never_rolls_back_the_submission(self):
        class ExplodingCorrectionService:
            def __init__(self, session):
                self.session = session

            async def correct(self, essay_submission_id):
                raise RuntimeError("provedor fora do ar")

        async with self.session_factory() as session:
            school, klass, prompt, _assignment, _students = await self._seed(
                session, ["Ana Lúcia Ferreira"]
            )
            paths = [_write_stamped_page(self.tmp_dir / "p1.png", "p1")]
            service = EssayBatchService(
                session, storage=self.storage,
                transcriber=ScriptedTranscriber({
                    "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Texto."),
                }),
                correction_factory=ExplodingCorrectionService,
            )
            created = await service.create_batch(
                school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
                uploaded_by_external_identity="prof", source_paths=paths,
            )
            await session.commit()
            await service.process_batch(created["id"])

            submission = (await session.execute(select(EssaySubmission))).scalars().one()
            self.assertEqual(submission.status, "SUBMITTED")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_grouping.py -q`
Expected: FAIL com `ImportError: cannot import name 'consecutive_runs'`.

- [ ] **Step 3: Implement the grouping**

Acrescentar aos imports de `src/agente_ia_edu/services/essay_batch.py`:

```python
from datetime import datetime, timezone

from ..db.models import EssaySubmission
from .essay_correction import EssayCorrectionService
from .essay_correction_key import essay_text_hash, normalize_essay_text
```

Acrescentar uma função de módulo, junto das outras funções puras:

```python
def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


def consecutive_runs(pages: Sequence[EssayBatchPage]) -> list[list[EssayBatchPage]]:
    """Corridas MAXIMAIS de paginas com numero consecutivo e o MESMO
    matched_student_id (spec s4.4 reconciliada com s7).

    Uma pagina sem aluno casado quebra a corrida e nunca entra em nenhuma:
    resolver essa pagina e trabalho do professor. Uma segunda corrida do mesmo
    aluno, separada por paginas de outra pessoa, e um grupo PROPRIO - e o que
    torna possivel um aluno entregar duas redacoes no mesmo lote.

    ``pages`` precisa vir ordenada por page_number.
    """
    runs: list[list[EssayBatchPage]] = []
    current: list[EssayBatchPage] = []
    for page in pages:
        if page.matched_student_id is None:
            current = []
            continue
        if (
            current
            and current[-1].matched_student_id == page.matched_student_id
            and page.page_number == current[-1].page_number + 1
        ):
            current.append(page)
            continue
        current = [page]
        runs.append(current)
    return runs
```

E acrescentar à classe `EssayBatchService`:

```python
    async def _assignment_for_student(
        self, *, school_id: uuid.UUID, essay_prompt_id: uuid.UUID, student_id: uuid.UUID
    ) -> PromptAssignment:
        """A atribuicao desta proposta a turma ATIVA do aluno.

        Nao e simplesmente a turma do lote: a spec s7 decide que a turma da
        submissao final vem do ALUNO, nao do class_id escolhido no upload -
        entao um aluno resolvido manualmente que esteja em outra turma recebe a
        atribuicao da turma DELE. Pro caminho automatico isso cai naturalmente
        na atribuicao do proprio lote, ja que o match so olha alunos daquela
        turma.
        """
        assignment = await self.session.scalar(
            select(PromptAssignment)
            .join(
                StudentEnrollment,
                StudentEnrollment.class_id == PromptAssignment.class_id,
            )
            .where(
                PromptAssignment.school_id == school_id,
                PromptAssignment.essay_prompt_id == essay_prompt_id,
                StudentEnrollment.student_id == student_id,
                StudentEnrollment.status == "ACTIVE",
            )
            .order_by(PromptAssignment.created_at)
        )
        if assignment is None:
            raise ValueError(
                "Esta proposta nao esta atribuida a nenhuma turma ativa deste aluno."
            )
        return assignment

    async def _materialize_run(
        self, batch: EssayBatchUpload, run: list[EssayBatchPage]
    ) -> uuid.UUID | None:
        """Transforma UMA corrida em (ou atualiza) uma EssaySubmission e devolve
        o id dela quando ela precisa ser corrigida; None quando a corrida nao
        produz redacao nenhuma.

        A submissao nasce SUBMITTED com canonical_text ja preenchido e
        anchor_mode TEXT_OFFSET - o mesmo formato atomico de
        EssaySubmissionService.start_typed_submission, e NAO o de
        start_photo_submission (que depende de EssaySubmissionPage e de um
        reviewed_text humano que este fluxo deliberadamente nao tem).
        """
        text = "\n\n".join(
            (page.ocr_body_text or "").strip()
            for page in run
            if (page.ocr_body_text or "").strip()
        )
        if not text:
            # Spec s4.5: nome batido mas nenhum texto reconhecido nunca vira uma
            # redacao vazia - vai pro professor resolver olhando a imagem.
            for page in run:
                page.status = "NEEDS_REVIEW"
                page.essay_submission_id = None
            return None

        student_id = run[0].matched_student_id
        try:
            assignment = await self._assignment_for_student(
                school_id=batch.school_id,
                essay_prompt_id=batch.essay_prompt_id,
                student_id=student_id,
            )
        except ValueError as exc:
            logger.warning(
                "corrida do aluno %s no lote %s sem atribuicao valida: %s",
                student_id, batch.id, exc,
            )
            for page in run:
                page.status = "NEEDS_REVIEW"
                page.essay_submission_id = None
            return None

        existing_id = next(
            (page.essay_submission_id for page in run if page.essay_submission_id is not None),
            None,
        )
        if existing_id is not None:
            submission = await self.session.get(EssaySubmission, existing_id)
            submission.canonical_text = normalize_essay_text(text)
            submission.normalized_text_hash = essay_text_hash(text)
        else:
            submission = EssaySubmission(
                id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=batch.school_id,
                prompt_assignment_id=assignment.id, student_id=student_id,
                mode="PHOTO", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                canonical_text=normalize_essay_text(text),
                normalized_text_hash=essay_text_hash(text),
                submitted_at=_utcnow(),
            )
            self.session.add(submission)
            await self.session.flush()

        for page in run:
            if page.status != "RESOLVED_MANUAL":
                page.status = "MATCHED_AUTO"
            page.essay_submission_id = submission.id
        await self.session.flush()
        return submission.id

    async def materialize_batch(self, batch_id: uuid.UUID) -> list[uuid.UUID]:
        """Percorre TODAS as corridas do lote e devolve os ids das submissoes
        que precisam de correcao. Commita uma vez ao final - as paginas ja
        estavam commitadas individualmente pelo process_batch."""
        batch = await self.session.get(EssayBatchUpload, batch_id)
        if batch is None:
            raise ValueError(f"EssayBatchUpload not found: {batch_id}")
        pages = (await self.session.execute(
            select(EssayBatchPage)
            .where(EssayBatchPage.batch_id == batch_id)
            .order_by(EssayBatchPage.page_number)
        )).scalars().all()

        submission_ids: list[uuid.UUID] = []
        for run in consecutive_runs(pages):
            submission_id = await self._materialize_run(batch, run)
            if submission_id is not None:
                submission_ids.append(submission_id)
        await self.session.commit()
        return submission_ids

    async def run_corrections(self, submission_ids: Sequence[uuid.UUID]) -> None:
        """Dispara a correcao de cada submissao, uma por vez, cada uma com o seu
        proprio commit. Best-effort: uma correcao que estoure (provedor fora do
        ar, bug) nunca pode desfazer a submissao, que ja esta duravelmente
        SUBMITTED - o professor a ve como "Em correcao" e o retry manual que ja
        existe na fila de revisao resolve."""
        factory = self._correction_factory or EssayCorrectionService
        for submission_id in submission_ids:
            try:
                await factory(self.session).correct(submission_id)
                await self.session.commit()
            except Exception:
                await self.session.rollback()
                logger.exception(
                    "correcao do lote falhou para essay_submission_id=%s", submission_id
                )
```

E, em `process_batch`, trocar o bloco final:

```python
        batch = await self.session.get(EssayBatchUpload, batch_id)
        batch.status = "DONE"
        await self.session.commit()
```

por:

```python
        # Agrupa as corridas e cria as submissoes SO depois que todas as
        # paginas foram lidas: uma corrida so e conhecida quando se sabe quem
        # esta na pagina seguinte.
        submission_ids = await self.materialize_batch(batch_id)

        batch = await self.session.get(EssayBatchUpload, batch_id)
        batch.status = "DONE"
        await self.session.commit()

        # Correcao por ultimo, com o lote ja DONE: e a parte lenta, e o
        # professor nao deve ficar vendo "processando" so por causa dela.
        await self.run_corrections(submission_ids)
```

E acrescentar `"consecutive_runs",` ao `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_grouping.py tests/test_r4_essay_batch_processing.py -q`
Expected: PASS (18 testes).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/essay_batch.py tests/test_r4_essay_batch_grouping.py
git commit -m "feat(redacao): agrupa paginas consecutivas do mesmo aluno em uma submissao

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 8: Resolução manual de uma página

**Files:**
- Modify: `src/agente_ia_edu/services/essay_batch.py` (acrescenta `resolve_page`)
- Test: `tests/test_r4_essay_batch_resolve.py`

**Interfaces:**
- Consumes: `materialize_batch`/`_materialize_run`/`consecutive_runs` (Task 7).
- Produces: `async EssayBatchService.resolve_page(self, *, batch_id: uuid.UUID, page_id: uuid.UUID, student_id: uuid.UUID) -> list[uuid.UUID]` → ids das submissões que precisam de correção depois da resolução (lista vazia quando nenhuma). Levanta `ValueError` (→ `422`) pra página inexistente no lote, aluno fora da escola do lote, ou página sem texto reconhecido.
- Consumido pela Task 10.

- [ ] **Step 1: Write the failing test**

Criar `tests/test_r4_essay_batch_resolve.py`:

```python
"""R4 lote - resolucao manual: o professor escolhe o aluno de uma pagina."""

import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear, Class, EssayBatchPage, EssayBatchUpload, EssayPrompt, EssaySubmission,
    GradeLevel, Person, PromptAssignment, School, Segment, Student, StudentEnrollment,
)
from agente_ia_edu.services.essay_batch import EssayBatchService


class ResolvePageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.tmp_dir = Path(tempfile.mkdtemp(prefix="r4_resolve_"))

    async def asyncTearDown(self):
        await self.engine.dispose()
        shutil.rmtree(self.tmp_dir, ignore_errors=True)

    async def _seed(self, session, student_names):
        school = School(id=uuid.uuid4(), code="RS-1", name="Escola")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-RS")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="3a", external_id="GRADE-RS",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-RS")
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="3A", external_id="TURMA-RS",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof",
        )
        session.add_all([klass, prompt])
        await session.flush()
        session.add(PromptAssignment(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, assigned_by_external_identity="prof",
        ))
        students = {}
        for index, full_name in enumerate(student_names):
            person = Person(
                id=uuid.uuid4(), school_id=school.id, full_name=full_name,
                external_id=f"PER-R{index}",
            )
            session.add(person)
            await session.flush()
            student = Student(
                id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                external_id=f"STU-R{index}",
            )
            session.add(student)
            await session.flush()
            session.add(StudentEnrollment(
                id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                class_id=klass.id, status="ACTIVE", external_id=f"ENR-R{index}",
            ))
            students[full_name] = student.id
        await session.commit()
        return school, klass, prompt, students

    async def _batch_with_pages(self, session, school, klass, prompt, bodies):
        """Cria o lote e as paginas DIRETO no banco (sem OCR): esta tarefa testa
        so a resolucao manual, que parte de paginas ja lidas e nao identificadas."""
        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, uploaded_by_external_identity="prof",
            status="DONE", total_pages=len(bodies),
        )
        session.add(batch)
        await session.flush()
        pages = []
        for number, body in enumerate(bodies, start=1):
            page = EssayBatchPage(
                id=uuid.uuid4(), batch_id=batch.id, page_number=number,
                storage_uri=str(self.tmp_dir / f"p{number}.png"),
                ocr_body_text=body, status="NEEDS_REVIEW",
            )
            session.add(page)
            pages.append(page)
        await session.commit()
        return batch, pages

    async def test_resolving_a_page_creates_the_submission(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(session, ["Ana Lúcia Ferreira"])
            batch, pages = await self._batch_with_pages(
                session, school, klass, prompt, ["Texto ilegivel pro OCR mas legivel pro humano."]
            )
            service = EssayBatchService(session)

            created_ids = await service.resolve_page(
                batch_id=batch.id, page_id=pages[0].id,
                student_id=students["Ana Lúcia Ferreira"],
            )

            self.assertEqual(len(created_ids), 1)
            submission = await session.get(EssaySubmission, created_ids[0])
            self.assertEqual(submission.student_id, students["Ana Lúcia Ferreira"])
            self.assertEqual(submission.status, "SUBMITTED")
            self.assertEqual(submission.anchor_mode, "TEXT_OFFSET")
            refreshed = await session.get(EssayBatchPage, pages[0].id)
            self.assertEqual(refreshed.status, "RESOLVED_MANUAL")
            self.assertEqual(refreshed.matched_student_id, students["Ana Lúcia Ferreira"])
            self.assertEqual(refreshed.essay_submission_id, submission.id)

    async def test_resolving_a_neighbour_page_joins_the_existing_submission(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(session, ["Ana Lúcia Ferreira"])
            batch, pages = await self._batch_with_pages(
                session, school, klass, prompt, ["Primeira parte.", "Segunda parte."]
            )
            service = EssayBatchService(session)

            first = await service.resolve_page(
                batch_id=batch.id, page_id=pages[0].id,
                student_id=students["Ana Lúcia Ferreira"],
            )
            second = await service.resolve_page(
                batch_id=batch.id, page_id=pages[1].id,
                student_id=students["Ana Lúcia Ferreira"],
            )

            self.assertEqual(first, second, "a segunda pagina entra na MESMA submissao")
            submissions = (await session.execute(select(EssaySubmission))).scalars().all()
            self.assertEqual(len(submissions), 1)
            self.assertIn("Primeira parte.", submissions[0].canonical_text)
            self.assertIn("Segunda parte.", submissions[0].canonical_text)

    async def test_a_non_adjacent_page_of_the_same_student_gets_its_own_submission(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(
                session, ["Ana Lúcia Ferreira", "João da Silva"]
            )
            batch, pages = await self._batch_with_pages(
                session, school, klass, prompt, ["Ana um.", "Joao.", "Ana dois."]
            )
            service = EssayBatchService(session)

            first = await service.resolve_page(
                batch_id=batch.id, page_id=pages[0].id,
                student_id=students["Ana Lúcia Ferreira"],
            )
            third = await service.resolve_page(
                batch_id=batch.id, page_id=pages[2].id,
                student_id=students["Ana Lúcia Ferreira"],
            )

            self.assertNotEqual(first, third)
            submissions = (await session.execute(
                select(EssaySubmission).where(
                    EssaySubmission.student_id == students["Ana Lúcia Ferreira"]
                )
            )).scalars().all()
            self.assertEqual(len(submissions), 2)

    async def test_rejects_a_page_from_another_batch(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(session, ["Ana Lúcia Ferreira"])
            batch, _pages = await self._batch_with_pages(session, school, klass, prompt, ["x"])
            service = EssayBatchService(session)
            with self.assertRaises(ValueError):
                await service.resolve_page(
                    batch_id=batch.id, page_id=uuid.uuid4(),
                    student_id=students["Ana Lúcia Ferreira"],
                )

    async def test_rejects_a_student_from_another_school(self):
        async with self.session_factory() as session:
            school, klass, prompt, _students = await self._seed(session, ["Ana Lúcia Ferreira"])
            other_school = School(id=uuid.uuid4(), code="RS-2", name="Outra")
            session.add(other_school)
            await session.flush()
            other_person = Person(
                id=uuid.uuid4(), school_id=other_school.id, full_name="Fulano",
                external_id="PER-X",
            )
            session.add(other_person)
            await session.flush()
            outsider = Student(
                id=uuid.uuid4(), school_id=other_school.id, person_id=other_person.id,
                external_id="STU-X",
            )
            session.add(outsider)
            await session.commit()

            batch, pages = await self._batch_with_pages(session, school, klass, prompt, ["x"])
            service = EssayBatchService(session)
            with self.assertRaises(ValueError):
                await service.resolve_page(
                    batch_id=batch.id, page_id=pages[0].id, student_id=outsider.id
                )

    async def test_rejects_a_page_with_no_recognized_text(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(session, ["Ana Lúcia Ferreira"])
            batch, pages = await self._batch_with_pages(session, school, klass, prompt, ["   "])
            service = EssayBatchService(session)
            with self.assertRaises(ValueError) as ctx:
                await service.resolve_page(
                    batch_id=batch.id, page_id=pages[0].id,
                    student_id=students["Ana Lúcia Ferreira"],
                )
            self.assertIn("texto", str(ctx.exception).lower())
            refreshed = await session.get(EssayBatchPage, pages[0].id)
            self.assertEqual(refreshed.status, "NEEDS_REVIEW")
            self.assertIsNone(refreshed.matched_student_id)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_resolve.py -q`
Expected: FAIL com `AttributeError: 'EssayBatchService' object has no attribute 'resolve_page'`.

- [ ] **Step 3: Implement resolve_page**

Acrescentar aos imports de `src/agente_ia_edu/services/essay_batch.py`: `from ..db.models import Student` (se ainda não estiver na linha de import de models).

Acrescentar à classe `EssayBatchService`:

```python
    async def resolve_page(
        self, *, batch_id: uuid.UUID, page_id: uuid.UUID, student_id: uuid.UUID
    ) -> list[uuid.UUID]:
        """O professor diz de quem e esta pagina.

        Depois de atribuir o aluno, a corrida que CONTEM esta pagina e
        re-materializada inteira - e assim que uma pagina resolvida ao lado de
        outra ja resolvida do mesmo aluno entra na MESMA submissao em vez de
        criar uma segunda (spec s2, rota de resolve), sem nenhuma regra de
        concatenacao propria: e exatamente a mesma que o caminho automatico usa.
        """
        batch = await self.session.get(EssayBatchUpload, batch_id)
        if batch is None:
            raise ValueError(f"EssayBatchUpload not found: {batch_id}")
        page = await self.session.get(EssayBatchPage, page_id)
        if page is None or page.batch_id != batch_id:
            raise ValueError("Esta pagina nao pertence a este lote.")
        if not (page.ocr_body_text or "").strip():
            raise ValueError(
                "Esta pagina nao tem texto reconhecido - nao da para criar uma redacao "
                "vazia. Envie uma foto mais nitida desta folha em um novo lote."
            )
        student = await self.session.get(Student, student_id)
        if student is None or student.school_id != batch.school_id:
            raise ValueError("Este aluno nao pertence a esta escola.")

        page.matched_student_id = student_id
        page.status = "RESOLVED_MANUAL"
        await self.session.flush()

        pages = (await self.session.execute(
            select(EssayBatchPage)
            .where(EssayBatchPage.batch_id == batch_id)
            .order_by(EssayBatchPage.page_number)
        )).scalars().all()
        run = next(
            (group for group in consecutive_runs(pages) if any(p.id == page_id for p in group)),
            [page],
        )
        submission_id = await self._materialize_run(batch, run)
        await self.session.commit()
        return [submission_id] if submission_id is not None else []
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_resolve.py -q`
Expected: PASS (6 testes).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/essay_batch.py tests/test_r4_essay_batch_resolve.py
git commit -m "feat(redacao): resolucao manual de pagina do lote reaproveitando o agrupamento

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 9: Status agregado do lote

**Files:**
- Modify: `src/agente_ia_edu/services/essay_batch.py` (acrescenta `get_batch_status`)
- Test: `tests/test_r4_essay_batch_status.py`

**Interfaces:**
- Consumes: `class_roster` (Task 6), `EssayBatchUpload`/`EssayBatchPage` (Task 1).
- Produces: `async EssayBatchService.get_batch_status(self, batch_id: uuid.UUID) -> dict` com exatamente estas chaves:
  ```python
  {
      "id": uuid.UUID, "school_id": uuid.UUID, "essay_prompt_id": uuid.UUID,
      "class_id": uuid.UUID, "status": str, "total_pages": int,
      "matched_count": int, "needs_review_count": int,
      "needs_review_pages": [
          {"id": uuid.UUID, "page_number": int, "ocr_name_raw": str | None,
           "ocr_cpf_raw": str | None, "has_text": bool}
      ],
      "available_students": [
          {"student_id": uuid.UUID, "full_name": str, "document_number": str | None}
      ],
  }
  ```
  Consumido pela Task 10.

**Sobre a "URL da imagem" que a spec §2 cita nesta resposta:** ela não vira um campo do JSON. `id` do lote + `id` da página já determinam a rota `GET /api/v1/teacher/essay-batches/{batch_id}/pages/{page_id}/image` (Task 10), e o frontend monta a URL a partir deles — devolver a mesma informação duas vezes só criaria uma segunda fonte de verdade pro caminho da rota.

- [ ] **Step 1: Write the failing test**

Criar `tests/test_r4_essay_batch_status.py`:

```python
"""R4 lote - status agregado que a tela de acompanhamento do professor le."""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear, Class, EssayBatchPage, EssayBatchUpload, EssayPrompt, GradeLevel,
    Person, PromptAssignment, School, Segment, Student, StudentEnrollment,
)
from agente_ia_edu.services.essay_batch import EssayBatchService


class BatchStatusTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed(self, session, student_names):
        school = School(id=uuid.uuid4(), code="ST-1", name="Escola")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-ST")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="3a", external_id="GRADE-ST",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-ST")
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="3A", external_id="TURMA-ST",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof",
        )
        session.add_all([klass, prompt])
        await session.flush()
        session.add(PromptAssignment(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, assigned_by_external_identity="prof",
        ))
        students = {}
        for index, (full_name, document_number) in enumerate(student_names):
            person = Person(
                id=uuid.uuid4(), school_id=school.id, full_name=full_name,
                document_number=document_number, external_id=f"PER-S{index}",
            )
            session.add(person)
            await session.flush()
            student = Student(
                id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                external_id=f"STU-S{index}",
            )
            session.add(student)
            await session.flush()
            session.add(StudentEnrollment(
                id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                class_id=klass.id, status="ACTIVE", external_id=f"ENR-S{index}",
            ))
            students[full_name] = student.id
        await session.commit()
        return school, klass, prompt, students

    async def _batch(self, session, school, klass, prompt, page_specs, *, status="DONE"):
        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, uploaded_by_external_identity="prof",
            status=status, total_pages=len(page_specs),
        )
        session.add(batch)
        await session.flush()
        for number, spec in enumerate(page_specs, start=1):
            session.add(EssayBatchPage(
                id=uuid.uuid4(), batch_id=batch.id, page_number=number,
                storage_uri=f"/p{number}.png", status=spec["status"],
                ocr_name_raw=spec.get("name"), ocr_cpf_raw=spec.get("cpf"),
                ocr_body_text=spec.get("body"),
                matched_student_id=spec.get("student_id"),
                essay_submission_id=spec.get("submission_id"),
            ))
        await session.commit()
        return batch

    async def test_counts_matched_and_needs_review(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(
                session, [("Ana Lúcia Ferreira", "11122233344"), ("João da Silva", None)]
            )
            batch = await self._batch(session, school, klass, prompt, [
                {"status": "MATCHED_AUTO", "name": "ANA LUCIA FERREIRA", "body": "t",
                 "student_id": students["Ana Lúcia Ferreira"], "submission_id": uuid.uuid4()},
                {"status": "NEEDS_REVIEW", "name": "CARLOS MENDES", "cpf": "99988877766",
                 "body": "t"},
                {"status": "RESOLVED_MANUAL", "name": None, "body": "t",
                 "student_id": students["João da Silva"], "submission_id": uuid.uuid4()},
            ])

            data = await EssayBatchService(session).get_batch_status(batch.id)

            self.assertEqual(data["total_pages"], 3)
            self.assertEqual(data["matched_count"], 2)
            self.assertEqual(data["needs_review_count"], 1)
            self.assertEqual(data["status"], "DONE")

    async def test_lists_needs_review_pages_with_their_ocr_hints(self):
        async with self.session_factory() as session:
            school, klass, prompt, _students = await self._seed(
                session, [("Ana Lúcia Ferreira", None)]
            )
            batch = await self._batch(session, school, klass, prompt, [
                {"status": "NEEDS_REVIEW", "name": "CARLOS MENDES", "cpf": "99988877766",
                 "body": "algum texto"},
                {"status": "NEEDS_REVIEW", "name": None, "cpf": None, "body": None},
            ])

            data = await EssayBatchService(session).get_batch_status(batch.id)

            self.assertEqual([p["page_number"] for p in data["needs_review_pages"]], [1, 2])
            first, second = data["needs_review_pages"]
            self.assertEqual(first["ocr_name_raw"], "CARLOS MENDES")
            self.assertEqual(first["ocr_cpf_raw"], "99988877766")
            self.assertTrue(first["has_text"])
            self.assertIsNone(second["ocr_name_raw"])
            self.assertFalse(second["has_text"])

    async def test_available_students_excludes_whoever_already_has_a_submission(self):
        async with self.session_factory() as session:
            school, klass, prompt, students = await self._seed(
                session, [("Ana Lúcia Ferreira", "11122233344"), ("João da Silva", None)]
            )
            batch = await self._batch(session, school, klass, prompt, [
                {"status": "MATCHED_AUTO", "body": "t",
                 "student_id": students["Ana Lúcia Ferreira"], "submission_id": uuid.uuid4()},
                {"status": "NEEDS_REVIEW", "body": "t"},
            ])

            data = await EssayBatchService(session).get_batch_status(batch.id)

            self.assertEqual(
                data["available_students"],
                [{"student_id": students["João da Silva"], "full_name": "João da Silva",
                  "document_number": None}],
            )

    async def test_a_processing_batch_reports_its_partial_state(self):
        async with self.session_factory() as session:
            school, klass, prompt, _students = await self._seed(
                session, [("Ana Lúcia Ferreira", None)]
            )
            batch = await self._batch(session, school, klass, prompt, [
                {"status": "NEEDS_REVIEW"}, {"status": "NEEDS_REVIEW"},
            ], status="PROCESSING")

            data = await EssayBatchService(session).get_batch_status(batch.id)

            self.assertEqual(data["status"], "PROCESSING")
            self.assertEqual(data["matched_count"], 0)
            self.assertEqual(data["needs_review_count"], 2)

    async def test_unknown_batch_raises(self):
        async with self.session_factory() as session:
            with self.assertRaises(ValueError):
                await EssayBatchService(session).get_batch_status(uuid.uuid4())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_status.py -q`
Expected: FAIL com `AttributeError: 'EssayBatchService' object has no attribute 'get_batch_status'`.

- [ ] **Step 3: Implement get_batch_status**

Acrescentar à classe `EssayBatchService`:

```python
    async def get_batch_status(self, batch_id: uuid.UUID) -> dict:
        """Estado agregado do lote pra tela de acompanhamento do professor.

        Devolve dicts puros (nunca objetos ORM) pelo motivo de sempre: a rota
        monta a resposta e commita, e expire_on_commit=True transformaria
        qualquer acesso posterior a um atributo em MissingGreenlet.

        ``available_students`` e a lista pro seletor da tela de resolucao
        manual: alunos ATIVOS da turma do lote que ainda NAO tem nenhuma pagina
        deste lote vinculada a uma submissao (spec s2).
        """
        batch = await self.session.get(EssayBatchUpload, batch_id)
        if batch is None:
            raise ValueError(f"EssayBatchUpload not found: {batch_id}")

        pages = (await self.session.execute(
            select(EssayBatchPage)
            .where(EssayBatchPage.batch_id == batch_id)
            .order_by(EssayBatchPage.page_number)
        )).scalars().all()

        needs_review = [page for page in pages if page.status == "NEEDS_REVIEW"]
        taken_student_ids = {
            page.matched_student_id for page in pages
            if page.essay_submission_id is not None and page.matched_student_id is not None
        }
        roster = await self.class_roster(school_id=batch.school_id, class_id=batch.class_id)

        return {
            "id": batch.id,
            "school_id": batch.school_id,
            "essay_prompt_id": batch.essay_prompt_id,
            "class_id": batch.class_id,
            "status": batch.status,
            "total_pages": batch.total_pages,
            "matched_count": len(pages) - len(needs_review),
            "needs_review_count": len(needs_review),
            "needs_review_pages": [
                {
                    "id": page.id,
                    "page_number": page.page_number,
                    "ocr_name_raw": page.ocr_name_raw,
                    "ocr_cpf_raw": page.ocr_cpf_raw,
                    # O seletor de aluno fica desabilitado quando nao ha texto:
                    # resolver essa pagina so criaria uma redacao vazia, que
                    # resolve_page recusa de qualquer forma.
                    "has_text": bool((page.ocr_body_text or "").strip()),
                }
                for page in needs_review
            ],
            "available_students": [
                {
                    "student_id": student_id,
                    "full_name": full_name,
                    "document_number": document_number,
                }
                for student_id, full_name, document_number in roster
                if student_id not in taken_student_ids
            ],
        }
```

- [ ] **Step 4: Run test to verify it passes**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_status.py -q`
Expected: PASS (5 testes).

- [ ] **Step 5: Run every service test of this leva together**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batch_matching.py tests/test_r4_essay_batch_ocr_regions.py tests/test_r4_essay_batch_create.py tests/test_r4_essay_batch_processing.py tests/test_r4_essay_batch_grouping.py tests/test_r4_essay_batch_resolve.py tests/test_r4_essay_batch_status.py -q`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/essay_batch.py tests/test_r4_essay_batch_status.py
git commit -m "feat(redacao): status agregado do lote com fila de resolucao manual

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 10: Rotas do lote (`api/routes/essay_batches.py`)

**Files:**
- Create: `src/agente_ia_edu/api/routes/essay_batches.py`
- Modify: `src/agente_ia_edu/api/app.py` (import + `include_router`)
- Test: `tests/test_r4_essay_batches_routes.py`

**Interfaces:**
- Consumes: `EssayBatchService.create_batch` / `process_batch` / `get_batch_status` / `resolve_page` / `run_corrections` (Tasks 5-9).
- Produces: `essay_batches_router` (prefix `/api/v1/teacher/essay-batches`) com:
  - `POST ""` → `202`, body `EssayBatchCreatedResponse{id, school_id, essay_prompt_id, class_id, status, total_pages}`; multipart `essay_prompt_id: UUID = Form(...)`, `class_id: UUID = Form(...)`, `files: list[UploadFile] = File(...)`.
  - `GET "/{batch_id}"` → `EssayBatchStatusResponse`.
  - `POST "/{batch_id}/pages/{page_id}/resolve"` → `EssayBatchStatusResponse` (o estado já atualizado, pra tela não precisar de um segundo GET).
  - `GET "/{batch_id}/pages/{page_id}/image"` → `FileResponse`.

- [ ] **Step 1: Write the failing test**

Criar `tests/test_r4_essay_batches_routes.py`:

```python
"""R4 lote - rotas do professor: criar lote, acompanhar, resolver, ver imagem.

TestClient + dependency_overrides, mesmo padrao de
tests/test_r2_essay_prompts_routes.py. O provedor de OCR e o de correcao sao
substituidos por dublês, entao nenhuma chamada de IA acontece aqui.
"""

import asyncio
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear, Class, EssayBatchPage, EssayPrompt, EssaySubmission, GradeLevel,
    Person, PromptAssignment, School, Segment, Student, StudentEnrollment, UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.providers.models import EssayOcrToken, EssayPageTranscriptionResult


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class ScriptedTranscriber:
    script: dict[str, tuple[str, str]] = {}

    async def transcribe_page(self, request):
        import pymupdf

        doc = pymupdf.open(str(request.image_path))
        try:
            stamped = doc[0].get_text().strip().splitlines()
        finally:
            doc.close()
        key = next((line.strip() for line in stamped if line.strip()), "")
        header, body = ScriptedTranscriber.script.get(key, ("", ""))
        text = header if "_header" in request.image_path.name else body
        return EssayPageTranscriptionResult(
            tokens=(EssayOcrToken(text=text, confidence=0.95, start=0, end=len(text)),),
            provider="fake", model="fake-1",
        )


class NoopCorrectionService:
    def __init__(self, session):
        self.session = session

    async def correct(self, essay_submission_id):
        return None


def _write_stamped_page(path: Path, key: str) -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595.44, height=842.40)
    page.insert_text((50, 60), key, fontsize=11)
    page.insert_text((50, 500), key, fontsize=11)
    page.get_pixmap(dpi=100).save(str(path))
    doc.close()
    return path


class EssayBatchesRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        cls.loop.run_until_complete(_prep())
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_lote")
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def setUp(self):
        import agente_ia_edu.api.routes.essay_batches as routes_module

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="r4_routes_"))
        self._original_build = routes_module.build_batch_service
        storage_root = self.tmp_dir / "storage"

        def _build(session):
            from agente_ia_edu.services.essay_batch import EssayBatchService
            from agente_ia_edu.services.material_storage import MaterialStorage

            return EssayBatchService(
                session, storage=MaterialStorage(root=storage_root),
                transcriber=ScriptedTranscriber(),
                correction_factory=NoopCorrectionService,
            )

        routes_module.build_batch_service = _build

    def tearDown(self):
        import agente_ia_edu.api.routes.essay_batches as routes_module

        routes_module.build_batch_service = self._original_build
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        self.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_lote")

    def _seed(self, code: str, student_names):
        async def _run():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"BR-{code}", name=f"escola-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="prof_lote", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                segment = Segment(
                    id=uuid.uuid4(), school_id=school.id, name="seg", external_id=f"SEG-{code}"
                )
                session.add(segment)
                await session.flush()
                grade = GradeLevel(
                    id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
                    name="3a", external_id=f"GRADE-{code}",
                )
                year = AcademicYear(
                    id=uuid.uuid4(), school_id=school.id, year=2026, external_id=f"YEAR-{code}"
                )
                session.add_all([grade, year])
                await session.flush()
                klass = Class(
                    id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
                    grade_level_id=grade.id, name="3A", external_id=f"TURMA-{code}",
                )
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
                    year=2026, created_by_external_identity="prof_lote",
                )
                session.add_all([klass, prompt])
                await session.flush()
                session.add(PromptAssignment(
                    id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                    class_id=klass.id, assigned_by_external_identity="prof_lote",
                ))
                students = {}
                for index, full_name in enumerate(student_names):
                    person = Person(
                        id=uuid.uuid4(), school_id=school.id, full_name=full_name,
                        external_id=f"PER-{code}-{index}",
                    )
                    session.add(person)
                    await session.flush()
                    student = Student(
                        id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                        external_id=f"STU-{code}-{index}",
                    )
                    session.add(student)
                    await session.flush()
                    session.add(StudentEnrollment(
                        id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                        class_id=klass.id, status="ACTIVE", external_id=f"ENR-{code}-{index}",
                    ))
                    students[full_name] = str(student.id)
                await session.commit()
                return str(school.id), str(klass.id), str(prompt.id), students

        return self.loop.run_until_complete(_run())

    def _upload(self, prompt_id, class_id, keys):
        files = []
        for key in keys:
            path = _write_stamped_page(self.tmp_dir / f"{key}.png", key)
            files.append(("files", (f"{key}.png", path.read_bytes(), "image/png")))
        return self.client.post(
            "/api/v1/teacher/essay-batches",
            data={"essay_prompt_id": prompt_id, "class_id": class_id},
            files=files,
        )

    def test_happy_path_every_page_matches(self):
        _school_id, class_id, prompt_id, students = self._seed(
            "1", ["Ana Lúcia Ferreira", "João da Silva"]
        )
        ScriptedTranscriber.script = {
            "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Texto da Ana."),
            "p2": ("NOME COMPLETO DO PARTICIPANTE Joao da Silva", "Texto do Joao."),
        }

        create = self._upload(prompt_id, class_id, ["p1", "p2"])
        self.assertEqual(create.status_code, 202, create.text)
        batch_id = create.json()["id"]
        self.assertEqual(create.json()["total_pages"], 2)
        self.assertEqual(create.json()["status"], "PROCESSING")

        # TestClient roda as BackgroundTasks antes de devolver a resposta, entao
        # nesse ponto o lote ja terminou de processar.
        status = self.client.get(f"/api/v1/teacher/essay-batches/{batch_id}")
        self.assertEqual(status.status_code, 200, status.text)
        body = status.json()
        self.assertEqual(body["status"], "DONE")
        self.assertEqual(body["matched_count"], 2)
        self.assertEqual(body["needs_review_count"], 0)
        self.assertEqual(body["needs_review_pages"], [])

        async def _submissions():
            async with self.factory() as session:
                return (await session.execute(select(EssaySubmission))).scalars().all()

        submissions = self.loop.run_until_complete(_submissions())
        self.assertEqual(len(submissions), 2)
        self.assertEqual({str(s.student_id) for s in submissions}, set(students.values()))

    def test_unmatched_page_lands_in_the_manual_queue_and_can_be_resolved(self):
        _school_id, class_id, prompt_id, students = self._seed("2", ["Ana Lúcia Ferreira"])
        ScriptedTranscriber.script = {
            "q1": ("NOME COMPLETO DO PARTICIPANTE Carlos Mendes", "Texto de alguem."),
        }

        create = self._upload(prompt_id, class_id, ["q1"])
        batch_id = create.json()["id"]

        status = self.client.get(f"/api/v1/teacher/essay-batches/{batch_id}").json()
        self.assertEqual(status["needs_review_count"], 1)
        page = status["needs_review_pages"][0]
        self.assertEqual(page["ocr_name_raw"], "CARLOS MENDES")
        self.assertTrue(page["has_text"])
        self.assertEqual(
            [s["student_id"] for s in status["available_students"]],
            [students["Ana Lúcia Ferreira"]],
        )

        resolve = self.client.post(
            f"/api/v1/teacher/essay-batches/{batch_id}/pages/{page['id']}/resolve",
            json={"student_id": students["Ana Lúcia Ferreira"]},
        )
        self.assertEqual(resolve.status_code, 200, resolve.text)
        self.assertEqual(resolve.json()["needs_review_count"], 0)
        self.assertEqual(resolve.json()["matched_count"], 1)

    def test_batch_above_the_page_limit_is_422(self):
        _school_id, class_id, prompt_id, _students = self._seed("3", ["Ana Lúcia Ferreira"])
        ScriptedTranscriber.script = {}
        files = []
        for index in range(61):
            path = _write_stamped_page(self.tmp_dir / f"big{index}.png", f"big{index}")
            files.append(("files", (f"big{index}.png", path.read_bytes(), "image/png")))
        response = self.client.post(
            "/api/v1/teacher/essay-batches",
            data={"essay_prompt_id": prompt_id, "class_id": class_id}, files=files,
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("60", response.json()["detail"])

    def test_unsupported_file_type_is_422(self):
        _school_id, class_id, prompt_id, _students = self._seed("4", ["Ana Lúcia Ferreira"])
        response = self.client.post(
            "/api/v1/teacher/essay-batches",
            data={"essay_prompt_id": prompt_id, "class_id": class_id},
            files=[("files", ("planilha.xlsx", b"x", "application/vnd.ms-excel"))],
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_a_batch_from_another_school_is_403(self):
        _school_a, class_a, prompt_a, _students_a = self._seed("5", ["Ana Lúcia Ferreira"])
        ScriptedTranscriber.script = {
            "r1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Texto."),
        }
        batch_id = self._upload(prompt_a, class_a, ["r1"]).json()["id"]

        async def _other_teacher():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code="BR-OTHER", name="outra")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="prof_outro", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                await session.commit()

        self.loop.run_until_complete(_other_teacher())
        self.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_outro")
        response = self.client.get(f"/api/v1/teacher/essay-batches/{batch_id}")
        self.assertEqual(response.status_code, 403, response.text)

    def test_page_image_is_served(self):
        _school_id, class_id, prompt_id, _students = self._seed("6", ["Ana Lúcia Ferreira"])
        ScriptedTranscriber.script = {"s1": ("NOME Desconhecido Ninguem", "Texto.")}
        batch_id = self._upload(prompt_id, class_id, ["s1"]).json()["id"]

        async def _page_id():
            async with self.factory() as session:
                page = (await session.execute(
                    select(EssayBatchPage).where(EssayBatchPage.batch_id == uuid.UUID(batch_id))
                )).scalars().first()
                return str(page.id)

        page_id = self.loop.run_until_complete(_page_id())
        response = self.client.get(
            f"/api/v1/teacher/essay-batches/{batch_id}/pages/{page_id}/image"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content)

    def test_resolving_a_page_with_no_text_is_422(self):
        _school_id, class_id, prompt_id, students = self._seed("7", ["Ana Lúcia Ferreira"])
        ScriptedTranscriber.script = {"t1": ("NOME Alguem Desconhecido", "   ")}
        batch_id = self._upload(prompt_id, class_id, ["t1"]).json()["id"]
        status = self.client.get(f"/api/v1/teacher/essay-batches/{batch_id}").json()
        page = status["needs_review_pages"][0]
        self.assertFalse(page["has_text"])

        response = self.client.post(
            f"/api/v1/teacher/essay-batches/{batch_id}/pages/{page['id']}/resolve",
            json={"student_id": students["Ana Lúcia Ferreira"]},
        )
        self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batches_routes.py -q`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.api.routes.essay_batches'`.

- [ ] **Step 3: Write the router**

Criar `src/agente_ia_edu/api/routes/essay_batches.py`:

```python
"""Envio em lote de redacoes fisicas - superficie do professor.

TEACHER/COORDINATOR/DIRECTOR/PLATFORM_ADMIN, o mesmo conjunto de papeis que
essay_prompts.py e essay_corrections.py ja usam. school_id sempre vem do
contexto resolvido, nunca do corpo; um lote de outra escola e 403, nunca 404.

Spec: docs/superpowers/specs/2026-09-28-envio-lote-redacao-design.md
"""

from __future__ import annotations

import logging
import shutil
import tempfile
import uuid
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import FileResponse
from pydantic import BaseModel
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_identity, get_session_factory
from ...db.models import EssayBatchPage, EssayBatchUpload
from ...identity import ExternalIdentityContext
from ...services.authorization import AuthorizationService
from ...services.essay_batch import ALLOWED_BATCH_SUFFIXES, EssayBatchService

logger = logging.getLogger(__name__)

essay_batches_router = APIRouter(
    prefix="/api/v1/teacher/essay-batches", tags=["essay-batches"]
)

# Mesmo teto por arquivo que essay_submissions.py e essay_prompts.py ja usam.
_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


def build_batch_service(session: AsyncSession) -> EssayBatchService:
    """Ponto unico de construcao do servico - e o que os testes de rota
    substituem pra injetar um transcritor e um corretor falsos sem precisar de
    nenhuma variavel de ambiente de provedor de IA."""
    return EssayBatchService(session)


class EssayBatchCreatedResponse(BaseModel):
    id: UUID
    school_id: UUID
    essay_prompt_id: UUID
    class_id: UUID
    status: str
    total_pages: int


class EssayBatchNeedsReviewPage(BaseModel):
    id: UUID
    page_number: int
    ocr_name_raw: Optional[str] = None
    ocr_cpf_raw: Optional[str] = None
    has_text: bool


class EssayBatchAvailableStudent(BaseModel):
    student_id: UUID
    full_name: str
    document_number: Optional[str] = None


class EssayBatchStatusResponse(BaseModel):
    id: UUID
    school_id: UUID
    essay_prompt_id: UUID
    class_id: UUID
    status: str
    total_pages: int
    matched_count: int
    needs_review_count: int
    needs_review_pages: list[EssayBatchNeedsReviewPage]
    available_students: list[EssayBatchAvailableStudent]


class ResolveBatchPageRequest(BaseModel):
    student_id: UUID


async def _authorize(identity: ExternalIdentityContext, session: AsyncSession) -> uuid.UUID:
    authz = AuthorizationService(session)
    context = await authz.resolve_context(identity)
    role_check = await authz.require_role(
        context, "TEACHER", "COORDINATOR", "DIRECTOR", "PLATFORM_ADMIN"
    )
    if not role_check.allowed:
        raise HTTPException(
            status_code=403,
            detail="Enviar redacoes em lote requer um papel de professor, coordenador, "
            "diretor ou administrador da plataforma.",
        )
    if context.school_id is None:
        raise HTTPException(status_code=403, detail="An active school context is required.")
    return uuid.UUID(str(context.school_id))


async def _batch_for_own_school_or_403(
    session: AsyncSession, *, batch_id: uuid.UUID, school_id: uuid.UUID
) -> EssayBatchUpload:
    batch = await session.get(EssayBatchUpload, batch_id)
    if batch is None or batch.school_id != school_id:
        raise HTTPException(status_code=403, detail="Este lote nao e seu.")
    return batch


async def _run_batch_processing_in_background(batch_id: UUID, session_factory) -> None:
    """Roda depois que a resposta 202 ja saiu, com a sua propria sessao (a da
    requisicao ja esta fechada). process_batch e best-effort por pagina e ja
    trata falha de OCR; este try/except cobre so uma falha de infraestrutura de
    verdade (o banco fora do ar, um bug), pra que o lote nunca fique travado em
    PROCESSING sem nem uma linha de log pra diagnosticar."""
    try:
        async with session_factory() as session:
            await build_batch_service(session).process_batch(batch_id)
    except Exception:
        logger.exception("processamento em segundo plano falhou para batch_id=%s", batch_id)


@essay_batches_router.post("", status_code=202, response_model=EssayBatchCreatedResponse)
async def create_essay_batch(
    background_tasks: BackgroundTasks,
    essay_prompt_id: UUID = Form(...),
    class_id: UUID = Form(...),
    files: list[UploadFile] = File(...),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayBatchCreatedResponse:
    """Devolve 202 assim que o lote esta gravado, SEM esperar o processamento -
    o professor acompanha por GET /{batch_id} (spec s5).

    Streaming em pedacos de 1MB pra um arquivo temporario, checando o tamanho a
    cada pedaco, exatamente como upload_prompt_material ja faz: um upload
    gigante nunca chega a virar um arquivo em MaterialStorage.
    """
    async with session_factory() as session:
        school_id = await _authorize(identity, session)

        tmp_dir = Path(tempfile.mkdtemp(prefix="r4_batch_upload_"))
        try:
            source_paths: list[Path] = []
            for index, upload in enumerate(files):
                suffix = Path(upload.filename or "").suffix.lower()
                if suffix not in ALLOWED_BATCH_SUFFIXES:
                    raise HTTPException(
                        status_code=422, detail=f"Formato de arquivo nao suportado: {suffix!r}"
                    )
                tmp_path = tmp_dir / f"{index:03d}_{Path(upload.filename or 'arquivo').name}"
                size = 0
                with open(tmp_path, "wb") as out:
                    while chunk := await upload.read(1024 * 1024):
                        size += len(chunk)
                        if size > _MAX_UPLOAD_BYTES:
                            out.close()
                            raise HTTPException(
                                status_code=413,
                                detail=f"Arquivo {upload.filename!r} passa de 25MB.",
                            )
                        out.write(chunk)
                source_paths.append(tmp_path)

            try:
                created = await build_batch_service(session).create_batch(
                    school_id=school_id, essay_prompt_id=essay_prompt_id, class_id=class_id,
                    uploaded_by_external_identity=identity.external_user_id,
                    source_paths=source_paths,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            # MaterialStorage ja copiou tudo o que precisa sobreviver; o que
            # fica aqui e so rascunho (inclusive os "<stem>_pages" que o split
            # de PDF escreveu ao lado do arquivo).
            shutil.rmtree(tmp_dir, ignore_errors=True)

        # Resposta montada ANTES do commit (expire_on_commit=True em producao).
        response = EssayBatchCreatedResponse(**created)
        await session.commit()

    background_tasks.add_task(
        _run_batch_processing_in_background, response.id, session_factory
    )
    return response


@essay_batches_router.get("/{batch_id}", response_model=EssayBatchStatusResponse)
async def get_essay_batch(
    batch_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayBatchStatusResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _batch_for_own_school_or_403(session, batch_id=batch_id, school_id=school_id)
        data = await build_batch_service(session).get_batch_status(batch_id)
        return EssayBatchStatusResponse(**data)


async def _run_corrections_in_background(submission_ids, session_factory) -> None:
    try:
        async with session_factory() as session:
            await build_batch_service(session).run_corrections(submission_ids)
    except Exception:
        logger.exception("correcao em segundo plano falhou para %s", submission_ids)


@essay_batches_router.post(
    "/{batch_id}/pages/{page_id}/resolve", response_model=EssayBatchStatusResponse
)
async def resolve_essay_batch_page(
    batch_id: UUID,
    page_id: UUID,
    request: ResolveBatchPageRequest,
    background_tasks: BackgroundTasks,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayBatchStatusResponse:
    """Devolve o status JA atualizado do lote inteiro, pra que a tela de
    resolucao manual nao precise de um segundo GET depois de cada pagina."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _batch_for_own_school_or_403(session, batch_id=batch_id, school_id=school_id)
        service = build_batch_service(session)
        try:
            submission_ids = await service.resolve_page(
                batch_id=batch_id, page_id=page_id, student_id=request.student_id
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        data = await service.get_batch_status(batch_id)
        response = EssayBatchStatusResponse(**data)

    if submission_ids:
        # Mesma razao de confirm_essay_submission: a chamada de correcao e a
        # parte lenta e o professor nao deve esperar por ela.
        background_tasks.add_task(
            _run_corrections_in_background, submission_ids, session_factory
        )
    return response


@essay_batches_router.get("/{batch_id}/pages/{page_id}/image")
async def get_essay_batch_page_image(
    batch_id: UUID,
    page_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    """A imagem da folha, pro professor identificar o aluno olhando a letra."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _batch_for_own_school_or_403(session, batch_id=batch_id, school_id=school_id)
        page = await session.get(EssayBatchPage, page_id)
        if page is None or page.batch_id != batch_id:
            raise HTTPException(status_code=404, detail="Pagina nao encontrada neste lote.")
        return FileResponse(page.storage_uri)
```

- [ ] **Step 4: Wire the router**

Em `src/agente_ia_edu/api/app.py`, acrescentar depois da linha `from .routes.essay_prompts import essay_prompts_router`:

```python
from .routes.essay_batches import essay_batches_router
```

E depois da linha `app.include_router(essay_corrections_router, dependencies=reception_only_guard)`:

```python
    app.include_router(essay_batches_router, dependencies=reception_only_guard)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_batches_routes.py -q`
Expected: PASS (7 testes).

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_batches.py src/agente_ia_edu/api/app.py tests/test_r4_essay_batches_routes.py
git commit -m "feat(redacao): rotas do envio em lote com processamento em segundo plano

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 11: Rota da folha de resposta + upload da logo da escola

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_prompts.py` (nova rota `GET /{essay_prompt_id}/answer-sheet.pdf`)
- Modify: `src/agente_ia_edu/api/routes/essay_batches.py` (novo `teacher_school_router` com `GET`/`POST` da logo)
- Modify: `src/agente_ia_edu/api/app.py` (import + `include_router` do `teacher_school_router`)
- Test: `tests/test_r4_essay_answer_sheet_route.py`

**Interfaces:**
- Consumes: `render_answer_sheet_pdf`/`answer_sheet_available` (Task 2), `_authorize`/`_prompt_for_own_school_or_403` (`essay_prompts.py`, já existentes), `MaterialStorage.store` (já existente).
- Produces:
  - `GET /api/v1/catalog/essay-prompts/{id}/answer-sheet.pdf?copies=N` → `application/pdf`, `Content-Disposition: attachment`.
  - `teacher_school_router` (prefix `/api/v1/teacher/school`) com `GET "/logo"` → `SchoolLogoResponse{has_logo: bool}` e `POST "/logo"` (multipart `file`) → `SchoolLogoResponse`.

- [ ] **Step 1: Write the failing test**

Criar `tests/test_r4_essay_answer_sheet_route.py`:

```python
"""R4 lote - rota da folha de resposta em branco e upload da logo da escola."""

import asyncio
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import EssayPrompt, School, UserSchoolLink
from agente_ia_edu.identity import ExternalIdentityContext


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


def _logo_bytes() -> bytes:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=120, height=60)
    page.insert_text((10, 35), "LOGO", fontsize=20)
    data = page.get_pixmap(dpi=150).tobytes("png")
    doc.close()
    return data


class AnswerSheetRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        cls.loop.run_until_complete(_prep())
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_folha")
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _seed(self, code: str):
        async def _run():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"AS-{code}", name=f"escola-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="prof_folha", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id,
                    title="Os desafios da mobilidade urbana", statement="Disserte.",
                    year=2026, created_by_external_identity="prof_folha",
                )
                session.add(prompt)
                await session.commit()
                return str(school.id), str(prompt.id)

        return self.loop.run_until_complete(_run())

    def test_generates_the_requested_number_of_copies(self):
        import pymupdf

        _school_id, prompt_id = self._seed("1")
        response = self.client.get(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/answer-sheet.pdf?copies=4"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.headers["content-type"], "application/pdf")
        self.assertIn("attachment", response.headers["content-disposition"])
        doc = pymupdf.open(stream=response.content, filetype="pdf")
        try:
            self.assertEqual(doc.page_count, 4)
            self.assertTrue(doc[0].search_for("Os desafios da mobilidade"))
        finally:
            doc.close()

    def test_defaults_to_one_copy(self):
        import pymupdf

        _school_id, prompt_id = self._seed("2")
        response = self.client.get(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/answer-sheet.pdf"
        )
        doc = pymupdf.open(stream=response.content, filetype="pdf")
        try:
            self.assertEqual(doc.page_count, 1)
        finally:
            doc.close()

    def test_rejects_an_out_of_range_copies_value(self):
        _school_id, prompt_id = self._seed("3")
        self.assertEqual(
            self.client.get(
                f"/api/v1/catalog/essay-prompts/{prompt_id}/answer-sheet.pdf?copies=0"
            ).status_code, 422,
        )
        self.assertEqual(
            self.client.get(
                f"/api/v1/catalog/essay-prompts/{prompt_id}/answer-sheet.pdf?copies=200"
            ).status_code, 422,
        )

    def test_a_prompt_from_another_school_is_403(self):
        _school_id, prompt_id = self._seed("4")

        async def _other():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code="AS-OTHER", name="outra")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="prof_outro_folha", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                await session.commit()

        self.loop.run_until_complete(_other())
        self.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_outro_folha")
        try:
            response = self.client.get(
                f"/api/v1/catalog/essay-prompts/{prompt_id}/answer-sheet.pdf"
            )
            self.assertEqual(response.status_code, 403)
        finally:
            self.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_folha")

    def test_uploading_a_logo_makes_the_sheet_carry_it(self):
        import pymupdf

        _school_id, prompt_id = self._seed("5")

        before = self.client.get("/api/v1/teacher/school/logo")
        self.assertEqual(before.status_code, 200, before.text)
        self.assertFalse(before.json()["has_logo"])

        without_logo = self.client.get(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/answer-sheet.pdf"
        )
        doc = pymupdf.open(stream=without_logo.content, filetype="pdf")
        try:
            self.assertEqual(len(doc[0].get_images(full=True)), 0)
        finally:
            doc.close()

        upload = self.client.post(
            "/api/v1/teacher/school/logo",
            files={"file": ("logo.png", _logo_bytes(), "image/png")},
        )
        self.assertEqual(upload.status_code, 200, upload.text)
        self.assertTrue(upload.json()["has_logo"])
        self.assertTrue(self.client.get("/api/v1/teacher/school/logo").json()["has_logo"])

        with_logo = self.client.get(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/answer-sheet.pdf"
        )
        doc = pymupdf.open(stream=with_logo.content, filetype="pdf")
        try:
            self.assertEqual(len(doc[0].get_images(full=True)), 1)
        finally:
            doc.close()

    def test_rejects_a_logo_that_is_not_an_image(self):
        self._seed("6")
        response = self.client.post(
            "/api/v1/teacher/school/logo",
            files={"file": ("logo.pdf", b"%PDF-1.4", "application/pdf")},
        )
        self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
```

> **Nota de isolamento:** `test_uploading_a_logo_makes_the_sheet_carry_it` grava a logo no `MaterialStorage` real (`var/material_storage/`), como o teste de upload de material de proposta já faz hoje. Se ao implementar isso incomodar, injetar um `MaterialStorage(root=...)` temporário pelo mesmo mecanismo de `build_batch_service` (uma função `build_material_storage()` no módulo da rota, substituída no `setUp`) — mas só se for realmente necessário; o padrão já existente no repositório é gravar mesmo.

- [ ] **Step 2: Run test to verify it fails**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_answer_sheet_route.py -q`
Expected: FAIL com `404` em todas as rotas novas.

- [ ] **Step 3: Add the answer-sheet route**

Em `src/agente_ia_edu/api/routes/essay_prompts.py`, acrescentar `School` à linha de import de models que já existe (`from ...db.models import EssayPrompt, PromptAssignment, PromptMaterial` → `from ...db.models import EssayPrompt, PromptAssignment, PromptMaterial, School`) e acrescentar uma linha de import nova:

```python
from ...services.essay_answer_sheet import answer_sheet_available, render_answer_sheet_pdf
```

`Query`, `Response` e `HTTPException` já estão importados nesse arquivo — não duplicar.

E acrescentar esta rota ANTES de `@essay_prompts_router.get("/{essay_prompt_id}", ...)` (a rota catch-all de detalhe fica sempre por último, mesma razão documentada em `list_essay_prompts_trash`):

```python
@essay_prompts_router.get("/{essay_prompt_id}/answer-sheet.pdf")
async def get_essay_prompt_answer_sheet(
    essay_prompt_id: UUID,
    copies: int = Query(1, ge=1, le=60),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> Response:
    """A folha de resposta em branco desta proposta, pronta pra imprimir - uma
    folha por pagina do PDF gerado.

    O teto de 60 copias e o mesmo teto de paginas de um lote: mais folhas do que
    cabem num envio nao teriam pra onde ir.

    Logo nao cadastrada nunca bloqueia a geracao (spec s7) - a folha sai sem
    logo, exatamente como sairia se o arquivo tivesse sumido do disco.
    """
    if not answer_sheet_available():
        raise HTTPException(
            status_code=503, detail="PDF export requires the 'pymupdf' package"
        )
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        prompt = await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        school = await session.get(School, school_id)
        logo_path = school.logo_storage_uri if school is not None else None
        data = render_answer_sheet_pdf(
            prompt_title=prompt.title, logo_path=logo_path, copies=copies
        )
        safe_title = "".join(
            c if c.isalnum() or c in " -_" else "_" for c in prompt.title
        ).strip() or "redacao"
        filename = f"folha-de-redacao-{safe_title[:60]}.pdf"
        return Response(
            content=data, media_type="application/pdf",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )
```

- [ ] **Step 4: Add the school-logo routes**

Em `src/agente_ia_edu/api/routes/essay_batches.py`, acrescentar aos imports `from ...db.models import School` e `from ...services.material_storage import MaterialStorage`, e acrescentar ao fim do arquivo:

```python
teacher_school_router = APIRouter(prefix="/api/v1/teacher/school", tags=["teacher-school"])

_ALLOWED_LOGO_SUFFIXES = (".png", ".jpg", ".jpeg")


class SchoolLogoResponse(BaseModel):
    has_logo: bool


@teacher_school_router.get("/logo", response_model=SchoolLogoResponse)
async def get_school_logo_state(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> SchoolLogoResponse:
    """So diz SE existe uma logo - o arquivo em si nunca e devolvido por esta
    rota; quem precisa dele e a geracao da folha, do lado do servidor."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        school = await session.get(School, school_id)
        return SchoolLogoResponse(
            has_logo=bool(school is not None and school.logo_storage_uri)
        )


@teacher_school_router.post("/logo", response_model=SchoolLogoResponse)
async def upload_school_logo(
    file: UploadFile = File(...),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> SchoolLogoResponse:
    """Mesma forma de upload_prompt_material: streaming em pedacos de 1MB pra um
    temporario com teto de tamanho, e so entao MaterialStorage.store()."""
    suffix = Path(file.filename or "").suffix.lower()
    if suffix not in _ALLOWED_LOGO_SUFFIXES:
        raise HTTPException(
            status_code=422,
            detail=f"A logo precisa ser uma imagem PNG ou JPG - recebido {suffix!r}",
        )
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        school = await session.get(School, school_id)
        if school is None:
            raise HTTPException(status_code=403, detail="Escola nao encontrada.")

        tmp_dir = Path(tempfile.mkdtemp(prefix="r4_school_logo_"))
        try:
            tmp_path = tmp_dir / (Path(file.filename or "logo.png").name)
            size = 0
            with open(tmp_path, "wb") as out:
                while chunk := await file.read(1024 * 1024):
                    size += len(chunk)
                    if size > _MAX_UPLOAD_BYTES:
                        out.close()
                        raise HTTPException(
                            status_code=413, detail="Arquivo de logo passa de 25MB."
                        )
                    out.write(chunk)
            managed_path, _digest = MaterialStorage().store(tmp_path)
        finally:
            shutil.rmtree(tmp_dir, ignore_errors=True)

        school.logo_storage_uri = str(managed_path)
        # Resposta montada antes do commit (expire_on_commit=True em producao).
        response = SchoolLogoResponse(has_logo=True)
        await session.commit()
        return response
```

- [ ] **Step 5: Wire the school router**

Em `src/agente_ia_edu/api/app.py`, trocar o import de `essay_batches` por:

```python
from .routes.essay_batches import essay_batches_router, teacher_school_router
```

E, logo depois do `include_router(essay_batches_router, ...)`:

```python
    app.include_router(teacher_school_router, dependencies=reception_only_guard)
```

- [ ] **Step 6: Run test to verify it passes**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r4_essay_answer_sheet_route.py -q`
Expected: PASS (6 testes).

- [ ] **Step 7: Run the full suite**

Run: `/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests -q`
Expected: PASS. Atenção especial a `tests/test_r2_essay_prompts_routes.py` e `tests/test_r3_essay_prompt_dashboard_route.py`: a rota nova de folha entra no mesmo router, e um erro de ordem de registro apareceria ali.

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_prompts.py src/agente_ia_edu/api/routes/essay_batches.py src/agente_ia_edu/api/app.py tests/test_r4_essay_answer_sheet_route.py
git commit -m "feat(redacao): rota da folha de resposta em branco e upload da logo da escola

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

### Task 12: Frontend — aba "Lote" + botão "Gerar folha de resposta"

**Files:**
- Modify: `src/agente_ia_edu/web/essay-review.js` (aba nova + botão na tela de detalhe da proposta)

**Interfaces:**
- Consumes: `POST/GET /api/v1/teacher/essay-batches...`, `GET/POST /api/v1/teacher/school/logo`, `GET /api/v1/catalog/essay-prompts/{id}/answer-sheet.pdf` (Tasks 10-11); `GET /api/v1/teacher/classrooms?school_id=...&academic_year=2026` (já existente, é de onde a tela de atribuição já tira as turmas).
- Produces: nada consumido por outra tarefa — é a ponta da leva.

**Sem teste automatizado** (corte estabelecido nas levas anteriores de redação). Os passos de verificação manual abaixo fazem esse papel.

- [ ] **Step 1: Add the "Lote" tab button**

Em `src/agente_ia_edu/web/essay-review.js`, em `renderTabs`, acrescentar o botão entre "Propostas" e "Fila de Revisão":

```javascript
        <button class="btn ${activeTab === 'batch' ? 'btn-primary' : 'btn-secondary'}" type="button" data-tab="batch">Enviar em lote</button>
```

E em `wireTabs`, acrescentar a linha correspondente:

```javascript
        if (btn.dataset.tab === 'batch') renderBatchTab();
```

- [ ] **Step 2: Add the answer-sheet button to the proposal detail screen**

Em `renderPromptDetail`, dentro do template de `detailHtml.innerHTML`, logo depois de `<p class="empty-text">Status: ...</p>`, acrescentar:

```javascript
        <div class="tm-form-actions" style="margin: 8px 0;">
          <label for="er-sheet-copies" style="margin-right:6px;">Cópias</label>
          <input id="er-sheet-copies" class="text-input" type="number" min="1" max="60" value="30" style="width:80px;display:inline-block;">
          <button class="btn btn-secondary" type="button" id="er-answer-sheet-btn">Gerar folha de resposta</button>
          <input id="er-logo-file" type="file" accept="image/png,image/jpeg" hidden>
          <button class="btn btn-secondary" type="button" id="er-logo-btn">Enviar logo da escola</button>
          <span id="er-sheet-msg" class="tm-msg" hidden></span>
        </div>
```

E, junto dos outros `addEventListener` dessa função (logo depois de `container.querySelector('[data-back]').addEventListener('click', renderPromptsList);`), acrescentar:

```javascript
    const sheetMsg = container.querySelector('#er-sheet-msg');
    function showSheetMsg(text) {
      sheetMsg.hidden = false;
      sheetMsg.textContent = text;
    }

    container.querySelector('#er-answer-sheet-btn').addEventListener('click', async () => {
      const btn = container.querySelector('#er-answer-sheet-btn');
      const copies = Math.max(1, Math.min(60, Number(container.querySelector('#er-sheet-copies').value) || 1));
      btn.disabled = true;
      sheetMsg.hidden = true;
      try {
        // Aviso (nao bloqueio): sem logo a folha e gerada do mesmo jeito.
        const logoState = await reviewRequest('/api/v1/teacher/school/logo');
        if (!logoState.has_logo) showSheetMsg('A folha vai sair sem logo - envie a logo da escola se quiser que ela apareça.');
        const res = await fetch(
          `/api/v1/catalog/essay-prompts/${promptId}/answer-sheet.pdf?copies=${copies}`,
          { headers: reviewHeaders() },
        );
        if (!res.ok) throw new Error('Não foi possível gerar a folha de resposta.');
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `folha-de-redacao-${copies}-copias.pdf`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      } catch (e) {
        showSheetMsg(e.message);
      } finally {
        btn.disabled = false;
      }
    });

    container.querySelector('#er-logo-btn').addEventListener('click', () => {
      container.querySelector('#er-logo-file').click();
    });
    container.querySelector('#er-logo-file').addEventListener('change', async (ev) => {
      const file = ev.target.files[0];
      if (!file) return;
      const formData = new FormData();
      formData.append('file', file);
      try {
        await reviewRequest('/api/v1/teacher/school/logo', { method: 'POST', body: formData });
        showSheetMsg('Logo enviada. As próximas folhas geradas já saem com ela.');
      } catch (e) {
        showSheetMsg(e.message);
      }
    });
```

- [ ] **Step 3: Add the batch tab itself**

Acrescentar estas funções a `essay-review.js`, logo depois de `renderTrashTab` (elas usam `container`, `schoolId`, `reviewRequest`, `reviewHeaders` e `tmEsc`, todos já no escopo do módulo):

```javascript
  // Intervalo de consulta do progresso do lote. 2s: o processamento gasta
  // alguns segundos por pagina (OCR de cabecalho + corpo, com retentativa),
  // entao consultar mais rapido so geraria requisicao a toa.
  const BATCH_POLL_MS = 2000;
  let batchPollTimer = null;

  function stopBatchPolling() {
    if (batchPollTimer) {
      clearTimeout(batchPollTimer);
      batchPollTimer = null;
    }
  }

  async function renderBatchTab() {
    stopBatchPolling();
    container.innerHTML = `${renderTabs('batch')}<p class="empty-text">Carregando...</p>`;
    wireTabs();

    let promptOptions = [];
    let classrooms = [];
    try {
      promptOptions = await reviewRequest('/api/v1/catalog/essay-prompts');
    } catch (e) {
      promptOptions = [];
    }
    try {
      classrooms = await reviewRequest(
        `/api/v1/teacher/classrooms?school_id=${encodeURIComponent(schoolId)}&academic_year=2026`,
      );
    } catch (e) {
      classrooms = [];
    }
    // Mesmo filtro da tela de atribuicao: so turmas que ja resolvem para uma
    // Class real podem receber um lote (o lote guarda class_id como FK).
    const assignableClassrooms = classrooms.filter((c) => c.class_id);

    const tabsEl = container.querySelector('.essay-review-tabs');
    if (tabsEl.nextElementSibling) tabsEl.nextElementSibling.remove();
    tabsEl.insertAdjacentHTML('afterend', `
      <div class="card tm-form">
        <h3>Enviar redações em lote</h3>
        <p class="empty-text">Suba as fotos ou o PDF escaneado das folhas de uma turma inteira. O sistema identifica cada aluno pelo nome escrito no cabeçalho da folha, junta as páginas seguidas de um mesmo aluno em uma redação só e manda direto para correção. As folhas que ele não conseguir identificar ficam numa lista aqui embaixo para você escolher o aluno. Máximo de 60 páginas por envio.</p>
        <form id="er-batch-form">
          <div class="form-group">
            <label for="er-batch-prompt">Proposta</label>
            <select id="er-batch-prompt" class="text-input" required>
              ${promptOptions.map((p) => `<option value="${tmEsc(p.id)}">${tmEsc(p.title)} (${p.year})</option>`).join('')}
            </select>
          </div>
          <div class="form-group">
            <label for="er-batch-class">Turma</label>
            <select id="er-batch-class" class="text-input" required>
              ${assignableClassrooms.map((c) => `<option value="${tmEsc(c.class_id)}">${tmEsc(c.name)}</option>`).join('')}
            </select>
          </div>
          <div class="form-group">
            <label for="er-batch-files">Arquivos (fotos JPG/PNG e/ou PDF)</label>
            <input id="er-batch-files" class="text-input" type="file" accept=".png,.jpg,.jpeg,.pdf" multiple required>
          </div>
          <button class="btn btn-primary" type="submit">Enviar lote</button>
          <p id="er-batch-msg" class="tm-msg" hidden></p>
        </form>
      </div>
      <div id="er-batch-progress"></div>`);

    container.querySelector('#er-batch-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const msg = container.querySelector('#er-batch-msg');
      const submitBtn = ev.target.querySelector('button[type="submit"]');
      const files = container.querySelector('#er-batch-files').files;
      msg.hidden = true;
      if (!files.length) return;
      const formData = new FormData();
      formData.append('essay_prompt_id', container.querySelector('#er-batch-prompt').value);
      formData.append('class_id', container.querySelector('#er-batch-class').value);
      Array.from(files).forEach((file) => formData.append('files', file));
      submitBtn.disabled = true;
      try {
        const batch = await reviewRequest('/api/v1/teacher/essay-batches', {
          method: 'POST', body: formData,
        });
        pollBatch(batch.id);
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
      } finally {
        submitBtn.disabled = false;
      }
    });
  }

  async function pollBatch(batchId) {
    stopBatchPolling();
    let data;
    try {
      data = await reviewRequest(`/api/v1/teacher/essay-batches/${batchId}`);
    } catch (e) {
      const target = container.querySelector('#er-batch-progress');
      if (target) target.innerHTML = `<p class="empty-text">${tmEsc(e.message)}</p>`;
      return;
    }
    renderBatchProgress(data);
    if (data.status === 'PROCESSING') {
      batchPollTimer = setTimeout(() => pollBatch(batchId), BATCH_POLL_MS);
    }
  }

  function renderBatchProgress(data) {
    const target = container.querySelector('#er-batch-progress');
    if (!target) return;
    const processing = data.status === 'PROCESSING';
    const done = data.matched_count + data.needs_review_count;
    const studentOptions = data.available_students
      .map((s) => `<option value="${tmEsc(s.student_id)}">${tmEsc(s.full_name)}${s.document_number ? ` — CPF ${tmEsc(s.document_number)}` : ''}</option>`)
      .join('');

    target.innerHTML = `
      <div class="card">
        <h4>${processing ? 'Processando o lote...' : 'Lote processado'}</h4>
        <p class="empty-text">${done} de ${data.total_pages} páginas lidas — ${data.matched_count} identificadas, ${data.needs_review_count} aguardando você.</p>
        ${data.needs_review_pages.length ? `
        <h4>Folhas que o sistema não conseguiu identificar</h4>
        <div class="tm-table-wrap" style="overflow-x:auto;">
          <table class="tm-table">
            <thead><tr><th>Página</th><th>Folha</th><th>Nome lido</th><th>CPF lido</th><th>Aluno</th><th></th></tr></thead>
            <tbody>
              ${data.needs_review_pages.map((page) => `
                <tr data-batch-page="${tmEsc(page.id)}">
                  <td>${page.page_number}</td>
                  <td><a href="/api/v1/teacher/essay-batches/${tmEsc(data.id)}/pages/${tmEsc(page.id)}/image" target="_blank" rel="noopener" data-page-image="${tmEsc(page.id)}">ver folha</a></td>
                  <td>${tmEsc(page.ocr_name_raw || '—')}</td>
                  <td>${tmEsc(page.ocr_cpf_raw || '—')}</td>
                  <td>
                    <select class="text-input" data-student-select="${tmEsc(page.id)}" ${page.has_text ? '' : 'disabled'}>
                      <option value="">Selecione...</option>${studentOptions}
                    </select>
                  </td>
                  <td>
                    ${page.has_text
                      ? `<button class="btn btn-primary" type="button" data-resolve-page="${tmEsc(page.id)}">Confirmar</button>`
                      : '<span class="empty-text">Sem texto legível — reenvie esta folha em outro lote.</span>'}
                  </td>
                </tr>`).join('')}
            </tbody>
          </table>
        </div>` : (processing ? '' : '<p class="empty-text">Todas as folhas foram identificadas automaticamente.</p>')}
      </div>`;

    // A imagem da folha e servida por uma rota autenticada, entao um <a href>
    // simples abriria sem o cabecalho Authorization - buscamos o blob e abrimos
    // a URL de objeto, mesmo caminho que o export de XLSX ja usa.
    target.querySelectorAll('[data-page-image]').forEach((link) => {
      link.addEventListener('click', async (ev) => {
        ev.preventDefault();
        try {
          const res = await fetch(link.getAttribute('href'), { headers: reviewHeaders() });
          if (!res.ok) throw new Error('Não foi possível abrir a imagem da folha.');
          const url = URL.createObjectURL(await res.blob());
          window.open(url, '_blank', 'noopener');
        } catch (e) {
          alert(e.message);
        }
      });
    });

    target.querySelectorAll('[data-resolve-page]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const pageId = btn.dataset.resolvePage;
        const select = target.querySelector(`[data-student-select="${pageId}"]`);
        if (!select.value) {
          alert('Escolha o aluno desta folha.');
          return;
        }
        btn.disabled = true;
        try {
          const updated = await reviewRequest(
            `/api/v1/teacher/essay-batches/${data.id}/pages/${pageId}/resolve`,
            {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ student_id: select.value }),
            },
          );
          renderBatchProgress(updated);
        } catch (e) {
          alert(e.message);
          btn.disabled = false;
        }
      });
    });
  }
```

- [ ] **Step 4: Stop the poll timer when leaving the tab**

Em `wireTabs`, no começo do handler de clique (antes dos `if`), acrescentar:

```javascript
        stopBatchPolling();
```

Sem isso, sair da aba "Enviar em lote" com um lote ainda processando deixa um `setTimeout` rodando e escrevendo num `#er-batch-progress` que não existe mais.

- [ ] **Step 5: Manual verification**

Subir a API (`/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m uvicorn agente_ia_edu.api.app:app --reload`), abrir `/teacher`, módulo Redação, e conferir:

1. A aba "Enviar em lote" aparece entre "Propostas" e "Fila de Revisão", e clicar nela carrega o formulário com as propostas e as turmas preenchidas.
2. Em "Propostas" → abrir uma proposta → "Gerar folha de resposta" baixa um PDF com o número de cópias pedido; sem logo cadastrada aparece o aviso, e o PDF vem mesmo assim.
3. "Enviar logo da escola" com um PNG → mensagem de sucesso; gerar a folha de novo agora traz a logo no canto superior esquerdo.
4. Imprimir uma folha, preencher nome e CPF de um aluno real da turma, fotografar, e subir pela aba de lote: a tela de progresso mostra "0 de 1", depois "1 de 1 — 1 identificada", e a redação aparece na Fila de Revisão com o aluno certo.
5. Subir uma folha com um nome que não existe na turma: a linha aparece na tabela de folhas não identificadas com o nome/CPF lidos, "ver folha" abre a imagem, e escolher um aluno + "Confirmar" faz a linha sumir e o contador de identificadas subir.
6. Trocar de aba durante um processamento e voltar: nenhum erro no console (o timer foi parado).

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/web/essay-review.js
git commit -m "feat(redacao): aba de envio em lote e geracao da folha de resposta no portal do professor

Co-Authored-By: Claude Opus 5 (1M context) <noreply@anthropic.com>"
```

---

## Verificação final da leva

- [ ] **Rodar a suíte completa, com o Postgres de teste de pé**

```bash
docker compose up -d
/Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests -q
```
Expected: PASS, e `tests/test_r4_essay_batch_migration_postgresql.py` **executado**, não `skipped`.

- [ ] **Conferir que nada dos fluxos existentes mudou**

Nenhum teste de submissão individual, de correção ou de proposta foi alterado por esta leva (spec §8). Se algum deles precisou mudar, é sinal de que o lote encostou no pipeline de correção — o que esta leva explicitamente não deve fazer. Investigar antes de seguir.
