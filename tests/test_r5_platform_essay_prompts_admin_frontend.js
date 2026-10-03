// Contrato de frontend da secao "Propostas de Redacao da Plataforma"
// (src/agente_ia_edu/web/admin.js + admin.html) contra
// src/agente_ia_edu/api/routes/admin_essay_prompts.py.
//
// admin.js nao tem module.exports (roda inteiro dentro de um listener de
// DOMContentLoaded), entao - como os outros *_frontend.js desta suite - as
// assercoes rodam contra o texto-fonte, nao contra o codigo executado.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const js = fs.readFileSync('src/agente_ia_edu/web/admin.js', 'utf8');
const html = fs.readFileSync('src/agente_ia_edu/web/admin.html', 'utf8');

test('admin.html tem os elementos que admin.js procura por id', () => {
  for (const id of [
    'btn-new-platform-prompt',
    'platform-prompt-form',
    'platform-prompt-title',
    'platform-prompt-statement',
    'platform-prompt-cancel-btn',
    'platform-prompt-form-msg',
    'platform-prompts-list',
  ]) {
    assert.ok(html.includes(`id="${id}"`), `admin.html nao tem id="${id}"`);
  }
});

test('as chamadas usam os caminhos e os nomes de campo de admin_essay_prompts.py', () => {
  assert.match(js, /fetch\(`\$\{API\}\/platform-essay-prompts`, \{ headers: authHeaders\(\) \}\)/);
  assert.match(js, /fetch\(`\$\{API\}\/platform-essay-prompts`, \{\s*method: 'POST'/);
  assert.match(js, /fetch\(`\$\{API\}\/platform-essay-prompts\/\$\{promptId\}\/archive`, \{\s*method: 'POST'/);
  // PlatformEssayPromptCreateRequest: exatamente title + statement.
  assert.match(js, /title: \$\('platform-prompt-title'\)\.value\.trim\(\)/);
  assert.match(js, /statement: \$\('platform-prompt-statement'\)\.value\.trim\(\)/);
});

test('a listagem mostra status e a contagem de escolas que ja materializaram', () => {
  assert.match(js, /materialized_school_count/);
  assert.match(js, /PLATFORM_PROMPT_STATUS_LABELS/);
  assert.match(js, /ARCHIVED: 'Arquivada'/);
});

test('o botao arquivar so aparece em proposta ACTIVE', () => {
  // Uma proposta ja arquivada nao pode ser arquivada de novo - a linha dela
  // sai sem botao em vez de oferecer uma acao que nao faz nada.
  assert.match(js, /p\.status === 'ACTIVE'\s*\?[\s\S]{0,200}data-archive-platform-prompt/);
});

test('403 na listagem vira a mesma mensagem de acesso negado das escolas', () => {
  const platformSection = js.slice(js.indexOf('async function loadPlatformPrompts'));
  assert.match(platformSection, /res\.status === 403/);
  assert.match(platformSection, /Administrador da Plataforma/);
});

test('a secao carrega no boot e recarrega quando a identidade muda', () => {
  const boot = js.slice(js.indexOf("// ---------- Identity ----------"));
  assert.match(boot, /loadPlatformPrompts\(\);/);
  // Cinco ocorrencias no arquivo: apos arquivar, apos criar, apos anexar
  // material, no handler de troca de identidade e no boot.
  assert.equal((js.match(/loadPlatformPrompts\(\);/g) || []).length, 5);
});

test('todo texto vindo da API passa por esc() antes de virar HTML', () => {
  const render = js.slice(
    js.indexOf('function renderPlatformPrompts'),
    js.indexOf('async function archivePlatformPrompt'),
  );
  assert.match(render, /esc\(p\.title\)/);
  assert.doesNotMatch(render, /\$\{p\.title\}/);
});

test('a listagem mostra a contagem de materiais anexados', () => {
  const render = js.slice(
    js.indexOf('function renderPlatformPrompts'),
    js.indexOf('async function archivePlatformPrompt'),
  );
  assert.match(render, /material_count/);
});

test('cada proposta ACTIVE tem um input de arquivo pra anexar material', () => {
  const render = js.slice(
    js.indexOf('function renderPlatformPrompts'),
    js.indexOf('async function archivePlatformPrompt'),
  );
  assert.match(render, /data-upload-material-input="\$\{esc\(p\.id\)\}"/);
  assert.match(render, /data-upload-material-btn="\$\{esc\(p\.id\)\}"/);
});

test('o upload de material manda multipart pra rota certa', () => {
  assert.match(js, /fetch\(`\$\{API\}\/platform-essay-prompts\/\$\{promptId\}\/materials\/upload`, \{\s*method: 'POST'/);
  assert.match(js, /FormData\(\)/);
  assert.match(js, /formData\.append\('position', '0'\)/);
  assert.match(js, /formData\.append\('file', /);
});
