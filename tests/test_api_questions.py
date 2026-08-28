import unittest
from uuid import uuid4

from fastapi.testclient import TestClient

from agente_ia_edu.api.app import app
from agente_ia_edu.api.routes.questions import get_question_service
from agente_ia_edu.api.schemas.questions import Pagination, QuestionListResponse


class FakeQuestionService:
    async def list_questions(self, **kwargs):
        return QuestionListResponse(
            items=[],
            pagination=Pagination(
                page=kwargs["page"],
                limit=kwargs["limit"],
                total=0,
            ),
        )

    async def get_question(self, question_id, *, include_answer_key=False):
        return None


class QuestionsEndpointTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.service = FakeQuestionService()
        app.dependency_overrides[get_question_service] = lambda: cls.service

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()

    def test_list_returns_items_and_pagination_defaults(self):
        with TestClient(app) as client:
            response = client.get("/api/v1/questions")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json(), {"items": [], "pagination": {"page": 1, "limit": 20, "total": 0}})

    def test_list_accepts_page_and_limit(self):
        with TestClient(app) as client:
            response = client.get("/api/v1/questions?page=2&limit=100")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["pagination"], {"page": 2, "limit": 100, "total": 0})

    def test_limit_maximum_is_enforced(self):
        with TestClient(app) as client:
            response = client.get("/api/v1/questions?limit=101")

        self.assertEqual(response.status_code, 422)

    def test_detail_not_found_returns_404(self):
        with TestClient(app) as client:
            response = client.get(f"/api/v1/questions/{uuid4()}")

        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
