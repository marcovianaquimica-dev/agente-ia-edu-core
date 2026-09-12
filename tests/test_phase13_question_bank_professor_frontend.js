/* PHASE 13 - Professor Question Bank frontend (static source assertions).
 * Follows the project convention (node:test + fs.readFileSync + regex), like
 * tests/test_phase8c_question_modification_frontend.js.
 */
const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const BASE = 'src/agente_ia_edu/web/';
const html = fs.readFileSync(BASE + 'question-bank.html', 'utf8');
const js = fs.readFileSync(BASE + 'question-bank.js', 'utf8');
const css = fs.readFileSync(BASE + 'question-bank.css', 'utf8');

test('reuses the existing design system and asset conventions', () => {
  assert.match(html, /<link rel="stylesheet" href="styles\.css">/);
  assert.match(html, /<link rel="stylesheet" href="question-bank\.css">/);
  assert.match(html, /<script src="question-bank\.js"><\/script>/);
  assert.match(html, /class="btn btn-primary"/);
  assert.match(html, /class="btn btn-secondary"/);
  assert.match(html, /class="[^"]*\bcard\b/);
});

test('is AI-agnostic - no provider or LLM references anywhere', () => {
  for (const blob of [html, js, css]) {
    assert.doesNotMatch(blob, /openai|anthropic|gemini|OpenAIProvider|AsyncOpenAI/i);
    assert.doesNotMatch(blob, /classification_consensus|classification_prompts|build_text_provider/);
  }
});

test('talks only to the reused PHASE 12 Question Bank endpoints', () => {
  assert.match(js, /const API = '\/api\/v1\/question-bank';/);
  assert.match(js, /\$\{API\}\/questions\?\$\{buildQuery\(\)\}/);
  assert.match(js, /\$\{API\}\/questions\/\$\{questionId\}/);
  assert.match(js, /\$\{API\}\/selections\/preview/);
  // no other API namespaces
  const apiCalls = js.match(/fetch\(`?\/api\/[^`'"\s)]+/g) || [];
  for (const call of apiCalls) assert.ok(/\/api\/v1\/question-bank/.test(call), `unexpected API call: ${call}`);
});

test('reuses the established Bearer auth pattern, not a parallel one', () => {
  assert.match(js, /'Authorization': `Bearer \$\{state\.teacherId\}`/);
  assert.doesNotMatch(js, /localStorage\.setItem\(['"]token/);
});

test('search offers number / text / content and the required primary filters', () => {
  assert.match(html, /Busca \(n[uú]mero, texto ou conte[uú]do\)/);
  for (const label of ['Ano', 'Dia', 'Área', 'Disciplina', 'Conteúdo', 'Subconteúdo', 'Status', 'Dificuldade', 'Origem']) {
    assert.match(html, new RegExp(`<span>${label}</span>`));
  }
  // number vs code vs text routing
  assert.match(js, /\/\^\\d\{1,3\}\$\/\.test\(term\)/);
  assert.match(js, /p\.set\('official_number'/);
  assert.match(js, /p\.set\('content'/);
});

test('advanced filters contain classification_mode / provisional / visual / protected', () => {
  assert.match(html, /Filtros avançados/);
  assert.match(html, /id="qb-filter-mode"/);
  assert.match(html, /id="qb-filter-provisional"/);
  assert.match(html, /id="qb-filter-visual"/);
  assert.match(html, /id="qb-filter-protected"/);
  assert.match(js, /p\.set\('provisional_only', 'true'\)/);
  assert.match(js, /p\.set\('visual_dependency', 'true'\)/);
  assert.match(js, /p\.set\('protected_only', 'true'\)/);
  assert.match(js, /p\.set\('classification_mode'/);
});

test('list uses server-side pagination and never downloads the whole bank', () => {
  assert.match(js, /p\.set\('page', String\(state\.page\)\)/);
  assert.match(js, /p\.set\('page_size', String\(state\.pageSize\)\)/);
  assert.match(js, /pageSize:\s*20/);
  assert.match(html, /id="qb-page-prev"/);
  assert.match(html, /id="qb-page-next"/);
  assert.match(js, /\$\('qb-page-prev'\)\.addEventListener\('click'/);
  assert.match(js, /\$\('qb-page-next'\)\.addEventListener\('click'/);
  // no "load all" / giant page_size
  assert.doesNotMatch(js, /page_size=1000|page_size=10000|pageSize:\s*(?:500|1000|10000)/);
});

test('list rows show the required fields and provisional / visual indicators', () => {
  assert.match(js, /\$\{esc\(q\.year\)\} — Q\$\{esc\(q\.official_number\)\}/);
  assert.match(js, /Dia \$\{esc\(q\.day\)\}/);
  assert.match(js, /esc\(q\.enem_area/);
  assert.match(js, /q\.recommended_difficulty/);
  assert.match(js, /q\.has_visual_dependency/);
  assert.match(js, /Provisória · fechamento forçado/);
  assert.match(js, /Provisória · em revisão/);
  assert.match(js, /Não classificada/);
});

test('filters are preserved across pages (page change reloads with same state)', () => {
  assert.match(js, /qb-page-next.*addEventListener\('click', \(\) => \{[\s\S]*state\.page\+\+;[\s\S]*loadList\(\);/);
  assert.match(js, /qb-page-prev.*addEventListener\('click', \(\) => \{[\s\S]*state\.page--;[\s\S]*loadList\(\);/);
  // buildQuery always re-reads state.filters, so page nav keeps them
  assert.match(js, /function buildQuery\(\)/);
  assert.match(js, /const f = state\.filters;/);
});

test('preview loads only the selected question and preserves official option order', () => {
  assert.match(js, /async function openPreview\(questionId\)/);
  assert.match(js, /\$\{API\}\/questions\/\$\{questionId\}/);
  assert.match(js, /\(q\.options \|\| \[\]\)\.map/);
  assert.match(js, /q\.statement \|\| q\.canonical_text/);
  // dialog semantics reused
  assert.match(html, /role="dialog"/);
  assert.match(html, /aria-modal="true"/);
});

test('the Question Bank preview never exposes the answer key or internal identifiers', () => {
  // scope to the BANK preview (renderPreview). The authorized list-generator
  // preview (PHASE 14) may show the answer key - that is a separate, gated context.
  const bankPreview = js.match(/function renderPreview\(q\)[\s\S]*?\n  \}\n/);
  assert.ok(bankPreview, 'renderPreview not found');
  assert.doesNotMatch(bankPreview[0], /is_valid_option|answer_key|resolved_option|official_answer_label/);
  assert.doesNotMatch(bankPreview[0], /gabarito|resposta correta|alternativa correta/i);
  for (const blob of [html, js]) {
    assert.doesNotMatch(blob, /is_valid_option|resolved_option|official_answer_label/);
    assert.doesNotMatch(blob, /ProviderRouter|AssessmentItem|PedagogicalClassification\b/);
  }
});

test('classification display shows Discipline -> Area -> Content -> Subcontent + meta', () => {
  assert.match(js, /\['Disciplina', c\.discipline_code\]/);
  assert.match(js, /\['Área', c\.area_code\]/);
  assert.match(js, /\['Conteúdo', c\.content_code\]/);
  assert.match(js, /\['Subconteúdo', c\.subcontent_code\]/);
  assert.match(js, /Confiança: \$\{esc\(c\.confidence\)\}/);
  assert.match(js, /Modo: \$\{esc\(c\.classification_mode\)\}/);
  assert.match(js, /Motivo de revisão: \$\{esc\(c\.review_reason\)\}/);
  assert.match(js, /qb-provisional-banner/);
  assert.match(js, /Classificação provisória/);
});

test('visual dependency messaging matches the required copy and never fabricates assets', () => {
  assert.match(js, /Esta questão depende de material visual\./);
  assert.match(js, /Material visual não disponível\./);
  assert.doesNotMatch(js, /placeholder-image|reconstruct|inferFigure|generateImage/i);
});

test('selection is client-side only, counter is persistent, and clearing works', () => {
  assert.match(html, /id="qb-selection-count"/);
  assert.match(js, /Questões selecionadas: \$\{n\}/);
  assert.match(js, /function toggleSelect\(/);
  assert.match(js, /function clearSelection\(\)/);
  assert.match(js, /state\.selection\.splice/);
  // no persistence call for the selection itself
  assert.doesNotMatch(js, /selections`?, \{ method: 'POST'[\s\S]*persist/i);
  assert.doesNotMatch(js, /POST.*\/selections(?!\/preview)/);
});

test('selection review supports drag-and-drop AND move up/down, ordering == QuestionSelection', () => {
  assert.match(js, /function renderSelection\(\)/);
  assert.match(js, /draggable="true"/);
  assert.match(js, /addEventListener\('dragstart'/);
  assert.match(js, /addEventListener\('drop'/);
  assert.match(js, /function moveSelection\(index, delta\)/);
  assert.match(js, /aria-label="Mover para cima"/);
  assert.match(js, /aria-label="Mover para baixo"/);
  // finalize sends the selection order verbatim
  assert.match(js, /question_version_ids: state\.selection\.map\(\(s\) => s\.question_version_id\)/);
});

test('remove is implemented; replace only returns to the bank with context preserved', () => {
  assert.match(js, /function removeSelection\(index\)/);
  assert.match(js, /Remover questão/);           // dynamic control in the selection list
  assert.match(html, /Substituir questão/);       // static button in the preview dialog
  assert.match(js, /Sua seleção e filtros foram preservados/);
  // replace must NOT auto-pick or call AI
  assert.doesNotMatch(js, /autoReplace|suggestReplacement|recommendQuestion/i);
});

test('workflow state (search/filters/page/selection) survives view navigation', () => {
  const m = js.match(/function switchView\(view\) \{([\s\S]*?)\n  \}\n/);
  assert.ok(m, 'switchView function not found');
  const body = m[1];
  // switching views only toggles visibility + re-renders; it resets nothing
  assert.doesNotMatch(body, /state\.selection\s*=\s*\[\]/);
  assert.doesNotMatch(body, /state\.page\s*=\s*1/);
  assert.doesNotMatch(body, /state\.filters\s*=\s*\{/);
  assert.doesNotMatch(body, /state\.search\s*=\s*''/);
  assert.match(body, /renderSelection\(\)/);
  assert.match(body, /renderList\(\)/);
  // selection object is a single long-lived array, never reassigned on nav
  assert.match(js, /selection: \[\],/);
});

test('text search is debounced', () => {
  assert.match(js, /function debouncedReload\(\)/);
  assert.match(js, /setTimeout\([\s\S]*loadList\(\);\s*\}, 300\)/);
  assert.match(js, /qb-search-text'\)\.addEventListener\('input'/);
});
