# tests/test_essay_quality_gate_regression.py
"""Regressao obrigatoria (spec Fase B): Larissa e Joao Miguel, cujo texto
real e corrompido por OCR malfeito na origem, NUNCA podem voltar a ser
zerados por TEXTO_INSUFICIENTE. Chamada REAL de IA - roda fora do pytest
padrao (marcado 'live'), ja que depende de rede e custa dinheiro.

O texto real dessas redacoes NAO vive neste repositorio (ver
ESSAY_CALIBRATION_PRIVATE_DIR abaixo - 2026-10-08, depois que uma tentativa
de push revelou que pseudonimizar nomes nao bastava, os textos integrais
tambem precisavam sair do historico publicavel). Sem o arquivo privado
presente, esta classe e pulada automaticamente - nunca falha a suite padrao
nem quebra a colecao de testes para quem clonar o repositorio sem os dados
privados.

Harness: unittest.IsolatedAsyncioTestCase contra o Postgres REAL dedicado
deste worktree (DATABASE_URL resolvido por tests/conftest.py a partir do
.env - ver docstring daquele arquivo), e nao o SQLite em memoria que o
resto da suite usa, porque materialize_benchmark_submissions() precisa das
linhas-ancora (escola/ano/serie) ja semeadas nesse banco dedicado. Nenhuma
fixture seed_session/make_essay_submission existe neste projeto (ver
tests/test_essay_quality_gate_integration.py, Task 8, que usa o mesmo
padrao IsolatedAsyncioTestCase so que com SQLite + provider fake)."""
import json
import os
import unittest
import uuid
from pathlib import Path

import pytest

from agente_ia_edu.db.session import create_engine, create_session_factory
from agente_ia_edu.services.essay_calibration_runner import materialize_benchmark_submissions
from agente_ia_edu.services.essay_correction import EssayCorrectionService

_PRIVATE_DIR = Path(os.environ.get(
    "ESSAY_CALIBRATION_PRIVATE_DIR",
    str(Path.home() / "agente-ia-edu-core-private-data" / "essay_calibration"),
))
_PRIVATE_FIXTURE_PATH = _PRIVATE_DIR / "essay_calibration_benchmark_v1.real.json"


def _load_private_fixture() -> list[dict] | None:
    if not _PRIVATE_FIXTURE_PATH.is_file():
        return None
    return json.loads(_PRIVATE_FIXTURE_PATH.read_text(encoding="utf-8"))


_FIXTURE = _load_private_fixture()
_FIXTURE_BY_REF = {e["student_ref"]: e for e in _FIXTURE} if _FIXTURE is not None else None
_LARISSA = _FIXTURE_BY_REF["aluno_09"] if _FIXTURE_BY_REF is not None else None
_JOAO_MIGUEL = _FIXTURE_BY_REF["aluno_10"] if _FIXTURE_BY_REF is not None else None
_RUN_TAG_PREFIX = "task9-regression"


@pytest.mark.live
@pytest.mark.skipif(
    _FIXTURE is None,
    reason=f"dados privados de calibracao nao encontrados em {_PRIVATE_FIXTURE_PATH} - "
           "defina ESSAY_CALIBRATION_PRIVATE_DIR ou veja o README do armazenamento privado",
)
class EssayQualityGateGarbledInputRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_engine()
        self.session_factory = create_session_factory(self.engine)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _correct(self, entry: dict):
        # run_tag unico por execucao do teste: materialize_benchmark_submissions
        # deriva external_id (unique por escola) do run_tag + student_ref, e
        # este banco e persistente entre execucoes - reusar um run_tag fixo
        # colidiria com a UniqueConstraint na segunda vez que o teste rodasse.
        run_tag = f"{_RUN_TAG_PREFIX}-{uuid.uuid4().hex[:8]}"
        async with self.session_factory() as session:
            submissions = await materialize_benchmark_submissions(
                session, fixture_entries=[entry], run_tag=run_tag,
            )
            await session.commit()
            _, submission_id = submissions[0]

            service = EssayCorrectionService(session)  # providers REAIS, sem stub
            return await service.correct(submission_id)

    def _assert_quality_gate_protected(self, correction) -> None:
        self.assertEqual(correction.status, "NEEDS_REVIEW")
        # NUNCA publica nota como se a entrada fosse confiavel.
        self.assertIsNone(correction.final_scores)
        self.assertEqual(correction.quality_gate_status, "UNRELIABLE_NEEDS_REVIEW")
        alert_codes = {a["code"] for a in (correction.ai_output.get("alerts") or [])}
        self.assertNotIn("TEXTO_INSUFICIENTE", alert_codes)

    async def test_larissa_garbled_input_never_zeroed_via_texto_insuficiente(self) -> None:
        correction = await self._correct(_LARISSA)
        self._assert_quality_gate_protected(correction)

    async def test_joao_miguel_garbled_input_never_zeroed_via_texto_insuficiente(self) -> None:
        correction = await self._correct(_JOAO_MIGUEL)
        self._assert_quality_gate_protected(correction)


if __name__ == "__main__":
    unittest.main()
