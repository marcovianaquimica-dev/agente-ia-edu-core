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

  // Same labels/styles teacher.js's own content-mastery action plan already
  // uses (PRIORITY_LABEL/PRIORITY_STYLE there, applied via .plan-column
  // .column-danger/-warning/-success) - kept as a local copy here since
  // teacher.js declares them as plain top-level `const`s, never attached to
  // `window`, so this module (loaded as a separate script) can't reach them.
  const DASH_PRIORITY_LABEL = { HIGH: 'Alta', MEDIUM: 'Média', LOW: 'Baixa' };
  const DASH_PRIORITY_STYLE = { HIGH: 'danger', MEDIUM: 'warning', LOW: 'success' };
  // Same hex values essay-evolution.js's COMPETENCY_COLORS already uses for
  // C1-C5, kept as a local literal copy for the same reason (that module
  // only exports renderEvolutionSection/wireEvolutionSection, not its color
  // map or chart primitives).
  const DASH_COMPETENCY_COLORS = { C1: '#4f46e5', C2: '#06b6d4', C3: '#ef4444', C4: '#f59e0b', C5: '#10b981' };
  const DASH_COMPETENCY_CODES = ['C1', 'C2', 'C3', 'C4', 'C5'];

  // Display-only translation - the values stored/sent to the API stay the
  // English DB enum values (EssayPrompt.status, PromptAssignment.status),
  // only what the teacher reads on screen changes.
  const STATUS_LABEL = {
    DRAFT: 'Rascunho', ACTIVE: 'Ativa', SUPERSEDED: 'Substituída',
    OPEN: 'Aberta', CLOSED: 'Encerrada',
  };

  function statusLabel(status) {
    return STATUS_LABEL[status] || status;
  }

  function tmEsc(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[character]);
  }

  // GET /api/v1/catalog/essay-prompts (Task 6) devolve, na mesma lista, as
  // propostas da plataforma ACTIVE que esta escola ainda nao materializou -
  // so pre-visualizacao, sem EssayPrompt real por tras. Como ainda nao
  // existe um essay_prompts.id real pra elas, o backend devolve o proprio
  // platform_prompt_id como id (ver list_essay_prompts em
  // api/routes/essay_prompts.py). Numa copia ja materializada, id e o id
  // real da copia - sempre diferente do platform_prompt_id de origem.
  function isUnmaterializedPlatformPrompt(p) {
    return !!p.is_platform && p.id === p.platform_prompt_id;
  }

  function translateDetail(detail) {
    if (typeof detail === 'string') {
      const moduleMatch = detail.match(/^Module '(.+)' is not enabled for the current school\.$/);
      if (moduleMatch) return `O módulo '${moduleMatch[1]}' não está habilitado para esta escola.`;
      if (detail.includes('does not allow disabling teacher review per proposal')) {
        return 'Esta escola não permite desativar a revisão docente por proposta - a opção "Exigir revisão docente" precisa continuar marcada.';
      }
      if (/^Class not found in school /.test(detail)) {
        return 'Turma não encontrada nesta escola.';
      }
      if (/^EssayPrompt .+ is already assigned to class /.test(detail)) {
        return 'Esta proposta já está atribuída a essa turma.';
      }
      if (/^EssayPrompt not found in school /.test(detail)) {
        return 'Proposta não encontrada nesta escola.';
      }
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

  // A ORDEM, OS ROTULOS E O QUE E SECUNDARIO ficam em `RedacaoNav`, que tem
  // teste proprio. Aqui so se desenha o que ela decidiu.
  //
  // As CHAVES continuam as mesmas (`prompts`, `queue`, ...): elas sao o
  // contrato com `wireTabs` logo abaixo, e renomea-las seria risco cosmetico.
  function renderTabs(activeTab) {
    const nav = window.RedacaoNav;
    const area = (a) => `
        <button class="er-aba${a.ativa ? ' er-aba-ativa' : ''}" type="button"
                data-tab="${a.aba}"
                ${a.ativa ? 'aria-current="page"' : ''}>${tmEsc(a.rotulo)}</button>`;
    const secundario = (s) => `
          <button class="er-secundario${s.ativa ? ' er-aba-ativa' : ''}"
                  type="button" data-tab="${s.aba}"
                  ${s.ativa ? 'aria-current="page"' : ''}>${tmEsc(s.rotulo)}</button>`;
    return `
      <nav class="essay-review-tabs" aria-label="Áreas da Redação">
        <div class="er-abas">${nav.areas(activeTab).map(area).join('')}</div>
        <details class="er-mais"${nav.secundarios(activeTab)
          .some((s) => s.ativa) ? ' open' : ''}>
          <summary aria-label="Mais opções">⋯</summary>
          <div class="er-mais-itens">
            ${nav.secundarios(activeTab).map(secundario).join('')}
          </div>
        </details>
      </nav>`;
  }

  function wireTabs() {
    container.querySelectorAll('[data-tab]').forEach((btn) => {
      btn.addEventListener('click', () => {
        stopBatchPolling();
        if (btn.dataset.tab === 'prompts') renderPromptsList();
        if (btn.dataset.tab === 'batch') renderBatchTab();
        if (btn.dataset.tab === 'queue') renderReviewQueue();
        if (btn.dataset.tab === 'evolution') renderEvolutionTab();
        if (btn.dataset.tab === 'dashboard') renderDashboardTab();
        if (btn.dataset.tab === 'trash') renderTrashTab();
      });
    });
  }

  async function renderPromptsList() {
    container.innerHTML = `${renderTabs('prompts')}<p class="empty-text">Carregando propostas...</p>`;
    wireTabs();
    const tabsEl = container.querySelector('.essay-review-tabs');
    if (tabsEl.nextElementSibling) tabsEl.nextElementSibling.remove();
    tabsEl.insertAdjacentHTML('afterend', `
      <div class="card tm-form" id="er-prompt-entry">
        <h3>Propostas de redação</h3>
        <div class="tm-form-actions">
          <button class="btn btn-primary" type="button" id="er-create-prompt-btn">Criar proposta</button>
          <button class="btn btn-secondary" type="button" id="er-use-bank-btn">Usar proposta do banco</button>
          <button class="btn btn-secondary" type="button" id="er-generic-sheet-btn">Baixar folha de redação em branco</button>
        </div>
        <span id="er-generic-sheet-msg" class="tm-msg" hidden></span>
      </div>`);
    container.querySelector('#er-create-prompt-btn').addEventListener('click', renderNewPromptForm);
    container.querySelector('#er-use-bank-btn').addEventListener('click', renderPromptBankList);
    container.querySelector('#er-generic-sheet-btn').addEventListener('click', async () => {
      const btn = container.querySelector('#er-generic-sheet-btn');
      const msg = container.querySelector('#er-generic-sheet-msg');
      msg.hidden = true;
      btn.disabled = true;
      try {
        const res = await fetch('/api/v1/catalog/essay-prompts/answer-sheet.pdf', { headers: reviewHeaders() });
        if (!res.ok) throw new Error('Não foi possível gerar a folha de resposta.');
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = 'folha-de-redacao.pdf';
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
      } finally {
        btn.disabled = false;
      }
    });
  }

  async function renderTrashTab() {
    container.innerHTML = `${renderTabs('trash')}<p class="empty-text">Carregando lixeira...</p>`;
    wireTabs();
    let trashed;
    try {
      trashed = await reviewRequest('/api/v1/catalog/essay-prompts/trash');
    } catch (e) {
      container.innerHTML = `${renderTabs('trash')}<p class="empty-text">${tmEsc(e.message)}</p>`;
      wireTabs();
      return;
    }
    const tabsEl = container.querySelector('.essay-review-tabs');
    if (tabsEl.nextElementSibling) tabsEl.nextElementSibling.remove();
    tabsEl.insertAdjacentHTML('afterend', `
      <p class="empty-text" style="margin:12px 0;">Propostas excluídas ficam aqui por 30 dias - o tema, os materiais, as turmas atribuídas e as redações/correções dos alunos são preservados e voltam exatamente como estavam ao restaurar.</p>
      <div class="tm-table-wrap" style="overflow-x:auto;">
        <table class="tm-table">
          <thead><tr><th>Título</th><th>Ano</th><th>Dias restantes</th><th></th></tr></thead>
          <tbody id="er-trash-body">
            ${trashed.map((p) => `
              <tr>
                <td>${tmEsc(p.title)}</td><td>${p.year}</td>
                <td>${p.days_remaining}</td>
                <td><button class="btn btn-primary" type="button" data-restore-prompt="${tmEsc(p.id)}">Restaurar</button></td>
              </tr>`).join('') || '<tr><td colspan="4" class="empty-text">Lixeira vazia.</td></tr>'}
          </tbody>
        </table>
      </div>`);

    container.querySelectorAll('[data-restore-prompt]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          await reviewRequest(`/api/v1/catalog/essay-prompts/${btn.dataset.restorePrompt}/restore`, { method: 'POST' });
          renderTrashTab();
        } catch (e) {
          alert(e.message);
          btn.disabled = false;
        }
      });
    });
  }

  // Intervalo de consulta do progresso do lote. 2s: o processamento gasta
  // alguns segundos por pagina (OCR de cabecalho + corpo, com retentativa),
  // entao consultar mais rapido so geraria requisicao a toa.
  const BATCH_POLL_MS = 2000;
  let batchPollTimer = null;

  function stopBatchPolling() {
    if (batchPollTimer) {
      clearTimeout(batchPollTimer);
      batchPollTimer = null;
    }
  }

  async function renderBatchTab() {
    stopBatchPolling();
    container.innerHTML = `${renderTabs('batch')}<p class="empty-text">Carregando...</p>`;
    wireTabs();

    let promptOptions = [];
    let classrooms = [];
    let gradeLevels = [];
    try {
      promptOptions = await reviewRequest('/api/v1/catalog/essay-prompts');
    } catch (e) {
      promptOptions = [];
    }
    try {
      gradeLevels = await reviewRequest('/api/v1/teacher/essay-batches/grade-levels');
    } catch (e) {
      gradeLevels = [];
    }
    // Uma proposta da plataforma ainda nao materializada nesta escola nao
    // tem PromptAssignment nenhuma pra nenhuma turma - o backend rejeitaria
    // o envio em lote com 422, mas so depois do professor ja ter subido ate
    // 60 paginas de fotos. Tira essas da lista antes.
    promptOptions = promptOptions.filter((p) => !isUnmaterializedPlatformPrompt(p));
    try {
      classrooms = await reviewRequest(
        `/api/v1/teacher/classrooms?school_id=${encodeURIComponent(schoolId)}&academic_year=2026`,
      );
    } catch (e) {
      classrooms = [];
    }
    // Mesmo filtro da tela de atribuicao: so turmas que ja resolvem para uma
    // Class real podem receber um lote (o lote guarda class_id como FK).
    const assignableClassrooms = classrooms.filter((c) => c.class_id);

    const tabsEl = container.querySelector('.essay-review-tabs');
    if (tabsEl.nextElementSibling) tabsEl.nextElementSibling.remove();
    tabsEl.insertAdjacentHTML('afterend', `
      <div class="card tm-form">
        <h3>Enviar redações em lote</h3>
        <p class="empty-text">Suba as fotos ou o PDF escaneado das folhas de uma turma inteira. O sistema identifica cada aluno pelo nome escrito no cabeçalho da folha, junta as páginas seguidas de um mesmo aluno em uma redação só e manda direto para correção. As folhas que ele não conseguir identificar ficam numa lista aqui embaixo para você escolher o aluno. Máximo de 60 páginas por envio.</p>
        <form id="er-batch-form">
          <div class="form-group">
            <label for="er-batch-prompt">Proposta</label>
            <select id="er-batch-prompt" class="text-input" required>
              ${promptOptions.map((p) => `<option value="${tmEsc(p.id)}">${tmEsc(p.title)} (${p.year})</option>`).join('')}
            </select>
          </div>
          <div class="form-group">
            <label for="er-batch-scope">Abrangência</label>
            <select id="er-batch-scope" class="text-input">
              <option value="turma">Uma turma</option>
              <option value="serie">Uma série inteira</option>
              <option value="escola">A escola inteira</option>
            </select>
          </div>
          <div class="form-group" id="er-batch-class-group">
            <label for="er-batch-class">Turma</label>
            <select id="er-batch-class" class="text-input">
              ${assignableClassrooms.map((c) => `<option value="${tmEsc(c.class_id)}">${tmEsc(c.name)}</option>`).join('')}
            </select>
          </div>
          <div class="form-group" id="er-batch-grade-group" hidden>
            <label for="er-batch-grade-level">Série</label>
            <select id="er-batch-grade-level" class="text-input">
              ${gradeLevels.map((g) => `<option value="${tmEsc(g.id)}">${tmEsc(g.name)}</option>`).join('')}
            </select>
          </div>
          <div class="form-group">
            <label for="er-batch-files">Arquivos (fotos JPG/PNG e/ou PDF)</label>
            <input id="er-batch-files" class="text-input" type="file" accept=".png,.jpg,.jpeg,.pdf" multiple required>
          </div>
          <button class="btn btn-primary" type="submit">Enviar lote</button>
          <p id="er-batch-msg" class="tm-msg" hidden></p>
        </form>
      </div>
      <div id="er-batch-progress"></div>`);

    container.querySelector('#er-batch-scope').addEventListener('change', (ev) => {
      const scope = ev.target.value;
      container.querySelector('#er-batch-class-group').hidden = scope !== 'turma';
      container.querySelector('#er-batch-grade-group').hidden = scope !== 'serie';
    });

    container.querySelector('#er-batch-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const msg = container.querySelector('#er-batch-msg');
      const submitBtn = ev.target.querySelector('button[type="submit"]');
      const files = container.querySelector('#er-batch-files').files;
      msg.hidden = true;
      if (!files.length) return;
      const formData = new FormData();
      formData.append('essay_prompt_id', container.querySelector('#er-batch-prompt').value);
      const scope = container.querySelector('#er-batch-scope').value;
      if (scope === 'turma') {
        const classId = container.querySelector('#er-batch-class').value;
        if (!classId) {
          msg.hidden = false;
          msg.textContent = 'Selecione uma turma.';
          return;
        }
        formData.append('class_id', classId);
      } else if (scope === 'serie') {
        const gradeLevelId = container.querySelector('#er-batch-grade-level').value;
        if (!gradeLevelId) {
          msg.hidden = false;
          msg.textContent = 'Selecione uma série.';
          return;
        }
        formData.append('grade_level_id', gradeLevelId);
      }
      Array.from(files).forEach((file) => formData.append('files', file));
      submitBtn.disabled = true;
      try {
        const batch = await reviewRequest('/api/v1/teacher/essay-batches', {
          method: 'POST', body: formData,
        });
        pollBatch(batch.id);
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
      } finally {
        submitBtn.disabled = false;
      }
    });
  }

  async function pollBatch(batchId) {
    stopBatchPolling();
    let data;
    try {
      data = await reviewRequest(`/api/v1/teacher/essay-batches/${batchId}`);
    } catch (e) {
      const target = container.querySelector('#er-batch-progress');
      if (target) target.innerHTML = `<p class="empty-text">${tmEsc(e.message)}</p>`;
      return;
    }
    renderBatchProgress(data);
    if (data.status === 'PROCESSING') {
      batchPollTimer = setTimeout(() => pollBatch(batchId), BATCH_POLL_MS);
    }
  }

  function renderBatchProgress(data) {
    const target = container.querySelector('#er-batch-progress');
    if (!target) return;
    const processing = data.status === 'PROCESSING';
    // processed_count (nao matched_count + needs_review_count) e o contador
    // real de progresso: enquanto o lote esta PROCESSING, uma pagina que o
    // OCR ainda nem visitou nao entra em nenhum dos dois - so em
    // processed_count quando ela realmente foi lida (Problema 2a do
    // fix-round-1-brief.md, spec s5: GET .../essay-batches/{id} sempre
    // reflete o progresso real, mesmo com o lote ainda rodando).
    const done = data.processed_count;
    // Defesa em profundidade (Problema 2b): o backend ja filtra fora de
    // needs_review_pages qualquer pagina com matched_student_id preenchido
    // (ja casada, so ainda nao materializada porque o lote esta
    // PROCESSING), mas a fila de resolucao nunca deve confiar cegamente
    // nisso - resolver essa pagina de novo sobrescreveria um match que ja
    // estava certo.
    const resolvableNeedsReview = data.needs_review_pages.filter((page) => !page.matched_student_id);
    const studentOptions = data.available_students
      .map((s) => `<option value="${tmEsc(s.student_id)}">${tmEsc(s.full_name)}${s.document_number ? ` — CPF ${tmEsc(s.document_number)}` : ''}</option>`)
      .join('');

    target.innerHTML = `
      <div class="card">
        <h4>${processing ? 'Processando o lote...' : 'Lote processado'}</h4>
        <p class="empty-text">${done} de ${data.total_pages} páginas lidas — ${data.matched_count} identificadas, ${data.needs_review_count} aguardando você.</p>
        ${resolvableNeedsReview.length ? `
        <h4>Folhas que o sistema não conseguiu identificar</h4>
        <div class="tm-table-wrap" style="overflow-x:auto;">
          <table class="tm-table">
            <thead><tr><th>Página</th><th>Folha</th><th>Nome lido</th><th>CPF lido</th><th>Aluno</th><th></th></tr></thead>
            <tbody>
              ${resolvableNeedsReview.map((page) => `
                <tr data-batch-page="${tmEsc(page.id)}">
                  <td>${page.page_number}</td>
                  <td><a href="/api/v1/teacher/essay-batches/${tmEsc(data.id)}/pages/${tmEsc(page.id)}/image" target="_blank" rel="noopener" data-page-image="${tmEsc(page.id)}">ver folha</a></td>
                  <td>${tmEsc(page.ocr_name_raw || '—')}</td>
                  <td>${tmEsc(page.ocr_cpf_raw || '—')}</td>
                  <td>
                    <select class="text-input" data-student-select="${tmEsc(page.id)}" ${page.has_text ? '' : 'disabled'}>
                      <option value="">Selecione...</option>${studentOptions}
                    </select>
                  </td>
                  <td>
                    ${page.has_text
                      ? `<button class="btn btn-primary" type="button" data-resolve-page="${tmEsc(page.id)}">Confirmar</button>`
                      : '<span class="empty-text">Sem texto legível — reenvie esta folha em outro lote.</span>'}
                  </td>
                </tr>`).join('')}
            </tbody>
          </table>
        </div>` : (processing ? '' : '<p class="empty-text">Todas as folhas foram identificadas automaticamente.</p>')}
      </div>`;

    // A imagem da folha e servida por uma rota autenticada, entao um <a href>
    // simples abriria sem o cabecalho Authorization - buscamos o blob e abrimos
    // a URL de objeto, mesmo caminho que o export de XLSX ja usa.
    target.querySelectorAll('[data-page-image]').forEach((link) => {
      link.addEventListener('click', async (ev) => {
        ev.preventDefault();
        try {
          const res = await fetch(link.getAttribute('href'), { headers: reviewHeaders() });
          if (!res.ok) throw new Error('Não foi possível abrir a imagem da folha.');
          const url = URL.createObjectURL(await res.blob());
          window.open(url, '_blank', 'noopener');
        } catch (e) {
          alert(e.message);
        }
      });
    });

    target.querySelectorAll('[data-resolve-page]').forEach((btn) => {
      btn.addEventListener('click', async () => {
        const pageId = btn.dataset.resolvePage;
        const select = target.querySelector(`[data-student-select="${pageId}"]`);
        if (!select.value) {
          alert('Escolha o aluno desta folha.');
          return;
        }
        btn.disabled = true;
        try {
          const updated = await reviewRequest(
            `/api/v1/teacher/essay-batches/${data.id}/pages/${pageId}/resolve`,
            {
              method: 'POST', headers: { 'Content-Type': 'application/json' },
              body: JSON.stringify({ student_id: select.value }),
            },
          );
          renderBatchProgress(updated);
        } catch (e) {
          alert(e.message);
          btn.disabled = false;
        }
      });
    });
  }

  // Seletor de publico combinado (series + turmas + alunos) reaproveitado
  // pela tela de criar proposta E pela tela de usar proposta do banco.
  // Devolve {getTargets} - o form que o usa le getTargets() no submit.
  async function renderAudiencePicker(container) {
    let gradeLevels = [];
    let classrooms = [];
    try {
      gradeLevels = await reviewRequest('/api/v1/teacher/essay-batches/grade-levels');
    } catch (e) {
      gradeLevels = [];
    }
    try {
      classrooms = await reviewRequest(
        `/api/v1/teacher/classrooms?school_id=${encodeURIComponent(schoolId)}&academic_year=2026`,
      );
    } catch (e) {
      classrooms = [];
    }
    const assignableClassrooms = classrooms.filter((c) => c.class_id);
    const selectedStudents = new Map();

    container.innerHTML = `
      <div class="form-group">
        <label>Séries</label>
        <div id="er-audience-series" class="essay-class-checklist">
          ${gradeLevels.map((g) => `<label class="essay-class-check"><input type="checkbox" data-grade-id="${tmEsc(g.id)}"> ${tmEsc(g.name)}</label>`).join('') || '<p class="empty-text">Nenhuma série disponível.</p>'}
        </div>
      </div>
      <div class="form-group">
        <label>Turmas</label>
        <div id="er-audience-classes" class="essay-class-checklist">
          ${assignableClassrooms.map((c) => `<label class="essay-class-check"><input type="checkbox" data-class-id="${tmEsc(c.class_id)}"> ${tmEsc(c.name)}</label>`).join('') || '<p class="empty-text">Nenhuma turma disponível.</p>'}
        </div>
      </div>
      <div class="form-group">
        <label for="er-audience-students-search">Alunos específicos</label>
        <input id="er-audience-students-search" class="text-input" placeholder="Buscar aluno por nome...">
        <div id="er-audience-students-results"></div>
        <div id="er-audience-students-selected"></div>
      </div>`;

    function renderSelectedStudents() {
      const box = container.querySelector('#er-audience-students-selected');
      box.innerHTML = Array.from(selectedStudents.values()).map((s) => `
        <span class="er-chip" data-selected-student-id="${tmEsc(s.student_id)}">
          ${tmEsc(s.full_name)} (${tmEsc(s.class_name)}) <button type="button" data-remove-student="${tmEsc(s.student_id)}">&times;</button>
        </span>`).join('');
      box.querySelectorAll('[data-remove-student]').forEach((btn) => {
        btn.addEventListener('click', () => {
          selectedStudents.delete(btn.dataset.removeStudent);
          renderSelectedStudents();
        });
      });
    }

    function currentClassIds() {
      return Array.from(container.querySelectorAll('#er-audience-classes input:checked'))
        .map((el) => el.dataset.classId);
    }

    container.querySelector('#er-audience-students-search').addEventListener('input', async (ev) => {
      const q = ev.target.value.trim();
      const resultsBox = container.querySelector('#er-audience-students-results');
      if (!q) {
        resultsBox.innerHTML = '';
        return;
      }
      const classIdsParam = assignableClassrooms.map((c) => c.class_id)
        .concat(currentClassIds())
        .filter((v, i, arr) => arr.indexOf(v) === i);
      const params = new URLSearchParams();
      classIdsParam.forEach((id) => params.append('class_ids', id));
      params.set('q', q);
      let results = [];
      try {
        results = await reviewRequest(`/api/v1/catalog/essay-prompts/students-search?${params.toString()}`);
      } catch (e) {
        results = [];
      }
      resultsBox.innerHTML = results.map((s) => `
        <button type="button" class="btn btn-secondary" data-pick-student="${tmEsc(s.student_id)}"
          data-pick-name="${tmEsc(s.full_name)}" data-pick-class="${tmEsc(s.class_name)}">
          ${tmEsc(s.full_name)} (${tmEsc(s.class_name)})
        </button>`).join('');
      resultsBox.querySelectorAll('[data-pick-student]').forEach((btn) => {
        btn.addEventListener('click', () => {
          selectedStudents.set(btn.dataset.pickStudent, {
            student_id: btn.dataset.pickStudent, full_name: btn.dataset.pickName,
            class_name: btn.dataset.pickClass,
          });
          renderSelectedStudents();
          resultsBox.innerHTML = '';
          container.querySelector('#er-audience-students-search').value = '';
        });
      });
    });

    return {
      getTargets: () => ({
        grade_level_ids: Array.from(container.querySelectorAll('#er-audience-series input:checked'))
          .map((el) => el.dataset.gradeId),
        class_ids: currentClassIds(),
        student_ids: Array.from(selectedStudents.keys()),
      }),
    };
  }

  async function renderPromptBankList() {
    container.innerHTML = `${renderTabs('prompts')}<p class="empty-text">Carregando propostas...</p>`;
    wireTabs();
    try {
      prompts = await reviewRequest('/api/v1/catalog/essay-prompts');
    } catch (e) {
      prompts = [];
    }
    const tabsEl = container.querySelector('.essay-review-tabs');
    if (tabsEl.nextElementSibling) tabsEl.nextElementSibling.remove();
    tabsEl.insertAdjacentHTML('afterend', `
      <div class="card tm-form">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <h3>Usar proposta do banco</h3>
        <input id="er-bank-filter" class="text-input" placeholder="Filtrar por título...">
        <ul id="er-bank-list" class="tm-table"></ul>
      </div>`);
    container.querySelector('[data-back]').addEventListener('click', renderPromptsList);

    async function deletePrompt(btn) {
      const prompt = prompts.find((p) => p.id === btn.dataset.deletePrompt);
      if (!confirm(`Mover "${prompt ? prompt.title : 'esta proposta'}" para a lixeira? Ela ficará disponível para restaurar por 30 dias.`)) return;
      btn.disabled = true;
      try {
        await reviewRequest(`/api/v1/catalog/essay-prompts/${btn.dataset.deletePrompt}`, { method: 'DELETE' });
        renderPromptBankList();
      } catch (e) {
        alert(e.message);
        btn.disabled = false;
      }
    }
    function renderList(filterText) {
      const list = container.querySelector('#er-bank-list');
      const q = filterText.trim().toLowerCase();
      const filtered = prompts.filter((p) => !q || p.title.toLowerCase().includes(q));
      list.innerHTML = filtered.map((p) => `<li>
        <button class="btn btn-secondary" type="button" data-pick-prompt="${tmEsc(p.id)}">${tmEsc(p.title)} (${p.year})${p.is_platform ? ' <span class="er-platform-badge">Plataforma</span>' : ''}</button>
        ${p.is_platform ? '' : `<button class="btn btn-secondary" type="button" data-delete-prompt="${tmEsc(p.id)}" title="Mover para a lixeira">🗑️</button>`}
      </li>`).join('') || '<li class="empty-text">Nenhuma proposta encontrada.</li>';
      list.querySelectorAll('[data-pick-prompt]').forEach((btn) => btn.addEventListener('click', () => renderBankAssignScreen(btn.dataset.pickPrompt)));
      list.querySelectorAll('[data-delete-prompt]').forEach((btn) => btn.addEventListener('click', () => deletePrompt(btn)));
    }
    renderList('');
    container.querySelector('#er-bank-filter').addEventListener('input', (ev) => renderList(ev.target.value));
  }

  async function renderBankAssignScreen(promptId) {
    container.innerHTML = `${renderTabs('prompts')}<p class="empty-text">Carregando...</p>`;
    wireTabs();
    const tabsEl = container.querySelector('.essay-review-tabs');
    if (tabsEl.nextElementSibling) tabsEl.nextElementSibling.remove();
    tabsEl.insertAdjacentHTML('afterend', `
      <div class="card tm-form">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <h3>Atribuir proposta</h3>
        <div id="er-bank-audience-container"></div>
        <button class="btn btn-primary" type="button" id="er-bank-assign-btn">Atribuir</button>
        <p id="er-bank-assign-msg" class="tm-msg" hidden></p>
      </div>`);
    container.querySelector('[data-back]').addEventListener('click', renderPromptBankList);

    const audiencePicker = await renderAudiencePicker(container.querySelector('#er-bank-audience-container'));
    container.querySelector('#er-bank-assign-btn').addEventListener('click', async () => {
      const msg = container.querySelector('#er-bank-assign-msg');
      const targets = audiencePicker.getTargets();
      if (!targets.class_ids.length && !targets.grade_level_ids.length && !targets.student_ids.length) {
        msg.hidden = false;
        msg.textContent = 'Escolha pelo menos uma série, turma ou aluno.';
        return;
      }
      try {
        await reviewRequest(`/api/v1/catalog/essay-prompts/${promptId}/assignments/combined`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(targets),
        });
        renderPromptDetail(promptId);
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
      }
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
          <div class="form-group"><label for="er-material-file">Texto motivador (PDF, opcional)</label><input id="er-material-file" class="text-input" type="file"></div>
          <div id="er-audience-container"></div>
          <button class="btn btn-primary" type="submit">Criar e atribuir</button>
          <p id="er-new-prompt-msg" class="tm-msg" hidden></p>
        </form>
      </div>`;
    wireTabs();
    container.querySelector('[data-back]').addEventListener('click', renderPromptsList);

    let audiencePicker = null;
    renderAudiencePicker(container.querySelector('#er-audience-container')).then((p) => {
      audiencePicker = p;
    });

    container.querySelector('#er-new-prompt-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const msg = container.querySelector('#er-new-prompt-msg');
      const submitBtn = ev.target.querySelector('button[type="submit"]');
      submitBtn.disabled = true;
      const targets = audiencePicker ? audiencePicker.getTargets() : { class_ids: [], grade_level_ids: [], student_ids: [] };
      if (!targets.class_ids.length && !targets.grade_level_ids.length && !targets.student_ids.length) {
        msg.hidden = false;
        msg.textContent = 'Escolha pelo menos uma série, turma ou aluno.';
        submitBtn.disabled = false;
        return;
      }
      try {
        const prompt = await reviewRequest('/api/v1/catalog/essay-prompts', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            title: container.querySelector('#er-title').value.trim(),
            statement: container.querySelector('#er-statement').value.trim(),
            year: Number(container.querySelector('#er-year').value),
          }),
        });
        const fileInput = container.querySelector('#er-material-file');
        if (fileInput.files && fileInput.files[0]) {
          const formData = new FormData();
          formData.append('position', '0');
          formData.append('file', fileInput.files[0]);
          await reviewRequest(`/api/v1/catalog/essay-prompts/${prompt.id}/materials/upload`, {
            method: 'POST', body: formData,
          });
        }
        await reviewRequest(`/api/v1/catalog/essay-prompts/${prompt.id}/assignments/combined`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(targets),
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
    // classrooms only carries a real class_id when the free-text scope code
    // already resolved to an academic-hierarchy Class row (see
    // ClassroomSummaryItem.class_id's docstring) - only those can be
    // assigned a proposal (PromptAssignment.class_id is a real FK).
    const assignableClassrooms = classrooms.filter((c) => c.class_id);
    const classNameById = new Map(assignableClassrooms.map((c) => [c.class_id, c.name]));

    const detailHtml = document.createElement('div');
    detailHtml.innerHTML = `
      <div class="card tm-detail-grid">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <h3>${tmEsc(detail.title)}</h3>
        <p>${tmEsc(detail.statement)}</p>
        <p class="empty-text">Status: ${tmEsc(statusLabel(detail.status))}</p>
        <div class="tm-form-actions" id="er-sheet-actions" style="margin: 8px 0;">
          <label for="er-sheet-copies" style="margin-right:6px;">Cópias</label>
          <input id="er-sheet-copies" class="text-input" type="number" min="1" max="200" value="30" style="width:80px;display:inline-block;">
          <button class="btn btn-secondary" type="button" id="er-answer-sheet-btn">Gerar folha de resposta</button>
          <input id="er-logo-file" type="file" accept="image/png,image/jpeg" hidden>
          <button class="btn btn-secondary" type="button" id="er-logo-btn">Enviar logo da escola</button>
          <span id="er-sheet-msg" class="tm-msg" hidden></span>
        </div>

        <h4>Materiais de apoio</h4>
        <ul id="er-materials-list">${detail.materials.map((m) => `<li>${renderMaterialLabel(m)}</li>`).join('') || '<li class="empty-text">Nenhum material.</li>'}</ul>
        <form id="er-material-form" class="tm-form-row">
          <div class="form-group"><label for="er-material-content">Adicionar material de texto</label><textarea id="er-material-content" class="textarea-input" rows="3"></textarea></div>
          <div class="form-group"><label for="er-material-file">Ou enviar arquivo (PDF, imagem, reportagem...)</label><input id="er-material-file" class="text-input" type="file"></div>
          <button class="btn btn-secondary" type="submit">Adicionar</button>
        </form>
        <p id="er-material-msg" class="tm-msg" hidden></p>

        <h4>Turmas atribuídas</h4>
        <ul id="er-assignments-list">${detail.assignments.map((a) => `<li>${tmEsc(a.class_id ? (classNameById.get(a.class_id) || a.class_id) : 'Aluno específico')} — ${tmEsc(statusLabel(a.status))}</li>`).join('') || '<li class="empty-text">Nenhuma turma atribuída ainda.</li>'}</ul>
        <form id="er-assign-form" class="tm-form-row">
          <div class="form-group">
            <label>Turmas (selecione uma ou mais)</label>
            <div id="er-assign-classes" class="essay-class-checklist">
              ${assignableClassrooms.map((c) => `
                <label class="essay-class-check"><input type="checkbox" data-assign-class-id="${tmEsc(c.class_id)}"> ${tmEsc(c.name)}</label>`).join('') || '<p class="empty-text">Nenhuma turma disponível.</p>'}
            </div>
          </div>
          <div class="form-group"><label for="er-assign-due">Prazo (opcional)</label><input id="er-assign-due" class="text-input" type="date"></div>
          <div class="form-group"><label for="er-assign-validation"><input id="er-assign-validation" type="checkbox" checked> Exigir revisão docente</label></div>
          <button class="btn btn-primary" type="submit">Atribuir às turmas selecionadas</button>
        </form>
        <p id="er-assign-msg" class="tm-msg" hidden></p>
        <ul id="er-assign-failures" class="essay-assign-failures" hidden></ul>

        <h4>Histórico de atribuições</h4>
        <ul id="er-assignment-log-list"><li class="empty-text">Carregando...</li></ul>
      </div>`;
    const tabsEl = container.querySelector('.essay-review-tabs');
    if (tabsEl.nextElementSibling) tabsEl.nextElementSibling.remove();
    tabsEl.insertAdjacentElement('afterend', detailHtml.firstElementChild);

    // Proposta da plataforma é somente-leitura pro professor (spec, decisão
    // 3): ele só atribui a turmas, nunca edita nem adiciona material. Os
    // blocos continuam no DOM (os listeners abaixo os procuram) e só são
    // escondidos - o backend recusaria essas chamadas de qualquer forma.
    const isPlatform = !!detail.is_platform;
    if (isPlatform) {
      container.querySelector('#er-material-form').hidden = true;
    }
    if (detail.materialized === false) {
      // Ainda não existe EssayPrompt nenhum nesta escola - a folha de
      // resposta (answer-sheet.pdf) só passa a fazer sentido depois da
      // primeira atribuição, que é o que materializa a cópia.
      container.querySelector('#er-sheet-actions').hidden = true;
    }

    container.querySelector('[data-back]').addEventListener('click', renderPromptsList);
    try {
      const logs = await reviewRequest(`/api/v1/catalog/essay-prompts/${promptId}/assignment-log`);
      container.querySelector('#er-assignment-log-list').innerHTML = logs.map((log) => {
        const turmas = log.target_summary.turmas || [];
        const alunos = log.target_summary.alunos || [];
        const partes = [];
        if (turmas.length) partes.push(`${turmas.length} turma(s)`);
        if (alunos.length) partes.push(`${alunos.length} aluno(s)`);
        return `<li>${new Date(log.created_at).toLocaleString('pt-BR')} - ${partes.join(', ') || 'nenhum alvo'}</li>`;
      }).join('') || '<li class="empty-text">Nenhuma atribuição registrada ainda.</li>';
    } catch (e) {
      container.querySelector('#er-assignment-log-list').innerHTML = '<li class="empty-text">Não foi possível carregar o histórico.</li>';
    }
    const sheetMsg = container.querySelector('#er-sheet-msg');
    function showSheetMsg(text) {
      sheetMsg.hidden = false;
      sheetMsg.textContent = text;
    }

    container.querySelector('#er-answer-sheet-btn').addEventListener('click', async () => {
      const btn = container.querySelector('#er-answer-sheet-btn');
      const copies = Math.max(1, Math.min(200, Number(container.querySelector('#er-sheet-copies').value) || 1));
      btn.disabled = true;
      sheetMsg.hidden = true;
      try {
        // Aviso (nao bloqueio): sem logo a folha e gerada do mesmo jeito.
        const logoState = await reviewRequest('/api/v1/teacher/school/logo');
        if (!logoState.has_logo) showSheetMsg('A folha vai sair sem logo - envie a logo da escola se quiser que ela apareça.');
        const res = await fetch(
          `/api/v1/catalog/essay-prompts/${promptId}/answer-sheet.pdf?copies=${copies}`,
          { headers: reviewHeaders() },
        );
        if (!res.ok) throw new Error('Não foi possível gerar a folha de resposta.');
        const blob = await res.blob();
        const url = URL.createObjectURL(blob);
        const a = document.createElement('a');
        a.href = url;
        a.download = `folha-de-redacao-${copies}-copias.pdf`;
        document.body.appendChild(a);
        a.click();
        a.remove();
        URL.revokeObjectURL(url);
      } catch (e) {
        showSheetMsg(e.message);
      } finally {
        btn.disabled = false;
      }
    });

    container.querySelector('#er-logo-btn').addEventListener('click', () => {
      container.querySelector('#er-logo-file').click();
    });
    container.querySelector('#er-logo-file').addEventListener('change', async (ev) => {
      const file = ev.target.files[0];
      if (!file) return;
      const formData = new FormData();
      formData.append('file', file);
      try {
        await reviewRequest('/api/v1/teacher/school/logo', { method: 'POST', body: formData });
        showSheetMsg('Logo enviada. As próximas folhas geradas já saem com ela.');
      } catch (e) {
        showSheetMsg(e.message);
      }
    });
    container.querySelector('#er-material-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const content = container.querySelector('#er-material-content').value.trim();
      const fileInput = container.querySelector('#er-material-file');
      const file = fileInput.files[0];
      const msg = container.querySelector('#er-material-msg');
      msg.hidden = true;
      if (!content && !file) return;
      const submitBtn = ev.target.querySelector('button[type="submit"]');
      submitBtn.disabled = true;
      try {
        if (file) {
          const formData = new FormData();
          formData.append('file', file);
          formData.append('position', String(detail.materials.length));
          await reviewRequest(`/api/v1/catalog/essay-prompts/${promptId}/materials/upload`, {
            method: 'POST', body: formData,
          });
        } else {
          await reviewRequest(`/api/v1/catalog/essay-prompts/${promptId}/materials`, {
            method: 'POST', headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify({ material_type: 'TEXT', content, position: detail.materials.length }),
          });
        }
        renderPromptDetail(promptId);
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
        submitBtn.disabled = false;
      }
    });
    container.querySelector('#er-assign-form').addEventListener('submit', async (ev) => {
      ev.preventDefault();
      const classIds = Array.from(container.querySelectorAll('[data-assign-class-id]:checked'))
        .map((cb) => cb.dataset.assignClassId);
      const validationEnabled = container.querySelector('#er-assign-validation').checked;
      const dueDate = container.querySelector('#er-assign-due').value;
      const msg = container.querySelector('#er-assign-msg');
      const failuresEl = container.querySelector('#er-assign-failures');
      msg.hidden = true;
      failuresEl.hidden = true;
      failuresEl.innerHTML = '';
      if (!classIds.length) {
        msg.hidden = false;
        msg.textContent = 'Selecione ao menos uma turma.';
        return;
      }
      const submitBtn = ev.target.querySelector('button[type="submit"]');
      submitBtn.disabled = true;
      try {
        const result = await reviewRequest(`/api/v1/catalog/essay-prompts/${promptId}/assignments/bulk`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            class_ids: classIds,
            validation_enabled: validationEnabled,
            // <input type="date"> gives "YYYY-MM-DD" or "" when left blank -
            // never send an empty string as due_at, the backend expects a
            // real datetime or the field omitted entirely.
            due_at: dueDate ? new Date(dueDate).toISOString() : null,
          }),
        });
        detail.assignments = detail.assignments.concat(result.assigned);
        container.querySelector('#er-assignments-list').innerHTML = detail.assignments
          .map((a) => `<li>${tmEsc(a.class_id ? (classNameById.get(a.class_id) || a.class_id) : 'Aluno específico')} — ${tmEsc(statusLabel(a.status))}</li>`)
          .join('') || '<li class="empty-text">Nenhuma turma atribuída ainda.</li>';
        container.querySelectorAll('[data-assign-class-id]:checked').forEach((cb) => { cb.checked = false; });
        const failureEntries = Object.entries(result.failures || {});
        if (failureEntries.length) {
          // Best-effort per class (same shape the bulk-approve flow already
          // uses elsewhere) - a class that failed (e.g. already assigned)
          // never silently disappears, it's listed here instead while the
          // classes that succeeded above already show as assigned.
          failuresEl.hidden = false;
          failuresEl.innerHTML = failureEntries.map(([classId, reason]) => `
            <li>${tmEsc(classNameById.get(classId) || classId)}: ${tmEsc(translateDetail(reason))}</li>`).join('');
        }
      } catch (e) {
        msg.hidden = false;
        msg.textContent = e.message;
      } finally {
        submitBtn.disabled = false;
      }
    });
  }

  function renderMaterialLabel(material) {
    if (material.material_type === 'FILE') {
      const filename = (material.storage_uri || '').split('/').pop() || 'arquivo';
      return `📎 ${tmEsc(filename)}`;
    }
    return tmEsc(material.content || material.material_type);
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
      let checklistData = { rationales: [], feedbackStrengths: [], structured: null };
      try {
        const approved = await reviewRequest('/api/v1/teacher/essay-corrections?status=APPROVED');
        const match = approved.find((c) => c.essay_submission_id === data.entries[0].essay_submission_id);
        if (match) {
          checklistData = {
            rationales: (match.ai_output || {}).rationales || [],
            feedbackStrengths: (match.final_feedback || {}).strengths || [],
            structured: match.ai_output || {},
          };
        }
      } catch (e) {
        // Checklist degrades to its own empty state below.
      }
      try {
        body.innerHTML = window.EssayEvolution.renderEvolutionSection(data, checklistData);
        window.EssayEvolution.wireEvolutionSection(body, data, { onViewDevolutiva: viewSubmissionDevolutivaForTeacher });
      } catch (e) {
        body.innerHTML = `<p class="empty-text">${tmEsc(e.message)}</p>`;
      }
    }

    async function viewSubmissionDevolutivaForTeacher(entry) {
      try {
        const approved = await reviewRequest('/api/v1/teacher/essay-corrections?status=APPROVED');
        const match = approved.find((c) => c.essay_submission_id === entry.essay_submission_id);
        if (!match) {
          alert('Não foi possível abrir a devolutiva.');
          return;
        }
        // renderReviewPanel busca a correção em currentCorrections (variável
        // de módulo) por id - precisa estar populada com a lista que contém
        // a correção que queremos abrir antes de chamar.
        currentCorrections = approved;
        renderReviewPanel(match.id, 'APPROVED');
      } catch (e) {
        alert('Não foi possível abrir a devolutiva.');
      }
    }

    searchInput.addEventListener('input', () => loadStudents(searchInput.value.trim()));
    await loadStudents('');
  }

  // --- Dashboard tab: submission rate / average scores / per-competency
  // chart / roster / action plan for one essay prompt, with série-turma-
  // aluno filters and XLSX export - see essay_teacher_dashboard.py for the
  // aggregation this renders. ---

  async function renderDashboardTab() {
    container.innerHTML = `${renderTabs('dashboard')}<p class="empty-text">Carregando propostas...</p>`;
    wireTabs();
    try {
      if (!prompts.length) prompts = await reviewRequest('/api/v1/catalog/essay-prompts');
    } catch (e) {
      container.innerHTML = `${renderTabs('dashboard')}<p class="empty-text">${tmEsc(e.message)}</p>`;
      wireTabs();
      return;
    }
    // O dashboard so faz sentido pra uma proposta que ja tem EssayPrompt
    // real nesta escola (materializada) - uma proposta da plataforma ainda
    // nao adotada bateria em GET .../dashboard com 403 "This proposal is
    // not yours.", e pior, essa seria a primeira da lista (e abriria
    // sozinha) numa escola que ainda nao tem proposta propria nenhuma.
    const dashboardPrompts = prompts.filter((p) => !isUnmaterializedPlatformPrompt(p));
    const tabsEl = container.querySelector('.essay-review-tabs');
    if (tabsEl.nextElementSibling) tabsEl.nextElementSibling.remove();
    // O CONTEXTO ANTES DO FORMULARIO. A tela comecava por um `<select>`: o
    // professor tinha de configurar antes de saber do que se tratava.
    //
    // A "Proposta" deixou de ter linha propria. Ela e um FILTRO como os
    // outros tres, e estar sozinha acima deles fazia parecer navegacao - dois
    // blocos de filtro empilhados, com Turma aparecendo so no segundo.
    // `renderDashboardBody` desenha os quatro na mesma linha.
    tabsEl.insertAdjacentHTML('afterend', `
      <p class="er-contexto">Acompanhe a produção textual das suas turmas.</p>
      <div id="er-dash-body"></div>`);

    if (dashboardPrompts.length) {
      renderDashboardBody(dashboardPrompts[0].id);
    } else {
      container.querySelector('#er-dash-body').innerHTML = '<p class="empty-text">Nenhuma proposta criada ainda.</p>';
    }
  }

  async function renderDashboardBody(promptId) {
    const body = container.querySelector('#er-dash-body');
    if (!body) return;
    body.innerHTML = '<p class="empty-text">Carregando turmas...</p>';

    let classrooms = [];
    try {
      classrooms = await reviewRequest(
        `/api/v1/teacher/classrooms?school_id=${encodeURIComponent(schoolId)}&academic_year=2026`,
      );
    } catch (e) {
      classrooms = [];
    }
    const assignableClassrooms = classrooms.filter((c) => c.class_id);
    const classNameById = new Map(assignableClassrooms.map((c) => [c.class_id, c.name]));
    // NOTE (backend contract gap): the dashboard route accepts a
    // grade_level_id (UUID) filter, but no endpoint in this app exposes
    // GradeLevel ids to the frontend - /api/v1/teacher/classrooms only
    // returns `grade_level` as a display string. So "Série" below is a
    // client-side-only filter that narrows which turmas are offered (by
    // matching that same string label); it's never sent to the backend as
    // grade_level_id. Only Turma/Aluno actually filter the server response.
    const gradeLevels = Array.from(new Set(assignableClassrooms.map((c) => c.grade_level).filter(Boolean)));

    // Props de plataforma ainda nao adotadas pela escola nao entram aqui -
    // selecionar uma bateria em GET .../dashboard com 403 "This proposal is
    // not yours." (ver isUnmaterializedPlatformPrompt).
    const dashboardPrompts = prompts.filter((p) => !isUnmaterializedPlatformPrompt(p));
    body.innerHTML = `
      <div class="tm-form-row er-filtros" style="margin: 12px 0;">
        <div class="form-group">
          <label for="er-dash-prompt">Proposta</label>
          <select id="er-dash-prompt" class="text-input">
            ${dashboardPrompts.map((pr) => `<option value="${tmEsc(pr.id)}"${pr.id === promptId ? ' selected' : ''}>${tmEsc(pr.title)} (${pr.year})</option>`).join('') || '<option value="">Nenhuma proposta</option>'}
          </select>
        </div>
        <div class="form-group">
          <label for="er-dash-grade">Série</label>
          <select id="er-dash-grade" class="text-input">
            <option value="">Todas</option>
            ${gradeLevels.map((g) => `<option value="${tmEsc(g)}">${tmEsc(g)}</option>`).join('')}
          </select>
        </div>
        <div class="form-group">
          <label for="er-dash-class">Turma</label>
          <select id="er-dash-class" class="text-input">
            <option value="">Todas</option>
            ${assignableClassrooms.map((c) => `<option value="${tmEsc(c.class_id)}" data-grade="${tmEsc(c.grade_level || '')}">${tmEsc(c.name)}</option>`).join('')}
          </select>
        </div>
        <div class="form-group">
          <label for="er-dash-student">Aluno</label>
          <select id="er-dash-student" class="text-input"><option value="">Todos</option></select>
        </div>
      </div>
      <div id="er-dash-results"><p class="empty-text">Carregando dashboard...</p></div>`;

    const promptSelect = body.querySelector('#er-dash-prompt');
    if (promptSelect) {
      promptSelect.addEventListener('change', () => {
        if (promptSelect.value) renderDashboardBody(promptSelect.value);
      });
    }
    const gradeSelect = body.querySelector('#er-dash-grade');
    const classSelect = body.querySelector('#er-dash-class');
    const studentSelect = body.querySelector('#er-dash-student');

    function applyGradeFilterToClassOptions() {
      const grade = gradeSelect.value;
      Array.from(classSelect.options).forEach((opt) => {
        if (!opt.value) return; // "Todas" always stays visible
        opt.hidden = Boolean(grade) && opt.dataset.grade !== grade;
      });
      const selected = classSelect.selectedOptions[0];
      if (grade && selected && selected.value && selected.dataset.grade !== grade) {
        classSelect.value = '';
      }
    }

    async function loadDashboard() {
      const resultsEl = body.querySelector('#er-dash-results');
      resultsEl.innerHTML = '<p class="empty-text">Carregando...</p>';
      const params = new URLSearchParams();
      if (classSelect.value) params.set('class_id', classSelect.value);
      if (studentSelect.value) params.set('student_id', studentSelect.value);
      let dashboard;
      try {
        dashboard = await reviewRequest(
          `/api/v1/catalog/essay-prompts/${promptId}/dashboard${params.toString() ? `?${params.toString()}` : ''}`,
        );
      } catch (e) {
        resultsEl.innerHTML = `<p class="empty-text">${tmEsc(e.message)}</p>`;
        return;
      }
      const previousStudent = studentSelect.value;
      studentSelect.innerHTML = `<option value="">Todos</option>${dashboard.students
        .map((s) => `<option value="${tmEsc(s.student_id)}">${tmEsc(s.student_name)}</option>`).join('')}`;
      if (dashboard.students.some((s) => s.student_id === previousStudent)) studentSelect.value = previousStudent;

      resultsEl.innerHTML = renderDashboardResults(dashboard, classNameById);
      wireDashboardExportButtons(resultsEl, promptId, params);
    }

    gradeSelect.addEventListener('change', () => {
      applyGradeFilterToClassOptions();
      studentSelect.value = '';
      loadDashboard();
    });
    classSelect.addEventListener('change', () => {
      studentSelect.value = '';
      loadDashboard();
    });
    studentSelect.addEventListener('change', () => loadDashboard());

    await loadDashboard();
  }

  function renderDashboardResults(dashboard, classNameById) {
    if (dashboard.total_students === 0) {
      return '<p class="empty-text">Nenhum aluno encontrado para este filtro (a proposta pode ainda não estar atribuída a nenhuma turma).</p>';
    }
    const submittedPct = `${dashboard.submitted_percentage.toFixed(0)}%`;
    const avgTotal = dashboard.average_total_score != null ? `${dashboard.average_total_score.toFixed(0)}/1000` : '—';

    const statsHtml = `
      <div class="stats-grid" style="margin-bottom:20px;">
        <div class="stat-card">
          <div class="stat-icon bg-blue">📤</div>
          <div class="stat-data">
            <span class="stat-value">${submittedPct}</span>
            <span class="stat-label">${dashboard.submitted_count} de ${dashboard.total_students} entregaram</span>
          </div>
        </div>
        <div class="stat-card">
          <div class="stat-icon bg-purple">📊</div>
          <div class="stat-data">
            <span class="stat-value">${avgTotal}</span>
            <span class="stat-label">Nota média</span>
          </div>
        </div>
      </div>`;

    const chartHtml = renderCompetencyBarChart(dashboard.average_per_competency);

    const rosterHtml = `
      <div class="tm-table-wrap" style="overflow-x:auto;">
        <table class="tm-table">
          <thead><tr><th>Aluno</th><th>Turma</th><th>Entregou</th><th>Nota</th></tr></thead>
          <tbody>
            ${dashboard.students.map((s) => `
              <tr>
                <td>${tmEsc(s.student_name)}</td>
                <td>${tmEsc(classNameById.get(s.class_id) || '—')}</td>
                <td>${s.submitted ? 'Sim' : 'Não'}</td>
                <td>${s.total_score != null ? s.total_score : '—'}</td>
              </tr>`).join('') || '<tr><td colspan="4" class="empty-text">Nenhum aluno neste filtro.</td></tr>'}
          </tbody>
        </table>
      </div>`;

    const actionPlanHtml = dashboard.action_plan.length
      ? dashboard.action_plan.map((a) => `
          <div class="plan-column column-${DASH_PRIORITY_STYLE[a.priority] || 'warning'}" style="margin-bottom:12px;">
            <div class="column-header"><strong>Prioridade ${tmEsc(DASH_PRIORITY_LABEL[a.priority] || a.priority)}</strong></div>
            <p style="font-size:13px; margin-bottom:6px;"><strong>Evidência:</strong> ${tmEsc(a.evidence)}</p>
            <p style="font-size:13px; color:#4f46e5;"><strong>Ação recomendada:</strong> ${tmEsc(a.recommended_action)}</p>
          </div>`).join('')
      : '<p class="empty-text">Sem recomendações para este filtro.</p>';

    return `
      ${statsHtml}
      <h4 style="margin:16px 0 8px 0;">Média por competência</h4>
      ${chartHtml}
      <div class="tm-form-actions" style="margin: 16px 0; display:flex; gap:8px; flex-wrap:wrap;">
        <button class="btn btn-secondary" type="button" id="er-dash-export-total">Exportar nota total</button>
        <button class="btn btn-secondary" type="button" id="er-dash-export-competency">Exportar nota por competência</button>
        <button class="btn btn-secondary" type="button" id="er-dash-export-submission">Exportar lista de entrega</button>
      </div>
      <h4 style="margin:24px 0 8px 0;">Alunos</h4>
      ${rosterHtml}
      <h4 style="margin:24px 0 8px 0;">Plano de ação</h4>
      <div class="plan-list">${actionPlanHtml}</div>`;
  }

  // Bar chart in the same inline-SVG / visual language as essay-evolution.js's
  // renderLineChart (same viewBox, grid, axis labels, point-value labels) -
  // a bar per competency instead of a line over time, since this dashboard
  // has one snapshot (the current filter), not a timeline.
  function renderCompetencyBarChart(averagePerCompetency) {
    const hasData = averagePerCompetency && DASH_COMPETENCY_CODES.some((code) => averagePerCompetency[code] != null);
    if (!hasData) {
      return `
        <div class="essay-evolution-chart-card">
          <p class="empty-text">Sem notas suficientes ainda para este filtro.</p>
        </div>`;
    }
    const max = 200;
    const left = 50;
    const right = 500;
    const top = 20;
    const bottom = 180;
    const bandWidth = (right - left) / DASH_COMPETENCY_CODES.length;
    const barWidth = bandWidth * 0.5;
    const gridHtml = [top, (top + bottom) / 2, bottom]
      .map((y) => `<line x1="${left}" y1="${y}" x2="${right}" y2="${y}" stroke="#eef1f5"/>`).join('');
    const axisLabelsHtml = `
      <text x="10" y="${top + 4}" class="essay-evolution-axis-label">${max}</text>
      <text x="10" y="${(top + bottom) / 2 + 4}" class="essay-evolution-axis-label">${max / 2}</text>
      <text x="10" y="${bottom + 4}" class="essay-evolution-axis-label">0</text>`;
    const barsHtml = DASH_COMPETENCY_CODES.map((code, i) => {
      const value = averagePerCompetency[code];
      const bandCenter = left + bandWidth * (i + 0.5);
      const x = bandCenter - barWidth / 2;
      const h = value != null ? (Math.max(0, Math.min(value, max)) / max) * (bottom - top) : 0;
      const y = bottom - h;
      return `
        <rect x="${x}" y="${y}" width="${barWidth}" height="${h}" fill="${DASH_COMPETENCY_COLORS[code]}" rx="3"></rect>
        <text x="${bandCenter}" y="${bottom + 20}" text-anchor="middle" class="essay-evolution-pt-label">${code}</text>
        ${value != null ? `<text x="${bandCenter}" y="${Math.max(top + 10, y - 8)}" text-anchor="middle" class="essay-evolution-pt-value">${value.toFixed(0)}</text>` : ''}`;
    }).join('');
    return `
      <div class="essay-evolution-chart-card">
        <svg viewBox="0 0 520 220" width="100%">
          ${gridHtml}
          ${axisLabelsHtml}
          ${barsHtml}
        </svg>
      </div>`;
  }

  function wireDashboardExportButtons(resultsEl, promptId, baseParams) {
    const exports = [
      { id: 'er-dash-export-total', reportType: 'grades_total', label: 'a nota total' },
      { id: 'er-dash-export-competency', reportType: 'grades_per_competency', label: 'a nota por competência' },
      { id: 'er-dash-export-submission', reportType: 'submission_list', label: 'a lista de entrega' },
    ];
    exports.forEach(({ id, reportType, label }) => {
      const btn = resultsEl.querySelector(`#${id}`);
      if (!btn) return;
      btn.addEventListener('click', async () => {
        btn.disabled = true;
        try {
          const params = new URLSearchParams(baseParams);
          params.set('report_type', reportType);
          const res = await fetch(
            `/api/v1/catalog/essay-prompts/${promptId}/dashboard/export.xlsx?${params.toString()}`,
            { headers: reviewHeaders() },
          );
          if (!res.ok) throw new Error('export failed');
          const blob = await res.blob();
          const url = URL.createObjectURL(blob);
          const a = document.createElement('a');
          a.href = url;
          a.download = `${reportType}.xlsx`;
          document.body.appendChild(a);
          a.click();
          a.remove();
          URL.revokeObjectURL(url);
        } catch (e) {
          alert(`Não foi possível exportar ${label}.`);
        } finally {
          btn.disabled = false;
        }
      });
    });
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
    // An APPROVED correction is terminal (no status change happens here)
    // but the teacher can still revise its published score/feedback at any
    // time via /edit - so the score/feedback inputs stay editable for it,
    // unlike REJECTED which has no published content to revise.
    const isEditableNow = isPending || correction.status === 'APPROVED';

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
            <label ${isEditableNow ? `for="er-score-${code}"` : ''}>${code}</label>
            ${isEditableNow
              ? `<input id="er-score-${code}" class="text-input" type="number" min="0" max="200" step="40" value="${Number((perCompetency[code] || {}).points) || 0}">`
              : `<p class="empty-text">${tmEsc((perCompetency[code] || {}).points ?? '—')}</p>`}
          </div>`).join('')}
      </div>
      <h4>Feedback</h4>
      <div class="form-group">
        <label ${isEditableNow ? 'for="er-feedback-strategy"' : ''}>Próxima redação</label>
        ${isEditableNow
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
      : correction.status === 'APPROVED' ? `
        <p class="empty-text">Decisão: Aprovada</p>
        <button class="btn btn-secondary" type="button" id="er-edit-btn">Salvar alterações</button>`
      : isTerminal ? `
        <p class="empty-text">Decisão: Rejeitada</p>`
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

    const editBtn = container.querySelector('#er-edit-btn');
    if (editBtn) {
      editBtn.addEventListener('click', async () => {
        editBtn.disabled = true;
        const currentScores = ['C1', 'C2', 'C3', 'C4', 'C5'].map((code) => Number(container.querySelector(`#er-score-${code}`).value) || 0);
        const currentFeedbackText = container.querySelector('#er-feedback-strategy').value.trim();
        // Same dirty-check as er-approve-btn above: only send what actually
        // changed. Unlike approve (which always POSTs, even with an empty
        // body, to move the correction into APPROVED), an edit with nothing
        // changed has no reason to hit the network at all - just re-enable
        // the button and stop.
        const scoresEdited = currentScores.some((value, i) => value !== originalScores[i]);
        const feedbackEdited = currentFeedbackText !== originalFeedbackText;
        if (!scoresEdited && !feedbackEdited) {
          editBtn.disabled = false;
          return;
        }
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
          await reviewRequest(`/api/v1/teacher/essay-corrections/${correctionId}/edit`, {
            method: 'POST',
            headers: { 'Content-Type': 'application/json' },
            body: JSON.stringify(body),
          });
          renderReviewQueue(returnStatus);
        } catch (e) {
          msg.hidden = false;
          msg.textContent = e.message;
          editBtn.disabled = false;
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
    // A ENTRADA E A VISAO GERAL. "Como estao meus alunos?" vem antes de "o
    // que eu quero cadastrar?" - e a resposta estava na quarta aba.
    if (window.RedacaoNav && window.RedacaoNav.ABA_INICIAL === 'dashboard') {
      renderDashboardTab();
    } else {
      renderPromptsList();
    }
  }

  return { init };
});
