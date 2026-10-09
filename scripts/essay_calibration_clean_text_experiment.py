"""Fase D: roda o MESMO motor (mesmo provider/modelo/parametros/rubrica/
prompt/engine_version) contra o texto atual (A) e a transcricao limpa (B)
do mesmo subconjunto, para isolar quanto do vies vem de contaminacao de
OCR vs. regra de pontuacao (spec Fase D).

Os dados reais (texto original e texto limpo das 20 redacoes) NAO vivem
neste repositorio - sao lidos de ESSAY_CALIBRATION_PRIVATE_DIR (padrao:
~/agente-ia-edu-core-private-data/essay_calibration), ver o README daquele
diretorio (migrado para la em 2026-10-08)."""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _ROOT / ".env"
_DEFAULT_HOST = "localhost"
_DEFAULT_PORT = "5433"  # docker-compose.yml maps "5433:5432"


def _load_env_file(path: Path) -> dict[str, str]:
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for line in path.read_text().splitlines():
        stripped = line.strip()
        if not stripped or stripped.startswith("#") or "=" not in stripped:
            continue
        key, _, value = stripped.partition("=")
        values[key.strip()] = value.strip().strip('"').strip("'")
    return values


_env_values = _load_env_file(_ENV_FILE)
for _key in ("OPENAI_API_KEY", "OPENAI_MODEL", "OPENAI_VISION_MODEL"):
    if _key in _env_values:
        os.environ.setdefault(_key, _env_values[_key])

if "DATABASE_URL" not in os.environ:
    _user = _env_values.get("POSTGRES_USER")
    _password = _env_values.get("POSTGRES_PASSWORD")
    _database = _env_values.get("POSTGRES_DB")
    if _user and _password and _database:
        os.environ["DATABASE_URL"] = (
            f"postgresql+psycopg://{_user}:{_password}@{_DEFAULT_HOST}:{_DEFAULT_PORT}/{_database}"
        )

sys.path.insert(0, str(_ROOT / "src"))

from agente_ia_edu.db.session import create_engine, create_session_factory
from agente_ia_edu.services.essay_calibration_metrics import BenchmarkResultRow, compute_benchmark_report
from agente_ia_edu.services.essay_calibration_runner import (
    materialize_benchmark_submissions, run_benchmark_corrections,
)

_PRIVATE_DIR = Path(os.environ.get(
    "ESSAY_CALIBRATION_PRIVATE_DIR",
    str(Path.home() / "agente-ia-edu-core-private-data" / "essay_calibration"),
))
_SUBSET_PATH = _PRIVATE_DIR / "essay_calibration_clean_text_subset_v1.real.json"
_BENCHMARK_PATH = _PRIVATE_DIR / "essay_calibration_benchmark_v1.real.json"


async def _run_condition(session_factory, *, entries: list[dict], run_tag: str):
    async with session_factory() as session:
        submissions = await materialize_benchmark_submissions(session, fixture_entries=entries, run_tag=run_tag)
        await session.commit()
    print(f"[{run_tag}] {len(submissions)} submissoes materializadas.", flush=True)
    return await run_benchmark_corrections(session_factory, submissions=submissions)


async def main() -> None:
    if not _SUBSET_PATH.is_file() or not _BENCHMARK_PATH.is_file():
        raise SystemExit(
            f"Dados privados de calibracao nao encontrados em {_PRIVATE_DIR}. "
            "Este script precisa dos textos reais original/limpo das 20 redacoes, "
            "que vivem fora deste repositorio - defina ESSAY_CALIBRATION_PRIVATE_DIR "
            "ou veja o README do armazenamento privado."
        )
    subset = json.loads(_SUBSET_PATH.read_text(encoding="utf-8"))
    benchmark = {e["student_ref"]: e for e in json.loads(_BENCHMARK_PATH.read_text(encoding="utf-8"))}

    entries_a = [{"student_ref": s["student_ref"], "body_text": s["original_body_text"]} for s in subset]
    entries_b = [{"student_ref": s["student_ref"], "body_text": s["clean_body_text"]} for s in subset]

    engine = create_engine()
    session_factory = create_session_factory(engine)

    results_a = await _run_condition(session_factory, entries=entries_a, run_tag="CLEAN_EXPERIMENT_A_V2")
    results_b = await _run_condition(session_factory, entries=entries_b, run_tag="CLEAN_EXPERIMENT_B_V2")

    def _rows(results):
        rows = []
        for student_ref, correction, status in results:
            entry = benchmark[student_ref]
            engine_scores = None
            if correction is not None and correction.final_scores is not None:
                per_competency = correction.final_scores.get("per_competency") or {}
                engine_scores = {c: (per_competency.get(c) or {}).get("points") for c in ("C1", "C2", "C3", "C4", "C5")}
                engine_scores["total"] = correction.final_scores.get("total")
            rows.append(BenchmarkResultRow(
                student_ref=student_ref, normative_status="CONFIRMED",
                expected_scores=entry["expected_scores"], engine_scores=engine_scores,
                expected_is_special_situation=False, engine_zeroed_whole_essay=False,
                engine_status=status, ocr_duvidoso=False,
            ))
        return rows

    rows_a = _rows(results_a)
    rows_b = _rows(results_b)
    report_a = compute_benchmark_report(rows_a)
    report_b = compute_benchmark_report(rows_b)

    out = {
        "subset_size": len(subset),
        "condition_a_current_text": {
            "mae_total": report_a.mae_total, "mae_per_competency": report_a.mae_per_competency,
            "bias_total": report_a.bias_total, "bias_per_competency": report_a.bias_per_competency,
            "levels_distance_distribution": report_a.levels_distance_distribution,
            "needs_review_count": report_a.needs_review_count,
        },
        "condition_b_clean_text": {
            "mae_total": report_b.mae_total, "mae_per_competency": report_b.mae_per_competency,
            "bias_total": report_b.bias_total, "bias_per_competency": report_b.bias_per_competency,
            "levels_distance_distribution": report_b.levels_distance_distribution,
            "needs_review_count": report_b.needs_review_count,
        },
        "rows_a": [
            {"student_ref": r.student_ref, "engine_scores": r.engine_scores, "engine_status": r.engine_status}
            for r in rows_a
        ],
        "rows_b": [
            {"student_ref": r.student_ref, "engine_scores": r.engine_scores, "engine_status": r.engine_status}
            for r in rows_b
        ],
    }
    out_path = Path("tests/fixtures/essay_calibration_baselines/CLEAN_TEXT_EXPERIMENT_V1.json")
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
