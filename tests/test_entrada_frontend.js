// Frontend contract checks for the new student entry point
// (src/agente_ia_edu/web/entrada.js) against the /api/v1/student/modules
// contract documented in docs/superpowers/specs/2026-09-28-entrada-dois-ambientes-design.md
// and the backend route being built in parallel by another session
// (src/agente_ia_edu/api/routes/student.py - not touched here).
//
// entrada.js has no module.exports (it runs entirely inside a
// DOMContentLoaded listener, like admin.js/app.js), so - matching the
// established pattern in this suite (see test_admin_frontend.js) - the
// contract shape (fetch URL, headers, storage key) is asserted against the
// raw source text, while the pure routing decision (0/1/2 modules enabled ->
// error/redirect/cards) is exercised directly by copying the logic verbatim
// in spirit from entrada.js and running it against mocked
// /api/v1/student/modules response bodies.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');

const js = fs.readFileSync('src/agente_ia_edu/web/entrada.js', 'utf8');

test('confirming identity writes sessionStorage.studentAccessToken before calling the modules endpoint', () => {
  // Same key app.js (studentHeaders()) and essay.js already read from -
  // entrada.js must not invent a new identity mechanism.
  assert.match(js, /sessionStorage\.setItem\('studentAccessToken', studentId\)/);
});

test('calls GET /api/v1/student/modules with a Bearer header carrying the student id, per the documented contract', () => {
  assert.match(js, /fetch\('\/api\/v1\/student\/modules', \{\s*headers: \{ 'Authorization': `Bearer \$\{studentId\}` \},?\s*\}\)/);
});

test('never fabricates a module as enabled - only a strict === true from the real response counts', () => {
  // Regression guard: the plan/spec explicitly forbid assuming a module is
  // enabled to unblock the screen. filter(...) must check the real backend
  // value strictly, not a truthy/default fallback.
  assert.match(js, /modules\[key\] === true/);
});

test('network failure and non-2xx responses show an honest error instead of defaulting to a module', () => {
  assert.match(js, /catch \(e\) \{\s*setLoading\(false\);\s*showError\(/);
  assert.match(js, /if \(!resp\.ok\) \{/);
});

test('module -> destination map matches app.js (/student) and the new /redacao shell', () => {
  assert.match(js, /AGENTE_IA_EDU: '\/student'/);
  assert.match(js, /REDACAO_IA: '\/redacao'/);
});

test('routing decision: 0/1/2 enabled modules resolves to error / direct redirect / cards, for all 4 combinations', () => {
  // Copied verbatim in spirit from entrada.js's handleConfirm()/cardsBox
  // click handler - the part that decides where the student goes based on
  // the modules response, isolated from the DOM/fetch plumbing so it can be
  // exercised directly against mocked GET /api/v1/student/modules bodies.
  const ENV_DESTINATIONS = { AGENTE_IA_EDU: '/student', REDACAO_IA: '/redacao' };

  function decide(modulesResponse) {
    const enabled = Object.keys(ENV_DESTINATIONS).filter((key) => modulesResponse[key] === true);
    if (enabled.length === 0) return { outcome: 'error' };
    if (enabled.length === 1) return { outcome: 'redirect', to: ENV_DESTINATIONS[enabled[0]] };
    return { outcome: 'cards' };
  }

  // 0 modules enabled (school with no SchoolModule rows, or both explicitly off).
  assert.deepEqual(decide({ AGENTE_IA_EDU: false, REDACAO_IA: false }), { outcome: 'error' });

  // only AGENTE_IA_EDU -> straight to /student, no card screen.
  assert.deepEqual(decide({ AGENTE_IA_EDU: true, REDACAO_IA: false }), { outcome: 'redirect', to: '/student' });

  // only REDACAO_IA -> straight to /redacao, no card screen.
  assert.deepEqual(decide({ AGENTE_IA_EDU: false, REDACAO_IA: true }), { outcome: 'redirect', to: '/redacao' });

  // both enabled -> show the 2 cards, let the student choose.
  assert.deepEqual(decide({ AGENTE_IA_EDU: true, REDACAO_IA: true }), { outcome: 'cards' });
});

test('card click handler maps data-env="agente"/"redacao" to the same AGENTE_IA_EDU/REDACAO_IA destinations', () => {
  assert.match(js, /card\.dataset\.env === 'agente' \? 'AGENTE_IA_EDU' : 'REDACAO_IA'/);
});

test('empty identity input is rejected locally without ever calling the backend', () => {
  assert.match(js, /if \(!studentId\) \{\s*showError\(/);
});
