"""Correcao em massa via Batch API da OpenAI (spec 2026-09-29): monta
requisicoes em lote e aplica os resultados de volta aos modelos ja
existentes (EssayBatchPage/EssaySubmission/EssayCorrection), reaproveitando
a mesma logica de negocio que o pipeline sincrono ja usa - NUNCA duplica
prompt, validacao de contrato ou calculo de pontuacao.
"""

from __future__ import annotations

import base64
import json
import uuid as _uuid
from pathlib import Path

from ..essay_engine_contract.v5 import CONTRACT_VERSION
from ..essay_prompts import get_essay_prompt
from ..providers.adapters.openai import TRANSCRIPTION_SYSTEM_PROMPT
from .essay_engine_validation import EssayEngineOutputRejected, validate_engine_output_from_payload

OCR_SYSTEM_PROMPT = TRANSCRIPTION_SYSTEM_PROMPT

_PROMPT_VERSION = "essay_correction_v15"
_ENGINE_VERSION = "r3_correction_engine_v2"


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


def build_correction_batch_request(
    submission_id: str, *, essay_statement: str, rubric_payload: dict,
    canonical_text: str, include_scores: bool,
) -> dict:
    prompt_text = get_essay_prompt(_PROMPT_VERSION).build(
        anchor_mode="TEXT_OFFSET", essay_statement=essay_statement,
        rubric=rubric_payload, include_scores=include_scores, text=canonical_text,
    )
    return {
        "custom_id": submission_id,
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": None,  # preenchido pela Task 6 com OPENAI_MODEL
            "messages": [{"role": "user", "content": prompt_text}],
        },
    }


def apply_correction_batch_result(result_line: dict, *, rubric_view, text: str) -> dict:
    """Apply one Batch API result line for the correction stage (phase 1 only).

    Mirrors EssayCorrectionService._run_ai's failure shape and validation call
    (services/essay_correction.py) so both the sync and batch paths write the
    same EssayCorrection contract. `final_scores` is deliberately absent here -
    it is only complete after Task 5's phase 2 (competency scoring from
    evidence + alert review), which this function does not run.
    """
    custom_id = result_line["custom_id"]
    failure_fields = {
        "correction_key": None, "rubric_version": rubric_view.rubric_version,
        "model_version": None, "prompt_version": _PROMPT_VERSION,
        "engine_version": _ENGINE_VERSION, "ai_output": None, "final_feedback": None,
        "failure_reason": None,
    }
    if result_line.get("error"):
        return {**failure_fields, "failure_reason": f"BatchError: {result_line['error']}"}
    response = result_line["response"]
    if response["status_code"] != 200:
        return {
            **failure_fields,
            "failure_reason": f"BatchHTTPError: status {response['status_code']}: {response['body']}",
        }
    raw_content = response["body"]["choices"][0]["message"]["content"]
    try:
        raw_payload = json.loads(raw_content)
    except json.JSONDecodeError as exc:
        return {**failure_fields, "failure_reason": f"Model returned invalid JSON: {exc}"}

    identification = {
        "essay_id": str(_uuid.uuid4()), "essay_version_id": custom_id,
        "rubric_version": rubric_view.rubric_version, "model_version": "batch",
        "prompt_version": _PROMPT_VERSION, "engine_version": _ENGINE_VERSION,
        "contract_version": CONTRACT_VERSION, "anchor_mode": "TEXT_OFFSET",
    }
    full_payload = {**raw_payload, "identification": identification}
    try:
        output = validate_engine_output_from_payload(full_payload, rubric=rubric_view, text=text)
    except EssayEngineOutputRejected as exc:
        return {**failure_fields, "failure_reason": f"{exc}"}

    return {
        **failure_fields,
        "ai_output": output.model_dump(mode="json"),
        "final_feedback": output.feedback.model_dump(mode="json"),
        "failure_reason": None,
    }
