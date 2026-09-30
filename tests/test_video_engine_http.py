"""HTTP-layer tests for video_router (src/agente_ia_edu/api/routes/video_engine.py).

tests/test_video_engine.py already covers ResourceTrackingService and
VideoRecommendationEngine directly at the service layer, plus one HTTP test
for the /events 400 path. It never exercises: the /feedback endpoint's HTTP
path, /request-another (success and NO_VIDEO_AVAILABLE), or /{video_id}/progress
- all of which are FastAPI endpoint functions with their own request parsing,
identity extraction (student_id from ExternalIdentityContext), and response
shaping (e.g. stripping the internal ``video_object`` key from the JSON
response) that a direct service-layer call can never prove.
"""

import asyncio
import unittest
from uuid import uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.video_engine import video_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    ContentResourceLink,
    EducationalResource,
    VideoResourceDetail,
)
from agente_ia_edu.identity import ExternalIdentityContext


class VideoEngineHTTP(unittest.TestCase):
    def setUp(self):
        async def setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                root = CatalogNode(node_type="DISCIPLINE", name="Quimica", position=1, active=True)
                session.add(root)
                await session.flush()
                root.root_id = root.id
                content_node = CatalogNode(
                    parent_id=root.id, root_id=root.id, node_type="CONTENT",
                    name="Diluicao de Solucoes", position=1, active=True,
                )
                session.add(content_node)
                await session.flush()

                video = EducationalResource(
                    title="Diluicao - Conceito Basico",
                    resource_type="VIDEO",
                    origin_type="PLATFORM",
                    visibility_scope="PUBLIC",
                    source_url="https://youtube.com/watch?v=abc",
                    status="active",
                )
                session.add(video)
                await session.flush()
                session.add(VideoResourceDetail(
                    resource_id=video.id, platform="YOUTUBE", external_video_id="abc", duration_seconds=300,
                ))
                session.add(ContentResourceLink(
                    content_node_id=content_node.id, resource_id=video.id,
                    pedagogical_role="VIDEO", recommended_level="EASY",
                ))
                await session.commit()

                empty_content_node = CatalogNode(
                    parent_id=root.id, root_id=root.id, node_type="CONTENT",
                    name="Sem Videos", position=2, active=True,
                )
                session.add(empty_content_node)
                await session.commit()

                return engine, factory, content_node.id, video.id, empty_content_node.id

        (
            self.engine,
            self.session_factory,
            self.content_node_id,
            self.video_id,
            self.empty_content_node_id,
        ) = asyncio.run(setup())

        self.identity = {
            "value": ExternalIdentityContext(provider="test", external_user_id="student-a", roles=("student",))
        }
        app = FastAPI()
        app.include_router(video_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    # -- record_video_feedback (POST /feedback) --------------------------------

    def test_record_feedback_returns_201(self):
        response = self.client.post(
            "/api/v1/videos/feedback",
            json={
                "resource_id": str(self.video_id),
                "feedback_type": "LIKED",
                "feedback_reason": "NEEDS_EXAMPLES",
            },
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["student_id"], "student-a")
        self.assertEqual(body["action_type"], "FEEDBACK")
        self.assertEqual(body["feedback_type"], "LIKED")

    # -- request_another_video (POST /request-another) -------------------------

    def test_request_another_video_returns_next_candidate(self):
        response = self.client.post(
            "/api/v1/videos/request-another",
            json={
                "content_node_id": str(self.content_node_id),
                "current_video_id": str(uuid4()),
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "OK")
        self.assertEqual(body["video_resource_id"], str(self.video_id))
        self.assertNotIn("video_object", body)

    def test_request_another_video_no_candidates_returns_no_video_available(self):
        response = self.client.post(
            "/api/v1/videos/request-another",
            json={
                "content_node_id": str(self.empty_content_node_id),
                "current_video_id": str(uuid4()),
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "NO_VIDEO_AVAILABLE")
        self.assertIsNone(body["video"])

    def test_request_another_video_records_feedback_when_provided(self):
        response = self.client.post(
            "/api/v1/videos/request-another",
            json={
                "content_node_id": str(self.content_node_id),
                "current_video_id": str(self.video_id),
                "feedback_type": "DISLIKED",
                "feedback_reason": "TOO_FAST",
            },
        )
        self.assertEqual(response.status_code, 200, response.text)
        # current_video_id is excluded from candidates -> nothing else available
        self.assertEqual(response.json()["status"], "NO_VIDEO_AVAILABLE")

        progress = self.client.get(f"/api/v1/videos/{self.video_id}/progress")
        self.assertEqual(progress.json()["feedback_type"], "DISLIKED")

    # -- get_video_recommendation (GET /recommendation) ------------------------

    def test_get_video_recommendation_returns_ok_with_video(self):
        response = self.client.get(
            "/api/v1/videos/recommendation",
            params={"content_node_id": str(self.content_node_id)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "OK")
        self.assertEqual(body["video_resource_id"], str(self.video_id))
        self.assertEqual(body["title"], "Diluicao - Conceito Basico")
        self.assertNotIn("video_object", body)

    def test_get_video_recommendation_no_candidates_returns_no_video_available(self):
        response = self.client.get(
            "/api/v1/videos/recommendation",
            params={"content_node_id": str(self.empty_content_node_id)},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "NO_VIDEO_AVAILABLE")
        self.assertIsNone(body["video"])

    def test_get_video_recommendation_missing_content_node_id_returns_422(self):
        response = self.client.get("/api/v1/videos/recommendation")
        self.assertEqual(response.status_code, 422)

    # -- get_video_progress (GET /{video_id}/progress) -------------------------

    def test_get_video_progress_unwatched_returns_200(self):
        response = self.client.get(f"/api/v1/videos/{self.video_id}/progress")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "UNWATCHED")
        self.assertEqual(body["progress_percentage"], 0.0)

    def test_get_video_progress_after_events_reflects_state(self):
        self.client.post(
            "/api/v1/videos/events",
            json={
                "resource_id": str(self.video_id),
                "event_type": "PROGRESS",
                "progress_percentage": 45.0,
            },
        )
        response = self.client.get(f"/api/v1/videos/{self.video_id}/progress")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["status"], "IN_PROGRESS")
        self.assertEqual(body["progress_percentage"], 45.0)


if __name__ == "__main__":
    unittest.main()
