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
      // 1 = a regra, sem o numero. 2 = a regra aplicada. Quem escolhe e o
      // backend, pela contagem de tentativas daquela etapa.
      retornoNivel: (d.retorno && d.retorno.nivel) || 0,
      // SO NO PRIMEIRO NIVEL. O aluno acabou de receber a regra sem o
      // resultado, e precisa saber que a ajuda nao acabou - senao esconder o
      // numero parece desamparo em vez de convite a recontar.
      convite: (d.retorno && d.retorno.nivel === 1)
        ? 'Tente recontar com essa regra. Se ainda não sair, eu abro a conta.'
        : '',
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

  /**
   * A CONVERSA, montada do payload - turno a turno.
   *
   * POR QUE ISTO E UMA FUNCAO PURA
   * ===============================
   * A tela anterior desenhava "etapas concluidas" numa lista e a pergunta
   * aberta embaixo. Funcionava, e nao parecia uma conversa: o aluno via um
   * formulario com historico, nao alguem falando com ele.
   *
   * Aqui o payload vira uma sequencia de turnos - quem fala, o que diz - e
   * a tela so desenha. Nada e decidido aqui: a pergunta, a frase da
   * hipotese e o ensino da etapa vem todos do backend.
   *
   * O QUE O ALUNO DISSE VOLTA COMO ELE DISSE
   * =========================================
   * "15", e nao "resposta incorreta". A leitura que o sistema fez do texto
   * dele nao substitui o texto dele - e ver a propria resposta na conversa
   * e o que torna a sequencia legivel depois.
   */
  function turnos(dados, opcoes) {
    var d = dados || {};
    var o = opcoes || {};
    // O QUE ELE DIGITOU NESTA SESSAO, por ordem. O backend nao guarda o
    // texto - `GuidedPracticeItem` nao tem coluna para isso, e manter zero
    // migration foi uma escolha. Entao a tela lembra do que ELA enviou.
    //
    // O limite esta declarado: ao RECARREGAR a pagina o "15" se perde, e a
    // conversa recomeca do que o backend sabe. Dentro da sessao - que e
    // quando a continuidade importa - ela esta inteira.
    var dito = o.dito || {};
    var fios = [];
    var a = d.abertura || null;

    if (a && a.pergunta) {
      fios.push({ quem: 'edu', tipo: 'pergunta', texto: a.pergunta });
      var daAbertura = a.resposta_do_aluno || dito.abertura;
      if (a.respondida && daAbertura) {
        fios.push({ quem: 'aluno', tipo: 'resposta', texto: daAbertura });
      }
      // A HIPOTESE tambem some ao recarregar, pelo mesmo motivo - ela e
      // derivada do valor que ele escreveu.
      // A HIPOTESE, quando o valor sugeriu uma. Ela e um turno do Edu, e
      // nao um rotulo colado na resposta do aluno: a diferenca e que um
      // turno e algo que o Edu DIZ, e um rotulo seria algo que ele DECIDE
      // sobre ele.
      var hip = a.hipotese || dito.hipotese;
      if (hip) {
        fios.push({ quem: 'edu', tipo: 'hipotese', texto: hip });
      }
    }

    (d.concluidas || []).forEach(function (c) {
      fios.push({ quem: 'edu', tipo: 'pergunta', texto: c.question });
      // O que ele DISSE, nessa ordem de preferencia: o texto que a tela
      // enviou, o conteudo da alternativa, e so entao a letra.
      fios.push({ quem: 'aluno', tipo: 'resposta',
                  texto: dito[c.ordem] || c.resposta_texto || c.correct_option,
                  resolvida: true });
      if (c.comentario) {
        fios.push({ quem: 'edu', tipo: 'fala', texto: c.comentario });
      }
    });

    // A TENTATIVA QUE NAO FICOU DE PE.
    //
    // Ela nao entra em `concluidas` - so o que foi resolvido entra -, e sem
    // este bloco o que o aluno disse ao errar sumia da conversa. Ficava o
    // ensino sem a fala que o motivou, e a sequencia deixava de fazer
    // sentido: o Edu parecia explicar do nada.
    if (d.retorno && d.retorno.comentario) {
      var errou = dito[d.retorno.ordem];
      if (errou) {
        fios.push({ quem: 'aluno', tipo: 'resposta', texto: errou });
      }
      fios.push({ quem: 'edu', tipo: 'ensino', texto: d.retorno.comentario });
    }

    // A FALA DE QUANDO O EDU NAO CONSEGUIU LER - ou de acolhimento, quando
    // ele disse que nao sabe. Ela vem ANTES da pergunta, nao depois:
    // medido no navegador, "Sem problema, vamos por um caminho mais curto"
    // aparecia abaixo da pergunta que ela introduz, e a conversa lia ao
    // contrario.
    var fala = falaDaObservacao(o.observacao);
    if (fala) {
      fios.push({ quem: 'edu', tipo: 'acolhimento', texto: fala });
    }

    // E a pergunta aberta AGORA, por ultimo - e sempre a ultima coisa na
    // tela, porque e a unica que espera algo dele.
    if (d.etapa && d.etapa.question) {
      fios.push({ quem: 'edu', tipo: 'pergunta', texto: d.etapa.question,
                  atual: true });
    }
    return fios;
  }

  /**
   * O que a caixa de resposta deve ser agora.
   *
   * A interface escolhe o componente conforme o que o instrumento pede -
   * §20. Nao e tudo textarea: uma etapa de alternativas continua oferecendo
   * as alternativas, E aceitando texto, porque as duas coisas chegam ao
   * mesmo lugar no backend.
   */
  function entrada(dados) {
    var d = dados || {};
    var a = d.abertura || null;
    if (d.perguntar_abertura && a) {
      return { modo: 'texto', destino: 'abertura',
               espera: a.espera || 'SHORT_TEXT',
               unidade: a.unidade || null,
               rotulo: 'Sua resposta',
               alternativas: [] };
    }
    if (d.completed || !d.etapa) {
      return { modo: 'nenhum', destino: null, alternativas: [] };
    }
    return { modo: 'misto', destino: 'etapa',
             espera: 'SHORT_TEXT', unidade: null,
             rotulo: 'Sua resposta',
             alternativas: (d.etapa.options || []).slice() };
  }

  /**
   * O que o Edu diz quando NAO conseguiu ler o que o aluno escreveu.
   *
   * Isto nao e erro do aluno, e a frase nao pode soar como se fosse. E
   * tambem nao pode fingir que entendeu: pedir de novo, mais simples, e a
   * unica saida honesta.
   */
  function falaDaObservacao(observacao) {
    if (observacao === 'AMBIGUOUS_RESPONSE') {
      return 'Não consegui ler sua resposta com certeza. Pode escrever só o '
           + 'número?';
    }
    if (observacao === 'EMPTY_RESPONSE') {
      return 'Faltou a resposta — escreva o que você achar, mesmo sem '
           + 'certeza.';
    }
    if (observacao === 'UNKNOWN_RESPONSE') {
      return 'Sem problema. Vamos por um caminho mais curto.';
    }
    return '';
  }

  var InvestigacaoUI = {
    turnos: turnos,
    entrada: entrada,
    falaDaObservacao: falaDaObservacao,
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
