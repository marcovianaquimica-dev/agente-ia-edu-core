"""PHASE 27 - Question Extraction Engine: persistence, review workflow,
tenant isolation, no-N+1, and API tests.

In-memory SQLite + synthetic PDF fixtures generated at setUp time
(reproducible, self-contained - the real pilot golden-case assertions live
in test_phase27_question_extraction.py). Covers spec s21 (tenant
isolation), s18 (never auto-approve/publish), s17 (idempotent re-run),
s25 (batched, no N+1 as question count grows).
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid
import warnings
from pathlib import Path

warnings.filterwarnings("ignore")

from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import ExtractedQuestion, IngestionDocument
from agente_ia_edu.db.models.admin import School, UserSchoolLink
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.question_extraction_service import (
    QuestionExtractionError,
    QuestionExtractionService,
)
from agente_ia_edu.services.question_publication_service import QuestionPublicationService

_SCHOOL_A = _uuid.uuid5(_uuid.NAMESPACE_DNS, "phase27-school-a")
_SCHOOL_B = _uuid.uuid5(_uuid.NAMESPACE_DNS, "phase27-school-b")


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


def _make_pdf(path: Path, n_questions: int, *, tag: str = "") -> None:
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    y = 50
    for n in range(1, n_questions + 1):
        page.insert_text(
            (72, y), f"{n}.   Questão numero {n}{tag} com enunciado suficientemente longo.", fontsize=9)
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
        s.add(School(id=_SCHOOL_A, code="P27SCHA", name="Escola P27 A", status="ACTIVE"))
        s.add(School(id=_SCHOOL_B, code="P27SCHB", name="Escola P27 B", status="ACTIVE"))
        await s.flush()
        s.add(UserSchoolLink(external_user_id="prof_a", school_id=_SCHOOL_A, role="TEACHER",
                             scope_type="SCHOOL", scope_external_id=str(_SCHOOL_A), active=True))
        s.add(UserSchoolLink(external_user_id="prof_b", school_id=_SCHOOL_B, role="TEACHER",
                             scope_type="SCHOOL", scope_external_id=str(_SCHOOL_B), active=True))
        s.add(UserSchoolLink(external_user_id="student_a", school_id=_SCHOOL_A, role="STUDENT",
                             scope_type="CLASSROOM", scope_external_id="turma-a", active=True))
        await s.commit()


class Phase27ServiceTests(unittest.TestCase):
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

        cls.tmp = Path("/tmp/phase27_service_fixtures")
        cls.tmp.mkdir(exist_ok=True)

        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_a")
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
                    status="processed", ingested_by_external_identity="prof_a",
                )
                s.add(doc); await s.flush()
                await s.commit()
                return doc.id
        return self.loop.run_until_complete(run())

    # -- 1. run + persistence + idempotency ----------------------------
    def test_run_extraction_persists_and_is_idempotent(self):
        path = self.tmp / "doc5.pdf"
        _make_pdf(path, 5)
        doc_id = self._seed_document(path, "doc5")

        async def run():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run1, created1 = await svc.run_extraction(
                    doc_id, path, started_by="prof_a", school_id=_SCHOOL_A, expected_question_count=5)
                run2, created2 = await svc.run_extraction(
                    doc_id, path, started_by="prof_a", school_id=_SCHOOL_A, expected_question_count=5)
                questions = await svc.list_questions(run1.id)
                return run1, created1, run2, created2, questions
        run1, created1, run2, created2, questions = self.loop.run_until_complete(run())
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(run1.id, run2.id)
        self.assertEqual(run1.detected_question_count, 5)
        self.assertEqual(len(questions), 5)

    # -- 2. no question auto-approved/published -------------------------
    def test_new_questions_are_never_auto_approved_or_published(self):
        path = self.tmp / "doc3.pdf"
        _make_pdf(path, 3)
        doc_id = self._seed_document(path, "doc3")

        async def run():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run1, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                return await svc.list_questions(run1.id)
        questions = self.loop.run_until_complete(run())
        self.assertTrue(all(q.review_status not in ("APPROVED", "PUBLISHED") for q in questions))

    # -- 3. review workflow: edit -> approve -> publish (PHASE 29: publish
    #    now promotes into the OFFICIAL Question Bank via a SEPARATE
    #    service, question_publication_service.QuestionPublicationService -
    #    question_extraction_service itself only drives EXTRACTED..APPROVED/
    #    REJECTED) -----------------------------------------------------
    def test_review_then_approve_then_publish(self):
        path = self.tmp / "doc2.pdf"
        _make_pdf(path, 2, tag=" (review-then-publish)")
        doc_id = self._seed_document(path, "doc2")

        async def run():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                pub = QuestionPublicationService(s)
                run1, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                questions = await svc.list_questions(run1.id)
                q = questions[0]
                # force through VALIDATED via an edit even if flagged REVIEW_REQUIRED
                edited = await svc.update_question(
                    q.id, reviewed_text=(q.reconstructed_text or q.normalized_text), reviewed_by="prof_a",
                    options=[{"label": "A", "text": "a", "is_correct": True}, {"label": "B", "text": "b"},
                             {"label": "C", "text": "c"}, {"label": "D", "text": "d"}],
                )
                approved = await svc.approve_question(q.id, reviewed_by="prof_a")
                approved_status = approved.review_status  # ORM object is mutated in place below
                result1 = await pub.publish_run(run1.id, published_by="prof_a", school_id=_SCHOOL_A)
                refreshed = await svc.get_question(q.id)
                published_status = refreshed.review_status
                # idempotent: the question is no longer APPROVED, so a
                # second publish_run touches zero questions (never a
                # second official Question for the same staging row).
                result2 = await pub.publish_run(run1.id, published_by="prof_a", school_id=_SCHOOL_A)
                return edited, approved_status, published_status, result1, result2
        edited, approved_status, published_status, result1, result2 = self.loop.run_until_complete(run())
        self.assertEqual(approved_status, "APPROVED")
        self.assertEqual(published_status, "PUBLISHED")
        self.assertEqual(result1["published_count"], 1)
        self.assertEqual(result2["attempted"], 0)  # nothing left in APPROVED

    def test_cannot_publish_without_approval(self):
        path = self.tmp / "doc1.pdf"
        _make_pdf(path, 1)
        doc_id = self._seed_document(path, "doc1")

        async def run():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                pub = QuestionPublicationService(s)
                run1, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                result = await pub.publish_run(run1.id, published_by="prof_a", school_id=_SCHOOL_A)
                return result
        result = self.loop.run_until_complete(run())
        self.assertEqual(result["attempted"], 0)
        self.assertEqual(result["published_count"], 0)

    def test_publishing_creates_new_authorial_questions_without_touching_existing_ones(self):
        """PHASE 29 supersedes PHASE 27/28's 'PUBLISHED never touches the
        official Question Bank' guarantee BY DESIGN (spec s18/s20: a
        'Publicar no Banco de Questões' step now exists). The guarantee
        that actually matters now is the one this test asserts: publishing
        only ever ADDS new, origin_type='AUTHORIAL' rows and never alters
        an EXISTING official question."""
        path = self.tmp / "doc_official_check.pdf"
        _make_pdf(path, 2, tag=" (official-check)")
        doc_id = self._seed_document(path, "doc_official")

        async def run():
            async with self.factory() as s:
                from agente_ia_edu.db.models import Question
                pre_existing = Question(
                    question_type="MULTIPLE_CHOICE", origin_type="PLATFORM", status="PUBLISHED",
                    visibility_scope="PUBLIC", validation_status="validated",
                )
                s.add(pre_existing)
                await s.commit()
                pre_existing_id = pre_existing.id
                pre_existing_updated_at = pre_existing.updated_at

                svc = QuestionExtractionService(s)
                pub = QuestionPublicationService(s)
                run1, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                questions = await svc.list_questions(run1.id)
                for q in questions:
                    await svc.update_question(
                        q.id, reviewed_text=(q.reconstructed_text or q.normalized_text), reviewed_by="prof_a",
                        options=[{"label": "A", "text": "a", "is_correct": True}, {"label": "B", "text": "b"},
                                 {"label": "C", "text": "c"}, {"label": "D", "text": "d"}],
                    )
                    await svc.approve_question(q.id, reviewed_by="prof_a")
                result = await pub.publish_run(run1.id, published_by="prof_a", school_id=_SCHOOL_A)

                untouched = await s.get(Question, pre_existing_id)
                new_authorial = (await s.execute(
                    select(Question).where(Question.origin_type == "AUTHORIAL")
                )).scalars().all()
                return result, untouched.updated_at, pre_existing_updated_at, len(new_authorial)
        result, updated_at, original_updated_at, new_authorial_count = self.loop.run_until_complete(run())
        self.assertEqual(result["published_count"], 2)
        self.assertEqual(new_authorial_count, 2)
        self.assertEqual(updated_at, original_updated_at)  # pre-existing row: byte-identical, never touched

    # -- 4. tenant isolation via API --------------------------------------
    def test_tenant_isolation_via_api(self):
        path = self.tmp / "doc_tenant.pdf"
        _make_pdf(path, 2)
        doc_id = self._seed_document(path, "doc_tenant")

        self._as("prof_a")
        r = self.client.post(f"/api/v1/catalog/question-extraction/{doc_id}/run", json={})
        self.assertEqual(r.status_code, 201, r.text)
        run_id = r.json()["run"]["id"]

        self._as("prof_b")
        r2 = self.client.get(f"/api/v1/catalog/question-extraction/runs/{run_id}")
        self.assertEqual(r2.status_code, 403)
        self._as("prof_a")

    def test_run_extraction_does_not_leak_another_schools_existing_run(self):
        """IDOR: unlike every other endpoint in this router (get_run,
        get_question, update_question, ...), run_extraction's handler never
        called `_require_scope` on the run it obtained. QuestionExtractionRun
        is idempotent per (ingestion_document_id, engine_version) - so once
        prof_a's school has extracted a document, prof_b (a different
        school) POSTing /{same_document_id}/run hit the "existing run,
        return as-is" branch and got prof_a's full run + every extracted
        question (raw_text, reviewer notes, review_status) back in the 201
        response body, with no school check at all - even though the
        sibling GET /runs/{run_id} correctly 403s the exact same run for
        prof_b."""
        path = self.tmp / "doc_tenant_reuse.pdf"
        _make_pdf(path, 1, tag=" CONFIDENTIAL SCHOOL A CONTENT")
        doc_id = self._seed_document(path, "doc_tenant_reuse")

        self._as("prof_a")
        r = self.client.post(f"/api/v1/catalog/question-extraction/{doc_id}/run", json={})
        self.assertEqual(r.status_code, 201, r.text)

        self._as("prof_b")
        r2 = self.client.post(f"/api/v1/catalog/question-extraction/{doc_id}/run", json={})
        self.assertEqual(r2.status_code, 403, r2.text)
        self.assertNotIn("CONFIDENTIAL", r2.text)
        self._as("prof_a")

    def test_student_cannot_run_extraction(self):
        path = self.tmp / "doc_student.pdf"
        _make_pdf(path, 1)
        doc_id = self._seed_document(path, "doc_student")
        self._as("student_a")
        r = self.client.post(f"/api/v1/catalog/question-extraction/{doc_id}/run", json={})
        self.assertEqual(r.status_code, 403)
        self._as("prof_a")

    # -- PHASE 31: resolution review (edit/approve/reject) - independent of
    #    review_status - see question_extraction_service's resolution
    #    section ----------------------------------------------------------
    def _seed_question_with_resolution(self, *, resolution_status: str = "NONE",
                                        resolution_raw_text: str | None = "Passo 1: ...; Passo 2: resposta C.",
                                        resolution_reviewed_text: str | None = None) -> _uuid.UUID:
        """Simulates what the parallel capture agent's extraction would
        produce - a staged question with resolution_raw_text already
        populated by the engine, resolution_status set accordingly. Inserted
        directly (no dependency on that agent's code, per the task brief)."""
        path = self.tmp / f"doc_resolution_{_uuid.uuid4().hex[:8]}.pdf"
        _make_pdf(path, 1)
        doc_id = self._seed_document(path, f"res-{_uuid.uuid4().hex[:8]}")

        async def run():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run1, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                questions = await svc.list_questions(run1.id)
                q = questions[0]
                q.resolution_raw_text = resolution_raw_text
                q.resolution_reviewed_text = resolution_reviewed_text
                q.resolution_status = resolution_status
                await s.commit()
                return q.id
        return self.loop.run_until_complete(run())

    def test_update_resolution_sets_text_and_moves_to_pending_review(self):
        question_id = self._seed_question_with_resolution(resolution_status="NONE")

        async def run():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                return await svc.update_resolution(
                    question_id, resolution_reviewed_text="Passo 1 revisado; resposta correta: C.",
                    reviewed_by="prof_a")
        updated = self.loop.run_until_complete(run())
        self.assertEqual(updated.resolution_status, "PENDING_REVIEW")
        self.assertEqual(updated.resolution_reviewed_text, "Passo 1 revisado; resposta correta: C.")
        self.assertEqual(updated.status_history[-1]["event"], "RESOLUTION_TEXT_EDIT")

    def test_update_resolution_after_approval_reopens_review(self):
        """Design decision (documented in update_resolution's docstring):
        editing an already-APPROVED resolution invalidates that
        certification and always returns to PENDING_REVIEW - unlike the
        statement's update_question, which leaves an APPROVED question's
        status untouched on a later edit."""
        question_id = self._seed_question_with_resolution(
            resolution_status="APPROVED", resolution_reviewed_text="versão antiga aprovada")

        async def run():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                return await svc.update_resolution(
                    question_id, resolution_reviewed_text="correção pós-aprovação", reviewed_by="prof_a")
        updated = self.loop.run_until_complete(run())
        self.assertEqual(updated.resolution_status, "PENDING_REVIEW")
        self.assertEqual(updated.resolution_reviewed_text, "correção pós-aprovação")

    def test_approve_resolution_success(self):
        question_id = self._seed_question_with_resolution(
            resolution_status="PENDING_REVIEW", resolution_reviewed_text="resposta revisada: C.")

        async def run():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                return await svc.approve_resolution(question_id, reviewed_by="prof_a")
        updated = self.loop.run_until_complete(run())
        self.assertEqual(updated.resolution_status, "APPROVED")
        self.assertEqual(updated.status_history[-1]["event"], "RESOLUTION_STATUS_CHANGE")
        self.assertEqual(updated.status_history[-1]["detail"]["to_resolution_status"], "APPROVED")

    def test_approve_empty_resolution_is_rejected_with_clear_error_not_500(self):
        question_id = self._seed_question_with_resolution(
            resolution_status="NONE", resolution_raw_text=None, resolution_reviewed_text=None)

        async def run():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                return await svc.approve_resolution(question_id, reviewed_by="prof_a")
        with self.assertRaises(QuestionExtractionError) as ctx:
            self.loop.run_until_complete(run())
        self.assertEqual(ctx.exception.code, "RESOLUTION_APPROVAL_VALIDATION_FAILED")

    def test_approve_whitespace_only_resolution_is_rejected(self):
        question_id = self._seed_question_with_resolution(
            resolution_status="PENDING_REVIEW", resolution_raw_text=None, resolution_reviewed_text="   \n  ")

        async def run():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                return await svc.approve_resolution(question_id, reviewed_by="prof_a")
        with self.assertRaises(QuestionExtractionError) as ctx:
            self.loop.run_until_complete(run())
        self.assertEqual(ctx.exception.code, "RESOLUTION_APPROVAL_VALIDATION_FAILED")

    def test_approve_resolution_falls_back_to_raw_text_when_no_reviewed_text(self):
        question_id = self._seed_question_with_resolution(
            resolution_status="PENDING_REVIEW",
            resolution_raw_text="Passo 1: ...; resposta B.", resolution_reviewed_text=None)

        async def run():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                return await svc.approve_resolution(question_id, reviewed_by="prof_a")
        updated = self.loop.run_until_complete(run())
        self.assertEqual(updated.resolution_status, "APPROVED")

    def test_reject_resolution_with_reason(self):
        question_id = self._seed_question_with_resolution(
            resolution_status="PENDING_REVIEW", resolution_reviewed_text="resposta duvidosa")

        async def run():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                return await svc.reject_resolution(
                    question_id, reviewed_by="prof_a", reason="resposta incompatível com o gabarito oficial")
        updated = self.loop.run_until_complete(run())
        self.assertEqual(updated.resolution_status, "REJECTED")
        self.assertEqual(
            updated.status_history[-1]["detail"]["reason"], "resposta incompatível com o gabarito oficial")

    def test_reject_resolution_without_reason(self):
        question_id = self._seed_question_with_resolution(
            resolution_status="PENDING_REVIEW", resolution_reviewed_text="resposta duvidosa")

        async def run():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                return await svc.reject_resolution(question_id, reviewed_by="prof_a")
        updated = self.loop.run_until_complete(run())
        self.assertEqual(updated.resolution_status, "REJECTED")

    def test_resolution_status_independent_of_review_status(self):
        """An APPROVED question (statement) can have its resolution still
        PENDING_REVIEW or entirely absent - the two state machines never
        interfere with each other."""
        question_id = self._seed_question_with_resolution(
            resolution_status="NONE", resolution_raw_text=None, resolution_reviewed_text=None)

        async def run():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                q = await svc.get_question(question_id)
                edited = await svc.update_question(
                    q.id, reviewed_text=(q.reconstructed_text or q.normalized_text), reviewed_by="prof_a",
                    options=[{"label": "A", "text": "a", "is_correct": True}, {"label": "B", "text": "b"},
                             {"label": "C", "text": "c"}, {"label": "D", "text": "d"}],
                )
                approved = await svc.approve_question(q.id, reviewed_by="prof_a")
                return approved
        approved = self.loop.run_until_complete(run())
        self.assertEqual(approved.review_status, "APPROVED")
        self.assertEqual(approved.resolution_status, "NONE")

    # -- resolution review via HTTP ---------------------------------------
    def test_resolution_review_via_api_edit_then_approve(self):
        question_id = self._seed_question_with_resolution(resolution_status="NONE")
        self._as("prof_a")
        r = self.client.patch(
            f"/api/v1/catalog/question-extraction/questions/{question_id}/resolution",
            json={"resolution_reviewed_text": "Passo a passo revisado via API."})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["resolution_status"], "PENDING_REVIEW")

        r2 = self.client.post(
            f"/api/v1/catalog/question-extraction/questions/{question_id}/resolution/approve")
        self.assertEqual(r2.status_code, 200, r2.text)
        self.assertEqual(r2.json()["resolution_status"], "APPROVED")

    def test_resolution_approve_empty_via_api_is_422_not_500(self):
        question_id = self._seed_question_with_resolution(
            resolution_status="NONE", resolution_raw_text=None, resolution_reviewed_text=None)
        self._as("prof_a")
        r = self.client.post(
            f"/api/v1/catalog/question-extraction/questions/{question_id}/resolution/approve")
        self.assertEqual(r.status_code, 422, r.text)
        self.assertEqual(r.json()["detail"]["code"], "RESOLUTION_APPROVAL_VALIDATION_FAILED")

    def test_resolution_reject_via_api(self):
        question_id = self._seed_question_with_resolution(
            resolution_status="PENDING_REVIEW", resolution_reviewed_text="texto a rejeitar")
        self._as("prof_a")
        r = self.client.post(
            f"/api/v1/catalog/question-extraction/questions/{question_id}/resolution/reject",
            json={"reason": "gabarito incorreto"})
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["resolution_status"], "REJECTED")

    def test_resolution_review_tenant_isolation_via_api(self):
        question_id = self._seed_question_with_resolution(resolution_status="NONE")
        self._as("prof_b")
        r = self.client.patch(
            f"/api/v1/catalog/question-extraction/questions/{question_id}/resolution",
            json={"resolution_reviewed_text": "tentativa de outra escola"})
        self.assertEqual(r.status_code, 403)
        self._as("prof_a")

    # -- 5. no N+1: batched persistence as question count grows -----------
    def test_no_n_plus_1_as_question_count_grows(self):
        counts = {}
        for n in (1, 10, 50, 100):
            path = self.tmp / f"doc_perf_{n}.pdf"
            _make_pdf(path, n)
            doc_id = self._seed_document(path, f"perf{n}")

            async def run():
                async with self.factory() as s:
                    svc = QuestionExtractionService(s)
                    q = {"c": 0}

                    @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
                    def _c(*_a):  # noqa: ANN001
                        q["c"] += 1
                    try:
                        await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                    finally:
                        event.remove(self.engine.sync_engine, "before_cursor_execute", _c)
                    return q["c"]
            counts[n] = self.loop.run_until_complete(run())
        # a handful of BATCHED inserts regardless of how many questions -
        # never one extra query per question.
        self.assertLess(counts[100], counts[1] + 20, f"query counts: {counts}")


if __name__ == "__main__":
    unittest.main()
