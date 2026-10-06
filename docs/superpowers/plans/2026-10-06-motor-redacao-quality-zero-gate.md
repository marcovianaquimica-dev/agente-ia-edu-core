# Motor de correção ENEM — Quality Gate, Zero Gate e calibração - Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Corrigir, de forma arquitetural e auditável, dois bugs confirmados do motor de correção de redações ENEM (falso zero por confundir "entrada não confiável" com "texto insuficiente do aluno"; Zero Gate que só confirma alertas da fase 1, nunca descobre um caso que ela perdeu) e condicionar qualquer recalibração de C1-C5 a um experimento controlado - nunca a um ajuste improvisado para bater com as 30 notas de um gabarito de teste.

**Architecture:** Quatro fases sequenciais, cada uma com seu próprio ciclo de teste e sua própria rodada de benchmark: (A) benchmark de calibração versionado + baseline congelado do motor atual; (B) Quality Gate - separa tecnicamente confiabilidade de entrada de mérito pedagógico, unificando o sinal já existente de confiança de OCR (foto/PDF) com um sinal novo para texto digitado; (C) Zero Gate - vira um passo de avaliação explícito que roda sempre (não só quando a fase 1 já suspeitou de algo) e produz uma decisão auditável; (D) experimento controlado (texto corrompido vs. limpo) seguido de recalibração pontual, só se o experimento confirmar que a regra (e não o texto) é a causa.

**Tech Stack:** Python 3.13, SQLAlchemy async + Postgres (dev, porta 5433), Pydantic (contrato do motor), pytest + pytest-asyncio, providers de IA já existentes (OpenAI).

**Spec:** [docs/superpowers/specs/2026-10-06-motor-redacao-quality-zero-gate-design.md](../specs/2026-10-06-motor-redacao-quality-zero-gate-design.md)

## Global Constraints

- Nunca usar o mesmo código ou comportamento para "entrada não confiável" e "texto insuficiente do aluno" - são dois conceitos de domínio diferentes (spec, Fase B).
- A heurística determinística de confiabilidade de texto NUNCA decide isolada, NUNCA participa do julgamento de C1 ou qualquer competência, e seu limiar é deliberadamente largo para não reagir a nomes próprios/estrangeirismos/abreviações/erros reais do aluno (spec, Fase B).
- O Zero Gate não pode depender exclusivamente de a fase 1 ter levantado um candidato primeiro - precisa de responsabilidade própria de avaliar situações normativas (spec, Fase C).
- Nenhuma mudança à política de cópia do texto motivador nesta ronda - fica registrada como `normative_divergence` pendente (spec, Fase A/C).
- Nenhuma mudança a `_RULES_TOP_BAND`/teto de `TANGENCIAMENTO_AO_TEMA` antes do experimento controlado da Fase D confirmar que a causa é a regra, não o texto (spec, Fase D).
- Nunca `nota_nova = nota_IA + constante`; nunca regra amarrada a um nome ou redação específica (spec, Fase D).
- Critério de sucesso não é MAE = 0 contra as 30 notas - é reduzir viés sistemático, eliminar falsos zeros, reconhecer situações especiais, produzir decisão auditável, sem overfitting (spec, Fase D).
- Toda mudança de prompt é um `vN.py` novo, nunca uma edição do anterior (convenção já existente no projeto, reafirmada na spec).
- `student_ref` no benchmark é um identificador opaco - nomes reais só no arquivo local não versionado (spec, Fase A).
- Rodar o benchmark completo (30 chamadas reais de IA, sequenciais) é caro e lento - só nos 4 pontos de controle explícitos (fim de cada fase), nunca em todo commit.

---

## Fase A — Benchmark de calibração + baseline congelado

### Task A1: Fixture do benchmark

**Files:**
- Create: `scripts/generate_essay_calibration_fixture.py`
- Create: `tests/fixtures/essay_calibration_benchmark_v1.json`
- Create: `tests/fixtures/essay_calibration_benchmark_v1.local.json` (gitignored)
- Modify: `.gitignore` (adicionar a linha do arquivo `.local.json`)
- Test: `tests/test_essay_calibration_fixture.py`

**Interfaces:**
- Produces: o schema JSON de cada entrada da fixture (consumido pela Task A2/A3):
  ```json
  {
    "student_ref": "aluno_01",
    "body_text": "...",
    "expected_scores": {"C1": 160, "C2": 200, "C3": 200, "C4": 200, "C5": 200, "total": 960},
    "expected_special_situation": null,
    "normative_status": "CONFIRMED",
    "normative_divergence": null,
    "reference_source": "material_30_alunos_treinamento_corretores_enem",
    "reference_version": "2026-10-06"
  }
  ```
  Para os 10 casos de situação especial, `expected_scores` é `{"C1": 0, "C2": 0, "C3": 0, "C4": 0, "C5": 0, "total": 0}`, `expected_special_situation` é um objeto `{"category": "SITUACAO_ESPECIAL_NAO_ESPECIFICADA", "evidence_note": "..."}` e `normative_status` é `"UNVERIFIED"` (o gabarito não especifica qual das 5 hipóteses se aplica).

- [ ] **Step 1: Escrever o script gerador**

O texto de cada redação e a referência oficial já foram levantados nesta investigação. `scripts/generate_essay_calibration_fixture.py`:

```python
"""Gera a fixture versionada do benchmark de calibracao (30 redacoes reais +
referencia oficial) a partir dos dados ja levantados na investigacao de
2026-10-06. Roda uma vez; o resultado e committado - nao precisa rodar de
novo a menos que o conjunto de referencia mude."""
from __future__ import annotations

import json
from pathlib import Path

# (nome_real, texto_corpo) - extraido do PDF original nesta investigacao.
_BODIES: list[tuple[str, str]] = [
    ("Aluno 01", "..."),
    # ... as 30 entradas, na mesma ordem do PDF original.
]

# (C1, C2, C3, C4, C5, total) oficiais, mesma ordem de _BODIES.
_OFFICIAL: list[tuple[int, int, int, int, int, int]] = [
    (160, 200, 200, 200, 200, 960),
    # ...
]

# indices (0-based) das 10 redacoes de situacao especial, na ordem de _BODIES.
_SPECIAL_SITUATION_INDICES = frozenset(range(20, 30))

_REFERENCE_SOURCE = "material_30_alunos_treinamento_corretores_enem"
_REFERENCE_VERSION = "2026-10-06"


def build_fixture() -> tuple[list[dict], dict[str, str]]:
    public_entries = []
    name_map = {}
    for index, ((name, body), official) in enumerate(zip(_BODIES, _OFFICIAL, strict=True)):
        student_ref = f"aluno_{index + 1:02d}"
        name_map[student_ref] = name
        c1, c2, c3, c4, c5, total = official
        is_special = index in _SPECIAL_SITUATION_INDICES
        entry = {
            "student_ref": student_ref,
            "body_text": body,
            "expected_scores": {"C1": c1, "C2": c2, "C3": c3, "C4": c4, "C5": c5, "total": total},
            "expected_special_situation": (
                {"category": "SITUACAO_ESPECIAL_NAO_ESPECIFICADA",
                 "evidence_note": "gabarito marca zero sem especificar a hipotese "
                                   "exata (copia/fuga/anulacao/tipo textual/parte "
                                   "desconectada)"}
                if is_special else None
            ),
            "normative_status": "UNVERIFIED" if is_special else "CONFIRMED",
            "normative_divergence": None,
            "reference_source": _REFERENCE_SOURCE,
            "reference_version": _REFERENCE_VERSION,
        }
        public_entries.append(entry)
    return public_entries, name_map


def main() -> None:
    public_entries, name_map = build_fixture()
    fixtures_dir = Path(__file__).resolve().parent.parent / "tests" / "fixtures"
    (fixtures_dir / "essay_calibration_benchmark_v1.json").write_text(
        json.dumps(public_entries, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    (fixtures_dir / "essay_calibration_benchmark_v1.local.json").write_text(
        json.dumps(name_map, ensure_ascii=False, indent=2), encoding="utf-8",
    )
    print(f"Gravadas {len(public_entries)} entradas.")


if __name__ == "__main__":
    main()
```

Preencher `_BODIES` com o texto real das 30 redações (já extraído e salvo em
`redacoes_30.json` nesta investigação - copiar os 30 `body`, na mesma ordem
do PDF original (30 redações, aluno_01 a aluno_30) e `_OFFICIAL` com as 30 tuplas de nota
oficial (já tabuladas nesta investigação, mesma ordem).

- [ ] **Step 2: Rodar o script e conferir a saída**

```bash
python scripts/generate_essay_calibration_fixture.py
```

Esperado: `Gravadas 30 entradas.` e os dois arquivos JSON criados em
`tests/fixtures/`.

- [ ] **Step 3: Adicionar o arquivo local ao `.gitignore`**

```
# Benchmark de calibracao de redacao - nomes reais, nunca versionados
tests/fixtures/essay_calibration_benchmark_v1.local.json
```

- [ ] **Step 4: Escrever o teste da fixture**

```python
# tests/test_essay_calibration_fixture.py
import json
from pathlib import Path

_FIXTURE = Path(__file__).parent / "fixtures" / "essay_calibration_benchmark_v1.json"


def _load():
    return json.loads(_FIXTURE.read_text(encoding="utf-8"))


def test_fixture_has_thirty_entries():
    entries = _load()
    assert len(entries) == 30


def test_every_entry_has_required_fields():
    for entry in _load():
        for field in (
            "student_ref", "body_text", "expected_scores", "expected_special_situation",
            "normative_status", "normative_divergence", "reference_source", "reference_version",
        ):
            assert field in entry, f"{entry.get('student_ref')} missing {field}"


def test_student_refs_are_unique_and_opaque():
    refs = [entry["student_ref"] for entry in _load()]
    assert len(refs) == len(set(refs))
    assert all(ref.startswith("aluno_") for ref in refs)


def test_ten_entries_are_special_situation_unverified():
    entries = _load()
    special = [e for e in entries if e["expected_special_situation"] is not None]
    assert len(special) == 10
    assert all(e["normative_status"] == "UNVERIFIED" for e in special)
    assert all(e["expected_scores"]["total"] == 0 for e in special)


def test_twenty_entries_are_confirmed_non_zero():
    entries = _load()
    confirmed = [e for e in entries if e["normative_status"] == "CONFIRMED"]
    assert len(confirmed) == 20
    assert all(e["expected_special_situation"] is None for e in confirmed)
```

- [ ] **Step 5: Rodar os testes**

Run: `pytest tests/test_essay_calibration_fixture.py -v`
Expected: 5 passed.

- [ ] **Step 6: Commit**

```bash
git add scripts/generate_essay_calibration_fixture.py \
        tests/fixtures/essay_calibration_benchmark_v1.json \
        tests/test_essay_calibration_fixture.py .gitignore
git commit -m "test(redacao): fixture versionada do benchmark de calibracao (30 redacoes reais)"
```

(`essay_calibration_benchmark_v1.local.json` não é commitado - está no `.gitignore`.)

---

### Task A2: Módulo de métricas do benchmark (puro, sem DB/IA)

**Files:**
- Create: `src/agente_ia_edu/services/essay_calibration_metrics.py`
- Test: `tests/test_essay_calibration_metrics.py`

**Interfaces:**
- Consumes: nada de tasks anteriores (módulo independente).
- Produces (consumido pela Task A3):
  ```python
  @dataclass(frozen=True)
  class BenchmarkResultRow:
      student_ref: str
      normative_status: str  # "CONFIRMED" | "DIVERGENT" | "UNVERIFIED"
      expected_scores: dict[str, int] | None  # None se expected_special_situation existir
      engine_scores: dict[str, int] | None     # None se a correcao nao produziu nota (NEEDS_REVIEW)
      expected_is_special_situation: bool
      engine_zeroed_whole_essay: bool
      engine_status: str      # EssayCorrection.status
      ocr_duvidoso: bool

  @dataclass(frozen=True)
  class BenchmarkReport:
      mae_total: float
      mae_per_competency: dict[str, float]
      bias_total: float
      bias_per_competency: dict[str, float]
      levels_distance_distribution: dict[int, int]
      zero_gate_recall: float
      zero_gate_precision: float
      needs_review_count: int
      ocr_duvidoso_rate: float
      excluded_divergent_or_unverified: list[str]

  def compute_benchmark_report(rows: list[BenchmarkResultRow]) -> BenchmarkReport: ...
  ```

- [ ] **Step 1: Escrever os testes com casos sintéticos conhecidos**

```python
# tests/test_essay_calibration_metrics.py
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
```

- [ ] **Step 2: Rodar e confirmar que falha (módulo não existe)**

Run: `pytest tests/test_essay_calibration_metrics.py -v`
Expected: FAIL com `ModuleNotFoundError`.

- [ ] **Step 3: Implementar o módulo**

```python
# src/agente_ia_edu/services/essay_calibration_metrics.py
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
```

- [ ] **Step 4: Rodar os testes**

Run: `pytest tests/test_essay_calibration_metrics.py -v`
Expected: 6 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/essay_calibration_metrics.py tests/test_essay_calibration_metrics.py
git commit -m "feat(redacao): metricas puras do benchmark de calibracao (MAE, bias, Zero Gate recall/precision)"
```

---

### Task A3: Script de materialização + execução do benchmark

**Files:**
- Create: `src/agente_ia_edu/services/essay_calibration_runner.py`
- Create: `scripts/essay_calibration_benchmark.py`
- Test: `tests/test_essay_calibration_runner.py`

**Interfaces:**
- Consumes: `BenchmarkResultRow`/`BenchmarkReport`/`compute_benchmark_report` (Task A2); schema da fixture (Task A1).
- Produces:
  ```python
  async def materialize_benchmark_submissions(
      session: AsyncSession, *, fixture_entries: list[dict], run_tag: str,
  ) -> list[tuple[str, uuid.UUID]]:
      """Cria escola/turma/proposta descartaveis com nome unico (run_tag embutido)
      e devolve [(student_ref, essay_submission_id), ...] na ordem da fixture."""

  async def run_benchmark_corrections(
      session_factory, *, submissions: list[tuple[str, uuid.UUID]],
      correction_service_factory=None,
  ) -> list[tuple[str, "EssayCorrection | None", str | None]]:
      """Roda a correcao (real, via EssayCorrectionService.correct, ou um
      factory injetado para teste) sequencialmente. Devolve
      [(student_ref, correction_ou_None, status_da_submission), ...]."""
  ```

- [ ] **Step 1: Escrever o teste com um `correction_service_factory` falso**

Reaproveita o padrão de transcritor falso já usado em
`tests/test_r4_essay_batch_processing.py` - aqui, uma correção falsa em vez
de um transcritor falso, para testar a materialização e a orquestração sem
custo real de IA.

```python
# tests/test_essay_calibration_runner.py
import uuid

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.services.essay_calibration_runner import (
    materialize_benchmark_submissions, run_benchmark_corrections,
)


class _FakeCorrection:
    def __init__(self, status: str, final_scores=None):
        self.status = status
        self.final_scores = final_scores
        self.ai_output = {"alerts": []}
        self.failure_reason = None


class _FakeCorrectionService:
    """Substitui EssayCorrectionService.correct - nunca chama IA real."""

    def __init__(self, session):
        self.session = session

    async def correct(self, submission_id):
        return _FakeCorrection(
            status="PENDING_REVIEW",
            final_scores={"total": 200, "per_competency": {c: {"points": 40} for c in ("C1", "C2", "C3", "C4", "C5")}},
        )


_FIXTURE = [
    {"student_ref": "aluno_01", "body_text": "Texto da redacao um, com conteudo suficiente para submissao.",
     "expected_scores": {"C1": 160, "C2": 160, "C3": 160, "C4": 160, "C5": 160, "total": 800},
     "expected_special_situation": None, "normative_status": "CONFIRMED", "normative_divergence": None,
     "reference_source": "teste", "reference_version": "2026-10-06"},
    {"student_ref": "aluno_02", "body_text": "Texto da redacao dois, tambem com conteudo suficiente.",
     "expected_scores": {"C1": 0, "C2": 0, "C3": 0, "C4": 0, "C5": 0, "total": 0},
     "expected_special_situation": {"category": "SITUACAO_ESPECIAL_NAO_ESPECIFICADA", "evidence_note": "teste"},
     "normative_status": "UNVERIFIED", "normative_divergence": None,
     "reference_source": "teste", "reference_version": "2026-10-06"},
]


@pytest.mark.asyncio
async def test_materialize_creates_one_submission_per_fixture_entry(seed_session):
    submissions = await materialize_benchmark_submissions(
        seed_session, fixture_entries=_FIXTURE, run_tag="test-run-001",
    )
    assert len(submissions) == 2
    refs = [ref for ref, _ in submissions]
    assert refs == ["aluno_01", "aluno_02"]
    for _, submission_id in submissions:
        assert isinstance(submission_id, uuid.UUID)


@pytest.mark.asyncio
async def test_materialize_is_isolated_per_run_tag(seed_session):
    first = await materialize_benchmark_submissions(
        seed_session, fixture_entries=_FIXTURE[:1], run_tag="test-run-A",
    )
    second = await materialize_benchmark_submissions(
        seed_session, fixture_entries=_FIXTURE[:1], run_tag="test-run-B",
    )
    assert first[0][1] != second[0][1]  # submissoes diferentes, nunca reaproveita escola/turma


@pytest.mark.asyncio
async def test_run_benchmark_corrections_calls_injected_factory(seed_session, session_factory):
    submissions = await materialize_benchmark_submissions(
        seed_session, fixture_entries=_FIXTURE, run_tag="test-run-002",
    )
    await seed_session.commit()

    results = await run_benchmark_corrections(
        session_factory, submissions=submissions,
        correction_service_factory=_FakeCorrectionService,
    )
    assert len(results) == 2
    for student_ref, correction, _status in results:
        assert correction is not None
        assert correction.status == "PENDING_REVIEW"
```

Checar em `tests/conftest.py` (ou equivalente) quais fixtures `seed_session`/
`session_factory` já existem no projeto para teste assíncrono com Postgres -
reaproveitar as mesmas usadas em `test_r4_essay_batch_processing.py`, nunca
criar um esquema de fixture de teste novo.

- [ ] **Step 2: Rodar e confirmar que falha**

Run: `pytest tests/test_essay_calibration_runner.py -v`
Expected: FAIL com `ModuleNotFoundError`.

- [ ] **Step 3: Implementar `materialize_benchmark_submissions`**

Mesmo padrão já usado manualmente nesta investigação (ver
`EssayBatchService._materialize_run` como referência de estilo, e os campos
exatos de `Class`/`EssayPrompt`/`PromptAssignment`/`Person`/`Student`/
`StudentEnrollment`/`EssaySubmission` em `db/models/academic.py` e
`db/models/essay_proposal.py`):

```python
# src/agente_ia_edu/services/essay_calibration_runner.py
"""Materializa e corrige o benchmark de calibracao (tests/fixtures/
essay_calibration_benchmark_v1.json) contra um motor real, isolado numa
escola/turma/proposta descartaveis por execucao - nunca reaproveita dados
de outra sessao/teste manual no mesmo banco de desenvolvimento."""
from __future__ import annotations

import unicodedata
import uuid
from datetime import datetime, timezone

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..db.models.academic import Class, Person, Student, StudentEnrollment
from ..db.models.essay_proposal import EssayPrompt, EssaySubmission, PromptAssignment
from .essay_correction import EssayCorrectionService
from .essay_correction_key import essay_text_hash, normalize_essay_text

# Mesma escola/ano/serie usados nos testes manuais desta investigacao -
# reaproveitados so como ANCORA de escola existente (precisamos de um
# school_id/academic_year_id/grade_level_id validos); a turma/proposta em si
# sao sempre novas e isoladas por run_tag.
_ANCHOR_SCHOOL_ID = uuid.UUID("a8c1e3d0-5f6b-4a7e-8c9d-1234567890ab")
_ANCHOR_ACADEMIC_YEAR_ID = uuid.UUID("dac2049d-0687-4ff1-b2f5-57380843263f")
_ANCHOR_GRADE_LEVEL_ID = uuid.UUID("2b989afc-5b33-4a5f-b409-0ed0fa21add4")
_TEACHER_IDENTITY = "benchmark_runner"

_BENCHMARK_STATEMENT = (
    "A partir da leitura dos textos motivadores e com base nos conhecimentos "
    "construidos ao longo de sua formacao, redija um texto dissertativo-"
    "argumentativo em modalidade escrita formal da lingua portuguesa sobre o "
    "tema 'Desafios para a valorizacao da pessoa idosa e o enfrentamento do "
    "preconceito etario no Brasil', apresentando proposta de intervencao que "
    "respeite os direitos humanos."
)


def _slug(text: str) -> str:
    normalized = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    return "_".join(normalized.lower().split())


async def materialize_benchmark_submissions(
    session: AsyncSession, *, fixture_entries: list[dict], run_tag: str,
) -> list[tuple[str, uuid.UUID]]:
    now = datetime.now(timezone.utc)
    klass = Class(
        id=uuid.uuid4(), school_id=_ANCHOR_SCHOOL_ID, academic_year_id=_ANCHOR_ACADEMIC_YEAR_ID,
        grade_level_id=_ANCHOR_GRADE_LEVEL_ID, name=f"Benchmark calibracao - {run_tag}",
    )
    session.add(klass)
    await session.flush()

    prompt = EssayPrompt(
        id=uuid.uuid4(), school_id=_ANCHOR_SCHOOL_ID,
        title=f"Benchmark calibracao - {run_tag}", statement=_BENCHMARK_STATEMENT,
        year=now.year, status="ACTIVE", is_free_theme=False,
        created_by_external_identity=_TEACHER_IDENTITY,
    )
    session.add(prompt)
    await session.flush()

    assignment = PromptAssignment(
        id=uuid.uuid4(), school_id=_ANCHOR_SCHOOL_ID, essay_prompt_id=prompt.id,
        class_id=klass.id, assigned_by_external_identity=_TEACHER_IDENTITY,
        validation_enabled=True, status="OPEN",
    )
    session.add(assignment)
    await session.flush()

    results: list[tuple[str, uuid.UUID]] = []
    for entry in fixture_entries:
        student_ref = entry["student_ref"]
        slug = f"{_slug(student_ref)}_{run_tag}"

        person = Person(id=uuid.uuid4(), school_id=_ANCHOR_SCHOOL_ID, external_id=slug, full_name=student_ref)
        session.add(person)
        await session.flush()

        student = Student(id=uuid.uuid4(), school_id=_ANCHOR_SCHOOL_ID, person_id=person.id, external_id=slug)
        session.add(student)
        await session.flush()

        session.add(StudentEnrollment(
            id=uuid.uuid4(), school_id=_ANCHOR_SCHOOL_ID, student_id=student.id,
            class_id=klass.id, enrolled_on=now.date(),
        ))

        canonical = normalize_essay_text(entry["body_text"])
        submission = EssaySubmission(
            id=uuid.uuid4(), essay_id=uuid.uuid4(), school_id=_ANCHOR_SCHOOL_ID,
            prompt_assignment_id=assignment.id, student_id=student.id,
            mode="TYPED", anchor_mode="TEXT_OFFSET", status="SUBMITTED",
            canonical_text=canonical, normalized_text_hash=essay_text_hash(canonical),
            submitted_at=now,
        )
        session.add(submission)
        await session.flush()

        results.append((student_ref, submission.id))

    return results


async def run_benchmark_corrections(
    session_factory: async_sessionmaker, *, submissions: list[tuple[str, uuid.UUID]],
    correction_service_factory=EssayCorrectionService,
):
    results = []
    for student_ref, submission_id in submissions:
        async with session_factory() as session:
            service = correction_service_factory(session)
            try:
                correction = await service.correct(submission_id)
                # Capturar status ANTES do commit - SQLAlchemy expira
                # atributos no commit, e um lazy-load fora de um contexto
                # awaited levanta MissingGreenlet (confirmado nesta
                # investigacao, rodando o script de correcao manual).
                status = correction.status
                await session.commit()
                results.append((student_ref, correction, status))
            except Exception as exc:  # noqa: BLE001 - registra e segue o lote
                results.append((student_ref, None, f"ERROR: {exc}"))
    return results
```

- [ ] **Step 4: Rodar os testes**

Run: `pytest tests/test_essay_calibration_runner.py -v`
Expected: 3 passed.

- [ ] **Step 5: Escrever o script CLI**

```python
# scripts/essay_calibration_benchmark.py
"""Roda o benchmark de calibracao completo (30 chamadas REAIS de IA,
sequenciais - caro e lento, so nos pontos de controle de cada fase).

Uso: DATABASE_URL=... python scripts/essay_calibration_benchmark.py --tag BASELINE_V1
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
import uuid
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agente_ia_edu.db.session import create_engine, create_session_factory
from agente_ia_edu.services.essay_calibration_metrics import (
    BenchmarkResultRow, compute_benchmark_report,
)
from agente_ia_edu.services.essay_calibration_runner import (
    materialize_benchmark_submissions, run_benchmark_corrections,
)

_FIXTURE_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "essay_calibration_benchmark_v1.json"


def _row_from_result(entry: dict, correction, status: str) -> BenchmarkResultRow:
    expected_scores = None if entry["expected_special_situation"] else entry["expected_scores"]
    engine_scores = None
    engine_zeroed = False
    ocr_duvidoso = False
    if correction is not None and correction.final_scores is not None:
        per_competency = correction.final_scores.get("per_competency") or {}
        engine_scores = {
            code: (per_competency.get(code) or {}).get("points") for code in ("C1", "C2", "C3", "C4", "C5")
        }
        engine_scores["total"] = correction.final_scores.get("total")
        engine_zeroed = engine_scores["total"] == 0
    if correction is not None and correction.ai_output:
        ocr_duvidoso = any(a.get("code") == "OCR_DUVIDOSO" for a in (correction.ai_output.get("alerts") or []))
    return BenchmarkResultRow(
        student_ref=entry["student_ref"], normative_status=entry["normative_status"],
        expected_scores=expected_scores, engine_scores=engine_scores,
        expected_is_special_situation=entry["expected_special_situation"] is not None,
        engine_zeroed_whole_essay=engine_zeroed, engine_status=status, ocr_duvidoso=ocr_duvidoso,
    )


async def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True, help="identificador unico desta execucao, ex. BASELINE_V1")
    parser.add_argument("--out", default=None, help="caminho do relatorio JSON de saida")
    args = parser.parse_args()

    fixture_entries = json.loads(_FIXTURE_PATH.read_text(encoding="utf-8"))
    engine = create_engine()
    session_factory = create_session_factory(engine)

    async with session_factory() as session:
        submissions = await materialize_benchmark_submissions(
            session, fixture_entries=fixture_entries, run_tag=args.tag,
        )
        await session.commit()
    print(f"[{args.tag}] {len(submissions)} submissoes materializadas.", flush=True)

    results = await run_benchmark_corrections(session_factory, submissions=submissions)
    entries_by_ref = {e["student_ref"]: e for e in fixture_entries}
    rows = [_row_from_result(entries_by_ref[ref], correction, status) for ref, correction, status in results]
    report = compute_benchmark_report(rows)

    out_path = Path(args.out) if args.out else Path(f"tests/fixtures/essay_calibration_baselines/{args.tag}.json")
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps({
        "tag": args.tag, "generated_at": datetime.now(timezone.utc).isoformat(),
        "report": {
            "mae_total": report.mae_total, "mae_per_competency": report.mae_per_competency,
            "bias_total": report.bias_total, "bias_per_competency": report.bias_per_competency,
            "levels_distance_distribution": report.levels_distance_distribution,
            "zero_gate_recall": report.zero_gate_recall, "zero_gate_precision": report.zero_gate_precision,
            "needs_review_count": report.needs_review_count, "ocr_duvidoso_rate": report.ocr_duvidoso_rate,
            "excluded_divergent_or_unverified": report.excluded_divergent_or_unverified,
        },
        "rows": [
            {"student_ref": r.student_ref, "normative_status": r.normative_status,
             "expected_scores": r.expected_scores, "engine_scores": r.engine_scores,
             "engine_status": r.engine_status, "ocr_duvidoso": r.ocr_duvidoso}
            for r in rows
        ],
    }, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"Relatorio gravado em {out_path}")
    print(json.dumps({
        "mae_total": report.mae_total, "bias_total": report.bias_total,
        "zero_gate_recall": report.zero_gate_recall, "zero_gate_precision": report.zero_gate_precision,
        "needs_review_count": report.needs_review_count, "ocr_duvidoso_rate": report.ocr_duvidoso_rate,
    }, indent=2))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/essay_calibration_runner.py \
        scripts/essay_calibration_benchmark.py tests/test_essay_calibration_runner.py
git commit -m "feat(redacao): runner do benchmark de calibracao (materializacao isolada + execucao real)"
```

---

### Task A4: Captura do baseline congelado

**Files:**
- Create: `tests/fixtures/essay_calibration_baselines/CALIBRATION_BASELINE_V1.json` (gerado, committado)

**Interfaces:**
- Consumes: `scripts/essay_calibration_benchmark.py` (Task A3), `DATABASE_URL` já configurado no `.env` do worktree.

- [ ] **Step 1: Rodar o benchmark contra o motor ATUAL (antes de qualquer mudança das Fases B/C/D)**

```bash
cd /caminho/do/worktree
set -a && source .env && set +a
export DATABASE_URL="postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/agente_ia_edu"
python scripts/essay_calibration_benchmark.py --tag CALIBRATION_BASELINE_V1
```

Isso faz 30 chamadas reais de IA, sequenciais - leva de 30 a 45 minutos.
Deixar rodar até o fim antes do próximo passo.

- [ ] **Step 2: Confirmar o formato e os números contra o que já sabemos**

O relatório deve mostrar aproximadamente: `mae_total` perto de 207,
`zero_gate_recall` perto de 0.1 (1 de 10), `ocr_duvidoso_rate` perto de 0.93
(28/30) - estes números já são conhecidos desta investigação; servem só
para confirmar que o script reproduz o comportamento já medido, não para
decidir nada novo.

- [ ] **Step 3: Commit do baseline**

```bash
git add tests/fixtures/essay_calibration_baselines/CALIBRATION_BASELINE_V1.json
git commit -m "chore(redacao): baseline congelado CALIBRATION_BASELINE_V1 (motor antes do Quality/Zero Gate)"
```

Este arquivo nunca é regenerado automaticamente depois disso - é a
fotografia do "antes", usada nos relatórios comparativos das Fases B/C/D.

---

## Fase B — Quality Gate

### Task B1: Heurística determinística de confiabilidade de texto

**Files:**
- Create: `src/agente_ia_edu/data/pt_common_words.txt`
- Create: `src/agente_ia_edu/services/essay_input_reliability.py`
- Modify: `pyproject.toml` (registrar `pt_common_words.txt` como package-data, mesmo padrão já usado para `agente_ia_edu.rubrics` → `*.yaml`)
- Test: `tests/test_essay_input_reliability.py`

**Interfaces:**
- Produces (consumido pela Task B4):
  ```python
  def estimate_text_reliability_heuristic(text: str) -> float: ...
  # 0.0-1.0, proporcao de tokens reconheciveis como portugues comum.

  def combine_input_reliability_signals(
      *, heuristic_ratio: float | None, model_status: str,
      ocr_average_confidence: float | None,
  ) -> str: ...
  # "RELIABLE" | "USABLE_WITH_WARNING" | "UNRELIABLE_NEEDS_REVIEW"
  ```

- [ ] **Step 1: Criar a lista de palavras comuns**

`src/agente_ia_edu/data/pt_common_words.txt`: uma palavra por linha, minúsculas,
sem acentuação normalizada à parte (a comparação normaliza no código, não no
arquivo) - as ~2000 palavras mais comuns do português (artigos, preposições,
conjunções, verbos comuns, substantivos comuns, incluindo vocabulário
temático esperado em redações ENEM: "sociedade", "brasil", "governo",
"população", "direitos", "idoso", "jovem", etc). Não é exaustiva por design -
é só o suficiente para distinguir texto real de corrupção grosseira.

- [ ] **Step 2: Escrever os testes**

```python
# tests/test_essay_input_reliability.py
from agente_ia_edu.services.essay_input_reliability import (
    combine_input_reliability_signals, estimate_text_reliability_heuristic,
)

_CLEAN_TEXT = (
    "A sociedade brasileira enfrenta desafios para valorizar a pessoa idosa "
    "e combater o preconceito etario. O governo deve criar politicas publicas "
    "que garantam direitos e dignidade aos idosos."
)

# Texto SINTETICO de teste (nenhuma correspondencia com nenhuma redacao real).
_GARBLED_TEXT = (
    "cumprin seu papeu comu garamtisdor dos dineitos fumdamentais, "
    "perpetuamdo desigualdades imto - | numa sociedade onde o ideal "
    "demucratico proposto pur. Bolo."
)


def test_clean_text_scores_high_reliability():
    ratio = estimate_text_reliability_heuristic(_CLEAN_TEXT)
    assert ratio > 0.7


def test_garbled_text_scores_low_reliability():
    ratio = estimate_text_reliability_heuristic(_GARBLED_TEXT)
    assert ratio < 0.4


def test_proper_nouns_and_foreign_words_do_not_tank_the_score():
    text = (
        "Carlos Eduardo Pereira Lima discutiu o tema com Beatriz Fernanda Souza em "
        "Sao Paulo, citando o conceito de welfare state e o relatorio da UNESCO "
        "sobre o envelhecimento da populacao brasileira."
    )
    ratio = estimate_text_reliability_heuristic(text)
    assert ratio > 0.5  # nomes proprios/estrangeirismos nao devem reprovar isso


def test_combine_trusts_model_unreliable_report_directly():
    status = combine_input_reliability_signals(
        heuristic_ratio=0.9, model_status="UNRELIABLE_NEEDS_REVIEW", ocr_average_confidence=None,
    )
    assert status == "UNRELIABLE_NEEDS_REVIEW"


def test_combine_never_escalates_past_warning_from_heuristic_alone():
    status = combine_input_reliability_signals(
        heuristic_ratio=0.1, model_status="RELIABLE", ocr_average_confidence=None,
    )
    assert status == "USABLE_WITH_WARNING"  # discordancia nunca forca UNRELIABLE isolada


def test_combine_agrees_on_reliable():
    status = combine_input_reliability_signals(
        heuristic_ratio=0.9, model_status="RELIABLE", ocr_average_confidence=0.95,
    )
    assert status == "RELIABLE"


def test_combine_model_warning_is_never_downgraded_by_good_technical_signals():
    status = combine_input_reliability_signals(
        heuristic_ratio=0.95, model_status="USABLE_WITH_WARNING", ocr_average_confidence=0.95,
    )
    assert status == "USABLE_WITH_WARNING"
```

- [ ] **Step 3: Rodar e confirmar que falha**

Run: `pytest tests/test_essay_input_reliability.py -v`
Expected: FAIL com `ModuleNotFoundError`.

- [ ] **Step 4: Implementar**

```python
# src/agente_ia_edu/services/essay_input_reliability.py
"""Sinal de confiabilidade de ENTRADA - deliberadamente separado de
julgamento pedagogico (spec Fase B). Nunca decide isolado: ver
combine_input_reliability_signals."""
from __future__ import annotations

import re
import unicodedata
from functools import lru_cache
from importlib import resources

# Larga o suficiente para nao reagir a nomes proprios, estrangeirismos,
# abreviacoes e erros ortograficos reais do aluno - so pega corrupcao
# grosseira (spec Fase B, Global Constraints).
_HEURISTIC_UNRELIABLE_THRESHOLD = 0.4
_OCR_UNRELIABLE_THRESHOLD = 0.6  # mesmo piso de essay_submission.py::_MIN_AVERAGE_CONFIDENCE

_TOKEN_PATTERN = re.compile(r"[a-zA-ZàáâãéêíóôõúçÀÁÂÃÉÊÍÓÔÕÚÇ]+")


def _normalize(word: str) -> str:
    decomposed = unicodedata.normalize("NFKD", word.lower())
    return "".join(c for c in decomposed if not unicodedata.combining(c))


@lru_cache(maxsize=1)
def _common_words() -> frozenset[str]:
    text = resources.files("agente_ia_edu.data").joinpath("pt_common_words.txt").read_text(encoding="utf-8")
    return frozenset(_normalize(line.strip()) for line in text.splitlines() if line.strip())


def estimate_text_reliability_heuristic(text: str) -> float:
    """Proporcao de tokens reconheciveis como portugues comum - UM sinal de
    entrada entre varios, nunca usado isolado (ver combine_input_reliability_
    signals) e nunca lido pelo julgamento de C1 ou qualquer competencia."""
    tokens = _TOKEN_PATTERN.findall(text)
    if not tokens:
        return 1.0  # sem tokens alfabeticos (ex. so numeros/pontuacao) - nao e questao desta heuristica
    words = _common_words()
    recognizable = sum(1 for token in tokens if _normalize(token) in words)
    return recognizable / len(tokens)


def combine_input_reliability_signals(
    *, heuristic_ratio: float | None, model_status: str, ocr_average_confidence: float | None,
) -> str:
    """Combina os sinais disponiveis - nunca decide por uma fonte isolada
    (spec Fase B). model_status vem da autoavaliacao do modelo na mesma
    chamada de fase 1 (campo input_reliability do contrato v6); e a UNICA
    fonte que pode, por si so, levar a UNRELIABLE_NEEDS_REVIEW - um sinal
    tecnico discordando de um modelo confiante so rebaixa RELIABLE para
    USABLE_WITH_WARNING, nunca forca o nivel mais severo isolado."""
    if model_status == "UNRELIABLE_NEEDS_REVIEW":
        return "UNRELIABLE_NEEDS_REVIEW"
    if model_status == "USABLE_WITH_WARNING":
        return "USABLE_WITH_WARNING"

    technical_signal_bad = (
        (heuristic_ratio is not None and heuristic_ratio < _HEURISTIC_UNRELIABLE_THRESHOLD)
        or (ocr_average_confidence is not None and ocr_average_confidence < _OCR_UNRELIABLE_THRESHOLD)
    )
    return "USABLE_WITH_WARNING" if technical_signal_bad else "RELIABLE"
```

- [ ] **Step 5: Registrar o arquivo de dados em `pyproject.toml`**

```toml
[tool.setuptools.package-data]
"agente_ia_edu.rubrics" = ["*.yaml"]
"agente_ia_edu.data" = ["*.txt"]
```

Criar `src/agente_ia_edu/data/__init__.py` vazio se o pacote ainda não existir.

- [ ] **Step 6: Rodar os testes**

Run: `pytest tests/test_essay_input_reliability.py -v`
Expected: 6 passed.

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/data/ src/agente_ia_edu/services/essay_input_reliability.py \
        tests/test_essay_input_reliability.py pyproject.toml
git commit -m "feat(redacao): heuristica deterministica + combinacao de sinais de confiabilidade de entrada"
```

---

### Task B2: Contrato v6 — campo `input_reliability`

**Files:**
- Create: `src/agente_ia_edu/essay_engine_contract/v6.py`
- Test: `tests/test_essay_engine_contract_v6.py`

**Interfaces:**
- Consumes: nada de código novo (copia `v5.py` integralmente, só soma).
- Produces (consumido pela Task B3/B4):
  ```python
  class InputReliability(BaseModel):
      status: Literal["RELIABLE", "USABLE_WITH_WARNING", "UNRELIABLE_NEEDS_REVIEW"]
      rationale: str  # min_length=1

  class EssayEngineOutput(BaseModel):
      # ... todos os campos de v5, MAIS:
      input_reliability: InputReliability
  CONTRACT_VERSION = "essay_engine_output_v6"
  ```

- [ ] **Step 1: Copiar `v5.py` para `v6.py`**

```bash
cp src/agente_ia_edu/essay_engine_contract/v5.py src/agente_ia_edu/essay_engine_contract/v6.py
```

- [ ] **Step 2: Editar `v6.py`**

Trocar `CONTRACT_VERSION = "essay_engine_output_v5"` por
`CONTRACT_VERSION = "essay_engine_output_v6"`. Adicionar, antes da classe
`class EssayEngineOutput(BaseModel):`:

```python
class InputReliability(BaseModel):
    """Separa tecnicamente confiabilidade de ENTRADA de merito pedagogico
    (spec Fase B - 'falha ou baixa confiabilidade da entrada nao e
    deficiencia do aluno'). Nunca usar Alert/alerts para isto - Alert e
    julgamento pedagogico sobre o conteudo, este e julgamento sobre a
    LEITURA do texto, uma pergunta anterior e de natureza diferente."""

    model_config = _Strict

    status: Literal["RELIABLE", "USABLE_WITH_WARNING", "UNRELIABLE_NEEDS_REVIEW"]
    rationale: str = Field(min_length=1)
```

Adicionar o campo na classe `EssayEngineOutput` (logo depois de
`identification: Identification`):

```python
    identification: Identification
    input_reliability: InputReliability
    scores: Scores | None = None
```

Adicionar `"InputReliability"` ao `__all__` (ordem alfabética, junto com os
demais nomes já listados).

- [ ] **Step 3: Escrever os testes**

```python
# tests/test_essay_engine_contract_v6.py
import pytest
from pydantic import ValidationError

from agente_ia_edu.essay_engine_contract.v6 import CONTRACT_VERSION, InputReliability


def test_contract_version_is_v6():
    assert CONTRACT_VERSION == "essay_engine_output_v6"


def test_input_reliability_accepts_the_three_statuses():
    for status in ("RELIABLE", "USABLE_WITH_WARNING", "UNRELIABLE_NEEDS_REVIEW"):
        InputReliability(status=status, rationale="motivo qualquer")


def test_input_reliability_rejects_unknown_status():
    with pytest.raises(ValidationError):
        InputReliability(status="ALGO_INVALIDO", rationale="motivo")


def test_input_reliability_requires_non_blank_rationale():
    with pytest.raises(ValidationError):
        InputReliability(status="RELIABLE", rationale="")
```

(Um teste end-to-end de `EssayEngineOutput` completo com `input_reliability`
obrigatório já existe implicitamente via `essay_engine_validation.py` -
cobrir isso na Task B4, quando o payload completo de exemplo for montado
para o teste de integração.)

- [ ] **Step 4: Rodar os testes**

Run: `pytest tests/test_essay_engine_contract_v6.py -v`
Expected: 4 passed.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/essay_engine_contract/v6.py tests/test_essay_engine_contract_v6.py
git commit -m "feat(redacao): contrato v6 - campo input_reliability, separado de Alert"
```

---

### Task B3: Prompt v16 — instruções de `input_reliability`

**Files:**
- Create: `src/agente_ia_edu/essay_prompts/v16.py`
- Modify: `src/agente_ia_edu/essay_prompts/__init__.py:20,24-28` (registrar v16)
- Test: `tests/test_essay_prompts_v16.py`

**Interfaces:**
- Consumes: nenhuma interface de código (prompt é texto).
- Produces: `VERSION = "essay_correction_v16"`, `RESPONSE_SCHEMA` com a chave
  `input_reliability`, `build_prompt(...)` com a mesma assinatura de v15.

- [ ] **Step 1: Copiar `v15.py` para `v16.py`**

```bash
cp src/agente_ia_edu/essay_prompts/v15.py src/agente_ia_edu/essay_prompts/v16.py
```

- [ ] **Step 2: Editar `v16.py`**

Trocar `VERSION = "essay_correction_v15"` por `VERSION = "essay_correction_v16"`.

Adicionar ao `RESPONSE_SCHEMA` (antes da chave `"scores"`):

```python
    "input_reliability": {
        "status": "RELIABLE|USABLE_WITH_WARNING|UNRELIABLE_NEEDS_REVIEW",
        "rationale": "string - uma frase explicando a avaliacao",
    },
```

Adicionar, depois do bloco `_RULES_ALERTS` (antes de `_RULES_SIGNALS`), um
novo bloco de regras:

```python
_RULES_INPUT_RELIABILITY = (
    "INPUT_RELIABILITY_RULES: antes de qualquer julgamento pedagogico, "
    "avalie se o texto recebido e confiavel o suficiente para ser avaliado. "
    "Isto e INDEPENDENTE do merito da redacao - um texto fraco mas bem "
    "legivel e RELIABLE; um texto que parece corrompido na leitura "
    "(palavras quebradas, sequencias sem sentido que lembram erro de "
    "transcricao, nao erro de ortografia do aluno) pode ser "
    "USABLE_WITH_WARNING ou UNRELIABLE_NEEDS_REVIEW mesmo que o conteudo "
    "pareca relevante ao tema. Use RELIABLE quando conseguir ler o texto "
    "com confianca, independente da qualidade da escrita. Use "
    "USABLE_WITH_WARNING quando partes relevantes do texto parecerem "
    "corrompidas mas voce ainda conseguir extrair sentido suficiente para "
    "avaliar as cinco competencias. Use UNRELIABLE_NEEDS_REVIEW quando a "
    "corrupcao for extensa o suficiente para tornar qualquer nota nao "
    "confiavel - nesse caso, NUNCA force uma nota: o campo scores e "
    "respeitado normalmente (pode ficar null se SCORING_MODE nao pedir "
    "nota), mas a decisao de USAR essa nota e de quem chama este motor, "
    "nao sua. Jamais confunda isto com TEXTO_INSUFICIENTE: aquele alerta e "
    "sobre o ALUNO ter escrito pouco ou interrompido o texto; "
    "input_reliability e sobre VOCE conseguir ler com confianca o que foi "
    "escrito - um texto completo e longo mas ilegivel por corrupcao e "
    "UNRELIABLE_NEEDS_REVIEW, nunca TEXTO_INSUFICIENTE."
)
```

No corpo de `build_prompt`, inserir `_RULES_INPUT_RELIABILITY` na
concatenação (depois de `_RULES_ALERTS`, antes de `_RULES_SIGNALS`):

```python
        + _RULES_ALERTS + "\n"
        + _RULES_INPUT_RELIABILITY + "\n"
        + _RULES_SIGNALS + "\n"
```

Atualizar o docstring de `build_prompt` para mencionar v16 em vez de v14/v15
como referência de assinatura (mesma assinatura, só o conteúdo mudou).

- [ ] **Step 3: Registrar em `essay_prompts/__init__.py`**

```python
from . import v1, v2, v3, v4, v5, v6, v7, v8, v9, v10, v11, v12, v13, v14, v15, v16

_ARTIFACTS: dict[str, Any] = {
    v1.VERSION: v1, v2.VERSION: v2, v3.VERSION: v3, v4.VERSION: v4, v5.VERSION: v5,
    v6.VERSION: v6, v7.VERSION: v7, v8.VERSION: v8, v9.VERSION: v9, v10.VERSION: v10,
    v11.VERSION: v11, v12.VERSION: v12, v13.VERSION: v13, v14.VERSION: v14,
    v15.VERSION: v15, v16.VERSION: v16,
}
```

- [ ] **Step 4: Escrever os testes**

```python
# tests/test_essay_prompts_v16.py
import json

from agente_ia_edu.essay_prompts import get_essay_prompt
from agente_ia_edu.essay_prompts.v16 import VERSION, RESPONSE_SCHEMA, build_prompt


def test_version_is_v16():
    assert VERSION == "essay_correction_v16"


def test_response_schema_has_input_reliability():
    assert "input_reliability" in RESPONSE_SCHEMA
    assert "status" in RESPONSE_SCHEMA["input_reliability"]


def test_build_prompt_mentions_input_reliability_rules():
    prompt = build_prompt(
        anchor_mode="TEXT_OFFSET", essay_statement="tema qualquer",
        rubric={}, include_scores=True, text="redacao qualquer",
    )
    assert "INPUT_RELIABILITY_RULES" in prompt
    assert "UNRELIABLE_NEEDS_REVIEW" in prompt
    assert "TEXTO_INSUFICIENTE" in prompt  # distingue explicitamente os dois conceitos


def test_registry_resolves_v16():
    artifact = get_essay_prompt("essay_correction_v16")
    assert artifact.version == "essay_correction_v16"
    assert json.dumps(artifact.response_schema)  # serializavel
```

- [ ] **Step 5: Rodar os testes**

Run: `pytest tests/test_essay_prompts_v16.py -v`
Expected: 4 passed.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/essay_prompts/v16.py src/agente_ia_edu/essay_prompts/__init__.py \
        tests/test_essay_prompts_v16.py
git commit -m "feat(redacao): prompt v16 - instrucoes de input_reliability, distintas de TEXTO_INSUFICIENTE"
```

---

### Task B4: Integração no pipeline - Quality Gate decide, short-circuit em `UNRELIABLE_NEEDS_REVIEW`

**Files:**
- Modify: `src/agente_ia_edu/services/essay_correction.py:44` (import do contrato v6 em vez de v5)
- Modify: `src/agente_ia_edu/services/essay_correction.py:70` (`_PROMPT_VERSION`)
- Modify: `src/agente_ia_edu/services/essay_correction.py:544-725` (`_run_ai` - inserir a decisão do Quality Gate antes do bloco de fase 2)
- Modify: `src/agente_ia_edu/db/models/essay_correction.py` (novo campo `quality_gate_status`, migration)
- Create: `migrations/versions/065_essay_quality_gate.py`
- Test: `tests/test_essay_quality_gate_integration.py`

**Interfaces:**
- Consumes: `estimate_text_reliability_heuristic`/`combine_input_reliability_signals` (Task B1); `InputReliability`/contrato v6 (Task B2); prompt v16 (Task B3).
- Produces: `EssayCorrection.quality_gate_status: str | None`, usado pela Fase C (o Zero Gate só avalia quando o Quality Gate não já encerrou o fluxo) e pelo script de benchmark (Task A3, que lê `correction.ai_output`/`final_scores` - sem mudança de interface lá, já que `engine_scores=None` continua representando "sem nota" independente da causa).

- [ ] **Step 1: Migration - novo campo**

```python
# migrations/versions/065_essay_quality_gate.py
"""Fase B do Quality Gate - novo campo quality_gate_status em essay_corrections,
e a versao do Quality Gate que produziu a decisao (spec 2026-10-06, Fase B)."""
from alembic import op
import sqlalchemy as sa

revision = "065_essay_quality_gate"
down_revision = "064_essay_batch_extracted_text"  # confirmar o down_revision real antes de aplicar


def upgrade() -> None:
    op.add_column("essay_corrections", sa.Column("quality_gate_status", sa.String(30), nullable=True))
    op.add_column("essay_corrections", sa.Column("quality_gate_version", sa.String(50), nullable=True))
    op.create_check_constraint(
        "ck_essay_corrections_quality_gate_status",
        "essay_corrections",
        "quality_gate_status IS NULL OR quality_gate_status IN "
        "('RELIABLE', 'USABLE_WITH_WARNING', 'UNRELIABLE_NEEDS_REVIEW')",
    )


def downgrade() -> None:
    op.drop_constraint("ck_essay_corrections_quality_gate_status", "essay_corrections", type_="check")
    op.drop_column("essay_corrections", "quality_gate_version")
    op.drop_column("essay_corrections", "quality_gate_status")
```

Conferir o `down_revision` real com `alembic heads`/`ls migrations/versions/
| tail -1` antes de fixar - usar o último existente no momento da
implementação, não necessariamente `064_essay_batch_extracted_text`.

- [ ] **Step 2: Rodar a migration**

```bash
DATABASE_URL="postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/agente_ia_edu" \
  alembic upgrade head
```

- [ ] **Step 3: Adicionar as colunas ao model**

Em `src/agente_ia_edu/db/models/essay_correction.py`, depois do campo
`failure_reason`:

```python
    quality_gate_status: Mapped[str | None] = mapped_column(String(30))
    quality_gate_version: Mapped[str | None] = mapped_column(String(50))
```

- [ ] **Step 4: Escrever o teste de integração (com provider falso)**

Mesmo padrão de teste já usado para `EssayCorrectionService` no projeto -
um provider de texto falso que devolve um payload JSON completo e válido,
incluindo `input_reliability`.

```python
# tests/test_essay_quality_gate_integration.py
import json

import pytest

from agente_ia_edu.services.essay_correction import EssayCorrectionService


def _payload_with_reliability(status: str, rationale: str, *, scores=None):
    return {
        "identification": {"essay_id": "x", "essay_version_id": "x", "rubric_version": "enem_2025",
                            "model_version": "fake", "prompt_version": "essay_correction_v16",
                            "engine_version": "x", "contract_version": "essay_engine_output_v6",
                            "anchor_mode": "TEXT_OFFSET"},
        "input_reliability": {"status": status, "rationale": rationale},
        "scores": scores,
        "rationales": [], "annotations": [], "rewrites": [],
        "feedback": {"strengths": [], "improvements": [], "next_essay_strategy": "x"},
        "intervention": {"agente": "x", "acao": "x", "meio_modo": "x", "finalidade": "x",
                          "detalhamento": "x", "respeita_direitos_humanos": True},
        "alerts": [], "intro_message": "x", "closing_message": "x", "mechanical_review": [],
        "c2_tipologia_textual": "x", "c2_tema": "x", "c2_repertorio_sociocultural": "x",
        "c2_orientacao_melhoria": "x", "c3_projeto_argumentativo": "x",
        "c3_fatos_informacoes_opinioes": "x", "c3_autoria": "x", "c3_orientacao_melhoria": "x",
    }


class _FakeTextProvider:
    def __init__(self, payload):
        self._payload = payload

    async def generate(self, request):
        from agente_ia_edu.providers.models import TextGenerationResult
        return TextGenerationResult(text=json.dumps(self._payload), model="fake-model",
                                     input_tokens=10, output_tokens=10)


@pytest.mark.asyncio
async def test_unreliable_input_short_circuits_to_needs_review_without_a_score(
    seed_session, make_essay_submission,  # fixtures ja existentes no projeto - conferir nome exato em conftest
):
    submission = await make_essay_submission(
        canonical_text="texto corrompido qualquer, irrelevante para este teste",
    )
    payload = _payload_with_reliability(
        "UNRELIABLE_NEEDS_REVIEW", "Texto apresenta corrupcao extensa de leitura.",
        scores={"per_competency": {c: {"points": 80, "confidence": 0.9} for c in ("C1", "C2", "C3", "C4", "C5")}, "total": 400},
    )
    service = EssayCorrectionService(seed_session, text_provider=_FakeTextProvider(payload))

    correction = await service.correct(submission.id)

    assert correction.status == "NEEDS_REVIEW"
    assert correction.final_scores is None  # NUNCA publica nota como se fosse confiavel
    assert correction.quality_gate_status == "UNRELIABLE_NEEDS_REVIEW"
    assert "QUALITY_GATE_UNRELIABLE" in (correction.failure_reason or "")


@pytest.mark.asyncio
async def test_reliable_input_proceeds_to_normal_scoring(seed_session, make_essay_submission):
    submission = await make_essay_submission(canonical_text="redacao normal e legivel sobre o tema")
    payload = _payload_with_reliability(
        "RELIABLE", "Texto legivel sem ressalvas.",
        scores={"per_competency": {c: {"points": 120, "confidence": 0.9} for c in ("C1", "C2", "C3", "C4", "C5")}, "total": 600},
    )
    service = EssayCorrectionService(seed_session, text_provider=_FakeTextProvider(payload))

    correction = await service.correct(submission.id)

    assert correction.quality_gate_status == "RELIABLE"
    assert correction.final_scores is not None
```

Conferir em `tests/test_r3_essay_correction_service.py` o nome exato das
fixtures `seed_session`/equivalente de `make_essay_submission` já usadas no
projeto - reaproveitar, nunca criar um helper de setup paralelo.

- [ ] **Step 5: Rodar e confirmar que falha**

Run: `pytest tests/test_essay_quality_gate_integration.py -v`
Expected: FAIL (`quality_gate_status` não existe / contrato ainda espera v5).

- [ ] **Step 6: Implementar a integração em `essay_correction.py`**

Trocar o import (linha 44):

```python
from ..essay_engine_contract.v6 import (
    CONTRACT_VERSION,
    EssayEngineOutput,
    # ... os demais nomes ja importados de v5, sem mudanca de nome.
)
```

Trocar a constante (linha 70):

```python
_PROMPT_VERSION = "essay_correction_v16"
_QUALITY_GATE_VERSION = "quality_gate_v1"
```

Importar as novas funções no topo do arquivo:

```python
from .essay_input_reliability import (
    combine_input_reliability_signals, estimate_text_reliability_heuristic,
)
```

Inserir, imediatamente depois da validação de `validate_engine_output_from_payload`
ter sucedido (depois do bloco `except EssayEngineOutputRejected`, antes de
`phase2_points: dict[str, int] | None = None`):

```python
        ocr_average_confidence = None
        if submission.anchor_mode == "IMAGE_REGION":
            pages_result = await self.session.execute(
                select(EssaySubmissionPage).where(EssaySubmissionPage.essay_submission_id == submission.id)
            )
            all_tokens = [
                t for page in pages_result.scalars().all() for t in (page.ocr_tokens or [])
            ]
            if all_tokens:
                ocr_average_confidence = sum(t["confidence"] for t in all_tokens) / len(all_tokens)

        heuristic_ratio = estimate_text_reliability_heuristic(text) if text is not None else None
        quality_gate_status = combine_input_reliability_signals(
            heuristic_ratio=heuristic_ratio,
            model_status=output.input_reliability.status,
            ocr_average_confidence=ocr_average_confidence,
        )

        if quality_gate_status == "UNRELIABLE_NEEDS_REVIEW":
            # Quality Gate decide ANTES de qualquer fase 2 - nunca gasta uma
            # chamada de IA refinando uma nota sobre leitura nao confiavel, e
            # NUNCA publica nota como se fosse valida (spec Fase B).
            key = compute_correction_key(
                normalized_text_hash=input_hash, essay_prompt_id=str(essay_prompt.id),
                rubric_version=rubric_version, model_version=model_version,
                prompt_version=prompt_artifact.version, engine_version=_ENGINE_VERSION,
            )
            return {
                "correction_key": key, "rubric_version": rubric_version,
                "model_version": model_version, "prompt_version": prompt_artifact.version,
                "engine_version": _ENGINE_VERSION,
                "ai_output": output.model_dump(mode="json"),
                "final_scores": None, "final_feedback": output.feedback.model_dump(mode="json"),
                "failure_reason": f"QUALITY_GATE_UNRELIABLE: {output.input_reliability.rationale}",
                "input_tokens": input_tokens, "output_tokens": output_tokens,
                "quality_gate_status": quality_gate_status, "quality_gate_version": _QUALITY_GATE_VERSION,
            }
```

No `return` final de `_run_ai` (o bloco que hoje termina em
`"input_tokens": input_tokens, "output_tokens": output_tokens,`), somar:

```python
            "quality_gate_status": quality_gate_status, "quality_gate_version": _QUALITY_GATE_VERSION,
```

E em TODOS os outros `return {**failure_fields, ...}` do método (os de
`ProviderError`/`JSONDecodeError`/`ValueError` capturados ANTES da leitura
do contrato), adicionar ao dict `failure_fields` (definido mais acima no
método): `"quality_gate_status": None, "quality_gate_version": None,`.

Em `correct()` e `retry()` (onde `EssayCorrection(**fields)` é construído e
onde `setattr(correction, field, value)` roda), nenhuma mudança adicional é
necessária - os dois novos campos entram em `fields`/no loop de `setattr`
automaticamente, já que `EssayCorrection` agora tem as colunas (Step 3).

Em `_apply_review_policy`, adicionar no topo (antes do check de
`correction.ai_output is None`):

```python
        if correction.quality_gate_status == "UNRELIABLE_NEEDS_REVIEW":
            correction.status = "NEEDS_REVIEW"
            return
```

(`_run_ai` já devolve `final_scores=None` para este caso - este guard em
`_apply_review_policy` é defensivo, para o caso de `correct()`/`retry()`
chamarem `_apply_review_policy` sobre um resultado já marcado
`UNRELIABLE_NEEDS_REVIEW` por outro caminho futuro.)

- [ ] **Step 7: Rodar os testes**

Run: `pytest tests/test_essay_quality_gate_integration.py -v`
Expected: 2 passed.

- [ ] **Step 8: Rodar a suíte completa de redação**

Run: `pytest tests/ -k essay -v`
Expected: todos passam (nenhuma regressão nos testes existentes de
`essay_correction`/`essay_submission`/`essay_engine_validation`).

- [ ] **Step 9: Commit**

```bash
git add migrations/versions/065_essay_quality_gate.py \
        src/agente_ia_edu/db/models/essay_correction.py \
        src/agente_ia_edu/services/essay_correction.py \
        tests/test_essay_quality_gate_integration.py
git commit -m "feat(redacao): Quality Gate decide antes da fase 2, nunca publica nota sobre entrada nao confiavel"
```

---

### Task B5: Regressão Larissa/João Miguel + benchmark pós-Fase B

**Files:**
- Modify: `tests/fixtures/essay_calibration_benchmark_v1.json` (nenhuma mudança de dado - os dois casos já estão na fixture da Task A1)
- Create: `tests/test_essay_quality_gate_regression.py`
- Create: `tests/fixtures/essay_calibration_baselines/AFTER_QUALITY_GATE_V1.json` (gerado, committado)

**Interfaces:**
- Consumes: texto real de Larissa/João Miguel (já na fixture, Task A1); `EssayCorrectionService` real (Task B4).

- [ ] **Step 1: Escrever o teste de regressão com o texto REAL (chamada real de IA)**

```python
# tests/test_essay_quality_gate_regression.py
"""Regressao obrigatoria (spec Fase B): Larissa e Joao Miguel, cujo texto
real e corrompido por OCR malfeito na origem, NUNCA podem voltar a ser
zerados por TEXTO_INSUFICIENTE. Chamada REAL de IA - roda fora do pytest
padrao (marcado 'live'), ja que depende de rede e custa dinheiro."""
import json
from pathlib import Path

import pytest

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "essay_calibration_benchmark_v1.json").read_text(encoding="utf-8")
)
_FIXTURE_BY_REF = {e["student_ref"]: e for e in _FIXTURE}
_LARISSA = _FIXTURE_BY_REF["aluno_09"]
_JOAO_MIGUEL = _FIXTURE_BY_REF["aluno_10"]


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.parametrize("entry", [_LARISSA, _JOAO_MIGUEL])
async def test_garbled_input_never_zeroed_via_texto_insuficiente(entry, seed_session, make_essay_submission):
    from agente_ia_edu.services.essay_correction import EssayCorrectionService

    submission = await make_essay_submission(canonical_text=entry["body_text"])
    service = EssayCorrectionService(seed_session)  # providers REAIS

    correction = await service.correct(submission.id)

    assert correction.status == "NEEDS_REVIEW"
    assert correction.final_scores is None
    assert correction.quality_gate_status == "UNRELIABLE_NEEDS_REVIEW"
    alert_codes = {a["code"] for a in (correction.ai_output.get("alerts") or [])}
    assert "TEXTO_INSUFICIENTE" not in alert_codes
```

Registrar o marker `live` em `pyproject.toml` (`[tool.pytest.ini_options]`,
`markers = ["live: faz chamadas reais de IA - nao roda no CI padrao"]`) se
ainda não existir um marker equivalente no projeto - conferir primeiro se já
existe algo assim para testes com IA real.

- [ ] **Step 2: Rodar só este teste (chamada real, ~1-2 min)**

```bash
pytest tests/test_essay_quality_gate_regression.py -v -m live
```

Expected: 2 passed.

- [ ] **Step 3: Rodar o benchmark completo pós-Fase B**

```bash
python scripts/essay_calibration_benchmark.py --tag AFTER_QUALITY_GATE_V1
```

- [ ] **Step 4: Comparar com o baseline**

```python
import json
baseline = json.load(open("tests/fixtures/essay_calibration_baselines/CALIBRATION_BASELINE_V1.json"))
after = json.load(open("tests/fixtures/essay_calibration_baselines/AFTER_QUALITY_GATE_V1.json"))
print("needs_review_count:", baseline["report"]["needs_review_count"], "->", after["report"]["needs_review_count"])
print("mae_total:", baseline["report"]["mae_total"], "->", after["report"]["mae_total"])
```

Esperado: `needs_review_count` sobe (Larissa, João Miguel e qualquer outro
caso similar saem da amostra de "válidas"); `mae_total` das restantes pode
mudar (menos contaminação de casos extremos) mas não é o alvo desta fase -
só confirmar que não piorou destravado.

- [ ] **Step 5: Commit**

```bash
git add tests/test_essay_quality_gate_regression.py \
        tests/fixtures/essay_calibration_baselines/AFTER_QUALITY_GATE_V1.json pyproject.toml
git commit -m "test(redacao): regressao Larissa/Joao Miguel + benchmark pos-Quality-Gate"
```

---

## Fase C — Zero Gate

### Task C1: Diagnóstico ao vivo — por que a fase 2 rejeitou `FUGA_AO_TEMA` em Sabrina/Henrique

**Files:**
- Create: `scripts/diagnose_zero_gate_rejection.py` (script de investigação, não fica no pipeline de produção)
- Create: `docs/superpowers/plans/2026-10-06-motor-redacao-quality-zero-gate.diagnostico-c1.md` (achado documentado, usado pela Task C2)

**Interfaces:**
- Consumes: texto e os alertas reais já conhecidos de Sabrina/Henrique (confirmados nesta investigação: ambas com `FUGA_AO_TEMA` na fase 1, com `detail` citando o conteúdo real).

- [ ] **Step 1: Reproduzir a chamada real da fase 2 isoladamente**

```python
# scripts/diagnose_zero_gate_rejection.py
"""Reproduz, isoladamente, a chamada de alert_review_v1 para Sabrina e
Henrique - os dois casos confirmados de FUGA_AO_TEMA levantado na fase 1 e
rejeitado na fase 2 (spec Fase C, diagnostico obrigatorio antes de
qualquer mudanca de prompt/threshold)."""
import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

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
```

- [ ] **Step 2: Rodar e inspecionar o `reasoning` real**

```bash
python scripts/diagnose_zero_gate_rejection.py
```

- [ ] **Step 3: Rodar de novo, 3-5 vezes, para checar consistência**

O mesmo problema que motivou a existência de `alert_review_v1.py`
(inconsistência rodada-a-rodada) pode estar presente na rejeição também -
rodar múltiplas vezes decide se a causa é determinística (sempre rejeita) ou
inconsistente (as vezes confirma, as vezes rejeita).

- [ ] **Step 4: Classificar a causa e documentar**

Escrever `docs/superpowers/plans/2026-10-06-motor-redacao-quality-zero-gate.diagnostico-c1.md`
com: o `reasoning` real obtido, qual das 3 hipóteses da spec (prompt
interpretado mais estrito na prática / evidência perdida entre fases /
bug de código) o dado sustenta, e se o comportamento é consistente ou
inconsistente entre execuções. Isto direciona a Task C2 - não prosseguir
para C2 sem este documento.

**Nenhum código de produção é alterado nesta task** - é só investigação. Não
há "commit de feature" aqui; commitar só o script e o documento de achado:

```bash
git add scripts/diagnose_zero_gate_rejection.py \
        docs/superpowers/plans/2026-10-06-motor-redacao-quality-zero-gate.diagnostico-c1.md
git commit -m "docs(redacao): diagnostico ao vivo - causa da rejeicao de FUGA_AO_TEMA em Sabrina/Henrique"
```

---

### Task C2: Zero Gate reestruturado — descobrir, não só confirmar

**Files:**
- Create: `src/agente_ia_edu/services/essay_zero_gate.py`
- Modify: `src/agente_ia_edu/services/essay_correction.py` (reordenar `_run_ai`: Zero Gate resolve antes de C1-C5)
- Modify: `src/agente_ia_edu/db/models/essay_correction.py` (novo campo `zero_gate_decision`)
- Create: `migrations/versions/066_essay_zero_gate.py`
- Test: `tests/test_essay_zero_gate.py`

**Interfaces:**
- Consumes: achado da Task C1 (direciona SE a correção é no prompt de
  `alert_review_v1`/sucessor, no código de agregação, ou em ambos);
  `_ANULA_REDACAO_ALERT_CODES` (já existe, `essay_correction.py:154-158`,
  sem mudança de conteúdo).
- Produces:
  ```python
  @dataclass(frozen=True)
  class ZeroGateDecision:
      decision: str        # "ZERAR" | "NAO_ZERAR" | "ENCAMINHAR_REVISAO"
      rule_code: str | None
      evidence: str
      confidence: float | None
      requires_human_review: bool
      rule_version: str

  async def evaluate_zero_gate(
      *, output: EssayEngineOutput, essay_statement: str, text_provider,
  ) -> ZeroGateDecision: ...
  ```

**Esta task é moldada pelo achado da Task C1 - a estrutura abaixo (um passo
de avaliação próprio, que roda sempre, produzindo uma decisão auditável) é
fixa; o CONTEÚDO exato da chamada de IA usada dentro de `evaluate_zero_gate`
(reaproveitar `alert_review_v1.py` reescrito, ou uma chamada nova) depende
do que a Task C1 encontrar:**

- **Se a causa for H1 (prompt interpretado mais estrito na prática que o
  texto sugere):** criar `alert_review_v2.py` (nunca editar `v1.py` -
  convenção de imutabilidade) com instruções reforçadas por exemplo
  concreto (incluir o próprio caso Sabrina/Henrique como exemplo
  "DEVE confirmar" no prompt), e trocar a chamada dentro de
  `evaluate_zero_gate` para usar `alert_review_v2.build_prompt`.
- **Se a causa for H3 (bug de código):** corrigir o bug identificado
  (ex.: se `confirmed &= candidate_codes` estiver descartando algo que não
  deveria, ou se `all_codes`/`candidates` estiverem montados errado) -
  documentar o bug exato encontrado no commit.
- **Em qualquer caso**, a mudança estrutural abaixo (Zero Gate roda sempre,
  não só quando `_ANULA_REDACAO_ALERT_CODES` já tem candidato) é aplicada -
  é isto que resolve os outros 7 casos perdidos (texto corrompido que nunca
  chegou a ser candidato na fase 1).

- [ ] **Step 1: Migration**

```python
# migrations/versions/066_essay_zero_gate.py
"""Fase C - decisao auditavel do Zero Gate em essay_corrections (spec
2026-10-06, Fase C)."""
from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB

revision = "066_essay_zero_gate"
down_revision = "065_essay_quality_gate"


def upgrade() -> None:
    op.add_column("essay_corrections", sa.Column("zero_gate_decision", JSONB, nullable=True))
    op.add_column("essay_corrections", sa.Column("zero_gate_version", sa.String(50), nullable=True))


def downgrade() -> None:
    op.drop_column("essay_corrections", "zero_gate_version")
    op.drop_column("essay_corrections", "zero_gate_decision")
```

- [ ] **Step 2: Rodar a migration**

```bash
DATABASE_URL="postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/agente_ia_edu" alembic upgrade head
```

- [ ] **Step 3: Adicionar as colunas ao model**

Em `essay_correction.py` (o model, `db/models/essay_correction.py`):

```python
    zero_gate_decision: Mapped[dict[str, Any] | None] = mapped_column(JSONBCompatible)
    zero_gate_version: Mapped[str | None] = mapped_column(String(50))
```

- [ ] **Step 4: Escrever os testes (com provider falso, cobrindo os 3 desfechos)**

```python
# tests/test_essay_zero_gate.py
import pytest

from agente_ia_edu.services.essay_zero_gate import ZeroGateDecision, evaluate_zero_gate


class _FakeProvider:
    def __init__(self, response: dict):
        self._response = response

    async def generate(self, request):
        import json
        from agente_ia_edu.providers.models import TextGenerationResult
        return TextGenerationResult(text=json.dumps(self._response), model="fake", input_tokens=1, output_tokens=1)


def _output_with_alerts(alert_codes):
    from agente_ia_edu.essay_engine_contract.v6 import Alert, EssayEngineOutput, InputReliability
    # construir um EssayEngineOutput minimo valido com os alertas dados -
    # reaproveitar o builder de payload completo ja usado na Task B4 e so
    # trocar "alerts".
    ...  # implementar usando o mesmo payload builder de test_essay_quality_gate_integration.py


@pytest.mark.asyncio
async def test_zera_quando_fuga_ao_tema_confirmado_com_evidencia_concreta():
    output = _output_with_alerts([{"code": "FUGA_AO_TEMA", "detail": "cita apenas relacoes familiares, nunca o tema de envelhecimento"}])
    provider = _FakeProvider({"confirmed_alert_codes": ["FUGA_AO_TEMA"], "reasoning": "o texto realmente nao toca o tema em nenhum momento"})

    decision = await evaluate_zero_gate(output=output, essay_statement="tema qualquer", text_provider=provider)

    assert decision.decision == "ZERAR"
    assert decision.rule_code == "FUGA_AO_TEMA"
    assert decision.evidence
    assert decision.requires_human_review is False


@pytest.mark.asyncio
async def test_nao_zera_quando_fase1_nao_levanta_nenhum_candidato():
    output = _output_with_alerts([])
    provider = _FakeProvider({"confirmed_alert_codes": [], "reasoning": "nao se aplica"})

    decision = await evaluate_zero_gate(output=output, essay_statement="tema qualquer", text_provider=provider)

    assert decision.decision == "NAO_ZERAR"


@pytest.mark.asyncio
async def test_encaminha_revisao_quando_evidencia_insuficiente():
    output = _output_with_alerts([{"code": "TEXTO_ILEGIVEL", "detail": "parcialmente dificil de ler"}])
    provider = _FakeProvider({"confirmed_alert_codes": [], "reasoning": "evidencia duvidosa, nao e possivel confirmar nem rejeitar com seguranca"})

    decision = await evaluate_zero_gate(output=output, essay_statement="tema qualquer", text_provider=provider)

    assert decision.requires_human_review is True
```

(O terceiro teste depende de `evaluate_zero_gate`/o prompt sucessor
exporem um jeito de distinguir "rejeitado com confiança" de "incerto" -
decidir o mecanismo exato na implementação, informado pelo achado C1;
manter o contrato de que `requires_human_review=True` implica
`decision != "ZERAR"` e que esse caso nunca segue para nota comum como se a
dúvida não existisse.)

- [ ] **Step 5: Implementar `essay_zero_gate.py`**

```python
# src/agente_ia_edu/services/essay_zero_gate.py
"""Zero Gate: avalia situacoes normativas de zero ANTES de C1-C5, com
responsabilidade propria - nao depende exclusivamente de a fase 1 ja ter
levantado um candidato (spec Fase C). O conteudo exato da chamada de IA
usada aqui foi decidido pelo diagnostico da Task C1 - ver
docs/superpowers/plans/2026-10-06-motor-redacao-quality-zero-gate.diagnostico-c1.md."""
from __future__ import annotations

from dataclasses import dataclass

from ..essay_engine_contract.v6 import EssayEngineOutput

_ZERO_GATE_VERSION = "zero_gate_v1"
_ANULA_REDACAO_ALERT_CODES = frozenset({
    "FUGA_AO_TEMA", "TIPO_TEXTUAL_PREDOMINANTE", "TEXTO_INSUFICIENTE",
    "ANULACAO_PROPOSITAL", "PARTE_DESCONECTADA_DO_TEMA",
    "IDENTIFICACAO_INDEVIDA", "LINGUA_ESTRANGEIRA", "TEXTO_ILEGIVEL",
})


@dataclass(frozen=True)
class ZeroGateDecision:
    decision: str  # "ZERAR" | "NAO_ZERAR" | "ENCAMINHAR_REVISAO"
    rule_code: str | None
    evidence: str
    confidence: float | None
    requires_human_review: bool
    rule_version: str = _ZERO_GATE_VERSION


async def evaluate_zero_gate(
    *, output: EssayEngineOutput, essay_statement: str, text_provider,
) -> ZeroGateDecision:
    # implementacao exata definida na Task C2, de acordo com o achado da
    # Task C1 - preencher aqui o corpo real (chamada ao prompt sucessor de
    # alert_review, interpretacao do reasoning, montagem de ZeroGateDecision).
    raise NotImplementedError
```

(O corpo de `evaluate_zero_gate` não é fixado neste plano porque depende do
achado empírico da Task C1 - a Global Constraint "não afrouxe o threshold
sem causa identificada" significa que a implementação real só é escrita
depois desse diagnóstico. A assinatura, o retorno, e os 4 testes acima são
o contrato que a implementação tem que satisfazer, qualquer que seja a
causa raiz encontrada.)

- [ ] **Step 6: Rodar os testes**

Run: `pytest tests/test_essay_zero_gate.py -v`
Expected: 3 passed.

- [ ] **Step 7: Integrar em `essay_correction.py` - reordenar `_run_ai`**

Trocar o bloco atual (linhas ~670-707, o `asyncio.gather` de
`_score_competencies_from_evidence` + `_review_anula_redacao_alerts`) por:

```python
        zero_gate_decision = None
        phase2_points: dict[str, int] | None = None
        if output.scores is not None:
            zero_gate_decision = await evaluate_zero_gate(
                output=output, essay_statement=_effective_essay_statement(submission, essay_prompt),
                text_provider=self._get_text_provider(),
            )
            if zero_gate_decision.decision == "ENCAMINHAR_REVISAO":
                key = compute_correction_key(
                    normalized_text_hash=input_hash, essay_prompt_id=str(essay_prompt.id),
                    rubric_version=rubric_version, model_version=model_version,
                    prompt_version=prompt_artifact.version, engine_version=_ENGINE_VERSION,
                )
                return {
                    "correction_key": key, "rubric_version": rubric_version,
                    "model_version": model_version, "prompt_version": prompt_artifact.version,
                    "engine_version": _ENGINE_VERSION, "ai_output": output.model_dump(mode="json"),
                    "final_scores": None, "final_feedback": output.feedback.model_dump(mode="json"),
                    "failure_reason": f"ZERO_GATE_UNCERTAIN: {zero_gate_decision.evidence}",
                    "input_tokens": input_tokens, "output_tokens": output_tokens,
                    "quality_gate_status": quality_gate_status, "quality_gate_version": _QUALITY_GATE_VERSION,
                    "zero_gate_decision": {
                        "decision": zero_gate_decision.decision, "rule_code": zero_gate_decision.rule_code,
                        "evidence": zero_gate_decision.evidence, "confidence": zero_gate_decision.confidence,
                        "requires_human_review": zero_gate_decision.requires_human_review,
                    },
                    "zero_gate_version": zero_gate_decision.rule_version,
                }
            if zero_gate_decision.decision == "NAO_ZERAR":
                # Short-circuit NA OUTRA direcao do antigo codigo: so chama
                # competency-scoring quando o Zero Gate ja decidiu que a
                # redacao NAO zera - antes rodava sempre, em paralelo com a
                # revisao de alerta; agora o Zero Gate resolve primeiro.
                try:
                    phase2_points = await self._score_competencies_from_evidence(
                        output=output, rubric_file=rubric_file,
                    )
                except ProviderError as exc:
                    return {**failure_fields, "model_version": model_version,
                            "failure_reason": f"CompetencyScoringFailed: {type(exc).__name__}: {exc}"}
                except (json.JSONDecodeError, KeyError, ValueError) as exc:
                    return {**failure_fields, "model_version": model_version,
                            "failure_reason": f"CompetencyScoringFailed: {type(exc).__name__}: {exc}"}
            # decision == "ZERAR": phase2_points fica None de propósito -
            # _apply_deterministic_scoring_rules zera tudo, nao ha nota de
            # competencia para refinar.
```

Trocar a chamada final de `_apply_deterministic_scoring_rules` (que hoje
recebe `confirmed_alert_codes`) para receber o `rule_code` do Zero Gate
quando `decision == "ZERAR"`, ou o conjunto de alertas não-zeradores
(`OCR_DUVIDOSO`/`POSSIVEL_DUPLICIDADE`/`TANGENCIAMENTO_AO_TEMA`) quando
`decision == "NAO_ZERAR"` - ajustar a assinatura de
`_apply_deterministic_scoring_rules` para aceitar o `ZeroGateDecision`
diretamente em vez de `alert_codes: set[str] | None`, já que agora há uma
decisão estruturada em vez de um conjunto plano.

Adicionar `"zero_gate_decision": None, "zero_gate_version": None,` a
`failure_fields` e a todo `return` que hoje não passa por este novo bloco.

Remover `_review_anula_redacao_alerts` (substituído por
`evaluate_zero_gate`) e seu import de `alert_review_v1` SE o diagnóstico C1
confirmar que a lógica foi inteiramente absorvida por `essay_zero_gate.py` -
se a Task C1 decidir reaproveitar `alert_review_v1`/`v2` dentro de
`evaluate_zero_gate`, manter o import lá dentro em vez de em
`essay_correction.py`.

Bump de `_ENGINE_VERSION` (o fluxo de `_run_ai` mudou de ordem):

```python
_ENGINE_VERSION = "r3_correction_engine_v4"
```

- [ ] **Step 8: Rodar a suíte completa de redação**

Run: `pytest tests/ -k essay -v`
Expected: todos passam, incluindo os testes de B4/B5 (nenhuma regressão).

- [ ] **Step 9: Commit**

```bash
git add migrations/versions/066_essay_zero_gate.py \
        src/agente_ia_edu/db/models/essay_correction.py \
        src/agente_ia_edu/services/essay_zero_gate.py \
        src/agente_ia_edu/services/essay_correction.py \
        tests/test_essay_zero_gate.py
git commit -m "feat(redacao): Zero Gate reestruturado - avalia sempre, decisao auditavel, nunca so confirma"
```

---

### Task C3: Regressão Sabrina/Henrique + Larissa/João Miguel (conjunta)

**Files:**
- Create: `tests/test_essay_zero_gate_regression.py`

**Interfaces:**
- Consumes: texto real de Sabrina/Henrique/Larissa/João Miguel (fixture, Task A1); pipeline completo pós-Task C2.

- [ ] **Step 1: Escrever os 4 testes de regressão (chamada real)**

```python
# tests/test_essay_zero_gate_regression.py
"""Regressao obrigatoria (spec Fase C): os 4 casos que motivaram este
trabalho nunca podem voltar ao comportamento antigo. Chamada REAL de IA."""
import json
from pathlib import Path

import pytest

_FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "essay_calibration_benchmark_v1.json").read_text(encoding="utf-8")
)


def _find(snippet: str) -> dict:
    return next(e for e in _FIXTURE if snippet in e["body_text"])


_SABRINA = _FIXTURE_BY_REF["aluno_23"]
_HENRIQUE = _FIXTURE_BY_REF["aluno_28"]
_LARISSA = _FIXTURE_BY_REF["aluno_09"]
_JOAO_MIGUEL = _FIXTURE_BY_REF["aluno_10"]


@pytest.mark.live
@pytest.mark.asyncio
async def test_sabrina_e_zerada_por_fuga_ao_tema_com_evidencia(seed_session, make_essay_submission):
    from agente_ia_edu.services.essay_correction import EssayCorrectionService

    submission = await make_essay_submission(canonical_text=_SABRINA["body_text"])
    correction = await EssayCorrectionService(seed_session).correct(submission.id)

    assert correction.status in ("PENDING_REVIEW", "APPROVED")
    assert correction.final_scores["total"] == 0
    assert correction.zero_gate_decision["decision"] == "ZERAR"
    assert correction.zero_gate_decision["rule_code"] == "FUGA_AO_TEMA"
    assert correction.zero_gate_decision["evidence"]


@pytest.mark.live
@pytest.mark.asyncio
async def test_henrique_e_zerado_por_fuga_ao_tema_com_evidencia(seed_session, make_essay_submission):
    from agente_ia_edu.services.essay_correction import EssayCorrectionService

    submission = await make_essay_submission(canonical_text=_HENRIQUE["body_text"])
    correction = await EssayCorrectionService(seed_session).correct(submission.id)

    assert correction.final_scores["total"] == 0
    assert correction.zero_gate_decision["decision"] == "ZERAR"
    assert correction.zero_gate_decision["rule_code"] == "FUGA_AO_TEMA"


@pytest.mark.live
@pytest.mark.asyncio
@pytest.mark.parametrize("entry", [_LARISSA, _JOAO_MIGUEL])
async def test_larissa_e_joao_miguel_continuam_fora_de_texto_insuficiente(entry, seed_session, make_essay_submission):
    from agente_ia_edu.services.essay_correction import EssayCorrectionService

    submission = await make_essay_submission(canonical_text=entry["body_text"])
    correction = await EssayCorrectionService(seed_session).correct(submission.id)

    assert correction.status == "NEEDS_REVIEW"
    assert correction.final_scores is None
    assert correction.quality_gate_status == "UNRELIABLE_NEEDS_REVIEW"
```

- [ ] **Step 2: Rodar (chamada real, ~4-8 min)**

```bash
pytest tests/test_essay_zero_gate_regression.py -v -m live
```

Expected: 4 passed. Se Sabrina/Henrique ainda falharem aqui, a Task C2 não
está completa - voltar e revisar a implementação à luz do diagnóstico C1,
nunca "afrouxar threshold" como atalho (Global Constraint).

- [ ] **Step 3: Commit**

```bash
git add tests/test_essay_zero_gate_regression.py
git commit -m "test(redacao): regressao permanente Sabrina/Henrique (Zero Gate) + Larissa/Joao Miguel (Quality Gate)"
```

---

### Task C4: Benchmark pós-Fase C

**Files:**
- Create: `tests/fixtures/essay_calibration_baselines/AFTER_ZERO_GATE_V1.json` (gerado, committado)

- [ ] **Step 1: Rodar o benchmark completo**

```bash
python scripts/essay_calibration_benchmark.py --tag AFTER_ZERO_GATE_V1
```

- [ ] **Step 2: Comparar `zero_gate_recall`/`zero_gate_precision` com os dois baselines anteriores**

Esperado: `zero_gate_recall` sobe bem acima de 0.1 (pelo menos os 2 casos de
fuga ao tema confirmados, mais qualquer outro que o Zero Gate reestruturado
capture nos 7 restantes - sem garantia de 10/10, já que alguns podem
genuinamente não ter nenhuma regra normativa aplicável detectável só pelo
texto). `zero_gate_precision` não deve cair (nenhum dos 20 válidos deve
começar a ser zerado por engano).

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/essay_calibration_baselines/AFTER_ZERO_GATE_V1.json
git commit -m "chore(redacao): benchmark pos-Zero-Gate (AFTER_ZERO_GATE_V1)"
```

---

## Fase D — Experimento controlado + recalibração C1-C5

### Task D1: Transcrição limpa do subconjunto

**Files:**
- Create: `tests/fixtures/essay_calibration_clean_text_subset_v1.json`

- [ ] **Step 1: Selecionar o subconjunto**

Dos 20 casos `CONFIRMED` (não-especiais) restantes após a Fase B, escolher
8-10 cobrindo diferentes severidades de `OCR_DUVIDOSO` observadas no
baseline `AFTER_ZERO_GATE_V1` (incluir pelo menos 2 com severidade alta, 2
média, 2 baixa/nenhuma - usar o próprio `ai_output.alerts` dos relatórios
anteriores para classificar).

- [ ] **Step 2: Produzir a transcrição limpa**

Para cada um dos 8-10 selecionados, reescrever `body_text` corrigindo
APENAS artefatos de OCR (substituir palavras corrompidas pela palavra real
mais provável, mantendo pontuação e estrutura originais) - nunca corrigir
gramática, concordância ou qualquer erro real do aluno, mesmo que pareça
óbvio. Esta é uma tarefa de julgamento humano cuidadoso, não mecanizável -
se o subagente implementador não tiver confiança alta em alguma palavra
específica, deixá-la como está e anotar no campo `uncertain_tokens` em vez
de arriscar uma correção errada que contamine o experimento na outra
direção.

```json
[
  {
    "student_ref": "aluno_09",
    "original_body_text": "...",
    "clean_body_text": "...",
    "uncertain_tokens": ["palavra_x"]
  }
]
```

- [ ] **Step 3: Commit**

```bash
git add tests/fixtures/essay_calibration_clean_text_subset_v1.json
git commit -m "test(redacao): subconjunto com transcricao limpa para experimento controlado (Fase D)"
```

---

### Task D2: Rodar o experimento controlado (A = atual, B = limpo)

**Files:**
- Create: `scripts/essay_calibration_clean_text_experiment.py`
- Create: `tests/fixtures/essay_calibration_baselines/CLEAN_TEXT_EXPERIMENT_V1.json`

**Interfaces:**
- Consumes: `materialize_benchmark_submissions`/`run_benchmark_corrections` (Task A3, reaproveitados sem mudança de assinatura - a fixture de entrada é que muda de formato); `compute_benchmark_report` (Task A2).

- [ ] **Step 1: Escrever o script**

```python
# scripts/essay_calibration_clean_text_experiment.py
"""Fase D: roda o MESMO motor (mesmo provider/modelo/parametros/rubrica/
prompt/engine_version) contra o texto atual (A) e a transcricao limpa (B)
do mesmo subconjunto, para isolar quanto do vies vem de contaminacao de
OCR vs. regra de pontuacao (spec Fase D)."""
from __future__ import annotations

import asyncio
import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))

from agente_ia_edu.db.session import create_engine, create_session_factory
from agente_ia_edu.services.essay_calibration_metrics import BenchmarkResultRow, compute_benchmark_report
from agente_ia_edu.services.essay_calibration_runner import (
    materialize_benchmark_submissions, run_benchmark_corrections,
)

_SUBSET_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "essay_calibration_clean_text_subset_v1.json"
_BENCHMARK_PATH = Path(__file__).resolve().parent.parent / "tests" / "fixtures" / "essay_calibration_benchmark_v1.json"


async def _run_condition(session_factory, *, entries: list[dict], run_tag: str):
    async with session_factory() as session:
        submissions = await materialize_benchmark_submissions(session, fixture_entries=entries, run_tag=run_tag)
        await session.commit()
    return await run_benchmark_corrections(session_factory, submissions=submissions)


async def main() -> None:
    subset = json.loads(_SUBSET_PATH.read_text(encoding="utf-8"))
    benchmark = {e["student_ref"]: e for e in json.loads(_BENCHMARK_PATH.read_text(encoding="utf-8"))}

    entries_a = [{"student_ref": s["student_ref"], "body_text": s["original_body_text"]} for s in subset]
    entries_b = [{"student_ref": s["student_ref"], "body_text": s["clean_body_text"]} for s in subset]

    engine = create_engine()
    session_factory = create_session_factory(engine)

    results_a = await _run_condition(session_factory, entries=entries_a, run_tag="CLEAN_EXPERIMENT_A")
    results_b = await _run_condition(session_factory, entries=entries_b, run_tag="CLEAN_EXPERIMENT_B")

    def _rows(results):
        rows = []
        for student_ref, correction, status in results:
            entry = benchmark[student_ref]
            engine_scores = None
            if correction is not None and correction.final_scores is not None:
                per_competency = correction.final_scores.get("per_competency") or {}
                engine_scores = {c: (per_competency.get(c) or {}).get("points") for c in ("C1", "C2", "C3", "C4", "C5")}
                engine_scores["total"] = correction.final_scores.get("total")
            rows.append(BenchmarkResultRow(
                student_ref=student_ref, normative_status="CONFIRMED",
                expected_scores=entry["expected_scores"], engine_scores=engine_scores,
                expected_is_special_situation=False, engine_zeroed_whole_essay=False,
                engine_status=status, ocr_duvidoso=False,
            ))
        return rows

    report_a = compute_benchmark_report(_rows(results_a))
    report_b = compute_benchmark_report(_rows(results_b))

    out = {
        "subset_size": len(subset),
        "condition_a_current_text": {
            "mae_total": report_a.mae_total, "mae_per_competency": report_a.mae_per_competency,
            "bias_total": report_a.bias_total, "bias_per_competency": report_a.bias_per_competency,
            "levels_distance_distribution": report_a.levels_distance_distribution,
        },
        "condition_b_clean_text": {
            "mae_total": report_b.mae_total, "mae_per_competency": report_b.mae_per_competency,
            "bias_total": report_b.bias_total, "bias_per_competency": report_b.bias_per_competency,
            "levels_distance_distribution": report_b.levels_distance_distribution,
        },
    }
    out_path = Path("tests/fixtures/essay_calibration_baselines/CLEAN_TEXT_EXPERIMENT_V1.json")
    out_path.write_text(json.dumps(out, ensure_ascii=False, indent=2), encoding="utf-8")
    print(json.dumps(out, indent=2))
    await engine.dispose()


if __name__ == "__main__":
    asyncio.run(main())
```

- [ ] **Step 2: Rodar**

```bash
python scripts/essay_calibration_clean_text_experiment.py
```

- [ ] **Step 3: Commit**

```bash
git add scripts/essay_calibration_clean_text_experiment.py \
        tests/fixtures/essay_calibration_baselines/CLEAN_TEXT_EXPERIMENT_V1.json
git commit -m "test(redacao): experimento controlado texto atual vs. limpo (Fase D)"
```

---

### Task D3: Decisão de recalibração (condicional ao resultado de D2)

**Files:**
- Modify (SE E SOMENTE SE o experimento confirmar viés residual com texto
  limpo): `src/agente_ia_edu/essay_prompts/competency_scoring_v2.py:105-109` (`_RULES_TOP_BAND`)
  e `src/agente_ia_edu/services/essay_correction.py:214-222` (teto de `TANGENCIAMENTO_AO_TEMA`)
- Test: `tests/test_essay_competency_scoring_symmetry.py` (só se a mudança ocorrer)

**Interfaces:**
- Consumes: `condition_a_current_text`/`condition_b_clean_text` (Task D2).

- [ ] **Step 1: Interpretar o resultado de D2**

Comparar `bias_total`/`bias_per_competency` das duas condições:

- **Se o viés da condição B (texto limpo) estiver claramente menor** (ex.:
  reduzido a menos da metade do observado na condição A) - a causa
  dominante é contaminação de entrada, não a regra. **Não alterar nenhuma
  regra de pontuação nesta task.** Em vez disso, registrar a conclusão e
  considerar (fora deste plano, como item futuro) calibrar o limiar entre
  `USABLE_WITH_WARNING` e `RELIABLE` na Fase B.
- **Se o viés persistir, com magnitude parecida, mesmo com texto limpo** -
  sinal real de regra enviesada. Prosseguir para o Step 2.

- [ ] **Step 2 (só se o viés persistir): escrever o teste de simetria ANTES de mudar a regra**

```python
# tests/test_essay_competency_scoring_symmetry.py
"""_RULES_TOP_BAND e o teto de TANGENCIAMENTO_AO_TEMA nao podem preferir
sistematicamente uma direcao sob incerteza (spec Fase D - so chega a esta
mudanca se o experimento controlado da Task D2 confirmar vies residual com
texto limpo)."""
from agente_ia_edu.essay_prompts.competency_scoring_v2 import build_prompt  # ou v3, se nova versao


def test_top_band_rule_does_not_name_a_single_preferred_direction():
    prompt = build_prompt(...)  # mesma assinatura ja usada no projeto
    # a regra de desempate sob duvida genuina nao pode conter uma frase que
    # nomeia uma unica banda preferida ("prefira 160") sem uma contrapartida
    # equivalente para o lado de cima - este teste serve de ancora textual,
    # ajustar a asserção exata para o novo texto escrito no Step 3.
    assert "prefira 160" not in prompt.lower()
```

- [ ] **Step 3 (só se o viés persistir): tornar a regra simétrica**

Em `competency_scoring_v2.py`, reescrever `_RULES_TOP_BAND` (criar uma nova
versão versionada do artefato de prompt, nunca editar a atual, mesma
convenção do projeto) removendo a direção única "na dúvida, prefira 160" por
uma formulação que não prefira nenhum lado sob incerteza genuína - ex.: "na
dúvida genuína entre duas bandas adjacentes, a banda escolhida é a que a
maioria da evidência concreta sustenta; se a evidência for igualmente
compatível com as duas, documente a incerteza no rationale em vez de
escolher mecanicamente a mais baixa". Mesma lógica para o teto de
`TANGENCIAMENTO_AO_TEMA` em `essay_correction.py:214-222` - dar-lhe uma
contrapartida sob incerteza em vez de só penalizar.

**Nunca**: `nota_nova = nota_IA + constante`; nenhuma regra amarrada a nome
ou redação específica (Global Constraint).

- [ ] **Step 4: Rodar os testes de C1-C5 já existentes**

Run: `pytest tests/ -k "competency or scoring" -v`
Expected: nenhuma regressão.

- [ ] **Step 5: Commit (só se houve mudança)**

```bash
git add src/agente_ia_edu/essay_prompts/ src/agente_ia_edu/services/essay_correction.py \
        tests/test_essay_competency_scoring_symmetry.py
git commit -m "fix(redacao): torna _RULES_TOP_BAND/teto TANGENCIAMENTO_AO_TEMA simetricos sob incerteza"
```

Se o Step 1 concluir que a causa é contaminação de entrada, este commit não
acontece - documentar a decisão de não mudar no relatório final (Task D4).

---

### Task D4: Benchmark final + relatório consolidado

**Files:**
- Create: `tests/fixtures/essay_calibration_baselines/AFTER_CALIBRATION_V1.json` (gerado, committado)
- Create: `scripts/essay_calibration_consolidated_report.py`
- Create: `docs/superpowers/plans/2026-10-06-motor-redacao-quality-zero-gate.relatorio-final.md`

**Interfaces:**
- Consumes: os 4 relatórios de baseline (`CALIBRATION_BASELINE_V1`,
  `AFTER_QUALITY_GATE_V1`, `AFTER_ZERO_GATE_V1`, e este novo
  `AFTER_CALIBRATION_V1`).

- [ ] **Step 1: Rodar o benchmark completo final**

```bash
python scripts/essay_calibration_benchmark.py --tag AFTER_CALIBRATION_V1
```

- [ ] **Step 2: Escrever o script de relatório consolidado**

```python
# scripts/essay_calibration_consolidated_report.py
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
```

- [ ] **Step 3: Rodar e escrever o relatório final em prosa**

```bash
python scripts/essay_calibration_consolidated_report.py > /tmp/relatorio_consolidado.txt
```

Escrever `docs/superpowers/plans/2026-10-06-motor-redacao-quality-zero-gate.relatorio-final.md`
com a tabela acima, mais: análise individual de qualquer caso que
permaneça com grande divergência (`|engine_total - expected_total| > 160`,
dois níveis oficiais) depois da Fase D, citando o `student_ref`,
`normative_status`, e uma hipótese do porquê; e a decisão tomada na Task D3
(recalibrou ou não, e por quê) com os números de `CLEAN_TEXT_EXPERIMENT_V1`
ao lado.

- [ ] **Step 4: Commit**

```bash
git add tests/fixtures/essay_calibration_baselines/AFTER_CALIBRATION_V1.json \
        scripts/essay_calibration_consolidated_report.py \
        docs/superpowers/plans/2026-10-06-motor-redacao-quality-zero-gate.relatorio-final.md
git commit -m "docs(redacao): relatorio consolidado - baseline x quality gate x zero gate x calibracao"
```

---

## Self-Review

**Cobertura da spec:** Fase A (benchmark + baseline) → Tasks A1-A4. Fase B
(Quality Gate, separação de domínio, sinal duplo, short-circuit) → Tasks
B1-B5. Fase C (diagnóstico antes de corrigir, decisão auditável, descobrir
não só confirmar, reordenação do pipeline) → Tasks C1-C4. Fase D
(experimento controlado antes de recalibrar, critério de sucesso que não é
MAE=0, nunca constante somada) → Tasks D1-D4. Relatório consolidado final →
Task D4. Política de cópia fora de escopo → nenhuma task a toca,
consistente com a spec.

**Placeholders:** a única lacuna deliberada é o corpo de
`evaluate_zero_gate` (Task C2, Step 5) e o conteúdo exato da Task D3 - ambos
dependem de um resultado empírico que não existe até o diagnóstico/
experimento rodar; cada um tem um contrato de teste fixo e uma árvore de
decisão completa para qualquer resultado possível, não um "TBD" aberto.

**Consistência de tipos:** `ZeroGateDecision`/`BenchmarkResultRow`/
`BenchmarkReport`/`InputReliability` usam os mesmos nomes de campo em toda
task que os consome (conferido manualmente). `_QUALITY_GATE_VERSION`/
`_ZERO_GATE_VERSION` nomeados de forma consistente com a spec.
