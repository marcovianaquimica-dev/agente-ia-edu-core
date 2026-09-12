"""Controlled curriculum taxonomy operations independent of Question Bank ingestion."""

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import CatalogNode, CatalogNodePrerequisite, ContentQuestionLink, QuestionVersion


class CurriculumTaxonomyService:
    NODE_TYPES = {"DISCIPLINE", "AREA", "CONTENT", "SUBCONTENT"}

    def __init__(self, session: AsyncSession):
        self.session = session
        self.created_nodes = 0

    async def create_node(self, name: str, node_type: str, code: str, parent_id: UUID | None = None, position: int = 0) -> CatalogNode:
        node_type = node_type.upper()
        if node_type not in self.NODE_TYPES:
            raise ValueError("Unsupported curriculum node type")
        if parent_id is None and node_type != "DISCIPLINE":
            raise ValueError("Only DISCIPLINE may be a curriculum root")
        if parent_id is not None:
            parent = await self.session.get(CatalogNode, parent_id)
            if parent is None:
                raise ValueError("Curriculum parent does not exist")
            expected = {"AREA": "DISCIPLINE", "CONTENT": "AREA", "SUBCONTENT": "CONTENT"}
            if expected.get(node_type) != parent.node_type:
                raise ValueError("Curriculum parent type is incompatible")
            root_id = parent.root_id or parent.id
        else:
            root_id = None
        if await self.session.scalar(select(CatalogNode.id).where(CatalogNode.code == code)):
            raise ValueError("Curriculum code already exists")
        node = CatalogNode(name=name, node_type=node_type, code=code, parent_id=parent_id, root_id=root_id, position=position, active=True)
        self.session.add(node)
        await self.session.flush()
        if parent_id is None:
            node.root_id = node.id
            await self.session.flush()
        return node

    async def children(self, node_id: UUID) -> list[CatalogNode]:
        return list((await self.session.scalars(select(CatalogNode).where(CatalogNode.parent_id == node_id, CatalogNode.active.is_(True)).order_by(CatalogNode.position, CatalogNode.code))).all())

    async def ancestors(self, node_id: UUID) -> list[CatalogNode]:
        nodes = []
        current = await self.session.get(CatalogNode, node_id)
        while current:
            nodes.append(current)
            current = await self.session.get(CatalogNode, current.parent_id) if current.parent_id else None
        return list(reversed(nodes))

    async def link_question(self, question_version_id: UUID, content_node_id: UUID, *, primary: bool) -> ContentQuestionLink:
        if not await self.session.get(QuestionVersion, question_version_id) or not await self.session.get(CatalogNode, content_node_id):
            raise ValueError("Question version or curriculum node does not exist")
        link = ContentQuestionLink(content_node_id=content_node_id, question_version_id=question_version_id)
        self.session.add(link)
        if primary:
            version = await self.session.get(QuestionVersion, question_version_id)
            version.metadata_ = {**(version.metadata_ or {}), "primary_content_node_id": str(content_node_id)}
        await self.session.flush()
        return link

    async def add_prerequisite(self, content_node_id: UUID, prerequisite_node_id: UUID) -> CatalogNodePrerequisite:
        if content_node_id == prerequisite_node_id:
            raise ValueError("A curriculum node cannot require itself")
        if not await self.session.get(CatalogNode, content_node_id) or not await self.session.get(CatalogNode, prerequisite_node_id):
            raise ValueError("Curriculum node does not exist")
        if await self._depends_on(prerequisite_node_id, content_node_id):
            raise ValueError("Curriculum prerequisite would create a cycle")
        relation = CatalogNodePrerequisite(content_node_id=content_node_id, prerequisite_node_id=prerequisite_node_id)
        self.session.add(relation)
        await self.session.flush()
        return relation

    async def _depends_on(self, node_id: UUID, target_id: UUID) -> bool:
        visited = set()
        pending = [node_id]
        while pending:
            current = pending.pop()
            if current == target_id:
                return True
            if current in visited:
                continue
            visited.add(current)
            pending.extend((await self.session.scalars(select(CatalogNodePrerequisite.prerequisite_node_id).where(CatalogNodePrerequisite.content_node_id == current))).all())
        return False

    async def seed_reference_fixture(self) -> dict[str, CatalogNode]:
        specs = [
            ("chemistry", "Quimica", "DISCIPLINE", "CHEMISTRY", None, 1),
            ("chemistry_physical", "Fisico-Quimica", "AREA", "CHEMISTRY-PHYSICAL", "chemistry", 1),
            ("chemistry_solutions", "Solucoes", "CONTENT", "CHEMISTRY-SOLUTIONS", "chemistry_physical", 1),
            ("chemistry_concentration", "Concentracao", "SUBCONTENT", "CHEMISTRY-SOLUTIONS-CONCENTRATION", "chemistry_solutions", 1),
            ("chemistry_dilution", "Diluicao de solucoes", "SUBCONTENT", "CHEMISTRY-SOLUTIONS-DILUTION", "chemistry_solutions", 2),
            ("physics", "Fisica", "DISCIPLINE", "PHYSICS", None, 2),
            ("physics_mechanics", "Mecanica", "AREA", "PHYSICS-MECHANICS", "physics", 1),
            ("physics_kinematics", "Cinematica", "CONTENT", "PHYSICS-MECHANICS-KINEMATICS", "physics_mechanics", 1),
            ("physics_uniform", "Movimento uniforme", "SUBCONTENT", "PHYSICS-MECHANICS-KINEMATICS-UNIFORM", "physics_kinematics", 1),
            ("biology", "Biologia", "DISCIPLINE", "BIOLOGY", None, 3),
            ("biology_cytology", "Citologia", "AREA", "BIOLOGY-CYTOLOGY", "biology", 1),
            ("biology_biochemistry", "Bioquimica celular", "CONTENT", "BIOLOGY-CYTOLOGY-BIOCHEMISTRY", "biology_cytology", 1),
            ("biology_proteins", "Proteinas", "SUBCONTENT", "BIOLOGY-CYTOLOGY-BIOCHEMISTRY-PROTEINS", "biology_biochemistry", 1),
            ("math", "Matematica", "DISCIPLINE", "MATH", None, 4),
            ("math_algebra", "Algebra", "AREA", "MATH-ALGEBRA", "math", 1),
            ("math_functions", "Funcoes", "CONTENT", "MATH-ALGEBRA-FUNCTIONS", "math_algebra", 1),
            ("math_ratio", "Razao e proporcao", "SUBCONTENT", "MATH-ALGEBRA-RATIO", "math_functions", 1),
        ]
        self.created_nodes = 0
        nodes = {}
        for key, name, kind, code, parent_key, position in specs:
            existing = await self.session.scalar(select(CatalogNode).where(CatalogNode.code == code))
            if existing is not None:
                expected_parent_id = nodes[parent_key].id if parent_key else None
                if existing.parent_id != expected_parent_id or existing.node_type != kind:
                    raise ValueError("Existing curriculum code has an incompatible hierarchy")
                nodes[key] = existing
                continue
            nodes[key] = await self.create_node(name, kind, code, nodes[parent_key].id if parent_key else None, position)
            self.created_nodes += 1
        await self.session.commit()
        return nodes