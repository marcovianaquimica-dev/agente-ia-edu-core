# R0 Fase 3C — Autorização, onda 4 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fecha os quatro atalhos que ainda concedem acesso entre escolas sem checar `school_id`/`classroom_id`, dentro das mesmas funções de autorização que as ondas 1-3 desta fase já endureceram.

**Architecture:** Nenhuma lógica nova — só remoção. Três dos quatro pontos são o padrão "identidade sem nenhum vínculo, mas com formato de teste (`teacher:*`, `prof_mendes`, `coordinator:*`, `coord_1`) recebe `True`/lista cheia sem checar nada"; o quarto é a mesma cláusula `scope_type == AdminScopeType.PLATFORM` que a onda 2 já removeu do método irmão, ainda viva em `verify_coordinator_scope`. Removida a lógica, o caminho "sem vínculo" cai no `raise ScopeAuthorizationError` (ou lista vazia) que já existe logo abaixo — nenhum branch novo.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async, pytest/unittest.

**Spec:** `docs/superpowers/specs/2026-09-13-r0-estrutura-academica-configuracao-design.md` (§7, passo 5 — autorização, sozinha, por último).

## Global Constraints

- Python `>=3.13,<3.14` (de `pyproject.toml`). Nenhuma dependência nova. Nenhuma migração.
- Nenhuma coluna ou campo com formato de credencial (`password`, `token`, `secret`, `credential`, `senha`).
- Testes usam `unittest.IsolatedAsyncioTestCase` com `sqlite+aiosqlite:///:memory:` + `StaticPool` (convenção já usada em todos os testes `test_r0_*` desta fase) — nenhum teste precisa de banco real.
- Código e comentários em inglês; mensagem de commit em português.

---

## Contexto verificado por execução antes deste plano

Os quatro achados abaixo foram provados ao vivo contra o código atual (não apenas lidos), e a remoção proposta foi aplicada de verdade, testada contra as suítes vizinhas e o gate inteiro da fase, e revertida antes de escrever este plano — mesma disciplina das ondas 1-3.

Probe (identidade com **zero vínculos**, ou vínculo real na **escola errada**):

```
Probe 1 (verify_teacher_classroom_scope, no links, teacher:ghost):     LEAKED, returned True
Probe 2 (verify_coordinator_scope, no links, coordinator:ghost):        LEAKED, returned True
Probe 3 (get_teacher_authorized_classrooms, no links, teacher:ghost):   ['TURMA_3A', 'TURMA_3B']
Probe 4 (verify_coordinator_scope, real link at OTHER school, PLATFORM): LEAKED, returned True
```

Depois da remoção, os mesmos quatro probes:

```
Probe 1: denied -> User 'teacher:ghost' has no active school bindings.
Probe 2: denied -> User 'coordinator:ghost' has no active school bindings.
Probe 3: []
Probe 4: denied -> Coordinator 'coord-of-school-a' is not authorized for school '...'.
```

Gate da fase (`tests/test_r0_*.py tests/test_platform_administration.py tests/test_coordination_portal.py tests/test_teacher_portal.py tests/test_teaching_context.py tests/test_end_to_end_learning_flow.py`) rodado contra a correção real: **215 passed, 87 subtests, 0 falhas** — idêntico ao baseline. As suítes vizinhas mais os testes `test_r0_*` relevantes (`test_teaching_context.py`, `test_coordination_portal.py`, `test_teacher_portal.py`, `test_end_to_end_learning_flow.py`, `test_r0_scope_validation_teacher.py`, `test_r0_teacher_scope_cross_school.py`, `test_r0_pedagogical_context_route_authorization.py`): **47 passed, 0 falhas**. Nenhum teste hoje depende dos atalhos removidos.

---

## Task 1: Remove os três escapes de `TeachingContextService`

**Files:**
- Modify: `src/agente_ia_edu/services/teaching_context.py:58-120` (`verify_teacher_classroom_scope` e `verify_coordinator_scope`)
- Test: `tests/test_r0_dev_fallback_removal.py` (criar)

**Interfaces:**
- Consumes: `PlatformAdminService.link_user_to_school` (assinatura já usada nos testes desta fase — ver Step 1) e `PlatformAdminService.get_user_active_links` (inalterado, não é tocado por esta tarefa).
- Produces: `TeachingContextService.verify_teacher_classroom_scope` e `.verify_coordinator_scope` continuam com a mesma assinatura pública (`bool`/`ScopeAuthorizationError`) — só o corpo muda. Nenhum consumidor (rotas, `teacher_portal.py`, `coordination_portal.py`) precisa mudar.

- [ ] **Step 1: Escreva os três testes que falham**

Crie `tests/test_r0_dev_fallback_removal.py`:

```python
# tests/test_r0_dev_fallback_removal.py
"""verify_teacher_classroom_scope and verify_coordinator_scope both grant
access to any identity shaped like a dev/test subject (teacher_id starting
with "teacher:", or exactly "prof_mendes"; coordinator_id starting with
"coordinator:", or exactly "coord_1") even when that identity has ZERO real
links - no school_id or classroom_id check at all. Confirmed live: an
identity with no links read another school's data through both functions.

verify_coordinator_scope also let a DIRECTOR/COORDINATOR link registered at
one school pass for ANY OTHER school, as long as its scope_type was
PLATFORM. link_user_to_school never validates scope_type against role, so
such a link is creatable through the ordinary API. This is the exact clause
onda 2 already removed from verify_teacher_classroom_scope's sibling check -
verify_coordinator_scope never got the same fix.

All three removals leave no new logic behind: the existing "no links" raise,
and the existing per-link school_id comparison, already do the right thing
once the shortcut is gone.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.teaching_context import ScopeAuthorizationError, TeachingContextService


class DevFallbackRemovalTests(unittest.IsolatedAsyncioTestCase):
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

    async def _school(self, session, code):
        school = School(id=uuid.uuid4(), code=f"SCH-{code}", name=f"school-{code}")
        session.add(school)
        await session.commit()
        return school

    async def test_teacher_classroom_scope_denies_unlinked_dev_shaped_identity(self):
        """teacher_id="teacher:ghost" (matches the removed prefix) has ZERO
        links - it must be denied like any other unlinked identity, not
        silently let through."""
        async with self.session_factory() as session:
            school = await self._school(session, "T")
            svc = TeachingContextService(session)
            with self.assertRaises(ScopeAuthorizationError):
                await svc.verify_teacher_classroom_scope(
                    teacher_id="teacher:ghost", school_id=school.id, classroom_id="QUALQUER"
                )

    async def test_coordinator_scope_denies_unlinked_dev_shaped_identity(self):
        """coordinator_id="coord_1" (the removed exact match) has ZERO
        links - same denial as any other unlinked identity."""
        async with self.session_factory() as session:
            school = await self._school(session, "C")
            svc = TeachingContextService(session)
            with self.assertRaises(ScopeAuthorizationError):
                await svc.verify_coordinator_scope(coordinator_id="coord_1", school_id=school.id)

    async def test_coordinator_scope_denies_platform_scoped_link_at_the_wrong_school(self):
        """A COORDINATOR link registered at school A, with scope_type
        PLATFORM, must not authorize school B - only a real school_id match,
        or PLATFORM_ADMIN, does."""
        async with self.session_factory() as session:
            school_a = await self._school(session, "A")
            school_b = await self._school(session, "B")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-of-school-a",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.PLATFORM,
                school_id=school_a.id,
            )

            svc = TeachingContextService(session)
            with self.assertRaises(ScopeAuthorizationError):
                await svc.verify_coordinator_scope(
                    coordinator_id="coord-of-school-a", school_id=school_b.id
                )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rode para confirmar que falham**

Run: `.venv/bin/python -m pytest tests/test_r0_dev_fallback_removal.py -v`
Expected: as três `test_*` FALHAM (o código atual ainda concede acesso — as chamadas retornam `True`/não levantam `ScopeAuthorizationError`, então `assertRaises` falha).

- [ ] **Step 3: Remova os dois atalhos de dev/teste**

Em `src/agente_ia_edu/services/teaching_context.py`, dentro de `verify_teacher_classroom_scope`:

```python
        links = await self.admin_service.get_user_active_links(teacher_id)
        if not links:
            raise ScopeAuthorizationError(f"User '{teacher_id}' has no active school bindings.")
```

(Isso substitui o bloco atual, que tinha um `if teacher_id.startswith("teacher:") or teacher_id == "prof_mendes": return True` entre o `if not links:` e o `raise` — remova só essas duas linhas do meio, mantendo `if not links:` e o `raise` como estão.)

Dentro de `verify_coordinator_scope`, mesma remoção:

```python
        links = await self.admin_service.get_user_active_links(coordinator_id)
        if not links:
            raise ScopeAuthorizationError(f"User '{coordinator_id}' has no active school bindings.")
```

- [ ] **Step 4: Remova a cláusula PLATFORM de `verify_coordinator_scope`**

No mesmo método, o laço atual é:

```python
        for link in links:
            if link.role in (AdminRole.PLATFORM_ADMIN, AdminRole.DIRECTOR, AdminRole.COORDINATOR):
                if link.role == AdminRole.PLATFORM_ADMIN:
                    return True
                if link.school_id == school_id or link.scope_type == AdminScopeType.PLATFORM:
                    return True
```

Troque a última condição, removendo só o `or link.scope_type == AdminScopeType.PLATFORM`:

```python
        for link in links:
            if link.role in (AdminRole.PLATFORM_ADMIN, AdminRole.DIRECTOR, AdminRole.COORDINATOR):
                if link.role == AdminRole.PLATFORM_ADMIN:
                    return True
                if link.school_id == school_id:
                    return True
```

- [ ] **Step 5: Rode para confirmar que passam**

Run: `.venv/bin/python -m pytest tests/test_r0_dev_fallback_removal.py -v`
Expected: PASS, 3 testes. Verificado por execução antes de escrever este plano.

- [ ] **Step 6: Rode as suítes vizinhas**

Run: `.venv/bin/python -m pytest tests/test_teaching_context.py tests/test_coordination_portal.py tests/test_teacher_portal.py tests/test_end_to_end_learning_flow.py tests/test_r0_teacher_scope_cross_school.py tests/test_r0_pedagogical_context_route_authorization.py -q`
Expected: zero falhas — 44 testes, verificado por execução antes de escrever este plano (esse número exclui `test_r0_scope_validation_teacher.py`, que é o alvo da Task 2, e o próprio `test_r0_dev_fallback_removal.py`, novo nesta tarefa).

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/services/teaching_context.py tests/test_r0_dev_fallback_removal.py
git commit -m "fix: remove atalhos de dev/teste e clausula PLATFORM de verify_coordinator_scope

verify_teacher_classroom_scope e verify_coordinator_scope concediam acesso
a qualquer identidade sem vinculo nenhum, desde que o teacher_id/
coordinator_id tivesse formato de sujeito de teste (teacher:*, prof_mendes,
coordinator:*, coord_1) - sem checar school_id nem classroom_id. Provado ao
vivo: identidade sem vinculo leu dados de outra escola pelas duas funcoes.

verify_coordinator_scope tambem aceitava um vinculo DIRECTOR/COORDINATOR de
scope_type PLATFORM registrado em QUALQUER escola para autorizar QUALQUER
outra - a mesma clausula que a onda 2 ja removeu da funcao irma
(verify_teacher_classroom_scope), que nunca chegou aqui.

Nenhum teste existente dependia dos tres atalhos (confirmado por execucao
antes deste plano)."
```

---

## Task 2: Remove o atalho de dev/teste de `TeacherPortalService`

**Files:**
- Modify: `src/agente_ia_edu/services/teacher_portal.py:150-166` (`get_teacher_authorized_classrooms`)
- Test: `tests/test_r0_scope_validation_teacher.py` (arquivo já existe — adicionar um teste)

**Interfaces:**
- Consumes: nada de Task 1 — arquivo e função diferentes, independentes.
- Produces: `TeacherPortalService.get_teacher_authorized_classrooms` mantém a mesma assinatura pública (`list[str]`). Consumidores existentes (`get_teacher_dashboard`, `verify_student_access`, conforme já documentado em `tests/test_r0_teacher_scope_cross_school.py`) não mudam.

- [ ] **Step 1: Escreva o teste que falha**

Adicione a `tests/test_r0_scope_validation_teacher.py`, dentro da classe `TeacherScopeValidationTests` já existente (depois do último teste, `test_every_code_survives_when_the_school_has_no_hierarchy_yet`):

```python
    async def test_an_unlinked_dev_shaped_identity_gets_no_classrooms(self):
        """teacher_id="teacher:ghost" (matches the removed dev/test prefix)
        has ZERO links to this school - it must get an empty list like any
        other unlinked identity, not every classroom in the school."""
        async with self.session_factory() as session:
            school = await self._school(session, "dev-fallback")
            portal = TeacherPortalService(session, None, None, None)
            classrooms = await portal.get_teacher_authorized_classrooms("teacher:ghost", school.id)

        self.assertEqual(classrooms, [])
```

- [ ] **Step 2: Rode para confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_r0_scope_validation_teacher.py::TeacherScopeValidationTests::test_an_unlinked_dev_shaped_identity_gets_no_classrooms -v`
Expected: FALHA (`AssertionError: ['TURMA_3A', 'TURMA_3B'] != []`, já que não há nenhuma `TeachingLesson` seed nesta escola).

- [ ] **Step 3: Remova o atalho**

Em `src/agente_ia_edu/services/teacher_portal.py`, dentro de `get_teacher_authorized_classrooms`, o corpo atual começa assim:

```python
        links = await self.admin_service.get_user_active_links(teacher_id)

        # Dev / test fallback for test subjects
        if not links and (teacher_id.startswith("teacher:") or teacher_id in ("prof_mendes", "prof_joao")):
            # Return all classrooms in school or recorded lessons
            stmt = select(TeachingLesson.classroom_id).where(TeachingLesson.school_id == school_id).distinct()
            res = await self.session.execute(stmt)
            classrooms = list(res.scalars().all())
            return classrooms if classrooms else ["TURMA_3A", "TURMA_3B"]

        authorized_classrooms = set()
```

Remova o bloco `# Dev / test fallback...` até o `return classrooms if classrooms else [...]` inclusive, deixando:

```python
        links = await self.admin_service.get_user_active_links(teacher_id)

        authorized_classrooms = set()
```

- [ ] **Step 4: Rode para confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_r0_scope_validation_teacher.py -v`
Expected: PASS, 4 testes (os 3 já existentes + o novo). Verificado por execução antes de escrever este plano.

- [ ] **Step 5: Rode as suítes vizinhas**

Run: `.venv/bin/python -m pytest tests/test_teacher_portal.py tests/test_coordination_portal.py tests/test_end_to_end_learning_flow.py tests/test_r0_teacher_scope_cross_school.py -q`
Expected: zero falhas.

- [ ] **Step 6: Rode o gate inteiro da fase**

Run: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py tests/test_coordination_portal.py tests/test_teacher_portal.py tests/test_teaching_context.py tests/test_end_to_end_learning_flow.py -q`
Expected: zero falhas, mesma contagem do baseline (215 + os 4 testes novos desta onda = 219 passed, 87 subtests).

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/services/teacher_portal.py tests/test_r0_scope_validation_teacher.py
git commit -m "fix: remove atalho de dev/teste de get_teacher_authorized_classrooms

Identidade sem nenhum vinculo, mas com teacher_id no formato de sujeito de
teste (teacher:*, prof_mendes, prof_joao), recebia a lista inteira de
turmas da escola pedida - ou o fallback fixo TURMA_3A/TURMA_3B. Provado ao
vivo: identidade sem vinculo leu turmas de uma escola com a qual nao tinha
nenhuma relacao.

Sem o atalho, o caminho ja existente para 'sem vinculo' (laco vazio sobre
authorized_classrooms) devolve lista vazia, como os testes existentes de
tests/test_r0_scope_validation_teacher.py ja cobrem para vinculos reais."
```

---

## Depois da última tarefa

Rode o gate desta fase e a suíte completa contra um banco descartável, comparando com o baseline da onda 3 (2007 passed, 4 skipped, 448 subtests, 13 erros ambientais pré-existentes). Esta onda não deveria mover o número além dos 4 testes que ela mesma acrescenta.

## O que fica para a próxima onda de 3C

`coordination_portal.py` tem o MESMO padrão de atalho de dev/teste, em pelo menos duas funções (`get_coordinator_authorized_scopes`, por volta da linha 82-86, e outra ocorrência por volta da linha 259-261): identidade sem vínculo, mas com `coordinator_id` no formato `coordinator:*`, `director:*`, ou em `("coord_1", "coord_a", "admin:master")`, recebe `is_global: True` com turmas fabricadas. Diferente dos quatro pontos desta onda, `tests/test_phase15_list_persistence_export.py:252` usa `_ctx("coord_1", ...)` — é preciso confirmar se esse teste depende do atalho ou já cria vínculo real antes de decidir remover, não presumir que é seguro como nesta onda.

Continuam abertos, sem mudança nesta onda: as 12 rotas em `catalog.py`/`discovery.py`/`question_bank.py` com `identity` injetada e nunca lida (achado da revisão final da onda 3), a ausência de branch para papel `STUDENT` em `verify_teacher_classroom_scope`, e a remoção do andaime `TURMA_3A`/`TURMA_3B` (passo 6 do §7 da spec) — que só pode começar depois que a autorização (passo 5) for considerada fechada, e `coordination_portal.py` ainda não foi.

### Achado da revisão final desta onda, confirmado por execução e enfileirado (não corrigido aqui)

A revisão final de branch (dispatch em modelo mais capaz, sobre `15e335c..530a95a`) aprovou o merge, mas achou e provou ao vivo:

- **`TeacherPortalService._fetch_students_in_classrooms` (`services/teacher_portal.py:668-696`) trata lista de turmas vazia como "sem filtro"**, não como "nenhuma turma autorizada". Antes desta onda, `get_teacher_authorized_classrooms` devolvia `["TURMA_3A", "TURMA_3B"]` para uma identidade sem vínculo em formato de dev — errado, mas pelo menos ficava dentro da escola pedida. Depois da Task 2 desta onda, a mesma função devolve `[]` para essa identidade, e `_fetch_students_in_classrooms` cai numa query sem `school_id` nenhum. Reproduzido ao vivo: identidade sem vínculo, pedindo a escola B, recebeu aluno da escola A. **Importante mas não é regressão nova**: o revisor confirmou que esse buraco já era alcançável antes desta onda por qualquer identidade sem vínculo cujo nome não batesse no antigo atalho (ou seja, a maioria das identidades reais) — esta onda só estendeu o mesmo buraco pré-existente para a família de nomes que antes tinha um atalho diferente, também errado. A correção fica numa função que esta onda não tocou; precisa do seu próprio plano+teste, ideal combinada com o fechamento de `coordination_portal.py` (mesma classe de problema em duas camadas).
- `teacher_portal.py:175,183` ainda devolvem `["TURMA_3A"]` como fallback para usuários com vínculo real, mas query vazia — decisão deliberada, não um esquecimento: pertence ao passo 6 do §7, que só pode começar depois que o passo 5 (autorização) fechar de verdade.
