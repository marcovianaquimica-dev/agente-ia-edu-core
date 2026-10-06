/**
 * A NAVEGAÇÃO INTERNA DA REDAÇÃO - as decisões, isoladas para terem teste.
 *
 * O QUE EXISTIA
 * =============
 *     Propostas | Fila de Revisão | Evolução | Dashboard | 🗑️ Lixeira
 *
 * Cinco abas no mesmo peso visual misturando três naturezas: áreas de
 * trabalho (Propostas, Evolução), uma fila operacional (Fila de Revisão) e
 * uma lixeira - operação secundária e destrutiva, com emoji, ao lado das
 * áreas principais.
 *
 * E o "Dashboard", que responde à primeira pergunta do professor ("como estão
 * meus alunos?"), era a quarta aba em vez da entrada.
 *
 * O QUE MUDOU, E O QUE NÃO
 * =========================
 * Mudaram os RÓTULOS e a hierarquia. As chaves internas (`prompts`, `queue`,
 * `evolution`, `dashboard`, `trash`) continuam exatamente as mesmas: elas são
 * o contrato com `essay-review.js`, e renomeá-las seria risco cosmético.
 *
 *     Dashboard        -> Visão geral   (e virou a entrada)
 *     Fila de Revisão  -> Correções
 *     Lixeira          -> menu secundário, sem emoji
 *
 * "Enviar em lote" NÃO existe neste módulo - procurei em todo o `web/`. O
 * único "lote" do repositório é classificação em lote no Banco de Questões.
 * Não há o que mover, e inventar a ação seria criar um botão decorativo.
 *
 * A LIXEIRA CONTINUA ALCANÇÁVEL
 * ==============================
 * Ela sai do primeiro nível, não do produto. Fica num menu secundário visível
 * na mesma barra - esconder a ponto de ninguém achar seria trocar um problema
 * por outro.
 */
(function (raiz) {
  'use strict';

  // As chaves são o contrato com `essay-review.js`. Não são rótulos.
  var AREAS = [
    { aba: 'dashboard', rotulo: 'Visão geral' },
    { aba: 'prompts', rotulo: 'Propostas' },
    { aba: 'queue', rotulo: 'Correções' },
    { aba: 'evolution', rotulo: 'Evolução' },
  ];

  var SECUNDARIOS = [
    { aba: 'trash', rotulo: 'Lixeira' },
  ];

  // A entrada do módulo. "Como estão meus alunos?" vem antes de "o que eu
  // quero cadastrar?".
  var ABA_INICIAL = 'dashboard';

  // Views do professor em que os filtros GLOBAIS do cabeçalho (Turma,
  // Período) não afetam nada. Medido no navegador: na Redação eles
  // continuavam visíveis, não recalculavam coisa alguma, e a própria Redação
  // tinha a sua Turma logo abaixo. Filtro inerte é pior que duplicado: ele
  // promete recalcular e não recalcula.
  var VIEWS_SEM_FILTRO_GLOBAL = ['essay-review'];

  function _marcar(lista, ativa) {
    return lista.map(function (item) {
      return { aba: item.aba, rotulo: item.rotulo, ativa: item.aba === ativa };
    });
  }

  function areas(ativa) { return _marcar(AREAS, ativa); }
  function secundarios(ativa) { return _marcar(SECUNDARIOS, ativa); }

  /** Os filtros globais do cabeçalho valem nesta view?
   *
   * Lista de EXCEÇÕES, não de permissões: uma view desconhecida mantém os
   * filtros. Esconder por engano um filtro que funciona é pior que mostrar
   * um inerte.
   */
  function filtrosGlobaisValem(view) {
    return VIEWS_SEM_FILTRO_GLOBAL.indexOf(view) === -1;
  }

  var RedacaoNav = {
    areas: areas,
    secundarios: secundarios,
    filtrosGlobaisValem: filtrosGlobaisValem,
    ABA_INICIAL: ABA_INICIAL,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = RedacaoNav;
  } else {
    raiz.RedacaoNav = RedacaoNav;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this));
