# tests/test_essay_calibration_runner.py
"""Testa a materializacao + orquestracao do benchmark de calibracao sem
nenhum custo real de IA - uma correcao falsa substitui
EssayCorrectionService.correct via correction_service_factory, mesmo padrao
de transcritor falso ja usado em tests/test_r4_essay_batch_processing.py.

Harness: unittest.IsolatedAsyncioTestCase com SQLite em memoria, o mesmo
padrao de tests/test_r3_essay_correction_service.py e
tests/test_r4_essay_batch_processing.py (nao existe fixture pytest
seed_session/session_factory neste projeto)."""
import unittest
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AcademicYear, GradeLevel, School, Segment
from agente_ia_edu.services.essay_calibration_runner import (
    _ANCHOR_ACADEMIC_YEAR_ID,
    _ANCHOR_GRADE_LEVEL_ID,
    _ANCHOR_SCHOOL_ID,
    materialize_benchmark_submissions,
    run_benchmark_corrections,
)


class _FakeCorrection:
    def __init__(self, status: str, final_scores=None):
        self.status = status
        self.final_scores = final_scores
        self.ai_output = {"alerts": []}
        self.failure_reason = None


class _FakeCorrectionService:
    """Substitui EssayCorrectionService.correct - nunca chama IA real."""

    def __init__(self, session):
        self.session = session

    async def correct(self, submission_id):
        return _FakeCorrection(
            status="PENDING_REVIEW",
            final_scores={"total": 200, "per_competency": {c: {"points": 40} for c in ("C1", "C2", "C3", "C4", "C5")}},
        )


_FIXTURE = [
    {"student_ref": "aluno_01", "body_text": "Texto da redacao um, com conteudo suficiente para submissao.",
     "expected_scores": {"C1": 160, "C2": 160, "C3": 160, "C4": 160, "C5": 160, "total": 800},
     "expected_special_situation": None, "normative_status": "CONFIRMED", "normative_divergence": None,
     "reference_source": "teste", "reference_version": "2026-10-06"},
    {"student_ref": "aluno_02", "body_text": "Texto da redacao dois, tambem com conteudo suficiente.",
     "expected_scores": {"C1": 0, "C2": 0, "C3": 0, "C4": 0, "C5": 0, "total": 0},
     "expected_special_situation": {"category": "SITUACAO_ESPECIAL_NAO_ESPECIFICADA", "evidence_note": "teste"},
     "normative_status": "UNVERIFIED", "normative_divergence": None,
     "reference_source": "teste", "reference_version": "2026-10-06"},
]


class EssayCalibrationRunnerTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine(
            "sqlite+aiosqlite:///:memory:", echo=False, poolclass=StaticPool
        )
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(
            self.engine, class_=AsyncSession, expire_on_commit=False
        )
        # As constantes _ANCHOR_* assumem escola/ano/serie ja existentes no
        # banco de dev real - aqui, no SQLite em memoria do teste, temos que
        # semear essas ancoras nos mesmos ids antes de materializar.
        async with self.session_factory() as session:
            school = School(id=_ANCHOR_SCHOOL_ID, code="BENCH-ANCHOR", name="Escola ancora do benchmark")
            session.add(school)
            await session.flush()
            segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id="SEG-BENCH")
            session.add(segment)
            await session.flush()
            session.add_all([
                GradeLevel(
                    id=_ANCHOR_GRADE_LEVEL_ID, school_id=school.id, segment_id=segment.id,
                    name="serie-ancora", external_id="GRADE-BENCH",
                ),
                AcademicYear(
                    id=_ANCHOR_ACADEMIC_YEAR_ID, school_id=school.id, year=2026,
                    external_id="YEAR-BENCH",
                ),
            ])
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def test_materialize_creates_one_submission_per_fixture_entry(self):
        async with self.session_factory() as session:
            submissions = await materialize_benchmark_submissions(
                session, fixture_entries=_FIXTURE, run_tag="test-run-001",
            )
            await session.commit()
        assert len(submissions) == 2
        refs = [ref for ref, _ in submissions]
        assert refs == ["aluno_01", "aluno_02"]
        for _, submission_id in submissions:
            assert isinstance(submission_id, uuid.UUID)

    async def test_materialize_is_isolated_per_run_tag(self):
        async with self.session_factory() as session:
            first = await materialize_benchmark_submissions(
                session, fixture_entries=_FIXTURE[:1], run_tag="test-run-A",
            )
            await session.commit()
        async with self.session_factory() as session:
            second = await materialize_benchmark_submissions(
                session, fixture_entries=_FIXTURE[:1], run_tag="test-run-B",
            )
            await session.commit()
        assert first[0][1] != second[0][1]  # submissoes diferentes, nunca reaproveita escola/turma

    async def test_run_benchmark_corrections_calls_injected_factory(self):
        async with self.session_factory() as session:
            submissions = await materialize_benchmark_submissions(
                session, fixture_entries=_FIXTURE, run_tag="test-run-002",
            )
            await session.commit()

        results = await run_benchmark_corrections(
            self.session_factory, submissions=submissions,
            correction_service_factory=_FakeCorrectionService,
        )
        assert len(results) == 2
        for student_ref, correction, _status in results:
            assert correction is not None
            assert correction.status == "PENDING_REVIEW"


if __name__ == "__main__":
    unittest.main()
