# R0 Fase 3C — Autorização, onda 6 — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fecha `TeacherPortalService.verify_student_access` — o último atalho conhecido "sem vínculo = acesso concedido" em `teaching_context.py`, `teacher_portal.py` ou `coordination_portal.py`, achado pela revisão final da onda 5 e não corrigido lá por estar fora do escopo das suas duas tarefas.

**Architecture:** Só remoção, igual às cinco ondas anteriores. Diferente dos atalhos já removidos (todos gatilhados pelo *formato* do id), este é gatilhado pela *ausência de vínculo* do lado do aluno, não do professor: um `student_id` sem nenhum `UserSchoolLink` ativo pula o laço por-vínculo inteiro e cai num fallback que autoriza sempre que o professor tem pelo menos uma turma autorizada em `school_id` — sem checar se o aluno tem qualquer relação com essa turma, essa escola, ou existe de fato nos registros administrativos. Removendo o fallback, o laço vazio (aluno sem vínculo nenhum) cai direto no `raise` que já existe logo abaixo — nenhum branch novo.

**Tech Stack:** Python 3.13, FastAPI, SQLAlchemy async, pytest/unittest.

**Spec:** `docs/superpowers/specs/2026-09-13-r0-estrutura-academica-configuracao-design.md` (§7, passo 5 — autorização, sozinha, por último).

## Global Constraints

- Python `>=3.13,<3.14` (de `pyproject.toml`). Nenhuma dependência nova. Nenhuma migração.
- Nenhuma coluna ou campo com formato de credencial (`password`, `token`, `secret`, `credential`, `senha`).
- Testes usam `unittest.IsolatedAsyncioTestCase` com `sqlite+aiosqlite:///:memory:` + `StaticPool` (convenção já usada em todos os testes `test_r0_*` e `test_teacher_portal.py` desta fase) — nenhum teste precisa de banco real.
- Código e comentários em inglês; mensagem de commit em português.

---

## Contexto verificado por execução antes deste plano

O achado foi provado ao vivo contra o código atual, a remoção proposta foi aplicada de verdade, testada contra a suíte-alvo (`test_teacher_portal.py`, 12/12), as suítes vizinhas (`test_r0_teacher_scope_cross_school.py`, `test_r0_scope_validation_teacher.py`, `test_coordination_portal.py`, `test_end_to_end_learning_flow.py`, 27/27) e o gate da fase, e revertida antes de escrever este plano.

Probe (professor com turma autorizada em `school_id`; aluno com dado real de `mastery` mas **zero vínculos ativos** — o mesmo tipo de dado órfão que a fragmentação de pipelines de mastery deste projeto já produziu antes):

```
LEAKED - teacher-a (authorized in school A) read orphan-student-B's real data, no relationship at all:
  content_masteries: [{'content_node_id': '...', 'content_name': 'Conteúdo', 'mastery_score': 91.0, ...}]
```

Depois da remoção:

```
denied -> Student 'orphan-student-B' is outside teacher 'teacher-a' authorized scope in school '...'.
```

Gate da fase (`tests/test_r0_*.py tests/test_platform_administration.py tests/test_coordination_portal.py tests/test_teacher_portal.py tests/test_teaching_context.py tests/test_end_to_end_learning_flow.py`) rodado contra a correção real: **226 passed, 87 subtests, 0 falhas**. Nenhum teste hoje depende do fallback removido — confirmado por grep (nenhum teste chama `verify_student_access`/`get_student_detail_for_teacher` com um `student_id` sem vínculo real) e por execução (a suíte inteira do arquivo alvo e as vizinhas passam sem alteração de contagem além do teste novo).

**Nota sobre blast radius:** diferente do achado de `_fetch_students_in_classrooms` (onda 5), que vazava para QUALQUER identidade sem vínculo, este exige que o `student_id` não tenha *nenhum* `UserSchoolLink` ativo — ou seja, um id de aluno que existe em `StudentContentMastery`/`LearningHistory` mas nunca foi formalmente vinculado (dado órfão de pipeline, ou aluno desativado). Mais estreito, mas real e alcançável hoje via `get_student_detail_for_teacher`, chamada pelas rotas `api/routes/teacher_portal.py` e `api/routes/coordination_portal.py`.

---

## Task 1: Remove o fallback de `verify_student_access`

**Files:**
- Modify: `src/agente_ia_edu/services/teacher_portal.py:218-243` (`verify_student_access`)
- Test: `tests/test_teacher_portal.py` (arquivo já existe — adicionar um teste)

**Interfaces:**
- Consumes: nada de tarefas anteriores desta onda (é a única tarefa). Usa `PlatformAdminService.get_user_active_links` e `TeacherPortalService.get_teacher_authorized_classrooms` (ambos inalterados).
- Produces: `TeacherPortalService.verify_student_access` mantém a mesma assinatura pública (`bool` / `ScopeAuthorizationError`). O único chamador, `get_student_detail_for_teacher` (mesmo arquivo, linha ~553), não precisa mudar.

- [ ] **Step 1: Escreva o teste que falha**

`tests/test_teacher_portal.py` já existe, com uma única classe `TestTeacherPortal(unittest.IsolatedAsyncioTestCase)`, um helper `_seed_data(session)` que cria as escolas A/B, vincula `user:prof_mendes` (TEACHER, CLASSROOM, escola A, `TURMA_3A`) e retorna `(sa.id, sb.id, c_dil.id, c_est.id)`, e `School`, `StudentContentMastery`, `TeacherPortalService`, `ScopeAuthorizationError`, `KnowledgeService`, `TeachingContextService`, `RecommendationEngine`, `VideoRecommendationEngine` já importados no topo — não adicione nenhum import novo. Adicione este método à classe `TestTeacherPortal`, antes de `test_15_report_export_service` (ou em qualquer outro ponto da classe — a posição não importa):

```python
    async def test_verify_student_access_denies_unlinked_student_regardless_of_teacher_scope(self):
        """A student_id with ZERO active UserSchoolLink rows - e.g. mastery/
        history data orphaned by a pipeline that never linked the student -
        must be denied, not granted just because the teacher happens to have
        at least one authorized classroom somewhere in school_id."""
        async with self.session_factory() as session:
            sa_id, _, c_dil_id, _ = await self._seed_data(session)
            session.add(StudentContentMastery(
                external_identity_id="orphan-student",
                content_node_id=c_dil_id,
                mastery_score=91.0,
            ))
            await session.commit()

            ks = KnowledgeService(session)
            t_svc = TeachingContextService(session)
            rec_eng = RecommendationEngine(session, ks)
            vid_eng = VideoRecommendationEngine(session, ks)
            portal_svc = TeacherPortalService(session, ks, t_svc, rec_eng, vid_eng)

            with self.assertRaises(ScopeAuthorizationError):
                await portal_svc.get_student_detail_for_teacher(
                    teacher_id="user:prof_mendes",
                    school_id=sa_id,
                    student_id="orphan-student",
                )
```

- [ ] **Step 2: Rode para confirmar que falha**

Run: `.venv/bin/python -m pytest tests/test_teacher_portal.py::TestTeacherPortal::test_verify_student_access_denies_unlinked_student_regardless_of_teacher_scope -v`
Expected: FALHA (`AssertionError: ScopeAuthorizationError not raised`). Verificado por execução antes de escrever este plano.

- [ ] **Step 3: Remova o fallback**

Em `src/agente_ia_edu/services/teacher_portal.py`, dentro de `verify_student_access`, o corpo atual é:

```python
        # Check student bindings in UserSchoolLink
        links = await self.admin_service.get_user_active_links(student_id)
        if links:
            for link in links:
                if link.school_id == school_id and link.role == AdminRole.STUDENT:
                    if link.scope_type == AdminScopeType.CLASSROOM and link.scope_external_id in authorized_classrooms:
                        return True
                    if link.scope_type in (AdminScopeType.SCHOOL, AdminScopeType.PLATFORM):
                        return True
            raise ScopeAuthorizationError(f"Student '{student_id}' is outside teacher '{teacher_id}' authorized scope in school '{school_id}'.")

        # Fallback check
        if not authorized_classrooms:
            raise ScopeAuthorizationError(f"Teacher '{teacher_id}' has no authorized classrooms in school '{school_id}'.")

        return True
```

Troque para:

```python
        # Check student bindings in UserSchoolLink
        links = await self.admin_service.get_user_active_links(student_id)
        for link in links:
            if link.school_id == school_id and link.role == AdminRole.STUDENT:
                if link.scope_type == AdminScopeType.CLASSROOM and link.scope_external_id in authorized_classrooms:
                    return True
                if link.scope_type in (AdminScopeType.SCHOOL, AdminScopeType.PLATFORM):
                    return True
        raise ScopeAuthorizationError(f"Student '{student_id}' is outside teacher '{teacher_id}' authorized scope in school '{school_id}'.")
```

(Removendo o `if links:`/`else` faz um `student_id` sem vínculo nenhum cair no laço vazio e direto no `raise` final — mesmo padrão usado em `verify_teacher_classroom_scope` e `verify_coordinator_scope` desde as ondas anteriores desta fase. A mensagem "Teacher '{teacher_id}' has no authorized classrooms..." deixa de existir; nenhum teste depende dela — confirmado por grep antes deste plano.)

- [ ] **Step 4: Rode para confirmar que passa**

Run: `.venv/bin/python -m pytest tests/test_teacher_portal.py -v`
Expected: PASS, todos os testes do arquivo (12, incluindo o novo). Verificado por execução antes de escrever este plano.

- [ ] **Step 5: Rode as suítes vizinhas**

Run: `.venv/bin/python -m pytest tests/test_r0_teacher_scope_cross_school.py tests/test_r0_scope_validation_teacher.py tests/test_coordination_portal.py tests/test_end_to_end_learning_flow.py -q`
Expected: zero falhas.

- [ ] **Step 6: Rode o gate inteiro da fase**

Run: `.venv/bin/python -m pytest tests/test_r0_*.py tests/test_platform_administration.py tests/test_coordination_portal.py tests/test_teacher_portal.py tests/test_teaching_context.py tests/test_end_to_end_learning_flow.py -q`
Expected: zero falhas, 226 passed, 87 subtests (baseline pós-onda-5 + este teste novo).

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/services/teacher_portal.py tests/test_teacher_portal.py
git commit -m "fix: remove fallback de verify_student_access que autorizava aluno sem vinculo

Um student_id sem nenhum UserSchoolLink ativo pulava o laco por-vinculo
inteiro e caia num fallback que autorizava sempre que o professor tivesse
pelo menos uma turma autorizada em school_id - sem checar se o aluno tinha
qualquer relacao com essa turma, essa escola, ou existia nos registros
administrativos. Provado ao vivo: professor autorizado so na escola A leu
dado real de mastery de um aluno orfao (sem vinculo nenhum, dado sobrevivente
de uma das pipelines de mastery fragmentadas do projeto).

Achado pela revisao final da onda 5 desta fase, fora do escopo das suas duas
tarefas - fechado aqui com sua propria verificacao e teste.

Nenhum teste existente dependia do fallback (confirmado por grep e execucao
antes deste plano)."
```

---

## Depois da última tarefa

Rode o gate desta fase e a suíte completa contra um banco descartável, comparando com o baseline pós-onda-5 (2019 passed, 4 skipped, 448 subtests, 13 erros ambientais pré-existentes — esse número já inclui os três commits paralelos de portal de administração e correções de sessão de estudo que chegaram em `main` durante a onda 5). Esta onda não deveria mover o número além do teste que ela mesma acrescenta.

## O que fica para a próxima onda de 3C

**Correção à afirmação original deste plano:** a frase abaixo (mantida riscada, não apagada, porque foi escrita e depois provada falsa pela própria revisão final desta onda — o registro fica) dizia:

> ~~Com este plano, `teaching_context.py`, `teacher_portal.py` e `coordination_portal.py` não têm mais nenhum atalho conhecido — nem por formato de id, nem por ausência de vínculo — de "sem relação real = acesso concedido".~~

Isso é **falso**. A revisão final desta onda achou e provou ao vivo dois atalhos pré-existentes, do mesmo padrão, na PRÓPRIA `teacher_portal.py`:

- **`verify_student_access` (`teacher_portal.py:234-235`)**: quando o **aluno** tem vínculo `SCHOOL`/`PLATFORM` (não `CLASSROOM`), a função retorna `True` sem nunca checar se o **professor** tem qualquer autorização em `school_id`. Provado ao vivo: professor vinculado só à escola B (ou sem vínculo nenhum) leu aluno com vínculo `SCHOOL`-scoped na escola A.
- **`_fetch_students_in_classrooms` (`teacher_portal.py:670-682`)**: a cláusula `UserSchoolLink.scope_type == AdminScopeType.SCHOOL` dentro do `or_` é independente da lista `classrooms` recebida — mesmo com `classrooms=[]` (professor sem autorização nenhuma), alunos `SCHOOL`-scoped da escola ainda aparecem via `search_students_in_scope` e os agregados de dashboard.

Ambos pré-existentes (não pioraram com esta onda, que só estreitou acesso) e não corrigidos aqui — exigem projetar o que "professor autorizado" significa para os dois casos sem quebrar os cenários legítimos que o gate de 226 testes hoje cobre; não são remoções mecânicas como as seis já feitas nesta fase. Candidatos naturais para a onda 7.

Restam também, do que já foi mapeado antes desta onda:

- As 12 rotas em `catalog.py`/`discovery.py`/`question_bank.py` com `identity` injetada e nunca lida (achado da revisão final da onda 3) — ainda não investigadas linha a linha.
- A ausência de branch para papel `STUDENT` em `verify_teacher_classroom_scope` (onda 3) — confirmado sem consumidor no repo até a onda 3, não reconfirmado desde então.
- `coordination_portal.py`'s fallback de dado fabricado ("Prof. Mendes") por volta da linha 598-609 — não é bypass de autorização, é dado de demonstração; fica para quando alguém for limpar placeholders.
- `knowledge.py::_is_question_visible`, `question_governance.py`, `reception.py`, `teacher_materials.py`, `assessments.py`, `study_session.py` — os seis arquivos nomeados desde a onda 2 desta fase, nunca lidos linha a linha para o mesmo padrão.

Com os dois achados desta revisão, fica ainda mais claro: só depois de mapear pelo menos os itens acima faz sentido declarar o passo 5 do §7 da spec fechado e começar o passo 6 (remoção do andaime `TURMA_3A`/`TURMA_3B`).
