# Simulados — Fase 3: Motor de TRI — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar um motor de TRI puro (calibragem 2PL por MMLE-EM, theta por EAP, escala de reporte ancorada no ENEM), com portões de qualidade, análise de itens e uma camada fina de persistência que liga o motor às tabelas `tri_*`.

**Architecture:** O pacote `agente_ia_edu.tri` é **puro**: entra `numpy.ndarray`, saem dataclasses frozen. Ele não importa SQLAlchemy, não conhece aluno, não conhece HTTP, não lê arquivo. Toda a tradução banco↔matriz vive em `agente_ia_edu.services.tri_calibration`, que monta a matriz a partir de `mock_exam_responses`, chama `calibrate()` e grava `tri_calibrations` / `tri_item_parameters` / `tri_student_scores`. Essa fronteira é o que torna o motor verificável por recuperação de parâmetros (§8.1) sem banco nenhum.

**Tech Stack:** Python 3.13, NumPy, SciPy, SQLAlchemy 2.0 (async, psycopg), Alembic, PyYAML, `unittest` sob pytest.

**Spec:** `docs/superpowers/specs/2026-09-29-correcao-simulados-tri-design.md` (fonte de verdade — §6 inteiro, §7, §8.1, §9 fase 3)

## Global Constraints

- Python `>=3.13,<3.14` (`pyproject.toml:9`). Nenhum código pode depender de sintaxe posterior.
- `pytest` roda com `pythonpath = ["src", "."]` e `testpaths = ["tests"]` (`pyproject.toml:56-57`). Comandos de teste neste plano usam `.venv/bin/python -m pytest`.
- **Pillow é proibido no core.** `pyproject.toml:33-34` diz, literalmente, para não adicionar `pdfplumber` porque ele puxa Pillow, que altera o comportamento de `page.images` do pypdf e quebra o parser de ingestão. NumPy e SciPy **não** dependem de Pillow (SciPy depende apenas de NumPy). A Tarefa 1 adiciona uma guarda automatizada contra regressão disso.
- **O core nunca importa `cv2`, `PIL`/Pillow, `opencv-python` nem `reportlab`.** Nem direta nem transitivamente. ReportLab declara `pillow>=9.0.0` como dependência obrigatória, e o import tardio não resolve: o pypdf muda de comportamento conforme Pillow estar **instalado no ambiente**, independentemente de quem o importou (spec §2.1). Geração de cartão e visão computacional vivem no ambiente isolado de §2.1/§2.3, que é Fase 1/Fase 4.
- **Isolamento multi-tenant (spec §3.0, normativo).** Toda tabela deste subsistema carrega `school_id`, e toda chave estrangeira entre tabelas do subsistema é **composta** — `(school_id, parent_id)` referenciando `parent(school_id, id)` — viabilizada por um `UNIQUE(school_id, id)` em cada pai. É a mesma regra que `src/agente_ia_edu/db/models/academic.py:80-82` já aplica (`uq_persons_school_id_id`, `uq_students_school_id_id`). A coluna é redundante com a cadeia de chaves e é exatamente por isso que vale: transforma "nunca misturar dados de escolas diferentes" de disciplina de query em invariante que o banco recusa violar.
- O pacote `agente_ia_edu.tri` **não pode importar** `sqlalchemy`, `fastapi` nem qualquer módulo de `agente_ia_edu.db` / `agente_ia_edu.api`. Guarda automatizada na Tarefa 1.
- Calibragem 2PL por MMLE-EM (Bock-Aitkin), **41 pontos de quadratura**, convergência quando a maior mudança absoluta de parâmetro fica **abaixo de 1e-4**, teto de **500 iterações**; estourar o teto marca `converged = False` (spec §6.1).
- **A distribuição de theta da população depende do modo (spec §6.1, normativo).** *Calibragem livre* (primeiro simulado de uma régua, sem âncoras): prior **fixo em N(0,1)** — a escala precisa de uma origem e essa é a convenção que a fornece. *Calibragem equalizada* (§6.4, com âncoras travadas): **média e desvio da população são estimados junto com o resto**, num laço externo que alterna passo E, passo M e atualização da distribuição populacional. A distinção é obrigatória: com o prior preso em N(0,1), travar os parâmetros das âncoras **não produz equalização nenhuma** — o EM re-centra o grupo novo em zero e desfaz exatamente o deslocamento que a equalização existe para medir. Esse laço vive **dentro** de `tri/`, não em cima dele, porque estimação de parâmetro que fica fora do motor fica fora do teste de recuperação.
- **A estimação da população exige itens travados.** Sem nenhum `fixed_items`, deslocar todos os `b` e a média da população produz a mesma verossimilhança: o modelo não é identificado. `calibrate()` recusa a combinação em voz alta em vez de devolver um número plausível.
- Theta por **EAP**, nunca MLE (spec §6.2).
- Escala de reporte: linear por partes em três pontos — `theta_min` (−3.0) → `min_score`, `0.0` → `median_score`, `theta_max` (+3.0) → `max_score`, com clamp fora da faixa (spec §6.3).
- Régua com `reference_verified_at` nulo **não publica nota**: calcula e devolve marcada como provisória (spec §6.3.1).
- Portões: **N < 200 recusa 2PL**; não-convergência bloqueia publicação. Flags que sinalizam sem bloquear: `a < 0` (gabarito provavelmente errado), `a < 0.2`, proporção de acerto `< 0.05` ou `> 0.95` (spec §6.5).
- **Fora do escopo desta fase:** geração de cartão, leitura óptica, e a lógica de equalização por âncoras (Fase 5). As assinaturas **já aceitam** `fixed_items` para que a calibragem com parâmetros travados seja possível sem refatoração (spec §6.4).
- Testes seguem o padrão do repositório: `unittest.TestCase`, um arquivo `tests/test_*.py` por unidade. Testes com banco usam PostgreSQL real e descartável via `tests/_postgres_test_db.py` (porta 5433), no molde de `tests/test_material_assignment_model.py:44-100`.
- Mensagens de commit em português, no formato `feat:` / `test:` / `chore:` já usado no repositório. **Sem linhas de atribuição** salvo instrução do usuário.

## Pré-requisitos de outras fases (leia antes da Tarefa 17)

As Tarefas 1–16 **não dependem de banco nenhum** e podem ser executadas hoje, contra o repositório como ele está.

As Tarefas 17–19 consomem o schema entregue pela **Fase 1** e populado pela **Fase 2**:

| Plano | Migration | Entrega que a Fase 3 consome |
|---|---|---|
| `2026-09-29-simulados-fase1-dados-e-cartao.md` | `057_mock_exam_core` | `mock_exams`, `mock_exam_areas`, `mock_exam_items`, `mock_exam_responses` (modelos `MockExam`, `MockExamArea`, `MockExamItem`, `MockExamResponse`) |
| `2026-09-29-simulados-fase1-dados-e-cartao.md` | `059_tri_scales_and_scores` | **as quatro tabelas `tri_*`** e os modelos `TriScale`, `TriCalibration`, `TriItemParameter`, `TriStudentScore` em `src/agente_ia_edu/db/models/tri.py` |
| `2026-09-29-simulados-fase2-correcao-manual.md` | *(nenhuma)* | preenche `mock_exam_responses` pela digitação manual; toda linha carrega `school_id` |

**A Fase 3 não cria tabela nem migration.** As `tri_*` são da Fase 1 — duas fases criando a
mesma tabela é o caminho mais curto para duas definições divergentes da nota de um aluno. A
Tarefa 17 apenas **prende em teste** o contrato que o motor precisa desse schema.

O schema da Fase 1 difere em três pontos do que uma leitura direta da spec §3 sugeriria, e as
Tarefas 18 e 19 já estão escritas para ele:

- `tri_calibrations.status` ∈ `PENDING | RUNNING | DONE | FAILED` — a Fase 3 grava `DONE`.
- As colunas de nota são `Numeric`, não `Float`: a leitura devolve `Decimal`. A conversão para
  `float` acontece num único ponto (`reporting_scale_from_row`), porque misturar `Decimal` com
  `ndarray` levanta `TypeError` no meio de um cálculo de nota.
- `school_id` existe em `tri_scales`, e não nas outras três tabelas `tri_*`. O isolamento entre
  escolas é, para elas, verificado no serviço (`calibrate_and_store` recusa uma régua de outra
  escola) — ver a lacuna registrada no fim deste plano.

Se as tabelas não existirem quando você chegar na Tarefa 17, **pare**: o motor (Tarefas 1–15)
está completo e verificado sozinho, e é isso que deve ser reportado.

## File Structure

**Motor puro — `src/agente_ia_edu/tri/` (novo pacote, zero I/O):**

| Arquivo | Responsabilidade |
|---|---|
| `__init__.py` | Reexporta a API pública do motor |
| `model.py` | `probability_2pl`, `ItemParameters`, `ResponseMatrix` (validação e estatísticas brutas) |
| `quadrature.py` | Grade de 41 nós; pesos do prior N(0,1) e repesagem da grade sob N(µ, σ²) |
| `theta.py` | Verossimilhança na grade, posterior, EAP, verossimilhança marginal |
| `quality.py` | Flags de item e portão de publicação (§6.5, §6.3.1) |
| `calibration.py` | Passo M por item, laço EM e atualização da distribuição populacional (`calibrate`) |
| `simulation.py` | Gerador determinístico de respostas a partir de `a`,`b`,`theta` (§8.1) |
| `scale.py` | `ReportingScale` — theta → nota, linear por partes (§6.3) |
| `item_analysis.py` | p-value, bisserial-pontual corrigido, CCI, distratores (§7) |
| `estimator.py` | `TriProficiencyEstimator`, implementa o protocolo `ProficiencyEstimator` |

**Dados de referência (package-data, padrão de `agente_ia_edu.rubrics`):**

| Arquivo | Responsabilidade |
|---|---|
| `src/agente_ia_edu/data/__init__.py` | Marca o pacote |
| `src/agente_ia_edu/data/enem_reference_scales.yaml` | Seed das réguas ENEM 2025 (§6.3.1) |
| `src/agente_ia_edu/data/loader.py` | Lê e valida o YAML; recusa virar régua sem mediana |
| `scripts/compute_enem_reference_medians.py` | Calcula as medianas a partir de `NU_NOTA_*` dos microdados |

**Persistência (fina, é a única que conhece banco):**

| Arquivo | Responsabilidade |
|---|---|
| `src/agente_ia_edu/services/tri_calibration.py` | Banco → matriz → `calibrate()` → linhas `tri_*` |
| `src/agente_ia_edu/services/tri_item_panel.py` | Painel de qualidade da prova (§7) |

Os modelos e a migration das tabelas `tri_*` **não estão aqui**: são da Fase 1 (ver Pré-requisitos).

**Modificados:** `pyproject.toml` (NumPy, SciPy, package-data), `src/agente_ia_edu/services/proficiency.py` (extrai `mastery_band`, sem mudança de comportamento).

---

### Task 1: Dependências, esqueleto do pacote e guarda de fronteira

**Files:**
- Modify: `pyproject.toml:9-22` (bloco `dependencies`)
- Create: `src/agente_ia_edu/tri/__init__.py`
- Test: `tests/test_tri_dependency_boundary.py`

**Interfaces:**
- Consumes: nada.
- Produces: o pacote importável `agente_ia_edu.tri`; NumPy e SciPy disponíveis para todas as tarefas seguintes.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tri_dependency_boundary.py
"""Guardas de fronteira do motor de TRI (spec §2.2, §2.3).

Duas garantias que nenhum teste de comportamento pegaria:

1. O core continua sem Pillow / OpenCV / ReportLab. pyproject.toml:33-34
   registra que Pillow altera page.images do pypdf e quebra o parser de
   ingestao; OpenCV convive com Pillow; e ReportLab DECLARA pillow>=9.0.0
   como dependencia obrigatoria, entao o gerador de cartao mora no ambiente
   isolado de §2.1, nao aqui. NumPy e SciPy sao seguros (SciPy depende apenas
   de NumPy), mas nada impede um futuro `pip install pillow` virar dependencia
   declarada por engano.
2. O pacote agente_ia_edu.tri e puro: importa-lo nao pode arrastar
   SQLAlchemy, FastAPI nem agente_ia_edu.db. E essa pureza que permite
   alimentar o motor com respostas simuladas e exigir que ele recupere os
   parametros (spec §8.1).
"""

from __future__ import annotations

import pathlib
import subprocess
import sys
import tomllib
import unittest

_ROOT = pathlib.Path(__file__).resolve().parents[1]
_FORBIDDEN_DISTRIBUTIONS = ("pillow", "opencv", "pdfplumber", "cv2", "reportlab")
_FORBIDDEN_MODULES = ("PIL", "cv2", "sqlalchemy", "fastapi", "agente_ia_edu.db")


class TriDependencyBoundaryTests(unittest.TestCase):
    def test_core_declares_numpy_and_scipy(self):
        raw = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        declared = [item.lower() for item in raw["project"]["dependencies"]]
        self.assertTrue(any(item.startswith("numpy") for item in declared), declared)
        self.assertTrue(any(item.startswith("scipy") for item in declared), declared)

    def test_core_never_declares_pillow_or_opencv(self):
        raw = tomllib.loads((_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
        optional = raw["project"].get("optional-dependencies", {})
        declared = list(raw["project"]["dependencies"])
        for extra in optional.values():
            declared.extend(extra)
        for item in declared:
            for forbidden in _FORBIDDEN_DISTRIBUTIONS:
                self.assertNotIn(
                    forbidden, item.lower(),
                    f"{item!r} traz {forbidden!r} para o core (pyproject.toml:33-34). "
                    "ReportLab conta: ele declara pillow>=9.0.0 como dependencia "
                    "obrigatoria (spec §2.1).",
                )

    def test_importing_the_engine_pulls_no_heavy_or_io_module(self):
        program = (
            "import sys\n"
            "import agente_ia_edu.tri\n"
            "print(','.join(sorted(m for m in sys.modules if m in "
            f"{_FORBIDDEN_MODULES!r} or m.startswith('agente_ia_edu.db'))))\n"
        )
        completed = subprocess.run(
            [sys.executable, "-c", program],
            cwd=_ROOT, capture_output=True, text=True, check=False,
        )
        self.assertEqual(completed.returncode, 0, completed.stderr)
        self.assertEqual(completed.stdout.strip(), "", completed.stdout)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tri_dependency_boundary.py -v`
Expected: FAIL — `test_core_declares_numpy_and_scipy` falha (nenhuma entrada `numpy`) e `test_importing_the_engine_pulls_no_heavy_or_io_module` falha com `ModuleNotFoundError: No module named 'agente_ia_edu.tri'`.

- [ ] **Step 3: Declare as dependências**

Em `pyproject.toml`, dentro de `[project] dependencies`, após a linha `"openpyxl>=3.1,<4.0",`:

```toml
	# TRI (spec 2026-09-29 §2.2): o motor e numerico puro. NumPy e SciPy nao
	# arrastam Pillow (SciPy depende apenas de NumPy), entao a garantia do
	# comentario do extra "recovery" acima - core sem Pillow, para nao quebrar
	# page.images do pypdf - continua valendo. OpenCV NAO entra aqui: visao
	# computacional e exclusividade do omr_worker (spec §2.3).
	"numpy>=2.1,<3.0",
	"scipy>=1.14,<2.0",
```

- [ ] **Step 4: Instale e crie o esqueleto do pacote**

```bash
.venv/bin/pip install -e '.[dev]'
mkdir -p src/agente_ia_edu/tri
```

`src/agente_ia_edu/tri/__init__.py`:

```python
"""Motor de TRI puro (spec 2026-09-29 §2.2).

Entra matriz NumPy, saem parametros de item e thetas. Este pacote nao conhece
banco de dados, nao conhece aluno e nao conhece HTTP; a traducao banco<->matriz
vive em ``agente_ia_edu.services.tri_calibration``.

Essa fronteira nao e estetica: um motor de TRI errado nao lanca excecao e nao
quebra teste de integracao - ele devolve numeros plausiveis e errados que viram
nota de aluno (spec §8.1). Manter o motor puro e o que permite alimenta-lo com
respostas simuladas a partir de parametros conhecidos e exigir que ele os
recupere.
"""
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_dependency_boundary.py -v`
Expected: PASS (3 testes)

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml src/agente_ia_edu/tri/__init__.py tests/test_tri_dependency_boundary.py
git commit -m "feat: adiciona NumPy/SciPy ao core e o esqueleto do pacote tri

Guarda automatizada: o core continua sem Pillow/OpenCV e o pacote tri
continua sem SQLAlchemy/FastAPI/agente_ia_edu.db."
```

---

### Task 2: Primitivas do modelo 2PL

**Files:**
- Create: `src/agente_ia_edu/tri/model.py`
- Test: `tests/test_tri_model.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `probability_2pl(theta, a, b) -> np.ndarray` — `1 / (1 + exp(-a(theta - b)))`, difunde (broadcast) normalmente.
  - `ItemParameters(a: float | None, b: float | None, se_a: float | None = None, se_b: float | None = None, is_fixed: bool = False, flags: tuple[str, ...] = ())` — dataclass frozen. `a is None` significa "não estimável" (item sem variância), nunca "zero".
  - `ResponseMatrix(values: np.ndarray)` — dataclass frozen `eq=False`, shape `(n_examinees, n_items)`, valores `0.0`, `1.0` ou `nan` (item não apresentado). Propriedades `n_examinees`, `n_items`, `observed`; métodos `item_p_values()`, `zero_variance_items()`, `raw_correct()`.
  - `ResponseMatrixError(ValueError)`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tri_model.py
"""Primitivas do modelo 2PL (spec §6.1)."""

from __future__ import annotations

import unittest

import numpy as np

from agente_ia_edu.tri.model import (
    ItemParameters,
    ResponseMatrix,
    ResponseMatrixError,
    probability_2pl,
)


class Probability2PLTests(unittest.TestCase):
    def test_probability_is_one_half_at_the_difficulty(self):
        self.assertAlmostEqual(float(probability_2pl(0.7, 1.3, 0.7)), 0.5, places=12)

    def test_higher_theta_gives_higher_probability(self):
        values = probability_2pl(np.array([-2.0, 0.0, 2.0]), 1.0, 0.0)
        self.assertTrue(np.all(np.diff(values) > 0))

    def test_discrimination_steepens_the_curve(self):
        flat = float(probability_2pl(1.0, 0.3, 0.0))
        steep = float(probability_2pl(1.0, 2.5, 0.0))
        self.assertGreater(steep, flat)

    def test_negative_discrimination_inverts_the_curve(self):
        values = probability_2pl(np.array([-2.0, 0.0, 2.0]), -1.0, 0.0)
        self.assertTrue(np.all(np.diff(values) < 0))

    def test_extreme_theta_never_overflows(self):
        values = probability_2pl(np.array([-500.0, 500.0]), 4.0, 0.0)
        self.assertTrue(np.all(np.isfinite(values)))
        self.assertTrue(np.all((values >= 0.0) & (values <= 1.0)))

    def test_broadcasts_items_against_nodes(self):
        nodes = np.linspace(-3.0, 3.0, 7)
        a = np.array([1.0, 0.5])
        b = np.array([0.0, 1.0])
        grid = probability_2pl(nodes[None, :], a[:, None], b[:, None])
        self.assertEqual(grid.shape, (2, 7))


class ResponseMatrixTests(unittest.TestCase):
    def _matrix(self) -> ResponseMatrix:
        return ResponseMatrix(np.array([
            [1.0, 0.0, 1.0, 1.0],
            [1.0, 1.0, 1.0, 0.0],
            [0.0, 0.0, 1.0, np.nan],
            [1.0, 0.0, 1.0, 1.0],
        ]))

    def test_reports_shape(self):
        matrix = self._matrix()
        self.assertEqual(matrix.n_examinees, 4)
        self.assertEqual(matrix.n_items, 4)

    def test_p_values_ignore_missing_responses(self):
        # item 3 (indice 3): observados [1, 0, 1] -> 2/3
        values = self._matrix().item_p_values()
        np.testing.assert_allclose(values[:3], [0.75, 0.25, 1.0])
        self.assertAlmostEqual(float(values[3]), 2.0 / 3.0)

    def test_zero_variance_items_are_flagged(self):
        flags = self._matrix().zero_variance_items()
        # item 2 foi acertado por todos -> sem variancia
        np.testing.assert_array_equal(flags, [False, False, True, False])

    def test_raw_correct_counts_blanks_as_wrong_and_skips_missing(self):
        np.testing.assert_array_equal(self._matrix().raw_correct(), [3, 3, 1, 3])

    def test_rejects_a_matrix_that_is_not_two_dimensional(self):
        with self.assertRaises(ResponseMatrixError):
            ResponseMatrix(np.array([1.0, 0.0, 1.0]))

    def test_rejects_values_outside_zero_one_and_nan(self):
        with self.assertRaises(ResponseMatrixError):
            ResponseMatrix(np.array([[1.0, 0.5], [0.0, 1.0]]))

    def test_rejects_an_empty_matrix(self):
        with self.assertRaises(ResponseMatrixError):
            ResponseMatrix(np.zeros((0, 3)))


class ItemParametersTests(unittest.TestCase):
    def test_defaults_are_unestimated_and_unflagged(self):
        parameters = ItemParameters(a=1.2, b=-0.4)
        self.assertIsNone(parameters.se_a)
        self.assertIsNone(parameters.se_b)
        self.assertFalse(parameters.is_fixed)
        self.assertEqual(parameters.flags, ())

    def test_none_means_not_estimable_not_zero(self):
        parameters = ItemParameters(a=None, b=None, flags=("NO_VARIANCE",))
        self.assertIsNone(parameters.a)
        self.assertIn("NO_VARIANCE", parameters.flags)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tri_model.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.tri.model'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agente_ia_edu/tri/model.py
"""Primitivas do modelo logistico de dois parametros (spec §6.1).

P(acerto | theta) = 1 / (1 + exp(-a(theta - b)))

Metrica logistica pura, sem o fator de escala D = 1.7: e a mesma metrica que o
``mirt`` (R) reporta com ``IRTpars = TRUE``, o que torna o conjunto-ouro da
Tarefa 11 comparavel sem conversao.
"""

from __future__ import annotations

from dataclasses import dataclass, field

import numpy as np

# exp(35) ~ 1.6e15: ja satura a probabilidade em 1.0 dentro da precisao de
# float64 sem chegar perto do overflow, entao o clip nao muda resultado nenhum
# e elimina o RuntimeWarning de overflow em thetas extremos.
_EXPONENT_LIMIT = 35.0


class ResponseMatrixError(ValueError):
    """A matriz de respostas e estruturalmente invalida e nao pode ser calibrada."""


def probability_2pl(theta, a, b) -> np.ndarray:
    """Probabilidade de acerto sob o 2PL. Difunde theta, a e b normalmente."""
    z = np.asarray(a, dtype=float) * (
        np.asarray(theta, dtype=float) - np.asarray(b, dtype=float)
    )
    return 1.0 / (1.0 + np.exp(-np.clip(z, -_EXPONENT_LIMIT, _EXPONENT_LIMIT)))


@dataclass(frozen=True)
class ItemParameters:
    """Parametros calibrados de um item.

    ``a`` e ``b`` sao ``None`` quando o item nao e estimavel (sem variancia de
    resposta). ``None`` nao e zero: um zero silencioso viraria uma curva
    caracteristica plana de aparencia legitima, e portanto nota de aluno.
    """

    a: float | None
    b: float | None
    se_a: float | None = None
    se_b: float | None = None
    is_fixed: bool = False
    flags: tuple[str, ...] = ()


@dataclass(frozen=True, eq=False)
class ResponseMatrix:
    """Matriz (respondentes x itens) de 0.0 / 1.0 / nan.

    Branco conta como erro (0.0) - ``mock_exam_responses.is_correct`` ja chega
    resolvido assim. ``nan`` significa "item nao apresentado a esse
    respondente", nao "errou": um item nao apresentado nao pode contribuir com
    verossimilhanca nenhuma.

    ``eq=False`` porque a igualdade gerada por dataclass compararia ndarrays e
    produziria "truth value of an array is ambiguous".
    """

    values: np.ndarray

    def __post_init__(self) -> None:
        values = np.asarray(self.values, dtype=float)
        if values.ndim != 2:
            raise ResponseMatrixError(
                f"A matriz de respostas precisa ser 2-D; recebido ndim={values.ndim}"
            )
        if values.shape[0] == 0 or values.shape[1] == 0:
            raise ResponseMatrixError(
                f"A matriz de respostas esta vazia (shape={values.shape})"
            )
        finite = values[~np.isnan(values)]
        if finite.size and not np.all(np.isin(finite, (0.0, 1.0))):
            raise ResponseMatrixError(
                "A matriz de respostas so aceita 0.0, 1.0 ou nan"
            )
        object.__setattr__(self, "values", values)

    @property
    def n_examinees(self) -> int:
        return int(self.values.shape[0])

    @property
    def n_items(self) -> int:
        return int(self.values.shape[1])

    @property
    def observed(self) -> np.ndarray:
        """Máscara booleana (respondentes x itens): True onde houve resposta."""
        return ~np.isnan(self.values)

    def _observed_counts_and_correct(self) -> tuple[np.ndarray, np.ndarray]:
        observed = self.observed
        counts = observed.sum(axis=0).astype(float)
        correct = np.where(observed, self.values, 0.0).sum(axis=0)
        return counts, correct

    def item_p_values(self) -> np.ndarray:
        """Proporcao de acerto por item, sobre quem respondeu. nan se ninguem respondeu."""
        counts, correct = self._observed_counts_and_correct()
        with np.errstate(invalid="ignore", divide="ignore"):
            return np.where(counts > 0, correct / counts, np.nan)

    def zero_variance_items(self) -> np.ndarray:
        """True para itens que ninguem errou, que ninguem acertou, ou com menos de
        duas respostas. Nenhum deles tem ``b`` finito, e tentar estima-los devolve
        o valor da fronteira do otimizador - plausivel e errado."""
        counts, correct = self._observed_counts_and_correct()
        return (counts < 2) | (correct == 0.0) | (correct == counts)

    def raw_correct(self) -> np.ndarray:
        """Numero de acertos por respondente (inteiros)."""
        return np.where(self.observed, self.values, 0.0).sum(axis=1).astype(int)


__all__ = [
    "ItemParameters",
    "ResponseMatrix",
    "ResponseMatrixError",
    "probability_2pl",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_model.py -v`
Expected: PASS (13 testes)

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/tri/model.py tests/test_tri_model.py
git commit -m "feat: primitivas do modelo 2PL (probabilidade, ItemParameters, ResponseMatrix)"
```

---

### Task 3: Quadratura gaussiana de 41 pontos

**Files:**
- Create: `src/agente_ia_edu/tri/quadrature.py`
- Test: `tests/test_tri_quadrature.py`

**Interfaces:**
- Consumes: nada.
- Produces:
  - `QUADRATURE_POINTS = 41`, `THETA_LIMIT = 4.0`
  - `gaussian_quadrature(points: int = QUADRATURE_POINTS, limit: float = THETA_LIMIT, mean: float = 0.0, sd: float = 1.0) -> tuple[np.ndarray, np.ndarray]` — nós igualmente espaçados em `[mean - limit*sd, mean + limit*sd]` e pesos da densidade N(mean, sd²) normalizados para somar 1.
  - `prior_weights(nodes: np.ndarray, mean: float, sd: float) -> np.ndarray` — repesa uma grade **já existente** sob N(mean, sd²). É o que a calibragem equalizada usa para atualizar a distribuição populacional sem mover os nós (spec §6.1).

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tri_quadrature.py
"""Grade de quadratura do passo E (spec §6.1).

41 pontos. Os pesos saem do prior N(0,1) na calibragem livre, e sao repesados
sob N(mu, sigma^2) - com a grade parada - na calibragem equalizada.
"""

from __future__ import annotations

import unittest

import numpy as np

from agente_ia_edu.tri.quadrature import (
    QUADRATURE_POINTS,
    THETA_LIMIT,
    gaussian_quadrature,
    prior_weights,
)


class GaussianQuadratureTests(unittest.TestCase):
    def test_spec_default_is_forty_one_points(self):
        self.assertEqual(QUADRATURE_POINTS, 41)
        nodes, weights = gaussian_quadrature()
        self.assertEqual(nodes.shape, (41,))
        self.assertEqual(weights.shape, (41,))

    def test_nodes_span_the_declared_limit_symmetrically(self):
        nodes, _ = gaussian_quadrature()
        self.assertAlmostEqual(float(nodes[0]), -THETA_LIMIT)
        self.assertAlmostEqual(float(nodes[-1]), THETA_LIMIT)
        self.assertAlmostEqual(float(nodes[20]), 0.0)

    def test_weights_form_a_probability_distribution(self):
        _, weights = gaussian_quadrature()
        self.assertAlmostEqual(float(weights.sum()), 1.0, places=12)
        self.assertTrue(np.all(weights > 0))

    def test_weights_recover_the_standard_normal_moments(self):
        nodes, weights = gaussian_quadrature()
        mean = float(weights @ nodes)
        variance = float(weights @ (nodes ** 2)) - mean ** 2
        self.assertAlmostEqual(mean, 0.0, places=12)
        self.assertAlmostEqual(variance, 1.0, places=3)

    def test_prior_can_be_shifted_and_scaled(self):
        nodes, weights = gaussian_quadrature(mean=0.5, sd=2.0)
        mean = float(weights @ nodes)
        variance = float(weights @ (nodes ** 2)) - mean ** 2
        self.assertAlmostEqual(mean, 0.5, places=10)
        self.assertAlmostEqual(variance, 4.0, places=2)

    def test_is_deterministic(self):
        first_nodes, first_weights = gaussian_quadrature()
        second_nodes, second_weights = gaussian_quadrature()
        np.testing.assert_array_equal(first_nodes, second_nodes)
        np.testing.assert_array_equal(first_weights, second_weights)


class PriorWeightTests(unittest.TestCase):
    """Repesagem de uma grade FIXA, usada pela calibragem equalizada (spec §6.1).

    Mover os nos a cada iteracao mudaria a grade sob os itens travados, que estao
    na escala antiga - a atualizacao da populacao mexe no PESO, nunca na posicao.
    """

    def test_reproduces_the_default_weights_for_a_standard_normal(self):
        nodes, weights = gaussian_quadrature()
        np.testing.assert_allclose(prior_weights(nodes, 0.0, 1.0), weights, atol=1e-15)

    def test_weights_still_sum_to_one_after_a_shift(self):
        nodes, _ = gaussian_quadrature()
        self.assertAlmostEqual(float(prior_weights(nodes, 0.6, 1.0).sum()), 1.0, places=12)

    def test_a_shifted_prior_recovers_its_own_mean_on_the_fixed_grid(self):
        nodes, _ = gaussian_quadrature()
        weights = prior_weights(nodes, 0.6, 1.0)
        self.assertAlmostEqual(float(weights @ nodes), 0.6, places=4)

    def test_a_wider_prior_recovers_its_own_spread_on_the_fixed_grid(self):
        nodes, _ = gaussian_quadrature()
        weights = prior_weights(nodes, 0.0, 1.3)
        mean = float(weights @ nodes)
        variance = float(weights @ (nodes ** 2)) - mean ** 2
        self.assertAlmostEqual(variance, 1.69, delta=0.05)

    def test_the_grid_is_never_moved(self):
        nodes, _ = gaussian_quadrature()
        before = nodes.copy()
        prior_weights(nodes, 1.0, 2.0)
        np.testing.assert_array_equal(nodes, before)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tri_quadrature.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.tri.quadrature'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agente_ia_edu/tri/quadrature.py
"""Grade de quadratura sobre theta para o passo E do Bock-Aitkin (spec §6.1).

Duas funcoes, porque §6.1 tem dois modos. ``gaussian_quadrature`` constroi a
grade e os pesos de um prior dado - e a calibragem livre, com N(0,1) por padrao.
``prior_weights`` repesa uma grade JA EXISTENTE - e a calibragem equalizada, que
atualiza a distribuicao da populacao a cada iteracao sem nunca mover os nos.

Nos igualmente espacados com pesos proporcionais a densidade do prior,
normalizados para somar 1 - a forma classica do Bock-Aitkin, e a mesma que o
``mirt`` usa com ``quadpts``. Nao e Gauss-Hermite: os nos do Gauss-Hermite
mudam de posicao com o numero de pontos, o que tornaria a comparacao entre
calibragens de configuracoes diferentes desnecessariamente opaca.

O limite de +-4 desvios-padrao cobre 99,994% da massa do prior N(0,1). Ampliar
para +-6 nao muda parametro nenhum de forma mensuravel e gasta nos em regiao
com peso da ordem de 1e-9.

Na calibragem equalizada a grade fica parada em [-4, +4] enquanto a media da
populacao se desloca. Um deslocamento de +-1 logit - ja bem maior do que a
diferenca tipica entre duas turmas - ainda deixa a cauda curta coberta ate 3
desvios, o que e folga suficiente. Deslocamento muito alem disso e sinal de
ancora ruim, nao de grade curta, e quem trata disso e a verificacao de deriva
da Fase 5.
"""

from __future__ import annotations

import numpy as np
from scipy.stats import norm

QUADRATURE_POINTS = 41
THETA_LIMIT = 4.0


def gaussian_quadrature(
    points: int = QUADRATURE_POINTS,
    limit: float = THETA_LIMIT,
    mean: float = 0.0,
    sd: float = 1.0,
) -> tuple[np.ndarray, np.ndarray]:
    """Devolve ``(nodes, weights)``; ``weights`` soma exatamente 1."""
    if points < 3:
        raise ValueError(f"A quadratura precisa de ao menos 3 pontos; recebido {points}")
    if sd <= 0.0:
        raise ValueError(f"O desvio-padrao do prior precisa ser positivo; recebido {sd}")
    nodes = np.linspace(mean - limit * sd, mean + limit * sd, points)
    return nodes, prior_weights(nodes, mean, sd)


def prior_weights(nodes: np.ndarray, mean: float, sd: float) -> np.ndarray:
    """Repesa uma grade JA EXISTENTE sob N(mean, sd^2). Soma exatamente 1.

    E o que a calibragem equalizada usa para atualizar a distribuicao
    populacional (spec §6.1). A grade nao se move: os itens travados estao na
    escala antiga, e deslocar os nos sob eles mudaria o significado dos proprios
    parametros que a equalizacao mantem fixos.
    """
    if sd <= 0.0:
        raise ValueError(f"O desvio-padrao do prior precisa ser positivo; recebido {sd}")
    density = norm.pdf(np.asarray(nodes, dtype=float), loc=mean, scale=sd)
    return density / density.sum()


__all__ = [
    "QUADRATURE_POINTS",
    "THETA_LIMIT",
    "gaussian_quadrature",
    "prior_weights",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_quadrature.py -v`
Expected: PASS (11 testes)

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/tri/quadrature.py tests/test_tri_quadrature.py
git commit -m "feat: quadratura de 41 pontos e repesagem do prior populacional"
```

---

### Task 4: Posterior na grade e estimativa de theta por EAP

**Files:**
- Create: `src/agente_ia_edu/tri/theta.py`
- Test: `tests/test_tri_theta.py`

**Interfaces:**
- Consumes: `probability_2pl`, `ResponseMatrix` (Tarefa 2); `gaussian_quadrature` (Tarefa 3).
- Produces:
  - `log_likelihood_grid(values: np.ndarray, a: np.ndarray, b: np.ndarray, nodes: np.ndarray) -> np.ndarray` — shape `(n_examinees, n_nodes)`.
  - `posterior_over_grid(values, a, b, nodes, weights) -> np.ndarray` — shape `(n_examinees, n_nodes)`, cada linha soma 1.
  - `marginal_log_likelihood(values, a, b, nodes, weights) -> float`.
  - `ThetaEstimates(theta: np.ndarray, se: np.ndarray)` — dataclass frozen `eq=False`.
  - `estimate_eap(values, a, b, nodes=None, weights=None) -> ThetaEstimates`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tri_theta.py
"""Posterior na grade e EAP (spec §6.2).

EAP, nao MLE: o MLE diverge para +-infinito para quem gabaritou ou zerou, e com
400-1.500 alunos por simulado esses casos aparecem em toda aplicacao.
"""

from __future__ import annotations

import unittest

import numpy as np

from agente_ia_edu.tri.quadrature import gaussian_quadrature
from agente_ia_edu.tri.theta import (
    ThetaEstimates,
    estimate_eap,
    log_likelihood_grid,
    marginal_log_likelihood,
    posterior_over_grid,
)

A = np.array([1.2, 0.9, 1.5, 1.1, 0.8])
B = np.array([-1.0, -0.5, 0.0, 0.5, 1.0])


class LogLikelihoodGridTests(unittest.TestCase):
    def test_shape_is_examinees_by_nodes(self):
        nodes, _ = gaussian_quadrature()
        values = np.array([[1.0, 1.0, 0.0, 0.0, 0.0], [1.0, 1.0, 1.0, 1.0, 1.0]])
        self.assertEqual(log_likelihood_grid(values, A, B, nodes).shape, (2, 41))

    def test_a_single_correct_answer_matches_the_closed_form(self):
        nodes = np.array([-1.0, 0.0, 1.0])
        values = np.array([[1.0]])
        grid = log_likelihood_grid(values, np.array([1.0]), np.array([0.0]), nodes)
        expected = np.log(1.0 / (1.0 + np.exp(-(nodes - 0.0))))
        np.testing.assert_allclose(grid[0], expected, atol=1e-12)

    def test_missing_responses_contribute_nothing(self):
        nodes, _ = gaussian_quadrature()
        answered = np.array([[1.0, 0.0, np.nan, np.nan, np.nan]])
        shorter = np.array([[1.0, 0.0]])
        full = log_likelihood_grid(answered, A, B, nodes)
        partial = log_likelihood_grid(shorter, A[:2], B[:2], nodes)
        np.testing.assert_allclose(full, partial, atol=1e-12)


class PosteriorTests(unittest.TestCase):
    def test_every_row_is_a_probability_distribution(self):
        nodes, weights = gaussian_quadrature()
        values = np.array([
            [1.0, 1.0, 1.0, 1.0, 1.0],
            [0.0, 0.0, 0.0, 0.0, 0.0],
            [1.0, 0.0, 1.0, 0.0, 1.0],
        ])
        posterior = posterior_over_grid(values, A, B, nodes, weights)
        np.testing.assert_allclose(posterior.sum(axis=1), np.ones(3), atol=1e-12)
        self.assertTrue(np.all(posterior >= 0.0))

    def test_an_examinee_with_no_answers_reproduces_the_prior(self):
        nodes, weights = gaussian_quadrature()
        values = np.full((1, 5), np.nan)
        posterior = posterior_over_grid(values, A, B, nodes, weights)
        np.testing.assert_allclose(posterior[0], weights, atol=1e-12)

    def test_marginal_log_likelihood_is_finite_and_negative(self):
        nodes, weights = gaussian_quadrature()
        values = np.array([[1.0, 1.0, 0.0, 0.0, 0.0], [1.0, 0.0, 1.0, 0.0, 1.0]])
        value = marginal_log_likelihood(values, A, B, nodes, weights)
        self.assertTrue(np.isfinite(value))
        self.assertLess(value, 0.0)


class EapTests(unittest.TestCase):
    def test_returns_theta_and_standard_error_per_examinee(self):
        values = np.array([[1.0, 1.0, 0.0, 0.0, 0.0], [1.0, 1.0, 1.0, 1.0, 0.0]])
        estimates = estimate_eap(values, A, B)
        self.assertIsInstance(estimates, ThetaEstimates)
        self.assertEqual(estimates.theta.shape, (2,))
        self.assertEqual(estimates.se.shape, (2,))

    def test_more_correct_answers_give_a_higher_theta(self):
        values = np.array([
            [0.0, 0.0, 0.0, 0.0, 0.0],
            [1.0, 1.0, 0.0, 0.0, 0.0],
            [1.0, 1.0, 1.0, 1.0, 1.0],
        ])
        theta = estimate_eap(values, A, B).theta
        self.assertTrue(np.all(np.diff(theta) > 0))

    def test_perfect_and_zero_scores_stay_finite(self):
        values = np.array([[1.0] * 5, [0.0] * 5])
        estimates = estimate_eap(values, A, B)
        self.assertTrue(np.all(np.isfinite(estimates.theta)))
        self.assertTrue(np.all(np.isfinite(estimates.se)))
        self.assertTrue(np.all(estimates.se > 0.0))

    def test_an_examinee_with_no_answers_returns_the_prior_mean(self):
        estimates = estimate_eap(np.full((1, 5), np.nan), A, B)
        self.assertAlmostEqual(float(estimates.theta[0]), 0.0, places=10)
        self.assertAlmostEqual(float(estimates.se[0]), 1.0, places=2)

    def test_more_items_shrink_the_standard_error(self):
        short = estimate_eap(np.array([[1.0, 0.0, np.nan, np.nan, np.nan]]), A, B)
        full = estimate_eap(np.array([[1.0, 0.0, 1.0, 0.0, 1.0]]), A, B)
        self.assertLess(float(full.se[0]), float(short.se[0]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tri_theta.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.tri.theta'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agente_ia_edu/tri/theta.py
"""Posterior de theta na grade de quadratura e estimativa EAP (spec §6.2).

EAP e nao MLE por decisao registrada na spec: o MLE diverge para +-infinito
para quem acertou tudo ou errou tudo, e com 400-1.500 alunos por simulado esses
casos aparecem em toda aplicacao. O EAP devolve estimativa finita e erro-padrao
para todos, ao custo de uma leve regressao a media nos extremos.

Este modulo tambem serve ao passo E da calibragem: ``posterior_over_grid`` e
exatamente a distribuicao a posteriori que o Bock-Aitkin usa para acumular as
contagens esperadas por item.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .model import probability_2pl
from .quadrature import gaussian_quadrature

# Piso/teto de probabilidade antes do log. Sem isso, um item com |a| alto e
# theta longe de b produz P exatamente 0.0 ou 1.0 em float64, e log(0) = -inf
# contamina a linha inteira do respondente.
_PROBABILITY_FLOOR = 1e-12


@dataclass(frozen=True, eq=False)
class ThetaEstimates:
    """Theta e erro-padrao a posteriori, um por respondente."""

    theta: np.ndarray
    se: np.ndarray


def _item_log_probabilities(
    a: np.ndarray, b: np.ndarray, nodes: np.ndarray
) -> tuple[np.ndarray, np.ndarray]:
    probability = probability_2pl(nodes[None, :], np.asarray(a, float)[:, None], np.asarray(b, float)[:, None])
    probability = np.clip(probability, _PROBABILITY_FLOOR, 1.0 - _PROBABILITY_FLOOR)
    return np.log(probability), np.log1p(-probability)


def log_likelihood_grid(
    values: np.ndarray, a: np.ndarray, b: np.ndarray, nodes: np.ndarray
) -> np.ndarray:
    """Log-verossimilhanca de cada respondente em cada no. Shape (n_ex, n_nodes)."""
    values = np.asarray(values, dtype=float)
    observed = ~np.isnan(values)
    correct = np.where(observed, values, 0.0)
    wrong = observed.astype(float) - correct
    log_p, log_q = _item_log_probabilities(a, b, nodes)
    return correct @ log_p + wrong @ log_q


def posterior_over_grid(
    values: np.ndarray,
    a: np.ndarray,
    b: np.ndarray,
    nodes: np.ndarray,
    weights: np.ndarray,
) -> np.ndarray:
    """Distribuicao a posteriori de theta por respondente, normalizada por linha."""
    log_likelihood = log_likelihood_grid(values, a, b, nodes)
    # Subtrair o maximo por linha antes de exponenciar: a verossimilhanca de 90
    # itens fica na casa de e^-120, que e 0.0 em float64 sem essa normalizacao,
    # e a linha inteira viraria 0/0.
    shifted = log_likelihood - log_likelihood.max(axis=1, keepdims=True)
    unnormalized = np.exp(shifted) * weights[None, :]
    return unnormalized / unnormalized.sum(axis=1, keepdims=True)


def marginal_log_likelihood(
    values: np.ndarray,
    a: np.ndarray,
    b: np.ndarray,
    nodes: np.ndarray,
    weights: np.ndarray,
) -> float:
    """Log-verossimilhanca marginal da amostra; e ela que o EM maximiza."""
    log_likelihood = log_likelihood_grid(values, a, b, nodes)
    peak = log_likelihood.max(axis=1, keepdims=True)
    integral = (np.exp(log_likelihood - peak) * weights[None, :]).sum(axis=1)
    return float((peak.ravel() + np.log(integral)).sum())


def estimate_eap(
    values: np.ndarray,
    a: np.ndarray,
    b: np.ndarray,
    nodes: np.ndarray | None = None,
    weights: np.ndarray | None = None,
) -> ThetaEstimates:
    """Media e desvio-padrao a posteriori de theta, por respondente."""
    if nodes is None or weights is None:
        nodes, weights = gaussian_quadrature()
    posterior = posterior_over_grid(values, a, b, nodes, weights)
    theta = posterior @ nodes
    variance = posterior @ (nodes ** 2) - theta ** 2
    return ThetaEstimates(theta=theta, se=np.sqrt(np.maximum(variance, 0.0)))


__all__ = [
    "ThetaEstimates",
    "estimate_eap",
    "log_likelihood_grid",
    "marginal_log_likelihood",
    "posterior_over_grid",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_theta.py -v`
Expected: PASS (11 testes)

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/tri/theta.py tests/test_tri_theta.py
git commit -m "feat: posterior na grade de quadratura e estimativa de theta por EAP"
```

---

### Task 5: Flags de item e portão de publicação

**Files:**
- Create: `src/agente_ia_edu/tri/quality.py`
- Test: `tests/test_tri_quality.py`

**Interfaces:**
- Consumes: nada (deliberadamente: recebe escalares, não `CalibrationResult`, para que `calibration.py` possa importar `quality.py` sem ciclo).
- Produces:
  - Constantes de flag: `NEGATIVE_DISCRIMINATION = "NEGATIVE_DISCRIMINATION"`, `LOW_DISCRIMINATION = "LOW_DISCRIMINATION"`, `DEGENERATE_ITEM = "DEGENERATE_ITEM"`, `NO_VARIANCE = "NO_VARIANCE"`.
  - Limiares: `LOW_DISCRIMINATION_THRESHOLD = 0.2`, `DEGENERATE_LOW_P_VALUE = 0.05`, `DEGENERATE_HIGH_P_VALUE = 0.95`, `MINIMUM_EXAMINEES_FOR_2PL = 200`.
  - Constantes de bloqueio: `NOT_CONVERGED = "NOT_CONVERGED"`, `SCALE_NOT_VERIFIED = "SCALE_NOT_VERIFIED"`, `FLAGGED_ITEMS = "FLAGGED_ITEMS"`.
  - `item_flags(*, a: float | None, p_value: float | None) -> tuple[str, ...]`.
  - `PublicationDecision(can_publish: bool, is_provisional: bool, blockers: tuple[str, ...], warnings: tuple[str, ...])` — dataclass frozen.
  - `publication_gate(*, converged: bool, scale_verified: bool, item_flags_by_position: Mapping[int, tuple[str, ...]]) -> PublicationDecision`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tri_quality.py
"""Portoes de qualidade da calibragem (spec §6.5 e §6.3.1)."""

from __future__ import annotations

import unittest

from agente_ia_edu.tri.quality import (
    DEGENERATE_ITEM,
    FLAGGED_ITEMS,
    LOW_DISCRIMINATION,
    MINIMUM_EXAMINEES_FOR_2PL,
    NEGATIVE_DISCRIMINATION,
    NOT_CONVERGED,
    NO_VARIANCE,
    SCALE_NOT_VERIFIED,
    PublicationDecision,
    item_flags,
    publication_gate,
)


class ItemFlagTests(unittest.TestCase):
    def test_a_healthy_item_carries_no_flag(self):
        self.assertEqual(item_flags(a=1.1, p_value=0.55), ())

    def test_negative_discrimination_is_the_wrong_answer_key_signature(self):
        flags = item_flags(a=-0.8, p_value=0.30)
        self.assertIn(NEGATIVE_DISCRIMINATION, flags)

    def test_negative_discrimination_also_counts_as_low(self):
        flags = item_flags(a=-0.8, p_value=0.30)
        self.assertIn(LOW_DISCRIMINATION, flags)

    def test_low_discrimination_is_flagged_below_the_spec_threshold(self):
        self.assertIn(LOW_DISCRIMINATION, item_flags(a=0.19, p_value=0.5))
        self.assertNotIn(LOW_DISCRIMINATION, item_flags(a=0.2, p_value=0.5))

    def test_degenerate_items_are_flagged_at_both_extremes(self):
        self.assertIn(DEGENERATE_ITEM, item_flags(a=1.0, p_value=0.04))
        self.assertIn(DEGENERATE_ITEM, item_flags(a=1.0, p_value=0.96))
        self.assertNotIn(DEGENERATE_ITEM, item_flags(a=1.0, p_value=0.05))
        self.assertNotIn(DEGENERATE_ITEM, item_flags(a=1.0, p_value=0.95))

    def test_an_unestimable_item_is_flagged_as_having_no_variance(self):
        flags = item_flags(a=None, p_value=1.0)
        self.assertIn(NO_VARIANCE, flags)
        self.assertIn(DEGENERATE_ITEM, flags)

    def test_flags_are_sorted_so_persisted_json_is_comparable(self):
        flags = item_flags(a=-0.8, p_value=0.02)
        self.assertEqual(list(flags), sorted(flags))


class PublicationGateTests(unittest.TestCase):
    def test_spec_minimum_sample_for_2pl_is_two_hundred(self):
        self.assertEqual(MINIMUM_EXAMINEES_FOR_2PL, 200)

    def test_a_converged_calibration_on_a_verified_scale_publishes(self):
        decision = publication_gate(
            converged=True, scale_verified=True, item_flags_by_position={1: (), 2: ()}
        )
        self.assertIsInstance(decision, PublicationDecision)
        self.assertTrue(decision.can_publish)
        self.assertFalse(decision.is_provisional)
        self.assertEqual(decision.blockers, ())
        self.assertEqual(decision.warnings, ())

    def test_non_convergence_blocks_publication(self):
        decision = publication_gate(
            converged=False, scale_verified=True, item_flags_by_position={}
        )
        self.assertFalse(decision.can_publish)
        self.assertIn(NOT_CONVERGED, decision.blockers)

    def test_an_unverified_scale_blocks_and_marks_the_result_provisional(self):
        decision = publication_gate(
            converged=True, scale_verified=False, item_flags_by_position={}
        )
        self.assertFalse(decision.can_publish)
        self.assertTrue(decision.is_provisional)
        self.assertIn(SCALE_NOT_VERIFIED, decision.blockers)

    def test_flagged_items_warn_without_blocking(self):
        decision = publication_gate(
            converged=True,
            scale_verified=True,
            item_flags_by_position={7: (NEGATIVE_DISCRIMINATION, LOW_DISCRIMINATION)},
        )
        self.assertTrue(decision.can_publish)
        self.assertEqual(decision.blockers, ())
        self.assertIn(f"{FLAGGED_ITEMS}:7:{NEGATIVE_DISCRIMINATION}", decision.warnings)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tri_quality.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.tri.quality'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agente_ia_edu/tri/quality.py
"""Portoes de qualidade da calibragem (spec §6.5) e da publicacao (§6.3.1).

Este modulo recebe escalares, nunca ``CalibrationResult``. E de proposito:
``calibration.py`` importa daqui para carimbar as flags nos itens, e um
``publication_gate`` que dependesse do resultado fecharia o ciclo de import.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

NEGATIVE_DISCRIMINATION = "NEGATIVE_DISCRIMINATION"
LOW_DISCRIMINATION = "LOW_DISCRIMINATION"
DEGENERATE_ITEM = "DEGENERATE_ITEM"
NO_VARIANCE = "NO_VARIANCE"

NOT_CONVERGED = "NOT_CONVERGED"
SCALE_NOT_VERIFIED = "SCALE_NOT_VERIFIED"
FLAGGED_ITEMS = "FLAGGED_ITEMS"

LOW_DISCRIMINATION_THRESHOLD = 0.2
DEGENERATE_LOW_P_VALUE = 0.05
DEGENERATE_HIGH_P_VALUE = 0.95
MINIMUM_EXAMINEES_FOR_2PL = 200


@dataclass(frozen=True)
class PublicationDecision:
    """``can_publish`` falso com ``blockers`` vazio e impossivel por construcao."""

    can_publish: bool
    is_provisional: bool
    blockers: tuple[str, ...]
    warnings: tuple[str, ...]


def item_flags(*, a: float | None, p_value: float | None) -> tuple[str, ...]:
    """Sinaliza sem bloquear (spec §6.5).

    ``NEGATIVE_DISCRIMINATION`` e o achado de maior valor pratico do subsistema:
    aluno forte errando e fraco acertando e a assinatura classica de gabarito
    cadastrado errado, e essa e a verificacao que mais vai evitar prejuizo real.
    """
    flags: set[str] = set()
    if a is None:
        flags.add(NO_VARIANCE)
        flags.add(DEGENERATE_ITEM)
    else:
        if a < 0.0:
            flags.add(NEGATIVE_DISCRIMINATION)
        if a < LOW_DISCRIMINATION_THRESHOLD:
            flags.add(LOW_DISCRIMINATION)
    if p_value is not None:
        if p_value < DEGENERATE_LOW_P_VALUE or p_value > DEGENERATE_HIGH_P_VALUE:
            flags.add(DEGENERATE_ITEM)
    return tuple(sorted(flags))


def publication_gate(
    *,
    converged: bool,
    scale_verified: bool,
    item_flags_by_position: Mapping[int, tuple[str, ...]],
) -> PublicationDecision:
    """Decide se a calibragem pode virar nota publicada.

    Bloqueia: calibragem que nao convergiu (spec §6.5) e regua cujo
    ``reference_verified_at`` e nulo (spec §6.3.1 - numeros de terceira mao nao
    viram nota de aluno em silencio).

    Sinaliza: cada flag de item, com a posicao, para que a coordenacao veja
    exatamente qual questao revisar.
    """
    blockers: list[str] = []
    if not converged:
        blockers.append(NOT_CONVERGED)
    if not scale_verified:
        blockers.append(SCALE_NOT_VERIFIED)
    warnings = tuple(
        f"{FLAGGED_ITEMS}:{position}:{flag}"
        for position in sorted(item_flags_by_position)
        for flag in item_flags_by_position[position]
    )
    return PublicationDecision(
        can_publish=not blockers,
        is_provisional=not scale_verified,
        blockers=tuple(blockers),
        warnings=warnings,
    )


__all__ = [
    "DEGENERATE_HIGH_P_VALUE",
    "DEGENERATE_ITEM",
    "DEGENERATE_LOW_P_VALUE",
    "FLAGGED_ITEMS",
    "LOW_DISCRIMINATION",
    "LOW_DISCRIMINATION_THRESHOLD",
    "MINIMUM_EXAMINEES_FOR_2PL",
    "NEGATIVE_DISCRIMINATION",
    "NOT_CONVERGED",
    "NO_VARIANCE",
    "PublicationDecision",
    "SCALE_NOT_VERIFIED",
    "item_flags",
    "publication_gate",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_quality.py -v`
Expected: PASS (12 testes)

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/tri/quality.py tests/test_tri_quality.py
git commit -m "feat: flags de item e portao de publicacao da calibragem"
```

---

### Task 6: Passo M — ajuste de um item por máxima verossimilhança ponderada

**Files:**
- Create: `src/agente_ia_edu/tri/calibration.py`
- Test: `tests/test_tri_calibration_m_step.py`

**Interfaces:**
- Consumes: `probability_2pl` (Tarefa 2).
- Produces:
  - `A_BOUNDS = (-4.0, 4.0)`, `B_BOUNDS = (-6.0, 6.0)`
  - `item_negative_log_likelihood(params: np.ndarray, nodes, expected_correct, expected_total) -> tuple[float, np.ndarray]` — devolve `(valor, gradiente)`; gradiente na ordem `(a, b)`.
  - `fit_item(nodes, expected_correct, expected_total, *, start_a: float, start_b: float, fix_a: bool = False) -> tuple[float, float]`
  - `item_standard_errors(nodes, expected_total, a: float, b: float) -> tuple[float | None, float | None]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tri_calibration_m_step.py
"""Passo M do Bock-Aitkin: um ajuste 2PL ponderado por item (spec §6.1).

O passo M nao ve respondente nenhum - ele ve, por no de quadratura, o numero
ESPERADO de acertos e o numero ESPERADO de respostas. Testar isso isoladamente
importa porque um erro de sinal no gradiente nao lanca excecao: o otimizador
converge para o parametro errado e devolve um numero plausivel.
"""

from __future__ import annotations

import unittest

import numpy as np
from scipy.optimize import approx_fprime

from agente_ia_edu.tri.calibration import (
    A_BOUNDS,
    B_BOUNDS,
    fit_item,
    item_negative_log_likelihood,
    item_standard_errors,
)
from agente_ia_edu.tri.model import probability_2pl
from agente_ia_edu.tri.quadrature import gaussian_quadrature


def _perfectly_consistent_counts(a: float, b: float, weight: float = 500.0):
    """Contagens esperadas geradas EXATAMENTE pelo 2PL em (a, b).

    Nesse dado, o maximo da verossimilhanca esta em (a, b) por construcao, entao
    o passo M tem que devolver (a, b) - e um teste de identidade, nao de
    tolerancia estatistica.
    """
    nodes, weights = gaussian_quadrature()
    expected_total = weights * weight
    expected_correct = expected_total * probability_2pl(nodes, a, b)
    return nodes, expected_correct, expected_total


class GradientTests(unittest.TestCase):
    def test_analytic_gradient_matches_the_numeric_one(self):
        nodes, correct, total = _perfectly_consistent_counts(1.3, -0.4)
        point = np.array([0.8, 0.2])
        _, analytic = item_negative_log_likelihood(point, nodes, correct, total)
        numeric = approx_fprime(
            point,
            lambda x: item_negative_log_likelihood(x, nodes, correct, total)[0],
            1e-6,
        )
        np.testing.assert_allclose(analytic, numeric, rtol=1e-4, atol=1e-5)

    def test_the_generating_parameters_are_the_minimum(self):
        nodes, correct, total = _perfectly_consistent_counts(1.3, -0.4)
        at_truth, _ = item_negative_log_likelihood(np.array([1.3, -0.4]), nodes, correct, total)
        elsewhere, _ = item_negative_log_likelihood(np.array([0.7, 0.9]), nodes, correct, total)
        self.assertLess(at_truth, elsewhere)


class FitItemTests(unittest.TestCase):
    def test_recovers_the_generating_parameters(self):
        nodes, correct, total = _perfectly_consistent_counts(1.4, -0.6)
        a, b = fit_item(nodes, correct, total, start_a=1.0, start_b=0.0)
        self.assertAlmostEqual(a, 1.4, places=3)
        self.assertAlmostEqual(b, -0.6, places=3)

    def test_recovers_a_negative_discrimination(self):
        nodes, correct, total = _perfectly_consistent_counts(-0.9, 0.3)
        a, b = fit_item(nodes, correct, total, start_a=1.0, start_b=0.0)
        self.assertLess(a, 0.0)
        self.assertAlmostEqual(a, -0.9, places=3)

    def test_rasch_mode_keeps_the_discrimination_fixed(self):
        nodes, correct, total = _perfectly_consistent_counts(1.0, 0.8)
        a, b = fit_item(nodes, correct, total, start_a=1.0, start_b=0.0, fix_a=True)
        self.assertEqual(a, 1.0)
        self.assertAlmostEqual(b, 0.8, places=3)

    def test_the_result_stays_inside_the_declared_bounds(self):
        nodes, correct, total = _perfectly_consistent_counts(3.9, 5.5)
        a, b = fit_item(nodes, correct, total, start_a=1.0, start_b=0.0)
        self.assertGreaterEqual(a, A_BOUNDS[0])
        self.assertLessEqual(a, A_BOUNDS[1])
        self.assertGreaterEqual(b, B_BOUNDS[0])
        self.assertLessEqual(b, B_BOUNDS[1])

    def test_bounds_allow_negative_discrimination(self):
        # Sem isso, a flag de gabarito errado (spec §6.5) nunca dispararia.
        self.assertLess(A_BOUNDS[0], 0.0)


class StandardErrorTests(unittest.TestCase):
    def test_standard_errors_are_positive_and_finite(self):
        nodes, _, total = _perfectly_consistent_counts(1.2, 0.1)
        se_a, se_b = item_standard_errors(nodes, total, 1.2, 0.1)
        self.assertTrue(np.isfinite(se_a) and se_a > 0.0)
        self.assertTrue(np.isfinite(se_b) and se_b > 0.0)

    def test_more_respondents_shrink_the_standard_errors(self):
        nodes, _, small = _perfectly_consistent_counts(1.2, 0.1, weight=200.0)
        _, _, large = _perfectly_consistent_counts(1.2, 0.1, weight=2000.0)
        small_se = item_standard_errors(nodes, small, 1.2, 0.1)
        large_se = item_standard_errors(nodes, large, 1.2, 0.1)
        self.assertLess(large_se[0], small_se[0])
        self.assertLess(large_se[1], small_se[1])

    def test_a_flat_item_has_no_usable_standard_errors(self):
        nodes, _, total = _perfectly_consistent_counts(1.0, 0.0)
        se_a, se_b = item_standard_errors(nodes, total, 0.0, 0.0)
        self.assertIsNone(se_b)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tri_calibration_m_step.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.tri.calibration'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agente_ia_edu/tri/calibration.py
"""Calibragem 2PL por maxima verossimilhanca marginal, algoritmo EM
(Bock-Aitkin), spec §6.1.

Esta tarefa entrega apenas o passo M: dado, por no de quadratura, o numero
esperado de acertos e o numero esperado de respostas de UM item, encontrar o
(a, b) que os explica. O laco EM vem na tarefa seguinte, neste mesmo arquivo.
"""

from __future__ import annotations

import numpy as np
from scipy.optimize import minimize

from .model import probability_2pl

# O limite inferior de ``a`` e NEGATIVO de proposito: item com discriminacao
# negativa e a assinatura de gabarito cadastrado errado (spec §6.5), e um piso
# em zero esconderia exatamente o achado que mais evita prejuizo real.
A_BOUNDS = (-4.0, 4.0)
# +-6 logits cobre com folga o intervalo em que um item ainda discrimina alguem
# dentro da grade de quadratura (+-4); alem disso ``b`` e um numero sem uso.
B_BOUNDS = (-6.0, 6.0)

_PROBABILITY_FLOOR = 1e-12


def item_negative_log_likelihood(
    params: np.ndarray,
    nodes: np.ndarray,
    expected_correct: np.ndarray,
    expected_total: np.ndarray,
) -> tuple[float, np.ndarray]:
    """-log L do item e seu gradiente em (a, b).

    l = sum_q [ r_q log P_q + (n_q - r_q) log(1 - P_q) ], com z_q = a(theta_q - b).
    dl/dz_q = r_q - n_q P_q, dz/da = theta_q - b, dz/db = -a.
    """
    a = float(params[0])
    b = float(params[1])
    probability = np.clip(
        probability_2pl(nodes, a, b), _PROBABILITY_FLOOR, 1.0 - _PROBABILITY_FLOOR
    )
    log_likelihood = float(
        expected_correct @ np.log(probability)
        + (expected_total - expected_correct) @ np.log1p(-probability)
    )
    residual = expected_correct - expected_total * probability
    gradient_a = float(residual @ (nodes - b))
    gradient_b = float(-a * residual.sum())
    return -log_likelihood, np.array([-gradient_a, -gradient_b])


def fit_item(
    nodes: np.ndarray,
    expected_correct: np.ndarray,
    expected_total: np.ndarray,
    *,
    start_a: float,
    start_b: float,
    fix_a: bool = False,
) -> tuple[float, float]:
    """Passo M de um item. ``fix_a=True`` e o modo Rasch: so ``b`` se move."""
    if fix_a:
        fixed_a = float(start_a)

        def objective(x: np.ndarray) -> tuple[float, np.ndarray]:
            value, gradient = item_negative_log_likelihood(
                np.array([fixed_a, float(x[0])]), nodes, expected_correct, expected_total
            )
            return value, gradient[1:]

        result = minimize(
            objective,
            np.array([float(start_b)]),
            jac=True,
            method="L-BFGS-B",
            bounds=[B_BOUNDS],
        )
        return fixed_a, float(result.x[0])

    result = minimize(
        item_negative_log_likelihood,
        np.array([float(start_a), float(start_b)]),
        args=(nodes, expected_correct, expected_total),
        jac=True,
        method="L-BFGS-B",
        bounds=[A_BOUNDS, B_BOUNDS],
    )
    return float(result.x[0]), float(result.x[1])


def item_standard_errors(
    nodes: np.ndarray, expected_total: np.ndarray, a: float, b: float
) -> tuple[float | None, float | None]:
    """Erros-padrao a partir da informacao esperada no otimo do passo M.

    Honestidade sobre o que isso e: a informacao esperada CONDICIONADA as
    contagens do passo E ignora a incerteza da propria distribuicao a
    posteriori de theta, e por isso subestima o erro-padrao MMLE verdadeiro -
    tipicamente em 5% a 15%. E a mesma aproximacao que a maioria das
    implementacoes de referencia usa por padrao. Devolve ``(None, None)``
    quando a informacao e singular (item plano, ou sem massa).
    """
    probability = probability_2pl(nodes, a, b)
    weight = expected_total * probability * (1.0 - probability)
    distance = nodes - b
    information = np.array([
        [float((weight * distance * distance).sum()), float(-a * (weight * distance).sum())],
        [float(-a * (weight * distance).sum()), float(a * a * weight.sum())],
    ])
    try:
        covariance = np.linalg.inv(information)
    except np.linalg.LinAlgError:
        return None, None
    diagonal = np.diag(covariance)
    if not np.all(np.isfinite(diagonal)) or np.any(diagonal <= 0.0):
        return None, None
    return float(np.sqrt(diagonal[0])), float(np.sqrt(diagonal[1]))
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_calibration_m_step.py -v`
Expected: PASS (10 testes)

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/tri/calibration.py tests/test_tri_calibration_m_step.py
git commit -m "feat: passo M do Bock-Aitkin com gradiente analitico e erros-padrao"
```

---

### Task 7: Laço EM — `calibrate()`

**Files:**
- Modify: `src/agente_ia_edu/tri/calibration.py` (acrescenta o laço EM ao arquivo da Tarefa 6)
- Modify: `src/agente_ia_edu/tri/__init__.py`
- Test: `tests/test_tri_calibration_em.py`

**Interfaces:**
- Consumes: `fit_item`, `item_standard_errors` (Tarefa 6); `ResponseMatrix`, `ItemParameters` (Tarefa 2); `gaussian_quadrature` (Tarefa 3); `posterior_over_grid`, `marginal_log_likelihood`, `estimate_eap` (Tarefa 4); `item_flags`, `MINIMUM_EXAMINEES_FOR_2PL` (Tarefa 5).
- Produces:
  - `MAX_ITERATIONS = 500`, `CONVERGENCE_TOLERANCE = 1e-4`, `SUPPORTED_MODELS = ("2PL", "RASCH")`, `MINIMUM_ESTIMABLE_ITEMS = 2`, `MINIMUM_POPULATION_SD = 0.2`, `ENGINE_VERSION = "tri-1.0.0"`
  - `CalibrationError(RuntimeError)`, `InsufficientSampleError(CalibrationError)`
  - `PopulationPrior(mean: float = 0.0, sd: float = 1.0, estimate: bool = False)` — dataclass frozen; e as duas constantes `FIXED_STANDARD_NORMAL = PopulationPrior()` e `ESTIMATED_POPULATION = PopulationPrior(estimate=True)`.
  - `CalibrationResult(model, items, theta, theta_se, converged, iterations, log_likelihood, n_examinees, n_items, population_mean, population_sd, population_estimated, engine_version)` — dataclass frozen `eq=False`; `items` é `tuple[ItemParameters, ...]` **alinhada às colunas originais da matriz**.
  - **Assinatura final de `calibrate` (é ela que a Fase 5 consome):**

```python
def calibrate(
    matrix: ResponseMatrix,
    *,
    model: str = "2PL",
    population: PopulationPrior = FIXED_STANDARD_NORMAL,
    fixed_items: Mapping[int, tuple[float, float]] | None = None,
    max_iterations: int = MAX_ITERATIONS,
    tolerance: float = CONVERGENCE_TOLERANCE,
    minimum_examinees: int = MINIMUM_EXAMINEES_FOR_2PL,
) -> CalibrationResult: ...
```
  - `agente_ia_edu.tri.simulation.simulate_responses(*, a: np.ndarray, b: np.ndarray, thetas: np.ndarray, seed: int) -> np.ndarray` (criado no Step 3b; é dele que as Tarefas 8, 10, 11, 13, 18 e 19 dependem)

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tri_calibration_em.py
"""Laco EM da calibragem (spec §6.1).

Comportamento estrutural: convergencia, teto de iteracoes, alinhamento da
saida com as colunas de entrada, itens travados (§6.4) e modo Rasch (§6.5).
A verificacao numerica de verdade - recuperacao de parametros - vive em
tests/test_tri_parameter_recovery.py.
"""

from __future__ import annotations

import unittest

import numpy as np

from agente_ia_edu.tri.calibration import (
    CONVERGENCE_TOLERANCE,
    ENGINE_VERSION,
    ESTIMATED_POPULATION,
    FIXED_STANDARD_NORMAL,
    MAX_ITERATIONS,
    CalibrationError,
    CalibrationResult,
    PopulationPrior,
    calibrate,
)
from agente_ia_edu.tri.model import ResponseMatrix
from agente_ia_edu.tri.quality import NO_VARIANCE
from agente_ia_edu.tri.simulation import simulate_responses

SEED = 4242


def _matrix(n_examinees: int = 400, n_items: int = 12) -> ResponseMatrix:
    rng = np.random.default_rng(SEED)
    a = rng.uniform(0.7, 1.8, n_items)
    b = rng.uniform(-1.5, 1.5, n_items)
    thetas = rng.normal(0.0, 1.0, n_examinees)
    return ResponseMatrix(simulate_responses(a=a, b=b, thetas=thetas, seed=SEED))


class CalibrateStructureTests(unittest.TestCase):
    def test_spec_defaults(self):
        self.assertEqual(MAX_ITERATIONS, 500)
        self.assertEqual(CONVERGENCE_TOLERANCE, 1e-4)

    def test_returns_one_parameter_set_per_input_column(self):
        matrix = _matrix()
        result = calibrate(matrix)
        self.assertIsInstance(result, CalibrationResult)
        self.assertEqual(len(result.items), matrix.n_items)
        self.assertEqual(result.theta.shape, (matrix.n_examinees,))
        self.assertEqual(result.theta_se.shape, (matrix.n_examinees,))
        self.assertEqual(result.n_examinees, matrix.n_examinees)
        self.assertEqual(result.n_items, matrix.n_items)
        self.assertEqual(result.engine_version, ENGINE_VERSION)

    def test_converges_within_the_iteration_cap(self):
        result = calibrate(_matrix())
        self.assertTrue(result.converged)
        self.assertGreater(result.iterations, 1)
        self.assertLessEqual(result.iterations, MAX_ITERATIONS)
        self.assertTrue(np.isfinite(result.log_likelihood))

    def test_hitting_the_iteration_cap_marks_it_as_not_converged(self):
        result = calibrate(_matrix(), max_iterations=2)
        self.assertFalse(result.converged)
        self.assertEqual(result.iterations, 2)

    def test_is_deterministic(self):
        matrix = _matrix()
        first = calibrate(matrix)
        second = calibrate(matrix)
        np.testing.assert_allclose(
            [item.a for item in first.items], [item.a for item in second.items]
        )
        np.testing.assert_allclose(first.theta, second.theta)

    def test_every_estimated_item_carries_standard_errors(self):
        result = calibrate(_matrix())
        for parameters in result.items:
            self.assertIsNotNone(parameters.se_a)
            self.assertIsNotNone(parameters.se_b)
            self.assertGreater(parameters.se_b, 0.0)

    def test_items_are_flagged_with_the_quality_vocabulary(self):
        values = _matrix().values.copy()
        values[:, 3] = 1.0  # todo mundo acertou o item 3
        result = calibrate(ResponseMatrix(values))
        self.assertIsNone(result.items[3].a)
        self.assertIn(NO_VARIANCE, result.items[3].flags)
        self.assertIsNotNone(result.items[0].a)


class RaschModeTests(unittest.TestCase):
    def test_rasch_pins_every_discrimination_at_one(self):
        result = calibrate(_matrix(), model="RASCH")
        self.assertEqual(result.model, "RASCH")
        for parameters in result.items:
            self.assertEqual(parameters.a, 1.0)
            self.assertIsNone(parameters.se_a)
            self.assertIsNotNone(parameters.b)

    def test_rasch_ignores_the_two_hundred_examinee_floor(self):
        matrix = _matrix(n_examinees=120)
        result = calibrate(matrix, model="RASCH")
        self.assertEqual(result.n_examinees, 120)

    def test_an_unknown_model_is_refused(self):
        with self.assertRaises(CalibrationError):
            calibrate(_matrix(), model="3PL")


class PopulationPriorTests(unittest.TestCase):
    """Modo de distribuicao populacional (spec §6.1). A prova de que a estimacao
    RECUPERA um grupo deslocado vive no seu proprio arquivo; aqui e so o
    contrato."""

    def test_the_default_is_the_free_calibration_convention(self):
        self.assertEqual(FIXED_STANDARD_NORMAL, PopulationPrior(0.0, 1.0, False))
        self.assertTrue(ESTIMATED_POPULATION.estimate)

    def test_free_calibration_reports_the_prior_it_was_given(self):
        result = calibrate(_matrix())
        self.assertFalse(result.population_estimated)
        self.assertAlmostEqual(result.population_mean, 0.0, places=12)
        self.assertAlmostEqual(result.population_sd, 1.0, places=12)

    def test_estimating_the_population_without_anchors_is_refused(self):
        # Nao identificavel: deslocar todos os b e a media junto da a mesma
        # verossimilhanca. Recusar e a unica saida honesta.
        with self.assertRaises(CalibrationError) as caught:
            calibrate(_matrix(), population=ESTIMATED_POPULATION)
        self.assertIn("identific", str(caught.exception).lower())

    def test_estimating_the_population_with_anchors_is_allowed(self):
        result = calibrate(
            _matrix(), population=ESTIMATED_POPULATION, fixed_items={2: (1.4, -0.3)}
        )
        self.assertTrue(result.population_estimated)
        self.assertTrue(np.isfinite(result.population_mean))
        self.assertGreater(result.population_sd, 0.0)

    def test_a_non_positive_prior_sd_is_refused(self):
        with self.assertRaises(CalibrationError):
            calibrate(_matrix(), population=PopulationPrior(sd=0.0))

    def test_a_shifted_fixed_prior_moves_the_thetas_with_it(self):
        centred = calibrate(_matrix()).theta.mean()
        shifted = calibrate(_matrix(), population=PopulationPrior(mean=1.0)).theta.mean()
        self.assertGreater(shifted, centred)


class FixedItemTests(unittest.TestCase):
    def test_a_fixed_item_comes_back_exactly_as_given(self):
        result = calibrate(_matrix(), fixed_items={2: (1.75, -0.35)})
        self.assertAlmostEqual(result.items[2].a, 1.75, places=12)
        self.assertAlmostEqual(result.items[2].b, -0.35, places=12)
        self.assertTrue(result.items[2].is_fixed)

    def test_free_items_are_still_estimated_next_to_a_fixed_one(self):
        result = calibrate(_matrix(), fixed_items={2: (1.75, -0.35)})
        self.assertFalse(result.items[0].is_fixed)
        self.assertIsNotNone(result.items[0].a)

    def test_an_out_of_range_fixed_position_is_refused(self):
        with self.assertRaises(CalibrationError):
            calibrate(_matrix(n_items=5), fixed_items={99: (1.0, 0.0)})

    def test_a_fixed_item_without_variance_is_refused(self):
        values = _matrix().values.copy()
        values[:, 1] = 0.0
        with self.assertRaises(CalibrationError):
            calibrate(ResponseMatrix(values), fixed_items={1: (1.0, 0.0)})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tri_calibration_em.py -v`
Expected: FAIL com `ImportError: cannot import name 'calibrate'` (e `ModuleNotFoundError` para `agente_ia_edu.tri.simulation`, criado na Tarefa 8 — crie-o agora, ver Step 3b).

- [ ] **Step 3a: Acrescente o laço EM a `src/agente_ia_edu/tri/calibration.py`**

Troque o bloco de imports no topo do arquivo por:

```python
from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

import numpy as np
from scipy.optimize import minimize

from .model import ItemParameters, ResponseMatrix, probability_2pl
from .quadrature import gaussian_quadrature, prior_weights
from .quality import MINIMUM_EXAMINEES_FOR_2PL, item_flags
from .theta import estimate_eap, marginal_log_likelihood, posterior_over_grid
```

E acrescente ao final do arquivo:

```python
MAX_ITERATIONS = 500
CONVERGENCE_TOLERANCE = 1e-4
SUPPORTED_MODELS = ("2PL", "RASCH")
MINIMUM_ESTIMABLE_ITEMS = 2
# Piso do desvio-padrao da populacao estimada. Sem ele, uma amostra pequena e
# homogenea pode colapsar a distribuicao para perto de zero, e um prior quase
# degenerado engole a verossimilhanca: todo mundo recebe o mesmo theta.
MINIMUM_POPULATION_SD = 0.2
# Carimbado em tri_calibrations.engine_version: quando o algoritmo mudar, e
# preciso saber quais notas ja publicadas vieram de qual versao, sem recalcular
# a historia inteira (spec §3).
ENGINE_VERSION = "tri-1.0.0"

_STARTING_P_VALUE_CLIP = (0.05, 0.95)


class CalibrationError(RuntimeError):
    """A calibragem nao pode ser executada como pedida."""


class InsufficientSampleError(CalibrationError):
    """Respondentes de menos para o modelo pedido (spec §6.5)."""


@dataclass(frozen=True)
class PopulationPrior:
    """Distribuicao de theta da populacao (spec §6.1).

    ``estimate=False`` e a CALIBRAGEM LIVRE: o prior fica preso em N(mean, sd^2),
    por padrao N(0,1). A escala precisa de uma origem e essa e a convencao que a
    fornece.

    ``estimate=True`` e a CALIBRAGEM EQUALIZADA: ``mean`` e ``sd`` viram chute
    inicial e sao estimados junto com os itens. E isso que faz a equalizacao por
    ancoras funcionar - com o prior preso em N(0,1), travar os parametros das
    ancoras nao equaliza nada, porque o EM re-centra o grupo novo em zero e
    desfaz exatamente o deslocamento que a equalizacao existe para medir. A
    turma melhora, a regua sobe junto, a nota nao mexe.
    """

    mean: float = 0.0
    sd: float = 1.0
    estimate: bool = False


FIXED_STANDARD_NORMAL = PopulationPrior()
ESTIMATED_POPULATION = PopulationPrior(estimate=True)


@dataclass(frozen=True, eq=False)
class CalibrationResult:
    """``items`` esta alinhada as colunas da matriz de ENTRADA, inclusive as
    que foram excluidas da estimacao por falta de variancia - quem chamou nao
    precisa reconstruir indice nenhum.

    ``population_mean`` e ``population_sd`` sao a distribuicao sob a qual os
    thetas foram estimados. No modo livre repetem o prior fixo; no modo
    equalizado sao a estimativa, e e ela que carrega o quanto este grupo esta
    acima ou abaixo do grupo que definiu a escala."""

    model: str
    items: tuple[ItemParameters, ...]
    theta: np.ndarray
    theta_se: np.ndarray
    converged: bool
    iterations: int
    log_likelihood: float
    n_examinees: int
    n_items: int
    population_mean: float
    population_sd: float
    population_estimated: bool
    engine_version: str = ENGINE_VERSION


def _starting_difficulties(values: np.ndarray) -> np.ndarray:
    """b inicial pelo p-valor: com a = 1, P(theta=0) = sigma(-b) = p => b = -logit(p)."""
    with np.errstate(invalid="ignore"):
        p = np.nanmean(values, axis=0)
    p = np.clip(np.nan_to_num(p, nan=0.5), *_STARTING_P_VALUE_CLIP)
    return -np.log(p / (1.0 - p))


def calibrate(
    matrix: ResponseMatrix,
    *,
    model: str = "2PL",
    population: PopulationPrior = FIXED_STANDARD_NORMAL,
    fixed_items: Mapping[int, tuple[float, float]] | None = None,
    max_iterations: int = MAX_ITERATIONS,
    tolerance: float = CONVERGENCE_TOLERANCE,
    minimum_examinees: int = MINIMUM_EXAMINEES_FOR_2PL,
) -> CalibrationResult:
    """Calibragem MMLE-EM (Bock-Aitkin) e theta por EAP.

    ``fixed_items`` mapeia POSICAO DE COLUNA -> (a, b) travados. E a
    fixed-parameter calibration de §6.4: os itens-ancora nao se movem.

    ``population`` escolhe o modo de §6.1. ``FIXED_STANDARD_NORMAL`` (padrao) e a
    calibragem livre. ``ESTIMATED_POPULATION`` e a equalizada: a cada iteracao,
    depois do passo M, a media e o desvio da populacao sao reestimados a partir
    da posteriori acumulada, e a grade e repesada. So com esse laco os itens
    travados produzem equalizacao de verdade.

    A Fase 3 entrega o mecanismo completo, inclusive a estimacao da populacao -
    ela e materia do motor, e deixa-la fora significaria deixa-la fora do teste
    de recuperacao de parametros. A escolha das ancoras, a transformacao
    mean-sigma de diagnostico e a verificacao de deriva sao da Fase 5.
    """
    if model not in SUPPORTED_MODELS:
        raise CalibrationError(
            f"Modelo {model!r} nao suportado; use um de {list(SUPPORTED_MODELS)}"
        )
    if model == "2PL" and matrix.n_examinees < minimum_examinees:
        raise InsufficientSampleError(
            f"2PL exige ao menos {minimum_examinees} respondentes (spec §6.5); "
            f"a area tem {matrix.n_examinees}. Caia para RASCH ou para nota bruta, "
            "sempre com aviso explicito na tela."
        )

    fixed_items = dict(fixed_items or {})
    if population.estimate and not fixed_items:
        raise CalibrationError(
            "Estimar a distribuicao da populacao sem nenhum item travado nao e "
            "identificavel: deslocar todos os b e a media da populacao na mesma "
            "medida produz exatamente a mesma verossimilhanca. Use "
            "FIXED_STANDARD_NORMAL para calibragem livre, ou passe as ancoras "
            "em fixed_items (spec §6.1, §6.4)."
        )
    if population.sd <= 0.0:
        raise CalibrationError(
            f"O desvio-padrao do prior precisa ser positivo; recebido {population.sd}"
        )
    for position in fixed_items:
        if not 0 <= position < matrix.n_items:
            raise CalibrationError(
                f"Item travado na posicao {position}, fora da matriz de "
                f"{matrix.n_items} itens"
            )

    p_values = matrix.item_p_values()
    degenerate = matrix.zero_variance_items()
    estimable_positions = [i for i in range(matrix.n_items) if not degenerate[i]]
    if len(estimable_positions) < MINIMUM_ESTIMABLE_ITEMS:
        raise CalibrationError(
            f"So {len(estimable_positions)} item(ns) tem variancia de resposta; "
            f"sao necessarios ao menos {MINIMUM_ESTIMABLE_ITEMS} para estimar theta"
        )
    for position in fixed_items:
        if degenerate[position]:
            raise CalibrationError(
                f"Item travado na posicao {position} nao tem variancia de resposta; "
                "uma ancora sem variancia nao carrega escala nenhuma"
            )

    values = matrix.values[:, estimable_positions]
    observed = ~np.isnan(values)
    correct = np.where(observed, values, 0.0)
    attempts = observed.astype(float)

    # A grade e fixa; o que a atualizacao da populacao muda sao os PESOS. Mover
    # os nos deslocaria a escala sob os itens travados, que e exatamente o que a
    # equalizacao existe para preservar.
    nodes, _ = gaussian_quadrature()
    population_mean = float(population.mean)
    population_sd = float(population.sd)
    weights = prior_weights(nodes, population_mean, population_sd)
    a = np.ones(len(estimable_positions))
    b = _starting_difficulties(values)
    is_fixed = np.zeros(len(estimable_positions), dtype=bool)
    for column, position in enumerate(estimable_positions):
        if position in fixed_items:
            a[column], b[column] = (float(v) for v in fixed_items[position])
            is_fixed[column] = True

    converged = False
    iterations = 0
    for iteration in range(1, max_iterations + 1):
        iterations = iteration
        # Passo E: posterior de theta por respondente e contagens esperadas por no.
        posterior = posterior_over_grid(values, a, b, nodes, weights)
        expected_correct = correct.T @ posterior
        expected_total = attempts.T @ posterior

        # Passo M: um ajuste 2PL ponderado por item livre.
        new_a = a.copy()
        new_b = b.copy()
        for column in range(len(estimable_positions)):
            if is_fixed[column]:
                continue
            new_a[column], new_b[column] = fit_item(
                nodes,
                expected_correct[column],
                expected_total[column],
                start_a=1.0 if model == "RASCH" else a[column],
                start_b=b[column],
                fix_a=(model == "RASCH"),
            )
        # Atualizacao da distribuicao populacional (spec §6.1, modo equalizado).
        # Momentos da posteriori acumulada sobre todos os respondentes - o passo
        # padrao do Bock-Aitkin quando a populacao nao e tratada como conhecida.
        new_mean, new_sd = population_mean, population_sd
        if population.estimate:
            new_mean = float((posterior @ nodes).mean())
            second_moment = float((posterior @ (nodes ** 2)).mean())
            new_sd = float(np.sqrt(max(
                second_moment - new_mean ** 2, MINIMUM_POPULATION_SD ** 2
            )))

        change = max(
            float(np.abs(new_a - a).max()),
            float(np.abs(new_b - b).max()),
            abs(new_mean - population_mean),
            abs(new_sd - population_sd),
        )
        a, b = new_a, new_b
        population_mean, population_sd = new_mean, new_sd
        weights = prior_weights(nodes, population_mean, population_sd)
        if change < tolerance:
            converged = True
            break

    posterior = posterior_over_grid(values, a, b, nodes, weights)
    expected_total = attempts.T @ posterior
    log_likelihood = marginal_log_likelihood(values, a, b, nodes, weights)
    estimates = estimate_eap(values, a, b, nodes, weights)

    parameters: list[ItemParameters] = []
    column_by_position = {p: c for c, p in enumerate(estimable_positions)}
    for position in range(matrix.n_items):
        p_value = None if np.isnan(p_values[position]) else float(p_values[position])
        if position not in column_by_position:
            parameters.append(
                ItemParameters(
                    a=None, b=None, flags=item_flags(a=None, p_value=p_value)
                )
            )
            continue
        column = column_by_position[position]
        se_a, se_b = item_standard_errors(
            nodes, expected_total[column], float(a[column]), float(b[column])
        )
        parameters.append(
            ItemParameters(
                a=float(a[column]),
                b=float(b[column]),
                se_a=None if model == "RASCH" else se_a,
                se_b=se_b,
                is_fixed=bool(is_fixed[column]),
                flags=item_flags(a=float(a[column]), p_value=p_value),
            )
        )

    return CalibrationResult(
        model=model,
        items=tuple(parameters),
        theta=estimates.theta,
        theta_se=estimates.se,
        converged=converged,
        iterations=iterations,
        log_likelihood=log_likelihood,
        n_examinees=matrix.n_examinees,
        n_items=matrix.n_items,
        population_mean=population_mean,
        population_sd=population_sd,
        population_estimated=bool(population.estimate),
    )


__all__ = [
    "A_BOUNDS",
    "B_BOUNDS",
    "CONVERGENCE_TOLERANCE",
    "ENGINE_VERSION",
    "ESTIMATED_POPULATION",
    "FIXED_STANDARD_NORMAL",
    "MAX_ITERATIONS",
    "MINIMUM_ESTIMABLE_ITEMS",
    "MINIMUM_POPULATION_SD",
    "SUPPORTED_MODELS",
    "CalibrationError",
    "CalibrationResult",
    "InsufficientSampleError",
    "PopulationPrior",
    "calibrate",
    "fit_item",
    "item_negative_log_likelihood",
    "item_standard_errors",
]
```

- [ ] **Step 3b: Crie `src/agente_ia_edu/tri/simulation.py`**

```python
# src/agente_ia_edu/tri/simulation.py
"""Gerador determinístico de respostas a partir de parametros conhecidos (spec §8.1).

Vive no pacote do motor, e nao em tests/, porque e a unica forma honesta de
saber que a calibragem esta certa: alimentar o motor com dados cuja verdade e
conhecida e exigir que ele a recupere. Um motor de TRI errado nao lanca
excecao - ele devolve numeros plausiveis e errados que viram nota de aluno.
"""

from __future__ import annotations

import numpy as np

from .model import probability_2pl


def simulate_responses(
    *, a: np.ndarray, b: np.ndarray, thetas: np.ndarray, seed: int
) -> np.ndarray:
    """Matriz (len(thetas) x len(a)) de 0.0/1.0 sorteada sob o 2PL.

    ``seed`` e obrigatorio e nomeado: um teste de recuperacao de parametros que
    nao e deterministico nao e um teste, e uma loteria que as vezes acusa.
    """
    a = np.asarray(a, dtype=float)
    b = np.asarray(b, dtype=float)
    thetas = np.asarray(thetas, dtype=float)
    if a.shape != b.shape:
        raise ValueError(f"a e b precisam ter o mesmo shape; {a.shape} vs {b.shape}")
    probability = probability_2pl(thetas[:, None], a[None, :], b[None, :])
    rng = np.random.default_rng(seed)
    return (rng.random(probability.shape) < probability).astype(float)


__all__ = ["simulate_responses"]
```

- [ ] **Step 3c: Reexporte a API pública em `src/agente_ia_edu/tri/__init__.py`**

Acrescente ao final do docstring já existente:

```python
from .calibration import (
    ESTIMATED_POPULATION,
    FIXED_STANDARD_NORMAL,
    CalibrationError,
    CalibrationResult,
    InsufficientSampleError,
    PopulationPrior,
    calibrate,
)
from .model import ItemParameters, ResponseMatrix, ResponseMatrixError, probability_2pl
from .quadrature import gaussian_quadrature, prior_weights
from .quality import PublicationDecision, item_flags, publication_gate
from .simulation import simulate_responses
from .theta import ThetaEstimates, estimate_eap

__all__ = [
    "ESTIMATED_POPULATION",
    "FIXED_STANDARD_NORMAL",
    "CalibrationError",
    "CalibrationResult",
    "InsufficientSampleError",
    "ItemParameters",
    "PopulationPrior",
    "PublicationDecision",
    "ResponseMatrix",
    "ResponseMatrixError",
    "ThetaEstimates",
    "calibrate",
    "estimate_eap",
    "gaussian_quadrature",
    "item_flags",
    "prior_weights",
    "probability_2pl",
    "publication_gate",
    "simulate_responses",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_calibration_em.py tests/test_tri_dependency_boundary.py -v`
Expected: PASS (todos)

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/tri/calibration.py src/agente_ia_edu/tri/simulation.py \
        src/agente_ia_edu/tri/__init__.py tests/test_tri_calibration_em.py
git commit -m "feat: laco EM com modo Rasch, itens travados e populacao estimavel

A distribuicao de theta da populacao e parametro do motor (spec 6.1): fixa
em N(0,1) na calibragem livre, estimada no laco quando ha ancoras travadas.
Sem isso, travar ancoras nao equaliza nada."
```

---

### Task 8: ⭐ Recuperação de parâmetros — o teste central do subsistema

**Files:**
- Test: `tests/test_tri_parameter_recovery.py`
- (nenhum arquivo de produção é criado ou modificado: `simulate_responses` e `calibrate` já existem desde a Tarefa 7)

**Interfaces:**
- Consumes: `simulate_responses`, `calibrate`, `ResponseMatrix` (Tarefas 2 e 7).
- Produces: a evidência de que o motor está certo. Nenhuma API nova.

**Por que esta tarefa existe sozinha.** Um motor de TRI errado não lança exceção, não quebra teste de integração e não falha em produção — ele devolve números plausíveis e errados, que viram nota de aluno (spec §8.1). Recuperação de parâmetros é a única verificação que distingue "roda" de "está certo". **Se este teste falhar, o defeito está no motor, não nas tolerâncias.** As tolerâncias são da spec e só mudam com justificativa registrada no próprio arquivo de teste.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tri_parameter_recovery.py
"""RECUPERACAO DE PARAMETROS - o teste central do subsistema de TRI (spec §8.1).

Simular 1.000 respondentes a partir de parametros a,b conhecidos, calibrar, e
exigir que o motor recupere os originais dentro de tolerancia.

Tolerancias, LITERALMENTE as da spec §8.1:
  - correlacao entre b verdadeiro e estimado  > 0,95
  - erro quadratico medio de b                < 0,15
  - |vies medio de b|                         < 0,05

"Os limites de partida, a ajustar apenas com justificativa registrada."
Se este teste falhar, o lugar certo de mexer e o motor. Afrouxar um numero
daqui sem registrar o porque transforma o unico teste honesto do subsistema em
decoracao.

A semente e fixa para que o teste seja deterministico (spec §8.1).
"""

from __future__ import annotations

import unittest

import numpy as np

from agente_ia_edu.tri.calibration import calibrate
from agente_ia_edu.tri.model import ResponseMatrix
from agente_ia_edu.tri.simulation import simulate_responses

# --- Tolerancias da spec §8.1 -------------------------------------------------
MINIMUM_B_CORRELATION = 0.95
MAXIMUM_B_MEAN_SQUARED_ERROR = 0.15
MAXIMUM_ABSOLUTE_B_BIAS = 0.05

# --- Desenho da simulacao -----------------------------------------------------
SEED = 20260929
N_EXAMINEES = 1000          # spec §8.1: "simular 1.000 respondentes"
N_ITEMS = 40                # ordem de grandeza de uma area do 1o dia do ENEM
A_RANGE = (0.6, 2.0)        # faixa de discriminacao tipica de item calibravel
B_RANGE = (-2.0, 2.0)       # dentro da grade de quadratura (+-4)


def _truth() -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Parametros verdadeiros e thetas verdadeiros, deterministicos.

    Um unico Generator, consumido sempre na mesma ordem: trocar a ordem das tres
    chamadas abaixo muda os dados e invalida a comparacao com execucoes
    anteriores.
    """
    rng = np.random.default_rng(SEED)
    true_a = rng.uniform(*A_RANGE, N_ITEMS)
    true_b = rng.uniform(*B_RANGE, N_ITEMS)
    true_theta = rng.normal(0.0, 1.0, N_EXAMINEES)
    return true_a, true_b, true_theta


class ParameterRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.true_a, cls.true_b, cls.true_theta = _truth()
        responses = simulate_responses(
            a=cls.true_a, b=cls.true_b, thetas=cls.true_theta, seed=SEED
        )
        cls.matrix = ResponseMatrix(responses)
        cls.result = calibrate(cls.matrix)
        cls.estimated_a = np.array([item.a for item in cls.result.items])
        cls.estimated_b = np.array([item.b for item in cls.result.items])

    def test_the_simulation_is_deterministic(self):
        again = simulate_responses(
            a=self.true_a, b=self.true_b, thetas=self.true_theta, seed=SEED
        )
        np.testing.assert_array_equal(again, self.matrix.values)

    def test_the_calibration_converged(self):
        self.assertTrue(
            self.result.converged,
            f"nao convergiu em {self.result.iterations} iteracoes",
        )

    def test_every_item_was_estimated(self):
        self.assertTrue(np.all(np.isfinite(self.estimated_a)))
        self.assertTrue(np.all(np.isfinite(self.estimated_b)))

    def test_difficulty_correlation_is_above_the_spec_floor(self):
        correlation = float(np.corrcoef(self.true_b, self.estimated_b)[0, 1])
        self.assertGreater(
            correlation,
            MINIMUM_B_CORRELATION,
            f"correlacao de b = {correlation:.4f} (spec §8.1 exige > "
            f"{MINIMUM_B_CORRELATION})",
        )

    def test_difficulty_mean_squared_error_is_below_the_spec_ceiling(self):
        mse = float(np.mean((self.estimated_b - self.true_b) ** 2))
        self.assertLess(
            mse,
            MAXIMUM_B_MEAN_SQUARED_ERROR,
            f"EQM de b = {mse:.4f} (spec §8.1 exige < "
            f"{MAXIMUM_B_MEAN_SQUARED_ERROR})",
        )

    def test_difficulty_bias_is_below_the_spec_ceiling(self):
        bias = float(np.mean(self.estimated_b - self.true_b))
        self.assertLess(
            abs(bias),
            MAXIMUM_ABSOLUTE_B_BIAS,
            f"vies de b = {bias:+.4f} (spec §8.1 exige |vies| < "
            f"{MAXIMUM_ABSOLUTE_B_BIAS})",
        )

    def test_discrimination_is_recovered_too(self):
        # A spec §8.1 so fixa limites para b. Um motor pode acertar b inteiro e
        # errar a escala de a - por exemplo aplicando o fator D = 1.7 por
        # engano em um lado so - e isso deformaria a informacao do item e a
        # analise de qualidade da prova (§7) sem tocar em uma nota sequer.
        correlation = float(np.corrcoef(self.true_a, self.estimated_a)[0, 1])
        self.assertGreater(correlation, 0.80, f"correlacao de a = {correlation:.4f}")
        bias = float(np.mean(self.estimated_a - self.true_a))
        self.assertLess(abs(bias), 0.20, f"vies de a = {bias:+.4f}")

    def test_theta_tracks_the_true_proficiency(self):
        correlation = float(np.corrcoef(self.true_theta, self.result.theta)[0, 1])
        self.assertGreater(correlation, 0.90, f"correlacao de theta = {correlation:.4f}")

    def test_theta_is_on_the_prior_scale(self):
        # O EM ancora a distribuicao de theta no prior N(0,1). Uma media longe de
        # zero ou um desvio longe de 1 significa que a escala derivou - e uma
        # escala derivada converte theta em nota errada (§6.3) sem erro nenhum
        # aparecer nos parametros de item.
        self.assertAlmostEqual(float(self.result.theta.mean()), 0.0, delta=0.10)
        self.assertAlmostEqual(float(self.result.theta.std()), 0.90, delta=0.20)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Antes de rodar, garanta que o teste realmente é capaz de falhar — injete um defeito silencioso no motor e confirme que ele é pego:

```bash
# Inverte o sinal de b no passo M: o motor continua rodando, continua
# convergindo, e devolve numeros plausiveis e errados.
sed -i '' 's/gradient_a = float(residual @ (nodes - b))/gradient_a = float(residual @ (nodes + b))/' \
    src/agente_ia_edu/tri/calibration.py
.venv/bin/python -m pytest tests/test_tri_parameter_recovery.py -v
```

Expected: FAIL em `test_difficulty_mean_squared_error_is_below_the_spec_ceiling` e/ou `test_difficulty_correlation_is_above_the_spec_floor`, com o valor observado impresso na mensagem.

Depois desfaça o defeito:

```bash
git checkout -- src/agente_ia_edu/tri/calibration.py
```

- [ ] **Step 3: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_parameter_recovery.py -v`
Expected: PASS (9 testes). A calibragem de 1.000 × 40 leva ~10–40 s; é `setUpClass`, roda uma vez.

Se algum limite de `b` falhar, **não mexa nos números do teste**. Investigue nesta ordem, que é a ordem de probabilidade:
1. Sinal do gradiente em `item_negative_log_likelihood` (`dz/db = -a`, `dz/da = theta - b`).
2. Transposição das contagens esperadas: `expected_correct` tem shape `(n_items, n_nodes)`; `correct.T @ posterior` com `correct` em `(n_ex, n_items)`.
3. Normalização dos pesos da quadratura (`weights.sum() == 1.0`).
4. `THETA_LIMIT` pequeno demais para `B_RANGE` (a grade precisa cobrir os `b` verdadeiros com folga).

- [ ] **Step 4: Commit**

```bash
git add tests/test_tri_parameter_recovery.py
git commit -m "test: recuperacao de parametros do motor de TRI (spec 8.1)

1.000 respondentes simulados com semente fixa a partir de a,b conhecidos.
Exige correlacao de b > 0,95, EQM < 0,15 e |vies| < 0,05. E a unica
verificacao que distingue 'o motor roda' de 'o motor esta certo'."
```

---

### Task 9: ⭐ Recuperação da população de um grupo deslocado

**Files:**
- Test: `tests/test_tri_population_recovery.py`
- (nenhum arquivo de produção: o comportamento é da Tarefa 7; esta tarefa o **prova**)

**Interfaces:**
- Consumes: `calibrate`, `ESTIMATED_POPULATION`, `FIXED_STANDARD_NORMAL`, `CalibrationError` (Tarefa 7); `simulate_responses`, `ResponseMatrix`.
- Produces: nenhuma API nova.

**Por que esta tarefa existe sozinha, ao lado da Tarefa 8.** A Tarefa 8 prova que o motor
recupera `a` e `b`. Ela **não** pegaria o bug que a spec §6.1 descreve, porque aquele bug não
está nos parâmetros de item: está na origem da escala. Com o prior preso em N(0,1), travar as
âncoras não equaliza nada — o EM re-centra o grupo novo em zero e desfaz o deslocamento que a
equalização existe para medir. A turma melhora, a régua sobe junto, a nota não mexe.

O par de asserções é o ponto: um teste só do modo estimado passaria com a equalização
desligada, porque não haveria com o que comparar. É a **diferença** entre os dois modos, sobre
os mesmos dados simulados, que prova que a coisa está ligada.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tri_population_recovery.py
"""RECUPERACAO DA POPULACAO de um grupo deslocado (spec §6.1).

A spec §6.1 define dois modos para a distribuicao de theta:

  - calibragem LIVRE: prior fixo em N(0,1). A escala precisa de uma origem.
  - calibragem EQUALIZADA: media e desvio da populacao estimados junto com o
    resto, num laco que alterna passo E, passo M e atualizacao da populacao.

Este teste simula um grupo cuja media verdadeira e +0,6 e calibra dos dois
jeitos, com as MESMAS ancoras travadas nos seus valores verdadeiros.

O que se exige:
  - modo estimado devolve population_mean ~ +0,6 e thetas centrados ali;
  - modo fixo devolve thetas puxados para baixo e desloca os itens LIVRES para
    cima, absorvendo na regua o avanco que era do grupo.

Esse par e a prova. Uma asserção so do modo estimado passaria com a equalizacao
desligada - nao haveria com o que comparar.
"""

from __future__ import annotations

import unittest

import numpy as np

from agente_ia_edu.tri.calibration import (
    ESTIMATED_POPULATION,
    FIXED_STANDARD_NORMAL,
    CalibrationError,
    calibrate,
)
from agente_ia_edu.tri.model import ResponseMatrix
from agente_ia_edu.tri.simulation import simulate_responses

SEED = 20260929
N_EXAMINEES = 1000
N_ITEMS = 30
N_ANCHORS = 10                 # posicoes 0..9 entram travadas nos valores verdadeiros
TRUE_POPULATION_MEAN = 0.6     # o grupo novo e melhor que o que definiu a escala
TRUE_POPULATION_SD = 1.0

# --- Tolerancias de recuperacao (o que o modo estimado tem que acertar) -------
POPULATION_MEAN_TOLERANCE = 0.12
POPULATION_SD_TOLERANCE = 0.15
FREE_ITEM_BIAS_TOLERANCE = 0.08

# --- Limiares DIRECIONAIS (o que o modo fixo tem que errar) ------------------
# O SINAL destes dois e garantido pelo mecanismo: com o prior preso, o unico
# jeito de o modelo explicar um grupo melhor e empurrar os itens livres para
# cima e os thetas para baixo. A MAGNITUDE depende da proporcao ancora/livre
# (aqui 10/20). Se falharem, confira essa proporcao ANTES de mexer no numero -
# e, diferente das tolerancias da spec §8.1, estes limiares sao deste plano.
MINIMUM_THETA_GAP = 0.15
MINIMUM_FIXED_MODE_FREE_ITEM_BIAS = 0.10


def _truth():
    """Parametros e thetas verdadeiros, deterministicos.

    Um unico Generator consumido sempre na mesma ordem: trocar a ordem das tres
    chamadas muda os dados e invalida a comparacao com execucoes anteriores.
    """
    rng = np.random.default_rng(SEED)
    true_a = rng.uniform(0.7, 1.8, N_ITEMS)
    true_b = rng.uniform(-1.8, 1.8, N_ITEMS)
    true_theta = rng.normal(TRUE_POPULATION_MEAN, TRUE_POPULATION_SD, N_EXAMINEES)
    return true_a, true_b, true_theta


class PopulationRecoveryTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.true_a, cls.true_b, cls.true_theta = _truth()
        cls.matrix = ResponseMatrix(simulate_responses(
            a=cls.true_a, b=cls.true_b, thetas=cls.true_theta, seed=SEED
        ))
        # As ancoras entram travadas nos valores VERDADEIROS: e o que a Fase 5
        # fara com os parametros ja registrados na regua.
        cls.anchors = {
            position: (float(cls.true_a[position]), float(cls.true_b[position]))
            for position in range(N_ANCHORS)
        }
        cls.free_positions = list(range(N_ANCHORS, N_ITEMS))
        cls.estimated = calibrate(
            cls.matrix, population=ESTIMATED_POPULATION, fixed_items=cls.anchors
        )
        cls.fixed = calibrate(
            cls.matrix, population=FIXED_STANDARD_NORMAL, fixed_items=cls.anchors
        )

    def _free_item_bias(self, result) -> float:
        estimated_b = np.array([result.items[i].b for i in self.free_positions])
        return float(np.mean(estimated_b - self.true_b[self.free_positions]))

    # --- o que o modo estimado tem que acertar -------------------------------

    def test_both_modes_converged(self):
        self.assertTrue(self.estimated.converged, self.estimated.iterations)
        self.assertTrue(self.fixed.converged, self.fixed.iterations)

    def test_the_estimated_mode_recovers_the_true_population_mean(self):
        self.assertAlmostEqual(
            self.estimated.population_mean, TRUE_POPULATION_MEAN,
            delta=POPULATION_MEAN_TOLERANCE,
            msg=f"media estimada = {self.estimated.population_mean:+.4f}, "
                f"verdadeira = {TRUE_POPULATION_MEAN:+.2f}",
        )

    def test_the_estimated_mode_recovers_the_true_population_spread(self):
        self.assertAlmostEqual(
            self.estimated.population_sd, TRUE_POPULATION_SD,
            delta=POPULATION_SD_TOLERANCE,
            msg=f"desvio estimado = {self.estimated.population_sd:.4f}",
        )

    def test_the_estimated_mode_centres_the_thetas_on_the_true_mean(self):
        self.assertAlmostEqual(
            float(self.estimated.theta.mean()), TRUE_POPULATION_MEAN,
            delta=POPULATION_MEAN_TOLERANCE,
        )

    def test_the_estimated_mode_leaves_the_free_items_unbiased(self):
        bias = self._free_item_bias(self.estimated)
        self.assertLess(
            abs(bias), FREE_ITEM_BIAS_TOLERANCE,
            f"vies dos itens livres no modo estimado = {bias:+.4f}",
        )

    def test_the_estimated_mode_reports_itself_as_estimated(self):
        self.assertTrue(self.estimated.population_estimated)
        self.assertFalse(self.fixed.population_estimated)

    # --- o que o modo fixo tem que ERRAR (o bug que a spec descreve) ----------

    def test_the_fixed_prior_pulls_the_thetas_back_toward_zero(self):
        gap = float(self.estimated.theta.mean()) - float(self.fixed.theta.mean())
        self.assertGreater(
            gap, MINIMUM_THETA_GAP,
            "com o prior preso em N(0,1) os thetas do grupo deslocado tinham que "
            f"ficar visivelmente abaixo dos do modo estimado; diferenca = {gap:+.4f}",
        )

    def test_the_fixed_prior_pushes_the_ruler_up_instead(self):
        # A regua sobe junto com a turma: e assim que o avanco do grupo some.
        bias = self._free_item_bias(self.fixed)
        self.assertGreater(
            bias, MINIMUM_FIXED_MODE_FREE_ITEM_BIAS,
            "com o prior preso, os itens livres tinham que absorver o avanco do "
            f"grupo aparecendo mais dificeis do que sao; vies = {bias:+.4f}",
        )

    def test_the_two_modes_really_disagree(self):
        # A asserção que amarra o par: se alguem desligar a estimacao da
        # populacao, os dois modos passam a devolver a MESMA coisa e este teste
        # cai, mesmo que todos os outros continuem verdes.
        self.assertGreater(
            abs(self.estimated.population_mean - self.fixed.population_mean), 0.3
        )

    # --- as ancoras nao se movem em modo nenhum ------------------------------

    def test_anchors_come_back_untouched_in_both_modes(self):
        for result in (self.estimated, self.fixed):
            for position, (true_a, true_b) in self.anchors.items():
                self.assertAlmostEqual(result.items[position].a, true_a, places=12)
                self.assertAlmostEqual(result.items[position].b, true_b, places=12)
                self.assertTrue(result.items[position].is_fixed)

    # --- identificabilidade --------------------------------------------------

    def test_estimating_the_population_without_anchors_is_refused(self):
        with self.assertRaises(CalibrationError):
            calibrate(self.matrix, population=ESTIMATED_POPULATION)

    def test_is_deterministic(self):
        again = calibrate(
            self.matrix, population=ESTIMATED_POPULATION, fixed_items=self.anchors
        )
        self.assertAlmostEqual(
            again.population_mean, self.estimated.population_mean, places=10
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Antes de rodar, confirme que o teste é capaz de pegar exatamente o bug que existe para
prevenir — desligue a atualização da população e veja o par de asserções cair:

```bash
.venv/bin/python - <<'EOF'
import pathlib
path = pathlib.Path("src/agente_ia_edu/tri/calibration.py")
text = path.read_text()
broken = text.replace("if population.estimate:", "if False:  # DEFEITO INJETADO", 1)
assert broken != text, "a linha alvo mudou; ajuste a injecao antes de seguir"
path.write_text(broken)
EOF
.venv/bin/python -m pytest tests/test_tri_population_recovery.py -v
```

Expected: FAIL em `test_the_estimated_mode_recovers_the_true_population_mean` (média volta ~0,0)
e em `test_the_two_modes_really_disagree`, com os valores observados impressos.

Depois desfaça o defeito:

```bash
git checkout -- src/agente_ia_edu/tri/calibration.py
```

- [ ] **Step 3: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_population_recovery.py -v`
Expected: PASS (12 testes). Duas calibragens de 1.000 × 30 no `setUpClass`; ~20–60 s.

Se `test_the_estimated_mode_recovers_the_true_population_mean` falhar, investigue nesta ordem:
1. A grade de quadratura está sendo **movida** em vez de repesada? Os nós têm de ficar parados
   (`prior_weights`), senão os itens travados passam a viver numa escala que se desloca.
2. Os momentos estão sendo tirados da posteriori **de todos os respondentes** (`.mean()` sobre
   o eixo dos examinandos), e não de um respondente só?
3. `weights` está sendo recalculado **depois** de atualizar `population_mean`/`population_sd`,
   dentro do laço?
4. O critério de convergência inclui a mudança da média e do desvio? Sem isso o laço pode parar
   antes de a população ter se acomodado.

- [ ] **Step 4: Commit**

```bash
git add tests/test_tri_population_recovery.py
git commit -m "test: recuperacao da populacao de um grupo deslocado (spec 6.1)

Grupo simulado com media verdadeira +0,6 e ancoras travadas. O modo
estimado recupera +0,6; o modo de prior fixo puxa os thetas para baixo e
empurra os itens livres para cima, que e exatamente o bug silencioso que a
equalizacao existe para eliminar. E a DIFERENCA entre os dois modos que
prova que a estimacao esta ligada."
```

---

### Task 10: Casos-limite e o portão de amostra insuficiente

**Files:**
- Test: `tests/test_tri_edge_cases.py`
- (nenhum arquivo de produção: o comportamento já foi implementado nas Tarefas 2, 5 e 7; esta tarefa o **prende**)

**Interfaces:**
- Consumes: `calibrate`, `InsufficientSampleError`, `CalibrationError`, `ResponseMatrix`, `simulate_responses`, `MINIMUM_EXAMINEES_FOR_2PL`, `NO_VARIANCE`.
- Produces: nenhuma API nova.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tri_edge_cases.py
"""Casos-limite da calibragem (spec §8.1, complementos; §6.5).

Todos aparecem em toda aplicacao real de 400-1.500 alunos. O criterio de
aprovacao nao e "nao explode": e "devolve um numero finito e utilizavel, ou
recusa em voz alta". Numero errado e silencioso e o unico desfecho proibido.
"""

from __future__ import annotations

import unittest

import numpy as np

from agente_ia_edu.tri.calibration import (
    CalibrationError,
    InsufficientSampleError,
    calibrate,
)
from agente_ia_edu.tri.model import ResponseMatrix
from agente_ia_edu.tri.quality import MINIMUM_EXAMINEES_FOR_2PL, NO_VARIANCE
from agente_ia_edu.tri.simulation import simulate_responses

SEED = 777
N_ITEMS = 15


def _base_values(n_examinees: int) -> np.ndarray:
    rng = np.random.default_rng(SEED)
    a = rng.uniform(0.8, 1.6, N_ITEMS)
    b = rng.uniform(-1.5, 1.5, N_ITEMS)
    thetas = rng.normal(0.0, 1.0, n_examinees)
    return simulate_responses(a=a, b=b, thetas=thetas, seed=SEED)


class ExtremeRespondentTests(unittest.TestCase):
    """O MLE divergiria para +-infinito nestes dois casos. O EAP nao (spec §6.2)."""

    @classmethod
    def setUpClass(cls):
        values = _base_values(400)
        values[0, :] = 0.0   # zerou a prova
        values[1, :] = 1.0   # gabaritou a prova
        cls.result = calibrate(ResponseMatrix(values))

    def test_the_zero_scorer_gets_a_finite_theta(self):
        self.assertTrue(np.isfinite(self.result.theta[0]))
        self.assertGreater(self.result.theta_se[0], 0.0)

    def test_the_perfect_scorer_gets_a_finite_theta(self):
        self.assertTrue(np.isfinite(self.result.theta[1]))
        self.assertGreater(self.result.theta_se[1], 0.0)

    def test_the_extremes_sit_at_the_ends_of_the_cohort(self):
        self.assertEqual(int(np.argmin(self.result.theta)), 0)
        self.assertEqual(int(np.argmax(self.result.theta)), 1)

    def test_the_extremes_stay_inside_the_quadrature_grid(self):
        # Regressao a media nos extremos e o custo aceito do EAP (spec §6.2);
        # um theta fora da grade seria escala derivada, nao regressao.
        self.assertGreater(float(self.result.theta[0]), -4.0)
        self.assertLess(float(self.result.theta[1]), 4.0)

    def test_the_extremes_are_the_least_precise_estimates(self):
        middle = float(np.median(self.result.theta_se))
        self.assertGreater(float(self.result.theta_se[0]), middle)
        self.assertGreater(float(self.result.theta_se[1]), middle)


class ZeroVarianceItemTests(unittest.TestCase):
    def test_an_item_everyone_got_right_is_reported_as_unestimable(self):
        values = _base_values(400)
        values[:, 4] = 1.0
        result = calibrate(ResponseMatrix(values))
        self.assertIsNone(result.items[4].a)
        self.assertIsNone(result.items[4].b)
        self.assertIn(NO_VARIANCE, result.items[4].flags)

    def test_an_item_everyone_got_wrong_is_reported_as_unestimable(self):
        values = _base_values(400)
        values[:, 9] = 0.0
        result = calibrate(ResponseMatrix(values))
        self.assertIsNone(result.items[9].a)
        self.assertIn(NO_VARIANCE, result.items[9].flags)

    def test_the_surviving_items_are_still_calibrated(self):
        values = _base_values(400)
        values[:, 4] = 1.0
        result = calibrate(ResponseMatrix(values))
        estimated = [item.a for item in result.items if item.a is not None]
        self.assertEqual(len(estimated), N_ITEMS - 1)
        self.assertTrue(all(np.isfinite(value) for value in estimated))

    def test_theta_is_unchanged_by_dropping_an_uninformative_item(self):
        values = _base_values(400)
        values[:, 4] = 1.0
        with_degenerate = calibrate(ResponseMatrix(values)).theta
        without = calibrate(ResponseMatrix(np.delete(values, 4, axis=1))).theta
        np.testing.assert_allclose(with_degenerate, without, atol=1e-9)

    def test_a_matrix_with_almost_no_variance_is_refused_out_loud(self):
        values = _base_values(400)
        values[:, 1:] = 1.0
        with self.assertRaises(CalibrationError):
            calibrate(ResponseMatrix(values))


class ItemAnsweredByEveryoneTests(unittest.TestCase):
    def test_a_fully_answered_item_behaves_like_any_other(self):
        values = _base_values(400)
        values[:, 2] = np.nan
        values[:5, 2] = np.array([1.0, 0.0, 1.0, 0.0, 1.0])
        result = calibrate(ResponseMatrix(values))
        self.assertIsNotNone(result.items[2].a)
        self.assertIsNotNone(result.items[0].a)


class SampleSizeGateTests(unittest.TestCase):
    def test_2pl_is_refused_below_the_spec_floor(self):
        values = _base_values(MINIMUM_EXAMINEES_FOR_2PL - 1)
        with self.assertRaises(InsufficientSampleError) as caught:
            calibrate(ResponseMatrix(values))
        self.assertIn("199", str(caught.exception))
        self.assertIn("RASCH", str(caught.exception))

    def test_2pl_is_accepted_exactly_at_the_floor(self):
        values = _base_values(MINIMUM_EXAMINEES_FOR_2PL)
        result = calibrate(ResponseMatrix(values))
        self.assertEqual(result.model, "2PL")

    def test_rasch_is_the_documented_fallback_below_the_floor(self):
        values = _base_values(MINIMUM_EXAMINEES_FOR_2PL - 1)
        result = calibrate(ResponseMatrix(values), model="RASCH")
        self.assertEqual(result.model, "RASCH")
        self.assertTrue(np.all(np.isfinite(result.theta)))

    def test_the_insufficient_sample_error_is_a_calibration_error(self):
        self.assertTrue(issubclass(InsufficientSampleError, CalibrationError))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tri_edge_cases.py -v`
Expected: A maioria passa (o comportamento já existe). Falham, hoje, os que dependem da mensagem de erro — `test_2pl_is_refused_below_the_spec_floor` exige que a mensagem cite `199` e `RASCH`. Confirme lendo a saída; se a mensagem da Tarefa 7 já atende, o teste passa e a tarefa vira uma trava de regressão, que é o seu propósito.

- [ ] **Step 3: Ajuste a mensagem do portão, se necessário**

Se `test_2pl_is_refused_below_the_spec_floor` falhar, a mensagem de `InsufficientSampleError` em `calibrate` deve ser exatamente:

```python
        raise InsufficientSampleError(
            f"2PL exige ao menos {minimum_examinees} respondentes (spec §6.5); "
            f"a area tem {matrix.n_examinees}. Caia para RASCH ou para nota bruta, "
            "sempre com aviso explicito na tela."
        )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_edge_cases.py -v`
Expected: PASS (16 testes)

- [ ] **Step 5: Commit**

```bash
git add tests/test_tri_edge_cases.py src/agente_ia_edu/tri/calibration.py
git commit -m "test: casos-limite da TRI (zerou, gabaritou, item sem variancia, N<200)"
```

---

### Task 11: Conjunto-ouro conferido contra o `mirt` (R)

**Files:**
- Create: `tests/fixtures/tri_gold_set.json`
- Create: `tests/fixtures/generate_tri_gold_set.py`
- Test: `tests/test_tri_gold_set.py`

**Interfaces:**
- Consumes: `simulate_responses`, `calibrate`, `ResponseMatrix`.
- Produces: `tests/fixtures/tri_gold_set.json`, com o schema `{"metadata": {...}, "responses": [[int, ...], ...], "mirt": {"a": [float, ...], "b": [float, ...]}}`.

**Por quê.** A Tarefa 8 prova que o motor recupera a verdade que ele mesmo simulou. Isso não exclui um viés compartilhado entre o simulador e o estimador (ambos usam `probability_2pl`). Conferir contra o `mirt` — a implementação de referência da área, independente deste código — fecha essa porta.

- [ ] **Step 1: Gere a matriz fixa**

```python
# tests/fixtures/generate_tri_gold_set.py
"""Gera a matriz de respostas do conjunto-ouro (spec §8.1).

Rode UMA VEZ. O resultado (tests/fixtures/tri_gold_set.json) e versionado e
nunca regenerado sem trocar tambem os parametros do mirt: a matriz e os
parametros de referencia sao um par indivisivel.

    .venv/bin/python tests/fixtures/generate_tri_gold_set.py
"""

from __future__ import annotations

import json
import pathlib

import numpy as np

from agente_ia_edu.tri.simulation import simulate_responses

SEED = 31415
N_EXAMINEES = 600
N_ITEMS = 20
OUTPUT = pathlib.Path(__file__).parent / "tri_gold_set.json"


def main() -> None:
    rng = np.random.default_rng(SEED)
    true_a = rng.uniform(0.7, 1.8, N_ITEMS)
    true_b = rng.uniform(-1.8, 1.8, N_ITEMS)
    thetas = rng.normal(0.0, 1.0, N_EXAMINEES)
    responses = simulate_responses(a=true_a, b=true_b, thetas=thetas, seed=SEED)

    payload = {
        "metadata": {
            "seed": SEED,
            "n_examinees": N_EXAMINEES,
            "n_items": N_ITEMS,
            "generated_by": "tests/fixtures/generate_tri_gold_set.py",
            "true_a": [round(float(value), 6) for value in true_a],
            "true_b": [round(float(value), 6) for value in true_b],
            "mirt_version": "PREENCHER no Step 3",
            "mirt_command": "PREENCHER no Step 3",
        },
        "responses": responses.astype(int).tolist(),
        "mirt": {"a": [], "b": []},
    }
    OUTPUT.write_text(json.dumps(payload, indent=1) + "\n", encoding="utf-8")
    print(f"Escrito {OUTPUT} ({N_EXAMINEES}x{N_ITEMS}).")
    print("Proximo passo: rodar o script R do plano e colar 'mirt'.")


if __name__ == "__main__":
    main()
```

Run: `.venv/bin/python tests/fixtures/generate_tri_gold_set.py`

- [ ] **Step 2: Exporte a matriz para CSV**

```bash
.venv/bin/python -c "
import json, pathlib, csv
payload = json.loads(pathlib.Path('tests/fixtures/tri_gold_set.json').read_text())
with open('/tmp/tri_gold_set.csv', 'w', newline='') as handle:
    writer = csv.writer(handle)
    writer.writerow([f'Item{i+1}' for i in range(payload['metadata']['n_items'])])
    writer.writerows(payload['responses'])
print('/tmp/tri_gold_set.csv')
"
```

- [ ] **Step 3: Rode o `mirt` em R e cole os parâmetros**

```r
# Instalar uma vez: install.packages("mirt")
library(mirt)
data <- read.csv("/tmp/tri_gold_set.csv")
model <- mirt(
  data, 1, itemtype = "2PL",
  quadpts = 41,
  TOL = 1e-4,
  technical = list(NCYCLES = 500)
)
parameters <- coef(model, IRTpars = TRUE, simplify = TRUE)$items
cat(sprintf('"a": [%s],\n', paste(round(parameters[, "a"], 6), collapse = ", ")))
cat(sprintf('"b": [%s]\n',  paste(round(parameters[, "b"], 6), collapse = ", ")))
cat(sprintf('mirt %s\n', as.character(packageVersion("mirt"))))
```

`IRTpars = TRUE` é obrigatório: sem ele o `mirt` devolve a parametrização inclinação-intercepto (`a`, `d = -a*b`), que **não** é a métrica deste motor. `quadpts = 41`, `TOL = 1e-4` e `NCYCLES = 500` alinham a configuração com a spec §6.1.

Cole os dois vetores em `tests/fixtures/tri_gold_set.json` sob a chave `"mirt"`, e preencha `metadata.mirt_version` (ex.: `"1.42.0"`) e `metadata.mirt_command` com o comando `mirt(...)` acima em uma linha.

**Se o R ou o pacote `mirt` não estiverem disponíveis no ambiente:** marque esta tarefa como BLOQUEADA no checklist, deixe `"mirt": {"a": [], "b": []}` no arquivo e siga para a Tarefa 12. O teste do Step 4 pula sozinho nesse estado (`skipTest`), e o relatório da fase deve registrar que a conferência externa está pendente — a Tarefa 8 continua sendo a evidência principal.

- [ ] **Step 4: Write the failing test**

```python
# tests/test_tri_gold_set.py
"""Conjunto-ouro: parametros conferidos contra o mirt (R), spec §8.1.

A Tarefa 8 prova que o motor recupera a verdade que ELE MESMO simulou - o
simulador e o estimador compartilham probability_2pl, entao um viés comum aos
dois passaria despercebido. O mirt e a implementacao de referencia da area e
nao compartilha uma linha de codigo com este motor.

A tolerancia e de CONCORDANCIA entre implementacoes, nao de recuperacao: as
duas resolvem o mesmo problema de otimizacao por caminhos numericos diferentes
(ponto de partida, criterio de parada por parametro vs. por log-verossimilhanca,
tratamento da fronteira), e uma diferenca de centesimos e esperada.
"""

from __future__ import annotations

import json
import pathlib
import unittest

import numpy as np

from agente_ia_edu.tri.calibration import calibrate
from agente_ia_edu.tri.model import ResponseMatrix

FIXTURE = pathlib.Path(__file__).parent / "fixtures" / "tri_gold_set.json"

MAXIMUM_B_DIFFERENCE = 0.10
MAXIMUM_A_DIFFERENCE = 0.15
MAXIMUM_MEAN_B_DIFFERENCE = 0.03


class GoldSetTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not FIXTURE.exists():
            raise unittest.SkipTest(f"conjunto-ouro ausente: {FIXTURE}")
        cls.payload = json.loads(FIXTURE.read_text(encoding="utf-8"))
        if not cls.payload["mirt"]["a"]:
            raise unittest.SkipTest(
                "conjunto-ouro sem parametros do mirt: rode o script R do plano "
                "(Tarefa 11, Step 3) e preencha a chave 'mirt'"
            )
        cls.result = calibrate(
            ResponseMatrix(np.array(cls.payload["responses"], dtype=float))
        )
        cls.a = np.array([item.a for item in cls.result.items])
        cls.b = np.array([item.b for item in cls.result.items])
        cls.mirt_a = np.array(cls.payload["mirt"]["a"], dtype=float)
        cls.mirt_b = np.array(cls.payload["mirt"]["b"], dtype=float)

    def test_the_fixture_records_its_provenance(self):
        metadata = self.payload["metadata"]
        self.assertNotIn("PREENCHER", metadata["mirt_version"])
        self.assertNotIn("PREENCHER", metadata["mirt_command"])
        self.assertIn("IRTpars", metadata["mirt_command"])

    def test_the_fixture_shape_matches_its_metadata(self):
        metadata = self.payload["metadata"]
        self.assertEqual(len(self.payload["responses"]), metadata["n_examinees"])
        self.assertEqual(len(self.payload["responses"][0]), metadata["n_items"])
        self.assertEqual(self.mirt_a.size, metadata["n_items"])
        self.assertEqual(self.mirt_b.size, metadata["n_items"])

    def test_every_difficulty_agrees_with_mirt(self):
        difference = np.abs(self.b - self.mirt_b)
        worst = int(np.argmax(difference))
        self.assertLess(
            float(difference.max()),
            MAXIMUM_B_DIFFERENCE,
            f"item {worst}: b={self.b[worst]:.4f} vs mirt={self.mirt_b[worst]:.4f}",
        )

    def test_every_discrimination_agrees_with_mirt(self):
        difference = np.abs(self.a - self.mirt_a)
        worst = int(np.argmax(difference))
        self.assertLess(
            float(difference.max()),
            MAXIMUM_A_DIFFERENCE,
            f"item {worst}: a={self.a[worst]:.4f} vs mirt={self.mirt_a[worst]:.4f}",
        )

    def test_there_is_no_systematic_shift_against_mirt(self):
        # Um deslocamento medio seria escala derivada - erro que nenhuma
        # diferenca item a item dentro da tolerancia revelaria.
        self.assertLess(
            abs(float(np.mean(self.b - self.mirt_b))), MAXIMUM_MEAN_B_DIFFERENCE
        )

    def test_the_engine_also_recovers_the_generating_parameters(self):
        true_b = np.array(self.payload["metadata"]["true_b"], dtype=float)
        correlation = float(np.corrcoef(true_b, self.b)[0, 1])
        self.assertGreater(correlation, 0.95)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 5: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_gold_set.py -v`
Expected: PASS (6 testes), ou SKIPPED com a mensagem sobre o `mirt` se o Step 3 ficou bloqueado.

- [ ] **Step 6: Commit**

```bash
git add tests/fixtures/tri_gold_set.json tests/fixtures/generate_tri_gold_set.py \
        tests/test_tri_gold_set.py
git commit -m "test: conjunto-ouro da TRI conferido contra o mirt (R)"
```

---

### Task 12: Escala de reporte — theta vira nota

**Files:**
- Create: `src/agente_ia_edu/tri/scale.py`
- Modify: `src/agente_ia_edu/tri/__init__.py`
- Test: `tests/test_tri_reporting_scale.py`

**Interfaces:**
- Consumes: nada do motor (puro NumPy).
- Produces:
  - `DEFAULT_THETA_MIN = -3.0`, `DEFAULT_THETA_MAX = 3.0`
  - `ReportingScaleError(ValueError)`
  - `ReportingScale(area_code: str, min_score: float, median_score: float, max_score: float, theta_min: float = -3.0, theta_max: float = 3.0, reference_label: str = "", reference_source: str | None = None, reference_verified_at: datetime | None = None)` — dataclass frozen; valida no `__post_init__`.
  - `ReportingScale.is_verified -> bool`
  - `ReportingScale.to_score(theta: float) -> float` e `ReportingScale.to_scores(thetas: np.ndarray) -> np.ndarray`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tri_reporting_scale.py
"""Escala de reporte: theta -> nota legivel (spec §6.3 e §6.3.1)."""

from __future__ import annotations

import unittest
from datetime import datetime, timezone

import numpy as np

from agente_ia_edu.tri.scale import (
    DEFAULT_THETA_MAX,
    DEFAULT_THETA_MIN,
    ReportingScale,
    ReportingScaleError,
)

# ENEM 2025, Matematica (spec §6.3.1). A mediana de 520 e o valor citado na
# spec como "media nacional real na casa dos 520" - o valor definitivo sai do
# script da Tarefa 16.
MATEMATICA = ReportingScale(
    area_code="MT",
    min_score=312.6,
    median_score=520.0,
    max_score=980.3,
    reference_label="ENEM 2025",
)


class AnchorTests(unittest.TestCase):
    def test_spec_defaults_are_minus_three_and_plus_three(self):
        self.assertEqual(DEFAULT_THETA_MIN, -3.0)
        self.assertEqual(DEFAULT_THETA_MAX, 3.0)
        self.assertEqual(MATEMATICA.theta_min, -3.0)
        self.assertEqual(MATEMATICA.theta_max, 3.0)

    def test_theta_minus_three_maps_to_the_minimum(self):
        self.assertAlmostEqual(MATEMATICA.to_score(-3.0), 312.6, places=1)

    def test_theta_zero_maps_to_the_median(self):
        self.assertAlmostEqual(MATEMATICA.to_score(0.0), 520.0, places=1)

    def test_theta_plus_three_maps_to_the_maximum(self):
        self.assertAlmostEqual(MATEMATICA.to_score(3.0), 980.3, places=1)


class PiecewiseTests(unittest.TestCase):
    def test_the_lower_half_is_linear(self):
        midpoint = MATEMATICA.to_score(-1.5)
        self.assertAlmostEqual(midpoint, (312.6 + 520.0) / 2, places=1)

    def test_the_upper_half_is_linear(self):
        midpoint = MATEMATICA.to_score(1.5)
        self.assertAlmostEqual(midpoint, (520.0 + 980.3) / 2, places=1)

    def test_the_two_halves_have_different_slopes(self):
        lower = (MATEMATICA.to_score(0.0) - MATEMATICA.to_score(-1.0)) / 1.0
        upper = (MATEMATICA.to_score(1.0) - MATEMATICA.to_score(0.0)) / 1.0
        self.assertNotAlmostEqual(lower, upper, places=1)

    def test_the_median_kink_removes_the_inflation_the_spec_names(self):
        # Com mapeamento linear simples entre 312,6 e 980,3, theta = 0 daria
        # 646,4 - mais de 100 pontos acima da media nacional real (spec §6.3).
        naive_midpoint = (312.6 + 980.3) / 2
        self.assertGreater(naive_midpoint - MATEMATICA.to_score(0.0), 100.0)

    def test_the_mapping_is_monotonic(self):
        thetas = np.linspace(-4.0, 4.0, 200)
        scores = MATEMATICA.to_scores(thetas)
        self.assertTrue(np.all(np.diff(scores) >= 0.0))


class ClampTests(unittest.TestCase):
    def test_theta_below_the_range_is_pinned_to_the_minimum(self):
        self.assertAlmostEqual(MATEMATICA.to_score(-9.0), 312.6, places=1)

    def test_theta_above_the_range_is_pinned_to_the_maximum(self):
        self.assertAlmostEqual(MATEMATICA.to_score(9.0), 980.3, places=1)

    def test_vectorised_conversion_clamps_too(self):
        scores = MATEMATICA.to_scores(np.array([-10.0, 0.0, 10.0]))
        np.testing.assert_allclose(scores, [312.6, 520.0, 980.3], atol=0.05)


class ValidationTests(unittest.TestCase):
    def test_a_non_monotonic_reference_triple_is_refused(self):
        with self.assertRaises(ReportingScaleError):
            ReportingScale(area_code="MT", min_score=500.0, median_score=400.0, max_score=900.0)

    def test_a_median_above_the_maximum_is_refused(self):
        with self.assertRaises(ReportingScaleError):
            ReportingScale(area_code="MT", min_score=300.0, median_score=950.0, max_score=900.0)

    def test_a_theta_range_that_does_not_straddle_zero_is_refused(self):
        with self.assertRaises(ReportingScaleError):
            ReportingScale(
                area_code="MT", min_score=300.0, median_score=500.0, max_score=900.0,
                theta_min=0.5, theta_max=3.0,
            )

    def test_an_empty_area_code_is_refused(self):
        with self.assertRaises(ReportingScaleError):
            ReportingScale(area_code="", min_score=300.0, median_score=500.0, max_score=900.0)


class VerificationTests(unittest.TestCase):
    def test_a_scale_without_a_verification_stamp_is_unverified(self):
        self.assertFalse(MATEMATICA.is_verified)

    def test_a_stamped_scale_is_verified(self):
        verified = ReportingScale(
            area_code="MT", min_score=312.6, median_score=520.0, max_score=980.3,
            reference_source="INEP - microdados ENEM 2025",
            reference_verified_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
        )
        self.assertTrue(verified.is_verified)

    def test_an_unverified_scale_still_computes_a_score(self):
        # Spec §6.3.1: o sistema CALCULA e mostra como provisorio; quem recusa a
        # publicacao e o publication_gate, nao a escala.
        self.assertGreater(MATEMATICA.to_score(0.5), 0.0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tri_reporting_scale.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.tri.scale'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agente_ia_edu/tri/scale.py
"""Escala de reporte: converte theta em nota legivel (spec §6.3).

Mapeamento linear POR PARTES, passando por tres pontos:

    theta_min (-3.0) -> min_score
    0.0              -> median_score
    theta_max (+3.0) -> max_score

A mediana existe porque um mapeamento linear simples entre minimo e maximo
infla a nota do miolo da distribuicao. A distribuicao do ENEM e fortemente
assimetrica: em Matematica 2025 o ponto medio entre minimo e maximo e 646,
contra uma media nacional real na casa dos 520 - mais de 100 pontos de inflacao
para todo aluno mediano.

Os extremos sao os extremos TEORICOS de theta, nao o pior e o melhor aluno do
simulado: minimo e maximo observados sao as duas estatisticas menos estaveis de
qualquer distribuicao - cada uma e determinada por uma unica pessoa - e usa-las
deslocaria a regua inteira a cada aplicacao.

O que essa nota e: uma escala de referencia DA ESCOLA, construida para ser
legivel na faixa do ENEM. O que ela NAO e: previsao de nota do ENEM. A
populacao da escola nao e a populacao nacional e nenhum item em comum liga as
duas provas. Os relatorios precisam apresenta-la com esse rotulo.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

import numpy as np

DEFAULT_THETA_MIN = -3.0
DEFAULT_THETA_MAX = 3.0
_SCORE_DECIMALS = 1


class ReportingScaleError(ValueError):
    """A regua e internamente inconsistente e nao pode converter theta em nota."""


@dataclass(frozen=True)
class ReportingScale:
    """Uma regua, por escola e por area. Espelha uma linha de ``tri_scales``."""

    area_code: str
    min_score: float
    median_score: float
    max_score: float
    theta_min: float = DEFAULT_THETA_MIN
    theta_max: float = DEFAULT_THETA_MAX
    reference_label: str = ""
    reference_source: str | None = None
    reference_verified_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.area_code:
            raise ReportingScaleError("A regua precisa declarar area_code")
        if not (self.min_score < self.median_score < self.max_score):
            raise ReportingScaleError(
                f"A regua de {self.area_code!r} precisa de "
                f"min < mediana < max; recebido {self.min_score}, "
                f"{self.median_score}, {self.max_score}"
            )
        if not (self.theta_min < 0.0 < self.theta_max):
            raise ReportingScaleError(
                f"A faixa de theta da regua de {self.area_code!r} precisa conter "
                f"o zero (a mediana e ancorada em theta = 0); recebido "
                f"[{self.theta_min}, {self.theta_max}]"
            )

    @property
    def is_verified(self) -> bool:
        """Spec §6.3.1: regua com ``reference_verified_at`` nulo nao publica nota."""
        return self.reference_verified_at is not None

    def to_scores(self, thetas) -> np.ndarray:
        """Converte um vetor de thetas em notas, com clamp fora da faixa."""
        clamped = np.clip(np.asarray(thetas, dtype=float), self.theta_min, self.theta_max)
        lower = self.min_score + (clamped - self.theta_min) * (
            (self.median_score - self.min_score) / (0.0 - self.theta_min)
        )
        upper = self.median_score + clamped * (
            (self.max_score - self.median_score) / self.theta_max
        )
        return np.round(np.where(clamped <= 0.0, lower, upper), _SCORE_DECIMALS)

    def to_score(self, theta: float) -> float:
        """Converte um theta em nota."""
        return float(self.to_scores(np.array([float(theta)]))[0])


__all__ = [
    "DEFAULT_THETA_MAX",
    "DEFAULT_THETA_MIN",
    "ReportingScale",
    "ReportingScaleError",
]
```

Acrescente a `src/agente_ia_edu/tri/__init__.py`:

```python
from .scale import ReportingScale, ReportingScaleError
```

e as duas entradas correspondentes em `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_reporting_scale.py -v`
Expected: PASS (17 testes)

- [ ] **Step 5: Escrever o teste de ponte com o default da coluna**

`DEFAULT_THETA_MIN`/`DEFAULT_THETA_MAX` existem duas vezes de propósito: aqui, em
`tri/scale.py`, e em `db/models/tri.py` (Fase 1), onde são o default das colunas
`reference_theta_min`/`reference_theta_max` de `tri_scales`. A duplicação é imposta pela
guarda de pureza da Task 1 — `tri/` não pode importar `agente_ia_edu.db.*`, e essa fronteira
vale mais do que evitar a repetição de dois floats.

O teste mora em arquivo próprio, e não em `tests/test_tri_reporting_scale.py`, para aquele
arquivo continuar exercitando só o motor puro.

Crie `tests/test_tri_scale_bridge.py`:

```python
"""Ponte entre a constante do motor puro e o default da coluna.

Existe porque a duplicacao e INTENCIONAL e precisa continuar assim: o pacote
``agente_ia_edu.tri`` tem uma guarda (Task 1) que reprova qualquer import de
``agente_ia_edu.db.*``, e e ela que mantem o motor testavel com uma matriz
NumPy e sem banco nenhum. Fazer ``tri/scale.py`` importar do modelo apagaria
essa duplicacao e derrubaria a guarda junto - a repeticao de dois floats e o
preco da fronteira, e e barato.

NAO "conserte" isto trocando a declaracao de ``tri/scale.py`` por um import.
Este teste e a amarracao, e ``tests/`` e o unico lugar que pode importar os
dois lados sem violar fronteira nenhuma.

Se os valores divergirem: a nota gravada no banco teria sido convertida por
uma faixa de theta diferente da que o motor usou para estimar. Nenhum aluno
sairia com nota absurda - todos sairiam com a nota levemente errada, que e a
forma mais cara de errar porque ninguem percebe.
"""

import unittest

from agente_ia_edu.db.models.tri import DEFAULT_THETA_MAX, DEFAULT_THETA_MIN
from agente_ia_edu.tri import scale


class TriScaleBridgeTests(unittest.TestCase):
    def test_the_engine_bounds_match_the_stored_scale_defaults(self):
        self.assertEqual(scale.DEFAULT_THETA_MIN, DEFAULT_THETA_MIN)
        self.assertEqual(scale.DEFAULT_THETA_MAX, DEFAULT_THETA_MAX)

    def test_the_bounds_are_symmetric_and_ordered(self):
        # Protege contra o erro de digitacao que o assertEqual acima nao pega:
        # os dois lados trocados em bloco continuariam iguais entre si.
        self.assertLess(scale.DEFAULT_THETA_MIN, scale.DEFAULT_THETA_MAX)
        self.assertAlmostEqual(scale.DEFAULT_THETA_MIN, -scale.DEFAULT_THETA_MAX)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 6: Rodar o teste de ponte**

Run: `.venv/bin/python -m pytest tests/test_tri_scale_bridge.py -v`
Expected: PASS (2 testes). Se falhar com `ModuleNotFoundError` em
`agente_ia_edu.db.models.tri`, a Fase 1 ainda não foi executada — pare e traga a Fase 1, não
crie o modelo aqui.

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/tri/scale.py src/agente_ia_edu/tri/__init__.py \
        tests/test_tri_reporting_scale.py tests/test_tri_scale_bridge.py
git commit -m "feat: escala de reporte linear por partes ancorada na faixa do ENEM"
```

---

### Task 13: Análise de itens — p-valor, bisserial-pontual, CCI e distratores

**Files:**
- Create: `src/agente_ia_edu/tri/item_analysis.py`
- Modify: `src/agente_ia_edu/tri/__init__.py`
- Test: `tests/test_tri_item_analysis.py`

**Interfaces:**
- Consumes: `ResponseMatrix`, `ItemParameters`, `probability_2pl` (Tarefa 2); `gaussian_quadrature` (Tarefa 3); `CalibrationResult` (Tarefa 7).
- Produces:
  - `corrected_point_biserial(matrix: ResponseMatrix) -> np.ndarray` — correlação item-total **corrigida** (o total exclui o próprio item); `nan` onde não é calculável.
  - `item_characteristic_curve(a: float, b: float, nodes: np.ndarray | None = None) -> tuple[np.ndarray, np.ndarray]`
  - `DistractorStat(option: str, count: int, share: float, mean_theta: float | None, is_correct: bool)`
  - `analyze_distractors(chosen: np.ndarray, thetas: np.ndarray, *, correct_option: str, options: tuple[str, ...] = OPTIONS) -> tuple[DistractorStat, ...]`
  - `OPTIONS = ("A", "B", "C", "D", "E")`, `BLANK = ""`
  - `ItemAnalysis(position, p_value, point_biserial, a, b, flags, icc_theta, icc_probability, n_responses)`
  - `analyze_items(matrix: ResponseMatrix, result: CalibrationResult, *, positions: Sequence[int] | None = None) -> tuple[ItemAnalysis, ...]`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tri_item_analysis.py
"""Analise de itens - painel de qualidade da prova (spec §7).

"Sai diretamente dos parametros calibrados, sem calculo adicional" - exceto
duas estatisticas classicas que o painel precisa e que a TRI nao produz:
o p-valor e a correlacao bisserial-pontual corrigida. A analise de distratores
e o unico calculo que olha a ALTERNATIVA escolhida, nao so o acerto.
"""

from __future__ import annotations

import unittest

import numpy as np

from agente_ia_edu.tri.calibration import calibrate
from agente_ia_edu.tri.item_analysis import (
    BLANK,
    OPTIONS,
    DistractorStat,
    ItemAnalysis,
    analyze_distractors,
    analyze_items,
    corrected_point_biserial,
    item_characteristic_curve,
)
from agente_ia_edu.tri.model import ResponseMatrix
from agente_ia_edu.tri.quality import NEGATIVE_DISCRIMINATION
from agente_ia_edu.tri.simulation import simulate_responses

SEED = 909
N_ITEMS = 12


def _matrix(n_examinees: int = 400) -> ResponseMatrix:
    rng = np.random.default_rng(SEED)
    a = rng.uniform(0.8, 1.8, N_ITEMS)
    b = rng.uniform(-1.5, 1.5, N_ITEMS)
    thetas = rng.normal(0.0, 1.0, n_examinees)
    return ResponseMatrix(simulate_responses(a=a, b=b, thetas=thetas, seed=SEED))


class PointBiserialTests(unittest.TestCase):
    def test_returns_one_value_per_item(self):
        self.assertEqual(corrected_point_biserial(_matrix()).shape, (N_ITEMS,))

    def test_a_well_behaved_item_correlates_positively_with_the_rest(self):
        values = corrected_point_biserial(_matrix())
        self.assertTrue(np.all(values[np.isfinite(values)] > 0.0))

    def test_an_item_with_the_wrong_answer_key_correlates_negatively(self):
        values = _matrix().values.copy()
        values[:, 5] = 1.0 - values[:, 5]  # gabarito invertido
        correlations = corrected_point_biserial(ResponseMatrix(values))
        self.assertLess(float(correlations[5]), 0.0)

    def test_the_correction_excludes_the_item_from_its_own_total(self):
        # Sem a correcao, um item curto correlaciona consigo mesmo e a
        # estatistica fica inflada para cima - um item ruim pareceria aceitavel.
        values = np.array([[1.0, 1.0], [1.0, 0.0], [0.0, 1.0], [0.0, 0.0]])
        correlations = corrected_point_biserial(ResponseMatrix(values))
        self.assertAlmostEqual(float(correlations[0]), 0.0, places=10)

    def test_a_zero_variance_item_yields_nan_not_zero(self):
        values = _matrix().values.copy()
        values[:, 3] = 1.0
        self.assertTrue(np.isnan(corrected_point_biserial(ResponseMatrix(values))[3]))


class CharacteristicCurveTests(unittest.TestCase):
    def test_returns_the_quadrature_grid_by_default(self):
        theta, probability = item_characteristic_curve(1.2, 0.0)
        self.assertEqual(theta.shape, (41,))
        self.assertEqual(probability.shape, (41,))

    def test_the_curve_is_increasing_and_crosses_one_half_at_b(self):
        theta, probability = item_characteristic_curve(1.2, 0.5)
        self.assertTrue(np.all(np.diff(probability) > 0))
        self.assertAlmostEqual(
            float(np.interp(0.5, probability, theta)), 0.5, places=2
        )

    def test_accepts_an_explicit_grid(self):
        grid = np.linspace(-2.0, 2.0, 9)
        theta, probability = item_characteristic_curve(1.0, 0.0, grid)
        np.testing.assert_array_equal(theta, grid)
        self.assertEqual(probability.shape, (9,))


class DistractorTests(unittest.TestCase):
    def _sample(self):
        chosen = np.array(["A"] * 40 + ["B"] * 30 + ["C"] * 20 + [BLANK] * 10)
        thetas = np.concatenate([
            np.full(40, 1.0),    # quem marcou A (gabarito) tem theta alto
            np.full(30, 0.6),    # B atrai theta alto tambem: distrator suspeito
            np.full(20, -1.0),
            np.full(10, -1.5),
        ])
        return chosen, thetas

    def test_reports_one_row_per_option_plus_blank(self):
        chosen, thetas = self._sample()
        stats = analyze_distractors(chosen, thetas, correct_option="A")
        self.assertEqual(len(stats), len(OPTIONS) + 1)
        self.assertIsInstance(stats[0], DistractorStat)
        self.assertEqual([stat.option for stat in stats][-1], BLANK)

    def test_counts_and_shares_are_right(self):
        chosen, thetas = self._sample()
        by_option = {stat.option: stat for stat in analyze_distractors(chosen, thetas, correct_option="A")}
        self.assertEqual(by_option["A"].count, 40)
        self.assertAlmostEqual(by_option["A"].share, 0.4, places=6)
        self.assertEqual(by_option[BLANK].count, 10)

    def test_the_answer_key_is_marked(self):
        chosen, thetas = self._sample()
        by_option = {stat.option: stat for stat in analyze_distractors(chosen, thetas, correct_option="A")}
        self.assertTrue(by_option["A"].is_correct)
        self.assertFalse(by_option["B"].is_correct)

    def test_mean_theta_per_option_exposes_the_attractive_distractor(self):
        chosen, thetas = self._sample()
        by_option = {stat.option: stat for stat in analyze_distractors(chosen, thetas, correct_option="A")}
        self.assertAlmostEqual(by_option["B"].mean_theta, 0.6, places=6)
        self.assertGreater(by_option["B"].mean_theta, by_option["C"].mean_theta)

    def test_an_option_nobody_chose_has_no_mean_theta(self):
        chosen, thetas = self._sample()
        by_option = {stat.option: stat for stat in analyze_distractors(chosen, thetas, correct_option="A")}
        self.assertEqual(by_option["E"].count, 0)
        self.assertIsNone(by_option["E"].mean_theta)

    def test_mismatched_lengths_are_refused(self):
        with self.assertRaises(ValueError):
            analyze_distractors(np.array(["A", "B"]), np.array([1.0]), correct_option="A")


class AnalyzeItemsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        values = _matrix().values.copy()
        values[:, 5] = 1.0 - values[:, 5]  # item 5 com gabarito invertido
        cls.matrix = ResponseMatrix(values)
        cls.result = calibrate(cls.matrix)
        cls.analyses = analyze_items(cls.matrix, cls.result)

    def test_returns_one_analysis_per_item_in_position_order(self):
        self.assertEqual(len(self.analyses), N_ITEMS)
        self.assertIsInstance(self.analyses[0], ItemAnalysis)
        self.assertEqual([item.position for item in self.analyses], list(range(N_ITEMS)))

    def test_carries_the_calibrated_parameters_and_flags(self):
        analysis = self.analyses[0]
        self.assertAlmostEqual(analysis.a, self.result.items[0].a)
        self.assertAlmostEqual(analysis.b, self.result.items[0].b)
        self.assertEqual(analysis.flags, self.result.items[0].flags)

    def test_the_inverted_answer_key_is_caught_by_both_statistics(self):
        suspect = self.analyses[5]
        self.assertIn(NEGATIVE_DISCRIMINATION, suspect.flags)
        self.assertLess(suspect.point_biserial, 0.0)

    def test_the_characteristic_curve_is_attached(self):
        analysis = self.analyses[0]
        self.assertEqual(len(analysis.icc_theta), 41)
        self.assertEqual(len(analysis.icc_probability), 41)

    def test_an_unestimable_item_gets_an_empty_curve_not_a_flat_one(self):
        values = self.matrix.values.copy()
        values[:, 2] = 1.0
        matrix = ResponseMatrix(values)
        analyses = analyze_items(matrix, calibrate(matrix))
        self.assertIsNone(analyses[2].a)
        self.assertEqual(analyses[2].icc_theta, ())
        self.assertEqual(analyses[2].icc_probability, ())

    def test_can_be_narrowed_to_a_subset_of_positions(self):
        subset = analyze_items(self.matrix, self.result, positions=[0, 5])
        self.assertEqual([item.position for item in subset], [0, 5])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tri_item_analysis.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.tri.item_analysis'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agente_ia_edu/tri/item_analysis.py
"""Analise de itens: o painel de qualidade da prova (spec §7).

Dificuldade, discriminacao e curva caracteristica saem direto dos parametros
calibrados. Duas estatisticas classicas entram porque o painel precisa delas e
a TRI nao as produz:

  - p-valor: proporcao bruta de acerto, a leitura que todo professor ja sabe
    interpretar sem saber o que e theta.
  - bisserial-pontual CORRIGIDA: correlacao entre acertar o item e o escore no
    RESTANTE da prova. A correcao (excluir o proprio item do total) importa:
    sem ela o item correlaciona consigo mesmo e a estatistica fica inflada,
    fazendo item ruim parecer aceitavel - exatamente o erro que o painel
    existe para pegar.

A analise de distratores e o unico calculo aqui que olha a ALTERNATIVA
escolhida, e nao so o acerto: qual alternativa errada atraiu os alunos de theta
alto (spec §7).
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .calibration import CalibrationResult
from .model import ResponseMatrix, probability_2pl
from .quadrature import gaussian_quadrature

OPTIONS = ("A", "B", "C", "D", "E")
BLANK = ""

_MINIMUM_RESPONSES_FOR_CORRELATION = 3


@dataclass(frozen=True)
class DistractorStat:
    """Uma alternativa de um item. ``mean_theta`` e ``None`` se ninguem a marcou."""

    option: str
    count: int
    share: float
    mean_theta: float | None
    is_correct: bool


@dataclass(frozen=True)
class ItemAnalysis:
    """Uma linha do painel de qualidade. ``icc_*`` vazios = item nao estimavel."""

    position: int
    p_value: float | None
    point_biserial: float | None
    a: float | None
    b: float | None
    flags: tuple[str, ...]
    icc_theta: tuple[float, ...]
    icc_probability: tuple[float, ...]
    n_responses: int


def corrected_point_biserial(matrix: ResponseMatrix) -> np.ndarray:
    """Correlacao item-total corrigida, um valor por item. ``nan`` se indefinida."""
    observed = matrix.observed
    scored = np.where(observed, matrix.values, 0.0)
    total = scored.sum(axis=1)
    result = np.full(matrix.n_items, np.nan)
    for position in range(matrix.n_items):
        rows = observed[:, position]
        if int(rows.sum()) < _MINIMUM_RESPONSES_FOR_CORRELATION:
            continue
        item = scored[rows, position]
        rest = total[rows] - item
        if item.std() == 0.0 or rest.std() == 0.0:
            continue
        result[position] = float(np.corrcoef(item, rest)[0, 1])
    return result


def item_characteristic_curve(
    a: float, b: float, nodes: np.ndarray | None = None
) -> tuple[np.ndarray, np.ndarray]:
    """Pontos da curva caracteristica do item, prontos para plotar."""
    if nodes is None:
        nodes, _ = gaussian_quadrature()
    return nodes, probability_2pl(nodes, float(a), float(b))


def analyze_distractors(
    chosen: np.ndarray,
    thetas: np.ndarray,
    *,
    correct_option: str,
    options: tuple[str, ...] = OPTIONS,
) -> tuple[DistractorStat, ...]:
    """Contagem, participacao e theta medio por alternativa, mais o branco."""
    chosen = np.asarray(chosen, dtype=object)
    thetas = np.asarray(thetas, dtype=float)
    if chosen.shape != thetas.shape:
        raise ValueError(
            f"chosen e thetas precisam ter o mesmo shape; "
            f"{chosen.shape} vs {thetas.shape}"
        )
    total = max(int(chosen.size), 1)
    stats: list[DistractorStat] = []
    for option in (*options, BLANK):
        mask = chosen == option
        count = int(mask.sum())
        stats.append(
            DistractorStat(
                option=option,
                count=count,
                share=count / total,
                mean_theta=float(thetas[mask].mean()) if count else None,
                is_correct=(option == correct_option),
            )
        )
    return tuple(stats)


def analyze_items(
    matrix: ResponseMatrix,
    result: CalibrationResult,
    *,
    positions: Sequence[int] | None = None,
) -> tuple[ItemAnalysis, ...]:
    """Monta o painel de qualidade dos itens de uma calibragem."""
    if result.n_items != matrix.n_items:
        raise ValueError(
            f"A calibragem tem {result.n_items} itens e a matriz {matrix.n_items}"
        )
    p_values = matrix.item_p_values()
    biserials = corrected_point_biserial(matrix)
    response_counts = matrix.observed.sum(axis=0)
    wanted = range(matrix.n_items) if positions is None else positions
    analyses: list[ItemAnalysis] = []
    for position in wanted:
        parameters = result.items[position]
        if parameters.a is None or parameters.b is None:
            icc_theta: tuple[float, ...] = ()
            icc_probability: tuple[float, ...] = ()
        else:
            theta_grid, probability = item_characteristic_curve(parameters.a, parameters.b)
            icc_theta = tuple(float(value) for value in theta_grid)
            icc_probability = tuple(float(value) for value in probability)
        analyses.append(
            ItemAnalysis(
                position=int(position),
                p_value=None if np.isnan(p_values[position]) else float(p_values[position]),
                point_biserial=(
                    None if np.isnan(biserials[position]) else float(biserials[position])
                ),
                a=parameters.a,
                b=parameters.b,
                flags=parameters.flags,
                icc_theta=icc_theta,
                icc_probability=icc_probability,
                n_responses=int(response_counts[position]),
            )
        )
    return tuple(analyses)


__all__ = [
    "BLANK",
    "OPTIONS",
    "DistractorStat",
    "ItemAnalysis",
    "analyze_distractors",
    "analyze_items",
    "corrected_point_biserial",
    "item_characteristic_curve",
]
```

Acrescente a `src/agente_ia_edu/tri/__init__.py`:

```python
from .item_analysis import (
    DistractorStat,
    ItemAnalysis,
    analyze_distractors,
    analyze_items,
)
```

e as quatro entradas correspondentes em `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_item_analysis.py -v`
Expected: PASS (19 testes)

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/tri/item_analysis.py src/agente_ia_edu/tri/__init__.py \
        tests/test_tri_item_analysis.py
git commit -m "feat: analise de itens com TRI (p-valor, bisserial corrigida, CCI, distratores)"
```

---

### Task 14: `TriProficiencyEstimator` — o encaixe no protocolo `ProficiencyEstimator`

**Files:**
- Create: `src/agente_ia_edu/tri/estimator.py`
- Modify: `src/agente_ia_edu/services/proficiency.py:27-44` (extrai `mastery_band`, sem mudança de comportamento)
- Modify: `src/agente_ia_edu/tri/__init__.py`
- Test: `tests/test_tri_proficiency_estimator.py`

**Interfaces:**
- Consumes: `PedagogicalEvidence`, `ProficiencyEstimate`, `ProficiencyEstimator` (`services/proficiency.py:9-24`); `estimate_eap` (Tarefa 4); `ReportingScale` (Tarefa 12).
- Produces:
  - Em `services/proficiency.py`: `mastery_band(score: float) -> str` — função módulo, exatamente os mesmos limiares já usados em `SimpleProficiencyEstimator.estimate` (`proficiency.py:39-43`).
  - Em `tri/estimator.py`: `DEFAULT_DIFFICULTY_B = {"EASY": -1.0, "MEDIUM": 0.0, "HARD": 1.0}`, `DEFAULT_DISCRIMINATION = 1.0`, `MASTERY_SCALE: ReportingScale`, `TriProficiencyEstimator(difficulty_b=..., discrimination=..., prior_sd=1.0)` com `estimate(evidence) -> ProficiencyEstimate`.

**Nota de fronteira.** `tri/estimator.py` importa de `services/proficiency.py`, que é puro (só `dataclasses` e `typing`; verificado em `proficiency.py:1-6`). A guarda da Tarefa 1 continua valendo: nenhum SQLAlchemy entra por aí.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tri_proficiency_estimator.py
"""Encaixe do motor de TRI no protocolo ProficiencyEstimator.

O docstring de SimpleProficiencyEstimator (services/proficiency.py:28) ja
antecipava este encaixe: "TRI/MIRT can implement this protocol later". Os dois
estimadores precisam ser intercambiaveis de verdade - mesma faixa de
mastery_score, mesmo vocabulario de band - senao trocar um pelo outro muda o
significado do numero exibido sem nenhum erro aparecer.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.services.proficiency import (
    DiagnosticCoveragePolicy,
    PedagogicalEvidence,
    ProficiencyEstimate,
    ProficiencyEstimator,
    SimpleProficiencyEstimator,
    mastery_band,
)
from agente_ia_edu.tri.estimator import (
    DEFAULT_DIFFICULTY_B,
    DEFAULT_DISCRIMINATION,
    MASTERY_SCALE,
    TriProficiencyEstimator,
)


class MasteryBandTests(unittest.TestCase):
    def test_the_extracted_helper_keeps_the_original_thresholds(self):
        self.assertEqual(mastery_band(0.0), "NEEDS_DEVELOPMENT")
        self.assertEqual(mastery_band(39.9), "NEEDS_DEVELOPMENT")
        self.assertEqual(mastery_band(40.0), "INITIAL_DEVELOPMENT")
        self.assertEqual(mastery_band(59.9), "INITIAL_DEVELOPMENT")
        self.assertEqual(mastery_band(60.0), "DEVELOPING")
        self.assertEqual(mastery_band(74.9), "DEVELOPING")
        self.assertEqual(mastery_band(75.0), "CONSISTENT_MASTERY")
        self.assertEqual(mastery_band(89.9), "CONSISTENT_MASTERY")
        self.assertEqual(mastery_band(90.0), "CONSOLIDATED_MASTERY")

    def test_the_simple_estimator_still_agrees_with_it(self):
        estimate = SimpleProficiencyEstimator().estimate([
            PedagogicalEvidence(True, "EASY"),
            PedagogicalEvidence(True, "MEDIUM"),
            PedagogicalEvidence(False, "HARD"),
        ])
        self.assertEqual(estimate.band, mastery_band(estimate.mastery_score))


class ProtocolConformanceTests(unittest.TestCase):
    def test_satisfies_the_protocol(self):
        estimator: ProficiencyEstimator = TriProficiencyEstimator()
        self.assertIsInstance(
            estimator.estimate([PedagogicalEvidence(True, "EASY")]), ProficiencyEstimate
        )

    def test_is_a_drop_in_for_the_coverage_policy(self):
        state = DiagnosticCoveragePolicy().assess(
            [
                PedagogicalEvidence(True, "EASY"),
                PedagogicalEvidence(True, "MEDIUM"),
                PedagogicalEvidence(True, "HARD"),
            ],
            TriProficiencyEstimator(),
        )
        self.assertEqual(state.status, "CONSOLIDATED")

    def test_empty_evidence_matches_the_simple_estimator_contract(self):
        estimate = TriProficiencyEstimator().estimate([])
        self.assertEqual(estimate.mastery_score, 0.0)
        self.assertEqual(estimate.confidence, 0.0)
        self.assertEqual(estimate.band, "NEEDS_DEVELOPMENT")
        self.assertEqual(estimate.evidence_count, 0)


class ScoringTests(unittest.TestCase):
    def test_the_mastery_scale_spans_zero_to_one_hundred_through_fifty(self):
        self.assertEqual(MASTERY_SCALE.min_score, 0.0)
        self.assertEqual(MASTERY_SCALE.median_score, 50.0)
        self.assertEqual(MASTERY_SCALE.max_score, 100.0)

    def test_default_difficulties_order_easy_medium_hard(self):
        self.assertLess(DEFAULT_DIFFICULTY_B["EASY"], DEFAULT_DIFFICULTY_B["MEDIUM"])
        self.assertLess(DEFAULT_DIFFICULTY_B["MEDIUM"], DEFAULT_DIFFICULTY_B["HARD"])
        self.assertEqual(DEFAULT_DISCRIMINATION, 1.0)

    def test_the_score_stays_inside_zero_and_one_hundred(self):
        estimator = TriProficiencyEstimator()
        levels = ("EASY", "MEDIUM", "HARD")
        perfect = estimator.estimate([PedagogicalEvidence(True, level) for level in levels * 5])
        zero = estimator.estimate([PedagogicalEvidence(False, level) for level in levels * 5])
        self.assertLessEqual(perfect.mastery_score, 100.0)
        self.assertGreaterEqual(zero.mastery_score, 0.0)
        self.assertGreater(perfect.mastery_score, zero.mastery_score)

    def test_a_hard_hit_is_worth_more_than_an_easy_one(self):
        estimator = TriProficiencyEstimator()
        hard = estimator.estimate([
            PedagogicalEvidence(True, "HARD"), PedagogicalEvidence(False, "EASY")
        ])
        easy = estimator.estimate([
            PedagogicalEvidence(True, "EASY"), PedagogicalEvidence(False, "HARD")
        ])
        self.assertGreater(hard.mastery_score, easy.mastery_score)

    def test_an_unknown_difficulty_level_falls_back_to_medium(self):
        estimator = TriProficiencyEstimator()
        unknown = estimator.estimate([PedagogicalEvidence(True, "IMPOSSIBLE")])
        medium = estimator.estimate([PedagogicalEvidence(True, "MEDIUM")])
        self.assertAlmostEqual(unknown.mastery_score, medium.mastery_score)

    def test_confidence_grows_with_evidence(self):
        estimator = TriProficiencyEstimator()
        few = estimator.estimate([PedagogicalEvidence(True, "MEDIUM")])
        many = estimator.estimate([PedagogicalEvidence(True, "MEDIUM")] * 12)
        self.assertGreater(many.confidence, few.confidence)
        self.assertLessEqual(many.confidence, 1.0)

    def test_evidence_count_is_reported_verbatim(self):
        estimate = TriProficiencyEstimator().estimate(
            [PedagogicalEvidence(True, "EASY")] * 7
        )
        self.assertEqual(estimate.evidence_count, 7)

    def test_the_band_comes_from_the_shared_helper(self):
        estimate = TriProficiencyEstimator().estimate([
            PedagogicalEvidence(True, "HARD"), PedagogicalEvidence(True, "MEDIUM")
        ])
        self.assertEqual(estimate.band, mastery_band(estimate.mastery_score))

    def test_is_deterministic(self):
        evidence = [PedagogicalEvidence(True, "EASY"), PedagogicalEvidence(False, "HARD")]
        estimator = TriProficiencyEstimator()
        self.assertEqual(estimator.estimate(evidence), estimator.estimate(evidence))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tri_proficiency_estimator.py -v`
Expected: FAIL com `ImportError: cannot import name 'mastery_band'`

- [ ] **Step 3a: Extraia `mastery_band` em `src/agente_ia_edu/services/proficiency.py`**

Acrescente, logo após a definição de `ProficiencyEstimator` (`proficiency.py:24`):

```python
def mastery_band(score: float) -> str:
    """Faixa pedagogica de um mastery_score 0-100.

    Extraida de SimpleProficiencyEstimator para que qualquer estimador que
    implemente ProficiencyEstimator - inclusive o de TRI - produza exatamente
    o mesmo vocabulario de faixa. Dois estimadores com faixas diferentes nao
    sao intercambiaveis, e o ponto do protocolo e que sejam.
    """
    return (
        "NEEDS_DEVELOPMENT" if score < 40 else "INITIAL_DEVELOPMENT" if score < 60
        else "DEVELOPING" if score < 75 else "CONSISTENT_MASTERY" if score < 90
        else "CONSOLIDATED_MASTERY"
    )
```

E substitua, em `SimpleProficiencyEstimator.estimate` (`proficiency.py:39-44`), o bloco:

```python
        band = (
            "NEEDS_DEVELOPMENT" if score < 40 else "INITIAL_DEVELOPMENT" if score < 60
            else "DEVELOPING" if score < 75 else "CONSISTENT_MASTERY" if score < 90
            else "CONSOLIDATED_MASTERY"
        )
        return ProficiencyEstimate(score, confidence, band, len(evidence))
```

por:

```python
        return ProficiencyEstimate(score, confidence, mastery_band(score), len(evidence))
```

- [ ] **Step 3b: Crie `src/agente_ia_edu/tri/estimator.py`**

```python
# src/agente_ia_edu/tri/estimator.py
"""O motor de TRI implementando o protocolo ProficiencyEstimator.

O docstring de SimpleProficiencyEstimator (services/proficiency.py:28) ja
antecipava este encaixe. A traducao que ele faz:

  - PedagogicalEvidence carrega apenas ``is_correct`` e ``difficulty_level``
    (EASY/MEDIUM/HARD), nao um item calibrado. Cada nivel vira uma dificuldade
    ``b`` nominal, com discriminacao unica.
  - O theta sai por EAP sobre a grade de 41 pontos com prior N(0,1) - a mesma
    matematica da calibragem de simulado, o mesmo tratamento honesto de quem
    acertou tudo ou errou tudo (spec §6.2).
  - Theta vira ``mastery_score`` 0-100 pela MESMA escala linear por partes de
    §6.3, ancorada em 0 / 50 / 100. A faixa vem de ``mastery_band``, entao os
    dois estimadores sao intercambiaveis de verdade.

Limite honesto: esses ``b`` sao NOMINAIS, nao calibrados. A spec §1 registra
que os parametros calibrados de simulado ficam presos ao item do simulado e nao
realimentam os pipelines de maestria, porque a prova vem de fora e o sistema
conhece so o gabarito. Este estimador nao contorna isso - ele so oferece uma
escala de maestria com tratamento correto dos extremos.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np

from ..services.proficiency import (
    PedagogicalEvidence,
    ProficiencyEstimate,
    mastery_band,
)
from .scale import ReportingScale
from .theta import estimate_eap

DEFAULT_DIFFICULTY_B = {"EASY": -1.0, "MEDIUM": 0.0, "HARD": 1.0}
DEFAULT_DISCRIMINATION = 1.0
_FALLBACK_LEVEL = "MEDIUM"

MASTERY_SCALE = ReportingScale(
    area_code="MASTERY",
    min_score=0.0,
    median_score=50.0,
    max_score=100.0,
    reference_label="Escala de maestria 0-100",
)


@dataclass(frozen=True)
class TriProficiencyEstimator:
    """Implementa ``ProficiencyEstimator`` com EAP sobre dificuldades nominais."""

    difficulty_b: Mapping[str, float] = field(
        default_factory=lambda: dict(DEFAULT_DIFFICULTY_B)
    )
    discrimination: float = DEFAULT_DISCRIMINATION
    prior_sd: float = 1.0

    def estimate(self, evidence: Sequence[PedagogicalEvidence]) -> ProficiencyEstimate:
        if not evidence:
            return ProficiencyEstimate(0.0, 0.0, mastery_band(0.0), 0)
        fallback = self.difficulty_b.get(_FALLBACK_LEVEL, 0.0)
        b = np.array(
            [self.difficulty_b.get(item.difficulty_level, fallback) for item in evidence]
        )
        a = np.full(b.shape, float(self.discrimination))
        responses = np.array([[1.0 if item.is_correct else 0.0 for item in evidence]])
        estimates = estimate_eap(responses, a, b)
        score = float(MASTERY_SCALE.to_scores(estimates.theta)[0])
        # A incerteza a priori e prior_sd; quanto mais o posterior encolhe em
        # relacao a ela, mais a evidencia disse alguma coisa.
        confidence = round(
            float(np.clip(1.0 - estimates.se[0] / self.prior_sd, 0.0, 1.0)), 4
        )
        return ProficiencyEstimate(
            round(score, 2), confidence, mastery_band(score), len(evidence)
        )


__all__ = [
    "DEFAULT_DIFFICULTY_B",
    "DEFAULT_DISCRIMINATION",
    "MASTERY_SCALE",
    "TriProficiencyEstimator",
]
```

Acrescente a `src/agente_ia_edu/tri/__init__.py`:

```python
from .estimator import TriProficiencyEstimator
```

e a entrada correspondente em `__all__`.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_proficiency_estimator.py tests/test_diagnostic_proficiency.py tests/test_tri_dependency_boundary.py -v`
Expected: PASS em todos — `test_diagnostic_proficiency.py` prova que a extração de `mastery_band` não mudou comportamento nenhum.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/tri/estimator.py src/agente_ia_edu/tri/__init__.py \
        src/agente_ia_edu/services/proficiency.py \
        tests/test_tri_proficiency_estimator.py
git commit -m "feat: TriProficiencyEstimator implementa o protocolo ProficiencyEstimator

Extrai mastery_band de SimpleProficiencyEstimator para que os dois
estimadores usem exatamente o mesmo vocabulario de faixa."
```

---

### Task 15: Seed das réguas ENEM 2025 como package-data

**Files:**
- Create: `src/agente_ia_edu/data/__init__.py`
- Create: `src/agente_ia_edu/data/enem_reference_scales.yaml`
- Create: `src/agente_ia_edu/data/loader.py`
- Modify: `pyproject.toml:49-50` (bloco `[tool.setuptools.package-data]`)
- Test: `tests/test_tri_reference_scales_data.py`

**Interfaces:**
- Consumes: `ReportingScale`, `ReportingScaleError` (Tarefa 12); `yaml` (já é dependência, `pyproject.toml:20`).
- Produces:
  - `ReferenceScaleError(ValueError)`
  - `ReferenceScaleEntry(code: str, label: str, min_score: float, median_score: float | None, max_score: float, median_source: str | None)` com `to_reporting_scale(*, reference_label, reference_source, reference_verified_at=None) -> ReportingScale`
  - `ReferenceScaleFile(version: str, label: str, source: str, verified: bool, theta_min: float, theta_max: float, areas: tuple[ReferenceScaleEntry, ...])` com `area(code) -> ReferenceScaleEntry`
  - `parse_reference_scales_mapping(raw: Any) -> ReferenceScaleFile`
  - `load_reference_scales(name: str = "enem_reference_scales") -> ReferenceScaleFile`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tri_reference_scales_data.py
"""Seed das reguas de referencia do ENEM (spec §6.3.1).

Os minimos e maximos vieram de portal educacional SECUNDARIO, nao do INEP
direto, e por isso estao registrados como nao verificados. As medianas saem dos
microdados publicos, pelo script da Tarefa 16.

A trava central deste arquivo: uma entrada sem mediana NAO vira regua. Sem isso,
um default silencioso (a media entre minimo e maximo, por exemplo) inflaria a
nota de todo aluno mediano em mais de 100 pontos sem erro nenhum aparecer.
"""

from __future__ import annotations

import unittest

import yaml

from agente_ia_edu.data.loader import (
    ReferenceScaleEntry,
    ReferenceScaleError,
    ReferenceScaleFile,
    load_reference_scales,
    parse_reference_scales_mapping,
)
from agente_ia_edu.tri.scale import ReportingScale

SPEC_TABLE = {
    "LC": (309.2, 794.5),
    "CH": (320.8, 856.4),
    "CN": (308.6, 858.7),
    "MT": (312.6, 980.3),
}


class PackagedFileTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.file = load_reference_scales()

    def test_loads_as_a_reference_scale_file(self):
        self.assertIsInstance(self.file, ReferenceScaleFile)
        self.assertEqual(self.file.version, "enem_2025")

    def test_carries_the_four_enem_areas(self):
        self.assertEqual(
            sorted(entry.code for entry in self.file.areas), sorted(SPEC_TABLE)
        )

    def test_minimums_and_maximums_match_the_spec_table(self):
        for code, (minimum, maximum) in SPEC_TABLE.items():
            entry = self.file.area(code)
            self.assertAlmostEqual(entry.min_score, minimum, places=1, msg=code)
            self.assertAlmostEqual(entry.max_score, maximum, places=1, msg=code)

    def test_the_seed_ships_unverified(self):
        # Spec §6.3.1: numeros de terceira mao nao viram nota de aluno em silencio.
        self.assertFalse(self.file.verified)

    def test_the_seed_records_where_the_numbers_came_from(self):
        self.assertIn("secund", self.file.source.lower())

    def test_every_area_has_a_human_readable_label(self):
        for entry in self.file.areas:
            self.assertTrue(entry.label.strip(), entry.code)

    def test_an_unknown_area_code_is_refused(self):
        with self.assertRaises(ReferenceScaleError):
            self.file.area("ZZ")


class ParsingTests(unittest.TestCase):
    def _raw(self, **overrides) -> dict:
        raw = {
            "version": "teste",
            "label": "Teste",
            "source": "fixture",
            "verified": False,
            "theta_min": -3.0,
            "theta_max": 3.0,
            "areas": [
                {
                    "code": "MT", "label": "Matematica",
                    "min_score": 312.6, "median_score": 520.0, "max_score": 980.3,
                    "median_source": "microdados ENEM 2025",
                }
            ],
        }
        raw.update(overrides)
        return raw

    def test_parses_a_complete_mapping(self):
        parsed = parse_reference_scales_mapping(self._raw())
        self.assertIsInstance(parsed.areas[0], ReferenceScaleEntry)
        self.assertEqual(parsed.areas[0].median_score, 520.0)

    def test_a_missing_version_is_refused(self):
        raw = self._raw()
        del raw["version"]
        with self.assertRaises(ReferenceScaleError):
            parse_reference_scales_mapping(raw)

    def test_a_non_mapping_is_refused(self):
        with self.assertRaises(ReferenceScaleError):
            parse_reference_scales_mapping(["not", "a", "mapping"])

    def test_a_file_with_no_areas_is_refused(self):
        with self.assertRaises(ReferenceScaleError):
            parse_reference_scales_mapping(self._raw(areas=[]))

    def test_duplicate_area_codes_are_refused(self):
        raw = self._raw()
        raw["areas"] = raw["areas"] * 2
        with self.assertRaises(ReferenceScaleError):
            parse_reference_scales_mapping(raw)

    def test_a_null_median_parses_but_is_marked_missing(self):
        raw = self._raw()
        raw["areas"][0]["median_score"] = None
        raw["areas"][0]["median_source"] = None
        parsed = parse_reference_scales_mapping(raw)
        self.assertIsNone(parsed.areas[0].median_score)

    def test_a_median_outside_the_min_max_range_is_refused(self):
        raw = self._raw()
        raw["areas"][0]["median_score"] = 1200.0
        with self.assertRaises(ReferenceScaleError):
            parse_reference_scales_mapping(raw)

    def test_a_median_without_a_source_is_refused(self):
        raw = self._raw()
        raw["areas"][0]["median_source"] = None
        with self.assertRaises(ReferenceScaleError):
            parse_reference_scales_mapping(raw)

    def test_an_unknown_file_name_is_refused(self):
        with self.assertRaises(ReferenceScaleError):
            load_reference_scales("nao_existe")


class ToReportingScaleTests(unittest.TestCase):
    def _entry(self, median: float | None) -> ReferenceScaleEntry:
        return ReferenceScaleEntry(
            code="MT", label="Matematica",
            min_score=312.6, median_score=median, max_score=980.3,
            median_source="microdados ENEM 2025" if median is not None else None,
        )

    def test_builds_a_reporting_scale_when_the_median_is_known(self):
        scale = self._entry(520.0).to_reporting_scale(
            reference_label="ENEM 2025", reference_source="portal secundario"
        )
        self.assertIsInstance(scale, ReportingScale)
        self.assertEqual(scale.area_code, "MT")
        self.assertAlmostEqual(scale.to_score(0.0), 520.0, places=1)

    def test_refuses_to_build_a_scale_without_a_median(self):
        with self.assertRaises(ReferenceScaleError) as caught:
            self._entry(None).to_reporting_scale(
                reference_label="ENEM 2025", reference_source="portal secundario"
            )
        self.assertIn("mediana", str(caught.exception).lower())

    def test_the_scale_is_unverified_unless_a_stamp_is_given(self):
        scale = self._entry(520.0).to_reporting_scale(
            reference_label="ENEM 2025", reference_source="portal secundario"
        )
        self.assertFalse(scale.is_verified)


class YamlShapeTests(unittest.TestCase):
    def test_the_packaged_file_is_valid_yaml_with_the_expected_keys(self):
        import agente_ia_edu.data

        path = (
            __import__("pathlib").Path(agente_ia_edu.data.__file__).parent
            / "enem_reference_scales.yaml"
        )
        raw = yaml.safe_load(path.read_text(encoding="utf-8"))
        self.assertEqual(
            sorted(raw),
            sorted(["areas", "label", "source", "theta_max", "theta_min", "verified", "version"]),
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tri_reference_scales_data.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.data'`

- [ ] **Step 3a: Crie o pacote e o YAML**

```bash
mkdir -p src/agente_ia_edu/data
```

`src/agente_ia_edu/data/__init__.py`:

```python
"""Arquivos de dados de referencia versionados. O numero normativo vive aqui, nao em codigo."""
```

`src/agente_ia_edu/data/enem_reference_scales.yaml`:

```yaml
# Reguas de referencia do ENEM 2025 (spec 2026-09-29 §6.3.1).
#
# PROCEDENCIA. Minimos e maximos vieram de portal educacional SECUNDARIO, nao
# do INEP direto. Por isso `verified: false`: o sistema calcula a nota e a
# mostra marcada como provisoria, e exige confirmacao da coordenacao antes de
# publicar. Numeros de terceira mao nao viram nota de aluno em silencio.
#
# MEDIANAS. Calculadas a partir das colunas NU_NOTA_* dos microdados publicos
# do ENEM 2025, que trazem a nota de cada participante, por
# scripts/compute_enem_reference_medians.py. Enquanto uma mediana for null, a
# area nao vira regua: o loader recusa. Um default silencioso - o ponto medio
# entre minimo e maximo, por exemplo - inflaria a nota de todo aluno mediano em
# mais de 100 pontos (spec §6.3).
#
# Este arquivo e revisado em pull request, como agente_ia_edu/rubrics/*.yaml.
version: enem_2025
label: "ENEM 2025 - faixa de referencia por area"
source: "Portal educacional secundario (nao INEP direto) - minimos e maximos"
verified: false
theta_min: -3.0
theta_max: 3.0
areas:
  - code: LC
    label: "Linguagens, Codigos e suas Tecnologias"
    min_score: 309.2
    median_score: null
    max_score: 794.5
    median_source: null
  - code: CH
    label: "Ciencias Humanas e suas Tecnologias"
    min_score: 320.8
    median_score: null
    max_score: 856.4
    median_source: null
  - code: CN
    label: "Ciencias da Natureza e suas Tecnologias"
    min_score: 308.6
    median_score: null
    max_score: 858.7
    median_source: null
  - code: MT
    label: "Matematica e suas Tecnologias"
    min_score: 312.6
    median_score: null
    max_score: 980.3
    median_source: null
```

- [ ] **Step 3b: Crie `src/agente_ia_edu/data/loader.py`**

```python
# src/agente_ia_edu/data/loader.py
"""Le e valida estruturalmente o seed das reguas de referencia (spec §6.3.1).

Segue o padrao de ``agente_ia_edu.rubrics.loader``: o YAML e o artefato
revisavel, e este loader recusa um arquivo estruturalmente incompleto para que
um seed pela metade nunca chegue ao banco.

A regra que da dente ao §6.3.1: ``to_reporting_scale`` recusa uma area sem
mediana. Sem isso o caminho de menor resistencia seria usar o ponto medio entre
minimo e maximo, que em Matematica 2025 daria 646 contra uma media nacional
real na casa dos 520 - mais de 100 pontos de inflacao para todo aluno mediano,
sem erro nenhum aparecer em lugar nenhum.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

import yaml

from ..tri.scale import ReportingScale

_DATA_DIR = Path(__file__).parent
_REQUIRED_FILE_FIELDS = ("version", "label", "source", "theta_min", "theta_max")
_REQUIRED_AREA_FIELDS = ("code", "label", "min_score", "max_score")


class ReferenceScaleError(ValueError):
    """O seed de reguas e invalido ou esta incompleto para o uso pedido."""


@dataclass(frozen=True)
class ReferenceScaleEntry:
    """Uma area do seed. ``median_score`` None = ainda nao computada."""

    code: str
    label: str
    min_score: float
    median_score: float | None
    max_score: float
    median_source: str | None
    theta_min: float = -3.0
    theta_max: float = 3.0

    def to_reporting_scale(
        self,
        *,
        reference_label: str,
        reference_source: str,
        reference_verified_at: datetime | None = None,
    ) -> ReportingScale:
        if self.median_score is None:
            raise ReferenceScaleError(
                f"A area {self.code!r} nao tem mediana computada; rode "
                "scripts/compute_enem_reference_medians.py antes de criar a regua. "
                "Sem a mediana, o mapeamento inflaria a nota de todo aluno mediano."
            )
        return ReportingScale(
            area_code=self.code,
            min_score=self.min_score,
            median_score=self.median_score,
            max_score=self.max_score,
            theta_min=self.theta_min,
            theta_max=self.theta_max,
            reference_label=reference_label,
            reference_source=reference_source,
            reference_verified_at=reference_verified_at,
        )


@dataclass(frozen=True)
class ReferenceScaleFile:
    version: str
    label: str
    source: str
    verified: bool
    theta_min: float
    theta_max: float
    areas: tuple[ReferenceScaleEntry, ...]

    def area(self, code: str) -> ReferenceScaleEntry:
        for entry in self.areas:
            if entry.code == code:
                return entry
        raise ReferenceScaleError(
            f"Area {code!r} nao existe no seed {self.version!r}; disponiveis: "
            f"{[entry.code for entry in self.areas]}"
        )


def parse_reference_scales_mapping(raw: Any) -> ReferenceScaleFile:
    if not isinstance(raw, dict):
        raise ReferenceScaleError("O seed de reguas precisa ser um mapeamento")
    for field in _REQUIRED_FILE_FIELDS:
        if raw.get(field) in (None, ""):
            raise ReferenceScaleError(f"Campo obrigatorio ausente no seed: {field!r}")

    theta_min = float(raw["theta_min"])
    theta_max = float(raw["theta_max"])
    raw_areas = raw.get("areas") or []
    if not raw_areas:
        raise ReferenceScaleError(f"O seed {raw['version']!r} nao declara area nenhuma")

    areas = tuple(_parse_area(entry, theta_min, theta_max) for entry in raw_areas)
    codes = [entry.code for entry in areas]
    if len(set(codes)) != len(codes):
        raise ReferenceScaleError(f"O seed repete codigos de area: {codes}")

    return ReferenceScaleFile(
        version=str(raw["version"]),
        label=str(raw["label"]),
        source=str(raw["source"]),
        verified=bool(raw.get("verified", False)),
        theta_min=theta_min,
        theta_max=theta_max,
        areas=areas,
    )


def _parse_area(raw: Any, theta_min: float, theta_max: float) -> ReferenceScaleEntry:
    if not isinstance(raw, dict):
        raise ReferenceScaleError("Cada area do seed precisa ser um mapeamento")
    for field in _REQUIRED_AREA_FIELDS:
        if raw.get(field) in (None, ""):
            raise ReferenceScaleError(
                f"Area {raw.get('code')!r}: campo obrigatorio ausente {field!r}"
            )

    code = str(raw["code"])
    minimum = float(raw["min_score"])
    maximum = float(raw["max_score"])
    if minimum >= maximum:
        raise ReferenceScaleError(
            f"Area {code!r}: min_score ({minimum}) precisa ser menor que "
            f"max_score ({maximum})"
        )

    median_raw = raw.get("median_score")
    median_source = raw.get("median_source")
    median: float | None = None
    if median_raw is not None:
        median = float(median_raw)
        if not (minimum < median < maximum):
            raise ReferenceScaleError(
                f"Area {code!r}: mediana ({median}) fora da faixa "
                f"({minimum}, {maximum})"
            )
        if not median_source:
            raise ReferenceScaleError(
                f"Area {code!r}: mediana declarada sem median_source. De onde saiu "
                "o numero e parte do numero."
            )

    return ReferenceScaleEntry(
        code=code,
        label=str(raw["label"]),
        min_score=minimum,
        median_score=median,
        max_score=maximum,
        median_source=str(median_source) if median_source else None,
        theta_min=theta_min,
        theta_max=theta_max,
    )


def load_reference_scales(name: str = "enem_reference_scales") -> ReferenceScaleFile:
    """Carrega ``<name>.yaml`` deste pacote."""
    path = _DATA_DIR / f"{name}.yaml"
    if not path.exists():
        raise ReferenceScaleError(f"Seed de reguas desconhecido: {name!r} ({path})")
    return parse_reference_scales_mapping(yaml.safe_load(path.read_text(encoding="utf-8")))


__all__ = [
    "ReferenceScaleEntry",
    "ReferenceScaleError",
    "ReferenceScaleFile",
    "load_reference_scales",
    "parse_reference_scales_mapping",
]
```

- [ ] **Step 3c: Declare o package-data em `pyproject.toml`**

Substitua o bloco `[tool.setuptools.package-data]` (`pyproject.toml:49-50`) por:

```toml
[tool.setuptools.package-data]
"agente_ia_edu.rubrics" = ["*.yaml"]
"agente_ia_edu.data" = ["*.yaml"]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_reference_scales_data.py -v`
Expected: PASS (20 testes)

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/data/ pyproject.toml tests/test_tri_reference_scales_data.py
git commit -m "feat: seed das reguas de referencia do ENEM 2025 como package-data

Nasce com verified: false e medianas nulas. Area sem mediana nao vira
regua: o loader recusa, em vez de cair num default que inflaria a nota
de todo aluno mediano."
```

---

### Task 16: Script que computa as medianas a partir dos microdados do ENEM

**Files:**
- Create: `scripts/compute_enem_reference_medians.py`
- Test: `tests/test_compute_enem_reference_medians_script.py`

**Interfaces:**
- Consumes: `load_reference_scales`, `ReferenceScaleError` (Tarefa 15).
- Produces (importáveis pelo teste):
  - `AREA_COLUMNS = {"LC": "NU_NOTA_LC", "CH": "NU_NOTA_CH", "CN": "NU_NOTA_CN", "MT": "NU_NOTA_MT"}`
  - `read_area_scores(path: pathlib.Path, *, delimiter: str = ";", encoding: str = "latin-1") -> dict[str, list[float]]`
  - `compute_medians(scores: dict[str, list[float]]) -> dict[str, float]`
  - `apply_medians(yaml_path: pathlib.Path, medians: dict[str, float], *, source: str) -> str`
  - `main(argv: Sequence[str] | None = None) -> int`

**Sem pandas.** `pandas` não é dependência do projeto e não vai virar uma por causa de um script de uso único. O `csv` da stdlib lê o arquivo em fluxo, sem carregar os ~4 GB de microdados na memória.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_compute_enem_reference_medians_script.py
"""Script que computa as medianas de referencia (spec §6.3.1).

As medianas sao calculadas a partir das colunas NU_NOTA_* dos microdados
publicos do ENEM, que trazem a nota de cada participante.

A regra que o teste prende: ausente e zero NAO entram na mediana. Nos
microdados, quem faltou a um dia aparece com a nota em branco ou zero; incluir
essas linhas puxaria a mediana para baixo e deslocaria a regua inteira - um
erro que nenhuma verificacao posterior pegaria, porque o numero resultante e
perfeitamente plausivel.
"""

from __future__ import annotations

import pathlib
import tempfile
import unittest

import yaml

from scripts.compute_enem_reference_medians import (
    AREA_COLUMNS,
    apply_medians,
    compute_medians,
    main,
    read_area_scores,
)

_CSV = "\n".join([
    "NU_INSCRICAO;NU_NOTA_LC;NU_NOTA_CH;NU_NOTA_CN;NU_NOTA_MT",
    "1;500.0;600.0;450.0;700.0",
    "2;400.0;500.0;350.0;600.0",
    "3;600.0;700.0;550.0;800.0",
    "4;;;;",                      # faltou aos dois dias
    "5;0.0;0.0;0.0;0.0",          # zerado/ausente
    "6;450.0;550.0;400.0;650.0",
]) + "\n"

_YAML = """version: teste
label: "Teste"
source: "fixture"
verified: false
theta_min: -3.0
theta_max: 3.0
areas:
  - code: LC
    label: "Linguagens"
    min_score: 309.2
    median_score: null
    max_score: 794.5
    median_source: null
  - code: MT
    label: "Matematica"
    min_score: 312.6
    median_score: null
    max_score: 980.3
    median_source: null
"""


class ColumnMappingTests(unittest.TestCase):
    def test_maps_the_four_enem_areas_to_their_microdata_columns(self):
        self.assertEqual(
            AREA_COLUMNS,
            {
                "LC": "NU_NOTA_LC",
                "CH": "NU_NOTA_CH",
                "CN": "NU_NOTA_CN",
                "MT": "NU_NOTA_MT",
            },
        )


class ReadingTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.csv_path = pathlib.Path(self.directory.name) / "microdados.csv"
        self.csv_path.write_text(_CSV, encoding="latin-1")

    def tearDown(self):
        self.directory.cleanup()

    def test_reads_one_list_of_scores_per_area(self):
        scores = read_area_scores(self.csv_path)
        self.assertEqual(sorted(scores), sorted(AREA_COLUMNS))

    def test_absent_and_zero_rows_are_excluded(self):
        scores = read_area_scores(self.csv_path)
        self.assertEqual(sorted(scores["LC"]), [400.0, 450.0, 500.0, 600.0])

    def test_a_file_without_the_expected_columns_is_refused(self):
        broken = pathlib.Path(self.directory.name) / "broken.csv"
        broken.write_text("A;B\n1;2\n", encoding="latin-1")
        with self.assertRaises(ValueError):
            read_area_scores(broken)


class MedianTests(unittest.TestCase):
    def test_computes_one_median_per_area(self):
        medians = compute_medians({"LC": [400.0, 450.0, 500.0, 600.0], "MT": [600.0]})
        self.assertAlmostEqual(medians["LC"], 475.0)
        self.assertAlmostEqual(medians["MT"], 600.0)

    def test_the_median_is_rounded_to_one_decimal(self):
        medians = compute_medians({"LC": [400.0, 401.0, 402.0]})
        self.assertEqual(medians["LC"], 401.0)

    def test_an_area_with_no_usable_score_is_omitted(self):
        self.assertEqual(compute_medians({"LC": []}), {})


class ApplyTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.yaml_path = pathlib.Path(self.directory.name) / "scales.yaml"
        self.yaml_path.write_text(_YAML, encoding="utf-8")

    def tearDown(self):
        self.directory.cleanup()

    def test_writes_the_median_and_its_source(self):
        updated = apply_medians(
            self.yaml_path, {"LC": 475.0, "MT": 600.0}, source="microdados ENEM 2025"
        )
        raw = yaml.safe_load(updated)
        by_code = {entry["code"]: entry for entry in raw["areas"]}
        self.assertAlmostEqual(by_code["LC"]["median_score"], 475.0)
        self.assertEqual(by_code["LC"]["median_source"], "microdados ENEM 2025")

    def test_never_flips_the_verified_flag(self):
        # Spec §6.3.1: quem verifica e a coordenacao, nao um script.
        updated = apply_medians(self.yaml_path, {"LC": 475.0}, source="microdados")
        self.assertFalse(yaml.safe_load(updated)["verified"])

    def test_a_median_outside_the_area_range_is_refused(self):
        with self.assertRaises(ValueError):
            apply_medians(self.yaml_path, {"LC": 5000.0}, source="microdados")

    def test_a_median_for_an_unknown_area_is_refused(self):
        with self.assertRaises(ValueError):
            apply_medians(self.yaml_path, {"ZZ": 500.0}, source="microdados")

    def test_leaves_areas_it_has_no_median_for_untouched(self):
        updated = apply_medians(self.yaml_path, {"LC": 475.0}, source="microdados")
        by_code = {entry["code"]: entry for entry in yaml.safe_load(updated)["areas"]}
        self.assertIsNone(by_code["MT"]["median_score"])


class CommandLineTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.csv_path = pathlib.Path(self.directory.name) / "microdados.csv"
        self.csv_path.write_text(_CSV, encoding="latin-1")
        self.yaml_path = pathlib.Path(self.directory.name) / "scales.yaml"
        self.yaml_path.write_text(_YAML, encoding="utf-8")

    def tearDown(self):
        self.directory.cleanup()

    def test_dry_run_leaves_the_file_untouched(self):
        before = self.yaml_path.read_text(encoding="utf-8")
        exit_code = main([
            str(self.csv_path), "--scales", str(self.yaml_path),
            "--source", "microdados ENEM 2025",
        ])
        self.assertEqual(exit_code, 0)
        self.assertEqual(self.yaml_path.read_text(encoding="utf-8"), before)

    def test_write_updates_the_file(self):
        exit_code = main([
            str(self.csv_path), "--scales", str(self.yaml_path),
            "--source", "microdados ENEM 2025", "--write",
        ])
        self.assertEqual(exit_code, 0)
        raw = yaml.safe_load(self.yaml_path.read_text(encoding="utf-8"))
        by_code = {entry["code"]: entry for entry in raw["areas"]}
        self.assertAlmostEqual(by_code["LC"]["median_score"], 475.0)

    def test_a_missing_microdata_file_exits_nonzero(self):
        exit_code = main([
            str(self.csv_path.parent / "nope.csv"), "--scales", str(self.yaml_path),
            "--source", "microdados",
        ])
        self.assertNotEqual(exit_code, 0)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_compute_enem_reference_medians_script.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'scripts.compute_enem_reference_medians'`

O import `from scripts.compute_enem_reference_medians import ...` funciona porque `pythonpath = ["src", "."]` põe a raiz do checkout no `sys.path` (`pyproject.toml:57`), o mesmo mecanismo que os testes que importam `tools.extract_cartilha_enem` já usam.

- [ ] **Step 3: Write minimal implementation**

```python
# scripts/compute_enem_reference_medians.py
"""Calcula as medianas de referencia por area a partir dos microdados do ENEM.

Spec §6.3.1: a regua nasce com minimo e maximo de portal secundario e com a
mediana A COMPUTAR. As medianas saem das colunas NU_NOTA_* dos microdados
publicos, que trazem a nota de cada participante.

A mediana nao e detalhe: sem ela, o mapeamento theta -> nota vira uma reta
entre minimo e maximo, e em Matematica 2025 o ponto medio dessa reta e 646
contra uma media nacional real na casa dos 520 - mais de 100 pontos de inflacao
para todo aluno mediano (spec §6.3).

AUSENTE E ZERO NAO ENTRAM. Nos microdados, quem faltou a um dia aparece com a
nota em branco ou zero. Incluir essas linhas puxaria a mediana para baixo e
deslocaria a regua inteira - e o numero resultante seria perfeitamente
plausivel, ou seja, nenhuma verificacao posterior pegaria o erro.

Este script NUNCA marca a regua como verificada. Quem verifica e a coordenacao
(spec §6.3.1).

Uso:
    # baixe MICRODADOS_ENEM_2025.csv do portal de dados abertos do INEP
    .venv/bin/python scripts/compute_enem_reference_medians.py \\
        ~/Downloads/MICRODADOS_ENEM_2025.csv \\
        --source "INEP - microdados ENEM 2025, coluna NU_NOTA_*" \\
        --write
"""

from __future__ import annotations

import argparse
import csv
import pathlib
import statistics
import sys
from collections.abc import Sequence

import yaml

AREA_COLUMNS = {
    "LC": "NU_NOTA_LC",
    "CH": "NU_NOTA_CH",
    "CN": "NU_NOTA_CN",
    "MT": "NU_NOTA_MT",
}

DEFAULT_SCALES_PATH = (
    pathlib.Path(__file__).resolve().parents[1]
    / "src" / "agente_ia_edu" / "data" / "enem_reference_scales.yaml"
)

# Microdados do INEP sao ';' e latin-1 desde sempre; parametrizavel porque a
# codificacao ja mudou de edicao para edicao.
DEFAULT_DELIMITER = ";"
DEFAULT_ENCODING = "latin-1"


def read_area_scores(
    path: pathlib.Path,
    *,
    delimiter: str = DEFAULT_DELIMITER,
    encoding: str = DEFAULT_ENCODING,
) -> dict[str, list[float]]:
    """Le as notas por area, em fluxo, descartando ausentes e zeros."""
    scores: dict[str, list[float]] = {code: [] for code in AREA_COLUMNS}
    with path.open("r", encoding=encoding, newline="") as handle:
        reader = csv.DictReader(handle, delimiter=delimiter)
        header = reader.fieldnames or []
        present = [code for code, column in AREA_COLUMNS.items() if column in header]
        if not present:
            raise ValueError(
                f"{path} nao tem nenhuma coluna NU_NOTA_* conhecida; "
                f"cabecalho lido: {header[:10]}"
            )
        for row in reader:
            for code in present:
                raw = (row.get(AREA_COLUMNS[code]) or "").strip()
                if not raw:
                    continue
                try:
                    value = float(raw.replace(",", "."))
                except ValueError:
                    continue
                if value <= 0.0:
                    continue
                scores[code].append(value)
    return scores


def compute_medians(scores: dict[str, list[float]]) -> dict[str, float]:
    """Mediana por area, com uma casa decimal. Area sem nota nenhuma e omitida."""
    return {
        code: round(statistics.median(values), 1)
        for code, values in scores.items()
        if values
    }


def apply_medians(
    yaml_path: pathlib.Path, medians: dict[str, float], *, source: str
) -> str:
    """Devolve o conteudo do YAML com as medianas preenchidas. Nao escreve em disco."""
    raw = yaml.safe_load(yaml_path.read_text(encoding="utf-8"))
    by_code = {entry["code"]: entry for entry in raw["areas"]}
    for code, median in medians.items():
        if code not in by_code:
            raise ValueError(
                f"Area {code!r} nao existe em {yaml_path}; disponiveis: {sorted(by_code)}"
            )
        entry = by_code[code]
        if not (float(entry["min_score"]) < median < float(entry["max_score"])):
            raise ValueError(
                f"Area {code!r}: mediana {median} fora da faixa "
                f"({entry['min_score']}, {entry['max_score']}). Confira se o arquivo "
                "de microdados e da mesma edicao que o minimo e o maximo da regua."
            )
        entry["median_score"] = median
        entry["median_source"] = source
    # verified nunca e tocado aqui: quem verifica e a coordenacao (spec §6.3.1).
    return yaml.safe_dump(raw, allow_unicode=True, sort_keys=False)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("microdata", type=pathlib.Path, help="CSV dos microdados do ENEM")
    parser.add_argument("--scales", type=pathlib.Path, default=DEFAULT_SCALES_PATH)
    parser.add_argument(
        "--source", required=True,
        help="Procedencia da mediana, gravada em median_source (ex.: 'INEP - microdados ENEM 2025')",
    )
    parser.add_argument("--delimiter", default=DEFAULT_DELIMITER)
    parser.add_argument("--encoding", default=DEFAULT_ENCODING)
    parser.add_argument(
        "--write", action="store_true",
        help="Grava o YAML. Sem esta flag, apenas imprime o resultado.",
    )
    args = parser.parse_args(argv)

    if not args.microdata.is_file():
        print(f"ERRO: arquivo de microdados nao encontrado: {args.microdata}", file=sys.stderr)
        return 2
    if not args.scales.is_file():
        print(f"ERRO: seed de reguas nao encontrado: {args.scales}", file=sys.stderr)
        return 2

    scores = read_area_scores(
        args.microdata, delimiter=args.delimiter, encoding=args.encoding
    )
    medians = compute_medians(scores)
    for code in sorted(medians):
        print(f"{code}: mediana {medians[code]:.1f} (n = {len(scores[code])})")
    if not medians:
        print("ERRO: nenhuma nota utilizavel encontrada.", file=sys.stderr)
        return 1

    try:
        updated = apply_medians(args.scales, medians, source=args.source)
    except ValueError as error:
        print(f"ERRO: {error}", file=sys.stderr)
        return 1

    if args.write:
        args.scales.write_text(updated, encoding="utf-8")
        print(f"\n{args.scales} atualizado.")
        print("A regua continua NAO VERIFICADA: a coordenacao precisa conferir")
        print("minimo e maximo contra a fonte oficial e carimbar reference_verified_at.")
    else:
        print("\n(dry-run; use --write para gravar)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_compute_enem_reference_medians_script.py -v`
Expected: PASS (15 testes)

- [ ] **Step 5: Commit**

```bash
git add scripts/compute_enem_reference_medians.py \
        tests/test_compute_enem_reference_medians_script.py
git commit -m "feat: script que computa as medianas de referencia dos microdados do ENEM

Ausente e zero ficam de fora da mediana. O script nunca marca a regua
como verificada - isso e da coordenacao."
```

- [ ] **Step 6: Checkpoint — o motor puro está completo**

Run: `.venv/bin/python -m pytest tests/test_tri_*.py tests/test_compute_enem_reference_medians_script.py tests/test_diagnostic_proficiency.py -v`
Expected: PASS em tudo (exceto `test_tri_gold_set.py`, que pode estar SKIPPED se o `mirt` não estava disponível).

A partir daqui as tarefas dependem das tabelas das Fases 1 e 2 — veja a seção **Pré-requisitos de outras fases** no topo deste plano.

---

### Task 17: Verificação do contrato de schema entregue pela Fase 1

> **Pré-requisito:** as migrations `057_mock_exam_core`, `058_answer_card_capture` e `059_tri_scales_and_scores` da Fase 1, e `mock_exam_responses` da Fase 2, já aplicadas.

**A Fase 3 NÃO cria tabela nenhuma.** As quatro tabelas `tri_*` são entregues pela Fase 1
(`docs/superpowers/plans/2026-09-29-simulados-fase1-dados-e-cartao.md`, migration
`059_tri_scales_and_scores`, modelos em `src/agente_ia_edu/db/models/tri.py`). Duas fases
criando a mesma tabela é o caminho mais curto para duas definições divergentes da nota de um
aluno.

O que esta tarefa faz é prender, em teste, o **contrato** que o motor da Fase 3 precisa que
esse schema respeite. Sem isso, uma mudança na Fase 1 quebra a persistência da Fase 3 em
produção, não no CI.

**Files:**
- Test: `tests/test_tri_schema_contract_postgresql.py`
- (nenhum arquivo de produção é criado ou modificado)

**Interfaces:**
- Consumes: `TriScale`, `TriCalibration`, `TriItemParameter`, `TriStudentScore`, `MockExam`, `MockExamItem`, `MockExamResponse` (Fases 1 e 2).
- Produces: nenhuma API nova. Produz a garantia de que as Tarefas 18 e 19 podem escrever.

**Diferenças da Fase 1 que as Tarefas 18 e 19 já respeitam** (verificadas em
`2026-09-29-simulados-fase1-dados-e-cartao.md`, migration `059_tri_scales_and_scores`):

| Ponto | O que a Fase 1 entrega | Consequência para a Fase 3 |
|---|---|---|
| `tri_calibrations.status` | `PENDING` / `RUNNING` / `DONE` / `FAILED` | a Fase 3 grava `DONE`, nunca `COMPLETED` |
| `reference_*_score`, `scaled_score`, `percentile_*` | `Numeric(6,2)` / `Numeric(5,2)` | leitura devolve `Decimal`; converter para `float` na fronteira, e só ali |
| `school_id` | só em `tri_scales` | as outras três não têm a coluna; o isolamento é verificado no serviço |
| `tri_student_scores.student_id` | `NOT NULL` | a Fase 3 sempre tem o aluno, então não muda nada |
| unicidade de `tri_calibrations` | `(mock_exam_id, area_code, engine_version)` | recalibrar a mesma área com a mesma `engine_version` exige apagar a anterior |

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tri_schema_contract_postgresql.py
"""Contrato de schema que o motor da Fase 3 exige da Fase 1.

A Fase 3 nao cria tabela nenhuma: as quatro tabelas tri_* vem da migration
059_tri_scales_and_scores. Este teste prende o contrato que a persistencia da
Fase 3 (Tarefas 18 e 19) assume, para que uma mudanca na Fase 1 quebre o CI em
vez de quebrar a nota de um aluno em producao.

Roda contra um PostgreSQL real e descartavel, no molde de
tests/test_material_assignment_model.py.
"""

from __future__ import annotations

import asyncio
import os
import unittest

from sqlalchemy import create_engine, inspect, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    MockExam,
    MockExamItem,
    MockExamResponse,
    TriCalibration,
    TriItemParameter,
    TriScale,
    TriStudentScore,
)
from tests._postgres_test_db import create_database, drop_database

# Coluna -> True se a Fase 3 ESCREVE nela; False se apenas le.
REQUIRED_COLUMNS = {
    "tri_scales": [
        "id", "school_id", "area_code", "base_mock_exam_id",
        "reference_min_score", "reference_median_score", "reference_max_score",
        "reference_theta_min", "reference_theta_max",
        "reference_source", "reference_verified_at",
    ],
    "tri_calibrations": [
        "id", "mock_exam_id", "area_code", "scale_id", "model", "status",
        "n_examinees", "n_items", "converged", "iterations", "log_likelihood",
        "engine_version", "calibrated_at",
    ],
    "tri_item_parameters": [
        "id", "calibration_id", "item_id", "a", "b", "c", "se_a", "se_b",
        "n_responses", "p_value", "point_biserial", "is_fixed", "flags",
    ],
    "tri_student_scores": [
        "id", "calibration_id", "student_id", "area_code", "theta", "theta_se",
        "scaled_score", "raw_correct", "percentile_class", "percentile_school",
    ],
    "mock_exam_items": [
        "id", "school_id", "mock_exam_id", "position", "area_code",
        "correct_option", "is_anchor",
    ],
    "mock_exam_responses": [
        "id", "mock_exam_id", "student_id", "item_id", "chosen_option", "is_correct",
    ],
}

# Colunas que a Fase 3 PRECISA poder deixar nulas, porque o motor devolve None
# para item sem variancia de resposta.
NULLABLE_COLUMNS = {"tri_item_parameters": ["a", "b", "c", "se_a", "se_b", "point_biserial"]}

CALIBRATION_STATUS_WRITTEN_BY_PHASE_3 = "DONE"


class TriSchemaContractPostgreSQLTests(unittest.TestCase):
    database_name = "agente_ia_edu_tri_schema_contract_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "TRI_SCHEMA_TEST_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    async_database_url = os.getenv(
        "TRI_SCHEMA_TEST_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin_execute("SELECT 1")
        except Exception as exc:
            raise unittest.SkipTest("PostgreSQL de teste indisponivel") from exc
        drop_database(cls.admin_url, cls.database_name)
        create_database(cls.admin_url, cls.database_name)
        cls.engine = create_async_engine(cls.async_database_url)
        cls.session_factory = async_sessionmaker(
            cls.engine, class_=AsyncSession, expire_on_commit=False
        )

        async def _prep():
            async with cls.engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
                return await connection.run_sync(
                    lambda sync_connection: {
                        table: {
                            column["name"]: column
                            for column in inspect(sync_connection).get_columns(table)
                        }
                        for table in REQUIRED_COLUMNS
                    }
                )

        cls.columns = asyncio.run(_prep())

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "engine"):
            asyncio.run(cls.engine.dispose())
        drop_database(cls.admin_url, cls.database_name)

    @classmethod
    def _admin_execute(cls, statement):
        engine = create_engine(
            cls.admin_url,
            connect_args={"autocommit": True},
            execution_options={"isolation_level": "AUTOCOMMIT"},
        )
        try:
            with engine.connect() as connection:
                return connection.execute(text(statement))
        finally:
            engine.dispose()

    def test_every_column_the_engine_touches_exists(self):
        for table, names in REQUIRED_COLUMNS.items():
            for name in names:
                self.assertIn(
                    name, self.columns[table],
                    f"{table}.{name} sumiu; a persistencia da Fase 3 depende dela",
                )

    def test_unestimable_item_parameters_can_be_null(self):
        for table, names in NULLABLE_COLUMNS.items():
            for name in names:
                self.assertTrue(
                    self.columns[table][name]["nullable"],
                    f"{table}.{name} virou NOT NULL; item sem variancia de resposta "
                    "nao tem parametro estimavel, e gravar zero produziria uma "
                    "curva caracteristica plana de aparencia legitima",
                )

    def test_the_status_phase_three_writes_is_accepted(self):
        async def scenario():
            async with self.session_factory() as session:
                allowed = (await session.execute(text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'ck_tri_calibrations_status'"
                ))).scalar_one()
                self.assertIn(CALIBRATION_STATUS_WRITTEN_BY_PHASE_3, allowed, allowed)
        asyncio.run(scenario())

    def test_the_model_vocabulary_covers_what_the_engine_produces(self):
        async def scenario():
            async with self.session_factory() as session:
                allowed = (await session.execute(text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conname = 'ck_tri_calibrations_model'"
                ))).scalar_one()
                self.assertIn("2PL", allowed)
                self.assertIn("RASCH", allowed)
        asyncio.run(scenario())

    def test_the_orm_classes_are_importable_under_the_names_phase_three_uses(self):
        for model, table in (
            (TriScale, "tri_scales"),
            (TriCalibration, "tri_calibrations"),
            (TriItemParameter, "tri_item_parameters"),
            (TriStudentScore, "tri_student_scores"),
            (MockExam, "mock_exams"),
            (MockExamItem, "mock_exam_items"),
            (MockExamResponse, "mock_exam_responses"),
        ):
            self.assertEqual(model.__tablename__, table)

    def test_the_scale_exposes_its_verification_stamp(self):
        # Spec §6.3.1: e essa coluna que decide entre nota publicada e provisoria.
        self.assertIn("reference_verified_at", self.columns["tri_scales"])
        self.assertTrue(self.columns["tri_scales"]["reference_verified_at"]["nullable"])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tri_schema_contract_postgresql.py -v`
Expected: FAIL com `ImportError: cannot import name 'TriScale'` se a Fase 1 ainda não foi mesclada. Esse é o sinal de parar: as Tarefas 17–19 dependem dela.

- [ ] **Step 3: Confirme a cadeia de migrations**

```bash
.venv/bin/alembic heads
.venv/bin/alembic upgrade head
```

Expected: o head é `059_tri_scales_and_scores` (ou posterior) e o upgrade passa. Se qualquer coluna da tabela `REQUIRED_COLUMNS` estiver faltando, **não escreva uma migration da Fase 3 para consertar**: a divergência é de contrato entre fases e se resolve na Fase 1, senão passam a existir duas definições da mesma tabela.

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_schema_contract_postgresql.py -v`
Expected: PASS (6 testes)

- [ ] **Step 5: Commit**

```bash
git add tests/test_tri_schema_contract_postgresql.py
git commit -m "test: contrato de schema que o motor de TRI exige da Fase 1

A Fase 3 nao cria tabela: as tri_* vem de 059_tri_scales_and_scores. Este
teste prende o contrato para que uma mudanca la quebre o CI, e nao a nota
de um aluno em producao."
```

---

### Task 18: Camada de persistência — banco → matriz → motor → linhas `tri_*`

> **Pré-requisito:** Tarefa 17 concluída.

**Files:**
- Create: `src/agente_ia_edu/services/tri_calibration.py`
- Test: `tests/test_tri_calibration_service_postgresql.py`

**Interfaces:**
- Consumes: `calibrate`, `CalibrationResult`, `InsufficientSampleError` (Tarefa 7); `ResponseMatrix` (Tarefa 2); `ReportingScale` (Tarefa 12); `corrected_point_biserial` (Tarefa 13); `publication_gate`, `PublicationDecision` (Tarefa 5); `TriScale`, `TriCalibration`, `TriItemParameter`, `TriStudentScore` (Tarefa 17); `MockExamItem`, `MockExamResponse` (Fase 1/2).
- Produces:
  - `CalibrationInputError(RuntimeError)`
  - `CalibrationInput(school_id, mock_exam_id, area_code, student_ids: tuple[uuid.UUID, ...], item_ids: tuple[uuid.UUID, ...], matrix: ResponseMatrix)`
  - `percentiles(scores: np.ndarray) -> np.ndarray`
  - `reporting_scale_from_row(scale: TriScale) -> ReportingScale`
  - `TriCalibrationService(session: AsyncSession)` com:
    - `async load_input(mock_exam_id, area_code) -> CalibrationInput`
    - `async calibrate_and_store(mock_exam_id, area_code, scale_id, *, model="2PL", population=FIXED_STANDARD_NORMAL, fixed_items=None) -> uuid.UUID`
    - `async publication_decision(calibration_id) -> PublicationDecision`

**A fronteira, explicitamente.** Este é o **único** arquivo da Fase 3 que fala com o banco. Ele faz exatamente três coisas: (1) `load_input` lê `mock_exam_responses` e monta uma `ResponseMatrix`; (2) chama `calibrate()`, que não sabe que um banco existe; (3) grava o `CalibrationResult` em linhas. Nenhuma decisão de modelagem estatística mora aqui — se você se pegar escrevendo matemática neste arquivo, ela pertence a `tri/`.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tri_calibration_service_postgresql.py
"""Camada de persistencia do motor de TRI (spec §2.2, §2.4, §3).

O que este teste protege e a FRONTEIRA: o servico traduz banco <-> matriz e
nada mais. Se uma decisao estatistica vazar para ca, ela deixa de ser coberta
pelo teste de recuperacao de parametros, que nao conhece banco nenhum.
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import create_engine, func, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    MockExam,
    MockExamItem,
    MockExamResponse,
    Person,
    School,
    Student,
    TriCalibration,
    TriItemParameter,
    TriScale,
    TriStudentScore,
)
from agente_ia_edu.services.tri_calibration import (
    CalibrationInput,
    CalibrationInputError,
    TriCalibrationService,
    percentiles,
    reporting_scale_from_row,
)
from agente_ia_edu.tri.calibration import ENGINE_VERSION, InsufficientSampleError
from agente_ia_edu.tri.quality import NEGATIVE_DISCRIMINATION, NOT_CONVERGED, SCALE_NOT_VERIFIED
from agente_ia_edu.tri.simulation import simulate_responses
from tests._postgres_test_db import create_database, drop_database

SEED = 5150
N_STUDENTS = 260
N_SMALL_AREA_STUDENTS = 150   # abaixo do piso de 200 do 2PL (spec §6.5)
N_ITEMS = 20
OPTIONS = ("A", "B", "C", "D", "E")


class PurePercentileTests(unittest.TestCase):
    def test_the_lowest_score_gets_the_lowest_percentile(self):
        values = percentiles(np.array([10.0, 20.0, 30.0, 40.0]))
        self.assertLess(values[0], values[-1])
        self.assertAlmostEqual(float(values[0]), 12.5, places=4)
        self.assertAlmostEqual(float(values[-1]), 87.5, places=4)

    def test_ties_share_the_same_percentile(self):
        values = percentiles(np.array([10.0, 10.0, 30.0]))
        self.assertAlmostEqual(float(values[0]), float(values[1]))

    def test_an_empty_cohort_returns_an_empty_array(self):
        self.assertEqual(percentiles(np.array([])).size, 0)


class TriCalibrationServicePostgreSQLTests(unittest.TestCase):
    database_name = "agente_ia_edu_tri_service_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "TRI_SERVICE_TEST_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    async_database_url = os.getenv(
        "TRI_SERVICE_TEST_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin_execute("SELECT 1")
        except Exception as exc:
            raise unittest.SkipTest("PostgreSQL de teste indisponivel") from exc
        drop_database(cls.admin_url, cls.database_name)
        create_database(cls.admin_url, cls.database_name)
        cls.engine = create_async_engine(cls.async_database_url)
        cls.session_factory = async_sessionmaker(
            cls.engine, class_=AsyncSession, expire_on_commit=False
        )
        cls.context = asyncio.run(cls._seed())
        # uq_tri_calibrations (mock_exam_id, area_code, engine_version) da Fase 1
        # permite UMA calibragem de MT por versao de motor. Calibrar uma vez aqui
        # e afirmar sobre as linhas gravadas e mais fiel ao uso real, alem de
        # cortar o tempo da classe.
        cls.mt_calibration_id = asyncio.run(cls._calibrate_mt())

    @classmethod
    async def _calibrate_mt(cls):
        async with cls.session_factory() as session:
            return await TriCalibrationService(session).calibrate_and_store(
                cls.context["mock_exam_id"], "MT", cls.context["scale_id"]
            )

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "engine"):
            asyncio.run(cls.engine.dispose())
        drop_database(cls.admin_url, cls.database_name)

    @classmethod
    def _admin_execute(cls, statement):
        engine = create_engine(
            cls.admin_url,
            connect_args={"autocommit": True},
            execution_options={"isolation_level": "AUTOCOMMIT"},
        )
        try:
            with engine.connect() as connection:
                return connection.execute(text(statement))
        finally:
            engine.dispose()

    @classmethod
    async def _seed(cls) -> dict:
        """Simulado de 260 alunos x 20 itens, com o item 7 de gabarito invertido."""
        async with cls.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        rng = np.random.default_rng(SEED)
        true_a = rng.uniform(0.8, 1.8, N_ITEMS)
        true_b = rng.uniform(-1.5, 1.5, N_ITEMS)
        thetas = rng.normal(0.0, 1.0, N_STUDENTS)
        correct = simulate_responses(a=true_a, b=true_b, thetas=thetas, seed=SEED)
        correct[:, 7] = 1.0 - correct[:, 7]  # gabarito cadastrado errado

        async with cls.session_factory() as session:
            school = School(code="TRI-SERVICO", name="Escola TRI Servico")
            session.add(school)
            await session.flush()

            students = []
            for index in range(N_STUDENTS):
                person = Person(school_id=school.id, full_name=f"Aluno {index:04d}")
                session.add(person)
                await session.flush()
                student = Student(person_id=person.id, school_id=school.id)
                session.add(student)
                students.append(student)
            await session.flush()

            exam = MockExam(
                school_id=school.id, name="Simulado TRI", exam_day=1, status="APPLIED"
            )
            session.add(exam)
            await session.flush()

            items = []
            for position in range(N_ITEMS):
                item = MockExamItem(
                    school_id=school.id,
                    mock_exam_id=exam.id,
                    position=position + 1,
                    area_code="MT" if position < 15 else "CN",
                    correct_option="A",
                )
                session.add(item)
                items.append(item)
            await session.flush()

            for row, student in enumerate(students):
                for column, item in enumerate(items):
                    # Area CN de proposito com menos de 200 respondentes: e o que
                    # faz o portao de amostra insuficiente (§6.5) disparar de
                    # verdade no teste, em vez de so na chamada direta do motor.
                    if column >= 15 and row >= N_SMALL_AREA_STUDENTS:
                        continue
                    is_correct = bool(correct[row, column])
                    session.add(MockExamResponse(
                        school_id=school.id,
                        mock_exam_id=exam.id,
                        student_id=student.id,
                        item_id=item.id,
                        chosen_option="A" if is_correct else OPTIONS[1 + (row + column) % 4],
                        is_correct=is_correct,
                    ))

            scale = TriScale(
                school_id=school.id, area_code="MT", name="ENEM 2025 - MT",
                base_mock_exam_id=exam.id, reference_label="ENEM 2025",
                reference_min_score=312.6, reference_median_score=520.0,
                reference_max_score=980.3,
                reference_source="Portal educacional secundario",
            )
            # NAO existe uma segunda regua MT desta escola: a Fase 1 declara
            # uq_tri_scales_school_area (uma regua por escola e por area). A
            # verificacao e testada carimbando reference_verified_at NESTA regua,
            # que e exatamente o que a coordenacao faz.
            other_school = School(code="TRI-SERVICO-2", name="Outra escola")
            session.add(other_school)
            await session.flush()
            other_school_scale = TriScale(
                school_id=other_school.id, area_code="MT", name="ENEM 2025 - MT",
                reference_label="ENEM 2025",
                reference_min_score=312.6, reference_median_score=520.0,
                reference_max_score=980.3,
                reference_source="Portal educacional secundario",
            )
            session.add(other_school_scale)
            small_area_scale = TriScale(
                school_id=school.id, area_code="CN", name="ENEM 2025 - CN",
                base_mock_exam_id=exam.id, reference_label="ENEM 2025",
                reference_min_score=308.6, reference_median_score=495.0,
                reference_max_score=858.7,
                reference_source="Portal educacional secundario",
            )
            session.add_all([scale, small_area_scale])
            await session.commit()
            return {
                "school_id": school.id,
                "mock_exam_id": exam.id,
                "scale_id": scale.id,
                "small_area_scale_id": small_area_scale.id,
                "other_school_scale_id": other_school_scale.id,
                "item_ids": [item.id for item in items],
            }

    def _run(self, coro):
        return asyncio.run(coro)

    # --- load_input ----------------------------------------------------------

    def test_load_input_builds_a_matrix_for_one_area_only(self):
        async def scenario():
            async with self.session_factory() as session:
                loaded = await TriCalibrationService(session).load_input(
                    self.context["mock_exam_id"], "MT"
                )
                self.assertIsInstance(loaded, CalibrationInput)
                self.assertEqual(loaded.matrix.n_items, 15)
                self.assertEqual(loaded.matrix.n_examinees, N_STUDENTS)
                self.assertEqual(len(loaded.item_ids), 15)
                self.assertEqual(len(loaded.student_ids), N_STUDENTS)
        self._run(scenario())

    def test_load_input_orders_items_by_position(self):
        async def scenario():
            async with self.session_factory() as session:
                loaded = await TriCalibrationService(session).load_input(
                    self.context["mock_exam_id"], "MT"
                )
                self.assertEqual(list(loaded.item_ids), self.context["item_ids"][:15])
        self._run(scenario())

    def test_load_input_refuses_an_area_with_no_response(self):
        async def scenario():
            async with self.session_factory() as session:
                with self.assertRaises(CalibrationInputError):
                    await TriCalibrationService(session).load_input(
                        self.context["mock_exam_id"], "LC"
                    )
        self._run(scenario())

    # --- calibrate_and_store -------------------------------------------------

    def test_calibrate_and_store_writes_one_row_per_item_and_per_student(self):
        async def scenario():
            async with self.session_factory() as session:
                calibration_id = self.mt_calibration_id
                calibration = await session.get(TriCalibration, calibration_id)
                self.assertEqual(calibration.status, "DONE")
                self.assertEqual(calibration.model, "2PL")
                self.assertEqual(calibration.engine_version, ENGINE_VERSION)
                self.assertTrue(calibration.converged)
                self.assertEqual(calibration.n_examinees, N_STUDENTS)
                self.assertEqual(calibration.n_items, 15)

                items = (await session.execute(
                    select(func.count()).select_from(TriItemParameter)
                    .where(TriItemParameter.calibration_id == calibration_id)
                )).scalar_one()
                scores = (await session.execute(
                    select(func.count()).select_from(TriStudentScore)
                    .where(TriStudentScore.calibration_id == calibration_id)
                )).scalar_one()
                self.assertEqual(items, 15)
                self.assertEqual(scores, N_STUDENTS)
        self._run(scenario())

    def test_the_inverted_answer_key_is_persisted_as_a_negative_discrimination(self):
        async def scenario():
            async with self.session_factory() as session:
                calibration_id = self.mt_calibration_id
                row = (await session.execute(
                    select(TriItemParameter)
                    .where(TriItemParameter.calibration_id == calibration_id)
                    .where(TriItemParameter.item_id == self.context["item_ids"][7])
                )).scalar_one()
                self.assertLess(row.a, 0.0)
                self.assertIn(NEGATIVE_DISCRIMINATION, row.flags)
                self.assertLess(row.point_biserial, 0.0)
                self.assertIsNotNone(row.p_value)
                self.assertEqual(row.n_responses, N_STUDENTS)
        self._run(scenario())

    def test_scores_are_converted_through_the_scale_and_stay_in_the_enem_range(self):
        async def scenario():
            async with self.session_factory() as session:
                calibration_id = self.mt_calibration_id
                rows = (await session.execute(
                    select(TriStudentScore)
                    .where(TriStudentScore.calibration_id == calibration_id)
                )).scalars().all()
                # scaled_score e Numeric(6,2) na Fase 1: a leitura volta Decimal.
                values = [float(row.scaled_score) for row in rows]
                self.assertGreaterEqual(min(values), 312.6)
                self.assertLessEqual(max(values), 980.3)
                self.assertEqual({row.area_code for row in rows}, {"MT"})
                self.assertTrue(all(row.raw_correct >= 0 for row in rows))
                self.assertTrue(all(row.percentile_school is not None for row in rows))
                self.assertTrue(all(row.percentile_class is None for row in rows))
        self._run(scenario())

    def test_the_c_parameter_stays_null_under_2pl(self):
        async def scenario():
            async with self.session_factory() as session:
                calibration_id = self.mt_calibration_id
                rows = (await session.execute(
                    select(TriItemParameter)
                    .where(TriItemParameter.calibration_id == calibration_id)
                )).scalars().all()
                self.assertTrue(all(row.c is None for row in rows))
        self._run(scenario())

    def test_an_area_below_two_hundred_examinees_refuses_2pl(self):
        async def scenario():
            async with self.session_factory() as session:
                with self.assertRaises(InsufficientSampleError):
                    await TriCalibrationService(session).calibrate_and_store(
                        self.context["mock_exam_id"], "CN",
                        self.context["small_area_scale_id"],
                    )
        self._run(scenario())

    def test_the_same_area_below_the_floor_still_calibrates_under_rasch(self):
        async def scenario():
            async with self.session_factory() as session:
                calibration_id = await TriCalibrationService(session).calibrate_and_store(
                    self.context["mock_exam_id"], "CN",
                    self.context["small_area_scale_id"], model="RASCH",
                )
                calibration = await session.get(TriCalibration, calibration_id)
                self.assertEqual(calibration.model, "RASCH")
                self.assertEqual(calibration.n_examinees, N_SMALL_AREA_STUDENTS)
        self._run(scenario())

    def test_a_scale_from_another_area_is_refused(self):
        async def scenario():
            async with self.session_factory() as session:
                with self.assertRaises(CalibrationInputError):
                    await TriCalibrationService(session).calibrate_and_store(
                        self.context["mock_exam_id"], "MT",
                        self.context["small_area_scale_id"],
                    )
        self._run(scenario())

    def test_a_scale_from_another_school_is_refused(self):
        # Spec §3.0: o banco ja recusaria pela chave composta; o servico recusa
        # antes, com uma mensagem que diz QUAIS escolas nao batem.
        async def scenario():
            async with self.session_factory() as session:
                with self.assertRaises(CalibrationInputError):
                    await TriCalibrationService(session).calibrate_and_store(
                        self.context["mock_exam_id"], "MT",
                        self.context["other_school_scale_id"],
                    )
        self._run(scenario())

    def test_an_unknown_scale_is_refused(self):
        async def scenario():
            async with self.session_factory() as session:
                with self.assertRaises(CalibrationInputError):
                    await TriCalibrationService(session).calibrate_and_store(
                        self.context["mock_exam_id"], "MT", uuid.uuid4()
                    )
        self._run(scenario())

    # --- publication_decision ------------------------------------------------

    def test_an_unverified_scale_blocks_publication_and_marks_it_provisional(self):
        async def scenario():
            async with self.session_factory() as session:
                scale = await session.get(TriScale, self.context["scale_id"])
                scale.reference_verified_at = None
                await session.commit()
                decision = await TriCalibrationService(session).publication_decision(
                    self.mt_calibration_id
                )
                self.assertFalse(decision.can_publish)
                self.assertTrue(decision.is_provisional)
                self.assertIn(SCALE_NOT_VERIFIED, decision.blockers)
                self.assertNotIn(NOT_CONVERGED, decision.blockers)
        self._run(scenario())

    def test_stamping_the_scale_unblocks_publication_and_still_warns(self):
        # Spec §6.3.1: a coordenacao confere a regua e carimba
        # reference_verified_at; a MESMA calibragem passa de provisoria a
        # publicavel, sem recalcular nada - o que so funciona porque o estado
        # "provisorio" e derivado da regua, e nao copiado para a calibragem.
        async def scenario():
            async with self.session_factory() as session:
                scale = await session.get(TriScale, self.context["scale_id"])
                scale.reference_verified_at = datetime(2026, 9, 29, tzinfo=timezone.utc)
                await session.commit()
                decision = await TriCalibrationService(session).publication_decision(
                    self.mt_calibration_id
                )
                self.assertTrue(decision.can_publish)
                self.assertFalse(decision.is_provisional)
                self.assertTrue(
                    any(NEGATIVE_DISCRIMINATION in warning for warning in decision.warnings),
                    decision.warnings,
                )
        self._run(scenario())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tri_calibration_service_postgresql.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.services.tri_calibration'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agente_ia_edu/services/tri_calibration.py
"""Camada FINA de persistencia do motor de TRI (spec §2.2, §2.4).

Este e o unico arquivo da Fase 3 que fala com o banco, e ele faz exatamente
tres coisas:

  1. ``load_input``: le ``mock_exam_responses`` de uma area e monta uma
     ``ResponseMatrix``.
  2. chama ``calibrate()``, que nao sabe que um banco existe.
  3. grava o ``CalibrationResult`` em linhas ``tri_*``.

Nenhuma decisao de modelagem estatistica mora aqui. O motor e puro porque e
isso que permite verifica-lo por recuperacao de parametros (spec §8.1); toda
regra que migrar para este arquivo deixa de ser coberta por aquele teste.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    MockExamItem,
    MockExamResponse,
    TriCalibration,
    TriItemParameter,
    TriScale,
    TriStudentScore,
)
from ..tri.calibration import (
    ENGINE_VERSION,
    FIXED_STANDARD_NORMAL,
    CalibrationResult,
    PopulationPrior,
    calibrate,
)
from ..tri.item_analysis import corrected_point_biserial
from ..tri.model import ResponseMatrix
from ..tri.quality import PublicationDecision, publication_gate
from ..tri.scale import ReportingScale

_PERCENTILE_DECIMALS = 2


class CalibrationInputError(RuntimeError):
    """Nao ha dados suficientes, ou coerentes, para montar a matriz de respostas."""


@dataclass(frozen=True, eq=False)
class CalibrationInput:
    """A matriz e os dois vetores de identidade que a traduzem de volta.

    ``matrix.values[i, j]`` e a resposta de ``student_ids[i]`` ao item
    ``item_ids[j]``. Essa correspondencia posicional e todo o contrato entre o
    banco e o motor.
    """

    school_id: uuid.UUID
    mock_exam_id: uuid.UUID
    area_code: str
    student_ids: tuple[uuid.UUID, ...]
    item_ids: tuple[uuid.UUID, ...]
    matrix: ResponseMatrix


def percentiles(scores: np.ndarray) -> np.ndarray:
    """Percentil de cada nota na coorte, com empates compartilhando o valor."""
    scores = np.asarray(scores, dtype=float)
    if scores.size == 0:
        return scores
    below = (scores[:, None] < scores[None, :]).sum(axis=0)
    ties = (scores[:, None] == scores[None, :]).sum(axis=0)
    return np.round(
        100.0 * (below + 0.5 * (ties - 1)) / scores.size, _PERCENTILE_DECIMALS
    )


def reporting_scale_from_row(scale: TriScale) -> ReportingScale:
    """Traduz uma linha de ``tri_scales`` na regua pura do motor."""
    if scale.reference_median_score is None:
        raise CalibrationInputError(
            f"A regua {scale.id} nao tem reference_median_score. Sem a mediana o "
            "mapeamento vira uma reta entre minimo e maximo e infla a nota de todo "
            "aluno mediano em mais de 100 pontos (spec §6.3). Rode "
            "scripts/compute_enem_reference_medians.py e cadastre a mediana."
        )
    # As colunas de nota sao Numeric na Fase 1, entao a leitura devolve Decimal.
    # A conversao para float acontece AQUI e so aqui: o motor e float64 puro, e
    # misturar Decimal com ndarray levanta TypeError no meio de um calculo de nota.
    return ReportingScale(
        area_code=scale.area_code,
        min_score=float(scale.reference_min_score),
        median_score=float(scale.reference_median_score),
        max_score=float(scale.reference_max_score),
        theta_min=float(scale.reference_theta_min),
        theta_max=float(scale.reference_theta_max),
        reference_label=scale.reference_label,
        reference_source=scale.reference_source,
        reference_verified_at=scale.reference_verified_at,
    )


class TriCalibrationService:
    """Orquestra uma calibragem de uma area de um simulado."""

    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def load_input(
        self, mock_exam_id: uuid.UUID, area_code: str
    ) -> CalibrationInput:
        items = (await self._session.execute(
            select(MockExamItem)
            .where(MockExamItem.mock_exam_id == mock_exam_id)
            .where(MockExamItem.area_code == area_code)
            .order_by(MockExamItem.position)
        )).scalars().all()
        if not items:
            raise CalibrationInputError(
                f"O simulado {mock_exam_id} nao tem item nenhum na area {area_code!r}"
            )
        item_ids = [item.id for item in items]
        column_by_item = {item_id: index for index, item_id in enumerate(item_ids)}

        responses = (await self._session.execute(
            select(
                MockExamResponse.student_id,
                MockExamResponse.item_id,
                MockExamResponse.is_correct,
            )
            .where(MockExamResponse.mock_exam_id == mock_exam_id)
            .where(MockExamResponse.item_id.in_(item_ids))
            .order_by(MockExamResponse.student_id)
        )).all()
        if not responses:
            raise CalibrationInputError(
                f"O simulado {mock_exam_id} nao tem resposta nenhuma na area {area_code!r}"
            )

        student_ids: list[uuid.UUID] = []
        row_by_student: dict[uuid.UUID, int] = {}
        for student_id, _, _ in responses:
            if student_id not in row_by_student:
                row_by_student[student_id] = len(student_ids)
                student_ids.append(student_id)

        # nan = item nao apresentado. Branco ja chega como is_correct = False
        # (spec §3: chosen_option nulo para branco), e branco e erro, nao ausencia.
        values = np.full((len(student_ids), len(item_ids)), np.nan)
        for student_id, item_id, is_correct in responses:
            values[row_by_student[student_id], column_by_item[item_id]] = (
                1.0 if is_correct else 0.0
            )

        return CalibrationInput(
            # Os itens ja carregam school_id (spec §3.0); ler dali evita uma
            # consulta extra a mock_exams so para descobrir o tenant.
            school_id=items[0].school_id,
            mock_exam_id=mock_exam_id,
            area_code=area_code,
            student_ids=tuple(student_ids),
            item_ids=tuple(item_ids),
            matrix=ResponseMatrix(values),
        )

    async def calibrate_and_store(
        self,
        mock_exam_id: uuid.UUID,
        area_code: str,
        scale_id: uuid.UUID,
        *,
        model: str = "2PL",
        population: PopulationPrior = FIXED_STANDARD_NORMAL,
        fixed_items: dict[int, tuple[float, float]] | None = None,
    ) -> uuid.UUID:
        """Calibra uma area e grava tudo. Devolve o id da calibragem.

        ``population`` e ``fixed_items`` sao repassados intactos ao motor. A Fase
        3 nunca os usa fora do padrao - ela so calibra livre - mas o caminho de
        escrita ja existe para a Fase 5 nao precisar de um segundo, o que
        significaria duas formas de gravar a mesma nota.
        """
        scale_row = await self._session.get(TriScale, scale_id)
        if scale_row is None:
            raise CalibrationInputError(f"Regua {scale_id} nao existe")
        if scale_row.area_code != area_code:
            raise CalibrationInputError(
                f"A regua {scale_id} e da area {scale_row.area_code!r}, "
                f"nao {area_code!r}"
            )

        loaded = await self.load_input(mock_exam_id, area_code)
        if scale_row.school_id != loaded.school_id:
            raise CalibrationInputError(
                f"A regua {scale_id} e da escola {scale_row.school_id} e o simulado "
                f"{mock_exam_id} e da escola {loaded.school_id}. As chaves compostas "
                "do banco recusariam a gravacao (spec §3.0); recusar aqui da uma "
                "mensagem legivel em vez de um IntegrityError."
            )
        # A excecao de amostra insuficiente sobe INTACTA: quem chamou precisa
        # decidir entre cair para Rasch e ficar so na nota bruta, e essa escolha
        # e da coordenacao, com aviso explicito na tela (spec §6.5).
        result: CalibrationResult = calibrate(
            loaded.matrix, model=model, population=population, fixed_items=fixed_items
        )

        calibration = TriCalibration(
            mock_exam_id=mock_exam_id,
            area_code=area_code,
            scale_id=scale_id,
            model=result.model,
            # Vocabulario da Fase 1 (ck_tri_calibrations_status):
            # PENDING | RUNNING | DONE | FAILED. Nao existe "COMPLETED".
            status="DONE",
            n_examinees=result.n_examinees,
            n_items=result.n_items,
            converged=result.converged,
            iterations=result.iterations,
            log_likelihood=result.log_likelihood,
            engine_version=ENGINE_VERSION,
        )
        self._session.add(calibration)
        await self._session.flush()

        p_values = loaded.matrix.item_p_values()
        biserials = corrected_point_biserial(loaded.matrix)
        response_counts = loaded.matrix.observed.sum(axis=0)
        for column, item_id in enumerate(loaded.item_ids):
            parameters = result.items[column]
            self._session.add(TriItemParameter(
                calibration_id=calibration.id,
                item_id=item_id,
                a=parameters.a,
                b=parameters.b,
                c=None,  # o 2PL nao estima c; a coluna espera a promocao a 3PL (§6.6)
                se_a=parameters.se_a,
                se_b=parameters.se_b,
                n_responses=int(response_counts[column]),
                p_value=None if np.isnan(p_values[column]) else float(p_values[column]),
                point_biserial=(
                    None if np.isnan(biserials[column]) else float(biserials[column])
                ),
                is_fixed=parameters.is_fixed,
                flags=list(parameters.flags),
            ))

        reporting_scale = reporting_scale_from_row(scale_row)
        scores = reporting_scale.to_scores(result.theta)
        raw_correct = loaded.matrix.raw_correct()
        school_percentiles = percentiles(scores)
        for row, student_id in enumerate(loaded.student_ids):
            self._session.add(TriStudentScore(
                calibration_id=calibration.id,
                student_id=student_id,
                area_code=area_code,
                theta=float(result.theta[row]),
                theta_se=float(result.theta_se[row]),
                scaled_score=float(scores[row]),
                raw_correct=int(raw_correct[row]),
                # percentile_class fica nulo na Fase 3: a leitura por turma
                # depende do vinculo aluno->turma e pertence aos relatorios.
                percentile_class=None,
                percentile_school=float(school_percentiles[row]),
            ))

        await self._session.commit()
        return calibration.id

    async def publication_decision(
        self, calibration_id: uuid.UUID
    ) -> PublicationDecision:
        """Aplica os portoes de §6.5 e §6.3.1 a uma calibragem ja gravada."""
        calibration = await self._session.get(TriCalibration, calibration_id)
        if calibration is None:
            raise CalibrationInputError(f"Calibragem {calibration_id} nao existe")
        scale_row = await self._session.get(TriScale, calibration.scale_id)
        if scale_row is None:
            raise CalibrationInputError(
                f"A calibragem {calibration_id} aponta para a regua "
                f"{calibration.scale_id}, que nao existe"
            )

        rows = (await self._session.execute(
            select(TriItemParameter, MockExamItem.position)
            .join(MockExamItem, MockExamItem.id == TriItemParameter.item_id)
            .where(TriItemParameter.calibration_id == calibration_id)
            .order_by(MockExamItem.position)
        )).all()
        flags_by_position = {
            int(position): tuple(parameters.flags or ())
            for parameters, position in rows
            if parameters.flags
        }
        return publication_gate(
            converged=calibration.converged,
            scale_verified=scale_row.is_verified,
            item_flags_by_position=flags_by_position,
        )


__all__ = [
    "CalibrationInput",
    "CalibrationInputError",
    "TriCalibrationService",
    "percentiles",
    "reporting_scale_from_row",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_calibration_service_postgresql.py -v`
Expected: PASS (17 testes). O `setUpClass` insere ~4.650 respostas e roda **uma** calibragem de MT; a classe leva ~1–2 min. Os dois testes de publicação alteram `reference_verified_at` da mesma régua e por isso são escritos para ser independentes da ordem: cada um carimba o valor que precisa antes de decidir.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/tri_calibration.py \
        tests/test_tri_calibration_service_postgresql.py
git commit -m "feat: camada de persistencia do motor de TRI (banco -> matriz -> motor -> linhas)"
```

---

### Task 19: Painel de qualidade da prova

> **Pré-requisito:** Tarefa 18 concluída.

**Files:**
- Create: `src/agente_ia_edu/services/tri_item_panel.py`
- Test: `tests/test_tri_item_panel_postgresql.py`

**Interfaces:**
- Consumes: `analyze_distractors`, `DistractorStat`, `item_characteristic_curve`, `OPTIONS`, `BLANK` (Tarefa 13); `TriCalibration`, `TriItemParameter`, `TriStudentScore` (Tarefa 17); `MockExamItem`, `MockExamResponse` (Fase 1/2).
- Produces:
  - `ItemPanelError(RuntimeError)`
  - `ItemPanelEntry(position: int, item_id: uuid.UUID, area_code: str, correct_option: str, is_anchor: bool, p_value: float | None, point_biserial: float | None, a: float | None, b: float | None, se_a: float | None, se_b: float | None, n_responses: int, flags: tuple[str, ...], icc_theta: tuple[float, ...], icc_probability: tuple[float, ...], distractors: tuple[DistractorStat, ...])`
  - `ExamQualityPanel(calibration_id, mock_exam_id, area_code, model, converged, n_examinees, entries: tuple[ItemPanelEntry, ...])` com a propriedade `flagged_entries -> tuple[ItemPanelEntry, ...]`
  - `TriItemPanelService(session: AsyncSession)` com `async build(calibration_id: uuid.UUID) -> ExamQualityPanel`

**Nota de escopo.** Este serviço devolve dataclasses. Nenhuma rota HTTP é criada aqui: os painéis de professor e coordenação (`api/routes/teacher_portal.py`, `api/routes/coordination_portal.py`) recebem as novas seções na fatia de relatórios, e uma rota escrita antes dos relatórios existirem seria uma rota sem consumidor.

- [ ] **Step 1: Write the failing test**

```python
# tests/test_tri_item_panel_postgresql.py
"""Painel de qualidade da prova (spec §7, bloco "Itens").

"Sai diretamente dos parametros calibrados, sem calculo adicional" - este
servico junta esses parametros com a analise de distratores, que e o unico
calculo que precisa da ALTERNATIVA escolhida e do theta ja estimado.

O caso que importa: o item com gabarito cadastrado errado tem que aparecer
sinalizado, com a alternativa que atraiu os alunos de theta alto visivel ao
lado. E essa leitura que evita prejuizo real.
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid
from datetime import datetime, timezone

import numpy as np
from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    MockExam,
    MockExamItem,
    MockExamResponse,
    Person,
    School,
    Student,
    TriScale,
)
from agente_ia_edu.services.tri_calibration import TriCalibrationService
from agente_ia_edu.services.tri_item_panel import (
    ExamQualityPanel,
    ItemPanelEntry,
    ItemPanelError,
    TriItemPanelService,
)
from agente_ia_edu.tri.item_analysis import BLANK, OPTIONS
from agente_ia_edu.tri.quality import NEGATIVE_DISCRIMINATION
from agente_ia_edu.tri.simulation import simulate_responses
from tests._postgres_test_db import create_database, drop_database

SEED = 8080
N_STUDENTS = 240
N_ITEMS = 12
INVERTED_POSITION = 5          # indice 0-based da coluna com gabarito invertido
ATTRACTIVE_DISTRACTOR = "B"


class TriItemPanelPostgreSQLTests(unittest.TestCase):
    database_name = "agente_ia_edu_tri_panel_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "TRI_PANEL_TEST_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    async_database_url = os.getenv(
        "TRI_PANEL_TEST_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            cls._admin_execute("SELECT 1")
        except Exception as exc:
            raise unittest.SkipTest("PostgreSQL de teste indisponivel") from exc
        drop_database(cls.admin_url, cls.database_name)
        create_database(cls.admin_url, cls.database_name)
        cls.engine = create_async_engine(cls.async_database_url)
        cls.session_factory = async_sessionmaker(
            cls.engine, class_=AsyncSession, expire_on_commit=False
        )
        cls.calibration_id = asyncio.run(cls._seed_and_calibrate())

    @classmethod
    def tearDownClass(cls):
        if hasattr(cls, "engine"):
            asyncio.run(cls.engine.dispose())
        drop_database(cls.admin_url, cls.database_name)

    @classmethod
    def _admin_execute(cls, statement):
        engine = create_engine(
            cls.admin_url,
            connect_args={"autocommit": True},
            execution_options={"isolation_level": "AUTOCOMMIT"},
        )
        try:
            with engine.connect() as connection:
                return connection.execute(text(statement))
        finally:
            engine.dispose()

    @classmethod
    async def _seed_and_calibrate(cls) -> uuid.UUID:
        async with cls.engine.begin() as connection:
            await connection.run_sync(Base.metadata.create_all)

        rng = np.random.default_rng(SEED)
        true_a = rng.uniform(0.9, 1.8, N_ITEMS)
        true_b = rng.uniform(-1.2, 1.2, N_ITEMS)
        thetas = rng.normal(0.0, 1.0, N_STUDENTS)
        correct = simulate_responses(a=true_a, b=true_b, thetas=thetas, seed=SEED)
        correct[:, INVERTED_POSITION] = 1.0 - correct[:, INVERTED_POSITION]

        async with cls.session_factory() as session:
            school = School(code="TRI-PAINEL", name="Escola painel TRI")
            session.add(school)
            await session.flush()

            students = []
            for index in range(N_STUDENTS):
                person = Person(school_id=school.id, full_name=f"Aluno painel {index:04d}")
                session.add(person)
                await session.flush()
                student = Student(person_id=person.id, school_id=school.id)
                session.add(student)
                students.append(student)
            await session.flush()

            exam = MockExam(
                school_id=school.id, name="Simulado painel", exam_day=1, status="APPLIED"
            )
            session.add(exam)
            await session.flush()

            items = []
            for position in range(N_ITEMS):
                item = MockExamItem(
                    school_id=school.id, mock_exam_id=exam.id, position=position + 1,
                    area_code="MT", correct_option="A", is_anchor=(position == 0),
                )
                session.add(item)
                items.append(item)
            await session.flush()

            for row, student in enumerate(students):
                for column, item in enumerate(items):
                    is_correct = bool(correct[row, column])
                    if is_correct:
                        chosen = "A"
                    elif column == INVERTED_POSITION:
                        # No item de gabarito invertido, quem "erra" segundo o
                        # gabarito cadastrado marcou majoritariamente B.
                        chosen = ATTRACTIVE_DISTRACTOR
                    elif row % 3 == 0:
                        # Um terco dos erros vira branco, em todos os itens:
                        # garante linha de branco nao vazia no painel sem
                        # depender do p-valor sorteado de nenhum item.
                        chosen = None  # branco
                    else:
                        chosen = OPTIONS[1 + (row + column) % 4]
                    session.add(MockExamResponse(
                        school_id=school.id, mock_exam_id=exam.id,
                        student_id=student.id, item_id=item.id,
                        chosen_option=chosen, is_correct=is_correct,
                    ))

            scale = TriScale(
                school_id=school.id, area_code="MT", name="ENEM 2025 - MT",
                base_mock_exam_id=exam.id, reference_label="ENEM 2025",
                reference_min_score=312.6, reference_median_score=520.0,
                reference_max_score=980.3,
                reference_source="INEP - microdados ENEM 2025",
                reference_verified_at=datetime(2026, 9, 29, tzinfo=timezone.utc),
            )
            session.add(scale)
            await session.commit()

            return await TriCalibrationService(session).calibrate_and_store(
                exam.id, "MT", scale.id
            )

    def _run(self, coro):
        return asyncio.run(coro)

    def _panel(self) -> ExamQualityPanel:
        async def scenario():
            async with self.session_factory() as session:
                return await TriItemPanelService(session).build(self.calibration_id)
        return self._run(scenario())

    def test_builds_one_entry_per_item_in_position_order(self):
        panel = self._panel()
        self.assertIsInstance(panel, ExamQualityPanel)
        self.assertEqual(len(panel.entries), N_ITEMS)
        self.assertIsInstance(panel.entries[0], ItemPanelEntry)
        self.assertEqual(
            [entry.position for entry in panel.entries], list(range(1, N_ITEMS + 1))
        )

    def test_carries_the_calibration_header(self):
        panel = self._panel()
        self.assertEqual(panel.calibration_id, self.calibration_id)
        self.assertEqual(panel.area_code, "MT")
        self.assertEqual(panel.model, "2PL")
        self.assertTrue(panel.converged)
        self.assertEqual(panel.n_examinees, N_STUDENTS)

    def test_each_entry_carries_the_persisted_parameters(self):
        entry = self._panel().entries[0]
        self.assertIsNotNone(entry.a)
        self.assertIsNotNone(entry.b)
        self.assertIsNotNone(entry.se_b)
        self.assertIsNotNone(entry.p_value)
        self.assertIsNotNone(entry.point_biserial)
        self.assertEqual(entry.n_responses, N_STUDENTS)
        self.assertEqual(entry.correct_option, "A")
        self.assertTrue(entry.is_anchor)

    def test_the_characteristic_curve_is_ready_to_plot(self):
        entry = self._panel().entries[0]
        self.assertEqual(len(entry.icc_theta), 41)
        self.assertEqual(len(entry.icc_probability), 41)
        self.assertTrue(all(0.0 <= value <= 1.0 for value in entry.icc_probability))

    def test_the_item_with_the_wrong_answer_key_is_flagged(self):
        entry = self._panel().entries[INVERTED_POSITION]
        self.assertIn(NEGATIVE_DISCRIMINATION, entry.flags)
        self.assertLess(entry.a, 0.0)

    def test_flagged_entries_are_easy_to_pull_out(self):
        panel = self._panel()
        positions = [entry.position for entry in panel.flagged_entries]
        self.assertIn(INVERTED_POSITION + 1, positions)

    def test_distractor_analysis_covers_every_option_plus_blank(self):
        entry = self._panel().entries[0]
        self.assertEqual(len(entry.distractors), len(OPTIONS) + 1)
        self.assertEqual(entry.distractors[-1].option, BLANK)
        self.assertAlmostEqual(
            sum(stat.share for stat in entry.distractors), 1.0, places=6
        )

    def test_the_answer_key_option_is_marked_in_the_distractor_rows(self):
        entry = self._panel().entries[0]
        by_option = {stat.option: stat for stat in entry.distractors}
        self.assertTrue(by_option["A"].is_correct)
        self.assertFalse(by_option["B"].is_correct)

    def test_the_attractive_distractor_of_the_broken_item_is_visible(self):
        # Spec §7: "qual alternativa errada atraiu os alunos de theta alto".
        entry = self._panel().entries[INVERTED_POSITION]
        by_option = {stat.option: stat for stat in entry.distractors}
        self.assertGreater(by_option[ATTRACTIVE_DISTRACTOR].count, 0)
        self.assertGreater(
            by_option[ATTRACTIVE_DISTRACTOR].mean_theta, by_option["A"].mean_theta
        )

    def test_blanks_are_counted_as_their_own_row(self):
        entry = self._panel().entries[0]
        blank = entry.distractors[-1]
        self.assertGreater(blank.count, 0)
        self.assertFalse(blank.is_correct)

    def test_an_unknown_calibration_is_refused(self):
        async def scenario():
            async with self.session_factory() as session:
                with self.assertRaises(ItemPanelError):
                    await TriItemPanelService(session).build(uuid.uuid4())
        self._run(scenario())


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `.venv/bin/python -m pytest tests/test_tri_item_panel_postgresql.py -v`
Expected: FAIL com `ModuleNotFoundError: No module named 'agente_ia_edu.services.tri_item_panel'`

- [ ] **Step 3: Write minimal implementation**

```python
# src/agente_ia_edu/services/tri_item_panel.py
"""Painel de qualidade da prova (spec §7, bloco "Itens").

Dificuldade, discriminacao e curva caracteristica saem direto dos parametros ja
gravados em ``tri_item_parameters`` - nenhum recalculo. A analise de distratores
e o unico acrescimo, e precisa de duas coisas que o motor puro nao ve: a
ALTERNATIVA que cada aluno marcou (``mock_exam_responses.chosen_option``) e o
theta ja estimado (``tri_student_scores.theta``).

O achado que este painel existe para entregar: o item com discriminacao
negativa - aluno forte errando e fraco acertando - com a alternativa que atraiu
os alunos de theta alto visivel ao lado. Quase sempre e gabarito cadastrado
errado, e e a verificacao que mais evita prejuizo real.

Este servico devolve dataclasses. As rotas dos portais recebem as novas secoes
na fatia de relatorios; uma rota escrita antes dos relatorios existirem seria
uma rota sem consumidor.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

import numpy as np
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    MockExamItem,
    MockExamResponse,
    TriCalibration,
    TriItemParameter,
    TriStudentScore,
)
from ..tri.item_analysis import (
    BLANK,
    DistractorStat,
    analyze_distractors,
    item_characteristic_curve,
)


class ItemPanelError(RuntimeError):
    """A calibragem pedida nao existe, ou nao tem dados para montar o painel."""


@dataclass(frozen=True)
class ItemPanelEntry:
    """Uma linha do painel. ``icc_*`` vazios = item que nao foi estimavel."""

    position: int
    item_id: uuid.UUID
    area_code: str
    correct_option: str
    is_anchor: bool
    p_value: float | None
    point_biserial: float | None
    a: float | None
    b: float | None
    se_a: float | None
    se_b: float | None
    n_responses: int
    flags: tuple[str, ...]
    icc_theta: tuple[float, ...]
    icc_probability: tuple[float, ...]
    distractors: tuple[DistractorStat, ...]


@dataclass(frozen=True)
class ExamQualityPanel:
    calibration_id: uuid.UUID
    mock_exam_id: uuid.UUID
    area_code: str
    model: str
    converged: bool
    n_examinees: int
    entries: tuple[ItemPanelEntry, ...]

    @property
    def flagged_entries(self) -> tuple[ItemPanelEntry, ...]:
        """As linhas que a coordenacao precisa olhar antes de publicar."""
        return tuple(entry for entry in self.entries if entry.flags)


class TriItemPanelService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    async def build(self, calibration_id: uuid.UUID) -> ExamQualityPanel:
        calibration = await self._session.get(TriCalibration, calibration_id)
        if calibration is None:
            raise ItemPanelError(f"Calibragem {calibration_id} nao existe")

        rows = (await self._session.execute(
            select(TriItemParameter, MockExamItem)
            .join(MockExamItem, MockExamItem.id == TriItemParameter.item_id)
            .where(TriItemParameter.calibration_id == calibration_id)
            .order_by(MockExamItem.position)
        )).all()
        if not rows:
            raise ItemPanelError(
                f"A calibragem {calibration_id} nao tem parametro de item gravado"
            )

        theta_by_student = dict((await self._session.execute(
            select(TriStudentScore.student_id, TriStudentScore.theta)
            .where(TriStudentScore.calibration_id == calibration_id)
        )).all())

        item_ids = [item.id for _, item in rows]
        chosen_rows = (await self._session.execute(
            select(
                MockExamResponse.item_id,
                MockExamResponse.student_id,
                MockExamResponse.chosen_option,
            )
            .where(MockExamResponse.mock_exam_id == calibration.mock_exam_id)
            .where(MockExamResponse.item_id.in_(item_ids))
        )).all()

        chosen_by_item: dict[uuid.UUID, list[str]] = {item_id: [] for item_id in item_ids}
        thetas_by_item: dict[uuid.UUID, list[float]] = {item_id: [] for item_id in item_ids}
        for item_id, student_id, chosen_option in chosen_rows:
            theta = theta_by_student.get(student_id)
            if theta is None:
                # Aluno sem theta nesta calibragem (respondeu so outra area):
                # incluir a escolha dele sem o theta distorceria a media por
                # alternativa, que e justamente a leitura do painel.
                continue
            chosen_by_item[item_id].append(chosen_option or BLANK)
            thetas_by_item[item_id].append(float(theta))

        entries: list[ItemPanelEntry] = []
        for parameters, item in rows:
            if parameters.a is None or parameters.b is None:
                icc_theta: tuple[float, ...] = ()
                icc_probability: tuple[float, ...] = ()
            else:
                grid, probability = item_characteristic_curve(parameters.a, parameters.b)
                icc_theta = tuple(float(value) for value in grid)
                icc_probability = tuple(float(value) for value in probability)
            entries.append(ItemPanelEntry(
                position=int(item.position),
                item_id=item.id,
                area_code=item.area_code,
                correct_option=item.correct_option,
                is_anchor=bool(item.is_anchor),
                p_value=parameters.p_value,
                point_biserial=parameters.point_biserial,
                a=parameters.a,
                b=parameters.b,
                se_a=parameters.se_a,
                se_b=parameters.se_b,
                n_responses=int(parameters.n_responses),
                flags=tuple(parameters.flags or ()),
                icc_theta=icc_theta,
                icc_probability=icc_probability,
                distractors=analyze_distractors(
                    np.array(chosen_by_item[item.id], dtype=object),
                    np.array(thetas_by_item[item.id], dtype=float),
                    correct_option=item.correct_option,
                ),
            ))

        return ExamQualityPanel(
            calibration_id=calibration.id,
            mock_exam_id=calibration.mock_exam_id,
            area_code=calibration.area_code,
            model=calibration.model,
            converged=calibration.converged,
            n_examinees=calibration.n_examinees,
            entries=tuple(entries),
        )


__all__ = [
    "ExamQualityPanel",
    "ItemPanelEntry",
    "ItemPanelError",
    "TriItemPanelService",
]
```

- [ ] **Step 4: Run test to verify it passes**

Run: `.venv/bin/python -m pytest tests/test_tri_item_panel_postgresql.py -v`
Expected: PASS (11 testes)

- [ ] **Step 5: Rode a suíte inteira**

Run: `.venv/bin/python -m pytest -q`
Expected: nenhuma regressão. A Fase 3 acrescenta um pacote novo e quatro tabelas novas; o único arquivo existente com mudança de código é `services/proficiency.py` (extração de `mastery_band`, coberta por `tests/test_diagnostic_proficiency.py`).

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/tri_item_panel.py \
        tests/test_tri_item_panel_postgresql.py
git commit -m "feat: painel de qualidade da prova com CCI e analise de distratores"
```

---

## Verificação final da fase

Antes de declarar a Fase 3 pronta, rode e confirme com a saída na tela (não de memória):

```bash
.venv/bin/python -m pytest tests/test_tri_parameter_recovery.py -v       # o teste central
.venv/bin/python -m pytest tests/test_tri_population_recovery.py -v      # a origem da escala
.venv/bin/python -m pytest tests/test_tri_gold_set.py -v                 # PASS ou SKIPPED com motivo
.venv/bin/python -m pytest tests/test_tri_schema_contract_postgresql.py -v
.venv/bin/python -m pytest -q                                            # suite inteira
```

A Fase 3 não acrescenta migration, então não há ida-e-volta de Alembic para verificar aqui — essa verificação é da Fase 1.

A fase **não está pronta** se `test_tri_parameter_recovery.py` não passar com as tolerâncias originais da spec §8.1, ou se alguma delas tiver sido afrouxada sem justificativa escrita dentro do próprio arquivo de teste.

Também não está pronta se `test_tri_population_recovery.py` passar por acidente: os dois testes marcados com ⭐ têm um Step que **injeta um defeito e exige ver a falha** antes de aceitar o verde. Um teste de estimação que nunca se viu falhar não é evidência de nada.

## O que esta fase deliberadamente NÃO entrega

- **Geração de cartão e leitura óptica** (Fases 1 e 4). Nenhum `cv2`, nenhum Pillow, nenhum ReportLab entra aqui.
- **Equalização por âncoras** (Fase 5). Duas metades do mecanismo já vivem no motor e são testadas aqui: os itens travados (`fixed_items=`) e a **estimação da distribuição populacional** (`population=ESTIMATED_POPULATION`, Tarefas 7 e 9). Isso é deliberado — a estimação da população é matéria do motor, e mantê-la aqui é o que a põe sob o teste de recuperação. **A Fase 5 deve remover o `calibrate_equated` que escreveu por fora** e chamar `calibrate(..., population=ESTIMATED_POPULATION, fixed_items=...)` direto. Continuam sendo da Fase 5: a escolha das âncoras, a transformação mean-sigma com purificação iterativa, o limiar de deriva de 0,5 logit, o mínimo de 4 âncoras sobreviventes e a supressão do gráfico de evolução sem âncoras (spec §6.4).
- **Modelo 3PL** (spec §6.6). A coluna `c` existe e fica nula.
- **Rotas HTTP e telas.** Os painéis de professor e coordenação recebem as novas seções na fatia de relatórios.
- **`percentile_class`.** Fica nulo; depende do vínculo aluno→turma, que pertence aos relatórios.
- **O schema `tri_*`.** É da Fase 1. A Fase 3 só verifica o contrato (Tarefa 17).

## Lacuna conhecida, a decidir fora deste plano

A spec §3.0 é normativa e diz que **toda** tabela do subsistema carrega `school_id` e que
**toda** FK entre elas é composta. A migration `059_tri_scales_and_scores` da Fase 1 entrega
isso apenas para `tri_scales`: `tri_calibrations`, `tri_item_parameters` e `tri_student_scores`
saem sem `school_id` e com chaves estrangeiras de coluna única. O próprio plano da Fase 1
declara a regra nas suas Global Constraints, então a divergência é interna àquele plano, não
uma escolha desta fase.

Consequência prática: para essas três tabelas, "não misturar escolas" volta a ser disciplina de
query em vez de invariante do banco. A Fase 3 mitiga no serviço — `calibrate_and_store` recusa
uma régua cuja `school_id` não bate com a do simulado, com mensagem explícita — mas **mitigar
não é o mesmo que impedir**: qualquer outro caminho de escrita que surja depois não passa por
essa checagem.

Isto precisa de uma decisão consciente antes da Fase 1 ser mesclada: ou a `059` ganha
`school_id` e as chaves compostas nas três tabelas, ou a spec §3.0 registra a exceção e o
porquê. Não é uma correção que a Fase 3 deva fazer sozinha, porque mudaria uma tabela de que
ela não é dona.
