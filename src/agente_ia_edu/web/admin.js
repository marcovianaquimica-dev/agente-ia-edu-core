/* AGENTE IA EDU — Portal de Administração da Plataforma.
 *
 * Pure frontend over the already-existing /api/v1/admin/* backend
 * (Phase 12A, Platform Administration & Multi-tenancy). No new backend
 * behavior beyond GET /schools/{id}/users and DELETE .../users/{link_id},
 * both thin reads/writes over the pre-existing UserSchoolLink model.
 */
document.addEventListener('DOMContentLoaded', () => {
  'use strict';

  const API = '/api/v1/admin';
  const state = {
    identity: 'user:ADMIN',
    schools: [],
    selectedSchool: null,
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

  // ---------- Schools list ----------

  async function loadSchools() {
    const container = $('schools-list');
    try {
      const res = await fetch(`${API}/schools`, { headers: authHeaders() });
      if (res.status === 403) {
        container.innerHTML = '<p class="empty-text">Acesso negado — este usuário não tem papel PLATFORM_ADMIN.</p>';
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
              <td>${esc(s.status)}</td>
              <td>${(s.modules || []).filter((m) => m.enabled).map((m) => esc(m.module_key)).join(', ') || '—'}</td>
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
      external_identifier: $('school-external-id').value.trim() || null,
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

  // ---------- School detail: modules + status + user links ----------

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
  }

  $('btn-close-school-detail').addEventListener('click', () => {
    $('school-detail').hidden = true;
    state.selectedSchool = null;
  });

  function renderSchoolDetail() {
    const s = state.selectedSchool;
    $('school-detail-title').textContent = `${s.name} (${s.code})`;
    $('school-status-select').value = s.status;

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

  // ---------- User links (permissions) ----------

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
        <thead><tr><th>Usuário</th><th>Papel</th><th>Escopo</th><th>Vinculado em</th><th></th></tr></thead>
        <tbody>
          ${links.map((l) => `
            <tr>
              <td>${esc(l.external_user_id)}</td>
              <td>${esc(l.role)}</td>
              <td>${esc(l.scope_type)}${l.scope_external_id ? ` — ${esc(l.scope_external_id)}` : ''}</td>
              <td>${formatDate(l.created_at)}</td>
              <td><button class="btn btn-secondary" type="button" data-deactivate-link="${l.id}">Desativar</button></td>
            </tr>
          `).join('')}
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
    const body = {
      external_user_id: $('link-user-id').value.trim(),
      role: $('link-role').value,
      scope_type: $('link-scope-type').value,
      scope_external_id: $('link-scope-id').value.trim() || null,
    };
    try {
      const res = await fetch(`${API}/schools/${s.id}/users`, { method: 'POST', headers: authHeaders(), body: JSON.stringify(body) });
      if (!res.ok) throw new Error(await errorDetail(res));
      $('link-form').reset();
      $('link-scope-type').value = 'SCHOOL';
      formMsg('link-form-msg', 'Usuário vinculado.', true);
      loadSchoolUsers();
    } catch (err) {
      formMsg('link-form-msg', `Não foi possível vincular: ${err.message}`, false);
    }
  });

  // ---------- Identity ----------

  $('admin-identity-id').addEventListener('change', (e) => {
    state.identity = e.target.value.trim() || 'user:ADMIN';
    $('school-detail').hidden = true;
    state.selectedSchool = null;
    loadSchools();
  });

  loadSchools();
});
