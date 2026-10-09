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
const UI = require("../src/agente_ia_edu/web/aluno-investigacao.js");
const Inv = UI;

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

// ------------------------------------- os dois níveis do retorno (§10)

test('no primeiro retorno a tela avisa que a ajuda não acabou', () => {
  // Esconder o número sem dizer que há mais parece desamparo. Esta linha é o
  // que transforma a omissão em convite a recontar.
  const dados = { ...ERROU_A_PRIMEIRA,
                  retorno: { ordem: 1, nivel: 1, comentario: 'a regra' } };
  const e = UI.estado(dados, {});
  assert.equal(e.retornoNivel, 1);
  assert.ok(e.convite.length > 10, e.convite);
});

test('no segundo retorno não há mais convite — a conta já foi aberta', () => {
  const dados = { ...ERROU_A_PRIMEIRA,
                  retorno: { ordem: 1, nivel: 2,
                             comentario: 'a regra aplicada' } };
  const e = UI.estado(dados, {});
  assert.equal(e.retornoNivel, 2);
  assert.equal(e.convite, '');
});

test('sem retorno nenhum não há convite', () => {
  const e = UI.estado(ABERTA, {});
  assert.equal(e.retornoNivel, 0);
  assert.equal(e.convite, '');
});

test('o convite não entrega resposta nenhuma', () => {
  const dados = { ...ERROU_A_PRIMEIRA,
                  retorno: { ordem: 1, nivel: 1, comentario: 'a regra' } };
  const baixo = UI.estado(dados, {}).convite.toLowerCase();
  for (const proibido of ['a resposta', 'alternativa', 'marque']) {
    assert.ok(!baixo.includes(proibido), `${proibido} em ${baixo}`);
  }
});

// ===================== A CONVERSA, E NÃO UMA LISTA DE CARDS =============
//
// §19: a tela anterior desenhava "etapas concluídas" numa lista e a pergunta
// aberta embaixo. Funcionava, e não parecia uma conversa — o aluno via um
// formulário com histórico, não alguém falando com ele.

const ABERTURA = {
  key: 'INV-EST-MASSA-MOLAR',
  skill: 'MASSA_MOLAR',
  perguntar_abertura: true,
  abertura: {
    pergunta: 'Qual é a massa molar do NH₃?\nConsidere N = 14 g/mol e H = 1 g/mol.',
    espera: 'NUMERIC', unidade: 'g/mol', respondida: false,
    resposta_do_aluno: null, observacao: null, hipotese: null,
  },
  total_etapas: 3, completed: false, etapa: null, concluidas: [], retorno: null,
};

const DEPOIS_DO_15 = {
  ...ABERTURA,
  perguntar_abertura: false,
  abertura: {
    ...ABERTURA.abertura, respondida: true, resposta_do_aluno: '15',
    observacao: 'INCORRECT_RESPONSE',
    hipotese: 'Esse resultado pode indicar que o índice da fórmula ficou de '
            + 'fora da conta. Vamos conferir uma coisa antes de seguir.',
    hipotese_codigo: 'INDEX_OMISSION', hipotese_estado: 'OPEN',
  },
  etapa: { ordem: 1, skill: 'LEITURA_DE_FORMULA',
           question: 'Na fórmula NH₃, quantos átomos de hidrogênio?',
           options: [{ key: 'A', text: '1' }, { key: 'C', text: '3' }] },
};

const DEPOIS_DO_3 = {
  ...DEPOIS_DO_15,
  concluidas: [{ ordem: 1, question: 'Na fórmula NH₃, quantos átomos de hidrogênio?',
                 correct_option: 'C', comentario: 'Isso. Então a massa molar vai precisar contar o hidrogênio três vezes.' }],
  etapa: { ordem: 2, skill: 'MASSA_MOLAR',
           question: 'Se cada hidrogênio contribui com 1 g/mol...',
           options: [{ key: 'B', text: '3 g/mol' }] },
};

test('a abertura é o primeiro turno, e é do Edu', () => {
  const t = Inv.turnos(ABERTURA);
  assert.equal(t[0].quem, 'edu');
  assert.ok(t[0].texto.includes('NH₃'));
});

test('antes de responder, a conversa tem um turno só', () => {
  assert.equal(Inv.turnos(ABERTURA).length, 1);
});

test('o que o aluno escreveu volta COMO ELE ESCREVEU', () => {
  const t = Inv.turnos(DEPOIS_DO_15);
  const dele = t.find((x) => x.quem === 'aluno');
  assert.equal(dele.texto, '15');
});

test('a tela NÃO mostra "resposta incorreta" no lugar do que ele disse', () => {
  const t = Inv.turnos(DEPOIS_DO_15);
  for (const turno of t.filter((x) => x.quem === 'aluno')) {
    assert.ok(!/incorrect|INCORRECT/i.test(turno.texto), turno.texto);
  }
});

test('a hipótese é um turno DO EDU, e não um rótulo na resposta do aluno', () => {
  // A diferença importa: um turno é algo que o Edu DIZ; um rótulo seria algo
  // que ele DECIDE sobre o aluno.
  const t = Inv.turnos(DEPOIS_DO_15);
  const h = t.find((x) => x.tipo === 'hipotese');
  assert.equal(h.quem, 'edu');
  assert.ok(h.texto.includes('pode indicar'));
});

test('a hipótese vem DEPOIS da resposta dele, não antes', () => {
  const t = Inv.turnos(DEPOIS_DO_15);
  assert.ok(t.findIndex((x) => x.quem === 'aluno')
            < t.findIndex((x) => x.tipo === 'hipotese'));
});

test('sem hipótese não se inventa turno nenhum', () => {
  const sem = { ...DEPOIS_DO_15,
                abertura: { ...DEPOIS_DO_15.abertura, hipotese: null } };
  assert.equal(Inv.turnos(sem).filter((x) => x.tipo === 'hipotese').length, 0);
});

test('a pergunta aberta é sempre o ÚLTIMO turno', () => {
  for (const dados of [DEPOIS_DO_15, DEPOIS_DO_3]) {
    const t = Inv.turnos(dados);
    assert.equal(t[t.length - 1].atual, true);
    assert.equal(t[t.length - 1].quem, 'edu');
  }
});

test('a cadeia cresce: cada etapa resolvida acrescenta turnos', () => {
  assert.ok(Inv.turnos(DEPOIS_DO_3).length > Inv.turnos(DEPOIS_DO_15).length);
});

test('o ensino da etapa errada aparece como turno do Edu', () => {
  const comRetorno = { ...DEPOIS_DO_15,
    retorno: { ordem: 1, nivel: 1, comentario: 'O índice fica colado no símbolo.' } };
  const t = Inv.turnos(comRetorno);
  const e = t.find((x) => x.tipo === 'ensino');
  assert.equal(e.quem, 'edu');
});

test('turnos de payload vazio não estouram', () => {
  assert.deepEqual(Inv.turnos(null), []);
  assert.deepEqual(Inv.turnos({}), []);
});

// ------------------------------------------- a caixa de resposta (§20)

test('na abertura a entrada é de texto', () => {
  const e = Inv.entrada(ABERTURA);
  assert.equal(e.modo, 'texto');
  assert.equal(e.destino, 'abertura');
  assert.equal(e.espera, 'NUMERIC');
});

test('a unidade esperada acompanha a caixa', () => {
  assert.equal(Inv.entrada(ABERTURA).unidade, 'g/mol');
});

test('numa etapa a entrada aceita texto E alternativas', () => {
  const e = Inv.entrada(DEPOIS_DO_15);
  assert.equal(e.modo, 'misto');
  assert.equal(e.destino, 'etapa');
  assert.equal(e.alternativas.length, 2);
});

test('concluída, não há onde responder', () => {
  const e = Inv.entrada({ ...DEPOIS_DO_3, completed: true, etapa: null });
  assert.equal(e.modo, 'nenhum');
  assert.equal(e.alternativas.length, 0);
});

// --------------------------------- o Edu quando não conseguiu ler (§13)

test('ambiguidade pede de novo, sem culpar o aluno', () => {
  const fala = Inv.falaDaObservacao('AMBIGUOUS_RESPONSE').toLowerCase();
  assert.ok(fala.length > 10);
  for (const proibido of ['errado', 'errou', 'inválid', 'invalid']) {
    assert.ok(!fala.includes(proibido), `${proibido} em ${fala}`);
  }
});

test('e não finge que entendeu', () => {
  const fala = Inv.falaDaObservacao('AMBIGUOUS_RESPONSE').toLowerCase();
  assert.ok(fala.includes('não consegui') || fala.includes('pode escrever'));
});

test('"não sei" é acolhido, não punido', () => {
  const fala = Inv.falaDaObservacao('UNKNOWN_RESPONSE').toLowerCase();
  assert.ok(fala.includes('sem problema') || fala.includes('mais curto'));
  for (const proibido of ['errado', 'deveria', 'precisa saber']) {
    assert.ok(!fala.includes(proibido), fala);
  }
});

test('resposta certa não produz fala de observação — quem fala é o backend', () => {
  assert.equal(Inv.falaDaObservacao('CORRECT_RESPONSE'), '');
  assert.equal(Inv.falaDaObservacao('INCORRECT_RESPONSE'), '');
});

// ========================================================================
// A CONVERSA RECARREGADA - o fio sem nenhuma memoria do cliente.
//
// Estes testes passam `turnos(dados)` SEM `opcoes`: e exatamente o que a
// tela tem depois de um F5, quando o transcript do cliente nao existe mais.
// Se o fio depender do que a tela lembrava, eles falham.
// ========================================================================

const RECARREGADO_COM_ERRO = {
  ...DEPOIS_DO_15,
  etapa: { ordem: 1, skill: 'LEITURA_DE_FORMULA',
           question: 'Na fórmula NH₃, quantos átomos de hidrogênio?',
           options: [{ key: 'A', text: '1' }, { key: 'C', text: '3' }],
           resposta_do_aluno: '1' },
  retorno: { ordem: 1, nivel: 1, comentario: 'O índice fica colado no símbolo.' },
};

test('depois de recarregar, o 15 continua no fio', () => {
  const t = Inv.turnos(DEPOIS_DO_15);
  const dele = t.filter((x) => x.quem === 'aluno').map((x) => x.texto);
  assert.deepEqual(dele, ['15']);
});

test('e a hipótese continua sendo um turno do Edu', () => {
  const h = Inv.turnos(DEPOIS_DO_15).find((x) => x.tipo === 'hipotese');
  assert.equal(h.quem, 'edu');
  assert.ok(h.texto.includes('pode indicar'));
});

test('depois de recarregar, a tentativa ERRADA continua no fio', () => {
  const t = Inv.turnos(RECARREGADO_COM_ERRO);
  const dele = t.filter((x) => x.quem === 'aluno').map((x) => x.texto);
  assert.deepEqual(dele, ['15', '1']);
});

test('e o ensino vem DEPOIS da fala que o motivou', () => {
  const t = Inv.turnos(RECARREGADO_COM_ERRO);
  const erro = t.findIndex((x) => x.quem === 'aluno' && x.texto === '1');
  const ensino = t.findIndex((x) => x.tipo === 'ensino');
  assert.ok(erro >= 0 && ensino > erro, `erro=${erro} ensino=${ensino}`);
});

test('a fala gravada vence a grafia do gabarito', () => {
  const comFala = { ...DEPOIS_DO_3,
    concluidas: [{ ...DEPOIS_DO_3.concluidas[0], resposta_texto: 'três' }] };
  const t = Inv.turnos(comFala);
  assert.ok(t.some((x) => x.quem === 'aluno' && x.texto === 'três'));
  assert.ok(!t.some((x) => x.texto === 'C'));
});

test('sem fala gravada o fio nao inventa uma', () => {
  const semFala = { ...DEPOIS_DO_15,
    abertura: { ...DEPOIS_DO_15.abertura, resposta_do_aluno: null } };
  const t = Inv.turnos(semFala);
  assert.equal(t.filter((x) => x.quem === 'aluno').length, 0);
});

// ==========================================================================
// §11 - DOIS CANAIS, MAS NAO DOIS CONTROLES CONCORRENTES
//
// A validacao manual de 2026-10-08 mostrou a tela com A/B/C/D e um campo
// "Sua resposta" lado a lado, sem explicacao: o aluno nao sabe qual vale.
//
// Os dois sao legitimos - quem digita "3" e quem toca na alternativa "3"
// dizem a mesma coisa, e o backend as le igual. Entao a correcao nao e
// tirar um: e dizer QUAL e o principal e o que o outro e.
// ==========================================================================

test('numa etapa o canal PRINCIPAL é escrever', () => {
  const e = Inv.entrada(DEPOIS_DO_15);
  assert.equal(e.canalPrincipal, 'texto');
});

test('e as alternativas vêm rotuladas como ATALHO', () => {
  const e = Inv.entrada(DEPOIS_DO_15);
  assert.ok(e.rotuloDosAtalhos.length > 5, e.rotuloDosAtalhos);
  assert.ok(/toque|escolh|opç/i.test(e.rotuloDosAtalhos), e.rotuloDosAtalhos);
});

test('o rótulo dos atalhos não promete que é obrigatório escolher', () => {
  const r = Inv.entrada(DEPOIS_DO_15).rotuloDosAtalhos.toLowerCase();
  for (const proibido of ['selecione uma', 'marque uma', 'obrigat']) {
    assert.ok(!r.includes(proibido), `${proibido} em "${r}"`);
  }
});

test('na abertura não há atalho nenhum, e o rótulo some', () => {
  const e = Inv.entrada(ABERTURA);
  assert.equal(e.alternativas.length, 0);
  assert.equal(e.rotuloDosAtalhos, '');
  assert.equal(e.canalPrincipal, 'texto');
});

test('concluída não há canal nenhum', () => {
  const e = Inv.entrada({ ...DEPOIS_DO_3, completed: true, etapa: null });
  assert.equal(e.canalPrincipal, null);
  assert.equal(e.rotuloDosAtalhos, '');
});
