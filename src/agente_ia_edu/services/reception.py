"""Application service for school reception and diagnostic release."""

from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import func, or_, select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import InitialDiagnostic, ReceptionCandidate, UserInvitation
from agente_ia_edu.services.admin import PlatformAdminService
from agente_ia_edu.services.invitation import InvitationService


class ReceptionService:
    def __init__(self, session: AsyncSession):
        self.session = session
        self.admin_service = PlatformAdminService(session)
        self.invitation_service = InvitationService(session)

    async def create_candidate(self, *, actor_id: str, **data) -> ReceptionCandidate:
        duplicate = await self.session.scalar(
            select(ReceptionCandidate.id).where(
                ReceptionCandidate.school_id == data["school_id"],
                or_(
                    func.lower(ReceptionCandidate.email) == data["email"].lower(),
                    ReceptionCandidate.phone == data["phone"],
                ),
            ).limit(1)
        )
        if duplicate:
            raise ValueError("Já existe um pré-cadastro nesta escola com o mesmo e-mail ou telefone.")

        candidate = ReceptionCandidate(
            **data,
            status="PRE_REGISTRATION",
            created_by_external_id=actor_id,
        )
        self.session.add(candidate)
        await self.session.flush()
        await self.admin_service.log_action(
            performed_by_external_id=actor_id,
            action="RECEPTION_CANDIDATE_CREATED",
            entity_type="RECEPTION_CANDIDATE",
            entity_id=str(candidate.id),
            school_id=candidate.school_id,
            metadata={"academic_year": candidate.academic_year},
        )
        await self.session.commit()
        await self.session.refresh(candidate)
        return candidate

    async def list_candidates(
        self,
        *,
        school_id: UUID,
        query: str | None = None,
        scope_type: str | None = None,
        scope_external_id: str | None = None,
        limit: int = 50,
    ) -> list[ReceptionCandidate]:
        statement = select(ReceptionCandidate).where(ReceptionCandidate.school_id == school_id)
        scope_columns = {
            "UNIT": ReceptionCandidate.unit_id,
            "SEGMENT": ReceptionCandidate.segment_id,
            "GRADE_LEVEL": ReceptionCandidate.grade_level,
            "CLASSROOM": ReceptionCandidate.classroom_id,
        }
        if scope_type in scope_columns and scope_external_id:
            statement = statement.where(scope_columns[scope_type] == scope_external_id)
        if query:
            pattern = f"%{query.strip().lower()}%"
            statement = statement.where(or_(
                func.lower(ReceptionCandidate.full_name).like(pattern),
                func.lower(ReceptionCandidate.email).like(pattern),
                ReceptionCandidate.phone.like(f"%{query.strip()}%"),
            ))
        result = await self.session.execute(
            statement.order_by(ReceptionCandidate.created_at.desc()).limit(limit)
        )
        return list(result.scalars().all())

    async def get_candidate(self, candidate_id: UUID) -> ReceptionCandidate | None:
        return await self.session.get(ReceptionCandidate, candidate_id)

    async def release_diagnostic(
        self, *, candidate: ReceptionCandidate, actor_id: str
    ) -> UserInvitation:
        if candidate.status != "PRE_REGISTRATION" or candidate.invitation_id:
            raise ValueError("O diagnóstico já foi liberado para este candidato.")

        scope_type = "CLASSROOM" if candidate.classroom_id else "GRADE_LEVEL"
        scope_external_id = candidate.classroom_id or candidate.grade_level
        invitation = await self.invitation_service.create_invitation(
            school_id=candidate.school_id,
            external_email=candidate.email,
            role="STUDENT",
            scope_type=scope_type,
            scope_external_id=scope_external_id,
            display_name=candidate.full_name,
            invited_by_external_id=actor_id,
            metadata={
                "reception_candidate_id": str(candidate.id),
                "academic_year": candidate.academic_year,
                "unit_id": candidate.unit_id,
                "segment": candidate.segment_id,
                "grade_level": candidate.grade_level,
                "classroom_id": candidate.classroom_id,
                "preferred_name": candidate.preferred_name,
            },
        )
        candidate.status = "DIAGNOSTIC_RELEASED"
        candidate.invitation_id = invitation.id
        candidate.released_by_external_id = actor_id
        candidate.released_at = datetime.now(timezone.utc)
        await self.admin_service.log_action(
            performed_by_external_id=actor_id,
            action="INITIAL_DIAGNOSTIC_RELEASED",
            entity_type="RECEPTION_CANDIDATE",
            entity_id=str(candidate.id),
            school_id=candidate.school_id,
            metadata={"invitation_id": str(invitation.id)},
        )
        await self.session.commit()
        await self.session.refresh(candidate)
        await self.session.refresh(invitation)
        return invitation

    async def activate_access(
        self, *, token: str, external_student_id: str
    ) -> ReceptionCandidate:
        invitation = await self.invitation_service.validate_token(token)
        invitation_id = invitation.id
        candidate = await self.session.scalar(
            select(ReceptionCandidate).where(ReceptionCandidate.invitation_id == invitation_id)
        )
        if candidate is None:
            raise ValueError("O convite não está associado a um atendimento.")
        await self.invitation_service.activate_invitation(token, external_student_id)
        await self.session.refresh(candidate)
        candidate.external_student_id = external_student_id
        await self.admin_service.log_action(
            performed_by_external_id=external_student_id,
            action="DIAGNOSTIC_ACCESS_ACTIVATED",
            entity_type="RECEPTION_CANDIDATE",
            entity_id=str(candidate.id),
            school_id=candidate.school_id,
            metadata={"invitation_id": str(invitation_id)},
        )
        await self.session.commit()
        await self.session.refresh(candidate)
        return candidate

    async def latest_diagnostic(
        self, candidate: ReceptionCandidate
    ) -> InitialDiagnostic | None:
        if not candidate.external_student_id:
            return None
        return await self.session.scalar(
            select(InitialDiagnostic).where(
                InitialDiagnostic.student_id == candidate.external_student_id,
                InitialDiagnostic.school_id == candidate.school_id,
                InitialDiagnostic.academic_year == candidate.academic_year,
            ).order_by(InitialDiagnostic.created_at.desc()).limit(1)
        )


__all__ = ["ReceptionService"]