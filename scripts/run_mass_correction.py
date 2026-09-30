# scripts/run_mass_correction.py
"""Dispara/retoma o processamento em massa de um lote de redacoes
escaneadas via Batch API. Uso:

    .venv/bin/python scripts/run_mass_correction.py --school-id <uuid> --stage OCR

Idempotente: rodar de novo com os mesmos argumentos enquanto ha um
MassCorrectionRun em andamento so consulta o status, nunca resubmete
(mass_correction_driver.advance_run garante isso). Rodar de novo depois de
um lote "completed" baixa e aplica o resultado (uma unica vez - cada
aplicacao verifica se o alvo ja tem o valor antes de escrever, entao
chamar duas vezes sobre o mesmo lote e inofensivo) e, se ainda houver
trabalho pendente para o (school_id, stage), submete um novo lote com
sequence_number incrementado.

Tres estagios (Tasks 3-5), cada um com sua propria nocao de "pendente":
  - OCR: EssayBatchPage sem ocr_body_text, desta escola.
  - CORRECTION: EssaySubmission SUBMITTED, TEXT_OFFSET, com canonical_text,
    sem EssayCorrection ainda (mesma idempotencia que
    EssayCorrectionService.correct() ja usa no caminho sincrono).
  - SCORING: EssayCorrection com ai_output mas sem final_scores ainda
    (fase 1 da correcao ja rodou, fase 2 - pontuacao - ainda nao).

Task 9 (amostragem para revisao humana): apos aplicar final_scores de cada
correcao recem-pontuada do estagio SCORING e rodar
EssayCorrectionService._apply_review_policy (a mesma politica do caminho
sincrono), select_sample_for_review decide, entre as que ficaram
PENDING_REVIEW, quem entra na amostra de revisao humana (garantido para
quem tem alerta ou falhou sem nota; sorteado a 5% pro resto) e quem e
aprovado automaticamente via EssayCorrectionService.bulk_approve - ver
_apply_scoring_results.

Achado corrigido aqui (nao nas Tasks 3-5, que ja estao aprovadas e
commitadas): apply_correction_batch_result nunca preenche model_version/
correction_key no caminho de sucesso (fica None nos dois, herdado do
failure_fields inicial) - isso violaria a CHECK constraint
ck_essay_corrections_success_has_key_and_model do Postgres assim que
tentassemos gravar uma correcao bem-sucedida. Corrigido aqui, no ponto de
gravacao real, lendo o "model" que a propria resposta da Batch API traz e
computando correction_key com a MESMA funcao que o caminho sincrono usa
(essay_correction_key.correction_key) - nunca reimplementada.
"""
from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import sys
import tempfile
import uuid
from collections import defaultdict
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from dotenv import load_dotenv
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.models import (
    EssayBatchPage,
    EssayBatchUpload,
    EssayCorrection,
    EssayPrompt,
    EssaySubmission,
    MassCorrectionRun,
    PromptAssignment,
)
from agente_ia_edu.rubrics.loader import load_rubric_file
from agente_ia_edu.services.essay_batch import EssayBatchService, match_student, parse_header_text
from agente_ia_edu.services.essay_correction import (
    _RUBRIC_FILE_NAME,
    EssayCorrectionService,
    _effective_essay_statement,
    _rubric_payload,
)
from agente_ia_edu.services.essay_correction_key import correction_key as compute_correction_key
from agente_ia_edu.services.essay_engine_validation import load_rubric_view
from agente_ia_edu.services.institution_settings import InstitutionSettingsService
from agente_ia_edu.services.mass_correction_batch import (
    apply_correction_batch_result,
    apply_ocr_batch_result,
    apply_scoring_batch_results,
    build_correction_batch_request,
    build_ocr_batch_request,
    build_scoring_batch_requests,
)
from agente_ia_edu.services.mass_correction_driver import advance_run
from agente_ia_edu.services.mass_correction_sampling import select_sample_for_review
from agente_ia_edu.providers.openai_batch_client import download_file_lines

IN_FLIGHT_STATUSES = {"PENDING", "validating", "in_progress", "finalizing"}
TERMINAL_STATUSES = {"completed", "failed", "expired", "cancelled"}


async def _latest_run(session, *, school_id: uuid.UUID, stage: str) -> MassCorrectionRun | None:
    result = await session.execute(
        select(MassCorrectionRun)
        .where(MassCorrectionRun.school_id == school_id, MassCorrectionRun.stage == stage)
        .order_by(MassCorrectionRun.sequence_number.desc())
        .limit(1)
    )
    return result.scalar_one_or_none()


# --------------------------------------------------------------------------
# OCR
# --------------------------------------------------------------------------

async def _collect_ocr_pending_lines(session, *, school_id: uuid.UUID) -> list[dict]:
    """Recorta cada pagina pendente em (cabecalho, corpo) e gera 2 linhas de
    JSONL por pagina - a mesma separacao POSICIONAL que
    EssayBatchService.read_page_regions faz no caminho sincrono
    (services/essay_batch.py), reaproveitando _crop_regions em vez de
    reimplementa-la. Sem o recorte, o OCR le a folha inteira (cabecalho +
    corpo colados) e parse_header_text nunca encontra um nome limpo o
    suficiente para casar com o roster - e por isso nenhuma pagina virava
    MATCHED_AUTO antes desta correcao."""
    vision_model = os.environ["OPENAI_VISION_MODEL"]
    pages = (
        await session.execute(
            select(EssayBatchPage)
            .join(EssayBatchUpload, EssayBatchPage.batch_id == EssayBatchUpload.id)
            .where(
                EssayBatchUpload.school_id == school_id,
                EssayBatchPage.ocr_body_text.is_(None),
            )
        )
    ).scalars().all()
    lines = []
    for page in pages:
        scratch_dir = Path(tempfile.mkdtemp(prefix="mass_ocr_regions_"))
        try:
            header_path, body_path = await asyncio.to_thread(
                EssayBatchService._crop_regions, Path(page.storage_uri), scratch_dir
            )
            header_line = build_ocr_batch_request(f"{page.id}:header", header_path)
            body_line = build_ocr_batch_request(f"{page.id}:body", body_path)
            header_line["body"]["model"] = vision_model
            body_line["body"]["model"] = vision_model
            lines.append(header_line)
            lines.append(body_line)
        finally:
            shutil.rmtree(scratch_dir, ignore_errors=True)
    return lines


async def _apply_ocr_results(session, result_lines: list[dict]) -> None:
    """Aplica os resultados de OCR em lote e materializa as submissoes
    prontas - a metade que faltava do caminho sincrono
    (EssayBatchService.process_batch/_process_page): parsear o cabecalho,
    casar o aluno pelo roster da turma e agrupar corridas de paginas
    consecutivas do mesmo aluno em EssaySubmission (materialize_batch),
    reaproveitados sem reescrita. Uma pagina so e aplicada quando os dois
    resultados (header E body) chegaram nesta passada - se so um chegou (por
    exemplo por causa de fracionamento de lote, Task 12, ainda nao
    implementada), a pagina fica pendente para a proxima aplicacao."""
    texts_by_page: dict[uuid.UUID, dict[str, str]] = defaultdict(dict)
    for result_line in result_lines:
        custom_id = result_line.get("custom_id", "")
        page_id_str, _, region = custom_id.rpartition(":")
        if region not in ("header", "body") or not page_id_str:
            print(f"[OCR] custom_id inesperado: {custom_id!r}")
            continue
        try:
            _, text = apply_ocr_batch_result(result_line)
        except ValueError as exc:
            print(f"[OCR] falha em {custom_id!r}: {exc}")
            continue
        texts_by_page[uuid.UUID(page_id_str)][region] = text

    roster_cache: dict[uuid.UUID, list[tuple[uuid.UUID, str]]] = {}
    touched_batch_ids: set[uuid.UUID] = set()
    for page_id, regions in texts_by_page.items():
        if "body" not in regions:
            print(f"[OCR] pagina {page_id} sem resultado de corpo - deixada pendente")
            continue
        page = await session.get(EssayBatchPage, page_id)
        if page is None:
            print(f"[OCR] EssayBatchPage nao encontrada para {page_id}")
            continue
        if page.ocr_body_text is not None:
            continue  # ja aplicado (chamada repetida sobre o mesmo lote) - idempotente

        name, cpf = parse_header_text(regions.get("header", ""))
        page.ocr_name_raw = name
        page.ocr_cpf_raw = cpf
        page.ocr_body_text = regions["body"].strip()

        if page.batch_id not in roster_cache:
            batch = await session.get(EssayBatchUpload, page.batch_id)
            rows = await EssayBatchService(session).class_roster(
                school_id=batch.school_id, class_id=batch.class_id
            )
            roster_cache[page.batch_id] = [(student_id, full_name) for student_id, full_name, _doc in rows]
        page.matched_student_id = match_student(name, roster_cache[page.batch_id])
        if page.status != "RESOLVED_MANUAL":
            page.status = "MATCHED_AUTO" if page.matched_student_id else "NEEDS_REVIEW"
        touched_batch_ids.add(page.batch_id)

    await session.commit()

    batch_service = EssayBatchService(session)
    for batch_id in touched_batch_ids:
        await batch_service.materialize_batch(batch_id)
        remaining = await session.execute(
            select(EssayBatchPage.id).where(
                EssayBatchPage.batch_id == batch_id, EssayBatchPage.ocr_body_text.is_(None),
            )
        )
        if remaining.first() is None:
            batch = await session.get(EssayBatchUpload, batch_id)
            batch.status = "DONE"
            await session.commit()


# --------------------------------------------------------------------------
# CORRECTION (fase 1)
# --------------------------------------------------------------------------

async def _collect_correction_pending_lines(session, *, school_id: uuid.UUID) -> list[dict]:
    text_model = os.environ["OPENAI_MODEL"]
    already_corrected = select(EssayCorrection.essay_submission_id).where(
        EssayCorrection.essay_submission_id == EssaySubmission.id
    )
    submissions = (
        await session.execute(
            select(EssaySubmission).where(
                EssaySubmission.school_id == school_id,
                EssaySubmission.status == "SUBMITTED",
                EssaySubmission.anchor_mode == "TEXT_OFFSET",
                EssaySubmission.canonical_text.is_not(None),
                ~already_corrected.exists(),
            )
        )
    ).scalars().all()
    if not submissions:
        return []

    rubric_file = load_rubric_file(_RUBRIC_FILE_NAME)
    rubric_payload = _rubric_payload(rubric_file)
    settings = await InstitutionSettingsService(session).get_settings(school_id)
    include_scores = settings.correction_mode == "AVALIATIVO"

    lines = []
    for submission in submissions:
        assignment = await session.get(PromptAssignment, submission.prompt_assignment_id)
        essay_prompt = await session.get(EssayPrompt, assignment.essay_prompt_id)
        essay_statement = _effective_essay_statement(submission, essay_prompt)
        line = build_correction_batch_request(
            str(submission.id), essay_statement=essay_statement, rubric_payload=rubric_payload,
            canonical_text=submission.canonical_text, include_scores=include_scores,
        )
        line["body"]["model"] = text_model
        lines.append(line)
    return lines


async def _apply_correction_results(session, result_lines: list[dict]) -> None:
    rubric_file = load_rubric_file(_RUBRIC_FILE_NAME)
    rubric_view = await load_rubric_view(session, rubric_file.rubric_version)

    for result_line in result_lines:
        submission_id = result_line["custom_id"]
        existing = await session.scalar(
            select(EssayCorrection).where(
                EssayCorrection.essay_submission_id == uuid.UUID(submission_id)
            )
        )
        if existing is not None:
            continue  # ja aplicado (chamada repetida) - mesma idempotencia de EssayCorrectionService.correct()

        submission = await session.get(EssaySubmission, uuid.UUID(submission_id))
        if submission is None:
            print(f"[CORRECTION] EssaySubmission nao encontrada para custom_id={submission_id!r}")
            continue

        fields = apply_correction_batch_result(
            result_line, rubric_view=rubric_view, text=submission.canonical_text,
        )
        if fields["ai_output"] is not None:
            # Ver docstring do modulo: apply_correction_batch_result nao preenche
            # model_version/correction_key no sucesso - completa aqui com a mesma
            # logica do caminho sincrono (essay_correction_key.correction_key),
            # usando o "model" que a propria resposta em lote ja trouxe.
            response_body = result_line["response"]["body"]
            model_version = response_body.get("model")
            fields["model_version"] = model_version
            assignment = await session.get(PromptAssignment, submission.prompt_assignment_id)
            fields["correction_key"] = compute_correction_key(
                normalized_text_hash=submission.normalized_text_hash,
                essay_prompt_id=str(assignment.essay_prompt_id),
                rubric_version=fields["rubric_version"],
                model_version=model_version,
                prompt_version=fields["prompt_version"],
                engine_version=fields["engine_version"],
            )
            status = "PENDING_REVIEW"  # final_scores so fecha na fase 2 (estagio SCORING)
        else:
            status = "NEEDS_REVIEW"

        correction = EssayCorrection(
            id=uuid.uuid4(), school_id=submission.school_id,
            essay_submission_id=submission.id, status=status, **fields,
        )
        session.add(correction)
    await session.commit()


# --------------------------------------------------------------------------
# SCORING (fase 2)
# --------------------------------------------------------------------------

async def _collect_scoring_pending_lines(session, *, school_id: uuid.UUID) -> list[dict]:
    text_model = os.environ["OPENAI_MODEL"]
    corrections = (
        await session.execute(
            select(EssayCorrection).where(
                EssayCorrection.school_id == school_id,
                EssayCorrection.ai_output.is_not(None),
                EssayCorrection.final_scores.is_(None),
                EssayCorrection.status == "PENDING_REVIEW",
            )
        )
    ).scalars().all()
    if not corrections:
        return []

    rubric_file = load_rubric_file(_RUBRIC_FILE_NAME)
    lines = []
    for correction in corrections:
        submission = await session.get(EssaySubmission, correction.essay_submission_id)
        assignment = await session.get(PromptAssignment, submission.prompt_assignment_id)
        essay_prompt = await session.get(EssayPrompt, assignment.essay_prompt_id)
        essay_statement = _effective_essay_statement(submission, essay_prompt)
        correction_lines = build_scoring_batch_requests(
            str(correction.id), output_dict=correction.ai_output, rubric_file=rubric_file,
            essay_statement=essay_statement,
        )
        for line in correction_lines:
            line["body"]["model"] = text_model
        lines.extend(correction_lines)
    return lines


def _sampling_entry(correction: EssayCorrection) -> dict:
    """Monta a entrada que select_sample_for_review espera para UMA
    correcao recem-pontuada (Task 9, spec "Amostragem e revisao"):
    alerts vem do ai_output da fase 1 (a mesma lista que
    _apply_deterministic_scoring_rules olha, confirmada ou nao pela fase 2 -
    qualquer alerta reportado pelo modelo e motivo suficiente para exigir
    revisao humana, nao so os confirmados como ANULA_REDACAO)."""
    alerts = [alert["code"] for alert in (correction.ai_output or {}).get("alerts") or []]
    return {"id": correction.id, "alerts": alerts, "has_scores": correction.final_scores is not None}


async def _apply_scoring_results(session, result_lines: list[dict]) -> None:
    by_correction: dict[str, dict[str, dict]] = defaultdict(dict)
    for result_line in result_lines:
        custom_id = result_line["custom_id"]
        correction_id_str, _, _suffix = custom_id.rpartition(":")
        by_correction[correction_id_str][custom_id] = result_line

    rubric_file = load_rubric_file(_RUBRIC_FILE_NAME)
    service = EssayCorrectionService(session)

    scored_for_sampling: list[dict] = []
    for correction_id_str, results_by_custom_id in by_correction.items():
        correction = await session.get(EssayCorrection, uuid.UUID(correction_id_str))
        if correction is None:
            print(f"[SCORING] EssayCorrection nao encontrada para {correction_id_str!r}")
            continue
        if correction.final_scores is not None:
            continue  # ja aplicado (chamada repetida) - idempotente

        try:
            result = apply_scoring_batch_results(
                correction_id_str, results_by_custom_id,
                output_dict=correction.ai_output, rubric_file=rubric_file,
            )
        except (ValueError, KeyError) as exc:
            print(f"[SCORING] falha em {correction_id_str!r}: {exc}")
            correction.status = "NEEDS_REVIEW"
            correction.failure_reason = f"BatchScoringFailed: {exc}"
            scored_for_sampling.append(_sampling_entry(correction))
            continue

        correction.final_scores = result["final_scores"]
        submission = await session.get(EssaySubmission, correction.essay_submission_id)
        await service._apply_review_policy(correction, submission)
        if correction.status != "APPROVED":
            # A politica institucional (FORMATIVO / tema livre /
            # validation_enabled=False) ja publicou esta correcao sozinha -
            # a amostragem so decide o destino de quem AINDA esta pendente
            # (PENDING_REVIEW) ou falhou (NEEDS_REVIEW), nunca reabre o que
            # a politica ja fechou.
            scored_for_sampling.append(_sampling_entry(correction))

    # Amostragem para revisao humana (Task 9, spec "Amostragem e revisao"):
    # entre as correcoes recem-pontuadas neste lote, quem tem alerta ou
    # falhou (sem nota) sempre fica pendente de revisao (PENDING_REVIEW/
    # NEEDS_REVIEW, como _apply_review_policy/o bloco except ja deixaram);
    # o resto e sorteado a uma taxa configuravel (padrao 5%, ver
    # select_sample_for_review) e quem nao cai na amostra e aprovado
    # automaticamente - bulk_approve ja existente (services/essay_correction.py),
    # que so aprova quem ainda esta PENDING_REVIEW (best-effort, ignora
    # silenciosamente qualquer id que ja tenha saido desse estado).
    sample_ids, auto_approve_ids = select_sample_for_review(scored_for_sampling)
    approved_corrections: list = []
    if auto_approve_ids:
        approved_corrections, _failures = await service.bulk_approve(
            auto_approve_ids, reviewed_by_external_identity="mass-correction-driver",
        )
    if scored_for_sampling:
        print(
            f"[SCORING] amostragem: {len(sample_ids)} para revisao humana, "
            f"{len(approved_corrections)} aprovadas automaticamente"
        )
    await session.commit()


# --------------------------------------------------------------------------
# Driver de estagio
# --------------------------------------------------------------------------

_COLLECTORS = {
    "OCR": _collect_ocr_pending_lines,
    "CORRECTION": _collect_correction_pending_lines,
    "SCORING": _collect_scoring_pending_lines,
}
_APPLIERS = {
    "OCR": _apply_ocr_results,
    "CORRECTION": _apply_correction_results,
    "SCORING": _apply_scoring_results,
}


async def run_stage(session, *, school_id: uuid.UUID, stage: str, api_key: str) -> None:
    run = await _latest_run(session, school_id=school_id, stage=stage)

    if run is not None and run.status not in TERMINAL_STATUSES:
        if run.openai_batch_id is not None:
            # Ja submetido - so consulta o status, nunca resubmete.
            run = await advance_run(session, run.id, api_key=api_key)
            print(f"[{stage}] lote {run.openai_batch_id} (run {run.id}) esta {run.status!r}")
            return
        # Run criada mas o processo caiu antes de submeter (nunca chegou a
        # ter openai_batch_id) - retoma a MESMA run em vez de criar outra.
        pending_lines = await _COLLECTORS[stage](session, school_id=school_id)
        if not pending_lines:
            print(f"[{stage}] run {run.id} estava PENDING sem trabalho pendente para submeter")
            return
        run = await advance_run(session, run.id, api_key=api_key, pending_lines=pending_lines)
        print(f"[{stage}] run {run.id} retomada e submetida: lote {run.openai_batch_id}, status={run.status!r}")
        return

    if run is not None and run.status == "completed" and run.output_file_id is not None:
        result_lines = await download_file_lines(run.output_file_id, api_key=api_key)
        await _APPLIERS[stage](session, result_lines)
        print(f"[{stage}] resultados do lote {run.openai_batch_id} aplicados ({len(result_lines)} linhas)")

    pending_lines = await _COLLECTORS[stage](session, school_id=school_id)
    if not pending_lines:
        print(f"[{stage}] nada pendente para a escola {school_id}")
        return

    next_sequence = 1 if run is None else run.sequence_number + 1
    new_run = MassCorrectionRun(
        id=uuid.uuid4(), school_id=school_id, stage=stage,
        sequence_number=next_sequence, request_count=len(pending_lines), status="PENDING",
    )
    session.add(new_run)
    await session.flush()
    new_run = await advance_run(session, new_run.id, api_key=api_key, pending_lines=pending_lines)
    print(
        f"[{stage}] novo lote submetido: run {new_run.id}, batch {new_run.openai_batch_id}, "
        f"{len(pending_lines)} linhas, status={new_run.status!r}"
    )


async def main(school_id: str, stage: str) -> None:
    load_dotenv(Path(__file__).resolve().parent.parent / ".env")
    api_key = os.environ["OPENAI_API_KEY"]
    database_url = os.environ["DATABASE_URL"]
    engine = create_async_engine(database_url)
    session_factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    school_uuid = uuid.UUID(school_id)
    try:
        async with session_factory() as session:
            await run_stage(session, school_id=school_uuid, stage=stage, api_key=api_key)
    finally:
        await engine.dispose()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--school-id", required=True)
    parser.add_argument("--stage", required=True, choices=["OCR", "CORRECTION", "SCORING"])
    args = parser.parse_args()
    asyncio.run(main(args.school_id, args.stage))
