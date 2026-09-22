# Frontend do fluxo de redação — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Construir a primeira UI real do ciclo de redação (propostas → envio → correção → devolutiva) para os portais do aluno e do professor, sobre um backend que já existe por inteiro (R2/R3).

**Architecture:** Cinco rotas novas e aditivas de backend (nenhum modelo de dados novo, só leitura/enriquecimento) mais dois módulos JS novos, `essay.js` (aluno) e `essay-review.js` (professor), plugados nos portais existentes seguindo as convenções já estabelecidas (vanilla JS, sem build step, template literals + `innerHTML`, um módulo carregado como `window.X` antes do arquivo principal do portal — único precedente: `evolution.js`).

**Tech Stack:** FastAPI, SQLAlchemy 2.0 (async), `unittest` (backend), vanilla HTML/CSS/JS (frontend, sem framework, sem build step, verificação manual via browser).

**Spec:** `docs/superpowers/specs/2026-09-22-frontend-redacao-design.md`

## Global Constraints

- **Nenhum modelo de dados novo, nenhuma migration.** Todas as 5 rotas de backend são leitura pura ou enriquecimento de uma resposta já existente — nunca escrevem em uma tabela nova.
- **403, nunca 404, para o que não é do chamador** — regra da Fase 3C, reutilizada literalmente em toda rota nova: aluno só vê suas próprias propostas/submissões/correções (via matrícula ativa), professor só vê o que é da própria escola.
- **`essay.js` não é um módulo "puro renderizador" como `evolution.js`.** `evolution.js` só expõe `render(data)`/`renderLoading()`/`renderError()` e deixa `app.js` fazer toda a busca de dados. `essay.js` e `essay-review.js` são maiores e mais interativos (múltiplas sub-telas, formulários, upload) — cada um expõe um único `init()` chamado pelo dispatch do portal (`if (viewName === 'essay') window.EssayView.init();`) e faz sua própria busca/renderização/wiring de eventos internamente. Isso é uma adaptação deliberada do padrão único existente, documentada aqui para não ser "corrigida" de volta para o padrão de `evolution.js` por engano.
- **Sem framework, sem build step.** Scripts carregados via `<script src="...">` direto no HTML, na ordem: módulo novo antes do arquivo principal do portal (mesma ordem que `evolution.js` → `app.js`).
- **Renderização**: template literals + `.innerHTML`, sem virtual DOM. Handlers de evento religados após cada `innerHTML` (re-`querySelectorAll` + `addEventListener`), nunca inline `onclick="..."` a menos que siga o precedente existente (`window.selectOption`, etc. — não usado neste plano).
- **Escape de HTML obrigatório** em todo texto vindo do backend antes de interpolar em template literal — cada arquivo novo define sua própria função local (mesmo padrão já duplicado 4x no repo: `escActivity`/`esc`/`escapeHtml`/`tmEsc`), nunca importa de outro arquivo.
- **Upload de arquivo**: `FormData`, nunca define `Content-Type` manualmente (o browser define o boundary do multipart sozinho). Botão desabilitado durante o envio, reabilitado no fim (sucesso ou erro). Sem barra de progresso (sem precedente no repo).
- **Erro de módulo desabilitado**: cada arquivo novo replica o tradutor de `reception.js` (`translateDetail`-style, regex sobre `"Module 'X' is not enabled for the current school."`) — gating é reativo (deixa a API 403 e traduz), nunca proativo.
- **Autenticação**: mesma convenção de todos os portais — `Authorization: Bearer <papel>:<id>` construído a partir do `state` do portal (`state.studentId`/`state.teacherId`), sem tentar construir login real.
- **Badges de status**: idioma `.badge.is-X` já usado em `styles.css`/`teacher.css` (ex.: `.path-step-badge.is-blocked`) — classes novas seguem o mesmo padrão (`.badge.is-pending-review`, `.is-needs-review`, `.is-approved`, `.is-rejected`).
- **Teste de backend**: `unittest.IsolatedAsyncioTestCase`/`unittest.TestCase`, `sqlite+aiosqlite:///:memory:` + `StaticPool`, mesmo padrão de todas as fases anteriores. **Cuidado**: SQLite não força foreign keys por padrão neste repo — uma junção real (`JOIN`) simplesmente não retorna nada se a linha referenciada não existir de verdade, ao contrário de uma FK que falharia. Toda fixture que envolve `Student`→`Person` (necessário na Task 4) precisa criar o `Person` de verdade, não só um `person_id` aleatório.
- **Teste de frontend**: não existe framework de teste JS neste repo (sem build step, sem `package.json`). Verificação é manual, via `preview_start`/Browser tool contra o servidor de desenvolvimento real, clicando o fluxo ponta a ponta — nunca inventar um script de teste JS que não seria executado por ninguém depois.

---

### Task 1: Rota do aluno — listar propostas abertas

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_submissions.py`
- Modify: `src/agente_ia_edu/api/app.py`
- Test: `tests/test_frontend_r_student_essay_prompts_route.py`

**Interfaces:**
- Consumes: `PromptAssignment`, `EssayPrompt`, `EssaySubmission` (R2, `db/models`, sem modificação), `_authorize_student`/`_resolve_enrollment_or_403` (já existentes em `essay_submissions.py`, sem modificação).
- Produces: `essay_student_prompts_router` montado em `/api/v1/student/essay-prompts`, rota `GET ""` retornando `list[EssayPromptForStudentResponse]`. Nenhuma outra task depende desta rota diretamente (o frontend consome via HTTP), mas o router precisa existir antes da Task 5 poder ser verificada de ponta a ponta.

Esta é a única rota que lista, do ponto de vista do aluno, quais propostas estão abertas para a turma dele e se ele já começou/terminou o envio — sem ela, a tela do aluno não tem por onde começar.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_frontend_r_student_essay_prompts_route.py
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


class StudentEssayPromptsRouteTests(unittest.TestCase):
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

    def _seed(self, code: str, *, with_submission: bool = False):
        async def _seed_async():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"SEP-{code}", name=f"school-{code}")
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

                submission_id = None
                if with_submission:
                    submission_id = uuid.uuid4()
                    session.add(EssaySubmission(
                        id=submission_id, essay_id=uuid.uuid4(), school_id=school.id,
                        prompt_assignment_id=assignment.id, student_id=student.id,
                        mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                        canonical_text="Redacao.", normalized_text_hash="a" * 64,
                        submitted_at=datetime.now(timezone.utc),
                    ))

                await session.commit()
                return assignment.id, submission_id

        return self.loop.run_until_complete(_seed_async())

    def test_lists_open_assignment_with_no_submission_yet(self):
        assignment_id, _submission_id = self._seed("1")
        self._as("student_1")
        resp = self.client.get("/api/v1/student/essay-prompts")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["prompt_assignment_id"], str(assignment_id))
        self.assertEqual(body[0]["title"], "Tema")
        self.assertIsNone(body[0]["my_submission"])

    def test_reflects_existing_submission_status(self):
        _assignment_id, submission_id = self._seed("2", with_submission=True)
        self._as("student_2")
        resp = self.client.get("/api/v1/student/essay-prompts")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertIsNotNone(body[0]["my_submission"])
        self.assertEqual(body[0]["my_submission"]["status"], "SUBMITTED")
        self.assertEqual(body[0]["my_submission"]["id"], str(submission_id))
        self.assertEqual(body[0]["my_submission"]["anchor_mode"], "TEXT_OFFSET")

    def test_does_not_leak_another_school_or_class_assignment(self):
        self._seed("3")
        self._seed("4")
        self._as("student_3")
        resp = self.client.get("/api/v1/student/essay-prompts")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(len(resp.json()), 1)

    def test_requires_student_role(self):
        self._seed("5")
        self._as("teacher_5")
        resp = self.client.get("/api/v1/student/essay-prompts")
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_frontend_r_student_essay_prompts_route.py -v`
Expected: FAIL — `404 Not Found` (a rota ainda não existe).

- [ ] **Step 3: Escrever a rota**

Em `src/agente_ia_edu/api/routes/essay_submissions.py`, adicionar ao final do arquivo (depois de `confirm_essay_submission`), um novo router e sua única rota:

```python
essay_student_prompts_router = APIRouter(
    prefix="/api/v1/student/essay-prompts", tags=["essay-prompts-student"]
)


class MySubmissionSummary(BaseModel):
    id: UUID
    essay_id: UUID
    status: str
    anchor_mode: str


class EssayPromptForStudentResponse(BaseModel):
    prompt_assignment_id: UUID
    title: str
    statement: str
    due_at: Optional[datetime] = None
    status: str
    my_submission: Optional[MySubmissionSummary] = None


@essay_student_prompts_router.get("", response_model=list[EssayPromptForStudentResponse])
async def list_essay_prompts_for_student(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[EssayPromptForStudentResponse]:
    async with session_factory() as session:
        context = await _authorize_student(identity, session)
        school_id = uuid.UUID(str(context.school_id))
        enrollment = await _resolve_enrollment_or_403(
            session, school_id=school_id, external_user_id=identity.external_user_id
        )

        rows = (
            await session.execute(
                select(PromptAssignment, EssayPrompt)
                .join(EssayPrompt, EssayPrompt.id == PromptAssignment.essay_prompt_id)
                .where(
                    PromptAssignment.school_id == school_id,
                    PromptAssignment.class_id == enrollment.class_id,
                )
                .order_by(PromptAssignment.created_at.desc())
            )
        ).all()

        results: list[EssayPromptForStudentResponse] = []
        for assignment, prompt in rows:
            submission = await session.scalar(
                select(EssaySubmission)
                .where(
                    EssaySubmission.prompt_assignment_id == assignment.id,
                    EssaySubmission.student_id == enrollment.student_id,
                    EssaySubmission.status != "SUPERSEDED",
                )
                .order_by(EssaySubmission.created_at.desc())
            )
            my_submission = (
                MySubmissionSummary(
                    id=submission.id, essay_id=submission.essay_id,
                    status=submission.status, anchor_mode=submission.anchor_mode,
                )
                if submission is not None
                else None
            )
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

Adicionar `datetime` ao import já existente `from datetime import ...` — checar o import atual do arquivo primeiro (hoje o arquivo não importa `datetime` no topo; adicionar `from datetime import datetime` junto aos outros imports padrão) e `EssayPrompt` ao import já existente `from ...db.models import EssaySubmission, EssaySubmissionPage, PromptAssignment` (vira `from ...db.models import EssayPrompt, EssaySubmission, EssaySubmissionPage, PromptAssignment`).

- [ ] **Step 4: Registrar o router novo**

Em `src/agente_ia_edu/api/app.py`, adicionar ao import existente:

```python
from .routes.essay_submissions import essay_student_prompts_router, essay_submissions_router
```

(substituindo a linha atual `from .routes.essay_submissions import essay_submissions_router`), e registrar ao lado do router existente:

```python
    app.include_router(essay_submissions_router, dependencies=reception_only_guard)
    app.include_router(essay_student_prompts_router, dependencies=reception_only_guard)
```

- [ ] **Step 5: Rodar os testes**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_frontend_r_student_essay_prompts_route.py -v`
Expected: PASS (4 testes).

Rodar também a suíte de rotas de submissão já existente pra confirmar zero regressão:

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r2_essay_submissions_routes.py tests/test_r3_essay_submission_confirm_triggers_correction.py -v`
Expected: PASS (inalterado).

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_submissions.py \
        src/agente_ia_edu/api/app.py \
        tests/test_frontend_r_student_essay_prompts_route.py
git commit -m "feat(frontend-redacao): add student essay-prompts listing route"
```

---

### Task 2: Rota do aluno — ler a própria devolutiva

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_submissions.py`
- Test: `tests/test_frontend_r_student_essay_correction_route.py`

**Interfaces:**
- Consumes: `EssayCorrection` (R3, `db/models`, sem modificação), `_authorize_student`/`_resolve_enrollment_or_403`/`_submission_for_own_school_or_403` (já existentes, sem modificação).
- Produces: `GET /api/v1/student/essay-submissions/{essay_submission_id}/correction` retornando `StudentCorrectionResponse`. Nada mais no plano depende diretamente desta rota (consumida via HTTP pela Task 7).

Colapsa `NEEDS_REVIEW`/`PENDING_REVIEW`/`REJECTED`/ausência de correção em um único `status="PENDING"` — só quando `status="APPROVED"` os campos de conteúdo (notas, feedback, anotações, alertas) vêm preenchidos. Segue o mesmo padrão de `essay_corrections.py`: campos JSON tipados como `dict`/`list` simples, não como os modelos Pydantic estritos de `essay_engine_contract`.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_frontend_r_student_essay_correction_route.py
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


class StudentEssayCorrectionRouteTests(unittest.TestCase):
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

    def _seed_submission(self, code: str):
        async def _seed_async():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"SEC-{code}", name=f"school-{code}")
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
                submission = EssaySubmission(
                    id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                    prompt_assignment_id=assignment.id, student_id=student.id,
                    mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                    canonical_text="Redacao.", normalized_text_hash="a" * 64,
                    submitted_at=datetime.now(timezone.utc),
                )
                session.add(submission)
                await session.commit()
                return submission.id

        return self.loop.run_until_complete(_seed_async())

    def _add_correction(self, submission_id, *, status: str, with_content: bool):
        async def _add_async():
            async with self.factory() as session:
                submission = await session.get(EssaySubmission, submission_id)
                ai_output = None
                final_scores = None
                final_feedback = None
                if with_content:
                    ai_output = {
                        "annotations": [{"letter": "A", "short_comment": "ok"}],
                        "rewrites": [], "intervention": {"respeita_direitos_humanos": True}, "alerts": [],
                    }
                    final_scores = {"total": 800}
                    final_feedback = {"next_essay_strategy": "Revisar conectivos."}
                session.add(EssayCorrection(
                    id=uuid.uuid4(), school_id=submission.school_id,
                    essay_submission_id=submission_id, correction_key="k" * 64,
                    rubric_version="ENEM_2025", model_version="gpt-test",
                    prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                    ai_output=ai_output, final_scores=final_scores, final_feedback=final_feedback,
                    status=status,
                    reviewed_at=datetime.now(timezone.utc) if status in ("APPROVED", "REJECTED") else None,
                    published_at=datetime.now(timezone.utc) if status == "APPROVED" else None,
                ))
                await session.commit()

        self.loop.run_until_complete(_add_async())

    def test_pending_review_collapses_to_pending_with_no_content(self):
        submission_id = self._seed_submission("1")
        self._add_correction(submission_id, status="PENDING_REVIEW", with_content=False)
        self._as("student_1")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "PENDING")
        self.assertIsNone(body["final_scores"])
        self.assertIsNone(body["annotations"])

    def test_rejected_also_collapses_to_pending(self):
        submission_id = self._seed_submission("2")
        self._add_correction(submission_id, status="REJECTED", with_content=False)
        self._as("student_2")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "PENDING")

    def test_no_correction_yet_is_pending(self):
        submission_id = self._seed_submission("3")
        self._as("student_3")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "PENDING")

    def test_approved_exposes_full_content(self):
        submission_id = self._seed_submission("4")
        self._add_correction(submission_id, status="APPROVED", with_content=True)
        self._as("student_4")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "APPROVED")
        self.assertEqual(body["final_scores"]["total"], 800)
        self.assertEqual(len(body["annotations"]), 1)
        self.assertTrue(body["intervention"]["respeita_direitos_humanos"])

    def test_another_students_submission_is_403(self):
        submission_id = self._seed_submission("5")
        self._add_correction(submission_id, status="APPROVED", with_content=True)
        self._seed_submission("6")
        self._as("student_6")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_frontend_r_student_essay_correction_route.py -v`
Expected: FAIL — `404 Not Found`.

- [ ] **Step 3: Escrever a rota**

Em `src/agente_ia_edu/api/routes/essay_submissions.py`, adicionar `EssayCorrection` ao import de `...db.models` (fica `from ...db.models import EssayCorrection, EssayPrompt, EssaySubmission, EssaySubmissionPage, PromptAssignment`), e adicionar, logo depois de `confirm_essay_submission`:

```python
class StudentCorrectionResponse(BaseModel):
    essay_submission_id: UUID
    status: str
    final_scores: Optional[dict] = None
    final_feedback: Optional[dict] = None
    annotations: Optional[list] = None
    rewrites: Optional[list] = None
    intervention: Optional[dict] = None
    alerts: Optional[list] = None


@essay_submissions_router.get(
    "/{essay_submission_id}/correction", response_model=StudentCorrectionResponse
)
async def get_essay_submission_correction(
    essay_submission_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> StudentCorrectionResponse:
    async with session_factory() as session:
        context = await _authorize_student(identity, session)
        school_id = uuid.UUID(str(context.school_id))
        enrollment = await _resolve_enrollment_or_403(
            session, school_id=school_id, external_user_id=identity.external_user_id
        )
        submission = await _submission_for_own_school_or_403(
            session, essay_submission_id=essay_submission_id, school_id=school_id,
            student_id=enrollment.student_id,
        )
        correction = await session.scalar(
            select(EssayCorrection).where(EssayCorrection.essay_submission_id == submission.id)
        )
        if correction is None or correction.status != "APPROVED":
            return StudentCorrectionResponse(essay_submission_id=submission.id, status="PENDING")

        ai_output = correction.ai_output or {}
        return StudentCorrectionResponse(
            essay_submission_id=submission.id, status="APPROVED",
            final_scores=correction.final_scores, final_feedback=correction.final_feedback,
            annotations=ai_output.get("annotations"), rewrites=ai_output.get("rewrites"),
            intervention=ai_output.get("intervention"), alerts=ai_output.get("alerts"),
        )
```

- [ ] **Step 4: Rodar os testes**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_frontend_r_student_essay_correction_route.py -v`
Expected: PASS (5 testes).

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_frontend_r_student_essay_prompts_route.py tests/test_r2_essay_submissions_routes.py -v`
Expected: PASS (inalterado — confirma que a Task 1 e o R2 continuam intactos).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_submissions.py \
        tests/test_frontend_r_student_essay_correction_route.py
git commit -m "feat(frontend-redacao): add student essay correction read route"
```

---

### Task 3: Rotas do professor — listar e detalhar propostas

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_prompts.py`
- Test: `tests/test_frontend_r_teacher_essay_prompts_list_route.py`

**Interfaces:**
- Consumes: `EssayPrompt`, `PromptMaterial`, `PromptAssignment` (R2, `db/models`, sem modificação), `_authorize`/`_prompt_for_own_school_or_403` (já existentes em `essay_prompts.py`, sem modificação), `EssayPromptResponse`/`PromptMaterialResponse`/`PromptAssignmentResponse` (já existentes no mesmo arquivo, reaproveitados sem mudança).
- Produces: `GET /api/v1/catalog/essay-prompts` → `list[EssayPromptResponse]`; `GET /api/v1/catalog/essay-prompts/{essay_prompt_id}` → `EssayPromptDetailResponse`. Consumidas via HTTP pela Task 8.

Hoje `essay_prompts.py` só tem rotas de escrita (3 `POST`) — nenhuma forma de listar ou ver o que já foi criado. Estas duas rotas fecham essa lacuna sem tocar em nenhuma rota existente.

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_frontend_r_teacher_essay_prompts_list_route.py
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
    PromptAssignment,
    PromptMaterial,
    School,
    Segment,
    UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class TeacherEssayPromptsListRouteTests(unittest.TestCase):
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

    def _seed(self, code: str, *, with_material_and_assignment: bool = False):
        async def _seed_async():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"TEP-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id=f"teacher_{code}", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
                    year=2026, status="DRAFT", created_by_external_identity="teacher:t",
                )
                session.add(prompt)
                await session.flush()

                if with_material_and_assignment:
                    session.add(PromptMaterial(
                        id=uuid.uuid4(), essay_prompt_id=prompt.id, material_type="TEXT",
                        content="Texto de apoio.", position=0,
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
                    session.add(PromptAssignment(
                        id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                        class_id=klass.id, assigned_by_external_identity="teacher:t",
                    ))

                await session.commit()
                return school.id, prompt.id

        return self.loop.run_until_complete(_seed_async())

    def test_list_scopes_to_own_school(self):
        school_id, prompt_id = self._seed("1")
        self._seed("2")
        self._as("teacher_1")
        resp = self.client.get("/api/v1/catalog/essay-prompts")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["id"], str(prompt_id))

    def test_detail_includes_materials_and_assignments(self):
        school_id, prompt_id = self._seed("3", with_material_and_assignment=True)
        self._as("teacher_3")
        resp = self.client.get(f"/api/v1/catalog/essay-prompts/{prompt_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(len(body["materials"]), 1)
        self.assertEqual(len(body["assignments"]), 1)

    def test_detail_from_another_school_is_403(self):
        _school_id, prompt_id = self._seed("4")
        self._seed("5")
        self._as("teacher_5")
        resp = self.client.get(f"/api/v1/catalog/essay-prompts/{prompt_id}")
        self.assertEqual(resp.status_code, 403)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_frontend_r_teacher_essay_prompts_list_route.py -v`
Expected: FAIL — `404 Not Found`.

- [ ] **Step 3: Escrever as rotas**

Em `src/agente_ia_edu/api/routes/essay_prompts.py`, ajustar os imports:

```python
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
```

e

```python
from ...db.models import EssayPrompt, PromptAssignment, PromptMaterial
```

Adicionar, depois de `create_prompt_assignment` (final do arquivo):

```python
class EssayPromptDetailResponse(EssayPromptResponse):
    materials: list[PromptMaterialResponse]
    assignments: list[PromptAssignmentResponse]


@essay_prompts_router.get("", response_model=list[EssayPromptResponse])
async def list_essay_prompts(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[EssayPromptResponse]:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        result = await session.execute(
            select(EssayPrompt)
            .where(EssayPrompt.school_id == school_id)
            .order_by(EssayPrompt.created_at.desc())
        )
        return [
            EssayPromptResponse(
                id=p.id, school_id=p.school_id, title=p.title,
                statement=p.statement, year=p.year, status=p.status,
            )
            for p in result.scalars().all()
        ]


@essay_prompts_router.get("/{essay_prompt_id}", response_model=EssayPromptDetailResponse)
async def get_essay_prompt_detail(
    essay_prompt_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> EssayPromptDetailResponse:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        prompt = await _prompt_for_own_school_or_403(
            session, essay_prompt_id=essay_prompt_id, school_id=school_id
        )
        materials = (
            await session.execute(
                select(PromptMaterial)
                .where(PromptMaterial.essay_prompt_id == prompt.id)
                .order_by(PromptMaterial.position)
            )
        ).scalars().all()
        assignments = (
            await session.execute(
                select(PromptAssignment).where(PromptAssignment.essay_prompt_id == prompt.id)
            )
        ).scalars().all()
        return EssayPromptDetailResponse(
            id=prompt.id, school_id=prompt.school_id, title=prompt.title,
            statement=prompt.statement, year=prompt.year, status=prompt.status,
            materials=[
                PromptMaterialResponse(
                    id=m.id, essay_prompt_id=m.essay_prompt_id, material_type=m.material_type,
                    content=m.content, storage_uri=m.storage_uri, position=m.position,
                )
                for m in materials
            ],
            assignments=[
                PromptAssignmentResponse(
                    id=a.id, school_id=a.school_id, essay_prompt_id=a.essay_prompt_id,
                    class_id=a.class_id, status=a.status, validation_enabled=a.validation_enabled,
                )
                for a in assignments
            ],
        )
```

- [ ] **Step 4: Rodar os testes**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_frontend_r_teacher_essay_prompts_list_route.py -v`
Expected: PASS (3 testes).

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r3_prompt_assignment_validation_policy.py -v`
Expected: PASS (inalterado — confirma que as rotas de escrita existentes continuam intactas).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_prompts.py \
        tests/test_frontend_r_teacher_essay_prompts_list_route.py
git commit -m "feat(frontend-redacao): add teacher essay-prompts list and detail routes"
```

---

### Task 4: Enriquecer a listagem de correções com nome do aluno e título da proposta

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_corrections.py`
- Test: `tests/test_frontend_r_teacher_essay_corrections_enriched_list.py`

**Interfaces:**
- Consumes: `EssayCorrection`, `EssaySubmission`, `Student`, `Person`, `PromptAssignment`, `EssayPrompt` (R0-R3, `db/models`, sem modificação).
- Produces: `EssayCorrectionResponse` ganha três campos novos e opcionais (`student_name`, `prompt_title`, `submitted_at`), populados só por `list_essay_corrections`; `approve`/`reject`/`retry`/`bulk-approve` continuam retornando o mesmo formato, com esses três campos como `null` (mudança aditiva, sem quebra de contrato). Consumida via HTTP pela Task 10.

Sem esses três campos, a fila de revisão do professor é uma lista de UUIDs e status — ilegível. `list_essay_corrections` passa a montar a resposta com um `JOIN` direto em vez de `EssayCorrectionService.list_by_status` (que continua existindo e sendo usado/testado como está, só deixa de ser chamado por esta rota específica).

- [ ] **Step 1: Escrever o teste que falha**

```python
# tests/test_frontend_r_teacher_essay_corrections_enriched_list.py
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
    EssayCorrection,
    EssayPrompt,
    EssaySubmission,
    Person,
    PromptAssignment,
    School,
    Student,
    UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class TeacherEssayCorrectionsEnrichedListTests(unittest.TestCase):
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

    def _seed_pending_correction(self, code: str):
        async def _seed_async():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"ENR-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id=f"teacher_{code}", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                person = Person(id=uuid.uuid4(), school_id=school.id, full_name="Ana Beatriz")
                session.add(person)
                await session.flush()
                student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id, student_code=f"ST-{code}")
                session.add(student)
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Desafios da mobilidade urbana",
                    statement="Disserte.", year=2026, status="ACTIVE",
                    created_by_external_identity="teacher:t",
                )
                session.add(prompt)
                await session.flush()
                assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                    class_id=uuid.uuid4(), assigned_by_external_identity="teacher:t",
                )
                session.add(assignment)
                await session.flush()
                submitted_at = datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc)
                submission = EssaySubmission(
                    id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                    prompt_assignment_id=assignment.id, student_id=student.id,
                    mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                    canonical_text="Redacao.", normalized_text_hash="a" * 64,
                    submitted_at=submitted_at,
                )
                session.add(submission)
                await session.flush()
                correction = EssayCorrection(
                    id=uuid.uuid4(), school_id=school.id, essay_submission_id=submission.id,
                    correction_key="k" * 64, rubric_version="ENEM_2025", model_version="gpt-test",
                    prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                    ai_output={"scores": None}, status="PENDING_REVIEW",
                )
                session.add(correction)
                await session.commit()
                return correction.id

        return self.loop.run_until_complete(_seed_async())

    def test_list_includes_student_name_prompt_title_and_submitted_at(self):
        self._seed_pending_correction("1")
        self._as("teacher_1")
        resp = self.client.get("/api/v1/teacher/essay-corrections")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(len(body), 1)
        self.assertEqual(body[0]["student_name"], "Ana Beatriz")
        self.assertEqual(body[0]["prompt_title"], "Desafios da mobilidade urbana")
        # Parse rather than compare the raw string: Pydantic's exact ISO
        # serialization format (trailing "Z" vs "+00:00") is not worth
        # pinning down here, only that the timestamp round-trips correctly.
        # Matches the fixed instant _seed_pending_correction hardcodes.
        returned = datetime.fromisoformat(body[0]["submitted_at"].replace("Z", "+00:00"))
        self.assertEqual(returned, datetime(2026, 9, 20, 12, 0, tzinfo=timezone.utc))

    def test_approve_still_works_with_the_new_optional_fields(self):
        correction_id = self._seed_pending_correction("2")
        self._as("teacher_2")
        resp = self.client.post(f"/api/v1/teacher/essay-corrections/{correction_id}/approve", json={})
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.json()["status"], "APPROVED")
        self.assertIsNone(resp.json()["student_name"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_frontend_r_teacher_essay_corrections_enriched_list.py -v`
Expected: FAIL — `KeyError`/`AssertionError` (os campos `student_name`/`prompt_title`/`submitted_at` ainda não existem na resposta).

- [ ] **Step 3: Enriquecer a rota**

Em `src/agente_ia_edu/api/routes/essay_corrections.py`, ajustar os imports:

```python
from datetime import datetime

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..dependencies import get_current_identity, get_session_factory
from ...db.models import EssayCorrection, EssayPrompt, EssaySubmission, Person, PromptAssignment, Student
```

Adicionar os três campos novos em `EssayCorrectionResponse` (depois de `reviewed_by_external_identity`):

```python
    student_name: Optional[str] = None
    prompt_title: Optional[str] = None
    submitted_at: Optional[datetime] = None
```

Trocar `_correction_to_response` para aceitar os três campos opcionalmente:

```python
def _correction_to_response(
    correction: EssayCorrection, *, student_name: Optional[str] = None,
    prompt_title: Optional[str] = None, submitted_at: Optional[datetime] = None,
) -> EssayCorrectionResponse:
    return EssayCorrectionResponse(
        id=correction.id, essay_submission_id=correction.essay_submission_id,
        school_id=correction.school_id, status=correction.status,
        rubric_version=correction.rubric_version, model_version=correction.model_version,
        prompt_version=correction.prompt_version, engine_version=correction.engine_version,
        ai_output=correction.ai_output, final_scores=correction.final_scores,
        final_feedback=correction.final_feedback, failure_reason=correction.failure_reason,
        reviewed_by_external_identity=correction.reviewed_by_external_identity,
        student_name=student_name, prompt_title=prompt_title, submitted_at=submitted_at,
    )
```

Trocar o corpo de `list_essay_corrections` (mantendo a assinatura da rota igual):

```python
@essay_corrections_router.get("", response_model=list[EssayCorrectionResponse])
async def list_essay_corrections(
    status: str = "PENDING_REVIEW",
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[EssayCorrectionResponse]:
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        if status not in _LISTABLE_STATUSES:
            raise HTTPException(status_code=422, detail=f"Unknown status: {status!r}")
        rows = (
            await session.execute(
                select(EssayCorrection, Person.full_name, EssayPrompt.title, EssaySubmission.submitted_at)
                .join(EssaySubmission, EssaySubmission.id == EssayCorrection.essay_submission_id)
                .join(Student, Student.id == EssaySubmission.student_id)
                .join(Person, Person.id == Student.person_id)
                .join(PromptAssignment, PromptAssignment.id == EssaySubmission.prompt_assignment_id)
                .join(EssayPrompt, EssayPrompt.id == PromptAssignment.essay_prompt_id)
                .where(EssayCorrection.school_id == school_id, EssayCorrection.status == status)
                .order_by(EssayCorrection.created_at)
            )
        ).all()
        return [
            _correction_to_response(
                correction, student_name=student_name, prompt_title=prompt_title, submitted_at=submitted_at,
            )
            for correction, student_name, prompt_title, submitted_at in rows
        ]
```

- [ ] **Step 4: Rodar os testes**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_frontend_r_teacher_essay_corrections_enriched_list.py -v`
Expected: PASS (2 testes).

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r3_essay_corrections_routes.py -v`
Expected: PASS (inalterado — confirma que `approve`/`reject`/`retry`/`bulk-approve` continuam corretos).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_corrections.py \
        tests/test_frontend_r_teacher_essay_corrections_enriched_list.py
git commit -m "feat(frontend-redacao): enrich essay corrections list with student and prompt info"
```

---

### Task 5: Portal do aluno — esqueleto de `essay.js` + lista de propostas + envio digitado

**Files:**
- Create: `src/agente_ia_edu/web/essay.js`
- Modify: `src/agente_ia_edu/web/index.html`
- Modify: `src/agente_ia_edu/web/app.js`
- Modify: `src/agente_ia_edu/web/styles.css`

**Interfaces:**
- Consumes: `GET /api/v1/student/essay-prompts` (Task 1), `POST /api/v1/student/essay-submissions` (R2, já existe, inalterado).
- Produces: `window.EssayView.init()` — chamado por `app.js` no dispatch de `switchView`. Task 6 e Task 7 estendem o mesmo arquivo `essay.js` (mais funções internas, mesmo `init()`).

Sem framework de teste JS neste repo — a verificação desta e das próximas tasks de frontend é manual, via `preview_start` + Browser tool, navegando o fluxo real contra o servidor de desenvolvimento.

- [ ] **Step 1: Trocar o placeholder por um container real**

Em `src/agente_ia_edu/web/index.html`, substituir o painel placeholder atual:

```html
        <!-- 7. REDAÇÃO VIEW (PLACEHOLDER) -->
        <section id="view-essay" class="view-panel">
          <div class="card essay-placeholder-card">
            <div class="essay-icon">📝</div>
            <h2>Módulo REDAÇÃO IA</h2>
            <p>Em breve você poderá praticar suas redações no padrão ENEM e receber correção instantânea com inteligência artificial.</p>
            <span class="badge badge-accent">Próximo Lançamento</span>
          </div>
        </section>
```

por:

```html
        <!-- 7. REDAÇÃO VIEW -->
        <section id="view-essay" class="view-panel">
          <div id="essay-root" aria-live="polite"></div>
        </section>
```

Adicionar o script novo antes de `app.js` (mesma ordem que `evolution.js`):

```html
  <script src="evolution.js"></script>
  <script src="essay.js"></script>
  <script src="app.js"></script>
```

- [ ] **Step 2: Ligar o dispatch em `app.js`**

Em `src/agente_ia_edu/web/app.js`, adicionar à lista de "Trigger View Loaders" dentro de `switchView`:

```js
    if (viewName === 'essay') window.EssayView.init();
```

- [ ] **Step 3: Escrever `essay.js`**

```javascript
/* AGENTE IA EDU — Portal do Aluno — módulo de Redação */
(function essayModule(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.EssayView = api;
})(typeof window !== 'undefined' ? window : null, function createEssayView() {
  let container = null;
  let prompts = [];

  function escEssay(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[character]);
  }

  function translateDetail(detail) {
    if (typeof detail === 'string') {
      const moduleMatch = detail.match(/^Module '(.+)' is not enabled for the current school\.$/);
      if (moduleMatch) return `O módulo '${moduleMatch[1]}' não está habilitado para esta escola.`;
      return detail;
    }
    if (detail && detail.message) return detail.message;
    return 'Não foi possível completar a ação.';
  }

  function essayHeaders(extra = {}) {
    const accessToken = sessionStorage.getItem('studentAccessToken') || 'student:alice';
    return { 'Authorization': `Bearer ${accessToken}`, ...extra };
  }

  async function essayRequest(path, options = {}) {
    const res = await fetch(path, { ...options, headers: essayHeaders(options.headers || {}) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const error = new Error(translateDetail(data.detail));
      error.status = res.status;
      throw error;
    }
    return data;
  }

  function submissionState(mySubmission) {
    if (!mySubmission) return { label: 'Enviar redação', badge: 'badge-accent' };
    if (mySubmission.status === 'PENDING_TRANSCRIPTION' || mySubmission.status === 'PENDING_CONFIRMATION') {
      return { label: 'Continuar envio', badge: 'badge-accent' };
    }
    if (mySubmission.status === 'SUBMITTED') return { label: 'Em correção / ver devolutiva', badge: 'badge-primary' };
    return { label: 'Ver detalhes', badge: 'badge-primary' };
  }

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

  function openPrompt(prompt) {
    if (!prompt.my_submission) {
      renderNewSubmissionForm(prompt);
      return;
    }
    if (prompt.my_submission.status === 'SUBMITTED') {
      renderDevolutiva(prompt);
      return;
    }
    renderContinueUpload(prompt);
  }

  function renderNewSubmissionForm(prompt) {
    container.innerHTML = `
      <div class="card essay-form">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <h3>${escEssay(prompt.title)}</h3>
        <p>${escEssay(prompt.statement)}</p>
        <div class="essay-mode-tabs">
          <button class="btn btn-secondary is-active" type="button" data-mode="TYPED">Digitar</button>
          <button class="btn btn-secondary" type="button" data-mode="PHOTO">Fotografar</button>
          <button class="btn btn-secondary" type="button" data-mode="PDF">Enviar PDF</button>
        </div>
        <div id="essay-mode-body"></div>
      </div>`;

    container.querySelector('[data-back]').addEventListener('click', () => renderList());
    container.querySelectorAll('[data-mode]').forEach((btn) => {
      btn.addEventListener('click', () => {
        container.querySelectorAll('[data-mode]').forEach((b) => b.classList.remove('is-active'));
        btn.classList.add('is-active');
        renderModeBody(btn.dataset.mode, prompt);
      });
    });
    renderModeBody('TYPED', prompt);
  }

  function renderModeBody(mode, prompt) {
    const body = container.querySelector('#essay-mode-body');
    if (mode !== 'TYPED') {
      body.innerHTML = '<p class="empty-text">Este modo de envio é adicionado na próxima etapa.</p>';
      return;
    }
    body.innerHTML = `
      <form id="essay-typed-form">
        <div class="form-group">
          <label for="essay-typed-text">Sua redação</label>
          <textarea id="essay-typed-text" rows="16" required></textarea>
        </div>
        <button class="btn btn-primary" type="submit">Enviar redação</button>
        <p id="essay-typed-msg" class="tm-msg" hidden></p>
      </form>`;

    body.querySelector('#essay-typed-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const text = body.querySelector('#essay-typed-text').value.trim();
      const msg = body.querySelector('#essay-typed-msg');
      const submitBtn = ev.target.querySelector('button[type="submit"]');
      if (!text) return;
      submitBtn.disabled = true;
      msg.hidden = true;
      try {
        await essayRequest('/api/v1/student/essay-submissions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt_assignment_id: prompt.prompt_assignment_id, mode: 'TYPED', text }),
        });
        await loadPrompts();
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
        submitBtn.disabled = false;
      }
    });
  }

  function renderContinueUpload() {
    // Implementado na Task 6.
    container.innerHTML = '<div class="card"><p class="empty-text">Continuar envio de foto/PDF — próxima etapa.</p></div>';
  }

  function renderDevolutiva() {
    // Implementado na Task 7.
    container.innerHTML = '<div class="card"><p class="empty-text">Devolutiva — próxima etapa.</p></div>';
  }

  async function loadPrompts() {
    container.innerHTML = '<p class="empty-text">Carregando propostas...</p>';
    try {
      prompts = await essayRequest('/api/v1/student/essay-prompts');
      renderList();
    } catch (e) {
      container.innerHTML = `<div class="card"><p class="empty-text">${escEssay(e.message)}</p></div>`;
    }
  }

  function init() {
    container = document.getElementById('essay-root');
    if (!container) return;
    loadPrompts();
  }

  return { init };
});
```

- [ ] **Step 4: Adicionar CSS mínimo**

Em `src/agente_ia_edu/web/styles.css`, adicionar ao final do arquivo:

```css
/* Módulo Redação */
.essay-prompt-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(280px, 1fr)); gap: 16px; }
.essay-prompt-card { display: flex; flex-direction: column; gap: 10px; }
.essay-mode-tabs { display: flex; gap: 8px; margin: 12px 0; }
.essay-mode-tabs .is-active { background: var(--primary); color: #fff; }
.essay-form input, .essay-form textarea, .essay-form select {
  width: 100%; padding: 12px; color: var(--text-main); font: inherit;
  background: #fff; border: 1px solid #cbd8e7; border-radius: 6px; outline: none;
}
.essay-form input:focus, .essay-form textarea:focus {
  border-color: #0f9f91; box-shadow: 0 0 0 3px rgba(15, 159, 145, .14);
}
```

- [ ] **Step 5: Verificar manualmente no browser**

Use `preview_start` para abrir o servidor de desenvolvimento, navegue até o portal do aluno (`index.html`), clique em "Redação" na barra lateral, e confirme:
- A lista de propostas carrega (é preciso ter ao menos uma `PromptAssignment` de teste no banco — usar `scripts/seed_demo_data.py` se necessário, ou criar uma proposta manualmente via a API do professor).
- Clicar em "Enviar redação" abre o formulário com a aba "Digitar" ativa.
- Enviar um texto cria a submissão (confirme via `read_network_requests` que o `POST` retornou 201) e a lista recarrega mostrando "Em correção / ver devolutiva".

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/web/essay.js \
        src/agente_ia_edu/web/index.html \
        src/agente_ia_edu/web/app.js \
        src/agente_ia_edu/web/styles.css
git commit -m "feat(frontend-redacao): add student essay list and typed submission flow"
```

---

### Task 6: Rota de imagem de página + fluxo de upload/revisão/confirmação no aluno

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_submissions.py`
- Modify: `src/agente_ia_edu/web/essay.js`
- Modify: `src/agente_ia_edu/web/styles.css`
- Test: `tests/test_frontend_r_student_essay_page_image_route.py`

**Interfaces:**
- Consumes: `POST .../pages`, `POST .../document`, `GET .../pages`, `PATCH .../pages/{n}`, `POST .../confirm` (R2, já existentes, inalterados); `_submission_for_own_school_or_403` (já existente).
- Produces: `GET /api/v1/student/essay-submissions/{essay_submission_id}/pages/{page_number}/image` — serve os bytes da imagem já enviada. Sem esta rota, `<img>` não tem como carregar a foto: `storage_uri` é um caminho de arquivo local (`MaterialStorage`), não uma URL — descoberto ao desenhar esta task, não estava na spec original.

`essay.js` ganha o fluxo real de foto/PDF: criar a submissão no modo escolhido, enviar arquivos, revisar o texto do OCR por página (quando `anchor_mode=TEXT_OFFSET`), e confirmar só quando toda página tiver `reviewed_text`.

- [ ] **Step 1: Escrever o teste que falha (rota de imagem)**

```python
# tests/test_frontend_r_student_essay_page_image_route.py
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


class StudentEssayPageImageRouteTests(unittest.TestCase):
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
        cls.tmp_dir = Path("/tmp/r_frontend_page_image_test")
        cls.tmp_dir.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _seed_submission_with_page(self, code: str):
        image_path = self.tmp_dir / f"page-{code}.png"
        image_path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)

        async def _seed_async():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"PIM-{code}", name=f"school-{code}")
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
                submission = EssaySubmission(
                    id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
                    prompt_assignment_id=assignment.id, student_id=student.id,
                    mode="PHOTO", anchor_mode="IMAGE_REGION", status="PENDING_TRANSCRIPTION",
                )
                session.add(submission)
                await session.flush()
                session.add(EssaySubmissionPage(
                    id=uuid.uuid4(), essay_submission_id=submission.id, page_number=1,
                    storage_uri=str(image_path),
                ))
                await session.commit()
                return submission.id

        return self.loop.run_until_complete(_seed_async())

    def test_returns_the_image_bytes_for_the_owning_student(self):
        submission_id = self._seed_submission_with_page("1")
        self._as("student_1")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/pages/1/image")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.content.startswith(b"\x89PNG"))

    def test_another_students_page_image_is_403(self):
        submission_id = self._seed_submission_with_page("2")
        self._seed_submission_with_page("3")
        self._as("student_3")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/pages/1/image")
        self.assertEqual(resp.status_code, 403)

    def test_missing_page_number_is_404(self):
        submission_id = self._seed_submission_with_page("4")
        self._as("student_4")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/pages/99/image")
        self.assertEqual(resp.status_code, 404)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e confirmar que falha**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_frontend_r_student_essay_page_image_route.py -v`
Expected: FAIL — `404 Not Found` (a rota ainda não existe, nem para o caso que deveria dar 200).

- [ ] **Step 3: Escrever a rota de imagem**

Em `src/agente_ia_edu/api/routes/essay_submissions.py`, adicionar ao import de `fastapi.responses`:

```python
from fastapi.responses import FileResponse
```

Adicionar, depois da rota de correção criada na Task 2:

```python
@essay_submissions_router.get("/{essay_submission_id}/pages/{page_number}/image")
async def get_essay_submission_page_image(
    essay_submission_id: UUID,
    page_number: int,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        context = await _authorize_student(identity, session)
        school_id = uuid.UUID(str(context.school_id))
        enrollment = await _resolve_enrollment_or_403(
            session, school_id=school_id, external_user_id=identity.external_user_id
        )
        submission = await _submission_for_own_school_or_403(
            session, essay_submission_id=essay_submission_id, school_id=school_id,
            student_id=enrollment.student_id,
        )
        page = await session.scalar(
            select(EssaySubmissionPage).where(
                EssaySubmissionPage.essay_submission_id == submission.id,
                EssaySubmissionPage.page_number == page_number,
            )
        )
        if page is None:
            raise HTTPException(status_code=404, detail="Page not found")
        return FileResponse(page.storage_uri)
```

- [ ] **Step 4: Rodar o teste da rota**

Run: `PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_frontend_r_student_essay_page_image_route.py -v`
Expected: PASS (3 testes).

- [ ] **Step 5: Substituir o fluxo de upload em `essay.js`**

Em `src/agente_ia_edu/web/essay.js`, substituir a função `renderModeBody` inteira (escrita na Task 5) por:

```javascript
  async function renderModeBody(mode, prompt) {
    const body = container.querySelector('#essay-mode-body');
    if (mode === 'TYPED') {
      renderTypedForm(body, prompt);
      return;
    }
    body.innerHTML = '<p class="empty-text">Preparando envio...</p>';
    try {
      const submission = await essayRequest('/api/v1/student/essay-submissions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt_assignment_id: prompt.prompt_assignment_id, mode }),
      });
      await renderUploadArea(body, submission.id, submission.anchor_mode, mode);
    } catch (e) {
      body.innerHTML = `<p class="empty-text">${escEssay(e.message)}</p>`;
    }
  }

  function renderTypedForm(body, prompt) {
    body.innerHTML = `
      <form id="essay-typed-form">
        <div class="form-group">
          <label for="essay-typed-text">Sua redação</label>
          <textarea id="essay-typed-text" rows="16" required></textarea>
        </div>
        <button class="btn btn-primary" type="submit">Enviar redação</button>
        <p id="essay-typed-msg" class="tm-msg" hidden></p>
      </form>`;

    body.querySelector('#essay-typed-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const text = body.querySelector('#essay-typed-text').value.trim();
      const msg = body.querySelector('#essay-typed-msg');
      const submitBtn = ev.target.querySelector('button[type="submit"]');
      if (!text) return;
      submitBtn.disabled = true;
      msg.hidden = true;
      try {
        await essayRequest('/api/v1/student/essay-submissions', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ prompt_assignment_id: prompt.prompt_assignment_id, mode: 'TYPED', text }),
        });
        await loadPrompts();
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
        submitBtn.disabled = false;
      }
    });
  }

  async function renderUploadArea(target, submissionId, anchorMode, mode) {
    target.innerHTML = `
      <div class="form-group">
        <label for="essay-file-input">${mode === 'PDF' ? 'Arquivo PDF (até 25MB)' : 'Fotos das páginas (até 25MB cada)'}</label>
        <input id="essay-file-input" type="file" ${mode === 'PDF' ? 'accept=".pdf"' : 'accept="image/*" multiple capture="environment"'}>
      </div>
      <p id="essay-upload-msg" class="tm-msg" hidden></p>
      <div id="essay-pages-list"></div>
      <button class="btn btn-primary" type="button" id="essay-confirm-btn" disabled>Confirmar envio</button>
      <p id="essay-confirm-msg" class="tm-msg" hidden></p>`;

    target.querySelector('#essay-file-input').addEventListener('change', async (ev) => {
      const files = Array.from(ev.target.files || []);
      if (!files.length) return;
      const msg = target.querySelector('#essay-upload-msg');
      msg.hidden = true;
      try {
        if (mode === 'PDF') {
          const form = new FormData();
          form.append('file', files[0]);
          await essayRequest(`/api/v1/student/essay-submissions/${submissionId}/document`, {
            method: 'POST', body: form,
          });
        } else {
          const existing = await essayRequest(`/api/v1/student/essay-submissions/${submissionId}/pages`);
          let nextPage = existing.length + 1;
          for (const file of files) {
            const form = new FormData();
            form.append('page_number', String(nextPage));
            form.append('file', file);
            await essayRequest(`/api/v1/student/essay-submissions/${submissionId}/pages`, {
              method: 'POST', body: form,
            });
            nextPage += 1;
          }
        }
        await refreshPages(target, submissionId, anchorMode);
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
      }
    });

    await refreshPages(target, submissionId, anchorMode);
  }

  async function refreshPages(target, submissionId, anchorMode) {
    const list = target.querySelector('#essay-pages-list');
    const confirmBtn = target.querySelector('#essay-confirm-btn');
    let pages = [];
    try {
      pages = await essayRequest(`/api/v1/student/essay-submissions/${submissionId}/pages`);
    } catch (e) {
      list.innerHTML = `<p class="empty-text">${escEssay(e.message)}</p>`;
      return;
    }

    list.innerHTML = pages.map((p) => {
      const ocrText = (p.ocr_tokens || []).map((t) => t.text).join(' ');
      const reviewBlock = anchorMode === 'TEXT_OFFSET' ? `
        <div class="form-group">
          <label for="essay-review-${p.page_number}">Texto revisado (página ${p.page_number})</label>
          <textarea id="essay-review-${p.page_number}" rows="6">${escEssay(p.reviewed_text || ocrText)}</textarea>
        </div>
        <button class="btn btn-secondary" type="button" data-save-review="${p.page_number}">Salvar revisão</button>
        <p class="essay-review-status">${p.reviewed_text ? '✓ Revisado' : 'Pendente de revisão'}</p>
      ` : '<p class="empty-text">Página enviada.</p>';
      return `
        <div class="card essay-page-card" data-page="${p.page_number}">
          <img src="/api/v1/student/essay-submissions/${submissionId}/pages/${p.page_number}/image" alt="Página ${p.page_number}">
          ${reviewBlock}
        </div>`;
    }).join('');

    list.querySelectorAll('[data-save-review]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const pageNumber = btn.dataset.saveReview;
        const text = list.querySelector(`#essay-review-${pageNumber}`).value.trim();
        btn.disabled = true;
        try {
          await essayRequest(`/api/v1/student/essay-submissions/${submissionId}/pages/${pageNumber}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reviewed_text: text }),
          });
          await refreshPages(target, submissionId, anchorMode);
        } catch (e) {
          btn.disabled = false;
          btn.insertAdjacentHTML('afterend', `<p class="tm-msg">${escEssay(e.message)}</p>`);
        }
      });
    });

    const allReviewed = anchorMode !== 'TEXT_OFFSET' || (pages.length > 0 && pages.every((p) => p.reviewed_text));
    confirmBtn.disabled = !(pages.length > 0 && allReviewed);
    confirmBtn.onclick = async () => {
      const confirmMsg = target.querySelector('#essay-confirm-msg');
      confirmBtn.disabled = true;
      try {
        await essayRequest(`/api/v1/student/essay-submissions/${submissionId}/confirm`, { method: 'POST' });
        await loadPrompts();
      } catch (e) {
        confirmMsg.hidden = false;
        confirmMsg.textContent = e.message;
        confirmBtn.disabled = false;
      }
    };
  }
```

Substituir também `renderContinueUpload` (escrita como placeholder na Task 5) por:

```javascript
  function renderContinueUpload(prompt) {
    container.innerHTML = `
      <div class="card essay-form">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <h3>${escEssay(prompt.title)}</h3>
      </div>`;
    container.querySelector('[data-back]').addEventListener('click', () => renderList());
    renderUploadArea(container, prompt.my_submission.id, prompt.my_submission.anchor_mode, 'PHOTO');
  }
```

(`renderNewSubmissionForm` chama `renderModeBody('TYPED', prompt)` no fim, como já escrito na Task 5 — sem mudança ali.)

- [ ] **Step 6: CSS da página**

Em `src/agente_ia_edu/web/styles.css`, adicionar ao bloco "Módulo Redação" já criado na Task 5:

```css
.essay-page-card { display: flex; flex-direction: column; gap: 8px; }
.essay-page-card img { max-width: 100%; border-radius: 6px; border: 1px solid var(--border, #e2e6ee); }
.essay-review-status { font-size: 13px; color: var(--text-muted); }
```

- [ ] **Step 7: Verificar manualmente no browser**

Use `preview_start`, abra o portal do aluno, envie uma redação em modo "Fotografar" com 1-2 imagens de teste, confirme que:
- Cada página enviada aparece com a imagem visível (não quebrada).
- Se a escola tem transcrição habilitada, o textarea de revisão aparece pré-preenchido e o botão "Confirmar envio" só habilita depois de salvar a revisão de toda página.
- Confirmar envio muda a lista de volta para "Em correção / ver devolutiva".

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_submissions.py \
        src/agente_ia_edu/web/essay.js \
        src/agente_ia_edu/web/styles.css \
        tests/test_frontend_r_student_essay_page_image_route.py
git commit -m "feat(frontend-redacao): add page image route and photo/PDF upload flow"
```

---

### Task 7: Portal do aluno — tela de devolutiva

**Files:**
- Modify: `src/agente_ia_edu/web/essay.js`
- Modify: `src/agente_ia_edu/web/styles.css`

**Interfaces:**
- Consumes: `GET /api/v1/student/essay-submissions/{id}/correction` (Task 2).
- Produces: `renderApprovedDevolutiva` (substitui o placeholder `renderDevolutiva` da Task 5). Fecha o fluxo do aluno — nenhuma task de frontend do aluno depende desta.

- [ ] **Step 1: Substituir `renderDevolutiva` em `essay.js`**

Em `src/agente_ia_edu/web/essay.js`, substituir a função `renderDevolutiva` (placeholder da Task 5) por:

```javascript
  const COMPETENCY_LABELS = {
    C1: 'Domínio da norma padrão', C2: 'Compreensão do tema', C3: 'Argumentação',
    C4: 'Coesão textual', C5: 'Proposta de intervenção',
  };

  async function renderDevolutiva(prompt) {
    container.innerHTML = '<p class="empty-text">Carregando devolutiva...</p>';
    const submissionId = prompt.my_submission.id;
    try {
      const correction = await essayRequest(`/api/v1/student/essay-submissions/${submissionId}/correction`);
      if (correction.status === 'PENDING') {
        container.innerHTML = `
          <div class="card">
            <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
            <h3>${escEssay(prompt.title)}</h3>
            <p class="empty-text">Sua redação ainda está sendo corrigida. Volte mais tarde para ver a devolutiva.</p>
          </div>`;
        container.querySelector('[data-back]').addEventListener('click', () => loadPrompts());
        return;
      }
      renderApprovedDevolutiva(prompt, correction);
    } catch (e) {
      container.innerHTML = `<div class="card"><p class="empty-text">${escEssay(e.message)}</p></div>`;
    }
  }

  function renderApprovedDevolutiva(prompt, correction) {
    const scores = correction.final_scores || {};
    const perCompetency = scores.per_competency || {};
    const feedback = correction.final_feedback || {};
    const annotations = correction.annotations || [];
    const alerts = correction.alerts || [];
    const intervention = correction.intervention || {};

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
      ? annotations.map((a) => {
          const quote = (a.anchor && (a.anchor.quote || a.anchor.read_text)) || '';
          return `
            <div class="essay-annotation">
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
        <h4>Anotações</h4>
        ${annotationsHtml}
        <h4>Competência 5 — Proposta de intervenção</h4>
        ${interventionHtml}
      </div>`;

    container.querySelector('[data-back]').addEventListener('click', () => loadPrompts());
  }
```

- [ ] **Step 2: CSS da devolutiva**

Em `src/agente_ia_edu/web/styles.css`, adicionar ao bloco "Módulo Redação":

```css
.essay-total-score { font-size: 22px; font-weight: 800; margin: 12px 0; }
.essay-competency-row { display: grid; grid-template-columns: 220px 1fr 60px; align-items: center; gap: 10px; margin: 8px 0; }
.essay-competency-bar { background: #eef2f7; border-radius: 6px; height: 10px; overflow: hidden; }
.essay-competency-fill { background: var(--primary); height: 100%; }
.essay-alerts { margin: 10px 0; }
.essay-annotation { border-left: 3px solid var(--primary); padding: 8px 12px; margin: 10px 0; background: #fbfdff; }
.essay-annotation blockquote { margin: 6px 0 0; font-style: italic; color: var(--text-muted); }
.essay-intervention-checklist { list-style: none; padding: 0; }
.essay-intervention-checklist li { padding: 4px 0; }
.essay-warning { color: var(--danger, #d64545); font-weight: 700; }
```

- [ ] **Step 3: Verificar manualmente no browser**

Use `preview_start`, abra o portal do aluno. Como não há uma correção `APPROVED` real sem rodar a IA de verdade, verifique os dois casos possíveis manualmente:
- Uma submissão `SUBMITTED` sem correção `APPROVED` ainda mostra "ainda está sendo corrigida" (via `read_network_requests`, confirme que `GET .../correction` retornou `status: "PENDING"`).
- Insira manualmente (via `docker exec` no banco de desenvolvimento, ou um script Python pontual, nunca escrito em produção) uma `EssayCorrection` com `status="APPROVED"` e conteúdo de exemplo para uma submissão de teste, recarregue a tela e confirme que a nota, as barras de competência, os pontos fortes/a melhorar, as anotações e o bloco da competência 5 aparecem corretamente.

- [ ] **Step 4: Commit**

```bash
git add src/agente_ia_edu/web/essay.js src/agente_ia_edu/web/styles.css
git commit -m "feat(frontend-redacao): add student devolutiva view"
```

---

### Task 8: Portal do professor — esqueleto de `essay-review.js` + gerenciar propostas

**Files:**
- Create: `src/agente_ia_edu/web/essay-review.js`
- Modify: `src/agente_ia_edu/web/teacher.html`
- Modify: `src/agente_ia_edu/web/teacher.js`

**Interfaces:**
- Consumes: `GET /api/v1/catalog/essay-prompts` e `GET .../{id}` (Task 3), `POST /api/v1/catalog/essay-prompts`/`.../materials`/`.../assignments` (R2, inalterados), `GET /api/v1/teacher/classrooms` (fase anterior, inalterado).
- Produces: `window.EssayReviewView.init()`, chamado por `teacher.js`. Task 9/10 estendem o mesmo arquivo.

**Limitação conhecida, aceita para esta leva**: `GET /api/v1/teacher/classrooms` resolve turmas no escopo do professor identificado por `identity.external_user_id` (`teacher_portal.py::list_teacher_classrooms`), não uma lista de "todas as turmas da escola". Para um `TEACHER` isso é exatamente o esperado; para `COORDINATOR`/`DIRECTOR` pode retornar uma lista vazia ou incompleta se eles não estiverem vinculados às turmas como professor. Aceitável para esta leva — uma rota "todas as turmas da escola" fica para depois, fora de escopo.

- [ ] **Step 1: Adicionar o nav item e o painel**

Em `src/agente_ia_edu/web/teacher.html`, adicionar à barra lateral, depois de `data-view="reports"` e antes de `data-view="profile"`:

```html
        <button class="nav-item" data-view="essay-review">
          <span class="icon">📝</span> Redação
        </button>
```

Adicionar o painel, imediatamente antes de `<section id="view-profile" class="view-panel">`:

```html
        <section id="view-essay-review" class="view-panel">
          <div id="essay-review-root" aria-live="polite"></div>
        </section>
```

Adicionar o script novo antes de `teacher.js`:

```html
  <script src="essay-review.js"></script>
  <script src="teacher.js"></script>
```

- [ ] **Step 2: Ligar o dispatch em `teacher.js`**

Em `src/agente_ia_edu/web/teacher.js`, adicionar ao `titleMap` dentro de `switchView`:

```js
      'essay-review': { title: 'Redação', sub: 'Propostas de redação e correções pendentes de revisão' },
```

E ao `loadCurrentView`:

```js
    if (state.currentView === 'essay-review') window.EssayReviewView.init(state.schoolId, state.teacherId);
```

- [ ] **Step 3: Escrever `essay-review.js` — esqueleto e gerenciar propostas**

```javascript
/* AGENTE IA EDU — Portal do Professor — módulo de Redação */
(function essayReviewModule(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.EssayReviewView = api;
})(typeof window !== 'undefined' ? window : null, function createEssayReviewView() {
  let container = null;
  let schoolId = '';
  let teacherId = '';
  let prompts = [];

  function tmEsc(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[character]);
  }

  function translateDetail(detail) {
    if (typeof detail === 'string') {
      const moduleMatch = detail.match(/^Module '(.+)' is not enabled for the current school\.$/);
      if (moduleMatch) return `O módulo '${moduleMatch[1]}' não está habilitado para esta escola.`;
      return detail;
    }
    if (detail && detail.message) return detail.message;
    return 'Não foi possível completar a ação.';
  }

  function reviewHeaders(extra = {}) {
    // teacherId comes from teacher.js's own in-memory state (never
    // sessionStorage - unlike the student portal's access token, this
    // repo's teacher/coordinator identity is only ever an in-memory
    // filter-input value). init() receives the current value each time
    // teacher.js calls it, including every time that filter input changes
    // (loadCurrentView() re-fires on every state.teacherId change), so this
    // module never goes stale relative to what teacher.js itself shows.
    return { 'Authorization': `Bearer ${teacherId || 'user:prof_mendes'}`, ...extra };
  }

  async function reviewRequest(path, options = {}) {
    const res = await fetch(path, { ...options, headers: reviewHeaders(options.headers || {}) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const error = new Error(translateDetail(data.detail));
      error.status = res.status;
      throw error;
    }
    return data;
  }

  function renderTabs(activeTab) {
    return `
      <div class="essay-review-tabs">
        <button class="btn ${activeTab === 'prompts' ? 'btn-primary' : 'btn-secondary'}" type="button" data-tab="prompts">Propostas</button>
        <button class="btn ${activeTab === 'queue' ? 'btn-primary' : 'btn-secondary'}" type="button" data-tab="queue">Fila de Revisão</button>
      </div>`;
  }

  function wireTabs() {
    container.querySelectorAll('[data-tab]').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (btn.dataset.tab === 'prompts') renderPromptsList();
        if (btn.dataset.tab === 'queue') renderReviewQueue();
      });
    });
  }

  async function renderPromptsList() {
    container.innerHTML = `${renderTabs('prompts')}<p class="empty-text">Carregando propostas...</p>`;
    wireTabs();
    try {
      prompts = await reviewRequest('/api/v1/catalog/essay-prompts');
    } catch (e) {
      container.innerHTML = `${renderTabs('prompts')}<p class="empty-text">${tmEsc(e.message)}</p>`;
      wireTabs();
      return;
    }
    const list = container.querySelector('.essay-review-tabs').insertAdjacentHTML('afterend', `
      <div class="tm-form-actions" style="margin: 12px 0;">
        <button class="btn btn-primary" type="button" id="er-new-prompt-btn">Nova proposta</button>
      </div>
      <div class="tm-table-wrap" style="overflow-x:auto;">
        <table class="tm-table">
          <thead><tr><th>Título</th><th>Ano</th><th>Status</th><th></th></tr></thead>
          <tbody id="er-prompts-body">
            ${prompts.map((p) => `
              <tr>
                <td>${tmEsc(p.title)}</td><td>${p.year}</td><td>${tmEsc(p.status)}</td>
                <td><button class="btn btn-secondary" type="button" data-open-prompt="${tmEsc(p.id)}">Abrir</button></td>
              </tr>`).join('') || '<tr><td colspan="4" class="empty-text">Nenhuma proposta criada ainda.</td></tr>'}
          </tbody>
        </table>
      </div>`);

    container.querySelector('#er-new-prompt-btn').addEventListener('click', renderNewPromptForm);
    container.querySelectorAll('[data-open-prompt]').forEach((btn) => {
      btn.addEventListener('click', () => renderPromptDetail(btn.dataset.openPrompt));
    });
  }

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
          <button class="btn btn-primary" type="submit">Criar proposta</button>
          <p id="er-new-prompt-msg" class="tm-msg" hidden></p>
        </form>
      </div>`;
    wireTabs();
    container.querySelector('[data-back]').addEventListener('click', renderPromptsList);
    container.querySelector('#er-new-prompt-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const msg = container.querySelector('#er-new-prompt-msg');
      const submitBtn = ev.target.querySelector('button[type="submit"]');
      submitBtn.disabled = true;
      try {
        const prompt = await reviewRequest('/api/v1/catalog/essay-prompts', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: container.querySelector('#er-title').value.trim(),
            statement: container.querySelector('#er-statement').value.trim(),
            year: Number(container.querySelector('#er-year').value),
          }),
        });
        renderPromptDetail(prompt.id);
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
        submitBtn.disabled = false;
      }
    });
  }

  async function renderPromptDetail(promptId) {
    container.innerHTML = `${renderTabs('prompts')}<p class="empty-text">Carregando...</p>`;
    wireTabs();
    let detail;
    try {
      detail = await reviewRequest(`/api/v1/catalog/essay-prompts/${promptId}`);
    } catch (e) {
      container.innerHTML = `${renderTabs('prompts')}<p class="empty-text">${tmEsc(e.message)}</p>`;
      wireTabs();
      return;
    }

    let classrooms = [];
    try {
      classrooms = await reviewRequest(
        `/api/v1/teacher/classrooms?school_id=${encodeURIComponent(schoolId)}&academic_year=2026`,
      );
    } catch (e) {
      classrooms = [];
    }

    const detailHtml = document.createElement('div');
    detailHtml.innerHTML = `
      <div class="card tm-detail-grid">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <h3>${tmEsc(detail.title)}</h3>
        <p>${tmEsc(detail.statement)}</p>
        <p class="empty-text">Status: ${tmEsc(detail.status)}</p>

        <h4>Materiais de apoio</h4>
        <ul>${detail.materials.map((m) => `<li>${tmEsc(m.content || m.material_type)}</li>`).join('') || '<li class="empty-text">Nenhum material.</li>'}</ul>
        <form id="er-material-form" class="tm-form-row">
          <div class="form-group"><label for="er-material-content">Adicionar material de texto</label><textarea id="er-material-content" class="textarea-input" rows="3"></textarea></div>
          <button class="btn btn-secondary" type="submit">Adicionar</button>
        </form>
        <p id="er-material-msg" class="tm-msg" hidden></p>

        <h4>Turmas atribuídas</h4>
        <ul>${detail.assignments.map((a) => `<li>${tmEsc(a.class_id)} — ${tmEsc(a.status)}</li>`).join('') || '<li class="empty-text">Nenhuma turma atribuída ainda.</li>'}</ul>
        <form id="er-assign-form" class="tm-form-row">
          <div class="form-group">
            <label for="er-assign-class">Turma</label>
            <select id="er-assign-class" class="text-input">
              ${classrooms.map((c) => `<option value="${tmEsc(c.classroom_id)}">${tmEsc(c.name)}</option>`).join('') || '<option value="">Nenhuma turma disponível</option>'}
            </select>
          </div>
          <div class="form-group"><label for="er-assign-due"><input id="er-assign-validation" type="checkbox" checked> Exigir revisão docente</label></div>
          <button class="btn btn-primary" type="submit">Atribuir</button>
        </form>
        <p id="er-assign-msg" class="tm-msg" hidden></p>
      </div>`;
    container.querySelector('.essay-review-tabs').insertAdjacentElement('afterend', detailHtml.firstElementChild);

    container.querySelector('[data-back]').addEventListener('click', renderPromptsList);
    container.querySelector('#er-material-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const content = container.querySelector('#er-material-content').value.trim();
      const msg = container.querySelector('#er-material-msg');
      if (!content) return;
      try {
        await reviewRequest(`/api/v1/catalog/essay-prompts/${promptId}/materials`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ material_type: 'TEXT', content, position: detail.materials.length }),
        });
        renderPromptDetail(promptId);
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
      }
    });
    container.querySelector('#er-assign-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const classId = container.querySelector('#er-assign-class').value;
      const validationEnabled = container.querySelector('#er-assign-validation').checked;
      const msg = container.querySelector('#er-assign-msg');
      if (!classId) return;
      try {
        await reviewRequest(`/api/v1/catalog/essay-prompts/${promptId}/assignments`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ class_id: classId, validation_enabled: validationEnabled }),
        });
        renderPromptDetail(promptId);
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
      }
    });
  }

  function renderReviewQueue() {
    // Implementado na Task 9.
    container.innerHTML = `${renderTabs('queue')}<p class="empty-text">Fila de revisão — próxima etapa.</p>`;
    wireTabs();
  }

  function init(currentSchoolId, currentTeacherId) {
    container = document.getElementById('essay-review-root');
    schoolId = currentSchoolId || '';
    teacherId = currentTeacherId || '';
    if (!container) return;
    renderPromptsList();
  }

  return { init };
});
```

- [ ] **Step 4: CSS mínimo**

Em `src/agente_ia_edu/web/teacher.css`, adicionar ao final:

```css
.essay-review-tabs { display: flex; gap: 8px; margin-bottom: 16px; }
```

- [ ] **Step 5: Verificar manualmente no browser**

Use `preview_start`, abra o portal do professor, clique em "Redação" na barra lateral, confirme:
- A aba "Propostas" carrega e lista propostas existentes (ou mostra "Nenhuma proposta criada ainda").
- "Nova proposta" cria uma proposta e abre o detalhe dela.
- Adicionar material de texto e atribuir a uma turma funcionam (confirme via `read_network_requests`).

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/web/essay-review.js \
        src/agente_ia_edu/web/teacher.html \
        src/agente_ia_edu/web/teacher.js \
        src/agente_ia_edu/web/teacher.css
git commit -m "feat(frontend-redacao): add teacher essay proposal management screen"
```

---

### Task 9: Portal do professor — fila de revisão

**Files:**
- Modify: `src/agente_ia_edu/web/essay-review.js`

**Interfaces:**
- Consumes: `GET /api/v1/teacher/essay-corrections?status=` (Task 4, já enriquecida).
- Produces: `renderReviewQueue` (substitui o placeholder da Task 8), `currentCorrections` (estado de módulo mantido em memória — a lista já traz `ai_output`/`final_scores`/`final_feedback`/`failure_reason` completos, então a Task 10 não precisa buscar nada de novo para abrir o painel de revisão, só localizar o item já carregado pelo `id`).

Não existe uma rota `GET /api/v1/teacher/essay-corrections/{id}` — só a listagem e as ações. A lista já retorna tudo que o painel de revisão precisa, então este módulo guarda o resultado da última busca em memória em vez de inventar uma rota de detalhe que não existe e não faz falta.

- [ ] **Step 1: Substituir `renderReviewQueue`**

Em `src/agente_ia_edu/web/essay-review.js`, adicionar `let currentCorrections = [];` junto às outras variáveis de módulo (ao lado de `let prompts = [];`), e substituir a função `renderReviewQueue` (placeholder da Task 8) por:

```javascript
  async function renderReviewQueue(status) {
    const currentStatus = status || 'PENDING_REVIEW';
    container.innerHTML = `
      ${renderTabs('queue')}
      <div class="tm-form-row" style="margin: 12px 0;">
        <div class="form-group"><label for="er-queue-status">Status</label>
          <select id="er-queue-status" class="text-input">
            <option value="PENDING_REVIEW">Pendente de revisão</option>
            <option value="NEEDS_REVIEW">Precisa de atenção (falha)</option>
            <option value="APPROVED">Aprovadas</option>
            <option value="REJECTED">Rejeitadas</option>
          </select>
        </div>
      </div>
      <div id="er-queue-body"><p class="empty-text">Carregando...</p></div>`;
    wireTabs();
    const statusSelect = container.querySelector('#er-queue-status');
    statusSelect.value = currentStatus;
    statusSelect.addEventListener('change', (ev) => renderReviewQueue(ev.target.value));

    const body = container.querySelector('#er-queue-body');
    try {
      currentCorrections = await reviewRequest(
        `/api/v1/teacher/essay-corrections?status=${encodeURIComponent(currentStatus)}`,
      );
    } catch (e) {
      body.innerHTML = `<p class="empty-text">${tmEsc(e.message)}</p>`;
      return;
    }

    body.innerHTML = `
      <div class="tm-table-wrap" style="overflow-x:auto;">
        <table class="tm-table">
          <thead><tr><th>Aluno</th><th>Proposta</th><th>Enviada em</th><th></th></tr></thead>
          <tbody>
            ${currentCorrections.map((c) => `
              <tr>
                <td>${tmEsc(c.student_name || '—')}</td>
                <td>${tmEsc(c.prompt_title || '—')}</td>
                <td>${c.submitted_at ? new Date(c.submitted_at).toLocaleString('pt-BR') : '—'}</td>
                <td><button class="btn btn-secondary" type="button" data-open-correction="${tmEsc(c.id)}">Revisar</button></td>
              </tr>`).join('') || '<tr><td colspan="4" class="empty-text">Nenhuma correção com este status.</td></tr>'}
          </tbody>
        </table>
      </div>`;

    body.querySelectorAll('[data-open-correction]').forEach((btn) => {
      btn.addEventListener('click', () => renderReviewPanel(btn.dataset.openCorrection, currentStatus));
    });
  }
```

- [ ] **Step 2: Verificar manualmente no browser**

Use `preview_start`, abra o portal do professor, clique em "Redação" → aba "Fila de Revisão". Confirme que a tabela carrega (mesmo vazia) e que trocar o filtro de status refaz a busca (confirme via `read_network_requests` que o `status` na query string muda).

- [ ] **Step 3: Commit**

```bash
git add src/agente_ia_edu/web/essay-review.js
git commit -m "feat(frontend-redacao): add teacher essay correction review queue"
```

---

### Task 10: Portal do professor — painel de revisão (aprovar/rejeitar/tentar novamente)

**Files:**
- Modify: `src/agente_ia_edu/web/essay-review.js`
- Modify: `src/agente_ia_edu/web/teacher.css`

**Interfaces:**
- Consumes: `POST /api/v1/teacher/essay-corrections/{id}/approve`/`.../reject`/`.../retry` (R3, inalterados), `currentCorrections` (Task 9).
- Produces: `renderReviewPanel` (substitui a chamada direta que a Task 9 já religa via `data-open-correction`). Fecha o fluxo do professor — nenhuma task de frontend depende desta.

**Simplificação deliberada**: o painel sempre reenvia `final_scores`/`final_feedback` completos ao aprovar, mesmo que o professor não tenha alterado nada (em vez de detectar se algo mudou e omitir o corpo quando não houve edição). Funcionalmente idêntico a "aprovar sem edição" — o único efeito colateral é que o registro de auditoria sempre grava um `before`/`after` (iguais, quando nada mudou) em vez de omitir o `metadata_`. Aceitável para esta leva; detectar edição real exigiria guardar os valores originais separadamente dos valores do formulário, complexidade não justificada agora.

- [ ] **Step 1: Substituir a abertura do painel**

Em `src/agente_ia_edu/web/essay-review.js`, adicionar a função `renderReviewPanel` (chamada pelo `data-open-correction` já religado na Task 9):

```javascript
  function renderReviewPanel(correctionId, returnStatus) {
    const correction = currentCorrections.find((c) => c.id === correctionId);
    if (!correction) {
      renderReviewQueue(returnStatus);
      return;
    }
    const aiOutput = correction.ai_output || {};
    const scores = correction.final_scores || {};
    const perCompetency = scores.per_competency || {};
    const feedback = correction.final_feedback || {};
    const annotations = aiOutput.annotations || [];
    const alerts = aiOutput.alerts || [];

    const failureHtml = correction.status === 'NEEDS_REVIEW'
      ? `<div class="alert-banner alert-danger">Falha na correção automática: ${tmEsc(correction.failure_reason || 'motivo não informado')}</div>`
      : '';

    const alertsHtml = alerts.length
      ? `<div>${alerts.map((a) => `<span class="badge badge-accent">${tmEsc(a.code)}</span>`).join(' ')}</div>`
      : '';

    const scoresFeedbackHtml = correction.status === 'PENDING_REVIEW' ? `
      <h4>Notas por competência</h4>
      <div class="tm-form-row">
        ${['C1', 'C2', 'C3', 'C4', 'C5'].map((code) => `
          <div class="form-group">
            <label for="er-score-${code}">${code}</label>
            <input id="er-score-${code}" class="text-input" type="number" min="0" max="200" step="40"
                   value="${(perCompetency[code] || {}).points ?? 0}">
          </div>`).join('')}
      </div>
      <h4>Feedback</h4>
      <div class="form-group">
        <label for="er-feedback-strategy">Próxima redação</label>
        <textarea id="er-feedback-strategy" class="textarea-input" rows="3">${tmEsc(feedback.next_essay_strategy || '')}</textarea>
      </div>
      <h4>Anotações da IA</h4>
      ${annotations.length ? annotations.map((a) => `
        <div class="essay-annotation">
          <strong>${tmEsc(a.letter)} — ${tmEsc(a.competency_code)}</strong>
          <p>${tmEsc(a.short_comment)}</p>
        </div>`).join('') : '<p class="empty-text">Nenhuma anotação.</p>'}
    ` : '';

    const actionsHtml = correction.status === 'PENDING_REVIEW' ? `
        <button class="btn btn-primary" type="button" id="er-approve-btn">Aprovar</button>
        <button class="btn btn-secondary" type="button" id="er-reject-btn">Rejeitar</button>`
      : correction.status === 'NEEDS_REVIEW' ? `
        <button class="btn btn-primary" type="button" id="er-retry-btn">Tentar novamente</button>`
      : '';

    container.innerHTML = `
      ${renderTabs('queue')}
      <div class="card tm-detail-grid">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar à fila</button>
        ${failureHtml}
        ${alertsHtml}
        ${scoresFeedbackHtml}
        <div class="tm-form-actions">${actionsHtml}</div>
        <p id="er-review-msg" class="tm-msg" hidden></p>
      </div>`;
    wireTabs();
    container.querySelector('[data-back]').addEventListener('click', () => renderReviewQueue(returnStatus));

    const msg = container.querySelector('#er-review-msg');

    const approveBtn = container.querySelector('#er-approve-btn');
    if (approveBtn) {
      approveBtn.addEventListener('click', async () => {
        approveBtn.disabled = true;
        const editedPerCompetency = Object.fromEntries(['C1', 'C2', 'C3', 'C4', 'C5'].map((code) => [
          code,
          {
            points: Number(container.querySelector(`#er-score-${code}`).value),
            confidence: (perCompetency[code] || {}).confidence ?? 1.0,
          },
        ]));
        const editedScores = {
          per_competency: editedPerCompetency,
          total: Object.values(editedPerCompetency).reduce((sum, s) => sum + s.points, 0),
        };
        const editedFeedback = { ...feedback, next_essay_strategy: container.querySelector('#er-feedback-strategy').value.trim() };
        try {
          await reviewRequest(`/api/v1/teacher/essay-corrections/${correctionId}/approve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ final_scores: editedScores, final_feedback: editedFeedback }),
          });
          renderReviewQueue(returnStatus);
        } catch (e) {
          msg.hidden = false;
          msg.textContent = e.message;
          approveBtn.disabled = false;
        }
      });
    }

    const rejectBtn = container.querySelector('#er-reject-btn');
    if (rejectBtn) {
      rejectBtn.addEventListener('click', async () => {
        rejectBtn.disabled = true;
        try {
          await reviewRequest(`/api/v1/teacher/essay-corrections/${correctionId}/reject`, { method: 'POST' });
          renderReviewQueue(returnStatus);
        } catch (e) {
          msg.hidden = false;
          msg.textContent = e.message;
          rejectBtn.disabled = false;
        }
      });
    }

    const retryBtn = container.querySelector('#er-retry-btn');
    if (retryBtn) {
      retryBtn.addEventListener('click', async () => {
        retryBtn.disabled = true;
        try {
          await reviewRequest(`/api/v1/teacher/essay-corrections/${correctionId}/retry`, { method: 'POST' });
          renderReviewQueue(returnStatus);
        } catch (e) {
          msg.hidden = false;
          msg.textContent = e.message;
          retryBtn.disabled = false;
        }
      });
    }
  }
```

- [ ] **Step 2: CSS da anotação (reaproveitar a classe já criada na Task 7)**

`essay-review.js` reaproveita a classe `.essay-annotation` já criada em `styles.css` na Task 7 — mas `teacher.css` é o arquivo carregado no portal do professor, não `styles.css`. Adicionar a mesma regra em `src/agente_ia_edu/web/teacher.css`:

```css
.essay-annotation { border-left: 3px solid var(--primary); padding: 8px 12px; margin: 10px 0; background: #fbfdff; }
```

- [ ] **Step 3: Verificar manualmente no browser**

Use `preview_start`, abra o portal do professor. Para testar de ponta a ponta sem depender de uma correção real da IA (que exige `OPENAI_API_KEY` configurada), insira manualmente uma `EssayCorrection` de teste com `status="PENDING_REVIEW"` no banco de desenvolvimento (script Python pontual, nunca commitado) e confirme:
- A correção aparece na fila com nome do aluno e título da proposta.
- Abrir "Revisar" mostra os campos de nota editáveis e o feedback.
- Aprovar (com ou sem editar os campos) remove a correção da fila `PENDING_REVIEW` e ela passa a aparecer em `APPROVED`.
- Repita com uma correção `status="NEEDS_REVIEW"` e confirme que "Tentar novamente" aparece e que o `failure_reason` é mostrado.

- [ ] **Step 4: Commit**

```bash
git add src/agente_ia_edu/web/essay-review.js src/agente_ia_edu/web/teacher.css
git commit -m "feat(frontend-redacao): add teacher essay correction review panel"
```

---

### Task 11: Regressão completa do backend + verificação de ponta a ponta no browser

**Files:**
- Nenhum criado ou modificado — esta task só roda e verifica. Se encontrar uma regressão real, a correção pertence à task que a causou (reabrir os arquivos daquela task), não a esta.

**Interfaces:**
- Consumes: toda task 1-10.
- Produces: confirmação de que o backend está limpo e que os dois portais funcionam de ponta a ponta — a definição de pronto deste plano.

- [ ] **Step 1: Rodar a suíte completa de backend**

```bash
PYTHONPATH="src:." .venv/bin/python -m pytest tests/ -v
```

Esperado: tudo passa — a suíte pré-existente (R0-R3, inalterada) mais os testes que este plano acrescentou (Tasks 1, 2, 3, 4, 6). Se algo fora de `tests/test_frontend_r_*.py` falhar, é uma regressão real (Tasks 4 e 6 tocam código já mesclado — `essay_corrections.py`, `essay_submissions.py`) — investigue e corrija nos arquivos daquela task, depois rode de novo. Nunca enfraqueça ou apague uma asserção pré-existente para fazer passar.

- [ ] **Step 2: Verificar o gate de coluna de credencial**

```bash
PYTHONPATH="src:." .venv/bin/python -m pytest tests/test_r0_phase1_gate.py -v
```

Esperado: PASS, sem mudança. Os campos novos deste plano (`id`, `essay_id`, `status`, `anchor_mode`, `due_at`, `prompt_assignment_id`, `student_name`, `prompt_title`, `submitted_at`) não contêm nenhum dos fragmentos proibidos (`password`, `token`, `secret`, `credential`, `senha`) — não deveria precisar de exceção nova.

- [ ] **Step 3: Verificação de ponta a ponta no browser — portal do aluno**

Use `preview_start` para abrir o servidor de desenvolvimento. Se necessário, rode `scripts/seed_demo_data.py` (ou crie manualmente via a API do professor) para garantir que existe ao menos uma `PromptAssignment` aberta para uma turma com aluno matriculado.

1. Abra `index.html`, clique em "Redação". Confirme que a lista de propostas carrega.
2. Envie uma redação digitada. Confirme (via `read_network_requests`) que o `POST` retornou 201 e que a lista volta mostrando "Em correção / ver devolutiva".
3. Clique para ver a devolutiva. Se não houver `OPENAI_API_KEY` configurada (nenhuma correção real vai rodar), confirme que aparece "ainda está sendo corrigida" — isso já prova que a rota `GET .../correction` funciona e colapsa `NEEDS_REVIEW` corretamente.
4. Insira manualmente (script Python pontual local, nunca commitado) uma `EssayCorrection` com `status="APPROVED"` e conteúdo de exemplo (`final_scores`, `final_feedback`, `ai_output` com `annotations`/`intervention`/`alerts`) para a submissão criada no passo 2. Recarregue a devolutiva e confirme que nota, barras de competência, pontos fortes/a melhorar, anotações e bloco da competência 5 aparecem corretamente.
5. Repita o fluxo em modo "Fotografar" com 1-2 imagens de teste: confirme que cada página aparece com a imagem visível, que a revisão de texto funciona, e que "Confirmar envio" só habilita depois de revisar toda página.

- [ ] **Step 4: Verificação de ponta a ponta no browser — portal do professor**

1. Abra `teacher.html`, clique em "Redação" → aba "Propostas". Crie uma proposta nova, adicione um material de texto, atribua a uma turma.
2. Confirme que a proposta agora aparece com `status="ACTIVE"` na listagem.
3. Vá para a aba "Fila de Revisão". Se a escola estiver em `AVALIATIVO` com `validation_enabled=true` na atribuição, a correção `PENDING_REVIEW` inserida manualmente no Step 3 (ou uma nova, inserida com `status="PENDING_REVIEW"`) deve aparecer com nome do aluno e título da proposta.
4. Abra "Revisar", edite uma nota, aprove. Confirme que a correção some da fila `PENDING_REVIEW` e passa a aparecer em `APPROVED` (troque o filtro de status para confirmar).
5. Insira manualmente uma segunda correção com `status="NEEDS_REVIEW"` e `failure_reason` preenchido. Confirme que ela aparece na fila (filtro `NEEDS_REVIEW`), que o painel mostra o motivo da falha, e que "Tentar novamente" está disponível (não precisa necessariamente ter sucesso sem uma API key real configurada — confirme que o clique chega a fazer a chamada, via `read_network_requests`).

- [ ] **Step 5: Reportar**

Sem commit — esta task não produz diff. Se todos os passos passarem limpos, o frontend de redação está pronto; prossiga para o passo `finishing-a-development-branch` deste plano (mesmo passo final usado por R2 e R3).

---
