/**
 * COMPORTAMENTO do relatório de apoio na tela do aluno — executando o código.
 *
 * O §17 tem uma proibição que mora exatamente aqui: "não incluir botão de
 * envio ou compartilhamento integrado". A tela monta botão só a partir de
 * `ACOES`, e o primeiro teste deste arquivo é sobre essa lista não crescer.
 *
 * O resto cuida de não mentir: o que o backend manda é o que aparece, e sem
 * documento carregado não há botão prometendo baixar o vazio.
 */

const test = require('node:test');
const assert = require('node:assert/strict');
const path = require('node:path');

const R = require(path.join(__dirname, '..', 'src', 'agente_ia_edu',
                            'web', 'aluno-relatorio.js'));

const DOC = {
  titulo: 'Apoio à aprendizagem — Ana',
  subtitulo: 'Estequiometria · 08/10/2026',
  ressalva: 'Este material não é uma avaliação da escola.',
  secoes: [
    { chave: 'sozinho', titulo: 'O que você já faz sozinho',
      itens: ['Massa molar — resolveu sozinho.'] },
    { chave: 'com_apoio', titulo: 'O que você conseguiu com apoio',
      itens: ['Mol — resolveu (com 2 dicas).'] },
  ],
};

test('o vocabulario de acoes nao tem verbo de envio', () => {
  const proibidos = ['enviar', 'send', 'compartilhar', 'share', 'encaminhar',
                     'forward', 'notificar', 'notify', 'mail', 'professor',
                     'coordenacao', 'teacher'];
  for (const acao of R.ACOES) {
    for (const p of proibidos) {
      assert.ok(!acao.toLowerCase().includes(p),
                `a acao "${acao}" fala de envio — o §17 proibe`);
    }
  }
});

test('e a lista completa tem exatamente ver e baixar', () => {
  assert.deepEqual(R.ACOES.slice().sort(),
                   ['relatorio-baixar', 'relatorio-ver']);
});

test('sem documento carregado so se oferece ver', () => {
  assert.deepEqual(R.acoesDisponiveis(null), ['relatorio-ver']);
  assert.deepEqual(R.acoesDisponiveis({}), ['relatorio-ver']);
  assert.deepEqual(R.acoesDisponiveis({ titulo: 'x', secoes: [] }),
                   ['relatorio-ver']);
});

test('com documento carregado o baixar aparece', () => {
  assert.ok(R.acoesDisponiveis(DOC).includes('relatorio-baixar'));
});

test('as secoes saem na ordem em que vieram', () => {
  assert.deepEqual(R.secoesParaDesenhar(DOC).map((s) => s.chave),
                   ['sozinho', 'com_apoio']);
});

test('e os itens nao sao reescritos nem resumidos', () => {
  const s = R.secoesParaDesenhar(DOC)[0];
  assert.deepEqual(s.itens, ['Massa molar — resolveu sozinho.']);
});

test('secao sem item nenhum nao vira titulo solto', () => {
  const doc = { titulo: 't', secoes: [{ chave: 'a', titulo: 'A', itens: [] }] };
  assert.deepEqual(R.secoesParaDesenhar(doc), []);
});

test('documento ausente nao explode', () => {
  assert.deepEqual(R.secoesParaDesenhar(null), []);
  assert.deepEqual(R.secoesParaDesenhar({}), []);
});

test('a lista de acoes devolvida e uma copia', () => {
  const a = R.acoesDisponiveis(DOC);
  a.push('relatorio-enviar');
  assert.ok(!R.ACOES.includes('relatorio-enviar'),
            'quem mexeu na lista devolvida contaminou o vocabulario');
});
