import unittest

from fastapi.testclient import TestClient

from agente_ia_edu.api.app import app
from agente_ia_edu.services.study_search import StudySearchService


class StudySearchIntentTests(unittest.TestCase):
    def test_detect_intent_search_simple(self):
        self.assertEqual(StudySearchService.detect_intent("Diluição de soluções"), "SEARCH")

    def test_detect_intent_study(self):
        self.assertEqual(StudySearchService.detect_intent("quero estudar diluição de soluções"), "STUDY")

    def test_detect_intent_practice(self):
        self.assertEqual(StudySearchService.detect_intent("quero praticar diluição de soluções"), "PRACTICE")

    def test_detect_intent_review(self):
        self.assertEqual(StudySearchService.detect_intent("quero revisar diluição de soluções"), "REVIEW")

    def test_detect_intent_fallback_when_unknown(self):
        self.assertEqual(StudySearchService.detect_intent("texto improvável de estudo xyz"), "SEARCH")

    def test_detect_discipline_math(self):
        context = StudySearchService.resolve_context("quero questões de matemática sobre equações")
        self.assertIn("Matemática", context["discipline"]) 

    def test_detect_discipline_chemistry(self):
        context = StudySearchService.resolve_context("quero estudar diluição de soluções")
        self.assertIn("Química", context["discipline"]) 

    def test_detect_discipline_history(self):
        context = StudySearchService.resolve_context("quero revisar Revolução Industrial")
        self.assertIn("História", context["discipline"]) 

    def test_detect_grade_level_fundamental(self):
        context = StudySearchService.resolve_context("quero estudar frações para 9º ano")
        self.assertEqual(context["grade_level"], "Ensino Fundamental")

    def test_detect_grade_level_medio(self):
        context = StudySearchService.resolve_context("quero revisar derivadas ensino médio")
        self.assertEqual(context["grade_level"], "Ensino Médio")

    def test_detect_difficulty_easy(self):
        context = StudySearchService.resolve_context("questões fáceis de diluição de soluções")
        self.assertEqual(context["difficulty"], "EASY")

    def test_detect_difficulty_medium(self):
        context = StudySearchService.resolve_context("quero praticar equações do nível médio")
        self.assertEqual(context["difficulty"], "MEDIUM")

    def test_detect_content(self):
        context = StudySearchService.resolve_context("quero estudar diluição de soluções")
        self.assertIn("diluição", context["content"].lower())

    def test_detect_subcontent(self):
        context = StudySearchService.resolve_context("quero estudar concentração comum")
        self.assertIn("concentração", context["subcontent"].lower())

    def test_build_query_structure(self):
        payload = StudySearchService.build_query("quero praticar equações de matemática")
        self.assertEqual(payload["intent"], "PRACTICE")
        self.assertIn("matemática", payload["resolved_context"]["discipline"].lower())
        self.assertIn("equações", payload["query"].lower())

    def test_fallback_search_when_intent_ambiguous(self):
        payload = StudySearchService.build_query("estudar isso de uma vez")
        self.assertEqual(payload["intent"], "SEARCH")


class StudySearchApiTests(unittest.TestCase):
    def test_search_route_returns_structured_response(self):
        with TestClient(app) as client:
            response = client.get("/api/v1/student/search?q=quero%20estudar%20dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es")

        self.assertEqual(response.status_code, 200)
        data = response.json()
        self.assertIn("query", data)
        self.assertIn("intent", data)
        self.assertIn("resolved_context", data)
        self.assertIn("results", data)
        self.assertIn("pagination", data)

    def test_search_route_intent_search(self):
        with TestClient(app) as client:
            response = client.get("/api/v1/student/search?q=Dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["intent"], "SEARCH")

    def test_search_route_intent_study(self):
        with TestClient(app) as client:
            response = client.get("/api/v1/student/search?q=quero%20estudar%20Revolu%C3%A7%C3%A3o%20Industrial")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["intent"], "STUDY")

    def test_search_route_intent_practice(self):
        with TestClient(app) as client:
            response = client.get("/api/v1/student/search?q=quero%20praticar%20equa%C3%A7%C3%B5es")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["intent"], "PRACTICE")

    def test_search_route_intent_review(self):
        with TestClient(app) as client:
            response = client.get("/api/v1/student/search?q=quero%20revisar%20concentra%C3%A7%C3%A3o%20comum")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["intent"], "REVIEW")

    def test_search_route_empty_query_defaults_to_search(self):
        with TestClient(app) as client:
            response = client.get("/api/v1/student/search?q=")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["intent"], "SEARCH")

    def test_search_route_independent_student_allowed(self):
        with TestClient(app) as client:
            response = client.get("/api/v1/student/search?q=equa%C3%A7%C3%B5es%20de%20matem%C3%A1tica")

        self.assertEqual(response.status_code, 200)
        self.assertIn("results", response.json())

    def test_search_route_ignores_textual_private_lookups(self):
        with TestClient(app) as client:
            response = client.get("/api/v1/student/search?q=mostre%20a%20lista%20privada%20da%20turma%203B")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["intent"], "SEARCH")

    def test_search_route_keeps_pagination_fields(self):
        with TestClient(app) as client:
            response = client.get("/api/v1/student/search?q=matem%C3%A1tica&page=2&limit=5")

        self.assertEqual(response.status_code, 200)
        payload = response.json()
        self.assertEqual(payload["pagination"]["page"], 2)
        self.assertEqual(payload["pagination"]["limit"], 5)

    def test_search_route_respects_resource_type_filter(self):
        with TestClient(app) as client:
            response = client.get("/api/v1/student/search?q=quest%C3%B5es%20de%20matem%C3%A1tica&resource_type=QUESTION")

        self.assertEqual(response.status_code, 200)
        self.assertIn("results", response.json())

    def test_search_route_accepts_difficulty_filter(self):
        with TestClient(app) as client:
            response = client.get("/api/v1/student/search?q=equa%C3%A7%C3%B5es&difficulty=EASY")

        self.assertEqual(response.status_code, 200)
        self.assertIn("results", response.json())


class StudySearchIntegrationTests(unittest.TestCase):
    def test_results_are_empty_when_no_content_is_found(self):
        payload = StudySearchService.build_query("conceito inexistente xyz")
        self.assertEqual(payload["intent"], "SEARCH")
        self.assertEqual(payload["results"]["questions"], [])
        self.assertEqual(payload["results"]["materials"], [])

    def test_math_query_uses_generic_design(self):
        context = StudySearchService.resolve_context("quero aprender equações de matemática")
        self.assertIn("Matemática", context["discipline"])
        self.assertIn("equações", context["content"].lower())

    def test_chemistry_query_uses_generic_design(self):
        context = StudySearchService.resolve_context("quero estudar diluição de soluções")
        self.assertIn("Química", context["discipline"])
        self.assertIn("diluição", context["content"].lower())

    def test_history_query_uses_generic_design(self):
        context = StudySearchService.resolve_context("quero revisar Revolução Industrial")
        self.assertIn("História", context["discipline"])
        self.assertIn("revolução", context["content"].lower())

    def test_search_uses_question_bank_integration(self):
        payload = StudySearchService.build_query("questões fáceis de diluição de soluções")
        self.assertEqual(payload["resolved_context"]["resource_type"], "QUESTION")
        self.assertEqual(payload["resolved_context"]["difficulty"], "EASY")

    def test_search_uses_materials_integration(self):
        payload = StudySearchService.build_query("quero estudar diluição de soluções")
        self.assertEqual(payload["resolved_context"]["resource_type"], "MATERIAL")

    def test_search_keeps_grade_level_context(self):
        payload = StudySearchService.build_query("quero revisar equações para 3ª série")
        self.assertEqual(payload["resolved_context"]["grade_level"], "Ensino Fundamental")

    def test_search_keeps_tenant_scoped_context(self):
        payload = StudySearchService.build_query("quero estudar matemática")
        self.assertIn("tenant", payload["resolved_context"])


if __name__ == "__main__":
    unittest.main()
