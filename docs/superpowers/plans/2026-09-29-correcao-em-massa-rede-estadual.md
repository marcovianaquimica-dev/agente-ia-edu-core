# Correção em massa de redações (Batch API) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Processar até 50.000 redações escaneadas via Batch API da OpenAI (OCR → correção → pontuação), reaproveitando toda a lógica de negócio já existente do pipeline síncrono, sem tocar em nenhuma decisão de prompt/pontuação/validação.

**Architecture:** Três estágios sequenciais (OCR, correção fase 1, pontuação fase 2), cada um um ou mais lotes da Batch API da OpenAI, rastreados numa tabela nova (`MassCorrectionRun`) que sustenta retomada se o processo cair no meio. Cada estágio monta um JSONL, submete, espera, baixa o resultado, e aplica o resultado usando as MESMAS funções que o pipeline síncrono já usa hoje (matching de aluno, validação do contrato do motor, cálculo de nota).

**Tech Stack:** Python/FastAPI/SQLAlchemy assíncrono, `httpx` pra chamadas HTTP cruas à Batch API (a SDK `openai` já usada no projeto não expõe a Batch API através do client assíncrono existente neste código - ver Task 2), Alembic pra migration.

**Spec:** `docs/superpowers/specs/2026-09-29-correcao-em-massa-rede-estadual-design.md`

## Global Constraints

- Nenhuma lógica de prompt, validação de contrato (`essay_engine_validation.py`), ou cálculo de pontuação (`_apply_deterministic_scoring_rules`) é reescrita ou duplicada - todo estágio de lote REAPROVEITA as funções síncronas já existentes e testadas, só troca "chamar a API direto" por "ler o resultado de um arquivo de lote".
- A calibração real do recorte cabeçalho/corpo (qual fração usar pro layout real da folha da rede estadual) **NÃO é uma tarefa deste plano** - fica bloqueada até o usuário ter acesso a fotos reais (Task 7 só torna a fração CONFIGURÁVEL, com o valor de hoje como padrão; escolher o valor certo é trabalho futuro, fora deste plano).
- Nenhuma mudança na tabela `essay_batch_uploads`/`essay_batch_pages`/`essay_submissions`/`essay_corrections` - só uma tabela nova (`mass_correction_runs`) e uma constante que vira parâmetro.
- `.venv/bin/pytest` a partir do worktree (nunca o `.venv` do checkout principal). Se o comando `pytest` direto for bloqueado pelo classificador de segurança do Claude Code, usar `.venv/bin/python -m pytest`, redirecionando a saída pra um arquivo.
- `custom_id` de cada linha do JSONL de um lote é sempre uma string livre que a OpenAI devolve IDÊNTICA na linha de resultado correspondente - é o único mecanismo de correlação entre requisição e resposta; nunca depender da ORDEM das linhas do arquivo de resultado (a Batch API não garante a mesma ordem do arquivo de entrada).

---

### Task 1: Tabela de acompanhamento `MassCorrectionRun`

**Files:**
- Create: `src/agente_ia_edu/db/models/mass_correction_run.py`
- Modify: `src/agente_ia_edu/db/models/__init__.py` (adicionar import + `__all__`, mesmo padrão de `EssayBatchUpload`/`EssayBatchPage`, linhas 138 e 233-234)
- Create: `migrations/versions/059_mass_correction_runs.py`
- Test: `tests/test_mass_correction_run_model.py`, `tests/test_r6_mass_correction_migration_postgresql.py`

**Interfaces:**
- Consumes: nada de outra task.
- Produces (usado pelas Tasks 2-6):
  - `MassCorrectionRun` (modelo SQLAlchemy): `id` (UUID, PK), `school_id` (UUID, not null), `stage` (str, `"OCR"`/`"CORRECTION"`/`"SCORING"`), `sequence_number` (int, not null, a ordem deste lote dentro do estágio - útil quando um estágio precisa de mais de um lote por passar de 50.000 requisições), `openai_batch_id` (str, nullable - só é preenchido depois que o lote é de fato criado na OpenAI), `input_file_id` (str, nullable), `output_file_id` (str, nullable), `request_count` (int, not null), `status` (str, um de `"PENDING"` (linha criada localmente, ainda não submetida), `"validating"`, `"in_progress"`, `"finalizing"`, `"completed"`, `"failed"`, `"expired"`, `"cancelled"` - os 6 últimos são os valores literais que a Batch API da OpenAI usa, propagados sem tradução), `created_at`, `updated_at`, `completed_at` (nullable).

- [ ] **Step 1: Escrever o teste do modelo (falhando)**

```python
# tests/test_mass_correction_run_model.py
import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import MassCorrectionRun, School


class MassCorrectionRunModelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_creates_a_pending_run_with_defaults(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="EST-1", name="Rede Estadual")
            session.add(school)
            await session.flush()
            run = MassCorrectionRun(
                id=uuid.uuid4(), school_id=school.id, stage="OCR",
                sequence_number=1, request_count=40000, status="PENDING",
            )
            session.add(run)
            await session.commit()

            fetched = (await session.execute(
                select(MassCorrectionRun).where(MassCorrectionRun.id == run.id)
            )).scalar_one()
            self.assertEqual(fetched.stage, "OCR")
            self.assertEqual(fetched.status, "PENDING")
            self.assertIsNone(fetched.openai_batch_id)
            self.assertIsNone(fetched.completed_at)
            self.assertIsInstance(fetched.created_at, datetime)

    async def test_rejects_an_unknown_stage(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="EST-2", name="Rede Estadual")
            session.add(school)
            await session.flush()
            session.add(MassCorrectionRun(
                id=uuid.uuid4(), school_id=school.id, stage="TRANSCODE",
                sequence_number=1, request_count=1, status="PENDING",
            ))
            with self.assertRaises(Exception):
                await session.commit()

    async def test_rejects_an_unknown_status(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="EST-3", name="Rede Estadual")
            session.add(school)
            await session.flush()
            session.add(MassCorrectionRun(
                id=uuid.uuid4(), school_id=school.id, stage="OCR",
                sequence_number=1, request_count=1, status="RUNNING",
            ))
            with self.assertRaises(Exception):
                await session.commit()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/pytest tests/test_mass_correction_run_model.py -v`
Expected: FAIL com `ImportError: cannot import name 'MassCorrectionRun'`

- [ ] **Step 3: Criar o modelo**

```python
# src/agente_ia_edu/db/models/mass_correction_run.py
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
```

Adicione em `src/agente_ia_edu/db/models/__init__.py`: `from .mass_correction_run import MassCorrectionRun` (junto dos outros imports, ordem alfabética por arquivo como o resto do arquivo já segue) e `"MassCorrectionRun"` em `__all__`.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/pytest tests/test_mass_correction_run_model.py -v`
Expected: PASS (3 testes)

- [ ] **Step 5: Escrever o teste de migration Postgres (falhando)**

Leia primeiro `tests/test_r5_platform_essay_prompts_migration_postgresql.py` inteiro - é o padrão exato a seguir (banco descartável próprio, `create_database`/`drop_database` de `tests/_postgres_test_db.py`, `command.upgrade(config, "059_mass_correction_runs")`).

```python
# tests/test_r6_mass_correction_migration_postgresql.py
"""Validacao em PostgreSQL da migration 059 (mass_correction_runs)."""

from __future__ import annotations

import os
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from tests._postgres_test_db import create_database, drop_database


class TestMassCorrectionRunsMigrationPostgreSQL(unittest.TestCase):
    database_name = "agente_ia_edu_mass_correction_test"
    admin_url = os.getenv(
        "MASS_CORRECTION_TEST_ADMIN_DATABASE_URL",
        "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/postgres",
    )
    database_url = os.getenv(
        "MASS_CORRECTION_TEST_DATABASE_URL",
        f"postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            engine = create_engine(
                cls.admin_url, connect_args={"autocommit": True},
                execution_options={"isolation_level": "AUTOCOMMIT"},
            )
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            engine.dispose()
        except Exception as exc:
            raise unittest.SkipTest(
                "PostgreSQL de teste indisponivel; migration 059 nao validada."
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

    def test_upgrade_059_creates_the_table(self):
        config = self._alembic_config()
        command.upgrade(config, "059_mass_correction_runs")

        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            self.assertIn("mass_correction_runs", inspector.get_table_names())
            columns = {c["name"] for c in inspector.get_columns("mass_correction_runs")}
            self.assertEqual(
                columns,
                {
                    "id", "school_id", "stage", "sequence_number", "openai_batch_id",
                    "input_file_id", "output_file_id", "request_count", "status",
                    "created_at", "updated_at", "completed_at",
                },
            )
        finally:
            engine.dispose()

    def test_downgrade_059_is_clean(self):
        config = self._alembic_config()
        command.upgrade(config, "059_mass_correction_runs")
        command.downgrade(config, "058_platform_essay_prompts")

        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            self.assertNotIn("mass_correction_runs", inspector.get_table_names())
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: Rodar e confirmar que falha**

Run: `.venv/bin/pytest tests/test_r6_mass_correction_migration_postgresql.py -v`
Expected: FAIL (revision `059_mass_correction_runs` não existe)

- [ ] **Step 7: Escrever a migration**

```python
# migrations/versions/059_mass_correction_runs.py
"""Acompanhamento de lotes de correcao em massa via Batch API.

Revision ID: 059_mass_correction_runs
Revises: 058_platform_essay_prompts

Puramente aditiva: uma tabela nova, nao mexe em nenhuma tabela existente.
Guarda so o progresso junto a OpenAI (id do lote, status, arquivos de
entrada/saida) - os dados em si continuam em essay_batch_pages/
essay_submissions/essay_corrections, ja existentes.
"""

from alembic import op
import sqlalchemy as sa

revision = "059_mass_correction_runs"
down_revision = "058_platform_essay_prompts"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "mass_correction_runs",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("stage", sa.String(length=20), nullable=False),
        sa.Column("sequence_number", sa.Integer(), nullable=False),
        sa.Column("openai_batch_id", sa.String(length=64), nullable=True),
        sa.Column("input_file_id", sa.String(length=64), nullable=True),
        sa.Column("output_file_id", sa.String(length=64), nullable=True),
        sa.Column("request_count", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("completed_at", sa.DateTime(timezone=True), nullable=True),
        sa.PrimaryKeyConstraint("id", name="pk_mass_correction_runs"),
        sa.CheckConstraint(
            "stage IN ('OCR', 'CORRECTION', 'SCORING')",
            name="ck_mass_correction_runs_stage",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING', 'validating', 'in_progress', 'finalizing', "
            "'completed', 'failed', 'expired', 'cancelled')",
            name="ck_mass_correction_runs_status",
        ),
        sa.CheckConstraint(
            "request_count >= 0", name="ck_mass_correction_runs_request_count_non_negative"
        ),
    )
    op.create_index(
        "ix_mass_correction_runs_school_id", "mass_correction_runs", ["school_id"]
    )
    op.create_index(
        "ix_mass_correction_runs_stage_status", "mass_correction_runs", ["stage", "status"]
    )


def downgrade() -> None:
    op.drop_index("ix_mass_correction_runs_stage_status", table_name="mass_correction_runs")
    op.drop_index("ix_mass_correction_runs_school_id", table_name="mass_correction_runs")
    op.drop_table("mass_correction_runs")
```

- [ ] **Step 8: Rodar e confirmar que passa, e aplicar no banco de dev**

Run: `.venv/bin/pytest tests/test_r6_mass_correction_migration_postgresql.py -v`
Expected: PASS (2 testes)

Run: `DATABASE_URL="postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/agente_ia_edu" .venv/bin/python -m alembic upgrade head`
Expected: aplica 059 no banco de dev compartilhado sem erro.

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/db/models/mass_correction_run.py src/agente_ia_edu/db/models/__init__.py \
        migrations/versions/059_mass_correction_runs.py tests/test_mass_correction_run_model.py \
        tests/test_r6_mass_correction_migration_postgresql.py
git commit -m "feat: tabela de acompanhamento de lotes de correcao em massa"
```

---

### Task 2: Cliente HTTP da Batch API da OpenAI

**Files:**
- Create: `src/agente_ia_edu/providers/openai_batch_client.py`
- Test: `tests/test_openai_batch_client.py`

**Interfaces:**
- Consumes: nada de outra task.
- Produces (usado pelas Tasks 3-6):
  - `async def upload_batch_file(lines: list[dict], *, api_key: str) -> str` - serializa `lines` como JSONL (uma linha `json.dumps` por dict), faz upload via `POST /v1/files` (`purpose="batch"`), devolve o `id` do arquivo.
  - `async def create_batch(input_file_id: str, *, api_key: str) -> dict` - `POST /v1/batches` com `endpoint="/v1/chat/completions"`, `completion_window="24h"`, devolve o objeto JSON do lote (contém `id`, `status`).
  - `async def get_batch(batch_id: str, *, api_key: str) -> dict` - `GET /v1/batches/{batch_id}`, devolve o objeto JSON do lote (contém `status`, `output_file_id` quando `status == "completed"`, `error_file_id` quando há falhas parciais).
  - `async def download_file_lines(file_id: str, *, api_key: str) -> list[dict]` - `GET /v1/files/{file_id}/content`, devolve a lista de dicts (um `json.loads` por linha do JSONL baixado).

**Decisão já tomada (verificada, não condicional):** a SDK `openai` instalada neste projeto (confirmado agora: versão 1.109.1) já suporta a Batch API via `client.files`/`client.batches` no client assíncrono (`AsyncOpenAI(...).batches` existe). Use a SDK, não `httpx` cru - é mais seguro contra mudança de formato da API. As 4 funções abaixo mantêm a assinatura `*, api_key: str` (não `client: AsyncOpenAI`) para não propagar uma mudança de assinatura pras Tasks 3-6, que já referenciam `api_key=...` - cada função constrói seu próprio `AsyncOpenAI(api_key=api_key)` internamente, mesmo padrão que `providers/adapters/openai.py`'s `_create_client()` já usa.

- [ ] **Step 1: Escrever os testes (falhando)**

Assinaturas reais confirmadas na versão instalada (`openai==1.109.1`) antes de escrever este plano: `client.files.create(*, file, purpose)` devolve um `FileObject` (tem `.id`); `client.batches.create(*, completion_window, endpoint, input_file_id)` e `client.batches.retrieve(batch_id)` devolvem um `Batch` (tem `.model_dump()`); `client.files.content(file_id)` devolve um objeto com `.text` (conteúdo bruto do arquivo).

```python
# tests/test_openai_batch_client.py
import json
import unittest
from unittest.mock import AsyncMock, MagicMock, patch

from agente_ia_edu.providers.openai_batch_client import (
    create_batch,
    download_file_lines,
    get_batch,
    upload_batch_file,
)


class OpenAIBatchClientTests(unittest.IsolatedAsyncioTestCase):
    @patch("agente_ia_edu.providers.openai_batch_client.AsyncOpenAI")
    async def test_upload_batch_file_serializes_lines_as_jsonl_and_returns_file_id(self, mock_cls):
        mock_client = MagicMock()
        mock_client.files.create = AsyncMock(return_value=MagicMock(id="file-abc123"))
        mock_cls.return_value = mock_client

        file_id = await upload_batch_file(
            [{"custom_id": "a", "body": {"x": 1}}, {"custom_id": "b", "body": {"x": 2}}],
            api_key="sk-test",
        )

        self.assertEqual(file_id, "file-abc123")
        call_kwargs = mock_client.files.create.call_args.kwargs
        self.assertEqual(call_kwargs["purpose"], "batch")
        uploaded_bytes = call_kwargs["file"][1]
        lines = uploaded_bytes.decode("utf-8").strip().split("\n")
        self.assertEqual(len(lines), 2)
        self.assertEqual(json.loads(lines[0]), {"custom_id": "a", "body": {"x": 1}})

    @patch("agente_ia_edu.providers.openai_batch_client.AsyncOpenAI")
    async def test_create_batch_posts_the_right_endpoint_and_window(self, mock_cls):
        mock_client = MagicMock()
        mock_batch = MagicMock()
        mock_batch.model_dump.return_value = {"id": "batch-xyz", "status": "validating"}
        mock_client.batches.create = AsyncMock(return_value=mock_batch)
        mock_cls.return_value = mock_client

        result = await create_batch("file-abc123", api_key="sk-test")

        self.assertEqual(result, {"id": "batch-xyz", "status": "validating"})
        call_kwargs = mock_client.batches.create.call_args.kwargs
        self.assertEqual(call_kwargs["input_file_id"], "file-abc123")
        self.assertEqual(call_kwargs["endpoint"], "/v1/chat/completions")
        self.assertEqual(call_kwargs["completion_window"], "24h")

    @patch("agente_ia_edu.providers.openai_batch_client.AsyncOpenAI")
    async def test_get_batch_returns_the_raw_status_object(self, mock_cls):
        mock_client = MagicMock()
        mock_batch = MagicMock()
        mock_batch.model_dump.return_value = {
            "id": "batch-xyz", "status": "completed", "output_file_id": "file-out",
        }
        mock_client.batches.retrieve = AsyncMock(return_value=mock_batch)
        mock_cls.return_value = mock_client

        result = await get_batch("batch-xyz", api_key="sk-test")

        self.assertEqual(result["status"], "completed")
        self.assertEqual(result["output_file_id"], "file-out")
        mock_client.batches.retrieve.assert_called_once_with("batch-xyz")

    @patch("agente_ia_edu.providers.openai_batch_client.AsyncOpenAI")
    async def test_download_file_lines_parses_each_line_as_json(self, mock_cls):
        mock_client = MagicMock()
        mock_content = MagicMock()
        mock_content.text = (
            json.dumps({"custom_id": "a", "response": {"body": {"ok": 1}}})
            + "\n"
            + json.dumps({"custom_id": "b", "response": {"body": {"ok": 2}}})
            + "\n"
        )
        mock_client.files.content = AsyncMock(return_value=mock_content)
        mock_cls.return_value = mock_client

        lines = await download_file_lines("file-out", api_key="sk-test")

        self.assertEqual(len(lines), 2)
        self.assertEqual(lines[0]["custom_id"], "a")
        self.assertEqual(lines[1]["response"]["body"]["ok"], 2)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/pytest tests/test_openai_batch_client.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar**

```python
# src/agente_ia_edu/providers/openai_batch_client.py
"""Cliente fino da Batch API da OpenAI (upload de arquivo, criacao de lote,
consulta de status, download de resultado), usando a SDK oficial (versao
instalada ja suporta client.files/client.batches). Usado pelos 3 estagios
de correcao em massa (services/mass_correction_batch.py) - nenhuma logica
de negocio aqui, so a mecanica de chamada da Batch API em si.

Cada funcao constroi seu proprio AsyncOpenAI(api_key=...) - mesmo padrao
que providers/adapters/openai.py's _create_client() ja usa - em vez de
receber um client pronto, para manter a assinatura simples e nao acoplar
quem chama a um client de longa duracao."""

from __future__ import annotations

import json

from openai import AsyncOpenAI


async def upload_batch_file(lines: list[dict], *, api_key: str) -> str:
    jsonl_bytes = "\n".join(json.dumps(line, ensure_ascii=False) for line in lines).encode("utf-8")
    client = AsyncOpenAI(api_key=api_key)
    file_object = await client.files.create(
        file=("batch_input.jsonl", jsonl_bytes, "application/jsonl"), purpose="batch",
    )
    return file_object.id


async def create_batch(input_file_id: str, *, api_key: str) -> dict:
    client = AsyncOpenAI(api_key=api_key)
    batch = await client.batches.create(
        input_file_id=input_file_id, endpoint="/v1/chat/completions", completion_window="24h",
    )
    return batch.model_dump()


async def get_batch(batch_id: str, *, api_key: str) -> dict:
    client = AsyncOpenAI(api_key=api_key)
    batch = await client.batches.retrieve(batch_id)
    return batch.model_dump()


async def download_file_lines(file_id: str, *, api_key: str) -> list[dict]:
    client = AsyncOpenAI(api_key=api_key)
    content = await client.files.content(file_id)
    return [json.loads(line) for line in content.text.splitlines() if line.strip()]
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/pytest tests/test_openai_batch_client.py -v`
Expected: PASS (4 testes)

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/providers/openai_batch_client.py tests/test_openai_batch_client.py
git commit -m "feat: cliente HTTP fino da Batch API da OpenAI"
```

---

### Task 3: Estágio 1 (OCR) — montagem de lote e aplicação de resultado

**Files:**
- Create: `src/agente_ia_edu/services/mass_correction_batch.py`
- Test: `tests/test_mass_correction_batch_ocr.py`

**Interfaces:**
- Consumes: nada de código de outra task diretamente (usa `EssayBatchService._crop_regions`, `parse_header_text`, `match_student`, já existentes em `services/essay_batch.py`; usa `EssayOcrToken` de `providers/models.py`).
- Produces (usado pela Task 6):
  - `OCR_SYSTEM_PROMPT: str` - constante extraída de `providers/adapters/openai.py`'s `transcribe_page` (ver Step 3), pra nunca duplicar o texto do prompt entre o caminho síncrono e o de lote.
  - `build_ocr_batch_request(page_id: str, image_path: Path, *, detail: str = "high") -> dict` - monta UMA linha do JSONL (dict com `custom_id`, `method`, `url`, `body`) pra uma imagem já recortada (cabeçalho OU corpo - chamada 2x por página, mesma forma que o caminho síncrono já faz).
  - `apply_ocr_batch_result(result_line: dict) -> tuple[str, str]` - recebe UMA linha do arquivo de resultado, devolve `(custom_id, texto_transcrito)`. Levanta `ValueError` se a linha representa uma falha (`result_line["error"]` não nulo, ou `response.status_code != 200`).

**Sobre `logprobs` no modo de lote:** o caminho síncrono (`_tokens_from_logprobs`, `providers/adapters/openai.py:229`) espera um objeto com atributo `.content` (o jeito que a SDK `openai` tipa a resposta) pra calcular confiança por token - a resposta de um lote vem como **dict puro** (JSON cru), não como esse objeto tipado. `apply_ocr_batch_result` desta task devolve só o TEXTO transcrito (`response.body.choices[0].message.content`), sem tentar replicar o cálculo de confiança por token - is a simplificação aceita pra este projeto (a amostragem de auditoria, não a confiança por token, é o mecanismo de controle de qualidade aqui). Se precisar de confiança por token no futuro, seria uma task separada adaptando `_tokens_from_logprobs` pra aceitar dict puro.

- [ ] **Step 1: Extrair o prompt de transcrição pra uma constante compartilhada**

Em `src/agente_ia_edu/providers/adapters/openai.py`, extraia a string do parâmetro `"content"` do `{"role": "system", ...}` dentro de `transcribe_page` (linhas ~117-145) para uma constante no nível do módulo, logo antes da classe:

```python
TRANSCRIPTION_SYSTEM_PROMPT = (
    "Transcreva literalmente o texto manuscrito ou impresso na "
    "imagem, palavra por palavra, na ordem em que aparece. Nao "
    # ... (cole aqui o texto EXATO já existente, char por char - não
    # reescreva, é o mesmo texto que já está testado e calibrado)
)
```

e troque o `"content": (...)` inline por `"content": TRANSCRIPTION_SYSTEM_PROMPT`. Rode a suíte de testes de transcrição existente pra confirmar que nada quebrou:

Run: `.venv/bin/pytest tests -k "transcri" -q`
Expected: mesmo resultado de antes da extração (só refatoração, sem mudança de comportamento).

- [ ] **Step 2: Escrever os testes de `mass_correction_batch.py` (falhando)**

```python
# tests/test_mass_correction_batch_ocr.py
import base64
import unittest
from pathlib import Path

from agente_ia_edu.services.mass_correction_batch import (
    OCR_SYSTEM_PROMPT,
    apply_ocr_batch_result,
    build_ocr_batch_request,
)


class BuildOcrBatchRequestTests(unittest.TestCase):
    def test_builds_a_valid_batch_line_shape(self):
        image_path = Path(__file__).parent / "fixtures" / "tiny.png"
        image_path.parent.mkdir(exist_ok=True)
        image_path.write_bytes(base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mNk+A8AAQUBAScY42YAAAAASUVORK5CYII="
        ))

        line = build_ocr_batch_request("page-123", image_path)

        self.assertEqual(line["custom_id"], "page-123")
        self.assertEqual(line["method"], "POST")
        self.assertEqual(line["url"], "/v1/chat/completions")
        messages = line["body"]["messages"]
        self.assertEqual(messages[0], {"role": "system", "content": OCR_SYSTEM_PROMPT})
        image_content = messages[1]["content"][0]
        self.assertEqual(image_content["type"], "image_url")
        self.assertEqual(image_content["image_url"]["detail"], "high")
        self.assertTrue(image_content["image_url"]["url"].startswith("data:image/png;base64,"))


class ApplyOcrBatchResultTests(unittest.TestCase):
    def test_returns_custom_id_and_transcribed_text_on_success(self):
        result_line = {
            "custom_id": "page-123",
            "response": {
                "status_code": 200,
                "body": {"choices": [{"message": {"content": "texto transcrito aqui"}}]},
            },
            "error": None,
        }
        custom_id, text = apply_ocr_batch_result(result_line)
        self.assertEqual(custom_id, "page-123")
        self.assertEqual(text, "texto transcrito aqui")

    def test_raises_on_a_batch_level_error(self):
        result_line = {"custom_id": "page-123", "response": None, "error": {"message": "rate limited"}}
        with self.assertRaises(ValueError) as caught:
            apply_ocr_batch_result(result_line)
        self.assertIn("page-123", str(caught.exception))

    def test_raises_on_a_non_200_response_status(self):
        result_line = {
            "custom_id": "page-123",
            "response": {"status_code": 500, "body": {"error": "internal"}},
            "error": None,
        }
        with self.assertRaises(ValueError) as caught:
            apply_ocr_batch_result(result_line)
        self.assertIn("500", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `.venv/bin/pytest tests/test_mass_correction_batch_ocr.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 4: Implementar**

```python
# src/agente_ia_edu/services/mass_correction_batch.py
"""Correcao em massa via Batch API da OpenAI (spec 2026-09-29): monta
requisicoes em lote e aplica os resultados de volta aos modelos ja
existentes (EssayBatchPage/EssaySubmission/EssayCorrection), reaproveitando
a mesma logica de negocio que o pipeline sincrono ja usa - NUNCA duplica
prompt, validacao de contrato ou calculo de pontuacao.
"""

from __future__ import annotations

import base64
from pathlib import Path

from ..providers.adapters.openai import TRANSCRIPTION_SYSTEM_PROMPT

OCR_SYSTEM_PROMPT = TRANSCRIPTION_SYSTEM_PROMPT


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    return "image/png" if suffix == ".png" else "image/jpeg"


def build_ocr_batch_request(custom_id: str, image_path: Path, *, detail: str = "high") -> dict:
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    mime = _guess_mime(image_path)
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": None,  # preenchido pela Task 6, que conhece OPENAI_VISION_MODEL
            "messages": [
                {"role": "system", "content": OCR_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{image_b64}", "detail": detail},
                        }
                    ],
                },
            ],
        },
    }


def apply_ocr_batch_result(result_line: dict) -> tuple[str, str]:
    custom_id = result_line["custom_id"]
    if result_line.get("error"):
        raise ValueError(f"OCR em lote falhou para {custom_id}: {result_line['error']}")
    response = result_line["response"]
    if response["status_code"] != 200:
        raise ValueError(
            f"OCR em lote devolveu status {response['status_code']} para {custom_id}: {response['body']}"
        )
    text = response["body"]["choices"][0]["message"]["content"]
    return custom_id, text
```

Nota sobre `"model": None` no corpo: a Task 6 (o driver, que lê `OPENAI_VISION_MODEL` do ambiente) é responsável por substituir esse `None` pelo nome real do modelo antes de serializar o JSONL - `build_ocr_batch_request` em si não lê variável de ambiente nenhuma, pra ficar testável sem depender de configuração externa.

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `.venv/bin/pytest tests/test_mass_correction_batch_ocr.py -v`
Expected: PASS (4 testes)

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/mass_correction_batch.py tests/test_mass_correction_batch_ocr.py \
        src/agente_ia_edu/providers/adapters/openai.py
git commit -m "feat: montagem e aplicacao de resultado do lote de OCR"
```

---

### Task 4: Estágio 2 (correção fase 1) — montagem de lote e aplicação de resultado

**Files:**
- Modify: `src/agente_ia_edu/services/mass_correction_batch.py`
- Test: `tests/test_mass_correction_batch_correction.py`

**Interfaces:**
- Consumes: `essay_prompts.get_essay_prompt("essay_correction_v15").build(...)` (já existente); `essay_engine_validation.validate_engine_output_from_payload` (já existente, Camadas 1-3).
- Produces (usado pela Task 6):
  - `build_correction_batch_request(submission_id: str, *, essay_statement: str, rubric_payload: dict, canonical_text: str, include_scores: bool) -> dict` - monta uma linha de JSONL pra corrigir UMA submissão (fase 1), reaproveitando `get_essay_prompt("essay_correction_v15").build(anchor_mode="TEXT_OFFSET", ...)`.
  - `apply_correction_batch_result(result_line: dict, *, rubric_view, text: str) -> dict` - recebe uma linha de resultado, faz `json.loads` do conteúdo da mensagem, chama `validate_engine_output_from_payload` (mesma validação de 3 camadas do caminho síncrono), devolve um dict com as mesmas chaves que `EssayCorrectionService._run_ai` monta hoje pra gravar em `EssayCorrection` (`ai_output`, `final_feedback`, `failure_reason`) - MENOS `final_scores`, que só fica completo depois da Task 5 (fase 2). Se a validação rejeitar (`EssayEngineOutputRejected`), devolve o mesmo formato de falha que `_run_ai` já usa (`ai_output=None`, `failure_reason=...`).

- [ ] **Step 1: Escrever os testes (falhando)**

Leia primeiro `src/agente_ia_edu/services/essay_correction.py`'s `_run_ai` (linhas 536-620 aproximadamente) pra replicar exatamente a forma do dict de falha (`failure_fields`) - não invente um formato novo.

```python
# tests/test_mass_correction_batch_correction.py
import json
import unittest
import uuid

from agente_ia_edu.essay_engine_contract.v5 import CONTRACT_VERSION
from agente_ia_edu.rubrics.loader import RubricView
from agente_ia_edu.services.mass_correction_batch import (
    apply_correction_batch_result,
    build_correction_batch_request,
)

RUBRIC_PAYLOAD = {
    "rubric_version": "ENEM_2025",
    "competencies": [
        {"code": c, "official_title": "titulo", "levels": [
            {"points": p, "descriptor": "descricao"} for p in (0, 40, 80, 120, 160, 200)
        ]}
        for c in ("C1", "C2", "C3", "C4", "C5")
    ],
}
RUBRIC_VIEW = RubricView(
    rubric_version="ENEM_2025",
    levels={c: frozenset((0, 40, 80, 120, 160, 200)) for c in ("C1", "C2", "C3", "C4", "C5")},
    signal_keys=frozenset(),
)


class BuildCorrectionBatchRequestTests(unittest.TestCase):
    def test_builds_a_text_offset_prompt_for_the_submission(self):
        submission_id = str(uuid.uuid4())
        line = build_correction_batch_request(
            submission_id, essay_statement="Disserte sobre X.",
            rubric_payload=RUBRIC_PAYLOAD, canonical_text="Um texto qualquer.",
            include_scores=True,
        )
        self.assertEqual(line["custom_id"], submission_id)
        prompt_text = line["body"]["messages"][-1]["content"]
        self.assertIn("Um texto qualquer.", prompt_text)
        self.assertIn("TEXT_OFFSET", prompt_text)


class ApplyCorrectionBatchResultTests(unittest.TestCase):
    def test_rejects_and_returns_failure_shape_on_invalid_payload(self):
        result_line = {
            "custom_id": str(uuid.uuid4()),
            "response": {"status_code": 200, "body": {"choices": [{"message": {"content": "{}"}}]}},
            "error": None,
        }
        result = apply_correction_batch_result(result_line, rubric_view=RUBRIC_VIEW, text="Um texto qualquer.")
        self.assertIsNone(result["ai_output"])
        self.assertIsNotNone(result["failure_reason"])

    def test_a_provider_level_error_also_returns_the_failure_shape(self):
        result_line = {"custom_id": str(uuid.uuid4()), "response": None, "error": {"message": "boom"}}
        result = apply_correction_batch_result(result_line, rubric_view=RUBRIC_VIEW, text="Um texto qualquer.")
        self.assertIsNone(result["ai_output"])
        self.assertIn("boom", result["failure_reason"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/pytest tests/test_mass_correction_batch_correction.py -v`
Expected: FAIL com `ImportError`

- [ ] **Step 3: Implementar (adicionar ao mesmo arquivo da Task 3)**

Leia `services/essay_correction.py`'s `_run_ai` (a parte que monta `identification`, chama `validate_engine_output_from_payload`, e o formato de `failure_fields`) antes de escrever - a intenção é reproduzir o MESMO contrato de saída, nunca inventar um novo.

```python
# adicionar em src/agente_ia_edu/services/mass_correction_batch.py

import json
import uuid as _uuid

from ..essay_engine_contract.v5 import CONTRACT_VERSION
from ..essay_engine_validation import EssayEngineOutputRejected, validate_engine_output_from_payload
from ..essay_prompts import get_essay_prompt

_PROMPT_VERSION = "essay_correction_v15"
_ENGINE_VERSION = "r3_correction_engine_v2"


def build_correction_batch_request(
    submission_id: str, *, essay_statement: str, rubric_payload: dict,
    canonical_text: str, include_scores: bool,
) -> dict:
    prompt_text = get_essay_prompt(_PROMPT_VERSION).build(
        anchor_mode="TEXT_OFFSET", essay_statement=essay_statement,
        rubric=rubric_payload, include_scores=include_scores, text=canonical_text,
    )
    return {
        "custom_id": submission_id,
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": None,  # preenchido pela Task 6 com OPENAI_MODEL
            "messages": [{"role": "user", "content": prompt_text}],
        },
    }


def apply_correction_batch_result(result_line: dict, *, rubric_view, text: str) -> dict:
    custom_id = result_line["custom_id"]
    failure_fields = {
        "correction_key": None, "model_version": None, "prompt_version": _PROMPT_VERSION,
        "engine_version": _ENGINE_VERSION, "ai_output": None, "final_feedback": None,
        "failure_reason": None,
    }
    if result_line.get("error"):
        return {**failure_fields, "failure_reason": f"BatchError: {result_line['error']}"}
    response = result_line["response"]
    if response["status_code"] != 200:
        return {
            **failure_fields,
            "failure_reason": f"BatchHTTPError: status {response['status_code']}: {response['body']}",
        }
    raw_content = response["body"]["choices"][0]["message"]["content"]
    try:
        raw_payload = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        return {**failure_fields, "failure_reason": f"Model returned invalid JSON: {exc}"}

    identification = {
        "essay_id": str(_uuid.uuid4()), "essay_version_id": custom_id,
        "rubric_version": rubric_view.rubric_version, "model_version": "batch",
        "prompt_version": _PROMPT_VERSION, "engine_version": _ENGINE_VERSION,
        "contract_version": CONTRACT_VERSION, "anchor_mode": "TEXT_OFFSET",
    }
    full_payload = {**raw_payload, "identification": identification}
    try:
        output = validate_engine_output_from_payload(full_payload, rubric=rubric_view, text=text)
    except EssayEngineOutputRejected as exc:
        return {**failure_fields, "failure_reason": f"{exc}"}

    return {
        **failure_fields,
        "ai_output": output.model_dump(mode="json"),
        "final_feedback": output.feedback.model_dump(mode="json"),
        "failure_reason": None,
    }
```

Confira o nome exato do módulo de validação (`essay_engine_validation` vs. `services.essay_engine_validation`) e o nome exato da exceção lendo `src/agente_ia_edu/services/essay_correction.py`'s imports antes de escrever os `import`s acima - ajuste o caminho se divergir do que está aqui.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/pytest tests/test_mass_correction_batch_correction.py -v`
Expected: PASS (3 testes)

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/mass_correction_batch.py tests/test_mass_correction_batch_correction.py
git commit -m "feat: montagem e aplicacao de resultado do lote de correcao (fase 1)"
```

---

### Task 5: Estágio 3 (pontuação fase 2) — montagem de lote e aplicação de resultado

**Files:**
- Modify: `src/agente_ia_edu/services/mass_correction_batch.py`
- Test: `tests/test_mass_correction_batch_scoring.py`

**Interfaces:**
- Consumes: `essay_prompts.competency_scoring_v1`/`essay_prompts.alert_review_v1` (já existentes); `_apply_deterministic_scoring_rules` de `services/essay_correction.py` (leia a assinatura exata antes de escrever - se for um método privado da classe em vez de função de módulo, pode precisar ser promovido a função de módulo nesta task, já que o código de lote não tem uma instância de `EssayCorrectionService`).
- Produces (usado pela Task 6):
  - `build_scoring_batch_requests(correction_id: str, *, output_dict: dict, rubric_file) -> list[dict]` - devolve uma lista de linhas de JSONL (uma por competência C1-C5 + uma de revisão de alertas, 6 linhas), `custom_id` no formato `f"{correction_id}:C1"`, `f"{correction_id}:C2"`, ..., `f"{correction_id}:alert"`.
  - `apply_scoring_batch_results(correction_id: str, result_lines_by_custom_id: dict[str, dict], *, output_dict: dict, rubric_file) -> dict` - recebe todas as 6 linhas de resultado de UMA correção já agrupadas por `custom_id`, reconstrói `phase2_points`/`confirmed_alert_codes`, chama a mesma função de pontuação determinística que o caminho síncrono usa, devolve `{"final_scores": {...}}`.

- [ ] **Step 1: Ler o código síncrono de fase 2 antes de escrever qualquer coisa**

Leia `src/agente_ia_edu/services/essay_correction.py`'s `_score_competencies_from_evidence`, `_review_anula_redacao_alerts`, e `_apply_deterministic_scoring_rules` por inteiro. Confirme:
- Os nomes exatos dos prompts usados (`essay_prompts.competency_scoring_v1`/`essay_prompts.alert_review_v1` - confirme os nomes de função/módulo exatos, o brief pode estar citando de memória).
- Se `_apply_deterministic_scoring_rules` é uma função de módulo (importável direto) ou um método - se for método sem usar `self` de forma essencial (não faz chamada de rede nem acessa `self.session`), extraia para função de módulo em `essay_correction.py` nesta mesma task, e troque a chamada síncrona existente pra usar essa função extraída (retrocompatibilidade: teste os testes de `essay_correction.py` já existentes depois da extração).

- [ ] **Step 2: Escrever os testes (falhando)**

Escreva os testes espelhando o formato real encontrado no Step 1 - como este código depende diretamente da forma exata de `_apply_deterministic_scoring_rules`/dos prompts de fase 2 (que só se confirma lendo o Step 1), os testes completos ficam a cargo de quem implementa, seguindo este padrão:

```python
# tests/test_mass_correction_batch_scoring.py
import unittest
import uuid

from agente_ia_edu.services.mass_correction_batch import (
    apply_scoring_batch_results,
    build_scoring_batch_requests,
)

# ... fixtures de output_dict/rubric_file no mesmo formato que
# tests/test_r3_essay_correction_service.py já usa pros testes de
# _score_competencies_from_evidence/_review_anula_redacao_alerts (reaproveite
# esse fixture em vez de inventar um novo)


class BuildScoringBatchRequestsTests(unittest.TestCase):
    def test_builds_six_lines_one_per_competency_plus_one_alert_review(self):
        correction_id = str(uuid.uuid4())
        # output_dict/rubric_file: mesma fixture do teste síncrono equivalente
        lines = build_scoring_batch_requests(correction_id, output_dict=..., rubric_file=...)
        custom_ids = {line["custom_id"] for line in lines}
        self.assertEqual(
            custom_ids,
            {f"{correction_id}:{code}" for code in ("C1", "C2", "C3", "C4", "C5")} | {f"{correction_id}:alert"},
        )


class ApplyScoringBatchResultsTests(unittest.TestCase):
    def test_combines_all_six_results_into_final_scores(self):
        correction_id = str(uuid.uuid4())
        # monte result_lines_by_custom_id com as 6 respostas simuladas, no
        # mesmo formato de response.body.choices[0].message.content que os
        # testes síncronos de _score_competencies_from_evidence já usam
        result = apply_scoring_batch_results(
            correction_id, result_lines_by_custom_id={...}, output_dict=..., rubric_file=...,
        )
        self.assertIn("final_scores", result)
        self.assertEqual(set(result["final_scores"]["per_competency"]), {"C1", "C2", "C3", "C4", "C5"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `.venv/bin/pytest tests/test_mass_correction_batch_scoring.py -v`
Expected: FAIL com `ImportError`

- [ ] **Step 4: Implementar**

Monte `build_scoring_batch_requests`/`apply_scoring_batch_results` reaproveitando os prompts e a função de pontuação determinística encontrados no Step 1 - a estrutura exata do corpo de cada requisição (uma por competência + uma de alerta) replica o que `_score_competencies_from_evidence`/`_review_anula_redacao_alerts` já montam hoje pra cada chamada individual, só que cada uma vira uma linha de JSONL em vez de uma chamada `await self._get_text_provider().generate(...)`.

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `.venv/bin/pytest tests/test_mass_correction_batch_scoring.py -v`
Expected: PASS

- [ ] **Step 6: Rodar a suíte de correção inteira pra checar que a extração do Step 1 não regrediu nada**

Run: `.venv/bin/pytest tests -k "essay_correction" -q`
Expected: mesmo resultado de antes desta task.

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/services/mass_correction_batch.py src/agente_ia_edu/services/essay_correction.py \
        tests/test_mass_correction_batch_scoring.py
git commit -m "feat: montagem e aplicacao de resultado do lote de pontuacao (fase 2)"
```

---

### Task 6: Driver resumível + script de linha de comando

**Files:**
- Create: `src/agente_ia_edu/services/mass_correction_driver.py`
- Create: `scripts/run_mass_correction.py`
- Test: `tests/test_mass_correction_driver.py`

**Interfaces:**
- Consumes: `MassCorrectionRun` (Task 1); `openai_batch_client` (Task 2); `mass_correction_batch`'s `build_*`/`apply_*` (Tasks 3-5).
- Produces: nada consumido por outra task - é o topo da pilha.

- [ ] **Step 1: Escrever o teste do driver (falhando)**

O driver é orquestração fina (decide o que fazer dado o estado atual de `MassCorrectionRun`) - os testes verificam DECISÕES, não a mecânica HTTP (já coberta pelas Tasks 2-5), usando dublês (`unittest.mock`) pro cliente da Batch API.

```python
# tests/test_mass_correction_driver.py
import unittest
import uuid
from unittest.mock import AsyncMock, patch

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import MassCorrectionRun, School
from agente_ia_edu.services.mass_correction_driver import advance_run


class AdvanceRunTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_run(self, session, *, status):
        school = School(id=uuid.uuid4(), code="EST", name="Rede")
        session.add(school)
        await session.flush()
        run = MassCorrectionRun(
            id=uuid.uuid4(), school_id=school.id, stage="OCR",
            sequence_number=1, request_count=10, status=status,
        )
        session.add(run)
        await session.commit()
        return run

    @patch("agente_ia_edu.services.mass_correction_driver.upload_batch_file", new_callable=AsyncMock)
    @patch("agente_ia_edu.services.mass_correction_driver.create_batch", new_callable=AsyncMock)
    async def test_a_pending_run_gets_submitted(self, mock_create_batch, mock_upload):
        mock_upload.return_value = "file-in-123"
        mock_create_batch.return_value = {"id": "batch-abc", "status": "validating"}
        async with self.session_factory() as session:
            run = await self._seed_run(session, status="PENDING")
            await advance_run(session, run.id, api_key="sk-test", pending_lines=[{"custom_id": "x"}])
            await session.refresh(run)
            self.assertEqual(run.openai_batch_id, "batch-abc")
            self.assertEqual(run.status, "validating")
            self.assertEqual(run.input_file_id, "file-in-123")

    @patch("agente_ia_edu.services.mass_correction_driver.get_batch", new_callable=AsyncMock)
    async def test_an_in_progress_run_just_polls_and_updates_status(self, mock_get_batch):
        mock_get_batch.return_value = {"id": "batch-abc", "status": "in_progress"}
        async with self.session_factory() as session:
            run = await self._seed_run(session, status="validating")
            run.openai_batch_id = "batch-abc"
            await session.commit()
            await advance_run(session, run.id, api_key="sk-test")
            await session.refresh(run)
            self.assertEqual(run.status, "in_progress")
            self.assertIsNone(run.completed_at)

    @patch("agente_ia_edu.services.mass_correction_driver.get_batch", new_callable=AsyncMock)
    async def test_a_completed_run_records_the_output_file_and_completed_at(self, mock_get_batch):
        mock_get_batch.return_value = {"id": "batch-abc", "status": "completed", "output_file_id": "file-out"}
        async with self.session_factory() as session:
            run = await self._seed_run(session, status="in_progress")
            run.openai_batch_id = "batch-abc"
            await session.commit()
            await advance_run(session, run.id, api_key="sk-test")
            await session.refresh(run)
            self.assertEqual(run.status, "completed")
            self.assertEqual(run.output_file_id, "file-out")
            self.assertIsNotNone(run.completed_at)

    @patch("agente_ia_edu.services.mass_correction_driver.upload_batch_file", new_callable=AsyncMock)
    @patch("agente_ia_edu.services.mass_correction_driver.create_batch", new_callable=AsyncMock)
    async def test_resuming_a_run_that_already_has_a_batch_id_never_submits_again(
        self, mock_create_batch, mock_upload,
    ):
        """A resumibilidade do driver: um PENDING sem openai_batch_id ainda
        submete; qualquer status != PENDING (já tem batch_id) nunca chama
        create_batch de novo, mesmo se advance_run for chamado de novo depois
        de o processo cair no meio."""
        async with self.session_factory() as session:
            run = await self._seed_run(session, status="validating")
            run.openai_batch_id = "batch-ja-existe"
            await session.commit()
            with patch(
                "agente_ia_edu.services.mass_correction_driver.get_batch", new_callable=AsyncMock
            ) as mock_get_batch:
                mock_get_batch.return_value = {"id": "batch-ja-existe", "status": "validating"}
                await advance_run(session, run.id, api_key="sk-test")
            mock_create_batch.assert_not_called()
            mock_upload.assert_not_called()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/pytest tests/test_mass_correction_driver.py -v`
Expected: FAIL com `ImportError`

- [ ] **Step 3: Implementar `advance_run`**

```python
# src/agente_ia_edu/services/mass_correction_driver.py
"""Driver resumivel de correcao em massa (spec 2026-09-29): dado o estado
atual de um MassCorrectionRun, decide a UNICA proxima acao (submeter, ou
so consultar status, ou finalizar) - nunca reenvia um lote que ja tem
openai_batch_id, e por isso pode ser chamado repetidamente (inclusive apos
o processo cair no meio) sem duplicar trabalho nem gastar em dobro."""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import MassCorrectionRun
from ..providers.openai_batch_client import create_batch, get_batch, upload_batch_file


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


async def advance_run(
    session: AsyncSession, run_id: uuid.UUID, *, api_key: str, pending_lines: list[dict] | None = None,
) -> MassCorrectionRun:
    run = await session.get(MassCorrectionRun, run_id)
    if run is None:
        raise ValueError(f"MassCorrectionRun not found: {run_id}")

    if run.openai_batch_id is None:
        if pending_lines is None:
            raise ValueError(
                f"MassCorrectionRun {run_id} has no openai_batch_id yet and no pending_lines "
                "were provided to submit it"
            )
        file_id = await upload_batch_file(pending_lines, api_key=api_key)
        batch = await create_batch(file_id, api_key=api_key)
        run.input_file_id = file_id
        run.openai_batch_id = batch["id"]
        run.status = batch["status"]
        await session.commit()
        return run

    batch = await get_batch(run.openai_batch_id, api_key=api_key)
    run.status = batch["status"]
    if batch["status"] == "completed":
        run.output_file_id = batch.get("output_file_id")
        run.completed_at = _utcnow()
    await session.commit()
    return run
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/pytest tests/test_mass_correction_driver.py -v`
Expected: PASS (4 testes)

- [ ] **Step 5: Script de linha de comando**

```python
# scripts/run_mass_correction.py
"""Dispara/retoma o processamento em massa de um lote de redacoes
escaneadas via Batch API. Uso:

    .venv/bin/python scripts/run_mass_correction.py --school-id <uuid> --stage OCR

Idempotente: rodar de novo com os mesmos argumentos enquanto ha um
MassCorrectionRun em andamento so consulta o status, nunca resubmete.
"""
import argparse
import asyncio
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv

from agente_ia_edu.db.session import get_session_factory
from agente_ia_edu.services.mass_correction_driver import advance_run


async def main(school_id: str, stage: str) -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    api_key = os.environ["OPENAI_API_KEY"]
    session_factory = get_session_factory()
    async with session_factory() as session:
        # A busca do MassCorrectionRun pendente/em-andamento pra este
        # school_id+stage, e a montagem das pending_lines na primeira
        # submissao (chamando os build_* da Task certa conforme o stage),
        # ficam a cargo de quem implementar este passo - a forma exata
        # depende de qual EssayBatchUpload/EssaySubmission esta escola tem
        # pendentes neste estagio, uma consulta real ao banco, nao um mock.
        raise NotImplementedError(
            "monte aqui a consulta real ao MassCorrectionRun mais recente "
            "para (school_id, stage) e chame advance_run - ver Task 6 Step 6"
        )


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--school-id", required=True)
    parser.add_argument("--stage", required=True, choices=["OCR", "CORRECTION", "SCORING"])
    args = parser.parse_args()
    asyncio.run(main(args.school_id, args.stage))
```

- [ ] **Step 6: Completar o `main()` do script com a consulta real**

Substitua o `raise NotImplementedError` acima: consulte `select(MassCorrectionRun).where(MassCorrectionRun.school_id == school_id, MassCorrectionRun.stage == stage).order_by(MassCorrectionRun.sequence_number.desc())` pra achar a run mais recente. Se não existir nenhuma, ou a mais recente está `"completed"`/`"failed"`/`"expired"`/`"cancelled"` e ainda há trabalho pendente (páginas sem `ocr_body_text` pro estágio OCR, por exemplo), crie uma nova `MassCorrectionRun` com `status="PENDING"` e monte `pending_lines` chamando `build_ocr_batch_request`/`build_correction_batch_request`/`build_scoring_batch_requests` (Tasks 3-5) para os itens pendentes encontrados. Se a run mais recente ainda está em andamento (`PENDING`/`validating`/`in_progress`/`finalizing`), chame `advance_run` sem `pending_lines` - ele só consulta o status. Se `status == "completed"`, baixe o resultado (`download_file_lines`, Task 2) e aplique cada linha com `apply_ocr_batch_result`/`apply_correction_batch_result`/`apply_scoring_batch_results` conforme o `stage`, gravando de volta nos modelos reais (`EssayBatchPage`/`EssaySubmission`/`EssayCorrection`) exatamente como o caminho síncrono já faz.

- [ ] **Step 7: Verificação manual com um lote real pequeno (obrigatória, documentar no relatório)**

Rode o script de ponta a ponta contra 2-3 páginas reais (reaproveite uma das imagens reais já usadas nesta sessão, ex: `var/material_storage/76/.../racismo-manuscrita.jpg`), com `--stage OCR` primeiro, aguarde a conclusão real do lote (pode levar minutos, não precisa esperar as 24h completas pra um lote pequeno), confirme que `ocr_body_text` foi gravado corretamente comparando com o texto real da imagem. Documente no relatório desta task o `batch_id` real usado e o tempo real de conclusão observado.

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/services/mass_correction_driver.py scripts/run_mass_correction.py \
        tests/test_mass_correction_driver.py
git commit -m "feat: driver resumivel de correcao em massa + script de linha de comando"
```

---

### Task 7: Recorte cabeçalho/corpo configurável

**Files:**
- Modify: `src/agente_ia_edu/services/essay_batch.py` (`_crop_regions`, `HEADER_REGION_FRACTION`)
- Test: `tests/test_r4_essay_batch_ocr.py` (ou o arquivo de teste que já cobre `_crop_regions` hoje - localize com `grep -rn "_crop_regions\|HEADER_REGION_FRACTION" tests/`)

**Interfaces:**
- Consumes: nada de outra task.
- Produces: nada consumido por outra task nesta leva (a calibração do valor real fica para depois, fora deste plano).

- [ ] **Step 1: Localizar os usos atuais e o teste existente**

Run: `grep -rn "_crop_regions\|HEADER_REGION_FRACTION" src/ tests/`

Leia o(s) teste(s) encontrado(s) antes de mexer - a mudança precisa manter o comportamento de HOJE como padrão, então o teste existente deve continuar passando sem alteração.

- [ ] **Step 2: Escrever o teste do parâmetro novo (falhando)**

```python
# adicionar ao arquivo de teste existente de _crop_regions
def test_crop_regions_accepts_a_custom_header_fraction(self):
    # reaproveite a mesma imagem de fixture que o teste existente de
    # _crop_regions já usa
    header_path, body_path = EssayBatchService._crop_regions(
        image_path, scratch_dir, header_fraction=0.5,
    )
    # confirme que o recorte de cabecalho ficou proporcionalmente maior
    # que o recorte com a fracao padrao - compare a altura em pixels dos
    # dois arquivos gerados (PIL.Image.open(path).size ou
    # pymupdf.Pixmap(path).height)
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `.venv/bin/pytest tests/test_r4_essay_batch_ocr.py -k custom_header_fraction -v`
Expected: FAIL com `TypeError: _crop_regions() got an unexpected keyword argument 'header_fraction'`

- [ ] **Step 4: Adicionar o parâmetro, mantendo o valor de hoje como padrão**

Em `services/essay_batch.py`, ache a constante `HEADER_REGION_FRACTION` (usada dentro de `_crop_regions`) e mude a assinatura do método:

```python
@staticmethod
def _crop_regions(
    image_path: Path, dest_dir: Path, *, header_fraction: float = HEADER_REGION_FRACTION,
) -> tuple[Path, Path]:
```

e troque o uso interno de `HEADER_REGION_FRACTION` por `header_fraction` no corpo do método (a constante do módulo continua existindo, só passa a ser o VALOR PADRÃO em vez de hard-coded dentro da função). Não mude o valor da constante - o padrão de hoje continua sendo o padrão.

- [ ] **Step 5: Rodar e confirmar que passa, e que nada regrediu**

Run: `.venv/bin/pytest tests/test_r4_essay_batch_ocr.py -q`
Expected: todos passam, incluindo o teste novo.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/essay_batch.py tests/test_r4_essay_batch_ocr.py
git commit -m "feat: recorte cabecalho/corpo do OCR em lote passa a ser configuravel"
```

**Nota para o usuário (não é uma tarefa a executar agora):** quando as fotos reais das folhas da rede estadual chegarem, calibrar o valor de `header_fraction` certo (e, se as folhas usarem mais de um layout, decidir como escolher a fração certa por lote) é um passo curto e separado, fora deste plano - não precisa de mais nenhuma mudança de código além de descobrir o número certo e passá-lo pro driver da Task 6.

---

### Task 8: Tela de status mínima

**Files:**
- Create: `src/agente_ia_edu/api/routes/mass_correction_status.py`
- Modify: `src/agente_ia_edu/api/app.py` (registrar o router novo, mesmo padrão dos outros `include_router` já existentes)
- Test: `tests/test_mass_correction_status_route.py`

**Interfaces:**
- Consumes: `MassCorrectionRun` (Task 1).
- Produces: nada consumido por outra task - última task do plano.

- [ ] **Step 1: Escrever o teste da rota (falhando)**

Leia primeiro uma rota administrativa existente simples (`api/routes/admin.py` ou similar) pra reaproveitar o mesmo padrão de autorização (`require_platform_admin`, já usado pelas rotas de propostas da plataforma).

```python
# tests/test_mass_correction_status_route.py
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import MassCorrectionRun, School
from agente_ia_edu.identity import ExternalIdentityContext


class MassCorrectionStatusRouteTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import asyncio
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        cls.loop.run_until_complete(_prep())
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def test_status_reports_counts_per_stage(self):
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code="EST", name="Rede")
                session.add(school)
                await session.flush()
                session.add(MassCorrectionRun(
                    id=uuid.uuid4(), school_id=school.id, stage="OCR",
                    sequence_number=1, request_count=100, status="completed",
                ))
                session.add(MassCorrectionRun(
                    id=uuid.uuid4(), school_id=school.id, stage="CORRECTION",
                    sequence_number=1, request_count=50, status="in_progress",
                ))
                await session.commit()
                return school.id

        school_id = self.loop.run_until_complete(_seed())
        # ajuste o override de identidade pra um admin da plataforma - mesmo
        # padrao ja usado em tests/test_admin_essay_prompts_routes.py (ou
        # arquivo equivalente) para require_platform_admin
        resp = self.client.get(f"/api/v1/admin/mass-correction-runs?school_id={school_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["OCR"]["completed"], 100)
        self.assertEqual(body["CORRECTION"]["in_progress"], 50)


if __name__ == "__main__":
    unittest.main()
```

Ajuste o teste com o mecanismo REAL de autorização de admin depois de ler o arquivo equivalente já existente (`admin_essay_prompts.py`/seu teste) - o esqueleto acima mostra a intenção, não o `dependency_overrides` exato.

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/pytest tests/test_mass_correction_status_route.py -v`
Expected: FAIL com 404 (rota não existe)

- [ ] **Step 3: Implementar a rota**

```python
# src/agente_ia_edu/api/routes/mass_correction_status.py
"""Tela de status minima da correcao em massa (spec 2026-09-29) - so
leitura, agregando MassCorrectionRun por estagio. Sem paginacao/filtro
avancado de proposito: e uma tela de operacao pontual, nao um produto."""

from __future__ import annotations

import uuid
from collections import defaultdict

from fastapi import APIRouter, Depends
from sqlalchemy import func, select

from ..dependencies import get_current_identity, get_session_factory
from ...db.models import MassCorrectionRun
from ...identity import ExternalIdentityContext
from ...services.authorization import require_platform_admin  # confirme o nome exato lendo admin_essay_prompts.py

mass_correction_status_router = APIRouter(prefix="/api/v1/admin/mass-correction-runs", tags=["mass-correction"])


@mass_correction_status_router.get("")
async def get_mass_correction_status(
    school_id: uuid.UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> dict:
    async with session_factory() as session:
        await require_platform_admin(identity, session)
        rows = (await session.execute(
            select(MassCorrectionRun.stage, MassCorrectionRun.status, func.sum(MassCorrectionRun.request_count))
            .where(MassCorrectionRun.school_id == school_id)
            .group_by(MassCorrectionRun.stage, MassCorrectionRun.status)
        )).all()
        result: dict = defaultdict(dict)
        for stage, status, total in rows:
            result[stage][status] = total
        return dict(result)
```

Confirme o nome exato da função/mecanismo de `require_platform_admin` lendo `api/routes/admin_essay_prompts.py` antes de importar - use o padrão real do projeto, não invente uma assinatura.

Registre o router em `api/app.py` junto dos outros `app.include_router(...)` já existentes.

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/pytest tests/test_mass_correction_status_route.py -v`
Expected: PASS

- [ ] **Step 5: Rodar a suíte inteira do projeto pra checar regressão**

Run: `.venv/bin/pytest tests -q` (pode levar ~15 minutos; as 2 falhas conhecidas de `test_r4_essay_batch_migration_postgresql.py`, migration 024, são pré-existentes e não relacionadas - já documentadas nesta sessão)

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/api/routes/mass_correction_status.py src/agente_ia_edu/api/app.py \
        tests/test_mass_correction_status_route.py
git commit -m "feat: tela de status minima da correcao em massa"
```

---

### Task 9: Amostragem para revisão humana + aprovação automática do resto

**Files:**
- Create: `src/agente_ia_edu/services/mass_correction_sampling.py`
- Modify: `src/agente_ia_edu/services/mass_correction_driver.py` (chamar a amostragem depois que uma correção termina o estágio SCORING)
- Test: `tests/test_mass_correction_sampling.py`

**Interfaces:**
- Consumes: `EssayCorrectionService.bulk_approve` (já existente, `services/essay_correction.py:460`).
- Produces: nada consumido por outra task - a spec (seção "Amostragem e revisão") descreve este passo como o fechamento do fluxo: correções com alerta (ou sem nota, indicando falha) sempre entram na amostra; o resto é sorteado a uma taxa configurável (padrão 5%) e o restante é aprovado automaticamente.

- [ ] **Step 1: Escrever o teste da função de seleção (falhando)**

Sorteio determinístico (baseado no hash do próprio `id` da correção, não em `random.random()`) para que o teste seja reprodutível sem precisar fixar uma seed global do `random`, que vazaria pra qualquer outro código que também use `random` no mesmo processo.

```python
# tests/test_mass_correction_sampling.py
import unittest
import uuid

from agente_ia_edu.services.mass_correction_sampling import select_sample_for_review


class SelectSampleForReviewTests(unittest.TestCase):
    def test_a_correction_with_alerts_always_goes_to_the_sample(self):
        correction_id = uuid.uuid4()
        sample_ids, auto_approve_ids = select_sample_for_review(
            [{"id": correction_id, "alerts": ["FUGA_AO_TEMA"], "has_scores": True}],
            sample_rate=0.0,  # mesmo com taxa zero, alerta sempre entra
        )
        self.assertEqual(sample_ids, [correction_id])
        self.assertEqual(auto_approve_ids, [])

    def test_a_correction_without_scores_always_goes_to_the_sample(self):
        correction_id = uuid.uuid4()
        sample_ids, auto_approve_ids = select_sample_for_review(
            [{"id": correction_id, "alerts": [], "has_scores": False}],
            sample_rate=0.0,
        )
        self.assertEqual(sample_ids, [correction_id])

    def test_sample_rate_100_percent_puts_everything_in_the_sample(self):
        ids = [uuid.uuid4() for _ in range(20)]
        corrections = [{"id": i, "alerts": [], "has_scores": True} for i in ids]
        sample_ids, auto_approve_ids = select_sample_for_review(corrections, sample_rate=1.0)
        self.assertEqual(set(sample_ids), set(ids))
        self.assertEqual(auto_approve_ids, [])

    def test_sample_rate_0_percent_auto_approves_everything_healthy(self):
        ids = [uuid.uuid4() for _ in range(20)]
        corrections = [{"id": i, "alerts": [], "has_scores": True} for i in ids]
        sample_ids, auto_approve_ids = select_sample_for_review(corrections, sample_rate=0.0)
        self.assertEqual(sample_ids, [])
        self.assertEqual(set(auto_approve_ids), set(ids))

    def test_selection_is_deterministic_across_calls(self):
        ids = [uuid.uuid4() for _ in range(50)]
        corrections = [{"id": i, "alerts": [], "has_scores": True} for i in ids]
        first_call = select_sample_for_review(corrections, sample_rate=0.2)
        second_call = select_sample_for_review(corrections, sample_rate=0.2)
        self.assertEqual(first_call, second_call)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/pytest tests/test_mass_correction_sampling.py -v`
Expected: FAIL com `ModuleNotFoundError`

- [ ] **Step 3: Implementar**

```python
# src/agente_ia_edu/services/mass_correction_sampling.py
"""Selecao de amostra para revisao humana apos a correcao em massa (spec
2026-09-29, secao "Amostragem e revisao"). Correcoes com alerta ou sem
nota (falha) sempre entram na amostra - nao sao sorteadas, sao garantidas.
O resto e sorteado a uma taxa configuravel; quem nao cai na amostra e
aprovado automaticamente (bulk_approve, ja existente).

O sorteio usa o hash do proprio id da correcao (nao random.random()) para
ser deterministico e reprodutivel sem depender de fixar uma seed global
que afetaria qualquer outro codigo do processo que tambem use random."""

from __future__ import annotations

import hashlib
import uuid


def _deterministic_unit_interval(correction_id: uuid.UUID) -> float:
    digest = hashlib.sha256(correction_id.bytes).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def select_sample_for_review(
    corrections: list[dict], *, sample_rate: float = 0.05,
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    sample_ids: list[uuid.UUID] = []
    auto_approve_ids: list[uuid.UUID] = []
    for correction in corrections:
        correction_id = correction["id"]
        if correction["alerts"] or not correction["has_scores"]:
            sample_ids.append(correction_id)
            continue
        if _deterministic_unit_interval(correction_id) < sample_rate:
            sample_ids.append(correction_id)
        else:
            auto_approve_ids.append(correction_id)
    return sample_ids, auto_approve_ids
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/pytest tests/test_mass_correction_sampling.py -v`
Expected: PASS (5 testes)

- [ ] **Step 5: Ligar ao driver**

Em `services/mass_correction_driver.py`, depois que o estágio `SCORING` de um lote é aplicado (a parte que, na Task 6 Step 6, aplica `apply_scoring_batch_results` e grava `final_scores` em cada `EssayCorrection`), monte a lista de dicts `{"id": correction.id, "alerts": ..., "has_scores": correction.final_scores is not None}` para as correções recém-pontuadas, chame `select_sample_for_review`, e então `EssayCorrectionService(session).bulk_approve(auto_approve_ids, reviewed_by_external_identity="mass-correction-driver")` - as que caíram na amostra ficam como estão (`PENDING_REVIEW`), sem nenhuma chamada adicional, prontas pra aparecer na fila de revisão que já existe hoje no portal do professor/coordenador.

- [ ] **Step 6: Rodar a suíte de correção inteira pra checar que nada regrediu**

Run: `.venv/bin/pytest tests -k "essay_correction or mass_correction" -q`
Expected: todos passam.

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/services/mass_correction_sampling.py src/agente_ia_edu/services/mass_correction_driver.py \
        tests/test_mass_correction_sampling.py
git commit -m "feat: amostragem para revisao humana e aprovacao automatica do resto"
```

---

### Task 10: Provisionamento em massa de alunos/turmas via CSV

**Files:**
- Create: `scripts/import_students_csv.py`
- Test: `tests/test_import_students_csv.py`

**Interfaces:**
- Consumes: nada de outra task - usa só os modelos já existentes (`School`, `Segment`, `AcademicYear`, `GradeLevel`, `Class`, `Person`, `Student`, `StudentEnrollment`, todos em `db/models/academic.py`/`db/models/admin.py`).
- Produces: nada consumido por outra task.

O CSV de entrada tem uma linha por aluno, com colunas `nome`, `documento` (CPF, pode vir vazio), `segmento`, `serie` (grade level), `turma`, `ano_letivo` (ex: `2026`). Cada linha, se o segmento/série/turma ainda não existirem NESTA escola, cria (get-or-create, nunca duplica); se já existirem (rodar o script duas vezes com o mesmo CSV nunca duplica nada), reaproveita.

- [ ] **Step 1: Escrever o teste (falhando)**

```python
# tests/test_import_students_csv.py
import csv
import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import Class, GradeLevel, Person, School, Segment, Student, StudentEnrollment
from scripts.import_students_csv import import_students_from_csv

CSV_ROWS = [
    {"nome": "Ana Lúcia Ferreira", "documento": "12345678900", "segmento": "Médio",
     "serie": "3ª Série", "turma": "3A", "ano_letivo": "2026"},
    {"nome": "Bruno Costa", "documento": "", "segmento": "Médio",
     "serie": "3ª Série", "turma": "3A", "ano_letivo": "2026"},
    {"nome": "Carla Dias", "documento": "98765432100", "segmento": "Médio",
     "serie": "3ª Série", "turma": "3B", "ano_letivo": "2026"},
]


class ImportStudentsCsvTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

        async with self.session_factory() as session:
            self.school = School(id=uuid.uuid4(), code="EST-CSV", name="Rede Estadual Teste")
            session.add(self.school)
            await session.commit()

        self.csv_path = Path(tempfile.mktemp(suffix=".csv"))
        with self.csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["nome", "documento", "segmento", "serie", "turma", "ano_letivo"])
            writer.writeheader()
            writer.writerows(CSV_ROWS)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.csv_path.unlink(missing_ok=True)

    async def test_imports_three_students_into_two_classes(self):
        async with self.session_factory() as session:
            await import_students_from_csv(session, self.csv_path, school_id=self.school.id)

        async with self.session_factory() as session:
            students = (await session.execute(select(Student))).scalars().all()
            self.assertEqual(len(students), 3)
            classes = (await session.execute(select(Class))).scalars().all()
            self.assertEqual({c.name for c in classes}, {"3A", "3B"})
            enrollments = (await session.execute(select(StudentEnrollment))).scalars().all()
            self.assertEqual(len(enrollments), 3)
            segments = (await session.execute(select(Segment))).scalars().all()
            self.assertEqual(len(segments), 1)  # so um "Medio" - nao duplicou
            grade_levels = (await session.execute(select(GradeLevel))).scalars().all()
            self.assertEqual(len(grade_levels), 1)  # so uma "3a Serie" - nao duplicou

    async def test_running_twice_with_the_same_csv_never_duplicates(self):
        async with self.session_factory() as session:
            await import_students_from_csv(session, self.csv_path, school_id=self.school.id)
        async with self.session_factory() as session:
            await import_students_from_csv(session, self.csv_path, school_id=self.school.id)

        async with self.session_factory() as session:
            students = (await session.execute(select(Student))).scalars().all()
            self.assertEqual(len(students), 3)
            persons = (await session.execute(select(Person))).scalars().all()
            self.assertEqual(len(persons), 3)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/pytest tests/test_import_students_csv.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'scripts.import_students_csv'`

- [ ] **Step 3: Implementar**

Antes de escrever, leia `src/agente_ia_edu/db/models/academic.py` por inteiro (as classes `AcademicYear`, `Segment`, `GradeLevel`, `Class`, `Person`, `Student`, `StudentEnrollment`) - o código abaixo usa os campos exatos já confirmados nesta task, mas confirme você mesmo antes de copiar, pra pegar qualquer campo obrigatório que não esteja listado aqui.

```python
# scripts/import_students_csv.py
"""Provisionamento em massa de alunos/turmas a partir de um CSV (spec
2026-09-29, "correcao em massa"). Get-or-create em cada nivel da hierarquia
(Segment -> AcademicYear -> GradeLevel -> Class) - rodar o mesmo CSV duas
vezes nunca duplica nada, identificado por nome dentro da mesma escola.

Uso: .venv/bin/python scripts/import_students_csv.py --school-id <uuid> --csv caminho.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import (
    AcademicYear, Class, GradeLevel, Person, Segment, Student, StudentEnrollment,
)


async def _get_or_create_segment(session: AsyncSession, *, school_id: uuid.UUID, name: str) -> Segment:
    existing = (await session.execute(
        select(Segment).where(Segment.school_id == school_id, Segment.name == name)
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    segment = Segment(id=uuid.uuid4(), school_id=school_id, name=name)
    session.add(segment)
    await session.flush()
    return segment


async def _get_or_create_academic_year(session: AsyncSession, *, school_id: uuid.UUID, year: int) -> AcademicYear:
    existing = (await session.execute(
        select(AcademicYear).where(AcademicYear.school_id == school_id, AcademicYear.year == year)
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    academic_year = AcademicYear(id=uuid.uuid4(), school_id=school_id, year=year, status="ACTIVE")
    session.add(academic_year)
    await session.flush()
    return academic_year


async def _get_or_create_grade_level(
    session: AsyncSession, *, school_id: uuid.UUID, segment_id: uuid.UUID, name: str,
) -> GradeLevel:
    existing = (await session.execute(
        select(GradeLevel).where(GradeLevel.school_id == school_id, GradeLevel.name == name)
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    grade_level = GradeLevel(id=uuid.uuid4(), school_id=school_id, segment_id=segment_id, name=name)
    session.add(grade_level)
    await session.flush()
    return grade_level


async def _get_or_create_class(
    session: AsyncSession, *, school_id: uuid.UUID, academic_year_id: uuid.UUID,
    grade_level_id: uuid.UUID, name: str,
) -> Class:
    existing = (await session.execute(
        select(Class).where(
            Class.academic_year_id == academic_year_id, Class.grade_level_id == grade_level_id,
            Class.name == name,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    klass = Class(
        id=uuid.uuid4(), school_id=school_id, academic_year_id=academic_year_id,
        grade_level_id=grade_level_id, name=name,
    )
    session.add(klass)
    await session.flush()
    return klass


async def _get_or_create_student(
    session: AsyncSession, *, school_id: uuid.UUID, full_name: str, document_number: str | None,
) -> Student:
    existing_person = (await session.execute(
        select(Person).where(Person.school_id == school_id, Person.full_name == full_name)
    )).scalar_one_or_none()
    if existing_person is None:
        existing_person = Person(
            id=uuid.uuid4(), school_id=school_id, full_name=full_name,
            document_number=document_number or None,
        )
        session.add(existing_person)
        await session.flush()
    existing_student = (await session.execute(
        select(Student).where(Student.school_id == school_id, Student.person_id == existing_person.id)
    )).scalar_one_or_none()
    if existing_student is not None:
        return existing_student
    student = Student(id=uuid.uuid4(), school_id=school_id, person_id=existing_person.id)
    session.add(student)
    await session.flush()
    return student


async def _ensure_enrollment(
    session: AsyncSession, *, school_id: uuid.UUID, student_id: uuid.UUID, class_id: uuid.UUID,
) -> None:
    existing = (await session.execute(
        select(StudentEnrollment).where(
            StudentEnrollment.student_id == student_id, StudentEnrollment.class_id == class_id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return
    session.add(StudentEnrollment(
        id=uuid.uuid4(), school_id=school_id, student_id=student_id, class_id=class_id,
    ))
    await session.flush()


async def import_students_from_csv(session: AsyncSession, csv_path: Path, *, school_id: uuid.UUID) -> None:
    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        segment = await _get_or_create_segment(session, school_id=school_id, name=row["segmento"])
        academic_year = await _get_or_create_academic_year(
            session, school_id=school_id, year=int(row["ano_letivo"])
        )
        grade_level = await _get_or_create_grade_level(
            session, school_id=school_id, segment_id=segment.id, name=row["serie"]
        )
        klass = await _get_or_create_class(
            session, school_id=school_id, academic_year_id=academic_year.id,
            grade_level_id=grade_level.id, name=row["turma"],
        )
        student = await _get_or_create_student(
            session, school_id=school_id, full_name=row["nome"],
            document_number=row.get("documento") or None,
        )
        await _ensure_enrollment(session, school_id=school_id, student_id=student.id, class_id=klass.id)

    await session.commit()


if __name__ == "__main__":
    from agente_ia_edu.db.session import get_session_factory

    parser = argparse.ArgumentParser()
    parser.add_argument("--school-id", required=True, type=uuid.UUID)
    parser.add_argument("--csv", required=True, type=Path)
    args = parser.parse_args()

    async def _run():
        session_factory = get_session_factory()
        async with session_factory() as session:
            await import_students_from_csv(session, args.csv, school_id=args.school_id)

    asyncio.run(_run())
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/pytest tests/test_import_students_csv.py -v`
Expected: PASS (2 testes)

- [ ] **Step 5: Commit**

```bash
git add scripts/import_students_csv.py tests/test_import_students_csv.py
git commit -m "feat: script de provisionamento em massa de alunos/turmas via CSV"
```
