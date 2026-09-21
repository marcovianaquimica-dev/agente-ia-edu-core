"""
API routes for learning path / practice sessions.

Endpoints for the full practice flow:
- Creating practice sessions (with real question selection)
- Listing / fetching the next question (no answer key exposed)
- Answering practice questions
- Completing practice sessions (real correction + domain update)
- Querying results, learning history, and mastery/progress
"""

from dataclasses import asdict
from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy import select
from sqlalchemy.orm import selectinload

from ..dependencies import get_session_factory, get_current_identity
from .diagnostic import get_current_diagnostic_identity
from ..schemas.learning_path import (
    PracticeSessionCreateRequest,
    PracticeSessionResponse,
    PracticeQuestionResponse,
    PracticeQuestionOption,
    NextPracticeQuestionResponse,
    PracticeQuestionAnswerRequest,
    PracticeQuestionAnswerResponse,
    PracticeSessionCompleteRequest,
    PracticeSessionResult,
    StudentContentMasteryResponse,
    StudentMasteryListResponse,
    LearningHistoryListResponse,
    LearningHistoryEntryResponse,
)
from ...identity import ExternalIdentityContext
from ...db.models import (
    CatalogNode,
    PedagogicalUniverse,
    QuestionOption,
    QuestionVersion,
    TaxonomyNode,
)
from ...repositories.learning_path import (
    PracticeSessionRepository,
    PracticeQuestionSelectionRepository,
    StudentContentMasteryRepository,
    LearningHistoryRepository,
)
from ...services.answer_key import resolve_official_answer_key_snapshots
from ...services.learning_path import (
    PracticeSessionService,
    ContentMasteryService,
    DifficultyRecommendationService,
    LearningHistoryService,
    QuestionSelectionService,
)
from ...services.learning_path_policies import ActivityType, DifficultyLevel
from ...services.domain_map import DomainMapService
from ...services.pedagogical_universe import PedagogicalUniverseService

practice_router = APIRouter(
    prefix="/api/v1/practice",
    tags=["practice"],
)


async def _load_question_version(session, question_version_id: UUID) -> QuestionVersion | None:
    """Load a question version with its options, ordered for display."""
    result = await session.execute(
        select(QuestionVersion)
        .where(QuestionVersion.id == question_version_id)
        .options(selectinload(QuestionVersion.options))
    )
    return result.scalar_one_or_none()


async def _load_question_versions(
    session, question_version_ids: list[UUID]
) -> dict[UUID, QuestionVersion]:
    if not question_version_ids:
        return {}
    result = await session.execute(
        select(QuestionVersion)
        .where(QuestionVersion.id.in_(question_version_ids))
        .options(selectinload(QuestionVersion.options))
    )
    return {version.id: version for version in result.scalars().unique().all()}


def _to_question_response(
    selection,
    version: QuestionVersion,
) -> PracticeQuestionResponse:
    """Build the pre-answer question payload. Never includes the answer key."""
    options = [
        PracticeQuestionOption(
            id=option.id,
            option_key=option.option_key,
            text=option.text,
            position=option.position,
        )
        for option in sorted(version.options, key=lambda o: o.position)
        if option.is_valid_option
    ]
    return PracticeQuestionResponse(
        id=selection.id,
        position=selection.position,
        question_version_id=selection.question_version_id,
        difficulty_level=selection.difficulty_level,
        canonical_text=version.canonical_text,
        options=options,
    )


# ============================================================================
# Practice Session Endpoints
# ============================================================================


@practice_router.post(
    "/sessions",
    status_code=201,
    response_model=PracticeSessionResponse,
    summary="Create a new practice session",
    description="Initiates a practice session for a student and selects real questions.",
)
async def create_practice_session(
    request: PracticeSessionCreateRequest,
    identity: ExternalIdentityContext = Depends(get_current_diagnostic_identity),
    session_factory=Depends(get_session_factory),
) -> PracticeSessionResponse:
    """Create a new practice session and populate it with selected questions."""
    external_identity_id = identity.external_user_id

    async with session_factory() as session:
        practice_service = PracticeSessionService()
        difficulty_service = DifficultyRecommendationService()
        selection_service = QuestionSelectionService()
        domain_map = await DomainMapService(session).build(
            student_id=external_identity_id,
            identity=identity,
        )
        recommendation = domain_map["next_best_action"]
        content_node_id = request.content_node_id
        recommendation_origin = "USER_SELECTED"
        reason = "User requested content"
        if content_node_id is None:
            if recommendation is None:
                raise HTTPException(
                    status_code=409,
                    detail="No pedagogical next action is available for practice.",
                )
            content_node_id = UUID(recommendation.target_content_node_id)
            recommendation_origin = "NEXT_BEST_ACTION"
            reason = recommendation.reason

        canonical_node = await session.get(CatalogNode, content_node_id)
        legacy_node = None if canonical_node else await session.get(TaxonomyNode, content_node_id)
        if canonical_node is None and legacy_node is None:
            raise HTTPException(status_code=404, detail="Content node not found")

        universe_payload = domain_map.get("universe")
        universe_id = UUID(str(universe_payload["id"])) if universe_payload else None
        universe = await session.get(PedagogicalUniverse, universe_id) if universe_id else None
        if universe_id:
            if canonical_node is None or not await PedagogicalUniverseService(session).contains_catalog_node(
                universe_id, content_node_id
            ):
                raise HTTPException(
                    status_code=403,
                    detail="Content is outside the authorized pedagogical universe.",
                )

        selected_content = next(
            (
                item for item in domain_map["contents"]
                if str(item["content_node_id"]) == str(content_node_id)
            ),
            None,
        )
        recommendation_snapshot = asdict(recommendation) if recommendation else None
        metadata = {
            "selected_content_node_id": str(content_node_id),
            "selection_origin": recommendation_origin,
            "recommendation": recommendation_snapshot,
            "universe": ({
                "id": str(universe.id),
                "owner_type": universe.owner_type,
                "configuration_version": universe.configuration_version,
            } if universe else None),
            "academic_context": {
                "school_id": identity.institution_id,
                "unit_id": identity.unit_id,
                "segment": identity.metadata.get("segment"),
                "grade_level": identity.grade_level,
                "classroom_id": identity.classroom_id,
            },
            "pedagogical_context": [
                {
                    "source": item["source"],
                    "title": item["title"],
                    "recorded_at": item["recorded_at"].isoformat(),
                }
                for item in (selected_content or {}).get("pedagogical_contexts", [])
            ],
        }

        recommended_difficulty = await difficulty_service.get_recommended_difficulty(
            session,
            external_identity_id,
            content_node_id,
        )

        practice_session = await practice_service.create_session(
            session,
            external_identity_id,
            content_node_id=content_node_id,
            requested_question_count=request.requested_question_count,
            recommended_difficulty=recommended_difficulty,
            recommendation_reason=reason,
            metadata=metadata,
        )

        school_id = None
        if identity.institution_id:
            try:
                school_id = UUID(identity.institution_id)
            except ValueError:
                school_id = None
        eligibility_context = None
        if canonical_node is not None:
            eligibility_context = {
                "school_id": school_id,
                "classroom_id": identity.classroom_id,
                "unit_id": identity.unit_id,
                "segment": identity.metadata.get("segment"),
                "grade_level": identity.grade_level,
                "universe_id": universe_id,
            }
        selections = await selection_service.populate_session(
            session,
            practice_session,
            external_identity_id,
            recommended_difficulty,
            eligibility_context=eligibility_context,
        )

        if not selections:
            await session.rollback()
            raise HTTPException(
                status_code=409,
                detail="No eligible questions are available for this practice content.",
            )

        await session.commit()
        await session.refresh(practice_session)

        return PracticeSessionResponse(
            id=practice_session.id,
            external_identity_id=practice_session.external_identity_id,
            content_node_id=practice_session.content_node_id,
            recommended_difficulty=practice_session.recommended_difficulty,
            requested_question_count=practice_session.requested_question_count,
            status=practice_session.status,
            started_at=practice_session.started_at,
            completed_at=practice_session.completed_at,
            recommendation_reason=practice_session.recommendation_reason,
        )


@practice_router.get(
    "/sessions/{session_id}",
    response_model=PracticeSessionResponse,
    summary="Get practice session details",
)
async def get_practice_session(
    session_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> PracticeSessionResponse:
    """Get details of a practice session."""
    external_identity_id = identity.external_user_id

    async with session_factory() as session:
        repo = PracticeSessionRepository(session)
        practice_session = await repo.get_by_id(session_id)

        if not practice_session:
            raise HTTPException(status_code=404, detail="Practice session not found")

        if practice_session.external_identity_id != external_identity_id:
            raise HTTPException(status_code=403, detail="Access denied")

        return PracticeSessionResponse(
            id=practice_session.id,
            external_identity_id=practice_session.external_identity_id,
            content_node_id=practice_session.content_node_id,
            recommended_difficulty=practice_session.recommended_difficulty,
            requested_question_count=practice_session.requested_question_count,
            status=practice_session.status,
            started_at=practice_session.started_at,
            completed_at=practice_session.completed_at,
            recommendation_reason=practice_session.recommendation_reason,
        )


# ============================================================================
# Practice Questions Endpoints
# ============================================================================


@practice_router.get(
    "/sessions/{session_id}/questions",
    response_model=list[PracticeQuestionResponse],
    summary="List questions in practice session",
    description="Returns all questions in a practice session with options but no answer key.",
)
async def list_practice_questions(
    session_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[PracticeQuestionResponse]:
    """List questions in a practice session."""
    external_identity_id = identity.external_user_id

    async with session_factory() as session:
        practice_repo = PracticeSessionRepository(session)
        practice_session = await practice_repo.get_by_id(session_id)

        if not practice_session:
            raise HTTPException(status_code=404, detail="Practice session not found")

        if practice_session.external_identity_id != external_identity_id:
            raise HTTPException(status_code=403, detail="Access denied")

        question_repo = PracticeQuestionSelectionRepository(session)
        selections = await question_repo.list_by_session(session_id)
        versions = await _load_question_versions(
            session, [selection.question_version_id for selection in selections]
        )

        responses = []
        for sel in selections:
            version = versions.get(sel.question_version_id)
            if version is None:
                continue
            responses.append(_to_question_response(sel, version))

        return responses


@practice_router.get(
    "/sessions/{session_id}/next-question",
    response_model=NextPracticeQuestionResponse,
    summary="Get the next unanswered question in the session",
)
async def get_next_practice_question(
    session_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> NextPracticeQuestionResponse:
    """Return the next unanswered question, or is_complete=True if none remain."""
    external_identity_id = identity.external_user_id

    async with session_factory() as session:
        practice_repo = PracticeSessionRepository(session)
        practice_session = await practice_repo.get_by_id(session_id)

        if not practice_session:
            raise HTTPException(status_code=404, detail="Practice session not found")

        if practice_session.external_identity_id != external_identity_id:
            raise HTTPException(status_code=403, detail="Access denied")

        question_repo = PracticeQuestionSelectionRepository(session)
        selections = await question_repo.list_by_session(session_id)

        next_selection = next((s for s in selections if s.answered_at is None), None)
        if next_selection is None:
            return NextPracticeQuestionResponse(is_complete=True, question=None)

        version = await _load_question_version(session, next_selection.question_version_id)
        if version is None:
            raise HTTPException(status_code=404, detail="Question content not found")

        return NextPracticeQuestionResponse(
            is_complete=False,
            question=_to_question_response(next_selection, version),
        )


@practice_router.post(
    "/sessions/{session_id}/questions/{selection_id}/answer",
    response_model=PracticeQuestionAnswerResponse,
    summary="Answer a practice question",
    status_code=201,
)
async def answer_practice_question(
    session_id: UUID,
    selection_id: UUID,
    request: PracticeQuestionAnswerRequest,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> PracticeQuestionAnswerResponse:
    """Answer a practice question."""
    external_identity_id = identity.external_user_id

    async with session_factory() as session:
        practice_repo = PracticeSessionRepository(session)
        practice_session = await practice_repo.get_by_id(session_id)

        if not practice_session:
            raise HTTPException(status_code=404, detail="Practice session not found")

        if practice_session.external_identity_id != external_identity_id:
            raise HTTPException(status_code=403, detail="Access denied")

        if practice_session.status != "active":
            raise HTTPException(status_code=400, detail="Practice session is not active")

        selection_repo = PracticeQuestionSelectionRepository(session)
        selection = await selection_repo.get_by_id(selection_id)

        if not selection:
            raise HTTPException(status_code=404, detail="Question selection not found")

        if selection.practice_session_id != session_id:
            raise HTTPException(status_code=400, detail="Question does not belong to this session")

        if request.is_unknown and (
            request.selected_option_id is not None or request.response_text
        ):
            raise HTTPException(
                status_code=400,
                detail="UNKNOWN cannot include an answer.",
            )

        if request.selected_option_id is not None:
            option = await session.get(QuestionOption, request.selected_option_id)
            if option is None or option.question_version_id != selection.question_version_id:
                raise HTTPException(
                    status_code=400,
                    detail="Selected option does not belong to this question",
                )

        selection.selected_option_id = None if request.is_unknown else request.selected_option_id
        selection.response_text = "UNKNOWN" if request.is_unknown else request.response_text
        selection.is_correct = None if request.is_unknown else selection.is_correct
        selection.answered_at = datetime.now(timezone.utc)

        await session.flush()
        await session.commit()
        await session.refresh(selection)

        return PracticeQuestionAnswerResponse(
            practice_question_selection_id=selection.id,
            is_received=True,
            is_unknown=request.is_unknown,
            position=selection.position,
        )


# ============================================================================
# Practice Completion Endpoints
# ============================================================================


@practice_router.post(
    "/sessions/{session_id}/complete",
    response_model=PracticeSessionResult,
    summary="Complete a practice session",
    description="Submits the practice session. Corrects objective questions, updates mastery.",
)
async def complete_practice_session(
    session_id: UUID,
    request: PracticeSessionCompleteRequest = PracticeSessionCompleteRequest(),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> PracticeSessionResult:
    """Complete a practice session."""
    external_identity_id = identity.external_user_id

    async with session_factory() as session:
        practice_repo = PracticeSessionRepository(session)
        practice_session = await practice_repo.get_by_id(session_id)

        if not practice_session:
            raise HTTPException(status_code=404, detail="Practice session not found")

        if practice_session.external_identity_id != external_identity_id:
            raise HTTPException(status_code=403, detail="Access denied")

        if practice_session.status != "active":
            raise HTTPException(status_code=400, detail="Practice session is already completed")

        selection_repo = PracticeQuestionSelectionRepository(session)
        selections = await selection_repo.list_by_session(session_id)

        # perf: resolve every pending selection's official answer key in ONE
        # batched query instead of one query per selection (was: one
        # answer_key_entries/booklet_questions round trip per answered
        # question in the session - measured 1:1 with the number of
        # answered questions against real Postgres). Same source of truth
        # (`answer_key.py`) and identical selection rule (latest official
        # revision) as `resolve_official_correct_option_id`, and the same
        # pattern already used by `PracticeSessionService.complete_session`.
        pending_version_ids = [
            selection.question_version_id
            for selection in selections
            if selection.answered_at is not None
            and selection.is_correct is None
            and selection.selected_option_id is not None
        ]
        answer_key_snapshots: dict = {}
        if pending_version_ids:
            answer_key_snapshots = await resolve_official_answer_key_snapshots(
                session, pending_version_ids
            )

        # Correct each answered, objective question using the official answer key.
        for selection in selections:
            if selection.answered_at is None or selection.is_correct is not None:
                continue
            if selection.selected_option_id is None:
                # Discursive or unanswered-objective: nothing to auto-correct.
                continue

            snapshot = answer_key_snapshots.get(selection.question_version_id)
            if snapshot is None:
                # No official answer key available yet: leave uncorrected (None).
                continue
            _revision_id, correct_option_id = snapshot

            is_correct = selection.selected_option_id == correct_option_id
            selection.is_correct = is_correct
            selection.points_awarded = 1.0 if is_correct else 0.0

        await session.flush()

        total = len(selections)
        answered = sum(1 for s in selections if s.answered_at is not None)
        correct = sum(1 for s in selections if s.is_correct is True)
        incorrect = sum(1 for s in selections if s.is_correct is False)
        unanswered = total - answered

        score = sum(
            float(s.points_awarded or 0) for s in selections if s.is_correct is True
        )
        max_score = float(total)
        percentage = (score / max_score * 100) if max_score > 0 else 0.0

        practice_service = PracticeSessionService()
        await practice_service.mark_completed(session, practice_session)

        updated_level = practice_session.recommended_difficulty

        if practice_session.content_node_id:
            mastery_service = ContentMasteryService()
            history_service = LearningHistoryService()

            mastery = await mastery_service.get_or_create_mastery(
                session,
                external_identity_id,
                practice_session.content_node_id,
            )

            for selection in selections:
                if not selection.answered_at:
                    continue

                await history_service.record_history(
                    session,
                    external_identity_id,
                    ActivityType.INDIVIDUAL_PRACTICE,
                    selection.question_version_id,
                    DifficultyLevel(selection.difficulty_level),
                    is_correct=selection.is_correct,
                    selected_option_id=selection.selected_option_id,
                    response_text=selection.response_text,
                    points_awarded=selection.points_awarded,
                    response_time_ms=selection.response_time_ms,
                    content_node_id=practice_session.content_node_id,
                    practice_session_id=session_id,
                    practice_question_selection_id=selection.id,
                )

                if selection.is_correct is not None:
                    await mastery_service.update_mastery_after_response(
                        session,
                        mastery,
                        selection.is_correct,
                    )

            updated_level = mastery.current_level

        await session.commit()
        await session.refresh(practice_session)

        return PracticeSessionResult(
            practice_session_id=practice_session.id,
            total_questions=total,
            answered_count=answered,
            correct_count=correct,
            incorrect_count=incorrect,
            unanswered_count=unanswered,
            score=score,
            percentage=percentage,
            updated_mastery_level=updated_level,
        )


@practice_router.get(
    "/sessions/{session_id}/result",
    response_model=PracticeSessionResult,
    summary="Get the result of a completed practice session",
)
async def get_practice_session_result(
    session_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> PracticeSessionResult:
    """Return the (already computed) result of a completed session."""
    external_identity_id = identity.external_user_id

    async with session_factory() as session:
        practice_repo = PracticeSessionRepository(session)
        practice_session = await practice_repo.get_by_id(session_id)

        if not practice_session:
            raise HTTPException(status_code=404, detail="Practice session not found")

        if practice_session.external_identity_id != external_identity_id:
            raise HTTPException(status_code=403, detail="Access denied")

        if practice_session.status != "completed":
            raise HTTPException(status_code=400, detail="Practice session is not completed yet")

        selection_repo = PracticeQuestionSelectionRepository(session)
        selections = await selection_repo.list_by_session(session_id)

        total = len(selections)
        answered = sum(1 for s in selections if s.answered_at is not None)
        correct = sum(1 for s in selections if s.is_correct is True)
        incorrect = sum(1 for s in selections if s.is_correct is False)
        unanswered = total - answered
        score = sum(
            float(s.points_awarded or 0) for s in selections if s.is_correct is True
        )
        max_score = float(total)
        percentage = (score / max_score * 100) if max_score > 0 else 0.0

        mastery_level = practice_session.recommended_difficulty
        if practice_session.content_node_id:
            mastery_repo = StudentContentMasteryRepository(session)
            mastery = await mastery_repo.get_by_student_content(
                external_identity_id, practice_session.content_node_id
            )
            if mastery:
                mastery_level = mastery.current_level

        return PracticeSessionResult(
            practice_session_id=practice_session.id,
            total_questions=total,
            answered_count=answered,
            correct_count=correct,
            incorrect_count=incorrect,
            unanswered_count=unanswered,
            score=score,
            percentage=percentage,
            updated_mastery_level=mastery_level,
        )


# ============================================================================
# Mastery Endpoints
# ============================================================================


@practice_router.get(
    "/mastery",
    response_model=StudentMasteryListResponse,
    summary="Get student's mastery in all contents",
    description="Returns student's mastery levels across all content nodes.",
)
async def get_student_mastery(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> StudentMasteryListResponse:
    """Get all mastery records for a student."""
    external_identity_id = identity.external_user_id

    async with session_factory() as session:
        mastery_repo = StudentContentMasteryRepository(session)
        masteries = await mastery_repo.list_by_student(external_identity_id)

        responses = [
            StudentContentMasteryResponse(
                content_node_id=m.content_node_id,
                mastery_score=float(m.mastery_score),
                current_level=m.current_level,
                questions_answered=m.questions_answered,
                questions_correct=m.questions_correct,
                confidence=float(m.confidence),
                last_activity_at=m.last_activity_at,
            )
            for m in masteries
        ]

        lowest = await mastery_repo.get_lowest_mastery(external_identity_id)

        return StudentMasteryListResponse(
            external_identity_id=external_identity_id,
            masteries=responses,
            next_recommended_content_node_id=lowest.content_node_id if lowest else None,
        )


# ============================================================================
# Learning History Endpoints
# ============================================================================


@practice_router.get(
    "/history",
    response_model=LearningHistoryListResponse,
    summary="Get student's learning history",
    description="Returns all recorded learning activities (assessments and practices).",
)
async def get_learning_history(
    limit: int = 100,
    offset: int = 0,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> LearningHistoryListResponse:
    """Get learning history for a student."""
    external_identity_id = identity.external_user_id

    async with session_factory() as session:
        history_repo = LearningHistoryRepository(session)
        entries = await history_repo.list_by_student(
            external_identity_id,
            limit=limit,
            offset=offset,
        )

        responses = [
            LearningHistoryEntryResponse(
                id=entry.id,
                activity_type=entry.activity_type,
                question_version_id=entry.question_version_id,
                difficulty_level=entry.difficulty_level,
                is_correct=entry.is_correct,
                response_text=entry.response_text,
                points_awarded=float(entry.points_awarded) if entry.points_awarded else None,
                response_time_ms=entry.response_time_ms,
                content_node_id=entry.content_node_id,
                created_at=entry.created_at,
            )
            for entry in entries
        ]

        return LearningHistoryListResponse(
            external_identity_id=external_identity_id,
            entries=responses,
            total_count=len(entries),
        )



# Missing import fix
from ...services.learning_path import LearningHistoryService
