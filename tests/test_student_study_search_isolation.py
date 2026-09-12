import asyncio
import unittest
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.app import app
from agente_ia_edu.api.dependencies import get_session_factory, reset_identity_provider, set_identity_provider
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    CatalogNode,
    ContentQuestionLink,
    ContentResourceLink,
    EducationalResource,
    PedagogicalClassification,
    Question,
    QuestionVersion,
    ResourceAccessGrant,
    School,
)
from agente_ia_edu.identity import ExternalIdentityContext, ExternalIdentityRequest


class HeaderIdentityProvider:
    def __init__(self, school_mapping: dict | None = None):
        self.school_mapping = school_mapping or {}
    
    async def resolve(self, request: ExternalIdentityRequest) -> ExternalIdentityContext:
        subject = request.subject or request.external_user_id or "student:STUDENT_INDEPENDENT"
        parts = [p for p in subject.split(":") if p]
        role = parts[0] if parts else "student"
        user = parts[1] if len(parts) > 1 else "STUDENT_INDEPENDENT"
        school_name = parts[2] if len(parts) > 2 else None
        classroom_name = parts[3] if len(parts) > 3 else None

        school_id = self.school_mapping.get(school_name) if school_name else None
        classroom_id = classroom_name

        return ExternalIdentityContext(
            provider="test-header",
            external_user_id=user,
            student_id=user if role == "student" else None,
            institution_id=school_id,
            classroom_id=classroom_id,
            roles=(role,),
            metadata={
                "scope_type": "CLASSROOM" if classroom_id else "SCHOOL" if school_id else "PLATFORM",
                "original_subject": subject,
                "school_code": school_name,
                "institution_code": school_name,
            },
        )


class StudentStudySearchIsolationTests(unittest.IsolatedAsyncioTestCase):
    @classmethod
    def setUpClass(cls):
        cls.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
        )
        cls.session_factory = async_sessionmaker(
            cls.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

        async def init_db():
            async with cls.engine.begin() as conn:
                await conn.run_sync(Base.metadata.create_all)

        asyncio.run(init_db())
        app.dependency_overrides[get_session_factory] = lambda: cls.session_factory
        
        # Create identity provider with mapping (will be set after first seed)
        cls.identity_provider = HeaderIdentityProvider({})
        set_identity_provider(cls.identity_provider)
        cls.client = TestClient(app)
        
        # Seed once at class level and populate the school mapping
        cls.school_ids = {}
        asyncio.run(cls._setup_seed())

    @classmethod
    async def _setup_seed(cls):
        async with cls.session_factory() as session:
            school_a = School(code="SCHOOL_A", name="School A")
            school_b = School(code="SCHOOL_B", name="School B")
            session.add_all([school_a, school_b])
            await session.flush()
            cls.school_ids = {"SCHOOL_A": str(school_a.id), "SCHOOL_B": str(school_b.id)}
            cls.identity_provider.school_mapping = cls.school_ids
            await session.commit()

    @classmethod
    def tearDownClass(cls):
        app.dependency_overrides.clear()
        reset_identity_provider()
        asyncio.run(cls.engine.dispose())

    async def asyncSetUp(self):
        async with self.session_factory() as session:
            await self._seed_entities(session)

    async def _seed_entities(self, session: AsyncSession):
        await session.execute(text("DELETE FROM content_question_links"))
        await session.execute(text("DELETE FROM content_resource_links"))
        await session.execute(text("DELETE FROM resource_access_grants"))
        await session.execute(text("DELETE FROM educational_resources"))
        await session.execute(text("DELETE FROM question_versions"))
        await session.execute(text("DELETE FROM questions"))
        await session.execute(text("DELETE FROM catalog_nodes"))
        await session.execute(text("DELETE FROM schools"))

        # Create real School records with UUIDs
        school_a = School(code="SCHOOL_A", name="School A")
        school_b = School(code="SCHOOL_B", name="School B")
        session.add_all([school_a, school_b])
        await session.flush()

        school_a_uuid = school_a.id
        school_b_uuid = school_b.id

        self.school_ids = {"SCHOOL_A": str(school_a_uuid), "SCHOOL_B": str(school_b_uuid)}
        self.classroom_ids = {"CLASSROOM_A": "CLASSROOM_A", "CLASSROOM_B": "CLASSROOM_B"}
        self.identity_provider.school_mapping = self.school_ids

        # Create catalog node
        root = CatalogNode(node_type="DISCIPLINE", name="Química", position=1, active=True)
        session.add(root)
        await session.flush()
        root.root_id = root.id

        content_node = CatalogNode(
            parent_id=root.id,
            root_id=root.id,
            node_type="CONTENT",
            code="QUIM-DIL",
            name="Diluição de Soluções",
            position=1,
            active=True,
        )
        session.add(content_node)
        await session.flush()

        # Create questions
        question_public = Question(
            status="PUBLISHED",
            visibility_scope="PUBLIC",
            origin_type="PLATFORM",
            validation_status="valid",
            owner_external_id="PLATFORM",
        )
        question_school_a = Question(
            status="PUBLISHED",
            visibility_scope="SCHOOL",
            origin_type="SCHOOL",
            school_id=school_a_uuid,
            owner_external_id="SCHOOL_A",
            validation_status="valid",
        )
        question_school_b = Question(
            status="PUBLISHED",
            visibility_scope="SCHOOL",
            origin_type="SCHOOL",
            school_id=school_b_uuid,
            owner_external_id="SCHOOL_B",
            validation_status="valid",
        )
        question_classroom_a = Question(
            status="PUBLISHED",
            visibility_scope="CLASSROOM",
            origin_type="SCHOOL",
            school_id=school_a_uuid,
            owner_external_id="SCHOOL_A",
            validation_status="valid",
            metadata_={"classroom_id": "CLASSROOM_A"},
        )
        question_classroom_b = Question(
            status="PUBLISHED",
            visibility_scope="CLASSROOM",
            origin_type="SCHOOL",
            school_id=school_b_uuid,
            owner_external_id="SCHOOL_B",
            validation_status="valid",
            metadata_={"classroom_id": "CLASSROOM_B"},
        )
        session.add_all([
            question_public,
            question_school_a,
            question_school_b,
            question_classroom_a,
            question_classroom_b,
        ])
        await session.flush()

        # Create versions
        versions = {}
        for label, question in [
            ("QUESTION_PUBLIC", question_public),
            ("QUESTION_SCHOOL_A", question_school_a),
            ("QUESTION_SCHOOL_B", question_school_b),
            ("QUESTION_CLASSROOM_A", question_classroom_a),
            ("QUESTION_CLASSROOM_B", question_classroom_b),
        ]:
            version = QuestionVersion(
                question_id=question.id,
                version_kind="official_original",
                canonical_text=f"{label} sobre diluição de soluções",
                statement=f"{label} sobre diluição de soluções",
                content_hash=f"hash-{label}",
                recommended_difficulty="MEDIUM",
            )
            session.add(version)
            await session.flush()
            versions[label] = version
            session.add(
                PedagogicalClassification(
                    question_version_id=version.id,
                    discipline="Química",
                    content="Diluição de Soluções",
                    subcontent=label,
                    difficulty="MEDIUM",
                    reasoning_type="conceitual",
                    status="CLASSIFIED",
                    source="ai",
                )
            )
            session.add(
                ContentQuestionLink(
                    content_node_id=content_node.id,
                    question_version_id=version.id,
                )
            )

        # Create educational resources
        resource_public = EducationalResource(
            title="MATERIAL_PUBLIC",
            resource_type="VIDEO",
            origin_type="PLATFORM",
            visibility_scope="PUBLIC",
            status="active",
        )
        resource_school_a = EducationalResource(
            title="MATERIAL_SCHOOL_A",
            resource_type="THEORY_MATERIAL",
            origin_type="SCHOOL",
            owner_external_id="SCHOOL_A",
            visibility_scope="SCHOOL",
            status="active",
        )
        resource_school_b = EducationalResource(
            title="MATERIAL_SCHOOL_B",
            resource_type="THEORY_MATERIAL",
            origin_type="SCHOOL",
            owner_external_id="SCHOOL_B",
            visibility_scope="SCHOOL",
            status="active",
        )
        resource_classroom_a = EducationalResource(
            title="MATERIAL_CLASSROOM_A",
            resource_type="THEORY_MATERIAL",
            origin_type="SCHOOL",
            owner_external_id="SCHOOL_A",
            visibility_scope="CLASSROOM",
            status="active",
        )
        resource_classroom_b = EducationalResource(
            title="MATERIAL_CLASSROOM_B",
            resource_type="THEORY_MATERIAL",
            origin_type="SCHOOL",
            owner_external_id="SCHOOL_B",
            visibility_scope="CLASSROOM",
            status="active",
        )
        resource_granted_to_a = EducationalResource(
            title="RESOURCE_GRANTED_TO_A",
            resource_type="THEORY_MATERIAL",
            origin_type="SCHOOL",
            owner_external_id="SCHOOL_A",
            visibility_scope="PRIVATE",
            status="active",
        )
        session.add_all([
            resource_public,
            resource_school_a,
            resource_school_b,
            resource_classroom_a,
            resource_classroom_b,
            resource_granted_to_a,
        ])
        await session.flush()

        # Create resource links
        session.add_all([
            ContentResourceLink(content_node_id=content_node.id, resource_id=resource_public.id, pedagogical_role="VIDEO"),
            ContentResourceLink(content_node_id=content_node.id, resource_id=resource_school_a.id, pedagogical_role="THEORY"),
            ContentResourceLink(content_node_id=content_node.id, resource_id=resource_school_b.id, pedagogical_role="THEORY"),
            ContentResourceLink(content_node_id=content_node.id, resource_id=resource_classroom_a.id, pedagogical_role="THEORY"),
            ContentResourceLink(content_node_id=content_node.id, resource_id=resource_classroom_b.id, pedagogical_role="THEORY"),
            ContentResourceLink(content_node_id=content_node.id, resource_id=resource_granted_to_a.id, pedagogical_role="THEORY"),
        ])

        # Create access grants
        session.add_all([
            ResourceAccessGrant(resource_id=resource_school_a.id, grantee_type="SCHOOL", grantee_external_id="SCHOOL_A"),
            ResourceAccessGrant(resource_id=resource_school_b.id, grantee_type="SCHOOL", grantee_external_id="SCHOOL_B"),
            ResourceAccessGrant(resource_id=resource_classroom_a.id, grantee_type="CLASSROOM", grantee_external_id="CLASSROOM_A"),
            ResourceAccessGrant(resource_id=resource_classroom_b.id, grantee_type="CLASSROOM", grantee_external_id="CLASSROOM_B"),
            ResourceAccessGrant(resource_id=resource_granted_to_a.id, grantee_type="EXTERNAL_IDENTITY", grantee_external_id="STUDENT_A"),
        ])

        await session.commit()

    def _auth(self, user_name: str):
        mapping = {
            "STUDENT_A": "student:STUDENT_A:SCHOOL_A:CLASSROOM_A",
            "STUDENT_B": "student:STUDENT_B:SCHOOL_B:CLASSROOM_B",
            "STUDENT_INDEPENDENT": "student:STUDENT_INDEPENDENT",
        }
        return {"Authorization": f"Bearer {mapping[user_name]}"}

    def test_01_student_a_can_see_public_and_school_a_questions(self):
        response = self.client.get("/api/v1/student/search?q=dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es", headers=self._auth("STUDENT_A"))
        self.assertEqual(response.status_code, 200)
        titles = [item.get("title", "") for item in response.json()["results"]["questions"]]
        self.assertTrue(any("QUESTION_PUBLIC" in title for title in titles))
        self.assertTrue(any("QUESTION_SCHOOL_A" in title for title in titles))

    def test_02_student_a_cannot_see_school_b_question(self):
        response = self.client.get("/api/v1/student/search?q=dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es", headers=self._auth("STUDENT_A"))
        payload = response.json()
        self.assertEqual(response.status_code, 200)
        question_titles = [item.get("title", "") for item in payload["results"]["questions"]]
        self.assertFalse(any("QUESTION_SCHOOL_B" in title for title in question_titles))

    def test_03_student_b_sees_public_and_school_b_questions(self):
        response = self.client.get("/api/v1/student/search?q=dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es", headers=self._auth("STUDENT_B"))
        self.assertEqual(response.status_code, 200)
        payload = response.json()
        titles = [item.get("title", "") for item in payload["results"]["questions"]]
        self.assertTrue(any("QUESTION_PUBLIC" in title for title in titles))
        self.assertTrue(any("QUESTION_SCHOOL_B" in title for title in titles))

    def test_04_student_independent_sees_only_public_question(self):
        response = self.client.get("/api/v1/student/search?q=dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es", headers=self._auth("STUDENT_INDEPENDENT"))
        self.assertEqual(response.status_code, 200)
        titles = [item.get("title", "") for item in response.json()["results"]["questions"]]
        self.assertTrue(any("QUESTION_PUBLIC" in title for title in titles))
        self.assertFalse(any("QUESTION_SCHOOL_A" in title for title in titles))
        self.assertFalse(any("QUESTION_SCHOOL_B" in title for title in titles))

    def test_05_student_a_can_see_public_materials_and_school_a_material(self):
        response = self.client.get("/api/v1/student/search?q=dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es&resource_type=MATERIAL", headers=self._auth("STUDENT_A"))
        self.assertEqual(response.status_code, 200)
        titles = [item.get("title", "") for item in response.json()["results"]["materials"]]
        self.assertTrue(any("MATERIAL_PUBLIC" in title for title in titles))
        self.assertTrue(any("MATERIAL_SCHOOL_A" in title for title in titles))

    def test_06_student_a_cannot_see_school_b_or_classroom_b_material(self):
        response = self.client.get("/api/v1/student/search?q=dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es&resource_type=MATERIAL", headers=self._auth("STUDENT_A"))
        titles = [item.get("title", "") for item in response.json()["results"]["materials"]]
        self.assertFalse(any("MATERIAL_SCHOOL_B" in title for title in titles))
        self.assertFalse(any("MATERIAL_CLASSROOM_B" in title for title in titles))

    def test_07_student_b_cannot_see_school_a_or_classroom_a_material(self):
        response = self.client.get("/api/v1/student/search?q=dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es&resource_type=MATERIAL", headers=self._auth("STUDENT_B"))
        titles = [item.get("title", "") for item in response.json()["results"]["materials"]]
        self.assertFalse(any("MATERIAL_SCHOOL_A" in title for title in titles))
        self.assertFalse(any("MATERIAL_CLASSROOM_A" in title for title in titles))

    def test_08_student_independent_sees_only_public_material(self):
        response = self.client.get("/api/v1/student/search?q=dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es&resource_type=MATERIAL", headers=self._auth("STUDENT_INDEPENDENT"))
        titles = [item.get("title", "") for item in response.json()["results"]["materials"]]
        self.assertTrue(any("MATERIAL_PUBLIC" in title for title in titles))
        self.assertFalse(any("MATERIAL_SCHOOL_A" in title for title in titles))
        self.assertFalse(any("MATERIAL_SCHOOL_B" in title for title in titles))

    def test_09_grant_allows_student_a_and_blocks_student_b(self):
        response_a = self.client.get("/api/v1/student/search?q=dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es&resource_type=MATERIAL", headers=self._auth("STUDENT_A"))
        response_b = self.client.get("/api/v1/student/search?q=dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es&resource_type=MATERIAL", headers=self._auth("STUDENT_B"))
        titles_a = [item.get("title", "") for item in response_a.json()["results"]["materials"]]
        titles_b = [item.get("title", "") for item in response_b.json()["results"]["materials"]]
        self.assertTrue(any("RESOURCE_GRANTED_TO_A" in title for title in titles_a))
        self.assertFalse(any("RESOURCE_GRANTED_TO_A" in title for title in titles_b))

    def test_10_student_independent_cannot_see_granted_material(self):
        response = self.client.get("/api/v1/student/search?q=dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es&resource_type=MATERIAL", headers=self._auth("STUDENT_INDEPENDENT"))
        titles = [item.get("title", "") for item in response.json()["results"]["materials"]]
        self.assertFalse(any("RESOURCE_GRANTED_TO_A" in title for title in titles))

    def test_11_scope_matrix_public_is_global(self):
        response = self.client.get("/api/v1/student/search?q=dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es", headers=self._auth("STUDENT_INDEPENDENT"))
        titles = [item.get("title", "") for item in response.json()["results"]["questions"]]
        self.assertTrue(any("QUESTION_PUBLIC" in title for title in titles))

    def test_12_scope_matrix_school_requires_matching_school(self):
        response = self.client.get("/api/v1/student/search?q=dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es", headers=self._auth("STUDENT_A"))
        titles = [item.get("title", "") for item in response.json()["results"]["questions"]]
        self.assertTrue(any("QUESTION_SCHOOL_A" in title for title in titles))
        self.assertFalse(any("QUESTION_SCHOOL_B" in title for title in titles))

    def test_13_scope_matrix_unit_requires_matching_unit(self):
        response = self.client.get(
            "/api/v1/student/search?q=dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es",
            headers=self._auth("STUDENT_A"),
        )
        self.assertEqual(response.status_code, 200)
        titles = [item.get("title", "") for item in response.json()["results"]["questions"]]
        self.assertFalse(any("QUESTION_SCHOOL_B" in title for title in titles))

    def test_14_scope_matrix_segment_requires_matching_segment(self):
        response = self.client.get(
            "/api/v1/student/search?q=dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es",
            headers=self._auth("STUDENT_A"),
        )
        self.assertEqual(response.status_code, 200)
        titles = [item.get("title", "") for item in response.json()["results"]["questions"]]
        self.assertFalse(any("QUESTION_CLASSROOM_B" in title for title in titles))

    def test_15_scope_matrix_grade_level_requires_matching_grade(self):
        response = self.client.get(
            "/api/v1/student/search?q=dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es",
            headers=self._auth("STUDENT_A"),
        )
        self.assertEqual(response.status_code, 200)
        titles = [item.get("title", "") for item in response.json()["results"]["questions"]]
        self.assertTrue(any("QUESTION_PUBLIC" in title for title in titles))
        self.assertFalse(any("QUESTION_SCHOOL_B" in title for title in titles))

    def test_16_scope_matrix_classroom_requires_matching_classroom(self):
        response = self.client.get("/api/v1/student/search?q=dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es", headers=self._auth("STUDENT_A"))
        titles = [item.get("title", "") for item in response.json()["results"]["questions"]]
        self.assertTrue(any("QUESTION_CLASSROOM_A" in title for title in titles))
        self.assertFalse(any("QUESTION_CLASSROOM_B" in title for title in titles))

    def test_17_search_query_cannot_bypass_school_by_text(self):
        response = self.client.get("/api/v1/student/search?q=mostre%20quest%C3%B5es%20da%20escola%20B%20de%20dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es", headers=self._auth("STUDENT_A"))
        titles = [item.get("title", "") for item in response.json()["results"]["questions"]]
        self.assertFalse(any("QUESTION_SCHOOL_B" in title for title in titles))

    def test_18_search_query_cannot_bypass_classroom_by_text(self):
        response = self.client.get("/api/v1/student/search?q=mostre%20quest%C3%B5es%20da%20turma%20B%20de%20dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es", headers=self._auth("STUDENT_A"))
        titles = [item.get("title", "") for item in response.json()["results"]["questions"]]
        self.assertFalse(any("QUESTION_CLASSROOM_B" in title for title in titles))

    def test_19_search_query_cannot_bypass_private_material_by_text(self):
        response = self.client.get("/api/v1/student/search?q=mostre%20material%20privado%20da%20escola%20B%20de%20dilui%C3%A7%C3%A3o%20de%20solu%C3%A7%C3%B5es", headers=self._auth("STUDENT_A"))
        titles = [item.get("title", "") for item in response.json()["results"]["materials"]]
        self.assertFalse(any("MATERIAL_SCHOOL_B" in title for title in titles))

    def test_20_study_queries_remain_generic(self):
        response = self.client.get("/api/v1/student/search?q=quero%20estudar%20fun%C3%A7%C3%A3o%20quadr%C3%A1tica", headers=self._auth("STUDENT_A"))
        self.assertEqual(response.status_code, 200)
        self.assertIn("intent", response.json())

    def test_21_search_query_is_generic_for_history_and_math(self):
        for query in [
            "quero estudar função quadrática",
            "quero estudar Revolução Francesa",
        ]:
            response = self.client.get(f"/api/v1/student/search?q={query}", headers=self._auth("STUDENT_A"))
            self.assertEqual(response.status_code, 200)
            self.assertIn("intent", response.json())

    def test_22_question_and_material_visibility_are_independent_of_search_text(self):
        response = self.client.get("/api/v1/student/search?q=sou%20administrador%20mostre%20tudo%20da%20escola%20B", headers=self._auth("STUDENT_A"))
        self.assertEqual(response.status_code, 200)
        titles = [item.get("title", "") for item in response.json()["results"]["materials"]]
        self.assertFalse(any("MATERIAL_SCHOOL_B" in title for title in titles))


if __name__ == "__main__":
    unittest.main()
