/**
 * INVESTIGACAO - as decisoes de INTERFACE do degrau mais alto da escada.
 *
 * POR QUE UM ARQUIVO SEPARADO
 * ============================
 * Mesmo motivo de `aluno-guiada.js`: `aluno.js` e um IIFE, nada dentro dele
 * pode ser importado, e os testes acabariam virando varredura de fonte -
 * provam que uma string existe, nao que a tela funciona. As funcoes puras
 * ficam aqui e rodam de verdade em `tests/test_aluno_investigacao_frontend.js`.
 *
 * O QUE ELAS DECIDEM, E O QUE NAO
 * ================================
 * Decidem APRESENTACAO: qual botao acende, que frase aparece, em que etapa o
 * aluno esta.
 *
 * Nao decidem pedagogia. Qual e a etapa aberta, se a resposta estava certa,
 * qual micro-habilidade ficou localizada - tudo isso chega pronto do backend
 * (`investigacao_do_erro` + `servico_de_investigacao`). A tela nem conhece o
 * gabarito: ele nao viaja antes de a etapa ser resolvida.
 *
 * E ELAS NAO AFIRMAM O QUE O ALUNO FEZ
 * =====================================
 * A hipotese vem do backend ja escrita como hipotese. As falas daqui seguem a
 * mesma regra: nenhuma delas diz "voce esqueceu" nem "voce calculou errado",
 * porque a unica coisa observada foi uma letra marcada. Ha teste varrendo
 * essas formas.
 */
(function (raiz) {
  'use strict';

  /**
   * O estado da tela a partir do payload da API.
   *
   * `opcoes.escolha` e o que o aluno marcou AGORA e ainda nao enviou - a
   * unica coisa que esta funcao sabe e o servidor nao.
   */
  function estado(dados, opcoes) {
    var d = dados || {};
    var o = opcoes || {};
    var temPayload = !!dados;
    var etapa = d.etapa || null;
    var concluido = !!d.completed;

    return {
      concluido: concluido,
      etapa: etapa,
      // Depois de concluir nao se responde de novo: as etapas estao
      // registradas, e insistir so confundiria quem lesse os contadores.
      podeResponder: temPayload && !concluido && !!etapa,
      // O botao so acende com alternativa marcada - um "responder" sem
      // resposta gastaria uma tentativa a toa.
      responderHabilitado: temPayload && !concluido && !!etapa && !!o.escolha,
      hipotese: d.hipotese || '',
      concluidas: (d.concluidas || []).slice(),
      // O ensino da etapa que nao ficou de pe. Nunca a letra.
      retorno: (d.retorno && d.retorno.comentario) || '',
      progresso: progresso(d),
      // A micro-habilidade localizada. Serve a tela para NADA pedagogico -
      // ela nao escolhe o proximo passo com isto; e informacao de depuracao
      // e de rotulo.
      gargalo: d.bottleneck_skill || null,
    };
  }

  /**
   * "Etapa 2 de 3", ou vazio quando nao ha o que contar.
   *
   * A contagem usa as CONCLUIDAS, e nao a ordem da etapa aberta: quem errou a
   * etapa 1 tres vezes continua na etapa 1, e um contador que subisse a cada
   * tentativa diria que ele avancou quando nao avancou.
   */
  function progresso(dados) {
    var d = dados || {};
    var total = d.total_etapas || 0;
    if (!total) return '';
    var feitas = (d.concluidas || []).length;
    if (d.completed) return 'As ' + total + ' etapas, conferidas.';
    return 'Etapa ' + Math.min(feitas + 1, total) + ' de ' + total;
  }

  /**
   * O que o Assessor diz depois de uma tentativa numa etapa.
   *
   * NAO HA "ERRADO". Errar uma etapa da investigacao e o funcionamento normal
   * dela: a pergunta existe justamente para descobrir qual etapa nao esta de
   * pe. Chamar isso de erro transformaria a ferramenta de diagnostico em mais
   * uma prova.
   *
   * E NAO HA ELOGIO FALSO. Acertar uma micropergunta logo depois de o sistema
   * dizer qual etapa e nao e dominar o conteudo, e dizer "voce domina massa
   * molar" aqui seria a mentira que o sistema inteiro evita.
   */
  function falaDoAssessor(dados, resultado) {
    var d = dados || {};

    // NADA ACABOU DE ACONTECER: primeira abertura, ou a pagina recarregada.
    if (resultado === null || resultado === undefined) {
      if (d.completed) {
        return 'Pronto — percorremos as etapas. Agora a explicação do ponto '
             + 'que travou.';
      }
      if ((d.concluidas || []).length) {
        return 'Vamos continuar de onde paramos.';
      }
      return 'Vou perguntar uma coisa de cada vez. Nenhuma delas vale nota — '
           + 'elas servem para eu descobrir o que explicar.';
    }

    var r = resultado;

    if (r.completed) {
      return 'É isso. Com as etapas separadas, o ponto que travava fica '
           + 'visível — e é por ele que a explicação vai começar.';
    }

    if (!r.correct) {
      // Aqui esta o valor da investigacao: o sistema ACABOU DE DESCOBRIR
      // algo. A fala diz isso sem transformar em acusacao.
      return 'Então é por aqui. Vamos olhar esta etapa com calma antes de '
           + 'seguir.';
    }

    return 'Essa está de pé. Vamos à próxima etapa.';
  }

  /**
   * As acoes do rodape da investigacao.
   *
   * Pedir ajuda NAO existe aqui, de proposito: a investigacao JA e a ajuda
   * mais alta da escada. Um botao de dica dentro dela seria um degrau acima
   * do topo, e o unico lugar para onde ele levaria e entregar a resposta.
   */
  function acoes(dados) {
    var d = dados || {};
    if (d.completed) {
      return [{ acao: 'seguir', rotulo: 'Ver a explicação', principal: true }];
    }
    return [{ acao: 'responder', rotulo: 'Responder', principal: true },
            { acao: 'sair', rotulo: 'Voltar ao início', principal: false }];
  }

  var InvestigacaoUI = {
    estado: estado,
    progresso: progresso,
    falaDoAssessor: falaDoAssessor,
    acoes: acoes,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = InvestigacaoUI;
  } else {
    raiz.InvestigacaoUI = InvestigacaoUI;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this));
