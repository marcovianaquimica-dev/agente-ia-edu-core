"""Microacervo de Quimica do Piloto Zero: o no de Balanceamento e o arco
curricular Estequiometria -> Balanceamento.

O QUE ESTE SCRIPT FAZ, E SO ISSO
=================================
1. cria o CONTENT `CHEMISTRY-GENERAL-BALANCING` sob `CHEMISTRY-GENERAL`;
2. registra o arco `CHEMISTRY-PHYSICAL-STOICHIOMETRY REQUIRES
   CHEMISTRY-GENERAL-BALANCING`.

Nao publica questao, nao classifica questao, nao cria escola, nao toca em
nada alem desses dois registros curriculares.

POR QUE O NO E NOVO, E POR QUE AQUI
====================================
A taxonomia ja tinha Estequiometria (`CHEMISTRY-PHYSICAL-STOICHIOMETRY`,
sob Fisico-Quimica). Balanceamento nao existia em lugar nenhum: o unico no
de "reacoes" era `CHEMISTRY-ORGANIC-REACTIONS`, que e Quimica Organica -
outro conceito, nao o pre-requisito de calculo estequiometrico.

O pai escolhido e `CHEMISTRY-GENERAL` (Quimica Geral), onde ja moram
"Propriedades da materia" e "Polaridade e forcas intermoleculares":
balancear equacoes e conteudo de Quimica Geral, ensinado antes de
Fisico-Quimica. O codigo segue a convencao existente,
CHEMISTRY-<AREA>-<CONTEUDO>.

A PROVENIENCIA DO ARCO
=======================
A relacao curricular NAO foi inferida por IA. Foi autorizada explicitamente
pelo responsavel pedagogico do projeto, professor de Quimica, no prompt de
2026-10-04 que abriu o microacervo do Piloto Zero:

    "eu, responsavel pedagogico e professor de Quimica, autorizo trabalhar
     com a relacao curricular: ESTEQUIOMETRIA E CALCULOS QUIMICOS requer
     conhecimento previo adequado de BALANCEAMENTO DE EQUACOES/REACOES
     QUIMICAS"

Isso fica gravado no `metadata` do no e e o que distingue este arco de uma
sugestao automatica. O principio permanente do projeto - IA sugere, humano
valida conhecimento curricular - continua valendo: a classificacao
INDIVIDUAL de cada questao NAO e validada por este script.

Idempotente. Seguro para reexecutar.

Uso:
    .venv/bin/python scripts/seed_microacervo_quimica.py
    .venv/bin/python scripts/seed_microacervo_quimica.py --conferir
"""

from __future__ import annotations

import asyncio
import os
import sys

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.models import CatalogNode
from agente_ia_edu.db.models.catalog import CatalogNodePrerequisite

ALVO = "CHEMISTRY-PHYSICAL-STOICHIOMETRY"
PRE = "CHEMISTRY-GENERAL-BALANCING"
PRE_NOME = "Reações químicas e balanceamento"
PAI_DO_PRE = "CHEMISTRY-GENERAL"

PROVENIENCIA = {
    "validation": "HUMAN_VALIDATED",
    "validated_by": "responsavel_pedagogico_do_projeto",
    "validated_at": "2026-10-04",
    "context": "PILOTO_ZERO",
    "rationale": (
        "Calculo estequiometrico exige a equacao quimica corretamente "
        "balanceada: sem a proporcao entre reagentes e produtos nao ha o que "
        "calcular. Relacao autorizada explicitamente pelo professor de "
        "Quimica responsavel pelo projeto."
    ),
}


def url() -> str:
    direto = os.getenv("DATABASE_URL")
    if direto:
        return (direto if "+" in direto.split("://")[0]
                else direto.replace("postgresql://", "postgresql+asyncpg://", 1))
    u = os.getenv("POSTGRES_USER", "agenteedu")
    p = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    d = os.getenv("POSTGRES_DB", "agente_ia_edu")
    return f"postgresql+psycopg://{u}:{p}@localhost:5433/{d}"


async def no_por_codigo(s: AsyncSession, codigo: str) -> CatalogNode | None:
    return await s.scalar(select(CatalogNode).where(CatalogNode.code == codigo))


async def garantir_no_do_pre_requisito(s: AsyncSession) -> CatalogNode:
    existente = await no_por_codigo(s, PRE)
    if existente is not None:
        print(f"[no] ja existe: {PRE} ({existente.name})")
        return existente

    pai = await no_por_codigo(s, PAI_DO_PRE)
    if pai is None:
        raise SystemExit(
            f"ABORTADO: a area {PAI_DO_PRE} nao existe. Este script nao cria "
            "area - se a taxonomia mudou, a decisao de onde pendurar o "
            "conteudo e humana.")

    irmaos = (await s.scalars(
        select(CatalogNode).where(CatalogNode.parent_id == pai.id))).all()
    posicao = max([n.position for n in irmaos], default=0) + 1

    no = CatalogNode(
        code=PRE, name=PRE_NOME, node_type="CONTENT",
        parent_id=pai.id, root_id=pai.root_id, position=posicao, active=True,
        metadata_={"curriculum_provenance": PROVENIENCIA},
    )
    s.add(no)
    await s.flush()
    print(f"[no] criado: {PRE} ({PRE_NOME}) sob {PAI_DO_PRE}, posicao {posicao}")
    return no


async def garantir_arco(s: AsyncSession, alvo: CatalogNode, pre: CatalogNode) -> None:
    existente = await s.scalar(
        select(CatalogNodePrerequisite).where(
            CatalogNodePrerequisite.content_node_id == alvo.id,
            CatalogNodePrerequisite.prerequisite_node_id == pre.id))
    if existente is not None:
        print(f"[arco] ja existe: {alvo.code} REQUIRES {pre.code}")
        return
    s.add(CatalogNodePrerequisite(content_node_id=alvo.id,
                                  prerequisite_node_id=pre.id))
    await s.flush()
    print(f"[arco] criado: {alvo.code} REQUIRES {pre.code}")


async def conferir(s: AsyncSession) -> int:
    alvo = await no_por_codigo(s, ALVO)
    pre = await no_por_codigo(s, PRE)
    problemas = []
    if alvo is None:
        problemas.append(f"no alvo ausente: {ALVO}")
    if pre is None:
        problemas.append(f"no pre-requisito ausente: {PRE}")
    if alvo is not None and pre is not None:
        arco = await s.scalar(select(CatalogNodePrerequisite).where(
            CatalogNodePrerequisite.content_node_id == alvo.id,
            CatalogNodePrerequisite.prerequisite_node_id == pre.id))
        if arco is None:
            problemas.append("arco ausente")
        proveniencia = ((pre.metadata_ or {}).get("curriculum_provenance") or {})
        if proveniencia.get("validation") != "HUMAN_VALIDATED":
            problemas.append("o no nao registra validacao humana da relacao")
    for p in problemas:
        print(f"  FALTA: {p}")
    if not problemas:
        print("  OK: no, arco e proveniencia no lugar")
    return len(problemas)


async def main() -> None:
    so_conferir = "--conferir" in sys.argv
    engine = create_async_engine(url())
    fabrica = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with fabrica() as s:
            if so_conferir:
                raise SystemExit(1 if await conferir(s) else 0)

            alvo = await no_por_codigo(s, ALVO)
            if alvo is None:
                raise SystemExit(
                    f"ABORTADO: {ALVO} nao existe na taxonomia. Este script "
                    "pressupoe que Estequiometria ja esteja modelada.")
            print(f"[no] alvo encontrado: {ALVO} ({alvo.name})")

            pre = await garantir_no_do_pre_requisito(s)
            await garantir_arco(s, alvo, pre)
            await s.commit()
            print("\nconferindo depois de gravar:")
            await conferir(s)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
