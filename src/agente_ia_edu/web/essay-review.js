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
      </div>`;
  }

  function wireTabs() {
    container.querySelectorAll('[data-tab]').forEach((btn) => {
      btn.addEventListener('click', () => {
        if (btn.dataset.tab === 'prompts') renderPromptsList();
        if (btn.dataset.tab === 'queue') renderReviewQueue();
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
              ${classrooms.map((c) => `<option value="${tmEsc(c.classroom_id)}">${tmEsc(c.name)}</option>`).join('') || '<option value="">Nenhuma turma disponível</option>'}
            </select>
          </div>
          <div class="form-group"><label for="er-assign-due"><input id="er-assign-validation" type="checkbox" checked> Exigir revisão docente</label></div>
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
      const msg = container.querySelector('#er-assign-msg');
      if (!classId) return;
      try {
        await reviewRequest(`/api/v1/catalog/essay-prompts/${promptId}/assignments`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ class_id: classId, validation_enabled: validationEnabled }),
        });
        renderPromptDetail(promptId);
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
      }
    });
  }

  function renderReviewQueue() {
    // Implementado na Task 9.
    container.innerHTML = `${renderTabs('queue')}<p class="empty-text">Fila de revisão — próxima etapa.</p>`;
    wireTabs();
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
