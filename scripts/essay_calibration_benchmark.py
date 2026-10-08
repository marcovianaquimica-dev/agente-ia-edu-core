"""Roda o benchmark de calibracao completo (30 chamadas REAIS de IA,
sequenciais - caro e lento, so nos pontos de controle de cada fase).

Os dados reais das 30 redacoes NAO vivem neste repositorio - sao lidos de
ESSAY_CALIBRATION_PRIVATE_DIR (padrao: ~/agente-ia-edu-core-private-data/
essay_calibration), um armazenamento privado fora de qualquer historico
git publicavel (ver README daquele diretorio; migrado para la em
2026-10-08 depois que uma tentativa de push revelou que pseudonimizar
nomes nao bastava).

Uso: python scripts/essay_calibration_benchmark.py --tag BASELINE_V1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
import uuid
from datetime import datetime, timezone
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
from agente_ia_edu.services.essay_calibration_metrics import (
    BenchmarkResultRow, compute_benchmark_report,
)
from agente_ia_edu.services.essay_calibration_runner import (
    materialize_benchmark_submissions, run_benchmark_corrections,
)

_PRIVATE_DIR = Path(os.environ.get(
    "ESSAY_CALIBRATION_PRIVATE_DIR",
    str(Path.home() / "agente-ia-edu-core-private-data" / "essay_calibration"),
))
_FIXTURE_PATH = _PRIVATE_DIR / "essay_calibration_benchmark_v1.real.json"


def _row_from_result(entry: dict, correction, status: str) -> BenchmarkResultRow:
    expected_scores = None if entry["expected_special_situation"] else entry["expected_scores"]
    engine_scores = None
    engine_zeroed = False
    ocr_duvidoso = False
    if correction is not None and correction.final_scores is not None:
        per_competency = correction.final_scores.get("per_competency") or {}
        engine_scores = {
            code: (per_competency.get(code) or {}).get("points") for code in ("C1", "C2", "C3", "C4", "C5")
        }
        engine_scores["total"] = correction.final_scores.get("total")
        engine_zeroed = engine_scores["total"] == 0
    if correction is not None and correction.ai_output:
        ocr_duvidoso = any(a.get("code") == "OCR_DUVIDOSO" for a in (correction.ai_output.get("alerts") or []))
    return BenchmarkResultRow(
        student_ref=entry["student_ref"], normative_status=entry["normative_status"],
        expected_scores=expected_scores, engine_scores=engine_scores,
        expected_is_special_situation=entry["expected_special_situation"] is not None,
        engine_zeroed_whole_essay=engine_zeroed, engine_status=status, ocr_duvidoso=ocr_duvidoso,
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True, help="identificador unico desta execucao, ex. BASELINE_V1")
    parser.add_argument("--out", default=None, help="caminho do relatorio JSON de saida")
    args = parser.parse_args()

    if not _FIXTURE_PATH.is_file():
        raise SystemExit(
            f"Dados privados de calibracao nao encontrados em {_FIXTURE_PATH}. "
            "Este script precisa das 30 redacoes reais, que vivem fora deste "
            "repositorio - defina ESSAY_CALIBRATION_PRIVATE_DIR ou veja o README "
            "do armazenamento privado."
        )
    fixture_entries = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    engine = create_engine()
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        submissions = await materialize_benchmark_submissions(
            session, fixture_entries=fixture_entries, run_tag=args.tag,
        )
        await session.commit()
    print(f"[{args.tag}] {len(submissions)} submissoes materializadas.", flush=True)

    results = await run_benchmark_corrections(session_factory, submissions=submissions)
    entries_by_ref = {e["student_ref"]: e for e in fixture_entries}
    rows = [_row_from_result(entries_by_ref[ref], correction, status) for ref, correction, status in results]
    report = compute_benchmark_report(rows)

    out_path = Path(args.out) if args.out else Path(f"tests/fixtures/essay_calibration_baselines/{args.tag}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "tag": args.tag, "generated_at": datetime.now(timezone.utc).isoformat(),
        "report": {
            "mae_total": report.mae_total, "mae_per_competency": report.mae_per_competency,
            "bias_total": report.bias_total, "bias_per_competency": report.bias_per_competency,
            "levels_distance_distribution": report.levels_distance_distribution,
            "zero_gate_recall": report.zero_gate_recall, "zero_gate_precision": report.zero_gate_precision,
            "needs_review_count": report.needs_review_count, "ocr_duvidoso_rate": report.ocr_duvidoso_rate,
            "excluded_divergent_or_unverified": report.excluded_divergent_or_unverified,
        },
        "rows": [
            {"student_ref": r.student_ref, "normative_status": r.normative_status,
             "expected_scores": r.expected_scores, "engine_scores": r.engine_scores,
             "engine_status": r.engine_status, "ocr_duvidoso": r.ocr_duvidoso}
            for r in rows
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Relatorio gravado em {out_path}")
    print(json.dumps({
        "mae_total": report.mae_total, "bias_total": report.bias_total,
        "zero_gate_recall": report.zero_gate_recall, "zero_gate_precision": report.zero_gate_precision,
        "needs_review_count": report.needs_review_count, "ocr_duvidoso_rate": report.ocr_duvidoso_rate,
    }, indent=2))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
