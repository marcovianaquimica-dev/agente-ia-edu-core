/**
 * OS FILTROS DE TAXONOMIA — as decisões, testáveis.
 *
 * O PROBLEMA MEDIDO, EM 2026-10-06
 * =================================
 * Conteúdo e Subconteúdo eram campos de texto livre que só funcionavam com o
 * código canônico. Medido no navegador:
 *
 *     content=CHEMISTRY-PHYSICAL-STOICHIOMETRY  → 21 questões
 *     content=Estequiometria                    → 0
 *
 * O placeholder dizia "código curriculum-v2": é verdade, e é a versão do
 * esquema interno na cara de quem dá aula. Trocá-lo por "Ex.: Estequiometria"
 * seria pior — passaria a prometer uma coisa que não funciona.
 *
 * A saída não é escolher entre as duas: é o professor ESCOLHER numa lista com
 * os nomes do catálogo, e a requisição continuar mandando o código.
 *
 * E a Disciplina era uma lista escrita à mão no HTML, com quatro traduções
 * fixas. Qualquer disciplina nova ficaria de fora sem ninguém perceber.
 */
(function (raiz) {
  'use strict';

  /**
   * Só os nós vivos de um tipo, uma vez cada, em ordem estável.
   *
   * O "uma vez cada" não é defensivo: a árvore de cada disciplina devolve a
   * própria raiz, então juntar as raízes com as árvores duplicava as quatro
   * disciplinas na lista — medido no navegador.
   */
  function doTipo(nos, tipo) {
    var vistos = Object.create(null);
    return (nos || [])
      .filter(function (n) {
        if (!n || n.node_type !== tipo || n.active === false) return false;
        if (vistos[n.code]) return false;
        vistos[n.code] = true;
        return true;
      })
      .slice()
      .sort(function (a, b) {
        if ((a.position || 0) !== (b.position || 0)) return (a.position || 0) - (b.position || 0);
        return String(a.name || '').localeCompare(String(b.name || ''), 'pt-BR');
      });
  }

  /**
   * As opções de um filtro: valor é o CÓDIGO, rótulo é o NOME.
   *
   * `pai` restringe à árvore de um ancestral — escolher Química não deve
   * oferecer os conteúdos de Biologia. A relação é pelo prefixo do código,
   * que é como esta taxonomia se organiza (`CHEMISTRY-PHYSICAL-...`): o
   * `root_id` só liga à disciplina, e não serviria para filtrar por área.
   *
   * Nó sem nome volta com o próprio código: mostrar o código é honesto.
   */
  function opcoes(nos, tipo, pai) {
    var lista = doTipo(nos, tipo);
    if (pai) {
      lista = lista.filter(function (n) {
        return String(n.code || '').indexOf(pai + '-') === 0;
      });
    }
    return lista.map(function (n) {
      var nome = String(n.name || '').trim();
      return { value: n.code, rotulo: nome || n.code };
    });
  }

  /**
   * O filtro escolhido ainda existe na lista nova?
   *
   * Trocar a Disciplina para Biologia com "Estequiometria" selecionada
   * deixaria um filtro invisível ligado, e a lista voltaria vazia sem o
   * professor entender por quê.
   */
  function aindaVale(valor, lista) {
    if (!valor) return true;
    return (lista || []).some(function (o) { return o.value === valor; });
  }

  var QBankTaxonomia = {
    opcoes: opcoes,
    aindaVale: aindaVale,
    doTipo: doTipo,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = QBankTaxonomia;
  } else {
    raiz.QBankTaxonomia = QBankTaxonomia;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this));
