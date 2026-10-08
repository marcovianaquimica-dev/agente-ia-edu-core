"""Publica o material de Estequiometria que o Assessor usa para ENSINAR.

O CONTEUDO NAO MORA AQUI
=========================
Ele mora em `services/conteudo_estequiometria.py`, onde entra na suite e tem
cada conta refeita por `massa_molar`. Este script so o coloca nas tabelas da
PHASE 23 - TheoryMaterial / Version / Section / Block - que e o que
`GET /student/materials` ja serve.

POR QUE ESTE SCRIPT PRECISOU EXISTIR
=====================================
Auditado em 2026-10-06, o unico material de
CHEMISTRY-PHYSICAL-STOICHIOMETRY no banco de desenvolvimento era:

    titulo       "lista teste"
    visibilidade PRIVATE
    versao       DRAFT

Isto e um rascunho de teste, nao conteudo pedagogico. Publica-lo seria
tornar publico conteudo que ninguem auditou - entao ele fica onde esta, e o
Assessor passa a ter material proprio. Era por isso que, depois da sondagem,
a decisao caia direto em PRATICA: `ha_material=False`.

IDEMPOTENTE
============
Rodar duas vezes nao cria dois materiais nem duplica secoes: o material e
encontrado pelo titulo + conteudo, e a versao PUBLISHED e reconstruida. Uma
versao ja publicada normalmente nao se altera em silencio (invariante da
PHASE 23) - aqui isso e aceitavel porque este e conteudo-piloto do proprio
Nucleo, versionado no codigo, e o script e explicito sobre o que faz.

    .venv/bin/python scripts/publicar_material_estequiometria.py --conferir
    .venv/bin/python scripts/publicar_material_estequiometria.py
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
from agente_ia_edu.services.conteudo_estequiometria import (  # noqa: E402
    CONTENT_CODE, MATERIAL, SECOES, conferir,
)

AUTOR = "nucleo_edu_360"


def _agora():
    return datetime.now(timezone.utc)


async def publicar(session: AsyncSession) -> dict:
    """Escreve o material. FAIL CLOSED: a quimica e conferida antes.

    Recebe a sessao (e nao a fabrica) para que o teste possa rodar o script
    de verdade contra um banco em memoria - o que o torna um teste do
    SCRIPT, e nao de uma copia dele.
    """
    problemas = conferir()
    if problemas:
        raise SystemExit("conteudo com conta errada - nada publicado:\n  "
                         + "\n  ".join(problemas))

    s = session
    no = (await s.scalars(
        select(CatalogNode).where(CatalogNode.code == CONTENT_CODE))).first()
    if no is None:
        raise SystemExit(
            f"conteudo {CONTENT_CODE} nao existe no catalogo - o material "
            f"precisa apontar para um no real")

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
            "criou": criou, "secoes": len(SECOES),
            "blocos": sum(len(x["blocks"]) for x in SECOES)}


def mostrar_conferencia() -> None:
    """A quimica, antes de qualquer escrita no banco."""
    from agente_ia_edu.services.conteudo_estequiometria import MICRO_HABILIDADES

    print("--- CONTAS DO CONTEUDO ---")
    for skill, dados in MICRO_HABILIDADES.items():
        print(f"  {skill}")
        for p in dados["exemplo"]["passos"]:
            print(f"    {p['rotulo']:<42} {p['conta']:<24} = {p['resultado']}")
    problemas = conferir()
    if problemas:
        for x in problemas:
            print(f"  [ERRO] {x}")
        raise SystemExit("conteudo quimicamente incorreto - nada publicado")
    print("  todas as contas conferem")


async def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--conferir", action="store_true",
                   help="so confere a quimica, nao escreve nada")
    args = p.parse_args()

    mostrar_conferencia()
    if args.conferir:
        return

    url = os.environ.get("DATABASE_URL")
    if not url:
        raise SystemExit("DATABASE_URL nao definida")
    engine = create_async_engine(url)
    factory = async_sessionmaker(engine, class_=AsyncSession,
                                 expire_on_commit=False)
    try:
        async with factory() as s:
            r = await publicar(s)
    finally:
        await engine.dispose()
    print("\n--- MATERIAL PUBLICADO ---")
    print(f"  material   {r['material_id']} "
          f"({'novo' if r['criou'] else 'atualizado'})")
    print(f"  versao     {r['version_id']}")
    print(f"  secoes     {r['secoes']}")
    print(f"  blocos     {r['blocos']}")


if __name__ == "__main__":
    asyncio.run(main())
