"""R2 - the identity->enrollment resolver spec §4 says nothing in this
repository computes yet: (school_id, external_user_id) -> User -> Person ->
Student -> the student's real, ACTIVE StudentEnrollment.

This is the piece that turns "a request claims to be a student" into "this
student's own class_id", which essay-submission authorization (Task 14)
depends on completely - without it there is no way to check that a
submission's prompt_assignment_id belongs to the caller's own class.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import Student, StudentEnrollment, User


async def resolve_active_enrollment(
    session: AsyncSession, *, school_id: uuid.UUID, external_user_id: str
) -> StudentEnrollment | None:
    """Return the caller's own, real, ACTIVE enrollment in ``school_id``, or
    None if any link in the chain is missing. Never raises for a missing
    link - a None here is a normal "not enrolled" outcome the caller (a
    route) turns into 403, not a bug."""
    user = await session.scalar(
        select(User).where(User.school_id == school_id, User.external_user_id == external_user_id)
    )
    if user is None:
        return None

    student = await session.scalar(
        select(Student).where(Student.school_id == school_id, Student.person_id == user.person_id)
    )
    if student is None:
        return None

    result = await session.execute(
        select(StudentEnrollment)
        .where(
            StudentEnrollment.school_id == school_id,
            StudentEnrollment.student_id == student.id,
            StudentEnrollment.status == "ACTIVE",
        )
        .order_by(StudentEnrollment.created_at.desc())
    )
    return result.scalars().first()


__all__ = ["resolve_active_enrollment"]
