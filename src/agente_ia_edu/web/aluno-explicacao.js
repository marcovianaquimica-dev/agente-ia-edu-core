/**
 * ENTENDER O ERRO — as decisões de interface, testáveis.
 *
 * O QUE MUDOU, E POR QUÊ
 * =======================
 * Até 2026-10-06 a revisão de uma questão errada abria um `<details>` com o
 * campo `resolution`. Nenhuma das 595 questões do acervo tem resolução
 * curada, então o que o aluno lia, sempre, era:
 *
 *     "Não há resolução oficial passo a passo armazenada para esta questão.
 *      A geração de resolução por IA é uma fase futura e não é usada aqui."
 *
 * Duas frases sobre a dívida técnica do produto, para quem acabou de errar.
 *
 * Agora a explicação é pedida ao backend (`POST .../result/explanation`), que
 * decide a fonte — curada, IA ou fallback — e devolve texto de aluno.
 *
 * O QUE ESTE MÓDULO NÃO FAZ
 * ==========================
 * Não escolhe a estratégia: ele informa qual já foi mostrada e o backend
 * decide a próxima. Não conclui nada sobre domínio. E nenhuma das ações
 * daqui é evidência: "entendi" é uma intenção de tentar, não uma medida.
 */
(function (raiz) {
  'use strict';

  /**
   * O que o aluno pode fazer, dado o estado da explicação.
   *
   * Antes de haver explicação há UMA porta, e ela é um convite, não um
   * aviso. Depois, três reações — e as três levam a lugares diferentes:
   * tentar, outro jeito, perguntar. "Entendi" NÃO libera nada; ele apenas
   * devolve o aluno ao passo que o backend já decidiu.
   */
  function acoes(estado) {
    var e = estado || {};
    if (!e.texto) {
      return [{ acao: 'explicar', rotulo: 'Entenda o que aconteceu',
                principal: true }];
    }
    return [
      { acao: 'entendi', rotulo: 'Entendi, quero tentar', principal: true },
      { acao: 'outro-jeito', rotulo: 'Explique de outro jeito' },
      { acao: 'duvida', rotulo: 'Tenho uma dúvida' },
    ];
  }

  /**
   * O corpo do pedido de explicação.
   *
   * `previous_strategy` só vai quando JÁ houve uma: é o que transforma
   * "explique de outro jeito" em outro jeito de verdade. Numa primeira
   * explicação mandá-la faria o backend pular a primeira abordagem sem
   * motivo.
   */
  function pedido(questionVersionId, estado) {
    var corpo = { question_version_id: questionVersionId };
    var anterior = (estado || {}).estrategia;
    if (anterior) corpo.previous_strategy = anterior;
    return corpo;
  }

  /**
   * O que a tela mostra depois da resposta.
   *
   * `fallback` não se esconde: significa que a explicação não veio da IA, e o
   * aluno tem direito de saber que o que está lendo é um caminho genérico. O
   * que a tela NUNCA mostra é a fonte técnica (`CURADA`/`IA`), o nome do
   * provedor ou a versão do prompt — isso é do sistema.
   */
  function leitura(resposta) {
    var r = resposta || {};
    var texto = String(r.texto || '').trim();
    if (!texto) {
      return { texto: '', estrategia: null, fallback: true, pronta: false };
    }
    return {
      texto: texto,
      estrategia: r.estrategia || null,
      fallback: r.fallback === true,
      pronta: true,
    };
  }

  /** A mensagem de falha de rede — honesta, e sem jargão. */
  function leituraDaFalha() {
    return 'Não consegui carregar a explicação agora. Você pode tentar de '
         + 'novo, ou seguir pelo botão abaixo — ele não depende disto.';
  }

  var ExplicacaoUI = {
    acoes: acoes,
    pedido: pedido,
    leitura: leitura,
    leituraDaFalha: leituraDaFalha,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = ExplicacaoUI;
  } else {
    raiz.ExplicacaoUI = ExplicacaoUI;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this));
