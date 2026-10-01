// Fallback de 3 níveis da tabela "o que você já faz bem e onde pode avançar"
// (spec §4). renderCompetencyChecklist é o ÚNICO ponto de renderização dessa
// tabela no frontend - aluno, professor e evolução chamam a mesma função -
// então é aqui que os três níveis são testados. essay-report.js exporta via
// module.exports, então o teste roda contra o comportamento real, não contra
// o texto-fonte.

const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const EssayReport = require('../src/agente_ia_edu/web/essay-report.js');

function esc(value) {
  return String(value ?? '').replace(/[&<>'"]/g, (c) => ({
    '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
  })[c]);
}

const STRUCTURED = {
  c2_tipologia_textual: 'Texto dissertativo-argumentativo completo.',
  c2_tema: 'Desenvolve o tema proposto.',
  c2_repertorio_sociocultural: 'Não foi identificado repertório sociocultural no texto.',
  c2_orientacao_melhoria: 'Traga um repertório pertinente ao tema.',
  c3_projeto_argumentativo: 'A tese é retomada na conclusão.',
  c3_fatos_informacoes_opinioes: 'Usa dados do IBGE no segundo parágrafo.',
  c3_autoria: 'Há voz autoral no terceiro parágrafo.',
  c3_orientacao_melhoria: 'Desenvolva o segundo argumento com um exemplo.',
};

function rationale(code) {
  return {
    competency_code: code,
    summary: `resumo ${code}`,
    strengths: `forças ${code}`,
    growth_area: `avançar ${code}`,
    signal_keys: [],
  };
}

const RATIONALES_V5 = ['C1', 'C4', 'C5'].map(rationale);
const RATIONALES_V4 = ['C1', 'C2', 'C3', 'C4', 'C5'].map(rationale);

// --- nível 1: campos estruturados ---

test('nível 1: C2 renderiza os três aspectos diagnósticos rotulados, na ordem da spec, com "Como melhorar" em coluna própria', () => {
  const html = EssayReport.renderCompetencyChecklist(RATIONALES_V5, [], esc, STRUCTURED);
  assert.match(html, /Tipologia textual:/);
  assert.match(html, /Tema:/);
  assert.match(html, /Repertório sociocultural:/);
  // "Como melhorar" não é mais um rótulo dentro da lista - o próprio
  // conteúdo (não o rótulo) vai na célula "Onde pode avançar".
  assert.doesNotMatch(html, /Como melhorar:/);
  const ordem = ['Tipologia textual', 'Tema:', 'Repertório sociocultural'];
  let cursor = 0;
  ordem.forEach((label) => {
    const at = html.indexOf(label, cursor);
    assert.ok(at > -1, `rótulo ausente ou fora de ordem: ${label}`);
    cursor = at;
  });
  assert.match(html, /Não foi identificado repertório sociocultural no texto\./);
  assert.match(
    html,
    /data-label="Onde pode avançar">Traga um repertório pertinente ao tema\.</,
  );
});

test('nível 1: C3 renderiza os três aspectos diagnósticos rotulados, na ordem da spec, com "Como melhorar" em coluna própria', () => {
  const html = EssayReport.renderCompetencyChecklist(RATIONALES_V5, [], esc, STRUCTURED);
  const ordem = ['Projeto argumentativo', 'Informações, fatos e opiniões', 'Autoria:'];
  let cursor = html.indexOf('C3 —');
  assert.ok(cursor > -1, 'linha de C3 ausente');
  ordem.forEach((label) => {
    const at = html.indexOf(label, cursor);
    assert.ok(at > -1, `rótulo ausente ou fora de ordem: ${label}`);
    cursor = at;
  });
  const c3RowHtml = html.slice(cursor, html.indexOf('</tr>', cursor));
  assert.doesNotMatch(c3RowHtml, /Como melhorar:/);
  assert.match(html, /Usa dados do IBGE no segundo parágrafo\./);
  assert.match(
    c3RowHtml,
    /data-label="Onde pode avançar">Desenvolva o segundo argumento com um exemplo\.</,
  );
});

test('nível 1: C2 e C3 aparecem mesmo sem nenhum rationale para eles', () => {
  // Contrato v5: rationales cobre só C1/C4/C5. Sem isso a linha sumiria.
  const html = EssayReport.renderCompetencyChecklist(RATIONALES_V5, [], esc, STRUCTURED);
  assert.match(html, /C2 — Tipologia, tema e repertório/);
  assert.match(html, /C3 — Projeto argumentativo e autoria/);
});

test('nível 1 usa os rótulos novos de C2 e C3', () => {
  const html = EssayReport.renderCompetencyChecklist(RATIONALES_V5, [], esc, STRUCTURED);
  assert.doesNotMatch(html, /Compreensão do tema/);
  assert.doesNotMatch(html, /C3 — Argumentação/);
});

test('nível 1 escapa HTML vindo do conteúdo', () => {
  const html = EssayReport.renderCompetencyChecklist(
    RATIONALES_V5, [], esc,
    { ...STRUCTURED, c2_tema: '<script>alert(1)</script>' },
  );
  assert.doesNotMatch(html, /<script>/);
  assert.match(html, /&lt;script&gt;/);
});

// --- nível 2: strengths/growth_area (correção v4) ---

test('nível 2: sem campos estruturados, C2 cai no par forças/avançar', () => {
  const html = EssayReport.renderCompetencyChecklist(RATIONALES_V4, [], esc, null);
  assert.match(html, /forças C2/);
  assert.match(html, /avançar C2/);
  assert.doesNotMatch(html, /Tipologia textual:/);
});

test('nível 2: estruturado PARCIAL cai no nível 2, nunca renderiza meia tabela', () => {
  const parcial = { ...STRUCTURED };
  delete parcial.c2_orientacao_melhoria;
  const html = EssayReport.renderCompetencyChecklist(RATIONALES_V4, [], esc, parcial);
  assert.match(html, /forças C2/);
  assert.doesNotMatch(html, /Tipologia textual:/);
  // C3 continua completo, então segue no nível 1
  assert.match(html, /Projeto argumentativo:/);
});

test('nível 2: string vazia num campo estruturado também derruba para o nível 2', () => {
  const html = EssayReport.renderCompetencyChecklist(
    RATIONALES_V4, [], esc, { ...STRUCTURED, c2_tema: '   ' },
  );
  assert.match(html, /forças C2/);
  assert.doesNotMatch(html, /Tipologia textual:/);
});

// --- nível 3: summary ---

test('nível 3: sem estruturado e sem split, C2 cai no summary em célula única', () => {
  const soSummary = [{ competency_code: 'C2', summary: 'resumo antigo de C2' }];
  const html = EssayReport.renderCompetencyChecklist(soSummary, [], esc, null);
  assert.match(html, /colspan="2">resumo antigo de C2/);
});

// --- C1/C4/C5 nunca mudam ---

test('C1, C4 e C5 continuam no nível 2 mesmo com os campos estruturados presentes', () => {
  const html = EssayReport.renderCompetencyChecklist(RATIONALES_V5, [], esc, STRUCTURED);
  ['C1', 'C4', 'C5'].forEach((code) => {
    assert.match(html, new RegExp(`forças ${code}`));
    assert.match(html, new RegExp(`avançar ${code}`));
  });
});

test('a lista "Pontos fortes" não reaparece quando só há feedback estruturado', () => {
  // O fallback de feedback.strengths existe para correções sem NENHUM detalhe
  // por competência - uma correção v5 tem detalhe de sobra.
  const html = EssayReport.renderCompetencyChecklist([], ['ponto forte solto'], esc, STRUCTURED);
  assert.doesNotMatch(html, /Pontos fortes/);
});

test('a lista "Pontos fortes" continua aparecendo quando não há detalhe nenhum', () => {
  const soSummary = [{ competency_code: 'C1', summary: 'resumo' }];
  const html = EssayReport.renderCompetencyChecklist(soSummary, ['ponto forte solto'], esc, null);
  assert.match(html, /Pontos fortes/);
});

// --- integração com os consumidores ---

test('renderRichReport repassa a própria correção como fonte dos campos estruturados', () => {
  const html = EssayReport.renderRichReport(
    {
      final_scores: null, final_feedback: {}, rationales: RATIONALES_V5,
      annotations: [], rewrites: [], alerts: [], mechanical_review: [],
      intro_message: '', closing_message: '', ...STRUCTURED,
    },
    { escFn: esc, originalContentHtml: '' },
  );
  assert.match(html, /Tipologia textual:/);
  assert.match(html, /Projeto argumentativo:/);
});

test('essay-evolution.js repassa checklist.structured para o renderizador', () => {
  const src = fs.readFileSync('src/agente_ia_edu/web/essay-evolution.js', 'utf8');
  assert.match(src, /checklist\.rationales,\s*checklist\.feedbackStrengths,\s*esc,\s*checklist\.structured/);
  assert.match(src, /structured:\s*null/);
});

test('essay.js e essay-review.js montam checklistData com structured', () => {
  const aluno = fs.readFileSync('src/agente_ia_edu/web/essay.js', 'utf8');
  const professor = fs.readFileSync('src/agente_ia_edu/web/essay-review.js', 'utf8');
  assert.match(aluno, /structured:\s*mostRecent/);
  assert.match(professor, /structured:\s*match\.ai_output \|\| \{\}/);
});

test('styles.css tem a regra da lista de aspectos', () => {
  const css = fs.readFileSync('src/agente_ia_edu/web/styles.css', 'utf8');
  assert.match(css, /\.essay-competency-aspects\s*\{/);
});
