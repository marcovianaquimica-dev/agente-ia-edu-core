/* PHASE 14 - List Generator wizard frontend (static source assertions).
 * node:test + fs.readFileSync + regex, same convention as PHASE 13.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const BASE = 'src/agente_ia_edu/web/';
const html = fs.readFileSync(BASE + 'question-bank.html', 'utf8');
const js = fs.readFileSync(BASE + 'question-bank.js', 'utf8');
const css = fs.readFileSync(BASE + 'question-bank.css', 'utf8');

test('builds on the PHASE 13 page - no new framework, same design system', () => {
  assert.match(html, /<link rel="stylesheet" href="styles\.css">/);
  assert.match(html, /<script src="question-bank\.js"><\/script>/);
  assert.doesNotMatch(html, /react|vue|angular|svelte|import .* from ['"]https?:/i);
});

test('is AI-agnostic - no provider / LLM / consensus / prompt references', () => {
  for (const blob of [html, js, css]) {
    assert.doesNotMatch(blob, /openai|anthropic|gemini|OpenAIProvider|AsyncOpenAI/i);
    assert.doesNotMatch(blob, /classification_consensus|classification_prompts|build_text_provider/);
  }
});

test('wizard has the required five ordered steps', () => {
  for (const [n, label] of [[1, 'Configurar Lista'], [2, 'Revisar Questões'],
    [3, 'Configurar Gabarito'], [4, 'Pré-visualizar'], [5, 'Finalizar']]) {
    assert.match(html, new RegExp(`${n}\\. ${label}`));
  }
  assert.match(html, /data-step="config"[\s\S]*data-step="review"[\s\S]*data-step="answerkey"[\s\S]*data-step="preview"[\s\S]*data-step="finalize"/);
});

test('list configuration screen: title, optional instructions, activity mode placeholder', () => {
  assert.match(html, /id="qb-list-title"/);
  assert.match(html, /Título da lista/);
  assert.match(html, /id="qb-list-instructions"/);
  assert.match(html, /Instruções \(opcional\)/);
  assert.match(html, /<select id="qb-list-mode"><option value="EXERCISE_LIST">Lista de exercícios<\/option><\/select>/);
  assert.match(html, /Simulado, Prova, Tarefa, Diagnóstico e Prática individual serão adicionados em fases futuras/);
});

test('review step reuses the QuestionSelection with reorder / remove / back-to-bank / add', () => {
  assert.match(html, /id="qb-review-list"/);
  assert.match(js, /function renderReviewList\(\)/);
  assert.match(js, /qb-rev-up|qb-rev-down|qb-rev-remove/);
  assert.match(js, /moveSelection\(Number\(up\.dataset\.index\), -1\)/);
  assert.match(js, /removeSelection\(Number\(rm\.dataset\.index\)\)/);
  assert.match(html, /Voltar ao Banco \(adicionar questão\)/);
  assert.match(js, /Sua lista e a ordem foram preservadas/);
  // review reorder is also drag-and-drop
  assert.match(js, /reviewDnD/);
  assert.match(js, /state\.selection\.splice\(from, 1\)/);
});

test('answer-key step offers exactly the three presentation modes', () => {
  for (const v of ['NONE', 'KEY_AT_END', 'KEY_AND_RESOLUTION_AT_END']) {
    assert.match(html, new RegExp(`name="qb-ak" value="${v}"`));
  }
  assert.match(html, /Sem gabarito \/ resolução/);
  assert.match(html, /Gabarito ao final/);
  assert.match(html, /Gabarito \+ resolução ao final/);
});

test('resolution style (resumo / passos) only shows for mode 3', () => {
  assert.match(html, /id="qb-resolution-style" hidden/);
  assert.match(html, /name="qb-res" value="SUMMARY"/);
  assert.match(html, /name="qb-res" value="STEP_BY_STEP"/);
  assert.match(js, /\$\('qb-resolution-style'\)\.hidden = e\.target\.value !== 'KEY_AND_RESOLUTION_AT_END'/);
});

test('calls only the reused/new question-bank endpoints; no answer key in the bank preview', () => {
  assert.match(js, /\$\{API\}\/lists\/config-options/);
  assert.match(js, /\$\{API\}\/lists\/generate/);
  const apiCalls = js.match(/fetch\(`?\$\{API\}[^`)]+|fetch\(`?\/api\/[^`'"\s)]+/g) || [];
  for (const call of apiCalls) {
    assert.ok(/\$\{API\}|\/api\/v1\/question-bank/.test(call), `unexpected API call: ${call}`);
  }
  // answer key must never be rendered by the plain bank preview (renderPreview)
  const bankPreview = js.match(/function renderPreview\(q\)[\s\S]*?\n  \}\n/);
  assert.ok(bankPreview);
  assert.doesNotMatch(bankPreview[0], /answer_key|correct_option|is_valid_option|gabarito/i);
});

test('generateList sends the selection order verbatim + the chosen configuration', () => {
  assert.match(js, /async function generateList\(\)/);
  assert.match(js, /question_version_ids: state\.selection\.map\(\(s\) => s\.question_version_id\)/);
  assert.match(js, /answer_key_presentation: cfg\.answerKey/);
  assert.match(js, /resolution_style: cfg\.answerKey === 'KEY_AND_RESOLUTION_AT_END' \? cfg\.resolutionStyle : null/);
  assert.match(js, /method: 'POST'/);
});

test('preview renders title, instructions, numbering, order, statement, A-E options, key config', () => {
  assert.match(js, /function renderListPreview\(\)/);
  assert.match(js, /esc\(cfg\.title\)/);
  assert.match(js, /cfg\.instructions \? `<p class="qb-preview-instructions">/);
  assert.match(js, /\$\{it\.position\}\. \$\{esc\(it\.year/);
  assert.match(js, /function optionsBlock\(opts\)/);
  assert.match(js, /d\.answer_key_included/);
  assert.match(js, /Gabarito<\/h4>/);
});

test('finalization screen lists title / count / order / key / resolution and shows nothing for time', () => {
  assert.match(js, /function renderFinalize\(\)/);
  assert.match(js, /Total de questões/);
  assert.match(js, /d\.items\.map\(\(i\) => `Q\$\{i\.official_number\}`\)\.join\(', '\)/);
  assert.match(js, /Tempo estimado<\/span><strong>—<\/strong>/);  // no invented estimate
  assert.match(html, /id="qb-finalize-confirm"/);
  assert.match(html, /Finalizar lista/);
});

test('edit / back-to-selection actions exist and preserve state (no resets in switchView)', () => {
  assert.match(html, /id="qb-preview-edit"/);
  assert.match(html, /id="qb-finalize-edit"/);
  assert.match(js, /'qb-preview-edit'\)\.addEventListener\('click', \(\) => setListStep\('config'\)\)/);
  assert.match(js, /'qb-finalize-back-selection'\)\.addEventListener\('click', \(\) => switchView\('selection'\)\)/);
  const sw = js.match(/function switchView\(view\) \{([\s\S]*?)\n  \}\n/)[1];
  assert.doesNotMatch(sw, /state\.selection\s*=\s*\[\]/);
  assert.doesNotMatch(sw, /state\.list\s*=\s*\{/);
});

test('deterministic ordering: the wizard never re-sorts the selection', () => {
  // review + generate both consume state.selection in its current array order
  assert.doesNotMatch(js, /state\.selection\.sort\(/);
  assert.match(js, /state\.selection\.map\(\(row, i\)/);  // renderReviewList numbers 1..N in array order
});

test('the PHASE 14 wizard preview generates no PDF/DOCX itself (export is PHASE 15)', () => {
  // no client-side PDF/DOCX library, no print
  for (const blob of [html, js]) {
    assert.doesNotMatch(blob, /jspdf|pdfmake|html2pdf|docx-templates|window\.print\(/i);
  }
  // the wizard preview renderer builds only HTML - it does not embed a file
  const previewRenderer = js.match(/function renderListPreview\(\)[\s\S]*?\n  \}\n/);
  assert.ok(previewRenderer);
  assert.doesNotMatch(previewRenderer[0], /\.pdf|\.docx|application\/pdf/i);
});
