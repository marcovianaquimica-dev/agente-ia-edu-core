"""Atribuir uma proposta da plataforma a uma turma materializa a copia da
escola (spec s4) - individual e em lote, idempotente e isolada por escola.
"""

import asyncio
import unittest
import uuid

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayPrompt,
    GradeLevel,
    PlatformEssayPrompt,
    School,
    Segment,
    UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext

PROMPTS = "/api/v1/catalog/essay-prompts"


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class PlatformPromptAssignmentMaterializationTests(unittest.TestCase):
    def setUp(self):
        self.loop = asyncio.new_event_loop()
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

        async def _prep():
            async with self.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        self.loop.run_until_complete(_prep())
        self.app = create_app()
        self.app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_r5")
        self.client = TestClient(self.app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        self.loop.run_until_complete(self.engine.dispose())
        self.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _seed(self, code: str, user: str = "prof_r5", class_count: int = 2):
        """Escola + professor vinculado + N turmas reais."""

        async def _do():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"PAM-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id=user, school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                segment = Segment(
                    id=uuid.uuid4(), school_id=school.id, name="seg", external_id=f"SEG-{code}"
                )
                session.add(segment)
                await session.flush()
                grade = GradeLevel(
                    id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
                    name="grade", external_id=f"GRADE-{code}",
                )
                year = AcademicYear(
                    id=uuid.uuid4(), school_id=school.id, year=2026, external_id=f"YEAR-{code}"
                )
                session.add_all([grade, year])
                await session.flush()
                class_ids = []
                for index in range(class_count):
                    klass = Class(
                        id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
                        grade_level_id=grade.id, name=f"turma-{index}",
                        external_id=f"TURMA-{code}-{index}",
                    )
                    session.add(klass)
                    class_ids.append(klass.id)
                await session.commit()
                return school.id, class_ids

        return self.loop.run_until_complete(_do())

    def _seed_platform_prompt(self, title="Mobilidade urbana"):
        async def _do():
            async with self.factory() as session:
                prompt = PlatformEssayPrompt(
                    id=uuid.uuid4(), title=title, statement=f"Enunciado de {title}.",
                    status="ACTIVE", created_by_external_identity="user:ADMIN",
                )
                session.add(prompt)
                await session.commit()
                return prompt.id

        return self.loop.run_until_complete(_do())

    def _copies(self, school_id, origin_id):
        async def _do():
            async with self.factory() as session:
                rows = (await session.execute(
                    select(EssayPrompt).where(
                        EssayPrompt.school_id == school_id,
                        EssayPrompt.materialized_from_platform_prompt_id == origin_id,
                    )
                )).scalars().all()
                return [row.id for row in rows]

        return self.loop.run_until_complete(_do())

    def test_single_assignment_materializes_and_points_at_the_copy(self):
        school_id, class_ids = self._seed("1")
        origin_id = self._seed_platform_prompt()

        response = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments", json={"class_id": str(class_ids[0])}
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()

        copies = self._copies(school_id, origin_id)
        self.assertEqual(len(copies), 1)
        self.assertEqual(body["essay_prompt_id"], str(copies[0]))
        self.assertNotEqual(body["essay_prompt_id"], str(origin_id))
        self.assertEqual(body["school_id"], str(school_id))

    def test_two_assignments_to_different_classes_reuse_the_same_copy(self):
        school_id, class_ids = self._seed("2")
        origin_id = self._seed_platform_prompt()

        first = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments", json={"class_id": str(class_ids[0])}
        )
        second = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments", json={"class_id": str(class_ids[1])}
        )
        self.assertEqual(first.status_code, 201, first.text)
        self.assertEqual(second.status_code, 201, second.text)
        self.assertEqual(
            first.json()["essay_prompt_id"], second.json()["essay_prompt_id"]
        )
        self.assertEqual(len(self._copies(school_id, origin_id)), 1)

    def test_bulk_assignment_materializes_once_for_every_class(self):
        school_id, class_ids = self._seed("3")
        origin_id = self._seed_platform_prompt()

        response = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments/bulk",
            json={"class_ids": [str(c) for c in class_ids]},
        )
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["failures"], {})
        self.assertEqual(len(body["assigned"]), 2)

        copies = self._copies(school_id, origin_id)
        self.assertEqual(len(copies), 1)
        self.assertEqual({a["essay_prompt_id"] for a in body["assigned"]}, {str(copies[0])})

    def test_bulk_then_single_still_reuses_the_same_copy(self):
        school_id, class_ids = self._seed("4", class_count=2)
        origin_id = self._seed_platform_prompt()

        bulk = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments/bulk",
            json={"class_ids": [str(class_ids[0])]},
        )
        single = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments", json={"class_id": str(class_ids[1])}
        )
        self.assertEqual(bulk.status_code, 200, bulk.text)
        self.assertEqual(single.status_code, 201, single.text)
        self.assertEqual(
            bulk.json()["assigned"][0]["essay_prompt_id"], single.json()["essay_prompt_id"]
        )
        self.assertEqual(len(self._copies(school_id, origin_id)), 1)

    def test_two_schools_get_independent_copies_through_the_routes(self):
        school_a, classes_a = self._seed("5A", user="prof_a", class_count=1)
        school_b, classes_b = self._seed("5B", user="prof_b", class_count=1)
        origin_id = self._seed_platform_prompt()

        self._as("prof_a")
        resp_a = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments", json={"class_id": str(classes_a[0])}
        )
        self._as("prof_b")
        resp_b = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments", json={"class_id": str(classes_b[0])}
        )
        self.assertEqual(resp_a.status_code, 201, resp_a.text)
        self.assertEqual(resp_b.status_code, 201, resp_b.text)
        self.assertNotEqual(
            resp_a.json()["essay_prompt_id"], resp_b.json()["essay_prompt_id"]
        )
        self.assertEqual(resp_a.json()["school_id"], str(school_a))
        self.assertEqual(resp_b.json()["school_id"], str(school_b))
        self.assertEqual(len(self._copies(school_a, origin_id)), 1)
        self.assertEqual(len(self._copies(school_b, origin_id)), 1)

    def test_a_normal_prompt_assignment_is_completely_unchanged(self):
        school_id, class_ids = self._seed("6", class_count=1)
        created = self.client.post(
            PROMPTS, json={"title": "Minha", "statement": "Disserte.", "year": 2026}
        )
        self.assertEqual(created.status_code, 201, created.text)
        prompt_id = created.json()["id"]

        response = self.client.post(
            f"{PROMPTS}/{prompt_id}/assignments", json={"class_id": str(class_ids[0])}
        )
        self.assertEqual(response.status_code, 201, response.text)
        self.assertEqual(response.json()["essay_prompt_id"], prompt_id)

    def test_a_platform_prompt_is_never_assignable_to_another_schools_class(self):
        # A copia de prof_b chega a ser criada na escola B (a materializacao
        # acontece antes da validacao da turma), mas a atribuicao e recusada:
        # o que nao pode existir em hipotese nenhuma e uma PromptAssignment
        # ligando uma proposta da escola B a uma turma da escola A. A copia
        # orfa e inofensiva - e uma proposta comum da escola B, sem turma.
        _school_a, classes_a = self._seed("7A", user="prof_a", class_count=1)
        self._seed("7B", user="prof_b", class_count=1)
        origin_id = self._seed_platform_prompt()

        self._as("prof_b")
        response = self.client.post(
            f"{PROMPTS}/{origin_id}/assignments", json={"class_id": str(classes_a[0])}
        )
        self.assertEqual(response.status_code, 422, response.text)


if __name__ == "__main__":
    unittest.main()
