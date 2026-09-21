"""PHASE 24 - StudySessionService: the ORCHESTRATION layer.

It composes, never recreates:
    PHASE 21 AdaptiveLearningPathService  -> what to work on + state
    PHASE 23 MaterialAvailabilityService  -> is there material for STUDY
    PHASE 24 StudySessionPlanner          -> the ordered plan (pure, deterministic)
    PHASE 22 AdaptivePracticeService      -> a PRACTICE block's real activity (lazy)
    PHASE 17 player / PHASE 18 correction  -> executed via the existing endpoints

Persistence (study_sessions) exists only for resume (s16) + idempotency (s24).
ZERO AI. Multi-tenant: students only ever touch their own sessions; a
coordinator only within their verified school scope (reused authz).
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm.attributes import flag_modified

from ..db.models import StudySession
from ..db.models.admin import UserSchoolLink
from ..db.models.catalog import CatalogNode
from .adaptive_learning_path import AdaptiveLearningPathService
from .adaptive_practice import (
    AdaptivePracticeService,
    PracticeError,
    MIN_QUESTIONS as _PRACTICE_MIN_Q,
)
from .material_availability import MaterialAvailabilityService
from .question_list_store import Requester
from .study_session_planner import (
    BLOCK_BREAK,
    BLOCK_PRACTICE,
    StudySessionPlanner,
    StudySessionPlanPolicy,
)
from .teaching_context import ScopeAuthorizationError, TeachingContextService

SOURCE_STUDENT = "STUDENT_DEFINED"
SOURCE_SCHOOL = "SCHOOL_DEFINED"

ST_SCHEDULED = "SCHEDULED"
ST_READY = "READY"
ST_IN_PROGRESS = "IN_PROGRESS"
ST_COMPLETED = "COMPLETED"
ST_CANCELLED = "CANCELLED"
_ACTIVE_STATUSES = (ST_SCHEDULED, ST_READY, ST_IN_PROGRESS)

BL_PENDING, BL_ACTIVE, BL_DONE, BL_SKIPPED = "PENDING", "ACTIVE", "DONE", "SKIPPED"


class StudySessionError(ValueError):
    """422."""


class StudySessionNotFound(LookupError):
    """404."""


class StudySessionAuthError(PermissionError):
    """403."""


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _today() -> str:
    return _now().date().isoformat()


class StudySessionService:
    def __init__(self, session: AsyncSession, policy: StudySessionPlanPolicy | None = None) -> None:
        self._session = session
        self.policy = policy or StudySessionPlanPolicy.default()
        self._planner = StudySessionPlanner(self.policy)

    # ================= student =================

    async def get_today(self, student_external_id: str, *, requester: Requester) -> dict:
        self._authz_self(student_external_id, requester)
        row = await self._active_session(student_external_id, prefer_source=SOURCE_SCHOOL)
        if row is None:
            # No SCHEDULED/READY/IN_PROGRESS row left - but a session finished
            # (or cancelled) earlier TODAY must still be reported, or the
            # frontend's "sessão concluída" screen (renderSsDone) can never be
            # reached again after a page reload: has_session would silently
            # flip back to False and the student would be re-prompted as if
            # nothing had happened today (s16 - resume/reload must be lossless).
            row = await self._active_session(
                student_external_id, prefer_source=SOURCE_SCHOOL, statuses=None)
        if row is None:
            return {"student_external_id": student_external_id, "has_session": False,
                    "prompt_for_time": True, "session": None}
        return {"student_external_id": student_external_id, "has_session": True,
                "prompt_for_time": row.source == SOURCE_STUDENT and row.status == ST_SCHEDULED,
                "session": self._view(row)}

    async def create_student_session(
        self, student_external_id: str, *, requester: Requester,
        available_minutes: int | None = None, no_timer: bool = False,
        target_content_codes: list[str] | None = None,
    ) -> dict:
        self._authz_self(student_external_id, requester)

        # a mandatory school session cannot be replaced by a free one (s20)
        school_row = await self._active_session(student_external_id, only_source=SOURCE_SCHOOL)
        if school_row is not None:
            raise StudySessionError(
                "Você já tem um momento de aprendizado programado pela coordenação hoje.")

        # idempotency (s24): reuse an existing active free session for today
        existing = await self._active_session(student_external_id, only_source=SOURCE_STUDENT)
        if existing is not None and existing.status in (ST_SCHEDULED, ST_READY):
            return self._view(existing)

        timer_mode = "UNTIMED" if no_timer else "TIMED"
        eff = 0
        if not no_timer:
            if available_minutes is None:
                raise StudySessionError("Informe o tempo disponível ou escolha 'sem tempo definido'.")
            available_minutes = int(available_minutes)
            if not (self.policy.session_min_minutes <= available_minutes <= self.policy.session_max_minutes):
                raise StudySessionError(
                    f"O tempo deve estar entre {self.policy.session_min_minutes} e "
                    f"{self.policy.session_max_minutes} minutos.")
            eff = available_minutes

        codes = await self._validate_codes(target_content_codes)
        plan = await self._build_plan(
            student_external_id, requester, effective_minutes=eff, timer_mode=timer_mode,
            breaks=[], target_content_codes=codes)

        row = StudySession(
            student_external_id=student_external_id, source=SOURCE_STUDENT,
            school_id=UUID(str(requester.school_id)) if requester.school_id else None,
            scope_type="STUDENT", scope_external_id=student_external_id,
            created_by_external_id=student_external_id, session_date=_today(),
            timer_mode=timer_mode, available_minutes=available_minutes,
            break_minutes=plan["break_minutes"],
            effective_study_minutes=plan["effective_study_minutes"],
            target_content_codes=plan["target_content_codes"] or None,
            config={"breaks": []}, plan=plan,
            status=ST_READY, current_block_index=0,
        )
        self._session.add(row)
        await self._session.flush()
        view = self._view(row)
        await self._session.commit()
        return view

    async def get_session(self, student_external_id: str, session_id: UUID, *,
                          requester: Requester) -> dict:
        self._authz_self(student_external_id, requester)
        row = await self._session.get(StudySession, session_id)
        if row is None:
            raise StudySessionNotFound(str(session_id))
        if row.student_external_id != student_external_id:
            raise StudySessionAuthError("this study session does not belong to you")
        return self._view(row)

    async def start_session(self, student_external_id: str, session_id: UUID, *,
                            requester: Requester) -> dict:
        row = await self._own_row(student_external_id, session_id, requester)
        if row.status == ST_COMPLETED:
            raise StudySessionError("Este momento de aprendizado já foi concluído.")
        if row.status == ST_CANCELLED:
            raise StudySessionError("Este momento de aprendizado foi cancelado.")
        if row.status != ST_IN_PROGRESS:
            row.status = ST_IN_PROGRESS
            row.started_at = row.started_at or _now()
            row.current_block_index = 0
            self._activate_first_pending(row)
            row.updated_at = _now()
        view = self._view(row)
        await self._session.commit()
        return view

    async def start_block(self, student_external_id: str, session_id: UUID, index: int, *,
                          requester: Requester) -> dict:
        row = await self._own_row(student_external_id, session_id, requester)
        if row.status not in (ST_IN_PROGRESS, ST_READY):
            raise StudySessionError("Inicie o momento de aprendizado antes de abrir um bloco.")
        blocks = list((row.plan or {}).get("blocks", []))
        block = self._block_at(blocks, index)
        if block["status"] == BL_DONE:
            return self._view(row)

        # PHASE 22 create_practice commits internally -> it expires our ORM row.
        # Do it first, then re-load a fresh row and apply the mutation.
        practice_result = None
        if block["block_type"] == BLOCK_PRACTICE and not block.get("practice_id"):
            practice_result = await self._materialize_practice(
                student_external_id, requester,
                block["content_code"], block.get("requested_questions"))
            row = await self._session.get(StudySession, session_id)
            blocks = list((row.plan or {}).get("blocks", []))
            block = self._block_at(blocks, index)

        if practice_result is not None:
            if not practice_result["ok"]:
                block["action_available"] = False
                block["status"] = BL_SKIPPED
                block["action_note"] = practice_result["note"]
                nxt = next((b["index"] for b in blocks if b["status"] == BL_PENDING), None)
                row.current_block_index = nxt if nxt is not None else len(blocks)
                row.plan = {**(row.plan or {}), "blocks": blocks}; flag_modified(row, "plan")
                row.updated_at = _now()
                view = self._view(row)
                await self._session.commit()
                return view
            block["practice_id"] = practice_result["practice_id"]
            block["assignment_id"] = practice_result["assignment_id"]
            block["question_count"] = practice_result["question_count"]
            if practice_result.get("note"):
                block["action_note"] = practice_result["note"]

        block["status"] = BL_ACTIVE
        block["started_at"] = block.get("started_at") or _now().isoformat()
        row.current_block_index = index
        if row.status == ST_READY:
            row.status = ST_IN_PROGRESS
            row.started_at = row.started_at or _now()
        row.plan = {**(row.plan or {}), "blocks": blocks}; flag_modified(row, "plan")
        row.updated_at = _now()
        view = self._view(row)
        await self._session.commit()
        return view

    async def complete_block(self, student_external_id: str, session_id: UUID, index: int, *,
                             requester: Requester, skipped: bool = False) -> dict:
        row = await self._own_row(student_external_id, session_id, requester)
        blocks = (row.plan or {}).get("blocks", [])
        block = self._block_at(blocks, index)
        if block["status"] != BL_DONE:
            block["status"] = BL_SKIPPED if skipped else BL_DONE
            block["completed_at"] = _now().isoformat()
        nxt = next((b["index"] for b in blocks if b["status"] == BL_PENDING), None)
        if nxt is not None:
            row.current_block_index = nxt
        else:
            row.current_block_index = len(blocks)
        row.plan = {**(row.plan or {}), "blocks": blocks}; flag_modified(row, "plan")
        row.updated_at = _now()
        view = self._view(row)
        await self._session.commit()
        return view

    async def complete_session(self, student_external_id: str, session_id: UUID, *,
                               requester: Requester) -> dict:
        row = await self._own_row(student_external_id, session_id, requester)
        if row.status != ST_COMPLETED:
            row.status = ST_COMPLETED
            row.completed_at = row.completed_at or _now()
            for b in (row.plan or {}).get("blocks", []):
                if b["status"] in (BL_PENDING, BL_ACTIVE):
                    b["status"] = BL_SKIPPED
            row.plan = {**(row.plan or {}), "blocks": (row.plan or {}).get("blocks", [])}; flag_modified(row, "plan")
            row.updated_at = _now()
        view = self._view(row)
        await self._session.commit()
        return view

    # ================= coordination =================

    async def create_coordination_sessions(
        self, coordinator_external_id: str, *, school_id: str,
        target_type: str, target_id: str, session_date: str,
        start_at: str, end_at: str,
        target_content_codes: list[str] | None = None,
        breaks: list[dict] | None = None,
    ) -> dict:
        tcs = TeachingContextService(self._session)
        # A SCHOOL-scoped coordinator/director covers every classroom; a
        # classroom-scoped teacher is only authorised for that classroom. Try the
        # school-wide check first, then fall back to the classroom check.
        try:
            await tcs.verify_coordinator_scope(
                coordinator_id=coordinator_external_id, school_id=UUID(str(school_id)))
        except ScopeAuthorizationError as school_exc:
            if (target_type or "").upper() == "CLASSROOM":
                try:
                    await tcs.verify_teacher_classroom_scope(
                        teacher_id=coordinator_external_id, school_id=UUID(str(school_id)),
                        classroom_id=str(target_id))
                except ScopeAuthorizationError as exc:
                    raise StudySessionAuthError(str(exc)) from exc
            else:
                raise StudySessionAuthError(str(school_exc)) from school_exc

        start_dt = _parse_dt(start_at)
        end_dt = _parse_dt(end_at)
        if start_dt is None or end_dt is None or end_dt <= start_dt:
            raise StudySessionError("Janela inválida: informe início e fim coerentes.")
        window_minutes = int((end_dt - start_dt).total_seconds() // 60)
        if window_minutes < self.policy.session_min_minutes:
            raise StudySessionError(
                f"A janela precisa ter ao menos {self.policy.session_min_minutes} minutos.")

        norm_breaks, break_minutes = self._normalize_breaks(breaks, start_dt, end_dt, window_minutes)
        effective = window_minutes - break_minutes
        if effective <= 0:
            raise StudySessionError("Os intervalos consomem toda a janela — reduza os intervalos.")

        codes = await self._validate_codes(target_content_codes)
        students = await self._students_for_target(school_id, target_type, target_id)
        if not students:
            raise StudySessionError("Nenhum aluno encontrado para o destino informado.")

        results = []
        skipped = 0
        content_names: list[str] = []
        for sid in students:
            row = await self._active_session(sid, only_source=SOURCE_SCHOOL,
                                             on_date=session_date)
            # A student who already STARTED (or finished) today's SCHOOL
            # session must not have their real progress silently discarded by
            # a coordinator refreshing the classroom's window/content - same
            # idempotency contract create_student_session already honours for
            # STUDENT_DEFINED sessions (never reset an active session). Only
            # a not-yet-started row (SCHEDULED/READY, or none yet) is safe to
            # (re)plan.
            if row is not None and row.status not in (ST_SCHEDULED, ST_READY):
                skipped += 1
                results.append({"student_external_id": sid, "session_id": str(row.id),
                                "effective_study_minutes": row.effective_study_minutes,
                                "break_minutes": row.break_minutes,
                                "skipped": True,
                                "skipped_reason": "já iniciado ou concluído hoje — progresso preservado"})
                continue
            plan = await self._build_plan(
                sid, self._student_requester(sid, school_id),
                effective_minutes=effective, timer_mode="TIMED",
                breaks=norm_breaks, target_content_codes=codes)
            content_names = plan["target_content_names"]
            if row is None:
                row = StudySession(student_external_id=sid, source=SOURCE_SCHOOL,
                                   school_id=UUID(str(school_id)))
                self._session.add(row)
            row.scope_type = (target_type or "CLASSROOM").upper()
            row.scope_external_id = str(target_id)
            row.created_by_external_id = coordinator_external_id
            row.session_date = session_date
            row.timer_mode = "TIMED"
            row.start_at = start_dt
            row.end_at = end_dt
            row.available_minutes = window_minutes
            row.break_minutes = plan["break_minutes"]
            row.effective_study_minutes = plan["effective_study_minutes"]
            # reflect what the plan actually targeted - if the coordinator left
            # this open, the platform's own choice (from the Learning Path) is
            # still worth showing the student, not just the empty input (s2/s14).
            row.target_content_codes = plan["target_content_codes"] or None
            row.config = {"breaks": norm_breaks}
            row.plan = plan
            row.status = ST_SCHEDULED
            row.current_block_index = 0
            row.started_at = None
            row.completed_at = None
            row.updated_at = _now()
            await self._session.flush()
            results.append({"student_external_id": sid, "session_id": str(row.id),
                            "effective_study_minutes": row.effective_study_minutes,
                            "break_minutes": row.break_minutes})
        summary = {
            "created_or_updated": len(results) - skipped,
            "skipped_in_progress": skipped,
            "window_minutes": window_minutes, "break_minutes": break_minutes,
            "effective_study_minutes": effective,
            "target_content_codes": codes, "target_content_names": content_names, "breaks": norm_breaks,
            "sessions": results, "ai_used": False,
        }
        await self._session.commit()
        return summary

    async def list_coordination_sessions(
        self, coordinator_external_id: str, *, school_id: str,
        session_date: str | None = None, classroom_id: str | None = None,
    ) -> dict:
        tcs = TeachingContextService(self._session)
        try:
            await tcs.verify_coordinator_scope(
                coordinator_id=coordinator_external_id, school_id=UUID(str(school_id)))
        except ScopeAuthorizationError as exc:
            raise StudySessionAuthError(str(exc)) from exc
        q = select(StudySession).where(
            StudySession.school_id == UUID(str(school_id)),
            StudySession.source == SOURCE_SCHOOL,
        )
        if session_date:
            q = q.where(StudySession.session_date == session_date)
        if classroom_id:
            q = q.where(StudySession.scope_external_id == str(classroom_id))
        rows = (await self._session.execute(q.order_by(StudySession.created_at.desc()))).scalars().all()
        return {"school_id": str(school_id), "count": len(rows),
                "sessions": [self._view(r, brief=True) for r in rows]}

    # ================= internals =================

    @staticmethod
    def _authz_self(student_external_id: str, requester: Requester) -> None:
        if requester.external_user_id != student_external_id and not requester.is_platform_admin:
            raise StudySessionAuthError("a student can only access their own study sessions")

    def _student_requester(self, student_external_id: str, school_id: str | None) -> Requester:
        return Requester(external_user_id=student_external_id, school_id=school_id, role="STUDENT")

    async def _own_row(self, student_external_id: str, session_id: UUID,
                       requester: Requester) -> StudySession:
        self._authz_self(student_external_id, requester)
        row = await self._session.get(StudySession, session_id)
        if row is None:
            raise StudySessionNotFound(str(session_id))
        if row.student_external_id != student_external_id:
            raise StudySessionAuthError("this study session does not belong to you")
        return row

    async def _active_session(self, student_external_id: str, *, only_source: str | None = None,
                              prefer_source: str | None = None,
                              on_date: str | None = None,
                              statuses: tuple[str, ...] | None = _ACTIVE_STATUSES,
                              ) -> StudySession | None:
        q = select(StudySession).where(
            StudySession.student_external_id == student_external_id,
            StudySession.session_date == (on_date or _today()),
        )
        if statuses:
            q = q.where(StudySession.status.in_(statuses))
        if only_source:
            q = q.where(StudySession.source == only_source)
        rows = (await self._session.execute(q.order_by(StudySession.created_at.desc()))).scalars().all()
        if not rows:
            return None
        if prefer_source:
            for r in rows:
                if r.source == prefer_source:
                    return r
        return rows[0]

    async def _validate_codes(self, codes: list[str] | None) -> list[str]:
        if not codes:
            return []
        clean = [c for c in dict.fromkeys(codes) if c]
        if not clean:
            return []
        found = set((await self._session.execute(
            select(CatalogNode.code).where(CatalogNode.code.in_(clean),
                                           CatalogNode.active.is_(True)))).scalars().all())
        missing = [c for c in clean if c not in found]
        if missing:
            raise StudySessionError(f"content_code inválido ou inativo: {', '.join(missing)}")
        return clean

    async def _catalog_names(self, codes: list[str]) -> dict[str, str]:
        if not codes:
            return {}
        rows = (await self._session.execute(
            select(CatalogNode.code, CatalogNode.name).where(CatalogNode.code.in_(codes)))).all()
        return {code: name for code, name in rows}

    async def _build_plan(self, student_external_id: str, requester: Requester, *,
                          effective_minutes: int, timer_mode: str,
                          breaks: list[dict], target_content_codes: list[str]) -> dict:
        try:
            path = await AdaptiveLearningPathService(self._session).build_path(
                student_external_id, requester=requester)
        except Exception:  # noqa: BLE001 - a broken graph must not break the session
            path = {"state": "NO_EVIDENCE", "steps": [], "mastered": []}
        codes_for_material = list(target_content_codes) or [
            s["content_code"] for s in path.get("steps", [])[: self.policy.max_contents]]
        material = {}
        if codes_for_material:
            # PHASE 25: tenant-aware resolution (never a material outside the
            # student's own school/PUBLIC scope), and it carries a concrete
            # material_id so a STUDY block can open the real reader.
            avail = await MaterialAvailabilityService(self._session).resolve_for_content(
                codes_for_material, requester_school_id=requester.school_id)
            material = {k: v.as_dict() for k, v in avail.items()}
        catalog_names = await self._catalog_names(list(target_content_codes or []))
        return self._planner.plan(
            effective_minutes=effective_minutes, timer_mode=timer_mode, breaks=breaks,
            target_content_codes=target_content_codes or None, path=path,
            material_availability=material, catalog_names=catalog_names)

    async def _materialize_practice(self, student_external_id: str, requester: Requester,
                                    content_code: str, requested: int | None) -> dict:
        """Create the real PHASE 22 practice for a PRACTICE block. Returns a
        plain result dict (the practice service COMMITS internally, so the caller
        must re-load its ORM row afterwards). Never raises for an insufficient
        bank - it reduces the practice or reports the block as skipped (spec s11)."""
        svc = AdaptivePracticeService(self._session)
        want = int(requested or 10)
        note = None
        try:
            res = await svc.create_practice(student_external_id, requester=requester,
                                            content_code=content_code, question_count=want)
        except PracticeError as exc:
            payload = getattr(exc, "payload", {}) or {}
            avail = int(payload.get("available_questions") or 0)
            if avail >= _PRACTICE_MIN_Q:
                res = await svc.create_practice(student_external_id, requester=requester,
                                                content_code=content_code, question_count=avail)
                note = f"Prática reduzida para {avail} questões — banco insuficiente para {want}."
            else:
                return {"ok": False,
                        "note": ("Não há questões suficientes disponíveis para esta prática "
                                 "agora. Bloco pulado.")}
        return {"ok": True, "practice_id": res["practice_id"],
                "assignment_id": res["assignment_id"],
                "question_count": res["question_count"], "note": note}

    def _activate_first_pending(self, row: StudySession) -> None:
        blocks = (row.plan or {}).get("blocks", [])
        for b in blocks:
            if b["status"] == BL_PENDING:
                row.current_block_index = b["index"]
                break
        row.plan = {**(row.plan or {}), "blocks": blocks}; flag_modified(row, "plan")

    @staticmethod
    def _block_at(blocks: list[dict], index: int) -> dict:
        for b in blocks:
            if b["index"] == index:
                return b
        raise StudySessionError(f"bloco {index} não existe nesta sessão")

    def _normalize_breaks(self, breaks, start_dt, end_dt, window_minutes):
        pol = self.policy
        out = []
        total = 0
        spans = []
        for raw in (breaks or []):
            dur = raw.get("duration_minutes")
            b_start = _parse_dt(raw.get("start_at")) if raw.get("start_at") else None
            b_end = _parse_dt(raw.get("end_at")) if raw.get("end_at") else None
            if b_start and b_end:
                if b_end <= b_start:
                    raise StudySessionError("Intervalo com fim antes do início.")
                dur = int((b_end - b_start).total_seconds() // 60)
            elif b_start and dur:
                b_end = b_start + timedelta(minutes=int(dur))
            dur = int(dur or pol.break_default_minutes)
            if dur <= 0:
                raise StudySessionError("Intervalo com duração não positiva.")
            if b_start and (b_start < start_dt or (b_end and b_end > end_dt)):
                raise StudySessionError("Intervalo fora da janela da sessão.")
            if b_start and b_end:
                for (s0, e0) in spans:
                    if b_start < e0 and s0 < b_end:
                        raise StudySessionError("Intervalos sobrepostos não são permitidos.")
                spans.append((b_start, b_end))
            total += dur
            out.append({
                "duration_minutes": dur,
                "start_at": b_start.isoformat() if b_start else None,
                "end_at": b_end.isoformat() if b_end else None,
                "after_block": raw.get("after_block"),
            })
        if total >= window_minutes:
            raise StudySessionError("Os intervalos não podem ocupar toda a janela.")
        return out, total

    async def _students_for_target(self, school_id: str, target_type: str,
                                   target_id: str) -> list[str]:
        tt = (target_type or "CLASSROOM").upper()
        if tt == "STUDENT":
            # target_id alone proves nothing - without a real UserSchoolLink
            # membership check, a coordinator scoped to school_id could write
            # a StudySession for any student in any other school.
            rows = (await self._session.execute(
                select(UserSchoolLink.external_user_id).where(
                    UserSchoolLink.school_id == UUID(str(school_id)),
                    UserSchoolLink.external_user_id == str(target_id),
                    UserSchoolLink.role == "STUDENT",
                    UserSchoolLink.active.is_(True),
                ))).scalars().all()
            return list(rows)
        rows = (await self._session.execute(
            select(UserSchoolLink.external_user_id).where(
                UserSchoolLink.school_id == UUID(str(school_id)),
                UserSchoolLink.role == "STUDENT",
                UserSchoolLink.scope_type == "CLASSROOM",
                UserSchoolLink.scope_external_id == str(target_id),
                UserSchoolLink.active.is_(True),
            ))).scalars().all()
        return sorted(dict.fromkeys(rows))

    def _view(self, row: StudySession, *, brief: bool = False) -> dict:
        blocks = (row.plan or {}).get("blocks", [])
        # progress = blocks the student has moved past, whether completed or
        # skipped (a skip is still a real decision, not lost progress - s16).
        done = sum(1 for b in blocks if b["status"] in (BL_DONE, BL_SKIPPED))
        base = {
            "id": str(row.id),
            "student_external_id": row.student_external_id,
            "source": row.source,
            "status": row.status,
            "timer_mode": row.timer_mode,
            "session_date": row.session_date,
            "start_at": row.start_at.isoformat() if row.start_at else None,
            "end_at": row.end_at.isoformat() if row.end_at else None,
            "available_minutes": row.available_minutes,
            "window_minutes": row.available_minutes,
            "break_minutes": row.break_minutes,
            "effective_study_minutes": row.effective_study_minutes,
            "target_content_codes": row.target_content_codes or [],
            "target_content_names": (row.plan or {}).get("target_content_names", []),
            "scope_type": row.scope_type,
            "scope_external_id": row.scope_external_id,
            "current_block_index": row.current_block_index,
            "blocks_total": len(blocks),
            "blocks_done": done,
            "progress_percent": round(done / len(blocks) * 100, 1) if blocks else 0.0,
            "created_by_external_id": row.created_by_external_id,
            "ai_used": False,
        }
        if brief:
            return base
        base["plan_notes"] = (row.plan or {}).get("notes", [])
        base["planner_version"] = (row.plan or {}).get("planner_version")
        base["blocks"] = blocks
        return base


def _parse_dt(value) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=timezone.utc)
    try:
        txt = str(value).strip().replace("Z", "+00:00")
        dt = datetime.fromisoformat(txt)
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


__all__ = [
    "StudySessionService", "StudySessionError", "StudySessionNotFound", "StudySessionAuthError",
    "SOURCE_STUDENT", "SOURCE_SCHOOL",
]
