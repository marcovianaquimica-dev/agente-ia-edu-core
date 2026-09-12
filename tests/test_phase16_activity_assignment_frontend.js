/**
 * PHASE 16 - activity distribution frontend (professor + student).
 *
 * Static assertions over the shipped web assets (node:test + regex), matching
 * the style of the PHASE 13/14/15 frontend tests. Verifies the "Distribuir"
 * flow on Minhas Listas, the distributions listing, and the student-facing
 * "Atividades" view - and that NONE of it anticipates PHASE 17 (no attempt /
 * answer / grade / performance).
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const qbJs = fs.readFileSync(path.join(WEB, 'question-bank.js'), 'utf8');
const qbHtml = fs.readFileSync(path.join(WEB, 'question-bank.html'), 'utf8');
const appJs = fs.readFileSync(path.join(WEB, 'app.js'), 'utf8');
const indexHtml = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');

// ---------------------------------------------------------------------------
// Professor - Minhas Listas -> Distribuir
// ---------------------------------------------------------------------------

test('a "Distribuir" action is offered only for finalized (published) lists', () => {
  const row = qbJs.match(/data\.items\.map\(\(l\) => `[\s\S]*?`\)\.join\(''\)/)[0];
  assert.match(row, /qb-ml-distribute/);
  assert.match(row, /qb-ml-distributions/);
  // both distribution controls are inside a `status === 'published'` guard
  const guarded = row.match(/l\.status === 'published' \? `<button class="btn btn-primary qb-ml-distribute[\s\S]*?` : ''/);
  assert.ok(guarded, 'distribute buttons must be guarded by published status');
  // a draft only gets Editar / Finalizar
  assert.match(row, /l\.status !== 'published' \? `<button class="btn btn-secondary qb-ml-edit/);
});

test('the distribute dialog has the 4 ordered steps: recipients -> period -> review -> publish', () => {
  const steps = [...qbHtml.matchAll(/data-dstep="([a-z]+)"/g)].map((m) => m[1]);
  // step list + one panel per step
  assert.deepStrictEqual(
    ['recipients', 'period', 'review', 'done', 'recipients', 'period', 'review', 'done'],
    steps.slice(0, 8),
  );
  assert.match(qbHtml, /1\. Destinat/);
  assert.match(qbHtml, /2\. Per[ií]odo/);
  assert.match(qbHtml, /3\. Revis/);
  assert.match(qbHtml, /4\. Publica/);
  assert.match(qbJs, /function setDistStep\(step\)/);
});

test('recipients step chooses between a CLASS (turma) and a STUDENT (aluno)', () => {
  assert.match(qbHtml, /name="qb-dist-target" value="CLASS"/);
  assert.match(qbHtml, /name="qb-dist-target" value="STUDENT"/);
  assert.match(qbHtml, /Uma turma/);
  assert.match(qbHtml, /Um aluno espec/);
  assert.match(qbJs, /function currentDistTarget\(\)/);
  // the target-id label follows the chosen target type
  assert.match(qbJs, /qb-dist-target-label'\)\.textContent = currentDistTarget\(\) === 'CLASS'/);
});

test('period step exposes optional available_from and due_at date inputs', () => {
  assert.match(qbHtml, /id="qb-dist-available-from" *\/?>/);
  assert.match(qbHtml, /id="qb-dist-due-at" *\/?>/);
  assert.match(qbHtml, /type="datetime-local"/);
  assert.match(qbJs, /available_from: localInputToIso\(\$\('qb-dist-available-from'\)\.value\)/);
  assert.match(qbJs, /due_at: localInputToIso\(\$\('qb-dist-due-at'\)\.value\)/);
});

test('review step summarizes the list, recipient and period before publishing', () => {
  assert.match(qbJs, /function renderDistReview\(\)/);
  assert.match(qbJs, /<strong>Lista:<\/strong>/);
  assert.match(qbJs, /<strong>Destinat[^<]*:<\/strong>/);
  assert.match(qbJs, /<strong>Prazo:<\/strong>/);
});

test('publishing POSTs to the list assignments endpoint and reports idempotent replay', () => {
  assert.match(qbJs, /fetch\(`\$\{API\}\/lists\/\$\{dist\.listId\}\/assignments`, \{\s*method: 'POST'/);
  assert.match(qbJs, /x-idempotent-replay/);
  assert.match(qbJs, /já estava distribu/);
});

test('"Ver distribuições" lists target, date, period and status - never grades or performance', () => {
  assert.match(qbJs, /function toggleDistributions\(listId\)/);
  assert.match(qbJs, /fetch\(`\$\{API\}\/lists\/\$\{listId\}\/assignments`/);
  const table = qbJs.match(/qb-dist-table[\s\S]*?<\/tbody><\/table>/)[0];
  assert.match(table, /Alvo/);
  assert.match(table, /Distribu[ií]da em/);
  assert.match(table, /Per[ií]odo/);
  assert.match(table, /Status/);
  assert.match(table, /Disponibilidade/);
  // forbidden PHASE 17 columns
  assert.doesNotMatch(table, /[Nn]ota|[Pp]ercentual|[Rr]espondidas|[Dd]esempenho|[Aa]certos/);
});

test('the Minhas Listas row shows distribution count / active / last date', () => {
  assert.match(qbJs, /qb-mylist-dist-meta/);
  assert.match(qbJs, /function hydrateDistributionMeta\(ids\)/);
  assert.match(qbJs, /Distribui[çc][õo]es: \$\{s\.count \|\| 0\}/);
  assert.match(qbJs, /última: \$\{last\}/);
});

test('professor distribution code never creates an attempt / answer / correction', () => {
  const raw = qbJs.slice(qbJs.indexOf('PHASE 16: distribute'), qbJs.indexOf('PHASE 14: list generator wizard'));
  assert.ok(raw.length > 500);
  // ignore the explanatory `//` comments; assert on executable code only
  const p16 = raw.split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');
  assert.doesNotMatch(p16, /attempt|tentativa|StudentResponse|\/answer|autosave|corre[çc][ãa]o|nota do aluno/i);
  // the only writes are POSTs to the assignments endpoint (create / idempotent replay)
  const writes = [...p16.matchAll(/method:\s*'(POST|PUT|PATCH|DELETE)'/g)].map((m) => m[1]);
  assert.deepStrictEqual(writes, ['POST']);
});

// ---------------------------------------------------------------------------
// Student - Atividades (visibility only)
// ---------------------------------------------------------------------------

test('the student portal has an "Atividades" nav item and view panel', () => {
  assert.match(indexHtml, /data-view="activities"/);
  assert.match(indexHtml, /id="view-activities"/);
  assert.match(indexHtml, /id="activities-list-container"/);
  assert.match(indexHtml, /id="activity-entry-screen"/);
});

test('switching to the activities view triggers a load from /api/v1/student/activities', () => {
  assert.match(appJs, /if \(viewName === 'activities'\) loadActivitiesView\(\);/);
  assert.match(appJs, /studentRequest\('\/api\/v1\/student\/activities'\)/);
  assert.match(appJs, /function loadActivitiesView\(\)/);
});

test('each activity card shows title, author, question count, dates and a status badge', () => {
  const card = appJs.match(/<article class="activity-card card"[\s\S]*?<\/article>`/)[0];
  assert.match(card, /a\.title/);
  assert.match(card, /author/);
  assert.match(card, /a\.question_count/);
  assert.match(card, /available_from/);
  assert.match(card, /a\.due_at/);
  assert.match(appJs, /DISPONÍVEL/);
  assert.match(appJs, /AGUARDANDO/);
  assert.match(appJs, /ENCERRADA/);
});

test('only a DISPONIVEL activity can be opened; others are disabled', () => {
  assert.match(appJs, /const canOpen = a\.availability === 'DISPONIVEL';/);
  assert.match(appJs, /\$\{canOpen \? '' : 'disabled'\}/);
});

test('opening an activity shows an entry screen only - no attempt is created', () => {
  assert.match(appJs, /function openActivityEntry\(assignmentId\)/);
  assert.match(appJs, /studentRequest\(`\/api\/v1\/student\/activities\/\$\{assignmentId\}`\)/);
  assert.match(appJs, /Iniciar atividade/);
  assert.match(appJs, /entry_screen/);
  const fn = appJs.slice(appJs.indexOf('async function openActivityEntry'), appJs.indexOf('const activitiesContainer'));
  assert.doesNotMatch(fn, /method:\s*'POST'|method:\s*'PUT'|\/answer|autosave|corre[çc][ãa]o|salvar resposta/i);
});

test('the PHASE 16 activities sub-block stays visibility-only (execution is PHASE 17)', () => {
  // the PHASE 16 region ends where the PHASE 17 player block begins
  const start = appJs.indexOf('PHASE 16: Atividades');
  const end = appJs.indexOf('PHASE 17: student activity PLAYER');
  assert.ok(start > 0 && end > start, 'PHASE 16 and PHASE 17 markers must both exist, in order');
  const p16 = appJs.slice(start, end);
  assert.ok(p16.length > 500);
  assert.doesNotMatch(p16, /StudentResponse|autosave|anti-?cheat|fullscreen|corre[çc][ãa]o|\bnota\b|ranking|cron[oô]metro|timer/i);
  // PHASE 16 itself only GETs (the entry screen); writes belong to PHASE 17
  assert.doesNotMatch(p16, /method:\s*'(POST|PUT|PATCH|DELETE)'/);
});
