import unittest

from agente_ia_edu.services.essay_correction_edits import (
    AI_OUTPUT_EDITABLE_FIELDS,
    apply_ai_output_patch,
)


class ApplyAiOutputPatchTests(unittest.TestCase):
    def test_editable_fields_list_the_eight_structured_fields_plus_three_more(self):
        self.assertEqual(AI_OUTPUT_EDITABLE_FIELDS, (
            "c2_tipologia_textual", "c2_tema", "c2_repertorio_sociocultural",
            "c2_orientacao_melhoria", "c3_projeto_argumentativo",
            "c3_fatos_informacoes_opinioes", "c3_autoria", "c3_orientacao_melhoria",
            "mechanical_review", "intro_message", "closing_message",
        ))

    def test_overwrites_an_editable_field_and_reports_the_diff(self):
        ai_output = {"c2_tema": "texto antigo", "annotations": []}
        diff = apply_ai_output_patch(ai_output, {"c2_tema": "texto novo"})
        self.assertEqual(ai_output["c2_tema"], "texto novo")
        self.assertEqual(diff, {"c2_tema": {"before": "texto antigo", "after": "texto novo"}})

    def test_reports_no_diff_for_a_field_set_to_its_own_current_value(self):
        ai_output = {"intro_message": "mesmo texto"}
        diff = apply_ai_output_patch(ai_output, {"intro_message": "mesmo texto"})
        self.assertEqual(diff, {})

    def test_rejects_a_field_outside_the_editable_whitelist(self):
        ai_output = {"annotations": []}
        with self.assertRaises(ValueError) as caught:
            apply_ai_output_patch(ai_output, {"annotations": []})
        self.assertIn("annotations", str(caught.exception))

    def test_a_missing_field_in_ai_output_is_treated_as_none_before(self):
        ai_output = {}
        diff = apply_ai_output_patch(ai_output, {"closing_message": "Até a próxima!"})
        self.assertEqual(ai_output["closing_message"], "Até a próxima!")
        self.assertEqual(diff, {"closing_message": {"before": None, "after": "Até a próxima!"}})


class NextAnnotationLetterTests(unittest.TestCase):
    def test_first_letter_when_ai_output_has_no_annotations(self):
        from agente_ia_edu.services.essay_correction_edits import next_annotation_letter
        self.assertEqual(next_annotation_letter({}), "A")

    def test_next_letter_after_existing_ones(self):
        from agente_ia_edu.services.essay_correction_edits import next_annotation_letter
        ai_output = {"annotations": [{"letter": "A"}, {"letter": "C"}]}
        self.assertEqual(next_annotation_letter(ai_output), "D")

    def test_never_reuses_a_letter_removed_earlier_even_if_no_longer_present(self):
        from agente_ia_edu.services.essay_correction_edits import next_annotation_letter
        ai_output = {"annotations": [], "_annotation_letter_watermark": 2}
        self.assertEqual(next_annotation_letter(ai_output), "D")

    def test_rolls_over_past_z_into_two_letters(self):
        from agente_ia_edu.services.essay_correction_edits import next_annotation_letter
        ai_output = {"annotations": [], "_annotation_letter_watermark": 25}
        self.assertEqual(next_annotation_letter(ai_output), "AA")


TEXT = "A leitura transforma o pensamento crítico das pessoas."


class ApplyAnnotationsPatchTests(unittest.TestCase):
    def _ai_output(self, annotations=None):
        return {"annotations": annotations or []}

    def test_add_creates_a_localized_annotation_with_the_next_letter(self):
        from agente_ia_edu.services.essay_correction_edits import apply_annotations_patch
        ai_output = self._ai_output()
        quote = "pensamento crítico"
        start = TEXT.index(quote)
        diff = apply_annotations_patch(
            ai_output,
            [{
                "op": "add",
                "annotation": {
                    "competency_code": "C3", "kind": "ACERTO",
                    "anchor": {"start": start, "end": start + len(quote), "quote": quote},
                    "short_comment": "curto", "long_comment": "longo",
                },
            }],
            canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
        )
        self.assertEqual(len(ai_output["annotations"]), 1)
        added = ai_output["annotations"][0]
        self.assertEqual(added["letter"], "A")
        self.assertEqual(added["evidence_kind"], "LOCALIZED")
        self.assertEqual(added["anchor"], {"type": "TEXT_OFFSET", "start": start, "end": start + len(quote), "quote": quote})
        self.assertEqual(diff["added"], [added])

    def test_add_rejects_a_quote_that_does_not_match_the_text_at_that_position(self):
        from agente_ia_edu.services.essay_correction_edits import apply_annotations_patch
        with self.assertRaises(ValueError) as caught:
            apply_annotations_patch(
                self._ai_output(),
                [{
                    "op": "add",
                    "annotation": {
                        "competency_code": "C1", "kind": "MELHORIA",
                        "anchor": {"start": 0, "end": 5, "quote": "texto que não está aqui"},
                        "short_comment": "curto", "long_comment": "longo",
                    },
                }],
                canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
            )
        self.assertIn("does not match", str(caught.exception))

    def test_add_rejects_when_anchor_mode_is_not_text_offset(self):
        from agente_ia_edu.services.essay_correction_edits import apply_annotations_patch
        with self.assertRaises(ValueError) as caught:
            apply_annotations_patch(
                self._ai_output(),
                [{
                    "op": "add",
                    "annotation": {
                        "competency_code": "C1", "kind": "MELHORIA",
                        "anchor": {"start": 0, "end": 1, "quote": TEXT[0]},
                        "short_comment": "curto", "long_comment": "longo",
                    },
                }],
                canonical_text=TEXT, anchor_mode="IMAGE_REGION",
            )
        self.assertIn("IMAGE_REGION", str(caught.exception))

    def test_two_adds_in_the_same_patch_get_consecutive_letters(self):
        from agente_ia_edu.services.essay_correction_edits import apply_annotations_patch
        ai_output = self._ai_output()
        apply_annotations_patch(
            ai_output,
            [
                {"op": "add", "annotation": {
                    "competency_code": "C1", "kind": "ACERTO",
                    "anchor": {"start": 0, "end": 1, "quote": "A"},
                    "short_comment": "c", "long_comment": "l",
                }},
                {"op": "add", "annotation": {
                    "competency_code": "C2", "kind": "MELHORIA",
                    "anchor": {"start": 2, "end": 9, "quote": "leitura"},
                    "short_comment": "c", "long_comment": "l",
                }},
            ],
            canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
        )
        self.assertEqual([a["letter"] for a in ai_output["annotations"]], ["A", "B"])

    def test_edit_changes_only_the_given_fields_and_keeps_the_anchor(self):
        from agente_ia_edu.services.essay_correction_edits import apply_annotations_patch
        ai_output = self._ai_output([{
            "letter": "A", "competency_code": "C1", "kind": "ACERTO",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 0, "end": 1, "quote": "A"},
            "short_comment": "antigo", "long_comment": "longo antigo",
        }])
        diff = apply_annotations_patch(
            ai_output, [{"op": "edit", "letter": "A", "changes": {"short_comment": "novo"}}],
            canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
        )
        edited = ai_output["annotations"][0]
        self.assertEqual(edited["short_comment"], "novo")
        self.assertEqual(edited["long_comment"], "longo antigo")
        self.assertEqual(edited["anchor"]["start"], 0)
        self.assertEqual(diff["edited"], [{
            "letter": "A", "before": {"short_comment": "antigo"}, "after": {"short_comment": "novo"},
        }])

    def test_edit_of_an_unknown_letter_raises(self):
        from agente_ia_edu.services.essay_correction_edits import apply_annotations_patch
        with self.assertRaises(ValueError) as caught:
            apply_annotations_patch(
                self._ai_output(), [{"op": "edit", "letter": "Z", "changes": {"short_comment": "x"}}],
                canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
            )
        self.assertIn("Z", str(caught.exception))

    def test_remove_takes_the_annotation_out_and_reports_it(self):
        from agente_ia_edu.services.essay_correction_edits import apply_annotations_patch
        ai_output = self._ai_output([{
            "letter": "B", "competency_code": "C2", "kind": "ACERTO",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 0, "end": 1, "quote": "A"},
            "short_comment": "c", "long_comment": "l",
        }])
        diff = apply_annotations_patch(
            ai_output, [{"op": "remove", "letter": "B"}],
            canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
        )
        self.assertEqual(ai_output["annotations"], [])
        self.assertEqual(diff["removed"][0]["letter"], "B")

    def test_remove_an_ai_generated_annotation_is_allowed(self):
        from agente_ia_edu.services.essay_correction_edits import apply_annotations_patch
        ai_output = self._ai_output([{
            "letter": "A", "competency_code": "C1", "kind": "ACERTO",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 0, "end": 1, "quote": "A"},
            "short_comment": "gerado pela IA", "long_comment": "l",
        }])
        apply_annotations_patch(
            ai_output, [{"op": "remove", "letter": "A"}],
            canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
        )
        self.assertEqual(ai_output["annotations"], [])

    def test_a_letter_removed_is_never_reused_by_a_later_add_in_the_same_patch(self):
        from agente_ia_edu.services.essay_correction_edits import apply_annotations_patch
        ai_output = self._ai_output([{
            "letter": "A", "competency_code": "C1", "kind": "ACERTO",
            "evidence_kind": "LOCALIZED",
            "anchor": {"type": "TEXT_OFFSET", "start": 0, "end": 1, "quote": "A"},
            "short_comment": "c", "long_comment": "l",
        }])
        apply_annotations_patch(
            ai_output,
            [
                {"op": "remove", "letter": "A"},
                {"op": "add", "annotation": {
                    "competency_code": "C2", "kind": "MELHORIA",
                    "anchor": {"start": 2, "end": 9, "quote": "leitura"},
                    "short_comment": "c", "long_comment": "l",
                }},
            ],
            canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
        )
        self.assertEqual(ai_output["annotations"][0]["letter"], "B")

    def test_unknown_op_raises(self):
        from agente_ia_edu.services.essay_correction_edits import apply_annotations_patch
        with self.assertRaises(ValueError) as caught:
            apply_annotations_patch(
                self._ai_output(), [{"op": "rename", "letter": "A"}],
                canonical_text=TEXT, anchor_mode="TEXT_OFFSET",
            )
        self.assertIn("rename", str(caught.exception))
