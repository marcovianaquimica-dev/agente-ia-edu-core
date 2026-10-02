import os
import tempfile
import unittest

from agente_ia_edu.services.essay_pdf_export import (
    _annotations_html,
    _competency_table_html,
    _mechanical_occurrences_html,
    _rewrites_html,
    _strengths_fallback_html,
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
        self.assertEqual(filename_for_title("   "), "devolutiva-devolutiva.pdf")

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
        pdf_bytes = render_pdf(model, title="Foto de teste", anchor_mode="IMAGE_REGION", page_images=None)
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

    def test_annotations_and_rewrites_html_show_number_not_letter(self):
        """Product ask (2026-09): the header shown next to each annotation and
        each suggested rewrite must show the same number as the numbered
        marker circle (e.g. '1 — C1'), not the engine's internal letter
        (e.g. 'A — C1'). The `letter` field itself is untouched in the data
        - annotations[i]["letter"] is still 'A' below - only the rendered
        HTML changes."""
        model = build_render_model(_full_correction_view())
        self.assertEqual(model["annotations"][0]["letter"], "A")
        self.assertEqual(model["rewrites"][0]["letter"], "A")

        annotations_html = _annotations_html(model)
        self.assertIn("1 — C1", annotations_html)
        self.assertNotIn("A — C1", annotations_html)

        rewrites_html = _rewrites_html(model)
        self.assertIn("1 — C1", rewrites_html)
        self.assertNotIn("A — C1", rewrites_html)

    def test_render_pdf_image_region_draws_overlay_only_for_localized_annotation(self):
        """The main job of render_pdf's IMAGE_REGION-with-pages branch: a real
        page image gets embedded, a LOCALIZED IMAGE_REGION annotation gets an
        overlay box drawn on it, and a GLOBAL IMAGE_REGION annotation (which
        isn't pinned to a spot) does NOT (Fix 5's exclusion, exercised here
        for the first time - see Fix 6 of the 2026-09-24 whole-branch review)."""
        import pymupdf

        fd, png_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            src_doc = pymupdf.open()
            src_doc.new_page(width=200, height=300)
            pix = src_doc[0].get_pixmap()
            pix.save(png_path)
            src_doc.close()

            view = _full_correction_view(
                annotations=[
                    {
                        "letter": "A", "competency_code": "C1", "evidence_kind": "LOCALIZED",
                        "anchor": {
                            "type": "IMAGE_REGION", "page": 1, "x": 10, "y": 10,
                            "width": 50, "height": 20, "read_text": "trecho localizado",
                        },
                        "short_comment": "curto", "long_comment": "longo",
                    },
                    {
                        "letter": "B", "competency_code": "C2", "evidence_kind": "GLOBAL",
                        "anchor": {
                            "type": "IMAGE_REGION", "page": 1, "x": 20, "y": 40,
                            "width": 30, "height": 15, "read_text": "trecho global",
                        },
                        "short_comment": "curto global", "long_comment": "longo global",
                    },
                ],
            )
            model = build_render_model(view)
            pdf_bytes = render_pdf(
                model, title="Foto com anotações", anchor_mode="IMAGE_REGION",
                page_images=[(1, png_path)],
            )
            self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

            result_doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            try:
                image_pages = [p for p in result_doc if len(p.get_images()) > 0]
                self.assertEqual(len(image_pages), 1, "expected exactly one manually-drawn image page")
                image_page = image_pages[0]
                self.assertEqual(len(image_page.get_images()), 1)
                self.assertEqual(len(image_page.get_drawings()), 1)
            finally:
                result_doc.close()
        finally:
            os.unlink(png_path)

    def test_render_pdf_image_region_overlay_uses_line_based_anchor(self):
        """Line-based anchors (contract v2+, no x/y/width/height) must still
        draw a visible overlay - converted to a full-width band for that
        line, mirroring essay-annotations.js's renderImageMarkers."""
        import pymupdf

        fd, png_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            src_doc = pymupdf.open()
            src_doc.new_page(width=400, height=3000)
            pix = src_doc[0].get_pixmap()
            pix.save(png_path)
            src_doc.close()

            view = _full_correction_view(annotations=[{
                "letter": "A", "competency_code": "C2", "evidence_kind": "LOCALIZED",
                "anchor": {
                    "type": "IMAGE_REGION", "page": 1, "line": 3, "total_lines": 30,
                    "read_text": "trecho na linha 3",
                },
                "short_comment": "curto", "long_comment": "longo",
            }])
            model = build_render_model(view)
            pdf_bytes = render_pdf(
                model, title="Foto com anotações", anchor_mode="IMAGE_REGION",
                page_images=[(1, png_path)],
            )
            self.assertTrue(pdf_bytes.startswith(b"%PDF-"))

            result_doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            try:
                image_page = next(p for p in result_doc if len(p.get_images()) > 0)
                self.assertEqual(len(image_page.get_drawings()), 1)
                rect = image_page.get_drawings()[0]["rect"]
                # Line 3 of 30 on a 3000pt-tall source, scaled to fit the
                # page - the overlay must sit near the top, not at (0, 0).
                self.assertGreater(rect.y0, 0)
            finally:
                result_doc.close()
        finally:
            os.unlink(png_path)

    def test_render_pdf_image_region_overlay_uses_a_visible_solid_color(self):
        """Confirmed live (2026-09-25): the overlay was drawn with the pale
        _COMPETENCY_COLORS background tint instead of _COMPETENCY_SOLID_COLORS,
        making a 2pt border essentially invisible against a white page - the
        student reported the devolutiva as having no markings at all. The
        overlay's stroke must be a solid, high-contrast color."""
        import pymupdf

        from agente_ia_edu.services.essay_pdf_export import _COMPETENCY_SOLID_COLORS, _hex_to_rgb

        fd, png_path = tempfile.mkstemp(suffix=".png")
        os.close(fd)
        try:
            src_doc = pymupdf.open()
            src_doc.new_page(width=200, height=300)
            pix = src_doc[0].get_pixmap()
            pix.save(png_path)
            src_doc.close()

            view = _full_correction_view(annotations=[{
                "letter": "A", "competency_code": "C3", "evidence_kind": "LOCALIZED",
                "anchor": {
                    "type": "IMAGE_REGION", "page": 1, "x": 10, "y": 10,
                    "width": 50, "height": 20, "read_text": "trecho localizado",
                },
                "short_comment": "curto", "long_comment": "longo",
            }])
            model = build_render_model(view)
            pdf_bytes = render_pdf(
                model, title="Foto com anotações", anchor_mode="IMAGE_REGION",
                page_images=[(1, png_path)],
            )

            result_doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
            try:
                image_page = next(p for p in result_doc if len(p.get_images()) > 0)
                drawing = image_page.get_drawings()[0]
                expected = _hex_to_rgb(_COMPETENCY_SOLID_COLORS["C3"])
                for actual, wanted in zip(drawing["color"], expected):
                    self.assertAlmostEqual(actual, wanted, places=2)
            finally:
                result_doc.close()
        finally:
            os.unlink(png_path)


    def test_static_mechanical_reference_and_intervention_checklist_are_gone(self):
        """Product ask (2026-09-28): the static "O que observamos" glossary
        table, the C5 intervention checklist (which often rendered as an
        empty "Agente —", "Ação —", ... wall when the AI left those fields
        blank), and the closing AI-disclaimer paragraph were all removed
        from the devolutiva - essay-report.js and this module must both stay
        in sync, per this module's own docstring rule."""
        model = build_render_model(_full_correction_view())
        pdf_bytes = render_pdf(
            model, title="Sem blocos removidos", anchor_mode="TEXT_OFFSET",
            canonical_text="Um texto qualquer para o teste.",
        )
        import pymupdf
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            text = "".join(page.get_text() for page in doc)
        finally:
            doc.close()
        self.assertNotIn("O que observamos", text)
        # "Proposta de intervenção" alone still legitimately appears as the
        # C5 competency label (kept) - only the dedicated checklist heading
        # and its own content are gone.
        self.assertNotIn("Competência 5", text)
        self.assertNotIn("Respeita os direitos humanos", text)
        self.assertNotIn("estimativa pedagógica", text)

    def test_mechanical_review_section_omitted_when_no_occurrences(self):
        """Product ask (2026-09-29, mirrors tests/test_essay_report_empty_sections.js):
        when there is no confirmed mechanical occurrence, the whole "Revisão
        de domínio da norma padrão (C1)" section - heading AND content - must
        disappear from the exported PDF, not just fall back to a placeholder
        sentence. The on-screen essay-report.js already does this; this pins
        the same behavior down for the PDF export used by both the student
        and teacher download routes."""
        view = _full_correction_view(mechanical_review=[])
        model = build_render_model(view)
        self.assertEqual(_mechanical_occurrences_html(model), "")
        pdf_bytes = render_pdf(
            model, title="Sem ocorrências mecânicas", anchor_mode="TEXT_OFFSET",
            canonical_text="Um texto qualquer para o teste.",
        )
        import pymupdf
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            text = "".join(page.get_text() for page in doc)
        finally:
            doc.close()
        self.assertNotIn("Revisão de domínio da norma padrão", text)
        self.assertNotIn("Nenhuma ocorrência mecânica confirmada", text)

    def test_mechanical_review_section_shown_when_occurrences_present(self):
        """Counterpart to the omission test above: with at least one
        confirmed occurrence, the section must render normally (heading and
        occurrence content)."""
        model = build_render_model(_full_correction_view())
        pdf_bytes = render_pdf(
            model, title="Com ocorrência mecânica", anchor_mode="TEXT_OFFSET",
            canonical_text="Um texto qualquer para o teste.",
        )
        import pymupdf
        doc = pymupdf.open(stream=pdf_bytes, filetype="pdf")
        try:
            text = "".join(page.get_text() for page in doc)
        finally:
            doc.close()
        self.assertIn("Revisão de domínio da norma padrão", text)
        self.assertIn("de acordo com a pesquisa", text)

    def test_annotations_rewrites_mechanics_and_table_never_use_fill_decorations(self):
        """Confirmed live 2026-09-28: PyMuPDF's Story engine (1.28.2) echoes
        `background`/`border-left` fills from these exact sections as empty,
        meaningless colored bars far later in the document - reported by a
        real user as "colored markings that don't make sense" at the very
        end of an otherwise-blank final page. Verified directly: stripping
        every `background:`/`border-left:` from this module's HTML made the
        stray fills disappear entirely from the rendered PDF (0 drawings on
        every trailing page, in a document that previously had 15). Colored
        TEXT (font `color:`) does not trigger it and stays in use for
        competency identification instead. This test guards the root cause
        at the HTML-string level - cheap and deterministic - rather than
        re-driving the full multi-page PyMuPDF repro on every test run."""
        model = build_render_model(_full_correction_view())
        for html in (
            _annotations_html(model), _rewrites_html(model),
            _mechanical_occurrences_html(model), _competency_table_html(model),
        ):
            self.assertNotIn("background:", html)
            self.assertNotIn("border-left:", html)


STRUCTURED_C2_C3 = {
    "c2_tipologia_textual": "Texto dissertativo-argumentativo completo.",
    "c2_tema": "Desenvolve o tema proposto.",
    "c2_repertorio_sociocultural": "Não foi identificado repertório sociocultural no texto.",
    "c2_orientacao_melhoria": "Traga um repertório pertinente ao tema.",
    "c3_projeto_argumentativo": "A tese é retomada na conclusão.",
    "c3_fatos_informacoes_opinioes": "Usa dados do IBGE no segundo parágrafo.",
    "c3_autoria": "Há voz autoral no terceiro parágrafo.",
    "c3_orientacao_melhoria": "Desenvolva o segundo argumento com um exemplo.",
}


def _rationale(code: str) -> dict:
    return {
        "competency_code": code, "summary": f"resumo {code}",
        "strengths": f"forças {code}", "growth_area": f"avançar {code}",
        "signal_keys": [],
    }


def _view(rationale_codes, **overrides) -> dict:
    """The PDF export mirrors renderCompetencyChecklist's fallback rules
    deliberately (spec §4) - these tests are the Python half of the same
    three-level contract tested in
    tests/test_feedback_estruturado_c2_c3_frontend.js."""
    view = {
        "final_scores": {
            "total": 800,
            "per_competency": {c: {"points": 160} for c in ("C1", "C2", "C3", "C4", "C5")},
        },
        "final_feedback": {"strengths": [], "improvements": [], "next_essay_strategy": "x"},
        "annotations": [], "rewrites": [], "alerts": [], "mechanical_review": [],
        "intervention": {}, "intro_message": "", "closing_message": "",
        "rationales": [_rationale(c) for c in rationale_codes],
    }
    view.update(overrides)
    return view


class StructuredC2C3PdfTests(unittest.TestCase):
    def _row(self, model, code):
        return next(r for r in model["competency_rows"] if r["code"] == code)

    def test_level_1_c2_row_carries_the_four_labelled_aspects_in_order(self):
        model = build_render_model(_view(["C1", "C4", "C5"], **STRUCTURED_C2_C3))
        row = self._row(model, "C2")
        self.assertEqual(
            [a["label"] for a in row["aspects"]],
            ["Tipologia textual", "Tema", "Repertório sociocultural", "Como melhorar"],
        )
        self.assertEqual(
            row["aspects"][2]["text"],
            "Não foi identificado repertório sociocultural no texto.",
        )

    def test_level_1_c3_row_carries_the_four_labelled_aspects_in_order(self):
        model = build_render_model(_view(["C1", "C4", "C5"], **STRUCTURED_C2_C3))
        row = self._row(model, "C3")
        self.assertEqual(
            [a["label"] for a in row["aspects"]],
            ["Projeto argumentativo", "Informações, fatos e opiniões", "Autoria",
             "Como melhorar"],
        )

    def test_level_1_rows_exist_even_without_a_rationale_for_c2_c3(self):
        """Contract v5: rationales covers only C1/C4/C5. Without this the two
        rows would simply vanish from the PDF."""
        model = build_render_model(_view(["C1", "C4", "C5"], **STRUCTURED_C2_C3))
        self.assertEqual(
            [r["code"] for r in model["competency_rows"]], ["C1", "C2", "C3", "C4", "C5"]
        )

    def test_level_1_html_renders_labels_and_the_new_competency_name(self):
        model = build_render_model(_view(["C1", "C4", "C5"], **STRUCTURED_C2_C3))
        html = _competency_table_html(model)
        self.assertIn("Tipologia textual:", html)
        self.assertIn("Informações, fatos e opiniões:", html)
        self.assertIn("C2 — Tipologia, tema e repertório", html)
        self.assertIn("C3 — Projeto argumentativo e autoria", html)

    def test_level_1_splits_como_melhorar_into_its_own_column(self):
        """The last aspect of both C2 and C3 (_COMPETENCY_ASPECTS) is
        always "Como melhorar" - same meaning as growth_area for C1/C4/C5,
        so it renders in its own "Onde pode avançar" cell instead of being
        listed alongside the three diagnostic aspects."""
        model = build_render_model(_view(["C1", "C4", "C5"], **STRUCTURED_C2_C3))
        html = _competency_table_html(model)
        row = self._row(model, "C2")
        self.assertEqual(len(row["aspects"]), 4)
        self.assertNotIn("colspan", html.split("C2 —")[1].split("</tr>")[0])
        self.assertIn(
            "<td>Traga um repertório pertinente ao tema.</td>",
            html.split("C2 —")[1].split("</tr>")[0],
        )
        # "Como melhorar" itself never appears as a <li> label anymore -
        # only the 3 diagnostic aspects do.
        c2_row_html = html.split("C2 —")[1].split("</tr>")[0]
        self.assertNotIn("Como melhorar:", c2_row_html)
        self.assertIn("Tipologia textual:", c2_row_html)
        self.assertIn("Tema:", c2_row_html)
        self.assertIn("Repertório sociocultural:", c2_row_html)

    def test_level_2_when_the_structured_fields_are_absent(self):
        model = build_render_model(_view(["C1", "C2", "C3", "C4", "C5"]))
        row = self._row(model, "C2")
        self.assertIsNone(row["aspects"])
        self.assertTrue(row["has_split"])
        html = _competency_table_html(model)
        self.assertIn("forças C2", html)
        self.assertNotIn("Tipologia textual:", html)

    def test_partial_structured_fields_fall_back_to_level_2(self):
        partial = dict(STRUCTURED_C2_C3)
        del partial["c2_orientacao_melhoria"]
        model = build_render_model(_view(["C1", "C2", "C3", "C4", "C5"], **partial))
        self.assertIsNone(self._row(model, "C2")["aspects"])
        self.assertIsNotNone(self._row(model, "C3")["aspects"])

    def test_blank_structured_field_falls_back_to_level_2(self):
        blank = {**STRUCTURED_C2_C3, "c2_tema": "   "}
        model = build_render_model(_view(["C1", "C2", "C3", "C4", "C5"], **blank))
        self.assertIsNone(self._row(model, "C2")["aspects"])

    def test_level_3_summary_only_still_renders_a_single_cell(self):
        view = _view([])
        view["rationales"] = [{"competency_code": "C2", "summary": "resumo antigo de C2"}]
        model = build_render_model(view)
        html = _competency_table_html(model)
        self.assertIn('colspan="2">resumo antigo de C2', html)

    def test_c1_c4_c5_keep_the_split_layout(self):
        model = build_render_model(_view(["C1", "C4", "C5"], **STRUCTURED_C2_C3))
        for code in ("C1", "C4", "C5"):
            with self.subTest(code=code):
                row = self._row(model, code)
                self.assertIsNone(row["aspects"])
                self.assertTrue(row["has_split"])

    def test_has_any_split_is_true_when_only_structured_rows_exist(self):
        """has_any_split drives the "Pontos fortes" fallback list, which only
        exists for corrections with NO per-competency detail at all."""
        view = _view([], **STRUCTURED_C2_C3)
        view["final_feedback"] = {"strengths": ["ponto forte solto"],
                                  "improvements": [], "next_essay_strategy": "x"}
        model = build_render_model(view)
        self.assertTrue(model["has_any_split"])
        self.assertEqual(_strengths_fallback_html(model), "")


if __name__ == "__main__":
    unittest.main()
