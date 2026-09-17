document.addEventListener('DOMContentLoaded', () => {
  const state = {
    staffToken: sessionStorage.getItem('receptionStaffToken') || '',
    schoolId: sessionStorage.getItem('receptionSchoolId') || '',
    currentCandidate: null,
    activationToken: '',
  };
  const views = document.querySelectorAll('.view');
  const notice = document.getElementById('notice');
  const sessionPanel = document.getElementById('session-panel');

  const escapeHtml = value => String(value ?? '').replace(/[&<>'"]/g, char => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
  })[char]);
  const formatDate = value => value ? new Intl.DateTimeFormat('pt-BR', { dateStyle: 'short', timeStyle: 'short' }).format(new Date(value)) : 'Não registrado';
  const statusLabel = status => ({
    PRE_REGISTRATION: 'Pré-cadastro', DIAGNOSTIC_RELEASED: 'Diagnóstico liberado',
    DIAGNOSTIC_IN_PROGRESS: 'Diagnóstico em andamento', DIAGNOSTIC_COMPLETED: 'Diagnóstico concluído',
    FEEDBACK_AVAILABLE: 'Devolutiva disponível', CONVERTED: 'Matrícula / conversão'
  })[status] || status;
  const statusClass = status => status === 'FEEDBACK_AVAILABLE' ? 'feedback' : (status === 'DIAGNOSTIC_IN_PROGRESS' ? 'progress' : '');

  function showNotice(message, type = '') {
    notice.textContent = message;
    notice.className = `notice ${type}`;
    notice.hidden = false;
  }
  function clearNotice() { notice.hidden = true; }
  function headers(token = state.staffToken) { return { 'Content-Type': 'application/json', Authorization: `Bearer ${token}` }; }
  // The backend's shared authorization service reports failures in English
  // (an internal/API-contract string, not meant for display) - translate the
  // known patterns here rather than showing them raw to the atendente.
  const ROLE_LABELS = {
    DIRECTOR: 'Diretor(a)', COORDINATOR: 'Coordenador(a)', SECRETARY: 'Secretaria',
    TEACHER: 'Professor(a)', STUDENT: 'Aluno(a)', PLATFORM_ADMIN: 'Administrador da Plataforma',
  };
  function translateDetail(detail) {
    if (!detail) return detail;
    if (Array.isArray(detail)) {
      // FastAPI 422 validation errors: a list of {msg, loc, type} - not a
      // string, so the regex/lookup logic below would throw on it.
      return detail.map(e => (e && e.msg) || 'Dado inválido.').join(' ');
    }
    if (typeof detail === 'object') {
      return detail.message || 'Não foi possível concluir a operação.';
    }
    const roleMatch = detail.match(/^Role required: (.+)$/);
    if (roleMatch) {
      const roles = roleMatch[1].split(', ').map(r => ROLE_LABELS[r] || r).join(', ');
      return `Perfil de acesso necessário: ${roles}.`;
    }
    const scopeMatch = detail.match(/^Scope type mismatch: expected (.+)$/);
    if (scopeMatch) return `Contexto incompatível: era esperado o escopo ${scopeMatch[1]}.`;
    const moduleMatch = detail.match(/^Module '(.+)' is not enabled for the current school\.$/);
    if (moduleMatch) return `O módulo '${moduleMatch[1]}' não está habilitado para esta escola.`;
    const known = {
      'User account is inactive.': 'A conta do usuário está inativa.',
      'This user does not belong to a school context.': 'Este usuário não pertence a um contexto de escola.',
      'User does not have access to the requested school.': 'Este usuário não tem acesso à escola informada.',
      'Scope mismatch for the requested context.': 'O contexto informado não corresponde ao escopo esperado.',
    };
    return known[detail] || detail;
  }
  async function api(path, options = {}) {
    const response = await fetch(path, { ...options, headers: { ...headers(), ...(options.headers || {}) } });
    if (!response.ok) {
      const error = await response.json().catch(() => ({}));
      throw new Error(translateDetail(error.detail) || 'Não foi possível concluir a operação.');
    }
    return response.json();
  }
  function switchView(name) {
    views.forEach(view => view.classList.toggle('active', view.id === `view-${name}`));
    document.querySelectorAll('.nav-button').forEach(button => button.classList.toggle('active', button.dataset.view === name));
    document.getElementById('page-title').textContent = name === 'new' ? 'Novo atendimento' : (name === 'detail' ? 'Acompanhamento' : 'Atendimentos recentes');
    clearNotice();
  }
  function requireSession() {
    if (state.staffToken && state.schoolId) return true;
    sessionPanel.hidden = false;
    showNotice('Informe o contexto autenticado da atendente e da escola.', 'error');
    return false;
  }

  async function loadCandidates(query = '') {
    if (!requireSession()) return;
    const body = document.getElementById('candidate-list');
    body.innerHTML = '<tr><td colspan="5" class="empty">Carregando atendimentos...</td></tr>';
    try {
      const params = new URLSearchParams({ school_id: state.schoolId });
      if (query) params.set('q', query);
      const candidates = await api(`/api/v1/reception/candidates?${params}`);
      body.innerHTML = candidates.length ? candidates.map(candidate => `
        <tr><td><strong>${escapeHtml(candidate.preferred_name || candidate.full_name)}</strong><br><small>${escapeHtml(candidate.full_name)}</small></td>
        <td>${escapeHtml(candidate.phone)}<br><small>${escapeHtml(candidate.email)}</small></td>
        <td><span class="status ${statusClass(candidate.status)}">${escapeHtml(statusLabel(candidate.status))}</span></td>
        <td>${formatDate(candidate.updated_at)}</td><td><button class="row-action" data-candidate="${candidate.id}">Abrir →</button></td></tr>`).join('') : '<tr><td colspan="5" class="empty">Nenhum atendimento encontrado.</td></tr>';
      body.querySelectorAll('[data-candidate]').forEach(button => button.onclick = () => openCandidate(button.dataset.candidate));
    } catch (error) { body.innerHTML = '<tr><td colspan="5" class="empty">Não foi possível carregar os atendimentos.</td></tr>'; showNotice(error.message, 'error'); }
  }

  async function openCandidate(id) {
    try {
      state.currentCandidate = await api(`/api/v1/reception/candidates/${id}`);
      renderCandidate(state.currentCandidate);
      switchView('detail');
    } catch (error) { showNotice(error.message, 'error'); }
  }

  function renderCandidate(candidate) {
    const result = candidate.result;
    const mastery = result?.mastery_map || [];
    const strengths = mastery.filter(item => item.estimated_mastery >= 70);
    const gaps = result?.probable_gaps || [];
    document.getElementById('candidate-detail').innerHTML = `
      <div class="detail-head"><div><h2>${escapeHtml(candidate.preferred_name || candidate.full_name)}</h2><p>${escapeHtml(candidate.full_name)} · ${escapeHtml(candidate.grade_level)}</p></div><span class="status ${statusClass(candidate.status)}">${escapeHtml(statusLabel(candidate.status))}</span></div>
      <div class="detail-grid"><div class="panel"><h3>Dados do candidato</h3><div class="facts">
        <div class="fact"><span>Contato</span><strong>${escapeHtml(candidate.phone)}<br>${escapeHtml(candidate.email)}</strong></div>
        <div class="fact"><span>Responsável</span><strong>${escapeHtml(candidate.guardian_name || 'Não aplicável / não informado')}</strong></div>
        <div class="fact"><span>Contexto</span><strong>${escapeHtml(candidate.academic_year)} · ${escapeHtml(candidate.unit_id)} · ${escapeHtml(candidate.segment_id)}</strong></div>
        <div class="fact"><span>Série e turma</span><strong>${escapeHtml(candidate.grade_level)} · ${escapeHtml(candidate.classroom_id || 'Turma não definida')}</strong></div>
      </div>${candidate.status === 'PRE_REGISTRATION' ? '<div class="release-box"><strong>Pronto para a próxima etapa</strong><p>A liberação cria um convite de estudante vinculado a este atendimento.</p><button class="button primary" id="release-diagnostic">Liberar Diagnóstico Inicial</button></div>' : ''}
      ${result ? '<div class="result-section"><button class="button primary" id="view-diagnostic" type="button">Visualizar diagnóstico</button></div>' : ''}
      ${state.activationToken ? activationMarkup(state.activationToken) : ''}</div>
      <aside class="panel"><h3>Linha do atendimento</h3><div class="timeline">
        <div class="timeline-item active"><strong>Pré-cadastro</strong><small>${formatDate(candidate.created_at)}</small></div>
        <div class="timeline-item ${candidate.released_at ? 'active' : ''}"><strong>Diagnóstico liberado</strong><small>${formatDate(candidate.released_at)}</small></div>
        <div class="timeline-item ${candidate.diagnostic_started_at ? 'active' : ''}"><strong>Início</strong><small>${formatDate(candidate.diagnostic_started_at)}</small></div>
        <div class="timeline-item ${candidate.diagnostic_completed_at ? 'active' : ''}"><strong>Conclusão</strong><small>${formatDate(candidate.diagnostic_completed_at)}</small></div>
      </div><div class="fact result-section"><span>Progresso</span><strong>${candidate.progress_percent == null ? 'Percentual não informado pela API' : `${candidate.progress_percent}%`}</strong></div></aside></div>
      ${result ? resultMarkup(result, strengths, gaps) : ''}`;
    document.getElementById('release-diagnostic')?.addEventListener('click', releaseDiagnostic);
    document.getElementById('view-diagnostic')?.addEventListener('click', () => {
      document.getElementById('diagnostic-result').hidden = false;
      document.getElementById('diagnostic-result').scrollIntoView({ behavior: 'smooth' });
    });
    bindActivationForm();
  }

  function activationMarkup(token) {
    return `<div class="activation-box"><strong>Acesso liberado</strong><p>Ative o convite sob a identidade autenticada do candidato.</p><div class="token"><span>${escapeHtml(token)}</span><button class="button quiet" id="copy-token" type="button">Copiar código</button></div>
      <form id="activation-form"><label>Token de acesso do candidato<input id="candidate-auth-token" type="password" required autocomplete="off"></label><button class="button primary" type="submit">Ativar e abrir diagnóstico</button></form></div>`;
  }
  function bindActivationForm() {
    document.getElementById('copy-token')?.addEventListener('click', async () => { await navigator.clipboard.writeText(state.activationToken); showNotice('Código de ativação copiado.'); });
    document.getElementById('activation-form')?.addEventListener('submit', async event => {
      event.preventDefault();
      const candidateToken = document.getElementById('candidate-auth-token').value;
      try {
        await api('/api/v1/reception/diagnostic-access/activate', { method: 'POST', headers: headers(candidateToken), body: JSON.stringify({ token: state.activationToken }) });
        sessionStorage.setItem('studentAccessToken', candidateToken);
        window.location.assign('/student/#diagnostic');
      } catch (error) { showNotice(error.message, 'error'); }
    });
  }
  async function releaseDiagnostic() {
    try {
      const released = await api(`/api/v1/reception/candidates/${state.currentCandidate.id}/diagnostic-release`, { method: 'POST' });
      state.currentCandidate = released.candidate;
      state.activationToken = released.activation_token;
      renderCandidate(state.currentCandidate);
      showNotice('Diagnóstico liberado e ação registrada.');
    } catch (error) { showNotice(error.message, 'error'); }
  }
  function resultMarkup(result, strengths, gaps) {
    const answered = result.raw_result?.questions_answered;
    const accuracy = answered ? Math.round((result.total_correct / answered) * 100) : null;
    return `<section class="panel result-section" id="diagnostic-result" hidden><h2>Devolutiva do diagnóstico</h2><div class="result-grid">
      <div class="metric"><span>Resultado geral</span><strong>${accuracy == null ? '—' : `${accuracy}%`}</strong></div>
      <div class="metric"><span>Evidências</span><strong>${escapeHtml(result.evidence_count)}</strong></div>
      <div class="metric"><span>Confiança</span><strong>${Math.round(result.overall_confidence * 100)}%</strong></div></div>
      <div class="detail-grid"><div><h3>Pontos de domínio</h3><ul class="result-list">${strengths.length ? strengths.map(item => `<li><strong>${escapeHtml(item.content_name)}</strong>${escapeHtml(item.estimated_mastery)}% de domínio estimado</li>`).join('') : '<li>Nenhum ponto de domínio consolidado foi retornado.</li>'}</ul></div>
      <div><h3>Lacunas e pré-requisitos</h3><ul class="result-list">${gaps.length ? gaps.map(item => `<li><strong>${escapeHtml(item.content_name)}</strong>${escapeHtml(item.estimated_mastery)}% estimado${item.possible_prerequisite_gap ? `<br>Possível pré-requisito: ${escapeHtml(item.possible_prerequisite_gap.content_name)}` : ''}</li>`).join('') : '<li>Nenhuma lacuna foi retornada.</li>'}</ul></div></div>
      <h3 class="result-section">Recomendações da trilha</h3><p>${result.recommendations?.length ? escapeHtml(result.recommendations.join(', ')) : 'Ainda não disponíveis neste contrato de diagnóstico.'}</p>
      <div class="future-actions"><button class="button quiet" disabled title="Contrato de exportação ainda não disponível">PDF</button><button class="button quiet" disabled title="Integração ainda não disponível">WhatsApp</button><button class="button quiet" disabled title="Integração ainda não disponível">E-mail</button></div></section>`;
  }

  document.getElementById('open-session').onclick = () => { sessionPanel.hidden = !sessionPanel.hidden; };
  document.getElementById('session-form').onsubmit = event => {
    event.preventDefault(); state.staffToken = document.getElementById('staff-token').value.trim(); state.schoolId = document.getElementById('school-id').value.trim();
    sessionStorage.setItem('receptionStaffToken', state.staffToken); sessionStorage.setItem('receptionSchoolId', state.schoolId); sessionPanel.hidden = true;
    clearNotice();
    document.getElementById('session-label').textContent = `Escola ${state.schoolId}`; loadCandidates();
  };
  document.querySelectorAll('[data-open-new]').forEach(button => button.onclick = () => requireSession() && switchView('new'));
  document.querySelectorAll('[data-view-dashboard]').forEach(button => button.onclick = () => { switchView('dashboard'); loadCandidates(); });
  document.querySelectorAll('.nav-button').forEach(button => button.onclick = () => button.dataset.view === 'new' ? (requireSession() && switchView('new')) : (switchView('dashboard'), loadCandidates()));
  let searchTimer; document.getElementById('search').addEventListener('input', event => { clearTimeout(searchTimer); searchTimer = setTimeout(() => loadCandidates(event.target.value.trim()), 250); });
  document.getElementById('candidate-form').onsubmit = async event => {
    event.preventDefault(); const formEl = event.currentTarget; const form = new FormData(formEl); const payload = Object.fromEntries(form.entries());
    ['preferred_name', 'birth_date', 'guardian_name', 'classroom_id'].forEach(key => { if (!payload[key]) payload[key] = null; }); payload.school_id = state.schoolId;
    try { const created = await api('/api/v1/reception/candidates', { method: 'POST', body: JSON.stringify(payload) }); formEl.reset(); await openCandidate(created.id); showNotice('Pré-cadastro criado e auditado.'); } catch (error) { showNotice(error.message, 'error'); }
  };
  if (state.schoolId) document.getElementById('session-label').textContent = `Escola ${state.schoolId}`;
  loadCandidates();
});