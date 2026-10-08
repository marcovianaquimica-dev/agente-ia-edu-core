/**
 * O FIM DE UMA ATIVIDADE — testes I, J, M e N do bloco.
 *
 * I. Acabaram as questões e há intervenção a fazer → a tela não termina num
 *    encerramento genérico.
 * J. Havendo próximo passo conhecido → o botão principal é o dele.
 * M. Em recuperação → o gabarito não aparece cedo demais.
 * N. Em revisão histórica → o gabarito continua aparecendo.
 *
 * Estes testes EXECUTAM o módulo (`node --test`).
 */

const test = require('node:test');
const assert = require('node:assert');
const UI = require('../src/agente_ia_edu/web/aluno-resultado.js');

const UM_DE_CINCO = { correct_count: 1, question_count: 5 };

const PASSO_COM_INTERVENCAO = {
  kind: 'INVESTIGATE',
  cta: 'Vamos olhar por partes',
  feedback: {
    tom: 'REVISAR',
    titulo: 'Vamos localizar onde massa molar está travando.',
    detalhe: 'São três perguntas curtas, uma etapa de cada vez.',
  },
};

const SEM_PASSO = { kind: 'NONE' };

// ========================================================= I e J ==========

test('com próximo passo, o título é a frase do backend e não o placar', () => {
  const r = UI.resultadoDaAtividade({
    resultado: UM_DE_CINCO, passo: PASSO_COM_INTERVENCAO, temRevisao: true });
  assert.equal(r.titulo, PASSO_COM_INTERVENCAO.feedback.titulo);
  assert.ok(!r.titulo.includes('1 de 5'), r.titulo);
});

test('mas o placar NÃO some — ele vira linha secundária', () => {
  // A escola recebe o número, e esconder do aluno o que a escola vê seria
  // outro problema. O que muda é o destaque.
  const r = UI.resultadoDaAtividade({
    resultado: UM_DE_CINCO, passo: PASSO_COM_INTERVENCAO, temRevisao: true });
  assert.equal(r.placar, 'Você acertou 1 de 5.');
});

test('o botão principal é o CTA do backend', () => {
  const r = UI.resultadoDaAtividade({
    resultado: UM_DE_CINCO, passo: PASSO_COM_INTERVENCAO, temRevisao: true });
  const principal = r.acoes.find((a) => a.principal);
  assert.equal(principal.rotulo, 'Vamos olhar por partes');
  assert.equal(principal.acao, 'seguir');
});

test('"Rever as questões" deixa de ser o botão principal', () => {
  // O achado: o produto oferecia como ação principal voltar ao que já passou,
  // enquanto o motor já sabia o que fazer a seguir.
  const r = UI.resultadoDaAtividade({
    resultado: UM_DE_CINCO, passo: PASSO_COM_INTERVENCAO, temRevisao: true });
  const revisar = r.acoes.find((a) => a.acao === 'revisar');
  assert.ok(revisar, 'a revisão sumiu — ela tem de continuar disponível');
  assert.equal(revisar.principal, false);
});

test('há sempre exatamente uma ação principal', () => {
  const casos = [
    { resultado: UM_DE_CINCO, passo: PASSO_COM_INTERVENCAO, temRevisao: true },
    { resultado: UM_DE_CINCO, passo: PASSO_COM_INTERVENCAO, temRevisao: false },
    { resultado: UM_DE_CINCO, passo: SEM_PASSO, temRevisao: true },
    { resultado: UM_DE_CINCO, passo: SEM_PASSO, temRevisao: false },
  ];
  for (const c of casos) {
    const n = UI.resultadoDaAtividade(c).acoes.filter((a) => a.principal).length;
    assert.equal(n, 1, JSON.stringify(c.passo));
  }
});

test('sem próximo passo, o placar volta a ser o título', () => {
  const r = UI.resultadoDaAtividade({
    resultado: UM_DE_CINCO, passo: SEM_PASSO, temRevisao: true });
  assert.equal(r.titulo, 'Você acertou 1 de 5.');
  assert.equal(r.acoes.find((a) => a.principal).acao, 'revisar');
});

test('sem passo e sem revisão ainda há uma saída', () => {
  const r = UI.resultadoDaAtividade({
    resultado: UM_DE_CINCO, passo: SEM_PASSO, temRevisao: false });
  assert.equal(r.acoes.find((a) => a.principal).acao, 'inicio');
});

test('ACTIVITY não conta como próxima intervenção', () => {
  // "Próximo passo: abrir a atividade", na tela de resultado dela, é um laço.
  assert.equal(UI.temProximaIntervencao({ kind: 'ACTIVITY' }), false);
  assert.equal(UI.temProximaIntervencao({ kind: 'NONE' }), false);
  assert.equal(UI.temProximaIntervencao({}), false);
  assert.equal(UI.temProximaIntervencao(null), false);
});

test('os passos de apoio contam como próxima intervenção', () => {
  for (const k of ['INVESTIGATE', 'LEARN', 'GUIDED_PRACTICE', 'PRACTICE',
                   'VERIFY', 'ESCALATE']) {
    assert.equal(UI.temProximaIntervencao({ kind: k }), true, k);
  }
});

test('sem feedback do backend a tela NÃO inventa pedagogia', () => {
  const r = UI.resultadoDaAtividade({
    resultado: UM_DE_CINCO, passo: { kind: 'PRACTICE', cta: 'Praticar agora' },
    temRevisao: true });
  assert.equal(r.titulo, 'Você acertou 1 de 5.');
});

test('a escola continua sendo informada, em linha própria', () => {
  const r = UI.resultadoDaAtividade({
    resultado: UM_DE_CINCO, passo: PASSO_COM_INTERVENCAO, temRevisao: true });
  assert.ok(r.nota.toLowerCase().includes('escola'), r.nota);
});

test('sem contagem não se inventa placar', () => {
  assert.equal(UI.placarDe({}), '');
  assert.equal(UI.placarDe(null), '');
  assert.equal(UI.placarDe({ correct_count: 0, question_count: 3 }),
               'Você acertou 0 de 3.');
});

// ========================================================= M e N ==========

test('N — em revisão histórica o gabarito aparece', () => {
  assert.equal(UI.revelaGabarito(UI.MODO_REVISAO, {}), true);
  assert.equal(UI.revelaGabarito(UI.MODO_REVISAO, { explicacaoLida: false }),
               true);
});

test('M — em recuperação, antes da explicação, não aparece', () => {
  assert.equal(UI.revelaGabarito(UI.MODO_RECUPERACAO, {}), false);
});

test('e em recuperação, DEPOIS da explicação, aparece', () => {
  // O gabarito não fica escondido para sempre — ele muda de função: deixa de
  // encerrar a questão e passa a servir para conferir o raciocínio.
  assert.equal(
    UI.revelaGabarito(UI.MODO_RECUPERACAO, { explicacaoLida: true }), true);
});

test('modo desconhecido revela — na dúvida, não esconder do aluno', () => {
  assert.equal(UI.revelaGabarito(undefined, {}), true);
  assert.equal(UI.revelaGabarito('QUALQUER_COISA', {}), true);
});

test('em recuperação o selo "correta" não viaja antes da hora', () => {
  const ctx = { modo: UI.MODO_RECUPERACAO, correta: 'C', marcada: 'A' };
  assert.equal(UI.seloDaAlternativa({ key: 'C' }, ctx), null);
});

test('mas o aluno continua vendo o que ELE marcou', () => {
  const ctx = { modo: UI.MODO_RECUPERACAO, correta: 'C', marcada: 'A' };
  const selo = UI.seloDaAlternativa({ key: 'A' }, ctx);
  assert.equal(selo.tipo, 'marcada');
  assert.ok(selo.texto.includes('sua resposta'));
});

test('em revisão o selo "correta" aparece', () => {
  const ctx = { modo: UI.MODO_REVISAO, correta: 'C', marcada: 'A' };
  assert.equal(UI.seloDaAlternativa({ key: 'C' }, ctx).tipo, 'certa');
});

test('depois de ler a explicação, a correta aparece também na recuperação', () => {
  const ctx = { modo: UI.MODO_RECUPERACAO, correta: 'C', marcada: 'A',
                explicacaoLida: true };
  assert.equal(UI.seloDaAlternativa({ key: 'C' }, ctx).tipo, 'certa');
});

test('alternativa que não é nem a marcada nem a certa não tem selo', () => {
  const ctx = { modo: UI.MODO_REVISAO, correta: 'C', marcada: 'A' };
  assert.equal(UI.seloDaAlternativa({ key: 'B' }, ctx), null);
});

test('a etiqueta diz que errou nos DOIS modos', () => {
  // Esconder isso deixaria o aluno sem entender por que está sendo ajudado.
  for (const modo of [UI.MODO_REVISAO, UI.MODO_RECUPERACAO]) {
    const etiqueta = UI.etiquetaDaQuestao(false, modo);
    assert.ok(etiqueta.trim().length > 0, modo);
    assert.ok(!etiqueta.includes('acertou'), etiqueta);
  }
});

test('a etiqueta da recuperação convida em vez de acusar', () => {
  const etiqueta = UI.etiquetaDaQuestao(false, UI.MODO_RECUPERACAO);
  assert.ok(!etiqueta.toLowerCase().includes('errou'), etiqueta);
});

test('quem acertou vê que acertou nos dois modos', () => {
  for (const modo of [UI.MODO_REVISAO, UI.MODO_RECUPERACAO]) {
    assert.ok(UI.etiquetaDaQuestao(true, modo).includes('acertou'), modo);
  }
});
