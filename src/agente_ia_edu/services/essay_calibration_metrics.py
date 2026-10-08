"""Metricas puras do benchmark de calibracao - nenhuma dependencia de DB ou
IA, testavel so com dados sinteticos. Ver spec Fase A: duas 'verdades'
distintas - uma linha com normative_status DIVERGENT ou UNVERIFIED nunca
entra em MAE/bias (o motor nunca e cobrado por reproduzir um resultado nao
confirmado contra a norma vigente), mas ainda conta pra visibilidade via
excluded_divergent_or_unverified."""
from __future__ import annotations

from dataclasses import dataclass

_COMPETENCIES = ("C1", "C2", "C3", "C4", "C5")


@dataclass(frozen=True)
class BenchmarkResultRow:
    student_ref: str
    normative_status: str
    expected_scores: dict[str, int] | None
    engine_scores: dict[str, int] | None
    expected_is_special_situation: bool
    engine_zeroed_whole_essay: bool
    engine_status: str
    ocr_duvidoso: bool


@dataclass(frozen=True)
class BenchmarkReport:
    mae_total: float
    mae_per_competency: dict[str, float]
    bias_total: float
    bias_per_competency: dict[str, float]
    levels_distance_distribution: dict[int, int]
    zero_gate_recall: float | None
    zero_gate_precision: float | None
    needs_review_count: int
    ocr_duvidoso_rate: float
    excluded_divergent_or_unverified: list[str]


def compute_benchmark_report(rows: list[BenchmarkResultRow]) -> BenchmarkReport:
    excluded = [r.student_ref for r in rows if r.normative_status in ("DIVERGENT", "UNVERIFIED")]
    needs_review_count = sum(1 for r in rows if r.engine_scores is None)
    ocr_duvidoso_rate = (
        sum(1 for r in rows if r.ocr_duvidoso) / len(rows) if rows else 0.0
    )

    # So entram no MAE/bias/distribuicao de niveis: normative_status CONFIRMED
    # (unica referencia validada contra a norma vigente) E com nota real do
    # motor (engine_scores is not None - uma linha NEEDS_REVIEW nao tem nota
    # pra comparar, conta so em needs_review_count).
    scored_confirmed = [
        r for r in rows
        if r.normative_status == "CONFIRMED" and r.engine_scores is not None
    ]

    total_abs_errors = [abs(r.engine_scores["total"] - r.expected_scores["total"]) for r in scored_confirmed]
    total_signed_errors = [r.engine_scores["total"] - r.expected_scores["total"] for r in scored_confirmed]
    mae_total = sum(total_abs_errors) / len(total_abs_errors) if total_abs_errors else 0.0
    bias_total = sum(total_signed_errors) / len(total_signed_errors) if total_signed_errors else 0.0

    mae_per_competency = {}
    bias_per_competency = {}
    for competency in _COMPETENCIES:
        abs_errors = [abs(r.engine_scores[competency] - r.expected_scores[competency]) for r in scored_confirmed]
        signed_errors = [r.engine_scores[competency] - r.expected_scores[competency] for r in scored_confirmed]
        mae_per_competency[competency] = sum(abs_errors) / len(abs_errors) if abs_errors else 0.0
        bias_per_competency[competency] = sum(signed_errors) / len(signed_errors) if signed_errors else 0.0

    levels_distance_distribution: dict[int, int] = {}
    for error in total_abs_errors:
        level = round(error / 40)
        levels_distance_distribution[level] = levels_distance_distribution.get(level, 0) + 1

    # Zero Gate recall/precision: sobre TODAS as linhas com nota do motor,
    # independente de normative_status - a pergunta "o motor zerou quando
    # devia, e so quando devia" vale tambem para os casos UNVERIFIED (nao
    # sabemos QUAL hipotese normativa se aplica, mas sabemos que o gabarito
    # diz zero).
    scored_rows = [r for r in rows if r.engine_scores is not None]
    expected_special = [r for r in scored_rows if r.expected_is_special_situation]
    engine_zeroed = [r for r in scored_rows if r.engine_zeroed_whole_essay]
    zero_gate_recall = (
        sum(1 for r in expected_special if r.engine_zeroed_whole_essay) / len(expected_special)
        if expected_special else None
    )
    zero_gate_precision = (
        sum(1 for r in engine_zeroed if r.expected_is_special_situation) / len(engine_zeroed)
        if engine_zeroed else None
    )

    return BenchmarkReport(
        mae_total=mae_total, mae_per_competency=mae_per_competency,
        bias_total=bias_total, bias_per_competency=bias_per_competency,
        levels_distance_distribution=levels_distance_distribution,
        zero_gate_recall=zero_gate_recall, zero_gate_precision=zero_gate_precision,
        needs_review_count=needs_review_count, ocr_duvidoso_rate=ocr_duvidoso_rate,
        excluded_divergent_or_unverified=excluded,
    )
