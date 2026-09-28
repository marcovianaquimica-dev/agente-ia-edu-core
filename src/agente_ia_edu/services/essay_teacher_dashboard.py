"""R2/R3 - Teacher-facing essay dashboard: submission rate, average scores
(overall and per competency), the roster of who submitted vs who didn't, and
a deterministic (never LLM) class-level action plan - same shape and
philosophy as services/teacher_portal.py's TeacherPerformancePolicy
(thresholds -> classify -> generate item), applied to the essay domain
instead of content mastery.

Scoped to ONE EssayPrompt at a time (a prompt can now have several
PromptAssignment rows across different classes, via
EssayProposalService.create_assignments_bulk) - grade_level_id/class_id/
student_id filters narrow WHICH of that prompt's assignments/students count,
they never pull in a different prompt's data.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Optional

from pydantic import BaseModel, Field
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    Class,
    EssayCorrection,
    EssayPrompt,
    EssaySubmission,
    Person,
    PromptAssignment,
    Student,
    StudentEnrollment,
)
from ..essay_engine_contract.v1 import COMPETENCY_CODES

_COMPETENCY_LABELS: dict[str, str] = {
    "C1": "Domínio da norma padrão", "C2": "Compreensão do tema", "C3": "Argumentação",
    "C4": "Coesão textual", "C5": "Proposta de intervenção",
}


@dataclass
class EssayDashboardPolicy:
    """Deterministic thresholds for the essay dashboard's action plan - same
    role as teacher_portal.py's TeacherPerformancePolicy, scoped to essays."""

    low_submission_threshold: float = 50.0   # < 50% entregue = prioridade alta
    medium_submission_threshold: float = 80.0  # 50-79.9% = media, >= 80% = ok
    # 100/200 - metade da pontuacao maxima de uma competencia - mesmo
    # espirito do 50%/70% de mastery da TeacherPerformancePolicy, adaptado a
    # escala 0-200 de cada competencia do ENEM.
    low_competency_threshold: float = 100.0
    medium_competency_threshold: float = 140.0

    def build_action_plan(
        self, *, submitted_count: int, total_students: int,
        average_total: float | None, average_per_competency: dict[str, float] | None,
    ) -> list[dict[str, Any]]:
        if total_students == 0:
            # No enrolled student matches the applied filters at all - there
            # is nothing to "cobrar entrega" of, and no scores to react to.
            return []
        items: list[dict[str, Any]] = []
        submission_rate = (submitted_count / total_students) * 100

        if submission_rate < self.low_submission_threshold:
            items.append({
                "priority": "HIGH",
                "recommended_action": (
                    "Cobrar a entrega: mais da metade da turma ainda não enviou "
                    "esta redação."
                ),
                "evidence": (
                    f"{submitted_count} de {total_students} aluno(s) entregaram "
                    f"({submission_rate:.0f}%)."
                ),
            })
        elif submission_rate < self.medium_submission_threshold:
            items.append({
                "priority": "MEDIUM",
                "recommended_action": (
                    "Lembrar os alunos que ainda faltam entregar - a maioria já "
                    "enviou, mas a turma ainda não está completa."
                ),
                "evidence": (
                    f"{submitted_count} de {total_students} aluno(s) entregaram "
                    f"({submission_rate:.0f}%)."
                ),
            })

        if average_per_competency:
            for code in COMPETENCY_CODES:
                avg = average_per_competency.get(code)
                if avg is None:
                    continue
                label = _COMPETENCY_LABELS.get(code, code)
                if avg < self.low_competency_threshold:
                    items.append({
                        "priority": "HIGH",
                        "recommended_action": (
                            f"Reforçar {code} — {label}: revisão dirigida para "
                            "esse eixo antes da próxima proposta."
                        ),
                        "evidence": f"Média da turma em {code}: {avg:.0f}/200.",
                    })
                elif avg < self.medium_competency_threshold:
                    items.append({
                        "priority": "MEDIUM",
                        "recommended_action": (
                            f"Acompanhar {code} — {label}: média abaixo do "
                            "esperado, vale um exercício de fixação."
                        ),
                        "evidence": f"Média da turma em {code}: {avg:.0f}/200.",
                    })

        if not items and submitted_count > 0:
            evidence = (
                f"{submitted_count} de {total_students} aluno(s) entregaram "
                f"({submission_rate:.0f}%)."
            )
            if average_total is not None:
                evidence += f" Nota média: {average_total:.0f}/1000."
            items.append({
                "priority": "LOW",
                "recommended_action": (
                    "Turma consolidada nesta proposta - considere um tema mais "
                    "desafiador na próxima redação."
                ),
                "evidence": evidence,
            })

        priority_order = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
        items.sort(key=lambda item: priority_order[item["priority"]])
        return items


class StudentSubmissionRow(BaseModel):
    student_id: uuid.UUID
    student_name: str
    class_id: uuid.UUID
    submitted: bool
    essay_submission_id: Optional[uuid.UUID] = None
    correction_status: Optional[str] = None
    total_score: Optional[int] = None
    per_competency: Optional[dict[str, int]] = None


class EssayDashboardResponse(BaseModel):
    essay_prompt_id: uuid.UUID
    essay_prompt_title: str
    total_students: int
    submitted_count: int
    submitted_percentage: float
    average_total_score: Optional[float] = None
    average_per_competency: Optional[dict[str, float]] = None
    students: list[StudentSubmissionRow] = Field(default_factory=list)
    action_plan: list[dict[str, Any]] = Field(default_factory=list)


async def build_essay_prompt_dashboard(
    session: AsyncSession,
    *,
    school_id: uuid.UUID,
    essay_prompt_id: uuid.UUID,
    grade_level_id: uuid.UUID | None = None,
    class_id: uuid.UUID | None = None,
    student_id: uuid.UUID | None = None,
    policy: EssayDashboardPolicy | None = None,
) -> EssayDashboardResponse:
    policy = policy or EssayDashboardPolicy()

    prompt = await session.get(EssayPrompt, essay_prompt_id)
    if prompt is None or prompt.school_id != school_id:
        raise ValueError(f"EssayPrompt not found in school {school_id}: {essay_prompt_id}")

    assignment_rows = (
        await session.execute(
            select(PromptAssignment, Class.grade_level_id)
            .join(Class, Class.id == PromptAssignment.class_id)
            .where(PromptAssignment.essay_prompt_id == essay_prompt_id)
        )
    ).all()
    assignments = [
        a for a, grade_level in assignment_rows
        if class_id is None or a.class_id == class_id
        if grade_level_id is None or grade_level == grade_level_id
    ]
    if not assignments:
        return EssayDashboardResponse(
            essay_prompt_id=prompt.id, essay_prompt_title=prompt.title,
            total_students=0, submitted_count=0, submitted_percentage=0.0,
        )

    assignment_by_class = {a.class_id: a for a in assignments}
    class_ids = list(assignment_by_class.keys())

    enrollment_query = select(StudentEnrollment, Person.full_name).join(
        Student, Student.id == StudentEnrollment.student_id
    ).join(Person, Person.id == Student.person_id).where(
        StudentEnrollment.class_id.in_(class_ids),
        StudentEnrollment.status == "ACTIVE",
    )
    if student_id is not None:
        enrollment_query = enrollment_query.where(StudentEnrollment.student_id == student_id)
    enrollment_rows = (await session.execute(enrollment_query)).all()

    if not enrollment_rows:
        return EssayDashboardResponse(
            essay_prompt_id=prompt.id, essay_prompt_title=prompt.title,
            total_students=0, submitted_count=0, submitted_percentage=0.0,
        )

    student_ids = [enrollment.student_id for enrollment, _name in enrollment_rows]
    assignment_ids = [a.id for a in assignments]

    submission_rows = (
        await session.execute(
            select(EssaySubmission)
            .where(
                EssaySubmission.prompt_assignment_id.in_(assignment_ids),
                EssaySubmission.student_id.in_(student_ids),
                EssaySubmission.status != "SUPERSEDED",
            )
            .order_by(EssaySubmission.created_at.desc())
        )
    ).scalars().all()
    # Most recent non-superseded submission per (student, assignment) - same
    # "latest wins" rule essay_submissions.py's own my_submission resolution
    # already uses.
    latest_submission: dict[tuple[uuid.UUID, uuid.UUID], EssaySubmission] = {}
    for submission in submission_rows:
        key = (submission.student_id, submission.prompt_assignment_id)
        if key not in latest_submission:
            latest_submission[key] = submission

    submission_ids = [s.id for s in latest_submission.values()]
    correction_by_submission: dict[uuid.UUID, EssayCorrection] = {}
    if submission_ids:
        correction_rows = (
            await session.execute(
                select(EssayCorrection).where(
                    EssayCorrection.essay_submission_id.in_(submission_ids)
                )
            )
        ).scalars().all()
        correction_by_submission = {c.essay_submission_id: c for c in correction_rows}

    students: list[StudentSubmissionRow] = []
    total_scores: list[int] = []
    per_competency_scores: dict[str, list[int]] = {code: [] for code in COMPETENCY_CODES}
    submitted_count = 0

    for enrollment, full_name in enrollment_rows:
        assignment = assignment_by_class[enrollment.class_id]
        submission = latest_submission.get((enrollment.student_id, assignment.id))
        submitted = submission is not None and submission.status == "SUBMITTED"
        if submitted:
            submitted_count += 1

        correction = correction_by_submission.get(submission.id) if submission else None
        correction_status = correction.status if correction is not None else None
        total_score = None
        per_competency: dict[str, int] | None = None
        if correction is not None and correction.status == "APPROVED" and correction.final_scores:
            total_score = correction.final_scores.get("total")
            per_competency_raw = correction.final_scores.get("per_competency") or {}
            per_competency = {
                code: (per_competency_raw.get(code) or {}).get("points", 0)
                for code in COMPETENCY_CODES
            }
            if total_score is not None:
                total_scores.append(total_score)
            for code in COMPETENCY_CODES:
                if per_competency.get(code) is not None:
                    per_competency_scores[code].append(per_competency[code])

        students.append(StudentSubmissionRow(
            student_id=enrollment.student_id, student_name=full_name,
            class_id=enrollment.class_id, submitted=submitted,
            essay_submission_id=submission.id if submission else None,
            correction_status=correction_status,
            total_score=total_score, per_competency=per_competency,
        ))

    total_students = len(enrollment_rows)
    submitted_percentage = (submitted_count / total_students * 100) if total_students else 0.0
    average_total = (sum(total_scores) / len(total_scores)) if total_scores else None
    average_per_competency = (
        {
            code: (sum(scores) / len(scores)) for code, scores in per_competency_scores.items()
            if scores
        }
        or None
    )

    action_plan = policy.build_action_plan(
        submitted_count=submitted_count, total_students=total_students,
        average_total=average_total, average_per_competency=average_per_competency,
    )

    return EssayDashboardResponse(
        essay_prompt_id=prompt.id, essay_prompt_title=prompt.title,
        total_students=total_students, submitted_count=submitted_count,
        submitted_percentage=round(submitted_percentage, 1),
        average_total_score=round(average_total, 1) if average_total is not None else None,
        average_per_competency=(
            {code: round(v, 1) for code, v in average_per_competency.items()}
            if average_per_competency else None
        ),
        students=students, action_plan=action_plan,
    )


__all__ = [
    "EssayDashboardPolicy", "EssayDashboardResponse", "StudentSubmissionRow",
    "build_essay_prompt_dashboard",
]
