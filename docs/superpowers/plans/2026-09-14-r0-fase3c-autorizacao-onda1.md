# R0 Fase 3C — Autorização (Onda 1): Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fechar, na função de autorização real onde ele vive, o bug de alargamento que a revisão final da Fase 3B encontrou e prometeu nomear aqui: um coordenador sem escopo específico declarado passa a ser **negado** por padrão, não liberado por acidente.

**Architecture:** Duas correções pequenas, no mesmo arquivo, no mesmo par de funções — `verify_coordinator_access` para de usar a verdade de um conjunto como pré-condição para checar, e `_resolve_scope_classrooms` para de cair no andaime `TURMA_3A`/`TURMA_3B` para quem tem escopo específico e vazio. Nenhuma das duas ganha lógica nova; as duas perdem a lógica que confundia "sem restrição" com "restrição vazia".

**Tech Stack:** Python 3.13, SQLAlchemy 2.x async, `unittest.IsolatedAsyncioTestCase` com `sqlite+aiosqlite:///:memory:` e `StaticPool`.

**Spec:** `docs/superpowers/specs/2026-09-13-r0-estrutura-academica-configuracao-design.md` — §3.2, §6, §7 (passo 5).

## Global Constraints

- Python `>=3.13,<3.14`. Nenhuma dependência nova.
- **Esta fase não cria migration, não cria consumidor novo, não toca `external_id_resolution.py`.** É correção pontual num bug já diagnosticado, não migração de mais um consumidor.
- **O andaime `TURMA_3A` não é removido.** Ele continua existindo como fallback para coordenador `is_global=True` — essa parte está correta e fica. Só o caminho que o alcançava por engano (`is_global=False` com conjunto vazio) fecha.
- Nenhuma coluna de credencial em lugar nenhum.
- Testes com `unittest.IsolatedAsyncioTestCase`, `sqlite+aiosqlite:///:memory:` e `StaticPool`.
- `tests/conftest.py` monta `DATABASE_URL` a partir do `.env` quando ela não está exportada.
- **Nunca aponte `DATABASE_URL` para `agente_ia_edu`** — banco de desenvolvimento, compartilhado. **A suíte completa derruba o banco que `DATABASE_URL` nomeia.**
- Código e comentários em inglês; mensagem de commit em português, como o `git log`.
- Gate desta fase: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py tests/test_coordination_portal.py tests/test_teacher_portal.py -q`.

## O que "autorização" significa de verdade neste repositório, e por que isso muda o escopo

Antes de escrever este plano, verifiquei quem de fato chama `AuthorizationService.require_scope` e `require_discipline`, em `services/authorization.py` — o módulo com o nome óbvio para "autorização, sozinha, por último" da §7.

```
grep -rn '\.require_scope(' src/agente_ia_edu | grep -v 'def require_scope'
```

devolve **nada**. Zero chamadores, o mesmo achado que a revisão final da Fase 2 já tinha feito sobre `require_discipline`. `resolve_context`, esse sim, é chamado de verdade (6 arquivos de rota, 7 instanciam `AuthorizationService`) — mas ele só monta o contexto autenticado e carrega o `scope_external_id` adiante como string; não compara nada contra recurso nenhum.

A decisão de "pode ou não pode" — a autorização de fato, no sentido que a §3.2 teme quando diz *"um erro ali não quebra: mostra dado errado para a pessoa errada"* — mora nas mesmas funções que a Fase 2 e a Fase 3B já identificaram como autorização disfarçada de outra coisa: `verify_coordinator_access` e `_resolve_scope_classrooms` em `coordination_portal.py`, `verify_teacher_classroom_scope` em `teaching_context.py`, `verify_student_access` em `teacher_portal.py`, `_is_question_visible` em `knowledge.py`, e outras.

**Esta onda escolhe uma só, e a razão é evidência, não conveniência.** A revisão final da Fase 3B executou o bug ao vivo em `verify_coordinator_access` e `_resolve_scope_classrooms`: um coordenador com um vínculo obsoleto perdia `{"TURMA-VELHA"}` (nega tudo real) e virava `set()` (concede tudo, inclusive o andaime). A correção daquela onda impediu a *filtragem* de produzir esse conjunto vazio a partir de um vínculo real — mas o bug em si, o de ler conjunto vazio como ausência de restrição, sempre morou aqui, nestas duas funções, e continua alcançável por um caminho mais antigo que a filtragem: um coordenador ou diretor cujos vínculos simplesmente não incluem nenhum de nível `CLASSROOM` (só `SEGMENT`, digamos) nunca preenche `allowed_classrooms`, e o conjunto chega vazio nestas funções sem passar pelo resolvedor nenhuma vez.

**Verifiquei o bug executando, na forma exata em que ele se manifesta hoje**, antes de escrever este plano:

```
coord-segment-only: vinculo SO de SEGMENT, nenhum de CLASSROOM
  verify_coordinator_access(classroom_id="QUALQUER-TURMA")
  ANTES desta correcao: retorna True — concede acesso a uma turma que o
    coordenador nunca declarou, porque "and scopes['allowed_classrooms']"
    e falso e o if inteiro nunca roda.
  DEPOIS desta correcao: ScopeAuthorizationError — negado, corretamente.
```

`_resolve_scope_classrooms` tem o mesmo padrão, só que devolvendo lista em vez de levantar: `if scopes["allowed_classrooms"]:` também é falso para conjunto vazio, e cai para "todas as turmas da escola, e se não houver nenhuma, `TURMA_3A`/`TURMA_3B`" — o mesmo alargamento, num caminho que constrói *views* em vez de negar ações.

**`teacher_portal.py::verify_student_access` não tem este bug**, e verifiquei isso lendo o código antes de escrever este plano em vez de assumir simetria: ela testa `link.scope_external_id in authorized_classrooms` diretamente — um `in` contra conjunto vazio já é `False` por definição, sem gate de verdade nenhum na frente. Não entra nesta onda porque não há nada para corrigir nela.

**Tudo o resto fica de fora desta onda, nomeado:** `teaching_context.py::verify_teacher_classroom_scope`, `knowledge.py::_is_question_visible`, `question_governance.py`, `reception.py`, `teacher_materials.py`, `assessments.py`, `study_session.py` — as mesmas funções de autorização real que a Fase 3B já tinha excluído da onda dela, pelo mesmo motivo. Nenhuma foi lida linha a linha para decidir se carrega o mesmo bug; é trabalho da próxima onda de 3C, não desta. A remoção do andaime `TURMA_3A` (passo 6 da §7) e o fim da transição (passo 7) continuam esperando essa migração completa — não fazem sentido antes dela, porque o passo 6 exige que nada mais depende do fallback, e isso só é verdade depois que todas as funções de autorização real tiverem sido corrigidas.

---

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/agente_ia_edu/services/coordination_portal.py` (**modificar**) | `verify_coordinator_access` para de pular a checagem quando o conjunto é vazio; `_resolve_scope_classrooms` para de alargar para "todas as turmas" quando o coordenador tem escopo específico e vazio. |
| `tests/test_r0_coordinator_access_widening.py` (**criar**) | Prova o bug na forma exata em que a revisão final o encontrou, e prova o fechamento. |

---

### Task 1: `verify_coordinator_access` não concede por conjunto vazio

**Files:**
- Modify: `src/agente_ia_edu/services/coordination_portal.py:242-260`
- Test: `tests/test_r0_coordinator_access_widening.py` (criar)

**Interfaces:**
- Consumes: nada novo — usa `get_coordinator_authorized_scopes`, que já existe.
- Produces: nada novo — `verify_coordinator_access` mantém assinatura e comportamento de exceção.

- [ ] **Step 1: Escreva o teste que falha**

Crie `tests/test_r0_coordinator_access_widening.py`:

```python
# tests/test_r0_coordinator_access_widening.py
"""The exact widening bug the Fase 3B final review found and executed live.

A coordinator whose links never include a CLASSROOM-level scope never
populates allowed_classrooms; the set arrives empty at verify_coordinator_access
without ever passing through the resolver. Before this fix, the guard
`if classroom_id and scopes["allowed_classrooms"] and classroom_id not in ...`
treats that empty set the same as "nothing to check" and grants access to
anything. After it, an empty set of a coordinator who is not global correctly
denies everything specific.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.coordination_portal import CoordinationPortalService
from agente_ia_edu.services.teaching_context import ScopeAuthorizationError


class CoordinatorAccessWideningTests(unittest.IsolatedAsyncioTestCase):
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

    async def test_a_coordinator_with_only_a_segment_link_is_denied_any_classroom(self):
        """The reported shape exactly: no CLASSROOM-scoped link at all, so
        allowed_classrooms is empty from the start - not from filtering."""
        async with self.session_factory() as session:
            school = await self._school(session, "1")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-segment-only",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SEGMENT,
                school_id=school.id,
                scope_external_id="SEG-1",
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            with self.assertRaises(ScopeAuthorizationError):
                await portal.verify_coordinator_access(
                    coordinator_id="coord-segment-only",
                    school_id=school.id,
                    classroom_id="QUALQUER-TURMA",
                )

    async def test_the_same_coordinator_is_denied_any_grade_and_any_unit_too(self):
        """The bug repeats identically for grade_level and unit_id - same guard
        shape, same fix, in the same function."""
        async with self.session_factory() as session:
            school = await self._school(session, "2")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-segment-only-2",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SEGMENT,
                school_id=school.id,
                scope_external_id="SEG-2",
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            with self.assertRaises(ScopeAuthorizationError):
                await portal.verify_coordinator_access(
                    coordinator_id="coord-segment-only-2",
                    school_id=school.id,
                    grade_level="QUALQUER-SERIE",
                )
            with self.assertRaises(ScopeAuthorizationError):
                await portal.verify_coordinator_access(
                    coordinator_id="coord-segment-only-2",
                    school_id=school.id,
                    unit_id="QUALQUER-UNIDADE",
                )

    async def test_a_coordinator_with_a_real_classroom_scope_still_works(self):
        """The fix must not deny what the coordinator actually has - only what
        they never declared."""
        async with self.session_factory() as session:
            school = await self._school(session, "3")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-with-classroom",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA-REAL",
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            allowed = await portal.verify_coordinator_access(
                coordinator_id="coord-with-classroom",
                school_id=school.id,
                classroom_id="TURMA-REAL",
            )
        self.assertTrue(allowed)

    async def test_a_global_coordinator_is_unaffected(self):
        """is_global short-circuits before either guard runs - unchanged."""
        async with self.session_factory() as session:
            school = await self._school(session, "4")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-global",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school.id,
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            allowed = await portal.verify_coordinator_access(
                coordinator_id="coord-global",
                school_id=school.id,
                classroom_id="QUALQUER-TURMA",
            )
        self.assertTrue(allowed)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rode para ver falhar**

Run: `.venv/bin/python -m pytest tests/test_r0_coordinator_access_widening.py -q`
Expected: FAIL — os dois primeiros testes não levantam `ScopeAuthorizationError` (o `if` inteiro é pulado, a função devolve `True`). Os dois últimos já passam hoje.

- [ ] **Step 3: Implemente a correção**

Em `src/agente_ia_edu/services/coordination_portal.py`, substitua o bloco de três checagens em `verify_coordinator_access` (logo após `if scopes["is_global"]: return True`):

```python
        if classroom_id and classroom_id not in scopes["allowed_classrooms"]:
            raise ScopeAuthorizationError(
                f"Coordinator '{coordinator_id}' is not authorized for classroom '{classroom_id}' in school '{school_id}'."
            )

        if grade_level and grade_level not in scopes["allowed_grades"]:
            raise ScopeAuthorizationError(
                f"Coordinator '{coordinator_id}' is not authorized for grade '{grade_level}' in school '{school_id}'."
            )

        if unit_id and unit_id not in scopes["allowed_units"]:
            raise ScopeAuthorizationError(
                f"Coordinator '{coordinator_id}' is not authorized for unit '{unit_id}' in school '{school_id}'."
            )
```

A mudança é remover `scopes["allowed_..."] and` de cada condição — a checagem de posse do conjunto não deve mais decidir se o `if` roda; ela nunca deveria ter decidido isso, já que a essa altura `is_global` já é sabidamente `False`.

- [ ] **Step 4: Rode para ver passar**

Run: `.venv/bin/python -m pytest tests/test_r0_coordinator_access_widening.py -q`
Expected: PASS, 4 testes.

- [ ] **Step 5: Rode os testes existentes de coordenação**

Run: `.venv/bin/python -m pytest tests/test_coordination_portal.py -q`
Expected: zero falhas. `test_15_16_17_18_multi_tenant_scope_isolation_and_unauthorized_blocking` já exercita o caminho de conjunto não-vazio negando corretamente (coordenador só com `TURMA_3A` tentando `TURMA_3B`) — confirme que continua passando.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/coordination_portal.py tests/test_r0_coordinator_access_widening.py
git commit -m "fix: coordenador sem escopo de turma declarado deixa de ser liberado por acidente

verify_coordinator_access usava a verdade do proprio conjunto de permissoes
como pre-condicao para checar contra ele. Um coordenador cujos vinculos nunca
incluem nivel CLASSROOM chega com o conjunto vazio sem passar pelo
resolvedor — e o if inteiro era pulado, concedendo acesso a qualquer turma.
Achado e executado ao vivo pela revisao final da Fase 3B; prometido aqui."
```

---

### Task 2: `_resolve_scope_classrooms` não alarga para o andaime quando o escopo é específico e vazio

**Files:**
- Modify: `src/agente_ia_edu/services/coordination_portal.py:200-212`
- Test: `tests/test_r0_coordinator_access_widening.py` (acrescentar)

**Interfaces:**
- Consumes: nada novo.
- Produces: nada novo — `_resolve_scope_classrooms` mantém assinatura e tipo de retorno (`list[str]`).

- [ ] **Step 1: Escreva o teste que falha**

Acrescente ao final de `tests/test_r0_coordinator_access_widening.py`, dentro da mesma classe (antes do `if __name__ == "__main__":`):

```python
    async def test_a_coordinator_with_only_a_segment_link_sees_no_classrooms(self):
        """The read-side twin of Task 1's fix: instead of raising, this one
        returns a list - and an empty, specific scope must return an empty
        list, not every classroom in the school plus the TURMA_3A scaffold."""
        async with self.session_factory() as session:
            school = await self._school(session, "5")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-segment-only-3",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SEGMENT,
                school_id=school.id,
                scope_external_id="SEG-3",
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            classrooms = await portal._resolve_scope_classrooms(
                "coord-segment-only-3", school.id
            )
        self.assertEqual(classrooms, [])

    async def test_a_global_coordinator_still_sees_every_classroom(self):
        """is_global must keep reaching the TeachingLesson query and its
        TURMA_3A/3B fallback - that half of the function is correct and stays."""
        async with self.session_factory() as session:
            school = await self._school(session, "6")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-global-2",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school.id,
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            classrooms = await portal._resolve_scope_classrooms(
                "coord-global-2", school.id
            )
        self.assertEqual(classrooms, ["TURMA_3A", "TURMA_3B"])
```

- [ ] **Step 2: Rode para ver falhar**

Run: `.venv/bin/python -m pytest tests/test_r0_coordinator_access_widening.py -q`
Expected: o primeiro dos dois novos testes falha — hoje devolve `["TURMA_3A", "TURMA_3B"]` em vez de `[]`. O segundo já passa (comportamento correto, que não pode mudar).

- [ ] **Step 3: Implemente a correção**

Em `src/agente_ia_edu/services/coordination_portal.py`, substitua o corpo de `_resolve_scope_classrooms`:

```python
    async def _resolve_scope_classrooms(
        self,
        coordinator_id: str,
        school_id: uuid.UUID,
    ) -> list[str]:
        """Resolves classroom_ids in the coordinator's authorized scope (coordinator-scoped, not teacher-scoped)."""
        scopes = await self.get_coordinator_authorized_scopes(coordinator_id, school_id)
        if scopes["is_global"]:
            stmt_c = select(TeachingLesson.classroom_id).where(TeachingLesson.school_id == school_id).distinct()
            res_c = await self.session.execute(stmt_c)
            return list(res_c.scalars().all()) or ["TURMA_3A", "TURMA_3B"]
        return list(scopes["allowed_classrooms"])
```

A mudança: o `TeachingLesson`-query-mais-andaime só roda quando `is_global` é verdadeiro — não mais quando `allowed_classrooms` simplesmente calha de estar vazio. Um coordenador com escopo específico e vazio agora devolve lista vazia, refletindo com precisão que ele não tem turma nenhuma autorizada.

- [ ] **Step 4: Rode para ver passar**

Run: `.venv/bin/python -m pytest tests/test_r0_coordinator_access_widening.py -q`
Expected: PASS, 6 testes.

- [ ] **Step 5: Rode o gate da fase inteiro**

Run: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py tests/test_coordination_portal.py tests/test_teacher_portal.py -q`
Expected: zero falhas.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/coordination_portal.py tests/test_r0_coordinator_access_widening.py
git commit -m "fix: escopo especifico e vazio nao alarga mais para todas as turmas

_resolve_scope_classrooms so cai no andaime TURMA_3A/3B para coordenador
is_global de verdade. Coordenador com escopo especifico (mesmo vazio) devolve
so o que tem — hoje, nada, em vez de todas as turmas da escola."
```

---

## Depois da última tarefa

Rode o gate desta fase e a suíte completa contra um banco descartável, comparando com o baseline no commit em que esta fase começou (`b547f23`). Esta fase é pequena e não deveria mover o número da suíte completa além dos testes que ela mesma acrescenta.

## O que fica para a próxima onda de 3C

Registrado para não ser redescoberto do zero: `teaching_context.py::verify_teacher_classroom_scope`, `knowledge.py::_is_question_visible`, `question_governance.py`, `reception.py`, `teacher_materials.py`, `assessments.py`, `study_session.py` — nenhuma foi lida linha a linha para saber se carrega o mesmo padrão de "conjunto/contexto vazio lido como ausência de restrição". A remoção do andaime `TURMA_3A` (passo 6 da §7) e o fim da transição — `external_id` que não resolve virar erro em vez de `None` (passo 7) — continuam esperando essa migração terminar; não fazem sentido antes, porque remover o andaime exige que nada mais dependa dele, e isso só fica verdade depois que toda função de autorização real tiver sido corrigida ou migrada. `services/authorization.py::require_scope` e `require_discipline` seguem sem nenhum chamador — capacidade construída, nunca ligada — e continuam fora do escopo até que alguém decida ligá-los ou removê-los; nenhuma decisão foi tomada aqui sobre qual dos dois.
