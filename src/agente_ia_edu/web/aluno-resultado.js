/**
 * O FIM DE UMA ATIVIDADE - e por que ele nao e o fim da jornada.
 *
 * DOIS ACHADOS DO TESTE HUMANO MORAVAM NESTA TELA
 * ================================================
 * O primeiro: ela terminava em
 *
 *     ATIVIDADE CONCLUIDA
 *     Voce acertou 1 de 5.
 *     [ Revisar questoes ]
 *
 * O placar como mensagem pedagogica principal, e um botao que leva de volta
 * ao que ja passou. Enquanto isso o backend JA SABIA qual era o proximo
 * passo - localizar o gargalo, rever a explicacao, praticar a proporcao - e
 * a tela nao o oferecia. Acabaram as questoes, acabou a jornada.
 *
 * O segundo: o placar nao e a pergunta do aluno. "1 de 5" diz o tamanho do
 * problema, nunca o que fazer com ele. Para a ESCOLA o numero e o
 * entregavel, e ele continua indo - mas para quem errou quatro, a frase que
 * importa e a que diz por onde comecar.
 *
 * A REGRA
 * ========
 * Havendo proximo passo conhecido, a mensagem principal e a DELE e o botao
 * principal e o DELE. O placar vira linha secundaria - informacao, nao
 * conclusao - e a revisao continua ali, como opcao.
 *
 * Nao havendo, o placar volta a ser o titulo: ai ele e mesmo a noticia.
 *
 * QUEM ESCREVE A FRASE E O BACKEND
 * =================================
 * `passo.feedback` vem de `feedback_pedagogico`, e `passo.cta` de
 * `proximo_passo`. Esta tela escolhe a ORDEM e o DESTAQUE; nenhuma frase
 * pedagogica e montada aqui. Foi exatamente isso - pedagogia escrita no
 * navegador - o achado 3 do teste humano de 2026-10-05.
 */
(function (raiz) {
  'use strict';

  // Passos que NAO sao uma proxima intervencao pedagogica.
  //
  // ACTIVITY esta na lista porque, na tela de resultado de uma atividade,
  // "proximo passo: abrir a atividade" e a propria atividade que acabou de
  // ser entregue - oferecer isso como novidade seria um laco.
  var SEM_INTERVENCAO = ['NONE', 'ACTIVITY', ''];

  function temProximaIntervencao(passo) {
    var p = passo || {};
    return !!p.kind && SEM_INTERVENCAO.indexOf(p.kind) === -1;
  }

  function placarDe(resultado) {
    var r = resultado || {};
    if (r.correct_count === undefined || r.question_count === undefined) {
      return '';
    }
    return 'Você acertou ' + r.correct_count + ' de ' + r.question_count + '.';
  }

  /**
   * O que a tela de resultado de uma ATIVIDADE DA ESCOLA mostra.
   *
   * `temRevisao` e falso quando as questoes nao puderam ser carregadas - ai
   * nao se oferece um botao que nao abre nada.
   */
  function resultadoDaAtividade(entrada) {
    var e = entrada || {};
    var passo = e.passo || {};
    var fb = passo.feedback || {};
    var placar = placarDe(e.resultado);
    var revisao = { acao: 'revisar', rotulo: 'Rever as questões',
                    principal: false };
    var inicio = { acao: 'inicio', rotulo: 'Voltar ao início',
                   principal: false };

    if (temProximaIntervencao(passo)) {
      var acoes = [{ acao: 'seguir', rotulo: passo.cta || 'Continuar',
                     principal: true }];
      if (e.temRevisao) acoes.push(revisao);
      acoes.push(inicio);
      return {
        etiqueta: 'Atividade entregue',
        // A frase do backend vence o placar. Sem ela a tela NAO inventa
        // pedagogia: cai no placar, que e verdade.
        titulo: fb.titulo || placar || 'Atividade concluída.',
        placar: placar,
        detalhe: fb.detalhe || 'Sua escola recebe este resultado.',
        // Dito uma vez, em linha propria: a nota foi entregue, e o que vem
        // agora nao e nota.
        nota: 'Sua escola recebe este resultado.',
        acoes: acoes,
      };
    }

    // SEM PROXIMO PASSO, ALGUEM PRECISA SER O BOTAO PRINCIPAL.
    //
    // A primeira versao marcava `revisar` como principal e empurrava
    // `inicio` como secundario - e quando as questoes nao carregavam
    // (`temRevisao` falso) a tela ficava sem acao principal nenhuma, so com
    // um botao apagado de voltar. O teste de "exatamente uma acao principal"
    // pegou isso antes do navegador.
    var semPasso = [];
    if (e.temRevisao) {
      semPasso.push({ acao: 'revisar', rotulo: 'Rever as questões',
                      principal: true });
      semPasso.push(inicio);
    } else {
      semPasso.push({ acao: 'inicio', rotulo: 'Voltar ao início',
                      principal: true });
    }
    return {
      etiqueta: 'Atividade entregue',
      titulo: placar || 'Atividade concluída.',
      placar: '',
      detalhe: 'Sua escola recebe este resultado.',
      nota: '',
      acoes: semPasso,
    };
  }

  // ======================================================== os dois modos ==
  //
  // A MESMA TELA SERVE A DUAS COISAS DIFERENTES, e so uma delas pode mostrar
  // o gabarito de imediato.
  //
  // REVISAO e olhar para tras: a atividade foi entregue, a nota foi dada, e
  // esconder a resposta certa ali nao protege aprendizagem nenhuma - so
  // impede o aluno de conferir o proprio raciocinio.
  //
  // RECUPERACAO e o aluno que acabou de errar e esta sendo ajudado AGORA.
  // Revelar a alternativa certa no primeiro segundo encerra a recuperacao
  // antes de ela comecar: nao sobra o que investigar, e a explicacao que vem
  // depois chega para quem ja sabe o final.
  //
  // O que se esconde e QUAL ERA A CERTA, e so ate a explicacao ser lida.
  // Nunca "voce errou" - isso o aluno precisa saber de imediato, senao fica
  // sem entender por que esta sendo ajudado.
  var MODO_REVISAO = 'REVISAO';
  var MODO_RECUPERACAO = 'RECUPERACAO';

  /**
   * Pode mostrar qual alternativa era a correta?
   *
   * Em revisao, sempre. Em recuperacao, depois de a explicacao ter sido
   * lida - que e quando ela passa a servir para conferir o raciocinio em vez
   * de encerrar a questao.
   *
   * O gabarito NUNCA fica escondido para sempre: nao ha caminho em que esta
   * funcao devolva false definitivamente.
   */
  function revelaGabarito(modo, contexto) {
    var c = contexto || {};
    if (modo !== MODO_RECUPERACAO) return true;
    return !!c.explicacaoLida;
  }

  /**
   * O selo de cada alternativa na revisao.
   *
   * Devolve null quando a alternativa nao merece selo nenhum.
   */
  function seloDaAlternativa(opcao, contexto) {
    var o = opcao || {};
    var c = contexto || {};
    var revela = revelaGabarito(c.modo, c);
    if (o.key === c.correta && revela) return { tipo: 'certa',
                                                texto: '✓ correta' };
    if (o.key === c.marcada) {
      return { tipo: o.key === c.correta && revela ? 'certa' : 'marcada',
               texto: '✗ sua resposta' };
    }
    return null;
  }

  /**
   * A etiqueta no alto da questao revisada.
   *
   * "Voce errou" aparece nos dois modos: e o que explica ao aluno por que
   * ele esta vendo esta tela. O que muda e o que vem DEPOIS dela.
   */
  function etiquetaDaQuestao(acertou, modo) {
    if (acertou) return '✓ Você acertou';
    if (modo === MODO_RECUPERACAO) return 'Vamos olhar esta';
    return '✗ Você errou';
  }

  var ResultadoUI = {
    MODO_RECUPERACAO: MODO_RECUPERACAO,
    MODO_REVISAO: MODO_REVISAO,
    etiquetaDaQuestao: etiquetaDaQuestao,
    placarDe: placarDe,
    resultadoDaAtividade: resultadoDaAtividade,
    revelaGabarito: revelaGabarito,
    seloDaAlternativa: seloDaAlternativa,
    temProximaIntervencao: temProximaIntervencao,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = ResultadoUI;
  } else {
    raiz.ResultadoUI = ResultadoUI;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this));
