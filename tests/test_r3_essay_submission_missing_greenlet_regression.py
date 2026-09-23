"""Regression test: create_essay_submission and confirm_essay_submission
used to read ORM attributes (via _submission_to_response) AFTER
session.commit(). In production the app's real session uses
expire_on_commit=True (see db/session.py), so that post-commit access
triggers a synchronous lazy-load and raises MissingGreenlet under the
async engine - a 500 on every TYPED create and every PHOTO/PDF confirm.
Every other test file for these routes builds its session_factory with
expire_on_commit=False, which silently masks this. This file is the only
one that mirrors the production session configuration."""

import asyncio
import unittest
import uuid
from pathlib import Path
from unittest.mock import patch

from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayCorrection,
    EssayPrompt,
    EssaySubmission,
    GradeLevel,
    Person,
    PromptAssignment,
    School,
    SchoolModule,
    Segment,
    Student,
    StudentEnrollment,
    User,
    UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.institution_settings import InstitutionSettingsService


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


async def _fake_correct(self, essay_submission_id):
    submission = await self.session.get(EssaySubmission, essay_submission_id)
    correction = EssayCorrection(
        id=uuid.uuid4(), school_id=submission.school_id,
        essay_submission_id=submission.id, correction_key="k" * 64,
        rubric_version="ENEM_2025", model_version="stub", prompt_version="essay_correction_v1",
        engine_version="r3_correction_engine_v1", ai_output={"scores": None},
        final_feedback={"strengths": [], "improvements": [], "next_essay_strategy": "x"},
        status="APPROVED", reviewed_at=submission.submitted_at,
        published_at=submission.submitted_at,
    )
    self.session.add(correction)
    await self.session.flush()
    return correction


class SubmissionRoutesExpireOnCommitTests(unittest.TestCase):
    """Same route exercise as test_r3_essay_submission_confirm_triggers_correction.py,
    but with expire_on_commit=True to match production and actually catch the
    MissingGreenlet regression that expire_on_commit=False fixtures hide."""

    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=True)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        cls.loop.run_until_complete(_prep())
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls.client = TestClient(cls.app)
        cls.tmp_dir = Path("/tmp/r3_expire_on_commit_regression_fixtures")
        cls.tmp_dir.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _seed_assignment(self, code: str) -> uuid.UUID:
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"EOC-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(SchoolModule(
                    id=uuid.uuid4(), school_id=school.id, module_key="REDACAO_IA", enabled=True,
                ))
                session.add(UserSchoolLink(
                    external_user_id=f"student_eoc_{code}", school_id=school.id, role="STUDENT",
                    scope_type="SCHOOL", active=True,
                ))
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
                person = Person(id=uuid.uuid4(), school_id=school.id, full_name=f"Aluno {code}")
                session.add(person)
                await session.flush()
                session.add(User(
                    id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                    external_identity_provider="test", external_user_id=f"student_eoc_{code}",
                ))
                student = Student(id=uuid.uuid4(), school_id=school.id, person_id=person.id, student_code=f"ST-{code}")
                session.add(student)
                await session.flush()
                session.add(StudentEnrollment(
                    id=uuid.uuid4(), school_id=school.id, student_id=student.id, class_id=klass.id,
                    status="ACTIVE",
                ))
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
                    year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
                )
                session.add(prompt)
                await session.flush()
                assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                    class_id=klass.id, assigned_by_external_identity="teacher:t",
                )
                session.add(assignment)
                await InstitutionSettingsService(session).configure(
                    school.id, performed_by_external_id="admin:x", transcription_enabled=False,
                )
                assignment_id = assignment.id
                await session.commit()
                return assignment_id

        return self.loop.run_until_complete(_seed())

    def test_typed_create_does_not_500_under_expire_on_commit(self):
        assignment_id = self._seed_assignment("1")
        self._as("student_eoc_1")

        with patch(
            "agente_ia_edu.services.essay_correction.EssayCorrectionService.correct",
            new=_fake_correct,
        ):
            create_resp = self.client.post(
                "/api/v1/student/essay-submissions",
                json={
                    "prompt_assignment_id": str(assignment_id), "mode": "TYPED",
                    "text": "Uma redacao qualquer para testar o regression de MissingGreenlet.",
                },
            )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        body = create_resp.json()
        self.assertEqual(body["status"], "SUBMITTED")
        self.assertEqual(body["mode"], "TYPED")

    def test_pdf_document_upload_does_not_500_under_expire_on_commit(self):
        assignment_id = self._seed_assignment("3")
        self._as("student_eoc_3")

        create_resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "PDF"},
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        submission_id = create_resp.json()["id"]

        import pymupdf as fitz

        pdf_path = self.tmp_dir / "eoc_two_pages.pdf"
        doc = fitz.open()
        for _ in range(2):
            page = doc.new_page()
            page.insert_text((72, 72), "pagina de teste")
        doc.save(str(pdf_path))
        doc.close()

        with open(pdf_path, "rb") as f:
            document_resp = self.client.post(
                f"/api/v1/student/essay-submissions/{submission_id}/document",
                files={"file": ("redacao.pdf", f, "application/pdf")},
            )
        self.assertEqual(document_resp.status_code, 201, document_resp.text)
        pages = document_resp.json()
        self.assertEqual(len(pages), 2)
        self.assertEqual([p["page_number"] for p in pages], [1, 2])

    def test_page_review_does_not_500_under_expire_on_commit(self):
        assignment_id = self._seed_assignment("4")
        self._as("student_eoc_4")

        create_resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "PHOTO"},
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        submission_id = create_resp.json()["id"]

        source = self.tmp_dir / "eoc_review_page.png"
        import pymupdf
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
        pix.clear_with(255)
        pix.save(str(source))
        with open(source, "rb") as f:
            page_resp = self.client.post(
                f"/api/v1/student/essay-submissions/{submission_id}/pages",
                data={"page_number": "1"}, files={"file": ("page1.png", f, "image/png")},
            )
        self.assertEqual(page_resp.status_code, 201, page_resp.text)

        review_resp = self.client.patch(
            f"/api/v1/student/essay-submissions/{submission_id}/pages/1",
            json={"reviewed_text": "Texto revisado."},
        )
        self.assertEqual(review_resp.status_code, 200, review_resp.text)
        body = review_resp.json()
        self.assertEqual(body["reviewed_text"], "Texto revisado.")

    def test_photo_confirm_does_not_500_under_expire_on_commit(self):
        assignment_id = self._seed_assignment("2")
        self._as("student_eoc_2")

        create_resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "PHOTO"},
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        submission_id = create_resp.json()["id"]

        source = self.tmp_dir / "eoc_page.png"
        import pymupdf
        pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
        pix.clear_with(255)
        pix.save(str(source))
        with open(source, "rb") as f:
            page_resp = self.client.post(
                f"/api/v1/student/essay-submissions/{submission_id}/pages",
                data={"page_number": "1"}, files={"file": ("page1.png", f, "image/png")},
            )
        self.assertEqual(page_resp.status_code, 201, page_resp.text)

        with patch(
            "agente_ia_edu.services.essay_correction.EssayCorrectionService.correct",
            new=_fake_correct,
        ):
            confirm_resp = self.client.post(
                f"/api/v1/student/essay-submissions/{submission_id}/confirm"
            )
        self.assertEqual(confirm_resp.status_code, 200, confirm_resp.text)
        body = confirm_resp.json()
        self.assertEqual(body["status"], "SUBMITTED")


if __name__ == "__main__":
    unittest.main()
