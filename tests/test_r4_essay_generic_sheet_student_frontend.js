// Contrato de frontend do botao "Baixar folha de redacao em branco" no
// portal do aluno (essay.js) - mesmo padrao de teste por texto-fonte de
// test_r2_prompt_entry_and_audience_frontend.js (essay.js nao tem
// module.exports pra renderList, so pra init).

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const js = fs.readFileSync('src/agente_ia_edu/web/essay.js', 'utf8');
const renderListFn = js.slice(
  js.indexOf('function renderList()'),
  js.indexOf('async function loadEvolutionSection'),
);

test('a lista de propostas do aluno tem um botao avulso pra baixar a folha padrao', () => {
  assert.match(renderListFn, /Baixar folha de redação em branco/);
  assert.match(renderListFn, /fetch\('\/api\/v1\/student\/essay-prompts\/answer-sheet\.pdf', \{ headers: essayHeaders\(\) \}\)/);
});
