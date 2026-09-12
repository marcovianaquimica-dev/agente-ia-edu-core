/**
 * PHASE 26 - Authorial Material Ingestion Engine frontend (teacher portal).
 *
 * Static assertions (node:test + regex) over "Importar Material": upload,
 * ingestion list, review screen (classification edit, structure, detected
 * exercises), approve/publish/reject. Confirms INGESTION != PUBLICATION
 * (publish only enabled once APPROVED), reuse of the existing auth header
 * pattern (no parallel authorization), and no AI.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const teacherHtml = fs.readFileSync(path.join(WEB, 'teacher.html'), 'utf8');
const teacherJs = fs.readFileSync(path.join(WEB, 'teacher.js'), 'utf8');

const MI = teacherJs.slice(teacherJs.indexOf('PHASE 26 — "Importar Material"'), teacherJs.indexOf('// Initial Load'));

test('the nav item + view exist', () => {
  assert.match(teacherHtml, /data-view="material-ingestion"[\s\S]*?Importar Material/);
  assert.match(teacherHtml, /id="view-material-ingestion"/);
  assert.match(teacherJs, /'material-ingestion':\s*\{ title: 'Importar Material'/);
  assert.match(teacherJs, /state\.currentView === 'material-ingestion'\) loadMaterialIngestions\(\)/);
});

test('upload posts multipart form data to the ingestion endpoint', () => {
  assert.match(MI, /fetch\('\/api\/v1\/catalog\/ingestion\/upload', \{/);
  assert.match(MI, /method: 'POST'/);
  assert.match(MI, /new FormData\(\)/);
  assert.match(MI, /form\.append\('file', input\.files\[0\]\)/);
});

test('the file input accepts only PDF/DOCX/TXT/MD, up to 25MB per the API', () => {
  assert.match(teacherHtml, /id="mi-file"[^>]*accept="\.pdf,\.docx,\.txt,\.md"/);
});

test('the list reads GET /api/v1/catalog/ingestion and shows an honest status label', () => {
  assert.match(MI, /fetch\('\/api\/v1\/catalog\/ingestion', \{ headers: tmHeaders\(\) \}\)/);
  assert.match(MI, /MI_STATUS_LABEL/);
  assert.match(MI, /TAXONOMY_GAP/);
});

test('opening a review reads document + structure + exercises + classification in one call', () => {
  assert.match(MI, /fetch\(`\/api\/v1\/catalog\/ingestion\/\$\{id\}`, \{ headers: tmHeaders\(\) \}\)/);
  assert.match(MI, /d\.sections/);
  assert.match(MI, /d\.exercises/);
});

test('a TAXONOMY_GAP review is surfaced honestly, never a fabricated classification', () => {
  assert.match(MI, /classification_state === 'MAPPED'/);
  assert.match(MI, /nenhum conteúdo do currículo-v2 correspondeu/);
});

test('classification is editable and saved via PATCH, never auto-applied', () => {
  assert.match(MI, /method: 'PATCH'/);
  assert.match(MI, /\/classification`, \{/);
  assert.match(teacherHtml, /id="mi-discipline"/);
  assert.match(teacherHtml, /id="mi-content"/);
  assert.match(teacherHtml, /id="mi-subcontents"/);
});

test('INGESTION != PUBLICATION: Publicar is only enabled once the review is APPROVED', () => {
  assert.match(MI, /publishBtn\.disabled = r\.review_status !== 'APPROVED'/);
  assert.match(MI, /approveBtn\.disabled = !\(r\.review_status === 'PENDING_REVIEW' \|\| r\.review_status === 'NEEDS_REVIEW'\)/);
});

test('approve/publish/reject call the existing REST actions, not a bespoke flow', () => {
  assert.match(MI, /\/\$\{mi\.current\.review\.id\}\/\$\{action\}`/);
  assert.match(teacherHtml, /id="mi-approve-btn"/);
  assert.match(teacherHtml, /id="mi-publish-btn"/);
  assert.match(teacherHtml, /id="mi-reject-btn"/);
});

test('detected exercises are shown for review, flagged when uncertain - never silently accepted', () => {
  assert.match(MI, /q\.requires_review \? ' <span class="tm-badge">revisar<\/span>' : ''/);
});

test('reuses the existing teacher auth header pattern - no parallel authorization', () => {
  assert.match(MI, /Authorization.*Bearer \$\{state\.teacherId\}/);
  assert.match(MI, /headers: tmHeaders\(\)/);
});

test('NO AI anywhere in the ingestion UI block', () => {
  assert.doesNotMatch(MI, /openai|gpt|classifica.*automatica.*ia\b/i);
});
