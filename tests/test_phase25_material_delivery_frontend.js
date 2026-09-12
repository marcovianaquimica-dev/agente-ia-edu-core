/**
 * PHASE 25 - Material Delivery & Study Integration frontend.
 *
 * Static assertions (node:test + regex) over the student SPA's Material
 * Player (opened contextually from the Trilha's "Estudar agora" and from a
 * Study Session STUDY block - never a top-nav view), plus the Coordination
 * portal's read-only "Materiais" view. Confirms: no second player/practice
 * engine, no AI, no invented block types, prior-context preservation on
 * "Voltar", and 320-360px reflow.
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

const MR = appJs.slice(appJs.indexOf('PHASE 25 — Material Player'), appJs.indexOf('// Initial Boot'));

// ---------------------------------------------------------------- student --

test('the Material Player view exists and is NOT a top-nav item', () => {
  assert.ok(MR.length > 1500, 'PHASE 25 material reader block should be substantial');
  assert.match(indexHtml, /id="view-material-reader"/);
  assert.doesNotMatch(indexHtml, /data-view="material-reader"/);
});

test('opening reads material + sections + progress in parallel, never a fabricated one', () => {
  assert.match(MR, /Promise\.all\(\[/);
  assert.match(MR, /studentRequest\(`\/api\/v1\/student\/materials\/\$\{materialId\}`\)/);
  assert.match(MR, /studentRequest\(`\/api\/v1\/student\/materials\/\$\{materialId\}\/sections`\)/);
  assert.match(MR, /studentRequest\(`\/api\/v1\/student\/materials\/\$\{materialId\}\/progress`\)/);
});

test('a 403 shows an honest "not available" message, never invented content', () => {
  assert.match(MR, /matRes\.status === 403/);
  assert.match(MR, /não está disponível para você/);
});

test('resume: an existing progress row picks up the saved section, never restarting', () => {
  assert.match(MR, /progress\.started && progress\.current_section_id/);
  assert.match(MR, /mr\.sections\.findIndex/);
});

test('only the block types the PHASE 23 model actually supports are rendered - none invented', () => {
  assert.match(MR, /DEFINITION: 'Definição'/);
  assert.match(MR, /FORMULA: 'Fórmula'/);
  assert.match(MR, /EXAMPLE: 'Exemplo'/);
  assert.match(MR, /SOLVED_EXAMPLE: 'Exemplo resolvido'/);
  assert.match(MR, /TABLE: 'Tabela'/);
  assert.match(MR, /IMAGE: 'Imagem'/);
  assert.match(MR, /CALLOUT: 'Destaque'/);
  assert.match(MR, /EXERCISE_REFERENCE: 'Exercício'/);
  assert.match(MR, /SUMMARY: 'Resumo'/);
  assert.match(MR, /REVIEW: 'Revisão'/);
});

test('section navigation: prev/next + a numbered dot nav, progress label "Seção X de Y"', () => {
  assert.match(MR, /Seção \$\{idx \+ 1\} de \$\{total\}/);
  assert.match(indexHtml, /id="mr-prev"/);
  assert.match(indexHtml, /id="mr-next"/);
  assert.match(indexHtml, /id="mr-section-nav"/);
});

test('every navigation step saves progress (PUT), so resume is never far off', () => {
  assert.match(MR, /mrSaveProgress\(s\.section_id, idx === total - 1\)/);
  assert.match(MR, /method: 'PUT'/);
  assert.match(MR, /\/progress`, \{/);
});

test('"Pratique o que você estudou" reuses the EXISTING launchPractice()/PHASE 17 player - no second player', () => {
  assert.match(MR, /materialPracticeFlow = \{ materialId: mr\.materialId \}/);
  assert.match(MR, /launchPractice\(cc, mr\.material\.title, 10,/);
  assert.doesNotMatch(MR, /new.*Player\(/);
});

test('"Voltar" preserves the calling context (Trilha vs Momento de Aprendizado)', () => {
  assert.match(MR, /materialReaderFlow = opts \|\| \{\}/);
  assert.match(MR, /flow\.returnTo === 'study-session' \? 'study-session' : 'study-path'/);
});

test('a finished material-linked practice returns to the SAME material, not the Trilha', () => {
  assert.match(appJs, /if \(materialPracticeFlow\) \{[\s\S]{0,220}openMaterialReader\(flow\.materialId, materialReaderFlow \|\| \{\}\)/);
});

test('the Trilha step gets an honest "Estudar agora" only when material_available is true - no fabrication', () => {
  assert.match(appJs, /step\.material_available && step\.material_id/);
  assert.match(appJs, /📘 Estudar agora/);
  assert.match(appJs, /openMaterialReader\(studyBtn\.dataset\.materialId, \{ returnTo: 'study-path' \}\)/);
});

test('the Study Session STUDY block gets "Estudar agora" only when material_id is present, and it does not replace "Concluir bloco"', () => {
  assert.match(appJs, /now\.block_type === 'STUDY' && now\.material_id/);
  assert.match(appJs, /openMaterialReader\(now\.material_id, \{ returnTo: 'study-session' \}\)/);
  assert.match(appJs, /ss-block-action.*Concluir bloco|Concluir bloco/);
});

test('NO second player, NO second correction engine, NO AI anywhere in the Material Player block', () => {
  assert.doesNotMatch(MR, /gpt|openai|classifica.*automatica/i);
  assert.doesNotMatch(MR, /correctAnswer\s*=/);
});

test('CSS: the Material Player reflows at 360px with no fixed overflow-causing widths', () => {
  assert.match(stylesCss, /@media \(max-width: 360px\)[\s\S]{0,200}\.mr-section-content/);
  assert.match(stylesCss, /\.mr-block-image \{[^}]*max-width: 100%/);
});

// ------------------------------------------------------------ coordination --

test('coordination gets a read-only Materiais view reusing the EXISTING GET /api/v1/catalog/materials', () => {
  assert.match(coordHtml, /data-view="materials"[\s\S]*?Materiais/);
  assert.match(coordHtml, /id="view-materials"/);
  assert.match(coordJs, /fetch\('\/api\/v1\/catalog\/materials', \{/);
});

test('coordination Materiais reuses the existing auth header pattern - no parallel authorization', () => {
  assert.match(coordJs, /loadCoordinationMaterials[\s\S]{0,400}Authorization.*Bearer \$\{state\.coordinatorId\}/);
});

test('coordination Materiais never creates/edits a material (read-only this phase)', () => {
  const block = coordJs.slice(coordJs.indexOf('function loadCoordinationMaterials'),
                              coordJs.indexOf('// 8. ACTION PLAN'));
  assert.doesNotMatch(block, /method:\s*'(POST|PUT|PATCH|DELETE)'/);
});
