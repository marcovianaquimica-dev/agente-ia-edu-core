"""PHASE 14 - Question List / Exercise Generator backend tests.

TestClient + in-memory SQLite. Verifies the deterministic pipeline
QuestionSelection -> ListConfiguration -> GeneratedListDefinition against a seeded
bank with an authoritative official answer key, covering: valid/dup/invalid
selection, deterministic ordering, the three answer-key modes, both resolution
styles, official option order A-E, answer-key integrity (correct option matches
the official AnswerKeyEntry AND the option flag, and is unaffected by reorder),
no mutation of official data, and bounded (non-N+1) retrieval.
"""

from __future__ import annotations

import asyncio
import unittest

from fastapi.testclient import TestClient
from sqlalchemy import event, func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
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
    SourceDocument,
)
from agente_ia_edu.services.list_generator import (
    ANSWER_KEY_AND_RESOLUTION_AT_END,
    ANSWER_KEY_AT_END,
    ANSWER_KEY_NONE,
    ListConfiguration,
    ListGeneratorService,
)
from datetime import datetime, timezone

AUTH = {"Authorization": "Bearer teacher:prof_mendes"}
# deterministic correct answers per official number
ANSWERS = {130: "C", 131: "A", 132: "E", 133: "B", 96: "D"}


async def _seed(factory: async_sessionmaker[AsyncSession]) -> dict:
    async with factory() as s:
        inst = Institution(code="INEP", name="INEP")
        s.add(inst)
        await s.flush()
        exam = Exam(institution_id=inst.id, code="ENEM", name="ENEM")
        s.add(exam)
        await s.flush()
        math = CatalogNode(code="MATH", name="Matematica", node_type="DISCIPLINE", active=True)
        s.add(math)
        await s.flush()
        math.root_id = math.id
        area = CatalogNode(code="MATH-ALGEBRA", name="Algebra", node_type="AREA",
                           parent_id=math.id, root_id=math.id, active=True)
        s.add(area)
        await s.flush()
        content = CatalogNode(code="MATH-ALGEBRA-FUNCTIONS", name="Funcoes", node_type="CONTENT",
                              parent_id=area.id, root_id=math.id, active=True)
        s.add(content)
        await s.flush()

        app = ExamApplication(exam_id=exam.id, year=2024, application_type="regular", day=2)
        s.add(app)
        await s.flush()
        booklet = ExamBooklet(exam_application_id=app.id, code="D2_CD5", color="AMARELO")
        s.add(booklet)
        await s.flush()
        source_doc = SourceDocument(
            exam_application_id=app.id, exam_booklet_id=booklet.id, document_type="ANSWER_KEY",
            source_url="https://example/gab.pdf", acquired_at=datetime.now(timezone.utc),
            content_hash="gab-hash",
        )
        s.add(source_doc)
        await s.flush()
        revision = AnswerKeyRevision(source_document_id=source_doc.id, revision_number=1, is_official=True)
        s.add(revision)
        await s.flush()

        made = {}
        for num, correct in ANSWERS.items():
            q = Question(validation_status="validated", origin_type="IMPORTED",
                         status="PUBLISHED", visibility_scope="PUBLIC")
            s.add(q)
            await s.flush()
            v = QuestionVersion(
                question_id=q.id, version_kind="official_original",
                canonical_text=f"Enunciado oficial 2024 Q{num}. Texto imutável.",
                statement=f"Enunciado oficial 2024 Q{num}. Texto imutável.",
                content_hash=f"h-{num}", is_immutable=True,
            )
            s.add(v)
            await s.flush()
            opts = {}
            for pos, key in enumerate("ABCDE", start=1):
                o = QuestionOption(question_version_id=v.id, option_key=key, position=pos,
                                   text=f"Alternativa {key}", is_valid_option=(key == correct))
                s.add(o)
                await s.flush()
                opts[key] = o
            bq = BookletQuestion(exam_booklet_id=booklet.id, question_version_id=v.id,
                                 position=num, official_number=num, page_number=1)
            s.add(bq)
            await s.flush()
            s.add(AnswerKeyEntry(
                answer_key_revision_id=revision.id, booklet_question_id=bq.id,
                official_answer_label=correct, resolved_option_id=opts[correct].id, page_number=1,
            ))
            if num in (130, 132):
                s.add(PedagogicalClassification(
                    question_version_id=v.id, discipline="CURRICULUM_PROPOSAL",
                    content="MATH-ALGEBRA-FUNCTIONS", subcontent="MATH-ALGEBRA-FUNCTIONS",
                    difficulty="UNKNOWN", reasoning_type="UNSPECIFIED", prerequisites=[], keywords=[],
                    competencies=[], skills=[], status="CLASSIFIED", source="rule", lifecycle="ACTIVE",
                    model_version="fixture", prompt_version="v1",
                    metadata_={"taxonomy_version": "curriculum-v2",
                               "primary_content_code": "MATH-ALGEBRA-FUNCTIONS", "evidence": []}))
            made[num] = {"question_id": str(q.id), "question_version_id": str(v.id), "correct": correct}
        await s.commit()
        return made


class Phase14ListGeneratorTests(unittest.TestCase):
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
        cls.all_vids = [cls.made[n]["question_version_id"] for n in (130, 131, 132, 133, 96)]

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _gen(self, ids, **cfg):
        body = {"question_version_ids": ids, "title": cfg.pop("title", "Lista de exercícios"),
                "answer_key_presentation": cfg.pop("answer_key_presentation", "NONE"), **cfg}
        return self.client.post("/api/v1/question-bank/lists/generate", json=body, headers=AUTH)

    # -- config options --
    def test_config_options_lists_supported_and_reserved_modes(self):
        co = self.client.get("/api/v1/question-bank/lists/config-options", headers=AUTH).json()
        self.assertEqual(co["activity_modes"]["supported"], ["EXERCISE_LIST"])
        self.assertIn("SIMULADO", co["activity_modes"]["reserved_future"])
        # PHASE 31 - availability is now per-question (captured + approved
        # resolution), not a permanently blocked global flag.
        self.assertEqual(co["resolution_availability"], "PER_QUESTION")
        self.assertEqual([p["value"] for p in co["answer_key_presentations"]],
                         ["NONE", "KEY_AT_END", "KEY_AND_RESOLUTION_AT_END"])

    # -- valid selection + deterministic ordering --
    def test_deterministic_ordering_and_identity_preserved(self):
        order = [self.all_vids[i] for i in (3, 0, 4, 1, 2)]
        r = self._gen(order, answer_key_presentation="NONE")
        self.assertEqual(r.status_code, 200)
        d = r.json()
        self.assertEqual(d["question_count"], 5)
        self.assertEqual(d["question_version_ids"], order)
        self.assertEqual([i["question_version_id"] for i in d["items"]], order)
        self.assertEqual([i["position"] for i in d["items"]], [1, 2, 3, 4, 5])
        for it in d["items"]:
            self.assertEqual(it["source"], "ENEM")
            self.assertEqual(it["year"], 2024)
            self.assertIsNotNone(it["official_number"])
            self.assertTrue(it["statement"].startswith("Enunciado oficial"))
            self.assertEqual([o["key"] for o in it["options"]], ["A", "B", "C", "D", "E"])
            self.assertIsNone(it["answer_key"])  # NONE -> no key exposed
        # a second identical request is byte-stable
        self.assertEqual(self._gen(order).json()["selection_fingerprint"],
                         d["selection_fingerprint"])

    # -- rejections --
    def test_selection_validation(self):
        self.assertEqual(self._gen([self.all_vids[0], self.all_vids[0]]).status_code, 422)
        self.assertEqual(self._gen(["00000000-0000-0000-0000-000000000000"]).status_code, 422)
        self.assertEqual(self._gen([]).status_code, 422)  # pydantic min_length
        self.assertEqual(self._gen(self.all_vids, title="   ").status_code, 422)
        self.assertEqual(self._gen(self.all_vids, activity_mode="PROVA").status_code, 422)
        self.assertEqual(self._gen(self.all_vids, answer_key_presentation="BOGUS").status_code, 422)

    # -- answer-key modes --
    def test_answer_key_modes(self):
        none = self._gen(self.all_vids, answer_key_presentation=ANSWER_KEY_NONE).json()
        self.assertFalse(none["answer_key_included"])
        self.assertTrue(all(i["answer_key"] is None for i in none["items"]))

        key = self._gen(self.all_vids, answer_key_presentation=ANSWER_KEY_AT_END).json()
        self.assertTrue(key["answer_key_included"])
        self.assertFalse(key["resolution_included"])
        for it in key["items"]:
            ak = it["answer_key"]
            self.assertEqual(ak["source"], "official_answer_key")
            self.assertEqual(ak["correct_option_key"], ANSWERS[it["official_number"]])
            self.assertIsNone(ak["resolution"])

        both = self._gen(self.all_vids, answer_key_presentation=ANSWER_KEY_AND_RESOLUTION_AT_END,
                         resolution_style="STEP_BY_STEP").json()
        self.assertTrue(both["resolution_included"])
        for it in both["items"]:
            res = it["answer_key"]["resolution"]
            self.assertEqual(res["style"], "STEP_BY_STEP")
            self.assertFalse(res["available"])  # no official resolution exists -> unavailable, not invented
            self.assertIsNone(res["text"])
            self.assertIsNotNone(res["unavailable_reason"])

        summ = self._gen(self.all_vids, answer_key_presentation=ANSWER_KEY_AND_RESOLUTION_AT_END,
                         resolution_style="SUMMARY").json()
        self.assertEqual(summ["items"][0]["answer_key"]["resolution"]["style"], "SUMMARY")

    # -- ANSWER KEY INTEGRITY: correct option follows the official version, not the order --
    def test_answer_key_integrity_survives_reorder(self):
        forward = self._gen(self.all_vids, answer_key_presentation=ANSWER_KEY_AT_END).json()
        reverse = self._gen(self.all_vids[::-1], answer_key_presentation=ANSWER_KEY_AT_END).json()
        fwd = {i["question_version_id"]: i["answer_key"]["correct_option_key"] for i in forward["items"]}
        rev = {i["question_version_id"]: i["answer_key"]["correct_option_key"] for i in reverse["items"]}
        self.assertEqual(fwd, rev)  # identical mapping regardless of list order
        for it in forward["items"]:
            self.assertEqual(it["answer_key"]["correct_option_key"], ANSWERS[it["official_number"]])
            # options are always A..E ascending
            self.assertEqual([o["position"] for o in it["options"]], [1, 2, 3, 4, 5])

    async def _integrity_against_db(self):
        async with self.factory() as s:
            svc = ListGeneratorService(s)
            d = await svc.generate([__import__("uuid").UUID(v) for v in self.all_vids[::-1]],
                                   ListConfiguration(title="x", answer_key_presentation="KEY_AT_END"))
            for it in d.items:
                row = (await s.execute(select(
                    AnswerKeyEntry.official_answer_label, QuestionOption.option_key,
                ).join(BookletQuestion, BookletQuestion.id == AnswerKeyEntry.booklet_question_id)
                 .join(AnswerKeyRevision, AnswerKeyRevision.id == AnswerKeyEntry.answer_key_revision_id)
                 .join(QuestionOption, QuestionOption.question_version_id == BookletQuestion.question_version_id)
                 .where(BookletQuestion.question_version_id == __import__("uuid").UUID(it.question_version_id),
                        AnswerKeyRevision.is_official.is_(True),
                        QuestionOption.is_valid_option.is_(True)))).first()
                ake_label, flag_key = row
                assert it.answer_key.correct_option_key == ake_label == flag_key, (
                    it.official_number, it.answer_key.correct_option_key, ake_label, flag_key)

    def test_answer_key_matches_official_entry_and_option_flag(self):
        self.loop.run_until_complete(self._integrity_against_db())

    # -- no mutation of official data --
    def test_generation_never_mutates_official_data(self):
        async def counts():
            async with self.factory() as s:
                return {
                    "q": await s.scalar(select(func.count()).select_from(Question)),
                    "v": await s.scalar(select(func.count()).select_from(QuestionVersion)),
                    "o": await s.scalar(select(func.count()).select_from(QuestionOption)),
                    "ake": await s.scalar(select(func.count()).select_from(AnswerKeyEntry)),
                    "pc": await s.scalar(select(func.count()).select_from(PedagogicalClassification)),
                    "cn": await s.scalar(select(func.count()).select_from(CatalogNode)),
                }
        before = self.loop.run_until_complete(counts())
        for mode in ("NONE", "KEY_AT_END", "KEY_AND_RESOLUTION_AT_END"):
            self._gen(self.all_vids, answer_key_presentation=mode, resolution_style="SUMMARY")
        after = self.loop.run_until_complete(counts())
        self.assertEqual(before, after)

    # -- bounded retrieval: query count does not scale with N --
    def test_no_n_plus_1_in_selected_question_retrieval(self):
        async def run():
            n = {"c": 0}

            @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
            def _count(*_a):  # noqa: ANN001
                n["c"] += 1

            try:
                async with self.factory() as s:
                    svc = ListGeneratorService(s)
                    import uuid
                    n["c"] = 0
                    await svc.generate([uuid.UUID(v) for v in self.all_vids[:2]],
                                       ListConfiguration(title="x", answer_key_presentation="KEY_AT_END"))
                    q2 = n["c"]
                    n["c"] = 0
                    await svc.generate([uuid.UUID(v) for v in self.all_vids],
                                       ListConfiguration(title="x", answer_key_presentation="KEY_AT_END"))
                    q5 = n["c"]
                return q2, q5
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", _count)

        q2, q5 = self.loop.run_until_complete(run())
        # constant-ish (catalog cached on 2nd call); never proportional to N
        self.assertLessEqual(q5, q2 + 1)
        self.assertLessEqual(q5, 8)

    # -- PHASE 31 - resolution shown when QuestionVersion.resolution_text exists --
    def test_resolution_available_when_question_version_has_resolution_text(self):
        vid_with = self.made[130]["question_version_id"]
        vid_without = self.made[131]["question_version_id"]
        sample_text = "Passo 1: isolar a incógnita. Passo 2: substituir e concluir."

        async def _set(text):
            async with self.factory() as s:
                v = await s.get(QuestionVersion, __import__("uuid").UUID(vid_with))
                v.resolution_text = text
                await s.commit()

        self.loop.run_until_complete(_set(sample_text))
        try:
            d = self._gen([vid_with, vid_without],
                          answer_key_presentation=ANSWER_KEY_AND_RESOLUTION_AT_END,
                          resolution_style="STEP_BY_STEP").json()
            items = {i["question_version_id"]: i for i in d["items"]}

            res_with = items[vid_with]["answer_key"]["resolution"]
            self.assertTrue(res_with["available"])
            self.assertEqual(res_with["text"], sample_text)
            self.assertIsNone(res_with["unavailable_reason"])
            self.assertEqual(res_with["style"], "STEP_BY_STEP")

            # both styles surface the exact same captured text - no synthetic summary
            summ = self._gen([vid_with], answer_key_presentation=ANSWER_KEY_AND_RESOLUTION_AT_END,
                             resolution_style="SUMMARY").json()
            self.assertEqual(summ["items"][0]["answer_key"]["resolution"]["text"], sample_text)

            # current/default behaviour is untouched for a question without one
            res_without = items[vid_without]["answer_key"]["resolution"]
            self.assertFalse(res_without["available"])
            self.assertIsNone(res_without["text"])
            self.assertIsNotNone(res_without["unavailable_reason"])
        finally:
            self.loop.run_until_complete(_set(None))  # never leave fixture state mutated

    # -- acceptance walk: 10+ questions is out of fixture range, but the pipeline shape holds --
    def test_full_pipeline_shape(self):
        d = self._gen(self.all_vids, title="Prova de Álgebra", instructions="Resolva sem calculadora.",
                      answer_key_presentation="KEY_AT_END").json()
        self.assertEqual(d["configuration"]["title"], "Prova de Álgebra")
        self.assertEqual(d["configuration"]["instructions"], "Resolva sem calculadora.")
        self.assertEqual(d["configuration"]["activity_mode"], "EXERCISE_LIST")
        self.assertEqual(d["question_count"], len(self.all_vids))
        self.assertEqual(d["question_version_ids"], self.all_vids)
        self.assertIn("canonical_reference", d["future_compat"])
        self.assertEqual(d["future_compat"]["canonical_reference"], "question_version_id")
        self.assertIn("question_usage_history", d["future_compat"])


if __name__ == "__main__":
    unittest.main()
