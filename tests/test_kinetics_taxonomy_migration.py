import importlib.util
import unittest
from pathlib import Path

from alembic.operations import Operations
from alembic.runtime.migration import MigrationContext
from sqlalchemy import create_engine, func, select
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import CatalogNode, ContentQuestionLink, PedagogicalClassification, Question, QuestionVersion
from agente_ia_edu.services.curriculum_classification import KINETICS_RETRIEVAL_VOCABULARY, ClassificationProposalService
from agente_ia_edu.services.ingestion_parser import PdfParser


MIGRATION_PATH = Path(__file__).parents[1] / "migrations" / "versions" / "024_chemistry_kinetics.py"
SPEC = importlib.util.spec_from_file_location("kinetics_migration", MIGRATION_PATH)
assert SPEC is not None and SPEC.loader is not None
migration = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(migration)


class KineticsTaxonomyMigrationTests(unittest.TestCase):
    def setUp(self):
        self.engine = create_engine("sqlite://", poolclass=StaticPool)
        Base.metadata.create_all(self.engine)
        self.factory = sessionmaker(self.engine, expire_on_commit=False)

    def tearDown(self):
        self.engine.dispose()

    def _seed(self):
        with self.factory() as session:
            nodes = {}
            specs = (
                ("chemistry", "Quimica", "DISCIPLINE", "CHEMISTRY", None, 1),
                ("physical", "Fisico-Quimica", "AREA", "CHEMISTRY-PHYSICAL", "chemistry", 1),
                ("solutions", "Solucoes", "CONTENT", "CHEMISTRY-SOLUTIONS", "physical", 1),
                ("concentration", "Concentracao", "SUBCONTENT", "CHEMISTRY-SOLUTIONS-CONCENTRATION", "solutions", 1),
                ("dilution", "Diluicao", "SUBCONTENT", "CHEMISTRY-SOLUTIONS-DILUTION", "solutions", 2),
                ("physics", "Fisica", "DISCIPLINE", "PHYSICS", None, 2),
                ("mechanics", "Mecanica", "AREA", "PHYSICS-MECHANICS", "physics", 1),
                ("kinematics", "Cinematica", "CONTENT", "PHYSICS-MECHANICS-KINEMATICS", "mechanics", 1),
                ("uniform", "Movimento uniforme", "SUBCONTENT", "PHYSICS-MECHANICS-KINEMATICS-UNIFORM", "kinematics", 1),
                ("biology", "Biologia", "DISCIPLINE", "BIOLOGY", None, 3),
                ("cytology", "Citologia", "AREA", "BIOLOGY-CYTOLOGY", "biology", 1),
                ("biochemistry", "Bioquimica", "CONTENT", "BIOLOGY-CYTOLOGY-BIOCHEMISTRY", "cytology", 1),
                ("proteins", "Proteinas", "SUBCONTENT", "BIOLOGY-CYTOLOGY-BIOCHEMISTRY-PROTEINS", "biochemistry", 1),
                ("math", "Matematica", "DISCIPLINE", "MATH", None, 4),
                ("algebra", "Algebra", "AREA", "MATH-ALGEBRA", "math", 1),
                ("functions", "Funcoes", "CONTENT", "MATH-ALGEBRA-FUNCTIONS", "algebra", 1),
                ("ratio", "Razao", "SUBCONTENT", "MATH-ALGEBRA-RATIO", "functions", 1),
            )
            for key, name, node_type, code, parent_key, position in specs:
                parent = nodes.get(parent_key)
                node = CatalogNode(
                    name=name, node_type=node_type, code=code, position=position,
                    parent_id=parent.id if parent else None,
                    root_id=parent.root_id or parent.id if parent else None,
                )
                session.add(node)
                session.flush()
                if parent is None:
                    node.root_id = node.id
                nodes[key] = node
            session.commit()

    def _run(self, operation):
        with self.engine.begin() as connection:
            context = MigrationContext.configure(connection)
            original = migration.op
            migration.op = Operations(context)
            try:
                operation()
            finally:
                migration.op = original

    def test_upgrade_is_idempotent_and_preserves_existing_catalog(self):
        self._seed()
        self._run(migration.upgrade)
        self._run(migration.upgrade)
        with self.factory() as session:
            node = session.scalar(select(CatalogNode).where(CatalogNode.code == migration.NODE_CODE))
            parent = session.scalar(select(CatalogNode).where(CatalogNode.code == migration.PARENT_CODE))
            self.assertEqual(session.scalar(select(func.count()).select_from(CatalogNode)), 18)
            self.assertEqual((node.name, node.description, node.node_type, node.parent_id), (migration.NODE_NAME, migration.NODE_DESCRIPTION, migration.NODE_TYPE, parent.id))
            self.assertEqual(session.scalar(select(func.count()).select_from(ContentQuestionLink)), 0)

    def test_upgrade_fails_closed_for_missing_or_incompatible_parent_and_node(self):
        with self.assertRaisesRegex(RuntimeError, "parent is missing"):
            self._run(migration.upgrade)
        self._seed()
        with self.factory() as session:
            parent = session.scalar(select(CatalogNode).where(CatalogNode.code == migration.PARENT_CODE))
            parent.node_type = "CONTENT"
            session.commit()
        with self.assertRaisesRegex(RuntimeError, "parent has incompatible type"):
            self._run(migration.upgrade)

    def test_upgrade_fails_closed_for_incompatible_existing_code(self):
        self._seed()
        with self.factory() as session:
            baseline_count = session.scalar(select(func.count()).select_from(CatalogNode))
        with self.factory() as session:
            parent = session.scalar(select(CatalogNode).where(CatalogNode.code == migration.PARENT_CODE))
            session.add(CatalogNode(
                code=migration.NODE_CODE, name="Incompatible", node_type=migration.NODE_TYPE,
                parent_id=parent.id, root_id=parent.root_id, position=migration.NODE_POSITION,
            ))
            session.commit()
        with self.assertRaisesRegex(RuntimeError, "Existing taxonomy node is incompatible"):
            self._run(migration.upgrade)
        with self.factory() as session:
            self.assertEqual(session.scalar(select(func.count()).select_from(CatalogNode)), baseline_count + 1)

    def test_upgrade_fails_closed_for_inactive_parent(self):
        self._seed()
        with self.factory() as session:
            parent = session.scalar(select(CatalogNode).where(CatalogNode.code == migration.PARENT_CODE))
            parent.active = False
            session.commit()
        with self.assertRaisesRegex(RuntimeError, "parent is inactive"):
            self._run(migration.upgrade)
        with self.factory() as session:
            self.assertIsNone(session.scalar(select(CatalogNode).where(CatalogNode.code == migration.NODE_CODE)))

    def test_downgrade_removes_only_unreferenced_node_and_fails_closed_for_dependency(self):
        self._seed()
        self._run(migration.upgrade)
        self._run(migration.downgrade)
        with self.factory() as session:
            self.assertIsNone(session.scalar(select(CatalogNode).where(CatalogNode.code == migration.NODE_CODE)))
        self._run(migration.upgrade)
        with self.factory() as session:
            kinetics = session.scalar(select(CatalogNode).where(CatalogNode.code == migration.NODE_CODE))
            question = Question(validation_status="validated", origin_type="IMPORTED", status="DRAFT", visibility_scope="PRIVATE")
            session.add(question)
            session.flush()
            version = QuestionVersion(question_id=question.id, version_kind="official_original", canonical_text="Question", content_hash="kinetics-dependency")
            session.add(version)
            session.flush()
            session.add(ContentQuestionLink(content_node_id=kinetics.id, question_version_id=version.id))
            session.commit()
        with self.assertRaisesRegex(RuntimeError, "with dependencies"):
            self._run(migration.downgrade)

    def test_upgrade_preserves_history_and_enables_controlled_vocabulary_binding(self):
        self._seed()
        with self.factory() as session:
            question = Question(validation_status="validated", origin_type="IMPORTED", status="DRAFT", visibility_scope="PRIVATE")
            session.add(question)
            session.flush()
            version = QuestionVersion(question_id=question.id, version_kind="official_original", canonical_text="Historical", content_hash="historical")
            session.add(version)
            session.flush()
            classification = PedagogicalClassification(
                question_version_id=version.id, discipline="CHEMISTRY", content="CHEMISTRY-SOLUTIONS", subcontent="", difficulty="UNKNOWN", reasoning_type="UNSPECIFIED", status="NEEDS_REVIEW", source="ai",
            )
            session.add(classification)
            session.commit()
            historical_id = classification.id
        self._run(migration.upgrade)
        questions = {item.question_number: item for item in PdfParser.parse_file(Path("var/inep-pilot/2020_PV_impresso_D2_CD5.pdf")).questions}
        for number in (93, 128):
            with self.subTest(question=number):
                self.assertIsNotNone(ClassificationProposalService.match_retrieval_vocabulary(questions[number].statement_text, KINETICS_RETRIEVAL_VOCABULARY, known_codes={migration.NODE_CODE}))
        for number in (91, 92, 95, 104, 135):
            with self.subTest(question=number):
                self.assertIsNone(ClassificationProposalService.match_retrieval_vocabulary(questions[number].statement_text, KINETICS_RETRIEVAL_VOCABULARY, known_codes={migration.NODE_CODE}))
        with self.factory() as session:
            self.assertIsNotNone(session.get(PedagogicalClassification, historical_id))