import asyncio
import unittest
import uuid
from pathlib import Path

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
from agente_ia_edu.services.essay_submission import EssaySubmissionService
from agente_ia_edu.services.material_storage import MaterialStorage


class _ScriptedTranscriber:
    """Test double: returns a fixed, controllable token list regardless of
    which image it's given - real image content is irrelevant to what these
    tests check (the service's own page/status bookkeeping)."""

    def __init__(self, tokens):
        self._tokens = tokens
        self.calls = 0

    async def transcribe_page(self, request):
        self.calls += 1
        return EssayPageTranscriptionResult(tokens=self._tokens, provider="scripted", model="v1")


def _make_png(path: Path) -> None:
    import pymupdf
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
    pix.clear_with(255)
    path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(path))


def _make_jpg(path: Path) -> None:
    # Same pixmap-based approach as _make_png; pymupdf.Pixmap.save() infers
    # the encoder from the output extension, and it supports JPEG output
    # just as it supports PNG - the fixture just needs a real, decodable
    # image on disk because upload_page() measures it via
    # pymupdf.Pixmap(path) (see essay_submission.py's _measure_page_image).
    import pymupdf
    pix = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 100, 100), False)
    pix.clear_with(255)
    path.parent.mkdir(parents=True, exist_ok=True)
    pix.save(str(path))


class PhotoUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.storage_root = Path("/tmp/r2_upload_test_storage")
        self.tmp_dir = Path("/tmp/r2_upload_test_fixtures")
        self.tmp_dir.mkdir(exist_ok=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_upload_page_with_transcription_runs_ocr_synchronously(self):
        async with self.session_factory() as session:
            tokens = (
                EssayOcrToken(text="Ola", confidence=0.99, start=0, end=3),
                EssayOcrToken(text="mundo", confidence=0.4, start=4, end=9),
            )
            transcriber = _ScriptedTranscriber(tokens)
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=True,
            )
            self.assertEqual(submission.anchor_mode, "TEXT_OFFSET")
            self.assertEqual(submission.status, "PENDING_TRANSCRIPTION")

            source = self.tmp_dir / "page1.png"
            _make_png(source)
            page = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1,
                source_path=source,
            )

            self.assertEqual(transcriber.calls, 1)
            self.assertEqual(len(page.ocr_tokens), 2)
            self.assertEqual(page.ocr_tokens[0]["text"], "Ola")
            self.assertEqual(page.ocr_tokens[1]["confidence"], 0.4)
            self.assertIsNone(page.reviewed_text)

            refreshed = await session.get(type(submission), submission.id)
            self.assertEqual(refreshed.status, "PENDING_CONFIRMATION")

    async def test_reuploading_the_same_page_number_replaces_it(self):
        async with self.session_factory() as session:
            transcriber = _ScriptedTranscriber(
                (EssayOcrToken(text="v1", confidence=0.9, start=0, end=2),)
            )
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=True,
            )

            source1 = self.tmp_dir / "reupload1.png"
            _make_png(source1)
            page_first = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1,
                source_path=source1,
            )

            source2 = self.tmp_dir / "reupload2.png"
            _make_png(source2)
            page_second = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1,
                source_path=source2,
            )

            self.assertEqual(page_first.id, page_second.id)
            self.assertEqual(transcriber.calls, 2)

    async def test_upload_page_without_transcription_never_calls_the_transcriber(self):
        async with self.session_factory() as session:
            transcriber = _ScriptedTranscriber((EssayOcrToken(text="x", confidence=1.0, start=0, end=1),))
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=False,
            )
            self.assertEqual(submission.anchor_mode, "IMAGE_REGION")

            source = self.tmp_dir / "no_ocr.png"
            _make_png(source)
            page = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1,
                source_path=source,
            )
            self.assertEqual(transcriber.calls, 0)
            self.assertIsNone(page.ocr_tokens)

    async def test_upload_page_with_jpg_extension_succeeds(self):
        # No test in this class had exercised a .jpg/.jpeg page before -
        # _ALLOWED_PAGE_SUFFIXES in api/routes/essay_submissions.py accepts
        # .png, .jpg and .jpeg alike, and this confirms the service layer
        # (which this class calls directly, bypassing the route) handles a
        # .jpg source file exactly like the .png ones above: same
        # EssaySubmissionPage shape, OCR still runs.
        async with self.session_factory() as session:
            tokens = (EssayOcrToken(text="Ola", confidence=0.95, start=0, end=3),)
            transcriber = _ScriptedTranscriber(tokens)
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PHOTO", transcription_enabled=True,
            )

            source = self.tmp_dir / "page1.jpg"
            _make_jpg(source)
            page = await svc.upload_page(
                essay_submission_id=submission.id, page_number=1,
                source_path=source,
            )

            self.assertEqual(transcriber.calls, 1)
            self.assertEqual(len(page.ocr_tokens), 1)
            self.assertIsNone(page.reviewed_text)


class PdfUploadTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        self.storage_root = Path("/tmp/r2_upload_test_storage_pdf")
        self.tmp_dir = Path("/tmp/r2_upload_test_fixtures_pdf")
        self.tmp_dir.mkdir(exist_ok=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _make_two_page_pdf(self) -> Path:
        import pymupdf as fitz

        path = self.tmp_dir / "two_pages.pdf"
        doc = fitz.open()
        for _ in range(2):
            page = doc.new_page()
            page.insert_text((72, 72), "pagina de teste")
        doc.save(str(path))
        doc.close()
        return path

    async def test_upload_document_splits_a_pdf_into_one_page_per_call(self):
        async with self.session_factory() as session:
            transcriber = _ScriptedTranscriber(
                (EssayOcrToken(text="pagina", confidence=0.9, start=0, end=6),)
            )
            svc = EssaySubmissionService(
                session, storage=MaterialStorage(root=self.storage_root), transcriber=transcriber
            )
            school_id, assignment_id, student_id = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
            submission = await svc.start_photo_submission(
                school_id=school_id, prompt_assignment_id=assignment_id,
                student_id=student_id, mode="PDF", transcription_enabled=True,
            )

            pdf_path = self._make_two_page_pdf()
            pages = await svc.upload_document(
                essay_submission_id=submission.id, source_path=pdf_path,
            )

            self.assertEqual(len(pages), 2)
            self.assertEqual([p.page_number for p in pages], [1, 2])
            self.assertEqual(transcriber.calls, 2)
            for page in pages:
                self.assertEqual(len(page.ocr_tokens), 1)


class PageFormatValidationRouteTests(unittest.TestCase):
    """Exercises the real HTTP route, not the service layer.

    The suffix allowlist (_ALLOWED_PAGE_SUFFIXES) and the "unsupported file
    format" 422 live only in upload_essay_submission_page
    (api/routes/essay_submissions.py) - EssaySubmissionService.upload_page,
    which PhotoUploadTests above calls directly, never checks the file
    extension at all. So a rejection test written against the service layer
    would be vacuous (it would still pass if the route-level check were
    deleted). Setup mirrors EssaySubmissionsRoutesTests in
    tests/test_r2_essay_submissions_routes.py.
    """

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
        cls.tmp_dir = Path("/tmp/r2_upload_test_fixtures_format")
        cls.tmp_dir.mkdir(exist_ok=True)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: ExternalIdentityContext(
            provider="test", external_user_id=user
        )

    def _seed_submission(self, code: str) -> str:
        """Seeds one enrolled student (module enabled, transcription off -
        irrelevant to a suffix check) and creates a PHOTO-mode submission for
        them, mirroring _seed_enrolled_student in
        test_r2_essay_submissions_routes.py."""

        async def _seed():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"FMT-{code}", name=f"school-{code}")
                session.add(school)
                await session.flush()
                session.add(SchoolModule(
                    id=uuid.uuid4(), school_id=school.id, module_key="REDACAO_IA", enabled=True,
                ))
                session.add(UserSchoolLink(
                    external_user_id=f"student_fmt_{code}", school_id=school.id, role="STUDENT",
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
                    external_identity_provider="test", external_user_id=f"student_fmt_{code}",
                ))
                student = Student(
                    id=uuid.uuid4(), school_id=school.id, person_id=person.id, student_code=f"ST-{code}"
                )
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
                    transcription_enabled=False,
                )
                await session.commit()
                return assignment.id

        assignment_id = self.loop.run_until_complete(_seed())
        self._as(f"student_fmt_{code}")
        create_resp = self.client.post(
            "/api/v1/student/essay-submissions",
            json={"prompt_assignment_id": str(assignment_id), "mode": "PHOTO"},
        )
        self.assertEqual(create_resp.status_code, 201, create_resp.text)
        return create_resp.json()["id"]

    def test_upload_page_with_jpg_extension_succeeds_via_route(self):
        submission_id = self._seed_submission("jpg")
        source = self.tmp_dir / "page1.jpg"
        _make_jpg(source)
        with open(source, "rb") as f:
            resp = self.client.post(
                f"/api/v1/student/essay-submissions/{submission_id}/pages",
                data={"page_number": "1"},
                files={"file": ("page1.jpg", f, "image/jpeg")},
            )
        self.assertEqual(resp.status_code, 201, resp.text)

    def test_upload_page_with_unsupported_extension_is_rejected(self):
        submission_id = self._seed_submission("gif")
        source = self.tmp_dir / "page1.gif"
        # The route rejects on the filename suffix before it ever reads/
        # decodes the file body (see upload_essay_submission_page), so the
        # bytes here don't need to be a real image.
        source.write_bytes(b"not a real gif")
        with open(source, "rb") as f:
            resp = self.client.post(
                f"/api/v1/student/essay-submissions/{submission_id}/pages",
                data={"page_number": "1"},
                files={"file": ("page1.gif", f, "image/gif")},
            )
        self.assertEqual(resp.status_code, 422, resp.text)
        self.assertIn("unsupported file format", resp.json()["detail"])


if __name__ == "__main__":
    unittest.main()
