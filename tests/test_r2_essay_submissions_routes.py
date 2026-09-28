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
from agente_ia_edu.providers.models import EssayOcrToken, EssayPageTranscriptionResult


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


def _make_png(path: Path) -> None:
    import pymupdf
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
    pix.clear_with(255)
    path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(path))


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

    def _seed_enrolled_student(
        self, code: str, *, transcription_enabled: bool, module_enabled: bool = True,
        is_free_theme: bool = False,
    ):
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
                    is_free_theme=is_free_theme,
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

    def test_pdf_flow_uses_the_document_endpoint_and_splits_server_side(self):
        assignment_id = self._seed_enrolled_student("4", transcription_enabled=False)
        self._as("student_4")

        create_resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "PDF"},
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        submission_id = create_resp.json()["id"]

        import pymupdf as fitz
        pdf_path = self.tmp_dir / "route_two_pages.pdf"
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

        confirm_resp = self.client.post(
            f"/api/v1/student/essay-submissions/{submission_id}/confirm"
        )
        self.assertEqual(confirm_resp.status_code, 200, confirm_resp.text)

    def test_typed_submission_with_free_theme_requires_declared_theme(self):
        assignment_id = self._seed_enrolled_student("5", transcription_enabled=True, is_free_theme=True)
        self._as("student_5")

        create_resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "TYPED", "text": "Minha redacao."},
        )
        self.assertEqual(create_resp.status_code, 422, create_resp.text)

    def test_typed_submission_with_free_theme_stores_declared_theme(self):
        assignment_id = self._seed_enrolled_student("6", transcription_enabled=True, is_free_theme=True)
        self._as("student_6")

        create_resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={
                "prompt_assignment_id": str(assignment_id), "mode": "TYPED", "text": "Minha redacao.",
                "student_declared_theme": "O futuro do trabalho remoto",
            },
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)

    def test_list_essay_prompts_pins_free_theme_first_and_exposes_flag(self):
        code = "7"

        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"SUB-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
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

                # Free-theme prompt/assignment created FIRST (older created_at)
                # so the test actually proves is_free_theme wins the sort
                # over recency, not merely that it happens to be newest.
                free_prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Tema livre", statement="Escolha seu tema.",
                    year=2026, status="ACTIVE", created_by_external_identity="teacher:t", is_free_theme=True,
                )
                session.add(free_prompt)
                await session.flush()
                from datetime import datetime, timedelta, timezone
                older = datetime.now(timezone.utc) - timedelta(days=1)
                newer = datetime.now(timezone.utc)

                free_assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=school.id, essay_prompt_id=free_prompt.id,
                    class_id=klass.id, assigned_by_external_identity="teacher:t",
                    created_at=older,
                )
                session.add(free_assignment)
                await session.commit()

                normal_prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Tema fixo", statement="Disserte.",
                    year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
                )
                session.add(normal_prompt)
                await session.flush()
                normal_assignment = PromptAssignment(
                    id=uuid.uuid4(), school_id=school.id, essay_prompt_id=normal_prompt.id,
                    class_id=klass.id, assigned_by_external_identity="teacher:t",
                    created_at=newer,
                )
                session.add(normal_assignment)
                await session.commit()

                from agente_ia_edu.services.institution_settings import InstitutionSettingsService
                await InstitutionSettingsService(session).configure(
                    school.id, performed_by_external_id="admin:x", transcription_enabled=True,
                )
                await session.commit()

        self.loop.run_until_complete(_seed())
        self._as(f"student_{code}")

        list_resp = self.client.get("/api/v1/student/essay-prompts")
        self.assertEqual(list_resp.status_code, 200, list_resp.text)
        body = list_resp.json()
        self.assertEqual(len(body), 2)
        self.assertEqual(body[0]["title"], "Tema livre")
        self.assertTrue(body[0]["is_free_theme"])
        self.assertEqual(body[1]["title"], "Tema fixo")
        self.assertFalse(body[1]["is_free_theme"])

    def test_approved_free_theme_submission_reopens_the_slot_for_a_new_essay(self):
        assignment_id = self._seed_enrolled_student("8", transcription_enabled=True, is_free_theme=True)

        async def _approve_a_submission():
            async with self.factory() as session:
                import datetime as dt
                from sqlalchemy import select as sa_select

                assignment = await session.get(PromptAssignment, assignment_id)
                student = (await session.execute(
                    sa_select(Student).where(Student.school_id == assignment.school_id)
                )).scalars().first()
                now = dt.datetime.now(dt.timezone.utc)
                submission = EssaySubmission(
                    id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=assignment.school_id,
                    prompt_assignment_id=assignment_id, student_id=student.id,
                    mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
                    canonical_text="Redacao anterior.", normalized_text_hash="a" * 64,
                    submitted_at=now, student_declared_theme="Um tema ja corrigido",
                )
                session.add(submission)
                await session.flush()
                session.add(EssayCorrection(
                    id=uuid.uuid4(), school_id=assignment.school_id, essay_submission_id=submission.id,
                    correction_key="k" * 64, rubric_version="ENEM_2025", model_version="gpt-test",
                    prompt_version="essay_correction_v1", engine_version="r3_correction_engine_v1",
                    ai_output={"annotations": [], "rewrites": [], "intervention": {}, "alerts": []},
                    final_scores={"total": 800}, final_feedback={},
                    status="APPROVED", reviewed_at=now, published_at=now,
                ))
                await session.commit()

        self.loop.run_until_complete(_approve_a_submission())
        self._as("student_8")

        list_resp = self.client.get("/api/v1/student/essay-prompts")
        self.assertEqual(list_resp.status_code, 200, list_resp.text)
        body = list_resp.json()
        free_theme_entry = next(p for p in body if p["prompt_assignment_id"] == str(assignment_id))
        self.assertIsNone(free_theme_entry["my_submission"])


if __name__ == "__main__":
    unittest.main()
