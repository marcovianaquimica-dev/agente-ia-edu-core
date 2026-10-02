"""Validacao em PostgreSQL da migration 061 (escopo turma/serie/escola do
envio em lote).

Ler a migration nao prova nada: um CHECK constraint novo so se comprova
rodando o upgrade de verdade contra linhas reais. Banco descartavel na
porta 5433, mesmo alvo dos outros testes de migration desta suite.
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


class TestEssayBatchScopeMigrationPostgreSQL(unittest.TestCase):
    database_name = "agente_ia_edu_essay_batch_scope_test"
    admin_url = os.getenv(
        "ESSAY_BATCH_SCOPE_TEST_ADMIN_DATABASE_URL",
        "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/postgres",
    )
    database_url = os.getenv(
        "ESSAY_BATCH_SCOPE_TEST_DATABASE_URL",
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
                "PostgreSQL de teste indisponivel; migration 061 nao validada."
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
        """024_chemistry_kinetics e uma DATA migration: ela exige que o no de
        catalogo CHEMISTRY-PHYSICAL (AREA) ja exista, e nada na cadeia de
        migrations cria isso - em producao quem cria e
        CurriculumTaxonomyService.seed_reference_fixture(). Mesmo padrao
        minimo usado em test_r6_mass_correction_migration_postgresql.py pra
        destravar o upgrade num banco descartavel vazio."""
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
                        "id": uuid.uuid4(),
                        "parent_id": parent_id,
                        "root_id": root_id or parent_id,
                        "node_type": node_type,
                        "code": code,
                        "name": name,
                        "position": position,
                    },
                )

    def _upgrade_to(self, config: Config, target: str) -> None:
        """Sobe ate `023_curriculum_taxonomy`, semeia o catalogo minimo que a
        DATA migration 024 exige, e so entao continua ate `target`."""
        command.upgrade(config, "023_curriculum_taxonomy")
        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                self._seed_catalog(connection)
        finally:
            engine.dispose()
        command.upgrade(config, target)

    def _seed_through_060(self, connection) -> dict:
        """Monta a cadeia minima de linhas-pai (school, segment, grade_level,
        academic_year, class, essay_prompt) pra poder inserir um
        essay_batch_uploads de verdade depois do upgrade."""
        ids = {name: uuid.uuid4() for name in (
            "school", "segment", "grade_level", "year", "class", "prompt",
        )}
        connection.execute(text(
            "INSERT INTO schools (id, code, name, status, created_at, updated_at) "
            "VALUES (:id, 'SC-061', 'Escola 061', 'ACTIVE', now(), now())"
        ), {"id": ids["school"]})
        connection.execute(text(
            "INSERT INTO segments (id, school_id, name, external_id) "
            "VALUES (:id, :school_id, 'Medio', 'SEG-061')"
        ), {"id": ids["segment"], "school_id": ids["school"]})
        connection.execute(text(
            "INSERT INTO grade_levels (id, school_id, segment_id, name, external_id, ordinal) "
            "VALUES (:id, :school_id, :segment_id, '3a Serie', 'GRADE-061', 0)"
        ), {"id": ids["grade_level"], "school_id": ids["school"], "segment_id": ids["segment"]})
        connection.execute(text(
            "INSERT INTO academic_years (id, school_id, year, external_id, created_at) "
            "VALUES (:id, :school_id, 2026, 'YEAR-061', now())"
        ), {"id": ids["year"], "school_id": ids["school"]})
        connection.execute(text(
            "INSERT INTO classes "
            "(id, school_id, academic_year_id, grade_level_id, name, external_id, created_at) "
            "VALUES (:id, :school_id, :year_id, :grade_level_id, '3A', 'TURMA-061', now())"
        ), {
            "id": ids["class"], "school_id": ids["school"],
            "year_id": ids["year"], "grade_level_id": ids["grade_level"],
        })
        connection.execute(text(
            "INSERT INTO essay_prompts "
            "(id, school_id, title, statement, year, created_by_external_identity, created_at, updated_at) "
            "VALUES (:id, :school_id, 'Tema', 'Disserte.', 2026, 'prof', now(), now())"
        ), {"id": ids["prompt"], "school_id": ids["school"]})
        return ids

    def test_upgrade_061_class_id_vira_opcional_e_grade_level_id_existe(self):
        config = self._alembic_config()
        self._upgrade_to(config, "061_essay_batch_scope")

        engine = create_engine(self.database_url)
        inspector = inspect(engine)
        columns = {c["name"]: c for c in inspector.get_columns("essay_batch_uploads")}
        self.assertIn("grade_level_id", columns)
        self.assertTrue(columns["class_id"]["nullable"])
        self.assertTrue(columns["grade_level_id"]["nullable"])
        engine.dispose()

    def test_upgrade_061_check_constraint_aceita_so_um_dos_dois_ou_nenhum(self):
        config = self._alembic_config()
        self._upgrade_to(config, "061_essay_batch_scope")

        engine = create_engine(self.database_url)
        with engine.begin() as connection:
            ids = self._seed_through_060(connection)

            # class_id setado, grade_level_id NULL: valido (caso de hoje).
            connection.execute(text(
                "INSERT INTO essay_batch_uploads "
                "(id, school_id, essay_prompt_id, class_id, grade_level_id, "
                " uploaded_by_external_identity, status, total_pages, created_at, updated_at) "
                "VALUES (:id, :school_id, :prompt_id, :class_id, NULL, 'prof', 'PROCESSING', 0, now(), now())"
            ), {
                "id": uuid.uuid4(), "school_id": ids["school"],
                "prompt_id": ids["prompt"], "class_id": ids["class"],
            })

            # grade_level_id setado, class_id NULL: valido (serie inteira).
            connection.execute(text(
                "INSERT INTO essay_batch_uploads "
                "(id, school_id, essay_prompt_id, class_id, grade_level_id, "
                " uploaded_by_external_identity, status, total_pages, created_at, updated_at) "
                "VALUES (:id, :school_id, :prompt_id, NULL, :grade_level_id, 'prof', 'PROCESSING', 0, now(), now())"
            ), {
                "id": uuid.uuid4(), "school_id": ids["school"],
                "prompt_id": ids["prompt"], "grade_level_id": ids["grade_level"],
            })

            # os dois NULL: valido (escola inteira).
            connection.execute(text(
                "INSERT INTO essay_batch_uploads "
                "(id, school_id, essay_prompt_id, class_id, grade_level_id, "
                " uploaded_by_external_identity, status, total_pages, created_at, updated_at) "
                "VALUES (:id, :school_id, :prompt_id, NULL, NULL, 'prof', 'PROCESSING', 0, now(), now())"
            ), {"id": uuid.uuid4(), "school_id": ids["school"], "prompt_id": ids["prompt"]})

            # os dois setados: invalido, deve estourar o CHECK.
            with self.assertRaises(IntegrityError):
                connection.execute(text(
                    "INSERT INTO essay_batch_uploads "
                    "(id, school_id, essay_prompt_id, class_id, grade_level_id, "
                    " uploaded_by_external_identity, status, total_pages, created_at, updated_at) "
                    "VALUES (:id, :school_id, :prompt_id, :class_id, :grade_level_id, 'prof', 'PROCESSING', 0, now(), now())"
                ), {
                    "id": uuid.uuid4(), "school_id": ids["school"], "prompt_id": ids["prompt"],
                    "class_id": ids["class"], "grade_level_id": ids["grade_level"],
                })
        engine.dispose()

    def test_downgrade_061_remove_grade_level_id_e_class_id_volta_not_null(self):
        config = self._alembic_config()
        self._upgrade_to(config, "061_essay_batch_scope")
        command.downgrade(config, "057_essay_batch_upload")

        engine = create_engine(self.database_url)
        inspector = inspect(engine)
        columns = {c["name"]: c for c in inspector.get_columns("essay_batch_uploads")}
        self.assertNotIn("grade_level_id", columns)
        self.assertFalse(columns["class_id"]["nullable"])
        engine.dispose()
