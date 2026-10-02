# tests/test_run_mass_correction_scoring_sampling.py
"""Task 9: liga select_sample_for_review (mass_correction_sampling.py) ao
ponto real onde o estagio SCORING da correcao em massa e aplicado.

mass_correction_driver.py (Task 6) so submete/consulta o lote na Batch API -
generico para os 3 estagios, nunca grava final_scores. Quem de fato aplica
apply_scoring_batch_results e grava final_scores em cada EssayCorrection e
_apply_scoring_results, em scripts/run_mass_correction.py (Task 8) - e por
isso e ali que a amostragem se liga, logo apos EssayCorrectionService.
_apply_review_policy decidir o status de cada correcao recem-pontuada.
"""
import unittest
import uuid
from datetime import datetime, timezone
from unittest.mock import patch

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    EssayCorrection,
    EssayPrompt,
    EssaySubmission,
    GradeLevel,
    PromptAssignment,
    School,
    Segment,
    Student,
)
from agente_ia_edu.rubrics.loader import load_rubric_file
from agente_ia_edu.services.essay_rubric_seed import EssayRubricSeeder
from agente_ia_edu.services.institution_settings import InstitutionSettingsService

from scripts.run_mass_correction import _apply_scoring_results
from test_mass_correction_batch_scoring import RUBRIC_FILE, _alert_result_line, _competency_result_line, _output_dict

_HEALTHY_POINTS = {"C1": 160, "C2": 160, "C3": 160, "C4": 160, "C5": 160}


def _healthy_result_lines(correction_id: str) -> list[dict]:
    lines = [
        _competency_result_line(correction_id, code, points=p) for code, p in _HEALTHY_POINTS.items()
    ]
    lines.append(_alert_result_line(correction_id, confirmed_alert_codes=[]))
    return lines


class ApplyScoringResultsSamplingTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
        async with self.engine.begin() as conn:
            await conn.run_sync(Base.metadata.create_all)
        self.session_factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)
        async with self.session_factory() as session:
            await EssayRubricSeeder(session).seed(load_rubric_file("enem_2025"))
            await session.commit()

    async def asyncTearDown(self):
        await self.engine.dispose()

    async def _seed_correction(
        self, session, code: str, *, alerts=None,
        correction_mode: str = "AVALIATIVO", validation_enabled: bool = True,
    ) -> EssayCorrection:
        school = School(id=uuid.uuid4(), code=f"SAMP-{code}", name=f"school-{code}")
        session.add(school)
        await session.flush()
        await InstitutionSettingsService(session).configure(
            school.id, performed_by_external_id="test", correction_mode=correction_mode,
        )
        segment = Segment(id=uuid.uuid4(), school_id=school.id, name="seg", external_id=f"SEG-{code}")
        session.add(segment)
        await session.flush()
        grade = GradeLevel(
            id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
            name="grade", external_id=f"GRADE-{code}",
        )
        year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026, external_id=f"YEAR-{code}")
        session.add_all([grade, year])
        await session.flush()
        klass = Class(
            id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
            grade_level_id=grade.id, name="turma", external_id=f"TURMA-{code}",
        )
        session.add(klass)
        student = Student(
            id=uuid.uuid4(), school_id=school.id, person_id=uuid.uuid4(), student_code=f"ST-{code}"
        )
        session.add(student)
        await session.flush()
        prompt = EssayPrompt(
            id=uuid.uuid4(), school_id=school.id, title="Tema", statement="Disserte sobre X.",
            year=2026, status="ACTIVE", created_by_external_identity="teacher:t",
        )
        session.add(prompt)
        await session.flush()
        assignment = PromptAssignment(
            id=uuid.uuid4(), school_id=school.id, essay_prompt_id=prompt.id,
            class_id=klass.id, assigned_by_external_identity="teacher:t",
            # Garante PENDING_REVIEW apos _apply_review_policy (spec Sec 5),
            # independente do total - assim o teste exercita o caminho real
            # que a amostragem precisa decidir (aprovar ou deixar na fila).
            validation_enabled=validation_enabled,
        )
        session.add(assignment)
        await session.flush()
        submission = EssaySubmission(
            id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=school.id,
            prompt_assignment_id=assignment.id, student_id=student.id,
            mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
            canonical_text="Texto qualquer da redacao para fins de teste.",
            normalized_text_hash="a" * 64, submitted_at=datetime.now(timezone.utc),
        )
        session.add(submission)
        await session.flush()

        correction = EssayCorrection(
            id=uuid.uuid4(), school_id=school.id, essay_submission_id=submission.id,
            status="PENDING_REVIEW", correction_key=f"key-{code}",
            rubric_version=RUBRIC_FILE.rubric_version, model_version="batch",
            prompt_version="essay_correction_v15", engine_version="r3_correction_engine_v2",
            ai_output=_output_dict(alerts=alerts or []), final_scores=None, final_feedback=None,
        )
        session.add(correction)
        await session.commit()
        return correction

    async def test_a_healthy_correction_below_sample_gets_auto_approved(self):
        """Wiring test: quando select_sample_for_review devolve um id na
        lista de auto-aprovacao, _apply_scoring_results chama bulk_approve
        para ele - mockado para nao depender da taxa de 5% real (sorteio
        probabilistico deixaria o teste instavel)."""
        async with self.session_factory() as session:
            correction = await self._seed_correction(session, "1")
            result_lines = _healthy_result_lines(str(correction.id))

            with patch(
                "scripts.run_mass_correction.select_sample_for_review"
            ) as mock_select:
                mock_select.return_value = ([], [correction.id])
                await _apply_scoring_results(session, result_lines)

            mock_select.assert_called_once()
            (call_args,), _ = mock_select.call_args
            self.assertEqual(
                call_args, [{"id": correction.id, "alerts": [], "has_scores": True}]
            )

            await session.refresh(correction)
            self.assertEqual(correction.status, "APPROVED")
            self.assertEqual(correction.reviewed_by_external_identity, "mass-correction-driver")
            self.assertIsNotNone(correction.final_scores)

    async def test_a_correction_select_keeps_in_sample_untouched(self):
        async with self.session_factory() as session:
            correction = await self._seed_correction(session, "2")
            result_lines = _healthy_result_lines(str(correction.id))

            with patch(
                "scripts.run_mass_correction.select_sample_for_review"
            ) as mock_select:
                mock_select.return_value = ([correction.id], [])
                await _apply_scoring_results(session, result_lines)

            await session.refresh(correction)
            # final_scores foi aplicado normalmente (fase 2 rodou), so a
            # aprovacao automatica que nao aconteceu - fica PENDING_REVIEW,
            # pronta pra fila de revisao existente.
            self.assertIsNotNone(correction.final_scores)
            self.assertEqual(correction.status, "PENDING_REVIEW")
            self.assertIsNone(correction.reviewed_by_external_identity)

    async def test_an_alerted_correction_is_never_auto_approved_even_unmocked(self):
        """Sem mock: uma correcao com alerta confirmado como ANULA_REDACAO
        sempre cai na amostra (select_sample_for_review real), entao nunca e
        passada pra bulk_approve - deterministico, nao depende da taxa."""
        async with self.session_factory() as session:
            correction = await self._seed_correction(
                session, "3", alerts=[{"code": "FUGA_AO_TEMA", "detail": "Nao desenvolveu o tema."}]
            )
            result_lines = [
                _competency_result_line(str(correction.id), code, points=p)
                for code, p in _HEALTHY_POINTS.items()
            ]
            result_lines.append(
                _alert_result_line(str(correction.id), confirmed_alert_codes=["FUGA_AO_TEMA"])
            )

            await _apply_scoring_results(session, result_lines)

            await session.refresh(correction)
            self.assertEqual(correction.final_scores["total"], 0)
            self.assertEqual(correction.status, "PENDING_REVIEW")
            self.assertIsNone(correction.reviewed_by_external_identity)

    async def test_a_failed_scoring_stays_needs_review_and_is_never_auto_approved(self):
        """Sem mock: pontuacao invalida (points fora da escala oficial) faz
        apply_scoring_batch_results levantar ValueError - final_scores nunca
        e setado (has_scores=False), entao select_sample_for_review real
        sempre manda pra amostra, nunca pra auto-aprovacao."""
        async with self.session_factory() as session:
            correction = await self._seed_correction(session, "4")
            result_lines = [
                _competency_result_line(str(correction.id), code, points=p)
                for code, p in _HEALTHY_POINTS.items()
            ]
            # C1 com pontuacao fora da escala oficial -> ValueError.
            result_lines[0] = _competency_result_line(str(correction.id), "C1", points=77)
            result_lines.append(_alert_result_line(str(correction.id), confirmed_alert_codes=[]))

            await _apply_scoring_results(session, result_lines)

            await session.refresh(correction)
            self.assertIsNone(correction.final_scores)
            self.assertEqual(correction.status, "NEEDS_REVIEW")
            self.assertIsNotNone(correction.failure_reason)
            self.assertIsNone(correction.reviewed_by_external_identity)

    async def test_a_null_content_scoring_result_stays_needs_review_and_does_not_abort_the_batch(self):
        """Achado C4 do relatorio final: uma linha de resultado com
        content=None (recusa ou resposta vazia do modelo) tem que virar
        NEEDS_REVIEW como qualquer outra falha de pontuacao ja tratada -
        nunca propagar e derrubar o resto da passada."""
        async with self.session_factory() as session:
            correction = await self._seed_correction(session, "8")
            result_lines = [
                _competency_result_line(str(correction.id), code, points=p)
                for code, p in _HEALTHY_POINTS.items()
                if code != "C1"
            ]
            result_lines.append({
                "custom_id": f"{correction.id}:C1",
                "response": {
                    "status_code": 200,
                    "body": {"choices": [{"message": {"content": None}}]},
                },
            })
            result_lines.append(_alert_result_line(str(correction.id), confirmed_alert_codes=[]))

            await _apply_scoring_results(session, result_lines)

            await session.refresh(correction)
            self.assertIsNone(correction.final_scores)
            self.assertEqual(correction.status, "NEEDS_REVIEW")
            self.assertIsNotNone(correction.failure_reason)
            self.assertIsNone(correction.reviewed_by_external_identity)

    async def test_a_correction_missing_a_result_line_stays_needs_review_and_does_not_abort_the_batch(self):
        """C3 do relatorio final: quando falta uma das 6 linhas esperadas
        (ex: a requisicao individual daquele custom_id falhou na API e caiu
        no arquivo de erro, nao no de resultado), apply_scoring_batch_results
        agora levanta ValueError (nao mais KeyError sem tratamento) - essa
        correcao cai em NEEDS_REVIEW como qualquer outra falha de pontuacao
        ja tratada, e o loop continua para a proxima correcao do MESMO lote,
        que deve ser processada e comitada normalmente junto."""
        async with self.session_factory() as session:
            broken = await self._seed_correction(session, "6")
            healthy = await self._seed_correction(session, "7")

            # "broken" perde a linha de C3 de proposito.
            broken_lines = [
                _competency_result_line(str(broken.id), code, points=p)
                for code, p in _HEALTHY_POINTS.items()
                if code != "C3"
            ]
            broken_lines.append(_alert_result_line(str(broken.id), confirmed_alert_codes=[]))

            result_lines = broken_lines + _healthy_result_lines(str(healthy.id))

            with patch(
                "scripts.run_mass_correction.select_sample_for_review"
            ) as mock_select:
                mock_select.return_value = ([], [healthy.id])
                await _apply_scoring_results(session, result_lines)

            await session.refresh(broken)
            await session.refresh(healthy)

            self.assertIsNone(broken.final_scores)
            self.assertEqual(broken.status, "NEEDS_REVIEW")
            self.assertIsNotNone(broken.failure_reason)
            self.assertIsNone(broken.reviewed_by_external_identity)

            # A correcao "saudavel" no mesmo lote continua sendo processada
            # e comitada normalmente, mesmo com a outra falhando ao lado.
            self.assertIsNotNone(healthy.final_scores)
            self.assertEqual(healthy.status, "APPROVED")
            self.assertEqual(healthy.reviewed_by_external_identity, "mass-correction-driver")

    async def test_a_formativo_correction_is_never_sent_to_sampling_or_reapproved(self):
        """FORMATIVO publica sozinho dentro de _apply_review_policy, antes da
        amostragem rodar - a correcao tem que ficar de fora de
        scored_for_sampling (senao select_sample_for_review e chamado com
        uma correcao ja APPROVED, que bulk_approve tentaria reaprovar a toa,
        e o print de resumo contaria errado)."""
        async with self.session_factory() as session:
            correction = await self._seed_correction(session, "5", correction_mode="FORMATIVO")
            result_lines = _healthy_result_lines(str(correction.id))

            with patch(
                "scripts.run_mass_correction.select_sample_for_review"
            ) as mock_select:
                mock_select.return_value = ([], [])
                await _apply_scoring_results(session, result_lines)

            mock_select.assert_called_once_with([])

            await session.refresh(correction)
            self.assertEqual(correction.status, "APPROVED")
            self.assertIsNone(correction.reviewed_by_external_identity)
            self.assertIsNotNone(correction.final_scores)


if __name__ == "__main__":
    unittest.main()
