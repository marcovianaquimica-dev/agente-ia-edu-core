const test = require('node:test');
const assert = require('node:assert/strict');
const fs = require('node:fs');
const EvolutionView = require('../src/agente_ia_edu/web/evolution.js');

function domainMap(overrides = {}) {
  const contents = overrides.contents || [
    {
      content_node_id: 'content-mastered',
      content_name: 'Concentração',
      discipline_name: 'Química',
      state: 'MASTERED',
      confidence: 0.8,
      evidence_count: 8,
      unknown_count: 0,
      trend: 'IMPROVING',
      is_gap: false,
      pedagogical_contexts: [{ source: 'TEACHER', title: 'Aula recente' }],
      prerequisites: [],
    },
    {
      content_node_id: 'content-gap',
      content_name: 'Equilíbrio químico',
      discipline_name: 'Química',
      state: 'POSSIBLE_GAP',
      confidence: 0.7,
      evidence_count: 6,
      unknown_count: 2,
      trend: 'DECLINING',
      is_gap: true,
      pedagogical_contexts: [],
      prerequisites: [{ content_name: 'Concentração' }],
    },
    {
      content_node_id: 'content-developing',
      content_name: 'Estequiometria',
      discipline_name: 'Química',
      state: 'DEVELOPING',
      confidence: 0.5,
      evidence_count: 4,
      unknown_count: 0,
      trend: 'STABLE',
      is_gap: false,
      pedagogical_contexts: [{ source: 'SCHOOL_PLAN' }],
      prerequisites: [],
    },
  ];
  return {
    student_id: 'student-test',
    is_independent: false,
    summary: { mastered_count: 1, gap_count: 1, low_evidence_count: 0 },
    contents,
    next_best_action: {
      action: 'STUDY_PREREQUISITE',
      target_content_node_id: 'content-mastered',
      related_content_node_id: 'content-gap',
      target_content_name: 'Concentração',
      reason: 'Equilíbrio químico pode depender de Concentração.',
    },
    ...overrides,
  };
}

test('renders real domain states without exposing technical entities', () => {
  const html = EvolutionView.render(domainMap());
  assert.match(html, /Como está seu aprendizado/);
  assert.match(html, /Domino/);
  assert.match(html, /Em desenvolvimento/);
  assert.match(html, /Preciso praticar/);
  assert.match(html, /80% de confiança/);
  assert.doesNotMatch(html, /StudentContentMastery|LearningHistory|content-mastered/);
});

test('renders new-student state without invented mastery', () => {
  const html = EvolutionView.render(domainMap({
    summary: { mastered_count: 0, gap_count: 0, low_evidence_count: 1 },
    contents: [{
      content_node_id: 'new', content_name: 'Frações', state: 'NOT_EVALUATED',
      confidence: 0, evidence_count: 0, unknown_count: 0,
      trend: 'INSUFFICIENT_EVIDENCE', is_gap: false,
      pedagogical_contexts: [], prerequisites: [],
    }],
    next_best_action: { action: 'COMPLETE_MISSING_EVIDENCE', target_content_name: 'Frações' },
  }));
  assert.match(html, /Estamos conhecendo seu nível/);
  assert.match(html, /Começar diagnóstico/);
  assert.doesNotMatch(html, /0% de domínio/);
});

test('renders strengths and attention from backend states', () => {
  const html = EvolutionView.render(domainMap());
  assert.match(html, /Você está indo bem em/);
  assert.match(html, /Pontos fortes/);
  assert.match(html, /Vale dar uma atenção especial a/);
  assert.match(html, /Antes de avançar, vale revisar Concentração/);
});

test('renders next best action and server reason', () => {
  const html = EvolutionView.render(domainMap());
  assert.match(html, /Seu próximo passo/);
  assert.match(html, /Reforçar pré-requisito/);
  assert.match(html, /Recomendamos isso porque/);
  assert.match(html, /Equilíbrio químico pode depender de Concentração/);
});

test('renders unknown and prerequisite evidence when relevant', () => {
  const html = EvolutionView.render(domainMap());
  assert.match(html, /2 respostas “não sei”/);
  assert.match(html, /Possível pré-requisito/);
  assert.match(html, /Vale confirmar este conhecimento/);
});

test('renders school and independent context language', () => {
  assert.equal(
    EvolutionView.contextMessage(domainMap().contents[0], false),
    'Seu professor está trabalhando este conteúdo.'
  );
  assert.equal(
    EvolutionView.contextMessage({ pedagogical_contexts: [] }, true),
    'Trilha personalizada com base no seu desempenho.'
  );
});

test('maps backend actions only to existing portal destinations', () => {
  assert.equal(EvolutionView.actionTarget({ action: 'COMPLETE_MISSING_EVIDENCE' }), 'diagnostic');
  assert.equal(EvolutionView.actionTarget({ action: 'PRACTICE_CONTENT' }), 'practice');
  assert.equal(EvolutionView.actionTarget({ action: 'ADVANCE_CONTENT' }), 'learning-path');
});

test('renders loading skeleton without fake data', () => {
  const html = EvolutionView.renderLoading();
  assert.match(html, /Carregando sua evolução/);
  assert.match(html, /evolution-skeleton/);
  assert.doesNotMatch(html, /Concentração|% de confiança/);
});

test('renders recoverable API error', () => {
  const html = EvolutionView.renderError();
  assert.match(html, /Não foi possível carregar sua evolução/);
  assert.match(html, /Tentar novamente/);
  assert.match(html, /data-evolution-retry/);
});

test('keeps evolution next action isolated from legacy diagnostic styles', () => {
  const renderer = fs.readFileSync('src/agente_ia_edu/web/evolution.js', 'utf8');
  const styles = fs.readFileSync('src/agente_ia_edu/web/styles.css', 'utf8');
  assert.match(renderer, /class="evolution-next-action"/);
  assert.doesNotMatch(renderer, /class="next-action"/);
  assert.match(styles, /\.evolution-next-action/);
});

test('hides unsupported period filter on evolution view', () => {
  const app = fs.readFileSync('src/agente_ia_edu/web/app.js', 'utf8');
  assert.match(app, /headerActions\.hidden = viewName === 'evolution'/);
  const periodHandler = app.match(/timePeriodSelect\.addEventListener\('change',[\s\S]*?\n  \}\);/)?.[0] || '';
  assert.doesNotMatch(periodHandler, /loadEvolutionData/);
});
