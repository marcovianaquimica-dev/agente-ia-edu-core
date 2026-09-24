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

  // Shared with renderRejectedDevolutiva below, which shows this same text
  // proactively (via correction.resubmission_allowed) instead of waiting for
  // the student to hit the 409 this message is also used for.
  const RESUBMISSION_NOT_ALLOWED_MSG = 'Esta avaliação não permite reenvio.';

  function translateResubmitError(e) {
    if (e.status === 409) return RESUBMISSION_NOT_ALLOWED_MSG;
    return e.message;
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
    const promptsHtml = prompts.length
      ? `<div class="essay-prompt-grid">${prompts.map((p) => {
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
        }).join('')}</div>`
      : '<div class="card"><p class="empty-text">Nenhuma proposta de redação aberta para sua turma no momento.</p></div>';

    container.innerHTML = `${promptsHtml}<div id="essay-evolution-section" class="essay-evolution-root"><p class="empty-text">Carregando evolução...</p></div>`;

    container.querySelectorAll('[data-open-prompt]').forEach((btn) => {
      btn.addEventListener('click', () => {
        const prompt = prompts.find((p) => p.prompt_assignment_id === btn.dataset.openPrompt);
        openPrompt(prompt);
      });
    });

    loadEvolutionSection();
  }

  async function loadEvolutionSection() {
    const section = container.querySelector('#essay-evolution-section');
    if (!section) return;
    let data;
    try {
      data = await essayRequest('/api/v1/student/essay-evolution');
    } catch (e) {
      section.innerHTML = `<p class="empty-text">${escEssay(e.message)}</p>`;
      return;
    }
    if (!data.entries.length) {
      section.innerHTML = '<p class="empty-text">Vamos ver sua evolução assim que sua primeira redação for corrigida.</p>';
      return;
    }
    let checklistData = { rationales: [], feedbackStrengths: [] };
    try {
      const mostRecent = await essayRequest(
        `/api/v1/student/essay-submissions/${data.entries[0].essay_submission_id}/correction`,
      );
      checklistData = {
        rationales: mostRecent.rationales || [],
        feedbackStrengths: (mostRecent.final_feedback || {}).strengths || [],
      };
    } catch (e) {
      // Checklist degrades to its own empty state below - the timeline and
      // charts above it (already rendered from `data`) don't depend on this
      // second fetch succeeding.
    }
    section.innerHTML = window.EssayEvolution.renderEvolutionSection(data, checklistData);
    window.EssayEvolution.wireEvolutionSection(section, data);
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

  function renderNewSubmissionForm(prompt, resubmitEssayId, resubmitText) {
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
    // resubmitText follows the same threading: the student's own canonical
    // text from the rejected correction, so renderTypedForm can pre-fill the
    // textarea instead of opening blank (only meaningful for a TYPED/
    // TEXT_OFFSET original - PHOTO/PDF originals have no canonical_text, so
    // this is simply undefined for them and the textarea falls through to
    // its normal empty state).
    const state = {
      submissionId: null, anchorMode: null, mode: null,
      resubmitEssayId: resubmitEssayId || null, resubmitText: resubmitText || null,
    };

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

    function switchMode(requestedMode) {
      // Once a real submission exists, its mode is locked (the backend
      // never lets it change) - clicking a different tab re-shows that same
      // submission instead of a blank form that would create a competing one.
      const effectiveMode = state.submissionId ? state.mode : requestedMode;
      container.querySelectorAll('[data-mode]').forEach((b) => b.classList.toggle('is-active', b.dataset.mode === effectiveMode));
      renderModeBody(effectiveMode, prompt, state);
    }

    container.querySelector('[data-back]').addEventListener('click', () => renderList());
    container.querySelectorAll('[data-mode]').forEach((btn) => {
      btn.addEventListener('click', () => switchMode(btn.dataset.mode));
    });
    switchMode('TYPED');
  }

  function renderModeBody(mode, prompt, state) {
    const body = container.querySelector('#essay-mode-body');
    if (mode === 'TYPED') {
      renderTypedForm(body, prompt, state);
      return;
    }
    renderUploadArea(body, prompt, mode, state);
  }

  function renderTypedForm(body, prompt, state) {
    body.innerHTML = `
      <form id="essay-typed-form">
        <div class="form-group">
          <label for="essay-typed-text">Sua redação</label>
          <textarea id="essay-typed-text" rows="16" required>${escEssay(state.resubmitText || '')}</textarea>
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

  async function renderUploadArea(target, prompt, mode, state) {
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
        if (mode === 'PDF') {
          const form = new FormData();
          form.append('file', files[0]);
          await essayRequest(`/api/v1/student/essay-submissions/${state.submissionId}/document`, {
            method: 'POST', body: form,
          });
        } else {
          const existing = await essayRequest(`/api/v1/student/essay-submissions/${state.submissionId}/pages`);
          let nextPage = existing.length + 1;
          for (const file of files) {
            const form = new FormData();
            form.append('page_number', String(nextPage));
            form.append('file', file);
            await essayRequest(`/api/v1/student/essay-submissions/${state.submissionId}/pages`, {
              method: 'POST', body: form,
            });
            nextPage += 1;
          }
        }
        await refreshPages(target, state.submissionId, state.anchorMode);
      } catch (e) {
        msg.hidden = false;
        msg.textContent = translateResubmitError(e);
      }
    });

    if (state.submissionId) {
      // Either resuming an already-created submission (renderContinueUpload)
      // or re-showing this same mode's tab after a submission was already
      // lazily created above - either way, load what's already there.
      await refreshPages(target, state.submissionId, state.anchorMode);
    }
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
    // The submission already exists (this is a resume, not a fresh
    // attempt), so the state carries its real id/mode from the start -
    // my_submission.mode (not a guess) drives which upload UI renders.
    const state = {
      submissionId: prompt.my_submission.id,
      anchorMode: prompt.my_submission.anchor_mode,
      mode: prompt.my_submission.mode,
    };
    renderUploadArea(container, prompt, prompt.my_submission.mode, state);
  }

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
        renderRejectedDevolutiva(prompt, correction);
        return;
      }
      renderApprovedDevolutiva(prompt, correction);
    } catch (e) {
      container.innerHTML = `<div class="card"><p class="empty-text">${escEssay(e.message)}</p></div>`;
    }
  }

  function renderRejectedDevolutiva(prompt, correction) {
    // resubmission_allowed is absent/undefined for older correction payloads
    // (or simply not yet threaded through by a given code path) - treat that
    // as "allowed" so the button still renders rather than silently
    // vanishing; only an explicit false (AVALIATIVO) hides it.
    const canResubmit = correction.resubmission_allowed !== false;
    const resubmitSection = canResubmit
      ? '<button class="btn btn-primary" type="button" id="essay-resubmit-btn">Reenviar redação</button>'
      : `<p class="empty-text">${escEssay(RESUBMISSION_NOT_ALLOWED_MSG)}</p>`;
    container.innerHTML = `
      <div class="card">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <h3>${escEssay(prompt.title)}</h3>
        <p class="empty-text">A correção automática desta redação foi descartada pelo professor. Você pode enviar uma nova versão para receber uma nova correção.</p>
        ${resubmitSection}
      </div>`;
    container.querySelector('[data-back]').addEventListener('click', () => loadPrompts());
    const resubmitBtn = container.querySelector('#essay-resubmit-btn');
    if (resubmitBtn) {
      resubmitBtn.addEventListener('click', () => {
        renderNewSubmissionForm(prompt, prompt.my_submission.essay_id, correction.canonical_text);
      });
    }
  }

  function renderApprovedDevolutiva(prompt, correction) {
    const annotations = correction.annotations || [];
    const anchorMode = prompt.my_submission.anchor_mode;

    const originalContentHtml = anchorMode === 'TEXT_OFFSET'
      ? `<div class="essay-highlighted-text">${window.EssayAnnotations.renderHighlightedText(correction.canonical_text || '', annotations)}</div>`
      : '<div id="essay-original-pages"><p class="empty-text">Carregando páginas...</p></div>';

    const reportHtml = window.EssayReport.renderRichReport(correction, {
      promptTitle: prompt.title, editable: false, escFn: escEssay, originalContentHtml,
    });

    const submissionId = prompt.my_submission.id;
    container.innerHTML = `
      <div class="card">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <button class="btn btn-secondary" type="button" id="essay-export-pdf-btn">Exportar PDF</button>
        ${reportHtml}
      </div>`;

    container.querySelector('[data-back]').addEventListener('click', () => loadPrompts());

    const exportBtn = container.querySelector('#essay-export-pdf-btn');
    if (exportBtn) {
      exportBtn.addEventListener('click', async () => {
        exportBtn.disabled = true;
        try {
          const res = await fetch(`/api/v1/student/essay-submissions/${submissionId}/correction/export.pdf`, {
            headers: essayHeaders(),
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

    if (anchorMode === 'TEXT_OFFSET') {
      window.EssayAnnotations.wirePopovers(container, annotations);
    } else {
      loadOriginalPages(prompt, annotations);
    }
  }

  async function loadOriginalPages(prompt, annotations) {
    const submissionId = prompt.my_submission.id;
    const pagesContainer = container.querySelector('#essay-original-pages');
    let pages = [];
    try {
      pages = await essayRequest(`/api/v1/student/essay-submissions/${submissionId}/pages`);
    } catch (e) {
      pagesContainer.innerHTML = `<p class="empty-text">${escEssay(e.message)}</p>`;
      return;
    }
    pagesContainer.innerHTML = pages.map((p) => `
      <div class="essay-page-image-wrap" data-page-wrap="${p.page_number}">
        <img data-page-image="${p.page_number}" alt="Página ${p.page_number}">
      </div>`).join('') || '<p class="empty-text">Nenhuma página enviada.</p>';

    pagesContainer.querySelectorAll('[data-page-image]').forEach((img) => {
      const pageNumber = Number(img.dataset.pageImage);
      fetch(`/api/v1/student/essay-submissions/${submissionId}/pages/${pageNumber}/image`, {
        headers: essayHeaders(),
      })
        .then((res) => (res.ok ? res.blob() : Promise.reject(new Error('image fetch failed'))))
        .then((blob) => new Promise((resolve, reject) => {
          img.onload = resolve;
          img.onerror = () => reject(new Error('image decode failed'));
          img.src = URL.createObjectURL(blob);
        }))
        .then(() => {
          const wrap = pagesContainer.querySelector(`[data-page-wrap="${pageNumber}"]`);
          window.EssayAnnotations.renderImageMarkers(wrap, img, annotations, pageNumber);
          window.EssayAnnotations.wirePopovers(wrap, annotations);
        })
        .catch(() => { img.alt = `Não foi possível carregar a página ${pageNumber}.`; });
    });
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
