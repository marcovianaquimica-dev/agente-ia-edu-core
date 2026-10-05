/**
 * COMPORTAMENTO do "Pergunte ao Assessor" — executando o código.
 *
 * A conversa é a parte do Assessor em que é mais fácil mentir sem querer:
 * um balão a mais, um "pronto, você aprendeu", um botão que promete algo que
 * não existe. Estes testes cuidam disso na camada de apresentação; o backend
 * cuida do resto, e tem os seus.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const C = require(path.join(__dirname, '..', 'src', 'agente_ia_edu',
                            'web', 'aluno-conversa.js'));

test('o turno novo entra no fim do historico', () => {
  const h = C.comTurno([{ de: 'aluno', texto: 'oi' }], 'assessor', 'olá');
  assert.deepEqual(h.map((t) => t.de), ['aluno', 'assessor']);
  assert.equal(h[1].texto, 'olá');
});

test('turno vazio nao entra', () => {
  assert.deepEqual(C.comTurno([], 'aluno', '   '), []);
  assert.deepEqual(C.comTurno([], 'aluno', null), []);
});

test('o historico nao cresce sem fim', () => {
  let h = [];
  for (let i = 0; i < C.TURNOS_NA_TELA + 10; i += 1) {
    h = C.comTurno(h, 'aluno', `pergunta ${i}`);
  }
  assert.equal(h.length, C.TURNOS_NA_TELA);
  assert.match(h[h.length - 1].texto, new RegExp(`${C.TURNOS_NA_TELA + 9}$`));
});

test('nao se envia pergunta vazia', () => {
  assert.equal(C.podeEnviar('', {}), false);
  assert.equal(C.podeEnviar('   ', {}), false);
  assert.equal(C.podeEnviar('por quê?', {}), true);
});

test('nao se envia duas perguntas ao mesmo tempo', () => {
  // Duas em voo fariam as respostas chegarem fora de ordem, e o aluno leria
  // a resposta da pergunta errada.
  assert.equal(C.podeEnviar('outra', { enviando: true }), false);
  assert.equal(C.podeEnviar('outra', { enviando: false }), true);
});

test('a resposta do Assessor aparece como veio', () => {
  const r = C.leituraDaResposta({ reply: 'O índice é da fórmula.',
                                  fallback: false });
  assert.equal(r.texto, 'O índice é da fórmula.');
  assert.equal(r.fallback, false);
});

test('o fallback NAO se disfarca de resposta da IA', () => {
  // O aluno tem direito de saber que o que ele lê não veio do modelo.
  const r = C.leituraDaResposta({ reply: 'Não consegui responder agora.',
                                  fallback: true });
  assert.equal(r.fallback, true);
});

test('resposta vazia e tratada como falha, nao como balao em branco', () => {
  const r = C.leituraDaResposta({ reply: '   ' });
  assert.equal(r.fallback, true);
  assert.ok(r.texto.trim().length > 0);
});

test('o botao de volta e o passo REAL do backend', () => {
  const r = C.leituraDaResposta({
    reply: 'x', next_step: { kind: 'PRACTICE', cta: 'Praticar agora' } });
  assert.deepEqual(r.cta, { kind: 'PRACTICE', rotulo: 'Praticar agora' });
});

test('sem passo do backend, nenhum botao e inventado', () => {
  assert.equal(C.leituraDaResposta({ reply: 'x' }).cta, null);
  assert.equal(C.leituraDaResposta({ reply: 'x', next_step: {} }).cta, null);
  assert.equal(
    C.leituraDaResposta({ reply: 'x', next_step: { kind: 'PRACTICE' } }).cta,
    null, 'tipo sem rotulo viraria botao mudo');
});

test('a conversa nunca declara dominio', () => {
  const r = C.leituraDaResposta({
    reply: 'Agora você dominou!', next_step: { kind: 'LEARN', cta: 'Estudar' } });
  // O texto é do modelo e a tela o mostra como veio - mas o CTA, que é a
  // AÇÃO, continua vindo do backend, não da frase.
  assert.equal(r.cta.kind, 'LEARN');
});

test('falha de rede tem frase honesta e sem jargao', () => {
  const texto = C.leituraDaFalha({ status: 500 });
  assert.ok(texto.trim().length > 0);
  for (const jargao of ['500', 'erro', 'fetch', 'undefined', 'null']) {
    assert.doesNotMatch(texto.toLowerCase(), new RegExp(jargao));
  }
});

test('pergunta longa demais recebe orientacao, nao codigo', () => {
  const texto = C.leituraDaFalha({ status: 422 });
  assert.match(texto.toLowerCase(), /menos palavras|grande demais/);
});
