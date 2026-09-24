/* AGENTE IA EDU — módulo compartilhado de devolutiva rica de redação.
   Carregado nos dois portais (aluno e professor), depois de
   essay-annotations.js (que este módulo NÃO chama diretamente - a seção de
   texto/imagem original com os marcadores continua sendo montada por quem
   chama, e passada pronta via options.originalContentHtml, porque os dois
   portais já montam essa seção de formas incompatíveis entre si) e antes de
   essay.js/essay-review.js. Função pura: renderRichReport monta uma string
   HTML e não toca o DOM nem religa eventos - quem chama decide onde
   inserir. */
(function essayReportModule(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.EssayReport = api;
})(typeof window !== 'undefined' ? window : null, function createEssayReport() {
  const COMPETENCY_LABELS = {
    C1: 'Domínio da norma padrão', C2: 'Compreensão do tema', C3: 'Argumentação',
    C4: 'Coesão textual', C5: 'Proposta de intervenção',
  };

  // Always shown, whether or not mechanical_review has any dynamic
  // occurrences - a static reference for what C1's mechanical review covers.
  const MECHANICAL_REFERENCE = [
    { label: 'Ortografia', description: 'Grafia correta das palavras conforme a norma padrão.' },
    { label: 'Acentuação', description: 'Uso correto dos acentos gráficos.' },
    { label: 'Crase', description: 'Uso da crase (à) apenas quando há fusão da preposição "a" com o artigo "a(s)".' },
    { label: 'Porquês', description: 'Emprego correto de "por que", "por quê", "porque" e "porquê".' },
    { label: 'Concordância', description: 'Concordância verbal e nominal (sujeito–verbo, substantivo–adjetivo).' },
    { label: 'Regência', description: 'Uso correto das preposições exigidas por verbos e nomes.' },
    { label: 'Pontuação', description: 'Uso adequado de vírgulas, pontos e demais sinais de pontuação.' },
  ];

  const TRANSPARENCY_NOTICE = 'A nota apresentada é uma estimativa pedagógica gerada por '
    + 'inteligência artificial e revisada por um professor: ela apoia o processo de '
    + 'aprendizagem, mas não substitui a avaliação oficial do ENEM ou de qualquer banca examinadora.';

  function renderRichReport(correction, options) {
    const opts = options || {};
    const esc = opts.escFn;
    const scores = correction.final_scores || {};
    const perCompetency = scores.per_competency || {};
    const feedback = correction.final_feedback || {};
    const rationales = correction.rationales || [];
    const annotations = correction.annotations || [];
    const rewrites = correction.rewrites || [];
    const alerts = correction.alerts || [];
    const intervention = correction.intervention || {};
    const mechanicalReview = correction.mechanical_review || [];

    const titleHtml = opts.promptTitle ? `<h3>${esc(opts.promptTitle)}</h3>` : '';

    const introHtml = correction.intro_message
      ? `<p class="essay-intro-message">${esc(correction.intro_message)}</p>`
      : '';

    const alertsHtml = alerts.length
      ? `<div class="essay-alerts">${alerts.map((a) => `<span class="badge badge-accent">${esc(a.code)}</span>`).join(' ')}</div>`
      : '';

    const competencyBarsHtml = Object.keys(COMPETENCY_LABELS).map((code) => {
      const points = (perCompetency[code] || {}).points || 0;
      const pct = Math.round((points / 200) * 100);
      return `
        <div class="essay-competency-row">
          <span>${code} — ${esc(COMPETENCY_LABELS[code])}</span>
          <div class="essay-competency-bar"><div class="essay-competency-fill essay-mark-${code}" style="width:${pct}%"></div></div>
          <span>${points}/200</span>
        </div>`;
    }).join('');

    const rationaleByCode = {};
    rationales.forEach((r) => { rationaleByCode[r.competency_code] = r; });
    const competencyTableRows = Object.keys(COMPETENCY_LABELS).map((code) => {
      const rationale = rationaleByCode[code];
      if (!rationale) return '';
      const hasSplit = rationale.strengths && rationale.growth_area;
      const cells = hasSplit
        ? `<td>${esc(rationale.strengths)}</td><td>${esc(rationale.growth_area)}</td>`
        : `<td colspan="2">${esc(rationale.summary || '')}</td>`;
      return `<tr><th scope="row" class="essay-mark-${code}">${code} — ${esc(COMPETENCY_LABELS[code])}</th>${cells}</tr>`;
    }).join('');
    const competencyTableHtml = competencyTableRows
      ? `<table class="essay-competency-table">
          <thead><tr><th>Competência</th><th>Você já faz bem</th><th>Onde pode avançar</th></tr></thead>
          <tbody>${competencyTableRows}</tbody>
        </table>`
      : '<p class="empty-text">Nenhuma avaliação por competência.</p>';

    const annotationsHtml = annotations.length
      ? annotations.map((a, i) => {
          const quote = (a.anchor && (a.anchor.quote || a.anchor.read_text)) || '';
          return `
            <div class="essay-annotation essay-mark-${esc(a.competency_code)}">
              <span class="essay-annotation-number essay-mark-${esc(a.competency_code)}">${i + 1}</span>
              <strong>${esc(a.letter)} — ${esc(a.competency_code)}</strong>
              <p>${esc(a.short_comment)}</p>
              <p class="empty-text">${esc(a.long_comment)}</p>
              ${quote ? `<blockquote>"${esc(quote)}"</blockquote>` : ''}
            </div>`;
        }).join('')
      : '<p class="empty-text">Nenhuma anotação específica.</p>';

    const rewritesHtml = rewrites.length
      ? rewrites.map((r) => {
          const header = (r.letter && r.competency_code)
            ? `<strong>${esc(r.letter)} — ${esc(r.competency_code)}</strong>`
            : '';
          const markClass = r.competency_code ? ` essay-mark-${esc(r.competency_code)}` : '';
          return `
            <div class="essay-rewrite-block${markClass}">
              ${header}
              <p class="empty-text">Trecho original:</p>
              <blockquote>"${esc(r.original)}"</blockquote>
              <p class="empty-text">Sugestão de reescrita:</p>
              <blockquote>"${esc(r.suggestion)}"</blockquote>
              <p>${esc(r.pedagogical_goal)}</p>
            </div>`;
        }).join('')
      : '';

    const mechanicalReferenceHtml = `
      <table class="essay-mechanical-reference">
        <thead><tr><th>Categoria</th><th>O que observamos</th></tr></thead>
        <tbody>${MECHANICAL_REFERENCE.map((m) => `<tr><th scope="row">${esc(m.label)}</th><td>${esc(m.description)}</td></tr>`).join('')}</tbody>
      </table>`;
    const mechanicalOccurrencesHtml = mechanicalReview.length
      ? mechanicalReview.map((m) => `
          <div class="essay-mechanical-occurrence">
            <strong>${esc(m.category)}</strong>
            <blockquote>"${esc(m.excerpt)}"</blockquote>
            <p>Forma sugerida: ${esc(m.suggested_form)}</p>
            <p class="empty-text">${esc(m.rule_explanation)}</p>
          </div>`).join('')
      : '<p class="empty-text">Nenhuma ocorrência mecânica confirmada nesta redação.</p>';

    const interventionHtml = `
      <ul class="essay-intervention-checklist">
        <li>${intervention.agente ? '✓' : '○'} Agente: ${esc(intervention.agente || '—')}</li>
        <li>${intervention.acao ? '✓' : '○'} Ação: ${esc(intervention.acao || '—')}</li>
        <li>${intervention.meio_modo ? '✓' : '○'} Meio/modo: ${esc(intervention.meio_modo || '—')}</li>
        <li>${intervention.finalidade ? '✓' : '○'} Finalidade: ${esc(intervention.finalidade || '—')}</li>
        <li>${intervention.detalhamento ? '✓' : '○'} Detalhamento: ${esc(intervention.detalhamento || '—')}</li>
      </ul>
      <p class="${intervention.respeita_direitos_humanos ? '' : 'essay-warning'}">
        ${intervention.respeita_direitos_humanos ? '✓ Respeita os direitos humanos' : '⚠ Atenção: verificar respeito aos direitos humanos'}
      </p>`;

    const actionPlanItems = feedback.improvements || [];
    const actionPlanHtml = actionPlanItems.length
      ? `<ol class="essay-action-plan">${actionPlanItems.map((s) => `<li>${esc(s)}</li>`).join('')}</ol>`
      : '<p class="empty-text">Nenhum ponto de melhoria registrado.</p>';

    const nextEssayHtml = feedback.next_essay_strategy
      ? `<h4>Próxima redação</h4><p>${esc(feedback.next_essay_strategy)}</p>`
      : '';

    const hasAnyRationaleSplit = rationales.some((r) => r.strengths && r.growth_area);
    const strengthsFallbackHtml = (!hasAnyRationaleSplit && (feedback.strengths || []).length)
      ? `<h4>Pontos fortes</h4><ul>${feedback.strengths.map((s) => `<li>${esc(s)}</li>`).join('')}</ul>`
      : '';

    const closingHtml = correction.closing_message
      ? `<p class="essay-closing-message">${esc(correction.closing_message)}</p>`
      : '';

    const transparencyHtml = `<p class="essay-transparency-notice">${esc(TRANSPARENCY_NOTICE)}</p>`;

    return `
      ${titleHtml}
      ${introHtml}
      <div class="essay-total-score">Nota total: ${scores.total != null ? scores.total : '—'} / 1000</div>
      ${alertsHtml}
      <h4>Notas por competência</h4>
      ${competencyBarsHtml}
      <h4>O que você já faz bem e onde pode avançar</h4>
      ${competencyTableHtml}
      ${strengthsFallbackHtml}
      <h4>Sua redação</h4>
      ${opts.originalContentHtml || ''}
      <h4>Anotações</h4>
      ${annotationsHtml}
      ${rewritesHtml ? `<h4>Reescritas sugeridas</h4>${rewritesHtml}` : ''}
      <h4>Revisão de domínio da norma padrão (C1)</h4>
      ${mechanicalReferenceHtml}
      ${mechanicalOccurrencesHtml}
      <h4>Competência 5 — Proposta de intervenção</h4>
      ${interventionHtml}
      <h4>Plano de ação</h4>
      ${actionPlanHtml}
      ${nextEssayHtml}
      ${closingHtml}
      ${transparencyHtml}`;
  }

  return { renderRichReport };
});
