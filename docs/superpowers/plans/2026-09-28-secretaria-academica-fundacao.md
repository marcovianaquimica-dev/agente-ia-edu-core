# Secretaria Acadêmica — Fundação (F) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir a fundação acadêmica — componente curricular, professor, período letivo, calendário, matriz curricular, grupos de ensino, alocação docente, grade horária e dados civis — para que a secretaria consiga montar um ano letivo completo.

**Architecture:** Onze tabelas novas escopadas por `school_id`, seguindo o padrão de `db/models/academic.py` (SQLAlchemy 2.0 com `Mapped`/`mapped_column`, `Uuid` nativo, `external_id` para conciliação com o sistema legado). Nada existente é alterado: as migrations são puramente aditivas e o router novo não recebe o `reception_only_guard`, ganhando um guard de domínio próprio. A entidade central é `TeachingGroup`, irmã de `Class` e não filha, porque os grupos de ensino cruzam turmas.

**Tech Stack:** Python 3.13, SQLAlchemy 2.0 (async), Alembic, FastAPI, PostgreSQL 16, unittest (`IsolatedAsyncioTestCase`), SQLite in-memory para testes de modelo.

**Spec:** `docs/superpowers/specs/2026-09-28-secretaria-academica-fundacao-diario-design.md`

## Global Constraints

- Python `>=3.13,<3.14`; SQLAlchemy `>=2.0,<3.0`; nenhuma dependência nova neste plano.
- Toda entidade nova carrega `school_id` não nulo com `ForeignKey("schools.id", ondelete="RESTRICT")`.
- Toda entidade que será referenciada por chave estrangeira composta declara `UniqueConstraint("school_id", "id")` — é o que permite à tabela filha nomear `(school_id, pai_id)` e impedir o cruzamento de escolas no próprio banco.
- Migrations são **aditivas**: nenhuma tabela existente é alterada em nenhuma task deste plano. Todo `upgrade()` tem `downgrade()` funcional.
- `revision` segue o padrão `NNN_nome_curto`; a cadeia deste plano começa em `down_revision = "056_material_assignments"`. **Se a migration 056 não tiver sido mergeada quando este plano for executado** (ela está no working tree mas não commitada em 2026-09-28), aponte a Task 1 para `055_essay_prompt_soft_delete` e mantenha o resto da cadeia intacto.
- Testes de modelo rodam em SQLite in-memory com `PRAGMA foreign_keys=ON` registrado antes do primeiro connect — sem isso o SQLite ignora chave estrangeira e o teste passa provando nada.
- Testes de migration rodam em PostgreSQL real na porta 5433, em banco dedicado, com `upgrade → downgrade → re-upgrade`.
- A suíte completa (`pytest`) roda ao fim de cada task. Coluna nova em tabela existente não se verifica pela leitura da migration.
- Nenhum router novo recebe `reception_only_guard`.
- Datas e horas usam `DateTime(timezone=True)` com default `_utcnow`; datas civis usam `Date`.

## File Structure

**Modelos** (três arquivos focados, em vez de um grande):
- `src/agente_ia_edu/db/models/school_registry.py` — cadastros: `Discipline`, `Room`, `Teacher`, `TeacherAvailability`, `AcademicTerm`, `SchoolCalendarDay`, `CurriculumMatrix`
- `src/agente_ia_edu/db/models/teaching_group.py` — ensino: `TeachingGroup`, `TeachingGroupMember`, `TeachingAssignment`, `TimetableSlot`
- `src/agente_ia_edu/db/models/person_civil.py` — `PersonCivilRecord`, isolado por conter dado pessoal sensível

**Serviços:**
- `src/agente_ia_edu/services/timetable_conflicts.py` — validação das três dimensões de conflito
- `src/agente_ia_edu/services/teaching_group_builder.py` — criação automática dos grupos `CLASS_WIDE`

**API:**
- `src/agente_ia_edu/api/routes/academic_registry.py`
- `src/agente_ia_edu/api/schemas/academic_registry.py`
- `src/agente_ia_edu/api/dependencies.py` — acrescenta `require_academic_registry_access`

**Web:**
- `src/agente_ia_edu/web/secretaria.html`, `secretaria.js`, `secretaria.css`

**Migrations:** `057` a `062`.

---

### Task 1: Cadastros base — Discipline, Room, Teacher, TeacherAvailability

**Files:**
- Create: `src/agente_ia_edu/db/models/school_registry.py`
- Create: `migrations/versions/057_academic_registry_base.py`
- Modify: `src/agente_ia_edu/db/models/__init__.py`
- Test: `tests/test_academic_registry_model.py`

**Interfaces:**
- Consumes: `Base` de `agente_ia_edu.db.base`; `School` e `Person` já existentes.
- Produces: `Discipline`, `Room`, `Teacher`, `TeacherAvailability`. Cada uma com `id: uuid.UUID`, `school_id: uuid.UUID`, `external_id: str | None`, `created_at`/`updated_at: datetime`. `Teacher` expõe `person_id: uuid.UUID` e `registration_code: str | None`. Tasks seguintes referenciam `disciplines.id`, `rooms.id` e `teachers.id`.

- [ ] **Step 1: Write the failing test**

Crie `tests/test_academic_registry_model.py`:

```python
"""Cadastros base da secretaria acadêmica: disciplina, sala, professor.

O PRAGMA de chave estrangeira é registrado antes do primeiro connect porque o
SQLite só a aplica quando mandado, por conexão - sem ele um teste de FK passa
sem provar nada.
"""

import unittest
import uuid

from sqlalchemy import event
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    Discipline,
    Person,
    Room,
    School,
    SchoolUnit,
    Teacher,
    TeacherAvailability,
)


class AcademicRegistryModelCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )

        @event.listens_for(self.engine.sync_engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_discipline_is_created_scoped_to_school(self):
        async with self.session_factory() as session:
            school = School(code="ESC-1", name="Escola 1")
            session.add(school)
            await session.flush()

            discipline = Discipline(
                school_id=school.id,
                name="Matemática",
                short_name="MAT",
                knowledge_area="Matemática e suas Tecnologias",
                ordinal=1,
            )
            session.add(discipline)
            await session.flush()

            self.assertIsInstance(discipline.id, uuid.UUID)
            self.assertEqual(discipline.status, "ACTIVE")

    async def test_discipline_rejects_unknown_school(self):
        async with self.session_factory() as session:
            session.add(
                Discipline(school_id=uuid.uuid4(), name="Fantasma", ordinal=1)
            )
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_teacher_binds_to_person_of_the_same_school(self):
        async with self.session_factory() as session:
            school = School(code="ESC-2", name="Escola 2")
            session.add(school)
            await session.flush()

            person = Person(school_id=school.id, full_name="Prof. Mendes")
            session.add(person)
            await session.flush()

            teacher = Teacher(
                school_id=school.id,
                person_id=person.id,
                registration_code="MAT-001",
            )
            session.add(teacher)
            await session.flush()

            self.assertEqual(teacher.status, "ACTIVE")

    async def test_teacher_cannot_bind_person_from_another_school(self):
        async with self.session_factory() as session:
            school_a = School(code="ESC-A", name="Escola A")
            school_b = School(code="ESC-B", name="Escola B")
            session.add_all([school_a, school_b])
            await session.flush()

            person_b = Person(school_id=school_b.id, full_name="Alheia")
            session.add(person_b)
            await session.flush()

            session.add(Teacher(school_id=school_a.id, person_id=person_b.id))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_availability_records_weekday_and_period(self):
        async with self.session_factory() as session:
            school = School(code="ESC-3", name="Escola 3")
            session.add(school)
            await session.flush()
            person = Person(school_id=school.id, full_name="Prof. Lima")
            session.add(person)
            await session.flush()
            teacher = Teacher(school_id=school.id, person_id=person.id)
            session.add(teacher)
            await session.flush()

            session.add(
                TeacherAvailability(
                    school_id=school.id,
                    teacher_id=teacher.id,
                    weekday=1,
                    period_ordinal=1,
                    available=False,
                )
            )
            await session.flush()

    async def test_room_belongs_to_a_unit(self):
        async with self.session_factory() as session:
            school = School(code="ESC-4", name="Escola 4")
            session.add(school)
            await session.flush()
            unit = SchoolUnit(school_id=school.id, name="Unidade Centro")
            session.add(unit)
            await session.flush()

            room = Room(
                school_id=school.id,
                school_unit_id=unit.id,
                name="Laboratório de Química",
                kind="LAB",
                capacity=24,
            )
            session.add(room)
            await session.flush()

            self.assertEqual(room.kind, "LAB")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_academic_registry_model.py -v`
Expected: FAIL com `ImportError: cannot import name 'Discipline' from 'agente_ia_edu.db.models'`

- [ ] **Step 3: Write the models**

Crie `src/agente_ia_edu/db/models/school_registry.py`:

```python
"""Cadastros da secretaria acadêmica.

Disciplina, sala, professor e disponibilidade docente. Todos escopados por
escola, como o resto de academic.py: a mesma pessoa física lecionando em duas
escolas tem duas linhas em ``teachers``.

``UniqueConstraint("school_id", "id")`` é redundante com a chave primária e
obrigatório: é o que permite a uma tabela filha declarar a chave estrangeira
composta ``(school_id, pai_id)``, que o banco usa para recusar uma linha que
cruze escolas.

Spec: docs/superpowers/specs/2026-09-28-secretaria-academica-fundacao-diario-design.md §3
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Discipline(Base):
    """Componente curricular da escola."""

    __tablename__ = "disciplines"
    __table_args__ = (
        UniqueConstraint("school_id", "external_id", name="uq_disciplines_school_external_id"),
        UniqueConstraint("school_id", "id", name="uq_disciplines_school_id_id"),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_disciplines_status"),
        Index("ix_disciplines_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    short_name: Mapped[str | None] = mapped_column(String(20))
    knowledge_area: Mapped[str | None] = mapped_column(String(255))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class Room(Base):
    """Sala ou recurso físico onde uma aula acontece."""

    __tablename__ = "rooms"
    __table_args__ = (
        UniqueConstraint("school_id", "external_id", name="uq_rooms_school_external_id"),
        UniqueConstraint("school_id", "id", name="uq_rooms_school_id_id"),
        CheckConstraint(
            "kind IN ('REGULAR', 'LAB', 'COURT', 'AUDITORIUM', 'OTHER')",
            name="ck_rooms_kind",
        ),
        CheckConstraint("capacity IS NULL OR capacity > 0", name="ck_rooms_capacity_positive"),
        ForeignKeyConstraint(
            ["school_id", "school_unit_id"],
            ["school_units.school_id", "school_units.id"],
            ondelete="RESTRICT",
            name="fk_rooms_unit_same_school",
        ),
        Index("ix_rooms_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    school_unit_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    external_id: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="REGULAR")
    capacity: Mapped[int | None] = mapped_column(Integer)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class Teacher(Base):
    """Professor da escola. Espelha Student: uma Person por escola."""

    __tablename__ = "teachers"
    __table_args__ = (
        UniqueConstraint("school_id", "external_id", name="uq_teachers_school_external_id"),
        UniqueConstraint("school_id", "id", name="uq_teachers_school_id_id"),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_teachers_status"),
        ForeignKeyConstraint(
            ["school_id", "person_id"],
            ["persons.school_id", "persons.id"],
            ondelete="RESTRICT",
            name="fk_teachers_person_same_school",
        ),
        Index("ix_teachers_school_id", "school_id"),
        Index("ix_teachers_person_id", "person_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    external_id: Mapped[str | None] = mapped_column(String(255))
    registration_code: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class TeacherAvailability(Base):
    """Disponibilidade declarada do professor, por dia e horário.

    Habilitador do gerador automático de grade (SP8) e insumo da validação
    manual: a secretaria vê o choque antes de salvar.
    """

    __tablename__ = "teacher_availabilities"
    __table_args__ = (
        UniqueConstraint(
            "teacher_id", "weekday", "period_ordinal",
            name="uq_teacher_availabilities_slot",
        ),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_teacher_availabilities_weekday"),
        CheckConstraint("period_ordinal > 0", name="ck_teacher_availabilities_period"),
        ForeignKeyConstraint(
            ["school_id", "teacher_id"],
            ["teachers.school_id", "teachers.id"],
            ondelete="CASCADE",
            name="fk_teacher_availabilities_teacher_same_school",
        ),
        Index("ix_teacher_availabilities_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    teacher_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    period_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    available: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
```

- [ ] **Step 4: Export the models**

Em `src/agente_ia_edu/db/models/__init__.py`, acrescente o bloco de import junto aos demais e inclua os quatro nomes em `__all__` (o arquivo lista os nomes explicitamente; siga o mesmo estilo):

```python
from .school_registry import (
    Discipline,
    Room,
    Teacher,
    TeacherAvailability,
)
```

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_academic_registry_model.py -v`
Expected: PASS, 6 testes.

- [ ] **Step 6: Write the migration**

Crie `migrations/versions/057_academic_registry_base.py`:

```python
"""Secretaria academica - cadastros base: disciplina, sala, professor.

Revision ID: 057_academic_registry_base
Revises: 056_material_assignments

Puramente aditiva: nenhuma tabela existente e tocada. As chaves estrangeiras
compostas (school_id, pai_id) apontam para as UniqueConstraint (school_id, id)
das tabelas-pai, e sao o que faz o banco recusar uma linha que cruze escolas -
a aplicacao nao e a unica guarda.

Spec: docs/superpowers/specs/2026-09-28-secretaria-academica-fundacao-diario-design.md
"""

from alembic import op
import sqlalchemy as sa

revision = "057_academic_registry_base"
down_revision = "056_material_assignments"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "disciplines",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("short_name", sa.String(length=20), nullable=True),
        sa.Column("knowledge_area", sa.String(length=255), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT",
                                name="fk_disciplines_school"),
        sa.UniqueConstraint("school_id", "external_id", name="uq_disciplines_school_external_id"),
        sa.UniqueConstraint("school_id", "id", name="uq_disciplines_school_id_id"),
        sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_disciplines_status"),
    )
    op.create_index("ix_disciplines_school_id", "disciplines", ["school_id"])

    op.create_table(
        "rooms",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("school_unit_id", sa.Uuid(), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="REGULAR"),
        sa.Column("capacity", sa.Integer(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT",
                                name="fk_rooms_school"),
        sa.ForeignKeyConstraint(["school_id", "school_unit_id"],
                                ["school_units.school_id", "school_units.id"],
                                ondelete="RESTRICT", name="fk_rooms_unit_same_school"),
        sa.UniqueConstraint("school_id", "external_id", name="uq_rooms_school_external_id"),
        sa.UniqueConstraint("school_id", "id", name="uq_rooms_school_id_id"),
        sa.CheckConstraint("kind IN ('REGULAR', 'LAB', 'COURT', 'AUDITORIUM', 'OTHER')",
                           name="ck_rooms_kind"),
        sa.CheckConstraint("capacity IS NULL OR capacity > 0", name="ck_rooms_capacity_positive"),
    )
    op.create_index("ix_rooms_school_id", "rooms", ["school_id"])

    op.create_table(
        "teachers",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("registration_code", sa.String(length=50), nullable=True),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT",
                                name="fk_teachers_school"),
        sa.ForeignKeyConstraint(["school_id", "person_id"],
                                ["persons.school_id", "persons.id"],
                                ondelete="RESTRICT", name="fk_teachers_person_same_school"),
        sa.UniqueConstraint("school_id", "external_id", name="uq_teachers_school_external_id"),
        sa.UniqueConstraint("school_id", "id", name="uq_teachers_school_id_id"),
        sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_teachers_status"),
    )
    op.create_index("ix_teachers_school_id", "teachers", ["school_id"])
    op.create_index("ix_teachers_person_id", "teachers", ["person_id"])

    op.create_table(
        "teacher_availabilities",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("period_ordinal", sa.Integer(), nullable=False),
        sa.Column("available", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT",
                                name="fk_teacher_availabilities_school"),
        sa.ForeignKeyConstraint(["school_id", "teacher_id"],
                                ["teachers.school_id", "teachers.id"],
                                ondelete="CASCADE",
                                name="fk_teacher_availabilities_teacher_same_school"),
        sa.UniqueConstraint("teacher_id", "weekday", "period_ordinal",
                            name="uq_teacher_availabilities_slot"),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_teacher_availabilities_weekday"),
        sa.CheckConstraint("period_ordinal > 0", name="ck_teacher_availabilities_period"),
    )
    op.create_index("ix_teacher_availabilities_school_id", "teacher_availabilities", ["school_id"])


def downgrade() -> None:
    op.drop_index("ix_teacher_availabilities_school_id", table_name="teacher_availabilities")
    op.drop_table("teacher_availabilities")
    op.drop_index("ix_teachers_person_id", table_name="teachers")
    op.drop_index("ix_teachers_school_id", table_name="teachers")
    op.drop_table("teachers")
    op.drop_index("ix_rooms_school_id", table_name="rooms")
    op.drop_table("rooms")
    op.drop_index("ix_disciplines_school_id", table_name="disciplines")
    op.drop_table("disciplines")
```

- [ ] **Step 7: Apply the migration against real PostgreSQL**

Run:
```bash
alembic upgrade head && alembic downgrade 056_material_assignments && alembic upgrade head
```
Expected: os três comandos terminam sem erro. Se o `downgrade` falhar, corrija `downgrade()` antes de prosseguir — uma migration sem volta trava o ambiente de desenvolvimento de todo mundo.

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest`
Expected: PASS. Nenhum teste existente pode falhar — este plano é aditivo por construção.

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/db/models/school_registry.py \
        src/agente_ia_edu/db/models/__init__.py \
        migrations/versions/057_academic_registry_base.py \
        tests/test_academic_registry_model.py
git commit -m "feat(secretaria): cadastros base - disciplina, sala, professor"
```

---

### Task 2: Período letivo e calendário — AcademicTerm, SchoolCalendarDay

**Files:**
- Modify: `src/agente_ia_edu/db/models/school_registry.py`
- Create: `migrations/versions/058_academic_term_calendar.py`
- Modify: `src/agente_ia_edu/db/models/__init__.py`
- Create: `tests/_academic_fixtures.py`
- Test: `tests/test_academic_term_calendar.py`

**Interfaces:**
- Consumes: `Discipline`, `Room`, `Teacher` da Task 1; `AcademicYear`, `Segment`, `SchoolUnit` já existentes.
- Produces: `AcademicTerm` com `status: str` nos valores `PLANNED`/`OPEN`/`CLOSED`/`REOPENED` e os campos de reabertura `reopened_by_user_id: uuid.UUID | None`, `reopened_at: datetime | None`, `reopen_reason: str | None`. `SchoolCalendarDay` com `kind: str` nos valores `SCHOOL_DAY`/`HOLIDAY`/`RECESS`/`SATURDAY_SCHOOL_DAY`/`EVENT`. Também produz `tests/_academic_fixtures.py` com a classe base `AcademicModelCase` (atributos `self.engine`, `self.session_factory`) e a corrotina `build_school(session, code) -> SchoolFixture`, reutilizadas por todas as tasks seguintes.

- [ ] **Step 1: Write the shared test fixture**

Crie `tests/_academic_fixtures.py`:

```python
"""Base de teste compartilhada pelas tasks da fundação acadêmica.

O PRAGMA de chave estrangeira é registrado antes do primeiro connect: o SQLite
só aplica FK quando mandado, por conexão, e sem isso um teste de chave composta
passa sem provar nada.
"""

from __future__ import annotations

import unittest
from dataclasses import dataclass

from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    GradeLevel,
    Person,
    School,
    SchoolUnit,
    Segment,
    Student,
    StudentEnrollment,
)


@dataclass
class SchoolFixture:
    school: School
    unit: SchoolUnit
    segment: Segment
    grade: GradeLevel
    year: AcademicYear
    person: Person
    student: Student
    klass: Class
    enrollment: StudentEnrollment


async def build_school(session, code: str) -> SchoolFixture:
    """Uma escola completa até a matrícula, que é onde a fundação se apoia."""
    school = School(code=code, name=f"Escola {code}")
    session.add(school)
    await session.flush()

    unit = SchoolUnit(school_id=school.id, name="Unidade Centro")
    segment = Segment(school_id=school.id, name="Ensino Médio", ordinal=3)
    year = AcademicYear(school_id=school.id, year=2026, status="ACTIVE")
    person = Person(school_id=school.id, full_name=f"Aluno {code}")
    session.add_all([unit, segment, year, person])
    await session.flush()

    grade = GradeLevel(
        school_id=school.id, segment_id=segment.id, name="1ª série", ordinal=1
    )
    student = Student(school_id=school.id, person_id=person.id, student_code=f"{code}-001")
    session.add_all([grade, student])
    await session.flush()

    klass = Class(
        school_id=school.id,
        academic_year_id=year.id,
        grade_level_id=grade.id,
        school_unit_id=unit.id,
        name="A",
    )
    session.add(klass)
    await session.flush()

    enrollment = StudentEnrollment(
        school_id=school.id, student_id=student.id, class_id=klass.id
    )
    session.add(enrollment)
    await session.flush()

    return SchoolFixture(
        school=school, unit=unit, segment=segment, grade=grade, year=year,
        person=person, student=student, klass=klass, enrollment=enrollment,
    )


class AcademicModelCase(unittest.IsolatedAsyncioTestCase):
    """Engine SQLite in-memory com chave estrangeira realmente ligada."""

    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )

        @event.listens_for(self.engine.sync_engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()
```

- [ ] **Step 2: Write the failing test**

Crie `tests/test_academic_term_calendar.py`:

```python
"""Período letivo e calendário: os dois eixos temporais da fundação."""

import unittest
import uuid
from datetime import date

from sqlalchemy.exc import IntegrityError

from agente_ia_edu.db.models import AcademicTerm, SchoolCalendarDay
from tests._academic_fixtures import AcademicModelCase, build_school


class AcademicTermCase(AcademicModelCase):
    async def test_term_starts_planned(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "T1")
            term = AcademicTerm(
                school_id=fx.school.id,
                academic_year_id=fx.year.id,
                ordinal=1,
                name="1º Bimestre",
                starts_on=date(2026, 2, 2),
                ends_on=date(2026, 4, 24),
            )
            session.add(term)
            await session.flush()

            self.assertEqual(term.status, "PLANNED")
            self.assertIsNone(term.reopened_at)

    async def test_term_accepts_reopening_fields(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "T2")
            term = AcademicTerm(
                school_id=fx.school.id,
                academic_year_id=fx.year.id,
                ordinal=1,
                name="1º Bimestre",
                starts_on=date(2026, 2, 2),
                ends_on=date(2026, 4, 24),
                status="REOPENED",
                reopened_by_user_id=uuid.uuid4(),
                reopen_reason="Falta lançada em aluno errado",
            )
            session.add(term)
            await session.flush()

            self.assertEqual(term.status, "REOPENED")

    async def test_term_rejects_unknown_status(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "T3")
            session.add(
                AcademicTerm(
                    school_id=fx.school.id,
                    academic_year_id=fx.year.id,
                    ordinal=1,
                    name="Inválido",
                    starts_on=date(2026, 2, 2),
                    ends_on=date(2026, 4, 24),
                    status="ARQUIVADO",
                )
            )
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_term_ordinal_is_unique_per_year_and_segment(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "T4")
            common = dict(
                school_id=fx.school.id,
                academic_year_id=fx.year.id,
                segment_id=fx.segment.id,
                ordinal=1,
                starts_on=date(2026, 2, 2),
                ends_on=date(2026, 4, 24),
            )
            session.add(AcademicTerm(name="1º Bimestre", **common))
            await session.flush()
            session.add(AcademicTerm(name="Duplicado", **common))
            with self.assertRaises(IntegrityError):
                await session.flush()


class SchoolCalendarDayCase(AcademicModelCase):
    async def test_calendar_day_records_kind(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "C1")
            session.add(
                SchoolCalendarDay(
                    school_id=fx.school.id,
                    academic_year_id=fx.year.id,
                    date=date(2026, 2, 2),
                    kind="SCHOOL_DAY",
                )
            )
            await session.flush()

    async def test_same_date_cannot_repeat_for_the_same_unit(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "C2")
            common = dict(
                school_id=fx.school.id,
                academic_year_id=fx.year.id,
                school_unit_id=fx.unit.id,
                date=date(2026, 2, 2),
            )
            session.add(SchoolCalendarDay(kind="SCHOOL_DAY", **common))
            await session.flush()
            session.add(SchoolCalendarDay(kind="HOLIDAY", **common))
            with self.assertRaises(IntegrityError):
                await session.flush()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `python -m pytest tests/test_academic_term_calendar.py -v`
Expected: FAIL com `ImportError: cannot import name 'AcademicTerm'`

- [ ] **Step 4: Add the models**

Acrescente ao fim de `src/agente_ia_edu/db/models/school_registry.py` (e acrescente `Date` e `Text` à linha de import do `sqlalchemy`):

```python
class AcademicTerm(Base):
    """Período letivo: bimestre, trimestre ou semestre.

    Genérico por desenho - ordinal mais datas, sem enumerar o regime -, o que
    permite regimes distintos por segmento na mesma escola sem alteração de
    modelo. É a unidade em que o diário fecha (SP1) e a nota é apurada (SP2).
    """

    __tablename__ = "academic_terms"
    __table_args__ = (
        UniqueConstraint(
            "academic_year_id", "segment_id", "ordinal",
            name="uq_academic_terms_year_segment_ordinal",
        ),
        UniqueConstraint("school_id", "id", name="uq_academic_terms_school_id_id"),
        CheckConstraint(
            "status IN ('PLANNED', 'OPEN', 'CLOSED', 'REOPENED')",
            name="ck_academic_terms_status",
        ),
        CheckConstraint("ordinal > 0", name="ck_academic_terms_ordinal_positive"),
        CheckConstraint("ends_on >= starts_on", name="ck_academic_terms_date_order"),
        ForeignKeyConstraint(
            ["school_id", "academic_year_id"],
            ["academic_years.school_id", "academic_years.id"],
            ondelete="RESTRICT",
            name="fk_academic_terms_year_same_school",
        ),
        Index("ix_academic_terms_school_id", "school_id"),
        Index("ix_academic_terms_academic_year_id", "academic_year_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    academic_year_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    segment_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    external_id: Mapped[str | None] = mapped_column(String(255))
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date] = mapped_column(Date, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PLANNED")

    closed_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    closed_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopened_by_user_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    reopened_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    reopen_reason: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class SchoolCalendarDay(Base):
    """Um dia do calendário letivo.

    Base da apuração dos 200 dias letivos e insumo da materialização das aulas
    previstas em SP1: só dias SCHOOL_DAY e SATURDAY_SCHOOL_DAY geram aula.
    """

    __tablename__ = "school_calendar_days"
    __table_args__ = (
        UniqueConstraint(
            "academic_year_id", "school_unit_id", "date",
            name="uq_school_calendar_days_year_unit_date",
        ),
        CheckConstraint(
            "kind IN ('SCHOOL_DAY', 'HOLIDAY', 'RECESS', 'SATURDAY_SCHOOL_DAY', 'EVENT')",
            name="ck_school_calendar_days_kind",
        ),
        ForeignKeyConstraint(
            ["school_id", "academic_year_id"],
            ["academic_years.school_id", "academic_years.id"],
            ondelete="RESTRICT",
            name="fk_school_calendar_days_year_same_school",
        ),
        Index("ix_school_calendar_days_school_id", "school_id"),
        Index("ix_school_calendar_days_date", "academic_year_id", "date"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    academic_year_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    school_unit_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    date: Mapped[date] = mapped_column(Date, nullable=False)
    kind: Mapped[str] = mapped_column(String(25), nullable=False, default="SCHOOL_DAY")
    description: Mapped[str | None] = mapped_column(String(255))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
```

Acrescente `Date` e `Text` à lista de imports do `sqlalchemy` que a Task 1 criou, deixando-a assim:

```python
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
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
```

E acrescente `date` ao import de `datetime` no topo do arquivo:

```python
from datetime import date, datetime, timezone
```

- [ ] **Step 5: Export the models**

Acrescente `AcademicTerm` e `SchoolCalendarDay` ao bloco `from .school_registry import (...)` e ao `__all__` de `src/agente_ia_edu/db/models/__init__.py`.

- [ ] **Step 6: Run test to verify it passes**

Run: `python -m pytest tests/test_academic_term_calendar.py -v`
Expected: PASS, 6 testes.

- [ ] **Step 7: Write the migration**

Crie `migrations/versions/058_academic_term_calendar.py`:

```python
"""Secretaria academica - periodo letivo e calendario.

Revision ID: 058_academic_term_calendar
Revises: 057_academic_registry_base

Os campos de fechamento e reabertura nascem aqui, junto com a tabela, embora
so passem a ser escritos em SP1: a coluna e barata agora e cara depois, e o
regime de registro oficial nao admite fechar periodo sem saber quem fechou.
"""

from alembic import op
import sqlalchemy as sa

revision = "058_academic_term_calendar"
down_revision = "057_academic_registry_base"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "academic_terms",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("academic_year_id", sa.Uuid(), nullable=False),
        sa.Column("segment_id", sa.Uuid(), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("ordinal", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(length=100), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=False),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="PLANNED"),
        sa.Column("closed_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("closed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopened_by_user_id", sa.Uuid(), nullable=True),
        sa.Column("reopened_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reopen_reason", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT",
                                name="fk_academic_terms_school"),
        sa.ForeignKeyConstraint(["school_id", "academic_year_id"],
                                ["academic_years.school_id", "academic_years.id"],
                                ondelete="RESTRICT", name="fk_academic_terms_year_same_school"),
        sa.UniqueConstraint("academic_year_id", "segment_id", "ordinal",
                            name="uq_academic_terms_year_segment_ordinal"),
        sa.UniqueConstraint("school_id", "id", name="uq_academic_terms_school_id_id"),
        sa.CheckConstraint("status IN ('PLANNED', 'OPEN', 'CLOSED', 'REOPENED')",
                           name="ck_academic_terms_status"),
        sa.CheckConstraint("ordinal > 0", name="ck_academic_terms_ordinal_positive"),
        sa.CheckConstraint("ends_on >= starts_on", name="ck_academic_terms_date_order"),
    )
    op.create_index("ix_academic_terms_school_id", "academic_terms", ["school_id"])
    op.create_index("ix_academic_terms_academic_year_id", "academic_terms", ["academic_year_id"])

    op.create_table(
        "school_calendar_days",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("academic_year_id", sa.Uuid(), nullable=False),
        sa.Column("school_unit_id", sa.Uuid(), nullable=True),
        sa.Column("date", sa.Date(), nullable=False),
        sa.Column("kind", sa.String(length=25), nullable=False, server_default="SCHOOL_DAY"),
        sa.Column("description", sa.String(length=255), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT",
                                name="fk_school_calendar_days_school"),
        sa.ForeignKeyConstraint(["school_id", "academic_year_id"],
                                ["academic_years.school_id", "academic_years.id"],
                                ondelete="RESTRICT",
                                name="fk_school_calendar_days_year_same_school"),
        sa.UniqueConstraint("academic_year_id", "school_unit_id", "date",
                            name="uq_school_calendar_days_year_unit_date"),
        sa.CheckConstraint(
            "kind IN ('SCHOOL_DAY', 'HOLIDAY', 'RECESS', 'SATURDAY_SCHOOL_DAY', 'EVENT')",
            name="ck_school_calendar_days_kind"),
    )
    op.create_index("ix_school_calendar_days_school_id", "school_calendar_days", ["school_id"])
    op.create_index("ix_school_calendar_days_date", "school_calendar_days",
                    ["academic_year_id", "date"])


def downgrade() -> None:
    op.drop_index("ix_school_calendar_days_date", table_name="school_calendar_days")
    op.drop_index("ix_school_calendar_days_school_id", table_name="school_calendar_days")
    op.drop_table("school_calendar_days")
    op.drop_index("ix_academic_terms_academic_year_id", table_name="academic_terms")
    op.drop_index("ix_academic_terms_school_id", table_name="academic_terms")
    op.drop_table("academic_terms")
```

- [ ] **Step 8: Apply and reverse the migration**

Run:
```bash
alembic upgrade head && alembic downgrade 057_academic_registry_base && alembic upgrade head
```
Expected: os três comandos terminam sem erro.

- [ ] **Step 9: Run the full suite**

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 10: Commit**

```bash
git add src/agente_ia_edu/db/models/school_registry.py \
        src/agente_ia_edu/db/models/__init__.py \
        migrations/versions/058_academic_term_calendar.py \
        tests/_academic_fixtures.py \
        tests/test_academic_term_calendar.py
git commit -m "feat(secretaria): periodo letivo e calendario escolar"
```

---

### Task 3: Matriz curricular — CurriculumMatrix

**Files:**
- Modify: `src/agente_ia_edu/db/models/school_registry.py`
- Create: `migrations/versions/059_curriculum_matrix.py`
- Modify: `src/agente_ia_edu/db/models/__init__.py`
- Test: `tests/test_curriculum_matrix.py`

**Interfaces:**
- Consumes: `Discipline` (Task 1); `AcademicModelCase` e `build_school` (Task 2); `GradeLevel` e `AcademicYear` existentes.
- Produces: `CurriculumMatrix` com `grade_level_id`, `academic_year_id`, `discipline_id`, `weekly_lessons: int`, `annual_hours: int | None`, `is_mandatory: bool`. A Task 6 lê esta tabela para criar os grupos `CLASS_WIDE`; SP1 a usa como denominador da carga horária.

- [ ] **Step 1: Write the failing test**

Crie `tests/test_curriculum_matrix.py`:

```python
"""Matriz curricular: o contrato de carga horária da série."""

import unittest

from sqlalchemy.exc import IntegrityError

from agente_ia_edu.db.models import CurriculumMatrix, Discipline
from tests._academic_fixtures import AcademicModelCase, build_school


class CurriculumMatrixCase(AcademicModelCase):
    async def _discipline(self, session, school_id, name="Matemática"):
        discipline = Discipline(school_id=school_id, name=name, ordinal=1)
        session.add(discipline)
        await session.flush()
        return discipline

    async def test_matrix_entry_is_created(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "M1")
            discipline = await self._discipline(session, fx.school.id)

            entry = CurriculumMatrix(
                school_id=fx.school.id,
                grade_level_id=fx.grade.id,
                academic_year_id=fx.year.id,
                discipline_id=discipline.id,
                weekly_lessons=5,
                annual_hours=200,
            )
            session.add(entry)
            await session.flush()

            self.assertTrue(entry.is_mandatory)

    async def test_same_discipline_cannot_repeat_in_the_same_grade_and_year(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "M2")
            discipline = await self._discipline(session, fx.school.id)
            common = dict(
                school_id=fx.school.id,
                grade_level_id=fx.grade.id,
                academic_year_id=fx.year.id,
                discipline_id=discipline.id,
            )
            session.add(CurriculumMatrix(weekly_lessons=5, **common))
            await session.flush()
            session.add(CurriculumMatrix(weekly_lessons=3, **common))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_weekly_lessons_must_be_positive(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "M3")
            discipline = await self._discipline(session, fx.school.id)
            session.add(
                CurriculumMatrix(
                    school_id=fx.school.id,
                    grade_level_id=fx.grade.id,
                    academic_year_id=fx.year.id,
                    discipline_id=discipline.id,
                    weekly_lessons=0,
                )
            )
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_discipline_from_another_school_is_rejected(self):
        async with self.session_factory() as session:
            fx_a = await build_school(session, "MA")
            fx_b = await build_school(session, "MB")
            alien = await self._discipline(session, fx_b.school.id, "Alheia")

            session.add(
                CurriculumMatrix(
                    school_id=fx_a.school.id,
                    grade_level_id=fx_a.grade.id,
                    academic_year_id=fx_a.year.id,
                    discipline_id=alien.id,
                    weekly_lessons=2,
                )
            )
            with self.assertRaises(IntegrityError):
                await session.flush()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_curriculum_matrix.py -v`
Expected: FAIL com `ImportError: cannot import name 'CurriculumMatrix'`

- [ ] **Step 3: Add the model**

Acrescente ao fim de `src/agente_ia_edu/db/models/school_registry.py`:

```python
class CurriculumMatrix(Base):
    """Matriz curricular: o que cada série cursa em cada ano letivo.

    É o contrato contra o qual a carga horária realizada é apurada em SP1, e a
    fonte a partir da qual os grupos CLASS_WIDE são criados quando uma turma é
    aberta.
    """

    __tablename__ = "curriculum_matrices"
    __table_args__ = (
        UniqueConstraint(
            "grade_level_id", "academic_year_id", "discipline_id",
            name="uq_curriculum_matrices_grade_year_discipline",
        ),
        UniqueConstraint("school_id", "id", name="uq_curriculum_matrices_school_id_id"),
        CheckConstraint("weekly_lessons > 0", name="ck_curriculum_matrices_weekly_positive"),
        CheckConstraint(
            "annual_hours IS NULL OR annual_hours > 0",
            name="ck_curriculum_matrices_annual_positive",
        ),
        ForeignKeyConstraint(
            ["school_id", "discipline_id"],
            ["disciplines.school_id", "disciplines.id"],
            ondelete="RESTRICT",
            name="fk_curriculum_matrices_discipline_same_school",
        ),
        ForeignKeyConstraint(
            ["school_id", "academic_year_id"],
            ["academic_years.school_id", "academic_years.id"],
            ondelete="RESTRICT",
            name="fk_curriculum_matrices_year_same_school",
        ),
        Index("ix_curriculum_matrices_school_id", "school_id"),
        Index("ix_curriculum_matrices_lookup", "academic_year_id", "grade_level_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    grade_level_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    academic_year_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    discipline_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    weekly_lessons: Mapped[int] = mapped_column(Integer, nullable=False)
    annual_hours: Mapped[int | None] = mapped_column(Integer)
    is_mandatory: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
```

- [ ] **Step 4: Export the model**

Acrescente `CurriculumMatrix` ao bloco `from .school_registry import (...)` e ao `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_curriculum_matrix.py -v`
Expected: PASS, 4 testes.

- [ ] **Step 6: Write the migration**

Crie `migrations/versions/059_curriculum_matrix.py`:

```python
"""Secretaria academica - matriz curricular.

Revision ID: 059_curriculum_matrix
Revises: 058_academic_term_calendar
"""

from alembic import op
import sqlalchemy as sa

revision = "059_curriculum_matrix"
down_revision = "058_academic_term_calendar"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "curriculum_matrices",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("grade_level_id", sa.Uuid(), nullable=False),
        sa.Column("academic_year_id", sa.Uuid(), nullable=False),
        sa.Column("discipline_id", sa.Uuid(), nullable=False),
        sa.Column("weekly_lessons", sa.Integer(), nullable=False),
        sa.Column("annual_hours", sa.Integer(), nullable=True),
        sa.Column("is_mandatory", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT",
                                name="fk_curriculum_matrices_school"),
        sa.ForeignKeyConstraint(["school_id", "discipline_id"],
                                ["disciplines.school_id", "disciplines.id"],
                                ondelete="RESTRICT",
                                name="fk_curriculum_matrices_discipline_same_school"),
        sa.ForeignKeyConstraint(["school_id", "academic_year_id"],
                                ["academic_years.school_id", "academic_years.id"],
                                ondelete="RESTRICT",
                                name="fk_curriculum_matrices_year_same_school"),
        sa.UniqueConstraint("grade_level_id", "academic_year_id", "discipline_id",
                            name="uq_curriculum_matrices_grade_year_discipline"),
        sa.UniqueConstraint("school_id", "id", name="uq_curriculum_matrices_school_id_id"),
        sa.CheckConstraint("weekly_lessons > 0", name="ck_curriculum_matrices_weekly_positive"),
        sa.CheckConstraint("annual_hours IS NULL OR annual_hours > 0",
                           name="ck_curriculum_matrices_annual_positive"),
    )
    op.create_index("ix_curriculum_matrices_school_id", "curriculum_matrices", ["school_id"])
    op.create_index("ix_curriculum_matrices_lookup", "curriculum_matrices",
                    ["academic_year_id", "grade_level_id"])


def downgrade() -> None:
    op.drop_index("ix_curriculum_matrices_lookup", table_name="curriculum_matrices")
    op.drop_index("ix_curriculum_matrices_school_id", table_name="curriculum_matrices")
    op.drop_table("curriculum_matrices")
```

- [ ] **Step 7: Apply and reverse the migration**

Run:
```bash
alembic upgrade head && alembic downgrade 058_academic_term_calendar && alembic upgrade head
```
Expected: os três comandos terminam sem erro.

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/db/models/school_registry.py \
        src/agente_ia_edu/db/models/__init__.py \
        migrations/versions/059_curriculum_matrix.py \
        tests/test_curriculum_matrix.py
git commit -m "feat(secretaria): matriz curricular por serie e ano letivo"
```

---

### Task 4: Grupos de ensino — TeachingGroup, TeachingGroupMember

**Files:**
- Create: `src/agente_ia_edu/db/models/teaching_group.py`
- Create: `migrations/versions/060_teaching_groups.py`
- Modify: `src/agente_ia_edu/db/models/__init__.py`
- Test: `tests/test_teaching_group_model.py`

**Interfaces:**
- Consumes: `Discipline` (Task 1); `AcademicModelCase`, `build_school` (Task 2); `Class`, `StudentEnrollment` existentes.
- Produces: `TeachingGroup` com `kind: str` nos valores `CLASS_WIDE`/`SPLIT`/`CROSS_CLASS`, `origin_class_id: uuid.UUID | None`, `discipline_id`, `academic_year_id`. `TeachingGroupMember` com `teaching_group_id`, `student_enrollment_id`, `joined_on: date`, `left_on: date | None`. As Tasks 5, 6 e 7 referenciam `teaching_groups.id`; SP1 inteiro pendura diário e chamada aqui.

- [ ] **Step 1: Write the failing test**

Crie `tests/test_teaching_group_model.py`:

```python
"""Grupo de ensino: a unidade que assiste junto a uma disciplina.

É irmão de Class, não filho, porque um grupo pode juntar alunos de turmas
diferentes (eletiva, nivelamento, itinerário formativo). Os testes abaixo
travam justamente a regra que o kind impõe sobre origin_class_id.
"""

import unittest
from datetime import date

from sqlalchemy.exc import IntegrityError

from agente_ia_edu.db.models import Discipline, TeachingGroup, TeachingGroupMember
from tests._academic_fixtures import AcademicModelCase, build_school


class TeachingGroupCase(AcademicModelCase):
    async def _discipline(self, session, school_id, name="Inglês"):
        discipline = Discipline(school_id=school_id, name=name, ordinal=1)
        session.add(discipline)
        await session.flush()
        return discipline

    async def test_class_wide_group_carries_origin_class(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "G1")
            discipline = await self._discipline(session, fx.school.id)

            group = TeachingGroup(
                school_id=fx.school.id,
                academic_year_id=fx.year.id,
                discipline_id=discipline.id,
                name="Inglês — 1ª A",
                kind="CLASS_WIDE",
                origin_class_id=fx.klass.id,
            )
            session.add(group)
            await session.flush()

            self.assertEqual(group.origin_class_id, fx.klass.id)

    async def test_class_wide_group_without_origin_class_is_rejected(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "G2")
            discipline = await self._discipline(session, fx.school.id)

            session.add(
                TeachingGroup(
                    school_id=fx.school.id,
                    academic_year_id=fx.year.id,
                    discipline_id=discipline.id,
                    name="Sem turma",
                    kind="CLASS_WIDE",
                )
            )
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_cross_class_group_must_not_carry_origin_class(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "G3")
            discipline = await self._discipline(session, fx.school.id)

            session.add(
                TeachingGroup(
                    school_id=fx.school.id,
                    academic_year_id=fx.year.id,
                    discipline_id=discipline.id,
                    name="Inglês Intermediário",
                    kind="CROSS_CLASS",
                    origin_class_id=fx.klass.id,
                )
            )
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_cross_class_group_is_accepted_without_origin_class(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "G4")
            discipline = await self._discipline(session, fx.school.id)

            group = TeachingGroup(
                school_id=fx.school.id,
                academic_year_id=fx.year.id,
                discipline_id=discipline.id,
                name="Inglês Intermediário",
                kind="CROSS_CLASS",
            )
            session.add(group)
            await session.flush()

            self.assertIsNone(group.origin_class_id)


class TeachingGroupMemberCase(AcademicModelCase):
    async def _group(self, session, fx, kind="CROSS_CLASS", origin=None):
        discipline = Discipline(school_id=fx.school.id, name="Inglês", ordinal=1)
        session.add(discipline)
        await session.flush()
        group = TeachingGroup(
            school_id=fx.school.id,
            academic_year_id=fx.year.id,
            discipline_id=discipline.id,
            name="Grupo",
            kind=kind,
            origin_class_id=origin,
        )
        session.add(group)
        await session.flush()
        return group

    async def test_member_records_join_date(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "GM1")
            group = await self._group(session, fx)

            member = TeachingGroupMember(
                school_id=fx.school.id,
                teaching_group_id=group.id,
                student_enrollment_id=fx.enrollment.id,
                joined_on=date(2026, 2, 2),
            )
            session.add(member)
            await session.flush()

            self.assertIsNone(member.left_on)

    async def test_same_enrollment_cannot_join_the_same_group_twice_on_the_same_day(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "GM2")
            group = await self._group(session, fx)
            common = dict(
                school_id=fx.school.id,
                teaching_group_id=group.id,
                student_enrollment_id=fx.enrollment.id,
                joined_on=date(2026, 2, 2),
            )
            session.add(TeachingGroupMember(**common))
            await session.flush()
            session.add(TeachingGroupMember(**common))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_left_on_cannot_precede_joined_on(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "GM3")
            group = await self._group(session, fx)

            session.add(
                TeachingGroupMember(
                    school_id=fx.school.id,
                    teaching_group_id=group.id,
                    student_enrollment_id=fx.enrollment.id,
                    joined_on=date(2026, 5, 1),
                    left_on=date(2026, 3, 1),
                )
            )
            with self.assertRaises(IntegrityError):
                await session.flush()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_teaching_group_model.py -v`
Expected: FAIL com `ImportError: cannot import name 'TeachingGroup'`

- [ ] **Step 3: Write the models**

Crie `src/agente_ia_edu/db/models/teaching_group.py`:

```python
"""Grupo de ensino, composição, alocação docente e grade horária.

O grupo de ensino é IRMÃO de Class, não filho: os grupos podem juntar alunos de
turmas diferentes, e se o grupo fosse filho de Class esse caso viraria exceção
em todo o fluxo de diário. Com o grupo como irmão, Class volta a ser o que de
fato é - agrupamento administrativo, base da matrícula, do boletim e do censo -
e diário, chamada, alocação e grade apontam uniformemente para o grupo.

O caso majoritário (a turma inteira cursa a disciplina) é kind=CLASS_WIDE com
origin_class_id preenchido, o que permite à interface e ao boletim resolverem
direto sem varrer a lista de membros. A interface nunca exibe a palavra "grupo"
nesse caso: mostra "Matemática - 1ª A".

Spec: docs/superpowers/specs/2026-09-28-secretaria-academica-fundacao-diario-design.md §3.3
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, time, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    String,
    Time,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class TeachingGroup(Base):
    """Conjunto de alunos que assiste junto a uma disciplina."""

    __tablename__ = "teaching_groups"
    __table_args__ = (
        UniqueConstraint("school_id", "external_id", name="uq_teaching_groups_school_external_id"),
        UniqueConstraint("school_id", "id", name="uq_teaching_groups_school_id_id"),
        CheckConstraint(
            "kind IN ('CLASS_WIDE', 'SPLIT', 'CROSS_CLASS')",
            name="ck_teaching_groups_kind",
        ),
        # CLASS_WIDE e SPLIT nascem de uma turma e a nomeiam; CROSS_CLASS por
        # definicao nao pertence a turma alguma. Deixar isso a cargo da
        # aplicacao seria confiar em quem nao e a unica porta de escrita.
        CheckConstraint(
            "(kind = 'CROSS_CLASS' AND origin_class_id IS NULL) "
            "OR (kind IN ('CLASS_WIDE', 'SPLIT') AND origin_class_id IS NOT NULL)",
            name="ck_teaching_groups_origin_matches_kind",
        ),
        ForeignKeyConstraint(
            ["school_id", "discipline_id"],
            ["disciplines.school_id", "disciplines.id"],
            ondelete="RESTRICT",
            name="fk_teaching_groups_discipline_same_school",
        ),
        ForeignKeyConstraint(
            ["school_id", "academic_year_id"],
            ["academic_years.school_id", "academic_years.id"],
            ondelete="RESTRICT",
            name="fk_teaching_groups_year_same_school",
        ),
        ForeignKeyConstraint(
            ["school_id", "origin_class_id"],
            ["classes.school_id", "classes.id"],
            ondelete="RESTRICT",
            name="fk_teaching_groups_origin_class_same_school",
        ),
        Index("ix_teaching_groups_school_id", "school_id"),
        Index("ix_teaching_groups_lookup", "academic_year_id", "discipline_id"),
        Index("ix_teaching_groups_origin_class_id", "origin_class_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    academic_year_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    discipline_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    origin_class_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    school_unit_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    external_id: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    kind: Mapped[str] = mapped_column(String(20), nullable=False, default="CLASS_WIDE")
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class TeachingGroupMember(Base):
    """Quem pertence ao grupo, e desde quando.

    joined_on/left_on não são conveniência: a chamada de uma data lista quem era
    membro NAQUELA data. Sem isso o aluno que entra em maio acumula falta em
    março - o bug clássico de sistema de diário.
    """

    __tablename__ = "teaching_group_members"
    __table_args__ = (
        UniqueConstraint(
            "teaching_group_id", "student_enrollment_id", "joined_on",
            name="uq_teaching_group_members_group_enrollment_join",
        ),
        CheckConstraint(
            "left_on IS NULL OR left_on >= joined_on",
            name="ck_teaching_group_members_date_order",
        ),
        ForeignKeyConstraint(
            ["school_id", "teaching_group_id"],
            ["teaching_groups.school_id", "teaching_groups.id"],
            ondelete="CASCADE",
            name="fk_teaching_group_members_group_same_school",
        ),
        ForeignKeyConstraint(
            ["school_id", "student_enrollment_id"],
            ["student_enrollments.school_id", "student_enrollments.id"],
            ondelete="RESTRICT",
            name="fk_teaching_group_members_enrollment_same_school",
        ),
        Index("ix_teaching_group_members_school_id", "school_id"),
        Index("ix_teaching_group_members_group_id", "teaching_group_id"),
        Index("ix_teaching_group_members_enrollment_id", "student_enrollment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    teaching_group_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    student_enrollment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    joined_on: Mapped[date] = mapped_column(Date, nullable=False)
    left_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
```

**Nota para o implementador:** `StudentEnrollment` e `Class` precisam ter `UniqueConstraint("school_id", "id")` para que as chaves compostas acima resolvam. Verifique em `db/models/academic.py`; se alguma faltar, **pare e reporte** em vez de acrescentar — alterar uma tabela existente está fora do escopo aditivo deste plano e exige decisão explícita.

- [ ] **Step 4: Export the models**

Acrescente a `src/agente_ia_edu/db/models/__init__.py`:

```python
from .teaching_group import (
    TeachingGroup,
    TeachingGroupMember,
)
```
e inclua ambos em `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_teaching_group_model.py -v`
Expected: PASS, 7 testes.

- [ ] **Step 6: Write the migration**

Crie `migrations/versions/060_teaching_groups.py`:

```python
"""Secretaria academica - grupos de ensino e composicao.

Revision ID: 060_teaching_groups
Revises: 059_curriculum_matrix

O CHECK ck_teaching_groups_origin_matches_kind e o que impede um grupo
CROSS_CLASS de fingir pertencer a uma turma, e um CLASS_WIDE de nao pertencer a
nenhuma. A aplicacao nao e a unica porta de escrita do banco.
"""

from alembic import op
import sqlalchemy as sa

revision = "060_teaching_groups"
down_revision = "059_curriculum_matrix"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teaching_groups",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("academic_year_id", sa.Uuid(), nullable=False),
        sa.Column("discipline_id", sa.Uuid(), nullable=False),
        sa.Column("origin_class_id", sa.Uuid(), nullable=True),
        sa.Column("school_unit_id", sa.Uuid(), nullable=True),
        sa.Column("external_id", sa.String(length=255), nullable=True),
        sa.Column("name", sa.String(length=255), nullable=False),
        sa.Column("kind", sa.String(length=20), nullable=False, server_default="CLASS_WIDE"),
        sa.Column("status", sa.String(length=20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT",
                                name="fk_teaching_groups_school"),
        sa.ForeignKeyConstraint(["school_id", "discipline_id"],
                                ["disciplines.school_id", "disciplines.id"],
                                ondelete="RESTRICT",
                                name="fk_teaching_groups_discipline_same_school"),
        sa.ForeignKeyConstraint(["school_id", "academic_year_id"],
                                ["academic_years.school_id", "academic_years.id"],
                                ondelete="RESTRICT", name="fk_teaching_groups_year_same_school"),
        sa.ForeignKeyConstraint(["school_id", "origin_class_id"],
                                ["classes.school_id", "classes.id"],
                                ondelete="RESTRICT",
                                name="fk_teaching_groups_origin_class_same_school"),
        sa.UniqueConstraint("school_id", "external_id",
                            name="uq_teaching_groups_school_external_id"),
        sa.UniqueConstraint("school_id", "id", name="uq_teaching_groups_school_id_id"),
        sa.CheckConstraint("kind IN ('CLASS_WIDE', 'SPLIT', 'CROSS_CLASS')",
                           name="ck_teaching_groups_kind"),
        sa.CheckConstraint(
            "(kind = 'CROSS_CLASS' AND origin_class_id IS NULL) "
            "OR (kind IN ('CLASS_WIDE', 'SPLIT') AND origin_class_id IS NOT NULL)",
            name="ck_teaching_groups_origin_matches_kind"),
    )
    op.create_index("ix_teaching_groups_school_id", "teaching_groups", ["school_id"])
    op.create_index("ix_teaching_groups_lookup", "teaching_groups",
                    ["academic_year_id", "discipline_id"])
    op.create_index("ix_teaching_groups_origin_class_id", "teaching_groups", ["origin_class_id"])

    op.create_table(
        "teaching_group_members",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("teaching_group_id", sa.Uuid(), nullable=False),
        sa.Column("student_enrollment_id", sa.Uuid(), nullable=False),
        sa.Column("joined_on", sa.Date(), nullable=False),
        sa.Column("left_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT",
                                name="fk_teaching_group_members_school"),
        sa.ForeignKeyConstraint(["school_id", "teaching_group_id"],
                                ["teaching_groups.school_id", "teaching_groups.id"],
                                ondelete="CASCADE",
                                name="fk_teaching_group_members_group_same_school"),
        sa.ForeignKeyConstraint(["school_id", "student_enrollment_id"],
                                ["student_enrollments.school_id", "student_enrollments.id"],
                                ondelete="RESTRICT",
                                name="fk_teaching_group_members_enrollment_same_school"),
        sa.UniqueConstraint("teaching_group_id", "student_enrollment_id", "joined_on",
                            name="uq_teaching_group_members_group_enrollment_join"),
        sa.CheckConstraint("left_on IS NULL OR left_on >= joined_on",
                           name="ck_teaching_group_members_date_order"),
    )
    op.create_index("ix_teaching_group_members_school_id", "teaching_group_members", ["school_id"])
    op.create_index("ix_teaching_group_members_group_id", "teaching_group_members",
                    ["teaching_group_id"])
    op.create_index("ix_teaching_group_members_enrollment_id", "teaching_group_members",
                    ["student_enrollment_id"])


def downgrade() -> None:
    op.drop_index("ix_teaching_group_members_enrollment_id", table_name="teaching_group_members")
    op.drop_index("ix_teaching_group_members_group_id", table_name="teaching_group_members")
    op.drop_index("ix_teaching_group_members_school_id", table_name="teaching_group_members")
    op.drop_table("teaching_group_members")
    op.drop_index("ix_teaching_groups_origin_class_id", table_name="teaching_groups")
    op.drop_index("ix_teaching_groups_lookup", table_name="teaching_groups")
    op.drop_index("ix_teaching_groups_school_id", table_name="teaching_groups")
    op.drop_table("teaching_groups")
```

- [ ] **Step 7: Apply and reverse the migration**

Run:
```bash
alembic upgrade head && alembic downgrade 059_curriculum_matrix && alembic upgrade head
```
Expected: os três comandos terminam sem erro.

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/db/models/teaching_group.py \
        src/agente_ia_edu/db/models/__init__.py \
        migrations/versions/060_teaching_groups.py \
        tests/test_teaching_group_model.py
git commit -m "feat(secretaria): grupos de ensino e composicao no tempo"
```

---

### Task 5: Alocação docente e grade horária — TeachingAssignment, TimetableSlot

**Files:**
- Modify: `src/agente_ia_edu/db/models/teaching_group.py`
- Create: `migrations/versions/061_assignments_timetable.py`
- Modify: `src/agente_ia_edu/db/models/__init__.py`
- Test: `tests/test_assignments_timetable.py`

**Interfaces:**
- Consumes: `Teacher`, `Room` (Task 1); `TeachingGroup` (Task 4); `AcademicModelCase`, `build_school` (Task 2).
- Produces: `TeachingAssignment` com `teaching_group_id`, `teacher_id`, `role: str` em `LEAD`/`ASSISTANT`, `starts_on: date`, `ends_on: date | None`. `TimetableSlot` com `teaching_group_id`, `weekday: int` (0–6), `period_ordinal: int`, `starts_at: time | None`, `ends_at: time | None`, `room_id: uuid.UUID | None`, `is_double: bool`. A Task 6 consulta ambas para detectar conflito.

- [ ] **Step 1: Write the failing test**

Crie `tests/test_assignments_timetable.py`:

```python
"""Alocação docente e grade horária semanal.

A co-docência não é caso especial: TeachingAssignment é N:N por natureza, então
dois professores no mesmo grupo são duas linhas, sem coluna de "substituto" nem
flag.
"""

import unittest
from datetime import date, time

from sqlalchemy.exc import IntegrityError

from agente_ia_edu.db.models import (
    Discipline,
    Person,
    Teacher,
    TeachingAssignment,
    TeachingGroup,
    TimetableSlot,
)
from tests._academic_fixtures import AcademicModelCase, build_school


class _Scaffold(AcademicModelCase):
    async def _group(self, session, fx):
        discipline = Discipline(school_id=fx.school.id, name="Matemática", ordinal=1)
        session.add(discipline)
        await session.flush()
        group = TeachingGroup(
            school_id=fx.school.id,
            academic_year_id=fx.year.id,
            discipline_id=discipline.id,
            name="Matemática — 1ª A",
            kind="CLASS_WIDE",
            origin_class_id=fx.klass.id,
        )
        session.add(group)
        await session.flush()
        return group

    async def _teacher(self, session, fx, name="Prof. Mendes"):
        person = Person(school_id=fx.school.id, full_name=name)
        session.add(person)
        await session.flush()
        teacher = Teacher(school_id=fx.school.id, person_id=person.id)
        session.add(teacher)
        await session.flush()
        return teacher


class TeachingAssignmentCase(_Scaffold):
    async def test_two_teachers_can_share_one_group(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "A1")
            group = await self._group(session, fx)
            lead = await self._teacher(session, fx, "Prof. Mendes")
            assistant = await self._teacher(session, fx, "Prof. Lima")

            session.add_all([
                TeachingAssignment(
                    school_id=fx.school.id,
                    teaching_group_id=group.id,
                    teacher_id=lead.id,
                    role="LEAD",
                    starts_on=date(2026, 2, 2),
                ),
                TeachingAssignment(
                    school_id=fx.school.id,
                    teaching_group_id=group.id,
                    teacher_id=assistant.id,
                    role="ASSISTANT",
                    starts_on=date(2026, 2, 2),
                ),
            ])
            await session.flush()

    async def test_unknown_role_is_rejected(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "A2")
            group = await self._group(session, fx)
            teacher = await self._teacher(session, fx)

            session.add(
                TeachingAssignment(
                    school_id=fx.school.id,
                    teaching_group_id=group.id,
                    teacher_id=teacher.id,
                    role="ESTAGIARIO",
                    starts_on=date(2026, 2, 2),
                )
            )
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_teacher_from_another_school_is_rejected(self):
        async with self.session_factory() as session:
            fx_a = await build_school(session, "AA")
            fx_b = await build_school(session, "AB")
            group = await self._group(session, fx_a)
            alien = await self._teacher(session, fx_b, "Alheio")

            session.add(
                TeachingAssignment(
                    school_id=fx_a.school.id,
                    teaching_group_id=group.id,
                    teacher_id=alien.id,
                    role="LEAD",
                    starts_on=date(2026, 2, 2),
                )
            )
            with self.assertRaises(IntegrityError):
                await session.flush()


class TimetableSlotCase(_Scaffold):
    async def test_slot_is_created(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "S1")
            group = await self._group(session, fx)

            slot = TimetableSlot(
                school_id=fx.school.id,
                teaching_group_id=group.id,
                weekday=1,
                period_ordinal=1,
                starts_at=time(7, 30),
                ends_at=time(8, 20),
            )
            session.add(slot)
            await session.flush()

            self.assertFalse(slot.is_double)

    async def test_group_cannot_occupy_the_same_slot_twice(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "S2")
            group = await self._group(session, fx)
            common = dict(
                school_id=fx.school.id,
                teaching_group_id=group.id,
                weekday=1,
                period_ordinal=1,
            )
            session.add(TimetableSlot(**common))
            await session.flush()
            session.add(TimetableSlot(**common))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_weekday_out_of_range_is_rejected(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "S3")
            group = await self._group(session, fx)

            session.add(
                TimetableSlot(
                    school_id=fx.school.id,
                    teaching_group_id=group.id,
                    weekday=9,
                    period_ordinal=1,
                )
            )
            with self.assertRaises(IntegrityError):
                await session.flush()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_assignments_timetable.py -v`
Expected: FAIL com `ImportError: cannot import name 'TeachingAssignment'`

- [ ] **Step 3: Add the models**

Acrescente ao fim de `src/agente_ia_edu/db/models/teaching_group.py`:

```python
class TeachingAssignment(Base):
    """Professor alocado a um grupo de ensino.

    Relação N:N por natureza, o que faz a co-docência sair de graça: dois
    titulares, ou titular mais auxiliar, são duas linhas. A substituição
    temporária é uma linha com starts_on/ends_on delimitados.
    """

    __tablename__ = "teaching_assignments"
    __table_args__ = (
        UniqueConstraint(
            "teaching_group_id", "teacher_id", "starts_on",
            name="uq_teaching_assignments_group_teacher_start",
        ),
        CheckConstraint("role IN ('LEAD', 'ASSISTANT')", name="ck_teaching_assignments_role"),
        CheckConstraint(
            "ends_on IS NULL OR ends_on >= starts_on",
            name="ck_teaching_assignments_date_order",
        ),
        ForeignKeyConstraint(
            ["school_id", "teaching_group_id"],
            ["teaching_groups.school_id", "teaching_groups.id"],
            ondelete="CASCADE",
            name="fk_teaching_assignments_group_same_school",
        ),
        ForeignKeyConstraint(
            ["school_id", "teacher_id"],
            ["teachers.school_id", "teachers.id"],
            ondelete="RESTRICT",
            name="fk_teaching_assignments_teacher_same_school",
        ),
        Index("ix_teaching_assignments_school_id", "school_id"),
        Index("ix_teaching_assignments_group_id", "teaching_group_id"),
        Index("ix_teaching_assignments_teacher_id", "teacher_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    teaching_group_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    teacher_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    role: Mapped[str] = mapped_column(String(20), nullable=False, default="LEAD")
    starts_on: Mapped[date] = mapped_column(Date, nullable=False)
    ends_on: Mapped[date | None] = mapped_column(Date)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class TimetableSlot(Base):
    """Uma posição fixa da grade semanal ocupada por um grupo.

    É o molde a partir do qual SP1 materializa as aulas previstas do ano,
    cruzando estes slots com os dias letivos do calendário.
    """

    __tablename__ = "timetable_slots"
    __table_args__ = (
        UniqueConstraint(
            "teaching_group_id", "weekday", "period_ordinal",
            name="uq_timetable_slots_group_weekday_period",
        ),
        CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_timetable_slots_weekday"),
        CheckConstraint("period_ordinal > 0", name="ck_timetable_slots_period"),
        ForeignKeyConstraint(
            ["school_id", "teaching_group_id"],
            ["teaching_groups.school_id", "teaching_groups.id"],
            ondelete="CASCADE",
            name="fk_timetable_slots_group_same_school",
        ),
        ForeignKeyConstraint(
            ["school_id", "room_id"],
            ["rooms.school_id", "rooms.id"],
            ondelete="RESTRICT",
            name="fk_timetable_slots_room_same_school",
        ),
        Index("ix_timetable_slots_school_id", "school_id"),
        Index("ix_timetable_slots_group_id", "teaching_group_id"),
        Index("ix_timetable_slots_weekday_period", "weekday", "period_ordinal"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    teaching_group_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    room_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
    weekday: Mapped[int] = mapped_column(Integer, nullable=False)
    period_ordinal: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_at: Mapped[time | None] = mapped_column(Time)
    ends_at: Mapped[time | None] = mapped_column(Time)
    is_double: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
```

- [ ] **Step 4: Export the models**

Acrescente `TeachingAssignment` e `TimetableSlot` ao bloco `from .teaching_group import (...)` e ao `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_assignments_timetable.py -v`
Expected: PASS, 6 testes.

- [ ] **Step 6: Write the migration**

Crie `migrations/versions/061_assignments_timetable.py`:

```python
"""Secretaria academica - alocacao docente e grade horaria.

Revision ID: 061_assignments_timetable
Revises: 060_teaching_groups
"""

from alembic import op
import sqlalchemy as sa

revision = "061_assignments_timetable"
down_revision = "060_teaching_groups"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "teaching_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("teaching_group_id", sa.Uuid(), nullable=False),
        sa.Column("teacher_id", sa.Uuid(), nullable=False),
        sa.Column("role", sa.String(length=20), nullable=False, server_default="LEAD"),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT",
                                name="fk_teaching_assignments_school"),
        sa.ForeignKeyConstraint(["school_id", "teaching_group_id"],
                                ["teaching_groups.school_id", "teaching_groups.id"],
                                ondelete="CASCADE",
                                name="fk_teaching_assignments_group_same_school"),
        sa.ForeignKeyConstraint(["school_id", "teacher_id"],
                                ["teachers.school_id", "teachers.id"],
                                ondelete="RESTRICT",
                                name="fk_teaching_assignments_teacher_same_school"),
        sa.UniqueConstraint("teaching_group_id", "teacher_id", "starts_on",
                            name="uq_teaching_assignments_group_teacher_start"),
        sa.CheckConstraint("role IN ('LEAD', 'ASSISTANT')", name="ck_teaching_assignments_role"),
        sa.CheckConstraint("ends_on IS NULL OR ends_on >= starts_on",
                           name="ck_teaching_assignments_date_order"),
    )
    op.create_index("ix_teaching_assignments_school_id", "teaching_assignments", ["school_id"])
    op.create_index("ix_teaching_assignments_group_id", "teaching_assignments",
                    ["teaching_group_id"])
    op.create_index("ix_teaching_assignments_teacher_id", "teaching_assignments", ["teacher_id"])

    op.create_table(
        "timetable_slots",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("teaching_group_id", sa.Uuid(), nullable=False),
        sa.Column("room_id", sa.Uuid(), nullable=True),
        sa.Column("weekday", sa.Integer(), nullable=False),
        sa.Column("period_ordinal", sa.Integer(), nullable=False),
        sa.Column("starts_at", sa.Time(), nullable=True),
        sa.Column("ends_at", sa.Time(), nullable=True),
        sa.Column("is_double", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT",
                                name="fk_timetable_slots_school"),
        sa.ForeignKeyConstraint(["school_id", "teaching_group_id"],
                                ["teaching_groups.school_id", "teaching_groups.id"],
                                ondelete="CASCADE", name="fk_timetable_slots_group_same_school"),
        sa.ForeignKeyConstraint(["school_id", "room_id"], ["rooms.school_id", "rooms.id"],
                                ondelete="RESTRICT", name="fk_timetable_slots_room_same_school"),
        sa.UniqueConstraint("teaching_group_id", "weekday", "period_ordinal",
                            name="uq_timetable_slots_group_weekday_period"),
        sa.CheckConstraint("weekday BETWEEN 0 AND 6", name="ck_timetable_slots_weekday"),
        sa.CheckConstraint("period_ordinal > 0", name="ck_timetable_slots_period"),
    )
    op.create_index("ix_timetable_slots_school_id", "timetable_slots", ["school_id"])
    op.create_index("ix_timetable_slots_group_id", "timetable_slots", ["teaching_group_id"])
    op.create_index("ix_timetable_slots_weekday_period", "timetable_slots",
                    ["weekday", "period_ordinal"])


def downgrade() -> None:
    op.drop_index("ix_timetable_slots_weekday_period", table_name="timetable_slots")
    op.drop_index("ix_timetable_slots_group_id", table_name="timetable_slots")
    op.drop_index("ix_timetable_slots_school_id", table_name="timetable_slots")
    op.drop_table("timetable_slots")
    op.drop_index("ix_teaching_assignments_teacher_id", table_name="teaching_assignments")
    op.drop_index("ix_teaching_assignments_group_id", table_name="teaching_assignments")
    op.drop_index("ix_teaching_assignments_school_id", table_name="teaching_assignments")
    op.drop_table("teaching_assignments")
```

- [ ] **Step 7: Apply and reverse the migration**

Run:
```bash
alembic upgrade head && alembic downgrade 060_teaching_groups && alembic upgrade head
```
Expected: os três comandos terminam sem erro.

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/db/models/teaching_group.py \
        src/agente_ia_edu/db/models/__init__.py \
        migrations/versions/061_assignments_timetable.py \
        tests/test_assignments_timetable.py
git commit -m "feat(secretaria): alocacao docente e grade horaria semanal"
```

---

### Task 6: Validação de conflito de horário — as três dimensões

**Files:**
- Create: `src/agente_ia_edu/services/timetable_conflicts.py`
- Test: `tests/test_timetable_conflicts.py`

**Interfaces:**
- Consumes: `TimetableSlot`, `TeachingGroup`, `TeachingAssignment`, `TeachingGroupMember` (Tasks 4 e 5).
- Produces: `TimetableConflict` (dataclass congelada com `kind: str`, `weekday: int`, `period_ordinal: int`, `subject_id: uuid.UUID`, `group_ids: tuple[uuid.UUID, ...]`) e a corrotina `find_timetable_conflicts(session, *, school_id, academic_year_id, reference_date) -> list[TimetableConflict]`. As constantes de `kind` são `CONFLICT_TEACHER`, `CONFLICT_ROOM` e `CONFLICT_STUDENT`. A Task 9 expõe isso via API e a Task 11 exibe no portal.

**Por que a dimensão "aluno" existe:** porque os grupos cruzam turmas. Se a 1ª A e a 1ª B compartilham um grupo de Inglês, as duas turmas precisam ter aquele horário sincronizado. Sem essa checagem o choque só aparece em fevereiro, com a escola funcionando.

- [ ] **Step 1: Write the failing test**

Crie `tests/test_timetable_conflicts.py`:

```python
"""As três dimensões de conflito de grade: professor, sala e aluno."""

import unittest
from datetime import date

from agente_ia_edu.db.models import (
    Class,
    Discipline,
    Person,
    Room,
    Student,
    StudentEnrollment,
    Teacher,
    TeachingAssignment,
    TeachingGroup,
    TeachingGroupMember,
    TimetableSlot,
)
from agente_ia_edu.services.timetable_conflicts import (
    CONFLICT_ROOM,
    CONFLICT_STUDENT,
    CONFLICT_TEACHER,
    find_timetable_conflicts,
)
from tests._academic_fixtures import AcademicModelCase, build_school

REFERENCE = date(2026, 3, 10)


class TimetableConflictCase(AcademicModelCase):
    async def _discipline(self, session, fx, name):
        discipline = Discipline(school_id=fx.school.id, name=name, ordinal=1)
        session.add(discipline)
        await session.flush()
        return discipline

    async def _group(self, session, fx, name, kind="CROSS_CLASS", origin=None):
        discipline = await self._discipline(session, fx, name)
        group = TeachingGroup(
            school_id=fx.school.id,
            academic_year_id=fx.year.id,
            discipline_id=discipline.id,
            name=name,
            kind=kind,
            origin_class_id=origin,
        )
        session.add(group)
        await session.flush()
        return group

    async def _teacher(self, session, fx, name):
        person = Person(school_id=fx.school.id, full_name=name)
        session.add(person)
        await session.flush()
        teacher = Teacher(school_id=fx.school.id, person_id=person.id)
        session.add(teacher)
        await session.flush()
        return teacher

    async def _slot(self, session, fx, group, period=1, room=None):
        slot = TimetableSlot(
            school_id=fx.school.id,
            teaching_group_id=group.id,
            weekday=1,
            period_ordinal=period,
            room_id=room.id if room is not None else None,
        )
        session.add(slot)
        await session.flush()
        return slot

    async def _assign(self, session, fx, group, teacher):
        session.add(
            TeachingAssignment(
                school_id=fx.school.id,
                teaching_group_id=group.id,
                teacher_id=teacher.id,
                role="LEAD",
                starts_on=date(2026, 2, 2),
            )
        )
        await session.flush()

    async def test_no_conflict_returns_empty(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "K0")
            group_a = await self._group(session, fx, "Matemática")
            group_b = await self._group(session, fx, "História")
            teacher_a = await self._teacher(session, fx, "Prof. A")
            teacher_b = await self._teacher(session, fx, "Prof. B")
            await self._assign(session, fx, group_a, teacher_a)
            await self._assign(session, fx, group_b, teacher_b)
            await self._slot(session, fx, group_a, period=1)
            await self._slot(session, fx, group_b, period=2)

            conflicts = await find_timetable_conflicts(
                session,
                school_id=fx.school.id,
                academic_year_id=fx.year.id,
                reference_date=REFERENCE,
            )
            self.assertEqual(conflicts, [])

    async def test_teacher_in_two_groups_at_the_same_slot(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "K1")
            group_a = await self._group(session, fx, "Matemática")
            group_b = await self._group(session, fx, "Física")
            teacher = await self._teacher(session, fx, "Prof. Único")
            await self._assign(session, fx, group_a, teacher)
            await self._assign(session, fx, group_b, teacher)
            await self._slot(session, fx, group_a, period=1)
            await self._slot(session, fx, group_b, period=1)

            conflicts = await find_timetable_conflicts(
                session,
                school_id=fx.school.id,
                academic_year_id=fx.year.id,
                reference_date=REFERENCE,
            )
            self.assertEqual(len(conflicts), 1)
            self.assertEqual(conflicts[0].kind, CONFLICT_TEACHER)
            self.assertEqual(conflicts[0].subject_id, teacher.id)
            self.assertEqual(conflicts[0].period_ordinal, 1)

    async def test_room_taken_by_two_groups_at_the_same_slot(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "K2")
            room = Room(school_id=fx.school.id, name="Lab 1", kind="LAB")
            session.add(room)
            await session.flush()

            group_a = await self._group(session, fx, "Química")
            group_b = await self._group(session, fx, "Biologia")
            teacher_a = await self._teacher(session, fx, "Prof. A")
            teacher_b = await self._teacher(session, fx, "Prof. B")
            await self._assign(session, fx, group_a, teacher_a)
            await self._assign(session, fx, group_b, teacher_b)
            await self._slot(session, fx, group_a, period=3, room=room)
            await self._slot(session, fx, group_b, period=3, room=room)

            conflicts = await find_timetable_conflicts(
                session,
                school_id=fx.school.id,
                academic_year_id=fx.year.id,
                reference_date=REFERENCE,
            )
            self.assertEqual([c.kind for c in conflicts], [CONFLICT_ROOM])
            self.assertEqual(conflicts[0].subject_id, room.id)

    async def test_student_in_two_groups_at_the_same_slot(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "K3")
            group_a = await self._group(session, fx, "Inglês Intermediário")
            group_b = await self._group(session, fx, "Espanhol")
            teacher_a = await self._teacher(session, fx, "Prof. A")
            teacher_b = await self._teacher(session, fx, "Prof. B")
            await self._assign(session, fx, group_a, teacher_a)
            await self._assign(session, fx, group_b, teacher_b)
            await self._slot(session, fx, group_a, period=4)
            await self._slot(session, fx, group_b, period=4)

            session.add_all([
                TeachingGroupMember(
                    school_id=fx.school.id,
                    teaching_group_id=group_a.id,
                    student_enrollment_id=fx.enrollment.id,
                    joined_on=date(2026, 2, 2),
                ),
                TeachingGroupMember(
                    school_id=fx.school.id,
                    teaching_group_id=group_b.id,
                    student_enrollment_id=fx.enrollment.id,
                    joined_on=date(2026, 2, 2),
                ),
            ])
            await session.flush()

            conflicts = await find_timetable_conflicts(
                session,
                school_id=fx.school.id,
                academic_year_id=fx.year.id,
                reference_date=REFERENCE,
            )
            self.assertEqual([c.kind for c in conflicts], [CONFLICT_STUDENT])
            self.assertEqual(conflicts[0].subject_id, fx.enrollment.id)

    async def test_membership_ended_before_reference_date_is_not_a_conflict(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "K4")
            group_a = await self._group(session, fx, "Inglês")
            group_b = await self._group(session, fx, "Espanhol")
            teacher_a = await self._teacher(session, fx, "Prof. A")
            teacher_b = await self._teacher(session, fx, "Prof. B")
            await self._assign(session, fx, group_a, teacher_a)
            await self._assign(session, fx, group_b, teacher_b)
            await self._slot(session, fx, group_a, period=5)
            await self._slot(session, fx, group_b, period=5)

            session.add_all([
                TeachingGroupMember(
                    school_id=fx.school.id,
                    teaching_group_id=group_a.id,
                    student_enrollment_id=fx.enrollment.id,
                    joined_on=date(2026, 2, 2),
                    left_on=date(2026, 2, 28),
                ),
                TeachingGroupMember(
                    school_id=fx.school.id,
                    teaching_group_id=group_b.id,
                    student_enrollment_id=fx.enrollment.id,
                    joined_on=date(2026, 3, 1),
                ),
            ])
            await session.flush()

            conflicts = await find_timetable_conflicts(
                session,
                school_id=fx.school.id,
                academic_year_id=fx.year.id,
                reference_date=REFERENCE,
            )
            self.assertEqual(conflicts, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_timetable_conflicts.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.services.timetable_conflicts'`

- [ ] **Step 3: Write the service**

Crie `src/agente_ia_edu/services/timetable_conflicts.py`:

```python
"""Detecção de choque na grade horária.

Três dimensões, não duas. Professor e sala são as óbvias; a do ALUNO existe
porque os grupos de ensino cruzam turmas (spec §3.4): se a 1ª A e a 1ª B
compartilham um grupo de Inglês, as duas turmas precisam ter aquele horário
sincronizado, e sem esta checagem o choque só aparece com a escola funcionando.

A data de referência não é enfeite: alocação docente e composição de grupo têm
vigência (starts_on/ends_on, joined_on/left_on), então "há conflito?" só tem
resposta em relação a um dia. Um professor que substituiu em março e saiu em
abril não conflita em maio.

Spec: docs/superpowers/specs/2026-09-28-secretaria-academica-fundacao-diario-design.md §3.4
"""

from __future__ import annotations

import uuid
from collections import defaultdict
from dataclasses import dataclass
from datetime import date

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    TeachingAssignment,
    TeachingGroup,
    TeachingGroupMember,
    TimetableSlot,
)

CONFLICT_TEACHER = "TEACHER"
CONFLICT_ROOM = "ROOM"
CONFLICT_STUDENT = "STUDENT"


@dataclass(frozen=True)
class TimetableConflict:
    """Um recurso disputado por mais de um grupo no mesmo ponto da grade."""

    kind: str
    weekday: int
    period_ordinal: int
    subject_id: uuid.UUID
    group_ids: tuple[uuid.UUID, ...]


def _collect(bucket: dict, kind: str) -> list[TimetableConflict]:
    """Um subject com mais de um grupo no mesmo slot é um conflito."""
    found: list[TimetableConflict] = []
    for (weekday, period, subject_id), group_ids in sorted(
        bucket.items(), key=lambda item: (item[0][0], item[0][1])
    ):
        if len(group_ids) > 1:
            found.append(
                TimetableConflict(
                    kind=kind,
                    weekday=weekday,
                    period_ordinal=period,
                    subject_id=subject_id,
                    group_ids=tuple(sorted(group_ids, key=str)),
                )
            )
    return found


async def find_timetable_conflicts(
    session: AsyncSession,
    *,
    school_id: uuid.UUID,
    academic_year_id: uuid.UUID,
    reference_date: date,
) -> list[TimetableConflict]:
    """Todos os choques da grade de um ano letivo, numa data de referência."""
    slot_rows = (
        await session.execute(
            select(
                TimetableSlot.teaching_group_id,
                TimetableSlot.weekday,
                TimetableSlot.period_ordinal,
                TimetableSlot.room_id,
            )
            .join(TeachingGroup, TeachingGroup.id == TimetableSlot.teaching_group_id)
            .where(
                TimetableSlot.school_id == school_id,
                TeachingGroup.academic_year_id == academic_year_id,
            )
        )
    ).all()

    if not slot_rows:
        return []

    # group_id -> [(weekday, period), ...]
    positions: dict[uuid.UUID, list[tuple[int, int]]] = defaultdict(list)
    room_bucket: dict[tuple[int, int, uuid.UUID], set[uuid.UUID]] = defaultdict(set)
    for group_id, weekday, period, room_id in slot_rows:
        positions[group_id].append((weekday, period))
        if room_id is not None:
            room_bucket[(weekday, period, room_id)].add(group_id)

    assignment_rows = (
        await session.execute(
            select(TeachingAssignment.teaching_group_id, TeachingAssignment.teacher_id).where(
                TeachingAssignment.school_id == school_id,
                TeachingAssignment.starts_on <= reference_date,
                or_(
                    TeachingAssignment.ends_on.is_(None),
                    TeachingAssignment.ends_on >= reference_date,
                ),
            )
        )
    ).all()

    member_rows = (
        await session.execute(
            select(
                TeachingGroupMember.teaching_group_id,
                TeachingGroupMember.student_enrollment_id,
            ).where(
                TeachingGroupMember.school_id == school_id,
                TeachingGroupMember.joined_on <= reference_date,
                or_(
                    TeachingGroupMember.left_on.is_(None),
                    TeachingGroupMember.left_on >= reference_date,
                ),
            )
        )
    ).all()

    teacher_bucket: dict[tuple[int, int, uuid.UUID], set[uuid.UUID]] = defaultdict(set)
    for group_id, teacher_id in assignment_rows:
        for weekday, period in positions.get(group_id, ()):
            teacher_bucket[(weekday, period, teacher_id)].add(group_id)

    student_bucket: dict[tuple[int, int, uuid.UUID], set[uuid.UUID]] = defaultdict(set)
    for group_id, enrollment_id in member_rows:
        for weekday, period in positions.get(group_id, ()):
            student_bucket[(weekday, period, enrollment_id)].add(group_id)

    return (
        _collect(teacher_bucket, CONFLICT_TEACHER)
        + _collect(room_bucket, CONFLICT_ROOM)
        + _collect(student_bucket, CONFLICT_STUDENT)
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_timetable_conflicts.py -v`
Expected: PASS, 5 testes.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/timetable_conflicts.py \
        tests/test_timetable_conflicts.py
git commit -m "feat(secretaria): deteccao de conflito de grade nas tres dimensoes"
```

---

### Task 7: Criação automática dos grupos CLASS_WIDE

**Files:**
- Create: `src/agente_ia_edu/services/teaching_group_builder.py`
- Test: `tests/test_teaching_group_builder.py`

**Interfaces:**
- Consumes: `CurriculumMatrix` (Task 3), `TeachingGroup`, `TeachingGroupMember` (Task 4), `Class`, `StudentEnrollment`, `Discipline`.
- Produces: a corrotina `ensure_class_wide_groups(session, *, school_id, class_id, reference_date) -> list[TeachingGroup]`, que devolve os grupos existentes ou recém-criados da turma, em ordem de `Discipline.ordinal`. **Idempotente**: chamada duas vezes não duplica grupo nem membro. A Task 9 a chama quando a secretaria abre uma turma.

**Por que existe:** o spec decidiu que todo ensino passa por grupo, inclusive o caso majoritário em que a turma inteira cursa a disciplina. Criar esses grupos à mão seria trabalho manual de dezenas de linhas por turma. Este serviço os deriva da matriz curricular, para que a secretaria nunca precise saber que "grupo" existe.

- [ ] **Step 1: Write the failing test**

Crie `tests/test_teaching_group_builder.py`:

```python
"""Derivação dos grupos CLASS_WIDE a partir da matriz curricular."""

import unittest
from datetime import date

from sqlalchemy import func, select

from agente_ia_edu.db.models import (
    CurriculumMatrix,
    Discipline,
    TeachingGroup,
    TeachingGroupMember,
)
from agente_ia_edu.services.teaching_group_builder import ensure_class_wide_groups
from tests._academic_fixtures import AcademicModelCase, build_school

REFERENCE = date(2026, 2, 2)


class TeachingGroupBuilderCase(AcademicModelCase):
    async def _matrix(self, session, fx, names):
        for ordinal, name in enumerate(names, start=1):
            discipline = Discipline(school_id=fx.school.id, name=name, ordinal=ordinal)
            session.add(discipline)
            await session.flush()
            session.add(
                CurriculumMatrix(
                    school_id=fx.school.id,
                    grade_level_id=fx.grade.id,
                    academic_year_id=fx.year.id,
                    discipline_id=discipline.id,
                    weekly_lessons=ordinal + 1,
                )
            )
        await session.flush()

    async def test_creates_one_group_per_matrix_entry(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "B1")
            await self._matrix(session, fx, ["Matemática", "História", "Física"])

            groups = await ensure_class_wide_groups(
                session,
                school_id=fx.school.id,
                class_id=fx.klass.id,
                reference_date=REFERENCE,
            )

            self.assertEqual(len(groups), 3)
            self.assertTrue(all(g.kind == "CLASS_WIDE" for g in groups))
            self.assertTrue(all(g.origin_class_id == fx.klass.id for g in groups))

    async def test_group_name_reads_as_discipline_and_class(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "B2")
            await self._matrix(session, fx, ["Matemática"])

            groups = await ensure_class_wide_groups(
                session,
                school_id=fx.school.id,
                class_id=fx.klass.id,
                reference_date=REFERENCE,
            )

            self.assertEqual(groups[0].name, "Matemática — A")

    async def test_enrolled_students_become_members(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "B3")
            await self._matrix(session, fx, ["Matemática"])

            groups = await ensure_class_wide_groups(
                session,
                school_id=fx.school.id,
                class_id=fx.klass.id,
                reference_date=REFERENCE,
            )

            members = (
                await session.execute(
                    select(TeachingGroupMember).where(
                        TeachingGroupMember.teaching_group_id == groups[0].id
                    )
                )
            ).scalars().all()

            self.assertEqual(len(members), 1)
            self.assertEqual(members[0].student_enrollment_id, fx.enrollment.id)

    async def test_running_twice_does_not_duplicate(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "B4")
            await self._matrix(session, fx, ["Matemática", "História"])

            await ensure_class_wide_groups(
                session, school_id=fx.school.id, class_id=fx.klass.id,
                reference_date=REFERENCE,
            )
            await ensure_class_wide_groups(
                session, school_id=fx.school.id, class_id=fx.klass.id,
                reference_date=REFERENCE,
            )

            group_count = (
                await session.execute(select(func.count()).select_from(TeachingGroup))
            ).scalar_one()
            member_count = (
                await session.execute(select(func.count()).select_from(TeachingGroupMember))
            ).scalar_one()

            self.assertEqual(group_count, 2)
            self.assertEqual(member_count, 2)

    async def test_empty_matrix_creates_nothing(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "B5")

            groups = await ensure_class_wide_groups(
                session, school_id=fx.school.id, class_id=fx.klass.id,
                reference_date=REFERENCE,
            )

            self.assertEqual(groups, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_teaching_group_builder.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.services.teaching_group_builder'`

- [ ] **Step 3: Write the service**

Crie `src/agente_ia_edu/services/teaching_group_builder.py`:

```python
"""Derivação dos grupos CLASS_WIDE de uma turma a partir da matriz curricular.

O spec decidiu que TODO ensino passa por um grupo, inclusive o caso majoritário
em que a turma inteira cursa a disciplina - foi o preço de não ter dois caminhos
de código em toda consulta, todo fechamento e todo relatório (§3.3). Este
serviço é o que faz esse preço não recair sobre a secretaria: os grupos triviais
nascem da matriz, e a interface nunca precisa pronunciar a palavra "grupo".

Idempotente por desenho: a secretaria abre a turma, acrescenta uma disciplina à
matriz e roda de novo, e só o que falta é criado.

Spec: docs/superpowers/specs/2026-09-28-secretaria-academica-fundacao-diario-design.md §3.3
"""

from __future__ import annotations

import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    Class,
    CurriculumMatrix,
    Discipline,
    StudentEnrollment,
    TeachingGroup,
    TeachingGroupMember,
)


async def ensure_class_wide_groups(
    session: AsyncSession,
    *,
    school_id: uuid.UUID,
    class_id: uuid.UUID,
    reference_date: date,
) -> list[TeachingGroup]:
    """Garante um grupo CLASS_WIDE por entrada da matriz curricular da turma."""
    klass = (
        await session.execute(
            select(Class).where(Class.school_id == school_id, Class.id == class_id)
        )
    ).scalar_one_or_none()
    if klass is None:
        return []

    matrix_rows = (
        await session.execute(
            select(CurriculumMatrix.discipline_id, Discipline.name, Discipline.ordinal)
            .join(Discipline, Discipline.id == CurriculumMatrix.discipline_id)
            .where(
                CurriculumMatrix.school_id == school_id,
                CurriculumMatrix.grade_level_id == klass.grade_level_id,
                CurriculumMatrix.academic_year_id == klass.academic_year_id,
            )
            .order_by(Discipline.ordinal, Discipline.name)
        )
    ).all()
    if not matrix_rows:
        return []

    existing = {
        group.discipline_id: group
        for group in (
            await session.execute(
                select(TeachingGroup).where(
                    TeachingGroup.school_id == school_id,
                    TeachingGroup.origin_class_id == class_id,
                    TeachingGroup.kind == "CLASS_WIDE",
                )
            )
        ).scalars()
    }

    enrollment_ids = list(
        (
            await session.execute(
                select(StudentEnrollment.id).where(
                    StudentEnrollment.school_id == school_id,
                    StudentEnrollment.class_id == class_id,
                    StudentEnrollment.status == "ACTIVE",
                )
            )
        ).scalars()
    )

    groups: list[TeachingGroup] = []
    for discipline_id, discipline_name, _ordinal in matrix_rows:
        group = existing.get(discipline_id)
        if group is None:
            group = TeachingGroup(
                school_id=school_id,
                academic_year_id=klass.academic_year_id,
                discipline_id=discipline_id,
                origin_class_id=class_id,
                school_unit_id=klass.school_unit_id,
                name=f"{discipline_name} — {klass.name}",
                kind="CLASS_WIDE",
            )
            session.add(group)
            await session.flush()

        already = set(
            (
                await session.execute(
                    select(TeachingGroupMember.student_enrollment_id).where(
                        TeachingGroupMember.teaching_group_id == group.id
                    )
                )
            ).scalars()
        )
        for enrollment_id in enrollment_ids:
            if enrollment_id in already:
                continue
            session.add(
                TeachingGroupMember(
                    school_id=school_id,
                    teaching_group_id=group.id,
                    student_enrollment_id=enrollment_id,
                    joined_on=reference_date,
                )
            )
        groups.append(group)

    await session.flush()
    return groups
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_teaching_group_builder.py -v`
Expected: PASS, 5 testes.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/teaching_group_builder.py \
        tests/test_teaching_group_builder.py
git commit -m "feat(secretaria): grupos CLASS_WIDE derivados da matriz curricular"
```

---

### Task 8: Dados civis — PersonCivilRecord

**Files:**
- Create: `src/agente_ia_edu/db/models/person_civil.py`
- Create: `migrations/versions/062_person_civil_record.py`
- Modify: `src/agente_ia_edu/db/models/__init__.py`
- Test: `tests/test_person_civil_record.py`

**Interfaces:**
- Consumes: `Person` existente; `AcademicModelCase`, `build_school` (Task 2).
- Produces: `PersonCivilRecord`, um-para-um com `Person` (`person_id` é `UNIQUE`), com `birth_date: date`, `sex`, `race_color`, `nationality`, `birth_municipality`, `birth_state`, `mother_name`, `father_name`, `cpf`, `rg`, `nis`, `birth_certificate`, `has_disability: bool`, `disability_details`. SP5 (histórico e certificado) e SP6 (Educacenso) leem daqui.

**Por que é tabela separada:** cor/raça e deficiência são dado pessoal **sensível** sob a LGPD, referente a menores de idade. Em tabela própria o acesso é controlado e auditável por si só, e `Person` — carregada em praticamente toda consulta do sistema — continua sem transportar dado sensível.

- [ ] **Step 1: Write the failing test**

Crie `tests/test_person_civil_record.py`:

```python
"""Ficha civil: o que o histórico escolar e o Educacenso exigem."""

import unittest
from datetime import date

from sqlalchemy.exc import IntegrityError

from agente_ia_edu.db.models import Person, PersonCivilRecord
from tests._academic_fixtures import AcademicModelCase, build_school


class PersonCivilRecordCase(AcademicModelCase):
    async def test_record_is_created_for_a_person(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "P1")

            record = PersonCivilRecord(
                school_id=fx.school.id,
                person_id=fx.person.id,
                birth_date=date(2009, 5, 14),
                sex="F",
                race_color="PARDA",
                nationality="BRASILEIRA",
                birth_municipality="Salvador",
                birth_state="BA",
                mother_name="Maria Souza",
            )
            session.add(record)
            await session.flush()

            self.assertFalse(record.has_disability)

    async def test_one_record_per_person(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "P2")
            session.add(
                PersonCivilRecord(
                    school_id=fx.school.id,
                    person_id=fx.person.id,
                    birth_date=date(2009, 5, 14),
                )
            )
            await session.flush()
            session.add(
                PersonCivilRecord(
                    school_id=fx.school.id,
                    person_id=fx.person.id,
                    birth_date=date(2009, 5, 14),
                )
            )
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_unknown_race_color_is_rejected(self):
        async with self.session_factory() as session:
            fx = await build_school(session, "P3")
            session.add(
                PersonCivilRecord(
                    school_id=fx.school.id,
                    person_id=fx.person.id,
                    birth_date=date(2009, 5, 14),
                    race_color="ROXA",
                )
            )
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_person_from_another_school_is_rejected(self):
        async with self.session_factory() as session:
            fx_a = await build_school(session, "PA")
            fx_b = await build_school(session, "PB")

            session.add(
                PersonCivilRecord(
                    school_id=fx_a.school.id,
                    person_id=fx_b.person.id,
                    birth_date=date(2009, 5, 14),
                )
            )
            with self.assertRaises(IntegrityError):
                await session.flush()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_person_civil_record.py -v`
Expected: FAIL com `ImportError: cannot import name 'PersonCivilRecord'`

- [ ] **Step 3: Write the model**

Crie `src/agente_ia_edu/db/models/person_civil.py`:

```python
"""Ficha civil da pessoa - dado pessoal sensível, em tabela própria.

Separada de Person de propósito. Cor/raça e deficiência são dado pessoal
SENSÍVEL sob a LGPD, e aqui se referem majoritariamente a menores de idade. Numa
tabela própria o acesso é controlado e auditável por si só, e Person - que é
carregada em praticamente toda consulta do sistema - continua sem transportar
dado sensível a cada leitura.

Os valores de race_color seguem a categorização do IBGE, que é a usada pelo
Educacenso. O layout exato do censo deve ser conferido contra a publicação do
ano vigente do INEP em SP6; esta tabela guarda o dado, não presume o formato de
exportação.

Spec: docs/superpowers/specs/2026-09-28-secretaria-academica-fundacao-diario-design.md §3.2
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    String,
    Text,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class PersonCivilRecord(Base):
    """Dados civis de uma pessoa, um para um com Person."""

    __tablename__ = "person_civil_records"
    __table_args__ = (
        UniqueConstraint("person_id", name="uq_person_civil_records_person"),
        CheckConstraint(
            "race_color IS NULL OR race_color IN "
            "('BRANCA', 'PRETA', 'PARDA', 'AMARELA', 'INDIGENA', 'NAO_DECLARADA')",
            name="ck_person_civil_records_race_color",
        ),
        CheckConstraint(
            "sex IS NULL OR sex IN ('F', 'M', 'NAO_INFORMADO')",
            name="ck_person_civil_records_sex",
        ),
        ForeignKeyConstraint(
            ["school_id", "person_id"],
            ["persons.school_id", "persons.id"],
            ondelete="CASCADE",
            name="fk_person_civil_records_person_same_school",
        ),
        Index("ix_person_civil_records_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)

    birth_date: Mapped[date | None] = mapped_column(Date)
    sex: Mapped[str | None] = mapped_column(String(20))
    race_color: Mapped[str | None] = mapped_column(String(20))
    nationality: Mapped[str | None] = mapped_column(String(100))
    birth_municipality: Mapped[str | None] = mapped_column(String(255))
    birth_state: Mapped[str | None] = mapped_column(String(2))

    mother_name: Mapped[str | None] = mapped_column(String(255))
    father_name: Mapped[str | None] = mapped_column(String(255))

    cpf: Mapped[str | None] = mapped_column(String(14))
    rg: Mapped[str | None] = mapped_column(String(30))
    nis: Mapped[str | None] = mapped_column(String(20))
    birth_certificate: Mapped[str | None] = mapped_column(String(50))

    has_disability: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    disability_details: Mapped[str | None] = mapped_column(Text)

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )
```

- [ ] **Step 4: Export the model**

Acrescente a `src/agente_ia_edu/db/models/__init__.py`:

```python
from .person_civil import PersonCivilRecord
```
e inclua `PersonCivilRecord` em `__all__`.

- [ ] **Step 5: Run test to verify it passes**

Run: `python -m pytest tests/test_person_civil_record.py -v`
Expected: PASS, 4 testes.

- [ ] **Step 6: Write the migration**

Crie `migrations/versions/062_person_civil_record.py`:

```python
"""Secretaria academica - ficha civil da pessoa.

Revision ID: 062_person_civil_record
Revises: 061_assignments_timetable

Tabela propria, e nao colunas em persons: cor/raca e deficiencia sao dado
pessoal sensivel sob a LGPD, referente majoritariamente a menores. Separar
mantem persons - carregada em quase toda consulta - livre de dado sensivel e
torna o acesso a ficha auditavel por si so.
"""

from alembic import op
import sqlalchemy as sa

revision = "062_person_civil_record"
down_revision = "061_assignments_timetable"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "person_civil_records",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("birth_date", sa.Date(), nullable=True),
        sa.Column("sex", sa.String(length=20), nullable=True),
        sa.Column("race_color", sa.String(length=20), nullable=True),
        sa.Column("nationality", sa.String(length=100), nullable=True),
        sa.Column("birth_municipality", sa.String(length=255), nullable=True),
        sa.Column("birth_state", sa.String(length=2), nullable=True),
        sa.Column("mother_name", sa.String(length=255), nullable=True),
        sa.Column("father_name", sa.String(length=255), nullable=True),
        sa.Column("cpf", sa.String(length=14), nullable=True),
        sa.Column("rg", sa.String(length=30), nullable=True),
        sa.Column("nis", sa.String(length=20), nullable=True),
        sa.Column("birth_certificate", sa.String(length=50), nullable=True),
        sa.Column("has_disability", sa.Boolean(), nullable=False, server_default=sa.false()),
        sa.Column("disability_details", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False,
                  server_default=sa.text("CURRENT_TIMESTAMP")),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT",
                                name="fk_person_civil_records_school"),
        sa.ForeignKeyConstraint(["school_id", "person_id"],
                                ["persons.school_id", "persons.id"],
                                ondelete="CASCADE",
                                name="fk_person_civil_records_person_same_school"),
        sa.UniqueConstraint("person_id", name="uq_person_civil_records_person"),
        sa.CheckConstraint(
            "race_color IS NULL OR race_color IN "
            "('BRANCA', 'PRETA', 'PARDA', 'AMARELA', 'INDIGENA', 'NAO_DECLARADA')",
            name="ck_person_civil_records_race_color"),
        sa.CheckConstraint("sex IS NULL OR sex IN ('F', 'M', 'NAO_INFORMADO')",
                           name="ck_person_civil_records_sex"),
    )
    op.create_index("ix_person_civil_records_school_id", "person_civil_records", ["school_id"])


def downgrade() -> None:
    op.drop_index("ix_person_civil_records_school_id", table_name="person_civil_records")
    op.drop_table("person_civil_records")
```

- [ ] **Step 7: Apply and reverse the migration**

Run:
```bash
alembic upgrade head && alembic downgrade 061_assignments_timetable && alembic upgrade head
```
Expected: os três comandos terminam sem erro.

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/db/models/person_civil.py \
        src/agente_ia_edu/db/models/__init__.py \
        migrations/versions/062_person_civil_record.py \
        tests/test_person_civil_record.py
git commit -m "feat(secretaria): ficha civil em tabela propria (LGPD)"
```

---

### Task 9: Guard de domínio e API da secretaria

**Files:**
- Modify: `src/agente_ia_edu/api/dependencies.py`
- Create: `src/agente_ia_edu/api/schemas/academic_registry.py`
- Create: `src/agente_ia_edu/api/routes/academic_registry.py`
- Modify: `src/agente_ia_edu/api/app.py`
- Test: `tests/test_academic_registry_access.py`

**Interfaces:**
- Consumes: `AuthenticatedUserContext` de `agente_ia_edu.identity` (campos `role`, `school_id`, `is_platform_admin`); `find_timetable_conflicts` (Task 6); `ensure_class_wide_groups` (Task 7); `Discipline` (Task 1).
- Produces: `require_academic_registry_access(context) -> AuthenticatedUserContext` em `api/dependencies.py`, e `academic_registry_router` (prefixo `/api/v1/academic-registry`) com quatro rotas: `GET /disciplines`, `POST /disciplines`, `POST /classes/{class_id}/teaching-groups`, `GET /timetable/conflicts`.

**A decisão central desta task:** o `reject_reception_only_role` é aplicado **router a router** em `api/app.py`, não globalmente. Este router simplesmente **não o recebe** — recebe o guard próprio. Nenhuma linha do comportamento atual muda, e a trava continua valendo para tudo que já existe.

- [ ] **Step 1: Write the failing test**

Crie `tests/test_academic_registry_access.py`:

```python
"""Quem entra no domínio da secretaria acadêmica, e como o router é montado.

O segundo teste é o que prova a promessa do spec §7.2: o router novo NÃO carrega
o reject_reception_only_role, que é o que hoje barra o SECRETARY. Se alguém o
acrescentar por hábito, este teste cai.
"""

import asyncio
import unittest

from fastapi import HTTPException

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import (
    reject_reception_only_role,
    require_academic_registry_access,
)
from agente_ia_edu.identity import AuthenticatedUserContext


def _context(role: str, school_id: str | None = "school-1", platform_admin: bool = False):
    return AuthenticatedUserContext(
        user_id="u1",
        external_identity_id="u1",
        role=role,
        school_id=school_id,
        is_platform_admin=platform_admin,
    )


class RequireAcademicRegistryAccessCase(unittest.TestCase):
    def test_secretary_is_allowed(self):
        context = _context("SECRETARY")
        result = asyncio.run(require_academic_registry_access(context=context))
        self.assertIs(result, context)

    def test_coordinator_and_director_are_allowed(self):
        for role in ("COORDINATOR", "DIRECTOR"):
            with self.subTest(role=role):
                context = _context(role)
                self.assertIs(
                    asyncio.run(require_academic_registry_access(context=context)), context
                )

    def test_teacher_is_refused(self):
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(require_academic_registry_access(context=_context("TEACHER")))
        self.assertEqual(caught.exception.status_code, 403)

    def test_student_is_refused(self):
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(require_academic_registry_access(context=_context("STUDENT")))
        self.assertEqual(caught.exception.status_code, 403)

    def test_school_scoped_role_without_school_is_refused(self):
        with self.assertRaises(HTTPException) as caught:
            asyncio.run(
                require_academic_registry_access(context=_context("SECRETARY", school_id=None))
            )
        self.assertEqual(caught.exception.status_code, 403)

    def test_platform_admin_without_school_is_allowed(self):
        context = _context("PLATFORM_ADMIN", school_id=None, platform_admin=True)
        self.assertIs(asyncio.run(require_academic_registry_access(context=context)), context)


class RouterWiringCase(unittest.TestCase):
    def test_registry_routes_do_not_carry_the_reception_guard(self):
        app = create_app()
        registry_routes = [
            route for route in app.routes
            if getattr(route, "path", "").startswith("/api/v1/academic-registry")
        ]
        self.assertTrue(registry_routes, "o router da secretaria não foi registrado no app")

        for route in registry_routes:
            with self.subTest(path=route.path):
                callables = [dep.call for dep in route.dependant.dependencies]
                self.assertNotIn(reject_reception_only_role, callables)
                self.assertIn(require_academic_registry_access, callables)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_academic_registry_access.py -v`
Expected: FAIL com `ImportError: cannot import name 'require_academic_registry_access'`

- [ ] **Step 3: Write the guard**

Acrescente a `src/agente_ia_edu/api/dependencies.py`, logo abaixo de `reject_reception_only_role`:

```python
_ACADEMIC_REGISTRY_ROLES = frozenset(
    {"PLATFORM_ADMIN", "DIRECTOR", "COORDINATOR", "SECRETARY"}
)


async def require_academic_registry_access(
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
) -> AuthenticatedUserContext:
    """Porta do domínio da secretaria acadêmica.

    TEACHER fica de fora de propósito: professor registra aula e chamada (SP1),
    não faz cadastro. E secretaria não lança frequência - quem não deu a aula
    não registra quem estava nela, que é o que torna o diário sustentável numa
    auditoria (spec §7.1).

    Este guard SUBSTITUI reject_reception_only_role neste domínio; o router da
    secretaria não recebe aquele, que continua valendo para todos os demais.
    """
    role = (context.role or "").upper()
    if role not in _ACADEMIC_REGISTRY_ROLES:
        raise HTTPException(
            status_code=403,
            detail="Acesso restrito à secretaria acadêmica.",
        )
    if not context.is_platform_admin and not context.school_id:
        raise HTTPException(
            status_code=403,
            detail="Vínculo com escola é obrigatório neste domínio.",
        )
    return context
```

Acrescente os imports que faltarem no topo do arquivo (`Depends` de `fastapi`, `AuthenticatedUserContext` de `...identity`) e inclua `"require_academic_registry_access"` no `__all__`.

- [ ] **Step 4: Write the schemas**

Crie `src/agente_ia_edu/api/schemas/academic_registry.py`:

```python
"""Contratos HTTP do cadastro acadêmico."""

from __future__ import annotations

import uuid
from datetime import date

from pydantic import BaseModel, Field


class DisciplineCreateRequest(BaseModel):
    name: str = Field(min_length=1, max_length=255)
    short_name: str | None = Field(default=None, max_length=20)
    knowledge_area: str | None = Field(default=None, max_length=255)
    ordinal: int = 0
    external_id: str | None = Field(default=None, max_length=255)


class DisciplineResponse(BaseModel):
    id: uuid.UUID
    school_id: uuid.UUID
    name: str
    short_name: str | None
    knowledge_area: str | None
    ordinal: int
    status: str


class TeachingGroupResponse(BaseModel):
    id: uuid.UUID
    name: str
    kind: str
    discipline_id: uuid.UUID
    origin_class_id: uuid.UUID | None


class EnsureGroupsRequest(BaseModel):
    reference_date: date


class TimetableConflictResponse(BaseModel):
    kind: str
    weekday: int
    period_ordinal: int
    subject_id: uuid.UUID
    group_ids: list[uuid.UUID]
```

- [ ] **Step 5: Write the router**

Crie `src/agente_ia_edu/api/routes/academic_registry.py`:

```python
"""Cadastro acadêmico da secretaria.

Este router NÃO recebe reject_reception_only_role em app.py. A trava do
SECRETARY continua valendo para todos os demais routers; aqui vale
require_academic_registry_access, que é a porta deste domínio (spec §7.2).
"""

from __future__ import annotations

import uuid
from datetime import date

from fastapi import APIRouter, Depends, Query
from sqlalchemy import select

from ..dependencies import get_session_factory, require_academic_registry_access
from ..schemas.academic_registry import (
    DisciplineCreateRequest,
    DisciplineResponse,
    EnsureGroupsRequest,
    TeachingGroupResponse,
    TimetableConflictResponse,
)
from ...db.models import Discipline
from ...identity import AuthenticatedUserContext
from ...services.teaching_group_builder import ensure_class_wide_groups
from ...services.timetable_conflicts import find_timetable_conflicts

academic_registry_router = APIRouter(
    prefix="/api/v1/academic-registry",
    tags=["academic-registry"],
    dependencies=[Depends(require_academic_registry_access)],
)


def _school_id(context: AuthenticatedUserContext) -> uuid.UUID:
    return uuid.UUID(str(context.school_id))


@academic_registry_router.get("/disciplines", response_model=list[DisciplineResponse])
async def list_disciplines(
    context: AuthenticatedUserContext = Depends(require_academic_registry_access),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        rows = (
            await session.execute(
                select(Discipline)
                .where(Discipline.school_id == _school_id(context))
                .order_by(Discipline.ordinal, Discipline.name)
            )
        ).scalars().all()
    return [DisciplineResponse.model_validate(row, from_attributes=True) for row in rows]


@academic_registry_router.post(
    "/disciplines", response_model=DisciplineResponse, status_code=201
)
async def create_discipline(
    payload: DisciplineCreateRequest,
    context: AuthenticatedUserContext = Depends(require_academic_registry_access),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        discipline = Discipline(
            school_id=_school_id(context),
            name=payload.name,
            short_name=payload.short_name,
            knowledge_area=payload.knowledge_area,
            ordinal=payload.ordinal,
            external_id=payload.external_id,
        )
        session.add(discipline)
        await session.commit()
        await session.refresh(discipline)
    return DisciplineResponse.model_validate(discipline, from_attributes=True)


@academic_registry_router.post(
    "/classes/{class_id}/teaching-groups",
    response_model=list[TeachingGroupResponse],
    status_code=201,
)
async def ensure_teaching_groups(
    class_id: uuid.UUID,
    payload: EnsureGroupsRequest,
    context: AuthenticatedUserContext = Depends(require_academic_registry_access),
    session_factory=Depends(get_session_factory),
):
    """Deriva da matriz curricular os grupos CLASS_WIDE da turma. Idempotente."""
    async with session_factory() as session:
        groups = await ensure_class_wide_groups(
            session,
            school_id=_school_id(context),
            class_id=class_id,
            reference_date=payload.reference_date,
        )
        await session.commit()
        result = [
            TeachingGroupResponse(
                id=group.id,
                name=group.name,
                kind=group.kind,
                discipline_id=group.discipline_id,
                origin_class_id=group.origin_class_id,
            )
            for group in groups
        ]
    return result


@academic_registry_router.get(
    "/timetable/conflicts", response_model=list[TimetableConflictResponse]
)
async def list_timetable_conflicts(
    academic_year_id: uuid.UUID = Query(...),
    reference_date: date = Query(...),
    context: AuthenticatedUserContext = Depends(require_academic_registry_access),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        conflicts = await find_timetable_conflicts(
            session,
            school_id=_school_id(context),
            academic_year_id=academic_year_id,
            reference_date=reference_date,
        )
    return [
        TimetableConflictResponse(
            kind=conflict.kind,
            weekday=conflict.weekday,
            period_ordinal=conflict.period_ordinal,
            subject_id=conflict.subject_id,
            group_ids=list(conflict.group_ids),
        )
        for conflict in conflicts
    ]
```

- [ ] **Step 6: Register the router**

Em `src/agente_ia_edu/api/app.py`, importe `academic_registry_router` junto dos demais routers e registre-o **sem** o guard de recepção, logo antes de `app.include_router(reception_router)`:

```python
    # Sem reception_only_guard de proposito: este dominio tem guard proprio
    # (require_academic_registry_access) e e onde o SECRETARY trabalha.
    app.include_router(academic_registry_router)
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python -m pytest tests/test_academic_registry_access.py -v`
Expected: PASS, 8 testes.

- [ ] **Step 8: Run the full suite**

Run: `python -m pytest`
Expected: PASS. Atenção especial aos testes existentes de recepção e escopo — se algum falhar, o guard vazou para outro router.

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/api/dependencies.py \
        src/agente_ia_edu/api/schemas/academic_registry.py \
        src/agente_ia_edu/api/routes/academic_registry.py \
        src/agente_ia_edu/api/app.py \
        tests/test_academic_registry_access.py
git commit -m "feat(secretaria): guard de dominio e API de cadastro academico"
```

---

### Task 10: Validação em PostgreSQL real e isolamento multi-tenant

**Files:**
- Test: `tests/test_academic_registry_migration_postgresql.py`
- Test: `tests/test_r0_academic_registry_scope.py`

**Interfaces:**
- Consumes: as migrations `057` a `062` (Tasks 1–8); `tests/_postgres_test_db.py` (`create_database`, `drop_database`), já existente.
- Produces: nada consumido por outra task. É a prova de que o bloco inteiro sobrevive a PostgreSQL real e recusa cruzamento de escolas.

**Por que é uma task própria:** o resto do plano valida em SQLite. Diferenças de `CHECK` com `BETWEEN`, `server_default` e ordem de `DROP` só aparecem no Postgres, e a memória do projeto registra que fase aditiva exige a suíte inteira contra banco real.

- [ ] **Step 1: Write the migration test**

Crie `tests/test_academic_registry_migration_postgresql.py`:

```python
"""Validação em PostgreSQL real das migrations 057-062.

Segue tests/test_reception_migration_postgresql.py, que é o precedente do
projeto: banco dedicado, upgrade -> downgrade -> re-upgrade, e inspeção do que
ficou de pé. Pula com mensagem clara quando não há PostgreSQL disponível, em vez
de falhar e esconder o motivo.
"""

from __future__ import annotations

import os
import unittest

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text

from tests._postgres_test_db import create_database, drop_database

_TABLES = (
    "disciplines",
    "rooms",
    "teachers",
    "teacher_availabilities",
    "academic_terms",
    "school_calendar_days",
    "curriculum_matrices",
    "teaching_groups",
    "teaching_group_members",
    "teaching_assignments",
    "timetable_slots",
    "person_civil_records",
)

_BASE_REVISION = "056_material_assignments"
_HEAD_REVISION = "062_person_civil_record"


class AcademicRegistryMigrationPostgreSQLCase(unittest.TestCase):
    database_name = "agente_ia_edu_academic_registry_test"
    admin_url = os.getenv(
        "ACADEMIC_REGISTRY_TEST_ADMIN_DATABASE_URL",
        "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/postgres",
    )
    database_url = os.getenv(
        "ACADEMIC_REGISTRY_TEST_DATABASE_URL",
        f"postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            engine = create_engine(
                cls.admin_url, execution_options={"isolation_level": "AUTOCOMMIT"}
            )
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            engine.dispose()
        except Exception as exc:
            raise unittest.SkipTest(
                "PostgreSQL de teste indisponível; migrations 057-062 não validadas."
            ) from exc

    def setUp(self):
        drop_database(self.admin_url, self.database_name)
        create_database(self.admin_url, self.database_name)

    def tearDown(self):
        drop_database(self.admin_url, self.database_name)

    def _config(self) -> Config:
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", self.database_url)
        return config

    def test_upgrade_downgrade_and_reupgrade(self):
        config = self._config()

        command.upgrade(config, _HEAD_REVISION)
        engine = create_engine(self.database_url)
        try:
            tables = set(inspect(engine).get_table_names())
            for table in _TABLES:
                self.assertIn(table, tables, f"{table} não foi criada")
        finally:
            engine.dispose()

        command.downgrade(config, _BASE_REVISION)
        engine = create_engine(self.database_url)
        try:
            tables = set(inspect(engine).get_table_names())
            for table in _TABLES:
                self.assertNotIn(table, tables, f"{table} sobreviveu ao downgrade")
        finally:
            engine.dispose()

        command.upgrade(config, _HEAD_REVISION)
        engine = create_engine(self.database_url)
        try:
            tables = set(inspect(engine).get_table_names())
            for table in _TABLES:
                self.assertIn(table, tables, f"{table} não voltou no re-upgrade")
        finally:
            engine.dispose()

    def test_composite_foreign_keys_are_present(self):
        command.upgrade(self._config(), _HEAD_REVISION)
        engine = create_engine(self.database_url)
        try:
            inspector = inspect(engine)
            teacher_fks = {fk["name"] for fk in inspector.get_foreign_keys("teachers")}
            self.assertIn("fk_teachers_person_same_school", teacher_fks)

            group_fks = {fk["name"] for fk in inspector.get_foreign_keys("teaching_groups")}
            self.assertIn("fk_teaching_groups_origin_class_same_school", group_fks)
        finally:
            engine.dispose()

    def test_group_kind_check_is_enforced_by_postgres(self):
        command.upgrade(self._config(), _HEAD_REVISION)
        engine = create_engine(self.database_url)
        try:
            constraints = {
                check["name"] for check in inspect(engine).get_check_constraints("teaching_groups")
            }
            self.assertIn("ck_teaching_groups_origin_matches_kind", constraints)
        finally:
            engine.dispose()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the migration test**

Run: `python -m pytest tests/test_academic_registry_migration_postgresql.py -v`
Expected: PASS, 3 testes. Se pular, suba o container (`docker compose up -d`) e rode de novo — pular aqui significa que a validação que mais importa não aconteceu.

- [ ] **Step 3: Write the isolation test**

Crie `tests/test_r0_academic_registry_scope.py`:

```python
"""O banco recusa cadastro da secretaria que cruze a fronteira de escola.

Cada caso abaixo é uma travessia de tenant e nada mais: a mesma linha escrita
inteiramente dentro de uma escola é aceita primeiro, então o que dispara só pode
ser a chave composta.
"""

import unittest
from datetime import date

from sqlalchemy.exc import IntegrityError

from agente_ia_edu.db.models import (
    CurriculumMatrix,
    Discipline,
    Room,
    TeachingGroup,
    TeachingGroupMember,
)
from tests._academic_fixtures import AcademicModelCase, build_school


class AcademicRegistryScopeCase(AcademicModelCase):
    async def test_group_cannot_point_to_a_class_of_another_school(self):
        async with self.session_factory() as session:
            fx_a = await build_school(session, "XA")
            fx_b = await build_school(session, "XB")
            discipline = Discipline(school_id=fx_a.school.id, name="Matemática", ordinal=1)
            session.add(discipline)
            await session.flush()

            # Aceito dentro da própria escola.
            session.add(
                TeachingGroup(
                    school_id=fx_a.school.id,
                    academic_year_id=fx_a.year.id,
                    discipline_id=discipline.id,
                    origin_class_id=fx_a.klass.id,
                    name="Matemática — A",
                    kind="CLASS_WIDE",
                )
            )
            await session.flush()

            # Recusado ao cruzar para a turma da outra escola.
            session.add(
                TeachingGroup(
                    school_id=fx_a.school.id,
                    academic_year_id=fx_a.year.id,
                    discipline_id=discipline.id,
                    origin_class_id=fx_b.klass.id,
                    name="Invasor",
                    kind="CLASS_WIDE",
                )
            )
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_member_cannot_be_an_enrollment_of_another_school(self):
        async with self.session_factory() as session:
            fx_a = await build_school(session, "YA")
            fx_b = await build_school(session, "YB")
            discipline = Discipline(school_id=fx_a.school.id, name="Inglês", ordinal=1)
            session.add(discipline)
            await session.flush()
            group = TeachingGroup(
                school_id=fx_a.school.id,
                academic_year_id=fx_a.year.id,
                discipline_id=discipline.id,
                name="Inglês",
                kind="CROSS_CLASS",
            )
            session.add(group)
            await session.flush()

            session.add(
                TeachingGroupMember(
                    school_id=fx_a.school.id,
                    teaching_group_id=group.id,
                    student_enrollment_id=fx_b.enrollment.id,
                    joined_on=date(2026, 2, 2),
                )
            )
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_matrix_cannot_use_a_year_of_another_school(self):
        async with self.session_factory() as session:
            fx_a = await build_school(session, "ZA")
            fx_b = await build_school(session, "ZB")
            discipline = Discipline(school_id=fx_a.school.id, name="Física", ordinal=1)
            session.add(discipline)
            await session.flush()

            session.add(
                CurriculumMatrix(
                    school_id=fx_a.school.id,
                    grade_level_id=fx_a.grade.id,
                    academic_year_id=fx_b.year.id,
                    discipline_id=discipline.id,
                    weekly_lessons=2,
                )
            )
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_room_cannot_belong_to_a_unit_of_another_school(self):
        async with self.session_factory() as session:
            fx_a = await build_school(session, "WA")
            fx_b = await build_school(session, "WB")

            session.add(
                Room(
                    school_id=fx_a.school.id,
                    school_unit_id=fx_b.unit.id,
                    name="Sala invasora",
                )
            )
            with self.assertRaises(IntegrityError):
                await session.flush()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 4: Run the isolation test**

Run: `python -m pytest tests/test_r0_academic_registry_scope.py -v`
Expected: PASS, 4 testes.

- [ ] **Step 5: Run the full suite**

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 6: Commit**

```bash
git add tests/test_academic_registry_migration_postgresql.py \
        tests/test_r0_academic_registry_scope.py
git commit -m "test(secretaria): validacao em postgres real e isolamento de tenant"
```

---

### Task 11: Portal da secretaria e dados de demonstração

**Files:**
- Create: `src/agente_ia_edu/web/secretaria.html`
- Create: `src/agente_ia_edu/web/secretaria.js`
- Create: `src/agente_ia_edu/web/secretaria.css`
- Modify: `src/agente_ia_edu/api/app.py`
- Modify: `scripts/seed_demo_data.py`
- Test: `tests/test_secretaria_portal_assets.py`

**Interfaces:**
- Consumes: as rotas da Task 9 (`/api/v1/academic-registry/...`).
- Produces: o portal servido em `/secretaria`, com assets em `/secretaria/assets`, seguindo o mount de `coordination` e `admin` em `api/app.py`.

**Nota de escopo:** o portal desta task é a superfície mínima que torna F utilizável — listar e criar disciplina, e ver conflitos da grade. A tela completa de montagem de grade não está neste plano; ela ganha corpo junto com o diário, quando houver o que exibir.

- [ ] **Step 1: Write the failing test**

Crie `tests/test_secretaria_portal_assets.py`:

```python
"""O portal da secretaria está registrado e servido.

Teste de fiação, não de aparência: garante que o mount existe e que o HTML
carrega os assets pelo caminho montado, que é o erro que mais se repete quando
um portal novo é acrescentado ao app.
"""

import unittest
from pathlib import Path

from agente_ia_edu.api.app import create_app

_WEB = Path(__file__).resolve().parents[1] / "src" / "agente_ia_edu" / "web"


class SecretariaPortalAssetsCase(unittest.TestCase):
    def test_portal_files_exist(self):
        for name in ("secretaria.html", "secretaria.js", "secretaria.css"):
            with self.subTest(name=name):
                self.assertTrue((_WEB / name).is_file(), f"{name} não existe")

    def test_assets_mount_is_registered(self):
        app = create_app()
        mounts = {getattr(route, "name", None) for route in app.routes}
        self.assertIn("secretaria-assets", mounts)

    def test_html_references_assets_through_the_mount(self):
        html = (_WEB / "secretaria.html").read_text(encoding="utf-8")
        self.assertIn("/secretaria/assets/secretaria.css", html)
        self.assertIn("/secretaria/assets/secretaria.js", html)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_secretaria_portal_assets.py -v`
Expected: FAIL — os três testes falham, o primeiro por arquivo inexistente.

- [ ] **Step 3: Write the portal HTML**

Crie `src/agente_ia_edu/web/secretaria.html`:

```html
<!DOCTYPE html>
<html lang="pt-BR">
<head>
  <meta charset="utf-8">
  <meta name="viewport" content="width=device-width, initial-scale=1">
  <title>Secretaria Acadêmica</title>
  <link rel="stylesheet" href="/secretaria/assets/styles.css">
  <link rel="stylesheet" href="/secretaria/assets/secretaria.css">
</head>
<body>
  <main class="secretaria-shell">
    <header class="secretaria-header">
      <h1>Secretaria Acadêmica</h1>
      <p class="subtitle">Cadastros do ano letivo</p>
    </header>

    <div id="secretaria-alert" class="alert-banner" style="display:none;"></div>

    <section class="panel" id="panel-disciplines">
      <h2>Componentes curriculares</h2>
      <form id="discipline-form" class="inline-form">
        <input type="text" id="discipline-name" placeholder="Nome da disciplina" required>
        <input type="text" id="discipline-short" placeholder="Sigla" maxlength="20">
        <input type="number" id="discipline-ordinal" placeholder="Ordem" value="0" min="0">
        <button type="submit">Adicionar</button>
      </form>
      <ul id="discipline-list" class="entity-list"></ul>
    </section>

    <section class="panel" id="panel-conflicts">
      <h2>Conflitos da grade</h2>
      <form id="conflict-form" class="inline-form">
        <input type="text" id="conflict-year-id" placeholder="ID do ano letivo" required>
        <input type="date" id="conflict-date" required>
        <button type="submit">Verificar</button>
      </form>
      <ul id="conflict-list" class="entity-list"></ul>
    </section>
  </main>

  <script src="/secretaria/assets/secretaria.js"></script>
</body>
</html>
```

- [ ] **Step 4: Write the portal script**

Crie `src/agente_ia_edu/web/secretaria.js`:

```javascript
// Portal da secretaria academica. Segue o padrao dos demais portais do
// projeto: vanilla JS, sem build, um arquivo por papel.

const API = '/api/v1/academic-registry';

const CONFLICT_LABELS = {
  TEACHER: 'Professor em duas turmas',
  ROOM: 'Sala ocupada duas vezes',
  STUDENT: 'Aluno em duas aulas',
};

const WEEKDAYS = ['Domingo', 'Segunda', 'Terca', 'Quarta', 'Quinta', 'Sexta', 'Sabado'];

function showAlert(message, isError) {
  const box = document.getElementById('secretaria-alert');
  box.textContent = message;
  box.className = isError ? 'alert-banner alert-error' : 'alert-banner alert-success';
  box.style.display = 'block';
}

async function request(path, options) {
  const response = await fetch(`${API}${path}`, options);
  if (!response.ok) {
    const body = await response.text();
    throw new Error(body || `HTTP ${response.status}`);
  }
  return response.status === 204 ? null : response.json();
}

async function loadDisciplines() {
  const list = document.getElementById('discipline-list');
  try {
    const disciplines = await request('/disciplines');
    list.innerHTML = '';
    if (disciplines.length === 0) {
      list.innerHTML = '<li class="empty">Nenhum componente curricular cadastrado.</li>';
      return;
    }
    disciplines.forEach((discipline) => {
      const item = document.createElement('li');
      const short = discipline.short_name ? ` (${discipline.short_name})` : '';
      item.textContent = `${discipline.ordinal}. ${discipline.name}${short}`;
      list.appendChild(item);
    });
  } catch (error) {
    showAlert(`Nao foi possivel carregar os componentes: ${error.message}`, true);
  }
}

async function createDiscipline(event) {
  event.preventDefault();
  const name = document.getElementById('discipline-name').value.trim();
  const shortName = document.getElementById('discipline-short').value.trim();
  const ordinal = Number(document.getElementById('discipline-ordinal').value || 0);
  if (!name) return;

  try {
    await request('/disciplines', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        name,
        short_name: shortName || null,
        ordinal,
      }),
    });
    document.getElementById('discipline-form').reset();
    document.getElementById('discipline-ordinal').value = '0';
    showAlert(`Componente "${name}" cadastrado.`, false);
    await loadDisciplines();
  } catch (error) {
    showAlert(`Nao foi possivel cadastrar: ${error.message}`, true);
  }
}

async function checkConflicts(event) {
  event.preventDefault();
  const yearId = document.getElementById('conflict-year-id').value.trim();
  const referenceDate = document.getElementById('conflict-date').value;
  const list = document.getElementById('conflict-list');
  if (!yearId || !referenceDate) return;

  try {
    const query = `academic_year_id=${encodeURIComponent(yearId)}`
      + `&reference_date=${encodeURIComponent(referenceDate)}`;
    const conflicts = await request(`/timetable/conflicts?${query}`);
    list.innerHTML = '';
    if (conflicts.length === 0) {
      list.innerHTML = '<li class="empty">Nenhum conflito na data informada.</li>';
      return;
    }
    conflicts.forEach((conflict) => {
      const item = document.createElement('li');
      const label = CONFLICT_LABELS[conflict.kind] || conflict.kind;
      const weekday = WEEKDAYS[conflict.weekday] || `Dia ${conflict.weekday}`;
      item.textContent = `${label} - ${weekday}, ${conflict.period_ordinal}a aula `
        + `(${conflict.group_ids.length} grupos)`;
      item.className = 'conflict-item';
      list.appendChild(item);
    });
  } catch (error) {
    showAlert(`Nao foi possivel verificar conflitos: ${error.message}`, true);
  }
}

document.addEventListener('DOMContentLoaded', () => {
  document.getElementById('discipline-form').addEventListener('submit', createDiscipline);
  document.getElementById('conflict-form').addEventListener('submit', checkConflicts);
  loadDisciplines();
});
```

- [ ] **Step 5: Write the portal stylesheet**

Crie `src/agente_ia_edu/web/secretaria.css`:

```css
/* Portal da secretaria academica. Apoia-se em styles.css, que ja define as
   variaveis e os componentes comuns dos demais portais. */

.secretaria-shell {
  max-width: 960px;
  margin: 0 auto;
  padding: 24px 16px 48px;
}

.secretaria-header h1 {
  margin: 0 0 4px;
}

.secretaria-header .subtitle {
  margin: 0 0 24px;
  opacity: 0.75;
}

.panel {
  border: 1px solid rgba(0, 0, 0, 0.12);
  border-radius: 10px;
  padding: 16px;
  margin-bottom: 24px;
}

.panel h2 {
  margin: 0 0 12px;
  font-size: 1.05rem;
}

.inline-form {
  display: flex;
  flex-wrap: wrap;
  gap: 8px;
  margin-bottom: 12px;
}

.inline-form input {
  flex: 1 1 160px;
  padding: 8px 10px;
  border: 1px solid rgba(0, 0, 0, 0.2);
  border-radius: 6px;
}

.inline-form button {
  padding: 8px 16px;
  border: 0;
  border-radius: 6px;
  cursor: pointer;
}

.entity-list {
  list-style: none;
  margin: 0;
  padding: 0;
}

.entity-list li {
  padding: 8px 0;
  border-bottom: 1px solid rgba(0, 0, 0, 0.08);
}

.entity-list li:last-child {
  border-bottom: 0;
}

.entity-list .empty {
  opacity: 0.6;
  font-style: italic;
}

.conflict-item {
  color: #b42318;
  font-weight: 600;
}

@media (max-width: 600px) {
  .inline-form input,
  .inline-form button {
    flex: 1 1 100%;
  }
}
```

- [ ] **Step 6: Register the mount**

Em `src/agente_ia_edu/api/app.py`, junto dos demais mounts de assets:

```python
        app.mount("/secretaria/assets", StaticFiles(directory=str(web_dir), html=False),
                  name="secretaria-assets")
```

Acrescente também a rota que serve o HTML, ao lado de `serve_coordination_portal`, no mesmo bloco e com a mesma forma:

```python
        @app.get("/secretaria", include_in_schema=False)
        @app.get("/secretaria/", include_in_schema=False)
        async def serve_secretaria_portal():
            secretaria_html = web_dir / "secretaria.html"
            if secretaria_html.exists():
                return FileResponse(secretaria_html)
            return FileResponse(web_dir / "index.html")
```

- [ ] **Step 7: Run test to verify it passes**

Run: `python -m pytest tests/test_secretaria_portal_assets.py -v`
Expected: PASS, 3 testes.

- [ ] **Step 8: Extend the demo seed**

Em `scripts/seed_demo_data.py`, acrescente ao final do fluxo de criação da escola de demonstração: cinco `Discipline` (Matemática, Português, História, Física, Biologia, com `ordinal` 1 a 5), quatro `AcademicTerm` (bimestres, `status="OPEN"` no primeiro e `"PLANNED"` nos demais), os dias letivos de um mês em `SchoolCalendarDay`, e uma entrada de `CurriculumMatrix` por disciplina para a série da turma de demonstração. Em seguida chame `ensure_class_wide_groups` para a turma, de modo que o portal abra com grupos reais.

Leia o arquivo antes de editar e siga a forma como ele já cria escola, turma e matrícula — não introduza um estilo novo de construção de dados.

- [ ] **Step 9: Verify the seed runs**

Run: `python scripts/seed_demo_data.py`
Expected: termina sem erro. Rode duas vezes seguidas: a segunda também deve terminar sem erro, porque `ensure_class_wide_groups` é idempotente.

- [ ] **Step 10: Run the full suite**

Run: `python -m pytest`
Expected: PASS.

- [ ] **Step 11: Commit**

```bash
git add src/agente_ia_edu/web/secretaria.html \
        src/agente_ia_edu/web/secretaria.js \
        src/agente_ia_edu/web/secretaria.css \
        src/agente_ia_edu/api/app.py \
        scripts/seed_demo_data.py \
        tests/test_secretaria_portal_assets.py
git commit -m "feat(secretaria): portal da secretaria e dados de demonstracao"
```

---

## Ao terminar

A Fundação está completa quando a secretaria consegue, num ambiente limpo:
cadastrar disciplinas, definir os períodos letivos e o calendário, lançar a
matriz curricular, abrir uma turma e ver seus grupos criados sozinhos, alocar
professores, montar a grade e receber a lista de conflitos nas três dimensões.

**Próximo plano:** SP1 — Diário de classe e frequência (etapas 4 a 6 do
faseamento da §9 do spec): materialização das aulas previstas, registro de aula,
chamada, projeção para `TeachingLesson`, fechamento de período com auditoria e
apuração de carga horária.

**Não implemente SP1 a partir deste documento.** Ele tem plano próprio, e a
projeção para `TeachingLesson` — a decisão mais delicada do módulo — precisa das
tarefas de regressão que só fazem sentido escritas junto com ela.
