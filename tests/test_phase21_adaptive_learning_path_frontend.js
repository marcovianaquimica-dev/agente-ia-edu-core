/**
 * PHASE 21 - "Trilha de Estudos" frontend (student adaptive learning path view).
 *
 * Static assertions over the student SPA (node:test + regex). Verifies the new
 * nav item + view, the ordered step rendering (state, reason, prerequisites,
 * "destrava"), the summary, the mastered list, the transparency + provisional
 * notes, disabled ("em breve") actions, the graph-invalid / empty / error
 * states, and that it introduces NO gamification / ranking / student comparison
 * / grade.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const appJs = fs.readFileSync(path.join(WEB, 'app.js'), 'utf8');
const indexHtml = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
const stylesCss = fs.readFileSync(path.join(WEB, 'styles.css'), 'utf8');

const PATH_BLOCK = appJs.slice(
  appJs.indexOf('PHASE 21: "Trilha de Estudos"'),
  appJs.indexOf('// Initial Boot'),
);
const PATH_CODE = PATH_BLOCK.split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');

test('the path block exists', () => {
  assert.ok(PATH_BLOCK.length > 1500, 'PHASE 21 "Trilha de Estudos" block should be present');
});

test('index.html has the "Trilha de Estudos" nav item and view', () => {
  assert.match(indexHtml, /data-view="study-path"[\s\S]*?Trilha de Estudos/);
  assert.match(indexHtml, /id="view-study-path"/);
  assert.match(indexHtml, /id="path-summary"/);
  assert.match(indexHtml, /id="path-body"/);
  assert.match(indexHtml, /id="path-mastered"/);
  assert.match(indexHtml, /id="path-provisional-note"[^>]*\bhidden\b/);
});

test('switching to the study-path view loads GET /api/v1/student/study-path', () => {
  assert.match(appJs, /if \(viewName === 'study-path'\) loadStudyPathView\(\);/);
  assert.match(appJs, /function loadStudyPathView\(\)/);
  assert.match(appJs, /studentRequest\('\/api\/v1\/student\/study-path'\)/);
});

test('the distinct endpoint is used - the legacy /learning-path is NOT touched', () => {
  assert.doesNotMatch(PATH_CODE, /\/api\/v1\/student\/learning-path/);
});

test('the summary shows próximos passos / bloqueados / diagnóstico / dominados', () => {
  const sm = appJs.match(/path-summary'\)\.innerHTML = [\s\S]*?`;/)[0];
  assert.match(sm, /s\.step_count/);
  assert.match(sm, /s\.blocked_count/);
  assert.match(sm, /s\.insufficient_evidence_count/);
  assert.match(sm, /s\.mastered_count/);
});

test('each step renders order, state badge, reason, prerequisites and "destrava"', () => {
  assert.match(appJs, /function renderPathStep\(step\)/);
  assert.match(appJs, /step\.recommended_order/);
  assert.match(appJs, /PATH_STATE_LABEL\[step\.content_state\]/);
  assert.match(appJs, /<strong>Por quê:<\/strong> \$\{escActivity\(step\.priority_reason\)\}/);
  assert.match(appJs, /step\.unsatisfied_prerequisites/);
  assert.match(appJs, /step\.blocks_contents/);
  const map = appJs.match(/const PATH_STATE_LABEL = \{[\s\S]*?\};/)[0];
  for (const st of ['BLOCKED_BY_PREREQUISITE', 'INSUFFICIENT_EVIDENCE', 'NEEDS_REVIEW',
                    'RECOMMENDED', 'READY', 'MASTERED']) {
    assert.ok(map.includes(st), `state ${st} not labelled`);
  }
});

test('actions are shown as disabled "em breve" (no false functional buttons)', () => {
  assert.match(appJs, /disabled title="\$\{escActivity\(step\.action_note \|\| 'Em breve'\)\}"/);
  assert.match(appJs, /— em breve/);
  assert.match(appJs, /step\.action_type && step\.action_type !== 'NONE'/);
});

test('the mastered list is rendered separately, never as a ranking', () => {
  assert.match(appJs, /Conteúdos que você já demonstrou dominar/);
  assert.match(appJs, /d\.mastered \|\| \[\]/);
});

test('transparency + provisional-classification notes are surfaced', () => {
  assert.match(indexHtml, /É uma orientação de estudo, não uma nota/i);
  assert.match(appJs, /d\.transparency/);
  assert.match(appJs, /d\.provisional_note/);
});

test('the PREREQUISITE_GRAPH_INVALID state is handled gracefully', () => {
  assert.match(appJs, /PREREQUISITE_GRAPH_INVALID/);
  assert.match(appJs, /árvore de pré-requisitos/i);
});

test('empty (NO_EVIDENCE) and API-error states have their own messages', () => {
  assert.match(appJs, /d\.state === 'NO_EVIDENCE'/);
  assert.match(appJs, /Ainda não temos evidências suficientes para montar sua trilha/);
  assert.match(appJs, /Não foi possível carregar sua trilha\./);
});

test('no gamification / ranking / student comparison / grade in the path view', () => {
  assert.doesNotMatch(PATH_CODE,
    /\bnota\b|nota enem|\bTRI\b|MIRT|ranking|medalha|pontos|competi|comparar com|posição na turma|melhor que|pior que|n[ií]vel \d|gamif/i);
});

test('CSS: figure cards reflow on mobile; step states are visually distinct', () => {
  assert.match(stylesCss, /@media \(max-width: 640px\)[\s\S]*\.path-figures/);
  assert.match(stylesCss, /\.path-step\.is-blocked/);
  assert.match(stylesCss, /\.path-step-badge\.is-study/);
});
