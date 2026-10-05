/**
 * A PREPARAÇÃO vista por dentro - as decisões de interface, testáveis.
 *
 * O PROBLEMA
 * ==========
 * O teste humano de 2026-10-05 ficou preso em
 *
 *     Diagnóstico ✓   Preparação ●   Atividade ○   Resultado ○
 *
 * sem perceber o que mudava entre uma volta e outra. A barra estava certa: ele
 * ESTAVA na preparação. O que faltava era, dentro dela, dizer onde.
 *
 *     Entender → Tentar com ajuda → Praticar → Verificar
 *
 * POR QUE "VER EXEMPLO" NÃO É UM SUB-PASSO
 * =========================================
 * O exemplo resolvido é uma seção do próprio material, não uma etapa com
 * decisão própria: o backend nunca devolve "mostre o exemplo" como passo.
 * Desenhá-lo aqui criaria na tela uma etapa que não existe na máquina — e a
 * tela voltaria a contar uma história diferente da que o sistema vive.
 *
 * O QUE ESTE MÓDULO NÃO FAZ
 * ==========================
 * Não decide passo nenhum. Recebe o `next_step` já decidido e escolhe como
 * desenhá-lo. E, no resultado da prática, ele PREFERE a frase do backend a
 * inventar uma: inventá-la no navegador foi exatamente o terceiro achado do
 * teste humano.
 */
(function (raiz) {
  'use strict';

  // A ordem é a da máquina de decisão, não uma preferência visual.
  var TRILHA = [
    { kind: 'LEARN', rotulo: 'Entender' },
    { kind: 'GUIDED_PRACTICE', rotulo: 'Tentar com ajuda' },
    { kind: 'PRACTICE', rotulo: 'Praticar' },
    { kind: 'VERIFY', rotulo: 'Verificar' },
  ];

  /**
   * Os sub-passos da preparação, com o atual aceso.
   *
   * Vazio fora da preparação: diagnóstico, atividade e escalonamento não são
   * etapas desta escada, e desenhá-la neles diria que o aluno está onde não
   * está. O escalonamento em particular é o oposto de um sub-passo - ele
   * existe porque a escada acabou.
   */
  function subpassos(passo) {
    var kind = (passo || {}).kind;
    var pos = -1;
    for (var i = 0; i < TRILHA.length; i += 1) {
      if (TRILHA[i].kind === kind) { pos = i; break; }
    }
    if (pos < 0) return [];
    return TRILHA.map(function (s, idx) {
      return {
        rotulo: s.rotulo,
        cumprido: idx < pos,
        atual: idx === pos,
      };
    });
  }

  /**
   * O que a tela diz depois de uma prática corrigida.
   *
   * `feedback` é o do backend (`next_step.feedback`), decidido junto com o
   * próximo passo. Sem ele a tela informa o placar e PARA - não conclui nada
   * sobre domínio, que é justamente o que ela não tem como saber.
   */
  function falaDoResultado(resultado, feedback) {
    var r = resultado || {};
    var temPlacar = (r.correct_count !== undefined
                     && r.question_count !== undefined);
    var placar = temPlacar
      ? ('Você acertou ' + r.correct_count + ' de ' + r.question_count + '.')
      : '';
    var f = feedback || {};
    return {
      placar: placar,
      tom: f.tom || 'NEUTRO',
      titulo: f.titulo || placar || 'Prática concluída.',
      detalhe: f.detalhe || 'Suas respostas foram registradas.',
    };
  }

  /**
   * Para onde olhar depois de revelar um passo do exemplo.
   *
   * Medido no teste humano de 2026-10-05: o clique em "Ver o próximo passo"
   * levava a página de scrollY 889 para 0 - de volta ao topo, no meio de uma
   * leitura. A causa é a repintura do bloco, que apaga a âncora do navegador.
   *
   * O destino certo não é a posição antiga (o conteúdo mudou embaixo dela) nem
   * o topo: é o INÍCIO DO PASSO RECÉM-REVELADO, que é o que o aluno pediu
   * para ler.
   *
   * `reduzido` vem de `prefers-reduced-motion`. Não é preferência estética:
   * para parte das pessoas a rolagem animada causa enjoo. O destino é o
   * mesmo; o que muda é haver percurso.
   */
  function comoRevelar(opcoes) {
    var o = opcoes || {};
    var indice = o.indice;
    var total = o.total || 0;
    if (typeof indice !== 'number' || indice < 0 || indice >= total) return null;
    return { indice: indice, behavior: o.reduzido ? 'auto' : 'smooth',
             block: 'start' };
  }

  var PrepUI = {
    subpassos: subpassos,
    falaDoResultado: falaDoResultado,
    comoRevelar: comoRevelar,
    TRILHA: TRILHA,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = PrepUI;
  } else {
    raiz.PrepUI = PrepUI;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this));
