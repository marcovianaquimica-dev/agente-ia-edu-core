# R0 Fase 3B — Consumidores de Leitura (Onda 1): Plano de Implementação

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ligar o resolvedor de `external_id` (Fase 3A) aos dois consumidores que motivaram `resolve_many` — `coordination_portal.py` e `teacher_portal.py` — para que um vínculo (`UserSchoolLink`) com código de escopo obsoleto, de outra escola, ou com erro de digitação deixe de conceder acesso silenciosamente, **sem** derrubar o acesso de nenhuma escola cuja hierarquia acadêmica ainda não foi populada.

**Architecture:** Cada consumidor continua devolvendo os mesmos conjuntos de strings que devolve hoje — nada a jusante muda de tipo. A mudança: antes de devolver, cada conjunto de códigos é checado contra a hierarquia real da escola, mas **só entra em vigor escola por escola, nível por nível, quando aquele nível já tem pelo menos uma linha real**. Enquanto a Fase 1 não foi seguida de uma migração de dado real para uma escola, esta fase não tira acesso de ninguém nela.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x async, `unittest.IsolatedAsyncioTestCase` com `sqlite+aiosqlite:///:memory:` e `StaticPool`.

**Spec:** `docs/superpowers/specs/2026-09-13-r0-estrutura-academica-configuracao-design.md` — §3.2, §6, §7 (passo 4).

## Global Constraints

- Python `>=3.13,<3.14`. Nenhuma dependência nova.
- **Esta fase não cria migration.**
- **O andaime `TURMA_3A` não é tocado.** Sua remoção é o passo 6 da §7, amarrado ao passo 5 (autorização), que é a Fase 3C. As linhas `or ["TURMA_3A", "TURMA_3B"]` ficam byte a byte como estão.
- **Nenhum tipo de retorno muda.** As duas funções migradas continuam devolvendo `dict`/`list` de strings.
- **Uma escola sem hierarquia populada num nível não perde acesso naquele nível.** Esta é a regra que governa a fase inteira — ver a seção abaixo, que registra por que ela existe e o que aconteceu quando eu testei o desenho sem ela.
- Nenhuma coluna de credencial em lugar nenhum.
- Testes com `unittest.IsolatedAsyncioTestCase`, `sqlite+aiosqlite:///:memory:` e `StaticPool`.
- `tests/conftest.py` monta `DATABASE_URL` a partir do `.env` quando ela não está exportada.
- **Nunca aponte `DATABASE_URL` para `agente_ia_edu`** — banco de desenvolvimento, compartilhado. **A suíte completa derruba o banco que `DATABASE_URL` nomeia.**
- Código e comentários em inglês; mensagem de commit em português, como o `git log`.
- Gate desta fase: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py tests/test_coordination_portal.py tests/test_teacher_portal.py -q`.

## O desenho ingênuo, testado e descartado antes de qualquer despacho

A primeira versão deste plano validava cada código de escopo contra a hierarquia e descartava o que não resolvesse, sem checar mais nada. Escrevi a implementação, apliquei-a temporariamente ao código real e rodei a suíte existente antes de despachar qualquer tarefa — e ela quebrou três testes:

```
FAILED tests/test_coordination_portal.py::...::test_15_16_17_18_multi_tenant_scope_isolation_and_unauthorized_blocking
FAILED tests/test_teacher_portal.py::...::test_16_list_teacher_classrooms
FAILED tests/test_teacher_portal.py::...::test_17_student_detail_authorized_and_unauthorized
```

A causa: `tests/test_teacher_portal.py`'s `_seed_data` cria um `UserSchoolLink` real para `prof_mendes` com `scope_type=CLASSROOM, scope_external_id="TURMA_3A"` — mas não semeia nenhuma linha em `classes` para essa escola, porque a hierarquia acadêmica da Fase 1 é nova e nenhum dado de produção foi migrado para dentro dela ainda. `TURMA_3A` aqui não é o andaime; é um vínculo de verdade cujo código nunca teve um `Class` correspondente. Descartá-lo por não resolver é **exatamente o erro que a §6 já preveniu uma vez, na Fase 2, só que na direção oposta**: lá, ausência de restrição tinha que significar acesso total, para não trancar toda escola existente no dia do deploy. Aqui, ausência de *dado migrado* teria que significar "não valide ainda", pela mesma razão — senão toda escola cuja hierarquia não foi populada perde acesso a tudo no dia em que esta fase for ao ar.

A correção: **`ExternalIdResolver` ganha um método que verifica se a escola tem alguma linha naquele nível da hierarquia.** Só quando a resposta é sim a validação entra em vigor para aquele nível, naquela escola. Enquanto for não, o código passa como está hoje — sem filtro, sem mudança de comportamento.

## Reconciliação com o parágrafo final da §7 (registrada na revisão final de branch, achado 2)

A §7 fecha dizendo: *"**Durante a transição**, um `external_id` que não resolve devolve `None` e o chamador segue pelo caminho antigo. A mudança desse regime é o passo 7, não um efeito colateral de nenhum passo anterior."* Lida sozinha, essa frase parece proibir o que esta fase (passo 4) faz: numa escola com hierarquia populada, um código que não resolve **não** segue pelo caminho antigo — ele sai do conjunto de permissões.

A reconciliação é que os dois parágrafos falam de momentos diferentes. "O chamador segue pelo caminho antigo" descreve um consumidor **ainda não migrado**: enquanto ninguém chama o resolvedor, uma resolução falha não muda nada, e é por isso que a ordem da §7 pode ser incremental sem quebrar a transição. Migrar um consumidor é, por definição, o passo em que aquele chamador deixa de seguir o caminho antigo — se migrar não mudasse comportamento nenhum, os passos 3 a 6 não existiriam. O que o passo 7 reserva para si, e que **esta fase não faz**, é transformar a falha de resolução em **erro**: aqui ela nunca levanta exceção, nunca muda tipo de retorno, e só age onde `has_any_entities` prova que a hierarquia daquele nível já existe. Todos os outros consumidores continuam intocados, exatamente como a frase da §7 descreve.

Duas cercas mantêm essa antecipação dentro do que a §7 autoriza, e ambas estão testadas: a guarda de hierarquia populada (nenhuma escola não migrada perde nada) e a regra de que filtrar nunca esvazia um conjunto que tinha entradas (`_resolved` e o bloco equivalente do professor, corrigidos depois da revisão final — ver o achado Critical). Juntas, esta fase nunca concede mais nem nega mais do que encontrou na entrada, exceto no caso estreito e desejado: um código obsoleto ao lado de códigos válidos, numa escola cuja hierarquia já foi backfillada.

## Por que só dois consumidores, quando `scope_external_id` aparece em 27 arquivos

> **Correção da revisão final de branch (achado 6).** A contagem original dizia "26"; `grep -rln --include='*.py' scope_external_id src` devolve **32** arquivos, **27** fora de `db/models`. E quatro deles ficavam sem classificação nenhuma nesta seção, apesar da promessa de exaustividade: `api/routes/catalog.py`, `api/routes/diagnostic.py`, `api/routes/exercise_lists.py` e `api/routes/student.py`. Estão nomeados na lista abaixo.

Antes de escrever este plano, li o código real dos arquivos que mencionam `scope_external_id` fora de `db/models` e `.js`. A lista nua sugeria uma divisão simples — "tudo exceto `services/authorization.py`" — e essa divisão está errada.

**A maior parte desses arquivos não filtra conteúdo: decide quem pode ver o quê.** `teacher_portal.py::verify_student_access`, `teaching_context.py::verify_teacher_classroom_scope` (que levanta `ScopeAuthorizationError`, um `PermissionError`), `coordination_portal.py::get_coordinator_authorized_scopes` em si, `knowledge.py::_is_question_visible`, `question_governance.py` — todos comparam `scope_external_id` para conceder ou negar algo. Isso é autorização, mesmo fora de um arquivo chamado `authorization.py`, e carrega o mesmo risco que a §3.2 atribui à autorização central: *"um erro ali não quebra: mostra dado errado para a pessoa errada."*

A leitura que sustenta a ordem da §7 não é "arquivo `authorization.py` é arriscado, o resto não". É que **o portão central** — `AuthorizationService`, por onde toda requisição passa — é o ponto de maior alavancagem, e por isso fica por último, depois que o resolvedor tiver sido provado em pontos de menor alavancagem primeiro.

`coordination_portal.py::get_coordinator_authorized_scopes` e `teacher_portal.py::get_teacher_authorized_classrooms` são, na prática, também autorização — decidem quais turmas um coordenador ou professor pode ver. Mas são **locais**: um erro numa delas afeta um recurso (a lista de turmas de um portal), não toda requisição do sistema, e — o que decide — são exatamente os dois pontos que a docstring do próprio `resolve_many` cita como razão de existir (`external_id_resolution.py:180-183`). Migrá-las agora, com a guarda de "hierarquia populada" acima, prova o resolvedor contra dado real, com o menor raio de explosão possível, antes de tocar no portão central.

**Tudo mais fica de fora desta onda, nomeado:**

- `teaching_context.py`, `knowledge.py`, `question_governance.py`, `reception.py`, `teacher_materials.py` — decisões de autorização/visibilidade que levantam exceção ou negam acesso. Mesma classe de risco que `services/authorization.py`; migram junto da Fase 3C ou em onda própria antes dela.
- `assessments.py` (rota e serviço), `study_session.py` — já têm `scope_type` alimentado por texto de cliente sem `CHECK` (registrado na Fase 3A). Precisam de decisão de validação na fronteira antes de qualquer resolução fazer sentido.
- `attempts.py`, `admin.py`, `invitation.py`, `activity_assignment_store.py`, `adaptive_learning_path.py`, `study_search.py` — não lidos linha a linha para este plano; ficam para a próxima onda de 3B.
- `api/schemas/*.py`, `identity.py` — carregam o campo, não decidem nada com ele. Não são consumidores no sentido da §7.
- `api/routes/catalog.py` (linhas 505, 570, 607) — repassa `context.scope_external_id` adiante como `requester_scope_external_id`; quem decide é o serviço a jusante, não a rota. Migra junto do serviço que ela alimenta.
- `api/routes/diagnostic.py:58` — passa o código adiante só quando `scope_type == "CLASSROOM"`, senão `None`. Mesma situação: repasse, não decisão.
- `api/routes/exercise_lists.py:44` — `scope_external_id=str(auth_context.school_id)`, um **UUID de escola** dentro do campo, com `scope_type="SCHOOL"` (hoje `NOT_APPLICABLE` no resolvedor, portanto inofensivo). Registrado como precedente de formato: o campo não carrega só código de SIS.
- `api/routes/student.py:156-158` — **atenção para a onda 2**: `requester_scope_external_id` pode ser uma **tupla** de identificadores (student_id, classroom_id, school_code, institution_code, institution_id), reduzida a uma string só quando sobra um elemento. Ela vai para `StudySearchService.search`, e `study_search.py` já está nomeado para a próxima onda. `resolve_many` aceita `Iterable[str | None]`; uma tupla aninhada aqui resolveria como se fosse **um** código, errado. Quem planejar a onda 2 precisa decidir a forma antes de migrar `study_search.py`.

## Fatos verificados por execução antes de escrever este plano

- `ExternalIdResolver.resolve_many(school_id, scope_type, external_ids) -> dict[str | None, ScopeResolution]` já existe (Fase 3A), devolve um valor por código passado, chaveado pelo código como foi passado.
- `AdminScopeType` tem `PLATFORM, SCHOOL, UNIT, SEGMENT, GRADE_LEVEL, CLASSROOM` — os mesmos seis valores que o `CHECK` de `user_school_links` permite.
- `PlatformAdminService.link_user_to_school(performed_by_external_id, external_user_id, role, scope_type, school_id, scope_external_id)` é o único caminho de escrita de `UserSchoolLink` usado nos testes existentes; normaliza `scope_type` para maiúsculas e `scope_external_id` com `.strip()`. Requer que `School` já exista.
- `CoordinationPortalService(session, knowledge_service, teaching_context_service, teacher_portal_service, recommendation_engine, performance_policy=None)` aceita `None` nos quatro colaboradores quando só se testa a função que não os usa — confirmado rodando.
- `TeacherPortalService(session, knowledge_service, teaching_context_service, recommendation_engine, video_engine=None, performance_policy=None)` — **quatro** posicionais, não três; confirmado só depois de tentar com três e ver `TypeError`.
- Em `coordination_portal.py`, os quatro conjuntos (`allowed_units`, `allowed_segments`, `allowed_grades`, `allowed_classrooms`) só são preenchidos com dado de vínculo real no retorno final da função (linhas 131-137); os outros dois retornos consultam `TeachingLesson` diretamente e nunca leem `scope_external_id` de vínculo algum — não precisam de mudança.
- Em `teacher_portal.py`, só o ramo `TEACHER` (linhas 182-190) constrói `authorized_classrooms` a partir de `link.scope_external_id`; o ramo `PLATFORM_ADMIN/DIRECTOR/COORDINATOR` (linhas 167-180) já consulta `TeachingLesson` e `UserSchoolLink` diretamente via SQL, sem passar pelos vínculos individuais, e fica fora desta onda.
- Rodei o desenho ingênuo (sem a guarda de hierarquia populada) contra a suíte real e ele quebrou `test_coordination_portal.py` e `test_teacher_portal.py` exatamente como descrito acima. Revertido antes de escrever este plano.

---

## Estrutura de arquivos

| Arquivo | Responsabilidade |
|---|---|
| `src/agente_ia_edu/services/external_id_resolution.py` (**modificar**) | Ganha `has_any_entities(school_id, scope_type) -> bool`: existe pelo menos uma linha real deste nível para esta escola? |
| `src/agente_ia_edu/services/coordination_portal.py` (**modificar**) | `get_coordinator_authorized_scopes` valida cada conjunto só onde a hierarquia já foi populada. |
| `src/agente_ia_edu/services/teacher_portal.py` (**modificar**) | `get_teacher_authorized_classrooms` faz o mesmo para o conjunto de turmas do professor. |
| `tests/test_r0_external_id_resolution.py` (**modificar**) | Cobre `has_any_entities`. |
| `tests/test_r0_scope_validation_coordination.py` (**criar**) | Prova, com dado semeado de verdade, que um código obsoleto some do conjunto quando a hierarquia existe — e sobrevive quando ela não existe. |
| `tests/test_r0_scope_validation_teacher.py` (**criar**) | O mesmo para o professor. |
| `tests/test_r0_fase3a_gate.py` (**modificar**) | Perde `test_the_resolver_has_no_consumer_yet` — a afirmação da Fase 3A que esta fase existe para violar. `test_the_resolver_works_at_all` fica. |

---

### Task 1: O resolvedor sabe dizer se uma escola já tem hierarquia num nível

**Files:**
- Modify: `src/agente_ia_edu/services/external_id_resolution.py`
- Test: `tests/test_r0_external_id_resolution.py` (modificar)

**Interfaces:**
- Consumes: `_model_for` (privado, já existe no módulo).
- Produces: `ExternalIdResolver.has_any_entities(school_id: uuid.UUID | str | None, scope_type: str | None) -> bool`.

- [ ] **Step 1: Escreva o teste que falha**

Acrescente ao final de `tests/test_r0_external_id_resolution.py`, dentro da classe `ExternalIdResolutionTests` (antes do `if __name__ == "__main__":`):

```python
    async def test_has_any_entities_is_false_for_an_unpopulated_school(self):
        """A school whose academic hierarchy was never migrated into R0's new
        tables - every school today, until someone backfills it - must read as
        unpopulated, not as broken."""
        async with self.session_factory() as session:
            resolver = ExternalIdResolver(session)
            found = await resolver.has_any_entities(SCHOOL_A, "CLASSROOM")
        self.assertFalse(found)

    async def test_has_any_entities_is_true_once_one_row_exists(self):
        async with self.session_factory() as session:
            await self._seed(session, SCHOOL_A)
            resolver = ExternalIdResolver(session)
            found = await resolver.has_any_entities(SCHOOL_A, "CLASSROOM")
        self.assertTrue(found)

    async def test_has_any_entities_does_not_see_another_schools_rows(self):
        async with self.session_factory() as session:
            await self._seed(session, SCHOOL_B)
            resolver = ExternalIdResolver(session)
            found = await resolver.has_any_entities(SCHOOL_A, "CLASSROOM")
        self.assertFalse(found)

    async def test_has_any_entities_is_false_for_platform_and_school(self):
        """PLATFORM and SCHOOL address no hierarchy level, so there is nothing
        to be populated - same NOT_APPLICABLE idea as resolve()."""
        async with self.session_factory() as session:
            resolver = ExternalIdResolver(session)
            for scope_type in ("PLATFORM", "SCHOOL"):
                with self.subTest(scope_type=scope_type):
                    found = await resolver.has_any_entities(SCHOOL_A, scope_type)
                    self.assertFalse(found)
```

- [ ] **Step 2: Rode para ver falhar**

Run: `.venv/bin/python -m pytest tests/test_r0_external_id_resolution.py -q`
Expected: FAIL com `AttributeError: 'ExternalIdResolver' object has no attribute 'has_any_entities'`

- [ ] **Step 3: Implemente**

Em `src/agente_ia_edu/services/external_id_resolution.py`, acrescente o método logo depois de `resolve_many` (antes do fechamento da classe):

```python
    async def has_any_entities(
        self, school_id: uuid.UUID | str | None, scope_type: str | None
    ) -> bool:
        """Whether this school has at least one real row at this hierarchy level.

        R0's hierarchy tables are additive and new: nothing migrated existing
        production data into them. A consumer that validates scope codes
        against these tables must check this first - filtering a level that
        was never populated would strip access from every school that has not
        been backfilled yet, which today is every school.
        """
        model = _model_for(scope_type)
        if model is None:
            return False
        result = await self.session.execute(
            select(model.id).where(model.school_id == school_id).limit(1)
        )
        return result.scalar_one_or_none() is not None
```

- [ ] **Step 4: Rode para ver passar**

Run: `.venv/bin/python -m pytest tests/test_r0_external_id_resolution.py -q`
Expected: PASS, 10 testes (6 já existentes + 4 novos).

- [ ] **Step 5: Rode o gate da fase**

Run: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py -q`
Expected: zero falhas.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/external_id_resolution.py tests/test_r0_external_id_resolution.py
git commit -m "feat: resolvedor diz se a escola ja tem hierarquia num nivel

Validar um nivel nunca populado tiraria acesso de toda escola que ainda nao
foi migrada — hoje, todas. As Tasks 2 e 3 usam isto antes de filtrar."
```

---

### Task 2: `coordination_portal.py` valida escopo onde a hierarquia já existe

**Files:**
- Modify: `src/agente_ia_edu/services/coordination_portal.py:98-137`
- Test: `tests/test_r0_scope_validation_coordination.py` (criar)

**Interfaces:**
- Consumes: `ExternalIdResolver.resolve_many`, `ExternalIdResolver.has_any_entities`, `ResolutionState` (Task 1 e Fase 3A).
- Produces: nada novo — `get_coordinator_authorized_scopes` mantém a assinatura e o formato de retorno atuais.

**Aviso, confirmado rodando esta task contra a suíte real antes de escrever o plano:** assim que `coordination_portal.py` importar o resolvedor, `tests/test_r0_fase3a_gate.py::test_the_resolver_has_no_consumer_yet` **vai falhar** — é o gate da Fase 3A, e a afirmação dele era verdadeira só até o fim daquela fase; o trabalho desta é exatamente violá-la. O Step 3 abaixo cuida disso antes de implementar, para que a suíte não fique vermelha entre um passo e outro.

- [ ] **Step 1: Escreva o teste que falha**

Crie `tests/test_r0_scope_validation_coordination.py`:

```python
# tests/test_r0_scope_validation_coordination.py
"""A stale or cross-school scope_external_id must stop granting access - but
only once the school's hierarchy has been populated at that level.

get_coordinator_authorized_scopes builds its allow-sets directly from
UserSchoolLink.scope_external_id, a free-text field nothing validates at
write time. This is the first of the two call sites resolve_many's own
docstring names as the reason it exists (external_id_resolution.py:180-183).

The naive version of this file - validate everything, unconditionally - broke
tests/test_teacher_portal.py::test_16_list_teacher_classrooms and two others,
because their fixtures link a real user to a real scope_external_id with no
matching Class/GradeLevel/... row, exactly the state of every school today
before its hierarchy is backfilled. The tests below cover both states on
purpose: populated (validate) and unpopulated (pass through unchanged).
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.db.models.academic import Class, GradeLevel, Segment, SchoolUnit, AcademicYear
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.coordination_portal import CoordinationPortalService


class CoordinatorScopeValidationTests(unittest.IsolatedAsyncioTestCase):
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

    async def _seed_hierarchy(self, session, school, code):
        unit = SchoolUnit(id=uuid.uuid4(), school_id=school.id, name="unit", external_id=f"UNIT-{code}")
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="segment", external_id=f"SEG-{code}")
        session.add_all([unit, segment])
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
            grade_level_id=grade.id, name="class", external_id=f"TURMA-{code}",
        )
        session.add(klass)
        await session.commit()

    async def test_a_stale_classroom_code_is_dropped_once_hierarchy_exists(self):
        async with self.session_factory() as session:
            school = await self._school(session, "1")
            await self._seed_hierarchy(session, school, "1")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-real",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA-1",
            )
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-real",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA-QUE-NAO-EXISTE-MAIS",
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            scopes = await portal.get_coordinator_authorized_scopes("coord-real", school.id)

        self.assertIn("TURMA-1", scopes["allowed_classrooms"])
        self.assertNotIn("TURMA-QUE-NAO-EXISTE-MAIS", scopes["allowed_classrooms"])

    async def test_every_code_survives_when_the_school_has_no_hierarchy_yet(self):
        """The exact scenario that broke the naive design: a real link, a
        school whose hierarchy tables are still empty. Nothing is dropped."""
        async with self.session_factory() as session:
            school = await self._school(session, "unmigrated")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="coord-legacy",
                role=AdminRole.COORDINATOR,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA_3A",
            )

            portal = CoordinationPortalService(session, None, None, None, None)
            scopes = await portal.get_coordinator_authorized_scopes("coord-legacy", school.id)

        self.assertIn("TURMA_3A", scopes["allowed_classrooms"])

    async def test_a_valid_code_of_every_level_survives(self):
        """The four levels - unit, segment, grade, classroom - each go through
        their own has_any_entities + resolve_many call. One test per level
        would be four files of the same shape; this proves all four at once,
        in a school whose hierarchy is fully populated."""
        async with self.session_factory() as session:
            school = await self._school(session, "2")
            await self._seed_hierarchy(session, school, "2")
            admin = PlatformAdminService(session)
            for scope_type, code in (
                (AdminScopeType.UNIT, "UNIT-2"),
                (AdminScopeType.SEGMENT, "SEG-2"),
                (AdminScopeType.GRADE_LEVEL, "GRADE-2"),
                (AdminScopeType.CLASSROOM, "TURMA-2"),
            ):
                await admin.link_user_to_school(
                    performed_by_external_id="setup",
                    external_user_id="coord-multi",
                    role=AdminRole.COORDINATOR,
                    scope_type=scope_type,
                    school_id=school.id,
                    scope_external_id=code,
                )

            portal = CoordinationPortalService(session, None, None, None, None)
            scopes = await portal.get_coordinator_authorized_scopes("coord-multi", school.id)

        self.assertEqual(scopes["allowed_units"], {"UNIT-2"})
        self.assertEqual(scopes["allowed_segments"], {"SEG-2"})
        self.assertEqual(scopes["allowed_grades"], {"GRADE-2"})
        self.assertEqual(scopes["allowed_classrooms"], {"TURMA-2"})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rode para ver falhar**

Run: `.venv/bin/python -m pytest tests/test_r0_scope_validation_coordination.py -q`
Expected: FAIL — `test_a_stale_classroom_code_is_dropped_once_hierarchy_exists` falha porque hoje o código obsoleto continua no conjunto. `test_every_code_survives_when_the_school_has_no_hierarchy_yet` já passa hoje (nada filtra ainda) — vai continuar passando depois; ele existe para travar o comportamento, não para virar de vermelho para verde.

- [ ] **Step 3: Aposente a afirmação da Fase 3A de que o resolvedor não tem consumidor**

Em `tests/test_r0_fase3a_gate.py`, remova o método `test_the_resolver_has_no_consumer_yet` (a classe `Fase3AGateTests` e o método `test_the_resolver_works_at_all` continuam de pé — só a checagem de ausência de consumidor sai). Ela provava uma afirmação verdadeira só até o fim da Fase 3A; o trabalho desta tarefa é exatamente ligar o primeiro consumidor, e um gate que reprova a fase seguinte por fazer o que a spec manda deixou de proteger coisa alguma. Substitua o parágrafo inicial do docstring do módulo (o que começa com `"""Proves this phase delivers a service and changes nothing else.`) por uma frase registrando que a checagem de ausência foi removida aqui, na Fase 3B, e por quê — para quem ler o arquivo depois não precisar reconstruir o raciocínio a partir do `git log`.

Rode `tests/test_r0_fase3a_gate.py -q` sozinho neste ponto, antes de tocar em `coordination_portal.py`: deve passar com 1 teste (`test_the_resolver_works_at_all`), confirmando que a remoção foi limpa antes de prosseguir.

- [ ] **Step 4: Implemente a validação**

Em `src/agente_ia_edu/services/coordination_portal.py`, acrescente o import no topo do arquivo, junto dos outros de `agente_ia_edu.services`:

```python
from agente_ia_edu.services.external_id_resolution import ExternalIdResolver, ResolutionState
```

Substitua o bloco final da função, de `if is_global or not links:` (linha 119) até o fim do `return` em `allowed_segments,` (linha 137), por:

```python
        if is_global or not links:
            stmt = select(TeachingLesson.classroom_id).where(TeachingLesson.school_id == school_id).distinct()
            res = await self.session.execute(stmt)
            classrooms = set(res.scalars().all()) or {"TURMA_3A", "TURMA_3B"}
            return {
                "is_global": True,
                "allowed_classrooms": classrooms,
                "allowed_grades": {"1ª Série", "2ª Série", "3ª Série"},
                "allowed_units": {"Unidade Principal"},
                "allowed_segments": {"Ensino Médio"},
            }

        resolver = ExternalIdResolver(self.session)
        allowed_units = await self._resolved(resolver, school_id, AdminScopeType.UNIT, allowed_units)
        allowed_segments = await self._resolved(resolver, school_id, AdminScopeType.SEGMENT, allowed_segments)
        allowed_grades = await self._resolved(resolver, school_id, AdminScopeType.GRADE_LEVEL, allowed_grades)
        allowed_classrooms = await self._resolved(resolver, school_id, AdminScopeType.CLASSROOM, allowed_classrooms)

        return {
            "is_global": False,
            "allowed_classrooms": allowed_classrooms,
            "allowed_grades": allowed_grades,
            "allowed_units": allowed_units,
            "allowed_segments": allowed_segments,
        }

    @staticmethod
    async def _resolved(
        resolver: "ExternalIdResolver",
        school_id: uuid.UUID,
        scope_type: str,
        codes: set[str],
    ) -> set[str]:
        """Keep only the codes that resolve - once this school's hierarchy
        actually has rows at this level.

        A link's scope_external_id is free text nothing validates at write
        time, and R0's hierarchy tables are new: no school's existing data
        was migrated into them yet. Filtering before that backfill happens
        would strip access from every school that has not been migrated -
        today, every school. has_any_entities is the guard against that;
        dropping what does not resolve, once it is safe to check, narrows an
        allow-set and never widens it.
        """
        if not codes:
            return codes
        if not await resolver.has_any_entities(school_id, scope_type):
            return codes
        resolutions = await resolver.resolve_many(school_id, scope_type, codes)
        return {
            code for code in codes
            if resolutions[code].state == ResolutionState.RESOLVED
        }
```

**Não mexa** nos dois blocos de retorno anteriores (o `if not links and (...)` no início da função, e o já mostrado `if is_global or not links:`) além de copiá-los como estão — nenhum dos dois lê `scope_external_id` de vínculo, então nenhum precisa de validação.

- [ ] **Step 5: Rode para ver passar**

Run: `.venv/bin/python -m pytest tests/test_r0_scope_validation_coordination.py -q`
Expected: PASS, 3 testes.

- [ ] **Step 6: Rode os testes existentes de coordenação**

Run: `.venv/bin/python -m pytest tests/test_coordination_portal.py -q`
Expected: zero falhas. `test_15_16_17_18_multi_tenant_scope_isolation_and_unauthorized_blocking` é o teste que a versão ingênua deste plano quebrou — confirme que ele passa agora.

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/services/coordination_portal.py tests/test_r0_scope_validation_coordination.py tests/test_r0_fase3a_gate.py
git commit -m "feat: coordenacao descarta escopo obsoleto onde a hierarquia ja existe

Um vinculo com scope_external_id obsoleto, de outra escola ou com erro de
digitacao concedia acesso as cegas. Agora e validado contra a hierarquia real
quando ela existe — e passa intacto quando a escola ainda nao foi migrada,
para nao tirar acesso de ninguem no dia do deploy.

Aposenta tambem o gate da Fase 3A que afirmava ausencia de consumidor: essa
afirmacao valia so ate o fim daquela fase, e o trabalho desta e viola-la."
```

---

### Task 3: `teacher_portal.py` valida turmas do professor

**Files:**
- Modify: `src/agente_ia_edu/services/teacher_portal.py:165-192`
- Test: `tests/test_r0_scope_validation_teacher.py` (criar)

**Interfaces:**
- Consumes: `ExternalIdResolver.resolve_many`, `ExternalIdResolver.has_any_entities`, `ResolutionState` (Task 1 e Fase 3A).
- Produces: nada novo — `get_teacher_authorized_classrooms` mantém a assinatura e o retorno atuais (`list[str]`).

- [ ] **Step 1: Escreva o teste que falha**

Crie `tests/test_r0_scope_validation_teacher.py`:

```python
# tests/test_r0_scope_validation_teacher.py
"""The second call site resolve_many's docstring names: a teacher's own
classroom list, currently built with no check that the code is real - and, as
with coordination_portal, no check that the school's hierarchy exists at all.
"""

import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.db.models.academic import Class, GradeLevel, Segment, AcademicYear
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.teacher_portal import TeacherPortalService


class TeacherScopeValidationTests(unittest.IsolatedAsyncioTestCase):
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

    async def _seed_hierarchy(self, session, school, code):
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="segment", external_id=f"SEG-{code}")
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
            grade_level_id=grade.id, name="class", external_id=f"TURMA-{code}",
        )
        session.add(klass)
        await session.commit()

    async def test_a_stale_classroom_code_is_dropped_once_hierarchy_exists(self):
        async with self.session_factory() as session:
            school = await self._school(session, "1")
            await self._seed_hierarchy(session, school, "1")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="teacher-real",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA-1",
            )
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="teacher-real",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA-FANTASMA",
            )

            portal = TeacherPortalService(session, None, None, None)
            classrooms = await portal.get_teacher_authorized_classrooms("teacher-real", school.id)

        self.assertIn("TURMA-1", classrooms)
        self.assertNotIn("TURMA-FANTASMA", classrooms)

    async def test_every_code_survives_when_the_school_has_no_hierarchy_yet(self):
        """This is exactly tests/test_teacher_portal.py's own prof_mendes
        fixture: a real link to scope_external_id="TURMA_3A", no Class row
        behind it. It must keep working."""
        async with self.session_factory() as session:
            school = await self._school(session, "unmigrated")
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="teacher-legacy",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school.id,
                scope_external_id="TURMA_3A",
            )

            portal = TeacherPortalService(session, None, None, None)
            classrooms = await portal.get_teacher_authorized_classrooms("teacher-legacy", school.id)

        self.assertIn("TURMA_3A", classrooms)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rode para ver falhar**

Run: `.venv/bin/python -m pytest tests/test_r0_scope_validation_teacher.py -q`
Expected: FAIL — `test_a_stale_classroom_code_is_dropped_once_hierarchy_exists` falha no `assertNotIn`. O segundo teste já passa (nada filtra ainda) e continua passando depois.

- [ ] **Step 3: Implemente a validação**

Em `src/agente_ia_edu/services/teacher_portal.py`, acrescente o import junto dos outros de `agente_ia_edu.services` (se ainda não estiver lá depois da Task 1):

```python
from agente_ia_edu.services.external_id_resolution import ExternalIdResolver, ResolutionState
```

Substitua a linha final `return list(authorized_classrooms)` (linha 192) por:

```python
        if not authorized_classrooms:
            return []
        resolver = ExternalIdResolver(self.session)
        if not await resolver.has_any_entities(school_id, AdminScopeType.CLASSROOM):
            return list(authorized_classrooms)
        resolutions = await resolver.resolve_many(
            school_id, AdminScopeType.CLASSROOM, authorized_classrooms
        )
        return [
            code for code in authorized_classrooms
            if resolutions[code].state == ResolutionState.RESOLVED
        ]
```

**Não mexa** no ramo `if link.role in (AdminRole.PLATFORM_ADMIN, ...)` (linhas 167-180) nem no `if not links and (...)` do topo — nenhum dos dois usa `scope_external_id` de vínculo individual.

- [ ] **Step 4: Rode para ver passar**

Run: `.venv/bin/python -m pytest tests/test_r0_scope_validation_teacher.py -q`
Expected: PASS, 2 testes.

- [ ] **Step 5: Rode os testes existentes do portal do professor**

Run: `.venv/bin/python -m pytest tests/test_teacher_portal.py -q`
Expected: zero falhas. `test_16_list_teacher_classrooms` e `test_17_student_detail_authorized_and_unauthorized` são os dois que a versão ingênua deste plano quebrou — confirme que passam agora.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/teacher_portal.py tests/test_r0_scope_validation_teacher.py
git commit -m "feat: professor so perde turma que nao resolve onde a hierarquia ja existe

Mesma logica da coordenacao (Task 2), no segundo call site que a docstring do
resolve_many cita como motivo de existir."
```

---

## Depois da última tarefa

Rode o gate desta fase (`test_r0_*`, `test_platform_administration`, `test_coordination_portal`, `test_teacher_portal`) e a suíte completa contra um banco descartável, comparando com o baseline no commit em que esta fase começou. Preste atenção especial a qualquer teste que semeie `UserSchoolLink` com `scope_type` de nível UNIT/SEGMENT/GRADE_LEVEL/CLASSROOM sem semear a entidade correspondente — é exatamente essa combinação que este plano existe para não quebrar.

## O que fica para a próxima onda de 3B

Registrado aqui para não ser esquecido nem redescoberto do zero: `knowledge.py`/`study_search.py` (visibilidade de conteúdo — mesma classe de risco de autorização, superfície maior), `activity_assignment_store.py`, `adaptive_learning_path.py`, o ramo `PLATFORM_ADMIN/DIRECTOR/COORDINATOR` de `teacher_portal.py` (consulta SQL direta, não por vínculo). `teaching_context.py`, `question_governance.py`, `reception.py`, `teacher_materials.py`, `assessments.py` e `study_session.py` não migram em nenhuma onda de 3B — são autorização de fato e migram junto da Fase 3C, com as duas pendências já registradas na Fase 3A: `assessments.py` e `study_session.py` recebem `scope_type` de texto de cliente sem `CHECK`, e precisam de validação na fronteira antes de qualquer resolução.

**Para a Fase 3C, e para quem decidir quando "a hierarquia foi populada" deixa de ser hipótese e vira fato:** `has_any_entities` é um proxy — populada o suficiente para não confiar mais no código livre. Antes do passo 7 da §7 (transformar "não resolve" em erro), alguém precisa decidir, escola por escola, quando a migração de dado real para dentro da hierarquia da Fase 1 está completa. Este plano não resolve essa pergunta; só garante que, enquanto ela não for respondida, ninguém perde acesso por causa disso.
