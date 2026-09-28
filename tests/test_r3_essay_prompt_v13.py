import json
import unittest

from agente_ia_edu.essay_prompts import available_versions, get_essay_prompt


class EssayPromptV13Tests(unittest.TestCase):
    def setUp(self):
        self.rubric = {
            "rubric_version": "ENEM_2025",
            "competencies": [
                {"code": "C1", "official_title": "Domínio da norma padrão",
                 "levels": [{"points": 0, "descriptor": "..."}]},
            ],
        }

    def test_registered_and_available(self):
        self.assertIn("essay_correction_v13", available_versions())

    def test_text_offset_prompt_embeds_the_text_and_schema(self):
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET",
            essay_statement="Disserte sobre X.",
            rubric=self.rubric,
            include_scores=True,
            text="Um texto qualquer.",
        )
        self.assertIn("RESPONSE_SCHEMA:", prompt)
        self.assertIn("TEXT_OFFSET", prompt)
        self.assertIn(json.dumps("Um texto qualquer.", ensure_ascii=False), prompt)
        self.assertIn("ESSAY_STATEMENT:", prompt)
        self.assertIn("RUBRIC:", prompt)
        self.assertNotIn('"identification"', prompt)

    def test_image_region_prompt_embeds_page_count_and_asks_for_line_counting(self):
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="IMAGE_REGION",
            essay_statement="Disserte sobre X.",
            rubric=self.rubric,
            include_scores=True,
            page_count=2,
        )
        self.assertIn("IMAGE_REGION", prompt)
        self.assertIn("PAGE_COUNT: 2", prompt)
        self.assertIn('"line": int, "total_lines": int', prompt)
        self.assertNotIn('"x": float', prompt)
        self.assertNotIn("dimensoes REAIS de cada pagina", prompt)

    def test_image_region_prompt_requires_positive_page_count(self):
        artifact = get_essay_prompt("essay_correction_v13")
        with self.assertRaises(ValueError):
            artifact.build(
                anchor_mode="IMAGE_REGION", essay_statement="Disserte.",
                rubric=self.rubric, include_scores=True, page_count=0,
            )

    def test_unknown_anchor_mode_raises(self):
        artifact = get_essay_prompt("essay_correction_v13")
        with self.assertRaises(ValueError):
            artifact.build(
                anchor_mode="SOMETHING_ELSE", essay_statement="Disserte.", rubric=self.rubric,
                include_scores=True,
            )

    def test_avaliativo_asks_for_a_grade(self):
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("SCORING_MODE: AVALIATIVO", prompt)

    def test_formativo_asks_for_no_grade(self):
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=False, text="Um texto.",
        )
        self.assertIn("SCORING_MODE: FORMATIVO", prompt)
        self.assertIn("null", prompt.split("SCORING_MODE:")[1])

    def test_image_region_anchor_shape_uses_single_braces_not_doubled(self):
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="IMAGE_REGION", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, page_count=1,
        )
        self.assertIn('{"type": "IMAGE_REGION"', prompt)
        self.assertNotIn('{{"type"', prompt)

    def test_system_policy_demands_portuguese_output(self):
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("SEMPRE em portugues do Brasil", prompt)
        self.assertIn("nunca em ingles", prompt)

    def test_coverage_rules_require_localized_annotations_per_competency(self):
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("COVERAGE_RULES", prompt)
        self.assertIn("pelo menos uma annotation com evidence_kind=LOCALIZED", prompt)

    def test_coverage_rules_require_narrated_problems_to_also_be_annotated(self):
        """v13 keeps v7's REGRA CRITICA (COVERAGE_RULES) unchanged - only
        ALERT_RULES and the new C1_CALIBRATION block differ from v12."""
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("REGRA CRITICA", prompt)
        self.assertIn("tambem criar uma annotation localizada", prompt)

    def test_coverage_rules_state_no_maximum_or_minimum_annotation_count(self):
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("Nao existe um numero maximo", prompt)
        self.assertNotIn("pelo menos 5 annotations no total", prompt)

    def test_anchor_rules_demand_character_counting_not_bytes_or_tokens(self):
        """Confirmed live (2026-09-25): two real QUOTE_DOES_NOT_MATCH_TEXT
        rejections were caused by the model's start/end arithmetic drifting
        by a small amount on a line-numbered, accented Portuguese
        transcription - not by wrong content. v8 (kept unchanged through v13)
        spells out exactly what to count so the model's own offsets need
        fewer validation-layer repairs (see
        essay_engine_validation._resolve_text_offset)."""
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("Conte CARACTERES, nunca", prompt)
        self.assertIn("end - start deve ser exatamente o numero de caracteres", prompt)
        self.assertIn("LITERALMENTE", prompt)

    def test_anchor_rules_unchanged_for_image_region(self):
        """Only the TEXT_OFFSET branch changed; IMAGE_REGION keeps v7's
        line-counting rules verbatim."""
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="IMAGE_REGION", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, page_count=1,
        )
        self.assertIn("NUNCA estime uma coordenada em", prompt)

    def test_anchor_rules_forbid_line_number_stub_quotes(self):
        """Confirmed live (2026-09-25): a real correction's TEXT_OFFSET
        annotations all validated, but every quote was a stub like
        "23. do trabalhador" - the line-number prefix plus a throwaway word,
        never the actual criticized phrase. v9 (kept unchanged through v13)
        explicitly forbids this."""
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("nunca apenas o numero da linha seguido", prompt)
        self.assertIn("o quote deveria conter a frase ou o trecho realmente criticado", prompt)

    def test_alert_rules_distinguish_fuga_total_from_tangenciamento(self):
        """Confirmed by rubric audit (2026-09-27): the model had no way to
        express "tangenciou mas nao fugiu totalmente" - FUGA_AO_TEMA was
        being used for both, but only the total case is an official zero."""
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("ALERT_RULES", prompt)
        self.assertIn("TANGENCIAMENTO_AO_TEMA", prompt)
        self.assertIn("FUGA_AO_TEMA APENAS quando a redacao fugiu TOTALMENTE", prompt)

    def test_alert_rules_distinguish_tipo_textual_severity(self):
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("TIPO_TEXTUAL_PREDOMINANTE", prompt)
        self.assertIn("predominantemente dissertativo", prompt)

    def test_alert_rules_explain_the_human_rights_field_and_its_effect(self):
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("respeita_direitos_humanos", prompt)
        self.assertIn("zera automaticamente", prompt)

    def test_response_schema_lists_the_new_alert_codes(self):
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("TANGENCIAMENTO_AO_TEMA", prompt)
        self.assertIn("TIPO_TEXTUAL_PREDOMINANTE", prompt)

    def test_signal_rules_ground_signal_keys_in_the_rubric_vocabulary(self):
        """Confirmed by rubric audit (2026-09-27): rubrics/enem_2025.yaml
        seeds ~30 cartilha-sourced signal concepts per competency, but the
        model was never told they existed - signal_keys were freely invented,
        never grounded in anything real."""
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("SIGNAL_RULES", prompt)
        self.assertIn("RUBRIC.competencies[].signals", prompt)
        self.assertIn("nunca invente uma key nova", prompt)

    def test_signal_rules_allow_an_empty_list_over_a_made_up_key(self):
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("lista vazia e valida e preferivel a uma key inventada", prompt)

    def test_rubric_signals_reach_the_prompt_when_present(self):
        """The rubric payload's own `signals` field (built by
        essay_correction._rubric_payload) must survive straight through to
        the assembled prompt - this is what the model actually reads."""
        rubric_with_signals = {
            "rubric_version": "ENEM_2025",
            "competencies": [
                {
                    "code": "C1", "official_title": "Domínio da norma padrão",
                    "levels": [{"points": 0, "descriptor": "..."}],
                    "signals": [
                        {"key": "estrutura_sintatica", "label": "Estrutura sintática",
                         "description": "Construção das frases do texto."},
                    ],
                },
            ],
        }
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.",
            rubric=rubric_with_signals, include_scores=True, text="Um texto.",
        )
        self.assertIn("estrutura_sintatica", prompt)
        self.assertIn("Estrutura sintática", prompt)

    def test_response_schema_lists_the_five_new_alert_codes(self):
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        for code in (
            "ANULACAO_PROPOSITAL", "PARTE_DESCONECTADA_DO_TEMA",
            "IDENTIFICACAO_INDEVIDA", "LINGUA_ESTRANGEIRA", "TEXTO_ILEGIVEL",
        ):
            with self.subTest(code=code):
                self.assertIn(code, prompt)

    def test_alert_rules_scope_anulacao_proposital_to_real_sabotage(self):
        """Confirmed by rubric audit (2026-09-27): a weak or badly-written
        essay is a quality problem, never grounds for ANULACAO_PROPOSITAL -
        that code is reserved for genuine slurs/drawings/sabotage."""
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("nunca para uma redacao apenas fraca", prompt)

    def test_alert_rules_scope_parte_desconectada_to_genuinely_unrelated_content(self):
        """A real argument that invokes religion or politics IN SERVICE of
        the theme must not be confused with a disconnected insertion."""
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("NAO use este alerta para um argumento legitimo", prompt)

    def test_alert_rules_scope_identificacao_indevida_to_self_identification(self):
        """A name cited as a repertoire example, author, character or an
        intervention's suggested agent is ordinary content, not this alert."""
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("nunca para um nome citado como exemplo", prompt)

    def test_alert_rules_scope_lingua_estrangeira_to_predominant_use(self):
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("nao use por causa de uma palavra estrangeira isolada", prompt)

    def test_alert_rules_set_a_high_bar_for_texto_ilegivel(self):
        """Confirmed by rubric audit (2026-09-27): TEXTO_ILEGIVEL is an
        official zero-grade condition and must not be confused with
        OCR_DUVIDOSO (this engine's own everyday uncertainty flag, no
        scoring consequence) - producing a coherent correction is evidence
        AGAINST this alert."""
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("isso e evidencia CONTRA este alerta", prompt)
        self.assertIn("prefira OCR_DUVIDOSO", prompt)

    def test_c1_calibration_rule_is_present(self):
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("C1_CALIBRATION", prompt)

    def test_c1_calibration_generalizes_the_rubrics_own_recidivism_principle(self):
        """The 200-level C1 descriptor already says a recurring deviation
        should not be double-counted ("quando nao caracterizarem
        reincidencia") - v13's new rule is this SAME principle, applied to
        every band boundary instead of only the top one, not an invented
        one (calibration run 2026-09-28: the model averaged C1=78 against
        the human corrector's 120, a full band low)."""
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertIn("reincidencia", prompt)
        self.assertIn("dominio insuficiente, com muitos desvios", prompt)
        self.assertIn("dominio mediano, com alguns desvios", prompt)
        self.assertIn("poucos desvios", prompt)

    def test_alert_rules_name_zero_consequence_codes_explicitly_not_by_stale_count(self):
        """v12 said "os cinco alertas acima zeram a redacao inteira" from
        back when there were exactly five such codes - v12 itself grew that
        set to eight without updating the sentence. v13 names all eight
        codes explicitly instead of counting them, so nothing can silently
        drift out of sync again."""
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertNotIn("cinco alertas acima zeram", prompt)
        for code in (
            "FUGA_AO_TEMA", "TIPO_TEXTUAL_PREDOMINANTE", "TEXTO_INSUFICIENTE",
            "ANULACAO_PROPOSITAL", "PARTE_DESCONECTADA_DO_TEMA",
            "IDENTIFICACAO_INDEVIDA", "LINGUA_ESTRANGEIRA", "TEXTO_ILEGIVEL",
        ):
            with self.subTest(code=code):
                self.assertIn(code, prompt)

    def test_copia_do_texto_motivador_was_deliberately_not_added(self):
        """Regression guard for a real mistake caught before it shipped: a
        COPIA_DO_TEXTO_MOTIVADOR alert looked like the obvious fix for the
        calibration run's one unzeroed "copia" essay, but (1)
        rubrics/enem_2025.yaml's own "REGRAS NORMATIVAS QUE FICARAM DE FORA"
        comment documents that the official INEP cartilha does NOT treat
        plain copying as an automatic-zero condition, only a line-count
        discount plus C3's own 80-point cap; and (2) this prompt is never
        given the motivational texts' own content
        (essay_correction._effective_essay_statement never reads
        PromptMaterial), so no alert code could let the model detect a copy
        it structurally cannot see. See the module docstring's NOTE for the
        full reasoning - this stays out until PromptMaterial content is
        actually wired into the prompt."""
        artifact = get_essay_prompt("essay_correction_v13")
        prompt = artifact.build(
            anchor_mode="TEXT_OFFSET", essay_statement="Disserte.", rubric=self.rubric,
            include_scores=True, text="Um texto.",
        )
        self.assertNotIn("COPIA_DO_TEXTO_MOTIVADOR", prompt)


if __name__ == "__main__":
    unittest.main()
