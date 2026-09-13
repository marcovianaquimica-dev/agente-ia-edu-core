"""Read and structurally validate a rubric source file.

The YAML is the reviewable artifact: it carries the literal cartilha wording and
the page each descriptor came from, and it is reviewed in a pull request against
the official PDF. This loader refuses a file that is structurally incomplete, so
a half-transcribed rubric can never reach the seed.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import yaml

_RUBRIC_DIR = Path(__file__).parent
OFFICIAL_LEVEL_POINTS = (0, 40, 80, 120, 160, 200)
COMPETENCY_CODES = ("C1", "C2", "C3", "C4", "C5")
PROVENANCES = ("OFICIAL_INEP", "INTERPRETACAO_PEDAGOGICA", "HEURISTICA_MOTOR")
SCORING_RULE_EFFECTS = ("ANULA_REDACAO", "ZERA_COMPETENCIA", "LIMITA_PONTUACAO")
# Effects that target a single competency rather than the whole essay, and
# therefore must name which one.
_EFFECTS_REQUIRING_COMPETENCY_CODE = ("ZERA_COMPETENCIA", "LIMITA_PONTUACAO")


class RubricFileError(ValueError):
    """The rubric file is structurally invalid and must not be seeded."""


@dataclass(frozen=True)
class LevelEntry:
    points: int
    descriptor: str
    source_page: int
    provenance: str = "OFICIAL_INEP"


@dataclass(frozen=True)
class SignalEntry:
    key: str
    label: str
    description: str | None
    provenance: str
    source_ref: str | None
    rationale: str | None


@dataclass(frozen=True)
class CompetencyEntry:
    code: str
    ordinal: int
    official_title: str
    source_page: int | None
    levels: tuple[LevelEntry, ...]
    signals: tuple[SignalEntry, ...]


@dataclass(frozen=True)
class ScoringRuleEntry:
    key: str
    label: str
    description: str | None
    effect: str
    competency_code: str | None
    max_points: int | None
    source_page: int | None
    provenance: str


@dataclass(frozen=True)
class RubricFile:
    rubric_version: str
    label: str
    effective_year: int | None
    max_total_points: int
    official_source_title: str | None
    official_source_url: str | None
    official_source_sha256: str | None
    competencies: tuple[CompetencyEntry, ...]
    scoring_rules: tuple[ScoringRuleEntry, ...]


def load_rubric_file(name: str) -> RubricFile:
    """Load ``<name>.yaml`` from this package."""
    path = _RUBRIC_DIR / f"{name}.yaml"
    if not path.exists():
        raise RubricFileError(f"Unknown rubric file {name!r} ({path})")
    return parse_rubric_mapping(yaml.safe_load(path.read_text(encoding="utf-8")))


def parse_rubric_mapping(raw: Any) -> RubricFile:
    if not isinstance(raw, dict):
        raise RubricFileError("Rubric file must be a mapping")

    competencies = tuple(
        _parse_competency(entry) for entry in raw.get("competencies", [])
    )
    codes = [c.code for c in competencies]
    if codes != list(COMPETENCY_CODES):
        raise RubricFileError(
            f"Rubric must declare {list(COMPETENCY_CODES)} in order; got {codes}"
        )

    return RubricFile(
        rubric_version=_required(raw, "rubric_version"),
        label=_required(raw, "label"),
        effective_year=raw.get("effective_year"),
        max_total_points=raw.get("max_total_points", 1000),
        official_source_title=raw.get("official_source_title"),
        official_source_url=raw.get("official_source_url"),
        official_source_sha256=raw.get("official_source_sha256"),
        competencies=competencies,
        scoring_rules=tuple(
            _parse_scoring_rule(entry) for entry in raw.get("scoring_rules", [])
        ),
    )


def _parse_competency(raw: Any) -> CompetencyEntry:
    if not isinstance(raw, dict):
        raise RubricFileError("Each competency must be a mapping")

    levels = tuple(_parse_level(entry) for entry in raw.get("levels", []))
    points = sorted(level.points for level in levels)
    if points != sorted(OFFICIAL_LEVEL_POINTS):
        raise RubricFileError(
            f"Competency {raw.get('code')!r} must declare levels "
            f"{sorted(OFFICIAL_LEVEL_POINTS)}; got {points}"
        )

    return CompetencyEntry(
        code=_required(raw, "code"),
        ordinal=_required(raw, "ordinal"),
        official_title=_required(raw, "official_title"),
        source_page=raw.get("source_page"),
        levels=levels,
        signals=tuple(_parse_signal(entry) for entry in raw.get("signals", [])),
    )


def _parse_level(raw: Any) -> LevelEntry:
    if not isinstance(raw, dict):
        raise RubricFileError("Each level must be a mapping")
    source_page = raw.get("source_page")
    if not isinstance(source_page, int) or source_page <= 0:
        raise RubricFileError(
            f"Level {raw.get('points')!r} must cite a positive source_page"
        )
    provenance = raw.get("provenance", "OFICIAL_INEP")
    if provenance not in PROVENANCES:
        raise RubricFileError(
            f"Level {raw.get('points')!r} declares unknown provenance {provenance!r}"
        )
    return LevelEntry(
        points=_required(raw, "points"),
        descriptor=_required(raw, "descriptor"),
        source_page=source_page,
        provenance=provenance,
    )


def _parse_signal(raw: Any) -> SignalEntry:
    if not isinstance(raw, dict):
        raise RubricFileError("Each signal must be a mapping")
    provenance = _required(raw, "provenance")
    if provenance not in PROVENANCES:
        raise RubricFileError(f"Unknown provenance {provenance!r}")
    source_ref = raw.get("source_ref")
    rationale = raw.get("rationale")
    if provenance == "HEURISTICA_MOTOR" and not rationale:
        raise RubricFileError(
            f"Signal {raw.get('key')!r} is an engine heuristic and must carry a rationale"
        )
    if provenance != "HEURISTICA_MOTOR" and not source_ref:
        raise RubricFileError(
            f"Signal {raw.get('key')!r} claims an external source and must cite it"
        )
    return SignalEntry(
        key=_required(raw, "key"),
        label=_required(raw, "label"),
        description=raw.get("description"),
        provenance=provenance,
        source_ref=source_ref,
        rationale=rationale,
    )


def _parse_scoring_rule(raw: Any) -> ScoringRuleEntry:
    if not isinstance(raw, dict):
        raise RubricFileError("Each scoring rule must be a mapping")
    key = raw.get("key")
    effect = _required(raw, "effect")
    if effect not in SCORING_RULE_EFFECTS:
        raise RubricFileError(f"Scoring rule {key!r} declares unknown effect {effect!r}")

    provenance = _required(raw, "provenance")
    if provenance not in PROVENANCES:
        raise RubricFileError(
            f"Scoring rule {key!r} declares unknown provenance {provenance!r}"
        )

    competency_code = raw.get("competency_code")
    if competency_code is not None and competency_code not in COMPETENCY_CODES:
        raise RubricFileError(
            f"Scoring rule {key!r} declares unknown competency_code {competency_code!r}"
        )
    if effect in _EFFECTS_REQUIRING_COMPETENCY_CODE and competency_code is None:
        raise RubricFileError(
            f"Scoring rule {key!r} has effect {effect!r} and must declare competency_code"
        )

    max_points = raw.get("max_points")
    if effect == "LIMITA_PONTUACAO":
        if max_points is None:
            raise RubricFileError(
                f"Scoring rule {key!r} has effect LIMITA_PONTUACAO and must declare max_points"
            )
        if max_points not in OFFICIAL_LEVEL_POINTS:
            raise RubricFileError(
                f"Scoring rule {key!r} declares max_points {max_points!r} outside "
                f"the official scale {OFFICIAL_LEVEL_POINTS}"
            )
    elif max_points is not None:
        raise RubricFileError(
            f"Scoring rule {key!r} declares max_points but effect is not LIMITA_PONTUACAO"
        )

    return ScoringRuleEntry(
        key=_required(raw, "key"),
        label=_required(raw, "label"),
        description=raw.get("description"),
        effect=effect,
        competency_code=competency_code,
        max_points=max_points,
        source_page=raw.get("source_page"),
        provenance=provenance,
    )


def _required(raw: dict[str, Any], field: str) -> Any:
    if field not in raw or raw[field] in (None, ""):
        raise RubricFileError(f"Missing required field {field!r}")
    return raw[field]


__all__ = [
    "CompetencyEntry",
    "LevelEntry",
    "RubricFile",
    "RubricFileError",
    "SignalEntry",
    "ScoringRuleEntry",
    "load_rubric_file",
    "parse_rubric_mapping",
]
