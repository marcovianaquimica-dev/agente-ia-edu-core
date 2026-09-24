/* AGENTE IA EDU — Portal do Professor — módulo de Redação */
(function essayReviewModule(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.EssayReviewView = api;
})(typeof window !== 'undefined' ? window : null, function createEssayReviewView() {
  let container = null;
  let schoolId = '';
  let teacherId = '';
  let prompts = [];
  let currentCorrections = [];

  function tmEsc(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[character]);
  }

  function translateDetail(detail) {
    if (typeof detail === 'string') {
      const moduleMatch = detail.match(/^Module '(.+)' is not enabled for the current school\.$/);
      if (moduleMatch) return `O módulo '${moduleMatch[1]}' não está habilitado para esta escola.`;
      return detail;
    }
    if (detail && detail.message) return detail.message;
    return 'Não foi possível completar a ação.';
  }

  function reviewHeaders(extra = {}) {
    // teacherId comes from teacher.js's own in-memory state (never
    // sessionStorage - unlike the student portal's access token, this
    // repo's teacher/coordinator identity is only ever an in-memory
    // filter-input value). init() receives the current value each time
    // teacher.js calls it, including every time that filter input changes
    // (loadCurrentView() re-fires on every state.teacherId change), so this
    // module never goes stale relative to what teacher.js itself shows.
    return { 'Authorization': `Bearer ${teacherId || 'user:prof_mendes'}`, ...extra };
  }

  async function reviewRequest(path, options = {}) {
    const res = await fetch(path, { ...options, headers: reviewHeaders(options.headers || {}) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const error = new Error(translateDetail(data.detail));
      error.status = res.status;
      throw error;
    }
    return data;
  }

  function renderTabs(activeTab) {
    return `
      <div class="essay-review-tabs">
        <button class="btn ${activeTab === 'prompts' ? 'btn-primary' : 'btn-secondary'}" type="button" data-tab="prompts">Propostas</button>
        <button class="btn ${activeTab === 'queue' ? 'btn-primary' : 'btn-secondary'}" type="button" data-tab="queue">Fila de Revisão</button>
        <button class="btn ${activeTab === 'evolution' ? 'btn-primary' : 'btn-secondary'}" type="button" data-tab="evolution">Evolução</button>
      </div>`;
  }

  function wireTabs() {
    container.querySelectorAll('[data-tab]').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (btn.dataset.tab === 'prompts') renderPromptsList();
        if (btn.dataset.tab === 'queue') renderReviewQueue();
        if (btn.dataset.tab === 'evolution') renderEvolutionTab();
      });
    });
  }

  async function renderPromptsList() {
    container.innerHTML = `${renderTabs('prompts')}<p class="empty-text">Carregando propostas...</p>`;
    wireTabs();
    try {
      prompts = await reviewRequest('/api/v1/catalog/essay-prompts');
    } catch (e) {
      container.innerHTML = `${renderTabs('prompts')}<p class="empty-text">${tmEsc(e.message)}</p>`;
      wireTabs();
      return;
    }
    const tabsEl = container.querySelector('.essay-review-tabs');
    if (tabsEl.nextElementSibling) tabsEl.nextElementSibling.remove();
    tabsEl.insertAdjacentHTML('afterend', `
      <div class="tm-form-actions" style="margin: 12px 0;">
        <button class="btn btn-primary" type="button" id="er-new-prompt-btn">Nova proposta</button>
      </div>
      <div class="tm-table-wrap" style="overflow-x:auto;">
        <table class="tm-table">
          <thead><tr><th>Título</th><th>Ano</th><th>Status</th><th></th></tr></thead>
          <tbody id="er-prompts-body">
            ${prompts.map((p) => `
              <tr>
                <td>${tmEsc(p.title)}</td><td>${p.year}</td><td>${tmEsc(p.status)}</td>
                <td><button class="btn btn-secondary" type="button" data-open-prompt="${tmEsc(p.id)}">Abrir</button></td>
              </tr>`).join('') || '<tr><td colspan="4" class="empty-text">Nenhuma proposta criada ainda.</td></tr>'}
          </tbody>
        </table>
      </div>`);

    container.querySelector('#er-new-prompt-btn').addEventListener('click', renderNewPromptForm);
    container.querySelectorAll('[data-open-prompt]').forEach((btn) => {
      btn.addEventListener('click', () => renderPromptDetail(btn.dataset.openPrompt));
    });
  }

  function renderNewPromptForm() {
    container.innerHTML = `
      ${renderTabs('prompts')}
      <div class="card tm-form">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <h3>Nova proposta de redação</h3>
        <form id="er-new-prompt-form">
          <div class="form-group"><label for="er-title">Título</label><input id="er-title" class="text-input" required></div>
          <div class="form-group"><label for="er-statement">Enunciado</label><textarea id="er-statement" class="textarea-input" rows="6" required></textarea></div>
          <div class="form-group"><label for="er-year">Ano</label><input id="er-year" class="text-input" type="number" value="2026" required></div>
          <button class="btn btn-primary" type="submit">Criar proposta</button>
          <p id="er-new-prompt-msg" class="tm-msg" hidden></p>
        </form>
      </div>`;
    wireTabs();
    container.querySelector('[data-back]').addEventListener('click', renderPromptsList);
    container.querySelector('#er-new-prompt-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const msg = container.querySelector('#er-new-prompt-msg');
      const submitBtn = ev.target.querySelector('button[type="submit"]');
      submitBtn.disabled = true;
      try {
        const prompt = await reviewRequest('/api/v1/catalog/essay-prompts', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: container.querySelector('#er-title').value.trim(),
            statement: container.querySelector('#er-statement').value.trim(),
            year: Number(container.querySelector('#er-year').value),
          }),
        });
        renderPromptDetail(prompt.id);
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
        submitBtn.disabled = false;
      }
    });
  }

  async function renderPromptDetail(promptId) {
    container.innerHTML = `${renderTabs('prompts')}<p class="empty-text">Carregando...</p>`;
    wireTabs();
    let detail;
    try {
      detail = await reviewRequest(`/api/v1/catalog/essay-prompts/${promptId}`);
    } catch (e) {
      container.innerHTML = `${renderTabs('prompts')}<p class="empty-text">${tmEsc(e.message)}</p>`;
      wireTabs();
      return;
    }

    let classrooms = [];
    try {
      classrooms = await reviewRequest(
        `/api/v1/teacher/classrooms?school_id=${encodeURIComponent(schoolId)}&academic_year=2026`,
      );
    } catch (e) {
      classrooms = [];
    }

    const detailHtml = document.createElement('div');
    detailHtml.innerHTML = `
      <div class="card tm-detail-grid">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <h3>${tmEsc(detail.title)}</h3>
        <p>${tmEsc(detail.statement)}</p>
        <p class="empty-text">Status: ${tmEsc(detail.status)}</p>

        <h4>Materiais de apoio</h4>
        <ul>${detail.materials.map((m) => `<li>${tmEsc(m.content || m.material_type)}</li>`).join('') || '<li class="empty-text">Nenhum material.</li>'}</ul>
        <form id="er-material-form" class="tm-form-row">
          <div class="form-group"><label for="er-material-content">Adicionar material de texto</label><textarea id="er-material-content" class="textarea-input" rows="3"></textarea></div>
          <button class="btn btn-secondary" type="submit">Adicionar</button>
        </form>
        <p id="er-material-msg" class="tm-msg" hidden></p>

        <h4>Turmas atribuídas</h4>
        <ul>${detail.assignments.map((a) => `<li>${tmEsc(a.class_id)} — ${tmEsc(a.status)}</li>`).join('') || '<li class="empty-text">Nenhuma turma atribuída ainda.</li>'}</ul>
        <form id="er-assign-form" class="tm-form-row">
          <div class="form-group">
            <label for="er-assign-class">Turma</label>
            <select id="er-assign-class" class="text-input">
              ${classrooms.map((c) => (c.class_id
                ? `<option value="${tmEsc(c.class_id)}">${tmEsc(c.name)}</option>`
                : `<option value="" disabled>${tmEsc(c.name)} (sem turma cadastrada)</option>`)).join('') || '<option value="">Nenhuma turma disponível</option>'}
            </select>
          </div>
          <div class="form-group"><label for="er-assign-due">Prazo (opcional)</label><input id="er-assign-due" class="text-input" type="date"></div>
          <div class="form-group"><label for="er-assign-validation"><input id="er-assign-validation" type="checkbox" checked> Exigir revisão docente</label></div>
          <button class="btn btn-primary" type="submit">Atribuir</button>
        </form>
        <p id="er-assign-msg" class="tm-msg" hidden></p>
      </div>`;
    const tabsEl = container.querySelector('.essay-review-tabs');
    if (tabsEl.nextElementSibling) tabsEl.nextElementSibling.remove();
    tabsEl.insertAdjacentElement('afterend', detailHtml.firstElementChild);

    container.querySelector('[data-back]').addEventListener('click', renderPromptsList);
    container.querySelector('#er-material-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const content = container.querySelector('#er-material-content').value.trim();
      const msg = container.querySelector('#er-material-msg');
      if (!content) return;
      try {
        await reviewRequest(`/api/v1/catalog/essay-prompts/${promptId}/materials`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ material_type: 'TEXT', content, position: detail.materials.length }),
        });
        renderPromptDetail(promptId);
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
      }
    });
    container.querySelector('#er-assign-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const classId = container.querySelector('#er-assign-class').value;
      const validationEnabled = container.querySelector('#er-assign-validation').checked;
      const dueDate = container.querySelector('#er-assign-due').value;
      const msg = container.querySelector('#er-assign-msg');
      if (!classId) return;
      try {
        await reviewRequest(`/api/v1/catalog/essay-prompts/${promptId}/assignments`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            class_id: classId,
            validation_enabled: validationEnabled,
            // <input type="date"> gives "YYYY-MM-DD" or "" when left blank -
            // never send an empty string as due_at, the backend expects a
            // real datetime or the field omitted entirely.
            due_at: dueDate ? new Date(dueDate).toISOString() : null,
          }),
        });
        renderPromptDetail(promptId);
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
      }
    });
  }

  async function renderReviewQueue(status) {
    const currentStatus = status || 'PENDING_REVIEW';
    container.innerHTML = `
      ${renderTabs('queue')}
      <div class="tm-form-row" style="margin: 12px 0;">
        <div class="form-group"><label for="er-queue-status">Status</label>
          <select id="er-queue-status" class="text-input">
            <option value="PENDING_REVIEW">Pendente de revisão</option>
            <option value="NEEDS_REVIEW">Precisa de atenção (falha)</option>
            <option value="APPROVED">Aprovadas</option>
            <option value="REJECTED">Rejeitadas</option>
          </select>
        </div>
      </div>
      <div id="er-queue-body"><p class="empty-text">Carregando...</p></div>`;
    wireTabs();
    const statusSelect = container.querySelector('#er-queue-status');
    statusSelect.value = currentStatus;
    statusSelect.addEventListener('change', (ev) => renderReviewQueue(ev.target.value));

    const body = container.querySelector('#er-queue-body');
    try {
      currentCorrections = await reviewRequest(
        `/api/v1/teacher/essay-corrections?status=${encodeURIComponent(currentStatus)}`,
      );
    } catch (e) {
      body.innerHTML = `<p class="empty-text">${tmEsc(e.message)}</p>`;
      return;
    }

    body.innerHTML = `
      <div class="tm-table-wrap" style="overflow-x:auto;">
        <table class="tm-table">
          <thead><tr><th>Aluno</th><th>Proposta</th><th>Enviada em</th><th></th></tr></thead>
          <tbody>
            ${currentCorrections.map((c) => `
              <tr>
                <td>${tmEsc(c.student_name || '—')}</td>
                <td>${tmEsc(c.prompt_title || '—')}</td>
                <td>${c.submitted_at ? new Date(c.submitted_at).toLocaleString('pt-BR') : '—'}</td>
                <td><button class="btn btn-secondary" type="button" data-open-correction="${tmEsc(c.id)}">Revisar</button></td>
              </tr>`).join('') || '<tr><td colspan="4" class="empty-text">Nenhuma correção com este status.</td></tr>'}
          </tbody>
        </table>
      </div>`;

    body.querySelectorAll('[data-open-correction]').forEach((btn) => {
      btn.addEventListener('click', () => renderReviewPanel(btn.dataset.openCorrection, currentStatus));
    });
  }

  async function renderEvolutionTab() {
    container.innerHTML = `
      ${renderTabs('evolution')}
      <div class="tm-form-row" style="margin: 12px 0;">
        <div class="form-group">
          <label for="er-evolution-search">Buscar aluno</label>
          <input id="er-evolution-search" class="text-input" placeholder="Nome do aluno">
        </div>
      </div>
      <div id="er-evolution-students"></div>
      <div id="er-evolution-body"></div>`;
    wireTabs();

    const searchInput = container.querySelector('#er-evolution-search');
    const studentsList = container.querySelector('#er-evolution-students');
    const body = container.querySelector('#er-evolution-body');

    async function loadStudents(q) {
      let students = [];
      try {
        students = await reviewRequest(`/api/v1/teacher/essay-evolution/students${q ? `?q=${encodeURIComponent(q)}` : ''}`);
      } catch (e) {
        studentsList.innerHTML = `<p class="empty-text">${tmEsc(e.message)}</p>`;
        return;
      }
      studentsList.innerHTML = students.length
        ? `<ul class="essay-evolution-student-list">${students.map((s) => `
            <li><button class="btn btn-secondary" type="button" data-select-student="${tmEsc(s.student_id)}">${tmEsc(s.student_name)}</button></li>`).join('')}</ul>`
        : '<p class="empty-text">Nenhum aluno com redação corrigida ainda.</p>';
      studentsList.querySelectorAll('[data-select-student]').forEach((btn) => {
        btn.addEventListener('click', () => loadEvolutionForStudent(btn.dataset.selectStudent));
      });
    }

    async function loadEvolutionForStudent(studentId) {
      body.innerHTML = '<p class="empty-text">Carregando evolução...</p>';
      let data;
      try {
        data = await reviewRequest(`/api/v1/teacher/essay-evolution?student_id=${encodeURIComponent(studentId)}`);
      } catch (e) {
        body.innerHTML = `<p class="empty-text">${tmEsc(e.message)}</p>`;
        return;
      }
      if (!data.entries.length) {
        body.innerHTML = '<p class="empty-text">Este aluno ainda não tem redação aprovada.</p>';
        return;
      }
      let checklistData = { rationales: [], feedbackStrengths: [] };
      try {
        const approved = await reviewRequest('/api/v1/teacher/essay-corrections?status=APPROVED');
        const match = approved.find((c) => c.essay_submission_id === data.entries[0].essay_submission_id);
        if (match) {
          checklistData = {
            rationales: (match.ai_output || {}).rationales || [],
            feedbackStrengths: (match.final_feedback || {}).strengths || [],
          };
        }
      } catch (e) {
        // Checklist degrades to its own empty state below.
      }
      body.innerHTML = window.EssayEvolution.renderEvolutionSection(data, checklistData);
      window.EssayEvolution.wireEvolutionSection(body, data);
    }

    searchInput.addEventListener('input', () => loadStudents(searchInput.value.trim()));
    await loadStudents('');
  }

  function renderReviewPanel(correctionId, returnStatus) {
    const correction = currentCorrections.find((c) => c.id === correctionId);
    if (!correction) {
      renderReviewQueue(returnStatus);
      return;
    }
    const aiOutput = correction.ai_output || {};
    const scores = correction.final_scores || {};
    const perCompetency = scores.per_competency || {};
    const feedback = correction.final_feedback || {};
    const annotations = aiOutput.annotations || [];
    const alerts = aiOutput.alerts || [];
    // final_scores/final_feedback (not aiOutput.scores/aiOutput.feedback) are
    // the source of truth here on purpose: EssayCorrectionService seeds them
    // from the AI's raw output at correction-creation time, and they're what
    // approve() actually commits - so this preview always matches what will
    // be published, even before any teacher edit.
    const reportCorrection = {
      ...aiOutput,
      final_scores: correction.final_scores,
      final_feedback: correction.final_feedback,
    };

    const isPending = correction.status === 'PENDING_REVIEW';
    const isTerminal = correction.status === 'APPROVED' || correction.status === 'REJECTED';
    const showsContent = isPending || isTerminal;

    // Captured once, at render time, so the approve handler below can tell
    // an actual edit apart from the field simply still showing what it was
    // pre-filled with (including the "AI produced no score" default of 0 -
    // see the dirty-check note by er-approve-btn for why that distinction
    // matters).
    const originalScores = ['C1', 'C2', 'C3', 'C4', 'C5'].map((code) => Number((perCompetency[code] || {}).points) || 0);
    const originalFeedbackText = (feedback.next_essay_strategy || '').trim();

    const failureHtml = correction.status === 'NEEDS_REVIEW'
      ? `<div class="alert-banner alert-danger">Falha na correção automática: ${tmEsc(correction.failure_reason || 'motivo não informado')}</div>`
      : '';

    const alertsHtml = alerts.length
      ? `<div>${alerts.map((a) => `<span class="badge badge-accent">${tmEsc(a.code)}</span>`).join(' ')}</div>`
      : '';

    // Rendered for PENDING_REVIEW (editable, so a teacher has something to
    // act on) and for the terminal APPROVED/REJECTED states (read-only, so
    // "Revisar" on an already-decided correction - reachable from the
    // Aprovadas/Rejeitadas queue filters - shows what was actually decided
    // instead of an empty panel). NEEDS_REVIEW has no scores/feedback yet
    // (the AI call never produced any), so it stays out of this block.
    const scoresFeedbackHtml = showsContent ? `
      <h4>Notas por competência</h4>
      <div class="tm-form-row">
        ${['C1', 'C2', 'C3', 'C4', 'C5'].map((code) => `
          <div class="form-group">
            <label ${isPending ? `for="er-score-${code}"` : ''}>${code}</label>
            ${isPending
              ? `<input id="er-score-${code}" class="text-input" type="number" min="0" max="200" step="40" value="${Number((perCompetency[code] || {}).points) || 0}">`
              : `<p class="empty-text">${tmEsc((perCompetency[code] || {}).points ?? '—')}</p>`}
          </div>`).join('')}
      </div>
      <h4>Feedback</h4>
      <div class="form-group">
        <label ${isPending ? 'for="er-feedback-strategy"' : ''}>Próxima redação</label>
        ${isPending
          ? `<textarea id="er-feedback-strategy" class="textarea-input" rows="3">${tmEsc(feedback.next_essay_strategy || '')}</textarea>`
          : `<p class="empty-text">${tmEsc(feedback.next_essay_strategy || '—')}</p>`}
      </div>
      <h4>Pré-visualização da devolutiva (com base na nota/feedback atuais desta correção)</h4>
      <div class="essay-report-preview">
        ${window.EssayReport.renderRichReport(reportCorrection, {
          editable: true, escFn: tmEsc,
          originalContentHtml: '<div id="er-original-content"><p class="empty-text">Carregando conteúdo original...</p></div>',
        })}
      </div>
    ` : '';

    const exportHtml = (isPending || correction.status === 'APPROVED')
      ? '<button class="btn btn-secondary" type="button" id="er-export-pdf-btn">Exportar PDF</button>'
      : '';

    const actionsHtml = isPending ? `
        <button class="btn btn-primary" type="button" id="er-approve-btn">Aprovar</button>
        <button class="btn btn-secondary" type="button" id="er-reject-btn">Rejeitar</button>
        <p class="empty-text">Rejeitar descarta esta correção permanentemente e oferece ao aluno a opção de reenviar a redação.</p>`
      : correction.status === 'NEEDS_REVIEW' ? `
        <button class="btn btn-primary" type="button" id="er-retry-btn">Tentar novamente</button>`
      : isTerminal ? `
        <p class="empty-text">Decisão: ${correction.status === 'APPROVED' ? 'Aprovada' : 'Rejeitada'}</p>`
      : '';

    container.innerHTML = `
      ${renderTabs('queue')}
      <div class="card tm-detail-grid">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar à fila</button>
        ${failureHtml}
        ${exportHtml}
        ${alertsHtml}
        ${scoresFeedbackHtml}
        <div class="tm-form-actions">${actionsHtml}</div>
        <p id="er-review-msg" class="tm-msg" hidden></p>
      </div>`;
    wireTabs();
    container.querySelector('[data-back]').addEventListener('click', () => renderReviewQueue(returnStatus));

    if (showsContent) {
      loadOriginalContent(correctionId, annotations);
    }

    const exportBtn = container.querySelector('#er-export-pdf-btn');
    if (exportBtn) {
      exportBtn.addEventListener('click', async () => {
        exportBtn.disabled = true;
        try {
          const res = await fetch(`/api/v1/teacher/essay-corrections/${correctionId}/export.pdf`, {
            headers: reviewHeaders(),
          });
          if (!res.ok) throw new Error('export failed');
          const blob = await res.blob();
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = 'devolutiva.pdf';
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(url);
        } catch (e) {
          alert('Não foi possível exportar o PDF.');
        } finally {
          exportBtn.disabled = false;
        }
      });
    }

    const msg = container.querySelector('#er-review-msg');

    const approveBtn = container.querySelector('#er-approve-btn');
    if (approveBtn) {
      approveBtn.addEventListener('click', async () => {
        approveBtn.disabled = true;
        const currentScores = ['C1', 'C2', 'C3', 'C4', 'C5'].map((code) => Number(container.querySelector(`#er-score-${code}`).value) || 0);
        const currentFeedbackText = container.querySelector('#er-feedback-strategy').value.trim();
        // Dirty-check against what was actually loaded into the form: only
        // send final_scores/final_feedback when the teacher changed them,
        // matching spec §5.5's "approve as-is" path. This is also what keeps
        // an untouched AI-null score (defaults to 0 in the inputs) from
        // silently becoming a real, published 0/1000 grade - if nothing was
        // edited, nothing is sent, and the correction's own final_scores
        // (still null in that case) is left alone.
        const scoresEdited = currentScores.some((value, i) => value !== originalScores[i]);
        const feedbackEdited = currentFeedbackText !== originalFeedbackText;
        const body = {};
        if (scoresEdited) {
          const editedPerCompetency = Object.fromEntries(['C1', 'C2', 'C3', 'C4', 'C5'].map((code, i) => [
            code,
            {
              points: currentScores[i],
              confidence: (perCompetency[code] || {}).confidence ?? 1.0,
            },
          ]));
          body.final_scores = {
            per_competency: editedPerCompetency,
            total: currentScores.reduce((sum, value) => sum + value, 0),
          };
        }
        if (feedbackEdited) {
          body.final_feedback = { ...feedback, next_essay_strategy: currentFeedbackText };
        }
        try {
          await reviewRequest(`/api/v1/teacher/essay-corrections/${correctionId}/approve`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
          renderReviewQueue(returnStatus);
        } catch (e) {
          msg.hidden = false;
          msg.textContent = e.message;
          approveBtn.disabled = false;
        }
      });
    }

    const rejectBtn = container.querySelector('#er-reject-btn');
    if (rejectBtn) {
      rejectBtn.addEventListener('click', async () => {
        rejectBtn.disabled = true;
        try {
          await reviewRequest(`/api/v1/teacher/essay-corrections/${correctionId}/reject`, { method: 'POST' });
          renderReviewQueue(returnStatus);
        } catch (e) {
          msg.hidden = false;
          msg.textContent = e.message;
          rejectBtn.disabled = false;
        }
      });
    }

    const retryBtn = container.querySelector('#er-retry-btn');
    if (retryBtn) {
      retryBtn.addEventListener('click', async () => {
        retryBtn.disabled = true;
        try {
          await reviewRequest(`/api/v1/teacher/essay-corrections/${correctionId}/retry`, { method: 'POST' });
          renderReviewQueue(returnStatus);
        } catch (e) {
          msg.hidden = false;
          msg.textContent = e.message;
          retryBtn.disabled = false;
        }
      });
    }
  }

  async function loadOriginalContent(correctionId, annotations) {
    const target = container.querySelector('#er-original-content');
    if (!target) return;
    let content;
    try {
      content = await reviewRequest(`/api/v1/teacher/essay-corrections/${correctionId}/submission-content`);
    } catch (e) {
      target.innerHTML = `<p class="empty-text">${tmEsc(e.message)}</p>`;
      return;
    }
    if (content.anchor_mode === 'TEXT_OFFSET') {
      target.innerHTML = `<div class="essay-highlighted-text">${window.EssayAnnotations.renderHighlightedText(content.canonical_text || '', annotations)}</div>`;
      window.EssayAnnotations.wirePopovers(target, annotations);
      return;
    }
    const pages = content.pages || [];
    target.innerHTML = pages.map((p) => `
      <div class="essay-page-image-wrap" data-page-wrap="${p.page_number}">
        <img data-page-image="${p.page_number}" alt="Página ${p.page_number}">
      </div>`).join('') || '<p class="empty-text">Nenhuma página.</p>';

    target.querySelectorAll('[data-page-image]').forEach((img) => {
      const pageNumber = Number(img.dataset.pageImage);
      fetch(`/api/v1/teacher/essay-corrections/${correctionId}/pages/${pageNumber}/image`, {
        headers: reviewHeaders(),
      })
        .then((res) => (res.ok ? res.blob() : Promise.reject(new Error('image fetch failed'))))
        .then((blob) => new Promise((resolve, reject) => {
          img.onload = resolve;
          img.onerror = () => reject(new Error('image decode failed'));
          img.src = URL.createObjectURL(blob);
        }))
        .then(() => {
          const wrap = target.querySelector(`[data-page-wrap="${pageNumber}"]`);
          window.EssayAnnotations.renderImageMarkers(wrap, img, annotations, pageNumber);
          window.EssayAnnotations.wirePopovers(wrap, annotations);
        })
        .catch(() => { img.alt = `Não foi possível carregar a página ${pageNumber}.`; });
    });
  }

  function init(currentSchoolId, currentTeacherId) {
    container = document.getElementById('essay-review-root');
    schoolId = currentSchoolId || '';
    teacherId = currentTeacherId || '';
    if (!container) return;
    renderPromptsList();
  }

  return { init };
});
