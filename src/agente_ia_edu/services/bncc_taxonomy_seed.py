"""Semeadura da BNCC em ``Taxonomy``/``TaxonomyNode``.

VIVE NO DOMINIO DE CURRICULO, fora de ``services/knowledge_engine/``, e isso
e deliberado (spec 22.1): mudanca de taxonomia e mudanca de curriculo, com
script e revisao proprios, nunca efeito colateral de uma ingestao de
documento. Segue o precedente de ``essay_rubric_seed.py``.

NENHUM MODELO NOVO. As constraints que o versionamento precisa ja existem:

    Taxonomy      UNIQUE(code, version)
    TaxonomyNode  UNIQUE(taxonomy_id, code)

CNT E UM RAMO, nao uma taxonomia independente. A arvore e
``subject(EM13CNT) -> competency(CNT-CE1..3) -> skill(EM13CNT###)``, e LGG,
MAT e CHS entram depois como ramos IRMAOS da mesma versao.

``node_type='subject'`` para a area reusa o vocabulario que o CheckConstraint
de ``TaxonomyNode`` ja admite (``competency|skill|subject|subsubject``) -
acrescentar um valor exigiria migracao para nenhum ganho.

INGERIR NAO E ATIVAR (spec 22.4). ``seed`` cria a versao SEMPRE inativa e
nunca toca nenhuma outra; ``promote`` e a operacao separada que define a
vigente. Inclusive a primeira versao nasce inativa: um caso especial para "a
primeira" seria onde o bug moraria.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..bncc_contract.v1 import BNCC_TAXONOMY_CODE, BnccNodeRef
from ..db.models import Taxonomy, TaxonomyNode
from .knowledge_engine.bncc_extraction import BnccFramework

BNCC_TAXONOMY_NAME = "Base Nacional Comum Curricular"


class BnccSeedError(ValueError):
    """A semeadura nao pode prosseguir. ``code`` e estavel."""

    def __init__(self, code: str, message: str) -> None:
        super().__init__(message)
        self.code = code


@dataclass(frozen=True)
class SeedResult:
    taxonomy_id: Any
    taxonomy_code: str
    taxonomy_version: str
    active: bool
    area_created: int
    competencies_created: int
    skills_created: int
    total_nodes: int
    already_present: bool


@dataclass(frozen=True)
class PromotionResult:
    promoted_version: str
    demoted_version: str | None
    performed_by: str
    performed_at: datetime


class BnccTaxonomySeedService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def seed(
        self, framework: BnccFramework, *, document_id: str | None = None
    ) -> SeedResult:
        """Semeia uma versao da BNCC. NUNCA ativa, NUNCA desativa."""
        existing = await self._taxonomy(framework.taxonomy_version)
        if existing is not None:
            await self._assert_no_conflict(existing, framework)
            return SeedResult(
                taxonomy_id=existing.id,
                taxonomy_code=existing.code,
                taxonomy_version=existing.version,
                active=existing.active,
                area_created=0,
                competencies_created=0,
                skills_created=0,
                total_nodes=0,
                already_present=True,
            )

        taxonomy = Taxonomy(
            code=framework.taxonomy_code,
            name=BNCC_TAXONOMY_NAME,
            version=framework.taxonomy_version,
            description=(
                "Base Nacional Comum Curricular - Ensino Medio. Ramos por area "
                "de conhecimento; esta versao contem apenas Ciencias da Natureza "
                "e suas Tecnologias."
            ),
            # SEMPRE inativa. Promover e explicito - ver o docstring do modulo.
            active=False,
            metadata_={
                "extractor_version": framework.extractor_version,
                "seeded_areas": [framework.area_code],
            },
        )
        self.session.add(taxonomy)
        await self.session.flush()

        area = TaxonomyNode(
            taxonomy_id=taxonomy.id,
            parent_id=None,
            code=framework.area_code,
            name=framework.area_name,
            node_type="subject",
            description=None,
            metadata_=_node_metadata(
                framework, framework.area_code, framework.area_page, document_id, 0
            ),
        )
        self.session.add(area)
        await self.session.flush()

        competencies_created = 0
        skills_created = 0
        for competency in framework.competencies:
            competency_node = TaxonomyNode(
                taxonomy_id=taxonomy.id,
                parent_id=area.id,
                code=competency.code,
                name=f"Competência específica {competency.number}",
                node_type="competency",
                description=competency.statement,
                metadata_=_node_metadata(
                    framework,
                    competency.code,
                    competency.page,
                    document_id,
                    competency.dehyphenations,
                ),
            )
            self.session.add(competency_node)
            await self.session.flush()
            competencies_created += 1

            for skill in competency.skills:
                self.session.add(
                    TaxonomyNode(
                        taxonomy_id=taxonomy.id,
                        parent_id=competency_node.id,
                        code=skill.code,
                        name=skill.code,
                        node_type="skill",
                        description=skill.statement,
                        metadata_=_node_metadata(
                            framework,
                            skill.code,
                            skill.page,
                            document_id,
                            skill.dehyphenations,
                            source_text_sha256=skill.source_text_sha256,
                        ),
                    )
                )
                skills_created += 1

        await self.session.flush()
        result = SeedResult(
            taxonomy_id=taxonomy.id,
            taxonomy_code=taxonomy.code,
            taxonomy_version=taxonomy.version,
            active=False,
            area_created=1,
            competencies_created=competencies_created,
            skills_created=skills_created,
            total_nodes=1 + competencies_created + skills_created,
            already_present=False,
        )
        await self.session.commit()
        return result

    async def promote(self, version: str, *, performed_by: str) -> PromotionResult:
        """Define qual versao da BNCC e a VIGENTE.

        Separada da semeadura de proposito: permite processar, comparar e
        curar uma versao nova antes de promove-la.
        """
        target = await self._taxonomy(version)
        if target is None:
            raise BnccSeedError(
                "VERSION_NOT_SEEDED",
                f"a versao {version!r} da BNCC nao foi semeada; "
                "semeie antes de promover",
            )

        demoted: str | None = None
        others = await self.session.scalars(
            select(Taxonomy).where(
                Taxonomy.code == BNCC_TAXONOMY_CODE,
                Taxonomy.version != version,
                Taxonomy.active.is_(True),
            )
        )
        for taxonomy in others.all():
            taxonomy.active = False
            demoted = taxonomy.version

        target.active = True
        performed_at = datetime.now(timezone.utc)
        target.metadata_ = {
            **(target.metadata_ or {}),
            "promotion": {
                "performed_by": performed_by,
                "performed_at": performed_at.isoformat(),
                "demoted_version": demoted,
            },
        }
        await self.session.flush()
        result = PromotionResult(
            promoted_version=version,
            demoted_version=demoted,
            performed_by=performed_by,
            performed_at=performed_at,
        )
        await self.session.commit()
        return result

    async def current_version(self) -> str | None:
        """A versao VIGENTE, ou ``None`` quando nenhuma foi promovida."""
        return await self.session.scalar(
            select(Taxonomy.version).where(
                Taxonomy.code == BNCC_TAXONOMY_CODE, Taxonomy.active.is_(True)
            )
        )

    # -- internos --------------------------------------------------------

    async def _taxonomy(self, version: str) -> Taxonomy | None:
        return await self.session.scalar(
            select(Taxonomy).where(
                Taxonomy.code == BNCC_TAXONOMY_CODE, Taxonomy.version == version
            )
        )

    async def _assert_no_conflict(
        self, taxonomy: Taxonomy, framework: BnccFramework
    ) -> None:
        """Reingestao da MESMA versao com texto diferente e conflito.

        Nao e um UPDATE: mudar o enunciado de uma habilidade dentro da mesma
        versao significa que a extracao ou o arquivo mudou, e as duas coisas
        exigem decisao humana. Sobrescrever em silencio faria uma curadoria
        feita sobre o texto antigo parecer valida sobre o novo.
        """
        stored = {
            node.code: (node.description or "")
            for node in (
                await self.session.scalars(
                    select(TaxonomyNode).where(TaxonomyNode.taxonomy_id == taxonomy.id)
                )
            ).all()
        }
        incoming: dict[str, str] = {framework.area_code: ""}
        for competency in framework.competencies:
            incoming[competency.code] = competency.statement
            for skill in competency.skills:
                incoming[skill.code] = skill.statement

        differences = []
        for code in sorted(set(stored) | set(incoming)):
            if code not in stored:
                differences.append(f"{code}: ausente no banco")
            elif code not in incoming:
                differences.append(f"{code}: ausente na extracao")
            elif stored[code] != incoming[code]:
                differences.append(f"{code}: enunciado divergente")

        if differences:
            raise BnccSeedError(
                "TAXONOMY_VERSION_CONFLICT",
                f"a versao {taxonomy.version!r} da BNCC ja existe com conteudo "
                f"diferente; nada foi escrito. Divergencias: "
                f"{'; '.join(differences[:10])}",
            )


def _node_metadata(
    framework: BnccFramework,
    node_code: str,
    page: int,
    document_id: str | None,
    dehyphenations: int,
    *,
    source_text_sha256: str | None = None,
) -> dict[str, Any]:
    """Metadados de UM no: de onde veio, e qual e a sua identidade normativa.

    ``bncc_ref`` guarda a TRIPLA completa (spec 22.3). Gravar so o codigo
    faria uma curadoria feita sob a BNCC de 2018 parecer valida sob uma BNCC
    futura sem que ninguem a tenha revisado.
    """
    reference = BnccNodeRef(
        taxonomy_code=framework.taxonomy_code,
        taxonomy_version=framework.taxonomy_version,
        node_code=node_code,
    )
    return {
        "source": {
            "document_id": document_id,
            "page": page,
            "extractor_version": framework.extractor_version,
            "dehyphenations": dehyphenations,
            "source_text_sha256": source_text_sha256,
        },
        "bncc_ref": {
            "taxonomy_code": reference.taxonomy_code,
            "taxonomy_version": reference.taxonomy_version,
            "node_code": reference.node_code,
            "urn": reference.as_urn(),
        },
    }
