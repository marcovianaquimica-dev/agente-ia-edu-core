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
  };

  // UI Element References
  const navItems = document.querySelectorAll('.nav-item');
  const viewPanels = document.querySelectorAll('.view-panel');
  const pageTitle = document.getElementById('page-title');
  const pageSubtitle = document.getElementById('page-subtitle');
  const timePeriodSelect = document.getElementById('time-period-select');

  // Navigation Click Handlers
  navItems.forEach(item => {
    item.addEventListener('click', () => {
      const targetView = item.getAttribute('data-view');
      switchView(targetView);
    });
  });

  // Time Period Filter Handler
  timePeriodSelect.addEventListener('change', (e) => {
    state.timePeriod = e.target.value;
    loadDashboardData();
    loadEvolutionData();
  });

  function switchView(viewName) {
    state.currentView = viewName;

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
      'learning-path': { title: 'Minha Trilha de Aprendizagem', sub: 'Sua jornada adaptativa passo a passo' },
      'practice': { title: 'Praticar Questões', sub: 'Treinamento adaptativo focado na sua evolução' },
      'materials': { title: 'Materiais Teóricos', sub: 'Apostilas, PDFs e resumos organizados' },
      'videos': { title: 'Videoaulas Recomendadas', sub: 'Aulas interativas e personalizadas para seu nível' },
      'evolution': { title: 'Minha Evolução', sub: 'Análise detalhada do seu progresso por conteúdo' },
      'essay': { title: 'Módulo Redação IA', sub: 'Treino de redação no padrão ENEM' },
      'profile': { title: 'Meu Perfil', sub: 'Informações da sua conta e escola' },
    };

    if (titleMap[viewName]) {
      pageTitle.textContent = titleMap[viewName].title;
      pageSubtitle.textContent = titleMap[viewName].sub;
    }

    // Trigger View Loaders
    if (viewName === 'dashboard') loadDashboardData();
    if (viewName === 'learning-path') loadLearningPathData();
    if (viewName === 'evolution') loadEvolutionData();
    if (viewName === 'materials') loadMaterialsView();
    if (viewName === 'videos') loadVideosView();
  }

  // 1. Load Dashboard Data
  async function loadDashboardData() {
    try {
      showDashboardSkeleton();
      const res = await fetch(`/api/v1/student/dashboard?time_period=${state.timePeriod}`, {
        headers: { 'X-External-User-Id': state.studentId }
      });
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
    document.getElementById('welcome-msg-text').textContent = 'Atenção';
    document.getElementById('welcome-msg-sub').textContent = 'Não conseguimos carregar sua orientação agora. Tente novamente mais tarde.';
  }

  function renderDashboard(data) {
    // Welcome Banner / Empty State Check
    const welcomeMsgText = document.getElementById('welcome-msg-text');
    const welcomeMsgSub = document.getElementById('welcome-msg-sub');

    welcomeMsgText.textContent = data.welcome_message || 'Veja sua evolução';

    if (!data.has_data) {
      welcomeMsgSub.textContent = 'Você ainda não possui histórico neste período. Comece uma prática!';
    } else {
      welcomeMsgSub.textContent = `Filtro atual: ${getPeriodLabel(data.time_period)}`;
    }

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
      const res = await fetch('/api/v1/student/learning-path', {
        headers: { 'X-External-User-Id': state.studentId }
      });
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
    try {
      const res = await fetch(`/api/v1/student/evolution?time_period=${state.timePeriod}`, {
        headers: { 'X-External-User-Id': state.studentId }
      });
      if (!res.ok) throw new Error('Falha ao carregar evolução');
      const data = await res.json();

      container.innerHTML = `
        <div class="evolution-summary-box">
          <div class="evo-metric">
            <span>Precisão Geral</span>
            <strong>${data.accuracy_percentage || 0}%</strong>
          </div>
          <div class="evo-metric">
            <span>Respostas Corretas</span>
            <strong>${data.total_correct || 0} / ${data.total_answered || 0}</strong>
          </div>
        </div>
        <h4 style="margin-top:20px;">Evolução por Conteúdo</h4>
        <div class="plan-list" style="margin-top:10px;">
          ${(data.content_evolution || []).map(c => `
            <div class="plan-item">
              <strong>${c.content_name}</strong>
              <span>${c.current_score}%</span>
            </div>
          `).join('')}
        </div>
      `;
    } catch (err) {
      container.innerHTML = '<p class="empty-text">Nenhum histórico de evolução no período selecionado.</p>';
    }
  }

  // 4. Materials View
  function loadMaterialsView() {
    const container = document.getElementById('materials-list-container');
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
      const res = await fetch('/api/v1/practice/sessions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-External-User-Id': state.studentId
        },
        body: JSON.stringify({ requested_question_count: 5 })
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
      const res = await fetch(`/api/v1/practice/sessions/${sessionId}/next-question`, {
        headers: { 'X-External-User-Id': state.studentId }
      });
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

  function renderPracticeQuestion(sessionId, question) {
    const area = document.getElementById('practice-interactive-area');
    area.innerHTML = `
      <div class="practice-question-box">
        <div class="card-header">
          <span class="badge badge-primary">Questão ${question.position}</span>
          <span class="badge badge-accent">Nível ${question.difficulty_level}</span>
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
      const res = await fetch(`/api/v1/practice/sessions/${sessionId}/questions/${selectionId}/answer`, {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'X-External-User-Id': state.studentId
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
      const res = await fetch(`/api/v1/practice/sessions/${sessionId}/complete`, {
        method: 'POST',
        headers: { 'X-External-User-Id': state.studentId }
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

  // Initial Boot
  loadDashboardData();
});
