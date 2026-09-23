"""
HTTP-level route-handler coverage for
src/agente_ia_edu/api/routes/question_modification_proposals.py.

Overnight bug-hunt campaign, "smaller route files" zone. Targets the 2
explicit `await session.commit()` sites plus the implicit commit from
`async with session.begin():` in this file:
  1. create_modification_proposal (POST /{question_version_id}/modification-proposals)
  2. cancel_modification_proposal (POST /modification-proposals/{id}/cancel)
  3. accept_modification_proposal (POST /modification-proposals/{id}/accept)
     - commits implicitly when `async with session.begin():` exits normally,
       including via the early `return _response(proposal)` for an
       already-ACCEPTED proposal (Python evaluates the return expression
       BEFORE unwinding the `async with`, so that read happens pre-commit -
       verified by static reading, and empirically covered by the "repeat
       accept" case below).

Static review showed all 3 sites already refresh (or capture into a
Pydantic response before the implicit commit happens) the one object read
afterward - `proposal`. This file is the required empirical proof: it
drives create -> accept -> repeat-accept -> cancel -> repeat-cancel through
the real endpoints, using a session factory built EXACTLY like production
(async_sessionmaker(engine, class_=AsyncSession), default expire_on_commit=
True), unlike tests/test_phase8c_question_modification.py and
tests/test_route_coverage_question_modification_proposals.py, whose shared
Phase8ATeacherListBuilderHTTP fixture sets expire_on_commit=False and would
paper over a MissingGreenlet bug here.

Reuses TeacherMaterialsProdFidelityHTTP from
tests/test_teacher_materials_route_coverage_http.py (production-fidelity
sibling of Phase8ATeacherListBuilderHTTP) for the school/universe/candidate
seeding, the same way test_route_coverage_question_modification_proposals.py
reuses Phase8ATeacherListBuilderHTTP.
"""

from __future__ import annotations

import json
import unittest
from dataclasses import dataclass

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.api.routes.question_modification_proposals import (
    get_modification_provider,
    router as modification_proposals_router,
)
from agente_ia_edu.api.routes.teacher_materials import router as teacher_materials_router
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult
from test_teacher_materials_route_coverage_http import TeacherMaterialsProdFidelityHTTP


@dataclass
class StructuredProposalFake:
    """Same shape as tests/test_phase8c_question_modification.py's fake -
    duplicated locally to avoid cross-importing a sibling test module's
    fixture-adjacent helper for an unrelated bug-hunt file."""

    mode: str = "valid"
    provider: str = "prodfidelity-fake"
    model: str = "prodfidelity-test-v1"
    last_prompt: str = ""

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        self.last_prompt = request.prompt
        payload = {
            "statement": "Enunciado simplificado com os dados necessarios.",
            "options": ["Resposta correta", "Distrator B", "Distrator C", "Distrator D"],
            "correct_option": "Resposta correta",
            "difficulty": "EASY",
            "modification_type": "MAKE_EASIER",
        }
        return TextGenerationResult(text=json.dumps(payload), provider=self.provider, model=self.model)


class QuestionModificationProposalsRouteCoverageHTTP(TeacherMaterialsProdFidelityHTTP):
    def setUp(self):
        super().setUp()
        self.provider = StructuredProposalFake()
        app = FastAPI()
        app.include_router(teacher_materials_router)
        app.include_router(modification_proposals_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_authenticated_context] = lambda: self.context["value"]
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        app.dependency_overrides[get_modification_provider] = lambda: self.provider
        self.client.close()
        self.app = app
        self.client = TestClient(app)

    def _list_item(self):
        content_id, candidates, _ = self.seed_candidates()
        material_id = self.create_list(content_id, quantity=1, easy=1, medium=0, hard=0).json()["id"]
        item = self.client.post(
            f"/api/v1/teacher/materials/{material_id}/items",
            json={"question_version_id": str(candidates[0])},
        ).json()
        return material_id, item, candidates[0]

    def _create_proposal(self, version_id, item_id, instruction=""):
        return self.client.post(
            f"/api/v1/teacher/questions/{version_id}/modification-proposals",
            json={"assessment_item_id": item_id, "modification_type": "MAKE_EASIER", "instruction": instruction},
        )

    # -- commit site 1: create_modification_proposal --------------------------

    def test_create_proposal_returns_clean_201_not_500(self):
        _, item, version_id = self._list_item()
        response = self._create_proposal(version_id, item["id"], "Deixe mais facil")
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["status"], "PENDING")
        self.assertEqual(body["original_question_version_id"], str(version_id))
        self.assertEqual(body["provider"], "prodfidelity-fake")
        self.assertIn("statement", body["proposal"])

    # -- commit site 3: accept_modification_proposal (async with session.begin()) --

    def test_accept_then_repeat_accept_returns_clean_200_not_500(self):
        """Repeat accept exercises the early-return-inside-session.begin()
        branch (proposal.status == 'ACCEPTED') - the subtlest of the 3
        sites, since the implicit commit fires on the way out of the
        `async with` block even for that early return."""
        _, item, version_id = self._list_item()
        proposal = self._create_proposal(version_id, item["id"]).json()

        accepted = self.client.post(
            f"/api/v1/teacher/questions/modification-proposals/{proposal['id']}/accept"
        )
        self.assertEqual(accepted.status_code, 200, accepted.text)
        result = accepted.json()
        self.assertEqual(result["status"], "ACCEPTED")
        self.assertNotEqual(result["question_version_id"], str(version_id))

        repeated = self.client.post(
            f"/api/v1/teacher/questions/modification-proposals/{proposal['id']}/accept"
        )
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json()["question_version_id"], result["question_version_id"])

    # -- commit site 2: cancel_modification_proposal ---------------------------

    def test_cancel_then_repeat_cancel_returns_clean_200_not_500(self):
        _, item, version_id = self._list_item()
        proposal = self._create_proposal(version_id, item["id"]).json()

        cancelled = self.client.post(
            f"/api/v1/teacher/questions/modification-proposals/{proposal['id']}/cancel"
        )
        self.assertEqual(cancelled.status_code, 200, cancelled.text)
        self.assertEqual(cancelled.json()["status"], "CANCELLED")

        # Repeat cancel: proposal.status != "PENDING" now, so the route's
        # `if proposal.status == "PENDING":` guard skips the commit entirely
        # and reads the already-loaded (never-expired-this-session) proposal
        # straight through - a different, cheaper-looking but still worth
        # confirming, branch of the same commit site.
        repeated = self.client.post(
            f"/api/v1/teacher/questions/modification-proposals/{proposal['id']}/cancel"
        )
        self.assertEqual(repeated.status_code, 200, repeated.text)
        self.assertEqual(repeated.json()["status"], "CANCELLED")


if __name__ == "__main__":
    unittest.main()
