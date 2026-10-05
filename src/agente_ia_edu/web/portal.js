/**
 * PORTAL — a Home do Núcleo Edu 360.
 *
 * Uma chamada, três respostas: quem sou, onde estou, o que tenho. As decisões
 * de apresentação moram em `portal-ui.js`, que é exercitado por `node --test`.
 *
 * ESTA TELA NÃO DECIDE ACESSO. `can_access` e `route` chegam prontos do
 * backend, e as rotas de cada módulo continuam com os seus próprios guards.
 * Esconder um card não protege nada — o que ele evita é oferecer uma porta
 * que vai bater na cara da pessoa.
 */
(function () {
  'use strict';

  // A mesma chave que o Assessor usa: entrar por um e sair pelo outro nao
  // pode pedir identidade duas vezes.
  var CHAVE = 'nucleo.aluno.identidade';
  var $ = function (id) { return document.getElementById(id); };

  function esc(v) {
    return String(v == null ? '' : v).replace(/[&<>"']/g, function (c) {
      return { '&': '&amp;', '<': '&lt;', '>': '&gt;',
               '"': '&quot;', "'": '&#39;' }[c];
    });
  }

  function identidade() {
    try { return localStorage.getItem(CHAVE) || ''; } catch (_) { return ''; }
  }

  async function buscar() {
    var cabecalhos = {};
    var quem = identidade();
    if (quem) cabecalhos.Authorization = 'Bearer ' + quem;
    var r = await fetch('/api/v1/portal/overview', { headers: cabecalhos });
    if (!r.ok) throw new Error('HTTP ' + r.status);
    return r.json();
  }

  // Um SVG por modulo. Icones, nao emoji: emoji muda de desenho e de tamanho
  // entre Android, iOS e Windows, e a grade fica visivelmente desalinhada.
  var ICONES = {
    redacao: '<path d="M4 20h16M6 16l10-10 2 2-10 10H6z"/>',
    assessor: '<path d="M12 3 3 8l9 5 9-5-9-5zM5 11v5c0 1.7 3.1 3 7 3s7-1.3 7-3v-5"/>',
    saeb: '<path d="M4 19V9M10 19V5M16 19v-7M22 19H2"/>',
    academico: '<path d="M4 4h16v16H4zM4 9h16M9 9v11"/>',
    formacao: '<path d="M12 3 3 8l9 5 9-5-9-5zM7 12v4a5 3 0 0 0 10 0v-4"/>',
    simulados: '<path d="M5 3h14v18l-7-4-7 4z"/>',
  };

  function icone(nome) {
    return '<svg class="modulo-icone" viewBox="0 0 24 24" aria-hidden="true">'
         + (ICONES[nome] || ICONES.academico) + '</svg>';
  }

  function cartaoHTML(m) {
    var c = PortalUI.cartao(m);
    var cabeca =
        '<span class="modulo-topo">' + icone(m.icon)
      + '<h3 class="modulo-titulo">' + esc(m.title) + '</h3></span>'
      + '<p class="modulo-descricao">' + esc(m.description) + '</p>';

    if (c.acionavel) {
      // Um <a> de verdade: abre em nova aba com ctrl+clique, aparece na
      // navegacao por teclado e nao precisa de JavaScript para funcionar.
      return '<li class="modulo modulo-acessivel">' + cabeca
           + '<a class="modulo-acao" href="' + esc(m.route) + '">'
           + esc(c.rotulo) + ' <span aria-hidden="true">→</span></a></li>';
    }

    // SEM BOTAO MORTO. Um `disabled` finge que havia uma acao ali; um selo
    // diz o que esta acontecendo, que e o que a pessoa precisa saber.
    var selo = c.rotulo
      ? '<span class="modulo-selo">' + esc(c.rotulo) + '</span>' : '';
    var nota = c.nota
      ? '<p class="modulo-nota">' + esc(c.nota) + '</p>' : '';
    return '<li class="modulo modulo-' + c.tipo.toLowerCase() + '">'
         + cabeca + nota + selo + '</li>';
  }

  function pintar(dados) {
    var ctx = PortalUI.contexto(dados);
    $('instituicao').textContent = ctx.instituicao;
    $('pessoa').textContent = ctx.papel
      ? ctx.pessoa + ' · ' + ctx.papel : ctx.pessoa;

    var grupos = PortalUI.separar(dados.modules);
    $('modulos-disponiveis').innerHTML = grupos.disponiveis.map(cartaoHTML).join('');
    $('modulos-futuros').innerHTML = grupos.futuros.map(cartaoHTML).join('');
    $('sem-modulos').hidden = grupos.disponiveis.length > 0;
  }

  async function iniciar() {
    try {
      pintar(await buscar());
    } catch (e) {
      // Falhar em silencio deixaria a Home parecendo vazia, que e pior: o
      // produto pareceria incompleto quando o problema e outro.
      $('modulos-disponiveis').innerHTML =
        '<li class="modulo portal-erro">Não consegui carregar seus módulos agora. '
        + 'Recarregue a página.</li>';
    }
  }

  iniciar();
}());
