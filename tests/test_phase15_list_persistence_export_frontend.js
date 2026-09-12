/* PHASE 15 - list persistence / Minhas Listas / export frontend (static assertions).
 * node:test + fs.readFileSync + regex, same convention as PHASE 13/14.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const BASE = 'src/agente_ia_edu/web/';
const html = fs.readFileSync(BASE + 'question-bank.html', 'utf8');
const js = fs.readFileSync(BASE + 'question-bank.js', 'utf8');
const css = fs.readFileSync(BASE + 'question-bank.css', 'utf8');

test('no new framework; AI-agnostic', () => {
  assert.doesNotMatch(html, /react|vue|angular|svelte/i);
  for (const blob of [html, js, css]) {
    assert.doesNotMatch(blob, /openai|anthropic|gemini|OpenAIProvider|AsyncOpenAI/i);
    assert.doesNotMatch(blob, /classification_consensus|classification_prompts|build_text_provider/);
  }
});

test('talks only to the /lists persistence + export endpoints under question-bank', () => {
  assert.match(js, /\$\{API\}\/lists`?, \{ method: 'POST'/);
  assert.match(js, /\$\{API\}\/lists\/\$\{state\.list\.storedId\}`?, \{\s*\n?\s*method: 'PATCH'/);
  assert.match(js, /\$\{API\}\/lists\/\$\{summary\.id\}\/finalize/);
  assert.match(js, /\$\{API\}\/lists\?\$\{p\.toString\(\)\}/);
  assert.match(js, /\$\{API\}\/lists\/\$\{listId\}/);
  const calls = js.match(/fetch\(`?\$\{API\}[^`)]+/g) || [];
  for (const c of calls) assert.ok(/\$\{API\}\/(questions|selections|lists)/.test(c), `unexpected: ${c}`);
});

test('reuses Bearer auth; no parallel permission system', () => {
  assert.match(js, /'Authorization': `Bearer \$\{state\.teacherId\}`/);
  assert.doesNotMatch(js, /localStorage\.setItem\(['"]token/);
});

test('finalize step persists then finalizes (draft -> published)', () => {
  assert.match(js, /async function persistCurrentList\(\{\s*finalize = false\s*\}\s*=\s*\{\}\)\s*\{/);
  assert.match(js, /persistCurrentList\(\{ finalize: false \}\)/);
  assert.match(js, /persistCurrentList\(\{ finalize: true \}\)/);
  // finalize actually calls the finalize endpoint after the save
  assert.match(js, /if \(finalize\) \{[\s\S]*\/finalize`, \{ method: 'POST'/);
  assert.match(html, /id="qb-finalize-save-draft"/);
  assert.match(html, /id="qb-finalize-confirm"/);
});

test('Minhas Listas section: search, status filter, sort, date, question count', () => {
  assert.match(html, /Minhas Listas/);
  assert.match(html, /id="qb-mylists-q"/);
  assert.match(html, /id="qb-mylists-status"/);
  assert.match(html, /id="qb-mylists-order"/);
  assert.match(html, /id="qb-mylists-dir"/);
  assert.match(js, /function loadMyLists\(\)/);
  assert.match(js, /l\.question_count\} questões/);
  assert.match(js, /\(l\.created_at \|\| ''\)\.slice\(0, 10\)/);
  assert.match(js, /l\.status === 'published' \? 'Finalizada' : 'Rascunho'/);
});

test('per-list actions: Visualizar / Editar(draft) / Finalizar / Exportar PDF / Exportar DOCX', () => {
  assert.match(js, />Visualizar<\/button>/);
  assert.match(js, /l\.status !== 'published' \? `<button class="btn btn-secondary qb-ml-edit"/);
  assert.match(js, /qb-ml-finalize/);
  assert.match(js, /href="\$\{API\}\/lists\/\$\{esc\(l\.id\)\}\/export\.pdf"/);
  assert.match(js, /href="\$\{API\}\/lists\/\$\{esc\(l\.id\)\}\/export\.docx"/);
});

test('opening a stored list preserves the exact persisted order', () => {
  assert.match(js, /async function openStoredList\(listId\)/);
  assert.match(js, /state\.selection = d\.items\.map\(\(it\) => \(\{/);
  assert.match(js, /question_version_id: it\.question_version_id/);
  // rehydrates from d.items in server order - no client sort
  assert.doesNotMatch(js, /d\.items\.sort\(|state\.selection\.sort\(/);
});

test('answer key never leaks into the plain Question Bank preview', () => {
  const bankPreview = js.match(/function renderPreview\(q\)[\s\S]*?\n  \}\n/);
  assert.ok(bankPreview);
  assert.doesNotMatch(bankPreview[0], /answer_key|correct_option|is_valid_option|gabarito/i);
});

test('switching views does not reset the workflow or the stored list handle', () => {
  const sw = js.match(/function switchView\(view\) \{([\s\S]*?)\n  \}\n/)[1];
  assert.doesNotMatch(sw, /state\.selection\s*=\s*\[\]/);
  assert.doesNotMatch(sw, /state\.list\s*=\s*\{/);
  assert.doesNotMatch(sw, /state\.list\.storedId\s*=\s*null/);
  assert.match(sw, /view === 'mylists'/);
});

test('no client-side PDF/DOCX generation - export is a plain authorized GET link', () => {
  assert.doesNotMatch(js, /jspdf|pdfmake|html2pdf|docx-templates|new Blob\(\[[^\]]*pdf/i);
  // export is an <a href> to the backend endpoint, not a generated file
  assert.match(js, /<a class="btn btn-secondary" href="\$\{API\}\/lists\/[^"]+\/export\.(pdf|docx)"/);
});
