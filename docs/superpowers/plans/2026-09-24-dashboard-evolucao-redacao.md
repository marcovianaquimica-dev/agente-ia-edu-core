# Dashboard de Evolução do Aluno — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Give students and teachers a read-only dashboard, inside the existing Redação module, showing how a student's essay scores evolve across their approved corrections: a timeline of cards, six SVG line charts (one per competency plus total), and a checklist of what's going well / what needs work, all fed by real data.

**Architecture:** One new backend aggregation service (`essay_evolution.py`) shared by two new routers — a student-side router appended to `essay_submissions.py` and a teacher-side router (plus a student-search endpoint) appended to `essay_corrections.py` — both reusing this codebase's existing authorization helpers unmodified. One new shared frontend module (`essay-evolution.js`) renders the timeline/charts/checklist as pure HTML strings (no DOM state), consumed by both `essay.js` (student portal, new section under the prompt list) and `essay-review.js` (teacher portal, new "Evolução" tab with a student search field). No new dependency, no DB migration, no charting library — hand-rolled SVG, following the mockup the user already approved.

**Tech Stack:** FastAPI + SQLAlchemy async ORM (existing), vanilla JS IIFE modules (existing), raw SVG (new, no library).

**Spec:** `docs/superpowers/specs/2026-09-24-dashboard-evolucao-redacao-design.md`

## Global Constraints

- No new frontend dependency: charts are hand-rolled SVG strings, matching the mockup the user approved (`linechart.html` visual companion screen).
- Competency color palette is fixed and must match exactly what's already used on screen and in the PDF export: C1 `#4f46e5`, C2 `#06b6d4`, C3 `#ef4444`, C4 `#f59e0b`, C5 `#10b981`. Total-score chart uses a neutral dark color, `#1e293b`.
- Competency point scale is 0–200 per competency (`OFFICIAL_LEVEL_POINTS` in `essay_engine_contract/v1.py`), 0–1000 for the total (5 × 200).
- No DB migration: this feature only reads existing columns (`EssayCorrection.final_scores`, `.published_at`, `.status`; `EssaySubmission.student_id`).
- Reuse authorization exactly as-is: student routes call `_authorize_student` + `_resolve_enrollment_or_403` (from `essay_submissions.py`); teacher routes call `_authorize` + the same school-level scoping `list_essay_corrections` already uses (from `essay_corrections.py`). No new authorization logic.
- Any `pytest`/`python` invocation in this worktree MUST be run as `PYTHONPATH=src .venv/bin/python -m pytest ...` (or `PYTHONPATH=src .venv/bin/pytest ...`) — this worktree's `.venv` is a symlink to the main checkout's shared venv, whose editable install otherwise silently resolves `agente_ia_edu` to the main checkout's `src/`, not this worktree's own files.
- Reuse `EssayEvolutionResponse`/`EssayEvolutionEntry` (defined once, in `essay_evolution.py`) from both route files — do not redefine these Pydantic models in each route file.
- Frontend: no automated tests (established convention in this codebase — see `essay.js`/`essay-review.js`, neither has a test file).

---

### Task 1: Backend aggregation service (`essay_evolution.py`)

**Files:**
- Create: `src/agente_ia_edu/services/essay_evolution.py`
- Test: `tests/test_essay_evolution.py`

**Interfaces:**
- Produces: `EssayEvolutionEntry` (Pydantic model: `essay_submission_id: uuid.UUID`, `prompt_title: str`, `published_at: datetime`, `total: Optional[int] = None`, `per_competency: Optional[dict[str, int]] = None`), `EssayEvolutionResponse` (Pydantic model: `entries: list[EssayEvolutionEntry]`, `total_delta: Optional[int] = None`), and `async def build_evolution(session: AsyncSession, *, school_id: uuid.UUID, student_id: uuid.UUID) -> dict[str, Any]` returning a plain dict shaped like `EssayEvolutionResponse` (i.e. `{"entries": [...], "total_delta": ...}`, each entry a plain dict matching `EssayEvolutionEntry`'s fields) — routes in Task 2/3 wrap this dict with `EssayEvolutionResponse(**data)`.
- Consumes: `EssayCorrection`, `EssayPrompt`, `EssaySubmission`, `PromptAssignment` from `..db.models`; `COMPETENCY_CODES` from `..essay_engine_contract.v1`.

- [ ] **Step 1: Write the service module**

```python
"""Aggregates a student's approved essay corrections into evolution data:
one entry per APPROVED correction (most-recent first), plus the total-score
delta between the newest and oldest scored entry. Shared by the student and
teacher evolution routes (api/routes/essay_submissions.py and
api/routes/essay_corrections.py) - same shape both sides return, so neither
route repeats the aggregation query or the delta rule.

A FORMATIVO-mode correction (institution configured with no grading, spec
essay_engine_contract/v1.py) has final_scores=None - such an entry keeps
total/per_competency as None rather than defaulting to 0, so the frontend
can skip that point on every line chart instead of plotting a misleading
zero (devolutiva-rica-redacao spec's edge case §3.3, dashboard spec §3.3).
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Optional

from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import EssayCorrection, EssayPrompt, EssaySubmission, PromptAssignment
from ..essay_engine_contract.v1 import COMPETENCY_CODES


class EssayEvolutionEntry(BaseModel):
    essay_submission_id: uuid.UUID
    prompt_title: str
    published_at: datetime
    total: Optional[int] = None
    per_competency: Optional[dict[str, int]] = None


class EssayEvolutionResponse(BaseModel):
    entries: list[EssayEvolutionEntry]
    total_delta: Optional[int] = None


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


async def build_evolution(
    session: AsyncSession, *, school_id: uuid.UUID, student_id: uuid.UUID,
) -> dict[str, Any]:
    rows = (
        await session.execute(
            select(EssayCorrection, EssayPrompt.title)
            .join(EssaySubmission, EssaySubmission.id == EssayCorrection.essay_submission_id)
            .join(PromptAssignment, PromptAssignment.id == EssaySubmission.prompt_assignment_id)
            .join(EssayPrompt, EssayPrompt.id == PromptAssignment.essay_prompt_id)
            .where(
                EssaySubmission.school_id == school_id,
                EssaySubmission.student_id == student_id,
                EssayCorrection.status == "APPROVED",
            )
            .order_by(EssayCorrection.published_at.desc())
        )
    ).all()

    entries: list[dict[str, Any]] = []
    for correction, prompt_title in rows:
        scores = _as_dict(correction.final_scores)
        per_competency_raw = _as_dict(scores.get("per_competency"))
        per_competency = (
            {code: _as_dict(per_competency_raw.get(code)).get("points", 0) for code in COMPETENCY_CODES}
            if per_competency_raw
            else None
        )
        entries.append({
            "essay_submission_id": correction.essay_submission_id,
            "prompt_title": prompt_title,
            "published_at": correction.published_at,
            "total": scores.get("total"),
            "per_competency": per_competency,
        })

    # entries is newest-first (query order); the delta compares the oldest
    # SCORED entry to the newest SCORED entry, skipping any FORMATIVO gaps
    # in between - reversed() walks it chronologically without a second query.
    scored_chronological = [e["total"] for e in reversed(entries) if e["total"] is not None]
    total_delta = (
        scored_chronological[-1] - scored_chronological[0]
        if len(scored_chronological) >= 2
        else None
    )

    return {"entries": entries, "total_delta": total_delta}


__all__ = ["EssayEvolutionEntry", "EssayEvolutionResponse", "build_evolution"]
```

- [ ] **Step 2: Write the tests**

```python
import asyncio
import unittest
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayCorrection,
    EssayPrompt,
    EssaySubmission,
    GradeLevel,
    Person,
    PromptAssignment,
    School,
    Segment,
    Student,
)
from agente_ia_edu.services.essay_evolution import build_evolution


class EssayEvolutionServiceTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        cls.loop.run_until_complete(_prep())

    @classmethod
    def tearDownClass(cls):
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _seed_school_and_student(self, code: str):
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"SEV-{code}", name=f"school-{code}")
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
                person = Person(id=uuid.uuid4(), school_id=school.id, full_name=f"Aluno {code}")
                session.add(person)
                await session.flush()
                student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id, student_code=f"ST-{code}")
                session.add(student)
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title=f"Tema {code}", statement="Disserte.",
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
                return school.id, student.id, assignment.id

        return self.loop.run_until_complete(_seed())

    def _add_submission_with_correction(
        self, *, school_id, student_id, assignment_id, published_at, total=None, formativo=False,
    ):
        async def _add():
            async with self.factory() as session:
                submission = EssaySubmission(
                    id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school_id,
                    prompt_assignment_id=assignment_id, student_id=student_id,
                    mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                    canonical_text="Redacao.", normalized_text_hash="a" * 64,
                    submitted_at=published_at,
                )
                session.add(submission)
                await session.flush()
                final_scores = None if formativo else {
                    "total": total,
                    "per_competency": {
                        code: {"points": total // 5, "confidence": 1.0}
                        for code in ("C1", "C2", "C3", "C4", "C5")
                    },
                }
                session.add(EssayCorrection(
                    id=uuid.uuid4(), school_id=school_id, essay_submission_id=submission.id,
                    correction_key="k" * 64, rubric_version="ENEM_2025", model_version="gpt-test",
                    prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                    ai_output={"annotations": [], "rewrites": [], "intervention": {}, "alerts": []},
                    final_scores=final_scores, final_feedback={},
                    status="APPROVED", reviewed_at=published_at, published_at=published_at,
                ))
                await session.commit()
                return submission.id

        return self.loop.run_until_complete(_add())

    async def _call(self, school_id, student_id):
        async with self.factory() as session:
            return await build_evolution(session, school_id=school_id, student_id=student_id)

    def test_no_approved_corrections_returns_empty(self):
        school_id, student_id, _assignment_id = self._seed_school_and_student("1")
        result = self.loop.run_until_complete(self._call(school_id, student_id))
        self.assertEqual(result["entries"], [])
        self.assertIsNone(result["total_delta"])

    def test_single_correction_has_no_delta(self):
        school_id, student_id, assignment_id = self._seed_school_and_student("2")
        self._add_submission_with_correction(
            school_id=school_id, student_id=student_id, assignment_id=assignment_id,
            published_at=datetime.now(timezone.utc), total=620,
        )
        result = self.loop.run_until_complete(self._call(school_id, student_id))
        self.assertEqual(len(result["entries"]), 1)
        self.assertIsNone(result["total_delta"])
        self.assertEqual(result["entries"][0]["total"], 620)
        self.assertEqual(result["entries"][0]["per_competency"]["C1"], 124)

    def test_multiple_corrections_ordered_newest_first_with_delta(self):
        school_id, student_id, assignment_id = self._seed_school_and_student("3")
        base = datetime.now(timezone.utc)
        self._add_submission_with_correction(
            school_id=school_id, student_id=student_id, assignment_id=assignment_id,
            published_at=base, total=620,
        )
        self._add_submission_with_correction(
            school_id=school_id, student_id=student_id, assignment_id=assignment_id,
            published_at=base + timedelta(days=10), total=800,
        )
        result = self.loop.run_until_complete(self._call(school_id, student_id))
        self.assertEqual(len(result["entries"]), 2)
        self.assertEqual(result["entries"][0]["total"], 800)
        self.assertEqual(result["entries"][1]["total"], 620)
        self.assertEqual(result["total_delta"], 180)

    def test_formativo_correction_has_no_total_or_per_competency(self):
        school_id, student_id, assignment_id = self._seed_school_and_student("4")
        self._add_submission_with_correction(
            school_id=school_id, student_id=student_id, assignment_id=assignment_id,
            published_at=datetime.now(timezone.utc), formativo=True,
        )
        result = self.loop.run_until_complete(self._call(school_id, student_id))
        self.assertIsNone(result["entries"][0]["total"])
        self.assertIsNone(result["entries"][0]["per_competency"])

    def test_formativo_excluded_from_total_delta(self):
        school_id, student_id, assignment_id = self._seed_school_and_student("5")
        base = datetime.now(timezone.utc)
        self._add_submission_with_correction(
            school_id=school_id, student_id=student_id, assignment_id=assignment_id,
            published_at=base, total=620,
        )
        self._add_submission_with_correction(
            school_id=school_id, student_id=student_id, assignment_id=assignment_id,
            published_at=base + timedelta(days=5), formativo=True,
        )
        self._add_submission_with_correction(
            school_id=school_id, student_id=student_id, assignment_id=assignment_id,
            published_at=base + timedelta(days=10), total=800,
        )
        result = self.loop.run_until_complete(self._call(school_id, student_id))
        self.assertEqual(result["total_delta"], 180)

    def test_other_students_corrections_are_excluded(self):
        school_id, student_id, assignment_id = self._seed_school_and_student("6")
        _other_school_id, other_student_id, other_assignment_id = self._seed_school_and_student("7")
        self._add_submission_with_correction(
            school_id=school_id, student_id=student_id, assignment_id=assignment_id,
            published_at=datetime.now(timezone.utc), total=620,
        )
        self._add_submission_with_correction(
            school_id=_other_school_id, student_id=other_student_id, assignment_id=other_assignment_id,
            published_at=datetime.now(timezone.utc), total=999,
        )
        result = self.loop.run_until_complete(self._call(school_id, student_id))
        self.assertEqual(len(result["entries"]), 1)
        self.assertEqual(result["entries"][0]["total"], 620)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 3: Run the tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_essay_evolution.py -v`
Expected: 6 tests PASS.

- [ ] **Step 4: Commit**

```bash
git add src/agente_ia_edu/services/essay_evolution.py tests/test_essay_evolution.py
git commit -m "feat(redacao): add essay evolution aggregation service

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 2: Student evolution route

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_submissions.py` (add import + new router at end of file)
- Modify: `src/agente_ia_edu/api/app.py` (import + register the new router)
- Test: `tests/test_frontend_r_student_essay_correction_route.py` (extend with a new test class reusing this file's existing `_ident`/`_as`/`_seed_submission`/`_add_correction` helpers)

**Interfaces:**
- Consumes: `EssayEvolutionResponse`, `build_evolution` from Task 1's `..services.essay_evolution`; `_authorize_student`, `_resolve_enrollment_or_403` (already defined in this file, unmodified).
- Produces: `essay_evolution_student_router` (an `APIRouter`, prefix `/api/v1/student/essay-evolution`), registered in `app.py` alongside the file's other two routers.

- [ ] **Step 1: Add the import**

In `src/agente_ia_edu/api/routes/essay_submissions.py`, the imports block currently reads (lines 27-31):

```python
from ...services.admin import PlatformModuleKey
from ...services.authorization import AuthorizationService
from ...services.essay_correction import EssayCorrectionService
from ...services.essay_pdf_export import build_render_model, filename_for_title, pdf_available, render_pdf
from ...services.essay_submission import EssayResubmissionBlockedError, EssaySubmissionService
```

Insert a new line between `essay_correction` and `essay_pdf_export` (alphabetical, matching this block's existing order):

```python
from ...services.admin import PlatformModuleKey
from ...services.authorization import AuthorizationService
from ...services.essay_correction import EssayCorrectionService
from ...services.essay_evolution import EssayEvolutionResponse, build_evolution
from ...services.essay_pdf_export import build_render_model, filename_for_title, pdf_available, render_pdf
from ...services.essay_submission import EssayResubmissionBlockedError, EssaySubmissionService
```

- [ ] **Step 2: Append the new router at the end of the file**

The file currently ends (after `list_essay_prompts_for_student`, the last route on the second router already in this file) with:

```python
            results.append(
                EssayPromptForStudentResponse(
                    prompt_assignment_id=assignment.id,
                    title=prompt.title,
                    statement=prompt.statement,
                    due_at=assignment.due_at,
                    status=assignment.status,
                    my_submission=my_submission,
                )
            )

        return results
```

Append this new section after that (a third router in the same file — the same pattern `essay_submissions_router`/`essay_student_prompts_router` already establish here):

```python


essay_evolution_student_router = APIRouter(
    prefix="/api/v1/student/essay-evolution", tags=["essay-evolution"]
)


@essay_evolution_student_router.get("", response_model=EssayEvolutionResponse)
async def get_student_essay_evolution(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayEvolutionResponse:
    async with session_factory() as session:
        context = await _authorize_student(identity, session)
        school_id = uuid.UUID(str(context.school_id))
        enrollment = await _resolve_enrollment_or_403(
            session, school_id=school_id, external_user_id=identity.external_user_id
        )
        data = await build_evolution(session, school_id=school_id, student_id=enrollment.student_id)
        return EssayEvolutionResponse(**data)
```

- [ ] **Step 3: Register the router in `app.py`**

In `src/agente_ia_edu/api/app.py`, the import line currently reads:

```python
from .routes.essay_submissions import essay_student_prompts_router, essay_submissions_router
```

Change it to:

```python
from .routes.essay_submissions import (
    essay_evolution_student_router,
    essay_student_prompts_router,
    essay_submissions_router,
)
```

And the registration block currently reads:

```python
    app.include_router(essay_submissions_router, dependencies=reception_only_guard)
    app.include_router(essay_student_prompts_router, dependencies=reception_only_guard)
    app.include_router(essay_corrections_router, dependencies=reception_only_guard)
```

Add the new router's registration between the second and third lines:

```python
    app.include_router(essay_submissions_router, dependencies=reception_only_guard)
    app.include_router(essay_student_prompts_router, dependencies=reception_only_guard)
    app.include_router(essay_evolution_student_router, dependencies=reception_only_guard)
    app.include_router(essay_corrections_router, dependencies=reception_only_guard)
```

- [ ] **Step 4: Write the tests**

Append this new test class to `tests/test_frontend_r_student_essay_correction_route.py` (it already imports everything this class needs: `_ident`, `EssayCorrection`, `EssaySubmission`, `AcademicYear`, `Class`, `GradeLevel`, `Person`, `PromptAssignment`, `School`, `SchoolModule`, `Segment`, `Student`, `StudentEnrollment`, `User`, `UserSchoolLink`, `TestClient`, `async_sessionmaker`, etc. — reuse the same `setUpClass`/`tearDownClass`/`_as` shape as `StudentEssayCorrectionRouteTests` above it in the same file):

```python
class StudentEssayEvolutionRouteTests(unittest.TestCase):
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

    def _seed_student_with_corrections(self, code: str, totals: list):
        """Seeds one student with one submission+APPROVED correction per
        entry in ``totals`` (None entries are seeded FORMATIVO - no
        final_scores)."""
        async def _seed_async():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"SEE-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
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
                await session.flush()
                for i, total in enumerate(totals):
                    now = datetime.now(timezone.utc)
                    submission = EssaySubmission(
                        id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                        prompt_assignment_id=assignment.id, student_id=student.id,
                        mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                        canonical_text=f"Redacao {i}.", normalized_text_hash="a" * 64,
                        submitted_at=now,
                    )
                    session.add(submission)
                    await session.flush()
                    final_scores = None if total is None else {
                        "total": total,
                        "per_competency": {
                            c: {"points": total // 5, "confidence": 1.0} for c in ("C1", "C2", "C3", "C4", "C5")
                        },
                    }
                    session.add(EssayCorrection(
                        id=uuid.uuid4(), school_id=school.id, essay_submission_id=submission.id,
                        correction_key="k" * 64, rubric_version="ENEM_2025", model_version="gpt-test",
                        prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                        ai_output={"annotations": [], "rewrites": [], "intervention": {}, "alerts": []},
                        final_scores=final_scores, final_feedback={},
                        status="APPROVED", reviewed_at=now, published_at=now,
                    ))
                await session.commit()

        self.loop.run_until_complete(_seed_async())

    def test_no_approved_corrections_returns_empty_entries(self):
        self._seed_student_with_corrections("1", [])
        self._as("student_1")
        resp = self.client.get("/api/v1/student/essay-evolution")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["entries"], [])
        self.assertIsNone(body["total_delta"])

    def test_returns_own_entries_newest_first_with_delta(self):
        self._seed_student_with_corrections("2", [620, 800])
        self._as("student_2")
        resp = self.client.get("/api/v1/student/essay-evolution")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(len(body["entries"]), 2)
        self.assertEqual(body["entries"][0]["total"], 800)
        self.assertEqual(body["total_delta"], 180)

    def test_requires_student_role(self):
        self._seed_student_with_corrections("3", [620])
        self.app.dependency_overrides[get_current_identity] = lambda: _ident("nobody")
        resp = self.client.get("/api/v1/student/essay-evolution")
        self.assertEqual(resp.status_code, 403, resp.text)
```

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_frontend_r_student_essay_correction_route.py -v`
Expected: all tests (existing + 3 new) PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_submissions.py src/agente_ia_edu/api/app.py tests/test_frontend_r_student_essay_correction_route.py
git commit -m "feat(redacao): add student essay evolution route

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 3: Teacher evolution routes (evolution + student search)

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_corrections.py` (add import + two new routes on a new router at end of file)
- Modify: `src/agente_ia_edu/api/app.py` (import + register the new router)
- Test: `tests/test_r3_essay_corrections_routes.py` (extend with a new test class)

**Interfaces:**
- Consumes: `EssayEvolutionResponse`, `build_evolution` from Task 1's `..services.essay_evolution`; `_authorize` (already defined in this file, unmodified); `Person`, `Student`, `EssayCorrection`, `EssaySubmission` (already imported in this file).
- Produces: `essay_evolution_teacher_router` (an `APIRouter`, prefix `/api/v1/teacher/essay-evolution`), registered in `app.py`.

- [ ] **Step 1: Add the import**

In `src/agente_ia_edu/api/routes/essay_corrections.py`, the imports block currently reads:

```python
from ...services.authorization import AuthorizationService
from ...services.essay_correction import EssayCorrectionService
from ...services.essay_pdf_export import build_render_model, filename_for_title, pdf_available, render_pdf
```

Insert a new line between `essay_correction` and `essay_pdf_export`:

```python
from ...services.authorization import AuthorizationService
from ...services.essay_correction import EssayCorrectionService
from ...services.essay_evolution import EssayEvolutionResponse, build_evolution
from ...services.essay_pdf_export import build_render_model, filename_for_title, pdf_available, render_pdf
```

- [ ] **Step 2: Append the new router at the end of the file**

The file currently ends (in `get_essay_correction_page_image`) with:

```python
        page = await session.scalar(
            select(EssaySubmissionPage).where(
                EssaySubmissionPage.essay_submission_id == correction.essay_submission_id,
                EssaySubmissionPage.page_number == page_number,
            )
        )
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found")
```

(followed by the rest of that function, unchanged). Append this new section at the end of the file:

```python


class EssayEvolutionStudentItem(BaseModel):
    student_id: uuid.UUID
    student_name: str


essay_evolution_teacher_router = APIRouter(
    prefix="/api/v1/teacher/essay-evolution", tags=["essay-evolution"]
)


@essay_evolution_teacher_router.get("", response_model=EssayEvolutionResponse)
async def get_teacher_essay_evolution(
    student_id: uuid.UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayEvolutionResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        data = await build_evolution(session, school_id=school_id, student_id=student_id)
        return EssayEvolutionResponse(**data)


@essay_evolution_teacher_router.get("/students", response_model=list[EssayEvolutionStudentItem])
async def list_essay_evolution_students(
    q: Optional[str] = None,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[EssayEvolutionStudentItem]:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        stmt = (
            select(Student.id, Person.full_name)
            .join(Person, Person.id == Student.person_id)
            .join(EssaySubmission, EssaySubmission.student_id == Student.id)
            .join(EssayCorrection, EssayCorrection.essay_submission_id == EssaySubmission.id)
            .where(Student.school_id == school_id, EssayCorrection.status == "APPROVED")
            .distinct()
            .order_by(Person.full_name)
        )
        if q:
            stmt = stmt.where(Person.full_name.ilike(f"%{q}%"))
        rows = (await session.execute(stmt)).all()
        return [
            EssayEvolutionStudentItem(student_id=row_student_id, student_name=row_student_name)
            for row_student_id, row_student_name in rows
        ]
```

- [ ] **Step 3: Register the router in `app.py`**

In `src/agente_ia_edu/api/app.py`, the import line currently reads:

```python
from .routes.essay_corrections import essay_corrections_router
```

Change it to:

```python
from .routes.essay_corrections import essay_corrections_router, essay_evolution_teacher_router
```

And add its registration right after `essay_corrections_router`'s own registration:

```python
    app.include_router(essay_corrections_router, dependencies=reception_only_guard)
    app.include_router(essay_evolution_teacher_router, dependencies=reception_only_guard)
```

- [ ] **Step 4: Write the tests**

Append this new test class to `tests/test_r3_essay_corrections_routes.py` (it already imports `EssayCorrection`, `EssayPrompt`, `EssaySubmission`, `Person`, `PromptAssignment`, `School`, `Student`, `UserSchoolLink`, `_ident`, `TestClient`, `create_app`, etc.):

```python
class EssayEvolutionTeacherRoutesTests(unittest.TestCase):
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

    def _seed_school_with_teacher(self, code: str):
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"EVT-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id=f"teacher_{code}", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                await session.commit()
                return school.id

        return self.loop.run_until_complete(_seed())

    def _seed_student_with_correction(self, code: str, school_id, *, student_name: str, total):
        async def _seed():
            async with self.factory() as session:
                person = Person(id=uuid.uuid4(), school_id=school_id, full_name=student_name)
                session.add(person)
                await session.flush()
                student = Student(id=uuid.uuid4(), school_id=school_id, person_id=person.id, student_code=f"ST-{code}")
                session.add(student)
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school_id, title="Tema", statement="Disserte.",
                    year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
                )
                session.add(prompt)
                await session.flush()
                assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=school_id, essay_prompt_id=prompt.id,
                    class_id=uuid.uuid4(), assigned_by_external_identity="teacher:t",
                )
                session.add(assignment)
                await session.flush()
                now = datetime.now(timezone.utc)
                submission = EssaySubmission(
                    id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school_id,
                    prompt_assignment_id=assignment.id, student_id=student.id,
                    mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                    canonical_text="Redacao.", normalized_text_hash="a" * 64,
                    submitted_at=now,
                )
                session.add(submission)
                await session.flush()
                session.add(EssayCorrection(
                    id=uuid.uuid4(), school_id=school_id, essay_submission_id=submission.id,
                    correction_key="k" * 64, rubric_version="ENEM_2025", model_version="gpt-test",
                    prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                    ai_output={"annotations": [], "rewrites": [], "intervention": {}, "alerts": []},
                    final_scores={
                        "total": total,
                        "per_competency": {
                            c: {"points": total // 5, "confidence": 1.0} for c in ("C1", "C2", "C3", "C4", "C5")
                        },
                    },
                    final_feedback={}, status="APPROVED", reviewed_at=now, published_at=now,
                ))
                await session.commit()
                return student.id

        return self.loop.run_until_complete(_seed())

    def test_evolution_for_student_in_own_school(self):
        school_id = self._seed_school_with_teacher("1")
        student_id = self._seed_student_with_correction("1", school_id, student_name="Ana", total=700)
        self._as("teacher_1")
        resp = self.client.get(f"/api/v1/teacher/essay-evolution?student_id={student_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(len(body["entries"]), 1)
        self.assertEqual(body["entries"][0]["total"], 700)

    def test_evolution_for_student_in_another_school_returns_empty(self):
        school_id = self._seed_school_with_teacher("2")
        other_school_id = self._seed_school_with_teacher("3")
        other_student_id = self._seed_student_with_correction(
            "3", other_school_id, student_name="Bia", total=700,
        )
        self._as("teacher_2")
        resp = self.client.get(f"/api/v1/teacher/essay-evolution?student_id={other_student_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        # school_id scoping in build_evolution() means a student from a
        # DIFFERENT school simply has no matching rows, not a 403 - same
        # non-restrictive-by-classroom rule list_essay_corrections already
        # uses (dashboard spec §4).
        self.assertEqual(resp.json()["entries"], [])

    def test_list_students_filters_by_school_and_name(self):
        school_id = self._seed_school_with_teacher("4")
        self._seed_student_with_correction("4a", school_id, student_name="Carla Souza", total=700)
        self._seed_student_with_correction("4b", school_id, student_name="Daniel Reis", total=650)
        self._as("teacher_4")
        resp = self.client.get("/api/v1/teacher/essay-evolution/students")
        self.assertEqual(resp.status_code, 200, resp.text)
        names = sorted(row["student_name"] for row in resp.json())
        self.assertEqual(names, ["Carla Souza", "Daniel Reis"])

        resp = self.client.get("/api/v1/teacher/essay-evolution/students?q=carla")
        self.assertEqual(resp.status_code, 200, resp.text)
        names = [row["student_name"] for row in resp.json()]
        self.assertEqual(names, ["Carla Souza"])

    def test_list_students_excludes_students_without_approved_corrections(self):
        school_id = self._seed_school_with_teacher("5")
        # A student who exists but has no APPROVED correction yet must not
        # show up in the selector (dashboard spec §2: "lists students ...
        # with >= 1 approved correction").
        async def _seed_unapproved():
            async with self.factory() as session:
                person = Person(id=uuid.uuid4(), school_id=school_id, full_name="Sem Correção")
                session.add(person)
                await session.flush()
                session.add(Student(id=uuid.uuid4(), school_id=school_id, person_id=person.id, student_code="ST-5x"))
                await session.commit()

        self.loop.run_until_complete(_seed_unapproved())
        self._as("teacher_5")
        resp = self.client.get("/api/v1/teacher/essay-evolution/students")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json(), [])
```

- [ ] **Step 5: Run the tests**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_r3_essay_corrections_routes.py -v`
Expected: all tests (existing + 4 new) PASS.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_corrections.py src/agente_ia_edu/api/app.py tests/test_r3_essay_corrections_routes.py
git commit -m "feat(redacao): add teacher essay evolution and student search routes

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 4: Shared frontend module (`essay-evolution.js`) + `essay-report.js` refactor

**Files:**
- Modify: `src/agente_ia_edu/web/essay-report.js` (extract `renderCompetencyChecklist` so both the existing devolutiva and the new checklist section use one function)
- Create: `src/agente_ia_edu/web/essay-evolution.js`
- Modify: `src/agente_ia_edu/web/index.html` (add script tag)
- Modify: `src/agente_ia_edu/web/teacher.html` (add script tag)

**Interfaces:**
- Produces (from `essay-report.js`, added to its existing return statement): `renderCompetencyChecklist(rationales, feedbackStrengths, esc) -> string` (HTML: the competency table, or its empty state, plus the strengths-fallback list when no rationale has both strengths/growth_area).
- Produces (from `essay-evolution.js`, new `window.EssayEvolution`): `renderEvolutionSection(data, checklistData) -> string` where `data` is the exact JSON body of `GET .../essay-evolution` (`{entries: [...], total_delta}`) and `checklistData` is `{rationales: array, feedbackStrengths: array}`; `wireEvolutionSection(rootEl, data)` — call after inserting the returned HTML into the DOM, wires hover/tap popovers on every chart point.
- Consumes: nothing from other new modules — `essay-evolution.js` defines its own `esc()` (matching every other module's own-local-escaper convention) and its own competency color/label constants (matching the fixed hex values in Global Constraints).

- [ ] **Step 1: Refactor `essay-report.js`**

In `src/agente_ia_edu/web/essay-report.js`, replace this block (currently lines 70-86):

```javascript
    const rationaleByCode = {};
    rationales.forEach((r) => { rationaleByCode[r.competency_code] = r; });
    const competencyTableRows = Object.keys(COMPETENCY_LABELS).map((code) => {
      const rationale = rationaleByCode[code];
      if (!rationale) return '';
      const hasSplit = rationale.strengths && rationale.growth_area;
      const cells = hasSplit
        ? `<td>${esc(rationale.strengths)}</td><td>${esc(rationale.growth_area)}</td>`
        : `<td colspan="2">${esc(rationale.summary || '')}</td>`;
      return `<tr><th scope="row" class="essay-mark-${code}">${code} — ${esc(COMPETENCY_LABELS[code])}</th>${cells}</tr>`;
    }).join('');
    const competencyTableHtml = competencyTableRows
      ? `<table class="essay-competency-table">
          <thead><tr><th>Competência</th><th>Você já faz bem</th><th>Onde pode avançar</th></tr></thead>
          <tbody>${competencyTableRows}</tbody>
        </table>`
      : '<p class="empty-text">Nenhuma avaliação por competência.</p>';
```

with:

```javascript
    const competencyTableHtml = renderCompetencyChecklist(rationales, feedback.strengths, esc);
```

Then remove the now-duplicate `hasAnyRationaleSplit`/`strengthsFallbackHtml` block (currently lines 156-159):

```javascript
    const hasAnyRationaleSplit = rationales.some((r) => r.strengths && r.growth_area);
    const strengthsFallbackHtml = (!hasAnyRationaleSplit && (feedback.strengths || []).length)
      ? `<h4>Pontos fortes</h4><ul>${feedback.strengths.map((s) => `<li>${esc(s)}</li>`).join('')}</ul>`
      : '';
```

entirely (delete it — `renderCompetencyChecklist` now folds the fallback into `competencyTableHtml`).

In the function's final template literal (currently around line 167-191), remove the `${strengthsFallbackHtml}` line — `competencyTableHtml` already includes it:

```javascript
      <h4>O que você já faz bem e onde pode avançar</h4>
      ${competencyTableHtml}
      ${strengthsFallbackHtml}
      <h4>Sua redação</h4>
```

becomes:

```javascript
      <h4>O que você já faz bem e onde pode avançar</h4>
      ${competencyTableHtml}
      <h4>Sua redação</h4>
```

Now add the new `renderCompetencyChecklist` function, right before `renderRichReport` (so it's defined before use — this codebase's IIFE factory function body allows either order, but keeping the smaller pure helper first reads cleaner, matching `escEssay`/`translateDetail` coming before their usage sites in `essay.js`):

```javascript
  function renderCompetencyChecklist(rationales, feedbackStrengths, esc) {
    const rationaleByCode = {};
    (rationales || []).forEach((r) => { rationaleByCode[r.competency_code] = r; });
    const competencyTableRows = Object.keys(COMPETENCY_LABELS).map((code) => {
      const rationale = rationaleByCode[code];
      if (!rationale) return '';
      const hasSplit = rationale.strengths && rationale.growth_area;
      const cells = hasSplit
        ? `<td>${esc(rationale.strengths)}</td><td>${esc(rationale.growth_area)}</td>`
        : `<td colspan="2">${esc(rationale.summary || '')}</td>`;
      return `<tr><th scope="row" class="essay-mark-${code}">${code} — ${esc(COMPETENCY_LABELS[code])}</th>${cells}</tr>`;
    }).join('');
    const competencyTableHtml = competencyTableRows
      ? `<table class="essay-competency-table">
          <thead><tr><th>Competência</th><th>Você já faz bem</th><th>Onde pode avançar</th></tr></thead>
          <tbody>${competencyTableRows}</tbody>
        </table>`
      : '<p class="empty-text">Nenhuma avaliação por competência.</p>';
    const hasAnyRationaleSplit = (rationales || []).some((r) => r.strengths && r.growth_area);
    const strengthsFallbackHtml = (!hasAnyRationaleSplit && (feedbackStrengths || []).length)
      ? `<h4>Pontos fortes</h4><ul>${feedbackStrengths.map((s) => `<li>${esc(s)}</li>`).join('')}</ul>`
      : '';
    return competencyTableHtml + strengthsFallbackHtml;
  }
```

Finally, update the module's return statement (currently `return { renderRichReport };`) to:

```javascript
  return { renderRichReport, renderCompetencyChecklist };
```

- [ ] **Step 2: Manually verify the refactor didn't change existing behavior**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_essay_pdf_export.py -v`
Expected: all tests PASS unchanged (this file doesn't test `essay-report.js` directly — it's JS with no test file, per this repo's frontend-has-no-tests convention — but `essay_pdf_export.py`'s render model tests share the same field-fallback philosophy this refactor touches, so a regression there is the closest automated signal available; Task 8's manual browser check is what actually proves the refactor).

- [ ] **Step 3: Write the new shared module**

Create `src/agente_ia_edu/web/essay-evolution.js`:

```javascript
/* AGENTE IA EDU — módulo compartilhado do dashboard de evolução de redação.
   Carregado nos dois portais, depois de essay-report.js (usa
   window.EssayReport.renderCompetencyChecklist para a seção "o que está
   bom / o que precisa melhorar") e antes de essay.js/essay-review.js.
   Funções puras: renderEvolutionSection monta uma string HTML e não toca o
   DOM - quem chama decide onde inserir e chama wireEvolutionSection depois
   para ligar os popovers dos pontos do gráfico. */
(function essayEvolutionModule(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.EssayEvolution = api;
})(typeof window !== 'undefined' ? window : null, function createEssayEvolution() {
  const COMPETENCY_LABELS = {
    C1: 'Domínio da norma padrão', C2: 'Compreensão do tema', C3: 'Argumentação',
    C4: 'Coesão textual', C5: 'Proposta de intervenção',
  };
  const COMPETENCY_CODES = ['C1', 'C2', 'C3', 'C4', 'C5'];
  // Same hex values already used on screen (styles.css's --primary/--accent/
  // --danger/--warning/--success) and in the PDF export
  // (essay_pdf_export.py's _COMPETENCY_SOLID_COLORS) - kept as literal hex
  // here (not var(--...)) so the SVG stroke/fill attributes render
  // correctly even where CSS custom properties in SVG attributes aren't
  // supported.
  const COMPETENCY_COLORS = {
    C1: '#4f46e5', C2: '#06b6d4', C3: '#ef4444', C4: '#f59e0b', C5: '#10b981',
  };
  const TOTAL_COLOR = '#1e293b';

  function esc(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[character]);
  }

  function fmtDate(iso) {
    return new Date(iso).toLocaleDateString('pt-BR');
  }

  // --- timeline cards ---

  function renderTimelineCards(entries) {
    if (!entries.length) {
      return '<p class="empty-text">Vamos ver sua evolução assim que sua primeira redação for corrigida.</p>';
    }
    const cardsHtml = entries.map((entry) => {
      const barsHtml = COMPETENCY_CODES.map((code) => {
        const points = entry.per_competency ? entry.per_competency[code] : null;
        const pct = points != null ? Math.round((points / 200) * 100) : 0;
        return `
          <div>
            <div class="essay-evolution-mini-label">${code}</div>
            <div class="essay-evolution-mini-track"><div class="essay-evolution-mini-fill essay-mark-${code}" style="width:${pct}%"></div></div>
          </div>`;
      }).join('');
      const totalHtml = entry.total != null ? `${entry.total}/1000` : 'Sem nota (formativo)';
      return `
        <div class="card essay-evolution-card">
          <div class="essay-evolution-card-head">
            <span>${fmtDate(entry.published_at)} — ${esc(entry.prompt_title)}</span>
            <span class="essay-evolution-card-total">${totalHtml}</span>
          </div>
          <div class="essay-evolution-mini-bars">${barsHtml}</div>
        </div>`;
    }).join('');
    return `<div class="essay-evolution-timeline">${cardsHtml}</div>`;
  }

  function renderDeltaSummary(totalDelta) {
    if (totalDelta == null) return '';
    const verb = totalDelta >= 0 ? 'subiu' : 'desceu';
    return `<p class="essay-evolution-summary">Sua nota total ${verb} ${Math.abs(totalDelta)} pontos desde a primeira redação.</p>`;
  }

  // --- SVG line charts: one per competency (0-200) plus one for total (0-1000) ---

  function buildSeries(entries, key) {
    // entries chega do backend em ordem mais-recente-primeiro; o gráfico é
    // lido da esquerda (mais antiga) para a direita (mais recente).
    const chronological = entries.slice().reverse();
    return chronological
      .map((entry) => ({
        value: key === 'total' ? entry.total : (entry.per_competency ? entry.per_competency[key] : null),
        dateLabel: fmtDate(entry.published_at),
        fullLabel: `${fmtDate(entry.published_at)} — ${entry.prompt_title}`,
      }))
      .filter((point) => point.value != null);
  }

  function renderLineChart(points, opts) {
    const { max, color, title, chartIndex } = opts;
    if (!points.length) {
      return `
        <div class="essay-evolution-chart-card" data-chart-index="${chartIndex}">
          <div class="essay-evolution-chart-title"><span class="essay-evolution-chart-badge" style="background:${color}"></span>${esc(title)}</div>
          <p class="empty-text">Sem dados suficientes ainda.</p>
        </div>`;
    }
    const left = 50;
    const right = 500;
    const top = 20;
    const bottom = 180;
    const stepX = points.length > 1 ? (right - left) / (points.length - 1) : 0;
    const coords = points.map((p, i) => ({
      x: points.length > 1 ? left + i * stepX : (left + right) / 2,
      y: bottom - (Math.max(0, Math.min(p.value, max)) / max) * (bottom - top),
      value: p.value, dateLabel: p.dateLabel,
    }));
    const gridHtml = [top, (top + bottom) / 2, bottom]
      .map((y) => `<line x1="${left}" y1="${y}" x2="${right}" y2="${y}" stroke="#eef1f5"/>`).join('');
    const axisLabelsHtml = `
      <text x="10" y="${top + 4}" class="essay-evolution-axis-label">${max}</text>
      <text x="10" y="${(top + bottom) / 2 + 4}" class="essay-evolution-axis-label">${Math.round(max / 2)}</text>
      <text x="10" y="${bottom + 4}" class="essay-evolution-axis-label">0</text>`;
    const polylineHtml = coords.length > 1
      ? `<polyline points="${coords.map((c) => `${c.x},${c.y}`).join(' ')}" fill="none" stroke="${color}" stroke-width="2.5"/>`
      : '';
    const pointsHtml = coords.map((c, i) => `
      <circle cx="${c.x}" cy="${c.y}" r="5" fill="${color}" data-evolution-point="${i}" tabindex="0"></circle>
      <text x="${c.x}" y="${bottom + 20}" text-anchor="middle" class="essay-evolution-pt-label">${esc(c.dateLabel)}</text>
      <text x="${c.x}" y="${Math.max(top + 10, c.y - 10)}" text-anchor="middle" class="essay-evolution-pt-value">${c.value}</text>`).join('');
    return `
      <div class="essay-evolution-chart-card" data-chart-index="${chartIndex}">
        <div class="essay-evolution-chart-title"><span class="essay-evolution-chart-badge" style="background:${color}"></span>${esc(title)}</div>
        <svg viewBox="0 0 520 220" width="100%">
          ${gridHtml}
          ${axisLabelsHtml}
          ${polylineHtml}
          ${pointsHtml}
        </svg>
      </div>`;
  }

  function renderCharts(entries) {
    const competencyChartsHtml = COMPETENCY_CODES.map((code, i) => renderLineChart(
      buildSeries(entries, code),
      { max: 200, color: COMPETENCY_COLORS[code], title: `${code} — ${COMPETENCY_LABELS[code]}`, chartIndex: i },
    )).join('');
    const totalChartHtml = renderLineChart(
      buildSeries(entries, 'total'),
      { max: 1000, color: TOTAL_COLOR, title: 'Nota total', chartIndex: COMPETENCY_CODES.length },
    );
    return `<div class="essay-evolution-charts">${competencyChartsHtml}${totalChartHtml}</div>`;
  }

  // --- chart point popovers (fixed-position div, positioning technique
  // copied from essay-annotations.js's showPopover/closePopover; content
  // shape is different here - a chart point, not an annotation - so this
  // is a small local reimplementation rather than a shared function) ---

  let openChartPopover = null;

  function closeChartPopover() {
    if (openChartPopover) { openChartPopover.remove(); openChartPopover = null; }
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('click', (ev) => {
      if (openChartPopover && !openChartPopover.contains(ev.target) && !ev.target.closest('[data-evolution-point]')) {
        closeChartPopover();
      }
    });
  }

  function showChartPopover(rootEl, pointEl, point) {
    closeChartPopover();
    const popover = document.createElement('div');
    popover.className = 'essay-popover';
    popover.innerHTML = `<strong>${esc(point.fullLabel)}</strong><p>${esc(String(point.value))}</p>`;
    rootEl.appendChild(popover);
    popover.style.position = 'fixed';
    const pointRect = pointEl.getBoundingClientRect();
    const popRect = popover.getBoundingClientRect();
    let left = pointRect.left;
    let top = pointRect.bottom + 6;
    if (left + popRect.width > window.innerWidth) left = window.innerWidth - popRect.width - 8;
    if (top + popRect.height > window.innerHeight) top = pointRect.top - popRect.height - 6;
    popover.style.left = `${Math.max(8, left)}px`;
    popover.style.top = `${Math.max(8, top)}px`;
    openChartPopover = popover;
  }

  function wireChartPopovers(rootEl, seriesByChart) {
    rootEl.querySelectorAll('[data-evolution-point]').forEach((pointEl) => {
      const chartCard = pointEl.closest('[data-chart-index]');
      if (!chartCard) return;
      const chartIndex = Number(chartCard.dataset.chartIndex);
      const pointIndex = Number(pointEl.dataset.evolutionPoint);
      const point = (seriesByChart[chartIndex] || [])[pointIndex];
      if (!point) return;
      pointEl.addEventListener('mouseenter', () => showChartPopover(rootEl, pointEl, point));
      pointEl.addEventListener('mouseleave', closeChartPopover);
      pointEl.addEventListener('click', (ev) => {
        ev.stopPropagation();
        showChartPopover(rootEl, pointEl, point);
      });
    });
  }

  // --- public API ---

  function renderEvolutionSection(data, checklistData) {
    const entries = data.entries || [];
    const checklist = checklistData || { rationales: [], feedbackStrengths: [] };
    const checklistHtml = window.EssayReport.renderCompetencyChecklist(
      checklist.rationales, checklist.feedbackStrengths, esc,
    );
    return `
      ${renderDeltaSummary(data.total_delta)}
      ${renderTimelineCards(entries)}
      <h4 class="essay-evolution-section-title">Evolução por competência</h4>
      ${renderCharts(entries)}
      <h4 class="essay-evolution-section-title">O que está bom e o que precisa melhorar</h4>
      ${checklistHtml}`;
  }

  function wireEvolutionSection(rootEl, data) {
    const entries = data.entries || [];
    const seriesByChart = COMPETENCY_CODES.map((code) => buildSeries(entries, code));
    seriesByChart.push(buildSeries(entries, 'total'));
    wireChartPopovers(rootEl, seriesByChart);
  }

  return { renderEvolutionSection, wireEvolutionSection };
});
```

- [ ] **Step 4: Register the script in both portals**

In `src/agente_ia_edu/web/index.html`, currently:

```html
  <script src="essay-annotations.js"></script>
  <script src="essay-report.js"></script>
  <script src="essay.js"></script>
```

becomes:

```html
  <script src="essay-annotations.js"></script>
  <script src="essay-report.js"></script>
  <script src="essay-evolution.js"></script>
  <script src="essay.js"></script>
```

In `src/agente_ia_edu/web/teacher.html`, currently:

```html
  <script src="essay-annotations.js"></script>
  <script src="essay-report.js"></script>
  <script src="essay-review.js"></script>
```

becomes:

```html
  <script src="essay-annotations.js"></script>
  <script src="essay-report.js"></script>
  <script src="essay-evolution.js"></script>
  <script src="essay-review.js"></script>
```

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/web/essay-report.js src/agente_ia_edu/web/essay-evolution.js src/agente_ia_edu/web/index.html src/agente_ia_edu/web/teacher.html
git commit -m "feat(redacao): add shared evolution dashboard rendering module

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 5: Student portal integration (`essay.js`)

**Files:**
- Modify: `src/agente_ia_edu/web/essay.js`

**Interfaces:**
- Consumes: `window.EssayEvolution.renderEvolutionSection`/`wireEvolutionSection` (Task 4); `GET /api/v1/student/essay-evolution` (Task 2); the already-existing `GET /api/v1/student/essay-submissions/{id}/correction` (fetches the most recent entry's full correction, for the checklist).

- [ ] **Step 1: Add the evolution section below the prompt list**

In `src/agente_ia_edu/web/essay.js`, replace the current `renderList` function body:

```javascript
  function renderList() {
    if (!prompts.length) {
      container.innerHTML = '<div class="card"><p class="empty-text">Nenhuma proposta de redação aberta para sua turma no momento.</p></div>';
      return;
    }
    container.innerHTML = `<div class="essay-prompt-grid">${prompts.map((p) => {
      const state = submissionState(p.my_submission);
      const dueText = p.due_at ? `<p class="empty-text">Prazo: ${new Date(p.due_at).toLocaleDateString('pt-BR')}</p>` : '';
      return `
        <article class="card essay-prompt-card">
          <h3>${escEssay(p.title)}</h3>
          <p>${escEssay(p.statement)}</p>
          ${dueText}
          <span class="badge ${state.badge}">${state.label}</span>
          <button class="btn btn-primary" type="button" data-open-prompt="${escEssay(p.prompt_assignment_id)}">${state.label}</button>
        </article>`;
    }).join('')}</div>`;

    container.querySelectorAll('[data-open-prompt]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const prompt = prompts.find((p) => p.prompt_assignment_id === btn.dataset.openPrompt);
        openPrompt(prompt);
      });
    });
  }
```

with:

```javascript
  function renderList() {
    const promptsHtml = prompts.length
      ? `<div class="essay-prompt-grid">${prompts.map((p) => {
          const state = submissionState(p.my_submission);
          const dueText = p.due_at ? `<p class="empty-text">Prazo: ${new Date(p.due_at).toLocaleDateString('pt-BR')}</p>` : '';
          return `
            <article class="card essay-prompt-card">
              <h3>${escEssay(p.title)}</h3>
              <p>${escEssay(p.statement)}</p>
              ${dueText}
              <span class="badge ${state.badge}">${state.label}</span>
              <button class="btn btn-primary" type="button" data-open-prompt="${escEssay(p.prompt_assignment_id)}">${state.label}</button>
            </article>`;
        }).join('')}</div>`
      : '<div class="card"><p class="empty-text">Nenhuma proposta de redação aberta para sua turma no momento.</p></div>';

    container.innerHTML = `${promptsHtml}<div id="essay-evolution-section" class="essay-evolution-root"><p class="empty-text">Carregando evolução...</p></div>`;

    container.querySelectorAll('[data-open-prompt]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const prompt = prompts.find((p) => p.prompt_assignment_id === btn.dataset.openPrompt);
        openPrompt(prompt);
      });
    });

    loadEvolutionSection();
  }

  async function loadEvolutionSection() {
    const section = container.querySelector('#essay-evolution-section');
    if (!section) return;
    let data;
    try {
      data = await essayRequest('/api/v1/student/essay-evolution');
    } catch (e) {
      section.innerHTML = `<p class="empty-text">${escEssay(e.message)}</p>`;
      return;
    }
    if (!data.entries.length) {
      section.innerHTML = '<p class="empty-text">Vamos ver sua evolução assim que sua primeira redação for corrigida.</p>';
      return;
    }
    let checklistData = { rationales: [], feedbackStrengths: [] };
    try {
      const mostRecent = await essayRequest(
        `/api/v1/student/essay-submissions/${data.entries[0].essay_submission_id}/correction`,
      );
      checklistData = {
        rationales: mostRecent.rationales || [],
        feedbackStrengths: (mostRecent.final_feedback || {}).strengths || [],
      };
    } catch (e) {
      // Checklist degrades to its own empty state below - the timeline and
      // charts above it (already rendered from `data`) don't depend on this
      // second fetch succeeding.
    }
    section.innerHTML = window.EssayEvolution.renderEvolutionSection(data, checklistData);
    window.EssayEvolution.wireEvolutionSection(section, data);
  }
```

- [ ] **Step 2: Commit**

```bash
git add src/agente_ia_edu/web/essay.js
git commit -m "feat(redacao): show evolution dashboard in student essay module

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 6: Teacher portal integration (`essay-review.js`)

**Files:**
- Modify: `src/agente_ia_edu/web/essay-review.js`

**Interfaces:**
- Consumes: `window.EssayEvolution.renderEvolutionSection`/`wireEvolutionSection` (Task 4); `GET /api/v1/teacher/essay-evolution/students?q=...` and `GET /api/v1/teacher/essay-evolution?student_id=...` (Task 3); the already-existing `GET /api/v1/teacher/essay-corrections?status=APPROVED` (fetches the full correction list, filtered client-side by `essay_submission_id` to find the most recent entry's rationales for the checklist — same fetch-a-list-then-find-by-id pattern `renderReviewPanel` already uses via `currentCorrections.find(...)`).

- [ ] **Step 1: Add the "Evolução" tab**

In `src/agente_ia_edu/web/essay-review.js`, replace `renderTabs`:

```javascript
  function renderTabs(activeTab) {
    return `
      <div class="essay-review-tabs">
        <button class="btn ${activeTab === 'prompts' ? 'btn-primary' : 'btn-secondary'}" type="button" data-tab="prompts">Propostas</button>
        <button class="btn ${activeTab === 'queue' ? 'btn-primary' : 'btn-secondary'}" type="button" data-tab="queue">Fila de Revisão</button>
      </div>`;
  }
```

with:

```javascript
  function renderTabs(activeTab) {
    return `
      <div class="essay-review-tabs">
        <button class="btn ${activeTab === 'prompts' ? 'btn-primary' : 'btn-secondary'}" type="button" data-tab="prompts">Propostas</button>
        <button class="btn ${activeTab === 'queue' ? 'btn-primary' : 'btn-secondary'}" type="button" data-tab="queue">Fila de Revisão</button>
        <button class="btn ${activeTab === 'evolution' ? 'btn-primary' : 'btn-secondary'}" type="button" data-tab="evolution">Evolução</button>
      </div>`;
  }
```

Replace `wireTabs`:

```javascript
  function wireTabs() {
    container.querySelectorAll('[data-tab]').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (btn.dataset.tab === 'prompts') renderPromptsList();
        if (btn.dataset.tab === 'queue') renderReviewQueue();
      });
    });
  }
```

with:

```javascript
  function wireTabs() {
    container.querySelectorAll('[data-tab]').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (btn.dataset.tab === 'prompts') renderPromptsList();
        if (btn.dataset.tab === 'queue') renderReviewQueue();
        if (btn.dataset.tab === 'evolution') renderEvolutionTab();
      });
    });
  }
```

- [ ] **Step 2: Add the tab's rendering function**

Add this new function after `renderReviewQueue` (or anywhere else at the module's top level — order among sibling functions in this file doesn't matter, they're all hoisted function declarations):

```javascript
  async function renderEvolutionTab() {
    container.innerHTML = `
      ${renderTabs('evolution')}
      <div class="tm-form-row" style="margin: 12px 0;">
        <div class="form-group">
          <label for="er-evolution-search">Buscar aluno</label>
          <input id="er-evolution-search" class="text-input" placeholder="Nome do aluno">
        </div>
      </div>
      <div id="er-evolution-students"></div>
      <div id="er-evolution-body"></div>`;
    wireTabs();

    const searchInput = container.querySelector('#er-evolution-search');
    const studentsList = container.querySelector('#er-evolution-students');
    const body = container.querySelector('#er-evolution-body');

    async function loadStudents(q) {
      let students = [];
      try {
        students = await reviewRequest(`/api/v1/teacher/essay-evolution/students${q ? `?q=${encodeURIComponent(q)}` : ''}`);
      } catch (e) {
        studentsList.innerHTML = `<p class="empty-text">${tmEsc(e.message)}</p>`;
        return;
      }
      studentsList.innerHTML = students.length
        ? `<ul class="essay-evolution-student-list">${students.map((s) => `
            <li><button class="btn btn-secondary" type="button" data-select-student="${tmEsc(s.student_id)}">${tmEsc(s.student_name)}</button></li>`).join('')}</ul>`
        : '<p class="empty-text">Nenhum aluno com redação corrigida ainda.</p>';
      studentsList.querySelectorAll('[data-select-student]').forEach((btn) => {
        btn.addEventListener('click', () => loadEvolutionForStudent(btn.dataset.selectStudent));
      });
    }

    async function loadEvolutionForStudent(studentId) {
      body.innerHTML = '<p class="empty-text">Carregando evolução...</p>';
      let data;
      try {
        data = await reviewRequest(`/api/v1/teacher/essay-evolution?student_id=${encodeURIComponent(studentId)}`);
      } catch (e) {
        body.innerHTML = `<p class="empty-text">${tmEsc(e.message)}</p>`;
        return;
      }
      if (!data.entries.length) {
        body.innerHTML = '<p class="empty-text">Este aluno ainda não tem redação aprovada.</p>';
        return;
      }
      let checklistData = { rationales: [], feedbackStrengths: [] };
      try {
        const approved = await reviewRequest('/api/v1/teacher/essay-corrections?status=APPROVED');
        const match = approved.find((c) => c.essay_submission_id === data.entries[0].essay_submission_id);
        if (match) {
          checklistData = {
            rationales: (match.ai_output || {}).rationales || [],
            feedbackStrengths: (match.final_feedback || {}).strengths || [],
          };
        }
      } catch (e) {
        // Checklist degrades to its own empty state below.
      }
      body.innerHTML = window.EssayEvolution.renderEvolutionSection(data, checklistData);
      window.EssayEvolution.wireEvolutionSection(body, data);
    }

    searchInput.addEventListener('input', () => loadStudents(searchInput.value.trim()));
    await loadStudents('');
  }
```

- [ ] **Step 3: Commit**

```bash
git add src/agente_ia_edu/web/essay-review.js
git commit -m "feat(redacao): add evolution tab with student search to teacher review

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 7: CSS

**Files:**
- Modify: `src/agente_ia_edu/web/styles.css`

**Interfaces:**
- Consumes: existing CSS variables (`--success`, `--success-light`, `--text-muted`, `--border`) and the existing `.essay-mark-C1`..`.essay-mark-C5`/`.essay-popover`/`.card`/`.empty-text`/`.btn`/`.text-input` classes (all already defined — no changes to any of them).

- [ ] **Step 1: Append the new rules**

Add this block to the end of `src/agente_ia_edu/web/styles.css`:

```css
.essay-evolution-root { margin-top: 24px; }
.essay-evolution-summary {
  background: var(--success-light); border: 1px solid var(--success); border-radius: 6px;
  padding: 10px 12px; margin-bottom: 12px; font-size: 13px; font-weight: 600; color: var(--success);
}
.essay-evolution-timeline { display: flex; flex-direction: column; gap: 10px; margin-bottom: 20px; }
.essay-evolution-card { padding: 10px 12px; }
.essay-evolution-card-head { display: flex; justify-content: space-between; font-size: 13px; font-weight: 700; margin-bottom: 8px; }
.essay-evolution-card-total { color: var(--text-muted); }
.essay-evolution-mini-bars { display: grid; grid-template-columns: repeat(5, 1fr); gap: 6px; }
.essay-evolution-mini-label { font-size: 10px; color: var(--text-muted); text-align: center; margin-bottom: 2px; }
.essay-evolution-mini-track { background: #eef1f5; border-radius: 3px; height: 6px; overflow: hidden; }
.essay-evolution-mini-fill { height: 100%; }
.essay-evolution-section-title { margin-top: 24px; }
.essay-evolution-charts { display: flex; flex-direction: column; gap: 12px; }
.essay-evolution-chart-card {
  border: 1px solid var(--border, #e2e6ee); border-radius: 8px; padding: 16px;
  background: #fff; max-width: 560px;
}
.essay-evolution-chart-title {
  font-size: 13px; font-weight: 700; margin-bottom: 10px; color: var(--text-muted);
  display: flex; align-items: center;
}
.essay-evolution-chart-badge { display: inline-block; width: 10px; height: 10px; border-radius: 50%; margin-right: 6px; }
.essay-evolution-axis-label { font-size: 10px; fill: #94a3b8; }
.essay-evolution-pt-label { font-size: 10px; fill: #64748b; }
.essay-evolution-pt-value { font-size: 11px; fill: #1e293b; font-weight: 700; }
.essay-evolution-student-list { list-style: none; padding: 0; display: flex; flex-direction: column; gap: 6px; }
```

- [ ] **Step 2: Commit**

```bash
git add src/agente_ia_edu/web/styles.css
git commit -m "style(redacao): add evolution dashboard CSS

Co-Authored-By: Claude Sonnet 5 <noreply@anthropic.com>"
```

---

### Task 8: Manual verification (no code changes)

**Files:** none — this task drives the running app through the browser to confirm Tasks 1-7 actually work together. If anything here fails, fix it as part of the task that owns the broken piece (do not silently patch around it here).

- [ ] **Step 1: Start the dev server and seed data**

Ensure this worktree has the gitignored dev fixtures copied from the main checkout first (`.env`, `.claude/launch.json`, `.claude/run_dev_server.sh`, `var/inep-pilot/*`, `var/material_storage/*` if not already present), then start the server (via this project's `preview_start` tool, or manually per the recurring `preview_start` cwd-bug workaround already established this session if it launches from the wrong directory).

Seed at least two APPROVED corrections for the same student (reuse the existing "Alice" demo student and the fictitious TEXT_OFFSET essay already created this session as one data point; add a second APPROVED correction for the same student with a different total score, either via a second real submission+approval through the teacher portal UI, or via a one-off script against the dev DB following the same shape as this plan's own test fixtures).

- [ ] **Step 2: Verify the student portal**

Log in as the seeded student, open the Redação tab. Confirm:
- The evolution section renders below the prompt list without a console error.
- The delta summary line appears (or is absent if only one scored entry exists) and its wording matches the actual score direction.
- The timeline shows one card per approved essay, newest first, with correct per-competency mini-bars and total.
- The "Evolução por competência" section shows 6 charts (C1-C5 + total), each with the right color, and hovering/tapping a point shows the correct date/prompt/score popover.
- The checklist below shows the most recent essay's strengths/growth areas (or the summary fallback, if that correction predates the split fields).

- [ ] **Step 3: Verify the teacher portal**

Log in as a teacher at the same school, open Redação → Evolução tab. Confirm:
- The student search field lists students with at least one approved correction, filters correctly by name, and excludes students with none.
- Selecting the seeded student loads the same timeline/charts/checklist as the student portal.

- [ ] **Step 4: Verify the empty-state and single-entry edge cases**

Using a second, fresh student with zero approved corrections, confirm the student portal's evolution section shows the "Vamos ver sua evolução..." message (not an error, not a blank area) and the teacher's search excludes that student entirely. Using a student with exactly one approved correction, confirm the delta summary is absent and every chart renders a single point with no line (not a crash).

- [ ] **Step 5: Report results to the user**

Summarize what was checked and any residual issues found (there should be none if Tasks 1-7 passed their own tests) — do not mark this plan complete until this step reports a clean pass.
