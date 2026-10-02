# Fluxo de criação/uso de propostas de redação - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Trocar o botão único "Nova proposta" por uma tela de entrada
("Criar proposta" | "Usar proposta do banco"), fazer a criação virar uma
tela única (tema + PDF opcional + público combinado de séries/turmas/
alunos específicos), permitir atribuição a um aluno individual (não só
turma inteira), e registrar cada atribuição num histórico visível só pro
professor que atribuiu.

**Architecture:** A maior parte da infraestrutura de criação/material já
existe e é reaproveitada sem mudança (`create_essay_prompt`,
`upload_prompt_material`). O trabalho novo real é: (1) generalizar
`PromptAssignment` pra aceitar `student_id` opcional além de `class_id`,
propagando essa generalização pelos 3 lugares do código que hoje
verificam autorização só por `class_id` (envio em lote, lista de
propostas do aluno, e o portão de submissão do aluno); (2) uma rota nova
de atribuição combinada (série/turma/aluno numa chamada só, idempotente);
(3) um registro de uso append-only; (4) as 2 telas novas no frontend.

**Tech Stack:** FastAPI + SQLAlchemy async + Alembic (Postgres em
produção, SQLite in-memory nos testes de modelo/serviço) + JS vanilla
(IIFE, sem framework) no frontend, mesmo padrão de `essay-review.js`.

**Spec:** docs/superpowers/specs/2026-10-02-fluxo-criacao-propostas-redacao-design.md

## Global Constraints

- `class_id` e `student_id` em `PromptAssignment` são mutuamente
  exclusivos (CHECK `ck_prompt_assignments_target`), nunca os dois, nunca
  nenhum.
- Um aluno só pode ser atribuído diretamente UMA vez à mesma proposta
  (unique parcial `uq_prompt_assignments_prompt_student`, só quando
  `student_id IS NOT NULL`) - a unique de turma (`uq_prompt_assignments_prompt_class`)
  já existe e continua intocada.
- Reatribuir um alvo (turma ou aluno) que já tinha essa proposta é
  **idempotente** (sem erro, sem linha duplicada) - a rota de atribuição
  combinada pré-consulta o que já existe e pula, nunca deixa o
  `IntegrityError` estourar.
- `PromptAssignmentLog` é 1 linha por clique em "atribuir" (não por
  turma/aluno dentro da mesma atribuição) - um retrato histórico
  (`target_summary` JSON com nomes resolvidos no momento), não uma
  referência viva.
- `match_student`, `class_roster`, `create_assignment`,
  `create_assignments_bulk` (a rota `assignments/bulk` já existente, só
  turma) continuam **intocados** - nada nesta leva muda o comportamento
  já existente de atribuição só-turma.
- Todo ponto que hoje decide "este aluno pode ver/usar esta atribuição"
  checando só `class_id` precisa de um segundo ramo por `student_id`: são
  exatamente 3 (`_assignment_for_own_class_or_403`,
  `list_essay_prompts_for_student`, `EssayBatchService._assignment_for_student`)
  - nenhum a mais, nenhum a menos. Nos 3, se os dois ramos acharem
  resultado pra mesma proposta, a atribuição por turma vence (prioridade
  de hoje).
- A tela única "Criar e atribuir" NÃO é uma transação de banco única -
  orquestra chamadas client-side em sequência (criar proposta → upload de
  material, se houver → atribuir combinado), mesmo espírito do que
  `_materialize_if_platform_prompt` já aceita hoje (uma falha no meio
  deixa uma `EssayPrompt` DRAFT inofensiva, sem nenhum aluno vendo).

---

### Task 1: Migration - `prompt_assignments.student_id` + `prompt_assignment_logs`

**Files:**
- Create: `migrations/versions/062_prompt_assignment_target.py`
- Test: `tests/test_r2_prompt_assignment_target_migration_postgresql.py`

**Interfaces:**
- Produces: coluna `prompt_assignments.student_id` (UUID, nullable), FK
  composta `(school_id, student_id)` → `students(school_id, id)`, CHECK
  `ck_prompt_assignments_target`, índice único parcial
  `uq_prompt_assignments_prompt_student`; tabela nova
  `prompt_assignment_logs` (colunas: `id`, `school_id`, `essay_prompt_id`,
  `assigned_by_external_identity`, `created_at`, `target_summary` JSONB).

- [ ] **Step 1: Escrever o teste falho**

`tests/test_r2_prompt_assignment_target_migration_postgresql.py`:

```python
"""R2 - migration 062: prompt_assignments.student_id + prompt_assignment_logs,
contra Postgres descartavel de verdade (porta 5433), mesmo padrao de
tests/test_r4_essay_batch_scope_migration_postgresql.py."""

import os
import uuid

import pytest
import sqlalchemy as sa
from alembic import command
from alembic.config import Config

from tests._postgres_test_db import create_database, drop_database

TEST_DB_URL = os.environ.get(
    "ESSAY_SCOPE_MIGRATION_TEST_DATABASE_URL",
    "postgresql://postgres:postgres@localhost:5433/r2_prompt_assignment_target_test",
)


def _alembic_config(db_url: str) -> Config:
    cfg = Config()
    cfg.set_main_option("script_location", "migrations")
    cfg.set_main_option("sqlalchemy.url", db_url)
    return cfg


@pytest.fixture()
def pg_db():
    create_database(TEST_DB_URL)
    try:
        yield TEST_DB_URL
    finally:
        drop_database(TEST_DB_URL)


def _seed_catalog(engine):
    """Mesmo contorno ja usado em tests/test_r4_essay_batch_scope_migration_postgresql.py
    e tests/test_r6_mass_correction_migration_postgresql.py: a cadeia de
    migrations do zero esbarra em 024_chemistry_kinetics precisando de um
    seed de taxonomia que nao existe sem isso - bloqueio pre-existente,
    alheio a esta migration."""
    with engine.begin() as conn:
        conn.execute(sa.text(
            "INSERT INTO catalog_nodes (id, code, name, node_type, parent_id) "
            "VALUES (gen_random_uuid(), 'CHEMISTRY', 'Quimica', 'DISCIPLINE', NULL), "
            "(gen_random_uuid(), 'CHEMISTRY-PHYSICAL', 'Fisico-Quimica', 'TOPIC', "
            "(SELECT id FROM catalog_nodes WHERE code = 'CHEMISTRY'))"
        ))


def test_upgrade_062_adds_student_id_and_log_table(pg_db):
    engine = sa.create_engine(pg_db)
    _seed_catalog(engine)
    cfg = _alembic_config(pg_db)
    command.upgrade(cfg, "061_essay_batch_scope")
    command.upgrade(cfg, "062_prompt_assignment_target")

    inspector = sa.inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("prompt_assignments")}
    assert "student_id" in columns
    student_col = next(c for c in inspector.get_columns("prompt_assignments") if c["name"] == "student_id")
    assert student_col["nullable"] is True

    assert "prompt_assignment_logs" in inspector.get_table_names()
    log_columns = {c["name"] for c in inspector.get_columns("prompt_assignment_logs")}
    assert log_columns == {
        "id", "school_id", "essay_prompt_id", "assigned_by_external_identity",
        "created_at", "target_summary",
    }


def test_check_constraint_accepts_class_or_student_never_both_or_neither(pg_db):
    engine = sa.create_engine(pg_db)
    _seed_catalog(engine)
    cfg = _alembic_config(pg_db)
    command.upgrade(cfg, "062_prompt_assignment_target")

    with engine.begin() as conn:
        school_id = str(uuid.uuid4())
        conn.execute(sa.text(
            "INSERT INTO schools (id, code, name) VALUES (:id, 'PA-1', 'Escola')"
        ), {"id": school_id})
        segment_id = str(uuid.uuid4())
        conn.execute(sa.text(
            "INSERT INTO segments (id, school_id, name, external_id) "
            "VALUES (:id, :sid, 'seg', 'SEG-PA-1')"
        ), {"id": segment_id, "sid": school_id})
        grade_id = str(uuid.uuid4())
        conn.execute(sa.text(
            "INSERT INTO grade_levels (id, school_id, segment_id, name, external_id) "
            "VALUES (:id, :sid, :gid, 'Serie', 'GRADE-PA-1')"
        ), {"id": grade_id, "sid": school_id, "gid": segment_id})
        year_id = str(uuid.uuid4())
        conn.execute(sa.text(
            "INSERT INTO academic_years (id, school_id, year, external_id, created_at) "
            "VALUES (:id, :sid, 2026, 'YEAR-PA-1', now())"
        ), {"id": year_id, "sid": school_id})
        class_id = str(uuid.uuid4())
        conn.execute(sa.text(
            "INSERT INTO classes (id, school_id, academic_year_id, grade_level_id, name, "
            "external_id, created_at) VALUES (:id, :sid, :yid, :gid, 'Turma', 'TURMA-PA-1', now())"
        ), {"id": class_id, "sid": school_id, "yid": year_id, "gid": grade_id})
        person_id = str(uuid.uuid4())
        conn.execute(sa.text(
            "INSERT INTO persons (id, school_id, full_name) VALUES (:id, :sid, 'Aluno')"
        ), {"id": person_id, "sid": school_id})
        student_id = str(uuid.uuid4())
        conn.execute(sa.text(
            "INSERT INTO students (id, school_id, person_id) VALUES (:id, :sid, :pid)"
        ), {"id": student_id, "sid": school_id, "pid": person_id})
        prompt_id = str(uuid.uuid4())
        conn.execute(sa.text(
            "INSERT INTO essay_prompts (id, school_id, title, statement, year, status, "
            "created_by_external_identity, is_free_theme, created_at, updated_at) "
            "VALUES (:id, :sid, 'Tema', 'Disserte.', 2026, 'DRAFT', 'prof', false, now(), now())"
        ), {"id": prompt_id, "sid": school_id})

        # valido: so class_id
        conn.execute(sa.text(
            "INSERT INTO prompt_assignments (id, school_id, essay_prompt_id, class_id, "
            "assigned_by_external_identity, validation_enabled, status, created_at) "
            "VALUES (gen_random_uuid(), :sid, :pid, :cid, 'prof', true, 'OPEN', now())"
        ), {"sid": school_id, "pid": prompt_id, "cid": class_id})

        # valido: so student_id
        conn.execute(sa.text(
            "INSERT INTO prompt_assignments (id, school_id, essay_prompt_id, student_id, "
            "assigned_by_external_identity, validation_enabled, status, created_at) "
            "VALUES (gen_random_uuid(), :sid, :pid, :stid, 'prof', true, 'OPEN', now())"
        ), {"sid": school_id, "pid": prompt_id, "stid": student_id})

        # invalido: os dois juntos
        with pytest.raises(Exception, match="ck_prompt_assignments_target"):
            conn.execute(sa.text(
                "INSERT INTO prompt_assignments (id, school_id, essay_prompt_id, class_id, "
                "student_id, assigned_by_external_identity, validation_enabled, status, created_at) "
                "VALUES (gen_random_uuid(), :sid, :pid, :cid, :stid, 'prof', true, 'OPEN', now())"
            ), {"sid": school_id, "pid": prompt_id, "cid": class_id, "stid": student_id})


def test_partial_unique_blocks_same_student_assigned_twice_to_same_prompt(pg_db):
    engine = sa.create_engine(pg_db)
    _seed_catalog(engine)
    cfg = _alembic_config(pg_db)
    command.upgrade(cfg, "062_prompt_assignment_target")

    with engine.begin() as conn:
        school_id = str(uuid.uuid4())
        conn.execute(sa.text(
            "INSERT INTO schools (id, code, name) VALUES (:id, 'PA-2', 'Escola')"
        ), {"id": school_id})
        person_id = str(uuid.uuid4())
        conn.execute(sa.text(
            "INSERT INTO persons (id, school_id, full_name) VALUES (:id, :sid, 'Aluno')"
        ), {"id": person_id, "sid": school_id})
        student_id = str(uuid.uuid4())
        conn.execute(sa.text(
            "INSERT INTO students (id, school_id, person_id) VALUES (:id, :sid, :pid)"
        ), {"id": student_id, "sid": school_id, "pid": person_id})
        prompt_id = str(uuid.uuid4())
        conn.execute(sa.text(
            "INSERT INTO essay_prompts (id, school_id, title, statement, year, status, "
            "created_by_external_identity, is_free_theme, created_at, updated_at) "
            "VALUES (:id, :sid, 'Tema', 'Disserte.', 2026, 'DRAFT', 'prof', false, now(), now())"
        ), {"id": prompt_id, "sid": school_id})
        conn.execute(sa.text(
            "INSERT INTO prompt_assignments (id, school_id, essay_prompt_id, student_id, "
            "assigned_by_external_identity, validation_enabled, status, created_at) "
            "VALUES (gen_random_uuid(), :sid, :pid, :stid, 'prof', true, 'OPEN', now())"
        ), {"sid": school_id, "pid": prompt_id, "stid": student_id})

        with pytest.raises(Exception, match="uq_prompt_assignments_prompt_student"):
            conn.execute(sa.text(
                "INSERT INTO prompt_assignments (id, school_id, essay_prompt_id, student_id, "
                "assigned_by_external_identity, validation_enabled, status, created_at) "
                "VALUES (gen_random_uuid(), :sid, :pid, :stid, 'prof', true, 'OPEN', now())"
            ), {"sid": school_id, "pid": prompt_id, "stid": student_id})


def test_downgrade_062_is_clean(pg_db):
    engine = sa.create_engine(pg_db)
    _seed_catalog(engine)
    cfg = _alembic_config(pg_db)
    command.upgrade(cfg, "062_prompt_assignment_target")
    command.downgrade(cfg, "061_essay_batch_scope")

    inspector = sa.inspect(engine)
    columns = {c["name"] for c in inspector.get_columns("prompt_assignments")}
    assert "student_id" not in columns
    assert "prompt_assignment_logs" not in inspector.get_table_names()
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_r2_prompt_assignment_target_migration_postgresql.py -v`
Expected: FAIL - a revision `062_prompt_assignment_target` ainda não existe.

- [ ] **Step 3: Escrever a migration**

`migrations/versions/062_prompt_assignment_target.py`:

```python
"""PromptAssignment aceita atribuicao a um aluno especifico, nao so turma;
registro de uso de atribuicoes.

class_id vira opcional; student_id novo (tambem opcional); um CHECK
garante exatamente um dos dois preenchido - nunca os dois, nunca nenhum
(diferente do envio em lote: aqui nao existe "escola inteira implicita",
toda atribuicao precisa de um alvo explicito). Um indice unico parcial
impede o mesmo aluno ser atribuido diretamente 2x a mesma proposta (a
unique de turma, uq_prompt_assignments_prompt_class, ja existe e
continua intocada). Tabela nova prompt_assignment_logs: 1 linha por
atribuicao feita pelo professor (nao por turma/aluno dentro dela), com um
retrato historico (target_summary) de quem foi alcancado.

Puramente aditiva: toda atribuicao existente hoje ja tem class_id
preenchido e seria valida pelo novo CHECK sem nenhum backfill.

Revision ID: 062_prompt_assignment_target
Revises: 061_essay_batch_scope
"""

from alembic import op
import sqlalchemy as sa

revision = "062_prompt_assignment_target"
down_revision = "061_essay_batch_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "prompt_assignments", sa.Column("student_id", sa.Uuid(), nullable=True)
    )
    op.alter_column("prompt_assignments", "class_id", nullable=True)
    op.create_foreign_key(
        "fk_prompt_assignments_school_student",
        "prompt_assignments",
        "students",
        ["school_id", "student_id"],
        ["school_id", "id"],
        ondelete="RESTRICT",
    )
    op.create_check_constraint(
        "ck_prompt_assignments_target",
        "prompt_assignments",
        "(class_id IS NOT NULL AND student_id IS NULL) OR "
        "(class_id IS NULL AND student_id IS NOT NULL)",
    )
    op.create_index(
        "uq_prompt_assignments_prompt_student", "prompt_assignments",
        ["essay_prompt_id", "student_id"],
        unique=True, postgresql_where=sa.text("student_id IS NOT NULL"),
        sqlite_where=sa.text("student_id IS NOT NULL"),
    )
    op.create_index(
        "ix_prompt_assignments_student_id", "prompt_assignments", ["student_id"]
    )

    op.create_table(
        "prompt_assignment_logs",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("essay_prompt_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_external_identity", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("target_summary", sa.JSON(), nullable=False),
        sa.ForeignKeyConstraint(
            ["school_id", "essay_prompt_id"],
            ["essay_prompts.school_id", "essay_prompts.id"],
            ondelete="RESTRICT",
            name="fk_prompt_assignment_logs_school_prompt",
        ),
    )
    op.create_index(
        "ix_prompt_assignment_logs_essay_prompt_id",
        "prompt_assignment_logs", ["essay_prompt_id"],
    )


def downgrade() -> None:
    op.drop_index("ix_prompt_assignment_logs_essay_prompt_id", table_name="prompt_assignment_logs")
    op.drop_table("prompt_assignment_logs")

    op.drop_index("ix_prompt_assignments_student_id", table_name="prompt_assignments")
    op.drop_index("uq_prompt_assignments_prompt_student", table_name="prompt_assignments")
    op.drop_constraint("ck_prompt_assignments_target", "prompt_assignments", type_="check")
    op.drop_constraint(
        "fk_prompt_assignments_school_student", "prompt_assignments", type_="foreignkey"
    )
    op.alter_column("prompt_assignments", "class_id", nullable=False)
    op.drop_column("prompt_assignments", "student_id")
```

Use `sa.JSON()` (não `JSONB` do dialeto Postgres) pra o tipo da coluna
ficar portável entre Postgres (produção) e SQLite (testes de modelo) -
mesma convenção que outras colunas JSON já existentes no projeto (grep
por `sa.JSON()` em `migrations/versions/*.py` se precisar confirmar o
padrão antes de escrever).

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_r2_prompt_assignment_target_migration_postgresql.py -v`
Expected: PASS (5 testes).

- [ ] **Step 5: Commit**

```bash
git add migrations/versions/062_prompt_assignment_target.py \
        tests/test_r2_prompt_assignment_target_migration_postgresql.py
git commit -m "feat(propostas): migration - PromptAssignment aceita student_id + registro de uso"
```

---

### Task 2: Modelo - `PromptAssignment.student_id` + `PromptAssignmentLog`

**Files:**
- Modify: `src/agente_ia_edu/db/models/essay_proposal.py`
- Test: `tests/test_r2_essay_proposal_models.py`

**Interfaces:**
- Consumes: migration 062 (Task 1) - mesmo schema exato.
- Produces: `PromptAssignment.student_id: uuid.UUID | None`;
  `PromptAssignmentLog` (novo model), colunas `id`, `school_id`,
  `essay_prompt_id`, `assigned_by_external_identity`, `created_at`,
  `target_summary: dict`.

- [ ] **Step 1: Ler os testes existentes de `PromptAssignment`**

Run: `grep -n "class PromptAssignmentTests\|async def test_\|def _seed" tests/test_r2_essay_proposal_models.py`

Leia pelo menos 1 teste existente de `PromptAssignment` e o `_seed` do
arquivo antes de escrever os testes novos - reaproveite o mesmo padrão.

- [ ] **Step 2: Escrever os testes falhos**

Adicionar a `tests/test_r2_essay_proposal_models.py` (mesma classe de
teste de `PromptAssignment` já existente, reaproveitando o `_seed`):

```python
    async def test_assignment_with_student_id_and_no_class_id_succeeds(self):
        async with self.session_factory() as session:
            seed = await self._seed(session)
            assignment = PromptAssignment(
                id=uuid.uuid4(), school_id=seed["school"].id,
                essay_prompt_id=seed["prompt"].id, class_id=None,
                student_id=seed["student"].id, assigned_by_external_identity="prof",
            )
            session.add(assignment)
            await session.commit()
            refreshed = await session.get(PromptAssignment, assignment.id)
            self.assertIsNone(refreshed.class_id)
            self.assertEqual(refreshed.student_id, seed["student"].id)

    async def test_assignment_with_class_id_and_student_id_together_violates_check(self):
        async with self.session_factory() as session:
            seed = await self._seed(session)
            session.add(PromptAssignment(
                id=uuid.uuid4(), school_id=seed["school"].id,
                essay_prompt_id=seed["prompt"].id, class_id=seed["klass"].id,
                student_id=seed["student"].id, assigned_by_external_identity="prof",
            ))
            with self.assertRaises(IntegrityError):
                await session.commit()

    async def test_same_student_assigned_twice_to_same_prompt_violates_unique(self):
        async with self.session_factory() as session:
            seed = await self._seed(session)
            session.add(PromptAssignment(
                id=uuid.uuid4(), school_id=seed["school"].id,
                essay_prompt_id=seed["prompt"].id, class_id=None,
                student_id=seed["student"].id, assigned_by_external_identity="prof",
            ))
            await session.commit()
            session.add(PromptAssignment(
                id=uuid.uuid4(), school_id=seed["school"].id,
                essay_prompt_id=seed["prompt"].id, class_id=None,
                student_id=seed["student"].id, assigned_by_external_identity="prof",
            ))
            with self.assertRaises(IntegrityError):
                await session.commit()

    async def test_prompt_assignment_log_round_trips_target_summary_json(self):
        async with self.session_factory() as session:
            seed = await self._seed(session)
            log = PromptAssignmentLog(
                id=uuid.uuid4(), school_id=seed["school"].id,
                essay_prompt_id=seed["prompt"].id,
                assigned_by_external_identity="prof",
                target_summary={
                    "turmas": [{"class_id": str(seed["klass"].id), "name": "Turma A"}],
                    "series": [],
                    "alunos": [{"student_id": str(seed["student"].id), "name": "Aluno"}],
                },
            )
            session.add(log)
            await session.commit()
            refreshed = await session.get(PromptAssignmentLog, log.id)
            self.assertEqual(refreshed.target_summary["turmas"][0]["name"], "Turma A")
            self.assertEqual(refreshed.target_summary["series"], [])
```

Verifique o nome exato das chaves do dict devolvido pelo `_seed`
existente (`seed["school"]`/`seed["klass"]`/`seed["prompt"]`/
`seed["student"]` são ilustrativos - confirme contra o código real antes
de rodar; se `_seed` não cria um `Student`, adicione a criação dele
seguindo o mesmo padrão `Person` → `Student` já usado nos testes de
`essay_batch` desta sessão, sem alterar os testes já existentes que usam
`_seed`).

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_r2_essay_proposal_models.py -v -k "student_id or target_summary"`
Expected: FAIL - `PromptAssignment` não aceita `student_id`,
`PromptAssignmentLog` não existe.

- [ ] **Step 4: Editar o modelo**

Em `src/agente_ia_edu/db/models/essay_proposal.py`, trocar o
`__table_args__` e os campos de `PromptAssignment`:

```python
class PromptAssignment(Base):
    """Assigns a proposal to a class OR to a specific student (mutually
    exclusive) - authorization for essay submission checks against this
    table directly, either via the student's own class or via a direct
    student_id grant."""

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
        ForeignKeyConstraint(
            ["school_id", "student_id"],
            ["students.school_id", "students.id"],
            ondelete="RESTRICT",
            name="fk_prompt_assignments_school_student",
        ),
        UniqueConstraint("school_id", "id", name="uq_prompt_assignments_school_id_id"),
        UniqueConstraint(
            "essay_prompt_id", "class_id", name="uq_prompt_assignments_prompt_class"
        ),
        Index(
            "uq_prompt_assignments_prompt_student",
            "essay_prompt_id", "student_id",
            unique=True,
            postgresql_where=text("student_id IS NOT NULL"),
            sqlite_where=text("student_id IS NOT NULL"),
        ),
        CheckConstraint(
            "(class_id IS NOT NULL AND student_id IS NULL) OR "
            "(class_id IS NULL AND student_id IS NOT NULL)",
            name="ck_prompt_assignments_target",
        ),
        CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_prompt_assignments_status"),
        Index("ix_prompt_assignments_school_id", "school_id"),
        Index("ix_prompt_assignments_essay_prompt_id", "essay_prompt_id"),
        Index("ix_prompt_assignments_class_id", "class_id"),
        Index("ix_prompt_assignments_student_id", "student_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    essay_prompt_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    class_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    student_id: Mapped[uuid.UUID | None] = mapped_column(Uuid, nullable=True)
    assigned_by_external_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
```

Confirme que `text` já está importado no topo do arquivo (de
`sqlalchemy`) - se não estiver, adicione à linha de import existente.

Adicionar o modelo novo logo depois de `PromptAssignment` (antes de
`class EssaySubmission`):

```python
class PromptAssignmentLog(Base):
    """1 linha por clique em "atribuir" (nao por turma/aluno dentro da
    mesma atribuicao) - um retrato historico de quem foi alcancado, nao
    uma referencia viva: sobrevive a turma renomeada, aluno transferido ou
    desatribuido depois. Visivel so pro professor que atribuiu (nenhuma
    visao de coordenacao/direcao nesta leva)."""

    __tablename__ = "prompt_assignment_logs"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "essay_prompt_id"],
            ["essay_prompts.school_id", "essay_prompts.id"],
            ondelete="RESTRICT",
            name="fk_prompt_assignment_logs_school_prompt",
        ),
        Index("ix_prompt_assignment_logs_essay_prompt_id", "essay_prompt_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    essay_prompt_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    assigned_by_external_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    target_summary: Mapped[dict] = mapped_column(JSON, nullable=False)
```

Adicione `JSON` ao import existente de `sqlalchemy` no topo do arquivo
(ao lado de `String`, `Boolean`, etc. - confirme a linha real antes de
editar).

Em `src/agente_ia_edu/db/models/__init__.py`, adicione `PromptAssignmentLog`
à lista de exports onde `PromptAssignment` já está (mesmo padrão -
confirme a linha exata antes de editar, não suponha o nome do arquivo
sem ler).

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_r2_essay_proposal_models.py -v`
Expected: PASS, incluindo todos os testes já existentes (regressão -
`class_id` sozinho continua funcionando).

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/db/models/essay_proposal.py \
        src/agente_ia_edu/db/models/__init__.py \
        tests/test_r2_essay_proposal_models.py
git commit -m "feat(propostas): modelo - PromptAssignment.student_id + PromptAssignmentLog"
```

---

### Task 3: Envio em lote - `_assignment_for_student` aceita atribuição direta

**Files:**
- Modify: `src/agente_ia_edu/services/essay_batch.py`
- Test: `tests/test_r4_essay_batch_resolve.py` (ou arquivo de teste
  equivalente de `_assignment_for_student` - confirme o nome real com
  `grep -rn "_assignment_for_student" tests/` antes de escolher onde
  adicionar, já que vários arquivos de teste de `essay_batch` existem).

**Interfaces:**
- Consumes: `PromptAssignment.student_id` (Task 2).
- Produces: nenhuma interface nova - `_assignment_for_student` mantém a
  mesma assinatura, só o corpo ganha um segundo ramo.

- [ ] **Step 1: Localizar os testes existentes de `_assignment_for_student`**

Run: `grep -rln "_assignment_for_student" tests/*.py`

Leia o arquivo que aparecer (provavelmente `tests/test_r4_essay_batch_resolve.py`
ou `tests/test_r4_essay_batch_processing.py` - confirme) e pelo menos 1
teste existente antes de escrever os novos.

- [ ] **Step 2: Escrever os testes falhos**

Adicionar ao arquivo de teste certo (mesma classe que já testa
`_assignment_for_student`/`resolve_page`/`materialize_batch`, reaproveitando
o `_seed` já existente):

```python
    async def test_student_with_only_a_direct_assignment_is_found(self):
        """Aluno SEM a turma dele atribuida, mas com PromptAssignment.student_id
        apontando direto pra ele - _assignment_for_student precisa achar
        essa atribuicao pelo ramo novo, nao so pelo JOIN de turma."""
        async with self.session_factory() as session:
            seed = await self._seed(session)
            # Remove a atribuicao por turma que o _seed padrao cria (se
            # houver) e cria so a atribuicao direta ao aluno - confirme
            # contra o _seed real se e preciso deletar algo ou se o cenario
            # de teste ja comeca sem atribuicao de turma nenhuma.
            session.add(PromptAssignment(
                id=uuid.uuid4(), school_id=seed["school"].id,
                essay_prompt_id=seed["prompt"].id, class_id=None,
                student_id=seed["student_id"], assigned_by_external_identity="prof",
            ))
            await session.commit()

            service = EssayBatchService(session)
            assignment = await service._assignment_for_student(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                student_id=seed["student_id"],
            )
            self.assertEqual(assignment.student_id, seed["student_id"])
            self.assertIsNone(assignment.class_id)

    async def test_student_with_both_class_and_direct_assignment_uses_class_one(self):
        """Caso raro mas possivel: a mesma proposta tem atribuicao pra turma
        do aluno E atribuicao direta a ele - a de turma vence (prioridade
        de hoje, menor mudanca de comportamento)."""
        async with self.session_factory() as session:
            seed = await self._seed(session)  # ja cria a atribuicao por turma
            session.add(PromptAssignment(
                id=uuid.uuid4(), school_id=seed["school"].id,
                essay_prompt_id=seed["prompt"].id, class_id=None,
                student_id=seed["student_id"], assigned_by_external_identity="prof",
            ))
            await session.commit()

            service = EssayBatchService(session)
            assignment = await service._assignment_for_student(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                student_id=seed["student_id"],
            )
            self.assertIsNotNone(assignment.class_id)
```

Confira os nomes reais das chaves do `_seed` (`seed["student_id"]` pode
na verdade ser `seed["student"].id` ou algo diferente - leia o `_seed`
real do arquivo antes de rodar, igual nas tasks anteriores desta mesma
leva).

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest <arquivo escolhido> -v -k "direct_assignment or both_class_and_direct"`
Expected: FAIL - `_assignment_for_student` só olha `class_id` hoje,
levanta `ValueError` mesmo com a atribuição direta existindo.

- [ ] **Step 4: Editar `_assignment_for_student`**

Em `src/agente_ia_edu/services/essay_batch.py`, trocar o corpo do
método (a assinatura não muda):

```python
    async def _assignment_for_student(
        self, *, school_id: uuid.UUID, essay_prompt_id: uuid.UUID, student_id: uuid.UUID
    ) -> PromptAssignment:
        """A atribuicao desta proposta ao aluno: por turma ATIVA dele
        (ramo 1, prioridade - igual sempre foi) OU diretamente a ele
        (ramo 2, novo - R2 passou a permitir atribuir uma proposta a um
        aluno especifico, independente da turma dele ter sido atribuida).

        Nao e simplesmente a turma do lote: a spec s7 decide que a turma da
        submissao final vem do ALUNO, nao do class_id escolhido no upload -
        entao um aluno resolvido manualmente que esteja em outra turma recebe a
        atribuicao da turma DELE. Pro caminho automatico isso cai naturalmente
        na atribuicao do proprio lote, ja que o match so olha alunos daquela
        turma (ou da serie/escola, nos escopos mais amplos).
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
        if assignment is not None:
            return assignment

        assignment = await self.session.scalar(
            select(PromptAssignment).where(
                PromptAssignment.school_id == school_id,
                PromptAssignment.essay_prompt_id == essay_prompt_id,
                PromptAssignment.student_id == student_id,
            )
        )
        if assignment is None:
            raise ValueError(
                "Esta proposta nao esta atribuida a nenhuma turma ativa nem "
                "diretamente a este aluno."
            )
        return assignment
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest <arquivo escolhido> -v`
Expected: PASS, incluindo todos os testes já existentes do arquivo
(regressão - o caso só-turma de hoje não pode mudar de comportamento).

Rode também a suíte mais ampla de envio em lote pra garantir que nada
quebrou:

Run: `.venv/bin/python -m pytest tests/ -k essay_batch -q`
Expected: mesmas 2 falhas pré-existentes já documentadas (migration
024_chemistry_kinetics), nenhuma falha nova.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/essay_batch.py <arquivo de teste escolhido>
git commit -m "feat(propostas): envio em lote reconhece atribuicao direta ao aluno"
```

---

### Task 4: Aluno vê e consegue responder a uma atribuição direta

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_submissions.py`
- Test: `tests/test_r2_essay_submissions_authorization.py`,
  `tests/test_r2_essay_submissions_routes.py` (confirme em qual dos 2 fica
  `list_essay_prompts_for_student` antes de editar - pode ser num terceiro
  arquivo, `grep -rn "list_essay_prompts_for_student" tests/*.py` resolve).

**Interfaces:**
- Consumes: `PromptAssignment.student_id` (Task 2).
- Produces: `_assignment_for_own_class_or_403` ganha um parâmetro novo
  `student_id: uuid.UUID` (obrigatório, não opcional - todo chamador já
  tem o `enrollment.student_id` em mãos no mesmo ponto onde já passa
  `class_id`).

- [ ] **Step 1: Ler os testes existentes**

Leia `tests/test_r2_essay_submissions_authorization.py` inteiro (282
linhas, já visto nesta sessão de brainstorming - usa
`_seed_school_with_class_and_assignment` e `_enroll_student`, TestClient
síncrono com `dependency_overrides`). Rode
`grep -rn "list_essay_prompts_for_student" tests/*.py` pra achar onde
ela é testada hoje.

- [ ] **Step 2: Escrever os testes falhos**

Adicionar a `tests/test_r2_essay_submissions_authorization.py`, mesma
classe (`EssaySubmissionAuthorizationTests`), reaproveitando
`_seed_school_with_class_and_assignment`/`_enroll_student`:

```python
    def _seed_direct_student_assignment(self, code: str):
        """Escola com uma proposta atribuida DIRETO a um aluno, sem
        nenhuma turma atribuida - o cenario que esta task resolve."""
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"AUT-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(SchoolModule(
                    id=uuid.uuid4(), school_id=school.id, module_key="REDACAO_IA", enabled=True,
                ))
                segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id=f"SEG-{code}")
                session.add(segment)
                await session.flush()
                grade = GradeLevel(
                    id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
                    name="grade", external_id=f"GRADE-{code}",
                )
                year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id=f"YEAR-{code}")
                session.add_all([grade, year])
                await session.flush()
                klass = Class(
                    id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
                    grade_level_id=grade.id, name="turma", external_id=f"TURMA-{code}",
                )
                session.add(klass)
                await session.flush()
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
                    year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
                )
                session.add(prompt)
                await session.flush()
                person = Person(id=uuid.uuid4(), school_id=school.id, full_name="Aluno Direto")
                session.add(person)
                await session.flush()
                student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id)
                session.add(student)
                await session.flush()
                session.add(StudentEnrollment(
                    id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                    class_id=klass.id, status="ACTIVE",
                ))
                assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                    class_id=None, student_id=student.id,
                    assigned_by_external_identity="teacher:t", status="OPEN",
                )
                session.add(assignment)
                session.add(UserSchoolLink(
                    external_user_id="direct_student", school_id=school.id, role="STUDENT",
                    scope_type="SCHOOL", active=True,
                ))
                session.add(User(
                    id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                    external_identity_provider="test", external_user_id="direct_student",
                ))
                await session.commit()
                return school.id, klass.id, assignment.id

        return self.loop.run_until_complete(_seed())

    def test_a_student_with_only_a_direct_assignment_can_submit(self):
        """O ponto mais critico desta leva: o aluno atribuido direto
        (sem a turma dele ter sido atribuida) PRECISA conseguir submeter -
        sem isso a atribuicao individual e invisivel na pratica."""
        _school_id, _class_id, assignment_id = self._seed_direct_student_assignment("3")
        self._as("direct_student")
        resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "TYPED", "text": "Minha redacao."},
        )
        self.assertEqual(resp.status_code, 201, resp.text)

    def test_a_different_student_in_the_same_school_cannot_use_someone_elses_direct_assignment(self):
        school_id, _class_id, assignment_id = self._seed_direct_student_assignment("4")
        self._enroll_student(school_id, _class_id, "other_student")
        self._as("other_student")
        resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "TYPED", "text": "x"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_direct_assignment_appears_in_the_assigned_students_own_prompt_list(self):
        _school_id, _class_id, _assignment_id = self._seed_direct_student_assignment("5")
        self._as("direct_student")
        resp = self.client.get("/api/v1/student/essay-prompts")
        self.assertEqual(resp.status_code, 200)
        titles = [p["title"] for p in resp.json()]
        self.assertIn("Tema", titles)
```

Verifique os caminhos reais das rotas (`/api/v1/student/essay-submissions`,
`/api/v1/student/essay-prompts` são os usados pelos testes já existentes
no arquivo - confirme contra o `grep` do Step 1, incluindo o import de
`PromptAssignment`/`Person`/`Student`/`StudentEnrollment`/`User`/
`UserSchoolLink` que já estão no topo do arquivo, conforme visto nesta
sessão).

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_r2_essay_submissions_authorization.py -v -k "direct_assignment or direct_student"`
Expected: FAIL - `test_a_student_with_only_a_direct_assignment_can_submit`
recebe 403 ("This proposal was not assigned to your class.");
`test_direct_assignment_appears...` não acha "Tema" na lista.

- [ ] **Step 4: Editar `_assignment_for_own_class_or_403` e `list_essay_prompts_for_student`**

Em `src/agente_ia_edu/api/routes/essay_submissions.py`, trocar a
assinatura e o corpo de `_assignment_for_own_class_or_403`:

```python
async def _assignment_for_own_class_or_403(
    session: AsyncSession, *, prompt_assignment_id: uuid.UUID, school_id: uuid.UUID,
    class_id: uuid.UUID, student_id: uuid.UUID,
) -> PromptAssignment:
    assignment = await session.get(PromptAssignment, prompt_assignment_id)
    if assignment is None or assignment.school_id != school_id:
        raise HTTPException(
            status_code=403, detail="This proposal was not assigned to your class."
        )
    # Por turma (comportamento de sempre) OU direto a este aluno (R2
    # passou a permitir atribuir uma proposta a um aluno especifico).
    is_authorized = (
        assignment.class_id == class_id
        or assignment.student_id == student_id
    )
    if not is_authorized:
        raise HTTPException(
            status_code=403, detail="This proposal was not assigned to your class."
        )
    # Defense-in-depth: list_essay_prompts_for_student only ever surfaces
    # OPEN assignments (spec §3/§4), so a CLOSED one should never reach this
    # far via the normal UI flow - but nothing stops a client from posting a
    # prompt_assignment_id it saw while the assignment was still open (or
    # simply guessed), so this is enforced here too, not just in the list.
    if assignment.status != "OPEN":
        raise HTTPException(
            status_code=403, detail="This proposal is closed and no longer accepts submissions."
        )
    return assignment
```

No único chamador (`create_essay_submission`), acrescentar o argumento
novo:

```python
        assignment = await _assignment_for_own_class_or_403(
            session, prompt_assignment_id=request.prompt_assignment_id,
            school_id=school_id, class_id=enrollment.class_id,
            student_id=enrollment.student_id,
        )
```

Em `list_essay_prompts_for_student`, trocar a cláusula `.where(...)` da
query (precisa de `or_` - confirme se já está importado de `sqlalchemy`
no topo do arquivo, adicione se não estiver):

```python
                .where(
                    PromptAssignment.school_id == school_id,
                    or_(
                        PromptAssignment.class_id == enrollment.class_id,
                        PromptAssignment.student_id == enrollment.student_id,
                    ),
                    PromptAssignment.status == "OPEN",
                )
```

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_r2_essay_submissions_authorization.py -v`
Expected: PASS, incluindo todos os testes já existentes (regressão - o
caso só-turma de hoje não pode mudar de comportamento, inclusive o teste
`test_a_student_enrolled_in_a_different_class_is_denied_not_404` que já
existe).

Rode também a suíte mais ampla do R2:

Run: `.venv/bin/python -m pytest tests/ -k "r2_essay" -q`
Expected: nenhuma falha nova.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_submissions.py \
        tests/test_r2_essay_submissions_authorization.py
git commit -m "feat(propostas): aluno com atribuicao direta ve e consegue responder a proposta"
```

---

### Task 5: `EssayProposalService` - atribuição combinada, busca de alunos, histórico

**Files:**
- Modify: `src/agente_ia_edu/services/essay_proposal.py`
- Test: `tests/test_r2_essay_proposal_service.py`

**Interfaces:**
- Consumes: `PromptAssignment.student_id`, `PromptAssignmentLog` (Task 2);
  `Class.grade_level_id` (modelo já existente).
- Produces:
  - `resolve_assignment_targets(self, *, school_id, class_ids, grade_level_ids, student_ids) -> dict`
    devolve `{"class_ids": set[uuid.UUID], "student_ids": set[uuid.UUID],
    "target_summary": dict}` (série já expandida em turmas reais, sem
    duplicar turma que apareça direto E via série).
  - `create_assignments_combined(self, *, school_id, essay_prompt_id, class_ids, grade_level_ids, student_ids, assigned_by_external_identity, due_at=None, validation_enabled=True) -> dict`
    devolve `{"created_count": int, "already_assigned_count": int, "log_id": uuid.UUID}`.
  - `search_students_for_assignment(self, *, school_id, class_ids, query) -> list[tuple[uuid.UUID, str, str | None, str]]`
    devolve `(student_id, full_name, document_number, class_name)`.
  - `list_assignment_log(self, *, school_id, essay_prompt_id) -> list[PromptAssignmentLog]`
    mais recente primeiro.

- [ ] **Step 1: Ler os testes existentes do serviço**

Leia `tests/test_r2_essay_proposal_service.py` inteiro (o `_seed`, o
padrão de `IsolatedAsyncioTestCase`/`session_factory` já usado) antes de
escrever os testes novos.

- [ ] **Step 2: Escrever os testes falhos**

Adicionar a `tests/test_r2_essay_proposal_service.py`, mesma classe,
reaproveitando o `_seed`:

```python
    async def _seed_two_classes_same_grade(self, session):
        """2 turmas da mesma serie (A, B) + 1 turma de outra serie (C),
        1 aluno ativo em cada - mesmo padrao do envio em lote, pra testar
        expansao de serie->turmas."""
        school = School(id=uuid.uuid4(), code="PR-SERIE", name="Escola")
        session.add(school)
        await session.flush()
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-PR")
        session.add(segment)
        await session.flush()
        grade_x = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="Serie X", external_id="GRADE-PR-X",
        )
        grade_y = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="Serie Y", external_id="GRADE-PR-Y",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id="YEAR-PR")
        session.add_all([grade_x, grade_y, year])
        await session.flush()
        class_a = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade_x.id, name="Turma A", external_id="TURMA-PR-A",
        )
        class_b = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade_x.id, name="Turma B", external_id="TURMA-PR-B",
        )
        class_c = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade_y.id, name="Turma C", external_id="TURMA-PR-C",
        )
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
            year=2026, created_by_external_identity="prof",
        )
        session.add_all([class_a, class_b, class_c, prompt])
        await session.flush()

        def _make_student(nome, klass):
            person = Person(id=uuid.uuid4(), school_id=school.id, full_name=nome)
            session.add(person)
            student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id)
            session.add(student)
            session.add(StudentEnrollment(
                id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                class_id=klass.id, status="ACTIVE",
            ))
            return student.id

        student_a = _make_student("Aluno Turma A", class_a)
        student_c = _make_student("Aluno Turma C", class_c)
        await session.flush()
        return {
            "school": school, "prompt": prompt, "grade_x": grade_x,
            "class_a": class_a, "class_b": class_b, "class_c": class_c,
            "student_a": student_a, "student_c": student_c,
        }

    async def test_resolve_assignment_targets_expands_grade_into_its_classes(self):
        async with self.session_factory() as session:
            seed = await self._seed_two_classes_same_grade(session)
            service = EssayProposalService(session)
            result = await service.resolve_assignment_targets(
                school_id=seed["school"].id, class_ids=[], grade_level_ids=[seed["grade_x"].id],
                student_ids=[],
            )
            self.assertEqual(result["class_ids"], {seed["class_a"].id, seed["class_b"].id})

    async def test_resolve_assignment_targets_does_not_duplicate_class_in_both_sets(self):
        async with self.session_factory() as session:
            seed = await self._seed_two_classes_same_grade(session)
            service = EssayProposalService(session)
            result = await service.resolve_assignment_targets(
                school_id=seed["school"].id, class_ids=[seed["class_a"].id],
                grade_level_ids=[seed["grade_x"].id], student_ids=[],
            )
            self.assertEqual(result["class_ids"], {seed["class_a"].id, seed["class_b"].id})

    async def test_create_assignments_combined_creates_class_and_student_targets(self):
        async with self.session_factory() as session:
            seed = await self._seed_two_classes_same_grade(session)
            service = EssayProposalService(session)
            result = await service.create_assignments_combined(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_ids=[seed["class_b"].id], grade_level_ids=[],
                student_ids=[seed["student_c"]],
                assigned_by_external_identity="prof",
            )
            self.assertEqual(result["created_count"], 2)
            self.assertEqual(result["already_assigned_count"], 0)

            rows = (await session.execute(
                select(PromptAssignment).where(PromptAssignment.essay_prompt_id == seed["prompt"].id)
            )).scalars().all()
            self.assertEqual(
                {(r.class_id, r.student_id) for r in rows},
                {(seed["class_b"].id, None), (None, seed["student_c"])},
            )

    async def test_create_assignments_combined_is_idempotent_for_already_assigned_targets(self):
        async with self.session_factory() as session:
            seed = await self._seed_two_classes_same_grade(session)
            service = EssayProposalService(session)
            await service.create_assignments_combined(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_ids=[seed["class_a"].id], grade_level_ids=[], student_ids=[],
                assigned_by_external_identity="prof",
            )
            result = await service.create_assignments_combined(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_ids=[seed["class_a"].id], grade_level_ids=[], student_ids=[seed["student_c"]],
                assigned_by_external_identity="prof",
            )
            self.assertEqual(result["created_count"], 1)
            self.assertEqual(result["already_assigned_count"], 1)
            rows = (await session.execute(
                select(PromptAssignment).where(
                    PromptAssignment.essay_prompt_id == seed["prompt"].id,
                    PromptAssignment.class_id == seed["class_a"].id,
                )
            )).scalars().all()
            self.assertEqual(len(rows), 1)

    async def test_create_assignments_combined_writes_one_log_per_call(self):
        async with self.session_factory() as session:
            seed = await self._seed_two_classes_same_grade(session)
            service = EssayProposalService(session)
            await service.create_assignments_combined(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id,
                class_ids=[seed["class_a"].id, seed["class_b"].id], grade_level_ids=[],
                student_ids=[seed["student_c"]],
                assigned_by_external_identity="prof",
            )
            logs = await service.list_assignment_log(
                school_id=seed["school"].id, essay_prompt_id=seed["prompt"].id
            )
            self.assertEqual(len(logs), 1)
            self.assertEqual(len(logs[0].target_summary["turmas"]), 2)
            self.assertEqual(len(logs[0].target_summary["alunos"]), 1)

    async def test_search_students_for_assignment_restricted_to_given_class_ids(self):
        async with self.session_factory() as session:
            seed = await self._seed_two_classes_same_grade(session)
            service = EssayProposalService(session)
            results = await service.search_students_for_assignment(
                school_id=seed["school"].id, class_ids=[seed["class_a"].id], query="Aluno"
            )
            ids = {r[0] for r in results}
            self.assertEqual(ids, {seed["student_a"]})
            self.assertNotIn(seed["student_c"], ids)
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_r2_essay_proposal_service.py -v -k "resolve_assignment_targets or combined or search_students_for_assignment"`
Expected: FAIL - nenhum dos métodos existe ainda.

- [ ] **Step 4: Implementar os métodos novos**

Em `src/agente_ia_edu/services/essay_proposal.py`, adicionar ao import
de `..db.models`:

```python
from ..db.models import (
    Class, EssayPrompt, GradeLevel, Person, PromptAssignment, PromptAssignmentLog,
    PromptMaterial, Student, StudentEnrollment,
)
```

Adicionar `select` já deve estar importado de `sqlalchemy` (confirme);
adicionar os 4 métodos novos ao final da classe `EssayProposalService`
(depois de `create_assignments_bulk`, antes de `soft_delete_prompt`):

```python
    async def resolve_assignment_targets(
        self, *, school_id: uuid.UUID, class_ids: list[uuid.UUID],
        grade_level_ids: list[uuid.UUID], student_ids: list[uuid.UUID],
    ) -> dict:
        """Expande serie em turmas reais (Class.grade_level_id), junta com
        as turmas escolhidas direto sem duplicar, e resolve os nomes pra um
        retrato pronto pro PromptAssignmentLog."""
        final_class_ids: set[uuid.UUID] = set(class_ids)
        series_summary = []
        if grade_level_ids:
            grade_classes = (await self.session.execute(
                select(Class.id, Class.name, Class.grade_level_id)
                .where(
                    Class.school_id == school_id,
                    Class.grade_level_id.in_(grade_level_ids),
                )
            )).all()
            by_grade: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
            for class_id, class_name, grade_level_id in grade_classes:
                final_class_ids.add(class_id)
                by_grade.setdefault(grade_level_id, []).append((class_id, class_name))
            grade_rows = (await self.session.execute(
                select(GradeLevel.id, GradeLevel.name).where(GradeLevel.id.in_(grade_level_ids))
            )).all()
            grade_names = {gid: name for gid, name in grade_rows}
            for grade_id, classes in by_grade.items():
                series_summary.append({
                    "grade_level_id": str(grade_id),
                    "name": grade_names.get(grade_id, str(grade_id)),
                    "turmas_expandidas": [
                        {"class_id": str(cid), "name": cname} for cid, cname in classes
                    ],
                })

        class_names: dict[uuid.UUID, str] = {}
        if final_class_ids:
            rows = (await self.session.execute(
                select(Class.id, Class.name).where(Class.id.in_(final_class_ids))
            )).all()
            class_names = {cid: name for cid, name in rows}

        student_names: dict[uuid.UUID, str] = {}
        if student_ids:
            rows = (await self.session.execute(
                select(Student.id, Person.full_name)
                .join(Person, Person.id == Student.person_id)
                .where(Student.id.in_(student_ids))
            )).all()
            student_names = {sid: name for sid, name in rows}

        # "turmas" lista TODAS as turmas finais (as escolhidas direto +
        # as expandidas de serie), uma vez cada, com o nome resolvido -
        # final_class_ids ja e a uniao sem duplicar (e um set).
        target_summary = {
            "turmas": [
                {"class_id": str(cid), "name": class_names.get(cid, str(cid))}
                for cid in sorted(final_class_ids, key=str)
            ],
            "series": series_summary,
            "alunos": [
                {"student_id": str(sid), "name": student_names.get(sid, str(sid))}
                for sid in sorted(student_ids, key=str)
            ],
        }

        return {
            "class_ids": final_class_ids,
            "student_ids": set(student_ids),
            "target_summary": target_summary,
        }

    async def create_assignments_combined(
        self, *, school_id: uuid.UUID, essay_prompt_id: uuid.UUID,
        class_ids: list[uuid.UUID], grade_level_ids: list[uuid.UUID],
        student_ids: list[uuid.UUID], assigned_by_external_identity: str,
        due_at: datetime | None = None, validation_enabled: bool = True,
    ) -> dict:
        """Atribui a proposta ao publico combinado (turmas + series
        expandidas + alunos especificos) numa chamada so. Idempotente: um
        alvo que ja tinha essa proposta e pulado silenciosamente (pre-
        consulta ANTES de inserir, nunca deixa o IntegrityError da unique
        estourar), mas o log registra a tentativa inteira - inclusive os
        alvos que ja existiam - porque ele retrata a ACAO do professor, nao
        so o que mudou no banco."""
        if not class_ids and not grade_level_ids and not student_ids:
            raise ValueError("Escolha pelo menos uma serie, turma ou aluno.")

        prompt = await self._active_prompt_or_raise(
            school_id=school_id, essay_prompt_id=essay_prompt_id
        )
        resolved = await self.resolve_assignment_targets(
            school_id=school_id, class_ids=class_ids,
            grade_level_ids=grade_level_ids, student_ids=student_ids,
        )
        final_class_ids = resolved["class_ids"]
        final_student_ids = resolved["student_ids"]

        existing_classes = set()
        if final_class_ids:
            rows = (await self.session.execute(
                select(PromptAssignment.class_id).where(
                    PromptAssignment.essay_prompt_id == essay_prompt_id,
                    PromptAssignment.class_id.in_(final_class_ids),
                )
            )).scalars().all()
            existing_classes = set(rows)
        existing_students = set()
        if final_student_ids:
            rows = (await self.session.execute(
                select(PromptAssignment.student_id).where(
                    PromptAssignment.essay_prompt_id == essay_prompt_id,
                    PromptAssignment.student_id.in_(final_student_ids),
                )
            )).scalars().all()
            existing_students = set(rows)

        created_count = 0
        for class_id in final_class_ids - existing_classes:
            self.session.add(PromptAssignment(
                id=uuid.uuid4(), school_id=school_id, essay_prompt_id=essay_prompt_id,
                class_id=class_id, student_id=None,
                assigned_by_external_identity=assigned_by_external_identity,
                due_at=due_at, validation_enabled=validation_enabled, status="OPEN",
            ))
            created_count += 1
        for student_id in final_student_ids - existing_students:
            self.session.add(PromptAssignment(
                id=uuid.uuid4(), school_id=school_id, essay_prompt_id=essay_prompt_id,
                class_id=None, student_id=student_id,
                assigned_by_external_identity=assigned_by_external_identity,
                due_at=due_at, validation_enabled=validation_enabled, status="OPEN",
            ))
            created_count += 1

        already_assigned_count = len(existing_classes) + len(existing_students)

        if prompt.status == "DRAFT" and created_count > 0:
            prompt.status = "ACTIVE"

        log = PromptAssignmentLog(
            id=uuid.uuid4(), school_id=school_id, essay_prompt_id=essay_prompt_id,
            assigned_by_external_identity=assigned_by_external_identity,
            target_summary=resolved["target_summary"],
        )
        self.session.add(log)
        await self.session.flush()

        return {
            "created_count": created_count,
            "already_assigned_count": already_assigned_count,
            "log_id": log.id,
        }

    async def search_students_for_assignment(
        self, *, school_id: uuid.UUID, class_ids: list[uuid.UUID], query: str,
    ) -> list[tuple[uuid.UUID, str, str | None, str]]:
        """(student_id, full_name, document_number, class_name) de alunos
        ativos nas turmas dadas (o chamador ja resolveu quais turmas o
        professor esta autorizado a ver, via
        TeacherPortalService.list_teacher_classrooms - este metodo nao
        resolve autorizacao, so filtra pelo escopo que recebe) cujo nome
        bate com a busca."""
        if not class_ids:
            return []
        q_clean = query.strip().lower()
        rows = (await self.session.execute(
            select(
                Student.id, Person.full_name, Person.document_number, Class.name,
            )
            .join(Person, Person.id == Student.person_id)
            .join(StudentEnrollment, StudentEnrollment.student_id == Student.id)
            .join(Class, Class.id == StudentEnrollment.class_id)
            .where(
                StudentEnrollment.school_id == school_id,
                StudentEnrollment.class_id.in_(class_ids),
                StudentEnrollment.status == "ACTIVE",
            )
            .order_by(Person.full_name)
        )).all()
        return [
            (student_id, full_name, document_number, class_name)
            for student_id, full_name, document_number, class_name in rows
            if not q_clean or q_clean in full_name.lower()
        ]

    async def list_assignment_log(
        self, *, school_id: uuid.UUID, essay_prompt_id: uuid.UUID,
    ) -> list[PromptAssignmentLog]:
        result = await self.session.execute(
            select(PromptAssignmentLog)
            .where(
                PromptAssignmentLog.school_id == school_id,
                PromptAssignmentLog.essay_prompt_id == essay_prompt_id,
            )
            .order_by(PromptAssignmentLog.created_at.desc())
        )
        return list(result.scalars().all())
```

`Person.document_number`/`Class.name` já existem nos modelos (confirme
os nomes exatos lendo `db/models/academic.py` antes de rodar, caso
algum difira do que está aqui).

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_r2_essay_proposal_service.py -v`
Expected: PASS, incluindo todos os testes já existentes (regressão -
`create_assignment`/`create_assignments_bulk` não foram tocados).

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/essay_proposal.py tests/test_r2_essay_proposal_service.py
git commit -m "feat(propostas): atribuicao combinada (serie/turma/aluno), busca de alunos, log de uso"
```

---

### Task 6: Rotas - atribuição combinada, busca de alunos, histórico

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_prompts.py`
- Test: `tests/test_r2_essay_prompts_routes.py`

**Interfaces:**
- Consumes: `EssayProposalService.create_assignments_combined`/
  `search_students_for_assignment`/`list_assignment_log` (Task 5).
- Produces:
  - `POST /api/v1/catalog/essay-prompts/{essay_prompt_id}/assignments/combined`
    → `PromptAssignmentCombinedResponse{created_count, already_assigned_count, log_id}`.
  - `GET /api/v1/catalog/essay-prompts/students-search?class_ids=...&q=...`
    → `list[StudentSearchResultResponse{student_id, full_name, document_number, class_name}]`.
  - `GET /api/v1/catalog/essay-prompts/{essay_prompt_id}/assignment-log`
    → `list[PromptAssignmentLogResponse{id, created_at, assigned_by_external_identity, target_summary}]`.

- [ ] **Step 1: Ler os testes de rota existentes**

Leia `tests/test_r2_essay_prompts_routes.py` - o cliente/autenticação já
usados, e como os testes existentes de `create_prompt_assignment`/
`create_prompt_assignments_bulk` montam o corpo da requisição.

- [ ] **Step 2: Escrever os testes falhos**

Adicionar a `tests/test_r2_essay_prompts_routes.py` (mesma classe,
reaproveitando o padrão de seed/cliente já usado nos testes de
assignment existentes):

```python
    async def test_combined_assignment_with_class_and_student_succeeds(self):
        # reaproveita o mesmo seed/cliente autenticado que os testes de
        # atribuicao ja existentes neste arquivo usam.
        seed = await self._seed_school_with_class_and_student()
        prompt = await self._create_prompt(seed)
        response = await self.client.post(
            f"/api/v1/catalog/essay-prompts/{prompt['id']}/assignments/combined",
            json={
                "class_ids": [], "grade_level_ids": [], "student_ids": [str(seed["student_id"])],
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["created_count"], 1)
        self.assertEqual(body["already_assigned_count"], 0)

    async def test_combined_assignment_with_empty_target_is_422(self):
        seed = await self._seed_school_with_class_and_student()
        prompt = await self._create_prompt(seed)
        response = await self.client.post(
            f"/api/v1/catalog/essay-prompts/{prompt['id']}/assignments/combined",
            json={"class_ids": [], "grade_level_ids": [], "student_ids": []},
        )
        self.assertEqual(response.status_code, 422, response.text)

    async def test_students_search_filters_by_name(self):
        seed = await self._seed_school_with_class_and_student()
        response = await self.client.get(
            "/api/v1/catalog/essay-prompts/students-search",
            params={"class_ids": str(seed["class_id"]), "q": "Aluno"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        names = [s["full_name"] for s in response.json()]
        self.assertTrue(any("Aluno" in n for n in names))

    async def test_assignment_log_lists_one_entry_per_combined_call(self):
        seed = await self._seed_school_with_class_and_student()
        prompt = await self._create_prompt(seed)
        await self.client.post(
            f"/api/v1/catalog/essay-prompts/{prompt['id']}/assignments/combined",
            json={"class_ids": [str(seed["class_id"])], "grade_level_ids": [], "student_ids": []},
        )
        response = await self.client.get(
            f"/api/v1/catalog/essay-prompts/{prompt['id']}/assignment-log"
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(len(response.json()), 1)
```

Ajuste `_seed_school_with_class_and_student`/`_create_prompt` pros nomes
reais de helpers já usados no arquivo (ilustrativos aqui - confirme
contra o `grep` do Step 1; se não existir um helper exatamente assim,
reaproveite o padrão dos testes de `create_prompt_assignment` já
existentes, adicionando um `Student` à escola do mesmo jeito que outros
arquivos de teste desta sessão já fizeram).

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_r2_essay_prompts_routes.py -v -k "combined or students_search or assignment_log"`
Expected: FAIL - as 3 rotas ainda não existem (404).

- [ ] **Step 4: Escrever as rotas**

Em `src/agente_ia_edu/api/routes/essay_prompts.py`, adicionar aos
imports:

```python
from ...services.teacher_portal import TeacherPortalService
```

(confirme o nome real da classe/arquivo - já usada nesta sessão em
`services/teacher_portal.py`, método `list_teacher_classrooms`.)

Adicionar os modelos Pydantic novos (perto dos outros `PromptAssignment*`
já existentes):

```python
class PromptAssignmentCombinedRequest(BaseModel):
    class_ids: list[UUID] = Field(default_factory=list)
    grade_level_ids: list[UUID] = Field(default_factory=list)
    student_ids: list[UUID] = Field(default_factory=list)
    due_at: Optional[datetime] = None
    validation_enabled: bool = True


class PromptAssignmentCombinedResponse(BaseModel):
    created_count: int
    already_assigned_count: int
    log_id: UUID


class StudentSearchResultResponse(BaseModel):
    student_id: UUID
    full_name: str
    document_number: Optional[str] = None
    class_name: str


class PromptAssignmentLogResponse(BaseModel):
    id: UUID
    created_at: datetime
    assigned_by_external_identity: str
    target_summary: dict
```

Adicionar as 3 rotas novas (depois de `create_prompt_assignments_bulk`,
no mesmo arquivo):

```python
@essay_prompts_router.post(
    "/{essay_prompt_id}/assignments/combined",
    response_model=PromptAssignmentCombinedResponse,
)
async def create_prompt_assignments_combined(
    essay_prompt_id: UUID,
    request: PromptAssignmentCombinedRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> PromptAssignmentCombinedResponse:
    """Atribui a proposta a um publico combinado - series inteiras
    (expandidas em turmas reais), turmas inteiras e alunos especificos,
    tudo numa chamada so. Idempotente: reatribuir um alvo que ja tinha
    essa proposta nao e erro."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        essay_prompt_id = await _materialize_if_platform_prompt(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id,
            created_by_external_identity=identity.external_user_id,
        )
        await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        service = EssayProposalService(session)
        try:
            result = await service.create_assignments_combined(
                school_id=school_id, essay_prompt_id=essay_prompt_id,
                class_ids=request.class_ids, grade_level_ids=request.grade_level_ids,
                student_ids=request.student_ids,
                assigned_by_external_identity=identity.external_user_id,
                due_at=request.due_at, validation_enabled=request.validation_enabled,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        response = PromptAssignmentCombinedResponse(**result)
        await session.commit()
        return response


@essay_prompts_router.get("/students-search", response_model=list[StudentSearchResultResponse])
async def search_prompt_assignment_students(
    class_ids: list[UUID] = Query(default_factory=list),
    q: str = Query(""),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[StudentSearchResultResponse]:
    """Busca de alunos pro seletor de publico - restrita as turmas que o
    CHAMADOR ja confirmou estarem no escopo autorizado do professor
    (TeacherPortalService.list_teacher_classrooms, a mesma rota que ja
    preenche o seletor de Turma hoje); esta rota nao resolve autorizacao
    de turma sozinha, so confia nos class_ids que recebe - um class_id de
    fora da escola do professor simplesmente nao aparece no resultado
    porque o filtro ja inclui school_id."""
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        service = EssayProposalService(session)
        results = await service.search_students_for_assignment(
            school_id=school_id, class_ids=class_ids, query=q,
        )
        return [
            StudentSearchResultResponse(
                student_id=student_id, full_name=full_name,
                document_number=document_number, class_name=class_name,
            )
            for student_id, full_name, document_number, class_name in results
        ]


@essay_prompts_router.get(
    "/{essay_prompt_id}/assignment-log", response_model=list[PromptAssignmentLogResponse]
)
async def get_prompt_assignment_log(
    essay_prompt_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[PromptAssignmentLogResponse]:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        service = EssayProposalService(session)
        logs = await service.list_assignment_log(
            school_id=school_id, essay_prompt_id=essay_prompt_id
        )
        return [
            PromptAssignmentLogResponse(
                id=log.id, created_at=log.created_at,
                assigned_by_external_identity=log.assigned_by_external_identity,
                target_summary=log.target_summary,
            )
            for log in logs
        ]
```

A rota `GET /students-search` fica **ANTES** de qualquer rota
`/{essay_prompt_id}/...` no arquivo só importa se "students-search" pudesse
colidir com um segmento dinâmico de `essay_prompt_id` - como o prefixo do
roteador aqui é `/api/v1/catalog/essay-prompts` e essa rota não tem
`/{essay_prompt_id}` no path (é `essay-prompts/students-search`, um
segmento literal direto), **não há o mesmo risco de ordenação** que a
feature de envio em lote teve com `/grade-levels` vs `/{batch_id}` - ainda
assim, confirme lendo o arquivo final que nenhuma rota
`/{algo_generico}` (sem o prefixo "essay_prompt_id" explícito no nome do
path param) foi declarada antes dela no mesmo arquivo de um jeito que
pudesse capturar "students-search" como valor de um path param.

- [ ] **Step 5: Rodar e confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_r2_essay_prompts_routes.py -v`
Expected: PASS, incluindo todos os testes já existentes (regressão).

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_prompts.py tests/test_r2_essay_prompts_routes.py
git commit -m "feat(propostas): rotas de atribuicao combinada, busca de alunos e historico de uso"
```

---

### Task 7: Frontend - tela de entrada + criação em tela única + seletor de público

**Files:**
- Modify: `src/agente_ia_edu/web/essay-review.js`
- Test: `tests/test_r2_prompt_entry_and_audience_frontend.js`

**Interfaces:**
- Consumes: `GET /api/v1/teacher/essay-batches/grade-levels` (já existe),
  `GET /api/v1/teacher/classrooms` (já existe), `POST /api/v1/catalog/essay-prompts`
  (já existe), `POST /api/v1/catalog/essay-prompts/{id}/materials/upload`
  (já existe), `POST /api/v1/catalog/essay-prompts/{id}/assignments/combined`
  e `GET /api/v1/catalog/essay-prompts/students-search` (Task 6).
- Produces: `renderAudiencePicker(container, {gradeLevels, classrooms})`
  reaproveitado pela Task 8, devolvendo um objeto com
  `getTargets(): {class_ids, grade_level_ids, student_ids}`.

- [ ] **Step 1: Escrever o teste falho**

`tests/test_r2_prompt_entry_and_audience_frontend.js` (mesmo padrão de
teste-contra-texto-fonte que `tests/test_r4_essay_batch_scope_frontend.js`
já usa, já que `essay-review.js` roda num IIFE sem `module.exports`):

```javascript
// Contrato de frontend da tela de entrada (Criar proposta | Usar do
// banco) e do seletor de publico combinado, em essay-review.js.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const js = fs.readFileSync('src/agente_ia_edu/web/essay-review.js', 'utf8');
const promptsListFn = js.slice(
  js.indexOf('async function renderPromptsList'),
  js.indexOf('async function renderTrashTab'),
);
const newPromptFn = js.slice(
  js.indexOf('function renderNewPromptForm'),
  js.indexOf('async function renderPromptDetail'),
);

test('a tela inicial de propostas tem os 2 botoes de entrada', () => {
  assert.match(promptsListFn, /Criar proposta/);
  assert.match(promptsListFn, /Usar proposta do banco/);
});

test('a tela inicial nao mostra nenhum campo de tema antes da escolha', () => {
  assert.doesNotMatch(promptsListFn, /id="er-title"/);
  assert.doesNotMatch(promptsListFn, /id="er-statement"/);
});

test('a tela de criar proposta e uma tela unica com tema, PDF opcional e publico', () => {
  assert.match(newPromptFn, /id="er-title"/);
  assert.match(newPromptFn, /id="er-statement"/);
  assert.match(newPromptFn, /type="file"/);
  assert.match(newPromptFn, /renderAudiencePicker/);
});

test('o seletor de publico tem os 3 blocos combinaveis', () => {
  assert.match(js, /function renderAudiencePicker/);
  const pickerFn = js.slice(
    js.indexOf('function renderAudiencePicker'),
    js.indexOf('function renderAudiencePicker') + 4000,
  );
  assert.match(pickerFn, /er-audience-series/);
  assert.match(pickerFn, /er-audience-classes/);
  assert.match(pickerFn, /er-audience-students-search/);
});

test('o submit da criacao chama criar prompt, upload opcional, e atribuicao combinada', () => {
  assert.match(newPromptFn, /\/api\/v1\/catalog\/essay-prompts/);
  assert.match(newPromptFn, /materials\/upload/);
  assert.match(newPromptFn, /assignments\/combined/);
});
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `node --test tests/test_r2_prompt_entry_and_audience_frontend.js`
Expected: FAIL (os 5 testes) - nenhuma das strings esperadas existe
ainda do jeito certo em `essay-review.js` (a tela hoje é um flat list
direto sem tela de entrada).

- [ ] **Step 3: Escrever `renderAudiencePicker`**

Em `src/agente_ia_edu/web/essay-review.js`, adicionar a função nova logo
antes de `function renderNewPromptForm`:

```javascript
  // Seletor de publico combinado (series + turmas + alunos) reaproveitado
  // pela tela de criar proposta E pela tela de usar proposta do banco.
  // Devolve {getTargets} - o form que o usa le getTargets() no submit.
  async function renderAudiencePicker(container) {
    let gradeLevels = [];
    let classrooms = [];
    try {
      gradeLevels = await reviewRequest('/api/v1/teacher/essay-batches/grade-levels');
    } catch (e) {
      gradeLevels = [];
    }
    try {
      classrooms = await reviewRequest(
        `/api/v1/teacher/classrooms?school_id=${encodeURIComponent(schoolId)}&academic_year=2026`,
      );
    } catch (e) {
      classrooms = [];
    }
    const assignableClassrooms = classrooms.filter((c) => c.class_id);
    const selectedStudents = new Map();

    container.innerHTML = `
      <div class="form-group">
        <label>Séries</label>
        <div id="er-audience-series" class="essay-class-checklist">
          ${gradeLevels.map((g) => `<label class="essay-class-check"><input type="checkbox" data-grade-id="${tmEsc(g.id)}"> ${tmEsc(g.name)}</label>`).join('') || '<p class="empty-text">Nenhuma série disponível.</p>'}
        </div>
      </div>
      <div class="form-group">
        <label>Turmas</label>
        <div id="er-audience-classes" class="essay-class-checklist">
          ${assignableClassrooms.map((c) => `<label class="essay-class-check"><input type="checkbox" data-class-id="${tmEsc(c.class_id)}"> ${tmEsc(c.name)}</label>`).join('') || '<p class="empty-text">Nenhuma turma disponível.</p>'}
        </div>
      </div>
      <div class="form-group">
        <label for="er-audience-students-search">Alunos específicos</label>
        <input id="er-audience-students-search" class="text-input" placeholder="Buscar aluno por nome...">
        <div id="er-audience-students-results"></div>
        <div id="er-audience-students-selected"></div>
      </div>`;

    function renderSelectedStudents() {
      const box = container.querySelector('#er-audience-students-selected');
      box.innerHTML = Array.from(selectedStudents.values()).map((s) => `
        <span class="er-chip" data-selected-student-id="${tmEsc(s.student_id)}">
          ${tmEsc(s.full_name)} (${tmEsc(s.class_name)}) <button type="button" data-remove-student="${tmEsc(s.student_id)}">&times;</button>
        </span>`).join('');
      box.querySelectorAll('[data-remove-student]').forEach((btn) => {
        btn.addEventListener('click', () => {
          selectedStudents.delete(btn.dataset.removeStudent);
          renderSelectedStudents();
        });
      });
    }

    function currentClassIds() {
      return Array.from(container.querySelectorAll('#er-audience-classes input:checked'))
        .map((el) => el.dataset.classId);
    }

    container.querySelector('#er-audience-students-search').addEventListener('input', async (ev) => {
      const q = ev.target.value.trim();
      const resultsBox = container.querySelector('#er-audience-students-results');
      if (!q) {
        resultsBox.innerHTML = '';
        return;
      }
      const classIdsParam = assignableClassrooms.map((c) => c.class_id)
        .concat(currentClassIds())
        .filter((v, i, arr) => arr.indexOf(v) === i);
      const params = new URLSearchParams();
      classIdsParam.forEach((id) => params.append('class_ids', id));
      params.set('q', q);
      let results = [];
      try {
        results = await reviewRequest(`/api/v1/catalog/essay-prompts/students-search?${params.toString()}`);
      } catch (e) {
        results = [];
      }
      resultsBox.innerHTML = results.map((s) => `
        <button type="button" class="btn btn-secondary" data-pick-student="${tmEsc(s.student_id)}"
          data-pick-name="${tmEsc(s.full_name)}" data-pick-class="${tmEsc(s.class_name)}">
          ${tmEsc(s.full_name)} (${tmEsc(s.class_name)})
        </button>`).join('');
      resultsBox.querySelectorAll('[data-pick-student]').forEach((btn) => {
        btn.addEventListener('click', () => {
          selectedStudents.set(btn.dataset.pickStudent, {
            student_id: btn.dataset.pickStudent, full_name: btn.dataset.pickName,
            class_name: btn.dataset.pickClass,
          });
          renderSelectedStudents();
          resultsBox.innerHTML = '';
          container.querySelector('#er-audience-students-search').value = '';
        });
      });
    });

    return {
      getTargets: () => ({
        grade_level_ids: Array.from(container.querySelectorAll('#er-audience-series input:checked'))
          .map((el) => el.dataset.gradeId),
        class_ids: currentClassIds(),
        student_ids: Array.from(selectedStudents.keys()),
      }),
    };
  }
```

Trocar `renderPromptsList`'s corpo (o que monta a tabela de propostas)
pra tela de entrada - trocar o bloco que começa em
`tabsEl.insertAdjacentHTML('afterend', ...)` (o que já lista as
propostas hoje) por uma versão que, quando a lista ainda não foi
"aberta", mostra só os 2 botões; ao clicar em "Usar proposta do banco"
é que a listagem de hoje aparece (vira a Task 8). Pra esta task, troque
só o necessário pra satisfazer o teste: adicione os 2 botões ANTES da
tabela existente, sem remover a tabela (ela vira a base da tela "usar do
banco" na próxima task):

```javascript
  async function renderPromptsList() {
    container.innerHTML = `${renderTabs('prompts')}<p class="empty-text">Carregando propostas...</p>`;
    wireTabs();
    const tabsEl = container.querySelector('.essay-review-tabs');
    if (tabsEl.nextElementSibling) tabsEl.nextElementSibling.remove();
    tabsEl.insertAdjacentHTML('afterend', `
      <div class="card tm-form" id="er-prompt-entry">
        <h3>Propostas de redação</h3>
        <div class="tm-form-actions">
          <button class="btn btn-primary" type="button" id="er-create-prompt-btn">Criar proposta</button>
          <button class="btn btn-secondary" type="button" id="er-use-bank-btn">Usar proposta do banco</button>
        </div>
      </div>`);
    container.querySelector('#er-create-prompt-btn').addEventListener('click', renderNewPromptForm);
    container.querySelector('#er-use-bank-btn').addEventListener('click', renderPromptBankList);
  }
```

(`renderPromptBankList` é criada na Task 8 - deixe a referência aqui
mesmo assim, já que o teste desta task só verifica a AUSÊNCIA de campos
de tema e a PRESENÇA dos 2 botões no texto-fonte, não a execução; a
Task 8 implementa a função de verdade).

Trocar `renderNewPromptForm` pra tela única:

```javascript
  function renderNewPromptForm() {
    container.innerHTML = `
      ${renderTabs('prompts')}
      <div class="card tm-form">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <h3>Nova proposta de redação</h3>
        <form id="er-new-prompt-form">
          <div class="form-group"><label for="er-title">Título</label><input id="er-title" class="text-input" required></div>
          <div class="form-group"><label for="er-statement">Enunciado</label><textarea id="er-statement" class="textarea-input" rows="6" required></textarea></div>
          <div class="form-group"><label for="er-year">Ano</label><input id="er-year" class="text-input" type="number" value="2026" required></div>
          <div class="form-group"><label for="er-material-file">Texto motivador (PDF, opcional)</label><input id="er-material-file" class="text-input" type="file"></div>
          <div id="er-audience-container"></div>
          <button class="btn btn-primary" type="submit">Criar e atribuir</button>
          <p id="er-new-prompt-msg" class="tm-msg" hidden></p>
        </form>
      </div>`;
    wireTabs();
    container.querySelector('[data-back]').addEventListener('click', renderPromptsList);

    let audiencePicker = null;
    renderAudiencePicker(container.querySelector('#er-audience-container')).then((p) => {
      audiencePicker = p;
    });

    container.querySelector('#er-new-prompt-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const msg = container.querySelector('#er-new-prompt-msg');
      const submitBtn = ev.target.querySelector('button[type="submit"]');
      submitBtn.disabled = true;
      const targets = audiencePicker ? audiencePicker.getTargets() : { class_ids: [], grade_level_ids: [], student_ids: [] };
      if (!targets.class_ids.length && !targets.grade_level_ids.length && !targets.student_ids.length) {
        msg.hidden = false;
        msg.textContent = 'Escolha pelo menos uma série, turma ou aluno.';
        submitBtn.disabled = false;
        return;
      }
      try {
        const prompt = await reviewRequest('/api/v1/catalog/essay-prompts', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: container.querySelector('#er-title').value.trim(),
            statement: container.querySelector('#er-statement').value.trim(),
            year: Number(container.querySelector('#er-year').value),
          }),
        });
        const fileInput = container.querySelector('#er-material-file');
        if (fileInput.files && fileInput.files[0]) {
          const formData = new FormData();
          formData.append('position', '0');
          formData.append('file', fileInput.files[0]);
          await reviewRequest(`/api/v1/catalog/essay-prompts/${prompt.id}/materials/upload`, {
            method: 'POST', body: formData,
          });
        }
        await reviewRequest(`/api/v1/catalog/essay-prompts/${prompt.id}/assignments/combined`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(targets),
        });
        renderPromptDetail(prompt.id);
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
        submitBtn.disabled = false;
      }
    });
  }
```

Confira se `reviewRequest` já sabe lidar com `body: FormData` sem
`Content-Type` manual (o padrão de upload de arquivo já usado em
`renderBatchTab`/`upload_prompt_material` do envio em lote deveria
confirmar isso - leia como `reviewRequest` é implementado antes de
assumir).

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `node --test tests/test_r2_prompt_entry_and_audience_frontend.js`
Expected: PASS (5 testes).

Rode também a suíte de frontend já existente:

Run: `node --test tests/test_r4_essay_batch_scope_frontend.js tests/test_r4_essay_batch_progress_frontend.js`
Expected: PASS, sem regressão (nenhuma mudança nesta task toca o fluxo
de envio em lote).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/web/essay-review.js tests/test_r2_prompt_entry_and_audience_frontend.js
git commit -m "feat(propostas): tela de entrada + criacao em tela unica + seletor de publico"
```

---

### Task 8: Frontend - "Usar proposta do banco" + histórico de atribuições

**Files:**
- Modify: `src/agente_ia_edu/web/essay-review.js`
- Test: `tests/test_r2_prompt_bank_and_log_frontend.js`

**Interfaces:**
- Consumes: `renderAudiencePicker` (Task 7); `GET /api/v1/catalog/essay-prompts`
  (já existe); `POST .../assignments/combined` e
  `GET .../assignment-log` (Task 6).
- Produces: nenhuma interface nova - só telas.

- [ ] **Step 1: Escrever o teste falho**

`tests/test_r2_prompt_bank_and_log_frontend.js`:

```javascript
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const js = fs.readFileSync('src/agente_ia_edu/web/essay-review.js', 'utf8');

test('existe uma tela de banco de propostas com filtro de texto', () => {
  assert.match(js, /function renderPromptBankList/);
  const fn = js.slice(js.indexOf('function renderPromptBankList'), js.indexOf('function renderPromptBankList') + 3000);
  assert.match(fn, /er-bank-filter/);
});

test('escolher uma proposta do banco leva ao seletor de publico e atribui', () => {
  const fn = js.slice(js.indexOf('function renderPromptBankList'), js.indexOf('function renderPromptBankList') + 3000);
  assert.match(fn, /renderAudiencePicker/);
  assert.match(fn, /assignments\/combined/);
});

test('a tela de detalhe de uma proposta ganhou a secao de historico de uso', () => {
  const detailFn = js.slice(js.indexOf('async function renderPromptDetail'), js.indexOf('async function renderPromptDetail') + 6000);
  assert.match(detailFn, /Histórico de atribuições/);
  assert.match(detailFn, /assignment-log/);
});
```

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `node --test tests/test_r2_prompt_bank_and_log_frontend.js`
Expected: FAIL - `renderPromptBankList` não existe, `renderPromptDetail`
não tem a seção nova.

- [ ] **Step 3: Escrever `renderPromptBankList` e a seção de histórico**

Em `src/agente_ia_edu/web/essay-review.js`, adicionar `renderPromptBankList`
logo antes de `renderNewPromptForm`:

```javascript
  async function renderPromptBankList() {
    container.innerHTML = `${renderTabs('prompts')}<p class="empty-text">Carregando propostas...</p>`;
    wireTabs();
    let allPrompts = [];
    try {
      allPrompts = await reviewRequest('/api/v1/catalog/essay-prompts');
    } catch (e) {
      allPrompts = [];
    }
    const tabsEl = container.querySelector('.essay-review-tabs');
    if (tabsEl.nextElementSibling) tabsEl.nextElementSibling.remove();
    tabsEl.insertAdjacentHTML('afterend', `
      <div class="card tm-form">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <h3>Usar proposta do banco</h3>
        <input id="er-bank-filter" class="text-input" placeholder="Filtrar por título...">
        <ul id="er-bank-list" class="tm-table"></ul>
      </div>`);
    container.querySelector('[data-back]').addEventListener('click', renderPromptsList);

    function renderList(filterText) {
      const list = container.querySelector('#er-bank-list');
      const q = filterText.trim().toLowerCase();
      const filtered = allPrompts.filter((p) => !q || p.title.toLowerCase().includes(q));
      list.innerHTML = filtered.map((p) => `
        <li><button class="btn btn-secondary" type="button" data-pick-prompt="${tmEsc(p.id)}">
          ${tmEsc(p.title)} (${p.year})${p.is_platform ? ' <span class="er-platform-badge">Plataforma</span>' : ''}
        </button></li>`).join('') || '<li class="empty-text">Nenhuma proposta encontrada.</li>';
      list.querySelectorAll('[data-pick-prompt]').forEach((btn) => {
        btn.addEventListener('click', () => renderBankAssignScreen(btn.dataset.pickPrompt));
      });
    }
    renderList('');
    container.querySelector('#er-bank-filter').addEventListener('input', (ev) => renderList(ev.target.value));
  }

  async function renderBankAssignScreen(promptId) {
    container.innerHTML = `${renderTabs('prompts')}<p class="empty-text">Carregando...</p>`;
    wireTabs();
    const tabsEl = container.querySelector('.essay-review-tabs');
    if (tabsEl.nextElementSibling) tabsEl.nextElementSibling.remove();
    tabsEl.insertAdjacentHTML('afterend', `
      <div class="card tm-form">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <h3>Atribuir proposta</h3>
        <div id="er-bank-audience-container"></div>
        <button class="btn btn-primary" type="button" id="er-bank-assign-btn">Atribuir</button>
        <p id="er-bank-assign-msg" class="tm-msg" hidden></p>
      </div>`);
    container.querySelector('[data-back]').addEventListener('click', renderPromptBankList);

    const audiencePicker = await renderAudiencePicker(container.querySelector('#er-bank-audience-container'));
    container.querySelector('#er-bank-assign-btn').addEventListener('click', async () => {
      const msg = container.querySelector('#er-bank-assign-msg');
      const targets = audiencePicker.getTargets();
      if (!targets.class_ids.length && !targets.grade_level_ids.length && !targets.student_ids.length) {
        msg.hidden = false;
        msg.textContent = 'Escolha pelo menos uma série, turma ou aluno.';
        return;
      }
      try {
        await reviewRequest(`/api/v1/catalog/essay-prompts/${promptId}/assignments/combined`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(targets),
        });
        renderPromptDetail(promptId);
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
      }
    });
  }
```

Em `renderPromptDetail` (função já existente), adicionar a seção de
histórico - logo depois do bloco `<h4>Turmas atribuídas</h4>...</form>`
que já existe, antes do fechamento do template literal (`</div>`` final):

```html
        <h4>Histórico de atribuições</h4>
        <ul id="er-assignment-log-list"><li class="empty-text">Carregando...</li></ul>
```

E depois de `container.querySelector('[data-back]').addEventListener(...)`
dentro de `renderPromptDetail`, adicionar:

```javascript
    try {
      const logs = await reviewRequest(`/api/v1/catalog/essay-prompts/${promptId}/assignment-log`);
      container.querySelector('#er-assignment-log-list').innerHTML = logs.map((log) => {
        const turmas = log.target_summary.turmas || [];
        const alunos = log.target_summary.alunos || [];
        const partes = [];
        if (turmas.length) partes.push(`${turmas.length} turma(s)`);
        if (alunos.length) partes.push(`${alunos.length} aluno(s)`);
        return `<li>${new Date(log.created_at).toLocaleString('pt-BR')} - ${partes.join(', ') || 'nenhum alvo'}</li>`;
      }).join('') || '<li class="empty-text">Nenhuma atribuição registrada ainda.</li>';
    } catch (e) {
      container.querySelector('#er-assignment-log-list').innerHTML = '<li class="empty-text">Não foi possível carregar o histórico.</li>';
    }
```

- [ ] **Step 4: Rodar e confirmar que passa**

Run: `node --test tests/test_r2_prompt_bank_and_log_frontend.js tests/test_r2_prompt_entry_and_audience_frontend.js`
Expected: PASS (ambos os arquivos, sem regressão no primeiro).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/web/essay-review.js tests/test_r2_prompt_bank_and_log_frontend.js
git commit -m "feat(propostas): tela de usar proposta do banco + historico de atribuicoes na tela de detalhe"
```

---

## Depois de todas as tasks

Seguir `superpowers:subagent-driven-development`: revisão final de todo
o branch (commits desde antes da Task 1 até o fim da Task 8), depois
`superpowers:finishing-a-development-branch` - apresentar as opções de
merge/PR ao usuário, nunca decidir sozinho.
