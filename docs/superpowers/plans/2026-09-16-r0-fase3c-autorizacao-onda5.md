# R0 Fase 3C — Autorização, onda 5 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fecha `coordination_portal.py` — o último arquivo com o padrão de atalho "sem vínculo real = acesso global" que as quatro ondas anteriores desta fase já removeram de todo o resto — e a lacuna irmã em `TeacherPortalService._fetch_students_in_classrooms`, que a onda 4 acabou de tornar alcançável para mais uma família de identidades.

**Architecture:** Só remoção, igual às ondas anteriores. `CoordinationPortalService.get_coordinator_authorized_scopes` tem DOIS pontos independentes que tratam "sem vínculo" como acesso global — um atalho de dev/teste explícito no topo, e uma condição `or not links` mais abaixo que é **incondicional** (não checa o formato do id, apenas se há vínculo). `verify_coordinator_access` tem o mesmo atalho de dev/teste de sempre, guardando a chamada acima. `TeacherPortalService._fetch_students_in_classrooms` trata "nenhum aluno encontrado na escola pedida" como "sem filtro nenhum" e cai numa busca sem `school_id`. Nenhuma lógica nova em nenhum dos quatro pontos — remover o atalho deixa o caminho já existente (`raise`, ou lista/set vazio) fazer a coisa certa.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async, pytest/unittest.

**Spec:** `docs/superpowers/specs/2026-09-13-r0-estrutura-academica-configuracao-design.md` (§7, passo 5 — autorização, sozinha, por último).

## Global Constraints

- Python `>=3.13,<3.14` (de `pyproject.toml`). Nenhuma dependência nova. Nenhuma migração.
- Nenhuma coluna ou campo com formato de credencial (`password`, `token`, `secret`, `credential`, `senha`).
- Testes usam `unittest.IsolatedAsyncioTestCase` com `sqlite+aiosqlite:///:memory:` + `StaticPool` (convenção já usada em todos os testes `test_r0_*` desta fase) — nenhum teste precisa de banco real.
- Código e comentários em inglês; mensagem de commit em português.

---

## Contexto verificado por execução antes deste plano

Os quatro achados abaixo foram provados ao vivo contra o código atual, a remoção proposta foi aplicada de verdade, testada contra as suítes vizinhas, o gate da fase e a **suíte completa contra um banco descartável** (não só o gate — `_fetch_students_in_classrooms` é usada por várias funções de dashboard, então o risco de quebrar algo fora do gate da fase era maior que nas ondas anteriores), e revertida antes de escrever este plano.

Probe (identidade com **zero vínculos**, formato de dev/teste; ou consulta que não acha ninguém na escola pedida):

```
Probe 1 (get_coordinator_authorized_scopes, no links, coordinator:ghost): True {'TURMA_3A', 'TURMA_3B'}
Probe 2 (verify_coordinator_access, no links, director:ghost):            LEAKED, returned True
Probe 3 (_fetch_students_in_classrooms, school B has 0 students):         ['student:alice-A']
```

Depois da remoção, os mesmos três probes:

```
Probe 1: False set()
Probe 2: denied -> User 'director:ghost' has no active coordination bindings.
Probe 3: []
```

Suítes vizinhas (`test_coordination_portal.py`, `test_r0_coordinator_access_widening.py`, `test_r0_scope_validation_coordination.py`, `test_teacher_portal.py`, `test_r0_teacher_scope_cross_school.py`, `test_r0_scope_validation_teacher.py`, `test_teaching_context.py`, `test_end_to_end_learning_flow.py`) rodadas contra a correção real: **55 passed, 0 falhas**. Gate da fase: **219 passed, 87 subtests, 0 falhas** — idêntico ao baseline da onda 4. Suíte completa contra banco descartável reconstruído do zero: **2011 passed, 4 skipped, 448 subtests, 13 erros ambientais pré-existentes (mesmos de sempre), 0 falhas** — idêntico ao baseline da onda 4. Nenhum teste hoje depende de nenhum dos quatro atalhos removidos.

**Achado descartado do escopo, por não ser do mesmo tipo:** `coordination_portal.py` também tem um terceiro "fallback" perto da linha 598-609 (`get_teacher_oversight_summary` ou função vizinha), que substitui uma lista vazia de professores por um "Prof. Mendes" fabricado. Esse não é um bypass de autorização — não lê dado de outra escola, não depende do formato do id, só fabrica dado de demonstração quando a consulta real não acha nada. Fora de escopo desta onda (que é especificamente sobre vazamento entre escolas); fica como observação para quem for limpar dados de placeholder mais tarde.

**Caveat do plano da onda 4, resolvido:** `tests/test_phase15_list_persistence_export.py:252` usa `_ctx("coord_1", "school-A", role="COORDINATION")` — verificado que `_ctx` monta um `AuthenticatedUserContext` para um mecanismo de autenticação completamente diferente (`get_current_authenticated_context`, usado só pela feature de listas salvas da Fase 15), não chama `CoordinationPortalService` nem nada relacionado. `"coord_1"` ali é só um `user_id` de rótulo, não passa pelo atalho que esta onda remove. Confirmado por grep: nenhuma referência a `CoordinationPortalService`, `get_coordinator_authorized_scopes` ou `verify_coordinator_access` nesse arquivo.

---

## Task 1: Remove os dois escapes de `CoordinationPortalService`

**Files:**
- Modify: `src/agente_ia_edu/services/coordination_portal.py:74-145` (`get_coordinator_authorized_scopes`) e `:236-267` (`verify_coordinator_access`)
- Test: `tests/test_r0_coordination_dev_fallback_removal.py` (criar)

**Interfaces:**
- Consumes: `PlatformAdminService.link_user_to_school` e `.get_user_active_links` (inalterados, não tocados por esta tarefa).
- Produces: `CoordinationPortalService.get_coordinator_authorized_scopes` e `.verify_coordinator_access` continuam com a mesma assinatura pública (`dict[str, Any]` / `bool`+`ScopeAuthorizationError`) — só o corpo muda. `_resolve_scope_classrooms` e os três call-sites de `get_coordination_dashboard`/`get_coordination_hierarchy`/`compare_classrooms` (que já chamam `verify_coordinator_access` antes de qualquer outra coisa) não precisam mudar.

- [ ] **Step 1: Escreva os três testes que falham**

Crie `tests/test_r0_coordination_dev_fallback_removal.py`:

```python
# tests/test_r0_coordination_dev_fallback_removal.py
"""get_coordinator_authorized_scopes grants global access ("is_global": True,
with a fabricated classroom list) to any identity with ZERO real links, in
TWO independent places: an explicit dev/test shortcut at the top of the
function (coordinator_id shaped like "coordinator:*", "director:*", or one
of "coord_1"/"coord_a"/"admin:master"), and an unconditional `or not links`
a few lines below it that does not even check the id's shape - ANY unlinked
identity that reaches that point gets the same fabricated result.

In today's codebase, every caller of get_coordinator_authorized_scopes
reaches it only after verify_coordinator_access's own gate has already run -
so the unconditional branch is not independently reachable from outside this
file today. It is still fixed here: a function whose own docstring promises
"authorized scope filters" should not silently grant everything to a future
caller that skips the gate, and this is the same "empty set reads as
unrestricted" anti-pattern onda 1 of this phase already fixed for this
exact function's neighbours (see _resolved's docstring in this same file).

verify_coordinator_access has the matching dev/test shortcut in its own
"no links" branch - remove that too, and reaching
get_coordinator_authorized_scopes with an unlinked identity becomes
impossible again through the only path that exists today.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.services.coordination_portal import CoordinationPortalService
from agente_ia_edu.services.teaching_context import ScopeAuthorizationError


class CoordinationDevFallbackRemovalTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_authorized_scopes_denies_unlinked_dev_shaped_identity(self):
        """coordinator_id="coordinator:ghost" (matches the removed prefix)
        has ZERO links - must NOT get is_global=True with a fabricated
        classroom set."""
        async with self.session_factory() as session:
            school = await self._school(session, "1")
            portal = CoordinationPortalService(session, None, None, None, None)
            scopes = await portal.get_coordinator_authorized_scopes(
                "coordinator:ghost", school.id
            )

        self.assertFalse(scopes["is_global"])
        self.assertEqual(scopes["allowed_classrooms"], set())

    async def test_authorized_scopes_denies_any_unlinked_identity_regardless_of_shape(self):
        """The unconditional `or not links` clause: an identity whose name
        does NOT match any dev/test shape must ALSO get is_global=False, not
        just the dev-shaped ones - confirms the second, independent branch
        was fixed too, not only the top shortcut."""
        async with self.session_factory() as session:
            school = await self._school(session, "2")
            portal = CoordinationPortalService(session, None, None, None, None)
            scopes = await portal.get_coordinator_authorized_scopes(
                "nobody-at-all", school.id
            )

        self.assertFalse(scopes["is_global"])
        self.assertEqual(scopes["allowed_classrooms"], set())

    async def test_verify_coordinator_access_denies_unlinked_dev_shaped_identity(self):
        """coordinator_id="director:ghost" (matches the removed prefix) has
        ZERO links - verify_coordinator_access must raise, not pass
        through to get_coordinator_authorized_scopes at all."""
        async with self.session_factory() as session:
            school = await self._school(session, "3")
            portal = CoordinationPortalService(session, None, None, None, None)
            with self.assertRaises(ScopeAuthorizationError):
                await portal.verify_coordinator_access(
                    coordinator_id="director:ghost", school_id=school.id
                )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rode para confirmar que falham**

Run: `.venv/bin/python -m pytest tests/test_r0_coordination_dev_fallback_removal.py -v`
Expected: os três `test_*` FALHAM (o código atual concede acesso global / não levanta `ScopeAuthorizationError`).

- [ ] **Step 3: Remova o atalho de dev/teste do topo de `get_coordinator_authorized_scopes`**

Em `src/agente_ia_edu/services/coordination_portal.py`, o corpo atual começa assim:

```python
        """Returns authorized scope filters for coordinator_id in school_id."""
        links = await self.admin_service.get_user_active_links(coordinator_id)

        # Dev / test fallback
        if not links and (
            coordinator_id.startswith("coordinator:")
            or coordinator_id.startswith("director:")
            or coordinator_id in ("coord_1", "coord_a", "admin:master")
        ):
            stmt = select(TeachingLesson.classroom_id).where(TeachingLesson.school_id == school_id).distinct()
            res = await self.session.execute(stmt)
            classrooms = list(res.scalars().all()) or ["TURMA_3A", "TURMA_3B"]
            return {
                "is_global": True,
                "allowed_classrooms": set(classrooms),
                "allowed_grades": {"1ª Série", "2ª Série", "3ª Série"},
                "allowed_units": {"Unidade Principal"},
                "allowed_segments": {"Ensino Médio"},
            }

        allowed_classrooms = set()
```

Remova o bloco `# Dev / test fallback` até o `}` que fecha o `return` inclusive, deixando:

```python
        """Returns authorized scope filters for coordinator_id in school_id."""
        links = await self.admin_service.get_user_active_links(coordinator_id)

        allowed_classrooms = set()
```

- [ ] **Step 4: Torne a condição `or not links` incondicional em algo que fecha, não abre, acesso**

Mais abaixo, no mesmo método, depois do laço `for link in links:`:

```python
        if is_global or not links:
```

Troque para:

```python
        if is_global:
```

(A branch `is_global` continua devolvendo `{"is_global": True, ...}` como hoje. Sem vínculo nenhum, `links` fica vazio, o laço não roda, `is_global` continua `False`, e o código cai para a seção do `resolver` abaixo, que recebe sets vazios e devolve sets vazios — `_resolved` já trata isso: "if not codes: return codes". O retorno final vira `{"is_global": False, "allowed_classrooms": set(), ...}`, que é o resultado correto para "sem vínculo".)

- [ ] **Step 5: Remova o atalho de dev/teste de `verify_coordinator_access`**

No mesmo arquivo, `verify_coordinator_access` tem:

```python
        else:
            if not (
                coordinator_id.startswith("coordinator:")
                or coordinator_id.startswith("director:")
                or coordinator_id in ("coord_1", "coord_a", "admin:master")
            ):
                raise ScopeAuthorizationError(f"User '{coordinator_id}' has no active coordination bindings.")
```

Troque para:

```python
        else:
            raise ScopeAuthorizationError(f"User '{coordinator_id}' has no active coordination bindings.")
```

- [ ] **Step 6: Rode para confirmar que passam**

Run: `.venv/bin/python -m pytest tests/test_r0_coordination_dev_fallback_removal.py -v`
Expected: PASS, 3 testes. Verificado por execução antes de escrever este plano.

- [ ] **Step 7: Rode as suítes vizinhas**

Run: `.venv/bin/python -m pytest tests/test_coordination_portal.py tests/test_r0_coordinator_access_widening.py tests/test_r0_scope_validation_coordination.py tests/test_end_to_end_learning_flow.py -q`
Expected: zero falhas.

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/services/coordination_portal.py tests/test_r0_coordination_dev_fallback_removal.py
git commit -m "fix: remove atalhos de dev/teste de CoordinationPortalService

get_coordinator_authorized_scopes concedia is_global=True (com turmas
fabricadas) a qualquer identidade sem vinculo nenhum, em dois pontos
independentes: um atalho explicito de dev/teste no topo (formato
coordinator:*/director:*/coord_1/coord_a/admin:master), e uma condicao
'or not links' mais abaixo que nao checava o formato do id - qualquer
identidade sem vinculo que chegasse ali recebia o mesmo resultado.

verify_coordinator_access tinha o mesmo atalho de dev/teste guardando a
unica chamada que hoje alcanca get_coordinator_authorized_scopes sem
vinculo - removido tambem, para que nenhuma identidade sem vinculo passe
por nenhum dos dois pontos.

Nenhum teste existente dependia dos atalhos (confirmado por execucao antes
deste plano)."
```

---

## Task 2: Remove o fallback de `_fetch_students_in_classrooms`

**Files:**
- Modify: `src/agente_ia_edu/services/teacher_portal.py:668-694` (`_fetch_students_in_classrooms`)
- Test: `tests/test_teacher_portal.py` (arquivo já existe — adicionar um teste)

**Interfaces:**
- Consumes: nada de Task 1 — arquivo e função diferentes, independentes.
- Produces: `TeacherPortalService._fetch_students_in_classrooms` mantém a mesma assinatura pública (`list[str]`). Os cinco call-sites existentes (`get_teacher_dashboard`, `get_classroom_detail`, `search_students_in_scope`, `get_coordination_dashboard` via `self.teacher_portal_service`, e outro dentro do próprio arquivo) não mudam — todos já tratam lista vazia como "sem alunos", não como erro.

- [ ] **Step 1: Escreva o teste que falha**

`tests/test_teacher_portal.py` já existe, com uma única classe `TestTeacherPortal(unittest.IsolatedAsyncioTestCase)`, `asyncSetUp` criando `self.engine`/`self.session_factory` (sqlite in-memory), e `School`, `uuid4` (de `from uuid import uuid4`), `StudentContentMastery`, `AdminRole`, `AdminScopeType`, `PlatformAdminService`, `TeacherPortalService` já importados no topo do arquivo — não adicione nenhum import novo. Adicione este método à classe `TestTeacherPortal`, depois do último teste existente:

```python
    async def test_fetch_students_in_classrooms_returns_empty_not_other_schools_data(self):
        """When the school-scoped query finds zero students, the function
        must return [] - not fall through to an unfiltered query across
        every school's StudentContentMastery rows."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school_a = School(id=uuid4(), code="SCH_MASTERY_A", name="Escola A Mastery")
            school_b = School(id=uuid4(), code="SCH_MASTERY_B", name="Escola B Mastery")
            session.add_all([school_a, school_b])
            await session.commit()

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="student:alice-A",
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )
            session.add(StudentContentMastery(
                external_identity_id="student:alice-A",
                content_node_id=uuid4(),
                mastery_score=80.0,
            ))
            await session.commit()

            portal = TeacherPortalService(session, None, None, None)
            students = await portal._fetch_students_in_classrooms(school_b.id, [])

        self.assertEqual(students, [])
```

- [ ] **Step 2: Rode para confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_teacher_portal.py::TestTeacherPortal::test_fetch_students_in_classrooms_returns_empty_not_other_schools_data -v`
Expected: FALHA (`AssertionError: ['student:alice-A'] != []`). Verificado por execução antes de escrever este plano (probe equivalente, não este teste exato — mas a mesma asserção contra o mesmo código).

- [ ] **Step 3: Remova o fallback**

Em `src/agente_ia_edu/services/teacher_portal.py`, dentro de `_fetch_students_in_classrooms`, o corpo atual termina assim:

```python
        res = await self.session.execute(stmt)
        students = list(res.scalars().all())

        if not students:
            # Fallback for test/dev environment
            stmt_mastery = select(StudentContentMastery.external_identity_id).distinct()
            res_m = await self.session.execute(stmt_mastery)
            students = list(res_m.scalars().all())

        return students if students else ["student:alice", "student:bob"]
```

Troque para:

```python
        res = await self.session.execute(stmt)
        return list(res.scalars().all())
```

- [ ] **Step 4: Rode para confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_teacher_portal.py -v`
Expected: PASS, todos os testes do arquivo (o existente + o novo). Verificado por execução antes de escrever este plano.

- [ ] **Step 5: Rode as suítes vizinhas**

Run: `.venv/bin/python -m pytest tests/test_coordination_portal.py tests/test_r0_teacher_scope_cross_school.py tests/test_r0_scope_validation_teacher.py tests/test_end_to_end_learning_flow.py -q`
Expected: zero falhas.

- [ ] **Step 6: Rode o gate inteiro da fase**

Run: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py tests/test_coordination_portal.py tests/test_teacher_portal.py tests/test_teaching_context.py tests/test_end_to_end_learning_flow.py -q`
Expected: zero falhas, mesma contagem do baseline (219 + os 4 testes novos desta onda = 223 passed, 87 subtests).

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/services/teacher_portal.py tests/test_teacher_portal.py
git commit -m "fix: remove fallback de _fetch_students_in_classrooms que vazava outras escolas

Quando a busca por alunos vinculados a school_id/classrooms nao achava
ninguem, a funcao caia numa segunda busca SEM filtro nenhum de escola sobre
StudentContentMastery inteira, e so devolvia dado fabricado se essa
segunda busca tambem viesse vazia. Provado ao vivo: identidade pedindo a
escola B, que nao tem alunos, recebeu o aluno real da escola A.

O primeiro nivel da funcao ja filtra por school_id corretamente - o
problema era so o fallback que ignorava esse filtro quando o resultado
vinha vazio. Sem ele, lista vazia significa 'nenhum aluno', como os
chamadores ja tratam hoje."
```

---

## Depois da última tarefa

Rode o gate desta fase e a suíte completa contra um banco descartável, comparando com o baseline da onda 4 (2011 passed, 4 skipped, 448 subtests, 13 erros ambientais pré-existentes). Esta onda não deveria mover o número além dos 4 testes que ela mesma acrescenta.

## O que fica para a próxima onda de 3C

Com `coordination_portal.py` fechado, os pontos que restam do padrão "acesso sem checagem" são, pelo que se sabe hoje: as 12 rotas em `catalog.py`/`discovery.py`/`question_bank.py` com `identity` injetada e nunca lida (achado da revisão final da onda 3), a ausência de branch para papel `STUDENT` em `verify_teacher_classroom_scope`, e o fallback de dado fabricado (não é bypass de autorização, é dado de demonstração) em `coordination_portal.py` por volta da linha 598-609 quando nenhum professor real é encontrado no escopo do coordenador.

Com isso, o passo 5 do §7 da spec (autorização) fica, pela primeira vez nesta fase, sem nenhum atalho conhecido de "sem vínculo = acesso liberado" em nenhuma das funções de `teaching_context.py`, `teacher_portal.py` ou `coordination_portal.py`. A remoção do andaime `TURMA_3A`/`TURMA_3B` (passo 6) ainda depende da varredura das 12 rotas acima e de uma decisão explícita de que o passo 5 está de fato fechado — não é automática só porque este plano fechou os três arquivos centrais.
