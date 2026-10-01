"""CEREBRO - Fase 4: curadoria do vinculo curriculo <-> BNCC, e o port.

O requisito estrutural (ajuste 7): **AI_SUGGESTION -> PROPOSED, nunca
VALIDATED automaticamente**. Testado dos dois lados - o service recusa, e o
banco recusa se o service for contornado.

E o default do Pack (ajuste 8): ``topic.bncc[] = []`` sem curadoria validada.
Ausencia e preferivel a associacao inferida.
"""

from __future__ import annotations

import unittest
import uuid

from sqlalchemy import func, select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.bncc_contract.v1 import BNCC_VERSION_EM_2018
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, CurriculumBnccLink, TaxonomyNode
from agente_ia_edu.services.bncc_taxonomy_seed import BnccTaxonomySeedService
from agente_ia_edu.services.curriculum_bncc_links import (
    CurriculumBnccLinkError,
    CurriculumBnccLinkNotFound,
    CurriculumBnccLinkService,
)
from agente_ia_edu.services.knowledge_engine.bncc_extraction import extract_bncc_cnt
from agente_ia_edu.services.knowledge_engine.curriculum_ports import (
    validated_bncc_for_node,
)

_PAGES = [
    "5.3.1. CIENCIAS DA NATUREZA\nCOMPETENCIA ESPECIFICA 1\n"
    "Analisar fenomenos naturais e processos tecnologicos. Comentario.",
    "HABILIDADES\n(EM13CNT101) Analisar e representar as transformacoes e "
    "conservacoes em sistemas que envolvam quantidade de materia.",
]

_TEACHER = {"actor": "user:prof_mendes", "actor_type": "TEACHER"}


class _LinkCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=True)

        async with self.factory() as session:
            await BnccTaxonomySeedService(session).seed(
                extract_bncc_cnt(_PAGES, taxonomy_version=BNCC_VERSION_EM_2018)
            )
            discipline = CatalogNode(node_type="DISCIPLINE", name="Quimica", position=1)
            session.add(discipline)
            await session.flush()
            discipline.root_id = discipline.id
            content = CatalogNode(
                parent_id=discipline.id, root_id=discipline.id, node_type="CONTENT",
                code="CHEMISTRY-PHYSICAL-STOICHIOMETRY",
                name="Estequiometria e calculos quimicos", position=1,
            )
            session.add(content)
            await session.flush()
            self.content_node_id = content.id
            skill = await session.scalar(
                select(TaxonomyNode).where(TaxonomyNode.code == "EM13CNT101")
            )
            self.skill_id = skill.id
            self.competency_id = skill.parent_id
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _propose(self, **overrides):
        payload = dict(
            content_node_id=self.content_node_id,
            taxonomy_node_id=self.skill_id,
            relation_type="PRIMARY",
            **_TEACHER,
        )
        payload.update(overrides)
        async with self.factory() as session:
            return await CurriculumBnccLinkService(session).propose(**payload)


class ProposalTests(_LinkCase):
    async def test_a_proposal_starts_proposed(self):
        link = await self._propose()
        self.assertEqual(link.status, "PROPOSED")
        self.assertEqual(link.origin, "MANUAL")
        self.assertIsNone(link.validated_by_external_identity)

    async def test_the_proposal_records_the_normative_triple(self):
        link = await self._propose()
        self.assertEqual(link.taxonomy_version, BNCC_VERSION_EM_2018)
        self.assertEqual(link.node_code, "EM13CNT101")
        self.assertIsNotNone(link.taxonomy_id)

    async def test_an_ai_suggestion_is_also_only_proposed(self):
        """Ajuste 7, do lado do service."""
        link = await self._propose(
            origin="AI_SUGGESTION", confidence=0.93,
            actor="ai:bncc-suggester", actor_type="AI",
        )
        self.assertEqual(link.status, "PROPOSED")
        self.assertEqual(link.origin, "AI_SUGGESTION")
        self.assertEqual(link.confidence, 0.93)

    async def test_an_ai_suggestion_without_confidence_is_refused(self):
        with self.assertRaises(CurriculumBnccLinkError) as caught:
            await self._propose(
                origin="AI_SUGGESTION", actor="ai:x", actor_type="AI"
            )
        self.assertEqual(caught.exception.code, "SUGGESTION_NEEDS_CONFIDENCE")

    async def test_prerequisite_is_not_a_relation_type_in_this_version(self):
        """Ajuste 6: pre-requisito pertence ao grafo curricular."""
        with self.assertRaises(CurriculumBnccLinkError) as caught:
            await self._propose(relation_type="PREREQUISITE")
        self.assertEqual(caught.exception.code, "INVALID_RELATION_TYPE")

    async def test_supporting_is_accepted(self):
        self.assertEqual((await self._propose(relation_type="SUPPORTING")).relation_type,
                         "SUPPORTING")

    async def test_the_target_must_be_a_skill_not_a_competency(self):
        with self.assertRaises(CurriculumBnccLinkError) as caught:
            await self._propose(taxonomy_node_id=self.competency_id)
        self.assertEqual(caught.exception.code, "TARGET_IS_NOT_A_SKILL")

    async def test_an_unknown_content_node_is_refused(self):
        with self.assertRaises(CurriculumBnccLinkNotFound):
            await self._propose(content_node_id=uuid.uuid4())


class ValidationTests(_LinkCase):
    async def test_validation_requires_a_human_actor(self):
        """O coracao do ajuste 7."""
        link = await self._propose(
            origin="AI_SUGGESTION", confidence=0.9, actor="ai:x", actor_type="AI"
        )
        async with self.factory() as session:
            with self.assertRaises(CurriculumBnccLinkError) as caught:
                await CurriculumBnccLinkService(session).validate(
                    link.id, rationale="parece certo",
                    actor="ai:bncc-suggester", actor_type="AI",
                )
            self.assertEqual(caught.exception.code, "VALIDATION_REQUIRES_HUMAN")

    async def test_validation_requires_a_rationale(self):
        link = await self._propose()
        async with self.factory() as session:
            with self.assertRaises(CurriculumBnccLinkError) as caught:
                await CurriculumBnccLinkService(session).validate(
                    link.id, rationale="   ", **_TEACHER
                )
            self.assertEqual(caught.exception.code, "VALIDATION_REQUIRES_RATIONALE")

    async def test_a_human_validates_and_the_identity_is_recorded(self):
        link = await self._propose()
        async with self.factory() as session:
            validated = await CurriculumBnccLinkService(session).validate(
                link.id,
                rationale="A habilidade trata de quantidade de materia, que e o "
                          "nucleo conceitual da estequiometria.",
                **_TEACHER,
            )
        self.assertEqual(validated.status, "VALIDATED")
        self.assertEqual(validated.validated_by_external_identity, "user:prof_mendes")
        self.assertIsNotNone(validated.validated_at)
        self.assertIn("quantidade de materia", validated.rationale)

    async def test_validating_twice_is_refused(self):
        link = await self._propose()
        async with self.factory() as session:
            await CurriculumBnccLinkService(session).validate(
                link.id, rationale="justificativa", **_TEACHER
            )
        async with self.factory() as session:
            with self.assertRaises(CurriculumBnccLinkError) as caught:
                await CurriculumBnccLinkService(session).validate(
                    link.id, rationale="de novo", **_TEACHER
                )
            self.assertEqual(caught.exception.code, "ALREADY_VALIDATED")

    async def test_the_database_refuses_validated_without_identity(self):
        """A trava nao e so do service: um INSERT direto no modelo contorna
        todo este arquivo e o banco ainda recusa."""
        with self.assertRaises(IntegrityError):
            async with self.factory() as session:
                skill = await session.get(TaxonomyNode, self.skill_id)
                session.add(
                    CurriculumBnccLink(
                        content_node_id=self.content_node_id,
                        taxonomy_node_id=self.skill_id,
                        taxonomy_id=skill.taxonomy_id,
                        relation_type="PRIMARY",
                        status="VALIDATED",
                        origin="AI_SUGGESTION",
                        confidence=0.99,
                        rationale="a IA achou",
                        # sem validated_by_external_identity
                    )
                )
                await session.commit()

    async def test_the_database_refuses_validated_without_rationale(self):
        with self.assertRaises(IntegrityError):
            async with self.factory() as session:
                skill = await session.get(TaxonomyNode, self.skill_id)
                session.add(
                    CurriculumBnccLink(
                        content_node_id=self.content_node_id,
                        taxonomy_node_id=self.skill_id,
                        taxonomy_id=skill.taxonomy_id,
                        relation_type="PRIMARY",
                        status="VALIDATED",
                        origin="MANUAL",
                        validated_by_external_identity="user:alguem",
                        # sem rationale
                    )
                )
                await session.commit()


class AuditTrailTests(_LinkCase):
    async def test_the_trail_is_append_only(self):
        link = await self._propose()
        async with self.factory() as session:
            await CurriculumBnccLinkService(session).validate(
                link.id, rationale="justificativa pedagogica", **_TEACHER
            )
        async with self.factory() as session:
            reviews = await CurriculumBnccLinkService(session).reviews_for(link.id)
        self.assertEqual([r["action"] for r in reviews], ["PROPOSE", "VALIDATE"])
        self.assertEqual(reviews[1]["previous_value"], {"status": "PROPOSED"})
        self.assertEqual(reviews[1]["new_value"], {"status": "VALIDATED"})
        self.assertEqual(reviews[1]["actor"], "user:prof_mendes")

    async def test_the_suggester_version_is_recorded_for_ai_proposals(self):
        link = await self._propose(
            origin="AI_SUGGESTION", confidence=0.8, actor="ai:x", actor_type="AI",
            suggester_version="bncc-suggester-v1",
        )
        async with self.factory() as session:
            reviews = await CurriculumBnccLinkService(session).reviews_for(link.id)
        self.assertEqual(reviews[0]["suggester_version"], "bncc-suggester-v1")

    async def test_rejection_is_recorded_and_keeps_the_row(self):
        link = await self._propose()
        async with self.factory() as session:
            rejected = await CurriculumBnccLinkService(session).reject(
                link.id, reason="a habilidade e de outra area", **_TEACHER
            )
        self.assertEqual(rejected.status, "REJECTED")
        async with self.factory() as session:
            total = await session.scalar(
                select(func.count()).select_from(CurriculumBnccLink)
            )
            reviews = await CurriculumBnccLinkService(session).reviews_for(link.id)
        self.assertEqual(total, 1)
        self.assertEqual([r["action"] for r in reviews], ["PROPOSE", "REJECT"])


class PortTests(_LinkCase):
    """spec 22.8 - o Pack ve ``BnccReference``, nunca a tabela."""

    async def test_without_curation_the_list_is_empty(self):
        """Ajuste 8. Nao e falha, nao e erro, nao merece aviso."""
        async with self.factory() as session:
            self.assertEqual(
                await validated_bncc_for_node(session, self.content_node_id), []
            )

    async def test_a_proposed_link_does_not_reach_the_pack(self):
        await self._propose()
        async with self.factory() as session:
            self.assertEqual(
                await validated_bncc_for_node(session, self.content_node_id), []
            )

    async def test_an_ai_proposal_does_not_reach_the_pack(self):
        await self._propose(
            origin="AI_SUGGESTION", confidence=0.99, actor="ai:x", actor_type="AI"
        )
        async with self.factory() as session:
            self.assertEqual(
                await validated_bncc_for_node(session, self.content_node_id), []
            )

    async def test_a_validated_link_reaches_the_pack_with_the_version(self):
        link = await self._propose()
        async with self.factory() as session:
            await CurriculumBnccLinkService(session).validate(
                link.id, rationale="nucleo conceitual compartilhado", **_TEACHER
            )
        async with self.factory() as session:
            references = await validated_bncc_for_node(session, self.content_node_id)
        self.assertEqual(len(references), 1)
        reference = references[0]
        self.assertEqual(reference.code, "EM13CNT101")
        self.assertEqual(reference.taxonomy_version, BNCC_VERSION_EM_2018)
        self.assertEqual(reference.competency_code, "CNT-CE1")
        self.assertEqual(reference.relation_type, "PRIMARY")
        self.assertIn("quantidade de materia", reference.statement)
        self.assertEqual(reference.node_ref.as_urn(), "bncc:EM-2018:EM13CNT101")

    async def test_the_version_filter_works(self):
        link = await self._propose()
        async with self.factory() as session:
            await CurriculumBnccLinkService(session).validate(
                link.id, rationale="justificativa", **_TEACHER
            )
        async with self.factory() as session:
            self.assertEqual(
                len(await validated_bncc_for_node(
                    session, self.content_node_id,
                    taxonomy_version=BNCC_VERSION_EM_2018,
                )),
                1,
            )
            self.assertEqual(
                await validated_bncc_for_node(
                    session, self.content_node_id, taxonomy_version="EM-2099"
                ),
                [],
            )

    async def test_a_rejected_link_does_not_reach_the_pack(self):
        link = await self._propose()
        async with self.factory() as session:
            await CurriculumBnccLinkService(session).reject(
                link.id, reason="nao se aplica", **_TEACHER
            )
        async with self.factory() as session:
            self.assertEqual(
                await validated_bncc_for_node(session, self.content_node_id), []
            )


if __name__ == "__main__":
    unittest.main()
