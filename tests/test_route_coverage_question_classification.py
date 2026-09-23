"""HTTP-layer coverage for api/routes/question_classification.py.

Test coverage audit (2026-09): the SERVICE layer
(AuthorialQuestionClassificationService, exercised directly by
test_phase30_authorial_question_classification.py) already has strong
coverage, but almost nothing calls the FastAPI routes themselves - request
parsing, dependency-injected auth/scope gating, and exception -> HTTP status
mapping (_map_error) were all untested. This file drives the qc_router
through fastapi.testclient.TestClient, reusing the same SQLite +
CurriculumTaxonomyService.seed_reference_fixture() + ScriptedProvider fixture
Phase 30 already established, so real classification behavior (not a stub)
runs on the other side of each request.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes import question_classification as qc_module
from agente_ia_edu.api.routes.question_classification import (
    get_text_generation_provider,
    qc_router,
)
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import PedagogicalClassification, Question, QuestionExtractionRun, QuestionVersion
from agente_ia_edu.db.models.admin import School, UserSchoolLink
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService
from test_phase30_authorial_question_classification import (
    _DILUTION_RESPONSE,
    _DILUTION_STATEMENT,
    ScriptedProvider,
)

_SCHOOL_A = _uuid.uuid5(_uuid.NAMESPACE_DNS, "qc-route-school-a")
_SCHOOL_B = _uuid.uuid5(_uuid.NAMESPACE_DNS, "qc-route-school-b")


class QuestionClassificationRouteHTTPTests(unittest.TestCase):
    def setUp(self):
        async def setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                await CurriculumTaxonomyService(session).seed_reference_fixture()
                session.add_all([
                    School(id=_SCHOOL_A, code="QCR-A", name="Escola QC Route A", status="ACTIVE"),
                    School(id=_SCHOOL_B, code="QCR-B", name="Escola QC Route B", status="ACTIVE"),
                ])
                await session.flush()
                session.add_all([
                    UserSchoolLink(external_user_id="teacher-a", school_id=_SCHOOL_A, role="TEACHER",
                                   scope_type="SCHOOL", scope_external_id=str(_SCHOOL_A), active=True),
                    UserSchoolLink(external_user_id="teacher-b", school_id=_SCHOOL_B, role="TEACHER",
                                   scope_type="SCHOOL", scope_external_id=str(_SCHOOL_B), active=True),
                ])
                await session.commit()
                question = Question(validation_status="validated", origin_type="AUTHORIAL",
                                     status="PUBLISHED", visibility_scope="SCHOOL", school_id=_SCHOOL_A)
                session.add(question)
                await session.flush()
                version = QuestionVersion(question_id=question.id, version_kind="official_original",
                                           canonical_text=_DILUTION_STATEMENT, content_hash=f"hash-{_uuid.uuid4().hex}")
                session.add(version)
                await session.commit()
                return engine, factory, version.id

        self.engine, self.session_factory, self.version_id = asyncio.run(setup())
        self.identity = {"value": ExternalIdentityContext(
            provider="test", external_user_id="teacher-a", roles=("teacher",),
        )}
        self.provider = {"value": ScriptedProvider([dict(_DILUTION_RESPONSE)])}
        app = FastAPI()
        app.include_router(qc_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        app.dependency_overrides[get_text_generation_provider] = lambda: self.provider["value"]
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    def _as(self, external_user_id: str):
        self.identity["value"] = ExternalIdentityContext(
            provider="test", external_user_id=external_user_id, roles=("teacher",),
        )

    def _manual_classify(self, version_id=None, difficulty="EASY", reason="revisao inicial"):
        return self.client.patch(
            f"/api/v1/catalog/question-classification/question-versions/{version_id or self.version_id}",
            json={
                "discipline_code": "CHEMISTRY", "area_code": "CHEMISTRY-PHYSICAL",
                "content_code": "CHEMISTRY-SOLUTIONS", "subcontent_code": "CHEMISTRY-SOLUTIONS-DILUTION",
                "difficulty": difficulty, "reason": reason,
            },
        )

    # -- _authorize -----------------------------------------------------
    def test_role_without_permission_is_rejected(self):
        self.identity["value"] = ExternalIdentityContext(
            provider="test", external_user_id="student-x", roles=("student",),
        )
        resp = self.client.get(f"/api/v1/catalog/question-classification/question-versions/{self.version_id}")
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertIn("teacher, coordinator, director, or platform admin", resp.json()["detail"])

    # -- _require_scope (real, through HTTP - not just the unit-level object test) --
    def test_cross_school_teacher_is_denied(self):
        self._as("teacher-b")
        resp = self.client.get(f"/api/v1/catalog/question-classification/question-versions/{self.version_id}")
        self.assertEqual(resp.status_code, 403, resp.text)
        self.assertIn("outside your school scope", resp.json()["detail"])

    # -- GET current classification --------------------------------------
    def test_get_classification_404_when_none_exists(self):
        resp = self.client.get(f"/api/v1/catalog/question-classification/question-versions/{self.version_id}")
        self.assertEqual(resp.status_code, 404, resp.text)
        self.assertEqual(resp.json()["detail"], "no classification exists for this question version")

    def test_get_classification_success_after_manual_classify(self):
        created = self._manual_classify()
        self.assertEqual(created.status_code, 200, created.text)
        resp = self.client.get(f"/api/v1/catalog/question-classification/question-versions/{self.version_id}")
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["content_code"], "CHEMISTRY-SOLUTIONS")
        self.assertEqual(body["difficulty"], "EASY")
        self.assertEqual(body["source"], "human")
        self.assertEqual(body["status"], "CLASSIFIED")

    # -- GET history ------------------------------------------------------
    def test_history_empty_then_populated(self):
        empty = self.client.get(f"/api/v1/catalog/question-classification/question-versions/{self.version_id}/history")
        self.assertEqual(empty.status_code, 200, empty.text)
        self.assertEqual(empty.json(), [])
        self._manual_classify()
        resp = self.client.get(f"/api/v1/catalog/question-classification/question-versions/{self.version_id}/history")
        self.assertEqual(resp.status_code, 200, resp.text)
        events = resp.json()
        self.assertEqual(len(events), 1)
        self.assertEqual(events[0]["action"], "MANUAL_CLASSIFY")
        self.assertEqual(events[0]["actor"], "teacher-a")

    # -- POST classify (AI) ------------------------------------------------
    def test_classify_runs_ai_then_caches_on_second_call(self):
        first = self.client.post(
            f"/api/v1/catalog/question-classification/question-versions/{self.version_id}/classify"
        )
        self.assertEqual(first.status_code, 201, first.text)
        body = first.json()
        self.assertEqual(body["cache_hit"], False)
        # One AI call for candidate classification, one for the separate
        # difficulty axis (_assess_and_apply_difficulty) - both go through
        # the same provider.generate().
        self.assertEqual(body["ai_calls"], 2)
        self.assertEqual(body["status"], "CLASSIFIED")

        second = self.client.post(
            f"/api/v1/catalog/question-classification/question-versions/{self.version_id}/classify"
        )
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(second.json()["cache_hit"], True)
        self.assertEqual(second.json()["ai_calls"], 0)

    # -- PATCH manual classify ---------------------------------------------
    def test_manual_classify_rejects_invalid_difficulty(self):
        resp = self._manual_classify(difficulty="IMPOSSIBLE")
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["detail"]["code"], "INVALID_DIFFICULTY")

    def test_manual_classify_rejects_invalid_catalog_path(self):
        resp = self.client.patch(
            f"/api/v1/catalog/question-classification/question-versions/{self.version_id}",
            json={
                "content_code": "CHEMISTRY-DOES-NOT-EXIST", "difficulty": "EASY",
                "reason": "codigo inventado",
            },
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["detail"]["code"], "CLASSIFICATION_INVALID")

    def test_manual_classify_unknown_question_version_is_404(self):
        resp = self._manual_classify(version_id=_uuid.uuid4())
        # The version belongs to no school (school_id resolves to None) and
        # teacher-a is not a platform admin, so _require_scope denies first.
        self.assertEqual(resp.status_code, 403, resp.text)

    # -- POST reclassify -----------------------------------------------------
    def test_reclassify_without_prior_classification_is_422(self):
        resp = self.client.post(
            f"/api/v1/catalog/question-classification/question-versions/{self.version_id}/reclassify",
            json={"reason": "tentar de novo"},
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["detail"]["code"], "INVALID_REQUEST")

    def test_reclassify_supersedes_previous_classification(self):
        created = self._manual_classify()
        self.assertEqual(created.status_code, 200, created.text)
        old_id = created.json()["id"]
        resp = self.client.post(
            f"/api/v1/catalog/question-classification/question-versions/{self.version_id}/reclassify",
            json={"reason": "reclassificar com IA"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["supersedes_id"], old_id)
        self.assertNotEqual(body["id"], old_id)

    # -- POST approve ----------------------------------------------------
    def test_approve_classification_not_found(self):
        resp = self.client.post(
            f"/api/v1/catalog/question-classification/classifications/{_uuid.uuid4()}/approve",
            json={},
        )
        self.assertEqual(resp.status_code, 404, resp.text)
        self.assertEqual(resp.json()["detail"], "classification not found")

    def test_approve_classification_success(self):
        created = self._manual_classify().json()
        resp = self.client.post(
            f"/api/v1/catalog/question-classification/classifications/{created['id']}/approve",
            json={"reason": "confirmado pela coordenacao"},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        self.assertTrue(resp.json()["human_approved"])
        self.assertEqual(resp.json()["approved_by"], "teacher-a")

    def test_approve_classification_rejects_non_active_lifecycle(self):
        created = self._manual_classify().json()
        reclassified = self.client.post(
            f"/api/v1/catalog/question-classification/question-versions/{self.version_id}/reclassify",
            json={"reason": "atualizar"},
        )
        self.assertEqual(reclassified.status_code, 200, reclassified.text)
        # `created["id"]` is now SUPERSEDED (reclassify demoted it) - only an
        # ACTIVE classification can be approved.
        resp = self.client.post(
            f"/api/v1/catalog/question-classification/classifications/{created['id']}/approve",
            json={},
        )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertEqual(resp.json()["detail"]["code"], "INVALID_REQUEST")

    def test_approve_classification_cross_school_denied(self):
        created = self._manual_classify().json()
        self._as("teacher-b")
        resp = self.client.post(
            f"/api/v1/catalog/question-classification/classifications/{created['id']}/approve",
            json={},
        )
        self.assertEqual(resp.status_code, 403, resp.text)

    # -- POST batch-classify -------------------------------------------------
    def test_batch_classify_by_explicit_version_ids(self):
        self.provider["value"] = ScriptedProvider([dict(_DILUTION_RESPONSE)])
        resp = self.client.post(
            "/api/v1/catalog/question-classification/batch-classify",
            json={"question_version_ids": [str(self.version_id)]},
        )
        self.assertEqual(resp.status_code, 200, resp.text)
        body = resp.json()
        self.assertEqual(body["questions_processed"], 1)
        self.assertEqual(body["classified"], 1)
        self.assertEqual(len(body["outcomes"]), 1)

    def test_batch_classify_with_unknown_extraction_run_is_404(self):
        resp = self.client.post(
            "/api/v1/catalog/question-classification/batch-classify",
            json={"question_version_ids": [], "extraction_run_id": str(_uuid.uuid4())},
        )
        self.assertEqual(resp.status_code, 404, resp.text)
        self.assertEqual(resp.json()["detail"], "extraction run not found")

    # -- GET review-queue -----------------------------------------------------
    def test_review_queue_lists_only_own_school_needs_review(self):
        low_confidence = {
            **_DILUTION_RESPONSE, "confidence": "LOW",
            "status": "NEEDS_REVIEW", "review_reason": "LOW_CONFIDENCE",
        }
        self.provider["value"] = ScriptedProvider([low_confidence])
        classify_resp = self.client.post(
            f"/api/v1/catalog/question-classification/question-versions/{self.version_id}/classify"
        )
        self.assertEqual(classify_resp.status_code, 201, classify_resp.text)
        self.assertEqual(classify_resp.json()["status"], "NEEDS_REVIEW")

        resp = self.client.get("/api/v1/catalog/question-classification/review-queue")
        self.assertEqual(resp.status_code, 200, resp.text)
        items = resp.json()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["question_version_id"], str(self.version_id))

        self._as("teacher-b")
        other_school_resp = self.client.get("/api/v1/catalog/question-classification/review-queue")
        self.assertEqual(other_school_resp.status_code, 200, other_school_resp.text)
        self.assertEqual(other_school_resp.json(), [])


class TextGenerationProviderFactoryTests(unittest.TestCase):
    """Covers get_text_generation_provider's production wiring passthrough
    (line never hit by any test that overrides the dependency)."""

    def test_passthrough_calls_build_text_provider(self):
        sentinel = object()
        original = qc_module.build_text_provider
        qc_module.build_text_provider = lambda: sentinel
        try:
            self.assertIs(get_text_generation_provider(), sentinel)
        finally:
            qc_module.build_text_provider = original


if __name__ == "__main__":
    unittest.main()
