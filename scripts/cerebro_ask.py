"""CEREBRO - primeira consulta ponta a ponta, com relatorio de inspecao.

    pergunta -> VectorSearcher -> ContextBuilder -> GroundedAnswerer -> saida

Mostra os dez itens que tornam a execucao auditavel: pergunta, chunks
recuperados, rastreabilidade de cada um, o que entrou no contexto, a
resposta, as fontes citadas, os tempos separados, o custo real, a
identificacao do espaco e das versoes, e a indicacao explicita quando a
evidencia nao basta.

O QUE ELE NAO FAZ
=================

Nao rotula hit como relevante ou irrelevante. Para pergunta nova nao existe
julgamento, e inventar um aqui seria fabricar a regua que a Fase 6 levou
duas rodadas de adjudicacao humana para construir. Mostra rank, score e
metadados; a relevancia e julgada depois, por quem sabe.

Nao exibe literal de obra comercial. O texto chega ao prompt - decisao
explicita da politica do piloto, ver ``rights.py`` -, mas a saida publica
traz so metadados e, quando a fonte permite, excerpt.

    python scripts/cerebro_ask.py --dry-run "pergunta"   # sem chamada paga
    python scripts/cerebro_ask.py "pergunta"
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import time
from pathlib import Path

if __package__ is None:
    RAIZ = Path(__file__).resolve().parents[1]
    sys.path.insert(0, str(RAIZ / "src"))
    for linha in (RAIZ / ".env").read_text().splitlines():
        if "=" in linha and not linha.lstrip().startswith("#"):
            chave, _, valor = linha.partition("=")
            os.environ.setdefault(chave.strip(), valor.strip())

from sqlalchemy import select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession, async_sessionmaker, create_async_engine,
)

from agente_ia_edu.db.models import (  # noqa: E402
    KnowledgeChunk, KnowledgeEmbeddingSpace,
)
from agente_ia_edu.knowledge_retrieval_policy.v1 import POLICY  # noqa: E402
from agente_ia_edu.providers.factory import (  # noqa: E402
    build_embedding_provider, build_text_provider,
)
from agente_ia_edu.services.knowledge_engine.context_builder import (  # noqa: E402
    ContextBuilder,
)
from agente_ia_edu.services.knowledge_engine.embedding_activation import (  # noqa: E402
    EmbeddingActivationService,
)
from agente_ia_edu.services.knowledge_engine.grounded_answer import (  # noqa: E402
    GroundedAnswerer,
)
from agente_ia_edu.services.knowledge_engine.public_answer import (  # noqa: E402
    structured_to_public, to_public,
)
from agente_ia_edu.services.knowledge_engine.structured_answer import (  # noqa: E402
    StructuredAnswerer,
)
from agente_ia_edu.services.knowledge_engine.vector_search import (  # noqa: E402
    VectorSearcher,
)

#: Preco publico por 1M tokens. So entra na conta quando o provider reporta
#: uso REAL - estimativa nunca e apresentada como custo realizado.
PRECO_EMBEDDING = 0.02
PRECO_ENTRADA = 0.15
PRECO_SAIDA = 0.60


class _Cronometrado:
    """Separa a latencia do provider da do banco."""

    def __init__(self, inner):
        self._inner = inner
        self.ms = 0.0
        self.tokens = None

    async def embed(self, request):
        comeco = time.perf_counter()
        try:
            r = await self._inner.embed(request)
            self.tokens = r.input_tokens
            return r
        finally:
            self.ms = (time.perf_counter() - comeco) * 1000


def _rule(t):
    print()
    print("=" * 94)
    print(t)
    print("=" * 94)


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("pergunta")
    p.add_argument("--db", default="agente_ia_edu_fase6_vetorial")
    p.add_argument("--limit", type=int, default=10)
    p.add_argument("--budget", type=int, default=12_000)
    p.add_argument("--purpose", default="LEARN")
    p.add_argument("--allow-degraded", action="store_true")
    p.add_argument("--dry-run", action="store_true",
                   help="recupera e monta o contexto, NAO chama o gerador")
    p.add_argument("--json", type=Path, help="grava o relatorio completo")
    p.add_argument("--structured", action="store_true",
                   help="roda TAMBEM o StructuredAnswerer sobre o MESMO "
                        "contexto, sem nova recuperacao")
    args = p.parse_args()

    user = os.getenv("POSTGRES_USER", "agenteedu")
    senha = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    url = f"postgresql+psycopg://{user}:{senha}@localhost:5433/{args.db}"
    engine = create_async_engine(url)
    f = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=True)
    total_comeco = time.perf_counter()
    relatorio: dict = {"question": args.pergunta}

    # -- 9. espaco, politica e versoes ---------------------------------
    async with f() as s:
        espaco = await s.scalar(
            select(KnowledgeEmbeddingSpace).where(
                KnowledgeEmbeddingSpace.status == "ACTIVE"
            )
        )
        if espaco is None:
            print("SEM ESPACO DE EMBEDDING ATIVO - nada a fazer.")
            await engine.dispose()
            raise SystemExit(2)
        prontidao = await EmbeddingActivationService(s).readiness(
            espaco.id, expected_population=0
        )
        provider_emb = _Cronometrado(
            build_embedding_provider(espaco.provider, model=espaco.model)
        )
        space_id, dims = espaco.id, espaco.dimensions

    from agente_ia_edu.services.knowledge_engine.vector_search import (
        _space_fingerprint,
    )

    _rule("9. ESPACO, POLITICA E VERSOES (reproducao)")
    print(f"  banco                 : {args.db}")
    print(f"  embedding_space_id    : {space_id}")
    print(f"  fingerprint           : {_space_fingerprint(espaco)}")
    print(f"  dimensions / metrica  : {dims} / {espaco.distance_metric}")
    print(f"  policy_version        : {POLICY.version}")
    print(f"  normalizer_version    : {POLICY.normalizer_version}")
    print(f"  editorial_detector    : {POLICY.editorial_detector_version}")
    print(f"  candidate_cap         : {POLICY.candidate_cap}")
    print(f"  retrieval_purpose     : {args.purpose}")
    print(f"  cobertura             : elegiveis="
          f"{prontidao.coverage.eligible_chunks} embedded="
          f"{prontidao.coverage.embedded} missing="
          f"{prontidao.coverage.missing} stale={prontidao.coverage.stale}")
    print(f"  indice ANN presente   : {prontidao.ann_index_present}")
    relatorio["reproduction"] = {
        "database": args.db, "embedding_space_id": str(space_id),
        "fingerprint": _space_fingerprint(espaco), "dimensions": dims,
        "distance_metric": espaco.distance_metric,
        "policy_version": POLICY.version,
        "normalizer_version": POLICY.normalizer_version,
        "editorial_detector_version": POLICY.editorial_detector_version,
        "candidate_cap": POLICY.candidate_cap,
        "retrieval_purpose": args.purpose,
        "coverage": {
            "eligible_chunks": prontidao.coverage.eligible_chunks,
            "embedded": prontidao.coverage.embedded,
            "missing": prontidao.coverage.missing,
            "stale": prontidao.coverage.stale,
        },
        "ann_index_present": prontidao.ann_index_present,
    }

    # -- 1. a pergunta --------------------------------------------------
    _rule("1. PERGUNTA ENVIADA")
    print(f"  {args.pergunta!r}")

    # -- 2 e 3. recuperacao ---------------------------------------------
    comeco = time.perf_counter()
    async with f() as s:
        busca = await VectorSearcher(s, provider=provider_emb).search(
            args.pergunta, retrieval_purpose=args.purpose,
            limit=args.limit, allow_degraded=args.allow_degraded,
        )
    retrieval_ms = (time.perf_counter() - comeco) * 1000 - provider_emb.ms

    _rule("2-3. CHUNKS RECUPERADOS (sem rotulo de relevancia - ver docstring)")
    print(f"  {'rk':>2} {'score':>8} {'pag':>5} {'tipo':<15} {'papel':<12} "
          f"{'fonte':<28} {'chunk_id':<10}")
    for h in busca.hits:
        print(f"  {h.rank:>2} {h.score:>8.4f} {h.page_start:>5} "
              f"{h.chunk_type:<15} {h.editorial_role:<12} "
              f"{h.source_title[:28]:<28} {str(h.chunk_id)[:8]}")
    print(f"\n  backend={busca.vector_backend} "
          f"candidatos={busca.total_candidates} "
          f"cap={busca.candidate_cap_reached} "
          f"degradado={busca.degraded} fontes={busca.distinct_sources}")
    if busca.filtered_out:
        print(f"  filtrados ({busca.filtered_out_scope}): {busca.filtered_out}")
    relatorio["retrieval"] = {
        "hits": [
            {"rank": h.rank, "score": h.score, "distance": h.distance,
             "chunk_id": str(h.chunk_id), "text_hash": h.text_hash,
             "page_start": h.page_start, "page_end": h.page_end,
             "chunk_type": h.chunk_type, "editorial_role": h.editorial_role,
             "source_title": h.source_title, "source_id": str(h.source_id),
             "document_filename": h.document_filename,
             "document_id": str(h.document_id),
             "heading_path": list(h.heading_path),
             "rights_class": h.rights_class, "quotable": h.quotable}
            for h in busca.hits
        ],
        "total_candidates": busca.total_candidates,
        "candidate_cap_reached": busca.candidate_cap_reached,
        "degraded": busca.degraded,
        "degradation_reasons": list(busca.degradation_reasons),
        "filtered_out": dict(busca.filtered_out),
        "filtered_out_scope": busca.filtered_out_scope,
        "query_fingerprint": busca.query_fingerprint,
        "empty_reasons": list(busca.empty_reasons),
    }

    # -- 4. contexto ----------------------------------------------------
    comeco = time.perf_counter()
    async with f() as s:
        textos = dict((await s.execute(
            select(KnowledgeChunk.id, KnowledgeChunk.raw_text).where(
                KnowledgeChunk.id.in_([h.chunk_id for h in busca.hits])
            )
        )).all()) if busca.hits else {}
    contexto = ContextBuilder(budget_chars=args.budget).build(
        busca.hits, texts=textos
    )
    context_build_ms = (time.perf_counter() - comeco) * 1000

    _rule("4. O QUE ENTROU NO CONTEXTO")
    for e in contexto.evidences:
        print(f"  [{e.marker}] rank={e.retrieval_rank} p.{e.page_start} "
              f"{e.chunk_type}/{e.editorial_role} {e.source_title[:30]} "
              f"({e.chars_used} chars)")
    if contexto.excluded:
        print("  excluidos:")
        for x in contexto.excluded:
            print(f"     rank={x['rank']} {x['reason']} "
                  f"p.{x['page_start']} {x['source_title'][:30]}")
    print(f"\n  orcamento {contexto.used_chars}/{contexto.budget_chars} chars"
          f"  |  {len(contexto.evidences)} evidencias")
    relatorio["context"] = contexto.admin_payload()

    if args.dry_run:
        print("\n  --dry-run: o gerador NAO foi chamado.")
        relatorio["dry_run"] = True
        _gravar(args, relatorio, total_comeco, provider_emb,
                retrieval_ms, context_build_ms, 0.0)
        await engine.dispose()
        return

    # -- 5 a 8. geracao --------------------------------------------------
    comeco = time.perf_counter()
    resposta = await GroundedAnswerer(
        provider=build_text_provider()
    ).answer(
        args.pergunta, contexto,
        retrieval_degraded=busca.degraded,
        degradation_reasons=busca.degradation_reasons,
        allow_degraded=args.allow_degraded,
    )
    generation_ms = (time.perf_counter() - comeco) * 1000

    _rule("5. RESPOSTA (VISAO ADMIN - texto CRU, com marcadores)")
    print(f"  grounding   : {resposta.grounding}   "
          f"is_grounded={resposta.is_grounded}")
    print(f"  suficiencia : {resposta.sufficiency}")
    print(f"  ENTREGAVEL  : {resposta.deliverable}"
          + (f"   bloqueio={resposta.delivery_block_reason}"
             if not resposta.deliverable else ""))
    if resposta.needs_human_review:
        print("  >>> MARCADA PARA REVISAO HUMANA: o modelo declarou as"
              " evidencias insuficientes.")
    if resposta.stripping_artifacts:
        print(f"  >>> DEFEITO NA SANITIZACAO: "
              f"{list(resposta.stripping_artifacts)}")
    if resposta.answer:
        print()
        for linha in resposta.answer.split("\n"):
            print(f"  {linha}")
    if resposta.error:
        print(f"  erro: {resposta.error}")

    _rule("6. FONTES CITADAS NA RESPOSTA (SO ADMIN)")
    if resposta.cited_evidences:
        for e in resposta.cited_evidences:
            print(f"  [{e.marker}] {e.source_title} — pagina {e.page_start} "
                  f"— {e.chunk_type}/{e.editorial_role}")
            print(f"        chunk_id={e.chunk_id}  hash={e.text_hash[:12]}")
            if e.excerpt:
                print(f"        trecho: {e.excerpt[:120]}")
            else:
                print(f"        (fonte {e.rights_class}: literal nao exibido)")
    else:
        print("  nenhuma")
    if resposta.invalid_markers:
        print(f"\n  MARCADORES INVENTADOS: {list(resposta.invalid_markers)}")
    print(f"\n  used_evidence BRUTO      : {resposta.raw_used_evidence!r}")
    print(f"  used_evidence NORMALIZADO: "
          f"{list(resposta.normalized_used_evidence)}")

    # -- 10. suficiencia --------------------------------------------------
    _rule("10. SUFICIENCIA DA EVIDENCIA")
    if resposta.deliverable:
        print("  Evidencia suficiente para fundamentar a resposta.")
    else:
        print(f"  NAO ENTREGAVEL — {resposta.delivery_block_reason}")
        print("  A resposta acima, se houver, NAO vai ao usuario.")
    relatorio["answer"] = resposta.admin_payload()

    # -- o outro lado ----------------------------------------------------
    publico = to_public(resposta)
    _rule("O QUE O USUARIO RECEBERIA (aluno / professor)")
    print(f"  outcome            : {publico.outcome}")
    print(f"  unavailable_reason : {publico.unavailable_reason}")
    if publico.answer_text:
        print()
        for linha in publico.answer_text.split("\n"):
            print(f"  {linha}")
    else:
        print("\n  (nenhum texto de resposta e entregue)")
    print("\n  Sem marcadores, sem fonte, sem pagina, sem chunk_id, sem"
          " score.")
    print("  Se algo disso aparecer acima, e defeito - nao estilo.")
    relatorio["public"] = publico.payload()

    # -- caminho paralelo, sobre o MESMO contexto ------------------------
    if args.structured:
        comeco = time.perf_counter()
        estruturada = await StructuredAnswerer(
            provider=build_text_provider()
        ).answer(
            args.pergunta, contexto,
            retrieval_degraded=busca.degraded,
            degradation_reasons=busca.degradation_reasons,
            allow_degraded=args.allow_degraded,
        )
        estruturada_ms = (time.perf_counter() - comeco) * 1000
        _rule("PARALELO: StructuredAnswerer (MESMO contexto, sem nova busca)")
        print(f"  status      : {estruturada.status}")
        print(f"  entregavel  : {estruturada.deliverable}"
              + (f"   bloqueio={estruturada.delivery_block_reason}"
                 if not estruturada.deliverable else ""))
        print(f"  suficiencia : {estruturada.sufficiency}")
        print(f"  afirmacoes  : {estruturada.factual_count} factual, "
              f"{estruturada.meta_count} meta, "
              f"{estruturada.connective_count} conectiva")
        print(f"  caixa       : {estruturada.span_case_mismatches} "
              f"SPAN_CASE_MISMATCH")
        if estruturada.contract_errors:
            print(f"  CONTRATO    : {list(estruturada.contract_errors)}")
        for c in estruturada.claims:
            print(f"\n  [{c.index}] {c.kind}  verificada={c.verified}"
                  + (f"  motivos={list(c.unverified_reasons)}"
                     if not c.verified else ""))
            print(f"      {c.text}")
            for s in c.support:
                print(f"      <- {s.evidence_marker} [{s.status}] "
                      f"papel={s.role!r}")
                print(f"         span: {s.span_text[:90]!r}")
            if c.derivation:
                d = c.derivation
                print(f"      derivacao [{d.status}] {d.expression!r} "
                      f"= {d.declared_result} (calculado={d.computed})")
                for s in d.inputs:
                    print(f"         insumo {s.evidence_marker} "
                          f"[{s.status}] {s.span_text[:60]!r}")
        print(f"\n  TEXTO MONTADO (ADMIN): {estruturada.answer_text}")

        pub_est = structured_to_public(estruturada)
        _rule("O QUE O USUARIO RECEBERIA - caminho estruturado")
        print(f"  outcome            : {pub_est.outcome}")
        print(f"  unavailable_reason : {pub_est.unavailable_reason}")
        if pub_est.answer_text:
            print()
            for linha in pub_est.answer_text.split("\n"):
                print(f"  {linha}")
        else:
            print("\n  (nenhum texto de resposta e entregue)")
        print("\n  Sem span, sem evidencia, sem fonte, sem estado interno.")

        relatorio["structured"] = estruturada.admin_payload()
        relatorio["structured"]["generation_ms"] = round(estruturada_ms, 1)
        relatorio["structured_public"] = pub_est.payload()

    _gravar(args, relatorio, total_comeco, provider_emb, retrieval_ms,
            context_build_ms, generation_ms, resposta)
    await engine.dispose()


def _gravar(args, relatorio, total_comeco, provider_emb, retrieval_ms,
            context_build_ms, generation_ms, resposta=None):
    total_ms = (time.perf_counter() - total_comeco) * 1000
    _rule("7. TEMPOS")
    print(f"  query_embedding_ms : {provider_emb.ms:>8.0f}")
    print(f"  retrieval_ms       : {retrieval_ms:>8.0f}")
    print(f"  context_build_ms   : {context_build_ms:>8.0f}")
    print(f"  generation_ms      : {generation_ms:>8.0f}")
    print(f"  total_ms           : {total_ms:>8.0f}")
    relatorio["timings_ms"] = {
        "query_embedding_ms": round(provider_emb.ms, 1),
        "retrieval_ms": round(retrieval_ms, 1),
        "context_build_ms": round(context_build_ms, 1),
        "generation_ms": round(generation_ms, 1),
        "total_ms": round(total_ms, 1),
    }

    _rule("8. TOKENS E CUSTO")
    emb_tok = provider_emb.tokens
    ent = resposta.input_tokens if resposta else None
    sai = resposta.output_tokens if resposta else None
    print(f"  tokens embedding : {emb_tok if emb_tok is not None else 'n/d'}")
    print(f"  tokens entrada   : {ent if ent is not None else 'n/d'}")
    print(f"  tokens saida     : {sai if sai is not None else 'n/d'}")
    # Num dry-run o gerador nem foi chamado, entao nao HA componente de
    # geracao a faltar - o custo do embedding e o custo total.
    esperados = ("embedding",) if resposta is None else (
        "embedding", "entrada", "saida"
    )
    partes, custo, faltando = [], 0.0, []
    for rotulo, toks, preco in (("embedding", emb_tok, PRECO_EMBEDDING),
                                ("entrada", ent, PRECO_ENTRADA),
                                ("saida", sai, PRECO_SAIDA)):
        if rotulo not in esperados:
            continue
        if toks is None:
            partes.append(f"{rotulo}=SEM DADO")
            faltando.append(rotulo)
            continue
        parcela = toks / 1_000_000 * preco
        custo += parcela
        partes.append(f"{rotulo}=US$ {parcela:.8f}")
    print(f"  {' | '.join(partes)}")
    if not faltando:
        rotulo = ("CUSTO REALIZADO (so embedding; geracao nao foi chamada)"
                  if resposta is None else "CUSTO REALIZADO")
        print(f"  {rotulo}: US$ {custo:.8f}")
    else:
        print(f"  CUSTO PARCIAL    : US$ {custo:.8f} "
              f"(sem dado real de {', '.join(faltando)}; NAO e o total)")
    completo = not faltando
    relatorio["cost"] = {
        "embedding_tokens": emb_tok, "input_tokens": ent,
        "output_tokens": sai, "usd": round(custo, 10),
        "complete": bool(completo),
    }

    if args.json:
        args.json.write_text(json.dumps(relatorio, indent=2,
                                        ensure_ascii=False, default=str))
        print(f"\n  relatorio: {args.json}")


if __name__ == "__main__":
    asyncio.run(main())
