"""Idempotent demo-data seed for local/dev environments.

Populates ONE realistic school - using the id already hardcoded in the
teacher and coordination portal frontends (`teacher.js`/`coordination.js`
`state.schoolId`), which previously pointed at a School row that did not
exist - with a teacher, a coordinator, a secretary, eight students split
across two classrooms, taught lessons, mastery evidence, pedagogical
context, a published theory material, exercise lists built from the
questions the classification pipeline has already classified
(PedagogicalClassification, status=CLASSIFIED), and reception candidates
at different funnel stages.

Goal: let the four web portals (teacher, coordination, reception, student)
be evaluated against real data instead of empty/fallback state.

Safe to re-run: every section checks for existing rows before inserting.

Usage:
    source .env
    export DATABASE_URL="postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@localhost:5433/${POSTGRES_DB}"
    .venv/bin/python scripts/seed_demo_data.py
"""

from __future__ import annotations

import asyncio
import os
import uuid
from datetime import datetime, timedelta, timezone

from sqlalchemy import or_, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.models import CatalogNode, QuestionVersion, School, UserSchoolLink
from agente_ia_edu.services.activity_assignment_store import ActivityAssignmentStore
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.catalog import TheoryMaterialService
from agente_ia_edu.services.learning_path import ContentMasteryService
from agente_ia_edu.services.list_generator import ListConfiguration
from agente_ia_edu.services.question_list_store import QuestionListStore, Requester
from agente_ia_edu.services.reception import ReceptionService
from agente_ia_edu.services.teaching_context import TeachingContextService

SCHOOL_ID = uuid.UUID("6f26cd3c-63d5-4509-a041-13714f75e53e")
SCHOOL_CODE = "SCH_A"
SCHOOL_NAME = "Escola Partner"
ACADEMIC_YEAR = "2026"
TEACHER_ID = "prof_mendes"
COORDINATOR_ID = "coord_a"
SECRETARY_ID = "sec_a"
CLASSROOMS = {
    "TURMA_3A": ["student:alice", "student:bruno", "student:carla", "student:diego"],
    "TURMA_3B": ["student:elisa", "student:felipe", "student:giovanna", "student:hugo"],
}
STUDENT_NAMES = {
    "student:alice": "Alice Ferreira",
    "student:bruno": "Bruno Castro",
    "student:carla": "Carla Nogueira",
    "student:diego": "Diego Ramos",
    "student:elisa": "Elisa Tavares",
    "student:felipe": "Felipe Sousa",
    "student:giovanna": "Giovanna Prado",
    "student:hugo": "Hugo Martins",
}
# (correct, total) per student -> spreads the class across crítico / em desenvolvimento / consolidado
MASTERY_PROFILE = {
    "student:alice": (7, 9),
    "student:bruno": (3, 9),
    "student:carla": (5, 9),
    "student:diego": (8, 9),
    "student:elisa": (2, 8),
    "student:felipe": (6, 9),
    "student:giovanna": (9, 10),
    "student:hugo": (4, 9),
}
CONTENT_CODES = [
    "CHEMISTRY-SOLUTIONS",
    "CHEMISTRY-PHYSICAL-STOICHIOMETRY",
    "CHEMISTRY-PHYSICAL-KINETICS",
    "MATH-ALGEBRA-FUNCTIONS",
    "PHYSICS-MECHANICS-KINEMATICS",
]


async def ensure_school(session: AsyncSession) -> School:
    school = await session.get(School, SCHOOL_ID)
    if school:
        print(f"[school] already exists: {school.name}")
        return school
    school = School(id=SCHOOL_ID, code=SCHOOL_CODE, name=SCHOOL_NAME, status="ACTIVE")
    session.add(school)
    await session.flush()
    print(f"[school] created: {school.name} ({school.id})")
    return school


async def ensure_link(
    admin: PlatformAdminService,
    session: AsyncSession,
    *,
    external_user_id: str,
    role: str,
    scope_type: str,
    scope_external_id: str | None = None,
) -> UserSchoolLink:
    existing = await session.scalar(
        select(UserSchoolLink).where(
            UserSchoolLink.external_user_id == external_user_id,
            UserSchoolLink.school_id == SCHOOL_ID,
            UserSchoolLink.role == role,
            UserSchoolLink.active.is_(True),
        )
    )
    if existing:
        return existing
    link = await admin.link_user_to_school(
        performed_by_external_id="admin:master",
        external_user_id=external_user_id,
        role=role,
        scope_type=scope_type,
        school_id=SCHOOL_ID,
        scope_external_id=scope_external_id,
    )
    print(f"[link] {external_user_id} -> {role}/{scope_type}" + (f"={scope_external_id}" if scope_external_id else ""))
    return link


async def ensure_roles(session: AsyncSession) -> None:
    admin = PlatformAdminService(session)
    await ensure_link(admin, session, external_user_id=TEACHER_ID, role=AdminRole.TEACHER, scope_type=AdminScopeType.SCHOOL)
    await ensure_link(admin, session, external_user_id=COORDINATOR_ID, role=AdminRole.COORDINATOR, scope_type=AdminScopeType.SCHOOL)
    await ensure_link(admin, session, external_user_id=SECRETARY_ID, role=AdminRole.SECRETARY, scope_type=AdminScopeType.SCHOOL)
    for classroom_id, students in CLASSROOMS.items():
        for student_id in students:
            await ensure_link(
                admin, session,
                external_user_id=student_id, role=AdminRole.STUDENT,
                scope_type=AdminScopeType.CLASSROOM, scope_external_id=classroom_id,
            )
    await session.commit()


async def get_content_nodes(session: AsyncSession) -> dict[str, CatalogNode]:
    result = await session.execute(select(CatalogNode).where(CatalogNode.code.in_(CONTENT_CODES)))
    nodes = {n.code: n for n in result.scalars().all()}
    missing = set(CONTENT_CODES) - set(nodes)
    if missing:
        raise RuntimeError(f"Catalog nodes not found (expected already seeded): {missing}")
    return nodes


async def seed_lessons(session: AsyncSession, nodes: dict[str, CatalogNode]) -> None:
    ctx = TeachingContextService(session)
    existing = await session.scalar(
        select(UserSchoolLink.id).where(UserSchoolLink.school_id == SCHOOL_ID).limit(1)
    )
    from agente_ia_edu.db.models import TeachingLesson

    already = await session.scalar(select(TeachingLesson.id).where(TeachingLesson.school_id == SCHOOL_ID).limit(1))
    if already:
        print("[lessons] already seeded, skipping")
        return

    content_by_classroom = {
        "TURMA_3A": [nodes["CHEMISTRY-SOLUTIONS"], nodes["CHEMISTRY-PHYSICAL-STOICHIOMETRY"], nodes["MATH-ALGEBRA-FUNCTIONS"]],
        "TURMA_3B": [nodes["CHEMISTRY-PHYSICAL-KINETICS"], nodes["PHYSICS-MECHANICS-KINEMATICS"], nodes["CHEMISTRY-SOLUTIONS"]],
    }
    base_date = datetime.now(timezone.utc) - timedelta(days=21)
    for classroom_id, contents in content_by_classroom.items():
        for i, node in enumerate(contents):
            await ctx.record_lesson(
                teacher_id=TEACHER_ID,
                school_id=SCHOOL_ID,
                classroom_id=classroom_id,
                content_node_id=node.id,
                academic_year=ACADEMIC_YEAR,
                unit_id="MAIN_UNIT",
                segment_id="MEDIO",
                grade_level="3ª Série",
                lesson_date=base_date + timedelta(days=i * 5),
                duration_minutes=50,
                title=f"Aula: {node.name}",
                summary_observation=f"Turma trabalhou {node.name.lower()} com exercícios guiados.",
            )
    print(f"[lessons] created for {len(content_by_classroom)} classrooms")


async def seed_mastery(session: AsyncSession, nodes: dict[str, CatalogNode]) -> None:
    already = await session.scalar(select(select(1).select_from(CatalogNode).exists()))
    from agente_ia_edu.db.models import StudentContentMastery

    existing = await session.scalar(select(StudentContentMastery.id).limit(1))
    if existing:
        print("[mastery] already seeded, skipping")
        return

    service = ContentMasteryService()
    content_pool = list(nodes.values())
    for idx, (student_id, (correct, total)) in enumerate(MASTERY_PROFILE.items()):
        student_contents = [content_pool[idx % len(content_pool)], content_pool[(idx + 1) % len(content_pool)]]
        for node in student_contents:
            mastery = await service.get_or_create_mastery(session, student_id, node.id)
            wrong = total - correct
            responses = [True] * correct + [False] * wrong
            for is_correct in responses:
                await service.update_mastery_after_response(session, mastery, is_correct)
        print(f"[mastery] {student_id}: {correct}/{total} across {len(student_contents)} content(s)")
    await session.commit()


async def seed_pedagogical_context(session: AsyncSession, nodes: dict[str, CatalogNode]) -> None:
    ctx = TeachingContextService(session)
    from agente_ia_edu.db.models import PedagogicalContext

    existing = await session.scalar(select(PedagogicalContext.id).where(PedagogicalContext.source == "COORDINATION").limit(1))
    if existing:
        print("[pedagogical-context] already seeded, skipping")
        return
    await ctx.record_coordination_context(
        coordinator_id=COORDINATOR_ID,
        school_id=SCHOOL_ID,
        content_node_id=nodes["CHEMISTRY-PHYSICAL-KINETICS"].id,
        classroom_id="TURMA_3B",
        source="COORDINATION",
        title="Priorizar cinética química no 3º bimestre",
        description="Resultado do simulado interno mostrou queda de desempenho neste conteúdo.",
        academic_year=ACADEMIC_YEAR,
    )
    print("[pedagogical-context] created 1 coordination directive")


async def seed_material(session: AsyncSession, nodes: dict[str, CatalogNode]) -> None:
    from agente_ia_edu.db.models import TheoryMaterial

    existing = await session.scalar(select(TheoryMaterial.id).where(TheoryMaterial.school_id == SCHOOL_ID).limit(1))
    if existing:
        print("[material] already seeded, skipping")
        return

    service = TheoryMaterialService()
    node = nodes["CHEMISTRY-SOLUTIONS"]
    material = await service.create_material(
        session,
        title="Soluções: conceitos e cálculo de concentração",
        created_by_external_identity=TEACHER_ID,
        primary_content_node_id=node.id,
        school_id=SCHOOL_ID,
        description="Apostila de apoio para a turma revisar antes da prova.",
        material_kind="SUPPORT",
        authoring_source="TEACHER",
        visibility_scope="SCHOOL",
    )
    from agente_ia_edu.repositories.catalog import TheoryMaterialRepository

    repo = TheoryMaterialRepository(session)
    version = await repo.get_latest_version(material.id)
    await service.add_section(
        session,
        material_version_id=version.id,
        section_type="THEORY",
        position=1,
        title="Concentração comum e molar",
        body="Concentração comum é a razão entre a massa do soluto e o volume da solução...",
        content_node_id=node.id,
    )
    await service.submit_for_review(session, material_version_id=version.id)
    await service.approve_version(session, material_version_id=version.id)
    await service.publish_version(session, material_version_id=version.id, visibility_scope="SCHOOL")
    await session.commit()
    print(f"[material] published: {material.title}")


async def seed_activity(session: AsyncSession) -> None:
    from agente_ia_edu.db.models import Assessment

    existing = await session.scalar(select(Assessment.id).where(Assessment.school_id == SCHOOL_ID).limit(1))
    if existing:
        print("[activity] already seeded, skipping")
        return

    versions = (await session.execute(
        select(QuestionVersion.id).where(QuestionVersion.version_kind == "official_original").limit(5)
    )).scalars().all()
    if not versions:
        print("[activity] no official questions available, skipping")
        return

    requester = Requester(external_user_id=TEACHER_ID, school_id=str(SCHOOL_ID), role="TEACHER")
    store = QuestionListStore(session)
    summary = await store.create(
        configuration=ListConfiguration(
            title="Lista de Revisão — Química e Física",
            instructions="Resolva com calma, revisando os conceitos vistos em aula.",
        ),
        question_version_ids=list(versions),
        requester=requester,
    )
    await store.finalize(list_id=uuid.UUID(summary.id), requester=requester)

    assign_store = ActivityAssignmentStore(session)
    await assign_store.create(
        uuid.UUID(summary.id),
        requester=requester,
        target_type="CLASS",
        target_id="TURMA_3A",
        academic_year=ACADEMIC_YEAR,
    )
    await session.commit()
    print(f"[activity] '{summary.title}' distributed to TURMA_3A")


CLASSIFIED_LISTS = [
    ("Revisão Classificada — Ciências da Natureza", ("BIOLOGY-", "CHEMISTRY-", "PHYSICS-"), "TURMA_3A"),
    ("Revisão Classificada — Matemática", ("MATH-",), "TURMA_3B"),
]


async def seed_classified_activities(session: AsyncSession) -> None:
    """Build real exercise lists out of questions the classification pipeline
    has already finished classifying (PedagogicalClassification, status=
    CLASSIFIED, lifecycle=ACTIVE, curriculum-v2) - as opposed to seed_activity()
    above, which just grabs arbitrary official questions."""
    from agente_ia_edu.db.models import (
        Assessment,
        BookletQuestion,
        ExamApplication,
        ExamBooklet,
        PedagogicalClassification,
    )

    requester = Requester(external_user_id=TEACHER_ID, school_id=str(SCHOOL_ID), role="TEACHER")
    for title, content_prefixes, classroom_id in CLASSIFIED_LISTS:
        existing = await session.scalar(select(Assessment.id).where(Assessment.title == title).limit(1))
        if existing:
            print(f"[classified-activity] '{title}' already seeded, skipping")
            continue

        prefix_filter = or_(*[PedagogicalClassification.content.like(f"{p}%") for p in content_prefixes])
        # Only official (booklet-linked) versions can go into a QuestionListStore list -
        # some classified rows point at authorial question_versions, which can't.
        rows = (await session.execute(
            select(PedagogicalClassification.question_version_id)
            .join(
                BookletQuestion,
                BookletQuestion.question_version_id == PedagogicalClassification.question_version_id,
            )
            .join(ExamBooklet, ExamBooklet.id == BookletQuestion.exam_booklet_id)
            .join(ExamApplication, ExamApplication.id == ExamBooklet.exam_application_id)
            .where(
                PedagogicalClassification.status == "CLASSIFIED",
                PedagogicalClassification.lifecycle == "ACTIVE",
                PedagogicalClassification.metadata_["taxonomy_version"].as_string() == "curriculum-v2",
                prefix_filter,
            )
        )).scalars().all()
        if not rows:
            print(f"[classified-activity] no classified questions found for '{title}', skipping")
            continue

        store = QuestionListStore(session)
        summary = await store.create(
            configuration=ListConfiguration(
                title=title,
                instructions="Questões já classificadas pelo motor de classificação curricular.",
            ),
            question_version_ids=list(rows),
            requester=requester,
        )
        await store.finalize(list_id=uuid.UUID(summary.id), requester=requester)

        assign_store = ActivityAssignmentStore(session)
        await assign_store.create(
            uuid.UUID(summary.id),
            requester=requester,
            target_type="CLASS",
            target_id=classroom_id,
            academic_year=ACADEMIC_YEAR,
        )
        await session.commit()
        print(f"[classified-activity] '{title}' ({len(rows)} questões classificadas) distributed to {classroom_id}")


async def seed_reception(session: AsyncSession) -> None:
    from agente_ia_edu.db.models import ReceptionCandidate

    existing = await session.scalar(select(ReceptionCandidate.id).where(ReceptionCandidate.school_id == SCHOOL_ID).limit(1))
    if existing:
        print("[reception] already seeded, skipping")
        return

    service = ReceptionService(session)
    pre_reg = [
        {"full_name": "Marina Alves Costa", "phone": "11988880001", "email": "marina.costa@example.com"},
        {"full_name": "Théo Fernandes Lima", "phone": "11988880002", "email": "theo.lima@example.com"},
    ]
    released = [
        {"full_name": "Sofia Ribeiro Dias", "phone": "11988880003", "email": "sofia.dias@example.com"},
    ]
    for data in pre_reg + released:
        candidate = await service.create_candidate(
            actor_id=SECRETARY_ID,
            school_id=SCHOOL_ID,
            academic_year=ACADEMIC_YEAR,
            unit_id="MAIN_UNIT",
            segment_id="MEDIO",
            grade_level="3ª Série",
            classroom_id=None,
            **data,
        )
        if data in released:
            await service.release_diagnostic(candidate=candidate, actor_id=SECRETARY_ID)
    await session.commit()
    print(f"[reception] {len(pre_reg)} pré-cadastro(s), {len(released)} diagnóstico liberado")


async def seed_content_question_links(session: AsyncSession) -> None:
    """Link every firmly-classified question to its CatalogNode via
    ContentQuestionLink - the table the diagnostic/practice/recommendation
    selection system actually reads (QuestionSelectionRepository), as
    opposed to PedagogicalClassification, which only drives the question
    bank's display/labeling. Without this, diagnostic/practice/recommendation
    always show "nenhum recurso disponivel" even for classified questions."""
    from agente_ia_edu.db.models import ContentQuestionLink, PedagogicalClassification
    from agente_ia_edu.repositories.catalog import ContentQuestionLinkRepository
    from agente_ia_edu.services.catalog import ContentQuestionLinkService

    existing = await session.scalar(select(ContentQuestionLink.id).limit(1))
    if existing:
        print("[content-question-links] already seeded, skipping")
        return

    rows = (await session.execute(
        select(PedagogicalClassification.question_version_id, PedagogicalClassification.content).where(
            PedagogicalClassification.status == "CLASSIFIED",
            PedagogicalClassification.lifecycle == "ACTIVE",
            PedagogicalClassification.metadata_["taxonomy_version"].as_string() == "curriculum-v2",
        )
    )).all()

    content_codes = {content for _, content in rows}
    node_by_code = {
        n.code: n
        for n in (await session.scalars(select(CatalogNode).where(CatalogNode.code.in_(content_codes)))).all()
    }

    service = ContentQuestionLinkService(ContentQuestionLinkRepository(session))
    linked = 0
    for question_version_id, content_code in rows:
        node = node_by_code.get(content_code)
        if node is None:
            continue
        try:
            await service.link(session, content_node_id=node.id, question_version_id=question_version_id)
            linked += 1
        except ValueError:
            pass
    await session.commit()
    print(f"[content-question-links] linked {linked} classified question(s) to their catalog content")


async def main() -> None:
    database_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_factory() as session:
        await ensure_school(session)
        await session.commit()
        await ensure_roles(session)
        nodes = await get_content_nodes(session)
        await seed_lessons(session, nodes)
        await session.commit()
        await seed_mastery(session, nodes)
        await seed_pedagogical_context(session, nodes)
        await session.commit()
        await seed_material(session, nodes)
        await seed_activity(session)
        await seed_classified_activities(session)
        await seed_content_question_links(session)
        await seed_reception(session)
    await engine.dispose()
    print("done.")


if __name__ == "__main__":
    asyncio.run(main())
