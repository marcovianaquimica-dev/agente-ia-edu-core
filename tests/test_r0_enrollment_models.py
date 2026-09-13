import unittest
from datetime import date

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EnrollmentTransition,
    GradeLevel,
    Person,
    School,
    Segment,
    Student,
    StudentEnrollment,
)


class TestEnrollmentModels(unittest.IsolatedAsyncioTestCase):
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

    async def _class_in_year(self, session, school, grade, year_number):
        year = AcademicYear(school_id=school.id, year=year_number, status="ACTIVE")
        session.add(year)
        await session.flush()
        klass = Class(academic_year_id=year.id, grade_level_id=grade.id, name="A")
        session.add(klass)
        await session.flush()
        return klass

    async def _fixture(self, session):
        school = School(code="ESCOLA_R0", name="Escola R0")
        session.add(school)
        await session.flush()
        segment = Segment(school_id=school.id, name="Ensino Médio", ordinal=3)
        session.add(segment)
        await session.flush()
        grade = GradeLevel(segment_id=segment.id, name="1ª série", ordinal=1)
        person = Person(school_id=school.id, full_name="Ana Clara")
        session.add_all([grade, person])
        await session.flush()
        student = Student(school_id=school.id, person_id=person.id, student_code="2026001")
        session.add(student)
        await session.flush()
        return school, grade, student

    async def test_a_student_enrolls_in_a_class(self):
        async with self.session_factory() as session:
            school, grade, student = await self._fixture(session)
            klass = await self._class_in_year(session, school, grade, 2026)

            enrollment = StudentEnrollment(
                student_id=student.id, class_id=klass.id, enrolled_on=date(2026, 2, 1)
            )
            session.add(enrollment)
            await session.flush()
            self.assertEqual(enrollment.status, "ACTIVE")

    async def test_the_same_student_cannot_enroll_twice_in_one_class(self):
        async with self.session_factory() as session:
            school, grade, student = await self._fixture(session)
            klass = await self._class_in_year(session, school, grade, 2026)

            session.add(StudentEnrollment(student_id=student.id, class_id=klass.id))
            await session.flush()
            session.add(StudentEnrollment(student_id=student.id, class_id=klass.id))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_a_transition_preserves_the_previous_enrollment(self):
        """Spec §4.3: moving up a year is a recorded fact, not an UPDATE that
        erases the prior state. After promoting, the 2026 enrollment is still
        readable with its own status."""
        async with self.session_factory() as session:
            school, grade, student = await self._fixture(session)
            class_2026 = await self._class_in_year(session, school, grade, 2026)
            class_2027 = await self._class_in_year(session, school, grade, 2027)

            first = StudentEnrollment(student_id=student.id, class_id=class_2026.id)
            session.add(first)
            await session.flush()

            first.status = "COMPLETED"
            second = StudentEnrollment(student_id=student.id, class_id=class_2027.id)
            session.add(second)
            await session.flush()

            session.add(EnrollmentTransition(
                from_enrollment_id=first.id, to_enrollment_id=second.id, kind="PROMOTED"
            ))
            await session.flush()

            stored = (await session.execute(
                select(StudentEnrollment).where(StudentEnrollment.id == first.id)
            )).scalar_one()
            self.assertEqual(stored.status, "COMPLETED")
            self.assertEqual(stored.class_id, class_2026.id)

    async def test_an_exit_transition_has_no_destination(self):
        async with self.session_factory() as session:
            school, grade, student = await self._fixture(session)
            klass = await self._class_in_year(session, school, grade, 2026)
            enrollment = StudentEnrollment(student_id=student.id, class_id=klass.id)
            session.add(enrollment)
            await session.flush()

            session.add(EnrollmentTransition(
                from_enrollment_id=enrollment.id, to_enrollment_id=None, kind="EXITED"
            ))
            await session.flush()

    async def test_transition_kind_is_constrained(self):
        async with self.session_factory() as session:
            school, grade, student = await self._fixture(session)
            klass = await self._class_in_year(session, school, grade, 2026)
            enrollment = StudentEnrollment(student_id=student.id, class_id=klass.id)
            session.add(enrollment)
            await session.flush()

            session.add(EnrollmentTransition(
                from_enrollment_id=enrollment.id, kind="NAO_EXISTE"
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_enrollment_status_is_constrained(self):
        async with self.session_factory() as session:
            school, grade, student = await self._fixture(session)
            klass = await self._class_in_year(session, school, grade, 2026)
            session.add(StudentEnrollment(
                student_id=student.id, class_id=klass.id, status="NAO_EXISTE"
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()
