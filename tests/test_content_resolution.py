"""Unit tests for canonical content text-to-ID resolution within PedagogicalUniverse."""

import unittest
import uuid

from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, PedagogicalUniverse, PedagogicalUniverseCatalogScope
from agente_ia_edu.services.pedagogical_universe import PedagogicalUniverseService


class ContentResolutionTests(unittest.IsolatedAsyncioTestCase):
    """Test cases for text-to-node resolution."""
    
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:")
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        
        SessionLocal = sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        self.session = SessionLocal()
        
    async def asyncTearDown(self):
        await self.session.close()
        await self.engine.dispose()
    
    async def _setup_universe_with_content(self):
        """Create a test universe with Química (area) -> Equilíbrio (content) and Pré-requisito."""
        # AREA
        quimica = CatalogNode(
            id=uuid.uuid4(), node_type="AREA", name="Química", 
            description="Ciências Naturais - Química", active=True
        )
        quimica.root_id = quimica.id
        self.session.add(quimica)
        await self.session.flush()
        
        # CONTENT under AREA
        equilibrio = CatalogNode(
            id=uuid.uuid4(), node_type="CONTENT", name="Equilíbrio", 
            parent_id=quimica.id, root_id=quimica.id, active=True
        )
        self.session.add(equilibrio)
        await self.session.flush()
        
        # PREREQUISITE under CONTENT
        prerequisito = CatalogNode(
            id=uuid.uuid4(), node_type="PREREQUISITE", name="Pré-requisito",
            parent_id=equilibrio.id, root_id=quimica.id, active=True
        )
        self.session.add(prerequisito)
        await self.session.flush()
        
        # Another discipline to test boundaries
        matematica = CatalogNode(
            id=uuid.uuid4(), node_type="AREA", name="Matemática",
            description="Ciências Exatas - Matemática", active=True
        )
        matematica.root_id = matematica.id
        self.session.add(matematica)
        await self.session.flush()
        
        funcoes = CatalogNode(
            id=uuid.uuid4(), node_type="CONTENT", name="Funções",
            parent_id=matematica.id, root_id=matematica.id, active=True
        )
        self.session.add(funcoes)
        await self.session.flush()
        
        # Create universe authorized for Química only
        universe = PedagogicalUniverse(
            id=uuid.uuid4(), external_id="univ-quimica-001", slug="quimica-2026",
            name="Universo Química 2026", owner_type="PARTNER", owner_external_id="partner-001",
            status="ACTIVE", configuration_version="v1"
        )
        self.session.add(universe)
        await self.session.flush()
        
        # Authorize AREA scope for Química (includes descendants)
        scope = PedagogicalUniverseCatalogScope(
            id=uuid.uuid4(), universe_id=universe.id, catalog_node_id=quimica.id,
            scope_kind="AREA", include_descendants=True
        )
        self.session.add(scope)
        await self.session.commit()
        
        return universe, quimica, equilibrio, prerequisito, matematica, funcoes
    
    async def test_a_resolve_exact_match_within_universe(self):
        """A. 'Equilíbrio' resolves to correct CatalogNode within universe."""
        universe, quimica, equilibrio, _, _, _ = await self._setup_universe_with_content()
        service = PedagogicalUniverseService(self.session)
        
        result = await service.resolve_preferred_content_node(universe.id, "Equilíbrio")
        
        self.assertEqual(result["status"], "RESOLVED")
        self.assertEqual(result["node_id"], str(equilibrio.id))
        self.assertEqual(result["name"], "Equilíbrio")
        self.assertEqual(result["content_text"], "Equilíbrio")
    
    async def test_b_resolve_normalized_lowercase(self):
        """B. 'equilibrio' (lowercase) resolves to same node via normalization."""
        universe, quimica, equilibrio, _, _, _ = await self._setup_universe_with_content()
        service = PedagogicalUniverseService(self.session)
        
        result = await service.resolve_preferred_content_node(universe.id, "equilibrio")
        
        self.assertEqual(result["status"], "RESOLVED")
        self.assertEqual(result["node_id"], str(equilibrio.id))
        self.assertEqual(result["name"], "Equilíbrio")
    
    async def test_c_resolve_nonexistent_content(self):
        """C. Nonexistent content results in NOT_FOUND."""
        universe, _, _, _, _, _ = await self._setup_universe_with_content()
        service = PedagogicalUniverseService(self.session)
        
        result = await service.resolve_preferred_content_node(universe.id, "Reações Nucleares")
        
        self.assertEqual(result["status"], "NOT_FOUND")
        self.assertIsNone(result["node_id"])
        self.assertEqual(result["content_text"], "Reações Nucleares")
    
    async def test_d_resolve_ambiguous_multiple_matches(self):
        """D. Ambiguous matches (multiple nodes with same name) return AMBIGUOUS."""
        universe, quimica, _, _, _, _ = await self._setup_universe_with_content()
        service = PedagogicalUniverseService(self.session)
        
        # Add another node with same name
        duplicate = CatalogNode(
            id=uuid.uuid4(), node_type="CONTENT", name="Equilíbrio",
            parent_id=quimica.id, root_id=quimica.id, active=True
        )
        self.session.add(duplicate)
        await self.session.commit()
        
        result = await service.resolve_preferred_content_node(universe.id, "Equilíbrio")
        
        self.assertEqual(result["status"], "AMBIGUOUS")
        self.assertIsNone(result["node_id"])
    
    async def test_e_resolve_content_outside_universe(self):
        """E. Content existing but outside PedagogicalUniverse is NOT_FOUND."""
        universe, _, _, _, matematica, funcoes = await self._setup_universe_with_content()
        service = PedagogicalUniverseService(self.session)
        
        # Try to resolve Matemática content from Química universe
        result = await service.resolve_preferred_content_node(universe.id, "Funções")
        
        self.assertEqual(result["status"], "NOT_FOUND")
        self.assertIsNone(result["node_id"])
    
    async def test_f_resolve_empty_or_none_content(self):
        """F. Empty or None content preference returns NOT_PROVIDED."""
        universe, _, _, _, _, _ = await self._setup_universe_with_content()
        service = PedagogicalUniverseService(self.session)
        
        result_none = await service.resolve_preferred_content_node(universe.id, None)
        self.assertEqual(result_none["status"], "NOT_PROVIDED")
        
        result_empty = await service.resolve_preferred_content_node(universe.id, "")
        self.assertEqual(result_empty["status"], "NOT_PROVIDED")
        
        result_whitespace = await service.resolve_preferred_content_node(universe.id, "   ")
        self.assertEqual(result_whitespace["status"], "NOT_PROVIDED")


if __name__ == "__main__":
    unittest.main()
