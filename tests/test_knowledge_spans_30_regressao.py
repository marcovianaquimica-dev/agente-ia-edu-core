"""CEREBRO - os 30 pares adjudicados como regressao permanente.

O QUE ESTES TESTES PROTEGEM
===========================

Trinta pares ``afirmacao -> trecho`` produzidos pelo StructuredAnswerer
em chamada real e adjudicados por humano: 9 ``SUSTENTA_INTEGRALMENTE``,
21 ``SUSTENTA_PARCIALMENTE``, 0 ``NAO_SUSTENTA``, 0 ``CONTRADIZ``.

Se uma mudanca futura fizer qualquer um deles deixar de verificar, e
regressao a investigar.

POR QUE A FIXTURE NAO TEM O TEXTO
=================================

O span e literal de obra comercial. A fixture guarda ``(chunk_id,
offset, comprimento)`` no chunk NORMALIZADO, e o teste recorta a regiao
do chunk vivo.

O texto da AFIRMACAO tambem ficou de fora, e por uma razao que vale
registrar: quando o modelo parafraseia de perto, a afirmacao e na
pratica copia do livro. A afirmacao de PC sobre grupos representativos
coincide em 30 caracteres com o span que a sustenta. Guardar hash
preserva a identidade sem guardar o literal.

NAO E CALIBRATION SET
=====================

Regressao. Nao serve para calibrar limiar, escolher parametro nem medir
populacao: os 30 vem de 4 perguntas em que o caminho estruturado
funcionou, e nao ha na amostra nenhum caso em que ele falhou - porque
nao se procurou por eles.
"""

from __future__ import annotations

import json
import os
import unittest
from collections import Counter
from pathlib import Path

FIXTURE = (Path(__file__).parent / "fixtures" / "cerebro_spans_30.json")
PARES = json.loads(FIXTURE.read_text(encoding="utf-8"))

CLASSES_SUSTENTAM = {"SUSTENTA_INTEGRALMENTE", "SUSTENTA_PARCIALMENTE"}


class FixtureShapeTests(unittest.TestCase):
    """Puros: nao precisam de banco."""

    def test_there_are_exactly_thirty_pairs(self):
        self.assertEqual(len(PARES), 30)

    def test_every_id_is_unique(self):
        self.assertEqual(len({p["id"] for p in PARES}), 30)

    def test_the_adjudicated_distribution_is_pinned(self):
        """Se alguem reescrever a fixture, o total tem de bater com o que
        o humano julgou - 9 integrais e 21 parciais."""
        c = Counter(p["classe_humana"] for p in PARES)
        self.assertEqual(c["SUSTENTA_INTEGRALMENTE"], 9)
        self.assertEqual(c["SUSTENTA_PARCIALMENTE"], 21)
        self.assertEqual(c["NAO_SUSTENTA"], 0)
        self.assertEqual(c["CONTRADIZ"], 0)

    def test_nothing_was_judged_unsupported(self):
        """O resultado central: nenhum span verificado pelo codigo foi
        julgado irrelevante pelo humano."""
        for p in PARES:
            with self.subTest(id=p["id"]):
                self.assertIn(p["classe_humana"], CLASSES_SUSTENTAM)

    def test_no_claim_rests_on_a_single_partial_span(self):
        """Complementaridade pressupoe que as partes se somem. Um parcial
        sozinho nao soma com ninguem."""
        por_claim = {}
        for p in PARES:
            por_claim.setdefault((p["caso"], p["claim_index"]), []).append(
                p["classe_humana"])
        for chave, classes in por_claim.items():
            with self.subTest(claim=chave):
                if "SUSTENTA_INTEGRALMENTE" not in classes:
                    self.assertGreaterEqual(len(classes), 2)

    def test_the_four_cases_are_all_present(self):
        self.assertEqual({p["caso"] for p in PARES}, {"PA", "PB", "PC", "PD"})

    def test_sixteen_distinct_claims(self):
        self.assertEqual(
            len({(p["caso"], p["claim_index"]) for p in PARES}), 16)

    def test_every_pair_has_a_resolvable_span_region(self):
        for p in PARES:
            with self.subTest(id=p["id"]):
                self.assertIsInstance(p["span_offset"], int)
                self.assertGreaterEqual(p["span_offset"], 0)
                self.assertGreater(p["span_length"], 0)
                self.assertTrue(p["chunk_id"])

    def test_the_fixture_carries_no_commercial_literal(self):
        """O span e a evidencia nao podem estar no repositorio. So
        identificadores, deslocamentos e hashes."""
        bruto = FIXTURE.read_text(encoding="utf-8")
        permitidos = {"id", "caso", "claim_index", "claim_sha256_12",
                      "claim_chars", "evidence_marker", "chunk_id",
                      "span_offset", "span_length", "classe_humana"}
        for p in PARES:
            self.assertEqual(set(p), permitidos)
        self.assertNotIn("claim_text", bruto)
        self.assertNotIn("span_text", bruto)


@unittest.skipUnless(os.getenv("POSTGRES_USER")
                     or Path("/Users/marcoviana/agente-ia-edu-core/"
                             ".claude/worktrees/cerebro-fase1/.env").exists(),
                     "precisa do banco do corpus")
class SpanStillVerifiesTests(unittest.IsolatedAsyncioTestCase):
    """O coracao da regressao: cada regiao continua verificando.

    Precisa do banco porque o literal nao esta no repositorio - que e
    exatamente a troca que a politica de direitos impoe.
    """

    async def _chunks(self):
        import sys
        raiz = Path(__file__).resolve().parents[1]
        sys.path.insert(0, str(raiz / "src"))
        for linha in (raiz / ".env").read_text().splitlines():
            if "=" in linha and not linha.lstrip().startswith("#"):
                k, _, v = linha.partition("=")
                os.environ.setdefault(k.strip(), v.strip())
        from sqlalchemy import select
        from sqlalchemy.ext.asyncio import (
            AsyncSession, async_sessionmaker, create_async_engine,
        )
        from agente_ia_edu.db.models import KnowledgeChunk

        user = os.getenv("POSTGRES_USER", "agenteedu")
        senha = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
        engine = create_async_engine(
            f"postgresql+psycopg://{user}:{senha}@localhost:5433/"
            f"agente_ia_edu_fase6_vetorial")
        f = async_sessionmaker(engine, class_=AsyncSession,
                               expire_on_commit=True)
        ids = list({p["chunk_id"] for p in PARES})
        async with f() as s:
            linhas = (await s.execute(
                select(KnowledgeChunk.id, KnowledgeChunk.raw_text)
                .where(KnowledgeChunk.id.in_(ids)))).all()
        await engine.dispose()
        return {str(k): v for k, v in linhas}

    async def test_every_one_of_the_thirty_regions_still_verifies(self):
        from agente_ia_edu.services.knowledge_engine.span_verification import (
            SPAN_VERIFIED, normalize_span, verify_span,
        )

        textos = await self._chunks()
        self.assertEqual(len(textos), 11, "chunks do corpus mudaram")
        for p in PARES:
            with self.subTest(id=p["id"]):
                normalizado = normalize_span(textos[p["chunk_id"]])
                ini = p["span_offset"]
                recorte = normalizado[ini:ini + p["span_length"]]
                self.assertEqual(len(recorte), p["span_length"],
                                 "o chunk encolheu")
                status, pos = verify_span(recorte, textos[p["chunk_id"]])
                self.assertEqual(status, SPAN_VERIFIED)
                self.assertEqual(pos, ini)

    async def test_no_region_verifies_against_a_different_chunk(self):
        """A propriedade que a ma atribuicao explora: o trecho pertence
        AQUELE chunk, nao a qualquer um."""
        from agente_ia_edu.services.knowledge_engine.span_verification import (
            SPAN_VERIFIED, normalize_span, verify_span,
        )

        textos = await self._chunks()
        for p in PARES:
            normalizado = normalize_span(textos[p["chunk_id"]])
            ini = p["span_offset"]
            recorte = normalizado[ini:ini + p["span_length"]]
            if len(recorte) < 25:
                continue      # trecho curto pode casar por coincidencia
            for outro, texto in textos.items():
                if outro == p["chunk_id"]:
                    continue
                with self.subTest(id=p["id"], contra=outro[:8]):
                    status, _ = verify_span(recorte, texto)
                    self.assertNotEqual(status, SPAN_VERIFIED)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
