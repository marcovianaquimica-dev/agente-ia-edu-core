/**
 * PHASE 24 - "Momento de Aprendizado" (Study Session) frontend.
 *
 * Static assertions over the student SPA and the Coordination portal
 * (node:test + regex). Verifies: the student time-prompt (presets + custom +
 * "sem tempo"), the plan preview, the running session (now/next, breaks,
 * PRACTICE launching the REUSED PHASE 17 player, resume), completion; and the
 * coordination "Novo Momento de Aprendizado" form (destinatários, data,
 * horário, conteúdos opcionais, intervalos, resumo, publicar) + its list.
 * Confirms no second player/practice engine is created, no AI, and 320-768px
 * reflow.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const indexHtml = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
const appJs = fs.readFileSync(path.join(WEB, 'app.js'), 'utf8');
const stylesCss = fs.readFileSync(path.join(WEB, 'styles.css'), 'utf8');
const coordHtml = fs.readFileSync(path.join(WEB, 'coordination.html'), 'utf8');
const coordJs = fs.readFileSync(path.join(WEB, 'coordination.js'), 'utf8');
const coordCss = fs.readFileSync(path.join(WEB, 'coordination.css'), 'utf8');

const SS = appJs.slice(appJs.indexOf('PHASE 24 — "Momento de Aprendizado"'), appJs.indexOf('// Initial Boot'));
const CS = coordJs.slice(coordJs.indexOf('PHASE 24 — "Momento de Aprendizado" scheduling'),
                         coordJs.indexOf('// Initial Load'));

// ---------------------------------------------------------------- student --

test('the student SS block exists and the nav item + view are present', () => {
  assert.ok(SS.length > 2000, 'PHASE 24 student block should be substantial');
  assert.match(indexHtml, /data-view="study-session"[\s\S]*?Momento de Aprendizado/);
  assert.match(indexHtml, /id="view-study-session"/);
  assert.match(appJs, /'study-session':\s*\{ title: 'Momento de Aprendizado'/);
  assert.match(appJs, /viewName === 'study-session'\) loadStudySessionView\(\)/);
});

test('it reads GET /api/v1/student/study-session/today, never inventing a session id', () => {
  assert.match(SS, /studentRequest\('\/api\/v1\/student\/study-session\/today'\)/);
  assert.doesNotMatch(SS, /\/api\/v1\/student\/study-session\/undefined/);
});

test('the time prompt offers the spec presets + custom + "sem tempo"', () => {
  assert.match(indexHtml, /data-min="15">15 min/);
  assert.match(indexHtml, /data-min="30">30 min/);
  assert.match(indexHtml, /data-min="45">45 min/);
  assert.match(indexHtml, /data-min="60">1 h/);
  assert.match(indexHtml, /data-min="90">1h30/);
  assert.match(indexHtml, /data-min="120">2 h/);
  assert.match(indexHtml, /data-min="150">2h30/);
  assert.match(indexHtml, /data-min="180">3 h/);
  assert.match(indexHtml, /id="ss-time-other"[^>]*>Outro/);
  assert.match(indexHtml, /id="ss-no-timer"[^>]*>Não quero definir um tempo/);
  assert.match(indexHtml, /id="ss-time-input"[^>]*min="5"[^>]*max="600"/);
});

test('a preset creates a TIMED session; "sem tempo" creates an UNTIMED one; both POST /study-session', () => {
  assert.match(SS, /startBtn\.dataset\.noTimer\s*\?\s*\{ no_timer: true \}\s*:\s*\{ available_minutes: Number\(startBtn\.dataset\.minutes\) \}/);
  assert.match(SS, /studentRequest\('\/api\/v1\/student\/study-session', \{/);
  assert.match(SS, /method: 'POST'/);
});

test('the plan preview shows blocks, minutes, and explains action_note when a block is unavailable', () => {
  assert.match(SS, /function renderSsPreview\(/);
  assert.match(SS, /ssBlockPreviewRow/);
  assert.match(SS, /b\.action_available/);
  assert.match(SS, /b\.action_note/);
  assert.match(SS, /s\.effective_study_minutes/);
  assert.match(SS, /s\.break_minutes/);
});

test('the school-scheduled announcement never shows the student a time prompt', () => {
  assert.match(SS, /Seu momento de aprendizado de hoje está programado das/);
  assert.match(SS, /source === 'SCHOOL_DEFINED' && s\.status === 'SCHEDULED'/);
});

test('running session shows "now" / "next", and a BREAK block is framed as a pause, never as study', () => {
  assert.match(SS, /function renderSsRunning\(/);
  assert.match(SS, /Hora de uma pausa\./);
  assert.match(SS, /Isso não conta como tempo de estudo\./);
  assert.match(SS, /Depois:/);
});

test('a PRACTICE block starts via /blocks/{index}/start and launches the REUSED PHASE 17 player', () => {
  assert.match(SS, /\/blocks\/\$\{block\.index\}\/start/);
  assert.match(SS, /studySessionFlow = \{ sessionId: ss\.data\.id, blockIndex: block\.index \}/);
  assert.match(SS, /startActivityPlayer\(updated\.assignment_id\)/);
  // no second player / no second practice engine
  assert.doesNotMatch(SS, /new ActivityPlayer|createPlayer\(|\/api\/v1\/practice\/sessions/);
});

test('finishing the PHASE 17/18 flow for a study-session PRACTICE block completes that block and returns here', () => {
  const closeResult = appJs.match(/function closeResultScreen\(\) \{[\s\S]*?\n  \}/)[0];
  const closePlayer = appJs.match(/function closePlayer\(\) \{[\s\S]*?\n  \}/)[0];
  assert.match(closeResult, /studySessionFlow/);
  assert.match(closeResult, /completeStudySessionBlock\(flow\.sessionId, flow\.blockIndex\)/);
  assert.match(closeResult, /switchView\('study-session'\)/);
  assert.match(closePlayer, /studySessionFlow/);
});

test('STUDY/REVIEW blocks are self-paced ("Concluir bloco") and never fabricate an action when unavailable', () => {
  assert.match(SS, /Concluir bloco/);
  assert.match(SS, /Pular bloco/);
  assert.match(SS, /now\.action_available/);
});

test('the block-complete call is idempotent-shaped (POST with an explicit skipped flag)', () => {
  assert.match(SS, /async function completeStudySessionBlock\(sessionId, index, skipped\)/);
  assert.match(SS, /\/blocks\/\$\{index\}\/complete/);
  assert.match(SS, /body: JSON\.stringify\(\{ skipped: !!skipped \}\)/);
});

test('resume: loading the view again re-fetches /today and renders by session status (no local-only state)', () => {
  assert.match(SS, /async function loadStudySessionView\(\)/);
  assert.match(SS, /renderSsForStatus/);
  assert.match(SS, /s\.status === 'IN_PROGRESS'/);
});

test('completion screen exists and never claims a grade', () => {
  assert.match(indexHtml, /Momento de aprendizado concluído/);
  assert.match(SS, /function renderSsDone\(/);
  assert.doesNotMatch(SS, /\bnota\b|\bTRI\b|ranking|gamif/i);
});

test('NO gamification / ranking / grade in the student study-session block', () => {
  assert.doesNotMatch(SS,
    /\bnota\b|nota enem|\bTRI\b|MIRT|ranking|medalha|\bpontos\b|competi|comparar com|posição na turma|melhor que|pior que|gamif|streak|troféu|trofeu/i);
});

test('CSS: the time grid and block cards reflow under 768px', () => {
  assert.match(stylesCss, /\.ss-time-grid/);
  assert.match(stylesCss, /\.ss-block-card/);
  assert.match(stylesCss, /@media \(max-width: 640px\)[\s\S]*\.ss-time-grid/);
});

// ------------------------------------------------------------ coordination --

test('the coordination portal now loads its own JS/CSS (the pre-existing relative-asset bug is fixed)', () => {
  assert.match(coordHtml, /<base href="\/coordination\/assets\/">/);
});

test('coordination gets a "Momento de Aprendizado" nav item + view, distinct from other views', () => {
  assert.match(coordHtml, /data-view="study-sessions"[\s\S]*?Momento de Aprendizado/);
  assert.match(coordHtml, /id="view-study-sessions"/);
  assert.match(coordJs, /'study-sessions':\s*\{ title: 'Momento de Aprendizado'/);
  assert.match(coordJs, /state\.currentView === 'study-sessions'\) initStudySessionsView\(\)/);
});

test('the form has destinatários / data / horário / conteúdos opcionais / intervalos / resumo / publicar', () => {
  assert.match(coordHtml, /id="cs-target-type"[\s\S]*?Turma[\s\S]*?Aluno/);
  assert.match(coordHtml, /id="cs-date"[^>]*type="date"/);
  assert.match(coordHtml, /id="cs-start"[^>]*type="time"/);
  assert.match(coordHtml, /id="cs-end"[^>]*type="time"/);
  assert.match(coordHtml, /opcional — em branco, a plataforma decide/);
  assert.match(coordHtml, /id="cs-add-content"[^>]*>\+ Adicionar conteúdo/);
  assert.match(coordHtml, /id="cs-add-break"[^>]*>\+ Adicionar intervalo/);
  assert.match(coordHtml, /id="cs-summary"/);
  assert.match(coordHtml, /id="cs-publish"[^>]*>Publicar/);
});

test('the summary computes total / intervalos / tempo efetivo client-side for immediate feedback, "efetivo" is never the raw window', () => {
  assert.match(CS, /function csUpdateSummary\(/);
  assert.match(CS, /Tempo total:/);
  assert.match(CS, /Intervalos:/);
  assert.match(CS, /Tempo efetivo:/);
  assert.match(CS, /total - breakMin/);
});

test('publishing POSTs /api/v1/coordination/study-sessions with the scope-authorised fields, then refreshes the list', () => {
  assert.match(CS, /fetch\('\/api\/v1\/coordination\/study-sessions', \{/);
  assert.match(CS, /method: 'POST'/);
  assert.match(CS, /school_id: state\.schoolId, target_type: targetType, target_id: targetId/);
  assert.match(CS, /content_codes: cs\.contents\.length \? cs\.contents : null/);
  assert.match(CS, /breaks: cs\.breaks\.length \? cs\.breaks : null/);
  assert.match(CS, /loadStudySessionsList\(\)/);
});

test('the list reads GET /api/v1/coordination/study-sessions scoped by school_id + date', () => {
  assert.match(CS, /\/api\/v1\/coordination\/study-sessions\?school_id=\$\{state\.schoolId\}&session_date=\$\{date\}/);
});

test('it reuses the existing coordination auth header pattern - no parallel authorization', () => {
  assert.match(CS, /Authorization': `Bearer \$\{state\.coordinatorId\}`/);
  assert.doesNotMatch(CS, /new Authoriz|customAuth|bypassScope/i);
});

test('NO AI / gamification anywhere in the coordination scheduling block', () => {
  assert.doesNotMatch(CS, /openai|gpt-|AsyncOpenAI|ranking|gamif|\bnota\b/i);
});

test('CSS: the coordination form + time row reflow under 768px', () => {
  assert.match(coordCss, /\.cs-form/);
  assert.match(coordCss, /\.cs-time-row/);
  assert.match(coordCss, /@media \(max-width: 768px\)[\s\S]*\.cs-time-row/);
});
