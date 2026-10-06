// Contrato de frontend do botao "Aprovar selecionadas" na Fila de Revisao
// (essay-review.js::renderReviewQueue) contra o endpoint real
// POST /api/v1/teacher/essay-corrections/bulk-approve (essay_corrections.py).
//
// essay-review.js roda dentro de um IIFE sem exportar renderReviewQueue
// (so `init` sai do modulo) - como os outros *_frontend.js desta suite, as
// assercoes rodam contra o texto-fonte da propria funcao.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const js = fs.readFileSync('src/agente_ia_edu/web/essay-review.js', 'utf8');
const queue = js.slice(
  js.indexOf('async function renderReviewQueue'),
  js.indexOf('async function renderEvolutionTab'),
);

test('selecao em lote so aparece quando o status e PENDING_REVIEW', () => {
  // Nunca faz sentido pra APPROVED/REJECTED/NEEDS_REVIEW - aprovar e a unica
  // acao em massa, e so corrections pendentes podem ser aprovadas.
  assert.match(queue, /currentStatus === 'PENDING_REVIEW'/);
});

test('o botao chama o endpoint real de bulk-approve com os ids selecionados', () => {
  assert.match(queue, /\/api\/v1\/teacher\/essay-corrections\/bulk-approve/);
  assert.match(queue, /essay_correction_ids:\s*ids/);
});

test('selecionar todas marca cada checkbox individual, e desmarcar uma desmarca o "selecionar todas"', () => {
  assert.match(queue, /selectAll\.addEventListener\('change'/);
  assert.match(queue, /cb\.checked = selectAll\.checked/);
  assert.match(queue, /if \(!cb\.checked\) selectAll\.checked = false/);
});

test('o botao fica desabilitado quando nada esta selecionado', () => {
  assert.match(queue, /approveBtn\.disabled = selected === 0/);
});

test('a fila recarrega depois de aprovar em lote, pra sumir as que foram aprovadas', () => {
  assert.match(queue, /await renderReviewQueue\(currentStatus\)/);
});
