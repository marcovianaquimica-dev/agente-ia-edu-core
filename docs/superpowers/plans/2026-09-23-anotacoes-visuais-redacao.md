# Destaque visual das anotações na redação — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Mostrar a redação original (texto digitado ou imagens de página) com marcadores numerados e coloridos por competência sobre cada trecho anotado pela IA, nos dois portais (devolutiva do aluno e painel de revisão do professor), com um popover de explicação ao passar o mouse (desktop) ou tocar (celular/tablet).

**Architecture:** Duas rotas de backend novas/enriquecidas expõem o conteúdo da submissão ao professor (que hoje só o aluno pode ler) e ao aluno de volta (o texto some após a resposta inicial do POST/confirm). Um novo módulo JS compartilhado (`essay-annotations.js`, carregado nos dois portais — exceção deliberada à convenção de "um helper por arquivo") calcula e desenha os marcadores a partir da mesma âncora (`TextOffsetAnchor`/`ImageRegionAnchor`) que a IA já produz, sem nenhuma coluna nova no banco.

**Tech Stack:** FastAPI (Python) + vanilla JS/HTML/CSS, sem build step — mesmo stack do resto do frontend deste repositório.

**Spec:** `docs/superpowers/specs/2026-09-23-anotacoes-visuais-redacao-design.md`

## Global Constraints

- HTML-escapar todo texto interpolado — regra dura já em vigor neste código (cada arquivo tem seu próprio helper de escape: `escEssay` em `essay.js`, `tmEsc` em `essay-review.js`; o novo `essay-annotations.js` tem o seu próprio, `esc`).
- Reaproveitar as convenções de autenticação já existentes por portal — `essayHeaders()`/`essayRequest()` em `essay.js`, `reviewHeaders()`/`reviewRequest()` em `essay-review.js`. Nenhum mecanismo novo.
- Nenhuma ferramenta de build nova.
- Testes de backend: `unittest.IsolatedAsyncioTestCase`/`unittest.TestCase`, `sqlite+aiosqlite:///:memory:` + `StaticPool` — mesmo padrão de todas as rotas já existentes.
- 403, nunca 404, para o que não é do chamador — as duas rotas novas de professor reaproveitam `_authorize`/`_correction_for_own_school_or_403` (`essay_corrections.py`, já existentes, sem modificação).
- Sem testes automatizados de frontend — verificação manual/browser real, mesmo corte da leva anterior.
- `essay-annotations.js` é uma exceção deliberada à convenção "um helper por arquivo, sem import entre portais": é carregado nos dois HTMLs (`index.html` e `teacher.html`) porque o problema de desenhar um marcador numerado a partir de uma âncora é idêntico nos dois lados. Não "corrigir" isso de volta para duas cópias.
- Numeração: sequencial pela posição no array `annotations` retornado pela API (índice + 1), para TODAS as anotações — inclusive `evidence_kind="GLOBAL"`, que não ganha marcador visual mas mantém seu número na lista. Lista e marcador sempre usam o mesmo número, sem recálculo.
- Cor por competência, fixa: `C1`→`--primary`, `C2`→`--accent`, `C3`→`--danger`, `C4`→`--warning`, `C5`→`--success` — usando a variante `-light` já existente (`--primary-light` etc.) para o fundo do `<mark>` de texto, e a variante plena para o marcador sólido sobre imagem. Não mexe na cor das barras de nota por competência já existentes (`--primary` uniforme) — fora de escopo desta leva.
- As imagens de página carregadas por este plano seguem o mesmo padrão de blob URL não-revogado que a Task 6 da leva anterior já estabeleceu (achado, mas deliberadamente não corrigido, na revisão final daquela leva) — este plano reaproveita o padrão existente conscientemente, não introduz uma classe de vazamento nova; resolver isso de forma geral fica fora de escopo.

---

### Task 1: Backend — rotas de conteúdo de submissão para o professor

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_corrections.py`
- Test: `tests/test_frontend_r_teacher_submission_content_route.py` (novo)

**Interfaces:**
- Consumes: `_authorize`, `_correction_for_own_school_or_403` (já existentes, sem modificação), `EssayCorrection`, `EssaySubmission` (já importados).
- Produces: `GET /api/v1/teacher/essay-corrections/{essay_correction_id}/submission-content` → `SubmissionContentResponse`; `GET /api/v1/teacher/essay-corrections/{essay_correction_id}/pages/{page_number}/image` → `FileResponse`. Consumido pela Task 3 (`essay-review.js`).

- [ ] **Step 1: Escrever os testes que falham**

```python
"""Teacher-facing routes exposing a submission's own content (canonical
text or page images), for the visual-annotation overlay feature. Mirrors
the 403-not-404 pattern every other route in essay_corrections.py already
uses - a correction from another school is 403, never 404/422."""
import asyncio
import unittest
import uuid
from datetime import datetime, timezone
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
    EssayCorrection,
    EssayPrompt,
    EssaySubmission,
    EssaySubmissionPage,
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


class TeacherSubmissionContentRouteTests(unittest.TestCase):
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
        cls.tmp_dir = Path("/tmp/r_submission_content_fixtures")
        cls.tmp_dir.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _seed(self, code: str, *, anchor_mode: str, canonical_text: str | None,
               pages: list[str] | None = None) -> tuple[uuid.UUID, uuid.UUID]:
        """Returns (school_id, essay_correction_id)."""
        async def _seed_async():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"SC-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(SchoolModule(
                    id=uuid.uuid4(), school_id=school.id, module_key="REDACAO_IA", enabled=True,
                ))
                session.add(UserSchoolLink(
                    external_user_id=f"teacher_{code}", school_id=school.id, role="TEACHER",
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
                submission = EssaySubmission(
                    id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                    prompt_assignment_id=assignment.id, student_id=student.id,
                    mode="TYPED" if anchor_mode == "TEXT_OFFSET" else "PHOTO",
                    anchor_mode=anchor_mode, status="SUBMITTED", canonical_text=canonical_text,
                    # Two CHECK constraints on essay_submissions (db/models/essay_proposal.py)
                    # require these paired with the fields above: canonical_text and
                    # normalized_text_hash must be null/non-null together, and
                    # submitted_at must be set whenever status is SUBMITTED/SUPERSEDED.
                    normalized_text_hash=("h" * 64) if canonical_text is not None else None,
                    submitted_at=datetime.now(timezone.utc),
                )
                session.add(submission)
                await session.flush()
                for i, uri in enumerate(pages or [], start=1):
                    session.add(EssaySubmissionPage(
                        id=uuid.uuid4(), essay_submission_id=submission.id,
                        page_number=i, storage_uri=uri,
                    ))
                correction = EssayCorrection(
                    id=uuid.uuid4(), school_id=school.id, essay_submission_id=submission.id,
                    correction_key="k" * 64, rubric_version="ENEM_2025", model_version="stub",
                    prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                    ai_output={"scores": None}, status="PENDING_REVIEW",
                )
                session.add(correction)
                await session.commit()
                return school.id, correction.id

        return self.loop.run_until_complete(_seed_async())

    def test_text_offset_submission_returns_canonical_text_and_no_pages(self):
        _, correction_id = self._seed("1", anchor_mode="TEXT_OFFSET", canonical_text="Uma redacao qualquer.")
        self._as("teacher_1")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/submission-content")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["anchor_mode"], "TEXT_OFFSET")
        self.assertEqual(body["canonical_text"], "Uma redacao qualquer.")
        self.assertIsNone(body["pages"])

    def test_image_region_submission_returns_pages_and_no_text(self):
        source = self.tmp_dir / "page1.png"
        import pymupdf
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 50, 50), False)
        pix.clear_with(200)
        pix.save(str(source))
        _, correction_id = self._seed(
            "2", anchor_mode="IMAGE_REGION", canonical_text=None, pages=[str(source)],
        )
        self._as("teacher_2")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/submission-content")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["anchor_mode"], "IMAGE_REGION")
        self.assertIsNone(body["canonical_text"])
        self.assertEqual(body["pages"], [{"page_number": 1}])

    def test_submission_content_from_another_school_is_403(self):
        _, correction_id = self._seed("3", anchor_mode="TEXT_OFFSET", canonical_text="x")
        self._seed("4", anchor_mode="TEXT_OFFSET", canonical_text="y")
        self._as("teacher_4")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/submission-content")
        self.assertEqual(resp.status_code, 403, resp.text)

    def test_page_image_returns_bytes_for_owner_school(self):
        source = self.tmp_dir / "page_bytes.png"
        import pymupdf
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
        pix.clear_with(100)
        pix.save(str(source))
        _, correction_id = self._seed(
            "5", anchor_mode="IMAGE_REGION", canonical_text=None, pages=[str(source)],
        )
        self._as("teacher_5")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/pages/1/image")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.content, source.read_bytes())

    def test_page_image_from_another_school_is_403(self):
        source = self.tmp_dir / "page_other.png"
        import pymupdf
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 40, 40), False)
        pix.clear_with(50)
        pix.save(str(source))
        _, correction_id = self._seed(
            "6", anchor_mode="IMAGE_REGION", canonical_text=None, pages=[str(source)],
        )
        self._seed("7", anchor_mode="TEXT_OFFSET", canonical_text="z")
        self._as("teacher_7")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/pages/1/image")
        self.assertEqual(resp.status_code, 403)

    def test_page_image_missing_page_number_is_404(self):
        _, correction_id = self._seed("8", anchor_mode="IMAGE_REGION", canonical_text=None, pages=[])
        self._as("teacher_8")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/pages/1/image")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar os testes para confirmar que falham**

Run: `pytest tests/test_frontend_r_teacher_submission_content_route.py -v`
Expected: FAIL (404, rota ainda não existe — `SubmissionContentResponse`/rotas novas ainda não foram criadas).

- [ ] **Step 3: Implementar**

No topo de `src/agente_ia_edu/api/routes/essay_corrections.py`, adicionar aos imports já existentes (linha 16, 22):
```python
from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
```
```python
from ...db.models import EssayCorrection, EssayPrompt, EssaySubmission, EssaySubmissionPage, Person, PromptAssignment, Student
```

Logo após a classe `BulkApproveResponse` (depois da linha 65, antes de `_correction_to_response`), adicionar:
```python
class SubmissionPageSummary(BaseModel):
    page_number: int


class SubmissionContentResponse(BaseModel):
    essay_submission_id: UUID
    anchor_mode: str
    canonical_text: Optional[str] = None
    pages: Optional[list[SubmissionPageSummary]] = None
```

No final do arquivo (depois de `bulk_approve_essay_corrections`), adicionar as duas rotas novas:
```python
@essay_corrections_router.get(
    "/{essay_correction_id}/submission-content", response_model=SubmissionContentResponse
)
async def get_essay_correction_submission_content(
    essay_correction_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> SubmissionContentResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        correction = await _correction_for_own_school_or_403(
            session, essay_correction_id=essay_correction_id, school_id=school_id
        )
        submission = await session.get(EssaySubmission, correction.essay_submission_id)
        pages = None
        if submission.anchor_mode == "IMAGE_REGION":
            page_numbers = (
                await session.execute(
                    select(EssaySubmissionPage.page_number)
                    .where(EssaySubmissionPage.essay_submission_id == submission.id)
                    .order_by(EssaySubmissionPage.page_number)
                )
            ).scalars().all()
            pages = [SubmissionPageSummary(page_number=n) for n in page_numbers]
        return SubmissionContentResponse(
            essay_submission_id=submission.id, anchor_mode=submission.anchor_mode,
            canonical_text=submission.canonical_text if submission.anchor_mode == "TEXT_OFFSET" else None,
            pages=pages,
        )


@essay_corrections_router.get("/{essay_correction_id}/pages/{page_number}/image")
async def get_essay_correction_page_image(
    essay_correction_id: UUID,
    page_number: int,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        correction = await _correction_for_own_school_or_403(
            session, essay_correction_id=essay_correction_id, school_id=school_id
        )
        page = await session.scalar(
            select(EssaySubmissionPage).where(
                EssaySubmissionPage.essay_submission_id == correction.essay_submission_id,
                EssaySubmissionPage.page_number == page_number,
            )
        )
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found")
        return FileResponse(page.storage_uri)
```

Nota: `submission` nunca é `None` aqui — `EssayCorrection.essay_submission_id` é uma FK `ondelete="RESTRICT"` (não-nula por schema), mesma garantia que o resto deste arquivo já assume sem checagem redundante (ex.: o `INNER JOIN` de `list_essay_corrections`).

- [ ] **Step 4: Rodar os testes para confirmar que passam**

Run: `pytest tests/test_frontend_r_teacher_submission_content_route.py -v`
Expected: PASS (6 testes)

- [ ] **Step 5: Rodar a suíte de regressão do arquivo**

Run: `pytest tests/test_r3_essay_corrections_routes.py tests/test_r3_essay_correction_review.py tests/test_frontend_r_teacher_essay_corrections_enriched_list.py tests/test_r3_essay_corrections_missing_greenlet_regression.py -v`
Expected: PASS, sem regressão (as rotas `approve`/`reject`/`retry`/`bulk-approve`/`list` não foram tocadas).

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_corrections.py tests/test_frontend_r_teacher_submission_content_route.py
git commit -m "feat(redacao): add teacher routes for submission content and page images"
```

---

### Task 2: Backend — enriquecer devolutiva do aluno com `canonical_text`

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_submissions.py:458-499`
- Test: `tests/test_frontend_r_student_correction_canonical_text.py` (novo)

**Interfaces:**
- Consumes: `StudentCorrectionResponse` (já existe, será enriquecida), `EssaySubmission.canonical_text` (já existe).
- Produces: `canonical_text: str | None` em `StudentCorrectionResponse`, populado só quando `status="APPROVED"`. Consumido pela Task 4 (`essay.js`). `anchor_mode` NÃO precisa ser adicionado aqui — `essay.js` já tem `prompt.my_submission.anchor_mode` disponível (Task 1 da leva anterior, `MySubmissionSummary.anchor_mode`).

- [ ] **Step 1: Escrever o teste que falha**

```python
"""canonical_text on the student's own devolutiva - a gap found while
designing the visual-annotation overlay feature: the only place
canonical_text existed before was the POST/confirm response body, which
doesn't survive a page reload. Mirrors the same PENDING/APPROVED gating
every other field on this response already uses."""
import asyncio
import unittest
import uuid
from datetime import datetime, timezone

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
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


class StudentCorrectionCanonicalTextTests(unittest.TestCase):
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

    def _seed(self, code: str, *, correction_status: str | None, anchor_mode: str = "TEXT_OFFSET",
               canonical_text: str | None = "Uma redacao digitada qualquer.") -> uuid.UUID:
        async def _seed_async():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"CT-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(SchoolModule(
                    id=uuid.uuid4(), school_id=school.id, module_key="REDACAO_IA", enabled=True,
                ))
                session.add(UserSchoolLink(
                    external_user_id=f"student_ct_{code}", school_id=school.id, role="STUDENT",
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
                    external_identity_provider="test", external_user_id=f"student_ct_{code}",
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
                submitted_at = datetime.now(timezone.utc)
                submission = EssaySubmission(
                    id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                    prompt_assignment_id=assignment.id, student_id=student.id,
                    mode="TYPED" if anchor_mode == "TEXT_OFFSET" else "PHOTO",
                    anchor_mode=anchor_mode, status="SUBMITTED", canonical_text=canonical_text,
                    # Same two paired CHECK constraints as Task 1's fixture (see its
                    # comment): canonical_text/normalized_text_hash null together,
                    # submitted_at required whenever status=SUBMITTED.
                    normalized_text_hash=("h" * 64) if canonical_text is not None else None,
                    submitted_at=submitted_at,
                )
                session.add(submission)
                await session.flush()
                if correction_status is not None:
                    session.add(EssayCorrection(
                        id=uuid.uuid4(), school_id=school.id, essay_submission_id=submission.id,
                        correction_key="k" * 64, rubric_version="ENEM_2025", model_version="stub",
                        prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                        # ai_output must be non-null for every status except
                        # NEEDS_REVIEW (ck_essay_corrections_non_failed_has_ai_output)
                        # - this fixture never exercises NEEDS_REVIEW, so it's set
                        # unconditionally here.
                        ai_output={"scores": None}, status=correction_status,
                        **({
                            "final_scores": {"total": 800, "per_competency": {}},
                            "final_feedback": {"strengths": [], "improvements": [], "next_essay_strategy": "x"},
                            "reviewed_at": submitted_at, "published_at": submitted_at,
                        } if correction_status == "APPROVED" else {}),
                    ))
                submission_id = submission.id
                await session.commit()
                return submission_id

        return self.loop.run_until_complete(_seed_async())

    def test_canonical_text_present_when_approved(self):
        submission_id = self._seed("1", correction_status="APPROVED")
        self._as("student_ct_1")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["canonical_text"], "Uma redacao digitada qualquer.")

    def test_canonical_text_null_when_pending(self):
        submission_id = self._seed("2", correction_status="PENDING_REVIEW")
        self._as("student_ct_2")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "PENDING")
        self.assertIsNone(body["canonical_text"])

    def test_canonical_text_null_for_image_region_submission(self):
        submission_id = self._seed(
            "3", correction_status="APPROVED", anchor_mode="IMAGE_REGION", canonical_text=None,
        )
        self._as("student_ct_3")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertIsNone(resp.json()["canonical_text"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `pytest tests/test_frontend_r_student_correction_canonical_text.py -v`
Expected: FAIL (`canonical_text` ainda não existe em `StudentCorrectionResponse` — `KeyError`/`AssertionError`).

- [ ] **Step 3: Implementar**

Em `src/agente_ia_edu/api/routes/essay_submissions.py`, modificar `StudentCorrectionResponse` (linha 458-466):
```python
class StudentCorrectionResponse(BaseModel):
    essay_submission_id: UUID
    status: str
    canonical_text: Optional[str] = None
    final_scores: Optional[dict] = None
    final_feedback: Optional[dict] = None
    annotations: Optional[list] = None
    rewrites: Optional[list] = None
    intervention: Optional[dict] = None
    alerts: Optional[list] = None
```

Modificar o corpo de `get_essay_submission_correction` (linha 493-499) para incluir `canonical_text`:
```python
        ai_output = correction.ai_output or {}
        return StudentCorrectionResponse(
            essay_submission_id=submission.id, status="APPROVED",
            canonical_text=submission.canonical_text,
            final_scores=correction.final_scores, final_feedback=correction.final_feedback,
            annotations=ai_output.get("annotations"), rewrites=ai_output.get("rewrites"),
            intervention=ai_output.get("intervention"), alerts=ai_output.get("alerts"),
        )
```
(`submission.canonical_text` já é `None` para submissões `IMAGE_REGION` — nada a filtrar aqui, o valor já vem certo do banco.)

- [ ] **Step 4: Rodar o teste para confirmar que passa**

Run: `pytest tests/test_frontend_r_student_correction_canonical_text.py -v`
Expected: PASS (3 testes)

- [ ] **Step 5: Rodar a suíte de regressão do arquivo**

Run: `pytest tests/test_frontend_r_student_essay_correction_route.py tests/test_r3_essay_submission_missing_greenlet_regression.py -v`
Expected: PASS, sem regressão.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_submissions.py tests/test_frontend_r_student_correction_canonical_text.py
git commit -m "feat(redacao): enrich student devolutiva route with canonical_text"
```

---

### Task 3: Frontend — módulo compartilhado `essay-annotations.js`

**Files:**
- Create: `src/agente_ia_edu/web/essay-annotations.js`
- Modify: `src/agente_ia_edu/web/index.html:600-601` (adicionar `<script>` antes de `essay.js`)
- Modify: `src/agente_ia_edu/web/teacher.html:824` (adicionar `<script>` antes de `essay-review.js`)
- Modify: `src/agente_ia_edu/web/styles.css` (novas regras)

**Interfaces:**
- Produces: `window.EssayAnnotations.renderHighlightedText(canonicalText, annotations)` → `string` (HTML); `window.EssayAnnotations.renderImageMarkers(wrapEl, imgEl, annotations, pageNumber)` → `void` (insere elementos no DOM); `window.EssayAnnotations.wirePopovers(rootEl, annotations)` → `void`; `window.EssayAnnotations.COMPETENCY_COLORS` → `{C1..C5: '--nome-da-variavel-css'}` (não consumido por nenhuma outra task deste plano — as Tasks 4/5 usam as classes CSS diretamente, não esta constante — mas exportado por completude, conforme o spec §2). Consumido pelas Tasks 4 e 5.
- Consumes: nada de outro módulo deste projeto — arquivo autocontido (tem seu próprio `esc()`), carregado antes de `essay.js`/`essay-review.js` em cada portal.

- [ ] **Step 1: Criar o arquivo**

```javascript
/* AGENTE IA EDU — módulo compartilhado de destaque de anotações.
   Carregado nos dois portais (aluno e professor) - exceção deliberada à
   convenção de "um helper por arquivo" deste código: o problema de
   desenhar um marcador numerado a partir de uma âncora (TextOffsetAnchor
   ou ImageRegionAnchor) é idêntico nos dois lados. */
(function essayAnnotationsModule(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.EssayAnnotations = api;
})(typeof window !== 'undefined' ? window : null, function createEssayAnnotations() {
  function esc(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[character]);
  }

  // Fixed mapping, no semantic meaning beyond telling competencies apart
  // visually. The CSS classes below (essay-mark-C1 etc.) are the actual
  // source of truth for what color renders - this constant exists so a
  // consumer outside this module (e.g. a future legend, or reusing the
  // same color on the existing competency-score bars) can look up which
  // CSS variable a competency maps to without duplicating the mapping.
  const COMPETENCY_COLORS = {
    C1: '--primary', C2: '--accent', C3: '--danger', C4: '--warning', C5: '--success',
  };

  const isTouch = typeof window !== 'undefined' && window.matchMedia
    && window.matchMedia('(hover: none)').matches;

  let openPopover = null;

  function closePopover() {
    if (openPopover) { openPopover.remove(); openPopover = null; }
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('click', (ev) => {
      if (openPopover && !openPopover.contains(ev.target) && !ev.target.closest('[data-marker-number]')) {
        closePopover();
      }
    });
  }

  function showPopover(rootEl, markerEl, annotation) {
    closePopover();
    const popover = document.createElement('div');
    popover.className = 'essay-popover';
    popover.innerHTML = `
      <strong>${esc(annotation.letter)} — ${esc(annotation.competency_code)}</strong>
      <p>${esc(annotation.short_comment)}</p>
      <p class="empty-text">${esc(annotation.long_comment)}</p>
      ${annotation.pedagogical_suggestion ? `<p class="essay-popover-suggestion">${esc(annotation.pedagogical_suggestion)}</p>` : ''}`;
    // Appended inside rootEl (not document.body): every view in this
    // codebase replaces its whole container's innerHTML on navigation, so
    // a popover living inside that container gets torn down for free -
    // appending to document.body would leak an orphaned node whenever the
    // user navigates away without closing the popover first.
    rootEl.appendChild(popover);
    popover.style.position = 'fixed';
    const markerRect = markerEl.getBoundingClientRect();
    const popRect = popover.getBoundingClientRect();
    let left = markerRect.left;
    let top = markerRect.bottom + 6;
    if (left + popRect.width > window.innerWidth) left = window.innerWidth - popRect.width - 8;
    if (top + popRect.height > window.innerHeight) top = markerRect.top - popRect.height - 6;
    popover.style.left = `${Math.max(8, left)}px`;
    popover.style.top = `${Math.max(8, top)}px`;
    openPopover = popover;
  }

  function wirePopovers(rootEl, annotations) {
    rootEl.querySelectorAll('[data-marker-number]').forEach((markerEl) => {
      const number = Number(markerEl.dataset.markerNumber);
      const annotation = annotations[number - 1];
      if (!annotation) return;
      if (isTouch) {
        markerEl.addEventListener('click', (ev) => {
          ev.stopPropagation();
          if (openPopover && openPopover.dataset.forMarker === String(number)) { closePopover(); return; }
          showPopover(rootEl, markerEl, annotation);
          openPopover.dataset.forMarker = String(number);
        });
      } else {
        markerEl.addEventListener('mouseenter', () => showPopover(rootEl, markerEl, annotation));
        markerEl.addEventListener('mouseleave', closePopover);
      }
    });
  }

  function renderHighlightedText(canonicalText, annotations) {
    const text = String(canonicalText ?? '');
    const items = (annotations || [])
      .map((a, i) => ({ a, number: i + 1 }))
      .filter(({ a }) => (
        a.evidence_kind !== 'GLOBAL'
        && a.anchor && a.anchor.type === 'TEXT_OFFSET'
        && a.anchor.start >= 0 && a.anchor.end > a.anchor.start && a.anchor.end <= text.length
      ))
      .sort((x, y) => x.a.anchor.start - y.a.anchor.start);

    let html = '';
    let cursor = 0;
    items.forEach(({ a, number }) => {
      const { start, end } = a.anchor;
      if (start < cursor) return; // overlaps a previous mark - skip visually, stays in the list
      html += esc(text.slice(cursor, start));
      html += `<mark class="essay-mark essay-mark-${esc(a.competency_code)}" data-marker-number="${number}" tabindex="0">${esc(text.slice(start, end))}<sup>${number}</sup></mark>`;
      cursor = end;
    });
    html += esc(text.slice(cursor));
    return html;
  }

  function renderImageMarkers(wrapEl, imgEl, annotations, pageNumber) {
    const items = (annotations || [])
      .map((a, i) => ({ a, number: i + 1 }))
      .filter(({ a }) => (
        a.evidence_kind !== 'GLOBAL'
        && a.anchor && a.anchor.type === 'IMAGE_REGION' && a.anchor.page === pageNumber
      ));
    const naturalWidth = imgEl.naturalWidth || 1;
    const naturalHeight = imgEl.naturalHeight || 1;
    wrapEl.querySelectorAll('.essay-image-marker').forEach((el) => el.remove());
    items.forEach(({ a, number }) => {
      const { x, y, width, height } = a.anchor;
      const marker = document.createElement('span');
      marker.className = `essay-image-marker essay-mark-${esc(a.competency_code)}`;
      marker.dataset.markerNumber = String(number);
      marker.tabIndex = 0;
      marker.style.left = `${(x / naturalWidth) * 100}%`;
      marker.style.top = `${(y / naturalHeight) * 100}%`;
      marker.style.width = `${Math.max((width / naturalWidth) * 100, 3)}%`;
      marker.style.height = `${Math.max((height / naturalHeight) * 100, 3)}%`;
      marker.textContent = String(number);
      wrapEl.appendChild(marker);
    });
  }

  return { renderHighlightedText, renderImageMarkers, wirePopovers, COMPETENCY_COLORS };
});
```

- [ ] **Step 2: Sanity-check de sintaxe**

Run: `node --check src/agente_ia_edu/web/essay-annotations.js`
Expected: sem saída (sintaxe válida). Se `node` não estiver disponível, revisar o arquivo manualmente por parênteses/chaves desbalanceados antes de seguir.

- [ ] **Step 3: Adicionar o `<script>` nos dois HTMLs**

Em `src/agente_ia_edu/web/index.html`, linha 600-601, adicionar a nova tag ANTES de `essay.js`:
```html
  <script src="evolution.js"></script>
  <script src="essay-annotations.js"></script>
  <script src="essay.js"></script>
  <script src="app.js"></script>
```

Em `src/agente_ia_edu/web/teacher.html`, linha 824, adicionar a nova tag ANTES de `essay-review.js`:
```html
  <script src="essay-annotations.js"></script>
  <script src="essay-review.js"></script>
  <script src="teacher.js"></script>
```

- [ ] **Step 4: CSS**

Adicionar ao final de `src/agente_ia_edu/web/styles.css`:
```css
.essay-mark { padding: 1px 2px; border-radius: 3px; cursor: pointer; }
.essay-mark sup { font-weight: 700; margin-left: 1px; }
.essay-mark-C1 { background-color: var(--primary-light); border-bottom: 2px solid var(--primary); }
.essay-mark-C2 { background-color: var(--accent-light); border-bottom: 2px solid var(--accent); }
.essay-mark-C3 { background-color: var(--danger-light); border-bottom: 2px solid var(--danger); }
.essay-mark-C4 { background-color: var(--warning-light); border-bottom: 2px solid var(--warning); }
.essay-mark-C5 { background-color: var(--success-light); border-bottom: 2px solid var(--success); }

.essay-image-marker {
  position: absolute; display: flex; align-items: center; justify-content: center;
  border-radius: 4px; border: 2px solid; font-size: 11px; font-weight: 700; color: #fff;
  cursor: pointer; min-width: 18px; min-height: 18px;
}
.essay-image-marker.essay-mark-C1 { background-color: var(--primary); border-color: var(--primary); }
.essay-image-marker.essay-mark-C2 { background-color: var(--accent); border-color: var(--accent); }
.essay-image-marker.essay-mark-C3 { background-color: var(--danger); border-color: var(--danger); }
.essay-image-marker.essay-mark-C4 { background-color: var(--warning); border-color: var(--warning); }
.essay-image-marker.essay-mark-C5 { background-color: var(--success); border-color: var(--success); }

.essay-page-image-wrap { position: relative; display: inline-block; }

.essay-popover {
  z-index: 1000; max-width: 320px; background: var(--bg-card); border: 1px solid var(--border-color);
  border-radius: 8px; padding: 12px 14px; box-shadow: 0 8px 24px rgba(15,23,42,0.18);
}
.essay-popover-suggestion { font-style: italic; color: var(--text-muted); margin-top: 6px; }

.essay-highlighted-text { white-space: pre-wrap; line-height: 1.6; }

.essay-annotation-number {
  display: inline-flex; align-items: center; justify-content: center; width: 20px; height: 20px;
  border-radius: 50%; font-size: 11px; font-weight: 700; color: #fff; margin-right: 6px;
}
.essay-annotation-number.essay-mark-C1 { background-color: var(--primary); }
.essay-annotation-number.essay-mark-C2 { background-color: var(--accent); }
.essay-annotation-number.essay-mark-C3 { background-color: var(--danger); }
.essay-annotation-number.essay-mark-C4 { background-color: var(--warning); }
.essay-annotation-number.essay-mark-C5 { background-color: var(--success); }
```
(`.essay-page-card img`/`.essay-page-image-wrap` — a regra já existente `.essay-page-card img { max-width: 100%; border-radius: 6px; border: 1px solid var(--border, #e2e6ee); }`, linha 1585 de `styles.css`, continua valendo sem mudança: `.essay-page-image-wrap` é só um wrapper posicional, o `<img>` dentro dele ainda casa com o seletor existente.)

- [ ] **Step 5: Verificação manual**

Se houver ferramenta de browser disponível, abrir `/student/` e `/teacher/` e confirmar via `read_console_messages`/inspeção do DOM que `window.EssayAnnotations` existe e expõe as três funções (`typeof window.EssayAnnotations.renderHighlightedText === 'function'` etc., verificável via `javascript_tool`), sem nenhum erro de carregamento de script. Nenhuma tela ainda usa o módulo nesta task — a verificação aqui é só de que o arquivo carrega sem erro, não de comportamento visual (isso vem nas Tasks 4/5).

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/web/essay-annotations.js src/agente_ia_edu/web/index.html src/agente_ia_edu/web/teacher.html src/agente_ia_edu/web/styles.css
git commit -m "feat(redacao): add shared essay-annotations.js module for visual highlight/marker overlay"
```

---

### Task 4: Frontend — integrar na devolutiva do aluno (`essay.js`)

**Files:**
- Modify: `src/agente_ia_edu/web/essay.js:366-435` (`renderApprovedDevolutiva`)

**Interfaces:**
- Consumes: `window.EssayAnnotations.renderHighlightedText`/`.renderImageMarkers`/`.wirePopovers` (Task 3); `correction.canonical_text` (Task 2); `prompt.my_submission.anchor_mode` (já existente, R3); `essayHeaders()`/`essayRequest()` (já existentes neste arquivo); `GET /api/v1/student/essay-submissions/{id}/pages` (já existente, R2); `GET /api/v1/student/essay-submissions/{id}/pages/{page_number}/image` (já existente, Task 6 da leva anterior).
- Produces: nada consumido por outra task deste plano.

- [ ] **Step 1: Substituir `renderApprovedDevolutiva` e adicionar `loadOriginalPages`**

Em `src/agente_ia_edu/web/essay.js`, substituir a função `renderApprovedDevolutiva` inteira (linhas 366-435) por:

```javascript
  function renderApprovedDevolutiva(prompt, correction) {
    const scores = correction.final_scores || {};
    const perCompetency = scores.per_competency || {};
    const feedback = correction.final_feedback || {};
    const annotations = correction.annotations || [];
    const alerts = correction.alerts || [];
    const intervention = correction.intervention || {};
    const anchorMode = prompt.my_submission.anchor_mode;

    const competencyBars = Object.keys(COMPETENCY_LABELS).map((code) => {
      const points = (perCompetency[code] || {}).points || 0;
      const pct = Math.round((points / 200) * 100);
      return `
        <div class="essay-competency-row">
          <span>${code} — ${COMPETENCY_LABELS[code]}</span>
          <div class="essay-competency-bar"><div class="essay-competency-fill" style="width:${pct}%"></div></div>
          <span>${points}/200</span>
        </div>`;
    }).join('');

    const alertsHtml = alerts.length
      ? `<div class="essay-alerts">${alerts.map((a) => `<span class="badge badge-accent">${escEssay(a.code)}</span>`).join(' ')}</div>`
      : '';

    const annotationsHtml = annotations.length
      ? annotations.map((a, i) => {
          const quote = (a.anchor && (a.anchor.quote || a.anchor.read_text)) || '';
          return `
            <div class="essay-annotation">
              <span class="essay-annotation-number essay-mark-${escEssay(a.competency_code)}">${i + 1}</span>
              <strong>${escEssay(a.letter)} — ${escEssay(a.competency_code)}</strong>
              <p>${escEssay(a.short_comment)}</p>
              <p class="empty-text">${escEssay(a.long_comment)}</p>
              ${quote ? `<blockquote>"${escEssay(quote)}"</blockquote>` : ''}
            </div>`;
        }).join('')
      : '<p class="empty-text">Nenhuma anotação específica.</p>';

    const interventionHtml = `
      <ul class="essay-intervention-checklist">
        <li>${intervention.agente ? '✓' : '○'} Agente: ${escEssay(intervention.agente || '—')}</li>
        <li>${intervention.acao ? '✓' : '○'} Ação: ${escEssay(intervention.acao || '—')}</li>
        <li>${intervention.meio_modo ? '✓' : '○'} Meio/modo: ${escEssay(intervention.meio_modo || '—')}</li>
        <li>${intervention.finalidade ? '✓' : '○'} Finalidade: ${escEssay(intervention.finalidade || '—')}</li>
        <li>${intervention.detalhamento ? '✓' : '○'} Detalhamento: ${escEssay(intervention.detalhamento || '—')}</li>
      </ul>
      <p class="${intervention.respeita_direitos_humanos ? '' : 'essay-warning'}">
        ${intervention.respeita_direitos_humanos ? '✓ Respeita os direitos humanos' : '⚠ Atenção: verificar respeito aos direitos humanos'}
      </p>`;

    const originalContentHtml = anchorMode === 'TEXT_OFFSET'
      ? `<div class="essay-highlighted-text">${window.EssayAnnotations.renderHighlightedText(correction.canonical_text || '', annotations)}</div>`
      : '<div id="essay-original-pages"><p class="empty-text">Carregando páginas...</p></div>';

    container.innerHTML = `
      <div class="card">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <h3>${escEssay(prompt.title)}</h3>
        <div class="essay-total-score">Nota total: ${scores.total != null ? scores.total : '—'} / 1000</div>
        ${alertsHtml}
        <h4>Notas por competência</h4>
        ${competencyBars}
        <h4>Pontos fortes</h4>
        <ul>${(feedback.strengths || []).map((s) => `<li>${escEssay(s)}</li>`).join('') || '<li class="empty-text">—</li>'}</ul>
        <h4>A melhorar</h4>
        <ul>${(feedback.improvements || []).map((s) => `<li>${escEssay(s)}</li>`).join('') || '<li class="empty-text">—</li>'}</ul>
        <h4>Próxima redação</h4>
        <p>${escEssay(feedback.next_essay_strategy || '')}</p>
        <h4>Sua redação</h4>
        ${originalContentHtml}
        <h4>Anotações</h4>
        ${annotationsHtml}
        <h4>Competência 5 — Proposta de intervenção</h4>
        ${interventionHtml}
      </div>`;

    container.querySelector('[data-back]').addEventListener('click', () => loadPrompts());

    if (anchorMode === 'TEXT_OFFSET') {
      window.EssayAnnotations.wirePopovers(container, annotations);
    } else {
      loadOriginalPages(prompt, annotations);
    }
  }

  async function loadOriginalPages(prompt, annotations) {
    const submissionId = prompt.my_submission.id;
    const pagesContainer = container.querySelector('#essay-original-pages');
    let pages = [];
    try {
      pages = await essayRequest(`/api/v1/student/essay-submissions/${submissionId}/pages`);
    } catch (e) {
      pagesContainer.innerHTML = `<p class="empty-text">${escEssay(e.message)}</p>`;
      return;
    }
    pagesContainer.innerHTML = pages.map((p) => `
      <div class="essay-page-image-wrap" data-page-wrap="${p.page_number}">
        <img data-page-image="${p.page_number}" alt="Página ${p.page_number}">
      </div>`).join('') || '<p class="empty-text">Nenhuma página enviada.</p>';

    pagesContainer.querySelectorAll('[data-page-image]').forEach((img) => {
      const pageNumber = Number(img.dataset.pageImage);
      fetch(`/api/v1/student/essay-submissions/${submissionId}/pages/${pageNumber}/image`, {
        headers: essayHeaders(),
      })
        .then((res) => (res.ok ? res.blob() : Promise.reject(new Error('image fetch failed'))))
        .then((blob) => new Promise((resolve) => {
          img.onload = resolve;
          img.src = URL.createObjectURL(blob);
        }))
        .then(() => {
          const wrap = pagesContainer.querySelector(`[data-page-wrap="${pageNumber}"]`);
          window.EssayAnnotations.renderImageMarkers(wrap, img, annotations, pageNumber);
          window.EssayAnnotations.wirePopovers(wrap, annotations);
        })
        .catch(() => { img.alt = `Não foi possível carregar a página ${pageNumber}.`; });
    });
  }
```

- [ ] **Step 2: Sanity-check de sintaxe**

Run: `node --check src/agente_ia_edu/web/essay.js`
Expected: sem saída.

- [ ] **Step 3: Verificação manual (browser real)**

Se houver ferramenta de browser disponível: aprovar (via API/DB, reaproveitando o padrão já usado pela Task 7 da leva anterior) uma correção `TEXT_OFFSET` com pelo menos uma anotação com âncora válida, abrir a devolutiva como esse aluno, confirmar que o texto aparece com o trecho destacado e numerado, que passar o mouse sobre o destaque mostra o popover, e que a lista de anotações abaixo mostra o mesmo número. Repetir para uma correção `IMAGE_REGION` com pelo menos uma página, confirmando o marcador sobre a imagem e o popover ao tocar/passar o mouse nele. Checar `read_console_messages` por erros. Limpar qualquer fixture criada.

- [ ] **Step 4: Commit**

```bash
git add src/agente_ia_edu/web/essay.js
git commit -m "feat(redacao): show highlighted/marked essay content in student devolutiva"
```

---

### Task 5: Frontend — integrar no painel de revisão do professor (`essay-review.js`)

**Files:**
- Modify: `src/agente_ia_edu/web/essay-review.js:295-377` (`renderReviewPanel`)

**Interfaces:**
- Consumes: `window.EssayAnnotations.renderHighlightedText`/`.renderImageMarkers`/`.wirePopovers` (Task 3); `GET /api/v1/teacher/essay-corrections/{id}/submission-content` e `GET .../pages/{page_number}/image` (Task 1); `reviewHeaders()`/`reviewRequest()` (já existentes neste arquivo).
- Produces: nada consumido por outra task deste plano.

- [ ] **Step 1: Modificar `renderReviewPanel` e adicionar `loadOriginalContent`**

Em `src/agente_ia_edu/web/essay-review.js`, dentro de `renderReviewPanel` (linhas 295-377), substituir o bloco `scoresFeedbackHtml` (linhas 327-357) por:

```javascript
    const showsContent = isPending || isTerminal;

    // Rendered for PENDING_REVIEW (editable, so a teacher has something to
    // act on) and for the terminal APPROVED/REJECTED states (read-only, so
    // "Revisar" on an already-decided correction - reachable from the
    // Aprovadas/Rejeitadas queue filters - shows what was actually decided
    // instead of an empty panel). NEEDS_REVIEW has no scores/feedback yet
    // (the AI call never produced any), so it stays out of this block.
    const scoresFeedbackHtml = showsContent ? `
      <h4>Notas por competência</h4>
      <div class="tm-form-row">
        ${['C1', 'C2', 'C3', 'C4', 'C5'].map((code) => `
          <div class="form-group">
            <label ${isPending ? `for="er-score-${code}"` : ''}>${code}</label>
            ${isPending
              ? `<input id="er-score-${code}" class="text-input" type="number" min="0" max="200" step="40" value="${Number((perCompetency[code] || {}).points) || 0}">`
              : `<p class="empty-text">${tmEsc((perCompetency[code] || {}).points ?? '—')}</p>`}
          </div>`).join('')}
      </div>
      <h4>Feedback</h4>
      <div class="form-group">
        <label ${isPending ? 'for="er-feedback-strategy"' : ''}>Próxima redação</label>
        ${isPending
          ? `<textarea id="er-feedback-strategy" class="textarea-input" rows="3">${tmEsc(feedback.next_essay_strategy || '')}</textarea>`
          : `<p class="empty-text">${tmEsc(feedback.next_essay_strategy || '—')}</p>`}
      </div>
      <h4>Redação do aluno</h4>
      <div id="er-original-content"><p class="empty-text">Carregando conteúdo original...</p></div>
      <h4>Anotações da IA</h4>
      ${annotations.length ? annotations.map((a, i) => `
        <div class="essay-annotation">
          <span class="essay-annotation-number essay-mark-${tmEsc(a.competency_code)}">${i + 1}</span>
          <strong>${tmEsc(a.letter)} — ${tmEsc(a.competency_code)}</strong>
          <p>${tmEsc(a.short_comment)}</p>
        </div>`).join('') : '<p class="empty-text">Nenhuma anotação.</p>'}
    ` : '';
```

Logo depois de `container.querySelector('[data-back]').addEventListener(...)` (a linha seguinte ao HTML do painel ser inserido, antes de `const msg = ...`), adicionar:
```javascript
    if (showsContent) {
      loadOriginalContent(correctionId, annotations);
    }
```

Adicionar a nova função, em qualquer lugar do módulo fora de `renderReviewPanel` (por exemplo, logo depois dela):
```javascript
  async function loadOriginalContent(correctionId, annotations) {
    const target = container.querySelector('#er-original-content');
    if (!target) return;
    let content;
    try {
      content = await reviewRequest(`/api/v1/teacher/essay-corrections/${correctionId}/submission-content`);
    } catch (e) {
      target.innerHTML = `<p class="empty-text">${tmEsc(e.message)}</p>`;
      return;
    }
    if (content.anchor_mode === 'TEXT_OFFSET') {
      target.innerHTML = `<div class="essay-highlighted-text">${window.EssayAnnotations.renderHighlightedText(content.canonical_text || '', annotations)}</div>`;
      window.EssayAnnotations.wirePopovers(target, annotations);
      return;
    }
    const pages = content.pages || [];
    target.innerHTML = pages.map((p) => `
      <div class="essay-page-image-wrap" data-page-wrap="${p.page_number}">
        <img data-page-image="${p.page_number}" alt="Página ${p.page_number}">
      </div>`).join('') || '<p class="empty-text">Nenhuma página.</p>';

    target.querySelectorAll('[data-page-image]').forEach((img) => {
      const pageNumber = Number(img.dataset.pageImage);
      fetch(`/api/v1/teacher/essay-corrections/${correctionId}/pages/${pageNumber}/image`, {
        headers: reviewHeaders(),
      })
        .then((res) => (res.ok ? res.blob() : Promise.reject(new Error('image fetch failed'))))
        .then((blob) => new Promise((resolve) => {
          img.onload = resolve;
          img.src = URL.createObjectURL(blob);
        }))
        .then(() => {
          const wrap = target.querySelector(`[data-page-wrap="${pageNumber}"]`);
          window.EssayAnnotations.renderImageMarkers(wrap, img, annotations, pageNumber);
          window.EssayAnnotations.wirePopovers(wrap, annotations);
        })
        .catch(() => { img.alt = `Não foi possível carregar a página ${pageNumber}.`; });
    });
  }
```

- [ ] **Step 2: Sanity-check de sintaxe**

Run: `node --check src/agente_ia_edu/web/essay-review.js`
Expected: sem saída.

- [ ] **Step 3: Verificação manual (browser real)**

Se houver ferramenta de browser disponível: abrir o painel de revisão para uma correção `PENDING_REVIEW` com anotações e âncoras válidas (texto e, separadamente, imagem), confirmar que o conteúdo original aparece com os marcadores e que o popover funciona. Repetir para uma correção já `APPROVED`/`REJECTED` (estado terminal, somente leitura) — confirmar que o conteúdo original aparece ali também, não só o estado `PENDING_REVIEW`. Checar `read_console_messages` por erros. Limpar qualquer fixture criada.

- [ ] **Step 4: Commit**

```bash
git add src/agente_ia_edu/web/essay-review.js
git commit -m "feat(redacao): show highlighted/marked essay content in teacher review panel"
```

---

### Task 6: Verificação final (sem código)

**Files:** nenhum — só verificação.

- [ ] **Step 1: Suíte de backend completa**

Run: `pytest tests/ -q`
Expected: todos os testes passam, incluindo os 9 novos (Tasks 1+2) e zero regressão nos já existentes.

- [ ] **Step 2: Checagem de credenciais**

Rodar o mesmo scan de credenciais/segredos já usado nas levas anteriores deste projeto (se o repositório tiver um script dedicado, ex. `scripts/credential-scan` ou equivalente — verificar `docs/superpowers/` por precedente; caso não exista, uma busca simples por padrões de chave/senha nos arquivos novos/modificados desta leva é suficiente) antes de considerar a branch pronta.

- [ ] **Step 3: Passeio manual completo pelos dois portais**

Se houver ferramenta de browser disponível, percorrer o fluxo ponta a ponta:
1. Aluno: enviar uma redação digitada, aguardar (ou forçar via API/DB, como as tasks anteriores já fizeram) uma correção aprovada com pelo menos 2 anotações `TEXT_OFFSET`, abrir a devolutiva e confirmar os destaques numerados, cores por competência, e o popover em hover e em modo touch simulado (usar `resize_window` com `preset: "mobile"` e conferir que o toque no marcador abre/fecha o popover).
2. Aluno: repetir com uma submissão `IMAGE_REGION` (foto), confirmando os marcadores sobre a imagem da página.
3. Professor: abrir o painel de revisão de uma correção `PENDING_REVIEW` com anotações, confirmar o mesmo destaque, aprovar a correção, reabrir a partir do filtro "Aprovadas" e confirmar que o conteúdo original ainda aparece (somente leitura).
4. Confirmar que nenhuma anotação `evidence_kind="GLOBAL"` (se houver alguma nos dados de teste) gera marcador visual, mas continua aparecendo na lista com seu número.

- [ ] **Step 4: Relatar quaisquer achados**

Se a verificação manual não for possível neste ambiente, registrar isso explicitamente em vez de declarar sucesso sem prova visual — mesma disciplina que a leva anterior já seguiu.

---
