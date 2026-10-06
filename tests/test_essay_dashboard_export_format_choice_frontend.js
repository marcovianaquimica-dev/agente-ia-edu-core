// Contrato de frontend da escolha de formato (PDF ou XLSX) nos botoes de
// exportar do Dashboard de redacao (essay-review.js::wireDashboardExportGroup)
// contra os dois endpoints reais (essay_prompts.py export.xlsx/export.pdf).
//
// essay-review.js roda dentro de um IIFE sem exportar essas funcoes - como
// os outros *_frontend.js desta suite, as assercoes rodam contra o
// texto-fonte da propria funcao.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const js = fs.readFileSync('src/agente_ia_edu/web/essay-review.js', 'utf8');
const group = js.slice(
  js.indexOf('async function downloadDashboardExport'),
  js.indexOf('function wireDashboardExportButtons'),
);

test('o primeiro clique mostra PDF e XLSX, nunca baixa direto', () => {
  assert.match(group, /data-export-format="pdf"/);
  assert.match(group, /data-export-format="xlsx"/);
});

test('cada formato baixa do endpoint certo (export.${format})', () => {
  assert.match(group, /\/dashboard\/export\.\$\{format\}/);
});

test('cancelar volta pro botao original sem baixar nada', () => {
  assert.match(group, /data-export-cancel/);
});

test('reconectar um grupo nunca re-registra os outros dois (sem duplicar ouvinte de clique)', () => {
  // wireDashboardExportGroup so chama a si mesma (reconectando so o proprio
  // grupo), nunca wireDashboardExportButtons (que iteraria os 3 juntos de
  // novo e duplicaria o listener dos botoes que nao foram trocados agora).
  assert.match(group, /wireDashboardExportGroup\(\{ resultsEl, promptId, baseParams, id, reportType, label, originalText \}\)/);
  assert.doesNotMatch(group, /wireDashboardExportButtons\(/);
});
