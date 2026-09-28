// AGENTE IA EDU — Tela de entrada (dois ambientes)
//
// Resolve, a partir da identidade auto-declarada do aluno (mesmo mecanismo
// já usado em app.js/essay.js - sessionStorage.studentAccessToken), quais
// módulos a escola dele tem habilitados via GET /api/v1/student/modules, e
// roteia: 0 módulos -> erro honesto; 1 módulo -> redireciona direto; 2
// módulos -> mostra os dois cards. Nunca assume um módulo como habilitado -
// a fonte de verdade é sempre a resposta real do backend.

document.addEventListener('DOMContentLoaded', () => {
  const idInput = document.getElementById('entrada-student-id');
  const confirmBtn = document.getElementById('entrada-confirm-btn');
  const errorBox = document.getElementById('entrada-error');
  const cardsBox = document.getElementById('entrada-cards');
  const loadingEl = document.getElementById('entrada-loading');

  const ENV_DESTINATIONS = {
    AGENTE_IA_EDU: '/student',
    REDACAO_IA: '/redacao',
  };

  function showError(msg) {
    errorBox.textContent = msg;
    errorBox.style.display = 'block';
    cardsBox.style.display = 'none';
  }

  function clearError() {
    errorBox.style.display = 'none';
    errorBox.textContent = '';
  }

  function setLoading(isLoading) {
    confirmBtn.disabled = isLoading;
    if (loadingEl) loadingEl.hidden = !isLoading;
  }

  async function handleConfirm() {
    const studentId = idInput.value.trim();
    if (!studentId) {
      showError('Informe sua identidade de aluno para continuar.');
      return;
    }

    clearError();
    cardsBox.style.display = 'none';
    setLoading(true);

    // Grava a identidade uma única vez - o mesmo token é lido depois por
    // app.js e essay.js (sessionStorage.studentAccessToken), independente
    // de qual ambiente o aluno escolher.
    sessionStorage.setItem('studentAccessToken', studentId);

    let resp;
    try {
      resp = await fetch('/api/v1/student/modules', {
        headers: { 'Authorization': `Bearer ${studentId}` },
      });
    } catch (e) {
      setLoading(false);
      showError('Não foi possível conectar ao servidor. Verifique sua conexão e tente novamente.');
      return;
    }

    if (!resp.ok) {
      setLoading(false);
      if (resp.status === 401 || resp.status === 403 || resp.status === 404) {
        showError('Não foi possível confirmar sua identidade. Verifique com a coordenação da sua escola.');
      } else {
        showError(`Não foi possível carregar os módulos da sua escola (HTTP ${resp.status}). Tente novamente.`);
      }
      return;
    }

    let modules;
    try {
      modules = await resp.json();
    } catch (e) {
      setLoading(false);
      showError('Resposta inesperada do servidor. Tente novamente.');
      return;
    }

    setLoading(false);

    const enabled = Object.keys(ENV_DESTINATIONS).filter((key) => modules[key] === true);

    if (enabled.length === 0) {
      showError('Nenhum módulo habilitado para sua escola. Fale com a coordenação.');
    } else if (enabled.length === 1) {
      window.location.href = ENV_DESTINATIONS[enabled[0]];
    } else {
      cardsBox.style.display = 'grid';
    }
  }

  confirmBtn.addEventListener('click', handleConfirm);
  idInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') handleConfirm();
  });

  cardsBox.addEventListener('click', (e) => {
    const card = e.target.closest('.entrada-card');
    if (!card) return;
    const env = card.dataset.env === 'agente' ? 'AGENTE_IA_EDU' : 'REDACAO_IA';
    window.location.href = ENV_DESTINATIONS[env];
  });
});
