// Contrato de frontend da tela de entrada (Criar proposta | Usar do
// banco) e do seletor de publico combinado, em essay-review.js.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const js = fs.readFileSync('src/agente_ia_edu/web/essay-review.js', 'utf8');
const promptsListFn = js.slice(
  js.indexOf('async function renderPromptsList'),
  js.indexOf('async function renderTrashTab'),
);
const newPromptFn = js.slice(
  js.indexOf('function renderNewPromptForm'),
  js.indexOf('async function renderPromptDetail'),
);

test('a tela inicial de propostas tem os 2 botoes de entrada', () => {
  assert.match(promptsListFn, /Criar proposta/);
  assert.match(promptsListFn, /Usar proposta do banco/);
});

test('a tela inicial nao mostra nenhum campo de tema antes da escolha', () => {
  assert.doesNotMatch(promptsListFn, /id="er-title"/);
  assert.doesNotMatch(promptsListFn, /id="er-statement"/);
});

test('a tela de criar proposta e uma tela unica com tema, PDF opcional e publico', () => {
  assert.match(newPromptFn, /id="er-title"/);
  assert.match(newPromptFn, /id="er-statement"/);
  assert.match(newPromptFn, /type="file"/);
  assert.match(newPromptFn, /renderAudiencePicker/);
});

test('o seletor de publico tem os 3 blocos combinaveis', () => {
  assert.match(js, /function renderAudiencePicker/);
  const pickerFn = js.slice(
    js.indexOf('function renderAudiencePicker'),
    js.indexOf('function renderAudiencePicker') + 4000,
  );
  assert.match(pickerFn, /er-audience-series/);
  assert.match(pickerFn, /er-audience-classes/);
  assert.match(pickerFn, /er-audience-students-search/);
});

test('o submit da criacao chama criar prompt, upload opcional, e atribuicao combinada', () => {
  assert.match(newPromptFn, /\/api\/v1\/catalog\/essay-prompts/);
  assert.match(newPromptFn, /materials\/upload/);
  assert.match(newPromptFn, /assignments\/combined/);
});
