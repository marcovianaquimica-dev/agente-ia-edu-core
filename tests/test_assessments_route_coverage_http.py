"""
HTTP-level route-handler coverage for src/agente_ia_edu/api/routes/assessments.py.

Wave 2 of the overnight HTTP-layer coverage campaign. This targets the branches
of the route HANDLER itself (auth/authz checks, 404/409/422 error mapping,
response construction) that are exercised only indirectly (or not at all) by
the existing service-layer and happy-path tests.

Special focus: create_publication (assessments.py ~267-296) was flagged by an
earlier audit as a suspected "commit-before-validating-the-response" bug -
i.e. `await session.commit()` happens before `_publication_response()` is
built, so if that response construction throws, a row could already be
committed with no rollback. This file verifies that hypothesis with a real
HTTP-level test before concluding anything.
"""

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.api.routes.assessments import router as assessments_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AnswerKeyEntry,
    AnswerKeyRevision,
    AssessmentPublication,
    AssessmentVersion,
    BookletQuestion,
    CatalogNode,
    ContentQuestionLink,
    Exam,
    ExamApplication,
    ExamBooklet,
    Institution,
    Question,
    QuestionOption,
    QuestionVersion,
    School,
    SourceDocument,
    Taxonomy,
    TaxonomyNode,
    UserSchoolLink,
)
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext


class AssessmentsRouteCoverageHTTP(unittest.TestCase):
    def setUp(self):
        async def setup_database():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            # expire_on_commit=True mirrors the real production session_factory
            # (create_session_factory() is called with no options in
            # api/dependencies.py) - this is exactly the setting under which the
            # suspected create_publication MissingGreenlet/phantom-row bug would
            # manifest, so we deliberately do NOT relax it to False here.
            factory = async_sessionmaker(engine, class_=AsyncSession)
            async with factory() as session:
                school_a = School(code=f"AC-A-{uuid4().hex[:6]}", name="Escola A")
                school_b = School(code=f"AC-B-{uuid4().hex[:6]}", name="Escola B")
                session.add_all([school_a, school_b])
                await session.flush()
                school_a_id, school_b_id = school_a.id, school_b.id
                await session.commit()
                return engine, factory, school_a_id, school_b_id

        self.engine, self.session_factory, self.school_a, self.school_b = asyncio.run(
            setup_database()
        )
        self.context = {
            "value": AuthenticatedUserContext(
                user_id="teacher-a",
                external_identity_id="teacher-a",
                role="TEACHER",
                school_id=self.school_a,
                scope_type="CLASSROOM",
                scope_external_id="CLASS-A",
            )
        }
        self.identity = {
            "value": ExternalIdentityContext(
                provider="test", external_user_id="teacher-a", roles=("teacher",)
            )
        }
        app = FastAPI()
        app.include_router(assessments_router)
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

    # ------------------------------------------------------------------
    # helpers
    # ------------------------------------------------------------------

    def become(self, role, school_id=None, scope_type="CLASSROOM", scope_external_id="CLASS-A"):
        self.context["value"] = AuthenticatedUserContext(
            user_id=f"user-{role.lower()}",
            external_identity_id=f"user-{role.lower()}",
            role=role,
            school_id=school_id if school_id is not None else self.school_a,
            scope_type=scope_type,
            scope_external_id=scope_external_id,
        )

    def create_assessment(self, **overrides):
        payload = {
            "title": "Avaliacao",
            "school_id": str(self.school_a),
            "academic_year": "2026",
            "scope_type": "CLASSROOM",
            "scope_external_id": "CLASS-A",
        }
        payload.update(overrides)
        response = self.client.post("/api/v1/assessments", json=payload)
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def create_version(self, assessment_id, **overrides):
        payload = {"title": "Versao"}
        payload.update(overrides)
        response = self.client.post(
            f"/api/v1/assessments/{assessment_id}/versions", json=payload
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    async def _seed_question(
        self,
        *,
        eligible=True,
        status="PUBLISHED",
        validation_status="approved",
        version_kind="official_original",
        visibility_scope="PUBLIC",
        school_id=None,
        link=True,
        with_official_answer_key=False,
    ):
        async with self.session_factory() as session:
            root = CatalogNode(node_type="DISCIPLINE", name="Quimica", position=1, active=True)
            session.add(root)
            await session.flush()
            root.root_id = root.id
            content = CatalogNode(
                parent_id=root.id,
                root_id=root.id,
                node_type="CONTENT",
                code=f"AC-{uuid4().hex[:8]}",
                name="Equilibrio",
                position=1,
                active=True,
            )
            session.add(content)
            taxonomy = Taxonomy(code=f"ac-{uuid4().hex}", name="Coverage", version="1")
            session.add(taxonomy)
            await session.flush()
            session.add(TaxonomyNode(
                id=content.id,
                taxonomy_id=taxonomy.id,
                code=content.code,
                name=content.name,
                node_type="skill",
            ))
            question = Question(
                validation_status=validation_status,
                status=status,
                visibility_scope=visibility_scope,
                school_id=school_id,
            )
            session.add(question)
            await session.flush()
            version = QuestionVersion(
                question_id=question.id,
                version_kind=version_kind,
                canonical_text="Enunciado",
                content_hash=uuid4().hex,
                recommended_difficulty="EASY",
            )
            session.add(version)
            await session.flush()
            correct = QuestionOption(
                question_version_id=version.id, option_key="A", position=1,
                text="Correta", is_valid_option=True,
            )
            session.add(correct)
            await session.flush()
            if link:
                session.add(ContentQuestionLink(
                    content_node_id=content.id, question_version_id=version.id,
                ))
            revision_id = None
            if with_official_answer_key:
                institution = Institution(code=f"I-{uuid4().hex}", name="Institution")
                session.add(institution)
                await session.flush()
                exam = Exam(institution_id=institution.id, code=f"E-{uuid4().hex}", name="Exam")
                session.add(exam)
                await session.flush()
                application = ExamApplication(exam_id=exam.id, year=2026, application_type="regular")
                session.add(application)
                await session.flush()
                booklet = ExamBooklet(exam_application_id=application.id, code=f"B-{uuid4().hex}")
                source = SourceDocument(
                    exam_application_id=application.id,
                    document_type="proof",
                    source_url="https://example.test/coverage.pdf",
                    acquired_at=datetime.now(timezone.utc),
                    content_hash=uuid4().hex,
                )
                session.add_all([booklet, source])
                await session.flush()
                revision = AnswerKeyRevision(
                    source_document_id=source.id, revision_number=1, is_official=True,
                )
                session.add(revision)
                await session.flush()
                occurrence = BookletQuestion(
                    exam_booklet_id=booklet.id, question_version_id=version.id, position=1,
                )
                session.add(occurrence)
                await session.flush()
                session.add(AnswerKeyEntry(
                    answer_key_revision_id=revision.id,
                    booklet_question_id=occurrence.id,
                    official_answer_label="A",
                    resolved_option_id=correct.id,
                ))
                revision_id = revision.id
            version_id = version.id
            await session.commit()
            return version_id, revision_id

    def seed_question(self, **kwargs):
        return asyncio.run(self._seed_question(**kwargs))

    def add_item(self, assessment_id, version_id, question_version_id, **overrides):
        payload = {"question_version_id": str(question_version_id), "position": 1, "points": 1}
        payload.update(overrides)
        return self.client.post(
            f"/api/v1/assessments/{assessment_id}/versions/{version_id}/items", json=payload
        )

    def progress_version(self, assessment_id, version_id, steps=("review", "approve", "publish")):
        last = None
        for step in steps:
            last = self.client.post(
                f"/api/v1/assessments/{assessment_id}/versions/{version_id}/{step}"
            )
            self.assertEqual(last.status_code, 200, last.text)
        return last

    def build_publishable_assessment(self, item_count=1, with_official_answer_key=True):
        assessment_id = self.create_assessment()
        version_id = self.create_version(assessment_id)
        question_version_ids = []
        for i in range(item_count):
            qv_id, _ = self.seed_question(with_official_answer_key=with_official_answer_key)
            question_version_ids.append(qv_id)
            resp = self.add_item(assessment_id, version_id, qv_id, position=i + 1)
            self.assertEqual(resp.status_code, 201, resp.text)
        self.progress_version(assessment_id, version_id)
        return assessment_id, version_id, question_version_ids

    def create_publication(self, assessment_id, version_id, **overrides):
        payload = {
            "assessment_version_id": version_id,
            "publication_type": "immediate",
            "time_limit_seconds": 1800,
            "attempts_allowed": 1,
        }
        payload.update(overrides)
        return self.client.post(f"/api/v1/assessments/{assessment_id}/publications", json=payload)

    # ------------------------------------------------------------------
    # create_assessment - authz / scope branches
    # ------------------------------------------------------------------

    def test_create_assessment_wrong_role_forbidden(self):
        self.become("STUDENT")
        response = self.client.post(
            "/api/v1/assessments",
            json={"school_id": str(self.school_a), "title": "X"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("author role", response.json()["detail"])

    def test_create_assessment_no_school_scope_forbidden(self):
        self.context["value"] = AuthenticatedUserContext(
            user_id="teacher-x", external_identity_id="teacher-x", role="TEACHER",
            school_id=None, scope_type="PLATFORM",
        )
        response = self.client.post("/api/v1/assessments", json={"title": "X"})
        self.assertEqual(response.status_code, 403)
        self.assertIn("school scope", response.json()["detail"])

    def test_create_assessment_school_scope_mismatch_forbidden(self):
        # teacher-a is scoped to school_a; posting for school_b must be denied.
        response = self.client.post(
            "/api/v1/assessments",
            json={"school_id": str(self.school_b), "title": "X"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("scope denied", response.json()["detail"])

    def test_create_assessment_academic_scope_denied_for_narrower_context(self):
        # Teacher scoped to a specific CLASSROOM cannot author under a
        # different scope_type/scope_external_id than their own context.
        response = self.client.post(
            "/api/v1/assessments",
            json={
                "school_id": str(self.school_a),
                "title": "X",
                "scope_type": "CLASSROOM",
                "scope_external_id": "CLASS-OTHER",
            },
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("academic scope", response.json()["detail"])

    # ------------------------------------------------------------------
    # list_assessments
    # ------------------------------------------------------------------

    def test_list_assessments_forbidden_for_non_author_role(self):
        self.become("STUDENT")
        response = self.client.get("/api/v1/assessments")
        self.assertEqual(response.status_code, 403)

    def test_list_assessments_scoped_to_own_school(self):
        self.create_assessment(title="A1")
        self.create_assessment(title="A2")
        self.become("COORDINATOR", school_id=self.school_b, scope_type="SCHOOL")
        self.create_assessment(
            title="B1", school_id=str(self.school_b), scope_type="SCHOOL", scope_external_id=None
        )
        self.become("TEACHER", school_id=self.school_a)
        response = self.client.get("/api/v1/assessments")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["total"], 2)
        titles = {item["title"] for item in body["items"]}
        self.assertEqual(titles, {"A1", "A2"})

    # ------------------------------------------------------------------
    # get_assessment / _load_assessment_for_author
    # ------------------------------------------------------------------

    def test_get_assessment_not_found(self):
        response = self.client.get(f"/api/v1/assessments/{uuid4()}")
        self.assertEqual(response.status_code, 404)

    def test_get_assessment_wrong_school_forbidden(self):
        assessment_id = self.create_assessment()
        self.become("TEACHER", school_id=self.school_b)
        response = self.client.get(f"/api/v1/assessments/{assessment_id}")
        self.assertEqual(response.status_code, 403)

    def test_get_assessment_no_school_scope_forbidden(self):
        # The create_assessment endpoint itself always rejects a missing
        # school_id (see test_create_assessment_no_school_scope_forbidden),
        # so a school-less assessment can only exist from direct DB access
        # (e.g. a legacy/platform-scoped row). _load_assessment_for_author
        # still has a defensive 403 for that shape - exercise it directly.
        from agente_ia_edu.db.models import Assessment as AssessmentModel

        async def seed_schoolless_assessment():
            async with self.session_factory() as session:
                assessment = AssessmentModel(
                    school_id=None, title="Sem escola", status="draft",
                    visibility_scope="SCHOOL", origin_type="SCHOOL",
                )
                session.add(assessment)
                await session.flush()
                assessment_id = assessment.id
                await session.commit()
                return assessment_id

        assessment_id = asyncio.run(seed_schoolless_assessment())
        response = self.client.get(f"/api/v1/assessments/{assessment_id}")
        self.assertEqual(response.status_code, 403)
        self.assertIn("no school scope", response.json()["detail"])

    # ------------------------------------------------------------------
    # create_assessment_version
    # ------------------------------------------------------------------

    def test_create_version_assessment_not_found(self):
        response = self.client.post(
            f"/api/v1/assessments/{uuid4()}/versions", json={"title": "V"}
        )
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # add_assessment_item
    # ------------------------------------------------------------------

    def test_add_item_version_not_found(self):
        assessment_id = self.create_assessment()
        qv_id, _ = self.seed_question()
        response = self.add_item(assessment_id, uuid4(), qv_id)
        self.assertEqual(response.status_code, 404)
        self.assertIn("version not found", response.json()["detail"])

    def test_add_item_version_not_draft_conflict(self):
        assessment_id = self.create_assessment()
        version_id = self.create_version(assessment_id)
        qv_id, _ = self.seed_question()
        self.assertEqual(self.add_item(assessment_id, version_id, qv_id).status_code, 201)
        self.progress_version(assessment_id, version_id, steps=("review",))
        qv_id2, _ = self.seed_question()
        response = self.add_item(assessment_id, version_id, qv_id2, position=2)
        self.assertEqual(response.status_code, 409)
        self.assertIn("immutable", response.json()["detail"])

    def test_add_item_question_version_not_found(self):
        assessment_id = self.create_assessment()
        version_id = self.create_version(assessment_id)
        response = self.add_item(assessment_id, version_id, uuid4())
        self.assertEqual(response.status_code, 404)
        self.assertIn("Question version not found", response.json()["detail"])

    def test_add_item_question_not_published_is_ineligible(self):
        assessment_id = self.create_assessment()
        version_id = self.create_version(assessment_id)
        qv_id, _ = self.seed_question(status="DRAFT")
        response = self.add_item(assessment_id, version_id, qv_id)
        self.assertEqual(response.status_code, 422)
        self.assertIn("not eligible", response.json()["detail"])

    def test_add_item_question_bad_validation_status_is_ineligible(self):
        assessment_id = self.create_assessment()
        version_id = self.create_version(assessment_id)
        qv_id, _ = self.seed_question(validation_status="pending")
        response = self.add_item(assessment_id, version_id, qv_id)
        self.assertEqual(response.status_code, 422)

    def test_add_item_question_wrong_version_kind_is_ineligible(self):
        assessment_id = self.create_assessment()
        version_id = self.create_version(assessment_id)
        qv_id, _ = self.seed_question(version_kind="modified")
        response = self.add_item(assessment_id, version_id, qv_id)
        self.assertEqual(response.status_code, 422)

    def test_add_item_question_without_content_link_is_ineligible(self):
        assessment_id = self.create_assessment()
        version_id = self.create_version(assessment_id)
        qv_id, _ = self.seed_question(link=False)
        response = self.add_item(assessment_id, version_id, qv_id)
        self.assertEqual(response.status_code, 422)

    def test_add_item_question_private_visibility_forbidden(self):
        assessment_id = self.create_assessment()
        version_id = self.create_version(assessment_id)
        qv_id, _ = self.seed_question(visibility_scope="PRIVATE")
        response = self.add_item(assessment_id, version_id, qv_id)
        self.assertEqual(response.status_code, 403)
        self.assertIn("visibility denied", response.json()["detail"])

    def test_add_item_question_school_visibility_wrong_school_forbidden(self):
        assessment_id = self.create_assessment()
        version_id = self.create_version(assessment_id)
        qv_id, _ = self.seed_question(visibility_scope="SCHOOL", school_id=self.school_b)
        response = self.add_item(assessment_id, version_id, qv_id)
        self.assertEqual(response.status_code, 403)
        self.assertIn("school scope denied", response.json()["detail"])

    def test_add_item_question_school_visibility_matching_school_succeeds(self):
        assessment_id = self.create_assessment()
        version_id = self.create_version(assessment_id)
        qv_id, _ = self.seed_question(visibility_scope="SCHOOL", school_id=self.school_a)
        response = self.add_item(assessment_id, version_id, qv_id)
        self.assertEqual(response.status_code, 201, response.text)

    # ------------------------------------------------------------------
    # create_publication - 404/409 branches
    # ------------------------------------------------------------------

    def test_create_publication_version_not_found(self):
        assessment_id = self.create_assessment()
        response = self.create_publication(assessment_id, str(uuid4()))
        self.assertEqual(response.status_code, 404)

    def test_create_publication_version_not_published_conflict(self):
        assessment_id = self.create_assessment()
        version_id = self.create_version(assessment_id)
        response = self.create_publication(assessment_id, version_id)
        self.assertEqual(response.status_code, 409)
        self.assertIn("published versions", response.json()["detail"])

    # ------------------------------------------------------------------
    # create_publication - the flagged investigation:
    # does an exception between commit() and the response ever leave a
    # phantom committed row with a 500 response and no rollback?
    # ------------------------------------------------------------------

    def test_create_publication_success_round_trip_no_missing_greenlet(self):
        """Happy path through the exact commit -> session.get -> response
        sequence flagged by the audit, run under expire_on_commit=True (the
        real production session setting) so any MissingGreenlet on the
        post-commit re-fetch would actually surface here."""
        assessment_id, version_id, _ = self.build_publishable_assessment()
        response = self.create_publication(assessment_id, version_id)
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["assessment_id"], assessment_id)
        self.assertEqual(body["assessment_version_id"], version_id)
        self.assertEqual(body["status"], "draft")
        self.assertEqual(body["time_limit_seconds"], 1800)
        self.assertEqual(body["attempts_allowed"], 1)

        # Confirm exactly one row was persisted - no phantom duplicate from a
        # retry or a partially-failed response construction.
        async def count_rows():
            async with self.session_factory() as session:
                rows = (await session.scalars(
                    select(AssessmentPublication).where(
                        AssessmentPublication.assessment_version_id == UUID(version_id)
                    )
                )).all()
                return len(rows)

        self.assertEqual(asyncio.run(count_rows()), 1)

    def test_create_publication_invalid_publication_type_does_not_return_raw_500(self):
        """AssessmentPersistenceService.create_publication raises a plain
        ValueError for an unsupported publication_type (services/assessments.py
        ~line 489). Before the fix, the route did not catch it, so this
        propagated as an unhandled exception -> generic 500 instead of a 4xx
        the client could act on (same bug shape as the wave-1 duplicate-key
        IntegrityError finding). The route now catches ValueError and maps it
        to 422."""
        assessment_id, version_id, _ = self.build_publishable_assessment()
        response = self.create_publication(assessment_id, version_id, publication_type="bogus")
        self.assertEqual(response.status_code, 422, response.text)

        # No publication should have been committed for this failed attempt.
        async def count_rows():
            async with self.session_factory() as session:
                rows = (await session.scalars(
                    select(AssessmentPublication).where(
                        AssessmentPublication.assessment_version_id == UUID(version_id)
                    )
                )).all()
                return len(rows)

        self.assertEqual(asyncio.run(count_rows()), 0)

    def test_create_publication_ends_before_starts_does_not_return_raw_500(self):
        assessment_id, version_id, _ = self.build_publishable_assessment()
        starts_at = datetime.now(timezone.utc)
        ends_at = starts_at - timedelta(hours=1)
        response = self.create_publication(
            assessment_id, version_id,
            starts_at=starts_at.isoformat(), ends_at=ends_at.isoformat(),
        )
        self.assertEqual(response.status_code, 422, response.text)

    # ------------------------------------------------------------------
    # list_publications
    # ------------------------------------------------------------------

    def test_list_publications_returns_created_publications(self):
        assessment_id, version_id, _ = self.build_publishable_assessment()
        created = self.create_publication(assessment_id, version_id)
        self.assertEqual(created.status_code, 201, created.text)
        response = self.client.get(f"/api/v1/assessments/{assessment_id}/publications")
        self.assertEqual(response.status_code, 200, response.text)
        items = response.json()
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["id"], created.json()["id"])
        self.assertEqual(items[0]["assessment_id"], assessment_id)

    def test_list_publications_assessment_not_found(self):
        response = self.client.get(f"/api/v1/assessments/{uuid4()}/publications")
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # version transitions
    # ------------------------------------------------------------------

    def test_version_transition_invalid_jump_conflict(self):
        assessment_id = self.create_assessment()
        version_id = self.create_version(assessment_id)
        # Skipping straight to "approve" from "draft" is not a legal transition.
        response = self.client.post(
            f"/api/v1/assessments/{assessment_id}/versions/{version_id}/approve"
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("Invalid assessment workflow transition", response.json()["detail"])

    def test_version_transition_not_found(self):
        assessment_id = self.create_assessment()
        response = self.client.post(
            f"/api/v1/assessments/{assessment_id}/versions/{uuid4()}/review"
        )
        self.assertEqual(response.status_code, 404)

    def test_version_transition_belongs_to_other_assessment(self):
        assessment_id = self.create_assessment()
        version_id = self.create_version(assessment_id)
        other_assessment_id = self.create_assessment(title="Outra")
        response = self.client.post(
            f"/api/v1/assessments/{other_assessment_id}/versions/{version_id}/review"
        )
        self.assertEqual(response.status_code, 404)

    # ------------------------------------------------------------------
    # activate_publication
    # ------------------------------------------------------------------

    def test_activate_publication_not_found(self):
        response = self.client.post(f"/api/v1/assessments/publications/{uuid4()}/activate")
        self.assertEqual(response.status_code, 404)

    def test_activate_publication_already_active_conflict(self):
        assessment_id, version_id, _ = self.build_publishable_assessment()
        publication_id = self.create_publication(assessment_id, version_id).json()["id"]
        first = self.client.post(f"/api/v1/assessments/publications/{publication_id}/activate")
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post(f"/api/v1/assessments/publications/{publication_id}/activate")
        self.assertEqual(second.status_code, 409)
        self.assertIn("cannot be activated", second.json()["detail"])

    def test_activate_publication_no_items_conflict(self):
        assessment_id = self.create_assessment()
        version_id = self.create_version(assessment_id)
        self.progress_version(assessment_id, version_id)
        publication_id = self.create_publication(assessment_id, version_id).json()["id"]
        response = self.client.post(f"/api/v1/assessments/publications/{publication_id}/activate")
        self.assertEqual(response.status_code, 409)
        self.assertIn("no items", response.json()["detail"])

    def test_activate_publication_missing_answer_key_conflict(self):
        assessment_id, version_id, _ = self.build_publishable_assessment(
            with_official_answer_key=False
        )
        publication_id = self.create_publication(assessment_id, version_id).json()["id"]
        response = self.client.post(f"/api/v1/assessments/publications/{publication_id}/activate")
        self.assertEqual(response.status_code, 409)
        self.assertIn("official answer key", response.json()["detail"])

    def test_activate_publication_success(self):
        assessment_id, version_id, _ = self.build_publishable_assessment()
        publication_id = self.create_publication(assessment_id, version_id).json()["id"]
        response = self.client.post(f"/api/v1/assessments/publications/{publication_id}/activate")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["status"], "active")

    # ------------------------------------------------------------------
    # assign_publication_to_student
    # ------------------------------------------------------------------

    def test_assign_publication_not_found(self):
        response = self.client.post(
            f"/api/v1/assessments/publications/{uuid4()}/assignments",
            json={"student_external_id": "student-a"},
        )
        self.assertEqual(response.status_code, 404)

    def test_assign_publication_not_active_conflict(self):
        assessment_id, version_id, _ = self.build_publishable_assessment()
        publication_id = self.create_publication(assessment_id, version_id).json()["id"]
        response = self.client.post(
            f"/api/v1/assessments/publications/{publication_id}/assignments",
            json={"student_external_id": "student-a"},
        )
        self.assertEqual(response.status_code, 409)
        self.assertIn("not active", response.json()["detail"])

    def test_assign_publication_student_outside_school_forbidden(self):
        assessment_id, version_id, _ = self.build_publishable_assessment()
        publication_id = self.create_publication(assessment_id, version_id).json()["id"]
        self.client.post(f"/api/v1/assessments/publications/{publication_id}/activate")
        response = self.client.post(
            f"/api/v1/assessments/publications/{publication_id}/assignments",
            json={"student_external_id": "student-not-enrolled"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("outside the assessment school", response.json()["detail"])

    def test_assign_publication_student_outside_teacher_classroom_forbidden(self):
        # Student is enrolled in the same school but a different classroom
        # than the requesting teacher's CLASSROOM-scoped context.
        assessment_id, version_id, _ = self.build_publishable_assessment()
        publication_id = self.create_publication(assessment_id, version_id).json()["id"]
        self.client.post(f"/api/v1/assessments/publications/{publication_id}/activate")

        async def enroll_student_other_classroom():
            async with self.session_factory() as session:
                session.add(UserSchoolLink(
                    external_user_id="student-other-class", school_id=self.school_a,
                    role="STUDENT", scope_type="CLASSROOM", scope_external_id="CLASS-B",
                    active=True,
                ))
                await session.commit()

        asyncio.run(enroll_student_other_classroom())
        response = self.client.post(
            f"/api/v1/assessments/publications/{publication_id}/assignments",
            json={"student_external_id": "student-other-class"},
        )
        self.assertEqual(response.status_code, 403)
        self.assertIn("outside the teacher classroom", response.json()["detail"])

    def test_assign_publication_success(self):
        assessment_id, version_id, _ = self.build_publishable_assessment()
        publication_id = self.create_publication(assessment_id, version_id).json()["id"]
        self.client.post(f"/api/v1/assessments/publications/{publication_id}/activate")

        async def enroll_student():
            async with self.session_factory() as session:
                session.add(UserSchoolLink(
                    external_user_id="student-a", school_id=self.school_a, role="STUDENT",
                    scope_type="CLASSROOM", scope_external_id="CLASS-A", active=True,
                ))
                await session.commit()

        asyncio.run(enroll_student())
        response = self.client.post(
            f"/api/v1/assessments/publications/{publication_id}/assignments",
            json={"student_external_id": "student-a"},
        )
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["student_external_id"], "student-a")
        self.assertEqual(body["status"], "PENDING")


if __name__ == "__main__":
    unittest.main()
