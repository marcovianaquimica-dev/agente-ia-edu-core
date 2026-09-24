# Exportar devolutiva de redação em PDF — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Let students and teachers download the rich essay-correction devolutiva as a PDF, matching the on-screen content and visual highlighting, in both portals.

**Architecture:** Server-side PDF generation via PyMuPDF (already a project dependency), following the exact precedent already in production at `services/list_export.py`. A new module `services/essay_pdf_export.py` ports `essay-report.js`'s template logic to Python HTML fragments, rendered through PyMuPDF's `Story` (HTML→PDF) API for all text content, with a separate low-level `Page.insert_image()`/`draw_rect()` pass for embedding submitted page photos with colored annotation overlays (IMAGE_REGION mode only) — the two techniques are combined into one final document via `Document.insert_pdf()`. Two new routes, one per portal, each reusing its portal's existing authorization chain unmodified.

**Tech Stack:** Python 3 / FastAPI / PyMuPDF (`pymupdf`, already pinned `>=1.24,<2.0`) / vanilla JS (link only, no new JS logic).

**Spec:** [docs/superpowers/specs/2026-09-24-exportar-devolutiva-pdf-design.md](../specs/2026-09-24-exportar-devolutiva-pdf-design.md)

## Global Constraints

- Server-side only, via PyMuPDF — no client-side PDF library, no headless browser, no new dependency (matches `services/list_export.py`'s established precedent exactly).
- Student export: only when `correction.status == "APPROVED"` → `404` otherwise.
- Teacher export: only when `correction.status in ("APPROVED", "PENDING_REVIEW")` → `404` otherwise (`NEEDS_REVIEW`/`REJECTED` have no publishable `ai_output`).
- No new authorization rule — both routes reuse their portal's existing chain unmodified (`_authorize_student`+`_resolve_enrollment_or_403`+`_submission_for_own_school_or_403` for the student route; `_authorize`+`_correction_for_own_school_or_403` for the teacher route).
- `pdf_available()` returning `False` → `503` with the same message shape as `question_bank.py`'s `export_list_pdf` (`"PDF export requires the 'pymupdf' package"`).
- The PDF's content and fallback rules for old data (missing `strengths`/`growth_area`/`intro_message`/`closing_message`/`mechanical_review`) must match `essay-report.js`'s rules exactly (spec §4, itself copied verbatim from the devolutiva-rica leva's spec §3) — this module is a port, not a new design.
- No automated frontend tests (established convention) — the frontend change in this plan is a single `<a href="...">` link per portal, mirroring `question-bank.js`'s existing `<a href="${API}/lists/${id}/export.pdf">Exportar PDF</a>` pattern exactly.
- This leva is read-only / no side effects — no change to the approve/reject/retry/resubmit workflows.

---

### Task 1: PDF rendering module (`services/essay_pdf_export.py`)

**Files:**
- Create: `src/agente_ia_edu/services/essay_pdf_export.py`

**Interfaces:**
- Produces: `pdf_available() -> bool`, `build_render_model(correction_view: dict) -> dict`, `render_pdf(model: dict, *, title: str | None, anchor_mode: str, canonical_text: str | None = None, page_image_paths: list[str] | None = None) -> bytes`, `filename_for_title(title: str) -> str`. All four consumed by Task 2 and Task 3.
- Consumes: nothing from other tasks — this is the foundation task.

`correction_view` is a plain dict with the same keys `essay-report.js`'s `renderRichReport` reads off its `correction` argument: `final_scores`, `final_feedback`, `annotations`, `rewrites`, `alerts`, `intervention`, `rationales`, `intro_message`, `closing_message`, `mechanical_review`. `build_render_model` is defensive about shape internally (any of these can be missing, `None`, or — for old/malformed data — present but wrong-typed) so neither calling route needs to pre-sanitize `ai_output` itself.

- [ ] **Step 1: Write the module**

```python
"""PDF export of the rich essay-correction devolutiva.

Mirrors services/list_export.py's shape (pdf_available/build_render_model/
render_pdf) and PyMuPDF usage - that module is the precedent this one
follows, not a new design.

The HTML fragments this module builds are a server-side port of
web/essay-report.js's renderRichReport template - same sections, same
order, same fallback rules for old data (spec §4, copied verbatim from the
devolutiva-rica leva's own spec §3). If a rendering rule here and
essay-report.js's ever disagree, essay-report.js is the source of truth;
fix this module to match it, not the other way around.

Text content (everything except submitted page photos) is rendered via
PyMuPDF's Story API (HTML+CSS -> PDF, with automatic pagination) into an
in-memory PDF, then merged with a small number of manually-drawn image
pages (Page.insert_image + Page.draw_rect, the same low-level API
list_export.py already uses) via Document.insert_pdf() - PyMuPDF documents
can be assembled this way with no conflict between the two techniques.

Spec: docs/superpowers/specs/2026-09-24-exportar-devolutiva-pdf-design.md
"""

from __future__ import annotations

import html
import io
from typing import Any

_COMPETENCY_CODES: tuple[str, ...] = ("C1", "C2", "C3", "C4", "C5")
_COMPETENCY_LABELS: dict[str, str] = {
    "C1": "Domínio da norma padrão", "C2": "Compreensão do tema", "C3": "Argumentação",
    "C4": "Coesão textual", "C5": "Proposta de intervenção",
}
# Same literal hex values as styles.css's --primary-light/--accent-light/
# --danger-light/--warning-light/--success-light (Story's CSS engine can't
# resolve CSS custom properties from the app's own stylesheet).
_COMPETENCY_COLORS: dict[str, str] = {
    "C1": "#eef2ff", "C2": "#ecfeff", "C3": "#fef2f2", "C4": "#fffbeb", "C5": "#ecfdf5",
}
_MECHANICAL_REFERENCE: tuple[dict[str, str], ...] = (
    {"label": "Ortografia", "description": "Grafia correta das palavras conforme a norma padrão."},
    {"label": "Acentuação", "description": "Uso correto dos acentos gráficos."},
    {"label": "Crase", "description": 'Uso da crase (à) apenas quando há fusão da preposição "a" com o artigo "a(s)".'},
    {"label": "Porquês", "description": 'Emprego correto de "por que", "por quê", "porque" e "porquê".'},
    {"label": "Concordância", "description": "Concordância verbal e nominal (sujeito–verbo, substantivo–adjetivo)."},
    {"label": "Regência", "description": "Uso correto das preposições exigidas por verbos e nomes."},
    {"label": "Pontuação", "description": "Uso adequado de vírgulas, pontos e demais sinais de pontuação."},
)
_TRANSPARENCY_NOTICE = (
    "A nota apresentada é uma estimativa pedagógica gerada por inteligência artificial e "
    "revisada por um professor: ela apoia o processo de aprendizagem, mas não substitui a "
    "avaliação oficial do ENEM ou de qualquer banca examinadora."
)
_BASE_CSS = (
    "body { font-family: helvetica, sans-serif; font-size: 11px; color: #111827; }"
    "h3 { font-size: 16px; margin: 0 0 8px 0; }"
    "h4 { font-size: 13px; margin: 14px 0 6px 0; }"
    "table { width: 100%; border-collapse: collapse; margin: 6px 0; }"
    "th, td { border: 1px solid #e2e8f0; padding: 4px 6px; text-align: left; vertical-align: top; }"
    "blockquote { margin: 4px 0; font-style: italic; color: #64748b; }"
    "p { margin: 4px 0; }"
)


def pdf_available() -> bool:
    try:
        import pymupdf  # noqa: F401
        return True
    except Exception:
        return False


def filename_for_title(title: str) -> str:
    """Same sanitization convention as question_bank.py's private _filename()
    (alphanumeric + space/hyphen/underscore, truncated to 60 chars) -
    reimplemented here (not imported cross-router) so both export routes
    below share ONE definition instead of two copies."""
    safe = "".join(c if c.isalnum() or c in " -_" else "_" for c in title).strip() or "devolutiva"
    return f"devolutiva-{safe[:60]}.pdf"


def _esc(value: Any) -> str:
    return html.escape(str(value if value is not None else ""), quote=True)


def _hex_to_rgb(hex_color: str) -> tuple[float, float, float]:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i : i + 2], 16) / 255 for i in (0, 2, 4))


def _as_list(value: Any) -> list:
    """Old/malformed ai_output may store a key with the wrong shape (e.g. a
    seed script's dict instead of the contract's list). Degrade to an empty
    list rather than letting a shape mismatch raise here - same philosophy
    as essay_submissions.py's _as_list_or_none, applied at the one place
    that needs it so neither calling route has to repeat it."""
    return value if isinstance(value, list) else []


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


# ---------------------------------------------------------------------------
# render model (pure data, no HTML/PyMuPDF - unit-testable without pymupdf)
# ---------------------------------------------------------------------------


def build_render_model(correction_view: dict) -> dict:
    """Normalize ai_output-shaped data into the fields render_pdf needs.
    Same field reads and fallback rules as essay-report.js's renderRichReport
    - see that file (and the devolutiva-rica spec §3) for the source of
    truth on what happens when a field is missing (old data)."""
    scores = _as_dict(correction_view.get("final_scores"))
    per_competency_raw = _as_dict(scores.get("per_competency"))
    feedback = _as_dict(correction_view.get("final_feedback"))
    rationales = _as_list(correction_view.get("rationales"))
    rationale_by_code = {r.get("competency_code"): r for r in rationales if isinstance(r, dict)}

    competency_rows = []
    for code in _COMPETENCY_CODES:
        rationale = rationale_by_code.get(code)
        if rationale is None:
            continue
        has_split = bool(rationale.get("strengths")) and bool(rationale.get("growth_area"))
        competency_rows.append({
            "code": code,
            "label": _COMPETENCY_LABELS[code],
            "has_split": has_split,
            "strengths": rationale.get("strengths"),
            "growth_area": rationale.get("growth_area"),
            "summary": rationale.get("summary") or "",
        })
    has_any_split = any(row["has_split"] for row in competency_rows)

    return {
        "total": scores.get("total"),
        "points_by_competency": {
            code: _as_dict(per_competency_raw.get(code)).get("points", 0) for code in _COMPETENCY_CODES
        },
        "alerts": [a.get("code", "") for a in _as_list(correction_view.get("alerts")) if isinstance(a, dict)],
        "competency_rows": competency_rows,
        "has_any_split": has_any_split,
        "feedback_strengths": _as_list(feedback.get("strengths")),
        "annotations": _as_list(correction_view.get("annotations")),
        "rewrites": _as_list(correction_view.get("rewrites")),
        "mechanical_occurrences": _as_list(correction_view.get("mechanical_review")),
        "intervention": _as_dict(correction_view.get("intervention")),
        "action_plan_items": _as_list(feedback.get("improvements")),
        "next_essay_strategy": feedback.get("next_essay_strategy") or "",
        "intro_message": correction_view.get("intro_message") or "",
        "closing_message": correction_view.get("closing_message") or "",
    }


# ---------------------------------------------------------------------------
# HTML fragment builders (pure string functions - no PyMuPDF)
# ---------------------------------------------------------------------------


def _competency_bars_html(model: dict) -> str:
    rows = []
    for code in _COMPETENCY_CODES:
        points = model["points_by_competency"].get(code, 0)
        rows.append(
            f'<p><b>{_esc(code)} — {_esc(_COMPETENCY_LABELS[code])}:</b> {points}/200</p>'
        )
    return "".join(rows)


def _competency_table_html(model: dict) -> str:
    if not model["competency_rows"]:
        return "<p><i>Nenhuma avaliação por competência.</i></p>"
    rows_html = []
    for row in model["competency_rows"]:
        header = f'{_esc(row["code"])} — {_esc(row["label"])}'
        if row["has_split"]:
            cells = f'<td>{_esc(row["strengths"])}</td><td>{_esc(row["growth_area"])}</td>'
        else:
            cells = f'<td colspan="2">{_esc(row["summary"])}</td>'
        rows_html.append(f"<tr><th>{header}</th>{cells}</tr>")
    return (
        "<table><thead><tr><th>Competência</th><th>Você já faz bem</th>"
        f'<th>Onde pode avançar</th></tr></thead><tbody>{"".join(rows_html)}</tbody></table>'
    )


def _strengths_fallback_html(model: dict) -> str:
    if model["has_any_split"] or not model["feedback_strengths"]:
        return ""
    items = "".join(f"<li>{_esc(s)}</li>" for s in model["feedback_strengths"])
    return f"<h4>Pontos fortes</h4><ul>{items}</ul>"


def _annotations_html(model: dict) -> str:
    annotations = model["annotations"]
    if not annotations:
        return "<p><i>Nenhuma anotação específica.</i></p>"
    parts = []
    for i, a in enumerate(annotations, start=1):
        if not isinstance(a, dict):
            continue
        anchor = _as_dict(a.get("anchor"))
        quote = anchor.get("quote") or anchor.get("read_text") or ""
        quote_html = f"<blockquote>&ldquo;{_esc(quote)}&rdquo;</blockquote>" if quote else ""
        parts.append(
            f'<div style="margin:6px 0;padding:4px 8px;border-left:3px solid #4f46e5;">'
            f'<b>{i}. {_esc(a.get("letter"))} — {_esc(a.get("competency_code"))}</b>'
            f'<p>{_esc(a.get("short_comment"))}</p>'
            f'<p style="color:#64748b;">{_esc(a.get("long_comment"))}</p>'
            f"{quote_html}</div>"
        )
    return "".join(parts)


def _rewrites_html(model: dict) -> str:
    rewrites = model["rewrites"]
    if not rewrites:
        return ""
    parts = []
    for r in rewrites:
        if not isinstance(r, dict):
            continue
        header = (
            f'<b>{_esc(r.get("letter"))} — {_esc(r.get("competency_code"))}</b>'
            if r.get("letter") and r.get("competency_code") else ""
        )
        parts.append(
            f'<div style="margin:6px 0;padding:4px 8px;border-left:3px solid #06b6d4;">'
            f"{header}"
            f'<p style="color:#64748b;">Trecho original:</p>'
            f'<blockquote>&ldquo;{_esc(r.get("original"))}&rdquo;</blockquote>'
            f'<p style="color:#64748b;">Sugestão de reescrita:</p>'
            f'<blockquote>&ldquo;{_esc(r.get("suggestion"))}&rdquo;</blockquote>'
            f'<p>{_esc(r.get("pedagogical_goal"))}</p>'
            f"</div>"
        )
    return f'<h4>Reescritas sugeridas</h4>{"".join(parts)}'


def _mechanical_reference_html() -> str:
    rows = "".join(
        f'<tr><th>{_esc(m["label"])}</th><td>{_esc(m["description"])}</td></tr>'
        for m in _MECHANICAL_REFERENCE
    )
    return f"<table><thead><tr><th>Categoria</th><th>O que observamos</th></tr></thead><tbody>{rows}</tbody></table>"


def _mechanical_occurrences_html(model: dict) -> str:
    occurrences = model["mechanical_occurrences"]
    if not occurrences:
        return "<p><i>Nenhuma ocorrência mecânica confirmada nesta redação.</i></p>"
    parts = []
    for m in occurrences:
        if not isinstance(m, dict):
            continue
        parts.append(
            f'<div style="margin:6px 0;padding:4px 8px;border-left:3px solid #f59e0b;">'
            f'<b>{_esc(m.get("category"))}</b>'
            f'<blockquote>&ldquo;{_esc(m.get("excerpt"))}&rdquo;</blockquote>'
            f'<p>Forma sugerida: {_esc(m.get("suggested_form"))}</p>'
            f'<p style="color:#64748b;">{_esc(m.get("rule_explanation"))}</p>'
            f"</div>"
        )
    return "".join(parts)


def _intervention_html(model: dict) -> str:
    intervention = model["intervention"]

    def _row(label: str, key: str) -> str:
        value = intervention.get(key)
        mark = "✓" if value else "○"
        return f'<p>{mark} {label}: {_esc(value or "—")}</p>'

    rows = (
        _row("Agente", "agente") + _row("Ação", "acao") + _row("Meio/modo", "meio_modo")
        + _row("Finalidade", "finalidade") + _row("Detalhamento", "detalhamento")
    )
    respects = intervention.get("respeita_direitos_humanos")
    notice = (
        '<p style="color:#10b981;">✓ Respeita os direitos humanos</p>' if respects
        else '<p style="color:#ef4444;">⚠ Atenção: verificar respeito aos direitos humanos</p>'
    )
    return rows + notice


def _action_plan_html(model: dict) -> str:
    items = model["action_plan_items"]
    if not items:
        return "<p><i>Nenhum ponto de melhoria registrado.</i></p>"
    return "<ol>" + "".join(f"<li>{_esc(s)}</li>" for s in items) + "</ol>"


def _highlighted_text_html(canonical_text: str, annotations: list) -> str:
    """Port of essay-annotations.js's renderHighlightedText: sort by start,
    slice, wrap in <mark>, skip overlaps."""
    text = canonical_text or ""
    items = []
    for i, a in enumerate(annotations, start=1):
        if not isinstance(a, dict):
            continue
        anchor = _as_dict(a.get("anchor"))
        start, end = anchor.get("start"), anchor.get("end")
        if (
            a.get("evidence_kind") == "GLOBAL"
            or anchor.get("type") != "TEXT_OFFSET"
            or not isinstance(start, int) or not isinstance(end, int)
            or start < 0 or end <= start or end > len(text)
        ):
            continue
        items.append((start, end, i, a.get("competency_code", "C1")))
    items.sort(key=lambda t: t[0])

    out = []
    cursor = 0
    for start, end, number, code in items:
        if start < cursor:
            continue
        out.append(_esc(text[cursor:start]))
        color = _COMPETENCY_COLORS.get(code, "#eef2ff")
        out.append(
            f'<mark style="background-color:{color};">{_esc(text[start:end])}'
            f"<sup>{number}</sup></mark>"
        )
        cursor = end
    out.append(_esc(text[cursor:]))
    return "".join(out)


def _group_image_annotations_by_page(annotations: list) -> dict[int, list[tuple[int, dict]]]:
    """(page_number -> [(global 1-based number, annotation), ...]) - the
    global number matches _annotations_html's numbering (index across ALL
    annotations, not per-page), so the number drawn on the photo overlay
    matches the number in the "Anotações" list below it."""
    grouped: dict[int, list[tuple[int, dict]]] = {}
    for i, a in enumerate(annotations, start=1):
        if not isinstance(a, dict):
            continue
        anchor = _as_dict(a.get("anchor"))
        if anchor.get("type") != "IMAGE_REGION":
            continue
        page_number = anchor.get("page", 1)
        grouped.setdefault(page_number, []).append((i, a))
    return grouped


# ---------------------------------------------------------------------------
# document assembly (PyMuPDF)
# ---------------------------------------------------------------------------


def _document_before_html(model: dict, *, title: str | None) -> str:
    title_html = f"<h3>{_esc(title)}</h3>" if title else ""
    intro_html = (
        f'<p style="font-style:italic;">{_esc(model["intro_message"])}</p>'
        if model["intro_message"] else ""
    )
    total = model["total"]
    total_text = f"{total} / 1000" if total is not None else "—"
    alerts_html = (
        " ".join(
            f'<span style="background:#ecfeff;padding:2px 6px;border-radius:4px;">{_esc(a)}</span>'
            for a in model["alerts"]
        )
        if model["alerts"] else ""
    )
    return (
        f"{title_html}{intro_html}"
        f'<p style="font-size:16px;font-weight:bold;">Nota total: {total_text}</p>'
        f"{alerts_html}"
        "<h4>Notas por competência</h4>" + _competency_bars_html(model)
        + "<h4>O que você já faz bem e onde pode avançar</h4>" + _competency_table_html(model)
        + _strengths_fallback_html(model)
    )


def _document_after_html(model: dict) -> str:
    next_essay_html = (
        f'<h4>Próxima redação</h4><p>{_esc(model["next_essay_strategy"])}</p>'
        if model["next_essay_strategy"] else ""
    )
    closing_html = (
        f'<p style="font-style:italic;">{_esc(model["closing_message"])}</p>'
        if model["closing_message"] else ""
    )
    return (
        "<h4>Anotações</h4>" + _annotations_html(model)
        + _rewrites_html(model)
        + "<h4>Revisão de domínio da norma padrão (C1)</h4>"
        + _mechanical_reference_html() + _mechanical_occurrences_html(model)
        + "<h4>Competência 5 — Proposta de intervenção</h4>" + _intervention_html(model)
        + "<h4>Plano de ação</h4>" + _action_plan_html(model)
        + next_essay_html + closing_html
        + f'<p style="font-size:9px;color:#64748b;margin-top:16px;">{_esc(_TRANSPARENCY_NOTICE)}</p>'
    )


def _story_pdf_bytes(body_html: str):
    import pymupdf

    story = pymupdf.Story(html=f"<html><body>{body_html}</body></html>", user_css=_BASE_CSS)
    mediabox = pymupdf.paper_rect("a4")
    where = mediabox + (36, 36, -36, -36)
    buf = io.BytesIO()
    writer = pymupdf.DocumentWriter(buf)
    more = 1
    while more:
        dev = writer.begin_page(mediabox)
        more, _filled = story.place(where)
        story.draw(dev)
        writer.end_page()
    writer.close()
    return buf.getvalue()


def render_pdf(
    model: dict,
    *,
    title: str | None,
    anchor_mode: str,
    canonical_text: str | None = None,
    page_image_paths: list[str] | None = None,
) -> bytes:
    import pymupdf

    if anchor_mode == "TEXT_OFFSET":
        highlighted = _highlighted_text_html(canonical_text or "", model["annotations"])
        body = (
            _document_before_html(model, title=title)
            + "<h4>Sua redação</h4>" + f"<div>{highlighted}</div>"
            + _document_after_html(model)
        )
        return _story_pdf_bytes(body)

    # IMAGE_REGION, no pages at all: still a single Story pass, same "Nenhuma
    # página enviada." text the frontend already uses in this case.
    if not page_image_paths:
        body = (
            _document_before_html(model, title=title)
            + "<h4>Sua redação</h4><p><i>Nenhuma página enviada.</i></p>"
            + _document_after_html(model)
        )
        return _story_pdf_bytes(body)

    # IMAGE_REGION with pages: Story (before) + manually-drawn image pages
    # with annotation overlays + Story (after), merged into one document.
    before_bytes = _story_pdf_bytes(_document_before_html(model, title=title) + "<h4>Sua redação</h4>")
    after_bytes = _story_pdf_bytes(_document_after_html(model))
    page_annotations = _group_image_annotations_by_page(model["annotations"])

    final_doc = pymupdf.open()
    final_doc.insert_pdf(pymupdf.open(stream=before_bytes, filetype="pdf"))

    mediabox = pymupdf.paper_rect("a4")
    margin = 40.0
    for page_number, path in enumerate(page_image_paths, start=1):
        src = pymupdf.Pixmap(path)
        page = final_doc.new_page(width=mediabox.width, height=mediabox.height)
        max_w, max_h = mediabox.width - 2 * margin, mediabox.height - 2 * margin
        scale = min(max_w / src.width, max_h / src.height)
        rect = pymupdf.Rect(margin, margin, margin + src.width * scale, margin + src.height * scale)
        page.insert_image(rect, filename=path)
        for number, annotation in page_annotations.get(page_number, []):
            anchor = _as_dict(annotation.get("anchor"))
            x, y = anchor.get("x", 0), anchor.get("y", 0)
            w, h = anchor.get("width", 0), anchor.get("height", 0)
            overlay = pymupdf.Rect(
                margin + x * scale, margin + y * scale,
                margin + (x + w) * scale, margin + (y + h) * scale,
            )
            rgb = _hex_to_rgb(_COMPETENCY_COLORS.get(annotation.get("competency_code"), "#eef2ff"))
            page.draw_rect(overlay, color=rgb, width=2)
            page.insert_text((overlay.x0 + 2, overlay.y0 + 10), str(number), fontsize=8, color=rgb)

    final_doc.insert_pdf(pymupdf.open(stream=after_bytes, filetype="pdf"))
    out = final_doc.tobytes()
    final_doc.close()
    return out


__all__ = ["build_render_model", "filename_for_title", "pdf_available", "render_pdf"]
```

- [ ] **Step 2: Run a quick manual sanity check (no test file yet — that's Task 6)**

Run: `.venv/bin/python -c "
from agente_ia_edu.services.essay_pdf_export import build_render_model, render_pdf, pdf_available
assert pdf_available()
model = build_render_model({
    'final_scores': {'total': 680, 'per_competency': {'C1': {'points': 160}}},
    'final_feedback': {'strengths': ['ok'], 'improvements': ['melhorar'], 'next_essay_strategy': 'praticar'},
    'annotations': [], 'rewrites': [], 'alerts': [], 'intervention': {'respeita_direitos_humanos': True},
    'rationales': [], 'intro_message': 'Oi', 'closing_message': 'Tchau', 'mechanical_review': [],
})
pdf_bytes = render_pdf(model, title='Teste', anchor_mode='TEXT_OFFSET', canonical_text='Um texto qualquer.')
assert pdf_bytes.startswith(b'%PDF-')
assert len(pdf_bytes) > 500
print('OK', len(pdf_bytes), 'bytes')
"`
Expected: prints `OK <N> bytes` with no traceback.

- [ ] **Step 3: Commit**

```bash
git add src/agente_ia_edu/services/essay_pdf_export.py
git commit -m "feat(redacao): add PDF export rendering module for the devolutiva"
```

---

### Task 2: Student export route

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_submissions.py`

**Interfaces:**
- Consumes: `pdf_available`, `build_render_model`, `render_pdf`, `filename_for_title` from Task 1's `essay_pdf_export.py`. `_authorize_student`, `_resolve_enrollment_or_403`, `_submission_for_own_school_or_403`, `_as_list_or_none` (all pre-existing in this file, unmodified).
- Produces: `GET /api/v1/student/essay-submissions/{essay_submission_id}/correction/export.pdf`, consumed by Task 4 (`essay.js`).

- [ ] **Step 1: Add the import**

Find (around line 27):
```python
from ...db.models import EssayCorrection, EssayPrompt, EssaySubmission, EssaySubmissionPage, PromptAssignment
```

This line already imports everything needed from `db.models`. Add the new service import right after the existing `essay_correction`/`essay_submission` service imports (around line 30-31):

Find:
```python
from ...services.essay_correction import EssayCorrectionService
from ...services.essay_submission import EssayResubmissionBlockedError, EssaySubmissionService
```

Replace with:
```python
from ...services.essay_correction import EssayCorrectionService
from ...services.essay_pdf_export import build_render_model, filename_for_title, pdf_available, render_pdf
from ...services.essay_submission import EssayResubmissionBlockedError, EssaySubmissionService
```

Also, `Response` is needed alongside the already-imported `FileResponse`. Find:
```python
from fastapi.responses import FileResponse
```

Replace with:
```python
from fastapi.responses import FileResponse, Response
```

- [ ] **Step 2: Add the route**

Add this new route immediately after `get_essay_submission_correction` (right after its closing, before the `class MySubmissionSummary` block that follows it — i.e., insert right after the function whose last line is `mechanical_review=ai_output.get("mechanical_review"),\n        )` and its closing blank line):

```python
@essay_submissions_router.get("/{essay_submission_id}/correction/export.pdf")
async def export_essay_submission_correction_pdf(
    essay_submission_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    if not pdf_available():
        raise HTTPException(status_code=503, detail="PDF export requires the 'pymupdf' package")
    async with session_factory() as session:
        context = await _authorize_student(identity, session)
        school_id = uuid.UUID(str(context.school_id))
        enrollment = await _resolve_enrollment_or_403(
            session, school_id=school_id, external_user_id=identity.external_user_id
        )
        submission = await _submission_for_own_school_or_403(
            session, essay_submission_id=essay_submission_id, school_id=school_id,
            student_id=enrollment.student_id,
        )
        correction = await session.scalar(
            select(EssayCorrection).where(EssayCorrection.essay_submission_id == submission.id)
        )
        if correction is None or correction.status != "APPROVED":
            raise HTTPException(status_code=404, detail="No approved correction to export yet.")

        prompt_title = (
            await session.execute(
                select(EssayPrompt.title)
                .join(PromptAssignment, PromptAssignment.essay_prompt_id == EssayPrompt.id)
                .where(PromptAssignment.id == submission.prompt_assignment_id)
            )
        ).scalar_one_or_none() or "Redação"

        ai_output = correction.ai_output or {}
        model = build_render_model({
            "final_scores": correction.final_scores,
            "final_feedback": correction.final_feedback,
            "annotations": ai_output.get("annotations"),
            "rewrites": ai_output.get("rewrites"),
            "alerts": ai_output.get("alerts"),
            "intervention": ai_output.get("intervention"),
            "rationales": ai_output.get("rationales"),
            "intro_message": ai_output.get("intro_message"),
            "closing_message": ai_output.get("closing_message"),
            "mechanical_review": ai_output.get("mechanical_review"),
        })

        page_image_paths = None
        if submission.anchor_mode == "IMAGE_REGION":
            page_image_paths = list(
                (
                    await session.execute(
                        select(EssaySubmissionPage.storage_uri)
                        .where(EssaySubmissionPage.essay_submission_id == submission.id)
                        .order_by(EssaySubmissionPage.page_number)
                    )
                ).scalars().all()
            )

        pdf_bytes = render_pdf(
            model, title=prompt_title, anchor_mode=submission.anchor_mode,
            canonical_text=submission.canonical_text, page_image_paths=page_image_paths,
        )
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename_for_title(prompt_title)}"'},
    )
```

Note: this route does NOT use `_as_list_or_none` — `build_render_model` (Task 1) already coerces malformed shapes internally, so passing `ai_output.get(...)` straight through is safe.

- [ ] **Step 3: Manual sanity check**

Run: `.venv/bin/python -c "import agente_ia_edu.api.routes.essay_submissions"` — expected: no import error (confirms the new imports resolve and the module still parses).

- [ ] **Step 4: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_submissions.py
git commit -m "feat(redacao): add student PDF export route for the devolutiva"
```

---

### Task 3: Teacher export route

**Files:**
- Modify: `src/agente_ia_edu/api/routes/essay_corrections.py`

**Interfaces:**
- Consumes: same four functions from Task 1. `_authorize`, `_correction_for_own_school_or_403` (pre-existing, unmodified).
- Produces: `GET /api/v1/teacher/essay-corrections/{essay_correction_id}/export.pdf`, consumed by Task 5 (`essay-review.js`).

- [ ] **Step 1: Add the imports**

Find (around line 17):
```python
from fastapi.responses import FileResponse
```

Replace with:
```python
from fastapi.responses import FileResponse, Response
```

Find (around line 26):
```python
from ...services.essay_correction import EssayCorrectionService
```

Replace with:
```python
from ...services.essay_correction import EssayCorrectionService
from ...services.essay_pdf_export import build_render_model, filename_for_title, pdf_available, render_pdf
```

- [ ] **Step 2: Add the route**

Add this new route immediately after `get_essay_correction_submission_content` (right after its closing, before `get_essay_correction_page_image`):

```python
@essay_corrections_router.get("/{essay_correction_id}/export.pdf")
async def export_essay_correction_pdf(
    essay_correction_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
):
    if not pdf_available():
        raise HTTPException(status_code=503, detail="PDF export requires the 'pymupdf' package")
    async with session_factory() as session:
        school_id = await _authorize(identity, session)
        correction = await _correction_for_own_school_or_403(
            session, essay_correction_id=essay_correction_id, school_id=school_id
        )
        if correction.status not in ("APPROVED", "PENDING_REVIEW"):
            raise HTTPException(status_code=404, detail="No publishable correction to export.")

        submission = await session.get(EssaySubmission, correction.essay_submission_id)
        prompt_title = (
            await session.execute(
                select(EssayPrompt.title)
                .join(PromptAssignment, PromptAssignment.essay_prompt_id == EssayPrompt.id)
                .where(PromptAssignment.id == submission.prompt_assignment_id)
            )
        ).scalar_one_or_none() or "Redação"

        ai_output = correction.ai_output or {}
        model = build_render_model({
            "final_scores": correction.final_scores,
            "final_feedback": correction.final_feedback,
            "annotations": ai_output.get("annotations"),
            "rewrites": ai_output.get("rewrites"),
            "alerts": ai_output.get("alerts"),
            "intervention": ai_output.get("intervention"),
            "rationales": ai_output.get("rationales"),
            "intro_message": ai_output.get("intro_message"),
            "closing_message": ai_output.get("closing_message"),
            "mechanical_review": ai_output.get("mechanical_review"),
        })

        page_image_paths = None
        if submission.anchor_mode == "IMAGE_REGION":
            page_image_paths = list(
                (
                    await session.execute(
                        select(EssaySubmissionPage.storage_uri)
                        .where(EssaySubmissionPage.essay_submission_id == submission.id)
                        .order_by(EssaySubmissionPage.page_number)
                    )
                ).scalars().all()
            )

        pdf_bytes = render_pdf(
            model, title=prompt_title, anchor_mode=submission.anchor_mode,
            canonical_text=submission.canonical_text, page_image_paths=page_image_paths,
        )
    return Response(
        content=pdf_bytes, media_type="application/pdf",
        headers={"Content-Disposition": f'attachment; filename="{filename_for_title(prompt_title)}"'},
    )
```

Note `PromptAssignment` needs to be in scope — it already is (imported at line 23: `from ...db.models import EssayCorrection, EssayPrompt, EssaySubmission, EssaySubmissionPage, Person, PromptAssignment, Student`, confirmed present already).

- [ ] **Step 3: Manual sanity check**

Run: `.venv/bin/python -c "import agente_ia_edu.api.routes.essay_corrections"` — expected: no import error.

- [ ] **Step 4: Commit**

```bash
git add src/agente_ia_edu/api/routes/essay_corrections.py
git commit -m "feat(redacao): add teacher PDF export route for the devolutiva"
```

---

### Task 4: Student portal — "Exportar PDF" link

**Files:**
- Modify: `src/agente_ia_edu/web/essay.js`

**Interfaces:**
- Consumes: Task 2's route (link `href` only — no `fetch`, matching `question-bank.js`'s established `<a href="...">Exportar PDF</a>` pattern exactly, since production identity resolution is cookie/session-based via the host's real `ExternalIdentityProvider`, not the dev-only `Authorization: Bearer` header scheme a plain anchor click can't send).

- [ ] **Step 1: Add the link**

Find (in `renderApprovedDevolutiva`, around line 431-435):
```javascript
    container.innerHTML = `
      <div class="card">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        ${reportHtml}
      </div>`;
```

Replace with:
```javascript
    const submissionId = prompt.my_submission.id;
    container.innerHTML = `
      <div class="card">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar</button>
        <a class="btn btn-secondary" href="/api/v1/student/essay-submissions/${submissionId}/correction/export.pdf">Exportar PDF</a>
        ${reportHtml}
      </div>`;
```

- [ ] **Step 2: Manual check**

Run: `node --check src/agente_ia_edu/web/essay.js` — expected: no output (exit 0).

- [ ] **Step 3: Commit**

```bash
git add src/agente_ia_edu/web/essay.js
git commit -m "feat(redacao): add Exportar PDF link to the student devolutiva view"
```

---

### Task 5: Teacher portal — "Exportar PDF" link

**Files:**
- Modify: `src/agente_ia_edu/web/essay-review.js`

**Interfaces:**
- Consumes: Task 3's route. Must only render when `isPending || correction.status === 'APPROVED'` — NOT the broader `showsContent`/`isTerminal` (those also cover `REJECTED`, which is not exportable per this leva's spec).

- [ ] **Step 1: Add the link**

Find (around line 371, right before `actionsHtml`'s definition):
```javascript
    const actionsHtml = isPending ? `
```

Insert immediately before it:
```javascript
    const exportHtml = (isPending || correction.status === 'APPROVED')
      ? `<a class="btn btn-secondary" href="/api/v1/teacher/essay-corrections/${correctionId}/export.pdf">Exportar PDF</a>`
      : '';

    const actionsHtml = isPending ? `
```

Find (around line 384, the container.innerHTML template):
```javascript
    container.innerHTML = `
      ${renderTabs('queue')}
      <div class="card tm-detail-grid">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar à fila</button>
        ${failureHtml}
        ${alertsHtml}
        ${scoresFeedbackHtml}
        <div class="tm-form-actions">${actionsHtml}</div>
        <p id="er-review-msg" class="tm-msg" hidden></p>
      </div>`;
```

Replace with:
```javascript
    container.innerHTML = `
      ${renderTabs('queue')}
      <div class="card tm-detail-grid">
        <button class="btn btn-secondary" type="button" data-back>&larr; Voltar à fila</button>
        ${failureHtml}
        ${exportHtml}
        ${alertsHtml}
        ${scoresFeedbackHtml}
        <div class="tm-form-actions">${actionsHtml}</div>
        <p id="er-review-msg" class="tm-msg" hidden></p>
      </div>`;
```

`correctionId` is already the function's own first parameter (`function renderReviewPanel(correctionId, returnStatus)`), already in scope — no new variable needed.

- [ ] **Step 2: Manual check**

Run: `node --check src/agente_ia_edu/web/essay-review.js` — expected: no output (exit 0).

- [ ] **Step 3: Commit**

```bash
git add src/agente_ia_edu/web/essay-review.js
git commit -m "feat(redacao): add Exportar PDF link to the teacher review panel"
```

---

### Task 6: Tests — `essay_pdf_export.py`

**Files:**
- Create: `tests/test_essay_pdf_export.py`

**Interfaces:**
- Consumes: Task 1's public functions only.

- [ ] **Step 1: Write the test file**

```python
import unittest

from agente_ia_edu.services.essay_pdf_export import (
    build_render_model,
    filename_for_title,
    pdf_available,
    render_pdf,
)


def _full_correction_view(**overrides) -> dict:
    view = {
        "final_scores": {
            "total": 680,
            "per_competency": {
                "C1": {"points": 160}, "C2": {"points": 160}, "C3": {"points": 120},
                "C4": {"points": 160}, "C5": {"points": 80},
            },
        },
        "final_feedback": {
            "strengths": ["Boa argumentação inicial."],
            "improvements": ["Revisar concordância verbal."],
            "next_essay_strategy": "Praticar coesão entre parágrafos.",
        },
        "annotations": [
            {
                "letter": "A", "competency_code": "C1", "evidence_kind": "LOCALIZED",
                "anchor": {"type": "TEXT_OFFSET", "start": 0, "end": 10, "quote": "As redes s"},
                "short_comment": "curto", "long_comment": "longo",
            }
        ],
        "rewrites": [
            {
                "letter": "A", "competency_code": "C1",
                "original": "As redes sociais tem", "suggestion": "As redes sociais têm",
                "pedagogical_goal": "Corrigir concordância.",
            }
        ],
        "alerts": [],
        "intervention": {
            "agente": "poder público", "acao": "criar programa", "meio_modo": "parcerias",
            "finalidade": "reduzir o problema", "detalhamento": None,
            "respeita_direitos_humanos": True,
        },
        "rationales": [
            {
                "competency_code": "C1", "summary": "ok",
                "strengths": "boa norma", "growth_area": "revisar crase",
            }
        ],
        "intro_message": "Olá! Vamos ver sua redação.",
        "closing_message": "Continue praticando!",
        "mechanical_review": [
            {
                "category": "CRASE", "excerpt": "de acordo a pesquisa",
                "suggested_form": "de acordo com a pesquisa", "rule_explanation": "regência de 'de acordo'.",
            }
        ],
    }
    view.update(overrides)
    return view


class EssayPdfExportTests(unittest.TestCase):
    def test_pdf_available_is_true_in_this_environment(self):
        self.assertTrue(pdf_available())

    def test_filename_for_title_sanitizes_and_prefixes(self):
        self.assertEqual(filename_for_title("Redação: Redes Sociais!"), "devolutiva-Redação_ Redes Sociais_.pdf")

    def test_filename_for_title_falls_back_when_title_is_empty_after_sanitizing(self):
        self.assertEqual(filename_for_title("???"), "devolutiva-___.pdf")

    def test_build_render_model_full_shape(self):
        model = build_render_model(_full_correction_view())
        self.assertEqual(model["total"], 680)
        self.assertEqual(model["points_by_competency"]["C1"], 160)
        self.assertEqual(len(model["competency_rows"]), 1)
        self.assertTrue(model["competency_rows"][0]["has_split"])
        self.assertTrue(model["has_any_split"])
        self.assertEqual(model["intro_message"], "Olá! Vamos ver sua redação.")
        self.assertEqual(model["closing_message"], "Continue praticando!")
        self.assertEqual(len(model["mechanical_occurrences"]), 1)

    def test_build_render_model_tolerates_missing_new_fields(self):
        """Old data: rationales items only have summary, no strengths/growth_area;
        no intro_message/closing_message/mechanical_review keys at all."""
        view = _full_correction_view(
            rationales=[{"competency_code": "C1", "summary": "resumo antigo"}],
            intro_message=None, closing_message=None, mechanical_review=None,
        )
        model = build_render_model(view)
        self.assertFalse(model["competency_rows"][0]["has_split"])
        self.assertEqual(model["competency_rows"][0]["summary"], "resumo antigo")
        self.assertFalse(model["has_any_split"])
        self.assertEqual(model["intro_message"], "")
        self.assertEqual(model["closing_message"], "")
        self.assertEqual(model["mechanical_occurrences"], [])

    def test_build_render_model_tolerates_malformed_shapes(self):
        """A dict where the contract expects a list (matches a real dev/demo
        data artifact found in this project) must not raise."""
        view = _full_correction_view(rationales={"C1": "x"}, annotations={"weird": True})
        model = build_render_model(view)
        self.assertEqual(model["competency_rows"], [])
        self.assertEqual(model["annotations"], [])

    def test_render_pdf_text_offset_produces_valid_pdf_bytes(self):
        model = build_render_model(_full_correction_view())
        pdf_bytes = render_pdf(
            model, title="Impactos das redes sociais", anchor_mode="TEXT_OFFSET",
            canonical_text="As redes sociais tem transformado a opinião pública.",
        )
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_bytes), 1000)

    def test_render_pdf_image_region_with_no_pages_produces_valid_pdf_bytes(self):
        model = build_render_model(_full_correction_view())
        pdf_bytes = render_pdf(model, title="Foto de teste", anchor_mode="IMAGE_REGION", page_image_paths=None)
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))
        self.assertGreater(len(pdf_bytes), 500)

    def test_render_pdf_omits_optional_sections_when_absent(self):
        """No intro_message, no closing_message, no rewrites, empty
        mechanical_review - the PDF must still render (no KeyError/None
        formatting artifact) exactly like essay-report.js's fallback."""
        view = _full_correction_view(
            intro_message=None, closing_message=None, rewrites=[], mechanical_review=[],
        )
        model = build_render_model(view)
        pdf_bytes = render_pdf(
            model, title="Sem campos novos", anchor_mode="TEXT_OFFSET",
            canonical_text="Um texto qualquer para o teste.",
        )
        self.assertTrue(pdf_bytes.startswith(b"%PDF-"))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run the tests**

Run: `.venv/bin/python -m pytest tests/test_essay_pdf_export.py -v`
Expected: all PASS.

- [ ] **Step 3: Commit**

```bash
git add tests/test_essay_pdf_export.py
git commit -m "test(redacao): cover essay_pdf_export's model builder and PDF rendering"
```

---

### Task 7: Tests — export routes

**Files:**
- Modify: `tests/test_frontend_r_student_essay_correction_route.py` (student export route — already has exactly the fixtures needed: `_seed_submission(code)`, `_add_correction(submission_id, *, status, with_content)`, `_as(user)`, class `StudentEssayCorrectionRouteTests`. Codes `"1"`-`"12"` are already used in this file — new tests below start at `"13"`.)
- Modify: `tests/test_r3_essay_corrections_routes.py` (teacher export route — already has exactly the fixtures needed: `_seed_pending_correction(code, *, school_id=None, status="PENDING_REVIEW") -> (correction_id, school_id)`, `_as(user)`, class `EssayCorrectionsRoutesTests`, `patch` already imported. Codes `"1"`-`"14"` are already used in this file — new tests below start at `"20"`.)

**Interfaces:**
- Consumes: Task 2's route (student file) and Task 3's route (teacher file).

Both existing files' established fixtures are a direct, better-fitting match for these tests than any new scaffolding — reuse them exactly as the tasks below specify. Do not create a new test file for this task.

- [ ] **Step 1: Add student export-route tests**

In `tests/test_frontend_r_student_essay_correction_route.py`, add `from unittest.mock import patch` to the imports (not currently imported in this file), then append these five test methods to `StudentEssayCorrectionRouteTests` (after `test_rejected_resubmission_blocked_in_avaliativo_mode`, the last existing test):

```python
    def test_export_pdf_approved_returns_pdf_bytes(self):
        submission_id = self._seed_submission("13")
        self._add_correction(submission_id, status="APPROVED", with_content=True)
        self._as("student_13")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction/export.pdf")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.headers["content-type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF-"))

    def test_export_pdf_pending_review_is_404(self):
        submission_id = self._seed_submission("14")
        self._add_correction(submission_id, status="PENDING_REVIEW", with_content=False)
        self._as("student_14")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction/export.pdf")
        self.assertEqual(resp.status_code, 404)

    def test_export_pdf_rejected_is_404(self):
        submission_id = self._seed_submission("15")
        self._add_correction(submission_id, status="REJECTED", with_content=True)
        self._as("student_15")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction/export.pdf")
        self.assertEqual(resp.status_code, 404)

    def test_export_pdf_another_students_submission_is_403(self):
        submission_id = self._seed_submission("16")
        self._add_correction(submission_id, status="APPROVED", with_content=True)
        self._seed_submission("17")
        self._as("student_17")
        resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction/export.pdf")
        self.assertEqual(resp.status_code, 403)

    def test_export_pdf_503_when_pymupdf_unavailable(self):
        submission_id = self._seed_submission("18")
        self._add_correction(submission_id, status="APPROVED", with_content=True)
        self._as("student_18")
        with patch("agente_ia_edu.api.routes.essay_submissions.pdf_available", return_value=False):
            resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/correction/export.pdf")
        self.assertEqual(resp.status_code, 503)
```

- [ ] **Step 2: Run the student export tests**

Run: `.venv/bin/python -m pytest tests/test_frontend_r_student_essay_correction_route.py -v`
Expected: all PASS, including every pre-existing test in the file (13 previously + 5 new = 18 total).

- [ ] **Step 3: Commit**

```bash
git add tests/test_frontend_r_student_essay_correction_route.py
git commit -m "test(redacao): cover the student PDF export route"
```

- [ ] **Step 4: Add teacher export-route tests**

In `tests/test_r3_essay_corrections_routes.py`, append these six test methods to `EssayCorrectionsRoutesTests` (anywhere after `_seed_pending_correction`'s definition — e.g. at the end of the class):

```python
    def test_export_pdf_approved_returns_pdf_bytes(self):
        correction_id, _school_id = self._seed_pending_correction("20", status="APPROVED")
        self._as("teacher_20")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/export.pdf")
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertEqual(resp.headers["content-type"], "application/pdf")
        self.assertTrue(resp.content.startswith(b"%PDF-"))

    def test_export_pdf_pending_review_returns_pdf_bytes(self):
        """Unlike the student route, PENDING_REVIEW is exportable for the teacher."""
        correction_id, _school_id = self._seed_pending_correction("21")
        self._as("teacher_21")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/export.pdf")
        self.assertEqual(resp.status_code, 200, resp.text)

    def test_export_pdf_needs_review_is_404(self):
        correction_id, _school_id = self._seed_pending_correction("22", status="NEEDS_REVIEW")
        self._as("teacher_22")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/export.pdf")
        self.assertEqual(resp.status_code, 404)

    def test_export_pdf_rejected_is_404(self):
        correction_id, _school_id = self._seed_pending_correction("23", status="REJECTED")
        self._as("teacher_23")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/export.pdf")
        self.assertEqual(resp.status_code, 404)

    def test_export_pdf_from_another_school_is_403(self):
        correction_id, _school_id = self._seed_pending_correction("24", status="APPROVED")
        self._seed_pending_correction("25")
        self._as("teacher_25")
        resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/export.pdf")
        self.assertEqual(resp.status_code, 403)

    def test_export_pdf_503_when_pymupdf_unavailable(self):
        correction_id, _school_id = self._seed_pending_correction("26", status="APPROVED")
        self._as("teacher_26")
        with patch("agente_ia_edu.api.routes.essay_corrections.pdf_available", return_value=False):
            resp = self.client.get(f"/api/v1/teacher/essay-corrections/{correction_id}/export.pdf")
        self.assertEqual(resp.status_code, 503)
```

`_seed_pending_correction`'s `status` parameter already accepts `"APPROVED"`/`"REJECTED"` directly (its `else` branch — anything other than `"NEEDS_REVIEW"` — builds a full correction record with `status=status`), and its default-path teacher identity is already `f"teacher_{code}"` (matching `"teacher_20"`, `"teacher_21"`, etc. above) — no changes to that helper are needed.

- [ ] **Step 5: Run the teacher export tests**

Run: `.venv/bin/python -m pytest tests/test_r3_essay_corrections_routes.py -v`
Expected: all PASS, including every pre-existing test in the file.

- [ ] **Step 6: Commit**

```bash
git add tests/test_r3_essay_corrections_routes.py
git commit -m "test(redacao): cover the teacher PDF export route"
```

---

### Task 8: Verification (full suite + manual browser check, no code changes)

**Files:** none modified.

- [ ] **Step 1: Run the full backend test suite**

Run: `.venv/bin/python -m pytest -q`
Expected: 0 failed, 0 errors (same baseline as the devolutiva-rica leva's own final verification — reuse the disposable dev Postgres on port 5433, and if this is a fresh worktree, restore the gitignored fixtures it needs first: `.env`, `var/inep-pilot/*`, `scratchpad_question_text_9u2.txt`, matching the exact recovery already done once this session).

- [ ] **Step 2: Manual browser check — student portal**

Start the dev server via the platform's preview tool from THIS worktree specifically (not the main checkout — confirm via `lsof -p <pid> | grep cwd` if unsure, exactly as this session already had to catch once). Open the student portal, navigate to an `APPROVED` essay devolutiva, click "Exportar PDF", and confirm a real PDF downloads (check via `read_network_requests` that the response's `content-type` is `application/pdf` and its body starts with `%PDF-`). If the submission is `IMAGE_REGION`, confirm the downloaded PDF actually contains an image page with a colored overlay box (open the PDF file, or check its byte size is meaningfully larger than a text-only export).

- [ ] **Step 3: Manual browser check — teacher portal**

Open the teacher review panel for a `PENDING_REVIEW` correction and for an `APPROVED` one; confirm "Exportar PDF" appears in both and downloads correctly. Open the review panel for a `REJECTED` correction and confirm "Exportar PDF" does NOT appear.

- [ ] **Step 4: Check browser console and network for errors**

`read_console_messages` (onlyErrors: true) on both pages. Expected: no errors.

- [ ] **Step 5: Report readiness**

No commit in this task. If Steps 1-4 are clean, the branch is ready for `finishing-a-development-branch` — per this session's established, platform-enforced boundary, do not merge/push/delete the branch unattended. Stop and hand the integration decision back for a live response.
