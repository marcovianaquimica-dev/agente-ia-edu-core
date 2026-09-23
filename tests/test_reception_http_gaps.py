"""HTTP-layer gap-fill tests for reception.py (src/agente_ia_edu/api/routes/reception.py).

tests/test_reception_portal.py already covers the happy paths (create/list/
release/activate) plus two of _translate_invitation_error's branches
("Invalid invitation token" and "already been activated"). This file targets
what it never reached:

  * SECRETARY with no explicit school/unit/segment/grade/classroom scope
    (PLATFORM scope_type) - _require_reception_access's own 403, distinct
    from the role/school checks already covered elsewhere.
  * _candidate_response's DIAGNOSTIC_COMPLETED (INSUFFICIENT_EVIDENCE/
    CANCELLED) and DIAGNOSTIC_IN_PROGRESS branches - every existing test's
    diagnostic is either absent or COMPLETED.
  * 404 on GET candidate / POST diagnostic-release for an unknown candidate.
  * 409 on releasing a diagnostic twice (ReceptionService.release_diagnostic's
    ValueError -> HTTPException mapping).
  * _translate_invitation_error's "expired" branch, and its untranslated
    fallback (a ValueError from ReceptionService.activate_access itself,
    not from InvitationService, so none of the regexes match).
"""

from __future__ import annotations

import asyncio
import unittest
from datetime import datetime, timedelta, timezone
from uuid import UUID, uuid4

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.api.routes.reception import reception_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import InitialDiagnostic, School, UserInvitation
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext
from agente_ia_edu.services.invitation import InvitationService


class ReceptionHttpGapsTests(unittest.TestCase):
    def setUp(self):
        async def setup_database():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            school = School(code="RECGAP_A", name="Escola Gaps A")
            async with factory() as session:
                session.add(school)
                await session.commit()
                return engine, factory, school.id

        self.engine, self.session_factory, self.school_a = asyncio.run(setup_database())
        self.context = {
            "value": AuthenticatedUserContext(
                user_id="secretary-a", external_identity_id="secretary-a", role="SECRETARY",
                school_id=self.school_a, scope_type="UNIT", scope_external_id="UNIDADE_CENTRO",
            )
        }
        self.identity = {
            "value": ExternalIdentityContext(provider="test", external_user_id="secretary-a", roles=("secretary",))
        }
        app = FastAPI()
        app.include_router(reception_router)
        app.dependency_overrides[get_session_factory] = lambda: self.session_factory
        app.dependency_overrides[get_current_authenticated_context] = lambda: self.context["value"]
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    def candidate_payload(self, **overrides):
        payload = {
            "school_id": str(self.school_a),
            "full_name": "Bruno Alves Ferreira",
            "preferred_name": "Bruno",
            "birth_date": "2000-04-12",
            "guardian_name": None,
            "phone": "(11) 97777-1122",
            "email": "bruno@example.com",
            "academic_year": "2026",
            "unit_id": "UNIDADE_CENTRO",
            "segment_id": "ENSINO_MEDIO",
            "grade_level": "3_SERIE",
            "classroom_id": "TURMA_3A",
        }
        payload.update(overrides)
        return payload

    def create_candidate(self, **overrides):
        response = self.client.post(
            "/api/v1/reception/candidates", json=self.candidate_payload(**overrides)
        )
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()

    # -- _require_reception_access: SECRETARY with no explicit scope --------

    def test_secretary_without_explicit_scope_is_denied(self):
        self.context["value"] = AuthenticatedUserContext(
            user_id="secretary-platform", external_identity_id="secretary-platform", role="SECRETARY",
            school_id=self.school_a, scope_type="PLATFORM", scope_external_id=None,
        )
        response = self.client.get(
            "/api/v1/reception/candidates", params={"school_id": str(self.school_a)}
        )
        self.assertEqual(response.status_code, 403, response.text)
        self.assertIn("explicit school scope", response.json()["detail"])

    # -- _candidate_response: non-COMPLETED diagnostic status branches ------

    async def _attach_diagnostic(self, candidate_id, external_student_id, status):
        async with self.session_factory() as session:
            from agente_ia_edu.db.models import ReceptionCandidate
            candidate = await session.get(ReceptionCandidate, UUID(candidate_id))
            candidate.external_student_id = external_student_id
            diagnostic = InitialDiagnostic(
                student_id=external_student_id, school_id=self.school_a, academic_year="2026",
                status=status,
            )
            session.add(diagnostic)
            await session.commit()

    def test_candidate_detail_reflects_insufficient_evidence_as_diagnostic_completed(self):
        created = self.create_candidate(email="gap-insuff@example.com", phone="11911110001")
        asyncio.run(self._attach_diagnostic(created["id"], "student-insuff", "INSUFFICIENT_EVIDENCE"))
        detail = self.client.get(f"/api/v1/reception/candidates/{created['id']}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["status"], "DIAGNOSTIC_COMPLETED")

    def test_candidate_detail_reflects_in_progress_diagnostic(self):
        created = self.create_candidate(email="gap-progress@example.com", phone="11911110002")
        asyncio.run(self._attach_diagnostic(created["id"], "student-progress", "IN_PROGRESS"))
        detail = self.client.get(f"/api/v1/reception/candidates/{created['id']}")
        self.assertEqual(detail.status_code, 200, detail.text)
        self.assertEqual(detail.json()["status"], "DIAGNOSTIC_IN_PROGRESS")

    # -- 404s -----------------------------------------------------------------

    def test_get_candidate_not_found_returns_404(self):
        response = self.client.get(f"/api/v1/reception/candidates/{uuid4()}")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Atendimento não encontrado.")

    def test_release_diagnostic_candidate_not_found_returns_404(self):
        response = self.client.post(f"/api/v1/reception/candidates/{uuid4()}/diagnostic-release")
        self.assertEqual(response.status_code, 404, response.text)
        self.assertEqual(response.json()["detail"], "Atendimento não encontrado.")

    # -- 409: releasing an already-released candidate ------------------------

    def test_release_diagnostic_twice_returns_409(self):
        created = self.create_candidate(email="double-release@example.com", phone="11911110003")
        first = self.client.post(f"/api/v1/reception/candidates/{created['id']}/diagnostic-release")
        self.assertEqual(first.status_code, 200, first.text)
        second = self.client.post(f"/api/v1/reception/candidates/{created['id']}/diagnostic-release")
        self.assertEqual(second.status_code, 409, second.text)
        self.assertIn("já foi liberado", second.json()["detail"])

    # -- _translate_invitation_error: expired + untranslated fallback -------

    def test_activation_of_expired_invitation_is_localized(self):
        created = self.create_candidate(email="expired-token@example.com", phone="11911110004")
        released = self.client.post(
            f"/api/v1/reception/candidates/{created['id']}/diagnostic-release"
        ).json()
        token = released["activation_token"]

        async def expire_invitation():
            async with self.session_factory() as session:
                invitation = await session.scalar(
                    select(UserInvitation).where(UserInvitation.token == token)
                )
                invitation.expires_at = datetime.now(timezone.utc) - timedelta(days=1)
                await session.commit()

        asyncio.run(expire_invitation())

        self.identity["value"] = ExternalIdentityContext(
            provider="test", external_user_id="student-expired", roles=("student",)
        )
        response = self.client.post(
            "/api/v1/reception/diagnostic-access/activate", json={"token": token}
        )
        self.assertEqual(response.status_code, 400, response.text)
        detail = response.json()["detail"]
        self.assertNotEqual(detail, "Invitation has expired")
        self.assertIn("expirou", detail.lower())

    def test_activation_error_without_regex_match_is_returned_untranslated(self):
        # An invitation valid at InvitationService level but with no
        # ReceptionCandidate pointing at it (data drift / a candidate
        # deleted after release, or - as built here - an invitation issued
        # outside the reception flow entirely) makes
        # ReceptionService.activate_access raise a ValueError whose text
        # ("O convite não está associado a um atendimento.") matches none
        # of _translate_invitation_error's regexes, exercising its final
        # `return detail` fallback.
        async def create_orphan_invitation():
            async with self.session_factory() as session:
                invitation = await InvitationService(session).create_invitation(
                    school_id=self.school_a, external_email="orphan@example.com", role="STUDENT",
                    scope_type="CLASSROOM", scope_external_id="TURMA_3A",
                    display_name="Orphan Invite", invited_by_external_id="secretary-a",
                )
                await session.commit()
                return invitation.token

        token = asyncio.run(create_orphan_invitation())
        self.identity["value"] = ExternalIdentityContext(
            provider="test", external_user_id="student-orphan", roles=("student",)
        )
        response = self.client.post(
            "/api/v1/reception/diagnostic-access/activate", json={"token": token}
        )
        self.assertEqual(response.status_code, 400, response.text)
        self.assertEqual(
            response.json()["detail"], "O convite não está associado a um atendimento."
        )


if __name__ == "__main__":
    unittest.main()
