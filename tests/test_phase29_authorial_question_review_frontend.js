/**
 * PHASE 29 - Authorial Question Review, Approval & Publication frontend
 * (teacher portal): review queue, side-by-side review screen, publication
 * step. Static, regex-based checks - same convention as
 * test_phase27_question_extraction_frontend.js.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const teacherHtml = fs.readFileSync(path.join(WEB, 'teacher.html'), 'utf8');
const teacherJs = fs.readFileSync(path.join(WEB, 'teacher.js'), 'utf8');
const teacherCss = fs.readFileSync(path.join(WEB, 'teacher.css'), 'utf8');

const RV = teacherJs.slice(teacherJs.indexOf('// PHASE 29 — Fila de Revisão'));

// -- 1/2/3/4/5. Fila de revisão -----------------------------------------
test('the review queue table and progress bar exist', () => {
  assert.match(teacherHtml, /id="rq-table"/);
  assert.match(teacherHtml, /id="rq-table-body"/);
  assert.match(teacherHtml, /id="rq-empty"/);
  assert.match(teacherHtml, /id="rq-progress-fill"/);
  assert.match(teacherHtml, /id="rq-progress-counts"/);
});

test('the queue fetches from the review-queue endpoint and renders priority + reasons', () => {
  assert.match(RV, /fetch\(`\/api\/v1\/catalog\/question-extraction\/review-queue\?\$\{params\.toString\(\)\}`/);
  assert.match(RV, /rq-priority-\$\{it\.priority\}/);
  assert.match(RV, /review_reasons/);
});

test('filters exist for status and reason and re-fetch the queue on change', () => {
  assert.match(teacherHtml, /id="rq-filter-status"/);
  assert.match(teacherHtml, /id="rq-filter-reason"/);
  assert.match(RV, /rq\.filters\.review_status = statusSel\.value; loadReviewQueue\(\)/);
  assert.match(RV, /rq\.filters\.reason = reasonSel\.value; loadReviewQueue\(\)/);
});

test('priority is never computed in the frontend - only rendered from the backend-computed field', () => {
  assert.doesNotMatch(RV, /function computePriority/);
  assert.match(RV, /it\.priority/);
});

test('the progress bar reflects real counts, not an estimate', () => {
  assert.match(RV, /rq\.progress\.percent_complete/);
  assert.match(RV, /rq\.progress\.approved/);
  assert.match(RV, /rq\.progress\.pending/);
});

// -- 6/7/10/11/12/13/14. Tela de revisão ---------------------------------
test('the review screen has a side-by-side layout: PDF page image + editable statement/options', () => {
  assert.match(teacherHtml, /id="rv-panel"/);
  assert.match(teacherHtml, /id="rv-page-img"/);
  assert.match(teacherHtml, /id="rv-statement"/);
  assert.match(teacherHtml, /id="rv-options"/);
});

test('opening a question calls start-review before editing (REVIEW_REQUIRED -> IN_REVIEW)', () => {
  assert.match(RV, /\/questions\/\$\{questionId\}\/start-review`/);
});

test('page navigation (previous/next) exists for the original PDF', () => {
  assert.match(teacherHtml, /id="rv-prev-page"/);
  assert.match(teacherHtml, /id="rv-next-page"/);
  assert.match(RV, /rv\.currentPage -= 1/);
  assert.match(RV, /rv\.currentPage \+= 1/);
});

test('alternatives can be added, removed and edited - never assumed to require A-E', () => {
  assert.match(teacherHtml, /id="rv-add-option"/);
  assert.match(RV, /data-rv-remove-option/);
  assert.match(RV, /rv\.options\.push\(\{ label: '', text: '' \}\)/);
  assert.match(RV, /rv\.options\.splice\(/);
});

test('editing never overwrites raw_text - only reviewed_text/options/notes are sent', () => {
  assert.match(RV, /reviewed_text: document\.getElementById\('rv-statement'\)\.value/);
  assert.doesNotMatch(RV, /raw_text:\s*document/);
});

test('approve validates and shows exact blockers, never silently forces approval', () => {
  assert.match(teacherHtml, /id="rv-validation-msg"/);
  assert.match(RV, /questions\/\$\{rv\.question\.id\}\/approve`/);
  assert.match(RV, /tmMsg\('rv-validation-msg'/);
});

test('reject requires a structured reason, never a bare rejection', () => {
  assert.match(teacherHtml, /id="rv-reject-reason"/);
  assert.match(teacherHtml, />Duplicada</);
  assert.match(RV, /body: JSON\.stringify\(\{ reason \}\)/);
});

test('save-and-continue advances the queue; save-and-exit closes the panel - never forces a return to the queue screen manually', () => {
  assert.match(teacherHtml, /id="rv-save-continue-btn"/);
  assert.match(teacherHtml, /id="rv-save-exit-btn"/);
  assert.match(RV, /saveContinueBtn\.addEventListener\('click', async \(\) => \{[\s\S]*?goToNextInQueue\(\)/);
});

test('keyboard shortcuts exist (Ctrl/Cmd+Enter approve, Ctrl/Cmd+S save, arrows navigate) and never fire inside text fields', () => {
  assert.match(RV, /mod && e\.key === 'Enter'/);
  assert.match(RV, /mod && e\.key\.toLowerCase\(\) === 's'/);
  assert.match(RV, /ArrowLeft/);
  assert.match(RV, /ArrowRight/);
  assert.match(RV, /const inField = tag === 'input' \|\| tag === 'textarea' \|\| tag === 'select'/);
});

// -- 8. Assets ------------------------------------------------------------
test('unassigned images can be associated or ignored, never discarded automatically', () => {
  assert.match(teacherHtml, /id="rv-assets-banner"/);
  assert.match(RV, /candidate-assets`/);
  assert.match(RV, /data-rv-associate/);
  assert.match(RV, /data-rv-ignore-asset/);
  assert.match(RV, /\/assets\/\$\{assetId\}\/\$\{action\}`/);
});

// -- 9. Orphan text ---------------------------------------------------------
test('orphan text offers add-to-question or ignore, and the original is never lost', () => {
  assert.match(teacherHtml, /id="rv-orphan-banner"/);
  assert.match(teacherHtml, /id="rv-orphan-add"/);
  assert.match(teacherHtml, /id="rv-orphan-ignore"/);
  assert.match(RV, /orphan_text_candidate/);
});

// -- 18/19. Publicação -----------------------------------------------------
test('publication is a separate explicit step, gated on a run-level summary', () => {
  assert.match(teacherHtml, /id="pub-summary-btn"/);
  assert.match(teacherHtml, /id="pub-publish-btn"/);
  assert.match(RV, /publish-summary`/);
  assert.match(RV, /runs\/\$\{qe\.lastRunId\}\/publish`/);
});

test('the publish button is hidden until there is at least one approved question', () => {
  assert.match(RV, /pub-publish-btn'\)\.hidden = data\.approved <= 0/);
});

// -- 25. Responsividade ------------------------------------------------
test('the review layout stacks on narrow viewports and wide tables scroll instead of the page', () => {
  assert.match(teacherCss, /@media \(max-width: 768px\)[\s\S]*?\.tm-detail-grid \{ grid-template-columns: 1fr; \}/);
  assert.match(teacherHtml, /class="tm-table-wrap" style="overflow-x:auto;"/);
});

// -- 31. AI guard -----------------------------------------------------------
test('NO AI anywhere in the review/publish UI block', () => {
  assert.doesNotMatch(RV, /openai|gpt/i);
});
