"""Correcao em massa via Batch API da OpenAI (spec 2026-09-29): monta
requisicoes em lote e aplica os resultados de volta aos modelos ja
existentes (EssayBatchPage/EssaySubmission/EssayCorrection), reaproveitando
a mesma logica de negocio que o pipeline sincrono ja usa - NUNCA duplica
prompt, validacao de contrato ou calculo de pontuacao.
"""

from __future__ import annotations

import base64
from pathlib import Path

from ..providers.adapters.openai import TRANSCRIPTION_SYSTEM_PROMPT

OCR_SYSTEM_PROMPT = TRANSCRIPTION_SYSTEM_PROMPT


def _guess_mime(path: Path) -> str:
    suffix = path.suffix.lower()
    return "image/png" if suffix == ".png" else "image/jpeg"


def build_ocr_batch_request(custom_id: str, image_path: Path, *, detail: str = "high") -> dict:
    image_b64 = base64.b64encode(image_path.read_bytes()).decode("ascii")
    mime = _guess_mime(image_path)
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": None,  # preenchido pela Task 6, que conhece OPENAI_VISION_MODEL
            "messages": [
                {"role": "system", "content": OCR_SYSTEM_PROMPT},
                {
                    "role": "user",
                    "content": [
                        {
                            "type": "image_url",
                            "image_url": {"url": f"data:{mime};base64,{image_b64}", "detail": detail},
                        }
                    ],
                },
            ],
        },
    }


def apply_ocr_batch_result(result_line: dict) -> tuple[str, str]:
    custom_id = result_line["custom_id"]
    if result_line.get("error"):
        raise ValueError(f"OCR em lote falhou para {custom_id}: {result_line['error']}")
    response = result_line["response"]
    if response["status_code"] != 200:
        raise ValueError(
            f"OCR em lote devolveu status {response['status_code']} para {custom_id}: {response['body']}"
        )
    text = response["body"]["choices"][0]["message"]["content"]
    return custom_id, text
