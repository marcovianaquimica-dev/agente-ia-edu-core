"""Proves the database itself refuses a row that crosses a school boundary.

The final branch review found six cross-school combinations ACCEPTED by the
schema, and found them by execution rather than by reading. This file is the
answer in the same currency: each one is attempted here against a real engine,
and each one must be rejected.

Two things make these tests mean something:

* ``PRAGMA foreign_keys=ON``. SQLite ignores foreign keys unless told not to,
  so a composite-key test on a default connection passes whatever the schema
  says - the worst kind of green. ``test_foreign_keys_are_actually_enforced``
  reads the pragma back and then violates a plain foreign key, so if the pragma
  were ever lost this file fails loudly instead of quietly proving nothing.
* Every rejection below is a TENANT crossing and nothing else: the same row
  written entirely within one school is accepted first (or is obviously
  legitimate), so what fires can only be the composite key.
"""

import unittest
import uuid

from sqlalchemy import event, text
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
    SchoolIdentityVersion,
    SchoolSetting,
    SchoolUnit,
    Segment,
    Student,
    StudentEnrollment,
    User,
    UserSchoolLink,
)


class _School:
    """One fully populated school, so a crossing has something to cross to."""

    def __init__(self, school, unit, segment, grade, year, person, user, student, klass,
                 enrollment, identity):
        self.school = school
        self.unit = unit
        self.segment = segment
        self.grade = grade
        self.year = year
        self.person = person
        self.user = user
        self.student = student
        self.klass = klass
        self.enrollment = enrollment
        self.identity = identity


class TenantIsolationCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )

        # SQLite enforces foreign keys - composite ones included - only when
        # asked, per connection. Registered before the first connect, so every
        # connection this engine hands out has it.
        @event.listens_for(self.engine.sync_engine, "connect")
        def _enable_foreign_keys(dbapi_connection, _record):  # pragma: no cover
            cursor = dbapi_connection.cursor()
            cursor.execute("PRAGMA foreign_keys=ON")
            cursor.close()

        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _populate(self, session, code) -> _School:
        school = School(code=code, name=f"Escola {code}")
        session.add(school)
        await session.flush()

        unit = SchoolUnit(school_id=school.id, name="Unidade Centro")
        segment = Segment(school_id=school.id, name="Ensino Médio", ordinal=3)
        year = AcademicYear(school_id=school.id, year=2026, status="ACTIVE")
        person = Person(school_id=school.id, full_name="Ana Clara")
        session.add_all([unit, segment, year, person])
        await session.flush()

        grade = GradeLevel(
            school_id=school.id, segment_id=segment.id, name="1ª série", ordinal=1
        )
        user = User(
            school_id=school.id,
            person_id=person.id,
            external_identity_provider="host",
            external_user_id="host:ana",
        )
        student = Student(
            school_id=school.id, person_id=person.id, student_code="2026001"
        )
        session.add_all([grade, user, student])
        await session.flush()

        klass = Class(
            school_id=school.id,
            academic_year_id=year.id,
            grade_level_id=grade.id,
            school_unit_id=unit.id,
            name="A",
        )
        session.add(klass)
        await session.flush()

        enrollment = StudentEnrollment(
            school_id=school.id, student_id=student.id, class_id=klass.id
        )
        identity = SchoolIdentityVersion(
            school_id=school.id,
            version=1,
            display_name=f"Colégio {code}",
            published_by_user_id=user.id,
        )
        session.add_all([enrollment, identity])
        await session.flush()

        return _School(
            school, unit, segment, grade, year, person, user, student, klass,
            enrollment, identity,
        )


class TestForeignKeysAreEnforced(TenantIsolationCase):
    async def test_the_pragma_is_on_for_the_connection_under_test(self):
        """Without this, every rejection test below would pass vacuously."""
        async with self.session_factory() as session:
            enabled = await session.scalar(text("PRAGMA foreign_keys"))
            self.assertEqual(enabled, 1)

    async def test_a_plainly_dangling_reference_is_rejected(self):
        """The control: a foreign key that points at nothing at all. If SQLite
        were not enforcing, this would be accepted and the whole file would be
        theatre."""
        async with self.session_factory() as session:
            session.add(Person(school_id=uuid.uuid4(), full_name="Fantasma"))
            with self.assertRaises(IntegrityError):
                await session.flush()


class TestCrossSchoolCombinationsAreRejected(TenantIsolationCase):
    """The six the review proved were accepted, plus the ones the same fix
    closes on the way past."""

    async def test_1_student_cannot_point_at_a_person_of_another_school(self):
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            b = await self._populate(session, "ESCOLA_B")
            session.add(Student(
                school_id=a.school.id, person_id=b.person.id, student_code="X"
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_2_class_cannot_mix_a_year_and_a_grade_level_of_two_schools(self):
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            b = await self._populate(session, "ESCOLA_B")
            session.add(Class(
                school_id=a.school.id,
                academic_year_id=a.year.id,
                grade_level_id=b.grade.id,
                name="MISTA",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_2b_class_cannot_take_a_unit_from_another_school(self):
        """The third parent. A class with three independent parents is the
        worst case, so all three are checked."""
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            b = await self._populate(session, "ESCOLA_B")
            session.add(Class(
                school_id=a.school.id,
                academic_year_id=a.year.id,
                grade_level_id=a.grade.id,
                school_unit_id=b.unit.id,
                name="MISTA",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_3_enrollment_cannot_bind_a_student_to_another_school_class(self):
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            b = await self._populate(session, "ESCOLA_B")
            session.add(StudentEnrollment(
                school_id=a.school.id, student_id=a.student.id, class_id=b.klass.id
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_4_a_link_of_one_school_cannot_point_at_another_school_class(self):
        """The row the authorisation service reads in Phase 3. A link belonging
        to school B naming school A's class is data from one school visible in
        the other (spec §8)."""
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            b = await self._populate(session, "ESCOLA_B")
            session.add(UserSchoolLink(
                external_user_id="host:ana",
                school_id=b.school.id,
                role="TEACHER",
                scope_type="CLASSROOM",
                scope_external_id="TURMA_3A",
                class_id=a.klass.id,
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_4b_a_link_cannot_point_at_a_user_of_another_school(self):
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            b = await self._populate(session, "ESCOLA_B")
            session.add(UserSchoolLink(
                external_user_id="host:ana",
                school_id=b.school.id,
                role="TEACHER",
                scope_type="SCHOOL",
                user_id=a.user.id,
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_4c_a_link_cannot_point_at_a_segment_of_another_school(self):
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            b = await self._populate(session, "ESCOLA_B")
            session.add(UserSchoolLink(
                external_user_id="host:ana",
                school_id=b.school.id,
                role="COORDINATOR",
                scope_type="SEGMENT",
                segment_id=a.segment.id,
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_5_a_school_cannot_adopt_another_school_visual_identity(self):
        """This is the row R4 stamps onto the PDF: school B's mark coming out on
        school A's devolutiva."""
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            b = await self._populate(session, "ESCOLA_B")
            session.add(SchoolSetting(
                school_id=a.school.id,
                correction_mode="FORMATIVO",
                current_identity_version_id=b.identity.id,
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_6_a_transition_cannot_span_two_schools(self):
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            b = await self._populate(session, "ESCOLA_B")
            session.add(EnrollmentTransition(
                school_id=a.school.id,
                from_enrollment_id=a.enrollment.id,
                to_enrollment_id=b.enrollment.id,
                kind="TRANSFERRED",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_6b_a_transition_cannot_be_decided_by_another_school_user(self):
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            b = await self._populate(session, "ESCOLA_B")
            session.add(EnrollmentTransition(
                school_id=a.school.id,
                from_enrollment_id=a.enrollment.id,
                kind="EXITED",
                decided_by_user_id=b.user.id,
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_7_a_user_cannot_point_at_a_person_of_another_school(self):
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            b = await self._populate(session, "ESCOLA_B")
            session.add(User(
                school_id=a.school.id,
                person_id=b.person.id,
                external_identity_provider="host",
                external_user_id="host:intruso",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_8_a_grade_level_cannot_belong_to_another_school_segment(self):
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            b = await self._populate(session, "ESCOLA_B")
            session.add(GradeLevel(
                school_id=a.school.id, segment_id=b.segment.id, name="2ª série"
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_9_an_identity_cannot_be_signed_by_another_school_user(self):
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            b = await self._populate(session, "ESCOLA_B")
            session.add(SchoolIdentityVersion(
                school_id=a.school.id,
                version=2,
                display_name="Colégio A",
                published_by_user_id=b.user.id,
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()


class TestTheSameRowsWithinOneSchoolAreAccepted(TenantIsolationCase):
    """The other half of the proof. If these failed, the tests above would be
    rejecting something other than the tenant crossing."""

    async def test_a_whole_school_builds_without_a_single_rejection(self):
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            session.add_all([
                UserSchoolLink(
                    external_user_id="host:ana",
                    school_id=a.school.id,
                    role="TEACHER",
                    scope_type="CLASSROOM",
                    scope_external_id="TURMA_3A",
                    user_id=a.user.id,
                    school_unit_id=a.unit.id,
                    segment_id=a.segment.id,
                    grade_level_id=a.grade.id,
                    class_id=a.klass.id,
                ),
                SchoolSetting(
                    school_id=a.school.id,
                    correction_mode="FORMATIVO",
                    current_identity_version_id=a.identity.id,
                ),
                EnrollmentTransition(
                    school_id=a.school.id,
                    from_enrollment_id=a.enrollment.id,
                    kind="EXITED",
                    decided_by_user_id=a.user.id,
                ),
            ])
            await session.flush()

    async def test_a_platform_scope_link_with_no_school_still_works(self):
        """``user_school_links.school_id`` is nullable for PLATFORM scope. Under
        MATCH SIMPLE a NULL leaves the composite key unenforced, which is right:
        there is no tenant to isolate."""
        async with self.session_factory() as session:
            session.add(UserSchoolLink(
                external_user_id="admin:master",
                school_id=None,
                role="PLATFORM_ADMIN",
                scope_type="PLATFORM",
            ))
            await session.flush()


class TestOneHostIdentityInTwoSchools(TenantIsolationCase):
    """C1: a teacher who works at two schools.

    ``persons`` is scoped per school by design (spec §4.1), so the same physical
    person is two rows. Before this fix ``users`` was unique on (provider,
    external_user_id) GLOBALLY, which made the second row impossible - the two
    decisions contradicted each other."""

    async def test_the_same_host_account_gets_one_user_per_school(self):
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            b = await self._populate(session, "ESCOLA_B")
            # a.user and b.user already share (host, host:ana), one per school.
            self.assertEqual(a.user.external_user_id, b.user.external_user_id)
            self.assertNotEqual(a.user.school_id, b.user.school_id)

    async def test_the_host_identity_still_cannot_repeat_within_one_school(self):
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            other = Person(school_id=a.school.id, full_name="Outra Ana")
            session.add(other)
            await session.flush()
            session.add(User(
                school_id=a.school.id,
                person_id=other.id,
                external_identity_provider="host",
                external_user_id="host:ana",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()


class TestExternalIdIsUniquePerSchool(TenantIsolationCase):
    """I4: spec §4 and §9 scope ``external_id`` to the school, and the bridge of
    §3.2 needs exactly that - it resolves a string plus a ``school_id`` to ONE
    entity. Scoping it to the parent instead (per segment, per year, per class)
    let one string name two rows in the same school, with no tie-break rule
    written anywhere."""

    async def test_a_grade_level_external_id_cannot_repeat_in_one_school(self):
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            other_segment = Segment(
                school_id=a.school.id, name="Fundamental II", ordinal=2
            )
            session.add(other_segment)
            await session.flush()
            session.add(GradeLevel(
                school_id=a.school.id,
                segment_id=a.segment.id,
                name="1ª série",
                external_id="SERIE_3",
            ))
            await session.flush()
            # A different segment of the SAME school: used to be accepted.
            session.add(GradeLevel(
                school_id=a.school.id,
                segment_id=other_segment.id,
                name="1ª série",
                external_id="SERIE_3",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_a_class_external_id_cannot_repeat_across_years_of_one_school(self):
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            year_2027 = AcademicYear(
                school_id=a.school.id, year=2027, status="PLANNED"
            )
            session.add(year_2027)
            await session.flush()
            session.add(Class(
                school_id=a.school.id,
                academic_year_id=year_2027.id,
                grade_level_id=a.grade.id,
                name="A",
                external_id="TURMA_3A",
            ))
            await session.flush()
            session.add(Class(
                school_id=a.school.id,
                academic_year_id=a.year.id,
                grade_level_id=a.grade.id,
                name="B",
                external_id="TURMA_3A",
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_the_same_external_id_still_repeats_across_schools(self):
        """Per school, never global: two hosts may use the same string."""
        async with self.session_factory() as session:
            a = await self._populate(session, "ESCOLA_A")
            b = await self._populate(session, "ESCOLA_B")
            a.klass.external_id = "TURMA_3A"
            b.klass.external_id = "TURMA_3A"
            await session.flush()


class TestPlatformScopeBridgeRequiresSchool(TenantIsolationCase):
    """The regression the composite fix introduced. Under MATCH SIMPLE, a
    composite foreign key is unchecked the moment any one of its columns is
    NULL, and ``school_id`` is NULL on every PLATFORM-scope link - so such a
    link could name a class_id (or any other bridge column) that does not
    exist at all, which is worse than the plain foreign key this phase
    replaced. ``ck_user_school_links_bridge_requires_school`` closes that: a
    link with no school must have every bridge column NULL too."""

    async def test_a_platform_scope_link_cannot_name_a_class_that_does_not_exist(self):
        """The composite foreign key (school_id, class_id) is unenforced here
        because school_id is NULL - that is MATCH SIMPLE, not a bug. What
        rejects this row is the CHECK, not the foreign key: class_id is
        non-NULL while school_id is NULL, which the CHECK forbids outright,
        before the (silent) foreign key ever gets a say."""
        async with self.session_factory() as session:
            session.add(UserSchoolLink(
                external_user_id="admin:master",
                school_id=None,
                role="PLATFORM_ADMIN",
                scope_type="PLATFORM",
                class_id=uuid.uuid4(),
            ))
            with self.assertRaises(IntegrityError):
                await session.flush()

    async def test_a_platform_scope_link_with_every_bridge_column_null_is_accepted(self):
        """The other half of the rule: a PLATFORM-scope link naming nothing at
        all - every such row in production today - must keep working."""
        async with self.session_factory() as session:
            session.add(UserSchoolLink(
                external_user_id="admin:master_bare",
                school_id=None,
                role="PLATFORM_ADMIN",
                scope_type="PLATFORM",
            ))
            await session.flush()
