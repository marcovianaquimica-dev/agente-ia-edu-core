from __future__ import annotations

import hashlib
from collections.abc import AsyncIterator
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select, func
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.schemas.questions import (
    QuestionApprovalRequest,
    QuestionApprovalResponse,
    QuestionAuthoringRequest,
    QuestionAuthoringResponse,
    QuestionCreateRequest,
    QuestionDetail,
    QuestionEligibilityResponse,
    QuestionListResponse,
    QuestionReviewRequest,
    QuestionStatusTransitionRequest,
    QuestionStatusTransitionResponse,
    QuestionVersionCreateRequest,
)
from agente_ia_edu.db.models import Question, QuestionOption, QuestionVersion
from agente_ia_edu.db.models.official import (
    QuestionApproval,
    QuestionStatusTransition,
    BookletQuestion,
    ExamBooklet,
    ExamApplication,
    Exam,
    Institution,
)
from agente_ia_edu.db.session import create_session_factory
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.repositories.questions import QuestionRepository
from agente_ia_edu.services.authorization import AuthorizationService
from agente_ia_edu.services.content_authoring import QuestionAuthoringService
from agente_ia_edu.services.question_governance import (
    InvalidQuestionStatusTransitionError,
    QuestionAuthorizationService,
    QuestionEligibilityCalculator,
    QuestionStatusWorkflow,
)
from agente_ia_edu.services.questions import QuestionService

router = APIRouter(prefix="/api/v1/questions", tags=["questions"])


async def get_question_service() -> AsyncIterator[QuestionService]:
    session_factory = create_session_factory()
    async with session_factory() as session:
        yield QuestionService(QuestionRepository(session))


async def _resolve_access_context(
    session: AsyncSession,
    identity: ExternalIdentityContext,
) -> object:
    authz = AuthorizationService(session)
    return await authz.resolve_context(identity)


async def _load_question_for_view(
    session: AsyncSession,
    identity: ExternalIdentityContext,
    question_id: UUID,
) -> tuple[Question, object]:
    question = await session.get(Question, question_id)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")

    context = await _resolve_access_context(session, identity)
    if not QuestionAuthorizationService.can_view_question(context, question):
        raise HTTPException(status_code=403, detail="Question is not visible in the current tenant scope")
    return question, context


async def _load_question_for_manage(
    session: AsyncSession,
    identity: ExternalIdentityContext,
    question_id: UUID,
) -> tuple[Question, object]:
    question = await session.get(
        Question, question_id, options=[selectinload(Question.versions)]
    )
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")

    context = await _resolve_access_context(session, identity)
    if not QuestionAuthorizationService.can_manage_question(context, question):
        raise HTTPException(status_code=403, detail="This question cannot be managed by the current user")
    return question, context


@router.get("", response_model=QuestionListResponse)
async def list_questions(
    # Pagination
    page: int = Query(default=1, ge=1),
    limit: int = Query(default=20, ge=1, le=100),
    # Ordering (whitelist only)
    order_by: str = Query(default="updated_at"),
    order_direction: str = Query(default="desc"),
    # Source filters (exams/institutions)
    institution_code: str | None = None,
    exam_code: str | None = None,
    year: int | None = Query(default=None, gt=0),
    # Content/classification filters
    content: str | None = None,
    subject: str | None = None,
    difficulty: str | None = None,
    question_type: str | None = None,
    # Governance filters
    status: str | None = None,
    visibility_scope: str | None = None,
    origin_type: str | None = None,
    # Eligibility filter
    eligible_only: bool = Query(default=False),
    # Date range filters
    created_from: str | None = None,
    created_to: str | None = None,
    updated_from: str | None = None,
    updated_to: str | None = None,
    # Existing dependencies
    service: QuestionService = Depends(get_question_service),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> QuestionListResponse:
    """
    Advanced question search with role/scope/tenant aware access control.

    Ordering is whitelist-based to prevent SQL injection.
    Date filters accept ISO format: YYYY-MM-DD
    """
    # Whitelist for ordering to prevent SQL injection
    ALLOWED_ORDER_FIELDS = {"created_at", "updated_at", "status", "difficulty", "question_type"}
    if order_by not in ALLOWED_ORDER_FIELDS:
        order_by = "updated_at"

    order_direction = order_direction.lower()
    if order_direction not in {"asc", "desc"}:
        order_direction = "desc"

    if hasattr(service, "repository") and hasattr(service.repository, "session"):
        async with session_factory() as session:
            context = await _resolve_access_context(session, identity)

            # Build the base query and include the ordering field in the SELECT list to avoid
            # PostgreSQL DISTINCT + ORDER BY mismatches.
            stmt = select(Question.id)
            if order_by == "difficulty":
                stmt = select(Question.id, QuestionVersion.recommended_difficulty)
            elif order_by in {"created_at", "updated_at", "status", "question_type"}:
                order_column = getattr(Question, order_by)
                stmt = select(Question.id, order_column)

            # Apply authorization rules first (tenant + visibility + role)
            if context.role == "STUDENT" and not context.is_platform_admin:
                stmt = stmt.where(Question.visibility_scope == "PUBLIC")
            elif context.school_id is not None:
                stmt = stmt.where((Question.school_id == context.school_id) | (Question.visibility_scope == "PUBLIC"))
            elif not context.is_platform_admin:
                # TEACHER/COORDINATOR/DIRECTOR is reachable with school_id=None
                # (AuthorizationService.resolve_context's role_hint fallback,
                # e.g. no active UserSchoolLink yet) - no real school
                # relationship, same fail-closed default STUDENT already gets
                # above. Without this branch the WHERE gets no tenant clause
                # at all: individual rows still come back filtered by
                # can_view_question below, but total/total_pages would leak
                # an unfiltered, cross-tenant count.
                stmt = stmt.where(Question.visibility_scope == "PUBLIC")

            # Apply governance filters
            if status:
                stmt = stmt.where(Question.status == status.upper())
            if visibility_scope:
                stmt = stmt.where(Question.visibility_scope == visibility_scope.upper())
            if origin_type:
                stmt = stmt.where(Question.origin_type == origin_type.upper())
            if question_type:
                stmt = stmt.where(Question.question_type == question_type.upper())

            # Apply content/classification filters (requires a single explicit join to QuestionVersion)
            join_needed = bool(content or difficulty or order_by == "difficulty")
            if join_needed:
                stmt = stmt.join(
                    QuestionVersion,
                    QuestionVersion.question_id == Question.id,
                )
                stmt = stmt.where(QuestionVersion.version_kind == "official_original")
                if content:
                    stmt = stmt.where(QuestionVersion.canonical_text.ilike(f"%{content}%"))
                if difficulty:
                    stmt = stmt.where(QuestionVersion.recommended_difficulty == difficulty.upper())

            # Apply date range filters
            if created_from:
                try:
                    created_from_dt = datetime.fromisoformat(created_from).replace(tzinfo=None)
                    stmt = stmt.where(Question.created_at >= created_from_dt)
                except (ValueError, AttributeError):
                    pass

            if created_to:
                try:
                    created_to_dt = datetime.fromisoformat(created_to).replace(tzinfo=None)
                    stmt = stmt.where(Question.created_at <= created_to_dt)
                except (ValueError, AttributeError):
                    pass

            if updated_from:
                try:
                    updated_from_dt = datetime.fromisoformat(updated_from).replace(tzinfo=None)
                    stmt = stmt.where(Question.updated_at >= updated_from_dt)
                except (ValueError, AttributeError):
                    pass

            if updated_to:
                try:
                    updated_to_dt = datetime.fromisoformat(updated_to).replace(tzinfo=None)
                    stmt = stmt.where(Question.updated_at <= updated_to_dt)
                except (ValueError, AttributeError):
                    pass

            # Count total before pagination
            count_subquery = select(func.count()).select_from(stmt.distinct().subquery())
            total = (await session.execute(count_subquery)).scalar_one() or 0

            # Apply ordering while keeping the ordering column in the select list.
            if order_by == "difficulty":
                if order_direction == "asc":
                    stmt = stmt.order_by(QuestionVersion.recommended_difficulty.asc(), Question.id.asc())
                else:
                    stmt = stmt.order_by(QuestionVersion.recommended_difficulty.desc(), Question.id.asc())
            else:
                order_column = getattr(Question, order_by)
                if order_direction == "asc":
                    stmt = stmt.order_by(order_column.asc(), Question.id.asc())
                else:
                    stmt = stmt.order_by(order_column.desc(), Question.id.asc())

            # Apply pagination and retrieve ordered ids.
            stmt = stmt.distinct().offset((page - 1) * limit).limit(limit)
            ordered_rows = (await session.execute(stmt)).all()
            ordered_ids = [row[0] for row in ordered_rows]
            if not ordered_ids:
                return QuestionListResponse(items=[], pagination={"page": page, "limit": limit, "total": total, "total_pages": (total + limit - 1) // limit if total > 0 else 0})

            question_stmt = select(Question).where(Question.id.in_(ordered_ids)).options(
                selectinload(Question.versions).selectinload(QuestionVersion.options),
                selectinload(Question.versions).selectinload(QuestionVersion.pedagogical_classifications),
            )
            question_rows = (await session.execute(question_stmt)).scalars().all()
            questions_by_id = {question.id: question for question in question_rows}
            rows = [questions_by_id[question_id] for question_id in ordered_ids if question_id in questions_by_id]

            # Filter by authorization and eligibility
            filtered: list[QuestionDetail] = []
            for question in rows:
                if not QuestionAuthorizationService.can_view_question(context, question):
                    continue

                # Apply eligibility filter if requested
                if eligible_only:
                    eligibility = QuestionEligibilityCalculator.calculate(question)
                    if not eligibility.is_eligible:
                        continue

                version = next((v for v in question.versions if v.version_kind == "official_original"), None)
                if version is None:
                    continue

                filtered.append(
                    QuestionDetail(
                        id=question.id,
                        validation_status=question.validation_status,
                        content={
                            "version_id": version.id,
                            "version_kind": version.version_kind,
                            "canonical_text": version.canonical_text,
                            "statement": version.statement,
                            "options": [
                                {
                                    "id": option.id,
                                    "key": option.option_key,
                                    "position": option.position,
                                    "text": option.text,
                                }
                                for option in sorted(version.options, key=lambda value: value.position)
                            ],
                        },
                        sources=[],
                        classification=None,
                        difficulty=None,
                        answer_key=None,
                    )
                )

            # Calculate total_pages
            total_pages = (total + limit - 1) // limit if total > 0 else 0

            return QuestionListResponse(
                items=filtered,
                pagination={
                    "page": page,
                    "limit": limit,
                    "total": total,
                    "total_pages": total_pages
                },
            )
    else:
        # Fallback for non-standard repository setup
        return QuestionListResponse(items=[], pagination={"page": page, "limit": limit, "total": 0, "total_pages": 0})

    return await service.list_questions(
        page=page,
        limit=limit,
        institution_code=institution_code,
        exam_code=exam_code,
        year=year,
        content=content,
    )


@router.post("", response_model=QuestionAuthoringResponse, status_code=201)
async def create_question_authoring(
    request: QuestionCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> QuestionAuthoringResponse:
    async with session_factory() as session:
        context = await _resolve_access_context(session, identity)

        if request.school_id and context.school_id and str(request.school_id) != str(context.school_id):
            raise HTTPException(status_code=403, detail="School context does not match the authenticated tenant")
        if request.author_external_id and request.author_external_id != identity.external_user_id:
            raise HTTPException(status_code=403, detail="Author identity cannot be overridden by the client")
        if request.created_by_external_identity and request.created_by_external_identity != identity.external_user_id:
            raise HTTPException(status_code=403, detail="Created-by identity cannot be overridden by the client")

        if request.visibility_scope.upper() not in {"PRIVATE", "SCHOOL", "CLASSROOM", "PUBLIC"}:
            raise HTTPException(status_code=422, detail="Unsupported visibility_scope")

        effective_school_id = context.school_id if context.school_id is not None else None

        question = Question(
            question_type=(request.question_type or "MULTIPLE_CHOICE").upper(),
            school_id=effective_school_id,
            author_external_id=context.user_id or identity.external_user_id,
            owner_external_id=context.user_id or identity.external_user_id,
            origin_type="TEACHER" if context.role in {"TEACHER", "COORDINATOR", "DIRECTOR"} else "PLATFORM",
            status="DRAFT",
            visibility_scope=request.visibility_scope.upper(),
            created_by_external_identity=identity.external_user_id,
            validation_status="draft",
            metadata_={**(request.metadata or {}), "subject": request.subject or "general"},
        )
        session.add(question)
        await session.flush()

        statement = (request.statement or "").strip()
        option_list = [item.strip() for item in request.options]
        if len(option_list) < 2:
            raise HTTPException(status_code=422, detail="At least two answer options are required")
        if request.correct_option not in option_list:
            raise HTTPException(status_code=422, detail="The correct option must be present in the answer options")

        version = QuestionVersion(
            question_id=question.id,
            version_kind="official_original",
            canonical_text=statement,
            statement=statement,
            content_hash=hashlib.sha256(statement.encode("utf-8")).hexdigest(),
            recommended_difficulty=(request.difficulty or "MEDIUM").upper(),
            created_by_type=context.role,
            created_by_id=context.user_id or identity.external_user_id,
            metadata_={"subject": request.subject or "general", "visibility_scope": request.visibility_scope.upper()},
        )
        session.add(version)
        await session.flush()

        for index, option_text in enumerate(option_list, start=1):
            session.add(
                QuestionOption(
                    question_version_id=version.id,
                    option_key=chr(65 + index - 1),
                    position=index,
                    text=option_text,
                    is_valid_option=(option_text == request.correct_option.strip()),
                )
            )

        question.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(question)
        await session.refresh(version)
        return QuestionAuthoringResponse(
            question_id=question.id,
            version_id=version.id,
            status=question.status,
        )


@router.post("/{question_id}/versions", response_model=QuestionAuthoringResponse)
async def create_question_version(
    question_id: UUID,
    request: QuestionVersionCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> QuestionAuthoringResponse:
    async with session_factory() as session:
        question, context = await _load_question_for_manage(session, identity, question_id)
        latest = next((v for v in question.versions if v.version_kind == "official_original"), None)
        if latest is None:
            raise HTTPException(status_code=404, detail="Question has no official version to revise")

        # version_kind="official_original" is at most one row per question
        # (uq_question_versions_official_original) - a revision is its own
        # kind, chained off the official version via parent_version_id, the
        # same convention question_modification_proposals.py uses for a
        # teacher-authored edit of a question.
        version = QuestionVersion(
            question_id=question.id,
            version_kind="teacher_modification",
            parent_version_id=latest.id,
            canonical_text=(request.statement or "").strip(),
            statement=(request.statement or "").strip(),
            content_hash=hashlib.sha256((request.statement or "").strip().encode("utf-8")).hexdigest(),
            recommended_difficulty=(request.difficulty or latest.recommended_difficulty or "MEDIUM").upper(),
            created_by_type=context.role,
            created_by_id=context.user_id or identity.external_user_id,
            change_reason=request.reason,
            metadata_={"source": "api_version", "status": "DRAFT"},
        )
        session.add(version)
        await session.flush()

        option_list = list(request.options)
        if len(option_list) < 2:
            raise HTTPException(status_code=422, detail="At least two answer options are required")
        if request.correct_option not in option_list:
            raise HTTPException(status_code=422, detail="The correct option must be present in the answer options")

        for index, option_text in enumerate(option_list, start=1):
            session.add(
                QuestionOption(
                    question_version_id=version.id,
                    option_key=chr(65 + index - 1),
                    position=index,
                    text=option_text.strip(),
                    is_valid_option=(option_text.strip() == request.correct_option.strip()),
                )
            )

        question.updated_at = datetime.now(timezone.utc)
        await session.commit()
        await session.refresh(question)
        await session.refresh(version)
        return QuestionAuthoringResponse(
            question_id=question.id,
            version_id=version.id,
            status=question.status,
        )


@router.post("/{question_id}/status", response_model=QuestionStatusTransitionResponse)
async def transition_question_status(
    question_id: UUID,
    request: QuestionStatusTransitionRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> QuestionStatusTransitionResponse:
    async with session_factory() as session:
        question, context = await _load_question_for_manage(session, identity, question_id)
        target_status = request.status.upper()

        if not QuestionAuthorizationService.can_transition_status(context, question, target_status):
            raise HTTPException(status_code=403, detail="Status transition is not authorized for this role and scope")

        try:
            record = QuestionStatusWorkflow.transition(
                question,
                target_status,
                performed_by_external_id=context.user_id or identity.external_user_id,
                reason=request.reason,
            )
        except InvalidQuestionStatusTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

        session.add(record)
        if target_status in {"APPROVED", "REJECTED"}:
            approval = QuestionApproval(
                question_id=question.id,
                version_id=(question.versions[-1].id if question.versions else None),
                reviewer_external_id=context.user_id or identity.external_user_id,
                decision=target_status,
                feedback_text=request.reason,
                approved_at=datetime.now(timezone.utc),
            )
            session.add(approval)

        await session.commit()
        await session.refresh(record)
        await session.refresh(question)
        return QuestionStatusTransitionResponse(
            id=record.id,
            question_id=question.id,
            from_status=record.from_status,
            to_status=record.to_status,
            performed_by_external_id=record.performed_by_external_id,
            reason=record.reason,
        )


@router.get("/{question_id}/eligibility", response_model=QuestionEligibilityResponse)
async def get_question_eligibility(
    question_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> QuestionEligibilityResponse:
    async with session_factory() as session:
        question, _ = await _load_question_for_view(session, identity, question_id)
        result = QuestionEligibilityCalculator.calculate(question)
        return QuestionEligibilityResponse(
            question_id=question.id,
            is_eligible=result.is_eligible,
            reasons=result.reasons,
        )


@router.post("/{question_id}/approval", response_model=QuestionApprovalResponse)
async def question_approval(
    question_id: UUID,
    request: QuestionApprovalRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> QuestionApprovalResponse:
    async with session_factory() as session:
        question, context = await _load_question_for_manage(session, identity, question_id)
        if request.decision.upper() not in {"APPROVED", "REJECTED", "FEEDBACK"}:
            raise HTTPException(status_code=422, detail="Unsupported approval decision")
        if context.role not in {"DIRECTOR", "COORDINATOR", "PLATFORM_ADMIN"}:
            raise HTTPException(status_code=403, detail="Only school admins or platform admins can approve questions")

        approval = QuestionApproval(
            question_id=question.id,
            version_id=(question.versions[-1].id if question.versions else None),
            reviewer_external_id=context.user_id or identity.external_user_id,
            decision=request.decision.upper(),
            feedback_text=request.feedback_text,
            approved_at=datetime.now(timezone.utc),
        )
        session.add(approval)
        await session.commit()
        await session.refresh(approval)
        await session.refresh(question)
        return QuestionApprovalResponse(
            id=approval.id,
            question_id=question.id,
            reviewer_external_id=approval.reviewer_external_id,
            decision=approval.decision,
            feedback_text=approval.feedback_text,
        )


@router.get("/{question_id}", response_model=QuestionDetail)
async def get_question(
    question_id: UUID,
    include_answer_key: bool = Query(default=False),
    service: QuestionService = Depends(get_question_service),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> QuestionDetail:
    if hasattr(service, "repository") and hasattr(service.repository, "session"):
        async with session_factory() as session:
            question, _ = await _load_question_for_view(session, identity, question_id)
            repo_question = await service.get_question(question_id, include_answer_key=include_answer_key)
            if repo_question is None:
                raise HTTPException(status_code=404, detail="Question not found")
            return repo_question

    question = await service.get_question(question_id, include_answer_key=include_answer_key)
    if question is None:
        raise HTTPException(status_code=404, detail="Question not found")
    return question


@router.post("/{question_id}/review", response_model=QuestionAuthoringResponse)
async def review_question_authoring(
    question_id: UUID,
    request: QuestionReviewRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> QuestionAuthoringResponse:
    async with session_factory() as session:
        question, context = await _load_question_for_manage(session, identity, question_id)
        # _load_question_for_manage only checks can_manage_question, which is
        # ownership-based - correct for "submit" (the author submitting their
        # own draft), but approve/reject/archive are a review decision and
        # must not be gradable by the same person who owns the question, or
        # the four-eyes guarantee can_transition_status already enforces for
        # Question.status (the parallel governance field) is undermined here.
        if request.action in ("approve", "reject", "archive") and context.role not in (
            "DIRECTOR", "COORDINATOR", "PLATFORM_ADMIN",
        ):
            raise HTTPException(
                status_code=403,
                detail="Approving, rejecting, or archiving a question requires a director, coordinator, or platform admin role.",
            )
        service = QuestionAuthoringService(session)
        try:
            if request.action == "submit":
                question = await service.submit_for_review(question_id)
            elif request.action == "approve":
                question = await service.approve(question_id)
            elif request.action == "reject":
                question = await service.reject(question_id, reason=request.reason)
            elif request.action == "archive":
                question = await service.archive_question(question_id)
            else:
                raise ValueError("Unsupported review action.")
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        await session.commit()
        await session.refresh(question)
        version = await service.get_current_version(question_id)
        return QuestionAuthoringResponse(
            question_id=question.id,
            version_id=version.id,
            status=question.validation_status,
        )
