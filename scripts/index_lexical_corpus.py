"""Reindexa o indice lexical do CEREBRO para um corpus JA ingerido.

Quando isto e necessario
========================

A ingestao (Fase 5) indexa na mesma transacao que persiste os chunks, logo um
documento recem-chunkado ja nasce indexado e este script NAO e parte do fluxo
normal. Ele existe para tres situacoes:

1. corpus ingerido ANTES da Fase 5 - chunks sem posting algum;
2. troca de ``normalizer_version`` - o indice antigo passa a ser obsoleto, e
   ``index-status`` acusa a divergencia;
3. suspeita de indice parcial, por qualquer motivo.

Por que script e nao endpoint
=============================

O acervo do piloto tem ~13 mil chunks. Reindexar tudo e trabalho longo, e a
forma errada de fazer trabalho longo e um timeout de HTTP esperando por ele.
O endpoint ``POST /lexical/reindex/{document_id}`` existe para UM documento,
que e trabalho limitado.

Uso
===

    python scripts/index_lexical_corpus.py --dry-run
    python scripts/index_lexical_corpus.py --stale-only
    python scripts/index_lexical_corpus.py --source-id <uuid>
    python scripts/index_lexical_corpus.py            # tudo

``--dry-run`` nao escreve nada: diz o que seria feito, com os numeros de
cobertura atuais. E o default do bom senso para um corpus de producao.
"""

from __future__ import annotations

import argparse
import asyncio
import os
import sys
from pathlib import Path
from uuid import UUID

if __package__ is None:  # execucao direta: o src/ do checkout tem de vir antes
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "src"))

from sqlalchemy import func, select  # noqa: E402
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine  # noqa: E402

from agente_ia_edu.db.models import (  # noqa: E402
    KnowledgeChunk,
    KnowledgeChunkLexicalIndex,
    KnowledgeDocument,
    KnowledgeSource,
)
from agente_ia_edu.knowledge_retrieval_policy.v1 import POLICY  # noqa: E402
from agente_ia_edu.services.knowledge_engine.lexical_index import (  # noqa: E402
    LexicalIndexService,
)


def _database_url() -> str:
    url = os.getenv("DATABASE_URL")
    if url:
        return url.replace("postgresql://", "postgresql+psycopg://", 1)
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    host = os.getenv("POSTGRES_HOST", "localhost")
    port = os.getenv("POSTGRES_PORT", "5433")
    database = os.getenv("POSTGRES_DB", "agente_ia_edu")
    return f"postgresql+psycopg://{user}:{password}@{host}:{port}/{database}"


async def _documents_to_process(
    session: AsyncSession, *, source_id: UUID | None, stale_only: bool
) -> list[tuple[UUID, str, str, int]]:
    """``(document_id, filename, source_title, chunks)`` dos documentos alvo."""
    query = (
        select(
            KnowledgeDocument.id,
            KnowledgeDocument.filename,
            KnowledgeSource.title,
            func.count(KnowledgeChunk.id),
        )
        .join(KnowledgeSource, KnowledgeSource.id == KnowledgeDocument.source_id)
        .join(KnowledgeChunk, KnowledgeChunk.document_id == KnowledgeDocument.id)
        .group_by(KnowledgeDocument.id, KnowledgeDocument.filename, KnowledgeSource.title)
        .order_by(KnowledgeSource.title, KnowledgeDocument.filename)
    )
    if source_id is not None:
        query = query.where(KnowledgeDocument.source_id == source_id)

    rows = list((await session.execute(query)).all())
    if not stale_only:
        return rows

    # Um documento precisa de reindexacao se tem ALGUM chunk sem posting, ou
    # cujo texto mudou, ou indexado por um normalizador anterior. Um unico
    # chunk divergente basta: indice parcial e pior que indice ausente, porque
    # a busca responde e esconde material.
    needs_work = set(
        (
            await session.scalars(
                select(KnowledgeChunk.document_id)
                .outerjoin(
                    KnowledgeChunkLexicalIndex,
                    KnowledgeChunkLexicalIndex.chunk_id == KnowledgeChunk.id,
                )
                .where(
                    (KnowledgeChunkLexicalIndex.chunk_id.is_(None))
                    | (KnowledgeChunkLexicalIndex.text_hash != KnowledgeChunk.text_hash)
                    | (
                        KnowledgeChunkLexicalIndex.normalizer_version
                        != POLICY.normalizer_version
                    )
                )
                .group_by(KnowledgeChunk.document_id)
            )
        ).all()
    )
    return [row for row in rows if row[0] in needs_work]


async def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-id", type=UUID, default=None)
    parser.add_argument(
        "--stale-only",
        action="store_true",
        help="so documentos cujo indice divergiu do texto ou do normalizador",
    )
    parser.add_argument("--dry-run", action="store_true")
    arguments = parser.parse_args()

    engine = create_async_engine(_database_url())
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=True)
    try:
        async with factory() as session:
            status = await LexicalIndexService(session).status(
                source_id=arguments.source_id
            )
            targets = await _documents_to_process(
                session, source_id=arguments.source_id, stale_only=arguments.stale_only
            )

        print(f"normalizador        {POLICY.normalizer_version}")
        print(f"politica            {POLICY.version}")
        print(f"geracao atual       {status.generation}")
        print(f"chunks              {status.total_chunks}")
        print(f"indexados           {status.indexed}")
        print(f"ausentes            {status.missing}")
        print(f"obsoletos           {status.stale}")
        print(f"postings            {status.postings}")
        print(f"documentos alvo     {len(targets)}")
        print()

        if arguments.dry_run:
            for document_id, filename, title, chunks in targets:
                print(f"  [dry-run] {title} / {filename}  ({chunks} chunks)")
            print("\nnada foi escrito (--dry-run)")
            return 0

        indexed = postings = 0
        for document_id, filename, title, chunks in targets:
            async with factory() as session:
                snapshot = await LexicalIndexService(session).reindex_document(
                    document_id
                )
                await session.commit()
            indexed += snapshot.chunks_indexed
            postings += snapshot.postings_written
            print(
                f"  {title} / {filename}: {snapshot.chunks_indexed} chunks, "
                f"{snapshot.postings_written} postings, "
                f"geracao {snapshot.generation}, {snapshot.duration_seconds}s"
            )

        async with factory() as session:
            final = await LexicalIndexService(session).status(
                source_id=arguments.source_id
            )
        print()
        print(f"total indexado      {indexed} chunks, {postings} postings")
        print(f"geracao final       {final.generation}")
        print(f"ausentes            {final.missing}")
        print(f"obsoletos           {final.stale}")
        return 0 if final.missing == 0 and final.stale == 0 else 1
    finally:
        await engine.dispose()


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
