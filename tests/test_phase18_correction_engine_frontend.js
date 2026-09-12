/**
 * PHASE 18 - correction engine & student result frontend.
 *
 * Static assertions over the student SPA (node:test + regex). Verifies the
 * result screen (summary + per-question detail + navigation back), that it is
 * reached only after completion + correction, and that it introduces NO grade /
 * TRI / ranking / comparison.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const appJs = fs.readFileSync(path.join(WEB, 'app.js'), 'utf8');
const indexHtml = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
const stylesCss = fs.readFileSync(path.join(WEB, 'styles.css'), 'utf8');

// the PHASE 18 result block ends where PHASE 19 (pedagogical analysis) begins,
// or at the player wiring if PHASE 19 is not present.
const _p18End = [
  appJs.indexOf('PHASE 19: análise pedagógica'),
  appJs.indexOf('// ----- player event wiring -----'),
  appJs.indexOf('// Initial Boot'),
].filter((i) => i > 0).sort((a, b) => a - b)[0];
const RESULT = appJs.slice(appJs.indexOf('PHASE 18: correção determinística'), _p18End);
const RESULT_CODE = RESULT.split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');

test('the result block exists', () => {
  assert.ok(RESULT.length > 800, 'PHASE 18 result block should be present');
});

test('index.html carries the result card: title, summary, ver-questões, back', () => {
  assert.match(indexHtml, /id="activity-result"/);
  assert.match(indexHtml, /id="activity-result-summary"/);
  assert.match(indexHtml, /id="result-toggle-questions"[^>]*>Ver questões</);
  assert.match(indexHtml, /id="result-back"[^>]*>Voltar para Atividades</);
  assert.match(indexHtml, /id="activity-result-questions"/);
  assert.match(indexHtml, /Atividade finalizada/);
});

test('the result screen is reached only after completion, via the correct endpoint', () => {
  // applyPlayerState routes a COMPLETED attempt to correction
  assert.match(appJs, /if \(done\) correctAndShowResult\(\);/);
  assert.match(appJs, /function correctAndShowResult\(\)/);
  assert.match(appJs, /\/api\/v1\/student\/activities\/\$\{aid\}\/attempt\/correct`, \{ method: 'POST' \}/);
  // a 404/409 falls back to a GET of the (already existing) result
  assert.match(appJs, /res\.status === 404 \|\| res\.status === 409/);
  assert.match(appJs, /\/api\/v1\/student\/activities\/\$\{aid\}\/attempt\/result`\)/);
});

test('the summary shows Questões / Acertos / Erros / Não respondidas / Aproveitamento', () => {
  assert.match(appJs, /function renderResultScreen\(data\)/);
  for (const label of ['Questões', 'Acertos', 'Erros', 'Não respondidas', 'Aproveitamento']) {
    assert.ok(RESULT.includes(label), `summary missing ${label}`);
  }
  assert.match(appJs, /r\.question_count/);
  assert.match(appJs, /r\.correct_count/);
  assert.match(appJs, /r\.incorrect_count/);
  assert.match(appJs, /r\.unanswered_count/);
  assert.match(appJs, /r\.aproveitamento_percent/);
});

test('the aproveitamento is explicitly framed as raw, not a grade', () => {
  assert.match(indexHtml, /não é nota, TRI ou ranking/i);
  // app.js: computed as acertos / questões, labelled "bruto", never "nota"
  assert.match(appJs, /Aproveitamento = acertos \/ questões \* 100 \(bruto\)/i);
});

test('"Ver questões" toggles the per-question detail', () => {
  assert.match(appJs, /result-toggle-questions'\)\.addEventListener\('click'/);
  assert.match(appJs, /box\.hidden = !box\.hidden/);
  assert.match(appJs, /box\.hidden \? 'Ver questões' : 'Ocultar questões'/);
});

test('each question row shows number, status, student answer and correct answer, in order', () => {
  assert.match(appJs, /\(data\.items \|\| \[\]\)\.map\(\(it\) =>/);
  assert.match(appJs, /it\.position/);
  assert.match(appJs, /it\.official_number/);
  assert.match(appJs, /Sua resposta: <strong>\$\{escActivity\(it\.selected_option_key \|\| '—'\)\}/);
  assert.match(appJs, /Resposta correta: <strong>\$\{escActivity\(it\.correct_option_key \|\| '—'\)\}/);
  const map = appJs.match(/const RESULT_STATUS = \{[\s\S]*?\};/)[0];
  assert.match(map, /CORRECT:/);
  assert.match(map, /INCORRECT:/);
  assert.match(map, /UNANSWERED:/);
});

test('resolution is deferred, never AI-generated here', () => {
  assert.match(appJs, /Resolução da questão: em breve\./);
  assert.doesNotMatch(RESULT_CODE, /openai|gpt|gerar resolução|explicação automática/i);
});

test('"Voltar para Atividades" closes the result and reloads the list', () => {
  assert.match(appJs, /function closeResultScreen\(\)/);
  assert.match(appJs, /result-back'\)\.addEventListener\('click', closeResultScreen\)/);
  assert.match(appJs, /loadActivitiesView\(\);/);
});

test('a COMPLETED activity opens straight to the result from the entry screen', () => {
  assert.match(appJs, /const finished = attemptStatus === 'COMPLETED';/);
  assert.match(appJs, /finished \? 'Ver resultado'/);
  assert.match(appJs, /if \(finished\) \{ player\.assignmentId = assignmentId; correctAndShowResult\(\); \}/);
});

test('the result frontend introduces no grade / TRI / ranking / comparison', () => {
  // "trilha" / "domínio" are sibling nav-view names (PHASE 22 adds a "Voltar para
  // a Trilha" button + a "não é uma nota escolar" disclaimer to the SAME block) —
  // they are navigation / anti-grade wording, not a grading signal.
  assert.doesNotMatch(RESULT_CODE, /nota enem|\bTRI\b|MIRT|ranking|gamif|comparar|posição na turma|melhor que/i);
});

test('CSS distinguishes correct / incorrect / unanswered', () => {
  assert.match(stylesCss, /\.activity-result-question\.is-correct/);
  assert.match(stylesCss, /\.activity-result-question\.is-incorrect/);
  assert.match(stylesCss, /\.activity-result-question\.is-unanswered/);
});

// ---- PHASE 18.1 visual-validation regressions ----

test('[hidden] toggling works on the flex overlays (explicit display:flex is neutralised)', () => {
  // #player-dialog and #activity-result-questions both set display:flex on a class,
  // which beats the [hidden] UA rule - each needs an explicit [hidden] override
  assert.match(stylesCss, /\.activity-player-dialog-backdrop\[hidden\]\s*\{\s*display:\s*none/);
  assert.match(stylesCss, /\.activity-result-questions\[hidden\]\s*\{\s*display:\s*none/);
});

test('the per-question detail starts hidden and is only shown via the toggle', () => {
  assert.match(indexHtml, /id="activity-result-questions"[^>]*\bhidden\b/);
  assert.match(appJs, /box\.hidden = true;/);   // renderResultScreen resets it closed
});

test('opening the player is a fresh entry - state does not carry over between activities', () => {
  const fn = appJs.slice(appJs.indexOf('async function startActivityPlayer'),
                         appJs.indexOf('function openPlayerShell'));
  assert.match(fn, /player\.state = null;/);
  assert.match(fn, /player\.index = 0;/);
  assert.match(fn, /player\.pending\.clear\(\);/);
});
