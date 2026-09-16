/* AGENTE IA EDU — Portal do Aluno JS (Phase 11) */

document.addEventListener('DOMContentLoaded', () => {
  const state = {
    currentView: 'dashboard',
    timePeriod: 'academic_year',
    studentId: 'student:alice',
    dashboardData: null,
    evolutionData: null,
    learningPathData: null,
    activePracticeSession: null,
    currentPracticeQuestion: null,
    diagnosticPreferredName: '',
    recommendedContentNodeId: null,
  };

  // UI Element References
  const navItems = document.querySelectorAll('.nav-item');
  const viewPanels = document.querySelectorAll('.view-panel');
  const pageTitle = document.getElementById('page-title');
  const pageSubtitle = document.getElementById('page-subtitle');
  const headerActions = document.querySelector('.header-actions');
  const timePeriodSelect = document.getElementById('time-period-select');
  const sidebar = document.getElementById('sidebar-nav');
  const sidebarBackdrop = document.getElementById('sidebar-backdrop');
  const mobileMenuToggle = document.querySelector('.mobile-menu-toggle');

  function studentHeaders(extra = {}) {
    const accessToken = sessionStorage.getItem('studentAccessToken') || `student:${state.studentId}`;
    return { 'Authorization': `Bearer ${accessToken}`, ...extra };
  }

  function studentRequest(path, options = {}) {
    return fetch(path, {
      ...options,
      headers: studentHeaders(options.headers || {}),
    });
  }

  function setSidebarOpen(isOpen) {
    if (!sidebar) return;

    sidebar.classList.toggle('is-open', isOpen);
    if (sidebarBackdrop) {
      sidebarBackdrop.classList.toggle('is-visible', isOpen);
      sidebarBackdrop.setAttribute('aria-hidden', String(!isOpen));
    }
    if (mobileMenuToggle) {
      mobileMenuToggle.setAttribute('aria-expanded', String(isOpen));
    }
    document.body.classList.toggle('sidebar-open', isOpen && window.innerWidth < 768);
  }

  if (mobileMenuToggle) {
    mobileMenuToggle.addEventListener('click', () => {
      const isOpen = !sidebar?.classList.contains('is-open');
      setSidebarOpen(isOpen);
    });
  }

  if (sidebarBackdrop) {
    sidebarBackdrop.addEventListener('click', () => setSidebarOpen(false));
  }

  // Navigation Click Handlers
  navItems.forEach(item => {
    item.addEventListener('click', () => {
      const targetView = item.getAttribute('data-view');
      if (window.innerWidth < 768) {
        setSidebarOpen(false);
      }
      switchView(targetView);
    });
  });

  window.addEventListener('resize', () => {
    if (window.innerWidth >= 768) {
      setSidebarOpen(false);
      document.body.classList.remove('sidebar-open');
    }
  });

  const btnStartPath = document.getElementById('btn-start-path');
  if (btnStartPath) {
    btnStartPath.onclick = () => switchView('learning-path');
  }

  // Time Period Filter Handler
  timePeriodSelect.addEventListener('change', (e) => {
    state.timePeriod = e.target.value;
    loadDashboardData();
  });

  function switchView(viewName) {
    state.currentView = viewName;
    headerActions.hidden = viewName === 'evolution';

    navItems.forEach(i => {
      if (i.getAttribute('data-view') === viewName) {
        i.classList.add('active');
      } else {
        i.classList.remove('active');
      }
    });

    viewPanels.forEach(p => {
      if (p.id === `view-${viewName}`) {
        p.classList.add('active');
      } else {
        p.classList.remove('active');
      }
    });

    // Update Titles
    const titleMap = {
      'dashboard': { title: 'Dashboard Principal', sub: 'Acompanhe sua orientação pedagógica personalizada' },
      'study-session': { title: 'Momento de Aprendizado', sub: 'Sua sessão de estudo, organizada a partir do tempo que você tem hoje' },
      'diagnostic': { title: 'Diagnóstico Inicial de Aprendizagem', sub: 'Sondagem adaptativa para estimar seu mapa de domínio' },
      'learning-path': { title: 'Minha Trilha de Aprendizagem', sub: 'Sua jornada adaptativa passo a passo' },
      'practice': { title: 'Praticar Questões', sub: 'Treinamento adaptativo focado na sua evolução' },
      'activities': { title: 'Atividades', sub: 'Atividades distribuídas pelos seus professores' },
      'domain': { title: 'Meu Domínio', sub: 'O que suas atividades já mostram por conteúdo do currículo' },
      'study-path': { title: 'Trilha de Estudos', sub: 'Seu próximo passo, considerando desempenho e pré-requisitos' },
      'materials': { title: 'Materiais Teóricos', sub: 'Apostilas, PDFs e resumos organizados' },
      'material-reader': { title: 'Material', sub: 'Estude o conteúdo, seção por seção' },
      'videos': { title: 'Videoaulas Recomendadas', sub: 'Aulas interativas e personalizadas para seu nível' },
      'evolution': { title: 'Minha Evolução', sub: 'Veja o que você já domina e descubra seu melhor próximo passo' },
      'essay': { title: 'Módulo Redação IA', sub: 'Treino de redação no padrão ENEM' },
      'profile': { title: 'Meu Perfil', sub: 'Informações da sua conta e escola' },
    };

    if (titleMap[viewName]) {
      pageTitle.textContent = titleMap[viewName].title;
      pageSubtitle.textContent = titleMap[viewName].sub;
    }

    // Trigger View Loaders
    if (viewName === 'dashboard') loadDashboardData();
    if (viewName === 'diagnostic') initDiagnosticView();
    if (viewName === 'learning-path') loadLearningPathData();
    if (viewName === 'evolution') loadEvolutionData();
    if (viewName === 'materials') loadMaterialsView();
    if (viewName === 'videos') loadVideosView();
    if (viewName === 'activities') loadActivitiesView();
    if (viewName === 'domain') loadDomainView();
    if (viewName === 'study-path') loadStudyPathView();
    if (viewName === 'study-session') loadStudySessionView();
    if (viewName === 'profile') loadProfileView();
  }

  function loadProfileView() {
    const el = document.getElementById('profile-id');
    if (el) el.textContent = sessionStorage.getItem('studentAccessToken') || `student:${state.studentId}`;
  }

  const requestedView = window.location.hash.replace('#', '');
  if (requestedView && document.getElementById(`view-${requestedView}`)) {
    switchView(requestedView);
  }

  // 1. Load Dashboard Data
  async function loadDashboardData() {
    try {
      showDashboardSkeleton();
      const res = await studentRequest(`/api/v1/student/dashboard?time_period=${state.timePeriod}`);
      if (!res.ok) throw new Error('Falha ao carregar dashboard');
      const data = await res.json();
      state.dashboardData = data;
      renderDashboard(data);
    } catch (err) {
      console.warn('Dashboard API call error:', err);
      renderDashboardError();
    }
  }

  function showDashboardSkeleton() {
    document.getElementById('welcome-msg-text').textContent = 'Carregando orientação...';
    document.getElementById('welcome-msg-sub').textContent = 'Analisando seu histórico e contextualização do professor...';
  }

  function renderDashboardError() {
    document.getElementById('welcome-msg-text').textContent = 'Vamos começar pelo seu diagnóstico.';
    document.getElementById('welcome-msg-sub').textContent = 'Assim que ele estiver pronto, sua orientação de estudos aparecerá aqui.';
    document.querySelector('.stats-grid').style.display = 'none';
    document.querySelector('.active-rec-card').style.display = 'none';
    document.querySelector('.action-plan-card').style.display = 'none';
    const banner = document.getElementById('welcome-banner');
    if (!banner.querySelector('#btn-dashboard-diagnostic')) {
      banner.querySelector('.banner-content').insertAdjacentHTML('beforeend', '<button class="btn btn-primary" id="btn-dashboard-diagnostic" type="button">Começar diagnóstico</button>');
      document.getElementById('btn-dashboard-diagnostic').onclick = () => switchView('diagnostic');
    }
  }

  function renderDashboard(data) {
    // Welcome Banner / Empty State Check
    const welcomeMsgText = document.getElementById('welcome-msg-text');
    const welcomeMsgSub = document.getElementById('welcome-msg-sub');

    welcomeMsgText.textContent = data.welcome_message || 'Veja sua evolução';

    if (!data.has_data) {
      renderDashboardError();
      return;
    } else {
      welcomeMsgSub.textContent = `Filtro atual: ${getPeriodLabel(data.time_period)}`;
    }

    document.querySelector('.stats-grid').style.display = '';
    document.querySelector('.active-rec-card').style.display = '';
    document.querySelector('.action-plan-card').style.display = '';

    // Summary Stats
    const sum = data.summary || {};
    document.getElementById('stat-avg').textContent = `${sum.overall_average || 0.0}%`;
    document.getElementById('stat-mastered').textContent = sum.contents_mastered || 0;
    document.getElementById('stat-questions').textContent = sum.questions_answered || 0;
    document.getElementById('stat-streak').textContent = `${sum.streak_days || 1} dia(s)`;

    // Active Recommendation
    const rec = data.active_recommendation;
    if (rec) {
      document.getElementById('rec-target-title').textContent = rec.content_name || 'Conteúdo Recomendado';
      document.getElementById('rec-mastery-text').textContent = `${rec.mastery_score || 0}%`;
      document.getElementById('rec-mastery-bar').style.width = `${rec.mastery_score || 0}%`;
      document.getElementById('rec-reason-text').textContent = rec.reason || 'Recomendação baseada no seu plano de estudo.';
      document.getElementById('rec-context-tag').textContent = getContextTagLabel(rec.context_source);

      // Render Steps Pipeline
      renderStepsFlow(rec.steps || []);
    } else {
      document.getElementById('rec-target-title').textContent = 'Nenhuma recomendação pendente';
      document.getElementById('rec-reason-text').textContent = 'Parabéns! Você está em dia com seus estudos.';
    }

    // Render Action Plan
    renderActionPlan(data.action_plan || {});
  }

  function renderStepsFlow(steps) {
    const container = document.getElementById('rec-steps-flow');
    if (!steps || steps.length === 0) return;

    container.innerHTML = steps.map(s => `
      <div class="step-item ${s.status === 'in_progress' ? 'active' : ''}">
        <div class="step-num">${s.step_number}</div>
        <div class="step-info">
          <strong>${s.title}</strong>
          <span>${s.description}</span>
        </div>
      </div>
    `).join('');
  }

  function renderActionPlan(actionPlan) {
    const dangerList = document.getElementById('plan-list-danger');
    const warningList = document.getElementById('plan-list-warning');
    const successList = document.getElementById('plan-list-success');

    dangerList.innerHTML = renderPlanItems(actionPlan.needs_improvement, 'danger');
    warningList.innerHTML = renderPlanItems(actionPlan.in_development, 'warning');
    successList.innerHTML = renderPlanItems(actionPlan.consolidated, 'success');
  }

  function renderPlanItems(items, type) {
    if (!items || items.length === 0) {
      return '<p class="empty-text">Nenhum conteúdo nesta faixa</p>';
    }
    return items.map(item => `
      <div class="plan-item">
        <strong>${item.content_name}</strong>
        <span class="text-${type}">${item.mastery_score}%</span>
      </div>
    `).join('');
  }

  // 2. Load Learning Path
  async function loadLearningPathData() {
    const container = document.getElementById('path-view-content');
    try {
      const res = await studentRequest('/api/v1/student/learning-path');
      if (!res.ok) throw new Error('Falha ao carregar trilha');
      const data = await res.json();
      state.learningPathData = data;

      container.innerHTML = `
        <div class="path-overview">
          <h3>🎯 Meta Atual: ${data.content_name}</h3>
          <p class="rec-explanation">${data.reason}</p>
          <div class="steps-vertical">
            ${(data.steps || []).map(s => `
              <div class="step-card ${s.status === 'in_progress' ? 'active' : ''}">
                <div class="step-header">
                  <span class="step-badge">Etapa ${s.step_number}</span>
                  <strong>${s.title}</strong>
                </div>
                <p>${s.description}</p>
              </div>
            `).join('')}
          </div>
        </div>
      `;
    } catch (err) {
      container.innerHTML = '<p class="empty-text">Erro ao carregar dados da trilha.</p>';
    }
  }

  // 3. Load Evolution View
  async function loadEvolutionData() {
    const container = document.getElementById('evolution-stats-container');
    container.innerHTML = window.EvolutionView.renderLoading();
    try {
      const res = await studentRequest('/api/v1/student/domain-map');
      if (!res.ok) throw new Error('Falha ao carregar evolução');
      const data = await res.json();
      state.evolutionData = data;
      container.innerHTML = window.EvolutionView.render(data);
    } catch (err) {
      state.evolutionData = null;
      container.innerHTML = window.EvolutionView.renderError();
    }
  }

  document.getElementById('evolution-stats-container').addEventListener('click', event => {
    if (event.target.closest('[data-evolution-retry]')) loadEvolutionData();
    const target = event.target.closest('[data-evolution-view]')?.getAttribute('data-evolution-view');
    if (target) {
      state.recommendedContentNodeId = state.evolutionData?.next_best_action?.target_content_node_id || null;
      switchView(target);
    }
  });

  // 4. Materials View
  async function loadMaterialsView() {
    const container = document.getElementById('materials-list-container');
    if (!state.dashboardData) {
      await loadDashboardData();
    }
    const rec = state.dashboardData?.active_recommendation;
    if (rec && rec.primary_resource) {
      const r = rec.primary_resource;
      container.innerHTML = `
        <div class="card">
          <h4>📖 ${r.title}</h4>
          <p>${r.description || 'Apostila recomendada para revisão.'}</p>
          <span class="badge badge-primary" style="margin-top:10px; display:inline-block;">${r.resource_type}</span>
        </div>
      `;
    } else {
      container.innerHTML = '<p class="empty-text">Acesse a aba Início para ver os materiais da sua trilha recomendada.</p>';
    }
  }

  // 5. Videos View
  function loadVideosView() {
    const container = document.getElementById('videos-list-container');
    container.innerHTML = `
      <div class="video-card card">
        <div class="video-header">
          <h4>🎥 Videoaula Recomendada: Diluição de Soluções</h4>
          <span class="badge badge-accent">Duração: 8 min</span>
        </div>
        <p class="rec-explanation">Vídeo focado na explicação dos conceitos essenciais com exercícios práticos.</p>
        <div class="video-controls" style="margin-top:16px; display:flex; gap:10px;">
          <button class="btn btn-primary" onclick="alert('Iniciando reprodução do vídeo...')">▶ Assistir Vídeo</button>
          <button class="btn btn-secondary" onclick="alert('Buscando próximo candidato a vídeo...')">🔄 Quero outro</button>
        </div>
        <div class="video-feedback" style="margin-top:16px; border-top:1px solid #eee; padding-top:12px;">
          <span style="font-size:13px; font-weight:600; color:#64748b;">Feedback da aula:</span>
          <div style="display:flex; gap:8px; margin-top:8px;">
            <button class="btn btn-secondary" onclick="alert('Feedback registrado: Gostei!')">👍 Gostei</button>
            <button class="btn btn-secondary" onclick="alert('Feedback registrado: Não Gostei')">👎 Não Gostei</button>
          </div>
        </div>
      </div>
    `;
  }

  // 1B. Diagnostic Interactive View
  function initDiagnosticView() {
    const btnStart = document.getElementById('btn-start-diagnostic');
    if (btnStart) {
      btnStart.onclick = startDiagnosticEntry;
    }
  }

  function diagnosticRequest(path, options = {}) {
    return studentRequest(path, {
      ...options,
      headers: {
        'Content-Type': 'application/json',
        ...(options.headers || {}),
      },
    });
  }

  async function startDiagnosticEntry() {
    const area = document.getElementById('diagnostic-interactive-area');
    area.innerHTML = '<div class="diagnostic-loading"><span></span><p>Preparando sua jornada de aprendizagem...</p></div>';

    try {
      const res = await diagnosticRequest('/api/v1/student/diagnostic/entry/start', {
        method: 'POST',
        body: JSON.stringify({ diagnostic_version: 'v1' })
      });
      if (!res.ok) throw new Error('Falha ao iniciar a conversa');
      const diagnostic = await res.json();
      if (diagnostic.entry_status === 'COMPLETED' && diagnostic.next_question) {
        renderDiagnosticQuestion(diagnostic.diagnostic_id, diagnostic.next_question, diagnostic.preferred_name || state.diagnosticPreferredName);
      } else {
        renderDiagnosticProfile(diagnostic);
      }
    } catch (err) {
      renderDiagnosticError('Não conseguimos iniciar seu diagnóstico agora. Tente novamente em alguns instantes.');
    }
  }

  function renderDiagnosticProfile(diagnostic) {
    const area = document.getElementById('diagnostic-interactive-area');
    area.innerHTML = `
      <form class="diagnostic-form" id="diagnostic-profile-form">
        <span class="eyebrow">Primeiro, vamos conversar</span>
        <h2>Quero entender o que você espera dos seus estudos.</h2>
        <p class="form-intro">Escolha quantas opções fizerem sentido para você.</p>
        <fieldset>
          <legend>Qual é seu principal objetivo?</legend>
          <div class="objective-grid">
            ${[
              ['MELHORAR_NOTAS', 'Melhorar minhas notas'], ['APRENDER_MELHOR', 'Aprender melhor as matérias'],
              ['ENEM', 'Me preparar para o ENEM'], ['SUPERAR_DIFICULDADES', 'Superar minhas dificuldades'],
              ['MEU_RITMO', 'Estudar no meu ritmo'], ['PRECISO_DE_AJUDA', 'Ainda não sei'],
            ].map(([value, label]) => `<label class="objective-card"><input type="checkbox" name="objective" value="${value}"><span>${label}</span></label>`).join('')}
          </div>
        </fieldset>
        <div class="form-row">
          <label>Como prefere ser chamado?<input id="preferred-name" name="preferred-name" maxlength="255" autocomplete="given-name" placeholder="Seu nome ou apelido"></label>
          <label>Tem algum assunto que parece mais difícil?<input id="perceived-difficulty" name="difficulty" maxlength="255" placeholder="Opcional"></label>
        </div>
        <label>Quer me contar um pouco mais?<textarea id="diagnostic-free-text" maxlength="2000" rows="3" placeholder="Você pode escrever do seu jeito. Isto só ajuda a orientar seus estudos."></textarea></label>
        <div class="diagnostic-actions"><button class="btn btn-primary" type="submit">Vamos começar</button></div>
      </form>`;
    document.getElementById('diagnostic-profile-form').addEventListener('submit', async event => {
      event.preventDefault();
      const objectives = [...area.querySelectorAll('input[name="objective"]:checked')].map(input => input.value);
      const difficulty = document.getElementById('perceived-difficulty').value.trim();
      state.diagnosticPreferredName = document.getElementById('preferred-name').value.trim();
      await saveDiagnosticProfile(diagnostic.diagnostic_id, {
        preferred_name: state.diagnosticPreferredName || null,
        study_objectives: objectives,
        perceived_difficulties: difficulty ? [difficulty] : [],
        free_text: document.getElementById('diagnostic-free-text').value.trim() || null,
        needs_guidance: objectives.includes('PRECISO_DE_AJUDA'),
        step: 'PREPARATION',
        complete: true,
      });
    });
  }

  async function saveDiagnosticProfile(diagnosticId, payload) {
    try {
      const res = await diagnosticRequest(`/api/v1/student/diagnostic/${diagnosticId}/entry`, { method: 'PUT', body: JSON.stringify(payload) });
      if (!res.ok) throw new Error('Falha ao salvar perfil');
      const data = await res.json();
      if (data.next_question) renderDiagnosticQuestion(diagnosticId, data.next_question, state.diagnosticPreferredName || data.preferred_name);
      else fetchDiagnosticResult(diagnosticId);
    } catch (err) {
      renderDiagnosticError('Não conseguimos salvar suas escolhas. Elas não foram perdidas: tente continuar novamente.');
    }
  }

  function renderDiagnosticQuestion(diagnosticId, question, preferredName = '') {
    const area = document.getElementById('diagnostic-interactive-area');
    area.innerHTML = `
      <section class="diagnostic-question" aria-labelledby="diagnostic-question-title">
        <div class="question-meta">
          <span>Diagnóstico de aprendizagem</span><span>Questão ${question.position}</span>
        </div>
        <p class="question-greeting">${preferredName ? `Vamos por partes, ${preferredName}.` : 'Vamos por partes.'}</p>
        <h2 id="diagnostic-question-title" class="question-text">${question.canonical_text}</h2>
        <div class="options-list" role="radiogroup" aria-label="Alternativas da questão">
          ${(question.options || []).map(opt => `
            <button class="option-item" type="button" role="radio" aria-checked="false" onclick="selectDiagnosticOption(this, '${opt.id}')">
              <span class="option-key">${opt.option_key}</span>
              <span class="option-text">${opt.text}</span>
            </button>
          `).join('')}
        </div>
        <button class="unknown-answer" type="button" onclick="submitDiagnosticUnknown('${diagnosticId}', '${question.selection_id}')">Não sei responder</button>
        <div class="diagnostic-actions"><button class="btn btn-primary" id="btn-submit-diag-answer" disabled onclick="submitDiagnosticAnswer('${diagnosticId}', '${question.selection_id}')">Continuar</button></div>
      </section>
    `;
  }

  window.selectDiagnosticOption = function(element, optionId) {
    document.querySelectorAll('#diagnostic-interactive-area .option-item').forEach(el => el.classList.remove('selected'));
    element.classList.add('selected');
    element.setAttribute('data-selected-id', optionId);
    element.setAttribute('aria-checked', 'true');
    document.getElementById('btn-submit-diag-answer').removeAttribute('disabled');
  };

  window.submitDiagnosticAnswer = async function(diagnosticId, selectionId) {
    const selectedEl = document.querySelector('#diagnostic-interactive-area .option-item.selected');
    if (!selectedEl) return;
    const optionId = selectedEl.getAttribute('data-selected-id');

    await sendDiagnosticAnswer(diagnosticId, selectionId, { selected_option_id: optionId });
  };

  window.submitDiagnosticUnknown = async function(diagnosticId, selectionId) {
    await sendDiagnosticAnswer(diagnosticId, selectionId, { is_unknown: true });
  };

  async function sendDiagnosticAnswer(diagnosticId, selectionId, payload) {
    try {
      const res = await diagnosticRequest(`/api/v1/student/diagnostic/${diagnosticId}/questions/${selectionId}/answer`, {
        method: 'POST', body: JSON.stringify(payload)
      });
      if (!res.ok) throw new Error('Falha ao enviar resposta');
      const data = await res.json();

      if (data.is_complete || !data.next_question) {
        renderDiagnosticTransition(diagnosticId);
      } else {
        const area = document.getElementById('diagnostic-interactive-area');
        area.classList.add('is-transitioning');
        window.setTimeout(() => {
          area.classList.remove('is-transitioning');
          renderDiagnosticQuestion(diagnosticId, data.next_question, state.diagnosticPreferredName);
        }, 180);
      }
    } catch (err) {
      renderDiagnosticError('Não conseguimos registrar essa resposta. Tente novamente.');
    }
  }

  function renderDiagnosticTransition(diagnosticId) {
    const area = document.getElementById('diagnostic-interactive-area');
    area.innerHTML = `<div class="diagnostic-transition"><span class="eyebrow">Etapa concluída</span><h2>Seu diagnóstico está pronto.</h2><p>Analisamos suas respostas para entender seu próximo passo.</p><button class="btn btn-primary" type="button" id="btn-view-result">Ver meu resultado</button></div>`;
    document.getElementById('btn-view-result').onclick = () => fetchDiagnosticResult(diagnosticId);
  }

  async function fetchDiagnosticResult(diagnosticId) {
    const area = document.getElementById('diagnostic-interactive-area');
    try {
      const res = await diagnosticRequest(`/api/v1/student/diagnostic/${diagnosticId}/result`);
      if (!res.ok) throw new Error('Falha ao obter resultado do diagnóstico');
      const result = await res.json();

      renderDiagnosticResult(result);
      loadDashboardData();
    } catch (err) {
      renderDiagnosticError('Seu diagnóstico foi concluído, mas não conseguimos carregar os detalhes agora. Tente abrir esta tela novamente.');
    }
  }

  function renderDiagnosticResult(result) {
    const area = document.getElementById('diagnostic-interactive-area');
    const map = result.mastery_map || [];
    const stronger = map.filter(item => item.coverage_status === 'CONSOLIDATED' || item.estimated_mastery >= 75);
    const developing = map.filter(item => !stronger.includes(item));
    const duration = result.duration_seconds ? `${Math.max(1, Math.round(result.duration_seconds / 60))} min` : 'No seu ritmo';
    area.innerHTML = `<section class="diagnostic-result"><span class="eyebrow">Seu diagnóstico</span><h2>Agora sabemos melhor onde você está.</h2><div class="result-overview"><div><span>Confiança da análise</span><strong>${Math.round((result.overall_confidence || 0) * 100)}%</strong></div><div><span>Questões analisadas</span><strong>${result.total_questions_asked || 0}</strong></div><div><span>Tempo</span><strong>${duration}</strong></div></div><div class="result-columns"><section><h3>O que você já domina</h3>${stronger.length ? stronger.map(item => `<div class="result-item strong"><strong>${item.content_name}</strong><span>Boa evidência até aqui</span></div>`).join('') : '<p class="result-empty">Ainda estamos reunindo evidências sobre seus pontos fortes.</p>'}</section><section><h3>Onde vale avançar</h3>${developing.length ? developing.map(item => `<div class="result-item"><strong>${item.content_name}</strong><span>${item.coverage_status === 'INSUFFICIENT_EVIDENCE' ? 'Vamos entender um pouco mais' : 'Um próximo passo possível'}</span></div>`).join('') : '<p class="result-empty">Nenhum conteúdo precisa de atenção agora.</p>'}</section></div>${(result.probable_gaps || []).length ? `<section class="gentle-insight"><h3>Uma possível conexão</h3><p>Encontramos indícios de que revisar alguns conceitos anteriores pode ajudar você a avançar com mais segurança.</p></section>` : ''}<section class="next-action"><div><span class="eyebrow">Sua próxima melhor ação</span><h3>${developing[0]?.content_name || 'Continuar aprendendo no seu ritmo'}</h3><p>Vamos usar esse ponto de partida para guiar seus próximos estudos.</p></div><button class="btn btn-primary" type="button" id="btn-result-learning-path">Ver minha trilha</button></section></section>`;
    document.getElementById('btn-result-learning-path').onclick = () => switchView('learning-path');
  }

  function renderDiagnosticError(message) {
    const area = document.getElementById('diagnostic-interactive-area');
    area.innerHTML = `<div class="diagnostic-error"><h2>Algo não saiu como esperado.</h2><p>${message}</p><button class="btn btn-secondary" type="button" id="btn-retry-diagnostic">Tentar novamente</button></div>`;
    document.getElementById('btn-retry-diagnostic').onclick = startDiagnosticEntry;
  }

  // Helper Utilities
  function getPeriodLabel(code) {
    const map = {
      'academic_year': 'Ano Letivo Vigente',
      'bimester': 'Bimestre Atual',
      'semester': 'Semestre',
      'last_30_days': 'Últimos 30 Dias',
    };
    return map[code] || 'Ano Letivo';
  }

  function getContextTagLabel(source) {
    const map = {
      'TEACHER': 'Aula do Professor',
      'COORDINATION': 'Orientação da Coordenação',
      'SCHOOL_PLAN': 'Planejamento da Escola',
      'AUTONOMOUS': 'Trilha Autônoma',
    };
    return map[source] || 'Recomendação';
  }

  // 3. Practice Interactive Area (Integrates with Practice Engine)
  const practiceBtn = document.getElementById('btn-create-practice-session');
  if (practiceBtn) {
    practiceBtn.addEventListener('click', startPracticeSession);
  }

  async function startPracticeSession() {
    const area = document.getElementById('practice-interactive-area');
    area.innerHTML = '<p class="empty-text">Iniciando sessão de prática e selecionando questões do banco...</p>';

    try {
      const res = await studentRequest('/api/v1/practice/sessions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({
          requested_question_count: 5,
          content_node_id: state.recommendedContentNodeId,
        })
      });
      if (!res.ok) throw new Error('Falha ao criar sessão');
      const sessionData = await res.json();
      state.activePracticeSession = sessionData;

      // Fetch Next Question
      loadNextPracticeQuestion(sessionData.id);
    } catch (err) {
      area.innerHTML = `
        <div class="empty-text">
          <p>Não há questões disponíveis para este conteúdo no momento.</p>
          <button class="btn btn-secondary" style="margin-top:10px;" onclick="location.reload()">Voltar</button>
        </div>
      `;
    }
  }

  async function loadNextPracticeQuestion(sessionId) {
    const area = document.getElementById('practice-interactive-area');
    try {
      const res = await studentRequest(`/api/v1/practice/sessions/${sessionId}/next-question`);
      if (!res.ok) throw new Error('Falha ao obter questão');
      const data = await res.json();

      if (data.is_complete || !data.question) {
        completePracticeSession(sessionId);
        return;
      }

      state.currentPracticeQuestion = data.question;
      renderPracticeQuestion(sessionId, data.question);
    } catch (err) {
      area.innerHTML = '<p class="empty-text">Erro ao carregar questão de prática.</p>';
    }
  }

  const DIFFICULTY_LABEL = { EASY: 'Fácil', MEDIUM: 'Médio', HARD: 'Difícil' };

  function renderPracticeQuestion(sessionId, question) {
    const area = document.getElementById('practice-interactive-area');
    area.innerHTML = `
      <div class="practice-question-box">
        <div class="card-header">
          <span class="badge badge-primary">Questão ${question.position}</span>
          <span class="badge badge-accent">Nível ${DIFFICULTY_LABEL[question.difficulty_level] || question.difficulty_level}</span>
        </div>
        <div class="question-text">${question.canonical_text}</div>
        <div class="options-list">
          ${(question.options || []).map(opt => `
            <div class="option-item" onclick="selectOption(this, '${opt.id}')">
              <span class="option-key">${opt.option_key}</span>
              <span class="option-text">${opt.text}</span>
            </div>
          `).join('')}
        </div>
        <button class="btn btn-primary" id="btn-submit-answer" disabled onclick="submitAnswer('${sessionId}', '${question.id}')">Confirmar Resposta</button>
      </div>
    `;
  }

  window.selectOption = function(element, optionId) {
    document.querySelectorAll('.option-item').forEach(el => el.classList.remove('selected'));
    element.classList.add('selected');
    element.setAttribute('data-selected-id', optionId);
    document.getElementById('btn-submit-answer').removeAttribute('disabled');
  };

  window.submitAnswer = async function(sessionId, selectionId) {
    const selectedEl = document.querySelector('.option-item.selected');
    if (!selectedEl) return;
    const optionId = selectedEl.getAttribute('data-selected-id');

    try {
      const res = await studentRequest(`/api/v1/practice/sessions/${sessionId}/questions/${selectionId}/answer`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ selected_option_id: optionId })
      });
      if (!res.ok) throw new Error('Falha ao enviar resposta');

      // Load Next
      loadNextPracticeQuestion(sessionId);
    } catch (err) {
      alert('Erro ao registrar resposta.');
    }
  };

  async function completePracticeSession(sessionId) {
    const area = document.getElementById('practice-interactive-area');
    try {
      const res = await studentRequest(`/api/v1/practice/sessions/${sessionId}/complete`, {
        method: 'POST',
        headers: studentHeaders()
      });
      if (!res.ok) throw new Error('Falha ao concluir prática');
      const result = await res.json();

      area.innerHTML = `
        <div class="practice-starter" style="text-align:center;">
          <h3>🎉 Prática Concluída!</h3>
          <p style="margin:10px 0;">Você acertou <strong>${result.correct_count}</strong> de <strong>${result.total_questions}</strong> questões (${result.percentage}%).</p>
          <span class="badge badge-primary">Novo nível de recomendação: ${result.updated_mastery_level}</span>
          <div style="margin-top:20px;">
            <button class="btn btn-primary" onclick="location.reload()">Voltar ao Dashboard</button>
          </div>
        </div>
      `;
    } catch (err) {
      area.innerHTML = '<p class="empty-text">Sessão concluída.</p>';
    }
  }

  // ---------- PHASE 16: Atividades (visibilidade apenas — sem resolução) ----------

  const ACTIVITY_STATUS = {
    DISPONIVEL: { label: 'DISPONÍVEL', cls: 'badge-primary' },
    AGUARDANDO: { label: 'AGUARDANDO', cls: 'badge-muted' },
    ENCERRADA: { label: 'ENCERRADA', cls: 'badge-muted' },
  };

  function escActivity(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  function formatActivityDate(iso) {
    if (!iso) return '—';
    const d = new Date(iso);
    if (Number.isNaN(d.getTime())) return '—';
    return d.toLocaleDateString('pt-BR');
  }

  async function loadActivitiesView() {
    const container = document.getElementById('activities-list-container');
    const entry = document.getElementById('activity-entry-screen');
    if (entry) entry.hidden = true;
    if (!container) return;
    container.innerHTML = '<p>Carregando atividades…</p>';
    try {
      const res = await studentRequest('/api/v1/student/activities');
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const data = await res.json();
      const items = data.items || [];
      if (!items.length) {
        container.innerHTML = '<p class="empty-text">Nenhuma atividade disponível no momento.</p>';
        return;
      }
      container.innerHTML = items.map((a) => {
        const badge = ACTIVITY_STATUS[a.availability] || ACTIVITY_STATUS.ENCERRADA;
        const canOpen = a.availability === 'DISPONIVEL';
        const author = a.author_external_id ? `Prof. ${escActivity(a.author_external_id)}` : 'Professor';
        return `
          <article class="activity-card card" data-assignment-id="${escActivity(a.assignment_id)}">
            <div class="activity-card-head">
              <strong>${escActivity(a.title)}</strong>
              <span class="badge ${badge.cls}">${badge.label}</span>
            </div>
            <div class="activity-card-meta">
              <span>${author}</span>
              <span>${a.question_count} questões</span>
              <span>Disponível em: ${formatActivityDate(a.available_from)}</span>
              <span>Prazo: ${formatActivityDate(a.due_at)}</span>
            </div>
            <div class="activity-card-actions">
              <button class="btn btn-primary activity-open" type="button"
                data-assignment-id="${escActivity(a.assignment_id)}" ${canOpen ? '' : 'disabled'}>
                ${canOpen ? 'Abrir atividade' : badge.label}
              </button>
            </div>
          </article>`;
      }).join('');
    } catch (err) {
      container.innerHTML = '<p class="empty-text">Não foi possível carregar suas atividades.</p>';
    }
  }

  async function openActivityEntry(assignmentId) {
    const entry = document.getElementById('activity-entry-screen');
    const body = document.getElementById('activity-entry-body');
    const title = document.getElementById('activity-entry-title');
    if (!entry || !body) return;
    body.innerHTML = '<p>Carregando…</p>';
    entry.hidden = false;
    try {
      const res = await studentRequest(`/api/v1/student/activities/${assignmentId}`);
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const d = await res.json();
      if (title) title.textContent = d.title || 'Atividade';
      const screen = d.entry_screen || {};
      // PHASE 16: entry screen ONLY. This function performs a GET; starting the
      // attempt is a separate action (PHASE 17 startActivityPlayer).
      let attemptStatus = null;
      try {
        const at = await studentRequest(`/api/v1/student/activities/${assignmentId}/attempt`);
        if (at.ok) attemptStatus = (await at.json()).status;
      } catch (e) { /* entry screen still works without it */ }
      const resumed = attemptStatus === 'IN_PROGRESS';
      const finished = attemptStatus === 'COMPLETED';
      const label = finished ? 'Ver resultado'
        : (resumed ? 'Continuar atividade' : (screen.action_label || 'Iniciar atividade'));
      body.innerHTML = `
        <p class="activity-entry-heading">${escActivity(screen.heading || d.title || 'Atividade')}</p>
        <div class="activity-card-meta">
          <span>${d.question_count} questões</span>
          <span>Disponível em: ${formatActivityDate(d.available_from)}</span>
          <span>Prazo: ${formatActivityDate(d.due_at)}</span>
        </div>
        <button class="btn btn-primary" type="button" id="activity-start-btn"
          data-assignment-id="${escActivity(assignmentId)}" ${d.can_start || resumed || finished ? '' : 'disabled'}>
          ${escActivity(label)}
        </button>
        <p class="activity-entry-note">${escActivity(
          finished ? 'Você já concluiu esta atividade. Veja o seu resultado.'
          : 'Ao iniciar, suas respostas são salvas automaticamente e você pode retomar de onde parou.',
        )}</p>`;
      const startBtn = document.getElementById('activity-start-btn');
      if (startBtn) startBtn.addEventListener('click', () => {
        if (startBtn.disabled) return;
        if (finished) { player.assignmentId = assignmentId; correctAndShowResult(); }
        else startActivityPlayer(assignmentId);
      });
    } catch (err) {
      body.innerHTML = '<p class="empty-text">Não foi possível abrir esta atividade.</p>';
    }
  }

  const activitiesContainer = document.getElementById('activities-list-container');
  if (activitiesContainer) {
    activitiesContainer.addEventListener('click', (e) => {
      const btn = e.target.closest('.activity-open');
      if (btn && !btn.disabled) openActivityEntry(btn.dataset.assignmentId);
    });
  }
  const activityBack = document.getElementById('activity-entry-back');
  if (activityBack) {
    activityBack.addEventListener('click', () => {
      document.getElementById('activity-entry-screen').hidden = true;
    });
  }

  // ---------- PHASE 17: student activity PLAYER (execution only) ----------
  // Navigation, selection, autosave, recovery and finalisation are all
  // deterministic client logic. The BACKEND is the authority for completion.
  // No correction, score, percentage, ranking or resolution here.

  // PHASE 22: set while a "Praticar agora" run is in the reused PHASE 17 player,
  // so the result screen shows the "Prática" framing and "Voltar" returns to the
  // Trilha de Estudos instead of the Atividades list. null = ordinary activity.
  let practiceFlow = null;
  // PHASE 24: set while a Momento de Aprendizado PRACTICE block is running in the
  // reused PHASE 17 player, so completing it marks that block DONE and returns to
  // the session instead of the Trilha/Atividades. { sessionId, blockIndex }.
  let studySessionFlow = null;
  // PHASE 25: where the Material Player's own "Voltar" button returns to -
  // { returnTo: 'study-path' | 'study-session' }. Set by openMaterialReader().
  let materialReaderFlow = null;
  // PHASE 25: set while a practice launched from "Pratique o que você estudou"
  // (inside the Material Player) is running in the reused PHASE 17 player, so
  // finishing it returns to that same material instead of the Trilha. { materialId }.
  let materialPracticeFlow = null;
  let pdpData = null;   // cached [{discipline_name, contents:[...]}] for the direct picker

  const player = {
    assignmentId: null,
    state: null,          // last server state payload
    index: 0,             // 0-based current question
    saving: 0,            // in-flight autosave count
    pending: new Map(),   // question_version_id -> desired option key (retry queue)
    uiState: 'LOADING',
  };

  const PLAYER_EL = {
    root: () => document.getElementById('activity-player'),
    listCard: () => document.getElementById('activities-list-card'),
    entry: () => document.getElementById('activity-entry-screen'),
  };

  function setPlayerUiState(s) {
    player.uiState = s;
    const root = PLAYER_EL.root();
    if (root) root.dataset.state = s;
    const el = document.getElementById('player-save-status');
    if (!el) return;
    const map = {
      SAVING: ['Salvando…', 'is-saving'],
      SAVED: ['Salvo', 'is-saved'],
      SAVE_ERROR: ['Erro ao salvar — tentar novamente', 'is-error'],
      NETWORK_ERROR: ['Sem conexão — suas respostas locais foram mantidas', 'is-error'],
      COMPLETING: ['Finalizando…', 'is-saving'],
      COMPLETED: ['Atividade finalizada', 'is-saved'],
      READY: ['', ''],
      IN_PROGRESS: ['', ''],
      LOADING: ['Carregando…', ''],
    };
    const [txt, cls] = map[s] || ['', ''];
    el.textContent = txt;
    el.className = `activity-player-save ${cls}`;
  }

  function playerBanner(message, kind) {
    const b = document.getElementById('player-banner');
    if (!b) return;
    if (!message) { b.hidden = true; b.textContent = ''; return; }
    b.hidden = false;
    b.textContent = message;
    b.className = `activity-player-banner ${kind === 'error' ? 'is-error' : 'is-info'}`;
  }

  async function startActivityPlayer(assignmentId) {
    // a fresh entry into the player - never carry over state from a previous activity
    player.assignmentId = assignmentId;
    player.state = null;
    player.index = 0;
    player.saving = 0;
    player.pending.clear();
    playerBanner('', null);
    openPlayerShell();
    setPlayerUiState('LOADING');
    try {
      const res = await studentRequest(`/api/v1/student/activities/${assignmentId}/attempt`, {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({}));
        const msg = (err.detail && (err.detail.message || err.detail)) || `HTTP ${res.status}`;
        playerBanner(typeof msg === 'string' ? msg : 'Não foi possível iniciar a atividade.', 'error');
        setPlayerUiState('SAVE_ERROR');
        return;
      }
      applyPlayerState(await res.json());
    } catch (err) {
      setPlayerUiState('NETWORK_ERROR');
      playerBanner('Sem conexão para iniciar a atividade.', 'error');
    }
  }

  function openPlayerShell() {
    if (PLAYER_EL.listCard()) PLAYER_EL.listCard().hidden = true;
    if (PLAYER_EL.entry()) PLAYER_EL.entry().hidden = true;
    if (PLAYER_EL.root()) PLAYER_EL.root().hidden = false;
  }

  function closePlayer() {
    if (PLAYER_EL.root()) PLAYER_EL.root().hidden = true;
    if (PLAYER_EL.listCard()) PLAYER_EL.listCard().hidden = false;
    player.assignmentId = null;
    player.state = null;
    if (materialPracticeFlow) {
      const flow = materialPracticeFlow; materialPracticeFlow = null;
      openMaterialReader(flow.materialId, materialReaderFlow || {});
      return;
    }
    if (studySessionFlow) { studySessionFlow = null; switchView('study-session'); return; }
    if (practiceFlow) { practiceFlow = null; switchView('study-path'); return; }
    loadActivitiesView();
  }

  function applyPlayerState(state) {
    player.state = state;
    const done = state.status === 'COMPLETED';
    // land on the first pending question, or keep current
    const pend = state.pending_positions || [];
    if (player.index === 0 || player.index >= state.questions.length) {
      const target = state.current_position || (pend.length ? pend[0] : 1);
      player.index = Math.max(0, Math.min(state.questions.length - 1, target - 1));
    }
    setPlayerUiState(done ? 'COMPLETED' : 'IN_PROGRESS');
    renderPlayer();
    // PHASE 18: a COMPLETED attempt goes straight to correction + the result screen
    if (done) correctAndShowResult();
  }

  function renderPlayer() {
    const st = player.state;
    if (!st) return;
    const q = st.questions[player.index];
    document.getElementById('player-title').textContent = st.activity.title || 'Atividade';
    document.getElementById('player-progress').textContent =
      `Questão ${player.index + 1}/${st.questions.length}`;
    document.getElementById('player-counter').textContent =
      `${st.answered_count}/${st.total_questions} respondidas`;

    document.getElementById('player-statement').textContent = q ? q.statement : '';
    const optForm = document.getElementById('player-options');
    const locked = st.status === 'COMPLETED';
    optForm.innerHTML = (q ? q.options : []).map((o) => {
      const checked = q.selected_option === o.key ? 'checked' : '';
      return `<label class="activity-player-option${q.selected_option === o.key ? ' is-selected' : ''}">
        <input type="radio" name="player-opt" value="${escActivity(o.key)}" ${checked}
          ${locked ? 'disabled' : ''} />
        <span class="activity-player-option-key">${escActivity(o.key)}</span>
        <span class="activity-player-option-text">${escActivity(o.text)}</span>
      </label>`;
    }).join('');

    renderPlayerNav();

    document.getElementById('player-prev').disabled = player.index === 0;
    document.getElementById('player-next').disabled = player.index >= st.questions.length - 1;
    const finish = document.getElementById('player-finish');
    finish.hidden = locked;
    finish.disabled = st.answered_count < st.total_questions;
    finish.title = finish.disabled ? 'Responda todas as questões para finalizar' : '';
  }

  function renderPlayerNav() {
    const st = player.state;
    const nav = document.getElementById('player-nav');
    nav.innerHTML = st.questions.map((q, i) => {
      const cls = i === player.index ? 'is-current'
        : (q.answered ? 'is-answered' : 'is-untouched');
      return `<button type="button" class="activity-player-dot ${cls}"
        data-index="${i}" aria-label="Questão ${i + 1}${q.answered ? ', respondida' : ''}"
        aria-current="${i === player.index ? 'true' : 'false'}">
        <span>${i + 1}</span>${q.answered ? '<span class="activity-player-check" aria-hidden="true">✓</span>' : ''}
      </button>`;
    }).join('');
  }

  function gotoQuestion(i) {
    const st = player.state;
    if (!st || i < 0 || i >= st.questions.length) return;
    player.index = i;
    renderPlayer();
    persistPosition(i + 1);
  }

  let positionTimer = null;
  function persistPosition(pos1) {
    if (positionTimer) clearTimeout(positionTimer);
    positionTimer = setTimeout(() => {
      studentRequest(
        `/api/v1/student/activities/${player.assignmentId}/attempt/position?position=${pos1}`,
        { method: 'PUT' },
      ).catch(() => { /* best-effort resume aid only */ });
    }, 400);
  }

  async function selectOption(key) {
    const st = player.state;
    if (!st || st.status === 'COMPLETED') return;
    const q = st.questions[player.index];
    if (!q) return;
    q.selected_option = key;          // optimistic UI
    q.answered = true;
    renderPlayer();
    await autosaveAnswer(q.question_version_id, key);
  }

  async function autosaveAnswer(qvid, key) {
    player.pending.set(qvid, key);
    player.saving += 1;
    setPlayerUiState('SAVING');
    try {
      const res = await studentRequest(
        `/api/v1/student/activities/${player.assignmentId}/attempt/answers/${qvid}`,
        { method: 'PUT', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ selected_option: key }) },
      );
      player.saving -= 1;
      if (!res.ok) {
        setPlayerUiState('SAVE_ERROR');
        playerBanner('Não foi possível salvar esta resposta. Toque em "tentar novamente".', 'error');
        return;
      }
      const data = await res.json();
      // only clear the retry entry if the server confirms the same choice
      if (player.pending.get(qvid) === key) player.pending.delete(qvid);
      if (player.state) {
        player.state.answered_count = data.answered_count;
        player.state.pending_count = data.pending_count;
      }
      if (player.saving === 0 && player.pending.size === 0) setPlayerUiState('SAVED');
      refreshCountersOnly();
    } catch (err) {
      player.saving -= 1;
      setPlayerUiState('NETWORK_ERROR');
      playerBanner('Sem conexão. Sua resposta foi mantida na tela e será reenviada.', 'error');
    }
  }

  function refreshCountersOnly() {
    const st = player.state;
    if (!st) return;
    document.getElementById('player-counter').textContent =
      `${st.answered_count}/${st.total_questions} respondidas`;
    const finish = document.getElementById('player-finish');
    finish.disabled = st.answered_count < st.total_questions;
    renderPlayerNav();
  }

  async function retryPending() {
    if (!player.pending.size) { setPlayerUiState('IN_PROGRESS'); return; }
    playerBanner('', null);
    for (const [qvid, key] of [...player.pending.entries()]) {
      await autosaveAnswer(qvid, key);
    }
  }

  async function attemptComplete() {
    if (player.pending.size) { await retryPending(); }
    setPlayerUiState('COMPLETING');
    try {
      const res = await studentRequest(
        `/api/v1/student/activities/${player.assignmentId}/attempt/complete`,
        { method: 'POST' },
      );
      const data = await res.json().catch(() => ({}));
      if (res.status === 409) {
        const d = data.detail || {};
        openPlayerDialog(
          'Atividade incompleta',
          `<p>${escActivity(d.message || 'Você ainda tem questões sem resposta.')}</p>
           <p>Questões pendentes: ${(d.pending_positions || []).join(', ') || '—'}</p>`,
          [
            { label: 'Ver questões pendentes', primary: true, action: () => {
              closePlayerDialog();
              const first = (d.pending_positions || [])[0];
              if (first) gotoQuestion(first - 1);
            } },
            { label: 'Cancelar', action: closePlayerDialog },
          ],
        );
        setPlayerUiState('IN_PROGRESS');
        return;
      }
      if (!res.ok) {
        setPlayerUiState('SAVE_ERROR');
        playerBanner('Não foi possível finalizar agora. Tente novamente.', 'error');
        return;
      }
      applyPlayerState(data);
    } catch (err) {
      setPlayerUiState('NETWORK_ERROR');
      playerBanner('Sem conexão para finalizar. Tente novamente.', 'error');
    }
  }

  function confirmComplete() {
    const st = player.state;
    if (!st) return;
    openPlayerDialog(
      'Finalizar atividade',
      '<p>Você respondeu todas as questões. Deseja finalizar a atividade?</p>',
      [
        { label: 'Finalizar', primary: true, action: () => { closePlayerDialog(); attemptComplete(); } },
        { label: 'Voltar', action: closePlayerDialog },
      ],
    );
  }

  function showCompletedScreen() {
    // fallback only (used if correction is unavailable)
    openPlayerDialog(
      'Atividade finalizada',
      '<p>Suas respostas foram registradas. Esta atividade não pode mais ser alterada.</p>',
      [{ label: 'Voltar às atividades', primary: true, action: () => { closePlayerDialog(); closePlayer(); } }],
    );
  }

  // ---------- PHASE 18: correção determinística + tela de Resultado ----------
  // Sem nota / TRI / ranking. Aproveitamento = acertos / questões * 100 (bruto).
  // O gabarito só aparece AQUI, depois da conclusão + correção.

  const RESULT_STATUS = {
    CORRECT: { label: 'Correta', cls: 'is-correct' },
    INCORRECT: { label: 'Incorreta', cls: 'is-incorrect' },
    UNANSWERED: { label: 'Não respondida', cls: 'is-unanswered' },
  };
  const resultView = { data: null };

  async function correctAndShowResult() {
    const aid = player.assignmentId;
    if (!aid) return;
    closePlayerDialog();
    try {
      let res = await studentRequest(`/api/v1/student/activities/${aid}/attempt/correct`, { method: 'POST' });
      if (res.status === 404 || res.status === 409) {
        // fall back to a fresh GET (already corrected, or a transient state)
        res = await studentRequest(`/api/v1/student/activities/${aid}/attempt/result`);
      }
      if (!res.ok) { showCompletedScreen(); return; }
      renderResultScreen(await res.json());
      loadResultAnalysis(aid);   // PHASE 19 - deterministic pedagogical analysis
    } catch (err) {
      showCompletedScreen();
    }
  }

  function renderResultScreen(data) {
    resultView.data = data;
    if (PLAYER_EL.root()) PLAYER_EL.root().hidden = true;
    if (PLAYER_EL.listCard()) PLAYER_EL.listCard().hidden = true;
    if (PLAYER_EL.entry()) PLAYER_EL.entry().hidden = true;
    const card = document.getElementById('activity-result');
    if (!card) return;
    card.hidden = false;
    const r = data.result;
    const isPractice = !!practiceFlow || !!studySessionFlow || !!materialPracticeFlow;
    document.getElementById('activity-result-title').textContent =
      isPractice ? 'Prática concluída' : 'Atividade finalizada';
    const pBadge = document.getElementById('activity-result-practice-badge');
    if (pBadge) pBadge.hidden = !isPractice;
    const pNote = document.getElementById('activity-result-practice-note');
    if (pNote) pNote.hidden = !isPractice;
    const backBtn = document.getElementById('result-back');
    if (backBtn) {
      backBtn.textContent = materialPracticeFlow ? 'Voltar para o material'
        : studySessionFlow ? 'Voltar para o Momento de Aprendizado'
        : (isPractice ? 'Voltar para a Trilha' : 'Voltar para Atividades');
    }
    document.getElementById('activity-result-summary').innerHTML = `
      <ul class="activity-result-figures">
        <li><span class="figure-value">${r.question_count}</span><span class="figure-label">Questões</span></li>
        <li><span class="figure-value">${r.correct_count}</span><span class="figure-label">Acertos</span></li>
        <li><span class="figure-value">${r.incorrect_count}</span><span class="figure-label">Erros</span></li>
        <li><span class="figure-value">${r.unanswered_count}</span><span class="figure-label">Não respondidas</span></li>
        <li class="figure-highlight"><span class="figure-value">${r.aproveitamento_percent}%</span><span class="figure-label">Aproveitamento</span></li>
      </ul>`;
    const box = document.getElementById('activity-result-questions');
    box.hidden = true;
    box.innerHTML = (data.items || []).map((it) => {
      const s = RESULT_STATUS[it.status] || RESULT_STATUS.UNANSWERED;
      const num = it.official_number ? `Q${it.official_number}` : `Questão ${it.position}`;
      return `<article class="activity-result-question ${s.cls}">
        <div class="activity-result-question-head">
          <strong>${it.position}. ${escActivity(num)}</strong>
          <span class="activity-result-badge ${s.cls}">${s.label}</span>
        </div>
        <div class="activity-result-question-body">
          <span>Sua resposta: <strong>${escActivity(it.selected_option_key || '—')}</strong></span>
          <span>Resposta correta: <strong>${escActivity(it.correct_option_key || '—')}</strong></span>
        </div>
        <p class="activity-result-question-res">Resolução da questão: em breve.</p>
      </article>`;
    }).join('');
    document.getElementById('result-toggle-questions').textContent = 'Ver questões';
    // reset the PHASE 19 analysis section for this render
    const an = document.getElementById('activity-analysis');
    if (an) an.hidden = true;
    ['analysis-overview', 'analysis-by-discipline', 'analysis-by-content',
     'analysis-strengths', 'analysis-improvements'].forEach((id) => {
      const el = document.getElementById(id);
      if (el) el.innerHTML = '';
    });
    const tgl = document.getElementById('result-toggle-analysis');
    if (tgl) { tgl.textContent = 'Ver desempenho pedagógico'; tgl.disabled = true; }
  }

  // ---------- PHASE 19: análise pedagógica determinística ----------
  // Somente MEDIÇÃO e AGREGAÇÃO. Sem nota, TRI, ranking, comparação entre alunos,
  // Domain Map ou recomendação — isso vem em fases posteriores.

  const BAND_LABEL = {
    PONTO_FORTE: 'Ponto forte',
    PONTO_MELHORIA: 'Ponto de melhoria',
    DESEMPENHO_INTERMEDIARIO: 'Desempenho intermediário',
    INSUFFICIENT_SAMPLE: 'Amostra insuficiente',
    SEM_DADOS: 'Sem dados',
  };
  const BAND_CLASS = {
    PONTO_FORTE: 'is-strong', PONTO_MELHORIA: 'is-improve',
    DESEMPENHO_INTERMEDIARIO: 'is-mid', INSUFFICIENT_SAMPLE: 'is-insufficient',
    SEM_DADOS: 'is-insufficient',
  };
  const analysisView = { data: null };

  function pct(accuracy) {
    return accuracy === null || accuracy === undefined ? '—' : `${Math.round(accuracy * 1000) / 10}%`;
  }

  async function loadResultAnalysis(assignmentId) {
    const tgl = document.getElementById('result-toggle-analysis');
    if (tgl) tgl.disabled = true;
    try {
      const res = await studentRequest(`/api/v1/student/activities/${assignmentId}/attempt/result/analysis`);
      if (!res.ok) return;   // section simply stays unavailable
      analysisView.data = await res.json();
      renderResultAnalysis(analysisView.data);
      if (tgl) tgl.disabled = false;
    } catch (err) { /* analysis is optional; the result screen still works */ }
  }

  function renderResultAnalysis(a) {
    const s = a.summary || {};
    document.getElementById('activity-analysis-note').textContent =
      'Medição por conteúdo e disciplina — aproveitamento (acertos ÷ respondidas), não é nota.';

    document.getElementById('analysis-overview').innerHTML = `
      <h5>Visão geral</h5>
      <ul class="activity-analysis-figures">
        <li><span class="figure-value">${s.total_questions ?? 0}</span><span class="figure-label">Questões</span></li>
        <li><span class="figure-value">${s.classified_questions ?? 0}</span><span class="figure-label">Classificadas</span></li>
        <li><span class="figure-value">${s.unclassified_questions ?? 0}</span><span class="figure-label">Sem classificação</span></li>
        <li><span class="figure-value">${s.provisional_questions ?? 0}</span><span class="figure-label">Provisórias</span></li>
        <li><span class="figure-value">${pct(s.accuracy)}</span><span class="figure-label">Aproveitamento</span></li>
      </ul>
      ${s.unclassified_questions ? `<p class="activity-analysis-hint">${s.unclassified_questions} questão(ões) sem classificação pedagógica entram no total, mas não são atribuídas a um conteúdo.</p>` : ''}`;

    document.getElementById('analysis-by-discipline').innerHTML =
      renderPerfTable('Por disciplina', a.by_discipline || [], (row) => escActivity(row.discipline_name || row.discipline_code));

    document.getElementById('analysis-by-content').innerHTML =
      renderPerfTable('Por conteúdo', a.by_content || [], (row) => escActivity(row.content_name || row.content_code),
        { showProvisional: true });

    document.getElementById('analysis-strengths').innerHTML =
      renderVerdictList('Pontos fortes', a.strengths || [], 'Nenhum ponto forte identificado ainda (é preciso um mínimo de questões respondidas).');
    document.getElementById('analysis-improvements').innerHTML =
      renderVerdictList('Pontos de melhoria', a.improvements || [], 'Nenhum ponto de melhoria identificado.');
  }

  function renderPerfTable(title, rows, labelFn, opts) {
    opts = opts || {};
    if (!rows.length) return `<h5>${title}</h5><p class="empty-text">Sem dados classificados.</p>`;
    const body = rows.map((r) => `
      <tr>
        <td>${labelFn(r)}</td>
        <td class="num">${r.correct}/${r.answered}</td>
        <td class="num">${pct(r.accuracy)}</td>
        <td><span class="activity-analysis-band ${BAND_CLASS[r.band] || ''}">${BAND_LABEL[r.band] || r.band}</span></td>
        ${opts.showProvisional ? `<td class="num">${r.provisional_count ?? r.provisional_questions ?? 0}${r.forced_closure_count ? ` (FC ${r.forced_closure_count})` : ''}</td>` : ''}
      </tr>`).join('');
    return `<h5>${title}</h5>
      <div class="activity-analysis-tablewrap">
      <table class="activity-analysis-table">
        <thead><tr><th>${title.replace('Por ', '').replace(/^./, (c) => c.toUpperCase())}</th><th class="num">Acertos</th><th class="num">Aproveit.</th><th>Situação</th>${opts.showProvisional ? '<th class="num">Provisórias</th>' : ''}</tr></thead>
        <tbody>${body}</tbody>
      </table></div>`;
  }

  function renderVerdictList(title, entries, emptyText) {
    if (!entries.length) return `<h5>${title}</h5><p class="empty-text">${emptyText}</p>`;
    const items = entries.map((e) => {
      const name = e.content_name || e.content_code || e.discipline_name || e.discipline_code;
      const scope = e.scope === 'content' ? 'Conteúdo' : 'Disciplina';
      return `<li><strong>${escActivity(name)}</strong> <span class="activity-analysis-scope">${scope}</span>
        <span class="activity-analysis-figure">${e.answered} respondidas · ${pct(e.accuracy)}</span></li>`;
    }).join('');
    return `<h5>${title}</h5><ul class="activity-analysis-verdicts">${items}</ul>`;
  }

  function closeResultScreen() {
    const card = document.getElementById('activity-result');
    if (card) card.hidden = true;
    resultView.data = null;
    analysisView.data = null;
    player.assignmentId = null;
    player.state = null;
    if (PLAYER_EL.listCard()) PLAYER_EL.listCard().hidden = false;
    if (materialPracticeFlow) {
      // an exercise associated to a material was just corrected - close the
      // loop (fresh evidence -> recalculated Domain Map) and return to the
      // SAME material, not the Trilha (spec s12: preserve prior context).
      const flow = materialPracticeFlow;
      materialPracticeFlow = null;
      studentRequest('/api/v1/student/domain/rebuild', { method: 'POST' })
        .catch(() => {})
        .finally(() => openMaterialReader(flow.materialId, materialReaderFlow || {}));
      return;
    }
    if (studySessionFlow) {
      // the PRACTICE block's real activity was just corrected - mark the block
      // DONE (never a second practice) and return to the running session.
      const flow = studySessionFlow;
      studySessionFlow = null;
      completeStudySessionBlock(flow.sessionId, flow.blockIndex).finally(() => {
        switchView('study-session');
      });
      return;
    }
    if (practiceFlow) {
      // close the loop: the practice added PRACTICE-origin evidence, so refresh
      // the derived Domain Map before returning to the recalculated Trilha.
      practiceFlow = null;
      studentRequest('/api/v1/student/domain/rebuild', { method: 'POST' })
        .catch(() => {})
        .finally(() => switchView('study-path'));
      return;
    }
    loadActivitiesView();
  }

  (function wireResult() {
    const card = document.getElementById('activity-result');
    if (!card) return;
    document.getElementById('result-back').addEventListener('click', closeResultScreen);
    document.getElementById('result-toggle-questions').addEventListener('click', () => {
      const box = document.getElementById('activity-result-questions');
      box.hidden = !box.hidden;
      document.getElementById('result-toggle-questions').textContent =
        box.hidden ? 'Ver questões' : 'Ocultar questões';
    });
    const anTgl = document.getElementById('result-toggle-analysis');
    if (anTgl) anTgl.addEventListener('click', () => {
      const sec = document.getElementById('activity-analysis');
      sec.hidden = !sec.hidden;
      anTgl.textContent = sec.hidden ? 'Ver desempenho pedagógico' : 'Ocultar desempenho pedagógico';
    });
  })();

  function openPlayerDialog(titleText, bodyHtml, actions) {
    const back = document.getElementById('player-dialog');
    document.getElementById('player-dialog-title').textContent = titleText;
    document.getElementById('player-dialog-body').innerHTML = bodyHtml;
    const box = document.getElementById('player-dialog-actions');
    box.innerHTML = '';
    (actions || []).forEach((a) => {
      const btn = document.createElement('button');
      btn.type = 'button';
      btn.className = `btn ${a.primary ? 'btn-primary' : 'btn-secondary'}`;
      btn.textContent = a.label;
      btn.addEventListener('click', a.action);
      box.appendChild(btn);
    });
    back.hidden = false;
  }
  function closePlayerDialog() {
    const back = document.getElementById('player-dialog');
    if (back) back.hidden = true;
  }

  // ----- player event wiring -----
  (function wirePlayer() {
    const root = PLAYER_EL.root();
    if (!root) return;
    document.getElementById('player-exit').addEventListener('click', closePlayer);
    document.getElementById('player-prev').addEventListener('click', () => gotoQuestion(player.index - 1));
    document.getElementById('player-next').addEventListener('click', () => gotoQuestion(player.index + 1));
    document.getElementById('player-finish').addEventListener('click', confirmComplete);
    document.getElementById('player-nav').addEventListener('click', (e) => {
      const dot = e.target.closest('.activity-player-dot');
      if (dot) gotoQuestion(Number(dot.dataset.index));
    });
    document.getElementById('player-options').addEventListener('change', (e) => {
      if (e.target && e.target.name === 'player-opt') selectOption(e.target.value);
    });
    document.getElementById('player-save-status').addEventListener('click', () => {
      if (player.uiState === 'SAVE_ERROR' || player.uiState === 'NETWORK_ERROR') retryPending();
    });
    // keyboard: arrows navigate, A-E / 1-5 pick an option — accessibility preserved
    root.addEventListener('keydown', (e) => {
      if (player.state && player.state.status === 'COMPLETED') return;
      if (e.key === 'ArrowLeft') { gotoQuestion(player.index - 1); }
      else if (e.key === 'ArrowRight') { gotoQuestion(player.index + 1); }
      else {
        const q = player.state && player.state.questions[player.index];
        if (!q) return;
        let key = null;
        const up = e.key.toUpperCase();
        if (/^[A-E]$/.test(up)) key = up;
        else if (/^[1-5]$/.test(e.key)) key = (q.options[Number(e.key) - 1] || {}).key;
        if (key && q.options.some((o) => o.key === key)) { e.preventDefault(); selectOption(key); }
      }
    });
    // pragmatic anti-casual-copy — does NOT block OS screenshots, and never traps keyboard nav
    const stage = document.getElementById('player-stage');
    stage.addEventListener('contextmenu', (e) => e.preventDefault());
    stage.addEventListener('copy', (e) => e.preventDefault());
    stage.addEventListener('cut', (e) => e.preventDefault());
    document.addEventListener('keydown', (e) => {
      if (root.hidden) return;
      const k = e.key.toLowerCase();
      if ((e.ctrlKey || e.metaKey) && (k === 'c' || k === 'x') && !e.target.closest('input,textarea')) {
        e.preventDefault();
      }
    });
    // best-effort: note tab/visibility changes only if a logging endpoint exists (none yet) — no-op
  })();

  // ---------- PHASE 20: "Meu Domínio" (mapa de domínio curricular, derivado) ----------
  // Somente MEDIÇÃO/EVIDÊNCIA. Sem nota, ranking, comparação entre alunos, trilha
  // ou "estude X agora" — isso é PHASE 21.

  const EVIDENCE_LABEL = {
    OBSERVED: 'Evidência suficiente',
    INSUFFICIENT_EVIDENCE: 'Evidência insuficiente',
  };
  const domainView = { data: null };

  function domainPct(a) {
    return a === null || a === undefined ? '—' : `${Math.round(a * 1000) / 10}%`;
  }

  async function loadDomainView() {
    const body = document.getElementById('domain-body');
    const detail = document.getElementById('domain-detail');
    if (detail) detail.hidden = true;
    if (!body) return;
    body.innerHTML = '<p>Carregando…</p>';
    try {
      const res = await studentRequest('/api/v1/student/domain');
      if (!res.ok) { body.innerHTML = '<p class="empty-text">Não foi possível carregar seu domínio.</p>'; return; }
      domainView.data = await res.json();
      renderDomainView(domainView.data);
    } catch (err) {
      body.innerHTML = '<p class="empty-text">Não foi possível carregar seu domínio.</p>';
    }
  }

  function renderDomainView(d) {
    const s = d.summary || {};
    document.getElementById('domain-summary').innerHTML = `
      <ul class="domain-figures">
        <li><span class="figure-value">${s.content_count ?? 0}</span><span class="figure-label">Conteúdos com evidência</span></li>
        <li><span class="figure-value">${s.observed_count ?? 0}</span><span class="figure-label">Evidência suficiente</span></li>
        <li><span class="figure-value">${s.insufficient_evidence_count ?? 0}</span><span class="figure-label">Evidência insuficiente</span></li>
        <li><span class="figure-value">${s.questions_answered ?? 0}</span><span class="figure-label">Questões respondidas</span></li>
        <li><span class="figure-value">${domainPct(s.accuracy)}</span><span class="figure-label">Aproveitamento</span></li>
      </ul>`;
    const body = document.getElementById('domain-body');
    if (!(d.disciplines || []).length) {
      body.innerHTML = '<p class="empty-text">Ainda não há atividades corrigidas com classificação curricular. Conclua uma atividade para começar seu mapa de domínio.</p>';
      return;
    }
    body.innerHTML = d.disciplines.map((disc) => `
      <section class="domain-discipline">
        <h4>${escActivity(disc.discipline_name || disc.discipline_code || 'Sem disciplina')}
          <span class="domain-discipline-figure">${disc.questions_correct}/${disc.questions_answered} · ${domainPct(disc.accuracy)}</span></h4>
        <ul class="domain-content-list">
          ${disc.contents.map((c) => `
            <li>
              <button class="domain-content-row" type="button" data-content="${escActivity(c.content_code)}">
                <span class="domain-content-name">${escActivity(c.content_name || c.content_code)}</span>
                <span class="domain-content-metrics">
                  <span>${c.questions_answered} respondidas</span>
                  <span>${domainPct(c.accuracy)}</span>
                  <span class="domain-evidence ${c.evidence_state === 'OBSERVED' ? 'is-ok' : 'is-low'}">${EVIDENCE_LABEL[c.evidence_state] || c.evidence_state}</span>
                </span>
              </button>
            </li>`).join('')}
        </ul>
      </section>`).join('');
    body.innerHTML += `<p class="domain-note">${escActivity(d.unclassified_note || '')}</p>`;
  }

  async function openDomainContent(contentCode) {
    const detail = document.getElementById('domain-detail');
    const box = document.getElementById('domain-detail-body');
    if (!detail || !box) return;
    box.innerHTML = '<p>Carregando…</p>';
    detail.hidden = false;
    try {
      const res = await studentRequest(`/api/v1/student/domain/content/${encodeURIComponent(contentCode)}`);
      if (!res.ok) { box.innerHTML = '<p class="empty-text">Conteúdo sem detalhe disponível.</p>'; return; }
      const d = await res.json();
      const c = d.content;
      document.getElementById('domain-detail-title').textContent = c.content_name || c.content_code;
      const prereq = (c.prerequisites || []).map((p) => escActivity(p.name || p.code)).join(', ') || '—';
      box.innerHTML = `
        <div class="domain-figures">
          <div><span class="figure-value">${c.questions_answered}</span><span class="figure-label">Respondidas</span></div>
          <div><span class="figure-value">${c.questions_correct}</span><span class="figure-label">Acertos</span></div>
          <div><span class="figure-value">${domainPct(c.accuracy)}</span><span class="figure-label">Aproveitamento</span></div>
          <div><span class="figure-value">${c.definitive_evidence_count}</span><span class="figure-label">Classificação definitiva</span></div>
          <div><span class="figure-value">${c.provisional_evidence_count}</span><span class="figure-label">Classificação provisória</span></div>
        </div>
        <p class="domain-detail-line"><strong>Evidência:</strong> ${EVIDENCE_LABEL[c.evidence_state] || c.evidence_state}
          (${c.evidence_count} de ${d.content ? c.questions_seen : 0} questões).</p>
        <p class="domain-detail-line"><strong>Disciplina:</strong> ${escActivity(d.discipline.discipline_name || d.discipline.discipline_code || '—')}
          &nbsp;·&nbsp; <strong>Pré-requisitos:</strong> ${prereq}</p>
        ${c.forced_closure_evidence_count ? `<p class="domain-detail-line domain-warn">Inclui ${c.forced_closure_evidence_count} questão(ões) de classificação por encerramento forçado.</p>` : ''}
        ${c.visual_dependency_evidence_count ? `<p class="domain-detail-line">Inclui ${c.visual_dependency_evidence_count} questão(ões) com dependência visual.</p>` : ''}
        ${(c.subcontents || []).length ? `<h5>Subconteúdos</h5><ul class="domain-subcontent-list">${c.subcontents.map((sc) => `<li>${escActivity(sc.subcontent_name || sc.subcontent_code)} — ${sc.questions_answered} respondidas · ${domainPct(sc.accuracy)}</li>`).join('')}</ul>` : ''}
        <p class="domain-detail-line domain-note">Última atividade: ${c.last_activity_at ? new Date(c.last_activity_at).toLocaleDateString('pt-BR') : '—'}.</p>`;
    } catch (err) {
      box.innerHTML = '<p class="empty-text">Não foi possível abrir este conteúdo.</p>';
    }
  }

  (function wireDomain() {
    const view = document.getElementById('view-domain');
    if (!view) return;
    const refresh = document.getElementById('domain-refresh');
    if (refresh) refresh.addEventListener('click', async () => {
      refresh.disabled = true;
      try { await studentRequest('/api/v1/student/domain/rebuild', { method: 'POST' }); } catch (e) { /* */ }
      refresh.disabled = false;
      loadDomainView();
    });
    document.getElementById('domain-body').addEventListener('click', (e) => {
      const row = e.target.closest('.domain-content-row');
      if (row) openDomainContent(row.dataset.content);
    });
    const back = document.getElementById('domain-detail-back');
    if (back) back.addEventListener('click', () => { document.getElementById('domain-detail').hidden = true; });
  })();

  // ---------- PHASE 21: "Trilha de Estudos" (decisão pedagógica derivada) ----------
  // Sem gamificação, sem ranking, sem comparação, sem nota. As ações ainda não
  // estão disponíveis (action_available=false) e são mostradas como "em breve".

  const PATH_STATE_LABEL = {
    BLOCKED_BY_PREREQUISITE: 'Bloqueado por pré-requisito',
    INSUFFICIENT_EVIDENCE: 'Evidência insuficiente',
    NEEDS_REVIEW: 'Revisar',
    RECOMMENDED: 'Estudar',
    READY: 'Praticar',
    MASTERED: 'Dominado',
  };
  const PATH_STATE_CLASS = {
    BLOCKED_BY_PREREQUISITE: 'is-blocked', INSUFFICIENT_EVIDENCE: 'is-low',
    NEEDS_REVIEW: 'is-review', RECOMMENDED: 'is-study', READY: 'is-ready', MASTERED: 'is-mastered',
  };
  const ACTION_LABEL = { STUDY: 'Estudar', PRACTICE: 'Praticar', REVIEW: 'Revisar', DIAGNOSE: 'Fazer diagnóstico', NONE: '' };

  function pathPct(a) {
    return a === null || a === undefined ? null : `${Math.round(a * 1000) / 10}%`;
  }

  async function loadStudyPathView() {
    const body = document.getElementById('path-body');
    if (!body) return;
    body.innerHTML = '<p>Carregando…</p>';
    document.getElementById('path-summary').innerHTML = '';
    document.getElementById('path-mastered').innerHTML = '';
    document.getElementById('path-provisional-note').hidden = true;
    try {
      const res = await studentRequest('/api/v1/student/study-path');
      const data = await res.json().catch(() => ({}));
      if (res.status === 409 && data.detail && data.detail.state === 'PREREQUISITE_GRAPH_INVALID') {
        body.innerHTML = '<p class="empty-text">A árvore de pré-requisitos do currículo está inconsistente no momento. Sua trilha ficará disponível assim que isso for corrigido.</p>';
        return;
      }
      if (!res.ok) { body.innerHTML = '<p class="empty-text">Não foi possível carregar sua trilha.</p>'; return; }
      renderStudyPath(data);
      populateDirectPractice();
    } catch (err) {
      body.innerHTML = '<p class="empty-text">Não foi possível carregar sua trilha.</p>';
    }
  }

  function renderStudyPath(d) {
    if (d.transparency) document.getElementById('path-transparency').textContent = d.transparency;
    const note = document.getElementById('path-provisional-note');
    if (d.provisional_note) { note.textContent = d.provisional_note; note.hidden = false; }
    else { note.hidden = true; }

    if (d.state === 'PREREQUISITE_GRAPH_INVALID') {
      document.getElementById('path-body').innerHTML = '<p class="empty-text">A árvore de pré-requisitos está inconsistente. Sua trilha ficará disponível em breve.</p>';
      return;
    }
    const s = d.summary || {};
    document.getElementById('path-summary').innerHTML = d.state === 'NO_EVIDENCE' ? '' : `
      <ul class="path-figures">
        <li><span class="figure-value">${s.step_count ?? 0}</span><span class="figure-label">Próximos passos</span></li>
        <li><span class="figure-value">${s.blocked_count ?? 0}</span><span class="figure-label">Bloqueados</span></li>
        <li><span class="figure-value">${s.insufficient_evidence_count ?? 0}</span><span class="figure-label">Precisam de diagnóstico</span></li>
        <li><span class="figure-value">${s.mastered_count ?? 0}</span><span class="figure-label">Dominados</span></li>
      </ul>`;

    const body = document.getElementById('path-body');
    if (d.state === 'NO_EVIDENCE' || !(d.steps || []).length) {
      body.innerHTML = '<p class="empty-text">Ainda não temos evidências suficientes para montar sua trilha. Conclua uma atividade com classificação curricular para começar.</p>';
    } else {
      body.innerHTML = '<h4>Seu próximo passo</h4>' + d.steps.map((step) => renderPathStep(step)).join('');
    }
    const mastered = document.getElementById('path-mastered');
    mastered.innerHTML = (d.mastered || []).length
      ? `<h4>Conteúdos que você já demonstrou dominar</h4><ul class="path-mastered-list">${d.mastered.map((m) => `<li>${escActivity(m.content_name)} <span class="path-mastered-figure">${pathPct(m.accuracy) || ''} · ${m.questions_answered} questões</span></li>`).join('')}</ul>`
      : '';
  }

  function renderPathStep(step) {
    const cls = PATH_STATE_CLASS[step.content_state] || '';
    const label = PATH_STATE_LABEL[step.content_state] || step.content_state;
    const acc = pathPct(step.accuracy);
    const ev = step.evidence_state === 'OBSERVED'
      ? (acc ? `Aproveitamento: ${acc} (${step.questions_answered} questões)` : `${step.questions_answered} questões respondidas`)
      : `Evidência: insuficiente (${step.questions_answered} ${step.questions_answered === 1 ? 'questão' : 'questões'})`;
    const prereqs = (step.unsatisfied_prerequisites || []).map((p) => escActivity(p.name || p.code)).join(', ');
    const blocks = (step.blocks_contents || []).map((p) => escActivity(p.name || p.code)).join(', ');
    // PHASE 22: real "Praticar agora" when the path says practice is available for
    // this content; otherwise keep the disabled "— em breve" for the other actions.
    const canPractice = step.practice_available === true
      || (step.action_available === true && step.action_type === 'PRACTICE');
    let actionBtn;
    if (canPractice && step.content_code) {
      const cc = escActivity(step.content_code);
      const cn = escActivity(step.content_name || step.content_code);
      actionBtn = `
        <button class="btn btn-primary path-practice-btn" type="button"
          data-content="${cc}" data-name="${cn}">Praticar agora</button>
        <div class="path-practice-panel" hidden>
          <span class="path-practice-q">Quantas questões?</span>
          <div class="path-practice-counts" role="radiogroup" aria-label="Quantidade de questões">
            ${[5, 10, 15, 20].map((n, i) => `<label class="path-practice-count">
              <input type="radio" name="ppc-${cc}" value="${n}"${i === 1 ? ' checked' : ''}> ${n}</label>`).join('')}
          </div>
          <div class="path-practice-actions">
            <button class="btn btn-primary path-practice-start" type="button">Começar prática</button>
            <button class="btn btn-secondary path-practice-cancel" type="button">Cancelar</button>
          </div>
          <p class="path-practice-msg" hidden></p>
        </div>`;
    } else {
      actionBtn = step.action_type && step.action_type !== 'NONE'
        ? `<button class="btn btn-secondary" type="button" disabled title="${escActivity(step.action_note || 'Em breve')}">${ACTION_LABEL[step.action_type] || step.action_type} — em breve</button>`
        : '';
    }
    // PHASE 25: honest "Estudar agora" when a published material is actually
    // linked to this content; otherwise no invented button, no fake availability.
    const studyBtn = step.material_available && step.material_id
      ? `<button class="btn btn-secondary path-study-btn" type="button"
          data-material-id="${escActivity(step.material_id)}">📘 Estudar agora</button>`
      : (step.material_note
          ? `<span class="path-step-note">${escActivity(step.material_note)}</span>` : '');
    return `<article class="path-step ${cls}">
      <div class="path-step-head">
        <span class="path-step-order">${step.recommended_order}</span>
        <strong>${escActivity(step.content_name)}</strong>
        <span class="path-step-badge ${cls}">${label}</span>
      </div>
      <p class="path-step-metrics">${ev}</p>
      <p class="path-step-reason"><strong>Por quê:</strong> ${escActivity(step.priority_reason)}</p>
      ${prereqs ? `<p class="path-step-prereq">Pré-requisito necessário: ${prereqs}</p>` : ''}
      ${blocks ? `<p class="path-step-blocks">Destrava: ${blocks}</p>` : ''}
      ${step.forced_closure_evidence_count ? `<p class="path-step-note">Inclui ${step.forced_closure_evidence_count} questão(ões) com classificação provisória.</p>` : ''}
      <div class="path-step-actions">${studyBtn}${actionBtn}</div>
    </article>`;
  }

  // ---------- PHASE 22: "Praticar agora" (motor de prática adaptativa) ----------
  // Turns a Trilha step into a real practice loop: create (origin=PRACTICE) ->
  // reused PHASE 17 player -> PHASE 18 result (framed as study, not a grade) ->
  // recalculated Trilha. Sem gamificação, sem ranking, sem nota.
  //
  // create a practice (origin=PRACTICE) for one content and hand it to
  // the reused PHASE 17 player. No new player, no new bank, no AI. On an
  // insufficient bank the server returns the three counts + the standard message,
  // which we surface inline so the student can pick a smaller size.
  async function launchPractice(contentCode, contentName, count, msgEl, btnEl) {
    if (!contentCode) return;
    if (msgEl) { msgEl.hidden = true; msgEl.textContent = ''; }
    if (btnEl) btnEl.disabled = true;
    try {
      const res = await studentRequest('/api/v1/student/practice', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ content_code: contentCode, question_count: Number(count) || 10 }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const d = (data && data.detail) || {};
        let text = (typeof d === 'string' ? d : d.message)
          || 'Não foi possível iniciar a prática agora.';
        if (d && d.available_questions !== undefined) {
          text = `Não há questões suficientes disponíveis para esta prática. `
            + `(disponíveis: ${d.available_questions}, pedidas: ${d.requested_questions})`;
        }
        if (msgEl) { msgEl.textContent = text; msgEl.hidden = false; }
        if (btnEl) btnEl.disabled = false;
        return;
      }
      practiceFlow = { contentName: data.content_name || contentName || 'Prática' };
      if (btnEl) btnEl.disabled = false;
      switchView('activities');
      startActivityPlayer(data.practice_id);
    } catch (err) {
      if (msgEl) { msgEl.textContent = 'Sem conexão para iniciar a prática.'; msgEl.hidden = false; }
      if (btnEl) btnEl.disabled = false;
    }
  }

  async function populateDirectPractice() {
    const wrap = document.getElementById('path-direct-practice');
    if (!wrap) return;
    try {
      const res = await studentRequest('/api/v1/student/domain');
      const d = await res.json().catch(() => ({}));
      const discs = (d.disciplines || []).filter((x) => (x.contents || []).length);
      if (!discs.length) { wrap.hidden = true; pdpData = null; return; }
      pdpData = discs;
      const dsel = document.getElementById('pdp-discipline');
      dsel.innerHTML = discs.map((x, i) =>
        `<option value="${i}">${escActivity(x.discipline_name || x.discipline_code || 'Disciplina')}</option>`).join('');
      populatePdpContents();
      wrap.hidden = false;
    } catch (err) { wrap.hidden = true; pdpData = null; }
  }

  function populatePdpContents() {
    const csel = document.getElementById('pdp-content');
    if (!csel || !pdpData) return;
    const di = Number((document.getElementById('pdp-discipline') || {}).value || 0);
    const contents = ((pdpData[di] || {}).contents) || [];
    csel.innerHTML = contents.map((c) =>
      `<option value="${escActivity(c.content_code)}">${escActivity(c.content_name || c.content_code)}</option>`).join('');
  }

  (function wireStudyPath() {
    const body = document.getElementById('path-body');
    if (body) body.addEventListener('click', (e) => {
      const studyBtn = e.target.closest('.path-study-btn');
      if (studyBtn) {
        openMaterialReader(studyBtn.dataset.materialId, { returnTo: 'study-path' });
        return;
      }
      const openBtn = e.target.closest('.path-practice-btn');
      if (openBtn) {
        const panel = openBtn.parentElement.querySelector('.path-practice-panel');
        if (panel) panel.hidden = !panel.hidden;
        return;
      }
      const cancel = e.target.closest('.path-practice-cancel');
      if (cancel) { const p = cancel.closest('.path-practice-panel'); if (p) p.hidden = true; return; }
      const start = e.target.closest('.path-practice-start');
      if (start) {
        const panel = start.closest('.path-practice-panel');
        const trigger = panel.parentElement.querySelector('.path-practice-btn');
        const picked = panel.querySelector('input[type="radio"]:checked');
        launchPractice(trigger.dataset.content, trigger.dataset.name,
          Number((picked || {}).value || 10), panel.querySelector('.path-practice-msg'), start);
      }
    });
    const ddisc = document.getElementById('pdp-discipline');
    if (ddisc) ddisc.addEventListener('change', populatePdpContents);
    const dstart = document.getElementById('pdp-start');
    if (dstart) dstart.addEventListener('click', () => {
      const csel = document.getElementById('pdp-content');
      if (!csel || !csel.value) return;
      const name = csel.selectedOptions && csel.selectedOptions[0]
        ? csel.selectedOptions[0].textContent : csel.value;
      launchPractice(csel.value, name, Number(document.getElementById('pdp-count').value || 10),
        document.getElementById('pdp-msg'), dstart);
    });
  })();

  // ===================================================================
  // PHASE 24 — "Momento de Aprendizado" (Study Session). ORCHESTRATION only:
  // it drives GET/POST /api/v1/student/study-session*, which itself composes
  // the Domain Map (PHASE 20) + Adaptive Learning Path (PHASE 21) + Material
  // availability (PHASE 23) + Adaptive Practice (PHASE 22, lazily per PRACTICE
  // block) + the REUSED PHASE 17 player / PHASE 18 result. No new player, no
  // new practice engine, no client-side scoring. Zero IA.
  // ===================================================================
  const ss = { data: null };
  const SS_ICON = { STUDY: '📘', PRACTICE: '📝', REVIEW: '🔄', BREAK: '☕', DIAGNOSE: '🧭' };
  const SS_SCREENS = ['ss-loading', 'ss-prompt', 'ss-scheduled', 'ss-preview', 'ss-running', 'ss-done', 'ss-error'];

  function ssShow(id) {
    SS_SCREENS.forEach((x) => {
      const el = document.getElementById(x);
      if (el) el.hidden = (x !== id);
    });
  }
  function ssMsg(id, text) {
    const el = document.getElementById(id);
    if (!el) return;
    if (!text) { el.hidden = true; el.textContent = ''; return; }
    el.textContent = text; el.hidden = false;
  }
  function ssFmtMin(total) {
    const m = Math.max(0, Math.round(total || 0));
    if (m < 60) return `${m} min`;
    const h = Math.floor(m / 60), r = m % 60;
    return r ? `${h}h${String(r).padStart(2, '0')}` : `${h} h`;
  }
  function ssFmtClock(iso) {
    if (!iso) return '';
    try { return new Date(iso).toLocaleTimeString('pt-BR', { hour: '2-digit', minute: '2-digit' }); }
    catch (e) { return ''; }
  }

  async function loadStudySessionView() {
    ssShow('ss-loading');
    try {
      const res = await studentRequest('/api/v1/student/study-session/today');
      if (!res.ok) { ssShow('ss-error'); return; }
      const data = await res.json();
      if (!data.has_session) { ss.data = null; renderSsPrompt(); return; }
      ss.data = data.session;
      renderSsForStatus();
    } catch (err) {
      ssShow('ss-error');
    }
  }

  function renderSsForStatus() {
    const s = ss.data;
    if (!s) { renderSsPrompt(); return; }
    if (s.status === 'CANCELLED') {
      document.getElementById('ss-error-text').textContent = 'Este momento de aprendizado foi cancelado.';
      ssShow('ss-error');
    } else if (s.status === 'COMPLETED') {
      renderSsDone(s);
    } else if (s.status === 'IN_PROGRESS') {
      renderSsRunning(s);
    } else if (s.source === 'SCHOOL_DEFINED' && s.status === 'SCHEDULED') {
      renderSsScheduled(s);
    } else {
      renderSsPreview(s);
    }
  }

  // ---------- 1. time prompt (aluno livre) ----------

  function renderSsPrompt() {
    document.querySelectorAll('.ss-time-btn').forEach((b) => b.classList.remove('is-selected'));
    document.getElementById('ss-time-custom').hidden = true;
    document.getElementById('ss-time-input').value = '';
    const btn = document.getElementById('ss-start-plan');
    btn.disabled = true;
    delete btn.dataset.minutes;
    delete btn.dataset.noTimer;
    ssMsg('ss-prompt-msg', '');
    ssShow('ss-prompt');
  }

  (function wireSsPrompt() {
    const grid = document.getElementById('ss-time-grid');
    if (!grid) return;
    const startBtn = document.getElementById('ss-start-plan');
    const customBox = document.getElementById('ss-time-custom');
    const customInput = document.getElementById('ss-time-input');

    grid.addEventListener('click', (e) => {
      const btn = e.target.closest('.ss-time-btn');
      if (!btn) return;
      grid.querySelectorAll('.ss-time-btn').forEach((b) => b.classList.remove('is-selected'));
      btn.classList.add('is-selected');
      delete startBtn.dataset.noTimer;
      if (btn.id === 'ss-time-other') {
        customBox.hidden = false;
        customInput.focus();
        startBtn.disabled = true;
        delete startBtn.dataset.minutes;
      } else {
        customBox.hidden = true;
        startBtn.dataset.minutes = btn.dataset.min;
        startBtn.disabled = false;
      }
    });
    customInput.addEventListener('input', () => {
      const v = Number(customInput.value);
      if (v >= 5 && v <= 600) { startBtn.dataset.minutes = String(v); startBtn.disabled = false; }
      else { delete startBtn.dataset.minutes; startBtn.disabled = true; }
    });
    document.getElementById('ss-no-timer').addEventListener('click', () => {
      grid.querySelectorAll('.ss-time-btn').forEach((b) => b.classList.remove('is-selected'));
      customBox.hidden = true;
      startBtn.dataset.noTimer = '1';
      delete startBtn.dataset.minutes;
      startBtn.disabled = false;
    });
    startBtn.addEventListener('click', async () => {
      ssMsg('ss-prompt-msg', '');
      startBtn.disabled = true;
      const payload = startBtn.dataset.noTimer
        ? { no_timer: true }
        : { available_minutes: Number(startBtn.dataset.minutes) };
      try {
        const res = await studentRequest('/api/v1/student/study-session', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) {
          const d = data.detail || {};
          ssMsg('ss-prompt-msg', (typeof d === 'string' ? d : d.message) || 'Não foi possível montar seu momento de aprendizado.');
          startBtn.disabled = false;
          return;
        }
        ss.data = data;
        renderSsForStatus();
      } catch (err) {
        ssMsg('ss-prompt-msg', 'Sem conexão para montar seu momento de aprendizado.');
        startBtn.disabled = false;
      }
    });
  })();

  // ---------- 2. school-scheduled announcement ----------

  function renderSsScheduled(s) {
    const text = (s.start_at && s.end_at)
      ? `Seu momento de aprendizado de hoje está programado das ${ssFmtClock(s.start_at)} às ${ssFmtClock(s.end_at)}.`
      : 'Sua coordenação programou um momento de aprendizado para hoje.';
    const names = (s.target_content_names && s.target_content_names.length)
      ? s.target_content_names : (s.target_content_codes || []);
    document.getElementById('ss-scheduled-text').textContent = text
      + (names.length
        ? ` Conteúdo${names.length > 1 ? 's' : ''}: ${names.join(', ')}.`
        : '');
    ssShow('ss-scheduled');
  }

  (function wireSsScheduled() {
    const btn = document.getElementById('ss-scheduled-start');
    if (btn) btn.addEventListener('click', () => renderSsPreview(ss.data));
  })();

  // ---------- 3. plan preview ----------

  function ssBlockPreviewRow(b) {
    const note = (!b.action_available && b.action_note)
      ? `<span class="ss-block-note">${escActivity(b.action_note)}</span>` : '';
    return `<div class="ss-preview-row ss-type-${b.block_type.toLowerCase()}">
      <span class="ss-preview-icon">${SS_ICON[b.block_type] || '•'}</span>
      <span class="ss-preview-title">${escActivity(b.title)}</span>
      <span class="ss-preview-minutes">${ssFmtMin(b.estimated_minutes)}</span>
      ${note}
    </div>`;
  }

  function renderSsPreview(s) {
    const contents = (s.target_content_names && s.target_content_names.length)
      ? s.target_content_names : (s.target_content_codes || []);
    document.getElementById('ss-preview-sub').textContent = contents.length
      ? `Hoje vamos trabalhar: ${contents.join(', ')}.`
      : 'A plataforma organizou sua sequência de estudo com base no seu domínio atual.';
    document.getElementById('ss-preview-blocks').innerHTML =
      (s.blocks || []).map(ssBlockPreviewRow).join('') || '<p class="empty-text">Nenhum bloco gerado.</p>';
    document.getElementById('ss-preview-time').textContent =
      `Tempo: ${ssFmtMin(s.effective_study_minutes)} de estudo` +
      (s.break_minutes ? ` + ${ssFmtMin(s.break_minutes)} de intervalos` : '') +
      (s.timer_mode === 'UNTIMED' ? ' (sem cronômetro rígido)' : '');
    document.getElementById('ss-preview-notes').textContent = (s.plan_notes || []).join(' ');
    ssShow('ss-preview');
  }

  (function wireSsPreview() {
    const btn = document.getElementById('ss-begin');
    if (!btn) return;
    btn.addEventListener('click', async () => {
      if (!ss.data) return;
      btn.disabled = true;
      try {
        const res = await studentRequest(`/api/v1/student/study-session/${ss.data.id}/start`, { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        btn.disabled = false;
        if (!res.ok) return;
        ss.data = data;
        renderSsForStatus();
      } catch (err) { btn.disabled = false; }
    });
  })();

  // ---------- 4. running session ----------

  function ssCurrentBlock(s) {
    return (s.blocks || []).find((b) => b.index === s.current_block_index) || null;
  }
  function ssNextBlock(s, from) {
    return (s.blocks || []).find((b) => b.index > from && b.status === 'PENDING') || null;
  }

  function renderSsRunning(s) {
    ssMsg('ss-running-msg', '');
    document.getElementById('ss-progress-label').textContent =
      `${s.blocks_done}/${s.blocks_total} blocos concluídos — ${s.progress_percent}%`;
    document.getElementById('ss-progress-fill').style.width = `${s.progress_percent}%`;

    const now = ssCurrentBlock(s);
    const nowCard = document.getElementById('ss-now-card');
    const nextCard = document.getElementById('ss-next-card');

    if (!now || s.blocks_done >= s.blocks_total) {
      nowCard.innerHTML = `<div class="ss-block-head"><span class="ss-block-icon">✅</span>
        <strong>Todos os blocos concluídos</strong></div>
        <button class="btn btn-primary" type="button" id="ss-finish-btn">Finalizar momento de aprendizado</button>`;
      nextCard.innerHTML = '';
      const fb = document.getElementById('ss-finish-btn');
      if (fb) fb.addEventListener('click', ssFinishSession);
      ssShow('ss-running');
      return;
    }

    if (now.block_type === 'BREAK') {
      const until = now.end_at ? ` Volta às ${ssFmtClock(now.end_at)}.` : '';
      nowCard.innerHTML = `<div class="ss-block-head"><span class="ss-block-icon">☕</span>
        <strong>Hora de uma pausa.</strong></div>
        <p class="ss-block-meta">Intervalo — ${ssFmtMin(now.estimated_minutes)}.${until}
        Isso não conta como tempo de estudo.</p>
        <button class="btn btn-primary" type="button" id="ss-block-continue">Continuar</button>`;
      document.getElementById('ss-block-continue').addEventListener('click', () => ssCompleteCurrentBlock(now));
    } else {
      const label = { STUDY: 'Estudar', PRACTICE: 'Praticar', REVIEW: 'Revisar', DIAGNOSE: 'Diagnosticar' }[now.block_type] || now.block_type;
      const content = now.content_name ? ` ${escActivity(now.content_name)}` : '';
      const note = now.action_note ? `<p class="ss-block-note">${escActivity(now.action_note)}</p>` : '';
      // PHASE 25: a STUDY block whose content has a real published material
      // gets a real "Estudar agora" opening the Material Player - it does not
      // replace "Concluir bloco"/"Pular" (the student still finishes the
      // block manually after reading; break/points logic is unchanged).
      const studyMaterialBtn = (now.block_type === 'STUDY' && now.material_id)
        ? `<button class="btn btn-secondary" type="button" id="ss-block-study">📘 Estudar agora</button>` : '';
      let actionBtn;
      if (now.block_type === 'PRACTICE') {
        actionBtn = `<button class="btn btn-primary" type="button" id="ss-block-action">Iniciar prática</button>`;
      } else if (now.action_available) {
        actionBtn = `<button class="btn btn-primary" type="button" id="ss-block-action">Concluir bloco</button>`;
      } else {
        actionBtn = `<button class="btn btn-secondary" type="button" id="ss-block-action">Pular bloco</button>`;
      }
      nowCard.innerHTML = `<div class="ss-block-head"><span class="ss-block-icon">${SS_ICON[now.block_type] || '•'}</span>
        <strong>${label}${content}</strong></div>
        <p class="ss-block-meta">${ssFmtMin(now.estimated_minutes)}</p>
        ${note}
        <div class="ss-block-actions">${studyMaterialBtn}${actionBtn}
          <button class="btn btn-link" type="button" id="ss-block-skip">Pular</button>
        </div>`;
      document.getElementById('ss-block-action').addEventListener('click', () => ssHandleBlockAction(now));
      const skipBtn = document.getElementById('ss-block-skip');
      if (skipBtn) skipBtn.addEventListener('click', () => ssCompleteCurrentBlock(now, true));
      const studyBtn = document.getElementById('ss-block-study');
      if (studyBtn) studyBtn.addEventListener('click', () =>
        openMaterialReader(now.material_id, { returnTo: 'study-session' }));
    }

    const nxt = ssNextBlock(s, now.index);
    nextCard.innerHTML = nxt
      ? `<span class="ss-next-label">Depois:</span>
         <span class="ss-next-icon">${SS_ICON[nxt.block_type] || '•'}</span>
         ${escActivity(nxt.title)} — ${ssFmtMin(nxt.estimated_minutes)}`
      : '<span class="ss-next-label">Este é o último bloco.</span>';
    ssShow('ss-running');
  }

  async function ssHandleBlockAction(block) {
    if (block.block_type === 'PRACTICE') {
      const btn = document.getElementById('ss-block-action');
      if (btn) btn.disabled = true;
      try {
        const res = await studentRequest(
          `/api/v1/student/study-session/${ss.data.id}/blocks/${block.index}/start`, { method: 'POST' });
        const data = await res.json().catch(() => ({}));
        if (!res.ok) { ssMsg('ss-running-msg', 'Não foi possível iniciar a prática.'); if (btn) btn.disabled = false; return; }
        ss.data = data;
        const updated = (data.blocks || []).find((b) => b.index === block.index);
        if (updated && updated.assignment_id && updated.status !== 'SKIPPED') {
          studySessionFlow = { sessionId: ss.data.id, blockIndex: block.index };
          switchView('activities');
          startActivityPlayer(updated.assignment_id);
        } else {
          // insufficient bank -> the block was skipped server-side; show why and move on
          renderSsRunning(ss.data);
        }
      } catch (err) {
        ssMsg('ss-running-msg', 'Sem conexão para iniciar a prática.');
        if (btn) btn.disabled = false;
      }
      return;
    }
    // STUDY / REVIEW: self-paced - the student marks it done when finished.
    ssCompleteCurrentBlock(block, !block.action_available);
  }

  async function ssCompleteCurrentBlock(block, skipped) {
    await completeStudySessionBlock(ss.data.id, block.index, !!skipped);
    renderSsRunning(ss.data);
  }

  async function completeStudySessionBlock(sessionId, index, skipped) {
    try {
      const res = await studentRequest(
        `/api/v1/student/study-session/${sessionId}/blocks/${index}/complete`, {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ skipped: !!skipped }),
        });
      if (res.ok) ss.data = await res.json();
    } catch (err) { /* best-effort; the session stays resumable */ }
  }

  async function ssFinishSession() {
    if (!ss.data) return;
    try {
      const res = await studentRequest(`/api/v1/student/study-session/${ss.data.id}/complete`, { method: 'POST' });
      if (res.ok) { ss.data = await res.json(); renderSsDone(ss.data); }
    } catch (err) { /* keep the running view */ }
  }

  // ---------- 5. done ----------

  function renderSsDone(s) {
    document.getElementById('ss-done-summary').textContent =
      `Você concluiu ${s.blocks_done} de ${s.blocks_total} blocos — ${ssFmtMin(s.effective_study_minutes)} de estudo.`;
    ssShow('ss-done');
  }

  (function wireSsDone() {
    const btn = document.getElementById('ss-done-back');
    if (btn) btn.addEventListener('click', () => switchView('dashboard'));
  })();

  // ===================================================================
  // PHASE 25 — Material Player ("Momento de Estudo" reading experience).
  // Opened CONTEXTUALLY (from the Trilha's "Estudar agora" or a Study Session
  // STUDY block) - not a top-nav view. Drives GET /api/v1/student/materials*
  // (PHASE 25, reuses the PHASE 23 material model as-is). "Pratique o que
  // você estudou" hands off to the EXISTING launchPractice()/PHASE 17 player -
  // no second player, no second question selector. Reading progress is saved
  // via PUT .../progress; it is position-only and is NEVER read as evidence
  // of mastery (no Domain Map write happens here).
  // ===================================================================

  const BLOCK_LABEL = {
    HEADING: null, TEXT: null, DEFINITION: 'Definição', FORMULA: 'Fórmula',
    EXAMPLE: 'Exemplo', SOLVED_EXAMPLE: 'Exemplo resolvido', TABLE: 'Tabela',
    IMAGE: 'Imagem', CALLOUT: 'Destaque', EXERCISE_REFERENCE: 'Exercício',
    SUMMARY: 'Resumo', REVIEW: 'Revisão', OTHER: null,
  };

  const mr = { materialId: null, material: null, sections: [], sectionIdx: 0, loading: false };

  function mrShow(id) {
    ['mr-loading', 'mr-error', 'mr-body'].forEach((x) => {
      const el = document.getElementById(x);
      if (el) el.hidden = x !== id;
    });
  }

  async function openMaterialReader(materialId, opts) {
    if (!materialId) return;
    materialReaderFlow = opts || {};
    mr.materialId = materialId;
    mr.material = null;
    mr.sections = [];
    mr.sectionIdx = 0;
    switchView('material-reader');
    mrShow('mr-loading');
    document.getElementById('mr-title').textContent = 'Carregando…';
    document.getElementById('mr-subtitle').textContent = '';
    try {
      const [matRes, secRes, progRes] = await Promise.all([
        studentRequest(`/api/v1/student/materials/${materialId}`),
        studentRequest(`/api/v1/student/materials/${materialId}/sections`),
        studentRequest(`/api/v1/student/materials/${materialId}/progress`),
      ]);
      if (!matRes.ok || !secRes.ok) {
        const detail = (await matRes.json().catch(() => ({}))).detail;
        document.getElementById('mr-error-text').textContent =
          matRes.status === 403
            ? 'Este material não está disponível para você.'
            : (typeof detail === 'string' ? detail : 'Não foi possível carregar este material.');
        mrShow('mr-error');
        return;
      }
      mr.material = await matRes.json();
      mr.sections = await secRes.json();
      const progress = progRes.ok ? await progRes.json() : null;
      pageTitle.textContent = mr.material.title || 'Material';
      document.getElementById('mr-title').textContent = mr.material.title || 'Material';
      document.getElementById('mr-subtitle').textContent =
        [mr.material.content_code, mr.material.estimated_minutes ? `~${mr.material.estimated_minutes} min de leitura` : null]
          .filter(Boolean).join(' · ');
      if (!mr.sections.length) {
        document.getElementById('mr-error-text').textContent = 'Este material ainda não tem conteúdo publicado.';
        mrShow('mr-error');
        return;
      }
      // resume approximately where the student left off (spec s10/s19)
      let startIdx = 0;
      if (progress && progress.started && progress.current_section_id) {
        const found = mr.sections.findIndex((s) => s.section_id === progress.current_section_id);
        if (found >= 0) startIdx = found;
      }
      mr.sectionIdx = startIdx;
      mrShow('mr-body');
      mrRenderSection();
    } catch (err) {
      document.getElementById('mr-error-text').textContent = 'Sem conexão para carregar este material.';
      mrShow('mr-error');
    }
  }

  function mrRenderBlock(b) {
    const label = BLOCK_LABEL[b.block_type];
    const heading = b.block_type === 'HEADING';
    const bodyHtml = escActivity(b.body || '').replace(/\n/g, '<br>');
    const imgUrl = b.metadata && b.metadata.image_url;
    return `<div class="mr-block mr-block-${(b.block_type || 'other').toLowerCase()}">
      ${b.title ? `<h4 class="mr-block-title">${escActivity(b.title)}</h4>` : ''}
      ${label ? `<span class="mr-block-label">${label}</span>` : ''}
      ${heading ? '' : imgUrl ? `<img class="mr-block-image" src="${escActivity(imgUrl)}" alt="${escActivity(b.title || '')}">` : ''}
      ${bodyHtml ? `<${heading ? 'h3' : 'p'} class="mr-block-body">${bodyHtml}</${heading ? 'h3' : 'p'}>` : ''}
    </div>`;
  }

  function mrRenderSection() {
    const total = mr.sections.length;
    const idx = mr.sectionIdx;
    const s = mr.sections[idx];
    document.getElementById('mr-progress-label').textContent = `Seção ${idx + 1} de ${total}`;
    document.getElementById('mr-progress-fill').style.width = `${Math.round(((idx + 1) / total) * 100)}%`;
    document.getElementById('mr-section-nav').innerHTML = mr.sections.map((sec, i) =>
      `<button type="button" class="mr-section-dot${i === idx ? ' is-active' : ''}" data-idx="${i}"
        title="${escActivity(sec.title || sec.section_type)}">${i + 1}</button>`).join('');
    const blocksHtml = (s.blocks || []).map(mrRenderBlock).join('')
      || '<p class="empty-text">Esta seção ainda não tem conteúdo.</p>';
    document.getElementById('mr-section-content').innerHTML = `
      <h3 class="mr-section-title">${escActivity(s.title || s.section_type)}</h3>
      ${s.body ? `<p class="mr-section-lead">${escActivity(s.body).replace(/\n/g, '<br>')}</p>` : ''}
      ${blocksHtml}`;
    const prevBtn = document.getElementById('mr-prev');
    const nextBtn = document.getElementById('mr-next');
    prevBtn.disabled = idx === 0;
    nextBtn.textContent = idx === total - 1 ? 'Concluir ✓' : 'Próxima ›';
    const exBox = document.getElementById('mr-practice-box');
    if (s.exercises && s.exercises.length) {
      exBox.hidden = false;
      const cc = s.content_code || mr.material.content_code;
      document.getElementById('mr-practice-btn').disabled = !cc;
    } else {
      exBox.hidden = true;
    }
    mrSaveProgress(s.section_id, idx === total - 1);
  }

  async function mrSaveProgress(sectionId, completed) {
    if (!mr.materialId) return;
    try {
      await studentRequest(`/api/v1/student/materials/${mr.materialId}/progress`, {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ current_section_id: sectionId, completed: !!completed }),
      });
    } catch (err) { /* best-effort; resume just falls back to the first section */ }
  }

  (function wireMaterialReader() {
    const backBtn = document.getElementById('mr-back');
    if (backBtn) backBtn.addEventListener('click', () => {
      const flow = materialReaderFlow || {};
      materialReaderFlow = null;
      switchView(flow.returnTo === 'study-session' ? 'study-session' : 'study-path');
    });
    const prevBtn = document.getElementById('mr-prev');
    if (prevBtn) prevBtn.addEventListener('click', () => {
      if (mr.sectionIdx > 0) { mr.sectionIdx -= 1; mrRenderSection(); }
    });
    const nextBtn = document.getElementById('mr-next');
    if (nextBtn) nextBtn.addEventListener('click', () => {
      if (mr.sectionIdx < mr.sections.length - 1) { mr.sectionIdx += 1; mrRenderSection(); }
    });
    const nav = document.getElementById('mr-section-nav');
    if (nav) nav.addEventListener('click', (e) => {
      const dot = e.target.closest('.mr-section-dot');
      if (dot) { mr.sectionIdx = Number(dot.dataset.idx); mrRenderSection(); }
    });
    const practiceBtn = document.getElementById('mr-practice-btn');
    if (practiceBtn) practiceBtn.addEventListener('click', () => {
      const s = mr.sections[mr.sectionIdx];
      const cc = (s && s.content_code) || (mr.material && mr.material.content_code);
      if (!cc) return;
      materialPracticeFlow = { materialId: mr.materialId };
      launchPractice(cc, mr.material.title, 10,
        document.getElementById('mr-practice-msg'), practiceBtn);
    });
  })();

  // Initial Boot
  loadDashboardData();
});
