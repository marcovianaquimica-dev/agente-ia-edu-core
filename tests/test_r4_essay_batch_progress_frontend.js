// Contrato de frontend da aba "Enviar em lote" de essay-review.js contra o
// contador real de progresso (processed_count) e a lista needs_review_pages
// ja filtrada que EssayBatchService.get_batch_status devolve depois do
// Problema 2 do fix-round-1-brief.md.
//
// essay-review.js roda dentro de um IIFE sem module.exports, entao - como os
// outros *_frontend.js desta suite - as assercoes rodam contra o texto-fonte
// (renderBatchProgress).

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const js = fs.readFileSync('src/agente_ia_edu/web/essay-review.js', 'utf8');
const progress = js.slice(
  js.indexOf('function renderBatchProgress'),
  js.indexOf('function renderNewPromptForm'),
);

test('o progresso do lote usa processed_count, nao matched_count + needs_review_count', () => {
  // Problema 2(a): matched_count + needs_review_count so cobre paginas ja
  // processadas - processed_count e o contador dedicado que o backend
  // agora expoe (EssayBatchStatusResponse.processed_count), e e ele que a
  // tela precisa ler, nao uma soma que dependia de um invariante implicito
  // do backend antigo (onde toda pagina contava como uma coisa ou outra,
  // mesmo antes do OCR rodar).
  assert.match(progress, /data\.processed_count/);
  assert.doesNotMatch(
    progress,
    /const done = data\.matched_count \+ data\.needs_review_count/,
  );
});

test('a fila de resolucao nunca renderiza uma pagina ja casada (matched_student_id preenchido)', () => {
  // Problema 2(b): defesa em profundidade no frontend, "no minimo" (brief),
  // alem do filtro que o backend ja aplica em needs_review_pages - uma
  // pagina com matched_student_id preenchido nunca deve ganhar seletor de
  // aluno nem botao Confirmar habilitados.
  assert.match(progress, /needs_review_pages\s*\.filter\(\s*\(?page\)?\s*=>\s*!page\.matched_student_id\s*\)/);
});
