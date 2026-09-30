# src/agente_ia_edu/services/mass_correction_sampling.py
"""Selecao de amostra para revisao humana apos a correcao em massa (spec
2026-09-29, secao "Amostragem e revisao"). Correcoes com alerta ou sem
nota (falha) sempre entram na amostra - nao sao sorteadas, sao garantidas.
O resto e sorteado a uma taxa configuravel; quem nao cai na amostra e
aprovado automaticamente (bulk_approve, ja existente).

O sorteio usa o hash do proprio id da correcao (nao random.random()) para
ser deterministico e reprodutivel sem depender de fixar uma seed global
que afetaria qualquer outro codigo do processo que tambem use random."""

from __future__ import annotations

import hashlib
import uuid


def _deterministic_unit_interval(correction_id: uuid.UUID) -> float:
    digest = hashlib.sha256(correction_id.bytes).digest()
    return int.from_bytes(digest[:8], "big") / 2**64


def select_sample_for_review(
    corrections: list[dict], *, sample_rate: float = 0.05,
) -> tuple[list[uuid.UUID], list[uuid.UUID]]:
    sample_ids: list[uuid.UUID] = []
    auto_approve_ids: list[uuid.UUID] = []
    for correction in corrections:
        correction_id = correction["id"]
        if correction["alerts"] or not correction["has_scores"]:
            sample_ids.append(correction_id)
            continue
        if _deterministic_unit_interval(correction_id) < sample_rate:
            sample_ids.append(correction_id)
        else:
            auto_approve_ids.append(correction_id)
    return sample_ids, auto_approve_ids
