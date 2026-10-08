/**
 * PERGUNTE AO ASSESSOR - as decisões de interface da conversa, testáveis.
 *
 * A conversa acontece DENTRO da intervenção: o contexto pedagógico é montado
 * no backend, a partir da prontidão. Esta tela não constrói verdade
 * pedagógica nenhuma - ela coleta a pergunta, mostra a resposta e devolve o
 * aluno ao percurso que o backend decidiu.
 *
 * O QUE ELA NUNCA FAZ
 * ====================
 * Dizer que o aluno aprendeu, liberar atividade, escolher próximo passo. O
 * CTA de volta vem de `next_step` na resposta - a conversa não o inventa.
 *
 * A CONVERSA NÃO É PERSISTIDA
 * ============================
 * O histórico vive aqui, em memória, e vai junto em cada pergunta. Recarregar
 * a página perde a conversa, e a tela diz isso. Guardar texto de aluno exige
 * decisão de retenção que ainda não foi tomada; fingir que guarda seria pior.
 */
(function (raiz) {
  'use strict';

  // Quantos turnos a tela guarda e manda de volta. O backend tem o seu próprio
  // limite; este existe para a tela não crescer sem fim numa sessão longa.
  var TURNOS_NA_TELA = 12;

  /** O histórico com o turno novo, já podado. */
  function comTurno(historico, de, texto) {
    var limpo = String(texto == null ? '' : texto).trim();
    if (!limpo) return (historico || []).slice();
    var lista = (historico || []).concat([{ de: de, texto: limpo }]);
    return lista.slice(-TURNOS_NA_TELA);
  }

  /**
   * Dá para enviar?
   *
   * Vazio não vai (não é pergunta), e nada vai enquanto a anterior não voltou:
   * duas perguntas em voo deixariam as respostas chegando fora de ordem, e o
   * aluno lendo a resposta da pergunta errada.
   */
  function podeEnviar(texto, estado) {
    return !!String(texto == null ? '' : texto).trim()
      && (estado || {}).enviando !== true;
  }

  /**
   * O que a tela mostra depois de uma resposta.
   *
   * `fallback` não é um detalhe de implementação que se esconde: significa que
   * a IA não respondeu, e o aluno tem direito de saber que o que ele está
   * lendo não veio dela.
   */
  function leituraDaResposta(resposta) {
    var r = resposta || {};
    var texto = String(r.reply || '').trim();
    if (!texto) {
      return { texto: 'Não consegui responder agora.', fallback: true,
               cta: null, fecho: null, retomada: null, percurso: null };
    }
    var passo = r.next_step || {};
    return {
      texto: texto,
      fallback: r.fallback === true,
      // O botão de volta é o passo REAL do sistema. Sem ele, nenhum botão -
      // melhor que um que não leva a lugar nenhum.
      cta: (passo.kind && passo.cta) ? { kind: passo.kind, rotulo: passo.cta }
                                     : null,
      // A SAIDA SEM ATIVIDADE - §6.
      //
      // Ate 2026-10-08 a unica porta de saida da conversa era o botao do
      // proximo passo da escada: uma duvida respondida virava atividade,
      // sempre. O §6 diz que nao e obrigatorio verificar cada intervencao
      // com uma questao nova.
      //
      // Quem autoriza e o BACKEND (`services/concisao.pode_encerrar`), e
      // ela nao substitui o passo: os dois aparecem lado a lado, e o aluno
      // escolhe. Resposta antiga, sem os campos, nao ganha saida - e por
      // isso a checagem e pelo rotulo, que so o backend novo envia.
      fecho: (r.pode_encerrar === true && r.rotulo_de_fecho)
        ? { rotulo: String(r.rotulo_de_fecho) } : null,
      // A VOLTA AO PONTO ANTERIOR, COM NOME - §9.
      //
      // "Voltar ao percurso" não diz para onde. O §9 pede retorno ao ponto
      // anterior sem perda de contexto, e saber o que se está retomando é
      // parte do contexto. O rótulo vem montado do backend
      // (`services/percurso.retomada`): a tela não escreve o nome do
      // destino, porque quem sabe onde ele estava é o servidor.
      //
      // Sem destino, nada - um botão que não leva a lugar nenhum é pior que
      // nenhum botão.
      //
      // E ela carrega A ATIVIDADE, não o próximo passo: o botão reabre
      // aquela atividade. Sem `assignment_id` não há para onde voltar, e
      // então não há botão - era esse o defeito achado no QA de 2026-10-08,
      // quando o destino era `next_step.kind` e o rótulo prometia a
      // atividade enquanto a ação despachava uma escalação.
      retomada: (r.retomada && r.retomada.rotulo && r.retomada.assignment_id)
        ? { rotulo: String(r.retomada.rotulo),
            destino: String(r.retomada.destino || ''),
            atividade: String(r.retomada.assignment_id) } : null,
      // Em que percurso o backend disse que esta resposta aconteceu. A tela
      // não classifica nada - ela só sabe se deve oferecer a volta.
      percurso: r.percurso ? String(r.percurso) : null,
    };
  }

  /** A mensagem de erro de rede - honesta, e sem jargão. */
  function leituraDaFalha(erro) {
    var e = erro || {};
    if (e.status === 422) {
      return 'Essa pergunta ficou grande demais. Tente em menos palavras.';
    }
    return 'Não consegui falar com o Assessor agora. Tente de novo em '
         + 'instantes — ou siga pelo botão abaixo, que não depende disto.';
  }

  var ConversaUI = {
    comTurno: comTurno,
    podeEnviar: podeEnviar,
    leituraDaResposta: leituraDaResposta,
    leituraDaFalha: leituraDaFalha,
    TURNOS_NA_TELA: TURNOS_NA_TELA,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = ConversaUI;
  } else {
    raiz.ConversaUI = ConversaUI;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this));
