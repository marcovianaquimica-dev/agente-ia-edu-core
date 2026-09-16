# tests/test_r0_pedagogical_context_route_authorization.py
"""GET /api/v1/pedagogical/context/{classroom_id} injected identity and never
read it. Any authenticated caller read any school's pedagogical context by
varying the school_id query parameter. The neighbouring route in this same
file, get_teacher_lesson, already solves this for a different resource by
calling verify_teacher_classroom_scope - this closes the same gap here.

Called directly as a plain async function, with the same arguments FastAPI's
Depends would inject, rather than through TestClient: no existing fixture
wires this router with an identity override, and building one just for this
route would be unneeded machinery around a function that already takes
identity as a parameter.
"""

import unittest
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.routes.teaching_context import get_classroom_pedagogical_context
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import School
from agente_ia_edu.db.models.catalog import CatalogNode
from agente_ia_edu.db.models.recommendations import PedagogicalContext
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService


class PedagogicalContextRouteAuthorizationTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _two_schools(self, session):
        school_a = School(id=uuid.uuid4(), code="SCH-A", name="school-a")
        school_b = School(id=uuid.uuid4(), code="SCH-B", name="school-b")
        session.add_all([school_a, school_b])
        await session.commit()
        return school_a, school_b

    async def test_a_stranger_to_the_school_is_denied(self):
        """The reported shape exactly: a real link at School A, a request for
        School B's classroom, no relationship between the two."""
        async with self.session_factory() as session:
            school_a, school_b = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="stranger",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school_a.id,
                scope_external_id="TURMA-A1",
            )

        identity = ExternalIdentityContext(provider="test", external_user_id="stranger")
        with self.assertRaises(HTTPException) as ctx:
            await get_classroom_pedagogical_context(
                classroom_id="QUALQUER-TURMA-DE-B",
                school_id=school_b.id,
                academic_year="2026",
                identity=identity,
                session_factory=self.session_factory,
            )
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_an_unlinked_identity_outside_the_dev_fallback_is_denied(self):
        """Not just cross-school - no link at all must also be denied, not
        silently pass because there was nothing to compare against.

        Uses an identity in the shape verify_teacher_classroom_scope's old
        dev/test fallback used to match (teacher_id starting with "teacher:",
        or "prof_mendes") - that fallback used to grant access to ANY
        school/classroom unconditionally. It was removed in commit b9f59d9
        on this branch, so this identity is now denied through the same
        "no active school bindings" path as any other unlinked identity.
        """
        identity = ExternalIdentityContext(provider="test", external_user_id="nobody-at-all")
        async with self.session_factory() as session:
            _, school_b = await self._two_schools(session)

        with self.assertRaises(HTTPException) as ctx:
            await get_classroom_pedagogical_context(
                classroom_id="QUALQUER-TURMA",
                school_id=school_b.id,
                academic_year="2026",
                identity=identity,
                session_factory=self.session_factory,
            )
        self.assertEqual(ctx.exception.status_code, 403)

    async def test_a_teacher_of_the_classroom_is_allowed(self):
        """The fix must not deny the caller it exists to protect - only
        strangers to the school. Seeds a real context so an authorized call
        that returns nothing can't be confused with a check that got
        skipped entirely."""
        async with self.session_factory() as session:
            school_a, _ = await self._two_schools(session)
            admin = PlatformAdminService(session)
            await admin.link_user_to_school(
                performed_by_external_id="setup",
                external_user_id="teacher-a",
                role=AdminRole.TEACHER,
                scope_type=AdminScopeType.CLASSROOM,
                school_id=school_a.id,
                scope_external_id="TURMA-A1",
            )
            node = CatalogNode(id=uuid.uuid4(), node_type="CONTENT", name="Diluição de Soluções")
            session.add(node)
            await session.flush()
            context = PedagogicalContext(
                content_node_id=node.id,
                source="TEACHER",
                institution_id=str(school_a.id),
                classroom_id="TURMA-A1",
                title="Ensinei Diluição de Soluções",
                recorded_at=datetime.now(timezone.utc),
                active=True,
                metadata_={"academic_year": "2026"},
            )
            session.add(context)
            await session.commit()
            school_a_id = school_a.id

        identity = ExternalIdentityContext(provider="test", external_user_id="teacher-a")
        result = await get_classroom_pedagogical_context(
            classroom_id="TURMA-A1",
            school_id=school_a_id,
            academic_year="2026",
            identity=identity,
            session_factory=self.session_factory,
        )
        self.assertEqual(len(result), 1)
        self.assertEqual(result[0].title, "Ensinei Diluição de Soluções")


if __name__ == "__main__":
    unittest.main()
