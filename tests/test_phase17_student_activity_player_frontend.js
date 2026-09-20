/**
 * PHASE 17 - student activity player frontend.
 *
 * Static assertions over the shipped student SPA (node:test + regex), matching
 * the PHASE 13-16 frontend-test style. Verifies the player shell, the numbered
 * navigation, autosave + save-state indicator, recovery, incomplete/complete
 * finalisation and the "Atividade finalizada" screen - and that the player
 * anticipates NONE of the correction/score phases.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const appJs = fs.readFileSync(path.join(WEB, 'app.js'), 'utf8');
const indexHtml = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');
const stylesCss = fs.readFileSync(path.join(WEB, 'styles.css'), 'utf8');

const _p17Start = appJs.indexOf('PHASE 17: student activity PLAYER');
// the PHASE 17 region ends where the PHASE 18 correction/result block begins
const _p17End = appJs.indexOf('PHASE 18: correção determinística');
const PLAYER = appJs.slice(_p17Start, _p17End > _p17Start ? _p17End : appJs.indexOf('// Initial Boot'));
// executable code only - the explanatory `//` comments name the excluded phases
const PLAYER_CODE = PLAYER.split('\n').filter((l) => !l.trim().startsWith('//')).join('\n');

test('the player block exists and is substantial', () => {
  assert.ok(PLAYER.length > 2000, 'PHASE 17 player block should be present');
});

// 1 - rendering of the player
test('index.html carries the player shell: topbar, stage, options, nav, controls', () => {
  assert.match(indexHtml, /id="activity-player"/);
  assert.match(indexHtml, /id="player-statement"/);
  assert.match(indexHtml, /id="player-options"[^>]*role="radiogroup"/);
  assert.match(indexHtml, /id="player-nav"[^>]*aria-label="Navegação entre questões"/);
  assert.match(indexHtml, /id="player-prev"/);
  assert.match(indexHtml, /id="player-next"/);
  assert.match(indexHtml, /id="player-finish"/);
  assert.match(indexHtml, /id="player-save-status"/);
  assert.match(appJs, /function renderPlayer\(\)/);
});

// 2 - numbered navigation
test('a numbered nav is rendered with one dot per question', () => {
  assert.match(appJs, /function renderPlayerNav\(\)/);
  assert.match(appJs, /st\.questions\.map\(\(q, i\) =>/);
  assert.match(appJs, /class="activity-player-dot/);
  assert.match(appJs, /data-index="\$\{i\}"/);
});

// 3 - current question is visually distinct
test('the current question dot has its own state', () => {
  assert.match(appJs, /i === player\.index \? 'is-current'/);
  assert.match(stylesCss, /\.activity-player-dot\.is-current/);
});

// 4 - answered question shows a check
test('an answered question shows a check / answered state', () => {
  assert.match(appJs, /q\.answered \? 'is-answered' : 'is-untouched'/);
  assert.match(appJs, /activity-player-check/);
  assert.match(stylesCss, /\.activity-player-dot\.is-answered/);
});

// 5 - clicking a number jumps to it
test('clicking a nav number jumps directly to that question', () => {
  assert.match(appJs, /player-nav'\)\.addEventListener\('click'/);
  assert.match(appJs, /closest\('\.activity-player-dot'\)/);
  assert.match(appJs, /gotoQuestion\(Number\(dot\.dataset\.index\)\)/);
});

// 6 / 7 - skip + go back freely (no gate)
test('navigation is free: prev/next never require an answer first', () => {
  assert.match(appJs, /function gotoQuestion\(i\)/);
  assert.match(appJs, /player-prev'\)\.addEventListener\('click', \(\) => gotoQuestion\(player\.index - 1\)\)/);
  assert.match(appJs, /player-next'\)\.addEventListener\('click', \(\) => gotoQuestion\(player\.index \+ 1\)\)/);
  // gotoQuestion only bounds-checks the index; it does not inspect answered state
  const fn = appJs.slice(appJs.indexOf('function gotoQuestion(i)'), appJs.indexOf('let positionTimer'));
  assert.doesNotMatch(fn, /answered|selected_option/);
});

// 8 / 9 - select + change an alternative
test('selecting or changing an alternative goes through selectOption + autosave', () => {
  assert.match(appJs, /function selectOption\(key\)/);
  assert.match(appJs, /player-options'\)\.addEventListener\('change'/);
  assert.match(appJs, /q\.selected_option = key;\s*\/\/ optimistic UI/);
  assert.match(appJs, /await autosaveAnswer\(q\.question_version_id, key\)/);
});

// 10 - autosave + save-state indicator
test('autosave PUTs the single answer and drives SAVING/SAVED/SAVE_ERROR', () => {
  assert.match(appJs, /function autosaveAnswer\(qvid, key\)/);
  assert.match(appJs, /method: 'PUT'[\s\S]*attempt\/answers\/\$\{qvid\}/);
  assert.match(appJs, /setPlayerUiState\('SAVING'\)/);
  assert.match(appJs, /setPlayerUiState\('SAVED'\)/);
  assert.match(appJs, /setPlayerUiState\('SAVE_ERROR'\)/);
  // "Salvo" is only shown after a backend OK response
  assert.match(appJs, /if \(!res\.ok\) \{\s*setPlayerUiState\('SAVE_ERROR'\)/);
});

test('all nine required UI states are represented', () => {
  for (const s of ['LOADING', 'READY', 'SAVING', 'SAVED', 'SAVE_ERROR',
                   'IN_PROGRESS', 'COMPLETING', 'COMPLETED', 'NETWORK_ERROR']) {
    assert.match(PLAYER, new RegExp(`\\b${s}\\b`), `missing UI state ${s}`);
  }
});

// 11 - recovery
test('recovery: a fresh GET rebuilds answers + current position; entry offers "Continuar"', () => {
  assert.match(appJs, /function applyPlayerState\(state\)/);
  assert.match(appJs, /state\.current_position/);
  assert.match(appJs, /Continuar atividade/);
  assert.match(appJs, /\/api\/v1\/student\/activities\/\$\{assignmentId\}\/attempt`\);?\s*\n\s*if \(at\.ok\) attemptStatus =/);
  assert.match(appJs, /const resumed = attemptStatus === 'IN_PROGRESS'/);
});

// 12 - save error keeps the local answer + allows retry
test('a save error keeps the local choice and offers retry', () => {
  assert.match(appJs, /player\.pending\.set\(qvid, key\)/);
  assert.match(appJs, /function retryPending\(\)/);
  assert.match(appJs, /setPlayerUiState\('NETWORK_ERROR'\)/);
  // clicking the status when errored retries
  assert.match(appJs, /player-save-status'\)\.addEventListener\('click', \(\) => \{\s*if \(player\.uiState === 'SAVE_ERROR' \|\| player\.uiState === 'NETWORK_ERROR'\) retryPending\(\)/);
});

// 13 - incomplete finalisation
test('incomplete finalisation shows the pending list and does not complete', () => {
  assert.match(appJs, /function attemptComplete\(\)/);
  assert.match(appJs, /if \(res\.status === 409\)/);
  assert.match(appJs, /Atividade incompleta/);
  assert.match(appJs, /Ver questões pendentes/);
  assert.match(appJs, /pending_positions/);
});

// 14 - complete confirmation + backend authority
test('completion asks for confirmation and POSTs to the backend complete endpoint', () => {
  assert.match(appJs, /function confirmComplete\(\)/);
  assert.match(appJs, /Você respondeu todas as questões\. Deseja finalizar a atividade\?/);
  assert.match(appJs, /attempt\/complete`,\s*\n?\s*\{ method: 'POST' \}/);
  // the finish button is disabled until every question is answered (client hint;
  // the backend is still the authority in attemptComplete)
  assert.match(appJs, /finish\.disabled = st\.answered_count < st\.total_questions/);
});

// 15 - "Atividade finalizada"
test('a completed attempt shows "Atividade finalizada" and locks the options', () => {
  assert.match(appJs, /function showCompletedScreen\(\)/);
  assert.match(appJs, /Atividade finalizada/);
  assert.match(appJs, /const locked = st\.status === 'COMPLETED';/);
  assert.match(appJs, /\$\{locked \? 'disabled' : ''\}/);
});

// accessibility + pragmatic anti-copy
test('keyboard navigation and option keys work; focus is visible', () => {
  assert.match(appJs, /e\.key === 'ArrowLeft'/);
  assert.match(appJs, /e\.key === 'ArrowRight'/);
  assert.match(appJs, /\/\^\[A-E\]\$\/\.test\(up\)/);
  assert.match(stylesCss, /\.activity-player-dot:focus-visible/);
});

test('pragmatic anti-casual-copy is scoped to the question stage and never traps nav keys', () => {
  assert.match(appJs, /stage\.addEventListener\('contextmenu', \(e\) => e\.preventDefault\(\)\)/);
  assert.match(appJs, /stage\.addEventListener\('copy'/);
  assert.match(stylesCss, /\.activity-player-stage \{[^}]*user-select: none/);
  // the comment is explicit that screenshots are NOT blocked
  assert.match(appJs, /does NOT block OS screenshots/i);
});

test('the player anticipates none of the correction / score phases', () => {
  assert.doesNotMatch(PLAYER_CODE, /\bnota\b|percentual|acertos|corre[çc][ãa]o|gabarito|ranking|\bTRI\b|domain[- ]?map|learning[- ]?path|desempenho|feedback de/i);
  // it never reads a correctness field from the payload
  assert.doesNotMatch(PLAYER_CODE, /is_correct|correct_option|is_valid_option|answer_key(?!_visible)/);
});

// bug found by live-testing (2026-09-20): persistPosition() debounces the
// PUT .../attempt/position by 400ms in a MODULE-level `positionTimer`, and
// reads the CURRENT `player.assignmentId` only when the timer fires - not
// when it was scheduled. startActivityPlayer() resets every other player.*
// field "never carry over state from a previous activity", but left this
// timer running: if the student navigates to a question and then finishes
// (or exits) within that 400ms window, the stale timeout later fires against
// whatever `player.assignmentId` is current at that moment - which, if the
// student has since opened a DIFFERENT activity, silently persists a leftover
// question index onto that unrelated attempt's current_position.
test('leaving the player cancels the pending position-persist debounce timer', () => {
  const closePlayerFn = appJs.slice(
    appJs.indexOf('function closePlayer()'),
    appJs.indexOf('function applyPlayerState'));
  assert.match(
    closePlayerFn, /clearTimeout\(positionTimer\)/,
    'closePlayer() must cancel positionTimer - otherwise a debounced position PUT ' +
    'scheduled just before exit can fire later against a different activity',
  );
});

test('starting a player run cancels any leftover position-persist timer from a previous activity', () => {
  const startFn = appJs.slice(
    appJs.indexOf('async function startActivityPlayer'),
    appJs.indexOf('function openPlayerShell'));
  assert.match(
    startFn, /clearTimeout\(positionTimer\)/,
    'startActivityPlayer() must cancel any pending positionTimer BEFORE reassigning ' +
    'player.assignmentId, so a stale debounced write from the previous activity never ' +
    'lands on the new one',
  );
});
