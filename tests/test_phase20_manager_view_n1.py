"""PHASE 30 perf audit - N+1 check for CurriculumDomainMapService.manager_view.

manager_view() returns the curriculum-v2 Domain Map of every student with a
corrected result under one assignment - the exact "whole classroom" shape a
teacher/coordinator report needs. It reuses the same seed fixtures as
tests/test_phase20_domain_map.py (in-memory SQLite, TestClient) but drives a
SHARED assignment across many students to prove/disprove an N+1: the query
count for manager_view must stay ~constant as the student count grows, not
scale linearly.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid

from fastapi.testclient import TestClient
from sqlalchemy import event
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_authenticated_context, get_session_factory
from agente_ia_edu.db.base import Base

from test_phase20_domain_map import KEYS, _ctx, _seed  # noqa: E402  (sibling test module)


class ManagerViewNPlus1Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _seed(cls.factory)

        cls.vids = cls.loop.run_until_complete(_prep())
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls._ctx = _ctx()
        cls.app.dependency_overrides[get_current_authenticated_context] = lambda: cls._ctx
        cls.client = TestClient(cls.app)
        cls.correct_key = {vid: KEYS[(i + 1) % 5] for i, vid in enumerate(cls.vids)}

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    # -- helpers ---------------------------------------------------

    def _as(self, ctx):
        self.__class__._ctx = ctx
        self.app.dependency_overrides[get_current_authenticated_context] = lambda: ctx

    def _link(self, uid, classroom, school="school-1"):
        from agente_ia_edu.db.models.admin import UserSchoolLink

        async def _do():
            async with self.factory() as s:
                s.add(UserSchoolLink(external_user_id=uid, school_id=_uuid.UUID(_school_uuid(school)),
                                     role="STUDENT", scope_type="CLASSROOM",
                                     scope_external_id=classroom, active=True))
                await s.commit()
        self.loop.run_until_complete(_do())

    def _create_assignment(self, indices, *, classroom, owner="prof_a", school="school-1"):
        ids = [self.vids[i] for i in indices]
        self._as(_ctx(owner, school))
        lid = self.client.post("/api/v1/question-bank/lists", json={
            "question_version_ids": ids, "title": f"MV {classroom}",
            "answer_key_presentation": "KEY_AT_END"}).json()["id"]
        self.client.post(f"/api/v1/question-bank/lists/{lid}/finalize")
        return self.client.post(f"/api/v1/question-bank/lists/{lid}/assignments", json={
            "target_type": "CLASS", "target_id": classroom,
            "available_from": "2000-01-01T00:00:00Z"}).json()["id"]

    def _run_shared(self, aid, indices, *, student, classroom, school="school-1"):
        ids = [self.vids[i] for i in indices]
        self._link(student, classroom, school=school)
        self._as(_ctx(student, school, role="STUDENT"))
        self.client.post(f"/api/v1/student/activities/{aid}/attempt")
        for pos, vid in enumerate(ids):
            ck = self.correct_key[vid]
            key = ck if pos % 2 == 0 else next(k for k in KEYS if k != ck)
            self.client.put(f"/api/v1/student/activities/{aid}/attempt/answers/{vid}",
                            json={"selected_option": key})
        self.client.post(f"/api/v1/student/activities/{aid}/attempt/complete")
        self.client.post(f"/api/v1/student/activities/{aid}/attempt/correct")

    def _count_manager_view_queries(self, aid) -> int:
        n = {"c": 0}

        @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
        def _c(*_a):  # noqa: ANN001
            n["c"] += 1
        try:
            self._as(_ctx("prof_a", "school-1"))
            resp = self.client.get(f"/api/v1/question-bank/assignments/{aid}/students/domain-map")
            self.assertEqual(resp.status_code, 200, resp.text)
            return n["c"], resp.json()
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", _c)

    # -- the actual check --------------------------------------

    def test_manager_view_query_count_does_not_scale_with_student_count(self):
        # small classroom: 3 students on a shared assignment
        aid_small = self._create_assignment(list(range(0, 10)), classroom="t-mv-small")
        for i in range(3):
            self._run_shared(aid_small, list(range(0, 10)), student=f"s_mv_small_{i}",
                             classroom="t-mv-small")
        q_small, j_small = self._count_manager_view_queries(aid_small)
        self.assertEqual(j_small["student_count"], 3)

        # larger classroom: 20 students on a shared assignment (same shape)
        aid_big = self._create_assignment(list(range(0, 10)), classroom="t-mv-big")
        for i in range(20):
            self._run_shared(aid_big, list(range(0, 10)), student=f"s_mv_big_{i}",
                             classroom="t-mv-big")
        q_big, j_big = self._count_manager_view_queries(aid_big)
        self.assertEqual(j_big["student_count"], 20)

        # N+1 signature: query count grows ~linearly with student count.
        # A batched implementation should cost only a few MORE queries total
        # (not per student) going from 3 -> 20 students.
        growth = q_big - q_small
        self.assertLess(
            growth, 15,
            f"query count grew by {growth} going from 3 to 20 students "
            f"({q_small} -> {q_big}) - looks like an N+1 in manager_view()",
        )


def _school_uuid(name):
    from test_phase20_domain_map import _school_uuid as impl
    return impl(name)


if __name__ == "__main__":
    unittest.main()
