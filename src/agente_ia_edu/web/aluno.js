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
    atividade: null,      // a tarefa da escola em andamento
    estudo: null,         // a explicacao aberta {material_id, secoes, exemplo}
    guiada: null,         // a pratica guiada aberta {dados, escolha, ultimo}
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

  // O NOME da pessoa, da mesma rota que o Portal usa. Sem isto a Home dizia
  // "Ola, aluno_teste_a" - o identificador tecnico no lugar do nome, e o nome
  // estava em `persons.full_name` o tempo todo.
  //
  // Falhar aqui nao bloqueia nada: `nomeDoAluno` ja cai no identificador.
  async function carregarNome() {
    if (app.aluno && app.aluno.nome) return;
    try {
      const d = await api('/api/v1/portal/overview');
      if (d && d.user && d.user.name) app.aluno = { nome: d.user.name };
    } catch (_) { /* sem nome, segue com o identificador */ }
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

  // ================================== o cartao OBEDECE o proximo passo ====
  // Nada aqui decide pedagogia. `next_step.kind` vem do backend
  // (services/proximo_passo.py) e esta funcao so escolhe as palavras.
  // Cada tipo de passo sabe que ACAO disparar. O TEXTO do botao nao mora
  // aqui: vem de `next_step.cta`, calculado no backend a partir de
  // (tipo x estado).
  //
  // Antes havia um rotulo fixo por tipo - DIAGNOSTIC era sempre "Responder" -
  // e por isso, depois de acertar as tres perguntas, a tela dizia
  // "DIAGNOSTICO CONCLUIDO" com um botao convidando a RESPONDER de novo.
  const ACOES = {
    DIAGNOSTIC: { acao: 'diagnosticar' },
    LEARN: { acao: 'estudar' },
    GUIDED_PRACTICE: { acao: 'guiada' },
    PRACTICE: { acao: 'praticar' },
    VERIFY: { acao: 'verificar' },
    // ESCALONAMENTO nao tem acao do sistema: o proximo movimento e procurar o
    // professor, e nao ha botao que faca isso por ele. O cartao mostra o texto
    // e um caminho de volta, sem prometer um aviso que ninguem recebe.
    ESCALATE: { acao: null },
    ACTIVITY: { acao: 'abrir-tarefa' },
  };

  // O backend e a autoridade sobre o rotulo. O fallback existe so para a tela
  // nao ficar com um botao vazio se o campo faltar.
  //
  // `aposConcluir` e CONTEXTO DE TELA, nao decisao pedagogica: o destino
  // continua sendo o `next_step` que o backend calculou. O que muda e o verbo.
  // Na Home, "Responder diagnostico" descreve o que vai acontecer. Logo
  // depois de o aluno terminar uma etapa, a mesma frase soa como se ele
  // tivesse de refazer o que acabou de fazer - ali ele esta SEGUINDO.
  // DEPOIS DE CONCLUIR, "Continuar" so vale se o proximo passo for MAIS DO
  // MESMO. Quando o assunto muda - o aluno firmou a base e agora vai ser
  // medido no conteudo da atividade - "Continuar" esconde justamente o que
  // ele precisa saber, e foi a desorientacao relatada no teste humano.
  //
  // `feito` e o tipo da etapa que acabou. Mesmo tipo: "Continuar" (dizer
  // "Responder diagnostico" a quem acabou de responder um soa como refazer).
  // Tipo diferente: o rotulo do backend, que diz para onde se vai.
  const rotuloDaAcao = (passo, { aposConcluir = false, feito = null } = {}) => {
    if (aposConcluir && (!feito || feito === passo.kind)) return 'Continuar';
    return passo.cta || 'Continuar';
  };

  // DEPOIS DA ENTREGA, "antes de começar" e mentira.
  //
  // Quem vai mal continua recebendo reforco - isso e certo e desejado. O que
  // estava errado era a moldura: o texto apresentava o passo como preparacao
  // PARA uma atividade que ja tinha sido entregue, logo abaixo do selo
  // "Entregue" e de uma jornada marcando "Resultado".
  function explicacao(passo, tarefa, entregue) {
    const alvo = esc(passo.content_name);
    const para = passo.for_content_name ? esc(passo.for_content_name) : null;
    if (entregue && (passo.kind === 'DIAGNOSTIC' || passo.kind === 'PRACTICE')) {
      return passo.kind === 'PRACTICE'
        ? `Você já entregou. Pelo que vi nas suas respostas, vale firmar
           <strong>${alvo}</strong>.`
        : `Você já entregou. Ainda não sei o quanto você sabe de
           <strong>${alvo}</strong> — três perguntas rápidas, não vale nota.`;
    }
    if (passo.kind === 'DIAGNOSTIC') {
      return para && para !== alvo
        ? `Antes de começar, três perguntas rápidas sobre <strong>${alvo}</strong>
           — é a base de ${para}. Isto não vale nota.`
        : `Antes de começar, três perguntas rápidas sobre <strong>${alvo}</strong>.
           Isto não vale nota.`;
    }
    if (passo.kind === 'LEARN') {
      // O motivo vem do Assessor (backend). O texto generico so existe para a
      // tela nao ficar muda se o campo faltar - nunca para inventar
      // explicacao pedagogica aqui.
      const inter = passo.intervention || {};
      if (inter.reason) return esc(inter.reason);
      return para && para !== alvo
        ? `Antes de seguir, vamos entender <strong>${alvo}</strong> — é a base
           de ${para}.`
        : `Antes de seguir, vamos entender <strong>${alvo}</strong>.`;
    }
    if (passo.kind === 'PRACTICE') {
      return para && para !== alvo
        ? `Vamos firmar <strong>${alvo}</strong> antes de seguir — é a base
           de ${para}. A atividade continua te esperando.`
        : `Vamos praticar <strong>${alvo}</strong> um pouco.`;
    }
    // VERIFICACAO e ESCALONAMENTO falam pela voz do backend. Escrever aqui um
    // texto proprio para eles seria repetir o achado 3 do teste humano, que
    // foi exatamente pedagogia montada no navegador.
    if (passo.kind === 'VERIFY' || passo.kind === 'ESCALATE') {
      const fb = passo.feedback || {};
      return esc(fb.detalhe || fb.titulo || '');
    }
    if (passo.kind === 'ACTIVITY') {
      if (passo.state === 'COMPLETED') return 'Você já entregou esta atividade.';
      if (passo.state === 'IN_PROGRESS') return 'Você começou e ainda não entregou.';
      return `${tarefa.question_count} ${tarefa.question_count === 1 ? 'questão' : 'questões'}.`;
    }
    return '';
  }

  function cartaoDaTarefa(t, p) {
    const prazo = t.due_at
      ? `<span class="selo selo-prazo">Entrega ${esc(t.due_at.slice(0, 10))}</span>` : '';
    const passo = (p && p.next_step) || { kind: 'NONE' };
    const cfg = ACOES[passo.kind];
    const ola = `<p class="saudacao">Olá, ${esc(nomeDoAluno())} 👋</p>`;

    // ESCALONAMENTO: o unico passo cujo proximo movimento nao e do sistema.
    //
    // Nao ha botao, porque nao ha nada que o sistema possa fazer por ele
    // aqui - e um botao que nao leva a lugar nenhum e pior que nenhum. O
    // texto e do backend, e NAO afirma que alguem foi avisado: nao existe
    // tela de professor que receba isso, e prometer um aviso inexistente
    // deixaria o aluno esperando algo que nao vem.
    if (passo.kind === 'ESCALATE') {
      const fb = passo.feedback || {};
      return `
        <div class="contexto">
          ${ola}
          ${prazo}
          <p class="chamada">Você tem <strong>${esc(t.title)}</strong>.</p>
          <ol class="jornada jornada-cartao" aria-label="Etapas da jornada">${jornadaHTML()}</ol>
          <p class="bloco-titulo">${esc(fb.titulo || 'Vamos pedir ajuda.')}</p>
          <p class="detalhe">${esc(fb.detalhe || '')}</p>
          <button class="botao botao-secundario" data-tela="atividades">Ver minhas atividades</button>
        </div>`;
    }

    // Sem acao possivel: dizemos o motivo em vez de oferecer um botao morto.
    if (!cfg) {
      return `
        <div class="contexto">
          ${ola}
          ${prazo}
          <p class="chamada">Você tem <strong>${esc(t.title)}</strong>.</p>
          <p class="indisponivel">${esc(passo.reason
            || 'Ainda não sei o que esta atividade exige, então não vou te mandar para dentro dela às cegas.')}</p>
        </div>`;
    }

    // O selo e da ATIVIDADE, nao do proximo passo. Lido de `next_step.state`,
    // ele so existia enquanto o passo fosse ACTIVITY: quem ia mal voltava a
    // ser mandado ao diagnostico, o passo mudava de tipo, e a Home parava de
    // dizer que a atividade tinha sido entregue - enquanto "Minhas
    // atividades" continuava dizendo "Concluida".
    const estadoAtividade = (p && p.activity_state) || 'NOT_STARTED';
    const selo = estadoAtividade === 'COMPLETED'
        ? '<span class="selo selo-bom">Entregue</span>'
      : estadoAtividade === 'IN_PROGRESS'
        ? '<span class="selo selo-neutro">Em andamento</span>'
      : passo.kind === 'ACTIVITY'
        ? '<span class="selo selo-bom">Pronto para começar</span>' : '';
    const id = passo.kind === 'ACTIVITY' ? ` data-id="${esc(t.assignment_id)}"` : '';
    const codigo = passo.content_code ? ` data-conteudo="${esc(passo.content_code)}"` : '';
    // Qual explicacao abrir. Quem decide e o backend; a tela so carrega o id.
    const material = passo.material_id ? ` data-material="${esc(passo.material_id)}"` : '';

    return `
      <div class="contexto">
        ${ola}
        ${prazo}
        ${selo}
        <p class="chamada">Você tem <strong>${esc(t.title)}</strong>.</p>
        <ol class="jornada jornada-cartao" aria-label="Etapas da jornada">${jornadaHTML()}</ol>
        ${trilhaHTML()}
        <p class="detalhe">${explicacao(passo, t, estadoAtividade === 'COMPLETED')}</p>
        <button class="botao botao-principal" data-acao="${cfg.acao}"${id}${codigo}${material}>${esc(rotuloDaAcao(passo))}</button>
      </div>`;
  }

  async function pintarHome() {
    if (!identidade()) return pedirIdentidade();
    carregando();
    await carregarNome();
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
      // `next_step.state` ja diz se a atividade foi entregue, esta em
      // andamento ou nem comecou - antes isto custava uma requisicao extra
      // so para descobrir o rotulo do botao.
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

  // ===================================================== a jornada =======
  // Desenha as etapas vindas de `readiness.journey`. Nao calcula nada: se o
  // frontend decidisse o que esta concluido, bastaria o aluno abrir uma tela
  // para a barra ficar verde.
  function jornadaHTML() {
    const etapas = (app.prontidao && app.prontidao.journey) || [];
    if (!etapas.length) return '';
    return etapas.map((e) => {
      const classe = e.state === 'COMPLETED' ? 'etapa etapa-feita'
        : e.state === 'IN_PROGRESS' ? 'etapa etapa-agora' : 'etapa';
      // O estado tambem e TEXTO, nao so cor e posicao.
      const marca = e.state === 'COMPLETED' ? '✓'
        : e.state === 'IN_PROGRESS' ? '●' : '○';
      const dito = e.state === 'COMPLETED' ? 'concluída'
        : e.state === 'IN_PROGRESS' ? 'etapa atual' : 'a seguir';
      return `<li class="${classe}" aria-current="${e.state === 'IN_PROGRESS' ? 'step' : 'false'}">
                <span class="etapa-marca" aria-hidden="true">${marca}</span>
                <span class="etapa-nome">${esc(e.label)}</span>
                <span class="sr">(${dito})</span>
              </li>`;
    }).join('');
  }

  // DENTRO DA PREPARACAO, ONDE ELE ESTA.
  //
  // A barra de quatro etapas esta certa, e o teste humano de 2026-10-05
  // mostrou que ela nao basta: ele ficava em "Preparacao ●" sem perceber o
  // que mudava de uma volta para a outra. A trilha abaixo mostra o sub-passo
  // atual - e so aparece DENTRO da preparacao, porque so la ela existe.
  function trilhaHTML() {
    const passo = (app.prontidao && app.prontidao.next_step) || {};
    const subs = PrepUI.subpassos(passo);
    if (!subs.length) return '';
    const itens = subs.map((s) => {
      const classe = s.atual ? 'subpasso subpasso-agora'
        : s.cumprido ? 'subpasso subpasso-feito' : 'subpasso';
      const dito = s.atual ? 'agora' : s.cumprido ? 'feito' : 'a seguir';
      return `<li class="${classe}"
                  aria-current="${s.atual ? 'step' : 'false'}">
                <span class="subpasso-nome">${esc(s.rotulo)}</span>
                <span class="sr">(${dito})</span>
              </li>`;
    }).join('');
    return `<p class="trilha-titulo">Preparação</p>
            <ol class="trilha">${itens}</ol>`;
  }

  function pintarJornada() {
    const el = $('jornada');
    if (!el) return;
    const html = jornadaHTML();
    el.innerHTML = html;
    el.hidden = !html;
    const trilha = $('trilha');
    if (trilha) {
      const t = trilhaHTML();
      trilha.innerHTML = t;
      trilha.hidden = !t;
    }
  }

  // ============================================ o assessor pedagogico =====
  //
  // A tela de ENSINO. Ela nao decide nada: o backend ja disse que o passo e
  // LEARN, qual material abrir e por que. Aqui so se desenha.
  //
  // NAO E UM CHAT. O aluno nao precisa descobrir o que perguntar para receber
  // ajuda - a ajuda vem estruturada: o que esta travando, a ideia, um exemplo
  // ate o fim, e entao a tentativa.

  async function estudar(materialId) {
    const passo = (app.prontidao && app.prontidao.next_step) || {};
    const id = materialId || passo.material_id;
    if (!id) return praticar(passo.content_code);
    irPara('sessao');
    $('bloco').innerHTML = aviso('Abrindo…');
    try {
      const [material, secoes] = await Promise.all([
        api(`/api/v1/student/materials/${id}`),
        api(`/api/v1/student/materials/${id}/sections`),
      ]);
      app.estudo = {
        material_id: id,
        titulo: material.title,
        secoes,
        // Qual passo do exemplo resolvido ja foi revelado. Mostrar os tres de
        // uma vez seria entregar a resposta pronta; o aluno acompanha o
        // raciocinio quando ele chega em partes.
        exemplo: 0,
      };
      pintarEstudo();
    } catch (e) {
      $('bloco').innerHTML = `<div class="cartao-bloco">
        <p class="bloco-titulo">Não consegui abrir a explicação agora</p>
        ${aviso(`Erro ${esc(e.status || '')}. Você pode praticar enquanto isso.`)}
        <button class="botao botao-secundario" data-acao="praticar"
                data-conteudo="${esc(passo.content_code || '')}">Praticar agora</button>
      </div>`;
    }
  }

  // O exemplo resolvido: cada passo traz a equacao, a fala e a CONTAGEM de
  // atomos - que vem do backend, derivada da propria equacao por
  // `chemistry_balance`. Nenhum destes numeros foi escrito a mao nesta tela.
  function exemploHTML(bloco, revelados) {
    const passos = ((bloco.metadata || {}).passos) || [];
    if (!passos.length) return '';
    const vistos = passos.slice(0, Math.max(1, revelados + 1));
    const corpo = vistos.map((p) => {
      const linhas = Object.entries(p.contagem || {}).map(([el, par]) => {
        const esq = par[0];
        const dir = par[1];
        return `
        <tr class="${esq === dir ? 'atomo-fecha' : 'atomo-aberto'}">
          <th scope="row">${esc(el)}</th>
          <td>${esq}</td>
          <td>${dir}</td>
          <td>${esq === dir ? 'fecha' : 'não fecha'}</td>
        </tr>`;
      }).join('');
      return `
        <li class="passo-exemplo">
          <p class="equacao">${esc(p.equacao_exibicao || p.equacao)}</p>
          <table class="contagem">
            <caption class="sr">Átomos de cada elemento nos dois lados</caption>
            <thead><tr><th scope="col">Elemento</th><th scope="col">Entra</th>
              <th scope="col">Sai</th><th scope="col">Situação</th></tr></thead>
            <tbody>${linhas}</tbody>
          </table>
          <p class="fala">${esc(p.fala)}</p>
        </li>`;
    }).join('');
    const faltam = passos.length - vistos.length;
    const proximo = faltam > 0
      ? `<button class="botao botao-secundario" data-acao="passo-exemplo"
                 data-passo="${vistos.length}">Ver o próximo passo</button>`
      : '';
    return `
      <h3 class="bloco-titulo">${esc(bloco.title || 'Exemplo')}</h3>
      <ol class="exemplo">${corpo}</ol>
      ${proximo}`;
  }

  function blocoHTML(bloco, revelados) {
    if (bloco.block_type === 'SOLVED_EXAMPLE') return exemploHTML(bloco, revelados);
    const corpo = (bloco.body || '').split('\n\n').map(
      (par) => `<p>${esc(par)}</p>`).join('');
    const classe = bloco.block_type === 'CALLOUT' ? 'aviso-conceito' : 'bloco-texto';
    return `<div class="${classe}">
      ${bloco.title ? `<h3 class="bloco-titulo">${esc(bloco.title)}</h3>` : ''}
      ${corpo}
    </div>`;
  }

  function pintarEstudo() {
    const e = app.estudo;
    if (!e) return;
    const passo = (app.prontidao && app.prontidao.next_step) || {};
    const inter = passo.intervention || {};
    $('trilho').innerHTML = '';
    $('sessao-resumo').textContent = '';
    pintarJornada();

    // O OBJETIVO FICA NA TELA. O aluno nao pode entrar em Balanceamento e
    // esquecer que estava indo para a atividade de Estequiometria - foi a
    // desorientacao relatada no teste humano.
    const alvo = (app.prontidao && app.prontidao.title) || null;
    $('objetivo').hidden = !alvo;
    if (alvo) $('objetivo-texto').textContent = alvo;

    // POR QUE ESTOU ESTUDANDO ISSO. O texto e do backend; esta tela nao
    // inventa explicacao pedagogica.
    const porque = inter.reason
      ? `<p class="assessor-fala">${esc(inter.reason)}</p>` : '';
    const objetivo = inter.learning_objective
      ? `<p class="assessor-meta"><strong>O que você leva daqui:</strong>
         ${esc(inter.learning_objective)}</p>` : '';
    const depois = inter.next_check
      ? `<p class="assessor-meta"><strong>Depois disso:</strong>
         ${esc(inter.next_check)}</p>` : '';

    const secoes = (e.secoes || []).map((s) => `
      <section class="secao-estudo">
        <h2>${esc(s.title || '')}</h2>
        ${(s.blocks || []).map((b) => blocoHTML(b, e.exemplo)).join('')}
      </section>`).join('');

    $('bloco').innerHTML = `
      <div class="cartao-bloco cartao-assessor">
        <p class="bloco-etiqueta assessor-etiqueta">
          <img class="assessor-marca" src="assets/nucleo-edu-360-simbolo.png"
               alt="" width="128" height="108" aria-hidden="true">
          Assessor Pedagógico
        </p>
        ${porque}
        ${objetivo}
        ${secoes}
        ${depois}
        <button class="botao botao-principal" data-acao="entendi">
          Entendi, vamos praticar
        </button>
      </div>`;
    window.scrollTo(0, 0);
  }

  // Revelar um passo do exemplo tambem GRAVA a posicao - e assim que o sistema
  // sabe que o aluno abriu a explicacao e parou no meio. Gravar posicao nao e
  // evidencia de dominio: `MaterialProgress` nao toca no mapa de dominio.
  function passoDoExemplo(indice) {
    const e = app.estudo;
    if (!e) return;
    e.exemplo = indice;
    pintarEstudo();
    marcarLeitura({ ateOFim: false });
  }

  async function marcarLeitura({ ateOFim }) {
    const e = app.estudo;
    if (!e) return;
    const secoes = e.secoes || [];
    const alvo = ateOFim ? secoes[secoes.length - 1] : secoes[0];
    if (!alvo) return;
    try {
      await api(`/api/v1/student/materials/${e.material_id}/progress`, {
        method: 'PUT',
        body: JSON.stringify({ current_section_id: alvo.section_id,
                               completed: !!ateOFim }),
      });
    } catch (_) { /* a posicao e conveniencia: perde-la nao bloqueia o estudo */ }
  }

  // "Entendi" marca a explicacao como concluida e leva a pratica. NAO marca
  // dominio: quem decide isso continua sendo a evidencia da pratica.
  async function concluirEstudo() {
    await marcarLeitura({ ateOFim: true });
    app.estudo = null;
    // QUEM DIZ O QUE VEM DEPOIS E O BACKEND.
    //
    // Esta funcao chamava `abrirGuiada` direto, assumindo que depois de
    // entender vem sempre tentar com ajuda. No SEGUNDO ciclo isso reabria a
    // guiada ja concluida - com a resposta a mostra, e sem tentativa
    // nenhuma. A tela tinha decidido o percurso.
    return seguirOProximoPasso();
  }

  // Releia a prontidao e va para onde ela mandar. Serve a qualquer ponto em
  // que o estado acabou de mudar e o proximo passo pode ter mudado junto.
  async function seguirOProximoPasso() {
    if (app.prontidao && app.prontidao.assignment_id) {
      try {
        app.prontidao = await api(
          `/api/v1/student/activities/${app.prontidao.assignment_id}/readiness`);
      } catch (_) { /* sem prontidao nova, segue com a que ha */ }
    }
    const passo = (app.prontidao && app.prontidao.next_step) || {};
    switch (passo.kind) {
      case 'LEARN': return estudar(passo.material_id);
      case 'GUIDED_PRACTICE': return abrirGuiada(passo.content_code);
      case 'PRACTICE': return praticar(passo.content_code);
      // A VERIFICACAO e uma pratica CURTA, pelo mesmo motor - nao ha segundo
      // motor de questoes. O que muda e o tamanho e o que a tela diz: sao
      // poucas questoes, e sao elas que decidem se ele avanca.
      case 'VERIFY': return praticar(passo.content_code,
                                     { quantas: passo.question_count || 3,
                                       verificacao: true });
      case 'ESCALATE': return irPara('inicio');
      case 'DIAGNOSTIC': return abrirDiagnostico();
      case 'ACTIVITY': return abrirTarefa(app.prontidao.assignment_id);
      default: return irPara('inicio');
    }
  }

  // ============================================== a pratica guiada ========
  //
  // O aluno TENTA, e a ajuda chega quando precisa. As decisoes de interface
  // (qual botao fica ativo, que frase aparece) estao em `aluno-guiada.js`,
  // fora deste IIFE, para poderem ser exercitadas por `node --test`.
  //
  // Nada aqui decide pedagogia: se acertou, quantas ajudas foram usadas, se
  // resolveu sozinho - tudo chega pronto do servidor, que e quem confere.

  async function abrirGuiada(contentCode, skill) {
    const passo = (app.prontidao && app.prontidao.next_step) || {};
    const codigo = contentCode || passo.content_code;
    if (!codigo) return;
    irPara('sessao');
    $('bloco').innerHTML = aviso('Preparando…');
    const inter = passo.intervention || {};
    const habilidade = skill || inter.skill;
    try {
      const dados = await api('/api/v1/student/guided-practice'
        + `?content_code=${encodeURIComponent(codigo)}`
        + (habilidade ? `&skill=${encodeURIComponent(habilidade)}` : ''));
      app.guiada = { dados, escolha: null, ultimo: null };
      pintarGuiada();
    } catch (e) {
      // Sem item guiado para este conteudo, segue a pratica comum - e e isso
      // que a tela diz, em vez de um botao sem destino.
      return praticar(codigo);
    }
  }

  function pintarGuiada() {
    const g = app.guiada;
    if (!g) return;
    const v = GuiadaUI.estado(g.dados, { escolha: g.escolha });
    $('trilho').innerHTML = '';
    $('sessao-resumo').textContent = v.resumoDaAjuda;
    pintarJornada();

    const alvo = (app.prontidao && app.prontidao.title) || null;
    $('objetivo').hidden = !alvo;
    if (alvo) $('objetivo-texto').textContent = alvo;

    const alternativas = (g.dados.options || []).map((o) => {
      const marcada = g.escolha === o.key;
      const certa = v.concluido && v.correta === o.key;
      return `
        <button class="alternativa${marcada ? ' alternativa-escolhida' : ''}${certa ? ' alternativa-certa' : ''}"
                data-opcao-guiada="${esc(o.key)}"
                type="button"${v.concluido ? ' disabled' : ''}>
          <span class="alternativa-letra">${esc(o.key)}</span>
          <span class="alternativa-texto">${esc(o.text)}</span>
          ${certa ? '<span class="alternativa-marca">✓ é esta</span>' : ''}
        </button>`;
    }).join('');

    // As ajudas ja liberadas, na ordem. O nivel aparece para o aluno saber
    // que ha uma progressao - e que ele nao esta recebendo a mesma coisa de
    // novo com outras palavras.
    const ajudas = v.ajudas.map((a) => `
      <li class="ajuda">
        <span class="ajuda-nivel">Ajuda ${a.nivel}</span>
        <p>${esc(a.texto)}</p>
      </li>`).join('');

    // UMA fonte de fala. O convite inicial ficava aqui, em HTML, fora de
    // qualquer teste - e a tela com duas vozes acabou se contradizendo
    // (pedia-se a ajuda, e ela voltava a dizer "tente primeiro").
    const fala = `<p class="assessor-fala">${
      esc(GuiadaUI.falaDoAssessor(g.dados, g.ultimo))}</p>`;

    const acoes = v.concluido
      ? `<button class="botao botao-principal" data-acao="guiada-seguir">
           ${esc(v.cta)}</button>`
      : `
        <button class="botao botao-principal" data-acao="guiada-responder"
                ${v.responderHabilitado ? '' : 'disabled'}>Responder</button>
        ${v.podePedirAjuda
          ? '<button class="botao botao-secundario" data-acao="guiada-ajuda">Quero uma dica</button>'
          : ''}`;

    $('bloco').innerHTML = `
      <div class="cartao-bloco cartao-assessor">
        <p class="bloco-etiqueta assessor-etiqueta">
          <img class="assessor-marca" src="assets/nucleo-edu-360-simbolo.png"
               alt="" width="128" height="108" aria-hidden="true">
          Vamos tentar juntos
        </p>
        ${fala}
        <p class="bloco-titulo">${esc(g.dados.question || '')}</p>
        <div class="alternativas">${alternativas}</div>
        ${ajudas ? `<ol class="ajudas">${ajudas}</ol>` : ''}
        ${v.concluido && v.fecho ? `<p class="assessor-meta">${esc(v.fecho)}</p>` : ''}
        ${acoes}
      </div>`;
  }

  function escolherGuiada(letra) {
    if (!app.guiada) return;
    app.guiada.escolha = letra;
    pintarGuiada();
  }

  async function responderGuiada() {
    const g = app.guiada;
    if (!g || !g.escolha) return;
    try {
      const r = await api(
        `/api/v1/student/guided-practice/${encodeURIComponent(g.dados.item_key)}/answer`,
        { method: 'POST', body: JSON.stringify({ selected_option: g.escolha }) });
      g.dados = r;
      g.ultimo = { correct: !!r.correct };
      // Errou: a alternativa sai desmarcada, para a proxima tentativa ser
      // uma escolha de novo e nao um clique no mesmo lugar.
      if (!r.correct) g.escolha = null;
      pintarGuiada();
    } catch (e) {
      $('bloco').innerHTML += aviso(`Não consegui registrar (erro ${esc(e.status || '')}).`);
    }
  }

  async function pedirAjudaGuiada() {
    const g = app.guiada;
    if (!g) return;
    try {
      g.dados = await api(
        `/api/v1/student/guided-practice/${encodeURIComponent(g.dados.item_key)}/hint`,
        { method: 'POST' });
      // `null` fazia a fala VOLTAR ao convite inicial ("tente primeiro por
      // conta propria") logo depois de o aluno pedir ajuda.
      g.ultimo = { pediuAjuda: true };
      pintarGuiada();
    } catch (e) {
      $('bloco').innerHTML += aviso(`Não consegui trazer a ajuda agora.`);
    }
  }

  // Concluida a guiada, o proximo passo e a pratica AUTONOMA - e quem diz
  // isso e o backend, relido aqui com o estado novo.
  async function seguirDaGuiada() {
    app.guiada = null;
    return seguirOProximoPasso();
  }

  // ================================================== o microdiagnostico ==

  // Reabre uma pratica/diagnostico que o aluno deixou pela metade.
  //
  // `next_step.resume_assignment_id` diz QUAL. Sem ele, o botao "Continuar
  // pratica" chamava POST /student/practice - que CRIA - e o aluno recomecava
  // da questao 1 numa pratica nova, com a anterior (e as respostas dentro)
  // inalcancavel.
  //
  // As escolhas vem do SERVIDOR, nao de memoria local: a aba pode ter sido
  // fechada, ou ser outra.
  //
  // E a POSICAO vem das proprias respostas, nao de `current_position`: num
  // diagnostico ou pratica o avanco e so da tela - o servidor nunca e
  // avisado - e `current_position` fica em 1 para sempre. A primeira questao
  // sem resposta e o que de fato falta fazer, inclusive se o aluno pulou uma
  // e respondeu a seguinte.
  async function retomar(assignmentId, extras) {
    const estado = await api(
      `/api/v1/student/activities/${assignmentId}/attempt`, { method: 'POST' });
    const questoes = estado.questions || [];
    const escolhas = {};
    questoes.forEach((q) => {
      if (q.selected_option) escolhas[q.question_version_id] = q.selected_option;
    });
    const falta = questoes.findIndex((q) => !q.selected_option);
    app.diagnostico = Object.assign({
      assignment_id: assignmentId,
      objetivo: app.prontidao,
      questoes,
      // tudo respondido: para na ultima, de onde se conclui
      pos: falta === -1 ? Math.max(0, questoes.length - 1) : falta,
      escolhas,
    }, extras || {});
    pintarSessao();
    return true;
  }

  // O que o backend mandou retomar neste passo, se mandou alguma coisa.
  //
  // O CONTEUDO tem de bater. "Praticar" tambem e chamado de Meu progresso com
  // um assunto escolhido pelo aluno; sem esta comparacao, pedir pratica de um
  // conteudo retomaria a pratica aberta de OUTRO.
  function aRetomar(kind, codigo) {
    const passo = (app.prontidao && app.prontidao.next_step) || {};
    if (passo.kind !== kind) return null;
    if (codigo && passo.content_code && passo.content_code !== codigo) return null;
    return passo.resume_assignment_id || null;
  }

  async function abrirDiagnostico() {
    const p = app.prontidao;
    if (!p || !p.target_content_code) return;
    $('bloco').innerHTML = aviso('Preparando…');
    irPara('sessao');

    const aberto = aRetomar('DIAGNOSTIC', p.target_content_code);
    if (aberto) return retomar(aberto, {
      content_code: p.target_content_code, titulo: 'Vamos ver onde você está',
    });

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

    pintarJornada();
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
      d.resultado = await api(
        `/api/v1/student/activities/${d.assignment_id}/attempt/correct`,
        { method: 'POST' });
      if (!d.pratica) {
        const obj = d.objetivo ? `&objective_assignment_id=${encodeURIComponent(d.objetivo.assignment_id)}` : '';
        d.decisao = await api(
          `/api/v1/student/micro-diagnostic/${d.assignment_id}/decision`
          + `?content_code=${encodeURIComponent(d.content_code)}${obj}`);
      }
      // O MAPA DE DOMINIO PRECISA SER RECONSTRUIDO ANTES DE RELER A PRONTIDAO.
      //
      // `attempt/correct` grava a evidencia, mas quem agrega evidencia em
      // dominio e o mapa - e so o DIAGNOSTICO o reconstruia, de carona em
      // `/micro-diagnostic/.../decision`. Depois de uma PRATICA ninguem
      // reconstruia, entao `/readiness` lia o estado velho e a tela oferecia
      // o passo anterior: o aluno acertava 5 de 5 e o botao continuava
      // mandando praticar o mesmo conteudo.
      if (d.pratica) {
        try {
          await api('/api/v1/student/domain/rebuild', { method: 'POST' });
        } catch (_) { /* sem rebuild a prontidao fica velha, mas nao quebra */ }
      }
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
    pintarJornada();
    const d = app.diagnostico;
    const dec = d.decisao || {};
    // O TEXTO E DO BACKEND (services/feedback_pedagogico.py). Esta funcao nao
    // escolhe palavra nenhuma sobre desempenho: ate 2026-10-04 ela montava a
    // frase num `switch` e dizia "agora falta Balanceamento" a quem acabara de
    // demonstrar Balanceamento.
    // Na PRATICA nao ha decisao de microdiagnostico - ha desempenho. O texto
    // entao reporta o que aconteceu, sem concluir nada sobre dominio: quem
    // conclui e a politica, no proximo recalculo de prontidao.
    const fb = d.pratica ? resumoDaPratica(d) : (dec.feedback || {});
    const passo = (d.pratica ? null : dec.next_step)
                  || (app.prontidao && app.prontidao.next_step) || {};
    const cfg = ACOES[passo.kind];
    // A legenda da sessao conta QUESTOES ("Pergunta 2 de 3"). Numa tela de
    // resultado nao ha questao corrente, e "Pronto" so repetia o titulo do
    // cartao logo abaixo de uma jornada que ja marca "Resultado". Vazia.
    //
    // O trilho conta as mesmas questoes e ficava desenhado aqui, com o
    // ultimo segmento aceso, como se houvesse uma pergunta na tela.
    $('sessao-resumo').textContent = '';
    $('trilho').innerHTML = '';

    // O aluno ACABOU de concluir uma etapa: o botao leva ao proximo passo,
    // com o rotulo que o backend calculou para ele.
    const seguir = cfg
      ? `<button class="botao botao-principal" data-acao="${cfg.acao}"
                 ${passo.kind === 'ACTIVITY' && d.objetivo
                   ? `data-id="${esc(d.objetivo.assignment_id)}"` : ''}
                 ${passo.content_code ? `data-conteudo="${esc(passo.content_code)}"` : ''}
                 >${esc(rotuloDaAcao(passo, { aposConcluir: true,
                        feito: d.pratica ? 'PRACTICE' : 'DIAGNOSTIC' }))}</button>`
      : '';

    $('bloco').innerHTML = `
      <div class="cartao-bloco cartao-${esc((fb.tom || 'NEUTRO').toLowerCase())}">
        <p class="bloco-etiqueta">${d.verificacao ? 'Verificação concluída'
          : d.pratica ? 'Prática concluída' : 'Diagnóstico concluído'}</p>
        <p class="bloco-titulo">${esc(fb.titulo || 'Resposta registrada.')}</p>
        ${fb.placar && fb.placar !== fb.titulo
          ? `<p class="bloco-placar">${esc(fb.placar)}</p>` : ''}
        ${fb.detalhe ? `<p class="bloco-porque">${esc(fb.detalhe)}</p>` : ''}
        <p class="detalhe">Isto não vale nota e não conta como atividade
           entregue — ${d.pratica
             ? 'serve para firmar o conteúdo e ajustar seu próximo passo.'
             : 'serve só para eu saber por onde te ajudar.'}</p>
        ${seguir}
        <button class="botao botao-secundario" data-acao="inicio">Voltar ao início</button>
      </div>`;
  }

  function resumoDaPratica(d) {
    // O ACHADO 3 DO TESTE HUMANO MORAVA AQUI.
    //
    // Esta funcao montava "Você acertou 1 de 5. Isso entra no seu progresso e
    // ajusta o próximo passo." — duas frases sobre o SISTEMA, escritas no
    // navegador. O aluno nao ficava sabendo o que foi observado, o que vem
    // agora, nem por que esse proximo passo ajuda.
    //
    // Agora a frase vem da DECISAO (`next_step.feedback`), ja recalculada
    // pelo rebuild que acontece ao finalizar. A tela acrescenta o placar, que
    // e informacao e nao conclusao, e nada mais. Sem o campo ela informa o
    // placar e PARA: inventar pedagogia aqui foi o erro que se corrigiu.
    //
    // Os numeros ficam em `result`, um nivel abaixo do envelope devolvido por
    // /attempt/correct — ler do envelope dava undefined e a tela caia no
    // texto generico "suas respostas foram registradas".
    const r = (d.resultado && d.resultado.result) || {};
    const passo = (app.prontidao && app.prontidao.next_step) || {};
    const fala = PrepUI.falaDoResultado(r, passo.feedback);
    return {
      tom: fala.tom,
      titulo: fala.titulo,
      detalhe: fala.detalhe,
      placar: fala.placar,
    };
  }

  // ======================================================= a preparacao ===
  // PRATICA de um conteudo, pelo AdaptivePracticeService que ja existe. Nao ha
  // segundo motor: e a mesma selecao que o microdiagnostico usa, com origem
  // PRACTICE em vez de MICRO_DIAGNOSTIC.
  async function praticar(contentCode, opcoes) {
    const o = opcoes || {};
    const quantas = o.quantas || 5;
    const titulo = o.verificacao ? 'Vamos confirmar' : 'Vamos praticar';
    const codigo = contentCode
      || (app.prontidao && app.prontidao.next_step && app.prontidao.next_step.content_code);
    if (!codigo) return;
    irPara('sessao');
    $('bloco').innerHTML = aviso('Preparando…');

    const aberta = aRetomar('PRACTICE', codigo);
    if (aberta) return retomar(aberta, {
      content_code: codigo, titulo: titulo, pratica: true,
      verificacao: !!o.verificacao,
    });

    let pr;
    try {
      pr = await api('/api/v1/student/practice', {
        method: 'POST',
        body: JSON.stringify({ content_code: codigo, question_count: quantas }),
      });
    } catch (e) {
      const corpo = e.corpo || {};
      $('bloco').innerHTML = `<div class="cartao-bloco">
        <p class="bloco-etiqueta">Ainda não dá</p>
        <p class="bloco-titulo">Não tenho questões suficientes sobre isso</p>
        ${aviso(corpo.available_questions !== undefined
          ? `Precisava de ${corpo.requested_questions}, tenho ${corpo.available_questions}.`
          : (corpo.message || `Erro ${e.status || ''}.`))}
        <button class="botao botao-secundario" data-acao="inicio">Voltar</button>
      </div>`;
      return;
    }

    const estado = await api(
      `/api/v1/student/activities/${pr.assignment_id}/attempt`, { method: 'POST' });
    app.diagnostico = {
      assignment_id: pr.assignment_id,
      content_code: codigo,
      objetivo: app.prontidao,
      // Na VERIFICACAO o titulo e nosso, nao o do motor de pratica: para o
      // backend as duas sao a mesma coisa (e e isso que garante que a
      // evidencia seja a mesma), mas para o aluno nao sao - ele precisa saber
      // que estas poucas questoes confirmam o que ele acabou de mostrar.
      titulo: o.verificacao ? titulo : (pr.title || titulo),
      pratica: true,
      // A VERIFICACAO so muda o que a tela diz; a evidencia e a mesma de
      // qualquer pratica, gravada porque o aluno respondeu questoes.
      verificacao: !!o.verificacao,
      questoes: estado.questions || [],
      pos: 0,
      escolhas: {},
    };
    pintarSessao();
  }

  // ===================================================== a atividade ======

  // ============================================ a ATIVIDADE DA ESCOLA =====
  // O backend ja estava completo desde a PHASE 17/18: iniciar, salvar,
  // retomar, finalizar e corrigir sao os MESMOS endpoints que o
  // microdiagnostico e a pratica usam. O que faltava era a tela.
  //
  // O texto "A resolucao da atividade sera disponibilizada em breve" era uma
  // string fixa em `entry_screen.note`, resquicio de quando o player ainda
  // nao existia. Nada no servidor recusava a tentativa - `can_start` ja vinha
  // true.
  //
  // DIFERENCA PARA O DIAGNOSTICO: aqui o aluno NAVEGA. Pode voltar, pular,
  // rever e mudar de ideia antes de finalizar - e uma tarefa da escola, nao
  // tres perguntas para o sistema decidir um caminho.

  async function abrirTarefa(assignmentId) {
    irPara('sessao');
    $('objetivo').hidden = true;
    $('bloco').innerHTML = aviso('Abrindo…');
    try {
      const estado = await api(
        `/api/v1/student/activities/${assignmentId}/attempt`, { method: 'POST' });
      app.atividade = {
        assignment_id: assignmentId,
        titulo: (await api(`/api/v1/student/activities/${assignmentId}`)).title,
        questoes: estado.questions || [],
        pos: Math.max(0, (estado.current_position || 1) - 1),
        status: estado.status,
      };
      if (app.atividade.status === 'COMPLETED') return mostrarResultadoOficial();
      pintarAtividade();
    } catch (e) {
      const corpo = e.corpo || {};
      $('bloco').innerHTML = `<div class="cartao-bloco">
        <p class="bloco-titulo">Não consegui abrir a atividade</p>
        ${aviso(corpo.message || corpo.detail
                || `Erro ${e.status || ''}. Avise sua escola.`)}
        <button class="botao botao-secundario" data-acao="inicio">Voltar</button>
      </div>`;
    }
  }

  function pintarAtividade() {
    const a = app.atividade;
    if (!a) return;
    pintarJornada();
    const total = a.questoes.length;
    const q = a.questoes[a.pos];

    $('trilho').innerHTML = a.questoes.map((x, i) => {
      const classe = x.selected_option ? 'passo passo-feito'
        : i === a.pos ? 'passo passo-agora' : 'passo';
      return `<span class="${classe}"></span>`;
    }).join('');

    const respondidas = a.questoes.filter((x) => x.selected_option).length;
    $('sessao-resumo').textContent =
      `Questão ${a.pos + 1} de ${total} · ${respondidas} respondida${respondidas === 1 ? '' : 's'}`;

    const escolhida = q.selected_option;
    const alternativas = (q.options || []).map((o) => `
      <button class="alternativa${escolhida === o.key ? ' alternativa-escolhida' : ''}"
              type="button" data-opcao-oficial="${esc(o.key)}">
        <span class="alternativa-letra">${esc(o.key)}</span>
        <span>${esc(o.text)}</span>
      </button>`).join('');

    // "Finalizar" so aparece quando TODAS foram respondidas. O backend recusa
    // de qualquer jeito; oferecer o botao antes disso seria oferecer um erro.
    const todas = respondidas === total;
    $('bloco').innerHTML = `
      <div class="cartao-bloco">
        <p class="bloco-etiqueta">${esc(a.titulo || 'Atividade da escola')}</p>
        <p class="bloco-enunciado">${esc(q.statement || '')}</p>
        <div class="alternativas">${alternativas}</div>
        <div class="navegacao-questoes">
          <button class="botao botao-secundario" data-acao="questao-anterior"
                  ${a.pos === 0 ? 'disabled' : ''}>Anterior</button>
          <button class="botao botao-secundario" data-acao="questao-proxima"
                  ${a.pos + 1 >= total ? 'disabled' : ''}>Próxima</button>
        </div>
        ${todas
          ? `<button class="botao botao-principal" data-acao="finalizar-atividade">Finalizar atividade</button>`
          : `<p class="nota">Responda todas as ${total} questões para finalizar.</p>`}
      </div>`;
  }

  async function responderOficial(opcao) {
    const a = app.atividade;
    const q = a.questoes[a.pos];
    q.selected_option = opcao;      // a escolha aparece na hora
    q.answered = true;
    pintarAtividade();
    try {
      await api(`/api/v1/student/activities/${a.assignment_id}`
                + `/attempt/answers/${q.question_version_id}`,
                { method: 'PUT', body: JSON.stringify({ selected_option: opcao }) });
    } catch (_) {
      // O backend revalida na finalizacao - e quem de fato recusa resposta
      // faltando. A escolha continua na tela.
    }
  }

  async function navegarQuestao(delta) {
    const a = app.atividade;
    const nova = Math.min(Math.max(0, a.pos + delta), a.questoes.length - 1);
    if (nova === a.pos) return;
    a.pos = nova;
    pintarAtividade();
    try {
      await api(`/api/v1/student/activities/${a.assignment_id}`
                + `/attempt/position?position=${nova + 1}`, { method: 'PUT' });
    } catch (_) {
      // posicao e so uma ajuda para retomar; perder isso nao perde resposta
    }
  }

  async function finalizarAtividade() {
    const a = app.atividade;
    $('bloco').innerHTML = aviso('Finalizando…');
    try {
      await api(`/api/v1/student/activities/${a.assignment_id}/attempt/complete`,
                { method: 'POST' });
      await api(`/api/v1/student/activities/${a.assignment_id}/attempt/correct`,
                { method: 'POST' });
      // O DOMINIO precisa ser reconstruido aqui.
      //
      // No microdiagnostico isso acontece sozinho, porque
      // /micro-diagnostic/{id}/decision reconstroi antes de decidir. A
      // atividade oficial nao tem endpoint equivalente - e sem este rebuild o
      // aluno terminava a atividade com 0 de 5 e via "Consolidado" em Meu
      // Progresso, porque a tela lia o estado anterior a correcao.
      await api('/api/v1/student/domain/rebuild', { method: 'POST' });
      // E a prontidao, que agora pode ter mudado: errar a atividade da escola
      // e evidencia como qualquer outra.
      if (app.prontidao) {
        try {
          app.prontidao = await api(
            `/api/v1/student/activities/${a.assignment_id}/readiness`);
        } catch (_) { /* a tela de resultado nao depende disto */ }
      }
    } catch (e) {
      const corpo = e.corpo || {};
      $('bloco').innerHTML = `<div class="cartao-bloco">
        <p class="bloco-titulo">Não consegui finalizar</p>
        ${aviso(corpo.message || `Erro ${e.status || ''}.`)}
        <button class="botao botao-secundario" data-acao="inicio">Voltar</button>
      </div>`;
      return;
    }
    await mostrarResultadoOficial();
  }

  async function mostrarResultadoOficial() {
    const a = app.atividade;
    $('trilho').innerHTML = '';
    pintarJornada();
    let r;
    try {
      r = await api(`/api/v1/student/activities/${a.assignment_id}/attempt/result`);
    } catch (e) {
      $('bloco').innerHTML = `<div class="cartao-bloco">
        <p class="bloco-titulo">Atividade concluída</p>
        ${aviso('Seu resultado estará disponível em instantes.')}
        <button class="botao botao-secundario" data-acao="inicio">Voltar</button>
      </div>`;
      return;
    }
    const res = r.result || {};
    // A revisao e montada dos DOIS endpoints, sem nenhum novo:
    //   /attempt         enunciado, alternativas e o que ele marcou
    //   /attempt/result  o que era certo, se acertou, e a resolucao
    // Casados pela question_version_id.
    let questoes = [];
    try {
      const estado = await api(
        `/api/v1/student/activities/${a.assignment_id}/attempt`);
      const porVid = {};
      for (const i of (r.items || [])) porVid[i.question_version_id] = i;
      questoes = (estado.questions || []).map((q) => ({
        ...q, resultado: porVid[q.question_version_id] || null,
      }));
    } catch (_) { /* sem revisao: o placar ainda aparece */ }
    a.revisao = { questoes, pos: 0 };

    $('trilho').innerHTML = '';
    $('sessao-resumo').textContent = '';  // idem pintarResultado
    // Esta E uma atividade da escola: aqui o acerto E reportado como
    // desempenho. O que NAO se faz e concluir dominio por ter concluido - quem
    // decide isso continua sendo a politica, no proximo calculo de prontidao.
    $('bloco').innerHTML = `
      <div class="cartao-bloco">
        <p class="bloco-etiqueta">Atividade concluída</p>
        <p class="bloco-titulo">Você acertou ${res.correct_count} de ${res.question_count}.</p>
        <p class="bloco-porque">Sua escola recebe este resultado.</p>
        ${questoes.length
          ? '<button class="botao botao-principal" data-acao="revisar">Revisar questões</button>'
          : ''}
        <button class="botao botao-secundario" data-acao="inicio">Voltar ao início</button>
      </div>`;
  }

  // ========================================================= a revisao ====
  // Leitura, so. Abrir isto NAO produz evidencia, nao reconstroi dominio e
  // nao reabre a tentativa - ha teste provando que rever tres vezes nao muda
  // nada no mapa do aluno.
  function pintarRevisao() {
    const a = app.atividade;
    const rev = a && a.revisao;
    if (!rev || !rev.questoes.length) return;
    pintarJornada();
    const total = rev.questoes.length;
    const q = rev.questoes[rev.pos];
    const res = q.resultado || {};
    const acertou = res.is_correct === true;

    $('trilho').innerHTML = rev.questoes.map((x, i) => {
      const r = x.resultado || {};
      const classe = i === rev.pos ? 'passo passo-agora'
        : r.is_correct ? 'passo passo-feito' : 'passo passo-errado';
      return `<span class="${classe}"></span>`;
    }).join('');
    $('sessao-resumo').textContent = `Revisão · questão ${rev.pos + 1} de ${total}`;

    // O estado NAO depende so da cor: traz simbolo e palavra, porque quem nao
    // distingue verde de vermelho tambem precisa saber se acertou.
    const alternativas = (q.options || []).map((o) => {
      const marcada = o.key === res.selected_option_key;
      const certa = o.key === res.correct_option_key;
      const classe = certa ? ' alternativa-certa'
        : marcada ? ' alternativa-errada' : '';
      const selo = certa ? '<span class="alt-selo">✓ correta</span>'
        : marcada ? '<span class="alt-selo">✗ sua resposta</span>' : '';
      return `
        <div class="alternativa alternativa-revisao${classe}">
          <span class="alternativa-letra">${esc(o.key)}</span>
          <span>${esc(o.text)}</span>
          ${selo}
        </div>`;
    }).join('');

    $('bloco').innerHTML = `
      <div class="cartao-bloco">
        <p class="bloco-etiqueta">${acertou ? '✓ Você acertou' : '✗ Você errou'}</p>
        <p class="bloco-enunciado">${esc(q.statement || '')}</p>
        <div class="alternativas">${alternativas}</div>
        ${res.resolution
          ? `<details class="resolucao">
               <summary>Entenda a resposta</summary>
               <p>${esc(res.resolution)}</p>
             </details>`
          : ''}
        <div class="navegacao-questoes">
          <button class="botao botao-secundario" data-acao="revisao-anterior"
                  ${rev.pos === 0 ? 'disabled' : ''}>Anterior</button>
          <button class="botao botao-secundario" data-acao="revisao-proxima"
                  ${rev.pos + 1 >= total ? 'disabled' : ''}>Próxima</button>
        </div>
        <button class="botao botao-secundario" data-acao="inicio">Voltar ao início</button>
      </div>`;
  }

  function navegarRevisao(delta) {
    const rev = app.atividade && app.atividade.revisao;
    if (!rev) return;
    const nova = Math.min(Math.max(0, rev.pos + delta), rev.questoes.length - 1);
    if (nova === rev.pos) return;
    rev.pos = nova;
    pintarRevisao();
  }

  // ========================================================= a busca ======
  // GET /api/v1/student/search EXISTE e devolve questoes de verdade. O que
  // ainda NAO existe e abrir uma questao avulsa fora de uma atividade - entao
  // a tela mostra o que encontrou e diz isso, em vez de oferecer um link que
  // nao leva a lugar nenhum. Nao ha chat, e nada aqui e simulado.
  async function buscar(termo) {
    const q = (termo || '').trim();
    const caixa = $('resultado-busca');
    if (!q) { caixa.innerHTML = ''; caixa.hidden = true; return; }
    caixa.hidden = false;
    caixa.innerHTML = aviso('Procurando…');
    try {
      const r = await api(`/api/v1/student/search?q=${encodeURIComponent(q)}&limit=5`);
      const achadas = ((r.results || {}).questions) || [];
      if (!achadas.length) {
        caixa.innerHTML = `<p class="detalhe">Não encontrei nada sobre
          <strong>${esc(q)}</strong> no acervo.</p>`;
        return;
      }
      caixa.innerHTML = `
        <p class="busca-titulo">${achadas.length} ${achadas.length === 1
          ? 'questão encontrada' : 'questões encontradas'} sobre ${esc(q)}</p>
        <ul class="busca-lista">
          ${achadas.map((x) => `<li>${esc((x.title || '').slice(0, 120))}…</li>`).join('')}
        </ul>
        <p class="indisponivel">Abrir uma questão avulsa ainda não está
           disponível. Por enquanto elas chegam pelas atividades e pelas
           práticas.</p>`;
    } catch (e) {
      caixa.innerHTML = `<p class="detalhe">Não consegui buscar agora
        (erro ${esc(e.status || '')}).</p>`;
    }
  }

  // ===================================================== progresso ========
  // Tudo aqui vem de GET /api/v1/student/progress, que ja devolve as faixas
  // prontas, traduzidas de PerformanceThresholdPolicy - a unica fonte dos
  // cortes no sistema. A tela so desenha o que recebe.

  // As faixas vem prontas de GET /student/progress, ja traduzidas de
  // PerformanceThresholdPolicy. Esta tela NAO calcula faixa nenhuma - so
  // escolhe o icone e decide se ha acao real para oferecer.
  const FAIXAS = {
    'Consolidado': { icone: '✓', classe: 'faixa-bom' },
    'Em desenvolvimento': { icone: '◐', classe: 'faixa-meio' },
    'Precisa de atenção': { icone: '!', classe: 'faixa-atencao' },
  };

  async function pintarProgresso() {
    if (!identidade()) { $('fatos').innerHTML = ''; return; }
    $('fatos').innerHTML = aviso('Carregando…');
    try {
      const j = await api('/api/v1/student/progress');
      const faixas = j.faixas || [];
      if (!faixas.length) {
        $('fatos').innerHTML = `<li class="vazio">Assim que você responder
          alguma coisa, seu progresso aparece aqui.</li>`;
        $('panorama-corpo').innerHTML = '';
        return;
      }
      // O conteudo que o sistema quer que ele veja agora - unica fonte de
      // "Revisar agora". Botao so existe onde ha rota de verdade.
      const alvo = (app.prontidao && app.prontidao.next_step) || {};
      const podeRevisar = alvo.kind === 'PRACTICE' ? alvo.content_name : null;

      $('fatos').innerHTML = faixas.flatMap((f) => {
        const cfg = FAIXAS[f.faixa] || { icone: '·', classe: '' };
        return (f.itens || []).map((item) => {
          const acao = (item === podeRevisar)
            ? `<button class="botao botao-secundario botao-pequeno"
                       data-acao="praticar" data-conteudo="${esc(alvo.content_code)}"
               >${esc(alvo.cta || 'Praticar agora')}</button>` : '';
          return `
            <li class="cartao-faixa ${cfg.classe}">
              <span class="faixa-icone" aria-hidden="true">${cfg.icone}</span>
              <span class="faixa-corpo">
                <span class="faixa-conteudo">${esc(item)}</span>
                <span class="faixa-estado">${esc(f.faixa)}</span>
              </span>
              ${acao}
            </li>`;
        });
      }).join('');
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
    ['inicio', 'sessao', 'atividades', 'progresso'].forEach((t) => {
      $(`tela-${t}`).hidden = (t !== tela);
    });
    // As duas barras - header no desktop, inferior no mobile - marcam a mesma
    // aba. Sao arranjos diferentes da MESMA navegacao, nao dois menus.
    document.querySelectorAll('.aba, .topo-aba').forEach((aba) => {
      const ativa = aba.dataset.tela === tela;
      aba.classList.toggle('ativa', ativa);
      if (ativa) aba.setAttribute('aria-current', 'page');
      else aba.removeAttribute('aria-current');
    });
    if (tela === 'inicio') pintarHome();
    if (tela === 'atividades') pintarAtividades();
    if (tela === 'progresso') pintarProgresso();
    window.scrollTo(0, 0);
  }

  // ================================================ minhas atividades ====
  // Reutiliza GET /student/activities. A rota ja devolve `state` e `cta` de
  // cada atividade; esta tela TRADUZ o estado em selo e desenha o rotulo que
  // veio - nao monta rotulo nenhum.
  //
  // A primeira versao fazia uma chamada a `/attempt` POR ATIVIDADE so para
  // descobrir o estado, e escrevia o rotulo aqui com um mapa proprio. O mapa
  // ja divergia da matriz do backend ("Abrir" onde a matriz diz "Comecar
  // atividade"): duas tabelas para a mesma pergunta, e a segunda errada.
  const ESTADO_LEGIVEL = {
    NOT_STARTED: { rotulo: 'Pendente', classe: 'selo-prazo' },
    IN_PROGRESS: { rotulo: 'Em andamento', classe: 'selo-neutro' },
    COMPLETED: { rotulo: 'Concluída', classe: 'selo-bom' },
  };

  async function pintarAtividades() {
    const el = $('lista-atividades');
    if (!identidade()) { el.innerHTML = ''; return; }
    el.innerHTML = `<li class="vazio">${esc('Carregando…')}</li>`;
    try {
      const lista = await api('/api/v1/student/activities');
      const itens = lista.items || [];
      if (!itens.length) {
        el.innerHTML = `<li class="vazio">Nenhuma atividade por enquanto.
          Quando sua escola enviar uma, ela aparece aqui.</li>`;
        return;
      }
      el.innerHTML = itens.map((a) => {
        const e = ESTADO_LEGIVEL[a.state] || ESTADO_LEGIVEL.NOT_STARTED;
        const acao = a.cta || 'Abrir';
        return `
          <li class="cartao-atividade">
            <span class="atividade-corpo">
              <span class="atividade-titulo">${esc(a.title)}</span>
              <span class="atividade-meta">
                <span class="selo ${e.classe}">${e.rotulo}</span>
                ${a.due_at ? `<span class="atividade-prazo">Entrega ${esc(a.due_at.slice(0, 10))}</span>` : ''}
                <span class="atividade-prazo">${a.question_count} ${a.question_count === 1 ? 'questão' : 'questões'}</span>
              </span>
            </span>
            <button class="botao botao-secundario botao-pequeno"
                    data-acao="abrir-tarefa" data-id="${esc(a.assignment_id)}"
            >${esc(acao)}</button>
          </li>`;
      }).join('');
    } catch (e) {
      el.innerHTML = `<li class="vazio">Não consegui carregar suas atividades
        (erro ${esc(e.status || '')}).</li>`;
    }
  }

  // ===================================================== acoes ============
  document.addEventListener('click', (e) => {
    const alvo = e.target.closest(
      '[data-acao], [data-opcao], [data-opcao-oficial], [data-opcao-guiada],'
      + ' [data-tela], [data-fechar-folha]');
    if (!alvo) return;

    // Cada tipo de alternativa tem o SEU atributo. A guiada chegou usando
    // `data-opcao`, que ja pertencia ao diagnostico - e como este ramo vem
    // ANTES do `data-acao`, o clique caia em `escolher()`, que mexe em
    // `app.diagnostico`, nulo ali, e morria em silencio. O botao "Responder"
    // nunca acendia e nada no console explicava por que.
    if (alvo.dataset.opcao !== undefined) { escolher(alvo.dataset.opcao); return; }
    if (alvo.dataset.opcaoOficial !== undefined) {
      responderOficial(alvo.dataset.opcaoOficial); return;
    }
    if (alvo.dataset.opcaoGuiada !== undefined) {
      escolherGuiada(alvo.dataset.opcaoGuiada); return;
    }

    switch (alvo.dataset.acao) {
      case 'diagnosticar': abrirDiagnostico(); return;
      case 'estudar': estudar(alvo.dataset.material); return;
      case 'guiada': abrirGuiada(alvo.dataset.conteudo); return;
      case 'guiada-responder': responderGuiada(); return;
      case 'guiada-ajuda': pedirAjudaGuiada(); return;
      case 'guiada-seguir': seguirDaGuiada(); return;
      case 'passo-exemplo': passoDoExemplo(Number(alvo.dataset.passo)); return;
      case 'entendi': concluirEstudo(); return;
      case 'praticar': praticar(alvo.dataset.conteudo); return;
      // A VERIFICACAO e uma pratica curta pelo mesmo motor. O tamanho vem do
      // backend (`next_step.question_count`); a tela nao escolhe quantas
      // questoes confirmam uma recuperacao.
      case 'verificar': {
        const passo = (app.prontidao && app.prontidao.next_step) || {};
        praticar(alvo.dataset.conteudo || passo.content_code,
                 { quantas: passo.question_count || 3, verificacao: true });
        return;
      }
      case 'avancar': avancar(); return;
      case 'abrir-tarefa': abrirTarefa(alvo.dataset.id); return;
      case 'questao-anterior': navegarQuestao(-1); return;
      case 'questao-proxima': navegarQuestao(1); return;
      case 'finalizar-atividade': finalizarAtividade(); return;
      case 'revisar': pintarRevisao(); return;
      case 'revisao-anterior': navegarRevisao(-1); return;
      case 'revisao-proxima': navegarRevisao(1); return;
      case 'inicio': app.diagnostico = null; app.atividade = null; app.estudo = null; app.guiada = null; irPara('inicio'); return;
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
    // As respostas ja estao no servidor: sair nao perde nada, e voltar retoma
    // a tentativa em andamento.
    app.diagnostico = null;
    app.atividade = null;
    irPara('inicio');
  });

  // Enter ja dispara submit; o botao Enviar e o mesmo caminho.
  $('form-perguntar').addEventListener('submit', (e) => {
    e.preventDefault();
    buscar($('campo-duvida').value);
  });

  irPara('inicio');
})();
