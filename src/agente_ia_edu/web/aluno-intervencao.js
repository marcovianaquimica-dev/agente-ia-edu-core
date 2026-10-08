/**
 * A INTERVENCAO FORMATIVA - as decisoes de INTERFACE de quando o aluno erra.
 *
 * POR QUE ESTE ARQUIVO EXISTE
 * ============================
 * A validacao manual de 2026-10-08 mostrou o aluno errando e a tela servindo
 * a proxima questao, e a proxima, ate "5 de 5". O backend passou a devolver
 * `may_advance` e `intervention` junto da resposta gravada; estas funcoes
 * sao o que a tela FAZ com isso.
 *
 * Ficam aqui, e nao em `aluno.js`, pelo mesmo motivo de
 * `aluno-investigacao.js`: `aluno.js` e um IIFE, nada dentro dele pode ser
 * importado, e os testes acabariam virando varredura de fonte - provam que
 * uma string existe, nao que a tela decide certo.
 *
 * O QUE ELAS DECIDEM, E O QUE NAO
 * ================================
 * Decidem APRESENTACAO: se a tela avanca ou intervem, que frase o Edu diz,
 * que botao executa a intervencao, e que controles a questao pede.
 *
 * Nao decidem pedagogia. A acao (INVESTIGATE/TEACH), a micro-habilidade, o
 * conteudo e o motivo chegam prontos de `intervencao_formativa`. A tela nao
 * confere resposta, nao escolhe estrategia e nao conhece gabarito.
 *
 * E ELAS NAO AFIRMAM O QUE O ALUNO FEZ
 * =====================================
 * "Vamos entender o que aconteceu" - nunca "voce esqueceu de multiplicar".
 * O que o numero dele sugere e hipotese, e quem a levanta e a investigacao,
 * depois de perguntar. Ha teste varrendo as falas atras de afirmacao.
 */
(function (raiz) {
  'use strict';

  var ACAO_INVESTIGAR = 'INVESTIGATE';
  var ACAO_ENSINAR = 'TEACH';

  function temIntervencao(d) {
    return !!(d && d.action);
  }

  /**
   * O que a tela faz depois de uma resposta: 'intervencao', 'proxima' ou
   * 'concluir'.
   *
   * A INTERVENCAO VENCE ATE NA ULTIMA QUESTAO. Deixar o lote terminar para
   * so entao intervir e exatamente o que o §10 chama de fila cega: o aluno
   * responderia as cinco e so depois descobriria que a primeira tinha algo
   * a entender.
   */
  function proximoPasso(estado) {
    var e = estado || {};
    if (temIntervencao(e.intervencao)) return 'intervencao';
    var pos = Number(e.pos || 0);
    var total = Number(e.total || 0);
    return (pos + 1 >= total) ? 'concluir' : 'proxima';
  }

  /**
   * O que o Edu diz ao interromper. Uma frase, nao uma aula: o ensino de
   * verdade esta na investigacao ou no material que o botao abre.
   */
  function falaDaIntervencao(d) {
    var acao = (d || {}).action;
    if (acao === ACAO_INVESTIGAR) {
      return 'Vamos entender o que aconteceu nessa resposta antes de '
           + 'continuar. São poucas perguntas, uma de cada vez.';
    }
    if (acao === ACAO_ENSINAR) {
      return 'Essa parte ainda está travando. Vale olhar a explicação antes '
           + 'de seguir — depois você tenta de novo.';
    }
    // Acao que esta tela nao conhece: nao inventa promessa sobre o que vem.
    return 'Vamos olhar isso antes de continuar.';
  }

  /**
   * O botao que EXECUTA a intervencao - e nao um "ok" que so fecha o aviso.
   *
   * O §6 e explicito: a frase sozinha nao resolve. O retorno carrega o que
   * `aluno.js` precisa para abrir a investigacao daquela micro-habilidade
   * ou o material daquele conteudo.
   */
  function acaoDaIntervencao(d) {
    if (!temIntervencao(d)) return null;
    if (d.action === ACAO_INVESTIGAR) {
      return {
        acao: 'intervencao-investigar',
        rotulo: 'Vamos olhar por partes',
        content_code: d.content_code || null,
        skill: d.skill || null,
      };
    }
    if (d.action === ACAO_ENSINAR) {
      return {
        acao: 'intervencao-estudar',
        rotulo: 'Ver a explicação',
        material_id: d.material_id || null,
        content_code: d.content_code || null,
      };
    }
    return { acao: 'intervencao-seguir', rotulo: 'Continuar' };
  }

  /**
   * OS CONTROLES DA QUESTAO - alternativas OU campo, nunca os dois.
   *
   * A validacao manual mostrou a tela com A/B/C/D e um campo "Sua resposta"
   * ao mesmo tempo, sem explicacao. Dois canais concorrentes para a mesma
   * resposta deixam o aluno sem saber qual vale, e o contrato de correcao
   * de um item de multipla escolha e a LETRA.
   *
   * A regra e o formato do item: tem alternativas, responde por alternativa.
   * Nao tem, responde por escrito. Ha teste exigindo que os dois nunca
   * aparecam juntos.
   */
  function controles(questao, opcoes) {
    var q = questao || {};
    var o = opcoes || {};
    var alternativas = (q.options || []).map(function (a) {
      return { key: a.key, text: a.text, marcada: o.escolha === a.key };
    });
    if (alternativas.length) {
      return {
        modo: 'alternativas',
        alternativas: alternativas,
        campoTexto: false,
        marcada: o.escolha || null,
        podeConfirmar: !!o.escolha,
      };
    }
    var texto = (o.texto === undefined || o.texto === null) ? '' : String(o.texto);
    return {
      modo: 'texto',
      alternativas: [],
      campoTexto: true,
      espera: q.espera || 'SHORT_TEXT',
      unidade: q.unidade || null,
      marcada: null,
      podeConfirmar: texto.trim().length > 0,
    };
  }

  var IntervencaoUI = {
    proximoPasso: proximoPasso,
    falaDaIntervencao: falaDaIntervencao,
    acaoDaIntervencao: acaoDaIntervencao,
    controles: controles,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = IntervencaoUI;
  } else {
    raiz.IntervencaoUI = IntervencaoUI;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this));
