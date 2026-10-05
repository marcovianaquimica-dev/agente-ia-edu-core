"""Publica o material de Balanceamento que o Assessor Pedagogico usa para ENSINAR.

O CONTEUDO NAO MORA AQUI
=========================
Ele mora em `services/conteudo_balanceamento.py`, onde entra na suite e tem
cada equacao conferida por contagem de atomos. Este script so o coloca nas
tabelas da PHASE 23 - TheoryMaterial / Version / Section / Block - que e o
que `GET /student/materials` ja serve.

IDEMPOTENTE
============
Rodar duas vezes nao cria dois materiais nem duplica secoes: o material e
encontrado pelo titulo + conteudo, e a versao PUBLISHED e reconstruida. Uma
versao ja publicada normalmente nao se altera em silencio (invariante da
PHASE 23) - aqui isso e aceitavel porque este e o conteudo-piloto do proprio
Nucleo, versionado no codigo, e o script e explicito sobre o que faz.

    .venv/bin/python scripts/publicar_material_balanceamento.py
    .venv/bin/python scripts/publicar_material_balanceamento.py --conferir
"""

from __future__ import annotations

import argparse
import asyncio
import os
import pathlib
import sys
from datetime import datetime, timezone

_RAIZ = pathlib.Path(__file__).resolve().parent.parent
if str(_RAIZ / "src") not in sys.path:
    sys.path.insert(0, str(_RAIZ / "src"))

from sqlalchemy import delete, select  # noqa: E402
from sqlalchemy.ext.asyncio import (  # noqa: E402
    AsyncSession, async_sessionmaker, create_async_engine,
)

from agente_ia_edu.db.models import CatalogNode  # noqa: E402
from agente_ia_edu.db.models.catalog import (  # noqa: E402
    MaterialBlock, MaterialSection, TheoryMaterial, TheoryMaterialVersion,
)
from agente_ia_edu.services.conteudo_balanceamento import (  # noqa: E402
    CONTENT_CODE, MATERIAL, SECOES, equacoes_citadas,
)

AUTOR = "nucleo_edu_360"


def _agora():
    return datetime.now(timezone.utc)


async def publicar(factory) -> dict:
    async with factory() as s:
        no = (await s.scalars(
            select(CatalogNode).where(CatalogNode.code == CONTENT_CODE))).first()
        if no is None:
            raise SystemExit(
                f"conteudo {CONTENT_CODE} nao existe no catalogo - "
                f"o material precisa apontar para um no real")

        material = (await s.scalars(
            select(TheoryMaterial).where(
                TheoryMaterial.title == MATERIAL["title"],
                TheoryMaterial.primary_content_node_id == no.id))).first()
        criou = material is None
        if material is None:
            material = TheoryMaterial(
                title=MATERIAL["title"], description=MATERIAL["description"],
                material_kind=MATERIAL["material_kind"],
                authoring_source="PLATFORM",
                # PUBLIC: este conteudo e do Nucleo, nao de uma escola.
                visibility_scope="PUBLIC",
                primary_content_node_id=no.id,
                created_by_external_identity=AUTOR)
            s.add(material)
            await s.flush()

        versao = (await s.scalars(
            select(TheoryMaterialVersion).where(
                TheoryMaterialVersion.material_id == material.id,
                TheoryMaterialVersion.status == "PUBLISHED"))).first()
        if versao is None:
            versao = TheoryMaterialVersion(
                material_id=material.id, version_number=1, status="PUBLISHED",
                introduction=MATERIAL["introduction"], summary=MATERIAL["summary"],
                created_by_external_identity=AUTOR, published_at=_agora())
            s.add(versao)
            await s.flush()
        else:
            versao.introduction = MATERIAL["introduction"]
            versao.summary = MATERIAL["summary"]
            # Blocos antes das secoes: ha FK de bloco para secao.
            await s.execute(delete(MaterialBlock).where(
                MaterialBlock.material_version_id == versao.id))
            await s.execute(delete(MaterialSection).where(
                MaterialSection.material_version_id == versao.id))
            await s.flush()

        for secao in SECOES:
            linha = MaterialSection(
                material_version_id=versao.id,
                section_type=secao["section_type"], position=secao["position"],
                title=secao["title"], body=secao.get("body"),
                content_node_id=no.id, curriculum_relation_type="PRIMARY")
            s.add(linha)
            await s.flush()
            for bloco in secao["blocks"]:
                s.add(MaterialBlock(
                    section_id=linha.id, material_version_id=versao.id,
                    block_type=bloco["block_type"], position=bloco["position"],
                    title=bloco.get("title"), body=bloco.get("body"),
                    metadata_=bloco.get("metadata")))
        await s.commit()
        return {"material_id": str(material.id), "version_id": str(versao.id),
                "criou": criou,
                "secoes": len(SECOES),
                "blocos": sum(len(x["blocks"]) for x in SECOES)}


def conferir() -> None:
    """A quimica, antes de qualquer escrita no banco."""
    from agente_ia_edu.services.chemistry_balance import equacao_balanceada
    print("--- EQUACOES DO CONTEUDO ---")
    for eq, esperado in equacoes_citadas():
        real = equacao_balanceada(eq)
        marca = "ok " if real == esperado else "ERRO"
        print(f"  [{marca}] {eq:28} declarada={esperado} contagem={real}")
        if real != esperado:
            raise SystemExit("conteudo quimicamente incorreto - nada publicado")


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--conferir", action="store_true",
                   help="so confere a quimica, nao escreve nada")
    args = p.parse_args()

    conferir()
    if args.conferir:
        return

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL nao definida")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        r = await publicar(factory)
    finally:
        await engine.dispose()
    print("\n--- MATERIAL PUBLICADO ---")
    print(f"  material   {r['material_id']} ({'novo' if r['criou'] else 'atualizado'})")
    print(f"  versao     {r['version_id']}")
    print(f"  secoes     {r['secoes']}")
    print(f"  blocos     {r['blocos']}")


if __name__ == "__main__":
    asyncio.run(main())
