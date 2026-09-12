"""PHASE 23 - Content & Question Bank Foundation backend tests.

TestClient + in-memory SQLite. PHASE 23 REUSES the existing authored-material
stack (TheoryMaterial / TheoryMaterialVersion / MaterialSection /
MaterialExercise / TheoryMaterialService) and adds only the 4th structural level
(MaterialBlock), a structured section<->curriculum association, a pedagogical
relation_type on the material<->question link, and the deterministic
material_available / material_count contract. Questions are NEVER copied - only
referenced by question_version_id. ZERO AI.

Covers spec s23: create / edit / versioning; tenant + owner authorization;
sections; blocks; curriculum-v2 association by stable code; material<->question;
link removal; selection without duplication; missing material / question /
content_code; determinism; idempotency; pagination; no N+1; AI-agnostic; DB
integrity (official questions untouched).
"""

from __future__ import annotations

import asyncio
import importlib
import unittest
import uuid as _uuid

from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import app
from agente_ia_edu.api.dependencies import get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode, MaterialBlock, MaterialExercise, MaterialSection, Question,
    QuestionOption, QuestionVersion, School, TheoryMaterial, TheoryMaterialVersion,
)
from agente_ia_edu.services.catalog import TheoryMaterialService
from agente_ia_edu.services.material_availability import MaterialAvailabilityService

KEYS = "ABCDE"


def _auth(user: str) -> dict:
    return {"Authorization": f"Bearer teacher:{user}"}


async def _seed(factory) -> dict:
    async with factory() as s:
        d = CatalogNode(code="MATH", name="Matemática", node_type="DISCIPLINE", active=True)
        s.add(d); await s.flush(); d.root_id = d.id
        a = CatalogNode(code="MATH-A", name="Área", node_type="AREA", parent_id=d.id, root_id=d.id, active=True)
        s.add(a); await s.flush()
        c1 = CatalogNode(code="MATH-ALGEBRA-FUNCTIONS", name="Funções", node_type="CONTENT",
                         parent_id=a.id, root_id=d.id, position=1, active=True)
        c2 = CatalogNode(code="MATH-GEOMETRY-AREA", name="Áreas", node_type="CONTENT",
                         parent_id=a.id, root_id=d.id, position=2, active=True)
        c_inactive = CatalogNode(code="MATH-DEAD", name="Inativo", node_type="CONTENT",
                                 parent_id=a.id, root_id=d.id, position=3, active=False)
        s.add_all([c1, c2, c_inactive]); await s.flush()

        qvids = []
        for i in range(3):
            q = Question(validation_status="validated", origin_type="IMPORTED",
                         status="PUBLISHED", visibility_scope="PUBLIC")
            s.add(q); await s.flush()
            v = QuestionVersion(question_id=q.id, version_kind="official_original",
                                canonical_text=f"e{i}", statement=f"e{i}", content_hash=f"h{i}",
                                is_immutable=True)
            s.add(v); await s.flush()
            for pos, k in enumerate(KEYS, start=1):
                s.add(QuestionOption(question_version_id=v.id, option_key=k, position=pos,
                                     text=f"Alt {k}", is_valid_option=(k == "A")))
            qvids.append(str(v.id))
        await s.commit()
        return {"content": "MATH-ALGEBRA-FUNCTIONS", "content2": "MATH-GEOMETRY-AREA",
                "inactive": "MATH-DEAD", "qvids": qvids}


class Phase23Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            return await _seed(cls.factory)

        cls.seed = cls.loop.run_until_complete(_prep())
        app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls.client = TestClient(app)

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    # -- helpers ---------------------------------------------------------
    def _create(self, user="prof_a", **over):
        body = {"title": "Apostila A", "material_kind": "WORKBOOK",
                "authoring_source": "TEACHER", "visibility_scope": "PRIVATE"}
        body.update(over)
        return self.client.post("/api/v1/catalog/materials", json=body, headers=_auth(user))

    # -- 1  create + auto draft version --------------------------------
    def test_create_material_has_draft_version(self):
        r = self._create(title="Apostila 1")
        self.assertEqual(r.status_code, 201, r.text)
        m = r.json()
        self.assertEqual(m["material_kind"], "WORKBOOK")
        self.assertEqual(m["authoring_source"], "TEACHER")
        self.assertEqual(m["visibility_scope"], "PRIVATE")
        self.assertEqual(m["latest_version_status"], "DRAFT")
        self.assertEqual(m["latest_version_number"], 1)
        self.assertEqual(m["curriculum_status"], "UNMAPPED")
        self.assertEqual((m["section_count"], m["block_count"], m["question_count"]), (0, 0, 0))

    # -- 2  PATCH identity fields ------------------------------------
    def test_patch_material(self):
        mid = self._create(title="Apostila 2").json()["id"]
        r = self.client.patch(f"/api/v1/catalog/materials/{mid}",
                              json={"description": "revisada", "visibility_scope": "SCHOOL",
                                    "material_kind": "CHAPTER"}, headers=_auth("prof_a"))
        self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["description"], "revisada")
        self.assertEqual(r.json()["visibility_scope"], "SCHOOL")
        self.assertEqual(r.json()["material_kind"], "CHAPTER")

    # -- 3  sections + curriculum-v2 association by stable code ------
    def test_sections_and_curriculum(self):
        mid = self._create(title="Apostila 3").json()["id"]
        r = self.client.post(f"/api/v1/catalog/materials/{mid}/sections", headers=_auth("prof_a"),
                             json={"section_type": "CHAPTER", "position": 1, "title": "Cap 1",
                                   "content_code": self.seed["content"],
                                   "curriculum_relation_type": "THEORY"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["content_code"], self.seed["content"])
        self.assertEqual(r.json()["curriculum_status"], "MAPPED")
        self.assertEqual(r.json()["curriculum_relation_type"], "THEORY")
        # a section with no code is UNMAPPED (never auto-creates a node)
        r2 = self.client.post(f"/api/v1/catalog/materials/{mid}/sections", headers=_auth("prof_a"),
                              json={"section_type": "SECTION", "position": 2})
        self.assertEqual(r2.json()["curriculum_status"], "UNMAPPED")
        # unknown / inactive content_code -> 422, no node invented
        bad = self.client.post(f"/api/v1/catalog/materials/{mid}/sections", headers=_auth("prof_a"),
                               json={"section_type": "SECTION", "position": 3, "content_code": "NOPE"})
        self.assertEqual(bad.status_code, 422)
        dead = self.client.post(f"/api/v1/catalog/materials/{mid}/sections", headers=_auth("prof_a"),
                                json={"section_type": "SECTION", "position": 4,
                                      "content_code": self.seed["inactive"]})
        self.assertEqual(dead.status_code, 422)
        lst = self.client.get(f"/api/v1/catalog/materials/{mid}/sections", headers=_auth("prof_a")).json()
        self.assertEqual(len(lst), 2)

    # -- 4  blocks (4th structural level) ---------------------------
    def test_blocks(self):
        mid = self._create(title="Apostila 4").json()["id"]
        sid = self.client.post(f"/api/v1/catalog/materials/{mid}/sections", headers=_auth("prof_a"),
                               json={"section_type": "CHAPTER", "position": 1}).json()["id"]
        for i, bt in enumerate(["HEADING", "TEXT", "FORMULA", "SOLVED_EXAMPLE"], start=1):
            rb = self.client.post(
                f"/api/v1/catalog/materials/{mid}/sections/{sid}/blocks", headers=_auth("prof_a"),
                json={"block_type": bt, "position": i, "title": bt, "body": f"corpo {i}"})
            self.assertEqual(rb.status_code, 201, rb.text)
        blocks = self.client.get(
            f"/api/v1/catalog/materials/{mid}/sections/{sid}/blocks", headers=_auth("prof_a")).json()
        self.assertEqual([b["block_type"] for b in blocks], ["HEADING", "TEXT", "FORMULA", "SOLVED_EXAMPLE"])
        detail = self.client.get(f"/api/v1/catalog/materials/{mid}", headers=_auth("prof_a")).json()
        self.assertEqual(detail["material"]["block_count"], 4)
        self.assertEqual(detail["material"]["section_count"], 1)

    # -- 5  material <-> question: reference only, no duplication ----
    def test_link_questions_no_duplication(self):
        async def _q_counts():
            async with self.factory() as s:
                return (
                    int(await s.scalar(select(func.count()).select_from(Question))),
                    int(await s.scalar(select(func.count()).select_from(QuestionVersion))),
                    int(await s.scalar(select(func.count()).select_from(QuestionOption))),
                )

        before = self.loop.run_until_complete(_q_counts())
        mid = self._create(title="Apostila 5").json()["id"]
        r = self.client.post(f"/api/v1/catalog/materials/{mid}/questions", headers=_auth("prof_a"),
                             json={"question_version_id": self.seed["qvids"][0], "relation_type": "EXERCISE"})
        self.assertEqual(r.status_code, 201, r.text)
        self.assertEqual(r.json()["question_version_id"], self.seed["qvids"][0])
        self.assertEqual(r.json()["relation_type"], "EXERCISE")
        r2 = self.client.post(f"/api/v1/catalog/materials/{mid}/questions", headers=_auth("prof_a"),
                              json={"question_version_id": self.seed["qvids"][1], "relation_type": "EXAMPLE"})
        self.assertEqual(r2.status_code, 201)
        # duplicate link -> 409 (idempotent guard, never a second copy)
        dup = self.client.post(f"/api/v1/catalog/materials/{mid}/questions", headers=_auth("prof_a"),
                               json={"question_version_id": self.seed["qvids"][0]})
        self.assertEqual(dup.status_code, 409)
        # unknown question_version -> 404
        miss = self.client.post(f"/api/v1/catalog/materials/{mid}/questions", headers=_auth("prof_a"),
                                json={"question_version_id": str(_uuid.uuid4())})
        self.assertEqual(miss.status_code, 404)
        # no question/version/option row was created by any of the above
        self.assertEqual(self.loop.run_until_complete(_q_counts()), before)
        qs = self.client.get(f"/api/v1/catalog/materials/{mid}/questions", headers=_auth("prof_a")).json()
        self.assertEqual(len(qs), 2)
        # remove one link - the question itself is untouched
        rm = self.client.delete(
            f"/api/v1/catalog/materials/{mid}/questions/{self.seed['qvids'][0]}", headers=_auth("prof_a"))
        self.assertEqual(rm.status_code, 204)
        self.assertEqual(self.loop.run_until_complete(_q_counts()), before)
        rm_again = self.client.delete(
            f"/api/v1/catalog/materials/{mid}/questions/{self.seed['qvids'][0]}", headers=_auth("prof_a"))
        self.assertEqual(rm_again.status_code, 404)

    # -- 6  tenant / owner authorization ---------------------------
    def test_owner_isolation(self):
        mid = self._create(user="prof_owner", title="Apostila 6").json()["id"]
        # another teacher cannot read or mutate an unscoped material they did not create
        self.assertEqual(self.client.get(f"/api/v1/catalog/materials/{mid}",
                                         headers=_auth("prof_intruder")).status_code, 403)
        self.assertEqual(self.client.patch(f"/api/v1/catalog/materials/{mid}", json={"title": "x"},
                                           headers=_auth("prof_intruder")).status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/catalog/materials/{mid}/sections",
                                          json={"section_type": "S", "position": 1},
                                          headers=_auth("prof_intruder")).status_code, 403)
        self.assertEqual(self.client.post(f"/api/v1/catalog/materials/{mid}/questions",
                                          json={"question_version_id": self.seed["qvids"][0]},
                                          headers=_auth("prof_intruder")).status_code, 403)

    def test_school_scoped_isolation_service(self):
        async def _run():
            async with self.factory() as s:
                sa = School(name="A", code="SA"); sb = School(name="B", code="SB")
                s.add_all([sa, sb]); await s.flush()
                svc = TheoryMaterialService()
                ma = await svc.create_material(s, title="Escola A", created_by_external_identity="t:a",
                                               school_id=sa.id)
                mb = await svc.create_material(s, title="Escola B", created_by_external_identity="t:b",
                                               school_id=sb.id)
                await s.commit()
                return ma.school_id, mb.school_id, sa.id, sb.id
        a_school, b_school, sa_id, sb_id = self.loop.run_until_complete(_run())
        self.assertEqual(a_school, sa_id)
        self.assertEqual(b_school, sb_id)
        self.assertNotEqual(a_school, b_school)

    # -- 7  versioning: published version is immutable -------------
    def test_published_version_is_immutable(self):
        mid = self._create(title="Apostila 7").json()["id"]
        for action in ("submit", "approve", "publish"):
            r = self.client.post(f"/api/v1/catalog/materials/{mid}/review",
                                 json={"action": action}, headers=_auth("prof_a"))
            self.assertEqual(r.status_code, 200, r.text)
        self.assertEqual(r.json()["status"], "PUBLISHED")
        blocked = self.client.post(f"/api/v1/catalog/materials/{mid}/sections",
                                   json={"section_type": "CHAPTER", "position": 1}, headers=_auth("prof_a"))
        self.assertEqual(blocked.status_code, 422)
        # a NEW draft version can still be created (non-destructive editing)
        nv = self.client.post(f"/api/v1/catalog/materials/{mid}/versions",
                              json={"introduction": "v2"}, headers=_auth("prof_a"))
        self.assertEqual(nv.status_code, 201)
        self.assertEqual(nv.json()["version_number"], 2)
        self.assertEqual(nv.json()["status"], "DRAFT")

    # -- 8  material_available / material_count contract ----------
    def test_material_availability_contract(self):
        async def _avail(codes):
            async with self.factory() as s:
                res = await MaterialAvailabilityService(s).for_content_codes(codes)
                return {k: (v.material_available, v.material_count) for k, v in res.items()}

        # nothing published yet for content2
        self.assertEqual(self.loop.run_until_complete(_avail([self.seed["content2"]])),
                         {self.seed["content2"]: (False, 0)})
        mid = self._create(title="Apostila 8", primary_content_node_id=None).json()["id"]
        # map it to content2 at material level, then publish
        # resolve node id
        async def _node_id(code):
            async with self.factory() as s:
                return str((await s.execute(
                    select(CatalogNode.id).where(CatalogNode.code == code))).scalar_one())
        nid = self.loop.run_until_complete(_node_id(self.seed["content2"]))
        self.client.patch(f"/api/v1/catalog/materials/{mid}",
                          json={"primary_content_node_id": nid}, headers=_auth("prof_a"))
        for action in ("submit", "approve", "publish"):
            self.client.post(f"/api/v1/catalog/materials/{mid}/review",
                             json={"action": action}, headers=_auth("prof_a"))
        self.assertEqual(self.loop.run_until_complete(_avail([self.seed["content2"]])),
                         {self.seed["content2"]: (True, 1)})
        # deterministic + idempotent: same call, same answer
        self.assertEqual(self.loop.run_until_complete(_avail([self.seed["content2"]])),
                         {self.seed["content2"]: (True, 1)})
        r = self.client.get(f"/api/v1/catalog/content-materials?content_code={self.seed['content2']}",
                            headers=_auth("prof_a"))
        self.assertEqual(r.status_code, 200)
        self.assertEqual(r.json(), {"content_code": self.seed["content2"],
                                    "material_available": True, "material_count": 1})

    # -- 9  missing material -> 404 -------------------------------
    def test_missing_material(self):
        rid = str(_uuid.uuid4())
        self.assertEqual(self.client.get(f"/api/v1/catalog/materials/{rid}",
                                         headers=_auth("prof_a")).status_code, 404)
        self.assertEqual(self.client.post(f"/api/v1/catalog/materials/{rid}/sections",
                                          json={"section_type": "S", "position": 1},
                                          headers=_auth("prof_a")).status_code, 404)

    # -- 10  list + pagination-shape + no N+1 -------------------
    def test_list_is_batched(self):
        for i in range(6):
            mid = self._create(user="prof_list", title=f"Bulk {i}").json()["id"]
            sid = self.client.post(f"/api/v1/catalog/materials/{mid}/sections", headers=_auth("prof_list"),
                                   json={"section_type": "CHAPTER", "position": 1}).json()["id"]
            self.client.post(f"/api/v1/catalog/materials/{mid}/sections/{sid}/blocks",
                             headers=_auth("prof_list"),
                             json={"block_type": "TEXT", "position": 1})
            self.client.post(f"/api/v1/catalog/materials/{mid}/questions", headers=_auth("prof_list"),
                             json={"question_version_id": self.seed["qvids"][i % 3]})

        n = {"c": 0}

        @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
        def _c(*_a):  # noqa: ANN001
            n["c"] += 1
        try:
            r = self.client.get("/api/v1/catalog/materials", headers=_auth("prof_list"))
        finally:
            event.remove(self.engine.sync_engine, "before_cursor_execute", _c)
        self.assertEqual(r.status_code, 200)
        rows = [m for m in r.json() if m["title"].startswith("Bulk ")]
        self.assertEqual(len(rows), 6)
        for m in rows:
            self.assertEqual((m["section_count"], m["block_count"], m["question_count"]), (1, 1, 1))
        # the list endpoint must not scale its query count with the number of
        # materials: a fixed batched set (auth + list + versions + 3 grouped
        # counts + codes). Allow generous headroom but reject linear growth.
        self.assertLessEqual(n["c"], 20, f"materials list is not batched: {n['c']} queries for 6+ materials")

    # -- 11  AI-agnostic import guard --------------------------
    def test_modules_are_ai_agnostic(self):
        for modname in ("agente_ia_edu.services.material_availability",
                        "agente_ia_edu.services.catalog"):
            mod = importlib.import_module(modname)
            src = importlib.util.find_spec(mod.__name__).origin
            with open(src, encoding="utf-8") as fh:
                body = fh.read()
            for banned in ("openai", "AsyncOpenAI", "OpenAIProvider", "build_text_provider",
                           "classification_consensus", "classification_prompts"):
                self.assertNotIn(banned, body, f"{modname} imports {banned}")

    # -- 12  official question tables untouched by the whole suite --
    def test_official_question_tables_untouched(self):
        async def _counts():
            async with self.factory() as s:
                return (
                    int(await s.scalar(select(func.count()).select_from(Question))),
                    int(await s.scalar(select(func.count()).select_from(QuestionVersion))),
                    int(await s.scalar(select(func.count()).select_from(QuestionOption))),
                )
        # 3 seeded questions x (1 version, 5 options) - nothing PHASE 23 does adds
        # or removes a question / version / option row.
        self.assertEqual(self.loop.run_until_complete(_counts()), (3, 3, 15))


if __name__ == "__main__":
    unittest.main()
