"""Student attempt endpoints for assessment execution."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession
from sqlalchemy.orm import selectinload

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.diagnostic import get_current_diagnostic_identity
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.api.schemas.assessments import (
    AttemptAnswerSaveRequest,
    AttemptAnswerSaveResponse,
    AttemptDetailResponse,
    AttemptResultResponse,
    AttemptStartRequest,
    AttemptStartResponse,
    AttemptSubmitResponse,
    AnswerItemResponse,
    AssessmentItemResponse,
    QuestionOptionResponse,
)
from agente_ia_edu.db.models import (
    Assessment,
    AssessmentAnswer,
    AssessmentAssignment,
    AssessmentAttempt,
    AssessmentItem,
    AssessmentPublication,
    AssessmentVersion,
    ContentQuestionLink,
    QuestionVersion,
    StudentContentMastery,
)
from agente_ia_edu.repositories.assessments import (
    AssessmentAnswerRepository,
    AssessmentAttemptRepository,
    AssessmentItemRepository,
    AssessmentPublicationRepository,
)
from agente_ia_edu.services.attempt_execution import (
    AttemptExecutionService,
    AttemptResultService,
    PublicationAvailabilityService,
)
from agente_ia_edu.services.learning_path import (
    ContentMasteryService,
    LearningHistoryService,
)
from agente_ia_edu.services.learning_path_policies import ActivityType, DifficultyLevel

router = APIRouter(prefix="/api/v1/assessments", tags=["attempts"])


@router.post(
    "/publications/{publication_id}/attempts",
    response_model=AttemptStartResponse,
    status_code=201,
)
async def start_attempt(
    publication_id: UUID = Path(...),
    payload: AttemptStartRequest = Depends(),
    identity: ExternalIdentityContext = Depends(get_current_diagnostic_identity),
    session_factory=Depends(get_session_factory),
) -> AttemptStartResponse:
    """Start a new assessment attempt for the authenticated student.
    
    Validates:
    - Publication exists and is available
    - Assessment version is published
    - Student hasn't exceeded attempt limit
    - Time windows are respected
    
    Returns:
    - Attempt ID
    - Attempt number
    - Expiration time
    """
    external_identity_id = identity.external_user_id
    async with session_factory() as session:
        attempt_repo = AssessmentAttemptRepository(session)

        publication = await session.scalar(
            select(AssessmentPublication)
            .where(AssessmentPublication.id == publication_id)
            .options(
                selectinload(AssessmentPublication.assessment_version)
                .selectinload(AssessmentVersion.items)
                .selectinload(AssessmentItem.question_version)
                .selectinload(QuestionVersion.options)
            )
        )
        if publication is None:
            raise HTTPException(status_code=404, detail="Publication not found")

        assessment = await session.get(
            Assessment, publication.assessment_version.assessment_id
        )
        assignment = await session.scalar(
            select(AssessmentAssignment).where(
                AssessmentAssignment.publication_id == publication.id,
                AssessmentAssignment.recipient_type == "STUDENT",
                AssessmentAssignment.recipient_id == external_identity_id,
                AssessmentAssignment.status.in_(("PENDING", "IN_PROGRESS")),
            )
        )
        if assignment is None:
            raise HTTPException(status_code=403, detail="Assessment assignment required")
        if (
            assessment.school_id is None
            or identity.institution_id is None
            or str(assessment.school_id) != str(identity.institution_id)
            or assignment.school_id != assessment.school_id
        ):
            raise HTTPException(status_code=403, detail="Assessment school scope denied")
        assignment_scope = assignment.metadata_ or {}
        expected_scope = assignment_scope.get("scope_external_id")
        if expected_scope and expected_scope != identity.classroom_id:
            raise HTTPException(status_code=403, detail="Assessment academic scope denied")

        # Check availability
        if not PublicationAvailabilityService.is_available(
            publication_status=publication.status,
            starts_at=publication.starts_at,
            ends_at=publication.ends_at,
            assessment_version_status=publication.assessment_version.status,
        ):
            raise HTTPException(status_code=403, detail="Publication is not available")

        # Check attempt limit
        existing_attempts = await attempt_repo.list_by_publication(
            publication_id=publication_id,
            external_identity_id=external_identity_id,
        )
        if (
            publication.attempts_allowed is not None
            and len(existing_attempts) >= publication.attempts_allowed
        ):
            raise HTTPException(status_code=409, detail="Attempt limit exceeded")

        items = sorted(publication.assessment_version.items, key=lambda item: item.position)
        content_rows = (await session.execute(
            select(ContentQuestionLink.question_version_id, ContentQuestionLink.content_node_id)
            .where(ContentQuestionLink.question_version_id.in_([
                item.question_version_id for item in items
            ]))
            .order_by(ContentQuestionLink.question_version_id, ContentQuestionLink.content_node_id)
        )).all()
        content_by_version: dict[UUID, list[str]] = {}
        for question_version_id, content_node_id in content_rows:
            content_by_version.setdefault(question_version_id, []).append(str(content_node_id))
        item_snapshots = [
            {
                "assessment_item_id": str(item.id),
                "question_version_id": str(item.question_version_id),
                "position": item.position,
                "points": item.points,
                "is_required": item.is_required,
                "canonical_text": item.question_version.canonical_text,
                "difficulty_level": item.question_version.recommended_difficulty or "EASY",
                "options": [
                    {
                        "id": str(option.id),
                        "option_key": option.option_key,
                        "text": option.text,
                        "position": option.position,
                    }
                    for option in sorted(item.question_version.options, key=lambda option: option.position)
                    if option.is_valid_option
                ],
                "frozen_correct_option_id": str(item.frozen_correct_option_id),
                "answer_key_revision_id": str(item.answer_key_revision_id),
                "content_node_ids": content_by_version.get(item.question_version_id, []),
            }
            for item in items
        ]

        attempt_number = len(existing_attempts) + 1
        now = datetime.now(timezone.utc)
        expires_at = AttemptExecutionService.compute_expires_at(
            started_at=now,
            time_limit_seconds=publication.time_limit_seconds,
            publication_ends_at=publication.ends_at,
        )

        # Create attempt
        attempt = await attempt_repo.create(
            publication_id=publication_id,
            external_identity_id=external_identity_id,
            attempt_number=attempt_number,
            started_at=now,
            expires_at=expires_at,
            assignment_id=assignment.id,
            metadata={
                "assessment_id": str(assessment.id),
                "assessment_version_id": str(publication.assessment_version_id),
                "publication_id": str(publication.id),
                "assignment_id": str(assignment.id),
                "student_external_id": external_identity_id,
                "school_id": str(assessment.school_id),
                "unit_id": identity.unit_id,
                "segment": identity.metadata.get("segment"),
                "grade_level": identity.grade_level,
                "classroom_id": identity.classroom_id,
                "started_at": now.isoformat(),
                "items": item_snapshots,
            },
        )
        assignment.status = "IN_PROGRESS"
        await session.commit()

        return AttemptStartResponse(
            id=attempt.id,
            publication_id=attempt.publication_id,
            assignment_id=assignment.id,
            assessment_version_id=publication.assessment_version_id,
            attempt_number=attempt.attempt_number,
            status=attempt.status,
            started_at=attempt.started_at,
            expires_at=attempt.expires_at,
            score=attempt.score,
            max_score=attempt.max_score,
        )


@router.get(
    "/attempts/{attempt_id}",
    response_model=AttemptDetailResponse,
)
async def get_attempt(
    attempt_id: UUID = Path(...),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> AttemptDetailResponse:
    """Get attempt details including assessment items and options.
    
    Does NOT return answer key or correction until submission.
    """
    external_identity_id = identity.external_user_id
    async with session_factory() as session:
        attempt_repo = AssessmentAttemptRepository(session)
        attempt = await attempt_repo.get(attempt_id)
        if attempt is None:
            raise HTTPException(status_code=404, detail="Attempt not found")

        # Verify ownership
        if attempt.external_identity_id != external_identity_id:
            raise HTTPException(status_code=403, detail="Access denied")

        # Check expiration
        if AttemptExecutionService.is_attempt_expired(expires_at=attempt.expires_at):
            attempt.status = "expired"
            await session.commit()
            raise HTTPException(status_code=403, detail="Attempt has expired")

        snapshot = attempt.metadata_ or {}
        items = snapshot.get("items", [])
        items_response = [
            AssessmentItemResponse(
                id=UUID(item["assessment_item_id"]),
                position=item["position"],
                question_number=item["position"],
                total_questions=len(items),
                points=item["points"],
                is_required=item["is_required"],
                question_version_id=UUID(item["question_version_id"]),
                canonical_text=item["canonical_text"],
                options=[QuestionOptionResponse(**option) for option in item["options"]],
            )
            for item in items
        ]

        return AttemptDetailResponse(
            id=attempt.id,
            publication_id=attempt.publication_id,
            assignment_id=attempt.assignment_id,
            assessment_version_id=UUID(snapshot["assessment_version_id"]),
            attempt_number=attempt.attempt_number,
            status=attempt.status,
            started_at=attempt.started_at,
            submitted_at=attempt.submitted_at,
            expires_at=attempt.expires_at,
            score=attempt.score,
            max_score=attempt.max_score,
            correct_answers=attempt.correct_answers,
            answered_count=attempt.answered_count,
            total_questions=len(items),
            items=items_response,
        )


@router.put(
    "/attempts/{attempt_id}/answers/{assessment_item_id}",
    response_model=AttemptAnswerSaveResponse,
)
async def save_answer(
    payload: AttemptAnswerSaveRequest,
    attempt_id: UUID = Path(...),
    assessment_item_id: UUID = Path(...),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> AttemptAnswerSaveResponse:
    """Save or update a student answer to an assessment item.
    
    Allows:
    - Multiple updates before submission
    - Both objective (selected_option_id) and discursive (response_text)
    
    Validates:
    - Attempt belongs to student
    - Attempt is in progress
    - Item belongs to assessment
    - Option (if provided) belongs to question
    """
    external_identity_id = identity.external_user_id
    async with session_factory() as session:
        attempt_repo = AssessmentAttemptRepository(session)
        answer_repo = AssessmentAnswerRepository(session)

        # Get and verify attempt
        attempt = await attempt_repo.get(attempt_id)
        if attempt is None:
            raise HTTPException(status_code=404, detail="Attempt not found")

        if attempt.external_identity_id != external_identity_id:
            raise HTTPException(status_code=403, detail="Access denied")

        if attempt.status != "in_progress":
            raise HTTPException(status_code=409, detail="Attempt is not in progress")

        # Check expiration
        if AttemptExecutionService.is_attempt_expired(expires_at=attempt.expires_at):
            attempt.status = "expired"
            await session.commit()
            raise HTTPException(status_code=403, detail="Attempt has expired")

        snapshot_item = next(
            (
                item for item in (attempt.metadata_ or {}).get("items", [])
                if item["assessment_item_id"] == str(assessment_item_id)
            ),
            None,
        )
        if snapshot_item is None:
            raise HTTPException(status_code=404, detail="Item not found")

        if payload.is_unknown and (payload.selected_option_id is not None or payload.response_text):
            raise HTTPException(status_code=400, detail="UNKNOWN cannot include an answer")

        # Check option validity if provided
        if payload.selected_option_id is not None:
            allowed_option_ids = {option["id"] for option in snapshot_item["options"]}
            if str(payload.selected_option_id) not in allowed_option_ids:
                raise HTTPException(
                    status_code=400, detail="Option does not belong to this question"
                )

        # Create or update answer
        existing_answer = await answer_repo.get_by_attempt_and_item(
            attempt_id=attempt_id,
            assessment_item_id=assessment_item_id,
        )

        if existing_answer is None:
            answer = await answer_repo.create(
                attempt_id=attempt_id,
                assessment_item_id=assessment_item_id,
                selected_option_id=None if payload.is_unknown else payload.selected_option_id,
                response_text="UNKNOWN" if payload.is_unknown else payload.response_text,
            )
        else:
            answer = existing_answer
            answer.selected_option_id = None if payload.is_unknown else payload.selected_option_id
            answer.response_text = "UNKNOWN" if payload.is_unknown else payload.response_text
            answer.is_correct = None
            answer.correction_status = "pending"
            answer.updated_at = datetime.now(timezone.utc)

        await session.commit()

        return AttemptAnswerSaveResponse(
            assessment_item_id=answer.assessment_item_id,
            selected_option_id=answer.selected_option_id,
            response_text=answer.response_text,
            is_unknown=payload.is_unknown,
            correction_status=answer.correction_status,
            first_answered_at=answer.first_answered_at,
        )


@router.post(
    "/attempts/{attempt_id}/submit",
    response_model=AttemptSubmitResponse,
)
async def submit_attempt(
    attempt_id: UUID = Path(...),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> AttemptSubmitResponse:
    """Submit and finalize an attempt.
    
    After submission:
    - Attempt status changes to 'submitted'
    - Objective answers are auto-corrected
    - Score is calculated
    - Discursive answers remain pending
    """
    external_identity_id = identity.external_user_id
    async with session_factory() as session:
        attempt_repo = AssessmentAttemptRepository(session)
        answer_repo = AssessmentAnswerRepository(session)

        attempt = await attempt_repo.get(attempt_id)
        if attempt is None:
            raise HTTPException(status_code=404, detail="Attempt not found")

        if attempt.external_identity_id != external_identity_id:
            raise HTTPException(status_code=403, detail="Access denied")

        if attempt.status != "in_progress":
            raise HTTPException(status_code=409, detail="Attempt is not in progress")

        # Check expiration
        if AttemptExecutionService.is_attempt_expired(expires_at=attempt.expires_at):
            attempt.status = "expired"
            await session.commit()
            raise HTTPException(status_code=403, detail="Attempt has expired")

        submitted_at = datetime.now(timezone.utc)
        await attempt_repo.finalize(attempt, submitted_at=submitted_at)
        answers = await answer_repo.list_by_attempt(attempt_id)
        answers_by_item = {str(answer.assessment_item_id): answer for answer in answers}
        snapshot_items = (attempt.metadata_ or {}).get("items", [])
        correct_count = 0
        incorrect_count = 0
        unknown_count = 0
        total_points = 0.0
        history_service = LearningHistoryService()
        mastery_service = ContentMasteryService()

        for snapshot_item in snapshot_items:
            answer = answers_by_item.get(snapshot_item["assessment_item_id"])
            if answer is None:
                continue
            answer.submitted_at = submitted_at
            answer.is_final = True
            is_unknown = answer.response_text == "UNKNOWN"
            if is_unknown:
                answer.is_correct = None
                answer.points_awarded = 0
                answer.correction_status = "ungraded"
                unknown_count += 1
            elif answer.selected_option_id is not None:
                answer.is_correct = (
                    str(answer.selected_option_id)
                    == snapshot_item["frozen_correct_option_id"]
                )
                answer.points_awarded = snapshot_item["points"] if answer.is_correct else 0
                answer.correction_status = "correct" if answer.is_correct else "incorrect"
                correct_count += int(answer.is_correct)
                incorrect_count += int(not answer.is_correct)
                total_points += float(answer.points_awarded)
            else:
                answer.is_correct = None
                answer.points_awarded = 0
                answer.correction_status = "ungraded"
            answer.corrected_at = submitted_at

            for content_node_id in snapshot_item.get("content_node_ids", []):
                await history_service.record_history(
                    session,
                    external_identity_id,
                    ActivityType.OFFICIAL_ASSESSMENT,
                    UUID(snapshot_item["question_version_id"]),
                    DifficultyLevel(snapshot_item["difficulty_level"]),
                    is_correct=answer.is_correct,
                    selected_option_id=answer.selected_option_id,
                    response_text=answer.response_text,
                    points_awarded=float(answer.points_awarded or 0),
                    response_time_ms=answer.response_time_ms,
                    content_node_id=UUID(content_node_id),
                    assessment_attempt_id=attempt.id,
                )
                if answer.is_correct is not None:
                    mastery = await mastery_service.get_or_create_mastery(
                        session, external_identity_id, UUID(content_node_id)
                    )
                    await mastery_service.update_mastery_after_response(
                        session, mastery, answer.is_correct
                    )

        attempt.correct_answers = correct_count
        attempt.answered_count = len(answers)
        attempt.score = total_points
        attempt.max_score = float(sum(item["points"] for item in snapshot_items))
        attempt.duration_seconds = AttemptExecutionService.calculate_duration_seconds(
            started_at=attempt.started_at, submitted_at=submitted_at
        )
        attempt.metadata_ = {
            **(attempt.metadata_ or {}),
            "submitted_at": submitted_at.isoformat(),
            "result": {
                "correct_answers": correct_count,
                "incorrect_answers": incorrect_count,
                "unknown_answers": unknown_count,
                "unanswered": len(snapshot_items) - len(answers),
            },
        }
        assignment = await session.get(AssessmentAssignment, attempt.assignment_id)
        if assignment:
            assignment.status = "COMPLETED"
            assignment.completed_at = submitted_at
        await session.commit()

        return AttemptSubmitResponse(
            id=attempt.id,
            status=attempt.status,
            submitted_at=attempt.submitted_at,
            score=attempt.score,
            max_score=attempt.max_score,
            correct_answers=attempt.correct_answers,
            answered_count=attempt.answered_count,
            incorrect_answers=incorrect_count,
            unknown_answers=unknown_count,
            unanswered=len(snapshot_items) - len(answers),
        )


@router.get(
    "/attempts/{attempt_id}/result",
    response_model=AttemptResultResponse,
)
async def get_result(
    attempt_id: UUID = Path(...),
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> AttemptResultResponse:
    """Get attempt result after submission.
    
    Only available after submission.
    Shows score, percentage, breakdown by item.
    """
    external_identity_id = identity.external_user_id
    async with session_factory() as session:
        attempt_repo = AssessmentAttemptRepository(session)
        answer_repo = AssessmentAnswerRepository(session)

        attempt = await attempt_repo.get(attempt_id)
        if attempt is None:
            raise HTTPException(status_code=404, detail="Attempt not found")

        if attempt.external_identity_id != external_identity_id:
            raise HTTPException(status_code=403, detail="Access denied")

        if attempt.status != "submitted":
            raise HTTPException(status_code=409, detail="Attempt has not been submitted yet")

        # Build result summary
        answers = await answer_repo.list_by_attempt(attempt_id)
        answers_response = [
            AnswerItemResponse(
                assessment_item_id=ans.assessment_item_id,
                selected_option_id=ans.selected_option_id,
                response_text=ans.response_text,
                is_correct=ans.is_correct,
                points_awarded=float(ans.points_awarded) if ans.points_awarded else 0,
                correction_status=ans.correction_status,
            )
            for ans in answers
        ]

        snapshot_items = (attempt.metadata_ or {}).get("items", [])
        incorrect_count = sum(answer.is_correct is False for answer in answers)
        unknown_count = sum(answer.response_text == "UNKNOWN" for answer in answers)
        result_summary = AttemptResultService.build_result_summary(
            score=attempt.score,
            max_score=attempt.max_score,
            correct_answers=attempt.correct_answers,
            answered_count=attempt.answered_count,
            total_items=len(snapshot_items),
            duration_seconds=attempt.duration_seconds,
        )

        return AttemptResultResponse(
            id=attempt.id,
            score=result_summary["score"],
            max_score=result_summary["max_score"],
            percentage=result_summary["percentage"],
            correct_answers=result_summary["correct_answers"],
            incorrect_answers=incorrect_count,
            unknown_answers=unknown_count,
            unanswered=result_summary["unanswered"],
            answered_count=result_summary["answered_count"],
            total_items=result_summary["total_items"],
            duration_seconds=result_summary["duration_seconds"],
            answers=answers_response,
        )
