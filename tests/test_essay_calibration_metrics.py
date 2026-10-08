from agente_ia_edu.services.essay_calibration_metrics import (
    BenchmarkResultRow, compute_benchmark_report,
)

_COMPETENCIES = ("C1", "C2", "C3", "C4", "C5")


def _valid_row(student_ref, official, engine, ocr_duvidoso=False):
    return BenchmarkResultRow(
        student_ref=student_ref, normative_status="CONFIRMED",
        expected_scores={**dict(zip(_COMPETENCIES, official[:5])), "total": official[5]},
        engine_scores={**dict(zip(_COMPETENCIES, engine[:5])), "total": engine[5]},
        expected_is_special_situation=False, engine_zeroed_whole_essay=(engine[5] == 0),
        engine_status="PENDING_REVIEW", ocr_duvidoso=ocr_duvidoso,
    )


def test_mae_and_bias_are_computed_per_competency_and_total():
    rows = [
        _valid_row("a1", (160, 200, 200, 200, 200, 960), (120, 160, 160, 120, 160, 720)),
        _valid_row("a2", (160, 160, 80, 160, 200, 760), (40, 160, 160, 120, 160, 640)),
    ]
    report = compute_benchmark_report(rows)
    # C1: |160-120|=40, |160-40|=120 -> MAE=80; bias (engine-official): -40,-120 -> -80
    assert report.mae_per_competency["C1"] == 80
    assert report.bias_per_competency["C1"] == -80
    # total: |960-720|=240, |760-640|=120 -> MAE=180
    assert report.mae_total == 180
    assert report.bias_total == -180


def test_bias_sign_distinguishes_over_from_under_scoring():
    rows = [_valid_row("a1", (0, 0, 0, 0, 0, 0), (40, 40, 40, 40, 40, 200))]
    report = compute_benchmark_report(rows)
    assert report.bias_total == 200  # IA pontuou ACIMA do oficial


def test_zero_gate_recall_and_precision():
    special_caught = BenchmarkResultRow(
        student_ref="s1", normative_status="UNVERIFIED", expected_scores=None,
        engine_scores={"C1": 0, "C2": 0, "C3": 0, "C4": 0, "C5": 0, "total": 0},
        expected_is_special_situation=True, engine_zeroed_whole_essay=True,
        engine_status="PENDING_REVIEW", ocr_duvidoso=False,
    )
    special_missed = BenchmarkResultRow(
        student_ref="s2", normative_status="UNVERIFIED", expected_scores=None,
        engine_scores={"C1": 40, "C2": 40, "C3": 40, "C4": 40, "C5": 40, "total": 200},
        expected_is_special_situation=True, engine_zeroed_whole_essay=False,
        engine_status="PENDING_REVIEW", ocr_duvidoso=False,
    )
    valid_false_zero = _valid_row("v1", (160, 160, 160, 160, 160, 800), (0, 0, 0, 0, 0, 0))
    valid_correct = _valid_row("v2", (160, 160, 160, 160, 160, 800), (160, 160, 160, 160, 160, 800))

    report = compute_benchmark_report([special_caught, special_missed, valid_false_zero, valid_correct])
    assert report.zero_gate_recall == 0.5  # 1 de 2 situacoes especiais pegas
    assert report.zero_gate_precision == 0.5  # 1 de 2 zeros do motor sao falso positivo


def test_divergent_and_unverified_rows_never_enter_mae_or_bias():
    confirmed = _valid_row("a1", (160, 160, 160, 160, 160, 800), (160, 160, 160, 160, 160, 800))
    divergent = BenchmarkResultRow(
        student_ref="d1", normative_status="DIVERGENT", expected_scores={"C1": 0, "C2": 0, "C3": 0, "C4": 0, "C5": 0, "total": 0},
        engine_scores={"C1": 80, "C2": 80, "C3": 80, "C4": 80, "C5": 80, "total": 400},
        expected_is_special_situation=True, engine_zeroed_whole_essay=False,
        engine_status="PENDING_REVIEW", ocr_duvidoso=False,
    )
    report = compute_benchmark_report([confirmed, divergent])
    assert report.mae_total == 0  # so a linha CONFIRMED entra, e esta e exata
    assert "d1" in report.excluded_divergent_or_unverified


def test_needs_review_rows_excluded_from_mae_counted_separately():
    confirmed = _valid_row("a1", (160, 160, 160, 160, 160, 800), (160, 160, 160, 160, 160, 800))
    needs_review = BenchmarkResultRow(
        student_ref="r1", normative_status="CONFIRMED",
        expected_scores={"C1": 160, "C2": 160, "C3": 160, "C4": 160, "C5": 160, "total": 800},
        engine_scores=None, expected_is_special_situation=False,
        engine_zeroed_whole_essay=False, engine_status="NEEDS_REVIEW", ocr_duvidoso=True,
    )
    report = compute_benchmark_report([confirmed, needs_review])
    assert report.needs_review_count == 1
    assert report.mae_total == 0  # so a linha com nota real entra
    assert report.ocr_duvidoso_rate == 0.5
