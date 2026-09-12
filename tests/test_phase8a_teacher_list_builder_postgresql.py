"""PostgreSQL proof for Phase 8A migration and teacher list builder HTTP flow."""

import asyncio
import os
import unittest

from alembic import command
from alembic.config import Config
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.api.routes.teacher_materials import router as teacher_materials_router
from agente_ia_edu.api.routes.catalog import catalog_router
from agente_ia_edu.db.models import School, UserSchoolLink
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext
from test_phase8a_teacher_list_builder_http import Phase8ATeacherListBuilderHTTP


class Phase8ATeacherListBuilderPostgreSQL(unittest.TestCase):
    database_name = "agente_ia_edu_phase8a_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "PHASE8A_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    database_url = os.getenv(
        "PHASE8A_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin_execute("SELECT 1")
        except Exception as exc:
            raise unittest.SkipTest("PostgreSQL indisponivel para Phase 8A") from exc

    def setUp(self):
        self._drop_database()
        self._admin_execute(f"CREATE DATABASE {self.database_name}")
        command.upgrade(self._config(), "021_teacher_list_builder")

    def tearDown(self):
        self._drop_database()

    def _config(self):
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", self.database_url)
        return config

    @classmethod
    def _admin_execute(cls, statement):
        engine = create_engine(
            cls.admin_url,
            connect_args={"autocommit": True},
            execution_options={"isolation_level": "AUTOCOMMIT"},
        )
        try:
            with engine.connect() as connection:
                connection.execute(text(statement))
        finally:
            engine.dispose()

    @classmethod
    def _drop_database(cls):
        try:
            cls._admin_execute(
                "SELECT pg_terminate_backend(pid) FROM pg_stat_activity "
                f"WHERE datname = '{cls.database_name}' AND pid <> pg_backend_pid()"
            )
            cls._admin_execute(f"DROP DATABASE IF EXISTS {cls.database_name}")
        except Exception:
            pass

    def test_upgrade_downgrade_reupgrade(self):
        command.downgrade(self._config(), "020_assessment_core_alignment")
        command.upgrade(self._config(), "021_teacher_list_builder")


class Phase8ATeacherListBuilderPostgreSQLE2E(Phase8ATeacherListBuilderHTTP):
    database_name = "agente_ia_edu_phase8a_http_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "PHASE8A_HTTP_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    database_url = os.getenv(
        "PHASE8A_HTTP_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin_execute("SELECT 1")
        except Exception as exc:
            raise unittest.SkipTest("PostgreSQL indisponivel para HTTP Phase 8A") from exc

    def setUp(self):
        self._drop_database()
        self._admin_execute(f"CREATE DATABASE {self.database_name}")
        config = Config("alembic.ini")
        config.set_main_option("sqlalchemy.url", self.database_url)
        command.upgrade(config, "021_teacher_list_builder")

        async def setup():
            engine = create_async_engine(self.database_url)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                school_a = School(code="P8A-PG-A", name="Escola A")
                school_b = School(code="P8A-PG-B", name="Escola B")
                session.add_all([school_a, school_b])
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="teacher-a", school_id=school_a.id, role="TEACHER",
                    scope_type="CLASSROOM", scope_external_id="CLASS-A", active=True,
                    metadata_={"academic_year": "2026", "unit_id": "UNIT-A", "segment": "EM", "grade_level": "3"},
                ))
                await session.commit()
                return engine, factory, school_a.id, school_b.id

        self.engine, self.session_factory, self.school_a, self.school_b = asyncio.run(setup())
        self.context = {"value": AuthenticatedUserContext(
            user_id="teacher-a", external_identity_id="teacher-a", role="TEACHER",
            school_id=self.school_a, scope_type="CLASSROOM", scope_external_id="CLASS-A",
        )}
        self.identity = {"value": ExternalIdentityContext(
            provider="test", external_user_id="teacher-a", roles=("teacher",), institution_id=str(self.school_a),
        )}
        app = FastAPI()
        app.include_router(teacher_materials_router)
        app.include_router(catalog_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_authenticated_context] = lambda: self.context["value"]
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())
        self._drop_database()

    @classmethod
    def _admin_execute(cls, statement):
        return Phase8ATeacherListBuilderPostgreSQL._admin_execute.__func__(cls, statement)

    @classmethod
    def _drop_database(cls):
        return Phase8ATeacherListBuilderPostgreSQL._drop_database.__func__(cls)


if __name__ == "__main__":
    unittest.main()