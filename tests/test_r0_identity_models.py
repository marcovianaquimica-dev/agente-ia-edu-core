import unittest
import uuid

from sqlalchemy import inspect
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import Person, School, User

FORBIDDEN_COLUMN_FRAGMENTS = ("password", "token", "secret", "credential", "senha")


class TestIdentityModels(unittest.IsolatedAsyncioTestCase):
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

    async def _school(self, session) -> School:
        school = School(code="ESCOLA_R0", name="Escola R0")
        session.add(school)
        await session.flush()
        return school

    def test_users_has_no_credential_column(self):
        """The core never stores credentials (spec §3.1). This test makes the CI
        refuse a future column named like one, instead of relying on memory."""
        for column in inspect(User).columns:
            with self.subTest(column=column.name):
                lowered = column.name.lower()
                for fragment in FORBIDDEN_COLUMN_FRAGMENTS:
                    self.assertNotIn(fragment, lowered)

    def test_persons_has_no_credential_column(self):
        for column in inspect(Person).columns:
            with self.subTest(column=column.name):
                lowered = column.name.lower()
                for fragment in FORBIDDEN_COLUMN_FRAGMENTS:
                    self.assertNotIn(fragment, lowered)

    async def test_person_external_id_is_unique_per_school(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            session.add(Person(school_id=school.id, full_name="Ana", external_id="EXT_1"))
            await session.flush()
            session.add(Person(school_id=school.id, full_name="Bruno", external_id="EXT_1"))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_the_same_external_id_may_repeat_in_another_school(self):
        """Tenant isolation: external ids are the host's, and two hosts may use
        the same string. Uniqueness is per school, never global."""
        async with self.session_factory() as session:
            first = await self._school(session)
            second = School(code="ESCOLA_R0_B", name="Escola R0 B")
            session.add(second)
            await session.flush()

            session.add(Person(school_id=first.id, full_name="Ana", external_id="EXT_1"))
            session.add(Person(school_id=second.id, full_name="Ana", external_id="EXT_1"))
            await session.flush()

    async def test_user_requires_a_person(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            person = Person(school_id=school.id, full_name="Ana")
            session.add(person)
            await session.flush()

            user = User(
                person_id=person.id,
                external_identity_provider="host",
                external_user_id="host:ana",
            )
            session.add(user)
            await session.flush()
            self.assertEqual(user.status, "ACTIVE")

    async def test_user_status_is_constrained(self):
        async with self.session_factory() as session:
            school = await self._school(session)
            person = Person(school_id=school.id, full_name="Ana")
            session.add(person)
            await session.flush()

            session.add(User(
                person_id=person.id,
                external_identity_provider="host",
                external_user_id="host:ana2",
                status="NAO_EXISTE",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()
