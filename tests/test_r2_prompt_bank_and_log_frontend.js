const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const js = fs.readFileSync('src/agente_ia_edu/web/essay-review.js', 'utf8');

// A janela abaixo (4300) cobre renderPromptBankList inteira (que ganhou o
// selo "Plataforma" e o botao de lixeira condicional, reaproveitados de
// renderPromptsList - ver test_r5_platform_essay_prompts_teacher_frontend.js)
// mais o comeco de renderBankAssignScreen, que vem logo depois no arquivo.
const BANK_LIST_WINDOW = 4300;

test('existe uma tela de banco de propostas com filtro de texto', () => {
  assert.match(js, /function renderPromptBankList/);
  const fn = js.slice(js.indexOf('function renderPromptBankList'), js.indexOf('function renderPromptBankList') + BANK_LIST_WINDOW);
  assert.match(fn, /er-bank-filter/);
});

test('escolher uma proposta do banco leva ao seletor de publico e atribui', () => {
  const fn = js.slice(js.indexOf('function renderPromptBankList'), js.indexOf('function renderPromptBankList') + BANK_LIST_WINDOW);
  assert.match(fn, /renderAudiencePicker/);
  assert.match(fn, /assignments\/combined/);
});

test('a tela de detalhe de uma proposta ganhou a secao de historico de uso', () => {
  const detailFn = js.slice(js.indexOf('async function renderPromptDetail'), js.indexOf('async function renderPromptDetail') + 6000);
  assert.match(detailFn, /Histórico de atribuições/);
  assert.match(detailFn, /assignment-log/);
});
