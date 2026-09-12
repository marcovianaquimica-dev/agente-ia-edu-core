/**
 * PHASE 27 - Question Extraction Engine frontend (teacher portal, minimal
 * review UI per spec s19 - "priorizar a engine e o backend").
 *
 * PHASE 29 superseded the per-question inline approve/publish/reject
 * actions with a dedicated review queue + review screen (see
 * test_phase29_authorial_question_review_frontend.js) - the extraction
 * panel itself (run/list/status display) is unchanged and still covered
 * here; the old per-row action assertions were moved/updated there.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const teacherHtml = fs.readFileSync(path.join(WEB, 'teacher.html'), 'utf8');
const teacherJs = fs.readFileSync(path.join(WEB, 'teacher.js'), 'utf8');

const QE = teacherJs.slice(teacherJs.indexOf('PHASE 27/28/29 — Question Extraction Engine'), teacherJs.indexOf('// PHASE 29 — Fila de Revisão'));

test('the extraction panel exists inside the material ingestion review screen', () => {
  assert.match(teacherHtml, /id="qe-run-btn"[^>]*>Extrair questões/);
  assert.match(teacherHtml, /id="qe-expected-count"/);
  assert.match(teacherHtml, /id="qe-questions"/);
});

test('running extraction posts to the document-scoped endpoint with the optional expected count', () => {
  assert.match(QE, /fetch\(`\/api\/v1\/catalog\/question-extraction\/\$\{docId\}\/run`, \{/);
  assert.match(QE, /expected_question_count: expected \? Number\(expected\) : null/);
});

test('each question shows type, confidence, cross-page flag and status - never fabricated', () => {
  assert.match(QE, /q\.question_type/);
  assert.match(QE, /q\.extraction_confidence \* 100/);
  assert.match(QE, /cross_page \? ' · atravessa páginas' : ''/);
  assert.match(QE, /QE_STATUS_LABEL/);
});

test('each extraction row opens the PHASE 29 review screen - no inline approve/publish/reject anymore', () => {
  assert.match(QE, /data-qe-action="review"/);
  assert.match(QE, /openReviewQuestion\(btn\.dataset\.qid\)/);
});

test('reuses the existing teacher auth header pattern - no parallel authorization', () => {
  assert.match(QE, /headers: tmHeaders\(\)/);
});

test('NO AI anywhere in the extraction UI block', () => {
  assert.doesNotMatch(QE, /openai|gpt/i);
});
