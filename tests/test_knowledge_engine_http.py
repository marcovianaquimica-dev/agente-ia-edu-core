"""CEREBRO / Knowledge Engine - Fase 2: a camada HTTP.

Passa pelo caminho HTTP de verdade (``TestClient``), nao chama a funcao da
rota diretamente: o que se quer cobrir aqui e justamente o que chamar a
funcao pula - injecao de dependencia, mapeamento de excecao para status, e
serializacao pelo response_model.

NOTA SOBRE AUTENTICACAO. Este projeto nao tem autenticacao real
(``TestExternalIdentityProvider`` confia no header, ver secao 2 da
documentacao da plataforma). Os testes de 403 aqui verificam AUTORIZACAO -
que a rota exige PLATFORM_ADMIN - e nao autenticacao, que nao existe ainda.
Dizer isso em voz alta importa: um 403 verde aqui nao e evidencia de que a
superficie esta segura em producao.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
import uuid
from pathlib import Path

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.knowledge_engine import knowledge_engine_router
from agente_ia_edu.api.schemas import knowledge_engine as knowledge_schemas
from agente_ia_edu.db.base import Base
from agente_ia_edu.identity import ExternalIdentityContext

COMMERCIAL = {
    "title": "Quimica na abordagem do cotidiano",
    "source_kind": "TEXTBOOK",
    "rights_class": "COMMERCIAL_REFERENCE",
    "authority_level": "COMMERCIAL_TEXTBOOK",
}
BNCC = {
    "title": "BNCC Ensino Medio",
    "source_kind": "CURRICULUM_FRAMEWORK",
    "rights_class": "OFFICIAL_PUBLIC",
    "authority_level": "OFFICIAL",
}


class KnowledgeEngineHTTP(unittest.TestCase):
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
        self._saved_root = os.environ.get("KNOWLEDGE_ENGINE_DOCUMENT_ROOT")
        os.environ["KNOWLEDGE_ENGINE_DOCUMENT_ROOT"] = str(self.root)
        self.addCleanup(self._restore_root)

    def _restore_root(self):
        if self._saved_root is None:
            os.environ.pop("KNOWLEDGE_ENGINE_DOCUMENT_ROOT", None)
        else:
            os.environ["KNOWLEDGE_ENGINE_DOCUMENT_ROOT"] = self._saved_root

    def tearDown(self):
        asyncio.run(self.engine.dispose())

    def _file(self, name: str, content: bytes = b"%PDF-1.7 conteudo") -> Path:
        path = self.root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(content)
        return path

    # -- cadastro --------------------------------------------------------

    def test_registering_a_source_returns_201_and_the_snapshot(self):
        response = self.client.post("/api/v1/knowledge-engine/sources", json=COMMERCIAL)
        self.assertEqual(response.status_code, 201)
        body = response.json()
        self.assertEqual(body["status"], "REGISTERED")
        self.assertEqual(body["rights_class"], "COMMERCIAL_REFERENCE")
        self.assertEqual(body["document_count"], 0)
        self.assertEqual(body["created_by_external_identity"], "ADMIN")

    def test_a_commercial_source_with_a_resource_is_409_with_the_reason(self):
        """Critério de aceite 1: 409 nomeando a regra, nunca um 500."""
        payload = dict(COMMERCIAL, educational_resource_id=str(uuid.uuid4()))
        response = self.client.post("/api/v1/knowledge-engine/sources", json=payload)
        self.assertEqual(response.status_code, 409)
        detail = response.json()["detail"]
        self.assertIn("COMMERCIAL_REFERENCE", detail)
        self.assertIn("educational_resource_id", detail)

    def test_an_official_source_is_201(self):
        """Critério de aceite 2."""
        self.assertEqual(
            self.client.post("/api/v1/knowledge-engine/sources", json=BNCC).status_code, 201
        )

    def test_an_unknown_rights_class_is_refused_by_the_schema_layer(self):
        payload = dict(COMMERCIAL, rights_class="FAIR_USE_MAYBE")
        response = self.client.post("/api/v1/knowledge-engine/sources", json=payload)
        self.assertEqual(response.status_code, 409)

    # -- documentos ------------------------------------------------------

    def _register_source(self, payload=None) -> str:
        response = self.client.post(
            "/api/v1/knowledge-engine/sources", json=payload or COMMERCIAL
        )
        return response.json()["id"]

    def test_registering_a_document_returns_the_registration(self):
        source_id = self._register_source()
        self._file("livros/volume1.pdf")
        response = self.client.post(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents",
            json={"local_path": "livros/volume1.pdf"},
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertTrue(body["created"])
        self.assertEqual(body["document"]["filename"], "volume1.pdf")
        self.assertEqual(body["document"]["ingress"], "LOCAL_PATH")

    def test_the_same_file_twice_is_idempotent_over_http(self):
        """Critério de aceite 3."""
        source_id = self._register_source()
        self._file("volume1.pdf")
        url = f"/api/v1/knowledge-engine/sources/{source_id}/documents"
        first = self.client.post(url, json={"local_path": "volume1.pdf"})
        second = self.client.post(url, json={"local_path": "volume1.pdf"})
        self.assertTrue(first.json()["created"])
        self.assertFalse(second.json()["created"])
        self.assertEqual(first.json()["document"]["id"], second.json()["document"]["id"])

        listing = self.client.get(f"/api/v1/knowledge-engine/sources/{source_id}/documents")
        self.assertEqual(len(listing.json()), 1)

    def test_a_document_stays_pending_with_no_page_count(self):
        """Critério de aceite 7."""
        source_id = self._register_source()
        self._file("v.pdf")
        body = self.client.post(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents",
            json={"local_path": "v.pdf"},
        ).json()
        self.assertEqual(body["document"]["extraction_status"], "PENDING")
        self.assertIsNone(body["document"]["page_count"])
        self.assertIsNone(body["document"]["extraction_method"])

    def test_the_response_never_leaks_the_server_filesystem_path(self):
        source_id = self._register_source()
        self._file("v.pdf")
        body = self.client.post(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents",
            json={"local_path": "v.pdf"},
        ).json()
        self.assertNotIn("storage_uri", body["document"])

    def test_a_path_escaping_the_root_is_refused(self):
        source_id = self._register_source()
        response = self.client.post(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents",
            json={"local_path": "../../etc/passwd.pdf"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("PATH_OUTSIDE_ROOT", response.json()["detail"])

    def test_a_missing_file_is_404(self):
        source_id = self._register_source()
        response = self.client.post(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents",
            json={"local_path": "nao-existe.pdf"},
        )
        self.assertEqual(response.status_code, 404)
        self.assertIn("FILE_NOT_FOUND", response.json()["detail"])

    def test_an_unsupported_extension_is_400(self):
        source_id = self._register_source()
        self._file("planilha.xlsx")
        response = self.client.post(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents",
            json={"local_path": "planilha.xlsx"},
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("UNSUPPORTED_FILE_TYPE", response.json()["detail"])

    def test_a_document_for_an_unknown_source_is_404(self):
        self._file("v.pdf")
        response = self.client.post(
            f"/api/v1/knowledge-engine/sources/{uuid.uuid4()}/documents",
            json={"local_path": "v.pdf"},
        )
        self.assertEqual(response.status_code, 404)

    def test_without_a_configured_root_the_endpoint_fails_closed(self):
        source_id = self._register_source()
        os.environ.pop("KNOWLEDGE_ENGINE_DOCUMENT_ROOT", None)
        response = self.client.post(
            f"/api/v1/knowledge-engine/sources/{source_id}/documents",
            json={"local_path": "v.pdf"},
        )
        self.assertEqual(response.status_code, 503)
        self.assertIn("KNOWLEDGE_ENGINE_DOCUMENT_ROOT", response.json()["detail"])

    # -- leitura e arquivamento ------------------------------------------

    def test_get_and_list(self):
        source_id = self._register_source()
        self._register_source(BNCC)
        detail = self.client.get(f"/api/v1/knowledge-engine/sources/{source_id}")
        self.assertEqual(detail.status_code, 200)
        listing = self.client.get("/api/v1/knowledge-engine/sources").json()
        self.assertEqual(listing["count"], 2)
        filtered = self.client.get(
            "/api/v1/knowledge-engine/sources", params={"rights_class": "OFFICIAL_PUBLIC"}
        ).json()
        self.assertEqual(filtered["count"], 1)

    def test_an_unknown_source_is_404(self):
        self.assertEqual(
            self.client.get(f"/api/v1/knowledge-engine/sources/{uuid.uuid4()}").status_code, 404
        )

    def test_delete_archives_instead_of_deleting(self):
        """Critério de aceite 4."""
        source_id = self._register_source()
        response = self.client.delete(f"/api/v1/knowledge-engine/sources/{source_id}")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["status"], "ARCHIVED")
        # A linha continua la, encontravel pedindo o status explicitamente.
        still_there = self.client.get(f"/api/v1/knowledge-engine/sources/{source_id}")
        self.assertEqual(still_there.status_code, 200)
        archived = self.client.get(
            "/api/v1/knowledge-engine/sources", params={"status": "ARCHIVED"}
        ).json()
        self.assertEqual(archived["count"], 1)

    # -- autorizacao -----------------------------------------------------

    def test_a_non_admin_identity_is_refused_on_every_endpoint(self):
        """Critério de aceite 5 (autorizacao, nao autenticacao - ver docstring)."""
        source_id = self._register_source()
        self.identity["value"] = ExternalIdentityContext(
            provider="test", external_user_id="prof_mendes", roles=("teacher",)
        )
        calls = (
            self.client.post("/api/v1/knowledge-engine/sources", json=COMMERCIAL),
            self.client.get("/api/v1/knowledge-engine/sources"),
            self.client.get(f"/api/v1/knowledge-engine/sources/{source_id}"),
            self.client.get(f"/api/v1/knowledge-engine/sources/{source_id}/documents"),
            self.client.post(
                f"/api/v1/knowledge-engine/sources/{source_id}/documents",
                json={"local_path": "v.pdf"},
            ),
            self.client.delete(f"/api/v1/knowledge-engine/sources/{source_id}"),
        )
        for response in calls:
            self.assertEqual(response.status_code, 403, response.request.url)


class SchemaSurfaceTests(unittest.TestCase):
    """O texto literal nao e filtrado em runtime: o campo nao existe."""

    def test_no_response_schema_declares_a_literal_text_field(self):
        """Critério de aceite 8."""
        forbidden = {"raw_text", "text", "content", "body", "excerpt"}
        offenders = []
        for name in dir(knowledge_schemas):
            candidate = getattr(knowledge_schemas, name)
            fields = getattr(candidate, "model_fields", None)
            if not isinstance(fields, dict) or not name.endswith("Response"):
                continue
            leaked = forbidden & set(fields)
            if leaked:
                offenders.append(f"{name}: {sorted(leaked)}")
        self.assertEqual(offenders, [], "schema de resposta expondo texto literal: " + str(offenders))

    def test_no_response_schema_exposes_the_server_storage_path(self):
        for name in dir(knowledge_schemas):
            candidate = getattr(knowledge_schemas, name)
            fields = getattr(candidate, "model_fields", None)
            if isinstance(fields, dict) and name.endswith("Response"):
                self.assertNotIn("storage_uri", fields, name)

    def test_the_guard_would_catch_a_regression(self):
        """Um teste estrutural que nao consegue falhar nao protege nada."""
        self.assertIn("document_hash", knowledge_schemas.KnowledgeDocumentResponse.model_fields)


class ReceptionGuardTests(unittest.TestCase):
    """O router entra em ``app.py`` sob ``reception_only_guard``, como todo
    router nao-publico. Isto so e verificavel pelo app REAL."""

    def test_the_router_is_registered_in_the_real_app(self):
        from agente_ia_edu.api.app import app

        paths = {path for path in app.openapi()["paths"] if "knowledge-engine" in path}
        self.assertEqual(
            paths,
            {
                "/api/v1/knowledge-engine/sources",
                "/api/v1/knowledge-engine/sources/{source_id}",
                "/api/v1/knowledge-engine/sources/{source_id}/documents",
            },
        )

    def test_the_real_app_applies_the_reception_guard_to_the_router(self):
        import inspect

        from agente_ia_edu.api import app as app_module

        source = inspect.getsource(app_module.create_app)
        self.assertIn(
            "app.include_router(knowledge_engine_router, dependencies=reception_only_guard)",
            source,
        )


if __name__ == "__main__":
    unittest.main()
