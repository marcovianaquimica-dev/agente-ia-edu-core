// Contrato de frontend do seletor de abrangencia (Turma | Serie | Escola
// inteira) da aba "Enviar em lote" de essay-review.js.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const js = fs.readFileSync('src/agente_ia_edu/web/essay-review.js', 'utf8');
const batchTab = js.slice(
  js.indexOf('async function renderBatchTab'),
  js.indexOf('async function pollBatch'),
);

test('o formulario de lote busca as series (grade-levels) da escola', () => {
  assert.match(batchTab, /\/api\/v1\/teacher\/essay-batches\/grade-levels/);
});

test('o formulario tem um seletor de abrangencia com as 3 opcoes', () => {
  assert.match(batchTab, /er-batch-scope/);
  assert.match(batchTab, /value="turma"/);
  assert.match(batchTab, /value="serie"/);
  assert.match(batchTab, /value="escola"/);
});

test('o campo de turma nao e mais obrigatorio incondicionalmente', () => {
  // antes: <select id="er-batch-class" class="text-input" required>
  // depois: a obrigatoriedade passa a ser validada em JS conforme o
  // escopo selecionado, nao via atributo HTML fixo - senao o form nunca
  // submeteria com escopo serie/escola (o campo de turma ficaria
  // escondido mas ainda "required").
  assert.doesNotMatch(batchTab, /id="er-batch-class" class="text-input" required/);
});

test('o envio manda grade_level_id quando o escopo e serie, nao class_id', () => {
  assert.match(batchTab, /formData\.append\('grade_level_id'/);
});
