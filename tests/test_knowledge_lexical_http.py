"""CEREBRO - Fase 5: a camada HTTP do diagnostico lexical.

Passa pelo caminho HTTP de verdade (``TestClient``), porque o que importa
aqui e exatamente o que chamar a funcao da rota pularia: injecao de
dependencia, mapeamento de excecao para status e, acima de tudo,
**serializacao pelo response_model** - e e a serializacao que faz a politica
de direitos valer.

NOTA SOBRE AUTENTICACAO, repetida de proposito. Este projeto nao tem
autenticacao real (``TestExternalIdentityProvider`` confia no header). Os
testes de 403 aqui provam AUTORIZACAO - que a rota exige PLATFORM_ADMIN -,
nao autenticacao de producao, que nao existe ainda.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.knowledge_lexical import knowledge_lexical_router
from agente_ia_edu.api.schemas import knowledge_lexical as lexical_schemas
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import KnowledgeChunk, KnowledgeDocument, KnowledgeSource
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.knowledge_engine.lexical_index import LexicalIndexService

SEARCH = "/api/v1/knowledge-engine/lexical/search"
EXPLAIN = "/api/v1/knowledge-engine/lexical/explain-query"
STATUS = "/api/v1/knowledge-engine/lexical/index-status"


class _LexicalHTTP(unittest.TestCase):
    def setUp(self):
        async def build():
            engine = create_async_engine(
                "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
            )
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            return engine, async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=True
            )

        self.engine, self.factory = asyncio.run(build())
        self.identity = {
            "value": ExternalIdentityContext(
                provider="test", external_user_id="ADMIN", roles=("PLATFORM_ADMIN",)
            )
        }
        app = FastAPI()
        app.include_router(knowledge_lexical_router)
        app.dependency_overrides[get_session_factory] = lambda: self.factory
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        self.client = TestClient(app)
        self.addCleanup(lambda: asyncio.run(self.engine.dispose()))
        self.ids = asyncio.run(self._seed())

    async def _seed(self) -> dict[str, uuid.UUID]:
        ids: dict[str, uuid.UUID] = {}
        async with self.factory() as session:
            commercial = KnowledgeSource(
                title="Quimica na abordagem do cotidiano",
                source_kind="TEXTBOOK",
                rights_class="COMMERCIAL_REFERENCE",
                authority_level="COMMERCIAL_TEXTBOOK",
            )
            official = KnowledgeSource(
                title="BNCC Ensino Medio",
                source_kind="CURRICULUM_FRAMEWORK",
                rights_class="OFFICIAL_PUBLIC",
                authority_level="OFFICIAL",
            )
            session.add_all([commercial, official])
            await session.flush()
            documents = {}
            for key, source in (("comercial", commercial), ("bncc", official)):
                document = KnowledgeDocument(
                    source_id=source.id,
                    filename=f"{key}.pdf",
                    storage_uri=f"/tmp/{key}.pdf",
                    document_hash=key.ljust(64, "x")[:64],
                )
                session.add(document)
                await session.flush()
                documents[key] = document.id
            ids["comercial_source"] = commercial.id
            ids["bncc_source"] = official.id
            ids["comercial_document"] = documents["comercial"]
            ids["bncc_document"] = documents["bncc"]

            rows = [
                KnowledgeChunk(
                    source_id=commercial.id,
                    document_id=documents["comercial"],
                    ordinal=1,
                    chunk_type="PROSE",
                    heading_path=["Chapter 9 - Solucoes"],
                    page_start=312,
                    page_end=312,
                    raw_text="A diluicao reduz a concentracao das solucoes aquosas.",
                    text_hash="1" * 64,
                    char_count=52,
                ),
                KnowledgeChunk(
                    source_id=commercial.id,
                    document_id=documents["comercial"],
                    ordinal=2,
                    chunk_type="SOLUTION",
                    heading_path=["Chapter 9"],
                    page_start=500,
                    page_end=500,
                    raw_text="Resposta: a diluicao correta e de 1 para 10.",
                    text_hash="2" * 64,
                    char_count=43,
                ),
                KnowledgeChunk(
                    source_id=official.id,
                    document_id=documents["bncc"],
                    ordinal=1,
                    chunk_type="CURRICULUM_ITEM",
                    heading_path=["Ciencias da Natureza", "Competencia especifica 1"],
                    page_start=117,
                    page_end=117,
                    raw_text="Analisar diluicao e transformacoes em sistemas.",
                    text_hash="3" * 64,
                    char_count=46,
                    bncc_node_codes=["EM13CNT101"],
                ),
            ]
            session.add_all(rows)
            await session.flush()
            ids["commercial_chunk"] = rows[0].id
            ids["solution_chunk"] = rows[1].id
            ids["bncc_chunk"] = rows[2].id
            await LexicalIndexService(session).index_chunks(rows)
            await session.commit()
        return ids


class SearchEndpointTests(_LexicalHTTP):
    def test_a_search_returns_hits_with_full_traceability(self):
        response = self.client.post(SEARCH, json={"query": "diluição"})
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertGreater(len(body["hits"]), 0)
        for hit in body["hits"]:
            for key in (
                "source_id",
                "source_title",
                "source_kind",
                "rights_class",
                "authority_level",
                "document_id",
                "document_filename",
                "page_start",
                "chunk_type",
                "heading_path",
                "score",
                "rank",
                "text_hash",
            ):
                self.assertIn(key, hit)
                self.assertIsNotNone(hit[key], key)

    def test_a_commercial_hit_has_no_text_key_at_all(self):
        """Nao e ``excerpt: null``: a chave NAO EXISTE no corpo serializado,
        porque o tipo devolvido nao a declara."""
        body = self.client.post(SEARCH, json={"query": "diluição"}).json()
        commercial = [
            hit for hit in body["hits"] if hit["rights_class"] == "COMMERCIAL_REFERENCE"
        ]
        self.assertTrue(commercial)
        for hit in commercial:
            self.assertNotIn("excerpt", hit)
            self.assertNotIn("raw_text", hit)
            self.assertFalse(hit["quotable"])

    def test_an_official_hit_carries_a_bounded_excerpt(self):
        body = self.client.post(SEARCH, json={"query": "diluição"}).json()
        official = [
            hit for hit in body["hits"] if hit["rights_class"] == "OFFICIAL_PUBLIC"
        ]
        self.assertTrue(official)
        self.assertTrue(official[0]["quotable"])
        self.assertIn("excerpt", official[0])
        self.assertLessEqual(len(official[0]["excerpt"]), 300)

    def test_no_commercial_text_appears_anywhere_in_the_payload(self):
        """A varredura e no corpo BRUTO: qualquer caminho que vazasse o
        literal - campo novo, metadata, explicacao - cairia aqui."""
        raw = self.client.post(
            SEARCH, json={"query": "diluição", "explain": True}
        ).text
        self.assertNotIn("reduz a concentracao das solucoes aquosas", raw)
        self.assertNotIn("Resposta: a diluicao correta", raw)

    def test_the_envelope_carries_mode_backend_and_generation(self):
        body = self.client.post(SEARCH, json={"query": "diluição"}).json()
        self.assertEqual(body["retrieval_mode"], "STRICT_CORPUS")
        self.assertEqual(body["lexical_backend"], "OWN_INVERTED_INDEX")
        self.assertIsNotNone(body["index_generation"])
        self.assertEqual(len(body["query_fingerprint"]), 64)
        self.assertEqual(body["policy"]["retrieval_mode"], "STRICT_CORPUS")

    def test_solution_is_closed_by_default_over_http(self):
        body = self.client.post(SEARCH, json={"query": "diluição"}).json()
        self.assertNotIn(
            "SOLUTION", [hit["chunk_type"] for hit in body["hits"]]
        )
        self.assertEqual(body["retrieval_purpose"], "UNKNOWN")
        self.assertFalse(body["solution_visible"])

    def test_solution_is_closed_in_practice_and_open_in_learn(self):
        practice = self.client.post(
            SEARCH, json={"query": "diluição", "retrieval_purpose": "PRACTICE"}
        ).json()
        learn = self.client.post(
            SEARCH, json={"query": "diluição", "retrieval_purpose": "LEARN"}
        ).json()
        self.assertNotIn("SOLUTION", [h["chunk_type"] for h in practice["hits"]])
        self.assertIn("SOLUTION", [h["chunk_type"] for h in learn["hits"]])
        self.assertEqual(practice["filtered_out"]["PURPOSE_SOLUTION"], 1)

    def test_an_unknown_purpose_is_422_with_a_named_code(self):
        response = self.client.post(
            SEARCH, json={"query": "diluição", "retrieval_purpose": "STUDYING"}
        )
        self.assertEqual(response.status_code, 422)
        self.assertEqual(
            response.json()["detail"]["code"], "INVALID_RETRIEVAL_PURPOSE"
        )

    def test_an_oversized_limit_is_refused_by_the_schema(self):
        response = self.client.post(SEARCH, json={"query": "mol", "limit": 10_000})
        self.assertEqual(response.status_code, 422)

    def test_the_explanation_is_omitted_unless_asked_for(self):
        plain = self.client.post(SEARCH, json={"query": "diluição"}).json()
        explained = self.client.post(
            SEARCH, json={"query": "diluição", "explain": True}
        ).json()
        self.assertIsNone(plain["hits"][0]["explanation"])
        self.assertIsNotNone(explained["hits"][0]["explanation"])
        self.assertIn("query_terms", explained["hits"][0]["explanation"])

    def test_an_empty_result_carries_a_named_reason(self):
        body = self.client.post(SEARCH, json={"query": "bauxita"}).json()
        self.assertEqual(body["hits"], [])
        self.assertIn("NO_LEXICAL_MATCH", body["empty_reasons"])
        self.assertEqual(body["unmatched_terms"], ["bauxita"])

    def test_filters_are_honoured_over_http(self):
        body = self.client.post(
            SEARCH,
            json={"query": "diluição", "source_kinds": ["CURRICULUM_FRAMEWORK"]},
        ).json()
        self.assertEqual(
            [hit["source_kind"] for hit in body["hits"]], ["CURRICULUM_FRAMEWORK"]
        )

    def test_a_bncc_code_is_searchable_over_http(self):
        body = self.client.post(SEARCH, json={"query": "EM13CNT101"}).json()
        self.assertEqual(len(body["hits"]), 1)
        self.assertEqual(body["hits"][0]["bncc_node_codes"], ["EM13CNT101"])

    def test_the_page_is_the_printed_page(self):
        body = self.client.post(SEARCH, json={"query": "diluição"}).json()
        pages = {hit["page_start"] for hit in body["hits"]}
        self.assertTrue(pages <= {312, 117, 500})

    def test_authorization_is_required(self):
        self.identity["value"] = ExternalIdentityContext(
            provider="test", external_user_id="TEACHER", roles=("TEACHER",)
        )
        for url in (SEARCH, EXPLAIN):
            self.assertEqual(
                self.client.post(url, json={"query": "mol"}).status_code, 403
            )
        self.assertEqual(self.client.get(STATUS).status_code, 403)


class ExplainQueryEndpointTests(_LexicalHTTP):
    def test_it_shows_the_normalization_without_searching(self):
        body = self.client.post(
            EXPLAIN, json={"query": "concentração das soluções"}
        ).json()
        terms = {item["term"]: item for item in body["terms"]}
        self.assertEqual(set(terms), {"concentracao", "solucao"})
        self.assertEqual(terms["solucao"]["positions_in_query"], [2])
        self.assertTrue(terms["solucao"]["matched"])
        self.assertGreater(terms["solucao"]["df"], 0)

    def test_it_explains_why_a_term_found_nothing(self):
        body = self.client.post(EXPLAIN, json={"query": "bauxita"}).json()
        self.assertFalse(body["terms"][0]["matched"])
        self.assertEqual(body["terms"][0]["df"], 0)
        self.assertIsNone(body["terms"][0]["idf"])

    def test_it_reports_dropped_tokens(self):
        body = self.client.post(EXPLAIN, json={"query": "a massa de pH"}).json()
        reasons = {item["surface"]: item["reason"] for item in body["dropped_tokens"]}
        self.assertEqual(reasons["a"], "TOO_SHORT")
        self.assertEqual(reasons["ph"], "TOO_SHORT")

    def test_it_carries_the_normalizer_version_and_the_generation(self):
        body = self.client.post(EXPLAIN, json={"query": "mol"}).json()
        self.assertEqual(body["normalizer_version"], "v1")
        self.assertIsNotNone(body["index_generation"])


class IndexStatusEndpointTests(_LexicalHTTP):
    def test_the_coverage_is_complete(self):
        body = self.client.get(STATUS).json()
        self.assertEqual(body["total_chunks"], 3)
        self.assertEqual(body["indexed"], 3)
        self.assertEqual(body["missing"], 0)
        self.assertEqual(body["stale"], 0)
        self.assertEqual(body["normalizer_versions_present"], ["v1"])
        self.assertGreater(body["postings"], 0)
        self.assertGreater(body["avgdl"], 0)

    def test_it_breaks_down_by_source(self):
        body = self.client.get(STATUS).json()
        self.assertEqual(len(body["by_source"]), 2)
        for row in body["by_source"]:
            self.assertEqual(row["missing"], 0)

    def test_it_can_be_scoped_to_one_source(self):
        body = self.client.get(
            STATUS, params={"source_id": str(self.ids["bncc_source"])}
        ).json()
        self.assertEqual(body["total_chunks"], 1)


class ReindexEndpointTests(_LexicalHTTP):
    def test_reindexing_shows_the_generation_before_and_after(self):
        """``index_generation`` demonstrado antes e depois - item do relatorio."""
        response = self.client.post(
            f"/api/v1/knowledge-engine/lexical/reindex/{self.ids['bncc_document']}"
        )
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["generation_after"], body["generation_before"] + 1)
        self.assertEqual(body["chunks_indexed"], 1)
        self.assertGreater(body["postings_written"], 0)

    def test_a_reindex_changes_the_query_fingerprint(self):
        before = self.client.post(SEARCH, json={"query": "diluição"}).json()
        self.client.post(
            f"/api/v1/knowledge-engine/lexical/reindex/{self.ids['bncc_document']}"
        )
        after = self.client.post(SEARCH, json={"query": "diluição"}).json()
        self.assertNotEqual(
            before["query_fingerprint"], after["query_fingerprint"]
        )
        self.assertNotEqual(before["index_generation"], after["index_generation"])

    def test_an_unknown_document_is_404(self):
        response = self.client.post(
            f"/api/v1/knowledge-engine/lexical/reindex/{uuid.uuid4()}"
        )
        self.assertEqual(response.status_code, 404)

    def test_the_corpus_stays_fully_indexed_after_a_reindex(self):
        self.client.post(
            f"/api/v1/knowledge-engine/lexical/reindex/{self.ids['comercial_document']}"
        )
        body = self.client.get(STATUS).json()
        self.assertEqual(body["missing"], 0)
        self.assertEqual(body["stale"], 0)
        self.assertEqual(body["indexed"], 3)


class SchemaSurfaceTests(unittest.TestCase):
    """O literal nao e filtrado em runtime: o campo nao existe no tipo."""

    def test_no_response_schema_declares_a_literal_text_field(self):
        forbidden = {"raw_text", "text", "content", "body"}
        offenders = []
        for name in dir(lexical_schemas):
            candidate = getattr(lexical_schemas, name)
            fields = getattr(candidate, "model_fields", None)
            if not isinstance(fields, dict) or not name.endswith("Response"):
                continue
            leaked = forbidden & set(fields)
            if leaked:
                offenders.append(f"{name}: {sorted(leaked)}")
        self.assertEqual(offenders, [])

    def test_the_restricted_hit_schema_has_no_excerpt(self):
        self.assertNotIn("excerpt", lexical_schemas.LexicalHitResponse.model_fields)
        self.assertIn(
            "excerpt", lexical_schemas.QuotableLexicalHitResponse.model_fields
        )

    def test_no_response_schema_exposes_the_server_storage_path(self):
        for name in dir(lexical_schemas):
            candidate = getattr(lexical_schemas, name)
            fields = getattr(candidate, "model_fields", None)
            if isinstance(fields, dict) and name.endswith("Response"):
                self.assertNotIn("storage_uri", fields, name)


if __name__ == "__main__":
    unittest.main()
