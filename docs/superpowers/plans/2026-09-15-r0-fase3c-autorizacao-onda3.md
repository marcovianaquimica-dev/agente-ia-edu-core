# R0 Fase 3C — Autorização (Onda 3): Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fechar o item que a onda 2 nomeou como diferente em espécie dos outros dois: uma rota que não tem autorização **nenhuma**, não uma checagem incompleta — `GET /api/v1/pedagogical/context/{classroom_id}` lê contexto pedagógico de qualquer escola, para qualquer identidade autenticada, ignorando a identidade por completo.

**Architecture:** A rota já recebe `identity` injetada e já importa `ScopeAuthorizationError`; só nunca usa nenhum dos dois. A correção liga `verify_teacher_classroom_scope` — a mesma função que as duas ondas anteriores já corrigiram duas vezes — no ponto exato onde `services/api/routes/teaching_context.py:170` já faz isso para a rota vizinha, no mesmo arquivo.

**Tech Stack:** Python 3.13, FastAPI, `unittest.IsolatedAsyncioTestCase`, chamada direta da função de rota (sem `TestClient`) — ver a seção abaixo sobre por quê.

**Spec:** `docs/superpowers/specs/2026-09-13-r0-estrutura-academica-configuracao-design.md` — §3.2, §7 (passo 5).

## Global Constraints

- Python `>=3.13,<3.14`. Nenhuma dependência nova.
- **Esta fase não cria migration, não toca `external_id_resolution.py`, não remove o andaime `TURMA_3A`.**
- Nenhuma coluna de credencial em lugar nenhum.
- Testes com `unittest.IsolatedAsyncioTestCase`, `sqlite+aiosqlite:///:memory:` e `StaticPool`.
- **Nunca aponte `DATABASE_URL` para `agente_ia_edu`** — banco de desenvolvimento, compartilhado. **A suíte completa derruba o banco que `DATABASE_URL` nomeia.**
- Código e comentários em inglês; mensagem de commit em português, como o `git log`.
- Gate desta fase: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py tests/test_coordination_portal.py tests/test_teacher_portal.py tests/test_teaching_context.py tests/test_end_to_end_learning_flow.py -q`.

## O bug, confirmado por execução antes de escrever este plano

`src/agente_ia_edu/api/routes/teaching_context.py::get_classroom_pedagogical_context`:

```python
async def get_classroom_pedagogical_context(
    classroom_id: str,
    school_id: UUID = Query(...),
    academic_year: str = Query("2026"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[PedagogicalContextResponse]:
    async with session_factory() as session:
        service = TeachingContextService(session)
        contexts = await service.get_active_recent_contexts(
            school_id=school_id,
            classroom_id=classroom_id,
            academic_year=academic_year,
        )
        return [_to_context_response(c) for c in contexts]
```

`identity` é injetada e **nunca lida**. `school_id` vem direto de um parâmetro de consulta, sem checagem nenhuma contra os vínculos de quem está pedindo. `get_active_recent_contexts`, o método de serviço chamado logo abaixo, também não aplica autorização — filtra só por `school_id`, `classroom_id` e `academic_year`. A rota é o único lugar onde a checagem poderia acontecer, e ela não acontece.

Rodei o cenário antes de escrever este plano, chamando a função da rota diretamente (não pela HTTP, mas com os mesmos argumentos que o FastAPI injetaria):

```
"stranger": vinculo TEACHER so na escola A
get_classroom_pedagogical_context(classroom_id="QUALQUER-TURMA-DE-B", school_id=<escola B>, identity=stranger)
ANTES desta correcao: devolve a lista de contextos de B — sem excecao, sem checagem
DEPOIS desta correcao: HTTPException 403
```

**Por que a correção liga `verify_teacher_classroom_scope`, e não inventa checagem nova.** A rota vizinha no mesmo arquivo, `get_teacher_lesson` (linha 158), já resolve exatamente esse problema para outro recurso — busca um registro, extrai `school_id`/`classroom_id` dele, e chama:

```python
try:
    await service.verify_teacher_classroom_scope(
        teacher_id=identity.external_user_id,
        school_id=lesson.school_id,
        classroom_id=lesson.classroom_id,
    )
except ScopeAuthorizationError as exc:
    raise HTTPException(status_code=403, detail=str(exc))
```

`verify_teacher_classroom_scope` é a mesma função que as ondas 2 e 3 anteriores já corrigiram duas vezes nesta fase — trata `PLATFORM_ADMIN` como global de verdade, `DIRECTOR`/`COORDINATOR` checando a própria escola, `TEACHER` checando a própria turma. Não há checagem nova para desenhar; há um ponto de chamada a mais para o que já existe e já está testado.

**Por que verificar chamando a função direto, sem `TestClient`.** O repositório tem convenção de `TestClient(app)` em vários arquivos, mas nenhum monta o app inteiro para este roteador específico nem faz *override* de `get_current_identity`. Construir esse aparato só para uma rota, quando a função async pode ser chamada com os mesmos argumentos que o FastAPI injetaria, seria complexidade sem necessidade — confirmei isso rodando a própria correção assim, com sucesso, antes de escrever este plano.

---

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/agente_ia_edu/api/routes/teaching_context.py` (**modificar**) | `get_classroom_pedagogical_context` passa a checar `verify_teacher_classroom_scope` antes de consultar os contextos. |
| `tests/test_r0_pedagogical_context_route_authorization.py` (**criar**) | Prova a leitura sem autorização, o fechamento dela, e que o caminho legítimo continua funcionando. |

---

### Task 1: `get_classroom_pedagogical_context` checa a identidade antes de devolver dado

**Files:**
- Modify: `src/agente_ia_edu/api/routes/teaching_context.py:226-243`
- Test: `tests/test_r0_pedagogical_context_route_authorization.py` (criar)

**Interfaces:**
- Consumes: `TeachingContextService.verify_teacher_classroom_scope` (já existe, corrigida nas ondas 2 e 3 anteriores).
- Produces: nada novo — a rota mantém assinatura e formato de resposta.

- [ ] **Step 1: Escreva o teste que falha**

Crie `tests/test_r0_pedagogical_context_route_authorization.py`:

```python
# tests/test_r0_pedagogical_context_route_authorization.py
"""GET /api/v1/pedagogical/context/{classroom_id} injected identity and never
read it. Any authenticated caller read any school's pedagogical context by
varying the school_id query parameter. The neighbouring route in this same
file, get_teacher_lesson, already solves this for a different resource by
calling verify_teacher_classroom_scope - this closes the same gap here.

Called directly as a plain async function, with the same arguments FastAPI's
Depends would inject, rather than through TestClient: no existing fixture
wires this router with an identity override, and building one just for this
route would be unneeded machinery around a function that already takes
identity as a parameter.
"""

import unittest
import uuid

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.routes.teaching_context import get_classroom_pedagogical_context
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService


class PedagogicalContextRouteAuthorizationTests(unittest.IsolatedAsyncioTestCase):
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

    async def _two_schools(self, session):
        school_a = School(id=uuid.uuid4(), code="SCH-A", name="school-a")
        school_b = School(id=uuid.uuid4(), code="SCH-B", name="school-b")
        session.add_all([school_a, school_b])
        await session.commit()
        return school_a, school_b

    async def test_a_stranger_to_the_school_is_denied(self):
        """The reported shape exactly: a real link at School A, a request for
        School B's classroom, no relationship between the two."""
        async with self.session_factory() as session:
            school_a, school_b = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="stranger",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school_a.id,
                scope_external_id="TURMA-A1",
            )

        identity = ExternalIdentityContext(provider="test", external_user_id="stranger")
        with self.assertRaises(HTTPException) as ctx:
            await get_classroom_pedagogical_context(
                classroom_id="QUALQUER-TURMA-DE-B",
                school_id=school_b.id,
                academic_year="2026",
                identity=identity,
                session_factory=self.session_factory,
            )
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_an_unlinked_identity_is_denied_too(self):
        """Not just cross-school - no link at all must also be denied, not
        silently pass because there was nothing to compare against."""
        identity = ExternalIdentityContext(provider="test", external_user_id="nobody-at-all")
        async with self.session_factory() as session:
            _, school_b = await self._two_schools(session)

        with self.assertRaises(HTTPException) as ctx:
            await get_classroom_pedagogical_context(
                classroom_id="QUALQUER-TURMA",
                school_id=school_b.id,
                academic_year="2026",
                identity=identity,
                session_factory=self.session_factory,
            )
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_a_teacher_of_the_classroom_is_allowed(self):
        """The fix must not deny the caller it exists to protect - only
        strangers to the school."""
        async with self.session_factory() as session:
            school_a, _ = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="teacher-a",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school_a.id,
                scope_external_id="TURMA-A1",
            )
            school_a_id = school_a.id

        identity = ExternalIdentityContext(provider="test", external_user_id="teacher-a")
        result = await get_classroom_pedagogical_context(
            classroom_id="TURMA-A1",
            school_id=school_a_id,
            academic_year="2026",
            identity=identity,
            session_factory=self.session_factory,
        )
        self.assertEqual(result, [])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rode para ver falhar**

Run: `.venv/bin/python -m pytest tests/test_r0_pedagogical_context_route_authorization.py -q`
Expected: FAIL — os dois primeiros testes não levantam `HTTPException` (a rota devolve `[]` sem checar nada, então `assertRaises` falha). O terceiro já passa hoje.

- [ ] **Step 3: Implemente a correção**

Em `src/agente_ia_edu/api/routes/teaching_context.py`, substitua o corpo de `get_classroom_pedagogical_context`:

```python
async def get_classroom_pedagogical_context(
    classroom_id: str,
    school_id: UUID = Query(...),
    academic_year: str = Query("2026"),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[PedagogicalContextResponse]:
    async with session_factory() as session:
        service = TeachingContextService(session)
        try:
            await service.verify_teacher_classroom_scope(
                teacher_id=identity.external_user_id,
                school_id=school_id,
                classroom_id=classroom_id,
            )
        except ScopeAuthorizationError as exc:
            raise HTTPException(status_code=403, detail=str(exc))
        contexts = await service.get_active_recent_contexts(
            school_id=school_id,
            classroom_id=classroom_id,
            academic_year=academic_year,
        )
        return [_to_context_response(c) for c in contexts]
```

`ScopeAuthorizationError` já está importado no topo do arquivo — nenhum import novo é necessário.

- [ ] **Step 4: Rode para ver passar**

Run: `.venv/bin/python -m pytest tests/test_r0_pedagogical_context_route_authorization.py -q`
Expected: PASS, 3 testes. Verificado por execução antes de escrever este plano.

- [ ] **Step 5: Rode as suítes que exercitam este arquivo**

Run: `.venv/bin/python -m pytest tests/test_teaching_context.py tests/test_coordination_portal.py tests/test_teacher_portal.py tests/test_end_to_end_learning_flow.py -q`
Expected: zero falhas — 31 testes, verificado por execução antes de escrever este plano.

- [ ] **Step 6: Rode o gate da fase inteiro**

Run: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py tests/test_coordination_portal.py tests/test_teacher_portal.py tests/test_teaching_context.py tests/test_end_to_end_learning_flow.py -q`
Expected: zero falhas.

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/api/routes/teaching_context.py tests/test_r0_pedagogical_context_route_authorization.py
git commit -m "fix: rota de contexto pedagogico deixa de ler qualquer escola sem checagem

get_classroom_pedagogical_context injetava a identidade e nunca a lia.
Qualquer identidade autenticada lia contexto pedagogico de qualquer escola,
variando school_id na query. A correcao liga verify_teacher_classroom_scope,
a mesma checagem que a rota vizinha no mesmo arquivo ja usa para outro
recurso — nenhuma logica nova, um ponto de chamada a mais.

Achado pela revisao final da Fase 3C onda 2, nomeado la como diferente em
especie dos outros dois: ausencia total de autorizacao, nao checagem
incompleta."
```

---

## Depois da última tarefa

Rode o gate desta fase e a suíte completa contra um banco descartável, comparando com o baseline no commit em que esta fase começou. Esta fase é pequena e não deveria mover o número da suíte completa além dos três testes que ela mesma acrescenta.

## O que fica para a próxima onda de 3C

`knowledge.py::_is_question_visible`, `question_governance.py`, `reception.py`, `teacher_materials.py`, `assessments.py`, `study_session.py` — nenhuma foi lida linha a linha ainda. A pergunta em aberto da onda 2 continua aberta: a cláusula `scope_type == AdminScopeType.PLATFORM` em `coordination_portal.py::verify_coordinator_scope` precisa sumir também, ou existe um caso real que a justifica? A remoção do andaime `TURMA_3A` e o fim da transição continuam esperando essa migração terminar.

Vale conferir também se outras rotas do mesmo arquivo (`teaching_context.py`) ou de arquivos vizinhos (`coordination_portal.py`'s rotas, se existirem) têm o mesmo padrão de identidade injetada e nunca lida — esta onda corrigiu a que foi nomeada, não uma varredura do arquivo inteiro.
