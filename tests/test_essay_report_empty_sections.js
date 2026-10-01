// Correcao pontual pedida pelo usuario (2026-09-29), fora do plano de C2/C3:
// a secao "Revisao de dominio da norma padrao (C1)" nao deve aparecer quando
// nao ha nenhuma ocorrencia mecanica confirmada - hoje o titulo aparece
// sempre, seguido de um texto placeholder ("Nenhuma ocorrencia mecanica
// confirmada nesta redacao."), o que da a impressao de uma secao vazia em
// vez de simplesmente nao mostrar nada.

const test = require('node:test');
const assert = require('node:assert/strict');
const { renderRichReport } = require('../src/agente_ia_edu/web/essay-report.js');

function esc(value) {
  return String(value == null ? '' : value)
    .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
}

function baseCorrection(overrides) {
  return Object.assign({
    final_scores: { total: 800, per_competency: {
      C1: { points: 160 }, C2: { points: 160 }, C3: { points: 160 },
      C4: { points: 160 }, C5: { points: 160 },
    } },
    final_feedback: { strengths: ['Boa organização.'], improvements: ['Aprofunde um argumento.'], next_essay_strategy: 'Continue assim.' },
    rationales: [], annotations: [], rewrites: [], alerts: [],
    mechanical_review: [],
  }, overrides);
}

test('secao de revisao mecanica some quando mechanical_review esta vazio', () => {
  const html = renderRichReport(baseCorrection({}), { escFn: esc });
  assert.doesNotMatch(html, /Revisão de domínio da norma padrão/);
  assert.doesNotMatch(html, /Nenhuma ocorrência mecânica confirmada/);
});

test('secao de revisao mecanica aparece quando ha ocorrencias', () => {
  const html = renderRichReport(baseCorrection({
    mechanical_review: [
      { category: 'CRASE', excerpt: 'a vista disso', suggested_form: 'à vista disso', rule_explanation: 'falta crase' },
    ],
  }), { escFn: esc });
  assert.match(html, /Revisão de domínio da norma padrão/);
  assert.match(html, /CRASE/);
});
