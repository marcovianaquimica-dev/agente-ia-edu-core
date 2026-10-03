"""Validacao em PostgreSQL da migration 063 (prompt_materials aceita
anexo em proposta da plataforma, nao so proposta normal de escola).

Ler a migration nao prova nada: um CHECK constraint novo so se comprova
rodando o upgrade de verdade contra linhas reais. Banco descartavel na
porta 5433, mesmo padrao dos outros testes de migration desta suite.
"""

from __future__ import annotations

import os
import unittest
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests._postgres_test_db import create_database, drop_database


class TestPlatformPromptMaterialMigrationPostgreSQL(unittest.TestCase):
    database_name = "agente_ia_edu_platform_prompt_material_test"
    admin_url = os.getenv(
        "PLATFORM_PROMPT_MATERIAL_TEST_ADMIN_DATABASE_URL",
        "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/postgres",
    )
    database_url = os.getenv(
        "PLATFORM_PROMPT_MATERIAL_TEST_DATABASE_URL",
        f"postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            engine = create_engine(
                cls.admin_url,
                connect_args={"autocommit": True},
                execution_options={"isolation_level": "AUTOCOMMIT"},
            )
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            engine.dispose()
        except Exception as exc:
            raise unittest.SkipTest(
                "PostgreSQL de teste indisponivel; migration 063 nao validada."
            ) from exc

    def setUp(self):
        drop_database(self.admin_url, self.database_name)
        create_database(self.admin_url, self.database_name)

    def tearDown(self):
        drop_database(self.admin_url, self.database_name)

    def _alembic_config(self) -> Config:
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", self.database_url)
        return config

    def _seed_catalog(self, connection):
        """024_chemistry_kinetics e uma DATA migration que exige o no de
        catalogo CHEMISTRY-PHYSICAL (AREA) ja existir - mesmo contorno
        minimo ja usado em outros testes de migration desta suite."""
        catalog_spec = [
            ("CHEMISTRY", None, "DISCIPLINE", "Quimica", 1),
            ("CHEMISTRY-PHYSICAL", "CHEMISTRY", "AREA", "Fisico-Quimica", 1),
        ]
        for code, parent_code, node_type, name, position in catalog_spec:
            parent_id = None
            root_id = None
            if parent_code:
                parent_row = connection.execute(
                    text("SELECT id, root_id FROM catalog_nodes WHERE code = :code"),
                    {"code": parent_code},
                ).mappings().first()
                if parent_row:
                    parent_id = parent_row["id"]
                    root_id = parent_row["root_id"] or parent_row["id"]
            existing = connection.execute(
                text("SELECT id FROM catalog_nodes WHERE code = :code"),
                {"code": code},
            ).mappings().first()
            if not existing:
                connection.execute(
                    text(
                        "INSERT INTO catalog_nodes "
                        "(id, parent_id, root_id, node_type, code, name, position, active, created_at, updated_at) "
                        "VALUES (:id, :parent_id, :root_id, :node_type, :code, :name, :position, true, now(), now())"
                    ),
                    {
                        "id": uuid.uuid4(), "parent_id": parent_id,
                        "root_id": root_id or parent_id, "node_type": node_type,
                        "code": code, "name": name, "position": position,
                    },
                )

    def _upgrade_to(self, config: Config, target: str) -> None:
        command.upgrade(config, "023_curriculum_taxonomy")
        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                self._seed_catalog(connection)
        finally:
            engine.dispose()
        command.upgrade(config, target)

    def _seed_school_essay_prompt(self, connection) -> uuid.UUID:
        school_id = uuid.uuid4()
        connection.execute(text(
            "INSERT INTO schools (id, code, name, status, created_at, updated_at) "
            "VALUES (:id, 'SC-063', 'Escola 063', 'ACTIVE', now(), now())"
        ), {"id": school_id})
        prompt_id = uuid.uuid4()
        connection.execute(text(
            "INSERT INTO essay_prompts "
            "(id, school_id, title, statement, year, created_by_external_identity, created_at, updated_at) "
            "VALUES (:id, :school_id, 'Tema', 'Disserte.', 2026, 'prof', now(), now())"
        ), {"id": prompt_id, "school_id": school_id})
        return prompt_id

    def _seed_platform_prompt(self, connection) -> uuid.UUID:
        platform_prompt_id = uuid.uuid4()
        connection.execute(text(
            "INSERT INTO platform_essay_prompts "
            "(id, title, statement, status, created_by_external_identity, created_at) "
            "VALUES (:id, 'Tema plataforma', 'Disserte.', 'ACTIVE', 'user:ADMIN', now())"
        ), {"id": platform_prompt_id})
        return platform_prompt_id

    def test_upgrade_063_platform_essay_prompt_id_opcional_existe(self):
        config = self._alembic_config()
        self._upgrade_to(config, "063_platform_material_target")

        engine = create_engine(self.database_url)
        inspector = inspect(engine)
        columns = {c["name"]: c for c in inspector.get_columns("prompt_materials")}
        self.assertIn("platform_essay_prompt_id", columns)
        self.assertTrue(columns["platform_essay_prompt_id"]["nullable"])
        self.assertTrue(columns["essay_prompt_id"]["nullable"])
        engine.dispose()

    def test_check_constraint_aceita_so_um_dos_dois_nunca_os_dois_ou_nenhum(self):
        config = self._alembic_config()
        self._upgrade_to(config, "063_platform_material_target")

        engine = create_engine(self.database_url)
        with engine.begin() as connection:
            essay_prompt_id = self._seed_school_essay_prompt(connection)
            platform_prompt_id = self._seed_platform_prompt(connection)

            # valido: so essay_prompt_id (proposta normal de escola)
            connection.execute(text(
                "INSERT INTO prompt_materials (id, essay_prompt_id, material_type, content, position) "
                "VALUES (:id, :essay_prompt_id, 'TEXT', 'conteudo', 0)"
            ), {"id": uuid.uuid4(), "essay_prompt_id": essay_prompt_id})

            # valido: so platform_essay_prompt_id
            connection.execute(text(
                "INSERT INTO prompt_materials (id, platform_essay_prompt_id, material_type, content, position) "
                "VALUES (:id, :platform_prompt_id, 'TEXT', 'conteudo', 0)"
            ), {"id": uuid.uuid4(), "platform_prompt_id": platform_prompt_id})

            # invalido: nenhum dos dois
            with self.assertRaises(IntegrityError):
                connection.execute(text(
                    "INSERT INTO prompt_materials (id, material_type, content, position) "
                    "VALUES (:id, 'TEXT', 'conteudo', 0)"
                ), {"id": uuid.uuid4()})
        engine.dispose()

    def test_check_constraint_recusa_os_dois_juntos(self):
        config = self._alembic_config()
        self._upgrade_to(config, "063_platform_material_target")

        engine = create_engine(self.database_url)
        with engine.begin() as connection:
            essay_prompt_id = self._seed_school_essay_prompt(connection)
            platform_prompt_id = self._seed_platform_prompt(connection)
            with self.assertRaises(IntegrityError):
                connection.execute(text(
                    "INSERT INTO prompt_materials "
                    "(id, essay_prompt_id, platform_essay_prompt_id, material_type, content, position) "
                    "VALUES (:id, :essay_prompt_id, :platform_prompt_id, 'TEXT', 'conteudo', 0)"
                ), {
                    "id": uuid.uuid4(), "essay_prompt_id": essay_prompt_id,
                    "platform_prompt_id": platform_prompt_id,
                })
        engine.dispose()

    def test_position_unique_por_proposta_da_plataforma_tambem(self):
        """A unique de posicao que ja existia pra essay_prompt_id precisa de
        uma equivalente pra platform_essay_prompt_id - sem isso, 2 materiais
        da MESMA proposta da plataforma poderiam ficar os 2 na posicao 0."""
        config = self._alembic_config()
        self._upgrade_to(config, "063_platform_material_target")

        engine = create_engine(self.database_url)
        with engine.begin() as connection:
            platform_prompt_id = self._seed_platform_prompt(connection)
            connection.execute(text(
                "INSERT INTO prompt_materials (id, platform_essay_prompt_id, material_type, content, position) "
                "VALUES (:id, :platform_prompt_id, 'TEXT', 'a', 0)"
            ), {"id": uuid.uuid4(), "platform_prompt_id": platform_prompt_id})
            with self.assertRaises(IntegrityError):
                connection.execute(text(
                    "INSERT INTO prompt_materials (id, platform_essay_prompt_id, material_type, content, position) "
                    "VALUES (:id, :platform_prompt_id, 'TEXT', 'b', 0)"
                ), {"id": uuid.uuid4(), "platform_prompt_id": platform_prompt_id})
        engine.dispose()

    def test_downgrade_063_remove_platform_essay_prompt_id(self):
        config = self._alembic_config()
        self._upgrade_to(config, "063_platform_material_target")
        command.downgrade(config, "062_prompt_assignment_target")

        engine = create_engine(self.database_url)
        inspector = inspect(engine)
        columns = {c["name"]: c for c in inspector.get_columns("prompt_materials")}
        self.assertNotIn("platform_essay_prompt_id", columns)
        self.assertFalse(columns["essay_prompt_id"]["nullable"])
        engine.dispose()


if __name__ == "__main__":
    unittest.main()
