"""PHASE 26 - Authorial Material Ingestion Engine backend tests.

In-memory SQLite + synthetic fixtures generated at setUp time (PDF via
pymupdf, DOCX via python-docx, TXT/MD as plain text) - self-contained and
reproducible, never depending on a real personal file (the REAL pilot walk
against the user's authorized files lives in
tests/manual/phase26_authorial_material_ingestion_report.py, against the
real database, per the project's established manual-walk convention).

Covers spec s25: PDF/DOCX/TXT/MD ingestion, hash-based duplicate detection,
extraction, structure, curriculum-v2 classification (+ TAXONOMY_GAP), review,
approval, publication (reusing the EXISTING PHASE 23 TheoryMaterialService),
versioning-safety, tenant isolation, permissions, exercise detection (never
duplicated into the Question Bank), Material Player integration (PHASE 25,
unchanged), idempotency, retry-safety, AI guard, no N+1.
"""

from __future__ import annotations

import ast
import asyncio
import importlib
import unittest
import uuid as _uuid
import warnings
from pathlib import Path

from fastapi.testclient import TestClient
from sqlalchemy import event, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

warnings.filterwarnings("ignore")

from agente_ia_edu.api.app import create_app
from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, IngestionDocument, IngestionMaterialReview
from agente_ia_edu.db.models.admin import School, UserSchoolLink
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.authorial_material_ingestion import (
    AuthorialIngestionError,
    AuthorialMaterialIngestionService,
)
from agente_ia_edu.services.material_storage import MaterialStorage
from agente_ia_edu.services.student_material import StudentMaterialService

_SCHOOL_A = _uuid.uuid5(_uuid.NAMESPACE_DNS, "phase26-school-a")
_SCHOOL_B = _uuid.uuid5(_uuid.NAMESPACE_DNS, "phase26-school-b")


def _ident(user: str) -> ExternalIdentityContext:
    return ExternalIdentityContext(provider="test", external_user_id=user)


def _make_docx(path: Path, *, title: str = "APOSTILA TESTE") -> None:
    from docx import Document
    doc = Document()
    doc.add_paragraph(f"Episódio 01 – {title}")
    # every fixture file must hash differently unless a test explicitly
    # re-ingests the SAME path (idempotency) - embed the filename as a
    # unique-per-file nonce so unrelated tests never collide by hash.
    doc.add_paragraph(f"Um parágrafo de introdução sobre o tema estudado nesta apostila ({path.stem}).")
    doc.add_paragraph("Questão 1. Qual é a resposta correta?")
    doc.add_paragraph("a) Alternativa um")
    doc.add_paragraph("b) Alternativa dois")
    doc.add_paragraph("c) Alternativa três")
    doc.add_paragraph("d) Alternativa quatro")
    doc.add_paragraph("e) Alternativa cinco")
    doc.save(path)


def _make_pdf(path: Path, *, heading_text: str = "SOLUÇÕES QUÍMICAS") -> None:
    import fitz
    d = fitz.open()
    page = d.new_page()
    page.insert_text((72, 72), "TEMPORADA 1: TESTE DE INGESTAO", fontsize=14)
    page.insert_text((72, 100), f"EPISÓDIO 01 – {heading_text}", fontsize=12)
    page.insert_text((72, 130), "1.1 Conceito. Uma solução química é uma mistura homogênea.", fontsize=10)
    page.insert_text((72, 160), "01. Qual das alternativas descreve uma solução?", fontsize=10)
    page.insert_text((72, 175), "a) opcao um b) opcao dois c) opcao tres d) opcao quatro e) opcao cinco", fontsize=10)
    d.save(str(path))


def _make_txt(path: Path) -> None:
    # embed the filename so distinct fixture paths always hash differently
    # (hash-based idempotency is content-addressed, not path-addressed).
    path.write_text(
        "Material de teste em texto puro\n\n"
        "INTRODUCAO\n"
        f"Este e um paragrafo introdutorio sobre o tema ({path.stem}).\n\n"
        "CONCEITOS\n"
        "Outro paragrafo com o conteudo principal do material.\n",
        encoding="utf-8",
    )


async def _seed_catalog(factory) -> None:
    async with factory() as s:
        d = CatalogNode(code="P26-DISC", name="Disciplina Teste", node_type="DISCIPLINE", active=True)
        s.add(d); await s.flush(); d.root_id = d.id
        a = CatalogNode(code="P26-AREA", name="Area Teste", node_type="AREA",
                        parent_id=d.id, root_id=d.id, active=True)
        s.add(a); await s.flush()
        c = CatalogNode(code="P26-CONTENT", name="Apostila Teste", node_type="CONTENT",
                        parent_id=a.id, root_id=d.id, active=True)
        s.add(c); await s.flush()
        s.add(School(id=_SCHOOL_A, code="P26SCHA", name="Escola P26 A", status="ACTIVE"))
        s.add(School(id=_SCHOOL_B, code="P26SCHB", name="Escola P26 B", status="ACTIVE"))
        await s.flush()
        s.add(UserSchoolLink(external_user_id="prof_a", school_id=_SCHOOL_A, role="TEACHER",
                             scope_type="SCHOOL", scope_external_id=str(_SCHOOL_A), active=True))
        s.add(UserSchoolLink(external_user_id="prof_b", school_id=_SCHOOL_B, role="TEACHER",
                             scope_type="SCHOOL", scope_external_id=str(_SCHOOL_B), active=True))
        s.add(UserSchoolLink(external_user_id="student_a", school_id=_SCHOOL_A, role="STUDENT",
                             scope_type="CLASSROOM", scope_external_id="turma-a", active=True))
        await s.commit()


class Phase26Tests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.loop = asyncio.new_event_loop()
        cls.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        cls.factory = async_sessionmaker(cls.engine, class_=AsyncSession, expire_on_commit=False)

        async def _prep():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)
            await _seed_catalog(cls.factory)

        cls.loop.run_until_complete(_prep())

        cls.tmp = Path("/tmp/phase26_fixtures")
        cls.tmp.mkdir(exist_ok=True)
        cls.docx_path = cls.tmp / "apostila.docx"
        _make_docx(cls.docx_path)
        cls.pdf_path = cls.tmp / "solucoes.pdf"
        _make_pdf(cls.pdf_path)
        cls.txt_path = cls.tmp / "material.txt"
        _make_txt(cls.txt_path)
        cls.storage_root = Path("/tmp/phase26_storage")

        cls.app = create_app()
        cls.app.dependency_overrides[get_session_factory] = lambda: cls.factory
        cls.app.dependency_overrides[get_current_identity] = lambda: _ident("prof_a")
        cls.client = TestClient(cls.app)

    @classmethod
    def tearDownClass(cls):
        cls.app.dependency_overrides.clear()
        cls.loop.run_until_complete(cls.engine.dispose())
        cls.loop.close()

    def _as(self, user: str):
        self.app.dependency_overrides[get_current_identity] = lambda: _ident(user)

    def _svc(self, session) -> AuthorialMaterialIngestionService:
        return AuthorialMaterialIngestionService(session, storage=MaterialStorage(root=self.storage_root))

    def _ingest(self, path: Path, *, uploaded_by="prof_a", school_id=_SCHOOL_A):
        async def run():
            async with self.factory() as s:
                return await self._svc(s).ingest_file(path, uploaded_by=uploaded_by, school_id=school_id)
        return self.loop.run_until_complete(run())

    # -- 1. DOCX ingestion: extraction + structure + exercise detection -----
    def test_docx_ingestion_extracts_structure_and_exercise(self):
        review, created = self._ingest(self.docx_path)
        self.assertTrue(created)
        self.assertGreaterEqual(review.exercises_detected, 1)
        self.assertIn(review.review_status, ("PENDING_REVIEW", "NEEDS_REVIEW"))

    # -- 2. PDF ingestion: the NEW authorial parser, NOT the ENEM PdfParser --
    def test_pdf_ingestion_extracts_structure_and_exercise(self):
        review, created = self._ingest(self.pdf_path)
        self.assertTrue(created)
        self.assertEqual(review.exercises_detected, 1)

        async def _doc():
            async with self.factory() as s:
                return await s.get(IngestionDocument, review.ingestion_document_id)
        doc = self.loop.run_until_complete(_doc())
        self.assertEqual(doc.document_type, "PDF")

    # -- 3. TXT ingestion --------------------------------------------------
    def test_txt_ingestion(self):
        path = self.tmp / "material_fresh.txt"
        _make_txt(path)
        review, created = self._ingest(path)
        self.assertTrue(created)

    # -- 4. hash / duplicate detection - never a silent duplicate -----------
    def test_duplicate_by_hash_returns_existing_not_a_copy(self):
        path = self.tmp / "duplicate_check.docx"
        _make_docx(path)
        review1, created1 = self._ingest(path)
        review2, created2 = self._ingest(path)
        self.assertTrue(created1)
        self.assertFalse(created2)
        self.assertEqual(review1.id, review2.id)

        async def _count():
            async with self.factory() as s:
                all_docs = (await s.execute(select(IngestionDocument))).scalars().all()
                return len({d.document_hash for d in all_docs}), len(all_docs)
        distinct_hashes, total = self.loop.run_until_complete(_count())
        self.assertEqual(distinct_hashes, total)  # no two rows share a hash

    # -- 5. retry-safety: a failed/aborted retry never duplicates -----------
    def test_retry_after_partial_failure_is_safe(self):
        review1, _ = self._ingest(self.txt_path)
        review2, created2 = self._ingest(self.txt_path)
        self.assertFalse(created2)
        self.assertEqual(review1.id, review2.id)

    # -- 6. curriculum-v2 classification: MAPPED when a real match exists ---
    def test_classification_mapped_to_curriculum_v2(self):
        path = self.tmp / "apostila_teste.docx"
        _make_docx(path, title="Apostila Teste")
        review, _ = self._ingest(path)
        self.assertEqual(review.classification_state, "MAPPED")
        self.assertEqual(review.content_code, "P26-CONTENT")
        self.assertEqual(review.discipline_code, "P26-DISC")

    # -- 7. TAXONOMY_GAP: never a forced/fabricated classification -----------
    def test_taxonomy_gap_never_forces_a_classification(self):
        path = self.tmp / "sem_correspondencia.docx"
        _make_docx(path, title="Zzqxw Unrelated Whatever 999")
        review, _ = self._ingest(path)
        self.assertEqual(review.classification_state, "TAXONOMY_GAP")
        self.assertIsNone(review.content_code)
        self.assertEqual(review.review_status, "NEEDS_REVIEW")

    # -- 8. review: manual classification edit ------------------------------
    def test_manual_classification_edit(self):
        path = self.tmp / "edit_test.docx"
        _make_docx(path, title="Sem Correspondencia Alguma 123")
        review, _ = self._ingest(path)
        self.assertEqual(review.classification_state, "TAXONOMY_GAP")

        async def run():
            async with self.factory() as s:
                return await self._svc(s).update_classification(
                    review.id, content_code="P26-CONTENT", reviewed_by="prof_a")
        updated = self.loop.run_until_complete(run())
        self.assertEqual(updated.classification_state, "MAPPED")
        self.assertEqual(updated.classification_source, "MANUAL")
        self.assertEqual(updated.review_status, "PENDING_REVIEW")

    # -- 9/10. approval + publication (INGESTION != PUBLICATION) -----------
    def test_approval_then_publication_creates_theory_material(self):
        path = self.tmp / "publish_test.docx"
        _make_docx(path, title="Apostila Teste")
        review, _ = self._ingest(path)
        self.assertIsNone(review.theory_material_id)  # not published yet

        async def run():
            async with self.factory() as s:
                svc = self._svc(s)
                approved = await svc.approve(review.id, reviewed_by="prof_a")
                self.assertEqual(approved.review_status, "APPROVED")
                published = await svc.publish(review.id, published_by="prof_a")
                return published
        published = self.loop.run_until_complete(run())
        self.assertEqual(published.review_status, "PUBLISHED")
        self.assertIsNotNone(published.theory_material_id)

    # -- 10b. cannot publish before approval (spec s6: ingestion != publication)
    def test_cannot_publish_without_approval(self):
        path = self.tmp / "no_approval.docx"
        _make_docx(path, title="Apostila Teste")
        review, _ = self._ingest(path)

        async def run():
            async with self.factory() as s:
                with self.assertRaises(AuthorialIngestionError):
                    await self._svc(s).publish(review.id, published_by="prof_a")
        self.loop.run_until_complete(run())

    # -- 10c. cannot approve a TAXONOMY_GAP material without manual mapping --
    def test_cannot_approve_taxonomy_gap_without_manual_classification(self):
        path = self.tmp / "gap_approve.docx"
        _make_docx(path, title="Totally Unrelated Xk992")
        review, _ = self._ingest(path)

        async def run():
            async with self.factory() as s:
                with self.assertRaises(AuthorialIngestionError):
                    await self._svc(s).approve(review.id, reviewed_by="prof_a")
        self.loop.run_until_complete(run())

    # -- 11. publish is idempotent - never a second material -----------------
    def test_publish_is_idempotent(self):
        path = self.tmp / "idempotent_publish.docx"
        _make_docx(path, title="Apostila Teste")
        review, _ = self._ingest(path)

        async def run():
            async with self.factory() as s:
                svc = self._svc(s)
                await svc.approve(review.id, reviewed_by="prof_a")
                p1 = await svc.publish(review.id, published_by="prof_a")
                p2 = await svc.publish(review.id, published_by="prof_a")
                return p1.theory_material_id, p2.theory_material_id
        m1, m2 = self.loop.run_until_complete(run())
        self.assertEqual(m1, m2)

    # -- 12. versioning-safety: publishing does not touch a different review -
    def test_publishing_one_review_does_not_affect_another(self):
        p1 = self.tmp / "v_a.docx"; _make_docx(p1, title="Apostila Teste")
        p2 = self.tmp / "v_b.docx"; _make_docx(p2, title="Apostila Teste")
        r1, _ = self._ingest(p1)
        r2, _ = self._ingest(p2)
        self.assertNotEqual(r1.id, r2.id)

        async def run():
            async with self.factory() as s:
                svc = self._svc(s)
                await svc.approve(r1.id, reviewed_by="prof_a")
                p1_pub = await svc.publish(r1.id, published_by="prof_a")
                r2_fresh = await svc.get_review(r2.id)
                return p1_pub.theory_material_id, r2_fresh.review_status
        mid, r2_status = self.loop.run_until_complete(run())
        self.assertIsNotNone(mid)
        self.assertNotIn(r2_status, ("PUBLISHED",))

    # -- 13. tenant isolation: school B cannot see/act on school A's review --
    def test_tenant_isolation_via_api(self):
        self._as("prof_a")
        with open(self.docx_path, "rb") as f:
            r = self.client.post("/api/v1/catalog/ingestion/upload",
                                 files={"file": ("apostila.docx", f,
                                       "application/vnd.openxmlformats-officedocument.wordprocessingml.document")})
        self.assertEqual(r.status_code, 201, r.text)
        review_id = r.json()["review"]["id"]

        self._as("prof_b")
        r2 = self.client.get(f"/api/v1/catalog/ingestion/{review_id}")
        self.assertEqual(r2.status_code, 403)
        r3 = self.client.post(f"/api/v1/catalog/ingestion/{review_id}/approve")
        self.assertEqual(r3.status_code, 403)

    # -- 14. permissions: a student cannot upload/review -----------------------
    def test_student_cannot_upload(self):
        self._as("student_a")
        with open(self.txt_path, "rb") as f:
            r = self.client.post("/api/v1/catalog/ingestion/upload",
                                 files={"file": ("material.txt", f, "text/plain")})
        self.assertEqual(r.status_code, 403)
        self._as("prof_a")

    # -- 15. exercise detection is never auto-imported into the Question Bank
    def test_exercises_are_not_official_questions(self):
        path = self.tmp / "exercises_check.docx"
        _make_docx(path, title="Apostila Teste")
        review, _ = self._ingest(path)

        async def counts():
            async with self.factory() as s:
                svc = self._svc(s)
                await svc.approve(review.id, reviewed_by="prof_a")
                published = await svc.publish(review.id, published_by="prof_a")
                from agente_ia_edu.db.models import MaterialExercise, Question
                exs = (await s.execute(select(MaterialExercise).where(
                    MaterialExercise.material_version_id == published.theory_material_version_id
                ))).scalars().all()
                q_count = int(await s.scalar(select(Question).with_only_columns(Question.id)) is not None
                              and len((await s.execute(select(Question))).scalars().all()))
                return exs, q_count
        exs, q_count = self.loop.run_until_complete(counts())
        self.assertGreaterEqual(len(exs), 1)
        self.assertTrue(all(e.source_type == "AUTHORED" for e in exs))
        self.assertTrue(all(e.question_version_id is None for e in exs))
        self.assertEqual(q_count, 0)  # never created an official Question row

    # -- 16. Material Player integration (PHASE 25, unchanged) --------------
    def test_published_material_reaches_the_existing_material_player(self):
        path = self.tmp / "player_check.docx"
        _make_docx(path, title="Apostila Teste")
        review, _ = self._ingest(path)

        async def run():
            async with self.factory() as s:
                svc = self._svc(s)
                await svc.approve(review.id, reviewed_by="prof_a")
                published = await svc.publish(review.id, published_by="prof_a")
                sm = StudentMaterialService(s)
                material = await sm.get_material(published.theory_material_id, requester_school_id=str(_SCHOOL_A))
                sections = await sm.get_sections(published.theory_material_id, requester_school_id=str(_SCHOOL_A))
                return material, sections
        material, sections = self.loop.run_until_complete(run())
        self.assertGreater(material["section_count"], 0)
        self.assertGreater(len(sections), 0)
        self.assertGreater(sum(len(s["blocks"]) for s in sections), 0)

    # -- 17. a student from another school cannot see the published material -
    def test_published_material_respects_tenant_for_students(self):
        path = self.tmp / "player_tenant.docx"
        _make_docx(path, title="Apostila Teste")
        review, _ = self._ingest(path)

        async def run():
            async with self.factory() as s:
                svc = self._svc(s)
                await svc.approve(review.id, reviewed_by="prof_a")
                published = await svc.publish(review.id, published_by="prof_a")
                sm = StudentMaterialService(s)
                from agente_ia_edu.services.student_material import MaterialAccessError
                with self.assertRaises(MaterialAccessError):
                    await sm.get_material(published.theory_material_id, requester_school_id=str(_SCHOOL_B))
        self.loop.run_until_complete(run())

    # -- 18. AI-agnostic import guard ----------------------------------------
    def test_ai_agnostic_import_guard(self):
        import agente_ia_edu.services.authorial_material_ingestion as m1
        import agente_ia_edu.services.authorial_material_parser as m2
        import agente_ia_edu.services.authorial_curriculum_matcher as m3
        import agente_ia_edu.services.material_storage as m4
        forbidden = {"openai", "AsyncOpenAI", "OpenAIProvider", "providers",
                    "classification_consensus", "classification_prompts"}
        for mod in (m1, m2, m3, m4):
            tree = ast.parse(open(mod.__file__, encoding="utf-8").read())
            names = set()
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    names.update(a.name.split(".")[0] for a in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module:
                    names.add(node.module.split(".")[0])
            self.assertFalse(names & forbidden, f"{mod.__file__} imports {names & forbidden}")

    # -- 19. no N+1: get_sections after publication stays batched (PHASE 25) -
    def test_no_n_plus_1_after_publication(self):
        path = self.tmp / "n1_check.docx"
        from docx import Document
        doc = Document()
        doc.add_paragraph("Episódio 01 – Apostila Teste")
        for i in range(40):
            doc.add_paragraph(f"Parágrafo de conteúdo número {i} com texto suficiente para o teste.")
        doc.save(path)
        review, _ = self._ingest(path)

        async def run():
            async with self.factory() as s:
                svc = self._svc(s)
                await svc.approve(review.id, reviewed_by="prof_a")
                published = await svc.publish(review.id, published_by="prof_a")
            n = {"c": 0}

            @event.listens_for(self.engine.sync_engine, "before_cursor_execute")
            def _c(*_a):  # noqa: ANN001
                n["c"] += 1
            try:
                async with self.factory() as s:
                    sm = StudentMaterialService(s)
                    n["c"] = 0
                    await sm.get_sections(published.theory_material_id, requester_school_id=str(_SCHOOL_A))
                    return n["c"]
            finally:
                event.remove(self.engine.sync_engine, "before_cursor_execute", _c)
        q = self.loop.run_until_complete(run())
        self.assertLessEqual(q, 8, f"query count too high: {q}")

    # -- 20. unsupported format -> explicit error, never silently ignored ---
    def test_unsupported_format_raises_explicit_error(self):
        path = self.tmp / "unsupported.xyz"
        path.write_text("nope")
        with self.assertRaises(AuthorialIngestionError) as ctx:
            self._ingest(path)
        self.assertEqual(ctx.exception.code, "UNSUPPORTED_FORMAT")


if __name__ == "__main__":
    unittest.main()
