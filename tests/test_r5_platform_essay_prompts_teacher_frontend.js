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
    js.indexOf('async function renderPromptBankList'),
    js.indexOf('async function renderBankAssignScreen'),
  );
  assert.match(list, /p\.is_platform/);
  assert.match(list, /er-platform-badge/);
  assert.match(list, /Plataforma/);
});

test('o botao de lixeira nunca aparece numa proposta da plataforma', () => {
  // Decisao 3 da spec: o professor nunca edita nem exclui uma proposta da
  // plataforma - so usa.
  const list = js.slice(
    js.indexOf('async function renderPromptBankList'),
    js.indexOf('async function renderBankAssignScreen'),
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

// Fix round 1 (revisao final I-1/M-2): GET /api/v1/catalog/essay-prompts
// devolve, na MESMA lista, propostas da plataforma ainda nao materializadas
// nesta escola - o backend so consegue devolver o platform_prompt_id como
// id nesse caso (nao existe essay_prompts.id real ainda), entao
// is_platform === true && id === platform_prompt_id e a assinatura de "essa
// proposta e so pre-visualizacao". Os selects de Dashboard e Envio em lote
// batem em rotas que exigem um EssayPrompt real (.../dashboard,
// PromptAssignment do lote) e tem que filtrar essas antes de listar.

test('existe um helper que reconhece proposta da plataforma ainda nao materializada', () => {
  assert.match(js, /function isUnmaterializedPlatformPrompt\(p\)/);
  const helper = js.slice(
    js.indexOf('function isUnmaterializedPlatformPrompt'),
    js.indexOf('function isUnmaterializedPlatformPrompt') + 400,
  );
  assert.match(helper, /p\.is_platform/);
  assert.match(helper, /p\.id\s*===\s*p\.platform_prompt_id/);
});

test('a aba Dashboard nao lista nem auto-abre proposta da plataforma ainda nao materializada', () => {
  const tab = js.slice(
    js.indexOf('async function renderDashboardTab'),
    js.indexOf('async function renderDashboardBody'),
  );
  assert.match(tab, /isUnmaterializedPlatformPrompt/);
  assert.match(tab, /dashboardPrompts\.map/);
  assert.doesNotMatch(tab, /prompts\.map/);
  assert.match(tab, /renderDashboardBody\(dashboardPrompts\[0\]\.id\)/);
});

test('a aba Envio em lote nao lista proposta da plataforma ainda nao materializada', () => {
  const tab = js.slice(
    js.indexOf('async function renderBatchTab'),
    js.indexOf('async function pollBatch'),
  );
  assert.match(tab, /isUnmaterializedPlatformPrompt/);
  assert.match(tab, /promptOptions\s*=\s*promptOptions\.filter\(/);
});
