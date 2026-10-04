/* =========================================================================
   PROTOTIPO DO PERFIL ALUNO.

   O portal atual (index.html + app.js) continua intacto. Este arquivo nao o
   importa nem o altera.

   O QUE E REAL E O QUE E MOCK
   ---------------------------
   Real no backend hoje (ver docs/frontend-aluno-auditoria.md s2):
     POST /api/v1/student/study-session  -> available_minutes, no_timer,
                                            target_content_codes
     GET  /api/v1/student/study-session/today
     GET  /api/v1/student/activities      -> due_at, availability
     GET  /api/v1/student/dashboard       -> welcome_message, has_data
     GET  /api/v1/student/learning-path   -> estado do aluno por conteudo
   O planejador ja monta blocos STUDY/PRACTICE/REVIEW/BREAK e ja reparte o
   tempo conforme o estado (INSUFFICIENT_EVIDENCE, BLOCKED_BY_PREREQUISITE...).

   Mock aqui, porque NAO existe contrato:
     - conteudos exigidos por uma atividade (QBStudentActivity nao expoe)
     - prova proxima (nao ha entidade de avaliacao com data)
     - interpretacao de texto livre -> assunto
     - foto / arquivo / voz (controles nascem DESABILITADOS)

   Regra que este arquivo respeita em todo lugar: nenhum botao que nao leva
   a lugar nenhum. Bloco sem experiencia executavel aparece com a nota e SEM
   botao - como o proprio planejador ja faz no backend.
   ========================================================================= */

(() => {
  'use strict';

  // ===================================================== MOCK =============
  // Trocar por fetch() quando os contratos existirem. Ver secao 10 de
  // docs/frontend-aluno-experiencia.md.
  const MOCK = {
    aluno: { nome: 'Pedro', inicial: 'P', temEscola: true },

    // GET /api/v1/student/activities  (real, menos `conteudos`)
    tarefas: [
      {
        assignment_id: 'a1', title: 'Atividade de Estequiometria',
        disciplina: 'Química', question_count: 12,
        due_at: 'amanhã', availability: 'OPEN',
        conteudos: ['CHEMISTRY-PHYSICAL-STOICHIOMETRY'],   // MOCK
      },
      {
        assignment_id: 'a2', title: 'Lista de Soluções',
        disciplina: 'Química', question_count: 8,
        due_at: 'sexta-feira', availability: 'OPEN',
        conteudos: ['CHEMISTRY-SOLUTIONS'],                // MOCK
      },
    ],

    // GET /api/v1/student/learning-path  (estados sao os reais do planejador)
    dominio: {
      'CHEMISTRY-PHYSICAL-STOICHIOMETRY': 'BLOCKED_BY_PREREQUISITE',
      'CHEMISTRY-SOLUTIONS': 'READY',
      'CHEMISTRY-GENERAL-BALANCING': 'INSUFFICIENT_EVIDENCE',
    },

    prerequisitos: {
      'CHEMISTRY-PHYSICAL-STOICHIOMETRY': [
        { codigo: 'CHEMISTRY-GENERAL-BALANCING', nome: 'Balanceamento de equações' },
      ],
    },

    nomes: {
      'CHEMISTRY-PHYSICAL-STOICHIOMETRY': 'Estequiometria',
      'CHEMISTRY-SOLUTIONS': 'Soluções',
      'CHEMISTRY-GENERAL-BALANCING': 'Balanceamento de equações',
    },

    // GET /api/v1/student/study-session/today
    // null = nao ha sessao em aberto. O seletor de estados do protótipo
    // preenche isto para demonstrar o estado D.
    interrompida: null,

    prova: { disciplina: 'Matemática', quando: 'sexta-feira' },   // MOCK
    dificuldade: { assunto: 'Soluções', minutos: 20 },

    progresso: {
      fatos: [
        'Você estudou 3 dias esta semana',
        'Soluções está ficando mais forte',
        'Você avançou em 4 habilidades',
      ],
      panorama: [
        { faixa: 'Precisa de atenção', itens: 'Estequiometria' },
        { faixa: 'Em desenvolvimento', itens: 'Soluções, Cinética' },
        { faixa: 'Consolidado', itens: 'Tabela periódica, Ligações' },
      ],
    },
  };

  // ============================================ roteamento por prontidao ==
  // Requisito 6 atualizado: a tarefa da escola e o OBJETIVO, nao
  // necessariamente o primeiro passo. Os tres caminhos correspondem 1:1 a
  // estados que o planejador do backend JA usa.
  const DIRETO = 'DIRECT';        // READY / MASTERED / RECOMMENDED
  const DIAGNOSTICO = 'DIAGNOSED'; // INSUFFICIENT_EVIDENCE
  const PREPARACAO = 'PREPARED';   // BLOCKED_BY_PREREQUISITE

  function rotaDeProntidao(conteudos) {
    const estados = conteudos.map((c) => MOCK.dominio[c] || 'INSUFFICIENT_EVIDENCE');
    if (estados.includes('BLOCKED_BY_PREREQUISITE')) return PREPARACAO;
    if (estados.includes('INSUFFICIENT_EVIDENCE')) return DIAGNOSTICO;
    return DIRETO;
  }

  // ======================================================= estado da tela =
  const app = {
    tela: 'inicio',
    homeForcada: null,   // so o seletor de protótipo usa
    sessao: null,
  };

  const $ = (id) => document.getElementById(id);
  const esc = (t) => String(t).replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  // =============================================== qual estado da Home ====
  // Precedencia declarada em docs/frontend-aluno-experiencia.md s2.
  function estadoDaHome() {
    if (app.homeForcada) return app.homeForcada;
    if (!MOCK.aluno.temHistorico && MOCK.semHistorico) return 'A';
    if (MOCK.interrompida) return 'D';
    if (MOCK.tarefas.length && MOCK.tarefas[0].due_at === 'amanhã') return 'B';
    if (MOCK.prova) return 'E';
    if (MOCK.dificuldade) return 'F';
    if (!MOCK.tarefas.length) return MOCK.aluno.temEscola ? 'H' : 'G';
    return 'C';
  }

  // ==================================================== Home: renderers ===
  function ola() {
    return `<p class="saudacao">Olá, ${esc(MOCK.aluno.nome)} 👋</p>`;
  }

  function blocoDeTempo(rotulo) {
    return `
      <p class="tempo-rotulo">${esc(rotulo)}</p>
      <div class="tempos">
        ${[15, 30, 45, 60].map((m) => `
          <button class="tempo" type="button" data-minutos="${m}">
            ${m === 60 ? '1h+' : `${m}&nbsp;min`}
          </button>`).join('')}
      </div>
      <button class="ligacao" type="button" data-minutos="0">Sem tempo definido</button>`;
  }

  const HOMES = {
    A: () => `
      <div class="contexto">
        ${ola()}
        <p class="chamada">Ainda não nos conhecemos.</p>
        <p class="detalhe">Em 10 minutos eu descubro por onde você deve começar.</p>
        <button class="botao botao-principal" data-acao="diagnostico">Vamos lá</button>
        <button class="ligacao" data-acao="escolher">Prefiro escolher eu mesmo</button>
      </div>`,

    B: () => {
      const t = MOCK.tarefas[0];
      return `
      <div class="contexto">
        ${ola()}
        <span class="selo selo-prazo">Para ${esc(t.due_at)}</span>
        <p class="chamada">Você tem uma atividade de <strong>${esc(t.disciplina)}</strong>.</p>
        <p class="detalhe">${esc(t.title)} · ${t.question_count} questões</p>
        <button class="botao botao-principal" data-acao="tarefa" data-id="${esc(t.assignment_id)}">
          Começar
        </button>
        <button class="ligacao" data-acao="escolher">Prefiro estudar outra coisa</button>
        ${MOCK.tarefas.length > 1
          ? `<button class="ligacao" data-acao="tarefas">${
               MOCK.tarefas.length === 2
                 ? 'Ver a outra tarefa'
                 : `Ver as outras ${MOCK.tarefas.length - 1} tarefas`
             }</button>`
          : ''}
      </div>`;
    },

    C: () => `
      <div class="contexto">
        ${ola()}
        <p class="chamada">Vamos estudar?</p>
        ${blocoDeTempo('Quanto tempo você tem hoje?')}
      </div>`,

    D: () => `
      <div class="contexto">
        <span class="selo selo-neutro">Continuar</span>
        <p class="chamada">Você parou em <strong>${esc(MOCK.interrompida.assunto)}</strong>.</p>
        <p class="detalhe">Faltavam ${MOCK.interrompida.faltam} minutos.</p>
        <button class="botao botao-principal" data-acao="retomar">Continuar</button>
        <button class="ligacao" data-acao="escolher">Começar outra coisa</button>
      </div>`,

    E: () => `
      <div class="contexto">
        ${ola()}
        <span class="selo selo-prazo">Prova ${esc(MOCK.prova.quando)}</span>
        <p class="chamada">Sua prova de <strong>${esc(MOCK.prova.disciplina)}</strong> está chegando.</p>
        <button class="botao botao-principal" data-acao="revisar-prova">Revisar para a prova</button>
        <button class="ligacao" data-acao="escolher">Estudar outra coisa</button>
      </div>`,

    F: () => `
      <div class="contexto">
        ${ola()}
        <p class="chamada"><strong>${esc(MOCK.dificuldade.assunto)}</strong> está custando mais que o resto.</p>
        <p class="detalhe">Que tal ${MOCK.dificuldade.minutos} minutos nisso hoje?</p>
        <button class="botao botao-principal" data-acao="dificuldade">Vamos lá</button>
        <button class="ligacao" data-acao="escolher">Prefiro outro assunto</button>
      </div>`,

    G: () => `
      <div class="contexto">
        ${ola()}
        <p class="chamada">O que vamos estudar hoje?</p>
        ${blocoDeTempo('Quanto tempo você tem?')}
      </div>`,

    H: () => `
      <div class="contexto">
        <span class="selo selo-bom">Tudo em dia</span>
        <p class="chamada">Nenhuma tarefa pendente 🎉</p>
        <p class="detalhe">Quer avançar ou revisar o que já viu?</p>
        <div class="dupla">
          <button class="botao botao-principal" data-acao="avancar">Avançar</button>
          <button class="botao botao-secundario" data-acao="revisar">Revisar</button>
        </div>
      </div>`,
  };

  function pintarHome() {
    const estado = estadoDaHome();
    $('home').innerHTML = (HOMES[estado] || HOMES.C)();
    $('home').dataset.estado = estado;
  }

  // ======================================================= a sessao =======
  // Monta o plano. No produto isto vem de POST /study-session; aqui o
  // formato espelha o do planejador (block_type, estimated_minutes,
  // action_available) para a troca ser direta.
  function montarSessao({ objetivo, conteudos, minutos }) {
    const rota = rotaDeProntidao(conteudos);
    const blocos = [];

    if (rota === DIAGNOSTICO) {
      blocos.push({
        tipo: 'DIAGNOSE', etiqueta: 'Antes de começar', minutos: 4,
        titulo: 'Três perguntas rápidas',
        porque: 'Assim eu descubro por onde te ajudar melhor.',
        acao: 'Responder', disponivel: true,
      });
    }

    if (rota === PREPARACAO) {
      const pre = (MOCK.prerequisitos[conteudos[0]] || [])[0];
      blocos.push({
        tipo: 'STUDY', etiqueta: 'Preparação', minutos: Math.max(8, Math.round(minutos * 0.35)),
        titulo: pre ? pre.nome : 'Uma ideia que vem antes',
        porque: 'Isso vai te ajudar a resolver a tarefa.',
        acao: 'Começar', disponivel: true,
      });
      blocos.push({
        tipo: 'PRACTICE', etiqueta: 'Praticar a base', minutos: Math.max(5, Math.round(minutos * 0.15)),
        titulo: 'Testar o que acabou de ver',
        porque: 'Poucas questões, só para firmar.',
        acao: 'Praticar', disponivel: true,
      });
    }

    const FECHAMENTO = 4;
    const gasto = blocos.reduce((s, b) => s + b.minutos, 0);
    const paraObjetivo = Math.max(5, minutos - gasto - FECHAMENTO);

    blocos.push({
      tipo: 'OBJECTIVE', etiqueta: 'A atividade', minutos: paraObjetivo,
      titulo: objetivo ? objetivo.title : 'Praticar',
      porque: objetivo
        ? (gasto > 0
            ? 'Agora sim, com a base pronta.'
            : 'O que a escola pediu.')
        : 'Questões no seu nível.',
      acao: 'Abrir', disponivel: true,
      parcial: Boolean(objetivo) && gasto > 0,
    });

    blocos.push({
      tipo: 'REVIEW', etiqueta: 'Fechamento', minutos: FECHAMENTO,
      titulo: 'Revisar o que ficou',
      // o backend marca review_action_available = False: nao inventamos botao
      porque: '', disponivel: false,
      nota: 'A revisão guiada ainda não está disponível. Vamos marcar o que você errou para a próxima.',
    });

    // CABER NO TEMPO QUE O ALUNO TEM.
    // Os pisos de cada bloco (8 + 5 + 5 + 4 = 22) nao cabem em 15 minutos.
    // O certo nao e inflar o tempo pedido - e fazer MENOS e dizer. Blocos
    // que nao cabem saem do fim para o comeco, porque a preparacao e o que
    // torna o resto possivel.
    const cabem = [];
    let acumulado = 0;
    for (const b of blocos) {
      if (acumulado + b.minutos > minutos && cabem.length) break;
      cabem.push(b);
      acumulado += b.minutos;
    }
    // Caso-limite: nem o PRIMEIRO bloco cabe (5 minutos contra um piso de 8).
    // Mantemos o bloco — ficar sem nada a fazer e pior — mas encurtado ao
    // tempo real e marcado como parcial, em vez de prometer 8 e gastar 8.
    if (cabem.length === 1 && cabem[0].minutos > minutos) {
      cabem[0] = { ...cabem[0], minutos, parcial: true };
    }

    const ficaramDeFora = blocos.slice(cabem.length);
    const objetivoFicouDeFora = ficaramDeFora.some((b) => b.tipo === 'OBJECTIVE');

    return {
      objetivo, rota, minutos, blocos: cabem, atual: 0,
      objetivoFicouDeFora,
      // dado que Professor/Coordenacao vao precisar depois (s3.1 do mapa)
      readiness_route: rota,
      objective_assignment_id: objetivo ? objetivo.assignment_id : null,
      completed_objective: false,
    };
  }

  function pintarSessao() {
    const s = app.sessao;
    if (!s) return;

    if (s.objetivo) {
      $('objetivo').hidden = false;
      $('objetivo-texto').textContent = s.objetivo.title;
    } else {
      $('objetivo').hidden = true;
    }

    $('trilho').innerHTML = s.blocos.map((_, i) => {
      const classe = i < s.atual ? 'passo passo-feito'
        : i === s.atual ? 'passo passo-agora' : 'passo';
      return `<span class="${classe}"></span>`;
    }).join('');

    const total = s.blocos.reduce((a, b) => a + b.minutos, 0);
    $('sessao-resumo').textContent =
      `Etapa ${s.atual + 1} de ${s.blocos.length} · ${total} min no total`
      + (s.objetivoFicouDeFora
          ? ' · hoje damos conta da preparação; a atividade fica para a próxima'
          : '');

    const b = s.blocos[s.atual];
    const proximo = s.blocos[s.atual + 1];

    if (!b) {
      $('bloco').innerHTML = `
        <div class="cartao-bloco">
          <p class="bloco-etiqueta">Pronto</p>
          <p class="bloco-titulo">Sessão concluída</p>
          <p class="bloco-porque">${s.objetivo
            ? 'Seu progresso na atividade foi salvo.'
            : 'Bom trabalho.'}</p>
          <button class="botao botao-principal" data-acao="inicio">Voltar ao início</button>
        </div>`;
      return;
    }

    $('bloco').innerHTML = `
      <div class="cartao-bloco">
        <p class="bloco-etiqueta">${esc(b.etiqueta)} · ${b.minutos} min</p>
        <p class="bloco-titulo">${esc(b.titulo)}</p>
        ${b.porque ? `<p class="bloco-porque">${esc(b.porque)}</p>` : ''}
        ${b.parcial
          ? `<p class="nota">Se não der para terminar hoje, seu progresso fica salvo.</p>`
          : ''}
        ${b.disponivel
          ? `<button class="botao botao-principal" data-acao="concluir-bloco">${esc(b.acao)}</button>`
          : `<p class="indisponivel">${esc(b.nota)}</p>
             <button class="botao botao-secundario" data-acao="concluir-bloco">Continuar</button>`}
        ${proximo
          ? `<p class="a-seguir">a seguir: ${esc(proximo.titulo.toLowerCase())} · ${proximo.minutos} min</p>`
          : ''}
      </div>`;
  }

  // ===================================================== progresso ========
  function pintarProgresso() {
    $('fatos').innerHTML = MOCK.progresso.fatos
      .map((f) => `<li>${esc(f)}</li>`).join('');
    $('panorama-corpo').innerHTML = MOCK.progresso.panorama.map((p) => `
      <div class="faixa">
        <h3>${esc(p.faixa)}</h3>
        <p>${esc(p.itens)}</p>
      </div>`).join('');
  }

  // ===================================================== navegacao ========
  function irPara(tela) {
    app.tela = tela;
    ['inicio', 'sessao', 'progresso'].forEach((t) => {
      $(`tela-${t}`).hidden = (t !== tela);
    });
    document.querySelectorAll('.aba').forEach((aba) => {
      const ativa = aba.dataset.tela === tela;
      aba.classList.toggle('ativa', ativa);
      if (ativa) aba.setAttribute('aria-current', 'page');
      else aba.removeAttribute('aria-current');
    });
    if (tela === 'inicio') pintarHome();
    if (tela === 'progresso') pintarProgresso();
    if (tela === 'sessao') pintarSessao();
    window.scrollTo(0, 0);
  }

  // ===================================================== acoes ============
  function comecarSessao({ objetivo = null, conteudos = [], minutos = 30 }) {
    app.sessao = montarSessao({ objetivo, conteudos, minutos });
    irPara('sessao');
  }

  let tarefaPendente = null;   // tarefa escolhida, esperando o tempo

  function perguntarTempo(tarefa) {
    tarefaPendente = tarefa;
    $('home').innerHTML = `
      <div class="contexto">
        ${tarefa ? `<span class="selo selo-neutro">${esc(tarefa.title)}</span>` : ''}
        <p class="chamada">${tarefa
          ? 'Vamos a ela. Quanto tempo você tem hoje?'
          : 'Quanto tempo você tem hoje?'}</p>
        ${blocoDeTempo(tarefa ? '' : 'Escolha abaixo')}
      </div>`;
  }

  document.addEventListener('click', (e) => {
    const alvo = e.target.closest('[data-acao], [data-minutos], [data-tela], [data-fechar-folha], [data-estado]');
    if (!alvo) return;

    // --- tempo escolhido
    if (alvo.dataset.minutos !== undefined) {
      const m = Number(alvo.dataset.minutos) || 45;   // "sem tempo" -> alvo do backend
      comecarSessao({
        objetivo: tarefaPendente,
        conteudos: tarefaPendente ? tarefaPendente.conteudos : ['CHEMISTRY-SOLUTIONS'],
        minutos: m,
      });
      tarefaPendente = null;
      return;
    }

    // --- abas
    if (alvo.dataset.tela) { irPara(alvo.dataset.tela); return; }

    // --- folhas
    if (alvo.hasAttribute('data-fechar-folha')) {
      alvo.closest('dialog').close();
      return;
    }

    // --- seletor de estados (protótipo)
    if (alvo.dataset.estado) {
      app.homeForcada = alvo.dataset.estado;
      document.querySelectorAll('#estados button').forEach((b) =>
        b.setAttribute('aria-pressed', String(b === alvo)));
      irPara('inicio');
      return;
    }

    switch (alvo.dataset.acao) {
      case 'tarefa': {
        const t = MOCK.tarefas.find((x) => x.assignment_id === alvo.dataset.id);
        perguntarTempo(t);
        break;
      }
      case 'tarefas':
        pintarFolhaTarefas();
        $('folha-tarefas').showModal();
        break;
      case 'escolher':
      case 'avancar':
      case 'revisar':
      case 'diagnostico':
        perguntarTempo(null);
        break;
      case 'dificuldade':
        comecarSessao({ conteudos: ['CHEMISTRY-SOLUTIONS'], minutos: MOCK.dificuldade.minutos });
        break;
      case 'revisar-prova':
        comecarSessao({ conteudos: ['CHEMISTRY-SOLUTIONS'], minutos: 30 });
        break;
      case 'retomar':
        comecarSessao({ conteudos: ['CHEMISTRY-SOLUTIONS'], minutos: MOCK.interrompida.faltam });
        break;
      case 'concluir-bloco':
        app.sessao.atual += 1;
        pintarSessao();
        break;
      case 'inicio':
        app.sessao = null;
        irPara('inicio');
        break;
      default:
        break;
    }
  });

  function pintarFolhaTarefas() {
    $('lista-tarefas').innerHTML = MOCK.tarefas.map((t) => `
      <li>
        <strong>${esc(t.title)}</strong>
        <span class="nota">${esc(t.disciplina)} · ${t.question_count} questões · para ${esc(t.due_at)}</span>
        <button class="ligacao" data-acao="tarefa" data-id="${esc(t.assignment_id)}">Começar esta</button>
      </li>`).join('');
  }

  // sair da sessao preserva o progresso (vira estado D na volta)
  $('btn-sair-sessao').addEventListener('click', () => {
    if (app.sessao) {
      const b = app.sessao.blocos[app.sessao.atual];
      MOCK.interrompida = {
        assunto: app.sessao.objetivo ? app.sessao.objetivo.title : (b ? b.titulo : 'seu estudo'),
        faltam: app.sessao.blocos.slice(app.sessao.atual).reduce((s, x) => s + x.minutos, 0),
      };
    }
    app.sessao = null;
    app.homeForcada = 'D';
    irPara('inicio');
  });

  $('btn-proximo-passo').addEventListener('click', () => {
    app.homeForcada = null;
    irPara('inicio');
  });

  $('btn-perfil').addEventListener('click', () => {
    $('perfil-nome').textContent = `${MOCK.aluno.nome} · aluno`;
    $('folha-perfil').showModal();
  });

  // texto livre: ainda NAO ha interpretacao de linguagem natural no backend.
  // Em vez de fingir, levamos para a pergunta de tempo com o texto como alvo.
  $('form-perguntar').addEventListener('submit', (e) => {
    e.preventDefault();
    const texto = $('campo-duvida').value.trim();
    if (!texto) return;
    $('nota-perguntar').textContent =
      `Entendido: "${texto}". A interpretação de texto livre ainda não existe no `
      + 'backend — por enquanto vamos montar uma sessão a partir do seu tempo.';
    perguntarTempo(null);
  });

  // =================================================== seletor de estados =
  (function montarSeletor() {
    const nomes = {
      A: 'primeira entrada', B: 'tarefa pendente', C: 'sem tarefa',
      D: 'sessão interrompida', E: 'prova próxima', F: 'dificuldade',
      G: 'sem escola', H: 'tudo em dia',
    };
    $('estados').innerHTML = Object.keys(nomes).map((k) => `
      <button type="button" data-estado="${k}" aria-pressed="false"
              title="${nomes[k]}">${k}</button>`).join('');
  })();

  // ============================================================= inicio ===
  irPara('inicio');
})();
