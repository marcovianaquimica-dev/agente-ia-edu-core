"""Compara BASELINE x APOS QUALITY GATE x APOS ZERO GATE x APOS CALIBRACAO
(spec, 'Relatorio consolidado')."""
import json
from pathlib import Path

_TAGS = ["CALIBRATION_BASELINE_V1", "AFTER_QUALITY_GATE_V1", "AFTER_ZERO_GATE_V1", "AFTER_CALIBRATION_V1"]
_DIR = Path("tests/fixtures/essay_calibration_baselines")


def main() -> None:
    reports = {tag: json.loads((_DIR / f"{tag}.json").read_text(encoding="utf-8"))["report"] for tag in _TAGS}

    print(f"{'Metrica':<30}" + "".join(f"{tag:<28}" for tag in _TAGS))
    for key in ("mae_total", "bias_total", "zero_gate_recall", "zero_gate_precision",
                "needs_review_count", "ocr_duvidoso_rate"):
        print(f"{key:<30}" + "".join(f"{reports[tag].get(key):<28}" for tag in _TAGS))
    print()
    for competency in ("C1", "C2", "C3", "C4", "C5"):
        print(f"MAE {competency:<26}" + "".join(
            f"{reports[tag]['mae_per_competency'].get(competency):<28}" for tag in _TAGS
        ))
    print()
    for competency in ("C1", "C2", "C3", "C4", "C5"):
        print(f"bias {competency:<25}" + "".join(
            f"{reports[tag]['bias_per_competency'].get(competency):<28}" for tag in _TAGS
        ))

    print("\nCasos excluidos por divergencia/nao-verificado (todas as fases):")
    print(reports["AFTER_CALIBRATION_V1"]["excluded_divergent_or_unverified"])


if __name__ == "__main__":
    main()
