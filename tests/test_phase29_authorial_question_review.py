"""PHASE 29 - Authorial Question Review, Approval & Publication: tests.

Covers the 27 cases from spec s27: empty/1/N-question queues, filters,
priority, open/edit/approve/reject, asset associate/ignore, audit trail,
tenant/role scope, duplicate detection, publish gating (APPROVED-only,
never REVIEW_REQUIRED/REJECTED), raw_text immutability, reload/determinism.
Mobile/320px and pure-frontend behaviour are covered separately in
test_phase29_authorial_question_review_frontend.js.
"""

from __future__ import annotations

import ast
import asyncio
import unittest
import uuid as _uuid
from pathlib import Path
from tempfile import TemporaryDirectory

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import ExtractedQuestion, IngestionDocument, Question, QuestionVersion
from agente_ia_edu.db.models.admin import School, UserSchoolLink
from agente_ia_edu.services.question_extraction_service import (
    QuestionExtractionError,
    QuestionExtractionService,
    compute_priority,
)
from agente_ia_edu.services.question_publication_service import QuestionPublicationService

_SCHOOL_A = _uuid.uuid5(_uuid.NAMESPACE_DNS, "phase29-school-a")
_SCHOOL_B = _uuid.uuid5(_uuid.NAMESPACE_DNS, "phase29-school-b")


def _make_pdf(path: Path, n_questions: int, *, tag: str = "") -> None:
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    y = 50
    for n in range(1, n_questions + 1):
        page.insert_text(
            (72, y), f"{n}.   Questao numero {n}{tag} com enunciado suficientemente longo.", fontsize=9)
        y += 15
        for letter in "abcd":
            page.insert_text((90, y), f"{letter}) alternativa {letter} da questao {n}{tag}", fontsize=9)
            y += 12
        y += 20
        if y > 780:
            page = doc.new_page()
            y = 50
    doc.save(str(path))


def _make_low_confidence_pdf(path: Path, n_questions: int, *, tag: str = "") -> None:
    """Discursive, option-less questions with NO paragraph-break signal
    score lower (~0.55, below the 0.6 REVIEW_REQUIRED threshold) and land
    in REVIEW_REQUIRED - used to populate a realistic review queue. The
    statement is kept >=40 chars so it is classified 'discursive' (spec's
    own numeric-hint regex only fires below 40 chars)."""
    import fitz
    doc = fitz.open()
    page = doc.new_page()
    y = 50
    for n in range(1, n_questions + 1):
        page.insert_text(
            (72, y),
            f"{n}. Explique com suas palavras o fenomeno descrito no texto{tag} numero {n}.",
            fontsize=9,
        )
        y += 20
        if y > 780:
            page = doc.new_page()
            y = 50
    doc.save(str(path))


class Phase29ReviewTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            async with cls.factory() as s:
                s.add(School(id=_SCHOOL_A, code="P29SCHA", name="Escola P29 A", status="ACTIVE"))
                s.add(School(id=_SCHOOL_B, code="P29SCHB", name="Escola P29 B", status="ACTIVE"))
                await s.flush()
                s.add(UserSchoolLink(external_user_id="prof_a", school_id=_SCHOOL_A, role="TEACHER",
                                     scope_type="SCHOOL", scope_external_id=str(_SCHOOL_A), active=True))
                s.add(UserSchoolLink(external_user_id="coord_a", school_id=_SCHOOL_A, role="COORDINATOR",
                                     scope_type="SCHOOL", scope_external_id=str(_SCHOOL_A), active=True))
                s.add(UserSchoolLink(external_user_id="prof_b", school_id=_SCHOOL_B, role="TEACHER",
                                     scope_type="SCHOOL", scope_external_id=str(_SCHOOL_B), active=True))
                await s.commit()

        cls.loop.run_until_complete(_prep())
        cls._tmp = TemporaryDirectory()
        cls.tmp = Path(cls._tmp.name)

    @classmethod
    def tearDownClass(cls):
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()
        cls._tmp.cleanup()

    def _run(self, coro):
        return self.loop.run_until_complete(coro)

    def _seed_document(self, path: Path, tag: str) -> _uuid.UUID:
        async def go():
            async with self.factory() as s:
                doc = IngestionDocument(
                    filename=path.name, document_type="PDF", document_hash=f"hash-{tag}",
                    storage_uri=str(path), file_size_bytes=path.stat().st_size,
                    status="processed", ingested_by_external_identity="prof_a",
                )
                s.add(doc); await s.flush()
                await s.commit()
                return doc.id
        return self._run(go())

    # -- 1. fila vazia -----------------------------------------------------
    def test_empty_queue(self):
        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                return await svc.list_review_queue(school_id=_uuid.uuid4())
        items = self._run(go())
        self.assertEqual(items, [])

    # -- 2. fila com uma questão --------------------------------------------
    def test_queue_with_one_question(self):
        path = self.tmp / "one.pdf"
        _make_low_confidence_pdf(path, 1, tag=" one")
        doc_id = self._seed_document(path, "one")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                return await svc.list_review_queue(school_id=_SCHOOL_A, run_id=run.id)
        items = self._run(go())
        self.assertEqual(len(items), 1)
        self.assertIn(items[0]["priority"], ("P1", "P2", "P3", "P4"))

    # -- 3. fila com N (33) questões -----------------------------------------
    def test_queue_with_33_questions(self):
        path = self.tmp / "thirty_three.pdf"
        _make_low_confidence_pdf(path, 33, tag=" tt")
        doc_id = self._seed_document(path, "thirtythree")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                items = await svc.list_review_queue(school_id=_SCHOOL_A, run_id=run.id, limit=100)
                progress = await svc.queue_progress(school_id=_SCHOOL_A, run_id=run.id)
                return items, progress
        items, progress = self._run(go())
        self.assertEqual(len(items), 33)
        self.assertEqual(progress["total"], 33)
        self.assertEqual(progress["pending"], 33)

    # -- 4. filtros ----------------------------------------------------------
    def test_queue_filters_by_status_and_reason(self):
        path = self.tmp / "filters.pdf"
        _make_low_confidence_pdf(path, 5, tag=" filt")
        doc_id = self._seed_document(path, "filters")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                by_status = await svc.list_review_queue(
                    school_id=_SCHOOL_A, run_id=run.id, review_status="REVIEW_REQUIRED")
                by_bad_reason = await svc.list_review_queue(
                    school_id=_SCHOOL_A, run_id=run.id, reason="COLUMN_AMBIGUITY")
                return by_status, by_bad_reason
        by_status, by_bad_reason = self._run(go())
        self.assertTrue(all(x["review_status"] == "REVIEW_REQUIRED" for x in by_status))
        self.assertEqual(by_bad_reason, [])  # none of these low-confidence discursive Qs have that reason

    # -- 5. prioridade (deterministic, no AI) --------------------------------
    def test_priority_is_deterministic_and_reproducible(self):
        self.assertEqual(compute_priority(0.9, []), "P1")
        self.assertEqual(compute_priority(0.5, ["LOW_CONFIDENCE"]), "P2")
        self.assertEqual(compute_priority(0.2, []), "P3")
        self.assertEqual(compute_priority(0.9, ["COLUMN_AMBIGUITY"]), "P4")
        self.assertEqual(compute_priority(0.9, ["A", "B", "C"]), "P4")
        # same inputs -> same output, always
        self.assertEqual(compute_priority(0.55, ["LOW_CONFIDENCE"]), compute_priority(0.55, ["LOW_CONFIDENCE"]))

    # -- 6. abrir questão (REVIEW_REQUIRED -> IN_REVIEW) ---------------------
    def test_start_review_transitions_review_required_to_in_review(self):
        path = self.tmp / "open.pdf"
        _make_low_confidence_pdf(path, 1, tag=" open")
        doc_id = self._seed_document(path, "open")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                qs = await svc.list_questions(run.id)
                q = qs[0]
                self.assertEqual(q.review_status, "REVIEW_REQUIRED")
                opened = await svc.start_review(q.id, reviewer="prof_a")
                return opened.review_status, opened.status_history[-1]
        status, last_event = self._run(go())
        self.assertEqual(status, "IN_REVIEW")
        self.assertEqual(last_event["to_status"], "IN_REVIEW")
        self.assertEqual(last_event["actor"], "prof_a")

    def test_start_review_rejects_from_terminal_status(self):
        path = self.tmp / "open2.pdf"
        _make_pdf(path, 1, tag=" open2")
        doc_id = self._seed_document(path, "open2")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                qs = await svc.list_questions(run.id)
                q = qs[0]
                await svc.reject_question(q.id, reviewed_by="prof_a", reason="NOT_A_QUESTION")
                with self.assertRaises(QuestionExtractionError):
                    await svc.start_review(q.id, reviewer="prof_a")
        self._run(go())

    # -- 7/15/16. editar enunciado / salvar / salvar e continuar -----------
    def test_edit_statement_stores_reviewed_text_separately_from_raw_text(self):
        path = self.tmp / "edit_stmt.pdf"
        _make_pdf(path, 1, tag=" stmt")
        doc_id = self._seed_document(path, "edit_stmt")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                q = (await svc.list_questions(run.id))[0]
                original_raw = q.raw_text
                updated = await svc.update_question(
                    q.id, reviewed_text="Texto corrigido pelo professor.", reviewed_by="prof_a")
                return original_raw, updated
        original_raw, updated = self._run(go())
        self.assertEqual(updated.reviewed_text, "Texto corrigido pelo professor.")
        self.assertEqual(updated.raw_text, original_raw)  # never touched
        self.assertEqual(updated.review_status, "VALIDATED")
        self.assertEqual(updated.status_history[-1]["event"], "TEXT_EDIT")

    # -- 8/9/10. editar/adicionar/remover alternativa ------------------------
    def test_edit_add_and_remove_options(self):
        path = self.tmp / "opts.pdf"
        _make_pdf(path, 1, tag=" opts")
        doc_id = self._seed_document(path, "opts")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                q = (await svc.list_questions(run.id))[0]
                # edit + add a 5th option
                new_options = [
                    {"label": "A", "text": "editada A"}, {"label": "B", "text": "b"},
                    {"label": "C", "text": "c"}, {"label": "D", "text": "d"},
                    {"label": "E", "text": "nova alternativa E"},
                ]
                updated = await svc.update_question(q.id, options=new_options, reviewed_by="prof_a")
                after_add = sorted(updated.options, key=lambda o: o.position)
                # remove the last one
                fewer_options = new_options[:-1]
                updated2 = await svc.update_question(q.id, options=fewer_options, reviewed_by="prof_a")
                after_remove = sorted(updated2.options, key=lambda o: o.position)
                return after_add, after_remove, updated2.status_history
        after_add, after_remove, history = self._run(go())
        self.assertEqual([o.label for o in after_add], ["A", "B", "C", "D", "E"])
        self.assertEqual(after_add[0].text, "editada A")
        self.assertEqual([o.label for o in after_remove], ["A", "B", "C", "D"])
        self.assertTrue(any(e["event"] == "OPTIONS_EDIT" for e in history))

    def test_discursive_question_keeps_empty_options_never_forced_to_a_e(self):
        path = self.tmp / "discursive.pdf"
        _make_low_confidence_pdf(path, 1, tag=" disc")
        doc_id = self._seed_document(path, "discursive")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                q = (await svc.list_questions(run.id))[0]
                return q.question_type, q.options
        qtype, options = self._run(go())
        self.assertEqual(qtype, "discursive")
        self.assertEqual(list(options), [])

    # -- 11/12. associar/ignorar asset ---------------------------------------
    def test_associate_and_ignore_unassociated_asset(self):
        import fitz
        path = self.tmp / "asset.pdf"
        doc = fitz.open()
        page = doc.new_page()
        page.insert_text((72, 60), "1. Observe a figura e responda: a) x b) y c) z d) w", fontsize=9)
        pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
        pix.set_rect(pix.irect, (200, 0, 0))
        # place the image far from the question's own association range
        # by putting it on the SAME page but let the engine's degenerate
        # filter and page-range matching still associate it automatically
        # in the common case; to exercise the truly-unassociated path we
        # instead directly flip an associated asset back for the test.
        page.insert_image(fitz.Rect(300, 300, 340, 340), pixmap=pix)
        doc.save(str(path))
        doc_id = self._seed_document(path, "asset")

        async def go():
            async with self.factory() as s:
                from agente_ia_edu.db.models import ExtractedQuestionAsset
                svc = QuestionExtractionService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                q = (await svc.list_questions(run.id))[0]
                # force the auto-associated asset back to UNASSOCIATED to
                # exercise the human associate/ignore decision explicitly.
                asset = (await s.execute(select(ExtractedQuestionAsset).where(
                    ExtractedQuestionAsset.run_id == run.id))).scalars().first()
                asset.question_id = None
                asset.status = "UNASSOCIATED"
                await s.commit()

                candidates = await svc.list_candidate_assets(q.id)
                self.assertEqual(len(candidates), 1)
                associated = await svc.associate_asset(q.id, candidates[0].id, reviewed_by="prof_a")
                assoc_status_value = associated.status  # snapshot - associate/ignore share one row
                ignored = await svc.ignore_asset(q.id, candidates[0].id, reviewed_by="prof_a")
                refreshed = await svc.get_question(q.id)
                return assoc_status_value, ignored.status, refreshed.status_history
        assoc_status, ignore_status, history = self._run(go())
        self.assertEqual(assoc_status, "ASSOCIATED")
        self.assertEqual(ignore_status, "IGNORED")  # asset preserved, never deleted
        events = {e["event"] for e in history}
        self.assertIn("ASSET_ASSOCIATED", events)
        self.assertIn("ASSET_IGNORED", events)

    # -- 13/23/24. aprovar / bloquear publicação indevida --------------------
    def test_approve_requires_edit_from_review_required(self):
        path = self.tmp / "approve_block.pdf"
        _make_low_confidence_pdf(path, 1, tag=" ablk")
        doc_id = self._seed_document(path, "approve_block")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                q = (await svc.list_questions(run.id))[0]
                with self.assertRaises(QuestionExtractionError):
                    await svc.approve_question(q.id, reviewed_by="prof_a")
        self._run(go())

    def test_approve_validates_structure_before_allowing(self):
        path = self.tmp / "approve_validate.pdf"
        _make_pdf(path, 1, tag=" aval")
        doc_id = self._seed_document(path, "approve_validate")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                q = (await svc.list_questions(run.id))[0]
                await svc.update_question(q.id, reviewed_text="   ", reviewed_by="prof_a")
                with self.assertRaises(QuestionExtractionError) as ctx:
                    await svc.approve_question(q.id, reviewed_by="prof_a")
                return ctx.exception.code
        code = self._run(go())
        self.assertEqual(code, "APPROVAL_VALIDATION_FAILED")

    # -- 14. rejeitar ---------------------------------------------------------
    def test_reject_requires_valid_reason(self):
        path = self.tmp / "reject.pdf"
        _make_pdf(path, 1, tag=" rej")
        doc_id = self._seed_document(path, "reject")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                q = (await svc.list_questions(run.id))[0]
                with self.assertRaises(QuestionExtractionError):
                    await svc.reject_question(q.id, reviewed_by="prof_a", reason="NOT_A_REAL_REASON")
                rejected = await svc.reject_question(q.id, reviewed_by="prof_a", reason="DUPLICATE")
                return rejected
        rejected = self._run(go())
        self.assertEqual(rejected.review_status, "REJECTED")
        self.assertEqual(rejected.rejection_reason, "DUPLICATE")

    # -- 17. auditoria ----------------------------------------------------------
    def test_audit_trail_never_overwritten_always_appended(self):
        path = self.tmp / "audit.pdf"
        _make_low_confidence_pdf(path, 1, tag=" aud")
        doc_id = self._seed_document(path, "audit")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                q = (await svc.list_questions(run.id))[0]
                await svc.start_review(q.id, reviewer="prof_a")
                await svc.update_question(q.id, reviewed_text="Enunciado revisado.", reviewed_by="prof_a")
                await svc.update_question(q.id, notes="nota interna", reviewed_by="prof_a")
                final = await svc.approve_question(q.id, reviewed_by="prof_a")
                return final.status_history
        history = self._run(go())
        events = [e["event"] for e in history]
        self.assertEqual(events[0], "EXTRACTED")
        self.assertIn("STATUS_CHANGE", events)  # IN_REVIEW, VALIDATED, APPROVED transitions
        self.assertIn("TEXT_EDIT", events)
        self.assertIn("NOTES_EDIT", events)
        # every entry has actor + timestamp - never anonymous, never undated
        self.assertTrue(all(e.get("actor") and e.get("at") for e in history))

    # -- 18/19/20. tenant isolation + escopo professor/coordenador -----------
    def test_tenant_isolation_professor_only_sees_own_school(self):
        path = self.tmp / "tenant.pdf"
        _make_low_confidence_pdf(path, 1, tag=" ten")
        doc_id = self._seed_document(path, "tenant")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                own_school = await svc.list_review_queue(school_id=_SCHOOL_A, run_id=run.id)
                other_school = await svc.list_review_queue(school_id=_SCHOOL_B, run_id=run.id)
                return own_school, other_school
        own, other = self._run(go())
        self.assertEqual(len(own), 1)
        self.assertEqual(other, [])

    def test_coordinator_can_review_within_their_school_scope(self):
        path = self.tmp / "coord.pdf"
        _make_low_confidence_pdf(path, 1, tag=" coord")
        doc_id = self._seed_document(path, "coord")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                q = (await svc.list_review_queue(school_id=_SCHOOL_A, run_id=run.id))[0]
                opened = await svc.start_review(_uuid.UUID(q["id"]), reviewer="coord_a")
                return opened.reviewed_by_external_identity
        reviewer = self._run(go())
        self.assertEqual(reviewer, "coord_a")

    # -- 21/22. duplicate detection + publish --------------------------------
    def test_publish_creates_official_questions_and_detects_duplicates(self):
        path1 = self.tmp / "pub1.pdf"
        path2 = self.tmp / "pub2.pdf"
        _make_pdf(path1, 1, tag=" (pub-run-1)")
        _make_pdf(path2, 1, tag=" (pub-run-1)")  # identical content -> duplicate on 2nd publish
        doc_id1 = self._seed_document(path1, "pub1")
        doc_id2 = self._seed_document(path2, "pub2")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                pub = QuestionPublicationService(s)

                run1, _ = await svc.run_extraction(doc_id1, path1, started_by="prof_a", school_id=_SCHOOL_A)
                q1 = (await svc.list_questions(run1.id))[0]
                await svc.update_question(q1.id, reviewed_text=(q1.reconstructed_text or q1.normalized_text), reviewed_by="prof_a")
                await svc.approve_question(q1.id, reviewed_by="prof_a")
                result1 = await pub.publish_run(run1.id, published_by="prof_a", school_id=_SCHOOL_A)

                run2, _ = await svc.run_extraction(doc_id2, path2, started_by="prof_a", school_id=_SCHOOL_A)
                q2 = (await svc.list_questions(run2.id))[0]
                await svc.update_question(q2.id, reviewed_text=(q2.reconstructed_text or q2.normalized_text), reviewed_by="prof_a")
                await svc.approve_question(q2.id, reviewed_by="prof_a")
                result2 = await pub.publish_run(run2.id, published_by="prof_a", school_id=_SCHOOL_A)

                # Scoped to THIS test's own content_hash, not a blanket
                # table scan: setUpClass shares one in-memory DB across
                # every test method in this class, so counting every
                # AUTHORIAL Question ever created here breaks as soon as any
                # other test method also publishes one (order-dependent).
                # Filtering by content_hash (rather than by result1's own
                # official_question_id, which would trivially always be 1
                # regardless of whether duplicate detection worked) still
                # catches a real regression: a second official Question for
                # the same content would carry the same content_hash on its
                # QuestionVersion.
                official_version = await s.get(
                    QuestionVersion, _uuid.UUID(result1["published"][0]["official_version_id"])
                )
                official = (await s.execute(
                    select(Question)
                    .join(QuestionVersion, QuestionVersion.question_id == Question.id)
                    .where(
                        Question.origin_type == "AUTHORIAL",
                        QuestionVersion.content_hash == official_version.content_hash,
                    )
                )).scalars().all()
                refreshed_q2 = await svc.get_question(q2.id)
                return result1, result2, len(official), refreshed_q2.review_status
        result1, result2, official_count, q2_status = self._run(go())
        self.assertEqual(result1["published_count"], 1)
        self.assertEqual(result2["published_count"], 0)
        self.assertEqual(result2["duplicate_count"], 1)
        self.assertEqual(official_count, 1)  # never a second official Question for identical content
        self.assertEqual(q2_status, "DUPLICATE_REVIEW")  # never silently discarded

    # -- PHASE 31. publicação copia resolução aprovada para o oficial --------
    def test_publish_copies_approved_resolution_to_official_version(self):
        path = self.tmp / "pub_resolution.pdf"
        _make_pdf(path, 1, tag=" (pub-resolution)")
        doc_id = self._seed_document(path, "pub_resolution")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                pub = QuestionPublicationService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                q = (await svc.list_questions(run.id))[0]
                await svc.update_question(
                    q.id, reviewed_text=(q.reconstructed_text or q.normalized_text), reviewed_by="prof_a")
                await svc.approve_question(q.id, reviewed_by="prof_a")
                # simulate the (separate, in-progress) resolution review flow
                # directly, as instructed: populate reviewed_text + APPROVED.
                stored = await s.get(ExtractedQuestion, q.id)
                stored.resolution_raw_text = "Passo 1: ... Passo 2: ..."
                stored.resolution_reviewed_text = "Passo 1: isolar x. Passo 2: substituir e resolver."
                stored.resolution_status = "APPROVED"
                await s.commit()

                result = await pub.publish_run(run.id, published_by="prof_a", school_id=_SCHOOL_A)
                version_id = result["published"][0]["official_version_id"]
                version = await s.get(QuestionVersion, _uuid.UUID(version_id))
                return result, version.resolution_text
        result, resolution_text = self._run(go())
        self.assertEqual(result["published_count"], 1)
        self.assertEqual(resolution_text, "Passo 1: isolar x. Passo 2: substituir e resolver.")

    def test_publish_without_approved_resolution_leaves_resolution_text_none(self):
        path = self.tmp / "pub_no_resolution.pdf"
        _make_pdf(path, 1, tag=" (pub-no-resolution)")
        doc_id = self._seed_document(path, "pub_no_resolution")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                pub = QuestionPublicationService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                q = (await svc.list_questions(run.id))[0]
                await svc.update_question(
                    q.id, reviewed_text=(q.reconstructed_text or q.normalized_text), reviewed_by="prof_a")
                await svc.approve_question(q.id, reviewed_by="prof_a")
                # no resolution captured at all (the current-corpus norm:
                # resolution_status stays 'NONE', matching real ENEM PDFs)
                result = await pub.publish_run(run.id, published_by="prof_a", school_id=_SCHOOL_A)
                version_id = result["published"][0]["official_version_id"]
                version = await s.get(QuestionVersion, _uuid.UUID(version_id))
                return result, version.resolution_text
        result, resolution_text = self._run(go())
        self.assertEqual(result["published_count"], 1)
        self.assertIsNone(resolution_text)

    def test_publish_ignores_resolution_pending_or_rejected(self):
        path = self.tmp / "pub_resolution_pending.pdf"
        _make_pdf(path, 1, tag=" (pub-resolution-pending)")
        doc_id = self._seed_document(path, "pub_resolution_pending")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                pub = QuestionPublicationService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                q = (await svc.list_questions(run.id))[0]
                await svc.update_question(
                    q.id, reviewed_text=(q.reconstructed_text or q.normalized_text), reviewed_by="prof_a")
                await svc.approve_question(q.id, reviewed_by="prof_a")
                stored = await s.get(ExtractedQuestion, q.id)
                stored.resolution_raw_text = "rascunho ainda não revisado"
                stored.resolution_reviewed_text = "rascunho ainda não revisado"
                stored.resolution_status = "PENDING_REVIEW"  # not yet APPROVED
                await s.commit()

                result = await pub.publish_run(run.id, published_by="prof_a", school_id=_SCHOOL_A)
                version_id = result["published"][0]["official_version_id"]
                version = await s.get(QuestionVersion, _uuid.UUID(version_id))
                return version.resolution_text
        resolution_text = self._run(go())
        self.assertIsNone(resolution_text)  # PENDING_REVIEW never leaks into the official row

    # -- 23. publicar REVIEW_REQUIRED nunca acontece -------------------------
    def test_publish_never_includes_review_required_questions(self):
        path = self.tmp / "pub_reqreview.pdf"
        _make_low_confidence_pdf(path, 1, tag=" prr")
        doc_id = self._seed_document(path, "pub_reqreview")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                pub = QuestionPublicationService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                q = (await svc.list_questions(run.id))[0]
                self.assertEqual(q.review_status, "REVIEW_REQUIRED")
                result = await pub.publish_run(run.id, published_by="prof_a", school_id=_SCHOOL_A)
                refreshed = await svc.get_question(q.id)
                return result, refreshed.review_status
        result, status = self._run(go())
        self.assertEqual(result["published_count"], 0)
        self.assertEqual(status, "REVIEW_REQUIRED")  # untouched by publish

    # -- 24. publicar REJECTED nunca acontece --------------------------------
    def test_publish_never_includes_rejected_questions(self):
        path = self.tmp / "pub_rejected.pdf"
        _make_pdf(path, 1, tag=" prj")
        doc_id = self._seed_document(path, "pub_rejected")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                pub = QuestionPublicationService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                q = (await svc.list_questions(run.id))[0]
                await svc.reject_question(q.id, reviewed_by="prof_a", reason="OTHER")
                result = await pub.publish_run(run.id, published_by="prof_a", school_id=_SCHOOL_A)
                refreshed = await svc.get_question(q.id)
                return result, refreshed.review_status
        result, status = self._run(go())
        self.assertEqual(result["published_count"], 0)
        self.assertEqual(status, "REJECTED")  # can never reach PUBLISHED without a fresh approval

    # -- 25. original imutável --------------------------------------------------
    def test_raw_text_and_source_document_are_never_altered_by_review(self):
        path = self.tmp / "immutable.pdf"
        _make_pdf(path, 1, tag=" immut")
        doc_id = self._seed_document(path, "immutable")
        original_bytes = path.read_bytes()

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                q = (await svc.list_questions(run.id))[0]
                raw_before = q.raw_text
                await svc.update_question(q.id, reviewed_text="algo completamente diferente", reviewed_by="prof_a")
                refreshed = await svc.get_question(q.id)
                return raw_before, refreshed.raw_text
        raw_before, raw_after = self._run(go())
        self.assertEqual(raw_before, raw_after)
        self.assertEqual(path.read_bytes(), original_bytes)  # source PDF byte-identical

    # -- 26. reload/determinism ------------------------------------------------
    def test_reopening_a_question_shows_persisted_state_without_re_extraction(self):
        path = self.tmp / "reload.pdf"
        _make_low_confidence_pdf(path, 1, tag=" reload")
        doc_id = self._seed_document(path, "reload")

        async def go():
            async with self.factory() as s:
                svc = QuestionExtractionService(s)
                run, _ = await svc.run_extraction(doc_id, path, started_by="prof_a", school_id=_SCHOOL_A)
                q = (await svc.list_questions(run.id))[0]
                await svc.start_review(q.id, reviewer="prof_a")
                await svc.update_question(q.id, reviewed_text="Texto persistido.", reviewed_by="prof_a")
                first_open = await svc.get_question(q.id)
                second_open = await svc.get_question(q.id)  # simulate reopening later
                return first_open, second_open
        first_open, second_open = self._run(go())
        self.assertEqual(first_open.reviewed_text, second_open.reviewed_text)
        self.assertEqual(first_open.review_status, second_open.review_status)
        self.assertEqual(len(first_open.status_history), len(second_open.status_history))


class AIGuardTests(unittest.TestCase):
    _FORBIDDEN = {"openai", "AsyncOpenAI", "OpenAIProvider", "providers",
                  "classification_consensus", "classification_prompts"}

    def _assert_clean(self, path: Path) -> None:
        tree = ast.parse(path.read_text(), filename=str(path))
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    self.assertNotIn(alias.name.split(".")[0], self._FORBIDDEN, f"{path}: {alias.name}")
            elif isinstance(node, ast.ImportFrom) and node.module:
                self.assertNotIn(node.module.split(".")[0], self._FORBIDDEN, f"{path}: {node.module}")

    def test_review_and_publication_services_have_no_ai_imports(self):
        root = Path(__file__).resolve().parents[1] / "src" / "agente_ia_edu" / "services"
        self._assert_clean(root / "question_extraction_service.py")
        self._assert_clean(root / "question_publication_service.py")

    def test_review_routes_have_no_ai_imports(self):
        path = Path(__file__).resolve().parents[1] / "src" / "agente_ia_edu" / "api" / "routes" / "question_extraction.py"
        self._assert_clean(path)


if __name__ == "__main__":
    unittest.main()
