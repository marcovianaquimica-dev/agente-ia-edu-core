"""CEREBRO / Knowledge Engine - Fase 3: a camada HTTP de extracao.

O criterio mais importante aqui e o 9: para fonte COMMERCIAL_REFERENCE,
NENHUMA resposta de chunk pode trazer texto literal, em nenhum tipo de chunk.

Sobre autenticacao, vale a mesma ressalva da Fase 2: este projeto nao tem
autenticacao real, entao os testes de 403 verificam AUTORIZACAO, nao
autenticacao de producao.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
import uuid
from pathlib import Path

import pymupdf
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.knowledge_engine import knowledge_engine_router
from agente_ia_edu.api.schemas import knowledge_engine as knowledge_schemas
from agente_ia_edu.db.base import Base
from agente_ia_edu.identity import ExternalIdentityContext

_PROSE = "O mol participa da reacao em proporcao definida pela equacao. " * 30
_RICH_PAGE = (
    "Capítulo 10 - Estequiometria\n\n"
    + _PROSE
    + "\n\nExemplo resolvido\nCalcule a massa de CO2 produzida na combustao.\n"
    + "\n\nN2 + 3H2 → 2NH3\n"
    + "\n\nChama-se reagente limitante aquele que se esgota primeiro na reacao.\n"
    + "\n\n1. Calcule a massa molar do dioxido de carbono, sabendo que as massas "
      "atomicas sao C igual a 12 e O igual a 16.\n"
      "a) 44 g\nb) 32 g\nc) 28 g\nd) 18 g\ne) 12 g\n"
)


class KnowledgeExtractionHTTP(unittest.TestCase):
    def setUp(self):
        async def build():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=True)

        self.engine, self.factory = asyncio.run(build())
        self.identity = {
            "value": ExternalIdentityContext(
                provider="test", external_user_id="ADMIN", roles=("PLATFORM_ADMIN",)
            )
        }
        app = FastAPI()
        app.include_router(knowledge_engine_router)
        app.dependency_overrides[get_session_factory] = lambda: self.factory
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        self.client = TestClient(app)

        self._tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self._tmp.cleanup)
        self.root = Path(self._tmp.name).resolve()
        self._saved = os.environ.get("KNOWLEDGE_ENGINE_DOCUMENT_ROOT")
        os.environ["KNOWLEDGE_ENGINE_DOCUMENT_ROOT"] = str(self.root)
        self.addCleanup(self._restore)

    def _restore(self):
        if self._saved is None:
            os.environ.pop("KNOWLEDGE_ENGINE_DOCUMENT_ROOT", None)
        else:
            os.environ["KNOWLEDGE_ENGINE_DOCUMENT_ROOT"] = self._saved

    def tearDown(self):
        asyncio.run(self.engine.dispose())

    def _pdf(self, name: str, pages: list[str]) -> str:
        path = self.root / name
        document = pymupdf.open()
        for text in pages:
            page = document.new_page()
            if text:
                page.insert_text((40, 60), text, fontsize=8)
        document.save(str(path))
        document.close()
        return name

    def _prepare(self, *, source_kind="TEXTBOOK", rights="COMMERCIAL_REFERENCE",
                 authority="COMMERCIAL_TEXTBOOK", pages=None, name="livro.pdf"):
        source = self.client.post(
            "/api/v1/knowledge-engine/sources",
            json={"title": "Fonte", "source_kind": source_kind,
                  "rights_class": rights, "authority_level": authority},
        ).json()
        local = self._pdf(name, pages or [_RICH_PAGE])
        document = self.client.post(
            f"/api/v1/knowledge-engine/sources/{source['id']}/documents",
            json={"local_path": local},
        ).json()
        return source["id"], document["document"]["id"]

    # -- extracao --------------------------------------------------------

    def test_extracting_a_document_returns_the_outcome(self):
        source_id, document_id = self._prepare()
        response = self.client.post(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents/{document_id}/extract"
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["extraction_status"], "EXTRACTED")
        self.assertEqual(body["extraction_method"], "PDF_TEXT_LAYER")
        self.assertGreater(body["chunks_created"], 0)
        self.assertEqual(body["page_count"], 1)
        self.assertIn("pages_without_text", body)
        self.assertGreaterEqual(body["duration_seconds"], 0)

    def test_a_second_extraction_without_force_is_409(self):
        source_id, document_id = self._prepare()
        url = f"/api/v1/knowledge-engine/sources/{source_id}/documents/{document_id}/extract"
        self.client.post(url)
        again = self.client.post(url)
        self.assertEqual(again.status_code, 409)
        self.assertIn("ALREADY_CHUNKED", again.json()["detail"])

    def test_force_rechunks(self):
        source_id, document_id = self._prepare()
        url = f"/api/v1/knowledge-engine/sources/{source_id}/documents/{document_id}/extract"
        first = self.client.post(url).json()
        forced = self.client.post(url, params={"force": True}).json()
        self.assertEqual(first["chunks_created"], forced["chunks_created"])

    def test_a_curriculum_framework_source_is_422(self):
        source_id, document_id = self._prepare(
            source_kind="CURRICULUM_FRAMEWORK", rights="OFFICIAL_PUBLIC",
            authority="OFFICIAL", name="bncc.pdf",
        )
        response = self.client.post(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents/{document_id}/extract"
        )
        self.assertEqual(response.status_code, 422)
        self.assertIn("UNSUPPORTED_SOURCE_KIND_FOR_PHASE", response.json()["detail"])

    def test_an_image_only_pdf_is_a_200_with_a_failed_outcome_not_an_http_error(self):
        """Nao e falha da requisicao: e um resultado, e um estado
        consultavel."""
        source_id, document_id = self._prepare(pages=["", ""], name="imagem.pdf")
        response = self.client.post(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents/{document_id}/extract"
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["extraction_status"], "FAILED")
        self.assertTrue(body["extraction_error"].startswith("OCR_REQUIRED"))
        self.assertEqual(body["chunks_created"], 0)

    def test_an_unknown_document_is_404(self):
        source_id, _ = self._prepare()
        response = self.client.post(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents/{uuid.uuid4()}/extract"
        )
        self.assertEqual(response.status_code, 404)

    # -- leitura de chunks e direitos -----------------------------------

    def test_a_commercial_source_never_exposes_literal_text_in_any_chunk_type(self):
        """Critério de aceite 9 - o mais importante desta fase."""
        source_id, document_id = self._prepare(rights="COMMERCIAL_REFERENCE")
        self.client.post(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents/{document_id}/extract"
        )
        chunks = self.client.get(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents/{document_id}/chunks",
            params={"limit": 200},
        ).json()
        self.assertTrue(chunks)
        seen_types = set()
        for chunk in chunks:
            seen_types.add(chunk["chunk_type"])
            self.assertIsNone(chunk["excerpt"], chunk["chunk_type"])
            self.assertNotIn("raw_text", chunk)
        # Varreu mais de um tipo, entao a garantia nao vale so para PROSE.
        self.assertGreater(len(seen_types), 1, seen_types)

    def test_a_non_commercial_source_may_show_a_bounded_excerpt(self):
        from agente_ia_edu.services.knowledge_engine.rights import (
            NON_COMMERCIAL_EXCERPT_LIMIT,
        )

        source_id, document_id = self._prepare(
            rights="OWN", authority="OWN", name="propria.pdf"
        )
        self.client.post(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents/{document_id}/extract"
        )
        chunks = self.client.get(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents/{document_id}/chunks"
        ).json()
        with_excerpt = [c for c in chunks if c["excerpt"]]
        self.assertTrue(with_excerpt)
        for chunk in with_excerpt:
            self.assertLessEqual(len(chunk["excerpt"]), NON_COMMERCIAL_EXCERPT_LIMIT)

    def test_chunk_metadata_is_exposed_for_inspection(self):
        source_id, document_id = self._prepare()
        self.client.post(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents/{document_id}/extract"
        )
        chunk = self.client.get(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents/{document_id}/chunks"
        ).json()[0]
        self.assertTrue(chunk["heading_path"])
        self.assertIsNotNone(chunk["page_start"])
        self.assertEqual(len(chunk["text_hash"]), 64)
        self.assertIn("structure", chunk["metadata"])
        self.assertIn("chunking_policy_version", chunk["metadata"])

    def test_stats_are_reported(self):
        source_id, document_id = self._prepare()
        self.client.post(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents/{document_id}/extract"
        )
        stats = self.client.get(
            f"/api/v1/knowledge-engine/sources/{source_id}/chunks/stats"
        ).json()
        self.assertGreater(stats["total_chunks"], 0)
        self.assertEqual(stats["chunks_without_page"], 0)
        self.assertIn("PROSE", stats["by_chunk_type"])

    # -- autorizacao ------------------------------------------------------

    def test_a_non_admin_is_refused_on_the_new_endpoints(self):
        source_id, document_id = self._prepare()
        self.identity["value"] = ExternalIdentityContext(
            provider="test", external_user_id="prof", roles=("teacher",)
        )
        base = f"/api/v1/knowledge-engine/sources/{source_id}"
        for response in (
            self.client.post(f"{base}/documents/{document_id}/extract"),
            self.client.get(f"{base}/documents/{document_id}/chunks"),
            self.client.get(f"{base}/chunks/stats"),
        ):
            self.assertEqual(response.status_code, 403, response.request.url)


class SchemaSurfaceTests(unittest.TestCase):
    def test_no_response_schema_declares_raw_text(self):
        for name in dir(knowledge_schemas):
            candidate = getattr(knowledge_schemas, name)
            fields = getattr(candidate, "model_fields", None)
            if isinstance(fields, dict) and name.endswith("Response"):
                self.assertNotIn("raw_text", fields, name)
                self.assertNotIn("storage_uri", fields, name)

    def test_the_chunk_schema_has_excerpt_but_not_raw_text(self):
        fields = knowledge_schemas.KnowledgeChunkResponse.model_fields
        self.assertIn("excerpt", fields)
        self.assertNotIn("raw_text", fields)


if __name__ == "__main__":
    unittest.main()
