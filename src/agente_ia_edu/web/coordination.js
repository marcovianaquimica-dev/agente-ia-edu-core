/* AGENTE IA EDU — Portal da Coordenação e Direção JS (Phase 12C.2) */

const PRIORITY_LABEL = { HIGH: 'Alta', MEDIUM: 'Média', LOW: 'Baixa' };
const PRIORITY_STYLE = { HIGH: 'danger', MEDIUM: 'warning', LOW: 'success' };

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
  const navItems = document.querySelectorAll('.nav-item[data-view]');
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
      'materials': { title: 'Materiais Publicados no Escopo', sub: 'Materiais teóricos autorados, no escopo de atuação da coordenação' },
      'action-plan': { title: 'Plano de Ação Gerencial', sub: 'Ações prioritárias para elevar o domínio nas turmas' },
      'reports': { title: 'Relatórios Gerenciais', sub: 'Exportação executiva de relatórios pedagógicos em PDF e XLSX' },
      'study-sessions': { title: 'Momento de Aprendizado', sub: 'Programe a sessão de estudo de uma turma ou de um aluno' },
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
    if (state.currentView === 'materials') loadCoordinationMaterials();
    if (state.currentView === 'action-plan') loadCoordinationActionPlan();
    if (state.currentView === 'reports') initCoordinationReportsView();
    if (state.currentView === 'study-sessions') initStudySessionsView();
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
        <div class="plan-column column-${PRIORITY_STYLE[a.priority] || 'warning'}" style="margin-bottom:12px;">
          <div class="column-header">
            <strong>🔴 Prioridade ${PRIORITY_LABEL[a.priority] || a.priority}: ${a.content_name}</strong>
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
      const res = await fetch('/api/v1/catalog/nodes', {
        headers: { 'Authorization': `Bearer ${state.coordinatorId}` }
      });
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
  async function loadCoordinationContents() {
    const container = document.getElementById('coord-contents-container');
    if (!state.dashboardData) {
      await loadCoordinationDashboard();
    }
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

  // PHASE 25 — MATERIALS (read-only; reuses GET /api/v1/catalog/materials,
  // already authorized for COORDINATOR + already school-scoped server-side -
  // no parallel authorization rule is introduced here).
  function coordEsc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  async function loadCoordinationMaterials() {
    const container = document.getElementById('coord-materials-container');
    try {
      const res = await fetch('/api/v1/catalog/materials', {
        headers: { 'Authorization': `Bearer ${state.coordinatorId}` }
      });
      if (res.status === 403) {
        container.innerHTML = '<p class="empty-text">Você não possui permissão para ver materiais deste escopo.</p>';
        return;
      }
      if (!res.ok) throw new Error('Erro ao carregar materiais');
      const materials = await res.json();
      if (!materials.length) {
        container.innerHTML = '<p class="empty-text">Nenhum material encontrado no seu escopo.</p>';
        return;
      }
      container.innerHTML = `
        <div class="plan-list">
          ${materials.map((m) => `
            <div class="plan-item">
              <div>
                <strong>${coordEsc(m.title)}</strong>
                <p style="font-size:12px; color:#64748b;">
                  ${coordEsc(m.material_kind || 'Material')} · ${coordEsc(m.latest_version_status || 'DRAFT')}
                  · v${m.latest_version_number ?? '—'}
                  · ${m.section_count} seção(ões) · ${m.block_count} bloco(s) · ${m.question_count} exercício(s)
                  ${m.primary_content_code ? ` · ${coordEsc(m.primary_content_code)}` : ''}
                </p>
              </div>
              <span class="${m.latest_version_status === 'PUBLISHED' ? 'text-success' : 'text-danger'}">
                ${coordEsc(m.curriculum_status)}
              </span>
            </div>
          `).join('')}
        </div>`;
    } catch (err) {
      console.warn('Coordination Materials error:', err);
      container.innerHTML = '<p class="empty-text">Não foi possível carregar os materiais agora.</p>';
    }
  }

  // 8. ACTION PLAN
  async function loadCoordinationActionPlan() {
    const container = document.getElementById('coord-full-action-plan-container');
    if (!state.dashboardData) {
      await loadCoordinationDashboard();
    }
    const d = state.dashboardData;
    if (d && d.action_plan && d.action_plan.length > 0) {
      container.innerHTML = d.action_plan.map(a => `
        <div class="card active-rec-card" style="margin-bottom:16px;">
          <div class="card-header">
            <h3>🔴 Prioridade ${PRIORITY_LABEL[a.priority] || a.priority}: ${a.content_name}</h3>
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

  // The backend's shared authorization service reports failures in English
  // (an internal/API-contract string, not meant for display) - translate the
  // known patterns here rather than showing them raw to the user.
  const ROLE_LABELS = {
    DIRECTOR: 'Diretor(a)', COORDINATOR: 'Coordenador(a)', SECRETARY: 'Secretaria',
    TEACHER: 'Professor(a)', STUDENT: 'Aluno(a)', PLATFORM_ADMIN: 'Administrador da Plataforma',
  };
  function translateDetail(detail) {
    if (!detail || typeof detail !== 'string') return detail;
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

  // ===================================================================
  // PHASE 24 — "Momento de Aprendizado" scheduling (Coordenação).
  // Reuses /api/v1/coordination/study-sessions (TeachingContextService scope
  // authz, no parallel authorization). The plan itself is generated server-side
  // by StudySessionPlanner from the Domain Map + Adaptive Learning Path -
  // nothing is decided here. Zero IA.
  // ===================================================================
  const cs = { contents: [], breaks: [] };

  function csHeaders() {
    return { 'Content-Type': 'application/json', 'Authorization': `Bearer ${state.coordinatorId}` };
  }
  function csEsc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function csFmtMin(total) {
    const m = Math.max(0, Math.round(total || 0));
    if (m < 60) return `${m} min`;
    const h = Math.floor(m / 60), r = m % 60;
    return r ? `${h}h${String(r).padStart(2, '0')}` : `${h} h`;
  }
  function csMsg(text, ok) {
    const el = document.getElementById('cs-msg');
    if (!text) { el.hidden = true; el.textContent = ''; return; }
    el.hidden = false; el.textContent = text;
    el.style.color = ok ? '#1a7f37' : '#b3261e';
  }

  function csUpdateSummary() {
    const start = document.getElementById('cs-start').value;
    const end = document.getElementById('cs-end').value;
    const summary = document.getElementById('cs-summary');
    if (!start || !end) { summary.textContent = ''; return; }
    const [sh, sm] = start.split(':').map(Number);
    const [eh, em] = end.split(':').map(Number);
    const total = (eh * 60 + em) - (sh * 60 + sm);
    const breakMin = cs.breaks.reduce((a, b) => a + b.duration_minutes, 0);
    if (!(total > 0)) { summary.innerHTML = '<span class="cs-summary-bad">Horário inválido — o fim deve ser depois do início.</span>'; return; }
    summary.innerHTML = `Tempo total: <strong>${csFmtMin(total)}</strong> · `
      + `Intervalos: <strong>${csFmtMin(breakMin)}</strong> · `
      + `Tempo efetivo: <strong>${csFmtMin(Math.max(0, total - breakMin))}</strong>`;
  }

  function csRenderContents() {
    document.getElementById('cs-contents-list').innerHTML = cs.contents.length
      ? cs.contents.map((c, i) => `<span class="cs-chip">${csEsc(c)}
          <button type="button" class="cs-chip-remove" data-i="${i}" aria-label="Remover ${csEsc(c)}">×</button></span>`).join('')
      : '<span class="empty-text">Nenhum — a plataforma decide o conteúdo.</span>';
  }
  function csRenderBreaks() {
    document.getElementById('cs-breaks-list').innerHTML = cs.breaks.length
      ? cs.breaks.map((b, i) => `<span class="cs-chip">☕ ${b.duration_minutes} min
          <button type="button" class="cs-chip-remove" data-i="${i}" aria-label="Remover intervalo">×</button></span>`).join('')
      : '<span class="empty-text">Nenhum intervalo.</span>';
  }

  function initStudySessionsView() {
    cs.contents = []; cs.breaks = [];
    csRenderContents(); csRenderBreaks(); csUpdateSummary(); csMsg('');
    const dateInput = document.getElementById('cs-date');
    if (!dateInput.value) dateInput.value = new Date().toISOString().slice(0, 10);
    loadStudySessionsList();
  }

  async function loadStudySessionsList() {
    const date = document.getElementById('cs-date').value;
    const list = document.getElementById('cs-list');
    if (!date) { list.innerHTML = '<p class="empty-text">Selecione uma data.</p>'; return; }
    list.innerHTML = '<p class="empty-text">Carregando…</p>';
    try {
      const res = await fetch(
        `/api/v1/coordination/study-sessions?school_id=${state.schoolId}&session_date=${date}`,
        { headers: csHeaders() });
      if (!res.ok) { list.innerHTML = '<p class="empty-text">Não foi possível carregar os momentos programados.</p>'; return; }
      const data = await res.json();
      if (!data.sessions.length) { list.innerHTML = '<p class="empty-text">Nenhum momento programado para esta data.</p>'; return; }
      list.innerHTML = `<table class="cs-table">
        <thead><tr><th>Aluno</th><th>Status</th><th>Tempo efetivo</th><th>Blocos</th></tr></thead>
        <tbody>${data.sessions.map((s) => `<tr>
          <td>${csEsc(s.student_external_id)}</td><td>${csEsc(s.status)}</td>
          <td>${csFmtMin(s.effective_study_minutes)}</td><td>${s.blocks_done}/${s.blocks_total}</td>
        </tr>`).join('')}</tbody></table>`;
    } catch (err) {
      list.innerHTML = '<p class="empty-text">Falha de conexão.</p>';
    }
  }

  (function wireStudySessions() {
    const form = document.getElementById('cs-form');
    if (!form) return;
    document.getElementById('cs-target-type').addEventListener('change', (e) => {
      document.getElementById('cs-target-id-label').textContent =
        e.target.value === 'STUDENT' ? 'ID do aluno' : 'ID da turma';
    });
    document.getElementById('cs-start').addEventListener('input', csUpdateSummary);
    document.getElementById('cs-end').addEventListener('input', csUpdateSummary);
    document.getElementById('cs-add-content').addEventListener('click', () => {
      const input = document.getElementById('cs-content-input');
      const v = input.value.trim();
      if (v && !cs.contents.includes(v)) { cs.contents.push(v); csRenderContents(); }
      input.value = '';
    });
    document.getElementById('cs-contents-list').addEventListener('click', (e) => {
      const b = e.target.closest('.cs-chip-remove');
      if (!b) return;
      cs.contents.splice(Number(b.dataset.i), 1);
      csRenderContents();
    });
    document.getElementById('cs-add-break').addEventListener('click', () => {
      const input = document.getElementById('cs-break-input');
      const m = Number(input.value);
      if (m > 0) { cs.breaks.push({ duration_minutes: m }); csRenderBreaks(); csUpdateSummary(); }
    });
    document.getElementById('cs-breaks-list').addEventListener('click', (e) => {
      const b = e.target.closest('.cs-chip-remove');
      if (!b) return;
      cs.breaks.splice(Number(b.dataset.i), 1);
      csRenderBreaks(); csUpdateSummary();
    });
    document.getElementById('cs-date').addEventListener('change', loadStudySessionsList);
    document.getElementById('cs-refresh').addEventListener('click', loadStudySessionsList);

    form.addEventListener('submit', async (ev) => {
      ev.preventDefault();
      csMsg('');
      const date = document.getElementById('cs-date').value;
      const start = document.getElementById('cs-start').value;
      const end = document.getElementById('cs-end').value;
      const targetType = document.getElementById('cs-target-type').value;
      const targetId = document.getElementById('cs-target-id').value.trim();
      if (!date || !start || !end || !targetId) {
        csMsg('Preencha destinatário, data e horário.');
        return;
      }
      const payload = {
        school_id: state.schoolId, target_type: targetType, target_id: targetId,
        session_date: date, start_at: `${date}T${start}:00Z`, end_at: `${date}T${end}:00Z`,
        content_codes: cs.contents.length ? cs.contents : null,
        breaks: cs.breaks.length ? cs.breaks : null,
      };
      const btn = document.getElementById('cs-publish');
      btn.disabled = true;
      try {
        const res = await fetch('/api/v1/coordination/study-sessions', {
          method: 'POST', headers: csHeaders(), body: JSON.stringify(payload),
        });
        const data = await res.json().catch(() => ({}));
        btn.disabled = false;
        if (!res.ok) {
          const d = data.detail || {};
          csMsg((typeof d === 'string' ? translateDetail(d) : d.message) || 'Não foi possível publicar.');
          return;
        }
        csMsg(`Publicado para ${data.created_or_updated} aluno(s).`, true);
        loadStudySessionsList();
      } catch (err) {
        btn.disabled = false;
        csMsg('Falha de conexão ao publicar.');
      }
    });
  })();

  // Initial Load
  loadCoordinationDashboard();
});
