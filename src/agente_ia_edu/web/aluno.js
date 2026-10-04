/* =========================================================================
   PERFIL ALUNO - PILOTO ZERO.

   O portal atual (index.html + app.js) continua intacto. Este arquivo nao o
   importa nem o altera.

   O QUE MUDOU NESTE BLOCO
   -----------------------
   Ate aqui esta tela era um prototipo: desenhava oito estados de Home a
   partir de um objeto MOCK, e a decisao pedagogica central - se o aluno pode
   comecar a tarefa da escola - era calculada AQUI, em quatro linhas de
   JavaScript sobre dados inventados.

   Agora tudo o que e pedagogico vem do servidor:

     GET  /api/v1/student/activities                      tarefas da escola
     GET  /api/v1/student/activities/{id}/readiness       pode comecar?
     POST /api/v1/student/micro-diagnostic                abre o diagnostico
     POST /api/v1/student/activities/{id}/attempt         player (ja existia)
     PUT  .../attempt/answers/{vid}                       resposta
     POST .../attempt/complete  +  .../attempt/correct    correcao
     GET  /api/v1/student/micro-diagnostic/{id}/decision  decisao nova
     GET  /api/v1/student/progress                        Meu Progresso

   O QUE AINDA E ESTATICO, E ESTA DITO NA TELA
   --------------------------------------------
     - foto / arquivo / voz: os controles nascem DESABILITADOS;
     - interpretacao de texto livre: o campo existe, e a nota diz que ainda
       nao interpreta;
     - "fatos" de Meu Progresso: SAIRAM. Eram tres frases inventadas
       ("Voce estudou 3 dias esta semana") que pareciam medidas e nao eram.

   Regra que este arquivo respeita em todo lugar: nenhum botao que nao leva a
   lugar nenhum, e nenhum numero pedagogico que nao tenha vindo do servidor.
   Em particular NAO ha aqui 0.6, 0.8 nem "3 questoes": esses cortes sao de
   PerformanceThresholdPolicy, no backend, e uma segunda copia seria uma
   segunda definicao do que significa saber alguma coisa.
   ========================================================================= */

(() => {
  'use strict';

  // ===================================================== identidade =======
  // Mesmo mecanismo DEV dos outros portais: a identidade digitada vai como
  // Bearer e o TestExternalIdentityProvider a resolve. Nao ha senha, e nao
  // ha autenticacao nova neste bloco - de proposito.
  const CHAVE = 'nucleo.aluno.identidade';
  const identidade = () => (localStorage.getItem(CHAVE) || '').trim();

  async function api(caminho, opcoes = {}) {
    const quem = identidade();
    const r = await fetch(caminho, {
      ...opcoes,
      headers: {
        'Content-Type': 'application/json',
        ...(quem ? { Authorization: `Bearer ${quem}` } : {}),
        ...(opcoes.headers || {}),
      },
    });
    if (!r.ok) {
      const erro = new Error(`${r.status} ${caminho}`);
      erro.status = r.status;
      try { erro.corpo = await r.json(); } catch (_) { /* resposta sem JSON */ }
      throw erro;
    }
    return r.status === 204 ? null : r.json();
  }

  // As tres rotas de prontidao. Valores identicos aos do CheckConstraint de
  // study_sessions (migration 064) e aos de services/readiness_route.py.
  const DIRETO = 'DIRECT';
  const DIAGNOSTICO = 'DIAGNOSTIC';
  const PREPARACAO = 'PREREQUISITE_PREPARATION';

  const app = {
    tela: 'inicio',
    aluno: null,          // {nome}
    tarefas: [],
    prontidao: null,      // resposta de /readiness
    diagnostico: null,    // {assignment_id, questions, pos, respostas}
  };

  const $ = (id) => document.getElementById(id);
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g,
    (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const aviso = (texto) => `<p class="detalhe">${esc(texto)}</p>`;

  // ==================================================== Home: entrada =====

  function pedirIdentidade(erro) {
    $('home').innerHTML = `
      <div class="contexto">
        <p class="chamada">Entrar</p>
        <p class="detalhe">Ambiente de desenvolvimento: digite sua identidade.</p>
        <form class="perguntar" id="form-entrar">
          <label class="perguntar-rotulo" for="campo-identidade">Identidade</label>
          <div class="perguntar-linha">
            <input class="perguntar-campo" id="campo-identidade" type="text"
                   autocomplete="off" placeholder="student:aluno_teste_a">
          </div>
          <button class="botao botao-principal" type="submit">Entrar</button>
        </form>
        ${erro ? aviso(erro) : ''}
      </div>`;
    const form = $('form-entrar');
    form.addEventListener('submit', (e) => {
      e.preventDefault();
      const valor = $('campo-identidade').value.trim();
      if (!valor) return;
      localStorage.setItem(CHAVE, valor);
      pintarHome();
    });
  }

  function carregando() {
    $('home').innerHTML = `<div class="contexto">${aviso('Carregando…')}</div>`;
  }

  // ===================================================== Home: estados ====

  function nomeDoAluno() {
    const bruto = identidade().replace(/^student:/, '');
    return (app.aluno && app.aluno.nome) || bruto;
  }

  function cartaoSemTarefa() {
    return `
      <div class="contexto">
        <p class="saudacao">Olá, ${esc(nomeDoAluno())} 👋</p>
        <span class="selo selo-bom">Tudo em dia</span>
        <p class="chamada">Nenhuma tarefa pendente 🎉</p>
        <p class="detalhe">Quando sua escola enviar uma atividade, ela aparece aqui.</p>
      </div>`;
  }

  function cartaoDaTarefa(t, p) {
    // O cartao muda conforme a PRONTIDAO - que veio do servidor, nao daqui.
    const prazo = t.due_at
      ? `<span class="selo selo-prazo">Entrega ${esc(t.due_at.slice(0, 10))}</span>` : '';

    if (p && p.readiness_route === DIAGNOSTICO && p.target_content_code) {
      return `
        <div class="contexto">
          <p class="saudacao">Olá, ${esc(nomeDoAluno())} 👋</p>
          ${prazo}
          <p class="chamada">Você tem <strong>${esc(t.title)}</strong>.</p>
          <p class="detalhe">Antes de começar, três perguntas rápidas sobre
             <strong>${esc(p.target_content_name)}</strong> — assim eu descubro
             por onde te ajudar. Isto não vale nota.</p>
          <button class="botao botao-principal" data-acao="diagnosticar">Responder</button>
        </div>`;
    }

    if (p && p.readiness_route === PREPARACAO && p.target_content_code) {
      return `
        <div class="contexto">
          <p class="saudacao">Olá, ${esc(nomeDoAluno())} 👋</p>
          ${prazo}
          <p class="chamada">Você tem <strong>${esc(t.title)}</strong>.</p>
          <p class="detalhe"><strong>${esc(p.target_content_name)}</strong> vem antes
             dela. Vamos firmar isso primeiro — a atividade continua te esperando.</p>
          <button class="botao botao-principal" data-acao="diagnosticar">Começar por aí</button>
        </div>`;
    }

    if (p && p.readiness_route === DIRETO) {
      return `
        <div class="contexto">
          <p class="saudacao">Olá, ${esc(nomeDoAluno())} 👋</p>
          ${prazo}
          <span class="selo selo-bom">Pronto para começar</span>
          <p class="chamada">Você tem <strong>${esc(t.title)}</strong>.</p>
          <p class="detalhe">${t.question_count} ${t.question_count === 1 ? 'questão' : 'questões'}.</p>
          <button class="botao botao-principal" data-acao="abrir-tarefa"
                  data-id="${esc(t.assignment_id)}">Começar</button>
        </div>`;
    }

    // Prontidao pede diagnostico mas nao ha o que perguntar: dizemos isso em
    // vez de oferecer um botao que nao faz nada.
    return `
      <div class="contexto">
        <p class="saudacao">Olá, ${esc(nomeDoAluno())} 👋</p>
        ${prazo}
        <p class="chamada">Você tem <strong>${esc(t.title)}</strong>.</p>
        <p class="indisponivel">Ainda não sei o que esta atividade exige, então
           não vou te mandar para dentro dela às cegas. Avise sua escola.</p>
      </div>`;
  }

  async function pintarHome() {
    if (!identidade()) return pedirIdentidade();
    carregando();
    try {
      const lista = await api('/api/v1/student/activities');
      app.tarefas = lista.items || [];
      if (!app.tarefas.length) {
        $('home').innerHTML = cartaoSemTarefa();
        return;
      }
      const t = app.tarefas[0];
      app.prontidao = await api(
        `/api/v1/student/activities/${t.assignment_id}/readiness`);
      $('home').innerHTML = cartaoDaTarefa(t, app.prontidao);
    } catch (e) {
      if (e.status === 401 || e.status === 403) {
        localStorage.removeItem(CHAVE);
        return pedirIdentidade('Não reconheci essa identidade. Tente de novo.');
      }
      $('home').innerHTML = `
        <div class="contexto">
          <p class="chamada">Não consegui carregar suas tarefas.</p>
          ${aviso(`Erro ${e.status || ''}. Tente recarregar a página.`)}
        </div>`;
    }
  }

  // ================================================== o microdiagnostico ==

  async function abrirDiagnostico() {
    const p = app.prontidao;
    if (!p || !p.target_content_code) return;
    $('bloco').innerHTML = aviso('Preparando…');
    irPara('sessao');

    let d;
    try {
      d = await api('/api/v1/student/micro-diagnostic', {
        method: 'POST',
        body: JSON.stringify({
          content_code: p.target_content_code,
          objective_assignment_id: p.assignment_id,
        }),
      });
    } catch (e) {
      $('bloco').innerHTML = `<div class="cartao-bloco">
        <p class="bloco-titulo">Não deu para começar agora</p>
        ${aviso((e.corpo && e.corpo.message) || 'Tente de novo mais tarde.')}
      </div>`;
      return;
    }

    if (!d.sufficient) {
      // O banco nao tem questoes suficientes. Dizer isso e honesto; concluir
      // "voce esta pronto" a partir de uma questao nao seria.
      const s = d.selection || {};
      $('bloco').innerHTML = `<div class="cartao-bloco">
        <p class="bloco-etiqueta">Ainda não dá</p>
        <p class="bloco-titulo">Não tenho perguntas suficientes sobre isso</p>
        ${aviso(`Precisava de ${s.requested_questions ?? d.question_count}, `
                + `tenho ${s.available_questions ?? 0}. Não vou adivinhar seu nível `
                + `com menos que isso.`)}
        <button class="botao botao-secundario" data-acao="inicio">Voltar</button>
      </div>`;
      return;
    }

    const estado = await api(
      `/api/v1/student/activities/${d.assignment_id}/attempt`, { method: 'POST' });

    app.diagnostico = {
      assignment_id: d.assignment_id,
      content_code: d.content_code,
      objetivo: p,
      titulo: d.title,
      instrucoes: d.instructions,
      questoes: estado.questions || [],
      pos: 0,
      escolhas: {},
    };
    pintarSessao();
  }

  function pintarSessao() {
    const d = app.diagnostico;
    if (!d) { $('bloco').innerHTML = ''; return; }

    // O objetivo continua visivel o tempo todo: o aluno esta se PREPARANDO
    // para a tarefa da escola, nao trocando de tarefa.
    const objetivo = d.objetivo && d.objetivo.title;
    $('objetivo').hidden = !objetivo;
    if (objetivo) $('objetivo-texto').textContent = objetivo;

    const total = d.questoes.length;
    $('trilho').innerHTML = d.questoes.map((_, i) => {
      const classe = i < d.pos ? 'passo passo-feito'
        : i === d.pos ? 'passo passo-agora' : 'passo';
      return `<span class="${classe}"></span>`;
    }).join('');

    if (d.pos >= total) return pintarResultado();

    $('sessao-resumo').textContent =
      `Pergunta ${d.pos + 1} de ${total} · isto não vale nota`;

    const q = d.questoes[d.pos];
    const escolhida = d.escolhas[q.question_version_id];
    // O player chama a letra de `key` (nao `option_key`, que e o nome da
    // coluna no banco). Com o nome errado as alternativas saem com
    // data-opcao="" e o botao nunca habilita - sem nenhum erro no console.
    const alternativas = (q.options || []).map((o) => `
      <button class="alternativa${escolhida === o.key ? ' alternativa-escolhida' : ''}"
              type="button" data-opcao="${esc(o.key)}">
        <span class="alternativa-letra">${esc(o.key)}</span>
        <span>${esc(o.text)}</span>
      </button>`).join('');

    $('bloco').innerHTML = `
      <div class="cartao-bloco">
        <p class="bloco-etiqueta">${esc(d.titulo || 'Vamos ver onde você está')}</p>
        <p class="bloco-enunciado">${esc(q.statement || '')}</p>
        <div class="alternativas">${alternativas}</div>
        <button class="botao botao-principal" data-acao="avancar"
                ${escolhida ? '' : 'disabled'}>
          ${d.pos + 1 === total ? 'Concluir' : 'Próxima'}
        </button>
      </div>`;
  }

  async function escolher(opcao) {
    const d = app.diagnostico;
    const q = d.questoes[d.pos];
    d.escolhas[q.question_version_id] = opcao;
    pintarSessao();                       // resposta aparece marcada na hora
    try {
      await api(`/api/v1/student/activities/${d.assignment_id}`
                + `/attempt/answers/${q.question_version_id}`,
                { method: 'PUT', body: JSON.stringify({ selected_option: opcao }) });
    } catch (_) {
      // autosave falhou: a escolha continua na tela, e o backend valida de
      // novo na conclusao - que e quem de fato recusa resposta faltando.
    }
  }

  async function avancar() {
    const d = app.diagnostico;
    d.pos += 1;
    if (d.pos < d.questoes.length) return pintarSessao();

    $('bloco').innerHTML = aviso('Corrigindo…');
    try {
      await api(`/api/v1/student/activities/${d.assignment_id}/attempt/complete`,
                { method: 'POST' });
      await api(`/api/v1/student/activities/${d.assignment_id}/attempt/correct`,
                { method: 'POST' });
      d.decisao = await api(
        `/api/v1/student/micro-diagnostic/${d.assignment_id}/decision`
        + `?content_code=${encodeURIComponent(d.content_code)}`);
      // A prontidao e relida do estado NOVO - nao reaproveitamos a de antes.
      if (d.objetivo) {
        app.prontidao = await api(
          `/api/v1/student/activities/${d.objetivo.assignment_id}/readiness`);
      }
    } catch (e) {
      $('bloco').innerHTML = `<div class="cartao-bloco">
        <p class="bloco-titulo">Não consegui concluir</p>
        ${aviso((e.corpo && e.corpo.message) || `Erro ${e.status || ''}.`)}
      </div>`;
      return;
    }
    pintarResultado();
  }

  function pintarResultado() {
    const d = app.diagnostico;
    const dec = d.decisao || {};
    const rota = app.prontidao && app.prontidao.readiness_route;
    $('sessao-resumo').textContent = 'Pronto';

    // A decisao e sobre o conteudo DIAGNOSTICADO; a rota e sobre a ATIVIDADE.
    // Elas nao coincidem, e a tela nao pode fingir que sim: dominar o
    // pre-requisito nao produz evidencia nenhuma sobre o conteudo da tarefa.
    // Dizer "voce esta pronto para a atividade" e nao oferecer caminho nenhum
    // foi o primeiro defeito que esta tela mostrou no navegador.
    const proximo = app.prontidao && app.prontidao.target_content_name;
    const mensagem = {
      PROCEED_TO_ACTIVITY: rota === DIRETO
        ? 'Você está pronto para a atividade.'
        : `Essa parte você sabe. Agora falta ${proximo || 'o resto'}.`,
      PREPARE_PREREQUISITE: 'Vamos firmar essa base antes de seguir.',
      INSUFFICIENT_EVIDENCE: 'Ainda não deu para concluir — precisamos de mais um pouco.',
    }[dec.decision] || 'Resposta registrada.';

    const seguir = (rota === DIRETO && d.objetivo)
      ? `<button class="botao botao-principal" data-acao="abrir-tarefa"
                 data-id="${esc(d.objetivo.assignment_id)}">Ir para a atividade</button>`
      : (app.prontidao && app.prontidao.target_content_code
          ? '<button class="botao botao-principal" data-acao="diagnosticar">Continuar</button>'
          : '');

    $('bloco').innerHTML = `
      <div class="cartao-bloco">
        <p class="bloco-etiqueta">Diagnóstico concluído</p>
        <p class="bloco-titulo">${esc(mensagem)}</p>
        <p class="detalhe">Isto não vale nota e não conta como atividade
           entregue — serve só para eu saber por onde te ajudar.</p>
        ${seguir}
        <button class="botao botao-secundario" data-acao="inicio">Voltar ao início</button>
      </div>`;
  }

  // ===================================================== a atividade ======

  async function abrirTarefa(assignmentId) {
    irPara('sessao');
    $('objetivo').hidden = true;
    $('trilho').innerHTML = '';
    $('sessao-resumo').textContent = '';
    try {
      const d = await api(`/api/v1/student/activities/${assignmentId}`);
      $('bloco').innerHTML = `
        <div class="cartao-bloco">
          <p class="bloco-etiqueta">Atividade da escola</p>
          <p class="bloco-titulo">${esc(d.title)}</p>
          <p class="detalhe">${d.question_count} ${d.question_count === 1 ? 'questão' : 'questões'}.</p>
          <p class="indisponivel">${esc(
            (d.entry_screen && d.entry_screen.note)
            || 'A resolução da atividade ainda não está disponível aqui.')}</p>
          <button class="botao botao-secundario" data-acao="inicio">Voltar</button>
        </div>`;
    } catch (e) {
      $('bloco').innerHTML = `<div class="cartao-bloco">
        <p class="bloco-titulo">Não consegui abrir a atividade</p>
        ${aviso(`Erro ${e.status || ''}.`)}
      </div>`;
    }
  }

  // ===================================================== progresso ========
  // Tudo aqui vem de GET /api/v1/student/progress, que ja devolve as faixas
  // prontas, traduzidas de PerformanceThresholdPolicy - a unica fonte dos
  // cortes no sistema. A tela so desenha o que recebe.

  async function pintarProgresso() {
    if (!identidade()) { $('fatos').innerHTML = ''; return; }
    $('fatos').innerHTML = '';
    $('panorama-corpo').innerHTML = aviso('Carregando…');
    try {
      const j = await api('/api/v1/student/progress');
      const faixas = j.faixas || [];
      if (!faixas.length) {
        $('fatos').innerHTML =
          '<li>Assim que você responder alguma coisa, seu progresso aparece aqui.</li>';
        $('panorama-corpo').innerHTML = '';
        return;
      }
      $('fatos').innerHTML = faixas.map((f) => `
        <li><strong>${esc(f.faixa)}</strong>: ${esc((f.itens || []).join(', '))}</li>`).join('');
      $('panorama-corpo').innerHTML = faixas.map((f) => `
        <div class="faixa">
          <h3>${esc(f.faixa)}</h3>
          <p>${esc((f.itens || []).join(', '))}</p>
        </div>`).join('');
    } catch (e) {
      // Nao inventa faixa nenhuma. Dizer "Consolidado" sem ter lido o
      // dominio seria pior que nao dizer nada.
      $('fatos').innerHTML = '';
      $('panorama-corpo').innerHTML = aviso('Não consegui carregar seu panorama agora.');
    }
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
    window.scrollTo(0, 0);
  }

  // ===================================================== acoes ============
  document.addEventListener('click', (e) => {
    const alvo = e.target.closest('[data-acao], [data-opcao], [data-tela], [data-fechar-folha]');
    if (!alvo) return;

    if (alvo.dataset.opcao !== undefined) { escolher(alvo.dataset.opcao); return; }

    switch (alvo.dataset.acao) {
      case 'diagnosticar': abrirDiagnostico(); return;
      case 'avancar': avancar(); return;
      case 'abrir-tarefa': abrirTarefa(alvo.dataset.id); return;
      case 'inicio': app.diagnostico = null; irPara('inicio'); return;
      case 'sair': localStorage.removeItem(CHAVE); irPara('inicio'); return;
      default: break;
    }

    if (alvo.dataset.tela) { irPara(alvo.dataset.tela); return; }
    if (alvo.dataset.fecharFolha !== undefined) {
      alvo.closest('dialog').close();
    }
  });

  $('btn-perfil').addEventListener('click', () => {
    $('perfil-nome').textContent = `${nomeDoAluno()} · aluno`;
    $('folha-perfil').showModal();
  });

  $('btn-sair-sessao').addEventListener('click', () => {
    app.diagnostico = null;
    irPara('inicio');
  });

  $('form-perguntar').addEventListener('submit', (e) => e.preventDefault());

  irPara('inicio');
})();
