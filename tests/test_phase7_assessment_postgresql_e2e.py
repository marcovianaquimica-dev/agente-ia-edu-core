"""PostgreSQL HTTP proof for the Phase 7 individual assessment flow."""

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
from agente_ia_edu.api.routes.assessments import router as assessments_router
from agente_ia_edu.api.routes.attempts import router as attempts_router
from agente_ia_edu.api.routes.domain_map import domain_map_router
from agente_ia_edu.db.models import School, UserSchoolLink
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext
from agente_ia_edu.services.curriculum_taxonomy import CurriculumTaxonomyService
from test_phase7_assessment_core_http import Phase7AssessmentCoreHTTP


def _migrate_to_head(database_url):
    """Build the schema from the real migration chain, all the way to head.

    A suite pinned to an old Alembic revision proved only that the code still
    ran against a schema no deployment has; ``Base.metadata.create_all``
    proves only what the models claim, never that a migration wrote it.

    The chain stops once on the way up. 024_chemistry_kinetics is a DATA
    migration: it inserts a content node under a REQUIRED, already-existing
    parent (catalog_node code CHEMISTRY-PHYSICAL) and fails loudly if that
    parent is missing - by design, see the migration's own _validate_parent.
    Nothing in this repository creates that node in a migration; it only ever
    comes from CurriculumTaxonomyService.seed_reference_fixture(), and in
    every real environment it was seeded long before 024 was authored. Seed
    it at exactly that point: after the chain reaches 023, which is the last
    revision before 024 reads the catalog, and before it advances past it.
    """
    config = Config("alembic.ini")
    config.set_main_option("sqlalchemy.url", database_url)
    command.upgrade(config, "023_curriculum_taxonomy")
    asyncio.run(_seed_reference_catalog(database_url))
    command.upgrade(config, "head")


async def _seed_reference_catalog(database_url):
    engine = create_async_engine(database_url)
    try:
        factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
        async with factory() as session:
            await CurriculumTaxonomyService(session).seed_reference_fixture()
    finally:
        await engine.dispose()


class Phase7AssessmentPostgreSQLE2E(Phase7AssessmentCoreHTTP):
    database_name = "agente_ia_edu_phase7_http_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "PHASE7_HTTP_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    database_url = os.getenv(
        "PHASE7_HTTP_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    test_teacher_scope_creation_and_version_workflow = None
    test_assignment_and_attempt_authorization = None
    test_attempt_snapshot_and_frozen_answer_key = None

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin_execute("SELECT 1")
        except Exception as exc:
            raise unittest.SkipTest("PostgreSQL indisponivel para HTTP Phase 7") from exc

    def setUp(self):
        self._drop_database()
        self._admin_execute(f"CREATE DATABASE {self.database_name}")
        _migrate_to_head(self.database_url)

        async def setup_database():
            engine = create_async_engine(self.database_url)
            factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            async with factory() as session:
                school_a = School(code="PH7-PG-A", name="Escola Phase 7 PG A")
                school_b = School(code="PH7-PG-B", name="Escola Phase 7 PG B")
                session.add_all([school_a, school_b])
                await session.flush()
                session.add(UserSchoolLink(
                    external_user_id="student-a",
                    school_id=school_a.id,
                    role="STUDENT",
                    scope_type="CLASSROOM",
                    scope_external_id="CLASS-A",
                    active=True,
                    metadata_={
                        "academic_year": "2026",
                        "unit_id": "UNIT-A",
                        "segment": "ENSINO_MEDIO",
                        "grade_level": "3_SERIE",
                        "classroom_id": "CLASS-A",
                    },
                ))
                await session.commit()
                return engine, factory, school_a.id, school_b.id

        self.engine, self.session_factory, self.school_a, self.school_b = asyncio.run(
            setup_database()
        )
        self.context = {"value": AuthenticatedUserContext(
            user_id="teacher-a",
            external_identity_id="teacher-a",
            role="TEACHER",
            school_id=self.school_a,
            scope_type="CLASSROOM",
            scope_external_id="CLASS-A",
        )}
        self.identity = {"value": ExternalIdentityContext(
            provider="test", external_user_id="teacher-a", roles=("teacher",)
        )}
        app = FastAPI()
        app.include_router(assessments_router)
        app.include_router(attempts_router)
        app.include_router(domain_map_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_authenticated_context] = (
            lambda: self.context["value"]
        )
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


if __name__ == "__main__":
    unittest.main()