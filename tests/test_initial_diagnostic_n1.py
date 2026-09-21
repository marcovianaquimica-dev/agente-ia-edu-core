"""N+1 query regression tests for initial_diagnostic.py.

Three separate N+1s lived in this file, all confirmed with a real in-process
query counter (SQLAlchemy `before_cursor_execute`, the same technique used
live against Postgres) before being fixed:

1. `_select_next_question_for_diagnostic` (~line 594) tested each candidate
   `CatalogNode` against `PedagogicalUniverseService.contains_catalog_node()`
   one at a time inside a list comprehension - exactly the pattern already
   fixed in `catalog.py`'s cascading `/nodes` listing this same audit
   (`pedagogical_universe.py`'s new `contains_catalog_nodes()` batched
   primitive). Fixed by routing through the batched primitive, same as
   `catalog.py:443`.

2. `_global_sufficiency` (called on every `answer_question()` while in
   GLOBAL/ENEM/UNSPECIFIED diagnostic mode) built a
   `{content_node_id: session.get(CatalogNode, ...)}` dict one `session.get`
   per answered selection, then issued a SECOND `session.get` per distinct
   node to resolve its DISCIPLINE root. Since it recomputes over every
   answered selection so far on every single answer, this was effectively
   O(n) queries per call and O(n^2) total queries across one diagnostic
   session. Fixed with two batched `IN (...)` queries (content nodes, then
   their distinct roots).

3. `get_diagnostic_result`'s `probable_gaps` loop issued a
   `session.get(CatalogNode, node.parent_id)` per content node scoring below
   the mastery threshold, to resolve the prerequisite node. Fixed by
   batch-fetching every candidate parent up front with one `IN (...)` query.

The signature of a real N+1 fix is that the query count for the SAME
operation stays constant (or grows only with genuinely distinct lookups,
never with the size of the collection being processed) as that collection
grows. That is exactly what each test below asserts: build a "small"
scenario and a "large" one and require the query count NOT to grow
proportionally with size.
"""

import unittest
import uuid
from datetime import datetime, timezone

from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    DiagnosticQuestionSelection,
    InitialDiagnostic,
    Question,
    QuestionClassification,
    QuestionOption,
    QuestionVersion,
    Taxonomy,
    TaxonomyNode,
    ContentQuestionLink,
)
from agente_ia_edu.services.initial_diagnostic import DiagnosticStatus, InitialDiagnosticService
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.pedagogical_universe import PedagogicalUniverseService


class QueryCounter:
    """Counts SQL statements executed against `engine` for the duration of a
    `with` block, via SQLAlchemy's `before_cursor_execute` event - the same
    instrumentation technique used to measure query counts live against the
    real dev Postgres, reused here so the (fast, disposable) sqlite-backed
    unit tests can assert exact counts."""

    def __init__(self, engine):
        self.engine = engine
        self.count = 0
        self._listener = None

    def __enter__(self):
        def _count(conn, cursor, statement, parameters, context, executemany):
            self.count += 1

        self._listener = _count
        event.listen(self.engine.sync_engine, "before_cursor_execute", self._listener)
        return self

    def __exit__(self, *exc):
        event.remove(self.engine.sync_engine, "before_cursor_execute", self._listener)


class InitialDiagnosticN1Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _service(self, session):
        return InitialDiagnosticService(session=session, knowledge_service=KnowledgeService(session))

    # -- 1) _select_next_question_for_diagnostic's universe-membership filter --

    async def _seed_universe_scoped_diagnostic(self, session, num_contents: int):
        """DISCIPLINE "Quimica" with `num_contents` direct CONTENT children,
        each with one EASY, PUBLIC, classified question version so the
        adaptive search loop resolves the FIRST candidate node immediately
        (isolating the universe-membership filter's query cost from the
        search loop's own cost). A PedagogicalUniverse is scoped to the
        whole discipline (include_descendants=True) so every content node
        passes the filter - the worst case for a per-node loop, since it
        can never short-circuit on an early miss."""
        suffix = uuid.uuid4().hex[:8]
        discipline_name = f"Quimica-{suffix}"
        root = CatalogNode(node_type="DISCIPLINE", name=discipline_name, position=1, active=True)
        session.add(root)
        await session.flush()
        root.root_id = root.id
        await session.flush()

        tax = Taxonomy(code=f"bncc-{uuid.uuid4().hex[:8]}", name="BNCC", version="1.0")
        session.add(tax)
        await session.flush()

        contents = []
        for i in range(num_contents):
            content = CatalogNode(
                parent_id=root.id, root_id=root.id, node_type="CONTENT",
                code=f"C{suffix}-{i}", name=f"Conteudo {i}", position=i, active=True,
            )
            session.add(content)
            await session.flush()
            contents.append(content)

            question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
            session.add(question)
            await session.flush()
            version = QuestionVersion(
                question_id=question.id, version_kind="official_original",
                canonical_text=f"Questao {i}", content_hash=f"hash-{uuid.uuid4().hex}",
                recommended_difficulty="EASY",
            )
            session.add(version)
            await session.flush()
            session.add_all([
                QuestionOption(question_version_id=version.id, option_key="A", position=1, text="Correta", is_valid_option=True),
                QuestionOption(question_version_id=version.id, option_key="B", position=2, text="Incorreta", is_valid_option=False),
            ])
            tax_node = TaxonomyNode(id=content.id, taxonomy_id=tax.id, code=f"SK{i}", name=f"Skill {i}", node_type="skill")
            session.add(tax_node)
            await session.flush()
            session.add(QuestionClassification(
                question_version_id=version.id, taxonomy_id=tax.id,
                competency_node_id=content.id, skill_node_id=content.id,
                is_primary=True, status="active", source="human",
            ))
            session.add(ContentQuestionLink(content_node_id=content.id, question_version_id=version.id))

        await session.commit()

        universe_service = PedagogicalUniverseService(session)
        universe = await universe_service.create_universe(
            external_id=f"U_{uuid.uuid4().hex[:8]}", slug=f"u-{uuid.uuid4().hex[:8]}", name="Universo",
            owner_type="PLATFORM", owner_external_id=None, performed_by_external_id="admin", status="ACTIVE",
        )
        await universe_service.add_catalog_scope(
            universe_id=universe.id, catalog_node_id=root.id, scope_kind="DISCIPLINE", include_descendants=True,
        )

        diagnostic = InitialDiagnostic(
            student_id="student:n1-test", discipline=discipline_name, diagnostic_version="v1",
            status=DiagnosticStatus.IN_PROGRESS, total_questions_asked=0, total_correct=0,
            overall_confidence=0.0, started_at=datetime.now(timezone.utc),
            metadata_={"context_snapshot": {"pedagogical_universe": {"id": str(universe.id)}}},
        )
        session.add(diagnostic)
        await session.commit()
        await session.refresh(diagnostic)
        return diagnostic

    async def test_universe_filter_query_count_does_not_grow_with_candidate_count(self):
        async with self.session_factory() as session:
            diagnostic = await self._seed_universe_scoped_diagnostic(session, 3)
            service = self._service(session)
            with QueryCounter(self.engine) as counter:
                selection = await service._select_next_question_for_diagnostic(diagnostic, position=1)
            self.assertIsNotNone(selection)
            small_count = counter.count

        async with self.session_factory() as session:
            diagnostic = await self._seed_universe_scoped_diagnostic(session, 40)
            service = self._service(session)
            with QueryCounter(self.engine) as counter:
                selection = await service._select_next_question_for_diagnostic(diagnostic, position=1)
            self.assertIsNotNone(selection)
            large_count = counter.count

        self.assertEqual(
            small_count, large_count,
            "_select_next_question_for_diagnostic's universe-membership filter "
            f"must not scale with candidate node count: 3 nodes -> {small_count} "
            f"queries, 40 nodes -> {large_count} queries.",
        )

    # -- 2) _global_sufficiency --

    async def _seed_global_sufficiency_scenario(self, session, num_selections: int):
        diagnostic = InitialDiagnostic(
            student_id="student:n1-global", discipline="GLOBAL", diagnostic_version="v1",
            status=DiagnosticStatus.IN_PROGRESS, total_questions_asked=num_selections, total_correct=0,
            overall_confidence=0.0, started_at=datetime.now(timezone.utc),
            metadata_={"entry_profile": {"diagnostic_mode": "GLOBAL"}},
        )
        session.add(diagnostic)
        await session.flush()

        question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
        session.add(question)
        await session.flush()
        version = QuestionVersion(
            question_id=question.id, version_kind="official_original", canonical_text="Q",
            content_hash=f"hash-{uuid.uuid4().hex}", recommended_difficulty="EASY",
        )
        session.add(version)
        await session.flush()

        for i in range(num_selections):
            discipline = CatalogNode(node_type="DISCIPLINE", name=f"Disciplina {i}", active=True)
            session.add(discipline)
            await session.flush()
            discipline.root_id = discipline.id
            content = CatalogNode(parent_id=discipline.id, root_id=discipline.id, node_type="CONTENT", name=f"Conteudo {i}", active=True)
            session.add(content)
            await session.flush()
            session.add(DiagnosticQuestionSelection(
                diagnostic_id=diagnostic.id, question_version_id=version.id, content_node_id=content.id,
                difficulty_level="EASY", position=i + 1, is_correct=(i % 2 == 0),
                answered_at=datetime.now(timezone.utc),
            ))
        await session.commit()
        await session.refresh(diagnostic)
        return diagnostic

    async def test_global_sufficiency_query_count_does_not_grow_with_selection_count(self):
        async with self.session_factory() as session:
            diagnostic = await self._seed_global_sufficiency_scenario(session, 3)
            service = self._service(session)
            with QueryCounter(self.engine) as counter:
                await service._global_sufficiency(diagnostic)
            small_count = counter.count

        async with self.session_factory() as session:
            diagnostic = await self._seed_global_sufficiency_scenario(session, 30)
            service = self._service(session)
            with QueryCounter(self.engine) as counter:
                await service._global_sufficiency(diagnostic)
            large_count = counter.count

        self.assertEqual(
            small_count, large_count,
            "_global_sufficiency() must not issue one query PER answered "
            f"selection: 3 selections -> {small_count} queries, 30 selections "
            f"-> {large_count} queries.",
        )

    # -- 3) get_diagnostic_result's probable_gaps prerequisite lookup --

    async def _seed_diagnostic_result_scenario(self, session, num_low_mastery_nodes: int):
        diagnostic = InitialDiagnostic(
            student_id="student:n1-result", discipline="Quimica", diagnostic_version="v1",
            status=DiagnosticStatus.COMPLETED, total_questions_asked=num_low_mastery_nodes, total_correct=0,
            overall_confidence=0.5, started_at=datetime.now(timezone.utc),
            completed_at=datetime.now(timezone.utc), metadata_={},
        )
        session.add(diagnostic)
        await session.flush()

        question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
        session.add(question)
        await session.flush()
        version = QuestionVersion(
            question_id=question.id, version_kind="official_original", canonical_text="Q",
            content_hash=f"hash-{uuid.uuid4().hex}", recommended_difficulty="EASY",
        )
        session.add(version)
        await session.flush()

        for i in range(num_low_mastery_nodes):
            parent = CatalogNode(node_type="DISCIPLINE", name=f"Pai {i}", active=True)
            session.add(parent)
            await session.flush()
            parent.root_id = parent.id
            content = CatalogNode(parent_id=parent.id, root_id=parent.id, node_type="CONTENT", name=f"Conteudo {i}", active=True)
            session.add(content)
            await session.flush()
            # All answers wrong -> mastery_score 0 -> below the 50.0 gap threshold.
            session.add(DiagnosticQuestionSelection(
                diagnostic_id=diagnostic.id, question_version_id=version.id, content_node_id=content.id,
                difficulty_level="EASY", position=i + 1, is_correct=False,
                answered_at=datetime.now(timezone.utc),
            ))
        await session.commit()
        return diagnostic.id

    async def test_diagnostic_result_prerequisite_lookup_does_not_grow_with_gap_count(self):
        async with self.session_factory() as session:
            diagnostic_id = await self._seed_diagnostic_result_scenario(session, 2)
            service = self._service(session)
            with QueryCounter(self.engine) as counter:
                result = await service.get_diagnostic_result(diagnostic_id)
            self.assertEqual(len(result["probable_gaps"]), 2)
            small_count = counter.count

        async with self.session_factory() as session:
            diagnostic_id = await self._seed_diagnostic_result_scenario(session, 25)
            service = self._service(session)
            with QueryCounter(self.engine) as counter:
                result = await service.get_diagnostic_result(diagnostic_id)
            self.assertEqual(len(result["probable_gaps"]), 25)
            large_count = counter.count

        self.assertEqual(
            small_count, large_count,
            "get_diagnostic_result()'s prerequisite lookup must not issue one "
            f"query PER low-mastery content node: 2 nodes -> {small_count} "
            f"queries, 25 nodes -> {large_count} queries.",
        )


if __name__ == "__main__":
    unittest.main()
