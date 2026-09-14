"""The discipline gate.

``pedagogical_universes`` already had the exact shape this needed and was
consumed by two services out of five, so the mechanism existed and restricted
nothing. This completes it rather than introducing a second notion of scope
that would eventually disagree with the first.

Its whole reason to exist is one rule from the spec, section 6: absence of a
universe means access to everything; restriction exists only where somebody
declared it. ``PedagogicalUniverseService.resolve_active_universe`` raises on
absence, which is right for choosing among authorized universes and wrong as a
gate - nothing here is built on it.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy.ext.asyncio import AsyncSession

from .pedagogical_universe import PedagogicalUniverseService


@dataclass(frozen=True)
class DisciplineScope:
    """What a school is allowed to see, resolved once per request."""

    unrestricted: bool
    allowed_node_ids: frozenset[uuid.UUID]
    allowed_codes: frozenset[str]

    @classmethod
    def unrestricted_scope(cls) -> "DisciplineScope":
        return cls(
            unrestricted=True, allowed_node_ids=frozenset(), allowed_codes=frozenset()
        )

    def permits(self, catalog_node_id: uuid.UUID | None) -> bool:
        # Checked before ``unrestricted``, on purpose: every school in
        # production is unrestricted today, so a type check placed after that
        # early return would never fire and the caller's bug would surface
        # only on the first school that declares a scope.
        if catalog_node_id is not None and not isinstance(catalog_node_id, uuid.UUID):
            # Returning False here would be the cheapest-looking option and the
            # most expensive one: denying access with no symptom is the exact
            # failure this gate exists to avoid, and a caller handing over a
            # stringified id would never learn. Raising does not deny anyone in
            # silence; it makes the caller's mistake immediate. Callers holding
            # text convert it themselves (``study_search._as_node_id``).
            raise TypeError(
                "catalog_node_id must be uuid.UUID or None, got "
                f"{type(catalog_node_id).__name__}"
            )
        if self.unrestricted:
            return True
        if catalog_node_id is None:
            # Unclassified content is not evidence of another discipline, and
            # hiding it would keep it from the only people who can classify it.
            return True
        return catalog_node_id in self.allowed_node_ids

    def permits_code(self, catalog_code: str | None) -> bool:
        """Same rule, for consumers that hold a catalog code rather than an id.

        ``pedagogical_classifications.content`` is one of those: it stores the
        code as text, with no foreign key to ``catalog_nodes``.

        The wrong-type check is the same as ``permits``, for the same reason,
        but it fixes nothing that was broken: a non-string already blew up on
        ``.strip()`` with an ``AttributeError``, so this method never denied
        anyone in silence. It raises a named contract instead of an accident.
        """
        if catalog_code is not None and not isinstance(catalog_code, str):
            raise TypeError(
                "catalog_code must be str or None, got "
                f"{type(catalog_code).__name__}"
            )
        if self.unrestricted:
            return True
        if not (catalog_code or "").strip():
            return True
        return catalog_code in self.allowed_codes


class DisciplineGate:
    def __init__(self, session: AsyncSession):
        self._universes = PedagogicalUniverseService(session)

    async def scope_for_school(
        self, school_id: str | uuid.UUID | None
    ) -> DisciplineScope:
        if school_id is None:
            return DisciplineScope.unrestricted_scope()

        universe_ids = await self._universes.active_school_universe_ids(str(school_id))
        if not universe_ids:
            return DisciplineScope.unrestricted_scope()

        allowed = await self._universes.expand_catalog_scope_nodes(universe_ids)
        if not allowed:
            # A universe that declares no catalog scope restricts nothing.
            # Intersecting with the empty set would block everything instead.
            return DisciplineScope.unrestricted_scope()

        return DisciplineScope(
            unrestricted=False,
            allowed_node_ids=allowed,
            allowed_codes=await self._universes.catalog_codes_for(allowed),
        )


__all__ = ["DisciplineGate", "DisciplineScope"]
