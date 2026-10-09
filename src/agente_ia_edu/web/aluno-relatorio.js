/**
 * RELATÓRIO DE APOIO À APRENDIZAGEM - as decisões de interface, testáveis.
 *
 * O documento chega PRONTO do backend: títulos, itens, ressalva, ordem das
 * seções. Esta tela não resume, não reordena, não classifica e não calcula
 * nada - ela desenha o que recebeu.
 *
 * O VOCABULÁRIO DE AÇÕES É FECHADO
 * =================================
 * `ACOES` é a lista COMPLETA do que esta tela pode oferecer, e ela tem dois
 * itens: ver e baixar. O §17 proíbe enviar ao professor, encaminhar à
 * Coordenação, compartilhar e notificar - "não incluir botão de envio ou
 * compartilhamento integrado".
 *
 * Deixar isso por ausência seria frágil: o próximo a mexer aqui não saberia
 * que não podia. Então a tela monta botão APENAS a partir desta lista, e há
 * teste exigindo que ela não cresça para um verbo de envio.
 *
 * O aluno pode mostrar o PDF a quem quiser, por conta própria. O que a
 * plataforma não faz é mandar.
 *
 * NÃO É DIAGNÓSTICO
 * ==================
 * A ressalva vem do backend e é desenhada SEMPRE, junto do documento - um
 * papel que o aluno leva a um adulto não pode ser lido como laudo da escola.
 */
(function (raiz) {
  'use strict';

  // A LISTA COMPLETA. Ver o cabeçalho: ela não cresce para "enviar".
  var ACOES = ['relatorio-ver', 'relatorio-baixar'];

  function temConteudo(doc) {
    return !!(doc && doc.titulo && Array.isArray(doc.secoes) && doc.secoes.length);
  }

  /**
   * As ações oferecidas AGORA.
   *
   * Sem documento carregado não há o que baixar, e um botão que baixaria o
   * vazio é pior que um botão ausente.
   */
  function acoesDisponiveis(doc) {
    return temConteudo(doc) ? ACOES.slice() : ['relatorio-ver'];
  }

  /**
   * As seções, na ordem em que vieram.
   *
   * Seção sem item nenhum não é desenhada aqui porque o backend já não a
   * manda vazia: ele preenche com "ainda não há registro". Se um dia vier
   * vazia mesmo assim, desenhar o título sozinho faria o aluno supor.
   */
  function secoesParaDesenhar(doc) {
    if (!doc || !Array.isArray(doc.secoes)) return [];
    return doc.secoes
      .filter(function (s) { return s && Array.isArray(s.itens) && s.itens.length; })
      .map(function (s) {
        return { chave: s.chave || '', titulo: s.titulo || '', itens: s.itens.slice() };
      });
  }

  /** O nome do arquivo que o aluno vê na pasta de downloads. */
  function nomeDoArquivo() {
    return 'apoio-a-aprendizagem.pdf';
  }

  var RelatorioUI = {
    ACOES: ACOES,
    acoesDisponiveis: acoesDisponiveis,
    secoesParaDesenhar: secoesParaDesenhar,
    nomeDoArquivo: nomeDoArquivo,
  };

  if (typeof module !== 'undefined' && module.exports) {
    module.exports = RelatorioUI;
  } else {
    raiz.RelatorioUI = RelatorioUI;
  }
}(typeof globalThis !== 'undefined' ? globalThis : this));
