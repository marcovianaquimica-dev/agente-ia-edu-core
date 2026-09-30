import csv
import tempfile
import unittest
import uuid
from pathlib import Path

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import Class, GradeLevel, Person, School, Segment, Student, StudentEnrollment
from scripts.import_students_csv import import_students_from_csv

CSV_ROWS = [
    {"nome": "Ana Lúcia Ferreira", "documento": "12345678900", "segmento": "Médio",
     "serie": "3ª Série", "turma": "3A", "ano_letivo": "2026"},
    {"nome": "Bruno Costa", "documento": "", "segmento": "Médio",
     "serie": "3ª Série", "turma": "3A", "ano_letivo": "2026"},
    {"nome": "Carla Dias", "documento": "98765432100", "segmento": "Médio",
     "serie": "3ª Série", "turma": "3B", "ano_letivo": "2026"},
]


class ImportStudentsCsvTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

        async with self.session_factory() as session:
            self.school = School(id=uuid.uuid4(), code="EST-CSV", name="Rede Estadual Teste")
            session.add(self.school)
            await session.commit()

        self.csv_path = Path(tempfile.mktemp(suffix=".csv"))
        with self.csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["nome", "documento", "segmento", "serie", "turma", "ano_letivo"])
            writer.writeheader()
            writer.writerows(CSV_ROWS)

    async def asyncTearDown(self):
        await self.engine.dispose()
        self.csv_path.unlink(missing_ok=True)

    async def test_imports_three_students_into_two_classes(self):
        async with self.session_factory() as session:
            await import_students_from_csv(session, self.csv_path, school_id=self.school.id)

        async with self.session_factory() as session:
            students = (await session.execute(select(Student))).scalars().all()
            self.assertEqual(len(students), 3)
            classes = (await session.execute(select(Class))).scalars().all()
            self.assertEqual({c.name for c in classes}, {"3A", "3B"})
            enrollments = (await session.execute(select(StudentEnrollment))).scalars().all()
            self.assertEqual(len(enrollments), 3)
            segments = (await session.execute(select(Segment))).scalars().all()
            self.assertEqual(len(segments), 1)  # so um "Medio" - nao duplicou
            grade_levels = (await session.execute(select(GradeLevel))).scalars().all()
            self.assertEqual(len(grade_levels), 1)  # so uma "3a Serie" - nao duplicou

    async def test_running_twice_with_the_same_csv_never_duplicates(self):
        async with self.session_factory() as session:
            await import_students_from_csv(session, self.csv_path, school_id=self.school.id)
        async with self.session_factory() as session:
            await import_students_from_csv(session, self.csv_path, school_id=self.school.id)

        async with self.session_factory() as session:
            students = (await session.execute(select(Student))).scalars().all()
            self.assertEqual(len(students), 3)
            persons = (await session.execute(select(Person))).scalars().all()
            self.assertEqual(len(persons), 3)

    async def test_same_grade_name_in_different_segments_creates_two_grade_levels(self):
        rows = [
            {"nome": "Diego Alves", "documento": "11111111111", "segmento": "Fundamental II",
             "serie": "9º Ano", "turma": "9A", "ano_letivo": "2026"},
            {"nome": "Elisa Gomes", "documento": "22222222222", "segmento": "EJA",
             "serie": "9º Ano", "turma": "EJA-A", "ano_letivo": "2026"},
        ]
        csv_path = Path(tempfile.mktemp(suffix=".csv"))
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=["nome", "documento", "segmento", "serie", "turma", "ano_letivo"])
            writer.writeheader()
            writer.writerows(rows)
        try:
            async with self.session_factory() as session:
                await import_students_from_csv(session, csv_path, school_id=self.school.id)

            async with self.session_factory() as session:
                grade_levels = (await session.execute(select(GradeLevel))).scalars().all()
                self.assertEqual(len(grade_levels), 2)
                self.assertEqual({gl.name for gl in grade_levels}, {"9º Ano"})
                self.assertEqual(len({gl.segment_id for gl in grade_levels}), 2)
        finally:
            csv_path.unlink(missing_ok=True)


if __name__ == "__main__":
    unittest.main()
