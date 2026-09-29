import json
import unittest

from agente_ia_edu.essay_prompts import available_versions, get_essay_prompt

REPERTORIO_VAZIO = (
    "Não foi identificado repertório sociocultural no texto. Para "
    "fortalecer sua argumentação, procure utilizar referências pertinentes "
    "ao tema, como fatos históricos, conceitos, pesquisas, dados, obras, "
    "legislação ou outros conhecimentos socioculturais, relacionando-os ao "
    "argumento desenvolvido."
)

STRUCTURED_FIELDS = (
    "c2_tipologia_textual", "c2_tema", "c2_repertorio_sociocultural",
    "c2_orientacao_melhoria", "c3_projeto_argumentativo",
    "c3_fatos_informacoes_opinioes", "c3_autoria", "c3_orientacao_melhoria",
)


class EssayPromptV15Tests(unittest.TestCase):
    def setUp(self):
        self.rubric = {
            "rubric_version": "ENEM_2025",
            "competencies": [
                {"code": "C1", "official_title": "Domínio da norma padrão",
                 "levels": [{"points": 0, "descriptor": "..."}]},
            ],
        }

    def _prompt(self, **overrides):
        kwargs = {
            "anchor_mode": "TEXT_OFFSET", "essay_statement": "Disserte sobre X.",
            "rubric": self.rubric, "include_scores": True, "text": "Um texto qualquer.",
        }
        kwargs.update(overrides)
        return get_essay_prompt("essay_correction_v15").build(**kwargs)

    # --- registry / assembly (same contract v14 already had) ---

    def test_registered_and_available(self):
        self.assertIn("essay_correction_v15", available_versions())

    def test_v14_is_still_registered(self):
        """v15 never replaces v14 in the registry - a correction persisted
        under essay_correction_v14 must stay resolvable."""
        self.assertIn("essay_correction_v14", available_versions())

    def test_text_offset_prompt_embeds_the_text_and_schema(self):
        prompt = self._prompt()
        self.assertIn("RESPONSE_SCHEMA:", prompt)
        self.assertIn("TEXT_OFFSET", prompt)
        self.assertIn(json.dumps("Um texto qualquer.", ensure_ascii=False), prompt)
        self.assertIn("ESSAY_STATEMENT:", prompt)
        self.assertIn("RUBRIC:", prompt)
        self.assertNotIn('"identification"', prompt)

    def test_image_region_prompt_embeds_page_count(self):
        prompt = self._prompt(anchor_mode="IMAGE_REGION", text=None, page_count=2)
        self.assertIn("PAGE_COUNT: 2", prompt)
        self.assertIn('"line": int, "total_lines": int', prompt)
        self.assertIn('{"type": "IMAGE_REGION"', prompt)
        self.assertNotIn('{{"type"', prompt)

    def test_image_region_prompt_requires_positive_page_count(self):
        with self.assertRaises(ValueError):
            self._prompt(anchor_mode="IMAGE_REGION", text=None, page_count=0)

    def test_unknown_anchor_mode_raises(self):
        with self.assertRaises(ValueError):
            self._prompt(anchor_mode="SOMETHING_ELSE")

    def test_avaliativo_asks_for_a_grade(self):
        self.assertIn("SCORING_MODE: AVALIATIVO", self._prompt())

    def test_formativo_asks_for_no_grade_but_still_for_the_structured_fields(self):
        prompt = self._prompt(include_scores=False)
        self.assertIn("SCORING_MODE: FORMATIVO", prompt)
        tail = prompt.split("SCORING_MODE:")[1]
        self.assertIn("null", tail)
        self.assertIn("oito campos estruturados de C2 e C3", tail)

    def test_system_policy_demands_portuguese_output(self):
        prompt = self._prompt()
        self.assertIn("SEMPRE em portugues do Brasil", prompt)
        self.assertIn("nunca em ingles", prompt)

    # --- v15's own changes ---

    def test_response_schema_declares_the_eight_structured_fields(self):
        artifact = get_essay_prompt("essay_correction_v15")
        for field in STRUCTURED_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, artifact.response_schema)

    def test_the_eight_structured_fields_reach_the_assembled_prompt(self):
        prompt = self._prompt()
        for field in STRUCTURED_FIELDS:
            with self.subTest(field=field):
                self.assertIn(field, prompt)

    def test_rationales_schema_excludes_c2_and_c3(self):
        artifact = get_essay_prompt("essay_correction_v15")
        code_spec = artifact.response_schema["rationales"][0]["competency_code"]
        self.assertIn("C1|C4|C5", code_spec)
        self.assertNotIn("C1|C2|C3|C4|C5", code_spec)

    def test_rationale_rules_forbid_a_c2_or_c3_rationale(self):
        prompt = self._prompt()
        self.assertIn("RATIONALE_RULES", prompt)
        self.assertIn("rationales cobre APENAS C1, C4 e C5", prompt)

    def test_feedback_rules_forbid_an_empty_improvements_list(self):
        prompt = self._prompt()
        self.assertIn("FEEDBACK_RULES", prompt)
        self.assertIn("improvements", prompt)
        self.assertIn("nunca pode ser uma lista vazia", prompt)

    def test_c2_rules_name_the_three_aspects_and_the_improvement_field(self):
        prompt = self._prompt()
        self.assertIn("C2_RULES", prompt)
        self.assertIn("c2_tipologia_textual", prompt)
        self.assertIn("c2_tema", prompt)
        self.assertIn("c2_repertorio_sociocultural", prompt)
        self.assertIn("c2_orientacao_melhoria", prompt)

    def test_c2_rules_carry_the_exact_empty_repertoire_sentence(self):
        """Spec §2: when no repertoire is identified, the field must carry
        exactly this sentence - not a paraphrase, and never an invented
        repertoire."""
        prompt = self._prompt()
        self.assertIn("REGRA DO REPERTORIO VAZIO", prompt)
        self.assertIn(REPERTORIO_VAZIO, prompt)
        self.assertIn("Nunca invente um repertorio", prompt)

    def test_c3_rules_name_the_three_aspects_and_the_improvement_field(self):
        prompt = self._prompt()
        self.assertIn("C3_RULES", prompt)
        self.assertIn("c3_projeto_argumentativo", prompt)
        self.assertIn("c3_fatos_informacoes_opinioes", prompt)
        self.assertIn("c3_autoria", prompt)
        self.assertIn("c3_orientacao_melhoria", prompt)

    def test_c3_rules_demand_real_evidence_and_forbid_pretending(self):
        prompt = self._prompt()
        self.assertIn("REGRA DA EVIDENCIA REAL", prompt)
        self.assertIn("nunca invente um fato", prompt)
        self.assertIn("nunca finja que existe o que nao existe", prompt)

    def test_mechanical_rules_demand_naming_the_pronoun_type_for_crase(self):
        prompt = self._prompt()
        self.assertIn("CRASE COM PRONOME", prompt)
        self.assertIn("demonstrativo, relativo, pessoal obliquo, indefinido ou possessivo", prompt)
        self.assertIn("Nunca responda com uma explicacao generica de crase", prompt)

    def test_mechanical_rules_demand_naming_the_construction_for_pontuacao(self):
        prompt = self._prompt()
        self.assertIn("nao pode parar em 'falta uma virgula'", prompt)
        self.assertIn("aposto, vocativo", prompt)
        self.assertIn("adjunto adverbial deslocado", prompt)

    def test_mechanical_rules_still_forbid_inventing_occurrences(self):
        prompt = self._prompt()
        self.assertIn("MECHANICAL_REVIEW_RULES", prompt)
        self.assertIn("Nunca invente uma ocorrencia para preencher a lista", prompt)

    # --- v14 rule blocks that v15 must carry over verbatim ---

    def test_c1_calibration_protocol_survives_unchanged(self):
        prompt = self._prompt()
        self.assertIn("C1_CALIBRATION", prompt)
        for passo in ("PASSO 1", "PASSO 2", "PASSO 3", "PASSO 4", "PASSO 5", "PASSO 6"):
            with self.subTest(passo=passo):
                self.assertIn(passo, prompt)
        self.assertIn("ESTILO NAO E ERRO", prompt)
        self.assertIn("Nunca use uma formula mecanica", prompt)

    def test_alert_rules_survive_unchanged(self):
        prompt = self._prompt()
        self.assertIn("ALERT_RULES", prompt)
        self.assertIn("FUGA_AO_TEMA APENAS quando a redacao fugiu TOTALMENTE", prompt)
        self.assertIn("isso e evidencia CONTRA este alerta", prompt)
        for code in (
            "FUGA_AO_TEMA", "TANGENCIAMENTO_AO_TEMA", "TIPO_TEXTUAL_PREDOMINANTE",
            "TEXTO_INSUFICIENTE", "ANULACAO_PROPOSITAL", "PARTE_DESCONECTADA_DO_TEMA",
            "IDENTIFICACAO_INDEVIDA", "LINGUA_ESTRANGEIRA", "TEXTO_ILEGIVEL",
        ):
            with self.subTest(code=code):
                self.assertIn(code, prompt)

    def test_coverage_and_signal_and_anchor_rules_survive_unchanged(self):
        prompt = self._prompt()
        self.assertIn("COVERAGE_RULES", prompt)
        self.assertIn("REGRA CRITICA", prompt)
        self.assertIn("SIGNAL_RULES", prompt)
        self.assertIn("nunca invente uma key nova", prompt)
        self.assertIn("Conte CARACTERES, nunca", prompt)
        self.assertIn("nunca apenas o numero da linha seguido", prompt)

    def test_rewrite_and_narrative_rules_survive_unchanged(self):
        prompt = self._prompt()
        self.assertIn("REWRITE_RULES", prompt)
        self.assertIn("NARRATIVE_RULES", prompt)

    def test_copia_do_texto_motivador_stays_out(self):
        self.assertNotIn("COPIA_DO_TEXTO_MOTIVADOR", self._prompt())


if __name__ == "__main__":
    unittest.main()
