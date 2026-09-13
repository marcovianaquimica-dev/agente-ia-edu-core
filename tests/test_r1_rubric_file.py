import unittest

from agente_ia_edu.rubrics.loader import load_rubric_file

OFFICIAL_SHA256 = "d8ab44dcbf5af808829d9dee89d23e7efa4f59df022b99102fac87489b870288"


class TestEnem2025RubricFile(unittest.TestCase):
    def setUp(self):
        self.rubric = load_rubric_file("enem_2025")

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

    def test_no_descriptor_carries_an_undecoded_glyph(self):
        """The decoder marks what it could not read. A marker surviving into the
        reviewed file means the human review missed a cell."""
        for competency in self.rubric.competencies:
            for level in competency.levels:
                with self.subTest(competency=competency.code, points=level.points):
                    self.assertNotIn("⟨?⟩", level.descriptor)

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

    def test_declares_the_annulment_rules(self):
        keys = {rule.key for rule in self.rubric.zero_rules}
        self.assertIn("fuga_ao_tema", keys)

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
            "zero_rules": [],
        }
        with self.assertRaises(RubricFileError):
            parse_rubric_mapping(broken)
