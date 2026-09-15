# R0 Fase 3C — Autorização (Onda 2): Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fechar a travessia de tenant que a revisão final da onda 1 encontrou e nomeou como primeiro item desta onda: um diretor ou coordenador de uma escola acessa turma de **outra** escola, porque `verify_teacher_classroom_scope` nunca checa `school_id` para esses dois papéis.

**Architecture:** Uma correção pontual, no formato que o próprio arquivo já usa corretamente logo abaixo — `verify_coordinator_scope`, a função vizinha, já distingue `PLATFORM_ADMIN` (global de verdade) de `DIRECTOR`/`COORDINATOR` (checa `school_id`). `verify_teacher_classroom_scope` passa a fazer o mesmo.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x async, `unittest.IsolatedAsyncioTestCase` com `sqlite+aiosqlite:///:memory:` e `StaticPool`.

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

`services/teaching_context.py::verify_teacher_classroom_scope` verifica se `teacher_id` pode acessar `classroom_id` em `school_id`, iterando sobre os vínculos do próprio `teacher_id`:

```python
for link in links:
    if link.role in (AdminRole.PLATFORM_ADMIN, AdminRole.DIRECTOR, AdminRole.COORDINATOR):
        return True
    ...
```

Não há checagem de `school_id` nenhuma para `DIRECTOR` ou `COORDINATOR`. `PLATFORM_ADMIN` é o único papel que legitimamente não tem escola — confirmado em `services/admin.py:318`, `link_user_to_school` recusa `school_id=None` para qualquer papel que não seja `PLATFORM_ADMIN`. `DIRECTOR` e `COORDINATOR` sempre têm um `school_id` real, e a função nunca olha para ele.

Rodei o cenário exato antes de escrever este plano:

```
diretor-a: vinculo DIRECTOR na escola A
verify_teacher_classroom_scope(teacher_id="diretor-a", school_id=<escola B>, classroom_id="QUALQUER")
ANTES desta correcao: True — diretor da escola A acessa turma da escola B
DEPOIS desta correcao: ScopeAuthorizationError — negado
```

**A função vizinha no mesmo arquivo já faz certo.** `verify_coordinator_scope`, logo abaixo, tem:

```python
if link.role == AdminRole.PLATFORM_ADMIN:
    return True
if link.school_id == school_id or link.scope_type == AdminScopeType.PLATFORM:
    return True
```

A correção desta onda é aplicar exatamente essa forma a `verify_teacher_classroom_scope` — não inventar um desenho novo, replicar o que já está certo duas funções abaixo.

**Impacto real, não hipotético.** A função tem seis pontos de chamada em produção: `api/routes/teaching_context.py:170` (busca uma aula pelo id, passando `teacher_id=identity.external_user_id` — o usuário autenticado — e `school_id=lesson.school_id`), mais chamadas internas em `services/teaching_context.py`, `services/study_session.py` e `services/teacher_portal.py` (duas vezes). Qualquer requisição por essas rotas, feita por alguém com vínculo `DIRECTOR` ou `COORDINATOR` em qualquer escola, hoje passa para aula de qualquer outra.

**Não há teste dedicado hoje.** `grep -rl verify_teacher_classroom_scope tests/` só acha `tests/manual/phase24_study_session_report.py`, um script manual. As chamadas em `test_teaching_context.py`, `test_teacher_portal.py`, `test_coordination_portal.py` e `test_end_to_end_learning_flow.py` exercitam a função indiretamente através de outros métodos — nenhuma delas semeia um diretor de duas escolas para provar isolamento de tenant. Rodei as quatro suítes inteiras contra a correção antes de escrever este plano: **31 testes, zero falhas.**

---

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/agente_ia_edu/services/teaching_context.py` (**modificar**) | `verify_teacher_classroom_scope` passa a checar `school_id` para `DIRECTOR`/`COORDINATOR`, no mesmo formato que `verify_coordinator_scope` já usa. |
| `tests/test_r0_teacher_scope_cross_school.py` (**criar**) | Prova a travessia de tenant e o fechamento dela, e prova que os casos legítimos (`PLATFORM_ADMIN`, diretor na própria escola, professor na própria turma) continuam funcionando. |

---

### Task 1: `verify_teacher_classroom_scope` checa `school_id` para diretor e coordenador

**Files:**
- Modify: `src/agente_ia_edu/services/teaching_context.py:73-75`
- Test: `tests/test_r0_teacher_scope_cross_school.py` (criar)

**Interfaces:**
- Consumes: nada novo.
- Produces: nada novo — `verify_teacher_classroom_scope` mantém assinatura e comportamento de exceção.

- [ ] **Step 1: Escreva o teste que falha**

Crie `tests/test_r0_teacher_scope_cross_school.py`:

```python
# tests/test_r0_teacher_scope_cross_school.py
"""verify_teacher_classroom_scope never checked school_id for DIRECTOR/COORDINATOR
links, so a director of one school passed for a classroom of any other. The
sibling function two methods below, verify_coordinator_scope, already gets this
right - this fix makes verify_teacher_classroom_scope match it.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.teaching_context import ScopeAuthorizationError, TeachingContextService


class TeacherScopeCrossSchoolTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_a_director_of_one_school_is_denied_another_schools_classroom(self):
        """The reported shape exactly, run against the real service."""
        async with self.session_factory() as session:
            school_a, school_b = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="director-a",
                role=AdminRole.DIRECTOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )

            svc = TeachingContextService(session)
            with self.assertRaises(ScopeAuthorizationError):
                await svc.verify_teacher_classroom_scope(
                    teacher_id="director-a",
                    school_id=school_b.id,
                    classroom_id="QUALQUER-TURMA-DE-B",
                )

    async def test_a_coordinator_of_one_school_is_denied_another_schools_classroom(self):
        """Same bug, same fix, the other of the two affected roles."""
        async with self.session_factory() as session:
            school_a, school_b = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-a",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )

            svc = TeachingContextService(session)
            with self.assertRaises(ScopeAuthorizationError):
                await svc.verify_teacher_classroom_scope(
                    teacher_id="coord-a",
                    school_id=school_b.id,
                    classroom_id="QUALQUER-TURMA-DE-B",
                )

    async def test_a_director_still_accesses_their_own_school(self):
        """The fix must not deny what was always legitimate - only what was
        never declared."""
        async with self.session_factory() as session:
            school_a, _ = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="director-own",
                role=AdminRole.DIRECTOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )

            svc = TeachingContextService(session)
            allowed = await svc.verify_teacher_classroom_scope(
                teacher_id="director-own",
                school_id=school_a.id,
                classroom_id="QUALQUER-TURMA-DA-PROPRIA-ESCOLA",
            )
        self.assertTrue(allowed)

    async def test_a_platform_admin_is_unaffected(self):
        """PLATFORM_ADMIN is the one role that genuinely has no school - the
        only role link_user_to_school lets through without one."""
        async with self.session_factory() as session:
            _, school_b = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="platform-admin-1",
                role=AdminRole.PLATFORM_ADMIN,
                scope_type=AdminScopeType.PLATFORM,
            )

            svc = TeachingContextService(session)
            allowed = await svc.verify_teacher_classroom_scope(
                teacher_id="platform-admin-1",
                school_id=school_b.id,
                classroom_id="QUALQUER-TURMA",
            )
        self.assertTrue(allowed)

    async def test_a_teacher_in_their_own_classroom_is_unaffected(self):
        """The TEACHER branch below this fix already checked school_id and
        must keep behaving exactly as it did."""
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

            svc = TeachingContextService(session)
            allowed = await svc.verify_teacher_classroom_scope(
                teacher_id="teacher-a", school_id=school_a.id, classroom_id="TURMA-A1"
            )
        self.assertTrue(allowed)

        async with self.session_factory() as session:
            svc = TeachingContextService(session)
            with self.assertRaises(ScopeAuthorizationError):
                await svc.verify_teacher_classroom_scope(
                    teacher_id="teacher-not-linked-at-all",
                    school_id=uuid.uuid4(),
                    classroom_id="QUALQUER",
                )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rode para ver falhar**

Run: `.venv/bin/python -m pytest tests/test_r0_teacher_scope_cross_school.py -q`
Expected: FAIL — os dois primeiros testes não levantam `ScopeAuthorizationError` (a função devolve `True` para qualquer `DIRECTOR`/`COORDINATOR`, de qualquer escola). Os três últimos já passam hoje.

- [ ] **Step 3: Implemente a correção**

Em `src/agente_ia_edu/services/teaching_context.py`, substitua o início do laço em `verify_teacher_classroom_scope`:

```python
        for link in links:
            if link.role == AdminRole.PLATFORM_ADMIN:
                return True

            if link.role in (AdminRole.DIRECTOR, AdminRole.COORDINATOR):
                if link.school_id == school_id or link.scope_type == AdminScopeType.PLATFORM:
                    return True
                continue

            if link.role == AdminRole.TEACHER:
```

O resto da função — o bloco `if link.role == AdminRole.TEACHER:` e tudo abaixo dele, e o `raise ScopeAuthorizationError` final — fica exatamente como está.

- [ ] **Step 4: Rode para ver passar**

Run: `.venv/bin/python -m pytest tests/test_r0_teacher_scope_cross_school.py -q`
Expected: PASS, 5 testes. Verificado por execução antes de escrever este plano.

- [ ] **Step 5: Rode as suítes que exercitam esta função indiretamente**

Run: `.venv/bin/python -m pytest tests/test_coordination_portal.py tests/test_end_to_end_learning_flow.py tests/test_teaching_context.py tests/test_teacher_portal.py -q`
Expected: zero falhas — 31 testes antes desta correção, verificado por execução antes de escrever este plano.

- [ ] **Step 6: Rode o gate da fase inteiro**

Run: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py tests/test_coordination_portal.py tests/test_teacher_portal.py tests/test_teaching_context.py tests/test_end_to_end_learning_flow.py -q`
Expected: zero falhas.

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/services/teaching_context.py tests/test_r0_teacher_scope_cross_school.py
git commit -m "fix: diretor e coordenador deixam de acessar turma de outra escola

verify_teacher_classroom_scope nunca checava school_id para DIRECTOR e
COORDINATOR, so para TEACHER. Um diretor da escola A passava para qualquer
turma de qualquer escola B. A funcao vizinha, verify_coordinator_scope, ja
fazia essa checagem certo — esta correcao replica a mesma forma.

Achado pela revisao final da Fase 3C onda 1, nomeado la como primeiro item
desta onda. Sem teste dedicado antes desta correcao."
```

---

## Depois da última tarefa

Rode o gate desta fase e a suíte completa contra um banco descartável, comparando com o baseline no commit em que esta fase começou. Esta fase é pequena e não deveria mover o número da suíte completa além dos cinco testes que ela mesma acrescenta.

## O que fica para a próxima onda de 3C

`knowledge.py::_is_question_visible`, `question_governance.py`, `reception.py`, `teacher_materials.py`, `assessments.py`, `study_session.py` — nenhuma foi lida linha a linha para saber se carrega o mesmo padrão de "conjunto ou contexto vazio, ou vínculo sem checagem de escola, lido como ausência de restrição". A remoção do andaime `TURMA_3A` (passo 6 da §7) e o fim da transição (passo 7) continuam esperando essa migração terminar.

`teaching_context.py::record_lesson` e os outros métodos do arquivo que chamam `verify_teacher_classroom_scope` internamente (linhas 141 e 235) não foram lidos além do necessário para confirmar que a correção desta onda não muda o contrato deles — vale conferir se algum deles tem lógica própria de fallback que também merece o mesmo tratamento.
