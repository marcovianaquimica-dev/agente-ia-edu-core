"""Student attempt endpoints for assessment execution."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Path
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.api.dependencies import get_current_identity
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
from agente_ia_edu.db.models.assessments import AssessmentItem
from agente_ia_edu.db.session import create_session_factory
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

router = APIRouter(prefix="/api/v1/assessments", tags=["attempts"])


async def get_session_factory():
    """Get database session factory."""
    return create_session_factory()


@router.post(
    "/publications/{publication_id}/attempts",
    response_model=AttemptStartResponse,
    status_code=201,
)
async def start_attempt(
    publication_id: UUID = Path(...),
    payload: AttemptStartRequest = Depends(),
    identity: ExternalIdentityContext = Depends(get_current_identity),
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
        pub_repo = AssessmentPublicationRepository(session)
        attempt_repo = AssessmentAttemptRepository(session)

        # Get publication with version
        publication = await pub_repo.get(publication_id)
        if publication is None:
            raise HTTPException(status_code=404, detail="Publication not found")

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

        # Calculate expires_at
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
        )
        await session.commit()

        return AttemptStartResponse(
            id=attempt.id,
            publication_id=attempt.publication_id,
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
        item_repo = AssessmentItemRepository(session)

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

        # Get items
        items = await item_repo.list_by_version(attempt.publication.assessment_version_id)

        items_response = []
        for item in items:
            # Get question options (without showing correct answer)
            options_response = []
            for option in item.question_version.options:
                options_response.append(
                    QuestionOptionResponse(
                        id=option.id,
                        option_key=option.option_key,
                        text=option.text,
                        position=option.position,
                    )
                )

            items_response.append(
                AssessmentItemResponse(
                    id=item.id,
                    position=item.position,
                    points=item.points,
                    is_required=item.is_required,
                    question_version_id=item.question_version_id,
                    options=options_response,
                )
            )

        return AttemptDetailResponse(
            id=attempt.id,
            publication_id=attempt.publication_id,
            attempt_number=attempt.attempt_number,
            status=attempt.status,
            started_at=attempt.started_at,
            submitted_at=attempt.submitted_at,
            expires_at=attempt.expires_at,
            score=attempt.score,
            max_score=attempt.max_score,
            correct_answers=attempt.correct_answers,
            answered_count=attempt.answered_count,
            items=items_response,
        )


@router.put(
    "/attempts/{attempt_id}/answers/{assessment_item_id}",
    response_model=AttemptAnswerSaveResponse,
)
async def save_answer(
    attempt_id: UUID = Path(...),
    assessment_item_id: UUID = Path(...),
    payload: AttemptAnswerSaveRequest = Depends(),
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
        item_repo = AssessmentItemRepository(session)

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

        # Get and verify item
        item = await item_repo.get(assessment_item_id)
        if item is None:
            raise HTTPException(status_code=404, detail="Item not found")

        # Verify item belongs to this attempt's assessment
        if (
            item.assessment_version_id
            != attempt.publication.assessment_version_id
        ):
            raise HTTPException(status_code=400, detail="Item does not belong to this assessment")

        # Check option validity if provided
        if payload.selected_option_id is not None:
            from agente_ia_edu.db.models.official import QuestionOption

            option = await session.get(QuestionOption, payload.selected_option_id)
            if option is None:
                raise HTTPException(status_code=404, detail="Option not found")
            if option.question_version_id != item.question_version_id:
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
                selected_option_id=payload.selected_option_id,
                response_text=payload.response_text,
            )
        else:
            answer = await answer_repo.update(
                existing_answer,
                selected_option_id=payload.selected_option_id,
                response_text=payload.response_text,
            )

        await session.commit()

        return AttemptAnswerSaveResponse(
            assessment_item_id=answer.assessment_item_id,
            selected_option_id=answer.selected_option_id,
            response_text=answer.response_text,
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

        # Finalize attempt
        submitted_at = datetime.now(timezone.utc)
        await attempt_repo.finalize(attempt, submitted_at=submitted_at)

        # Correct objective answers
        answers = await answer_repo.list_by_attempt(attempt_id)
        correct_count = 0
        total_points = 0

        for answer in answers:
            item = await session.get(AssessmentItem, answer.assessment_item_id)
            if item is None:
                continue

            # Try to auto-correct (only works if answer key exists)
            try:
                await answer_repo.correct_objective(answer)
                if answer.is_correct:
                    correct_count += 1
                total_points += answer.points_awarded or 0
            except ValueError:
                # No answer key found, leave as pending
                pass

        # Update attempt scores
        attempt.correct_answers = correct_count
        attempt.answered_count = len(answers)
        attempt.score = float(total_points)
        attempt.max_score = float(sum(item.points for item in attempt.publication.assessment_version.items))
        attempt.duration_seconds = int((submitted_at - attempt.started_at).total_seconds())
        await session.commit()

        return AttemptSubmitResponse(
            id=attempt.id,
            status=attempt.status,
            submitted_at=attempt.submitted_at,
            score=attempt.score,
            max_score=attempt.max_score,
            correct_answers=attempt.correct_answers,
            answered_count=attempt.answered_count,
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

        result_summary = AttemptResultService.build_result_summary(
            score=attempt.score,
            max_score=attempt.max_score,
            correct_answers=attempt.correct_answers,
            answered_count=attempt.answered_count,
            total_items=len(attempt.publication.assessment_version.items),
            duration_seconds=attempt.duration_seconds,
        )

        return AttemptResultResponse(
            id=attempt.id,
            score=result_summary["score"],
            max_score=result_summary["max_score"],
            percentage=result_summary["percentage"],
            correct_answers=result_summary["correct_answers"],
            incorrect_answers=result_summary["incorrect_answers"],
            unanswered=result_summary["unanswered"],
            answered_count=result_summary["answered_count"],
            total_items=result_summary["total_items"],
            duration_seconds=result_summary["duration_seconds"],
            answers=answers_response,
        )
