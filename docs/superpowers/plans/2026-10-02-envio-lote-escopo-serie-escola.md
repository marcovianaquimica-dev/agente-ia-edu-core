# Envio em lote: escopo por série ou escola inteira Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Permitir que o professor envie o "Enviar em lote" (upload de folhas de redação físicas escaneadas) de uma série inteira ou da escola inteira, não só de uma turma, com o casamento automático de aluno e a atribuição final de turma funcionando exatamente como hoje.

**Architecture:** Uma migration torna `EssayBatchUpload.class_id` opcional e adiciona `grade_level_id` opcional (CHECK garante exatamente um dos dois, ou nenhum = escola inteira). Uma função nova (`roster_for_batch`) decide o roster de busca certo por escopo, delegando para a função de turma única já existente (`class_roster`, intocada) quando aplicável. O algoritmo de match (`match_student`) e a atribuição final de turma por aluno (`_assignment_for_student`) não mudam nenhuma linha - só recebem (potencialmente) um roster maior.

**Tech Stack:** Python, FastAPI, SQLAlchemy async, Alembic, Postgres (produção/dev), SQLite in-memory (testes de modelo/serviço), vanilla JS (frontend).

**Spec:** [docs/superpowers/specs/2026-10-02-envio-lote-escopo-serie-escola-design.md](../specs/2026-10-02-envio-lote-escopo-serie-escola-design.md)

## Global Constraints

- `match_student` (`essay_batch.py:177-196`) e `_assignment_for_student` (`essay_batch.py:488-518`) **não são tocados** - já corretos para qualquer tamanho de roster.
- `class_roster` (`essay_batch.py:467-486`) **não é tocada** - continua sendo a implementação de turma única, reaproveitada por `roster_for_batch`, nunca duplicada.
- CPF nunca é critério de match automático (decisão de design já registrada no código) - esta mudança não altera isso.
- Revision ID de migration tem limite de 32 caracteres (`alembic_version.version_num` é `VARCHAR(32)` em toda a cadeia deste repo - confirmado na migration 060).
- Nada no pipeline de correção (`run_corrections`, `EssayCorrectionService`) muda.
- Fora de escopo: qualquer mudança em `/api/v1/teacher/classrooms` (tem um bug pré-existente de `grade_level` hardcoded como `"3ª Série"` sempre - não é desta leva, não mexer).

---

### Task 1: Migration - class_id opcional + grade_level_id + CHECK constraint

**Files:**
- Create: `migrations/versions/061_essay_batch_scope.py`
- Test: `tests/test_r4_essay_batch_scope_migration_postgresql.py`

**Interfaces:**
- Consumes: nada de outra task.
- Produces: coluna `essay_batch_uploads.class_id` nullable; coluna nova `essay_batch_uploads.grade_level_id` (UUID, nullable); FK composta `fk_essay_batch_uploads_school_grade_level` (school_id, grade_level_id) → (grade_levels.school_id, grade_levels.id); CHECK `ck_essay_batch_uploads_scope`.

- [ ] **Step 1: Escrever o teste de migration (falhando)**

`tests/test_r4_essay_batch_scope_migration_postgresql.py`:

```python
"""Validacao em PostgreSQL da migration 061 (escopo turma/serie/escola do
envio em lote).

Ler a migration nao prova nada: um CHECK constraint novo so se comprova
rodando o upgrade de verdade contra linhas reais. Banco descartavel na
porta 5433, mesmo alvo dos outros testes de migration desta suite.
"""

from __future__ import annotations

import os
import unittest
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests._postgres_test_db import create_database, drop_database


class TestEssayBatchScopeMigrationPostgreSQL(unittest.TestCase):
    database_name = "agente_ia_edu_essay_batch_scope_test"
    admin_url = os.getenv(
        "ESSAY_BATCH_SCOPE_TEST_ADMIN_DATABASE_URL",
        "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/postgres",
    )
    database_url = os.getenv(
        "ESSAY_BATCH_SCOPE_TEST_DATABASE_URL",
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
                "PostgreSQL de teste indisponivel; migration 061 nao validada."
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

    def _seed_through_060(self, connection) -> dict:
        """Monta a cadeia minima de linhas-pai (school, segment, grade_level,
        academic_year, class, essay_prompt) pra poder inserir um
        essay_batch_uploads de verdade depois do upgrade."""
        ids = {name: uuid.uuid4() for name in (
            "school", "segment", "grade_level", "year", "class", "prompt",
        )}
        connection.execute(text(
            "INSERT INTO schools (id, code, name, status, created_at, updated_at) "
            "VALUES (:id, 'SC-061', 'Escola 061', 'ACTIVE', now(), now())"
        ), {"id": ids["school"]})
        connection.execute(text(
            "INSERT INTO segments (id, school_id, name, external_id) "
            "VALUES (:id, :school_id, 'Medio', 'SEG-061')"
        ), {"id": ids["segment"], "school_id": ids["school"]})
        connection.execute(text(
            "INSERT INTO grade_levels (id, school_id, segment_id, name, external_id, ordinal) "
            "VALUES (:id, :school_id, :segment_id, '3a Serie', 'GRADE-061', 0)"
        ), {"id": ids["grade_level"], "school_id": ids["school"], "segment_id": ids["segment"]})
        connection.execute(text(
            "INSERT INTO academic_years (id, school_id, year, external_id) "
            "VALUES (:id, :school_id, 2026, 'YEAR-061')"
        ), {"id": ids["year"], "school_id": ids["school"]})
        connection.execute(text(
            "INSERT INTO classes (id, school_id, academic_year_id, grade_level_id, name, external_id) "
            "VALUES (:id, :school_id, :year_id, :grade_level_id, '3A', 'TURMA-061')"
        ), {
            "id": ids["class"], "school_id": ids["school"],
            "year_id": ids["year"], "grade_level_id": ids["grade_level"],
        })
        connection.execute(text(
            "INSERT INTO essay_prompts "
            "(id, school_id, title, statement, year, created_by_external_identity, created_at, updated_at) "
            "VALUES (:id, :school_id, 'Tema', 'Disserte.', 2026, 'prof', now(), now())"
        ), {"id": ids["prompt"], "school_id": ids["school"]})
        return ids

    def test_upgrade_061_class_id_vira_opcional_e_grade_level_id_existe(self):
        config = self._alembic_config()
        command.upgrade(config, "061_essay_batch_scope")

        engine = create_engine(self.database_url)
        inspector = inspect(engine)
        columns = {c["name"]: c for c in inspector.get_columns("essay_batch_uploads")}
        self.assertIn("grade_level_id", columns)
        self.assertTrue(columns["class_id"]["nullable"])
        self.assertTrue(columns["grade_level_id"]["nullable"])
        engine.dispose()

    def test_upgrade_061_check_constraint_aceita_so_um_dos_dois_ou_nenhum(self):
        config = self._alembic_config()
        command.upgrade(config, "061_essay_batch_scope")

        engine = create_engine(self.database_url)
        with engine.begin() as connection:
            ids = self._seed_through_060(connection)

            # class_id setado, grade_level_id NULL: valido (caso de hoje).
            connection.execute(text(
                "INSERT INTO essay_batch_uploads "
                "(id, school_id, essay_prompt_id, class_id, grade_level_id, "
                " uploaded_by_external_identity, status, total_pages, created_at, updated_at) "
                "VALUES (:id, :school_id, :prompt_id, :class_id, NULL, 'prof', 'PROCESSING', 0, now(), now())"
            ), {
                "id": uuid.uuid4(), "school_id": ids["school"],
                "prompt_id": ids["prompt"], "class_id": ids["class"],
            })

            # grade_level_id setado, class_id NULL: valido (serie inteira).
            connection.execute(text(
                "INSERT INTO essay_batch_uploads "
                "(id, school_id, essay_prompt_id, class_id, grade_level_id, "
                " uploaded_by_external_identity, status, total_pages, created_at, updated_at) "
                "VALUES (:id, :school_id, :prompt_id, NULL, :grade_level_id, 'prof', 'PROCESSING', 0, now(), now())"
            ), {
                "id": uuid.uuid4(), "school_id": ids["school"],
                "prompt_id": ids["prompt"], "grade_level_id": ids["grade_level"],
            })

            # os dois NULL: valido (escola inteira).
            connection.execute(text(
                "INSERT INTO essay_batch_uploads "
                "(id, school_id, essay_prompt_id, class_id, grade_level_id, "
                " uploaded_by_external_identity, status, total_pages, created_at, updated_at) "
                "VALUES (:id, :school_id, :prompt_id, NULL, NULL, 'prof', 'PROCESSING', 0, now(), now())"
            ), {"id": uuid.uuid4(), "school_id": ids["school"], "prompt_id": ids["prompt"]})

            # os dois setados: invalido, deve estourar o CHECK.
            with self.assertRaises(IntegrityError):
                connection.execute(text(
                    "INSERT INTO essay_batch_uploads "
                    "(id, school_id, essay_prompt_id, class_id, grade_level_id, "
                    " uploaded_by_external_identity, status, total_pages, created_at, updated_at) "
                    "VALUES (:id, :school_id, :prompt_id, :class_id, :grade_level_id, 'prof', 'PROCESSING', 0, now(), now())"
                ), {
                    "id": uuid.uuid4(), "school_id": ids["school"], "prompt_id": ids["prompt"],
                    "class_id": ids["class"], "grade_level_id": ids["grade_level"],
                })
        engine.dispose()

    def test_downgrade_061_remove_grade_level_id_e_class_id_volta_not_null(self):
        config = self._alembic_config()
        command.upgrade(config, "061_essay_batch_scope")
        command.downgrade(config, "057_essay_batch_upload")

        engine = create_engine(self.database_url)
        inspector = inspect(engine)
        columns = {c["name"]: c for c in inspector.get_columns("essay_batch_uploads")}
        self.assertNotIn("grade_level_id", columns)
        self.assertFalse(columns["class_id"]["nullable"])
        engine.dispose()
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_r4_essay_batch_scope_migration_postgresql.py -v`
Expected: FAIL - `command.upgrade(config, "061_essay_batch_scope")` não encontra essa revision (ela ainda não existe). Se o Postgres de teste (porta 5433) não estiver acessível, os 3 testes são pulados (`SkipTest`) em vez de falhar - confirme que o Postgres está no ar antes de prosseguir (`psql`/conexão rápida), senão este teste nunca vai exercitar nada de verdade.

- [ ] **Step 3: Escrever a migration**

`migrations/versions/061_essay_batch_scope.py`:

```python
"""Envio em lote aceita escopo por serie ou escola inteira, nao so turma.

class_id vira opcional; grade_level_id novo (tambem opcional); um CHECK
garante exatamente um dos dois preenchido, ou os dois NULL (escola
inteira) - nunca os dois setados ao mesmo tempo. Puramente aditiva: todo
lote existente hoje ja tem class_id preenchido e grade_level_id NULL, que
e exatamente um dos casos validos do novo CHECK - nenhuma linha existente
precisa de backfill nem fica invalida.

Revision ID: 061_essay_batch_scope
Revises: 060_mass_correction_cancelling
"""

from alembic import op
import sqlalchemy as sa

revision = "061_essay_batch_scope"
down_revision = "060_mass_correction_cancelling"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "essay_batch_uploads", sa.Column("grade_level_id", sa.Uuid(), nullable=True)
    )
    op.alter_column("essay_batch_uploads", "class_id", nullable=True)
    op.create_foreign_key(
        "fk_essay_batch_uploads_school_grade_level",
        "essay_batch_uploads",
        "grade_levels",
        ["school_id", "grade_level_id"],
        ["school_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_essay_batch_uploads_scope",
        "essay_batch_uploads",
        "(class_id IS NOT NULL AND grade_level_id IS NULL) OR "
        "(class_id IS NULL AND grade_level_id IS NOT NULL) OR "
        "(class_id IS NULL AND grade_level_id IS NULL)",
    )
    op.create_index(
        "ix_essay_batch_uploads_grade_level_id", "essay_batch_uploads", ["grade_level_id"]
    )


def downgrade() -> None:
    op.drop_index("ix_essay_batch_uploads_grade_level_id", table_name="essay_batch_uploads")
    op.drop_constraint(
        "ck_essay_batch_uploads_scope", "essay_batch_uploads", type_="check"
    )
    op.drop_constraint(
        "fk_essay_batch_uploads_school_grade_level", "essay_batch_uploads", type_="foreignkey"
    )
    op.alter_column("essay_batch_uploads", "class_id", nullable=False)
    op.drop_column("essay_batch_uploads", "grade_level_id")
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_r4_essay_batch_scope_migration_postgresql.py -v`
Expected: PASS (3 testes) contra o Postgres descartável da porta 5433.

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/061_essay_batch_scope.py tests/test_r4_essay_batch_scope_migration_postgresql.py
git commit -m "feat(lote): migration - escopo turma/serie/escola em essay_batch_uploads"
```

---

### Task 2: Modelo - EssayBatchUpload.class_id opcional + grade_level_id

**Files:**
- Modify: `src/agente_ia_edu/db/models/essay_batch.py`
- Test: `tests/test_r4_essay_batch_models.py`

**Interfaces:**
- Consumes: nada de outra task (independente da migration - usa `Base.metadata.create_all` contra SQLite in-memory, não a migration real).
- Produces: `EssayBatchUpload.class_id: uuid.UUID | None`, `EssayBatchUpload.grade_level_id: uuid.UUID | None`.

- [ ] **Step 1: Ler o teste de modelo existente**

Run: `grep -n "class EssayBatchUploadModelTests\|class_id" tests/test_r4_essay_batch_models.py`

Leia a classe de teste encontrada antes de mexer - o teste novo precisa seguir o mesmo padrão de sessão SQLite in-memory que o arquivo já usa.

- [ ] **Step 2: Escrever o teste falho**

Adicionar ao final da classe de teste de `EssayBatchUpload` em `tests/test_r4_essay_batch_models.py` (mesmo padrão `IsolatedAsyncioTestCase` + engine SQLite in-memory do arquivo):

```python
    async def test_class_id_e_opcional_quando_grade_level_id_esta_setado(self):
        school = School(id=uuid.uuid4(), code="MOD-1", name="Escola")
        self.session.add(school)
        await self.session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-MOD")
        self.session.add(segment)
        await self.session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="3a", external_id="GRADE-MOD",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof",
        )
        self.session.add_all([grade, prompt])
        await self.session.flush()

        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=None, grade_level_id=grade.id,
            uploaded_by_external_identity="prof", status="PROCESSING", total_pages=0,
        )
        self.session.add(batch)
        await self.session.flush()

        fetched = await self.session.get(EssayBatchUpload, batch.id)
        self.assertIsNone(fetched.class_id)
        self.assertEqual(fetched.grade_level_id, grade.id)

    async def test_class_id_e_grade_level_id_juntos_violam_o_check(self):
        from sqlalchemy.exc import IntegrityError

        school = School(id=uuid.uuid4(), code="MOD-2", name="Escola")
        self.session.add(school)
        await self.session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-MOD2")
        self.session.add(segment)
        await self.session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="3a", external_id="GRADE-MOD2",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-MOD2")
        self.session.add_all([grade, year])
        await self.session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="3A", external_id="TURMA-MOD2",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof",
        )
        self.session.add_all([klass, prompt])
        await self.session.flush()

        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, grade_level_id=grade.id,
            uploaded_by_external_identity="prof", status="PROCESSING", total_pages=0,
        )
        self.session.add(batch)
        with self.assertRaises(IntegrityError):
            await self.session.flush()
```

Se `AcademicYear`/`Class`/`GradeLevel`/`EssayPrompt`/`School`/`Segment` ainda não estiverem importados no topo do arquivo de teste, adicione ao import existente de `agente_ia_edu.db.models`.

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_r4_essay_batch_models.py -v -k "class_id_e"`
Expected: FAIL - `class_id=None` ainda viola `nullable=False` no modelo atual (`sqlalchemy.exc.IntegrityError` ou erro de validação, mas pelo motivo ERRADO - antes mesmo de chegar no CHECK novo, que ainda nem existe).

- [ ] **Step 4: Editar o modelo**

Em `src/agente_ia_edu/db/models/essay_batch.py`, dentro de `class EssayBatchUpload`:

Trocar:
```python
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
```

Por:
```python
        ForeignKeyConstraint(
            ["school_id", "class_id"],
            ["classes.school_id", "classes.id"],
            ondelete="RESTRICT",
            name="fk_essay_batch_uploads_school_class",
        ),
        ForeignKeyConstraint(
            ["school_id", "grade_level_id"],
            ["grade_levels.school_id", "grade_levels.id"],
            ondelete="RESTRICT",
            name="fk_essay_batch_uploads_school_grade_level",
        ),
        UniqueConstraint("school_id", "id", name="uq_essay_batch_uploads_school_id_id"),
        CheckConstraint(
            "status IN ('PROCESSING', 'DONE')", name="ck_essay_batch_uploads_status"
        ),
        CheckConstraint(
            "(class_id IS NOT NULL AND grade_level_id IS NULL) OR "
            "(class_id IS NULL AND grade_level_id IS NOT NULL) OR "
            "(class_id IS NULL AND grade_level_id IS NULL)",
            name="ck_essay_batch_uploads_scope",
        ),
```

E trocar:
```python
    class_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
```

Por:
```python
    class_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    grade_level_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
```

(mantendo a linha logo abaixo, `uploaded_by_external_identity: Mapped[str] = ...`, intocada - só insira `grade_level_id` como uma nova linha entre `class_id` e `uploaded_by_external_identity`).

Também adicionar `Index("ix_essay_batch_uploads_grade_level_id", "grade_level_id"),` junto dos outros `Index(...)` em `__table_args__`.

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_r4_essay_batch_models.py -v`
Expected: PASS, incluindo os testes já existentes (regressão: nenhum teste antigo quebrou).

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/db/models/essay_batch.py tests/test_r4_essay_batch_models.py
git commit -m "feat(lote): modelo - class_id opcional, grade_level_id novo"
```

---

### Task 3: `roster_for_batch` - roster com escopo turma/serie/escola

**Files:**
- Modify: `src/agente_ia_edu/services/essay_batch.py`
- Create: `tests/test_r4_essay_batch_roster_scope.py`

**Interfaces:**
- Consumes: `EssayBatchUpload.class_id`/`grade_level_id` (Task 2); `class_roster` (já existente, intocada).
- Produces: `EssayBatchService.roster_for_batch(self, batch: EssayBatchUpload) -> list[tuple[uuid.UUID, str, str | None]]`.

- [ ] **Step 1: Escrever o teste falho**

`tests/test_r4_essay_batch_roster_scope.py`:

```python
"""R4 lote - roster_for_batch: escopo turma/serie/escola do casamento
automatico de aluno.

match_student e _assignment_for_student nao sao tocados nesta leva - so o
TAMANHO do roster que chega neles muda. Este arquivo testa so o roster em
si, nao o match (ja coberto em test_r4_essay_batch_matching.py).
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear, Class, EssayBatchUpload, EssayPrompt, GradeLevel, Person,
    School, Segment, Student, StudentEnrollment,
)
from agente_ia_edu.services.essay_batch import EssayBatchService


class RosterForBatchTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.session = self.session_factory()

    async def asyncTearDown(self):
        await self.session.close()
        await self.engine.dispose()

    async def _seed(self):
        """2 turmas da MESMA serie (A e B) + 1 turma de OUTRA serie (C),
        1 aluno ativo em cada, mais 1 aluno INATIVO na turma A (nunca deve
        aparecer em roster nenhum)."""
        school = School(id=uuid.uuid4(), code="RS-1", name="Escola")
        self.session.add(school)
        await self.session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-RS")
        self.session.add(segment)
        await self.session.flush()
        grade_x = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="Serie X", external_id="GRADE-RS-X",
        )
        grade_y = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="Serie Y", external_id="GRADE-RS-Y",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-RS")
        self.session.add_all([grade_x, grade_y, year])
        await self.session.flush()

        class_a = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade_x.id, name="Turma A", external_id="TURMA-RS-A",
        )
        class_b = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade_x.id, name="Turma B", external_id="TURMA-RS-B",
        )
        class_c = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade_y.id, name="Turma C", external_id="TURMA-RS-C",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof",
        )
        self.session.add_all([class_a, class_b, class_c, prompt])
        await self.session.flush()

        def _make_student(nome: str, klass, status: str = "ACTIVE") -> uuid.UUID:
            person = Person(id=uuid.uuid4(), full_name=nome)
            self.session.add(person)
            student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id)
            self.session.add(student)
            enrollment = StudentEnrollment(
                id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                class_id=klass.id, status=status,
            )
            self.session.add(enrollment)
            return student.id

        student_a = _make_student("Aluno Turma A", class_a)
        student_b = _make_student("Aluno Turma B", class_b)
        student_c = _make_student("Aluno Turma C", class_c)
        student_inativo = _make_student("Aluno Inativo", class_a, status="INACTIVE")
        await self.session.flush()

        return {
            "school": school, "prompt": prompt,
            "class_a": class_a, "class_b": class_b, "class_c": class_c,
            "grade_x": grade_x, "grade_y": grade_y,
            "student_a": student_a, "student_b": student_b, "student_c": student_c,
            "student_inativo": student_inativo,
        }

    async def test_escopo_turma_delega_para_class_roster(self):
        seed = await self._seed()
        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
            class_id=seed["class_a"].id, grade_level_id=None,
            uploaded_by_external_identity="prof", status="PROCESSING", total_pages=0,
        )
        service = EssayBatchService(self.session)
        roster = await service.roster_for_batch(batch)
        ids = {student_id for student_id, _, _ in roster}
        self.assertEqual(ids, {seed["student_a"]})

    async def test_escopo_serie_inclui_turmas_a_e_b_mas_nao_c(self):
        seed = await self._seed()
        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
            class_id=None, grade_level_id=seed["grade_x"].id,
            uploaded_by_external_identity="prof", status="PROCESSING", total_pages=0,
        )
        service = EssayBatchService(self.session)
        roster = await service.roster_for_batch(batch)
        ids = {student_id for student_id, _, _ in roster}
        self.assertEqual(ids, {seed["student_a"], seed["student_b"]})

    async def test_escopo_escola_inclui_as_3_turmas(self):
        seed = await self._seed()
        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
            class_id=None, grade_level_id=None,
            uploaded_by_external_identity="prof", status="PROCESSING", total_pages=0,
        )
        service = EssayBatchService(self.session)
        roster = await service.roster_for_batch(batch)
        ids = {student_id for student_id, _, _ in roster}
        self.assertEqual(ids, {seed["student_a"], seed["student_b"], seed["student_c"]})

    async def test_matricula_inativa_nunca_aparece_em_nenhum_escopo(self):
        seed = await self._seed()
        for class_id, grade_level_id in (
            (seed["class_a"].id, None), (None, seed["grade_x"].id), (None, None),
        ):
            batch = EssayBatchUpload(
                id=uuid.uuid4(), school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_id=class_id, grade_level_id=grade_level_id,
                uploaded_by_external_identity="prof", status="PROCESSING", total_pages=0,
            )
            service = EssayBatchService(self.session)
            roster = await service.roster_for_batch(batch)
            ids = {student_id for student_id, _, _ in roster}
            self.assertNotIn(seed["student_inativo"], ids)
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_r4_essay_batch_roster_scope.py -v`
Expected: FAIL com `AttributeError: 'EssayBatchService' object has no attribute 'roster_for_batch'`.

- [ ] **Step 3: Implementar `roster_for_batch`**

Em `src/agente_ia_edu/services/essay_batch.py`, adicionar `Class` ao import existente de `..db.models`:

```python
from ..db.models import (
    Class, EssayBatchPage, EssayBatchUpload, EssayCorrection, PromptAssignment, Person, Student,
    StudentEnrollment, EssaySubmission,
)
```

Adicionar o método logo depois de `class_roster` (depois da linha que fecha o `return` de `class_roster`, ainda dentro da mesma classe de serviço):

```python
    async def roster_for_batch(
        self, batch: EssayBatchUpload
    ) -> list[tuple[uuid.UUID, str, str | None]]:
        """Roster de alunos ativos no escopo do lote: a turma unica (delega
        pra class_roster, sem duplicar a query), todas as turmas da serie
        (grade_level_id), ou a escola inteira (nenhum dos dois) - mesma
        forma de retorno de class_roster em qualquer um dos 3 casos."""
        if batch.class_id is not None:
            return await self.class_roster(school_id=batch.school_id, class_id=batch.class_id)

        condicoes = [
            StudentEnrollment.school_id == batch.school_id,
            StudentEnrollment.status == "ACTIVE",
        ]
        if batch.grade_level_id is not None:
            condicoes.append(
                StudentEnrollment.class_id.in_(
                    select(Class.id).where(
                        Class.school_id == batch.school_id,
                        Class.grade_level_id == batch.grade_level_id,
                    )
                )
            )
        rows = (await self.session.execute(
            select(StudentEnrollment.student_id, Person.full_name, Person.document_number)
            .join(Student, Student.id == StudentEnrollment.student_id)
            .join(Person, Person.id == Student.person_id)
            .where(*condicoes)
            .order_by(Person.full_name)
        )).all()
        return [
            (student_id, full_name, document_number)
            for student_id, full_name, document_number in rows
        ]
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_r4_essay_batch_roster_scope.py -v`
Expected: PASS (4 testes).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/essay_batch.py tests/test_r4_essay_batch_roster_scope.py
git commit -m "feat(lote): roster_for_batch - escopo turma/serie/escola"
```

---

### Task 4: `create_batch` aceita `class_id` OU `grade_level_id`

**Files:**
- Modify: `src/agente_ia_edu/services/essay_batch.py`
- Test: `tests/test_r4_essay_batch_create.py`

**Interfaces:**
- Consumes: `EssayBatchUpload.grade_level_id` (Task 2).
- Produces: `EssayBatchService.create_batch(..., class_id: uuid.UUID | None = None, grade_level_id: uuid.UUID | None = None, ...) -> dict` (dict agora inclui a chave `"grade_level_id"`).

- [ ] **Step 1: Ler os testes existentes de `create_batch`**

Run: `grep -n "async def test_\|_seed" tests/test_r4_essay_batch_create.py`

Leia a função `_seed` e pelo menos 2 testes existentes antes de mexer - os testes novos reaproveitam o mesmo `_seed`, e os testes JÁ existentes (escopo turma, com `_assignment_for_class_or_raise` rodando) precisam continuar passando sem alteração (regressão).

- [ ] **Step 2: Escrever os testes falhos**

Adicionar ao final de `tests/test_r4_essay_batch_create.py` (mesma classe `CreateBatchTests`, reaproveitando `self._seed`):

```python
    async def test_cria_lote_com_escopo_serie_sem_checar_atribuicao_antecipada(self):
        async with self.session_factory() as session:
            seed = await self._seed(session, assign=False)
            service = EssayBatchService(session, transcriber=None, storage=self.storage)
            source = _write_image(self.tmp_dir / "folha.png")
            created = await service.create_batch(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_id=None, grade_level_id=seed["grade"].id,
                uploaded_by_external_identity="prof", source_paths=[source],
            )
            self.assertIsNone(created["class_id"])
            self.assertEqual(created["grade_level_id"], seed["grade"].id)

    async def test_cria_lote_com_escopo_escola_inteira(self):
        async with self.session_factory() as session:
            seed = await self._seed(session, assign=False)
            service = EssayBatchService(session, transcriber=None, storage=self.storage)
            source = _write_image(self.tmp_dir / "folha.png")
            created = await service.create_batch(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_id=None, grade_level_id=None,
                uploaded_by_external_identity="prof", source_paths=[source],
            )
            self.assertIsNone(created["class_id"])
            self.assertIsNone(created["grade_level_id"])

    async def test_class_id_e_grade_level_id_juntos_e_erro(self):
        async with self.session_factory() as session:
            seed = await self._seed(session, assign=True)
            service = EssayBatchService(session, transcriber=None, storage=self.storage)
            source = _write_image(self.tmp_dir / "folha.png")
            with self.assertRaises(ValueError):
                await service.create_batch(
                    school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                    class_id=seed["class"].id, grade_level_id=seed["grade"].id,
                    uploaded_by_external_identity="prof", source_paths=[source],
                )
```

Verifique o nome exato das chaves do dict devolvido por `self._seed` lendo a função (o brief chama de `seed["grade"]`/`seed["class"]`/`seed["school"]`/`seed["prompt"]` - confirme contra o código real antes de rodar; ajuste os nomes usados acima se o `_seed` existente usar chaves diferentes, mas NÃO mude a estrutura do teste).

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_r4_essay_batch_create.py -v -k "escopo or juntos"`
Expected: FAIL - `create_batch` ainda não aceita `grade_level_id` (`TypeError: create_batch() got an unexpected keyword argument 'grade_level_id'`).

- [ ] **Step 4: Editar `create_batch`**

Em `src/agente_ia_edu/services/essay_batch.py`, trocar a assinatura e o corpo de `create_batch`:

```python
    async def create_batch(
        self,
        *,
        school_id: uuid.UUID,
        essay_prompt_id: uuid.UUID,
        class_id: uuid.UUID | None = None,
        grade_level_id: uuid.UUID | None = None,
        uploaded_by_external_identity: str,
        source_paths: list[Path],
    ) -> dict:
        """Cria o lote em PROCESSING com TODAS as suas paginas ja gravadas em
        MaterialStorage, e devolve os campos como dict simples.

        class_id e grade_level_id sao mutuamente exclusivos - nenhum dos
        dois (escola inteira), so class_id (turma unica, com checagem
        antecipada de atribuicao como sempre) ou so grade_level_id (serie
        inteira, sem checagem antecipada: cada corrida resolve a
        atribuicao pela turma REAL do aluno matched via
        _assignment_for_student, que ja trata gracilmente um aluno sem
        atribuicao valida marcando a pagina NEEDS_REVIEW sem derrubar o
        lote inteiro - ver _materialize_run).

        Os limites sao checados ANTES de qualquer escrita, pra que um envio
        recusado nao deixe meio lote no banco. total_pages e fixado aqui (os
        PDFs ja vem separados em paginas), entao o progresso do processamento
        e sempre legivel como paginas_com_status_final/total_pages.

        Devolve dict e nao o objeto ORM pelo motivo de sempre neste projeto: a
        rota commita logo em seguida e expire_on_commit=True faria o proximo
        acesso a um atributo do objeto virar MissingGreenlet.
        """
        if class_id is not None and grade_level_id is not None:
            raise ValueError(
                "Escolha turma ou serie, nunca as duas - ou nenhuma para a escola inteira."
            )
        if class_id is not None:
            await self._assignment_for_class_or_raise(
                school_id=school_id, essay_prompt_id=essay_prompt_id, class_id=class_id
            )
        page_images = await asyncio.to_thread(self._expand_to_page_images, source_paths)

        batch = EssayBatchUpload(
            id=uuid.uuid4(), school_id=school_id, essay_prompt_id=essay_prompt_id,
            class_id=class_id, grade_level_id=grade_level_id,
            uploaded_by_external_identity=uploaded_by_external_identity,
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
            "grade_level_id": batch.grade_level_id,
            "status": batch.status, "total_pages": batch.total_pages,
        }
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_r4_essay_batch_create.py -v`
Expected: PASS, incluindo todos os testes já existentes antes desta task (regressão).

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/essay_batch.py tests/test_r4_essay_batch_create.py
git commit -m "feat(lote): create_batch aceita class_id ou grade_level_id"
```

---

### Task 5: `process_batch` e `get_batch_status` usam `roster_for_batch`

**Files:**
- Modify: `src/agente_ia_edu/services/essay_batch.py`
- Test: `tests/test_r4_essay_batch_processing.py`, `tests/test_r4_essay_batch_status.py`

**Interfaces:**
- Consumes: `roster_for_batch` (Task 3), `create_batch` com `grade_level_id` (Task 4).
- Produces: nenhuma interface nova - só troca a fonte do roster nos 2 call sites, e `get_batch_status` passa a incluir `"grade_level_id"` no dict devolvido.

- [ ] **Step 1: Ler os testes existentes**

Run: `grep -n "async def test_\|_seed" tests/test_r4_essay_batch_processing.py tests/test_r4_essay_batch_status.py`

Leia pelo menos um teste de cada arquivo e a respectiva função `_seed` antes de escrever os testes novos - reaproveite exatamente o mesmo padrão de seed (escolas, turmas, provider de transcrição falso) já estabelecido neles.

- [ ] **Step 2: Escrever o teste falho de ponta a ponta com escopo série**

Adicionar a `tests/test_r4_essay_batch_processing.py` (mesma classe de teste que já exercita `process_batch`, reaproveitando o transcritor falso que o arquivo já usa):

```python
    async def test_escopo_serie_casa_aluno_de_qualquer_turma_da_serie_e_atribui_a_turma_real(self):
        """2 turmas (A e B) da mesma serie, 1 aluno com nome unico em cada.
        Lote com escopo = serie inteira (grade_level_id, sem class_id): as
        duas paginas casam automatico (nome unico NA SERIE INTEIRA), e cada
        submissao fica atribuida a turma REAL do aluno (A ou B, nunca a
        'turma do lote', que nem existe neste escopo) - confirma que
        _assignment_for_student continua correto sem nenhuma mudanca."""
        async with self.session_factory() as session:
            seed = await self._seed_two_classes_same_grade(session)  # ver Step 3
            service = EssayBatchService(
                session, transcriber=self._fake_transcriber({
                    "pagina_aluno_a.png": ("ALUNO TURMA A", None, "texto da redacao A"),
                    "pagina_aluno_b.png": ("ALUNO TURMA B", None, "texto da redacao B"),
                }),
                storage=self.storage,
            )
            created = await service.create_batch(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_id=None, grade_level_id=seed["grade"].id,
                uploaded_by_external_identity="prof",
                source_paths=[
                    _write_image(self.tmp_dir / "pagina_aluno_a.png", "ALUNO TURMA A"),
                    _write_image(self.tmp_dir / "pagina_aluno_b.png", "ALUNO TURMA B"),
                ],
            )
            await session.commit()
            await service.process_batch(created["id"])

            status = await service.get_batch_status(created["id"])
            self.assertEqual(status["matched_count"], 2)
            self.assertEqual(status["needs_review_count"], 0)

    async def test_escopo_serie_homonimos_em_turmas_diferentes_caem_em_revisao_manual(self):
        """Mesmo cenario, mas os dois alunos tem o MESMO nome normalizado
        (homonimos em turmas diferentes da mesma serie) - match_student ve
        2 candidatos no roster ampliado e NUNCA desempata sozinho (nem por
        CPF): as duas paginas devem ficar NEEDS_REVIEW, nunca um match
        errado. match_student em si nao muda nesta leva - este teste prova
        que o roster maior nao quebra essa garantia."""
        async with self.session_factory() as session:
            seed = await self._seed_two_classes_same_grade(
                session, nome_aluno_a="Maria Silva", nome_aluno_b="Maria Silva"
            )
            service = EssayBatchService(
                session, transcriber=self._fake_transcriber({
                    "pagina1.png": ("MARIA SILVA", None, "texto da redacao 1"),
                }),
                storage=self.storage,
            )
            created = await service.create_batch(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_id=None, grade_level_id=seed["grade"].id,
                uploaded_by_external_identity="prof",
                source_paths=[_write_image(self.tmp_dir / "pagina1.png", "MARIA SILVA")],
            )
            await session.commit()
            await service.process_batch(created["id"])

            status = await service.get_batch_status(created["id"])
            self.assertEqual(status["matched_count"], 0)
            self.assertEqual(status["needs_review_count"], 1)
```

Esta task NÃO precisa reimplementar o fake transcriber do zero: leia como o arquivo já injeta um transcritor falso (procure por `_fake_transcriber` ou equivalente em `tests/test_r4_essay_batch_processing.py`) e reaproveite a mesma assinatura/convenção.

- [ ] **Step 3: Adicionar o seed de 2 turmas da mesma série**

Adicionar à mesma classe de teste um helper novo (ao lado do `_seed` já existente, sem alterá-lo):

```python
    async def _seed_two_classes_same_grade(
        self, session, *, nome_aluno_a: str = "Aluno Turma A", nome_aluno_b: str = "Aluno Turma B"
    ) -> dict:
        school = School(id=uuid.uuid4(), code="PB-SERIE", name="Escola")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-PB-SERIE")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="Serie Unica", external_id="GRADE-PB-SERIE",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-PB-SERIE")
        session.add_all([grade, year])
        await session.flush()
        class_a = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="Turma A", external_id="TURMA-PB-A",
        )
        class_b = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="Turma B", external_id="TURMA-PB-B",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof",
        )
        session.add_all([class_a, class_b, prompt])
        await session.flush()

        def _make_student(nome, klass):
            person = Person(id=uuid.uuid4(), full_name=nome)
            session.add(person)
            student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id)
            session.add(student)
            session.add(StudentEnrollment(
                id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                class_id=klass.id, status="ACTIVE",
            ))
            session.add(PromptAssignment(
                id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                class_id=klass.id,
            ))
            return student.id

        student_a = _make_student(nome_aluno_a, class_a)
        student_b = _make_student(nome_aluno_b, class_b)
        await session.flush()
        return {
            "school": school, "prompt": prompt, "grade": grade,
            "class_a": class_a, "class_b": class_b,
            "student_a": student_a, "student_b": student_b,
        }
```

Ajuste os imports no topo do arquivo de teste (`AcademicYear, Class, EssayPrompt, GradeLevel, Person, PromptAssignment, School, Segment, Student, StudentEnrollment`) se algum destes ainda não estiver importado - confirme contra o import existente antes de duplicar.

- [ ] **Step 4: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_r4_essay_batch_processing.py -v -k escopo_serie`
Expected: FAIL - `process_batch` ainda chama `class_roster(class_id=batch.class_id)` direto, e `batch.class_id` é `None` neste cenário, então o roster vem vazio e as duas páginas ficam `NEEDS_REVIEW` em vez de casar (o teste espera `matched_count == 2`).

- [ ] **Step 5: Trocar os 2 call sites**

Em `src/agente_ia_edu/services/essay_batch.py`, dentro de `process_batch`, trocar:

```python
        roster = await self.class_roster(school_id=batch.school_id, class_id=batch.class_id)
```

Por:

```python
        roster = await self.roster_for_batch(batch)
```

E dentro de `get_batch_status`, trocar a linha equivalente (mesma troca, mesmo texto exato) e adicionar `"grade_level_id"` ao dict de retorno, logo depois de `"class_id": batch.class_id,`:

```python
        return {
            "id": batch.id,
            "school_id": batch.school_id,
            "essay_prompt_id": batch.essay_prompt_id,
            "class_id": batch.class_id,
            "grade_level_id": batch.grade_level_id,
            "status": batch.status,
```

(as linhas seguintes do dict - `total_pages`, `matched_count`, etc. - continuam exatamente como estão hoje, não precisam mudar).

- [ ] **Step 6: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_r4_essay_batch_processing.py tests/test_r4_essay_batch_status.py -v`
Expected: PASS, incluindo todos os testes já existentes nos dois arquivos (regressão - o caso de turma única não pode quebrar).

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/services/essay_batch.py tests/test_r4_essay_batch_processing.py
git commit -m "feat(lote): process_batch e get_batch_status usam roster_for_batch"
```

---

### Task 6: API - `class_id` OU `grade_level_id` + rota de listagem de séries

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_batches.py`
- Test: `tests/test_r4_essay_batches_routes.py`

**Interfaces:**
- Consumes: `create_batch(..., grade_level_id=...)` (Task 4), `GradeLevel` (modelo já existente).
- Produces: `POST /api/v1/teacher/essay-batches` aceita `class_id` OU `grade_level_id` (Form, ambos opcionais); `GET /api/v1/teacher/essay-batches/grade-levels` novo, devolve `[{"id": str, "name": str}, ...]`.

- [ ] **Step 1: Ler os testes de rota existentes**

Run: `grep -n "async def test_\|def _client\|class_id" tests/test_r4_essay_batches_routes.py | head -40`

Leia como os testes existentes montam o cliente de teste e autenticam antes de escrever os novos - reaproveite exatamente o mesmo padrão (mesmo fixture/helper de autenticação).

- [ ] **Step 2: Escrever os testes falhos**

Adicionar a `tests/test_r4_essay_batches_routes.py`:

```python
    async def test_post_lote_com_grade_level_id_em_vez_de_class_id(self):
        # reaproveita o mesmo seed/cliente autenticado que os testes de
        # criacao de lote ja existentes neste arquivo usam, so troca o
        # campo do form de class_id para grade_level_id.
        seed = await self._seed_school_with_grade_level_no_class_assignment()
        response = await self.client.post(
            "/api/v1/teacher/essay-batches",
            data={
                "essay_prompt_id": str(seed["prompt_id"]),
                "grade_level_id": str(seed["grade_level_id"]),
            },
            files={"files": ("folha.png", self._fake_png_bytes(), "image/png")},
            headers=self._auth_headers(seed["school_id"]),
        )
        self.assertEqual(response.status_code, 202)
        body = response.json()
        self.assertIsNone(body["class_id"])
        self.assertEqual(body["grade_level_id"], str(seed["grade_level_id"]))

    async def test_post_lote_com_class_id_e_grade_level_id_juntos_e_422(self):
        seed = await self._seed_school_with_grade_level_no_class_assignment()
        response = await self.client.post(
            "/api/v1/teacher/essay-batches",
            data={
                "essay_prompt_id": str(seed["prompt_id"]),
                "class_id": str(seed.get("class_id") or seed["grade_level_id"]),
                "grade_level_id": str(seed["grade_level_id"]),
            },
            files={"files": ("folha.png", self._fake_png_bytes(), "image/png")},
            headers=self._auth_headers(seed["school_id"]),
        )
        self.assertEqual(response.status_code, 422)

    async def test_get_grade_levels_lista_as_series_da_escola(self):
        seed = await self._seed_school_with_grade_level_no_class_assignment()
        response = await self.client.get(
            "/api/v1/teacher/essay-batches/grade-levels",
            headers=self._auth_headers(seed["school_id"]),
        )
        self.assertEqual(response.status_code, 200)
        ids = {item["id"] for item in response.json()}
        self.assertIn(str(seed["grade_level_id"]), ids)
```

Ajuste os nomes dos helpers (`self.client`, `self._auth_headers`, `self._fake_png_bytes`) para bater exatamente com os que o arquivo já usa (lidos no Step 1) - estes nomes são ilustrativos da forma, não necessariamente os literais do arquivo real. Adicione um helper `_seed_school_with_grade_level_no_class_assignment` que reaproveita o padrão de seed já existente no arquivo, criando uma escola com um `GradeLevel` mas SEM nenhuma turma atribuída à proposta (já que o escopo série não exige atribuição antecipada).

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_r4_essay_batches_routes.py -v -k "grade_level or juntos_e_422"`
Expected: FAIL - a rota ainda exige `class_id` obrigatório (`Form(...)`) e não existe rota `/grade-levels`.

- [ ] **Step 4: Editar a rota**

Em `src/agente_ia_edu/api/routes/essay_batches.py`:

Adicionar aos imports:

```python
from sqlalchemy import select

from ...db.models import EssayBatchPage, EssayBatchUpload, GradeLevel, School
```

(troca a linha de import existente `from ...db.models import EssayBatchPage, EssayBatchUpload, School` por essa, acrescentando `GradeLevel`, e adiciona a linha nova `from sqlalchemy import select` logo acima dela).

Trocar os 2 modelos Pydantic de resposta:

```python
class EssayBatchCreatedResponse(BaseModel):
    id: UUID
    school_id: UUID
    essay_prompt_id: UUID
    class_id: Optional[UUID] = None
    grade_level_id: Optional[UUID] = None
    status: str
    total_pages: int
```

```python
class EssayBatchStatusResponse(BaseModel):
    id: UUID
    school_id: UUID
    essay_prompt_id: UUID
    class_id: Optional[UUID] = None
    grade_level_id: Optional[UUID] = None
    status: str
    total_pages: int
    matched_count: int
    needs_review_count: int
    processed_count: int
    needs_review_pages: list[EssayBatchNeedsReviewPage]
    available_students: list[EssayBatchAvailableStudent]
```

Trocar a assinatura de `create_essay_batch` e a validação de escopo:

```python
@essay_batches_router.post("", status_code=202, response_model=EssayBatchCreatedResponse)
async def create_essay_batch(
    background_tasks: BackgroundTasks,
    essay_prompt_id: UUID = Form(...),
    class_id: Optional[UUID] = Form(None),
    grade_level_id: Optional[UUID] = Form(None),
    files: list[UploadFile] = File(...),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayBatchCreatedResponse:
    """Devolve 202 assim que o lote esta gravado, SEM esperar o processamento -
    o professor acompanha por GET /{batch_id} (spec s5).

    class_id e grade_level_id sao mutuamente exclusivos (turma unica, serie
    inteira, ou nenhum dos dois = escola inteira) - checado aqui ANTES de
    processar qualquer arquivo, pra falhar rapido em vez de so depois de
    receber ate 60 paginas de foto.

    Streaming em pedacos de 1MB pra um arquivo temporario, checando o tamanho a
    cada pedaco, exatamente como upload_prompt_material ja faz: um upload
    gigante nunca chega a virar um arquivo em MaterialStorage.
    """
    if class_id is not None and grade_level_id is not None:
        raise HTTPException(
            status_code=422,
            detail="Escolha turma ou serie, nunca as duas - ou nenhuma para a escola inteira.",
        )
    async with session_factory() as session:
```

(o resto do corpo da função, de `school_id = await _authorize(...)` até o fim, continua igual - só a chamada a `create_batch` ganha o argumento novo):

```python
            try:
                created = await build_batch_service(session).create_batch(
                    school_id=school_id, essay_prompt_id=essay_prompt_id,
                    class_id=class_id, grade_level_id=grade_level_id,
                    uploaded_by_external_identity=identity.external_user_id,
                    source_paths=source_paths,
                )
            except ValueError as exc:
                raise HTTPException(status_code=422, detail=str(exc)) from exc
```

Inserir a rota nova **ANTES** de `@essay_batches_router.get("/{batch_id}", ...)` (ordem importa: FastAPI casa rotas na ordem de registro, e `/{batch_id}` é um segmento dinâmico que casaria com "grade-levels" literalmente como se fosse um batch_id, nunca deixando a rota nova ser alcançada, se ela vier depois):

```python
@essay_batches_router.get("/grade-levels")
async def list_grade_levels_for_batch(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[dict]:
    """Series (GradeLevel) da escola do professor, pro seletor de abrangencia
    do envio em lote - dado real (GradeLevel.name), diferente do campo
    grade_level hardcoded que /api/v1/teacher/classrooms expoe hoje (bug
    pre-existente, fora de escopo desta rota)."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        rows = (await session.execute(
            select(GradeLevel.id, GradeLevel.name)
            .where(GradeLevel.school_id == school_id)
            .order_by(GradeLevel.ordinal)
        )).all()
        return [{"id": str(row.id), "name": row.name} for row in rows]


@essay_batches_router.get("/{batch_id}", response_model=EssayBatchStatusResponse)
```

(a linha `@essay_batches_router.get("/{batch_id}", response_model=EssayBatchStatusResponse)` já existe no arquivo - não duplique a função `get_essay_batch` abaixo dela, só garanta que a rota nova fica ANTES dessa linha no arquivo).

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_r4_essay_batches_routes.py -v`
Expected: PASS, incluindo todos os testes já existentes (regressão).

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_batches.py tests/test_r4_essay_batches_routes.py
git commit -m "feat(lote): API aceita grade_level_id + rota de listagem de series"
```

---

### Task 7: Frontend - seletor de abrangência (Turma | Série | Escola inteira)

**Files:**
- Modify: `src/agente_ia_edu/web/essay-review.js`
- Test: `tests/test_r4_essay_batch_scope_frontend.js`

**Interfaces:**
- Consumes: `GET /api/v1/teacher/essay-batches/grade-levels` (Task 6).
- Produces: nenhuma interface nova - só o formulário da aba "Enviar em lote".

- [ ] **Step 1: Escrever o teste falho**

`tests/test_r4_essay_batch_scope_frontend.js` (mesmo padrão de teste-contra-texto-fonte que `test_r4_essay_batch_progress_frontend.js` já usa, já que `essay-review.js` roda num IIFE sem `module.exports`):

```javascript
// Contrato de frontend do seletor de abrangencia (Turma | Serie | Escola
// inteira) da aba "Enviar em lote" de essay-review.js.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const js = fs.readFileSync('src/agente_ia_edu/web/essay-review.js', 'utf8');
const batchTab = js.slice(
  js.indexOf('async function renderBatchTab'),
  js.indexOf('async function pollBatch'),
);

test('o formulario de lote busca as series (grade-levels) da escola', () => {
  assert.match(batchTab, /\/api\/v1\/teacher\/essay-batches\/grade-levels/);
});

test('o formulario tem um seletor de abrangencia com as 3 opcoes', () => {
  assert.match(batchTab, /er-batch-scope/);
  assert.match(batchTab, /value="turma"/);
  assert.match(batchTab, /value="serie"/);
  assert.match(batchTab, /value="escola"/);
});

test('o campo de turma nao e mais obrigatorio incondicionalmente', () => {
  // antes: <select id="er-batch-class" class="text-input" required>
  // depois: a obrigatoriedade passa a ser validada em JS conforme o
  // escopo selecionado, nao via atributo HTML fixo - senao o form nunca
  // submeteria com escopo serie/escola (o campo de turma ficaria
  // escondido mas ainda "required").
  assert.doesNotMatch(batchTab, /id="er-batch-class" class="text-input" required/);
});

test('o envio manda grade_level_id quando o escopo e serie, nao class_id', () => {
  assert.match(batchTab, /formData\.append\('grade_level_id'/);
});
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `node --test tests/test_r4_essay_batch_scope_frontend.js`
Expected: FAIL (os 4 testes) - nenhuma das strings esperadas existe ainda em `essay-review.js`.

- [ ] **Step 3: Editar o formulário**

Em `src/agente_ia_edu/web/essay-review.js`, dentro de `renderBatchTab`:

Trocar:

```javascript
    let promptOptions = [];
    let classrooms = [];
    try {
      promptOptions = await reviewRequest('/api/v1/catalog/essay-prompts');
    } catch (e) {
      promptOptions = [];
    }
```

Por:

```javascript
    let promptOptions = [];
    let classrooms = [];
    let gradeLevels = [];
    try {
      promptOptions = await reviewRequest('/api/v1/catalog/essay-prompts');
    } catch (e) {
      promptOptions = [];
    }
    try {
      gradeLevels = await reviewRequest('/api/v1/teacher/essay-batches/grade-levels');
    } catch (e) {
      gradeLevels = [];
    }
```

Trocar o bloco do campo "Turma" (dentro do template literal do formulário):

```html
          <div class="form-group">
            <label for="er-batch-class">Turma</label>
            <select id="er-batch-class" class="text-input" required>
              ${assignableClassrooms.map((c) => `<option value="${tmEsc(c.class_id)}">${tmEsc(c.name)}</option>`).join('')}
            </select>
          </div>
```

Por:

```html
          <div class="form-group">
            <label for="er-batch-scope">Abrangência</label>
            <select id="er-batch-scope" class="text-input">
              <option value="turma">Uma turma</option>
              <option value="serie">Uma série inteira</option>
              <option value="escola">A escola inteira</option>
            </select>
          </div>
          <div class="form-group" id="er-batch-class-group">
            <label for="er-batch-class">Turma</label>
            <select id="er-batch-class" class="text-input">
              ${assignableClassrooms.map((c) => `<option value="${tmEsc(c.class_id)}">${tmEsc(c.name)}</option>`).join('')}
            </select>
          </div>
          <div class="form-group" id="er-batch-grade-group" hidden>
            <label for="er-batch-grade-level">Série</label>
            <select id="er-batch-grade-level" class="text-input">
              ${gradeLevels.map((g) => `<option value="${tmEsc(g.id)}">${tmEsc(g.name)}</option>`).join('')}
            </select>
          </div>
```

Logo depois de `container.querySelector('#er-batch-form').addEventListener('submit', ...)` ser registrado (antes dele, no mesmo ponto onde o form é montado), adicionar o listener de troca de escopo:

```javascript
    container.querySelector('#er-batch-scope').addEventListener('change', (ev) => {
      const scope = ev.target.value;
      container.querySelector('#er-batch-class-group').hidden = scope !== 'turma';
      container.querySelector('#er-batch-grade-group').hidden = scope !== 'serie';
    });
```

Trocar, dentro do handler de submit:

```javascript
      formData.append('essay_prompt_id', container.querySelector('#er-batch-prompt').value);
      formData.append('class_id', container.querySelector('#er-batch-class').value);
```

Por:

```javascript
      formData.append('essay_prompt_id', container.querySelector('#er-batch-prompt').value);
      const scope = container.querySelector('#er-batch-scope').value;
      if (scope === 'turma') {
        const classId = container.querySelector('#er-batch-class').value;
        if (!classId) {
          msg.hidden = false;
          msg.textContent = 'Selecione uma turma.';
          return;
        }
        formData.append('class_id', classId);
      } else if (scope === 'serie') {
        const gradeLevelId = container.querySelector('#er-batch-grade-level').value;
        if (!gradeLevelId) {
          msg.hidden = false;
          msg.textContent = 'Selecione uma série.';
          return;
        }
        formData.append('grade_level_id', gradeLevelId);
      }
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `node --test tests/test_r4_essay_batch_scope_frontend.js`
Expected: PASS (4 testes).

Também rode a suíte de frontend já existente pra confirmar que nada quebrou:

Run: `node --test tests/test_r4_essay_batch_progress_frontend.js`
Expected: PASS (testes já existentes, sem regressão).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/web/essay-review.js tests/test_r4_essay_batch_scope_frontend.js
git commit -m "feat(lote): seletor de abrangencia no formulario de envio em lote"
```

---

## Nota final (não é uma tarefa a executar agora)

O bug pré-existente em `/api/v1/teacher/classrooms` (campo `grade_level` hardcoded como `"3ª Série"` para toda turma, `teacher_portal.py:580`) foi encontrado durante a investigação desta leva mas é deliberadamente **fora de escopo** - não afeta esta feature (o seletor de série usa a rota nova, dado real), e corrigi-lo pertence a uma leva própria, com seu próprio raio de impacto avaliado (quem mais consome esse campo hoje, se existe algum lugar que já depende do valor fixo).
