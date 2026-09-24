"""Service-layer coverage for ``material_availability.py`` (SERVICE-layer wave,
2026-09).

Focused, direct async-service tests (real SQLAlchemy session against
in-memory SQLite - no HTTP layer, since ``MaterialAvailabilityService`` is a
read-only, DB-only derivation with no dedicated route of its own beyond the
already-covered ``GET /content-materials`` in test_catalog / phase23) for the
edge cases the existing HTTP/service-layer suites (test_phase23_..., test_
phase24_..., test_phase25_...) never happen to exercise:

  * empty / unknown content_code inputs (both ``for_content_codes`` and
    ``resolve_for_content`` fail-soft, never raising, on codes that don't
    resolve to any catalog_nodes row)
  * the SECTION-level association path (a material associated only via
    ``MaterialSection.content_node_id``, never at material level) for both
    the plain (staff-facing) and tenant-aware (student-facing) query
    families
  * the tenant-aware fail-closed rule when a SCHOOL-scoped material's school
    does NOT match the requester's school (must come back unavailable, not
    error)
  * ``resolve_for_one`` (the singular convenience wrapper)
  * ``visible_to_student`` (PUBLIC always-visible / PRIVATE never-visible)
    called directly, since none of the existing suites reach it as a static
    call with those two scopes
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    MaterialSection,
    School,
    TheoryMaterial,
    TheoryMaterialVersion,
)
from agente_ia_edu.services.material_availability import (
    MaterialAvailability,
    MaterialAvailabilityService,
    MaterialResolution,
)

_SCHOOL_A = _uuid.uuid5(_uuid.NAMESPACE_DNS, "matavail-school-a")
_SCHOOL_B = _uuid.uuid5(_uuid.NAMESPACE_DNS, "matavail-school-b")


async def _seed(factory) -> dict:
    async with factory() as s:
        s.add(School(id=_SCHOOL_A, code="MASCHA", name="Escola A", status="ACTIVE"))
        s.add(School(id=_SCHOOL_B, code="MASCHB", name="Escola B", status="ACTIVE"))
        await s.flush()

        disc = CatalogNode(code="MA-DISC", name="Disciplina", node_type="DISCIPLINE", active=True)
        s.add(disc); await s.flush(); disc.root_id = disc.id
        area = CatalogNode(code="MA-AREA", name="Area", node_type="AREA",
                           parent_id=disc.id, root_id=disc.id, active=True)
        s.add(area); await s.flush()
        node_material_level = CatalogNode(code="MA-CODE-MATERIAL", name="Conteudo Material-Level",
                                          node_type="CONTENT", parent_id=area.id, root_id=disc.id, active=True)
        node_section_level = CatalogNode(code="MA-CODE-SECTION", name="Conteudo Section-Level",
                                         node_type="CONTENT", parent_id=area.id, root_id=disc.id, active=True)
        s.add_all([node_material_level, node_section_level]); await s.flush()

        # M1: PUBLIC, associated at MATERIAL level to MA-CODE-MATERIAL.
        m1 = TheoryMaterial(title="Material Publico", visibility_scope="PUBLIC",
                            primary_content_node_id=node_material_level.id,
                            created_by_external_identity="t:seed")
        s.add(m1); await s.flush()
        v1 = TheoryMaterialVersion(material_id=m1.id, version_number=1, status="PUBLISHED",
                                   published_at=datetime.now(timezone.utc))
        s.add(v1); await s.flush()

        # M2: SCHOOL-scoped (school A), associated ONLY at SECTION level to
        # MA-CODE-SECTION (primary_content_node_id left unset on purpose -
        # this must still be discoverable through the section-level query).
        m2 = TheoryMaterial(title="Material Escolar", visibility_scope="SCHOOL", school_id=_SCHOOL_A,
                            primary_content_node_id=None, created_by_external_identity="t:seed")
        s.add(m2); await s.flush()
        v2 = TheoryMaterialVersion(material_id=m2.id, version_number=1, status="PUBLISHED",
                                   published_at=datetime.now(timezone.utc))
        s.add(v2); await s.flush()
        sec2 = MaterialSection(material_version_id=v2.id, section_type="CHAPTER", position=1,
                               title="Cap 1", content_node_id=node_section_level.id)
        s.add(sec2); await s.flush()

        await s.commit()
        return {
            "material_code": "MA-CODE-MATERIAL", "section_code": "MA-CODE-SECTION",
            "unknown_code": "MA-CODE-DOES-NOT-EXIST",
            "m1_id": str(m1.id), "m1_title": m1.title,
            "m2_id": str(m2.id), "m2_title": m2.title,
        }


class MaterialAvailabilityCoverageTests(unittest.TestCase):
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

    @classmethod
    def tearDownClass(cls):
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    # -- for_content_codes(): empty input never touches the DB, never errors
    def test_for_content_codes_empty_list_returns_empty_dict(self):
        async def run():
            async with self.factory() as s:
                return await MaterialAvailabilityService(s).for_content_codes([])
        self.assertEqual(self.loop.run_until_complete(run()), {})

    # -- for_content_codes(): a code with no matching catalog_nodes row at
    #    all comes back False/0, never a KeyError or an invented row
    def test_for_content_codes_unknown_code_is_unavailable(self):
        async def run():
            async with self.factory() as s:
                return await MaterialAvailabilityService(s).for_content_codes([self.seed["unknown_code"]])
        result = self.loop.run_until_complete(run())
        self.assertEqual(result, {self.seed["unknown_code"]: MaterialAvailability(self.seed["unknown_code"], False, 0)})

    # -- for_content_codes(): SECTION-level-only association must still be
    #    found (a material can be linked to a content purely through one of
    #    its sections, with no primary_content_node_id set at all)
    def test_for_content_codes_section_level_association_counts(self):
        async def run():
            async with self.factory() as s:
                return await MaterialAvailabilityService(s).for_content_codes([self.seed["section_code"]])
        result = self.loop.run_until_complete(run())
        avail = result[self.seed["section_code"]]
        self.assertTrue(avail.material_available)
        self.assertEqual(avail.material_count, 1)

    # -- resolve_for_content(): unknown code -> default MaterialResolution,
    #    never a lookup error
    def test_resolve_for_content_unknown_code_returns_default(self):
        async def run():
            async with self.factory() as s:
                return await MaterialAvailabilityService(s).resolve_for_content(
                    [self.seed["unknown_code"]], requester_school_id=None)
        result = self.loop.run_until_complete(run())
        self.assertEqual(
            result[self.seed["unknown_code"]],
            MaterialResolution(self.seed["unknown_code"], False, 0, None, None),
        )

    # -- resolve_for_content(): MATERIAL-level PUBLIC association is visible
    #    to every requester, including an independent student (school=None)
    def test_resolve_for_content_material_level_public_is_visible(self):
        async def run():
            async with self.factory() as s:
                return await MaterialAvailabilityService(s).resolve_for_content(
                    [self.seed["material_code"]], requester_school_id=None)
        res = self.loop.run_until_complete(run())[self.seed["material_code"]]
        self.assertTrue(res.material_available)
        self.assertEqual(res.material_count, 1)
        self.assertEqual(res.material_id, self.seed["m1_id"])
        self.assertEqual(res.material_title, self.seed["m1_title"])

    # -- resolve_for_content(): SECTION-level SCHOOL association is visible
    #    ONLY to a requester from the matching school
    def test_resolve_for_content_section_level_school_visible_to_matching_school(self):
        async def run():
            async with self.factory() as s:
                return await MaterialAvailabilityService(s).resolve_for_content(
                    [self.seed["section_code"]], requester_school_id=str(_SCHOOL_A))
        res = self.loop.run_until_complete(run())[self.seed["section_code"]]
        self.assertTrue(res.material_available)
        self.assertEqual(res.material_count, 1)
        self.assertEqual(res.material_id, self.seed["m2_id"])
        self.assertEqual(res.material_title, self.seed["m2_title"])

    # -- resolve_for_content(): the SAME SCHOOL-scoped material must be
    #    fail-closed invisible to a requester from a DIFFERENT school - a
    #    school-scoped material must never leak to another school's student
    def test_resolve_for_content_section_level_school_hidden_from_other_school(self):
        async def run():
            async with self.factory() as s:
                return await MaterialAvailabilityService(s).resolve_for_content(
                    [self.seed["section_code"]], requester_school_id=str(_SCHOOL_B))
        res = self.loop.run_until_complete(run())[self.seed["section_code"]]
        self.assertEqual(res, MaterialResolution(self.seed["section_code"], False, 0, None, None))

    # -- resolve_for_one(): singular convenience wrapper over resolve_for_content
    def test_resolve_for_one_matches_resolve_for_content(self):
        async def run():
            async with self.factory() as s:
                svc = MaterialAvailabilityService(s)
                one = await svc.resolve_for_one(self.seed["material_code"], requester_school_id=None)
                many = await svc.resolve_for_content([self.seed["material_code"]], requester_school_id=None)
                return one, many[self.seed["material_code"]]
        one, many = self.loop.run_until_complete(run())
        self.assertEqual(one, many)

    # -- visible_to_student(): PUBLIC is always visible, independent of tenant
    def test_visible_to_student_public_is_always_visible(self):
        material = TheoryMaterial(visibility_scope="PUBLIC")
        self.assertTrue(MaterialAvailabilityService.visible_to_student(material, requester_school_id=None))
        self.assertTrue(MaterialAvailabilityService.visible_to_student(material, requester_school_id=str(_SCHOOL_A)))

    # -- visible_to_student(): PRIVATE (and any non PUBLIC/SCHOOL scope, e.g.
    #    the reserved CLASS/STUDENT values) is never visible here - fail
    #    closed, since no classroom/student targeting column exists yet
    def test_visible_to_student_private_is_never_visible(self):
        private_material = TheoryMaterial(visibility_scope="PRIVATE")
        self.assertFalse(MaterialAvailabilityService.visible_to_student(private_material, requester_school_id=None))
        self.assertFalse(
            MaterialAvailabilityService.visible_to_student(private_material, requester_school_id=str(_SCHOOL_A)))
        class_material = TheoryMaterial(visibility_scope="CLASS")
        self.assertFalse(
            MaterialAvailabilityService.visible_to_student(class_material, requester_school_id=str(_SCHOOL_A)))


if __name__ == "__main__":
    unittest.main()
