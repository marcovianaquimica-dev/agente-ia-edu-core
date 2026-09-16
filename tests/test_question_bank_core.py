"""PHASE 12 - focused tests for the Question Bank core (QuestionBankService).

In-memory SQLite, unittest style (matches the rest of the suite). A small
controlled fixture exercises: listing, every filter, pagination, ordering,
get-by-id / get-by-official-number, deterministic ENEM area derivation,
curriculum-v2 integration, classified vs unclassified vs NEEDS_REVIEW vs
FORCED_CLOSURE, asset absence, alternative-order preservation, no provider
access, and no mutation of official data.
"""

from __future__ import annotations

import ast
import unittest
import uuid
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

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
from agente_ia_edu.services.question_bank import (
    ENEM_AREA_CODES,
    QuestionBankFilters,
    QuestionBankService,
    derive_enem_area,
)

_SRC = Path(__file__).resolve().parents[1] / "src" / "agente_ia_edu"


def _node(session, code, name, node_type, parent=None):
    n = CatalogNode(code=code, name=name, node_type=node_type,
                    parent_id=parent.id if parent else None,
                    root_id=(parent.root_id if parent else None), active=True)
    session.add(n)
    return n


class _Fixture:
    """Seeds a compact but representative bank."""

    async def build(self, session: AsyncSession) -> dict:
        inst = Institution(code="INEP", name="INEP")
        session.add(inst)
        await session.flush()
        exam = Exam(institution_id=inst.id, code="ENEM", name="ENEM")
        session.add(exam)
        await session.flush()

        # curriculum-v2 mini tree: BIOLOGY > BIOLOGY-ANIMAL-PHYSIOLOGY > -ADAPTATIONS
        bio = _node(session, "BIOLOGY", "Biologia", "DISCIPLINE")
        session.add(bio)
        await session.flush()
        bio.root_id = bio.id
        bio_area = _node(session, "BIOLOGY-ANIMAL-PHYSIOLOGY", "Fisiologia Animal", "AREA", bio)
        session.add(bio_area)
        await session.flush()
        bio_area.root_id = bio.id
        bio_content = _node(session, "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS",
                            "Fisiologia e adaptacoes", "CONTENT", bio_area)
        session.add(bio_content)
        await session.flush()
        bio_content.root_id = bio.id
        math = _node(session, "MATH", "Matematica", "DISCIPLINE")
        session.add(math)
        await session.flush()
        math.root_id = math.id
        math_area = _node(session, "MATH-ALGEBRA", "Algebra", "AREA", math)
        session.add(math_area)
        await session.flush()
        math_area.root_id = math.id
        math_content = _node(session, "MATH-ALGEBRA-FUNCTIONS", "Funcoes", "CONTENT", math_area)
        session.add(math_content)
        await session.flush()
        math_content.root_id = math.id

        apps = {}
        for year in (2024, 2025):
            app = ExamApplication(exam_id=exam.id, year=year, application_type="regular", day=2)
            session.add(app)
            await session.flush()
            bk = ExamBooklet(exam_application_id=app.id, code="D2_CD5", color="AMARELO")
            session.add(bk)
            await session.flush()
            apps[year] = (app, bk)

        made = {}
        # (year, official_number, kind) -> classification spec | None
        plan = [
            (2024, 96, None),                       # unclassified CN
            (2024, 130, ("CHEMISTRY", None)),       # (won't resolve path -> code passthrough) classified
            (2025, 97, ("BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS", "CLASSIFIED")),  # classified, mode None
            (2025, 116, ("MATH-ALGEBRA-FUNCTIONS", "CLASSIFIED")),
            (2025, 129, ("MATH-ALGEBRA-FUNCTIONS", "FORCED_CLOSURE")),  # forced closure / needs review
            (2025, 150, ("MATH-ALGEBRA-FUNCTIONS", "FORCED_CLOSURE_VISUAL")),  # visual dependency
            (2024, 30, None),                       # Day-1-range number (LC) but day=2 app -> area from number
            (2024, 160, None),                      # unclassified MT
        ]
        for year, num, spec in plan:
            app, bk = apps[year]
            q = Question(validation_status="validated", origin_type="IMPORTED",
                         status="PUBLISHED", visibility_scope="PUBLIC")
            session.add(q)
            await session.flush()
            v = QuestionVersion(
                question_id=q.id, version_kind="official_original",
                canonical_text=f"Enunciado oficial {year} Q{num}. Texto imutavel.",
                statement=f"Enunciado oficial {year} Q{num}. Texto imutavel.",
                content_hash=f"h-{year}-{num}", is_immutable=True,
                recommended_difficulty=("MEDIUM" if num == 130 else None),
            )
            session.add(v)
            await session.flush()
            for pos, key in enumerate("ABCDE", start=1):
                session.add(QuestionOption(
                    question_version_id=v.id, option_key=key, position=pos,
                    text=f"Alternativa {key}", is_valid_option=(key == "C"),
                ))
            session.add(BookletQuestion(
                exam_booklet_id=bk.id, question_version_id=v.id,
                position=num, official_number=num, page_number=1,
                evidence_uri=(f"s3://evidence/{year}/{num}.png" if num == 130 else None),
            ))
            await session.flush()
            if spec is not None:
                code, tag = spec
                md = {"taxonomy_version": "curriculum-v2", "primary_content_code": code,
                      "evidence": [{"text": "trecho", "content_code": code, "reason": "fixture"}],
                      "context": "fixture"}
                status = "CLASSIFIED"
                if tag == "FORCED_CLOSURE":
                    status = "NEEDS_REVIEW"
                    md.update(closure_phase="11.34", classification_mode="FORCED_CLOSURE",
                              confidence="LOW", review_reason="FORCED_CLOSURE")
                elif tag == "FORCED_CLOSURE_VISUAL":
                    status = "NEEDS_REVIEW"
                    md.update(closure_phase="11.34", classification_mode="FORCED_CLOSURE",
                              confidence="MEDIUM", review_reason="FORCED_CLOSURE_VISUAL",
                              visual_dependency=True)
                session.add(PedagogicalClassification(
                    question_version_id=v.id, discipline="CURRICULUM_PROPOSAL",
                    content=code, subcontent=code, difficulty="UNKNOWN",
                    reasoning_type="UNSPECIFIED", prerequisites=[], keywords=[],
                    competencies=[], skills=[], status=status, source="rule",
                    lifecycle="ACTIVE", model_version="fixture-v1", prompt_version="v1",
                    provider_name="fixture", metadata_=md,
                ))
                # a SUPERSEDED row must be ignored by the service
                session.add(PedagogicalClassification(
                    question_version_id=v.id, discipline="CURRICULUM_PROPOSAL",
                    content="STALE-CODE", subcontent="STALE-CODE", difficulty="UNKNOWN",
                    reasoning_type="UNSPECIFIED", prerequisites=[], keywords=[],
                    competencies=[], skills=[], status="CLASSIFIED", source="ai",
                    lifecycle="SUPERSEDED", model_version="old",
                    metadata_={"taxonomy_version": "curriculum-v2",
                               "primary_content_code": "STALE-CODE"},
                ))
            made[(year, num)] = (q, v)
        await session.commit()
        return {"made": made, "nodes": {"bio_content": bio_content, "math_content": math_content}}


class QuestionBankCoreTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        async with self.factory() as s:
            self.fx = await _Fixture().build(s)

    async def asyncTearDown(self):
        await self.engine.dispose()

    # 6. deterministic ENEM area derivation
    def test_enem_area_is_derived_deterministically(self):
        self.assertEqual(derive_enem_area(1), ("LC", "Linguagens, Codigos e suas Tecnologias"))
        self.assertEqual(derive_enem_area(45)[0], "LC")
        self.assertEqual(derive_enem_area(46)[0], "CH")
        self.assertEqual(derive_enem_area(90)[0], "CH")
        self.assertEqual(derive_enem_area(91)[0], "CN")
        self.assertEqual(derive_enem_area(135)[0], "CN")
        self.assertEqual(derive_enem_area(136)[0], "MT")
        self.assertEqual(derive_enem_area(180)[0], "MT")
        self.assertEqual(derive_enem_area(200), (None, None))
        self.assertEqual(derive_enem_area(None), (None, None))
        self.assertEqual(ENEM_AREA_CODES, ("LC", "CH", "CN", "MT"))

    # 1. listing + 3. pagination + 4. ordering
    async def test_list_paginates_and_orders_deterministically(self):
        async with self.factory() as s:
            svc = QuestionBankService(s)
            p1 = await svc.list_questions(page=1, page_size=3, order_by="official_number",
                                          order_direction="asc")
            self.assertEqual(p1.total, 8)
            self.assertEqual(len(p1.items), 3)
            self.assertEqual(p1.total_pages, 3)
            nums = [i.official_number for i in p1.items]
            self.assertEqual(nums, sorted(nums))
            p2 = await svc.list_questions(page=2, page_size=3, order_by="official_number")
            self.assertFalse(
                {i.question_version_id for i in p1.items}
                & {i.question_version_id for i in p2.items}
            )
            walk = set()
            for page in range(1, p1.total_pages + 1):
                page_result = await svc.list_questions(page=page, page_size=3)
                walk.update(i.question_version_id for i in page_result.items)
            self.assertEqual(len(walk), 8)
            desc = await svc.list_questions(page=1, page_size=8, order_by="official_number",
                                            order_direction="desc")
            self.assertEqual([i.official_number for i in desc.items],
                             sorted((i.official_number for i in desc.items), reverse=True))

    # 2. filters (year / day / area / booklet / official_number)
    async def test_structural_filters(self):
        async with self.factory() as s:
            svc = QuestionBankService(s)
            self.assertEqual((await svc.list_questions(QuestionBankFilters(year=2024))).total, 4)
            self.assertEqual((await svc.list_questions(QuestionBankFilters(year=2025))).total, 4)
            self.assertEqual((await svc.list_questions(QuestionBankFilters(day=2))).total, 8)
            cn = await svc.list_questions(QuestionBankFilters(enem_area="CN"), page_size=50)
            self.assertTrue(all(i.enem_area == "CN" for i in cn.items))
            self.assertTrue(all(91 <= i.official_number <= 135 for i in cn.items))
            mt = await svc.list_questions(QuestionBankFilters(enem_area="MT"), page_size=50)
            self.assertTrue(all(i.enem_area == "MT" for i in mt.items))
            self.assertEqual(
                (await svc.list_questions(QuestionBankFilters(booklet_code="D2_CD5"))).total, 8)
            one = await svc.list_questions(QuestionBankFilters(year=2024, official_number=130))
            self.assertEqual(one.total, 1)
            self.assertEqual(one.items[0].official_number, 130)

    # free-text search (the bank's own search box: "Busca (número, texto ou
    # conteúdo)") - the `text` filter was accepted by the frontend and sent
    # as a query param, but the route never declared it and the filters
    # dataclass never carried it, so it was silently dropped end to end and
    # the search box always returned the same unfiltered page regardless of
    # what was typed.
    async def test_text_search_matches_statement_content(self):
        async with self.factory() as s:
            svc = QuestionBankService(s)
            one = await svc.list_questions(QuestionBankFilters(text="Q116"))
            self.assertEqual(one.total, 1)
            self.assertEqual(one.items[0].official_number, 116)

            none = await svc.list_questions(QuestionBankFilters(text="nioquelquercoisa-inexistente"))
            self.assertEqual(none.total, 0)

            all_ = await svc.list_questions(QuestionBankFilters(text="Enunciado oficial"), page_size=50)
            self.assertEqual(all_.total, 8)

    # 7. curriculum-v2 integration + 8/9/10/11 classification states
    async def test_curriculum_v2_integration_and_states(self):
        async with self.factory() as s:
            svc = QuestionBankService(s)
            q97 = await svc.get_by_official_number(year=2025, official_number=97)
            self.assertIsNotNone(q97.classification)
            self.assertEqual(q97.classification.content_code, "BIOLOGY-ANIMAL-PHYSIOLOGY-ADAPTATIONS")
            self.assertEqual(q97.classification.discipline_code, "BIOLOGY")
            self.assertEqual(q97.classification.area_code, "BIOLOGY-ANIMAL-PHYSIOLOGY")
            self.assertEqual(q97.classification.taxonomy_version, "curriculum-v2")
            self.assertEqual(q97.classification_state, "CLASSIFIED")
            self.assertEqual(q97.classification.lifecycle, "ACTIVE")
            self.assertTrue(q97.classification.evidence)

            unclassified = await svc.get_by_official_number(year=2024, official_number=96)
            self.assertIsNone(unclassified.classification)
            self.assertEqual(unclassified.classification_state, "UNCLASSIFIED")

            fc = await svc.get_by_official_number(year=2025, official_number=129)
            self.assertEqual(fc.classification_state, "FORCED_CLOSURE")
            self.assertEqual(fc.classification.classification_mode, "FORCED_CLOSURE")
            self.assertEqual(fc.classification.confidence, "LOW")
            self.assertEqual(fc.classification.status, "NEEDS_REVIEW")

            # filters
            self.assertEqual(
                (await svc.list_questions(QuestionBankFilters(has_classification=True))).total, 5)
            self.assertEqual(
                (await svc.list_questions(QuestionBankFilters(has_classification=False))).total, 3)
            self.assertEqual(
                (await svc.list_questions(QuestionBankFilters(classification_state="FORCED_CLOSURE"))).total, 2)
            self.assertEqual(
                (await svc.list_questions(QuestionBankFilters(classification_state="NEEDS_REVIEW"))).total, 2)
            self.assertEqual(
                (await svc.list_questions(QuestionBankFilters(classification_state="CLASSIFIED"))).total, 3)
            self.assertEqual(
                (await svc.list_questions(QuestionBankFilters(provisional_only=True))).total, 2)
            dfns = await svc.list_questions(QuestionBankFilters(discipline_code="MATH"), page_size=50)
            self.assertEqual(dfns.total, 3)
            self.assertTrue(all(i.classification.content_code.startswith("MATH") for i in dfns.items))
            self.assertEqual(
                (await svc.list_questions(QuestionBankFilters(content_code="MATH-ALGEBRA-FUNCTIONS"))).total, 3)
            with self.assertRaises(ValueError):
                await svc.list_questions(QuestionBankFilters(classification_state="BOGUS"))

    # 12. asset present / absent
    async def test_visual_dependency_and_asset_absence(self):
        async with self.factory() as s:
            svc = QuestionBankService(s)
            vis = await svc.list_questions(QuestionBankFilters(visual_dependency=True), page_size=50)
            self.assertEqual(vis.total, 1)
            item = vis.items[0]
            self.assertTrue(item.has_visual_dependency)
            self.assertEqual(item.classification.review_reason, "FORCED_CLOSURE_VISUAL")
            # infrastructure ready but empty - never fabricated
            self.assertEqual(item.assets, [])
            non_visual = await svc.get_by_official_number(year=2024, official_number=96)
            self.assertEqual(non_visual.assets, [])
            self.assertFalse(non_visual.has_visual_dependency)
            self.assertIsNone(non_visual.evidence_uri)

    # 13. alternative order preserved + 5. get by id
    async def test_option_order_preserved_and_get_by_id(self):
        async with self.factory() as s:
            svc = QuestionBankService(s)
            (q, v) = self.fx["made"][(2024, 130)]
            item = await svc.get_question(q.id)
            self.assertIsNotNone(item)
            self.assertEqual([o.position for o in item.options], [1, 2, 3, 4, 5])
            self.assertEqual([o.key for o in item.options], ["A", "B", "C", "D", "E"])
            self.assertEqual([o.is_valid_option for o in item.options],
                             [False, False, True, False, False])
            self.assertEqual(item.recommended_difficulty, "MEDIUM")
            self.assertEqual(item.evidence_uri, "s3://evidence/2024/130.png")
            self.assertIsNone(await svc.get_question(uuid.uuid4()))

    # 9 (professor). ordered question_version_id collection for future list generator
    async def test_build_selection_is_ordered_and_validated(self):
        async with self.factory() as s:
            svc = QuestionBankService(s)
            v97 = self.fx["made"][(2025, 97)][1].id
            v116 = self.fx["made"][(2025, 116)][1].id
            v96 = self.fx["made"][(2024, 96)][1].id
            selection = await svc.build_selection([v116, v96, v97], source="teacher-draft")
            self.assertEqual(selection.question_version_ids, [v116, v96, v97])
            self.assertEqual([e.position for e in selection.entries], [1, 2, 3])
            self.assertEqual(selection.entries[0].content_code, "MATH-ALGEBRA-FUNCTIONS")
            self.assertIsNone(selection.entries[1].content_code)
            self.assertEqual(selection.source, "teacher-draft")
            with self.assertRaises(ValueError):
                await svc.build_selection([v97, v97])
            with self.assertRaises(ValueError):
                await svc.build_selection([uuid.uuid4()])

    # 15. no official data mutated by any read
    async def test_reads_never_mutate_official_data(self):
        async with self.factory() as s:
            before_q = await s.scalar(select(func.count()).select_from(Question))
            before_v = await s.scalar(select(func.count()).select_from(QuestionVersion))
            before_o = await s.scalar(select(func.count()).select_from(QuestionOption))
            before_c = await s.scalar(select(func.count()).select_from(CatalogNode))
            before_p = await s.scalar(select(func.count()).select_from(PedagogicalClassification))
            svc = QuestionBankService(s)
            await svc.list_questions(page_size=50)
            await svc.list_questions(QuestionBankFilters(discipline_code="MATH"))
            await svc.get_by_official_number(year=2025, official_number=97)
            await svc.build_selection([self.fx["made"][(2025, 97)][1].id])
        async with self.factory() as s:
            self.assertEqual(await s.scalar(select(func.count()).select_from(Question)), before_q)
            self.assertEqual(await s.scalar(select(func.count()).select_from(QuestionVersion)), before_v)
            self.assertEqual(await s.scalar(select(func.count()).select_from(QuestionOption)), before_o)
            self.assertEqual(await s.scalar(select(func.count()).select_from(CatalogNode)), before_c)
            self.assertEqual(
                await s.scalar(select(func.count()).select_from(PedagogicalClassification)), before_p)

    # 14 + 17. the query layer imports no AI provider / SDK
    def test_question_bank_layer_is_ai_agnostic(self):
        banned_modules = {
            "openai", "agente_ia_edu.providers", "agente_ia_edu.providers.factory",
            "agente_ia_edu.providers.adapters.openai", "agente_ia_edu.services.ai_classification_service",
            "agente_ia_edu.services.classification_consensus", "agente_ia_edu.classification_prompts",
            "agente_ia_edu.services.curriculum_classification",
        }
        banned_names = {"AsyncOpenAI", "OpenAIProvider", "build_text_provider"}
        for rel in ("services/question_bank.py", "api/schemas/question_bank.py",
                    "api/routes/question_bank.py"):
            tree = ast.parse((_SRC / rel).read_text())
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self.assertNotIn(alias.name.split(".")[0], {"openai"}, rel)
                        self.assertNotIn(alias.name, banned_modules, rel)
                elif isinstance(node, ast.ImportFrom):
                    mod = node.module or ""
                    self.assertNotIn(mod, banned_modules, f"{rel} imports {mod}")
                    self.assertFalse(mod.startswith("agente_ia_edu.providers"), f"{rel}:{mod}")
                    for alias in node.names:
                        self.assertNotIn(alias.name, banned_names, f"{rel} imports {alias.name}")


if __name__ == "__main__":
    unittest.main()
