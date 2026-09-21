# R2 — Propostas e envio de redação — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let a real student submit an essay (typed, photo, or PDF) against a proposal their class was actually assigned, with an optional OCR/review step for photo/PDF, closing the identity→enrollment resolver gap R0 left open.

**Architecture:** Five new SQLAlchemy tables (`essay_prompts`, `prompt_materials`, `prompt_assignments`, `essay_submissions`, `essay_submission_pages`) plus one new column (`school_settings.transcription_enabled`). Two new services (`EssayProposalService`, `EssaySubmissionService`) sit behind two new FastAPI routers, reusing `AuthorizationService`, `MaterialStorage`, and R1's `normalize_essay_text`/`essay_text_hash` without modification. OCR is a new, swappable provider (`EssayTranscriptionProvider`) added to the existing `providers/` package, backed by a real OpenAI vision call.

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (async), Alembic, `unittest` (`IsolatedAsyncioTestCase` / `TestCase`), `pymupdf` (already a dependency, for PDF→page-image splitting), `openai` (already a dependency, for OCR).

**Spec:** `docs/superpowers/specs/2026-09-21-r2-propostas-envio-redacao-design.md`

## Global Constraints

- **No `sa.Enum`/`postgresql.ENUM` anywhere.** Every status/mode/kind field is `String(N)` + a `CheckConstraint("... IN (...)")`, matching every existing model in this repo (confirmed: zero uses of `sa.Enum` in `migrations/versions/*.py`).
- **UUID columns** use bare SQLAlchemy `Uuid` (`from sqlalchemy import Uuid`), Python-side `default=uuid.uuid4`. Never a custom UUID wrapper.
- **Tenant scoping**: every table that can be reached through more than one independent parent carries its own `school_id` plus a composite `ForeignKeyConstraint(["school_id", "<parent>_id"], ["<parent_table>.school_id", "<parent_table>.id"])`, and declares `UniqueConstraint("school_id", "id", name="uq_<table>_school_id_id")` so children can do the same back to it. A table with exactly one parent (e.g. `PromptMaterial` → `EssayPrompt`, `EssaySubmissionPage` → `EssaySubmission`) uses a plain `ForeignKey`, no composite, matching `EssayRubricCompetency` → `EssayRubric`.
- **Naming collision, read this before importing `EssayPrompt`:** `agente_ia_edu.essay_prompts.EssayPrompt` already exists — an unrelated, frozen dataclass for an *AI prompt template artifact* (`src/agente_ia_edu/essay_prompts/__init__.py`), not a database model. This plan's `EssayPrompt` is the *essay topic/proposal* ORM model, in a **different module** (`agente_ia_edu.db.models.essay_proposal`). Never import both names into the same file without an alias. No task in this plan needs both at once, so this should never come up in practice — it is flagged here so nobody "fixes" the ORM model's name later out of confusion.
- **`EssayPrompt.status` reserves `SUPERSEDED` but nothing in this plan produces it.** The spec's column table lists `DRAFT | ACTIVE | SUPERSEDED`; R2's own scope only ever writes `DRAFT` (on creation) and `ACTIVE` (on first `PromptAssignment`). There is no "new version of a prompt" concept in R2 — unlike `EssayRubric`, a prompt is never replaced by another row. `SUPERSEDED` is reserved for whatever future work needs to retire a prompt; it is a valid CHECK value, never a reachable state in this plan's code.
- **`school_settings.transcription_enabled` defaults to `False`.** Ruling made in this plan (the spec left the default unstated): explicit opt-in, matching this codebase's documented preference for schools not silently gaining new capabilities (`institution.py`'s own docstring: typed columns exist specifically so nothing turns on by accident).
- **OCR uses the existing `providers/` package**, extended with a new `EssayTranscriptionProvider` protocol + an `OpenAIProvider.transcribe_page` implementation, following the exact shape `TextGenerationProvider`/`OpenAIProvider.generate` already establishes (config via `OPENAI_API_KEY`/new `OPENAI_VISION_MODEL` env vars, `AI_PROVIDER`-driven factory, a `FakeProvider` test double). Per-token confidence is approximated from the OpenAI chat-completion response's `logprobs` (`confidence = exp(logprob)`) — a documented approximation, not a real OCR confidence score, because no dedicated OCR/vision confidence API is in scope. This is the mechanism that makes the provider "replaceable in the future" (per the user's explicit answer): swapping vendors is a new adapter + one `_BUILDERS`/`_TRANSCRIBER_BUILDERS` entry, exactly like swapping the text-generation backend already is.
- **PDF pages are split server-side** via `pymupdf` (imported as `import pymupdf as _mu` with a `import fitz as _mu` fallback, matching `text_recovery.py`'s existing convention) — one `EssaySubmissionPage` + one rasterized PNG per PDF page. `PHOTO` mode uploads exactly one page per API call; `PDF` mode uploads the whole file in one call and the service splits it.
- **The same `POST /essay-submissions/{id}/confirm` endpoint serves both photo/PDF sub-flows** (§5.2 with transcription, §5.3 without) — behavior branches on `EssaySubmission.anchor_mode`, which is fixed at creation time from `school_settings.transcription_enabled`. No separate endpoint for the no-transcription case.
- **403, never 404, for "not yours"** — the established Fase 3C rule, reused verbatim: a `prompt_assignment_id` from another class (even the same school) is 403. Every route in this plan resolves ownership itself before calling a service method; services never see an id they haven't already been told belongs to the right tenant.
- **Services raise plain `ValueError`** for domain-state violations (mapped to 422 in routes), matching `TheoryMaterialService.create_material`'s existing convention — no new exception hierarchy, except `EssayResubmissionBlockedError` (a distinct `RuntimeError` subclass, mapped to 409, because "already submitted under AVALIATIVO" is a conflict, not a validation error).
- **No new third-party dependencies.** Everything needed (`openai`, `pymupdf`) is already in `pyproject.toml`.
- **Test style**: `unittest.TestCase` / `unittest.IsolatedAsyncioTestCase`, in-memory `sqlite+aiosqlite:///:memory:` + `StaticPool`, `Base.metadata.create_all`. No `pytest` fixtures/markers anywhere in this repo's suite — don't introduce any.

---

### Task 1: `school_settings.transcription_enabled`

**Files:**
- Create: `migrations/versions/047_school_settings_transcription_enabled.py`
- Modify: `src/agente_ia_edu/db/models/institution.py` (add column to `SchoolSetting`)
- Modify: `src/agente_ia_edu/services/institution_settings.py` (add to `configure()`'s whitelist)
- Test: `tests/test_r2_school_settings_transcription_enabled.py`

**Interfaces:**
- Consumes: nothing new.
- Produces: `SchoolSetting.transcription_enabled: bool` (readable via `InstitutionSettingsService.get_settings(school_id)`), settable via `InstitutionSettingsService.configure(school_id, performed_by_external_id=..., transcription_enabled=True/False)`. Task 10 reads this field.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_r2_school_settings_transcription_enabled.py
import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.services.institution_settings import InstitutionSettingsService


class TranscriptionEnabledSettingTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_defaults_to_disabled(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="TR1", name="school-tr1")
            session.add(school)
            await session.commit()

            settings = await InstitutionSettingsService(session).get_settings(school.id)
            self.assertFalse(settings.transcription_enabled)

    async def test_configure_can_enable_it(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="TR2", name="school-tr2")
            session.add(school)
            await session.commit()

            svc = InstitutionSettingsService(session)
            updated = await svc.configure(
                school.id, performed_by_external_id="admin:x", transcription_enabled=True
            )
            self.assertTrue(updated.transcription_enabled)

            fetched = await svc.get_settings(school.id)
            self.assertTrue(fetched.transcription_enabled)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_school_settings_transcription_enabled.py -v`
Expected: FAIL — `TypeError` (`transcription_enabled` is not a valid keyword for `SchoolSetting.__init__` yet) or `sqlalchemy.exc.CompileError`/`AttributeError` on `settings.transcription_enabled`.

- [ ] **Step 3: Add the column to the model**

In `src/agente_ia_edu/db/models/institution.py`, add to `SchoolSetting.__table_args__` (after the existing `ck_school_settings_threshold_range` check) and to the column list:

```python
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
    # R2: whether photo/PDF submissions get OCR transcription for this school.
    # Explicit opt-in (default False) - see this plan's Global Constraints.
    transcription_enabled: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )
    current_identity_version_id: Mapped[uuid.UUID | None] = mapped_column(Uuid)
```

(No change to `__table_args__`'s tuple content itself beyond what's shown — the CHECK constraints already there are untouched; `transcription_enabled` is a plain `NOT NULL` boolean with no CHECK needed.)

- [ ] **Step 4: Write the migration**

```python
# migrations/versions/047_school_settings_transcription_enabled.py
"""R2 - school_settings gains transcription_enabled.

Revision ID: 047_school_settings_transcription_enabled
Revises: 046_practice_sessions_catalog_fk

Purely additive: one nullable-never column on an existing table, backfilled
via server_default so every existing row gets the conservative default
(disabled) without a data migration step.
"""

from alembic import op
import sqlalchemy as sa

revision = "047_school_settings_transcription_enabled"
down_revision = "046_practice_sessions_catalog_fk"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "school_settings",
        sa.Column(
            "transcription_enabled",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("school_settings", "transcription_enabled")
```

- [ ] **Step 5: Add it to `InstitutionSettingsService.configure()`'s whitelist**

In `src/agente_ia_edu/services/institution_settings.py`, change:

```python
        unknown = set(changes) - {
            "correction_mode",
            "validation_default",
            "validation_teacher_can_disable",
            "validation_threshold_points",
        }
```

to:

```python
        unknown = set(changes) - {
            "correction_mode",
            "validation_default",
            "validation_teacher_can_disable",
            "validation_threshold_points",
            "transcription_enabled",
        }
```

`transcription_enabled` needs no extra validation branch — unlike `correction_mode`/`validation_default`, it is a plain boolean with no CHECK constraint and no cross-field coherence rule.

- [ ] **Step 6: Run the migration against the disposable verification DB, then the test**

Run: `cd /Users/marcoviana/agente-ia-edu-core/.claude/worktrees/r0-estrutura-academica && .venv/bin/alembic upgrade head`
Expected: migration `047_school_settings_transcription_enabled` applies cleanly.

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_school_settings_transcription_enabled.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Commit**

```bash
git add migrations/versions/047_school_settings_transcription_enabled.py \
        src/agente_ia_edu/db/models/institution.py \
        src/agente_ia_edu/services/institution_settings.py \
        tests/test_r2_school_settings_transcription_enabled.py
git commit -m "feat(r2): add school_settings.transcription_enabled, default disabled"
```

---

### Task 2: Essay proposal/submission DB models

**Files:**
- Create: `src/agente_ia_edu/db/models/essay_proposal.py`
- Create: `migrations/versions/048_essay_proposal_submission.py`
- Modify: `src/agente_ia_edu/db/models/__init__.py` (register the 5 new classes)
- Test: `tests/test_r2_essay_proposal_models.py`

**Interfaces:**
- Consumes: `Base` (`db/base.py`), `JSONBCompatible`/`Uuid` conventions.
- Produces: `EssayPrompt`, `PromptMaterial`, `PromptAssignment`, `EssaySubmission`, `EssaySubmissionPage` — importable from `agente_ia_edu.db.models` and `agente_ia_edu.db.models.essay_proposal`. Every later task's services/routes import from here.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_r2_essay_proposal_models.py
import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayPrompt,
    EssaySubmission,
    EssaySubmissionPage,
    GradeLevel,
    PromptAssignment,
    PromptMaterial,
    School,
    Segment,
    Student,
)


class EssayProposalModelTests(unittest.IsolatedAsyncioTestCase):
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

    async def _class_and_student(self, session, code):
        school = School(id=uuid.uuid4(), code=f"EP-{code}", name=f"school-{code}")
        session.add(school)
        await session.flush()
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
        student = Student(id=uuid.uuid4(), school_id=school.id, person_id=uuid.uuid4(), student_code=f"ST-{code}")
        session.add(student)
        await session.commit()
        return school, klass, student

    async def test_full_chain_round_trips(self):
        async with self.session_factory() as session:
            school, klass, student = await self._class_and_student(session, "1")

            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="Tema X",
                statement="Disserte sobre X.", year=2026,
                created_by_external_identity="teacher:prof1",
            )
            session.add(prompt)
            await session.flush()
            self.assertEqual(prompt.status, "DRAFT")

            material = PromptMaterial(
                id=uuid.uuid4(), essay_prompt_id=prompt.id, material_type="TEXT",
                content="Texto motivador.", position=0,
            )
            session.add(material)

            assignment = PromptAssignment(
                id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                class_id=klass.id, assigned_by_external_identity="teacher:prof1",
            )
            session.add(assignment)
            await session.flush()
            self.assertEqual(assignment.status, "OPEN")
            self.assertTrue(assignment.validation_enabled)

            submission = EssaySubmission(
                id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                prompt_assignment_id=assignment.id, student_id=student.id,
                mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                canonical_text="Redacao completa.", normalized_text_hash="a" * 64,
                submitted_at=datetime.now(timezone.utc),
            )
            session.add(submission)
            await session.commit()

            fetched = await session.get(EssaySubmission, submission.id)
            self.assertEqual(fetched.mode, "TYPED")
            self.assertEqual(fetched.status, "SUBMITTED")

    async def test_essay_submission_page_requires_positive_page_number(self):
        async with self.session_factory() as session:
            school, klass, student = await self._class_and_student(session, "2")
            prompt = EssayPrompt(
                id=uuid.uuid4(), school_id=school.id, title="T", statement="S", year=2026,
                created_by_external_identity="teacher:p",
            )
            session.add(prompt)
            await session.flush()
            assignment = PromptAssignment(
                id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                class_id=klass.id, assigned_by_external_identity="teacher:p",
            )
            session.add(assignment)
            await session.flush()
            submission = EssaySubmission(
                id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                prompt_assignment_id=assignment.id, student_id=student.id,
                mode="PHOTO", anchor_mode="IMAGE_REGION", status="PENDING_TRANSCRIPTION",
            )
            session.add(submission)
            await session.flush()

            page = EssaySubmissionPage(
                id=uuid.uuid4(), essay_submission_id=submission.id,
                page_number=0, storage_uri="var/x.png",
            )
            session.add(page)
            with self.assertRaises(Exception):
                await session.flush()


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_proposal_models.py -v`
Expected: FAIL — `ImportError: cannot import name 'EssayPrompt' from 'agente_ia_edu.db.models'`.

- [ ] **Step 3: Write the models**

```python
# src/agente_ia_edu/db/models/essay_proposal.py
"""R2 - essay proposal (topic) and submission.

Five additive tables. See docs/superpowers/specs/2026-09-21-r2-propostas-
envio-redacao-design.md for the full design.

NAMING NOTE: ``agente_ia_edu.essay_prompts.EssayPrompt`` is a different,
unrelated thing - a frozen dataclass for an AI prompt-template artifact
(R1). The ``EssayPrompt`` here is the essay TOPIC/proposal ORM model. Never
import both into the same module without an alias.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Float,
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
from ..types import JSONBCompatible


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EssayPrompt(Base):
    """A redação proposal/topic. Immutable once it leaves DRAFT (i.e. once the
    first PromptAssignment exists) - same "never edit, always supersede"
    family as EssayRubric, though R2 never actually produces a SUPERSEDED row
    (see this plan's Global Constraints)."""

    __tablename__ = "essay_prompts"
    __table_args__ = (
        UniqueConstraint("school_id", "id", name="uq_essay_prompts_school_id_id"),
        CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED')", name="ck_essay_prompts_status"
        ),
        Index("ix_essay_prompts_school_id", "school_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("schools.id", ondelete="RESTRICT"), nullable=False
    )
    title: Mapped[str] = mapped_column(String(255), nullable=False)
    statement: Mapped[str] = mapped_column(Text, nullable=False)
    year: Mapped[int] = mapped_column(Integer, nullable=False)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="DRAFT")
    created_by_external_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    materials: Mapped[list["PromptMaterial"]] = relationship(back_populates="prompt")


class PromptMaterial(Base):
    """Support material for a proposal. Single parent (EssayPrompt), so a
    plain FK is enough - no separate school_id, matching
    EssayRubricCompetency -> EssayRubric."""

    __tablename__ = "prompt_materials"
    __table_args__ = (
        UniqueConstraint("essay_prompt_id", "position", name="uq_prompt_materials_position"),
        CheckConstraint("material_type IN ('TEXT', 'IMAGE')", name="ck_prompt_materials_type"),
        CheckConstraint(
            "(material_type = 'TEXT') = (content IS NOT NULL)",
            name="ck_prompt_materials_text_has_content",
        ),
        CheckConstraint(
            "(material_type = 'IMAGE') = (storage_uri IS NOT NULL)",
            name="ck_prompt_materials_image_has_storage_uri",
        ),
        Index("ix_prompt_materials_essay_prompt_id", "essay_prompt_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    essay_prompt_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("essay_prompts.id", ondelete="RESTRICT"), nullable=False
    )
    material_type: Mapped[str] = mapped_column(String(10), nullable=False)
    content: Mapped[str | None] = mapped_column(Text)
    storage_uri: Mapped[str | None] = mapped_column(String(1024))
    position: Mapped[int] = mapped_column(Integer, nullable=False, default=0)

    prompt: Mapped["EssayPrompt"] = relationship(back_populates="materials")


class PromptAssignment(Base):
    """Assigns a proposal to a class. Authorization for essay submission
    checks against this table directly - a student only submits to a
    proposal their own class actually received."""

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
        UniqueConstraint("school_id", "id", name="uq_prompt_assignments_school_id_id"),
        UniqueConstraint(
            "essay_prompt_id", "class_id", name="uq_prompt_assignments_prompt_class"
        ),
        CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_prompt_assignments_status"),
        Index("ix_prompt_assignments_school_id", "school_id"),
        Index("ix_prompt_assignments_essay_prompt_id", "essay_prompt_id"),
        Index("ix_prompt_assignments_class_id", "class_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    essay_prompt_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    class_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    assigned_by_external_identity: Mapped[str] = mapped_column(String(255), nullable=False)
    due_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    validation_enabled: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    status: Mapped[str] = mapped_column(String(20), nullable=False, default="OPEN")
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )


class EssaySubmission(Base):
    """One submission/version of a student's essay for a given assignment.
    ``essay_id`` groups reenvios of the same (student, assignment); each row
    is an ``essay_version_id`` - the exact two names
    essay_engine_contract.v1.Identification already reserves."""

    __tablename__ = "essay_submissions"
    __table_args__ = (
        ForeignKeyConstraint(
            ["school_id", "prompt_assignment_id"],
            ["prompt_assignments.school_id", "prompt_assignments.id"],
            ondelete="RESTRICT",
            name="fk_essay_submissions_school_assignment",
        ),
        ForeignKeyConstraint(
            ["school_id", "student_id"],
            ["students.school_id", "students.id"],
            ondelete="RESTRICT",
            name="fk_essay_submissions_school_student",
        ),
        CheckConstraint("mode IN ('TYPED', 'PHOTO', 'PDF')", name="ck_essay_submissions_mode"),
        CheckConstraint(
            "anchor_mode IN ('TEXT_OFFSET', 'IMAGE_REGION')",
            name="ck_essay_submissions_anchor_mode",
        ),
        CheckConstraint(
            "status IN ('PENDING_TRANSCRIPTION', 'PENDING_CONFIRMATION', 'SUBMITTED', 'SUPERSEDED')",
            name="ck_essay_submissions_status",
        ),
        CheckConstraint(
            "(canonical_text IS NULL) = (normalized_text_hash IS NULL)",
            name="ck_essay_submissions_canonical_text_hash_paired",
        ),
        CheckConstraint(
            "canonical_text IS NULL OR anchor_mode = 'TEXT_OFFSET'",
            name="ck_essay_submissions_canonical_text_requires_text_offset",
        ),
        CheckConstraint(
            "(status IN ('SUBMITTED', 'SUPERSEDED')) = (submitted_at IS NOT NULL)",
            name="ck_essay_submissions_submitted_at_presence",
        ),
        Index("ix_essay_submissions_school_id", "school_id"),
        Index("ix_essay_submissions_prompt_assignment_id", "prompt_assignment_id"),
        Index("ix_essay_submissions_student_id", "student_id"),
        Index("ix_essay_submissions_essay_id", "essay_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    essay_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    school_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    prompt_assignment_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    student_id: Mapped[uuid.UUID] = mapped_column(Uuid, nullable=False)
    mode: Mapped[str] = mapped_column(String(10), nullable=False)
    anchor_mode: Mapped[str] = mapped_column(String(20), nullable=False)
    status: Mapped[str] = mapped_column(
        String(30), nullable=False, default="PENDING_TRANSCRIPTION"
    )
    canonical_text: Mapped[str | None] = mapped_column(Text)
    normalized_text_hash: Mapped[str | None] = mapped_column(String(64))
    submitted_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, default=_utcnow, onupdate=_utcnow
    )

    pages: Mapped[list["EssaySubmissionPage"]] = relationship(back_populates="submission")


class EssaySubmissionPage(Base):
    """One page, for PHOTO/PDF modes. Single parent (EssaySubmission), plain
    FK - same reasoning as PromptMaterial."""

    __tablename__ = "essay_submission_pages"
    __table_args__ = (
        UniqueConstraint(
            "essay_submission_id", "page_number", name="uq_essay_submission_pages_number"
        ),
        CheckConstraint(
            "page_number >= 1", name="ck_essay_submission_pages_page_number_positive"
        ),
        Index("ix_essay_submission_pages_essay_submission_id", "essay_submission_id"),
    )

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    essay_submission_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("essay_submissions.id", ondelete="RESTRICT"), nullable=False
    )
    page_number: Mapped[int] = mapped_column(Integer, nullable=False)
    storage_uri: Mapped[str] = mapped_column(String(1024), nullable=False)
    width: Mapped[float | None] = mapped_column(Float)
    height: Mapped[float | None] = mapped_column(Float)
    ocr_tokens: Mapped[list[dict[str, Any]] | None] = mapped_column(JSONBCompatible)
    reviewed_text: Mapped[str | None] = mapped_column(Text)

    submission: Mapped["EssaySubmission"] = relationship(back_populates="pages")
```

- [ ] **Step 4: Write the migration**

```python
# migrations/versions/048_essay_proposal_submission.py
"""R2 - essay proposal and submission foundation.

Revision ID: 048_essay_proposal_submission
Revises: 047_school_settings_transcription_enabled

Purely additive: five new tables, touches zero rows in any existing table.
"""

from alembic import op
import sqlalchemy as sa

revision = "048_essay_proposal_submission"
down_revision = "047_school_settings_transcription_enabled"
branch_labels = None
depends_on = None

_JSON = sa.JSON().with_variant(sa.dialects.postgresql.JSONB(), "postgresql")


def upgrade() -> None:
    op.create_table(
        "essay_prompts",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("title", sa.String(255), nullable=False),
        sa.Column("statement", sa.Text(), nullable=False),
        sa.Column("year", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(20), nullable=False, server_default="DRAFT"),
        sa.Column("created_by_external_identity", sa.String(255), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(["school_id"], ["schools.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint("school_id", "id", name="uq_essay_prompts_school_id_id"),
        sa.CheckConstraint(
            "status IN ('DRAFT', 'ACTIVE', 'SUPERSEDED')", name="ck_essay_prompts_status"
        ),
    )
    op.create_index("ix_essay_prompts_school_id", "essay_prompts", ["school_id"])

    op.create_table(
        "prompt_materials",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("essay_prompt_id", sa.Uuid(), nullable=False),
        sa.Column("material_type", sa.String(10), nullable=False),
        sa.Column("content", sa.Text()),
        sa.Column("storage_uri", sa.String(1024)),
        sa.Column("position", sa.Integer(), nullable=False, server_default="0"),
        sa.ForeignKeyConstraint(["essay_prompt_id"], ["essay_prompts.id"], ondelete="RESTRICT"),
        sa.UniqueConstraint(
            "essay_prompt_id", "position", name="uq_prompt_materials_position"
        ),
        sa.CheckConstraint("material_type IN ('TEXT', 'IMAGE')", name="ck_prompt_materials_type"),
        sa.CheckConstraint(
            "(material_type = 'TEXT') = (content IS NOT NULL)",
            name="ck_prompt_materials_text_has_content",
        ),
        sa.CheckConstraint(
            "(material_type = 'IMAGE') = (storage_uri IS NOT NULL)",
            name="ck_prompt_materials_image_has_storage_uri",
        ),
    )
    op.create_index(
        "ix_prompt_materials_essay_prompt_id", "prompt_materials", ["essay_prompt_id"]
    )

    op.create_table(
        "prompt_assignments",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("essay_prompt_id", sa.Uuid(), nullable=False),
        sa.Column("class_id", sa.Uuid(), nullable=False),
        sa.Column("assigned_by_external_identity", sa.String(255), nullable=False),
        sa.Column("due_at", sa.DateTime(timezone=True)),
        sa.Column("validation_enabled", sa.Boolean(), nullable=False, server_default=sa.true()),
        sa.Column("status", sa.String(20), nullable=False, server_default="OPEN"),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["school_id", "essay_prompt_id"],
            ["essay_prompts.school_id", "essay_prompts.id"],
            ondelete="RESTRICT",
            name="fk_prompt_assignments_school_prompt",
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "class_id"],
            ["classes.school_id", "classes.id"],
            ondelete="RESTRICT",
            name="fk_prompt_assignments_school_class",
        ),
        sa.UniqueConstraint("school_id", "id", name="uq_prompt_assignments_school_id_id"),
        sa.UniqueConstraint(
            "essay_prompt_id", "class_id", name="uq_prompt_assignments_prompt_class"
        ),
        sa.CheckConstraint("status IN ('OPEN', 'CLOSED')", name="ck_prompt_assignments_status"),
    )
    op.create_index("ix_prompt_assignments_school_id", "prompt_assignments", ["school_id"])
    op.create_index(
        "ix_prompt_assignments_essay_prompt_id", "prompt_assignments", ["essay_prompt_id"]
    )
    op.create_index("ix_prompt_assignments_class_id", "prompt_assignments", ["class_id"])

    op.create_table(
        "essay_submissions",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("essay_id", sa.Uuid(), nullable=False),
        sa.Column("school_id", sa.Uuid(), nullable=False),
        sa.Column("prompt_assignment_id", sa.Uuid(), nullable=False),
        sa.Column("student_id", sa.Uuid(), nullable=False),
        sa.Column("mode", sa.String(10), nullable=False),
        sa.Column("anchor_mode", sa.String(20), nullable=False),
        sa.Column(
            "status", sa.String(30), nullable=False, server_default="PENDING_TRANSCRIPTION"
        ),
        sa.Column("canonical_text", sa.Text()),
        sa.Column("normalized_text_hash", sa.String(64)),
        sa.Column("submitted_at", sa.DateTime(timezone=True)),
        sa.Column("created_at", sa.DateTime(timezone=True), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), nullable=False),
        sa.ForeignKeyConstraint(
            ["school_id", "prompt_assignment_id"],
            ["prompt_assignments.school_id", "prompt_assignments.id"],
            ondelete="RESTRICT",
            name="fk_essay_submissions_school_assignment",
        ),
        sa.ForeignKeyConstraint(
            ["school_id", "student_id"],
            ["students.school_id", "students.id"],
            ondelete="RESTRICT",
            name="fk_essay_submissions_school_student",
        ),
        sa.CheckConstraint("mode IN ('TYPED', 'PHOTO', 'PDF')", name="ck_essay_submissions_mode"),
        sa.CheckConstraint(
            "anchor_mode IN ('TEXT_OFFSET', 'IMAGE_REGION')",
            name="ck_essay_submissions_anchor_mode",
        ),
        sa.CheckConstraint(
            "status IN ('PENDING_TRANSCRIPTION', 'PENDING_CONFIRMATION', 'SUBMITTED', 'SUPERSEDED')",
            name="ck_essay_submissions_status",
        ),
        sa.CheckConstraint(
            "(canonical_text IS NULL) = (normalized_text_hash IS NULL)",
            name="ck_essay_submissions_canonical_text_hash_paired",
        ),
        sa.CheckConstraint(
            "canonical_text IS NULL OR anchor_mode = 'TEXT_OFFSET'",
            name="ck_essay_submissions_canonical_text_requires_text_offset",
        ),
        sa.CheckConstraint(
            "(status IN ('SUBMITTED', 'SUPERSEDED')) = (submitted_at IS NOT NULL)",
            name="ck_essay_submissions_submitted_at_presence",
        ),
    )
    op.create_index("ix_essay_submissions_school_id", "essay_submissions", ["school_id"])
    op.create_index(
        "ix_essay_submissions_prompt_assignment_id",
        "essay_submissions",
        ["prompt_assignment_id"],
    )
    op.create_index("ix_essay_submissions_student_id", "essay_submissions", ["student_id"])
    op.create_index("ix_essay_submissions_essay_id", "essay_submissions", ["essay_id"])

    op.create_table(
        "essay_submission_pages",
        sa.Column("id", sa.Uuid(), primary_key=True),
        sa.Column("essay_submission_id", sa.Uuid(), nullable=False),
        sa.Column("page_number", sa.Integer(), nullable=False),
        sa.Column("storage_uri", sa.String(1024), nullable=False),
        sa.Column("width", sa.Float()),
        sa.Column("height", sa.Float()),
        sa.Column("ocr_tokens", _JSON),
        sa.Column("reviewed_text", sa.Text()),
        sa.ForeignKeyConstraint(
            ["essay_submission_id"], ["essay_submissions.id"], ondelete="RESTRICT"
        ),
        sa.UniqueConstraint(
            "essay_submission_id", "page_number", name="uq_essay_submission_pages_number"
        ),
        sa.CheckConstraint(
            "page_number >= 1", name="ck_essay_submission_pages_page_number_positive"
        ),
    )
    op.create_index(
        "ix_essay_submission_pages_essay_submission_id",
        "essay_submission_pages",
        ["essay_submission_id"],
    )


def downgrade() -> None:
    op.drop_table("essay_submission_pages")
    op.drop_table("essay_submissions")
    op.drop_table("prompt_assignments")
    op.drop_table("prompt_materials")
    op.drop_table("essay_prompts")
```

- [ ] **Step 5: Register the models in `db/models/__init__.py`**

Add the import block (after the existing `from .institution import SchoolIdentityVersion, SchoolSetting` line):

```python
from .institution import SchoolIdentityVersion, SchoolSetting
from .essay_proposal import (
    EssayPrompt,
    PromptMaterial,
    PromptAssignment,
    EssaySubmission,
    EssaySubmissionPage,
)
```

Add to `__all__` (after `"SchoolSetting",`):

```python
    "SchoolSetting",
    "EssayPrompt",
    "PromptMaterial",
    "PromptAssignment",
    "EssaySubmission",
    "EssaySubmissionPage",
```

- [ ] **Step 6: Run the migration, then the test**

Run: `.venv/bin/alembic upgrade head`
Expected: migration `048_essay_proposal_submission` applies cleanly on top of `047`.

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_proposal_models.py -v`
Expected: PASS (2 tests).

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/db/models/essay_proposal.py \
        src/agente_ia_edu/db/models/__init__.py \
        migrations/versions/048_essay_proposal_submission.py \
        tests/test_r2_essay_proposal_models.py
git commit -m "feat(r2): add EssayPrompt/PromptMaterial/PromptAssignment/EssaySubmission/EssaySubmissionPage models"
```

---

### Task 3: OCR provider (`EssayTranscriptionProvider`)

Extends the existing `providers/` package (`contracts.py`/`models.py`/`adapters/openai.py`/`adapters/fake.py`/`factory.py`) the same way it already supports text generation, per this plan's Global Constraints.

**Files:**
- Modify: `src/agente_ia_edu/providers/models.py`
- Modify: `src/agente_ia_edu/providers/contracts.py`
- Modify: `src/agente_ia_edu/providers/adapters/openai.py`
- Modify: `src/agente_ia_edu/providers/adapters/fake.py`
- Modify: `src/agente_ia_edu/providers/factory.py`
- Test: `tests/test_r2_essay_transcription_provider.py`

**Interfaces:**
- Consumes: `ProviderConfigurationError`/`ProviderInvalidResponseError` (existing, `providers/errors.py`).
- Produces: `EssayPageTranscriptionRequest(image_path: Path, mime_type: str)`, `EssayOcrToken(text, confidence, start, end)`, `EssayPageTranscriptionResult(tokens: tuple[EssayOcrToken, ...], provider: str, model: str)`, `EssayTranscriptionProvider` protocol (`async def transcribe_page(request) -> EssayPageTranscriptionResult`), `build_essay_transcriber(name: str | None = None) -> EssayTranscriptionProvider`. Task 10 (`EssaySubmissionService.upload_page`) is the consumer.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_r2_essay_transcription_provider.py
import asyncio
import math
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock

from agente_ia_edu.providers.adapters.fake import FakeProvider
from agente_ia_edu.providers.adapters.openai import OpenAIProvider
from agente_ia_edu.providers.errors import ProviderConfigurationError
from agente_ia_edu.providers.factory import build_essay_transcriber
from agente_ia_edu.providers.models import EssayPageTranscriptionRequest


class FakeProviderTranscriptionTests(unittest.TestCase):
    def test_returns_deterministic_tokens_for_the_same_path(self):
        provider = FakeProvider()
        request = EssayPageTranscriptionRequest(
            image_path=Path("/tmp/page1.png"), mime_type="image/png"
        )
        result1 = asyncio.run(provider.transcribe_page(request))
        result2 = asyncio.run(provider.transcribe_page(request))
        self.assertEqual(result1.tokens, result2.tokens)
        self.assertGreater(len(result1.tokens), 0)
        for token in result1.tokens:
            self.assertTrue(token.text)
            self.assertTrue(0.0 <= token.confidence <= 1.0)


class OpenAIProviderTranscriptionTests(unittest.TestCase):
    def test_raises_when_not_configured(self):
        provider = OpenAIProvider(api_key=None, vision_model=None)
        request = EssayPageTranscriptionRequest(
            image_path=Path("/tmp/page1.png"), mime_type="image/png"
        )
        with self.assertRaises(ProviderConfigurationError):
            asyncio.run(provider.transcribe_page(request))

    def test_derives_confidence_from_logprobs(self):
        image_path = Path("/tmp/r2_test_page.png")
        image_path.write_bytes(b"\x89PNG\r\n\x1a\nfake")

        fake_client = SimpleNamespace()
        fake_client.chat = SimpleNamespace()

        async def _create(**kwargs):
            return SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        message=SimpleNamespace(content="ola mundo"),
                        logprobs=SimpleNamespace(
                            content=[
                                SimpleNamespace(token="ola", logprob=0.0),
                                SimpleNamespace(token=" mundo", logprob=math.log(0.5)),
                            ]
                        ),
                    )
                ]
            )

        fake_client.chat.completions = SimpleNamespace(create=_create)

        provider = OpenAIProvider(
            api_key="sk-test", vision_model="gpt-4o-mini", client=fake_client
        )
        request = EssayPageTranscriptionRequest(image_path=image_path, mime_type="image/png")
        result = asyncio.run(provider.transcribe_page(request))

        self.assertEqual(len(result.tokens), 2)
        self.assertEqual(result.tokens[0].text, "ola")
        self.assertAlmostEqual(result.tokens[0].confidence, 1.0, places=4)
        self.assertEqual(result.tokens[1].text, " mundo")
        self.assertAlmostEqual(result.tokens[1].confidence, 0.5, places=4)
        image_path.unlink(missing_ok=True)


class FactoryTests(unittest.TestCase):
    def test_build_essay_transcriber_requires_configuration(self):
        with self.assertRaises(ProviderConfigurationError):
            build_essay_transcriber("openai")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_transcription_provider.py -v`
Expected: FAIL — `ImportError: cannot import name 'EssayPageTranscriptionRequest' from 'agente_ia_edu.providers.models'`.

- [ ] **Step 3: Add the request/result dataclasses**

In `src/agente_ia_edu/providers/models.py`, add `from pathlib import Path` to the top imports and append:

```python
@dataclass(frozen=True)
class EssayPageTranscriptionRequest:
    image_path: Path
    mime_type: str


@dataclass(frozen=True)
class EssayOcrToken:
    text: str
    confidence: float
    start: int
    end: int


@dataclass(frozen=True)
class EssayPageTranscriptionResult:
    tokens: tuple[EssayOcrToken, ...]
    provider: str
    model: str
```

- [ ] **Step 4: Add the protocol**

In `src/agente_ia_edu/providers/contracts.py`, add the import and the protocol:

```python
from .models import (
    EmbeddingRequest,
    EmbeddingResult,
    EssayPageTranscriptionRequest,
    EssayPageTranscriptionResult,
    TextGenerationRequest,
    TextGenerationResult,
)


@runtime_checkable
class EssayTranscriptionProvider(Protocol):
    async def transcribe_page(
        self, request: EssayPageTranscriptionRequest
    ) -> EssayPageTranscriptionResult:
        """Transcribe one essay page image into raw, unedited text tokens -
        never suggesting spelling/grammar correction."""
```

- [ ] **Step 5: Implement `OpenAIProvider.transcribe_page`**

In `src/agente_ia_edu/providers/adapters/openai.py`, add `import base64` and `import math` to the top imports, extend the constructor, and add the new method plus its private helper:

```python
import base64
import math
import os
import re

from ..errors import (
    ProviderAuthenticationError,
    ProviderConfigurationError,
    ProviderInvalidResponseError,
    ProviderRateLimitError,
    ProviderTimeoutError,
    ProviderUnavailableError,
)
from ..models import (
    EssayOcrToken,
    EssayPageTranscriptionRequest,
    EssayPageTranscriptionResult,
    TextGenerationRequest,
    TextGenerationResult,
)


class OpenAIProvider:
    provider = "openai"

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str | None = None,
        vision_model: str | None = None,
        timeout_seconds: float | None = None,
        client=None,
    ):
        self._api_key = api_key or os.getenv("OPENAI_API_KEY")
        self._model = model or os.getenv("OPENAI_MODEL")
        self._vision_model = vision_model or os.getenv("OPENAI_VISION_MODEL")
        self._timeout_seconds = timeout_seconds or float(os.getenv("OPENAI_TIMEOUT_SECONDS", "30"))
        self._client = client

    # ... existing generate()/_create_client()/_map_error()/_sanitize_error_message() unchanged ...

    async def transcribe_page(
        self, request: EssayPageTranscriptionRequest
    ) -> EssayPageTranscriptionResult:
        if not self._api_key:
            raise ProviderConfigurationError("OpenAI is not configured")
        if not self._vision_model:
            raise ProviderConfigurationError("OpenAI vision model is not configured")
        try:
            client = self._client or self._create_client()
            image_b64 = base64.b64encode(request.image_path.read_bytes()).decode("ascii")
            response = await client.chat.completions.create(
                model=self._vision_model,
                messages=[
                    {
                        "role": "system",
                        "content": (
                            "Transcreva literalmente o texto manuscrito ou impresso na "
                            "imagem, palavra por palavra, na ordem em que aparece. Nao "
                            "corrija ortografia, gramatica ou concordancia - reproduza "
                            "exatamente o que esta escrito, mesmo que contenha erros. "
                            "Nao adicione nenhum texto que nao esteja na imagem."
                        ),
                    },
                    {
                        "role": "user",
                        "content": [
                            {
                                "type": "image_url",
                                "image_url": {
                                    "url": f"data:{request.mime_type};base64,{image_b64}"
                                },
                            }
                        ],
                    },
                ],
                logprobs=True,
                top_logprobs=1,
                timeout=self._timeout_seconds,
            )
            choice = response.choices[0]
            content = choice.message.content
            if not content:
                raise ProviderInvalidResponseError("OpenAI returned an empty transcription")
            tokens = self._tokens_from_logprobs(content, choice.logprobs)
            return EssayPageTranscriptionResult(
                tokens=tokens, provider=self.provider, model=self._vision_model
            )
        except ProviderInvalidResponseError:
            raise
        except Exception as exc:
            raise self._map_error(exc) from exc

    def _tokens_from_logprobs(self, content: str, logprobs) -> tuple[EssayOcrToken, ...]:
        """Approximates per-token confidence from the model's own generation
        logprobs (confidence = exp(logprob)). This is NOT a real OCR
        confidence score - it is a documented approximation, the best signal
        available without a dedicated vision/OCR confidence API. When the
        response carries no logprob data at all, every token gets full
        confidence rather than an invented number, so a missing signal never
        masquerades as a low-confidence flag the student has to review."""
        if logprobs is None or not getattr(logprobs, "content", None):
            return (EssayOcrToken(text=content, confidence=1.0, start=0, end=len(content)),)
        tokens: list[EssayOcrToken] = []
        cursor = 0
        for entry in logprobs.content:
            piece = entry.token
            idx = content.find(piece, cursor)
            if idx == -1:
                continue
            start, end = idx, idx + len(piece)
            confidence = math.exp(entry.logprob)
            tokens.append(EssayOcrToken(text=piece, confidence=confidence, start=start, end=end))
            cursor = end
        return tuple(tokens)
```

- [ ] **Step 6: Implement `FakeProvider.transcribe_page`**

In `src/agente_ia_edu/providers/adapters/fake.py`, add the import and method:

```python
from ..models import (
    EmbeddingArtifact,
    EmbeddingRequest,
    EmbeddingResult,
    EssayOcrToken,
    EssayPageTranscriptionRequest,
    EssayPageTranscriptionResult,
    TextGenerationRequest,
    TextGenerationResult,
)


class FakeProvider:
    # ... existing attributes/generate()/embed()/_build_artifact() unchanged ...

    async def transcribe_page(
        self, request: EssayPageTranscriptionRequest
    ) -> EssayPageTranscriptionResult:
        digest = hashlib.sha256(str(request.image_path).encode("utf-8")).hexdigest()
        text = f"texto de teste {digest[:8]}"
        words = text.split(" ")
        tokens = []
        cursor = 0
        for index, word in enumerate(words):
            start = text.index(word, cursor)
            end = start + len(word)
            confidence = 0.5 if index == len(words) - 1 else 0.95
            tokens.append(EssayOcrToken(text=word, confidence=confidence, start=start, end=end))
            cursor = end
        return EssayPageTranscriptionResult(
            tokens=tuple(tokens), provider=self.provider, model="fake-vision-v1"
        )
```

- [ ] **Step 7: Add the factory function**

In `src/agente_ia_edu/providers/factory.py`, add the import and the new builder/factory pair (after the existing `build_text_provider` function):

```python
from .contracts import EssayTranscriptionProvider, TextGenerationProvider


def _build_openai_transcriber() -> EssayTranscriptionProvider:
    from .adapters.openai import OpenAIProvider

    api_key = os.getenv("OPENAI_API_KEY")
    vision_model = os.getenv("OPENAI_VISION_MODEL")
    if not api_key:
        raise ProviderConfigurationError(
            "AI_PROVIDER=openai but OPENAI_API_KEY is not configured"
        )
    if not vision_model:
        raise ProviderConfigurationError(
            "AI_PROVIDER=openai but OPENAI_VISION_MODEL is not configured"
        )
    return OpenAIProvider(api_key=api_key, vision_model=vision_model)


# name -> builder returning a single EssayTranscriptionProvider.
# Same extension story as _BUILDERS above: a new vendor is one adapter + one
# entry here, no call-site change (this is what "replaceable in the future"
# means in this codebase).
_TRANSCRIBER_BUILDERS: dict[str, Callable[[], EssayTranscriptionProvider]] = {
    "openai": _build_openai_transcriber,
}


def build_essay_transcriber(name: str | None = None) -> EssayTranscriptionProvider:
    """Build the configured essay-page transcription provider.

    ``name`` overrides ``AI_PROVIDER`` (tests / explicit callers). Raises
    :class:`ProviderConfigurationError` when the selected backend's required
    configuration is missing.
    """
    selected = (name or os.getenv("AI_PROVIDER") or DEFAULT_PROVIDER).strip().lower()
    builder = _TRANSCRIBER_BUILDERS.get(selected)
    if builder is None:
        raise ProviderConfigurationError(
            f"Unsupported AI_PROVIDER {selected!r} for essay transcription; "
            f"supported: {sorted(_TRANSCRIBER_BUILDERS)}"
        )
    return builder()
```

- [ ] **Step 8: Run the test**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_transcription_provider.py -v`
Expected: PASS (4 tests). Note: `test_build_essay_transcriber_requires_configuration` must run in an environment where `OPENAI_API_KEY`/`OPENAI_VISION_MODEL` are unset — if your shell exports them, run with `env -u OPENAI_API_KEY -u OPENAI_VISION_MODEL -u AI_PROVIDER PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_transcription_provider.py -v` instead.

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/providers/models.py \
        src/agente_ia_edu/providers/contracts.py \
        src/agente_ia_edu/providers/adapters/openai.py \
        src/agente_ia_edu/providers/adapters/fake.py \
        src/agente_ia_edu/providers/factory.py \
        tests/test_r2_essay_transcription_provider.py
git commit -m "feat(r2): add EssayTranscriptionProvider (OpenAI vision + logprobs-derived confidence, fake for tests)"
```

---

### Task 4: `resolve_active_enrollment` — the identity→enrollment resolver (spec §4)

**Files:**
- Create: `src/agente_ia_edu/services/student_enrollment_resolution.py`
- Test: `tests/test_r2_student_enrollment_resolution.py`

**Interfaces:**
- Consumes: `agente_ia_edu.db.models.{User, Student, StudentEnrollment}` (unmodified, R0).
- Produces: `async def resolve_active_enrollment(session, *, school_id: uuid.UUID, external_user_id: str) -> StudentEnrollment | None`. Consumed by Task 14's submission routes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_r2_student_enrollment_resolution.py
import unittest
import uuid

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
    Student,
    StudentEnrollment,
    User,
)
from agente_ia_edu.services.student_enrollment_resolution import resolve_active_enrollment


class ResolveActiveEnrollmentTests(unittest.IsolatedAsyncioTestCase):
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

    async def _class_(self, session, school, code):
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
        await session.commit()
        return klass

    async def _full_academic_identity(self, session, school, klass, external_user_id, code):
        person = Person(id=uuid.uuid4(), school_id=school.id, full_name=f"Aluno {code}")
        session.add(person)
        await session.flush()
        user = User(
            id=uuid.uuid4(), school_id=school.id, person_id=person.id,
            external_identity_provider="test", external_user_id=external_user_id,
        )
        session.add(user)
        student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id, student_code=f"ST-{code}")
        session.add(student)
        await session.flush()
        enrollment = StudentEnrollment(
            id=uuid.uuid4(), school_id=school.id, student_id=student.id, class_id=klass.id,
            status="ACTIVE",
        )
        session.add(enrollment)
        await session.commit()
        return student, enrollment

    async def test_resolves_the_real_chain(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="RE1", name="school-re1")
            session.add(school)
            await session.flush()
            klass = await self._class_(session, school, "1")
            student, enrollment = await self._full_academic_identity(
                session, school, klass, "aluno1", "1"
            )

            resolved = await resolve_active_enrollment(
                session, school_id=school.id, external_user_id="aluno1"
            )
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.id, enrollment.id)
            self.assertEqual(resolved.class_id, klass.id)
            self.assertEqual(resolved.student_id, student.id)

    async def test_no_user_row_returns_none(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="RE2", name="school-re2")
            session.add(school)
            await session.commit()

            resolved = await resolve_active_enrollment(
                session, school_id=school.id, external_user_id="ghost"
            )
            self.assertIsNone(resolved)

    async def test_user_without_enrollment_returns_none(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="RE3", name="school-re3")
            session.add(school)
            await session.flush()
            person = Person(id=uuid.uuid4(), school_id=school.id, full_name="Sem Turma")
            session.add(person)
            await session.flush()
            user = User(
                id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                external_identity_provider="test", external_user_id="sem_turma",
            )
            session.add(user)
            student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id, student_code="ST-3")
            session.add(student)
            await session.commit()

            resolved = await resolve_active_enrollment(
                session, school_id=school.id, external_user_id="sem_turma"
            )
            self.assertIsNone(resolved)

    async def test_wrong_school_returns_none(self):
        async with self.session_factory() as session:
            school_a = School(id=uuid.uuid4(), code="RE4A", name="school-re4a")
            school_b = School(id=uuid.uuid4(), code="RE4B", name="school-re4b")
            session.add_all([school_a, school_b])
            await session.flush()
            klass = await self._class_(session, school_a, "4")
            await self._full_academic_identity(session, school_a, klass, "aluno4", "4")

            resolved = await resolve_active_enrollment(
                session, school_id=school_b.id, external_user_id="aluno4"
            )
            self.assertIsNone(resolved)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_student_enrollment_resolution.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.services.student_enrollment_resolution'`.

- [ ] **Step 3: Write the resolver**

```python
# src/agente_ia_edu/services/student_enrollment_resolution.py
"""R2 - the identity->enrollment resolver spec §4 says nothing in this
repository computes yet: (school_id, external_user_id) -> User -> Person ->
Student -> the student's real, ACTIVE StudentEnrollment.

This is the piece that turns "a request claims to be a student" into "this
student's own class_id", which essay-submission authorization (Task 14)
depends on completely - without it there is no way to check that a
submission's prompt_assignment_id belongs to the caller's own class.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Student, StudentEnrollment, User


async def resolve_active_enrollment(
    session: AsyncSession, *, school_id: uuid.UUID, external_user_id: str
) -> StudentEnrollment | None:
    """Return the caller's own, real, ACTIVE enrollment in ``school_id``, or
    None if any link in the chain is missing. Never raises for a missing
    link - a None here is a normal "not enrolled" outcome the caller (a
    route) turns into 403, not a bug."""
    user = await session.scalar(
        select(User).where(User.school_id == school_id, User.external_user_id == external_user_id)
    )
    if user is None:
        return None

    student = await session.scalar(
        select(Student).where(Student.school_id == school_id, Student.person_id == user.person_id)
    )
    if student is None:
        return None

    result = await session.execute(
        select(StudentEnrollment)
        .where(
            StudentEnrollment.school_id == school_id,
            StudentEnrollment.student_id == student.id,
            StudentEnrollment.status == "ACTIVE",
        )
        .order_by(StudentEnrollment.created_at.desc())
    )
    return result.scalars().first()


__all__ = ["resolve_active_enrollment"]
```

- [ ] **Step 4: Run the test**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_student_enrollment_resolution.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/student_enrollment_resolution.py \
        tests/test_r2_student_enrollment_resolution.py
git commit -m "feat(r2): add resolve_active_enrollment, the identity->class resolver R0 left open"
```

---

### Task 5: `EssayProposalService` — create prompt, add material, create assignment

**Files:**
- Create: `src/agente_ia_edu/services/essay_proposal.py`
- Test: `tests/test_r2_essay_proposal_service.py`

**Interfaces:**
- Consumes: `EssayPrompt`, `PromptMaterial`, `PromptAssignment` (Task 2), `agente_ia_edu.db.models.Class` (unmodified).
- Produces: `EssayProposalService(session)` with `create_prompt(...)`, `add_material(...)`, `create_assignment(...)` — all raising plain `ValueError` on domain-rule violations. Task 6's routes call these directly.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_r2_essay_proposal_service.py
import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AcademicYear, Class, GradeLevel, School, Segment
from agente_ia_edu.services.essay_proposal import EssayProposalService


class EssayProposalServiceTests(unittest.IsolatedAsyncioTestCase):
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

    async def _school_and_class(self, session, code):
        school = School(id=uuid.uuid4(), code=f"PR-{code}", name=f"school-{code}")
        session.add(school)
        await session.flush()
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
        await session.commit()
        return school, klass

    async def test_create_prompt_starts_as_draft(self):
        async with self.session_factory() as session:
            school, _ = await self._school_and_class(session, "1")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p1",
            )
            self.assertEqual(prompt.status, "DRAFT")

    async def test_add_material_requires_draft(self):
        async with self.session_factory() as session:
            school, klass = await self._school_and_class(session, "2")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p2",
            )
            material = await svc.add_material(
                essay_prompt_id=prompt.id, material_type="TEXT", content="Apoio.", position=0,
            )
            self.assertEqual(material.material_type, "TEXT")

            await svc.create_assignment(
                school_id=school.id, essay_prompt_id=prompt.id, class_id=klass.id,
                assigned_by_external_identity="teacher:p2",
            )
            with self.assertRaises(ValueError):
                await svc.add_material(
                    essay_prompt_id=prompt.id, material_type="TEXT", content="Tarde demais.", position=1,
                )

    async def test_add_material_rejects_type_content_mismatch(self):
        async with self.session_factory() as session:
            school, _ = await self._school_and_class(session, "3")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p3",
            )
            with self.assertRaises(ValueError):
                await svc.add_material(
                    essay_prompt_id=prompt.id, material_type="TEXT", content=None, position=0,
                )
            with self.assertRaises(ValueError):
                await svc.add_material(
                    essay_prompt_id=prompt.id, material_type="IMAGE", storage_uri=None, position=0,
                )

    async def test_create_assignment_activates_prompt_and_rejects_foreign_class(self):
        async with self.session_factory() as session:
            school_a, class_a = await self._school_and_class(session, "4a")
            school_b, class_b = await self._school_and_class(session, "4b")
            svc = EssayProposalService(session)
            prompt = await svc.create_prompt(
                school_id=school_a.id, title="Tema", statement="Disserte.", year=2026,
                created_by_external_identity="teacher:p4",
            )

            with self.assertRaises(ValueError):
                await svc.create_assignment(
                    school_id=school_a.id, essay_prompt_id=prompt.id, class_id=class_b.id,
                    assigned_by_external_identity="teacher:p4",
                )

            assignment = await svc.create_assignment(
                school_id=school_a.id, essay_prompt_id=prompt.id, class_id=class_a.id,
                assigned_by_external_identity="teacher:p4",
            )
            self.assertEqual(assignment.status, "OPEN")

            refreshed_prompt = await session.get(type(prompt), prompt.id)
            self.assertEqual(refreshed_prompt.status, "ACTIVE")

            with self.assertRaises(ValueError):
                await svc.create_assignment(
                    school_id=school_a.id, essay_prompt_id=prompt.id, class_id=class_a.id,
                    assigned_by_external_identity="teacher:p4",
                )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_proposal_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.services.essay_proposal'`.

- [ ] **Step 3: Write the service**

```python
# src/agente_ia_edu/services/essay_proposal.py
"""R2 - EssayPrompt/PromptMaterial/PromptAssignment: the "manage a proposal"
side of R2 (spec §6, "Gerenciar proposta"). Authorization (role check,
never-trust-the-body) lives in the route layer (Task 6); this service only
enforces the entity-level rules the database can't express as a CHECK
constraint - a prompt only accepts materials while DRAFT, and an assignment
requires its class to actually belong to the prompt's own school.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Class, EssayPrompt, PromptAssignment, PromptMaterial


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EssayProposalService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def create_prompt(
        self,
        *,
        school_id: uuid.UUID,
        title: str,
        statement: str,
        year: int,
        created_by_external_identity: str,
    ) -> EssayPrompt:
        prompt = EssayPrompt(
            id=uuid.uuid4(),
            school_id=school_id,
            title=title,
            statement=statement,
            year=year,
            status="DRAFT",
            created_by_external_identity=created_by_external_identity,
        )
        self.session.add(prompt)
        await self.session.flush()
        return prompt

    async def add_material(
        self,
        *,
        essay_prompt_id: uuid.UUID,
        material_type: str,
        position: int,
        content: str | None = None,
        storage_uri: str | None = None,
    ) -> PromptMaterial:
        prompt = await self.session.get(EssayPrompt, essay_prompt_id)
        if prompt is None:
            raise ValueError(f"EssayPrompt not found: {essay_prompt_id}")
        if prompt.status != "DRAFT":
            raise ValueError(
                f"EssayPrompt {essay_prompt_id} is {prompt.status}, not DRAFT - "
                "material can only be added before the first assignment."
            )
        if material_type == "TEXT" and not content:
            raise ValueError("material_type=TEXT requires content")
        if material_type == "IMAGE" and not storage_uri:
            raise ValueError("material_type=IMAGE requires storage_uri")
        if material_type not in ("TEXT", "IMAGE"):
            raise ValueError(f"Unknown material_type: {material_type!r}")

        material = PromptMaterial(
            id=uuid.uuid4(),
            essay_prompt_id=essay_prompt_id,
            material_type=material_type,
            content=content,
            storage_uri=storage_uri,
            position=position,
        )
        self.session.add(material)
        await self.session.flush()
        return material

    async def create_assignment(
        self,
        *,
        school_id: uuid.UUID,
        essay_prompt_id: uuid.UUID,
        class_id: uuid.UUID,
        assigned_by_external_identity: str,
        due_at: datetime | None = None,
        validation_enabled: bool = True,
    ) -> PromptAssignment:
        prompt = await self.session.get(EssayPrompt, essay_prompt_id)
        if prompt is None or prompt.school_id != school_id:
            raise ValueError(f"EssayPrompt not found in school {school_id}: {essay_prompt_id}")

        klass = await self.session.get(Class, class_id)
        if klass is None or klass.school_id != school_id:
            raise ValueError(f"Class not found in school {school_id}: {class_id}")

        assignment = PromptAssignment(
            id=uuid.uuid4(),
            school_id=school_id,
            essay_prompt_id=essay_prompt_id,
            class_id=class_id,
            assigned_by_external_identity=assigned_by_external_identity,
            due_at=due_at,
            validation_enabled=validation_enabled,
            status="OPEN",
        )
        self.session.add(assignment)

        if prompt.status == "DRAFT":
            prompt.status = "ACTIVE"

        await self.session.flush()
        return assignment


__all__ = ["EssayProposalService"]
```

Note: the `uq_prompt_assignments_prompt_class` unique constraint (Task 2) is what turns the test's second `create_assignment(..., class_a.id, ...)` call into an `IntegrityError` at `flush()` — that error is allowed to propagate as-is (a `sqlalchemy.exc.IntegrityError` **is** an `Exception`, and the test's `assertRaises(ValueError)` would actually fail against a raw `IntegrityError`). Catch it explicitly and re-raise as `ValueError` instead:

```python
        self.session.add(assignment)

        if prompt.status == "DRAFT":
            prompt.status = "ACTIVE"

        try:
            await self.session.flush()
        except IntegrityError as exc:
            await self.session.rollback()
            raise ValueError(
                f"EssayPrompt {essay_prompt_id} is already assigned to class {class_id}"
            ) from exc
        return assignment
```

Add `from sqlalchemy.exc import IntegrityError` to the imports.

- [ ] **Step 4: Run the test**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_proposal_service.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/essay_proposal.py \
        tests/test_r2_essay_proposal_service.py
git commit -m "feat(r2): add EssayProposalService (create_prompt/add_material/create_assignment)"
```

---

### Task 6: Proposal management routes

**Files:**
- Create: `src/agente_ia_edu/api/routes/essay_prompts.py`
- Test: `tests/test_r2_essay_prompts_routes.py`

**Interfaces:**
- Consumes: `EssayProposalService` (Task 5), `AuthorizationService`/`require_role` (existing), `get_current_identity`/`get_session_factory` (existing).
- Produces: `essay_prompts_router` (`APIRouter(prefix="/api/v1/catalog/essay-prompts", ...)`). Task 11 registers it in `app.py`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_r2_essay_prompts_routes.py
import asyncio
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_session_factory, get_current_identity
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AcademicYear, Class, GradeLevel, School, Segment, UserSchoolLink
from agente_ia_edu.identity import ExternalIdentityContext


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class EssayPromptsRoutesTests(unittest.TestCase):
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
        cls.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_r2")
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _seed_school_teacher_and_class(self, code: str):
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"EPR-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="prof_r2", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
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
                await session.commit()
                return school.id, klass.id

        return self.loop.run_until_complete(_seed())

    def test_full_management_flow(self):
        school_id, class_id = self._seed_school_teacher_and_class("1")
        self._as("prof_r2")

        create_resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema", "statement": "Disserte.", "year": 2026},
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        prompt_id = create_resp.json()["id"]
        self.assertEqual(create_resp.json()["status"], "DRAFT")

        material_resp = self.client.post(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/materials",
            json={"material_type": "TEXT", "content": "Apoio.", "position": 0},
        )
        self.assertEqual(material_resp.status_code, 201, material_resp.text)

        assignment_resp = self.client.post(
            f"/api/v1/catalog/essay-prompts/{prompt_id}/assignments",
            json={"class_id": str(class_id)},
        )
        self.assertEqual(assignment_resp.status_code, 201, assignment_resp.text)
        self.assertEqual(assignment_resp.json()["status"], "OPEN")

    def test_student_cannot_create_prompt(self):
        self._seed_school_teacher_and_class("2")
        self._as("student_r2")
        resp = self.client.post(
            "/api/v1/catalog/essay-prompts",
            json={"title": "Tema", "statement": "Disserte.", "year": 2026},
        )
        self.assertEqual(resp.status_code, 403)
        self._as("prof_r2")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_prompts_routes.py -v`
Expected: FAIL — `404 Not Found` on the first POST (the route doesn't exist yet) or a collection error if `create_app()` doesn't yet know the module.

- [ ] **Step 3: Write the route file**

```python
# src/agente_ia_edu/api/routes/essay_prompts.py
"""R2 - "Gerenciar proposta" (spec §6): create an EssayPrompt, add its
materials, assign it to a class. TEACHER/COORDINATOR/DIRECTOR/PLATFORM_ADMIN,
the same role set catalog.py's create_material/create_resource already use.
``school_id`` always comes from the resolved context, never the request body
- same rule catalog.py's create_resource established.
"""

from __future__ import annotations

import uuid
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_identity, get_session_factory
from ...identity import ExternalIdentityContext
from ...services.authorization import AuthorizationService
from ...services.essay_proposal import EssayProposalService

essay_prompts_router = APIRouter(prefix="/api/v1/catalog/essay-prompts", tags=["essay-prompts"])


class EssayPromptCreateRequest(BaseModel):
    title: str = Field(..., min_length=1, max_length=255)
    statement: str = Field(..., min_length=1)
    year: int


class EssayPromptResponse(BaseModel):
    id: UUID
    school_id: UUID
    title: str
    statement: str
    year: int
    status: str


class PromptMaterialCreateRequest(BaseModel):
    material_type: str = Field(..., description="TEXT, IMAGE")
    position: int = 0
    content: Optional[str] = None
    storage_uri: Optional[str] = None


class PromptMaterialResponse(BaseModel):
    id: UUID
    essay_prompt_id: UUID
    material_type: str
    content: Optional[str] = None
    storage_uri: Optional[str] = None
    position: int


class PromptAssignmentCreateRequest(BaseModel):
    class_id: UUID
    due_at: Optional[str] = None
    validation_enabled: bool = True


class PromptAssignmentResponse(BaseModel):
    id: UUID
    school_id: UUID
    essay_prompt_id: UUID
    class_id: UUID
    status: str
    validation_enabled: bool


async def _authorize(
    identity: ExternalIdentityContext, session: AsyncSession,
) -> uuid.UUID:
    """Returns the caller's school_id as a real uuid.UUID - AuthenticatedUserContext
    types school_id as str, but AuthorizationService actually populates it from a
    UUID column, so this normalizes either representation defensively (same
    conversion Task 11's routes use)."""
    authz = AuthorizationService(session)
    context = await authz.resolve_context(identity)
    role_check = await authz.require_role(context, "TEACHER", "COORDINATOR", "DIRECTOR", "PLATFORM_ADMIN")
    if not role_check.allowed:
        raise HTTPException(
            status_code=403,
            detail="Managing an essay proposal requires a teacher, coordinator, director, or platform admin role.",
        )
    if context.school_id is None:
        raise HTTPException(status_code=400, detail="An active school context is required.")
    return uuid.UUID(str(context.school_id))


@essay_prompts_router.post("", status_code=201, response_model=EssayPromptResponse)
async def create_essay_prompt(
    request: EssayPromptCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayPromptResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        service = EssayProposalService(session)
        prompt = await service.create_prompt(
            school_id=school_id,
            title=request.title,
            statement=request.statement,
            year=request.year,
            created_by_external_identity=identity.external_user_id,
        )
        await session.commit()
        return EssayPromptResponse(
            id=prompt.id, school_id=prompt.school_id, title=prompt.title,
            statement=prompt.statement, year=prompt.year, status=prompt.status,
        )


@essay_prompts_router.post(
    "/{essay_prompt_id}/materials", status_code=201, response_model=PromptMaterialResponse
)
async def add_prompt_material(
    essay_prompt_id: UUID,
    request: PromptMaterialCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> PromptMaterialResponse:
    async with session_factory() as session:
        _school_id = await _authorize(identity, session)
        service = EssayProposalService(session)
        try:
            material = await service.add_material(
                essay_prompt_id=essay_prompt_id,
                material_type=request.material_type,
                content=request.content,
                storage_uri=request.storage_uri,
                position=request.position,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await session.commit()
        return PromptMaterialResponse(
            id=material.id, essay_prompt_id=material.essay_prompt_id,
            material_type=material.material_type, content=material.content,
            storage_uri=material.storage_uri, position=material.position,
        )


@essay_prompts_router.post(
    "/{essay_prompt_id}/assignments", status_code=201, response_model=PromptAssignmentResponse
)
async def create_prompt_assignment(
    essay_prompt_id: UUID,
    request: PromptAssignmentCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> PromptAssignmentResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        service = EssayProposalService(session)
        try:
            assignment = await service.create_assignment(
                school_id=school_id,
                essay_prompt_id=essay_prompt_id,
                class_id=request.class_id,
                assigned_by_external_identity=identity.external_user_id,
                validation_enabled=request.validation_enabled,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await session.commit()
        return PromptAssignmentResponse(
            id=assignment.id, school_id=assignment.school_id,
            essay_prompt_id=assignment.essay_prompt_id, class_id=assignment.class_id,
            status=assignment.status, validation_enabled=assignment.validation_enabled,
        )
```

- [ ] **Step 4: Wire the router into `create_app()` (needed for the test to collect anything at all)**

In `src/agente_ia_edu/api/app.py`, add the import next to the other route imports:

```python
from .routes.essay_prompts import essay_prompts_router
```

and register it with the same `reception_only_guard` every other router gets, right after `app.include_router(catalog_router, dependencies=reception_only_guard)`:

```python
    app.include_router(catalog_router, dependencies=reception_only_guard)
    app.include_router(essay_prompts_router, dependencies=reception_only_guard)
```

- [ ] **Step 5: Run the test**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_prompts_routes.py -v`
Expected: PASS (2 tests).

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_prompts.py \
        src/agente_ia_edu/api/app.py \
        tests/test_r2_essay_prompts_routes.py
git commit -m "feat(r2): add essay-prompts management routes (create/materials/assignments)"
```

---

### Task 7: `EssaySubmissionService` — TYPED flow + resubmission plumbing (spec §5.1, §5.4)

This task creates `essay_submission.py` and its first method. Tasks 8-10 add more methods to the same file/class.

**Files:**
- Create: `src/agente_ia_edu/services/essay_submission.py`
- Test: `tests/test_r2_essay_submission_typed.py`

**Interfaces:**
- Consumes: `EssaySubmission` (Task 2), `normalize_essay_text`/`essay_text_hash` (`services/essay_correction_key.py`, R1, unmodified).
- Produces: `EssaySubmissionService(session, *, storage=None, transcriber=None)`, `start_typed_submission(*, school_id, prompt_assignment_id, student_id, text, essay_id=None, correction_mode=None) -> EssaySubmission`, `EssayResubmissionBlockedError`. Task 11's routes call this.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_r2_essay_submission_typed.py
import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.services.essay_correction_key import essay_text_hash, normalize_essay_text
from agente_ia_edu.services.essay_submission import EssayResubmissionBlockedError, EssaySubmissionService


class TypedSubmissionTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_typed_submission_is_immediately_submitted(self):
        async with self.session_factory() as session:
            svc = EssaySubmissionService(session)
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            raw = "  Minha redação   com espaços.  \r\n\r\nSegundo paragrafo.  "

            submission = await svc.start_typed_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, text=raw,
            )

            self.assertEqual(submission.status, "SUBMITTED")
            self.assertEqual(submission.anchor_mode, "TEXT_OFFSET")
            self.assertEqual(submission.canonical_text, normalize_essay_text(raw))
            self.assertEqual(submission.normalized_text_hash, essay_text_hash(raw))
            self.assertIsNotNone(submission.submitted_at)
            self.assertIsNotNone(submission.essay_id)

    async def test_formativo_resubmission_supersedes_the_previous_version(self):
        async with self.session_factory() as session:
            svc = EssaySubmissionService(session)
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            first = await svc.start_typed_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, text="Primeira versao.",
            )
            second = await svc.start_typed_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, text="Segunda versao.",
                essay_id=first.essay_id, correction_mode="FORMATIVO",
            )

            self.assertEqual(second.essay_id, first.essay_id)
            self.assertNotEqual(second.id, first.id)
            self.assertEqual(second.status, "SUBMITTED")

            refreshed_first = await session.get(type(first), first.id)
            self.assertEqual(refreshed_first.status, "SUPERSEDED")

    async def test_avaliativo_resubmission_is_blocked(self):
        async with self.session_factory() as session:
            svc = EssaySubmissionService(session)
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            first = await svc.start_typed_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, text="Unica versao.",
            )

            with self.assertRaises(EssayResubmissionBlockedError):
                await svc.start_typed_submission(
                    school_id=school_id, prompt_assignment_id=assignment_id,
                    student_id=student_id, text="Tentativa negada.",
                    essay_id=first.essay_id, correction_mode="AVALIATIVO",
                )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_submission_typed.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.services.essay_submission'`.

- [ ] **Step 3: Write the service (first slice)**

```python
# src/agente_ia_edu/services/essay_submission.py
"""R2 - EssaySubmission/EssaySubmissionPage: the "enviar redação" side of R2
(spec §5). One class, built up across this plan's Tasks 7-10:

  Task 7:  TYPED (§5.1) + resubmission plumbing shared by every mode (§5.4)
  Task 8:  PHOTO/PDF upload + synchronous OCR trigger (§5.2 steps 1-2, §5.3)
  Task 9:  page listing + review (§5.2 step 3)
  Task 10: confirm (§5.2 step 4, §5.3's direct-to-SUBMITTED path)

Authorization (role, module gate, enrollment, prompt_assignment ownership)
lives in the route layer (Task 11) - this service only enforces rules the
database can't express as a CHECK constraint.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import EssaySubmission
from ..providers.contracts import EssayTranscriptionProvider
from .essay_correction_key import essay_text_hash, normalize_essay_text
from .material_storage import MaterialStorage


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


class EssayResubmissionBlockedError(RuntimeError):
    """AVALIATIVO: a SUBMITTED essay is definitive and cannot be resubmitted
    (spec §5.4). Mapped to 409 in the route, not 422 - this is a conflict
    with existing state, not a malformed request."""


class EssaySubmissionService:
    def __init__(
        self,
        session: AsyncSession,
        *,
        storage: MaterialStorage | None = None,
        transcriber: EssayTranscriptionProvider | None = None,
    ) -> None:
        self.session = session
        self._storage = storage or MaterialStorage()
        # Lazily built (Task 8) so TYPED submissions and schools with
        # transcription disabled never require OPENAI_API_KEY to be set.
        self._transcriber = transcriber

    async def start_typed_submission(
        self,
        *,
        school_id: uuid.UUID,
        prompt_assignment_id: uuid.UUID,
        student_id: uuid.UUID,
        text: str,
        essay_id: uuid.UUID | None = None,
        correction_mode: str | None = None,
    ) -> EssaySubmission:
        if essay_id is not None:
            await self._supersede_previous(essay_id, correction_mode=correction_mode or "FORMATIVO")

        submission = EssaySubmission(
            id=uuid.uuid4(),
            essay_id=essay_id or uuid.uuid4(),
            school_id=school_id,
            prompt_assignment_id=prompt_assignment_id,
            student_id=student_id,
            mode="TYPED",
            anchor_mode="TEXT_OFFSET",
            status="SUBMITTED",
            canonical_text=normalize_essay_text(text),
            normalized_text_hash=essay_text_hash(text),
            submitted_at=_utcnow(),
        )
        self.session.add(submission)
        await self.session.flush()
        return submission

    async def _supersede_previous(
        self, essay_id: uuid.UUID, *, correction_mode: str
    ) -> EssaySubmission:
        previous = await self.session.scalar(
            select(EssaySubmission).where(
                EssaySubmission.essay_id == essay_id, EssaySubmission.status == "SUBMITTED"
            )
        )
        if previous is None:
            raise ValueError(f"No SUBMITTED version exists for essay_id={essay_id} to resubmit")
        if correction_mode == "AVALIATIVO":
            raise EssayResubmissionBlockedError(
                f"AVALIATIVO: essay_id={essay_id} is already SUBMITTED and cannot be resubmitted"
            )
        previous.status = "SUPERSEDED"
        await self.session.flush()
        return previous


__all__ = ["EssayResubmissionBlockedError", "EssaySubmissionService"]
```

- [ ] **Step 4: Run the test**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_submission_typed.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/essay_submission.py \
        tests/test_r2_essay_submission_typed.py
git commit -m "feat(r2): add EssaySubmissionService.start_typed_submission + shared resubmission plumbing"
```

---

### Task 8: `EssaySubmissionService` — PHOTO/PDF upload + synchronous OCR (spec §5.2 steps 1-2, §5.3)

**Files:**
- Modify: `src/agente_ia_edu/services/essay_submission.py`
- Test: `tests/test_r2_essay_submission_upload.py`

**Interfaces:**
- Consumes: `MaterialStorage.store()` (existing), `EssayTranscriptionProvider.transcribe_page()` (Task 3), `build_essay_transcriber()` (Task 3).
- Produces: `start_photo_submission(...)`, `upload_page(...)` (PHOTO, one page per call), `upload_document(...)` (PDF, splits server-side). Task 11's routes call these.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_r2_essay_submission_upload.py
import unittest
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.providers.models import EssayOcrToken, EssayPageTranscriptionResult
from agente_ia_edu.services.essay_submission import EssaySubmissionService
from agente_ia_edu.services.material_storage import MaterialStorage


class _ScriptedTranscriber:
    """Test double: returns a fixed, controllable token list regardless of
    which image it's given - real image content is irrelevant to what these
    tests check (the service's own page/status bookkeeping)."""

    def __init__(self, tokens):
        self._tokens = tokens
        self.calls = 0

    async def transcribe_page(self, request):
        self.calls += 1
        return EssayPageTranscriptionResult(tokens=self._tokens, provider="scripted", model="v1")


def _make_png(path: Path) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)


class PhotoUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.storage_root = Path("/tmp/r2_upload_test_storage")
        self.tmp_dir = Path("/tmp/r2_upload_test_fixtures")
        self.tmp_dir.mkdir(exist_ok=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_upload_page_with_transcription_runs_ocr_synchronously(self):
        async with self.session_factory() as session:
            tokens = (
                EssayOcrToken(text="Ola", confidence=0.99, start=0, end=3),
                EssayOcrToken(text="mundo", confidence=0.4, start=4, end=9),
            )
            transcriber = _ScriptedTranscriber(tokens)
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=True,
            )
            self.assertEqual(submission.anchor_mode, "TEXT_OFFSET")
            self.assertEqual(submission.status, "PENDING_TRANSCRIPTION")

            source = self.tmp_dir / "page1.png"
            _make_png(source)
            page = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1,
                source_path=source, transcription_enabled=True,
            )

            self.assertEqual(transcriber.calls, 1)
            self.assertEqual(len(page.ocr_tokens), 2)
            self.assertEqual(page.ocr_tokens[0]["text"], "Ola")
            self.assertEqual(page.ocr_tokens[1]["confidence"], 0.4)
            self.assertIsNone(page.reviewed_text)

            refreshed = await session.get(type(submission), submission.id)
            self.assertEqual(refreshed.status, "PENDING_CONFIRMATION")

    async def test_reuploading_the_same_page_number_replaces_it(self):
        async with self.session_factory() as session:
            transcriber = _ScriptedTranscriber(
                (EssayOcrToken(text="v1", confidence=0.9, start=0, end=2),)
            )
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=True,
            )

            source1 = self.tmp_dir / "reupload1.png"
            _make_png(source1)
            page_first = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1,
                source_path=source1, transcription_enabled=True,
            )

            source2 = self.tmp_dir / "reupload2.png"
            _make_png(source2)
            page_second = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1,
                source_path=source2, transcription_enabled=True,
            )

            self.assertEqual(page_first.id, page_second.id)
            self.assertEqual(transcriber.calls, 2)

    async def test_upload_page_without_transcription_never_calls_the_transcriber(self):
        async with self.session_factory() as session:
            transcriber = _ScriptedTranscriber((EssayOcrToken(text="x", confidence=1.0, start=0, end=1),))
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=False,
            )
            self.assertEqual(submission.anchor_mode, "IMAGE_REGION")

            source = self.tmp_dir / "no_ocr.png"
            _make_png(source)
            page = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1,
                source_path=source, transcription_enabled=False,
            )
            self.assertEqual(transcriber.calls, 0)
            self.assertIsNone(page.ocr_tokens)


class PdfUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.storage_root = Path("/tmp/r2_upload_test_storage_pdf")
        self.tmp_dir = Path("/tmp/r2_upload_test_fixtures_pdf")
        self.tmp_dir.mkdir(exist_ok=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _make_two_page_pdf(self) -> Path:
        import pymupdf as fitz

        path = self.tmp_dir / "two_pages.pdf"
        doc = fitz.open()
        for _ in range(2):
            page = doc.new_page()
            page.insert_text((72, 72), "pagina de teste")
        doc.save(str(path))
        doc.close()
        return path

    async def test_upload_document_splits_a_pdf_into_one_page_per_call(self):
        async with self.session_factory() as session:
            transcriber = _ScriptedTranscriber(
                (EssayOcrToken(text="pagina", confidence=0.9, start=0, end=6),)
            )
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PDF", transcription_enabled=True,
            )

            pdf_path = self._make_two_page_pdf()
            pages = await svc.upload_document(
                essay_submission_id=submission.id, source_path=pdf_path,
                transcription_enabled=True,
            )

            self.assertEqual(len(pages), 2)
            self.assertEqual([p.page_number for p in pages], [1, 2])
            self.assertEqual(transcriber.calls, 2)
            for page in pages:
                self.assertEqual(len(page.ocr_tokens), 1)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_submission_upload.py -v`
Expected: FAIL — `AttributeError: 'EssaySubmissionService' object has no attribute 'start_photo_submission'`.

- [ ] **Step 3: Add the upload methods**

In `src/agente_ia_edu/services/essay_submission.py`, add imports at the top:

```python
import mimetypes
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import EssaySubmission, EssaySubmissionPage
from ..providers.contracts import EssayTranscriptionProvider
from ..providers.factory import build_essay_transcriber
from ..providers.models import EssayPageTranscriptionRequest
from .essay_correction_key import essay_text_hash, normalize_essay_text
from .material_storage import MaterialStorage
```

Then add these methods to `EssaySubmissionService` (after `_supersede_previous`):

```python
    async def start_photo_submission(
        self,
        *,
        school_id: uuid.UUID,
        prompt_assignment_id: uuid.UUID,
        student_id: uuid.UUID,
        mode: str,
        transcription_enabled: bool,
        essay_id: uuid.UUID | None = None,
        correction_mode: str | None = None,
    ) -> EssaySubmission:
        if mode not in ("PHOTO", "PDF"):
            raise ValueError(f"start_photo_submission requires mode PHOTO or PDF, got {mode!r}")
        if essay_id is not None:
            await self._supersede_previous(essay_id, correction_mode=correction_mode or "FORMATIVO")

        submission = EssaySubmission(
            id=uuid.uuid4(),
            essay_id=essay_id or uuid.uuid4(),
            school_id=school_id,
            prompt_assignment_id=prompt_assignment_id,
            student_id=student_id,
            mode=mode,
            anchor_mode="TEXT_OFFSET" if transcription_enabled else "IMAGE_REGION",
            status="PENDING_TRANSCRIPTION",
        )
        self.session.add(submission)
        await self.session.flush()
        return submission

    async def upload_page(
        self,
        *,
        essay_submission_id: uuid.UUID,
        page_number: int,
        source_path: Path,
        transcription_enabled: bool,
    ) -> EssaySubmissionPage:
        dest, _digest = self._storage.store(source_path)

        existing = await self.session.scalar(
            select(EssaySubmissionPage).where(
                EssaySubmissionPage.essay_submission_id == essay_submission_id,
                EssaySubmissionPage.page_number == page_number,
            )
        )
        if existing is not None:
            existing.storage_uri = str(dest)
            existing.ocr_tokens = None
            existing.reviewed_text = None
            page = existing
        else:
            page = EssaySubmissionPage(
                id=uuid.uuid4(),
                essay_submission_id=essay_submission_id,
                page_number=page_number,
                storage_uri=str(dest),
            )
            self.session.add(page)
        await self.session.flush()

        if transcription_enabled:
            await self._ocr_page(page, dest)
            submission = await self.session.get(EssaySubmission, essay_submission_id)
            submission.status = "PENDING_CONFIRMATION"
            await self.session.flush()
        return page

    async def upload_document(
        self,
        *,
        essay_submission_id: uuid.UUID,
        source_path: Path,
        transcription_enabled: bool,
    ) -> list[EssaySubmissionPage]:
        """PDF mode: split ``source_path`` into one page-image per PDF page
        and upload each through the same path :meth:`upload_page` uses."""
        page_image_paths = self._split_pdf_pages(source_path)
        pages = []
        for index, image_path in enumerate(page_image_paths, start=1):
            page = await self.upload_page(
                essay_submission_id=essay_submission_id, page_number=index,
                source_path=image_path, transcription_enabled=transcription_enabled,
            )
            pages.append(page)
        return pages

    async def _ocr_page(self, page: EssaySubmissionPage, image_path: Path) -> None:
        result = await self._get_transcriber().transcribe_page(
            EssayPageTranscriptionRequest(image_path=image_path, mime_type=_guess_mime(image_path))
        )
        page.ocr_tokens = [
            {"text": t.text, "confidence": t.confidence, "start": t.start, "end": t.end}
            for t in result.tokens
        ]

    def _get_transcriber(self) -> EssayTranscriptionProvider:
        if self._transcriber is None:
            self._transcriber = build_essay_transcriber()
        return self._transcriber

    @staticmethod
    def _split_pdf_pages(pdf_path: Path) -> list[Path]:
        try:
            import pymupdf as _mu
        except ImportError:
            import fitz as _mu  # type: ignore

        dest_dir = pdf_path.parent / f"{pdf_path.stem}_pages"
        dest_dir.mkdir(exist_ok=True)
        doc = _mu.open(str(pdf_path))
        try:
            paths = []
            for index in range(len(doc)):
                pix = doc[index].get_pixmap(dpi=200)
                page_path = dest_dir / f"page_{index + 1}.png"
                pix.save(str(page_path))
                paths.append(page_path)
            return paths
        finally:
            doc.close()


def _guess_mime(path: Path) -> str:
    guessed, _ = mimetypes.guess_type(str(path))
    return guessed or "application/octet-stream"
```

- [ ] **Step 4: Run the test**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_submission_upload.py -v`
Expected: PASS (4 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/essay_submission.py \
        tests/test_r2_essay_submission_upload.py
git commit -m "feat(r2): add PHOTO/PDF upload with synchronous OCR (server-side PDF page splitting)"
```

---

### Task 9: `EssaySubmissionService` — page listing + review (spec §5.2 step 3)

**Files:**
- Modify: `src/agente_ia_edu/services/essay_submission.py`
- Test: `tests/test_r2_essay_submission_review.py`

**Interfaces:**
- Consumes: `EssaySubmissionPage` (Task 2).
- Produces: `list_pages(essay_submission_id) -> list[EssaySubmissionPage]`, `review_page(*, essay_submission_id, page_number, reviewed_text) -> EssaySubmissionPage`. Task 11's routes call these (`GET .../pages`, `PATCH .../pages/{page_number}`).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_r2_essay_submission_review.py
import unittest
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.providers.models import EssayOcrToken, EssayPageTranscriptionResult
from agente_ia_edu.services.essay_submission import EssaySubmissionService
from agente_ia_edu.services.material_storage import MaterialStorage


class _ScriptedTranscriber:
    def __init__(self, tokens):
        self._tokens = tokens

    async def transcribe_page(self, request):
        return EssayPageTranscriptionResult(tokens=self._tokens, provider="scripted", model="v1")


def _make_png(path: Path) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)


class PageReviewTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.storage_root = Path("/tmp/r2_review_test_storage")
        self.tmp_dir = Path("/tmp/r2_review_test_fixtures")
        self.tmp_dir.mkdir(exist_ok=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _submission_with_two_pages(self, session):
        transcriber = _ScriptedTranscriber(
            (EssayOcrToken(text="ola", confidence=0.9, start=0, end=3),)
        )
        svc = EssaySubmissionService(
            session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
        )
        submission = await svc.start_photo_submission(
            school_id=uuid.uuid4(), prompt_assignment_id=uuid.uuid4(),
            student_id=uuid.uuid4(), mode="PHOTO", transcription_enabled=True,
        )
        for number in (1, 2):
            source = self.tmp_dir / f"review_page{number}.png"
            _make_png(source)
            await svc.upload_page(
                essay_submission_id=submission.id, page_number=number,
                source_path=source, transcription_enabled=True,
            )
        return svc, submission

    async def test_list_pages_returns_them_in_order(self):
        async with self.session_factory() as session:
            svc, submission = await self._submission_with_two_pages(session)
            pages = await svc.list_pages(submission.id)
            self.assertEqual([p.page_number for p in pages], [1, 2])
            self.assertIsNone(pages[0].reviewed_text)

    async def test_review_page_sets_reviewed_text_and_is_idempotent(self):
        async with self.session_factory() as session:
            svc, submission = await self._submission_with_two_pages(session)

            page = await svc.review_page(
                essay_submission_id=submission.id, page_number=1, reviewed_text="ola mundo",
            )
            self.assertEqual(page.reviewed_text, "ola mundo")

            page_again = await svc.review_page(
                essay_submission_id=submission.id, page_number=1, reviewed_text="ola mundo revisado",
            )
            self.assertEqual(page_again.reviewed_text, "ola mundo revisado")

    async def test_review_page_rejects_unknown_page_number(self):
        async with self.session_factory() as session:
            svc, submission = await self._submission_with_two_pages(session)
            with self.assertRaises(ValueError):
                await svc.review_page(
                    essay_submission_id=submission.id, page_number=99, reviewed_text="x",
                )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_submission_review.py -v`
Expected: FAIL — `AttributeError: 'EssaySubmissionService' object has no attribute 'list_pages'`.

- [ ] **Step 3: Add the methods**

In `src/agente_ia_edu/services/essay_submission.py`, add after `_guess_mime` (still inside the class, before the module-level `_guess_mime` function — insert these as class methods, right after `_split_pdf_pages`):

```python
    async def list_pages(self, essay_submission_id: uuid.UUID) -> list[EssaySubmissionPage]:
        result = await self.session.execute(
            select(EssaySubmissionPage)
            .where(EssaySubmissionPage.essay_submission_id == essay_submission_id)
            .order_by(EssaySubmissionPage.page_number)
        )
        return list(result.scalars().all())

    async def review_page(
        self, *, essay_submission_id: uuid.UUID, page_number: int, reviewed_text: str
    ) -> EssaySubmissionPage:
        page = await self.session.scalar(
            select(EssaySubmissionPage).where(
                EssaySubmissionPage.essay_submission_id == essay_submission_id,
                EssaySubmissionPage.page_number == page_number,
            )
        )
        if page is None:
            raise ValueError(
                f"No page {page_number} on submission {essay_submission_id}"
            )
        page.reviewed_text = reviewed_text
        await self.session.flush()
        return page
```

- [ ] **Step 4: Run the test**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_submission_review.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/essay_submission.py \
        tests/test_r2_essay_submission_review.py
git commit -m "feat(r2): add page listing and free-text-per-page review (spec §5.2 step 3)"
```

---

### Task 10: `EssaySubmissionService.confirm_submission` (spec §5.2 step 4, §5.3)

One endpoint, two behaviors, branching on `anchor_mode` — see this plan's Global Constraints.

**Files:**
- Modify: `src/agente_ia_edu/services/essay_submission.py`
- Test: `tests/test_r2_essay_submission_confirm.py`

**Interfaces:**
- Consumes: `list_pages` (Task 9).
- Produces: `confirm_submission(essay_submission_id) -> EssaySubmission`. Task 11's `POST .../confirm` route calls this.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_r2_essay_submission_confirm.py
import unittest
import uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.providers.models import EssayOcrToken, EssayPageTranscriptionResult
from agente_ia_edu.services.essay_correction_key import essay_text_hash, normalize_essay_text
from agente_ia_edu.services.essay_submission import EssaySubmissionService
from agente_ia_edu.services.material_storage import MaterialStorage


class _ScriptedTranscriber:
    def __init__(self, tokens):
        self._tokens = tokens

    async def transcribe_page(self, request):
        return EssayPageTranscriptionResult(tokens=self._tokens, provider="scripted", model="v1")


def _make_png(path: Path) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)


class ConfirmSubmissionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.storage_root = Path("/tmp/r2_confirm_test_storage")
        self.tmp_dir = Path("/tmp/r2_confirm_test_fixtures")
        self.tmp_dir.mkdir(exist_ok=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_confirm_with_transcription_concatenates_reviewed_text_in_order(self):
        async with self.session_factory() as session:
            transcriber = _ScriptedTranscriber(
                (EssayOcrToken(text="x", confidence=0.9, start=0, end=1),)
            )
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            submission = await svc.start_photo_submission(
                school_id=uuid.uuid4(), prompt_assignment_id=uuid.uuid4(),
                student_id=uuid.uuid4(), mode="PHOTO", transcription_enabled=True,
            )
            for number in (1, 2):
                source = self.tmp_dir / f"confirm_page{number}.png"
                _make_png(source)
                await svc.upload_page(
                    essay_submission_id=submission.id, page_number=number,
                    source_path=source, transcription_enabled=True,
                )

            await svc.review_page(
                essay_submission_id=submission.id, page_number=1, reviewed_text="Primeira pagina."
            )
            with self.assertRaises(ValueError):
                await svc.confirm_submission(submission.id)

            await svc.review_page(
                essay_submission_id=submission.id, page_number=2, reviewed_text="Segunda pagina."
            )
            confirmed = await svc.confirm_submission(submission.id)

            expected_text = "Primeira pagina.\n\nSegunda pagina."
            self.assertEqual(confirmed.status, "SUBMITTED")
            self.assertEqual(confirmed.canonical_text, normalize_essay_text(expected_text))
            self.assertEqual(confirmed.normalized_text_hash, essay_text_hash(expected_text))
            self.assertIsNotNone(confirmed.submitted_at)

    async def test_confirm_without_transcription_needs_no_review(self):
        async with self.session_factory() as session:
            svc = EssaySubmissionService(session, storage=MaterialStorage(root=self.storage_root))
            submission = await svc.start_photo_submission(
                school_id=uuid.uuid4(), prompt_assignment_id=uuid.uuid4(),
                student_id=uuid.uuid4(), mode="PDF", transcription_enabled=False,
            )
            source = self.tmp_dir / "no_transcription.png"
            _make_png(source)
            await svc.upload_page(
                essay_submission_id=submission.id, page_number=1,
                source_path=source, transcription_enabled=False,
            )

            confirmed = await svc.confirm_submission(submission.id)
            self.assertEqual(confirmed.status, "SUBMITTED")
            self.assertIsNone(confirmed.canonical_text)
            self.assertIsNone(confirmed.normalized_text_hash)
            self.assertIsNotNone(confirmed.submitted_at)

    async def test_confirm_rejects_a_submission_with_zero_pages(self):
        async with self.session_factory() as session:
            svc = EssaySubmissionService(session, storage=MaterialStorage(root=self.storage_root))
            submission = await svc.start_photo_submission(
                school_id=uuid.uuid4(), prompt_assignment_id=uuid.uuid4(),
                student_id=uuid.uuid4(), mode="PDF", transcription_enabled=False,
            )
            with self.assertRaises(ValueError):
                await svc.confirm_submission(submission.id)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_submission_confirm.py -v`
Expected: FAIL — `AttributeError: 'EssaySubmissionService' object has no attribute 'confirm_submission'`.

- [ ] **Step 3: Add the method**

In `src/agente_ia_edu/services/essay_submission.py`, add after `review_page`:

```python
    async def confirm_submission(self, essay_submission_id: uuid.UUID) -> EssaySubmission:
        submission = await self.session.get(EssaySubmission, essay_submission_id)
        if submission is None:
            raise ValueError(f"EssaySubmission not found: {essay_submission_id}")

        pages = await self.list_pages(essay_submission_id)
        if not pages:
            raise ValueError(f"EssaySubmission {essay_submission_id} has no pages to confirm")

        if submission.anchor_mode == "TEXT_OFFSET":
            missing = [p.page_number for p in pages if p.reviewed_text is None]
            if missing:
                raise ValueError(
                    f"Pages not yet reviewed: {missing} - every page needs reviewed_text "
                    "before a transcribed submission can be confirmed"
                )
            full_text = "\n\n".join(p.reviewed_text for p in pages)
            submission.canonical_text = normalize_essay_text(full_text)
            submission.normalized_text_hash = essay_text_hash(full_text)
        # anchor_mode == "IMAGE_REGION": no transcription ran, canonical_text/
        # normalized_text_hash stay NULL - spec §5.3's documented consequence.

        submission.status = "SUBMITTED"
        submission.submitted_at = _utcnow()
        await self.session.flush()
        return submission
```

- [ ] **Step 4: Run the test**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_submission_confirm.py -v`
Expected: PASS (3 tests).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/essay_submission.py \
        tests/test_r2_essay_submission_confirm.py
git commit -m "feat(r2): add confirm_submission, serving both the transcribed and non-transcribed flows"
```

---

### Task 11: Submission routes, app wiring, and the authorization sweep (spec §6)

This is the task that actually enforces spec §6 ("Enviar redação"): role, module gate, real enrollment, and prompt_assignment ownership — 403, never 404, for anything that isn't the caller's own.

**Files:**
- Create: `src/agente_ia_edu/api/routes/essay_submissions.py`
- Modify: `src/agente_ia_edu/api/app.py` (register the router)
- Test: `tests/test_r2_essay_submissions_routes.py`
- Test: `tests/test_r2_essay_submissions_authorization.py`

**Interfaces:**
- Consumes: `EssaySubmissionService` (Tasks 7-10), `resolve_active_enrollment` (Task 4), `AuthorizationService.require_role`/`require_module` (existing), `InstitutionSettingsService.get_settings` (existing, extended in Task 1), `PromptAssignment` (Task 2).
- Produces: `essay_submissions_router`. Nothing later consumes this — it is the top of the R2 call stack.

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_r2_essay_submissions_routes.py
import asyncio
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayPrompt,
    GradeLevel,
    Person,
    PromptAssignment,
    School,
    SchoolModule,
    Segment,
    Student,
    StudentEnrollment,
    User,
    UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


def _make_png(path: Path) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)


class EssaySubmissionsRoutesTests(unittest.TestCase):
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
        cls.client = TestClient(cls.app)
        cls.tmp_dir = Path("/tmp/r2_route_test_fixtures")
        cls.tmp_dir.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _seed_enrolled_student(self, code: str, *, transcription_enabled: bool, module_enabled: bool = True):
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"SUB-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                if module_enabled:
                    session.add(SchoolModule(
                        id=uuid.uuid4(), school_id=school.id, module_key="REDACAO_IA", enabled=True,
                    ))
                session.add(UserSchoolLink(
                    external_user_id=f"student_{code}", school_id=school.id, role="STUDENT",
                    scope_type="SCHOOL", active=True,
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
                person = Person(id=uuid.uuid4(), school_id=school.id, full_name=f"Aluno {code}")
                session.add(person)
                await session.flush()
                session.add(User(
                    id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                    external_identity_provider="test", external_user_id=f"student_{code}",
                ))
                student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id, student_code=f"ST-{code}")
                session.add(student)
                await session.flush()
                session.add(StudentEnrollment(
                    id=uuid.uuid4(), school_id=school.id, student_id=student.id, class_id=klass.id,
                    status="ACTIVE",
                ))

                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
                    year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
                )
                session.add(prompt)
                await session.flush()
                assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                    class_id=klass.id, assigned_by_external_identity="teacher:t",
                )
                session.add(assignment)

                from agente_ia_edu.services.institution_settings import InstitutionSettingsService
                await InstitutionSettingsService(session).configure(
                    school.id, performed_by_external_id="admin:x",
                    transcription_enabled=transcription_enabled,
                )
                await session.commit()
                return assignment.id

        return self.loop.run_until_complete(_seed())

    def test_typed_submission_end_to_end(self):
        assignment_id = self._seed_enrolled_student("1", transcription_enabled=True)
        self._as("student_1")

        create_resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "TYPED", "text": "Minha redacao."},
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        body = create_resp.json()
        self.assertEqual(body["status"], "SUBMITTED")
        self.assertEqual(body["mode"], "TYPED")

    def test_photo_flow_with_transcription_end_to_end(self):
        assignment_id = self._seed_enrolled_student("2", transcription_enabled=True)
        self._as("student_2")

        create_resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "PHOTO"},
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        submission_id = create_resp.json()["id"]
        self.assertEqual(create_resp.json()["status"], "PENDING_TRANSCRIPTION")

        source = self.tmp_dir / "route_page1.png"
        _make_png(source)
        with open(source, "rb") as f:
            page_resp = self.client.post(
                f"/api/v1/student/essay-submissions/{submission_id}/pages",
                data={"page_number": "1"},
                files={"file": ("page1.png", f, "image/png")},
            )
        self.assertEqual(page_resp.status_code, 201, page_resp.text)

        pages_resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/pages")
        self.assertEqual(pages_resp.status_code, 200)
        self.assertEqual(len(pages_resp.json()), 1)

        review_resp = self.client.patch(
            f"/api/v1/student/essay-submissions/{submission_id}/pages/1",
            json={"reviewed_text": "Texto revisado."},
        )
        self.assertEqual(review_resp.status_code, 200, review_resp.text)

        confirm_resp = self.client.post(
            f"/api/v1/student/essay-submissions/{submission_id}/confirm"
        )
        self.assertEqual(confirm_resp.status_code, 200, confirm_resp.text)
        self.assertEqual(confirm_resp.json()["status"], "SUBMITTED")

    def test_photo_flow_without_transcription_skips_review(self):
        assignment_id = self._seed_enrolled_student("3", transcription_enabled=False)
        self._as("student_3")

        create_resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "PHOTO"},
        )
        submission_id = create_resp.json()["id"]

        source = self.tmp_dir / "route_page_no_ocr.png"
        _make_png(source)
        with open(source, "rb") as f:
            self.client.post(
                f"/api/v1/student/essay-submissions/{submission_id}/pages",
                data={"page_number": "1"},
                files={"file": ("page1.png", f, "image/png")},
            )

        confirm_resp = self.client.post(
            f"/api/v1/student/essay-submissions/{submission_id}/confirm"
        )
        self.assertEqual(confirm_resp.status_code, 200, confirm_resp.text)
        self.assertIsNone(confirm_resp.json()["canonical_text"])


if __name__ == "__main__":
    unittest.main()
```

```python
# tests/test_r2_essay_submissions_authorization.py
import asyncio
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayPrompt,
    GradeLevel,
    Person,
    PromptAssignment,
    School,
    SchoolModule,
    Segment,
    Student,
    StudentEnrollment,
    User,
    UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class EssaySubmissionAuthorizationTests(unittest.TestCase):
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
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _seed_school_with_class_and_assignment(self, code: str, *, module_enabled: bool = True):
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"AUT-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                if module_enabled:
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
                assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                    class_id=klass.id, assigned_by_external_identity="teacher:t",
                )
                session.add(assignment)
                await session.commit()
                return school.id, klass.id, assignment.id

        return self.loop.run_until_complete(_seed())

    def _enroll_student(self, school_id, class_id, external_user_id: str):
        async def _seed():
            async with self.factory() as session:
                session.add(UserSchoolLink(
                    external_user_id=external_user_id, school_id=school_id, role="STUDENT",
                    scope_type="SCHOOL", active=True,
                ))
                person = Person(id=uuid.uuid4(), school_id=school_id, full_name=external_user_id)
                session.add(person)
                await session.flush()
                session.add(User(
                    id=uuid.uuid4(), school_id=school_id, person_id=person.id,
                    external_identity_provider="test", external_user_id=external_user_id,
                ))
                student = Student(
                    id=uuid.uuid4(), school_id=school_id, person_id=person.id,
                    student_code=external_user_id,
                )
                session.add(student)
                await session.flush()
                session.add(StudentEnrollment(
                    id=uuid.uuid4(), school_id=school_id, student_id=student.id, class_id=class_id,
                    status="ACTIVE",
                ))
                await session.commit()

        self.loop.run_until_complete(_seed())

    def test_a_student_with_no_enrollment_at_all_is_denied(self):
        _school_id, _class_id, assignment_id = self._seed_school_with_class_and_assignment("1")
        self._as("ghost_student")
        resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "TYPED", "text": "x"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_a_student_enrolled_in_a_different_class_is_denied_not_404(self):
        school_id, _class_id, assignment_id = self._seed_school_with_class_and_assignment("2")

        async def _other_class():
            async with self.factory() as session:
                segment = Segment(id=uuid.uuid4(), school_id=school_id, name="seg2", external_id="SEG-2B")
                session.add(segment)
                await session.flush()
                grade = GradeLevel(
                    id=uuid.uuid4(), school_id=school_id, segment_id=segment.id,
                    name="grade2", external_id="GRADE-2B",
                )
                year = AcademicYear(id=uuid.uuid4(), school_id=school_id, year=2026, external_id="YEAR-2B")
                session.add_all([grade, year])
                await session.flush()
                other_class = Class(
                    id=uuid.uuid4(), school_id=school_id, academic_year_id=year.id,
                    grade_level_id=grade.id, name="outra-turma", external_id="TURMA-2B",
                )
                session.add(other_class)
                await session.commit()
                return other_class.id

        other_class_id = self.loop.run_until_complete(_other_class())
        self._enroll_student(school_id, other_class_id, "wrong_class_student")
        self._as("wrong_class_student")

        resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "TYPED", "text": "x"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_module_disabled_denies_submission(self):
        school_id, class_id, assignment_id = self._seed_school_with_class_and_assignment(
            "3", module_enabled=False
        )
        self._enroll_student(school_id, class_id, "no_module_student")
        self._as("no_module_student")

        resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "TYPED", "text": "x"},
        )
        self.assertEqual(resp.status_code, 403)

    def test_a_teacher_cannot_submit_an_essay(self):
        school_id, class_id, assignment_id = self._seed_school_with_class_and_assignment("4")

        async def _link_teacher():
            async with self.factory() as session:
                session.add(UserSchoolLink(
                    external_user_id="teacher_not_student", school_id=school_id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                await session.commit()

        self.loop.run_until_complete(_link_teacher())
        self._as("teacher_not_student")

        resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "TYPED", "text": "x"},
        )
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_submissions_routes.py tests/test_r2_essay_submissions_authorization.py -v`
Expected: FAIL — `404 Not Found` on every request (the router doesn't exist/isn't registered yet).

- [ ] **Step 3: Write the route file**

```python
# src/agente_ia_edu/api/routes/essay_submissions.py
"""R2 - "Enviar redação" (spec §6): the student-facing submission surface.

Every route here resolves the caller's own real enrollment
(resolve_active_enrollment) and validates prompt_assignment ownership
directly, before calling EssaySubmissionService - a prompt_assignment_id
that isn't the caller's own class is 403, never 404 (spec §6 item 4, the
Fase 3C rule reused verbatim).
"""

from __future__ import annotations

import tempfile
import uuid
from pathlib import Path
from typing import Optional
from uuid import UUID

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel, Field
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_identity, get_session_factory
from ...db.models import EssaySubmission, EssaySubmissionPage, PromptAssignment
from ...identity import ExternalIdentityContext
from ...services.admin import PlatformModuleKey
from ...services.authorization import AuthorizationService
from ...services.essay_submission import EssayResubmissionBlockedError, EssaySubmissionService
from ...services.institution_settings import InstitutionSettingsService
from ...services.student_enrollment_resolution import resolve_active_enrollment

essay_submissions_router = APIRouter(
    prefix="/api/v1/student/essay-submissions", tags=["essay-submissions"]
)

_MAX_UPLOAD_BYTES = 25 * 1024 * 1024


class EssaySubmissionCreateRequest(BaseModel):
    prompt_assignment_id: UUID
    mode: str = Field(..., description="TYPED, PHOTO, PDF")
    text: Optional[str] = Field(None, description="required when mode=TYPED")
    resubmit_essay_id: Optional[UUID] = Field(
        None, description="set to create a new version of an existing essay_id (reenvio)"
    )


class EssaySubmissionResponse(BaseModel):
    id: UUID
    essay_id: UUID
    school_id: UUID
    prompt_assignment_id: UUID
    student_id: UUID
    mode: str
    anchor_mode: str
    status: str
    canonical_text: Optional[str] = None
    normalized_text_hash: Optional[str] = None


class EssaySubmissionPageResponse(BaseModel):
    id: UUID
    essay_submission_id: UUID
    page_number: int
    storage_uri: str
    ocr_tokens: Optional[list] = None
    reviewed_text: Optional[str] = None


class PageReviewRequest(BaseModel):
    reviewed_text: str = Field(..., min_length=1)


def _submission_to_response(submission: EssaySubmission) -> EssaySubmissionResponse:
    return EssaySubmissionResponse(
        id=submission.id, essay_id=submission.essay_id, school_id=submission.school_id,
        prompt_assignment_id=submission.prompt_assignment_id, student_id=submission.student_id,
        mode=submission.mode, anchor_mode=submission.anchor_mode, status=submission.status,
        canonical_text=submission.canonical_text, normalized_text_hash=submission.normalized_text_hash,
    )


def _page_to_response(page: EssaySubmissionPage) -> EssaySubmissionPageResponse:
    return EssaySubmissionPageResponse(
        id=page.id, essay_submission_id=page.essay_submission_id, page_number=page.page_number,
        storage_uri=page.storage_uri, ocr_tokens=page.ocr_tokens, reviewed_text=page.reviewed_text,
    )


async def _authorize_student(
    identity: ExternalIdentityContext, session: AsyncSession,
):
    authz = AuthorizationService(session)
    context = await authz.resolve_context(identity)
    role_check = await authz.require_role(context, "STUDENT")
    if not role_check.allowed:
        raise HTTPException(status_code=403, detail="Only a student may submit an essay.")
    if context.school_id is None:
        raise HTTPException(status_code=403, detail="An active school context is required.")

    module_check = await authz.require_module(context, PlatformModuleKey.REDACAO_IA)
    if not module_check.allowed:
        raise HTTPException(status_code=403, detail=module_check.reason)
    return context


async def _resolve_enrollment_or_403(session: AsyncSession, *, school_id: uuid.UUID, external_user_id: str):
    enrollment = await resolve_active_enrollment(
        session, school_id=school_id, external_user_id=external_user_id
    )
    if enrollment is None:
        raise HTTPException(status_code=403, detail="No active enrollment - cannot submit an essay.")
    return enrollment


async def _assignment_for_own_class_or_403(
    session: AsyncSession, *, prompt_assignment_id: uuid.UUID, school_id: uuid.UUID, class_id: uuid.UUID,
) -> PromptAssignment:
    assignment = await session.get(PromptAssignment, prompt_assignment_id)
    if (
        assignment is None
        or assignment.school_id != school_id
        or assignment.class_id != class_id
    ):
        raise HTTPException(
            status_code=403, detail="This proposal was not assigned to your class."
        )
    return assignment


async def _submission_for_own_school_or_403(
    session: AsyncSession, *, essay_submission_id: uuid.UUID, school_id: uuid.UUID,
) -> EssaySubmission:
    submission = await session.get(EssaySubmission, essay_submission_id)
    if submission is None or submission.school_id != school_id:
        raise HTTPException(status_code=403, detail="This submission is not yours.")
    return submission


@essay_submissions_router.post("", status_code=201, response_model=EssaySubmissionResponse)
async def create_essay_submission(
    request: EssaySubmissionCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssaySubmissionResponse:
    async with session_factory() as session:
        context = await _authorize_student(identity, session)
        school_id = uuid.UUID(str(context.school_id))
        enrollment = await _resolve_enrollment_or_403(
            session, school_id=school_id, external_user_id=identity.external_user_id
        )
        assignment = await _assignment_for_own_class_or_403(
            session, prompt_assignment_id=request.prompt_assignment_id,
            school_id=school_id, class_id=enrollment.class_id,
        )
        settings = await InstitutionSettingsService(session).get_settings(school_id)
        service = EssaySubmissionService(session)

        try:
            if request.mode == "TYPED":
                if not request.text:
                    raise HTTPException(status_code=422, detail="text is required when mode=TYPED")
                submission = await service.start_typed_submission(
                    school_id=school_id, prompt_assignment_id=assignment.id,
                    student_id=enrollment.student_id, text=request.text,
                    essay_id=request.resubmit_essay_id, correction_mode=settings.correction_mode,
                )
            elif request.mode in ("PHOTO", "PDF"):
                submission = await service.start_photo_submission(
                    school_id=school_id, prompt_assignment_id=assignment.id,
                    student_id=enrollment.student_id, mode=request.mode,
                    transcription_enabled=settings.transcription_enabled,
                    essay_id=request.resubmit_essay_id, correction_mode=settings.correction_mode,
                )
            else:
                raise HTTPException(status_code=422, detail=f"Unknown mode: {request.mode!r}")
        except EssayResubmissionBlockedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc

        await session.commit()
        return _submission_to_response(submission)


@essay_submissions_router.post(
    "/{essay_submission_id}/pages", status_code=201, response_model=EssaySubmissionPageResponse
)
async def upload_essay_submission_page(
    essay_submission_id: UUID,
    page_number: int = Form(...),
    file: UploadFile = File(...),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssaySubmissionPageResponse:
    async with session_factory() as session:
        context = await _authorize_student(identity, session)
        school_id = uuid.UUID(str(context.school_id))
        submission = await _submission_for_own_school_or_403(
            session, essay_submission_id=essay_submission_id, school_id=school_id
        )
        settings = await InstitutionSettingsService(session).get_settings(school_id)

        tmp_dir = Path(tempfile.mkdtemp(prefix="r2_page_upload_"))
        tmp_path = tmp_dir / (file.filename or f"page{page_number}")
        size = 0
        with open(tmp_path, "wb") as out:
            while chunk := await file.read(1024 * 1024):
                size += len(chunk)
                if size > _MAX_UPLOAD_BYTES:
                    out.close()
                    tmp_path.unlink(missing_ok=True)
                    raise HTTPException(status_code=413, detail="file too large (max 25MB)")
                out.write(chunk)

        try:
            service = EssaySubmissionService(session)
            page = await service.upload_page(
                essay_submission_id=submission.id, page_number=page_number,
                source_path=tmp_path, transcription_enabled=settings.transcription_enabled,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        finally:
            tmp_path.unlink(missing_ok=True)
            try:
                tmp_dir.rmdir()
            except OSError:
                pass

        await session.commit()
        return _page_to_response(page)


@essay_submissions_router.get(
    "/{essay_submission_id}/pages", response_model=list[EssaySubmissionPageResponse]
)
async def list_essay_submission_pages(
    essay_submission_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[EssaySubmissionPageResponse]:
    async with session_factory() as session:
        context = await _authorize_student(identity, session)
        school_id = uuid.UUID(str(context.school_id))
        submission = await _submission_for_own_school_or_403(
            session, essay_submission_id=essay_submission_id, school_id=school_id
        )
        service = EssaySubmissionService(session)
        pages = await service.list_pages(submission.id)
        return [_page_to_response(p) for p in pages]


@essay_submissions_router.patch(
    "/{essay_submission_id}/pages/{page_number}", response_model=EssaySubmissionPageResponse
)
async def review_essay_submission_page(
    essay_submission_id: UUID,
    page_number: int,
    request: PageReviewRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssaySubmissionPageResponse:
    async with session_factory() as session:
        context = await _authorize_student(identity, session)
        school_id = uuid.UUID(str(context.school_id))
        submission = await _submission_for_own_school_or_403(
            session, essay_submission_id=essay_submission_id, school_id=school_id
        )
        service = EssaySubmissionService(session)
        try:
            page = await service.review_page(
                essay_submission_id=submission.id, page_number=page_number,
                reviewed_text=request.reviewed_text,
            )
        except ValueError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        await session.commit()
        return _page_to_response(page)


@essay_submissions_router.post(
    "/{essay_submission_id}/confirm", response_model=EssaySubmissionResponse
)
async def confirm_essay_submission(
    essay_submission_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssaySubmissionResponse:
    async with session_factory() as session:
        context = await _authorize_student(identity, session)
        school_id = uuid.UUID(str(context.school_id))
        submission = await _submission_for_own_school_or_403(
            session, essay_submission_id=essay_submission_id, school_id=school_id
        )
        service = EssaySubmissionService(session)
        try:
            confirmed = await service.confirm_submission(submission.id)
        except ValueError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        await session.commit()
        return _submission_to_response(confirmed)
```

- [ ] **Step 4: Wire the router into `create_app()`**

In `src/agente_ia_edu/api/app.py`, add the import next to `essay_prompts_router`'s:

```python
from .routes.essay_prompts import essay_prompts_router
from .routes.essay_submissions import essay_submissions_router
```

and register it right after `essay_prompts_router`:

```python
    app.include_router(essay_prompts_router, dependencies=reception_only_guard)
    app.include_router(essay_submissions_router, dependencies=reception_only_guard)
```

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_submissions_routes.py tests/test_r2_essay_submissions_authorization.py -v`
Expected: PASS (3 + 4 = 7 tests).

- [ ] **Step 6: Run the full R2 test sweep together, then the whole suite**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_*.py -v`
Expected: PASS (every test written across Tasks 1-11).

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/ -q`
Expected: PASS, same pre-existing/documented environmental failures as before this plan (none of R2's own tests newly fail).

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_submissions.py \
        src/agente_ia_edu/api/app.py \
        tests/test_r2_essay_submissions_routes.py \
        tests/test_r2_essay_submissions_authorization.py
git commit -m "feat(r2): add essay-submissions routes (typed/photo/pdf, review, confirm) with full §6 authorization"
```

---

## Self-Review

**Spec coverage** — every numbered section of `docs/superpowers/specs/2026-09-21-r2-propostas-envio-redacao-design.md` maps to a task:
- §3.1-3.5 (data model) → Task 2.
- §4 (enrollment resolver) → Task 4.
- §5.1 (TYPED) → Task 7.
- §5.2 (PHOTO/PDF with transcription: upload, OCR, free-text-per-page review, confirm) → Tasks 8, 9, 10.
- §5.3 (PHOTO/PDF without transcription) → Tasks 8, 10 (same code paths, `transcription_enabled=False`).
- §5.4 (reenvio) → Task 7 (`_supersede_previous`/`EssayResubmissionBlockedError`), wired into the route in Task 11.
- §6 (autorização: gerenciar proposta) → Task 6. §6 (autorização: enviar redação, all 4 numbered checks) → Task 11.
- §7 (armazenamento) → reused unmodified (`MaterialStorage`, Tasks 8/11).
- §8 (deferred) → nothing in this plan builds `EssayCorrection`, `correction_key()`, or any read/listing route — confirmed absent from every task.
- §9 (rastreabilidade) → `EssaySubmission.id`/`.essay_id`/`.anchor_mode` match `Identification` exactly (Task 2); `normalize_essay_text`/`essay_text_hash` called exactly once per terminal transition (Tasks 7, 10); `SchoolModule`/`PlatformModuleKey.REDACAO_IA`/`require_module` reused unmodified (Task 11); `User`/`Person`/`Student`/`StudentEnrollment`/`Class` reused unmodified (Task 4).

**Placeholder scan** — no "TBD"/"add validation"/"similar to Task N" found; every step has runnable code. The one deliberately open thing (OCR confidence being an approximation, not a real score) is called out explicitly in Global Constraints and in `_tokens_from_logprobs`'s own docstring, not hidden.

**Type consistency** — `EssaySubmissionService` methods and their call sites checked: `start_typed_submission`/`start_photo_submission` both accept `essay_id: uuid.UUID | None = None, correction_mode: str | None = None` and both route through the identically-named `_supersede_previous`; `upload_page`'s `transcription_enabled: bool` parameter name and position match across Task 8's own two callers (`start_photo_submission` doesn't call it directly - only the route and `upload_document` do) and Task 11's route; `EssayPageTranscriptionRequest`/`EssayOcrToken`/`EssayPageTranscriptionResult` field names are identical across Task 3 (definition), Task 8 (`_ocr_page`), and every test double (`_ScriptedTranscriber`, `FakeProvider`). Route response models (`EssaySubmissionResponse`, `EssaySubmissionPageResponse`) list exactly the ORM fields each `_*_to_response` helper reads.

## Execution Handoff

Plan complete and saved to `docs/superpowers/plans/2026-09-21-r2-propostas-envio-redacao.md`. Two execution options:

1. **Subagent-Driven (recommended)** - dispatch a fresh subagent per task, review between tasks, fast iteration.
2. **Inline Execution** - execute tasks in this session using executing-plans, batch execution with checkpoints.

Which approach?
