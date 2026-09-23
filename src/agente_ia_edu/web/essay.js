/* AGENTE IA EDU — Portal do Aluno — módulo de Redação */
(function essayModule(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.EssayView = api;
})(typeof window !== 'undefined' ? window : null, function createEssayView() {
  let container = null;
  let prompts = [];

  function escEssay(value) {
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

  // Mirrors app.js's studentHeaders() default exactly. There, state.studentId
  // is already the string 'student:alice', so its real fallback token is the
  // DOUBLE-prefixed 'student:student:alice' - TestExternalIdentityProvider
  // splits the token on the first ':' as role:identifier, so a single
  // 'student:alice' token resolves to identifier "alice", which matches no
  // seeded user (seed_demo_data.py seeds the literal external_user_id
  // 'student:alice'). Keep DEFAULT_STUDENT_ID in sync with app.js's
  // state.studentId if that ever changes.
  const DEFAULT_STUDENT_ID = 'student:alice';

  function essayHeaders(extra = {}) {
    const accessToken = sessionStorage.getItem('studentAccessToken') || `student:${DEFAULT_STUDENT_ID}`;
    return { 'Authorization': `Bearer ${accessToken}`, ...extra };
  }

  async function essayRequest(path, options = {}) {
    const res = await fetch(path, { ...options, headers: essayHeaders(options.headers || {}) });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const error = new Error(translateDetail(data.detail));
      error.status = res.status;
      throw error;
    }
    return data;
  }

  function submissionState(mySubmission) {
    if (!mySubmission) return { label: 'Enviar redação', badge: 'badge-accent' };
    if (mySubmission.status === 'PENDING_TRANSCRIPTION' || mySubmission.status === 'PENDING_CONFIRMATION') {
      return { label: 'Continuar envio', badge: 'badge-accent' };
    }
    if (mySubmission.status === 'SUBMITTED') return { label: 'Em correção / ver devolutiva', badge: 'badge-primary' };
    return { label: 'Ver detalhes', badge: 'badge-primary' };
  }

  function renderList() {
    if (!prompts.length) {
      container.innerHTML = '<div class="card"><p class="empty-text">Nenhuma proposta de redação aberta para sua turma no momento.</p></div>';
      return;
    }
    container.innerHTML = `<div class="essay-prompt-grid">${prompts.map((p) => {
      const state = submissionState(p.my_submission);
      const dueText = p.due_at ? `<p class="empty-text">Prazo: ${new Date(p.due_at).toLocaleDateString('pt-BR')}</p>` : '';
      return `
        <article class="card essay-prompt-card">
          <h3>${escEssay(p.title)}</h3>
          <p>${escEssay(p.statement)}</p>
          ${dueText}
          <span class="badge ${state.badge}">${state.label}</span>
          <button class="btn btn-primary" type="button" data-open-prompt="${escEssay(p.prompt_assignment_id)}">${state.label}</button>
        </article>`;
    }).join('')}</div>`;

    container.querySelectorAll('[data-open-prompt]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const prompt = prompts.find((p) => p.prompt_assignment_id === btn.dataset.openPrompt);
        openPrompt(prompt);
      });
    });
  }

  function openPrompt(prompt) {
    if (!prompt.my_submission) {
      renderNewSubmissionForm(prompt);
      return;
    }
    if (prompt.my_submission.status === 'SUBMITTED') {
      renderDevolutiva(prompt);
      return;
    }
    renderContinueUpload(prompt);
  }

  function renderNewSubmissionForm(prompt) {
    container.innerHTML = `
      <div class="card essay-form">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <h3>${escEssay(prompt.title)}</h3>
        <p>${escEssay(prompt.statement)}</p>
        <div class="essay-mode-tabs">
          <button class="btn btn-secondary is-active" type="button" data-mode="TYPED">Digitar</button>
          <button class="btn btn-secondary" type="button" data-mode="PHOTO">Fotografar</button>
          <button class="btn btn-secondary" type="button" data-mode="PDF">Enviar PDF</button>
        </div>
        <div id="essay-mode-body"></div>
      </div>`;

    container.querySelector('[data-back]').addEventListener('click', () => renderList());
    container.querySelectorAll('[data-mode]').forEach((btn) => {
      btn.addEventListener('click', () => {
        container.querySelectorAll('[data-mode]').forEach((b) => b.classList.remove('is-active'));
        btn.classList.add('is-active');
        renderModeBody(btn.dataset.mode, prompt);
      });
    });
    renderModeBody('TYPED', prompt);
  }

  function renderModeBody(mode, prompt) {
    const body = container.querySelector('#essay-mode-body');
    if (mode !== 'TYPED') {
      body.innerHTML = '<p class="empty-text">Este modo de envio é adicionado na próxima etapa.</p>';
      return;
    }
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
          body: JSON.stringify({ prompt_assignment_id: prompt.prompt_assignment_id, mode: 'TYPED', text }),
        });
        await loadPrompts();
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
        submitBtn.disabled = false;
      }
    });
  }

  function renderContinueUpload() {
    // Implementado na Task 6.
    container.innerHTML = '<div class="card"><p class="empty-text">Continuar envio de foto/PDF — próxima etapa.</p></div>';
  }

  function renderDevolutiva() {
    // Implementado na Task 7.
    container.innerHTML = '<div class="card"><p class="empty-text">Devolutiva — próxima etapa.</p></div>';
  }

  async function loadPrompts() {
    container.innerHTML = '<p class="empty-text">Carregando propostas...</p>';
    try {
      prompts = await essayRequest('/api/v1/student/essay-prompts');
      renderList();
    } catch (e) {
      container.innerHTML = `<div class="card"><p class="empty-text">${escEssay(e.message)}</p></div>`;
    }
  }

  function init() {
    container = document.getElementById('essay-root');
    if (!container) return;
    loadPrompts();
  }

  return { init };
});
