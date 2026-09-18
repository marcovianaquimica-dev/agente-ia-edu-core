from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Iterable, Sequence

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import AdminAuditLog, CatalogNode, ContentQuestionLink, PedagogicalUniverse, PedagogicalUniverseAcademicScope, PedagogicalUniverseBinding, PedagogicalUniverseCatalogScope
from agente_ia_edu.identity import ExternalIdentityContext


class PedagogicalUniverseService:
    CATALOG_SCOPE_KINDS = {"AREA", "DISCIPLINE", "CONTENT"}

    def __init__(self, session: AsyncSession):
        self.session = session

    async def create_universe(self, *, external_id: str, slug: str, name: str, owner_type: str, owner_external_id: str | None, performed_by_external_id: str, description: str | None = None, configuration: dict | None = None, configuration_version: str = "v1", status: str = "DRAFT") -> PedagogicalUniverse:
        universe = PedagogicalUniverse(
            external_id=external_id.strip(), slug=slug.strip().lower(), name=name.strip(), description=description,
            owner_type=owner_type.upper(), owner_external_id=owner_external_id, status=status.upper(),
            configuration=configuration, configuration_version=configuration_version,
        )
        self.session.add(universe)
        await self.session.flush()
        await self._audit(performed_by_external_id, "PEDAGOGICAL_UNIVERSE_CREATED", universe, {"external_id": universe.external_id})
        await self.session.commit()
        await self.session.refresh(universe)
        return universe

    async def update_configuration(self, *, universe_id: uuid.UUID, configuration: dict, configuration_version: str, performed_by_external_id: str) -> PedagogicalUniverse:
        universe = await self.require_universe(universe_id)
        if universe.status == "ARCHIVED":
            raise ValueError("Archived universe cannot be updated")
        previous = {"configuration_version": universe.configuration_version, "configuration": universe.configuration}
        universe.configuration = configuration
        universe.configuration_version = configuration_version
        universe.updated_at = datetime.now(timezone.utc)
        await self._audit(performed_by_external_id, "PEDAGOGICAL_UNIVERSE_CONFIGURATION_UPDATED", universe, {"previous": previous, "current": {"configuration_version": configuration_version, "configuration": configuration}})
        await self.session.commit()
        await self.session.refresh(universe)
        return universe

    async def add_catalog_scope(self, *, universe_id: uuid.UUID, catalog_node_id: uuid.UUID, scope_kind: str, include_descendants: bool = True) -> PedagogicalUniverseCatalogScope:
        await self.require_universe(universe_id)
        norm_scope_kind = scope_kind.upper()
        if norm_scope_kind not in self.CATALOG_SCOPE_KINDS:
            raise ValueError(f"Invalid scope_kind: {scope_kind}")
        if not await self.session.get(CatalogNode, catalog_node_id):
            raise ValueError("Catalog node not found")
        scope = PedagogicalUniverseCatalogScope(universe_id=universe_id, catalog_node_id=catalog_node_id, scope_kind=norm_scope_kind, include_descendants=include_descendants)
        self.session.add(scope)
        await self.session.commit()
        await self.session.refresh(scope)
        return scope

    async def list_catalog_scopes(self, universe_id: uuid.UUID) -> list[PedagogicalUniverseCatalogScope]:
        return list((await self.session.execute(
            select(PedagogicalUniverseCatalogScope).where(PedagogicalUniverseCatalogScope.universe_id == universe_id)
        )).scalars().all())

    async def add_academic_scope(self, *, universe_id: uuid.UUID, segment: str | None = None, grade_level: str | None = None, unit_id: str | None = None) -> PedagogicalUniverseAcademicScope:
        await self.require_universe(universe_id)
        scope = PedagogicalUniverseAcademicScope(universe_id=universe_id, segment=segment, grade_level=grade_level, unit_id=unit_id)
        self.session.add(scope)
        await self.session.commit()
        await self.session.refresh(scope)
        return scope

    async def bind(self, *, universe_id: uuid.UUID, subject_type: str, subject_external_id: str, priority: int = 0) -> PedagogicalUniverseBinding:
        await self.require_universe(universe_id)
        binding = PedagogicalUniverseBinding(universe_id=universe_id, subject_type=subject_type.upper(), subject_external_id=subject_external_id, priority=priority)
        self.session.add(binding)
        await self.session.commit()
        await self.session.refresh(binding)
        return binding

    async def list_universes(self) -> list[PedagogicalUniverse]:
        return list((await self.session.execute(select(PedagogicalUniverse).order_by(PedagogicalUniverse.slug))).scalars().all())

    async def set_status(self, *, universe_id: uuid.UUID, status: str, performed_by_external_id: str) -> PedagogicalUniverse:
        universe = await self.require_universe(universe_id)
        target = status.upper()
        if target not in {"DRAFT", "ACTIVE", "ARCHIVED"}:
            raise ValueError("Invalid pedagogical universe status")
        previous = universe.status
        universe.status = target
        universe.updated_at = datetime.now(timezone.utc)
        await self._audit(performed_by_external_id, "PEDAGOGICAL_UNIVERSE_STATUS_UPDATED", universe, {"previous": previous, "current": target})
        await self.session.commit()
        await self.session.refresh(universe)
        return universe

    async def remove_catalog_scope(self, *, scope_id: uuid.UUID, performed_by_external_id: str) -> None:
        scope = await self.session.get(PedagogicalUniverseCatalogScope, scope_id)
        if not scope:
            raise ValueError("Pedagogical universe catalog scope not found")
        universe = await self.require_universe(scope.universe_id)
        await self._audit(performed_by_external_id, "PEDAGOGICAL_UNIVERSE_CATALOG_SCOPE_REMOVED", universe, {"catalog_node_id": str(scope.catalog_node_id)})
        await self.session.delete(scope)
        await self.session.commit()

    async def remove_binding(self, *, binding_id: uuid.UUID, performed_by_external_id: str) -> None:
        binding = await self.session.get(PedagogicalUniverseBinding, binding_id)
        if not binding:
            raise ValueError("Pedagogical universe binding not found")
        universe = await self.require_universe(binding.universe_id)
        await self._audit(performed_by_external_id, "PEDAGOGICAL_UNIVERSE_BINDING_REMOVED", universe, {"subject_type": binding.subject_type, "subject_external_id": binding.subject_external_id})
        await self.session.delete(binding)
        await self.session.commit()

    async def authorized_universes(self, identity: ExternalIdentityContext) -> list[PedagogicalUniverse]:
        subjects = [("EXTERNAL_IDENTITY", identity.external_user_id)]
        if identity.institution_id:
            subjects.append(("SCHOOL", identity.institution_id))
        product_context = identity.metadata.get("product_context")
        if product_context:
            subjects.append(("PRODUCT_CONTEXT", str(product_context)))
        conditions = [
            (PedagogicalUniverseBinding.subject_type == kind) & (PedagogicalUniverseBinding.subject_external_id == value)
            for kind, value in subjects
        ]
        result = await self.session.execute(
            select(PedagogicalUniverse).join(PedagogicalUniverseBinding).where(
                PedagogicalUniverse.status == "ACTIVE", PedagogicalUniverseBinding.active.is_(True), or_(*conditions)
            ).order_by(PedagogicalUniverseBinding.priority.desc(), PedagogicalUniverse.default_for_context.desc(), PedagogicalUniverse.slug)
        )
        return list(result.scalars().unique().all())

    async def resolve_active_universe(self, identity: ExternalIdentityContext, requested_universe_id: uuid.UUID | None = None) -> PedagogicalUniverse:
        universes = await self.authorized_universes(identity)
        if requested_universe_id:
            universe = next((item for item in universes if item.id == requested_universe_id), None)
            if universe is None:
                raise PermissionError("Requested pedagogical universe is not authorized")
            return universe
        if not universes:
            raise PermissionError("No authorized pedagogical universe")
        return universes[0]

    async def contains_catalog_node(self, universe_id: uuid.UUID, catalog_node_id: uuid.UUID) -> bool:
        scopes = list((await self.session.execute(select(PedagogicalUniverseCatalogScope).where(PedagogicalUniverseCatalogScope.universe_id == universe_id))).scalars().all())
        node = await self.session.get(CatalogNode, catalog_node_id)
        if not node:
            return False
        for scope in scopes:
            if scope.catalog_node_id == node.id:
                return True
            if scope.include_descendants and await self._is_descendant(node, scope.catalog_node_id):
                return True
        return False

    async def contains_question_version(self, universe_id: uuid.UUID, question_version_id: uuid.UUID) -> bool:
        node_ids = list((await self.session.execute(
            select(ContentQuestionLink.content_node_id).where(
                ContentQuestionLink.question_version_id == question_version_id,
            )
        )).scalars().all())
        for node_id in node_ids:
            if await self.contains_catalog_node(universe_id, node_id):
                return True
        return False

    async def require_universe(self, universe_id: uuid.UUID) -> PedagogicalUniverse:
        universe = await self.session.get(PedagogicalUniverse, universe_id)
        if not universe:
            raise ValueError("Pedagogical universe not found")
        return universe

    async def active_school_universe_ids(self, school_id: str) -> list[uuid.UUID]:
        """Ids of the ACTIVE universes owned by this school.

        DRAFT and ARCHIVED are excluded on purpose: a universe that is still
        being configured must not restrict anyone yet.
        """
        result = await self.session.execute(
            select(PedagogicalUniverse.id).where(
                PedagogicalUniverse.owner_type == "SCHOOL",
                PedagogicalUniverse.owner_external_id == str(school_id),
                PedagogicalUniverse.status == "ACTIVE",
            )
        )
        return list(result.scalars().all())

    async def expand_catalog_scope_nodes(
        self, universe_ids: Sequence[uuid.UUID]
    ) -> frozenset[uuid.UUID]:
        """Every catalog node these universes reach, descendants included.

        Expanded eagerly, once, so callers can test membership against a set
        instead of issuing a query per node. That is what makes the gate cheap
        enough to apply inside a paginated SQL query.
        """
        if not universe_ids:
            return frozenset()
        scopes = list(
            (
                await self.session.execute(
                    select(PedagogicalUniverseCatalogScope).where(
                        PedagogicalUniverseCatalogScope.universe_id.in_(list(universe_ids))
                    )
                )
            )
            .scalars()
            .all()
        )
        reachable: set[uuid.UUID] = set()
        for scope in scopes:
            reachable.add(scope.catalog_node_id)
            if scope.include_descendants:
                reachable.update(await self._collect_descendants(scope.catalog_node_id))
        return frozenset(reachable)

    async def catalog_codes_for(
        self, node_ids: Iterable[uuid.UUID]
    ) -> frozenset[str]:
        """The codes of these nodes, minus the ones that have no usable code.

        ``pedagogical_classifications`` stores the content CODE as text rather
        than a foreign key, so a consumer filtering classifications needs codes,
        not ids. Translating once here keeps every consumer from inventing its
        own version of this.

        ``catalog_nodes.code`` is nullable, so a node inside a school's scope
        can carry no code at all. Dropping those here - rather than leaving a
        ``None`` in a set annotated ``frozenset[str]`` - is what keeps the
        return type honest: a consumer that sorts the codes to build a SQL
        ``IN`` would otherwise raise ``TypeError`` comparing ``str`` to
        ``None``. A node with no code cannot match any classification anyway,
        so dropping it removes nothing a consumer could have used.
        """
        ids = list(node_ids)
        if not ids:
            return frozenset()
        result = await self.session.execute(
            select(CatalogNode.code).where(
                CatalogNode.id.in_(ids), CatalogNode.code.is_not(None)
            )
        )
        return frozenset(
            code for code in result.scalars().all() if (code or "").strip()
        )

    async def _is_descendant(self, node: CatalogNode, ancestor_id: uuid.UUID) -> bool:
        current = node
        while current.parent_id:
            if current.parent_id == ancestor_id:
                return True
            current = await self.session.get(CatalogNode, current.parent_id)
            if current is None:
                return False
        return False

    async def resolve_preferred_content_node(
        self, universe_id: uuid.UUID, content_text: str | None
    ) -> dict[str, Any]:
        """Resolve free-form content text to a canonical CatalogNode.id within the universe.

        Returns dict with 'status', 'node_id', 'name', 'content_text' for audit/snapshot.
        Status: RESOLVED, NOT_FOUND, AMBIGUOUS.
        """
        import unicodedata
        
        def normalize_text(text: str) -> str:
            """Normalize text: lowercase, strip accents."""
            nfkd = unicodedata.normalize('NFKD', text.strip().lower())
            return ''.join(c for c in nfkd if unicodedata.category(c) != 'Mn')
        
        if not content_text or not content_text.strip():
            return {"status": "NOT_PROVIDED", "node_id": None, "name": None, "content_text": None}
        
        universe = await self.require_universe(universe_id)
        normalized = normalize_text(content_text)
        scopes = list((await self.session.execute(
            select(PedagogicalUniverseCatalogScope).where(
                PedagogicalUniverseCatalogScope.universe_id == universe_id
            )
        )).scalars().all())
        
        authorized_node_ids = set()
        for scope in scopes:
            authorized_node_ids.add(scope.catalog_node_id)
            if scope.include_descendants:
                descendants = await self._collect_descendants(scope.catalog_node_id)
                authorized_node_ids.update(descendants)
        
        candidates = list((await self.session.execute(
            select(CatalogNode).where(
                CatalogNode.id.in_(authorized_node_ids),
                CatalogNode.active.is_(True),
            )
        )).scalars().all())
        
        matches = [node for node in candidates if normalize_text(node.name) == normalized]
        if len(matches) == 1:
            return {"status": "RESOLVED", "node_id": str(matches[0].id), "name": matches[0].name, "content_text": content_text}
        elif len(matches) > 1:
            return {"status": "AMBIGUOUS", "node_id": None, "name": None, "content_text": content_text}
        else:
            return {"status": "NOT_FOUND", "node_id": None, "name": None, "content_text": content_text}
    
    async def _collect_descendants(self, node_id: uuid.UUID) -> list[uuid.UUID]:
        """Recursively collect all descendants of a node."""
        from sqlalchemy import select
        
        descendants = []
        queue = [node_id]
        visited = set()
        
        while queue:
            current_id = queue.pop(0)
            if current_id in visited:
                continue
            visited.add(current_id)
            
            children = list((await self.session.execute(
                select(CatalogNode).where(CatalogNode.parent_id == current_id)
            )).scalars().all())
            
            for child in children:
                descendants.append(child.id)
                queue.append(child.id)
        
        return descendants

    async def _audit(self, actor: str, action: str, universe: PedagogicalUniverse, metadata: dict) -> None:
        self.session.add(AdminAuditLog(performed_by_external_id=actor, action=action, entity_type="PEDAGOGICAL_UNIVERSE", entity_id=str(universe.id), school_id=uuid.UUID(universe.owner_external_id) if universe.owner_type == "SCHOOL" and universe.owner_external_id else None, metadata_=metadata))