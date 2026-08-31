/* AGENTE IA EDU — Portal do Professor e Coordenação JS (Phase 12C.1) */

document.addEventListener('DOMContentLoaded', () => {
  const state = {
    currentView: 'dashboard',
    teacherId: 'user:prof_mendes',
    schoolId: '6f26cd3c-63d5-4509-a041-13714f75e53e', // Default school UUID or code
    schoolCode: 'SCH_A',
    classroomId: '',
    academicYear: '2026',
    timePeriod: 'academic_year',
    dashboardData: null,
    catalogNodes: [],
  };

  // UI References
  const navItems = document.querySelectorAll('.nav-item');
  const viewPanels = document.querySelectorAll('.view-panel');
  const pageTitle = document.getElementById('page-title');
  const pageSubtitle = document.getElementById('page-subtitle');
  const alertBox = document.getElementById('alert-box');

  const filterTeacherId = document.getElementById('filter-teacher-id');
  const filterClassroom = document.getElementById('filter-classroom-select');
  const filterPeriod = document.getElementById('filter-period-select');

  // Event Listeners for Filters
  filterTeacherId.addEventListener('change', (e) => {
    state.teacherId = e.target.value.trim() || 'user:prof_mendes';
    loadCurrentView();
  });

  filterClassroom.addEventListener('change', (e) => {
    state.classroomId = e.target.value;
    loadCurrentView();
  });

  filterPeriod.addEventListener('change', (e) => {
    state.timePeriod = e.target.value;
    loadCurrentView();
  });

  // Navigation Click Handlers
  navItems.forEach(item => {
    item.addEventListener('click', () => {
      const targetView = item.getAttribute('data-view');
      switchView(targetView);
    });
  });

  function switchView(viewName) {
    state.currentView = viewName;
    hideAlert();

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

    const titleMap = {
      'dashboard': { title: 'Dashboard do Professor', sub: 'Acompanhamento pedagógico e orientação para turmas' },
      'classrooms': { title: 'Minhas Turmas', sub: 'Visão geral das turmas dentro do seu escopo autorizado' },
      'contents': { title: 'Desempenho por Conteúdo', sub: 'Média de domínio da turma para cada conteúdo' },
      'students': { title: 'Consulta de Alunos', sub: 'Análise do perfil e histórico individual de aprendizado' },
      'lessons': { title: 'Registro de Aulas', sub: 'Aulas ministradas e sincronização com a Trilha do Aluno' },
      'performance': { title: 'Análise de Desempenho', sub: 'Gráficos e distribuição de maestria da turma' },
      'action-plan': { title: 'Plano de Ação da Turma', sub: 'Ações pedagógicas prioritárias calculadas pelo sistema' },
      'reports': { title: 'Exportação de Relatórios', sub: 'Relatórios pedagógicos em PDF e XLSX' },
      'profile': { title: 'Meu Perfil', sub: 'Informações do usuário e escopo autorizado' },
    };

    if (titleMap[viewName]) {
      pageTitle.textContent = titleMap[viewName].title;
      pageSubtitle.textContent = titleMap[viewName].sub;
    }

    loadCurrentView();
  }

  function loadCurrentView() {
    if (state.currentView === 'dashboard') loadTeacherDashboard();
    if (state.currentView === 'classrooms') loadClassroomsList();
    if (state.currentView === 'contents' || state.currentView === 'performance') loadContentsBreakdown();
    if (state.currentView === 'students') initStudentSearch();
    if (state.currentView === 'lessons') loadLessonsList();
    if (state.currentView === 'action-plan') loadActionPlanView();
    if (state.currentView === 'reports') initReportsView();
  }

  // 1. DASHBOARD LOADER
  async function loadTeacherDashboard() {
    try {
      hideAlert();
      let url = `/api/v1/teacher/dashboard?school_id=${state.schoolId}&academic_year=${state.academicYear}&time_period=${state.timePeriod}`;
      if (state.classroomId) url += `&classroom_id=${state.classroomId}`;

      const res = await fetch(url, {
        headers: { 'Authorization': `Bearer ${state.teacherId}` }
      });

      if (res.status === 403) {
        showAlert('⚠️ Você não possui permissão para acessar o dashboard desta turma/escola.', 'danger');
        renderEmptyDashboard();
        return;
      }

      if (!res.ok) throw new Error('Erro ao carregar dashboard');

      const data = await res.json();
      state.dashboardData = data;
      renderDashboard(data);
    } catch (err) {
      console.warn('Dashboard error:', err);
      showAlert('Não conseguimos carregar os dados do dashboard agora. Tente novamente.', 'danger');
    }
  }

  function renderDashboard(d) {
    document.getElementById('dash-stat-students').textContent = `${d.student_count} (${d.active_students_count} Ativos)`;
    document.getElementById('dash-stat-avg').textContent = `${d.overall_class_average}%`;
    document.getElementById('dash-stat-struggling').textContent = `${d.students_struggling_count} (${d.students_struggling_percentage}%)`;
    document.getElementById('dash-stat-lessons').textContent = `${(d.recent_lessons || []).length} Aula(s)`;

    // Distribution
    document.getElementById('dist-count-danger').textContent = `${d.students_struggling_count} aluno(s) (${d.students_struggling_percentage}%)`;
    document.getElementById('dist-count-warning').textContent = `${d.students_developing_count} aluno(s) (${d.students_developing_percentage}%)`;
    document.getElementById('dist-count-success').textContent = `${d.students_mastered_count} aluno(s) (${d.students_mastered_percentage}%)`;

    // Strengths
    const strengthsContainer = document.getElementById('dash-strengths-list');
    if (d.top_performing_contents && d.top_performing_contents.length > 0) {
      strengthsContainer.innerHTML = d.top_performing_contents.map(s => `
        <div class="plan-item">
          <strong>${s.content_name}</strong>
          <span class="text-success">${s.class_average_mastery}%</span>
        </div>
      `).join('');
    } else {
      strengthsContainer.innerHTML = '<p class="empty-text">Nenhum ponto forte identificado para esta seleção.</p>';
    }

    // Improvements
    const improvementsContainer = document.getElementById('dash-improvements-list');
    if (d.needs_attention_contents && d.needs_attention_contents.length > 0) {
      improvementsContainer.innerHTML = d.needs_attention_contents.map(i => `
        <div class="plan-item">
          <div>
            <strong>${i.content_name}</strong>
            <p style="font-size:12px; color:#64748b;">${i.students_struggling_count} aluno(s) em nível crítico (&lt; 50%)</p>
          </div>
          <span class="text-danger">${i.class_average_mastery}%</span>
        </div>
      `).join('');
    } else {
      improvementsContainer.innerHTML = '<p class="empty-text">Nenhum ponto de melhoria crítico nesta seleção.</p>';
    }

    // Recent Lessons
    const lessonsContainer = document.getElementById('dash-recent-lessons-container');
    if (d.recent_lessons && d.recent_lessons.length > 0) {
      lessonsContainer.innerHTML = d.recent_lessons.map(l => `
        <div class="plan-item">
          <div>
            <strong>${l.content_name} (${l.classroom_id})</strong>
            <p style="font-size:12px; color:#64748b;">${l.title || 'Aula'} • ${formatDate(l.lesson_date)}</p>
          </div>
          <button class="btn btn-secondary" style="font-size:12px; padding:4px 10px;" onclick="openClassroomDetail('${l.classroom_id}')">VER TURMA</button>
        </div>
      `).join('');
    } else {
      lessonsContainer.innerHTML = '<p class="empty-text">Nenhuma aula registrada nos últimos 14 dias.</p>';
    }

    // Action Plan
    const planContainer = document.getElementById('dash-action-plan-container');
    if (d.action_plan && d.action_plan.length > 0) {
      planContainer.innerHTML = d.action_plan.map(a => `
        <div class="plan-column column-${a.priority === 'HIGH' ? 'danger' : 'warning'}" style="margin-bottom:12px;">
          <div class="column-header">
            <strong>🔴 Prioridade ${a.priority === 'HIGH' ? 'Alta' : 'Média'}: ${a.content_name}</strong>
          </div>
          <p style="font-size:13px; margin-bottom:6px;"><strong>Evidência:</strong> ${a.evidence}</p>
          <p style="font-size:13px; color:#4f46e5;"><strong>Ação Recomendada:</strong> ${a.recommended_action}</p>
        </div>
      `).join('');
    } else {
      planContainer.innerHTML = '<p class="empty-text">Você ainda não possui dados suficientes para este plano de ação.</p>';
    }
  }

  // 2. MINHAS TURMAS LOADER
  async function loadClassroomsList() {
    const container = document.getElementById('classrooms-grid-container');
    try {
      hideAlert();
      const res = await fetch(`/api/v1/teacher/classrooms?school_id=${state.schoolId}&academic_year=${state.academicYear}`, {
        headers: { 'Authorization': `Bearer ${state.teacherId}` }
      });
      if (!res.ok) throw new Error('Falha ao carregar turmas');
      const items = await res.json();

      if (!items || items.length === 0) {
        container.innerHTML = '<p class="empty-text">Nenhuma turma associada ao seu escopo neste ano letivo.</p>';
        return;
      }

      container.innerHTML = items.map(c => `
        <div class="cls-card">
          <div>
            <div class="cls-card-header">
              <span class="cls-card-title">${c.name}</span>
              <span class="badge badge-primary">${c.grade_level}</span>
            </div>
            <p style="font-size:13px; color:#64748b; margin-bottom:12px;">${c.segment} • ${c.unit} • ${c.student_count} aluno(s)</p>
            <div class="mastery-inline">
              <span>Domínio Médio:</span>
              <strong>${c.average_mastery}%</strong>
            </div>
            ${c.priority_contents.length > 0 ? `
              <p style="font-size:12px; color:#ef4444; margin-top:8px;">
                <strong>Atenção:</strong> ${c.priority_contents.join(', ')}
              </p>
            ` : ''}
          </div>
          <button class="btn btn-primary" style="margin-top:16px; width:100%;" onclick="openClassroomDetail('${c.classroom_id}')">ABRIR TURMA</button>
        </div>
      `).join('');
    } catch (err) {
      container.innerHTML = '<p class="empty-text">Erro ao carregar turmas autorizadas.</p>';
    }
  }

  // 3. CLASSROOM DETAIL LOADER
  window.openClassroomDetail = async function(classroomId) {
    switchView('classroom-detail');
    const container = document.getElementById('cls-detail-body');
    container.innerHTML = '<p class="empty-text">Carregando detalhes da turma...</p>';

    try {
      const res = await fetch(`/api/v1/teacher/classrooms/${classroomId}?school_id=${state.schoolId}&academic_year=${state.academicYear}`, {
        headers: { 'Authorization': `Bearer ${state.teacherId}` }
      });

      if (res.status === 403) {
        container.innerHTML = '<p class="empty-text text-danger">⚠️ Você não possui permissão para acessar esta turma.</p>';
        return;
      }

      if (!res.ok) throw new Error('Erro ao carregar detalhes da turma');
      const d = await res.json();

      document.getElementById('cls-detail-title').textContent = `${d.classroom_id} — Visão Detalhada da Turma (${d.academic_year})`;

      container.innerHTML = `
        <div class="stats-grid" style="margin-bottom:20px;">
          <div class="stat-card">
            <div class="stat-icon bg-blue">👨‍🎓</div>
            <div class="stat-data">
              <span class="stat-value">${d.summary.student_count}</span>
              <span class="stat-label">Alunos na Turma</span>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon bg-purple">📊</div>
            <div class="stat-data">
              <span class="stat-value">${d.summary.overall_class_average}%</span>
              <span class="stat-label">Domínio Médio</span>
            </div>
          </div>
        </div>

        <h4 style="margin:16px 0 8px 0;">Domínio por Conteúdo</h4>
        <div class="plan-list">
          ${(d.average_mastery_by_content || []).map(c => `
            <div class="plan-item">
              <strong>${c.content_name}</strong>
              <span class="${c.class_average_mastery < 50 ? 'text-danger' : (c.class_average_mastery < 70 ? 'text-warning' : 'text-success')}">${c.class_average_mastery}%</span>
            </div>
          `).join('')}
        </div>

        <h4 style="margin:24px 0 8px 0;">Alunos que Precisam de Atenção (&lt; 50%)</h4>
        ${d.students_needing_attention.length > 0 ? `
          <table class="data-table">
            <thead>
              <tr><th>Aluno</th><th>Domínio Médio</th><th>Situação</th><th>Ação</th></tr>
            </thead>
            <tbody>
              ${d.students_needing_attention.map(s => `
                <tr>
                  <td><strong>${s.name}</strong></td>
                  <td class="text-danger">${s.average_mastery}%</td>
                  <td><span class="badge badge-primary">${s.status_label}</span></td>
                  <td><button class="btn btn-secondary" style="font-size:12px; padding:4px 10px;" onclick="openStudentProfile('${s.student_id}')">VER ALUNO</button></td>
                </tr>
              `).join('')}
            </tbody>
          </table>
        ` : '<p class="empty-text">Nenhum aluno em nível crítico nesta turma.</p>'}

        <h4 style="margin:24px 0 8px 0;">Aulas Ensinadas nos Últimos 14 Dias</h4>
        ${d.recent_contents_taught.length > 0 ? `
          <div class="plan-list">
            ${d.recent_contents_taught.map(rc => `
              <div class="plan-column column-warning" style="margin-bottom:8px;">
                <strong>${rc.content_name}</strong>
                <p style="font-size:12px; color:#64748b;">Ensinado em: ${formatDate(rc.last_lesson_date)} • Média da turma: ${rc.class_average_mastery}%</p>
                <p style="font-size:13px; color:#4f46e5; margin-top:4px;"><strong>Recomendação:</strong> ${rc.recommended_action}</p>
              </div>
            `).join('')}
          </div>
        ` : '<p class="empty-text">Nenhuma aula registrada nos últimos 14 dias.</p>'}
      `;
    } catch (err) {
      container.innerHTML = '<p class="empty-text">Erro ao carregar detalhes da turma.</p>';
    }
  };

  // 4. STUDENT SEARCH & PROFILE LOADER
  function initStudentSearch() {
    const btnSearch = document.getElementById('btn-search-students');
    const inputSearch = document.getElementById('search-student-input');

    if (btnSearch) {
      btnSearch.onclick = performStudentSearch;
    }
  }

  async function performStudentSearch() {
    const q = document.getElementById('search-student-input').value.trim();
    const container = document.getElementById('search-results-container');

    if (!q) {
      container.innerHTML = '<p class="empty-text">Digite o ID ou nome do aluno para pesquisar.</p>';
      return;
    }

    try {
      const res = await fetch(`/api/v1/teacher/search?q=${encodeURIComponent(q)}&school_id=${state.schoolId}`, {
        headers: { 'Authorization': `Bearer ${state.teacherId}` }
      });
      if (res.status === 403) {
        container.innerHTML = '<p class="empty-text text-danger">⚠️ Pesquisa restrita ao seu escopo autorizado.</p>';
        return;
      }

      const results = await res.json();
      if (!results || results.length === 0) {
        container.innerHTML = '<p class="empty-text">Nenhum aluno encontrado no seu escopo de busca.</p>';
        return;
      }

      container.innerHTML = results.map(s => `
        <div class="plan-item" style="margin-bottom:8px;">
          <div>
            <strong>${s.name} (${s.student_id})</strong>
            <p style="font-size:12px; color:#64748b;">Turma: ${s.classroom_id} • Domínio Médio: ${s.average_mastery}%</p>
          </div>
          <button class="btn btn-primary" style="font-size:12px;" onclick="openStudentProfile('${s.student_id}')">VER PERFIL</button>
        </div>
      `).join('');
    } catch (err) {
      container.innerHTML = '<p class="empty-text">Erro ao pesquisar alunos.</p>';
    }
  }

  window.openStudentProfile = async function(studentId) {
    switchView('students');
    const profileBox = document.getElementById('student-profile-container');
    const body = document.getElementById('student-profile-body');
    profileBox.style.display = 'block';
    body.innerHTML = '<p class="empty-text">Carregando perfil do aluno...</p>';

    try {
      const res = await fetch(`/api/v1/teacher/students/${studentId}?school_id=${state.schoolId}`, {
        headers: { 'Authorization': `Bearer ${state.teacherId}` }
      });

      if (res.status === 403) {
        body.innerHTML = '<p class="empty-text text-danger">⚠️ Você não possui permissão para acessar os dados deste aluno.</p>';
        return;
      }

      if (!res.ok) throw new Error('Erro ao obter perfil');
      const st = await res.json();

      document.getElementById('student-profile-title').textContent = `Perfil de ${st.student_id} (${st.classroom_id})`;

      body.innerHTML = `
        <div class="stats-grid" style="margin-bottom:16px;">
          <div class="stat-card">
            <div class="stat-icon bg-blue">🎯</div>
            <div class="stat-data">
              <span class="stat-value">${st.accuracy_percentage}%</span>
              <span class="stat-label">Precisão em Questões</span>
            </div>
          </div>
          <div class="stat-card">
            <div class="stat-icon bg-purple">✏️</div>
            <div class="stat-data">
              <span class="stat-value">${st.total_questions_answered}</span>
              <span class="stat-label">Questões Respondidas</span>
            </div>
          </div>
        </div>

        <h4 style="margin:16px 0 8px 0;">O que este aluno precisa agora?</h4>
        ${st.current_recommendations && st.current_recommendations.length > 0 ? `
          <div class="card active-rec-card" style="margin-bottom:16px;">
            <span class="badge badge-primary">${getContextTagLabel(st.current_recommendations[0].context_source)}</span>
            <p style="margin-top:8px; font-size:14px;">${st.current_recommendations[0].reason}</p>
          </div>
        ` : '<p class="empty-text">Nenhuma recomendação pendente.</p>'}

        <h4 style="margin:16px 0 8px 0;">Domínio por Conteúdo</h4>
        <div class="plan-list">
          ${(st.content_masteries || []).map(cm => `
            <div class="plan-item">
              <strong>${cm.content_name}</strong>
              <span class="${cm.mastery_score < 50 ? 'text-danger' : 'text-success'}">${cm.mastery_score}% (${cm.current_level})</span>
            </div>
          `).join('')}
        </div>
      `;
    } catch (err) {
      body.innerHTML = '<p class="empty-text">Erro ao carregar perfil do aluno.</p>';
    }
  };

  // 5. LESSONS REGISTRATION & MODAL LOADER
  async function loadLessonsList() {
    const container = document.getElementById('lessons-table-container');
    try {
      let url = `/api/v1/teacher/lessons?school_id=${state.schoolId}&academic_year=${state.academicYear}`;
      if (state.classroomId) url += `&classroom_id=${state.classroomId}`;

      const res = await fetch(url, {
        headers: { 'Authorization': `Bearer ${state.teacherId}` }
      });
      if (!res.ok) throw new Error('Falha ao carregar aulas');
      const lessons = await res.json();

      if (!lessons || lessons.length === 0) {
        container.innerHTML = '<p class="empty-text">Nenhuma aula registrada ainda neste ano letivo.</p>';
        return;
      }

      container.innerHTML = `
        <table class="data-table">
          <thead>
            <tr><th>Data</th><th>Turma</th><th>Título / Conteúdo</th><th>Duração</th><th>Observação</th></tr>
          </thead>
          <tbody>
            ${lessons.map(l => `
              <tr>
                <td>${formatDate(l.lesson_date)}</td>
                <td><strong>${l.classroom_id}</strong></td>
                <td><strong>${l.title || 'Aula'}</strong></td>
                <td>${l.duration_minutes ? l.duration_minutes + ' min' : '-'}</td>
                <td style="font-size:12px; color:#64748b;">${l.summary_observation || '-'}</td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    } catch (err) {
      container.innerHTML = '<p class="empty-text">Erro ao carregar aulas do professor.</p>';
    }
  }

  // Modal New Lesson Listeners
  const btnOpenModal = document.getElementById('btn-open-new-lesson-modal');
  const modalLesson = document.getElementById('modal-new-lesson');
  const btnCloseModal = document.getElementById('btn-close-lesson-modal');
  const btnCancelLesson = document.getElementById('btn-cancel-lesson');
  const formLesson = document.getElementById('form-new-lesson');

  if (btnOpenModal) {
    btnOpenModal.onclick = () => {
      loadCatalogSelectOptions();
      modalLesson.style.display = 'flex';
    };
  }

  if (btnCloseModal) btnCloseModal.onclick = () => modalLesson.style.display = 'none';
  if (btnCancelLesson) btnCancelLesson.onclick = () => modalLesson.style.display = 'none';

  async function loadCatalogSelectOptions() {
    const select = document.getElementById('lesson-form-content');
    try {
      const res = await fetch('/api/v1/catalog/nodes');
      if (!res.ok) throw new Error('Falha ao carregar catálogo');
      const nodes = await res.json();
      state.catalogNodes = nodes;

      select.innerHTML = nodes.map(n => `
        <option value="${n.id}">${n.name} (${n.node_type})</option>
      `).join('');
    } catch (err) {
      select.innerHTML = '<option value="" disabled>Erro ao carregar conteúdos do catálogo</option>';
    }
  }

  if (formLesson) {
    formLesson.onsubmit = async (e) => {
      e.preventDefault();
      const classroom_id = document.getElementById('lesson-form-classroom').value;
      const content_node_id = document.getElementById('lesson-form-content').value;
      const lesson_date = document.getElementById('lesson-form-date').value;
      const duration_minutes = parseInt(document.getElementById('lesson-form-duration').value || '50');
      const title = document.getElementById('lesson-form-title').value;
      const summary_observation = document.getElementById('lesson-form-obs').value;

      try {
        const res = await fetch('/api/v1/teacher/lessons', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${state.teacherId}`
          },
          body: JSON.stringify({
            school_id: state.schoolId,
            classroom_id,
            content_node_id,
            academic_year: state.academicYear,
            lesson_date: new Date(lesson_date).toISOString(),
            duration_minutes,
            title,
            summary_observation,
          })
        });

        if (res.status === 403) {
          alert('⚠️ Você não possui permissão para registrar aulas para esta turma.');
          return;
        }

        if (!res.ok) throw new Error('Erro ao salvar aula');

        modalLesson.style.display = 'none';
        showAlert('✅ Aula registrada com sucesso e sincronizada com a Trilha do Aluno!', 'success');
        loadLessonsList();
        loadTeacherDashboard();
      } catch (err) {
        alert('Erro ao salvar aula.');
      }
    };
  }

  // 6. CONTENTS / PERFORMANCE BREAKDOWN
  function loadContentsBreakdown() {
    const container = document.getElementById('contents-breakdown-container');
    const d = state.dashboardData;
    if (d && d.average_mastery_by_content && d.average_mastery_by_content.length > 0) {
      container.innerHTML = `
        <div class="plan-list">
          ${d.average_mastery_by_content.map(c => `
            <div class="plan-item">
              <div>
                <strong>${c.content_name}</strong>
                <p style="font-size:12px; color:#64748b;">${c.students_struggling_count} aluno(s) em nível crítico (&lt; 50%)</p>
              </div>
              <span class="${c.class_average_mastery < 50 ? 'text-danger' : 'text-success'}">${c.class_average_mastery}%</span>
            </div>
          `).join('')}
        </div>
      `;
    } else {
      container.innerHTML = '<p class="empty-text">Acesse o Dashboard para carregar os conteúdos da turma.</p>';
    }
  }

  // 7. ACTION PLAN VIEW
  function loadActionPlanView() {
    const container = document.getElementById('full-action-plan-container');
    const d = state.dashboardData;
    if (d && d.action_plan && d.action_plan.length > 0) {
      container.innerHTML = d.action_plan.map(a => `
        <div class="card active-rec-card" style="margin-bottom:16px;">
          <div class="card-header">
            <h3>🔴 Prioridade ${a.priority}: ${a.content_name}</h3>
            <span class="badge badge-primary">Média: ${a.class_average_mastery}%</span>
          </div>
          <p><strong>Evidências:</strong> ${a.evidence}</p>
          <p style="margin-top:8px; color:#4f46e5;"><strong>Ação Recomendada:</strong> ${a.recommended_action}</p>
        </div>
      `).join('');
    } else {
      container.innerHTML = '<p class="empty-text">Nenhum plano de ação pendente para esta seleção.</p>';
    }
  }

  // 8. REPORTS VIEW
  function initReportsView() {
    const btnGen = document.getElementById('btn-generate-report');
    if (btnGen) {
      btnGen.onclick = async () => {
        const classroomId = document.getElementById('report-classroom-select').value;
        const fmt = document.getElementById('report-format-select').value;
        const preview = document.getElementById('report-preview-container');

        try {
          const res = await fetch(`/api/v1/teacher/classrooms/${classroomId}/export?format=${fmt}&school_id=${state.schoolId}`, {
            headers: { 'Authorization': `Bearer ${state.teacherId}` }
          });
          if (!res.ok) throw new Error('Erro ao gerar relatório');
          const data = await res.json();

          preview.innerHTML = `
            <div class="alert-banner alert-success">
              ✅ <strong>${data.title}</strong> gerado com sucesso!
              <p style="font-size:12px; margin-top:4px;">Arquivo: <code>${data.filename}</code> (${data.content_type})</p>
            </div>
          `;
        } catch (err) {
          preview.innerHTML = '<p class="empty-text text-danger">Erro ao solicitar exportação do relatório.</p>';
        }
      };
    }
  }

  // 9. PROFILE VIEW
  function loadProfileView() {
    document.getElementById('prof-profile-id').textContent = state.teacherId;
  }

  // Helper Utilities
  function showAlert(msg, type = 'danger') {
    alertBox.style.display = 'block';
    alertBox.className = `alert-banner alert-${type}`;
    alertBox.textContent = msg;
  }

  function hideAlert() {
    alertBox.style.display = 'none';
  }

  function formatDate(isoStr) {
    if (!isoStr) return '-';
    const d = new Date(isoStr);
    return d.toLocaleDateString('pt-BR');
  }

  function getContextTagLabel(source) {
    const map = {
      'TEACHER': 'Aula do Professor',
      'COORDINATION': 'Orientação da Coordenação',
      'SCHOOL_PLAN': 'Planejamento da Escola',
      'AUTONOMOUS': 'Trilha Autônoma',
    };
    return map[source] || 'Orientação';
  }

  function renderEmptyDashboard() {
    document.getElementById('dash-stat-students').textContent = '0 (0 Ativos)';
    document.getElementById('dash-stat-avg').textContent = '0.0%';
    document.getElementById('dash-stat-struggling').textContent = '0 (0%)';
    document.getElementById('dash-stat-lessons').textContent = '0 Aulas';
  }

  // Initial Load
  loadTeacherDashboard();
});
