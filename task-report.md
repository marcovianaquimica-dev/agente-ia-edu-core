# Relatório: editar correção de redação já aprovada

## Ambiente
- `pwd`: `/Users/marcoviana/agente-ia-edu-core/.claude/worktrees/teacher-edit-approved-correction`
- `git branch --show-current`: `teacher-edit-approved-correction`
- Confirmado antes de qualquer edição.

## O que foi implementado

### 1. Backend — `src/agente_ia_edu/api/routes/essay_corrections.py`
- Nova classe `EditCorrectionRequest` (mesmo shape de `ApproveCorrectionRequest`: `final_scores: Optional[dict] = None`, `final_feedback: Optional[dict] = None`), criada separada em vez de reusar `ApproveCorrectionRequest` porque a rota de edit tem semântica diferente (só 2 campos, sem envolver o `EssayCorrectionService.approve`).
- Nova rota `POST /api/v1/teacher/essay-corrections/{essay_correction_id}/edit`, registrada no mesmo `essay_corrections_router` já existente (não criei router novo):
  - `_authorize` + `_correction_for_own_school_or_403` (reuso total, sem lógica nova de permissão).
  - 409 se `correction.status != "APPROVED"`.
  - Atualiza `final_scores`/`final_feedback` só se vierem não-nulos no request (mesmo padrão parcial que o approve já usa, mas aplicado direto no atributo, sem passar pelo `EssayCorrectionService` — o approve usa o service porque também precisa mudar status/timestamps; o edit não).
  - Sempre atualiza `reviewed_by_external_identity = identity.external_user_id`.
  - NÃO toca em `status`, `published_at`, `reviewed_at`.
  - Resposta construída via `_correction_to_response(correction)` ANTES do `commit()`, mesmo padrão de todas as outras rotas neste arquivo (evita `MissingGreenlet` em produção com `expire_on_commit=True`).

### 2. Frontend — `src/agente_ia_edu/web/essay-review.js`, `renderReviewPanel`
- Nova variável `const isEditableNow = isPending || correction.status === 'APPROVED';`.
- Bloco `scoresFeedbackHtml` (inputs de nota C1–C5 e textarea de feedback): trocado `isPending` → `isEditableNow` nas 3 ocorrências que decidem `<input>`/`<textarea>` vs `<p>` somente-leitura. `REJECTED` continua fora de `isEditableNow`, logo continua somente-leitura.
- `actionsHtml`: ramo `correction.status === 'APPROVED'` agora mostra `<p>Decisão: Aprovada</p>` JUNTO com `<button id="er-edit-btn" class="btn btn-secondary">Salvar alterações</button>`. Separei o antigo branch `isTerminal` (que cobria APPROVED+REJECTED com o mesmo template) em dois ramos explícitos (`APPROVED` e depois `isTerminal` — que agora só resta REJECTED) para poder diferenciar o botão.
- Novo handler `er-edit-btn`, no mesmo estilo do `er-approve-btn`:
  - Mesmo dirty-check contra `originalScores`/`originalFeedbackText`.
  - Se nada mudou (`!scoresEdited && !feedbackEdited`): reabilita o botão e `return` sem chamar `reviewRequest` (sem POST vazio).
  - Se mudou algo: `POST /api/v1/teacher/essay-corrections/${correctionId}/edit` com o mesmo formato de `body.final_scores`/`body.final_feedback` que o approve monta.
  - Sucesso: `renderReviewQueue(returnStatus)`.
  - Erro: mensagem em `#er-review-msg`, reabilita o botão.

Sem alterações em outros arquivos JS — nenhum outro arquivo referencia as rotas de `essay-corrections`.

## Testes — `tests/test_r3_essay_corrections_routes.py`

Adicionados 4 testes na mesma classe `EssayCorrectionsRoutesTests` (mesmo `_seed_pending_correction` helper, já aceitava `status="APPROVED"` sem qualquer alteração):

1. `test_edit_approved_updates_final_scores` — edita `final_scores` de uma correção `APPROVED`, confirma resposta com `total == 1000`, `status == "APPROVED"`, `reviewed_by_external_identity == "teacher_30"`, e que um `GET /api/v1/teacher/essay-corrections?status=APPROVED` subsequente também reflete o novo valor (prova que persistiu no banco, não só na resposta em memória).
2. `test_edit_pending_review_is_409` — tenta editar uma correção `PENDING_REVIEW` via `/edit`, confirma `409`.
3. `test_edit_does_not_change_status_or_published_at` — lê `status`/`published_at`/`reviewed_at` do banco antes e depois de um edit bem-sucedido, confirma que os 3 continuam idênticos.
4. `test_edit_from_another_school_is_403` — professor de outra escola tentando editar (mesmo padrão dos testes cross-school já existentes para `/approve`/`/reject`/`/retry`/`export.pdf`), confirma `403`.

### Comando de teste + saída

```
PYTHONPATH=src /Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r3_essay_corrections_routes.py -v
```

```
20 passed, 5 warnings in 1.53s
```

Todos os 16 testes pré-existentes + 4 novos passam. Nenhum teste quebrado.

## Autorrevisão

- **Rota nova recusa não-APPROVED?** Sim, 409 explícito antes de qualquer mutação (`test_edit_pending_review_is_409` cobre `PENDING_REVIEW`; `REJECTED`/`NEEDS_REVIEW` caem no mesmo `if`, não testados individualmente mas mesma linha de código).
- **status/published_at/reviewed_at realmente não mudam?** Confirmado por teste dedicado lendo direto do banco antes/depois (`test_edit_does_not_change_status_or_published_at`). O código nunca atribui a esses 3 campos na rota nova.
- **Tela mostra editável pra APPROVED, somente-leitura pra REJECTED?** Sim — `isEditableNow` inclui só `isPending || status === 'APPROVED'`; `REJECTED` cai no `else` (`<p>` read-only) em todas as 3 ocorrências do bloco.
- **Botão não manda POST vazio quando nada mudou?** Sim — o handler faz o mesmo dirty-check do approve e dá `return` antes de chamar `reviewRequest` quando `!scoresEdited && !feedbackEdited`.
- **Autorização igual às outras rotas?** Sim, reuso literal de `_authorize` + `_correction_for_own_school_or_403`, sem lógica nova; teste cross-school confirma 403.
- **Os 4 testes exercitam o comportamento certo?** Sim, conforme detalhado acima — cobrem sucesso+persistência, 409 de status errado, imutabilidade dos campos de publicação, e autorização cross-school.

## Dúvidas / riscos

- Nenhuma divergência genuína entre o código atual e o que foi pedido — `ApproveCorrectionRequest` já tinha exatamente o shape esperado para copiar em `EditCorrectionRequest`, `_correction_to_response`/`_authorize`/`_correction_for_own_school_or_403` já existiam como descrito, e `isPending`/`isTerminal`/`actionsHtml` em `essay-review.js` bateram com a descrição da tarefa.
- Risco baixo: a rota nova ATUALIZA `correction.final_scores`/`final_feedback` diretamente no objeto ORM sem reaproveitar `EssayCorrectionService` (diferente de `/approve`, que delega pro service). Isso é intencional — o service existe pra encapsular a transição de status (que aqui não acontece) — mas é uma pequena divergência de padrão que vale registrar: se no futuro `EssayCorrectionService` ganhar validação de shape para `final_scores`/`final_feedback` (hoje não tem, o approve também aceita qualquer dict), essa rota não herdaria automaticamente essa validação.
- Sem trilha de auditoria dedicada (decisão explícita do ruling #3) — `reviewed_by_external_identity` é sobrescrito a cada edit, então quem aprovou originalmente se perde se o editor for outra pessoa. Já é o comportamento aceito pelo design.

## Rodada de correção — validação de shape

### Achado da revisão

A rota `/edit` gravava `request.final_scores`/`request.final_feedback` direto no atributo ORM, sem validar formato — diferente de `EssayCorrectionService.approve()` (`src/agente_ia_edu/services/essay_correction.py`, linhas 213-227), que valida `final_scores` contra `Scores.model_validate` e `final_feedback` contra `Feedback.model_validate` (ambos de `src/agente_ia_edu/essay_engine_contract/v1.py`, `ConfigDict(extra="forbid")`), envolvidos em `try/except ValidationError` que vira `ValueError` — capturado depois na rota `/approve` (`essay_corrections.py` linha ~187) como `except ValueError as exc: raise HTTPException(422, detail=str(exc))`.

### O que foi mudado

`src/agente_ia_edu/api/routes/essay_corrections.py`:
- Import novo: `from pydantic import BaseModel, ValidationError` (adicionado `ValidationError`) e `from ...essay_engine_contract.v1 import Feedback, Scores`.
- Na rota `edit_essay_correction`, antes de atribuir `correction.final_scores`/`correction.final_feedback`, adicionado:
  ```python
  if request.final_scores is not None:
      try:
          Scores.model_validate(request.final_scores)
      except ValidationError as exc:
          raise HTTPException(status_code=422, detail=f"final_scores is not a valid Scores payload: {exc}") from exc
  if request.final_feedback is not None:
      try:
          Feedback.model_validate(request.final_feedback)
      except ValidationError as exc:
          raise HTTPException(status_code=422, detail=f"final_feedback is not a valid Feedback payload: {exc}") from exc
  ```
  Só DEPOIS desse bloco (que levanta 422 direto, sem tocar em `correction`) é que o código segue para as atribuições `correction.final_scores = request.final_scores` / `correction.final_feedback = request.final_feedback`. Mesma mensagem de erro (`"final_scores is not a valid Scores payload: {exc}"` / `"final_feedback is not a valid Feedback payload: {exc}"`) que o service usa antes de virar `ValueError`, e mesmo `status_code=422` que `/approve` produz para o caso equivalente — consistência de formato de erro mantida.
- Confirmado (lendo `approve()`): o service guarda o **dict original** em `correction.final_scores`/`final_feedback` (não o `.model_dump()` do objeto Pydantic validado) — `Scores.model_validate`/`Feedback.model_validate` são usados só como gate, o dict é atribuído em seguida. A rota `/edit` segue o mesmo padrão.
- Diferença de estrutura vs. `/approve`: o service faz a validação e a atribuição juntas por campo (`if final_scores is not None: try/except; ...; correction.final_scores = final_scores`), enquanto na rota `/edit` a validação de AMBOS os campos roda primeiro (dois blocos `if`), e só depois as duas atribuições. Isso é intencional e mais seguro para o requisito de "correção no banco fica intacta se a validação falhar": mesmo que `final_scores` seja válido mas `final_feedback` seja inválido (ou vice-versa), nenhuma atribuição acontece antes de AMBOS passarem na validação — não há risco de `final_scores` novo já ter sido setado no objeto ORM quando `final_feedback` falha e a exceção sobe (o que aconteceria se eu tivesse seguido literalmente o padrão intercalado do service).

### Testes adicionados

Em `tests/test_r3_essay_corrections_routes.py`, mesma classe `EssayCorrectionsRoutesTests`, logo antes de `test_reject`:
1. `test_edit_with_malformed_final_scores_is_422_and_keeps_old_values` — `final_scores` faltando C5 (só 4 de 5 competências) → 422, e lê `correction.final_scores` do banco antes/depois do POST, confirma que não mudou (nem foi sobrescrito pelo dict malformado).
2. `test_edit_with_final_scores_bad_points_scale_is_422` — `C1.points = 50` (fora da escala oficial 0/40/80/120/160/200) → 422.
3. `test_edit_with_final_scores_inconsistent_total_is_422` — soma das 5 competências é 1000, `total` enviado é 999 → 422.
4. `test_edit_with_malformed_final_feedback_is_422_and_keeps_old_values` — `next_essay_strategy=""` (viola `min_length=1`) → 422, confirma `final_feedback` no banco inalterado.

### Comando de teste + saída completa

```
PYTHONPATH=src /Users/marcoviana/agente-ia-edu-core/.venv/bin/python -m pytest tests/test_r3_essay_corrections_routes.py -v
```

```
============================= test session starts ==============================
platform darwin -- Python 3.13.15, pytest-9.1.1, pluggy-1.6.0 -- /Users/marcoviana/agente-ia-edu-core/.venv/bin/python
cachedir: .pytest_cache
rootdir: /Users/marcoviana/agente-ia-edu-core/.claude/worktrees/teacher-edit-approved-correction
configfile: pyproject.toml
plugins: cov-7.1.0, asyncio-1.4.0, anyio-4.14.2
asyncio: mode=Mode.STRICT, debug=False, asyncio_default_fixture_loop_scope=None, asyncio_default_test_loop_scope=function
collecting ... collected 24 items

tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_a_correction_from_another_school_is_403 PASSED [  4%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_a_student_cannot_approve PASSED [  8%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_approve_publishes PASSED [ 12%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_approve_with_score_edit PASSED [ 16%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_bulk_approve_is_best_effort PASSED [ 20%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_bulk_approve_rejects_an_id_from_another_school PASSED [ 25%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_edit_approved_updates_final_scores PASSED [ 29%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_edit_does_not_change_status_or_published_at PASSED [ 33%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_edit_from_another_school_is_403 PASSED [ 37%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_edit_pending_review_is_409 PASSED [ 41%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_edit_with_final_scores_bad_points_scale_is_422 PASSED [ 45%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_edit_with_final_scores_inconsistent_total_is_422 PASSED [ 50%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_edit_with_malformed_final_feedback_is_422_and_keeps_old_values PASSED [ 54%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_edit_with_malformed_final_scores_is_422_and_keeps_old_values PASSED [ 58%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_export_pdf_503_when_pymupdf_unavailable PASSED [ 62%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_export_pdf_approved_returns_pdf_bytes PASSED [ 66%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_export_pdf_from_another_school_is_403 PASSED [ 70%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_export_pdf_needs_review_is_404 PASSED [ 75%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_export_pdf_pending_review_returns_pdf_bytes PASSED [ 79%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_export_pdf_rejected_is_404 PASSED [ 83%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_list_returns_pending_reviews_for_own_school PASSED [ 87%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_reject PASSED [ 91%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_retry_from_another_school_is_403 PASSED [ 95%]
tests/test_r3_essay_corrections_routes.py::EssayCorrectionsRoutesTests::test_retry_succeeds_for_own_school PASSED [100%]

======================== 24 passed, 5 warnings in 1.17s ========================
```

Todos os 20 testes pré-existentes (16 da implementação original + 4 do `/edit` da leva anterior) + os 4 novos desta correção passam. Nenhum teste quebrado.

Também confirmado: `python -c "import agente_ia_edu.api.routes.essay_corrections"` importa sem erro (imports novos `ValidationError`, `Feedback`, `Scores` resolvidos corretamente).

### Autorrevisão

- **A validação recusa mesmo um `final_scores` malformado, com 422?** Sim — três variantes cobertas por teste: competência faltando, `points` fora da escala oficial, `total` inconsistente com a soma. Todas 422.
- **A correção NO BANCO fica intacta (valores antigos) quando a validação falha?** Sim — a validação roda ANTES de qualquer `correction.final_scores = ...`/`correction.final_feedback = ...`; a exceção interrompe a função antes de chegar nas atribuições. Confirmado lendo o banco antes/depois em `test_edit_with_malformed_final_scores_is_422_and_keeps_old_values` e `test_edit_with_malformed_final_feedback_is_422_and_keeps_old_values`.
- **Um `final_scores`/`final_feedback` válido continua funcionando normalmente?** Sim — os 4 testes de sucesso já existentes (`test_edit_approved_updates_final_scores`, `test_edit_does_not_change_status_or_published_at`, etc.) continuam passando sem alteração.
- **A mensagem de erro segue o mesmo padrão que `/approve` já usa pro caso equivalente?** Sim — mesmo texto (`"final_scores is not a valid Scores payload: {exc}"` / `"final_feedback is not a valid Feedback payload: {exc}"`) e mesmo `status_code=422`.

### Riscos residuais

- Nenhum novo. O risco já registrado na seção anterior ("rota não reaproveita `EssayCorrectionService`") permanece, mas agora ambas as rotas (`/approve` e `/edit`) fazem a MESMA validação de shape de forma independente — se o contrato `Scores`/`Feedback` mudar, os dois lugares precisam ser atualizados manualmente (não há um único ponto de verdade fora do service). Isso já era verdade antes desta correção (a rota `/edit` não chama o service) e não piorou nem foi resolvido por ela — só ficou mais visível, já que agora as duas rotas duplicam a mesma lógica de validação em vez de uma não validar nada.
