/**
 * COMPORTAMENTO do "Entenda o que aconteceu" — executando o código.
 *
 * A tela do erro é onde é mais fácil mentir sem querer: um "pronto, agora
 * você sabe", um botão que libera o que não deveria, ou o texto interno do
 * sistema vazando para o aluno. Estes testes cuidam da camada de
 * apresentação; a decisão pedagógica é do backend, e tem a sua.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const E = require(path.join(__dirname, '..', 'src', 'agente_ia_edu',
                            'web', 'aluno-explicacao.js'));

test('antes de haver explicacao ha uma porta so, e ela convida', () => {
  const acoes = E.acoes({});
  assert.equal(acoes.length, 1);
  assert.equal(acoes[0].acao, 'explicar');
  assert.match(acoes[0].rotulo, /aconteceu/i);
});

test('depois da explicacao o aluno pode reagir de tres jeitos', () => {
  const acoes = E.acoes({ texto: 'Você parou no NH3.' });
  assert.deepEqual(acoes.map((a) => a.acao),
                   ['entendi', 'outro-jeito', 'duvida']);
});

test('nenhuma acao promete que o aluno aprendeu', () => {
  const rotulos = E.acoes({ texto: 'x' }).map((a) => a.rotulo.toLowerCase());
  for (const r of rotulos) {
    for (const proibido of ['aprendi', 'dominei', 'liberar', 'pular',
                            'concluir etapa', 'avançar']) {
      assert.ok(!r.includes(proibido), `rótulo promete domínio: ${r}`);
    }
  }
});

test('"entendi" e uma intencao de tentar, nao uma conclusao', () => {
  const entendi = E.acoes({ texto: 'x' })[0];
  assert.match(entendi.rotulo, /quero tentar/i);
});

test('a primeira explicacao nao manda estrategia anterior', () => {
  const corpo = E.pedido('abc', {});
  assert.deepEqual(Object.keys(corpo), ['question_version_id']);
});

test('pedir outro jeito manda a estrategia ja mostrada', () => {
  const corpo = E.pedido('abc', { estrategia: 'CONCEITO' });
  assert.equal(corpo.previous_strategy, 'CONCEITO');
});

test('a tela guarda a estrategia que voltou, para pedir a seguinte', () => {
  const l = E.leitura({ texto: 'Veja assim.', estrategia: 'EXEMPLO' });
  assert.equal(l.estrategia, 'EXEMPLO');
  assert.equal(l.pronta, true);
});

test('fallback nao se esconde do aluno', () => {
  const l = E.leitura({ texto: 'Tente conferir o enunciado.', fallback: true });
  assert.equal(l.fallback, true);
});

test('resposta vazia nao vira explicacao em branco', () => {
  assert.equal(E.leitura({ texto: '   ' }).pronta, false);
  assert.equal(E.leitura(null).pronta, false);
});

test('a leitura nao devolve fonte, provedor nem versao de prompt', () => {
  const l = E.leitura({ texto: 'x', fonte: 'IA', provider: 'openai',
                        model: 'gpt-x', prompt_version: 'v1' });
  for (const campo of ['fonte', 'provider', 'model', 'prompt_version']) {
    assert.ok(!(campo in l), `vazou campo interno para a tela: ${campo}`);
  }
});

test('a falha de rede nao mostra jargao nem divida tecnica', () => {
  const m = E.leituraDaFalha().toLowerCase();
  for (const proibido of ['fase futura', 'provider', 'json', 'http',
                          'erro 5', 'timeout', 'stack']) {
    assert.ok(!m.includes(proibido), `mensagem com jargão: ${proibido}`);
  }
});
