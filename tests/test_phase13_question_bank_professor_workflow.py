"""PHASE 13 - backend HTTP tests for the Professor Question Bank workflow.

Exercises the *reused* PHASE 12 endpoints exactly as the Professor frontend does:
search/filter, server-side pagination (filters preserved across pages), preview a
single question, and validate/reorder a selection via /selections/preview.
Confirms classification-state preservation and that the answer key is never
serialised to the professor-facing response.

TestClient + in-memory SQLite (matches tests/test_bloco_c_http.py).
"""

from __future__ import annotations

import asyncio
import unittest

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    BookletQuestion,
    CatalogNode,
    Exam,
    ExamApplication,
    ExamBooklet,
    Institution,
    PedagogicalClassification,
    Question,
    QuestionOption,
    QuestionVersion,
)

AUTH = {"Authorization": "Bearer teacher:prof_mendes"}


def _node(session, code, name, node_type, parent=None):
    n = CatalogNode(code=code, name=name, node_type=node_type,
                    parent_id=parent.id if parent else None,
                    root_id=parent.root_id if parent else None, active=True)
    session.add(n)
    return n


async def _seed(factory: async_sessionmaker[AsyncSession]) -> dict:
    async with factory() as s:
        inst = Institution(code="INEP", name="INEP")
        s.add(inst)
        await s.flush()
        exam = Exam(institution_id=inst.id, code="ENEM", name="ENEM")
        s.add(exam)
        await s.flush()
        bio = _node(s, "BIOLOGY", "Biologia", "DISCIPLINE")
        s.add(bio)
        await s.flush()
        bio.root_id = bio.id
        bio_area = _node(s, "BIOLOGY-ANIMAL-PHYSIOLOGY", "Fisiologia Animal", "AREA", bio)
        s.add(bio_area)
        await s.flush()
        bio_area.root_id = bio.id
        bio_c = _node(s, "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS", "Adaptacoes", "CONTENT", bio_area)
        s.add(bio_c)
        await s.flush()
        bio_c.root_id = bio.id
        math = _node(s, "MATH", "Matematica", "DISCIPLINE")
        s.add(math)
        await s.flush()
        math.root_id = math.id
        math_area = _node(s, "MATH-ALGEBRA", "Algebra", "AREA", math)
        s.add(math_area)
        await s.flush()
        math_area.root_id = math.id
        math_c = _node(s, "MATH-ALGEBRA-FUNCTIONS", "Funcoes", "CONTENT", math_area)
        s.add(math_c)
        await s.flush()
        math_c.root_id = math.id

        made = {}
        booklets: dict[tuple[int, int], ExamBooklet] = {}
        # (year, num, day, classification-spec | None)
        plan = [
            (2024, 96, 2, None),
            (2024, 130, 2, ("MATH-ALGEBRA-FUNCTIONS", "CLASSIFIED", None)),
            (2025, 97, 2, ("BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS", "CLASSIFIED", None)),
            (2025, 129, 2, ("MATH-ALGEBRA-FUNCTIONS", "NEEDS_REVIEW", "FORCED_CLOSURE")),
            (2025, 150, 2, ("MATH-ALGEBRA-FUNCTIONS", "NEEDS_REVIEW", "FORCED_CLOSURE_VISUAL")),
            (2024, 40, 1, None),
        ]
        for year, num, day, spec in plan:
            bk = booklets.get((year, day))
            if bk is None:
                app = ExamApplication(exam_id=exam.id, year=year, application_type="regular", day=day)
                s.add(app)
                await s.flush()
                bk = ExamBooklet(exam_application_id=app.id, code=f"D{day}_CD{year}", color="AMARELO")
                s.add(bk)
                await s.flush()
                booklets[(year, day)] = bk
            q = Question(validation_status="validated", origin_type="IMPORTED",
                         status="PUBLISHED", visibility_scope="PUBLIC")
            s.add(q)
            await s.flush()
            v = QuestionVersion(
                question_id=q.id, version_kind="official_original",
                canonical_text=f"Enunciado oficial {year} Q{num}.",
                statement=f"Enunciado oficial {year} Q{num}.",
                content_hash=f"h-{year}-{num}", is_immutable=True,
                recommended_difficulty="MEDIUM" if num == 130 else None,
            )
            s.add(v)
            await s.flush()
            for pos, key in enumerate("ABCDE", start=1):
                s.add(QuestionOption(question_version_id=v.id, option_key=key, position=pos,
                                     text=f"Alternativa {key}", is_valid_option=(key == "C")))
            s.add(BookletQuestion(exam_booklet_id=bk.id, question_version_id=v.id,
                                  position=num, official_number=num, page_number=1))
            await s.flush()
            if spec is not None:
                code, status, mode = spec
                md = {"taxonomy_version": "curriculum-v2", "primary_content_code": code,
                      "evidence": [{"text": "trecho", "content_code": code, "reason": "fixture"}]}
                if mode and mode.startswith("FORCED_CLOSURE"):
                    md.update(closure_phase="11.34", classification_mode="FORCED_CLOSURE",
                              confidence="MEDIUM" if mode.endswith("VISUAL") else "LOW",
                              review_reason=mode)
                    if mode.endswith("VISUAL"):
                        md["visual_dependency"] = True
                s.add(PedagogicalClassification(
                    question_version_id=v.id, discipline="CURRICULUM_PROPOSAL", content=code,
                    subcontent=code, difficulty="UNKNOWN", reasoning_type="UNSPECIFIED",
                    prerequisites=[], keywords=[], competencies=[], skills=[], status=status,
                    source="rule", lifecycle="ACTIVE", model_version="fixture-v1",
                    prompt_version="v1", provider_name="fixture", metadata_=md))
            made[(year, num)] = {"question_id": str(q.id), "question_version_id": str(v.id)}
        await s.commit()
        return made


class Phase13ProfessorWorkflowTests(unittest.TestCase):
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
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    # -- listing + server-side pagination + filter preservation across pages --
    def test_list_paginates_server_side_and_keeps_filters(self):
        r = self.client.get("/api/v1/question-bank/questions",
                            params={"day": 2, "page": 1, "page_size": 2, "order_by": "official_number"},
                            headers=AUTH)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual(body["pagination"]["total"], 5)
        self.assertEqual(len(body["items"]), 2)
        self.assertEqual(body["pagination"]["total_pages"], 3)
        page1_ids = {i["question_version_id"] for i in body["items"]}

        r2 = self.client.get("/api/v1/question-bank/questions",
                             params={"day": 2, "page": 2, "page_size": 2, "order_by": "official_number"},
                             headers=AUTH)
        body2 = r2.json()
        self.assertEqual(body2["pagination"]["total"], 5)  # same filter -> same total
        self.assertFalse(page1_ids & {i["question_version_id"] for i in body2["items"]})
        # list rows are LIGHT: no options, no full body - only a preview
        row = body["items"][0]
        self.assertNotIn("options", row)
        self.assertNotIn("canonical_text", row)
        self.assertNotIn("statement", row)
        self.assertIn("statement_preview", row)

    def test_filters_are_server_side(self):
        def total(**params):
            return self.client.get("/api/v1/question-bank/questions",
                                   params={**params, "page_size": 50}, headers=AUTH).json()["pagination"]["total"]

        self.assertEqual(total(year=2025), 3)
        # deterministic ENEM area from official_number: 40 -> LC, 96/97/129/130 -> CN, 150 -> MT
        self.assertEqual(total(area="LC"), 1)
        self.assertEqual(total(area="CN"), 4)
        self.assertEqual(total(area="MT"), 1)
        self.assertEqual(total(has_classification="true"), 4)
        self.assertEqual(total(has_classification="false"), 2)
        self.assertEqual(total(classification_status="FORCED_CLOSURE"), 2)
        self.assertEqual(total(classification_status="NEEDS_REVIEW"), 2)
        self.assertEqual(total(classification_status="CLASSIFIED"), 2)
        self.assertEqual(total(discipline="MATH"), 3)
        self.assertEqual(total(visual_dependency="true"), 1)
        self.assertEqual(total(provisional_only="true"), 2)
        self.assertEqual(
            self.client.get("/api/v1/question-bank/questions",
                            params={"classification_status": "BOGUS"}, headers=AUTH).status_code, 422)

    # -- preview retrieves only the selected question, keeps official order, no answer key --
    def test_preview_single_question_official_order_no_answer_key(self):
        qid = self.made[(2024, 130)]["question_id"]
        r = self.client.get(f"/api/v1/question-bank/questions/{qid}", headers=AUTH)
        self.assertEqual(r.status_code, 200)
        q = r.json()
        self.assertEqual([o["key"] for o in q["options"]], ["A", "B", "C", "D", "E"])
        self.assertEqual([o["position"] for o in q["options"]], [1, 2, 3, 4, 5])
        blob = r.text.lower()
        self.assertNotIn("is_valid_option", blob)
        self.assertNotIn("answer_key", blob)
        for opt in q["options"]:
            self.assertNotIn("is_valid_option", opt)
        self.assertEqual(q["classification"]["content_code"], "MATH-ALGEBRA-FUNCTIONS")
        self.assertEqual(q["classification"]["discipline_code"], "MATH")
        self.assertEqual(q["statement"], "Enunciado oficial 2024 Q130.")
        self.assertEqual(
            self.client.get("/api/v1/question-bank/questions/00000000-0000-0000-0000-000000000000",
                            headers=AUTH).status_code, 404)

    # -- classification state preserved across the professor-facing surface --
    def test_classification_states_preserved(self):
        def state_of(year, num):
            qid = self.made[(year, num)]["question_id"]
            return self.client.get(f"/api/v1/question-bank/questions/{qid}", headers=AUTH).json()

        self.assertEqual(state_of(2024, 96)["classification_state"], "UNCLASSIFIED")
        self.assertIsNone(state_of(2024, 96)["classification"])
        self.assertEqual(state_of(2025, 97)["classification_state"], "CLASSIFIED")
        fc = state_of(2025, 129)
        self.assertEqual(fc["classification_state"], "FORCED_CLOSURE")
        self.assertEqual(fc["classification"]["classification_mode"], "FORCED_CLOSURE")
        self.assertEqual(fc["classification"]["status"], "NEEDS_REVIEW")
        vis = state_of(2025, 150)
        self.assertTrue(vis["has_visual_dependency"])
        self.assertEqual(vis["classification"]["review_reason"], "FORCED_CLOSURE_VISUAL")
        self.assertEqual(vis["assets"], [])  # never fabricated

    # -- selection: order preserved, duplicates rejected, unknown rejected --
    def test_selection_preview_validates_and_orders(self):
        v97 = self.made[(2025, 97)]["question_version_id"]
        v130 = self.made[(2024, 130)]["question_version_id"]
        v96 = self.made[(2024, 96)]["question_version_id"]
        r = self.client.post("/api/v1/question-bank/selections/preview",
                             json={"question_version_ids": [v130, v96, v97], "source": "professor-workflow"},
                             headers=AUTH)
        self.assertEqual(r.status_code, 200)
        body = r.json()
        self.assertEqual([e["question_version_id"] for e in body["entries"]], [v130, v96, v97])
        self.assertEqual([e["position"] for e in body["entries"]], [1, 2, 3])
        self.assertEqual(body["count"], 3)
        self.assertEqual(body["source"], "professor-workflow")
        # reorder -> new order preserved exactly
        r2 = self.client.post("/api/v1/question-bank/selections/preview",
                              json={"question_version_ids": [v97, v130, v96]}, headers=AUTH)
        self.assertEqual([e["question_version_id"] for e in r2.json()["entries"]], [v97, v130, v96])
        # duplicate rejected
        self.assertEqual(self.client.post("/api/v1/question-bank/selections/preview",
                         json={"question_version_ids": [v97, v97]}, headers=AUTH).status_code, 422)
        # unknown rejected
        self.assertEqual(self.client.post("/api/v1/question-bank/selections/preview",
                         json={"question_version_ids": ["00000000-0000-0000-0000-000000000000"]},
                         headers=AUTH).status_code, 422)

    # -- full acceptance walk end to end --
    def test_acceptance_walk(self):
        # search (number) -> filter (day) -> open -> select multiple -> review order
        listed = self.client.get("/api/v1/question-bank/questions",
                                 params={"official_number": 130}, headers=AUTH).json()
        self.assertEqual(listed["pagination"]["total"], 1)
        picked = listed["items"][0]
        detail = self.client.get(
            f"/api/v1/question-bank/questions/{picked['question_id']}", headers=AUTH).json()
        self.assertIn("statement", detail)
        ids = [self.made[(2024, 130)]["question_version_id"],
               self.made[(2025, 97)]["question_version_id"]]
        final = self.client.post("/api/v1/question-bank/selections/preview",
                                 json={"question_version_ids": ids}, headers=AUTH).json()
        self.assertEqual([e["question_version_id"] for e in final["entries"]], ids)


if __name__ == "__main__":
    unittest.main()
