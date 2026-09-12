"""HTTP E2E test: diagnostic with preferred content resolution."""

import asyncio
import unittest
from uuid import UUID

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.diagnostic import diagnostic_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode, ContentQuestionLink, InitialDiagnostic, PedagogicalUniverse,
    PedagogicalUniverseBinding, PedagogicalUniverseCatalogScope,
    Question, QuestionOption, QuestionVersion
)
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.pedagogical_universe import PedagogicalUniverseService


class PreferredContentE2E(unittest.TestCase):
    """HTTP E2E: entry → save profile with content preference → verify resolution."""
    
    def setUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        asyncio.run(self._create_schema())
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        self.identity = ExternalIdentityContext(provider="test", external_user_id="student-pref")
        self.app = FastAPI()
        self.app.include_router(diagnostic_router)

        async def current_identity():
            return self.identity

        self.app.dependency_overrides[get_current_identity] = current_identity
        self.app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.client = TestClient(self.app)
    
    def tearDown(self):
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())
    
    async def _create_schema(self):
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
    
    async def _seed_question(self, session, content_node, name, difficulty="EASY"):
        """Helper to create a question linked to content."""
        question = Question(validation_status="approved", status="PUBLISHED", visibility_scope="PUBLIC")
        session.add(question)
        await session.flush()
        
        version = QuestionVersion(
            question_id=question.id, version_kind="official_original",
            canonical_text=name, content_hash=name,
            recommended_difficulty=difficulty
        )
        session.add(version)
        await session.flush()
        
        session.add(QuestionOption(
            question_version_id=version.id, option_key="A",
            position=1, text="Correct", is_valid_option=True
        ))
        session.add(ContentQuestionLink(
            content_node_id=content_node.id,
            question_version_id=version.id
        ))
        return version
    
    async def _setup_universe_with_preferred_content(self):
        """Create: Química (AREA) → Equilíbrio + Termoquímica with questions."""
        async with self.factory() as session:
            # Create Química AREA (root)
            quimica = CatalogNode(
                node_type="AREA", name="Química", position=1, active=True
            )
            session.add(quimica)
            await session.flush()
            quimica.root_id = quimica.id
            
            # Create Equilíbrio CONTENT
            equilibrio = CatalogNode(
                parent_id=quimica.id, root_id=quimica.id,
                node_type="CONTENT", name="Equilíbrio", position=1, active=True
            )
            session.add(equilibrio)
            await session.flush()
            
            # Create Termoquímica CONTENT
            termoquimica = CatalogNode(
                parent_id=quimica.id, root_id=quimica.id,
                node_type="CONTENT", name="Termoquímica", position=2, active=True
            )
            session.add(termoquimica)
            await session.flush()
            
            # Create 3 questions in Equilíbrio
            for i in range(3):
                await self._seed_question(session, equilibrio, f"Equilíbrio Question {i+1}")
            
            # Create 2 questions in Termoquímica
            for i in range(2):
                await self._seed_question(session, termoquimica, f"Termoquímica Question {i+1}")
            
            # Create universe
            universe = PedagogicalUniverse(
                external_id="univ-pref-test", slug="pref-test",
                name="Preferred Content Test", owner_type="PLATFORM", status="ACTIVE"
            )
            session.add(universe)
            await session.flush()
            
            # Authorize Química AREA
            scope = PedagogicalUniverseCatalogScope(
                universe_id=universe.id, catalog_node_id=quimica.id,
                scope_kind="AREA", include_descendants=True
            )
            session.add(scope)
            
            # Bind to student
            binding = PedagogicalUniverseBinding(
                universe_id=universe.id,
                subject_type="EXTERNAL_IDENTITY", subject_external_id="student-pref",
                active=True, priority=10
            )
            session.add(binding)
            await session.commit()
            return str(universe.id), str(equilibrio.id), str(termoquimica.id)
    
    def test_preferred_content_resolution_persists(self):
        """HTTP E2E: profile with content='Equilíbrio' → resolution is RESOLVED."""
        universe_id, eq_id, tq_id = asyncio.run(self._setup_universe_with_preferred_content())
        
        # 1. Start diagnostic
        start = self.client.post(
            "/api/v1/student/diagnostic/entry/start",
            json={"requested_universe_id": universe_id}
        )
        self.assertEqual(start.status_code, 201)
        diagnostic_id = start.json()["diagnostic_id"]
        
        # 2. Save profile with Equilíbrio preference
        entry = self.client.put(
            f"/api/v1/student/diagnostic/{diagnostic_id}/entry",
            json={
                "diagnostic_mode": "GLOBAL",
                "content": "Equilíbrio",
                "complete": True
            }
        )
        self.assertEqual(entry.status_code, 200)
        entry_data = entry.json()
        
        # Verify resolution cached and resolved
        self.assertIn("preferred_content_resolution", entry_data)
        resolution = entry_data["preferred_content_resolution"]
        self.assertEqual(resolution["status"], "RESOLVED")
        self.assertEqual(resolution["name"], "Equilíbrio")
        self.assertEqual(resolution["node_id"], eq_id)


if __name__ == "__main__":
    unittest.main()


