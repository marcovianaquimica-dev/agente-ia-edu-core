/**
 * PRATICA GUIADA - as decisoes de INTERFACE, isoladas para poderem ser testadas.
 *
 * POR QUE UM ARQUIVO SEPARADO
 * ============================
 * `aluno.js` e um IIFE: nada dentro dele pode ser importado, e por isso os
 * testes .js deste projeto acabaram virando varredura de fonte - provam que
 * uma string existe, nao que a tela funciona.
 *
 * Aqui estao as funcoes puras do fluxo guiado. Elas rodam de verdade no teste
 * (`tests/test_aluno_guiada_frontend.js`, com `node --test`), sem dependencia
 * nova: o arquivo se exporta como modulo quando ha `module`, e se pendura no
 * `window` quando e a pagina que o carrega.
 *
 * O QUE ELAS DECIDEM, E O QUE NAO
 * ================================
 * Decidem APRESENTACAO: qual botao fica ativo, que frase aparece, quantas
 * ajudas ja foram usadas.
 *
 * Nao decidem pedagogia. Se o aluno resolveu sozinho, quantas ajudas ele
 * usou, se concluiu - tudo isso chega pronto do backend. A tela so escolhe
 * como contar.
 */
(function (raiz) {
  'use strict';

  var CTA_TENTAR = 'Tentar com ajuda';
  var CTA_CONTINUAR = 'Continuar tentando';
  var CTA_SOZINHO = 'Agora tentar sozinho';

  /**
   * O estado da tela a partir do payload da API.
   *
   * `opcoes.escolha` e o que o aluno marcou AGORA e ainda nao enviou - e a
   * unica coisa que esta funcao sabe e o servidor nao.
   */
  function estado(dados, opcoes) {
    var d = dados || {};
    var o = opcoes || {};
    var concluido = !!d.completed;
    var usadas = d.hints_used || 0;
    var disponiveis = d.ajudas_disponiveis || 0;
    var temPayload = !!dados;

    return {
      concluido: concluido,
      // Depois de concluir nao se responde de novo: o registro ja foi feito,
      // e insistir so poderia confundir quem lesse os contadores depois.
      podeResponder: temPayload && !concluido,
      podePedirAjuda: temPayload && !concluido && usadas < disponiveis,
      // O botao so acende quando ha alternativa marcada - um "responder" sem
      // resposta gastaria uma tentativa a toa.
      responderHabilitado: temPayload && !concluido && !!o.escolha,
      cta: concluido ? CTA_SOZINHO : (d.attempts ? CTA_CONTINUAR : CTA_TENTAR),
      ajudas: (d.ajudas || []).slice(),
      tentativas: d.attempts || 0,
      ajudasUsadas: usadas,
      // Vazio quando nenhuma ajuda foi usada: anunciar "0 de 4" sugere que o
      // esperado e usar.
      resumoDaAjuda: usadas ? ('Ajuda: ' + usadas + ' de ' + disponiveis) : '',
      // So existe depois de concluir - antes disso o backend nem manda.
      correta: d.correct_option,
      fecho: d.fecho,
    };
  }

  /**
   * O que o Assessor diz depois de uma tentativa.
   *
   * NAO HA ELOGIO FALSO. Quem chegou a resposta so depois de toda a ajuda nao
   * ouve "muito bem, sozinho" - ouviria uma mentira, e a proxima etapa (tentar
   * um sem ajuda) pareceria castigo em vez de consequencia.
   *
   * E nao ha "ERRADO". Errar numa pratica guiada e o funcionamento normal
   * dela: e para isso que a ajuda existe.
   */
  function falaDoAssessor(dados, resultado) {
    var d = dados || {};
    var r = resultado || {};

    if (!r.correct) {
      if ((d.hints_used || 0) >= (d.ajudas_disponiveis || 0)) {
        return 'Ainda não é essa. Releia o último passo com calma e '
             + 'confira os átomos de cada elemento, um por um.';
      }
      return 'Ainda não é essa — e tudo bem, é para isso que estou aqui. '
           + 'Quer uma ajuda antes de tentar de novo?';
    }

    if (d.solved_unaided) {
      return 'Isso! Você chegou sozinho, sem precisar de ajuda nenhuma.';
    }
    return 'Chegamos lá. Com a ajuda, a ideia apareceu — agora vale tentar '
         + 'um parecido por conta própria, para ver se ela ficou firme.';
  }

  var GuiadaUI = {
    estado: estado,
    falaDoAssessor: falaDoAssessor,
    CTA_TENTAR: CTA_TENTAR,
    CTA_CONTINUAR: CTA_CONTINUAR,
    CTA_SOZINHO: CTA_SOZINHO,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = GuiadaUI;
  } else {
    raiz.GuiadaUI = GuiadaUI;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this));
