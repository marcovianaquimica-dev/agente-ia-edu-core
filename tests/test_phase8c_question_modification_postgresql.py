"""PostgreSQL HTTP proof for Phase 8C proposal creation, cancellation, and acceptance."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.api.routes.question_modification_proposals import (
    get_modification_provider,
    router as modification_proposals_router,
)
from agente_ia_edu.api.routes.teacher_materials import router as teacher_materials_router
from test_phase8a_teacher_list_builder_postgresql import Phase8ATeacherListBuilderPostgreSQLE2E
from test_phase8c_question_modification import (
    Phase8CQuestionModificationContract,
    StructuredProposalFake,
)


class Phase8CQuestionModificationPostgreSQLE2E(
    Phase8CQuestionModificationContract,
    Phase8ATeacherListBuilderPostgreSQLE2E,
):
    database_name = "agente_ia_edu_phase8c_http_test"
    database_url = "postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/agente_ia_edu_phase8c_http_test"

    @classmethod
    def setUpClass(cls):
        return Phase8ATeacherListBuilderPostgreSQLE2E.setUpClass.__func__(cls)

    def setUp(self):
        # The base setUp already builds the full schema from Base.metadata, so
        # the extra upgrade to 022 that used to run here is both redundant and
        # wrong: it pinned the tables to a revision the ORM no longer matches.
        Phase8ATeacherListBuilderPostgreSQLE2E.setUp(self)
        self.provider = StructuredProposalFake()
        app = FastAPI()
        app.include_router(teacher_materials_router)
        app.include_router(modification_proposals_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_authenticated_context] = lambda: self.context["value"]
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        app.dependency_overrides[get_modification_provider] = lambda: self.provider
        self.client.close()
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        Phase8ATeacherListBuilderPostgreSQLE2E.tearDown(self)


if __name__ == "__main__":
    import unittest
    unittest.main()