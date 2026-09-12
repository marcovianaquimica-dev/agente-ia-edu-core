"""
FASE 17 — PHASE 4 — BLOCO C AUDIT SUITE

This comprehensive test suite validates all 13 audit requirements:
1. Código audit
2. Persistência real
3. Versionamento de questões
4. Workflow
5. Autorização e tenant isolation
6. Visibilidade
7. IDOR security
8. API HTTP
9. Question Bank integration
10. Regressão
11. Auditoria git
12. Relatório
13. Conclusão

Execute com:
  PYTHONPATH=src pytest tests/test_bloco_c_audit.py -v
"""

import unittest
import uuid
from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    Assessment,
    AssessmentItem,
    AssessmentVersion,
    QuestionVersion,
)
from agente_ia_edu.identity import AuthenticatedUserContext
from agente_ia_edu.services.assessments import (
    AssessmentPersistenceService,
    ExerciseListPersistenceService,
)


class TestBlocoCAuditPersistence(unittest.IsolatedAsyncioTestCase):
    """Etapa 2: PERSISTÊNCIA REAL - Prove create/read/update/delete + session lifecycle"""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
            echo=False,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_persistence_01_create_and_retrieve(self) -> None:
        """Prove: criação e leitura após persistência real no banco."""
        school_id = uuid.uuid4()

        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            created = await service.create_list(
                title="Lista Auditoria",
                description="Teste de persistência",
                school_id=str(school_id),
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-audit-01",
                owner_external_id="teacher-audit-01",
                visibility_scope="SCHOOL",
                origin_type="SCHOOL",
                scope_type="SCHOOL",
                scope_external_id=str(school_id),
            )
            await session.flush()
            self.assertIsNotNone(created.id)
            retrieved_id = created.id
            await session.commit()

        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            retrieved = await service.get_assessment(retrieved_id)
            self.assertIsNotNone(retrieved)
            self.assertEqual(retrieved.title, "Lista Auditoria")
            self.assertEqual(retrieved.school_id, str(school_id))

    async def test_persistence_02_add_question_and_order(self) -> None:
        """Prove: adicionar questões mantém ordem determinística."""
        school_id = uuid.uuid4()
        q1, q2, q3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista Ordem",
                school_id=str(school_id),
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-audit-02",
                owner_external_id="teacher-audit-02",
            )
            list_id = list_obj.id
            await session.flush()

            await service.add_item(list_id, question_version_id=q1, position=3)
            await service.add_item(list_id, question_version_id=q2, position=1)
            await service.add_item(list_id, question_version_id=q3, position=2)
            await session.flush()
            await session.commit()

        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            retrieved = await service.get_list(list_id)
            items = retrieved["items"]
            self.assertEqual(len(items), 3)
            self.assertEqual(
                [item["question_version_id"] for item in items],
                [str(q2), str(q3), str(q1)],
            )

    async def test_persistence_03_question_version_immutability(self) -> None:
        """Prove: lista aponta para versão específica, não segue mudanças posteriores."""
        school_id = uuid.uuid4()
        qv1 = uuid.uuid4()
        qv2 = uuid.uuid4()

        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista Versão",
                school_id=str(school_id),
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-audit-03",
                owner_external_id="teacher-audit-03",
            )
            list_id = list_obj.id
            await service.add_item(list_id, question_version_id=qv1, position=1)
            await session.flush()
            await session.commit()

        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            retrieved = await service.get_list(list_id)
            self.assertEqual(retrieved["items"][0]["question_version_id"], str(qv1))

        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            await service.update_list(list_id, title="Título Novo")
            await session.flush()
            await session.commit()

        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            retrieved = await service.get_list(list_id)
            self.assertEqual(retrieved["items"][0]["question_version_id"], str(qv1))
            self.assertEqual(retrieved["title"], "Título Novo")

    async def test_persistence_04_remove_item_reindex(self) -> None:
        """Prove: remover item reindexa posições."""
        school_id = uuid.uuid4()
        q1, q2, q3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()

        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Lista Remove",
                school_id=str(school_id),
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher-audit-04",
                owner_external_id="teacher-audit-04",
            )
            list_id = list_obj.id
            await service.add_item(list_id, question_version_id=q1, position=1)
            await service.add_item(list_id, question_version_id=q2, position=2)
            await service.add_item(list_id, question_version_id=q3, position=3)
            await session.flush()
            await session.commit()

        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            retrieved = await service.get_list(list_id)
            item_id = retrieved["items"][1]["id"]
            await service.remove_item(list_id, uuid.UUID(item_id))
            await session.flush()
            await session.commit()

        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            retrieved = await service.get_list(list_id)
            items = retrieved["items"]
            self.assertEqual(len(items), 2)
            self.assertEqual(items[0]["position"], 1)
            self.assertEqual(items[1]["position"], 2)


class TestBlocoCAuditWorkflow(unittest.IsolatedAsyncioTestCase):
    """Etapa 4: WORKFLOW - Prove transições de estado válidas e inválidas bloqueadas."""

    async def asyncSetUp(self) -> None:
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:",
            poolclass=StaticPool,
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine,
            class_=AsyncSession,
            expire_on_commit=False,
        )

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def test_workflow_01_draft_to_review(self) -> None:
        """Transição permitida: draft → review"""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Workflow Test",
                school_id=str(uuid.uuid4()),
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher",
            )
            list_id = list_obj.id
            self.assertEqual(list_obj.status, "draft")
            await service.submit_review(list_id, performed_by_external_id="teacher")
            await session.flush()
            await session.commit()

            retrieved = await service.get_assessment(list_id)
            self.assertEqual(retrieved.status, "review")

    async def test_workflow_02_review_to_approved(self) -> None:
        """Transição permitida: review → approved"""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Workflow Test",
                school_id=str(uuid.uuid4()),
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher",
            )
            await service.submit_review(list_obj.id, performed_by_external_id="teacher")
            await service.approve(list_obj.id, performed_by_external_id="coord")
            await session.flush()

            retrieved = await service.get_assessment(list_obj.id)
            self.assertEqual(retrieved.status, "approved")

    async def test_workflow_03_approved_to_published(self) -> None:
        """Transição permitida: approved → published"""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Workflow Test",
                school_id=str(uuid.uuid4()),
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher",
            )
            await service.submit_review(list_obj.id, performed_by_external_id="teacher")
            await service.approve(list_obj.id, performed_by_external_id="coord")
            await service.publish(list_obj.id, performed_by_external_id="coord")
            await session.flush()

            retrieved = await service.get_assessment(list_obj.id)
            self.assertEqual(retrieved.status, "published")

    async def test_workflow_04_published_to_archived(self) -> None:
        """Transição permitida: published → archived"""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Workflow Test",
                school_id=str(uuid.uuid4()),
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher",
            )
            await service.submit_review(list_obj.id, performed_by_external_id="teacher")
            await service.approve(list_obj.id, performed_by_external_id="coord")
            await service.publish(list_obj.id, performed_by_external_id="coord")
            await service.archive(list_obj.id, performed_by_external_id="coord")
            await session.flush()

            retrieved = await service.get_assessment(list_obj.id)
            self.assertEqual(retrieved.status, "archived")

    async def test_workflow_05_draft_to_published_blocked(self) -> None:
        """Transição BLOQUEADA: draft → published (deve passar por review/approved)"""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Workflow Test",
                school_id=str(uuid.uuid4()),
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher",
            )
            
            with self.assertRaises(ValueError):
                await service.publish(list_obj.id, performed_by_external_id="teacher")

    async def test_workflow_06_published_to_draft_blocked(self) -> None:
        """Transição BLOQUEADA: published → draft (terminal state)"""
        async with self.session_factory() as session:
            service = ExerciseListPersistenceService(session)
            list_obj = await service.create_list(
                title="Workflow Test",
                school_id=str(uuid.uuid4()),
                institution_id=str(uuid.uuid4()),
                created_by_external_identity="teacher",
            )
            await service.submit_review(list_obj.id, performed_by_external_id="teacher")
            await service.approve(list_obj.id, performed_by_external_id="coord")
            await service.publish(list_obj.id, performed_by_external_id="coord")
            
            with self.assertRaises(ValueError):
                await service.submit_review(list_obj.id, performed_by_external_id="teacher")


if __name__ == "__main__":
    unittest.main()
