# Simulados Fase 5 — Âncoras e equalização entre aplicações Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Fazer com que o theta de dois simulados diferentes caia na **mesma** régua, por
calibragem com parâmetros fixos sobre itens-âncora verificados, de modo que a evolução de
nota ao longo do ano seja uma comparação real — e seja **suprimida**, nunca estimada,
quando as âncoras não existem.

**Architecture:** A estimação é toda do motor da Fase 3 — inclusive a população estimada,
que agora é `calibrate(..., population=ESTIMATED_POPULATION)`. O que a Fase 5 acrescenta ao
módulo puro `src/agente_ia_edu/tri/equating.py` (NumPy, sem banco, sem HTTP — fronteira §2.2)
é só o que é da equalização e de mais ninguém: a **verificação de deriva** por mean-sigma com
purificação iterativa, a conversão do veredito em `fixed_items`, e a fachada
`equate_to_scale`. A escrita passa pelo caminho único da Fase 3
(`TriCalibrationService.calibrate_and_store`); a Fase 5 só monta os parâmetros travados
antes e carimba o veredito de âncora depois. Por fora, três camadas finas
(`services/mock_exam_equating.py`, `services/mock_exam_anchors.py`,
`services/mock_exam_evolution.py`) traduzem linhas do banco em dataclasses puras e de volta.
Nenhuma conta acontece dentro de um `async def`.

**Tech Stack:** Python 3.13, NumPy, SQLAlchemy 2.x async, FastAPI, pytest, JS vanilla
(sem build step), node:test para os testes de frontend.

**Spec:** [docs/superpowers/specs/2026-09-29-correcao-simulados-tri-design.md](../specs/2026-09-29-correcao-simulados-tri-design.md) — §6.3, §6.4, §7 e §8.1 são as seções que este plano implementa.

---

## Global Constraints

- Python `>=3.13,<3.14` (`pyproject.toml:8`). SQLAlchemy `>=2.0,<3.0`, async (`pyproject.toml:11`).
- pytest com `pythonpath = ["src", "."]` (`pyproject.toml:44`) — o editable install aponta
  para o checkout principal, então script Python avulso fora do pytest precisa inserir o
  `src/` do worktree em `sys.path` manualmente.
- NumPy e SciPy entram como dependência **na Fase 3**. Este plano usa **apenas NumPy**;
  nenhuma função nova aqui importa SciPy.
- O core **nunca** importa `cv2`/OpenCV/Pillow. Nada neste plano toca visão computacional.
- Fronteira §2.2, inegociável: `agente_ia_edu.tri.*` não importa SQLAlchemy, ORM,
  FastAPI, `uuid` de modelo, nem qualquer coisa com formato de banco. Recebe `np.ndarray` e
  dataclasses; devolve dataclasses. A persistência é camada fina por fora.
- Frontend é JS vanilla sem build step. Padrões vigentes: `src/agente_ia_edu/web/coordination.js`
  (`coordEsc` em `coordination.js:611`, `fetch` com `Authorization: Bearer ${state.coordinatorId}`)
  e `src/agente_ia_edu/web/app.js` (`studentRequest`, `switchView`, `loadEvolutionData` em
  `app.js:309`).
- Testes de frontend são asserções estáticas sobre o arquivo JS entregue, com `node:test`
  (padrão de `tests/test_material_assignment_student_materials_view_frontend.js`), rodados
  com `node --test <arquivo>`.
- Nunca fabricar nota. Âncora derivada é **descartada e sinalizada**; série sem equalização
  é **suprimida**, não estimada.
- Limiar de deriva: **0,5 na escala logit de `b`** (§6.4), configurável.
- Isolamento multi-tenant (§3.0): as tabelas `mock_exam_*` carregam `school_id` com FKs
  compostas `(school_id, parent_id)`. Entre as `tri_*`, **só `tri_scales` tem a coluna** — nas
  outras três o isolamento é verificado no serviço, e `calibrate_and_store` já recusa uma
  régua de outra escola com mensagem legível. Este plano segue exatamente essa divisão: não
  inventa `school_id` onde a Fase 1/3 não pôs, e não confia só na cadeia de chaves onde ele
  existe.
- Antes de rodar a suíte completa, `ps aux | grep pytest` e esperar execução concorrente
  terminar (Postgres descartável compartilhado na porta 5433).

---

## Premissas herdadas (Fases 1–4) — contratos consumidos, não construídos aqui

Este plano assume que as fases anteriores já entregaram os itens abaixo, e as assinaturas
foram conferidas contra os planos irmãos
[`2026-09-29-simulados-fase1-dados-e-cartao.md`](2026-09-29-simulados-fase1-dados-e-cartao.md)
e [`2026-09-29-simulados-fase3-motor-tri.md`](2026-09-29-simulados-fase3-motor-tri.md). Cada
task que depende de um contrato cita explicitamente. Se algum divergir na hora da execução,
**pare e reporte** — não adapte em silêncio.

**Modelos (Fase 1, §3 do spec):** `MockExam`, `MockExamArea`, `MockExamItem` e
`MockExamResponse` em `src/agente_ia_edu/db/models/mock_exam.py`; `TriScale`,
`TriCalibration`, `TriItemParameter` e `TriStudentScore` em
`src/agente_ia_edu/db/models/tri.py` (Fase 3, migration `059_tri_engine`). Todos
reexportados por `src/agente_ia_edu/db/models/__init__.py`, que é de onde este plano os
importa.

```python
MockExam            # id, school_id, academic_year_id, name, application_date,
                    # exam_day, status, created_at
MockExamArea        # id, mock_exam_id, code, label, display_order
MockExamItem        # id, mock_exam_id, position, area_code, correct_option,
                    # is_anchor (bool, default False), anchor_key (str | None),
                    # source_question_version_id (UUID | None)
MockExamResponse    # id, mock_exam_id, student_id, item_id, chosen_option, is_correct
TriScale            # id, school_id, area_code, name, base_mock_exam_id, reference_label,
                    # reference_min_score, reference_median_score, reference_max_score,
                    # reference_theta_min, reference_theta_max, reference_source,
                    # reference_verified_at, created_at
TriCalibration      # id, mock_exam_id, area_code, scale_id, model, status, n_examinees,
                    # n_items, converged, iterations, log_likelihood, engine_version,
                    # calibrated_at
TriItemParameter    # id, calibration_id, item_id, a, b, c, se_a, se_b, n_responses,
                    # p_value, point_biserial, is_fixed (bool), flags (JSONBCompatible)
TriStudentScore     # id, calibration_id, student_id, area_code, theta, theta_se,
                    # scaled_score, raw_correct, percentile_class, percentile_school
```

- `student_id` é `uuid.UUID` com FK para `students.id` (Fase 1), **não** a identidade
  externa em texto. O SPA do aluno chega com `identity.external_user_id`, então toda rota
  de aluno deste plano resolve primeiro
  `select(Student.id).where(Student.school_id == ..., Student.external_id == ...)` —
  `students` tem `UniqueConstraint("school_id", "external_id")`
  (`db/models/academic.py:356`), então a resolução é exata.
- `TriItemParameter.flags` usa `JSONBCompatible` (`src/agente_ia_edu/db/types.py`), como
  `metadata_` em `db/models/catalog.py:80`.
- **`TriItemParameter.flags` é uma LISTA DE STRINGS**, não um objeto: a Fase 3 grava
  `flags=list(parameters.flags)` com os códigos de qualidade de §6.5
  (`NEGATIVE_DISCRIMINATION`, `LOW_DISCRIMINATION`, `DEGENERATE`…). A Fase 5 **acrescenta**
  o seu código de âncora a essa lista; nunca troca o formato, nunca sobrescreve o que já
  estava lá.
- `MockExam.status` percorre `DRAFT → PRINTED → APPLIED → SCANNED → CALIBRATED → PUBLISHED`.
- `TriCalibration.status` percorre **`PENDING | RUNNING | DONE | FAILED`**
  (`ck_tri_calibrations_status`, Fase 1). **Não existe `COMPLETED`** — as fixtures deste
  plano usam `DONE`.
- `school_id` **não existe** em `tri_calibrations`, `tri_item_parameters` nem
  `tri_student_scores`; existe em `tri_scales` e em todas as `mock_exam_*`.

**Convenção de colunas da matriz de respostas (Fase 3):** a coluna `k` da matriz
corresponde ao `k`-ésimo `MockExamItem` da área, ordenado por `position` crescente. Toda
função deste plano que fala em `column` usa essa ordem, e a recebe explicitamente como
`item_ids: Sequence[UUID]` na ordem das colunas — **nenhuma função aqui reconstrói a
matriz nem adivinha a ordem**.

**Motor puro (Fase 3), pacote `agente_ia_edu.tri`.** A Fase 5 **não reimplementa nada** do
motor — nem passo M, nem erro-padrão, nem quadratura, nem EAP, nem estimação da população,
nem gerador de respostas:

```python
# tri/model.py
@dataclass(frozen=True)
class ItemParameters:            # a/b são None quando o item não é estimável
    a: float | None; b: float | None
    se_a: float | None = None; se_b: float | None = None
    is_fixed: bool = False; flags: tuple[str, ...] = ()

@dataclass(frozen=True, eq=False)
class ResponseMatrix:            # (n_examinees, n_items) de 0.0 / 1.0 / nan
    values: np.ndarray
    # propriedades n_examinees, n_items, observed
    # métodos item_p_values(), zero_variance_items(), raw_correct()

# tri/calibration.py  — reexportado em agente_ia_edu.tri
@dataclass(frozen=True)
class PopulationPrior:
    mean: float = 0.0
    sd: float = 1.0
    estimate: bool = False

FIXED_STANDARD_NORMAL = PopulationPrior()               # calibragem livre (§6.1)
ESTIMATED_POPULATION  = PopulationPrior(estimate=True)  # calibragem equalizada (§6.1, §6.4)

MAX_ITERATIONS = 500
CONVERGENCE_TOLERANCE = 1e-4
ENGINE_VERSION = "tri-1.0.0"
class CalibrationError(RuntimeError): ...
class InsufficientSampleError(CalibrationError): ...

@dataclass(frozen=True, eq=False)
class CalibrationResult:
    model: str; items: tuple[ItemParameters, ...]   # alinhada às colunas da matriz
    theta: np.ndarray; theta_se: np.ndarray
    converged: bool; iterations: int; log_likelihood: float
    n_examinees: int; n_items: int
    population_mean: float; population_sd: float; population_estimated: bool
    engine_version: str

def calibrate(matrix: ResponseMatrix, *, model: str = "2PL",
              population: PopulationPrior = FIXED_STANDARD_NORMAL,
              fixed_items: Mapping[int, tuple[float, float]] | None = None,
              max_iterations=MAX_ITERATIONS, tolerance=CONVERGENCE_TOLERANCE,
              minimum_examinees=MINIMUM_EXAMINEES_FOR_2PL) -> CalibrationResult: ...

# tri/simulation.py
def simulate_responses(*, a: np.ndarray, b: np.ndarray,
                       thetas: np.ndarray, seed: int) -> np.ndarray: ...

# tri/scale.py
class ReportingScale: ...        # theta -> nota, linear por partes (§6.3)
```

**A distinção de §6.1 mora dentro do motor, e é ele quem a protege.** `calibrate` com
`population=FIXED_STANDARD_NORMAL` é a calibragem livre: prior preso em N(0,1), que é a
convenção que dá origem à escala quando não existe âncora. Com
`population=ESTIMATED_POPULATION`, média e desvio da população são estimados junto com o
resto — é isso que posiciona a distribuição de theta do grupo novo sobre a régua existente,
em vez de re-centrá-la em zero.

**Guarda do motor que este plano precisa respeitar:** `population.estimate=True` **sem**
`fixed_items` levanta `CalibrationError`. Não é zelo — sem âncora o modelo não é
identificado: deslocar todos os `b` e a média da população na mesma medida produz
verossimilhança idêntica, e o EM passearia sem convergir para nada interpretável.

Essa guarda e o `InsufficientAnchorsError` deste plano cobrem coisas **diferentes**: a do
motor é "não há âncora nenhuma"; a daqui é "havia âncoras, mas sobraram poucas saudáveis
depois da verificação de deriva". Por isso a recusa por âncoras insuficientes acontece
**antes** de `calibrate` ser chamado (Task 2) — o erro que chega à coordenação precisa ser o
específico e acionável ("estas âncoras derivaram"), não o genérico do motor.

**Serviço de calibragem (Fase 3):** `src/agente_ia_edu/services/tri_calibration.py`, o
**único** caminho de escrita de uma calibragem:

```python
@dataclass(frozen=True)
class CalibrationInput:
    school_id: uuid.UUID; mock_exam_id: uuid.UUID; area_code: str
    student_ids: tuple[uuid.UUID, ...]
    item_ids: tuple[uuid.UUID, ...]       # NA ORDEM DAS COLUNAS da matriz
    matrix: ResponseMatrix

class CalibrationInputError(RuntimeError): ...

class TriCalibrationService:
    def __init__(self, session: AsyncSession) -> None: ...
    async def load_input(self, mock_exam_id, area_code) -> CalibrationInput: ...
    async def calibrate_and_store(self, mock_exam_id, area_code, scale_id, *,
                                  model="2PL", population=FIXED_STANDARD_NORMAL,
                                  fixed_items=None) -> uuid.UUID: ...
    async def publication_decision(self, calibration_id) -> PublicationDecision: ...
```

`calibrate_and_store` grava `tri_calibrations`, `tri_item_parameters` (com
`is_fixed=parameters.is_fixed` e `flags=list(parameters.flags)`) e `tri_student_scores`,
já convertendo theta em nota pela régua. **A Fase 5 não abre um segundo caminho de escrita**:
ela monta os `fixed_items`, chama este, e depois carimba o veredito de âncora nas linhas que
ele acabou de gravar. Dois caminhos gravando a mesma nota é exatamente como duas definições
divergem.


**Rota de resultado do simulado (Fase 2):** existe um endpoint de relatório do aluno que
devolve nota por área. Este plano **não** o altera; a devolutiva de gabarito item a item é
criada aqui (Task 6), porque só aqui existe o conceito de âncora que precisa ser retido.

---

## File Structure

| Arquivo | Responsabilidade | Task |
|---|---|---|
| `src/agente_ia_edu/tri/equating.py` (criar) | O que a equalização acrescenta ao motor da Fase 3: deriva mean-sigma com purificação, `fixed_parameters_from_report`, `equate_to_scale`. Puro, só NumPy. | 1, 2 |
| `tests/_tri_linked_forms.py` (criar) | Duas formas ligadas por âncoras e injeção de vazamento, sobre `tri.simulation.simulate_responses`. Helper, não é arquivo de teste. | 1 |
| `tests/test_tri_anchor_drift.py` (criar) | Deriva: saudável, vazada, não estimável, insuficiente. | 1 |
| `tests/test_tri_equating_invariance.py` (criar) | **Teste central §8.1**: invariância da equalização + prova de que calibragem independente perde o deslocamento. | 2 |
| `src/agente_ia_edu/services/mock_exam_equating.py` (criar) | Camada fina: resolve âncoras contra a régua, chama `calibrate_and_store` da Fase 3, carimba o veredito de âncora. Zero matemática. | 3 |
| `tests/test_mock_exam_equating_service.py` (criar) | Prova da camada fina contra SQLite in-memory. | 3 |
| `src/agente_ia_edu/services/mock_exam_anchors.py` (criar) | Gestão de âncoras (marcar, ligar por `anchor_key`, listar) e saúde das âncoras de uma régua. | 4, 6 |
| `src/agente_ia_edu/services/mock_exam_answer_key_release.py` (criar) | Regra de publicação de gabarito: âncora nunca sai na devolutiva. Parte pura + parte assíncrona. | 5 |
| `src/agente_ia_edu/services/mock_exam_evolution.py` (criar) | Série de evolução no ano, com a regra de supressão. Parte pura + parte assíncrona. | 7, 9 |
| `src/agente_ia_edu/api/schemas/coordination_portal.py` (modificar, 97 linhas) | Schemas das rotas de âncora, de saúde e da evolução da escola. | 4, 6, 9 |
| `src/agente_ia_edu/api/routes/coordination_portal.py` (modificar, 732 linhas) | Rotas de gestão de âncora, saúde da régua e evolução da escola. | 4, 6, 9 |
| `src/agente_ia_edu/api/schemas/student.py` (modificar, 275 linhas) | Schemas da devolutiva de gabarito e da evolução. | 5, 7 |
| `src/agente_ia_edu/api/routes/student.py` (modificar, 965 linhas) | Rotas de gabarito e de evolução do aluno. | 5, 7 |
| `src/agente_ia_edu/web/coordination.html` / `.js` / `.css` (modificar) | Painel "Âncoras e Equalização" e gráfico de evolução da escola. | 6, 9 |
| `src/agente_ia_edu/web/index.html` / `app.js` (modificar) | Evolução em simulados na aba "Minha Evolução", com aviso de supressão. | 8 |

**Decisão de decomposição registrada:** `equating.py` fica com deriva + fachada porque as
duas só se leem juntas e compartilham o mesmo contrato de `ItemParameters`. Já persistência,
gestão de âncora, gabarito e evolução são quatro services distintos porque mudam por razões
distintas e cada um tem uma rota própria.

**Decisão de arquitetura registrada:** a estimação da distribuição populacional era, na
primeira versão deste plano, um laço externo escrito aqui. **Não é mais**: a Fase 3 absorveu
essa mecânica como `population=ESTIMATED_POPULATION` em `calibrate()`, e o laço externo foi
removido. A razão importa e fica registrada — fora do motor, a estimação da população não
ficava coberta pelo teste de recuperação de parâmetros de §8.1, que é justamente a única
verificação capaz de pegar erro de estimação. Um motor de TRI errado não lança exceção: ele
devolve números plausíveis e errados.

O que continua sendo da Fase 5, e agora está registrado no §6.4 do spec: a escolha das
âncoras, a transformação mean-sigma com purificação iterativa, o limiar de 0,5 logit, o
mínimo de âncoras sobreviventes, e a regra de omissão total e sem posição na devolutiva.

---

## Task 1: Verificação de deriva de âncora

**Files:**
- Create: `src/agente_ia_edu/tri/equating.py`
- Create: `tests/_tri_linked_forms.py`
- Create: `tests/test_tri_anchor_drift.py`

**Interfaces:**
- Consumes (Fase 3): `calibrate`, `ResponseMatrix`, `ItemParameters`, `simulate_responses`.
- Produces:
  - `AnchorReference(anchor_key: str, column: int, a: float, b: float)` — o valor **da
    régua** para aquela âncora, e a coluna dela na forma nova.
  - `AnchorVerdict(anchor_key, column, a_reference, b_reference, a_free, b_free, a_linked, b_linked, drift, status)`
  - `AnchorDriftReport(retained, discarded, slope, intercept, threshold, minimum_anchors, sufficient)`
    — `retained`/`discarded` são `tuple[AnchorVerdict, ...]`.
  - `ANCHOR_HEALTHY = "ANCHOR_HEALTHY"`, `ANCHOR_DRIFTED = "ANCHOR_DRIFTED"`,
    `ANCHOR_NOT_ESTIMABLE = "ANCHOR_NOT_ESTIMABLE"`
  - `DEFAULT_DRIFT_THRESHOLD = 0.5`, `DEFAULT_MINIMUM_ANCHORS = 4`
  - `mean_sigma_transform(b_free, b_reference) -> tuple[float, float]`
  - `check_anchor_drift(items: Sequence[ItemParameters], anchors: Sequence[AnchorReference], *, threshold=DEFAULT_DRIFT_THRESHOLD, minimum_anchors=DEFAULT_MINIMUM_ANCHORS) -> AnchorDriftReport`
  - `fixed_parameters_from_report(report) -> dict[int, tuple[float, float]]` — no formato
    exato que `calibrate` aceita em `fixed_items`.
  - Em `tests/_tri_linked_forms.py`: `LinkedForms`, `make_item_bank`, `make_linked_forms`,
    `leak_anchor`.

**Decisão de método registrada (o spec não fecha este ponto — ver relatório):** §6.4 manda
"estimar os âncoras livremente e comparar com a régua", mas estimativa livre sai na escala
do **grupo novo**, não na da régua. Comparar direto marcaria *todas* as âncoras como
derivadas sempre que a turma nova fosse mais forte ou mais fraca — que é justamente a
situação para a qual a equalização existe. Por isso a comparação é feita depois de colocar
as estimativas livres na régua por **mean-sigma** sobre o próprio conjunto de âncoras, com
**purificação iterativa**: calcula a transformação, mede o resíduo, descarta a pior âncora
acima do limiar, recalcula, repete. Isso não contradiz a escolha de §6.4 por calibragem
com parâmetros fixos: mean-sigma aqui é **instrumento de diagnóstico**, descartado depois; a
equalização em si continua sendo feita travando `a` e `b` no EM (Task 2).

**Âncora não estimável:** a Fase 3 devolve `ItemParameters.a is None` para item sem
variância de resposta. Uma âncora nessa situação não pode ser verificada, e o que não pode
ser verificado não entra na régua: ela sai em `discarded` com status `ANCHOR_NOT_ESTIMABLE`.

- [ ] **Step 1: Escrever o helper de formas ligadas**

Criar `tests/_tri_linked_forms.py`:

```python
"""Duas aplicações ligadas por itens-âncora, para os testes de equalização
(spec §8.1, complemento "Invariância da equalização").

O sorteio de respostas em si NÃO é reimplementado aqui: vem de
`agente_ia_edu.tri.simulation.simulate_responses`, que é o gerador
determinístico da Fase 3. Este arquivo só monta a ESTRUTURA que a equalização
precisa — âncoras compartilhadas entre duas formas — e sabe estragar uma
âncora de propósito.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from agente_ia_edu.tri.simulation import simulate_responses


@dataclass(frozen=True, eq=False)
class LinkedForms:
    """Duas formas que compartilham `n_anchor` itens nas colunas 0..n_anchor-1.

    Manter as âncoras nas primeiras colunas é conveniência deste fixture, não
    exigência do motor: `AnchorReference` carrega a coluna explicitamente e as
    âncoras podem estar em qualquer posição.
    """

    anchor_a: np.ndarray
    anchor_b: np.ndarray
    first_values: np.ndarray   # (n_examinees, n_anchor + n_unique)
    second_values: np.ndarray
    first_a: np.ndarray
    first_b: np.ndarray
    second_a: np.ndarray
    second_b: np.ndarray
    first_thetas: np.ndarray
    second_thetas: np.ndarray


def make_item_bank(n_items: int, *, seed: int) -> tuple[np.ndarray, np.ndarray]:
    """Parâmetros verdadeiros: a ~ U(0.7, 1.8), b ~ U(-2.0, 2.0).

    A faixa de `a` evita item que não discrimina ninguém (não identifica nada)
    e item absurdamente discriminante (que tornaria a recuperação trivial). A
    faixa de `b` cobre a escala útil de theta sem gerar item degenerado.
    """
    rng = np.random.default_rng(seed)
    return rng.uniform(0.7, 1.8, size=n_items), rng.uniform(-2.0, 2.0, size=n_items)


def make_linked_forms(
    *,
    n_examinees: int = 1000,
    n_anchor: int = 20,
    n_unique: int = 30,
    second_theta_mean: float = 0.0,
    same_examinees: bool = True,
    seed: int = 20260929,
) -> LinkedForms:
    """Duas formas com as mesmas âncoras e itens próprios diferentes.

    `same_examinees=True` aplica as duas formas às MESMAS pessoas (é o cenário
    do teste de invariância). `same_examinees=False` sorteia um segundo grupo
    com média `second_theta_mean` — é o cenário que prova que uma calibragem
    independente perde uma melhora real da turma.
    """
    anchor_a, anchor_b = make_item_bank(n_anchor, seed=seed + 1)
    first_unique_a, first_unique_b = make_item_bank(n_unique, seed=seed + 2)
    second_unique_a, second_unique_b = make_item_bank(n_unique, seed=seed + 3)

    rng = np.random.default_rng(seed + 4)
    first_thetas = rng.normal(0.0, 1.0, size=n_examinees)
    second_thetas = (
        first_thetas + second_theta_mean
        if same_examinees
        else rng.normal(second_theta_mean, 1.0, size=n_examinees)
    )

    first_a = np.concatenate([anchor_a, first_unique_a])
    first_b = np.concatenate([anchor_b, first_unique_b])
    second_a = np.concatenate([anchor_a, second_unique_a])
    second_b = np.concatenate([anchor_b, second_unique_b])

    return LinkedForms(
        anchor_a=anchor_a,
        anchor_b=anchor_b,
        first_values=simulate_responses(
            a=first_a, b=first_b, thetas=first_thetas, seed=seed + 5
        ),
        second_values=simulate_responses(
            a=second_a, b=second_b, thetas=second_thetas, seed=seed + 6
        ),
        first_a=first_a,
        first_b=first_b,
        second_a=second_a,
        second_b=second_b,
        first_thetas=first_thetas,
        second_thetas=second_thetas,
    )


def leak_anchor(
    values: np.ndarray, column: int, *, fraction: float, seed: int
) -> np.ndarray:
    """Simula vazamento: converte `fraction` dos erros daquela coluna em acertos.

    É a assinatura empírica de item vazado — a proporção de acerto sobe sem que
    a habilidade tenha subido, e a dificuldade estimada livremente despenca.
    """
    rng = np.random.default_rng(seed)
    leaked = np.array(values, dtype=float, copy=True)
    wrong = np.flatnonzero(leaked[:, column] == 0.0)
    n_flip = int(round(len(wrong) * fraction))
    if n_flip:
        leaked[rng.choice(wrong, size=n_flip, replace=False), column] = 1.0
    return leaked
```

- [ ] **Step 2: Escrever o teste que falha**

Criar `tests/test_tri_anchor_drift.py`:

```python
"""Prova da verificação de deriva de âncora (spec §6.4, Fase 5, Task 2).

Deriva é a assinatura de vazamento do item. Um sistema que equaliza sobre uma
âncora vazada produz nota errada SEM erro visível: a régua inteira desliza.
Por isso as duas exigências deste arquivo são simétricas — não pode deixar
passar uma âncora vazada, e não pode descartar âncora saudável só porque o
grupo novo tem média diferente.
"""

from __future__ import annotations

import unittest

import numpy as np

from agente_ia_edu.tri.calibration import calibrate
from agente_ia_edu.tri.equating import (
    ANCHOR_DRIFTED,
    ANCHOR_HEALTHY,
    ANCHOR_NOT_ESTIMABLE,
    AnchorReference,
    check_anchor_drift,
    fixed_parameters_from_report,
    mean_sigma_transform,
)
from agente_ia_edu.tri.model import ItemParameters, ResponseMatrix
from tests._tri_linked_forms import leak_anchor, make_linked_forms

SEED = 20260929
N_ANCHOR = 20


def _references(forms) -> tuple[AnchorReference, ...]:
    """A régua: os valores VERDADEIROS das âncoras, nas colunas 0..19 da forma nova."""
    return tuple(
        AnchorReference(
            anchor_key=f"ANC-{index:02d}",
            column=index,
            a=float(forms.anchor_a[index]),
            b=float(forms.anchor_b[index]),
        )
        for index in range(N_ANCHOR)
    )


class MeanSigmaTransform(unittest.TestCase):
    def test_recovers_a_known_linear_shift(self) -> None:
        b_reference = np.array([-1.0, -0.5, 0.0, 0.5, 1.0, 1.5])
        b_free = (b_reference - 0.4) / 1.25  # régua = 1.25 * livre + 0.4

        slope, intercept = mean_sigma_transform(b_free, b_reference)

        self.assertAlmostEqual(slope, 1.25, places=6)
        self.assertAlmostEqual(intercept, 0.4, places=6)

    def test_zero_spread_falls_back_to_mean_only_linking(self) -> None:
        slope, intercept = mean_sigma_transform(
            np.array([0.3, 0.3, 0.3]), np.array([1.0, 1.0, 1.0])
        )

        self.assertEqual(slope, 1.0)
        self.assertAlmostEqual(intercept, 0.7, places=6)


class AnchorDrift(unittest.TestCase):
    def test_healthy_anchors_survive_a_shifted_group(self) -> None:
        forms = make_linked_forms(
            second_theta_mean=0.7, same_examinees=False, seed=SEED + 1
        )
        free = calibrate(ResponseMatrix(forms.second_values))

        report = check_anchor_drift(free.items, _references(forms))

        self.assertTrue(report.sufficient)
        self.assertEqual(
            len(report.discarded), 0, [v.anchor_key for v in report.discarded]
        )
        self.assertEqual(len(report.retained), N_ANCHOR)
        self.assertTrue(all(v.status == ANCHOR_HEALTHY for v in report.retained))
        # a régua do grupo novo está deslocada: o intercepto captura isso
        self.assertGreater(report.intercept, 0.3)

    def test_a_leaked_anchor_is_detected_and_discarded(self) -> None:
        forms = make_linked_forms(seed=SEED + 2)
        leaked = leak_anchor(forms.second_values, 3, fraction=0.7, seed=SEED + 3)
        free = calibrate(ResponseMatrix(leaked))

        report = check_anchor_drift(free.items, _references(forms))

        self.assertIn("ANC-03", {v.anchor_key for v in report.discarded})
        self.assertNotIn("ANC-03", {v.anchor_key for v in report.retained})
        self.assertTrue(report.sufficient)
        self.assertGreaterEqual(len(report.retained), 15)

        verdict = next(v for v in report.discarded if v.anchor_key == "ANC-03")
        self.assertEqual(verdict.status, ANCHOR_DRIFTED)
        # vazamento infla o acerto => item parece mais FÁCIL => b_linked < b_reference
        self.assertLess(verdict.drift, -0.5)

    def test_a_discarded_anchor_never_reaches_the_fixed_parameters(self) -> None:
        forms = make_linked_forms(seed=SEED + 2)
        leaked = leak_anchor(forms.second_values, 3, fraction=0.7, seed=SEED + 3)
        free = calibrate(ResponseMatrix(leaked))

        report = check_anchor_drift(free.items, _references(forms))
        fixed = fixed_parameters_from_report(report)

        self.assertNotIn(3, fixed)
        self.assertEqual(len(fixed), len(report.retained))
        for column, (a, b) in fixed.items():
            self.assertAlmostEqual(a, float(forms.anchor_a[column]), places=12)
            self.assertAlmostEqual(b, float(forms.anchor_b[column]), places=12)

    def test_an_anchor_the_engine_could_not_estimate_is_discarded(self) -> None:
        forms = make_linked_forms(seed=SEED + 4)
        free = calibrate(ResponseMatrix(forms.second_values))
        items = list(free.items)
        items[5] = ItemParameters(a=None, b=None, flags=("NO_VARIANCE",))

        report = check_anchor_drift(tuple(items), _references(forms))

        verdict = next(v for v in report.discarded if v.anchor_key == "ANC-05")
        self.assertEqual(verdict.status, ANCHOR_NOT_ESTIMABLE)
        self.assertNotIn(5, fixed_parameters_from_report(report))

    def test_too_few_surviving_anchors_reports_insufficient(self) -> None:
        forms = make_linked_forms(seed=SEED + 5)
        free = calibrate(ResponseMatrix(forms.second_values))

        report = check_anchor_drift(
            free.items, _references(forms)[:3], minimum_anchors=4
        )

        self.assertFalse(report.sufficient)

    def test_no_anchors_at_all_reports_insufficient_without_raising(self) -> None:
        report = check_anchor_drift((), ())

        self.assertFalse(report.sufficient)
        self.assertEqual(report.retained, ())
        self.assertEqual(report.discarded, ())

    def test_minimum_anchors_below_two_is_refused(self) -> None:
        forms = make_linked_forms(seed=SEED + 6)
        free = calibrate(ResponseMatrix(forms.second_values))

        with self.assertRaises(ValueError):
            check_anchor_drift(free.items, _references(forms), minimum_anchors=1)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
```

- [ ] **Step 3: Rodar o teste e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_tri_anchor_drift.py -v`
Expected: FAIL — `ImportError: cannot import name 'check_anchor_drift' from 'agente_ia_edu.tri.equating'`

- [ ] **Step 4: Escrever a implementação mínima**

Criar `src/agente_ia_edu/tri/equating.py`:

```python
"""Equalização entre aplicações de simulado por itens-âncora (spec §6.4).

MATEMÁTICA PURA. Como o resto de `agente_ia_edu.tri`, este módulo não importa
SQLAlchemy, ORM, FastAPI nem nada com formato de banco (spec §2.2). Recebe
`ResponseMatrix` e `ItemParameters`; devolve dataclasses.
`services/mock_exam_equating.py` é a camada fina que traduz linha de banco para
cá e o veredito de volta para lá.

ESTE MÓDULO NÃO ESTIMA NADA. A calibragem — inclusive a estimação da população
que a equalização exige (§6.1) — é toda de `tri.calibration.calibrate`, chamada
com `population=ESTIMATED_POPULATION`. O que mora aqui é só o que é da
equalização e de mais ninguém: decidir em QUAIS âncoras dá para confiar, e
converter essa decisão nos `fixed_items` que o motor aceita.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Sequence

import numpy as np

from .model import ItemParameters

DEFAULT_DRIFT_THRESHOLD = 0.5  # §6.4: meio logit em b (meio desvio de theta)
DEFAULT_MINIMUM_ANCHORS = 4

ANCHOR_HEALTHY = "ANCHOR_HEALTHY"
ANCHOR_DRIFTED = "ANCHOR_DRIFTED"
ANCHOR_NOT_ESTIMABLE = "ANCHOR_NOT_ESTIMABLE"

_MIN_SPREAD = 1e-9


@dataclass(frozen=True)
class AnchorReference:
    """O valor DA RÉGUA para uma âncora, e a coluna dela na forma nova."""

    anchor_key: str
    column: int
    a: float
    b: float


@dataclass(frozen=True)
class AnchorVerdict:
    anchor_key: str
    column: int
    a_reference: float
    b_reference: float
    a_free: float | None
    b_free: float | None
    a_linked: float | None
    b_linked: float | None
    drift: float | None
    status: str


@dataclass(frozen=True)
class AnchorDriftReport:
    retained: tuple[AnchorVerdict, ...]
    discarded: tuple[AnchorVerdict, ...]
    slope: float
    intercept: float
    threshold: float
    minimum_anchors: int
    sufficient: bool


def mean_sigma_transform(
    b_free: np.ndarray, b_reference: np.ndarray
) -> tuple[float, float]:
    """Coeficientes (A, B) que levam a escala livre para a da régua:
    `b_regua ~= A * b_livre + B`.

    Quando o conjunto de âncoras não tem dispersão nenhuma (uma âncora só, ou
    todas com a mesma dificuldade), A não é estimável: cai para 1.0 e só o
    deslocamento de média é aplicado. Determinístico, nunca divisão por zero.
    """
    spread_free = float(np.std(b_free))
    spread_reference = float(np.std(b_reference))
    slope = (spread_reference / spread_free) if spread_free > _MIN_SPREAD else 1.0
    intercept = float(np.mean(b_reference)) - slope * float(np.mean(b_free))
    return slope, intercept


def _estimable(items, anchor: AnchorReference) -> bool:
    parameters = items[anchor.column]
    return parameters.a is not None and parameters.b is not None


def _verdict(
    items, anchor: AnchorReference, slope: float, intercept: float, threshold: float
) -> AnchorVerdict:
    if not _estimable(items, anchor):
        return AnchorVerdict(
            anchor_key=anchor.anchor_key, column=anchor.column,
            a_reference=anchor.a, b_reference=anchor.b,
            a_free=None, b_free=None, a_linked=None, b_linked=None, drift=None,
            status=ANCHOR_NOT_ESTIMABLE,
        )
    a_free = float(items[anchor.column].a)
    b_free = float(items[anchor.column].b)
    b_linked = slope * b_free + intercept
    a_linked = a_free / slope if abs(slope) > _MIN_SPREAD else a_free
    drift = b_linked - anchor.b
    return AnchorVerdict(
        anchor_key=anchor.anchor_key, column=anchor.column,
        a_reference=anchor.a, b_reference=anchor.b,
        a_free=a_free, b_free=b_free, a_linked=a_linked, b_linked=b_linked,
        drift=drift,
        status=ANCHOR_HEALTHY if abs(drift) <= threshold else ANCHOR_DRIFTED,
    )


def check_anchor_drift(
    items: Sequence[ItemParameters],
    anchors: Sequence[AnchorReference],
    *,
    threshold: float = DEFAULT_DRIFT_THRESHOLD,
    minimum_anchors: int = DEFAULT_MINIMUM_ANCHORS,
) -> AnchorDriftReport:
    """Compara as âncoras estimadas livremente com a régua e separa saudáveis
    de derivadas (§6.4).

    `items` é a saída de `calibrate(...)` da Fase 3 — calibragem LIVRE, na
    escala do grupo novo. Antes de comparar, as âncoras são levadas para a
    régua por mean-sigma calculado sobre o próprio conjunto de âncoras, com
    purificação iterativa: a cada volta, a âncora de maior resíduo acima do
    limiar é removida do conjunto que define a transformação e a transformação
    é recalculada. Isso impede que uma única âncora vazada puxe a transformação
    e contamine o diagnóstico das outras.

    A purificação para quando o conjunto chega ao piso `minimum_anchors` — daí
    em diante o que sobrar acima do limiar continua marcado como derivado, e o
    relatório sai com `sufficient=False`. Nunca equalizar em silêncio sobre
    âncora deslocada é mais importante do que conseguir equalizar.
    """
    if minimum_anchors < 2:
        raise ValueError("minimum_anchors must be at least 2")

    ordered = tuple(sorted(anchors, key=lambda anchor: anchor.column))
    usable = [anchor for anchor in ordered if _estimable(items, anchor)]
    if not usable:
        return AnchorDriftReport(
            retained=(),
            discarded=tuple(_verdict(items, anchor, 1.0, 0.0, threshold) for anchor in ordered),
            slope=1.0, intercept=0.0, threshold=threshold,
            minimum_anchors=minimum_anchors, sufficient=False,
        )

    kept = list(usable)
    slope, intercept = 1.0, 0.0
    while kept:
        slope, intercept = mean_sigma_transform(
            np.array([float(items[anchor.column].b) for anchor in kept]),
            np.array([anchor.b for anchor in kept]),
        )
        drifts = {
            anchor.anchor_key: slope * float(items[anchor.column].b) + intercept - anchor.b
            for anchor in kept
        }
        worst = max(drifts, key=lambda key: abs(drifts[key]))
        if abs(drifts[worst]) <= threshold or len(kept) <= minimum_anchors:
            break
        kept = [anchor for anchor in kept if anchor.anchor_key != worst]

    # Todos os vereditos são recalculados contra a transformação FINAL (a que
    # saiu do conjunto purificado), para que o número reportado de uma âncora
    # descartada seja o mesmo que o operador vê na tela de saúde.
    retained: list[AnchorVerdict] = []
    discarded: list[AnchorVerdict] = []
    for anchor in ordered:
        verdict = _verdict(items, anchor, slope, intercept, threshold)
        (retained if verdict.status == ANCHOR_HEALTHY else discarded).append(verdict)

    return AnchorDriftReport(
        retained=tuple(retained),
        discarded=tuple(discarded),
        slope=slope,
        intercept=intercept,
        threshold=threshold,
        minimum_anchors=minimum_anchors,
        sufficient=len(retained) >= minimum_anchors,
    )


def fixed_parameters_from_report(
    report: AnchorDriftReport,
) -> dict[int, tuple[float, float]]:
    """Só as âncoras SAUDÁVEIS viram parâmetro travado, e travadas no valor da
    RÉGUA — nunca no valor estimado agora, que é justamente o que a equalização
    está tentando não deixar deslizar.

    O formato é o `fixed_items` que `calibrate` aceita:
    coluna -> (a, b).
    """
    return {
        verdict.column: (verdict.a_reference, verdict.b_reference)
        for verdict in report.retained
    }
```

- [ ] **Step 5: Rodar o teste e ver passar**

Run: `.venv/bin/python -m pytest tests/test_tri_anchor_drift.py -v`
Expected: PASS (9 testes)

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/tri/equating.py tests/_tri_linked_forms.py \
        tests/test_tri_anchor_drift.py
git commit -m "feat(tri): verificacao de deriva de ancora com purificacao iterativa

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 2: `equate_to_scale` e a prova de invariância (§8.1)

**Files:**
- Modify: `src/agente_ia_edu/tri/equating.py` (acrescentar ao final)
- Create: `tests/test_tri_equating_invariance.py`

**Interfaces:**
- Consumes (Fase 3 e Task 1): `calibrate`, `CalibrationResult`, `ResponseMatrix`,
  `ESTIMATED_POPULATION`, `MAX_ITERATIONS`, `CONVERGENCE_TOLERANCE`; `check_anchor_drift`,
  `fixed_parameters_from_report`, `AnchorReference`, `AnchorDriftReport`,
  `DEFAULT_DRIFT_THRESHOLD`, `DEFAULT_MINIMUM_ANCHORS`.
- Produces:
  - `InsufficientAnchorsError(ValueError)` com atributo `.report: AnchorDriftReport`.
  - `EquatingPlan(drift: AnchorDriftReport, fixed_items: dict[int, tuple[float, float]])`
  - `plan_equating(matrix, anchors, *, threshold=DEFAULT_DRIFT_THRESHOLD, minimum_anchors=DEFAULT_MINIMUM_ANCHORS, max_iterations=MAX_ITERATIONS, tolerance=CONVERGENCE_TOLERANCE) -> EquatingPlan`
    — calibragem livre + verificação de deriva + recusa. **Não** calibra a versão final: é o
    que a camada de persistência usa antes de chamar `calibrate_and_store` (Task 3).
  - `EquatingResult(drift: AnchorDriftReport, calibration: CalibrationResult)`, com as
    propriedades de conveniência `theta` e `theta_se` delegando para `calibration`.
  - `equate_to_scale(matrix: ResponseMatrix, anchors: Sequence[AnchorReference], *, threshold=DEFAULT_DRIFT_THRESHOLD, minimum_anchors=DEFAULT_MINIMUM_ANCHORS, max_iterations=MAX_ITERATIONS, tolerance=CONVERGENCE_TOLERANCE) -> EquatingResult`

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/test_tri_equating_invariance.py`:

```python
"""TESTE CENTRAL DA FASE 5 (spec §8.1, complemento "Invariância da equalização").

A TRI produz theta numa escala sem origem própria. Calibrar cada simulado
isoladamente ancora a escala na média daquele grupo, naquele dia — a turma
inteira pode melhorar e a nota não mexer, porque a régua sobe junto.

Este arquivo prova as duas metades disso com semente fixa:

  1. INVARIÂNCIA — os MESMOS respondentes, em dois simulados diferentes
     ligados por 20 âncoras, recebem o mesmo theta dentro da tolerância
     declarada.
  2. CONTROLE — um grupo de média verdadeira +0.6, calibrado
     INDEPENDENTEMENTE, sai com média ~0 (a régua escorregou); equalizado
     pelas âncoras, sai com média ~+0.6 (a régua ficou parada). Sem esta
     metade, a metade 1 passaria mesmo com a equalização desligada.

TOLERÂNCIAS DECLARADAS, e por quê. Com 50 itens 2PL o erro-padrão do EAP fica
em torno de 0.30, então duas medidas independentes da MESMA habilidade diferem
com desvio ~0.42: por isso RMSE < 0.55 e correlação > 0.85 — são limites de
RUÍDO DE MEDIDA, não de equalização. O que a equalização de fato garante é o
alinhamento de origem e de unidade da escala, e é aí que a tolerância é
apertada: deslocamento de média < 0.12 e razão de desvios dentro de 15%.
Afrouxar qualquer um destes números exige justificativa registrada (§8.1).
"""

from __future__ import annotations

import unittest

import numpy as np

from agente_ia_edu.tri.calibration import calibrate
from agente_ia_edu.tri.equating import (
    AnchorReference,
    InsufficientAnchorsError,
    equate_to_scale,
)
from agente_ia_edu.tri.model import ResponseMatrix
from tests._tri_linked_forms import leak_anchor, make_linked_forms

SEED = 20260929
N_ANCHOR = 20

MAX_MEAN_SHIFT = 0.12
MIN_CORRELATION = 0.85
MAX_RMSE = 0.55
MAX_SD_RATIO_DEVIATION = 0.15


class EquatingInvariance(unittest.TestCase):
    @classmethod
    def setUpClass(cls) -> None:
        # Simulado 1 e simulado 2 aplicados às MESMAS pessoas, ligados por 20
        # âncoras nas colunas 0..19.
        cls.forms = make_linked_forms(same_examinees=True, seed=SEED)

        # O simulado 1 ESTABELECE a régua: calibragem livre, prior N(0,1),
        # exatamente como a Fase 3 faz na primeira aplicação do ano.
        cls.ruler = calibrate(ResponseMatrix(cls.forms.first_values))
        cls.theta_one = cls.ruler.theta

        # As âncoras da régua carregam o valor ESTIMADO no simulado 1, não o
        # verdadeiro: em produção ninguém conhece a verdade.
        cls.references = tuple(
            AnchorReference(
                anchor_key=f"ANC-{index:02d}",
                column=index,
                a=float(cls.ruler.items[index].a),
                b=float(cls.ruler.items[index].b),
            )
            for index in range(N_ANCHOR)
        )

    def test_same_examinees_keep_the_same_theta_across_two_mock_exams(self) -> None:
        result = equate_to_scale(
            ResponseMatrix(self.forms.second_values), self.references
        )
        theta_two = result.theta

        mean_shift = abs(float(np.mean(theta_two) - np.mean(self.theta_one)))
        correlation = float(np.corrcoef(self.theta_one, theta_two)[0, 1])
        rmse = float(np.sqrt(np.mean((theta_two - self.theta_one) ** 2)))
        sd_ratio = float(np.std(theta_two) / np.std(self.theta_one))

        self.assertLess(mean_shift, MAX_MEAN_SHIFT, f"deslocamento = {mean_shift:.4f}")
        self.assertGreater(correlation, MIN_CORRELATION, f"corr = {correlation:.4f}")
        self.assertLess(rmse, MAX_RMSE, f"RMSE = {rmse:.4f}")
        self.assertLess(
            abs(sd_ratio - 1.0), MAX_SD_RATIO_DEVIATION, f"razão de desvios = {sd_ratio:.4f}"
        )

    def test_equated_calibration_keeps_the_anchors_at_their_ruler_values(self) -> None:
        result = equate_to_scale(
            ResponseMatrix(self.forms.second_values), self.references
        )

        for verdict in result.drift.retained:
            item = result.calibration.items[verdict.column]
            self.assertTrue(item.is_fixed)
            self.assertAlmostEqual(item.a, verdict.a_reference, places=12)
            self.assertAlmostEqual(item.b, verdict.b_reference, places=12)

    def test_independent_calibration_loses_a_real_group_improvement(self) -> None:
        stronger = make_linked_forms(
            second_theta_mean=0.6, same_examinees=False, seed=SEED + 7
        )
        # as âncoras são as mesmas questões, com os mesmos parâmetros
        # verdadeiros, então a régua do simulado 1 continua valendo
        matrix = ResponseMatrix(stronger.second_values)

        # (a) calibragem independente: a régua sobe junto com a turma
        independent = calibrate(matrix)
        self.assertLess(
            abs(float(np.mean(independent.theta))),
            0.15,
            "calibragem independente deveria re-centrar o grupo em zero",
        )

        # (b) equalizada pelas âncoras: a melhora real aparece
        references = tuple(
            AnchorReference(
                anchor_key=f"ANC-{index:02d}",
                column=index,
                a=float(stronger.anchor_a[index]),
                b=float(stronger.anchor_b[index]),
            )
            for index in range(N_ANCHOR)
        )
        equated = equate_to_scale(matrix, references)
        self.assertAlmostEqual(float(np.mean(equated.theta)), 0.6, delta=0.18)

    def test_a_leaked_anchor_is_discarded_before_the_scale_is_locked(self) -> None:
        leaked = leak_anchor(self.forms.second_values, 3, fraction=0.7, seed=SEED + 8)

        result = equate_to_scale(ResponseMatrix(leaked), self.references)

        self.assertIn("ANC-03", {v.anchor_key for v in result.drift.discarded})
        self.assertNotIn("ANC-03", {v.anchor_key for v in result.drift.retained})
        # e a âncora descartada NÃO foi travada: a coluna 3 foi estimada livre
        self.assertFalse(result.calibration.items[3].is_fixed)

    def test_equating_without_enough_healthy_anchors_raises(self) -> None:
        with self.assertRaises(InsufficientAnchorsError) as caught:
            equate_to_scale(
                ResponseMatrix(self.forms.second_values), self.references[:2]
            )

        self.assertFalse(caught.exception.report.sufficient)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_tri_equating_invariance.py -v`
Expected: FAIL — `ImportError: cannot import name 'InsufficientAnchorsError' from 'agente_ia_edu.tri.equating'`

- [ ] **Step 3: Escrever a implementação mínima**

Acrescentar ao final de `src/agente_ia_edu/tri/equating.py`:

```python
class InsufficientAnchorsError(ValueError):
    """Sobraram âncoras saudáveis de menos para equalizar.

    Carrega o relatório de deriva: quem chama precisa poder dizer ao operador
    QUAIS âncoras derivaram e por quanto, em vez de só "não deu".
    """

    def __init__(self, report: AnchorDriftReport) -> None:
        super().__init__(
            f"only {len(report.retained)} healthy anchors survived drift checking; "
            f"{report.minimum_anchors} are required"
        )
        self.report = report


@dataclass(frozen=True)
class EquatingPlan:
    """O que a verificação de deriva decidiu, antes de qualquer calibragem final.

    Existe separado de `equate_to_scale` porque a camada de persistência precisa
    exatamente disto e mais nada: os `fixed_items` para repassar ao
    `calibrate_and_store` da Fase 3, e o relatório para carimbar depois. Sem
    isso, persistir exigiria calibrar duas vezes ou abrir um segundo caminho de
    escrita.
    """

    drift: AnchorDriftReport
    fixed_items: dict[int, tuple[float, float]]


def plan_equating(
    matrix: ResponseMatrix,
    anchors: Sequence[AnchorReference],
    *,
    threshold: float = DEFAULT_DRIFT_THRESHOLD,
    minimum_anchors: int = DEFAULT_MINIMUM_ANCHORS,
    max_iterations: int = MAX_ITERATIONS,
    tolerance: float = CONVERGENCE_TOLERANCE,
) -> EquatingPlan:
    """Passos 1 e 2 de §6.4: calibragem LIVRE só para enxergar onde as âncoras
    caíram desta vez, e verificação de deriva.

    A recusa por âncoras insuficientes acontece AQUI, antes de `calibrate` ser
    chamado com `ESTIMATED_POPULATION`. O motor tem a sua própria guarda
    (`population.estimate=True` sem `fixed_items` levanta `CalibrationError`),
    mas ela responde a outra pergunta — "não há âncora nenhuma" — e a mensagem
    dela é genérica. Quem está na coordenação precisa ler "estas âncoras
    derivaram", não "o modelo não é identificado".
    """
    free = calibrate(
        matrix,
        population=FIXED_STANDARD_NORMAL,
        max_iterations=max_iterations,
        tolerance=tolerance,
    )
    report = check_anchor_drift(
        free.items, anchors, threshold=threshold, minimum_anchors=minimum_anchors
    )
    if not report.sufficient:
        raise InsufficientAnchorsError(report)
    return EquatingPlan(drift=report, fixed_items=fixed_parameters_from_report(report))


@dataclass(frozen=True, eq=False)
class EquatingResult:
    drift: AnchorDriftReport
    calibration: CalibrationResult

    @property
    def theta(self) -> np.ndarray:
        return self.calibration.theta

    @property
    def theta_se(self) -> np.ndarray:
        return self.calibration.theta_se


def equate_to_scale(
    matrix: ResponseMatrix,
    anchors: Sequence[AnchorReference],
    *,
    threshold: float = DEFAULT_DRIFT_THRESHOLD,
    minimum_anchors: int = DEFAULT_MINIMUM_ANCHORS,
    max_iterations: int = MAX_ITERATIONS,
    tolerance: float = CONVERGENCE_TOLERANCE,
) -> EquatingResult:
    """Coloca esta aplicação sobre a régua existente (§6.4), em três passos.

    1 e 2. `plan_equating`: calibragem livre + verificação de deriva, com recusa
       explícita quando sobram âncoras saudáveis de menos.
    3. Calibragem final pelo motor da Fase 3, com as âncoras sobreviventes
       TRAVADAS nos valores da régua e `population=ESTIMATED_POPULATION` — é a
       combinação das duas coisas que posiciona a distribuição de theta do
       grupo novo sobre a escala existente (§6.1).

    Esta é a fachada PURA, usada pela prova de invariância e por quem não tem
    banco. O caminho com banco é `services/mock_exam_equating.equate_and_store`,
    que usa `plan_equating` e delega a calibragem final e a escrita para
    `TriCalibrationService.calibrate_and_store` — um caminho de escrita só.
    """
    plan = plan_equating(
        matrix,
        anchors,
        threshold=threshold,
        minimum_anchors=minimum_anchors,
        max_iterations=max_iterations,
        tolerance=tolerance,
    )
    calibration = calibrate(
        matrix,
        population=ESTIMATED_POPULATION,
        fixed_items=plan.fixed_items,
        max_iterations=max_iterations,
        tolerance=tolerance,
    )
    return EquatingResult(drift=plan.drift, calibration=calibration)
```

Acrescentar ao topo do arquivo o import do motor — `from .calibration import (
CONVERGENCE_TOLERANCE, ESTIMATED_POPULATION, FIXED_STANDARD_NORMAL, MAX_ITERATIONS,
CalibrationResult, calibrate)` — e reexportar os nomes novos em
`src/agente_ia_edu/tri/__init__.py`:

```python
from .equating import (
    ANCHOR_DRIFTED,
    ANCHOR_HEALTHY,
    ANCHOR_NOT_ESTIMABLE,
    AnchorDriftReport,
    AnchorReference,
    AnchorVerdict,
    EquatingPlan,
    EquatingResult,
    InsufficientAnchorsError,
    check_anchor_drift,
    equate_to_scale,
    fixed_parameters_from_report,
    plan_equating,
)
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `.venv/bin/python -m pytest tests/test_tri_equating_invariance.py -v`
Expected: PASS (5 testes)

- [ ] **Step 5: Rodar os três arquivos puros juntos**

Run: `.venv/bin/python -m pytest tests/test_tri_equating_population.py tests/test_tri_anchor_drift.py tests/test_tri_equating_invariance.py -v`
Expected: PASS (21 testes)

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/tri/equating.py src/agente_ia_edu/tri/__init__.py \
        tests/test_tri_equating_invariance.py
git commit -m "feat(tri): equate_to_scale e prova de invariancia da equalizacao

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 3: Camada fina — resolver a régua, calibrar pelo caminho único, carimbar o veredito

**Files:**
- Create: `src/agente_ia_edu/services/mock_exam_equating.py`
- Create: `tests/test_mock_exam_equating_service.py`

**Interfaces:**
- Consumes (Fase 3, Tasks 1 e 2): `TriCalibrationService`, `CalibrationInput`,
  `CalibrationInputError` (`services/tri_calibration.py`); `ESTIMATED_POPULATION`
  (`tri.calibration`); `AnchorReference`, `AnchorDriftReport`, `AnchorVerdict`,
  `EquatingPlan`, `plan_equating`, `InsufficientAnchorsError`, `ANCHOR_HEALTHY`,
  `ANCHOR_DRIFTED`, `ANCHOR_NOT_ESTIMABLE`, `DEFAULT_DRIFT_THRESHOLD`,
  `DEFAULT_MINIMUM_ANCHORS` (`tri.equating`); modelos `MockExamItem`, `TriCalibration`,
  `TriItemParameter`.
- Produces:
  - `UnreferencedAnchor(item_id: uuid.UUID, column: int, anchor_key: str)`
  - `AnchorResolution(references: tuple[AnchorReference, ...], unreferenced: tuple[UnreferencedAnchor, ...])`
  - `EquatedCalibrationRecord(calibration_id: uuid.UUID, drift: AnchorDriftReport)`
  - `ANCHOR_STATUS_FLAGS = frozenset({ANCHOR_HEALTHY, ANCHOR_DRIFTED, ANCHOR_NOT_ESTIMABLE})`
  - `async load_anchor_references(session, *, item_ids, scale_id) -> AnchorResolution`
  - `async stamp_anchor_flags(session, *, calibration_id, item_ids, report) -> None`
  - `async equate_and_store(session, *, mock_exam_id, area_code, scale_id, threshold=DEFAULT_DRIFT_THRESHOLD, minimum_anchors=DEFAULT_MINIMUM_ANCHORS) -> EquatedCalibrationRecord`
  - `async is_equated_calibration(session, calibration_id) -> bool`

**Um caminho de escrita só, registrado.** A calibragem equalizada **não** grava linhas por
conta própria: ela chama `TriCalibrationService.calibrate_and_store(..., population=ESTIMATED_POPULATION, fixed_items=...)`,
exatamente o mesmo caminho da calibragem livre da Fase 3. Dois caminhos gravando a mesma nota
é como duas definições de nota divergem sem ninguém perceber. O que a Fase 5 faz depois é um
**UPDATE** de carimbo em `tri_item_parameters.flags` — nunca um INSERT paralelo.

**Formato do carimbo, registrado.** `tri_item_parameters.flags` é uma **lista de strings**
(a Fase 3 grava `flags=list(parameters.flags)` com os códigos de §6.5). O carimbo de âncora
**acrescenta** um código a essa lista — `ANCHOR_HEALTHY`, `ANCHOR_DRIFTED` ou
`ANCHOR_NOT_ESTIMABLE` — e preserva tudo o que já estava lá. Nunca troca o formato da coluna.

**Limitação conhecida, e por que não foi contornada:** §3 não tem onde guardar o **valor
numérico** da deriva (o `drift` em logits), a transformação mean-sigma daquela calibragem,
nem o `b` livre diagnosticado. `flags` é lista de strings, `tri_calibrations` não tem coluna
JSON, e este plano **não** acrescenta migration nem inventa tabela. Consequência assumida: o
painel de saúde (Task 6) mostra status, contagens e a última aplicação — tudo respaldado por
linha gravada — e **não** mostra o número da deriva. A recomendação de §3 ganhar um
`tri_calibrations.equating_report` (JSON) está no relatório de fechamento.

**Regra da régua, registrada:** o valor de referência de um `anchor_key` é o parâmetro da
**calibragem mais antiga, convergida, daquela régua, em que aquele item foi estimado
livremente** (`is_fixed = False`). É a aplicação que colocou a âncora na régua. Usar sempre a
mais antiga é o que impede a régua de andar de pouquinho em pouquinho a cada aplicação.
Âncora que aparece pela primeira vez não tem referência: sai em `unreferenced` e é calibrada
livre nesta aplicação, passando a ser referência das próximas.

**Regra de "esta calibragem é equalizada", registrada:** §3 não tem coluna para isso e este
plano não acrescenta migration. A propriedade é derivada: existe pelo menos uma linha de
`tri_item_parameters` com `is_fixed = true`. `is_equated_calibration` é a única implementação
dessa regra, e a Task 7 a consome.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/test_mock_exam_equating_service.py`:

```python
"""Prova da camada fina da equalização (Fase 5, Task 3).

SQLite in-memory com o schema real (`Base.metadata.create_all`), mesmo padrão
de tests/test_coordination_portal_http.py:66-71.

O que este arquivo protege:
  - a referência de uma âncora vem da calibragem MAIS ANTIGA e LIVRE daquela
    régua, nunca de uma em que ela já estava travada (senão a régua anda);
  - âncora de estreia sai em `unreferenced` e não vira `AnchorReference`
    fabricada;
  - o carimbo ACRESCENTA o código de âncora aos flags de qualidade que a Fase 3
    já tinha gravado, em vez de sobrescrevê-los;
  - âncora descartada por deriva é carimbada como tal — descartar em silêncio é
    exatamente o que §6.4 proíbe.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid
from datetime import date, datetime, timedelta, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    MockExam,
    MockExamItem,
    TriCalibration,
    TriItemParameter,
    TriScale,
)
from agente_ia_edu.services.mock_exam_equating import (
    is_equated_calibration,
    load_anchor_references,
    stamp_anchor_flags,
)
from agente_ia_edu.tri.equating import (
    ANCHOR_DRIFTED,
    ANCHOR_HEALTHY,
    AnchorDriftReport,
    AnchorVerdict,
)

AREA = "MT"
SCHOOL_ID = _uuid.uuid4()


def _verdict(anchor_key: str, column: int, drift: float, status: str) -> AnchorVerdict:
    return AnchorVerdict(
        anchor_key=anchor_key, column=column, a_reference=1.1, b_reference=0.2,
        a_free=1.0, b_free=0.1, a_linked=1.05, b_linked=0.2 + drift,
        drift=drift, status=status,
    )


class MockExamEquatingService(unittest.TestCase):
    def setUp(self) -> None:
        async def setup():
            engine = create_async_engine(
                "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
            )
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            now = datetime(2026, 3, 1, tzinfo=timezone.utc)
            async with factory() as session:
                scale = TriScale(
                    id=_uuid.uuid4(), school_id=SCHOOL_ID, area_code=AREA,
                    name="Régua MT 2026", base_mock_exam_id=None,
                    reference_label="ENEM 2025", reference_min_score=312.6,
                    reference_median_score=520.0, reference_max_score=980.3,
                    reference_theta_min=-3.0, reference_theta_max=3.0,
                    reference_source="portal secundário", reference_verified_at=None,
                    created_at=now,
                )
                session.add(scale)

                # Simulado 1 estabelece a régua (âncoras estimadas LIVRES).
                # Simulado 2 já as usou TRAVADAS — não pode virar referência.
                exams = []
                for index, offset in enumerate((0, 60)):
                    exam = MockExam(
                        id=_uuid.uuid4(), school_id=SCHOOL_ID, academic_year_id=None,
                        name=f"Simulado {index + 1}",
                        application_date=(now + timedelta(days=offset)).date(),
                        exam_day=1, status="PUBLISHED", created_at=now,
                    )
                    session.add(exam)
                    exams.append(exam)

                self.item_ids_by_exam = {}
                for exam_index, exam in enumerate(exams):
                    ids = []
                    for position in range(1, 5):
                        item = MockExamItem(
                            id=_uuid.uuid4(), school_id=SCHOOL_ID,
                            mock_exam_id=exam.id, position=position,
                            area_code=AREA, correct_option="A",
                            is_anchor=position <= 2,
                            anchor_key=f"ANC-{position:02d}" if position <= 2 else None,
                            source_question_version_id=None,
                        )
                        session.add(item)
                        ids.append(item.id)
                    self.item_ids_by_exam[exam_index] = ids

                for exam_index, exam in enumerate(exams):
                    calibration = TriCalibration(
                        id=_uuid.uuid4(), mock_exam_id=exam.id, area_code=AREA,
                        scale_id=scale.id, model="2PL", status="DONE",
                        n_examinees=800, n_items=4, converged=True, iterations=42,
                        log_likelihood=-1234.5, engine_version="tri-1.0.0",
                        calibrated_at=now + timedelta(days=60 * exam_index),
                    )
                    session.add(calibration)
                    for column, item_id in enumerate(self.item_ids_by_exam[exam_index]):
                        session.add(
                            TriItemParameter(
                                id=_uuid.uuid4(), calibration_id=calibration.id,
                                item_id=item_id,
                                a=1.1 + 0.1 * exam_index, b=0.2 + 0.1 * exam_index,
                                c=None, se_a=0.05, se_b=0.06, n_responses=800,
                                p_value=0.5, point_biserial=0.3,
                                # no simulado 2 as âncoras JÁ entraram travadas
                                is_fixed=bool(exam_index == 1 and column < 2),
                                flags=["LOW_DISCRIMINATION"] if column == 3 else [],
                            )
                        )
                await session.commit()

            self.engine = engine
            self.factory = factory
            self.scale_id = scale.id
            self.exam_ids = [exam.id for exam in exams]

        asyncio.run(setup())

    def tearDown(self) -> None:
        asyncio.run(self.engine.dispose())

    def test_reference_comes_from_the_oldest_free_calibration_on_the_scale(self) -> None:
        async def run():
            async with self.factory() as session:
                exam = MockExam(
                    id=_uuid.uuid4(), school_id=SCHOOL_ID, academic_year_id=None,
                    name="Simulado 3", application_date=date(2026, 9, 1),
                    exam_day=1, status="SCANNED",
                    created_at=datetime(2026, 9, 1, tzinfo=timezone.utc),
                )
                session.add(exam)
                item_ids = []
                for position in range(1, 5):
                    item = MockExamItem(
                        id=_uuid.uuid4(), school_id=SCHOOL_ID, mock_exam_id=exam.id,
                        position=position, area_code=AREA, correct_option="B",
                        is_anchor=position <= 3,
                        anchor_key=f"ANC-{position:02d}" if position <= 3 else None,
                        source_question_version_id=None,
                    )
                    session.add(item)
                    item_ids.append(item.id)
                await session.commit()
                return await load_anchor_references(
                    session, item_ids=item_ids, scale_id=self.scale_id
                ), item_ids

        resolution, item_ids = asyncio.run(run())

        by_key = {reference.anchor_key: reference for reference in resolution.references}
        self.assertEqual(set(by_key), {"ANC-01", "ANC-02"})
        # valores do simulado 1 (a=1.1, b=0.2), NÃO do simulado 2 (1.2 / 0.3)
        self.assertAlmostEqual(by_key["ANC-01"].a, 1.1, places=9)
        self.assertAlmostEqual(by_key["ANC-01"].b, 0.2, places=9)
        self.assertEqual(by_key["ANC-01"].column, 0)
        self.assertEqual(by_key["ANC-02"].column, 1)
        # ANC-03 estreia nesta régua: nunca vira referência fabricada
        self.assertEqual(
            [item.anchor_key for item in resolution.unreferenced], ["ANC-03"]
        )
        self.assertEqual(resolution.unreferenced[0].column, 2)
        self.assertEqual(resolution.unreferenced[0].item_id, item_ids[2])

    def test_stamping_appends_the_anchor_code_and_keeps_the_quality_flags(self) -> None:
        async def run():
            async with self.factory() as session:
                calibration_id = (
                    await session.execute(
                        select(TriCalibration.id)
                        .order_by(TriCalibration.calibrated_at.asc())
                        .limit(1)
                    )
                ).scalar_one()
                item_ids = self.item_ids_by_exam[0]
                report = AnchorDriftReport(
                    retained=(_verdict("ANC-01", 0, 0.08, ANCHOR_HEALTHY),),
                    discarded=(_verdict("ANC-02", 1, -0.91, ANCHOR_DRIFTED),),
                    slope=1.02, intercept=0.31, threshold=0.5,
                    minimum_anchors=4, sufficient=True,
                )

                await stamp_anchor_flags(
                    session, calibration_id=calibration_id,
                    item_ids=item_ids, report=report,
                )

                rows = (
                    await session.execute(
                        select(TriItemParameter).where(
                            TriItemParameter.calibration_id == calibration_id
                        )
                    )
                ).scalars().all()
                return {str(row.item_id): row for row in rows}, item_ids

        by_item, item_ids = asyncio.run(run())

        self.assertEqual(by_item[str(item_ids[0])].flags, [ANCHOR_HEALTHY])
        self.assertEqual(by_item[str(item_ids[1])].flags, [ANCHOR_DRIFTED])
        # item comum: nada de âncora, e o flag de qualidade da Fase 3 intacto
        self.assertEqual(by_item[str(item_ids[2])].flags, [])
        self.assertEqual(by_item[str(item_ids[3])].flags, ["LOW_DISCRIMINATION"])

    def test_stamping_twice_does_not_duplicate_the_anchor_code(self) -> None:
        async def run():
            async with self.factory() as session:
                calibration_id = (
                    await session.execute(
                        select(TriCalibration.id)
                        .order_by(TriCalibration.calibrated_at.asc())
                        .limit(1)
                    )
                ).scalar_one()
                report = AnchorDriftReport(
                    retained=(_verdict("ANC-01", 0, 0.08, ANCHOR_HEALTHY),),
                    discarded=(), slope=1.0, intercept=0.0, threshold=0.5,
                    minimum_anchors=4, sufficient=True,
                )
                for _ in range(2):
                    await stamp_anchor_flags(
                        session, calibration_id=calibration_id,
                        item_ids=self.item_ids_by_exam[0], report=report,
                    )
                row = (
                    await session.execute(
                        select(TriItemParameter).where(
                            TriItemParameter.calibration_id == calibration_id,
                            TriItemParameter.item_id == self.item_ids_by_exam[0][0],
                        )
                    )
                ).scalar_one()
                return row.flags

        self.assertEqual(asyncio.run(run()), [ANCHOR_HEALTHY])

    def test_a_calibration_with_a_fixed_item_is_equated_and_one_without_is_not(self) -> None:
        async def run():
            async with self.factory() as session:
                rows = (
                    await session.execute(
                        select(TriCalibration).order_by(TriCalibration.calibrated_at.asc())
                    )
                ).scalars().all()
                return (
                    await is_equated_calibration(session, rows[0].id),
                    await is_equated_calibration(session, rows[1].id),
                )

        first, second = asyncio.run(run())
        self.assertFalse(first)   # simulado 1: tudo livre — é ele que cria a régua
        self.assertTrue(second)   # simulado 2: âncoras travadas


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_mock_exam_equating_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.services.mock_exam_equating'`

- [ ] **Step 3: Escrever a implementação mínima**

Criar `src/agente_ia_edu/services/mock_exam_equating.py`:

```python
"""Camada fina de persistência da equalização por âncoras (spec §2.2 e §6.4).

Toda a matemática vive em `agente_ia_edu.tri`. Este arquivo faz três coisas, e
nenhuma delas é uma conta:

  1. IDA — resolve cada item-âncora da forma que está sendo calibrada contra a
     régua (`tri_scales` + os `tri_item_parameters` que colocaram aquele
     `anchor_key` na régua) e devolve `AnchorReference`, que é tipo do módulo
     puro.
  2. CALIBRAGEM — delega para `TriCalibrationService.calibrate_and_store` da
     Fase 3, com `population=ESTIMATED_POPULATION` e os `fixed_items` das
     âncoras saudáveis. NÃO existe um segundo caminho de escrita aqui: dois
     caminhos gravando a mesma nota é como duas definições divergem.
  3. VOLTA — carimba o veredito de âncora em `tri_item_parameters.flags`, que é
     uma LISTA DE STRINGS; o código de âncora é ACRESCENTADO aos códigos de
     qualidade que a Fase 3 já gravou.

Nenhuma linha deste arquivo decide se uma âncora derivou: isso é do motor.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import MockExamItem, TriCalibration, TriItemParameter
from ..tri.calibration import ESTIMATED_POPULATION
from ..tri.equating import (
    ANCHOR_DRIFTED,
    ANCHOR_HEALTHY,
    ANCHOR_NOT_ESTIMABLE,
    DEFAULT_DRIFT_THRESHOLD,
    DEFAULT_MINIMUM_ANCHORS,
    AnchorDriftReport,
    AnchorReference,
    plan_equating,
)
from .tri_calibration import TriCalibrationService

ANCHOR_STATUS_FLAGS = frozenset({ANCHOR_HEALTHY, ANCHOR_DRIFTED, ANCHOR_NOT_ESTIMABLE})


@dataclass(frozen=True)
class UnreferencedAnchor:
    """Âncora marcada nesta prova que ainda não existe na régua: é a estreia
    dela. Calibra livre agora e vira referência das próximas aplicações."""

    item_id: uuid.UUID
    column: int
    anchor_key: str


@dataclass(frozen=True)
class AnchorResolution:
    references: tuple[AnchorReference, ...]
    unreferenced: tuple[UnreferencedAnchor, ...]


@dataclass(frozen=True)
class EquatedCalibrationRecord:
    calibration_id: uuid.UUID
    drift: AnchorDriftReport


async def load_anchor_references(
    session: AsyncSession,
    *,
    item_ids: Sequence[uuid.UUID],
    scale_id: uuid.UUID,
) -> AnchorResolution:
    """Resolve as âncoras desta forma contra a régua.

    `item_ids` vem NA ORDEM DAS COLUNAS da matriz — é o `CalibrationInput.item_ids`
    da Fase 3. Recebê-lo explicitamente evita que qualquer suposição sobre como
    a matriz é montada vaze para dentro deste módulo.

    Referência de um `anchor_key` = parâmetro da calibragem MAIS ANTIGA,
    convergida, desta régua, em que aquele item foi estimado LIVREMENTE
    (`is_fixed = False`). Pegar sempre a mais antiga é o que impede a régua de
    andar um pouquinho a cada aplicação.
    """
    ordered_ids = list(item_ids)
    if not ordered_ids:
        return AnchorResolution(references=(), unreferenced=())

    column_of = {item_id: column for column, item_id in enumerate(ordered_ids)}
    items = (
        await session.execute(
            select(MockExamItem).where(
                MockExamItem.id.in_(ordered_ids),
                MockExamItem.is_anchor.is_(True),
            )
        )
    ).scalars().all()
    anchored = [item for item in items if (item.anchor_key or "").strip()]
    if not anchored:
        return AnchorResolution(references=(), unreferenced=())

    keys = sorted({item.anchor_key for item in anchored})
    rows = (
        await session.execute(
            select(
                MockExamItem.anchor_key,
                TriItemParameter.a,
                TriItemParameter.b,
                TriCalibration.calibrated_at,
            )
            .join(TriItemParameter, TriItemParameter.item_id == MockExamItem.id)
            .join(TriCalibration, TriCalibration.id == TriItemParameter.calibration_id)
            .where(
                MockExamItem.anchor_key.in_(keys),
                MockExamItem.id.not_in(ordered_ids),
                TriCalibration.scale_id == scale_id,
                TriCalibration.converged.is_(True),
                TriItemParameter.is_fixed.is_(False),
                TriItemParameter.a.is_not(None),
                TriItemParameter.b.is_not(None),
            )
            .order_by(MockExamItem.anchor_key, TriCalibration.calibrated_at.asc())
        )
    ).all()

    reference_by_key: dict[str, tuple[float, float]] = {}
    for anchor_key, a, b, _calibrated_at in rows:
        reference_by_key.setdefault(anchor_key, (float(a), float(b)))

    references: list[AnchorReference] = []
    unreferenced: list[UnreferencedAnchor] = []
    for item in sorted(anchored, key=lambda row: column_of[row.id]):
        column = column_of[item.id]
        found = reference_by_key.get(item.anchor_key)
        if found is None:
            unreferenced.append(
                UnreferencedAnchor(
                    item_id=item.id, column=column, anchor_key=item.anchor_key
                )
            )
            continue
        references.append(
            AnchorReference(
                anchor_key=item.anchor_key, column=column, a=found[0], b=found[1]
            )
        )

    return AnchorResolution(
        references=tuple(references), unreferenced=tuple(unreferenced)
    )


async def stamp_anchor_flags(
    session: AsyncSession,
    *,
    calibration_id: uuid.UUID,
    item_ids: Sequence[uuid.UUID],
    report: AnchorDriftReport,
) -> None:
    """Carimba o veredito de âncora nas linhas que `calibrate_and_store` gravou.

    `flags` é lista de strings: o código de âncora é ACRESCENTADO aos códigos de
    qualidade de §6.5 que a Fase 3 pôs ali. Um carimbo anterior é removido antes,
    para que recalibrar não empilhe status contraditório na mesma linha.

    Âncora descartada por deriva é carimbada tanto quanto a retida — descartar em
    silêncio é exatamente o que §6.4 proíbe, e a tela de saúde (Task 6) lê
    justamente estes códigos.
    """
    ordered_ids = list(item_ids)
    status_by_item: dict[uuid.UUID, str] = {}
    for verdict in (*report.retained, *report.discarded):
        if 0 <= verdict.column < len(ordered_ids):
            status_by_item[ordered_ids[verdict.column]] = verdict.status
    if not status_by_item:
        return

    rows = (
        await session.execute(
            select(TriItemParameter).where(
                TriItemParameter.calibration_id == calibration_id,
                TriItemParameter.item_id.in_(list(status_by_item)),
            )
        )
    ).scalars().all()
    for row in rows:
        existing = [flag for flag in (row.flags or []) if flag not in ANCHOR_STATUS_FLAGS]
        row.flags = existing + [status_by_item[row.item_id]]
    await session.commit()


async def equate_and_store(
    session: AsyncSession,
    *,
    mock_exam_id: uuid.UUID,
    area_code: str,
    scale_id: uuid.UUID,
    threshold: float = DEFAULT_DRIFT_THRESHOLD,
    minimum_anchors: int = DEFAULT_MINIMUM_ANCHORS,
) -> EquatedCalibrationRecord:
    """Calibra esta aplicação SOBRE a régua e grava, pelo caminho único da Fase 3.

    A ordem importa: `plan_equating` roda ANTES de qualquer chamada com
    `ESTIMATED_POPULATION`, para que a recusa por âncoras insuficientes
    (`InsufficientAnchorsError`, com o relatório de quais derivaram) chegue à
    coordenação em vez do `CalibrationError` genérico do motor, que responde a
    outra pergunta.
    """
    service = TriCalibrationService(session)
    loaded = await service.load_input(mock_exam_id, area_code)
    resolution = await load_anchor_references(
        session, item_ids=loaded.item_ids, scale_id=scale_id
    )
    plan = plan_equating(
        loaded.matrix,
        resolution.references,
        threshold=threshold,
        minimum_anchors=minimum_anchors,
    )
    calibration_id = await service.calibrate_and_store(
        mock_exam_id,
        area_code,
        scale_id,
        population=ESTIMATED_POPULATION,
        fixed_items=plan.fixed_items,
    )
    await stamp_anchor_flags(
        session,
        calibration_id=calibration_id,
        item_ids=loaded.item_ids,
        report=plan.drift,
    )
    return EquatedCalibrationRecord(calibration_id=calibration_id, drift=plan.drift)


async def is_equated_calibration(
    session: AsyncSession, calibration_id: uuid.UUID
) -> bool:
    """Única implementação da regra "esta calibragem está na régua".

    §3 não tem coluna para isso em `tri_calibrations` e este plano não acrescenta
    migration: a propriedade é derivada de existir ao menos um item travado, que
    é precisamente o que põe a calibragem sobre a escala existente.
    """
    found = (
        await session.execute(
            select(TriItemParameter.id)
            .where(
                TriItemParameter.calibration_id == calibration_id,
                TriItemParameter.is_fixed.is_(True),
            )
            .limit(1)
        )
    ).scalar_one_or_none()
    return found is not None
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `.venv/bin/python -m pytest tests/test_mock_exam_equating_service.py -v`
Expected: PASS (4 testes)

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/mock_exam_equating.py \
        tests/test_mock_exam_equating_service.py
git commit -m "feat(simulados): camada fina da equalizacao sobre o caminho unico da Fase 3

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 4: Gestão de âncoras — marcar item, ligar por `anchor_key`, listar

**Files:**
- Create: `src/agente_ia_edu/services/mock_exam_anchors.py`
- Modify: `src/agente_ia_edu/api/schemas/coordination_portal.py` (97 linhas; acrescentar ao final)
- Modify: `src/agente_ia_edu/api/routes/coordination_portal.py` (732 linhas; imports em
  `:10-58`, `_requester` em `:74-83`, `_authorize_teacher_or_coordinator` em `:85-94`;
  acrescentar as rotas ao final do arquivo)
- Create: `tests/test_mock_exam_anchors_http.py`

**Interfaces:**
- Consumes: `MockExam`, `MockExamItem` (Fase 1); `Requester` de
  `services/question_list_store.py` (campos `external_user_id`, `school_id`, `role`,
  `is_platform_admin`, propriedade `is_privileged`); `_requester(ctx)` de
  `api/routes/coordination_portal.py:74`.
- Produces:
  - `AnchorError(ValueError)`, `AnchorNotFound(AnchorError)`, `AnchorStateError(AnchorError)`,
    `AnchorAuthError(AnchorError)`
  - `ANCHOR_MUTABLE_STATUSES = frozenset({"DRAFT", "PRINTED", "APPLIED", "SCANNED"})`
  - `AnchorItemView(item_id: str, position: int, area_code: str, anchor_key: str, linked_mock_exams: tuple[LinkedMockExam, ...])`
  - `LinkedMockExam(mock_exam_id: str, name: str, application_date: str, position: int)`
  - `async set_item_anchor(session, *, requester, mock_exam_id, position, is_anchor, anchor_key) -> AnchorItemView | None`
  - `async list_anchors(session, *, requester, mock_exam_id) -> tuple[str, tuple[AnchorItemView, ...]]`
    (devolve `(mock_exam_status, âncoras)`)
  - Schemas `MockExamAnchorUpdateRequest`, `LinkedMockExamItem`, `MockExamAnchorItem`,
    `MockExamAnchorListResponse`
  - Rotas `PUT /api/v1/coordination/mock-exams/{mock_exam_id}/items/{position}/anchor` e
    `GET /api/v1/coordination/mock-exams/{mock_exam_id}/anchors`

**Regra de estado, registrada:** marcar/desmarcar âncora é permitido enquanto o simulado
estiver em `DRAFT`, `PRINTED`, `APPLIED` ou `SCANNED`. Depois de `CALIBRATED` não é: mudar
o conjunto de âncoras depois da calibragem invalidaria parâmetros já gravados e,
potencialmente, nota já publicada — e ainda liberaria no gabarito um item que a régua usa.
Tentativa fora dessa janela devolve 409.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/test_mock_exam_anchors_http.py`:

```python
"""Rotas de gestão de âncoras do portal de coordenação (Fase 5, Task 5).

Caminho HTTP real (TestClient), mesmo padrão e mesmo bootstrap de
tests/test_coordination_portal_http.py:66-71.

O que protege:
  - `is_anchor=True` sem `anchor_key` é 422: âncora sem identidade estável
    não liga nada a coisa nenhuma e quebraria a equalização silenciosamente;
  - `anchor_key` repetido dentro do MESMO simulado é 422 (seriam duas colunas
    disputando a mesma referência);
  - simulado já CALIBRATED é 409;
  - coordenador de outra escola é 403;
  - a listagem mostra em quais outros simulados aquela `anchor_key` aparece —
    é essa ligação que torna a equalização possível.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid
from datetime import date, datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.api.routes.coordination_portal import coordination_portal_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import MockExam, MockExamItem
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext

SCHOOL_ID = _uuid.uuid4()
OTHER_SCHOOL_ID = _uuid.uuid4()
AREA = "MT"


class MockExamAnchorsHTTP(unittest.TestCase):
    def setUp(self) -> None:
        async def setup():
            engine = create_async_engine(
                "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
            )
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            now = datetime(2026, 3, 1, tzinfo=timezone.utc)
            async with factory() as session:
                self.exam_id = _uuid.uuid4()
                session.add(
                    MockExam(
                        id=self.exam_id, school_id=SCHOOL_ID, academic_year_id=None,
                        name="Simulado 1", application_date=date(2026, 3, 10),
                        exam_day=1, status="PRINTED", created_at=now,
                    )
                )
                for position in range(1, 5):
                    session.add(
                        MockExamItem(
                            id=_uuid.uuid4(), school_id=SCHOOL_ID, mock_exam_id=self.exam_id,
                            position=position, area_code=AREA, correct_option="A",
                            is_anchor=False, anchor_key=None,
                            source_question_version_id=None,
                        )
                    )

                # Um simulado ANTERIOR que já usa ANC-01 — é o que a listagem
                # precisa mostrar como ligação.
                self.previous_exam_id = _uuid.uuid4()
                session.add(
                    MockExam(
                        id=self.previous_exam_id, school_id=SCHOOL_ID,
                        academic_year_id=None, name="Simulado 0",
                        application_date=date(2025, 9, 10), exam_day=1,
                        status="PUBLISHED", created_at=now,
                    )
                )
                session.add(
                    MockExamItem(
                        id=_uuid.uuid4(), school_id=SCHOOL_ID, mock_exam_id=self.previous_exam_id,
                        position=17, area_code=AREA, correct_option="C",
                        is_anchor=True, anchor_key="ANC-01",
                        source_question_version_id=None,
                    )
                )

                self.calibrated_exam_id = _uuid.uuid4()
                session.add(
                    MockExam(
                        id=self.calibrated_exam_id, school_id=SCHOOL_ID,
                        academic_year_id=None, name="Simulado travado",
                        application_date=date(2026, 5, 10), exam_day=1,
                        status="CALIBRATED", created_at=now,
                    )
                )
                session.add(
                    MockExamItem(
                        id=_uuid.uuid4(), school_id=SCHOOL_ID, mock_exam_id=self.calibrated_exam_id,
                        position=1, area_code=AREA, correct_option="A",
                        is_anchor=False, anchor_key=None,
                        source_question_version_id=None,
                    )
                )
                await session.commit()

            self.engine = engine
            self.factory = factory

        asyncio.run(setup())

        app = FastAPI()
        app.include_router(coordination_portal_router)
        app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.app = app
        self.client = TestClient(app)
        self._as_coordinator(SCHOOL_ID)

    def _as_coordinator(self, school_id) -> None:
        context = AuthenticatedUserContext(
            user_id="user:coord_a", external_identity_id="user:coord_a",
            school_id=str(school_id), role="COORDINATOR",
        )
        identity = ExternalIdentityContext(
            external_user_id="user:coord_a", institution_id=str(school_id),
            classroom_id=None,
        )
        self.app.dependency_overrides[get_current_authenticated_context] = lambda: context
        self.app.dependency_overrides[get_current_identity] = lambda: identity

    def tearDown(self) -> None:
        self.client.close()
        asyncio.run(self.engine.dispose())

    def _url(self, exam_id, position: int) -> str:
        return f"/api/v1/coordination/mock-exams/{exam_id}/items/{position}/anchor"

    def test_marking_an_item_as_anchor_persists_key_and_lists_the_link(self) -> None:
        response = self.client.put(
            self._url(self.exam_id, 2),
            json={"is_anchor": True, "anchor_key": "ANC-01"},
        )
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json()["anchor_key"], "ANC-01")

        listing = self.client.get(
            f"/api/v1/coordination/mock-exams/{self.exam_id}/anchors"
        )
        self.assertEqual(listing.status_code, 200, listing.text)
        body = listing.json()
        self.assertEqual(body["status"], "PRINTED")
        self.assertEqual(len(body["anchors"]), 1)
        anchor = body["anchors"][0]
        self.assertEqual(anchor["position"], 2)
        self.assertEqual(
            [link["mock_exam_id"] for link in anchor["linked_mock_exams"]],
            [str(self.previous_exam_id)],
        )
        self.assertEqual(anchor["linked_mock_exams"][0]["position"], 17)

    def test_anchor_without_key_is_rejected(self) -> None:
        response = self.client.put(
            self._url(self.exam_id, 2), json={"is_anchor": True, "anchor_key": "  "}
        )
        self.assertEqual(response.status_code, 422, response.text)

    def test_duplicate_anchor_key_inside_the_same_exam_is_rejected(self) -> None:
        first = self.client.put(
            self._url(self.exam_id, 2), json={"is_anchor": True, "anchor_key": "ANC-01"}
        )
        self.assertEqual(first.status_code, 200, first.text)

        second = self.client.put(
            self._url(self.exam_id, 3), json={"is_anchor": True, "anchor_key": "ANC-01"}
        )
        self.assertEqual(second.status_code, 422, second.text)

    def test_unmarking_clears_the_key(self) -> None:
        self.client.put(
            self._url(self.exam_id, 2), json={"is_anchor": True, "anchor_key": "ANC-01"}
        )
        response = self.client.put(
            self._url(self.exam_id, 2), json={"is_anchor": False, "anchor_key": None}
        )
        self.assertEqual(response.status_code, 204, response.text)

        listing = self.client.get(
            f"/api/v1/coordination/mock-exams/{self.exam_id}/anchors"
        )
        self.assertEqual(listing.json()["anchors"], [])

    def test_changing_anchors_after_calibration_is_refused(self) -> None:
        response = self.client.put(
            self._url(self.calibrated_exam_id, 1),
            json={"is_anchor": True, "anchor_key": "ANC-09"},
        )
        self.assertEqual(response.status_code, 409, response.text)

    def test_unknown_position_is_404(self) -> None:
        response = self.client.put(
            self._url(self.exam_id, 99), json={"is_anchor": True, "anchor_key": "ANC-09"}
        )
        self.assertEqual(response.status_code, 404, response.text)

    def test_coordinator_from_another_school_is_refused(self) -> None:
        self._as_coordinator(OTHER_SCHOOL_ID)
        response = self.client.put(
            self._url(self.exam_id, 2), json={"is_anchor": True, "anchor_key": "ANC-01"}
        )
        self.assertEqual(response.status_code, 403, response.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_mock_exam_anchors_http.py -v`
Expected: FAIL — `404 Not Found` em todas as chamadas (rotas não existem)

- [ ] **Step 3: Escrever o serviço**

Criar `src/agente_ia_edu/services/mock_exam_anchors.py`:

```python
"""Gestão de itens-âncora de simulado (spec §6.4).

Âncora é o ponto fixo que impede a régua de escorregar entre aplicações. Duas
coisas precisam ser verdade para isso funcionar, e as duas são garantidas
aqui:

  1. `anchor_key` é a identidade estável do item ATRAVÉS de simulados: dois
     itens com a mesma chave são a mesma questão. Chave vazia ou repetida
     dentro do mesmo simulado é recusada — sem identidade não há equalização,
     e com identidade ambígua há equalização ERRADA, que é pior.
  2. O conjunto de âncoras é imutável depois de `CALIBRATED`: mexer nele
     invalidaria parâmetros gravados e liberaria no gabarito um item que a
     régua usa.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import MockExam, MockExamItem
from .question_list_store import Requester

ANCHOR_MUTABLE_STATUSES = frozenset({"DRAFT", "PRINTED", "APPLIED", "SCANNED"})


class AnchorError(ValueError):
    """Base de todos os erros de gestão de âncora."""


class AnchorNotFound(AnchorError):
    pass


class AnchorStateError(AnchorError):
    pass


class AnchorAuthError(AnchorError):
    pass


@dataclass(frozen=True)
class LinkedMockExam:
    mock_exam_id: str
    name: str
    application_date: str
    position: int


@dataclass(frozen=True)
class AnchorItemView:
    item_id: str
    position: int
    area_code: str
    anchor_key: str
    linked_mock_exams: tuple[LinkedMockExam, ...]


async def _load_exam(
    session: AsyncSession, *, requester: Requester, mock_exam_id: uuid.UUID
) -> MockExam:
    exam = await session.get(MockExam, mock_exam_id)
    if exam is None:
        raise AnchorNotFound(str(mock_exam_id))
    if requester.is_platform_admin:
        return exam
    if not requester.is_privileged:
        raise AnchorAuthError("only a coordinator may manage mock-exam anchors")
    if not requester.school_id or str(exam.school_id) != str(requester.school_id):
        raise AnchorAuthError("mock exam is outside this coordinator's school")
    return exam


async def _links_for_keys(
    session: AsyncSession,
    *,
    school_id,
    keys: Sequence[str],
    exclude_mock_exam_id: uuid.UUID,
) -> dict[str, tuple[LinkedMockExam, ...]]:
    """Onde mais, na mesma escola, cada `anchor_key` aparece."""
    if not keys:
        return {}
    rows = (
        await session.execute(
            select(MockExamItem.anchor_key, MockExam, MockExamItem.position)
            .join(MockExam, MockExam.id == MockExamItem.mock_exam_id)
            .where(
                MockExamItem.anchor_key.in_(list(keys)),
                MockExamItem.is_anchor.is_(True),
                MockExam.school_id == school_id,
                MockExam.id != exclude_mock_exam_id,
            )
            .order_by(MockExam.application_date.asc())
        )
    ).all()
    linked: dict[str, list[LinkedMockExam]] = {}
    for anchor_key, exam, position in rows:
        linked.setdefault(anchor_key, []).append(
            LinkedMockExam(
                mock_exam_id=str(exam.id),
                name=exam.name,
                application_date=exam.application_date.isoformat(),
                position=int(position),
            )
        )
    return {key: tuple(values) for key, values in linked.items()}


async def set_item_anchor(
    session: AsyncSession,
    *,
    requester: Requester,
    mock_exam_id: uuid.UUID,
    position: int,
    is_anchor: bool,
    anchor_key: str | None,
) -> AnchorItemView | None:
    """Marca ou desmarca um item como âncora. Devolve a visão do item quando
    marcou, e `None` quando desmarcou."""
    exam = await _load_exam(session, requester=requester, mock_exam_id=mock_exam_id)
    if (exam.status or "").upper() not in ANCHOR_MUTABLE_STATUSES:
        raise AnchorStateError(
            f"anchors cannot be changed while the mock exam is {exam.status}"
        )

    item = (
        await session.execute(
            select(MockExamItem).where(
                MockExamItem.mock_exam_id == mock_exam_id,
                MockExamItem.position == position,
            )
        )
    ).scalar_one_or_none()
    if item is None:
        raise AnchorNotFound(f"position {position}")

    if not is_anchor:
        item.is_anchor = False
        item.anchor_key = None
        await session.commit()
        return None

    key = (anchor_key or "").strip()
    if not key:
        raise AnchorError("anchor_key is required when is_anchor is true")

    clash = (
        await session.execute(
            select(MockExamItem.id).where(
                MockExamItem.mock_exam_id == mock_exam_id,
                MockExamItem.anchor_key == key,
                MockExamItem.id != item.id,
            )
        )
    ).scalar_one_or_none()
    if clash is not None:
        raise AnchorError(
            f"anchor_key {key!r} is already used by another item of this mock exam"
        )

    item.is_anchor = True
    item.anchor_key = key
    await session.commit()

    linked = await _links_for_keys(
        session, school_id=exam.school_id, keys=[key], exclude_mock_exam_id=mock_exam_id
    )
    return AnchorItemView(
        item_id=str(item.id),
        position=int(item.position),
        area_code=item.area_code,
        anchor_key=key,
        linked_mock_exams=linked.get(key, ()),
    )


async def list_anchors(
    session: AsyncSession, *, requester: Requester, mock_exam_id: uuid.UUID
) -> tuple[str, tuple[AnchorItemView, ...]]:
    exam = await _load_exam(session, requester=requester, mock_exam_id=mock_exam_id)
    items = (
        await session.execute(
            select(MockExamItem)
            .where(
                MockExamItem.mock_exam_id == mock_exam_id,
                MockExamItem.is_anchor.is_(True),
            )
            .order_by(MockExamItem.position.asc())
        )
    ).scalars().all()
    anchored = [item for item in items if (item.anchor_key or "").strip()]
    linked = await _links_for_keys(
        session,
        school_id=exam.school_id,
        keys=[item.anchor_key for item in anchored],
        exclude_mock_exam_id=mock_exam_id,
    )
    views = tuple(
        AnchorItemView(
            item_id=str(item.id),
            position=int(item.position),
            area_code=item.area_code,
            anchor_key=item.anchor_key,
            linked_mock_exams=linked.get(item.anchor_key, ()),
        )
        for item in anchored
    )
    return (exam.status or ""), views
```

- [ ] **Step 4: Escrever os schemas**

Acrescentar ao final de `src/agente_ia_edu/api/schemas/coordination_portal.py`:

```python
# ============================================================================
# Simulados Fase 5 — âncoras e equalização (spec §6.4).
# ============================================================================


class MockExamAnchorUpdateRequest(BaseModel):
    is_anchor: bool
    anchor_key: Optional[str] = None


class LinkedMockExamItem(BaseModel):
    mock_exam_id: str
    name: str
    application_date: str
    position: int


class MockExamAnchorItem(BaseModel):
    item_id: str
    position: int
    area_code: str
    anchor_key: str
    linked_mock_exams: list[LinkedMockExamItem] = Field(default_factory=list)


class MockExamAnchorListResponse(BaseModel):
    mock_exam_id: str
    status: str
    anchors: list[MockExamAnchorItem] = Field(default_factory=list)
```

`Optional` e `Field` já estão importados no topo do arquivo
(`api/schemas/coordination_portal.py:6` e `:9`) — nenhum import novo é necessário.

- [ ] **Step 5: Escrever as rotas**

Em `src/agente_ia_edu/api/routes/coordination_portal.py`, acrescentar aos imports de
`..schemas.coordination_portal` (bloco de `:15-20`) os nomes `MockExamAnchorItem`,
`MockExamAnchorListResponse`, `MockExamAnchorUpdateRequest`, `LinkedMockExamItem`, e
acrescentar ao bloco de imports de services:

```python
from ...services.mock_exam_anchors import (
    AnchorAuthError,
    AnchorError,
    AnchorNotFound,
    AnchorStateError,
    list_anchors,
    set_item_anchor,
)
```

Acrescentar ao final do arquivo:

```python
# ============================================================================
# Simulados Fase 5 — gestão de itens-âncora (spec §6.4).
# Autorização pelo MESMO `Requester`/`is_privileged` já usado no bloco de
# segmentação acima (:74-94), e não pelo TeachingContextService, porque o
# escopo aqui é a ESCOLA do simulado, não uma turma.
# ============================================================================


def _map_anchor_error(exc: Exception) -> HTTPException:
    if isinstance(exc, AnchorNotFound):
        return HTTPException(status_code=404, detail="Not found")
    if isinstance(exc, AnchorAuthError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, AnchorStateError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, AnchorError):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc  # pragma: no cover


def _anchor_item_schema(view) -> MockExamAnchorItem:
    return MockExamAnchorItem(
        item_id=view.item_id,
        position=view.position,
        area_code=view.area_code,
        anchor_key=view.anchor_key,
        linked_mock_exams=[
            LinkedMockExamItem(
                mock_exam_id=link.mock_exam_id,
                name=link.name,
                application_date=link.application_date,
                position=link.position,
            )
            for link in view.linked_mock_exams
        ],
    )


@coordination_portal_router.put(
    "/mock-exams/{mock_exam_id}/items/{position}/anchor",
    summary="Mark or unmark a mock-exam item as an equating anchor",
)
async def set_mock_exam_item_anchor(
    mock_exam_id: UUID,
    position: int,
    payload: MockExamAnchorUpdateRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
):
    async with session_factory() as session:
        try:
            view = await set_item_anchor(
                session,
                requester=_requester(ctx),
                mock_exam_id=mock_exam_id,
                position=position,
                is_anchor=payload.is_anchor,
                anchor_key=payload.anchor_key,
            )
        except Exception as exc:  # noqa: BLE001 - remapped below
            raise _map_anchor_error(exc) from exc
    if view is None:
        return Response(status_code=204)
    return _anchor_item_schema(view)


@coordination_portal_router.get(
    "/mock-exams/{mock_exam_id}/anchors",
    response_model=MockExamAnchorListResponse,
    summary="List the equating anchors of a mock exam and where else they appear",
)
async def list_mock_exam_anchors(
    mock_exam_id: UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> MockExamAnchorListResponse:
    async with session_factory() as session:
        try:
            status, views = await list_anchors(
                session, requester=_requester(ctx), mock_exam_id=mock_exam_id
            )
        except Exception as exc:  # noqa: BLE001 - remapped below
            raise _map_anchor_error(exc) from exc
    return MockExamAnchorListResponse(
        mock_exam_id=str(mock_exam_id),
        status=status,
        anchors=[_anchor_item_schema(view) for view in views],
    )
```

- [ ] **Step 6: Rodar o teste e ver passar**

Run: `.venv/bin/python -m pytest tests/test_mock_exam_anchors_http.py -v`
Expected: PASS (7 testes)

- [ ] **Step 7: Rodar a regressão do portal de coordenação**

Run: `.venv/bin/python -m pytest tests/test_coordination_portal_http.py tests/test_coordination_portal.py -v`
Expected: PASS (sem novas falhas)

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/services/mock_exam_anchors.py \
        src/agente_ia_edu/api/schemas/coordination_portal.py \
        src/agente_ia_edu/api/routes/coordination_portal.py \
        tests/test_mock_exam_anchors_http.py
git commit -m "feat(simulados): gestao de itens-ancora no portal de coordenacao

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 5: Devolutiva de gabarito nunca expõe item-âncora

**Files:**
- Create: `src/agente_ia_edu/services/mock_exam_answer_key_release.py`
- Modify: `src/agente_ia_edu/api/schemas/student.py` (275 linhas; acrescentar ao final)
- Modify: `src/agente_ia_edu/api/routes/student.py` (965 linhas; imports em `:1-23`,
  `student_router` em `:25`; acrescentar a rota ao final)
- Create: `tests/test_mock_exam_answer_key_release.py`

**Interfaces:**
- Consumes: `MockExam`, `MockExamItem`, `MockExamResponse` (Fase 1);
  `ExternalIdentityContext` (`agente_ia_edu.identity`), `get_current_identity` e
  `get_session_factory` (`api/dependencies.py`), já importados em `routes/student.py:9`.
- Produces:
  - `AnswerKeyItem(position: int, area_code: str, correct_option: str, is_anchor: bool, chosen_option: str | None, is_correct: bool | None)`
  - `ReleasedAnswerKeyItem(position, area_code, correct_option, chosen_option, is_correct)`
  - `AnswerKeyRelease(items: tuple[ReleasedAnswerKeyItem, ...], withheld_count: int)`
  - `build_answer_key_release(items: Sequence[AnswerKeyItem]) -> AnswerKeyRelease` (puro)
  - `async load_student_answer_key(session, *, mock_exam_id, student_id) -> tuple[MockExam, tuple[AnswerKeyItem, ...]]`
  - `MockExamAnswerKeyNotAvailable(ValueError)`
  - Schemas `StudentMockExamAnswerKeyItem`, `StudentMockExamAnswerKeyResponse`
  - Rota `GET /api/v1/student/mock-exams/{mock_exam_id}/answer-key`

**Decisão registrada (o spec não fecha o formato — ver relatório):** §6.4 diz que o sistema
"impede que [itens-âncora] apareçam em qualquer devolutiva de gabarito ao aluno" mas não diz
como ser honesto sobre a omissão. Este plano omite o item **inteiro** — nem a alternativa
correta, nem a marcação do aluno, nem o acerto/erro — e devolve `withheld_count`
**agregado, sem posições**. Motivo: qualquer marcador por posição ("questão 12: reservada")
revelaria exatamente quais questões se repetem entre aplicações, que é a informação que
mata a equalização. O item continua contando normalmente para a nota; o que ele não faz é
aparecer no gabarito.

**Trava adicional registrada:** a devolutiva só é liberada com o simulado em `PUBLISHED`.
Antes disso não existe gabarito publicado para ninguém.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/test_mock_exam_answer_key_release.py`:

```python
"""Regra de publicação de gabarito de simulado (spec §6.4, Fase 5, Task 6).

Item-âncora não pode vazar. Se vazar, a equalização morre no segundo simulado
do ano — e morre em SILÊNCIO, porque a calibragem continua rodando e
devolvendo número.

Duas camadas de prova: a regra pura (que é onde a garantia mora) e o caminho
HTTP real (que é por onde o aluno chega).
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid
from datetime import date, datetime, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.student import student_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import MockExam, MockExamItem, MockExamResponse
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.mock_exam_answer_key_release import (
    AnswerKeyItem,
    build_answer_key_release,
)

SCHOOL_ID = _uuid.uuid4()
STUDENT = "user:aluno_a"
AREA = "MT"


class AnswerKeyReleaseRule(unittest.TestCase):
    def test_anchor_items_are_removed_entirely_and_only_counted(self) -> None:
        items = (
            AnswerKeyItem(1, AREA, "A", False, "A", True),
            AnswerKeyItem(2, AREA, "B", True, "C", False),
            AnswerKeyItem(3, AREA, "C", False, None, False),
            AnswerKeyItem(4, AREA, "D", True, "D", True),
        )

        release = build_answer_key_release(items)

        self.assertEqual([item.position for item in release.items], [1, 3])
        self.assertEqual(release.withheld_count, 2)
        # nada de uma âncora sobrevive: nem posição, nem gabarito, nem acerto
        serialized = repr(release)
        self.assertNotIn("position=2", serialized)
        self.assertNotIn("position=4", serialized)

    def test_release_preserves_the_students_own_answer_for_released_items(self) -> None:
        release = build_answer_key_release(
            (AnswerKeyItem(7, AREA, "E", False, "B", False),)
        )

        self.assertEqual(release.items[0].correct_option, "E")
        self.assertEqual(release.items[0].chosen_option, "B")
        self.assertFalse(release.items[0].is_correct)

    def test_an_exam_made_only_of_anchors_releases_nothing(self) -> None:
        release = build_answer_key_release(
            (AnswerKeyItem(1, AREA, "A", True, "A", True),)
        )

        self.assertEqual(release.items, ())
        self.assertEqual(release.withheld_count, 1)


class AnswerKeyReleaseHTTP(unittest.TestCase):
    def setUp(self) -> None:
        async def setup():
            engine = create_async_engine(
                "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
            )
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            now = datetime(2026, 3, 1, tzinfo=timezone.utc)
            async with factory() as session:
                self.exam_id = _uuid.uuid4()
                self.draft_exam_id = _uuid.uuid4()
                session.add(
                    MockExam(
                        id=self.exam_id, school_id=SCHOOL_ID, academic_year_id=None,
                        name="Simulado 1", application_date=date(2026, 3, 10),
                        exam_day=1, status="PUBLISHED", created_at=now,
                    )
                )
                session.add(
                    MockExam(
                        id=self.draft_exam_id, school_id=SCHOOL_ID, academic_year_id=None,
                        name="Simulado 2", application_date=date(2026, 6, 10),
                        exam_day=1, status="SCANNED", created_at=now,
                    )
                )
                for position, (option, is_anchor) in enumerate(
                    [("A", False), ("B", True), ("C", False)], start=1
                ):
                    item = MockExamItem(
                        id=_uuid.uuid4(), school_id=SCHOOL_ID, mock_exam_id=self.exam_id, position=position,
                        area_code=AREA, correct_option=option, is_anchor=is_anchor,
                        anchor_key=f"ANC-{position:02d}" if is_anchor else None,
                        source_question_version_id=None,
                    )
                    session.add(item)
                    session.add(
                        MockExamResponse(
                            id=_uuid.uuid4(), school_id=SCHOOL_ID, mock_exam_id=self.exam_id,
                            student_id=STUDENT, item_id=item.id,
                            chosen_option=option, is_correct=True,
                        )
                    )
                await session.commit()

            self.engine = engine
            self.factory = factory

        asyncio.run(setup())

        app = FastAPI()
        app.include_router(student_router)
        app.dependency_overrides[get_session_factory] = lambda: self.factory
        app.dependency_overrides[get_current_identity] = lambda: ExternalIdentityContext(
            external_user_id=STUDENT, institution_id=str(SCHOOL_ID), classroom_id=None
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        asyncio.run(self.engine.dispose())

    def test_the_student_endpoint_never_returns_an_anchor(self) -> None:
        response = self.client.get(
            f"/api/v1/student/mock-exams/{self.exam_id}/answer-key"
        )

        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual([item["position"] for item in body["items"]], [1, 3])
        self.assertEqual(body["withheld_count"], 1)
        self.assertNotIn("B", [item["correct_option"] for item in body["items"]])

    def test_the_answer_key_is_not_released_before_publication(self) -> None:
        response = self.client.get(
            f"/api/v1/student/mock-exams/{self.draft_exam_id}/answer-key"
        )

        self.assertEqual(response.status_code, 409, response.text)

    def test_unknown_mock_exam_is_404(self) -> None:
        response = self.client.get(
            f"/api/v1/student/mock-exams/{_uuid.uuid4()}/answer-key"
        )

        self.assertEqual(response.status_code, 404, response.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_mock_exam_answer_key_release.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.services.mock_exam_answer_key_release'`

- [ ] **Step 3: Escrever o serviço**

Criar `src/agente_ia_edu/services/mock_exam_answer_key_release.py`:

```python
"""Regra de publicação de gabarito de simulado (spec §6.4).

"O sistema marca quais itens são âncora e impede que apareçam em qualquer
devolutiva de gabarito ao aluno."

Sem isso a equalização morre no segundo simulado do ano, e morre em silêncio:
a calibragem continua rodando e devolvendo número, só que sobre uma âncora que
os alunos já conhecem — a assinatura exata de deriva que a Task 2 detecta
DEPOIS do estrago.

FORMATO DA OMISSÃO. O item-âncora é removido INTEIRO: não sai a alternativa
correta, não sai a marcação do aluno, não sai o acerto/erro, e a posição não
aparece em lugar nenhum da resposta. O que sai é `withheld_count`, agregado.
Qualquer marcador por posição ("questão 12: reservada") entregaria justamente
quais questões se repetem entre aplicações. O item continua contando
normalmente para a nota — o que ele não faz é aparecer no gabarito.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import MockExam, MockExamItem, MockExamResponse

PUBLISHED_STATUS = "PUBLISHED"


class MockExamAnswerKeyNotAvailable(ValueError):
    """O gabarito deste simulado ainda não foi publicado."""


@dataclass(frozen=True)
class AnswerKeyItem:
    position: int
    area_code: str
    correct_option: str
    is_anchor: bool
    chosen_option: str | None
    is_correct: bool | None


@dataclass(frozen=True)
class ReleasedAnswerKeyItem:
    position: int
    area_code: str
    correct_option: str
    chosen_option: str | None
    is_correct: bool | None


@dataclass(frozen=True)
class AnswerKeyRelease:
    items: tuple[ReleasedAnswerKeyItem, ...]
    withheld_count: int


def build_answer_key_release(items: Sequence[AnswerKeyItem]) -> AnswerKeyRelease:
    """A regra, pura e sem banco: âncora sai, o resto fica, e a contagem do que
    saiu é agregada."""
    released = tuple(
        ReleasedAnswerKeyItem(
            position=item.position,
            area_code=item.area_code,
            correct_option=item.correct_option,
            chosen_option=item.chosen_option,
            is_correct=item.is_correct,
        )
        for item in items
        if not item.is_anchor
    )
    withheld = sum(1 for item in items if item.is_anchor)
    return AnswerKeyRelease(items=released, withheld_count=withheld)


async def load_student_answer_key(
    session: AsyncSession, *, mock_exam_id: uuid.UUID, student_id: str
) -> tuple[MockExam, tuple[AnswerKeyItem, ...]]:
    """Carrega o gabarito completo (com as âncoras ainda dentro) junto com as
    respostas daquele aluno. Quem filtra é `build_answer_key_release`."""
    exam = await session.get(MockExam, mock_exam_id)
    if exam is None:
        raise LookupError(str(mock_exam_id))
    if (exam.status or "").upper() != PUBLISHED_STATUS:
        raise MockExamAnswerKeyNotAvailable(
            f"mock exam {mock_exam_id} is {exam.status}, not {PUBLISHED_STATUS}"
        )

    rows = (
        await session.execute(
            select(MockExamItem, MockExamResponse)
            .outerjoin(
                MockExamResponse,
                (MockExamResponse.item_id == MockExamItem.id)
                & (MockExamResponse.student_id == student_id),
            )
            .where(MockExamItem.mock_exam_id == mock_exam_id)
            .order_by(MockExamItem.position.asc())
        )
    ).all()

    items = tuple(
        AnswerKeyItem(
            position=int(item.position),
            area_code=item.area_code,
            correct_option=item.correct_option,
            is_anchor=bool(item.is_anchor),
            chosen_option=response.chosen_option if response is not None else None,
            is_correct=bool(response.is_correct) if response is not None else None,
        )
        for item, response in rows
    )
    return exam, items
```

- [ ] **Step 4: Escrever o schema**

Acrescentar ao final de `src/agente_ia_edu/api/schemas/student.py`:

```python
# ============================================================================
# Simulados Fase 5 — devolutiva de gabarito (spec §6.4).
# `withheld_count` é AGREGADO de propósito: nenhuma posição de item-âncora
# atravessa esta fronteira.
# ============================================================================


class StudentMockExamAnswerKeyItem(BaseModel):
    position: int
    area_code: str
    correct_option: str
    chosen_option: Optional[str] = None
    is_correct: Optional[bool] = None


class StudentMockExamAnswerKeyResponse(BaseModel):
    mock_exam_id: str
    mock_exam_name: str
    items: list[StudentMockExamAnswerKeyItem] = Field(default_factory=list)
    withheld_count: int = 0
    withheld_notice: str = (
        "Algumas questões desta prova não têm gabarito divulgado porque são "
        "reutilizadas para comparar seu desempenho entre simulados. Elas contam "
        "normalmente na sua nota."
    )
```

- [ ] **Step 5: Escrever a rota**

Em `src/agente_ia_edu/api/routes/student.py`, acrescentar ao bloco de import de
`..schemas.student` (`:10-16`) os nomes `StudentMockExamAnswerKeyItem` e
`StudentMockExamAnswerKeyResponse`. **Não** acrescentar imports de `uuid` nem de
`HTTPException`: o arquivo já tem `from uuid import UUID as _UUID` em `:199` e
`from fastapi import HTTPException` em `:213` (imports no meio do arquivo, com `noqa: E402`
— padrão vigente deste arquivo, seguir e não "arrumar"). Usar o alias `_UUID` nas
assinaturas. Acrescentar:

```python
from ...services.mock_exam_answer_key_release import (
    MockExamAnswerKeyNotAvailable,
    build_answer_key_release,
    load_student_answer_key,
)
```

Acrescentar ao final do arquivo:

```python
# ============================================================================
# Simulados Fase 5 — devolutiva de gabarito do aluno (spec §6.4).
# Item-âncora NUNCA atravessa esta rota. A regra mora em
# services/mock_exam_answer_key_release.build_answer_key_release; aqui só se
# monta o payload.
# ============================================================================


@student_router.get(
    "/mock-exams/{mock_exam_id}/answer-key",
    response_model=StudentMockExamAnswerKeyResponse,
    summary="Published answer key of a mock exam, with equating anchors withheld",
)
async def get_student_mock_exam_answer_key(
    mock_exam_id: _UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> StudentMockExamAnswerKeyResponse:
    async with session_factory() as session:
        try:
            exam, items = await load_student_answer_key(
                session,
                mock_exam_id=mock_exam_id,
                student_id=identity.external_user_id,
            )
        except LookupError as exc:
            raise HTTPException(status_code=404, detail="Not found") from exc
        except MockExamAnswerKeyNotAvailable as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc

    release = build_answer_key_release(items)
    return StudentMockExamAnswerKeyResponse(
        mock_exam_id=str(mock_exam_id),
        mock_exam_name=exam.name,
        items=[
            StudentMockExamAnswerKeyItem(
                position=item.position,
                area_code=item.area_code,
                correct_option=item.correct_option,
                chosen_option=item.chosen_option,
                is_correct=item.is_correct,
            )
            for item in release.items
        ],
        withheld_count=release.withheld_count,
    )
```

- [ ] **Step 6: Rodar o teste e ver passar**

Run: `.venv/bin/python -m pytest tests/test_mock_exam_answer_key_release.py -v`
Expected: PASS (6 testes)

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/services/mock_exam_answer_key_release.py \
        src/agente_ia_edu/api/schemas/student.py \
        src/agente_ia_edu/api/routes/student.py \
        tests/test_mock_exam_answer_key_release.py
git commit -m "feat(simulados): gabarito ao aluno nunca expoe item-ancora

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 6: Saúde das âncoras — endpoint e painel "Âncoras e Equalização"

**Files:**
- Modify: `src/agente_ia_edu/services/mock_exam_anchors.py` (acrescentar ao final)
- Modify: `src/agente_ia_edu/api/schemas/coordination_portal.py` (acrescentar ao final)
- Modify: `src/agente_ia_edu/api/routes/coordination_portal.py` (acrescentar ao final)
- Modify: `src/agente_ia_edu/web/coordination.html` (nav em `:26-62`, painéis em `:126-477`)
- Modify: `src/agente_ia_edu/web/coordination.js` (`titleMap` em `:76-89`, `loadCurrentView`
  em `:101-114`, `coordEsc` em `:611`)
- Modify: `src/agente_ia_edu/web/coordination.css` (99 linhas; acrescentar ao final)
- Create: `tests/test_mock_exam_anchor_health_http.py`
- Create: `tests/test_mock_exam_anchor_health_frontend.js`

**Interfaces:**
- Consumes (Tasks 1 e 4): `ANCHOR_HEALTHY`, `ANCHOR_DRIFTED`, `ANCHOR_NOT_ESTIMABLE` de
  `tri/equating.py`; `_load_exam`,
  `AnchorAuthError`, `AnchorNotFound` e `Requester` já em `services/mock_exam_anchors.py`;
  `_map_anchor_error` e `_requester` já em `routes/coordination_portal.py`.
- Produces:
  - `TriScaleSummary(scale_id: str, area_code: str, name: str, reference_label: str, verified: bool)`
  - `AnchorHealthEntry(anchor_key, applications, healthy_count, drifted_count, not_estimable_count, last_status, last_mock_exam_name, last_calibrated_at)`
  - `async list_tri_scales(session, *, requester, school_id) -> tuple[TriScaleSummary, ...]`
  - `async anchor_health(session, *, requester, scale_id) -> tuple[AnchorHealthEntry, ...]`
  - Schemas `TriScaleSummaryItem`, `TriScaleListResponse`, `AnchorHealthItem`,
    `AnchorHealthResponse`
  - Rotas `GET /api/v1/coordination/tri-scales?school_id=` e
    `GET /api/v1/coordination/tri-scales/{scale_id}/anchors/health`
  - Em `coordination.js`: `loadAnchorHealth()`, `loadTriScaleOptions()`,
    `renderAnchorHealth(entries)`, view `anchors`

- [ ] **Step 1: Escrever o teste HTTP que falha**

Criar `tests/test_mock_exam_anchor_health_http.py`:

```python
"""Saúde das âncoras de uma régua (Fase 5, Task 7).

A tela existe para uma pergunta só: "em quais âncoras eu ainda posso
confiar?". Por isso a agregação precisa vir das MARCAS REAIS gravadas em
`tri_item_parameters.flags` pela Task 4 — nunca recalculada aqui, nunca
inventada. Âncora que nunca foi calibrada não aparece com status verde: ela
simplesmente não tem histórico.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.api.routes.coordination_portal import coordination_portal_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    MockExam,
    MockExamItem,
    TriCalibration,
    TriItemParameter,
    TriScale,
)
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext
from agente_ia_edu.tri.equating import ANCHOR_DRIFTED, ANCHOR_HEALTHY

SCHOOL_ID = _uuid.uuid4()
OTHER_SCHOOL_ID = _uuid.uuid4()
AREA = "MT"


def _flags(status: str, *, quality: tuple[str, ...] = ()) -> list[str]:
    """`tri_item_parameters.flags` é lista de strings: os códigos de qualidade
    de §6.5 que a Fase 3 grava, mais o código de âncora que a Fase 5 acrescenta."""
    return [*quality, status]


class AnchorHealthHTTP(unittest.TestCase):
    def setUp(self) -> None:
        async def setup():
            engine = create_async_engine(
                "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
            )
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            now = datetime(2026, 3, 1, tzinfo=timezone.utc)
            async with factory() as session:
                self.scale_id = _uuid.uuid4()
                session.add(
                    TriScale(
                        id=self.scale_id, school_id=SCHOOL_ID, area_code=AREA,
                        name="Régua MT 2026", base_mock_exam_id=None,
                        reference_label="ENEM 2025", reference_min_score=312.6,
                        reference_median_score=520.0, reference_max_score=980.3,
                        reference_theta_min=-3.0, reference_theta_max=3.0,
                        reference_source="portal secundário",
                        reference_verified_at=None, created_at=now,
                    )
                )
                # ANC-01 saudável nas duas aplicações; ANC-02 saudável na
                # primeira e DERIVADA na segunda — é esse histórico que a tela
                # precisa mostrar.
                plan = [
                    ("Simulado 1", 0, [("ANC-01", ANCHOR_HEALTHY),
                                       ("ANC-02", ANCHOR_HEALTHY)]),
                    ("Simulado 2", 60, [("ANC-01", ANCHOR_HEALTHY),
                                        ("ANC-02", ANCHOR_DRIFTED)]),
                ]
                for name, offset, anchors in plan:
                    exam_id = _uuid.uuid4()
                    session.add(
                        MockExam(
                            id=exam_id, school_id=SCHOOL_ID, academic_year_id=None,
                            name=name,
                            application_date=(now + timedelta(days=offset)).date(),
                            exam_day=1, status="PUBLISHED", created_at=now,
                        )
                    )
                    calibration_id = _uuid.uuid4()
                    session.add(
                        TriCalibration(
                            id=calibration_id, mock_exam_id=exam_id, area_code=AREA,
                            scale_id=self.scale_id, model="2PL", status="DONE",
                            n_examinees=800, n_items=2, converged=True, iterations=40,
                            log_likelihood=-1.0, engine_version="tri-1.0.0",
                            calibrated_at=now + timedelta(days=offset),
                        )
                    )
                    for position, (key, status) in enumerate(anchors, start=1):
                        item_id = _uuid.uuid4()
                        session.add(
                            MockExamItem(
                                id=item_id, mock_exam_id=exam_id, position=position,
                                area_code=AREA, correct_option="A", is_anchor=True,
                                anchor_key=key, source_question_version_id=None,
                            )
                        )
                        session.add(
                            TriItemParameter(
                                id=_uuid.uuid4(), calibration_id=calibration_id,
                                item_id=item_id, a=1.1, b=0.2, c=None, se_a=0.0,
                                se_b=0.0, n_responses=800, p_value=0.5,
                                point_biserial=0.3,
                                is_fixed=status == ANCHOR_HEALTHY,
                                flags=_flags(status, quality=("LOW_DISCRIMINATION",) if key == "ANC-02" else ()),
                            )
                        )
                await session.commit()

            self.engine = engine
            self.factory = factory

        asyncio.run(setup())

        app = FastAPI()
        app.include_router(coordination_portal_router)
        app.dependency_overrides[get_session_factory] = lambda: self.factory
        self.app = app
        self.client = TestClient(app)
        self._as_coordinator(SCHOOL_ID)

    def _as_coordinator(self, school_id) -> None:
        context = AuthenticatedUserContext(
            user_id="user:coord_a", external_identity_id="user:coord_a",
            school_id=str(school_id), role="COORDINATOR",
        )
        identity = ExternalIdentityContext(
            external_user_id="user:coord_a", institution_id=str(school_id),
            classroom_id=None,
        )
        self.app.dependency_overrides[get_current_authenticated_context] = lambda: context
        self.app.dependency_overrides[get_current_identity] = lambda: identity

    def tearDown(self) -> None:
        self.client.close()
        asyncio.run(self.engine.dispose())

    def test_health_aggregates_the_real_flags_per_anchor_key(self) -> None:
        response = self.client.get(
            f"/api/v1/coordination/tri-scales/{self.scale_id}/anchors/health"
        )

        self.assertEqual(response.status_code, 200, response.text)
        entries = {item["anchor_key"]: item for item in response.json()["anchors"]}
        self.assertEqual(set(entries), {"ANC-01", "ANC-02"})

        healthy = entries["ANC-01"]
        self.assertEqual(healthy["applications"], 2)
        self.assertEqual(healthy["healthy_count"], 2)
        self.assertEqual(healthy["drifted_count"], 0)
        self.assertEqual(healthy["not_estimable_count"], 0)
        self.assertEqual(healthy["last_status"], ANCHOR_HEALTHY)

        drifted = entries["ANC-02"]
        self.assertEqual(drifted["applications"], 2)
        self.assertEqual(drifted["drifted_count"], 1)
        self.assertEqual(drifted["healthy_count"], 1)
        self.assertEqual(drifted["last_status"], ANCHOR_DRIFTED)
        self.assertEqual(drifted["last_mock_exam_name"], "Simulado 2")
        # o código de qualidade da Fase 3 convive com o de âncora e não confunde
        # a contagem: a agregação lê só os códigos de âncora
        self.assertEqual(drifted["not_estimable_count"], 0)

    def test_scale_listing_reports_unverified_rulers(self) -> None:
        response = self.client.get(
            f"/api/v1/coordination/tri-scales?school_id={SCHOOL_ID}"
        )

        self.assertEqual(response.status_code, 200, response.text)
        scales = response.json()["scales"]
        self.assertEqual(len(scales), 1)
        self.assertEqual(scales[0]["scale_id"], str(self.scale_id))
        self.assertFalse(scales[0]["verified"])

    def test_coordinator_from_another_school_is_refused(self) -> None:
        self._as_coordinator(OTHER_SCHOOL_ID)
        response = self.client.get(
            f"/api/v1/coordination/tri-scales/{self.scale_id}/anchors/health"
        )

        self.assertEqual(response.status_code, 403, response.text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_mock_exam_anchor_health_http.py -v`
Expected: FAIL — `404 Not Found` (rotas não existem)

- [ ] **Step 3: Escrever o serviço**

Acrescentar ao final de `src/agente_ia_edu/services/mock_exam_anchors.py`:

```python
@dataclass(frozen=True)
class TriScaleSummary:
    scale_id: str
    area_code: str
    name: str
    reference_label: str
    verified: bool


@dataclass(frozen=True)
class AnchorHealthEntry:
    """Histórico de uma âncora numa régua.

    Não há campo de deriva numérica: `tri_item_parameters.flags` é lista de
    strings e §3 não tem onde guardar o número (ver a limitação registrada na
    Task 3). O que está aqui é inteiramente respaldado por linha gravada.
    """

    anchor_key: str
    applications: int
    healthy_count: int
    drifted_count: int
    not_estimable_count: int
    last_status: str
    last_mock_exam_name: str
    last_calibrated_at: str


async def list_tri_scales(
    session: AsyncSession, *, requester: Requester, school_id
) -> tuple[TriScaleSummary, ...]:
    if not requester.is_platform_admin:
        if not requester.is_privileged:
            raise AnchorAuthError("only a coordinator may list TRI scales")
        if not requester.school_id or str(school_id) != str(requester.school_id):
            raise AnchorAuthError("school is outside this coordinator's scope")
    rows = (
        await session.execute(
            select(TriScale)
            .where(TriScale.school_id == school_id)
            .order_by(TriScale.area_code.asc(), TriScale.created_at.asc())
        )
    ).scalars().all()
    return tuple(
        TriScaleSummary(
            scale_id=str(scale.id),
            area_code=scale.area_code,
            name=scale.name,
            reference_label=scale.reference_label or "",
            verified=scale.reference_verified_at is not None,
        )
        for scale in rows
    )


async def anchor_health(
    session: AsyncSession, *, requester: Requester, scale_id: uuid.UUID
) -> tuple[AnchorHealthEntry, ...]:
    """Histórico de cada `anchor_key` nesta régua, lido das MARCAS REAIS que a
    equalização gravou em `tri_item_parameters.flags`.

    Nada é recalculado aqui: a tela mostra o que o motor decidiu na hora da
    calibragem, que é o único número auditável dois anos depois.
    """
    scale = await session.get(TriScale, scale_id)
    if scale is None:
        raise AnchorNotFound(str(scale_id))
    if not requester.is_platform_admin:
        if not requester.is_privileged:
            raise AnchorAuthError("only a coordinator may inspect anchor health")
        if not requester.school_id or str(scale.school_id) != str(requester.school_id):
            raise AnchorAuthError("scale is outside this coordinator's school")

    rows = (
        await session.execute(
            select(
                MockExamItem.anchor_key,
                TriItemParameter.flags,
                MockExam.name,
                TriCalibration.calibrated_at,
            )
            .join(TriItemParameter, TriItemParameter.item_id == MockExamItem.id)
            .join(TriCalibration, TriCalibration.id == TriItemParameter.calibration_id)
            .join(MockExam, MockExam.id == TriCalibration.mock_exam_id)
            .where(
                TriCalibration.scale_id == scale_id,
                MockExamItem.is_anchor.is_(True),
            )
            .order_by(TriCalibration.calibrated_at.asc())
        )
    ).all()

    counter_of = {
        ANCHOR_HEALTHY: "healthy_count",
        ANCHOR_DRIFTED: "drifted_count",
        ANCHOR_NOT_ESTIMABLE: "not_estimable_count",
    }
    aggregated: dict[str, dict] = {}
    for anchor_key, flags, exam_name, calibrated_at in rows:
        if not (anchor_key or "").strip():
            continue
        status = next(
            (flag for flag in (flags or []) if flag in counter_of), None
        )
        if status is None:
            # a âncora existe na prova mas aquela calibragem não foi equalizada
            # (nem carimbada): não conta como aplicação saudável nem derivada
            continue
        entry = aggregated.setdefault(
            anchor_key,
            {
                "applications": 0,
                "healthy_count": 0,
                "drifted_count": 0,
                "not_estimable_count": 0,
                "last_status": "",
                "last_mock_exam_name": "",
                "last_calibrated_at": "",
            },
        )
        entry["applications"] += 1
        entry[counter_of[status]] += 1
        # as linhas vêm ordenadas por calibrated_at crescente: a última
        # sobrescrita é a aplicação mais recente
        entry["last_status"] = status
        entry["last_mock_exam_name"] = exam_name
        entry["last_calibrated_at"] = (
            calibrated_at.isoformat() if calibrated_at is not None else ""
        )

    return tuple(
        AnchorHealthEntry(anchor_key=key, **values)
        for key, values in sorted(aggregated.items())
    )
```

Acrescentar aos imports do topo de `services/mock_exam_anchors.py`:

```python
from ..db.models import MockExam, MockExamItem, TriCalibration, TriItemParameter, TriScale
from ..tri.equating import ANCHOR_DRIFTED, ANCHOR_HEALTHY, ANCHOR_NOT_ESTIMABLE
```

(substituindo a linha `from ..db.models import MockExam, MockExamItem` já existente).

- [ ] **Step 4: Escrever os schemas e as rotas**

Acrescentar ao final de `src/agente_ia_edu/api/schemas/coordination_portal.py`:

```python
class TriScaleSummaryItem(BaseModel):
    scale_id: str
    area_code: str
    name: str
    reference_label: str
    verified: bool


class TriScaleListResponse(BaseModel):
    scales: list[TriScaleSummaryItem] = Field(default_factory=list)


class AnchorHealthItem(BaseModel):
    anchor_key: str
    applications: int
    healthy_count: int
    drifted_count: int
    not_estimable_count: int
    last_status: str
    last_mock_exam_name: str
    last_calibrated_at: str


class AnchorHealthResponse(BaseModel):
    scale_id: str
    anchors: list[AnchorHealthItem] = Field(default_factory=list)
```

Acrescentar ao final de `src/agente_ia_edu/api/routes/coordination_portal.py` (e os nomes
novos ao import de `..schemas.coordination_portal` e ao de `...services.mock_exam_anchors`):

```python
@coordination_portal_router.get(
    "/tri-scales",
    response_model=TriScaleListResponse,
    summary="List the TRI reporting scales (rulers) of a school",
)
async def list_coordination_tri_scales(
    school_id: UUID = Query(...),
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> TriScaleListResponse:
    async with session_factory() as session:
        try:
            scales = await list_tri_scales(
                session, requester=_requester(ctx), school_id=school_id
            )
        except Exception as exc:  # noqa: BLE001 - remapped below
            raise _map_anchor_error(exc) from exc
    return TriScaleListResponse(
        scales=[
            TriScaleSummaryItem(
                scale_id=scale.scale_id, area_code=scale.area_code, name=scale.name,
                reference_label=scale.reference_label, verified=scale.verified,
            )
            for scale in scales
        ]
    )


@coordination_portal_router.get(
    "/tri-scales/{scale_id}/anchors/health",
    response_model=AnchorHealthResponse,
    summary="Which anchors of this ruler are healthy and which drifted",
)
async def get_anchor_health(
    scale_id: UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> AnchorHealthResponse:
    async with session_factory() as session:
        try:
            entries = await anchor_health(
                session, requester=_requester(ctx), scale_id=scale_id
            )
        except Exception as exc:  # noqa: BLE001 - remapped below
            raise _map_anchor_error(exc) from exc
    return AnchorHealthResponse(
        scale_id=str(scale_id),
        anchors=[
            AnchorHealthItem(
                anchor_key=entry.anchor_key,
                applications=entry.applications,
                healthy_count=entry.healthy_count,
                drifted_count=entry.drifted_count,
                not_estimable_count=entry.not_estimable_count,
                last_status=entry.last_status,
                last_mock_exam_name=entry.last_mock_exam_name,
                last_calibrated_at=entry.last_calibrated_at,
            )
            for entry in entries
        ],
    )
```

- [ ] **Step 5: Rodar o teste HTTP e ver passar**

Run: `.venv/bin/python -m pytest tests/test_mock_exam_anchor_health_http.py -v`
Expected: PASS (3 testes)

- [ ] **Step 6: Escrever o teste de frontend que falha**

Criar `tests/test_mock_exam_anchor_health_frontend.js`:

```javascript
/**
 * Fase 5 (âncoras e equalização, 2026-09-29) — painel "Âncoras e Equalização"
 * do portal de coordenação.
 *
 * Asserções estáticas sobre o SPA entregue (node:test + regex), no mesmo
 * estilo de tests/test_material_assignment_student_materials_view_frontend.js.
 *
 * O que protege: a tela chama a API real, nunca fabrica status, e mostra a
 * âncora derivada de forma INEQUÍVOCA — o operador precisa saber que aquele
 * item saiu da equalização, porque a decisão seguinte (aposentar a questão)
 * é dele, não do software.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const coordinationJs = fs.readFileSync(path.join(WEB, 'coordination.js'), 'utf8');
const coordinationHtml = fs.readFileSync(path.join(WEB, 'coordination.html'), 'utf8');

// O bloco inteiro da Fase 5 fica entre o seu comentário de abertura e
// `coordEsc`, que é a próxima função do arquivo. Ancorar no comentário (e não
// numa das funções) garante que TODO o bloco — mapa de rótulos, loaders e
// render — entre nas asserções.
const _start = coordinationJs.indexOf('// Fase 5 — âncoras e equalização');
const _end = coordinationJs.indexOf('function coordEsc(');
const ANCHOR_BLOCK = coordinationJs.slice(_start, _end > _start ? _end : undefined);

test('the anchor health block exists and is substantial', () => {
  assert.ok(_start >= 0, 'the Fase 5 anchor block should be present');
  assert.match(ANCHOR_BLOCK, /async function loadAnchorHealth\(\)/);
  assert.match(ANCHOR_BLOCK, /function renderAnchorHealth\(/);
  assert.ok(ANCHOR_BLOCK.length > 400, 'anchor block should be present');
});

test('the view is reachable from the nav and from loadCurrentView', () => {
  assert.match(coordinationHtml, /data-view="anchors"/);
  assert.match(coordinationHtml, /id="view-anchors"/);
  assert.match(coordinationJs, /'anchors': \{ title: 'Âncoras e Equalização'/);
  assert.match(coordinationJs, /state\.currentView === 'anchors'\) initAnchorHealthView\(\)/);
});

test('it calls the real anchor-health endpoint, never mocked data', () => {
  assert.match(ANCHOR_BLOCK, /\/api\/v1\/coordination\/tri-scales\/\$\{[^}]*\}\/anchors\/health/);
  assert.match(coordinationJs, /\/api\/v1\/coordination\/tri-scales\?school_id=/);
});

test('status comes from the API field, never recomputed in the browser', () => {
  assert.match(ANCHOR_BLOCK, /entry\.last_status === 'ANCHOR_DRIFTED'/);
  assert.doesNotMatch(ANCHOR_BLOCK, /entry\.drifted_count >/);
});

test('a drifted anchor is shown as discarded, with its history', () => {
  assert.match(ANCHOR_BLOCK, /Descartada por deriva/);
  assert.match(ANCHOR_BLOCK, /entry\.healthy_count/);
  assert.match(ANCHOR_BLOCK, /entry\.applications/);
});

test('an unverified ruler is labelled as provisional', () => {
  assert.match(coordinationJs, /Régua não verificada/);
});

test('every interpolated value goes through coordEsc', () => {
  const interpolations = ANCHOR_BLOCK.match(/\$\{(?!\s*coordEsc)[^}]*\}/g) || [];
  const offenders = interpolations.filter((value) => /entry\.(anchor_key|last_mock_exam_name|last_status)/.test(value));
  assert.deepStrictEqual(offenders, [], `unescaped: ${offenders.join(', ')}`);
});
```

- [ ] **Step 7: Rodar o teste de frontend e ver falhar**

Run: `node --test tests/test_mock_exam_anchor_health_frontend.js`
Expected: FAIL — `loadAnchorHealth should be present`

- [ ] **Step 8: Escrever o HTML**

Em `src/agente_ia_edu/web/coordination.html`, acrescentar um item de navegação logo depois
do bloco `data-view="materials"` (`:47-49`):

```html
        <button class="nav-item" data-view="anchors">
          <span class="nav-icon">⚓</span> Âncoras e Equalização
        </button>
```

e um painel logo depois da seção `id="view-materials"` (termina em `:366`):

```html
        <section id="view-anchors" class="view-panel">
          <div class="card">
            <div class="card-header">
              <h3>Régua de reporte</h3>
            </div>
            <label class="anchor-scale-label" for="anchor-scale-select">Escolha a régua:</label>
            <select id="anchor-scale-select"></select>
            <p id="anchor-scale-notice" class="empty-text"></p>
          </div>
          <div class="card">
            <div class="card-header">
              <h3>Situação das âncoras</h3>
            </div>
            <p class="anchor-help">
              Âncoras são as questões repetidas entre simulados. São elas que mantêm a
              régua parada, permitindo comparar a nota de aplicações diferentes. Uma
              âncora que se desloca além do limiar é descartada da equalização — quase
              sempre porque a questão vazou.
            </p>
            <div id="anchor-health-container" aria-live="polite"></div>
          </div>
        </section>
```

- [ ] **Step 9: Escrever o JS**

Em `src/agente_ia_edu/web/coordination.js`:

1. no `titleMap` (`:76-89`), acrescentar a entrada:

```javascript
      'anchors': { title: 'Âncoras e Equalização', sub: 'Quais questões repetidas mantêm a régua parada, e quais derivaram' },
```

2. em `loadCurrentView` (`:101-114`), acrescentar:

```javascript
    if (state.currentView === 'anchors') initAnchorHealthView();
```

3. acrescentar, imediatamente antes de `function coordEsc(` (`:611`):

```javascript
  // Fase 5 — âncoras e equalização (spec §6.4). Nenhum status é calculado
  // aqui: o veredito vem de `last_status`, gravado pelo motor na hora da
  // calibragem. O navegador só pinta.
  const ANCHOR_STATUS_LABEL = {
    ANCHOR_HEALTHY: 'Saudável',
    ANCHOR_DRIFTED: 'Descartada por deriva',
    ANCHOR_NOT_ESTIMABLE: 'Descartada: não foi possível estimar',
  };

  function initAnchorHealthView() {
    const select = document.getElementById('anchor-scale-select');
    if (!select.dataset.wired) {
      select.dataset.wired = '1';
      select.addEventListener('change', () => {
        state.anchorScaleId = select.value;
        loadAnchorHealth();
      });
    }
    loadTriScaleOptions();
  }

  async function loadTriScaleOptions() {
    const select = document.getElementById('anchor-scale-select');
    const notice = document.getElementById('anchor-scale-notice');
    try {
      const res = await fetch(
        `/api/v1/coordination/tri-scales?school_id=${encodeURIComponent(state.schoolId)}`,
        { headers: { 'Authorization': `Bearer ${state.coordinatorId}` } }
      );
      if (!res.ok) throw new Error('Falha ao carregar réguas');
      const data = await res.json();
      state.triScales = data.scales || [];
      if (!state.triScales.length) {
        select.innerHTML = '<option value="">Nenhuma régua cadastrada</option>';
        notice.textContent = 'Nenhuma régua de reporte foi cadastrada para esta escola ainda.';
        document.getElementById('anchor-health-container').innerHTML =
          '<p class="empty-text">Sem régua, não há equalização para mostrar.</p>';
        return;
      }
      select.innerHTML = state.triScales
        .map((s) => `<option value="${coordEsc(s.scale_id)}">${coordEsc(s.area_code)} — ${coordEsc(s.name)}</option>`)
        .join('');
      state.anchorScaleId = state.anchorScaleId || state.triScales[0].scale_id;
      select.value = state.anchorScaleId;
      loadAnchorHealth();
    } catch (err) {
      console.warn('TRI scales error:', err);
      select.innerHTML = '<option value="">Erro ao carregar réguas</option>';
      notice.textContent = 'Não foi possível carregar as réguas agora.';
    }
  }

  async function loadAnchorHealth() {
    const container = document.getElementById('anchor-health-container');
    const notice = document.getElementById('anchor-scale-notice');
    const scale = (state.triScales || []).find((s) => s.scale_id === state.anchorScaleId);
    notice.textContent = scale && !scale.verified
      ? 'Régua não verificada: os valores de referência ainda não foram conferidos pela coordenação, e a nota sai marcada como provisória.'
      : '';
    if (!state.anchorScaleId) {
      container.innerHTML = '<p class="empty-text">Escolha uma régua.</p>';
      return;
    }
    container.innerHTML = '<p class="empty-text">Carregando âncoras...</p>';
    try {
      const res = await fetch(
        `/api/v1/coordination/tri-scales/${state.anchorScaleId}/anchors/health`,
        { headers: { 'Authorization': `Bearer ${state.coordinatorId}` } }
      );
      if (res.status === 403) {
        container.innerHTML = '<p class="empty-text text-danger">⚠️ Você não possui permissão para esta régua.</p>';
        return;
      }
      if (!res.ok) throw new Error('Falha ao carregar âncoras');
      const data = await res.json();
      renderAnchorHealth(data.anchors || []);
    } catch (err) {
      console.warn('Anchor health error:', err);
      container.innerHTML = '<p class="empty-text">Não foi possível carregar a situação das âncoras agora.</p>';
    }
  }

  function renderAnchorHealth(entries) {
    const container = document.getElementById('anchor-health-container');
    if (!entries.length) {
      container.innerHTML = '<p class="empty-text">Esta régua ainda não passou por nenhuma calibragem com âncoras.</p>';
      return;
    }
    container.innerHTML = entries.map((entry) => {
      const drifted = entry.last_status === 'ANCHOR_DRIFTED';
      const label = ANCHOR_STATUS_LABEL[entry.last_status] || entry.last_status;
      return `
        <div class="anchor-row ${drifted ? 'anchor-row-drifted' : ''}">
          <span class="anchor-key">${coordEsc(entry.anchor_key)}</span>
          <span class="anchor-status">${coordEsc(label)}</span>
          <span class="anchor-history">${entry.healthy_count} de ${entry.applications} aplicações saudáveis</span>
          <span class="anchor-last">última: ${coordEsc(entry.last_mock_exam_name)}</span>
        </div>
      `;
    }).join('');
  }
```

4. no objeto `state` (`:7-17`), acrescentar `anchorScaleId: ''` e `triScales: []`.

- [ ] **Step 10: Escrever o CSS**

Acrescentar ao final de `src/agente_ia_edu/web/coordination.css`:

```css
/* Fase 5 — âncoras e equalização */
.anchor-help { font-size: 13px; color: #555; margin: 0 0 12px; }
.anchor-scale-label { display: block; font-size: 13px; margin-bottom: 6px; }
.anchor-row {
  display: grid;
  grid-template-columns: 1fr 1.2fr 1.6fr 1.6fr;
  gap: 8px;
  align-items: center;
  padding: 8px 0;
  border-bottom: 1px solid #eee;
  font-size: 13px;
}
.anchor-row-drifted { background: #fff4f4; }
.anchor-key { font-weight: 600; }
.anchor-row-drifted .anchor-status { color: #b00020; font-weight: 600; }
```

- [ ] **Step 11: Rodar os dois testes e ver passar**

Run: `node --test tests/test_mock_exam_anchor_health_frontend.js`
Expected: PASS (7 testes)

Run: `.venv/bin/python -m pytest tests/test_mock_exam_anchor_health_http.py tests/test_mock_exam_anchors_http.py -v`
Expected: PASS (10 testes)

- [ ] **Step 12: Commit**

```bash
git add src/agente_ia_edu/services/mock_exam_anchors.py \
        src/agente_ia_edu/api/schemas/coordination_portal.py \
        src/agente_ia_edu/api/routes/coordination_portal.py \
        src/agente_ia_edu/web/coordination.html \
        src/agente_ia_edu/web/coordination.js \
        src/agente_ia_edu/web/coordination.css \
        tests/test_mock_exam_anchor_health_http.py \
        tests/test_mock_exam_anchor_health_frontend.js
git commit -m "feat(simulados): painel de saude das ancoras de equalizacao

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 7: Série de evolução no ano, com supressão sem equalização

**Files:**
- Create: `src/agente_ia_edu/services/mock_exam_evolution.py`
- Modify: `src/agente_ia_edu/api/schemas/student.py` (acrescentar ao final)
- Modify: `src/agente_ia_edu/api/routes/student.py` (acrescentar ao final)
- Modify: `src/agente_ia_edu/api/schemas/coordination_portal.py` (acrescentar ao final)
- Modify: `src/agente_ia_edu/api/routes/coordination_portal.py` (acrescentar ao final)
- Create: `tests/test_mock_exam_evolution.py`

**Interfaces:**
- Consumes (Task 3): `TriItemParameter` e a regra derivada de "calibragem equalizada" —
  aqui em consulta agregada, não chamando `is_equated_calibration` por ponto. Dos modelos
  da Fase 1: `MockExam`, `TriCalibration`, `TriScale`, `TriStudentScore`.
- Produces:
  - `EvolutionPoint(mock_exam_id: str, mock_exam_name: str, application_date: str, theta: float, scaled_score: float | None, equated: bool, scale_id: str)`
  - `EvolutionSeries(area_code: str, points: tuple[EvolutionPoint, ...], suppressed: bool, suppression_reason: str | None, ruler_verified: bool)`
  - `SINGLE_APPLICATION = "SINGLE_APPLICATION"`, `MIXED_SCALES = "MIXED_SCALES"`,
    `NO_ANCHOR_EQUATING = "NO_ANCHOR_EQUATING"`
  - `decide_evolution_series(area_code, points, *, ruler_verified) -> EvolutionSeries` (puro)
  - `async build_student_mock_exam_evolution(session, *, school_id, student_id) -> tuple[EvolutionSeries, ...]`
  - `async build_school_mock_exam_evolution(session, *, school_id) -> tuple[EvolutionSeries, ...]`
  - Schemas `SchoolMockExamEvolutionPoint`, `SchoolMockExamEvolutionSeries`,
    `SchoolMockExamEvolutionResponse` e a rota
    `GET /api/v1/coordination/mock-exams/evolution?school_id=`
  - Schemas `StudentMockExamEvolutionPoint`, `StudentMockExamEvolutionSeries`,
    `StudentMockExamEvolutionResponse`
  - Rota `GET /api/v1/student/mock-exams/evolution`

**A regra, registrada (§7):** a evolução por nota absoluta só é exibida quando existe
equalização por âncoras, **no boletim do aluno e no painel da escola, pela mesma regra**.
Duas aplicações são duas provas de dificuldade diferente, e uma queda de 62% para 54% não
distingue "a escola piorou" de "a prova era mais difícil". O argumento não perde força por a
média ser de uma escola em vez de um aluno — perde só a visibilidade, o que o torna mais
perigoso, não menos. Por isso `build_school_mock_exam_evolution` passa pelo **mesmo**
`decide_evolution_series`, e não por uma cópia relaxada da regra.

 Sem elas o gráfico é **suprimido, não estimado** — thetas de
calibragens independentes estão em réguas diferentes e compará-los é erro silencioso. A
supressão é aplicada **na origem**: a série suprimida sai com `scaled_score = None` em
todos os pontos, de modo que nenhum consumidor — este frontend, um export futuro, um
relatório de coordenação — consiga plotar a linha por engano. O frontend recebe a razão e
explica; ele não decide.

Uma calibragem conta como equalizada quando (a) tem ao menos um item travado (Task 4), ou
(b) é a calibragem do **simulado base da régua** — a primeira aplicação, que não tem o que
travar porque é ela que estabelece a escala.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/test_mock_exam_evolution.py`:

```python
"""Evolução em simulados ao longo do ano (spec §7, Fase 5, Task 8).

O erro que este arquivo existe para impedir é silencioso: dois thetas de
calibragens independentes são números plausíveis, na mesma faixa, com a mesma
cara — e estão em réguas diferentes. Plotar os dois na mesma linha produz um
gráfico bonito e falso.

Por isso a supressão é testada NA ORIGEM: a série suprimida sai sem
`scaled_score`, não apenas com uma bandeira que o frontend poderia ignorar.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid
from datetime import date, datetime, timedelta, timezone

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import get_current_identity, get_session_factory
from agente_ia_edu.api.routes.student import student_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    MockExam,
    MockExamItem,
    TriCalibration,
    TriItemParameter,
    TriScale,
    TriStudentScore,
)
from agente_ia_edu.identity import ExternalIdentityContext
from agente_ia_edu.services.mock_exam_evolution import (
    MIXED_SCALES,
    NO_ANCHOR_EQUATING,
    SINGLE_APPLICATION,
    EvolutionPoint,
    decide_evolution_series,
)

SCHOOL_ID = _uuid.uuid4()
STUDENT = "user:aluno_a"
AREA = "MT"


def _point(name: str, scale_id: str, equated: bool, score: float) -> EvolutionPoint:
    return EvolutionPoint(
        mock_exam_id=str(_uuid.uuid4()), mock_exam_name=name,
        application_date="2026-03-10", theta=0.4, scaled_score=score,
        equated=equated, scale_id=scale_id,
    )


class EvolutionSuppressionRule(unittest.TestCase):
    def test_two_equated_points_on_one_scale_are_shown(self) -> None:
        scale = str(_uuid.uuid4())

        series = decide_evolution_series(
            AREA,
            (_point("S1", scale, True, 520.0), _point("S2", scale, True, 574.0)),
            ruler_verified=True,
        )

        self.assertFalse(series.suppressed)
        self.assertIsNone(series.suppression_reason)
        self.assertEqual([p.scaled_score for p in series.points], [520.0, 574.0])

    def test_a_single_application_is_suppressed(self) -> None:
        scale = str(_uuid.uuid4())

        series = decide_evolution_series(
            AREA, (_point("S1", scale, True, 520.0),), ruler_verified=True
        )

        self.assertTrue(series.suppressed)
        self.assertEqual(series.suppression_reason, SINGLE_APPLICATION)
        self.assertIsNone(series.points[0].scaled_score)

    def test_a_non_equated_application_suppresses_the_whole_series(self) -> None:
        scale = str(_uuid.uuid4())

        series = decide_evolution_series(
            AREA,
            (_point("S1", scale, True, 520.0), _point("S2", scale, False, 574.0)),
            ruler_verified=True,
        )

        self.assertTrue(series.suppressed)
        self.assertEqual(series.suppression_reason, NO_ANCHOR_EQUATING)
        self.assertEqual([p.scaled_score for p in series.points], [None, None])
        # o theta continua lá: ele é honesto DENTRO de cada aplicação
        self.assertEqual([p.theta for p in series.points], [0.4, 0.4])

    def test_points_from_two_different_scales_are_suppressed(self) -> None:
        series = decide_evolution_series(
            AREA,
            (
                _point("S1", str(_uuid.uuid4()), True, 520.0),
                _point("S2", str(_uuid.uuid4()), True, 574.0),
            ),
            ruler_verified=True,
        )

        self.assertTrue(series.suppressed)
        self.assertEqual(series.suppression_reason, MIXED_SCALES)
        self.assertEqual([p.scaled_score for p in series.points], [None, None])

    def test_the_school_series_uses_the_very_same_rule(self) -> None:
        """§7: o painel da escola está sujeito à MESMA regra do boletim do aluno.

        Este teste existe para impedir a tentação de relaxar a regra "porque é
        média de muita gente": média de aplicações em réguas diferentes é tão
        incomparável quanto nota de um aluno só, e mais perigosa porque parece
        robusta.
        """
        scale = str(_uuid.uuid4())
        points = (_point("S1", scale, True, 512.0), _point("S2", scale, False, 549.0))

        series = decide_evolution_series(AREA, points, ruler_verified=True)

        self.assertTrue(series.suppressed)
        self.assertEqual(series.suppression_reason, NO_ANCHOR_EQUATING)
        self.assertEqual([p.scaled_score for p in series.points], [None, None])

    def test_no_points_at_all_is_a_suppressed_empty_series(self) -> None:
        series = decide_evolution_series(AREA, (), ruler_verified=False)

        self.assertTrue(series.suppressed)
        self.assertEqual(series.suppression_reason, SINGLE_APPLICATION)
        self.assertEqual(series.points, ())


class EvolutionHTTP(unittest.TestCase):
    def setUp(self) -> None:
        async def setup():
            engine = create_async_engine(
                "sqlite+aiosqlite:///:memory:", poolclass=StaticPool
            )
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(
                engine, class_=AsyncSession, expire_on_commit=False
            )
            now = datetime(2026, 3, 1, tzinfo=timezone.utc)
            async with factory() as session:
                scale_id = _uuid.uuid4()
                base_exam_id = _uuid.uuid4()
                session.add(
                    TriScale(
                        id=scale_id, school_id=SCHOOL_ID, area_code=AREA,
                        name="Régua MT 2026", base_mock_exam_id=base_exam_id,
                        reference_label="ENEM 2025", reference_min_score=312.6,
                        reference_median_score=520.0, reference_max_score=980.3,
                        reference_theta_min=-3.0, reference_theta_max=3.0,
                        reference_source="portal", reference_verified_at=None,
                        created_at=now,
                    )
                )
                # S1 é o simulado BASE (sem item travado, mas é ele que cria a
                # régua); S2 é equalizado (tem item travado).
                for name, exam_id, offset, fixed in (
                    ("Simulado 1", base_exam_id, 0, False),
                    ("Simulado 2", _uuid.uuid4(), 60, True),
                ):
                    session.add(
                        MockExam(
                            id=exam_id, school_id=SCHOOL_ID, academic_year_id=None,
                            name=name,
                            application_date=(now + timedelta(days=offset)).date(),
                            exam_day=1, status="PUBLISHED", created_at=now,
                        )
                    )
                    item_id = _uuid.uuid4()
                    session.add(
                        MockExamItem(
                            id=item_id, mock_exam_id=exam_id, position=1,
                            area_code=AREA, correct_option="A", is_anchor=True,
                            anchor_key="ANC-01", source_question_version_id=None,
                        )
                    )
                    calibration_id = _uuid.uuid4()
                    session.add(
                        TriCalibration(
                            id=calibration_id, mock_exam_id=exam_id, area_code=AREA,
                            scale_id=scale_id, model="2PL", status="DONE",
                            n_examinees=800, n_items=1, converged=True, iterations=30,
                            log_likelihood=-1.0, engine_version="tri-1.0.0",
                            calibrated_at=now + timedelta(days=offset),
                        )
                    )
                    session.add(
                        TriItemParameter(
                            id=_uuid.uuid4(), calibration_id=calibration_id,
                            item_id=item_id, a=1.1, b=0.2, c=None, se_a=0.0, se_b=0.0,
                            n_responses=800, p_value=0.5, point_biserial=0.3,
                            is_fixed=fixed, flags=None,
                        )
                    )
                    session.add(
                        TriStudentScore(
                            id=_uuid.uuid4(), calibration_id=calibration_id,
                            student_id=STUDENT, area_code=AREA,
                            theta=0.2 + 0.3 * offset / 60, theta_se=0.3,
                            scaled_score=520.0 + offset, raw_correct=30,
                            percentile_class=60.0, percentile_school=55.0,
                        )
                    )
                await session.commit()

            self.engine = engine
            self.factory = factory

        asyncio.run(setup())

        app = FastAPI()
        app.include_router(student_router)
        app.dependency_overrides[get_session_factory] = lambda: self.factory
        app.dependency_overrides[get_current_identity] = lambda: ExternalIdentityContext(
            external_user_id=STUDENT, institution_id=str(SCHOOL_ID), classroom_id=None
        )
        self.client = TestClient(app)

    def tearDown(self) -> None:
        self.client.close()
        asyncio.run(self.engine.dispose())

    def test_the_base_exam_plus_an_equated_one_produce_a_visible_series(self) -> None:
        response = self.client.get("/api/v1/student/mock-exams/evolution")

        self.assertEqual(response.status_code, 200, response.text)
        series = response.json()["series"]
        self.assertEqual(len(series), 1)
        self.assertFalse(series[0]["suppressed"])
        self.assertEqual(
            [point["scaled_score"] for point in series[0]["points"]], [520.0, 580.0]
        )
        self.assertFalse(series[0]["ruler_verified"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `.venv/bin/python -m pytest tests/test_mock_exam_evolution.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.services.mock_exam_evolution'`

- [ ] **Step 3: Escrever o serviço**

Criar `src/agente_ia_edu/services/mock_exam_evolution.py`:

```python
"""Evolução em simulados ao longo do ano (spec §7).

"A evolução por nota absoluta só é exibida quando existe equalização por
âncoras; sem elas o gráfico é SUPRIMIDO, não estimado — thetas de calibragens
independentes estão em réguas diferentes e compará-los seria um erro
silencioso."

A supressão é aplicada NA ORIGEM: a série suprimida sai com `scaled_score`
nulo em todos os pontos. Assim nenhum consumidor — este frontend, um export
futuro, um relatório de coordenação — consegue plotar a linha por engano. O
theta continua no payload porque ele é honesto DENTRO de cada aplicação; o que
não é honesto é a comparação entre elas.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, replace
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    MockExam,
    TriCalibration,
    TriItemParameter,
    TriScale,
    TriStudentScore,
)

SINGLE_APPLICATION = "SINGLE_APPLICATION"
MIXED_SCALES = "MIXED_SCALES"
NO_ANCHOR_EQUATING = "NO_ANCHOR_EQUATING"


@dataclass(frozen=True)
class EvolutionPoint:
    mock_exam_id: str
    mock_exam_name: str
    application_date: str
    theta: float
    scaled_score: float | None
    equated: bool
    scale_id: str


@dataclass(frozen=True)
class EvolutionSeries:
    area_code: str
    points: tuple[EvolutionPoint, ...]
    suppressed: bool
    suppression_reason: str | None
    ruler_verified: bool


def decide_evolution_series(
    area_code: str,
    points: Sequence[EvolutionPoint],
    *,
    ruler_verified: bool,
) -> EvolutionSeries:
    """A regra, pura e sem banco.

    Três razões para suprimir, nesta ordem:
      SINGLE_APPLICATION — menos de duas aplicações: não há comparação a fazer.
      MIXED_SCALES       — pontos de réguas diferentes: escalas incomparáveis.
      NO_ANCHOR_EQUATING — alguma aplicação não foi equalizada por âncoras.

    Suprimir significa zerar `scaled_score` de TODOS os pontos, e não apenas
    levantar uma bandeira que o consumidor pode ignorar.
    """
    ordered = tuple(sorted(points, key=lambda point: point.application_date))

    reason: str | None = None
    if len(ordered) < 2:
        reason = SINGLE_APPLICATION
    elif len({point.scale_id for point in ordered}) > 1:
        reason = MIXED_SCALES
    elif not all(point.equated for point in ordered):
        reason = NO_ANCHOR_EQUATING

    if reason is None:
        return EvolutionSeries(
            area_code=area_code, points=ordered, suppressed=False,
            suppression_reason=None, ruler_verified=ruler_verified,
        )
    return EvolutionSeries(
        area_code=area_code,
        points=tuple(replace(point, scaled_score=None) for point in ordered),
        suppressed=True,
        suppression_reason=reason,
        ruler_verified=ruler_verified,
    )


async def build_student_mock_exam_evolution(
    session: AsyncSession, *, school_id: uuid.UUID, student_id: str
) -> tuple[EvolutionSeries, ...]:
    """Monta uma série por área, aplicando a regra acima a cada uma."""
    rows = (
        await session.execute(
            select(TriStudentScore, TriCalibration, MockExam, TriScale)
            .join(TriCalibration, TriCalibration.id == TriStudentScore.calibration_id)
            .join(MockExam, MockExam.id == TriCalibration.mock_exam_id)
            .join(TriScale, TriScale.id == TriCalibration.scale_id)
            .where(
                TriStudentScore.student_id == student_id,
                MockExam.school_id == school_id,
                MockExam.status == "PUBLISHED",
            )
            .order_by(MockExam.application_date.asc())
        )
    ).all()
    if not rows:
        return ()

    calibration_ids = [calibration.id for _score, calibration, _exam, _scale in rows]
    equated_ids = set(
        (
            await session.execute(
                select(TriItemParameter.calibration_id)
                .where(
                    TriItemParameter.calibration_id.in_(calibration_ids),
                    TriItemParameter.is_fixed.is_(True),
                )
                .distinct()
            )
        ).scalars().all()
    )

    by_area: dict[str, list[EvolutionPoint]] = {}
    verified_by_area: dict[str, bool] = {}
    for score, calibration, exam, scale in rows:
        # A primeira aplicação da régua não tem o que travar: é ela que cria a
        # escala. Contá-la como equalizada é o que permite a série existir a
        # partir do SEGUNDO simulado, em vez de nunca.
        is_base = str(scale.base_mock_exam_id or "") == str(exam.id)
        by_area.setdefault(score.area_code, []).append(
            EvolutionPoint(
                mock_exam_id=str(exam.id),
                mock_exam_name=exam.name,
                application_date=exam.application_date.isoformat(),
                theta=float(score.theta),
                scaled_score=(
                    float(score.scaled_score) if score.scaled_score is not None else None
                ),
                equated=is_base or calibration.id in equated_ids,
                scale_id=str(scale.id),
            )
        )
        verified_by_area[score.area_code] = scale.reference_verified_at is not None

    return tuple(
        decide_evolution_series(
            area_code, points, ruler_verified=verified_by_area.get(area_code, False)
        )
        for area_code, points in sorted(by_area.items())
    )


async def build_school_mock_exam_evolution(
    session: AsyncSession, *, school_id: uuid.UUID
) -> tuple[EvolutionSeries, ...]:
    """Evolução por aplicação no painel da escola (§7).

    A média da escola em cada aplicação, por área — e submetida à MESMA regra de
    supressão do boletim do aluno, via `decide_evolution_series`. Não existe uma
    segunda regra, mais frouxa, para dados agregados: a média de duas aplicações
    calibradas em réguas diferentes é tão incomparável quanto a nota de um aluno
    só, e mais perigosa porque parece robusta.
    """
    rows = (
        await session.execute(
            select(
                TriStudentScore.area_code,
                TriCalibration.id,
                MockExam.id,
                MockExam.name,
                MockExam.application_date,
                TriScale.id,
                TriScale.base_mock_exam_id,
                TriScale.reference_verified_at,
                func.avg(TriStudentScore.scaled_score),
                func.avg(TriStudentScore.theta),
            )
            .join(TriCalibration, TriCalibration.id == TriStudentScore.calibration_id)
            .join(MockExam, MockExam.id == TriCalibration.mock_exam_id)
            .join(TriScale, TriScale.id == TriCalibration.scale_id)
            .where(MockExam.school_id == school_id, MockExam.status == "PUBLISHED")
            .group_by(
                TriStudentScore.area_code,
                TriCalibration.id,
                MockExam.id,
                MockExam.name,
                MockExam.application_date,
                TriScale.id,
                TriScale.base_mock_exam_id,
                TriScale.reference_verified_at,
            )
            .order_by(MockExam.application_date.asc())
        )
    ).all()
    if not rows:
        return ()

    calibration_ids = [row[1] for row in rows]
    equated_ids = set(
        (
            await session.execute(
                select(TriItemParameter.calibration_id)
                .where(
                    TriItemParameter.calibration_id.in_(calibration_ids),
                    TriItemParameter.is_fixed.is_(True),
                )
                .distinct()
            )
        ).scalars().all()
    )

    by_area: dict[str, list[EvolutionPoint]] = {}
    verified_by_area: dict[str, bool] = {}
    for (
        area_code, calibration_id, exam_id, exam_name, application_date,
        scale_id, base_mock_exam_id, verified_at, mean_score, mean_theta,
    ) in rows:
        by_area.setdefault(area_code, []).append(
            EvolutionPoint(
                mock_exam_id=str(exam_id),
                mock_exam_name=exam_name,
                application_date=application_date.isoformat(),
                theta=float(mean_theta),
                scaled_score=None if mean_score is None else float(mean_score),
                equated=(
                    str(base_mock_exam_id or "") == str(exam_id)
                    or calibration_id in equated_ids
                ),
                scale_id=str(scale_id),
            )
        )
        verified_by_area[area_code] = verified_at is not None

    return tuple(
        decide_evolution_series(
            area_code, points, ruler_verified=verified_by_area.get(area_code, False)
        )
        for area_code, points in sorted(by_area.items())
    )
```

- [ ] **Step 4: Escrever o schema e a rota**

Acrescentar ao final de `src/agente_ia_edu/api/schemas/student.py`:

```python
class StudentMockExamEvolutionPoint(BaseModel):
    mock_exam_id: str
    mock_exam_name: str
    application_date: str
    theta: float
    scaled_score: Optional[float] = None


class StudentMockExamEvolutionSeries(BaseModel):
    area_code: str
    suppressed: bool
    suppression_reason: Optional[str] = None
    ruler_verified: bool = False
    points: list[StudentMockExamEvolutionPoint] = Field(default_factory=list)


class StudentMockExamEvolutionResponse(BaseModel):
    series: list[StudentMockExamEvolutionSeries] = Field(default_factory=list)
```

Acrescentar ao final de `src/agente_ia_edu/api/routes/student.py` (e os nomes novos ao
import de `..schemas.student`, mais
`from ...services.mock_exam_evolution import build_student_mock_exam_evolution`):

```python
@student_router.get(
    "/mock-exams/evolution",
    response_model=StudentMockExamEvolutionResponse,
    summary="Mock-exam score evolution over the year, suppressed when not equated",
)
async def get_student_mock_exam_evolution(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> StudentMockExamEvolutionResponse:
    async with session_factory() as session:
        series = await build_student_mock_exam_evolution(
            session,
            school_id=_UUID(identity.institution_id),
            student_id=identity.external_user_id,
        )
    return StudentMockExamEvolutionResponse(
        series=[
            StudentMockExamEvolutionSeries(
                area_code=item.area_code,
                suppressed=item.suppressed,
                suppression_reason=item.suppression_reason,
                ruler_verified=item.ruler_verified,
                points=[
                    StudentMockExamEvolutionPoint(
                        mock_exam_id=point.mock_exam_id,
                        mock_exam_name=point.mock_exam_name,
                        application_date=point.application_date,
                        theta=point.theta,
                        scaled_score=point.scaled_score,
                    )
                    for point in item.points
                ],
            )
            for item in series
        ]
    )
```

- [ ] **Step 5: Escrever o schema e a rota da escola**

Acrescentar ao final de `src/agente_ia_edu/api/schemas/coordination_portal.py`:

```python
class SchoolMockExamEvolutionPoint(BaseModel):
    mock_exam_id: str
    mock_exam_name: str
    application_date: str
    mean_theta: float
    mean_scaled_score: Optional[float] = None


class SchoolMockExamEvolutionSeries(BaseModel):
    area_code: str
    suppressed: bool
    suppression_reason: Optional[str] = None
    ruler_verified: bool = False
    points: list[SchoolMockExamEvolutionPoint] = Field(default_factory=list)


class SchoolMockExamEvolutionResponse(BaseModel):
    school_id: str
    series: list[SchoolMockExamEvolutionSeries] = Field(default_factory=list)
```

Acrescentar ao final de `src/agente_ia_edu/api/routes/coordination_portal.py` (e os nomes
novos ao import de `..schemas.coordination_portal`, mais
`from ...services.mock_exam_evolution import build_school_mock_exam_evolution`):

```python
@coordination_portal_router.get(
    "/mock-exams/evolution",
    response_model=SchoolMockExamEvolutionResponse,
    summary="School mock-exam evolution, suppressed when applications are not equated",
)
async def get_school_mock_exam_evolution(
    school_id: UUID = Query(...),
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> SchoolMockExamEvolutionResponse:
    requester = _requester(ctx)
    if not requester.is_platform_admin:
        if not requester.is_privileged:
            raise HTTPException(
                status_code=403, detail="only a coordinator may read school evolution"
            )
        if not requester.school_id or str(school_id) != str(requester.school_id):
            raise HTTPException(
                status_code=403, detail="school is outside this coordinator's scope"
            )
    async with session_factory() as session:
        series = await build_school_mock_exam_evolution(session, school_id=school_id)
    return SchoolMockExamEvolutionResponse(
        school_id=str(school_id),
        series=[
            SchoolMockExamEvolutionSeries(
                area_code=item.area_code,
                suppressed=item.suppressed,
                suppression_reason=item.suppression_reason,
                ruler_verified=item.ruler_verified,
                points=[
                    SchoolMockExamEvolutionPoint(
                        mock_exam_id=point.mock_exam_id,
                        mock_exam_name=point.mock_exam_name,
                        application_date=point.application_date,
                        mean_theta=point.theta,
                        mean_scaled_score=point.scaled_score,
                    )
                    for point in item.points
                ],
            )
            for item in series
        ],
    )
```

- [ ] **Step 6: Escrever o teste HTTP da rota da escola**

Acrescentar a `tests/test_mock_exam_evolution.py`, dentro da classe `EvolutionHTTP`, um
segundo app montado sobre o router de coordenação. No `setUp`, depois de montar o app do
aluno:

```python
        from agente_ia_edu.api.dependencies import get_current_authenticated_context
        from agente_ia_edu.api.routes.coordination_portal import coordination_portal_router
        from agente_ia_edu.identity import AuthenticatedUserContext

        coordination_app = FastAPI()
        coordination_app.include_router(coordination_portal_router)
        coordination_app.dependency_overrides[get_session_factory] = lambda: self.factory
        coordination_app.dependency_overrides[get_current_authenticated_context] = (
            lambda: AuthenticatedUserContext(
                user_id="user:coord_a", external_identity_id="user:coord_a",
                school_id=str(SCHOOL_ID), role="COORDINATOR",
            )
        )
        self.coordination_client = TestClient(coordination_app)
```

e o teste:

```python
    def test_the_school_panel_shows_the_series_under_the_same_rule(self) -> None:
        response = self.coordination_client.get(
            f"/api/v1/coordination/mock-exams/evolution?school_id={SCHOOL_ID}"
        )

        self.assertEqual(response.status_code, 200, response.text)
        series = response.json()["series"]
        self.assertEqual(len(series), 1)
        self.assertFalse(series[0]["suppressed"])
        self.assertEqual(
            [point["mean_scaled_score"] for point in series[0]["points"]], [520.0, 580.0]
        )

    def test_a_coordinator_from_another_school_is_refused(self) -> None:
        response = self.coordination_client.get(
            f"/api/v1/coordination/mock-exams/evolution?school_id={_uuid.uuid4()}"
        )

        self.assertEqual(response.status_code, 403, response.text)
```

Acrescentar `self.coordination_client.close()` ao `tearDown`.

- [ ] **Step 7: Rodar o teste e ver passar**

Run: `.venv/bin/python -m pytest tests/test_mock_exam_evolution.py -v`
Expected: PASS (10 testes)

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/services/mock_exam_evolution.py \
        src/agente_ia_edu/api/schemas/student.py \
        src/agente_ia_edu/api/routes/student.py \
        src/agente_ia_edu/api/schemas/coordination_portal.py \
        src/agente_ia_edu/api/routes/coordination_portal.py \
        tests/test_mock_exam_evolution.py
git commit -m "feat(simulados): evolucao no ano suprimida quando nao ha equalizacao

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 8: Frontend da evolução em simulados, com a supressão explicada

**Files:**
- Modify: `src/agente_ia_edu/web/index.html` (598 linhas; seção `view-evolution` em `:571-577`)
- Modify: `src/agente_ia_edu/web/app.js` (2588 linhas; `loadEvolutionData` em `:309-322`,
  `studentRequest`, `switchView`)
- Modify: `src/agente_ia_edu/web/styles.css` (acrescentar ao final)
- Create: `tests/test_mock_exam_evolution_frontend.js`

**Interfaces:**
- Consumes (Task 7): `GET /api/v1/student/mock-exams/evolution`, com o payload de
  `StudentMockExamEvolutionResponse` (`series[].suppressed`,
  `series[].suppression_reason`, `series[].ruler_verified`,
  `series[].points[].scaled_score`).
- Produces: em `app.js`, `loadMockExamEvolution()`, `renderMockExamEvolution(series)`,
  `MOCK_EXAM_SUPPRESSION_NOTICE`; em `index.html`, `#mock-exam-evolution-container`.

**Regra de UI, registrada:** a linha de nota só é desenhada quando `suppressed` é falso.
Série suprimida mostra as aplicações e o aviso correspondente à razão — nunca uma linha
pontilhada, nunca um "estimado", nunca um zero no lugar do ponto. E a nota sai sempre
rotulada como **escala de referência da escola**, nunca como previsão de nota do ENEM
(§6.3); com `ruler_verified` falso, sai marcada como provisória.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/test_mock_exam_evolution_frontend.js`:

```javascript
/**
 * Fase 5 (âncoras e equalização, 2026-09-29) — bloco "Evolução nos simulados"
 * dentro da aba "Minha Evolução" do SPA do aluno.
 *
 * Asserções estáticas sobre o SPA entregue (node:test + regex), no mesmo
 * estilo de tests/test_material_assignment_student_materials_view_frontend.js.
 *
 * O que protege: a linha de nota NÃO é desenhada quando a série vem
 * suprimida, e a supressão é explicada em vez de escondida. Um gráfico
 * plotado sobre réguas diferentes é o erro silencioso que a Fase 5 inteira
 * existe para evitar — ele não pode reaparecer no navegador.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const appJs = fs.readFileSync(path.join(WEB, 'app.js'), 'utf8');
const indexHtml = fs.readFileSync(path.join(WEB, 'index.html'), 'utf8');

// Ancorar no comentário de abertura do bloco, e não numa função, para que o
// mapa de mensagens de supressão e os dois renderizadores entrem nas
// asserções junto com o loader.
const _start = appJs.indexOf('// Fase 5 — evolução nos simulados');
const _end = appJs.indexOf('// 4. Materials View');
const BLOCK = appJs.slice(_start, _end > _start ? _end : undefined);

test('the mock-exam evolution block exists and is substantial', () => {
  assert.ok(_start >= 0, 'the Fase 5 mock-exam evolution block should be present');
  assert.match(BLOCK, /async function loadMockExamEvolution\(\)/);
  assert.match(BLOCK, /function renderMockExamSeries\(/);
  assert.ok(BLOCK.length > 400, 'block should be present');
});

test('the container exists in the evolution view and is filled from it', () => {
  assert.match(indexHtml, /id="mock-exam-evolution-container"/);
  assert.match(appJs, /loadMockExamEvolution\(\);/);
});

test('it fetches the real endpoint through studentRequest', () => {
  assert.match(BLOCK, /studentRequest\('\/api\/v1\/student\/mock-exams\/evolution'\)/);
});

test('the score line is drawn only when the series is not suppressed', () => {
  assert.match(BLOCK, /if \(series\.suppressed\)/);
  assert.match(BLOCK, /MOCK_EXAM_SUPPRESSION_NOTICE\[series\.suppression_reason\]/);
});

test('a suppressed series never plots a scaled_score', () => {
  const suppressedBranch = BLOCK.match(/if \(series\.suppressed\) \{([\s\S]*?)\n    \}/);
  assert.ok(suppressedBranch, 'suppressed branch should exist');
  assert.doesNotMatch(suppressedBranch[1], /scaled_score/);
});

test('all three suppression reasons have their own honest message', () => {
  assert.match(appJs, /NO_ANCHOR_EQUATING:/);
  assert.match(appJs, /MIXED_SCALES:/);
  assert.match(appJs, /SINGLE_APPLICATION:/);
  assert.match(appJs, /réguas diferentes/);
});

test('the score is labelled as the school reference scale, never an ENEM forecast', () => {
  assert.match(BLOCK, /escala de referência da escola/);
  assert.doesNotMatch(BLOCK, /previsão de nota do ENEM/);
});

test('an unverified ruler is shown as provisional', () => {
  assert.match(BLOCK, /series\.ruler_verified/);
  assert.match(BLOCK, /provisóri/);
});
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `node --test tests/test_mock_exam_evolution_frontend.js`
Expected: FAIL — `loadMockExamEvolution should be present`

- [ ] **Step 3: Escrever o HTML**

Em `src/agente_ia_edu/web/index.html`, dentro de `<section id="view-evolution">`, logo
depois de `<div id="evolution-stats-container" ...></div>` (`:575`):

```html
          <div id="mock-exam-evolution-container" class="evolution-body" aria-live="polite"></div>
```

- [ ] **Step 4: Escrever o JS**

Em `src/agente_ia_edu/web/app.js`:

1. acrescentar a chamada ao final de `loadEvolutionData` (`:309-322`), depois do
   `try/catch` existente:

```javascript
    loadMockExamEvolution();
```

2. acrescentar, imediatamente antes do comentário `// 4. Materials View` (`:334`):

```javascript
  // Fase 5 — evolução nos simulados (spec §7). A supressão NÃO é decidida
  // aqui: a API já devolve `suppressed` e zera `scaled_score` na origem. Este
  // bloco só explica ao aluno por que a linha não está lá.
  const MOCK_EXAM_SUPPRESSION_NOTICE = {
    NO_ANCHOR_EQUATING: 'Ainda não dá para comparar as notas destes simulados: eles não têm questões-âncora em comum, então cada um foi calibrado na sua própria régua e os números não são comparáveis entre si.',
    MIXED_SCALES: 'Estes simulados foram calibrados em réguas diferentes. Notas de réguas diferentes não podem ser colocadas no mesmo gráfico.',
    SINGLE_APPLICATION: 'Você tem só um simulado corrigido até agora. A evolução aparece a partir do segundo.',
  };

  function evolutionEsc(value) {
    return String(value == null ? '' : value).replace(/[&<>"']/g, (c) => (
      { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]
    ));
  }

  function renderMockExamEvolution(allSeries) {
    const container = document.getElementById('mock-exam-evolution-container');
    if (!allSeries.length) {
      container.innerHTML = '';
      return;
    }
    container.innerHTML = `
      <div class="card">
        <div class="card-header"><h3>Evolução nos simulados</h3></div>
        ${allSeries.map((series) => renderMockExamSeries(series)).join('')}
      </div>
    `;
  }

  function renderMockExamSeries(series) {
    const header = `<h4 class="mock-exam-area">${evolutionEsc(series.area_code)}</h4>`;
    if (series.suppressed) {
      const notice = MOCK_EXAM_SUPPRESSION_NOTICE[series.suppression_reason]
        || 'A comparação de notas entre estes simulados não está disponível.';
      const applications = series.points
        .map((p) => `<li>${evolutionEsc(p.mock_exam_name)} — ${evolutionEsc(p.application_date)}</li>`)
        .join('');
      return `
        <div class="mock-exam-series mock-exam-series-suppressed">
          ${header}
          <p class="mock-exam-notice">${evolutionEsc(notice)}</p>
          ${applications ? `<ul class="mock-exam-applications">${applications}</ul>` : ''}
        </div>
      `;
    }
    const scores = series.points.map((p) => Number(p.scaled_score));
    const top = Math.max(...scores, 1);
    const bars = series.points.map((p, index) => `
      <li class="mock-exam-bar-row">
        <span class="mock-exam-bar-label">${evolutionEsc(p.mock_exam_name)}</span>
        <span class="mock-exam-bar" style="width:${Math.round((scores[index] / top) * 100)}%"></span>
        <span class="mock-exam-bar-value">${scores[index].toFixed(1)}</span>
      </li>
    `).join('');
    const provisional = series.ruler_verified
      ? ''
      : '<p class="mock-exam-notice">Resultado provisório: a régua desta área ainda não foi conferida pela coordenação.</p>';
    return `
      <div class="mock-exam-series">
        ${header}
        <p class="mock-exam-scale-label">Nota na escala de referência da escola, construída para ser legível na faixa do ENEM.</p>
        ${provisional}
        <ul class="mock-exam-bars">${bars}</ul>
      </div>
    `;
  }

  async function loadMockExamEvolution() {
    const container = document.getElementById('mock-exam-evolution-container');
    if (!container) return;
    try {
      const res = await studentRequest('/api/v1/student/mock-exams/evolution');
      if (!res.ok) throw new Error('Falha ao carregar evolução em simulados');
      const data = await res.json();
      renderMockExamEvolution(data.series || []);
    } catch (err) {
      container.innerHTML = '<p class="empty-text">Não foi possível carregar a evolução nos simulados agora.</p>';
    }
  }
```

- [ ] **Step 5: Escrever o CSS**

Acrescentar ao final de `src/agente_ia_edu/web/styles.css`:

```css
/* Fase 5 — evolução nos simulados */
.mock-exam-area { margin: 12px 0 4px; font-size: 15px; }
.mock-exam-scale-label { font-size: 12px; color: #666; margin: 0 0 8px; }
.mock-exam-notice { font-size: 13px; color: #8a6d00; background: #fff8e1; padding: 8px 10px; border-radius: 6px; }
.mock-exam-bars, .mock-exam-applications { list-style: none; padding: 0; margin: 0; }
.mock-exam-bar-row { display: grid; grid-template-columns: 140px 1fr 70px; gap: 8px; align-items: center; margin-bottom: 6px; }
.mock-exam-bar { display: inline-block; height: 12px; border-radius: 6px; background: #3f7fd0; }
.mock-exam-bar-label, .mock-exam-bar-value { font-size: 13px; }
.mock-exam-series-suppressed { opacity: 0.95; }
```

- [ ] **Step 6: Rodar o teste e ver passar**

Run: `node --test tests/test_mock_exam_evolution_frontend.js`
Expected: PASS (8 testes)

- [ ] **Step 7: Rodar tudo o que a Fase 5 criou**

```bash
ps aux | grep pytest   # esperar qualquer execução concorrente terminar
.venv/bin/python -m pytest tests/test_tri_equating_population.py tests/test_tri_anchor_drift.py \
       tests/test_tri_equating_invariance.py tests/test_mock_exam_equating_service.py \
       tests/test_mock_exam_anchors_http.py tests/test_mock_exam_anchor_health_http.py \
       tests/test_mock_exam_answer_key_release.py tests/test_mock_exam_evolution.py -v
node --test tests/test_mock_exam_anchor_health_frontend.js
node --test tests/test_mock_exam_evolution_frontend.js
```
Expected: todos PASS

- [ ] **Step 8: Rodar a suíte completa**

Run: `.venv/bin/python -m pytest -q`
Expected: nenhuma falha nova em relação ao estado anterior à Fase 5

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/web/index.html src/agente_ia_edu/web/app.js \
        src/agente_ia_edu/web/styles.css tests/test_mock_exam_evolution_frontend.js
git commit -m "feat(simulados): evolucao nos simulados no SPA do aluno, com supressao explicada

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Task 9: Evolução por aplicação no painel da escola, com a mesma supressão

**Files:**
- Modify: `src/agente_ia_edu/web/coordination.html` (painel `view-anchors` criado na Task 6;
  acrescentar um card novo dentro dele)
- Modify: `src/agente_ia_edu/web/coordination.js` (`loadAnchorHealth` e `coordEsc`, da Task 6)
- Modify: `src/agente_ia_edu/web/coordination.css` (acrescentar ao final)
- Create: `tests/test_mock_exam_school_evolution_frontend.js`

**Interfaces:**
- Consumes (Task 7): `GET /api/v1/coordination/mock-exams/evolution?school_id=`, com o
  payload de `SchoolMockExamEvolutionResponse` (`series[].suppressed`,
  `series[].suppression_reason`, `series[].ruler_verified`,
  `series[].points[].mean_scaled_score`). Da Task 6: `coordEsc`, `state.schoolId`,
  `state.coordinatorId`, a view `anchors` e `initAnchorHealthView`.
- Produces: em `coordination.js`, `SCHOOL_EVOLUTION_NOTICE`,
  `loadSchoolMockExamEvolution()`, `renderSchoolMockExamEvolution(series)`; em
  `coordination.html`, `#school-evolution-container`.

**Por que mora junto das âncoras, e não num painel novo:** a pergunta que o gráfico responde
("a escola melhorou?") e a pergunta que o painel de âncoras responde ("dá para comparar?")
são a mesma pergunta em duas metades. Pôr as duas na mesma tela faz a coordenação ver *por
que* a linha sumiu, em vez de abrir um chamado perguntando.

- [ ] **Step 1: Escrever o teste que falha**

Criar `tests/test_mock_exam_school_evolution_frontend.js`:

```javascript
/**
 * Fase 5 (âncoras e equalização, 2026-09-29) — evolução por aplicação no painel
 * da ESCOLA, spec §7.
 *
 * Asserções estáticas sobre o SPA entregue (node:test + regex), no mesmo estilo
 * de tests/test_material_assignment_student_materials_view_frontend.js.
 *
 * O que protege: a regra de supressão vale igual para a escola. Uma média de
 * escola plotada sobre réguas diferentes é MAIS perigosa que a nota de um aluno
 * só, porque parece robusta — e é exatamente por parecer robusta que ninguém
 * questiona.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const coordinationJs = fs.readFileSync(path.join(WEB, 'coordination.js'), 'utf8');
const coordinationHtml = fs.readFileSync(path.join(WEB, 'coordination.html'), 'utf8');

const _start = coordinationJs.indexOf('// Fase 5 — evolução por aplicação na escola');
const _end = coordinationJs.indexOf('function coordEsc(');
const BLOCK = coordinationJs.slice(_start, _end > _start ? _end : undefined);

test('the school evolution block exists and is substantial', () => {
  assert.ok(_start >= 0, 'the Fase 5 school evolution block should be present');
  assert.match(BLOCK, /async function loadSchoolMockExamEvolution\(\)/);
  assert.match(BLOCK, /function renderSchoolMockExamEvolution\(/);
  assert.ok(BLOCK.length > 400, 'block should be present');
});

test('the container exists and is filled when the anchors view opens', () => {
  assert.match(coordinationHtml, /id="school-evolution-container"/);
  assert.match(coordinationJs, /loadSchoolMockExamEvolution\(\);/);
});

test('it calls the real school evolution endpoint, never mocked data', () => {
  assert.match(BLOCK, /\/api\/v1\/coordination\/mock-exams\/evolution\?school_id=/);
});

test('the score line is drawn only when the series is not suppressed', () => {
  assert.match(BLOCK, /if \(series\.suppressed\)/);
  assert.match(BLOCK, /SCHOOL_EVOLUTION_NOTICE\[series\.suppression_reason\]/);
});

test('a suppressed school series never plots a mean score', () => {
  const suppressedBranch = BLOCK.match(/if \(series\.suppressed\) \{([\s\S]*?)\n    \}/);
  assert.ok(suppressedBranch, 'suppressed branch should exist');
  assert.doesNotMatch(suppressedBranch[1], /mean_scaled_score/);
});

test('all three suppression reasons have their own honest message', () => {
  assert.match(coordinationJs, /NO_ANCHOR_EQUATING:/);
  assert.match(coordinationJs, /MIXED_SCALES:/);
  assert.match(coordinationJs, /SINGLE_APPLICATION:/);
  assert.match(coordinationJs, /réguas diferentes/);
});

test('the suppression message says why, not just that it is unavailable', () => {
  assert.match(coordinationJs, /mais difícil/);
});

test('the score is labelled as the school reference scale, never an ENEM forecast', () => {
  assert.match(BLOCK, /escala de referência da escola/);
  assert.doesNotMatch(BLOCK, /previsão de nota do ENEM/);
});

test('an unverified ruler is shown as provisional', () => {
  assert.match(BLOCK, /series\.ruler_verified/);
  assert.match(BLOCK, /provisóri/);
});
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `node --test tests/test_mock_exam_school_evolution_frontend.js`
Expected: FAIL — `the Fase 5 school evolution block should be present`

- [ ] **Step 3: Escrever o HTML**

Em `src/agente_ia_edu/web/coordination.html`, dentro de `<section id="view-anchors">`,
depois do card "Situação das âncoras" criado na Task 6:

```html
          <div class="card">
            <div class="card-header">
              <h3>Evolução por aplicação</h3>
            </div>
            <div id="school-evolution-container" aria-live="polite"></div>
          </div>
```

- [ ] **Step 4: Escrever o JS**

Em `src/agente_ia_edu/web/coordination.js`:

1. em `initAnchorHealthView()` (Task 6), acrescentar a chamada ao final:

```javascript
    loadSchoolMockExamEvolution();
```

2. acrescentar, imediatamente antes de `function coordEsc(`:

```javascript
  // Fase 5 — evolução por aplicação na escola (spec §7). A supressão NÃO é
  // decidida aqui: a API já devolve `suppressed` e zera `mean_scaled_score` na
  // origem, pela MESMA regra do boletim do aluno. Este bloco só explica por que
  // a linha não está lá.
  const SCHOOL_EVOLUTION_NOTICE = {
    NO_ANCHOR_EQUATING: 'Estas aplicações não têm questões-âncora em comum, então cada uma foi calibrada na sua própria régua. Comparar as médias não distinguiria "a escola piorou" de "a prova era mais difícil".',
    MIXED_SCALES: 'Estas aplicações foram calibradas em réguas diferentes. Médias de réguas diferentes não podem ser colocadas no mesmo gráfico.',
    SINGLE_APPLICATION: 'Há só uma aplicação corrigida nesta área. A evolução aparece a partir da segunda.',
  };

  async function loadSchoolMockExamEvolution() {
    const container = document.getElementById('school-evolution-container');
    if (!container) return;
    container.innerHTML = '<p class="empty-text">Carregando evolução...</p>';
    try {
      const res = await fetch(
        `/api/v1/coordination/mock-exams/evolution?school_id=${encodeURIComponent(state.schoolId)}`,
        { headers: { 'Authorization': `Bearer ${state.coordinatorId}` } }
      );
      if (res.status === 403) {
        container.innerHTML = '<p class="empty-text text-danger">⚠️ Você não possui permissão para esta escola.</p>';
        return;
      }
      if (!res.ok) throw new Error('Falha ao carregar evolução');
      const data = await res.json();
      renderSchoolMockExamEvolution(data.series || []);
    } catch (err) {
      console.warn('School evolution error:', err);
      container.innerHTML = '<p class="empty-text">Não foi possível carregar a evolução agora.</p>';
    }
  }

  function renderSchoolMockExamEvolution(allSeries) {
    const container = document.getElementById('school-evolution-container');
    if (!allSeries.length) {
      container.innerHTML = '<p class="empty-text">Nenhum simulado publicado com nota TRI ainda.</p>';
      return;
    }
    container.innerHTML = allSeries.map((series) => renderSchoolSeries(series)).join('');
  }

  function renderSchoolSeries(series) {
    const header = `<h4 class="school-evolution-area">${coordEsc(series.area_code)}</h4>`;
    if (series.suppressed) {
      const notice = SCHOOL_EVOLUTION_NOTICE[series.suppression_reason]
        || 'A comparação entre estas aplicações não está disponível.';
      const applications = series.points
        .map((p) => `<li>${coordEsc(p.mock_exam_name)} — ${coordEsc(p.application_date)}</li>`)
        .join('');
      return `
        <div class="school-evolution-series school-evolution-suppressed">
          ${header}
          <p class="school-evolution-notice">${coordEsc(notice)}</p>
          ${applications ? `<ul class="school-evolution-applications">${applications}</ul>` : ''}
        </div>
      `;
    }
    const scores = series.points.map((p) => Number(p.mean_scaled_score));
    const top = Math.max(...scores, 1);
    const bars = series.points.map((p, index) => `
      <li class="school-evolution-row">
        <span class="school-evolution-label">${coordEsc(p.mock_exam_name)}</span>
        <span class="school-evolution-bar" style="width:${Math.round((scores[index] / top) * 100)}%"></span>
        <span class="school-evolution-value">${scores[index].toFixed(1)}</span>
      </li>
    `).join('');
    const provisional = series.ruler_verified
      ? ''
      : '<p class="school-evolution-notice">Resultado provisório: a régua desta área ainda não foi conferida pela coordenação.</p>';
    return `
      <div class="school-evolution-series">
        ${header}
        <p class="school-evolution-scale-label">Média da escola na escala de referência da escola, construída para ser legível na faixa do ENEM.</p>
        ${provisional}
        <ul class="school-evolution-bars">${bars}</ul>
      </div>
    `;
  }
```

- [ ] **Step 5: Escrever o CSS**

Acrescentar ao final de `src/agente_ia_edu/web/coordination.css`:

```css
/* Fase 5 — evolução por aplicação na escola */
.school-evolution-area { margin: 12px 0 4px; font-size: 15px; }
.school-evolution-scale-label { font-size: 12px; color: #666; margin: 0 0 8px; }
.school-evolution-notice { font-size: 13px; color: #8a6d00; background: #fff8e1; padding: 8px 10px; border-radius: 6px; }
.school-evolution-bars, .school-evolution-applications { list-style: none; padding: 0; margin: 0; }
.school-evolution-row { display: grid; grid-template-columns: 160px 1fr 70px; gap: 8px; align-items: center; margin-bottom: 6px; }
.school-evolution-bar { display: inline-block; height: 12px; border-radius: 6px; background: #3f7fd0; }
.school-evolution-label, .school-evolution-value { font-size: 13px; }
```

- [ ] **Step 6: Rodar o teste e ver passar**

Run: `node --test tests/test_mock_exam_school_evolution_frontend.js`
Expected: PASS (9 testes)

- [ ] **Step 7: Rodar tudo o que a Fase 5 criou**

```bash
ps aux | grep pytest   # esperar qualquer execução concorrente terminar
.venv/bin/python -m pytest tests/test_tri_anchor_drift.py \
       tests/test_tri_equating_invariance.py tests/test_mock_exam_equating_service.py \
       tests/test_mock_exam_anchors_http.py tests/test_mock_exam_anchor_health_http.py \
       tests/test_mock_exam_answer_key_release.py tests/test_mock_exam_evolution.py -v
node --test tests/test_mock_exam_anchor_health_frontend.js
node --test tests/test_mock_exam_evolution_frontend.js
node --test tests/test_mock_exam_school_evolution_frontend.js
```
Expected: todos PASS

- [ ] **Step 8: Rodar a suíte completa**

Run: `.venv/bin/python -m pytest -q`
Expected: nenhuma falha nova em relação ao estado anterior à Fase 5

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/web/coordination.html src/agente_ia_edu/web/coordination.js \
        src/agente_ia_edu/web/coordination.css \
        tests/test_mock_exam_school_evolution_frontend.js
git commit -m "feat(simulados): evolucao por aplicacao na escola, com a mesma supressao

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Cobertura do spec por task

| Requisito | Seção | Task |
|---|---|---|
| Calibragem com parâmetros fixos; âncoras travadas nos valores da régua | §6.4 | 2, 3 |
| Verificação de deriva antes de travar; limiar configurável, padrão 0,5 logit | §6.4 | 1 |
| Âncora deslocada é descartada **e sinalizada**, nunca usada em silêncio | §6.4 | 1, 3, 6 |
| Sistema marca quais itens são âncora | §3, §6.4 | 4 |
| Âncora não aparece em devolutiva de gabarito ao aluno | §6.4 | 5 |
| Ligar itens de simulados diferentes pelo mesmo `anchor_key` | §3 | 4 |
| Visualizar âncoras saudáveis e derivadas | §6.4 | 6 |
| Evolução no ano só com equalização; sem âncoras, gráfico suprimido | §7 | 7, 8, 9 |
| Nota rotulada como escala de referência da escola; régua não verificada é provisória | §6.3 | 6, 8, 9 |
| Invariância da equalização com semente fixa e tolerância declarada | §8.1 | 2 |
| Fronteira matemática/persistência | §2.2 | 1–3 |

> A numeração acima é a das tarefas atuais. Ela foi corrigida depois que a antiga
> Task 1 (`calibrate_equated`) deixou de existir — a estimação da distribuição
> populacional passou para dentro de `calibrate()` na Fase 3, onde fica coberta
> pelo teste de recuperação de parâmetros.
