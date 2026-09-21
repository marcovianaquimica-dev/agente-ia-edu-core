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
    EssayPrompt,
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
from agente_ia_edu.providers.models import EssayOcrToken, EssayPageTranscriptionResult


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


def _make_png(path: Path) -> None:
    path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 16)


class _ScriptedTranscriber:
    """Test double for the route-level OCR call.

    NOTE: deviates from the task-11 brief's literal test code. The brief's
    route (src/agente_ia_edu/api/routes/essay_submissions.py) constructs
    EssaySubmissionService(session) with no transcriber override, so an
    unpatched HTTP call through this route lazily calls
    build_essay_transcriber() -> a real OpenAIProvider requiring
    OPENAI_API_KEY/OPENAI_VISION_MODEL and live network access to
    api.openai.com. Neither is configured in this repo's standard test
    environment (.env.example has no OPENAI_* vars, and no other R2 test
    reaches OpenAI - Task 8's own service-level tests inject a fake
    transcriber directly into EssaySubmissionService's constructor, which
    this HTTP-only route never exposes). Patching
    agente_ia_edu.services.essay_submission.build_essay_transcriber for the
    one test that exercises transcription is the minimal fix that lets the
    test verify what it's actually meant to verify - the HTTP
    upload/list/review/confirm plumbing - without a real external API call.
    This is flagged as a reportable gap in the task-11 report, not silently
    patched into production code."""

    async def transcribe_page(self, request):
        return EssayPageTranscriptionResult(
            tokens=(EssayOcrToken(text="texto", confidence=0.9, start=0, end=5),),
            provider="scripted", model="v1",
        )


class EssaySubmissionsRoutesTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        cls.loop.run_until_complete(_prep())
        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls.client = TestClient(cls.app)
        cls.tmp_dir = Path("/tmp/r2_route_test_fixtures")
        cls.tmp_dir.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _seed_enrolled_student(self, code: str, *, transcription_enabled: bool, module_enabled: bool = True):
        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"SUB-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                if module_enabled:
                    session.add(SchoolModule(
                        id=uuid.uuid4(), school_id=school.id, module_key="REDACAO_IA", enabled=True,
                    ))
                session.add(UserSchoolLink(
                    external_user_id=f"student_{code}", school_id=school.id, role="STUDENT",
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
                    external_identity_provider="test", external_user_id=f"student_{code}",
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

                from agente_ia_edu.services.institution_settings import InstitutionSettingsService
                await InstitutionSettingsService(session).configure(
                    school.id, performed_by_external_id="admin:x",
                    transcription_enabled=transcription_enabled,
                )
                await session.commit()
                return assignment.id

        return self.loop.run_until_complete(_seed())

    def test_typed_submission_end_to_end(self):
        assignment_id = self._seed_enrolled_student("1", transcription_enabled=True)
        self._as("student_1")

        create_resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "TYPED", "text": "Minha redacao."},
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        body = create_resp.json()
        self.assertEqual(body["status"], "SUBMITTED")
        self.assertEqual(body["mode"], "TYPED")

    def test_photo_flow_with_transcription_end_to_end(self):
        assignment_id = self._seed_enrolled_student("2", transcription_enabled=True)
        self._as("student_2")

        create_resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "PHOTO"},
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        submission_id = create_resp.json()["id"]
        self.assertEqual(create_resp.json()["status"], "PENDING_TRANSCRIPTION")

        source = self.tmp_dir / "route_page1.png"
        _make_png(source)
        with patch(
            "agente_ia_edu.services.essay_submission.build_essay_transcriber",
            return_value=_ScriptedTranscriber(),
        ):
            with open(source, "rb") as f:
                page_resp = self.client.post(
                    f"/api/v1/student/essay-submissions/{submission_id}/pages",
                    data={"page_number": "1"},
                    files={"file": ("page1.png", f, "image/png")},
                )
        self.assertEqual(page_resp.status_code, 201, page_resp.text)

        pages_resp = self.client.get(f"/api/v1/student/essay-submissions/{submission_id}/pages")
        self.assertEqual(pages_resp.status_code, 200)
        self.assertEqual(len(pages_resp.json()), 1)

        review_resp = self.client.patch(
            f"/api/v1/student/essay-submissions/{submission_id}/pages/1",
            json={"reviewed_text": "Texto revisado."},
        )
        self.assertEqual(review_resp.status_code, 200, review_resp.text)

        confirm_resp = self.client.post(
            f"/api/v1/student/essay-submissions/{submission_id}/confirm"
        )
        self.assertEqual(confirm_resp.status_code, 200, confirm_resp.text)
        self.assertEqual(confirm_resp.json()["status"], "SUBMITTED")

    def test_photo_flow_without_transcription_skips_review(self):
        assignment_id = self._seed_enrolled_student("3", transcription_enabled=False)
        self._as("student_3")

        create_resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "PHOTO"},
        )
        submission_id = create_resp.json()["id"]

        source = self.tmp_dir / "route_page_no_ocr.png"
        _make_png(source)
        with open(source, "rb") as f:
            self.client.post(
                f"/api/v1/student/essay-submissions/{submission_id}/pages",
                data={"page_number": "1"},
                files={"file": ("page1.png", f, "image/png")},
            )

        confirm_resp = self.client.post(
            f"/api/v1/student/essay-submissions/{submission_id}/confirm"
        )
        self.assertEqual(confirm_resp.status_code, 200, confirm_resp.text)
        self.assertIsNone(confirm_resp.json()["canonical_text"])


if __name__ == "__main__":
    unittest.main()
