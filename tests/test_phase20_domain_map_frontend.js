/**
 * PHASE 20 - "Meu Domínio" frontend (student curriculum Domain Map view).
 *
 * Static assertions over the student SPA (node:test + regex). Verifies the new
 * nav item + view, the summary + per-discipline/content rendering, the content
 * detail (subcontents, prerequisites, provisional/forced-closure/visual notes),
 * the "Recalcular" (rebuild) action, and that it introduces NO grade / ranking /
 * student comparison / learning path / "study X now".
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const appJs = fs.readFileSync(path.join(WEB, 'app.js'), 'utf8');
const indexHtml = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
const stylesCss = fs.readFileSync(path.join(WEB, 'styles.css'), 'utf8');

// the PHASE 20 block ends where the PHASE 21 "Trilha de Estudos" block begins
const _p20End = [
  appJs.indexOf('PHASE 21: "Trilha de Estudos"'),
  appJs.indexOf('// Initial Boot'),
].filter((i) => i > 0).sort((a, b) => a - b)[0];
const DOMAIN = appJs.slice(appJs.indexOf('PHASE 20: "Meu Domínio"'), _p20End);
const DOMAIN_CODE = DOMAIN.split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');

test('the domain block exists', () => {
  assert.ok(DOMAIN.length > 1500, 'PHASE 20 "Meu Domínio" block should be present');
});

test('index.html has the "Meu Domínio" nav item and view', () => {
  assert.match(indexHtml, /data-view="domain"[\s\S]*?Meu Domínio/);
  assert.match(indexHtml, /id="view-domain"/);
  assert.match(indexHtml, /id="domain-summary"/);
  assert.match(indexHtml, /id="domain-body"/);
  assert.match(indexHtml, /id="domain-detail"[^>]*\bhidden\b/);
  assert.match(indexHtml, /id="domain-refresh"/);
});

test('switching to the domain view loads GET /api/v1/student/domain', () => {
  assert.match(appJs, /if \(viewName === 'domain'\) loadDomainView\(\);/);
  assert.match(appJs, /function loadDomainView\(\)/);
  assert.match(appJs, /studentRequest\('\/api\/v1\/student\/domain'\)/);
});

test('the summary shows conteúdos / evidência / questões respondidas / aproveitamento', () => {
  const sm = appJs.match(/domain-summary'\)\.innerHTML = `[\s\S]*?`;/)[0];
  assert.match(sm, /s\.content_count/);
  assert.match(sm, /s\.observed_count/);
  assert.match(sm, /s\.insufficient_evidence_count/);
  assert.match(sm, /s\.questions_answered/);
  assert.match(sm, /domainPct\(s\.accuracy\)/);
  assert.match(sm, /Evidência suficiente/);
  assert.match(sm, /Aproveitamento/);
});

test('contents render per discipline with answered count, aproveitamento and evidence state', () => {
  assert.match(appJs, /d\.disciplines\.map\(\(disc\) =>/);
  assert.match(appJs, /disc\.contents\.map\(\(c\) =>/);
  assert.match(appJs, /c\.questions_answered\} respondidas/);
  assert.match(appJs, /domainPct\(c\.accuracy\)/);
  assert.match(appJs, /c\.evidence_state === 'OBSERVED' \? 'is-ok' : 'is-low'/);
  const map = appJs.match(/const EVIDENCE_LABEL = \{[\s\S]*?\};/)[0];
  assert.match(map, /OBSERVED/);
  assert.match(map, /INSUFFICIENT_EVIDENCE/);
});

test('clicking a content opens its detail via GET /domain/content/{code}', () => {
  assert.match(appJs, /function openDomainContent\(contentCode\)/);
  assert.match(appJs, /\/api\/v1\/student\/domain\/content\/\$\{encodeURIComponent\(contentCode\)\}/);
  assert.match(appJs, /domain-body'\)\.addEventListener\('click'/);
  assert.match(appJs, /closest\('\.domain-content-row'\)/);
});

test('the content detail shows prerequisites, definitive vs provisional evidence, subcontents', () => {
  assert.match(appJs, /Pré-requisitos:/);
  assert.match(appJs, /c\.definitive_evidence_count/);
  assert.match(appJs, /c\.provisional_evidence_count/);
  assert.match(appJs, /c\.forced_closure_evidence_count \? /);
  assert.match(appJs, /c\.visual_dependency_evidence_count \? /);
  assert.match(appJs, /c\.subcontents \|\| \[\]/);
});

test('"Recalcular" rebuilds the map (idempotent recompute) then reloads', () => {
  assert.match(appJs, /domain-refresh'\)/);
  assert.match(appJs, /\/api\/v1\/student\/domain\/rebuild`?', \{ method: 'POST' \}\)/);
  assert.match(appJs, /loadDomainView\(\);/);
});

test('the unclassified note is surfaced, not hidden', () => {
  assert.match(appJs, /d\.unclassified_note/);
});

test('the domain view frames metrics as evidence, never as a grade/ranking/path', () => {
  assert.match(indexHtml, /não é nota, ranking nem comparação com outros alunos/i);
  assert.doesNotMatch(DOMAIN_CODE,
    /\bnota\b|nota enem|\bTRI\b|MIRT|ranking|gamif|comparar com|posição na turma|melhor que|pior que|adaptive|trilha|recomend|próxima (melhor )?ação|estude (agora|já)/i);
});

test('CSS: figure cards reflow on mobile, content rows have visible focus', () => {
  assert.match(stylesCss, /@media \(max-width: 640px\)[\s\S]*\.domain-figures/);
  assert.match(stylesCss, /\.domain-content-row:focus-visible/);
  assert.match(stylesCss, /\.domain-evidence\.is-ok/);
});
