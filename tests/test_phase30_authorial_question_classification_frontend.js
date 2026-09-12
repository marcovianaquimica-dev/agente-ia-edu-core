/**
 * PHASE 30 - Authorial Question Pedagogical Classification Engine frontend
 * (teacher portal). Static, regex-based checks - same convention as the
 * PHASE 27/29 frontend test files.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const teacherHtml = fs.readFileSync(path.join(WEB, 'teacher.html'), 'utf8');
const teacherJs = fs.readFileSync(path.join(WEB, 'teacher.js'), 'utf8');
const teacherCss = fs.readFileSync(path.join(WEB, 'teacher.css'), 'utf8');

const PC = teacherJs.slice(teacherJs.indexOf('// PHASE 30 — Classificação Pedagógica'));

// -- classificação / confidence / status ---------------------------------
test('the classification table shows discipline/content/subcontent/difficulty/confidence/status', () => {
  assert.match(teacherHtml, /id="pc-table"/);
  assert.match(teacherHtml, /<th>Disciplina<\/th>/);
  assert.match(teacherHtml, /<th>Conteúdo<\/th>/);
  assert.match(teacherHtml, /<th>Subconteúdo<\/th>/);
  assert.match(teacherHtml, /<th>Dificuldade<\/th>/);
  assert.match(teacherHtml, /<th>Confidence<\/th>/);
  assert.match(teacherHtml, /<th>Status<\/th>/);
});

test('confidence is rendered from the backend-computed value, never invented in the frontend', () => {
  assert.match(PC, /c\.classification_confidence/);
  assert.doesNotMatch(PC, /function computeConfidence/);
});

test('a NEEDS_REVIEW classification shows the exact required banner text', () => {
  assert.match(teacherHtml, /id="pc-needs-review-banner"/);
  assert.match(teacherHtml, /Esta classificação precisa de revisão\./);
});

// -- edição / dropdown curricular -----------------------------------------
test('curriculum fields are dropdowns populated from the real catalog tree - never free text', () => {
  assert.match(teacherHtml, /<select id="pc-discipline"/);
  assert.match(teacherHtml, /<select id="pc-area"/);
  assert.match(teacherHtml, /<select id="pc-content"/);
  assert.match(teacherHtml, /<select id="pc-subcontent"/);
  assert.match(PC, /\/api\/v1\/catalog\/disciplines/);
  assert.match(PC, /\/api\/v1\/catalog\/nodes\?parent_id=\$\{parentId\}/);
});

test('the discipline/area/content dropdowns cascade - selecting one reloads the next level', () => {
  assert.match(PC, /disciplineSel\.addEventListener\('change', \(\) => populateAreaDropdown/);
  assert.match(PC, /areaSel\.addEventListener\('change', \(\) => populateContentDropdown/);
  assert.match(PC, /contentSel\.addEventListener\('change', \(\) => populateSubcontentDropdown/);
});

test('manual edit sends validated dropdown codes via PATCH, not typed text', () => {
  assert.match(PC, /method: 'PATCH', headers: tmHeaders\(\), body: JSON\.stringify\(payload\)/);
  assert.match(PC, /function selectedCode/);
});

// -- dificuldade -------------------------------------------------------------
test('difficulty uses the fixed EASY/MEDIUM/HARD enum, never free text', () => {
  assert.match(teacherHtml, /<select id="pc-difficulty"/);
  assert.match(teacherHtml, /<option value="EASY">/);
  assert.match(teacherHtml, /<option value="MEDIUM">/);
  assert.match(teacherHtml, /<option value="HARD">/);
});

// -- aprovação / reclassificação -------------------------------------------
test('approve and reclassify are separate, explicit actions', () => {
  assert.match(teacherHtml, /id="pc-approve-btn"/);
  assert.match(teacherHtml, /id="pc-reclassify-btn"/);
  assert.match(PC, /classifications\/\$\{pc\.current\.classification\.id\}\/approve`/);
  assert.match(PC, /question-versions\/\$\{pc\.current\.question_version_id\}\/reclassify`/);
});

// -- histórico -----------------------------------------------------------
test('classification history is viewable, showing actor, action and reason', () => {
  assert.match(teacherHtml, /id="pc-history-btn"/);
  assert.match(PC, /\/history`, \{ headers: tmHeaders\(\) \}/);
  assert.match(PC, /e\.actor_type/);
  assert.match(PC, /e\.reason/);
});

// -- erro / loading --------------------------------------------------------
test('network and validation errors are surfaced to the user, never silently swallowed', () => {
  assert.match(teacherHtml, /id="pc-msg"/);
  assert.match(teacherHtml, /id="pc-detail-msg"/);
  assert.match(PC, /tmMsg\('pc-msg', 'Sem conexão\.'\)/);
  assert.match(PC, /tmMsg\('pc-detail-msg'/);
});

test('batch classification reports processed/classified/needs_review/error counts', () => {
  assert.match(PC, /data\.questions_processed/);
  assert.match(PC, /data\.classified/);
  assert.match(PC, /data\.needs_review/);
  assert.match(PC, /data\.errors/);
});

// -- 320px / overflow ---------------------------------------------------
test('the classification table scrolls horizontally instead of the page at narrow widths', () => {
  const pcSection = teacherHtml.slice(teacherHtml.indexOf('Classificação Pedagógica'), teacherHtml.indexOf('Classificação Pedagógica') + 800);
  assert.match(pcSection, /class="tm-table-wrap" style="overflow-x:auto;"/);
  assert.match(teacherCss, /@media \(max-width: 768px\)[\s\S]*?\.tm-detail-grid \{ grid-template-columns: 1fr; \}/);
});

// -- AI guard -----------------------------------------------------------
test('NO AI anywhere in the classification UI block', () => {
  assert.doesNotMatch(PC, /openai|gpt/i);
});
