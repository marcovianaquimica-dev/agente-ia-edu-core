import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    GradeLevel,
    Person,
    School,
    Segment,
    Student,
    StudentEnrollment,
    User,
)
from agente_ia_edu.services.student_enrollment_resolution import resolve_active_enrollment


class ResolveActiveEnrollmentTests(unittest.IsolatedAsyncioTestCase):
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

    async def _class_(self, session, school, code):
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id=f"SEG-{code}")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="grade", external_id=f"GRADE-{code}",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id=f"YEAR-{code}")
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="turma", external_id=f"TURMA-{code}",
        )
        session.add(klass)
        await session.commit()
        return klass

    async def _full_academic_identity(self, session, school, klass, external_user_id, code):
        person = Person(id=uuid.uuid4(), school_id=school.id, full_name=f"Aluno {code}")
        session.add(person)
        await session.flush()
        user = User(
            id=uuid.uuid4(), school_id=school.id, person_id=person.id,
            external_identity_provider="test", external_user_id=external_user_id,
        )
        session.add(user)
        student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id, student_code=f"ST-{code}")
        session.add(student)
        await session.flush()
        enrollment = StudentEnrollment(
            id=uuid.uuid4(), school_id=school.id, student_id=student.id, class_id=klass.id,
            status="ACTIVE",
        )
        session.add(enrollment)
        await session.commit()
        return student, enrollment

    async def test_resolves_the_real_chain(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="RE1", name="school-re1")
            session.add(school)
            await session.flush()
            klass = await self._class_(session, school, "1")
            student, enrollment = await self._full_academic_identity(
                session, school, klass, "aluno1", "1"
            )

            resolved = await resolve_active_enrollment(
                session, school_id=school.id, external_user_id="aluno1"
            )
            self.assertIsNotNone(resolved)
            self.assertEqual(resolved.id, enrollment.id)
            self.assertEqual(resolved.class_id, klass.id)
            self.assertEqual(resolved.student_id, student.id)

    async def test_no_user_row_returns_none(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="RE2", name="school-re2")
            session.add(school)
            await session.commit()

            resolved = await resolve_active_enrollment(
                session, school_id=school.id, external_user_id="ghost"
            )
            self.assertIsNone(resolved)

    async def test_user_without_enrollment_returns_none(self):
        async with self.session_factory() as session:
            school = School(id=uuid.uuid4(), code="RE3", name="school-re3")
            session.add(school)
            await session.flush()
            person = Person(id=uuid.uuid4(), school_id=school.id, full_name="Sem Turma")
            session.add(person)
            await session.flush()
            user = User(
                id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                external_identity_provider="test", external_user_id="sem_turma",
            )
            session.add(user)
            student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id, student_code="ST-3")
            session.add(student)
            await session.commit()

            resolved = await resolve_active_enrollment(
                session, school_id=school.id, external_user_id="sem_turma"
            )
            self.assertIsNone(resolved)

    async def test_wrong_school_returns_none(self):
        async with self.session_factory() as session:
            school_a = School(id=uuid.uuid4(), code="RE4A", name="school-re4a")
            school_b = School(id=uuid.uuid4(), code="RE4B", name="school-re4b")
            session.add_all([school_a, school_b])
            await session.flush()
            klass = await self._class_(session, school_a, "4")
            await self._full_academic_identity(session, school_a, klass, "aluno4", "4")

            resolved = await resolve_active_enrollment(
                session, school_id=school_b.id, external_user_id="aluno4"
            )
            self.assertIsNone(resolved)


if __name__ == "__main__":
    unittest.main()
