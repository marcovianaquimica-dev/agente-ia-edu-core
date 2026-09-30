"""Idempotent demo-data seed: "Escola ABC" with a real R0 academic hierarchy.

Unlike `scripts/seed_demo_data.py` (which seeds "Escola Partner" using only
the OLD flat `UserSchoolLink(scope_type, scope_external_id)` convention),
this script ALSO populates the R0 academic-hierarchy tables
(`db/models/academic.py`: AcademicYear, Segment, GradeLevel, Class, Person,
User, Student, StudentEnrollment) that exist in the schema but, as of
2026-09-25, have no write path anywhere in the app (no route/service creates
rows in them). Written to close that gap with one concrete, real example
rather than leaving the tables permanently empty.

Creates: School "Escola ABC", segment "Ensino Médio" -> grade level "1ª
Série" -> two classes ("Turma A", "Turma B"), a coordinator, a teacher (both
SCHOOL-scoped) and one student (enrolled in Turma A) - plus the matching R0
Person/User/Student/StudentEnrollment rows for the student, bridged via
`external_id` to the SAME string used as `UserSchoolLink.scope_external_id`
(see the scope-id resolver service's own docstring: a `CLASSROOM` scope
resolves via `Class.external_id == scope_external_id` within the school).

No real authentication is set up (explicit user decision, 2026-09-25) - the
identities below are used exactly like every other identity in this dev
environment: typed as the Bearer subject / into each portal's own identity
input field, resolved by the DEV-ONLY self-asserted `TestExternalIdentityProvider`
(never for production - see `src/agente_ia_edu/auth/token.py`).

Safe to re-run: every section checks for existing rows before inserting.

Usage:
    source .env
    export DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5433/${POSTGRES_DB}"
    .venv/bin/python scripts/seed_escola_abc.py
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import date

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

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
    UserSchoolLink,
)
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService

SCHOOL_ID = uuid.UUID("a8c1e3d0-5f6b-4a7e-8c9d-1234567890ab")
SCHOOL_CODE = "ESCOLA_ABC"
SCHOOL_NAME = "Escola ABC"
ACADEMIC_YEAR = 2026

PERFORMED_BY = "admin:master"  # self-asserted PLATFORM_ADMIN, no DB row needed

COORDINATOR_ID = "coordenador_abc"  # bare id: coordination.js sends Bearer f"user:{coordinatorId}" raw (no doubling)
COORDINATOR_NAME = "Coordenador ABC"
TEACHER_ID = "professor_abc"  # bare id: teacher.js sends Bearer f"user:{teacherId}" raw, same convention
TEACHER_NAME = "Professor ABC"
STUDENT_EXTERNAL_ID = "student:aluno_abc"
STUDENT_NAME = "Aluno ABC"

TURMA_A_EXTERNAL_ID = "1EM_A"
TURMA_B_EXTERNAL_ID = "1EM_B"


async def ensure_school(session: AsyncSession) -> School:
    school = await session.get(School, SCHOOL_ID)
    if school:
        print(f"[school] already exists: {school.name}")
        return school
    school = School(id=SCHOOL_ID, code=SCHOOL_CODE, name=SCHOOL_NAME, status="ACTIVE")
    session.add(school)
    await session.flush()
    print(f"[school] created: {school.name} ({school.id})")
    return school


async def ensure_academic_year(session: AsyncSession) -> AcademicYear:
    existing = await session.scalar(
        select(AcademicYear).where(AcademicYear.school_id == SCHOOL_ID, AcademicYear.year == ACADEMIC_YEAR)
    )
    if existing:
        print(f"[academic_year] already exists: {existing.year}")
        return existing
    year = AcademicYear(
        school_id=SCHOOL_ID, year=ACADEMIC_YEAR, status="ACTIVE",
        starts_on=date(ACADEMIC_YEAR, 2, 1), ends_on=date(ACADEMIC_YEAR, 12, 15),
    )
    session.add(year)
    await session.flush()
    print(f"[academic_year] created: {year.year}")
    return year


async def ensure_segment(session: AsyncSession) -> Segment:
    existing = await session.scalar(
        select(Segment).where(Segment.school_id == SCHOOL_ID, Segment.external_id == "MEDIO")
    )
    if existing:
        print(f"[segment] already exists: {existing.name}")
        return existing
    segment = Segment(school_id=SCHOOL_ID, external_id="MEDIO", name="Ensino Médio", ordinal=3)
    session.add(segment)
    await session.flush()
    print(f"[segment] created: {segment.name}")
    return segment


async def ensure_grade_level(session: AsyncSession, segment: Segment) -> GradeLevel:
    existing = await session.scalar(
        select(GradeLevel).where(GradeLevel.school_id == SCHOOL_ID, GradeLevel.external_id == "1_SERIE_EM")
    )
    if existing:
        print(f"[grade_level] already exists: {existing.name}")
        return existing
    grade = GradeLevel(
        school_id=SCHOOL_ID, segment_id=segment.id, external_id="1_SERIE_EM",
        name="1ª Série", ordinal=1,
    )
    session.add(grade)
    await session.flush()
    print(f"[grade_level] created: {grade.name}")
    return grade


async def ensure_class(
    session: AsyncSession, *, academic_year: AcademicYear, grade_level: GradeLevel,
    external_id: str, name: str,
) -> Class:
    existing = await session.scalar(
        select(Class).where(Class.school_id == SCHOOL_ID, Class.external_id == external_id)
    )
    if existing:
        print(f"[class] already exists: {existing.name}")
        return existing
    klass = Class(
        school_id=SCHOOL_ID, academic_year_id=academic_year.id, grade_level_id=grade_level.id,
        external_id=external_id, name=name,
    )
    session.add(klass)
    await session.flush()
    print(f"[class] created: {klass.name} ({external_id})")
    return klass


async def ensure_link(
    admin: PlatformAdminService, session: AsyncSession, *,
    external_user_id: str, role: str, scope_type: str, scope_external_id: str | None = None,
) -> UserSchoolLink:
    existing = await session.scalar(
        select(UserSchoolLink).where(
            UserSchoolLink.external_user_id == external_user_id,
            UserSchoolLink.school_id == SCHOOL_ID,
            UserSchoolLink.role == role,
            UserSchoolLink.active.is_(True),
        )
    )
    if existing:
        print(f"[link] already exists: {external_user_id} -> {role}")
        return existing
    link = await admin.link_user_to_school(
        performed_by_external_id=PERFORMED_BY,
        external_user_id=external_user_id, role=role, scope_type=scope_type,
        school_id=SCHOOL_ID, scope_external_id=scope_external_id,
    )
    print(f"[link] {external_user_id} -> {role}/{scope_type}" + (f"={scope_external_id}" if scope_external_id else ""))
    return link


async def ensure_student_enrollment(
    session: AsyncSession, *, klass_id: uuid.UUID, klass_external_id: str, klass_name: str,
) -> None:
    """R0 hierarchy rows for the student, bridged to the same external_id
    used by the CLASSROOM-scoped UserSchoolLink above (see module docstring).

    Takes plain values, not the ORM `Class` object: by the time the caller
    reaches this point, one or more `session.commit()` calls have already
    happened (expire_on_commit=True, production default - see
    db/session.py), which expires every object this session ever loaded -
    including a `Class` fetched several steps earlier. Reading an attribute
    off it here would need a lazy reload outside any awaited call, raising
    MissingGreenlet."""
    person = await session.scalar(
        select(Person).where(Person.school_id == SCHOOL_ID, Person.external_id == STUDENT_EXTERNAL_ID)
    )
    if person is None:
        person = Person(school_id=SCHOOL_ID, external_id=STUDENT_EXTERNAL_ID, full_name=STUDENT_NAME, status="ACTIVE")
        session.add(person)
        await session.flush()
        print(f"[person] created: {person.full_name}")
    else:
        print(f"[person] already exists: {person.full_name}")

    user = await session.scalar(
        select(User).where(
            User.school_id == SCHOOL_ID, User.external_identity_provider == "test",
            User.external_user_id == STUDENT_EXTERNAL_ID,
        )
    )
    if user is None:
        user = User(
            school_id=SCHOOL_ID, person_id=person.id, external_identity_provider="test",
            external_user_id=STUDENT_EXTERNAL_ID, display_name=STUDENT_NAME, status="ACTIVE",
        )
        session.add(user)
        await session.flush()
        print(f"[user] created: {user.display_name}")
    else:
        print("[user] already exists")

    student = await session.scalar(
        select(Student).where(Student.school_id == SCHOOL_ID, Student.external_id == STUDENT_EXTERNAL_ID)
    )
    if student is None:
        student = Student(
            school_id=SCHOOL_ID, person_id=person.id, external_id=STUDENT_EXTERNAL_ID,
            student_code="ABC-0001", status="ACTIVE",
        )
        session.add(student)
        await session.flush()
        print("[student] created")
    else:
        print("[student] already exists")

    enrollment = await session.scalar(
        select(StudentEnrollment).where(StudentEnrollment.student_id == student.id, StudentEnrollment.class_id == klass_id)
    )
    if enrollment is None:
        enrollment = StudentEnrollment(
            school_id=SCHOOL_ID, student_id=student.id, class_id=klass_id,
            external_id=f"{STUDENT_EXTERNAL_ID}@{klass_external_id}",
            enrolled_on=date(ACADEMIC_YEAR, 2, 1), status="ACTIVE",
        )
        session.add(enrollment)
        await session.flush()
        print(f"[enrollment] created: {STUDENT_NAME} -> {klass_name}")
    else:
        print("[enrollment] already exists")


async def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession)
    async with factory() as session:
        await ensure_school(session)
        await session.commit()

        year = await ensure_academic_year(session)
        segment = await ensure_segment(session)
        grade = await ensure_grade_level(session, segment)
        turma_a = await ensure_class(session, academic_year=year, grade_level=grade, external_id=TURMA_A_EXTERNAL_ID, name="Turma A")
        turma_b = await ensure_class(session, academic_year=year, grade_level=grade, external_id=TURMA_B_EXTERNAL_ID, name="Turma B")
        # Captured before the commit below expires both Class objects.
        turma_a_id, turma_a_external_id, turma_a_name = turma_a.id, turma_a.external_id, turma_a.name
        await session.commit()

        admin = PlatformAdminService(session)
        await ensure_link(admin, session, external_user_id=COORDINATOR_ID, role=AdminRole.COORDINATOR, scope_type=AdminScopeType.SCHOOL)
        await ensure_link(admin, session, external_user_id=TEACHER_ID, role=AdminRole.TEACHER, scope_type=AdminScopeType.SCHOOL)
        await ensure_link(
            admin, session, external_user_id=STUDENT_EXTERNAL_ID, role=AdminRole.STUDENT,
            scope_type=AdminScopeType.CLASSROOM, scope_external_id=TURMA_A_EXTERNAL_ID,
        )
        await session.commit()

        await ensure_student_enrollment(
            session, klass_id=turma_a_id, klass_external_id=turma_a_external_id, klass_name=turma_a_name,
        )
        await session.commit()

    await engine.dispose()
    print("\ndone.")
    print(f"school_id = {SCHOOL_ID}")
    print(f"identities to use (type into each portal's own identity field):")
    print(f"  admin master:        {PERFORMED_BY}")
    print(f"  coordenador (ABC):   user:{COORDINATOR_ID}   (coordination.js sends this raw as Bearer subject)")
    print(f"  professor (ABC):     user:{TEACHER_ID}   (teacher.js sends this raw as Bearer subject)")
    print(f"  aluno (ABC):         {STUDENT_EXTERNAL_ID}  (Turma A) (app.js prefixes again -> Bearer student:{STUDENT_EXTERNAL_ID})")


if __name__ == "__main__":
    asyncio.run(main())
