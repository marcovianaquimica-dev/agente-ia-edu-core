"""CEREBRO - Fase 4: semeadura da BNCC em Taxonomy/TaxonomyNode.

Vive no dominio de CURRICULO, fora de ``services/knowledge_engine/``: mudanca
de taxonomia e mudanca de curriculo, com script e revisao proprios, nunca
efeito colateral de ingestao.

O requisito central aqui (spec 22.4): **ingerir nao e ativar**. Semear uma
versao nova nunca desativa a anterior, e a promocao a vigente e uma operacao
separada e explicita. Inclusive a PRIMEIRA versao nasce inativa - um caso
especial para "a primeira" seria onde o bug moraria.
"""

from __future__ import annotations

import unittest

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.bncc_contract.v1 import BNCC_TAXONOMY_CODE, BNCC_VERSION_EM_2018
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import Taxonomy, TaxonomyNode
from agente_ia_edu.services.bncc_taxonomy_seed import (
    BnccSeedError,
    BnccTaxonomySeedService,
)
from agente_ia_edu.services.knowledge_engine.bncc_extraction import extract_bncc_cnt

_PAGES = [
    "5.3.1. CIENCIAS DA NATUREZA\nCOMPETENCIA ESPECIFICA 1\n"
    "Analisar fenomenos naturais e pro- cessos tecnologicos. Comentario segue.",
    "HABILIDADES\n(EM13CNT101) Analisar transformacoes e con- servacoes.\n"
    "(EM13CNT102) Realizar previsoes e avaliar intervencoes.",
    "COMPETENCIA ESPECIFICA 2\nConstruir interpretacoes sobre a Vida. Comentario.",
    "HABILIDADES\n(EM13CNT201) Analisar modelos cientificos.",
]


def _framework(version: str = BNCC_VERSION_EM_2018, *, mutate: bool = False):
    pages = list(_PAGES)
    if mutate:
        pages[1] = pages[1].replace("Analisar transformacoes", "Reescrever tudo")
    return extract_bncc_cnt(pages, taxonomy_version=version)


class _SeedCase(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=True)

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed(self, framework=None, **kwargs):
        async with self.factory() as session:
            service = BnccTaxonomySeedService(session)
            return await service.seed(framework or _framework(), **kwargs)


class SeedingTests(_SeedCase):
    async def test_the_full_branch_is_created(self):
        result = await self._seed()
        self.assertEqual(result.area_created, 1)
        self.assertEqual(result.competencies_created, 2)
        self.assertEqual(result.skills_created, 3)
        self.assertEqual(result.total_nodes, 6)

    async def test_cnt_is_a_branch_not_an_independent_taxonomy(self):
        """Ajuste 3: LGG, MAT e CHS entram depois como ramos IRMAOS da mesma
        versao, nao como taxonomias separadas."""
        await self._seed()
        async with self.factory() as session:
            taxonomies = list((await session.scalars(select(Taxonomy))).all())
            area = await session.scalar(
                select(TaxonomyNode).where(TaxonomyNode.code == "EM13CNT")
            )
            competencies = list(
                (
                    await session.scalars(
                        select(TaxonomyNode).where(TaxonomyNode.node_type == "competency")
                    )
                ).all()
            )
        self.assertEqual(len(taxonomies), 1)
        self.assertEqual(taxonomies[0].code, BNCC_TAXONOMY_CODE)
        self.assertEqual(taxonomies[0].version, BNCC_VERSION_EM_2018)
        self.assertEqual(area.node_type, "subject")
        self.assertIsNone(area.parent_id)
        for competency in competencies:
            self.assertEqual(competency.parent_id, area.id)

    async def test_skills_hang_from_their_competency(self):
        await self._seed()
        async with self.factory() as session:
            nodes = {
                node.code: node
                for node in (await session.scalars(select(TaxonomyNode))).all()
            }
        self.assertEqual(nodes["EM13CNT101"].parent_id, nodes["CNT-CE1"].id)
        self.assertEqual(nodes["EM13CNT102"].parent_id, nodes["CNT-CE1"].id)
        self.assertEqual(nodes["EM13CNT201"].parent_id, nodes["CNT-CE2"].id)
        self.assertEqual(nodes["EM13CNT101"].node_type, "skill")

    async def test_every_node_records_its_source_page(self):
        """Critério de aceite: nenhum no sem pagina de origem."""
        await self._seed(document_id="doc-1")
        async with self.factory() as session:
            nodes = list((await session.scalars(select(TaxonomyNode))).all())
        self.assertEqual(len(nodes), 6)
        for node in nodes:
            source = (node.metadata_ or {}).get("source") or {}
            self.assertIsInstance(source.get("page"), int, node.code)
            self.assertGreaterEqual(source["page"], 1)
            self.assertEqual(source.get("extractor_version"), "v1")
            self.assertEqual(source.get("document_id"), "doc-1")

    async def test_skill_nodes_record_the_dehyphenation_count(self):
        await self._seed()
        async with self.factory() as session:
            node = await session.scalar(
                select(TaxonomyNode).where(TaxonomyNode.code == "EM13CNT101")
            )
        self.assertEqual((node.metadata_ or {})["source"]["dehyphenations"], 1)
        self.assertIn("conservacoes", node.description)

    async def test_the_normative_triple_is_recorded_on_every_node(self):
        """Ajuste 4: codigo isolado nao e identidade."""
        await self._seed()
        async with self.factory() as session:
            node = await session.scalar(
                select(TaxonomyNode).where(TaxonomyNode.code == "EM13CNT201")
            )
        reference = (node.metadata_ or {})["bncc_ref"]
        self.assertEqual(reference["taxonomy_code"], "bncc")
        self.assertEqual(reference["taxonomy_version"], "EM-2018")
        self.assertEqual(reference["node_code"], "EM13CNT201")
        self.assertEqual(reference["urn"], "bncc:EM-2018:EM13CNT201")


class ActivationTests(_SeedCase):
    """spec 22.4 - ingestao e ativacao curricular sao operacoes separadas."""

    async def test_the_first_version_is_seeded_inactive(self):
        result = await self._seed()
        self.assertFalse(result.active)
        async with self.factory() as session:
            taxonomy = await session.scalar(select(Taxonomy))
        self.assertFalse(taxonomy.active)

    async def test_nothing_is_in_force_until_promoted(self):
        await self._seed()
        async with self.factory() as session:
            self.assertIsNone(
                await BnccTaxonomySeedService(session).current_version()
            )

    async def test_promotion_is_explicit(self):
        await self._seed()
        async with self.factory() as session:
            promotion = await BnccTaxonomySeedService(session).promote(
                BNCC_VERSION_EM_2018, performed_by="user:ADMIN"
            )
        self.assertEqual(promotion.promoted_version, BNCC_VERSION_EM_2018)
        self.assertIsNone(promotion.demoted_version)
        async with self.factory() as session:
            self.assertEqual(
                await BnccTaxonomySeedService(session).current_version(),
                BNCC_VERSION_EM_2018,
            )

    async def test_seeding_a_new_version_does_not_deactivate_the_previous(self):
        """O requisito central do ajuste 5."""
        await self._seed()
        async with self.factory() as session:
            await BnccTaxonomySeedService(session).promote(
                BNCC_VERSION_EM_2018, performed_by="user:ADMIN"
            )
        await self._seed(_framework("EM-2026"))
        async with self.factory() as session:
            service = BnccTaxonomySeedService(session)
            self.assertEqual(await service.current_version(), BNCC_VERSION_EM_2018)
            new_taxonomy = await session.scalar(
                select(Taxonomy).where(Taxonomy.version == "EM-2026")
            )
        self.assertFalse(new_taxonomy.active)

    async def test_promoting_a_new_version_demotes_the_previous(self):
        await self._seed()
        async with self.factory() as session:
            await BnccTaxonomySeedService(session).promote(
                BNCC_VERSION_EM_2018, performed_by="user:ADMIN"
            )
        await self._seed(_framework("EM-2026"))
        async with self.factory() as session:
            promotion = await BnccTaxonomySeedService(session).promote(
                "EM-2026", performed_by="user:ADMIN"
            )
        self.assertEqual(promotion.promoted_version, "EM-2026")
        self.assertEqual(promotion.demoted_version, BNCC_VERSION_EM_2018)
        async with self.factory() as session:
            self.assertEqual(
                await BnccTaxonomySeedService(session).current_version(), "EM-2026"
            )

    async def test_promoting_an_unseeded_version_is_refused(self):
        async with self.factory() as session:
            with self.assertRaises(BnccSeedError) as caught:
                await BnccTaxonomySeedService(session).promote(
                    "EM-2099", performed_by="user:ADMIN"
                )
            self.assertEqual(caught.exception.code, "VERSION_NOT_SEEDED")


class IdempotencyTests(_SeedCase):
    async def test_seeding_the_same_version_twice_creates_nothing(self):
        await self._seed()
        second = await self._seed()
        self.assertEqual(second.total_nodes, 0)
        async with self.factory() as session:
            nodes = await session.scalar(select(func.count()).select_from(TaxonomyNode))
        self.assertEqual(nodes, 6)

    async def test_divergent_text_in_the_same_version_is_a_conflict(self):
        """Mudar o enunciado de uma habilidade DENTRO da mesma versao significa
        que a extracao ou o arquivo mudou - as duas coisas exigem decisao
        humana, nunca um UPDATE silencioso."""
        await self._seed()
        async with self.factory() as session:
            with self.assertRaises(BnccSeedError) as caught:
                await BnccTaxonomySeedService(session).seed(
                    _framework(mutate=True)
                )
        self.assertEqual(caught.exception.code, "TAXONOMY_VERSION_CONFLICT")
        self.assertIn("EM13CNT101", str(caught.exception))

    async def test_a_conflict_writes_nothing(self):
        await self._seed()
        async with self.factory() as session:
            before = await session.scalar(select(func.count()).select_from(TaxonomyNode))
        async with self.factory() as session:
            with self.assertRaises(BnccSeedError):
                await BnccTaxonomySeedService(session).seed(_framework(mutate=True))
        async with self.factory() as session:
            after = await session.scalar(select(func.count()).select_from(TaxonomyNode))
        self.assertEqual(before, after)

    async def test_a_new_version_coexists_with_the_old_one(self):
        await self._seed()
        await self._seed(_framework("EM-2026"))
        async with self.factory() as session:
            taxonomies = {
                taxonomy.version: taxonomy
                for taxonomy in (await session.scalars(select(Taxonomy))).all()
            }
            nodes = await session.scalar(select(func.count()).select_from(TaxonomyNode))
        self.assertEqual(set(taxonomies), {BNCC_VERSION_EM_2018, "EM-2026"})
        self.assertEqual(nodes, 12)

    async def test_the_same_code_can_exist_in_two_versions(self):
        """E por isso que o codigo isolado nao e identidade."""
        await self._seed()
        await self._seed(_framework("EM-2026"))
        async with self.factory() as session:
            same_code = list(
                (
                    await session.scalars(
                        select(TaxonomyNode).where(TaxonomyNode.code == "EM13CNT101")
                    )
                ).all()
            )
        self.assertEqual(len(same_code), 2)
        self.assertNotEqual(same_code[0].taxonomy_id, same_code[1].taxonomy_id)


if __name__ == "__main__":
    unittest.main()
