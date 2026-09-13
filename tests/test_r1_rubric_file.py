import unittest

from agente_ia_edu.rubrics.loader import load_rubric_file
from tools.extract_cartilha_enem import UNDECIDED_MARKER, UNDECODED_MARKER

OFFICIAL_SHA256 = "d8ab44dcbf5af808829d9dee89d23e7efa4f59df022b99102fac87489b870288"

# Verbatim from p. 35 of the PDF. C4's text layer is unrecoverable (the font
# subset maps a/b/c to control characters), so no future reviewer can
# cross-check these against extracted text - this test is the only guard
# against a truncated or paraphrased descriptor slipping through review.
C4_DESCRIPTORS_BY_POINTS = {
    200: (
        "Articula bem as partes do texto e apresenta repertório diversificado "
        "de recursos coesivos."
    ),
    160: (
        "Articula as partes do texto, com poucas inadequações, e apresenta "
        "repertório diversificado de recursos coesivos."
    ),
    120: (
        "Articula as partes do texto, de forma mediana, com inadequações, e "
        "apresenta repertório pouco diversificado de recursos coesivos."
    ),
    80: (
        "Articula as partes do texto, de forma insuficiente, com muitas "
        "inadequações, e apresenta repertório limitado de recursos coesivos."
    ),
    40: "Articula as partes do texto de forma precária.",
    0: "Não articula as informações.",
}


class TestEnem2025RubricFile(unittest.TestCase):
    def setUp(self):
        self.rubric = load_rubric_file("enem_2025")

    def _all_cartilha_text_fields(self):
        """Every string in the file that carries transcribed cartilha wording:
        level descriptors, competency official titles, signal labels and
        descriptions, and scoring-rule labels and descriptions."""
        fields: list[tuple[str, str]] = []
        for competency in self.rubric.competencies:
            fields.append((f"{competency.code}.official_title", competency.official_title))
            for level in competency.levels:
                fields.append(
                    (f"{competency.code}/{level.points}.descriptor", level.descriptor)
                )
            for signal in competency.signals:
                fields.append((f"signal:{signal.key}.label", signal.label))
                if signal.description:
                    fields.append((f"signal:{signal.key}.description", signal.description))
        for rule in self.rubric.scoring_rules:
            fields.append((f"rule:{rule.key}.label", rule.label))
            if rule.description:
                fields.append((f"rule:{rule.key}.description", rule.description))
        return fields

    def test_declares_the_official_source_and_its_hash(self):
        self.assertEqual(self.rubric.rubric_version, "ENEM_2025")
        self.assertEqual(self.rubric.official_source_sha256, OFFICIAL_SHA256)
        self.assertTrue(self.rubric.official_source_url.startswith("https://download.inep.gov.br/"))

    def test_has_the_five_competencies_in_order(self):
        self.assertEqual(
            [c.code for c in self.rubric.competencies], ["C1", "C2", "C3", "C4", "C5"]
        )

    def test_has_exactly_six_levels_per_competency(self):
        for competency in self.rubric.competencies:
            with self.subTest(competency=competency.code):
                self.assertEqual(
                    sorted(level.points for level in competency.levels),
                    [0, 40, 80, 120, 160, 200],
                )

    def test_every_level_cites_its_source_page(self):
        for competency in self.rubric.competencies:
            for level in competency.levels:
                with self.subTest(competency=competency.code, points=level.points):
                    self.assertIsInstance(level.source_page, int)
                    self.assertGreater(level.source_page, 0)

    def test_c4_descriptors_are_pinned_verbatim(self):
        """C4's descriptors cannot be cross-checked against the PDF's text
        layer at all (it is unrecoverable), so this is the only guard against
        a truncated or paraphrased cell reaching the seed."""
        c4 = next(c for c in self.rubric.competencies if c.code == "C4")
        by_points = {level.points: level.descriptor for level in c4.levels}
        for points, expected in C4_DESCRIPTORS_BY_POINTS.items():
            with self.subTest(points=points):
                self.assertEqual(by_points[points], expected)

    def test_no_text_field_carries_an_undecoded_or_undecided_marker(self):
        """The decoder marks what it could not read (UNDECODED_MARKER) and what
        it suspected was encoded but declined to decode (UNDECIDED_MARKER). Either
        marker surviving into the reviewed file means the human review missed a
        cell - across every field that came from the cartilha, not just level
        descriptors."""
        for field_name, value in self._all_cartilha_text_fields():
            with self.subTest(field=field_name):
                self.assertNotIn(UNDECODED_MARKER, value)
                self.assertNotIn(UNDECIDED_MARKER, value)

    def test_every_signal_declares_provenance_and_backs_it_up(self):
        for competency in self.rubric.competencies:
            for signal in competency.signals:
                with self.subTest(signal=signal.key):
                    self.assertIn(
                        signal.provenance,
                        ("OFICIAL_INEP", "INTERPRETACAO_PEDAGOGICA", "HEURISTICA_MOTOR"),
                    )
                    if signal.provenance == "HEURISTICA_MOTOR":
                        self.assertTrue(signal.rationale)
                    else:
                        self.assertTrue(signal.source_ref)

    def test_every_level_declares_provenance_defaulting_to_oficial_inep(self):
        """enem_2025.yaml never sets ``provenance:`` on a level - the default
        (OFICIAL_INEP) is correct for every one of its 30 descriptors, and this
        pins that the loader actually fills it in rather than leaving it unset."""
        for competency in self.rubric.competencies:
            for level in competency.levels:
                with self.subTest(competency=competency.code, points=level.points):
                    self.assertEqual(level.provenance, "OFICIAL_INEP")

    def test_declares_the_annulment_rules(self):
        keys = {rule.key for rule in self.rubric.scoring_rules}
        self.assertIn("fuga_ao_tema", keys)

    def test_declares_the_tangenciamento_caps_on_c3_and_c5(self):
        by_key = {rule.key: rule for rule in self.rubric.scoring_rules}
        c3_cap = by_key["tangenciamento_teto_c3"]
        c5_cap = by_key["tangenciamento_teto_c5"]
        self.assertEqual(c3_cap.effect, "LIMITA_PONTUACAO")
        self.assertEqual(c3_cap.max_points, 40)
        self.assertEqual(c3_cap.competency_code, "C3")
        self.assertEqual(c5_cap.effect, "LIMITA_PONTUACAO")
        self.assertEqual(c5_cap.max_points, 40)
        self.assertEqual(c5_cap.competency_code, "C5")

    def test_rejects_a_competency_missing_a_level(self):
        from agente_ia_edu.rubrics.loader import RubricFileError, parse_rubric_mapping

        broken = {
            "rubric_version": "BROKEN",
            "label": "broken",
            "competencies": [
                {
                    "code": "C1", "ordinal": 1, "official_title": "t", "source_page": 1,
                    "levels": [{"points": 200, "descriptor": "d", "source_page": 1}],
                    "signals": [],
                }
            ],
            "scoring_rules": [],
        }
        with self.assertRaises(RubricFileError):
            parse_rubric_mapping(broken)

    def _base_broken_rubric(self, scoring_rule):
        return {
            "rubric_version": "BROKEN",
            "label": "broken",
            "competencies": [
                {
                    "code": code, "ordinal": i, "official_title": "t", "source_page": 1,
                    "levels": [
                        {"points": p, "descriptor": "d", "source_page": 1}
                        for p in (0, 40, 80, 120, 160, 200)
                    ],
                    "signals": [],
                }
                for i, code in enumerate(["C1", "C2", "C3", "C4", "C5"], start=1)
            ],
            "scoring_rules": [scoring_rule],
        }

    def test_rejects_a_level_with_an_unknown_provenance(self):
        """Built on ``_base_broken_rubric`` (all five competencies fully valid,
        with a valid scoring rule) so the only possible source of the
        RubricFileError is the corrupted level provenance - not a missing
        competency or an unrelated structural gap."""
        from agente_ia_edu.rubrics.loader import RubricFileError, parse_rubric_mapping

        broken = self._base_broken_rubric({
            "key": "fuga_ao_tema", "label": "bad", "effect": "ANULA_REDACAO",
            "competency_code": None, "provenance": "OFICIAL_INEP",
        })
        broken["competencies"][0]["levels"][-1]["provenance"] = "NOPE"
        with self.assertRaises(RubricFileError):
            parse_rubric_mapping(broken)

    def test_rejects_a_scoring_rule_with_an_unknown_provenance(self):
        from agente_ia_edu.rubrics.loader import RubricFileError, parse_rubric_mapping

        broken = self._base_broken_rubric({
            "key": "bad_provenance",
            "label": "bad",
            "effect": "ANULA_REDACAO",
            "competency_code": None,
            "provenance": "NOPE",
        })
        with self.assertRaises(RubricFileError):
            parse_rubric_mapping(broken)

    def test_rejects_a_zera_competencia_rule_without_competency_code(self):
        from agente_ia_edu.rubrics.loader import RubricFileError, parse_rubric_mapping

        broken = self._base_broken_rubric({
            "key": "bad_zera",
            "label": "bad",
            "effect": "ZERA_COMPETENCIA",
            "competency_code": None,
            "provenance": "OFICIAL_INEP",
        })
        with self.assertRaises(RubricFileError):
            parse_rubric_mapping(broken)

    def test_rejects_a_limita_pontuacao_rule_without_max_points(self):
        from agente_ia_edu.rubrics.loader import RubricFileError, parse_rubric_mapping

        broken = self._base_broken_rubric({
            "key": "bad_limita",
            "label": "bad",
            "effect": "LIMITA_PONTUACAO",
            "competency_code": "C3",
            "provenance": "OFICIAL_INEP",
        })
        with self.assertRaises(RubricFileError):
            parse_rubric_mapping(broken)

    def test_rejects_a_limita_pontuacao_rule_with_an_off_scale_max_points(self):
        from agente_ia_edu.rubrics.loader import RubricFileError, parse_rubric_mapping

        broken = self._base_broken_rubric({
            "key": "bad_limita_value",
            "label": "bad",
            "effect": "LIMITA_PONTUACAO",
            "competency_code": "C3",
            "max_points": 137,
            "provenance": "OFICIAL_INEP",
        })
        with self.assertRaises(RubricFileError):
            parse_rubric_mapping(broken)
