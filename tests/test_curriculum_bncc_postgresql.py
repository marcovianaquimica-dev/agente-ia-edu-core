"""CEREBRO - Fase 4: o que so o PostgreSQL real prova.

As travas que impedem uma sugestao de IA de se autovalidar sao
CheckConstraints, nao `if` num service. Este arquivo as exercita contornando
o service por completo - INSERT direto no modelo - porque e exatamente nesse
cenario que elas precisam valer.

Mais o indice parcial de unicidade, que o SQLite aceita mas so o Postgres
aplica da forma que producao vera.

Banco DESCARTAVEL proprio. Nao toca o banco de desenvolvimento.
"""

from __future__ import annotations

import os
import unittest
import uuid

from sqlalchemy import create_engine, func, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.bncc_contract.v1 import BNCC_VERSION_EM_2018
from agente_ia_edu.db.base import Base
import agente_ia_edu.db.models  # noqa: F401
from agente_ia_edu.db.models import CatalogNode, CurriculumBnccLink, TaxonomyNode
from agente_ia_edu.services.bncc_taxonomy_seed import BnccTaxonomySeedService
from agente_ia_edu.services.curriculum_bncc_links import CurriculumBnccLinkService
from agente_ia_edu.services.knowledge_engine.bncc_extraction import extract_bncc_cnt
from agente_ia_edu.services.knowledge_engine.curriculum_ports import (
    validated_bncc_for_node,
)

_PAGES = [
    "5.3.1. CIENCIAS DA NATUREZA\nCOMPETENCIA ESPECIFICA 1\n"
    "Analisar fenomenos naturais e processos tecnologicos. Comentario.",
    "HABILIDADES\n(EM13CNT101) Analisar transformacoes e conservacoes em "
    "sistemas que envolvam quantidade de materia.\n"
    "(EM13CNT102) Realizar previsoes e avaliar intervencoes.",
]
_TEACHER = {"actor": "user:prof_mendes", "actor_type": "TEACHER"}


class CurriculumBnccPostgreSQL(unittest.IsolatedAsyncioTestCase):
    database_name = "agente_ia_edu_curriculum_bncc_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres"
    url = f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}"

    @classmethod
    def _admin(cls, statement: str) -> None:
        engine = create_engine(cls.admin_url, isolation_level="AUTOCOMMIT")
        try:
            with engine.connect() as connection:
                connection.execute(text(statement))
        finally:
            engine.dispose()

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin("SELECT 1")
        except Exception as exc:  # pragma: no cover
            raise unittest.SkipTest("PostgreSQL de teste indisponivel") from exc
        cls._admin(f"DROP DATABASE IF EXISTS {cls.database_name}")
        cls._admin(f"CREATE DATABASE {cls.database_name}")
        sync = create_engine(cls.url)
        try:
            Base.metadata.create_all(sync)
        finally:
            sync.dispose()

    @classmethod
    def tearDownClass(cls):
        cls._admin(f"DROP DATABASE IF EXISTS {cls.database_name}")

    async def asyncSetUp(self):
        self.engine = create_async_engine(self.url)
        self.factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=True
        )
        async with self.factory() as session:
            await session.execute(text("DELETE FROM curriculum_bncc_link_reviews"))
            await session.execute(text("DELETE FROM curriculum_bncc_links"))
            await session.execute(text("DELETE FROM taxonomy_nodes"))
            await session.execute(text("DELETE FROM taxonomies"))
            await session.execute(text("DELETE FROM catalog_nodes"))
            await session.commit()

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
                name="Estequiometria", position=1,
            )
            session.add(content)
            await session.flush()
            self.content_node_id = content.id
            skill = await session.scalar(
                select(TaxonomyNode).where(TaxonomyNode.code == "EM13CNT101")
            )
            self.skill_id, self.taxonomy_id = skill.id, skill.taxonomy_id
            other = await session.scalar(
                select(TaxonomyNode).where(TaxonomyNode.code == "EM13CNT102")
            )
            self.other_skill_id = other.id
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    def _link(self, **overrides) -> CurriculumBnccLink:
        payload = dict(
            content_node_id=self.content_node_id,
            taxonomy_node_id=self.skill_id,
            taxonomy_id=self.taxonomy_id,
            relation_type="PRIMARY",
            status="PROPOSED",
            origin="MANUAL",
        )
        payload.update(overrides)
        return CurriculumBnccLink(**payload)

    # -- as travas, com o service contornado -----------------------------

    async def test_validated_without_identity_is_refused_by_the_database(self):
        with self.assertRaises(IntegrityError) as caught:
            async with self.factory() as session:
                session.add(
                    self._link(
                        status="VALIDATED", origin="AI_SUGGESTION",
                        confidence=0.99, rationale="a IA achou",
                    )
                )
                await session.commit()
        self.assertIn(
            "ck_curriculum_bncc_links_validated_needs_identity", str(caught.exception)
        )

    async def test_validated_without_rationale_is_refused_by_the_database(self):
        with self.assertRaises(IntegrityError) as caught:
            async with self.factory() as session:
                session.add(
                    self._link(
                        status="VALIDATED",
                        validated_by_external_identity="user:alguem",
                    )
                )
                await session.commit()
        self.assertIn(
            "ck_curriculum_bncc_links_validated_needs_rationale", str(caught.exception)
        )

    async def test_a_suggestion_without_confidence_is_refused_by_the_database(self):
        with self.assertRaises(IntegrityError) as caught:
            async with self.factory() as session:
                session.add(self._link(origin="AI_SUGGESTION"))
                await session.commit()
        self.assertIn(
            "ck_curriculum_bncc_links_suggestion_needs_confidence",
            str(caught.exception),
        )

    async def test_prerequisite_is_refused_by_the_database(self):
        """Ajuste 6: o tipo nao existe nesta versao, e nao so no service."""
        with self.assertRaises(IntegrityError) as caught:
            async with self.factory() as session:
                session.add(self._link(relation_type="PREREQUISITE"))
                await session.commit()
        self.assertIn("ck_curriculum_bncc_links_relation_type", str(caught.exception))

    async def test_an_ai_actor_cannot_record_a_validate_action(self):
        """Defesa em profundidade: a trilha recusa registrar o que a tabela de
        vinculos ja recusa fazer."""
        async with self.factory() as session:
            link = self._link()
            session.add(link)
            await session.flush()
            # Capturado ANTES do commit: com expire_on_commit=True, ler um
            # atributo do objeto ORM depois do commit levanta MissingGreenlet -
            # a armadilha numero um deste projeto, e este teste a tomou na
            # primeira versao.
            link_id = link.id
            await session.commit()
        with self.assertRaises(IntegrityError) as caught:
            async with self.factory() as session:
                await session.execute(
                    text(
                        "INSERT INTO curriculum_bncc_link_reviews "
                        "(id, link_id, action, actor, actor_type, created_at) "
                        "VALUES (:id, :link, 'VALIDATE', 'ai:x', 'AI', "
                        "CURRENT_TIMESTAMP)"
                    ).bindparams(id=uuid.uuid4(), link=link_id)
                )
                await session.commit()
        self.assertIn(
            "ck_curriculum_bncc_link_reviews_ai_only_proposes", str(caught.exception)
        )

    # -- indice parcial de unicidade -------------------------------------

    async def test_many_proposals_but_one_validated_link_per_pair(self):
        async with self.factory() as session:
            for _ in range(3):
                session.add(self._link())
            await session.commit()
            total = await session.scalar(
                select(func.count()).select_from(CurriculumBnccLink)
            )
        self.assertEqual(total, 3)

        async with self.factory() as session:
            session.add(
                self._link(
                    status="VALIDATED",
                    validated_by_external_identity="user:prof",
                    rationale="justificativa",
                )
            )
            await session.commit()

        with self.assertRaises(IntegrityError) as caught:
            async with self.factory() as session:
                session.add(
                    self._link(
                        status="VALIDATED",
                        validated_by_external_identity="user:outro",
                        rationale="outra justificativa",
                    )
                )
                await session.commit()
        self.assertIn("uq_curriculum_bncc_links_validated", str(caught.exception))

    # -- caminho async completo ------------------------------------------

    async def test_the_full_async_path_never_raises_missing_greenlet(self):
        async with self.factory() as session:
            service = CurriculumBnccLinkService(session)
            proposed = await service.propose(
                content_node_id=self.content_node_id,
                taxonomy_node_id=self.skill_id,
                relation_type="PRIMARY",
                **_TEACHER,
            )
            validated = await service.validate(
                proposed.id,
                rationale="A habilidade trata de quantidade de materia, nucleo "
                          "conceitual da estequiometria.",
                **_TEACHER,
            )
            reviews = await service.reviews_for(proposed.id)
            references = await validated_bncc_for_node(session, self.content_node_id)

        self.assertEqual(validated.status, "VALIDATED")
        self.assertEqual(validated.node_code, "EM13CNT101")
        self.assertEqual([r["action"] for r in reviews], ["PROPOSE", "VALIDATE"])
        self.assertEqual(len(references), 1)
        self.assertEqual(references[0].node_ref.as_urn(), "bncc:EM-2018:EM13CNT101")
        self.assertIsNotNone(validated.validated_at.isoformat())

    async def test_two_different_skills_can_both_be_validated(self):
        """A unicidade e por PAR, nao por no de curriculo."""
        async with self.factory() as session:
            service = CurriculumBnccLinkService(session)
            for skill_id, relation in (
                (self.skill_id, "PRIMARY"),
                (self.other_skill_id, "SUPPORTING"),
            ):
                proposed = await service.propose(
                    content_node_id=self.content_node_id,
                    taxonomy_node_id=skill_id,
                    relation_type=relation,
                    **_TEACHER,
                )
                await service.validate(
                    proposed.id, rationale="justificativa", **_TEACHER
                )
        async with self.factory() as session:
            references = await validated_bncc_for_node(session, self.content_node_id)
        self.assertEqual(len(references), 2)
        self.assertEqual(
            {r.relation_type for r in references}, {"PRIMARY", "SUPPORTING"}
        )


if __name__ == "__main__":
    unittest.main()
