/**
 * PHASE 19 - pedagogical analysis frontend (student result screen).
 *
 * Static assertions over the student SPA (node:test + regex). Verifies the
 * "Desempenho pedagógico" section: overview, by-discipline, by-content,
 * strengths, improvements - fed by a READ-ONLY GET .../result/analysis - and
 * that it introduces NO grade / TRI / ranking / student comparison / domain map
 * / learning path.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const appJs = fs.readFileSync(path.join(WEB, 'app.js'), 'utf8');
const indexHtml = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
const stylesCss = fs.readFileSync(path.join(WEB, 'styles.css'), 'utf8');

const ANALYSIS = appJs.slice(
  appJs.indexOf('PHASE 19: análise pedagógica'),
  appJs.indexOf('function closeResultScreen'),
);
const ANALYSIS_CODE = ANALYSIS.split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');

test('the analysis block exists', () => {
  assert.ok(ANALYSIS.length > 1200, 'PHASE 19 analysis block should be present');
});

test('index.html carries the "Desempenho pedagógico" section with all 5 subsections', () => {
  assert.match(indexHtml, /id="activity-analysis"[^>]*\bhidden\b/);
  assert.match(indexHtml, />\s*Desempenho pedagógico\s*</);
  for (const id of ['analysis-overview', 'analysis-by-discipline', 'analysis-by-content',
                    'analysis-strengths', 'analysis-improvements']) {
    assert.ok(indexHtml.includes(`id="${id}"`), `missing #${id}`);
  }
  assert.match(indexHtml, /id="result-toggle-analysis"/);
});

test('the analysis is fetched READ-ONLY from the result/analysis endpoint after correction', () => {
  assert.match(appJs, /function loadResultAnalysis\(assignmentId\)/);
  assert.match(appJs, /\/api\/v1\/student\/activities\/\$\{assignmentId\}\/attempt\/result\/analysis/);
  assert.match(appJs, /loadResultAnalysis\(aid\);/);          // called from correctAndShowResult
  // GET only - no POST/PUT/PATCH/DELETE anywhere in the analysis block
  assert.doesNotMatch(ANALYSIS_CODE, /method:\s*'(POST|PUT|PATCH|DELETE)'/);
});

test('the overview shows questions / classified / unclassified / provisional / aproveitamento', () => {
  const ov = appJs.match(/analysis-overview'\)\.innerHTML = `[\s\S]*?`;/)[0];
  assert.match(ov, /Visão geral/);
  assert.match(ov, /s\.total_questions/);
  assert.match(ov, /s\.classified_questions/);
  assert.match(ov, /s\.unclassified_questions/);
  assert.match(ov, /s\.provisional_questions/);
  assert.match(ov, /pct\(s\.accuracy\)/);
  assert.match(ov, /Aproveitamento/);
});

test('unclassified questions are surfaced, not hidden', () => {
  assert.match(appJs, /sem classificação pedagógica entram no total, mas não são atribuídas a um conteúdo/i);
});

test('by-discipline and by-content render as tables with an accuracy and a band', () => {
  assert.match(appJs, /function renderPerfTable\(title, rows, labelFn, opts\)/);
  assert.match(appJs, /renderPerfTable\('Por disciplina'/);
  assert.match(appJs, /renderPerfTable\('Por conteúdo'/);
  assert.match(appJs, /r\.correct\}\/\$\{r\.answered\}/);      // acertos/respondidas
  assert.match(appJs, /pct\(r\.accuracy\)/);
  assert.match(appJs, /activity-analysis-band \$\{BAND_CLASS\[r\.band\]/);
  // content table also exposes the provisional / forced-closure counts
  assert.match(appJs, /showProvisional/);
  assert.match(appJs, /forced_closure_count/);
});

test('the band vocabulary maps to the deterministic backend bands', () => {
  const map = appJs.match(/const BAND_LABEL = \{[\s\S]*?\};/)[0];
  for (const b of ['PONTO_FORTE', 'PONTO_MELHORIA', 'DESEMPENHO_INTERMEDIARIO', 'INSUFFICIENT_SAMPLE', 'SEM_DADOS']) {
    assert.ok(map.includes(b), `band ${b} not mapped`);
  }
});

test('strengths and improvements are listed from the analysis, with an empty-sample fallback', () => {
  assert.match(appJs, /function renderVerdictList\(title, entries, emptyText\)/);
  assert.match(appJs, /renderVerdictList\('Pontos fortes', a\.strengths/);
  assert.match(appJs, /renderVerdictList\('Pontos de melhoria', a\.improvements/);
  assert.match(appJs, /mínimo de questões respondidas/i);     // insufficient-sample explanation
});

test('percentages are framed as aproveitamento, never as a grade', () => {
  assert.match(appJs, /aproveitamento \(acertos ÷ respondidas\), não é nota/i);
  assert.match(appJs, /function pct\(accuracy\)/);
});

test('the analysis section starts hidden and toggles', () => {
  assert.match(appJs, /result-toggle-analysis'\)/);
  assert.match(appJs, /sec\.hidden = !sec\.hidden/);
  assert.match(appJs, /an\.hidden = true;/);                   // reset on each new result render
});

test('closing the result clears the analysis state', () => {
  assert.match(appJs, /analysisView\.data = null;/);
});

test('CSS: analysis tables scroll inside their own wrapper (no page horizontal scroll)', () => {
  assert.match(stylesCss, /\.activity-analysis-tablewrap\s*\{[^}]*overflow-x:\s*auto/);
  assert.match(stylesCss, /\.activity-analysis-band\.is-strong/);
  assert.match(stylesCss, /\.activity-analysis-band\.is-improve/);
  assert.match(stylesCss, /@media \(max-width: 640px\)[\s\S]*\.activity-analysis-figures li/);
});

test('the analysis frontend introduces no grade / TRI / ranking / comparison / domain map / trilha', () => {
  assert.doesNotMatch(ANALYSIS_CODE,
    /\bnota\b(?!\.)|nota enem|\bTRI\b|MIRT|ranking|gamif|comparar|posição na turma|melhor que|pior que|domain[- ]?map|mapa de domínio|adaptive|recomend/i);
});
