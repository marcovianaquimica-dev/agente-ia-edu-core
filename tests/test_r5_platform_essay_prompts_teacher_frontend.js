// Contrato de frontend do selo "Plataforma" na tela de propostas do
// professor (src/agente_ia_edu/web/essay-review.js) contra os campos
// is_platform / platform_prompt_id / materialized que
// api/routes/essay_prompts.py passou a devolver.
//
// essay-review.js roda dentro de um IIFE sem module.exports, entao - como os
// outros *_frontend.js desta suite - as assercoes rodam contra o texto-fonte.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const js = fs.readFileSync('src/agente_ia_edu/web/essay-review.js', 'utf8');
const css = fs.readFileSync('src/agente_ia_edu/web/teacher.css', 'utf8');

test('a lista estampa o selo Plataforma nos itens com is_platform', () => {
  const list = js.slice(
    js.indexOf('async function renderPromptsList'),
    js.indexOf('async function renderTrashTab'),
  );
  assert.match(list, /p\.is_platform/);
  assert.match(list, /er-platform-badge/);
  assert.match(list, /Plataforma/);
});

test('o botao de lixeira nunca aparece numa proposta da plataforma', () => {
  // Decisao 3 da spec: o professor nunca edita nem exclui uma proposta da
  // plataforma - so usa.
  const list = js.slice(
    js.indexOf('async function renderPromptsList'),
    js.indexOf('async function renderTrashTab'),
  );
  assert.match(list, /p\.is_platform\s*\?\s*''\s*:\s*`<button[^`]*data-delete-prompt/);
});

test('o detalhe esconde o formulario de material quando a proposta e da plataforma', () => {
  const detail = js.slice(js.indexOf('async function renderPromptDetail'));
  assert.match(detail, /const isPlatform = !!detail\.is_platform;/);
  assert.match(detail, /#er-material-form'\)\.hidden = true/);
});

test('o detalhe esconde a folha de resposta enquanto a copia nao existe', () => {
  // answer-sheet.pdf exige um EssayPrompt real na escola; numa
  // pre-visualizacao (materialized === false) ele daria 403.
  const detail = js.slice(js.indexOf('async function renderPromptDetail'));
  assert.match(detail, /detail\.materialized === false/);
  assert.match(detail, /#er-sheet-actions/);
  assert.match(js, /id="er-sheet-actions"/);
});

test('o formulario de atribuicao continua postando com o id recebido na lista', () => {
  // E esse id (que pode ser o de uma platform_essay_prompts) que o backend
  // resolve e materializa - o frontend nao trata isso.
  assert.match(js, /\/api\/v1\/catalog\/essay-prompts\/\$\{promptId\}\/assignments\/bulk/);
});

test('teacher.css tem a regra do selo', () => {
  assert.match(css, /\.er-platform-badge\s*\{/);
});
