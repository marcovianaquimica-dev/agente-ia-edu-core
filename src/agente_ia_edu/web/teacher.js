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
    material: null,
    candidateDifficulty: '',
    modification: { item: null, type: '', proposal: null, busy: false },
  };

  // UI References
  const navItems = document.querySelectorAll('.nav-item');
  const viewPanels = document.querySelectorAll('.view-panel');
  const pageTitle = document.getElementById('page-title');
  const pageSubtitle = document.getElementById('page-subtitle');
  const alertBox = document.getElementById('alert-box');
  const sidebar = document.querySelector('.sidebar');
  const mobileMenuToggle = document.getElementById('teacher-mobile-menu-toggle');
  const mobileMenuClose = document.getElementById('teacher-mobile-menu-close');
  const sidebarBackdrop = document.getElementById('teacher-sidebar-backdrop');

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
      closeMobileMenu();
    });
  });

  function setMobileMenu(open) {
    sidebar.classList.toggle('is-open', open);
    sidebarBackdrop.classList.toggle('is-visible', open);
    mobileMenuToggle.setAttribute('aria-expanded', String(open));
    mobileMenuToggle.setAttribute('aria-label', open ? 'Fechar menu' : 'Abrir menu');
    sidebarBackdrop.setAttribute('aria-hidden', String(!open));
    document.body.classList.toggle('sidebar-open', open);
  }

  function closeMobileMenu() { setMobileMenu(false); }

  mobileMenuToggle.addEventListener('click', () => {
    setMobileMenu(!sidebar.classList.contains('is-open'));
  });
  mobileMenuClose.addEventListener('click', closeMobileMenu);
  sidebarBackdrop.addEventListener('click', closeMobileMenu);
  document.addEventListener('keydown', event => {
    if (event.key === 'Escape' && sidebar.classList.contains('is-open')) closeMobileMenu();
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
      'materials': { title: 'Listas e Avaliações', sub: 'Crie e revise listas de exercícios com questões existentes' },
      'theory-materials': { title: 'Meus Materiais', sub: 'Materiais teóricos autorais — estruturados, versionáveis e ligados ao currículo e às questões' },
      'material-ingestion': { title: 'Importar Material', sub: 'Extração e estruturação automática — revisão humana antes de publicar' },
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
    if (state.currentView === 'materials') loadMaterials();
    if (state.currentView === 'theory-materials') loadTheoryMaterials();
    if (state.currentView === 'material-ingestion') loadMaterialIngestions();
  }

  const materialBuilder = document.getElementById('material-builder');
  const materialList = document.getElementById('materials-list');
  const materialWorkspace = document.getElementById('material-workspace');

  function materialHeaders() {
    return { 'Content-Type': 'application/json', 'Authorization': `Bearer ${state.teacherId}` };
  }

  function updateMaterialTotal() {
    const quantity = Number(document.getElementById('material-quantity').value || 0);
    const distributed = ['easy', 'medium', 'hard'].reduce((total, difficulty) => total + Number(document.getElementById(`material-${difficulty}`).value || 0), 0);
    const total = document.getElementById('material-total');
    total.textContent = `Total solicitado: ${quantity} | Distribuído: ${distributed}`;
    total.classList.toggle('text-danger', quantity !== distributed || quantity <= 0);
    return quantity === distributed && quantity > 0;
  }

  async function loadMaterialContents() {
    await loadCurriculumLevel('material-discipline');
  }

  async function loadCurriculumLevel(selectId, parentId = '') {
    const select = document.getElementById(selectId);
    const response = await fetch(`/api/v1/catalog/nodes${parentId ? `?parent_id=${parentId}` : ''}`);
    const nodes = response.ok ? await response.json() : [];
    select.innerHTML = `<option value="">Selecione</option>${nodes.map(node => `<option value="${node.id}">${node.name}</option>`).join('')}`;
    select.disabled = false;
  }

  async function loadMaterials() {
    try {
      const response = await fetch('/api/v1/teacher/materials', { headers: materialHeaders() });
      if (!response.ok) throw new Error('Falha ao carregar listas');
      const materials = await response.json();
      materialList.innerHTML = materials.length ? materials.map(material => `
        <article class="material-row"><div><strong>${material.title}</strong><p>${material.items.length} questões · Rascunho</p></div><button class="btn btn-secondary" data-continue-material="${material.id}">Continuar</button></article>
      `).join('') : '<p class="empty-text">Nenhuma lista criada ainda.</p>';
      materialList.querySelectorAll('[data-continue-material]').forEach(button => button.onclick = () => openMaterial(button.dataset.continueMaterial));
    } catch (error) {
      materialList.innerHTML = '<p class="empty-text">Não foi possível carregar as listas.</p>';
    }
  }

  async function openMaterial(materialId) {
    const response = await fetch(`/api/v1/teacher/materials/${materialId}`, { headers: materialHeaders() });
    if (!response.ok) return showAlert('Não foi possível abrir esta lista.', 'danger');
    state.material = await response.json();
    document.getElementById('material-builder-title').textContent = state.material.title;
    document.getElementById('material-builder-status').textContent = 'Rascunho salvo';
    document.getElementById('material-config').hidden = true;
    materialBuilder.hidden = false;
    materialList.hidden = true;
    materialWorkspace.hidden = false;
    await loadMaterialCandidates();
    renderMaterialItems();
  }

  async function loadMaterialCandidates() {
    const query = state.candidateDifficulty ? `?difficulty=${state.candidateDifficulty}` : '';
    const response = await fetch(`/api/v1/teacher/materials/${state.material.id}/candidates${query}`, { headers: materialHeaders() });
    if (!response.ok) return showAlert('Não foi possível carregar as questões disponíveis.', 'danger');
    const data = await response.json();
    document.getElementById('material-availability').textContent = `Disponíveis: ${data.availability.EASY} fáceis, ${data.availability.MEDIUM} médias, ${data.availability.HARD} difíceis.`;
    const selected = new Set(state.material.items.map(item => item.id));
    document.getElementById('material-candidates').innerHTML = data.items.map(item => questionCard(item, selected.has(item.id) ? `<label class="selection-toggle"><input type="checkbox" data-remove-question="${item.id}" checked> Selecionada</label>` : `<label class="selection-toggle"><input type="checkbox" data-add-question="${item.id}"> Selecionar</label>`)).join('') || '<p class="empty-text">Não há questões elegíveis neste filtro.</p>';
    document.querySelectorAll('[data-add-question]').forEach(button => button.onclick = () => addMaterialItem(button.dataset.addQuestion));
    document.querySelectorAll('[data-remove-question]').forEach(button => button.onchange = () => removeMaterialItem(button.dataset.removeQuestion));
  }

  function questionCard(item, action) {
    return `<article class="question-card"><header><strong>Questão ${item.question_number || ''}</strong><span class="badge badge-primary">${item.difficulty}</span></header><p class="question-stem">${item.stem}</p><ol class="alternatives" type="A">${item.alternatives.map(option => `<li>${option.text}</li>`).join('')}</ol><footer><span>${item.content}${item.modified ? ' · Questão modificada' : ''}</span>${action}</footer></article>`;
  }

  function renderMaterialItems() {
    document.getElementById('material-item-count').textContent = `(${state.material.items.length})`;
    const planned = state.material.configuration.difficulty_distribution;
    const count = level => state.material.items.filter(item => item.difficulty === level).length;
    document.getElementById('material-selection-summary').textContent = `Objetivo: ${state.material.configuration.quantity} | Selecionadas: ${state.material.items.length} | Fáceis: ${count('EASY')}/${planned.EASY} | Médias: ${count('MEDIUM')}/${planned.MEDIUM} | Difíceis: ${count('HARD')}/${planned.HARD}`;
    document.getElementById('material-items').innerHTML = state.material.items.length ? state.material.items.map((item, index) => questionCard(item, `<div class="question-actions"><button class="btn btn-secondary" data-modify-item="${item.id}">Modificar questão</button><button class="icon-button" title="Mover para cima" data-up-item="${item.id}" ${index === 0 ? 'disabled' : ''}>↑</button><button class="icon-button" title="Mover para baixo" data-down-item="${item.id}" ${index === state.material.items.length - 1 ? 'disabled' : ''}>↓</button><button class="btn btn-secondary" data-remove-item="${item.id}">Remover</button></div>`)).join('') : '<p class="empty-text">Adicione questões para iniciar a revisão.</p>';
    document.querySelectorAll('[data-remove-item]').forEach(button => button.onclick = () => removeMaterialItem(button.dataset.removeItem));
    document.querySelectorAll('[data-up-item]').forEach(button => button.onclick = () => moveMaterialItem(button.dataset.upItem, -1));
    document.querySelectorAll('[data-down-item]').forEach(button => button.onclick = () => moveMaterialItem(button.dataset.downItem, 1));
    document.querySelectorAll('[data-modify-item]').forEach(button => button.onclick = () => openModification(button.dataset.modifyItem));
  }

  const modificationModal = document.getElementById('question-modification-modal');
  function setModificationView(view) {
    ['modification-request', 'modification-loading', 'modification-comparison'].forEach(id => document.getElementById(id).hidden = id !== view);
  }
  function closeModification() {
    modificationModal.hidden = true;
    state.modification = { item: null, type: '', proposal: null, busy: false };
  }
  function openModification(itemId) {
    state.modification = { item: state.material.items.find(item => item.id === itemId), type: '', proposal: null, busy: false };
    document.getElementById('modification-error').hidden = true;
    document.getElementById('custom-modification-field').hidden = true;
    document.getElementById('custom-modification-instruction').value = '';
    document.getElementById('btn-generate-proposal').disabled = true;
    document.querySelectorAll('#modification-options button').forEach(button => button.classList.remove('selected'));
    setModificationView('modification-request');
    modificationModal.hidden = false;
  }
  function proposalCard(proposal) {
    return `<article class="question-card"><header><strong>Questão</strong><span class="badge badge-primary">${proposal.difficulty}</span></header><p class="question-stem">${proposal.statement}</p><ol class="alternatives" type="A">${proposal.options.map(option => `<li>${option}</li>`).join('')}</ol></article>`;
  }
  async function requestProposal() {
    if (state.modification.busy || !state.modification.type) return;
    const instruction = document.getElementById('custom-modification-instruction').value.trim();
    if (state.modification.type === 'CUSTOM' && !instruction) return;
    state.modification.busy = true;
    setModificationView('modification-loading');
    try {
      const response = await fetch(`/api/v1/teacher/questions/${state.modification.item.question_version_id}/modification-proposals`, { method: 'POST', headers: materialHeaders(), body: JSON.stringify({ assessment_item_id: state.modification.item.id, modification_type: state.modification.type, instruction }) });
      if (!response.ok) throw new Error((await response.json()).detail || 'Não foi possível gerar a modificação. Tente novamente.');
      state.modification.proposal = await response.json();
      document.getElementById('modification-original').innerHTML = questionCard(state.modification.item, '');
      document.getElementById('modification-proposed').innerHTML = proposalCard(state.modification.proposal.proposal);
      setModificationView('modification-comparison');
    } catch (error) {
      document.getElementById('modification-error').textContent = error.message || 'Não foi possível gerar a modificação. Tente novamente.';
      document.getElementById('modification-error').hidden = false;
      setModificationView('modification-request');
    } finally { state.modification.busy = false; }
  }
  async function acceptProposal() {
    if (state.modification.busy) return;
    state.modification.busy = true;
    document.getElementById('btn-accept-proposal').disabled = true;
    try {
      const response = await fetch(`/api/v1/teacher/questions/modification-proposals/${state.modification.proposal.id}/accept`, { method: 'POST', headers: materialHeaders() });
      if (!response.ok) throw new Error('Não foi possível aplicar esta versão.');
      await openMaterial(state.material.id);
      closeModification();
    } catch (error) {
      document.getElementById('modification-apply-error').textContent = error.message;
      document.getElementById('modification-apply-error').hidden = false;
      document.getElementById('btn-accept-proposal').disabled = false;
    } finally { state.modification.busy = false; }
  }
  async function cancelProposal() {
    if (state.modification.proposal) await fetch(`/api/v1/teacher/questions/modification-proposals/${state.modification.proposal.id}/cancel`, { method: 'POST', headers: materialHeaders() });
    closeModification();
  }

  async function addMaterialItem(questionVersionId) {
    const response = await fetch(`/api/v1/teacher/materials/${state.material.id}/items`, { method: 'POST', headers: materialHeaders(), body: JSON.stringify({ question_version_id: questionVersionId }) });
    if (!response.ok) return showAlert((await response.json()).detail || 'Não foi possível adicionar a questão.', 'danger');
    state.material.items.push(await response.json());
    renderMaterialItems();
    loadMaterialCandidates();
  }

  async function removeMaterialItem(itemId) {
    const response = await fetch(`/api/v1/teacher/materials/${state.material.id}/items/${itemId}`, { method: 'DELETE', headers: materialHeaders() });
    if (!response.ok) return showAlert('Não foi possível remover a questão.', 'danger');
    state.material.items = state.material.items.filter(item => item.id !== itemId).map((item, index) => ({ ...item, question_number: index + 1 }));
    renderMaterialItems();
    loadMaterialCandidates();
  }

  async function moveMaterialItem(itemId, direction) {
    const index = state.material.items.findIndex(item => item.id === itemId);
    const target = index + direction;
    if (target < 0 || target >= state.material.items.length) return;
    [state.material.items[index], state.material.items[target]] = [state.material.items[target], state.material.items[index]];
    const response = await fetch(`/api/v1/teacher/materials/${state.material.id}/items/reorder`, { method: 'PATCH', headers: materialHeaders(), body: JSON.stringify({ item_ids: state.material.items.map(item => item.id) }) });
    if (!response.ok) return openMaterial(state.material.id);
    state.material = await response.json();
    renderMaterialItems();
  }

  document.getElementById('btn-new-material').onclick = async () => {
    state.material = null;
    materialBuilder.hidden = false;
    materialList.hidden = true;
    materialWorkspace.hidden = true;
    document.getElementById('material-config').hidden = false;
    document.getElementById('material-builder-title').textContent = 'Nova Lista';
    await loadMaterialContents();
    updateMaterialTotal();
  };
  document.getElementById('btn-close-material').onclick = () => { materialBuilder.hidden = true; materialList.hidden = false; loadMaterials(); };
  document.getElementById('btn-close-modification').onclick = cancelProposal;
  document.getElementById('btn-cancel-modification').onclick = closeModification;
  document.getElementById('btn-cancel-proposal').onclick = cancelProposal;
  document.getElementById('btn-accept-proposal').onclick = acceptProposal;
  document.getElementById('btn-refine-proposal').onclick = cancelProposal;
  document.querySelectorAll('#modification-options button').forEach(button => button.onclick = () => {
    state.modification.type = button.dataset.modification;
    document.querySelectorAll('#modification-options button').forEach(option => option.classList.toggle('selected', option === button));
    document.getElementById('custom-modification-field').hidden = state.modification.type !== 'CUSTOM';
    document.getElementById('btn-generate-proposal').disabled = false;
  });
  document.getElementById('btn-generate-proposal').onclick = requestProposal;
  [['material-discipline', 'material-area'], ['material-area', 'material-content'], ['material-content', 'material-subcontent']].forEach(([parent, child]) => document.getElementById(parent).onchange = async event => {
    const next = document.getElementById(child);
    next.disabled = !event.target.value;
    next.innerHTML = '<option value="">Selecione</option>';
    if (event.target.value) await loadCurriculumLevel(child, event.target.value);
  });
  document.querySelectorAll('#material-difficulty-filters [data-difficulty]').forEach(button => button.onclick = () => { state.candidateDifficulty = button.dataset.difficulty; loadMaterialCandidates(); });
  ['material-quantity', 'material-easy', 'material-medium', 'material-hard'].forEach(id => document.getElementById(id).oninput = updateMaterialTotal);
  document.getElementById('btn-create-material').onclick = async () => {
    if (!updateMaterialTotal()) return showAlert('A distribuição deve corresponder ao total solicitado.', 'danger');
    const response = await fetch('/api/v1/teacher/materials', { method: 'POST', headers: materialHeaders(), body: JSON.stringify({
      title: document.getElementById('material-title').value,
      content_node_id: document.getElementById('material-subcontent').value || document.getElementById('material-content').value,
      quantity: Number(document.getElementById('material-quantity').value),
      difficulty_distribution: { EASY: Number(document.getElementById('material-easy').value), MEDIUM: Number(document.getElementById('material-medium').value), HARD: Number(document.getElementById('material-hard').value) },
    }) });
    if (!response.ok) return showAlert((await response.json()).detail || 'Não foi possível criar o rascunho.', 'danger');
    state.material = await response.json();
    document.getElementById('material-config').hidden = true;
    materialWorkspace.hidden = false;
    document.getElementById('material-builder-title').textContent = state.material.title;
    document.getElementById('material-builder-status').textContent = 'Rascunho salvo';
    await loadMaterialCandidates();
    renderMaterialItems();
  };

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

  // ===================================================================
  // PHASE 23 — "Meus Materiais" (materiais teóricos autorais).
  // Reuses /api/v1/catalog/materials* and /api/v1/question-bank/questions.
  // Never duplicates a question — links by question_version_id. Zero IA.
  // ===================================================================
  const tm = { materials: [], current: null, disciplineTree: {} };

  function tmHeaders() {
    return { 'Content-Type': 'application/json', 'Authorization': `Bearer ${state.teacherId}` };
  }
  function tmEsc(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }
  function tmMsg(id, text) {
    const el = document.getElementById(id);
    if (!el) return;
    if (!text) { el.hidden = true; el.textContent = ''; return; }
    el.textContent = text; el.hidden = false;
  }

  async function tmLoadDisciplines() {
    const sel = document.getElementById('tm-discipline');
    if (!sel || sel.dataset.loaded) return;
    try {
      const res = await fetch('/api/v1/catalog/disciplines', { headers: tmHeaders() });
      const rows = res.ok ? await res.json() : [];
      sel.innerHTML = '<option value="">Selecione</option>'
        + rows.map((d) => `<option value="${tmEsc(d.id)}">${tmEsc(d.name)}</option>`).join('');
      sel.dataset.loaded = '1';
    } catch (e) { sel.innerHTML = '<option value="">Selecione</option>'; }
  }

  async function tmLoadContents(disciplineId) {
    const sel = document.getElementById('tm-content');
    sel.innerHTML = '<option value="">Selecione</option>';
    sel.disabled = true;
    if (!disciplineId) return;
    try {
      const res = await fetch(`/api/v1/catalog/nodes/${disciplineId}/tree`, { headers: tmHeaders() });
      const data = res.ok ? await res.json() : { nodes: [] };
      const contents = (data.nodes || []).filter(
        (n) => n.active && n.code && (n.node_type === 'CONTENT' || n.node_type === 'SUBCONTENT'));
      sel.innerHTML = '<option value="">Selecione</option>'
        + contents.map((n) => `<option value="${tmEsc(n.id)}" data-code="${tmEsc(n.code)}">${tmEsc(n.name)}</option>`).join('');
      sel.disabled = contents.length === 0;
    } catch (e) { /* leave empty */ }
  }

  async function loadTheoryMaterials() {
    await tmLoadDisciplines();
    const list = document.getElementById('tm-list');
    list.innerHTML = '<p class="empty-text">Carregando…</p>';
    document.getElementById('tm-detail').hidden = true;
    document.getElementById('tm-new-form').hidden = true;
    try {
      const res = await fetch('/api/v1/catalog/materials', { headers: tmHeaders() });
      if (!res.ok) { list.innerHTML = '<p class="empty-text">Não foi possível carregar seus materiais.</p>'; return; }
      tm.materials = await res.json();
      if (!tm.materials.length) {
        list.innerHTML = '<p class="empty-text">Nenhum material ainda. Crie o primeiro em “+ Novo material”.</p>';
        return;
      }
      list.innerHTML = `
        <table class="tm-table">
          <thead><tr><th>Título</th><th>Status</th><th>Versão</th><th>Disciplina / Conteúdo</th><th>Seções</th><th>Questões</th></tr></thead>
          <tbody>${tm.materials.map((m) => `
            <tr class="tm-row" data-id="${tmEsc(m.id)}">
              <td>${tmEsc(m.title)}</td>
              <td><span class="tm-badge">${tmEsc(m.latest_version_status || 'DRAFT')}</span></td>
              <td>v${m.latest_version_number || 1}</td>
              <td>${tmEsc(m.primary_content_code || (m.curriculum_status === 'UNMAPPED' ? 'Sem conteúdo' : '—'))}</td>
              <td>${m.section_count}</td>
              <td>${m.question_count}</td>
            </tr>`).join('')}</tbody>
        </table>`;
      list.querySelectorAll('.tm-row').forEach((row) => {
        row.addEventListener('click', () => openTheoryMaterial(row.dataset.id));
      });
    } catch (e) {
      list.innerHTML = '<p class="empty-text">Não foi possível carregar seus materiais.</p>';
    }
  }

  async function createTheoryMaterial(ev) {
    ev.preventDefault();
    tmMsg('tm-form-msg', '');
    const contentSel = document.getElementById('tm-content');
    const payload = {
      title: document.getElementById('tm-title').value.trim(),
      description: document.getElementById('tm-description').value.trim() || null,
      material_kind: document.getElementById('tm-kind').value,
      authoring_source: document.getElementById('tm-source').value,
      visibility_scope: document.getElementById('tm-visibility').value,
      primary_content_node_id: contentSel.value || null,
    };
    if (!payload.title) { tmMsg('tm-form-msg', 'Informe um título.'); return; }
    const btn = document.getElementById('tm-save-btn');
    btn.disabled = true;
    try {
      const res = await fetch('/api/v1/catalog/materials', {
        method: 'POST', headers: tmHeaders(), body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        tmMsg('tm-form-msg', (data && data.detail) ? String(data.detail) : 'Não foi possível salvar o material.');
        btn.disabled = false;
        return;
      }
      document.getElementById('tm-new-form').reset();
      document.getElementById('tm-new-form').hidden = true;
      btn.disabled = false;
      await loadTheoryMaterials();
      openTheoryMaterial(data.id);
    } catch (e) {
      tmMsg('tm-form-msg', 'Sem conexão para salvar o material.');
      btn.disabled = false;
    }
  }

  async function openTheoryMaterial(id) {
    const detail = document.getElementById('tm-detail');
    document.getElementById('tm-list').innerHTML = '';
    document.getElementById('tm-new-form').hidden = true;
    detail.hidden = false;
    tmMsg('tm-detail-msg', '');
    document.getElementById('tm-q-candidates').innerHTML = '';
    try {
      const [dRes, sRes, qRes] = await Promise.all([
        fetch(`/api/v1/catalog/materials/${id}`, { headers: tmHeaders() }),
        fetch(`/api/v1/catalog/materials/${id}/sections`, { headers: tmHeaders() }),
        fetch(`/api/v1/catalog/materials/${id}/questions`, { headers: tmHeaders() }),
      ]);
      const d = dRes.ok ? await dRes.json() : null;
      if (!d) { tmMsg('tm-detail-msg', 'Não foi possível abrir o material.'); return; }
      tm.current = d.material;
      const m = d.material;
      document.getElementById('tm-detail-title').textContent = m.title;
      document.getElementById('tm-detail-meta').textContent =
        `${m.material_kind || '—'} · origem ${m.authoring_source || '—'} · ${m.visibility_scope} · `
        + `${m.latest_version_status || 'DRAFT'} v${m.latest_version_number || 1} · `
        + `${m.section_count} seções · ${m.block_count} blocos · ${m.question_count} questões`;
      const sections = sRes.ok ? await sRes.json() : [];
      const secBox = document.getElementById('tm-sections');
      secBox.innerHTML = sections.length
        ? sections.map((s) => `<div class="tm-section-row"><strong>${tmEsc(s.title || s.section_type)}</strong>`
            + ` <span class="empty-text">${tmEsc(s.content_code || 'sem conteúdo')}`
            + `${s.curriculum_relation_type ? ' · ' + tmEsc(s.curriculum_relation_type) : ''} · ${s.block_count} blocos</span></div>`).join('')
        : '<p class="empty-text">Nenhuma seção. As seções carregam a associação ao currículo (content_code).</p>';
      const questions = qRes.ok ? await qRes.json() : [];
      renderTheoryQuestions(id, questions);
    } catch (e) {
      tmMsg('tm-detail-msg', 'Não foi possível abrir o material.');
    }
  }

  function renderTheoryQuestions(materialId, questions) {
    document.getElementById('tm-question-count').textContent = `(${questions.length})`;
    const box = document.getElementById('tm-questions');
    box.innerHTML = questions.length
      ? questions.map((q) => `<div class="tm-question-row" data-qv="${tmEsc(q.question_version_id)}">
          <span>${q.official_number ? 'Q' + q.official_number : 'questão'} · ${tmEsc(q.relation_type || 'EXERCISE')}</span>
          <button class="btn btn-secondary tm-q-remove" type="button">Remover</button></div>`).join('')
      : '<p class="empty-text">Nenhuma questão vinculada. Busque no banco e adicione — a questão não é copiada.</p>';
    box.querySelectorAll('.tm-q-remove').forEach((b) => {
      b.addEventListener('click', async () => {
        const qv = b.closest('.tm-question-row').dataset.qv;
        b.disabled = true;
        const res = await fetch(`/api/v1/catalog/materials/${materialId}/questions/${qv}`, {
          method: 'DELETE', headers: tmHeaders(),
        });
        if (res.status === 204) openTheoryMaterial(materialId);
        else { tmMsg('tm-detail-msg', 'Não foi possível remover a questão.'); b.disabled = false; }
      });
    });
  }

  async function searchTheoryCandidates(materialId) {
    const code = document.getElementById('tm-q-search').value.trim();
    const box = document.getElementById('tm-q-candidates');
    tmMsg('tm-detail-msg', '');
    box.innerHTML = '<p class="empty-text">Buscando…</p>';
    try {
      const qs = code ? `?content=${encodeURIComponent(code)}&page_size=10` : '?page_size=10';
      const res = await fetch(`/api/v1/question-bank/questions${qs}`, { headers: tmHeaders() });
      const data = res.ok ? await res.json() : { items: [] };
      const items = data.items || data.questions || [];
      if (!items.length) { box.innerHTML = '<p class="empty-text">Nenhuma questão encontrada para esse content_code.</p>'; return; }
      box.innerHTML = items.map((it) => {
        const qv = it.question_version_id || it.id;
        const num = it.official_number ? 'Q' + it.official_number : 'questão';
        return `<div class="tm-cand-row" data-qv="${tmEsc(qv)}">
          <span>${num} · ${tmEsc(it.content_code || it.content || '')}</span>
          <button class="btn btn-secondary tm-cand-add" type="button">Adicionar</button></div>`;
      }).join('');
      box.querySelectorAll('.tm-cand-add').forEach((b) => {
        b.addEventListener('click', async () => {
          const qv = b.closest('.tm-cand-row').dataset.qv;
          b.disabled = true;
          const res = await fetch(`/api/v1/catalog/materials/${materialId}/questions`, {
            method: 'POST', headers: tmHeaders(),
            body: JSON.stringify({ question_version_id: qv, relation_type: 'EXERCISE' }),
          });
          if (res.status === 201) { openTheoryMaterial(materialId); }
          else if (res.status === 409) { tmMsg('tm-detail-msg', 'Essa questão já está vinculada a este material.'); b.disabled = false; }
          else { tmMsg('tm-detail-msg', 'Não foi possível adicionar a questão.'); b.disabled = false; }
        });
      });
    } catch (e) {
      box.innerHTML = '<p class="empty-text">Falha na busca de questões.</p>';
    }
  }

  (function wireTheoryMaterials() {
    const newBtn = document.getElementById('tm-new-btn');
    if (!newBtn) return;
    newBtn.addEventListener('click', () => {
      const f = document.getElementById('tm-new-form');
      f.hidden = !f.hidden;
      document.getElementById('tm-detail').hidden = true;
    });
    document.getElementById('tm-cancel-btn').addEventListener('click', () => {
      document.getElementById('tm-new-form').hidden = true;
    });
    document.getElementById('tm-new-form').addEventListener('submit', createTheoryMaterial);
    document.getElementById('tm-discipline').addEventListener('change', (e) => tmLoadContents(e.target.value));
    document.getElementById('tm-detail-back').addEventListener('click', () => {
      document.getElementById('tm-detail').hidden = true;
      loadTheoryMaterials();
    });
    document.getElementById('tm-q-search-btn').addEventListener('click', () => {
      if (tm.current) searchTheoryCandidates(tm.current.id);
    });
  })();

  // ===================================================================
  // PHASE 26 — "Importar Material" (Authorial Material Ingestion Engine).
  // ARQUIVO -> EXTRAÇÃO -> ESTRUTURA -> CURRICULUM-V2 -> REVISÃO -> APROVAÇÃO
  // -> PUBLICAÇÃO. INGESTÃO != PUBLICAÇÃO: nada fica visível ao aluno até
  // aprovar + publicar. Publicação reutiliza o MESMO TheoryMaterialService da
  // PHASE 23 - o aluno usa o MESMO Material Player da PHASE 25, sem mudança.
  // Zero IA nesta fase.
  // ===================================================================
  const mi = { reviews: [], current: null };

  const MI_STATUS_LABEL = {
    PENDING_REVIEW: 'Pronto para revisão', NEEDS_REVIEW: 'Precisa de revisão',
    APPROVED: 'Aprovado', PUBLISHED: 'Publicado', REJECTED: 'Rejeitado',
  };

  async function loadMaterialIngestions() {
    const list = document.getElementById('mi-list');
    document.getElementById('mi-detail').hidden = true;
    list.innerHTML = '<p class="empty-text">Carregando…</p>';
    try {
      const res = await fetch('/api/v1/catalog/ingestion', { headers: tmHeaders() });
      if (!res.ok) { list.innerHTML = '<p class="empty-text">Não foi possível carregar as ingestões.</p>'; return; }
      mi.reviews = await res.json();
      if (!mi.reviews.length) {
        list.innerHTML = '<p class="empty-text">Nenhum material importado ainda. Envie um arquivo acima.</p>';
        return;
      }
      list.innerHTML = `
        <table class="tm-table">
          <thead><tr><th>Status</th><th>Classificação</th><th>Exercícios</th><th>Atualizado</th></tr></thead>
          <tbody>${mi.reviews.map((r) => `
            <tr class="tm-row" data-id="${tmEsc(r.id)}">
              <td><span class="tm-badge">${tmEsc(MI_STATUS_LABEL[r.review_status] || r.review_status)}</span></td>
              <td>${r.classification_state === 'MAPPED' ? tmEsc(r.content_code) : '⚠ TAXONOMY_GAP'}</td>
              <td>${r.exercises_detected}</td>
              <td>${new Date(r.updated_at).toLocaleString('pt-BR')}</td>
            </tr>`).join('')}</tbody>
        </table>`;
      list.querySelectorAll('.tm-row').forEach((row) => {
        row.addEventListener('click', () => openMaterialIngestion(row.dataset.id));
      });
    } catch (e) {
      list.innerHTML = '<p class="empty-text">Não foi possível carregar as ingestões.</p>';
    }
  }

  async function uploadMaterialIngestion(ev) {
    ev.preventDefault();
    tmMsg('mi-upload-msg', '');
    const input = document.getElementById('mi-file');
    if (!input.files || !input.files[0]) return;
    const btn = document.getElementById('mi-upload-btn');
    btn.disabled = true;
    const form = new FormData();
    form.append('file', input.files[0]);
    try {
      const res = await fetch('/api/v1/catalog/ingestion/upload', {
        method: 'POST', headers: { 'Authorization': `Bearer ${state.teacherId}` }, body: form,
      });
      const data = await res.json().catch(() => ({}));
      btn.disabled = false;
      if (!res.ok) {
        const d = data && data.detail;
        tmMsg('mi-upload-msg', (d && d.message) ? d.message : (typeof d === 'string' ? d : 'Não foi possível importar o arquivo.'));
        return;
      }
      document.getElementById('mi-upload-form').reset();
      await loadMaterialIngestions();
      if (data.review && data.review.id) openMaterialIngestion(data.review.id);
    } catch (e) {
      btn.disabled = false;
      tmMsg('mi-upload-msg', 'Sem conexão para importar o arquivo.');
    }
  }

  async function openMaterialIngestion(id) {
    document.getElementById('mi-detail').hidden = false;
    tmMsg('mi-action-msg', '');
    tmMsg('mi-classification-msg', '');
    try {
      const res = await fetch(`/api/v1/catalog/ingestion/${id}`, { headers: tmHeaders() });
      if (!res.ok) { tmMsg('mi-action-msg', 'Não foi possível abrir esta ingestão.'); return; }
      const d = await res.json();
      mi.current = d;
      const r = d.review;
      document.getElementById('mi-detail-title').textContent = d.document.title || d.document.filename;
      document.getElementById('mi-detail-meta').textContent =
        `${d.document.filename} · ${d.document.document_type} · ${(d.document.file_size_bytes / 1024).toFixed(0)} KB · `
        + `hash ${d.document.document_hash.slice(0, 12)}… · ${MI_STATUS_LABEL[r.review_status] || r.review_status}`;
      document.getElementById('mi-classification-state').textContent =
        r.classification_state === 'MAPPED'
          ? `Classificação sugerida automaticamente (confiança ${(r.classification_confidence * 100).toFixed(0)}%). Revise antes de aprovar.`
          : '⚠ TAXONOMY_GAP — nenhum conteúdo do currículo-v2 correspondeu com confiança suficiente. Preencha manualmente.';
      document.getElementById('mi-discipline').value = r.discipline_code || '';
      document.getElementById('mi-area').value = r.area_code || '';
      document.getElementById('mi-content').value = r.content_code || '';
      document.getElementById('mi-subcontents').value = (r.subcontent_codes || []).join(', ');
      document.getElementById('mi-notes').value = r.notes || '';
      tmMsg('mi-structure-issues', (r.structure_issues || []).join(' '));
      document.getElementById('mi-sections').innerHTML = (d.sections || []).map((s) =>
        `<div class="tm-section-row"><strong>${s.position}. ${tmEsc(s.title || s.section_type)}</strong> <span class="empty-text">(${tmEsc(s.section_type)})</span></div>`
      ).join('') || '<p class="empty-text">Nenhuma seção detectada.</p>';
      document.getElementById('mi-exercise-count').textContent = `(${(d.exercises || []).length})`;
      document.getElementById('mi-exercises').innerHTML = (d.exercises || []).map((q) =>
        `<div class="tm-question-row"><strong>${q.question_number}.</strong> ${tmEsc(q.statement_preview)}${q.requires_review ? ' <span class="tm-badge">revisar</span>' : ''}</div>`
      ).join('') || '<p class="empty-text">Nenhum exercício detectado.</p>';

      const approveBtn = document.getElementById('mi-approve-btn');
      const publishBtn = document.getElementById('mi-publish-btn');
      approveBtn.disabled = !(r.review_status === 'PENDING_REVIEW' || r.review_status === 'NEEDS_REVIEW');
      publishBtn.disabled = r.review_status !== 'APPROVED';
    } catch (e) {
      tmMsg('mi-action-msg', 'Sem conexão para abrir esta ingestão.');
    }
  }

  async function saveMaterialClassification() {
    if (!mi.current) return;
    tmMsg('mi-classification-msg', '');
    const payload = {
      discipline_code: document.getElementById('mi-discipline').value.trim() || null,
      area_code: document.getElementById('mi-area').value.trim() || null,
      content_code: document.getElementById('mi-content').value.trim() || null,
      subcontent_codes: document.getElementById('mi-subcontents').value.split(',').map((s) => s.trim()).filter(Boolean),
      notes: document.getElementById('mi-notes').value.trim() || null,
    };
    try {
      const res = await fetch(`/api/v1/catalog/ingestion/${mi.current.review.id}/classification`, {
        method: 'PATCH', headers: tmHeaders(), body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { tmMsg('mi-classification-msg', 'Não foi possível salvar a classificação.'); return; }
      tmMsg('mi-classification-msg', 'Classificação salva.');
      openMaterialIngestion(mi.current.review.id);
    } catch (e) { tmMsg('mi-classification-msg', 'Sem conexão.'); }
  }

  async function actOnMaterialIngestion(action) {
    if (!mi.current) return;
    tmMsg('mi-action-msg', '');
    try {
      const res = await fetch(`/api/v1/catalog/ingestion/${mi.current.review.id}/${action}`, {
        method: 'POST', headers: tmHeaders(), body: action === 'reject' ? JSON.stringify({}) : undefined,
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const d = data && data.detail;
        tmMsg('mi-action-msg', (d && d.message) ? d.message : 'Não foi possível concluir a ação.');
        return;
      }
      tmMsg('mi-action-msg', action === 'publish' ? 'Material publicado! Já está disponível para os alunos no escopo.' : 'Ação concluída.');
      openMaterialIngestion(mi.current.review.id);
    } catch (e) { tmMsg('mi-action-msg', 'Sem conexão.'); }
  }

  (function wireMaterialIngestion() {
    const form = document.getElementById('mi-upload-form');
    if (form) form.addEventListener('submit', uploadMaterialIngestion);
    const back = document.getElementById('mi-detail-back');
    if (back) back.addEventListener('click', () => { document.getElementById('mi-detail').hidden = true; loadMaterialIngestions(); });
    const saveBtn = document.getElementById('mi-save-classification');
    if (saveBtn) saveBtn.addEventListener('click', saveMaterialClassification);
    const approveBtn = document.getElementById('mi-approve-btn');
    if (approveBtn) approveBtn.addEventListener('click', () => actOnMaterialIngestion('approve'));
    const publishBtn = document.getElementById('mi-publish-btn');
    if (publishBtn) publishBtn.addEventListener('click', () => actOnMaterialIngestion('publish'));
    const rejectBtn = document.getElementById('mi-reject-btn');
    if (rejectBtn) rejectBtn.addEventListener('click', () => actOnMaterialIngestion('reject'));
  })();

  // ===================================================================
  // PHASE 27/28/29 — Question Extraction Engine (robust, per-question
  // review). ARQUIVO -> ESTRUTURA -> LIMITES DE QUESTÃO -> CLASSIFICAÇÃO ->
  // RECONSTRUÇÃO -> ASSOCIAÇÃO DE IMAGENS -> VALIDAÇÃO -> REVIEW_REQUIRED
  // -> PROFESSOR REVIEW -> APPROVED -> QUESTION BANK. Nunca aprova ou
  // publica automaticamente. Zero IA.
  // ===================================================================
  const qe = { questions: [], lastRunId: null };

  const QE_STATUS_LABEL = {
    DISCOVERED: 'Descoberta', EXTRACTED: 'Extraída', VALIDATED: 'Validada',
    REVIEW_REQUIRED: '⚠ Revisão necessária', APPROVED: 'Aprovada',
    PUBLISHED: 'Publicada', REJECTED: 'Rejeitada',
  };

  // PHASE 28 — structured review-required reason codes (spec s12), shown
  // in the side-by-side review panel instead of a bare "REVIEW_REQUIRED".
  const QE_REASON_LABEL = {
    COLUMN_AMBIGUITY: 'Ambiguidade de colunas',
    BROKEN_READING_ORDER: 'Ordem de leitura quebrada',
    MISSING_OPTION: 'Alternativa ausente',
    ORPHAN_TEXT: 'Texto órfão (conteúdo de outra questão)',
    UNASSIGNED_ASSET: 'Imagem/gráfico não associado',
    FORMULA_AMBIGUITY: 'Ambiguidade em fórmula',
    CROSS_PAGE_AMBIGUITY: 'Ambiguidade entre páginas',
    LOW_CONFIDENCE: 'Confiança baixa',
    SEQUENCE_ANOMALY: 'Anomalia na sequência de questões',
  };

  async function runQuestionExtraction() {
    if (!mi.current) return;
    tmMsg('qe-run-msg', '');
    const docId = mi.current.document.id;
    const expected = document.getElementById('qe-expected-count').value;
    const btn = document.getElementById('qe-run-btn');
    btn.disabled = true;
    try {
      const res = await fetch(`/api/v1/catalog/question-extraction/${docId}/run`, {
        method: 'POST', headers: tmHeaders(),
        body: JSON.stringify({ expected_question_count: expected ? Number(expected) : null }),
      });
      const data = await res.json().catch(() => ({}));
      btn.disabled = false;
      if (!res.ok) {
        const d = data && data.detail;
        tmMsg('qe-run-msg', (d && d.message) ? d.message : 'Não foi possível extrair as questões.');
        return;
      }
      qe.lastRunId = data.run.id;
      renderQuestionExtraction(data.run, data.questions);
      loadReviewQueue();
    } catch (e) {
      btn.disabled = false;
      tmMsg('qe-run-msg', 'Sem conexão para extrair questões.');
    }
  }

  function renderQuestionExtraction(run, questions) {
    qe.questions = questions;
    document.getElementById('qe-summary').innerHTML =
      `Detectadas: <strong>${run.detected_question_count}</strong>`
      + (run.expected_question_count ? ` de ${run.expected_question_count} esperadas` : '')
      + ` · Validadas: ${run.validated_question_count} · Precisam de revisão: ${run.review_required_count}`
      + (run.missing_numbers && run.missing_numbers.length ? ` · <span class="text-danger">Faltando: ${run.missing_numbers.join(', ')}</span>` : '');
    document.getElementById('qe-questions').innerHTML = questions.map((q) => `
      <div class="tm-question-row" data-qid="${tmEsc(q.id)}">
        <div>
          <strong>Questão ${q.question_number}</strong>
          <span class="tm-badge">${tmEsc(QE_STATUS_LABEL[q.review_status] || q.review_status)}</span>
          <span class="empty-text">· ${tmEsc(q.question_type)} · confiança ${(q.extraction_confidence * 100).toFixed(0)}%${q.cross_page ? ' · atravessa páginas' : ''}</span>
          <p>${tmEsc(q.normalized_text.slice(0, 220))}${q.normalized_text.length > 220 ? '…' : ''}</p>
          ${(q.flags || []).length ? `<p class="empty-text">flags: ${q.flags.map(tmEsc).join(', ')}</p>` : ''}
        </div>
        <div class="tm-form-actions">
          <button class="btn btn-link" type="button" data-qe-action="review" data-qid="${tmEsc(q.id)}">Revisar</button>
        </div>
      </div>`).join('') || '<p class="empty-text">Nenhuma questão detectada.</p>';
  }

  (function wireQuestionExtraction() {
    const runBtn = document.getElementById('qe-run-btn');
    if (runBtn) runBtn.addEventListener('click', runQuestionExtraction);
    const list = document.getElementById('qe-questions');
    if (list) list.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-qe-action]');
      if (!btn) return;
      if (btn.dataset.qeAction === 'review') openReviewQuestion(btn.dataset.qid);
    });
  })();

  // ===================================================================
  // PHASE 29 — Fila de Revisão, Tela de Revisão, Publicação. O professor
  // resolve aqui somente o que a engine não conseguiu resolver sozinha
  // (spec s1/s2). Nunca aprova/publica automaticamente; a fila é
  // determinística (prioridade calculada no backend, zero IA).
  // ===================================================================
  const rq = { items: [], progress: {}, filters: { review_status: '', reason: '' } };
  const rv = {
    question: null, options: [], currentPage: null, queueIds: [], queueIndex: -1,
  };

  const RQ_PRIORITY_LABEL = { P1: 'P1 — rápida', P2: 'P2', P3: 'P3', P4: 'P4 — complexa' };

  async function loadReviewQueue() {
    const params = new URLSearchParams();
    if (rq.filters.review_status) params.set('review_status', rq.filters.review_status);
    if (rq.filters.reason) params.set('reason', rq.filters.reason);
    try {
      const res = await fetch(`/api/v1/catalog/question-extraction/review-queue?${params.toString()}`, {
        headers: tmHeaders(),
      });
      if (!res.ok) return;
      const data = await res.json();
      rq.items = data.items || [];
      rq.progress = data.progress || {};
      renderReviewQueue();
    } catch (e) { /* keep last known queue rendered */ }
  }

  function renderReviewQueue() {
    const total = rq.progress.total || 0;
    const done = (rq.progress.approved || 0) + (rq.progress.rejected || 0);
    const pct = rq.progress.percent_complete || 0;
    document.getElementById('rq-progress-fill').style.width = `${pct}%`;
    document.getElementById('rq-progress-label').textContent =
      total ? `Revisão do material — ${pct}%` : 'Nenhuma extração executada ainda.';
    document.getElementById('rq-progress-counts').textContent = total
      ? `${rq.progress.approved || 0} aprovadas · ${rq.progress.rejected || 0} rejeitadas · ${rq.progress.pending || 0} restantes`
      : '';

    const body = document.getElementById('rq-table-body');
    const empty = document.getElementById('rq-empty');
    if (!rq.items.length) {
      body.innerHTML = '';
      empty.hidden = false;
      return;
    }
    empty.hidden = true;
    rq.queueIds = rq.items.map((it) => it.id);
    body.innerHTML = rq.items.map((it) => `
      <tr data-qid="${tmEsc(it.id)}">
        <td>${it.question_number}</td>
        <td>${tmEsc(it.document)}</td>
        <td>${it.source_page_start ?? '?'}</td>
        <td>${(it.extraction_confidence * 100).toFixed(0)}%</td>
        <td><span class="rq-priority rq-priority-${it.priority}">${it.priority}</span></td>
        <td>${(it.review_reasons || []).map((r) => tmEsc(QE_REASON_LABEL[r] || r)).join(', ')}</td>
        <td><button class="btn btn-link" type="button" data-rq-review="${tmEsc(it.id)}">Revisar</button></td>
      </tr>`).join('');
  }

  (function wireReviewQueue() {
    const statusSel = document.getElementById('rq-filter-status');
    const reasonSel = document.getElementById('rq-filter-reason');
    if (statusSel) statusSel.addEventListener('change', () => {
      rq.filters.review_status = statusSel.value; loadReviewQueue();
    });
    if (reasonSel) reasonSel.addEventListener('change', () => {
      rq.filters.reason = reasonSel.value; loadReviewQueue();
    });
    const refreshBtn = document.getElementById('rq-refresh-btn');
    if (refreshBtn) refreshBtn.addEventListener('click', loadReviewQueue);
    const body = document.getElementById('rq-table-body');
    if (body) body.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-rq-review]');
      if (btn) openReviewQuestion(btn.dataset.rqReview);
    });
  })();

  // -- Tela de Revisão (side-by-side) ------------------------------------
  async function openReviewQuestion(questionId) {
    tmMsg('rv-msg', ''); tmMsg('rv-validation-msg', '');
    const idx = rq.queueIds.indexOf(questionId);
    rv.queueIndex = idx;
    try {
      const res = await fetch(`/api/v1/catalog/question-extraction/questions/${questionId}`, { headers: tmHeaders() });
      if (!res.ok) { tmMsg('rv-msg', 'Não foi possível abrir a questão.'); return; }
      let q = await res.json();
      if (q.review_status === 'REVIEW_REQUIRED') {
        const sres = await fetch(`/api/v1/catalog/question-extraction/questions/${questionId}/start-review`, {
          method: 'POST', headers: tmHeaders(),
        });
        if (sres.ok) q = await sres.json();
      }
      renderReviewPanel(q);
      loadCandidateAssets(questionId);
    } catch (e) { tmMsg('rv-msg', 'Sem conexão para abrir a questão.'); }
  }

  function renderReviewPanel(q) {
    rv.question = q;
    rv.options = (q.options || []).map((o) => ({ label: o.label, text: o.text }));
    rv.currentPage = q.source_page_start || 1;
    document.getElementById('rv-panel').hidden = false;
    document.getElementById('rv-question-number').textContent = q.question_number;
    document.getElementById('rv-meta').textContent =
      `Status: ${QE_STATUS_LABEL[q.review_status] || q.review_status} · confiança ${(q.extraction_confidence * 100).toFixed(0)}%`
      + (q.reconstruction_applied ? ' · reconstrução aplicada' : '');
    const reasonLabels = (q.review_reasons || []).map((r) => QE_REASON_LABEL[r] || r);
    document.getElementById('rv-reasons').textContent = reasonLabels.length ? `Motivo(s): ${reasonLabels.join(', ')}` : '';
    document.getElementById('rv-statement').value = q.display_text || '';
    document.getElementById('rv-notes').value = q.notes || '';
    renderPageImage();
    renderOptions();

    const orphanBanner = document.getElementById('rv-orphan-banner');
    if (q.orphan_text_candidate) {
      orphanBanner.hidden = false;
      document.getElementById('rv-orphan-text').textContent =
        `Texto encontrado no documento que não foi associado com segurança a esta questão: "${q.orphan_text_candidate}"`;
    } else {
      orphanBanner.hidden = true;
    }
  }

  function renderPageImage() {
    document.getElementById('rv-page-label').textContent = `página ${rv.currentPage}`;
    document.getElementById('rv-page-img').src =
      `/api/v1/catalog/question-extraction/questions/${rv.question.id}/page-image?page=${rv.currentPage}`;
  }

  function renderOptions() {
    document.getElementById('rv-options').innerHTML = rv.options.map((o, i) => `
      <div class="rv-option-row" data-idx="${i}">
        <input type="text" class="text-input rv-option-label" data-field="label" value="${tmEsc(o.label)}" maxlength="2">
        <input type="text" class="text-input rv-option-text" data-field="text" value="${tmEsc(o.text)}">
        <button class="btn btn-link" type="button" data-rv-remove-option="${i}">Remover</button>
      </div>`).join('');
  }

  function readOptionsFromDom() {
    const rows = document.querySelectorAll('#rv-options .rv-option-row');
    rv.options = Array.from(rows).map((row) => ({
      label: row.querySelector('[data-field="label"]').value.trim(),
      text: row.querySelector('[data-field="text"]').value.trim(),
    }));
    return rv.options;
  }

  async function loadCandidateAssets(questionId) {
    const banner = document.getElementById('rv-assets-banner');
    try {
      const res = await fetch(`/api/v1/catalog/question-extraction/questions/${questionId}/candidate-assets`, {
        headers: tmHeaders(),
      });
      if (!res.ok) { banner.hidden = true; return; }
      const assets = await res.json();
      if (!assets.length) { banner.hidden = true; return; }
      banner.hidden = false;
      document.getElementById('rv-assets-list').innerHTML = assets.map((a) => `
        <div class="tm-form-actions" data-asset-id="${tmEsc(a.id)}">
          <span class="empty-text">Página ${a.source_page}</span>
          <button class="btn btn-secondary" type="button" data-rv-associate="${tmEsc(a.id)}">Associar à questão</button>
          <button class="btn btn-link" type="button" data-rv-ignore-asset="${tmEsc(a.id)}">Ignorar imagem</button>
        </div>`).join('');
    } catch (e) { banner.hidden = true; }
  }

  async function saveReviewedQuestion() {
    const payload = {
      reviewed_text: document.getElementById('rv-statement').value,
      options: readOptionsFromDom(),
      notes: document.getElementById('rv-notes').value,
    };
    const res = await fetch(`/api/v1/catalog/question-extraction/questions/${rv.question.id}`, {
      method: 'PATCH', headers: tmHeaders(), body: JSON.stringify(payload),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data && data.detail;
      tmMsg('rv-msg', (d && d.message) ? d.message : 'Não foi possível salvar.');
      return null;
    }
    rv.question = data;
    return data;
  }

  async function approveCurrentQuestion() {
    tmMsg('rv-validation-msg', '');
    const saved = await saveReviewedQuestion();
    if (!saved) return;
    const res = await fetch(`/api/v1/catalog/question-extraction/questions/${rv.question.id}/approve`, {
      method: 'POST', headers: tmHeaders(),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data && data.detail;
      tmMsg('rv-validation-msg', (d && d.message) ? d.message : 'Não foi possível aprovar.');
      return;
    }
    tmMsg('rv-msg', '');
    loadReviewQueue();
    goToNextInQueue();
  }

  async function confirmRejectCurrentQuestion() {
    const reason = document.getElementById('rv-reject-reason').value;
    const res = await fetch(`/api/v1/catalog/question-extraction/questions/${rv.question.id}/reject`, {
      method: 'POST', headers: tmHeaders(), body: JSON.stringify({ reason }),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data && data.detail;
      tmMsg('rv-msg', (d && d.message) ? d.message : 'Não foi possível rejeitar.');
      return;
    }
    document.getElementById('rv-reject-panel').hidden = true;
    loadReviewQueue();
    goToNextInQueue();
  }

  function goToNextInQueue() {
    const nextId = rq.queueIds[rv.queueIndex + 1];
    if (nextId) { openReviewQuestion(nextId); } else { closeReviewPanel(); }
  }

  function closeReviewPanel() {
    document.getElementById('rv-panel').hidden = true;
    rv.question = null;
  }

  (function wireReviewPanel() {
    const approveBtn = document.getElementById('rv-approve-btn');
    if (approveBtn) approveBtn.addEventListener('click', approveCurrentQuestion);
    const saveContinueBtn = document.getElementById('rv-save-continue-btn');
    if (saveContinueBtn) saveContinueBtn.addEventListener('click', async () => {
      const saved = await saveReviewedQuestion();
      if (saved) { loadReviewQueue(); goToNextInQueue(); }
    });
    const saveExitBtn = document.getElementById('rv-save-exit-btn');
    if (saveExitBtn) saveExitBtn.addEventListener('click', async () => {
      const saved = await saveReviewedQuestion();
      if (saved) { loadReviewQueue(); closeReviewPanel(); }
    });
    const rejectBtn = document.getElementById('rv-reject-btn');
    if (rejectBtn) rejectBtn.addEventListener('click', () => {
      document.getElementById('rv-reject-panel').hidden = false;
    });
    const rejectConfirmBtn = document.getElementById('rv-reject-confirm-btn');
    if (rejectConfirmBtn) rejectConfirmBtn.addEventListener('click', confirmRejectCurrentQuestion);
    const addOptionBtn = document.getElementById('rv-add-option');
    if (addOptionBtn) addOptionBtn.addEventListener('click', () => {
      readOptionsFromDom();
      rv.options.push({ label: '', text: '' });
      renderOptions();
    });
    const optionsWrap = document.getElementById('rv-options');
    if (optionsWrap) optionsWrap.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-rv-remove-option]');
      if (!btn) return;
      readOptionsFromDom();
      rv.options.splice(Number(btn.dataset.rvRemoveOption), 1);
      renderOptions();
    });
    const prevPageBtn = document.getElementById('rv-prev-page');
    if (prevPageBtn) prevPageBtn.addEventListener('click', () => {
      if (rv.currentPage > 1) { rv.currentPage -= 1; renderPageImage(); }
    });
    const nextPageBtn = document.getElementById('rv-next-page');
    if (nextPageBtn) nextPageBtn.addEventListener('click', () => {
      rv.currentPage += 1; renderPageImage();
    });
    const assetsList = document.getElementById('rv-assets-list');
    if (assetsList) assetsList.addEventListener('click', async (e) => {
      const assoc = e.target.closest('[data-rv-associate]');
      const ignore = e.target.closest('[data-rv-ignore-asset]');
      const assetId = assoc ? assoc.dataset.rvAssociate : (ignore ? ignore.dataset.rvIgnoreAsset : null);
      if (!assetId || !rv.question) return;
      const action = assoc ? 'associate' : 'ignore';
      await fetch(`/api/v1/catalog/question-extraction/questions/${rv.question.id}/assets/${assetId}/${action}`, {
        method: 'POST', headers: tmHeaders(),
      });
      loadCandidateAssets(rv.question.id);
    });
    const orphanAddBtn = document.getElementById('rv-orphan-add');
    if (orphanAddBtn) orphanAddBtn.addEventListener('click', () => {
      const ta = document.getElementById('rv-statement');
      const extra = rv.question.orphan_text_candidate || '';
      ta.value = ta.value ? `${ta.value} ${extra}` : extra;
      document.getElementById('rv-orphan-banner').hidden = true;
    });
    const orphanIgnoreBtn = document.getElementById('rv-orphan-ignore');
    if (orphanIgnoreBtn) orphanIgnoreBtn.addEventListener('click', () => {
      document.getElementById('rv-orphan-banner').hidden = true;
    });

    // spec s14 — atalhos de teclado (nunca interferem em campos de texto)
    document.addEventListener('keydown', (e) => {
      if (!rv.question) return;
      const tag = (e.target && e.target.tagName || '').toLowerCase();
      const inField = tag === 'input' || tag === 'textarea' || tag === 'select';
      const mod = e.ctrlKey || e.metaKey;
      if (mod && e.key === 'Enter') { e.preventDefault(); approveCurrentQuestion(); return; }
      if (mod && e.key.toLowerCase() === 's') {
        e.preventDefault();
        saveReviewedQuestion().then((saved) => { if (saved) loadReviewQueue(); });
        return;
      }
      if (!inField && e.key === 'ArrowLeft' && rv.queueIndex > 0) {
        openReviewQuestion(rq.queueIds[rv.queueIndex - 1]);
      }
      if (!inField && e.key === 'ArrowRight' && rv.queueIndex >= 0 && rv.queueIndex + 1 < rq.queueIds.length) {
        openReviewQuestion(rq.queueIds[rv.queueIndex + 1]);
      }
    });
  })();

  // -- Publicação no Banco de Questões (spec s18) ------------------------
  async function loadPublishSummary() {
    if (!qe.lastRunId) { tmMsg('pub-msg', 'Execute uma extração primeiro.'); return; }
    const res = await fetch(`/api/v1/catalog/question-extraction/runs/${qe.lastRunId}/publish-summary`, {
      headers: tmHeaders(),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) { tmMsg('pub-msg', 'Não foi possível carregar o resumo.'); return; }
    document.getElementById('pub-summary').innerHTML =
      `Documento: <strong>${tmEsc(data.document || '')}</strong><br>`
      + `${data.total} total · ${data.approved} aprovadas · ${data.pending} pendentes · `
      + `${data.rejected} rejeitadas · ${data.duplicate_review || 0} suspeitas de duplicata · ${data.published} publicadas`;
    document.getElementById('pub-publish-btn').hidden = data.approved <= 0;
  }

  async function publishApprovedQuestions() {
    tmMsg('pub-msg', '');
    const res = await fetch(`/api/v1/catalog/question-extraction/runs/${qe.lastRunId}/publish`, {
      method: 'POST', headers: tmHeaders(),
    });
    const data = await res.json().catch(() => ({}));
    if (!res.ok) {
      const d = data && data.detail;
      tmMsg('pub-msg', (d && d.message) ? d.message : 'Não foi possível publicar.');
      return;
    }
    tmMsg('pub-msg', `Publicadas: ${data.published_count} · Duplicatas: ${data.duplicate_count} · Erros: ${data.error_count}`);
    loadPublishSummary();
    loadReviewQueue();
  }

  (function wirePublish() {
    const summaryBtn = document.getElementById('pub-summary-btn');
    if (summaryBtn) summaryBtn.addEventListener('click', loadPublishSummary);
    const publishBtn = document.getElementById('pub-publish-btn');
    if (publishBtn) publishBtn.addEventListener('click', publishApprovedQuestions);
  })();

  // ===================================================================
  // PHASE 30 — Classificação Pedagógica de questões AUTHORIAL já
  // publicadas. Reutiliza o currículo existente (catalog_nodes) via
  // dropdowns reais - nunca digitação livre de código. IA nunca escolhe
  // livremente: apenas seleciona entre candidatos curriculares válidos, e
  // qualquer ambiguidade vai para revisão humana. Zero IA fora do
  // classificador.
  // ===================================================================
  const pc = { rows: [], current: null };

  const PC_STATUS_LABEL = { CLASSIFIED: 'Classificada', NEEDS_REVIEW: '⚠ Precisa de revisão', DRAFT: 'Rascunho' };

  async function loadPublishedQuestionsForClassification() {
    if (!qe.lastRunId) { tmMsg('pc-msg', 'Execute uma extração e publique questões primeiro.'); return; }
    tmMsg('pc-msg', '');
    try {
      const res = await fetch(`/api/v1/catalog/question-extraction/runs/${qe.lastRunId}`, { headers: tmHeaders() });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { tmMsg('pc-msg', 'Não foi possível carregar as questões publicadas.'); return; }
      const published = (data.questions || []).filter((q) => q.published_question_id);
      pc.rows = await Promise.all(published.map(async (q) => {
        const versionId = await resolvePublishedVersionId(q);
        let classification = null;
        if (versionId) {
          const cres = await fetch(`/api/v1/catalog/question-classification/question-versions/${versionId}`, { headers: tmHeaders() });
          if (cres.ok) classification = await cres.json();
        }
        return { question_number: q.question_number, question_version_id: versionId, classification };
      }));
      renderClassificationTable();
    } catch (e) { tmMsg('pc-msg', 'Sem conexão.'); }
  }

  async function resolvePublishedVersionId(extractedQuestion) {
    // the extraction question dict does not carry published_version_id
    // directly today - re-fetch the single question to get it.
    const res = await fetch(`/api/v1/catalog/question-extraction/questions/${extractedQuestion.id}`, { headers: tmHeaders() });
    if (!res.ok) return null;
    const full = await res.json();
    return full.published_version_id || null;
  }

  function renderClassificationTable() {
    const body = document.getElementById('pc-table-body');
    const empty = document.getElementById('pc-empty');
    if (!pc.rows.length) { body.innerHTML = ''; empty.hidden = false; return; }
    empty.hidden = true;
    body.innerHTML = pc.rows.map((row, idx) => {
      const c = row.classification;
      return `<tr>
        <td>${row.question_number}</td>
        <td>${tmEsc(c ? (c.discipline_code || '') : '')}</td>
        <td>${tmEsc(c ? (c.content_code || '') : '')}</td>
        <td>${tmEsc(c ? (c.subcontent_code || '') : '')}</td>
        <td>${tmEsc(c ? c.difficulty : '')}</td>
        <td>${c && c.classification_confidence != null ? (c.classification_confidence * 100).toFixed(0) + '%' : '—'}</td>
        <td>${c ? tmEsc(PC_STATUS_LABEL[c.status] || c.status) : 'Não classificada'}</td>
        <td><button class="btn btn-link" type="button" data-pc-open="${idx}">${c ? 'Ver / editar' : 'Classificar'}</button></td>
      </tr>`;
    }).join('');
  }

  async function batchClassifyPublished() {
    if (!qe.lastRunId) { tmMsg('pc-msg', 'Execute uma extração e publique questões primeiro.'); return; }
    tmMsg('pc-msg', '');
    try {
      const res = await fetch('/api/v1/catalog/question-classification/batch-classify', {
        method: 'POST', headers: tmHeaders(), body: JSON.stringify({ extraction_run_id: qe.lastRunId }),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) { tmMsg('pc-msg', 'Não foi possível classificar em lote.'); return; }
      tmMsg('pc-msg', `Processadas: ${data.questions_processed} · Classificadas: ${data.classified} · Precisam de revisão: ${data.needs_review} · Erros: ${data.errors}`);
      loadPublishedQuestionsForClassification();
    } catch (e) { tmMsg('pc-msg', 'Sem conexão.'); }
  }

  async function openClassificationDetail(idx) {
    const row = pc.rows[idx];
    if (!row || !row.question_version_id) {
      tmMsg('pc-msg', 'Esta questão ainda não foi classificada - use "Classificar todas" primeiro.');
      return;
    }
    pc.current = row;
    document.getElementById('pc-detail').hidden = false;
    document.getElementById('pc-detail-number').textContent = row.question_number;
    const c = row.classification;
    document.getElementById('pc-needs-review-banner').hidden = !(c && c.status === 'NEEDS_REVIEW');
    document.getElementById('pc-detail-reason').textContent = c && c.review_reason ? `Motivo: ${c.review_reason}` : (c && c.classification_reason ? c.classification_reason : '');
    document.getElementById('pc-detail-meta').textContent = c
      ? `Classificador: ${c.classifier_version || '—'} · Currículo: ${c.curriculum_version || '—'} · Confidence dificuldade: ${c.difficulty_confidence != null ? (c.difficulty_confidence * 100).toFixed(0) + '%' : '—'}`
      : '';
    document.getElementById('pc-edit-reason').value = '';
    document.getElementById('pc-history').hidden = true;
    await populateDisciplineDropdown(c);
  }

  async function fetchNodes(parentId) {
    const url = parentId ? `/api/v1/catalog/nodes?parent_id=${parentId}` : '/api/v1/catalog/disciplines';
    const res = await fetch(url, { headers: tmHeaders() });
    if (!res.ok) return [];
    return res.json();
  }

  function fillSelect(select, nodes, placeholder) {
    select.innerHTML = (placeholder ? `<option value="">${placeholder}</option>` : '')
      + nodes.map((n) => `<option value="${tmEsc(n.id)}" data-code="${tmEsc(n.code)}">${tmEsc(n.name)} (${tmEsc(n.code)})</option>`).join('');
  }

  async function populateDisciplineDropdown(classification) {
    const disciplines = await fetchNodes(null);
    const disciplineSel = document.getElementById('pc-discipline');
    fillSelect(disciplineSel, disciplines, null);
    const targetDiscipline = disciplines.find((d) => d.code === (classification && classification.discipline_code));
    if (targetDiscipline) disciplineSel.value = targetDiscipline.id;
    await populateAreaDropdown(classification);
  }

  async function populateAreaDropdown(classification) {
    const disciplineSel = document.getElementById('pc-discipline');
    const areas = disciplineSel.value ? await fetchNodes(disciplineSel.value) : [];
    const areaSel = document.getElementById('pc-area');
    fillSelect(areaSel, areas, null);
    const targetArea = areas.find((a) => a.code === (classification && classification.area_code));
    if (targetArea) areaSel.value = targetArea.id;
    await populateContentDropdown(classification);
  }

  async function populateContentDropdown(classification) {
    const areaSel = document.getElementById('pc-area');
    const contents = areaSel.value ? await fetchNodes(areaSel.value) : [];
    const contentSel = document.getElementById('pc-content');
    fillSelect(contentSel, contents, null);
    const targetContent = contents.find((c) => c.code === (classification && classification.content_code));
    if (targetContent) contentSel.value = targetContent.id;
    await populateSubcontentDropdown(classification);
  }

  async function populateSubcontentDropdown(classification) {
    const contentSel = document.getElementById('pc-content');
    const subcontents = contentSel.value ? await fetchNodes(contentSel.value) : [];
    const subSel = document.getElementById('pc-subcontent');
    fillSelect(subSel, subcontents, '(nenhum)');
    const targetSub = subcontents.find((s) => s.code === (classification && classification.subcontent_code));
    if (targetSub) subSel.value = targetSub.id;
    if (classification && classification.difficulty && classification.difficulty !== 'UNKNOWN') {
      document.getElementById('pc-difficulty').value = classification.difficulty;
    }
  }

  function selectedCode(selectId) {
    const el = document.getElementById(selectId);
    const opt = el.options[el.selectedIndex];
    return opt && opt.dataset.code ? opt.dataset.code : null;
  }

  async function saveManualClassification() {
    if (!pc.current) return;
    tmMsg('pc-detail-msg', '');
    const payload = {
      discipline_code: selectedCode('pc-discipline'), area_code: selectedCode('pc-area'),
      content_code: selectedCode('pc-content'), subcontent_code: selectedCode('pc-subcontent'),
      difficulty: document.getElementById('pc-difficulty').value,
      reason: document.getElementById('pc-edit-reason').value || 'Ajuste manual do professor.',
    };
    if (!payload.content_code) { tmMsg('pc-detail-msg', 'Selecione ao menos disciplina, área e conteúdo.'); return; }
    try {
      const res = await fetch(`/api/v1/catalog/question-classification/question-versions/${pc.current.question_version_id}`, {
        method: 'PATCH', headers: tmHeaders(), body: JSON.stringify(payload),
      });
      const data = await res.json().catch(() => ({}));
      if (!res.ok) {
        const d = data && data.detail;
        tmMsg('pc-detail-msg', (d && d.message) ? d.message : 'Classificação inválida.');
        return;
      }
      pc.current.classification = data;
      renderClassificationTable();
      tmMsg('pc-detail-msg', '');
    } catch (e) { tmMsg('pc-detail-msg', 'Sem conexão.'); }
  }

  async function approveCurrentClassification() {
    if (!pc.current || !pc.current.classification) return;
    const res = await fetch(`/api/v1/catalog/question-classification/classifications/${pc.current.classification.id}/approve`, {
      method: 'POST', headers: tmHeaders(), body: JSON.stringify({}),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) { pc.current.classification = data; renderClassificationTable(); }
  }

  async function reclassifyCurrentQuestion() {
    if (!pc.current) return;
    const reason = document.getElementById('pc-edit-reason').value || 'Reclassificação solicitada pelo professor.';
    const res = await fetch(`/api/v1/catalog/question-classification/question-versions/${pc.current.question_version_id}/reclassify`, {
      method: 'POST', headers: tmHeaders(), body: JSON.stringify({ reason }),
    });
    const data = await res.json().catch(() => ({}));
    if (res.ok) {
      pc.current.classification = data;
      renderClassificationTable();
      await populateDisciplineDropdown(data);
    } else {
      const d = data && data.detail;
      tmMsg('pc-detail-msg', (d && d.message) ? d.message : 'Não foi possível reclassificar.');
    }
  }

  async function loadClassificationHistory() {
    if (!pc.current) return;
    const historyEl = document.getElementById('pc-history');
    const res = await fetch(`/api/v1/catalog/question-classification/question-versions/${pc.current.question_version_id}/history`, { headers: tmHeaders() });
    if (!res.ok) return;
    const events = await res.json();
    historyEl.hidden = false;
    historyEl.innerHTML = events.map((e) => `
      <div class="tm-section-row">
        <div>
          <strong>${tmEsc(e.action)}</strong> · ${tmEsc(e.actor_type)} (${tmEsc(e.actor)}) · ${new Date(e.created_at).toLocaleString('pt-BR')}
          ${e.new_value ? `<p class="empty-text">${tmEsc(e.new_value.content_code || '')} → ${tmEsc(e.new_value.subcontent_code || '')} · dificuldade ${tmEsc(e.new_value.difficulty || '')}</p>` : ''}
          ${e.reason ? `<p class="empty-text">Motivo: ${tmEsc(e.reason)}</p>` : ''}
        </div>
      </div>`).join('') || '<p class="empty-text">Sem histórico.</p>';
  }

  (function wireClassification() {
    const loadBtn = document.getElementById('pc-load-btn');
    if (loadBtn) loadBtn.addEventListener('click', loadPublishedQuestionsForClassification);
    const batchBtn = document.getElementById('pc-batch-btn');
    if (batchBtn) batchBtn.addEventListener('click', batchClassifyPublished);
    const body = document.getElementById('pc-table-body');
    if (body) body.addEventListener('click', (e) => {
      const btn = e.target.closest('[data-pc-open]');
      if (btn) openClassificationDetail(Number(btn.dataset.pcOpen));
    });
    const disciplineSel = document.getElementById('pc-discipline');
    if (disciplineSel) disciplineSel.addEventListener('change', () => populateAreaDropdown(null));
    const areaSel = document.getElementById('pc-area');
    if (areaSel) areaSel.addEventListener('change', () => populateContentDropdown(null));
    const contentSel = document.getElementById('pc-content');
    if (contentSel) contentSel.addEventListener('change', () => populateSubcontentDropdown(null));
    const saveManualBtn = document.getElementById('pc-save-manual-btn');
    if (saveManualBtn) saveManualBtn.addEventListener('click', saveManualClassification);
    const approveBtn = document.getElementById('pc-approve-btn');
    if (approveBtn) approveBtn.addEventListener('click', approveCurrentClassification);
    const reclassifyBtn = document.getElementById('pc-reclassify-btn');
    if (reclassifyBtn) reclassifyBtn.addEventListener('click', reclassifyCurrentQuestion);
    const historyBtn = document.getElementById('pc-history-btn');
    if (historyBtn) historyBtn.addEventListener('click', loadClassificationHistory);
  })();

  // Initial Load
  loadTeacherDashboard();
});
