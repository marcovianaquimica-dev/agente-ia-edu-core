/* AGENTE IA EDU — Portal de Administração da Plataforma.
 *
 * Pure frontend over the already-existing /api/v1/admin/* backend
 * (Phase 12A, Platform Administration & Multi-tenancy) plus the
 * pre-existing Pedagogical Universe endpoints. New backend surface
 * added alongside this file: GET /schools/{id}/users, DELETE
 * .../users/{link_id}, and GET /pedagogical-universes/{id}/catalog-scopes
 * - all thin reads/writes over models that already existed.
 *
 * School address/phone/CNPJ and each management user's name/phone/email
 * live in the existing `metadata` JSON column (School.metadata_,
 * UserSchoolLink.metadata_) - no migration needed for either.
 */
document.addEventListener('DOMContentLoaded', () => {
  'use strict';

  const API = '/api/v1/admin';
  const state = {
    identity: 'user:ADMIN',
    schools: [],
    selectedSchool: null,
    disciplines: null,          // [{id, name, code}] - fetched once, ungated
    schoolUniverse: null,       // this school's SCHOOL-owned PedagogicalUniverse, or null
    schoolUniverseScopes: [],   // that universe's current catalog scopes
  };

  const $ = (id) => document.getElementById(id);
  const authHeaders = () => ({ 'Content-Type': 'application/json', 'Authorization': `Bearer ${state.identity}` });

  function esc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  function showAlert(message, kind) {
    const box = $('admin-alert');
    if (!message) { box.style.display = 'none'; box.textContent = ''; return; }
    box.style.display = 'block';
    box.className = `alert-banner ${kind === 'error' ? 'alert-danger' : 'alert-success'}`;
    box.textContent = message;
  }

  function formMsg(id, text, ok) {
    const el = $(id);
    if (!text) { el.hidden = true; el.textContent = ''; return; }
    el.hidden = false;
    el.textContent = text;
    el.style.color = ok ? '#1a7f37' : '#b3261e';
  }

  async function errorDetail(res) {
    const body = await res.json().catch(() => ({}));
    const d = body.detail;
    return (typeof d === 'string' ? d : (d && d.message)) || `HTTP ${res.status}`;
  }

  function formatDate(isoStr) {
    if (!isoStr) return '-';
    return new Date(isoStr).toLocaleString('pt-BR');
  }

  const STATUS_LABELS = { ACTIVE: 'Ativa', INACTIVE: 'Inativa', SUSPENDED: 'Suspensa' };
  const ROLE_LABELS = {
    DIRECTOR: 'Diretor(a)', COORDINATOR: 'Coordenador(a)', SECRETARY: 'Secretaria',
    TEACHER: 'Professor(a)', STUDENT: 'Aluno(a)', PLATFORM_ADMIN: 'Administrador da Plataforma',
  };
  const SCOPE_LABELS = {
    SCHOOL: 'Escola (toda)', UNIT: 'Unidade', SEGMENT: 'Segmento',
    GRADE_LEVEL: 'Série', CLASSROOM: 'Turma', PLATFORM: 'Plataforma',
  };

  // ---------- Schools list ----------

  async function loadSchools() {
    const container = $('schools-list');
    try {
      const res = await fetch(`${API}/schools`, { headers: authHeaders() });
      if (res.status === 403) {
        container.innerHTML = '<p class="empty-text">Acesso negado — este usuário não tem papel de Administrador da Plataforma.</p>';
        return;
      }
      if (!res.ok) throw new Error(await errorDetail(res));
      state.schools = await res.json();
      renderSchools();
    } catch (err) {
      container.innerHTML = '<p class="empty-text">Não foi possível carregar as escolas.</p>';
    }
  }

  function renderSchools() {
    const container = $('schools-list');
    if (!state.schools.length) {
      container.innerHTML = '<p class="empty-text">Nenhuma escola cadastrada ainda.</p>';
      return;
    }
    container.innerHTML = `
      <table class="data-table">
        <thead><tr><th>Código</th><th>Nome</th><th>Status</th><th>Módulos ativos</th><th>Criada em</th></tr></thead>
        <tbody>
          ${state.schools.map((s) => `
            <tr class="admin-school-row" data-school-id="${s.id}">
              <td><strong>${esc(s.code)}</strong></td>
              <td>${esc(s.name)}</td>
              <td>${esc(STATUS_LABELS[s.status] || s.status)}</td>
              <td>${(s.modules || []).filter((m) => m.enabled).map((m) => esc(MODULE_LABELS[m.module_key] || m.module_key)).join(', ') || '—'}</td>
              <td>${formatDate(s.created_at)}</td>
            </tr>
          `).join('')}
        </tbody>
      </table>`;
    container.querySelectorAll('.admin-school-row').forEach((row) => {
      row.addEventListener('click', () => openSchoolDetail(row.dataset.schoolId));
    });
  }

  // ---------- New school form ----------

  $('btn-new-school').addEventListener('click', () => {
    $('school-form').hidden = false;
    formMsg('school-form-msg', '');
  });
  $('school-cancel-btn').addEventListener('click', () => {
    $('school-form').hidden = true;
    $('school-form').reset();
  });
  $('school-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    formMsg('school-form-msg', '');
    const body = {
      code: $('school-code').value.trim(),
      name: $('school-name').value.trim(),
      short_name: $('school-short-name').value.trim() || null,
      metadata: {
        address: $('school-address').value.trim() || null,
        phone: $('school-phone').value.trim() || null,
        cnpj: $('school-cnpj').value.trim() || null,
      },
    };
    try {
      const res = await fetch(`${API}/schools`, { method: 'POST', headers: authHeaders(), body: JSON.stringify(body) });
      if (!res.ok) throw new Error(await errorDetail(res));
      $('school-form').hidden = true;
      $('school-form').reset();
      showAlert(`✅ Escola "${body.name}" criada.`, 'success');
      loadSchools();
    } catch (err) {
      formMsg('school-form-msg', `Não foi possível criar a escola: ${err.message}`, false);
    }
  });

  // ---------- School detail: info + modules + disciplines + status + user links ----------

  const MODULE_LABELS = { AGENTE_IA_EDU: 'AGENTE IA EDU', REDACAO_IA: 'Redação IA' };
  const ALL_MODULES = ['AGENTE_IA_EDU', 'REDACAO_IA'];

  async function openSchoolDetail(schoolId) {
    try {
      const res = await fetch(`${API}/schools/${schoolId}`, { headers: authHeaders() });
      if (!res.ok) throw new Error(await errorDetail(res));
      state.selectedSchool = await res.json();
    } catch (err) {
      showAlert(`Não foi possível abrir a escola: ${err.message}`, 'error');
      return;
    }
    $('school-detail').hidden = false;
    $('school-detail').scrollIntoView({ behavior: 'smooth', block: 'start' });
    renderSchoolDetail();
    loadSchoolUsers();
    loadSchoolDisciplines();
  }

  $('btn-close-school-detail').addEventListener('click', () => {
    $('school-detail').hidden = true;
    state.selectedSchool = null;
    state.schoolUniverse = null;
    state.schoolUniverseScopes = [];
  });

  function renderSchoolDetail() {
    const s = state.selectedSchool;
    $('school-detail-title').textContent = `${s.name} (${s.code})`;
    $('school-status-select').value = s.status;

    const md = s.metadata || {};
    const infoItems = [
      { label: 'Endereço', value: md.address },
      { label: 'Telefone', value: md.phone },
      { label: 'CNPJ', value: md.cnpj },
    ].filter((item) => item.value);
    $('school-detail-info').innerHTML = infoItems.length
      ? infoItems.map((item) => `
          <div class="admin-info-item"><span>${esc(item.label)}</span><strong>${esc(item.value)}</strong></div>
        `).join('')
      : '<p class="empty-text">Sem endereço, telefone ou CNPJ cadastrados para esta escola.</p>';

    const enabledByKey = {};
    (s.modules || []).forEach((m) => { enabledByKey[m.module_key] = m; });

    $('school-modules').innerHTML = ALL_MODULES.map((key) => {
      const mod = enabledByKey[key];
      const enabled = !!(mod && mod.enabled);
      return `
        <div class="admin-module-row">
          <div>
            <strong>${esc(MODULE_LABELS[key] || key)}</strong>
            <span class="${enabled ? '' : 'badge-off'}"> — ${enabled ? 'ativo' : 'inativo'}</span>
          </div>
          <button class="btn btn-secondary" type="button" data-toggle-module="${key}" data-enabled="${enabled}">
            ${enabled ? 'Desativar' : 'Ativar'}
          </button>
        </div>`;
    }).join('');

    $('school-modules').querySelectorAll('[data-toggle-module]').forEach((btn) => {
      btn.addEventListener('click', () => toggleModule(btn.dataset.toggleModule, btn.dataset.enabled !== 'true'));
    });

    const agenteEnabled = !!(enabledByKey.AGENTE_IA_EDU && enabledByKey.AGENTE_IA_EDU.enabled);
    $('school-disciplines-box').hidden = !agenteEnabled;
  }

  async function toggleModule(moduleKey, enable) {
    const s = state.selectedSchool;
    try {
      const res = await fetch(`${API}/schools/${s.id}/modules/${moduleKey}?enabled=${enable}`, {
        method: 'PATCH', headers: authHeaders(),
      });
      if (!res.ok) throw new Error(await errorDetail(res));
      const updatedSchoolRes = await fetch(`${API}/schools/${s.id}`, { headers: authHeaders() });
      state.selectedSchool = await updatedSchoolRes.json();
      renderSchoolDetail();
      showAlert(`✅ Módulo ${MODULE_LABELS[moduleKey] || moduleKey} ${enable ? 'ativado' : 'desativado'}.`, 'success');
    } catch (err) {
      showAlert(`Não foi possível alterar o módulo: ${err.message}`, 'error');
    }
  }

  $('btn-save-school-status').addEventListener('click', async () => {
    const s = state.selectedSchool;
    const status = $('school-status-select').value;
    formMsg('school-status-msg', '');
    try {
      const res = await fetch(`${API}/schools/${s.id}`, {
        method: 'PATCH', headers: authHeaders(), body: JSON.stringify({ status }),
      });
      if (!res.ok) throw new Error(await errorDetail(res));
      state.selectedSchool = await res.json();
      renderSchoolDetail();
      formMsg('school-status-msg', 'Status salvo.', true);
      loadSchools();
    } catch (err) {
      formMsg('school-status-msg', `Não foi possível salvar: ${err.message}`, false);
    }
  });

  // ---------- Disciplines (AGENTE IA EDU module scope) ----------
  // Backed by PedagogicalUniverse: a SCHOOL-owned, ACTIVE universe scoped
  // to whichever disciplines are checked. No universe yet = every
  // discipline reads as checked (nothing has restricted this school yet).
  // /api/v1/catalog/disciplines is intentionally ungated (unlike
  // /api/v1/catalog/nodes, which itself requires a resolved universe -
  // using it here would be circular for the page that configures one).

  async function loadDisciplines() {
    if (state.disciplines) return state.disciplines;
    const res = await fetch('/api/v1/catalog/disciplines');
    state.disciplines = res.ok ? await res.json() : [];
    return state.disciplines;
  }

  async function loadSchoolDisciplines() {
    const s = state.selectedSchool;
    const listEl = $('school-disciplines-list');
    listEl.innerHTML = '<p class="empty-text">Carregando…</p>';
    try {
      const disciplines = await loadDisciplines();
      const uniRes = await fetch(`${API}/pedagogical-universes`, { headers: authHeaders() });
      const universes = uniRes.ok ? await uniRes.json() : [];
      state.schoolUniverse = universes.find((u) => u.owner_type === 'SCHOOL' && u.owner_external_id === s.id) || null;

      if (state.schoolUniverse) {
        const scopeRes = await fetch(`${API}/pedagogical-universes/${state.schoolUniverse.id}/catalog-scopes`, { headers: authHeaders() });
        state.schoolUniverseScopes = scopeRes.ok ? await scopeRes.json() : [];
      } else {
        state.schoolUniverseScopes = [];
      }
      renderSchoolDisciplines(disciplines);
    } catch (err) {
      listEl.innerHTML = '<p class="empty-text">Não foi possível carregar as disciplinas.</p>';
    }
  }

  function renderSchoolDisciplines(disciplines) {
    const listEl = $('school-disciplines-list');
    if (!disciplines.length) {
      listEl.innerHTML = '<p class="empty-text">Nenhuma disciplina cadastrada no currículo ainda.</p>';
      return;
    }
    const scopedNodeIds = new Set(state.schoolUniverseScopes.map((sc) => sc.catalog_node_id));
    // No universe yet -> nothing has restricted this school -> every box reads checked.
    const noUniverseYet = !state.schoolUniverse;
    listEl.innerHTML = disciplines.map((d) => {
      const checked = noUniverseYet || scopedNodeIds.has(d.id);
      return `
        <label class="admin-discipline-check">
          <input type="checkbox" data-discipline-id="${d.id}" ${checked ? 'checked' : ''}>
          ${esc(d.name)}
        </label>`;
    }).join('');
    listEl.querySelectorAll('[data-discipline-id]').forEach((cb) => {
      cb.addEventListener('change', () => toggleDisciplineScope(cb.dataset.disciplineId, cb.checked, disciplines));
    });
  }

  async function ensureSchoolUniverse(allDisciplineIds, checkedDisciplineIds) {
    const s = state.selectedSchool;
    const res = await fetch(`${API}/pedagogical-universes`, {
      method: 'POST', headers: authHeaders(), body: JSON.stringify({
        external_id: `school-${s.id}`,
        slug: `school-${s.id}`,
        name: `Currículo — ${s.name}`,
        owner_type: 'SCHOOL',
        owner_external_id: s.id,
        status: 'ACTIVE',
      }),
    });
    if (!res.ok) throw new Error(await errorDetail(res));
    const universe = await res.json();

    // Real production identity providers populate institution_id, making
    // this SCHOOL binding reachable there; harmless (just unused) where
    // they don't.
    await fetch(`${API}/pedagogical-universes/${universe.id}/bindings`, {
      method: 'POST', headers: authHeaders(),
      body: JSON.stringify({ subject_type: 'SCHOOL', subject_external_id: s.id }),
    });
    // This dev environment's identity provider never sets institution_id,
    // so the binding above is unreachable today - bind every current
    // teacher/coordinator/director directly so the restriction actually
    // takes effect for them right now.
    await bindStaffToUniverse(universe.id);

    for (const nodeId of allDisciplineIds) {
      if (checkedDisciplineIds.has(nodeId)) {
        await fetch(`${API}/pedagogical-universes/${universe.id}/catalog-scopes`, {
          method: 'POST', headers: authHeaders(),
          body: JSON.stringify({ catalog_node_id: nodeId, scope_kind: 'DISCIPLINE', include_descendants: true }),
        });
      }
    }
    return universe;
  }

  async function bindStaffToUniverse(universeId) {
    const s = state.selectedSchool;
    try {
      const res = await fetch(`${API}/schools/${s.id}/users`, { headers: authHeaders() });
      const links = res.ok ? await res.json() : [];
      const staff = links.filter((l) => ['TEACHER', 'COORDINATOR', 'DIRECTOR'].includes(l.role));
      for (const link of staff) {
        await fetch(`${API}/pedagogical-universes/${universeId}/bindings`, {
          method: 'POST', headers: authHeaders(),
          body: JSON.stringify({ subject_type: 'EXTERNAL_IDENTITY', subject_external_id: link.external_user_id }),
        });
      }
    } catch (err) { /* best-effort - the discipline scope itself still saves */ }
  }

  async function toggleDisciplineScope(nodeId, checked, disciplines) {
    try {
      if (!state.schoolUniverse) {
        // First restriction on a school that had none: create the universe
        // with every discipline EXCEPT the one just unchecked (checking an
        // already-implicitly-checked box with no universe is a no-op).
        const allIds = disciplines.map((d) => d.id);
        const checkedIds = new Set(allIds.filter((id) => id !== nodeId || checked));
        state.schoolUniverse = await ensureSchoolUniverse(allIds, checkedIds);
      } else if (checked) {
        const res = await fetch(`${API}/pedagogical-universes/${state.schoolUniverse.id}/catalog-scopes`, {
          method: 'POST', headers: authHeaders(),
          body: JSON.stringify({ catalog_node_id: nodeId, scope_kind: 'DISCIPLINE', include_descendants: true }),
        });
        if (!res.ok) throw new Error(await errorDetail(res));
      } else {
        const scope = state.schoolUniverseScopes.find((sc) => sc.catalog_node_id === nodeId);
        if (scope) {
          const res = await fetch(`${API}/pedagogical-universes/catalog-scopes/${scope.id}`, { method: 'DELETE', headers: authHeaders() });
          if (!res.ok) throw new Error(await errorDetail(res));
        }
      }
      const scopeRes = await fetch(`${API}/pedagogical-universes/${state.schoolUniverse.id}/catalog-scopes`, { headers: authHeaders() });
      state.schoolUniverseScopes = scopeRes.ok ? await scopeRes.json() : [];
      showAlert('✅ Disciplinas do módulo AGENTE IA EDU atualizadas.', 'success');
    } catch (err) {
      showAlert(`Não foi possível atualizar as disciplinas: ${err.message}`, 'error');
      loadSchoolDisciplines();
    }
  }

  // ---------- User links (school management: director / coordinator / etc.) ----------

  async function loadSchoolUsers() {
    const container = $('school-users-list');
    const s = state.selectedSchool;
    try {
      const res = await fetch(`${API}/schools/${s.id}/users`, { headers: authHeaders() });
      if (!res.ok) throw new Error(await errorDetail(res));
      const links = await res.json();
      renderSchoolUsers(links);
    } catch (err) {
      container.innerHTML = '<p class="empty-text">Não foi possível carregar os vínculos.</p>';
    }
  }

  function renderSchoolUsers(links) {
    const container = $('school-users-list');
    if (!links.length) {
      container.innerHTML = '<p class="empty-text">Nenhum vínculo ativo nesta escola ainda.</p>';
      return;
    }
    container.innerHTML = `
      <table class="data-table">
        <thead><tr><th>Nome</th><th>Papel</th><th>Contato</th><th>Identificador de acesso</th><th>Escopo</th><th>Vinculado em</th><th></th></tr></thead>
        <tbody>
          ${links.map((l) => {
            const md = l.metadata || {};
            const contact = [md.phone, md.email].filter(Boolean).join(' · ');
            return `
            <tr>
              <td>${esc(md.name) || '<span class="empty-text">—</span>'}</td>
              <td>${esc(ROLE_LABELS[l.role] || l.role)}</td>
              <td>${contact ? esc(contact) : '<span class="empty-text">—</span>'}</td>
              <td>${esc(l.external_user_id)}</td>
              <td>${esc(SCOPE_LABELS[l.scope_type] || l.scope_type)}${l.scope_external_id ? ` — ${esc(l.scope_external_id)}` : ''}</td>
              <td>${formatDate(l.created_at)}</td>
              <td><button class="btn btn-secondary" type="button" data-deactivate-link="${l.id}">Desativar</button></td>
            </tr>
          `;
          }).join('')}
        </tbody>
      </table>`;
    container.querySelectorAll('[data-deactivate-link]').forEach((btn) => {
      btn.addEventListener('click', () => deactivateLink(btn.dataset.deactivateLink));
    });
  }

  async function deactivateLink(linkId) {
    const s = state.selectedSchool;
    try {
      const res = await fetch(`${API}/schools/${s.id}/users/${linkId}`, { method: 'DELETE', headers: authHeaders() });
      if (!res.ok && res.status !== 204) throw new Error(await errorDetail(res));
      showAlert('✅ Vínculo desativado.', 'success');
      loadSchoolUsers();
    } catch (err) {
      showAlert(`Não foi possível desativar o vínculo: ${err.message}`, 'error');
    }
  }

  $('link-form').addEventListener('submit', async (e) => {
    e.preventDefault();
    formMsg('link-form-msg', '');
    const s = state.selectedSchool;
    const role = $('link-role').value;
    const body = {
      external_user_id: $('link-user-id').value.trim(),
      role,
      scope_type: $('link-scope-type').value,
      scope_external_id: $('link-scope-id').value.trim() || null,
      metadata: {
        name: $('link-name').value.trim() || null,
        phone: $('link-phone').value.trim() || null,
        email: $('link-email').value.trim() || null,
      },
    };
    try {
      const res = await fetch(`${API}/schools/${s.id}/users`, { method: 'POST', headers: authHeaders(), body: JSON.stringify(body) });
      if (!res.ok) throw new Error(await errorDetail(res));
      $('link-form').reset();
      $('link-scope-type').value = 'SCHOOL';
      formMsg('link-form-msg', 'Usuário vinculado.', true);
      loadSchoolUsers();
      // A newly-linked teacher/coordinator/director should immediately be
      // able to use whatever this school already restricted its AGENTE IA
      // EDU curriculum to, if it restricted anything.
      if (state.schoolUniverse && ['TEACHER', 'COORDINATOR', 'DIRECTOR'].includes(role)) {
        await fetch(`${API}/pedagogical-universes/${state.schoolUniverse.id}/bindings`, {
          method: 'POST', headers: authHeaders(),
          body: JSON.stringify({ subject_type: 'EXTERNAL_IDENTITY', subject_external_id: body.external_user_id }),
        });
      }
    } catch (err) {
      formMsg('link-form-msg', `Não foi possível vincular: ${err.message}`, false);
    }
  });

  // ---------- Identity ----------

  $('admin-identity-id').addEventListener('change', (e) => {
    state.identity = e.target.value.trim() || 'user:ADMIN';
    $('school-detail').hidden = true;
    state.selectedSchool = null;
    state.schoolUniverse = null;
    state.schoolUniverseScopes = [];
    loadSchools();
  });

  loadSchools();
});
