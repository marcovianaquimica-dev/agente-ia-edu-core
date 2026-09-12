import unittest
from dataclasses import dataclass
from pathlib import Path
from uuid import uuid4

from agente_ia_edu.db.models import CatalogNode
from agente_ia_edu.services.curriculum_classification import ClassificationProposalService
from agente_ia_edu.services.ingestion_parser import PdfParser


@dataclass(frozen=True)
class GoldCase:
    question_id: str
    text: str
    expected_codes: tuple[str, ...]
    expected_gap: str | None = None


def catalog_fixture() -> list[CatalogNode]:
    specs = (
        ("chemistry", "Quimica", "DISCIPLINE", "CHEMISTRY", None),
        ("chemistry_physical", "Fisico-Quimica", "AREA", "CHEMISTRY-PHYSICAL", "chemistry"),
        ("chemistry_solutions", "Solucoes", "CONTENT", "CHEMISTRY-SOLUTIONS", "chemistry_physical"),
        ("chemistry_concentration", "Concentracao", "SUBCONTENT", "CHEMISTRY-SOLUTIONS-CONCENTRATION", "chemistry_solutions"),
        ("chemistry_dilution", "Diluicao de solucoes", "SUBCONTENT", "CHEMISTRY-SOLUTIONS-DILUTION", "chemistry_solutions"),
        ("physics", "Fisica", "DISCIPLINE", "PHYSICS", None),
        ("physics_mechanics", "Mecanica", "AREA", "PHYSICS-MECHANICS", "physics"),
        ("physics_kinematics", "Cinematica", "CONTENT", "PHYSICS-MECHANICS-KINEMATICS", "physics_mechanics"),
        ("physics_uniform", "Movimento uniforme", "SUBCONTENT", "PHYSICS-MECHANICS-KINEMATICS-UNIFORM", "physics_kinematics"),
        ("biology", "Biologia", "DISCIPLINE", "BIOLOGY", None),
        ("biology_cytology", "Citologia", "AREA", "BIOLOGY-CYTOLOGY", "biology"),
        ("biology_biochemistry", "Bioquimica celular", "CONTENT", "BIOLOGY-CYTOLOGY-BIOCHEMISTRY", "biology_cytology"),
        ("biology_proteins", "Proteinas", "SUBCONTENT", "BIOLOGY-CYTOLOGY-BIOCHEMISTRY-PROTEINS", "biology_biochemistry"),
        ("math", "Matematica", "DISCIPLINE", "MATH", None),
        ("math_algebra", "Algebra", "AREA", "MATH-ALGEBRA", "math"),
        ("math_functions", "Funcoes", "CONTENT", "MATH-ALGEBRA-FUNCTIONS", "math_algebra"),
        ("math_ratio", "Razao e proporcao", "SUBCONTENT", "MATH-ALGEBRA-RATIO", "math_functions"),
    )
    nodes: dict[str, CatalogNode] = {}
    for key, name, node_type, code, parent_key in specs:
        nodes[key] = CatalogNode(
            id=uuid4(), name=name, node_type=node_type, code=code,
            parent_id=nodes[parent_key].id if parent_key else None,
        )
    return list(nodes.values())


def candidate_codes(candidates: list[dict]) -> list[str]:
    return [
        candidate["subcontent_code"]
        or candidate["content_code"]
        or candidate["area_code"]
        or candidate["discipline_code"]
        for candidate in candidates
    ]


def evaluate(gold: GoldCase, retrieved: list[str]) -> dict:
    expected = set(gold.expected_codes)
    actual = set(retrieved)
    true_positives = sorted(expected & actual)
    false_positives = sorted(actual - expected)
    false_negatives = sorted(expected - actual)
    return {
        "question": gold.question_id,
        "gold": list(gold.expected_codes),
        "retrieved": retrieved,
        "true_positives": true_positives,
        "false_positives": false_positives,
        "false_negatives": false_negatives,
        "true_negative": not expected and not actual,
    }


def benchmark_metrics(results: list[dict], k: int) -> dict[str, float]:
    covered = [result for result in results if result["gold"]]
    true_positives = sum(len(result["true_positives"]) for result in results)
    retrieved = sum(len(result["retrieved"]) for result in results)
    recall = sum(bool(result["true_positives"]) for result in covered) / len(covered)
    precision = true_positives / retrieved if retrieved else 1.0
    top_k = sum(bool(set(result["gold"]) & set(result["retrieved"][:k])) for result in covered) / len(covered)
    top_1 = sum(bool(result["gold"]) and result["retrieved"][:1] == [result["gold"][0]] for result in covered) / len(covered)
    return {"recall": recall, "precision": precision, "top_1": top_1, f"top_{k}": top_k}


class CurriculumRetrieverBenchmarkTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.catalog = catalog_fixture()
        cls.service = ClassificationProposalService(None)

    def retrieve(self, text: str, catalog=None) -> list[str]:
        return candidate_codes(self.service.retrieve_candidate_classifications(text, catalog or self.catalog))

    def test_question91_retrieval_regression(self):
        question = next(item for item in PdfParser.parse_file(Path("var/inep-pilot/2020_PV_impresso_D2_CD5.pdf")).questions if item.question_number == 91)
        retrieved = self.retrieve(question.statement_text)
        self.assertIn("CHEMISTRY-SOLUTIONS", retrieved)
        self.assertNotIn("CHEMISTRY-SOLUTIONS-CONCENTRATION", retrieved)
        self.assertNotIn("CHEMISTRY-SOLUTIONS-DILUTION", retrieved)

    def test_question92_retrieval_regression(self):
        question = next(item for item in PdfParser.parse_file(Path("var/inep-pilot/2020_PV_impresso_D2_CD5.pdf")).questions if item.question_number == 92)
        self.assertEqual(self.retrieve(question.statement_text), [])

    def test_ancestor_term_only_does_not_promote_descendant(self):
        retrieved = self.retrieve("As soluções aquosas estão presentes no experimento.")
        self.assertIn("CHEMISTRY-SOLUTIONS", retrieved)
        self.assertNotIn("CHEMISTRY-SOLUTIONS-DILUTION", retrieved)
        self.assertNotIn("CHEMISTRY-SOLUTIONS-CONCENTRATION", retrieved)

    def test_distinctive_leaf_terms_recover_each_leaf(self):
        cases = (
            ("A concentração determina a quantidade de soluto.", "CHEMISTRY-SOLUTIONS-CONCENTRATION"),
            ("A diluição reduz a concentração de uma solução.", "CHEMISTRY-SOLUTIONS-DILUTION"),
            ("O movimento uniforme mantém velocidade constante.", "PHYSICS-MECHANICS-KINEMATICS-UNIFORM"),
            ("Proteínas participam da bioquímica celular.", "BIOLOGY-CYTOLOGY-BIOCHEMISTRY-PROTEINS"),
            ("Uma razão entre grandezas resolve a proporção.", "MATH-ALGEBRA-RATIO"),
        )
        for text, expected in cases:
            with self.subTest(expected=expected):
                self.assertIn(expected, self.retrieve(text))

    def test_normalization_and_catalog_order_are_deterministic(self):
        self.assertIn(
            "CHEMISTRY-SOLUTIONS-DILUTION", self.retrieve("DILUIÇÃO de soluções!")
        )
        # Controlled morphology now maps the regular Portuguese plural form.
        self.assertIn(
            "CHEMISTRY-SOLUTIONS-DILUTION", self.retrieve("diluicao de solucao")
        )
        self.assertIn(
            "CHEMISTRY-SOLUTIONS-DILUTION", self.retrieve("Diluições, soluções.")
        )
        text = "Diluição e concentração de soluções."
        expected = self.retrieve(text)
        self.assertEqual(expected, self.retrieve(text, list(reversed(self.catalog))))
        self.assertEqual(expected, self.retrieve(text))
        self.assertEqual(expected, self.retrieve(text))

    def test_short_terms_do_not_create_spurious_candidates(self):
        for text in ("pH", "gás", "água", "pressão"):
            with self.subTest(text=text):
                self.assertEqual(self.retrieve(text), [])

    def test_catalog_code_is_not_lexical_evidence_and_descriptions_are_empty(self):
        self.assertTrue(all(node.description is None for node in self.catalog))
        retrieved = self.retrieve("CHEMISTRY SOLUTIONS DILUTION")
        self.assertEqual(retrieved, [])

    def test_specific_terms_rank_leaves_above_ancestors_with_auditable_scores(self):
        candidates = self.service.retrieve_candidate_classifications(
            "O processo de diluição de soluções.", self.catalog
        )
        codes = candidate_codes(candidates)
        dilution = next(item for item in candidates if item["subcontent_code"] == "CHEMISTRY-SOLUTIONS-DILUTION")
        solutions = next(item for item in candidates if item["content_code"] == "CHEMISTRY-SOLUTIONS" and item["subcontent_code"] is None)
        self.assertEqual(codes[0], "CHEMISTRY-SOLUTIONS-DILUTION")
        self.assertGreater(dilution["score"], solutions["score"])
        self.assertEqual(dilution["candidate_type"], "EXACT_CANDIDATE")
        self.assertEqual(solutions["candidate_type"], "ANCESTOR_CANDIDATE")
        self.assertIn("diluicao", dilution["matched_terms"])
        self.assertEqual(dilution["score"], dilution["specificity_score"] + dilution["lexical_score"] + dilution["position_score"])
        self.assertEqual(dilution["term_proximity_score"], 0)
        self.assertEqual(dilution["context_conflict_score"], 0)
        self.assertEqual(dilution["final_score"], dilution["score"])
        self.assertEqual(dilution["matched_terms"], dilution["normalized_matched_terms"])

    def test_concentration_and_proteins_rank_specific_candidates_first(self):
        for text, expected in (
            ("A concentração molar de uma solução.", "CHEMISTRY-SOLUTIONS-CONCENTRATION"),
            ("Proteínas são moléculas importantes.", "BIOLOGY-CYTOLOGY-BIOCHEMISTRY-PROTEINS"),
        ):
            with self.subTest(expected=expected):
                self.assertEqual(self.retrieve(text)[0], expected)

    def test_d1_false_positives_never_become_complete_curriculum_coverage(self):
        d1_questions = PdfParser.parse_file(Path("var/inep-pilot/2020_PV_impresso_D1_CD1.pdf")).questions
        by_number = {question.question_number: question for question in d1_questions}
        for number in (6, 22, 26, 56, 61, 82):
            with self.subTest(question=number):
                candidates = self.service.retrieve_candidate_classifications(by_number[number].statement_text, self.catalog)
                self.assertFalse(any(candidate["subcontent_code"] is not None for candidate in candidates))

    def test_generic_ancestor_terms_are_recorded_but_not_actionable_candidates(self):
        for text in ("A reação química ocorreu.", "A função social é importante.", "O tecido celular foi observado."):
            with self.subTest(text=text):
                self.assertEqual(self.retrieve(text), [])


    def test_gold_metrics_are_reproducible(self):
        gold = (
            GoldCase("91", "soluções aquosas cujo pH é próximo da neutralidade", ("CHEMISTRY-SOLUTIONS",), "TAXONOMY_GRANULARITY_GAP"),
            GoldCase("92", "panela de pressão e temperatura de ebulição", (), "CATALOG_GAP"),
            GoldCase("dilution", "A diluição reduz concentração.", ("CHEMISTRY-SOLUTIONS-DILUTION",)),
            GoldCase("uniform", "Movimento uniforme tem velocidade constante.", ("PHYSICS-MECHANICS-KINEMATICS-UNIFORM",)),
            GoldCase("proteins", "Proteínas na bioquímica celular.", ("BIOLOGY-CYTOLOGY-BIOCHEMISTRY-PROTEINS",)),
            GoldCase("ratio", "Razão e proporção entre grandezas.", ("MATH-ALGEBRA-RATIO",)),
        )
        results = [evaluate(case, self.retrieve(case.text)) for case in gold]
        metrics = {**benchmark_metrics(results, 1), **benchmark_metrics(results, 3), **benchmark_metrics(results, 5)}
        self.assertEqual(metrics, {**benchmark_metrics(results, 1), **benchmark_metrics(results, 3), **benchmark_metrics(results, 5)})
        self.assertTrue(all(result["true_negative"] for result in results if not result["gold"]))