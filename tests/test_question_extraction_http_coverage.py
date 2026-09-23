"""HTTP-layer coverage audit for question_extraction.py (2026-09).

Every existing question-extraction HTTP test either only exercises
run_extraction/resolution-review, or calls the SERVICE directly and skips
the route entirely (request parsing, exception->status mapping, tenant
scope checks at the HTTP boundary). This file drives the remaining
endpoints - list_runs, get_run, get_question, question_page_image,
review_queue, start_review, update_question, approve_question,
reject_question, candidate_assets/associate_asset/ignore_asset,
publish_summary and publish_run - through a REAL fastapi.testclient
TestClient request, asserting on actual response status codes and bodies,
never an empty/placeholder assertion.

Same in-memory-SQLite + synthetic-PDF pattern as
test_phase27_question_extraction_service.py (this domain's established
HTTP test fixture).
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import ExtractedQuestionAsset, IngestionDocument
from agente_ia_edu.db.models.admin import School, UserSchoolLink
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.question_extraction_service import QuestionExtractionService

_SCHOOL_A = _uuid.uuid5(_uuid.NAMESPACE_DNS, "qehttp-school-a")
_SCHOOL_B = _uuid.uuid5(_uuid.NAMESPACE_DNS, "qehttp-school-b")


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


def _make_pdf(path: Path, n_questions: int, *, tag: str = "") -> None:
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    y = 50
    for n in range(1, n_questions + 1):
        page.insert_text(
            (72, y), f"{n}.   Questao numero {n}{tag} com enunciado suficientemente longo.", fontsize=9)
        y += 15
        for letter in "abcde":
            page.insert_text((90, y), f"{letter}) alternativa {letter} da questao {n}", fontsize=9)
            y += 12
        y += 20
        if y > 780:
            page = doc.new_page()
            y = 50
    doc.save(str(path))


async def _seed_catalog(factory) -> None:
    async with factory() as s:
        s.add(School(id=_SCHOOL_A, code="QEHTTPA", name="Escola QE HTTP A", status="ACTIVE"))
        s.add(School(id=_SCHOOL_B, code="QEHTTPB", name="Escola QE HTTP B", status="ACTIVE"))
        await s.flush()
        s.add(UserSchoolLink(external_user_id="qe_prof_a", school_id=_SCHOOL_A, role="TEACHER",
                             scope_type="SCHOOL", scope_external_id=str(_SCHOOL_A), active=True))
        s.add(UserSchoolLink(external_user_id="qe_prof_b", school_id=_SCHOOL_B, role="TEACHER",
                             scope_type="SCHOOL", scope_external_id=str(_SCHOOL_B), active=True))
        s.add(UserSchoolLink(external_user_id="qe_student_a", school_id=_SCHOOL_A, role="STUDENT",
                             scope_type="CLASSROOM", scope_external_id="turma-qe-a", active=True))
        await s.commit()


class QuestionExtractionHTTPCoverageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await _seed_catalog(cls.factory)

        cls.loop.run_until_complete(_prep())

        cls.tmp = Path("/tmp/qe_http_coverage_fixtures")
        cls.tmp.mkdir(exist_ok=True)

        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls.app.dependency_overrides[get_current_identity] = lambda: _ident("qe_prof_a")
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _seed_document(self, path: Path, tag: str) -> _uuid.UUID:
        async def run():
            async with self.factory() as s:
                doc = IngestionDocument(
                    filename=path.name, document_type="PDF", document_hash=f"hash-{tag}",
                    storage_uri=str(path), file_size_bytes=path.stat().st_size,
                    status="processed", ingested_by_external_identity="qe_prof_a",
                )
                s.add(doc)
                await s.flush()
                await s.commit()
                return doc.id
        return self.loop.run_until_complete(run())

    def _seed_run(self, *, n_questions: int = 2, school=_SCHOOL_A, started_by="qe_prof_a", tag=""):
        """Returns (run_id, [question_id, ...]) for a freshly-extracted run."""
        path = self.tmp / f"doc_{_uuid.uuid4().hex[:8]}.pdf"
        _make_pdf(path, n_questions, tag=tag)
        doc_id = self._seed_document(path, tag or "doc")

        async def run():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                r, _ = await svc.run_extraction(doc_id, path, started_by=started_by, school_id=school)
                qs = await svc.list_questions(r.id)
                return r.id, [q.id for q in qs]
        return self.loop.run_until_complete(run())

    def _validate_question(self, question_id: _uuid.UUID, *, user="qe_prof_a") -> None:
        """Push a REVIEW_REQUIRED/IN_REVIEW question to VALIDATED via a real edit,
        same as the approve-flow in test_phase27_question_extraction_service.py."""
        async def run():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                q = await svc.get_question(question_id)
                await svc.update_question(
                    q.id, reviewed_text=(q.reconstructed_text or q.normalized_text), reviewed_by=user)
        self.loop.run_until_complete(run())

    # -- run_extraction: ingestion document not found (line 193) ------------
    def test_run_extraction_document_not_found_404(self):
        self._as("qe_prof_a")
        r = self.client.post(
            f"/api/v1/catalog/question-extraction/{_uuid.uuid4()}/run", json={})
        self.assertEqual(r.status_code, 404, r.text)

    # -- list_runs (lines 219-228) ----------------------------------------
    def test_list_runs_scopes_to_callers_school(self):
        run_a_id, _ = self._seed_run(school=_SCHOOL_A, started_by="qe_prof_a", tag=" listA")
        run_b_id, _ = self._seed_run(school=_SCHOOL_B, started_by="qe_prof_b", tag=" listB")

        self._as("qe_prof_a")
        r = self.client.get("/api/v1/catalog/question-extraction/runs")
        self.assertEqual(r.status_code, 200, r.text)
        ids = {row["id"] for row in r.json()}
        self.assertIn(str(run_a_id), ids)
        self.assertNotIn(str(run_b_id), ids)

        self._as("qe_prof_b")
        r2 = self.client.get("/api/v1/catalog/question-extraction/runs")
        ids2 = {row["id"] for row in r2.json()}
        self.assertIn(str(run_b_id), ids2)
        self.assertNotIn(str(run_a_id), ids2)
        self._as("qe_prof_a")

    # -- get_run success + review_status filter (lines 231-248) -----------
    def test_get_run_success_returns_questions_with_options_and_assets(self):
        run_id, question_ids = self._seed_run(n_questions=2, tag=" getrun")
        self._as("qe_prof_a")
        r = self.client.get(f"/api/v1/catalog/question-extraction/runs/{run_id}")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["run"]["id"], str(run_id))
        self.assertEqual(len(body["questions"]), 2)
        first = body["questions"][0]
        # with_options=True / with_assets=True branches (lines 163-170)
        self.assertIn("options", first)
        self.assertIn("assets", first)
        self.assertTrue(len(first["options"]) >= 2)  # a)...e) alternatives were extracted

    def test_get_run_filters_by_review_status(self):
        # This synthetic fixture's questions extract cleanly (no flags), so
        # the engine stamps both VALIDATED on creation - reject one via the
        # real reject_question workflow to get two DIFFERENT statuses to
        # filter between.
        run_id, question_ids = self._seed_run(n_questions=2, tag=" filterstatus")

        async def _reject():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                await svc.reject_question(
                    question_ids[1], reviewed_by="qe_prof_a", reason="OTHER")
        self.loop.run_until_complete(_reject())

        self._as("qe_prof_a")
        r = self.client.get(
            f"/api/v1/catalog/question-extraction/runs/{run_id}",
            params={"review_status": "VALIDATED"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(len(body["questions"]), 1)
        self.assertEqual(body["questions"][0]["id"], str(question_ids[0]))

    def test_get_run_not_found_404(self):
        self._as("qe_prof_a")
        r = self.client.get(f"/api/v1/catalog/question-extraction/runs/{_uuid.uuid4()}")
        self.assertEqual(r.status_code, 404, r.text)

    # -- get_question (lines 251-265) --------------------------------------
    def test_get_question_success(self):
        _run_id, question_ids = self._seed_run(n_questions=1, tag=" getq")
        self._as("qe_prof_a")
        r = self.client.get(f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["id"], str(question_ids[0]))
        self.assertIn("options", r.json())

    def test_get_question_not_found_404(self):
        self._as("qe_prof_a")
        r = self.client.get(f"/api/v1/catalog/question-extraction/questions/{_uuid.uuid4()}")
        self.assertEqual(r.status_code, 404, r.text)

    def test_get_question_cross_tenant_403(self):
        _run_id, question_ids = self._seed_run(n_questions=1, school=_SCHOOL_A, tag=" crossq")
        self._as("qe_prof_b")
        r = self.client.get(f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}")
        self.assertEqual(r.status_code, 403, r.text)
        self._as("qe_prof_a")

    # -- question_page_image (lines 268-299) --------------------------------
    def test_question_page_image_success(self):
        _run_id, question_ids = self._seed_run(n_questions=1, tag=" pageimg")
        self._as("qe_prof_a")
        r = self.client.get(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}/page-image")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.headers["content-type"], "image/png")
        self.assertGreater(len(r.content), 100)
        self.assertEqual(r.content[:8], b"\x89PNG\r\n\x1a\n")

    def test_question_page_image_out_of_range_422(self):
        _run_id, question_ids = self._seed_run(n_questions=1, tag=" pagerange")
        self._as("qe_prof_a")
        r = self.client.get(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}/page-image",
            params={"page": 999})
        self.assertEqual(r.status_code, 422, r.text)

    def test_question_page_image_cross_tenant_403(self):
        _run_id, question_ids = self._seed_run(n_questions=1, school=_SCHOOL_A, tag=" pageimgx")
        self._as("qe_prof_b")
        r = self.client.get(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}/page-image")
        self.assertEqual(r.status_code, 403, r.text)
        self._as("qe_prof_a")

    # -- review_queue (lines 302-327) ---------------------------------------
    def test_review_queue_returns_items_and_progress(self):
        run_id, _qids = self._seed_run(n_questions=3, tag=" queue")
        self._as("qe_prof_a")
        r = self.client.get(
            "/api/v1/catalog/question-extraction/review-queue", params={"run_id": str(run_id)})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIn("items", body)
        self.assertIn("progress", body)
        self.assertEqual(body["progress"]["total"], 3)
        self.assertEqual(len(body["items"]), 3)

    def test_review_queue_cross_tenant_run_id_403(self):
        run_id, _qids = self._seed_run(n_questions=1, school=_SCHOOL_A, tag=" queuex")
        self._as("qe_prof_b")
        r = self.client.get(
            "/api/v1/catalog/question-extraction/review-queue", params={"run_id": str(run_id)})
        self.assertEqual(r.status_code, 403, r.text)
        self._as("qe_prof_a")

    # -- start_review (lines 330-345) ---------------------------------------
    def test_start_review_transitions_status(self):
        # This synthetic fixture's question extracts cleanly and is stamped
        # VALIDATED on creation - force it back to REVIEW_REQUIRED directly
        # (simulating a low-confidence/flagged real extraction) so the HTTP
        # call actually exercises the REVIEW_REQUIRED -> IN_REVIEW transition
        # instead of the VALIDATED no-op branch.
        _run_id, question_ids = self._seed_run(n_questions=1, tag=" startrev")

        async def _force_review_required():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                q = await svc.get_question(question_ids[0])
                q.review_status = "REVIEW_REQUIRED"
                await s.commit()
        self.loop.run_until_complete(_force_review_required())

        self._as("qe_prof_a")
        r = self.client.post(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}/start-review")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["review_status"], "IN_REVIEW")

    def test_start_review_cross_tenant_403(self):
        _run_id, question_ids = self._seed_run(n_questions=1, school=_SCHOOL_A, tag=" startrevx")
        self._as("qe_prof_b")
        r = self.client.post(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}/start-review")
        self.assertEqual(r.status_code, 403, r.text)
        self._as("qe_prof_a")

    # -- update_question (lines 348-369) ------------------------------------
    def test_update_question_edits_text_options_type_notes(self):
        _run_id, question_ids = self._seed_run(n_questions=1, tag=" upd")
        self._as("qe_prof_a")
        r = self.client.patch(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}",
            json={
                "reviewed_text": "Enunciado revisado via HTTP.",
                "options": [{"label": "A", "text": "opcao a"}, {"label": "B", "text": "opcao b"}],
                "question_type": "multiple_choice",
                "notes": "nota de revisao",
            },
        )
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(body["reviewed_text"], "Enunciado revisado via HTTP.")
        self.assertEqual(body["notes"], "nota de revisao")
        self.assertEqual(len(body["options"]), 2)
        self.assertEqual(body["review_status"], "VALIDATED")

    def test_update_question_cross_tenant_403(self):
        _run_id, question_ids = self._seed_run(n_questions=1, school=_SCHOOL_A, tag=" updx")
        self._as("qe_prof_b")
        r = self.client.patch(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}",
            json={"reviewed_text": "tentativa de outra escola"},
        )
        self.assertEqual(r.status_code, 403, r.text)
        self._as("qe_prof_a")

    # -- approve_question via HTTP (lines 372-387) ---------------------------
    def test_approve_question_via_http_success(self):
        _run_id, question_ids = self._seed_run(n_questions=1, tag=" appr")
        self._as("qe_prof_a")
        self.client.patch(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}",
            json={"reviewed_text": "pronto para aprovacao"},
        )
        r = self.client.post(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}/approve")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["review_status"], "APPROVED")

    def test_approve_question_cross_tenant_403(self):
        _run_id, question_ids = self._seed_run(n_questions=1, school=_SCHOOL_A, tag=" apprx")
        self._as("qe_prof_b")
        r = self.client.post(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}/approve")
        self.assertEqual(r.status_code, 403, r.text)
        self._as("qe_prof_a")

    # -- reject_question (lines 390-413) -------------------------------------
    def test_reject_question_invalid_reason_422(self):
        _run_id, question_ids = self._seed_run(n_questions=1, tag=" rejbad")
        self._as("qe_prof_a")
        r = self.client.post(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}/reject",
            json={"reason": "NOT_A_REAL_REASON"},
        )
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(r.json()["detail"]["code"], "INVALID_REJECTION_REASON")

    def test_reject_question_valid_reason_success(self):
        _run_id, question_ids = self._seed_run(n_questions=1, tag=" rejok")
        self._as("qe_prof_a")
        r = self.client.post(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}/reject",
            json={"reason": "NOT_A_QUESTION", "notes": "nao e questao"},
        )
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["review_status"], "REJECTED")
        self.assertEqual(r.json()["rejection_reason"], "NOT_A_QUESTION")

    def test_reject_question_cross_tenant_403(self):
        _run_id, question_ids = self._seed_run(n_questions=1, school=_SCHOOL_A, tag=" rejx")
        self._as("qe_prof_b")
        r = self.client.post(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}/reject",
            json={"reason": "OTHER"},
        )
        self.assertEqual(r.status_code, 403, r.text)
        self._as("qe_prof_a")

    # -- resolution/reject not-found (lines 457-475) -------------------------
    def test_reject_resolution_not_found_404(self):
        self._as("qe_prof_a")
        r = self.client.post(
            f"/api/v1/catalog/question-extraction/questions/{_uuid.uuid4()}/resolution/reject",
            json={"reason": "sem gabarito"},
        )
        self.assertEqual(r.status_code, 404, r.text)

    # -- candidate_assets / associate_asset / ignore_asset (lines 478-534) ---
    def _seed_asset(self, run_id, question, *, status="UNASSOCIATED", page=None) -> _uuid.UUID:
        async def run():
            async with self.factory() as s:
                asset = ExtractedQuestionAsset(
                    run_id=run_id, question_id=(question.id if status != "UNASSOCIATED" else None),
                    asset_type="IMAGE", source_page=page or question.source_page_start,
                    digest=f"digest-{_uuid.uuid4().hex[:8]}", extraction_confidence=0.9, status=status,
                )
                s.add(asset)
                await s.commit()
                return asset.id
        return self.loop.run_until_complete(run())

    def _get_question_obj(self, question_id):
        async def run():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                return await svc.get_question(question_id)
        return self.loop.run_until_complete(run())

    def test_candidate_assets_lists_unassociated_in_page_range(self):
        run_id, question_ids = self._seed_run(n_questions=1, tag=" candassets")
        question = self._get_question_obj(question_ids[0])
        asset_id = self._seed_asset(run_id, question, status="UNASSOCIATED")

        self._as("qe_prof_a")
        r = self.client.get(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}/candidate-assets")
        self.assertEqual(r.status_code, 200, r.text)
        ids = {a["id"] for a in r.json()}
        self.assertIn(str(asset_id), ids)

    def test_candidate_assets_cross_tenant_403(self):
        _run_id, question_ids = self._seed_run(n_questions=1, school=_SCHOOL_A, tag=" candassetsx")
        self._as("qe_prof_b")
        r = self.client.get(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}/candidate-assets")
        self.assertEqual(r.status_code, 403, r.text)
        self._as("qe_prof_a")

    def test_associate_asset_success(self):
        run_id, question_ids = self._seed_run(n_questions=1, tag=" assoc")
        question = self._get_question_obj(question_ids[0])
        asset_id = self._seed_asset(run_id, question, status="UNASSOCIATED")

        self._as("qe_prof_a")
        r = self.client.post(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}/assets/{asset_id}/associate")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "ASSOCIATED")

    def test_associate_asset_already_owned_by_other_question_is_422(self):
        run_id, question_ids = self._seed_run(n_questions=2, tag=" assocconflict")
        q0 = self._get_question_obj(question_ids[0])
        q1 = self._get_question_obj(question_ids[1])
        # asset already tied to q1 - q0 trying to steal it must be rejected
        asset_id = self._seed_asset(run_id, q1, status="ASSOCIATED", page=q0.source_page_start)

        self._as("qe_prof_a")
        r = self.client.post(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}/assets/{asset_id}/associate")
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(r.json()["detail"]["code"], "ASSET_NOT_AVAILABLE")

    def test_ignore_asset_success(self):
        run_id, question_ids = self._seed_run(n_questions=1, tag=" ignore")
        question = self._get_question_obj(question_ids[0])
        asset_id = self._seed_asset(run_id, question, status="UNASSOCIATED")

        self._as("qe_prof_a")
        r = self.client.post(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}/assets/{asset_id}/ignore")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "IGNORED")

    def test_ignore_asset_not_found_404(self):
        _run_id, question_ids = self._seed_run(n_questions=1, tag=" ignore404")
        self._as("qe_prof_a")
        r = self.client.post(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}"
            f"/assets/{_uuid.uuid4()}/ignore")
        self.assertEqual(r.status_code, 404, r.text)

    # -- publish_summary / publish_run via HTTP (lines 537-573) --------------
    def test_publish_summary_via_http(self):
        _run_id, question_ids = self._seed_run(n_questions=1, tag=" summary")
        run_id = _run_id
        self._as("qe_prof_a")
        r = self.client.get(
            f"/api/v1/catalog/question-extraction/runs/{run_id}/publish-summary")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertIsInstance(r.json(), dict)

    def test_publish_summary_cross_tenant_403(self):
        run_id, _qids = self._seed_run(n_questions=1, school=_SCHOOL_A, tag=" summaryx")
        self._as("qe_prof_b")
        r = self.client.get(
            f"/api/v1/catalog/question-extraction/runs/{run_id}/publish-summary")
        self.assertEqual(r.status_code, 403, r.text)
        self._as("qe_prof_a")

    def test_publish_run_via_http_success(self):
        run_id, question_ids = self._seed_run(n_questions=1, tag=" publish")
        self._as("qe_prof_a")
        self.client.patch(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}",
            json={"reviewed_text": "pronta para publicacao"},
        )
        self.client.post(
            f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}/approve")
        r = self.client.post(
            f"/api/v1/catalog/question-extraction/runs/{run_id}/publish")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["published_count"], 1)
        # confirm it actually landed: the question is now PUBLISHED
        r2 = self.client.get(f"/api/v1/catalog/question-extraction/questions/{question_ids[0]}")
        self.assertEqual(r2.json()["review_status"], "PUBLISHED")

    def test_publish_run_cross_tenant_403(self):
        run_id, _qids = self._seed_run(n_questions=1, school=_SCHOOL_A, tag=" publishx")
        self._as("qe_prof_b")
        r = self.client.post(f"/api/v1/catalog/question-extraction/runs/{run_id}/publish")
        self.assertEqual(r.status_code, 403, r.text)
        self._as("qe_prof_a")


if __name__ == "__main__":
    unittest.main()
