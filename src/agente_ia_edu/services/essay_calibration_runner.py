"""Materializa e corrige o benchmark de calibracao (tests/fixtures/
essay_calibration_benchmark_v1.json) contra um motor real, isolado numa
escola/turma/proposta descartaveis por execucao - nunca reaproveita dados
de outra sessao/teste manual no mesmo banco de desenvolvimento."""
from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime, timezone
from types import SimpleNamespace

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..db.models.academic import Class, Person, Student, StudentEnrollment
from ..db.models.essay_proposal import EssayPrompt, EssaySubmission, PromptAssignment
from .essay_correction import EssayCorrectionService
from .essay_correction_key import essay_text_hash, normalize_essay_text

# Mesma escola/ano/serie usados nos testes manuais desta investigacao -
# reaproveitados so como ANCORA de escola existente (precisamos de um
# school_id/academic_year_id/grade_level_id validos); a turma/proposta em si
# sao sempre novas e isoladas por run_tag.
_ANCHOR_SCHOOL_ID = uuid.UUID("a8c1e3d0-5f6b-4a7e-8c9d-1234567890ab")
_ANCHOR_ACADEMIC_YEAR_ID = uuid.UUID("dac2049d-0687-4ff1-b2f5-57380843263f")
_ANCHOR_GRADE_LEVEL_ID = uuid.UUID("2b989afc-5b33-4a5f-b409-0ed0fa21add4")
_TEACHER_IDENTITY = "benchmark_runner"

_BENCHMARK_STATEMENT = (
    "A partir da leitura dos textos motivadores e com base nos conhecimentos "
    "construidos ao longo de sua formacao, redija um texto dissertativo-"
    "argumentativo em modalidade escrita formal da lingua portuguesa sobre o "
    "tema 'Desafios para a valorizacao da pessoa idosa e o enfrentamento do "
    "preconceito etario no Brasil', apresentando proposta de intervencao que "
    "respeite os direitos humanos."
)


def _slug(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return "_".join(normalized.lower().split())


async def materialize_benchmark_submissions(
    session: AsyncSession, *, fixture_entries: list[dict], run_tag: str,
) -> list[tuple[str, uuid.UUID]]:
    now = datetime.now(timezone.utc)
    klass = Class(
        id=uuid.uuid4(), school_id=_ANCHOR_SCHOOL_ID, academic_year_id=_ANCHOR_ACADEMIC_YEAR_ID,
        grade_level_id=_ANCHOR_GRADE_LEVEL_ID, name=f"Benchmark calibracao - {run_tag}",
    )
    session.add(klass)
    await session.flush()

    prompt = EssayPrompt(
        id=uuid.uuid4(), school_id=_ANCHOR_SCHOOL_ID,
        title=f"Benchmark calibracao - {run_tag}", statement=_BENCHMARK_STATEMENT,
        year=now.year, status="ACTIVE", is_free_theme=False,
        created_by_external_identity=_TEACHER_IDENTITY,
    )
    session.add(prompt)
    await session.flush()

    assignment = PromptAssignment(
        id=uuid.uuid4(), school_id=_ANCHOR_SCHOOL_ID, essay_prompt_id=prompt.id,
        class_id=klass.id, assigned_by_external_identity=_TEACHER_IDENTITY,
        validation_enabled=True, status="OPEN",
    )
    session.add(assignment)
    await session.flush()

    results: list[tuple[str, uuid.UUID]] = []
    for entry in fixture_entries:
        student_ref = entry["student_ref"]
        slug = f"{_slug(student_ref)}_{run_tag}"

        person = Person(id=uuid.uuid4(), school_id=_ANCHOR_SCHOOL_ID, external_id=slug, full_name=student_ref)
        session.add(person)
        await session.flush()

        student = Student(id=uuid.uuid4(), school_id=_ANCHOR_SCHOOL_ID, person_id=person.id, external_id=slug)
        session.add(student)
        await session.flush()

        session.add(StudentEnrollment(
            id=uuid.uuid4(), school_id=_ANCHOR_SCHOOL_ID, student_id=student.id,
            class_id=klass.id, enrolled_on=now.date(),
        ))

        canonical = normalize_essay_text(entry["body_text"])
        submission = EssaySubmission(
            id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=_ANCHOR_SCHOOL_ID,
            prompt_assignment_id=assignment.id, student_id=student.id,
            mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
            canonical_text=canonical, normalized_text_hash=essay_text_hash(canonical),
            submitted_at=now,
        )
        session.add(submission)
        await session.flush()

        results.append((student_ref, submission.id))

    return results


async def run_benchmark_corrections(
    session_factory: async_sessionmaker, *, submissions: list[tuple[str, uuid.UUID]],
    correction_service_factory=EssayCorrectionService,
):
    results = []
    for student_ref, submission_id in submissions:
        async with session_factory() as session:
            service = correction_service_factory(session)
            try:
                correction = await service.correct(submission_id)
                # Capturar status/final_scores/ai_output ANTES do commit -
                # SQLAlchemy expira atributos no commit, e o `async with`
                # fecha a sessao ao sair deste bloco; um lazy-load depois
                # disso levanta MissingGreenlet (status, confirmado nesta
                # investigacao rodando o script de correcao manual) ou
                # DetachedInstanceError (final_scores/ai_output, confirmado
                # na primeira execucao real do benchmark completo - a
                # instancia ja estava fora da sessao quando o script
                # tentava monta o relatorio). Por isso devolvemos um
                # snapshot leve em vez do objeto ORM.
                status = correction.status
                final_scores = correction.final_scores
                ai_output = correction.ai_output
                await session.commit()
                snapshot = SimpleNamespace(final_scores=final_scores, ai_output=ai_output, status=status)
                results.append((student_ref, snapshot, status))
            except Exception as exc:  # noqa: BLE001 - registra e segue o lote
                results.append((student_ref, None, f"ERROR: {exc}"))
    return results
