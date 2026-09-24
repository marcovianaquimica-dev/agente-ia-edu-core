"""Service-level coverage for src/agente_ia_edu/services/domain_map.py.

Wave-7 (concurrent-zone campaign). tests/test_phase6_domain_map.py already
exercises the bulk of DomainMapService.build() (universe scoping, mastery,
trend, next-best-action integration); this file targets the branches it never
reaches: an institution-scoped student with NO classroom (the pedagogical
context "or classroom_id.is_(None))" else-branch), and the diagnostic-derived
prerequisite-hypothesis / study-objective payloads, which need an
InitialDiagnostic row with specific ``metadata_`` shapes no existing test
seeds. Baseline just before this file (existing test files only): 178
statements, 16 missing - 248, 274-279, 333-340, 385.

Uses a real async SQLAlchemy session against SQLite with the session_factory
default (``expire_on_commit=True``, matching production's
``create_session_factory()``) rather than the ``expire_on_commit=False``
override test_phase6_domain_map.py uses - ids needed after ``commit()`` are
therefore always captured into local variables beforehand, never read off an
expired ORM attribute.
"""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, InitialDiagnostic, PedagogicalContext
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.domain_map import DomainMapService


class DomainMapServiceCoverageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        # deliberately no expire_on_commit override - matches production's
        # create_session_factory() default (True).
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession)
        self.now = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    def service(self, session):
        return DomainMapService(session, now_provider=lambda: self.now)

    async def seed_catalog(self, session):
        root = CatalogNode(node_type="DISCIPLINE", name="Matematica", position=1, active=True)
        session.add(root)
        await session.flush()
        root.root_id = root.id
        area = CatalogNode(
            parent_id=root.id, root_id=root.id, node_type="AREA",
            name="Algebra", position=1, active=True,
        )
        session.add(area)
        await session.flush()
        need = CatalogNode(
            parent_id=area.id, root_id=root.id, node_type="CONTENT",
            code="NEED", name="Equacoes do 2 grau", position=1, active=True,
        )
        prereq = CatalogNode(
            parent_id=area.id, root_id=root.id, node_type="CONTENT",
            code="PREREQ", name="Equacoes do 1 grau", position=2, active=True,
        )
        objective = CatalogNode(
            parent_id=area.id, root_id=root.id, node_type="CONTENT",
            code="OBJ", name="Funcoes exponenciais", position=3, active=True,
        )
        orphan = CatalogNode(
            parent_id=area.id, root_id=root.id, node_type="CONTENT",
            code="ORPHAN", name="Progressoes aritmeticas", position=4, active=True,
        )
        session.add_all([need, prereq, objective, orphan])
        await session.flush()
        # capture ids now: with expire_on_commit=True (the default here) the
        # caller's `await session.commit()` right after seeding would expire
        # these instances, and a bare `.id` read afterwards would need a lazy
        # reload that MissingGreenlets outside the async bridge.
        return need.id, prereq.id, objective.id, orphan.id

    # -- line 248: institution-scoped student, no classroom_id - the
    #    "conditions.append(PedagogicalContext.classroom_id.is_(None))" else
    #    branch of _active_contexts (every existing test either has no
    #    institution_id at all, or sets classroom_id too).
    async def test_institution_wide_context_reaches_student_without_classroom(self):
        async with self.session_factory() as session:
            need_id, _prereq_id, _obj_id, _orphan_id = await self.seed_catalog(session)
            session.add(PedagogicalContext(
                content_node_id=need_id,
                source="SCHOOL_PLAN",
                institution_id="school-wide",
                classroom_id=None,
                recorded_at=self.now,
                active=True,
            ))
            await session.commit()

            payload = await self.service(session).build(
                student_id="student-no-classroom",
                identity=ExternalIdentityContext(
                    provider="test",
                    external_user_id="student-no-classroom",
                    institution_id="school-wide",
                    classroom_id=None,
                ),
            )
            item = next(c for c in payload["contents"] if c["content_node_id"] == need_id)
            self.assertEqual(len(item["pedagogical_contexts"]), 1)
            self.assertEqual(item["pedagogical_contexts"][0]["source"], "SCHOOL_PLAN")

    # -- lines 274-279 + 333-340: a diagnostic's prerequisite_hypotheses are
    #    deduplicated by target (highest-priority status wins) and turned
    #    into a full prerequisite payload on the TARGET content's entry.
    #    Also covers the three defensive "never fabricate a prerequisite"
    #    bail-outs: a hypothesis with no target (skipped outright), one with
    #    a target but no prerequisite_content_id, and one whose
    #    prerequisite_content_id does not match any known catalog node.
    async def test_prerequisite_hypothesis_from_diagnostic_populates_full_payload(self):
        async with self.session_factory() as session:
            need_id, prereq_id, objective_id, orphan_id = await self.seed_catalog(session)
            session.add(InitialDiagnostic(
                student_id="student-hyp",
                metadata_={
                    "prerequisite_hypotheses": [
                        # lower-priority status first: the dedup step in
                        # _hypotheses_by_target must keep SUPPORTED (priority
                        # 0), not this first-seen SUSPECTED (priority 2).
                        {
                            "target_content_id": str(need_id),
                            "prerequisite_content_id": str(prereq_id),
                            "status": "SUSPECTED",
                            "confidence": 0.4,
                        },
                        {
                            "target_content_id": str(need_id),
                            "prerequisite_content_id": str(prereq_id),
                            "status": "SUPPORTED",
                            "confidence": 0.9,
                        },
                        # no target_content_id at all - dropped, never crashes
                        {
                            "prerequisite_content_id": str(prereq_id),
                            "status": "SUPPORTED",
                        },
                        # target with no prerequisite_content_id - the target
                        # content simply gets no prerequisite payload.
                        {
                            "target_content_id": str(orphan_id),
                            "status": "SUPPORTED",
                        },
                        # prerequisite_content_id that matches no catalog
                        # node - never fabricates a prerequisite for it.
                        {
                            "target_content_id": str(objective_id),
                            "prerequisite_content_id": "00000000-0000-0000-0000-000000000000",
                            "status": "SUPPORTED",
                        },
                    ],
                },
            ))
            await session.commit()

            payload = await self.service(session).build(
                student_id="student-hyp",
                identity=ExternalIdentityContext(provider="test", external_user_id="student-hyp"),
            )
            by_id = {c["content_node_id"]: c for c in payload["contents"]}

            prereq_payload = by_id[need_id]["prerequisites"][0]
            self.assertEqual(prereq_payload["content_node_id"], prereq_id)
            self.assertEqual(prereq_payload["content_name"], "Equacoes do 1 grau")
            self.assertEqual(prereq_payload["hypothesis_status"], "SUPPORTED")
            self.assertEqual(prereq_payload["hypothesis_confidence"], 0.9)
            self.assertIsNone(prereq_payload["mastery_score"])
            self.assertEqual(prereq_payload["confidence"], 0.0)
            self.assertEqual(prereq_payload["evidence_count"], 0)

            self.assertEqual(by_id[orphan_id]["prerequisites"], [])
            self.assertEqual(by_id[objective_id]["prerequisites"], [])

    # -- line 385: preferred_content_resolution.node_id marks a content as
    #    objective_aligned by id, independent of any name-term match.
    async def test_preferred_content_resolution_node_id_marks_objective_aligned(self):
        async with self.session_factory() as session:
            _need_id, _prereq_id, objective_id, _orphan_id = await self.seed_catalog(session)
            session.add(InitialDiagnostic(
                student_id="student-obj",
                metadata_={
                    "preferred_content_resolution": {"node_id": str(objective_id)},
                },
            ))
            await session.commit()

            payload = await self.service(session).build(
                student_id="student-obj",
                identity=ExternalIdentityContext(provider="test", external_user_id="student-obj"),
            )
            item = next(c for c in payload["contents"] if c["content_node_id"] == objective_id)
            self.assertTrue(item["objective_aligned"])
            other = next(c for c in payload["contents"] if c["content_node_id"] != objective_id)
            self.assertFalse(other["objective_aligned"])


if __name__ == "__main__":
    unittest.main()
