import unittest

from agente_ia_edu.essay_prompts import competency_scoring_v3


class CompetencyScoringTopBandSymmetryTests(unittest.TestCase):
    """Fase D (2026-10-08): the controlled experiment found the engine
    under-scores by a large, consistent margin even on hand-verified clean
    text. _RULES_TOP_BAND's closing instruction in v2 - "na duvida genuina,
    prefira 160" - was added to fight a DIFFERENT, opposite problem (too
    many perfect-1000 over-scores) and is a plausible contributor to this
    now-opposite bias, since it tilts every genuinely ambiguous top-band
    judgment call downward with no counterweight. v3 removes that one-sided
    tie-break without removing the ability to ever choose 160 when the
    evidence genuinely points that way.
    """

    def _prompt(self, **overrides):
        kwargs = dict(
            competency_code="C1", competency_label="Dominio da norma padrao",
            levels=[(200, "excelente"), (160, "bom"), (120, "mediano"),
                    (80, "insuficiente"), (40, "precario"), (0, "desconhecimento")],
            annotations=[{"short_comment": "erro pontual", "long_comment": "detalhe do erro"}],
            mechanical_review=[],
            rationale=None,
        )
        kwargs.update(overrides)
        return competency_scoring_v3.build_prompt(**kwargs)

    def test_version_is_set(self):
        self.assertEqual(competency_scoring_v3.VERSION, "competency_scoring_v3")

    def test_top_band_no_longer_has_a_one_sided_preference_for_160(self):
        prompt = self._prompt()
        self.assertNotIn("prefira 160", prompt.lower())

    def test_top_band_has_the_new_symmetric_tie_break_mechanism(self):
        # Asserting the absence of the old one-sided phrase is not enough -
        # a future edit could reintroduce the same one-sided default under
        # different wording (e.g. "em caso de duvida, incline-se para 160")
        # and this test file would not notice. Assert the actual NEW
        # mechanism's own phrasing is present: decide by the weight of the
        # evidence, and name a true tie explicitly in reasoning instead of
        # mechanically picking the lower band.
        prompt = self._prompt().lower()
        self.assertIn("maioria da evidencia", prompt)
        self.assertIn("registre essa incerteza", prompt)

    def test_top_band_still_blocks_200_when_evidence_shows_a_real_gap(self):
        # The case-(b) negative-evidence-gap instruction (the real
        # mechanism that stops 200 from being handed out when there IS a
        # genuine lacuna) must survive - v3 only removes the mechanical
        # "default to 160 under doubt" tie-break, not the underlying rule
        # that a real gap disqualifies 200.
        prompt = self._prompt()
        self.assertIn("desqualifica 200", prompt)
        self.assertIn("nivel correto neste caso e 160", prompt)


if __name__ == "__main__":
    unittest.main()
