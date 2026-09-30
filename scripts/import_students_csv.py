# scripts/import_students_csv.py
"""Provisionamento em massa de alunos/turmas a partir de um CSV (spec
2026-09-29, "correcao em massa"). Get-or-create em cada nivel da hierarquia
(Segment -> AcademicYear -> GradeLevel -> Class) - rodar o mesmo CSV duas
vezes nunca duplica nada, identificado por nome dentro da mesma escola.

Uso: .venv/bin/python scripts/import_students_csv.py --school-id <uuid> --csv caminho.csv
"""
from __future__ import annotations

import argparse
import asyncio
import csv
import os
import sys
import uuid
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.models import (
    AcademicYear, Class, GradeLevel, Person, Segment, Student, StudentEnrollment,
)


async def _get_or_create_segment(session: AsyncSession, *, school_id: uuid.UUID, name: str) -> Segment:
    existing = (await session.execute(
        select(Segment).where(Segment.school_id == school_id, Segment.name == name)
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    segment = Segment(id=uuid.uuid4(), school_id=school_id, name=name)
    session.add(segment)
    await session.flush()
    return segment


async def _get_or_create_academic_year(session: AsyncSession, *, school_id: uuid.UUID, year: int) -> AcademicYear:
    existing = (await session.execute(
        select(AcademicYear).where(AcademicYear.school_id == school_id, AcademicYear.year == year)
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    academic_year = AcademicYear(id=uuid.uuid4(), school_id=school_id, year=year, status="ACTIVE")
    session.add(academic_year)
    await session.flush()
    return academic_year


async def _get_or_create_grade_level(
    session: AsyncSession, *, school_id: uuid.UUID, segment_id: uuid.UUID, name: str,
) -> GradeLevel:
    existing = (await session.execute(
        select(GradeLevel).where(GradeLevel.school_id == school_id, GradeLevel.name == name)
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    grade_level = GradeLevel(id=uuid.uuid4(), school_id=school_id, segment_id=segment_id, name=name)
    session.add(grade_level)
    await session.flush()
    return grade_level


async def _get_or_create_class(
    session: AsyncSession, *, school_id: uuid.UUID, academic_year_id: uuid.UUID,
    grade_level_id: uuid.UUID, name: str,
) -> Class:
    existing = (await session.execute(
        select(Class).where(
            Class.academic_year_id == academic_year_id, Class.grade_level_id == grade_level_id,
            Class.name == name,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return existing
    klass = Class(
        id=uuid.uuid4(), school_id=school_id, academic_year_id=academic_year_id,
        grade_level_id=grade_level_id, name=name,
    )
    session.add(klass)
    await session.flush()
    return klass


async def _get_or_create_student(
    session: AsyncSession, *, school_id: uuid.UUID, full_name: str, document_number: str | None,
) -> Student:
    existing_person = (await session.execute(
        select(Person).where(Person.school_id == school_id, Person.full_name == full_name)
    )).scalar_one_or_none()
    if existing_person is None:
        existing_person = Person(
            id=uuid.uuid4(), school_id=school_id, full_name=full_name,
            document_number=document_number or None,
        )
        session.add(existing_person)
        await session.flush()
    existing_student = (await session.execute(
        select(Student).where(Student.school_id == school_id, Student.person_id == existing_person.id)
    )).scalar_one_or_none()
    if existing_student is not None:
        return existing_student
    student = Student(id=uuid.uuid4(), school_id=school_id, person_id=existing_person.id)
    session.add(student)
    await session.flush()
    return student


async def _ensure_enrollment(
    session: AsyncSession, *, school_id: uuid.UUID, student_id: uuid.UUID, class_id: uuid.UUID,
) -> None:
    existing = (await session.execute(
        select(StudentEnrollment).where(
            StudentEnrollment.student_id == student_id, StudentEnrollment.class_id == class_id,
        )
    )).scalar_one_or_none()
    if existing is not None:
        return
    session.add(StudentEnrollment(
        id=uuid.uuid4(), school_id=school_id, student_id=student_id, class_id=class_id,
    ))
    await session.flush()


async def import_students_from_csv(session: AsyncSession, csv_path: Path, *, school_id: uuid.UUID) -> None:
    with csv_path.open(encoding="utf-8") as f:
        rows = list(csv.DictReader(f))

    for row in rows:
        segment = await _get_or_create_segment(session, school_id=school_id, name=row["segmento"])
        academic_year = await _get_or_create_academic_year(
            session, school_id=school_id, year=int(row["ano_letivo"])
        )
        grade_level = await _get_or_create_grade_level(
            session, school_id=school_id, segment_id=segment.id, name=row["serie"]
        )
        klass = await _get_or_create_class(
            session, school_id=school_id, academic_year_id=academic_year.id,
            grade_level_id=grade_level.id, name=row["turma"],
        )
        student = await _get_or_create_student(
            session, school_id=school_id, full_name=row["nome"],
            document_number=row.get("documento") or None,
        )
        await _ensure_enrollment(session, school_id=school_id, student_id=student.id, class_id=klass.id)

    await session.commit()


async def main(school_id: uuid.UUID, csv_path: Path) -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    database_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with session_factory() as session:
            await import_students_from_csv(session, csv_path, school_id=school_id)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--school-id", required=True, type=uuid.UUID)
    parser.add_argument("--csv", required=True, type=Path)
    args = parser.parse_args()

    asyncio.run(main(args.school_id, args.csv))
