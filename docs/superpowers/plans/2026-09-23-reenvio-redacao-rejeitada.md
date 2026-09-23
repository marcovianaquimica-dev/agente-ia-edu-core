# Reenvio de redação rejeitada — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Deixar o aluno saber quando uma correção foi rejeitada pelo professor e dar um caminho de reenvio, reaproveitando a lógica de `resubmit_essay_id` que o backend já suporta ponta a ponta.

**Architecture:** Uma rota já existente (`GET .../correction`) ganha um `elif` para expor `status="REJECTED"` distintamente (hoje colapsa em `"PENDING"`); o frontend do aluno ganha um terceiro ramo de renderização na devolutiva, que reabre o formulário de envio já existente passando `resubmit_essay_id`. Nenhuma rota nova, nenhuma mudança de modelo de dados, nenhuma mudança na lógica de negócio do backend — só exposição de um estado que já existe e reuso de um formulário que já existe.

**Tech Stack:** FastAPI (Python) + vanilla JS/HTML/CSS, sem build step — mesmo stack do resto do frontend deste repositório.

**Spec:** `docs/superpowers/specs/2026-09-23-reenvio-redacao-rejeitada-design.md`

## Global Constraints

- HTML-escapar todo texto interpolado — regra dura já em vigor (`escEssay`, já existente em `essay.js`).
- Reaproveitar `essayHeaders()`/`essayRequest()` — nenhum mecanismo de auth novo.
- Nenhuma rota nova de backend — só a rota `GET .../correction` já existente ganha um ramo a mais.
- Testes de backend: `unittest.IsolatedAsyncioTestCase`/`unittest.TestCase`, `sqlite+aiosqlite:///:memory:` + `StaticPool` — mesmo padrão de todas as rotas já existentes.
- Sem testes automatizados de frontend — verificação manual/browser real, mesmo corte das levas anteriores.
- `EssayCorrection` tem duas CHECK constraints relevantes para qualquer fixture de teste com `status="REJECTED"`: `reviewed_at` é obrigatório (mesma regra de `APPROVED`), mas `published_at` continua exclusivo de `APPROVED` (não deve ser setado para `REJECTED`).

---

### Task 1: Backend — expor REJECTED distintamente na devolutiva

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_submissions.py:491-492`
- Test: `tests/test_frontend_r_student_essay_correction_route.py` (arquivo já existente, adicionar um teste)

**Interfaces:**
- Consumes: `EssayCorrection.status` (já existente, sem mudança de schema).
- Produces: `StudentCorrectionResponse.status` passa a aceitar `"PENDING" | "APPROVED" | "REJECTED"` (era `"PENDING" | "APPROVED"`). Consumido pela Task 2 (`essay.js`).

- [ ] **Step 1: Escrever o teste que falha**

Adicionar ao final da classe `StudentEssayCorrectionRouteTests`, em `tests/test_frontend_r_student_essay_correction_route.py` (antes do `if __name__ == "__main__":` no final do arquivo), um novo método de teste. O arquivo já tem um helper `_add_correction(submission_id, *, status, with_content)` (linhas 129-158) que já trata `reviewed_at`/`published_at` corretamente para REJECTED (linha 153: `reviewed_at=... if status in ("APPROVED", "REJECTED") else None`) — não precisa mudar o helper, só usar com `status="REJECTED"`:

```python
    def test_rejected_exposes_status_but_no_content(self):
        submission_id = self._seed_submission("7")
        # with_content=True prova que a rota ATIVAMENTE esconde o conteúdo em
        # REJECTED, não que o conteúdo simplesmente não existia no banco.
        self._add_correction(submission_id, status="REJECTED", with_content=True)
        self._as("student_7")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["status"], "REJECTED")
        self.assertIsNone(body["final_scores"])
        self.assertIsNone(body["final_feedback"])
        self.assertIsNone(body["annotations"])
```

- [ ] **Step 2: Rodar o teste para confirmar que falha**

Run: `pytest tests/test_frontend_r_student_essay_correction_route.py::StudentEssayCorrectionRouteTests::test_rejected_exposes_status_but_no_content -v`
Expected: FAIL (`body["status"]` viria `"PENDING"`, não `"REJECTED"` — o `elif` ainda não existe).

- [ ] **Step 3: Implementar**

Em `src/agente_ia_edu/api/routes/essay_submissions.py`, dentro de `get_essay_submission_correction`, substituir (linhas 491-492):
```python
        if correction is None or correction.status != "APPROVED":
            return StudentCorrectionResponse(essay_submission_id=submission.id, status="PENDING")
```
por:
```python
        if correction is None or correction.status in ("NEEDS_REVIEW", "PENDING_REVIEW"):
            return StudentCorrectionResponse(essay_submission_id=submission.id, status="PENDING")
        if correction.status == "REJECTED":
            return StudentCorrectionResponse(essay_submission_id=submission.id, status="REJECTED")
```
O restante da função (o caminho `APPROVED`, linhas 494 em diante no arquivo original) fica exatamente como está — não precisa de nenhuma outra mudança, já que só resta `status == "APPROVED"` depois dos dois `if` acima.

- [ ] **Step 4: Rodar o teste para confirmar que passa**

Run: `pytest tests/test_frontend_r_student_essay_correction_route.py -v`
Expected: PASS (6 testes — os 5 já existentes + o novo)

- [ ] **Step 5: Rodar a suíte de regressão do arquivo**

Run: `pytest tests/test_frontend_r_student_correction_canonical_text.py tests/test_r3_essay_submission_missing_greenlet_regression.py tests/test_r2_essay_submissions_authorization.py -v`
Expected: PASS, sem regressão (`test_canonical_text_null_when_pending` usa `PENDING_REVIEW`, continua colapsando em PENDING como antes — o `elif` novo só afeta REJECTED).

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_submissions.py tests/test_frontend_r_student_essay_correction_route.py
git commit -m "feat(redacao): expose REJECTED status distinctly in student devolutiva"
```

---

### Task 2: Frontend — devolutiva de rejeição + reenvio

**Files:**
- Modify: `src/agente_ia_edu/web/essay.js` (`renderDevolutiva`, `renderNewSubmissionForm`, `renderModeBody`, `renderTypedForm`, `renderUploadArea`, mais uma função nova `renderRejectedDevolutiva` e um helper `translateResubmitError`)

**Interfaces:**
- Consumes: `StudentCorrectionResponse.status === "REJECTED"` (Task 1); `prompt.my_submission.essay_id` (já existente, `MySubmissionSummary.essay_id`, R2).
- Produces: nada consumido por outra task deste plano.

- [ ] **Step 1: Adicionar o helper de tradução de erro de reenvio bloqueado**

Logo depois de `translateDetail` (linha 24, antes de `essayHeaders`), adicionar:
```javascript
  function translateResubmitError(e) {
    if (e.status === 409) return 'Esta avaliação não permite reenvio.';
    return e.message;
  }
```

- [ ] **Step 2: Adicionar o ramo REJECTED em `renderDevolutiva` + a nova função `renderRejectedDevolutiva`**

Em `renderDevolutiva` (linhas 345-364), inserir um novo `if` entre o bloco `PENDING` existente e a chamada final a `renderApprovedDevolutiva`:
```javascript
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
      if (correction.status === 'REJECTED') {
        renderRejectedDevolutiva(prompt);
        return;
      }
      renderApprovedDevolutiva(prompt, correction);
    } catch (e) {
      container.innerHTML = `<div class="card"><p class="empty-text">${escEssay(e.message)}</p></div>`;
    }
  }

  function renderRejectedDevolutiva(prompt) {
    container.innerHTML = `
      <div class="card">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <h3>${escEssay(prompt.title)}</h3>
        <p class="empty-text">Seu professor pediu um reenvio desta redação. Envie uma nova versão para receber uma nova devolutiva.</p>
        <button class="btn btn-primary" type="button" id="essay-resubmit-btn">Reenviar redação</button>
      </div>`;
    container.querySelector('[data-back]').addEventListener('click', () => loadPrompts());
    container.querySelector('#essay-resubmit-btn').addEventListener('click', () => {
      renderNewSubmissionForm(prompt, prompt.my_submission.essay_id);
    });
  }
```

- [ ] **Step 3: `renderNewSubmissionForm` passa a aceitar um `resubmitEssayId` opcional**

Em `renderNewSubmissionForm` (linha 99), mudar a assinatura e a construção de `state` (linha 106):
```javascript
  function renderNewSubmissionForm(prompt, resubmitEssayId) {
    // Tracks the real EssaySubmission this attempt lazily creates (see
    // renderUploadArea): null until the student actually picks a file.
    // Threaded through switchMode/renderModeBody so tab-switching stays a
    // pure UI change - no POST fires just from clicking "Fotografar"/"Enviar
    // PDF", which is what used to strand uploaded pages on orphaned,
    // unreachable submissions (repeated tab clicks each created a new one).
    // resubmitEssayId, when present (reenvio de redação rejeitada), rides
    // along on state so both create-submission call sites below (typed
    // atomic submit, and the lazy create-on-first-file for photo/PDF) can
    // send it as resubmit_essay_id without needing a second code path.
    const state = { submissionId: null, anchorMode: null, mode: null, resubmitEssayId: resubmitEssayId || null };
```
O resto da função (o `container.innerHTML`, `switchMode`, os `addEventListener`, a chamada final `switchMode('TYPED')`) fica exatamente como está.

- [ ] **Step 4: `renderModeBody` passa `state` para `renderTypedForm` também**

Em `renderModeBody` (linhas 137-144), mudar a chamada a `renderTypedForm`:
```javascript
  function renderModeBody(mode, prompt, state) {
    const body = container.querySelector('#essay-mode-body');
    if (mode === 'TYPED') {
      renderTypedForm(body, prompt, state);
      return;
    }
    renderUploadArea(body, prompt, mode, state);
  }
```

- [ ] **Step 5: `renderTypedForm` recebe `state` e manda `resubmit_essay_id` quando presente**

Em `renderTypedForm` (linha 146), mudar a assinatura e o corpo do `POST`:
```javascript
  function renderTypedForm(body, prompt, state) {
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
          body: JSON.stringify({
            prompt_assignment_id: prompt.prompt_assignment_id, mode: 'TYPED', text,
            ...(state.resubmitEssayId ? { resubmit_essay_id: state.resubmitEssayId } : {}),
          }),
        });
        await loadPrompts();
      } catch (e) {
        msg.hidden = false;
        msg.textContent = translateResubmitError(e);
        submitBtn.disabled = false;
      }
    });
  }
```

- [ ] **Step 6: `renderUploadArea` manda `resubmit_essay_id` na criação lazy**

Em `renderUploadArea` (linhas 180-242), dentro do `if (!state.submissionId) { ... }` (linhas 197-209), mudar o corpo do `POST`:
```javascript
        if (!state.submissionId) {
          // Lazy creation: the EssaySubmission row (and its uploaded files)
          // is only worth creating once the student actually commits to a
          // file, not on every mode-tab click.
          const submission = await essayRequest('/api/v1/student/essay-submissions', {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({
              prompt_assignment_id: prompt.prompt_assignment_id, mode,
              ...(state.resubmitEssayId ? { resubmit_essay_id: state.resubmitEssayId } : {}),
            }),
          });
          state.submissionId = submission.id;
          state.anchorMode = submission.anchor_mode;
          state.mode = mode;
        }
```
E, no mesmo `catch` desse `addEventListener('change', ...)` (linhas 230-233), trocar `e.message` por `translateResubmitError(e)`:
```javascript
      } catch (e) {
        msg.hidden = false;
        msg.textContent = translateResubmitError(e);
      }
```

- [ ] **Step 7: Sanity-check de sintaxe**

Run: `node --check src/agente_ia_edu/web/essay.js`
Expected: sem saída.

- [ ] **Step 8: Verificação manual (browser real)**

Se houver ferramenta de browser disponível: aprovar uma correção e depois rejeitá-la via API/DB diretamente (reaproveitando o padrão de fixture já usado pelas levas anteriores — `EssayCorrection.status = "REJECTED"`, `reviewed_at` setado), abrir a devolutiva desse aluno, confirmar a mensagem de rejeição e o botão "Reenviar redação". Clicar no botão, confirmar que abre o mesmo formulário de 3 abas. Enviar uma redação digitada nova e confirmar que o `POST` inclui `resubmit_essay_id` (checar via `read_network_requests`) e retorna 201. Reabrir a lista e confirmar que a proposta agora mostra a NOVA submissão (não mais "rejeitada"). Se possível, também testar o caso de bloqueio: uma escola/turma com `correction_mode="AVALIATIVO"` deve mostrar "Esta avaliação não permite reenvio" em vez de travar. Checar `read_console_messages` por erros. Limpar qualquer fixture criada.

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/web/essay.js
git commit -m "feat(redacao): let students resubmit after a rejected correction"
```

---

### Task 3: Verificação final (sem código)

**Files:** nenhum — só verificação.

- [ ] **Step 1: Suíte de backend completa**

Run: `pytest tests/ -q`
Expected: todos os testes passam, incluindo o novo (Task 1), zero regressão nos já existentes.

- [ ] **Step 2: Passeio manual completo, se possível**

Se a Task 2 não conseguiu fazer a verificação manual (sem ferramenta de browser disponível naquele momento), tentar novamente aqui. Cobrir especificamente: (a) o caminho feliz completo — reenvio depois de rejeição até uma nova devolutiva aparecer; (b) o bloqueio AVALIATIVO, se houver uma escola configurada assim nos dados de teste; (c) confirmar que a submissão antiga (rejeitada) não aparece mais em lugar nenhum depois do reenvio confirmado (vira `SUPERSEDED`, comportamento já existente do backend, não modificado por este plano).

- [ ] **Step 3: Relatar quaisquer achados**

Se a verificação manual não for possível neste ambiente, registrar isso explicitamente em vez de declarar sucesso sem prova visual — mesma disciplina das levas anteriores.

---
