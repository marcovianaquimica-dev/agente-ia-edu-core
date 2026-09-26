"""
Coordination Portal Domain Service (Phase 12C.2).

Provides manager-level analytics, academic drill-down hierarchy, classroom comparison,
teacher oversight, and pedagogical context tracking for Coordinators and Directors.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime, timedelta, timezone
from typing import Any, Optional

from sqlalchemy import select, and_, or_, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agente_ia_edu.db.models import (
    AdminAuditLog,
    LearningHistory,
    PedagogicalContext,
    PedagogicalRecommendation,
    School,
    StudentContentMastery,
    TeachingLesson,
    UserSchoolLink,
)
from agente_ia_edu.db.models.academic import Class as AcademicClass
from agente_ia_edu.db.models.academic import GradeLevel, Segment, SchoolUnit
from agente_ia_edu.services.admin import AdminRole, AdminScopeType, PlatformAdminService
from agente_ia_edu.services.external_id_resolution import (
    ExternalIdResolver,
    ResolutionState,
    real_classroom_external_ids,
)
from agente_ia_edu.services.knowledge import KnowledgeService
from agente_ia_edu.services.learning_path_policies import DifficultyLevel
from agente_ia_edu.services.recommendation import RecommendationEngine
from agente_ia_edu.services.report_export import ReportExportService
from agente_ia_edu.services.teacher_portal import (
    TeacherPerformancePolicy,
    TeacherPortalService,
)
from agente_ia_edu.services.teaching_context import (
    ScopeAuthorizationError,
    TeachingContextService,
)
from agente_ia_edu.services.teaching_context_policies import RecencyPolicy

logger = logging.getLogger(__name__)


class CoordinationPortalService:
    """Service managing Coordination and Director portal analytics, scope verification, and oversight."""

    def __init__(
        self,
        session: AsyncSession,
        knowledge_service: KnowledgeService,
        teaching_context_service: TeachingContextService,
        teacher_portal_service: TeacherPortalService,
        recommendation_engine: RecommendationEngine,
        performance_policy: TeacherPerformancePolicy | None = None,
    ):
        self.session = session
        self.knowledge_service = knowledge_service
        self.teaching_context_service = teaching_context_service
        self.teacher_portal_service = teacher_portal_service
        self.recommendation_engine = recommendation_engine
        self.performance_policy = performance_policy or TeacherPerformancePolicy()
        self.recency_policy = RecencyPolicy()
        self.admin_service = PlatformAdminService(session)

    # -------------------------------------------------------------------------
    # 1. COORDINATOR SCOPE AUTHORIZATION & CLASSROOM RESOLUTION
    # -------------------------------------------------------------------------

    async def get_coordinator_authorized_scopes(
        self,
        coordinator_id: str,
        school_id: uuid.UUID,
    ) -> dict[str, Any]:
        """Returns authorized scope filters for coordinator_id in school_id."""
        links = await self.admin_service.get_user_active_links(coordinator_id)

        allowed_classrooms = set()
        allowed_grades = set()
        allowed_units = set()
        allowed_segments = set()
        is_global = False

        for link in links:
            if link.role == AdminRole.PLATFORM_ADMIN or (
                link.school_id == school_id and link.role in (AdminRole.DIRECTOR, AdminRole.COORDINATOR)
            ):
                if link.scope_type in (AdminScopeType.PLATFORM, AdminScopeType.SCHOOL):
                    is_global = True
                elif link.scope_type == AdminScopeType.UNIT and link.scope_external_id:
                    allowed_units.add(link.scope_external_id)
                elif link.scope_type == AdminScopeType.SEGMENT and link.scope_external_id:
                    allowed_segments.add(link.scope_external_id)
                elif link.scope_type == AdminScopeType.GRADE_LEVEL and link.scope_external_id:
                    allowed_grades.add(link.scope_external_id)
                elif link.scope_type == AdminScopeType.CLASSROOM and link.scope_external_id:
                    allowed_classrooms.add(link.scope_external_id)

        if is_global:
            stmt = select(TeachingLesson.classroom_id).where(TeachingLesson.school_id == school_id).distinct()
            res = await self.session.execute(stmt)
            classrooms = set(res.scalars().all())

            # Any CLASSROOM-scoped UserSchoolLink (teacher, student, or
            # coordinator) is also real evidence of a classroom's existence -
            # mirrors TeacherPortalService._full_classroom_set's own
            # school-wide query, which this branch used to fall short of.
            stmt_users = select(UserSchoolLink.scope_external_id).where(
                UserSchoolLink.school_id == school_id,
                UserSchoolLink.scope_type == AdminScopeType.CLASSROOM,
                UserSchoolLink.scope_external_id.isnot(None),
            ).distinct()
            res_users = await self.session.execute(stmt_users)
            classrooms.update(res_users.scalars().all())

            # The R0 hierarchy's own real Class rows complete the picture for
            # a freshly onboarded school with no TeachingLesson/CLASSROOM-link
            # history yet. Never an invented placeholder name (the reported
            # bug: a real school's classrooms are named "1EM_A"/"1EM_B", not
            # "TURMA_3A"/"TURMA_3B") - a genuinely empty school now correctly
            # yields an empty set instead.
            classrooms.update(await real_classroom_external_ids(self.session, school_id))

            # Real grade/unit/segment external_ids for this school, never the
            # hardcoded "1ª Série"/"Unidade Principal"/"Ensino Médio" strings.
            # A school with no rows yet at a level (pre-R0, or a real school
            # like Escola ABC that has no SchoolUnit) gets an empty set for
            # that level - which is what verify_coordinator_access actually
            # checks these against (lines below, unreached when is_global is
            # True but real for any future caller); an empty set there
            # correctly denies a filter for a level the school has no real
            # data for, rather than pretending three grades or one unit
            # exist when they do not.
            res_grades = await self.session.execute(
                select(GradeLevel.external_id).where(
                    GradeLevel.school_id == school_id,
                    GradeLevel.external_id.isnot(None),
                )
            )
            res_units = await self.session.execute(
                select(SchoolUnit.external_id).where(
                    SchoolUnit.school_id == school_id,
                    SchoolUnit.external_id.isnot(None),
                )
            )
            res_segments = await self.session.execute(
                select(Segment.external_id).where(
                    Segment.school_id == school_id,
                    Segment.external_id.isnot(None),
                )
            )
            return {
                "is_global": True,
                "allowed_classrooms": classrooms,
                "allowed_grades": set(res_grades.scalars().all()),
                "allowed_units": set(res_units.scalars().all()),
                "allowed_segments": set(res_segments.scalars().all()),
            }

        resolver = ExternalIdResolver(self.session)
        allowed_units = await self._resolved(resolver, school_id, AdminScopeType.UNIT, allowed_units)
        allowed_segments = await self._resolved(resolver, school_id, AdminScopeType.SEGMENT, allowed_segments)
        allowed_grades = await self._resolved(resolver, school_id, AdminScopeType.GRADE_LEVEL, allowed_grades)
        allowed_classrooms = await self._resolved(resolver, school_id, AdminScopeType.CLASSROOM, allowed_classrooms)

        return {
            "is_global": False,
            "allowed_classrooms": allowed_classrooms,
            "allowed_grades": allowed_grades,
            "allowed_units": allowed_units,
            "allowed_segments": allowed_segments,
        }

    @staticmethod
    async def _resolved(
        resolver: "ExternalIdResolver",
        school_id: uuid.UUID,
        scope_type: str,
        codes: set[str],
    ) -> set[str]:
        """Keep only the codes that resolve - once this school's hierarchy
        actually has rows at this level, and never to the point of emptying
        the set.

        A link's scope_external_id is free text nothing validates at write
        time, and R0's hierarchy tables are new: no school's existing data
        was migrated into them yet. Filtering before that backfill happens
        would strip access from every school that has not been migrated -
        today, every school. has_any_entities is the guard against that.

        Dropping what does not resolve narrows an allow-set and never widens
        it *inside this function* - but an empty set does not read as "no
        access" one call up. Before Fase 3C onda 1, verify_coordinator_access
        skipped its check when the set was falsy, and _resolve_scope_classrooms
        fell through to every classroom in the school; both read empty as "not
        restricted". Turning a narrow-but-stale set into an empty one would
        therefore have widened access at the caller. So when every code at a
        level fails to resolve, the unfiltered set is kept: a set of codes
        that match no real entity, which is exactly what this function was
        handed and exactly what runs in production today. A partial drop -
        some codes valid, some not - still narrows normally.

        Those two callers decide access. Fase 3C onda 1 fixed "empty means no
        access" at the reading end for both of them: verify_coordinator_access
        (commit c06b198) now keys off is_global instead of allow-set
        truthiness, and _resolve_scope_classrooms (commit b6d6a27) no longer
        falls through to every classroom in the school on an empty allow-set.
        """
        if not codes:
            return codes
        if not await resolver.has_any_entities(school_id, scope_type):
            return codes
        resolutions = await resolver.resolve_many(school_id, scope_type, codes)
        resolved = {
            code for code in codes
            if resolutions[code].state == ResolutionState.RESOLVED
        }
        if not resolved:
            logger.warning(
                "No %s scope code resolved for school %s (%d code(s) checked); "
                "keeping the unfiltered set, because an empty allow-set reads "
                "as unrestricted downstream.",
                scope_type,
                school_id,
                len(codes),
            )
            return codes
        return resolved

    async def _resolve_scope_classrooms(
        self,
        coordinator_id: str,
        school_id: uuid.UUID,
    ) -> list[str]:
        """Resolves classroom_ids in the coordinator's authorized scope (coordinator-scoped, not teacher-scoped).

        Note (not fixed here): this only reads scopes["allowed_classrooms"].
        A coordinator whose real scope is GRADE_LEVEL or UNIT - with no
        CLASSROOM link at all - resolves to [] here even though
        verify_coordinator_access correctly authorizes them for their
        grade/unit. Deriving classroom membership from grade/unit through the
        academic hierarchy is substantial new logic and belongs to a future
        wave, not this one.
        """
        scopes = await self.get_coordinator_authorized_scopes(coordinator_id, school_id)
        if scopes["is_global"]:
            # get_coordinator_authorized_scopes returns allowed_classrooms as a
            # set, and Python randomizes string hashing per process, so its
            # iteration order is not stable across runs. sorted() gives a
            # deterministic order for build_classroom_items (see call sites
            # around lines 429 and 486) and the API response it feeds -
            # never re-query TeachingLesson alone here, which would drop the
            # CLASSROOM-link and real-Class-row sources already unioned into
            # allowed_classrooms above (and reintroduce the placeholder gap).
            return sorted(scopes["allowed_classrooms"])
        return list(scopes["allowed_classrooms"])

    async def verify_coordinator_access(
        self,
        *,
        coordinator_id: str,
        school_id: uuid.UUID,
        classroom_id: str | None = None,
        grade_level: str | None = None,
        unit_id: str | None = None,
    ) -> bool:
        """Verifies that coordinator_id is authorized for the requested scope filters."""
        links = await self.admin_service.get_user_active_links(coordinator_id)
        if links:
            has_coord_access = any(
                l.role == AdminRole.PLATFORM_ADMIN
                or (l.school_id == school_id and l.role in (AdminRole.DIRECTOR, AdminRole.COORDINATOR))
                for l in links
            )
            if not has_coord_access:
                raise ScopeAuthorizationError(
                    f"User '{coordinator_id}' is not authorized as coordinator/director for school '{school_id}'."
                )
        else:
            raise ScopeAuthorizationError(f"User '{coordinator_id}' has no active coordination bindings.")

        scopes = await self.get_coordinator_authorized_scopes(coordinator_id, school_id)
        if scopes["is_global"]:
            return True

        if classroom_id and classroom_id not in scopes["allowed_classrooms"]:
            raise ScopeAuthorizationError(
                f"Coordinator '{coordinator_id}' is not authorized for classroom '{classroom_id}' in school '{school_id}'."
            )

        if grade_level and grade_level not in scopes["allowed_grades"]:
            raise ScopeAuthorizationError(
                f"Coordinator '{coordinator_id}' is not authorized for grade '{grade_level}' in school '{school_id}'."
            )

        if unit_id and unit_id not in scopes["allowed_units"]:
            raise ScopeAuthorizationError(
                f"Coordinator '{coordinator_id}' is not authorized for unit '{unit_id}' in school '{school_id}'."
            )

        return True

    async def verify_coordinator_student_scope(
        self,
        *,
        coordinator_id: str,
        school_id: uuid.UUID,
        student_id: str,
    ) -> bool:
        """Verifies that student_id falls within coordinator_id's OWN authorized
        scope in school_id - not just that they hold a coordinator/director role
        at the school.

        get_coordination_dashboard's classroom_id filter already goes through
        verify_coordinator_access(classroom_id=...), which correctly denies a
        classroom outside scopes["allowed_classrooms"]. The by-id lookups
        (get_student_detail_for_coordination, search_students_for_coordination)
        used to skip straight past that: they called verify_coordinator_access
        with no classroom_id (a bare role/school check) and then delegated to
        TeacherPortalService.get_student_detail_for_teacher /
        search_students_in_scope, whose get_teacher_authorized_classrooms
        treats ANY DIRECTOR/COORDINATOR link at the right school_id as fully
        school-wide regardless of that link's own scope_type - so a coordinator
        narrowly scoped to one CLASSROOM could still reach a student of any
        OTHER classroom in the same school. This closes that gap using the same
        get_coordinator_authorized_scopes/is_global primitives the dashboard
        path already relies on.
        """
        await self.verify_coordinator_access(coordinator_id=coordinator_id, school_id=school_id)

        scopes = await self.get_coordinator_authorized_scopes(coordinator_id, school_id)
        if scopes["is_global"]:
            return True

        student_links = await self.admin_service.get_user_active_links(student_id)
        for link in student_links:
            if link.school_id != school_id or link.role != AdminRole.STUDENT:
                continue
            if link.scope_type == AdminScopeType.CLASSROOM and link.scope_external_id in scopes["allowed_classrooms"]:
                return True

        raise ScopeAuthorizationError(
            f"Student '{student_id}' is outside coordinator '{coordinator_id}' authorized scope in school '{school_id}'."
        )

    async def search_students_in_scope(
        self,
        *,
        coordinator_id: str,
        school_id: uuid.UUID,
        query: str,
    ) -> list[dict[str, Any]]:
        """Searches students by ID STRICTLY within the coordinator's own
        authorized scope - mirrors TeacherPortalService.search_students_in_scope,
        but resolved via _resolve_scope_classrooms/is_global (the same
        coordinator-shaped primitives get_coordination_dashboard uses) instead
        of get_teacher_authorized_classrooms, whose DIRECTOR/COORDINATOR branch
        does not respect a CLASSROOM/GRADE/UNIT-restricted link (see
        verify_coordinator_student_scope's docstring for the full shape of the
        gap this closes)."""
        await self.verify_coordinator_access(coordinator_id=coordinator_id, school_id=school_id)

        scopes = await self.get_coordinator_authorized_scopes(coordinator_id, school_id)
        classrooms = await self._resolve_scope_classrooms(coordinator_id, school_id)
        school_wide = scopes["is_global"]
        all_students = await self.teacher_portal_service._fetch_students_in_classrooms(
            school_id, classrooms, school_wide=school_wide,
        )

        q_clean = query.strip().lower()
        matched_students = [sid for sid in all_students if q_clean in sid.lower()]

        all_masteries = await self.teacher_portal_service._fetch_masteries_for_students(matched_students)
        masteries_by_student: dict[str, list[StudentContentMastery]] = {}
        for m in all_masteries:
            masteries_by_student.setdefault(m.external_identity_id, []).append(m)

        results = []
        for sid in matched_students:
            s_masteries = masteries_by_student.get(sid, [])
            s_avg = (sum(float(m.mastery_score) for m in s_masteries) / len(s_masteries)) if s_masteries else 0.0
            results.append({
                "student_id": sid,
                "name": f"Aluno {sid.replace('student:', '').replace('_', ' ').title()}",
                "school_id": str(school_id),
                # First of the coordinator's OWN authorized classrooms, as a
                # display value only - never an invented classroom name when
                # none is known (matches TeacherPortalService's own
                # search_students_in_scope convention for the same case).
                "classroom_id": classrooms[0] if classrooms else "",
                "average_mastery": round(s_avg, 1),
            })

        return results

    # -------------------------------------------------------------------------
    # 2. COORDINATION DASHBOARD AGGREGATOR
    # -------------------------------------------------------------------------

    async def get_coordination_dashboard(
        self,
        *,
        coordinator_id: str,
        school_id: uuid.UUID,
        academic_year: str = "2026",
        unit_id: str | None = None,
        segment_id: str | None = None,
        grade_level: str | None = None,
        classroom_id: str | None = None,
        teacher_id: str | None = None,
        time_period: str = "academic_year",
    ) -> dict[str, Any]:
        """Aggregates coordination dashboard metrics across authorized scopes."""
        # 1. Verify Scope Authorization
        await self.verify_coordinator_access(
            coordinator_id=coordinator_id,
            school_id=school_id,
            classroom_id=classroom_id,
            grade_level=grade_level,
            unit_id=unit_id,
        )

        if classroom_id:
            target_classrooms = [classroom_id]
            # One named classroom: never pad with SCHOOL-wide students who
            # aren't actually assigned to it.
            school_wide = False
        else:
            target_classrooms = await self._resolve_scope_classrooms(coordinator_id, school_id)
            scopes = await self.get_coordinator_authorized_scopes(coordinator_id, school_id)
            school_wide = scopes["is_global"]

        # 2. Fetch Students in Scope
        student_ids = await self.teacher_portal_service._fetch_students_in_classrooms(
            school_id, target_classrooms, school_wide=school_wide,
        )

        # 3. Fetch Teachers in Scope
        teachers = await self.list_coordination_teachers(
            coordinator_id=coordinator_id,
            school_id=school_id,
            academic_year=academic_year,
        )
        if teacher_id:
            teachers = [t for t in teachers if t["teacher_id"] == teacher_id]

        # 4. Fetch Masteries
        masteries = await self.teacher_portal_service._fetch_masteries_for_students(student_ids)

        total_masteries = len(masteries)
        overall_avg = (sum(float(m.mastery_score) for m in masteries) / total_masteries) if total_masteries > 0 else 0.0

        # Distribution breakdown - one bucket per DISTINCT student (by their
        # average across contents), never per mastery row.
        buckets = self.teacher_portal_service._bucket_students_by_mastery(masteries)
        struggling_cnt = buckets["struggling"]
        developing_cnt = buckets["developing"]
        mastered_cnt = buckets["mastered"]
        assessed_students = struggling_cnt + developing_cnt + mastered_cnt

        struggling_pct = round((struggling_cnt / assessed_students * 100.0), 1) if assessed_students > 0 else 0.0
        developing_pct = round((developing_cnt / assessed_students * 100.0), 1) if assessed_students > 0 else 0.0
        mastered_pct = round((mastered_cnt / assessed_students * 100.0), 1) if assessed_students > 0 else 0.0

        # Breakdown by Content
        content_map: dict[uuid.UUID, list[float]] = {}
        for m in masteries:
            content_map.setdefault(m.content_node_id, []).append(float(m.mastery_score))

        content_node_names = await self.teacher_portal_service._fetch_content_node_names(content_map.keys())
        average_mastery_by_content = []
        for node_id, scores in content_map.items():
            c_avg = sum(scores) / len(scores) if scores else 0.0
            c_struggling = sum(1 for s in scores if s < 50.0)
            average_mastery_by_content.append({
                "content_node_id": str(node_id),
                "content_name": content_node_names.get(node_id, "Conteúdo"),
                "class_average_mastery": round(c_avg, 1),
                "students_struggling_count": c_struggling,
                "total_students": len(scores),
            })

        average_mastery_by_content.sort(key=lambda x: x["class_average_mastery"])
        strengths, improvements = self.performance_policy.classify_strengths_and_improvements(average_mastery_by_content)

        # Classrooms Needing Attention
        classrooms_list = await self.teacher_portal_service.build_classroom_items(
            target_classrooms,
            school_id,
            academic_year,
        )
        classrooms_needing_attention = [c for c in classrooms_list if c["average_mastery"] < 70.0]

        # Recent Lessons / Contexts (last 14 days)
        recent_contexts = await self.teaching_context_service.get_active_recent_contexts(
            school_id=school_id,
            classroom_id=classroom_id,
            academic_year=academic_year,
        )

        recent_context_node_names = await self.teacher_portal_service._fetch_content_node_names(
            {ctx.content_node_id for ctx in recent_contexts}
        )
        recent_contexts_payload = []
        for ctx in recent_contexts:
            recent_contexts_payload.append({
                "id": str(ctx.id),
                "content_node_id": str(ctx.content_node_id),
                "content_name": recent_context_node_names.get(ctx.content_node_id, "Conteúdo"),
                "source": ctx.source,
                "classroom_id": ctx.classroom_id,
                "author_id": ctx.author_id,
                "title": ctx.title,
                "recorded_at": ctx.recorded_at.isoformat(),
            })

        # Action Plan Items
        action_plan_items = []
        for imp in improvements[:5]:
            node_id = uuid.UUID(imp["content_node_id"])
            matched_ctx = next((c for c in recent_contexts if c.content_node_id == node_id), None)
            ap_item = self.performance_policy.generate_action_plan_item(
                content_name=imp["content_name"],
                class_avg=imp["class_average_mastery"],
                struggling_count=imp["students_struggling_count"],
                total_students=len(student_ids) or 1,
                recent_lesson_date=matched_ctx.recorded_at if matched_ctx else None,
            )
            action_plan_items.append(ap_item)

        return {
            "coordinator_id": coordinator_id,
            "school_id": str(school_id),
            "academic_year": academic_year,
            "unit_id": unit_id,
            "segment_id": segment_id,
            "grade_level": grade_level,
            "classroom_id": classroom_id,
            "time_period": time_period,
            "total_students": len(student_ids),
            "total_teachers": len(teachers),
            "total_classrooms": len(target_classrooms),
            "overall_mastery_average": round(overall_avg, 1),
            "students_struggling_count": struggling_cnt,
            "students_struggling_percentage": struggling_pct,
            "students_developing_count": developing_cnt,
            "students_developing_percentage": developing_pct,
            "students_mastered_count": mastered_cnt,
            "students_mastered_percentage": mastered_pct,
            "average_mastery_by_content": average_mastery_by_content,
            "classrooms_needing_attention": classrooms_needing_attention,
            "recent_contexts": recent_contexts_payload,
            "top_performing_contents": strengths,
            "needs_attention_contents": improvements,
            "action_plan": action_plan_items,
        }

    # -------------------------------------------------------------------------
    # 3. DRILL-DOWN ACADÊMICO (HIERARCHY)
    # -------------------------------------------------------------------------

    async def get_coordination_hierarchy(
        self,
        *,
        coordinator_id: str,
        school_id: uuid.UUID,
        academic_year: str = "2026",
    ) -> dict[str, Any]:
        """Returns structured academic tree (School -> Unit -> Segment -> Grade -> Classroom) with mastery metrics."""
        await self.verify_coordinator_access(coordinator_id=coordinator_id, school_id=school_id)

        school = await self.session.get(School, school_id)
        school_name = school.name if school else "Escola Partner"

        classroom_ids = await self._resolve_scope_classrooms(coordinator_id, school_id)
        classrooms_data = await self.teacher_portal_service.build_classroom_items(
            classroom_ids,
            school_id,
            academic_year,
        )

        # build_classroom_items already resolves each classroom_id's real
        # Class row when one exists (class_id). Fetch that row's real
        # GradeLevel/Segment/SchoolUnit in one batched query, instead of
        # assuming every classroom sits under one fabricated
        # "MAIN_UNIT"/"MEDIO" pair - a school with no SchoolUnit rows yet
        # (e.g. Escola ABC today) legitimately has classes with no unit at
        # all, and that must show up as "no unit", never as an invented one.
        resolved_class_ids = {
            cls["classroom_id"]: cls["class_id"]
            for cls in classrooms_data
            if cls.get("class_id") is not None
        }

        academic_by_class_id: dict[uuid.UUID, tuple[SchoolUnit | None, Segment, GradeLevel]] = {}
        if resolved_class_ids:
            stmt_academic = (
                select(AcademicClass, GradeLevel, Segment, SchoolUnit)
                .join(GradeLevel, AcademicClass.grade_level_id == GradeLevel.id)
                .join(Segment, GradeLevel.segment_id == Segment.id)
                .outerjoin(SchoolUnit, AcademicClass.school_unit_id == SchoolUnit.id)
                .where(AcademicClass.id.in_(resolved_class_ids.values()))
            )
            res_academic = await self.session.execute(stmt_academic)
            for klass, grade, segment, unit in res_academic.all():
                academic_by_class_id[klass.id] = (unit, segment, grade)

        NO_UNIT_NAME = "Sem unidade cadastrada"
        NO_SEGMENT_NAME = "Sem segmento cadastrado"
        NO_GRADE_NAME = "Sem série cadastrada"
        _NO_UNIT_KEY = "__NO_UNIT__"
        _NO_SEGMENT_KEY = "__NO_SEGMENT__"

        # unit_key -> {unit_id, unit_name, segments: {segment_key -> {...}}}
        units_map: dict[str, dict[str, Any]] = {}

        for cls in classrooms_data:
            class_id = resolved_class_ids.get(cls["classroom_id"])
            academic = academic_by_class_id.get(class_id) if class_id else None

            if academic is not None:
                unit, segment, grade = academic
                unit_key = str(unit.id) if unit is not None else _NO_UNIT_KEY
                unit_id = str(unit.id) if unit is not None else None
                unit_name = unit.name if unit is not None else NO_UNIT_NAME
                segment_key = str(segment.id)
                segment_id = str(segment.id)
                segment_name = segment.name
                grade_name = grade.name
            else:
                unit_key = _NO_UNIT_KEY
                unit_id = None
                unit_name = NO_UNIT_NAME
                segment_key = _NO_SEGMENT_KEY
                segment_id = None
                segment_name = NO_SEGMENT_NAME
                grade_name = NO_GRADE_NAME

            # The nested classroom object must agree with the tree it's
            # embedded in - never keep build_classroom_items' own
            # placeholder "grade_level"/"segment"/"unit" fields (out of
            # scope here; they belong to teacher_portal.py) once this
            # function has real values to report instead.
            cls_real = dict(cls)
            cls_real["grade_level"] = grade_name
            cls_real["segment"] = segment_name
            cls_real["unit"] = unit_name

            unit_entry = units_map.setdefault(unit_key, {
                "unit_id": unit_id,
                "unit_name": unit_name,
                "segments": {},
            })
            segment_entry = unit_entry["segments"].setdefault(segment_key, {
                "segment_id": segment_id,
                "segment_name": segment_name,
                "grades": {},
            })
            segment_entry["grades"].setdefault(grade_name, []).append(cls_real)

        units_list = []
        for unit_entry in units_map.values():
            segments_list = []
            for segment_entry in unit_entry["segments"].values():
                grades_list = []
                for g_name, cls_items in segment_entry["grades"].items():
                    g_students = sum(c["student_count"] for c in cls_items)
                    g_avg = (sum(c["average_mastery"] for c in cls_items) / len(cls_items)) if cls_items else 0.0
                    grades_list.append({
                        "grade_level": g_name,
                        "student_count": g_students,
                        "average_mastery": round(g_avg, 1),
                        "classrooms": cls_items,
                    })
                segments_list.append({
                    "segment_id": segment_entry["segment_id"],
                    "segment_name": segment_entry["segment_name"],
                    "grades": grades_list,
                })
            units_list.append({
                "unit_id": unit_entry["unit_id"],
                "unit_name": unit_entry["unit_name"],
                "segments": segments_list,
            })

        return {
            "school_id": str(school_id),
            "school_name": school_name,
            "academic_year": academic_year,
            "units": units_list,
        }

    # -------------------------------------------------------------------------
    # 4. CLASSROOM COMPARISON
    # -------------------------------------------------------------------------

    async def compare_classrooms(
        self,
        *,
        coordinator_id: str,
        school_id: uuid.UUID,
        academic_year: str = "2026",
    ) -> list[dict[str, Any]]:
        """Returns side-by-side comparison metrics for all classrooms in coordinator scope."""
        await self.verify_coordinator_access(coordinator_id=coordinator_id, school_id=school_id)

        classroom_ids = await self._resolve_scope_classrooms(coordinator_id, school_id)
        classrooms = await self.teacher_portal_service.build_classroom_items(
            classroom_ids,
            school_id,
            academic_year,
        )

        # Batched once for every classroom in scope, instead of a
        # student-roster query plus a mastery query per classroom inside the
        # loop below (comparing N classrooms used to cost 2*N extra queries
        # on top of build_classroom_items above).
        stmt_roster = (
            select(UserSchoolLink.scope_external_id, UserSchoolLink.external_user_id)
            .where(
                UserSchoolLink.school_id == school_id,
                UserSchoolLink.role == AdminRole.STUDENT,
                UserSchoolLink.active.is_(True),
                UserSchoolLink.scope_external_id.in_(classroom_ids),
            )
            .distinct()
        )
        res_roster = await self.session.execute(stmt_roster)
        students_by_classroom: dict[str, list[str]] = {}
        for cls_id, student_id in res_roster.all():
            students_by_classroom.setdefault(cls_id, []).append(student_id)

        all_student_ids = [sid for ids in students_by_classroom.values() for sid in ids]
        all_masteries = await self.teacher_portal_service._fetch_masteries_for_students(all_student_ids)
        masteries_by_student: dict[str, list[StudentContentMastery]] = {}
        for m in all_masteries:
            masteries_by_student.setdefault(m.external_identity_id, []).append(m)

        comparison_list = []
        for cls in classrooms:
            cls_id = cls["classroom_id"]
            # One classroom's own comparison row: never pad with SCHOOL-wide
            # students who aren't actually assigned to it.
            student_ids = students_by_classroom.get(cls_id, [])
            masteries = [m for sid in student_ids for m in masteries_by_student.get(sid, [])]

            # Distribution breakdown - one bucket per DISTINCT student (by
            # their average across contents), never per mastery row.
            buckets = self.teacher_portal_service._bucket_students_by_mastery(masteries)
            s_cnt = buckets["struggling"]
            d_cnt = buckets["developing"]
            m_cnt = buckets["mastered"]

            comparison_list.append({
                "classroom_id": cls_id,
                "name": cls["name"],
                "student_count": cls["student_count"],
                "average_mastery": cls["average_mastery"],
                "struggling_count": s_cnt,
                "developing_count": d_cnt,
                "mastered_count": m_cnt,
                "priority_contents": cls.get("priority_contents", []),
            })

        comparison_list.sort(key=lambda x: x["average_mastery"])
        return comparison_list

    # -------------------------------------------------------------------------
    # 5. TEACHERS OVERSIGHT
    # -------------------------------------------------------------------------

    async def list_coordination_teachers(
        self,
        *,
        coordinator_id: str,
        school_id: uuid.UUID,
        academic_year: str = "2026",
    ) -> list[dict[str, Any]]:
        """Lists teachers within coordinator scope, assigned classrooms, student counts, and class averages.

        Batched below: this used to call get_teacher_authorized_classrooms,
        _resolve_school_wide, _fetch_students_in_classrooms and
        _fetch_masteries_for_students once PER teacher inside the loop -
        each is its own DB round trip, so a school with T teachers cost
        roughly 8*T queries (measured: 16 queries for 1 teacher, 96 for 11).
        Every teacher's active links, every candidate student in the
        school, and every one of their mastery rows are now fetched in one
        batched query each, up front, and partitioned per teacher in
        Python. get_teacher_authorized_classrooms is still called once per
        teacher - its scope resolution is real per-teacher authorization
        logic - but it's given a shared `scope_cache` so the two
        school-wide sub-queries it can trigger run at most once per
        school_id total instead of once per teacher. list_teacher_lessons
        still runs once per teacher: it lives in TeachingContextService,
        outside this file, and already avoids its own N+1 via selectinload.
        """
        await self.verify_coordinator_access(coordinator_id=coordinator_id, school_id=school_id)

        # Query teachers linked to school
        stmt = (
            select(UserSchoolLink)
            .where(
                UserSchoolLink.school_id == school_id,
                UserSchoolLink.role == AdminRole.TEACHER,
                UserSchoolLink.active.is_(True),
            )
        )
        res = await self.session.execute(stmt)
        teacher_links = list(res.scalars().all())

        teacher_ids = []
        seen_teachers = set()
        for link in teacher_links:
            tid = link.external_user_id
            if tid in seen_teachers:
                continue
            seen_teachers.add(tid)
            teacher_ids.append(tid)

        if not teacher_ids:
            # A school with no TEACHER links has no teachers to show - never
            # a fabricated one, regardless of how real the rest of its data is.
            return []

        # Every teacher's own active links in one query, instead of one
        # query per teacher inside get_teacher_authorized_classrooms /
        # _teacher_is_school_wide_authorized below.
        stmt_links = (
            select(UserSchoolLink)
            .where(
                UserSchoolLink.external_user_id.in_(teacher_ids),
                UserSchoolLink.active.is_(True),
            )
            .options(selectinload(UserSchoolLink.school))
        )
        res_links = await self.session.execute(stmt_links)
        links_by_teacher: dict[str, list[UserSchoolLink]] = {}
        for link_row in res_links.scalars().all():
            links_by_teacher.setdefault(link_row.external_user_id, []).append(link_row)

        scope_cache: dict[str, Any] = {}
        teacher_scope: dict[str, tuple[list[str], bool]] = {}
        for tid in teacher_ids:
            t_links = links_by_teacher.get(tid, [])
            cls_ids = await self.teacher_portal_service.get_teacher_authorized_classrooms(
                tid, school_id, links=t_links, scope_cache=scope_cache,
            )
            # cls_ids is THIS teacher's own full authorized scope - reflect
            # their own real school_wide status, not the coordinator's.
            teacher_school_wide = self.teacher_portal_service._teacher_is_school_wide_authorized(t_links, school_id)
            teacher_scope[tid] = (cls_ids, teacher_school_wide)

        # Every active STUDENT link in the school, fetched once and matched
        # against each teacher's own (classrooms, school_wide) in Python -
        # the same OR-of-conditions _fetch_students_in_classrooms applies
        # per call, just without a round trip per teacher.
        stmt_students = select(
            UserSchoolLink.external_user_id,
            UserSchoolLink.scope_type,
            UserSchoolLink.scope_external_id,
        ).where(
            UserSchoolLink.school_id == school_id,
            UserSchoolLink.role == AdminRole.STUDENT,
            UserSchoolLink.active.is_(True),
        )
        res_students = await self.session.execute(stmt_students)
        all_student_rows = res_students.all()

        def _students_for(cls_ids: list[str], school_wide: bool) -> list[str]:
            if not cls_ids:
                return []
            cls_set = set(cls_ids)
            matched: set[str] = set()
            for uid, s_type, s_ext in all_student_rows:
                if s_ext in cls_set or (school_wide and s_type in (AdminScopeType.SCHOOL, AdminScopeType.PLATFORM)):
                    matched.add(uid)
            return list(matched)

        students_by_teacher = {
            tid: _students_for(cls_ids, school_wide)
            for tid, (cls_ids, school_wide) in teacher_scope.items()
        }

        union_student_ids = sorted({sid for ids in students_by_teacher.values() for sid in ids})
        all_masteries = await self.teacher_portal_service._fetch_masteries_for_students(union_student_ids)
        masteries_by_student: dict[str, list[StudentContentMastery]] = {}
        for m in all_masteries:
            masteries_by_student.setdefault(m.external_identity_id, []).append(m)

        teachers_payload = []
        for tid in teacher_ids:
            cls_ids, _ = teacher_scope[tid]
            student_ids = students_by_teacher[tid]
            masteries = [m for sid in student_ids for m in masteries_by_student.get(sid, [])]
            t_avg = (sum(float(m.mastery_score) for m in masteries) / len(masteries)) if masteries else 0.0

            # Count recent lessons
            lessons = await self.teaching_context_service.list_teacher_lessons(
                teacher_id=tid,
                school_id=school_id,
                academic_year=academic_year,
            )

            teachers_payload.append({
                "teacher_id": tid,
                "name": f"Prof. {tid.replace('user:', '').replace('teacher:', '').replace('_', ' ').title()}",
                "school_id": str(school_id),
                "assigned_classrooms": cls_ids,
                "total_students": len(student_ids),
                "classrooms_average_mastery": round(t_avg, 1),
                "recent_lessons_count": len(lessons),
            })

        return teachers_payload

    # -------------------------------------------------------------------------
    # 6. CONTEXTS LIST & EXPORTATION
    # -------------------------------------------------------------------------

    async def list_coordination_contexts(
        self,
        *,
        coordinator_id: str,
        school_id: uuid.UUID,
        classroom_id: str | None = None,
        academic_year: str = "2026",
    ) -> list[dict[str, Any]]:
        """Lists active pedagogical contexts recorded by teachers, coordination, or school plans."""
        await self.verify_coordinator_access(
            coordinator_id=coordinator_id,
            school_id=school_id,
            classroom_id=classroom_id,
        )

        contexts = await self.teaching_context_service.get_active_recent_contexts(
            school_id=school_id,
            classroom_id=classroom_id,
            academic_year=academic_year,
        )

        context_node_names = await self.teacher_portal_service._fetch_content_node_names(
            {ctx.content_node_id for ctx in contexts}
        )
        payload = []
        for ctx in contexts:
            payload.append({
                "id": str(ctx.id),
                "content_node_id": str(ctx.content_node_id),
                "content_name": context_node_names.get(ctx.content_node_id, "Conteúdo"),
                "source": ctx.source,
                "classroom_id": ctx.classroom_id,
                "author_id": ctx.author_id,
                "title": ctx.title,
                "description": ctx.description,
                "recorded_at": ctx.recorded_at.isoformat(),
            })

        return payload

    async def export_coordination_report(
        self,
        *,
        coordinator_id: str,
        school_id: uuid.UUID,
        academic_year: str = "2026",
        classroom_id: str | None = None,
        export_format: str = "pdf",
    ) -> dict[str, Any]:
        """Generates structured coordination export payload for PDF or XLSX."""
        dashboard = await self.get_coordination_dashboard(
            coordinator_id=coordinator_id,
            school_id=school_id,
            academic_year=academic_year,
            classroom_id=classroom_id,
        )
        return ReportExportService.export_classroom_report(dashboard, export_format=export_format)
