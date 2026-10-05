"""R4 lote - rotas do professor: criar lote, acompanhar, resolver, ver imagem.

TestClient + dependency_overrides, mesmo padrao de
tests/test_r2_essay_prompts_routes.py. O provedor de OCR e o de correcao sao
substituidos por dublês, entao nenhuma chamada de IA acontece aqui.
"""

import asyncio
import shutil
import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear, Class, EssayBatchPage, EssayPrompt, EssaySubmission, GradeLevel,
    Person, PromptAssignment, School, Segment, Student, StudentEnrollment, UserSchoolLink,
)
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.providers.models import EssayOcrToken, EssayPageTranscriptionResult


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


class ScriptedTranscriber:
    script: dict[str, tuple[str, str]] = {}

    async def transcribe_page(self, request):
        import re

        import pymupdf

        doc = pymupdf.open(str(request.image_path))
        try:
            stamped = doc[0].get_text().strip().splitlines()
        finally:
            doc.close()
        key = next((line.strip() for line in stamped if line.strip()), "")
        # get_text() nunca acha nada num PNG rasterizado (sem camada de
        # texto) - a rota grava o carimbo como pixels, nao como texto
        # selecionavel. Fallback validado nas Tarefas 6-9: usa o nome do
        # arquivo, que segue o esquema real "<stem>_header.png"/
        # "<stem>_body.png". A rota desta tarefa ainda prefixa o stem com um
        # indice de upload ("000_p1"), entao esse prefixo numerico tambem
        # precisa ser descartado pra sobrar so a chave do roteiro ("p1").
        if not key:
            stem = request.image_path.stem
            if "_" in stem:
                stem = stem.rsplit("_", 1)[0]
            key = re.sub(r"^\d+_", "", stem)
        header, body = ScriptedTranscriber.script.get(key, ("", ""))
        text = header if "_header" in request.image_path.name else body
        return EssayPageTranscriptionResult(
            tokens=(EssayOcrToken(text=text, confidence=0.95, start=0, end=len(text)),),
            provider="fake", model="fake-1",
        )


class NoopCorrectionService:
    def __init__(self, session):
        self.session = session

    async def correct(self, essay_submission_id):
        return None


def _write_stamped_page(path: Path, key: str) -> Path:
    import pymupdf

    doc = pymupdf.open()
    page = doc.new_page(width=595.44, height=842.40)
    page.insert_text((50, 60), key, fontsize=11)
    page.insert_text((50, 500), key, fontsize=11)
    page.get_pixmap(dpi=100).save(str(path))
    doc.close()
    return path


class EssayBatchesRoutesTests(unittest.TestCase):
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
        cls.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_lote")
        cls.client = TestClient(cls.app)
        # Pausa entre paginas e pra producao (rajada na API de visao) - sem
        # serventia aqui (transcritor falso) e so deixaria a suite lenta.
        from agente_ia_edu.services.essay_batch import EssayBatchService
        cls._original_pacing = EssayBatchService._PAGE_PACING_SECONDS
        EssayBatchService._PAGE_PACING_SECONDS = 0.0

    @classmethod
    def tearDownClass(cls):
        from agente_ia_edu.services.essay_batch import EssayBatchService
        EssayBatchService._PAGE_PACING_SECONDS = cls._original_pacing
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def setUp(self):
        import agente_ia_edu.api.routes.essay_batches as routes_module

        self.tmp_dir = Path(tempfile.mkdtemp(prefix="r4_routes_"))
        self._original_build = routes_module.build_batch_service
        storage_root = self.tmp_dir / "storage"

        def _build(session):
            from agente_ia_edu.services.essay_batch import EssayBatchService
            from agente_ia_edu.services.material_storage import MaterialStorage

            return EssayBatchService(
                session, storage=MaterialStorage(root=storage_root),
                transcriber=ScriptedTranscriber(),
                correction_factory=NoopCorrectionService,
            )

        routes_module.build_batch_service = _build

    def tearDown(self):
        import agente_ia_edu.api.routes.essay_batches as routes_module

        routes_module.build_batch_service = self._original_build
        shutil.rmtree(self.tmp_dir, ignore_errors=True)
        self.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_lote")

    def _seed(self, code: str, student_names):
        async def _run():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"BR-{code}", name=f"escola-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="prof_lote", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                segment = Segment(
                    id=uuid.uuid4(), school_id=school.id, name="seg", external_id=f"SEG-{code}"
                )
                session.add(segment)
                await session.flush()
                grade = GradeLevel(
                    id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
                    name="3a", external_id=f"GRADE-{code}",
                )
                year = AcademicYear(
                    id=uuid.uuid4(), school_id=school.id, year=2026, external_id=f"YEAR-{code}"
                )
                session.add_all([grade, year])
                await session.flush()
                klass = Class(
                    id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
                    grade_level_id=grade.id, name="3A", external_id=f"TURMA-{code}",
                )
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
                    year=2026, created_by_external_identity="prof_lote",
                )
                session.add_all([klass, prompt])
                await session.flush()
                session.add(PromptAssignment(
                    id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
                    class_id=klass.id, assigned_by_external_identity="prof_lote",
                ))
                students = {}
                for index, full_name in enumerate(student_names):
                    person = Person(
                        id=uuid.uuid4(), school_id=school.id, full_name=full_name,
                        external_id=f"PER-{code}-{index}",
                    )
                    session.add(person)
                    await session.flush()
                    student = Student(
                        id=uuid.uuid4(), school_id=school.id, person_id=person.id,
                        external_id=f"STU-{code}-{index}",
                    )
                    session.add(student)
                    await session.flush()
                    session.add(StudentEnrollment(
                        id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                        class_id=klass.id, status="ACTIVE", external_id=f"ENR-{code}-{index}",
                    ))
                    students[full_name] = str(student.id)
                await session.commit()
                return str(school.id), str(klass.id), str(prompt.id), students

        return self.loop.run_until_complete(_run())

    def _upload(self, prompt_id, class_id, keys):
        files = []
        for key in keys:
            path = _write_stamped_page(self.tmp_dir / f"{key}.png", key)
            files.append(("files", (f"{key}.png", path.read_bytes(), "image/png")))
        return self.client.post(
            "/api/v1/teacher/essay-batches",
            data={"essay_prompt_id": prompt_id, "class_id": class_id},
            files=files,
        )

    def test_happy_path_every_page_matches(self):
        school_id, class_id, prompt_id, students = self._seed(
            "1", ["Ana Lúcia Ferreira", "João da Silva"]
        )
        ScriptedTranscriber.script = {
            "p1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Texto da Ana."),
            "p2": ("NOME COMPLETO DO PARTICIPANTE Joao da Silva", "Texto do Joao."),
        }

        create = self._upload(prompt_id, class_id, ["p1", "p2"])
        self.assertEqual(create.status_code, 202, create.text)
        batch_id = create.json()["id"]
        self.assertEqual(create.json()["total_pages"], 2)
        self.assertEqual(create.json()["status"], "PROCESSING")

        # TestClient roda as BackgroundTasks antes de devolver a resposta, entao
        # nesse ponto o lote ja terminou de processar.
        status = self.client.get(f"/api/v1/teacher/essay-batches/{batch_id}")
        self.assertEqual(status.status_code, 200, status.text)
        body = status.json()
        self.assertEqual(body["status"], "DONE")
        self.assertEqual(body["matched_count"], 2)
        self.assertEqual(body["needs_review_count"], 0)
        self.assertEqual(body["needs_review_pages"], [])

        async def _submissions():
            async with self.factory() as session:
                # Filtrado por escola: a suite inteira compartilha o mesmo
                # banco em memoria (setUpClass cria o engine uma vez so), e
                # com o fallback do Aviso #1 agora lendo texto de verdade,
                # outros testes rodados antes deste (ex.: o lote de
                # test_a_batch_from_another_school_is_403) tambem materializam
                # submissoes reais - uma query sem filtro contaria as delas
                # tambem.
                return (
                    await session.execute(
                        select(EssaySubmission).where(
                            EssaySubmission.school_id == uuid.UUID(school_id)
                        )
                    )
                ).scalars().all()

        submissions = self.loop.run_until_complete(_submissions())
        self.assertEqual(len(submissions), 2)
        self.assertEqual({str(s.student_id) for s in submissions}, set(students.values()))

    def test_unmatched_page_lands_in_the_manual_queue_and_can_be_resolved(self):
        _school_id, class_id, prompt_id, students = self._seed("2", ["Ana Lúcia Ferreira"])
        ScriptedTranscriber.script = {
            "q1": ("NOME COMPLETO DO PARTICIPANTE Carlos Mendes", "Texto de alguem."),
        }

        create = self._upload(prompt_id, class_id, ["q1"])
        batch_id = create.json()["id"]

        status = self.client.get(f"/api/v1/teacher/essay-batches/{batch_id}").json()
        self.assertEqual(status["needs_review_count"], 1)
        page = status["needs_review_pages"][0]
        self.assertEqual(page["ocr_name_raw"], "CARLOS MENDES")
        self.assertTrue(page["has_text"])
        self.assertEqual(
            [s["student_id"] for s in status["available_students"]],
            [students["Ana Lúcia Ferreira"]],
        )

        resolve = self.client.post(
            f"/api/v1/teacher/essay-batches/{batch_id}/pages/{page['id']}/resolve",
            json={"student_id": students["Ana Lúcia Ferreira"]},
        )
        self.assertEqual(resolve.status_code, 200, resolve.text)
        self.assertEqual(resolve.json()["needs_review_count"], 0)
        self.assertEqual(resolve.json()["matched_count"], 1)

    def test_resolving_the_same_page_twice_is_422_on_the_second_call(self):
        """Desvio intencional do brief (Aviso #2 do controlador, achado na
        revisao da Tarefa 8): EssayBatchService.resolve_page nao valida que a
        pagina esta NEEDS_REVIEW antes de realocar - uma pagina ja resolvida
        (MATCHED_AUTO ou RESOLVED_MANUAL) poderia ser movida de novo em
        silencio, deixando a submissao antiga com uma pagina "presa". A rota
        checa isso ANTES de chamar o servico."""
        _school_id, class_id, prompt_id, students = self._seed("8", ["Ana Lúcia Ferreira"])
        ScriptedTranscriber.script = {
            "u1": ("NOME COMPLETO DO PARTICIPANTE Carlos Mendes", "Texto de alguem."),
        }

        create = self._upload(prompt_id, class_id, ["u1"])
        batch_id = create.json()["id"]

        status = self.client.get(f"/api/v1/teacher/essay-batches/{batch_id}").json()
        page = status["needs_review_pages"][0]

        first = self.client.post(
            f"/api/v1/teacher/essay-batches/{batch_id}/pages/{page['id']}/resolve",
            json={"student_id": students["Ana Lúcia Ferreira"]},
        )
        self.assertEqual(first.status_code, 200, first.text)

        second = self.client.post(
            f"/api/v1/teacher/essay-batches/{batch_id}/pages/{page['id']}/resolve",
            json={"student_id": students["Ana Lúcia Ferreira"]},
        )
        self.assertEqual(second.status_code, 422, second.text)
        self.assertIn("ja foi resolvida", second.json()["detail"])

    def test_batch_above_the_page_limit_is_422(self):
        _school_id, class_id, prompt_id, _students = self._seed("3", ["Ana Lúcia Ferreira"])
        ScriptedTranscriber.script = {}
        files = []
        for index in range(201):
            path = _write_stamped_page(self.tmp_dir / f"big{index}.png", f"big{index}")
            files.append(("files", (f"big{index}.png", path.read_bytes(), "image/png")))
        response = self.client.post(
            "/api/v1/teacher/essay-batches",
            data={"essay_prompt_id": prompt_id, "class_id": class_id}, files=files,
        )
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("200", response.json()["detail"])

    def test_unsupported_file_type_is_422(self):
        _school_id, class_id, prompt_id, _students = self._seed("4", ["Ana Lúcia Ferreira"])
        response = self.client.post(
            "/api/v1/teacher/essay-batches",
            data={"essay_prompt_id": prompt_id, "class_id": class_id},
            files=[("files", ("planilha.xlsx", b"x", "application/vnd.ms-excel"))],
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_a_batch_from_another_school_is_403(self):
        _school_a, class_a, prompt_a, _students_a = self._seed("5", ["Ana Lúcia Ferreira"])
        ScriptedTranscriber.script = {
            "r1": ("NOME COMPLETO DO PARTICIPANTE Ana Lucia Ferreira", "Texto."),
        }
        batch_id = self._upload(prompt_a, class_a, ["r1"]).json()["id"]

        async def _other_teacher():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code="BR-OTHER", name="outra")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="prof_outro", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                await session.commit()

        self.loop.run_until_complete(_other_teacher())
        self.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_outro")
        response = self.client.get(f"/api/v1/teacher/essay-batches/{batch_id}")
        self.assertEqual(response.status_code, 403, response.text)

    def test_page_image_is_served(self):
        _school_id, class_id, prompt_id, _students = self._seed("6", ["Ana Lúcia Ferreira"])
        ScriptedTranscriber.script = {"s1": ("NOME Desconhecido Ninguem", "Texto.")}
        batch_id = self._upload(prompt_id, class_id, ["s1"]).json()["id"]

        async def _page_id():
            async with self.factory() as session:
                page = (await session.execute(
                    select(EssayBatchPage).where(EssayBatchPage.batch_id == uuid.UUID(batch_id))
                )).scalars().first()
                return str(page.id)

        page_id = self.loop.run_until_complete(_page_id())
        response = self.client.get(
            f"/api/v1/teacher/essay-batches/{batch_id}/pages/{page_id}/image"
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.content)

    def _seed_school_with_grade_level_no_class_assignment(self) -> dict:
        """Escola com um GradeLevel real mas SEM nenhuma turma (nem
        PromptAssignment) atribuida a proposta - o escopo serie nao exige
        atribuicao antecipada (create_batch resolve isso por aluno, via
        _assignment_for_student, quando o lote roda)."""
        code = uuid.uuid4().hex[:8]

        async def _run():
            async with self.factory() as session:
                school = School(id=uuid.uuid4(), code=f"BR-GL-{code}", name=f"escola-gl-{code}")
                session.add(school)
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="prof_lote", school_id=school.id, role="TEACHER",
                    scope_type="SCHOOL", active=True,
                ))
                segment = Segment(
                    id=uuid.uuid4(), school_id=school.id, name="seg", external_id=f"SEG-{code}"
                )
                session.add(segment)
                await session.flush()
                grade = GradeLevel(
                    id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
                    name="3a", external_id=f"GRADE-{code}",
                )
                prompt = EssayPrompt(
                    id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte.",
                    year=2026, created_by_external_identity="prof_lote",
                )
                session.add_all([grade, prompt])
                await session.commit()
                return {
                    "school_id": str(school.id),
                    "grade_level_id": str(grade.id),
                    "prompt_id": str(prompt.id),
                    "class_id": None,
                }

        return self.loop.run_until_complete(_run())

    def _fake_png_bytes(self, key: str = "g1") -> bytes:
        path = _write_stamped_page(self.tmp_dir / f"{key}.png", key)
        return path.read_bytes()

    def test_post_lote_com_grade_level_id_em_vez_de_class_id(self):
        seed = self._seed_school_with_grade_level_no_class_assignment()
        response = self.client.post(
            "/api/v1/teacher/essay-batches",
            data={
                "essay_prompt_id": seed["prompt_id"],
                "grade_level_id": seed["grade_level_id"],
            },
            files={"files": ("folha.png", self._fake_png_bytes(), "image/png")},
        )
        self.assertEqual(response.status_code, 202, response.text)
        body = response.json()
        self.assertIsNone(body["class_id"])
        self.assertEqual(body["grade_level_id"], seed["grade_level_id"])

    def test_post_lote_com_class_id_e_grade_level_id_juntos_e_422(self):
        seed = self._seed_school_with_grade_level_no_class_assignment()
        response = self.client.post(
            "/api/v1/teacher/essay-batches",
            data={
                "essay_prompt_id": seed["prompt_id"],
                "class_id": seed.get("class_id") or seed["grade_level_id"],
                "grade_level_id": seed["grade_level_id"],
            },
            files={"files": ("folha.png", self._fake_png_bytes(), "image/png")},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_get_grade_levels_lista_as_series_da_escola(self):
        seed = self._seed_school_with_grade_level_no_class_assignment()
        response = self.client.get("/api/v1/teacher/essay-batches/grade-levels")
        self.assertEqual(response.status_code, 200, response.text)
        ids = {item["id"] for item in response.json()}
        self.assertIn(seed["grade_level_id"], ids)

    def test_resolving_a_page_with_no_text_is_422(self):
        _school_id, class_id, prompt_id, students = self._seed("7", ["Ana Lúcia Ferreira"])
        ScriptedTranscriber.script = {"t1": ("NOME Alguem Desconhecido", "   ")}
        batch_id = self._upload(prompt_id, class_id, ["t1"]).json()["id"]
        status = self.client.get(f"/api/v1/teacher/essay-batches/{batch_id}").json()
        page = status["needs_review_pages"][0]
        self.assertFalse(page["has_text"])

        response = self.client.post(
            f"/api/v1/teacher/essay-batches/{batch_id}/pages/{page['id']}/resolve",
            json={"student_id": students["Ana Lúcia Ferreira"]},
        )
        self.assertEqual(response.status_code, 422, response.text)

    def _seed_classroom_scoped_teacher(self, school_id: str) -> None:
        """Mesma escola de algum _seed/_seed_school_with_grade_level_no_class_assignment
        anterior, mas um SEGUNDO professor (prof_turma_unica) com
        scope_type=CLASSROOM - o caso que o gate da Fix 4 deve recusar pra
        serie/escola."""
        async def _run():
            async with self.factory() as session:
                session.add(UserSchoolLink(
                    external_user_id="prof_turma_unica", school_id=uuid.UUID(school_id),
                    role="TEACHER", scope_type="CLASSROOM", active=True,
                ))
                await session.commit()
        self.loop.run_until_complete(_run())

    def test_get_grade_levels_recusa_professor_de_turma_unica(self):
        seed = self._seed_school_with_grade_level_no_class_assignment()
        self._seed_classroom_scoped_teacher(seed["school_id"])
        self.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_turma_unica")
        response = self.client.get("/api/v1/teacher/essay-batches/grade-levels")
        self.assertEqual(response.status_code, 403, response.text)

    def test_post_lote_com_grade_level_id_recusa_professor_de_turma_unica(self):
        seed = self._seed_school_with_grade_level_no_class_assignment()
        self._seed_classroom_scoped_teacher(seed["school_id"])
        self.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_turma_unica")
        response = self.client.post(
            "/api/v1/teacher/essay-batches",
            data={"essay_prompt_id": seed["prompt_id"], "grade_level_id": seed["grade_level_id"]},
            files={"files": ("folha.png", self._fake_png_bytes(), "image/png")},
        )
        self.assertEqual(response.status_code, 403, response.text)


if __name__ == "__main__":
    unittest.main()
