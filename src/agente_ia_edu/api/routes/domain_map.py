"""Student domain-map and next-best-action API."""

from fastapi import APIRouter, Depends

from agente_ia_edu.api.dependencies import get_session_factory
from agente_ia_edu.api.routes.diagnostic import get_current_diagnostic_identity
from agente_ia_edu.api.schemas.domain_map import StudentDomainMapResponse
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.domain_map import DomainMapService


domain_map_router = APIRouter(prefix="/api/v1/student", tags=["domain-map"])


@domain_map_router.get("/domain-map", response_model=StudentDomainMapResponse)
async def get_student_domain_map(
    identity: ExternalIdentityContext = Depends(get_current_diagnostic_identity),
    session_factory=Depends(get_session_factory),
) -> StudentDomainMapResponse:
    async with session_factory() as session:
        payload = await DomainMapService(session).build(
            student_id=identity.external_user_id,
            identity=identity,
        )
        return StudentDomainMapResponse(**payload)


__all__ = ["domain_map_router"]