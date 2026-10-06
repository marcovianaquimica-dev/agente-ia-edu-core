/**
 * A TELA DA INVESTIGAÇÃO — e as duas coisas que ela não pode fazer.
 *
 * A primeira: avançar o contador quando o aluno errou. Quem errou a etapa 1
 * continua na etapa 1, e um "Etapa 2 de 3" ali diria que ele avançou quando
 * não avançou — e, pior, esconderia dele que é justamente aquela etapa que
 * está sendo trabalhada.
 *
 * A segunda: afirmar o que ele fez. A única coisa observada foi uma letra
 * marcada. "Você esqueceu a proporção" é uma conclusão sobre a cabeça de
 * alguém a partir disso.
 *
 * Estes testes EXECUTAM o módulo (`node --test`), não varrem o fonte.
 */

const test = require('node:test');
const assert = require('node:assert');
const UI = require('../src/agente_ia_edu/web/aluno-investigacao.js');

// Payload como o backend o devolve: a etapa aberta NÃO carrega a correta.
const ABERTA = {
  key: 'INV-EST-PROPORCAO',
  skill: 'PROPORCAO_ESTEQUIOMETRICA',
  hipotese: 'Essa resposta sugere que a conta pode ter ido do mol de N₂ '
          + 'direto para a massa de NH₃.',
  total_etapas: 3,
  completed: false,
  bottleneck_skill: null,
  etapa: { ordem: 1, skill: 'RELACAO_MASSA_MOL', question: '14,0 g de N₂...',
           options: [{ key: 'A', text: '0,25 mol' }, { key: 'B', text: '0,5 mol' }] },
  concluidas: [],
  retorno: null,
};

const UMA_FEITA = {
  ...ABERTA,
  etapa: { ordem: 2, skill: 'PROPORCAO_ESTEQUIOMETRICA',
           question: '0,5 mol de N₂ produzem...',
           options: [{ key: 'A', text: '0,5 mol' }, { key: 'C', text: '1,0 mol' }] },
  concluidas: [{ ordem: 1, question: '14,0 g...', selected: 'B',
                 correct_option: 'B', comentario: 'Isso. Meio mol de N₂.' }],
};

const ERROU_A_PRIMEIRA = {
  ...ABERTA,
  bottleneck_skill: 'RELACAO_MASSA_MOL',
  retorno: { ordem: 1, comentario: 'Para ir de massa para quantidade de '
                                   + 'matéria, divida pela massa molar.' },
};

const CONCLUIDA = {
  ...ABERTA,
  completed: true,
  etapa: null,
  concluidas: [
    { ordem: 1, correct_option: 'B', comentario: 'ok' },
    { ordem: 2, correct_option: 'C', comentario: 'ok' },
    { ordem: 3, correct_option: 'B', comentario: 'ok' },
  ],
};

test('sem alternativa marcada o botão de responder não acende', () => {
  assert.equal(UI.estado(ABERTA, {}).responderHabilitado, false);
});

test('com alternativa marcada ele acende', () => {
  assert.equal(UI.estado(ABERTA, { escolha: 'B' }).responderHabilitado, true);
});

test('sem payload nenhum nada pode ser respondido', () => {
  const e = UI.estado(null, { escolha: 'B' });
  assert.equal(e.podeResponder, false);
  assert.equal(e.responderHabilitado, false);
});

test('concluída não se responde de novo', () => {
  const e = UI.estado(CONCLUIDA, { escolha: 'B' });
  assert.equal(e.podeResponder, false);
  assert.equal(e.responderHabilitado, false);
  assert.equal(e.concluido, true);
});

test('a hipótese chega à tela como o backend a escreveu', () => {
  assert.equal(UI.estado(ABERTA, {}).hipotese, ABERTA.hipotese);
});

// ------------------------------------------------- o contador do progresso

test('no começo é a etapa 1 de 3', () => {
  assert.equal(UI.progresso(ABERTA), 'Etapa 1 de 3');
});

test('depois de uma concluída é a etapa 2 de 3', () => {
  assert.equal(UI.progresso(UMA_FEITA), 'Etapa 2 de 3');
});

test('ERRAR NÃO AVANÇA O CONTADOR', () => {
  // A regra central. O payload de quem errou a etapa 1 tem `concluidas`
  // vazio, e o contador tem de continuar dizendo 1.
  assert.equal(UI.progresso(ERROU_A_PRIMEIRA), 'Etapa 1 de 3');
});

test('no fim o contador não diz "etapa 4 de 3"', () => {
  const texto = UI.progresso(CONCLUIDA);
  assert.ok(!texto.includes('4'), texto);
  assert.ok(texto.includes('3'), texto);
});

test('sem total de etapas não há contador inventado', () => {
  assert.equal(UI.progresso({}), '');
  assert.equal(UI.progresso(null), '');
});

// ------------------------------------------------------- a fala do Assessor

test('na primeira abertura a fala explica para que servem as perguntas', () => {
  const fala = UI.falaDoAssessor(ABERTA, null);
  assert.ok(fala.toLowerCase().includes('nota'), fala);
});

test('retomando no meio, a fala não volta ao início', () => {
  const fala = UI.falaDoAssessor(UMA_FEITA, null);
  assert.ok(fala.toLowerCase().includes('continuar'), fala);
});

test('errar não é chamado de erro', () => {
  const fala = UI.falaDoAssessor(ERROU_A_PRIMEIRA, { correct: false });
  const baixo = fala.toLowerCase();
  for (const proibido of ['errado', 'errou', 'incorreto', 'falhou']) {
    assert.ok(!baixo.includes(proibido), `${proibido} em ${fala}`);
  }
});

test('e a fala de quem errou anuncia que o ponto foi localizado', () => {
  const fala = UI.falaDoAssessor(ERROU_A_PRIMEIRA, { correct: false });
  assert.ok(fala.toLowerCase().includes('etapa'), fala);
});

test('acertar uma etapa não vira elogio de domínio', () => {
  const fala = UI.falaDoAssessor(UMA_FEITA, { correct: true });
  const baixo = fala.toLowerCase();
  for (const proibido of ['você domina', 'dominou', 'aprendeu', 'parabéns']) {
    assert.ok(!baixo.includes(proibido), `${proibido} em ${fala}`);
  }
});

test('NENHUMA FALA AFIRMA O QUE O ALUNO FEZ', () => {
  const casos = [
    [ABERTA, null], [UMA_FEITA, null], [CONCLUIDA, null],
    [ERROU_A_PRIMEIRA, { correct: false }],
    [UMA_FEITA, { correct: true }],
    [CONCLUIDA, { correct: true, completed: true }],
  ];
  const acusatorias = ['você esqueceu', 'você calculou', 'você fez',
                       'você confundiu', 'você pulou', 'seu erro'];
  for (const [dados, resultado] of casos) {
    const baixo = UI.falaDoAssessor(dados, resultado).toLowerCase();
    for (const frase of acusatorias) {
      assert.ok(!baixo.includes(frase), `"${frase}" em: ${baixo}`);
    }
  }
});

test('toda fala tem conteúdo', () => {
  const casos = [[ABERTA, null], [UMA_FEITA, null], [CONCLUIDA, null],
                 [ERROU_A_PRIMEIRA, { correct: false }],
                 [UMA_FEITA, { correct: true }]];
  for (const [dados, resultado] of casos) {
    assert.ok(UI.falaDoAssessor(dados, resultado).trim().length > 20);
  }
});

// ------------------------------------------------------------------- ações

test('NÃO HÁ BOTÃO DE DICA NA INVESTIGAÇÃO', () => {
  // Ela já É o degrau mais alto da escada. Uma dica aqui seria um degrau
  // acima do topo, e o único lugar para onde levaria é a resposta.
  for (const dados of [ABERTA, UMA_FEITA, ERROU_A_PRIMEIRA, CONCLUIDA]) {
    for (const a of UI.acoes(dados)) {
      assert.ok(!['ajuda', 'dica'].includes(a.acao), a.acao);
    }
  }
});

test('aberta, a ação principal é responder', () => {
  const principal = UI.acoes(ABERTA).find((a) => a.principal);
  assert.equal(principal.acao, 'responder');
});

test('concluída, a ação principal leva ao degrau seguinte', () => {
  const principal = UI.acoes(CONCLUIDA).find((a) => a.principal);
  assert.equal(principal.acao, 'seguir');
});

test('há sempre exatamente uma ação principal', () => {
  for (const dados of [ABERTA, UMA_FEITA, ERROU_A_PRIMEIRA, CONCLUIDA]) {
    const n = UI.acoes(dados).filter((a) => a.principal).length;
    assert.equal(n, 1);
  }
});

// -------------------------------------------- o gabarito não chega cedo

test('a etapa aberta que a tela recebe não carrega a correta', () => {
  // O backend já garante isto; o teste existe para que a tela nunca passe a
  // DEPENDER de um campo que não deve existir.
  const e = UI.estado(ABERTA, {});
  assert.equal(e.etapa.correta, undefined);
  for (const o of e.etapa.options) {
    assert.equal(o.correct, undefined);
    assert.equal(o.is_correct, undefined);
  }
});

test('o retorno de quem errou ensina a etapa, sem a letra', () => {
  const e = UI.estado(ERROU_A_PRIMEIRA, {});
  assert.ok(e.retorno.length > 20, e.retorno);
  assert.ok(!/\ba resposta é\b/i.test(e.retorno), e.retorno);
});

test('só as etapas resolvidas mostram o que era', () => {
  assert.equal(UI.estado(ABERTA, {}).concluidas.length, 0);
  assert.equal(UI.estado(UMA_FEITA, {}).concluidas.length, 1);
  assert.equal(UI.estado(UMA_FEITA, {}).concluidas[0].correct_option, 'B');
});
