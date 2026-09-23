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

  async function renderModeBody(mode, prompt) {
    const body = container.querySelector('#essay-mode-body');
    if (mode === 'TYPED') {
      renderTypedForm(body, prompt);
      return;
    }
    body.innerHTML = '<p class="empty-text">Preparando envio...</p>';
    try {
      const submission = await essayRequest('/api/v1/student/essay-submissions', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ prompt_assignment_id: prompt.prompt_assignment_id, mode }),
      });
      await renderUploadArea(body, submission.id, submission.anchor_mode, mode);
    } catch (e) {
      body.innerHTML = `<p class="empty-text">${escEssay(e.message)}</p>`;
    }
  }

  function renderTypedForm(body, prompt) {
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

  async function renderUploadArea(target, submissionId, anchorMode, mode) {
    target.innerHTML = `
      <div class="form-group">
        <label for="essay-file-input">${mode === 'PDF' ? 'Arquivo PDF (até 25MB)' : 'Fotos das páginas (até 25MB cada)'}</label>
        <input id="essay-file-input" type="file" ${mode === 'PDF' ? 'accept=".pdf"' : 'accept="image/*" multiple capture="environment"'}>
      </div>
      <p id="essay-upload-msg" class="tm-msg" hidden></p>
      <div id="essay-pages-list"></div>
      <button class="btn btn-primary" type="button" id="essay-confirm-btn" disabled>Confirmar envio</button>
      <p id="essay-confirm-msg" class="tm-msg" hidden></p>`;

    target.querySelector('#essay-file-input').addEventListener('change', async (ev) => {
      const files = Array.from(ev.target.files || []);
      if (!files.length) return;
      const msg = target.querySelector('#essay-upload-msg');
      msg.hidden = true;
      try {
        if (mode === 'PDF') {
          const form = new FormData();
          form.append('file', files[0]);
          await essayRequest(`/api/v1/student/essay-submissions/${submissionId}/document`, {
            method: 'POST', body: form,
          });
        } else {
          const existing = await essayRequest(`/api/v1/student/essay-submissions/${submissionId}/pages`);
          let nextPage = existing.length + 1;
          for (const file of files) {
            const form = new FormData();
            form.append('page_number', String(nextPage));
            form.append('file', file);
            await essayRequest(`/api/v1/student/essay-submissions/${submissionId}/pages`, {
              method: 'POST', body: form,
            });
            nextPage += 1;
          }
        }
        await refreshPages(target, submissionId, anchorMode);
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
      }
    });

    await refreshPages(target, submissionId, anchorMode);
  }

  async function refreshPages(target, submissionId, anchorMode) {
    const list = target.querySelector('#essay-pages-list');
    const confirmBtn = target.querySelector('#essay-confirm-btn');
    let pages = [];
    try {
      pages = await essayRequest(`/api/v1/student/essay-submissions/${submissionId}/pages`);
    } catch (e) {
      list.innerHTML = `<p class="empty-text">${escEssay(e.message)}</p>`;
      return;
    }

    list.innerHTML = pages.map((p) => {
      const ocrText = (p.ocr_tokens || []).map((t) => t.text).join(' ');
      const reviewBlock = anchorMode === 'TEXT_OFFSET' ? `
        <div class="form-group">
          <label for="essay-review-${p.page_number}">Texto revisado (página ${p.page_number})</label>
          <textarea id="essay-review-${p.page_number}" rows="6">${escEssay(p.reviewed_text || ocrText)}</textarea>
        </div>
        <button class="btn btn-secondary" type="button" data-save-review="${p.page_number}">Salvar revisão</button>
        <p class="essay-review-status">${p.reviewed_text ? '✓ Revisado' : 'Pendente de revisão'}</p>
      ` : '<p class="empty-text">Página enviada.</p>';
      return `
        <div class="card essay-page-card" data-page="${p.page_number}">
          <img data-page-image="${p.page_number}" alt="Página ${p.page_number}">
          ${reviewBlock}
        </div>`;
    }).join('');

    // A plain <img src="..."> can't carry the Authorization header this API
    // requires (essayHeaders()/essayRequest() always add it) - the browser's
    // own image fetch is header-less, so the endpoint 403s and the <img>
    // never renders. Fetch each page's bytes through essayRequest's same
    // auth path instead and point the <img> at a local object URL.
    list.querySelectorAll('[data-page-image]').forEach((img) => {
      const pageNumber = img.dataset.pageImage;
      fetch(`/api/v1/student/essay-submissions/${submissionId}/pages/${pageNumber}/image`, {
        headers: essayHeaders(),
      })
        .then((res) => (res.ok ? res.blob() : Promise.reject(new Error('image fetch failed'))))
        .then((blob) => { img.src = URL.createObjectURL(blob); })
        .catch(() => { img.alt = `Não foi possível carregar a página ${pageNumber}.`; });
    });

    list.querySelectorAll('[data-save-review]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const pageNumber = btn.dataset.saveReview;
        const text = list.querySelector(`#essay-review-${pageNumber}`).value.trim();
        btn.disabled = true;
        try {
          await essayRequest(`/api/v1/student/essay-submissions/${submissionId}/pages/${pageNumber}`, {
            method: 'PATCH',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ reviewed_text: text }),
          });
          await refreshPages(target, submissionId, anchorMode);
        } catch (e) {
          btn.disabled = false;
          btn.insertAdjacentHTML('afterend', `<p class="tm-msg">${escEssay(e.message)}</p>`);
        }
      });
    });

    const allReviewed = anchorMode !== 'TEXT_OFFSET' || (pages.length > 0 && pages.every((p) => p.reviewed_text));
    confirmBtn.disabled = !(pages.length > 0 && allReviewed);
    confirmBtn.onclick = async () => {
      const confirmMsg = target.querySelector('#essay-confirm-msg');
      confirmBtn.disabled = true;
      try {
        await essayRequest(`/api/v1/student/essay-submissions/${submissionId}/confirm`, { method: 'POST' });
        await loadPrompts();
      } catch (e) {
        confirmMsg.hidden = false;
        confirmMsg.textContent = e.message;
        confirmBtn.disabled = false;
      }
    };
  }

  function renderContinueUpload(prompt) {
    container.innerHTML = `
      <div class="card essay-form">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <h3>${escEssay(prompt.title)}</h3>
      </div>`;
    container.querySelector('[data-back]').addEventListener('click', () => renderList());
    renderUploadArea(container, prompt.my_submission.id, prompt.my_submission.anchor_mode, 'PHOTO');
  }

  const COMPETENCY_LABELS = {
    C1: 'Domínio da norma padrão', C2: 'Compreensão do tema', C3: 'Argumentação',
    C4: 'Coesão textual', C5: 'Proposta de intervenção',
  };

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
      renderApprovedDevolutiva(prompt, correction);
    } catch (e) {
      container.innerHTML = `<div class="card"><p class="empty-text">${escEssay(e.message)}</p></div>`;
    }
  }

  function renderApprovedDevolutiva(prompt, correction) {
    const scores = correction.final_scores || {};
    const perCompetency = scores.per_competency || {};
    const feedback = correction.final_feedback || {};
    const annotations = correction.annotations || [];
    const alerts = correction.alerts || [];
    const intervention = correction.intervention || {};

    const competencyBars = Object.keys(COMPETENCY_LABELS).map((code) => {
      const points = (perCompetency[code] || {}).points || 0;
      const pct = Math.round((points / 200) * 100);
      return `
        <div class="essay-competency-row">
          <span>${code} — ${COMPETENCY_LABELS[code]}</span>
          <div class="essay-competency-bar"><div class="essay-competency-fill" style="width:${pct}%"></div></div>
          <span>${points}/200</span>
        </div>`;
    }).join('');

    const alertsHtml = alerts.length
      ? `<div class="essay-alerts">${alerts.map((a) => `<span class="badge badge-accent">${escEssay(a.code)}</span>`).join(' ')}</div>`
      : '';

    const annotationsHtml = annotations.length
      ? annotations.map((a) => {
          const quote = (a.anchor && (a.anchor.quote || a.anchor.read_text)) || '';
          return `
            <div class="essay-annotation">
              <strong>${escEssay(a.letter)} — ${escEssay(a.competency_code)}</strong>
              <p>${escEssay(a.short_comment)}</p>
              <p class="empty-text">${escEssay(a.long_comment)}</p>
              ${quote ? `<blockquote>"${escEssay(quote)}"</blockquote>` : ''}
            </div>`;
        }).join('')
      : '<p class="empty-text">Nenhuma anotação específica.</p>';

    const interventionHtml = `
      <ul class="essay-intervention-checklist">
        <li>${intervention.agente ? '✓' : '○'} Agente: ${escEssay(intervention.agente || '—')}</li>
        <li>${intervention.acao ? '✓' : '○'} Ação: ${escEssay(intervention.acao || '—')}</li>
        <li>${intervention.meio_modo ? '✓' : '○'} Meio/modo: ${escEssay(intervention.meio_modo || '—')}</li>
        <li>${intervention.finalidade ? '✓' : '○'} Finalidade: ${escEssay(intervention.finalidade || '—')}</li>
        <li>${intervention.detalhamento ? '✓' : '○'} Detalhamento: ${escEssay(intervention.detalhamento || '—')}</li>
      </ul>
      <p class="${intervention.respeita_direitos_humanos ? '' : 'essay-warning'}">
        ${intervention.respeita_direitos_humanos ? '✓ Respeita os direitos humanos' : '⚠ Atenção: verificar respeito aos direitos humanos'}
      </p>`;

    container.innerHTML = `
      <div class="card">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <h3>${escEssay(prompt.title)}</h3>
        <div class="essay-total-score">Nota total: ${scores.total != null ? scores.total : '—'} / 1000</div>
        ${alertsHtml}
        <h4>Notas por competência</h4>
        ${competencyBars}
        <h4>Pontos fortes</h4>
        <ul>${(feedback.strengths || []).map((s) => `<li>${escEssay(s)}</li>`).join('') || '<li class="empty-text">—</li>'}</ul>
        <h4>A melhorar</h4>
        <ul>${(feedback.improvements || []).map((s) => `<li>${escEssay(s)}</li>`).join('') || '<li class="empty-text">—</li>'}</ul>
        <h4>Próxima redação</h4>
        <p>${escEssay(feedback.next_essay_strategy || '')}</p>
        <h4>Anotações</h4>
        ${annotationsHtml}
        <h4>Competência 5 — Proposta de intervenção</h4>
        ${interventionHtml}
      </div>`;

    container.querySelector('[data-back]').addEventListener('click', () => loadPrompts());
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
