# R0 Fase 1 — Entidades acadêmicas e configuração da instituição — Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Criar a estrutura acadêmica e a configuração da instituição como entidades reais, de forma puramente aditiva — sem alterar o comportamento de nenhum consumidor existente.

**Architecture:** Dez tabelas acadêmicas novas mais duas de configuração, cada uma com uma coluna `external_id` que preserva o identificador que o código atual já usa. `user_school_links` ganha colunas FK anuláveis ao lado do `scope_external_id` que já tem. Nenhuma tabela existente é alterada de forma destrutiva, nenhum serviço existente muda de comportamento, e nenhum consumidor é migrado — isso é a Fase 3.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x async, Alembic, `unittest.IsolatedAsyncioTestCase` sobre SQLite em memória.

**Spec:** `docs/superpowers/specs/2026-09-13-r0-estrutura-academica-configuracao-design.md`

**Fase 1 de 3.** A Fase 2 liga o portão de disciplina; a Fase 3 migra os consumidores e remove o andaime. Esta fase não toca em nenhuma das duas.

## Global Constraints

- Python `>=3.13,<3.14`. SQLAlchemy `>=2.0,<3.0`. Sem dependências novas.
- **Nenhuma migration altera tabela existente de forma destrutiva.** As únicas alterações permitidas em tabela existente são colunas anuláveis acrescentadas a `user_school_links` (Task 4).
- **Nenhuma coluna de credencial em lugar nenhum.** Sem `password`, `token`, `secret`, `credential`. A Task 1 cria o teste que faz o CI recusar a introdução de uma.
- Modelos usam `Mapped`/`mapped_column`, `Uuid`, `JSONBCompatible`, e `metadata_` mapeado para a coluna `metadata` — o padrão de `src/agente_ia_edu/db/models/admin.py`.
- Testes usam `unittest.IsolatedAsyncioTestCase` com `sqlite+aiosqlite:///:memory:` e `StaticPool`, o padrão de `tests/test_platform_administration.py`. **Não existe `conftest.py` no projeto; não crie um.**
- Código e comentários em inglês; docstrings podem citar a spec em português. Mensagens de commit em português, seguindo `git log`.
- Toda tabela acadêmica tem `external_id` opcional, com UNIQUE por escola. A exceção é `users`, cuja ponte é o `external_user_id` que ela já nomeia.
- Migration head atual: **`039_essay_rubric_foundation`**. Esta fase usa `040` a `044`.
- Arquivos de teste desta fase: `tests/test_r0_*.py`.

## Ambiente

O worktree é `/Users/marcoviana/agente-ia-edu-core/.claude/worktrees/r0-estrutura-academica`, branch `feature/r0-estrutura-academica`. `.venv` e `var` são symlinks para o checkout principal, e `pyproject.toml` já traz `pythonpath = ["src", "."]`, então `pytest` importa o `src` deste worktree.

**A suíte completa do projeto tem baseline sujo** — 71 falhas e 13 erros, todos de `DATABASE_URL` não exportada, anteriores a este trabalho. Não investigue, não rode a suíte inteira. O gate desta fase é `tests/test_r0_*.py` mais `tests/test_platform_administration.py` e `tests/test_r1_*.py` seguindo verdes.

**Verificação de migration usa banco descartável, nunca o de desenvolvimento.** Crie-o uma vez:

```bash
docker exec agente-ia-edu-postgres psql -U "$POSTGRES_USER" -d postgres -c "CREATE DATABASE agente_ia_edu_r0_migcheck"
```

e carimbe-o em `039_essay_rubric_foundation` antes do primeiro `upgrade`, porque a cadeia não roda do zero — `024_chemistry_kinetics` é migration de dados e aborta em banco vazio. A porta do Postgres deste projeto é **5433**; a 5432 pertence a outro projeto.

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/agente_ia_edu/db/models/academic.py` | As dez entidades acadêmicas |
| `src/agente_ia_edu/db/models/institution.py` | `SchoolSetting` e `SchoolIdentityVersion` |
| `src/agente_ia_edu/db/models/__init__.py` | Exports (modificado) |
| `src/agente_ia_edu/db/models/admin.py` | FKs anuláveis em `UserSchoolLink` (modificado) |
| `src/agente_ia_edu/identity.py` | Docstring da revogação (modificado) |
| `src/agente_ia_edu/services/institution_settings.py` | Serviço de configuração, com auditoria obrigatória |
| `migrations/versions/040_..._044_...` | Cinco migrations aditivas |

Sete tarefas. Cada uma termina com commit e é independentemente testável.

---

### Task 1: Identidade — `persons` e `users`

**Files:**
- Create: `src/agente_ia_edu/db/models/academic.py`
- Create: `migrations/versions/040_academic_identity.py`
- Create: `tests/test_r0_identity_models.py`
- Modify: `src/agente_ia_edu/db/models/__init__.py`
- Modify: `src/agente_ia_edu/identity.py` (docstring apenas)

**Interfaces:**
- Consumes: nada.
- Produces: `Person`, `User`, exportados de `agente_ia_edu.db.models`. Constantes de módulo `PERSON_STATUSES`, `USER_STATUSES`.

- [ ] **Step 1: Escreva os testes que falham**

```python
# tests/test_r0_identity_models.py
import unittest
import uuid

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import Person, School, User

FORBIDDEN_COLUMN_FRAGMENTS = ("password", "token", "secret", "credential", "senha")


class TestIdentityModels(unittest.IsolatedAsyncioTestCase):
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

    async def _school(self, session) -> School:
        school = School(code="ESCOLA_R0", name="Escola R0")
        session.add(school)
        await session.flush()
        return school

    def test_users_has_no_credential_column(self):
        """The core never stores credentials (spec §3.1). This test makes the CI
        refuse a future column named like one, instead of relying on memory."""
        for column in inspect(User).columns:
            with self.subTest(column=column.name):
                lowered = column.name.lower()
                for fragment in FORBIDDEN_COLUMN_FRAGMENTS:
                    self.assertNotIn(fragment, lowered)

    def test_persons_has_no_credential_column(self):
        for column in inspect(Person).columns:
            with self.subTest(column=column.name):
                lowered = column.name.lower()
                for fragment in FORBIDDEN_COLUMN_FRAGMENTS:
                    self.assertNotIn(fragment, lowered)

    async def test_person_external_id_is_unique_per_school(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            session.add(Person(school_id=school.id, full_name="Ana", external_id="EXT_1"))
            await session.flush()
            session.add(Person(school_id=school.id, full_name="Bruno", external_id="EXT_1"))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_the_same_external_id_may_repeat_in_another_school(self):
        """Tenant isolation: external ids are the host's, and two hosts may use
        the same string. Uniqueness is per school, never global."""
        async with self.session_factory() as session:
            first = await self._school(session)
            second = School(code="ESCOLA_R0_B", name="Escola R0 B")
            session.add(second)
            await session.flush()

            session.add(Person(school_id=first.id, full_name="Ana", external_id="EXT_1"))
            session.add(Person(school_id=second.id, full_name="Ana", external_id="EXT_1"))
            await session.flush()

    async def test_user_requires_a_person(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            person = Person(school_id=school.id, full_name="Ana")
            session.add(person)
            await session.flush()

            user = User(
                person_id=person.id,
                external_identity_provider="host",
                external_user_id="host:ana",
            )
            session.add(user)
            await session.flush()
            self.assertEqual(user.status, "ACTIVE")

    async def test_user_status_is_constrained(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            person = Person(school_id=school.id, full_name="Ana")
            session.add(person)
            await session.flush()

            session.add(User(
                person_id=person.id,
                external_identity_provider="host",
                external_user_id="host:ana2",
                status="NAO_EXISTE",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()
```

- [ ] **Step 2: Rode e confirme que falha**

Run: `.venv/bin/python -m pytest tests/test_r0_identity_models.py -v`
Expected: FAIL com `ImportError: cannot import name 'Person'`

- [ ] **Step 3: Crie o módulo de modelos**

```python
# src/agente_ia_edu/db/models/academic.py
"""R0 - Academic structure owned by the core.

Until R0 the core referenced students, classes and units by opaque strings
supplied by a hosting platform. These tables make them real, so that class
dashboards, teacher scope and enrollment history rest on data this system
controls rather than on identifiers it cannot validate.

Every table here carries an optional ``external_id``, unique per school. That
column is the bridge: what today is ``scope_external_id = "TURMA_3A"`` resolves
to a real row while existing consumers keep reading the string, and they migrate
one at a time (spec §3.2, §7).

Credentials live nowhere in this module. The hosting platform stays the source
of truth for authentication; the core only needs a stable local identity for the
person it was told about (spec §3.1).
"""

from __future__ import annotations

import uuid
from datetime import date, datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    Date,
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

PERSON_STATUSES = ("ACTIVE", "INACTIVE")
USER_STATUSES = ("ACTIVE", "SUSPENDED", "INACTIVE")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class Person(Base):
    """A human being, scoped to one school.

    Scoped on purpose: the same physical person enrolled at two schools has two
    rows. Tenant isolation is worth more than de-duplicating people, and a shared
    person table would leak who studies where (spec §4.1).
    """

    __tablename__ = "persons"
    __table_args__ = (
        UniqueConstraint("school_id", "external_id", name="uq_persons_school_external_id"),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_persons_status"),
        Index("ix_persons_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    full_name: Mapped[str] = mapped_column(String(255), nullable=False)
    document_number: Mapped[str | None] = mapped_column(String(50))
    email: Mapped[str | None] = mapped_column(String(255))
    phone: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    users: Mapped[list["User"]] = relationship(back_populates="person")


class User(Base):
    """An account, pointing at a Person and at the host's identity.

    NO CREDENTIAL COLUMN EVER. ``tests/test_r0_identity_models.py`` fails the
    build if one appears, so this rule is enforced rather than remembered.
    """

    __tablename__ = "users"
    __table_args__ = (
        UniqueConstraint(
            "external_identity_provider",
            "external_user_id",
            name="uq_users_provider_external_user_id",
        ),
        CheckConstraint(
            "status IN ('ACTIVE', 'SUSPENDED', 'INACTIVE')", name="ck_users_status"
        ),
        Index("ix_users_person_id", "person_id"),
        Index("ix_users_external_user_id", "external_user_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    person_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("persons.id", ondelete="RESTRICT"), nullable=False
    )
    external_identity_provider: Mapped[str] = mapped_column(String(100), nullable=False)
    external_user_id: Mapped[str] = mapped_column(String(255), nullable=False)
    display_name: Mapped[str | None] = mapped_column(String(255))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    person: Mapped["Person"] = relationship(back_populates="users")
```

- [ ] **Step 4: Exporte os modelos**

Em `src/agente_ia_edu/db/models/__init__.py`, acrescente o import junto dos demais e os dois nomes ao final de `__all__`:

```python
from .academic import Person, User
```

```python
    "Person",
    "User",
```

- [ ] **Step 5: Rode e confirme que passa**

Run: `.venv/bin/python -m pytest tests/test_r0_identity_models.py -v`
Expected: PASS, 6 testes

- [ ] **Step 6: Atualize a docstring de `identity.py`**

O módulo declara um princípio que esta fase revoga pela metade. Substitua o parágrafo inicial mantendo o resto do arquivo intacto:

```python
"""External identity contracts for AGENTE IA EDU.

Authentication and credentials remain the hosting platform's responsibility.
This package never stores a password, a token or any other secret, and
``db/models/academic.py`` deliberately has no column that could hold one.

REVOGAÇÃO PARCIAL, 2026-09-13 (R0). This module used to state that the host was
also the source of truth for the academic hierarchy - institution, unit, grade
level and classroom arrived as external identifiers and were never modelled
here. That half no longer holds: the core now owns unit, segment, grade level,
class and enrollment (``db/models/academic.py``).

The reason is that the REDAÇÃO platform decides authorisation from class
membership, and deciding authorisation from strings supplied by another system
fails in a way no test catches. External identifiers survive as a bridge column
on each entity, not as the entity itself.

A revoked principle is not the same as a forgotten one, which is why this note
exists instead of a silent deletion. See
``docs/superpowers/specs/2026-09-13-r0-estrutura-academica-configuracao-design.md`` §3.1.
"""
```

- [ ] **Step 7: Escreva a migration**

```python
# migrations/versions/040_academic_identity.py
"""R0 Fase 1 - Person and User.

Revision ID: 040_academic_identity
Revises: 039_essay_rubric_foundation

Purely additive: two new tables, no existing table touched.

Audit of reuse: ``user_school_links`` already binds an ``external_user_id`` to a
role and a scope, but it stores no person, no name and no contact, and it is a
binding rather than an identity. Nothing in this schema represents a human being
today. These tables add that and change nothing about how links behave.

NO CREDENTIAL COLUMN. See spec §3.1 and the guard test in
``tests/test_r0_identity_models.py``.
"""

from alembic import op
import sqlalchemy as sa

revision = "040_academic_identity"
down_revision = "039_essay_rubric_foundation"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "persons",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("full_name", sa.String(255), nullable=False),
        sa.Column("document_number", sa.String(50)),
        sa.Column("email", sa.String(255)),
        sa.Column("phone", sa.String(50)),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("school_id", "external_id", name="uq_persons_school_external_id"),
        sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_persons_status"),
    )
    op.create_index("ix_persons_school_id", "persons", ["school_id"])

    op.create_table(
        "users",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("external_identity_provider", sa.String(100), nullable=False),
        sa.Column("external_user_id", sa.String(255), nullable=False),
        sa.Column("display_name", sa.String(255)),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["person_id"], ["persons.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "external_identity_provider",
            "external_user_id",
            name="uq_users_provider_external_user_id",
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'SUSPENDED', 'INACTIVE')", name="ck_users_status"
        ),
    )
    op.create_index("ix_users_person_id", "users", ["person_id"])
    op.create_index("ix_users_external_user_id", "users", ["external_user_id"])


def downgrade() -> None:
    op.drop_table("users")
    op.drop_table("persons")
```

- [ ] **Step 8: Verifique a migration no banco descartável**

Nunca no banco de desenvolvimento. A porta é **5433**.

```bash
set -a; . /Users/marcoviana/agente-ia-edu-core/.env; set +a
ENC=$(.venv/bin/python -c "import urllib.parse,os;print(urllib.parse.quote(os.environ['POSTGRES_PASSWORD'],safe=''))")
export DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${ENC}@localhost:5433/agente_ia_edu_r0_migcheck"
.venv/bin/python -m alembic stamp 039_essay_rubric_foundation
.venv/bin/python -m alembic upgrade head
.venv/bin/python -m alembic downgrade -1
.venv/bin/python -m alembic upgrade head
```

Nunca ecoe a senha nem a URL montada. Reporte apenas se cada comando teve sucesso.

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/db/models/academic.py src/agente_ia_edu/db/models/__init__.py src/agente_ia_edu/identity.py migrations/versions/040_academic_identity.py tests/test_r0_identity_models.py
git commit -m "feat: pessoa e usuario, sem nenhuma credencial

O core passa a ter identidade local estavel para quem o hospedeiro
autentica. Credenciais continuam fora, e o teste varre as colunas para que
o CI recuse a introducao de uma em vez de alguem lembrar da regra.

A docstring do identity.py registra a revogacao parcial do principio: o
hospedeiro deixa de ser dono da hierarquia academica e continua dono da
autenticacao. Principio revogado e diferente de principio esquecido."
```

---

### Task 2: Hierarquia — ano letivo, unidade, segmento, série e turma

**Files:**
- Modify: `src/agente_ia_edu/db/models/academic.py`
- Create: `migrations/versions/041_academic_hierarchy.py`
- Create: `tests/test_r0_hierarchy_models.py`
- Modify: `src/agente_ia_edu/db/models/__init__.py`

**Interfaces:**
- Consumes: `Person`, `User` da Task 1.
- Produces: `AcademicYear`, `SchoolUnit`, `Segment`, `GradeLevel`, `Class`, exportados de `agente_ia_edu.db.models`. Constante `ACADEMIC_YEAR_STATUSES`.

- [ ] **Step 1: Escreva os testes que falham**

```python
# tests/test_r0_hierarchy_models.py
import unittest

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    GradeLevel,
    School,
    SchoolUnit,
    Segment,
)


class TestHierarchyModels(unittest.IsolatedAsyncioTestCase):
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

    async def _base(self, session):
        school = School(code="ESCOLA_R0", name="Escola R0")
        session.add(school)
        await session.flush()
        segment = Segment(school_id=school.id, name="Ensino Médio", ordinal=3)
        session.add(segment)
        await session.flush()
        grade = GradeLevel(segment_id=segment.id, name="1ª série", ordinal=1)
        session.add(grade)
        await session.flush()
        return school, segment, grade

    async def test_the_same_class_name_in_two_years_is_two_entities(self):
        """Spec §4.2, quoting REDAÇÃO §13: "1ª Série A - 2026" and
        "1ª Série A - 2027" are different entities. Without this, a student's
        history is a pile of rows with no context."""
        async with self.session_factory() as session:
            school, _, grade = await self._base(session)
            year_2026 = AcademicYear(school_id=school.id, year=2026, status="ACTIVE")
            year_2027 = AcademicYear(school_id=school.id, year=2027, status="PLANNED")
            session.add_all([year_2026, year_2027])
            await session.flush()

            first = Class(academic_year_id=year_2026.id, grade_level_id=grade.id, name="A")
            second = Class(academic_year_id=year_2027.id, grade_level_id=grade.id, name="A")
            session.add_all([first, second])
            await session.flush()

            self.assertNotEqual(first.id, second.id)

    async def test_a_class_name_repeats_within_one_year_is_rejected(self):
        async with self.session_factory() as session:
            school, _, grade = await self._base(session)
            year = AcademicYear(school_id=school.id, year=2026, status="ACTIVE")
            session.add(year)
            await session.flush()

            session.add(Class(academic_year_id=year.id, grade_level_id=grade.id, name="A"))
            await session.flush()
            session.add(Class(academic_year_id=year.id, grade_level_id=grade.id, name="A"))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_academic_year_is_unique_per_school(self):
        async with self.session_factory() as session:
            school, _, _ = await self._base(session)
            session.add(AcademicYear(school_id=school.id, year=2026, status="ACTIVE"))
            await session.flush()
            session.add(AcademicYear(school_id=school.id, year=2026, status="PLANNED"))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_academic_year_status_is_constrained(self):
        async with self.session_factory() as session:
            school, _, _ = await self._base(session)
            session.add(AcademicYear(school_id=school.id, year=2028, status="NAO_EXISTE"))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_a_class_may_name_a_school_unit(self):
        async with self.session_factory() as session:
            school, _, grade = await self._base(session)
            unit = SchoolUnit(school_id=school.id, name="Unidade Centro")
            year = AcademicYear(school_id=school.id, year=2026, status="ACTIVE")
            session.add_all([unit, year])
            await session.flush()

            klass = Class(
                academic_year_id=year.id,
                grade_level_id=grade.id,
                school_unit_id=unit.id,
                name="A",
            )
            session.add(klass)
            await session.flush()
            self.assertEqual(klass.school_unit_id, unit.id)

    async def test_grade_level_belongs_to_a_segment(self):
        async with self.session_factory() as session:
            _, segment, grade = await self._base(session)
            self.assertEqual(grade.segment_id, segment.id)
```

- [ ] **Step 2: Rode e confirme que falha**

Run: `.venv/bin/python -m pytest tests/test_r0_hierarchy_models.py -v`
Expected: FAIL com `ImportError: cannot import name 'AcademicYear'`

- [ ] **Step 3: Acrescente os modelos a `academic.py`**

Acrescente ao final do arquivo, abaixo de `User`:

```python
ACADEMIC_YEAR_STATUSES = ("PLANNED", "ACTIVE", "CLOSED")


class AcademicYear(Base):
    """A school year. Classes belong to one, which is what keeps a student's
    history legible across years (spec §4.2)."""

    __tablename__ = "academic_years"
    __table_args__ = (
        UniqueConstraint("school_id", "year", name="uq_academic_years_school_year"),
        UniqueConstraint(
            "school_id", "external_id", name="uq_academic_years_school_external_id"
        ),
        CheckConstraint(
            "status IN ('PLANNED', 'ACTIVE', 'CLOSED')", name="ck_academic_years_status"
        ),
        Index("ix_academic_years_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    starts_on: Mapped[date | None] = mapped_column(Date)
    ends_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="PLANNED")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    classes: Mapped[list["Class"]] = relationship(back_populates="academic_year")


class SchoolUnit(Base):
    """A campus or building."""

    __tablename__ = "school_units"
    __table_args__ = (
        UniqueConstraint(
            "school_id", "external_id", name="uq_school_units_school_external_id"
        ),
        Index("ix_school_units_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class Segment(Base):
    """Fundamental I, Fundamental II, Médio."""

    __tablename__ = "segments"
    __table_args__ = (
        UniqueConstraint("school_id", "external_id", name="uq_segments_school_external_id"),
        Index("ix_segments_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    grade_levels: Mapped[list["GradeLevel"]] = relationship(back_populates="segment")


class GradeLevel(Base):
    """1ª, 2ª, 3ª série, inside a segment."""

    __tablename__ = "grade_levels"
    __table_args__ = (
        UniqueConstraint(
            "segment_id", "external_id", name="uq_grade_levels_segment_external_id"
        ),
        Index("ix_grade_levels_segment_id", "segment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    segment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("segments.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(255), nullable=False)
    ordinal: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    segment: Mapped["Segment"] = relationship(back_populates="grade_levels")
    classes: Mapped[list["Class"]] = relationship(back_populates="grade_level")


class Class(Base):
    """A class, belonging to BOTH an academic year and a grade level.

    The year is not decoration: "1ª Série A - 2026" and "1ª Série A - 2027" are
    different entities (REDAÇÃO spec §13), and collapsing them would make every
    enrollment history ambiguous.
    """

    __tablename__ = "classes"
    __table_args__ = (
        UniqueConstraint(
            "academic_year_id", "grade_level_id", "name", name="uq_classes_year_grade_name"
        ),
        UniqueConstraint(
            "academic_year_id", "external_id", name="uq_classes_year_external_id"
        ),
        Index("ix_classes_academic_year_id", "academic_year_id"),
        Index("ix_classes_grade_level_id", "grade_level_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    academic_year_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("academic_years.id", ondelete="RESTRICT"), nullable=False
    )
    grade_level_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("grade_levels.id", ondelete="RESTRICT"), nullable=False
    )
    school_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("school_units.id", ondelete="RESTRICT")
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    name: Mapped[str] = mapped_column(String(100), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    academic_year: Mapped["AcademicYear"] = relationship(back_populates="classes")
    grade_level: Mapped["GradeLevel"] = relationship(back_populates="classes")
```

- [ ] **Step 4: Exporte os modelos**

Em `src/agente_ia_edu/db/models/__init__.py`, estenda o import da Task 1 e acrescente os cinco nomes a `__all__`:

```python
from .academic import (
    AcademicYear,
    Class,
    GradeLevel,
    Person,
    SchoolUnit,
    Segment,
    User,
)
```

```python
    "AcademicYear",
    "Class",
    "GradeLevel",
    "SchoolUnit",
    "Segment",
```

- [ ] **Step 5: Rode e confirme que passa**

Run: `.venv/bin/python -m pytest tests/test_r0_hierarchy_models.py -v`
Expected: PASS, 6 testes

- [ ] **Step 6: Escreva a migration**

```python
# migrations/versions/041_academic_hierarchy.py
"""R0 Fase 1 - academic year, unit, segment, grade level and class.

Revision ID: 041_academic_hierarchy
Revises: 040_academic_identity

Purely additive: five new tables, no existing table touched.

``classes`` deliberately references ``academic_years``. A class is not a name
that persists across years; "1ª Série A - 2026" and "1ª Série A - 2027" are
different rows, because a student's history is only legible if the class carries
its year (REDAÇÃO spec §13).
"""

from alembic import op
import sqlalchemy as sa

revision = "041_academic_hierarchy"
down_revision = "040_academic_identity"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "academic_years",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("starts_on", sa.Date()),
        sa.Column("ends_on", sa.Date()),
        sa.Column("status", sa.String(20), nullable=False, server_default="PLANNED"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("school_id", "year", name="uq_academic_years_school_year"),
        sa.UniqueConstraint(
            "school_id", "external_id", name="uq_academic_years_school_external_id"
        ),
        sa.CheckConstraint(
            "status IN ('PLANNED', 'ACTIVE', 'CLOSED')", name="ck_academic_years_status"
        ),
    )
    op.create_index("ix_academic_years_school_id", "academic_years", ["school_id"])

    op.create_table(
        "school_units",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "school_id", "external_id", name="uq_school_units_school_external_id"
        ),
    )
    op.create_index("ix_school_units_school_id", "school_units", ["school_id"])

    op.create_table(
        "segments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("school_id", "external_id", name="uq_segments_school_external_id"),
    )
    op.create_index("ix_segments_school_id", "segments", ["school_id"])

    op.create_table(
        "grade_levels",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("segment_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("name", sa.String(255), nullable=False),
        sa.Column("ordinal", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["segment_id"], ["segments.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "segment_id", "external_id", name="uq_grade_levels_segment_external_id"
        ),
    )
    op.create_index("ix_grade_levels_segment_id", "grade_levels", ["segment_id"])

    op.create_table(
        "classes",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("academic_year_id", sa.Uuid(), nullable=False),
        sa.Column("grade_level_id", sa.Uuid(), nullable=False),
        sa.Column("school_unit_id", sa.Uuid()),
        sa.Column("external_id", sa.String(255)),
        sa.Column("name", sa.String(100), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["academic_year_id"], ["academic_years.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["grade_level_id"], ["grade_levels.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["school_unit_id"], ["school_units.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "academic_year_id", "grade_level_id", "name", name="uq_classes_year_grade_name"
        ),
        sa.UniqueConstraint(
            "academic_year_id", "external_id", name="uq_classes_year_external_id"
        ),
    )
    op.create_index("ix_classes_academic_year_id", "classes", ["academic_year_id"])
    op.create_index("ix_classes_grade_level_id", "classes", ["grade_level_id"])


def downgrade() -> None:
    op.drop_table("classes")
    op.drop_table("grade_levels")
    op.drop_table("segments")
    op.drop_table("school_units")
    op.drop_table("academic_years")
```

- [ ] **Step 7: Verifique a migration no banco descartável**

Mesmo procedimento e mesmas advertências da Task 1, Step 8. Porta **5433**, banco `agente_ia_edu_r0_migcheck`, nunca `agente_ia_edu`.

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/db/models/academic.py src/agente_ia_edu/db/models/__init__.py migrations/versions/041_academic_hierarchy.py tests/test_r0_hierarchy_models.py
git commit -m "feat: hierarquia academica - ano letivo, unidade, segmento, serie e turma

Turma pertence ao ano letivo, nao so a serie. 1a Serie A de 2026 e 1a Serie
A de 2027 sao entidades diferentes, e sem isso o historico do aluno vira uma
pilha de registros sem contexto."
```

---

### Task 3: Aluno, matrícula e transição

**Files:**
- Modify: `src/agente_ia_edu/db/models/academic.py`
- Create: `migrations/versions/042_student_enrollment.py`
- Create: `tests/test_r0_enrollment_models.py`
- Modify: `src/agente_ia_edu/db/models/__init__.py`

**Interfaces:**
- Consumes: `Person`, `Class`, `User` das Tasks 1 e 2.
- Produces: `Student`, `StudentEnrollment`, `EnrollmentTransition`. Constantes `ENROLLMENT_STATUSES`, `TRANSITION_KINDS`.

- [ ] **Step 1: Escreva os testes que falham**

```python
# tests/test_r0_enrollment_models.py
import unittest
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EnrollmentTransition,
    GradeLevel,
    Person,
    School,
    Segment,
    Student,
    StudentEnrollment,
)


class TestEnrollmentModels(unittest.IsolatedAsyncioTestCase):
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

    async def _class_in_year(self, session, school, grade, year_number):
        year = AcademicYear(school_id=school.id, year=year_number, status="ACTIVE")
        session.add(year)
        await session.flush()
        klass = Class(academic_year_id=year.id, grade_level_id=grade.id, name="A")
        session.add(klass)
        await session.flush()
        return klass

    async def _fixture(self, session):
        school = School(code="ESCOLA_R0", name="Escola R0")
        session.add(school)
        await session.flush()
        segment = Segment(school_id=school.id, name="Ensino Médio", ordinal=3)
        session.add(segment)
        await session.flush()
        grade = GradeLevel(segment_id=segment.id, name="1ª série", ordinal=1)
        person = Person(school_id=school.id, full_name="Ana Clara")
        session.add_all([grade, person])
        await session.flush()
        student = Student(school_id=school.id, person_id=person.id, student_code="2026001")
        session.add(student)
        await session.flush()
        return school, grade, student

    async def test_a_student_enrolls_in_a_class(self):
        async with self.session_factory() as session:
            school, grade, student = await self._fixture(session)
            klass = await self._class_in_year(session, school, grade, 2026)

            enrollment = StudentEnrollment(
                student_id=student.id, class_id=klass.id, enrolled_on=date(2026, 2, 1)
            )
            session.add(enrollment)
            await session.flush()
            self.assertEqual(enrollment.status, "ACTIVE")

    async def test_the_same_student_cannot_enroll_twice_in_one_class(self):
        async with self.session_factory() as session:
            school, grade, student = await self._fixture(session)
            klass = await self._class_in_year(session, school, grade, 2026)

            session.add(StudentEnrollment(student_id=student.id, class_id=klass.id))
            await session.flush()
            session.add(StudentEnrollment(student_id=student.id, class_id=klass.id))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_a_transition_preserves_the_previous_enrollment(self):
        """Spec §4.3: moving up a year is a recorded fact, not an UPDATE that
        erases the prior state. After promoting, the 2026 enrollment is still
        readable with its own status."""
        async with self.session_factory() as session:
            school, grade, student = await self._fixture(session)
            class_2026 = await self._class_in_year(session, school, grade, 2026)
            class_2027 = await self._class_in_year(session, school, grade, 2027)

            first = StudentEnrollment(student_id=student.id, class_id=class_2026.id)
            session.add(first)
            await session.flush()

            first.status = "COMPLETED"
            second = StudentEnrollment(student_id=student.id, class_id=class_2027.id)
            session.add(second)
            await session.flush()

            session.add(EnrollmentTransition(
                from_enrollment_id=first.id, to_enrollment_id=second.id, kind="PROMOTED"
            ))
            await session.flush()

            stored = (await session.execute(
                select(StudentEnrollment).where(StudentEnrollment.id == first.id)
            )).scalar_one()
            self.assertEqual(stored.status, "COMPLETED")
            self.assertEqual(stored.class_id, class_2026.id)

    async def test_an_exit_transition_has_no_destination(self):
        async with self.session_factory() as session:
            school, grade, student = await self._fixture(session)
            klass = await self._class_in_year(session, school, grade, 2026)
            enrollment = StudentEnrollment(student_id=student.id, class_id=klass.id)
            session.add(enrollment)
            await session.flush()

            session.add(EnrollmentTransition(
                from_enrollment_id=enrollment.id, to_enrollment_id=None, kind="EXITED"
            ))
            await session.flush()

    async def test_transition_kind_is_constrained(self):
        async with self.session_factory() as session:
            school, grade, student = await self._fixture(session)
            klass = await self._class_in_year(session, school, grade, 2026)
            enrollment = StudentEnrollment(student_id=student.id, class_id=klass.id)
            session.add(enrollment)
            await session.flush()

            session.add(EnrollmentTransition(
                from_enrollment_id=enrollment.id, kind="NAO_EXISTE"
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_enrollment_status_is_constrained(self):
        async with self.session_factory() as session:
            school, grade, student = await self._fixture(session)
            klass = await self._class_in_year(session, school, grade, 2026)
            session.add(StudentEnrollment(
                student_id=student.id, class_id=klass.id, status="NAO_EXISTE"
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()
```

- [ ] **Step 2: Rode e confirme que falha**

Run: `.venv/bin/python -m pytest tests/test_r0_enrollment_models.py -v`
Expected: FAIL com `ImportError: cannot import name 'Student'`

- [ ] **Step 3: Acrescente os modelos a `academic.py`**

Acrescente ao final do arquivo:

```python
ENROLLMENT_STATUSES = ("ACTIVE", "TRANSFERRED", "EXITED", "COMPLETED")
TRANSITION_KINDS = ("PROMOTED", "RETAINED", "TRANSFERRED", "EXITED")


class Student(Base):
    """Binds a Person to a school as a student."""

    __tablename__ = "students"
    __table_args__ = (
        UniqueConstraint("school_id", "external_id", name="uq_students_school_external_id"),
        UniqueConstraint("school_id", "student_code", name="uq_students_school_code"),
        CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_students_status"),
        Index("ix_students_school_id", "school_id"),
        Index("ix_students_person_id", "person_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    person_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("persons.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    student_code: Mapped[str | None] = mapped_column(String(50))
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    enrollments: Mapped[list["StudentEnrollment"]] = relationship(back_populates="student")


class StudentEnrollment(Base):
    """One student in one class. At most one row per pair."""

    __tablename__ = "student_enrollments"
    __table_args__ = (
        UniqueConstraint("student_id", "class_id", name="uq_student_enrollments_student_class"),
        CheckConstraint(
            "status IN ('ACTIVE', 'TRANSFERRED', 'EXITED', 'COMPLETED')",
            name="ck_student_enrollments_status",
        ),
        Index("ix_student_enrollments_student_id", "student_id"),
        Index("ix_student_enrollments_class_id", "class_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    student_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("students.id", ondelete="RESTRICT"), nullable=False
    )
    class_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("classes.id", ondelete="RESTRICT"), nullable=False
    )
    external_id: Mapped[str | None] = mapped_column(String(255))
    enrolled_on: Mapped[date | None] = mapped_column(Date)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="ACTIVE")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )

    student: Mapped["Student"] = relationship(back_populates="enrollments")


class EnrollmentTransition(Base):
    """Moving between years is a recorded fact, never an UPDATE that erases the
    prior state (spec §4.3). ``to_enrollment_id`` is NULL when the student left.

    Same principle that versions the rubric in R1: what happened has to stay
    readable after things change.
    """

    __tablename__ = "enrollment_transitions"
    __table_args__ = (
        CheckConstraint(
            "kind IN ('PROMOTED', 'RETAINED', 'TRANSFERRED', 'EXITED')",
            name="ck_enrollment_transitions_kind",
        ),
        Index("ix_enrollment_transitions_from", "from_enrollment_id"),
        Index("ix_enrollment_transitions_to", "to_enrollment_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    from_enrollment_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("student_enrollments.id", ondelete="RESTRICT"), nullable=False
    )
    to_enrollment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("student_enrollments.id", ondelete="RESTRICT")
    )
    kind: Mapped[str] = mapped_column(String(20), nullable=False)
    decided_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )
    decided_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    reason: Mapped[str | None] = mapped_column(Text)
```

- [ ] **Step 4: Exporte os modelos**

Acrescente `EnrollmentTransition`, `Student` e `StudentEnrollment` ao import de `.academic` e a `__all__` em `src/agente_ia_edu/db/models/__init__.py`.

- [ ] **Step 5: Rode e confirme que passa**

Run: `.venv/bin/python -m pytest tests/test_r0_enrollment_models.py -v`
Expected: PASS, 6 testes

- [ ] **Step 6: Escreva a migration**

```python
# migrations/versions/042_student_enrollment.py
"""R0 Fase 1 - student, enrollment and enrollment transition.

Revision ID: 042_student_enrollment
Revises: 041_academic_hierarchy

Purely additive: three new tables, no existing table touched.

``enrollment_transitions`` exists so that moving between years is a recorded
fact rather than an UPDATE that erases the previous state. It is the same
principle that versions the essay rubric in R1: what happened has to stay
readable after things change.
"""

from alembic import op
import sqlalchemy as sa

revision = "042_student_enrollment"
down_revision = "041_academic_hierarchy"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "students",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("person_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("student_code", sa.String(50)),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["person_id"], ["persons.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("school_id", "external_id", name="uq_students_school_external_id"),
        sa.UniqueConstraint("school_id", "student_code", name="uq_students_school_code"),
        sa.CheckConstraint("status IN ('ACTIVE', 'INACTIVE')", name="ck_students_status"),
    )
    op.create_index("ix_students_school_id", "students", ["school_id"])
    op.create_index("ix_students_person_id", "students", ["person_id"])

    op.create_table(
        "student_enrollments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("external_id", sa.String(255)),
        sa.Column("enrolled_on", sa.Date()),
        sa.Column("status", sa.String(20), nullable=False, server_default="ACTIVE"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["student_id"], ["students.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["class_id"], ["classes.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "student_id", "class_id", name="uq_student_enrollments_student_class"
        ),
        sa.CheckConstraint(
            "status IN ('ACTIVE', 'TRANSFERRED', 'EXITED', 'COMPLETED')",
            name="ck_student_enrollments_status",
        ),
    )
    op.create_index("ix_student_enrollments_student_id", "student_enrollments", ["student_id"])
    op.create_index("ix_student_enrollments_class_id", "student_enrollments", ["class_id"])

    op.create_table(
        "enrollment_transitions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("from_enrollment_id", sa.Uuid(), nullable=False),
        sa.Column("to_enrollment_id", sa.Uuid()),
        sa.Column("kind", sa.String(20), nullable=False),
        sa.Column("decided_by_user_id", sa.Uuid()),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("reason", sa.Text()),
        sa.ForeignKeyConstraint(
            ["from_enrollment_id"], ["student_enrollments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["to_enrollment_id"], ["student_enrollments.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(["decided_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.CheckConstraint(
            "kind IN ('PROMOTED', 'RETAINED', 'TRANSFERRED', 'EXITED')",
            name="ck_enrollment_transitions_kind",
        ),
    )
    op.create_index(
        "ix_enrollment_transitions_from", "enrollment_transitions", ["from_enrollment_id"]
    )
    op.create_index(
        "ix_enrollment_transitions_to", "enrollment_transitions", ["to_enrollment_id"]
    )


def downgrade() -> None:
    op.drop_table("enrollment_transitions")
    op.drop_table("student_enrollments")
    op.drop_table("students")
```

- [ ] **Step 7: Verifique a migration no banco descartável**

Mesmo procedimento da Task 1, Step 8.

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/db/models/academic.py src/agente_ia_edu/db/models/__init__.py migrations/versions/042_student_enrollment.py tests/test_r0_enrollment_models.py
git commit -m "feat: aluno, matricula e transicao de matricula

Passar de ano e um fato registrado, nao um UPDATE que apaga o estado
anterior. Mesmo principio que versiona a regua em R1: o que aconteceu
precisa continuar legivel depois que as coisas mudam."
```

---

### Task 4: Colunas FK em `user_school_links`

**Files:**
- Modify: `src/agente_ia_edu/db/models/admin.py`
- Create: `migrations/versions/043_user_school_link_entities.py`
- Create: `tests/test_r0_user_school_link_bridge.py`

**Interfaces:**
- Consumes: `Class`, `GradeLevel`, `SchoolUnit`, `Segment`, `User` das Tasks 1-3.
- Produces: cinco colunas anuláveis em `UserSchoolLink`: `user_id`, `school_unit_id`, `segment_id`, `grade_level_id`, `class_id`.

**Por que não existe `StaffAssignment`:** `user_school_links` já tem papel e escopo e já é consultado pela autorização. Uma tabela paralela produziria dois jeitos de dizer quem enxerga o quê, e o que decidiria de verdade seria o mais antigo (spec §4.4).

- [ ] **Step 1: Escreva os testes que falham**

```python
# tests/test_r0_user_school_link_bridge.py
import unittest

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    GradeLevel,
    Person,
    School,
    Segment,
    User,
    UserSchoolLink,
)


class TestUserSchoolLinkBridge(unittest.IsolatedAsyncioTestCase):
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

    async def test_an_existing_style_link_still_works_with_no_entity(self):
        """The bridge is additive: a link written the old way, with only a
        scope string, must keep working untouched. Every row in production
        today looks like this."""
        async with self.session_factory() as session:
            school = School(code="ESCOLA_R0", name="Escola R0")
            session.add(school)
            await session.flush()

            link = UserSchoolLink(
                external_user_id="prof_mendes",
                school_id=school.id,
                role="TEACHER",
                scope_type="CLASSROOM",
                scope_external_id="TURMA_3A",
            )
            session.add(link)
            await session.flush()

            self.assertIsNone(link.class_id)
            self.assertIsNone(link.user_id)
            self.assertEqual(link.scope_external_id, "TURMA_3A")

    async def test_a_link_may_point_at_a_real_class(self):
        async with self.session_factory() as session:
            school = School(code="ESCOLA_R0", name="Escola R0")
            session.add(school)
            await session.flush()
            segment = Segment(school_id=school.id, name="Ensino Médio", ordinal=3)
            year = AcademicYear(school_id=school.id, year=2026, status="ACTIVE")
            person = Person(school_id=school.id, full_name="Prof. Mendes")
            session.add_all([segment, year, person])
            await session.flush()
            grade = GradeLevel(segment_id=segment.id, name="3ª série", ordinal=3)
            user = User(
                person_id=person.id,
                external_identity_provider="host",
                external_user_id="prof_mendes",
            )
            session.add_all([grade, user])
            await session.flush()
            klass = Class(academic_year_id=year.id, grade_level_id=grade.id, name="A")
            session.add(klass)
            await session.flush()

            link = UserSchoolLink(
                external_user_id="prof_mendes",
                school_id=school.id,
                role="TEACHER",
                scope_type="CLASSROOM",
                scope_external_id="TURMA_3A",
                user_id=user.id,
                class_id=klass.id,
            )
            session.add(link)
            await session.flush()

            self.assertEqual(link.class_id, klass.id)
            self.assertEqual(link.scope_external_id, "TURMA_3A")
```

- [ ] **Step 2: Rode e confirme que falha**

Run: `.venv/bin/python -m pytest tests/test_r0_user_school_link_bridge.py -v`
Expected: FAIL com `TypeError: 'class_id' is an invalid keyword argument for UserSchoolLink`

- [ ] **Step 3: Acrescente as colunas ao modelo**

Em `src/agente_ia_edu/db/models/admin.py`, dentro da classe `UserSchoolLink`, logo após `scope_external_id`:

```python
    # R0 bridge: the entity this scope points at, when it is already known.
    # Nullable on purpose - every row written before R0 has only the string,
    # and consumers migrate one at a time (spec §3.2, §7).
    user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )
    school_unit_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("school_units.id", ondelete="RESTRICT")
    )
    segment_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("segments.id", ondelete="RESTRICT")
    )
    grade_level_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("grade_levels.id", ondelete="RESTRICT")
    )
    class_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("classes.id", ondelete="RESTRICT")
    )
```

E acrescente dois índices a `__table_args__`, junto dos existentes:

```python
        Index("ix_user_school_links_user_id", "user_id"),
        Index("ix_user_school_links_class_id", "class_id"),
```

- [ ] **Step 4: Rode e confirme que passa**

Run: `.venv/bin/python -m pytest tests/test_r0_user_school_link_bridge.py -v`
Expected: PASS, 2 testes

- [ ] **Step 5: Confirme que nada de administração quebrou**

Run: `.venv/bin/python -m pytest tests/test_platform_administration.py -v`
Expected: PASS, 8 testes. Estas colunas são anuláveis e não mudam comportamento; se algo aqui falhar, pare e reporte em vez de ajustar o teste.

- [ ] **Step 6: Escreva a migration**

```python
# migrations/versions/043_user_school_link_entities.py
"""R0 Fase 1 - bridge columns on user_school_links.

Revision ID: 043_user_school_link_entities
Revises: 042_student_enrollment

Additive and non-destructive: five NULLABLE columns on an existing table. No
row is altered, no existing column or constraint is touched, and no behaviour
changes - nothing reads these columns until Phase 3.

Deliberately NOT a new table. ``user_school_links`` already carries role and
scope and is already consulted by the authorisation service; a parallel
``staff_assignments`` would create two ways to say who sees what, and the one
that actually decided would be the older one (spec §4.4).
"""

from alembic import op
import sqlalchemy as sa

revision = "043_user_school_link_entities"
down_revision = "042_student_enrollment"
branch_labels = None
depends_on = None

_COLUMNS = (
    ("user_id", "users"),
    ("school_unit_id", "school_units"),
    ("segment_id", "segments"),
    ("grade_level_id", "grade_levels"),
    ("class_id", "classes"),
)


def upgrade() -> None:
    for column, target in _COLUMNS:
        op.add_column("user_school_links", sa.Column(column, sa.Uuid(), nullable=True))
        op.create_foreign_key(
            f"fk_user_school_links_{column}",
            "user_school_links",
            target,
            [column],
            ["id"],
            ondelete="RESTRICT",
        )
    op.create_index("ix_user_school_links_user_id", "user_school_links", ["user_id"])
    op.create_index("ix_user_school_links_class_id", "user_school_links", ["class_id"])


def downgrade() -> None:
    op.drop_index("ix_user_school_links_class_id", table_name="user_school_links")
    op.drop_index("ix_user_school_links_user_id", table_name="user_school_links")
    for column, _ in reversed(_COLUMNS):
        op.drop_constraint(
            f"fk_user_school_links_{column}", "user_school_links", type_="foreignkey"
        )
        op.drop_column("user_school_links", column)
```

- [ ] **Step 7: Verifique a migration no banco descartável**

Mesmo procedimento da Task 1, Step 8. Esta é a única migration da fase que toca uma tabela existente — confira que o `downgrade` devolve a tabela ao estado anterior.

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/db/models/admin.py migrations/versions/043_user_school_link_entities.py tests/test_r0_user_school_link_bridge.py
git commit -m "feat: colunas ponte em user_school_links, todas anulaveis

Nao e uma tabela nova. user_school_links ja tem papel e escopo e ja e lido
pela autorizacao; uma tabela paralela produziria dois jeitos de dizer quem
enxerga o que, e o que decidiria de verdade seria o mais antigo.

Nada le estas colunas ainda. A migracao dos consumidores e a Fase 3."
```

---

### Task 5: Configuração da instituição — tabelas

**Files:**
- Create: `src/agente_ia_edu/db/models/institution.py`
- Create: `migrations/versions/044_institution_settings.py`
- Create: `tests/test_r0_institution_models.py`
- Modify: `src/agente_ia_edu/db/models/__init__.py`

**Interfaces:**
- Consumes: `User` da Task 1.
- Produces: `SchoolSetting`, `SchoolIdentityVersion`. Constantes `CORRECTION_MODES`, `VALIDATION_MODES`.

- [ ] **Step 1: Escreva os testes que falham**

```python
# tests/test_r0_institution_models.py
import unittest

from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School, SchoolIdentityVersion, SchoolSetting


class TestInstitutionModels(unittest.IsolatedAsyncioTestCase):
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

    async def _school(self, session, code="ESCOLA_R0") -> School:
        school = School(code=code, name="Escola R0")
        session.add(school)
        await session.flush()
        return school

    async def test_formative_mode_accepts_no_validation_policy(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            session.add(SchoolSetting(school_id=school.id, correction_mode="FORMATIVO"))
            await session.flush()

    async def test_formative_mode_rejects_a_validation_policy(self):
        """Spec §5.1: the validation policy only means something when scores
        exist. A formative school with a validation default is a configuration
        nobody can act on."""
        async with self.session_factory() as session:
            school = await self._school(session)
            session.add(SchoolSetting(
                school_id=school.id,
                correction_mode="FORMATIVO",
                validation_default="EM_LOTE",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_formative_mode_rejects_a_threshold(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            session.add(SchoolSetting(
                school_id=school.id,
                correction_mode="FORMATIVO",
                validation_threshold_points=500,
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_evaluative_mode_accepts_the_full_policy(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            setting = SchoolSetting(
                school_id=school.id,
                correction_mode="AVALIATIVO",
                validation_default="UMA_A_UMA",
                validation_teacher_can_disable=True,
                validation_threshold_points=500,
            )
            session.add(setting)
            await session.flush()
            self.assertTrue(setting.validation_teacher_can_disable)

    async def test_correction_mode_is_constrained(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            session.add(SchoolSetting(school_id=school.id, correction_mode="NAO_EXISTE"))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_validation_default_is_constrained(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            session.add(SchoolSetting(
                school_id=school.id,
                correction_mode="AVALIATIVO",
                validation_default="NAO_EXISTE",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_a_school_has_at_most_one_settings_row(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            session.add(SchoolSetting(school_id=school.id, correction_mode="FORMATIVO"))
            await session.flush()
            session.add(SchoolSetting(school_id=school.id, correction_mode="AVALIATIVO"))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_identity_version_is_unique_per_school(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            session.add(SchoolIdentityVersion(
                school_id=school.id, version=1, display_name="Colégio Exemplo"
            ))
            await session.flush()
            session.add(SchoolIdentityVersion(
                school_id=school.id, version=1, display_name="Colégio Exemplo 2"
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()
```

- [ ] **Step 2: Rode e confirme que falha**

Run: `.venv/bin/python -m pytest tests/test_r0_institution_models.py -v`
Expected: FAIL com `ImportError: cannot import name 'SchoolSetting'`

- [ ] **Step 3: Crie o módulo de modelos**

```python
# src/agente_ia_edu/db/models/institution.py
"""R0 - per-institution configuration.

Typed columns with CHECK constraints rather than a free JSON blob, and the
reason is a lesson R1 paid for: its final review found ``provenance`` guarded on
one column of three, and validating in the loader was not enough because the
seeder wrote a hardcoded value and the constraint never fired. What has a rule,
the database refuses (spec §3.5).

The stake here is higher than a rubric's. A school whose mode is stored wrong is
not ugly data; it is a school running in evaluative mode believing it is
formative, in the exact control that exists for regulatory reasons.
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
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..base import Base
from ..types import JSONBCompatible

CORRECTION_MODES = ("FORMATIVO", "AVALIATIVO")
VALIDATION_MODES = ("UMA_A_UMA", "EM_LOTE", "AUTOMATICA")


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class SchoolSetting(Base):
    """One row per school.

    ``correction_mode`` says whether a score exists at all. The validation policy
    says who approves it, and only means something when scores exist - which is
    what the two CHECK constraints below enforce.

    The policy stored here is the DEFAULT and the PERMITTED, not the decision:
    the real choice happens per class when a teacher sends a prompt, in R3. A
    school may authorise teachers to skip validation, or fix that they may not.
    """

    __tablename__ = "school_settings"
    __table_args__ = (
        UniqueConstraint("school_id", name="uq_school_settings_school"),
        CheckConstraint(
            "correction_mode IN ('FORMATIVO', 'AVALIATIVO')",
            name="ck_school_settings_correction_mode",
        ),
        CheckConstraint(
            "validation_default IS NULL OR validation_default IN "
            "('UMA_A_UMA', 'EM_LOTE', 'AUTOMATICA')",
            name="ck_school_settings_validation_default",
        ),
        CheckConstraint(
            "correction_mode = 'AVALIATIVO' OR validation_default IS NULL",
            name="ck_school_settings_policy_requires_evaluative",
        ),
        CheckConstraint(
            "correction_mode = 'AVALIATIVO' OR validation_threshold_points IS NULL",
            name="ck_school_settings_threshold_requires_evaluative",
        ),
        CheckConstraint(
            "validation_threshold_points IS NULL OR "
            "(validation_threshold_points >= 0 AND validation_threshold_points <= 1000)",
            name="ck_school_settings_threshold_range",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    correction_mode: Mapped[str] = mapped_column(
        String(20), nullable=False, default="FORMATIVO"
    )
    validation_default: Mapped[str | None] = mapped_column(String(20))
    validation_teacher_can_disable: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    validation_threshold_points: Mapped[int | None] = mapped_column(Integer)
    current_identity_version_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("school_identity_versions.id", ondelete="RESTRICT")
    )
    metadata_: Mapped[dict[str, Any] | None] = mapped_column("metadata", JSONBCompatible)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )


class SchoolIdentityVersion(Base):
    """The school's visual identity, versioned.

    Versioned for the same reason the rubric is: REDAÇÃO spec §8 requires that
    regenerating an old devolutiva's PDF must not rewrite the document the family
    already received. If the school changes its logo in March, February's
    devolutiva keeps February's mark - so the devolutiva stamps the version id,
    exactly as it stamps ``rubric_version``.

    A published version is immutable. Changing the identity creates a new one.
    """

    __tablename__ = "school_identity_versions"
    __table_args__ = (
        UniqueConstraint("school_id", "version", name="uq_school_identity_versions_version"),
        CheckConstraint("version > 0", name="ck_school_identity_versions_version_positive"),
        Index("ix_school_identity_versions_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    display_name: Mapped[str] = mapped_column(String(255), nullable=False)
    logo_asset_uri: Mapped[str | None] = mapped_column(String(1024))
    primary_color: Mapped[str | None] = mapped_column(String(9))
    secondary_color: Mapped[str | None] = mapped_column(String(9))
    published_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    published_by_user_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("users.id", ondelete="RESTRICT")
    )
```

- [ ] **Step 4: Exporte os modelos**

Em `src/agente_ia_edu/db/models/__init__.py`:

```python
from .institution import SchoolIdentityVersion, SchoolSetting
```

```python
    "SchoolIdentityVersion",
    "SchoolSetting",
```

- [ ] **Step 5: Rode e confirme que passa**

Run: `.venv/bin/python -m pytest tests/test_r0_institution_models.py -v`
Expected: PASS, 8 testes

- [ ] **Step 6: Escreva a migration**

```python
# migrations/versions/044_institution_settings.py
"""R0 Fase 1 - per-institution settings and versioned visual identity.

Revision ID: 044_institution_settings
Revises: 043_user_school_link_entities

Purely additive: two new tables, no existing table touched.

Typed columns with CHECK constraints rather than JSON, because R1's final review
proved that validating only in code lets a wrong value through when the writer
hardcodes it. Here the stake is a school running in evaluative mode believing it
is formative (spec §3.5).
"""

from alembic import op
import sqlalchemy as sa

revision = "044_institution_settings"
down_revision = "043_user_school_link_entities"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "school_identity_versions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("version", sa.Integer(), nullable=False),
        sa.Column("display_name", sa.String(255), nullable=False),
        sa.Column("logo_asset_uri", sa.String(1024)),
        sa.Column("primary_color", sa.String(9)),
        sa.Column("secondary_color", sa.String(9)),
        sa.Column("published_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("published_by_user_id", sa.Uuid()),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(["published_by_user_id"], ["users.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "school_id", "version", name="uq_school_identity_versions_version"
        ),
        sa.CheckConstraint("version > 0", name="ck_school_identity_versions_version_positive"),
    )
    op.create_index(
        "ix_school_identity_versions_school_id", "school_identity_versions", ["school_id"]
    )

    op.create_table(
        "school_settings",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column(
            "correction_mode", sa.String(20), nullable=False, server_default="FORMATIVO"
        ),
        sa.Column("validation_default", sa.String(20)),
        sa.Column(
            "validation_teacher_can_disable",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
        sa.Column("validation_threshold_points", sa.Integer()),
        sa.Column("current_identity_version_id", sa.Uuid()),
        sa.Column("metadata", _JSON),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.ForeignKeyConstraint(
            ["current_identity_version_id"],
            ["school_identity_versions.id"],
            ondelete="RESTRICT",
        ),
        sa.UniqueConstraint("school_id", name="uq_school_settings_school"),
        sa.CheckConstraint(
            "correction_mode IN ('FORMATIVO', 'AVALIATIVO')",
            name="ck_school_settings_correction_mode",
        ),
        sa.CheckConstraint(
            "validation_default IS NULL OR validation_default IN "
            "('UMA_A_UMA', 'EM_LOTE', 'AUTOMATICA')",
            name="ck_school_settings_validation_default",
        ),
        sa.CheckConstraint(
            "correction_mode = 'AVALIATIVO' OR validation_default IS NULL",
            name="ck_school_settings_policy_requires_evaluative",
        ),
        sa.CheckConstraint(
            "correction_mode = 'AVALIATIVO' OR validation_threshold_points IS NULL",
            name="ck_school_settings_threshold_requires_evaluative",
        ),
        sa.CheckConstraint(
            "validation_threshold_points IS NULL OR "
            "(validation_threshold_points >= 0 AND validation_threshold_points <= 1000)",
            name="ck_school_settings_threshold_range",
        ),
    )


def downgrade() -> None:
    op.drop_table("school_settings")
    op.drop_index(
        "ix_school_identity_versions_school_id", table_name="school_identity_versions"
    )
    op.drop_table("school_identity_versions")
```

- [ ] **Step 7: Verifique a migration no banco descartável**

Mesmo procedimento da Task 1, Step 8.

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/db/models/institution.py src/agente_ia_edu/db/models/__init__.py migrations/versions/044_institution_settings.py tests/test_r0_institution_models.py
git commit -m "feat: configuracao da instituicao em colunas tipadas

Dois controles com papeis distintos: o modo diz se existe nota, a politica
de validacao diz quem aprova - e so existe no avaliativo, o que duas CHECK
garantem.

A identidade visual e versionada pela mesma razao que a regua: regerar o PDF
de uma devolutiva antiga nao pode reescrever o documento que a familia ja
recebeu."
```

---

### Task 6: Serviço de configuração, com auditoria obrigatória

**Files:**
- Create: `src/agente_ia_edu/services/institution_settings.py`
- Create: `tests/test_r0_institution_settings_service.py`

**Interfaces:**
- Consumes: `SchoolSetting`, `SchoolIdentityVersion` da Task 5; `AdminAuditLog` de `agente_ia_edu.db.models`.
- Produces: `InstitutionSettingsService(session)` com `get_settings(school_id)`, `configure(school_id, *, performed_by_external_id, **changes) -> SchoolSetting`, `publish_identity(school_id, *, performed_by_external_id, display_name, logo_asset_uri=None, primary_color=None, secondary_color=None) -> SchoolIdentityVersion`. Exceção `IdentityVersionImmutableError`.

- [ ] **Step 1: Escreva os testes que falham**

```python
# tests/test_r0_institution_settings_service.py
import unittest

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AdminAuditLog, School, SchoolIdentityVersion
from agente_ia_edu.services.institution_settings import (
    IdentityVersionImmutableError,
    InstitutionSettingsService,
)


class TestInstitutionSettingsService(unittest.IsolatedAsyncioTestCase):
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

    async def _school(self, session) -> School:
        school = School(code="ESCOLA_R0", name="Escola R0")
        session.add(school)
        await session.flush()
        return school

    async def test_first_read_creates_a_formative_default(self):
        """A school with no settings row is formative until someone says
        otherwise. Formative is the configuration that produces no score at all,
        and therefore the safest default to fall into."""
        async with self.session_factory() as session:
            school = await self._school(session)
            settings = await InstitutionSettingsService(session).get_settings(school.id)
            self.assertEqual(settings.correction_mode, "FORMATIVO")
            self.assertIsNone(settings.validation_default)

    async def test_configure_writes_an_audit_entry(self):
        """Spec §5.3: who changed a school's mode, when, and from what to what,
        has to be answerable."""
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)

            await service.configure(
                school.id,
                performed_by_external_id="admin:master",
                correction_mode="AVALIATIVO",
                validation_default="EM_LOTE",
            )

            logs = (await session.execute(select(AdminAuditLog))).scalars().all()
            self.assertEqual(len(logs), 1)
            self.assertEqual(logs[0].performed_by_external_id, "admin:master")
            self.assertIn("correction_mode", str(logs[0].metadata_))
            self.assertIn("FORMATIVO", str(logs[0].metadata_))
            self.assertIn("AVALIATIVO", str(logs[0].metadata_))

    async def test_configure_rejects_a_policy_in_formative_mode(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            with self.assertRaises(ValueError) as caught:
                await service.configure(
                    school.id,
                    performed_by_external_id="admin:master",
                    correction_mode="FORMATIVO",
                    validation_default="EM_LOTE",
                )
            self.assertIn("FORMATIVO", str(caught.exception))

    async def test_publishing_identity_increments_the_version(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)

            first = await service.publish_identity(
                school.id, performed_by_external_id="admin:master", display_name="Colégio A"
            )
            second = await service.publish_identity(
                school.id, performed_by_external_id="admin:master", display_name="Colégio B"
            )

            self.assertEqual(first.version, 1)
            self.assertEqual(second.version, 2)

            settings = await service.get_settings(school.id)
            self.assertEqual(settings.current_identity_version_id, second.id)

    async def test_a_published_identity_is_immutable(self):
        """Spec §5.2: February's devolutiva keeps February's mark. Editing a
        published version would rewrite a document someone already received."""
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            published = await service.publish_identity(
                school.id, performed_by_external_id="admin:master", display_name="Colégio A"
            )

            with self.assertRaises(IdentityVersionImmutableError):
                await service.amend_identity(published.id, display_name="Outro nome")

    async def test_publishing_identity_writes_an_audit_entry(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            await service.publish_identity(
                school.id, performed_by_external_id="admin:master", display_name="Colégio A"
            )
            count = await session.scalar(select(func.count()).select_from(AdminAuditLog))
            self.assertEqual(count, 1)

    async def test_the_previous_identity_version_survives(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            service = InstitutionSettingsService(session)
            await service.publish_identity(
                school.id, performed_by_external_id="admin:master", display_name="Colégio A"
            )
            await service.publish_identity(
                school.id, performed_by_external_id="admin:master", display_name="Colégio B"
            )

            versions = (await session.execute(
                select(SchoolIdentityVersion).order_by(SchoolIdentityVersion.version)
            )).scalars().all()
            self.assertEqual([v.display_name for v in versions], ["Colégio A", "Colégio B"])
```

- [ ] **Step 2: Rode e confirme que falha**

Run: `.venv/bin/python -m pytest tests/test_r0_institution_settings_service.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.services.institution_settings'`

- [ ] **Step 3: Implemente o serviço**

```python
# src/agente_ia_edu/services/institution_settings.py
"""R0 - the only sanctioned way to read and change a school's configuration.

Every write here records who did it, when, and from what value to which, because
"who put this school in evaluative mode?" has to be answerable (spec §5.3). A
direct UPDATE bypasses that, so nothing outside this service writes to
``school_settings`` or ``school_identity_versions``.

Publishing a visual identity never edits a published one. February's devolutiva
keeps February's mark; editing in place would rewrite a document a family
already received (spec §5.2).
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import AdminAuditLog, SchoolIdentityVersion, SchoolSetting
from agente_ia_edu.db.models.institution import CORRECTION_MODES, VALIDATION_MODES

_POLICY_FIELDS = ("validation_default", "validation_threshold_points")


class IdentityVersionImmutableError(RuntimeError):
    """A published visual identity cannot be edited; publish a new version."""


class InstitutionSettingsService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def get_settings(self, school_id: uuid.UUID) -> SchoolSetting:
        """Return the school's settings, creating a formative default if absent.

        Formative is the default because it produces no score at all, which is
        the configuration that assumes the least about what the school intends.
        """
        existing = await self.session.scalar(
            select(SchoolSetting).where(SchoolSetting.school_id == school_id)
        )
        if existing is not None:
            return existing

        created = SchoolSetting(school_id=school_id, correction_mode="FORMATIVO")
        self.session.add(created)
        await self.session.flush()
        return created

    async def configure(
        self,
        school_id: uuid.UUID,
        *,
        performed_by_external_id: str,
        **changes: Any,
    ) -> SchoolSetting:
        """Apply ``changes`` and record who made them.

        Raises ``ValueError`` when the resulting configuration is incoherent, so
        the caller learns why rather than meeting a database constraint error.
        """
        settings = await self.get_settings(school_id)

        unknown = set(changes) - {
            "correction_mode",
            "validation_default",
            "validation_teacher_can_disable",
            "validation_threshold_points",
        }
        if unknown:
            raise ValueError(f"Unknown setting(s): {sorted(unknown)}")

        mode = changes.get("correction_mode", settings.correction_mode)
        if mode not in CORRECTION_MODES:
            raise ValueError(f"Unknown correction_mode {mode!r}")

        default = changes.get("validation_default", settings.validation_default)
        if default is not None and default not in VALIDATION_MODES:
            raise ValueError(f"Unknown validation_default {default!r}")

        if mode == "FORMATIVO":
            for field in _POLICY_FIELDS:
                value = changes.get(field, getattr(settings, field))
                if value is not None:
                    raise ValueError(
                        f"{field} has no meaning in FORMATIVO mode, where no score "
                        f"exists to validate; got {value!r}"
                    )

        before = {field: getattr(settings, field) for field in changes}
        for field, value in changes.items():
            setattr(settings, field, value)

        self.session.add(AdminAuditLog(
            school_id=school_id,
            performed_by_external_id=performed_by_external_id,
            action="SCHOOL_SETTINGS_UPDATED",
            entity_type="SCHOOL_SETTINGS",
            entity_id=str(settings.id),
            metadata_={"before": _stringify(before), "after": _stringify(changes)},
        ))
        await self.session.flush()
        return settings

    async def publish_identity(
        self,
        school_id: uuid.UUID,
        *,
        performed_by_external_id: str,
        display_name: str,
        logo_asset_uri: str | None = None,
        primary_color: str | None = None,
        secondary_color: str | None = None,
    ) -> SchoolIdentityVersion:
        """Publish a new visual identity and point the school at it."""
        highest = await self.session.scalar(
            select(func.max(SchoolIdentityVersion.version)).where(
                SchoolIdentityVersion.school_id == school_id
            )
        )
        version = SchoolIdentityVersion(
            school_id=school_id,
            version=(highest or 0) + 1,
            display_name=display_name,
            logo_asset_uri=logo_asset_uri,
            primary_color=primary_color,
            secondary_color=secondary_color,
            published_at=datetime.now(timezone.utc),
        )
        self.session.add(version)
        await self.session.flush()

        settings = await self.get_settings(school_id)
        settings.current_identity_version_id = version.id

        self.session.add(AdminAuditLog(
            school_id=school_id,
            performed_by_external_id=performed_by_external_id,
            action="SCHOOL_IDENTITY_PUBLISHED",
            entity_type="SCHOOL_IDENTITY_VERSION",
            entity_id=str(version.id),
            metadata_={"version": version.version, "display_name": display_name},
        ))
        await self.session.flush()
        return version

    async def amend_identity(self, identity_version_id: uuid.UUID, **_: Any) -> None:
        """Always raises. A published identity is immutable by design.

        This method exists so the refusal is explicit and discoverable, rather
        than a rule someone has to remember not to break.
        """
        raise IdentityVersionImmutableError(
            f"Identity version {identity_version_id} is published and cannot be edited. "
            "Publish a new version instead - a devolutiva already delivered must keep "
            "the identity it was delivered with."
        )


def _stringify(values: dict[str, Any]) -> dict[str, str | None]:
    return {key: (None if value is None else str(value)) for key, value in values.items()}


__all__ = ["IdentityVersionImmutableError", "InstitutionSettingsService"]
```

- [ ] **Step 4: Confirme que a auditoria grava**

`AdminAuditLog` já foi inspecionada: suas colunas são `id`, `performed_by_external_id`,
`action`, `entity_type`, `entity_id`, `school_id`, `metadata` (mapeada como `metadata_`) e
`created_at`. **`entity_type` e `entity_id` são NOT NULL** — é por isso que o código acima
os preenche. Omiti-los faz toda escrita de auditoria falhar com `IntegrityError`, e foi o
defeito que esta verificação existe para não repetir.

Run: `.venv/bin/python -m pytest tests/test_r0_institution_settings_service.py::TestInstitutionSettingsService::test_configure_writes_an_audit_entry -v`
Expected: PASS. Se falhar com `IntegrityError`, alguma coluna obrigatória de
`AdminAuditLog` ficou sem valor — preencha-a em vez de alterar a tabela, que é existente
e não pode mudar nesta fase.

- [ ] **Step 5: Rode e confirme que passa**

Run: `.venv/bin/python -m pytest tests/test_r0_institution_settings_service.py -v`
Expected: PASS, 7 testes

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/institution_settings.py tests/test_r0_institution_settings_service.py
git commit -m "feat: servico de configuracao da instituicao, com auditoria obrigatoria

Toda escrita registra quem fez, quando, e de que valor para qual. Quem pos
esta escola em modo avaliativo precisa ser uma pergunta respondivel.

Identidade visual publicada e imutavel: amend_identity sempre levanta, para
que a recusa seja explicita e descobrivel em vez de uma regra que alguem
precisa lembrar de nao quebrar."
```

---

### Task 7: Gate da fase

**Files:**
- Create: `tests/test_r0_phase1_gate.py`

**Interfaces:**
- Consumes: tudo das Tasks 1-6.
- Produces: nada. É o teste que prova que a fase é aditiva.

Esta tarefa existe porque a afirmação central da Fase 1 — "nada quebra, nenhum comportamento muda" — não é verificada por nenhum teste das tarefas anteriores, que olham só para o que criaram.

- [ ] **Step 1: Escreva o teste**

```python
# tests/test_r0_phase1_gate.py
"""Proves the phase's central claim: everything here is additive.

Each earlier task tested what it created. Nothing tested that the rest of the
system is unaffected - and "purely additive" is exactly the kind of claim that
is believed rather than checked.
"""

import unittest

from sqlalchemy import inspect
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School, UserSchoolLink

R0_TABLES = {
    "persons",
    "users",
    "academic_years",
    "school_units",
    "segments",
    "grade_levels",
    "classes",
    "students",
    "student_enrollments",
    "enrollment_transitions",
    "school_settings",
    "school_identity_versions",
}

FORBIDDEN_COLUMN_FRAGMENTS = ("password", "token", "secret", "credential", "senha")


class TestPhase1IsAdditive(unittest.IsolatedAsyncioTestCase):
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

    def test_every_r0_table_exists(self):
        declared = set(Base.metadata.tables)
        missing = R0_TABLES - declared
        self.assertEqual(missing, set(), f"tabelas de R0 ausentes: {sorted(missing)}")

    def test_no_table_anywhere_has_a_credential_column(self):
        """Not just `users`: the rule is that this system stores no credential
        at all, so the guard covers every table in the schema."""
        offenders = []
        for table_name, table in Base.metadata.tables.items():
            for column in table.columns:
                lowered = column.name.lower()
                if any(fragment in lowered for fragment in FORBIDDEN_COLUMN_FRAGMENTS):
                    offenders.append(f"{table_name}.{column.name}")
        self.assertEqual(offenders, [], f"colunas de credencial encontradas: {offenders}")

    def test_the_new_link_columns_are_all_nullable(self):
        """The bridge only works if every existing row stays valid without it."""
        columns = {c.name: c for c in inspect(UserSchoolLink).columns}
        for name in (
            "user_id",
            "school_unit_id",
            "segment_id",
            "grade_level_id",
            "class_id",
        ):
            with self.subTest(column=name):
                self.assertIn(name, columns)
                self.assertTrue(columns[name].nullable)

    async def test_a_school_still_works_with_no_r0_rows_at_all(self):
        """Every school in production is in this state today: it exists, it has
        links, and it has nothing from R0. That must keep working."""
        async with self.session_factory() as session:
            school = School(code="ESCOLA_LEGADA", name="Escola Legada")
            session.add(school)
            await session.flush()

            link = UserSchoolLink(
                external_user_id="prof_mendes",
                school_id=school.id,
                role="TEACHER",
                scope_type="CLASSROOM",
                scope_external_id="TURMA_3A",
            )
            session.add(link)
            await session.flush()

            self.assertIsNone(link.class_id)
            self.assertEqual(link.scope_external_id, "TURMA_3A")
```

- [ ] **Step 2: Rode e confirme que passa**

Run: `.venv/bin/python -m pytest tests/test_r0_phase1_gate.py -v`
Expected: PASS, 4 testes

- [ ] **Step 3: Rode o gate inteiro da fase**

Run: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py tests/test_r1_*.py -q`
Expected: PASS. Se algo de `test_platform_administration` ou `test_r1_*` falhar, a fase deixou de ser aditiva — pare e reporte em vez de ajustar o teste.

- [ ] **Step 4: Commit**

```bash
git add tests/test_r0_phase1_gate.py
git commit -m "test: prova que a Fase 1 e aditiva

Cada tarefa testou o que criou. Nada testava que o resto do sistema segue
intacto - e 'puramente aditivo' e exatamente o tipo de afirmacao que se
acredita em vez de conferir.

Inclui a varredura de credencial em TODAS as tabelas do schema, nao so em
users: a regra e que este sistema nao guarda credencial nenhuma."
```

---

## Verificação final da fase

- [ ] `.venv/bin/python -m pytest tests/test_r0_*.py -q` — todos passando
- [ ] `.venv/bin/python -m pytest tests/test_r1_*.py tests/test_platform_administration.py -q` — nenhuma regressão
- [ ] As cinco migrations sobem e descem no banco descartável, na porta 5433
- [ ] `git diff 2109576..HEAD --stat -- src/agente_ia_edu/services/` mostra apenas `institution_settings.py`
- [ ] Nenhum consumidor existente foi modificado — a migração é a Fase 3

## Critérios de aceite do spec cobertos nesta fase

| Critério (spec §9) | Onde |
|---|---|
| As dez entidades acadêmicas existem, com `external_id` por escola | Tasks 1-3 |
| `users` não tem coluna de credencial, e um teste recusa a introdução | Tasks 1 e 7 |
| A docstring de `identity.py` registra a revogação | Task 1 |
| `classes` pertence a `academic_years` | Task 2 |
| `enrollment_transitions` preserva a matrícula anterior | Task 3 |
| `user_school_links` ganhou FKs anuláveis, sem alterar linha existente | Tasks 4 e 7 |
| `school_settings` recusa política em modo formativo | Tasks 5 e 6 |
| Identidade publicada é imutável | Tasks 5 e 6 |
| Toda escrita em configuração registra auditoria | Task 6 |
| Nenhuma migration destrutiva | Task 7 e verificação final |

**Fora desta fase, por decisão:** o portão de disciplina é a Fase 2; a migração dos consumidores, a remoção do andaime `TURMA_3A` e a migração das ~77 dependências nos testes são a Fase 3.
