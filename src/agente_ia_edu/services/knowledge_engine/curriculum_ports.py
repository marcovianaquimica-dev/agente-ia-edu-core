"""O PORT entre a curadoria de curriculo e o Knowledge Pack.

Existe para que o Knowledge Pack NAO consulte ``curriculum_bncc_links``
(spec 22.8). O contrato do Pack declara a forma de ``topic.bncc[]``; este
modulo entrega os dados; a tabela de curadoria e detalhe de implementacao que
nenhum dos dois nomeia. Trocar o armazenamento muda so este arquivo.

DOIS DEFAULTS QUE IMPORTAM:

  * **so ``status='VALIDATED'``** - uma proposta, inclusive de IA, nunca
    aparece num Pack;
  * **lista vazia quando nao ha curadoria**. ``topic.bncc[] = []`` e o
    resultado CORRETO, e preferivel a associacao inferida sem validacao. Nao
    e falha, nao e erro, nao merece aviso.

``BnccReference`` vem de ``bncc_contract.v1`` e nao carrega identificador de
banco algum.
"""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import aliased

from ...bncc_contract.v1 import BNCC_TAXONOMY_CODE, BnccReference
from ...db.models import CurriculumBnccLink, Taxonomy, TaxonomyNode


async def validated_bncc_for_node(
    session: AsyncSession,
    content_node_id: UUID,
    *,
    taxonomy_version: str | None = None,
) -> list[BnccReference]:
    """Referencias BNCC VALIDADAS de um no de curriculo.

    ``taxonomy_version`` filtra por versao; omitido, devolve as de qualquer
    versao curada. O chamador que precisa de uma versao especifica deve
    pedi-la: a identidade normativa e a tripla, e misturar versoes num mesmo
    Pack seria tao errado quanto inventar o vinculo.
    """
    competency = aliased(TaxonomyNode)
    rows = await session.execute(
        select(
            TaxonomyNode.code,
            TaxonomyNode.description,
            competency.code,
            competency.description,
            Taxonomy.code,
            Taxonomy.version,
            CurriculumBnccLink.relation_type,
        )
        .join(TaxonomyNode, TaxonomyNode.id == CurriculumBnccLink.taxonomy_node_id)
        .join(Taxonomy, Taxonomy.id == CurriculumBnccLink.taxonomy_id)
        .outerjoin(competency, competency.id == TaxonomyNode.parent_id)
        .where(
            CurriculumBnccLink.content_node_id == content_node_id,
            # O default que importa: so curadoria validada chega ao Pack.
            CurriculumBnccLink.status == "VALIDATED",
            Taxonomy.code == BNCC_TAXONOMY_CODE,
            *(
                (Taxonomy.version == taxonomy_version,)
                if taxonomy_version is not None
                else ()
            ),
        )
        .order_by(CurriculumBnccLink.relation_type, TaxonomyNode.code)
    )

    references = []
    for (
        skill_code,
        skill_statement,
        competency_code,
        competency_statement,
        code,
        version,
        relation_type,
    ) in rows.all():
        references.append(
            BnccReference(
                code=skill_code,
                statement=skill_statement or "",
                competency_code=competency_code or "",
                competency_statement=competency_statement or "",
                taxonomy_code=code,
                taxonomy_version=version,
                relation_type=relation_type,
            )
        )
    return references
