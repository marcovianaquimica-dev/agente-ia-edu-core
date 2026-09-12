(function evolutionModule(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.EvolutionView = api;
})(typeof window !== 'undefined' ? window : null, function createEvolutionView() {
  const stateLabels = {
    MASTERED: 'Domino',
    DEVELOPING: 'Em desenvolvimento',
    POSSIBLE_GAP: 'Preciso praticar',
    LOW_EVIDENCE: 'Ainda conhecendo',
    NOT_EVALUATED: 'Ainda não avaliado',
  };
  const trendLabels = {
    IMPROVING: 'Evoluindo',
    STABLE: 'Estável',
    DECLINING: 'Vale revisar',
    INSUFFICIENT_EVIDENCE: 'Poucas evidências',
  };
  const actionLabels = {
    PRACTICE_CONTENT: 'Praticar',
    REVIEW_CONTENT: 'Revisar',
    STUDY_PREREQUISITE: 'Reforçar pré-requisito',
    REINFORCE_CONTENT: 'Consolidar',
    ADVANCE_CONTENT: 'Avançar',
    COMPLETE_MISSING_EVIDENCE: 'Completar diagnóstico',
  };

  function escapeHtml(value) {
    return String(value ?? '').replace(/[&<>'"]/g, character => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;'
    })[character]);
  }

  function confidenceLabel(value) {
    return `${Math.round(Number(value || 0) * 100)}% de confiança`;
  }

  function contextMessage(content, isIndependent) {
    const sources = new Set((content.pedagogical_contexts || []).map(item => item.source));
    if (sources.has('TEACHER')) return 'Seu professor está trabalhando este conteúdo.';
    if (sources.has('COORDINATION')) return 'A coordenação destacou este conteúdo.';
    if (sources.has('SCHOOL_PLAN')) return 'Este conteúdo faz parte do seu planejamento atual.';
    return isIndependent ? 'Trilha personalizada com base no seu desempenho.' : '';
  }

  function actionTarget(action) {
    if (!action) return 'evolution';
    if (action.action === 'COMPLETE_MISSING_EVIDENCE') return 'diagnostic';
    if (['PRACTICE_CONTENT', 'REINFORCE_CONTENT', 'STUDY_PREREQUISITE'].includes(action.action)) return 'practice';
    return 'learning-path';
  }

  function renderLoading() {
    return `<div class="evolution-loading" aria-live="polite" aria-label="Carregando sua evolução">
      <div class="evolution-skeleton skeleton-wide"></div>
      <div class="evolution-overview-grid">${Array.from({ length: 4 }, () => '<div class="evolution-skeleton skeleton-card"></div>').join('')}</div>
      <div class="evolution-skeleton skeleton-map"></div>
    </div>`;
  }

  function renderError() {
    return `<div class="evolution-state evolution-error" role="alert">
      <span class="evolution-state-icon">!</span>
      <h2>Não foi possível carregar sua evolução.</h2>
      <p>Seus dados continuam seguros. Tente abrir esta visão novamente.</p>
      <button class="btn btn-primary" type="button" data-evolution-retry>Tentar novamente</button>
    </div>`;
  }

  function renderEmpty() {
    return `<div class="evolution-state evolution-empty">
      <span class="evolution-kicker">Seu mapa começa aqui</span>
      <h2>Estamos conhecendo seu nível.</h2>
      <p>À medida que você responder questões e realizar atividades, seu mapa de domínio ficará mais preciso.</p>
      <button class="btn btn-primary" type="button" data-evolution-view="diagnostic">Começar diagnóstico</button>
    </div>`;
  }

  function renderContent(content, isIndependent) {
    const prerequisites = (content.prerequisites || []).map(item => `
      <div class="evolution-prerequisite">
        <span>Possível pré-requisito</span>
        <strong>${escapeHtml(item.content_name)}</strong>
        <small>Vale confirmar este conhecimento antes de avançar.</small>
      </div>`).join('');
    const unknown = content.unknown_count > 0
      ? `<span class="evolution-detail">${content.unknown_count} resposta${content.unknown_count === 1 ? '' : 's'} “não sei”</span>`
      : '';
    const context = contextMessage(content, isIndependent);
    return `<article class="domain-content domain-${content.state.toLowerCase().replaceAll('_', '-')}">
      <div class="domain-content-head">
        <div><span class="domain-discipline">${escapeHtml(content.discipline_name || content.area_name || 'Área de estudo')}</span><h3>${escapeHtml(content.content_name)}</h3></div>
        <span class="domain-state">${escapeHtml(stateLabels[content.state] || content.state)}</span>
      </div>
      <div class="domain-content-meta">
        <span>${escapeHtml(confidenceLabel(content.confidence))}</span>
        <span>${content.evidence_count} evidência${content.evidence_count === 1 ? '' : 's'}</span>
        <span>${escapeHtml(trendLabels[content.trend] || content.trend)}</span>
        ${unknown}
      </div>
      ${context ? `<p class="domain-context">${escapeHtml(context)}</p>` : ''}
      ${prerequisites}
    </article>`;
  }

  function renderAction(action, sourceContent, isIndependent) {
    if (!action) return '';
    const context = sourceContent ? contextMessage(sourceContent, isIndependent) : '';
    return `<section class="evolution-next-action" aria-labelledby="next-action-title">
      <div class="evolution-next-action-copy"><span class="evolution-kicker">Seu próximo passo</span><h2 id="next-action-title">${escapeHtml(action.target_content_name)}</h2>
      <span class="evolution-next-action-kind">${escapeHtml(actionLabels[action.action] || action.action)}</span>
      <p><strong>Recomendamos isso porque:</strong> ${escapeHtml(action.reason)}</p>
      ${context ? `<p class="evolution-next-action-context">${escapeHtml(context)}</p>` : ''}</div>
      <button class="btn btn-primary" type="button" data-evolution-view="${actionTarget(action)}">Continuar</button>
    </section>`;
  }

  function renderTrail(action) {
    if (!action) return '';
    return `<section class="evolution-section"><div class="evolution-section-head"><div><span class="evolution-kicker">Minha Trilha</span><h2>Uma orientação de cada vez</h2></div></div>
      <div class="mini-trail"><div class="mini-trail-step current"><span>Agora</span><strong>Você está aqui</strong></div><span class="mini-trail-line" aria-hidden="true"></span>
      <div class="mini-trail-step"><span>Próxima ação</span><strong>${escapeHtml(actionLabels[action.action] || action.action)} ${escapeHtml(action.target_content_name)}</strong></div></div>
    </section>`;
  }

  function render(data) {
    const contents = data.contents || [];
    const hasEvidence = contents.some(content => content.evidence_count > 0);
    if (!hasEvidence) return renderEmpty();
    const counts = contents.reduce((result, content) => {
      result[content.state] = (result[content.state] || 0) + 1;
      return result;
    }, {});
    const strengths = contents.filter(content => content.state === 'MASTERED');
    const attention = contents.filter(content => content.is_gap || (content.prerequisites || []).length > 0);
    const actionSource = contents.find(content => String(content.content_node_id) === String(data.next_best_action?.related_content_node_id || data.next_best_action?.target_content_node_id));
    return `${renderAction(data.next_best_action, actionSource, data.is_independent)}
      <section class="evolution-overview" aria-labelledby="domain-overview-title"><div class="evolution-section-head"><div><span class="evolution-kicker">Visão geral</span><h2 id="domain-overview-title">Como está seu aprendizado</h2></div></div>
      <div class="evolution-overview-grid">
        <div class="overview-item mastered"><strong>${data.summary?.mastered_count || 0}</strong><span>Conteúdos dominados</span></div>
        <div class="overview-item developing"><strong>${counts.DEVELOPING || 0}</strong><span>Em desenvolvimento</span></div>
        <div class="overview-item attention"><strong>${data.summary?.gap_count || 0}</strong><span>Precisam de atenção</span></div>
        <div class="overview-item unknown"><strong>${data.summary?.low_evidence_count || 0}</strong><span>Ainda conhecendo</span></div>
      </div></section>
      <section class="evolution-section"><div class="evolution-section-head"><div><span class="evolution-kicker">Mapa de domínio</span><h2>O que seu percurso já mostra</h2></div><span class="domain-map-count">${contents.length} conteúdo${contents.length === 1 ? '' : 's'}</span></div>
      <div class="domain-map-grid">${contents.map(content => renderContent(content, data.is_independent)).join('')}</div></section>
      <div class="evolution-insights">
        <section class="evolution-section insight-section"><span class="evolution-kicker">Você está indo bem em</span><h2>Pontos fortes</h2>${strengths.length ? `<ul>${strengths.map(content => `<li><strong>${escapeHtml(content.content_name)}</strong><span>${escapeHtml(trendLabels[content.trend] || content.trend)}</span></li>`).join('')}</ul>` : '<p>Ainda estamos reunindo evidências para destacar seus pontos fortes.</p>'}</section>
        <section class="evolution-section insight-section"><span class="evolution-kicker">Vale dar uma atenção especial a</span><h2>Pontos de atenção</h2>${attention.length ? `<ul>${attention.map(content => `<li><strong>${escapeHtml(content.content_name)}</strong><span>${content.prerequisites?.length ? `Antes de avançar, vale revisar ${escapeHtml(content.prerequisites[0].content_name)}.` : 'Uma nova prática pode ajudar a consolidar este conteúdo.'}</span></li>`).join('')}</ul>` : '<p>Nenhuma lacuna com evidência suficiente foi identificada agora.</p>'}</section>
      </div>${renderTrail(data.next_best_action)}`;
  }

  return { actionTarget, contextMessage, render, renderError, renderLoading, stateLabels, trendLabels };
});