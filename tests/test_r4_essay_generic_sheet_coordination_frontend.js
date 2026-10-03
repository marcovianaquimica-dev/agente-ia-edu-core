// Contrato de frontend da secao nova "Redacao" no portal da coordenacao
// (coordination.html + coordination.js) - so o botao de baixar a folha
// padrao, sem dado nenhum pra carregar (coordination.js nao tem
// module.exports, mesmo padrao de teste por texto-fonte do resto da suite).

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync('src/agente_ia_edu/web/coordination.html', 'utf8');
const js = fs.readFileSync('src/agente_ia_edu/web/coordination.js', 'utf8');

test('o menu da coordenacao tem um item Redacao e o painel correspondente', () => {
  assert.match(html, /data-view="essay-sheet"/);
  assert.match(html, /id="view-essay-sheet"/);
  assert.match(html, /id="coord-essay-sheet-btn"/);
});

test('o botao de Redacao baixa a folha padrao, sem exigir proposta nenhuma', () => {
  assert.match(js, /getElementById\('coord-essay-sheet-btn'\)\.addEventListener\('click'/);
  assert.match(js, /fetch\('\/api\/v1\/catalog\/essay-prompts\/answer-sheet\.pdf', \{/);
});

test("o titleMap tem uma entrada pra 'essay-sheet'", () => {
  assert.match(js, /'essay-sheet': \{ title: /);
});
