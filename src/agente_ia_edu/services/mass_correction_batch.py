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

from ..essay_engine_contract.v5 import (
    COMPETENCY_CODES,
    CONTRACT_VERSION,
    OFFICIAL_LEVEL_POINTS,
    EssayEngineOutput,
)
from ..essay_prompts import alert_review_v1, competency_scoring_v1, get_essay_prompt
from ..providers.adapters.openai import TRANSCRIPTION_SYSTEM_PROMPT, _looks_like_a_refusal
from ..rubrics.loader import RubricFile
from .essay_correction import (
    _ANULA_REDACAO_ALERT_CODES,
    _apply_deterministic_scoring_rules,
    _structured_rationale,
)
from .essay_engine_validation import EssayEngineOutputRejected, validate_engine_output_from_payload

OCR_SYSTEM_PROMPT = TRANSCRIPTION_SYSTEM_PROMPT

_PROMPT_VERSION = "essay_correction_v15"
_ENGINE_VERSION = "r3_correction_engine_v2"


def _extract_message_content(response: dict, *, custom_id: str, context: str) -> str:
    """Extrai o content de uma resposta de chat completions da Batch API,
    com as MESMAS 3 travas que o caminho sincrono ja usa e confirmou em
    producao (providers/adapters/openai.py, "Confirmed live 2026-09-25"):
    recusa explicita no campo `refusal`, conteudo vazio/None, e recusa
    disfarcada de texto comum (_looks_like_a_refusal - nunca reimplementada
    aqui, sempre importada). Sem isso, uma recusa em texto seria gravada
    como se fosse conteudo real (transcricao, JSON de correcao, JSON de
    pontuacao) e seguiria pro resto do pipeline sem ninguem perceber.

    Levanta ValueError nos 3 casos - quem chama decide se isso vira uma
    falha "dura" (propaga, degrada o item no chamador) ou um dict de falha
    "suave" (ver apply_correction_batch_result, que converte em
    failure_fields em vez de deixar propagar)."""
    message = response["body"]["choices"][0]["message"]
    if message.get("refusal"):
        raise ValueError(f"{context} recusado pelo modelo ({custom_id}): {message['refusal']}")
    content = message.get("content")
    if not content:
        raise ValueError(f"{context} devolveu conteudo vazio ({custom_id})")
    if _looks_like_a_refusal(content):
        raise ValueError(f"{context} parece uma recusa em texto ({custom_id}): {content}")
    return content


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
    text = _extract_message_content(response, custom_id=custom_id, context="OCR em lote")
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
    try:
        raw_content = _extract_message_content(response, custom_id=custom_id, context="Correcao em lote")
    except ValueError as exc:
        return {**failure_fields, "failure_reason": str(exc)}
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


def _scoring_batch_line(custom_id: str, prompt_text: str) -> dict:
    return {
        "custom_id": custom_id,
        "method": "POST",
        "url": "/v1/chat/completions",
        "body": {
            "model": None,  # preenchido pela Task 6, que conhece OPENAI_MODEL
            "messages": [{"role": "user", "content": prompt_text}],
        },
    }


def build_scoring_batch_requests(
    correction_id: str, *, output_dict: dict, rubric_file: RubricFile, essay_statement: str,
) -> list[dict]:
    """Monta as 6 linhas de JSONL da fase 2 (pontuacao) de UMA correcao: uma
    por competencia C1-C5 (essay_prompts.competency_scoring_v1) mais uma de
    revisao de alertas ANULA_REDACAO (essay_prompts.alert_review_v1) -
    exatamente a mesma evidencia (anotacoes, mechanical_review so em C1,
    juizo holistico estruturado/rationale) e os mesmos prompts que
    EssayCorrectionService._score_competencies_from_evidence e
    ._review_anula_redacao_alerts ja montam hoje no caminho sincrono
    (services/essay_correction.py), so que cada chamada individual vira uma
    linha de lote em vez de um `await self._get_text_provider().generate(...)`.

    ``output_dict`` e o MESMO formato que EssayCorrection.ai_output guarda
    (um EssayEngineOutput.model_dump(mode="json") ja validado por um
    estagio anterior do lote - nunca o payload cru do modelo).

    ``essay_statement`` e o mesmo texto que
    EssayCorrectionService._effective_essay_statement ja calcula no caminho
    sincrono - nao esta em output_dict (EssayEngineOutput nao carrega o
    enunciado da redacao), entao quem monta o lote (Task 6) precisa
    calcula-lo do mesmo jeito, a partir do EssaySubmission/EssayPrompt, e
    passar aqui pronto.

    Sempre 6 linhas, mesmo quando output_dict nao tem nenhum alerta
    ANULA_REDACAO candidato - diferente do caminho sincrono (que nesse caso
    nem chama o provider, ver _review_anula_redacao_alerts), o lote precisa
    de uma contagem fixa de linhas por correcao para a tabela de
    acompanhamento (Task 3) poder contar linhas pendentes/concluidas sem um
    caso especial por correcao.
    """
    output = EssayEngineOutput.model_validate(output_dict)
    competency_by_code = {c.code: c for c in rubric_file.competencies}
    annotations_by_code: dict[str, list] = {code: [] for code in COMPETENCY_CODES}
    for annotation in output.annotations:
        annotations_by_code.setdefault(annotation.competency_code, []).append(annotation)
    rationale_by_code = {r.competency_code: r for r in output.rationales}
    mechanical_review = [
        {
            "category": m.category, "excerpt": m.excerpt,
            "suggested_form": m.suggested_form, "rule_explanation": m.rule_explanation,
        }
        for m in output.mechanical_review
    ]

    lines: list[dict] = []
    for code in COMPETENCY_CODES:
        competency = competency_by_code[code]
        levels = [(level.points, level.descriptor) for level in competency.levels]
        annotations = [
            {"short_comment": a.short_comment, "long_comment": a.long_comment}
            for a in annotations_by_code.get(code, [])
        ]
        rationale_obj = rationale_by_code.get(code)
        rationale = _structured_rationale(output, code)
        if rationale is None and rationale_obj is not None:
            rationale = {
                "summary": rationale_obj.summary,
                "strengths": rationale_obj.strengths,
                "growth_area": rationale_obj.growth_area,
            }
        prompt_text = competency_scoring_v1.build_prompt(
            competency_code=code, competency_label=competency.official_title,
            levels=levels, annotations=annotations,
            # mechanical_review e domino exclusivo de C1 (norma padrao) -
            # ver MechanicalOccurrence.category e _score_competencies_from_evidence.
            mechanical_review=mechanical_review if code == "C1" else (),
            rationale=rationale,
        )
        lines.append(_scoring_batch_line(f"{correction_id}:{code}", prompt_text))

    candidates = [
        {"code": alert.code, "detail": alert.detail}
        for alert in output.alerts
        if alert.code in _ANULA_REDACAO_ALERT_CODES
    ]
    alert_prompt_text = alert_review_v1.build_prompt(essay_statement=essay_statement, alerts=candidates)
    lines.append(_scoring_batch_line(f"{correction_id}:alert", alert_prompt_text))
    return lines


def _scoring_result_content(result_line: dict, custom_id: str) -> str:
    if result_line.get("error"):
        raise ValueError(f"Pontuacao em lote falhou para {custom_id}: {result_line['error']}")
    response = result_line["response"]
    if response["status_code"] != 200:
        raise ValueError(
            f"Pontuacao em lote devolveu status {response['status_code']} para "
            f"{custom_id}: {response['body']}"
        )
    return _extract_message_content(response, custom_id=custom_id, context="Pontuacao em lote")


def apply_scoring_batch_results(
    correction_id: str, result_lines_by_custom_id: dict[str, dict], *,
    output_dict: dict, rubric_file: RubricFile,
) -> dict:
    """Aplica as 6 linhas de resultado da fase 2 (pontuacao) de UMA
    correcao, ja agrupadas por custom_id, reconstruindo phase2_points/
    confirmed_alert_codes exatamente como EssayCorrectionService._run_ai faz
    no caminho sincrono apos _score_competencies_from_evidence/
    _review_anula_redacao_alerts, e chama a MESMA
    _apply_deterministic_scoring_rules (services/essay_correction.py) -
    nunca reimplementa a logica de pontuacao/regras da rubrica.

    Levanta ValueError para qualquer linha com erro de lote, status HTTP
    diferente de 200, JSON invalido, points fora da escala oficial ou
    confirmed_alert_codes de formato invalido - mesmo tipo de excecao que
    _score_competencies_from_evidence/_review_anula_redacao_alerts ja
    levantam no caminho sincrono, entao quem chama esta funcao pode
    reaproveitar o mesmo tratamento de falha que _run_ai ja usa.
    """
    output = EssayEngineOutput.model_validate(output_dict)
    if output.identification.rubric_version != rubric_file.rubric_version:
        # A licao da Task 4 (paridade de rubric_version com o caminho
        # sincrono) aplicada aqui na direcao oposta: garante que o
        # rubric_file usado para montar as linhas de pontuacao e o MESMO
        # que corrigiu esta redacao, nunca um rubric_file desatualizado.
        raise ValueError(
            f"rubric_version incompativel: output_dict foi corrigido com "
            f"{output.identification.rubric_version!r}, mas rubric_file "
            f"passado e {rubric_file.rubric_version!r}"
        )

    phase2_points: dict[str, int] = {}
    for code in COMPETENCY_CODES:
        custom_id = f"{correction_id}:{code}"
        if custom_id not in result_lines_by_custom_id:
            raise ValueError(
                f"pontuacao em lote nao tem resultado para {custom_id!r} - "
                "a requisicao pode ter falhado na API e caido no arquivo de erro"
            )
        content = _scoring_result_content(result_lines_by_custom_id[custom_id], custom_id)
        payload = json.loads(content)
        if "points" not in payload:
            raise ValueError(
                f"competency scoring em lote para {code} nao devolveu a chave 'points': {payload!r}"
            )
        points = int(payload["points"])
        if points not in OFFICIAL_LEVEL_POINTS:
            raise ValueError(
                f"competency scoring em lote para {code} devolveu points invalido: "
                f"{points!r} (deve ser um de {list(OFFICIAL_LEVEL_POINTS)})"
            )
        phase2_points[code] = points

    alert_custom_id = f"{correction_id}:alert"
    if alert_custom_id not in result_lines_by_custom_id:
        raise ValueError(
            f"pontuacao em lote nao tem resultado para {alert_custom_id!r} - "
            "a requisicao pode ter falhado na API e caido no arquivo de erro"
        )
    alert_content = _scoring_result_content(
        result_lines_by_custom_id[alert_custom_id], alert_custom_id
    )
    alert_payload = json.loads(alert_content)
    if "confirmed_alert_codes" not in alert_payload:
        raise ValueError(
            f"alert review em lote nao devolveu a chave 'confirmed_alert_codes': {alert_payload!r}"
        )
    raw_confirmed = alert_payload["confirmed_alert_codes"]
    if not isinstance(raw_confirmed, list) or not all(isinstance(c, str) for c in raw_confirmed):
        raise ValueError(
            f"alert review em lote devolveu confirmed_alert_codes invalido: {raw_confirmed!r}"
        )
    confirmed = set(raw_confirmed)
    all_codes = {alert.code for alert in output.alerts}
    candidate_codes = {
        alert.code for alert in output.alerts if alert.code in _ANULA_REDACAO_ALERT_CODES
    }
    # Nunca deixa a revisao inventar um codigo que nao era candidato -
    # mesma regra de _review_anula_redacao_alerts.
    confirmed &= candidate_codes
    passthrough_codes = all_codes - candidate_codes
    confirmed_alert_codes = confirmed | passthrough_codes

    return {
        "final_scores": _apply_deterministic_scoring_rules(output, phase2_points, confirmed_alert_codes),
    }
