/**
 * PHASE 22 - "Praticar agora" frontend (adaptive practice loop in the SPA).
 *
 * Static assertions over the student SPA (node:test + regex). Verifies that the
 * disabled "— em breve" action on a Trilha step becomes a real [Praticar agora]
 * button when the path says practice is available, that it opens a small
 * "Quantas questões?" selector (5/10/15/20) + [Começar prática], that starting a
 * practice POSTs /api/v1/student/practice and hands the returned id to the
 * REUSED PHASE 17 player (no second player), that the PHASE 18 result screen is
 * re-framed for practice ("Prática" badge + "não é uma nota escolar") and its
 * back button returns to the Trilha, that an insufficient bank surfaces the
 * standard message + counts, that a minimal direct "Praticar" entry
 * (disciplina -> conteúdo -> quantidade) exists, and that NOTHING here adds
 * gamification / ranking / comparison / grade or a second question bank.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const appJs = fs.readFileSync(path.join(WEB, 'app.js'), 'utf8');
const indexHtml = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
const stylesCss = fs.readFileSync(path.join(WEB, 'styles.css'), 'utf8');

const P22 = appJs.slice(
  appJs.indexOf('PHASE 22: "Praticar agora"'),
  appJs.indexOf('// Initial Boot'),
);
const P22_CODE = P22.split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');

test('the PHASE 22 block exists', () => {
  assert.ok(P22.length > 1200, 'PHASE 22 "Praticar agora" block should be present');
});

test('a Trilha step renders [Praticar agora] when practice is available', () => {
  assert.match(appJs, /const canPractice = step\.practice_available === true/);
  assert.match(appJs, /step\.action_available === true && step\.action_type === 'PRACTICE'/);
  assert.match(appJs, /class="btn btn-primary path-practice-btn"[\s\S]*?Praticar agora/);
  assert.match(appJs, /data-content="\$\{cc\}"/);
});

test('the disabled "— em breve" fallback is kept only for the other actions', () => {
  assert.match(appJs, /\} else \{\s*\n\s*actionBtn = step\.action_type && step\.action_type !== 'NONE'/);
  assert.match(appJs, /— em breve/);
});

test('the step exposes a "Quantas questões?" selector with 5 / 10 / 15 / 20', () => {
  assert.match(appJs, /path-practice-q">Quantas questões\?/);
  assert.match(appJs, /\[5, 10, 15, 20\]\.map/);
  assert.match(appJs, /path-practice-start"[^>]*>Começar prática/);
  assert.match(appJs, /path-practice-cancel"[^>]*>Cancelar/);
  // 10 is the default selection
  assert.match(appJs, /i === 1 \? ' checked' : ''/);
});

test('starting a practice POSTs /api/v1/student/practice with content_code + question_count', () => {
  assert.match(appJs, /async function launchPractice\(contentCode, contentName, count, msgEl, btnEl\)/);
  assert.match(appJs, /studentRequest\('\/api\/v1\/student\/practice', \{/);
  assert.match(appJs, /method: 'POST'/);
  assert.match(appJs, /JSON\.stringify\(\{ content_code: contentCode, question_count: Number\(count\) \|\| 10 \}\)/);
  // no student_external_id is ever sent - practice is always self
  assert.doesNotMatch(P22_CODE, /student_external_id/);
});

test('a successful create hands the id to the REUSED PHASE 17 player (no second player)', () => {
  assert.match(appJs, /practiceFlow = \{ contentName: data\.content_name \|\| contentName \|\| 'Prática' \}/);
  assert.match(appJs, /switchView\('activities'\);\s*\n\s*startActivityPlayer\(data\.practice_id\);/);
  // it does NOT invent its own question loop / bank
  assert.doesNotMatch(P22_CODE, /\/api\/v1\/practice\/sessions/);
  assert.doesNotMatch(P22_CODE, /next-question|renderPracticeQuestion|loadNextPracticeQuestion/);
});

test('an insufficient bank surfaces the standard message + available/requested counts', () => {
  assert.match(appJs, /Não há questões suficientes disponíveis para esta prática\./);
  assert.match(appJs, /d\.available_questions !== undefined/);
  assert.match(appJs, /disponíveis: \$\{d\.available_questions\}, pedidas: \$\{d\.requested_questions\}/);
});

test('the PHASE 18 result screen is re-framed for practice', () => {
  // PHASE 24 widens this to `|| !!studySessionFlow` (a Momento de Aprendizado
  // PRACTICE block is a practice too); PHASE 25 further widens it to
  // `|| !!materialPracticeFlow` (an exercise associated to a material is a
  // practice too). The original PHASE 22 flag is still checked.
  assert.match(appJs, /const isPractice = !!practiceFlow \|\| !!studySessionFlow \|\| !!materialPracticeFlow;/);
  assert.match(appJs, /isPractice \? 'Prática concluída' : 'Atividade finalizada'/);
  assert.match(appJs, /activity-result-practice-badge/);
  assert.match(appJs, /activity-result-practice-note/);
  assert.match(indexHtml, /id="activity-result-practice-badge"[^>]*\bhidden\b/);
  assert.match(indexHtml, /Este resultado faz parte do seu estudo e não é uma nota escolar\./);
});

test('the result "Voltar" returns to the Trilha for a practice run', () => {
  // PHASE 24 adds a third branch (a Momento de Aprendizado PRACTICE block) to
  // this same label; the PHASE 22 practice case is still exactly this text.
  assert.match(appJs, /isPractice \? 'Voltar para a Trilha' : 'Voltar para Atividades'/);
  assert.match(appJs, /if \(practiceFlow\) \{ practiceFlow = null; switchView\('study-path'\); return; \}/);
  // both the result-close and the mid-run player-exit paths return to the Trilha
  const closeResult = appJs.match(/function closeResultScreen\(\) \{[\s\S]*?\n  \}/)[0];
  const closePlayer = appJs.match(/function closePlayer\(\) \{[\s\S]*?\n  \}/)[0];
  assert.match(closeResult, /switchView\('study-path'\)/);
  assert.match(closePlayer, /switchView\('study-path'\)/);
});

test('a minimal direct "Praticar" entry (disciplina -> conteúdo -> quantidade) exists', () => {
  assert.match(indexHtml, /id="path-direct-practice"[^>]*\bhidden\b/);
  assert.match(indexHtml, /id="pdp-discipline"/);
  assert.match(indexHtml, /id="pdp-content"/);
  assert.match(indexHtml, /id="pdp-count"[\s\S]*?value="5"[\s\S]*?value="10"[\s\S]*?value="15"[\s\S]*?value="20"/);
  assert.match(indexHtml, /id="pdp-start"[^>]*>Começar prática/);
  assert.match(appJs, /async function populateDirectPractice\(\)/);
  assert.match(appJs, /function populatePdpContents\(\)/);
  // it is populated from the student's own Domain Map, not a new endpoint
  assert.match(appJs, /studentRequest\('\/api\/v1\/student\/domain'\)/);
});

test('the direct picker and step buttons are wired', () => {
  assert.match(appJs, /\(function wireStudyPath\(\)/);
  assert.match(appJs, /e\.target\.closest\('\.path-practice-btn'\)/);
  assert.match(appJs, /e\.target\.closest\('\.path-practice-start'\)/);
  assert.match(appJs, /getElementById\('pdp-start'\)/);
  assert.match(appJs, /getElementById\('pdp-discipline'\)[\s\S]*?addEventListener\('change', populatePdpContents\)/);
});

test('the Trilha reload picks up the recalculated path after a practice', () => {
  // loadStudyPathView re-fetches the path and repopulates the direct picker
  assert.match(appJs, /renderStudyPath\(data\);\s*\n\s*populateDirectPractice\(\);/);
});

test('NO gamification / ranking / comparison / grade / second bank in PHASE 22', () => {
  assert.doesNotMatch(P22_CODE,
    /\bnota\b|nota enem|\bTRI\b|MIRT|ranking|medalha|\bpontos\b|competi|comparar com|posição na turma|melhor que|pior que|n[ií]vel \d|gamif|streak|troféu|trofeu/i);
});

test('CSS: the practice panel + direct row reflow on mobile', () => {
  assert.match(stylesCss, /\.path-practice-panel/);
  assert.match(stylesCss, /\.path-practice-counts/);
  assert.match(stylesCss, /\.activity-result-practice-badge/);
  assert.match(stylesCss, /@media \(max-width: 640px\)[\s\S]*\.path-direct-row/);
});
