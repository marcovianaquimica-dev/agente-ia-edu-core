/**
 * PHASE 23 - "Meus Materiais" teacher frontend.
 *
 * Static assertions over the teacher SPA (node:test + regex). Verifies the new
 * nav item + view, the minimal "Novo material" form (título / descrição /
 * disciplina / conteúdo / tipo / origem / salvar rascunho), the list columns
 * (status / versão / disciplina-conteúdo / seções / questões), the detail panel
 * with "Conteúdos relacionados" + "Questões relacionadas" + "Adicionar questão",
 * that it REUSES /api/v1/catalog/materials* and /api/v1/question-bank/questions
 * (no second question bank), that it never copies a question, that the legacy
 * "Listas e Avaliações" view is left intact, no AI, and 320px reflow.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const html = fs.readFileSync(path.join(WEB, 'teacher.html'), 'utf8');
const js = fs.readFileSync(path.join(WEB, 'teacher.js'), 'utf8');
const css = fs.readFileSync(path.join(WEB, 'teacher.css'), 'utf8');

const P23 = js.slice(js.indexOf('PHASE 23'), js.indexOf('// Initial Load'));

test('nav item + dedicated view exist and are distinct from the legacy "Listas"', () => {
  assert.match(html, /data-view="theory-materials"[\s\S]*?Meus Materiais/);
  assert.match(html, /id="view-theory-materials"/);
  assert.match(html, /data-view="materials"[\s\S]*?Listas e Avaliações/); // legacy kept
  assert.match(js, /'theory-materials':\s*\{ title: 'Meus Materiais'/);
  assert.match(js, /state\.currentView === 'theory-materials'\) loadTheoryMaterials\(\)/);
});

test('the "Novo material" form has título / descrição / disciplina / conteúdo / tipo / origem / salvar rascunho', () => {
  assert.match(html, /id="tm-title"/);
  assert.match(html, /id="tm-description"/);
  assert.match(html, /id="tm-discipline"/);
  assert.match(html, /id="tm-content"/);
  assert.match(html, /id="tm-kind"[\s\S]*?Apostila/);
  assert.match(html, /id="tm-source"[\s\S]*?Professor/);
  assert.match(html, /id="tm-save-btn"[^>]*>Salvar como rascunho/);
  assert.match(html, /id="tm-visibility"/);
});

test('the list shows status / versão / disciplina-conteúdo / seções / questões', () => {
  const t = P23.match(/<thead>[\s\S]*?<\/thead>/)[0];
  for (const col of ['Título', 'Status', 'Versão', 'Disciplina / Conteúdo', 'Seções', 'Questões']) {
    assert.ok(t.includes(col), `list column ${col} missing`);
  }
  assert.match(js, /m\.latest_version_status/);
  assert.match(js, /m\.section_count/);
  assert.match(js, /m\.question_count/);
});

test('the detail panel has "Conteúdos relacionados", "Questões relacionadas" and "Adicionar questão"', () => {
  assert.match(html, /Conteúdos relacionados/);
  assert.match(html, /Questões relacionadas/);
  assert.match(html, /id="tm-q-search-btn"[^>]*>Buscar questões/);
  assert.match(html, /id="tm-sections"/);
  assert.match(html, /id="tm-questions"/);
  assert.match(js, /function openTheoryMaterial\(id\)/);
  assert.match(js, /function renderTheoryQuestions\(/);
});

test('it REUSES the catalog material API and the question bank - no second bank', () => {
  assert.match(P23, /fetch\('\/api\/v1\/catalog\/materials'/);
  assert.match(P23, /\/api\/v1\/catalog\/materials\/\$\{[a-zA-Z.]+\}\/sections/);
  assert.match(P23, /\/api\/v1\/catalog\/materials\/\$\{[a-zA-Z.]+\}\/questions/);
  assert.match(P23, /\/api\/v1\/question-bank\/questions/);
  // no parallel practice/question storage endpoints
  assert.doesNotMatch(P23, /\/api\/v1\/practice\/sessions|\/api\/v1\/teacher\/materials/);
});

test('adding a question links by question_version_id and never copies it', () => {
  assert.match(P23, /body: JSON\.stringify\(\{ question_version_id: qv, relation_type: 'EXERCISE' \}\)/);
  assert.match(P23, /\/questions\/\$\{qv\}`, \{\s*method: 'DELETE'/);
  assert.match(P23, /já está vinculada/); // 409 duplicate handled
});

test('create posts the minimal draft payload and shows it as a rascunho', () => {
  assert.match(P23, /method: 'POST', headers: tmHeaders\(\), body: JSON\.stringify\(payload\)/);
  assert.match(P23, /material_kind: document\.getElementById\('tm-kind'\)\.value/);
  assert.match(P23, /authoring_source: document\.getElementById\('tm-source'\)\.value/);
  assert.match(P23, /primary_content_node_id: contentSel\.value \|\| null/);
});

test('curriculum picker is disciplina -> conteúdo, resolved from catalog_nodes (no invented codes)', () => {
  assert.match(P23, /\/api\/v1\/catalog\/disciplines/);
  assert.match(P23, /\/api\/v1\/catalog\/nodes\/\$\{disciplineId\}\/tree/);
  assert.match(P23, /node_type === 'CONTENT' \|\| n\.node_type === 'SUBCONTENT'/);
});

test('NO AI anywhere in the PHASE 23 frontend block', () => {
  assert.doesNotMatch(P23, /openai|gpt-|AsyncOpenAI|gerar com ia|resumo autom[aá]tico|classifica[cç][aã]o autom/i);
});

test('CSS: the Meus Materiais layout reflows on mobile (320-768px)', () => {
  assert.match(css, /\.tm-form-row/);
  assert.match(css, /\.tm-detail-grid/);
  assert.match(css, /@media \(max-width: 768px\)[\s\S]*\.tm-detail-grid \{ grid-template-columns: 1fr; \}/);
});
