/* AGENTE IA EDU — Banco de Questões (Professor) — PHASE 13.
 *
 * A normal Question Bank workflow: search / filter / paginate (server-side),
 * preview one question, select / deselect / reorder / remove, and obtain a final
 * ordered QuestionSelection. ZERO AI: this file talks only to the PHASE 12
 * Question Bank endpoints. Selection lives entirely in the browser and is never
 * persisted in this phase.
 */
document.addEventListener('DOMContentLoaded', () => {
  'use strict';

  const API = '/api/v1/question-bank';
  const state = {
    // reused auth pattern (Bearer <subject>); host replaces the provider in prod
    teacherId: 'user:prof_mendes',
    view: 'bank',
    search: '',
    filters: {
      year: '', day: '', area: '', discipline: '', content: '', subcontent: '',
      status: '', difficulty: '', source: '', mode: '',
      provisional: false, visual: false, protected: false,
    },
    page: 1,
    pageSize: 20,
    orderBy: 'official_number',
    orderDirection: 'asc',
    lastPage: { items: [], total: 0, totalPages: 1 },
    // selection: ordered list of { question_version_id, question_id, label, area, content }
    selection: [],
    preview: null,
    loading: false,
    // PHASE 14 list generator wizard (client-side; deterministic; no persistence)
    list: {
      step: 'config',
      config: { title: '', instructions: '', mode: 'EXERCISE_LIST',
                answerKey: 'NONE', resolutionStyle: 'SUMMARY' },
      configOptions: null,
      definition: null,
      storedId: null,
      storedStatus: null,
    },
  };

  const $ = (id) => document.getElementById(id);
  const authHeaders = () => ({ 'Content-Type': 'application/json', 'Authorization': `Bearer ${state.teacherId}` });

  function showAlert(message, kind) {
    const box = $('qb-alert');
    if (!message) { box.hidden = true; box.textContent = ''; return; }
    box.hidden = false;
    box.textContent = message;
    box.className = `alert-box ${kind === 'error' ? 'alert-error' : 'alert-info'}`;
  }

  function esc(value) {
    return String(value == null ? '' : value)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  // ---------- server query (server-side filters + pagination) ----------

  function buildQuery() {
    const p = new URLSearchParams();
    const f = state.filters;
    if (state.search.trim()) {
      const term = state.search.trim();
      // number -> official_number; otherwise a content-code-ish token -> content; else text
      if (/^\d{1,3}$/.test(term)) p.set('official_number', term);
      else if (/^[A-Z][A-Z0-9-]+$/.test(term)) p.set('content', term);
      else p.set('text', term); // reserved: PHASE 12 has no free-text yet; kept for forward-compat
    }
    if (f.year) p.set('year', f.year);
    if (f.day) p.set('day', f.day);
    if (f.area) p.set('area', f.area);
    if (f.discipline) p.set('discipline', f.discipline);
    if (f.content) p.set('content', f.content);
    if (f.subcontent) p.set('subcontent', f.subcontent);
    if (f.status) p.set('classification_status', f.status);
    if (f.difficulty) p.set('difficulty', f.difficulty);
    if (f.source) p.set('classification_source', f.source);
    if (f.mode) p.set('classification_mode', f.mode);
    if (f.provisional) p.set('provisional_only', 'true');
    if (f.visual) p.set('visual_dependency', 'true');
    if (f.protected) p.set('protected_only', 'true');
    p.set('page', String(state.page));
    p.set('page_size', String(state.pageSize));
    p.set('order_by', state.orderBy);
    p.set('order_direction', state.orderDirection);
    return p.toString();
  }

  async function loadList() {
    state.loading = true;
    $('qb-list-status').textContent = 'Carregando questões…';
    showAlert('');
    try {
      const res = await fetch(`${API}/questions?${buildQuery()}`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      state.lastPage = {
        items: data.items || [],
        total: data.pagination.total,
        totalPages: data.pagination.total_pages || 1,
      };
      renderList();
    } catch (err) {
      $('qb-list').innerHTML = '';
      $('qb-list-status').textContent = '';
      showAlert('Não foi possível carregar as questões. Tente novamente.', 'error');
    } finally {
      state.loading = false;
    }
  }

  // ---------- rendering: list (light rows, no full body) ----------

  function classificationSummary(q) {
    const c = q.classification;
    if (!c) return '<span class="qb-tag qb-tag-muted">Não classificada</span>';
    const parts = [c.discipline_code, c.area_code, c.content_code, c.subcontent_code].filter(Boolean);
    const chain = parts.map(esc).join(' › ');
    let badge = '<span class="qb-tag qb-tag-ok">Classificada</span>';
    if (q.classification_state === 'FORCED_CLOSURE') badge = '<span class="qb-tag qb-tag-warn">Provisória · fechamento forçado</span>';
    else if (q.classification_state === 'NEEDS_REVIEW') badge = '<span class="qb-tag qb-tag-warn">Provisória · em revisão</span>';
    return `${badge} <span class="qb-chain">${chain}</span>`;
  }

  function rowIsSelected(q) {
    return state.selection.some((s) => s.question_version_id === q.question_version_id);
  }

  function renderList() {
    const { items, total, totalPages } = state.lastPage;
    $('qb-list-status').textContent = `${total} questão(ões) encontrada(s).`;
    if (!items.length) {
      $('qb-list').innerHTML = '<div class="empty-text">Nenhuma questão para os filtros atuais.</div>';
    } else {
      $('qb-list').innerHTML = items.map((q) => {
        const selected = rowIsSelected(q);
        const diff = q.recommended_difficulty ? `<span class="qb-tag">${esc(q.recommended_difficulty)}</span>` : '';
        const visual = q.has_visual_dependency ? '<span class="qb-tag qb-tag-visual" title="Depende de material visual">🖼️ visual</span>' : '';
        const prot = q.is_protected ? '<span class="qb-tag qb-tag-muted">protegida</span>' : '';
        return `
          <article class="qb-row card" data-qvid="${esc(q.question_version_id)}" data-qid="${esc(q.question_id)}">
            <div class="qb-row-main">
              <div class="qb-row-head">
                <strong>${esc(q.year)} — Q${esc(q.official_number)}</strong>
                <span class="qb-tag">Dia ${esc(q.day)}</span>
                <span class="qb-tag">${esc(q.enem_area || '—')}</span>
                ${diff} ${visual} ${prot}
              </div>
              <div class="qb-row-class">${classificationSummary(q)}</div>
            </div>
            <div class="qb-row-actions">
              <button class="btn btn-secondary qb-open" type="button" data-qid="${esc(q.question_id)}">Abrir</button>
              <button class="btn ${selected ? 'btn-secondary' : 'btn-primary'} qb-toggle" type="button"
                      data-qvid="${esc(q.question_version_id)}" data-qid="${esc(q.question_id)}">
                ${selected ? 'Remover' : 'Selecionar'}
              </button>
            </div>
          </article>`;
      }).join('');
    }
    $('qb-page-label').textContent = `Página ${state.page} de ${Math.max(1, totalPages)}`;
    $('qb-page-prev').disabled = state.page <= 1 || state.loading;
    $('qb-page-next').disabled = state.page >= totalPages || state.loading;
    updateSelectionCount();
  }

  // ---------- preview (loads ONLY the selected question) ----------

  async function openPreview(questionId) {
    showAlert('');
    $('qb-preview').hidden = false;
    $('qb-preview-body').innerHTML = '<p class="qb-list-status">Carregando…</p>';
    try {
      const res = await fetch(`${API}/questions/${questionId}`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const q = await res.json();
      state.preview = q;
      renderPreview(q);
    } catch (err) {
      $('qb-preview-body').innerHTML = '';
      showAlert('Não foi possível abrir a questão.', 'error');
      closePreview();
    }
  }

  function renderPreview(q) {
    $('qb-preview-title').textContent = `${q.year} — Q${q.official_number} · ${q.enem_area || '—'}`;
    const c = q.classification;
    let classificationBlock = '<p class="qb-tag qb-tag-muted">Questão não classificada.</p>';
    if (c) {
      const chain = [
        ['Disciplina', c.discipline_code], ['Área', c.area_code],
        ['Conteúdo', c.content_code], ['Subconteúdo', c.subcontent_code],
      ].filter(([, v]) => v).map(([k, v]) => `<li><span>${k}</span><strong>${esc(v)}</strong></li>`).join('');
      const meta = [
        c.confidence ? `Confiança: ${esc(c.confidence)}` : null,
        c.classification_mode ? `Modo: ${esc(c.classification_mode)}` : null,
        c.review_reason ? `Motivo de revisão: ${esc(c.review_reason)}` : null,
        c.taxonomy_version ? `Taxonomia: ${esc(c.taxonomy_version)}` : null,
      ].filter(Boolean).map((m) => `<span class="qb-tag">${m}</span>`).join(' ');
      const provisional = (q.classification_state === 'NEEDS_REVIEW' || q.classification_state === 'FORCED_CLOSURE')
        ? `<div class="qb-provisional-banner">⚠️ Classificação provisória (${esc(q.classification_state)}). Revise antes de usar.</div>`
        : '';
      classificationBlock = `${provisional}<ul class="qb-class-chain">${chain}</ul><div class="qb-class-meta">${meta}</div>`;
    }
    const visualNote = q.has_visual_dependency
      ? `<div class="qb-visual-note">Esta questão depende de material visual.${
          (q.assets && q.assets.length) ? '' : ' <em>Material visual não disponível.</em>'}</div>`
      : '';
    const options = (q.options || []).map((o) =>
      `<li class="qb-option"><span class="qb-option-key">${esc(o.key)}</span><span>${esc(o.text)}</span></li>`).join('');
    const selected = state.selection.some((s) => s.question_version_id === q.question_version_id);
    $('qb-preview-toggle-select').textContent = selected ? 'Remover da seleção' : 'Selecionar questão';
    $('qb-preview-toggle-select').dataset.qvid = q.question_version_id;
    $('qb-preview-toggle-select').dataset.qid = q.question_id;
    $('qb-preview-body').innerHTML = `
      <div class="qb-preview-meta">
        <span class="qb-tag">${esc(q.year)}</span><span class="qb-tag">Dia ${esc(q.day)}</span>
        <span class="qb-tag">Q${esc(q.official_number)}</span><span class="qb-tag">${esc(q.enem_area || '—')}</span>
        ${q.is_protected ? '<span class="qb-tag qb-tag-muted">protegida</span>' : ''}
      </div>
      <section class="qb-preview-class">${classificationBlock}</section>
      ${visualNote}
      <section class="qb-preview-statement"><h4>Enunciado</h4><p>${esc(q.statement || q.canonical_text)}</p></section>
      <section class="qb-preview-options"><h4>Alternativas</h4><ol class="qb-option-list">${options}</ol></section>`;
  }

  function closePreview() {
    $('qb-preview').hidden = true;
    state.preview = null;
  }

  // ---------- selection (client-side only, never persisted) ----------

  function labelFor(q) { return `${q.year} — Q${q.official_number}`; }

  function toggleSelect(questionVersionId, questionId, sourceItem) {
    const idx = state.selection.findIndex((s) => s.question_version_id === questionVersionId);
    if (idx >= 0) {
      state.selection.splice(idx, 1);
    } else {
      const src = sourceItem
        || state.lastPage.items.find((i) => i.question_version_id === questionVersionId)
        || (state.preview && state.preview.question_version_id === questionVersionId ? state.preview : null);
      state.selection.push({
        question_version_id: questionVersionId,
        question_id: questionId,
        label: src ? labelFor(src) : questionVersionId,
        area: src ? src.enem_area : null,
        content: src && src.classification ? src.classification.content_code : null,
        state: src ? src.classification_state : null,
      });
    }
    updateSelectionCount();
    renderList();
    if (state.preview) renderPreview(state.preview);
    if (state.view === 'selection') renderSelection();
  }

  function clearSelection() {
    state.selection = [];
    updateSelectionCount();
    renderList();
    renderSelection();
    if (state.preview) renderPreview(state.preview);
  }

  function updateSelectionCount() {
    const n = state.selection.length;
    $('qb-selection-count').textContent = `Questões selecionadas: ${n}`;
  }

  function moveSelection(index, delta) {
    const target = index + delta;
    if (target < 0 || target >= state.selection.length) return;
    const [row] = state.selection.splice(index, 1);
    state.selection.splice(target, 0, row);
    renderSelection();
  }

  function removeSelection(index) {
    state.selection.splice(index, 1);
    updateSelectionCount();
    renderSelection();
    renderList();
  }

  function renderSelection() {
    const list = $('qb-selection-list');
    const empty = $('qb-selection-empty');
    if (!state.selection.length) {
      list.innerHTML = '';
      empty.hidden = false;
      $('qb-selection-output').hidden = true;
      return;
    }
    empty.hidden = true;
    list.innerHTML = state.selection.map((row, i) => `
      <li class="qb-selection-item" draggable="true" data-index="${i}">
        <span class="qb-selection-pos">${i + 1}.</span>
        <span class="qb-selection-label">${esc(row.label)}${row.content ? ` <span class="qb-tag">${esc(row.content)}</span>` : ''}${
          (row.state === 'NEEDS_REVIEW' || row.state === 'FORCED_CLOSURE') ? ' <span class="qb-tag qb-tag-warn">provisória</span>' : ''}</span>
        <span class="qb-selection-controls">
          <button class="btn btn-secondary qb-sel-up" type="button" data-index="${i}" aria-label="Mover para cima" ${i === 0 ? 'disabled' : ''}>↑</button>
          <button class="btn btn-secondary qb-sel-down" type="button" data-index="${i}" aria-label="Mover para baixo" ${i === state.selection.length - 1 ? 'disabled' : ''}>↓</button>
          <button class="btn btn-secondary qb-sel-remove" type="button" data-index="${i}">Remover questão</button>
        </span>
      </li>`).join('');
  }

  async function finalizeSelection() {
    if (!state.selection.length) { showAlert('Selecione ao menos uma questão.', 'error'); return; }
    try {
      const res = await fetch(`${API}/selections/preview`, {
        method: 'POST', headers: authHeaders(),
        body: JSON.stringify({
          question_version_ids: state.selection.map((s) => s.question_version_id),
          source: 'professor-workflow',
        }),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      const data = await res.json();
      $('qb-selection-output').hidden = false;
      $('qb-selection-output').textContent = JSON.stringify(data, null, 2);
    } catch (err) {
      showAlert(`Não foi possível validar a seleção: ${err.message}`, 'error');
    }
  }

  // ---------- view switching (workflow state survives navigation) ----------

  function switchView(view) {
    state.view = view;
    $('qb-view-bank').hidden = view !== 'bank';
    $('qb-view-selection').hidden = view !== 'selection';
    $('qb-view-list').hidden = view !== 'list';
    $('qb-view-mylists').hidden = view !== 'mylists';
    document.querySelectorAll('.qb-tab').forEach((t) => {
      const on = t.dataset.qbView === view;
      t.classList.toggle('active', on);
      t.setAttribute('aria-selected', on ? 'true' : 'false');
    });
    if (view === 'selection') renderSelection();
    if (view === 'bank') renderList();
    if (view === 'list') setListStep(state.list.step);
    if (view === 'mylists') loadMyLists();
  }

  // ---------- PHASE 15: persist, Minhas Listas, export ----------

  async function persistCurrentList({ finalize = false } = {}) {
    const cfg = state.list.config;
    cfg.title = $('qb-list-title').value.trim() || cfg.title;
    if (!cfg.title) { listAlert('Informe o título da lista.', 'error'); setListStep('config'); return null; }
    const body = {
      question_version_ids: state.selection.map((s) => s.question_version_id),
      title: cfg.title,
      instructions: cfg.instructions || null,
      activity_mode: cfg.mode,
      answer_key_presentation: cfg.answerKey,
      resolution_style: cfg.answerKey === 'KEY_AND_RESOLUTION_AT_END' ? cfg.resolutionStyle : null,
    };
    try {
      let res;
      if (state.list.storedId) {
        res = await fetch(`${API}/lists/${state.list.storedId}`, {
          method: 'PATCH', headers: authHeaders(), body: JSON.stringify(body),
        });
      } else {
        res = await fetch(`${API}/lists`, { method: 'POST', headers: authHeaders(), body: JSON.stringify(body) });
      }
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      const summary = await res.json();
      state.list.storedId = summary.id;
      state.list.storedStatus = summary.status;
      if (finalize) {
        const fr = await fetch(`${API}/lists/${summary.id}/finalize`, { method: 'POST', headers: authHeaders() });
        if (!fr.ok) throw new Error((await fr.json().catch(() => ({}))).detail || `HTTP ${fr.status}`);
        state.list.storedStatus = (await fr.json()).status;
      }
      return summary;
    } catch (err) {
      listAlert(`Não foi possível salvar a lista: ${err.message}`, 'error');
      return null;
    }
  }

  async function loadMyLists() {
    const box = $('qb-mylists-list');
    box.innerHTML = '<p class="qb-list-status">Carregando…</p>';
    const p = new URLSearchParams();
    const qv = $('qb-mylists-q').value.trim();
    if (qv) p.set('q', qv);
    if ($('qb-mylists-status').value) p.set('status', $('qb-mylists-status').value);
    p.set('order_by', $('qb-mylists-order').value);
    p.set('order_direction', $('qb-mylists-dir').value);
    try {
      const res = await fetch(`${API}/lists?${p.toString()}`, { headers: authHeaders() });
      const data = await res.json();
      if (!data.items.length) { box.innerHTML = '<div class="empty-text">Você ainda não criou nenhuma lista.</div>'; return; }
      box.innerHTML = data.items.map((l) => `
        <article class="qb-mylist-row card" data-id="${esc(l.id)}" data-title="${esc(l.title)}" data-status="${esc(l.status)}">
          <div>
            <strong>${esc(l.title)}</strong>
            <span class="qb-tag ${l.status === 'published' ? 'qb-tag-ok' : 'qb-tag-muted'}">${l.status === 'published' ? 'Finalizada' : 'Rascunho'}</span>
            <div class="qb-mylist-meta">${l.question_count} questões · ${esc(l.answer_key_presentation)} · ${esc((l.created_at || '').slice(0, 10))}</div>
            ${l.status === 'published' ? `<div class="qb-mylist-dist-meta" data-id="${esc(l.id)}">Distribuições: carregando…</div>` : ''}
          </div>
          <div class="qb-mylist-actions">
            <button class="btn btn-secondary qb-ml-open" type="button" data-id="${esc(l.id)}">Visualizar</button>
            ${l.status !== 'published' ? `<button class="btn btn-secondary qb-ml-edit" type="button" data-id="${esc(l.id)}">Editar</button>
            <button class="btn btn-secondary qb-ml-finalize" type="button" data-id="${esc(l.id)}">Finalizar</button>` : ''}
            ${l.status === 'published' ? `<button class="btn btn-primary qb-ml-distribute" type="button" data-id="${esc(l.id)}">Distribuir</button>
            <button class="btn btn-secondary qb-ml-distributions" type="button" data-id="${esc(l.id)}">Ver distribuições</button>` : ''}
            <a class="btn btn-secondary" href="${API}/lists/${esc(l.id)}/export.pdf">Exportar PDF</a>
            <a class="btn btn-secondary" href="${API}/lists/${esc(l.id)}/export.docx">Exportar DOCX</a>
          </div>
          ${l.status === 'published' ? `<div class="qb-dist-list" data-id="${esc(l.id)}" hidden></div>` : ''}
        </article>`).join('');
      hydrateDistributionMeta(data.items.filter((l) => l.status === 'published').map((l) => l.id));
    } catch (err) {
      box.innerHTML = '<div class="empty-text">Não foi possível carregar suas listas.</div>';
    }
  }

  async function openStoredList(listId) {
    try {
      const res = await fetch(`${API}/lists/${listId}`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      state.list.definition = d;
      state.list.storedId = d.persisted.list_id;
      state.list.storedStatus = d.persisted.status;
      state.list.config.title = d.configuration.title;
      state.list.config.instructions = d.configuration.instructions || '';
      state.list.config.answerKey = d.configuration.answer_key_presentation;
      state.list.config.resolutionStyle = d.configuration.resolution_style || 'SUMMARY';
      // rehydrate the selection tray in the exact persisted order
      state.selection = d.items.map((it) => ({
        question_version_id: it.question_version_id, question_id: it.question_id,
        label: `${it.year} — Q${it.official_number}`, area: it.enem_area,
        content: it.content_code, state: it.classification_state,
      }));
      updateSelectionCount();
      switchView('list');
      setListStep('preview');
    } catch (err) {
      showAlert('Não foi possível abrir a lista.', 'error');
    }
  }

  // ---------- PHASE 16: distribute a finalized list as an activity ----------
  // A distribution is pure DISTRIBUTION: it targets a STUDENT or a CLASS and
  // freezes the published version. No attempt / answer / grade is created here.

  const dist = { listId: null, title: '', step: 'recipients', summaries: {} };

  function distDateLabel(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    return Number.isNaN(d.getTime()) ? '—' : d.toLocaleString('pt-BR');
  }

  function localInputToIso(value) {
    if (!value) return null;
    const d = new Date(value);
    return Number.isNaN(d.getTime()) ? null : d.toISOString();
  }

  async function hydrateDistributionMeta(ids) {
    await Promise.all((ids || []).map(async (id) => {
      const el = document.querySelector(`.qb-mylist-dist-meta[data-id="${id}"]`);
      if (!el) return;
      try {
        const res = await fetch(`${API}/lists/${id}/assignments`, { headers: authHeaders() });
        if (!res.ok) throw new Error(`HTTP ${res.status}`);
        const data = await res.json();
        dist.summaries[id] = data;
        const s = data.summary || {};
        const last = s.last_distributed_at ? distDateLabel(s.last_distributed_at) : '—';
        el.textContent = `Distribuições: ${s.count || 0} · ativas: ${s.active || 0} · canceladas: ${s.cancelled || 0} · última: ${last}`;
      } catch (err) {
        el.textContent = 'Distribuições: indisponível';
      }
    }));
  }

  async function toggleDistributions(listId) {
    const panel = document.querySelector(`.qb-dist-list[data-id="${listId}"]`);
    if (!panel) return;
    if (!panel.hidden) { panel.hidden = true; return; }
    panel.hidden = false;
    panel.innerHTML = '<p class="qb-list-status">Carregando…</p>';
    try {
      const res = await fetch(`${API}/lists/${listId}/assignments`, { headers: authHeaders() });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      if (!data.items.length) { panel.innerHTML = '<div class="empty-text">Nenhuma distribuição ainda.</div>'; return; }
      panel.innerHTML = `<table class="qb-dist-table">
        <thead><tr><th>Alvo</th><th>Distribuída em</th><th>Período</th><th>Status</th><th>Disponibilidade</th></tr></thead>
        <tbody>${data.items.map((a) => `
          <tr>
            <td>${esc(a.target_type === 'CLASS' ? 'Turma' : 'Aluno')}: ${esc(a.target_id)}</td>
            <td>${esc(distDateLabel(a.created_at))}</td>
            <td>${esc(distDateLabel(a.available_from))} → ${esc(distDateLabel(a.due_at))}</td>
            <td>${esc(a.status)}</td>
            <td>${esc(a.availability)}</td>
          </tr>`).join('')}</tbody></table>`;
    } catch (err) {
      panel.innerHTML = '<div class="empty-text">Não foi possível carregar as distribuições.</div>';
    }
  }

  function distAlert(message, kind) {
    const box = $('qb-dist-alert');
    if (!message) { box.hidden = true; box.textContent = ''; return; }
    box.hidden = false;
    box.textContent = message;
    box.className = `alert-box ${kind === 'error' ? 'alert-error' : 'alert-info'}`;
  }

  function setDistStep(step) {
    dist.step = step;
    distAlert('');
    document.querySelectorAll('#qb-dist .qb-dist-panel').forEach((p) => { p.hidden = p.dataset.dstep !== step; });
    document.querySelectorAll('#qb-dist-steps li').forEach((li) => li.classList.toggle('active', li.dataset.dstep === step));
    if (step === 'review') renderDistReview();
  }

  function currentDistTarget() {
    const checked = document.querySelector('input[name="qb-dist-target"]:checked');
    return checked ? checked.value : 'CLASS';
  }

  function renderDistReview() {
    const tt = currentDistTarget();
    const tid = $('qb-dist-target-id').value.trim();
    const from = localInputToIso($('qb-dist-available-from').value);
    const due = localInputToIso($('qb-dist-due-at').value);
    $('qb-dist-review').innerHTML = `
      <ul class="qb-finalize-list">
        <li><strong>Lista:</strong> ${esc(dist.title)}</li>
        <li><strong>Destinatário:</strong> ${tt === 'CLASS' ? 'Turma' : 'Aluno'} — ${esc(tid || '(não informado)')}</li>
        <li><strong>Disponível a partir de:</strong> ${esc(from ? distDateLabel(from) : 'imediatamente')}</li>
        <li><strong>Prazo:</strong> ${esc(due ? distDateLabel(due) : 'sem prazo')}</li>
      </ul>
      <p class="qb-dist-hint">A distribuição preserva a versão finalizada, a ordem das questões e a configuração de gabarito.</p>`;
  }

  function openDistributeDialog(listId, title) {
    dist.listId = listId;
    dist.title = title || '';
    $('qb-dist-list-name').textContent = dist.title;
    $('qb-dist-target-id').value = '';
    $('qb-dist-available-from').value = '';
    $('qb-dist-due-at').value = '';
    document.querySelectorAll('input[name="qb-dist-target"]').forEach((r) => { r.checked = r.value === 'CLASS'; });
    $('qb-dist-target-label').textContent = 'Identificador da turma';
    $('qb-dist-done').innerHTML = '';
    setDistStep('recipients');
    $('qb-dist').hidden = false;
  }

  function closeDistributeDialog() { $('qb-dist').hidden = true; }

  async function submitDistribution() {
    const tt = currentDistTarget();
    const tid = $('qb-dist-target-id').value.trim();
    if (!tid) { distAlert('Informe o identificador do destinatário.', 'error'); setDistStep('recipients'); return; }
    const body = {
      target_type: tt,
      target_id: tid,
      available_from: localInputToIso($('qb-dist-available-from').value),
      due_at: localInputToIso($('qb-dist-due-at').value),
    };
    try {
      const res = await fetch(`${API}/lists/${dist.listId}/assignments`, {
        method: 'POST', headers: authHeaders(), body: JSON.stringify(body),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) throw new Error(data.detail || `HTTP ${res.status}`);
      const replayed = res.headers.get('x-idempotent-replay') === 'true' || res.status === 200;
      $('qb-dist-done').innerHTML = `
        <p><strong>${replayed ? 'Esta atividade já estava distribuída para esse destinatário.' : 'Atividade distribuída.'}</strong></p>
        <ul class="qb-finalize-list">
          <li>Destinatário: ${esc(data.target_type === 'CLASS' ? 'Turma' : 'Aluno')} — ${esc(data.target_id)}</li>
          <li>Status: ${esc(data.status)} · Disponibilidade: ${esc(data.availability)}</li>
          <li>Questões: ${esc(String(data.question_count))}</li>
        </ul>`;
      setDistStep('done');
    } catch (err) {
      distAlert(`Não foi possível distribuir: ${err.message}`, 'error');
    }
  }

  // ---------- PHASE 14: list generator wizard ----------

  function listAlert(message, kind) {
    const box = $('qb-list-alert');
    if (!message) { box.hidden = true; box.textContent = ''; return; }
    box.hidden = false;
    box.textContent = message;
    box.className = `alert-box ${kind === 'error' ? 'alert-error' : 'alert-info'}`;
  }

  async function ensureConfigOptions() {
    if (state.list.configOptions) return;
    try {
      const res = await fetch(`${API}/lists/config-options`, { headers: authHeaders() });
      state.list.configOptions = await res.json();
    } catch (err) { state.list.configOptions = null; }
  }

  function setListStep(step) {
    state.list.step = step;
    listAlert('');
    document.querySelectorAll('.qb-wizard-panel').forEach((p) => {
      p.hidden = p.dataset.wstep !== step;
    });
    document.querySelectorAll('#qb-wizard-steps li').forEach((li) => {
      li.classList.toggle('active', li.dataset.step === step);
    });
    if (step === 'config') {
      $('qb-list-title').value = state.list.config.title;
      $('qb-list-instructions').value = state.list.config.instructions;
      $('qb-list-count').textContent = String(state.selection.length);
    }
    if (step === 'review') renderReviewList();
    if (step === 'answerkey') renderAnswerKeyStep();
    if (step === 'preview') renderListPreview();
    if (step === 'finalize') renderFinalize();
  }

  function renderReviewList() {
    const list = $('qb-review-list');
    if (!state.selection.length) {
      list.innerHTML = '<div class="empty-text">Nenhuma questão selecionada.</div>';
      return;
    }
    list.innerHTML = state.selection.map((row, i) => `
      <li class="qb-selection-item" draggable="true" data-index="${i}">
        <span class="qb-selection-pos">${i + 1}.</span>
        <span class="qb-selection-label">${esc(row.label)}${row.content ? ` <span class="qb-tag">${esc(row.content)}</span>` : ''}</span>
        <span class="qb-selection-controls">
          <button class="btn btn-secondary qb-rev-up" type="button" data-index="${i}" aria-label="Mover para cima" ${i === 0 ? 'disabled' : ''}>↑</button>
          <button class="btn btn-secondary qb-rev-down" type="button" data-index="${i}" aria-label="Mover para baixo" ${i === state.selection.length - 1 ? 'disabled' : ''}>↓</button>
          <button class="btn btn-secondary qb-rev-remove" type="button" data-index="${i}">Remover questão</button>
        </span>
      </li>`).join('');
  }

  function renderAnswerKeyStep() {
    const co = state.list.configOptions;
    const ak = state.list.config.answerKey;
    document.querySelectorAll('input[name="qb-ak"]').forEach((r) => { r.checked = r.value === ak; });
    const showRes = ak === 'KEY_AND_RESOLUTION_AT_END';
    $('qb-resolution-style').hidden = !showRes;
    document.querySelectorAll('input[name="qb-res"]').forEach((r) => {
      r.checked = r.value === state.list.config.resolutionStyle;
    });
    $('qb-resolution-note').textContent = (co && co.resolution_note)
      ? co.resolution_note
      : 'Não há resoluções oficiais armazenadas; a resolução será marcada como indisponível nesta fase.';
  }

  function optionsBlock(opts) {
    return `<ol class="qb-option-list">${(opts || []).map((o) =>
      `<li class="qb-option"><span class="qb-option-key">${esc(o.key)}</span><span>${esc(o.text)}</span></li>`).join('')}</ol>`;
  }

  function renderListPreview() {
    const d = state.list.definition;
    if (!d) { $('qb-list-preview').innerHTML = '<p class="qb-list-status">Gere a lista para pré-visualizar.</p>'; return; }
    const cfg = d.configuration;
    const questions = d.items.map((it) => `
      <article class="qb-preview-question">
        <h4>${it.position}. ${esc(it.year || '')} — Q${esc(it.official_number)} <span class="qb-tag">${esc(it.enem_area || '—')}</span>
          ${it.content_code ? `<span class="qb-tag">${esc(it.content_code)}</span>` : ''}</h4>
        <p class="qb-preview-statement-text">${esc(it.statement)}</p>
        ${optionsBlock(it.options)}
      </article>`).join('');
    let keyBlock = '';
    if (d.answer_key_included) {
      keyBlock = `<section class="qb-preview-key"><h4>Gabarito</h4><ol class="qb-key-list">${
        d.items.map((it) => `<li>${it.position}. <strong>${esc(it.answer_key.correct_option_key)}</strong>${
          d.resolution_included ? ` — <em>${esc(it.answer_key.resolution && it.answer_key.resolution.available
            ? it.answer_key.resolution.text : 'Resolução não disponível.')}</em>` : ''}</li>`).join('')
      }</ol></section>`;
    }
    $('qb-list-preview').innerHTML = `
      <header class="qb-preview-head">
        <h3>${esc(cfg.title)}</h3>
        ${cfg.instructions ? `<p class="qb-preview-instructions">${esc(cfg.instructions)}</p>` : ''}
        <p class="qb-list-hint">${d.question_count} questões · ${esc(cfg.answer_key_presentation)}${
          d.resolution_included ? ` · ${esc(cfg.resolution_style)}` : ''}</p>
      </header>
      ${questions}
      ${keyBlock}`;
  }

  function renderFinalize() {
    const d = state.list.definition;
    const cfg = d ? d.configuration : {
      title: state.list.config.title, answer_key_presentation: state.list.config.answerKey,
      resolution_style: state.list.config.resolutionStyle,
    };
    const count = d ? d.question_count : state.selection.length;
    const order = d ? d.items.map((i) => `Q${i.official_number}`).join(', ')
                    : state.selection.map((s) => s.label).join(', ');
    $('qb-finalize-summary').innerHTML = `
      <ul class="qb-finalize-list">
        <li><span>Título</span><strong>${esc(cfg.title)}</strong></li>
        <li><span>Total de questões</span><strong>${count}</strong></li>
        <li><span>Ordem</span><strong>${esc(order)}</strong></li>
        <li><span>Gabarito</span><strong>${esc(cfg.answer_key_presentation)}</strong></li>
        <li><span>Resolução</span><strong>${cfg.answer_key_presentation === 'KEY_AND_RESOLUTION_AT_END'
          ? esc(cfg.resolution_style) + ' (indisponível nesta fase)' : '—'}</strong></li>
        <li><span>Tempo estimado</span><strong>—</strong></li>
      </ul>`;
    $('qb-finalize-saved').hidden = true;
  }

  function showSavedList(summary, finalized) {
    const box = $('qb-finalize-saved');
    box.hidden = false;
    box.innerHTML = `
      <p><strong>Lista salva.</strong> Status: ${esc(summary.status)}${finalized ? ' (imutável)' : ''}.</p>
      <div class="qb-mylist-actions">
        <a class="btn btn-secondary" href="${API}/lists/${esc(summary.id)}/export.pdf">Exportar PDF</a>
        <a class="btn btn-secondary" href="${API}/lists/${esc(summary.id)}/export.docx">Exportar DOCX</a>
        <button class="btn btn-secondary" type="button" id="qb-saved-open-mylists">Ver em Minhas Listas</button>
      </div>`;
    const b = document.getElementById('qb-saved-open-mylists');
    if (b) b.addEventListener('click', () => switchView('mylists'));
    listAlert(finalized ? 'Lista finalizada e imutável. Exporte em PDF ou DOCX.'
                        : 'Rascunho salvo. Você pode editar ou finalizar depois.', 'info');
  }

  async function generateList() {
    const cfg = state.list.config;
    cfg.title = $('qb-list-title').value.trim();
    cfg.instructions = $('qb-list-instructions').value.trim();
    if (!state.selection.length) { listAlert('Selecione ao menos uma questão.', 'error'); return false; }
    if (!cfg.title) { listAlert('Informe o título da lista.', 'error'); setListStep('config'); return false; }
    try {
      const body = {
        question_version_ids: state.selection.map((s) => s.question_version_id),
        title: cfg.title,
        instructions: cfg.instructions || null,
        activity_mode: cfg.mode,
        answer_key_presentation: cfg.answerKey,
        resolution_style: cfg.answerKey === 'KEY_AND_RESOLUTION_AT_END' ? cfg.resolutionStyle : null,
      };
      const res = await fetch(`${API}/lists/generate`, {
        method: 'POST', headers: authHeaders(), body: JSON.stringify(body),
      });
      if (!res.ok) {
        const detail = await res.json().catch(() => ({}));
        throw new Error(detail.detail || `HTTP ${res.status}`);
      }
      state.list.definition = await res.json();
      return true;
    } catch (err) {
      listAlert(`Não foi possível gerar a lista: ${err.message}`, 'error');
      return false;
    }
  }

  // ---------- wiring ----------

  function readFiltersFromForm() {
    const f = state.filters;
    f.year = $('qb-filter-year').value;
    f.day = $('qb-filter-day').value;
    f.area = $('qb-filter-area').value;
    f.discipline = $('qb-filter-discipline').value;
    f.content = $('qb-filter-content').value.trim();
    f.subcontent = $('qb-filter-subcontent').value.trim();
    f.status = $('qb-filter-status').value;
    f.difficulty = $('qb-filter-difficulty').value;
    f.source = $('qb-filter-source').value;
    f.mode = $('qb-filter-mode').value.trim();
    f.provisional = $('qb-filter-provisional').checked;
    f.visual = $('qb-filter-visual').checked;
    f.protected = $('qb-filter-protected').checked;
  }

  let searchTimer = null;
  function debouncedReload() {
    clearTimeout(searchTimer);
    searchTimer = setTimeout(() => { state.page = 1; loadList(); }, 300);
  }

  function populateYears() {
    const sel = $('qb-filter-year');
    for (const y of [2017, 2020, 2024, 2025]) {
      const opt = document.createElement('option');
      opt.value = String(y); opt.textContent = String(y);
      sel.appendChild(opt);
    }
  }

  function init() {
    populateYears();
    $('qb-identity-id').addEventListener('change', (e) => {
      state.teacherId = e.target.value.trim() || 'user:prof_mendes';
      if (state.view === 'bank') loadList();
      if (state.view === 'mylists') loadMyLists();
    });
    $('qb-search-form').addEventListener('submit', (e) => {
      e.preventDefault();
      state.search = $('qb-search-text').value;
      readFiltersFromForm();
      state.page = 1;
      loadList();
    });
    $('qb-search-text').addEventListener('input', (e) => { state.search = e.target.value; debouncedReload(); });
    ['qb-filter-year', 'qb-filter-day', 'qb-filter-area', 'qb-filter-discipline', 'qb-filter-status',
     'qb-filter-difficulty', 'qb-filter-source', 'qb-filter-provisional', 'qb-filter-visual',
     'qb-filter-protected'].forEach((id) => {
      $(id).addEventListener('change', () => { readFiltersFromForm(); state.page = 1; loadList(); });
    });
    ['qb-filter-content', 'qb-filter-subcontent', 'qb-filter-mode'].forEach((id) => {
      $(id).addEventListener('input', () => { readFiltersFromForm(); debouncedReload(); });
    });
    $('qb-clear-filters').addEventListener('click', () => {
      $('qb-search-form').reset();
      state.search = '';
      state.filters = { year: '', day: '', area: '', discipline: '', content: '', subcontent: '',
        status: '', difficulty: '', source: '', mode: '', provisional: false, visual: false, protected: false };
      state.page = 1;
      loadList();
    });
    $('qb-page-prev').addEventListener('click', () => { if (state.page > 1) { state.page--; loadList(); } });
    $('qb-page-next').addEventListener('click', () => {
      if (state.page < state.lastPage.totalPages) { state.page++; loadList(); }
    });

    $('qb-list').addEventListener('click', (e) => {
      const open = e.target.closest('.qb-open');
      const toggle = e.target.closest('.qb-toggle');
      if (open) { openPreview(open.dataset.qid); return; }
      if (toggle) { toggleSelect(toggle.dataset.qvid, toggle.dataset.qid); }
    });

    $('qb-preview-close').addEventListener('click', closePreview);
    $('qb-preview').addEventListener('click', (e) => { if (e.target === $('qb-preview')) closePreview(); });
    $('qb-preview-toggle-select').addEventListener('click', (e) => {
      toggleSelect(e.currentTarget.dataset.qvid, e.currentTarget.dataset.qid, state.preview);
    });
    $('qb-preview-replace').addEventListener('click', () => {
      // "Substituir": só devolve o professor ao Banco com o contexto atual preservado.
      closePreview();
      switchView('bank');
      showAlert('Escolha a questão substituta no Banco. Sua seleção e filtros foram preservados.', 'info');
    });

    document.querySelectorAll('.qb-tab').forEach((t) =>
      t.addEventListener('click', () => switchView(t.dataset.qbView)));
    $('qb-selection-back').addEventListener('click', () => switchView('bank'));
    $('qb-selection-clear').addEventListener('click', clearSelection);
    $('qb-selection-finalize').addEventListener('click', finalizeSelection);
    $('qb-selection-configure').addEventListener('click', async () => {
      if (!state.selection.length) { showAlert('Selecione ao menos uma questão.', 'error'); return; }
      await ensureConfigOptions();
      state.list.step = 'config';
      switchView('list');
    });

    // ----- list generator wizard wiring -----
    $('qb-list-title').addEventListener('input', (e) => { state.list.config.title = e.target.value; });
    $('qb-list-instructions').addEventListener('input', (e) => { state.list.config.instructions = e.target.value; });
    $('qb-list-back-selection').addEventListener('click', () => switchView('selection'));
    $('qb-list-to-review').addEventListener('click', () => {
      state.list.config.title = $('qb-list-title').value.trim();
      if (!state.list.config.title) { listAlert('Informe o título da lista.', 'error'); return; }
      setListStep('review');
    });
    $('qb-review-back-config').addEventListener('click', () => setListStep('config'));
    $('qb-review-back-bank').addEventListener('click', () => {
      // return to the bank preserving bank state + selection; user adds a question then comes back
      switchView('bank');
      showAlert('Selecione a questão a adicionar. Sua lista e a ordem foram preservadas.', 'info');
    });
    $('qb-review-to-answerkey').addEventListener('click', () => {
      if (!state.selection.length) { listAlert('A lista precisa de ao menos uma questão.', 'error'); return; }
      setListStep('answerkey');
    });
    $('qb-review-list').addEventListener('click', (e) => {
      const up = e.target.closest('.qb-rev-up');
      const down = e.target.closest('.qb-rev-down');
      const rm = e.target.closest('.qb-rev-remove');
      if (up) { moveSelection(Number(up.dataset.index), -1); renderReviewList(); }
      else if (down) { moveSelection(Number(down.dataset.index), 1); renderReviewList(); }
      else if (rm) { removeSelection(Number(rm.dataset.index)); renderReviewList(); }
    });
    (function reviewDnD() {
      const el = $('qb-review-list');
      let from = null;
      el.addEventListener('dragstart', (e) => {
        const li = e.target.closest('.qb-selection-item');
        if (li) from = Number(li.dataset.index);
      });
      el.addEventListener('dragover', (e) => e.preventDefault());
      el.addEventListener('drop', (e) => {
        e.preventDefault();
        const li = e.target.closest('.qb-selection-item');
        if (li == null || from == null) return;
        const to = Number(li.dataset.index);
        if (to === from) return;
        const [row] = state.selection.splice(from, 1);
        state.selection.splice(to, 0, row);
        from = null;
        updateSelectionCount();
        renderReviewList();
      });
    })();
    document.querySelectorAll('input[name="qb-ak"]').forEach((r) => r.addEventListener('change', (e) => {
      state.list.config.answerKey = e.target.value;
      $('qb-resolution-style').hidden = e.target.value !== 'KEY_AND_RESOLUTION_AT_END';
    }));
    document.querySelectorAll('input[name="qb-res"]').forEach((r) => r.addEventListener('change', (e) => {
      state.list.config.resolutionStyle = e.target.value;
    }));
    $('qb-answerkey-back').addEventListener('click', () => setListStep('review'));
    $('qb-answerkey-to-preview').addEventListener('click', async () => {
      const ok = await generateList();
      if (ok) setListStep('preview');
    });
    $('qb-preview-back-answerkey').addEventListener('click', () => setListStep('answerkey'));
    $('qb-preview-edit').addEventListener('click', () => setListStep('config'));
    $('qb-preview-to-finalize').addEventListener('click', () => setListStep('finalize'));
    $('qb-finalize-edit').addEventListener('click', () => setListStep('config'));
    $('qb-finalize-back-selection').addEventListener('click', () => switchView('selection'));
    $('qb-finalize-save-draft').addEventListener('click', async () => {
      const s = await persistCurrentList({ finalize: false });
      if (s) showSavedList(s, false);
    });
    $('qb-finalize-confirm').addEventListener('click', async () => {
      const s = await persistCurrentList({ finalize: true });
      if (s) showSavedList(s, true);
    });

    // ----- Minhas Listas -----
    ['qb-mylists-q', 'qb-mylists-status', 'qb-mylists-order', 'qb-mylists-dir'].forEach((id) =>
      $(id).addEventListener('input', () => { if (state.view === 'mylists') loadMyLists(); }));
    $('qb-mylists-list').addEventListener('click', async (e) => {
      const open = e.target.closest('.qb-ml-open');
      const edit = e.target.closest('.qb-ml-edit');
      const fin = e.target.closest('.qb-ml-finalize');
      const distribute = e.target.closest('.qb-ml-distribute');
      const distributions = e.target.closest('.qb-ml-distributions');
      if (open || edit) { await openStoredList((open || edit).dataset.id); if (edit) setListStep('config'); return; }
      if (distribute) {
        const row = distribute.closest('.qb-mylist-row');
        openDistributeDialog(distribute.dataset.id, row ? row.dataset.title : '');
        return;
      }
      if (distributions) { await toggleDistributions(distributions.dataset.id); return; }
      if (fin) {
        const r = await fetch(`${API}/lists/${fin.dataset.id}/finalize`, { method: 'POST', headers: authHeaders() });
        if (!r.ok) showAlert('Não foi possível finalizar.', 'error');
        loadMyLists();
      }
    });

    // ----- Distribuir atividade (PHASE 16) -----
    $('qb-dist-close').addEventListener('click', closeDistributeDialog);
    document.querySelectorAll('input[name="qb-dist-target"]').forEach((r) => r.addEventListener('change', () => {
      $('qb-dist-target-label').textContent = currentDistTarget() === 'CLASS'
        ? 'Identificador da turma' : 'Identificador do aluno';
    }));
    $('qb-dist-to-period').addEventListener('click', () => {
      if (!$('qb-dist-target-id').value.trim()) { distAlert('Informe o identificador do destinatário.', 'error'); return; }
      setDistStep('period');
    });
    $('qb-dist-back-recipients').addEventListener('click', () => setDistStep('recipients'));
    $('qb-dist-to-review').addEventListener('click', () => setDistStep('review'));
    $('qb-dist-back-period').addEventListener('click', () => setDistStep('period'));
    $('qb-dist-confirm').addEventListener('click', submitDistribution);
    $('qb-dist-finish').addEventListener('click', () => { closeDistributeDialog(); loadMyLists(); });
    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('qb-dist').hidden) closeDistributeDialog(); });

    const selList = $('qb-selection-list');
    selList.addEventListener('click', (e) => {
      const up = e.target.closest('.qb-sel-up');
      const down = e.target.closest('.qb-sel-down');
      const rm = e.target.closest('.qb-sel-remove');
      if (up) moveSelection(Number(up.dataset.index), -1);
      else if (down) moveSelection(Number(down.dataset.index), 1);
      else if (rm) removeSelection(Number(rm.dataset.index));
    });
    let dragIndex = null;
    selList.addEventListener('dragstart', (e) => {
      const li = e.target.closest('.qb-selection-item');
      if (li) { dragIndex = Number(li.dataset.index); e.dataTransfer.effectAllowed = 'move'; }
    });
    selList.addEventListener('dragover', (e) => { e.preventDefault(); });
    selList.addEventListener('drop', (e) => {
      e.preventDefault();
      const li = e.target.closest('.qb-selection-item');
      if (li == null || dragIndex == null) return;
      const dropIndex = Number(li.dataset.index);
      if (dropIndex === dragIndex) return;
      const [row] = state.selection.splice(dragIndex, 1);
      state.selection.splice(dropIndex, 0, row);
      dragIndex = null;
      renderSelection();
    });

    document.addEventListener('keydown', (e) => { if (e.key === 'Escape' && !$('qb-preview').hidden) closePreview(); });

    loadList();
  }

  // expose a tiny surface for tests / host integration (no globals otherwise)
  window.__questionBank = {
    state, toggleSelect, moveSelection, removeSelection, clearSelection, buildQuery,
    setListStep, generateList, switchView, persistCurrentList, loadMyLists, openStoredList,
    openDistributeDialog, setDistStep, submitDistribution, toggleDistributions, hydrateDistributionMeta,
  };

  init();
});
