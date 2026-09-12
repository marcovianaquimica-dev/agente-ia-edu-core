"""PHASE 23 - deterministic ``material_available`` / ``material_count`` contract.

A tiny, READ-ONLY, AI-agnostic derivation the Learning Path (PHASE 21) and the
Practice engine (PHASE 22) can consume LATER without changing their algorithms.
It answers one question per curriculum-v2 content code:

    "Is there a PUBLISHED authored material for this content, and how many?"

A material counts for ``content_code`` when its PUBLISHED version is linked to
that content either at material level (``primary_content_node_id``) or at
section level (``material_sections.content_node_id``). Codes are resolved from
``catalog_nodes`` - never copied. One batched query set; no N+1.
"""

from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    CatalogNode,
    MaterialSection,
    TheoryMaterial,
    TheoryMaterialVersion,
)

PUBLISHED = "PUBLISHED"


@dataclass(frozen=True)
class MaterialAvailability:
    content_code: str
    material_available: bool
    material_count: int

    def as_dict(self) -> dict:
        return {
            "content_code": self.content_code,
            "material_available": self.material_available,
            "material_count": self.material_count,
        }


class MaterialAvailabilityService:
    """Derived view. Writes nothing. No AI."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def for_content_codes(self, content_codes: list[str]) -> dict[str, MaterialAvailability]:
        codes = sorted({c for c in content_codes if c})
        out = {c: MaterialAvailability(c, False, 0) for c in codes}
        if not codes:
            return out

        node_rows = (await self._session.execute(
            select(CatalogNode.id, CatalogNode.code).where(CatalogNode.code.in_(codes))
        )).all()
        id_to_code = {nid: code for nid, code in node_rows}
        if not id_to_code:
            return out

        node_ids = list(id_to_code)
        counts: dict[str, set] = {c: set() for c in codes}

        # material-level association
        mat_rows = (await self._session.execute(
            select(TheoryMaterial.primary_content_node_id, TheoryMaterialVersion.material_id)
            .join(TheoryMaterialVersion, TheoryMaterialVersion.material_id == TheoryMaterial.id)
            .where(
                TheoryMaterialVersion.status == PUBLISHED,
                TheoryMaterial.primary_content_node_id.in_(node_ids),
            )
        )).all()
        for node_id, material_id in mat_rows:
            code = id_to_code.get(node_id)
            if code is not None:
                counts[code].add(material_id)

        # section-level association
        sec_rows = (await self._session.execute(
            select(MaterialSection.content_node_id, TheoryMaterialVersion.material_id)
            .join(TheoryMaterialVersion, TheoryMaterialVersion.id == MaterialSection.material_version_id)
            .where(
                TheoryMaterialVersion.status == PUBLISHED,
                MaterialSection.content_node_id.in_(node_ids),
            )
        )).all()
        for node_id, material_id in sec_rows:
            code = id_to_code.get(node_id)
            if code is not None:
                counts[code].add(material_id)

        for code in codes:
            n = len(counts[code])
            out[code] = MaterialAvailability(code, n > 0, n)
        return out

    async def for_content_code(self, content_code: str) -> MaterialAvailability:
        return (await self.for_content_codes([content_code]))[content_code]

    # ---- PHASE 25: tenant-aware resolution for STUDENT-facing consumers ----
    #
    # for_content_codes() above answers "does a published material exist at
    # all" - useful for staff-facing signals, but NOT safe to show a specific
    # student: it does not check TheoryMaterial.visibility_scope/school_id, so
    # a PRIVATE or another school's SCHOOL-scoped material would count. This
    # method is the one PHASE 21 (Trilha) / PHASE 24 (Study Session) / the
    # student material endpoints must use.
    #
    # Rule (deterministic, fail-closed):
    #   PUBLIC material            -> visible to every student.
    #   SCHOOL material            -> visible only when requester_school_id
    #                                  matches TheoryMaterial.school_id.
    #   PRIVATE / CLASS / STUDENT  -> never counted here. CLASS/STUDENT are
    #                                  reserved values (spec s2/s6) the current
    #                                  schema has no classroom/student targeting
    #                                  column to verify against yet - failing
    #                                  closed (never shown) is the safe choice
    #                                  until that column exists.
    # An independent student (requester_school_id=None) only ever sees PUBLIC.
    async def resolve_for_content(
        self, content_codes: list[str], *, requester_school_id: str | None = None,
    ) -> dict[str, "MaterialResolution"]:
        codes = sorted({c for c in content_codes if c})
        out = {c: MaterialResolution(c, False, 0, None, None) for c in codes}
        if not codes:
            return out

        # SQLAlchemy's Uuid column type is strict under SQLite (raises
        # AttributeError on a plain str bind param) even though PostgreSQL
        # tolerates it - coerce to a real uuid.UUID, same idiom used
        # throughout study_session.py / adaptive_practice.py.
        school_uuid = UUID(str(requester_school_id)) if requester_school_id else None

        node_rows = (await self._session.execute(
            select(CatalogNode.id, CatalogNode.code).where(CatalogNode.code.in_(codes))
        )).all()
        id_to_code = {nid: code for nid, code in node_rows}
        if not id_to_code:
            return out
        node_ids = list(id_to_code)

        visible = (TheoryMaterial.visibility_scope == "PUBLIC") | (
            (TheoryMaterial.visibility_scope == "SCHOOL")
            & (TheoryMaterial.school_id == school_uuid)
        ) if school_uuid else (TheoryMaterial.visibility_scope == "PUBLIC")

        ids_by_code: dict[str, set] = {c: set() for c in codes}
        titles: dict = {}

        mat_rows = (await self._session.execute(
            select(TheoryMaterial.id, TheoryMaterial.primary_content_node_id, TheoryMaterial.title)
            .join(TheoryMaterialVersion, TheoryMaterialVersion.material_id == TheoryMaterial.id)
            .where(
                TheoryMaterialVersion.status == PUBLISHED,
                TheoryMaterial.primary_content_node_id.in_(node_ids),
                visible,
            )
        )).all()
        for material_id, node_id, title in mat_rows:
            code = id_to_code.get(node_id)
            if code is not None:
                ids_by_code[code].add(material_id)
                titles[material_id] = title

        sec_rows = (await self._session.execute(
            select(TheoryMaterial.id, MaterialSection.content_node_id, TheoryMaterial.title)
            .join(TheoryMaterialVersion, TheoryMaterialVersion.id == MaterialSection.material_version_id)
            .join(TheoryMaterial, TheoryMaterial.id == TheoryMaterialVersion.material_id)
            .where(
                TheoryMaterialVersion.status == PUBLISHED,
                MaterialSection.content_node_id.in_(node_ids),
                visible,
            )
        )).all()
        for material_id, node_id, title in sec_rows:
            code = id_to_code.get(node_id)
            if code is not None:
                ids_by_code[code].add(material_id)
                titles[material_id] = title

        for code in codes:
            ids = ids_by_code[code]
            if not ids:
                continue
            chosen = sorted(ids, key=str)[0]   # deterministic tiebreak, no AI
            out[code] = MaterialResolution(
                content_code=code, material_available=True, material_count=len(ids),
                material_id=str(chosen), material_title=titles.get(chosen))
        return out

    async def resolve_for_one(self, content_code: str, *,
                              requester_school_id: str | None = None) -> "MaterialResolution":
        return (await self.resolve_for_content(
            [content_code], requester_school_id=requester_school_id))[content_code]

    @staticmethod
    def visible_to_student(material: TheoryMaterial, *, requester_school_id: str | None) -> bool:
        """Same fail-closed rule as resolve_for_content(), applied to one
        already-loaded TheoryMaterial row (student-facing endpoints s6/s20:
        PUBLIC always; SCHOOL only when requester_school_id matches;
        PRIVATE/CLASS/STUDENT never - no classroom/student targeting column
        exists yet to verify against)."""
        scope = (material.visibility_scope or "PRIVATE").upper()
        if scope == "PUBLIC":
            return True
        if scope == "SCHOOL":
            if not requester_school_id or material.school_id is None:
                return False
            return UUID(str(material.school_id)) == UUID(str(requester_school_id))
        return False


@dataclass(frozen=True)
class MaterialResolution:
    content_code: str
    material_available: bool
    material_count: int
    material_id: str | None
    material_title: str | None

    def as_dict(self) -> dict:
        return {
            "content_code": self.content_code,
            "material_available": self.material_available,
            "material_count": self.material_count,
            "material_id": self.material_id,
            "material_title": self.material_title,
        }


__all__ = ["MaterialAvailability", "MaterialAvailabilityService", "MaterialResolution"]
