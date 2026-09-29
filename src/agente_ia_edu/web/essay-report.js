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
    C1: 'Domínio da norma padrão', C2: 'Tipologia, tema e repertório',
    C3: 'Projeto argumentativo e autoria',
    C4: 'Coesão textual', C5: 'Proposta de intervenção',
  };

  // Official wording (Matriz de Referência do ENEM) - shown small and
  // discreetly under each annotation's competency label, so a student who
  // doesn't have the five competencies memorized still knows what "C3"
  // actually means without leaving the annotation.
  const COMPETENCY_DESCRIPTIONS = {
    C1: 'Domínio da norma culta da língua escrita.',
    C2: 'Compreensão do tema e aplicação de áreas do conhecimento na estrutura dissertativo-argumentativa.',
    C3: 'Seleção e organização de argumentos em defesa de um ponto de vista.',
    C4: 'Conhecimento dos mecanismos linguísticos de coesão.',
    C5: 'Elaboração de proposta de intervenção respeitando os direitos humanos.',
  };

  function renderCompetencyChecklist(rationales, feedbackStrengths, esc) {
    const rationaleByCode = {};
    (rationales || []).forEach((r) => { rationaleByCode[r.competency_code] = r; });
    const competencyTableRows = Object.keys(COMPETENCY_LABELS).map((code) => {
      const rationale = rationaleByCode[code];
      if (!rationale) return '';
      const hasSplit = rationale.strengths && rationale.growth_area;
      const cells = hasSplit
        ? `<td data-label="Você já faz bem">${esc(rationale.strengths)}</td><td data-label="Onde pode avançar">${esc(rationale.growth_area)}</td>`
        : `<td colspan="2">${esc(rationale.summary || '')}</td>`;
      return `<tr><th scope="row" class="essay-mark-${code}">${code} — ${esc(COMPETENCY_LABELS[code])}</th>${cells}</tr>`;
    }).join('');
    const competencyTableHtml = competencyTableRows
      ? `<table class="essay-competency-table">
          <thead><tr><th>Competência</th><th>Você já faz bem</th><th>Onde pode avançar</th></tr></thead>
          <tbody>${competencyTableRows}</tbody>
        </table>`
      : '<p class="empty-text">Nenhuma avaliação por competência.</p>';
    const hasAnyRationaleSplit = (rationales || []).some((r) => r.strengths && r.growth_area);
    const strengthsFallbackHtml = (!hasAnyRationaleSplit && (feedbackStrengths || []).length)
      ? `<h4>Pontos fortes</h4><ul>${feedbackStrengths.map((s) => `<li>${esc(s)}</li>`).join('')}</ul>`
      : '';
    return competencyTableHtml + strengthsFallbackHtml;
  }

  function renderRichReport(correction, options) {
    const opts = options || {};
    const esc = opts.escFn;
    const hasScores = correction.final_scores != null;
    const scores = correction.final_scores || {};
    const perCompetency = scores.per_competency || {};
    const feedback = correction.final_feedback || {};
    const rationales = correction.rationales || [];
    const annotations = correction.annotations || [];
    const rewrites = correction.rewrites || [];
    const alerts = correction.alerts || [];
    const mechanicalReview = correction.mechanical_review || [];

    const titleHtml = opts.promptTitle ? `<h3>${esc(opts.promptTitle)}</h3>` : '';

    const introHtml = correction.intro_message
      ? `<p class="essay-intro-message">${esc(correction.intro_message)}</p>`
      : '';

    const alertsHtml = alerts.length
      ? `<div class="essay-alerts">${alerts.map((a) => `<span class="badge badge-accent">${esc(a.code)}</span>`).join(' ')}</div>`
      : '';

    const competencyBarsHtml = hasScores
      ? Object.keys(COMPETENCY_LABELS).map((code) => {
          const points = (perCompetency[code] || {}).points || 0;
          const pct = Math.round((points / 200) * 100);
          return `
            <div class="essay-competency-row">
              <span>${code} — ${esc(COMPETENCY_LABELS[code])}</span>
              <div class="essay-competency-bar"><div class="essay-competency-fill essay-mark-${code}" style="width:${pct}%"></div></div>
              <span>${points}/200</span>
            </div>`;
        }).join('')
      : '<p class="empty-text">Correção formativa: sem nota atribuída, apenas feedback pedagógico.</p>';

    const competencyTableHtml = renderCompetencyChecklist(rationales, feedback.strengths, esc);

    const annotationsHtml = annotations.length
      ? annotations.map((a, i) => {
          const quote = (a.anchor && (a.anchor.quote || a.anchor.read_text)) || '';
          return `
            <div class="essay-annotation essay-mark-${esc(a.competency_code)}">
              <span class="essay-annotation-number essay-mark-${esc(a.competency_code)}">${i + 1}</span>
              <strong>${i + 1} — ${esc(a.competency_code)}</strong>
              ${COMPETENCY_DESCRIPTIONS[a.competency_code] ? `<span class="essay-annotation-competency-hint">${esc(COMPETENCY_DESCRIPTIONS[a.competency_code])}</span>` : ''}
              <p>${esc(a.short_comment)}</p>
              <p class="empty-text">${esc(a.long_comment)}</p>
              ${quote ? `<blockquote>"${esc(quote)}"</blockquote>` : ''}
            </div>`;
        }).join('')
      : '<p class="empty-text">Nenhuma anotação específica.</p>';

    const letterToNumber = {};
    annotations.forEach((a, i) => { letterToNumber[a.letter] = i + 1; });
    const rewritesHtml = rewrites.length
      ? rewrites.map((r) => {
          const number = letterToNumber[r.letter];
          const header = (number !== undefined && r.competency_code)
            ? `<strong>${number} — ${esc(r.competency_code)}</strong>`
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

    const mechanicalReviewSectionHtml = mechanicalReview.length
      ? `<h4>Revisão de domínio da norma padrão (C1)</h4>${mechanicalReview.map((m) => `
          <div class="essay-mechanical-occurrence">
            <strong>${esc(m.category)}</strong>
            <blockquote>"${esc(m.excerpt)}"</blockquote>
            <p>Forma sugerida: ${esc(m.suggested_form)}</p>
            <p class="empty-text">${esc(m.rule_explanation)}</p>
          </div>`).join('')}`
      : '';

    const actionPlanItems = feedback.improvements || [];
    const actionPlanHtml = actionPlanItems.length
      ? `<ol class="essay-action-plan">${actionPlanItems.map((s) => `<li>${esc(s)}</li>`).join('')}</ol>`
      : '<p class="empty-text">Nenhum ponto de melhoria registrado.</p>';

    const nextEssayHtml = feedback.next_essay_strategy
      ? `<h4>Próxima redação</h4><p>${esc(feedback.next_essay_strategy)}</p>`
      : '';

    const closingHtml = correction.closing_message
      ? `<p class="essay-closing-message">${esc(correction.closing_message)}</p>`
      : '';

    return `
      ${titleHtml}
      ${introHtml}
      ${hasScores ? `<div class="essay-total-score">Nota total: ${scores.total != null ? scores.total : '—'} / 1000</div>` : ''}
      ${alertsHtml}
      <h4>Notas por competência</h4>
      ${competencyBarsHtml}
      <h4>O que você já faz bem e onde pode avançar</h4>
      ${competencyTableHtml}
      <h4>Sua redação</h4>
      ${opts.originalContentHtml || ''}
      <h4>Anotações</h4>
      ${annotationsHtml}
      ${rewritesHtml ? `<h4>Reescritas sugeridas</h4>${rewritesHtml}` : ''}
      ${mechanicalReviewSectionHtml}
      <h4>Plano de ação</h4>
      ${actionPlanHtml}
      ${nextEssayHtml}
      ${closingHtml}`;
  }

  return { renderRichReport, renderCompetencyChecklist };
});
