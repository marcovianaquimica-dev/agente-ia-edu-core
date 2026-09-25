/* AGENTE IA EDU — módulo compartilhado do dashboard de evolução de redação.
   Carregado nos dois portais, depois de essay-report.js (usa
   window.EssayReport.renderCompetencyChecklist para a seção "o que está
   bom / o que precisa melhorar") e antes de essay.js/essay-review.js.
   Funções puras: renderEvolutionSection monta uma string HTML e não toca o
   DOM - quem chama decide onde inserir e chama wireEvolutionSection depois
   para ligar os popovers dos pontos do gráfico. */
(function essayEvolutionModule(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.EssayEvolution = api;
})(typeof window !== 'undefined' ? window : null, function createEssayEvolution() {
  const COMPETENCY_LABELS = {
    C1: 'Domínio da norma padrão', C2: 'Compreensão do tema', C3: 'Argumentação',
    C4: 'Coesão textual', C5: 'Proposta de intervenção',
  };
  const COMPETENCY_CODES = ['C1', 'C2', 'C3', 'C4', 'C5'];
  // Same hex values already used on screen (styles.css's --primary/--accent/
  // --danger/--warning/--success) and in the PDF export
  // (essay_pdf_export.py's _COMPETENCY_SOLID_COLORS) - kept as literal hex
  // here (not var(--...)) so the SVG stroke/fill attributes render
  // correctly even where CSS custom properties in SVG attributes aren't
  // supported.
  const COMPETENCY_COLORS = {
    C1: '#4f46e5', C2: '#06b6d4', C3: '#ef4444', C4: '#f59e0b', C5: '#10b981',
  };
  const TOTAL_COLOR = '#1e293b';

  function esc(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[character]);
  }

  function fmtDate(iso) {
    return new Date(iso).toLocaleDateString('pt-BR');
  }

  // --- timeline cards ---

  function renderTimelineCards(entries) {
    if (!entries.length) {
      return '<p class="empty-text">Vamos ver sua evolução assim que sua primeira redação for corrigida.</p>';
    }
    const cardsHtml = entries.map((entry) => {
      const barsHtml = COMPETENCY_CODES.map((code) => {
        const points = entry.per_competency ? entry.per_competency[code] : null;
        const pct = points != null ? Math.round((points / 200) * 100) : 0;
        return `
          <div>
            <div class="essay-evolution-mini-label">${code}</div>
            <div class="essay-evolution-mini-track"><div class="essay-evolution-mini-fill essay-mark-${code}" style="width:${pct}%"></div></div>
          </div>`;
      }).join('');
      const totalHtml = entry.total != null ? `${entry.total}/1000` : 'Sem nota (formativo)';
      return `
        <div class="card essay-evolution-card">
          <div class="essay-evolution-card-head">
            <span>${fmtDate(entry.published_at)} — ${esc(entry.prompt_title)}</span>
            <span class="essay-evolution-card-total">${totalHtml}</span>
          </div>
          <div class="essay-evolution-mini-bars">${barsHtml}</div>
        </div>`;
    }).join('');
    return `<div class="essay-evolution-timeline">${cardsHtml}</div>`;
  }

  function renderDeltaSummary(totalDelta) {
    if (totalDelta == null) return '';
    const verb = totalDelta >= 0 ? 'subiu' : 'desceu';
    return `<p class="essay-evolution-summary">Sua nota total ${verb} ${Math.abs(totalDelta)} pontos desde a primeira redação.</p>`;
  }

  // --- SVG line charts: one per competency (0-200) plus one for total (0-1000) ---

  function buildSeries(entries, key) {
    // entries chega do backend em ordem mais-recente-primeiro; o gráfico é
    // lido da esquerda (mais antiga) para a direita (mais recente).
    const chronological = entries.slice().reverse();
    return chronological
      .map((entry) => ({
        value: key === 'total' ? entry.total : (entry.per_competency ? entry.per_competency[key] : null),
        dateLabel: fmtDate(entry.published_at),
        fullLabel: `${fmtDate(entry.published_at)} — ${entry.prompt_title}`,
      }))
      .filter((point) => point.value != null);
  }

  function renderLineChart(points, opts) {
    const { max, color, title, chartIndex } = opts;
    if (!points.length) {
      return `
        <div class="essay-evolution-chart-card" data-chart-index="${chartIndex}">
          <div class="essay-evolution-chart-title"><span class="essay-evolution-chart-badge" style="background:${color}"></span>${esc(title)}</div>
          <p class="empty-text">Sem dados suficientes ainda.</p>
        </div>`;
    }
    const left = 50;
    const right = 500;
    const top = 20;
    const bottom = 180;
    const stepX = points.length > 1 ? (right - left) / (points.length - 1) : 0;
    const coords = points.map((p, i) => ({
      x: points.length > 1 ? left + i * stepX : (left + right) / 2,
      y: bottom - (Math.max(0, Math.min(p.value, max)) / max) * (bottom - top),
      value: p.value, dateLabel: p.dateLabel,
    }));
    const gridHtml = [top, (top + bottom) / 2, bottom]
      .map((y) => `<line x1="${left}" y1="${y}" x2="${right}" y2="${y}" stroke="#eef1f5"/>`).join('');
    const axisLabelsHtml = `
      <text x="10" y="${top + 4}" class="essay-evolution-axis-label">${max}</text>
      <text x="10" y="${(top + bottom) / 2 + 4}" class="essay-evolution-axis-label">${Math.round(max / 2)}</text>
      <text x="10" y="${bottom + 4}" class="essay-evolution-axis-label">0</text>`;
    const polylineHtml = coords.length > 1
      ? `<polyline points="${coords.map((c) => `${c.x},${c.y}`).join(' ')}" fill="none" stroke="${color}" stroke-width="2.5"/>`
      : '';
    const pointsHtml = coords.map((c, i) => `
      <circle cx="${c.x}" cy="${c.y}" r="5" fill="${color}" data-evolution-point="${i}" tabindex="0"></circle>
      <text x="${c.x}" y="${bottom + 20}" text-anchor="middle" class="essay-evolution-pt-label">${esc(c.dateLabel)}</text>
      <text x="${c.x}" y="${Math.max(top + 10, c.y - 10)}" text-anchor="middle" class="essay-evolution-pt-value">${c.value}</text>`).join('');
    return `
      <div class="essay-evolution-chart-card" data-chart-index="${chartIndex}">
        <div class="essay-evolution-chart-title"><span class="essay-evolution-chart-badge" style="background:${color}"></span>${esc(title)}</div>
        <svg viewBox="0 0 520 220" width="100%">
          ${gridHtml}
          ${axisLabelsHtml}
          ${polylineHtml}
          ${pointsHtml}
        </svg>
      </div>`;
  }

  function renderCharts(entries) {
    const competencyChartsHtml = COMPETENCY_CODES.map((code, i) => renderLineChart(
      buildSeries(entries, code),
      { max: 200, color: COMPETENCY_COLORS[code], title: `${code} — ${COMPETENCY_LABELS[code]}`, chartIndex: i },
    )).join('');
    const totalChartHtml = renderLineChart(
      buildSeries(entries, 'total'),
      { max: 1000, color: TOTAL_COLOR, title: 'Nota total', chartIndex: COMPETENCY_CODES.length },
    );
    return `<div class="essay-evolution-charts">${competencyChartsHtml}${totalChartHtml}</div>`;
  }

  // --- chart point popovers (fixed-position div, positioning technique
  // copied from essay-annotations.js's showPopover/closePopover; content
  // shape is different here - a chart point, not an annotation - so this
  // is a small local reimplementation rather than a shared function) ---

  let openChartPopover = null;

  function closeChartPopover() {
    if (openChartPopover) { openChartPopover.remove(); openChartPopover = null; }
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('click', (ev) => {
      if (openChartPopover && !openChartPopover.contains(ev.target) && !ev.target.closest('[data-evolution-point]')) {
        closeChartPopover();
      }
    });
  }

  function showChartPopover(rootEl, pointEl, point) {
    closeChartPopover();
    const popover = document.createElement('div');
    popover.className = 'essay-popover';
    popover.innerHTML = `<strong>${esc(point.fullLabel)}</strong><p>${esc(String(point.value))}</p>`;
    rootEl.appendChild(popover);
    popover.style.position = 'fixed';
    const pointRect = pointEl.getBoundingClientRect();
    const popRect = popover.getBoundingClientRect();
    let left = pointRect.left;
    let top = pointRect.bottom + 6;
    if (left + popRect.width > window.innerWidth) left = window.innerWidth - popRect.width - 8;
    if (top + popRect.height > window.innerHeight) top = pointRect.top - popRect.height - 6;
    popover.style.left = `${Math.max(8, left)}px`;
    popover.style.top = `${Math.max(8, top)}px`;
    openChartPopover = popover;
  }

  function wireChartPopovers(rootEl, seriesByChart) {
    rootEl.querySelectorAll('[data-evolution-point]').forEach((pointEl) => {
      const chartCard = pointEl.closest('[data-chart-index]');
      if (!chartCard) return;
      const chartIndex = Number(chartCard.dataset.chartIndex);
      const pointIndex = Number(pointEl.dataset.evolutionPoint);
      const point = (seriesByChart[chartIndex] || [])[pointIndex];
      if (!point) return;
      pointEl.addEventListener('mouseenter', () => showChartPopover(rootEl, pointEl, point));
      pointEl.addEventListener('mouseleave', closeChartPopover);
      pointEl.addEventListener('click', (ev) => {
        ev.stopPropagation();
        showChartPopover(rootEl, pointEl, point);
      });
    });
  }

  // --- public API ---

  function renderEvolutionSection(data, checklistData) {
    const entries = data.entries || [];
    const checklist = checklistData || { rationales: [], feedbackStrengths: [] };
    const checklistHtml = window.EssayReport.renderCompetencyChecklist(
      checklist.rationales, checklist.feedbackStrengths, esc,
    );
    return `
      ${renderDeltaSummary(data.total_delta)}
      ${renderTimelineCards(entries)}
      <h4 class="essay-evolution-section-title">Evolução por competência</h4>
      ${renderCharts(entries)}
      <h4 class="essay-evolution-section-title">O que está bom e o que precisa melhorar</h4>
      ${checklistHtml}`;
  }

  function wireEvolutionSection(rootEl, data) {
    const entries = data.entries || [];
    const seriesByChart = COMPETENCY_CODES.map((code) => buildSeries(entries, code));
    seriesByChart.push(buildSeries(entries, 'total'));
    wireChartPopovers(rootEl, seriesByChart);
  }

  return { renderEvolutionSection, wireEvolutionSection };
});
