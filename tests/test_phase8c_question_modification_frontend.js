const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const html = fs.readFileSync('src/agente_ia_edu/web/teacher.html', 'utf8');
const js = fs.readFileSync('src/agente_ia_edu/web/teacher.js', 'utf8');
const css = fs.readFileSync('src/agente_ia_edu/web/teacher.css', 'utf8');
const modificationHtml = html.slice(html.indexOf('id="question-modification-modal"'));

test('renders accessible modification controls without exposing technical data', () => {
  assert.match(modificationHtml, /Como você quer modificar esta questão/);
  assert.match(modificationHtml, /role="dialog"/);
  assert.match(modificationHtml, /aria-labelledby="question-modification-title"/);
  assert.match(modificationHtml, /Descreva como você quer modificar a questão/);
  assert.doesNotMatch(modificationHtml, /Selecionar todas|Selecionar página/);
  assert.doesNotMatch(modificationHtml, /QuestionVersion|AssessmentItem|ProviderRouter/);
});

test('offers all quick modification options and a custom instruction path', () => {
  for (const label of ['Deixar mais fácil', 'Deixar mais difícil', 'Reduzir o texto', 'Simplificar a linguagem', 'Aumentar a contextualização', 'Adaptar ao estilo ENEM', 'Melhorar as alternativas', 'Outro']) {
    assert.match(html, new RegExp(label));
  }
  assert.match(js, /state\.modification\.type === 'CUSTOM'/);
  assert.match(js, /custom-modification-instruction/);
});

test('uses the established proposal, accept, and cancel endpoints', () => {
  assert.match(js, /\/modification-proposals`, \{ method: 'POST'/);
  assert.match(js, /\/modification-proposals\/\$\{state\.modification\.proposal\.id\}\/accept/);
  assert.match(js, /\/modification-proposals\/\$\{state\.modification\.proposal\.id\}\/cancel/);
  assert.match(js, /question_version_id/);
});

test('guards duplicate generation and acceptance while showing recoverable states', () => {
  assert.match(js, /state\.modification\.busy \|\| !state\.modification\.type/);
  assert.match(js, /state\.modification\.busy = true/);
  assert.match(html, /Preparando uma nova versão/);
  assert.match(js, /Não foi possível gerar a modificação\. Tente novamente/);
  assert.match(js, /Não foi possível aplicar esta versão/);
  assert.match(js, /btn-accept-proposal.*disabled = false/);
});

test('compares proposals responsively and keeps the modal hidden until requested', () => {
  assert.match(html, /Versão original/);
  assert.match(html, /Versão modificada/);
  assert.match(css, /\.modal-overlay\[hidden\] \{ display: none; \}/);
  assert.match(css, /\.comparison-grid \{ display: grid; grid-template-columns: repeat\(2/);
  assert.match(css, /\.comparison-grid, \.modification-options \{ grid-template-columns: 1fr; \}/);
  assert.match(css, /\.main-content \{ min-width: 0; overflow-x: hidden; \}/);
});