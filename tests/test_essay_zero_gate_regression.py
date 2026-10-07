# tests/test_essay_zero_gate_regression.py
"""Regressao obrigatoria (spec Fase C): os 4 casos reais que motivaram todo
este plano nunca podem voltar ao comportamento antigo (silenciosamente cair
numa nota pedagogica normal como se o problema do texto nao existisse).

Sabrina, Henrique, Larissa e Joao Miguel tem, nos 4, um body_text real
extensamente corrompido por OCR malfeito na origem (confirmado lendo o
fixture inteiro, nao so o trecho usado para localiza-los) - nao apenas
Larissa/Joao Miguel como se pensava quando este plano foi desenhado.
Por isso os 4 sao protegidos pelo MESMO mecanismo: o Quality Gate (Fase B)
intercepta ANTES do Zero Gate (Fase C) rodar, com
quality_gate_status=UNRELIABLE_NEEDS_REVIEW e final_scores=None. Isso e o
comportamento CORRETO por design - o proprio ponto da Fase B e "duvida sobre
a confiabilidade do texto tem prioridade sobre qualquer julgamento
pedagogico", e o julgamento de tema do Zero Gate e exatamente um desses
julgamentos pedagogicos que nao deveria ser confiado sobre um texto este
corrompido. O problema original de Sabrina/Henrique (cair numa nota normal
sem ningue perceber o problema) esta mesmo resolvido - so que pelo Quality
Gate interceptando primeiro, nao pelo Zero Gate confirmando FUGA_AO_TEMA
como se esperava quando este plano foi escrito (ver diagnostico ao vivo
2026-10-06, e o relatorio da Task 12 que corrigiu essa expectativa).

Chamada REAL de IA - roda fora do pytest padrao (marcado 'live', e excluido
por padrao via addopts em pyproject.toml), ja que depende de rede e custa
dinheiro. Harness e fixture_entries/run_tag seguem o mesmo padrao de
tests/test_essay_quality_gate_regression.py (Task 9): ver aquele arquivo
para a justificativa completa do IsolatedAsyncioTestCase contra o Postgres
real dedicado deste worktree, e para a justificativa de carregar o texto
real a partir de ESSAY_CALIBRATION_PRIVATE_DIR (fora deste repositorio)
em vez da fixture publica.

Os 4 casos usam retry unico (submissao NOVA, run_tag novo - correct() e
idempotente por submission_id, entao a mesma submissao nunca pode ser
re-corrigida) antes de concluir que algo esta de fato quebrado: tanto o
Zero Gate (voto 3x) quanto o proprio Quality Gate (classificacao de
confiabilidade numa unica chamada de phase 1) podem ter ruido real de
amostragem - a Task 12 observou Joao Miguel oscilar entre as duas rodadas
reais que rodou. Um retry por essay e esperado; re-tentar repetidamente até
sair verde nao e."""
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
_SABRINA = _FIXTURE_BY_REF["aluno_23"] if _FIXTURE_BY_REF is not None else None
_HENRIQUE = _FIXTURE_BY_REF["aluno_28"] if _FIXTURE_BY_REF is not None else None
_LARISSA = _FIXTURE_BY_REF["aluno_09"] if _FIXTURE_BY_REF is not None else None
_JOAO_MIGUEL = _FIXTURE_BY_REF["aluno_10"] if _FIXTURE_BY_REF is not None else None
_RUN_TAG_PREFIX = "task12-regression"


@pytest.mark.live
@pytest.mark.skipif(
    _FIXTURE is None,
    reason=f"dados privados de calibracao nao encontrados em {_PRIVATE_FIXTURE_PATH} - "
           "defina ESSAY_CALIBRATION_PRIVATE_DIR ou veja o README do armazenamento privado",
)
class EssayZeroGateRegressionTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self) -> None:
        self.engine = create_engine()
        self.session_factory = create_session_factory(self.engine)

    async def asyncTearDown(self) -> None:
        await self.engine.dispose()

    async def _correct(self, entry: dict):
        # run_tag unico por tentativa: materialize_benchmark_submissions deriva
        # external_id (unique por escola) do run_tag + student_ref, e este
        # banco e persistente entre execucoes - reusar um run_tag fixo
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

    @staticmethod
    def _is_quality_gate_protected(correction) -> bool:
        return (
            correction.status == "NEEDS_REVIEW"
            and correction.final_scores is None
            and correction.quality_gate_status == "UNRELIABLE_NEEDS_REVIEW"
        )

    async def _correct_with_retry(self, entry: dict):
        """Tenta uma vez; se o resultado nao bater com o padrao esperado
        (Quality Gate protegendo), tenta de novo com uma submissao NOVA antes
        de desistir - ve o docstring do modulo para por que isso e esperado
        (ruido de amostragem genuino, tanto no Zero Gate quanto no proprio
        Quality Gate) e por que um unico retry e o limite."""
        correction = await self._correct(entry)
        if self._is_quality_gate_protected(correction):
            return correction, 1
        correction = await self._correct(entry)
        return correction, 2

    def _assert_quality_gate_protected(self, correction, *, attempts: int) -> None:
        self.assertEqual(
            correction.status, "NEEDS_REVIEW",
            f"apos {attempts} tentativa(s): status={correction.status!r} "
            f"failure_reason={correction.failure_reason!r}",
        )
        # NUNCA publica nota como se a entrada fosse confiavel, e o
        # short-circuit do Quality Gate continua disparando ANTES do Zero
        # Gate mesmo agora que o Zero Gate roda incondicionalmente.
        self.assertIsNone(correction.final_scores)
        self.assertEqual(correction.quality_gate_status, "UNRELIABLE_NEEDS_REVIEW")

    async def test_sabrina_e_protegida_pelo_quality_gate_antes_do_zero_gate(self) -> None:
        # Texto real extensamente corrompido por OCR (nao apenas fora do
        # tema) - o Quality Gate intercepta antes do Zero Gate sequer
        # avaliar FUGA_AO_TEMA. Ver docstring do modulo.
        correction, attempts = await self._correct_with_retry(_SABRINA)
        self._assert_quality_gate_protected(correction, attempts=attempts)

    async def test_henrique_e_protegido_pelo_quality_gate_antes_do_zero_gate(self) -> None:
        correction, attempts = await self._correct_with_retry(_HENRIQUE)
        self._assert_quality_gate_protected(correction, attempts=attempts)

    async def test_larissa_garbled_input_continua_fora_do_zero_gate(self) -> None:
        correction, attempts = await self._correct_with_retry(_LARISSA)
        self._assert_quality_gate_protected(correction, attempts=attempts)

    async def test_joao_miguel_garbled_input_continua_fora_do_zero_gate(self) -> None:
        correction, attempts = await self._correct_with_retry(_JOAO_MIGUEL)
        self._assert_quality_gate_protected(correction, attempts=attempts)


if __name__ == "__main__":
    unittest.main()
