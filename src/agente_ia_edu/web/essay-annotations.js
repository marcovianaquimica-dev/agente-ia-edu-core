/* AGENTE IA EDU — módulo compartilhado de destaque de anotações.
   Carregado nos dois portais (aluno e professor) - exceção deliberada à
   convenção de "um helper por arquivo" deste código: o problema de
   desenhar um marcador numerado a partir de uma âncora (TextOffsetAnchor
   ou ImageRegionAnchor) é idêntico nos dois lados. */
(function essayAnnotationsModule(root, factory) {
  const api = factory();
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (root) root.EssayAnnotations = api;
})(typeof window !== 'undefined' ? window : null, function createEssayAnnotations() {
  function esc(value) {
    return String(value ?? '').replace(/[&<>'"]/g, (character) => ({
      '&': '&amp;', '<': '&lt;', '>': '&gt;', "'": '&#39;', '"': '&quot;',
    })[character]);
  }

  // Fixed mapping, no semantic meaning beyond telling competencies apart
  // visually. The CSS classes below (essay-mark-C1 etc.) are the actual
  // source of truth for what color renders - this constant exists so a
  // consumer outside this module (e.g. a future legend, or reusing the
  // same color on the existing competency-score bars) can look up which
  // CSS variable a competency maps to without duplicating the mapping.
  const COMPETENCY_COLORS = {
    C1: '--primary', C2: '--accent', C3: '--danger', C4: '--warning', C5: '--success',
  };

  const isTouch = typeof window !== 'undefined' && window.matchMedia
    && window.matchMedia('(hover: none)').matches;

  let openPopover = null;

  function closePopover() {
    if (openPopover) { openPopover.remove(); openPopover = null; }
  }

  if (typeof document !== 'undefined') {
    document.addEventListener('click', (ev) => {
      if (openPopover && !openPopover.contains(ev.target) && !ev.target.closest('[data-marker-number]')) {
        closePopover();
      }
    });
  }

  function showPopover(rootEl, markerEl, annotation) {
    closePopover();
    const popover = document.createElement('div');
    popover.className = 'essay-popover';
    popover.innerHTML = `
      <strong>${esc(markerEl.dataset.markerNumber)} — ${esc(annotation.competency_code)}</strong>
      <p>${esc(annotation.short_comment)}</p>
      <p class="empty-text">${esc(annotation.long_comment)}</p>
      ${annotation.pedagogical_suggestion ? `<p class="essay-popover-suggestion">${esc(annotation.pedagogical_suggestion)}</p>` : ''}`;
    // Appended inside rootEl (not document.body): every view in this
    // codebase replaces its whole container's innerHTML on navigation, so
    // a popover living inside that container gets torn down for free -
    // appending to document.body would leak an orphaned node whenever the
    // user navigates away without closing the popover first.
    rootEl.appendChild(popover);
    popover.style.position = 'fixed';
    const markerRect = markerEl.getBoundingClientRect();
    const popRect = popover.getBoundingClientRect();
    let left = markerRect.left;
    let top = markerRect.bottom + 6;
    if (left + popRect.width > window.innerWidth) left = window.innerWidth - popRect.width - 8;
    if (top + popRect.height > window.innerHeight) top = markerRect.top - popRect.height - 6;
    popover.style.left = `${Math.max(8, left)}px`;
    popover.style.top = `${Math.max(8, top)}px`;
    openPopover = popover;
  }

  function wirePopovers(rootEl, annotations) {
    rootEl.querySelectorAll('[data-marker-number]').forEach((markerEl) => {
      const number = Number(markerEl.dataset.markerNumber);
      const annotation = annotations[number - 1];
      if (!annotation) return;
      if (isTouch) {
        markerEl.addEventListener('click', (ev) => {
          ev.stopPropagation();
          if (openPopover && openPopover.dataset.forMarker === String(number)) { closePopover(); return; }
          showPopover(rootEl, markerEl, annotation);
          openPopover.dataset.forMarker = String(number);
        });
      } else {
        markerEl.addEventListener('mouseenter', () => showPopover(rootEl, markerEl, annotation));
        markerEl.addEventListener('mouseleave', closePopover);
      }
    });
  }

  function renderHighlightedText(canonicalText, annotations) {
    const text = String(canonicalText ?? '');
    const items = (annotations || [])
      .map((a, i) => ({ a, number: i + 1 }))
      .filter(({ a }) => (
        a.evidence_kind !== 'GLOBAL'
        && a.anchor && a.anchor.type === 'TEXT_OFFSET'
        && a.anchor.start >= 0 && a.anchor.end > a.anchor.start && a.anchor.end <= text.length
      ))
      .sort((x, y) => x.a.anchor.start - y.a.anchor.start);

    let html = '';
    let cursor = 0;
    items.forEach(({ a, number }) => {
      const { start, end } = a.anchor;
      if (start < cursor) return; // overlaps a previous mark - skip visually, stays in the list
      html += esc(text.slice(cursor, start));
      html += `<mark class="essay-mark essay-mark-${esc(a.competency_code)}" data-marker-number="${number}" tabindex="0">${esc(text.slice(start, end))}<sup>${number}</sup></mark>`;
      cursor = end;
    });
    html += esc(text.slice(cursor));
    return html;
  }

  function renderImageMarkers(wrapEl, imgEl, annotations, pageNumber) {
    const items = (annotations || [])
      .map((a, i) => ({ a, number: i + 1 }))
      .filter(({ a }) => (
        a.evidence_kind !== 'GLOBAL'
        && a.anchor && a.anchor.type === 'IMAGE_REGION' && a.anchor.page === pageNumber
      ));
    const naturalWidth = imgEl.naturalWidth || 1;
    const naturalHeight = imgEl.naturalHeight || 1;
    wrapEl.querySelectorAll('.essay-image-marker').forEach((el) => el.remove());
    items.forEach(({ a, number }) => {
      const anchor = a.anchor;
      const isLineBased = anchor.line != null && anchor.total_lines != null;
      let left; let top; let width; let height;
      if (isLineBased) {
        // Line-based (contract v2+): a full-width band for that line - the
        // model counts far more reliably than it estimates pixel position,
        // see essay_engine_contract/v2.py. RULED_AREA_BOTTOM_MARGIN accounts
        // for the blank paper border below the last ruled line in a real
        // photo (confirmed live 2026-09-25: dividing the FULL image height
        // by total_lines drifted later lines increasingly downward, landing
        // on the blank lines below the actual last line of writing).
        const RULED_AREA_BOTTOM_MARGIN = 0.04;
        const ruledAreaFraction = 1 - RULED_AREA_BOTTOM_MARGIN;
        left = 2;
        width = 96;
        top = ((anchor.line - 1) / anchor.total_lines) * ruledAreaFraction * 100;
        height = (1 / anchor.total_lines) * ruledAreaFraction * 100;
      } else {
        // Pixel-based (contract v1, historical corrections only).
        left = (anchor.x / naturalWidth) * 100;
        top = (anchor.y / naturalHeight) * 100;
        width = Math.max((anchor.width / naturalWidth) * 100, 3);
        height = Math.max((anchor.height / naturalHeight) * 100, 3);
      }
      const marker = document.createElement('span');
      const pixelClass = isLineBased ? '' : ' essay-image-marker-pixel';
      marker.className = `essay-image-marker${pixelClass} essay-mark-${esc(a.competency_code)}`;
      marker.dataset.markerNumber = String(number);
      marker.tabIndex = 0;
      marker.style.left = `${left}%`;
      marker.style.top = `${top}%`;
      marker.style.width = `${width}%`;
      marker.style.height = `${height}%`;
      const badge = document.createElement('span');
      badge.className = `essay-image-marker-number essay-mark-${esc(a.competency_code)}`;
      badge.textContent = String(number);
      marker.appendChild(badge);
      wrapEl.appendChild(marker);
    });
  }

  return { renderHighlightedText, renderImageMarkers, wirePopovers, COMPETENCY_COLORS };
});
