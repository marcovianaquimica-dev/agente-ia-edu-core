/* AGENTE IA EDU — Portal da Coordenação e Direção JS (Phase 12C.2) */

document.addEventListener('DOMContentLoaded', () => {
  const state = {
    currentView: 'dashboard',
    coordinatorId: 'user:coord_a',
    schoolId: '6f26cd3c-63d5-4509-a041-13714f75e53e',
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

  const filterCoordId = document.getElementById('filter-coord-id');
  const filterClassroom = document.getElementById('filter-coord-classroom-select');
  const filterPeriod = document.getElementById('filter-coord-period-select');

  filterCoordId.addEventListener('change', (e) => {
    state.coordinatorId = e.target.value.trim() || 'user:coord_a';
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
      'dashboard': { title: 'Dashboard da Coordenação', sub: 'Gestão pedagógica gerencial, turmas e direcionamento de professores' },
      'hierarchy': { title: 'Hierarquia Acadêmica', sub: 'Navegação em drill-down por Escola, Unidade, Segmento, Série e Turma' },
      'classrooms': { title: 'Turmas e Comparativo', sub: 'Visão agregada e comparativo lado a lado entre turmas' },
      'teachers': { title: 'Acompanhamento de Professores', sub: 'Supervisão de professores, escopos e aulas ministradas' },
      'students': { title: 'Consulta de Alunos na Coordenação', sub: 'Ficha individual e histórico de desempenho no escopo' },
      'contents': { title: 'Desempenho por Conteúdo no Escopo', sub: 'Domínio médio e contagem de alunos críticos por conteúdo' },
      'contexts': { title: 'Contexto Pedagógico e Orientações', sub: 'Registro de diretrizes da coordenação e acompanhamento de aulas' },
      'action-plan': { title: 'Plano de Ação Gerencial', sub: 'Ações prioritárias para elevar o domínio nas turmas' },
      'reports': { title: 'Relatórios Gerenciais', sub: 'Exportação executiva de relatórios pedagógicos em PDF e XLSX' },
      'profile': { title: 'Meu Perfil', sub: 'Escopo de atuação e credenciais da Coordenação' },
    };

    if (titleMap[viewName]) {
      pageTitle.textContent = titleMap[viewName].title;
      pageSubtitle.textContent = titleMap[viewName].sub;
    }

    loadCurrentView();
  }

  function loadCurrentView() {
    if (state.currentView === 'dashboard') loadCoordinationDashboard();
    if (state.currentView === 'hierarchy') loadCoordinationHierarchy();
    if (state.currentView === 'classrooms') loadClassroomsComparison();
    if (state.currentView === 'teachers') loadCoordinationTeachers();
    if (state.currentView === 'students') initCoordinationStudentSearch();
    if (state.currentView === 'contents') loadCoordinationContents();
    if (state.currentView === 'contexts') loadCoordinationContexts();
    if (state.currentView === 'action-plan') loadCoordinationActionPlan();
    if (state.currentView === 'reports') initCoordinationReportsView();
  }

  // 1. DASHBOARD LOADER
  async function loadCoordinationDashboard() {
    try {
      hideAlert();
      let url = `/api/v1/coordination/dashboard?school_id=${state.schoolId}&academic_year=${state.academicYear}&time_period=${state.timePeriod}`;
      if (state.classroomId) url += `&classroom_id=${state.classroomId}`;

      const res = await fetch(url, {
        headers: { 'Authorization': `Bearer ${state.coordinatorId}` }
      });

      if (res.status === 403) {
        showAlert('⚠️ Você não possui permissão para acessar os dados da coordenação para este escopo.', 'danger');
        renderEmptyCoordinationDashboard();
        return;
      }

      if (!res.ok) throw new Error('Erro ao carregar dashboard da coordenação');

      const data = await res.json();
      state.dashboardData = data;
      renderCoordinationDashboard(data);
    } catch (err) {
      console.warn('Coordination Dashboard error:', err);
      showAlert('Não conseguimos carregar os dados do dashboard da coordenação agora. Tente novamente.', 'danger');
    }
  }

  function renderCoordinationDashboard(d) {
    document.getElementById('coord-stat-students').textContent = d.total_students;
    document.getElementById('coord-stat-teachers').textContent = d.total_teachers;
    document.getElementById('coord-stat-classrooms').textContent = d.total_classrooms;
    document.getElementById('coord-stat-avg').textContent = `${d.overall_mastery_average}%`;

    // Distribution
    document.getElementById('coord-dist-danger').textContent = `${d.students_struggling_count} aluno(s) (${d.students_struggling_percentage}%)`;
    document.getElementById('coord-dist-warning').textContent = `${d.students_developing_count} aluno(s) (${d.students_developing_percentage}%)`;
    document.getElementById('coord-dist-success').textContent = `${d.students_mastered_count} aluno(s) (${d.students_mastered_percentage}%)`;

    // Classrooms Needing Attention
    const clsContainer = document.getElementById('coord-classrooms-attention-list');
    if (d.classrooms_needing_attention && d.classrooms_needing_attention.length > 0) {
      clsContainer.innerHTML = d.classrooms_needing_attention.map(c => `
        <div class="plan-item">
          <div>
            <strong>${c.name}</strong>
            <p style="font-size:12px; color:#ef4444;">Domínio Médio: ${c.average_mastery}% • Conteúdos Críticos: ${c.priority_contents.join(', ') || 'Vários'}</p>
          </div>
          <button class="btn btn-secondary" style="font-size:12px;" onclick="openClassroomDetailFromCoord('${c.classroom_id}')">VER TURMA</button>
        </div>
      `).join('');
    } else {
      clsContainer.innerHTML = '<p class="empty-text">Nenhuma turma em nível crítico no momento.</p>';
    }

    // Strengths
    const strengthsContainer = document.getElementById('coord-strengths-list');
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
    const improvementsContainer = document.getElementById('coord-improvements-list');
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
      improvementsContainer.innerHTML = '<p class="empty-text">Nenhum ponto de melhoria crítico no momento.</p>';
    }

    // Action Plan
    const planContainer = document.getElementById('coord-action-plan-container');
    if (d.action_plan && d.action_plan.length > 0) {
      planContainer.innerHTML = d.action_plan.map(a => `
        <div class="plan-column column-${a.priority === 'HIGH' ? 'danger' : 'warning'}" style="margin-bottom:12px;">
          <div class="column-header">
            <strong>🔴 Prioridade ${a.priority === 'HIGH' ? 'Alta' : 'Média'}: ${a.content_name}</strong>
          </div>
          <p style="font-size:13px; margin-bottom:6px;"><strong>Evidência:</strong> ${a.evidence}</p>
          <p style="font-size:13px; color:#4f46e5;"><strong>Ação Recomendada da Coordenação:</strong> ${a.recommended_action}</p>
        </div>
      `).join('');
    } else {
      planContainer.innerHTML = '<p class="empty-text">Nenhum plano de ação pendente para este escopo.</p>';
    }
  }

  // 2. HIERARQUIA ACADÊMICA LOADER
  async function loadCoordinationHierarchy() {
    const tree = document.getElementById('coord-hierarchy-tree');
    try {
      hideAlert();
      const res = await fetch(`/api/v1/coordination/hierarchy?school_id=${state.schoolId}&academic_year=${state.academicYear}`, {
        headers: { 'Authorization': `Bearer ${state.coordinatorId}` }
      });
      if (res.status === 403) {
        tree.innerHTML = '<p class="empty-text text-danger">⚠️ Acesso restrito ao escopo da coordenação.</p>';
        return;
      }
      if (!res.ok) throw new Error('Falha ao carregar hierarquia');
      const data = await res.json();

      tree.innerHTML = (data.units || []).map(u => `
        <div class="tree-unit">
          <h3>🏫 ${u.unit_name}</h3>
          ${(u.segments || []).map(seg => `
            <div class="tree-segment">
              <h4>🎓 ${seg.segment_name}</h4>
              ${(seg.grades || []).map(grd => `
                <div class="tree-grade">
                  <strong>📚 ${grd.grade_level} (${grd.student_count} alunos — Média: ${grd.average_mastery}%)</strong>
                  <div class="classrooms-grid" style="margin-top:10px;">
                    ${(grd.classrooms || []).map(cls => `
                      <div class="cls-card">
                        <div class="cls-card-header">
                          <span class="cls-card-title">${cls.name}</span>
                          <span class="badge badge-primary">${cls.average_mastery}%</span>
                        </div>
                        <p style="font-size:12px; color:#64748b;">${cls.student_count} alunos</p>
                        <button class="btn btn-secondary" style="font-size:12px; margin-top:8px;" onclick="openClassroomDetailFromCoord('${cls.classroom_id}')">VER TURMA</button>
                      </div>
                    `).join('')}
                  </div>
                </div>
              `).join('')}
            </div>
          `).join('')}
        </div>
      `).join('');
    } catch (err) {
      tree.innerHTML = '<p class="empty-text">Erro ao carregar hierarquia acadêmica.</p>';
    }
  }

  // 3. TURMAS E COMPARATIVO LOADER
  async function loadClassroomsComparison() {
    const container = document.getElementById('coord-comparison-table-container');
    try {
      hideAlert();
      const res = await fetch(`/api/v1/coordination/classrooms/compare?school_id=${state.schoolId}&academic_year=${state.academicYear}`, {
        headers: { 'Authorization': `Bearer ${state.coordinatorId}` }
      });
      if (res.status === 403) {
        container.innerHTML = '<p class="empty-text text-danger">⚠️ Acesso negado para comparativo de turmas.</p>';
        return;
      }
      if (!res.ok) throw new Error('Falha ao carregar comparativo');
      const items = await res.json();

      if (!items || items.length === 0) {
        container.innerHTML = '<p class="empty-text">Nenhuma turma no escopo para comparativo.</p>';
        return;
      }

      container.innerHTML = `
        <table class="data-table">
          <thead>
            <tr>
              <th>Turma</th>
              <th>Alunos</th>
              <th>Domínio Médio</th>
              <th>Críticos (&lt; 50%)</th>
              <th>Em Desenv. (50-69%)</th>
              <th>Consolidados (&ge; 70%)</th>
              <th>Ação</th>
            </tr>
          </thead>
          <tbody>
            ${items.map(i => `
              <tr>
                <td><strong>${i.name}</strong></td>
                <td>${i.student_count}</td>
                <td><strong class="${i.average_mastery < 50 ? 'text-danger' : (i.average_mastery < 70 ? 'text-warning' : 'text-success')}">${i.average_mastery}%</strong></td>
                <td class="text-danger">${i.struggling_count}</td>
                <td class="text-warning">${i.developing_count}</td>
                <td class="text-success">${i.mastered_count}</td>
                <td><button class="btn btn-secondary" style="font-size:12px;" onclick="openClassroomDetailFromCoord('${i.classroom_id}')">VER TURMA</button></td>
              </tr>
            `).join('')}
          </tbody>
        </table>
      `;
    } catch (err) {
      container.innerHTML = '<p class="empty-text">Erro ao carregar comparativo de turmas.</p>';
    }
  }

  // 4. ACOMPANHAMENTO DE PROFESSORES LOADER
  async function loadCoordinationTeachers() {
    const container = document.getElementById('coord-teachers-list-container');
    try {
      hideAlert();
      const res = await fetch(`/api/v1/coordination/teachers?school_id=${state.schoolId}&academic_year=${state.academicYear}`, {
        headers: { 'Authorization': `Bearer ${state.coordinatorId}` }
      });
      if (res.status === 403) {
        container.innerHTML = '<p class="empty-text text-danger">⚠️ Acesso restrito ao escopo da coordenação.</p>';
        return;
      }
      if (!res.ok) throw new Error('Falha ao carregar professores');
      const teachers = await res.json();

      if (!teachers || teachers.length === 0) {
        container.innerHTML = '<p class="empty-text">Nenhum professor encontrado no seu escopo.</p>';
        return;
      }

      container.innerHTML = teachers.map(t => `
        <div class="teacher-card">
          <div>
            <div class="teacher-card-header">
              <span class="teacher-card-title">${t.name}</span>
              <span class="badge badge-primary">${t.recent_lessons_count} Aulas Registradas</span>
            </div>
            <p style="font-size:13px; color:#64748b; margin-bottom:8px;">Turmas: ${t.assigned_classrooms.join(', ')}</p>
            <p style="font-size:13px; color:#64748b; margin-bottom:12px;">Total Alunos: ${t.total_students} • Média das Turmas: <strong>${t.classrooms_average_mastery}%</strong></p>
          </div>
          <button class="btn btn-secondary" style="font-size:12px; width:100%;" onclick="filterByTeacher('${t.teacher_id}')">VER TURMAS DO PROFESSOR</button>
        </div>
      `).join('');
    } catch (err) {
      container.innerHTML = '<p class="empty-text">Erro ao carregar lista de professores.</p>';
    }
  }

  window.filterByTeacher = function(tid) {
    state.classroomId = '';
    document.getElementById('filter-coord-classroom-select').value = '';
    switchView('dashboard');
  };

  // 5. SEARCH & STUDENT PROFILE
  function initCoordinationStudentSearch() {
    const btnSearch = document.getElementById('btn-coord-search-students');
    if (btnSearch) btnSearch.onclick = performCoordinationStudentSearch;
  }

  async function performCoordinationStudentSearch() {
    const q = document.getElementById('coord-search-student-input').value.trim();
    const container = document.getElementById('coord-search-results-container');

    if (!q) {
      container.innerHTML = '<p class="empty-text">Digite o nome ou ID do aluno para buscar.</p>';
      return;
    }

    try {
      const res = await fetch(`/api/v1/coordination/search?q=${encodeURIComponent(q)}&school_id=${state.schoolId}`, {
        headers: { 'Authorization': `Bearer ${state.coordinatorId}` }
      });
      if (res.status === 403) {
        container.innerHTML = '<p class="empty-text text-danger">⚠️ Pesquisa restrita ao escopo autorizado.</p>';
        return;
      }
      const results = await res.json();
      if (!results || results.length === 0) {
        container.innerHTML = '<p class="empty-text">Nenhum aluno encontrado no seu escopo.</p>';
        return;
      }

      container.innerHTML = results.map(s => `
        <div class="plan-item" style="margin-bottom:8px;">
          <div>
            <strong>${s.name} (${s.student_id})</strong>
            <p style="font-size:12px; color:#64748b;">Turma: ${s.classroom_id} • Domínio Médio: ${s.average_mastery}%</p>
          </div>
          <button class="btn btn-primary" style="font-size:12px;" onclick="openCoordinationStudentProfile('${s.student_id}')">VER FICHA INDIVIDUAL</button>
        </div>
      `).join('');
    } catch (err) {
      container.innerHTML = '<p class="empty-text">Erro na busca de alunos.</p>';
    }
  }

  window.openCoordinationStudentProfile = async function(studentId) {
    switchView('students');
    const box = document.getElementById('coord-student-profile-container');
    const body = document.getElementById('coord-student-profile-body');
    box.style.display = 'block';
    body.innerHTML = '<p class="empty-text">Carregando ficha do aluno...</p>';

    try {
      const res = await fetch(`/api/v1/coordination/students/${studentId}?school_id=${state.schoolId}`, {
        headers: { 'Authorization': `Bearer ${state.coordinatorId}` }
      });
      if (res.status === 403) {
        body.innerHTML = '<p class="empty-text text-danger">⚠️ Acesso não autorizado para esta ficha de aluno.</p>';
        return;
      }
      const st = await res.json();

      document.getElementById('coord-student-profile-title').textContent = `Ficha Individual — ${st.student_id} (${st.classroom_id})`;

      body.innerHTML = `
        <div class="stats-grid" style="margin-bottom:16px;">
          <div class="stat-card">
            <div class="stat-icon bg-blue">🎯</div>
            <div class="stat-data">
              <span class="stat-value">${st.accuracy_percentage}%</span>
              <span class="stat-label">Precisão Global</span>
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

        <h4 style="margin:16px 0 8px 0;">Orientação e Recomendação Ativa</h4>
        ${st.current_recommendations && st.current_recommendations.length > 0 ? `
          <div class="card active-rec-card" style="margin-bottom:16px;">
            <span class="badge badge-primary">${getContextTagLabel(st.current_recommendations[0].context_source)}</span>
            <p style="margin-top:8px; font-size:14px;">${st.current_recommendations[0].reason}</p>
          </div>
        ` : '<p class="empty-text">Sem recomendações pendentes.</p>'}

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

  // 6. CONTEXTO PEDAGÓGICO LOADER & MODAL
  async function loadCoordinationContexts() {
    const container = document.getElementById('coord-contexts-list-container');
    try {
      let url = `/api/v1/coordination/contexts?school_id=${state.schoolId}&academic_year=${state.academicYear}`;
      if (state.classroomId) url += `&classroom_id=${state.classroomId}`;

      const res = await fetch(url, {
        headers: { 'Authorization': `Bearer ${state.coordinatorId}` }
      });
      if (!res.ok) throw new Error('Falha ao carregar contextos');
      const contexts = await res.json();

      if (!contexts || contexts.length === 0) {
        container.innerHTML = '<p class="empty-text">Nenhuma orientação pedagógica ativa no momento.</p>';
        return;
      }

      container.innerHTML = contexts.map(c => `
        <div class="plan-column column-${c.source === 'TEACHER' ? 'success' : 'warning'}" style="margin-bottom:12px;">
          <div class="column-header">
            <span class="badge badge-primary">${getContextTagLabel(c.source)}</span>
            <strong>${c.title}</strong>
          </div>
          <p style="font-size:13px; margin:4px 0;"><strong>Conteúdo:</strong> ${c.content_name} • <strong>Turma:</strong> ${c.classroom_id || 'Toda a Escola'}</p>
          <p style="font-size:13px; color:#64748b;">${c.description || 'Sem observações adicionais.'}</p>
          <p style="font-size:12px; color:#94a3b8; margin-top:4px;">Registrado em: ${formatDate(c.recorded_at)} por ${c.author_id}</p>
        </div>
      `).join('');
    } catch (err) {
      container.innerHTML = '<p class="empty-text">Erro ao carregar contexto pedagógico.</p>';
    }
  }

  // Modal Context Listeners
  const btnOpenModal = document.getElementById('btn-open-context-modal');
  const modalContext = document.getElementById('modal-new-context');
  const btnCloseModal = document.getElementById('btn-close-context-modal');
  const btnCancelContext = document.getElementById('btn-cancel-context');
  const formContext = document.getElementById('form-new-context');

  if (btnOpenModal) {
    btnOpenModal.onclick = () => {
      loadCatalogSelectForContext();
      modalContext.style.display = 'flex';
    };
  }

  if (btnCloseModal) btnCloseModal.onclick = () => modalContext.style.display = 'none';
  if (btnCancelContext) btnCancelContext.onclick = () => modalContext.style.display = 'none';

  async function loadCatalogSelectForContext() {
    const select = document.getElementById('ctx-form-content');
    try {
      const res = await fetch('/api/v1/catalog/nodes');
      if (!res.ok) throw new Error('Falha ao carregar catálogo');
      const nodes = await res.json();
      select.innerHTML = nodes.map(n => `<option value="${n.id}">${n.name} (${n.node_type})</option>`).join('');
    } catch (err) {
      select.innerHTML = '<option value="" disabled>Erro ao carregar conteúdos do catálogo</option>';
    }
  }

  if (formContext) {
    formContext.onsubmit = async (e) => {
      e.preventDefault();
      const source = document.getElementById('ctx-form-source').value;
      const content_node_id = document.getElementById('ctx-form-content').value;
      const classroom_id = document.getElementById('ctx-form-classroom').value || null;
      const title = document.getElementById('ctx-form-title').value;
      const description = document.getElementById('ctx-form-desc').value;

      try {
        const res = await fetch('/api/v1/coordination/pedagogical-context', {
          method: 'POST',
          headers: {
            'Content-Type': 'application/json',
            'Authorization': `Bearer ${state.coordinatorId}`
          },
          body: JSON.stringify({
            school_id: state.schoolId,
            content_node_id,
            classroom_id,
            source,
            title,
            description,
            academic_year: state.academicYear,
          })
        });

        if (res.status === 403) {
          alert('⚠️ Você não possui permissão para registrar orientações neste escopo.');
          return;
        }

        if (!res.ok) throw new Error('Erro ao salvar orientação');

        modalContext.style.display = 'none';
        showAlert('✅ Orientação da Coordenação registrada e sincronizada!', 'success');
        loadCoordinationContexts();
        loadCoordinationDashboard();
      } catch (err) {
        alert('Erro ao registrar orientação.');
      }
    };
  }

  // 7. CONTENTS
  function loadCoordinationContents() {
    const container = document.getElementById('coord-contents-container');
    const d = state.dashboardData;
    if (d && d.average_mastery_by_content && d.average_mastery_by_content.length > 0) {
      container.innerHTML = `
        <div class="plan-list">
          ${d.average_mastery_by_content.map(c => `
            <div class="plan-item">
              <div>
                <strong>${c.content_name}</strong>
                <p style="font-size:12px; color:#64748b;">${c.students_struggling_count} aluno(s) na faixa crítica (&lt; 50%)</p>
              </div>
              <span class="${c.class_average_mastery < 50 ? 'text-danger' : 'text-success'}">${c.class_average_mastery}%</span>
            </div>
          `).join('')}
        </div>
      `;
    } else {
      container.innerHTML = '<p class="empty-text">Acesse o Dashboard para carregar os conteúdos da coordenação.</p>';
    }
  }

  // 8. ACTION PLAN
  function loadCoordinationActionPlan() {
    const container = document.getElementById('coord-full-action-plan-container');
    const d = state.dashboardData;
    if (d && d.action_plan && d.action_plan.length > 0) {
      container.innerHTML = d.action_plan.map(a => `
        <div class="card active-rec-card" style="margin-bottom:16px;">
          <div class="card-header">
            <h3>🔴 Prioridade ${a.priority}: ${a.content_name}</h3>
            <span class="badge badge-primary">Média: ${a.class_average_mastery}%</span>
          </div>
          <p><strong>Evidência:</strong> ${a.evidence}</p>
          <p style="margin-top:8px; color:#4f46e5;"><strong>Ação Recomendada:</strong> ${a.recommended_action}</p>
        </div>
      `).join('');
    } else {
      container.innerHTML = '<p class="empty-text">Nenhum plano de ação pendente para este escopo.</p>';
    }
  }

  // 9. REPORTS
  function initCoordinationReportsView() {
    const btnGen = document.getElementById('btn-coord-generate-report');
    if (btnGen) {
      btnGen.onclick = async () => {
        const classroomId = document.getElementById('coord-report-classroom-select').value;
        const fmt = document.getElementById('coord-report-format-select').value;
        const preview = document.getElementById('coord-report-preview-container');

        try {
          let url = `/api/v1/coordination/export?school_id=${state.schoolId}&academic_year=${state.academicYear}&format=${fmt}`;
          if (classroomId) url += `&classroom_id=${classroomId}`;

          const res = await fetch(url, {
            headers: { 'Authorization': `Bearer ${state.coordinatorId}` }
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
          preview.innerHTML = '<p class="empty-text text-danger">Erro ao solicitar exportação de relatório.</p>';
        }
      };
    }
  }

  // Helper Utilities
  window.openClassroomDetailFromCoord = function(clsId) {
    state.classroomId = clsId;
    document.getElementById('filter-coord-classroom-select').value = clsId;
    switchView('dashboard');
  };

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

  function renderEmptyCoordinationDashboard() {
    document.getElementById('coord-stat-students').textContent = '0';
    document.getElementById('coord-stat-teachers').textContent = '0';
    document.getElementById('coord-stat-classrooms').textContent = '0';
    document.getElementById('coord-stat-avg').textContent = '0.0%';
  }

  // Initial Load
  loadCoordinationDashboard();
});
