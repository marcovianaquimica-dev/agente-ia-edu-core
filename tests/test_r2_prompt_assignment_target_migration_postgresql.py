"""R2 - migration 062: prompt_assignments.student_id + prompt_assignment_logs,
contra Postgres descartavel de verdade (porta 5433), mesmo padrao de
tests/test_r4_essay_batch_scope_migration_postgresql.py."""

from __future__ import annotations

import os
import unittest
import uuid

from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError

from tests._postgres_test_db import create_database, drop_database


class TestPromptAssignmentTargetMigrationPostgreSQL(unittest.TestCase):
    database_name = "agente_ia_edu_prompt_assignment_target_test"
    admin_url = os.getenv(
        "PROMPT_ASSIGNMENT_TARGET_TEST_ADMIN_DATABASE_URL",
        "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/postgres",
    )
    database_url = os.getenv(
        "PROMPT_ASSIGNMENT_TARGET_TEST_DATABASE_URL",
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
                "PostgreSQL de teste indisponivel; migration 062 nao validada."
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
        """024_chemistry_kinetics e uma DATA migration: exige que o no de
        catalogo CHEMISTRY-PHYSICAL (AREA) ja exista - mesmo contorno minimo
        de test_r4_essay_batch_scope_migration_postgresql.py."""
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
        """Sobe ate 023_curriculum_taxonomy, semeia o catalogo minimo que a
        DATA migration 024 exige, e so entao continua ate target."""
        command.upgrade(config, "023_curriculum_taxonomy")
        engine = create_engine(self.database_url)
        try:
            with engine.begin() as connection:
                self._seed_catalog(connection)
        finally:
            engine.dispose()
        command.upgrade(config, target)

    def _seed_through_061(self, connection) -> dict:
        """Cadeia minima de linhas-pai (school, segment, grade_level, year,
        class, essay_prompt, person, student) pra poder inserir um
        prompt_assignments de verdade depois do upgrade."""
        ids = {name: uuid.uuid4() for name in (
            "school", "segment", "grade_level", "year", "class", "prompt",
            "person", "student",
        )}
        connection.execute(text(
            "INSERT INTO schools (id, code, name, status, created_at, updated_at) "
            "VALUES (:id, 'SC-062', 'Escola 062', 'ACTIVE', now(), now())"
        ), {"id": ids["school"]})
        connection.execute(text(
            "INSERT INTO segments (id, school_id, name, external_id) "
            "VALUES (:id, :school_id, 'Medio', 'SEG-062')"
        ), {"id": ids["segment"], "school_id": ids["school"]})
        connection.execute(text(
            "INSERT INTO grade_levels (id, school_id, segment_id, name, external_id, ordinal) "
            "VALUES (:id, :school_id, :segment_id, '3a Serie', 'GRADE-062', 0)"
        ), {"id": ids["grade_level"], "school_id": ids["school"], "segment_id": ids["segment"]})
        connection.execute(text(
            "INSERT INTO academic_years (id, school_id, year, external_id, created_at) "
            "VALUES (:id, :school_id, 2026, 'YEAR-062', now())"
        ), {"id": ids["year"], "school_id": ids["school"]})
        connection.execute(text(
            "INSERT INTO classes "
            "(id, school_id, academic_year_id, grade_level_id, name, external_id, created_at) "
            "VALUES (:id, :school_id, :year_id, :grade_level_id, '3A', 'TURMA-062', now())"
        ), {
            "id": ids["class"], "school_id": ids["school"],
            "year_id": ids["year"], "grade_level_id": ids["grade_level"],
        })
        connection.execute(text(
            "INSERT INTO essay_prompts "
            "(id, school_id, title, statement, year, created_by_external_identity, created_at, updated_at) "
            "VALUES (:id, :school_id, 'Tema', 'Disserte.', 2026, 'prof', now(), now())"
        ), {"id": ids["prompt"], "school_id": ids["school"]})
        connection.execute(text(
            "INSERT INTO persons (id, school_id, full_name, created_at, updated_at) VALUES (:id, :school_id, 'Aluno', now(), now())"
        ), {"id": ids["person"], "school_id": ids["school"]})
        connection.execute(text(
            "INSERT INTO students (id, school_id, person_id, created_at) VALUES (:id, :school_id, :person_id, now())"
        ), {"id": ids["student"], "school_id": ids["school"], "person_id": ids["person"]})
        return ids

    def test_upgrade_062_student_id_opcional_e_tabela_de_log_existe(self):
        config = self._alembic_config()
        self._upgrade_to(config, "062_prompt_assignment_target")

        engine = create_engine(self.database_url)
        inspector = inspect(engine)
        columns = {c["name"]: c for c in inspector.get_columns("prompt_assignments")}
        self.assertIn("student_id", columns)
        self.assertTrue(columns["student_id"]["nullable"])
        self.assertTrue(columns["class_id"]["nullable"])

        table_names = inspector.get_table_names()
        self.assertIn("prompt_assignment_logs", table_names)
        log_columns = {c["name"] for c in inspector.get_columns("prompt_assignment_logs")}
        self.assertEqual(log_columns, {
            "id", "school_id", "essay_prompt_id", "assigned_by_external_identity",
            "created_at", "target_summary",
        })
        engine.dispose()

    def test_check_constraint_aceita_so_um_dos_dois_nunca_os_dois_ou_nenhum(self):
        config = self._alembic_config()
        self._upgrade_to(config, "062_prompt_assignment_target")

        engine = create_engine(self.database_url)
        with engine.begin() as connection:
            ids = self._seed_through_061(connection)

            # valido: so class_id
            connection.execute(text(
                "INSERT INTO prompt_assignments (id, school_id, essay_prompt_id, class_id, "
                "assigned_by_external_identity, validation_enabled, status, created_at) "
                "VALUES (:id, :school_id, :prompt_id, :class_id, 'prof', true, 'OPEN', now())"
            ), {
                "id": uuid.uuid4(), "school_id": ids["school"],
                "prompt_id": ids["prompt"], "class_id": ids["class"],
            })

            # valido: so student_id
            connection.execute(text(
                "INSERT INTO prompt_assignments (id, school_id, essay_prompt_id, student_id, "
                "assigned_by_external_identity, validation_enabled, status, created_at) "
                "VALUES (:id, :school_id, :prompt_id, :student_id, 'prof', true, 'OPEN', now())"
            ), {
                "id": uuid.uuid4(), "school_id": ids["school"],
                "prompt_id": ids["prompt"], "student_id": ids["student"],
            })

            # invalido: nenhum dos dois
            with self.assertRaises(IntegrityError):
                connection.execute(text(
                    "INSERT INTO prompt_assignments (id, school_id, essay_prompt_id, "
                    "assigned_by_external_identity, validation_enabled, status, created_at) "
                    "VALUES (:id, :school_id, :prompt_id, 'prof', true, 'OPEN', now())"
                ), {"id": uuid.uuid4(), "school_id": ids["school"], "prompt_id": ids["prompt"]})
        engine.dispose()

    def test_check_constraint_recusa_os_dois_juntos(self):
        config = self._alembic_config()
        self._upgrade_to(config, "062_prompt_assignment_target")

        engine = create_engine(self.database_url)
        with engine.begin() as connection:
            ids = self._seed_through_061(connection)
            with self.assertRaises(IntegrityError):
                connection.execute(text(
                    "INSERT INTO prompt_assignments (id, school_id, essay_prompt_id, class_id, "
                    "student_id, assigned_by_external_identity, validation_enabled, status, created_at) "
                    "VALUES (:id, :school_id, :prompt_id, :class_id, :student_id, 'prof', true, 'OPEN', now())"
                ), {
                    "id": uuid.uuid4(), "school_id": ids["school"], "prompt_id": ids["prompt"],
                    "class_id": ids["class"], "student_id": ids["student"],
                })
        engine.dispose()

    def test_partial_unique_impede_o_mesmo_aluno_atribuido_2x_a_mesma_proposta(self):
        config = self._alembic_config()
        self._upgrade_to(config, "062_prompt_assignment_target")

        engine = create_engine(self.database_url)
        with engine.begin() as connection:
            ids = self._seed_through_061(connection)
            connection.execute(text(
                "INSERT INTO prompt_assignments (id, school_id, essay_prompt_id, student_id, "
                "assigned_by_external_identity, validation_enabled, status, created_at) "
                "VALUES (:id, :school_id, :prompt_id, :student_id, 'prof', true, 'OPEN', now())"
            ), {
                "id": uuid.uuid4(), "school_id": ids["school"],
                "prompt_id": ids["prompt"], "student_id": ids["student"],
            })
            with self.assertRaises(IntegrityError):
                connection.execute(text(
                    "INSERT INTO prompt_assignments (id, school_id, essay_prompt_id, student_id, "
                    "assigned_by_external_identity, validation_enabled, status, created_at) "
                    "VALUES (:id, :school_id, :prompt_id, :student_id, 'prof', true, 'OPEN', now())"
                ), {
                    "id": uuid.uuid4(), "school_id": ids["school"],
                    "prompt_id": ids["prompt"], "student_id": ids["student"],
                })
        engine.dispose()

    def test_downgrade_062_remove_student_id_e_prompt_assignment_logs(self):
        config = self._alembic_config()
        self._upgrade_to(config, "062_prompt_assignment_target")
        command.downgrade(config, "061_essay_batch_scope")

        engine = create_engine(self.database_url)
        inspector = inspect(engine)
        columns = {c["name"]: c for c in inspector.get_columns("prompt_assignments")}
        self.assertNotIn("student_id", columns)
        self.assertFalse(columns["class_id"]["nullable"])
        self.assertNotIn("prompt_assignment_logs", inspector.get_table_names())
        engine.dispose()
