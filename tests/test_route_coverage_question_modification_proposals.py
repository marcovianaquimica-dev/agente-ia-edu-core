"""HTTP-layer coverage for api/routes/question_modification_proposals.py.

Test coverage audit (2026-09): test_phase8c_question_modification.py already
gives this route file strong happy-path and provider-validation coverage
(measured, with coverage's thread+greenlet concurrency correctly configured
via pyproject.toml: 86%, not the 32% a naive `coverage report` run implied -
see this audit's summary for the concurrency finding). The lines still
missing are almost entirely defensive 404/403/409 branches for state that
changed out from under a request (a list item, exercise-list version, or
proposal that no longer matches what it did when the proposal/accept flow
started) and the 503 mapping for a provider that errors instead of returning
malformed JSON. This file targets exactly those branches, reusing
Phase8ATeacherListBuilderHTTP/StructuredProposalFake the same way
test_phase8c_question_modification.py does.
"""

from __future__ import annotations

import asyncio
import unittest
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import update

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.api.routes import question_modification_proposals as qmp_module
from agente_ia_edu.api.routes.question_modification_proposals import (
    get_modification_provider,
    router as modification_proposals_router,
)
from agente_ia_edu.api.routes.teacher_materials import router as teacher_materials_router
from agente_ia_edu.db.models import AssessmentItem, ModificationProposal
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.providers.errors import ProviderUnavailableError
from agente_ia_edu.providers.models import TextGenerationRequest, TextGenerationResult
from test_phase8a_teacher_list_builder_http import Phase8ATeacherListBuilderHTTP
from test_phase8c_question_modification import StructuredProposalFake


class FailingProvider:
    """Raises a real ProviderError subclass instead of returning JSON -
    the network/outage branch create_modification_proposal maps to 503,
    which StructuredProposalFake (always returns JSON) never exercises."""

    async def generate(self, request: TextGenerationRequest) -> TextGenerationResult:
        raise ProviderUnavailableError("provider temporarily unavailable")


class QuestionModificationProposalsRouteCoverageTests(Phase8ATeacherListBuilderHTTP):
    # Inherited contract tests belong to Phase 8A itself (already covered
    # there) - this subclass only reuses the fixture/setUp, the same way
    # Phase8CQuestionModificationContract does.
    test_complete_draft_builder_flow = None
    test_material_list_includes_items_without_a_content_link = None
    test_quantity_and_scope_protections = None
    test_candidates_are_paginated_filtered_and_persist_individual_selection = None
    test_catalog_children_do_not_leak_another_universe_branch = None

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

    def _two_list_items(self):
        # seed_candidates() seeds one hardcoded universe/slug - callable only
        # once per test, so a scenario needing two independent items pulls
        # both from a single seed instead of calling _list_item() twice.
        content_id, candidates, _ = self.seed_candidates()
        material_id = self.create_list(content_id, quantity=2, easy=2, medium=0, hard=0).json()["id"]
        item_a = self.client.post(
            f"/api/v1/teacher/materials/{material_id}/items",
            json={"question_version_id": str(candidates[0])},
        ).json()
        item_b = self.client.post(
            f"/api/v1/teacher/materials/{material_id}/items",
            json={"question_version_id": str(candidates[1])},
        ).json()
        return item_a, candidates[0], item_b, candidates[1]

    def _create_proposal(self, version_id, item_id, instruction=""):
        return self.client.post(
            f"/api/v1/teacher/questions/{version_id}/modification-proposals",
            json={"assessment_item_id": item_id, "modification_type": "MAKE_EASIER", "instruction": instruction},
        )

    def _set_item_field(self, item_id: str, **fields):
        # SQLAlchemy's Uuid column type needs an actual uuid.UUID for Core-
        # level bind params (unlike ORM attribute assignment, .where()/
        # .values() never auto-coerce a JSON-decoded str id or a bare uuid4()
        # passed positionally here) - coerce every value explicitly.
        item_uuid = UUID(str(item_id))
        coerced = {key: (UUID(str(value)) if value is not None else None) for key, value in fields.items()}

        async def run():
            async with self.session_factory() as session:
                await session.execute(update(AssessmentItem).where(AssessmentItem.id == item_uuid).values(**coerced))
                await session.commit()
        asyncio.run(run())

    # -- create: item/version mismatch and dangling references --------------
    def test_create_rejects_when_item_belongs_to_a_different_question(self):
        item_a, version_a, item_b, version_b = self._two_list_items()
        response = self._create_proposal(version_a, item_b["id"])
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Question is not the requested list item")

    def test_create_404s_when_the_list_version_no_longer_exists(self):
        _, item, version_id = self._list_item()
        self._set_item_field(item["id"], assessment_version_id=uuid4())
        response = self._create_proposal(version_id, item["id"])
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Exercise list version not found")

    def test_create_404s_when_the_question_version_no_longer_exists(self):
        _, item, version_id = self._list_item()
        vanished = uuid4()
        self._set_item_field(item["id"], question_version_id=vanished)
        response = self._create_proposal(vanished, item["id"])
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Question version not found")

    # -- create: no authorized pedagogical universe at all -------------------
    def test_create_403s_when_teacher_has_no_pedagogical_universe(self):
        # A TEACHER who owns the list (so _load_material's ownership check
        # passes) but holds no PedagogicalUniverseBinding at all -
        # resolve_active_universe's "No authorized pedagogical universe"
        # PermissionError, distinct from the "question outside universe"
        # ValueError-shaped rejection the create-time contract test already
        # covers.
        _, item, version_id = self._list_item()

        async def strip_universe_bindings():
            from agente_ia_edu.db.models import PedagogicalUniverseBinding
            async with self.session_factory() as session:
                await session.execute(
                    update(PedagogicalUniverseBinding)
                    .where(PedagogicalUniverseBinding.subject_external_id == "teacher-a")
                    .values(subject_external_id="teacher-a-no-longer-bound")
                )
                await session.commit()
        asyncio.run(strip_universe_bindings())

        response = self._create_proposal(version_id, item["id"])
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"], "No authorized pedagogical universe")

    # -- create: provider outage (503), not malformed JSON (422) -------------
    def test_create_maps_provider_error_to_503(self):
        _, item, version_id = self._list_item()
        self.app.dependency_overrides[get_modification_provider] = lambda: FailingProvider()
        response = self._create_proposal(version_id, item["id"])
        self.assertEqual(response.status_code, 503, response.text)
        self.assertEqual(response.json()["detail"], "Nao foi possivel gerar a modificacao. Tente novamente.")

    # -- cancel: not found / access denied ------------------------------------
    def test_cancel_not_found(self):
        response = self.client.post(f"/api/v1/teacher/questions/modification-proposals/{uuid4()}/cancel")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Modification proposal not found")

    def test_cancel_denied_for_a_different_requester(self):
        _, item, version_id = self._list_item()
        proposal = self._create_proposal(version_id, item["id"]).json()
        self.context["value"] = AuthenticatedUserContext(
            user_id="teacher-b", external_identity_id="teacher-b", role="TEACHER",
            school_id=self.school_a, scope_type="SCHOOL",
        )
        response = self.client.post(f"/api/v1/teacher/questions/modification-proposals/{proposal['id']}/cancel")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"], "Modification proposal access denied")

    # -- accept: not found / access denied ------------------------------------
    def test_accept_not_found(self):
        response = self.client.post(f"/api/v1/teacher/questions/modification-proposals/{uuid4()}/accept")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Modification proposal not found")

    def test_accept_denied_for_a_different_requester(self):
        _, item, version_id = self._list_item()
        proposal = self._create_proposal(version_id, item["id"]).json()
        self.context["value"] = AuthenticatedUserContext(
            user_id="teacher-b", external_identity_id="teacher-b", role="TEACHER",
            school_id=self.school_a, scope_type="SCHOOL",
        )
        response = self.client.post(f"/api/v1/teacher/questions/modification-proposals/{proposal['id']}/accept")
        self.assertEqual(response.status_code, 403, response.text)
        self.assertEqual(response.json()["detail"], "Modification proposal access denied")

    # -- accept: list state changed out from under the proposal ---------------
    def test_accept_409s_when_the_item_no_longer_points_at_the_original_version(self):
        content_id, candidates, _ = self.seed_candidates()
        material_id = self.create_list(content_id, quantity=1, easy=1, medium=0, hard=0).json()["id"]
        item = self.client.post(
            f"/api/v1/teacher/materials/{material_id}/items",
            json={"question_version_id": str(candidates[0])},
        ).json()
        proposal = self._create_proposal(candidates[0], item["id"]).json()
        # candidates[1] is a real, valid question version from the same seed
        # that was simply never added to this list - swapping the item onto
        # it (without touching the proposal) is exactly "the list moved on".
        self._set_item_field(item["id"], question_version_id=candidates[1])
        response = self.client.post(f"/api/v1/teacher/questions/modification-proposals/{proposal['id']}/accept")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertEqual(response.json()["detail"], "Proposal list context is no longer valid")

    def test_accept_404s_when_the_list_version_no_longer_exists(self):
        _, item, version_id = self._list_item()
        proposal = self._create_proposal(version_id, item["id"]).json()
        self._set_item_field(item["id"], assessment_version_id=uuid4())
        response = self.client.post(f"/api/v1/teacher/questions/modification-proposals/{proposal['id']}/accept")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Exercise list version not found")

    def test_accept_404s_when_the_original_question_version_vanished(self):
        _, item, version_id = self._list_item()
        proposal = self._create_proposal(version_id, item["id"]).json()
        vanished = uuid4()

        async def corrupt():
            async with self.session_factory() as session:
                # Both sides move to the SAME vanished id, so the
                # item-still-matches-the-proposal check (409 branch) stays
                # satisfied and execution reaches the original-version lookup.
                await session.execute(
                    update(ModificationProposal)
                    .where(ModificationProposal.id == UUID(proposal["id"]))
                    .values(original_question_version_id=vanished)
                )
                await session.execute(
                    update(AssessmentItem).where(AssessmentItem.id == UUID(item["id"]))
                    .values(question_version_id=vanished)
                )
                await session.commit()
        asyncio.run(corrupt())

        response = self.client.post(f"/api/v1/teacher/questions/modification-proposals/{proposal['id']}/accept")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Original question version not found")


class ModificationProviderFactoryTests(unittest.TestCase):
    """Covers get_modification_provider's production wiring passthrough
    (never hit by any test that overrides the dependency). Needs no DB
    fixture at all, so this stays a plain TestCase rather than inheriting
    Phase8ATeacherListBuilderHTTP (which would otherwise also re-run that
    base class's own contract tests a second time here)."""

    def test_passthrough_calls_build_text_provider(self):
        sentinel = object()
        original = qmp_module.build_text_provider
        qmp_module.build_text_provider = lambda: sentinel
        try:
            self.assertIs(get_modification_provider(), sentinel)
        finally:
            qmp_module.build_text_provider = original


if __name__ == "__main__":
    unittest.main()
