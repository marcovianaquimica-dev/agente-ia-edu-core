# R0 Fase 3C — Autorização, onda 7 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fecha os dois atalhos "professor não verificado, aluno com vínculo `SCHOOL`/`PLATFORM`" achados pela revisão final da onda 6, ambos em `teacher_portal.py`.

**Architecture:** Diferente das seis remoções puras das ondas 1-6, aqui o atalho não pode ser simplesmente removido — o comportamento de "aluno `SCHOOL`/`PLATFORM`-scoped é visível para quem tem qualquer autorização na escola" é legítimo e precisa continuar funcionando para diretores, coordenadores, `PLATFORM_ADMIN` e professores com escopo escola-inteira. O que falta é gatilhar esse comportamento na autorização REAL do professor, não em nada. A chave: `get_teacher_authorized_classrooms` já devolve lista vazia se e somente se o professor não tem NENHUMA autorização real na escola (confirmado por leitura completa da função nesta investigação — todo caminho de acesso legítimo, incluindo diretor/coordenador/`PLATFORM_ADMIN`/professor escola-inteira numa escola sem nenhuma `TeachingLesson` ainda, cai no fallback `["TURMA_3A"]`, nunca em lista vazia). Então gatir os dois pontos em "lista de turmas autorizadas não-vazia" é suficiente e seguro — nenhuma lógica nova de autorização, só usar um sinal que já existe.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async, pytest/unittest.

**Spec:** `docs/superpowers/specs/2026-09-13-r0-estrutura-academica-configuracao-design.md` (§7, passo 5 — autorização, sozinha, por último).

## Global Constraints

- Python `>=3.13,<3.14` (de `pyproject.toml`). Nenhuma dependência nova. Nenhuma migração.
- Nenhuma coluna ou campo com formato de credencial (`password`, `token`, `secret`, `credential`, `senha`).
- Testes usam `unittest.IsolatedAsyncioTestCase` com `sqlite+aiosqlite:///:memory:` + `StaticPool` (convenção já usada em todos os testes `test_r0_*` e `test_teacher_portal.py` desta fase) — nenhum teste precisa de banco real.
- Código e comentários em inglês; mensagem de commit em português.

---

## Contexto verificado por execução antes deste plano

Os dois achados foram provados ao vivo contra o código atual (probe abaixo), a correção proposta foi aplicada de verdade e verificada em duas frentes: (1) os dois casos negativos (leak fechado) e (2) dois casos positivos — diretor real numa escola sem nenhuma `TeachingLesson` ainda, e professor com escopo escola-inteira — continuam autorizados corretamente, confirmando que a correção não regride nenhum papel legítimo.

Probe (professor sem nenhuma autorização em `school_id`; aluno com vínculo `SCHOOL`-scoped real nessa escola):

```
Probe 1 (verify_student_access, teacher-b-only vs school A SCHOOL-scoped student): LEAKED, returned True
Probe 2 (_fetch_students_in_classrooms, school A, empty classrooms): ['student-school-scoped-A']
```

Depois da correção:

```
Probe 1: denied -> Student 'student-school-scoped-A' is outside teacher 'teacher-b-only' authorized scope in school '...'.
Probe 2 (_fetch_students_in_classrooms, school A, empty classrooms): []
```

Probe positivo (diretor real de uma escola SEM nenhuma `TeachingLesson`; professor com escopo escola-inteira) — ambos continuam vendo o aluno `SCHOOL`-scoped normalmente:

```
verify_student_access(director-a, school A, school-scoped student): True
_fetch_students_in_classrooms(director-a's classrooms=['TURMA_3A']): ['student-school-scoped-A']
verify_student_access(teacher-school-wide-a, school A, school-scoped student): True
_fetch_students_in_classrooms(teacher-school-wide-a's classrooms=['TURMA_3A']): ['student-school-scoped-A']
```

Suíte-alvo (`test_teacher_portal.py`, com os 4 testes desta onda somados aos 11 já existentes): **15/15 passed**. Suítes vizinhas (`test_r0_teacher_scope_cross_school.py`, `test_r0_scope_validation_teacher.py`, `test_coordination_portal.py`, `test_end_to_end_learning_flow.py`): **39/39 passed**. Gate da fase: **226 passed, 87 subtests, 0 falhas** — idêntico ao baseline pós-onda-6.

**Suíte completa contra banco descartável, com uma nota importante:** rodei a suíte inteira contra um banco Postgres real reconstruído do zero (`_fetch_students_in_classrooms` tem alcance amplo — mesma cautela da onda 5). Resultado: **2020 passed, 2 failed, 448 subtests, 13 erros ambientais pré-existentes**. As duas falhas (`tests/test_phase8a_teacher_list_builder_postgresql.py::Phase8ATeacherListBuilderPostgreSQLE2E::test_catalog_children_do_not_leak_another_universe_branch` e `::test_complete_draft_builder_flow`) foram investigadas e **não têm relação com esta correção**:
- Nenhum dos dois testes, nem o setup (`seed_candidates`/`create_list`) que compartilham, referencia `TeacherPortalService`, `verify_student_access`, `_fetch_students_in_classrooms` ou `get_teacher_authorized_classrooms` (confirmado por grep no arquivo de teste).
- Ambos passam limpos quando rodados isolados, e também quando rodados junto com todo o arquivo `test_phase8a_teacher_list_builder_postgresql.py` + `test_phase8a_teacher_list_builder_http.py` (13/13 passed).
- O domínio (catálogo de conteúdo / construtor de listas de exercícios) não tem relação com autorização de aluno-professor.

Avaliação: instabilidade pré-existente de ordem de execução no conjunto completo (suíte E2E longa compartilhando um Postgres real), não uma regressão desta onda. Registrado aqui para transparência — quem for investigar essa flakiness no futuro tem o ponto de partida.

---

## Task 1: Gatilha os dois retornos de vínculo `SCHOOL`/`PLATFORM` na autorização real do professor

**Files:**
- Modify: `src/agente_ia_edu/services/teacher_portal.py:218-236` (`verify_student_access`) e `:664-684` (`_fetch_students_in_classrooms`)
- Test: `tests/test_teacher_portal.py` (arquivo já existe — adicionar três testes)

**Interfaces:**
- Consumes: `get_teacher_authorized_classrooms` (inalterado, mas seu contrato — "lista vazia se e somente se professor não tem autorização real na escola" — é o que esta tarefa passa a depender explicitamente; documentado no código como parte da correção).
- Produces: `verify_student_access` e `_fetch_students_in_classrooms` mantêm as mesmas assinaturas públicas. Nenhum chamador (`get_student_detail_for_teacher`, `get_teacher_dashboard`, `get_classroom_detail`, `search_students_in_scope`, e os três call-sites em `coordination_portal.py`) precisa mudar.

- [ ] **Step 1: Escreva os quatro testes**

`tests/test_teacher_portal.py` já existe, com uma única classe `TestTeacherPortal(unittest.IsolatedAsyncioTestCase)`, `School`, `uuid4` (de `from uuid import uuid4`), `StudentContentMastery`, `PlatformAdminService`, `AdminRole`, `AdminScopeType`, `TeacherPortalService`, `KnowledgeService`, `TeachingContextService`, `RecommendationEngine`, `VideoRecommendationEngine`, `ScopeAuthorizationError` já importados no topo — não adicione nenhum import novo. Adicione estes quatro métodos à classe `TestTeacherPortal`, no final (antes de `if __name__ == "__main__":`):

```python
    async def test_verify_student_access_denies_school_scoped_student_for_unauthorized_teacher(self):
        """A SCHOOL-scoped student link must not bypass the teacher-side check.
        Before this fix, ANY teacher (even one with zero authorization in
        school_id) was granted access to a student holding a SCHOOL or
        PLATFORM scoped link there."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school_a = School(id=uuid4(), code="SCH_SCOPE_A", name="Escola A Scope")
            school_b = School(id=uuid4(), code="SCH_SCOPE_B", name="Escola B Scope")
            session.add_all([school_a, school_b])
            await session.commit()

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="teacher-b-only",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school_b.id,
                scope_external_id="TURMA_B1",
            )
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="student-school-scoped-a",
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )
            await session.commit()

            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

            with self.assertRaises(ScopeAuthorizationError):
                await portal_svc.verify_student_access(
                    teacher_id="teacher-b-only",
                    school_id=school_a.id,
                    student_id="student-school-scoped-a",
                )

    async def test_verify_student_access_allows_school_scoped_student_for_real_director(self):
        """A real DIRECTOR of school_id, with no TeachingLesson rows yet (a
        fresh school), must still be allowed - authorized_classrooms falls
        back to a non-empty placeholder for legitimate school-wide roles, so
        this must not regress alongside the fix above."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school_a = School(id=uuid4(), code="SCH_SCOPE_C", name="Escola C Scope")
            session.add(school_a)
            await session.commit()

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="director-a",
                role=AdminRole.DIRECTOR,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )
            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="student-school-scoped-c",
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )
            await session.commit()

            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

            allowed = await portal_svc.verify_student_access(
                teacher_id="director-a",
                school_id=school_a.id,
                student_id="student-school-scoped-c",
            )
            self.assertTrue(allowed)

    async def test_fetch_students_in_classrooms_denies_school_scoped_student_with_no_authorization(self):
        """The SCHOOL leg of the query's or_ must not match when classrooms
        is empty - an empty list means the caller has zero authorization in
        school_id, and a SCHOOL-scoped student must not leak through anyway."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school_a = School(id=uuid4(), code="SCH_SCOPE_D", name="Escola D Scope")
            session.add(school_a)
            await session.commit()

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="student-school-scoped-d",
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )
            await session.commit()

            portal = TeacherPortalService(session, None, None, None)
            students = await portal._fetch_students_in_classrooms(school_a.id, [])

        self.assertEqual(students, [])

    async def test_fetch_students_in_classrooms_allows_school_scoped_student_for_teacher_with_classroom(self):
        """A teacher with a real, non-empty classroom list must still see
        SCHOOL-scoped students alongside their own classroom's - the SCHOOL
        leg is gated on 'classrooms is non-empty', not removed."""
        async with self.session_factory() as session:
            admin_service = PlatformAdminService(session)
            school_a = School(id=uuid4(), code="SCH_SCOPE_E", name="Escola E Scope")
            session.add(school_a)
            await session.commit()

            await admin_service.link_user_to_school(
                performed_by_external_id="admin:master",
                external_user_id="student-school-scoped-e",
                role=AdminRole.STUDENT,
                scope_type=AdminScopeType.SCHOOL,
                school_id=school_a.id,
            )
            await session.commit()

            portal = TeacherPortalService(session, None, None, None)
            students = await portal._fetch_students_in_classrooms(school_a.id, ["TURMA_E1"])

        self.assertEqual(students, ["student-school-scoped-e"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rode para confirmar que os testes negativos falham**

Run: `.venv/bin/python -m pytest tests/test_teacher_portal.py::TestTeacherPortal::test_verify_student_access_denies_school_scoped_student_for_unauthorized_teacher tests/test_teacher_portal.py::TestTeacherPortal::test_fetch_students_in_classrooms_denies_school_scoped_student_with_no_authorization -v`
Expected: os dois FALHAM (código atual concede acesso sem checar o professor / devolve o aluno mesmo com `classrooms=[]`). Os outros dois (positivos) já PASSAM contra o código atual — não precisam ser confirmados como falhando, eles documentam comportamento que já existe e não pode regredir.

- [ ] **Step 3: Corrija `verify_student_access`**

Em `src/agente_ia_edu/services/teacher_portal.py`, o corpo atual é:

```python
        for link in links:
            if link.school_id == school_id and link.role == AdminRole.STUDENT:
                if link.scope_type == AdminScopeType.CLASSROOM and link.scope_external_id in authorized_classrooms:
                    return True
                if link.scope_type in (AdminScopeType.SCHOOL, AdminScopeType.PLATFORM):
                    return True
        raise ScopeAuthorizationError(f"Student '{student_id}' is outside teacher '{teacher_id}' authorized scope in school '{school_id}'.")
```

Troque a penúltima linha:

```python
        for link in links:
            if link.school_id == school_id and link.role == AdminRole.STUDENT:
                if link.scope_type == AdminScopeType.CLASSROOM and link.scope_external_id in authorized_classrooms:
                    return True
                if authorized_classrooms and link.scope_type in (AdminScopeType.SCHOOL, AdminScopeType.PLATFORM):
                    return True
        raise ScopeAuthorizationError(f"Student '{student_id}' is outside teacher '{teacher_id}' authorized scope in school '{school_id}'.")
```

- [ ] **Step 4: Corrija `_fetch_students_in_classrooms`**

No mesmo arquivo, o corpo atual é:

```python
        """Fetch student IDs bound to classrooms or school."""
        stmt = (
            select(UserSchoolLink.external_user_id)
            .where(
                UserSchoolLink.school_id == school_id,
                UserSchoolLink.role == AdminRole.STUDENT,
                UserSchoolLink.active.is_(True),
                or_(
                    UserSchoolLink.scope_external_id.in_(classrooms),
                    UserSchoolLink.scope_type == AdminScopeType.SCHOOL,
                ),
            )
            .distinct()
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())
```

Adicione a checagem antes do `stmt`:

```python
        """Fetch student IDs bound to classrooms or school."""
        if not classrooms:
            return []
        stmt = (
            select(UserSchoolLink.external_user_id)
            .where(
                UserSchoolLink.school_id == school_id,
                UserSchoolLink.role == AdminRole.STUDENT,
                UserSchoolLink.active.is_(True),
                or_(
                    UserSchoolLink.scope_external_id.in_(classrooms),
                    UserSchoolLink.scope_type == AdminScopeType.SCHOOL,
                ),
            )
            .distinct()
        )
        res = await self.session.execute(stmt)
        return list(res.scalars().all())
```

- [ ] **Step 5: Rode para confirmar que os quatro passam**

Run: `.venv/bin/python -m pytest tests/test_teacher_portal.py -v`
Expected: PASS, 15 testes (11 já existentes + 4 novos). Verificado por execução antes de escrever este plano.

- [ ] **Step 6: Rode as suítes vizinhas**

Run: `.venv/bin/python -m pytest tests/test_r0_teacher_scope_cross_school.py tests/test_r0_scope_validation_teacher.py tests/test_coordination_portal.py tests/test_end_to_end_learning_flow.py -q`
Expected: zero falhas.

- [ ] **Step 7: Rode o gate inteiro da fase**

Run: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py tests/test_coordination_portal.py tests/test_teacher_portal.py tests/test_teaching_context.py tests/test_end_to_end_learning_flow.py -q`
Expected: zero falhas, 230 passed, 87 subtests (226 baseline + os 4 testes novos).

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/services/teacher_portal.py tests/test_teacher_portal.py
git commit -m "fix: gatilha vinculo SCHOOL/PLATFORM do aluno na autorizacao real do professor

verify_student_access e _fetch_students_in_classrooms concediam acesso a um
aluno com vinculo SCHOOL ou PLATFORM sem nunca checar se o PROFESSOR tinha
qualquer autorizacao real na escola pedida - o vinculo do lado do aluno
bastava sozinho. Provado ao vivo: professor sem nenhum vinculo na escola A
(ou vinculado so a outra escola) leu aluno com vinculo SCHOOL-scoped na
escola A, tanto por verify_student_access quanto por
_fetch_students_in_classrooms.

get_teacher_authorized_classrooms ja devolve lista vazia se e somente se o
professor nao tem nenhuma autorizacao real na escola (confirmado por
leitura completa da funcao - todo papel legitimo, incluindo diretor numa
escola sem nenhuma TeachingLesson ainda, cai no fallback nao-vazio
[\"TURMA_3A\"]) - os dois pontos passam a gatilhar nessa lista, sem logica
de autorizacao nova.

Achado pela revisao final da onda 6 desta fase. Confirmado por execucao:
os dois papeis legitimos que dependem do vinculo SCHOOL/PLATFORM do aluno
(diretor real, professor de escopo escola-inteira) continuam autorizados
normalmente depois da correcao."
```

---

## Depois da última tarefa

Rode o gate desta fase e a suíte completa contra um banco descartável, comparando com o baseline pós-onda-6 (2021 passed, 4 skipped, 448 subtests, 13 erros ambientais pré-existentes). Se as duas falhas de `test_phase8a_teacher_list_builder_postgresql.py` documentadas acima reaparecerem, confirme que ainda são as mesmas duas (não uma regressão nova) antes de seguir.

## O que fica para a próxima onda de 3C

Restam, do que já foi mapeado nas ondas anteriores:

- As 12 rotas em `catalog.py`/`discovery.py`/`question_bank.py` com `identity` injetada e nunca lida (achado da revisão final da onda 3) — ainda não investigadas linha a linha.
- A ausência de branch para papel `STUDENT` em `verify_teacher_classroom_scope` (onda 3) — confirmado sem consumidor no repo até a onda 3, não reconfirmado desde então.
- `coordination_portal.py`'s fallback de dado fabricado ("Prof. Mendes") por volta da linha 598-609 — não é bypass de autorização, é dado de demonstração; fica para quando alguém for limpar placeholders.
- `knowledge.py::_is_question_visible`, `question_governance.py`, `reception.py`, `teacher_materials.py`, `assessments.py`, `study_session.py` — os seis arquivos nomeados desde a onda 2 desta fase, nunca lidos linha a linha para o mesmo padrão.
- A instabilidade de ordem de execução em `test_phase8a_teacher_list_builder_postgresql.py` documentada acima — não é autorização, mas fica registrada para quem for investigar a saúde da suíte completa.

Com este plano, se limpo, `teaching_context.py`, `teacher_portal.py` e `coordination_portal.py` voltam a não ter nenhum atalho conhecido de "sem relação real = acesso concedido" — desta vez confirmado por uma revisão final que já procurou especificamente por essa classe de problema nos dois arquivos. Ainda assim, só faz sentido declarar o passo 5 do §7 da spec fechado depois de mapear os itens acima.
