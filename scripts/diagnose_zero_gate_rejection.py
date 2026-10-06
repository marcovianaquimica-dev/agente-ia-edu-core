"""Reproduz, isoladamente, a chamada de alert_review_v1 para Sabrina e
Henrique - os dois casos confirmados de FUGA_AO_TEMA levantado na fase 1 e
rejeitado na fase 2 (spec Fase C, diagnostico obrigatorio antes de
qualquer mudanca de prompt/threshold).

Uso: python scripts/diagnose_zero_gate_rejection.py
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
from pathlib import Path

# Script standalone (nao via pytest) - tests/conftest.py so carrega o .env
# dentro de uma sessao pytest, entao mirrora aqui o mesmo pequeno helper ja
# usado em scripts/essay_calibration_benchmark.py para que
# `python scripts/diagnose_zero_gate_rejection.py` funcione sem nenhum
# secret digitado na linha de comando.
_ROOT = Path(__file__).resolve().parent.parent
_ENV_FILE = _ROOT / ".env"


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

sys.path.insert(0, str(_ROOT / "src"))

from agente_ia_edu.essay_prompts import alert_review_v1
from agente_ia_edu.providers.factory import build_text_provider
from agente_ia_edu.providers.models import TextGenerationRequest

_CASES = [
    {
        "name": "Sabrina",
        "alerts": [
            {"code": "FUGA_AO_TEMA", "detail": "O conteúdo identificável trata principalmente de mulher, marido, filhos e relações familiares, sem desenvolver o tema da valorização da pessoa idosa e do enfrentamento do preconceito etário."},
        ],
        "essay_statement": "Desafios para a valorização da pessoa idosa e o enfrentamento do preconceito etário no Brasil",
    },
    {
        "name": "Henrique",
        "alerts": [
            {"code": "FUGA_AO_TEMA", "detail": "A redação não desenvolve nem o tema específico sobre a valorização da pessoa idosa e o preconceito etário nem o assunto amplo relacionado à velhice."},
        ],
        "essay_statement": "Desafios para a valorização da pessoa idosa e o enfrentamento do preconceito etário no Brasil",
    },
]


async def main() -> None:
    provider = build_text_provider()
    for case in _CASES:
        prompt_text = alert_review_v1.build_prompt(
            essay_statement=case["essay_statement"], alerts=case["alerts"],
        )
        result = await provider.generate(TextGenerationRequest(prompt=prompt_text))
        payload = json.loads(result.text)
        print(f"=== {case['name']} ===")
        print(json.dumps(payload, indent=2, ensure_ascii=False))
        print()


if __name__ == "__main__":
    asyncio.run(main())
