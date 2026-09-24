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
            f'<p><b>{_esc(code)} — {_esc(_COMPETENCY_LABELS[code])}:</b> {_esc(points)}/200</p>'
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
        f'<p style="font-size:16px;font-weight:bold;">Nota total: {_esc(total_text)}</p>'
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
