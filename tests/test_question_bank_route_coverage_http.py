"""
HTTP-level route-handler coverage for src/agente_ia_edu/api/routes/question_bank.py.

Mop-up wave of the overnight HTTP-layer coverage campaign (waves 1/2 committed as
703661f and 65ff11c). This file targets the remaining gaps in question_bank.py:
the exception -> HTTP-status-code mapping helpers (``_map_store_error`` /
``_map_assignment_error``) for branches reachable via a real HTTP call, the
PHASE 16 assignment endpoints' happy-paths that were never exercised end-to-end
(``list_assignments`` / ``get_assignment``), the PHASE 16 ``update_assignment``
clear/set branches, and the PHASE 19/20/21 manager-view 404 branches
(``results/analysis``, ``students/domain-map``, ``students/study-path``) for a
truly nonexistent assignment id, plus the PDF-export-unavailable 503 branch.

TestClient + in-memory SQLite, following the seed pattern proven in
tests/test_phase16_activity_assignment.py and tests/test_phase15_list_persistence_export.py.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid
from datetime import datetime, timezone
from unittest.mock import patch
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_authenticated_context, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    BookletQuestion,
    Exam,
    ExamApplication,
    ExamBooklet,
    Institution,
    Question,
    QuestionOption,
    QuestionVersion,
    SourceDocument,
)
from agente_ia_edu.identity import AuthenticatedUserContext

ANSWERS = {101: "C", 102: "A", 103: "E"}

_SCHOOL_UUIDS: dict[str, str] = {}


def _school_uuid(name: str | None) -> str | None:
    if name is None:
        return None
    return _SCHOOL_UUIDS.setdefault(
        name, str(_uuid.uuid5(_uuid.NAMESPACE_DNS, f"qb-route-coverage-school-{name}")))


def _ctx(user="prof_a", school="school-1", role="TEACHER"):
    return AuthenticatedUserContext(user_id=user, external_identity_id=user, role=role,
                                    school_id=_school_uuid(school), scope_type="SCHOOL")


async def _seed(factory) -> dict:
    async with factory() as s:
        inst = Institution(code="INEP", name="INEP")
        s.add(inst)
        await s.flush()
        exam = Exam(institution_id=inst.id, code="ENEM", name="ENEM")
        s.add(exam)
        await s.flush()
        app = ExamApplication(exam_id=exam.id, year=2024, application_type="regular", day=1)
        s.add(app)
        await s.flush()
        bk = ExamBooklet(exam_application_id=app.id, code="CAD", color="AZUL")
        s.add(bk)
        await s.flush()
        sd = SourceDocument(exam_application_id=app.id, exam_booklet_id=bk.id, document_type="ANSWER_KEY",
                            source_url="https://x/g.pdf", acquired_at=datetime.now(timezone.utc), content_hash="g")
        s.add(sd)
        await s.flush()
        rev = AnswerKeyRevision(source_document_id=sd.id, revision_number=1, is_official=True)
        s.add(rev)
        await s.flush()
        made = {}
        for num, correct in ANSWERS.items():
            q = Question(validation_status="validated", origin_type="IMPORTED",
                        status="PUBLISHED", visibility_scope="PUBLIC")
            s.add(q)
            await s.flush()
            v = QuestionVersion(question_id=q.id, version_kind="official_original",
                                canonical_text=f"Enunciado oficial Q{num}.",
                                statement=f"Enunciado oficial Q{num}.", content_hash=f"h{num}",
                                is_immutable=True)
            s.add(v)
            await s.flush()
            opts = {}
            for pos, key in enumerate("ABCDE", start=1):
                o = QuestionOption(question_version_id=v.id, option_key=key, position=pos,
                                   text=f"Alt {key}", is_valid_option=(key == correct))
                s.add(o)
                await s.flush()
                opts[key] = o
            bq = BookletQuestion(exam_booklet_id=bk.id, question_version_id=v.id,
                                 position=num, official_number=num, page_number=1)
            s.add(bq)
            await s.flush()
            s.add(AnswerKeyEntry(answer_key_revision_id=rev.id, booklet_question_id=bq.id,
                                 official_answer_label=correct, resolved_option_id=opts[correct].id,
                                 page_number=1))
            made[num] = {"question_id": str(q.id), "question_version_id": str(v.id)}
        await s.commit()
        return made


class QuestionBankRouteCoverageHTTP(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _seed(cls.factory)

        cls.made = cls.loop.run_until_complete(_prep())
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls._ctx = _ctx()
        cls.app.dependency_overrides[get_current_authenticated_context] = lambda: cls._ctx
        cls.client = TestClient(cls.app)
        cls.vids = [cls.made[n]["question_version_id"] for n in (101, 102, 103)]

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def _as(self, ctx):
        self.__class__._ctx = ctx
        self.app.dependency_overrides[get_current_authenticated_context] = lambda: ctx

    def _create_list(self, ids=None, title="Lista", owner="prof_a", school="school-1", **kw):
        self._as(_ctx(owner, school))
        body = {"question_version_ids": ids or self.vids, "title": title,
                "answer_key_presentation": kw.pop("answer_key_presentation", "KEY_AT_END"), **kw}
        r = self.client.post("/api/v1/question-bank/lists", json=body)
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    def _published_list(self, ids=None, title="Lista Pub", owner="prof_a", school="school-1", **kw):
        lid = self._create_list(ids, title=title, owner=owner, school=school, **kw)
        self._as(_ctx(owner, school))
        f = self.client.post(f"/api/v1/question-bank/lists/{lid}/finalize")
        self.assertEqual(f.status_code, 200, f.text)
        return lid

    def _assign(self, lid, target_type="CLASS", target_id="turma-x", owner="prof_a", school="school-1", **kw):
        self._as(_ctx(owner, school))
        r = self.client.post(f"/api/v1/question-bank/lists/{lid}/assignments",
                             json={"target_type": target_type, "target_id": target_id, **kw})
        self.assertEqual(r.status_code, 201, r.text)
        return r.json()["id"]

    # ------------------------------------------------------------------
    # _map_store_error via finalize_list (404 / 403)
    # ------------------------------------------------------------------

    def test_finalize_nonexistent_list_returns_404(self):
        self._as(_ctx("prof_fin404", "school-1"))
        r = self.client.post(f"/api/v1/question-bank/lists/{uuid4()}/finalize")
        self.assertEqual(r.status_code, 404)

    def test_finalize_by_non_owner_returns_403(self):
        lid = self._create_list(title="Owned by A", owner="prof_fin_owner", school="school-finA")
        # a teacher in a different school, not the owner, not privileged
        self._as(_ctx("prof_fin_other", "school-finB"))
        r = self.client.post(f"/api/v1/question-bank/lists/{lid}/finalize")
        self.assertEqual(r.status_code, 403)

    # ------------------------------------------------------------------
    # _export_model / _map_store_error via export.pdf + export.docx (404 / 403)
    # ------------------------------------------------------------------

    def test_export_pdf_nonexistent_list_returns_404(self):
        self._as(_ctx("prof_exp404", "school-1"))
        r = self.client.get(f"/api/v1/question-bank/lists/{uuid4()}/export.pdf")
        self.assertEqual(r.status_code, 404)

    def test_export_docx_by_unauthorized_user_returns_403(self):
        lid = self._published_list(title="Export Owned", owner="prof_exp_owner", school="school-expA")
        self._as(_ctx("prof_exp_other", "school-expB"))
        r = self.client.get(f"/api/v1/question-bank/lists/{lid}/export.docx")
        self.assertEqual(r.status_code, 403)

    def test_export_pdf_returns_503_when_pymupdf_unavailable(self):
        lid = self._published_list(title="No PDF Engine", owner="prof_exp_engine", school="school-1")
        self._as(_ctx("prof_exp_engine", "school-1"))
        with patch("agente_ia_edu.api.routes.question_bank.pdf_available", return_value=False):
            r = self.client.get(f"/api/v1/question-bank/lists/{lid}/export.pdf")
        self.assertEqual(r.status_code, 503)
        self.assertIn("pymupdf", r.json()["detail"])

    # ------------------------------------------------------------------
    # list_assignments happy path (200 + summary) - previously untested end-to-end
    # ------------------------------------------------------------------

    def test_list_assignments_happy_path_returns_summary(self):
        lid = self._published_list(title="Distrib", owner="prof_la", school="school-la")
        self._assign(lid, "CLASS", "turma-la-1", owner="prof_la", school="school-la")
        self._assign(lid, "STUDENT", "aluno-la-1", owner="prof_la", school="school-la")
        self._as(_ctx("prof_la", "school-la"))
        r = self.client.get(f"/api/v1/question-bank/lists/{lid}/assignments")
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertEqual(len(body["items"]), 2)
        self.assertEqual(body["summary"]["count"], 2)
        self.assertEqual(body["summary"]["active"], 2)
        self.assertIsNotNone(body["summary"]["last_distributed_at"])

    def test_list_assignments_nonexistent_list_returns_404(self):
        self._as(_ctx("prof_la404", "school-1"))
        r = self.client.get(f"/api/v1/question-bank/lists/{uuid4()}/assignments")
        self.assertEqual(r.status_code, 404)

    # ------------------------------------------------------------------
    # get_assignment happy path (200) + 404 - previously untested end-to-end
    # ------------------------------------------------------------------

    def test_get_assignment_happy_path_returns_200(self):
        lid = self._published_list(title="GetA", owner="prof_ga", school="school-ga")
        aid = self._assign(lid, "CLASS", "turma-ga-1", owner="prof_ga", school="school-ga")
        self._as(_ctx("prof_ga", "school-ga"))
        r = self.client.get(f"/api/v1/question-bank/assignments/{aid}")
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["id"], aid)
        self.assertEqual(r.json()["target_id"], "turma-ga-1")

    def test_get_assignment_nonexistent_returns_404(self):
        self._as(_ctx("prof_ga404", "school-1"))
        r = self.client.get(f"/api/v1/question-bank/assignments/{uuid4()}")
        self.assertEqual(r.status_code, 404)

    # ------------------------------------------------------------------
    # update_assignment: clear_available_from / available_from / clear_due_at / due_at
    # ------------------------------------------------------------------

    def test_update_assignment_clear_available_from_and_set_due_at(self):
        lid = self._published_list(title="UpdA", owner="prof_ua", school="school-ua")
        aid = self._assign(lid, "CLASS", "turma-ua-1", owner="prof_ua", school="school-ua",
                           available_from="2020-01-01T00:00:00Z")
        self._as(_ctx("prof_ua", "school-ua"))
        r = self.client.patch(f"/api/v1/question-bank/assignments/{aid}",
                              json={"clear_available_from": True, "due_at": "2030-01-01T00:00:00Z"})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertIsNone(body["available_from"])
        self.assertTrue(body["due_at"].startswith("2030-01-01"))

    def test_update_assignment_set_available_from_and_clear_due_at(self):
        lid = self._published_list(title="UpdB", owner="prof_ub", school="school-ub")
        aid = self._assign(lid, "CLASS", "turma-ub-1", owner="prof_ub", school="school-ub",
                           due_at="2030-01-01T00:00:00Z")
        self._as(_ctx("prof_ub", "school-ub"))
        r = self.client.patch(f"/api/v1/question-bank/assignments/{aid}",
                              json={"available_from": "2020-06-01T00:00:00Z", "clear_due_at": True})
        self.assertEqual(r.status_code, 200, r.text)
        body = r.json()
        self.assertTrue(body["available_from"].startswith("2020-06-01"))
        self.assertIsNone(body["due_at"])

    # ------------------------------------------------------------------
    # PHASE 19/20/21 manager-view 404 branches for a truly nonexistent assignment
    # ------------------------------------------------------------------

    def test_results_analysis_nonexistent_assignment_returns_404(self):
        self._as(_ctx("prof_an404", "school-1"))
        r = self.client.get(f"/api/v1/question-bank/assignments/{uuid4()}/results/analysis")
        self.assertEqual(r.status_code, 404)
        self.assertIn("not found", r.json()["detail"].lower())

    def test_domain_map_nonexistent_assignment_returns_404(self):
        self._as(_ctx("prof_dm404", "school-1"))
        r = self.client.get(f"/api/v1/question-bank/assignments/{uuid4()}/students/domain-map")
        self.assertEqual(r.status_code, 404)
        self.assertIn("not found", r.json()["detail"].lower())

    def test_study_path_nonexistent_assignment_returns_404(self):
        self._as(_ctx("prof_sp404", "school-1"))
        r = self.client.get(f"/api/v1/question-bank/assignments/{uuid4()}/students/study-path")
        self.assertEqual(r.status_code, 404)
        self.assertIn("not found", r.json()["detail"].lower())


if __name__ == "__main__":
    unittest.main()
