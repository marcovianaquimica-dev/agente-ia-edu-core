# Simulados — Fase 2: correção manual, nota bruta e relatórios

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Entregar o simulado corrigido de ponta a ponta sem visão computacional: a coordenação cria o simulado, cadastra o gabarito, digita as respostas de cada aluno, e os quatro níveis de relatório (aluno, turma, escola, itens) saem com nota por acerto bruto.

**Architecture:** Quatro módulos de serviço no core, com fronteiras iguais às que o spec impõe ao `tri_engine`: `simulado_scoring.py` é matemática pura (sem banco, sem HTTP, sem dependência nova), `simulado_service.py` orquestra ciclo de vida e gabarito, `simulado_responses.py` cuida da digitação manual e da resolução de turma/roster, e `simulado_reports.py` monta os quatro relatórios lendo o banco e delegando a conta para o módulo puro. Em cima deles, um único arquivo de rotas expõe três routers — um por portal já existente (coordenação, professor, aluno) — e três blocos de frontend JS vanilla, um por portal.

**Tech Stack:** Python 3.13, SQLAlchemy 2.x async, FastAPI, Pydantic v2, pytest (`unittest.TestCase` + `sqlite+aiosqlite` em memória, padrão de `tests/test_coordination_portal_http.py`), JS vanilla sem build step, `node --test` para o frontend.

**Spec:** `docs/superpowers/specs/2026-09-29-correcao-simulados-tri-design.md` — fatia **Fase 2** da tabela §9.

## Global Constraints

- Python `>=3.13,<3.14` (`pyproject.toml:8`). SQLAlchemy `>=2.0,<3.0` async, FastAPI `>=0.115,<1.0`.
- **Nenhuma dependência nova.** `numpy`/`scipy` NÃO estão em `pyproject.toml` — eles entram só na Fase 3 com o `tri_engine`. Toda a estatística da Fase 2 (proporção de acerto, correlação ponto-bisserial) é escrita em `math` da biblioteca padrão.
- O core **nunca** importa `cv2`/OpenCV/Pillow. Nenhum arquivo desta fase importa qualquer um dos três. O comentário em `pyproject.toml:31-36` explica por quê (Pillow altera `page.images` do pypdf e quebra o parser de ingestão).
- `pytest` roda com `pythonpath = ["src", "."]` (`pyproject.toml:52`); `testpaths = ["tests"]`.
- Frontend é JS vanilla sem build step: `src/agente_ia_edu/web/*.js` são carregados direto por `<script src>`. Nenhum bundler, nenhum import ES module, nenhum framework.
- Autorização reusa o que existe, sem modelo novo: `Requester`/`Requester.is_privileged` de `src/agente_ia_edu/services/question_list_store.py:76-84`, `AuthorizationService.require_role` de `src/agente_ia_edu/services/authorization.py:179-187`, e `resolve_active_enrollment` de `src/agente_ia_edu/services/student_enrollment_resolution.py:21-48`.
- Escopo da fase: **acerto bruto**. Nenhum código de TRI, calibragem, equalização, escala ENEM ou leitura óptica. Os status `SCANNED` e `CALIBRATED` são recusados explicitamente com mensagem dizendo a qual fase pertencem.
- Spec §6.4, obrigação que vale já nesta fase: **item-âncora nunca aparece em devolutiva de gabarito ao aluno.** O boletim do aluno omite `correct_option` de item com `is_anchor = true`.
- Spec §7: a evolução por nota absoluta **é suprimida** sem equalização por âncoras. Nenhum relatório desta fase produz série temporal de nota.
- Migrations: **esta fase não cria nenhuma**. O modelo de dados de §3 é entregue pela Fase 1 (a partir da migration 057).
- Spec §3.0 (isolamento multi-tenant): **toda tabela do subsistema carrega `school_id`, e toda FK entre elas é composta** `(school_id, parent_id)`. Consequência direta para esta fase: **todo `INSERT` feito aqui preenche `school_id` explicitamente**, com o `school_id` do próprio simulado — inclusive em `mock_exam_areas`, `mock_exam_items` e `mock_exam_responses`. Omitir a coluna não gera um dado meio certo: gera erro de `NOT NULL`, o que é exatamente o objetivo da regra.
- Português nos textos de interface e nas mensagens de erro voltadas ao operador; identificadores em inglês, como no resto do repositório.

---

## Premissa: o que a Fase 1 entrega e esta fase consome

Este plano **não** cria modelo de dados. Ele consome, como dado, os modelos ORM de §3 do spec, importáveis de `agente_ia_edu.db.models.mock_exam` e reexportados em `agente_ia_edu/db/models/__init__.py` (mesmo padrão de `MaterialAssignment`, `db/models/__init__.py:69`):

```python
from agente_ia_edu.db.models.answer_card import (
    AnswerCardScan,   # school_id, mock_exam_id, answer_card_id, image_hash,
                      # storage_path, source, status, reader_version,
                      # processed_at, failure_reason
)
from agente_ia_edu.db.models.mock_exam import (
    MockExam,         # school_id, academic_year_id (NOT NULL), name,
                      # application_date, exam_day, status, created_at,
                      # updated_at
    MockExamArea,     # school_id, mock_exam_id, code, label, display_order
    MockExamItem,     # school_id, mock_exam_id, position, area_code,
                      # correct_option, is_anchor, anchor_key,
                      # source_question_version_id
    MockExamResponse, # school_id, mock_exam_id, student_id, item_id,
                      # chosen_option, is_correct, source ('MANUAL' | 'OMR'),
                      # entered_by_external_id (String(255), nulo quando OMR),
                      # created_at
    MockExamWorkflowAudit,  # school_id, mock_exam_id, from_status, to_status,
                            # actor_external_id (String(255)), occurred_at, note
)
```

**`school_id` está em TODAS elas** (spec §3.0), e as FKs entre elas são compostas
`(school_id, parent_id)`. Toda criação de linha neste plano passa `school_id=exam.school_id`
explicitamente — não é redundância decorativa, é o que impede um `join` mal escrito de ligar o
aluno de uma escola ao simulado de outra.

Detalhes do modelo que esta fase precisa respeitar:

- `AnswerCardScan` vive em `db/models/answer_card.py`, **não** em `mock_exam.py` — a Fase 1
  separa "a prova" de "o papel". Só a trava de publicação (Task 3) o importa.
- `MockExam.academic_year_id` é **NOT NULL** (spec §3): o schema garante o invariante, e não a
  disciplina de quem chama. A tipagem obrigatória no Pydantic de criação continua, como defesa
  em profundidade — a razão do invariante é que roster, turmas e todos os relatórios resolvem o
  aluno por `StudentEnrollment → Class.academic_year_id`, e um simulado sem ano letivo
  produziria turma vazia e boletim vazio **em silêncio**.
- `MockExam.updated_at` é mantido pelo `onupdate` do modelo (padrão de `MaterialAssignment`,
  `db/models/catalog.py:536-538`). Nenhum código desta fase o escreve à mão.
- **`MockExamResponse.source` e `entered_by_external_id` não são opcionais nesta fase.** Toda
  linha que a digitação manual grava leva `source='MANUAL'` e o identificador externo de quem
  digitou. Sem isso, quando a nota de um aluno for contestada, não há como saber quem digitou
  aquele cartão — e a fila de conferência da §5 depende de distinguir digitado de lido.
- **Toda transição de status grava uma linha em `MockExamWorkflowAudit`** (spec §3, no padrão
  de `AssessmentWorkflowAudit`, `db/models/assessments.py:183-207`).
- `MockExamWorkflowAudit.occurred_at` e `MockExamResponse.created_at` têm default no modelo
  (`default=lambda: datetime.now(timezone.utc)`, como toda coluna de tempo do repositório).
  Nenhum código desta fase os escreve à mão — se a Fase 1 os entregar sem default, os inserts
  desta fase quebram e o conserto é lá, não aqui.

`MockExam.status` percorre `DRAFT | PRINTED | APPLIED | SCANNED | CALIBRATED | PUBLISHED` (spec §3).
`MockExamResponse.student_id` é FK para `students.id` (`db/models/academic.py:345-377`), não o `external_user_id` das ligações de portal.

### O ator é gravado como identificador externo, sem resolução nenhuma

`entered_by_external_id` e `actor_external_id` são `String(255)` com o identificador externo
do chamador (spec §3), no mesmo tipo de `AssessmentWorkflowAudit.performed_by_external_id`
(`db/models/assessments.py:200`).

Isso significa que **não existe passo de resolução em lugar nenhum desta fase**: a camada HTTP
autentica por identificador externo, `AuthenticatedUserContext.external_identity_id` já o
carrega, e o serviço grava exatamente o que o chamador possui. Uma coluna preenchida com o que
já está em mãos nunca fica nula — enquanto um UUID dependeria de uma busca que pode falhar, e
falharia justamente no caso em que alguém pergunta quem digitou o cartão.

O `Requester` que os portais já constroem (`api/routes/teacher_portal.py:76`) tem esse valor em
`requester.external_user_id`. É ele que viaja até as duas colunas.

Modelos já existentes e já commitados que esta fase lê: `Student`, `StudentEnrollment`, `Class`, `Person`, `AcademicYear` (`src/agente_ia_edu/db/models/academic.py`), `School`, `UserSchoolLink` (`db/models/admin.py`).

**Se alguma coisa aqui não bater com o que a Fase 1 entregou, pare e reporte** — não invente coluna.

---

## File Structure

**Criar:**

| Arquivo | Responsabilidade |
|---|---|
| `src/agente_ia_edu/services/simulado_scoring.py` | Matemática pura: nota bruta por área/total, proporção de acerto e ponto-bisserial por item. Sem banco, sem HTTP, sem dependência nova. |
| `src/agente_ia_edu/services/simulado_service.py` | Orquestração: criar simulado, cadastrar gabarito+áreas, ler, transicionar status, trava de publicação. |
| `src/agente_ia_edu/services/simulado_responses.py` | Digitação manual: turmas do simulado, roster de uma turma, gravação idempotente das respostas de um aluno. |
| `src/agente_ia_edu/services/simulado_reports.py` | Os quatro relatórios: boletim do aluno, painel da turma, painel da escola, análise de itens. |
| `src/agente_ia_edu/api/schemas/simulados.py` | Schemas Pydantic de request/response. |
| `src/agente_ia_edu/api/routes/simulados.py` | Três routers (`/api/v1/coordination/simulados`, `/api/v1/teacher/simulados`, `/api/v1/student/simulados`). |
| `tests/test_simulado_scoring.py` | Unitário puro do módulo de matemática. |
| `tests/test_simulado_service.py` | Serviço: criação, gabarito, validações, ciclo de vida. |
| `tests/test_simulado_manual_entry.py` | Serviço: turmas, roster, digitação. |
| `tests/test_simulado_reports.py` | Serviço: os quatro relatórios. |
| `tests/test_simulados_http.py` | Camada HTTP dos três routers. |
| `tests/test_simulados_frontend.js` | Asserções estáticas sobre HTML/JS/CSS dos três portais. |

**Modificar:**

| Arquivo | O que muda |
|---|---|
| `src/agente_ia_edu/api/app.py:35-36, 76-81` | Registrar os três routers novos sob `reception_only_guard`. |
| `src/agente_ia_edu/web/coordination.html:62, :477` | Item de menu `data-view="simulados"` e `<section id="view-simulados">`. |
| `src/agente_ia_edu/web/coordination.js:58, :101` | Entrada no `titleMap` e no `loadCurrentView`, mais o bloco de simulados. |
| `src/agente_ia_edu/web/coordination.css` | Classes `.sim-*` e reflow em `@media (max-width: 768px)`. |
| `src/agente_ia_edu/web/teacher.html:66, :806` | Item de menu e painel da turma. |
| `src/agente_ia_edu/web/teacher.js:112, :136` | `titleMap` + `loadCurrentView` + bloco de simulados. |
| `src/agente_ia_edu/web/teacher.css` | Classes `.sim-*` e reflow. |
| `src/agente_ia_edu/web/index.html:56, :579` | Item de menu e painel do boletim. |
| `src/agente_ia_edu/web/app.js:96-150` | `titleMap`, loader em `switchView` e bloco de simulados. |
| `src/agente_ia_edu/web/styles.css` | Classes `.sim-*` e reflow. |

**Por que um arquivo de rotas e não três:** `essay_submissions.py` já exporta três routers com prefixos diferentes de um único módulo (`api/app.py:17-21`). Simulado é um assunto só, visto por três papéis; espalhá-lo por `teacher_portal.py` (511 linhas), `coordination_portal.py` (732 linhas) e `student.py` engordaria três arquivos que já estão grandes e deixaria a regra de autorização do simulado em três lugares.

---

### Task 1: Módulo de pontuação puro (`simulado_scoring.py`)

**Files:**
- Create: `src/agente_ia_edu/services/simulado_scoring.py`
- Test: `tests/test_simulado_scoring.py`

**Interfaces:**
- Consumes: nada (primeira tarefa; só `math`, `uuid`, `dataclasses`, `typing` da stdlib).
- Produces:
  - `ItemDescriptor(item_id: uuid.UUID, position: int, area_code: str, correct_option: str)`
  - `ResponseFact(item_id: uuid.UUID, area_code: str, chosen_option: str | None, is_correct: bool)`
  - `AreaRawScore(area_code: str, correct: int, answered: int, total_items: int, percentage: float)`
  - `StudentRawScore(student_id: uuid.UUID, by_area: tuple[AreaRawScore, ...], total_correct: int, total_answered: int, total_items: int, total_percentage: float)`
  - `ItemStatistics(item_id: uuid.UUID, position: int, area_code: str, correct_option: str, n_responses: int, p_value: float, point_biserial: float | None, option_distribution: dict[str, int])`
  - `score_student(student_id, responses, area_item_counts) -> StudentRawScore`
  - `compute_item_statistics(items, responses_by_student) -> list[ItemStatistics]`
  - constantes `VALID_OPTIONS: tuple[str, ...]` e `BLANK_KEY: str`

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_simulado_scoring.py`:

```python
"""Unitário puro de services/simulado_scoring.py (Fase 2, Task 1).

Sem banco, sem HTTP, sem rede: o módulo sob teste é o análogo de nota bruta
do que o spec §2.2 exige do tri_engine - matemática que dá para alimentar
com números conhecidos e conferir contra a conta feita à mão.

Os valores de ponto-bisserial abaixo foram calculados à mão sobre a matriz
4 alunos x 3 itens montada em _MATRIX, com correlação de Pearson entre o
acerto do item (0/1) e o REST score do aluno na mesma área (total de acertos
da área menos o próprio item):

  I1: x=[1,1,1,0] rest=[2,1,0,1] -> Sxy=0.0      -> r = 0.0
  I2: x=[1,1,0,0] rest=[2,1,1,1] -> Sxy=0.5,
                                    Sxx=1.0, Syy=0.75  -> r = 0.5/sqrt(0.75)  = 0.5774
  I3: x=[1,0,0,1] rest=[2,2,1,0] -> Sxy=-0.5,
                                    Sxx=1.0, Syy=2.75  -> r = -0.5/sqrt(2.75) = -0.3015
"""

import unittest
import uuid

from agente_ia_edu.services.simulado_scoring import (
    BLANK_KEY,
    ItemDescriptor,
    ResponseFact,
    compute_item_statistics,
    score_student,
)

I1, I2, I3 = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
ITEMS = [
    ItemDescriptor(item_id=I1, position=1, area_code="LC", correct_option="A"),
    ItemDescriptor(item_id=I2, position=2, area_code="LC", correct_option="B"),
    ItemDescriptor(item_id=I3, position=3, area_code="LC", correct_option="C"),
]

# (aluno, [acerto em I1, I2, I3]) - a matriz cujos r estão no docstring.
_MATRIX = {
    "A": (1, 1, 1),
    "B": (1, 1, 0),
    "C": (1, 0, 0),
    "D": (0, 0, 1),
}


def _facts(flags):
    """Transforma (1,0,1) em ResponseFacts: acerto grava a alternativa certa,
    erro grava 'E' (uma alternativa real e errada em todos os três itens)."""
    out = []
    for descriptor, flag in zip(ITEMS, flags):
        out.append(ResponseFact(
            item_id=descriptor.item_id,
            area_code=descriptor.area_code,
            chosen_option=descriptor.correct_option if flag else "E",
            is_correct=bool(flag),
        ))
    return out


class ScoreStudentTests(unittest.TestCase):
    def test_raw_score_by_area_counts_blanks_as_wrong_but_not_as_answered(self):
        student_id = uuid.uuid4()
        responses = [
            ResponseFact(item_id=I1, area_code="LC", chosen_option="A", is_correct=True),
            ResponseFact(item_id=I2, area_code="LC", chosen_option="E", is_correct=False),
            ResponseFact(item_id=I3, area_code="MT", chosen_option="C", is_correct=True),
            ResponseFact(item_id=uuid.uuid4(), area_code="MT", chosen_option=None, is_correct=False),
        ]
        score = score_student(student_id, responses, {"LC": 2, "MT": 2})

        self.assertEqual([a.area_code for a in score.by_area], ["LC", "MT"])
        self.assertEqual((score.by_area[0].correct, score.by_area[0].answered), (1, 2))
        self.assertEqual((score.by_area[1].correct, score.by_area[1].answered), (1, 1))
        self.assertEqual(score.by_area[0].percentage, 50.0)
        self.assertEqual(score.total_correct, 2)
        self.assertEqual(score.total_answered, 3)
        self.assertEqual(score.total_items, 4)
        self.assertEqual(score.total_percentage, 50.0)

    def test_unknown_area_code_raises_instead_of_being_silently_dropped(self):
        with self.assertRaises(ValueError):
            score_student(
                uuid.uuid4(),
                [ResponseFact(item_id=I1, area_code="XX", chosen_option="A", is_correct=True)],
                {"LC": 1},
            )


class ItemStatisticsTests(unittest.TestCase):
    def setUp(self):
        self.stats = {
            s.position: s
            for s in compute_item_statistics(
                ITEMS, {uuid.uuid4(): _facts(flags) for flags in _MATRIX.values()}
            )
        }

    def test_p_value_is_the_share_of_respondents_who_got_it_right(self):
        self.assertEqual(self.stats[1].p_value, 0.75)
        self.assertEqual(self.stats[2].p_value, 0.5)
        self.assertEqual(self.stats[3].p_value, 0.5)
        self.assertEqual(self.stats[1].n_responses, 4)

    def test_point_biserial_matches_the_hand_computed_rest_score_correlation(self):
        self.assertAlmostEqual(self.stats[1].point_biserial, 0.0, places=4)
        self.assertAlmostEqual(self.stats[2].point_biserial, 0.5774, places=4)
        self.assertAlmostEqual(self.stats[3].point_biserial, -0.3015, places=4)

    def test_option_distribution_counts_every_alternative_and_the_blank(self):
        distribution = self.stats[1].option_distribution
        self.assertEqual(distribution["A"], 3)
        self.assertEqual(distribution["E"], 1)
        self.assertEqual(distribution[BLANK_KEY], 0)
        self.assertEqual(set(distribution), {"A", "B", "C", "D", "E", BLANK_KEY})

    def test_degenerate_item_gets_none_point_biserial_never_a_fabricated_zero(self):
        everyone_right = {
            uuid.uuid4(): [ResponseFact(item_id=I1, area_code="LC", chosen_option="A", is_correct=True),
                           ResponseFact(item_id=I2, area_code="LC", chosen_option="B", is_correct=True)]
            for _ in range(4)
        }
        stats = {s.position: s for s in compute_item_statistics(ITEMS[:2], everyone_right)}
        self.assertEqual(stats[1].p_value, 1.0)
        self.assertIsNone(stats[1].point_biserial)

    def test_results_come_back_ordered_by_position(self):
        computed = compute_item_statistics(
            list(reversed(ITEMS)), {uuid.uuid4(): _facts((1, 0, 1))}
        )
        self.assertEqual([s.position for s in computed], [1, 2, 3])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar o teste e ver falhar**

Run: `python -m pytest tests/test_simulado_scoring.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.services.simulado_scoring'`

- [ ] **Step 3: Implementação mínima**

Crie `src/agente_ia_edu/services/simulado_scoring.py`:

```python
"""Matemática de nota bruta e de qualidade de item do subsistema de
simulados (Fase 2 de docs/superpowers/specs/2026-09-29-correcao-simulados-tri-design.md).

Módulo PURO, pela mesma razão que o spec §2.2 exige do tri_engine: não
conhece banco, não conhece aluno de verdade, não conhece HTTP. Dá para
alimentá-lo com uma matriz conhecida e exigir os números conferidos à mão -
é a única forma honesta de saber que uma conta que vira nota de aluno está
certa, porque uma conta errada aqui não levanta exceção nenhuma.

NENHUMA dependência nova: numpy e scipy só entram na Fase 3 junto com o
motor de TRI. Aqui é `math` da biblioteca padrão. O volume máximo previsto
no spec (1.500 alunos x 90 itens = 135 mil respostas) é folgado para Python
puro.

As duas estatísticas implementadas são exatamente as que o spec §7 coloca na
análise de itens SEM depender de calibragem:

- `p_value`: proporção de acerto. O denominador é o número de alunos com
  LINHA de resposta para o item - uma questão deixada em branco conta como
  erro, não como ausência. É assim que o aluno é pontuado, então é assim que
  o item é medido.
- `point_biserial`: correlação de Pearson entre o acerto no item (0/1) e o
  REST score do aluno - o total de acertos DA MESMA ÁREA menos o próprio
  item. Tirar o item do total evita a correlação inflada que aparece quando
  o item é parte do escore com que ele é comparado. Restringir à área é
  deliberado: Linguagens e Matemática medem construtos diferentes, e
  correlacionar um item de Linguagens contra o total geral mistura sinal com
  ruído. É este número que denuncia gabarito cadastrado errado (spec §6.5:
  discriminação negativa = o aluno forte erra e o fraco acerta).
"""

from __future__ import annotations

import math
import uuid
from dataclasses import dataclass
from typing import Mapping, Sequence

VALID_OPTIONS: tuple[str, ...] = ("A", "B", "C", "D", "E")
BLANK_KEY = "BRANCO"


@dataclass(frozen=True)
class ItemDescriptor:
    """Um item do gabarito, sem nada do banco além dos valores."""

    item_id: uuid.UUID
    position: int
    area_code: str
    correct_option: str


@dataclass(frozen=True)
class ResponseFact:
    """A resposta de UM aluno a UM item. `chosen_option` None é branco -
    nunca substituído por uma alternativa inventada."""

    item_id: uuid.UUID
    area_code: str
    chosen_option: str | None
    is_correct: bool


@dataclass(frozen=True)
class AreaRawScore:
    area_code: str
    correct: int
    answered: int
    total_items: int
    percentage: float


@dataclass(frozen=True)
class StudentRawScore:
    student_id: uuid.UUID
    by_area: tuple[AreaRawScore, ...]
    total_correct: int
    total_answered: int
    total_items: int
    total_percentage: float


@dataclass(frozen=True)
class ItemStatistics:
    item_id: uuid.UUID
    position: int
    area_code: str
    correct_option: str
    n_responses: int
    p_value: float
    point_biserial: float | None
    option_distribution: dict[str, int]


def _percentage(correct: int, total_items: int) -> float:
    """Percentual sobre o TOTAL de itens da área, não sobre os respondidos:
    deixar em branco custa a mesma coisa que errar, e arredondar sobre os
    respondidos premiaria quem respondeu menos."""
    if total_items <= 0:
        return 0.0
    return round(100.0 * correct / total_items, 1)


def score_student(
    student_id: uuid.UUID,
    responses: Sequence[ResponseFact],
    area_item_counts: Mapping[str, int],
) -> StudentRawScore:
    """Nota bruta de um aluno, por área e total.

    `area_item_counts` mapeia código de área -> número de itens da área no
    simulado, e a ORDEM do mapping é a ordem de `by_area` (o chamador passa
    um dict já ordenado por `MockExamArea.display_order`).
    """
    correct_by_area = {code: 0 for code in area_item_counts}
    answered_by_area = {code: 0 for code in area_item_counts}

    for fact in responses:
        if fact.area_code not in correct_by_area:
            raise ValueError(
                f"resposta para area desconhecida {fact.area_code!r}: "
                "o gabarito e as respostas estao fora de sincronia"
            )
        if fact.chosen_option is not None:
            answered_by_area[fact.area_code] += 1
        if fact.is_correct:
            correct_by_area[fact.area_code] += 1

    by_area = tuple(
        AreaRawScore(
            area_code=code,
            correct=correct_by_area[code],
            answered=answered_by_area[code],
            total_items=total_items,
            percentage=_percentage(correct_by_area[code], total_items),
        )
        for code, total_items in area_item_counts.items()
    )
    total_items = sum(area_item_counts.values())
    total_correct = sum(area.correct for area in by_area)
    return StudentRawScore(
        student_id=student_id,
        by_area=by_area,
        total_correct=total_correct,
        total_answered=sum(area.answered for area in by_area),
        total_items=total_items,
        total_percentage=_percentage(total_correct, total_items),
    )


def _pearson(xs: Sequence[float], ys: Sequence[float]) -> float | None:
    """Correlação de Pearson, ou None quando um dos lados não tem variância.

    None, e nunca 0.0: um item que todo mundo acertou não tem discriminação
    ZERO medida, ele tem discriminação NÃO MEDÍVEL. Devolver 0.0 aqui faria
    um item degenerado se parecer com um item ruim porém medido.
    """
    n = len(xs)
    if n < 2:
        return None
    mean_x = sum(xs) / n
    mean_y = sum(ys) / n
    sxy = sum((x - mean_x) * (y - mean_y) for x, y in zip(xs, ys))
    sxx = sum((x - mean_x) ** 2 for x in xs)
    syy = sum((y - mean_y) ** 2 for y in ys)
    if sxx == 0.0 or syy == 0.0:
        return None
    return sxy / math.sqrt(sxx * syy)


def compute_item_statistics(
    items: Sequence[ItemDescriptor],
    responses_by_student: Mapping[uuid.UUID, Sequence[ResponseFact]],
) -> list[ItemStatistics]:
    """Proporção de acerto, ponto-bisserial e distribuição de alternativas
    por item, ordenados por posição."""
    known = {item.item_id for item in items}
    correct_flags: dict[uuid.UUID, list[float]] = {item_id: [] for item_id in known}
    rest_scores: dict[uuid.UUID, list[float]] = {item_id: [] for item_id in known}
    distribution: dict[uuid.UUID, dict[str, int]] = {
        item_id: {key: 0 for key in (*VALID_OPTIONS, BLANK_KEY)} for item_id in known
    }

    for facts in responses_by_student.values():
        area_correct: dict[str, int] = {}
        for fact in facts:
            if fact.is_correct:
                area_correct[fact.area_code] = area_correct.get(fact.area_code, 0) + 1
        for fact in facts:
            if fact.item_id not in known:
                continue
            flag = 1.0 if fact.is_correct else 0.0
            correct_flags[fact.item_id].append(flag)
            rest_scores[fact.item_id].append(area_correct.get(fact.area_code, 0) - flag)
            key = fact.chosen_option if fact.chosen_option in VALID_OPTIONS else BLANK_KEY
            distribution[fact.item_id][key] += 1

    computed: list[ItemStatistics] = []
    for item in items:
        flags = correct_flags[item.item_id]
        n_responses = len(flags)
        correlation = _pearson(flags, rest_scores[item.item_id])
        computed.append(
            ItemStatistics(
                item_id=item.item_id,
                position=item.position,
                area_code=item.area_code,
                correct_option=item.correct_option,
                n_responses=n_responses,
                p_value=round(sum(flags) / n_responses, 4) if n_responses else 0.0,
                point_biserial=None if correlation is None else round(correlation, 4),
                option_distribution=distribution[item.item_id],
            )
        )
    computed.sort(key=lambda stats: stats.position)
    return computed


__all__ = [
    "BLANK_KEY",
    "VALID_OPTIONS",
    "AreaRawScore",
    "ItemDescriptor",
    "ItemStatistics",
    "ResponseFact",
    "StudentRawScore",
    "compute_item_statistics",
    "score_student",
]
```

- [ ] **Step 4: Rodar o teste e ver passar**

Run: `python -m pytest tests/test_simulado_scoring.py -v`
Expected: PASS — 7 testes.

- [ ] **Step 5: Confirmar que o core continua sem visão computacional**

Run: `grep -rn "cv2\|OpenCV\|PIL\|Pillow" src/agente_ia_edu/services/simulado_scoring.py`
Expected: sem saída (exit 1).

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/simulado_scoring.py tests/test_simulado_scoring.py
git commit -m "feat(simulados): nota bruta e estatistica de item em modulo puro

Fase 2 Task 1. Proporcao de acerto e ponto-bisserial de rest score por
area, em math da stdlib - numpy/scipy so entram na Fase 3 com o tri_engine.
Item degenerado devolve point_biserial None, nunca 0.0.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: Serviço de simulado — criação e cadastro do gabarito

**Files:**
- Create: `src/agente_ia_edu/services/simulado_service.py`
- Test: `tests/test_simulado_service.py`

**Interfaces:**
- Consumes: `Requester` (`services/question_list_store.py:76`); os modelos da Fase 1 listados em "Premissa".
- Produces:
  - constantes `STATUS_DRAFT/PRINTED/APPLIED/SCANNED/CALIBRATED/PUBLISHED`
  - **reexporta** `VALID_OPTIONS` de `simulado_scoring` (Task 1) — não redeclara
  - erros `SimuladoNotFoundError(LookupError)`, `SimuladoAuthError(PermissionError)`, `SimuladoStateError(ValueError)`, `SimuladoValidationError(ValueError)`
  - `AreaInput(code: str, label: str, display_order: int)`
  - `AnswerKeyEntryInput(position: int, area_code: str, correct_option: str, is_anchor: bool, anchor_key: str | None)`
  - `ExamSummary(exam: MockExam, item_count: int)`
  - `SimuladoService(session)` com `create_exam`, `get_exam`, `list_exams`, `load_answer_key`, `register_answer_key`
  - `require_coordination(requester) -> None`
  - constante `BLOCKING_SCAN_STATUSES: tuple[str, ...]`

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_simulado_service.py`:

```python
"""SimuladoService: criação do simulado e cadastro do gabarito (Fase 2, Task 2)
e ciclo de vida (Task 3).

SQLite em memória com Base.metadata.create_all, mesma receita de
tests/test_coordination_portal_http.py::CoordinationPortalHTTP.setUp - os
modelos de §3 do spec entram no metadata junto com o resto.

A autorização não é reinventada: usa o mesmo Requester de
services/question_list_store.py que coordination_portal.py/teacher_portal.py
já constroem a partir do AuthenticatedUserContext.
"""

import asyncio
import unittest
import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AcademicYear, School
from agente_ia_edu.db.models.mock_exam import MockExam, MockExamArea, MockExamItem
from agente_ia_edu.services.question_list_store import Requester
from agente_ia_edu.services.simulado_service import (
    VALID_OPTIONS,
    AnswerKeyEntryInput,
    AreaInput,
    SimuladoAuthError,
    SimuladoNotFoundError,
    SimuladoService,
    SimuladoStateError,
    SimuladoValidationError,
)

AREAS = [
    AreaInput(code="LC", label="Linguagens e Códigos", display_order=1),
    AreaInput(code="CH", label="Ciências Humanas", display_order=2),
]
ITEMS = [
    AnswerKeyEntryInput(position=1, area_code="LC", correct_option="A"),
    AnswerKeyEntryInput(position=2, area_code="LC", correct_option="B"),
    AnswerKeyEntryInput(position=3, area_code="CH", correct_option="C"),
    AnswerKeyEntryInput(position=4, area_code="CH", correct_option="D",
                        is_anchor=True, anchor_key="CH-ENEM-2024-91"),
]


class SimuladoServiceTestCase(unittest.TestCase):
    """Base com banco + duas escolas + um ano letivo em cada."""

    def setUp(self):
        async def _setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                school_a = School(id=uuid.uuid4(), code="SIM-A", name="Escola A")
                school_b = School(id=uuid.uuid4(), code="SIM-B", name="Escola B")
                year_a = AcademicYear(id=uuid.uuid4(), school_id=school_a.id, year=2026,
                                      external_id="YEAR-SIM-A")
                year_b = AcademicYear(id=uuid.uuid4(), school_id=school_b.id, year=2026,
                                      external_id="YEAR-SIM-B")
                session.add_all([school_a, school_b, year_a, year_b])
                await session.commit()
                return engine, factory, school_a.id, school_b.id, year_a.id, year_b.id

        (self.engine, self.factory, self.school_a, self.school_b,
         self.year_a, self.year_b) = asyncio.run(_setup())
        self.coord = Requester(external_user_id="coord-a", school_id=str(self.school_a),
                               role="COORDINATOR")
        self.coord_b = Requester(external_user_id="coord-b", school_id=str(self.school_b),
                                 role="COORDINATOR")
        self.teacher = Requester(external_user_id="prof-a", school_id=str(self.school_a),
                                 role="TEACHER")

    def tearDown(self):
        asyncio.run(self.engine.dispose())

    def _run(self, coroutine_factory):
        async def _do():
            async with self.factory() as session:
                return await coroutine_factory(SimuladoService(session), session)
        return asyncio.run(_do())

    def _create_exam(self, requester=None, name="Simulado ENEM 1"):
        requester = requester or self.coord
        return self._run(lambda service, _session: service.create_exam(
            requester=requester, school_id=self.school_a, academic_year_id=self.year_a,
            name=name, exam_day=1, application_date=None))

    def _with_answer_key(self, exam_id, areas=None, items=None):
        return self._run(lambda service, _session: service.register_answer_key(
            exam_id, requester=self.coord, areas=areas or AREAS, items=items or ITEMS))


class CreateExamTests(SimuladoServiceTestCase):
    def test_coordination_creates_a_draft_exam(self):
        exam = self._create_exam()
        self.assertEqual(exam.status, "DRAFT")
        self.assertEqual(exam.name, "Simulado ENEM 1")
        self.assertEqual(exam.exam_day, 1)
        self.assertEqual(str(exam.school_id), str(self.school_a))

    def test_teacher_cannot_create_an_exam(self):
        with self.assertRaises(SimuladoAuthError):
            self._create_exam(requester=self.teacher)

    def test_coordination_cannot_create_an_exam_in_another_school(self):
        with self.assertRaises(SimuladoAuthError):
            self._create_exam(requester=self.coord_b)

    def test_blank_name_is_refused(self):
        with self.assertRaises(SimuladoValidationError):
            self._create_exam(name="   ")

    def test_exam_day_outside_1_2_is_refused(self):
        with self.assertRaises(SimuladoValidationError):
            self._run(lambda service, _s: service.create_exam(
                requester=self.coord, school_id=self.school_a, academic_year_id=self.year_a,
                name="Dia 3", exam_day=3, application_date=None))

    def test_list_exams_only_returns_the_requesters_own_school(self):
        self._create_exam(name="A1")
        self._run(lambda service, _s: service.create_exam(
            requester=self.coord_b, school_id=self.school_b, academic_year_id=self.year_b,
            name="B1", exam_day=1, application_date=None))
        summaries = self._run(lambda service, _s: service.list_exams(requester=self.coord))
        self.assertEqual([s.exam.name for s in summaries], ["A1"])
        self.assertEqual(summaries[0].item_count, 0)

    def test_get_exam_from_another_school_is_403_not_404(self):
        exam = self._create_exam()
        with self.assertRaises(SimuladoAuthError):
            self._run(lambda service, _s: service.get_exam(exam.id, requester=self.coord_b))

    def test_get_exam_that_does_not_exist_is_404(self):
        with self.assertRaises(SimuladoNotFoundError):
            self._run(lambda service, _s: service.get_exam(uuid.uuid4(), requester=self.coord))


class RegisterAnswerKeyTests(SimuladoServiceTestCase):
    def test_answer_key_persists_areas_and_items(self):
        exam = self._create_exam()
        self._with_answer_key(exam.id)

        async def _read():
            async with self.factory() as session:
                areas = (await session.execute(
                    select(MockExamArea).where(MockExamArea.mock_exam_id == exam.id)
                    .order_by(MockExamArea.display_order))).scalars().all()
                items = (await session.execute(
                    select(MockExamItem).where(MockExamItem.mock_exam_id == exam.id)
                    .order_by(MockExamItem.position))).scalars().all()
                return [(a.code, a.label) for a in areas], items

        areas, items = asyncio.run(_read())
        self.assertEqual(areas, [("LC", "Linguagens e Códigos"), ("CH", "Ciências Humanas")])
        self.assertEqual([i.position for i in items], [1, 2, 3, 4])
        self.assertEqual([i.correct_option for i in items], ["A", "B", "C", "D"])
        self.assertEqual([i.is_anchor for i in items], [False, False, False, True])
        self.assertEqual(items[3].anchor_key, "CH-ENEM-2024-91")
        self.assertIsNone(items[0].source_question_version_id)

    def test_answer_key_replaces_the_previous_one_instead_of_appending(self):
        exam = self._create_exam()
        self._with_answer_key(exam.id)
        self._with_answer_key(exam.id, areas=AREAS[:1], items=[
            AnswerKeyEntryInput(position=1, area_code="LC", correct_option="E")])

        async def _count():
            async with self.factory() as session:
                items = (await session.execute(
                    select(MockExamItem).where(MockExamItem.mock_exam_id == exam.id))).scalars().all()
                areas = (await session.execute(
                    select(MockExamArea).where(MockExamArea.mock_exam_id == exam.id))).scalars().all()
                return len(items), len(areas), items[0].correct_option

        self.assertEqual(asyncio.run(_count()), (1, 1, "E"))

    def test_positions_with_a_gap_are_refused(self):
        exam = self._create_exam()
        with self.assertRaises(SimuladoValidationError):
            self._with_answer_key(exam.id, items=[
                AnswerKeyEntryInput(position=1, area_code="LC", correct_option="A"),
                AnswerKeyEntryInput(position=3, area_code="LC", correct_option="B")])

    def test_repeated_position_is_refused(self):
        exam = self._create_exam()
        with self.assertRaises(SimuladoValidationError):
            self._with_answer_key(exam.id, items=[
                AnswerKeyEntryInput(position=1, area_code="LC", correct_option="A"),
                AnswerKeyEntryInput(position=1, area_code="LC", correct_option="B")])

    def test_option_outside_a_to_e_is_refused(self):
        exam = self._create_exam()
        with self.assertRaises(SimuladoValidationError):
            self._with_answer_key(exam.id, items=[
                AnswerKeyEntryInput(position=1, area_code="LC", correct_option="F")])

    def test_item_pointing_at_an_unregistered_area_is_refused(self):
        exam = self._create_exam()
        with self.assertRaises(SimuladoValidationError):
            self._with_answer_key(exam.id, areas=AREAS[:1], items=[
                AnswerKeyEntryInput(position=1, area_code="CH", correct_option="A")])

    def test_anchor_without_anchor_key_is_refused(self):
        exam = self._create_exam()
        with self.assertRaises(SimuladoValidationError):
            self._with_answer_key(exam.id, items=[
                AnswerKeyEntryInput(position=1, area_code="LC", correct_option="A",
                                    is_anchor=True, anchor_key=None)])

    def test_repeated_anchor_key_in_the_same_exam_is_refused(self):
        exam = self._create_exam()
        with self.assertRaises(SimuladoValidationError):
            self._with_answer_key(exam.id, items=[
                AnswerKeyEntryInput(position=1, area_code="LC", correct_option="A",
                                    is_anchor=True, anchor_key="K1"),
                AnswerKeyEntryInput(position=2, area_code="LC", correct_option="B",
                                    is_anchor=True, anchor_key="K1")])

    def test_anchor_key_on_a_non_anchor_item_is_refused(self):
        exam = self._create_exam()
        with self.assertRaises(SimuladoValidationError):
            self._with_answer_key(exam.id, items=[
                AnswerKeyEntryInput(position=1, area_code="LC", correct_option="A",
                                    is_anchor=False, anchor_key="K1")])

    def test_area_codes_and_options_are_normalised_to_upper_case(self):
        exam = self._create_exam()
        self._with_answer_key(
            exam.id,
            areas=[AreaInput(code=" lc ", label="  Linguagens  ", display_order=1)],
            items=[AnswerKeyEntryInput(position=1, area_code="lc", correct_option="a")])
        areas, items = self._run(lambda service, _s: service.load_answer_key(
            exam.id, requester=self.coord))
        self.assertEqual(areas[0].code, "LC")
        self.assertEqual(areas[0].label, "Linguagens")
        self.assertEqual(items[0].area_code, "LC")
        self.assertEqual(items[0].correct_option, "A")

    def test_teacher_cannot_register_an_answer_key(self):
        exam = self._create_exam()
        with self.assertRaises(SimuladoAuthError):
            self._run(lambda service, _s: service.register_answer_key(
                exam.id, requester=self.teacher, areas=AREAS, items=ITEMS))

    def test_answer_key_is_refused_once_the_exam_left_draft(self):
        exam = self._create_exam()
        self._with_answer_key(exam.id)
        self._run(lambda service, _s: service.transition(
            exam.id, requester=self.coord, to_status="PRINTED"))
        with self.assertRaises(SimuladoStateError):
            self._with_answer_key(exam.id)

    def test_valid_options_is_the_scoring_object_not_a_copy(self):
        """VALID_OPTIONS tem UMA definicao, em simulado_scoring.

        Este modulo a reexporta porque consumidores (inclusive a fase 4)
        a importam daqui. ``assertIs`` e nao ``assertEqual``: duas tuplas
        iguais passariam por igualdade, e o estado que nao pode voltar a
        existir e exatamente esse - duas definicoes que hoje coincidem e
        que alguem atualiza pela metade no dia em que um simulado tiver
        quatro ou seis alternativas. Nesse dia, um lado aceitaria a letra
        que o outro descarta, a resposta do aluno viraria branco, e
        nenhum teste ficaria vermelho.
        """
        from agente_ia_edu.services import simulado_scoring
        self.assertIs(VALID_OPTIONS, simulado_scoring.VALID_OPTIONS)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_simulado_service.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.services.simulado_service'`

- [ ] **Step 3: Implementação mínima**

Crie `src/agente_ia_edu/services/simulado_service.py`:

```python
"""Orquestração do simulado (spec §2.4): criar a prova, cadastrar o gabarito
e mover o status pelo ciclo de vida de §4.

Fase 2 do plano de fatias (§9): sem visão computacional e sem TRI. O serviço
já conhece os seis status de §3, mas recusa explicitamente SCANNED e
CALIBRATED, dizendo a qual fase pertencem - um status aceito sem a máquina
que o produz seria uma mentira gravada no banco.

Autorização: administrar simulado é ato de coordenação (spec §4 item 1). A
regra reusa `Requester.is_privileged` de services/question_list_store.py -
o mesmo mecanismo que api/routes/coordination_portal.py:85 e
api/routes/teacher_portal.py:85 já usam - em vez de um modelo novo. Escola
diferente da do solicitante é 403 (SimuladoAuthError), simulado inexistente
é 404 (SimuladoNotFoundError), seguindo o que o portal da coordenação já
devolve para escopo alheio.

Cada método de escrita fecha a sua própria unidade de trabalho com commit,
para que a rota fique com parsing e mapeamento de erro apenas.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import date
from typing import Sequence

from sqlalchemy import delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models.answer_card import AnswerCardScan
from agente_ia_edu.db.models.mock_exam import (
    MockExam,
    MockExamArea,
    MockExamItem,
    MockExamResponse,
    MockExamWorkflowAudit,
)
from agente_ia_edu.services.question_list_store import Requester
# UMA definição, no módulo puro. Reexportada no __all__ abaixo porque
# consumidores (inclusive a fase 4) importam `VALID_OPTIONS` deste módulo -
# agora recebendo o MESMO objeto, não uma cópia que pode divergir. A ordem
# importa: simulado_scoring monta a tabela de distratores com
# (*VALID_OPTIONS, BLANK_KEY), e por isso o tipo é tuple, não frozenset.
from agente_ia_edu.services.simulado_scoring import VALID_OPTIONS

STATUS_DRAFT = "DRAFT"
STATUS_PRINTED = "PRINTED"
STATUS_APPLIED = "APPLIED"
STATUS_SCANNED = "SCANNED"
STATUS_CALIBRATED = "CALIBRATED"
STATUS_PUBLISHED = "PUBLISHED"

# Só as transições que a Fase 2 sabe sustentar. SCANNED depende do worker de
# OMR (fase 4) e CALIBRATED da calibragem de TRI (fase 3); quando elas
# existirem, este dicionário ganha APPLIED -> SCANNED -> CALIBRATED ->
# PUBLISHED e _DEFERRED_STATUSES esvazia.
_ALLOWED_TRANSITIONS: dict[str, tuple[str, ...]] = {
    STATUS_DRAFT: (STATUS_PRINTED,),
    STATUS_PRINTED: (STATUS_APPLIED,),
    STATUS_APPLIED: (STATUS_PUBLISHED,),
    STATUS_SCANNED: (),
    STATUS_CALIBRATED: (),
    STATUS_PUBLISHED: (),
}
_DEFERRED_STATUSES = frozenset({STATUS_SCANNED, STATUS_CALIBRATED})

# Spec §4: PENDING, NEEDS_REVIEW *e* FAILED bloqueiam. FAILED entra pela mesma
# razão que os outros dois - um cartão que o leitor não conseguiu processar é um
# aluno ausente da matriz de respostas, e a matriz é o insumo da calibragem. Um
# cartão só sai do caminho sendo lido ou sendo digitado à mão, nunca ignorado.
BLOCKING_SCAN_STATUSES: tuple[str, ...] = ("PENDING", "NEEDS_REVIEW", "FAILED")


class SimuladoNotFoundError(LookupError):
    """Mapeia para HTTP 404."""


class SimuladoAuthError(PermissionError):
    """Mapeia para HTTP 403."""


class SimuladoStateError(ValueError):
    """Operação incompatível com o status atual -> HTTP 409."""


class SimuladoValidationError(ValueError):
    """Dado de entrada inválido -> HTTP 422."""


@dataclass(frozen=True)
class AreaInput:
    code: str
    label: str
    display_order: int = 0


@dataclass(frozen=True)
class AnswerKeyEntryInput:
    position: int
    area_code: str
    correct_option: str
    is_anchor: bool = False
    anchor_key: str | None = None


@dataclass(frozen=True)
class ExamSummary:
    exam: MockExam
    item_count: int


def require_coordination(requester: Requester) -> None:
    """Coordenação (ou admin de plataforma) COM escopo de escola.

    O escopo é exigido também do admin de plataforma - diferente de
    `_authorize_teacher_or_coordinator` em api/routes/teacher_portal.py:85,
    que o dispensa - porque todo simulado pertence a uma escola e não há
    operação de simulado sem tenant para comparar.
    """
    if not requester.is_privileged:
        raise SimuladoAuthError("apenas a coordenação pode administrar simulados")
    if not requester.school_id:
        raise SimuladoAuthError("o solicitante não tem escopo de escola")


def _normalize_answer_key(
    areas: Sequence[AreaInput], items: Sequence[AnswerKeyEntryInput]
) -> tuple[list[AreaInput], list[AnswerKeyEntryInput]]:
    """Valida e devolve a versão normalizada (códigos e alternativas em
    maiúsculas, rótulos sem espaço sobrando). A normalização acontece UMA
    vez, aqui, para que o que é validado seja exatamente o que é gravado."""
    if not areas:
        raise SimuladoValidationError("cadastre ao menos uma área")

    clean_areas: list[AreaInput] = []
    for area in areas:
        code = (area.code or "").strip().upper()
        label = (area.label or "").strip()
        if not code:
            raise SimuladoValidationError("código de área vazio")
        if not label:
            raise SimuladoValidationError(f"área {code}: rótulo obrigatório")
        clean_areas.append(AreaInput(code=code, label=label, display_order=area.display_order))

    codes = [area.code for area in clean_areas]
    if len(set(codes)) != len(codes):
        raise SimuladoValidationError("códigos de área repetidos")

    if not items:
        raise SimuladoValidationError("cadastre ao menos um item")
    if sorted(item.position for item in items) != list(range(1, len(items) + 1)):
        raise SimuladoValidationError(
            "as posições do gabarito devem ser 1..N, sem buracos nem repetições")

    known = set(codes)
    anchor_keys: set[str] = set()
    clean_items: list[AnswerKeyEntryInput] = []
    for item in items:
        area_code = (item.area_code or "").strip().upper()
        option = (item.correct_option or "").strip().upper()
        anchor_key = (item.anchor_key or "").strip() or None
        if area_code not in known:
            raise SimuladoValidationError(
                f"item {item.position}: área {area_code!r} não cadastrada neste simulado")
        if option not in VALID_OPTIONS:
            raise SimuladoValidationError(
                f"item {item.position}: alternativa correta {option!r} inválida "
                f"(use {'/'.join(VALID_OPTIONS)})")
        if item.is_anchor:
            if not anchor_key:
                raise SimuladoValidationError(
                    f"item {item.position}: item-âncora exige anchor_key - é a identidade "
                    "estável que liga a mesma questão entre simulados (spec §3)")
            if anchor_key in anchor_keys:
                raise SimuladoValidationError(
                    f"anchor_key {anchor_key!r} repetido no mesmo simulado")
            anchor_keys.add(anchor_key)
        elif anchor_key:
            raise SimuladoValidationError(
                f"item {item.position}: anchor_key só faz sentido em item marcado como âncora")
        clean_items.append(AnswerKeyEntryInput(
            position=item.position, area_code=area_code, correct_option=option,
            is_anchor=bool(item.is_anchor), anchor_key=anchor_key))

    clean_items.sort(key=lambda entry: entry.position)
    return clean_areas, clean_items


class SimuladoService:
    def __init__(self, session: AsyncSession) -> None:
        self._session = session

    # ------------------------------------------------------------ leitura

    async def get_exam(self, exam_id: uuid.UUID, *, requester: Requester) -> MockExam:
        require_coordination(requester)
        exam = await self._session.get(MockExam, exam_id)
        if exam is None:
            raise SimuladoNotFoundError("simulado não encontrado")
        if str(exam.school_id) != str(requester.school_id):
            raise SimuladoAuthError("este simulado pertence a outra escola")
        return exam

    async def list_exams(
        self, *, requester: Requester, academic_year_id: uuid.UUID | None = None
    ) -> list[ExamSummary]:
        require_coordination(requester)
        stmt = (
            select(MockExam, func.count(MockExamItem.id))
            .outerjoin(MockExamItem, MockExamItem.mock_exam_id == MockExam.id)
            .where(MockExam.school_id == uuid.UUID(str(requester.school_id)))
            .group_by(MockExam.id)
            .order_by(MockExam.created_at.desc())
        )
        if academic_year_id is not None:
            stmt = stmt.where(MockExam.academic_year_id == academic_year_id)
        rows = (await self._session.execute(stmt)).all()
        return [ExamSummary(exam=row[0], item_count=int(row[1] or 0)) for row in rows]

    async def load_answer_key(
        self, exam_id: uuid.UUID, *, requester: Requester
    ) -> tuple[list[MockExamArea], list[MockExamItem]]:
        exam = await self.get_exam(exam_id, requester=requester)
        areas = list((await self._session.execute(
            select(MockExamArea)
            .where(MockExamArea.mock_exam_id == exam.id)
            .order_by(MockExamArea.display_order, MockExamArea.code))).scalars().all())
        items = list((await self._session.execute(
            select(MockExamItem)
            .where(MockExamItem.mock_exam_id == exam.id)
            .order_by(MockExamItem.position))).scalars().all())
        return areas, items

    # ------------------------------------------------------------ escrita

    async def create_exam(
        self,
        *,
        requester: Requester,
        school_id: uuid.UUID,
        academic_year_id: uuid.UUID,
        name: str,
        exam_day: int,
        application_date: date | None,
    ) -> MockExam:
        require_coordination(requester)
        if str(requester.school_id) != str(school_id):
            raise SimuladoAuthError("não é possível criar simulado em outra escola")
        clean_name = (name or "").strip()
        if not clean_name:
            raise SimuladoValidationError("o nome do simulado é obrigatório")
        if exam_day not in (1, 2):
            raise SimuladoValidationError("exam_day deve ser 1 ou 2 (modelo ENEM)")
        exam = MockExam(
            school_id=school_id,
            academic_year_id=academic_year_id,
            name=clean_name,
            exam_day=exam_day,
            application_date=application_date,
            status=STATUS_DRAFT,
        )
        self._session.add(exam)
        await self._session.commit()
        return exam

    async def register_answer_key(
        self,
        exam_id: uuid.UUID,
        *,
        requester: Requester,
        areas: Sequence[AreaInput],
        items: Sequence[AnswerKeyEntryInput],
    ) -> MockExam:
        """Cadastra (ou recadastra por inteiro) áreas e gabarito.

        Só em DRAFT: depois de PRINTED existem cartões impressos contra este
        gabarito, e trocar a alternativa correta embaixo deles mudaria a nota
        de todo mundo sem deixar rastro.
        """
        exam = await self.get_exam(exam_id, requester=requester)
        if exam.status != STATUS_DRAFT:
            raise SimuladoStateError(
                f"o gabarito só pode ser cadastrado com o simulado em DRAFT "
                f"(status atual: {exam.status})")
        clean_areas, clean_items = _normalize_answer_key(areas, items)

        await self._session.execute(
            delete(MockExamItem).where(MockExamItem.mock_exam_id == exam.id))
        await self._session.execute(
            delete(MockExamArea).where(MockExamArea.mock_exam_id == exam.id))
        # school_id explícito em cada linha filha: spec §3.0 exige a coluna em
        # toda tabela do subsistema, e as FKs são compostas (school_id, parent_id).
        for area in clean_areas:
            self._session.add(MockExamArea(
                school_id=exam.school_id, mock_exam_id=exam.id, code=area.code,
                label=area.label, display_order=area.display_order))
        for entry in clean_items:
            self._session.add(MockExamItem(
                school_id=exam.school_id, mock_exam_id=exam.id, position=entry.position,
                area_code=entry.area_code, correct_option=entry.correct_option,
                is_anchor=entry.is_anchor, anchor_key=entry.anchor_key,
                source_question_version_id=None))
        await self._session.commit()
        return exam


__all__ = [
    "STATUS_APPLIED",
    "STATUS_CALIBRATED",
    "STATUS_DRAFT",
    "STATUS_PRINTED",
    "STATUS_PUBLISHED",
    "STATUS_SCANNED",
    # Reexportação de simulado_scoring, não definição própria: o caminho de
    # import `from ...simulado_service import VALID_OPTIONS` continua a
    # resolver, e resolve para o mesmo objeto.
    "VALID_OPTIONS",
    "AnswerKeyEntryInput",
    "AreaInput",
    "ExamSummary",
    "SimuladoAuthError",
    "SimuladoNotFoundError",
    "SimuladoService",
    "SimuladoStateError",
    "SimuladoValidationError",
    "BLOCKING_SCAN_STATUSES",
    "require_coordination",
]
```

- [ ] **Step 4: Rodar e ver que só o teste de ciclo de vida falha**

Run: `python -m pytest tests/test_simulado_service.py -v`
Expected: 19 PASS e 1 FAIL — `test_answer_key_is_refused_once_the_exam_left_draft` com `AttributeError: 'SimuladoService' object has no attribute 'transition'`. É a Task 3.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/simulado_service.py tests/test_simulado_service.py
git commit -m "feat(simulados): criacao do simulado e cadastro do gabarito

Fase 2 Task 2. Areas sao dado do simulado (nao enum no codigo), posicoes
1..N sem buracos, alternativa A-E, ancora exige anchor_key unico. Recadastro
substitui o gabarito inteiro e so e aceito em DRAFT. Autorizacao reusa o
Requester.is_privileged de question_list_store.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: Ciclo de vida e trava de publicação

**Files:**
- Modify: `src/agente_ia_edu/services/simulado_service.py` (adicionar `transition` e `_assert_publishable` à classe `SimuladoService`)
- Test: `tests/test_simulado_service.py` (acrescentar a classe abaixo)

**Interfaces:**
- Consumes: `SimuladoService`, `SimuladoStateError`, `BLOCKING_SCAN_STATUSES`, constantes de status e `AnswerCardScan`/`MockExamResponse`/`MockExamWorkflowAudit` (Task 2).
- Produces: `SimuladoService.transition(exam_id, *, requester, to_status, note=None) -> MockExam`

- [ ] **Step 1: Escrever o teste que falha**

Acrescente ao fim de `tests/test_simulado_service.py`, antes do `if __name__`:

```python
class LifecycleTests(SimuladoServiceTestCase):
    def _transition(self, exam_id, to_status, requester=None, note=None):
        return self._run(lambda service, _s: service.transition(
            exam_id, requester=requester or self.coord, to_status=to_status, note=note))

    def _audit_rows(self, exam_id):
        async def _do():
            async with self.factory() as session:
                rows = (await session.execute(
                    select(MockExamWorkflowAudit)
                    .where(MockExamWorkflowAudit.mock_exam_id == exam_id)
                    .order_by(MockExamWorkflowAudit.occurred_at))).scalars().all()
                return [(row.from_status, row.to_status, row.actor_external_id,
                         row.note, row.school_id) for row in rows]
        return asyncio.run(_do())

    def _apply_one_response(self, exam_id):
        """Uma resposta crua, inserida direto: a digitação de verdade é a
        Task 4; aqui só interessa que EXISTA resposta para a publicação."""
        async def _do():
            async with self.factory() as session:
                item = (await session.execute(
                    select(MockExamItem).where(MockExamItem.mock_exam_id == exam_id)
                    .order_by(MockExamItem.position))).scalars().first()
                exam = await session.get(MockExam, exam_id)
                session.add(MockExamResponse(
                    school_id=exam.school_id, mock_exam_id=exam_id,
                    student_id=uuid.uuid4(), item_id=item.id,
                    chosen_option="A", is_correct=True))
                await session.commit()
        asyncio.run(_do())

    def test_draft_to_printed_requires_an_answer_key(self):
        exam = self._create_exam()
        with self.assertRaises(SimuladoStateError):
            self._transition(exam.id, "PRINTED")

    def test_the_happy_path_walks_draft_printed_applied_published(self):
        exam = self._create_exam()
        self._with_answer_key(exam.id)
        self.assertEqual(self._transition(exam.id, "PRINTED").status, "PRINTED")
        self.assertEqual(self._transition(exam.id, "APPLIED").status, "APPLIED")
        self._apply_one_response(exam.id)
        self.assertEqual(self._transition(exam.id, "PUBLISHED").status, "PUBLISHED")

    def test_publishing_without_a_single_typed_response_is_refused(self):
        exam = self._create_exam()
        self._with_answer_key(exam.id)
        self._transition(exam.id, "PRINTED")
        self._transition(exam.id, "APPLIED")
        with self.assertRaises(SimuladoStateError):
            self._transition(exam.id, "PUBLISHED")

    def _add_scan(self, exam_id, status, image_hash):
        async def _do():
            async with self.factory() as session:
                stored = await session.get(MockExam, exam_id)
                session.add(AnswerCardScan(
                    school_id=stored.school_id, mock_exam_id=exam_id,
                    answer_card_id=None, image_hash=image_hash,
                    storage_path=f"var/scans/{image_hash}.png", source="SCANNER",
                    status=status))
                await session.commit()
        asyncio.run(_do())

    def _ready_to_publish(self):
        exam = self._create_exam()
        self._with_answer_key(exam.id)
        self._transition(exam.id, "PRINTED")
        self._transition(exam.id, "APPLIED")
        self._apply_one_response(exam.id)
        return exam

    def test_publishing_is_blocked_by_every_unresolved_scan_status(self):
        # spec §4: PENDING, NEEDS_REVIEW e FAILED bloqueiam. FAILED também,
        # porque um cartão que o leitor não processou é um aluno ausente da
        # matriz - a matriz é o insumo da calibragem, e publicar sem ele
        # contamina a nota de todos, não só a dele.
        for status in ("PENDING", "NEEDS_REVIEW", "FAILED"):
            with self.subTest(scan_status=status):
                exam = self._ready_to_publish()
                self._add_scan(exam.id, status, f"hash-{status.lower()}")
                with self.assertRaises(SimuladoStateError) as ctx:
                    self._transition(exam.id, "PUBLISHED")
                self.assertIn("conferência", str(ctx.exception))

    def test_a_processed_scan_does_not_block_publication(self):
        exam = self._ready_to_publish()
        self._add_scan(exam.id, "PROCESSED", "hash-ok")
        self.assertEqual(self._transition(exam.id, "PUBLISHED").status, "PUBLISHED")

    def test_skipping_a_step_is_refused(self):
        exam = self._create_exam()
        self._with_answer_key(exam.id)
        with self.assertRaises(SimuladoStateError):
            self._transition(exam.id, "APPLIED")

    def test_going_backwards_is_refused(self):
        exam = self._create_exam()
        self._with_answer_key(exam.id)
        self._transition(exam.id, "PRINTED")
        with self.assertRaises(SimuladoStateError):
            self._transition(exam.id, "DRAFT")

    def test_scanned_and_calibrated_are_refused_naming_the_phase_that_owns_them(self):
        exam = self._create_exam()
        self._with_answer_key(exam.id)
        self._transition(exam.id, "PRINTED")
        self._transition(exam.id, "APPLIED")
        for status in ("SCANNED", "CALIBRATED"):
            with self.assertRaises(SimuladoStateError) as ctx:
                self._transition(exam.id, status)
            self.assertIn("fase", str(ctx.exception).lower())

    def test_unknown_status_is_refused(self):
        exam = self._create_exam()
        self._with_answer_key(exam.id)
        with self.assertRaises(SimuladoStateError):
            self._transition(exam.id, "QUALQUER_COISA")

    def test_teacher_cannot_transition(self):
        exam = self._create_exam()
        self._with_answer_key(exam.id)
        with self.assertRaises(SimuladoAuthError):
            self._transition(exam.id, "PRINTED", requester=self.teacher)

    def test_every_accepted_transition_writes_one_audit_row(self):
        exam = self._create_exam()
        self._with_answer_key(exam.id)
        self._transition(exam.id, "PRINTED")
        self._transition(exam.id, "APPLIED", note="prova aplicada no sábado")
        self._apply_one_response(exam.id)
        self._transition(exam.id, "PUBLISHED")

        rows = self._audit_rows(exam.id)
        self.assertEqual([(row[0], row[1]) for row in rows],
                         [("DRAFT", "PRINTED"), ("PRINTED", "APPLIED"),
                          ("APPLIED", "PUBLISHED")])
        self.assertEqual(rows[1][3], "prova aplicada no sábado")
        self.assertIsNone(rows[0][3])
        self.assertTrue(all(str(row[4]) == str(self.school_a) for row in rows))

    def test_the_audit_row_carries_the_callers_own_external_identifier(self):
        # Exatamente o identificador do chamador, sem tradução no meio: é o
        # que a camada HTTP autenticou, então nunca fica nulo (spec §3).
        exam = self._create_exam()
        self._with_answer_key(exam.id)
        self._transition(exam.id, "PRINTED")
        rows = self._audit_rows(exam.id)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0][2], "coord-a")

    def test_a_refused_transition_writes_no_audit_row(self):
        exam = self._create_exam()
        self._with_answer_key(exam.id)
        with self.assertRaises(SimuladoStateError):
            self._transition(exam.id, "PUBLISHED")
        self.assertEqual(self._audit_rows(exam.id), [])
```

E acrescente, no topo do arquivo de teste, `MockExamResponse` e `MockExamWorkflowAudit` ao
import de `agente_ia_edu.db.models.mock_exam`, mais a linha
`from agente_ia_edu.db.models.answer_card import AnswerCardScan` — a Fase 1 mantém o cartão
num módulo separado da prova.

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_simulado_service.py -k Lifecycle -v`
Expected: FAIL — `AttributeError: 'SimuladoService' object has no attribute 'transition'` em todos os 13.

- [ ] **Step 3: Implementação mínima**

Acrescente ao fim da classe `SimuladoService` em `src/agente_ia_edu/services/simulado_service.py`:

```python
    async def transition(
        self,
        exam_id: uuid.UUID,
        *,
        requester: Requester,
        to_status: str,
        note: str | None = None,
    ) -> MockExam:
        """Move o simulado pelo ciclo de vida de §4, com as travas da fase.

        A Fase 2 percorre DRAFT -> PRINTED -> APPLIED -> PUBLISHED. SCANNED e
        CALIBRATED existem no modelo mas não têm máquina que os produza
        ainda, então são recusados com o nome da fase que os traz - nunca
        aceitos como decoração.

        Toda transição ACEITA grava uma linha em ``mock_exam_workflow_audit``
        (spec §3): quem publicou a nota de um simulado, e quando, precisa
        estar registrado. Transição recusada não gera linha - o que se
        audita é o que aconteceu com a prova, não o que alguém tentou.
        """
        exam = await self.get_exam(exam_id, requester=requester)
        target = (to_status or "").strip().upper()

        if target in _DEFERRED_STATUSES:
            raise SimuladoStateError(
                f"status {target} depende do worker de OMR (fase 4) e da calibragem "
                "de TRI (fase 3); indisponível nesta fase, em que a nota é por acerto bruto")
        if target not in _ALLOWED_TRANSITIONS:
            raise SimuladoStateError(f"status {target!r} desconhecido")
        if target not in _ALLOWED_TRANSITIONS.get(exam.status, ()):
            raise SimuladoStateError(f"transição {exam.status} -> {target} não permitida")

        if target == STATUS_PRINTED:
            item_count = await self._session.scalar(
                select(func.count()).select_from(MockExamItem)
                .where(MockExamItem.mock_exam_id == exam.id))
            if not item_count:
                raise SimuladoStateError(
                    "cadastre o gabarito antes de imprimir os cartões")

        if target == STATUS_PUBLISHED:
            await self._assert_publishable(exam)

        previous_status = exam.status
        exam.status = target
        self._session.add(MockExamWorkflowAudit(
            school_id=exam.school_id,
            mock_exam_id=exam.id,
            from_status=previous_status,
            to_status=target,
            # O identificador externo do chamador, direto, sem resolução: é o
            # que a camada HTTP autenticou e sempre tem em mãos (spec §3).
            actor_external_id=requester.external_user_id,
            note=(note or "").strip() or None,
        ))
        await self._session.commit()
        return exam

    async def _assert_publishable(self, exam: MockExam) -> None:
        """Trava de integridade do spec §4.

        Publicar com parte dos alunos de fora não é "uma nota faltando": na
        fase de TRI distorce os parâmetros do item e contamina a nota de
        TODOS. Por isso a trava vale para CALIBRATED **e** para PUBLISHED - o
        efeito protegido é a publicação, CALIBRATED é só onde o dano nasce.

        O spec §4 manda escrevê-la e testá-la já nesta fase, mesmo sem
        leitura óptica: sem scan algum ela é vacuamente verdadeira, e é
        exatamente por isso que ela precisa nascer agora, em vez de depender
        de alguém lembrar dela quando o worker da fase 4 começar a produzir
        scans.
        """
        pending = await self._session.scalar(
            select(func.count()).select_from(AnswerCardScan).where(
                AnswerCardScan.mock_exam_id == exam.id,
                AnswerCardScan.status.in_(BLOCKING_SCAN_STATUSES),
            ))
        if pending:
            raise SimuladoStateError(
                f"{pending} cartão(ões) pendente(s) de conferência ou com falha de "
                "leitura: publicar agora distorceria a nota de todos os alunos, não "
                "só a deles (spec §4). Leia ou digite cada cartão antes de publicar.")

        answered = await self._session.scalar(
            select(func.count(func.distinct(MockExamResponse.student_id)))
            .where(MockExamResponse.mock_exam_id == exam.id))
        if not answered:
            raise SimuladoStateError(
                "nenhuma resposta digitada neste simulado: não há o que publicar")
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_simulado_service.py -v`
Expected: PASS — 33 testes (20 da Task 2 + 13 desta).

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/simulado_service.py tests/test_simulado_service.py
git commit -m "feat(simulados): ciclo de vida, auditoria e trava de publicacao

Fase 2 Task 3. DRAFT->PRINTED->APPLIED->PUBLISHED; SCANNED e CALIBRATED
recusados nomeando a fase que os traz. Toda transicao aceita grava
mock_exam_workflow_audit com from/to/ator/nota, o ator sendo o identificador
externo do chamador - sem resolucao que possa devolver nulo. Publicacao exige gabarito,
ao menos uma resposta digitada e zero scans em PENDING/NEEDS_REVIEW/FAILED
(spec secao 4, que estende a trava de CALIBRATED tambem para PUBLISHED).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: Digitação manual de respostas (`simulado_responses.py`)

Este caminho **não é andaime**: o spec §9 diz explicitamente que a digitação permanece como exceção permanente (cartão rasgado, aluno que preencheu a lápis, scanner quebrado). Ele é escrito para durar.

**Files:**
- Create: `src/agente_ia_edu/services/simulado_responses.py`
- Test: `tests/test_simulado_manual_entry.py`

**Interfaces:**
- Consumes: `STATUS_APPLIED`, `VALID_OPTIONS`, `SimuladoStateError`, `SimuladoValidationError` (Task 2); `MockExam`, `MockExamItem`, `MockExamResponse`; `Class`, `Person`, `Student`, `StudentEnrollment`.
- Produces:
  - `ManualResponseInput(position: int, chosen_option: str | None)`
  - `ManualEntryResult(student_id: uuid.UUID, created: int, replaced: int, total_items: int, total_correct: int)`
  - `ExamClass(class_id: uuid.UUID, class_name: str, student_count: int, typed_count: int)`
  - `RosterStudent(student_id: uuid.UUID, full_name: str, student_code: str | None, class_id: uuid.UUID, class_name: str, has_responses: bool)`
  - `async list_exam_classes(session, *, exam) -> list[ExamClass]`
  - `async list_exam_roster(session, *, exam, class_id) -> list[RosterStudent]`
  - `async enter_student_responses(session, *, exam, student_id, responses, entered_by_external_id) -> ManualEntryResult`
  - constante `RESPONSE_SOURCE_MANUAL = "MANUAL"`

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_simulado_manual_entry.py`:

```python
"""Digitação manual das respostas de um aluno (Fase 2, Task 4).

O spec §9 é explícito: a digitação NÃO é andaime descartável, é o caminho de
exceção permanente. Os testes tratam dela como tal - idempotência, recusa de
aluno fora da matrícula, recusa de gabarito parcial.

Fixture acadêmica com a receita real já usada em
tests/test_frontend_r_student_essay_prompts_route.py:65-104
(School -> Segment -> GradeLevel -> AcademicYear -> Class -> Person -> User
-> Student -> StudentEnrollment).
"""

import asyncio
import unittest
import uuid

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    GradeLevel,
    Person,
    School,
    Segment,
    Student,
    StudentEnrollment,
    User,
)
from agente_ia_edu.db.models.mock_exam import MockExam, MockExamArea, MockExamItem, MockExamResponse
from agente_ia_edu.services.simulado_responses import (
    ManualResponseInput,
    enter_student_responses,
    list_exam_classes,
    list_exam_roster,
)
from agente_ia_edu.services.simulado_service import SimuladoStateError, SimuladoValidationError

GABARITO = ["A", "B", "C", "D"]


def _make_student(session, *, school_id, class_id, name, code):
    person = Person(id=uuid.uuid4(), school_id=school_id, full_name=name)
    session.add(person)
    student = Student(id=uuid.uuid4(), school_id=school_id, person_id=person.id,
                      student_code=code)
    session.add(student)
    session.add(User(id=uuid.uuid4(), school_id=school_id, person_id=person.id,
                     external_identity_provider="test", external_user_id=f"user-{code}"))
    session.add(StudentEnrollment(id=uuid.uuid4(), school_id=school_id,
                                  student_id=student.id, class_id=class_id, status="ACTIVE"))
    return student.id


class ManualEntryTests(unittest.TestCase):
    def setUp(self):
        async def _setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                school = School(id=uuid.uuid4(), code="SIM-MAN", name="Escola Simulado")
                session.add(school)
                await session.flush()
                segment = Segment(id=uuid.uuid4(), school_id=school.id, name="EM",
                                  external_id="SEG-SIM")
                year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026,
                                    external_id="YEAR-SIM")
                other_year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2025,
                                          external_id="YEAR-SIM-OLD")
                session.add_all([segment, year, other_year])
                await session.flush()
                grade = GradeLevel(id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
                                   name="3a serie", external_id="GRADE-SIM")
                session.add(grade)
                await session.flush()
                class_a = Class(id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
                                grade_level_id=grade.id, name="3A", external_id="TURMA-3A")
                class_b = Class(id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
                                grade_level_id=grade.id, name="3B", external_id="TURMA-3B")
                class_old = Class(id=uuid.uuid4(), school_id=school.id,
                                  academic_year_id=other_year.id, grade_level_id=grade.id,
                                  name="3A-2025", external_id="TURMA-3A-2025")
                session.add_all([class_a, class_b, class_old])
                await session.flush()

                alice = _make_student(session, school_id=school.id, class_id=class_a.id,
                                      name="Alice", code="ST-A")
                bruno = _make_student(session, school_id=school.id, class_id=class_a.id,
                                      name="Bruno", code="ST-B")
                carla = _make_student(session, school_id=school.id, class_id=class_b.id,
                                      name="Carla", code="ST-C")
                # Matriculada só no ano letivo de 2025: fora deste simulado.
                davi = _make_student(session, school_id=school.id, class_id=class_old.id,
                                     name="Davi", code="ST-D")

                exam = MockExam(id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
                                name="Simulado 1", exam_day=1, status="APPLIED")
                session.add(exam)
                await session.flush()
                session.add(MockExamArea(school_id=school.id, mock_exam_id=exam.id,
                                         code="LC", label="Linguagens", display_order=1))
                for position, option in enumerate(GABARITO, start=1):
                    session.add(MockExamItem(school_id=school.id, mock_exam_id=exam.id,
                                             position=position, area_code="LC",
                                             correct_option=option,
                                             is_anchor=False, anchor_key=None))
                await session.commit()
                return (engine, factory, exam.id, class_a.id, class_b.id,
                        alice, bruno, carla, davi)

        (self.engine, self.factory, self.exam_id, self.class_a, self.class_b,
         self.alice, self.bruno, self.carla, self.davi) = asyncio.run(_setup())

    def tearDown(self):
        asyncio.run(self.engine.dispose())

    def _with_session(self, coroutine_factory):
        async def _do():
            async with self.factory() as session:
                exam = await session.get(MockExam, self.exam_id)
                return await coroutine_factory(session, exam)
        return asyncio.run(_do())

    def _type(self, student_id, options, typed_by="coord-ana"):
        return self._with_session(lambda session, exam: enter_student_responses(
            session, exam=exam, student_id=student_id,
            responses=[ManualResponseInput(position=index, chosen_option=option)
                       for index, option in enumerate(options, start=1)],
            entered_by_external_id=typed_by))

    def _set_status(self, status):
        async def _do():
            async with self.factory() as session:
                exam = await session.get(MockExam, self.exam_id)
                exam.status = status
                await session.commit()
        asyncio.run(_do())

    # --------------------------------------------------------- digitação

    def test_typing_a_full_card_stores_one_row_per_item_with_is_correct(self):
        result = self._type(self.alice, ["A", "B", "E", None])
        self.assertEqual((result.created, result.replaced), (4, 0))
        self.assertEqual(result.total_items, 4)
        self.assertEqual(result.total_correct, 2)

        async def _read():
            async with self.factory() as session:
                rows = (await session.execute(
                    select(MockExamResponse, MockExamItem.position)
                    .join(MockExamItem, MockExamItem.id == MockExamResponse.item_id)
                    .where(MockExamResponse.student_id == self.alice)
                    .order_by(MockExamItem.position))).all()
                return [(position, row.chosen_option, row.is_correct) for row, position in rows]

        self.assertEqual(asyncio.run(_read()), [
            (1, "A", True), (2, "B", True), (3, "E", False), (4, None, False)])

    def test_retyping_replaces_the_previous_entry_instead_of_duplicating(self):
        self._type(self.alice, ["A", "B", "E", None])
        result = self._type(self.alice, ["A", "B", "C", "D"])
        self.assertEqual((result.created, result.replaced), (4, 4))
        self.assertEqual(result.total_correct, 4)

        async def _count():
            async with self.factory() as session:
                return await session.scalar(
                    select(func.count()).select_from(MockExamResponse)
                    .where(MockExamResponse.student_id == self.alice))

        self.assertEqual(asyncio.run(_count()), 4)

    def test_lowercase_input_is_normalised(self):
        result = self._type(self.alice, ["a", "b", "c", "d"])
        self.assertEqual(result.total_correct, 4)

    def _provenance_of(self, student_id):
        async def _read():
            async with self.factory() as session:
                rows = (await session.execute(
                    select(MockExamResponse)
                    .where(MockExamResponse.student_id == student_id))).scalars().all()
                return [(row.source, row.entered_by_external_id, row.created_at is not None)
                        for row in rows]
        return asyncio.run(_read())

    def test_every_typed_row_records_MANUAL_provenance_and_its_author(self):
        # spec §3: sem source e sem autor, uma resposta digitada à mão fica
        # indistinguível de uma lida pelo scanner, e não há como auditar quem
        # digitou o cartão quando a nota for contestada.
        self._type(self.alice, ["A", "B", "C", "D"])

        rows = self._provenance_of(self.alice)
        self.assertEqual(len(rows), 4)
        self.assertEqual({row[0] for row in rows}, {"MANUAL"})
        self.assertEqual({row[1] for row in rows}, {"coord-ana"})
        self.assertTrue(all(row[2] for row in rows))

    def test_retyping_rewrites_the_provenance_to_the_new_author(self):
        # O segundo operador é quem responde por estas respostas agora - manter
        # o autor anterior apontaria para quem não digitou estas linhas.
        self._type(self.alice, ["A", "B", "C", "D"])
        self._type(self.alice, ["A", "B", "E", None], typed_by="coord-bruno")

        rows = self._provenance_of(self.alice)
        self.assertEqual({row[1] for row in rows}, {"coord-bruno"})
        self.assertEqual({row[0] for row in rows}, {"MANUAL"})

    def test_a_missing_position_is_refused_blank_must_be_typed_explicitly(self):
        with self.assertRaises(SimuladoValidationError) as ctx:
            self._with_session(lambda session, exam: enter_student_responses(
                session, exam=exam, student_id=self.alice,
                responses=[ManualResponseInput(position=1, chosen_option="A"),
                           ManualResponseInput(position=2, chosen_option="B")]))
        self.assertIn("branco", str(ctx.exception))

    def test_a_repeated_position_is_refused(self):
        with self.assertRaises(SimuladoValidationError):
            self._with_session(lambda session, exam: enter_student_responses(
                session, exam=exam, student_id=self.alice,
                responses=[ManualResponseInput(position=1, chosen_option="A")] * 4))

    def test_a_position_outside_the_answer_key_is_refused(self):
        with self.assertRaises(SimuladoValidationError):
            self._type(self.alice, ["A", "B", "C", "D", "E"])

    def test_an_invalid_option_is_refused(self):
        with self.assertRaises(SimuladoValidationError):
            self._type(self.alice, ["A", "B", "C", "Z"])

    def test_a_student_without_an_active_enrollment_in_this_year_is_refused(self):
        with self.assertRaises(SimuladoValidationError) as ctx:
            self._type(self.davi, ["A", "B", "C", "D"])
        self.assertIn("matrícula", str(ctx.exception))

    def test_typing_before_the_exam_was_applied_is_refused(self):
        self._set_status("PRINTED")
        with self.assertRaises(SimuladoStateError):
            self._type(self.alice, ["A", "B", "C", "D"])

    def test_typing_after_publication_is_refused(self):
        self._set_status("PUBLISHED")
        with self.assertRaises(SimuladoStateError):
            self._type(self.alice, ["A", "B", "C", "D"])

    # ------------------------------------------------------ turmas/roster

    def test_list_exam_classes_only_sees_classes_of_the_exams_academic_year(self):
        self._type(self.alice, ["A", "B", "C", "D"])
        classes = self._with_session(lambda session, exam: list_exam_classes(session, exam=exam))
        self.assertEqual([(c.class_name, c.student_count, c.typed_count) for c in classes],
                         [("3A", 2, 1), ("3B", 1, 0)])

    def test_roster_lists_the_class_by_name_and_flags_who_is_already_typed(self):
        self._type(self.bruno, ["A", "B", "C", "D"])
        roster = self._with_session(lambda session, exam: list_exam_roster(
            session, exam=exam, class_id=self.class_a))
        self.assertEqual([(s.full_name, s.student_code, s.has_responses) for s in roster],
                         [("Alice", "ST-A", False), ("Bruno", "ST-B", True)])
        self.assertTrue(all(str(s.class_id) == str(self.class_a) for s in roster))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_simulado_manual_entry.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.services.simulado_responses'`

- [ ] **Step 3: Implementação mínima**

Crie `src/agente_ia_edu/services/simulado_responses.py`:

```python
"""Digitação manual das respostas de um cartão (spec §9, fatia 2).

Este módulo NÃO é andaime a ser jogado fora quando o OMR da fase 4 chegar. O
próprio spec §9 diz que ele permanece como caminho de exceção: cartão
rasgado, aluno que preencheu a lápis, scanner quebrado no dia. É por isso
que ele valida com o mesmo rigor que o leitor óptico terá - e, como o
leitor, ele nunca chuta.

Três decisões que o código impõe:

1. **O cartão inteiro, sempre.** Digitar só as questões respondidas parece
   economia, mas um cartão parcialmente digitado é indistinguível, no banco,
   de um cartão em que o aluno deixou o resto em branco - e a diferença muda
   a proporção de acerto do item. Branco é digitado explicitamente.
2. **Idempotente por (simulado, aluno).** Redigitar apaga a digitação
   anterior inteira e grava de novo. O operador que percebeu que trocou a
   linha refaz o cartão sem gerar um segundo conjunto de respostas.
3. **Só aluno com matrícula ATIVA numa turma do ano letivo do simulado.** É
   a mesma cadeia Student -> StudentEnrollment -> Class que
   services/student_enrollment_resolution.py já percorre, e é o que impede
   uma resposta de aluno de outra escola ou de outro ano de entrar na
   matriz.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import Class, Person, Student, StudentEnrollment
from agente_ia_edu.db.models.mock_exam import MockExam, MockExamItem, MockExamResponse
from agente_ia_edu.services.simulado_service import (
    STATUS_APPLIED,
    VALID_OPTIONS,
    SimuladoStateError,
    SimuladoValidationError,
)

# `mock_exam_responses.source` (spec §3). A outra constante possível, "OMR", é
# escrita pelo worker da fase 4 - este módulo nunca a usa.
RESPONSE_SOURCE_MANUAL = "MANUAL"


@dataclass(frozen=True)
class ManualResponseInput:
    """`chosen_option` None (ou string vazia) é branco, explicitamente."""

    position: int
    chosen_option: str | None = None


@dataclass(frozen=True)
class ManualEntryResult:
    student_id: uuid.UUID
    created: int
    replaced: int
    total_items: int
    total_correct: int


@dataclass(frozen=True)
class ExamClass:
    class_id: uuid.UUID
    class_name: str
    student_count: int
    typed_count: int


@dataclass(frozen=True)
class RosterStudent:
    student_id: uuid.UUID
    full_name: str
    student_code: str | None
    class_id: uuid.UUID
    class_name: str
    has_responses: bool


async def list_exam_classes(session: AsyncSession, *, exam: MockExam) -> list[ExamClass]:
    """Turmas da escola no ano letivo do simulado, com quantos alunos têm e
    quantos já foram digitados. É o que a coordenação usa para saber onde
    ainda falta trabalho."""
    roster_rows = (await session.execute(
        select(Class.id, Class.name, func.count(func.distinct(StudentEnrollment.student_id)))
        .join(StudentEnrollment, StudentEnrollment.class_id == Class.id)
        .where(
            Class.school_id == exam.school_id,
            Class.academic_year_id == exam.academic_year_id,
            StudentEnrollment.status == "ACTIVE",
        )
        .group_by(Class.id, Class.name)
        .order_by(Class.name)
    )).all()

    typed_rows = (await session.execute(
        select(Class.id, func.count(func.distinct(MockExamResponse.student_id)))
        .join(StudentEnrollment, StudentEnrollment.class_id == Class.id)
        .join(MockExamResponse, MockExamResponse.student_id == StudentEnrollment.student_id)
        .where(
            Class.school_id == exam.school_id,
            Class.academic_year_id == exam.academic_year_id,
            StudentEnrollment.status == "ACTIVE",
            MockExamResponse.mock_exam_id == exam.id,
        )
        .group_by(Class.id)
    )).all()
    typed_by_class = {row[0]: int(row[1] or 0) for row in typed_rows}

    return [
        ExamClass(class_id=row[0], class_name=row[1], student_count=int(row[2] or 0),
                  typed_count=typed_by_class.get(row[0], 0))
        for row in roster_rows
    ]


async def list_exam_roster(
    session: AsyncSession, *, exam: MockExam, class_id: uuid.UUID
) -> list[RosterStudent]:
    """Alunos de UMA turma, em ordem alfabética, já marcando quem tem
    resposta gravada."""
    rows = (await session.execute(
        select(Student.id, Person.full_name, Student.student_code, Class.id, Class.name)
        .join(Person, Person.id == Student.person_id)
        .join(StudentEnrollment, StudentEnrollment.student_id == Student.id)
        .join(Class, Class.id == StudentEnrollment.class_id)
        .where(
            Student.school_id == exam.school_id,
            StudentEnrollment.status == "ACTIVE",
            Class.academic_year_id == exam.academic_year_id,
            Class.id == class_id,
        )
        .order_by(Person.full_name)
    )).all()

    typed = set((await session.execute(
        select(func.distinct(MockExamResponse.student_id))
        .where(MockExamResponse.mock_exam_id == exam.id))).scalars().all())

    return [
        RosterStudent(student_id=row[0], full_name=row[1], student_code=row[2],
                      class_id=row[3], class_name=row[4], has_responses=row[0] in typed)
        for row in rows
    ]


async def _has_active_enrollment(
    session: AsyncSession, *, exam: MockExam, student_id: uuid.UUID
) -> bool:
    found = await session.scalar(
        select(StudentEnrollment.id)
        .join(Class, Class.id == StudentEnrollment.class_id)
        .where(
            StudentEnrollment.school_id == exam.school_id,
            StudentEnrollment.student_id == student_id,
            StudentEnrollment.status == "ACTIVE",
            Class.academic_year_id == exam.academic_year_id,
        )
        .limit(1)
    )
    return found is not None


async def enter_student_responses(
    session: AsyncSession,
    *,
    exam: MockExam,
    student_id: uuid.UUID,
    responses: Sequence[ManualResponseInput],
    entered_by_external_id: str | None,
) -> ManualEntryResult:
    """Grava o cartão inteiro de um aluno, substituindo o que já houvesse.

    `exam` chega já autorizado pela rota (SimuladoService.get_exam), para que
    a regra de quem pode mexer em qual simulado viva num lugar só.

    Toda linha gravada aqui leva `source='MANUAL'` e o identificador externo
    de quem digitou, direto do chamador, sem resolução no meio (spec §3).
    Essa procedência é o que permite responder "quem digitou este cartão?"
    quando a nota de um aluno for contestada, e é o que distingue esta linha
    de uma que o leitor óptico da fase 4 vai gravar com `source='OMR'` e
    autor nulo.
    """
    if exam.status != STATUS_APPLIED:
        raise SimuladoStateError(
            f"a digitação só é aceita com o simulado em APPLIED (status atual: {exam.status}): "
            "antes disso não houve aplicação, e depois de PUBLISHED o boletim já saiu")

    if not await _has_active_enrollment(session, exam=exam, student_id=student_id):
        raise SimuladoValidationError(
            "aluno sem matrícula ativa em turma do ano letivo deste simulado")

    items = list((await session.execute(
        select(MockExamItem).where(MockExamItem.mock_exam_id == exam.id)
        .order_by(MockExamItem.position))).scalars().all())
    if not items:
        raise SimuladoStateError("simulado sem gabarito cadastrado")
    item_by_position = {item.position: item for item in items}

    given: dict[int, str | None] = {}
    for entry in responses:
        if entry.position not in item_by_position:
            raise SimuladoValidationError(
                f"posição {entry.position} não existe neste simulado")
        if entry.position in given:
            raise SimuladoValidationError(
                f"posição {entry.position} digitada duas vezes")
        option = (entry.chosen_option or "").strip().upper() or None
        if option is not None and option not in VALID_OPTIONS:
            raise SimuladoValidationError(
                f"posição {entry.position}: alternativa {option!r} inválida "
                f"(use {'/'.join(VALID_OPTIONS)} ou branco)")
        given[entry.position] = option

    missing = sorted(set(item_by_position) - set(given))
    if missing:
        shown = ", ".join(str(position) for position in missing[:10])
        suffix = "..." if len(missing) > 10 else ""
        raise SimuladoValidationError(
            f"faltam as posições {shown}{suffix}: uma questão deixada em branco precisa ser "
            "digitada explicitamente como branco, para não ficar indistinguível de cartão "
            "digitado pela metade")

    existing = list((await session.execute(
        select(MockExamResponse).where(
            MockExamResponse.mock_exam_id == exam.id,
            MockExamResponse.student_id == student_id,
        ))).scalars().all())
    replaced = len(existing)
    for row in existing:
        await session.delete(row)
    await session.flush()

    total_correct = 0
    for position in sorted(given):
        option = given[position]
        item = item_by_position[position]
        is_correct = option is not None and option == item.correct_option
        if is_correct:
            total_correct += 1
        session.add(MockExamResponse(
            school_id=exam.school_id, mock_exam_id=exam.id, student_id=student_id,
            item_id=item.id, chosen_option=option, is_correct=is_correct,
            source=RESPONSE_SOURCE_MANUAL, entered_by_external_id=entered_by_external_id))
    await session.commit()

    return ManualEntryResult(
        student_id=student_id, created=len(given), replaced=replaced,
        total_items=len(items), total_correct=total_correct)


__all__ = [
    "RESPONSE_SOURCE_MANUAL",
    "ExamClass",
    "ManualEntryResult",
    "ManualResponseInput",
    "RosterStudent",
    "enter_student_responses",
    "list_exam_classes",
    "list_exam_roster",
]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_simulado_manual_entry.py -v`
Expected: PASS — 14 testes.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/simulado_responses.py tests/test_simulado_manual_entry.py
git commit -m "feat(simulados): digitacao manual de respostas

Fase 2 Task 4. Caminho de excecao permanente (spec secao 9), nao andaime.
Cartao inteiro obrigatorio (branco e digitado), idempotente por
(simulado, aluno), so aluno com matricula ativa no ano letivo do simulado.
Toda linha gravada leva source='MANUAL' e o identificador externo de quem
digitou (secao 3): sem procedencia nao ha como auditar quem digitou o cartao
de um aluno quando a nota for contestada. Inclui as leituras de turma e roster que a tela consome.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: Relatórios — carregador comum e boletim do aluno

**Files:**
- Create: `src/agente_ia_edu/services/simulado_reports.py`
- Test: `tests/test_simulado_reports.py`

**Interfaces:**
- Consumes: `ItemDescriptor`, `ResponseFact`, `StudentRawScore`, `score_student` (Task 1); `SimuladoNotFoundError` (Task 2); modelos da Fase 1; `Class`, `StudentEnrollment`.
- Produces:
  - `ExamFacts` (dataclass exportada, consumida pelas Tasks 6 e 7) com `exam`, `areas`, `items`, `area_labels: dict[str, str]`, `area_item_counts: dict[str, int]`, `descriptors: tuple[ItemDescriptor, ...]`, `facts_by_student: dict[uuid.UUID, list[ResponseFact]]`, `scores_by_student: dict[uuid.UUID, StudentRawScore]`, `class_by_student: dict[uuid.UUID, tuple[uuid.UUID, str]]`, `unit_by_student: dict[uuid.UUID, tuple[uuid.UUID | None, str]]`
  - constantes `NO_CLASS_LABEL`, `NO_UNIT_LABEL`
  - `async load_exam_facts(session, *, exam_id) -> ExamFacts`
  - `AreaResult(area_code, label, correct, answered, total_items, percentage)`
  - `StudentItemResult(position, area_code, chosen_option, correct_option, is_correct, is_anchor)`
  - `StudentReport(exam_id, exam_name, exam_status, student_id, by_area, total_correct, total_answered, total_items, total_percentage, class_id, class_percentile, school_percentile, items)`
  - `async build_student_report(session, *, exam_id, student_id) -> StudentReport`

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_simulado_reports.py`:

```python
"""Os quatro relatórios de nota bruta (Fase 2, Tasks 5-7).

Cenário fixo, com todos os números conferidos à mão no docstring de
_EXPECTED abaixo. Um relatório errado não levanta exceção - ele devolve um
número plausível que vira boletim -, então os testes comparam contra a conta
feita fora do código, não contra o que o código produziu.

Gabarito: LC = posições 1,2,3 (A,B,C) | CH = posições 4,5 (D,E). A posição 5
é item-ÂNCORA: o boletim do aluno tem de esconder a alternativa correta dela
(spec §6.4).

Respostas (posições 1..5):
  Alice (3A): A B C D E -> LC 3/3, CH 2/2, total 5/5 = 100.0%
  Bruno (3A): A B E D A -> LC 2/3, CH 1/2, total 3/5 =  60.0%
  Carla (3B): A E E A A -> LC 1/3, CH 0/2, total 1/5 =  20.0%
  Davi  (3B): E E E . . -> LC 0/3, CH 0/2, total 0/5 =   0.0%   (. = branco)
"""

import asyncio
import unittest
import uuid

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    GradeLevel,
    Person,
    School,
    SchoolUnit,
    Segment,
    Student,
    StudentEnrollment,
)
from agente_ia_edu.db.models.mock_exam import (
    MockExam,
    MockExamArea,
    MockExamItem,
    MockExamResponse,
)
from agente_ia_edu.services.simulado_reports import build_student_report
from agente_ia_edu.services.simulado_service import SimuladoNotFoundError

ANSWER_KEY = {1: ("LC", "A"), 2: ("LC", "B"), 3: ("LC", "C"), 4: ("CH", "D"), 5: ("CH", "E")}
ANSWERS = {
    "Alice": ["A", "B", "C", "D", "E"],
    "Bruno": ["A", "B", "E", "D", "A"],
    "Carla": ["A", "E", "E", "A", "A"],
    "Davi": ["E", "E", "E", None, None],
}


class SimuladoReportsTestCase(unittest.TestCase):
    """Banco + escola + duas turmas + quatro alunos + simulado publicado."""

    def setUp(self):
        async def _setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                school = School(id=uuid.uuid4(), code="SIM-REP", name="Escola Relatorio")
                session.add(school)
                await session.flush()
                segment = Segment(id=uuid.uuid4(), school_id=school.id, name="EM",
                                  external_id="SEG-REP")
                year = AcademicYear(id=uuid.uuid4(), school_id=school.id, year=2026,
                                    external_id="YEAR-REP")
                session.add_all([segment, year])
                await session.flush()
                grade = GradeLevel(id=uuid.uuid4(), school_id=school.id, segment_id=segment.id,
                                   name="3a", external_id="GRADE-REP")
                session.add(grade)
                await session.flush()
                # 3A fica numa unidade real; 3B fica SEM unidade. As duas
                # situações convivem numa escola de verdade, e o relatório tem
                # de dizer isso em vez de inventar uma unidade para a 3B.
                unit = SchoolUnit(id=uuid.uuid4(), school_id=school.id,
                                  name="Campus Centro", external_id="UNIT-CENTRO")
                session.add(unit)
                await session.flush()
                classes = {}
                for name, unit_id in (("3A", unit.id), ("3B", None)):
                    klass = Class(id=uuid.uuid4(), school_id=school.id,
                                  academic_year_id=year.id, grade_level_id=grade.id,
                                  school_unit_id=unit_id,
                                  name=name, external_id=f"TURMA-{name}")
                    session.add(klass)
                    classes[name] = klass.id
                await session.flush()

                exam = MockExam(id=uuid.uuid4(), school_id=school.id, academic_year_id=year.id,
                                name="Simulado ENEM 1", exam_day=1, status="PUBLISHED")
                session.add(exam)
                await session.flush()
                session.add_all([
                    MockExamArea(school_id=school.id, mock_exam_id=exam.id, code="LC",
                                 label="Linguagens e Códigos", display_order=1),
                    MockExamArea(school_id=school.id, mock_exam_id=exam.id, code="CH",
                                 label="Ciências Humanas", display_order=2),
                ])
                items = {}
                for position, (area_code, correct) in ANSWER_KEY.items():
                    item = MockExamItem(id=uuid.uuid4(), school_id=school.id,
                                        mock_exam_id=exam.id,
                                        position=position, area_code=area_code,
                                        correct_option=correct, is_anchor=(position == 5),
                                        anchor_key="CH-ANCORA-1" if position == 5 else None)
                    session.add(item)
                    items[position] = item
                await session.flush()

                students = {}
                for name, class_name in (("Alice", "3A"), ("Bruno", "3A"),
                                         ("Carla", "3B"), ("Davi", "3B")):
                    person = Person(id=uuid.uuid4(), school_id=school.id, full_name=name)
                    session.add(person)
                    await session.flush()
                    student = Student(id=uuid.uuid4(), school_id=school.id,
                                      person_id=person.id, student_code=f"ST-{name[:1]}")
                    session.add(student)
                    session.add(StudentEnrollment(
                        id=uuid.uuid4(), school_id=school.id, student_id=student.id,
                        class_id=classes[class_name], status="ACTIVE"))
                    students[name] = student.id
                    for position, chosen in enumerate(ANSWERS[name], start=1):
                        session.add(MockExamResponse(
                            school_id=school.id, mock_exam_id=exam.id,
                            student_id=student.id,
                            item_id=items[position].id, chosen_option=chosen,
                            is_correct=chosen == ANSWER_KEY[position][1]))
                await session.commit()
                return engine, factory, exam.id, classes, students

        (self.engine, self.factory, self.exam_id, self.classes,
         self.students) = asyncio.run(_setup())

    def tearDown(self):
        asyncio.run(self.engine.dispose())

    def _report(self, coroutine_factory):
        async def _do():
            async with self.factory() as session:
                return await coroutine_factory(session)
        return asyncio.run(_do())


class StudentReportTests(SimuladoReportsTestCase):
    def _for(self, name):
        return self._report(lambda session: build_student_report(
            session, exam_id=self.exam_id, student_id=self.students[name]))

    def test_area_and_total_raw_scores_match_the_hand_computed_values(self):
        report = self._for("Bruno")
        self.assertEqual([(a.area_code, a.label, a.correct, a.total_items, a.percentage)
                          for a in report.by_area],
                         [("LC", "Linguagens e Códigos", 2, 3, 66.7),
                          ("CH", "Ciências Humanas", 1, 2, 50.0)])
        self.assertEqual(report.total_correct, 3)
        self.assertEqual(report.total_items, 5)
        self.assertEqual(report.total_percentage, 60.0)
        self.assertEqual(report.exam_name, "Simulado ENEM 1")
        self.assertEqual(report.exam_status, "PUBLISHED")

    def test_blanks_count_as_wrong_and_do_not_count_as_answered(self):
        report = self._for("Davi")
        self.assertEqual(report.total_correct, 0)
        self.assertEqual(report.total_answered, 3)
        self.assertEqual(report.total_percentage, 0.0)

    def test_percentiles_are_computed_against_school_and_own_class(self):
        self.assertEqual((self._for("Alice").school_percentile,
                          self._for("Bruno").school_percentile,
                          self._for("Carla").school_percentile,
                          self._for("Davi").school_percentile),
                         (75.0, 50.0, 25.0, 0.0))
        self.assertEqual(self._for("Alice").class_percentile, 50.0)
        self.assertEqual(self._for("Bruno").class_percentile, 0.0)
        self.assertEqual(str(self._for("Alice").class_id), str(self.classes["3A"]))

    def test_the_anchor_items_correct_option_is_never_revealed_to_the_student(self):
        report = self._for("Alice")
        by_position = {item.position: item for item in report.items}
        self.assertTrue(by_position[5].is_anchor)
        self.assertIsNone(by_position[5].correct_option)
        self.assertTrue(by_position[5].is_correct)   # o acerto continua contando
        self.assertFalse(by_position[4].is_anchor)
        self.assertEqual(by_position[4].correct_option, "D")

    def test_every_item_of_the_answer_key_appears_in_order(self):
        report = self._for("Davi")
        self.assertEqual([item.position for item in report.items], [1, 2, 3, 4, 5])
        self.assertIsNone({i.position: i for i in report.items}[4].chosen_option)

    def test_the_report_carries_no_score_evolution_series(self):
        # spec §7: sem equalização por âncoras, a evolução por nota absoluta é
        # SUPRIMIDA, não estimada. Nada aqui pode parecer uma série temporal.
        report = self._for("Alice")
        self.assertFalse(hasattr(report, "evolution"))
        self.assertFalse(hasattr(report, "history"))

    def test_a_student_without_typed_responses_is_a_lookup_error(self):
        with self.assertRaises(SimuladoNotFoundError):
            self._report(lambda session: build_student_report(
                session, exam_id=self.exam_id, student_id=uuid.uuid4()))

    def test_an_unknown_exam_is_a_lookup_error(self):
        with self.assertRaises(SimuladoNotFoundError):
            self._report(lambda session: build_student_report(
                session, exam_id=uuid.uuid4(), student_id=self.students["Alice"]))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_simulado_reports.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.services.simulado_reports'`

- [ ] **Step 3: Implementação mínima**

Crie `src/agente_ia_edu/services/simulado_reports.py`:

```python
"""Os quatro níveis de relatório do spec §7, na versão de NOTA BRUTA
(fatia 2 de §9): boletim do aluno, painel da turma, painel da escola e
análise de itens.

A conta em si não mora aqui: este módulo lê o banco, monta os fatos e
delega para services/simulado_scoring.py, que é puro e testado contra
números conferidos à mão. A separação é a mesma que o spec §2.2 exige do
tri_engine, e pela mesma razão - uma conta errada que vira nota não falha em
lugar nenhum, então ela precisa ser verificável isolada.

Duas obrigações do spec que este módulo carrega, e que NÃO são detalhe de
apresentação:

- §6.4: item-âncora não pode vazar. `build_student_report` devolve
  `correct_option = None` para todo item com `is_anchor`. O acerto continua
  contando na nota; o que some é a resposta certa. Os relatórios de
  professor e coordenação NÃO escondem nada - eles não são devolutiva ao
  aluno, e esconder o gabarito de quem precisa conferir a prova seria
  esconder exatamente o erro que §6.5 quer que apareça.
- §7: sem equalização por âncoras não existe evolução por nota absoluta. Não
  há, em nenhum dataclass daqui, série temporal de nota - ela é suprimida,
  não estimada.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Sequence

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from agente_ia_edu.db.models import Class, SchoolUnit, StudentEnrollment
from agente_ia_edu.db.models.mock_exam import (
    MockExam,
    MockExamArea,
    MockExamItem,
    MockExamResponse,
)
from agente_ia_edu.services.simulado_scoring import (
    ItemDescriptor,
    ResponseFact,
    StudentRawScore,
    score_student,
)
from agente_ia_edu.services.simulado_service import SimuladoNotFoundError

NO_CLASS_LABEL = "Sem turma no ano letivo"
# Class.school_unit_id é NULLABLE (db/models/academic.py:324) e muitas escolas
# não têm nenhuma SchoolUnit cadastrada - coordination_portal.py já trata esse
# caso como bucket explícito em vez de inventar uma unidade. Mesma escolha aqui.
NO_UNIT_LABEL = "Sem unidade cadastrada"


@dataclass(frozen=True)
class ExamFacts:
    """Tudo o que os quatro relatórios precisam, lido uma vez só.

    Um simulado de 1.500 alunos x 90 itens são ~135 mil linhas (spec §3) -
    uma leitura de tabela inteira, e depois só Python. Recarregar por
    relatório seria o mesmo trabalho quatro vezes.
    """

    exam: MockExam
    areas: tuple[MockExamArea, ...]
    items: tuple[MockExamItem, ...]
    area_labels: dict[str, str]
    area_item_counts: dict[str, int]
    descriptors: tuple[ItemDescriptor, ...]
    facts_by_student: dict[uuid.UUID, list[ResponseFact]]
    scores_by_student: dict[uuid.UUID, StudentRawScore]
    class_by_student: dict[uuid.UUID, tuple[uuid.UUID, str]]
    unit_by_student: dict[uuid.UUID, tuple[uuid.UUID | None, str]]


@dataclass(frozen=True)
class AreaResult:
    area_code: str
    label: str
    correct: int
    answered: int
    total_items: int
    percentage: float


@dataclass(frozen=True)
class StudentItemResult:
    position: int
    area_code: str
    chosen_option: str | None
    correct_option: str | None
    is_correct: bool
    is_anchor: bool


@dataclass(frozen=True)
class StudentReport:
    exam_id: uuid.UUID
    exam_name: str
    exam_status: str
    student_id: uuid.UUID
    by_area: tuple[AreaResult, ...]
    total_correct: int
    total_answered: int
    total_items: int
    total_percentage: float
    class_id: uuid.UUID | None
    class_percentile: float | None
    school_percentile: float
    items: tuple[StudentItemResult, ...]


async def load_exam_facts(session: AsyncSession, *, exam_id: uuid.UUID) -> ExamFacts:
    exam = await session.get(MockExam, exam_id)
    if exam is None:
        raise SimuladoNotFoundError("simulado não encontrado")

    areas = tuple((await session.execute(
        select(MockExamArea).where(MockExamArea.mock_exam_id == exam_id)
        .order_by(MockExamArea.display_order, MockExamArea.code))).scalars().all())
    items = tuple((await session.execute(
        select(MockExamItem).where(MockExamItem.mock_exam_id == exam_id)
        .order_by(MockExamItem.position))).scalars().all())

    area_labels = {area.code: area.label for area in areas}
    # Ordem das áreas = display_order. É a ordem em que o boletim e todos os
    # painéis listam as áreas, então ela nasce aqui e não se repete adiante.
    area_item_counts: dict[str, int] = {area.code: 0 for area in areas}
    for item in items:
        area_item_counts[item.area_code] = area_item_counts.get(item.area_code, 0) + 1

    area_by_item = {item.id: item.area_code for item in items}
    descriptors = tuple(
        ItemDescriptor(item_id=item.id, position=item.position,
                       area_code=item.area_code, correct_option=item.correct_option)
        for item in items
    )

    responses = (await session.execute(
        select(MockExamResponse).where(MockExamResponse.mock_exam_id == exam_id))).scalars().all()
    facts_by_student: dict[uuid.UUID, list[ResponseFact]] = {}
    for row in responses:
        area_code = area_by_item.get(row.item_id)
        if area_code is None:  # pragma: no cover - resposta órfã de item
            continue
        facts_by_student.setdefault(row.student_id, []).append(ResponseFact(
            item_id=row.item_id, area_code=area_code,
            chosen_option=row.chosen_option, is_correct=bool(row.is_correct)))

    scores_by_student = {
        student_id: score_student(student_id, facts, area_item_counts)
        for student_id, facts in facts_by_student.items()
    }

    # outerjoin em SchoolUnit, e não join: a unidade é opcional (§7 pede
    # comparação entre turmas E unidades, e Class.school_unit_id é nullable).
    # Mesma forma do batched query de services/coordination_portal.py:604-610.
    class_rows = (await session.execute(
        select(StudentEnrollment.student_id, Class.id, Class.name,
               Class.school_unit_id, SchoolUnit.name)
        .join(Class, Class.id == StudentEnrollment.class_id)
        .outerjoin(SchoolUnit, SchoolUnit.id == Class.school_unit_id)
        .where(
            StudentEnrollment.school_id == exam.school_id,
            StudentEnrollment.status == "ACTIVE",
            Class.academic_year_id == exam.academic_year_id,
        ))).all()
    class_by_student = {row[0]: (row[1], row[2]) for row in class_rows}
    unit_by_student = {row[0]: (row[3], row[4] or NO_UNIT_LABEL) for row in class_rows}

    return ExamFacts(
        exam=exam, areas=areas, items=items, area_labels=area_labels,
        area_item_counts=area_item_counts, descriptors=descriptors,
        facts_by_student=facts_by_student, scores_by_student=scores_by_student,
        class_by_student=class_by_student, unit_by_student=unit_by_student,
    )


def _percentile(value: int, population: Sequence[int]) -> float:
    """Percentual de colegas com acerto ESTRITAMENTE menor, sobre o total do
    grupo (o próprio aluno incluído no denominador). Empate não sobe
    ninguém: dois alunos com o mesmo número de acertos recebem o mesmo
    percentil."""
    if not population:
        return 0.0
    below = sum(1 for other in population if other < value)
    return round(100.0 * below / len(population), 1)


def _area_results(score: StudentRawScore, area_labels: dict[str, str]) -> tuple[AreaResult, ...]:
    return tuple(
        AreaResult(area_code=area.area_code, label=area_labels.get(area.area_code, area.area_code),
                   correct=area.correct, answered=area.answered,
                   total_items=area.total_items, percentage=area.percentage)
        for area in score.by_area
    )


async def build_student_report(
    session: AsyncSession, *, exam_id: uuid.UUID, student_id: uuid.UUID
) -> StudentReport:
    """Boletim do aluno (spec §7, nível 1) na versão de acerto bruto."""
    data = await load_exam_facts(session, exam_id=exam_id)
    score = data.scores_by_student.get(student_id)
    if score is None:
        raise SimuladoNotFoundError(
            "este aluno não tem respostas digitadas neste simulado")

    school_totals = [other.total_correct for other in data.scores_by_student.values()]
    class_entry = data.class_by_student.get(student_id)
    class_id = class_entry[0] if class_entry else None
    class_percentile = None
    if class_id is not None:
        class_totals = [
            other.total_correct
            for other_id, other in data.scores_by_student.items()
            if (data.class_by_student.get(other_id) or (None, ""))[0] == class_id
        ]
        class_percentile = _percentile(score.total_correct, class_totals)

    fact_by_item = {fact.item_id: fact for fact in data.facts_by_student[student_id]}
    items = tuple(
        StudentItemResult(
            position=item.position,
            area_code=item.area_code,
            chosen_option=fact_by_item[item.id].chosen_option if item.id in fact_by_item else None,
            # spec §6.4: âncora nunca aparece em devolutiva de gabarito ao aluno.
            correct_option=None if item.is_anchor else item.correct_option,
            is_correct=bool(item.id in fact_by_item and fact_by_item[item.id].is_correct),
            is_anchor=bool(item.is_anchor),
        )
        for item in data.items
    )

    return StudentReport(
        exam_id=data.exam.id,
        exam_name=data.exam.name,
        exam_status=data.exam.status,
        student_id=student_id,
        by_area=_area_results(score, data.area_labels),
        total_correct=score.total_correct,
        total_answered=score.total_answered,
        total_items=score.total_items,
        total_percentage=score.total_percentage,
        class_id=class_id,
        class_percentile=class_percentile,
        school_percentile=_percentile(score.total_correct, school_totals),
        items=items,
    )


__all__ = [
    "NO_CLASS_LABEL",
    "NO_UNIT_LABEL",
    "AreaResult",
    "ExamFacts",
    "StudentItemResult",
    "StudentReport",
    "build_student_report",
    "load_exam_facts",
]
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_simulado_reports.py -v`
Expected: PASS — 8 testes.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/simulado_reports.py tests/test_simulado_reports.py
git commit -m "feat(simulados): boletim do aluno por acerto bruto

Fase 2 Task 5. Carregador unico dos fatos do simulado + nivel 1 de secao 7.
Item-ancora nao revela a alternativa correta ao aluno (spec 6.4) e nenhum
dataclass carrega serie de evolucao, que secao 7 manda suprimir sem
equalizacao.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: Relatórios — painel da turma

**Files:**
- Modify: `src/agente_ia_edu/services/simulado_reports.py` (acrescentar helpers e `build_classroom_report`)
- Test: `tests/test_simulado_reports.py` (acrescentar a classe abaixo)

**Interfaces:**
- Consumes: `ExamFacts`, `load_exam_facts` (Task 5); `compute_item_statistics` (Task 1).
- Produces:
  - `AreaAverage(area_code, label, total_items, average_correct, average_percentage)`
  - `ScoreBand(label, lower_percentage, upper_percentage, student_count)`
  - `GroupSummary(class_id, class_name, student_count, average_percentage)`
  - `MissedItem(position, area_code, correct_option, n_responses, p_value)`
  - `ClassroomReport(exam_id, exam_name, exam_status, class_id, class_name, student_count, by_area, average_percentage, distribution, most_missed, other_classes)`
  - `async build_classroom_report(session, *, exam_id, class_id) -> ClassroomReport`

- [ ] **Step 1: Escrever o teste que falha**

Acrescente a `tests/test_simulado_reports.py`, antes do `if __name__`:

```python
class ClassroomReportTests(SimuladoReportsTestCase):
    def _for(self, class_name):
        return self._report(lambda session: build_classroom_report(
            session, exam_id=self.exam_id, class_id=self.classes[class_name]))

    def test_area_averages_match_the_hand_computed_values(self):
        # 3A = Alice (LC 3, CH 2) + Bruno (LC 2, CH 1).
        # LC: media 2.5 acertos de 3 -> 83.3% | CH: media 1.5 de 2 -> 75.0%
        report = self._for("3A")
        self.assertEqual(report.class_name, "3A")
        self.assertEqual(report.student_count, 2)
        self.assertEqual([(a.area_code, a.average_correct, a.average_percentage)
                          for a in report.by_area],
                         [("LC", 2.5, 83.3), ("CH", 1.5, 75.0)])
        self.assertEqual(report.average_percentage, 80.0)

    def test_the_weaker_class_gets_its_own_honest_numbers(self):
        # 3B = Carla (total 20.0%) + Davi (0.0%) -> media 10.0%
        report = self._for("3B")
        self.assertEqual(report.average_percentage, 10.0)
        self.assertEqual([(a.area_code, a.average_percentage) for a in report.by_area],
                         [("LC", 16.7), ("CH", 0.0)])

    def test_the_distribution_uses_five_fixed_bands_over_the_class(self):
        report = self._for("3A")   # percentuais 100.0 e 60.0
        self.assertEqual([band.label for band in report.distribution],
                         ["0–19%", "20–39%", "40–59%", "60–79%", "80–100%"])
        self.assertEqual([band.student_count for band in report.distribution],
                         [0, 0, 0, 1, 1])

    def test_most_missed_items_are_ranked_by_the_classs_own_hit_rate(self):
        # Em 3A, só as posições 3 e 5 tiveram erro (p=0.5); o resto foi 1.0.
        report = self._for("3A")
        self.assertEqual([item.position for item in report.most_missed][:2], [3, 5])
        self.assertEqual(report.most_missed[0].p_value, 0.5)
        self.assertEqual(report.most_missed[0].correct_option, "C")

    def test_the_teacher_panel_shows_the_anchor_items_correct_option(self):
        # Ao contrário do boletim do aluno (§6.4), este painel não é
        # devolutiva de gabarito: esconder a resposta de quem confere a prova
        # esconderia o erro de cadastro que §6.5 quer que apareça.
        report = self._for("3A")
        anchor = next(item for item in report.most_missed if item.position == 5)
        self.assertEqual(anchor.correct_option, "E")

    def test_other_classes_come_back_for_comparison_ranked_by_average(self):
        report = self._for("3A")
        self.assertEqual([(g.class_name, g.student_count, g.average_percentage)
                          for g in report.other_classes],
                         [("3A", 2, 80.0), ("3B", 2, 10.0)])

    def test_an_unknown_class_is_a_lookup_error(self):
        with self.assertRaises(SimuladoNotFoundError):
            self._report(lambda session: build_classroom_report(
                session, exam_id=self.exam_id, class_id=uuid.uuid4()))
```

E acrescente `build_classroom_report` ao import de `agente_ia_edu.services.simulado_reports` no topo do arquivo de teste.

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_simulado_reports.py -k Classroom -v`
Expected: FAIL — `ImportError: cannot import name 'build_classroom_report'`

- [ ] **Step 3: Implementação mínima**

Em `src/agente_ia_edu/services/simulado_reports.py`, acrescente `compute_item_statistics` ao import de `simulado_scoring` e insira, depois de `build_student_report`:

```python
# Cinco faixas fixas de percentual de acerto. Fixas de propósito: uma faixa
# calculada a partir da própria turma (quintis, por exemplo) muda de
# significado a cada aplicação e impede comparar duas turmas lado a lado -
# que é exatamente o que o painel de §7 existe para fazer.
_BANDS: tuple[tuple[float, float, str], ...] = (
    (0.0, 20.0, "0–19%"),
    (20.0, 40.0, "20–39%"),
    (40.0, 60.0, "40–59%"),
    (60.0, 80.0, "60–79%"),
    (80.0, 100.0, "80–100%"),
)
_MOST_MISSED_LIMIT = 10


@dataclass(frozen=True)
class AreaAverage:
    area_code: str
    label: str
    total_items: int
    average_correct: float
    average_percentage: float


@dataclass(frozen=True)
class ScoreBand:
    label: str
    lower_percentage: float
    upper_percentage: float
    student_count: int


@dataclass(frozen=True)
class GroupSummary:
    class_id: uuid.UUID | None
    class_name: str
    student_count: int
    average_percentage: float


@dataclass(frozen=True)
class MissedItem:
    position: int
    area_code: str
    correct_option: str
    n_responses: int
    p_value: float


@dataclass(frozen=True)
class ClassroomReport:
    exam_id: uuid.UUID
    exam_name: str
    exam_status: str
    class_id: uuid.UUID
    class_name: str
    student_count: int
    by_area: tuple[AreaAverage, ...]
    average_percentage: float
    distribution: tuple[ScoreBand, ...]
    most_missed: tuple[MissedItem, ...]
    other_classes: tuple[GroupSummary, ...]


def _distribution(percentages: Sequence[float]) -> tuple[ScoreBand, ...]:
    bands: list[ScoreBand] = []
    for index, (lower, upper, label) in enumerate(_BANDS):
        is_last = index == len(_BANDS) - 1
        count = sum(
            1 for value in percentages
            if value >= lower and (value <= upper if is_last else value < upper)
        )
        bands.append(ScoreBand(label=label, lower_percentage=lower,
                               upper_percentage=upper, student_count=count))
    return tuple(bands)


def _area_averages(
    scores: Sequence[StudentRawScore], data: ExamFacts
) -> tuple[AreaAverage, ...]:
    population = len(scores)
    averages: list[AreaAverage] = []
    for code, total_items in data.area_item_counts.items():
        corrects = [area.correct for score in scores for area in score.by_area
                    if area.area_code == code]
        average_correct = round(sum(corrects) / population, 2) if population else 0.0
        averages.append(AreaAverage(
            area_code=code,
            label=data.area_labels.get(code, code),
            total_items=total_items,
            average_correct=average_correct,
            average_percentage=(round(100.0 * average_correct / total_items, 1)
                                if total_items else 0.0),
        ))
    return tuple(averages)


def _class_summaries(data: ExamFacts) -> tuple[GroupSummary, ...]:
    """Uma linha por turma, ordenada da melhor média para a pior. Aluno com
    resposta digitada mas sem matrícula ativa no ano letivo cai num balde
    explícito - nunca é omitido nem colado numa turma qualquer."""
    grouped: dict[tuple[uuid.UUID | None, str], list[float]] = {}
    for student_id, score in data.scores_by_student.items():
        entry = data.class_by_student.get(student_id)
        key = entry if entry is not None else (None, NO_CLASS_LABEL)
        grouped.setdefault(key, []).append(score.total_percentage)
    summaries = [
        GroupSummary(class_id=class_id, class_name=class_name,
                     student_count=len(values),
                     average_percentage=round(sum(values) / len(values), 1))
        for (class_id, class_name), values in grouped.items()
    ]
    summaries.sort(key=lambda group: (-group.average_percentage, group.class_name))
    return tuple(summaries)


def _most_missed(data: ExamFacts, student_ids: Sequence[uuid.UUID]) -> tuple[MissedItem, ...]:
    statistics = compute_item_statistics(
        data.descriptors,
        {student_id: data.facts_by_student[student_id] for student_id in student_ids},
    )
    statistics.sort(key=lambda stats: (stats.p_value, stats.position))
    return tuple(
        MissedItem(position=stats.position, area_code=stats.area_code,
                   correct_option=stats.correct_option, n_responses=stats.n_responses,
                   p_value=stats.p_value)
        for stats in statistics[:_MOST_MISSED_LIMIT]
    )


async def build_classroom_report(
    session: AsyncSession, *, exam_id: uuid.UUID, class_id: uuid.UUID
) -> ClassroomReport:
    """Painel da turma (spec §7, nível 2) na versão de acerto bruto."""
    data = await load_exam_facts(session, exam_id=exam_id)
    student_ids = [
        student_id for student_id in data.scores_by_student
        if (data.class_by_student.get(student_id) or (None, ""))[0] == class_id
    ]
    if not student_ids:
        raise SimuladoNotFoundError(
            "nenhum aluno desta turma tem respostas digitadas neste simulado")

    class_name = data.class_by_student[student_ids[0]][1]
    scores = [data.scores_by_student[student_id] for student_id in student_ids]
    percentages = [score.total_percentage for score in scores]

    return ClassroomReport(
        exam_id=data.exam.id,
        exam_name=data.exam.name,
        exam_status=data.exam.status,
        class_id=class_id,
        class_name=class_name,
        student_count=len(student_ids),
        by_area=_area_averages(scores, data),
        average_percentage=round(sum(percentages) / len(percentages), 1),
        distribution=_distribution(percentages),
        most_missed=_most_missed(data, student_ids),
        other_classes=_class_summaries(data),
    )
```

Acrescente ao `__all__`: `"AreaAverage"`, `"ClassroomReport"`, `"GroupSummary"`, `"MissedItem"`, `"ScoreBand"`, `"build_classroom_report"`.

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_simulado_reports.py -v`
Expected: PASS — 15 testes.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/simulado_reports.py tests/test_simulado_reports.py
git commit -m "feat(simulados): painel da turma por acerto bruto

Fase 2 Task 6. Media por area, distribuicao em 5 faixas fixas, questoes mais
erradas pela taxa de acerto da propria turma e comparacao com as demais.
Faixas fixas de proposito: faixa derivada da turma impede comparar turmas.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: Relatórios — painel da escola e análise de itens

**Files:**
- Modify: `src/agente_ia_edu/services/simulado_reports.py`
- Test: `tests/test_simulado_reports.py`

**Interfaces:**
- Consumes: tudo das Tasks 5 e 6; `ItemStatistics` (Task 1).
- Produces:
  - `UnitSummary(unit_id, unit_name, student_count, average_percentage)`
  - `SchoolReport(exam_id, exam_name, exam_status, student_count, by_area, average_percentage, distribution, classes, units)`
  - `ItemAnalysis(exam_id, exam_name, exam_status, student_count, items: tuple[ItemStatistics, ...])`
  - `async build_school_report(session, *, exam_id) -> SchoolReport`
  - `async build_item_analysis(session, *, exam_id) -> ItemAnalysis`

- [ ] **Step 1: Escrever o teste que falha**

Acrescente a `tests/test_simulado_reports.py`, antes do `if __name__`:

```python
class SchoolReportTests(SimuladoReportsTestCase):
    def setUp(self):
        super().setUp()
        self.report = self._report(lambda session: build_school_report(
            session, exam_id=self.exam_id))

    def test_the_school_average_is_the_mean_of_every_typed_student(self):
        # 100.0 + 60.0 + 20.0 + 0.0 = 180.0 / 4 = 45.0
        self.assertEqual(self.report.student_count, 4)
        self.assertEqual(self.report.average_percentage, 45.0)

    def test_area_averages_cover_the_whole_school(self):
        # LC: (3+2+1+0)/4 = 1.5 de 3 -> 50.0% | CH: (2+1+0+0)/4 = 0.75 de 2 -> 37.5%
        self.assertEqual([(a.area_code, a.average_correct, a.average_percentage)
                          for a in self.report.by_area],
                         [("LC", 1.5, 50.0), ("CH", 0.75, 37.5)])

    def test_the_distribution_places_each_student_in_exactly_one_band(self):
        self.assertEqual([band.student_count for band in self.report.distribution],
                         [1, 1, 0, 1, 1])
        self.assertEqual(sum(band.student_count for band in self.report.distribution), 4)

    def test_classes_are_compared_side_by_side(self):
        self.assertEqual([(g.class_name, g.average_percentage) for g in self.report.classes],
                         [("3A", 80.0), ("3B", 10.0)])

    def test_units_are_compared_too_and_a_class_with_none_gets_an_explicit_bucket(self):
        # §7 pede comparação entre turmas E unidades. 3A está no Campus
        # Centro; 3B não tem unidade e cai num balde nomeado, nunca colada
        # numa unidade qualquer nem omitida da comparação.
        self.assertEqual([(u.unit_name, u.student_count, u.average_percentage)
                          for u in self.report.units],
                         [("Campus Centro", 2, 80.0), ("Sem unidade cadastrada", 2, 10.0)])


class ItemAnalysisTests(SimuladoReportsTestCase):
    def setUp(self):
        super().setUp()
        analysis = self._report(lambda session: build_item_analysis(
            session, exam_id=self.exam_id))
        self.analysis = analysis
        self.by_position = {stats.position: stats for stats in analysis.items}

    def test_p_values_match_the_hand_computed_hit_rates(self):
        self.assertEqual(self.analysis.student_count, 4)
        self.assertEqual([self.by_position[p].p_value for p in (1, 2, 3, 4, 5)],
                         [0.75, 0.5, 0.25, 0.5, 0.25])

    def test_point_biserial_matches_the_hand_computed_rest_score_correlation(self):
        # Correlação com o REST score DA MESMA ÁREA - conta no docstring do
        # módulo de teste de simulado_scoring.
        self.assertAlmostEqual(self.by_position[1].point_biserial, 0.5222, places=4)
        self.assertAlmostEqual(self.by_position[2].point_biserial, 0.7071, places=4)
        self.assertAlmostEqual(self.by_position[3].point_biserial, 0.5222, places=4)
        self.assertAlmostEqual(self.by_position[4].point_biserial, 0.5774, places=4)
        self.assertAlmostEqual(self.by_position[5].point_biserial, 0.5774, places=4)

    def test_the_distractor_distribution_counts_every_alternative_and_the_blank(self):
        self.assertEqual(self.by_position[1].option_distribution["A"], 3)
        self.assertEqual(self.by_position[1].option_distribution["E"], 1)
        self.assertEqual(self.by_position[4].option_distribution["D"], 2)
        self.assertEqual(self.by_position[4].option_distribution["A"], 1)
        self.assertEqual(self.by_position[4].option_distribution["BRANCO"], 1)

    def test_items_come_back_in_position_order_with_the_correct_option(self):
        self.assertEqual([stats.position for stats in self.analysis.items], [1, 2, 3, 4, 5])
        self.assertEqual([stats.correct_option for stats in self.analysis.items],
                         ["A", "B", "C", "D", "E"])
```

E acrescente `build_item_analysis`, `build_school_report` ao import de `agente_ia_edu.services.simulado_reports`.

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_simulado_reports.py -k "SchoolReport or ItemAnalysis" -v`
Expected: FAIL — `ImportError: cannot import name 'build_school_report'`

- [ ] **Step 3: Implementação mínima**

Em `src/agente_ia_edu/services/simulado_reports.py`, acrescente `ItemStatistics` ao import de `simulado_scoring` e insira ao fim, antes do `__all__`:

```python
@dataclass(frozen=True)
class UnitSummary:
    unit_id: uuid.UUID | None
    unit_name: str
    student_count: int
    average_percentage: float


@dataclass(frozen=True)
class SchoolReport:
    exam_id: uuid.UUID
    exam_name: str
    exam_status: str
    student_count: int
    by_area: tuple[AreaAverage, ...]
    average_percentage: float
    distribution: tuple[ScoreBand, ...]
    classes: tuple[GroupSummary, ...]
    units: tuple[UnitSummary, ...]


def _unit_summaries(data: ExamFacts) -> tuple[UnitSummary, ...]:
    """Uma linha por unidade (campus), ordenada da melhor média para a pior.
    Turma sem unidade cadastrada vira um balde nomeado - a mesma escolha que
    services/coordination_portal.py já faz na hierarquia acadêmica."""
    grouped: dict[tuple[uuid.UUID | None, str], list[float]] = {}
    for student_id, score in data.scores_by_student.items():
        key = data.unit_by_student.get(student_id) or (None, NO_UNIT_LABEL)
        grouped.setdefault(key, []).append(score.total_percentage)
    summaries = [
        UnitSummary(unit_id=unit_id, unit_name=unit_name, student_count=len(values),
                    average_percentage=round(sum(values) / len(values), 1))
        for (unit_id, unit_name), values in grouped.items()
    ]
    summaries.sort(key=lambda group: (-group.average_percentage, group.unit_name))
    return tuple(summaries)


@dataclass(frozen=True)
class ItemAnalysis:
    exam_id: uuid.UUID
    exam_name: str
    exam_status: str
    student_count: int
    items: tuple[ItemStatistics, ...]


async def build_school_report(session: AsyncSession, *, exam_id: uuid.UUID) -> SchoolReport:
    """Painel da escola (spec §7, nível 3) na versão de acerto bruto."""
    data = await load_exam_facts(session, exam_id=exam_id)
    scores = list(data.scores_by_student.values())
    percentages = [score.total_percentage for score in scores]
    return SchoolReport(
        exam_id=data.exam.id,
        exam_name=data.exam.name,
        exam_status=data.exam.status,
        student_count=len(scores),
        by_area=_area_averages(scores, data),
        average_percentage=round(sum(percentages) / len(percentages), 1) if percentages else 0.0,
        distribution=_distribution(percentages),
        classes=_class_summaries(data),
        units=_unit_summaries(data),
    )


async def build_item_analysis(session: AsyncSession, *, exam_id: uuid.UUID) -> ItemAnalysis:
    """Análise de itens (spec §7, nível 4) SEM TRI.

    Só as duas estatísticas que não dependem de calibragem: proporção de
    acerto e ponto-bisserial. A curva característica do item e os parâmetros
    `a`/`b`/`c` chegam na fase 3 - e o ponto-bisserial negativo já denuncia,
    aqui, o gabarito cadastrado errado que §6.5 aponta como a verificação de
    maior retorno prático.
    """
    data = await load_exam_facts(session, exam_id=exam_id)
    statistics = compute_item_statistics(data.descriptors, data.facts_by_student)
    return ItemAnalysis(
        exam_id=data.exam.id,
        exam_name=data.exam.name,
        exam_status=data.exam.status,
        student_count=len(data.facts_by_student),
        items=tuple(statistics),
    )
```

Acrescente ao `__all__`: `"ItemAnalysis"`, `"SchoolReport"`, `"UnitSummary"`, `"build_item_analysis"`, `"build_school_report"`.

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_simulado_reports.py -v`
Expected: PASS — 24 testes.

- [ ] **Step 5: Confirmar que a camada de serviço inteira está verde**

Run: `python -m pytest tests/test_simulado_scoring.py tests/test_simulado_service.py tests/test_simulado_manual_entry.py tests/test_simulado_reports.py -v`
Expected: PASS — 78 testes.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/simulado_reports.py tests/test_simulado_reports.py
git commit -m "feat(simulados): painel da escola e analise de itens sem TRI

Fase 2 Task 7. Niveis 3 e 4 de secao 7. A analise de itens traz so o que nao
depende de calibragem - proporcao de acerto, ponto-bisserial e distribuicao
de distratores; ponto-bisserial negativo ja denuncia gabarito errado (6.5).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: Schemas e rotas de administração do simulado (coordenação)

**Files:**
- Create: `src/agente_ia_edu/api/schemas/simulados.py`
- Create: `src/agente_ia_edu/api/routes/simulados.py`
- Modify: `src/agente_ia_edu/api/app.py` (import + `include_router`)
- Test: `tests/test_simulados_http.py`

**Interfaces:**
- Consumes: `SimuladoService` e erros (Tasks 2-3); `enter_student_responses`, `list_exam_classes`, `list_exam_roster`, `ManualResponseInput` (Task 4); `Requester`; `get_current_authenticated_context`, `get_session_factory`.
- Produces:
  - schemas `SimuladoAreaPayload`, `SimuladoItemPayload`, `SimuladoCreateRequest`, `AnswerKeyRequest`, `StatusTransitionRequest`, `ManualResponsePayload`, `ManualEntryRequest`, `ManualEntryResponse`, `SimuladoSummary`, `SimuladoDetail`, `ExamClassItem`, `RosterStudentItem`
  - `simulados_coordination_router: APIRouter` (prefixo `/api/v1/coordination/simulados`)
  - `_requester(ctx) -> Requester` e `_map_simulado_error(exc) -> HTTPException`, usados também na Task 9

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_simulados_http.py`:

```python
"""Camada HTTP do subsistema de simulados (Fase 2, Tasks 8 e 9).

Mesma receita de tests/test_coordination_portal_http.py: TestClient sobre um
FastAPI montado só com os routers sob teste, SQLite em memória e
dependency_overrides para identidade e session factory. O que se testa aqui
é o que o teste de serviço NÃO alcança: parsing do request, mapeamento
erro -> status e serialização da resposta.
"""

import asyncio
import unittest
import uuid

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.api.dependencies import (
    get_current_authenticated_context,
    get_current_identity,
    get_session_factory,
)
from agente_ia_edu.api.routes.simulados import simulados_coordination_router
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import (
    AcademicYear,
    Class,
    GradeLevel,
    Person,
    School,
    Segment,
    Student,
    StudentEnrollment,
    User,
    UserSchoolLink,
)
from agente_ia_edu.db.models.mock_exam import MockExamResponse, MockExamWorkflowAudit
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext

AREAS = [
    {"code": "LC", "label": "Linguagens e Códigos", "display_order": 1},
    {"code": "CH", "label": "Ciências Humanas", "display_order": 2},
]
ITEMS = [
    {"position": 1, "area_code": "LC", "correct_option": "A"},
    {"position": 2, "area_code": "LC", "correct_option": "B"},
    {"position": 3, "area_code": "CH", "correct_option": "C"},
    {"position": 4, "area_code": "CH", "correct_option": "D",
     "is_anchor": True, "anchor_key": "CH-ANCORA-1"},
]


class SimuladosHTTPTestCase(unittest.TestCase):
    routers = (simulados_coordination_router,)

    def setUp(self):
        async def _setup():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                school_a = School(id=uuid.uuid4(), code="SIM-HA", name="Escola A")
                school_b = School(id=uuid.uuid4(), code="SIM-HB", name="Escola B")
                session.add_all([school_a, school_b])
                await session.flush()
                segment = Segment(id=uuid.uuid4(), school_id=school_a.id, name="EM",
                                  external_id="SEG-H")
                year = AcademicYear(id=uuid.uuid4(), school_id=school_a.id, year=2026,
                                    external_id="YEAR-H")
                session.add_all([segment, year])
                await session.flush()
                grade = GradeLevel(id=uuid.uuid4(), school_id=school_a.id,
                                   segment_id=segment.id, name="3a", external_id="GRADE-H")
                session.add(grade)
                await session.flush()
                klass = Class(id=uuid.uuid4(), school_id=school_a.id,
                              academic_year_id=year.id, grade_level_id=grade.id,
                              name="3A", external_id="TURMA-3A")
                session.add(klass)
                await session.flush()
                students = {}
                for name in ("Alice", "Bruno"):
                    person = Person(id=uuid.uuid4(), school_id=school_a.id, full_name=name)
                    session.add(person)
                    await session.flush()
                    student = Student(id=uuid.uuid4(), school_id=school_a.id,
                                      person_id=person.id, student_code=f"ST-{name[:1]}")
                    session.add(student)
                    session.add(User(id=uuid.uuid4(), school_id=school_a.id,
                                     person_id=person.id, external_identity_provider="test",
                                     external_user_id=f"student-{name.lower()}"))
                    session.add(StudentEnrollment(
                        id=uuid.uuid4(), school_id=school_a.id, student_id=student.id,
                        class_id=klass.id, status="ACTIVE"))
                    # A rota do aluno NÃO usa o override de
                    # get_current_authenticated_context: ela resolve o contexto
                    # de verdade com AuthorizationService, que lê
                    # user_school_links. Sem esta linha, o aluno cai no
                    # contexto de estudante independente (school_id=None) e a
                    # rota devolve 403 - que é o comportamento correto, mas não
                    # o que estes testes querem provar.
                    session.add(UserSchoolLink(
                        external_user_id=f"student-{name.lower()}", school_id=school_a.id,
                        role="STUDENT", scope_type="CLASSROOM",
                        scope_external_id="TURMA-3A", active=True))
                    students[name] = student.id
                await session.commit()
                return engine, factory, school_a.id, school_b.id, year.id, klass.id, students

        (self.engine, self.factory, self.school_a, self.school_b, self.year_id,
         self.class_id, self.students) = asyncio.run(_setup())

        self.ctx = {"value": AuthenticatedUserContext(
            user_id="coord-a", external_identity_id="coord-a", role="COORDINATOR",
            school_id=str(self.school_a), scope_type="SCHOOL")}
        self.identity = {"value": ExternalIdentityContext(
            provider="test", external_user_id="coord-a", roles=("coordinator",))}

        app = FastAPI()
        for router in self.routers:
            app.include_router(router)
        app.dependency_overrides[get_session_factory] = lambda: self.factory
        app.dependency_overrides[get_current_authenticated_context] = lambda: self.ctx["value"]
        app.dependency_overrides[get_current_identity] = lambda: self.identity["value"]
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self.client.close()
        self.app.dependency_overrides.clear()
        asyncio.run(self.engine.dispose())

    def _as(self, user_id, *, role="COORDINATOR", school_id=None, roles=("coordinator",)):
        resolved = str(school_id) if school_id is not None else str(self.school_a)
        self.ctx["value"] = AuthenticatedUserContext(
            user_id=user_id, external_identity_id=user_id, role=role,
            school_id=resolved, scope_type="SCHOOL")
        self.identity["value"] = ExternalIdentityContext(
            provider="test", external_user_id=user_id, roles=roles)

    # ------------------------------------------------------------ helpers

    def _create(self, name="Simulado ENEM 1"):
        response = self.client.post("/api/v1/coordination/simulados", json={
            "academic_year_id": str(self.year_id), "name": name,
            "exam_day": 1, "application_date": None})
        self.assertEqual(response.status_code, 201, response.text)
        return response.json()["id"]

    def _answer_key(self, exam_id, items=None):
        return self.client.put(f"/api/v1/coordination/simulados/{exam_id}/answer-key",
                               json={"areas": AREAS, "items": items or ITEMS})

    def _status(self, exam_id, to_status):
        return self.client.post(f"/api/v1/coordination/simulados/{exam_id}/status",
                                json={"to_status": to_status})

    def _type(self, exam_id, student_id, options):
        return self.client.post(f"/api/v1/coordination/simulados/{exam_id}/responses", json={
            "student_id": str(student_id),
            "responses": [{"position": index, "chosen_option": option}
                          for index, option in enumerate(options, start=1)]})

    def _applied_exam_with_two_typed_students(self):
        exam_id = self._create()
        self.assertEqual(self._answer_key(exam_id).status_code, 200)
        self.assertEqual(self._status(exam_id, "PRINTED").status_code, 200)
        self.assertEqual(self._status(exam_id, "APPLIED").status_code, 200)
        self.assertEqual(self._type(exam_id, self.students["Alice"],
                                    ["A", "B", "C", "D"]).status_code, 200)
        self.assertEqual(self._type(exam_id, self.students["Bruno"],
                                    ["A", "B", "E", None]).status_code, 200)
        return exam_id


class AdminRoutesTests(SimuladosHTTPTestCase):
    def test_create_returns_201_with_a_draft_detail(self):
        response = self.client.post("/api/v1/coordination/simulados", json={
            "academic_year_id": str(self.year_id), "name": "Simulado 1", "exam_day": 1})
        self.assertEqual(response.status_code, 201, response.text)
        body = response.json()
        self.assertEqual(body["status"], "DRAFT")
        self.assertEqual(body["item_count"], 0)
        self.assertEqual(body["areas"], [])
        self.assertEqual(body["items"], [])

    def test_a_teacher_gets_403(self):
        self._as("prof-a", role="TEACHER", roles=("teacher",))
        response = self.client.post("/api/v1/coordination/simulados", json={
            "academic_year_id": str(self.year_id), "name": "Simulado 1", "exam_day": 1})
        self.assertEqual(response.status_code, 403, response.text)

    def test_invalid_exam_day_is_422(self):
        response = self.client.post("/api/v1/coordination/simulados", json={
            "academic_year_id": str(self.year_id), "name": "Simulado 1", "exam_day": 7})
        self.assertEqual(response.status_code, 422, response.text)

    def test_listing_returns_only_the_requesters_school(self):
        self._create("A1")
        self._as("coord-b", school_id=self.school_b)
        response = self.client.get("/api/v1/coordination/simulados")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual(response.json(), [])

    def test_detail_of_an_exam_from_another_school_is_403(self):
        exam_id = self._create()
        self._as("coord-b", school_id=self.school_b)
        response = self.client.get(f"/api/v1/coordination/simulados/{exam_id}")
        self.assertEqual(response.status_code, 403, response.text)

    def test_detail_of_an_unknown_exam_is_404(self):
        response = self.client.get(f"/api/v1/coordination/simulados/{uuid.uuid4()}")
        self.assertEqual(response.status_code, 404, response.text)

    def test_answer_key_round_trips_areas_items_and_the_anchor_flag(self):
        exam_id = self._create()
        self.assertEqual(self._answer_key(exam_id).status_code, 200)
        body = self.client.get(f"/api/v1/coordination/simulados/{exam_id}").json()
        self.assertEqual(body["item_count"], 4)
        self.assertEqual([area["code"] for area in body["areas"]], ["LC", "CH"])
        self.assertEqual([item["position"] for item in body["items"]], [1, 2, 3, 4])
        self.assertTrue(body["items"][3]["is_anchor"])
        self.assertEqual(body["items"][3]["anchor_key"], "CH-ANCORA-1")

    def test_an_invalid_answer_key_is_422_with_a_readable_detail(self):
        exam_id = self._create()
        response = self._answer_key(exam_id, items=[
            {"position": 1, "area_code": "LC", "correct_option": "A"},
            {"position": 3, "area_code": "LC", "correct_option": "B"}])
        self.assertEqual(response.status_code, 422, response.text)
        self.assertIn("posições", response.json()["detail"])

    def test_printing_without_an_answer_key_is_409(self):
        exam_id = self._create()
        response = self._status(exam_id, "PRINTED")
        self.assertEqual(response.status_code, 409, response.text)

    def test_the_status_walk_returns_200_at_each_allowed_step(self):
        exam_id = self._create()
        self._answer_key(exam_id)
        self.assertEqual(self._status(exam_id, "PRINTED").json()["status"], "PRINTED")
        self.assertEqual(self._status(exam_id, "APPLIED").json()["status"], "APPLIED")
        self._type(exam_id, self.students["Alice"], ["A", "B", "C", "D"])
        self.assertEqual(self._status(exam_id, "PUBLISHED").json()["status"], "PUBLISHED")

    def test_scanned_is_409_and_says_which_phase_owns_it(self):
        exam_id = self._create()
        self._answer_key(exam_id)
        self._status(exam_id, "PRINTED")
        response = self._status(exam_id, "APPLIED")
        self.assertEqual(response.status_code, 200)
        response = self._status(exam_id, "SCANNED")
        self.assertEqual(response.status_code, 409, response.text)
        self.assertIn("fase", response.json()["detail"].lower())


class ManualEntryRoutesTests(SimuladosHTTPTestCase):
    def test_typing_a_card_returns_the_real_counts(self):
        exam_id = self._create()
        self._answer_key(exam_id)
        self._status(exam_id, "PRINTED")
        self._status(exam_id, "APPLIED")
        response = self._type(exam_id, self.students["Alice"], ["A", "B", "C", "D"])
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual((body["created"], body["replaced"], body["total_correct"]), (4, 0, 4))

    def test_the_typed_rows_carry_MANUAL_provenance_and_the_caller_as_author(self):
        exam_id = self._create()
        self._answer_key(exam_id)
        self._status(exam_id, "PRINTED")
        self._status(exam_id, "APPLIED")
        self._type(exam_id, self.students["Alice"], ["A", "B", "C", "D"])

        async def _read():
            async with self.factory() as session:
                rows = (await session.execute(
                    select(MockExamResponse).where(
                        MockExamResponse.student_id == self.students["Alice"]))).scalars().all()
                return {(row.source, row.entered_by_external_id) for row in rows}

        # "coord-a" é o identificador que o dependency override autenticou:
        # o que a rota grava é exatamente ele.
        self.assertEqual(asyncio.run(_read()), {("MANUAL", "coord-a")})

    def test_a_status_transition_through_HTTP_is_audited_with_actor_and_note(self):
        exam_id = self._create()
        self._answer_key(exam_id)
        response = self.client.post(
            f"/api/v1/coordination/simulados/{exam_id}/status",
            json={"to_status": "PRINTED", "note": "cartões impressos na gráfica"})
        self.assertEqual(response.status_code, 200, response.text)

        async def _read():
            async with self.factory() as session:
                rows = (await session.execute(
                    select(MockExamWorkflowAudit).where(
                        MockExamWorkflowAudit.mock_exam_id == uuid.UUID(exam_id)))).scalars().all()
                return [(row.from_status, row.to_status, row.actor_external_id, row.note)
                        for row in rows]

        self.assertEqual(asyncio.run(_read()),
                         [("DRAFT", "PRINTED", "coord-a",
                           "cartões impressos na gráfica")])

    def test_typing_before_application_is_409(self):
        exam_id = self._create()
        self._answer_key(exam_id)
        response = self._type(exam_id, self.students["Alice"], ["A", "B", "C", "D"])
        self.assertEqual(response.status_code, 409, response.text)

    def test_a_partial_card_is_422(self):
        exam_id = self._create()
        self._answer_key(exam_id)
        self._status(exam_id, "PRINTED")
        self._status(exam_id, "APPLIED")
        response = self.client.post(f"/api/v1/coordination/simulados/{exam_id}/responses", json={
            "student_id": str(self.students["Alice"]),
            "responses": [{"position": 1, "chosen_option": "A"}]})
        self.assertEqual(response.status_code, 422, response.text)

    def test_a_student_from_outside_the_roster_is_422(self):
        exam_id = self._create()
        self._answer_key(exam_id)
        self._status(exam_id, "PRINTED")
        self._status(exam_id, "APPLIED")
        response = self._type(exam_id, uuid.uuid4(), ["A", "B", "C", "D"])
        self.assertEqual(response.status_code, 422, response.text)

    def test_classes_and_roster_feed_the_typing_screen(self):
        exam_id = self._applied_exam_with_two_typed_students()
        classes = self.client.get(f"/api/v1/coordination/simulados/{exam_id}/classes")
        self.assertEqual(classes.status_code, 200, classes.text)
        self.assertEqual(classes.json(), [{
            "class_id": str(self.class_id), "class_name": "3A",
            "student_count": 2, "typed_count": 2}])

        roster = self.client.get(
            f"/api/v1/coordination/simulados/{exam_id}/roster?class_id={self.class_id}")
        self.assertEqual(roster.status_code, 200, roster.text)
        self.assertEqual([(s["full_name"], s["has_responses"]) for s in roster.json()],
                         [("Alice", True), ("Bruno", True)])

    def test_roster_of_an_exam_from_another_school_is_403(self):
        exam_id = self._create()
        self._as("coord-b", school_id=self.school_b)
        response = self.client.get(
            f"/api/v1/coordination/simulados/{exam_id}/roster?class_id={self.class_id}")
        self.assertEqual(response.status_code, 403, response.text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_simulados_http.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.api.routes.simulados'`

- [ ] **Step 3: Criar os schemas**

Crie `src/agente_ia_edu/api/schemas/simulados.py`:

```python
"""Schemas Pydantic do subsistema de simulados (Fase 2).

`score_kind` aparece em todo schema de nota e vale `"RAW"` nesta fase. Não é
enfeite: o spec §6.3 exige que a nota carregue sempre o rótulo do que ela é,
e nesta fase ela é acerto bruto - nem escala de referência da escola, nem
previsão de nota do ENEM. Quando a fase 3 entrar, o valor passa a distinguir
as duas coisas sem que nenhum consumidor precise adivinhar.
"""

from __future__ import annotations

from datetime import date
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field


class SimuladoAreaPayload(BaseModel):
    code: str
    label: str
    display_order: int = 0


class SimuladoItemPayload(BaseModel):
    position: int
    area_code: str
    correct_option: str
    is_anchor: bool = False
    anchor_key: str | None = None


class SimuladoCreateRequest(BaseModel):
    academic_year_id: UUID
    name: str
    exam_day: int = 1
    application_date: date | None = None


class AnswerKeyRequest(BaseModel):
    areas: list[SimuladoAreaPayload]
    items: list[SimuladoItemPayload]


class StatusTransitionRequest(BaseModel):
    to_status: str
    # Vai para mock_exam_workflow_audit.note. Opcional: a auditoria registra a
    # transição com ou sem justificativa, e exigir texto faria o operador
    # inventar um.
    note: str | None = None


class SimuladoSummary(BaseModel):
    id: UUID
    name: str
    status: str
    exam_day: int
    academic_year_id: UUID
    application_date: date | None = None
    item_count: int


class SimuladoDetail(SimuladoSummary):
    areas: list[SimuladoAreaPayload] = Field(default_factory=list)
    items: list[SimuladoItemPayload] = Field(default_factory=list)


class ExamClassItem(BaseModel):
    class_id: UUID
    class_name: str
    student_count: int
    typed_count: int


class RosterStudentItem(BaseModel):
    student_id: UUID
    full_name: str
    student_code: str | None = None
    class_id: UUID
    class_name: str
    has_responses: bool


class ManualResponsePayload(BaseModel):
    position: int
    chosen_option: str | None = None


class ManualEntryRequest(BaseModel):
    student_id: UUID
    responses: list[ManualResponsePayload]


class ManualEntryResponse(BaseModel):
    student_id: UUID
    created: int
    replaced: int
    total_items: int
    total_correct: int
    score_kind: Literal["RAW"] = "RAW"
```

- [ ] **Step 4: Criar as rotas**

Crie `src/agente_ia_edu/api/routes/simulados.py`:

```python
"""Rotas do subsistema de simulados (Fase 2 do spec de correção de simulados).

Três routers num arquivo só, com os prefixos dos portais que já existem -
mesma forma de api/routes/essay_submissions.py, que exporta três routers de
um módulo (ver api/app.py:17-21). Simulado é um assunto só visto por três
papéis; espalhá-lo por coordination_portal.py (732 linhas) e
teacher_portal.py (511 linhas) engordaria dois arquivos já grandes e
espalharia a autorização do simulado por três lugares.

Autorização, sem mecanismo novo:
- coordenação: `Requester` + `require_coordination` (services/simulado_service.py),
  que é o mesmo `Requester.is_privileged` de services/question_list_store.py:82
  usado por api/routes/coordination_portal.py:85.
- professor (Task 9): `_authorize_teacher_or_coordinator`, cópia da regra de
  api/routes/teacher_portal.py:85.
- aluno (Task 9): `AuthorizationService.require_role` + `resolve_active_enrollment`,
  o par que api/routes/essay_submissions.py:107-129 já usa. Sem portão de
  módulo: PlatformModuleKey só tem AGENTE_IA_EDU e REDACAO_IA
  (services/admin.py:65-69), e inventar uma chave de módulo aqui seria
  inventar modelo de permissão.
"""

from __future__ import annotations

import uuid
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query

from ..dependencies import get_current_authenticated_context, get_session_factory
from ..schemas.simulados import (
    AnswerKeyRequest,
    ExamClassItem,
    ManualEntryRequest,
    ManualEntryResponse,
    RosterStudentItem,
    SimuladoAreaPayload,
    SimuladoCreateRequest,
    SimuladoDetail,
    SimuladoItemPayload,
    SimuladoSummary,
    StatusTransitionRequest,
)
from ...identity import AuthenticatedUserContext
from ...services.question_list_store import Requester
from ...services.simulado_responses import (
    ManualResponseInput,
    enter_student_responses,
    list_exam_classes,
    list_exam_roster,
)
from ...services.simulado_service import (
    AnswerKeyEntryInput,
    AreaInput,
    SimuladoAuthError,
    SimuladoNotFoundError,
    SimuladoService,
    SimuladoStateError,
    SimuladoValidationError,
    require_coordination,
)

simulados_coordination_router = APIRouter(
    prefix="/api/v1/coordination/simulados",
    tags=["simulados"],
)


def _requester(ctx: AuthenticatedUserContext) -> Requester:
    """Idêntico a api/routes/teacher_portal.py:76 e coordination_portal.py:76."""
    return Requester(
        external_user_id=ctx.external_identity_id or ctx.user_id,
        school_id=ctx.school_id,
        role=ctx.role,
        is_platform_admin=bool(getattr(ctx, "is_platform_admin", False)),
    )


def _map_simulado_error(exc: Exception) -> HTTPException:
    """Mesma ordem de api/routes/teacher_portal.py:114: o estado (409) é
    testado antes da validação (422), porque ambos descendem de ValueError."""
    if isinstance(exc, SimuladoNotFoundError):
        return HTTPException(status_code=404, detail=str(exc) or "Not found")
    if isinstance(exc, SimuladoAuthError):
        return HTTPException(status_code=403, detail=str(exc))
    if isinstance(exc, SimuladoStateError):
        return HTTPException(status_code=409, detail=str(exc))
    if isinstance(exc, (SimuladoValidationError, ValueError)):
        return HTTPException(status_code=422, detail=str(exc))
    raise exc  # pragma: no cover


async def _build_detail(service: SimuladoService, exam, *, requester: Requester) -> SimuladoDetail:
    areas, items = await service.load_answer_key(exam.id, requester=requester)
    return SimuladoDetail(
        id=exam.id, name=exam.name, status=exam.status, exam_day=exam.exam_day,
        academic_year_id=exam.academic_year_id, application_date=exam.application_date,
        item_count=len(items),
        areas=[SimuladoAreaPayload(code=area.code, label=area.label,
                                   display_order=area.display_order) for area in areas],
        items=[SimuladoItemPayload(position=item.position, area_code=item.area_code,
                                   correct_option=item.correct_option,
                                   is_anchor=bool(item.is_anchor), anchor_key=item.anchor_key)
               for item in items],
    )


@simulados_coordination_router.post(
    "", response_model=SimuladoDetail, status_code=201,
    summary="Criar um simulado em rascunho",
)
async def create_simulado(
    payload: SimuladoCreateRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> SimuladoDetail:
    requester = _requester(ctx)
    async with session_factory() as session:
        service = SimuladoService(session)
        try:
            # Antes de qualquer coisa, para que school_id nulo vire 403 e não
            # um erro de conversão de UUID mais adiante.
            require_coordination(requester)
            exam = await service.create_exam(
                requester=requester,
                school_id=uuid.UUID(str(requester.school_id)),
                academic_year_id=payload.academic_year_id,
                name=payload.name,
                exam_day=payload.exam_day,
                application_date=payload.application_date,
            )
            return await _build_detail(service, exam, requester=requester)
        except Exception as exc:  # noqa: BLE001
            raise _map_simulado_error(exc) from exc


@simulados_coordination_router.get(
    "", response_model=list[SimuladoSummary],
    summary="Listar os simulados da escola do solicitante",
)
async def list_simulados(
    academic_year_id: UUID | None = Query(None),
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> list[SimuladoSummary]:
    requester = _requester(ctx)
    async with session_factory() as session:
        try:
            summaries = await SimuladoService(session).list_exams(
                requester=requester, academic_year_id=academic_year_id)
        except Exception as exc:  # noqa: BLE001
            raise _map_simulado_error(exc) from exc
    return [
        SimuladoSummary(
            id=summary.exam.id, name=summary.exam.name, status=summary.exam.status,
            exam_day=summary.exam.exam_day, academic_year_id=summary.exam.academic_year_id,
            application_date=summary.exam.application_date, item_count=summary.item_count)
        for summary in summaries
    ]


@simulados_coordination_router.get(
    "/{exam_id}", response_model=SimuladoDetail,
    summary="Detalhe do simulado com áreas e gabarito",
)
async def get_simulado(
    exam_id: UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> SimuladoDetail:
    requester = _requester(ctx)
    async with session_factory() as session:
        service = SimuladoService(session)
        try:
            exam = await service.get_exam(exam_id, requester=requester)
            return await _build_detail(service, exam, requester=requester)
        except Exception as exc:  # noqa: BLE001
            raise _map_simulado_error(exc) from exc


@simulados_coordination_router.put(
    "/{exam_id}/answer-key", response_model=SimuladoDetail,
    summary="Cadastrar o gabarito: posição, área, alternativa correta e marcação de âncora",
)
async def put_answer_key(
    exam_id: UUID,
    payload: AnswerKeyRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> SimuladoDetail:
    requester = _requester(ctx)
    async with session_factory() as session:
        service = SimuladoService(session)
        try:
            exam = await service.register_answer_key(
                exam_id, requester=requester,
                areas=[AreaInput(code=area.code, label=area.label,
                                 display_order=area.display_order) for area in payload.areas],
                items=[AnswerKeyEntryInput(
                    position=item.position, area_code=item.area_code,
                    correct_option=item.correct_option, is_anchor=item.is_anchor,
                    anchor_key=item.anchor_key) for item in payload.items],
            )
            return await _build_detail(service, exam, requester=requester)
        except Exception as exc:  # noqa: BLE001
            raise _map_simulado_error(exc) from exc


@simulados_coordination_router.post(
    "/{exam_id}/status", response_model=SimuladoSummary,
    summary="Avançar o simulado no ciclo de vida",
)
async def transition_simulado(
    exam_id: UUID,
    payload: StatusTransitionRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> SimuladoSummary:
    requester = _requester(ctx)
    async with session_factory() as session:
        service = SimuladoService(session)
        try:
            exam = await service.transition(
                exam_id, requester=requester, to_status=payload.to_status,
                note=payload.note)
            _areas, items = await service.load_answer_key(exam_id, requester=requester)
        except Exception as exc:  # noqa: BLE001
            raise _map_simulado_error(exc) from exc
    return SimuladoSummary(
        id=exam.id, name=exam.name, status=exam.status, exam_day=exam.exam_day,
        academic_year_id=exam.academic_year_id, application_date=exam.application_date,
        item_count=len(items))


@simulados_coordination_router.get(
    "/{exam_id}/classes", response_model=list[ExamClassItem],
    summary="Turmas do ano letivo do simulado, com quantos cartões já foram digitados",
)
async def list_simulado_classes(
    exam_id: UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> list[ExamClassItem]:
    requester = _requester(ctx)
    async with session_factory() as session:
        try:
            exam = await SimuladoService(session).get_exam(exam_id, requester=requester)
            classes = await list_exam_classes(session, exam=exam)
        except Exception as exc:  # noqa: BLE001
            raise _map_simulado_error(exc) from exc
    return [
        ExamClassItem(class_id=entry.class_id, class_name=entry.class_name,
                      student_count=entry.student_count, typed_count=entry.typed_count)
        for entry in classes
    ]


@simulados_coordination_router.get(
    "/{exam_id}/roster", response_model=list[RosterStudentItem],
    summary="Alunos de uma turma, marcando quem já tem cartão digitado",
)
async def list_simulado_roster(
    exam_id: UUID,
    class_id: UUID = Query(...),
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> list[RosterStudentItem]:
    requester = _requester(ctx)
    async with session_factory() as session:
        try:
            exam = await SimuladoService(session).get_exam(exam_id, requester=requester)
            roster = await list_exam_roster(session, exam=exam, class_id=class_id)
        except Exception as exc:  # noqa: BLE001
            raise _map_simulado_error(exc) from exc
    return [
        RosterStudentItem(
            student_id=entry.student_id, full_name=entry.full_name,
            student_code=entry.student_code, class_id=entry.class_id,
            class_name=entry.class_name, has_responses=entry.has_responses)
        for entry in roster
    ]


@simulados_coordination_router.post(
    "/{exam_id}/responses", response_model=ManualEntryResponse,
    summary="Digitar manualmente o cartão de um aluno",
)
async def type_student_responses(
    exam_id: UUID,
    payload: ManualEntryRequest,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> ManualEntryResponse:
    requester = _requester(ctx)
    async with session_factory() as session:
        try:
            exam = await SimuladoService(session).get_exam(exam_id, requester=requester)
            result = await enter_student_responses(
                session, exam=exam, student_id=payload.student_id,
                responses=[ManualResponseInput(position=entry.position,
                                               chosen_option=entry.chosen_option)
                           for entry in payload.responses],
                # Quem está digitando, direto para
                # mock_exam_responses.entered_by_external_id (spec §3). O
                # `Requester` já carrega o identificador que a camada HTTP
                # autenticou - não há nada a resolver.
                entered_by_external_id=requester.external_user_id,
            )
        except Exception as exc:  # noqa: BLE001
            raise _map_simulado_error(exc) from exc
    return ManualEntryResponse(
        student_id=result.student_id, created=result.created, replaced=result.replaced,
        total_items=result.total_items, total_correct=result.total_correct)


__all__ = ["simulados_coordination_router"]
```

- [ ] **Step 5: Registrar o router em `app.py`**

Em `src/agente_ia_edu/api/app.py`, depois da linha 36 (`from .routes.coordination_portal import coordination_portal_router`):

```python
from .routes.simulados import simulados_coordination_router
```

E depois da linha 81 (`app.include_router(coordination_portal_router, dependencies=reception_only_guard)`):

```python
    app.include_router(simulados_coordination_router, dependencies=reception_only_guard)
```

- [ ] **Step 6: Rodar e ver passar**

Run: `python -m pytest tests/test_simulados_http.py -v`
Expected: PASS — 19 testes.

- [ ] **Step 7: Confirmar que o app inteiro ainda sobe**

Run: `python -c "from agente_ia_edu.api.app import create_app; app = create_app(); print(sorted({r.path for r in app.routes if r.path.startswith('/api/v1/coordination/simulados')}))"`
Expected: imprime as 7 rotas (`''`, `/{exam_id}`, `/{exam_id}/answer-key`, `/{exam_id}/status`, `/{exam_id}/classes`, `/{exam_id}/roster`, `/{exam_id}/responses`, com o prefixo).

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/api/schemas/simulados.py src/agente_ia_edu/api/routes/simulados.py \
        src/agente_ia_edu/api/app.py tests/test_simulados_http.py
git commit -m "feat(simulados): rotas de administracao e digitacao (coordenacao)

Fase 2 Task 8. Criacao, gabarito, ciclo de vida, turmas, roster e digitacao
manual sob /api/v1/coordination/simulados. Autorizacao reusa o Requester dos
portais; mapeamento de erro na mesma ordem de teacher_portal (409 antes de
422, ambos ValueError).

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: Rotas dos quatro relatórios (coordenação, professor, aluno)

**Files:**
- Modify: `src/agente_ia_edu/api/schemas/simulados.py`
- Modify: `src/agente_ia_edu/api/routes/simulados.py`
- Modify: `src/agente_ia_edu/services/simulado_reports.py` (listagem de simulados publicados de um aluno)
- Modify: `src/agente_ia_edu/api/app.py`
- Test: `tests/test_simulados_http.py`

**Interfaces:**
- Consumes: `build_student_report`, `build_classroom_report`, `build_school_report`, `build_item_analysis` (Tasks 5-7); `AuthorizationService`, `resolve_active_enrollment`.
- Produces:
  - schemas `AreaResultItem`, `StudentItemResultItem`, `StudentReportResponse`, `AreaAverageItem`, `ScoreBandItem`, `GroupSummaryItem`, `MissedItemEntry`, `ClassroomReportResponse`, `SchoolReportResponse`, `ItemStatisticsItem`, `ItemAnalysisResponse`, `StudentExamItem`
  - `StudentExamEntry` + `async list_published_exams_for_student(session, *, school_id, student_id)` em `simulado_reports.py`
  - `simulados_teacher_router`, `simulados_student_router`
  - `async _load_exam_for_school(session, *, exam_id, school_id) -> MockExam` em `routes/simulados.py`

- [ ] **Step 1: Escrever o teste que falha**

Acrescente a `tests/test_simulados_http.py`, antes do `if __name__`:

```python
class ReportRoutesTests(SimuladosHTTPTestCase):
    routers = (simulados_coordination_router, simulados_teacher_router, simulados_student_router)

    def _published_exam(self):
        exam_id = self._applied_exam_with_two_typed_students()
        self.assertEqual(self._status(exam_id, "PUBLISHED").status_code, 200)
        return exam_id

    # Alice acertou 4/4 (100.0%), Bruno acertou 2/4 (50.0%: A, B certos;
    # E errado na 3; branco na 4). Média da turma = 75.0%.

    def test_school_report_returns_the_real_averages(self):
        exam_id = self._published_exam()
        response = self.client.get(
            f"/api/v1/coordination/simulados/{exam_id}/report/school")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["student_count"], 2)
        self.assertEqual(body["average_percentage"], 75.0)
        self.assertEqual(body["score_kind"], "RAW")
        self.assertEqual([g["class_name"] for g in body["classes"]], ["3A"])
        # Nenhuma SchoolUnit no fixture HTTP: a comparação por unidade existe e
        # diz honestamente que não há unidade cadastrada.
        self.assertEqual([(u["unit_name"], u["unit_id"]) for u in body["units"]],
                         [("Sem unidade cadastrada", None)])
        self.assertEqual(sum(band["student_count"] for band in body["distribution"]), 2)

    def test_item_analysis_returns_p_value_and_point_biserial_per_item(self):
        exam_id = self._published_exam()
        response = self.client.get(
            f"/api/v1/coordination/simulados/{exam_id}/report/items")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual([item["position"] for item in body["items"]], [1, 2, 3, 4])
        by_position = {item["position"]: item for item in body["items"]}
        self.assertEqual(by_position[1]["p_value"], 1.0)
        self.assertIsNone(by_position[1]["point_biserial"])
        self.assertEqual(by_position[3]["p_value"], 0.5)
        self.assertEqual(by_position[4]["option_distribution"]["BRANCO"], 1)

    def test_coordination_reads_a_classroom_report(self):
        exam_id = self._published_exam()
        response = self.client.get(
            f"/api/v1/coordination/simulados/{exam_id}/report/classroom"
            f"?class_id={self.class_id}")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["class_name"], "3A")
        self.assertEqual(body["student_count"], 2)
        self.assertEqual(body["average_percentage"], 75.0)
        self.assertEqual(body["most_missed"][0]["position"], 3)

    def test_a_teacher_reads_the_classroom_report_but_not_the_admin_routes(self):
        exam_id = self._published_exam()
        self._as("prof-a", role="TEACHER", roles=("teacher",))
        listing = self.client.get("/api/v1/teacher/simulados")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual([exam["id"] for exam in listing.json()], [exam_id])

        report = self.client.get(
            f"/api/v1/teacher/simulados/{exam_id}/report/classroom?class_id={self.class_id}")
        self.assertEqual(report.status_code, 200, report.text)
        self.assertEqual(report.json()["class_name"], "3A")

        forbidden = self.client.get(
            f"/api/v1/coordination/simulados/{exam_id}/report/school")
        self.assertEqual(forbidden.status_code, 403, forbidden.text)

    def test_a_teacher_from_another_school_gets_403(self):
        exam_id = self._published_exam()
        self._as("prof-b", role="TEACHER", school_id=self.school_b, roles=("teacher",))
        response = self.client.get(
            f"/api/v1/teacher/simulados/{exam_id}/report/classroom?class_id={self.class_id}")
        self.assertEqual(response.status_code, 403, response.text)

    def test_the_student_reads_her_own_boletim(self):
        exam_id = self._published_exam()
        self._as("student-alice", role="STUDENT", roles=("student",))
        listing = self.client.get("/api/v1/student/simulados")
        self.assertEqual(listing.status_code, 200, listing.text)
        self.assertEqual([(e["exam_id"], e["total_correct"]) for e in listing.json()],
                         [(exam_id, 4)])

        response = self.client.get(f"/api/v1/student/simulados/{exam_id}/report")
        self.assertEqual(response.status_code, 200, response.text)
        body = response.json()
        self.assertEqual(body["total_correct"], 4)
        self.assertEqual(body["total_percentage"], 100.0)
        self.assertEqual(body["score_kind"], "RAW")
        self.assertEqual(body["school_percentile"], 50.0)
        self.assertNotIn("evolution", body)

    def test_the_students_boletim_hides_the_anchor_items_correct_option(self):
        exam_id = self._published_exam()
        self._as("student-alice", role="STUDENT", roles=("student",))
        body = self.client.get(f"/api/v1/student/simulados/{exam_id}/report").json()
        by_position = {item["position"]: item for item in body["items"]}
        self.assertTrue(by_position[4]["is_anchor"])
        self.assertIsNone(by_position[4]["correct_option"])
        self.assertEqual(by_position[3]["correct_option"], "C")

    def test_an_unpublished_exam_is_not_visible_to_the_student(self):
        exam_id = self._applied_exam_with_two_typed_students()   # ainda APPLIED
        self._as("student-alice", role="STUDENT", roles=("student",))
        self.assertEqual(self.client.get("/api/v1/student/simulados").json(), [])
        response = self.client.get(f"/api/v1/student/simulados/{exam_id}/report")
        self.assertEqual(response.status_code, 403, response.text)

    def test_a_student_cannot_read_another_students_boletim_through_this_route(self):
        # A rota do aluno não aceita student_id nenhum: ela resolve a própria
        # matrícula. Bruno pedindo o mesmo caminho recebe o boletim DELE.
        exam_id = self._published_exam()
        self._as("student-bruno", role="STUDENT", roles=("student",))
        body = self.client.get(f"/api/v1/student/simulados/{exam_id}/report").json()
        self.assertEqual(body["student_id"], str(self.students["Bruno"]))
        self.assertEqual(body["total_correct"], 2)

    def test_a_person_with_no_active_enrollment_gets_403(self):
        exam_id = self._published_exam()
        self._as("coord-a", role="STUDENT", roles=("student",))
        response = self.client.get(f"/api/v1/student/simulados/{exam_id}/report")
        self.assertEqual(response.status_code, 403, response.text)
```

E acrescente ao import de `agente_ia_edu.api.routes.simulados`, no topo do arquivo de teste:

```python
from agente_ia_edu.api.routes.simulados import (
    simulados_coordination_router,
    simulados_student_router,
    simulados_teacher_router,
)
```

Além disso, em `SimuladosHTTPTestCase.setUp`, troque a construção do `AuthenticatedUserContext` para que `_as` continue funcionando para aluno — nada muda, `role` já é parâmetro.

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_simulados_http.py -v`
Expected: FAIL — `ImportError: cannot import name 'simulados_student_router'`

- [ ] **Step 3: Acrescentar a listagem de simulados publicados de um aluno**

Em `src/agente_ia_edu/services/simulado_reports.py`, acrescente ao topo `from datetime import date` e `from sqlalchemy import case, func, select` (substituindo o import atual de `select`), e insira antes do `__all__`:

```python
@dataclass(frozen=True)
class StudentExamEntry:
    exam_id: uuid.UUID
    exam_name: str
    application_date: date | None
    total_correct: int
    total_items: int
    total_percentage: float


async def list_published_exams_for_student(
    session: AsyncSession, *, school_id: uuid.UUID, student_id: uuid.UUID
) -> list[StudentExamEntry]:
    """Simulados JÁ PUBLICADOS em que este aluno tem cartão digitado.

    Publicado e só: enquanto a coordenação não fecha o simulado, o resultado
    não existe para o aluno. Agregado em SQL porque aqui só interessa o
    total - o boletim completo é `build_student_report`.
    """
    rows = (await session.execute(
        select(
            MockExam,
            func.count(MockExamResponse.id),
            func.sum(case((MockExamResponse.is_correct, 1), else_=0)),
        )
        .join(MockExamResponse, MockExamResponse.mock_exam_id == MockExam.id)
        .where(
            MockExam.school_id == school_id,
            MockExam.status == "PUBLISHED",
            MockExamResponse.student_id == student_id,
        )
        .group_by(MockExam.id)
        .order_by(MockExam.created_at.desc())
    )).all()

    entries: list[StudentExamEntry] = []
    for exam, total_items, total_correct in rows:
        total_items = int(total_items or 0)
        total_correct = int(total_correct or 0)
        entries.append(StudentExamEntry(
            exam_id=exam.id, exam_name=exam.name, application_date=exam.application_date,
            total_correct=total_correct, total_items=total_items,
            total_percentage=round(100.0 * total_correct / total_items, 1) if total_items else 0.0,
        ))
    return entries
```

Acrescente ao `__all__`: `"StudentExamEntry"`, `"list_published_exams_for_student"`.

- [ ] **Step 4: Acrescentar os schemas de relatório**

Acrescente ao fim de `src/agente_ia_edu/api/schemas/simulados.py`:

```python
class AreaResultItem(BaseModel):
    area_code: str
    label: str
    correct: int
    answered: int
    total_items: int
    percentage: float


class StudentItemResultItem(BaseModel):
    position: int
    area_code: str
    chosen_option: str | None = None
    # None em item-âncora: spec §6.4 proíbe expor o gabarito da âncora ao aluno.
    correct_option: str | None = None
    is_correct: bool
    is_anchor: bool


class StudentReportResponse(BaseModel):
    exam_id: UUID
    exam_name: str
    exam_status: str
    student_id: UUID
    score_kind: Literal["RAW"] = "RAW"
    by_area: list[AreaResultItem] = Field(default_factory=list)
    total_correct: int
    total_answered: int
    total_items: int
    total_percentage: float
    class_id: UUID | None = None
    class_percentile: float | None = None
    school_percentile: float
    items: list[StudentItemResultItem] = Field(default_factory=list)


class StudentExamItem(BaseModel):
    exam_id: UUID
    exam_name: str
    application_date: date | None = None
    total_correct: int
    total_items: int
    total_percentage: float
    score_kind: Literal["RAW"] = "RAW"


class AreaAverageItem(BaseModel):
    area_code: str
    label: str
    total_items: int
    average_correct: float
    average_percentage: float


class ScoreBandItem(BaseModel):
    label: str
    lower_percentage: float
    upper_percentage: float
    student_count: int


class GroupSummaryItem(BaseModel):
    class_id: UUID | None = None
    class_name: str
    student_count: int
    average_percentage: float


class UnitSummaryItem(BaseModel):
    # None + "Sem unidade cadastrada" quando a turma não tem SchoolUnit:
    # Class.school_unit_id é nullable e muitas escolas não têm nenhuma.
    unit_id: UUID | None = None
    unit_name: str
    student_count: int
    average_percentage: float


class MissedItemEntry(BaseModel):
    position: int
    area_code: str
    correct_option: str
    n_responses: int
    p_value: float


class ClassroomReportResponse(BaseModel):
    exam_id: UUID
    exam_name: str
    exam_status: str
    class_id: UUID
    class_name: str
    student_count: int
    score_kind: Literal["RAW"] = "RAW"
    by_area: list[AreaAverageItem] = Field(default_factory=list)
    average_percentage: float
    distribution: list[ScoreBandItem] = Field(default_factory=list)
    most_missed: list[MissedItemEntry] = Field(default_factory=list)
    other_classes: list[GroupSummaryItem] = Field(default_factory=list)


class SchoolReportResponse(BaseModel):
    exam_id: UUID
    exam_name: str
    exam_status: str
    student_count: int
    score_kind: Literal["RAW"] = "RAW"
    by_area: list[AreaAverageItem] = Field(default_factory=list)
    average_percentage: float
    distribution: list[ScoreBandItem] = Field(default_factory=list)
    classes: list[GroupSummaryItem] = Field(default_factory=list)
    units: list[UnitSummaryItem] = Field(default_factory=list)


class ItemStatisticsItem(BaseModel):
    item_id: UUID
    position: int
    area_code: str
    correct_option: str
    n_responses: int
    p_value: float
    # None quando o item não tem variância (todo mundo acertou ou errou):
    # discriminação NÃO MEDÍVEL, nunca 0.0 fabricado.
    point_biserial: float | None = None
    option_distribution: dict[str, int] = Field(default_factory=dict)


class ItemAnalysisResponse(BaseModel):
    exam_id: UUID
    exam_name: str
    exam_status: str
    student_count: int
    score_kind: Literal["RAW"] = "RAW"
    items: list[ItemStatisticsItem] = Field(default_factory=list)
```

- [ ] **Step 5: Acrescentar as rotas de relatório e os dois routers**

Em `src/agente_ia_edu/api/routes/simulados.py`, acrescente aos imports:

```python
from sqlalchemy import func, select

from ..dependencies import get_current_identity
from ..schemas.simulados import (
    AreaAverageItem,
    AreaResultItem,
    ClassroomReportResponse,
    GroupSummaryItem,
    ItemAnalysisResponse,
    ItemStatisticsItem,
    MissedItemEntry,
    SchoolReportResponse,
    ScoreBandItem,
    StudentExamItem,
    UnitSummaryItem,
    StudentItemResultItem,
    StudentReportResponse,
)
from ...db.models.mock_exam import MockExam, MockExamItem
from ...identity import ExternalIdentityContext
from ...services.authorization import AuthorizationService
from ...services.simulado_reports import (
    build_classroom_report,
    build_item_analysis,
    build_school_report,
    build_student_report,
    list_published_exams_for_student,
)
from ...services.student_enrollment_resolution import resolve_active_enrollment
```

E acrescente ao fim do arquivo (antes do `__all__`):

```python
simulados_teacher_router = APIRouter(prefix="/api/v1/teacher/simulados", tags=["simulados"])
simulados_student_router = APIRouter(prefix="/api/v1/student/simulados", tags=["simulados"])

_PRIVILEGED_OR_TEACHER_DENIED = "apenas professor ou coordenação da escola pode ver este painel"


def _authorize_teacher_or_coordinator(requester: Requester) -> None:
    """Cópia literal da regra de api/routes/teacher_portal.py:85 - professor
    OU coordenador COM escopo de escola. O escopo de turma NÃO é checado
    aqui, exatamente como em GET /api/v1/teacher/content/{id}/segments: usar
    uma regra diferente só para simulado seria um segundo modelo de
    permissão, que é o que o plano proíbe."""
    if requester.is_platform_admin:
        return
    role = (requester.role or "").upper()
    if role != "TEACHER" and not requester.is_privileged:
        raise SimuladoAuthError(_PRIVILEGED_OR_TEACHER_DENIED)
    if not requester.school_id:
        raise SimuladoAuthError("o solicitante não tem escopo de escola")


async def _load_exam_for_school(session, *, exam_id: UUID, school_id: uuid.UUID) -> MockExam:
    """Leitura de simulado para quem NÃO é coordenação (professor, aluno):
    mesma separação 404/403 de SimuladoService.get_exam."""
    exam = await session.get(MockExam, exam_id)
    if exam is None:
        raise SimuladoNotFoundError("simulado não encontrado")
    if str(exam.school_id) != str(school_id):
        raise SimuladoAuthError("este simulado pertence a outra escola")
    return exam


def _classroom_response(report) -> ClassroomReportResponse:
    return ClassroomReportResponse(
        exam_id=report.exam_id, exam_name=report.exam_name, exam_status=report.exam_status,
        class_id=report.class_id, class_name=report.class_name,
        student_count=report.student_count,
        by_area=[AreaAverageItem(area_code=a.area_code, label=a.label,
                                 total_items=a.total_items, average_correct=a.average_correct,
                                 average_percentage=a.average_percentage)
                 for a in report.by_area],
        average_percentage=report.average_percentage,
        distribution=[ScoreBandItem(label=b.label, lower_percentage=b.lower_percentage,
                                    upper_percentage=b.upper_percentage,
                                    student_count=b.student_count)
                      for b in report.distribution],
        most_missed=[MissedItemEntry(position=m.position, area_code=m.area_code,
                                     correct_option=m.correct_option,
                                     n_responses=m.n_responses, p_value=m.p_value)
                     for m in report.most_missed],
        other_classes=[GroupSummaryItem(class_id=g.class_id, class_name=g.class_name,
                                        student_count=g.student_count,
                                        average_percentage=g.average_percentage)
                       for g in report.other_classes],
    )


# ---------------------------------------------------------- coordenação --

@simulados_coordination_router.get(
    "/{exam_id}/report/school", response_model=SchoolReportResponse,
    summary="Painel da escola: médias por área, distribuição e comparação entre turmas",
)
async def get_school_report(
    exam_id: UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> SchoolReportResponse:
    requester = _requester(ctx)
    async with session_factory() as session:
        try:
            await SimuladoService(session).get_exam(exam_id, requester=requester)
            report = await build_school_report(session, exam_id=exam_id)
        except Exception as exc:  # noqa: BLE001
            raise _map_simulado_error(exc) from exc
    return SchoolReportResponse(
        exam_id=report.exam_id, exam_name=report.exam_name, exam_status=report.exam_status,
        student_count=report.student_count,
        by_area=[AreaAverageItem(area_code=a.area_code, label=a.label,
                                 total_items=a.total_items, average_correct=a.average_correct,
                                 average_percentage=a.average_percentage)
                 for a in report.by_area],
        average_percentage=report.average_percentage,
        distribution=[ScoreBandItem(label=b.label, lower_percentage=b.lower_percentage,
                                    upper_percentage=b.upper_percentage,
                                    student_count=b.student_count)
                      for b in report.distribution],
        classes=[GroupSummaryItem(class_id=g.class_id, class_name=g.class_name,
                                  student_count=g.student_count,
                                  average_percentage=g.average_percentage)
                 for g in report.classes],
        units=[UnitSummaryItem(unit_id=u.unit_id, unit_name=u.unit_name,
                               student_count=u.student_count,
                               average_percentage=u.average_percentage)
               for u in report.units],
    )


@simulados_coordination_router.get(
    "/{exam_id}/report/items", response_model=ItemAnalysisResponse,
    summary="Análise de itens sem TRI: proporção de acerto, ponto-bisserial e distratores",
)
async def get_item_analysis(
    exam_id: UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> ItemAnalysisResponse:
    requester = _requester(ctx)
    async with session_factory() as session:
        try:
            await SimuladoService(session).get_exam(exam_id, requester=requester)
            analysis = await build_item_analysis(session, exam_id=exam_id)
        except Exception as exc:  # noqa: BLE001
            raise _map_simulado_error(exc) from exc
    return ItemAnalysisResponse(
        exam_id=analysis.exam_id, exam_name=analysis.exam_name,
        exam_status=analysis.exam_status, student_count=analysis.student_count,
        items=[ItemStatisticsItem(
            item_id=stats.item_id, position=stats.position, area_code=stats.area_code,
            correct_option=stats.correct_option, n_responses=stats.n_responses,
            p_value=stats.p_value, point_biserial=stats.point_biserial,
            option_distribution=stats.option_distribution)
            for stats in analysis.items],
    )


@simulados_coordination_router.get(
    "/{exam_id}/report/classroom", response_model=ClassroomReportResponse,
    summary="Painel de uma turma, visto pela coordenação",
)
async def get_coordination_classroom_report(
    exam_id: UUID,
    class_id: UUID = Query(...),
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> ClassroomReportResponse:
    requester = _requester(ctx)
    async with session_factory() as session:
        try:
            await SimuladoService(session).get_exam(exam_id, requester=requester)
            report = await build_classroom_report(session, exam_id=exam_id, class_id=class_id)
        except Exception as exc:  # noqa: BLE001
            raise _map_simulado_error(exc) from exc
    return _classroom_response(report)


# ------------------------------------------------------------ professor --

@simulados_teacher_router.get(
    "", response_model=list[SimuladoSummary],
    summary="Simulados já publicados na escola do professor",
)
async def list_teacher_simulados(
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> list[SimuladoSummary]:
    requester = _requester(ctx)
    async with session_factory() as session:
        try:
            _authorize_teacher_or_coordinator(requester)
            rows = (await session.execute(
                select(MockExam, func.count(MockExamItem.id))
                .outerjoin(MockExamItem, MockExamItem.mock_exam_id == MockExam.id)
                .where(MockExam.school_id == uuid.UUID(str(requester.school_id)),
                       MockExam.status == "PUBLISHED")
                .group_by(MockExam.id)
                .order_by(MockExam.created_at.desc()))).all()
        except Exception as exc:  # noqa: BLE001
            raise _map_simulado_error(exc) from exc
    return [
        SimuladoSummary(id=exam.id, name=exam.name, status=exam.status,
                        exam_day=exam.exam_day, academic_year_id=exam.academic_year_id,
                        application_date=exam.application_date, item_count=int(count or 0))
        for exam, count in rows
    ]


@simulados_teacher_router.get(
    "/{exam_id}/report/classroom", response_model=ClassroomReportResponse,
    summary="Painel de uma turma, visto pelo professor",
)
async def get_teacher_classroom_report(
    exam_id: UUID,
    class_id: UUID = Query(...),
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> ClassroomReportResponse:
    requester = _requester(ctx)
    async with session_factory() as session:
        try:
            _authorize_teacher_or_coordinator(requester)
            await _load_exam_for_school(
                session, exam_id=exam_id, school_id=uuid.UUID(str(requester.school_id)))
            report = await build_classroom_report(session, exam_id=exam_id, class_id=class_id)
        except Exception as exc:  # noqa: BLE001
            raise _map_simulado_error(exc) from exc
    return _classroom_response(report)


# ---------------------------------------------------------------- aluno --

async def _authorize_student(identity: ExternalIdentityContext, session):
    """Mesmo par de api/routes/essay_submissions.py:107-129: papel STUDENT
    com escopo de escola, e a matrícula ATIVA de verdade resolvida no banco.

    Sem portão de módulo: PlatformModuleKey só define AGENTE_IA_EDU e
    REDACAO_IA (services/admin.py:65-69); criar uma chave nova aqui seria
    inventar modelo de permissão.
    """
    authz = AuthorizationService(session)
    context = await authz.resolve_context(identity)
    role_check = await authz.require_role(context, "STUDENT")
    if not role_check.allowed:
        raise SimuladoAuthError("apenas o próprio aluno acessa este boletim")
    if context.school_id is None:
        raise SimuladoAuthError("é necessário um contexto de escola ativo")
    enrollment = await resolve_active_enrollment(
        session, school_id=uuid.UUID(str(context.school_id)),
        external_user_id=identity.external_user_id)
    if enrollment is None:
        raise SimuladoAuthError("sem matrícula ativa: não há boletim a mostrar")
    return uuid.UUID(str(context.school_id)), enrollment.student_id


@simulados_student_router.get(
    "", response_model=list[StudentExamItem],
    summary="Simulados publicados em que o aluno tem cartão corrigido",
)
async def list_student_simulados(
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> list[StudentExamItem]:
    async with session_factory() as session:
        try:
            school_id, student_id = await _authorize_student(identity, session)
            entries = await list_published_exams_for_student(
                session, school_id=school_id, student_id=student_id)
        except Exception as exc:  # noqa: BLE001
            raise _map_simulado_error(exc) from exc
    return [
        StudentExamItem(exam_id=entry.exam_id, exam_name=entry.exam_name,
                        application_date=entry.application_date,
                        total_correct=entry.total_correct, total_items=entry.total_items,
                        total_percentage=entry.total_percentage)
        for entry in entries
    ]


@simulados_student_router.get(
    "/{exam_id}/report", response_model=StudentReportResponse,
    summary="Boletim do próprio aluno, por acerto bruto",
)
async def get_student_simulado_report(
    exam_id: UUID,
    identity: ExternalIdentityContext = Depends(get_current_identity),
    session_factory=Depends(get_session_factory),
) -> StudentReportResponse:
    async with session_factory() as session:
        try:
            school_id, student_id = await _authorize_student(identity, session)
            exam = await _load_exam_for_school(session, exam_id=exam_id, school_id=school_id)
            if exam.status != "PUBLISHED":
                raise SimuladoAuthError(
                    "este simulado ainda não foi publicado pela coordenação")
            report = await build_student_report(
                session, exam_id=exam_id, student_id=student_id)
        except Exception as exc:  # noqa: BLE001
            raise _map_simulado_error(exc) from exc
    return StudentReportResponse(
        exam_id=report.exam_id, exam_name=report.exam_name, exam_status=report.exam_status,
        student_id=report.student_id,
        by_area=[AreaResultItem(area_code=a.area_code, label=a.label, correct=a.correct,
                                answered=a.answered, total_items=a.total_items,
                                percentage=a.percentage) for a in report.by_area],
        total_correct=report.total_correct, total_answered=report.total_answered,
        total_items=report.total_items, total_percentage=report.total_percentage,
        class_id=report.class_id, class_percentile=report.class_percentile,
        school_percentile=report.school_percentile,
        items=[StudentItemResultItem(
            position=item.position, area_code=item.area_code,
            chosen_option=item.chosen_option, correct_option=item.correct_option,
            is_correct=item.is_correct, is_anchor=item.is_anchor)
            for item in report.items],
    )
```

E troque a última linha do arquivo por:

```python
__all__ = [
    "simulados_coordination_router",
    "simulados_student_router",
    "simulados_teacher_router",
]
```

- [ ] **Step 6: Registrar os dois routers novos em `app.py`**

Troque a linha de import acrescentada na Task 8 por:

```python
from .routes.simulados import (
    simulados_coordination_router,
    simulados_student_router,
    simulados_teacher_router,
)
```

E, logo depois do `include_router` da Task 8:

```python
    app.include_router(simulados_teacher_router, dependencies=reception_only_guard)
    app.include_router(simulados_student_router, dependencies=reception_only_guard)
```

- [ ] **Step 7: Rodar e ver passar**

Run: `python -m pytest tests/test_simulados_http.py -v`
Expected: PASS — 29 testes.

- [ ] **Step 8: Rodar a camada de backend inteira desta fase**

Run: `python -m pytest tests/test_simulado_scoring.py tests/test_simulado_service.py tests/test_simulado_manual_entry.py tests/test_simulado_reports.py tests/test_simulados_http.py -q`
Expected: PASS — 107 testes.

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/api/schemas/simulados.py src/agente_ia_edu/api/routes/simulados.py \
        src/agente_ia_edu/services/simulado_reports.py src/agente_ia_edu/api/app.py \
        tests/test_simulados_http.py
git commit -m "feat(simulados): rotas dos quatro relatorios de nota bruta

Fase 2 Task 9. Coordenacao le escola/itens/turma, professor le a turma,
aluno le o proprio boletim (a rota nao aceita student_id: resolve a propria
matricula). Simulado nao publicado nao aparece para o aluno, e a ancora
nunca expoe a alternativa correta. score_kind=RAW em todo schema de nota.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: Frontend da coordenação — gestão do simulado, gabarito e digitação

**Files:**
- Modify: `src/agente_ia_edu/web/coordination.html` (item de menu antes da linha 62; `<section id="view-simulados">` antes da linha 477)
- Modify: `src/agente_ia_edu/web/coordination.js` (`titleMap` na linha 80-91; `loadCurrentView` na linha 101; bloco novo antes de `// Initial Load`)
- Modify: `src/agente_ia_edu/web/coordination.css`
- Test: `tests/test_simulados_frontend.js`

**Interfaces:**
- Consumes: as rotas da Task 8.
- Produces: `window.openSimuladoDetail(examId)`, e os ids de DOM `sim-list-container`, `sim-new-form`, `sim-detail`, `sim-areas-input`, `sim-key-input`, `sim-class-select`, `sim-student-select`, `sim-answers-input`, `sim-type-form`, `sim-type-result`, `sim-status-actions`.

- [ ] **Step 1: Escrever o teste que falha**

Crie `tests/test_simulados_frontend.js`:

```javascript
/**
 * Simulados — Fase 2. Asserções estáticas (node:test + regex) sobre o
 * HTML/JS/CSS dos três portais, no mesmo formato de
 * tests/test_segmentation_bulk_assignment_frontend.js.
 *
 * O que se prova aqui: que a tela existe, que ela chama as rotas REAIS da
 * fase, que ela mostra o número que a API devolveu (nunca um calculado no
 * cliente), que ela rotula a nota como acerto bruto, e que o boletim do
 * aluno não inventa gráfico de evolução nem revela a âncora.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const read = (name) => fs.readFileSync(path.join(WEB, name), 'utf8');

const coordHtml = read('coordination.html');
const coordJs = read('coordination.js');
const coordCss = read('coordination.css');

const COORD_BLOCK = coordJs.slice(coordJs.indexOf('SIMULADOS (Fase 2'));

// ------------------------------------------------------------ markup --

test('coordination gains a "Simulados" nav entry and a view panel', () => {
  assert.match(coordHtml, /<button class="nav-item" data-view="simulados">/);
  assert.match(coordHtml, /id="view-simulados" class="view-panel"/);
});

test('the coordination panel has the list, the create form, the answer-key editor and the typing form', () => {
  for (const id of ['sim-list-container', 'sim-new-form', 'sim-name-input',
                    'sim-exam-day-select', 'sim-year-input', 'sim-detail',
                    'sim-areas-input', 'sim-key-input', 'sim-save-key-btn',
                    'sim-status-actions', 'sim-class-select', 'sim-student-select',
                    'sim-answers-input', 'sim-type-form', 'sim-type-result']) {
    assert.match(coordHtml, new RegExp(`id="${id}"`), `faltou #${id}`);
  }
});

// ------------------------------------------------------------ wiring --

test('the view is wired into switchView/loadCurrentView like every other one', () => {
  assert.match(coordJs, /'simulados': \{ title: 'Simulados'/);
  assert.match(coordJs, /if \(state\.currentView === 'simulados'\) loadSimulados\(\);/);
});

test('every call goes to the REAL Fase 2 coordination routes', () => {
  assert.match(COORD_BLOCK, /fetch\('\/api\/v1\/coordination\/simulados'/);
  assert.match(COORD_BLOCK, /\/api\/v1\/coordination\/simulados\/\$\{examId\}\/answer-key/);
  assert.match(COORD_BLOCK, /\/api\/v1\/coordination\/simulados\/\$\{examId\}\/status/);
  assert.match(COORD_BLOCK, /\/api\/v1\/coordination\/simulados\/\$\{examId\}\/classes/);
  assert.match(COORD_BLOCK, /\/api\/v1\/coordination\/simulados\/\$\{examId\}\/roster\?class_id=/);
  assert.match(COORD_BLOCK, /\/api\/v1\/coordination\/simulados\/\$\{examId\}\/responses/);
});

test('it reuses the portal auth header instead of a parallel mechanism', () => {
  assert.match(COORD_BLOCK, /'Authorization': `Bearer \$\{state\.coordinatorId\}`/);
});

// -------------------------------------------------------- parsers --

test('the answer-key parser reads posicao;AREA;ALTERNATIVA and the optional ANCORA marker', () => {
  assert.match(COORD_BLOCK, /function simParseAnswerKey\(text\)/);
  assert.match(COORD_BLOCK, /parts\[3\]\.toUpperCase\(\) === 'ANCORA'/);
  assert.match(COORD_BLOCK, /anchor_key: isAnchor \? \(parts\[4\] \|\| ''\) : null/);
});

test('the typing parser demands exactly one mark per item and treats "." as blank', () => {
  assert.match(COORD_BLOCK, /function simParseAnswers\(text, totalItems\)/);
  assert.match(COORD_BLOCK, /replace\(\/\[\^A-E\.\]\/g, ''\)/);
  assert.match(COORD_BLOCK, /clean\.length !== totalItems/);
  assert.match(COORD_BLOCK, /chosen_option: ch === '\.' \? null : ch/);
});

// ------------------------------------------------------- honestidade --

test('the typing result shows the counts the API returned, never a client-side guess', () => {
  assert.match(COORD_BLOCK, /data\.created/);
  assert.match(COORD_BLOCK, /data\.replaced/);
  assert.match(COORD_BLOCK, /data\.total_correct/);
  assert.match(COORD_BLOCK, /data\.total_items/);
});

test('the status buttons offer only the transitions this phase supports', () => {
  assert.match(COORD_BLOCK, /const SIM_NEXT_STATUS = \{/);
  assert.doesNotMatch(COORD_BLOCK, /'SCANNED'/);
  assert.doesNotMatch(COORD_BLOCK, /'CALIBRATED'/);
});

test('an API error is surfaced to the operator, never swallowed', () => {
  assert.match(COORD_BLOCK, /async function simDetailOf\(response\)/);
  assert.match(COORD_BLOCK, /body && body\.detail \? String\(body\.detail\)/);
  assert.match(COORD_BLOCK, /showAlert\(await simDetailOf\(res\), 'danger'\)/);
});

test('every interpolated value goes through the existing escaper', () => {
  assert.match(COORD_BLOCK, /coordEsc\(/);
});

// -------------------------------------------------------------- css --

test('CSS: the simulado tables and forms reflow under 768px', () => {
  assert.match(coordCss, /\.sim-table/);
  assert.match(coordCss, /\.sim-detail-grid/);
  assert.match(coordCss, /@media \(max-width: 768px\)[\s\S]*\.sim-detail-grid/);
});

// --------------------------------------------------------- no AI/TRI --

test('NO AI and no TRI vocabulary anywhere in the Fase 2 coordination block', () => {
  // A nota desta fase é acerto bruto. Se "theta", "calibra" ou "escala de
  // referência" aparecerem nesta tela, ou a fase vazou de escopo ou o texto
  // está prometendo ao operador algo que o cálculo não faz.
  assert.doesNotMatch(COORD_BLOCK, /openai|gpt-|AsyncOpenAI|gamif/i);
  assert.doesNotMatch(COORD_BLOCK, /theta|calibra|escala de refer/i);
});
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `node --test tests/test_simulados_frontend.js`
Expected: FAIL — o primeiro teste já quebra (`data-view="simulados"` não existe).

- [ ] **Step 3: Acrescentar o markup**

Em `src/agente_ia_edu/web/coordination.html`, antes da linha 62 (`<button class="nav-item" data-view="profile">`):

```html
        <button class="nav-item" data-view="simulados">
          <span class="icon">📄</span> Simulados
        </button>
```

E antes da linha 477 (`<section id="view-profile" class="view-panel">`):

```html
        <!-- SIMULADOS (Fase 2) — criação, gabarito, ciclo de vida e
             digitação manual. A nota desta fase é ACERTO BRUTO: nenhuma
             escala de referência, nenhuma previsão de nota do ENEM. -->
        <section id="view-simulados" class="view-panel">
          <div class="card">
            <div class="card-header">
              <h3>📄 Simulados da escola</h3>
            </div>
            <div id="sim-list-container">
              <p class="empty-text">Carregando simulados...</p>
            </div>
          </div>

          <div class="card">
            <div class="card-header">
              <h3>➕ Novo simulado</h3>
            </div>
            <form id="sim-new-form" class="sim-new-form">
              <div class="form-group">
                <label for="sim-name-input">Nome</label>
                <input type="text" id="sim-name-input" class="text-input"
                       placeholder="Simulado ENEM — 1º dia" required>
              </div>
              <div class="form-group">
                <label for="sim-exam-day-select">Dia da prova</label>
                <select id="sim-exam-day-select" class="select-input">
                  <option value="1">1º dia</option>
                  <option value="2">2º dia</option>
                </select>
              </div>
              <div class="form-group">
                <label for="sim-year-input">ID do ano letivo</label>
                <input type="text" id="sim-year-input" class="text-input"
                       placeholder="UUID do ano letivo" required>
              </div>
              <div class="form-group">
                <label for="sim-date-input">Data de aplicação (opcional)</label>
                <input type="date" id="sim-date-input" class="text-input">
              </div>
              <button type="submit" class="btn btn-primary" id="sim-create-btn">Criar rascunho</button>
            </form>
          </div>

          <div class="card" id="sim-detail" hidden>
            <div class="card-header sim-detail-header">
              <div>
                <h3>🗂️ <span id="sim-detail-name">Simulado</span></h3>
                <p class="empty-text">Status: <span id="sim-detail-status">-</span> •
                  <span id="sim-detail-count">0</span> questões</p>
              </div>
              <button class="btn btn-secondary" type="button" id="sim-close-detail-btn">Fechar</button>
            </div>

            <div class="sim-detail-grid">
              <div>
                <h4>Gabarito</h4>
                <div class="form-group">
                  <label for="sim-areas-input">Áreas — uma por linha: <code>CODIGO|Rótulo</code></label>
                  <textarea id="sim-areas-input" class="text-input" rows="4"
                            placeholder="LC|Linguagens e Códigos&#10;CH|Ciências Humanas"></textarea>
                </div>
                <div class="form-group">
                  <label for="sim-key-input">Gabarito — uma por linha:
                    <code>posicao;AREA;ALTERNATIVA</code>, e
                    <code>;ANCORA;chave</code> para item-âncora</label>
                  <textarea id="sim-key-input" class="text-input" rows="10"
                            placeholder="1;LC;A&#10;2;LC;B&#10;3;CH;C;ANCORA;CH-ENEM-2024-91"></textarea>
                </div>
                <button class="btn btn-primary" type="button" id="sim-save-key-btn">Salvar gabarito</button>
                <div id="sim-key-result"></div>
              </div>

              <div>
                <h4>Ciclo de vida</h4>
                <div id="sim-status-actions"></div>

                <h4>Digitação manual</h4>
                <p class="empty-text">Caminho de exceção permanente: cartão rasgado,
                  prova a lápis, scanner fora do ar.</p>
                <form id="sim-type-form">
                  <div class="form-group">
                    <label for="sim-class-select">Turma</label>
                    <select id="sim-class-select" class="select-input">
                      <option value="">Carregando turmas...</option>
                    </select>
                  </div>
                  <div class="form-group">
                    <label for="sim-student-select">Aluno</label>
                    <select id="sim-student-select" class="select-input">
                      <option value="">Selecione uma turma primeiro</option>
                    </select>
                  </div>
                  <div class="form-group">
                    <label for="sim-answers-input">Marcações, uma por questão
                      (use <code>.</code> para branco)</label>
                    <input type="text" id="sim-answers-input" class="text-input"
                           placeholder="ABCDE.ACB...">
                  </div>
                  <button type="submit" class="btn btn-primary" id="sim-type-btn">Gravar cartão</button>
                </form>
                <div id="sim-type-result"></div>
              </div>
            </div>
          </div>
        </section>
```

- [ ] **Step 4: Acrescentar o JS**

Em `src/agente_ia_edu/web/coordination.js`, no `titleMap` (linha ~80), depois da entrada `'materials'`:

```javascript
      'simulados': { title: 'Simulados', sub: 'Criação, gabarito, digitação manual e correção por acerto bruto' },
```

Em `loadCurrentView` (linha ~101), depois da linha de `'materials'`:

```javascript
    if (state.currentView === 'simulados') loadSimulados();
```

E, imediatamente antes do comentário `// Initial Load` no fim do arquivo:

```javascript
  // ===================================================================
  // SIMULADOS (Fase 2 do spec de correção de simulados).
  //
  // A nota desta fase é ACERTO BRUTO. Nenhum texto desta tela pode
  // prometer ao operador a nota em faixa do ENEM: essa conversão chega na
  // fase 3, e o spec §6.3 é explícito sobre o estrago de trocar uma pela
  // outra. Um teste de frontend falha se o vocabulário da fase 3 aparecer
  // aqui.
  //
  // Os dois status que dependem das fases seguintes existem no modelo mas
  // não têm máquina que os produza ainda, então SIM_NEXT_STATUS
  // simplesmente não os oferece como ação.
  // ===================================================================

  const SIM_NEXT_STATUS = {
    DRAFT: { to: 'PRINTED', label: 'Marcar como impresso' },
    PRINTED: { to: 'APPLIED', label: 'Marcar como aplicado' },
    APPLIED: { to: 'PUBLISHED', label: 'Publicar resultados' },
  };

  function simHeaders() {
    return {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${state.coordinatorId}`,
    };
  }

  async function simDetailOf(response) {
    try {
      const body = await response.json();
      return body && body.detail ? String(body.detail) : `Erro ${response.status}`;
    } catch (err) {
      return `Erro ${response.status}`;
    }
  }

  function simParseAreas(text) {
    return String(text || '').split('\n').map((line) => line.trim()).filter(Boolean)
      .map((line, index) => {
        const parts = line.split('|');
        if (parts.length < 2) {
          throw new Error(`Linha de área inválida: "${line}" — use CODIGO|Rótulo`);
        }
        return { code: parts[0].trim(), label: parts[1].trim(), display_order: index + 1 };
      });
  }

  function simParseAnswerKey(text) {
    return String(text || '').split('\n').map((line) => line.trim()).filter(Boolean)
      .map((line) => {
        const parts = line.split(';').map((part) => part.trim());
        if (parts.length < 3) {
          throw new Error(`Linha de gabarito inválida: "${line}" — use posicao;AREA;ALTERNATIVA`);
        }
        const isAnchor = parts.length > 3 && parts[3].toUpperCase() === 'ANCORA';
        return {
          position: Number(parts[0]),
          area_code: parts[1].toUpperCase(),
          correct_option: parts[2].toUpperCase(),
          is_anchor: isAnchor,
          anchor_key: isAnchor ? (parts[4] || '') : null,
        };
      });
  }

  function simParseAnswers(text, totalItems) {
    // Uma marcação por questão, na ordem. Ponto é branco, e branco é
    // DIGITADO: a API recusa cartão parcial de propósito, porque um cartão
    // digitado pela metade é indistinguível de um cartão com o resto em
    // branco - e a diferença muda a taxa de acerto do item.
    const clean = String(text || '').toUpperCase().replace(/[^A-E.]/g, '');
    if (clean.length !== totalItems) {
      throw new Error(
        `Digite exatamente ${totalItems} marcações (use "." para branco). Recebi ${clean.length}.`);
    }
    return clean.split('').map((ch, index) => ({
      position: index + 1,
      chosen_option: ch === '.' ? null : ch,
    }));
  }

  async function loadSimulados() {
    const container = document.getElementById('sim-list-container');
    try {
      const res = await fetch('/api/v1/coordination/simulados', { headers: simHeaders() });
      if (res.status === 403) {
        container.innerHTML = '<p class="empty-text text-danger">⚠️ Você não tem permissão para administrar simulados desta escola.</p>';
        return;
      }
      if (!res.ok) throw new Error(await simDetailOf(res));
      const data = await res.json();
      if (!data.length) {
        container.innerHTML = '<p class="empty-text">Nenhum simulado cadastrado ainda.</p>';
        return;
      }
      container.innerHTML = `
        <table class="sim-table">
          <thead><tr><th>Simulado</th><th>Dia</th><th>Questões</th><th>Status</th><th></th></tr></thead>
          <tbody>
            ${data.map((exam) => `
              <tr>
                <td>${coordEsc(exam.name)}</td>
                <td>${coordEsc(exam.exam_day)}º</td>
                <td>${coordEsc(exam.item_count)}</td>
                <td><span class="sim-status">${coordEsc(exam.status)}</span></td>
                <td><button class="btn btn-secondary sim-open-btn" type="button"
                            data-exam-id="${coordEsc(exam.id)}">Abrir</button></td>
              </tr>`).join('')}
          </tbody>
        </table>`;
      container.querySelectorAll('.sim-open-btn').forEach((button) => {
        button.addEventListener('click', () => window.openSimuladoDetail(button.dataset.examId));
      });
    } catch (err) {
      console.warn('Simulados error:', err);
      container.innerHTML = '<p class="empty-text">Não foi possível carregar os simulados agora.</p>';
    }
  }

  window.openSimuladoDetail = async function (examId) {
    const res = await fetch(`/api/v1/coordination/simulados/${examId}`, { headers: simHeaders() });
    if (!res.ok) {
      showAlert(await simDetailOf(res), 'danger');
      return;
    }
    const exam = await res.json();
    state.simulado = { exam, classes: [], roster: [] };

    document.getElementById('sim-detail').hidden = false;
    document.getElementById('sim-detail-name').textContent = exam.name;
    document.getElementById('sim-detail-status').textContent = exam.status;
    document.getElementById('sim-detail-count').textContent = exam.item_count;
    document.getElementById('sim-areas-input').value =
      exam.areas.map((area) => `${area.code}|${area.label}`).join('\n');
    document.getElementById('sim-key-input').value = exam.items.map((item) => (
      item.is_anchor
        ? `${item.position};${item.area_code};${item.correct_option};ANCORA;${item.anchor_key || ''}`
        : `${item.position};${item.area_code};${item.correct_option}`
    )).join('\n');
    document.getElementById('sim-key-result').innerHTML = '';
    document.getElementById('sim-type-result').innerHTML = '';
    document.getElementById('sim-answers-input').value = '';

    renderSimStatusActions();
    loadSimuladoClasses();
  };

  function renderSimStatusActions() {
    const container = document.getElementById('sim-status-actions');
    const exam = state.simulado && state.simulado.exam;
    if (!exam) return;
    const next = SIM_NEXT_STATUS[exam.status];
    container.innerHTML = next
      ? `<button class="btn btn-primary" type="button" id="sim-advance-btn">${coordEsc(next.label)}</button>`
      : '<p class="empty-text">Simulado publicado: o ciclo desta fase terminou.</p>';
    const button = document.getElementById('sim-advance-btn');
    if (button) button.addEventListener('click', () => simAdvanceStatus(next.to));
  }

  async function simAdvanceStatus(toStatus) {
    const examId = state.simulado.exam.id;
    const res = await fetch(`/api/v1/coordination/simulados/${examId}/status`, {
      method: 'POST', headers: simHeaders(), body: JSON.stringify({ to_status: toStatus }),
    });
    if (!res.ok) {
      showAlert(await simDetailOf(res), 'danger');
      return;
    }
    const data = await res.json();
    state.simulado.exam.status = data.status;
    document.getElementById('sim-detail-status').textContent = data.status;
    renderSimStatusActions();
    loadSimulados();
  }

  document.getElementById('sim-new-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const payload = {
      academic_year_id: document.getElementById('sim-year-input').value.trim(),
      name: document.getElementById('sim-name-input').value.trim(),
      exam_day: Number(document.getElementById('sim-exam-day-select').value),
      application_date: document.getElementById('sim-date-input').value || null,
    };
    const res = await fetch('/api/v1/coordination/simulados', {
      method: 'POST', headers: simHeaders(), body: JSON.stringify(payload),
    });
    if (!res.ok) {
      showAlert(await simDetailOf(res), 'danger');
      return;
    }
    const exam = await res.json();
    document.getElementById('sim-name-input').value = '';
    await loadSimulados();
    window.openSimuladoDetail(exam.id);
  });

  document.getElementById('sim-close-detail-btn').addEventListener('click', () => {
    document.getElementById('sim-detail').hidden = true;
  });

  document.getElementById('sim-save-key-btn').addEventListener('click', async () => {
    const examId = state.simulado.exam.id;
    const result = document.getElementById('sim-key-result');
    let payload;
    try {
      payload = {
        areas: simParseAreas(document.getElementById('sim-areas-input').value),
        items: simParseAnswerKey(document.getElementById('sim-key-input').value),
      };
    } catch (err) {
      result.innerHTML = `<p class="text-danger">${coordEsc(err.message)}</p>`;
      return;
    }
    const res = await fetch(`/api/v1/coordination/simulados/${examId}/answer-key`, {
      method: 'PUT', headers: simHeaders(), body: JSON.stringify(payload),
    });
    if (!res.ok) {
      result.innerHTML = `<p class="text-danger">${coordEsc(await simDetailOf(res))}</p>`;
      return;
    }
    const exam = await res.json();
    state.simulado.exam = exam;
    document.getElementById('sim-detail-count').textContent = exam.item_count;
    result.innerHTML = `<p class="text-success">Gabarito salvo: ${coordEsc(exam.item_count)} questões.</p>`;
    loadSimulados();
  });

  async function loadSimuladoClasses() {
    const examId = state.simulado.exam.id;
    const select = document.getElementById('sim-class-select');
    const res = await fetch(`/api/v1/coordination/simulados/${examId}/classes`, {
      headers: simHeaders(),
    });
    if (!res.ok) {
      select.innerHTML = '<option value="">Não foi possível carregar as turmas</option>';
      return;
    }
    const data = await res.json();
    state.simulado.classes = data;
    select.innerHTML = '<option value="">Selecione a turma</option>' + data.map((entry) => `
      <option value="${coordEsc(entry.class_id)}">
        ${coordEsc(entry.class_name)} — ${coordEsc(entry.typed_count)} de ${coordEsc(entry.student_count)} digitados
      </option>`).join('');
  }

  document.getElementById('sim-class-select').addEventListener('change', async (event) => {
    const classId = event.target.value;
    const select = document.getElementById('sim-student-select');
    if (!classId) {
      select.innerHTML = '<option value="">Selecione uma turma primeiro</option>';
      return;
    }
    const examId = state.simulado.exam.id;
    const res = await fetch(
      `/api/v1/coordination/simulados/${examId}/roster?class_id=${encodeURIComponent(classId)}`,
      { headers: simHeaders() });
    if (!res.ok) {
      select.innerHTML = '<option value="">Não foi possível carregar os alunos</option>';
      return;
    }
    const data = await res.json();
    state.simulado.roster = data;
    select.innerHTML = '<option value="">Selecione o aluno</option>' + data.map((student) => `
      <option value="${coordEsc(student.student_id)}">
        ${coordEsc(student.full_name)}${student.has_responses ? ' (já digitado)' : ''}
      </option>`).join('');
  });

  document.getElementById('sim-type-form').addEventListener('submit', async (event) => {
    event.preventDefault();
    const result = document.getElementById('sim-type-result');
    const exam = state.simulado.exam;
    const studentId = document.getElementById('sim-student-select').value;
    if (!studentId) {
      result.innerHTML = '<p class="text-danger">Selecione o aluno antes de gravar o cartão.</p>';
      return;
    }
    let responses;
    try {
      responses = simParseAnswers(document.getElementById('sim-answers-input').value,
                                  exam.item_count);
    } catch (err) {
      result.innerHTML = `<p class="text-danger">${coordEsc(err.message)}</p>`;
      return;
    }
    const res = await fetch(`/api/v1/coordination/simulados/${exam.id}/responses`, {
      method: 'POST', headers: simHeaders(),
      body: JSON.stringify({ student_id: studentId, responses }),
    });
    if (!res.ok) {
      result.innerHTML = `<p class="text-danger">${coordEsc(await simDetailOf(res))}</p>`;
      return;
    }
    const data = await res.json();
    result.innerHTML = `<p class="text-success">
      Cartão gravado: ${coordEsc(data.created)} marcações
      (${coordEsc(data.replaced)} substituídas) —
      ${coordEsc(data.total_correct)} de ${coordEsc(data.total_items)} acertos brutos.</p>`;
    document.getElementById('sim-answers-input').value = '';
    loadSimuladoClasses();
  });
```

- [ ] **Step 5: Acrescentar o CSS**

Ao fim de `src/agente_ia_edu/web/coordination.css`:

```css
/* SIMULADOS (Fase 2) */
.sim-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.sim-table th, .sim-table td { padding: 8px 10px; border-bottom: 1px solid #e2e8f0; text-align: left; }
.sim-status { font-size: 12px; font-weight: 600; letter-spacing: .04em; }
.sim-new-form { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 12px; }
.sim-detail-header { display: flex; justify-content: space-between; align-items: flex-start; gap: 12px; }
.sim-detail-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; }
.sim-detail-grid h4 { margin: 12px 0 8px; font-size: 14px; }
.sim-detail-grid textarea { width: 100%; font-family: ui-monospace, monospace; font-size: 13px; }

@media (max-width: 768px) {
  .sim-detail-grid { grid-template-columns: 1fr; }
  .sim-new-form { grid-template-columns: 1fr; }
  .sim-table { display: block; overflow-x: auto; }
}
```

- [ ] **Step 6: Rodar e ver passar**

Run: `node --test tests/test_simulados_frontend.js`
Expected: PASS — 13 testes.

- [ ] **Step 7: Confirmar que o JS continua sintaticamente válido**

Run: `node --check src/agente_ia_edu/web/coordination.js`
Expected: sem saída (exit 0).

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/web/coordination.html src/agente_ia_edu/web/coordination.js \
        src/agente_ia_edu/web/coordination.css tests/test_simulados_frontend.js
git commit -m "feat(simulados): tela da coordenacao - gestao, gabarito e digitacao

Fase 2 Task 10. Gabarito colado como texto (posicao;AREA;ALTERNATIVA, com
ANCORA;chave opcional) e cartao digitado como uma marcacao por questao, com
'.' para branco. Os status SCANNED/CALIBRATED nao aparecem como acao.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Frontend da coordenação — painel da escola e análise de itens

**Files:**
- Modify: `src/agente_ia_edu/web/coordination.html` (dentro de `#view-simulados`, depois de `#sim-detail`)
- Modify: `src/agente_ia_edu/web/coordination.js` (dentro do bloco de simulados da Task 10)
- Modify: `src/agente_ia_edu/web/coordination.css`
- Test: `tests/test_simulados_frontend.js`

**Interfaces:**
- Consumes: `GET .../report/school`, `GET .../report/items` (Task 9); `state.simulado`, `simHeaders`, `simDetailOf`, `coordEsc` (Task 10).
- Produces: `loadSimuladoReports()` e os ids `sim-reports`, `sim-school-summary`, `sim-distribution`, `sim-classes-table`, `sim-items-table`.

- [ ] **Step 1: Escrever o teste que falha**

Acrescente a `tests/test_simulados_frontend.js`:

```javascript
// ------------------------------------------- relatórios da coordenação --

test('the coordination panel has the school report and the item analysis', () => {
  for (const id of ['sim-reports', 'sim-school-summary', 'sim-distribution',
                    'sim-classes-table', 'sim-units-table', 'sim-items-table']) {
    assert.match(coordHtml, new RegExp(`id="${id}"`), `faltou #${id}`);
  }
});

test('the reports call the real report routes', () => {
  assert.match(COORD_BLOCK, /\/api\/v1\/coordination\/simulados\/\$\{examId\}\/report\/school/);
  assert.match(COORD_BLOCK, /\/api\/v1\/coordination\/simulados\/\$\{examId\}\/report\/items/);
});

test('the school report labels the score as acerto bruto, never as an ENEM-scale score', () => {
  assert.match(COORD_BLOCK, /acerto bruto/i);
  assert.doesNotMatch(COORD_BLOCK, /nota do ENEM|previs[aã]o de nota/i);
});

test('the item analysis shows a non-measurable discrimination honestly instead of 0', () => {
  assert.match(COORD_BLOCK, /item\.point_biserial == null \? 'não medível'/);
});

test('a negative point-biserial is flagged as the answer-key smell it is', () => {
  assert.match(COORD_BLOCK, /item\.point_biserial < 0/);
  assert.match(COORD_BLOCK, /confira o gabarito/i);
});

test('the distractor distribution is rendered from the API payload', () => {
  assert.match(COORD_BLOCK, /item\.option_distribution/);
});

test('the school report compares units as well as classes (spec §7)', () => {
  assert.match(COORD_BLOCK, /school\.units\.map/);
  assert.match(COORD_BLOCK, /group\.unit_name/);
});

test('CSS: the report tables reflow under 768px', () => {
  assert.match(coordCss, /\.sim-report-grid/);
  assert.match(coordCss, /@media \(max-width: 768px\)[\s\S]*\.sim-report-grid/);
});
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `node --test tests/test_simulados_frontend.js`
Expected: FAIL — `faltou #sim-reports`.

- [ ] **Step 3: Acrescentar o markup**

Em `src/agente_ia_edu/web/coordination.html`, dentro de `<section id="view-simulados">`, logo depois do `</div>` que fecha `#sim-detail`:

```html
          <div class="card" id="sim-reports" hidden>
            <div class="card-header">
              <h3>📊 Resultados — nota por acerto bruto</h3>
              <button class="btn btn-secondary" type="button" id="sim-report-refresh-btn">Atualizar</button>
            </div>
            <div class="sim-report-grid">
              <div>
                <h4>Escola</h4>
                <div id="sim-school-summary"><p class="empty-text">Carregando...</p></div>
                <h4>Distribuição das notas</h4>
                <div id="sim-distribution"></div>
                <h4>Turmas</h4>
                <div id="sim-classes-table"></div>
                <h4>Unidades</h4>
                <div id="sim-units-table"></div>
              </div>
              <div>
                <h4>Análise de itens</h4>
                <p class="empty-text">Proporção de acerto e correlação ponto-bisserial.
                  Sem TRI nesta fase — dificuldade e discriminação calibradas chegam depois.</p>
                <div id="sim-items-table"></div>
              </div>
            </div>
          </div>
```

- [ ] **Step 4: Acrescentar o JS**

Em `src/agente_ia_edu/web/coordination.js`, dentro do bloco de simulados, acrescente ao fim de `window.openSimuladoDetail` (depois de `loadSimuladoClasses();`):

```javascript
    loadSimuladoReports();
```

E acrescente, depois de `loadSimuladoClasses`:

```javascript
  // Relatórios de nota BRUTA. O rótulo "acerto bruto" não é enfeite: o spec
  // §6.3 distingue duas coisas que esta fase não produz nenhuma das duas, e
  // tratar uma contagem de acerto como qualquer uma delas seria erro de
  // interpretação, não de cálculo. Aqui a nota é contagem de acerto, e o
  // texto da tela diz isso.
  async function loadSimuladoReports() {
    const examId = state.simulado.exam.id;
    const container = document.getElementById('sim-reports');
    container.hidden = false;

    const schoolRes = await fetch(
      `/api/v1/coordination/simulados/${examId}/report/school`, { headers: simHeaders() });
    const summary = document.getElementById('sim-school-summary');
    if (!schoolRes.ok) {
      summary.innerHTML = `<p class="empty-text">${coordEsc(await simDetailOf(schoolRes))}</p>`;
      document.getElementById('sim-distribution').innerHTML = '';
      document.getElementById('sim-classes-table').innerHTML = '';
      document.getElementById('sim-units-table').innerHTML = '';
    } else {
      const school = await schoolRes.json();
      summary.innerHTML = `
        <p><strong>${coordEsc(school.student_count)}</strong> aluno(s) corrigido(s) —
           média de <strong>${coordEsc(school.average_percentage)}%</strong> de acerto bruto.</p>
        <table class="sim-table">
          <thead><tr><th>Área</th><th>Média de acertos</th><th>%</th></tr></thead>
          <tbody>${school.by_area.map((area) => `
            <tr><td>${coordEsc(area.label)}</td>
                <td>${coordEsc(area.average_correct)} de ${coordEsc(area.total_items)}</td>
                <td>${coordEsc(area.average_percentage)}%</td></tr>`).join('')}
          </tbody>
        </table>`;
      document.getElementById('sim-distribution').innerHTML = `
        <table class="sim-table">
          <tbody>${school.distribution.map((band) => `
            <tr><td>${coordEsc(band.label)}</td>
                <td>${coordEsc(band.student_count)} aluno(s)</td></tr>`).join('')}
          </tbody>
        </table>`;
      document.getElementById('sim-classes-table').innerHTML = school.classes.length ? `
        <table class="sim-table">
          <thead><tr><th>Turma</th><th>Alunos</th><th>Média</th><th></th></tr></thead>
          <tbody>${school.classes.map((group) => `
            <tr><td>${coordEsc(group.class_name)}</td>
                <td>${coordEsc(group.student_count)}</td>
                <td>${coordEsc(group.average_percentage)}%</td>
                <td>${group.class_id ? `<button class="btn btn-secondary sim-class-report-btn"
                       type="button" data-class-id="${coordEsc(group.class_id)}">Ver turma</button>` : ''}</td>
            </tr>`).join('')}
          </tbody>
        </table>` : '<p class="empty-text">Nenhuma turma com cartão digitado.</p>';
      document.getElementById('sim-classes-table')
        .querySelectorAll('.sim-class-report-btn').forEach((button) => {
          button.addEventListener('click', () => loadSimuladoClassroomReport(button.dataset.classId));
        });
      document.getElementById('sim-units-table').innerHTML = `
        <table class="sim-table">
          <thead><tr><th>Unidade</th><th>Alunos</th><th>Média</th></tr></thead>
          <tbody>${school.units.map((group) => `
            <tr><td>${coordEsc(group.unit_name)}</td>
                <td>${coordEsc(group.student_count)}</td>
                <td>${coordEsc(group.average_percentage)}%</td></tr>`).join('')}
          </tbody>
        </table>`;
    }

    const itemsRes = await fetch(
      `/api/v1/coordination/simulados/${examId}/report/items`, { headers: simHeaders() });
    const itemsContainer = document.getElementById('sim-items-table');
    if (!itemsRes.ok) {
      itemsContainer.innerHTML = `<p class="empty-text">${coordEsc(await simDetailOf(itemsRes))}</p>`;
      return;
    }
    const analysis = await itemsRes.json();
    itemsContainer.innerHTML = `
      <table class="sim-table">
        <thead><tr><th>#</th><th>Área</th><th>Correta</th><th>Acerto</th>
                   <th>Ponto-bisserial</th><th>Marcações</th></tr></thead>
        <tbody>${analysis.items.map((item) => {
          // null = discriminação NÃO MEDÍVEL (todo mundo acertou ou errou).
          // Mostrar 0 aqui faria um item degenerado parecer um item ruim
          // porém medido - a confusão que o spec §6.5 quer evitar.
          const rpb = item.point_biserial == null ? 'não medível' : item.point_biserial;
          const suspect = item.point_biserial != null && item.point_biserial < 0;
          const distribution = Object.keys(item.option_distribution)
            .map((key) => `${key}:${item.option_distribution[key]}`).join(' ');
          return `
            <tr class="${suspect ? 'sim-item-suspect' : ''}">
              <td>${coordEsc(item.position)}</td>
              <td>${coordEsc(item.area_code)}</td>
              <td>${coordEsc(item.correct_option)}</td>
              <td>${coordEsc(Math.round(item.p_value * 1000) / 10)}%</td>
              <td>${coordEsc(rpb)}${suspect ? ' ⚠️ confira o gabarito' : ''}</td>
              <td>${coordEsc(distribution)}</td>
            </tr>`;
        }).join('')}
        </tbody>
      </table>`;
  }

  async function loadSimuladoClassroomReport(classId) {
    const examId = state.simulado.exam.id;
    const res = await fetch(
      `/api/v1/coordination/simulados/${examId}/report/classroom?class_id=${encodeURIComponent(classId)}`,
      { headers: simHeaders() });
    if (!res.ok) {
      showAlert(await simDetailOf(res), 'danger');
      return;
    }
    const report = await res.json();
    showAlert(
      `Turma ${report.class_name}: ${report.student_count} aluno(s), média de ` +
      `${report.average_percentage}% de acerto bruto. Questões mais erradas: ` +
      `${report.most_missed.map((item) => `#${item.position}`).join(', ')}.`,
      'success');
  }

  document.getElementById('sim-report-refresh-btn')
    .addEventListener('click', () => loadSimuladoReports());
```

- [ ] **Step 5: Acrescentar o CSS**

Ao fim de `src/agente_ia_edu/web/coordination.css`:

```css
.sim-report-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; }
.sim-report-grid h4 { margin: 12px 0 8px; font-size: 14px; }
.sim-item-suspect { background: #fef2f2; }

@media (max-width: 768px) {
  .sim-report-grid { grid-template-columns: 1fr; }
}
```

- [ ] **Step 6: Rodar e ver passar**

Run: `node --test tests/test_simulados_frontend.js && node --check src/agente_ia_edu/web/coordination.js`
Expected: PASS — 21 testes, e `node --check` sem saída.

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/web/coordination.html src/agente_ia_edu/web/coordination.js \
        src/agente_ia_edu/web/coordination.css tests/test_simulados_frontend.js
git commit -m "feat(simulados): painel da escola e analise de itens na coordenacao

Fase 2 Task 11. Discriminacao nao medivel aparece como 'nao medivel', nunca
como 0; ponto-bisserial negativo e destacado com 'confira o gabarito', que
secao 6.5 aponta como a verificacao de maior retorno pratico.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: Portal do professor — painel da turma

**Files:**
- Modify: `src/agente_ia_edu/api/routes/simulados.py` (endpoint de turmas para o professor)
- Modify: `src/agente_ia_edu/web/teacher.html` (menu antes da linha 66; painel antes da linha 806)
- Modify: `src/agente_ia_edu/web/teacher.js` (`titleMap` linha ~112; `loadCurrentView` linha ~136; bloco antes de `// Initial Load`, linha 2381)
- Modify: `src/agente_ia_edu/web/teacher.css`
- Test: `tests/test_simulados_http.py`, `tests/test_simulados_frontend.js`

**Interfaces:**
- Consumes: `list_exam_classes` (Task 4), `_authorize_teacher_or_coordinator`, `_load_exam_for_school`, `ExamClassItem` (Tasks 8-9).
- Produces: `GET /api/v1/teacher/simulados/{exam_id}/classes`; `loadTeacherSimulados()` e os ids `tsim-exam-select`, `tsim-class-select`, `tsim-report`.

- [ ] **Step 1: Escrever os testes que falham**

Acrescente à classe `ReportRoutesTests` em `tests/test_simulados_http.py`:

```python
    def test_a_teacher_lists_the_classes_of_a_published_exam(self):
        exam_id = self._published_exam()
        self._as("prof-a", role="TEACHER", roles=("teacher",))
        response = self.client.get(f"/api/v1/teacher/simulados/{exam_id}/classes")
        self.assertEqual(response.status_code, 200, response.text)
        self.assertEqual([(c["class_name"], c["typed_count"]) for c in response.json()],
                         [("3A", 2)])

    def test_a_student_cannot_list_the_classes_of_an_exam(self):
        exam_id = self._published_exam()
        self._as("student-alice", role="STUDENT", roles=("student",))
        response = self.client.get(f"/api/v1/teacher/simulados/{exam_id}/classes")
        self.assertEqual(response.status_code, 403, response.text)
```

E acrescente a `tests/test_simulados_frontend.js`:

```javascript
// ------------------------------------------------------- professor --

const teacherHtml = read('teacher.html');
const teacherJs = read('teacher.js');
const teacherCss = read('teacher.css');
const TEACHER_BLOCK = teacherJs.slice(teacherJs.indexOf('SIMULADOS (Fase 2'));

test('the teacher portal gains a "Simulados" nav entry, a panel and the loader wiring', () => {
  assert.match(teacherHtml, /<button class="nav-item" data-view="simulados">/);
  assert.match(teacherHtml, /id="view-simulados" class="view-panel"/);
  assert.match(teacherJs, /'simulados': \{ title: 'Simulados da Turma'/);
  assert.match(teacherJs, /if \(state\.currentView === 'simulados'\) loadTeacherSimulados\(\);/);
});

test('the teacher panel picks an exam and a class, then reads the classroom report', () => {
  for (const id of ['tsim-exam-select', 'tsim-class-select', 'tsim-report']) {
    assert.match(teacherHtml, new RegExp(`id="${id}"`), `faltou #${id}`);
  }
  assert.match(TEACHER_BLOCK, /fetch\('\/api\/v1\/teacher\/simulados'/);
  assert.match(TEACHER_BLOCK, /\/api\/v1\/teacher\/simulados\/\$\{examId\}\/classes/);
  assert.match(TEACHER_BLOCK, /\/api\/v1\/teacher\/simulados\/\$\{examId\}\/report\/classroom\?class_id=/);
});

test('the teacher panel reuses its own portal auth header and escaper', () => {
  assert.match(TEACHER_BLOCK, /'Authorization': `Bearer \$\{state\.teacherId\}`/);
  assert.match(TEACHER_BLOCK, /tmEsc\(/);
});

test('the teacher panel renders the API numbers and labels them as acerto bruto', () => {
  assert.match(TEACHER_BLOCK, /report\.average_percentage/);
  assert.match(TEACHER_BLOCK, /report\.most_missed/);
  assert.match(TEACHER_BLOCK, /report\.other_classes/);
  assert.match(TEACHER_BLOCK, /acerto bruto/i);
  assert.doesNotMatch(TEACHER_BLOCK, /theta|calibra|escala de refer/i);
});

test('CSS: the teacher simulado panel reflows under 768px', () => {
  assert.match(teacherCss, /\.tsim-grid/);
  assert.match(teacherCss, /@media \(max-width: 768px\)[\s\S]*\.tsim-grid/);
});
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_simulados_http.py -k classes -v && node --test tests/test_simulados_frontend.js`
Expected: FAIL — 404 na rota de turmas do professor, e o teste de frontend quebra no `data-view="simulados"` do teacher.html.

- [ ] **Step 3: Acrescentar o endpoint de turmas do professor**

Em `src/agente_ia_edu/api/routes/simulados.py`, depois de `list_teacher_simulados`:

```python
@simulados_teacher_router.get(
    "/{exam_id}/classes", response_model=list[ExamClassItem],
    summary="Turmas do ano letivo do simulado, para o professor escolher qual painel abrir",
)
async def list_teacher_simulado_classes(
    exam_id: UUID,
    ctx: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> list[ExamClassItem]:
    requester = _requester(ctx)
    async with session_factory() as session:
        try:
            _authorize_teacher_or_coordinator(requester)
            exam = await _load_exam_for_school(
                session, exam_id=exam_id, school_id=uuid.UUID(str(requester.school_id)))
            classes = await list_exam_classes(session, exam=exam)
        except Exception as exc:  # noqa: BLE001
            raise _map_simulado_error(exc) from exc
    return [
        ExamClassItem(class_id=entry.class_id, class_name=entry.class_name,
                      student_count=entry.student_count, typed_count=entry.typed_count)
        for entry in classes
    ]
```

- [ ] **Step 4: Acrescentar o markup do professor**

Em `src/agente_ia_edu/web/teacher.html`, antes da linha 66 (`<button class="nav-item" data-view="profile">`):

```html
        <button class="nav-item" data-view="simulados">
          <span class="icon">📄</span> Simulados
        </button>
```

E antes da linha 806 (`<section id="view-profile" class="view-panel">`):

```html
        <!-- SIMULADOS (Fase 2) — painel da turma, em nota de acerto bruto.
             Só simulados JÁ PUBLICADOS pela coordenação aparecem aqui. -->
        <section id="view-simulados" class="view-panel">
          <div class="card">
            <div class="card-header">
              <h3>📄 Resultado do simulado na turma</h3>
            </div>
            <div class="tsim-grid">
              <div class="form-group">
                <label for="tsim-exam-select">Simulado publicado</label>
                <select id="tsim-exam-select" class="select-input">
                  <option value="">Carregando simulados...</option>
                </select>
              </div>
              <div class="form-group">
                <label for="tsim-class-select">Turma</label>
                <select id="tsim-class-select" class="select-input">
                  <option value="">Selecione um simulado primeiro</option>
                </select>
              </div>
            </div>
            <div id="tsim-report">
              <p class="empty-text">Escolha um simulado e uma turma para ver o painel.</p>
            </div>
          </div>
        </section>
```

- [ ] **Step 5: Acrescentar o JS do professor**

Em `src/agente_ia_edu/web/teacher.js`, no `titleMap` (linha ~112), depois de `'reports'`:

```javascript
      'simulados': { title: 'Simulados da Turma', sub: 'Desempenho da turma no simulado, por acerto bruto' },
```

Em `loadCurrentView` (linha ~136), depois da linha de `'reports'`:

```javascript
    if (state.currentView === 'simulados') loadTeacherSimulados();
```

E imediatamente antes de `// Initial Load` (linha 2381):

```javascript
  // ===================================================================
  // SIMULADOS (Fase 2) — painel da turma.
  //
  // Somente leitura: quem cria simulado, cadastra gabarito e digita cartão
  // é a coordenação (spec §4 item 1). A nota aqui é ACERTO BRUTO.
  // ===================================================================

  function tsimHeaders() {
    return { 'Authorization': `Bearer ${state.teacherId}` };
  }

  async function tsimDetailOf(response) {
    try {
      const body = await response.json();
      return body && body.detail ? String(body.detail) : `Erro ${response.status}`;
    } catch (err) {
      return `Erro ${response.status}`;
    }
  }

  async function loadTeacherSimulados() {
    const select = document.getElementById('tsim-exam-select');
    const res = await fetch('/api/v1/teacher/simulados', { headers: tsimHeaders() });
    if (!res.ok) {
      select.innerHTML = '<option value="">Não foi possível carregar os simulados</option>';
      showAlert(await tsimDetailOf(res), 'danger');
      return;
    }
    const data = await res.json();
    select.innerHTML = data.length
      ? '<option value="">Selecione o simulado</option>' + data.map((exam) => `
          <option value="${tmEsc(exam.id)}">${tmEsc(exam.name)} (${tmEsc(exam.item_count)} questões)</option>`).join('')
      : '<option value="">Nenhum simulado publicado ainda</option>';
  }

  document.getElementById('tsim-exam-select').addEventListener('change', async (event) => {
    const examId = event.target.value;
    const classSelect = document.getElementById('tsim-class-select');
    document.getElementById('tsim-report').innerHTML =
      '<p class="empty-text">Escolha um simulado e uma turma para ver o painel.</p>';
    if (!examId) {
      classSelect.innerHTML = '<option value="">Selecione um simulado primeiro</option>';
      return;
    }
    const res = await fetch(`/api/v1/teacher/simulados/${examId}/classes`,
                            { headers: tsimHeaders() });
    if (!res.ok) {
      classSelect.innerHTML = '<option value="">Não foi possível carregar as turmas</option>';
      return;
    }
    const data = await res.json();
    classSelect.innerHTML = '<option value="">Selecione a turma</option>' + data.map((entry) => `
      <option value="${tmEsc(entry.class_id)}">${tmEsc(entry.class_name)}</option>`).join('');
  });

  document.getElementById('tsim-class-select').addEventListener('change', async (event) => {
    const classId = event.target.value;
    const examId = document.getElementById('tsim-exam-select').value;
    const container = document.getElementById('tsim-report');
    if (!classId || !examId) return;
    const res = await fetch(
      `/api/v1/teacher/simulados/${examId}/report/classroom?class_id=${encodeURIComponent(classId)}`,
      { headers: tsimHeaders() });
    if (!res.ok) {
      container.innerHTML = `<p class="empty-text">${tmEsc(await tsimDetailOf(res))}</p>`;
      return;
    }
    const report = await res.json();
    container.innerHTML = `
      <p><strong>${tmEsc(report.class_name)}</strong> — ${tmEsc(report.student_count)} aluno(s),
         média de <strong>${tmEsc(report.average_percentage)}%</strong> de acerto bruto.</p>
      <h4>Desempenho por área</h4>
      <table class="sim-table">
        <thead><tr><th>Área</th><th>Média de acertos</th><th>%</th></tr></thead>
        <tbody>${report.by_area.map((area) => `
          <tr><td>${tmEsc(area.label)}</td>
              <td>${tmEsc(area.average_correct)} de ${tmEsc(area.total_items)}</td>
              <td>${tmEsc(area.average_percentage)}%</td></tr>`).join('')}
        </tbody>
      </table>
      <h4>Distribuição das notas</h4>
      <table class="sim-table">
        <tbody>${report.distribution.map((band) => `
          <tr><td>${tmEsc(band.label)}</td><td>${tmEsc(band.student_count)} aluno(s)</td></tr>`).join('')}
        </tbody>
      </table>
      <h4>Questões mais erradas nesta turma</h4>
      <table class="sim-table">
        <thead><tr><th>#</th><th>Área</th><th>Correta</th><th>Acerto</th></tr></thead>
        <tbody>${report.most_missed.map((item) => `
          <tr><td>${tmEsc(item.position)}</td><td>${tmEsc(item.area_code)}</td>
              <td>${tmEsc(item.correct_option)}</td>
              <td>${tmEsc(Math.round(item.p_value * 1000) / 10)}%</td></tr>`).join('')}
        </tbody>
      </table>
      <h4>Comparação com as demais turmas</h4>
      <table class="sim-table">
        <thead><tr><th>Turma</th><th>Alunos</th><th>Média</th></tr></thead>
        <tbody>${report.other_classes.map((group) => `
          <tr><td>${tmEsc(group.class_name)}</td><td>${tmEsc(group.student_count)}</td>
              <td>${tmEsc(group.average_percentage)}%</td></tr>`).join('')}
        </tbody>
      </table>`;
  });
```

- [ ] **Step 6: Acrescentar o CSS do professor**

Ao fim de `src/agente_ia_edu/web/teacher.css`:

```css
/* SIMULADOS (Fase 2) — painel da turma */
.tsim-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 16px; }
.sim-table { width: 100%; border-collapse: collapse; font-size: 14px; margin-bottom: 16px; }
.sim-table th, .sim-table td { padding: 8px 10px; border-bottom: 1px solid #e2e8f0; text-align: left; }

@media (max-width: 768px) {
  .tsim-grid { grid-template-columns: 1fr; }
  .sim-table { display: block; overflow-x: auto; }
}
```

- [ ] **Step 7: Rodar e ver passar**

Run: `python -m pytest tests/test_simulados_http.py -v && node --test tests/test_simulados_frontend.js && node --check src/agente_ia_edu/web/teacher.js`
Expected: PASS — 31 testes de HTTP, 26 de frontend, e `node --check` sem saída.

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/api/routes/simulados.py src/agente_ia_edu/web/teacher.html \
        src/agente_ia_edu/web/teacher.js src/agente_ia_edu/web/teacher.css \
        tests/test_simulados_http.py tests/test_simulados_frontend.js
git commit -m "feat(simulados): painel da turma no portal do professor

Fase 2 Task 12. Somente leitura, e so de simulado ja publicado: quem cria,
cadastra gabarito e digita cartao e a coordenacao (spec secao 4). Inclui o
endpoint de turmas que a tela precisa para escolher o painel.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 13: Portal do aluno — boletim, e verificação final da fase

**Files:**
- Modify: `src/agente_ia_edu/web/index.html` (menu antes da linha 56; painel antes da linha 579)
- Modify: `src/agente_ia_edu/web/app.js` (`titleMap` e loaders em `switchView`, linhas 96-150; bloco novo antes do fechamento do `DOMContentLoaded`)
- Modify: `src/agente_ia_edu/web/styles.css`
- Test: `tests/test_simulados_frontend.js`

**Interfaces:**
- Consumes: `GET /api/v1/student/simulados` e `GET /api/v1/student/simulados/{exam_id}/report` (Task 9); `studentRequest` (`app.js:34`) e `escActivity` (`app.js:939`).
- Produces: `loadStudentSimulados()`, `window.openStudentBoletim(examId)` e os ids `ssim-list`, `ssim-report`.

- [ ] **Step 1: Escrever o teste que falha**

Acrescente a `tests/test_simulados_frontend.js`:

```javascript
// ----------------------------------------------------------- aluno --

const studentHtml = read('index.html');
const studentJs = read('app.js');
const studentCss = read('styles.css');
const STUDENT_BLOCK = studentJs.slice(studentJs.indexOf('SIMULADOS (Fase 2'));

test('the student portal gains a "Simulados" nav entry, a panel and the loader wiring', () => {
  assert.match(studentHtml, /<button class="nav-item" data-view="simulados">/);
  assert.match(studentHtml, /id="view-simulados" class="view-panel"/);
  assert.match(studentJs, /'simulados': \{ title: 'Meus Simulados'/);
  assert.match(studentJs, /if \(viewName === 'simulados'\) loadStudentSimulados\(\);/);
});

test('the student panel has the list and the boletim container', () => {
  assert.match(studentHtml, /id="ssim-list"/);
  assert.match(studentHtml, /id="ssim-report"/);
});

test('the student calls only its own routes, which never take a student_id', () => {
  assert.match(STUDENT_BLOCK, /studentRequest\('\/api\/v1\/student\/simulados'\)/);
  assert.match(STUDENT_BLOCK, /studentRequest\(`\/api\/v1\/student\/simulados\/\$\{examId\}\/report`\)/);
  assert.doesNotMatch(STUDENT_BLOCK, /student_id=/);
});

test('the boletim labels the score as acerto bruto and never promises an ENEM score', () => {
  assert.match(STUDENT_BLOCK, /acerto bruto/i);
  assert.doesNotMatch(STUDENT_BLOCK, /nota do ENEM|previs[aã]o de nota|theta|escala de refer/i);
});

test('the boletim renders the percentiles the API returned', () => {
  assert.match(STUDENT_BLOCK, /report\.school_percentile/);
  assert.match(STUDENT_BLOCK, /report\.class_percentile/);
});

test('an anchor item is shown without its correct option, with an honest explanation', () => {
  assert.match(STUDENT_BLOCK, /item\.is_anchor/);
  assert.match(STUDENT_BLOCK, /item\.correct_option == null/);
  assert.match(STUDENT_BLOCK, /não divulgada/i);
});

test('the boletim draws NO score-evolution chart: spec §7 suppresses it without equalização', () => {
  assert.doesNotMatch(STUDENT_BLOCK, /evolution|chart|grafico|gráfico/i);
});

test('CSS: the student boletim reflows under 768px', () => {
  assert.match(studentCss, /\.ssim-grid/);
  assert.match(studentCss, /@media \(max-width: 768px\)[\s\S]*\.ssim-grid/);
});
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `node --test tests/test_simulados_frontend.js`
Expected: FAIL — `index.html` não tem `data-view="simulados"`.

- [ ] **Step 3: Acrescentar o markup do aluno**

Em `src/agente_ia_edu/web/index.html`, antes da linha 56 (`<button class="nav-item" data-view="profile">`):

```html
        <button class="nav-item" data-view="simulados">
          <span class="icon">📄</span> Simulados
        </button>
```

E antes da linha 579 (`<section id="view-profile" class="view-panel">`):

```html
        <!-- SIMULADOS (Fase 2) — boletim do aluno em nota de acerto bruto.
             Sem gráfico de evolução: o spec §7 manda SUPRIMIR a evolução por
             nota absoluta enquanto não houver equalização por âncoras, e um
             gráfico estimado seria um erro silencioso. -->
        <section id="view-simulados" class="view-panel">
          <div class="card">
            <div class="card-header">
              <h3>📄 Simulados corrigidos</h3>
            </div>
            <div id="ssim-list">
              <p class="empty-text">Carregando seus simulados...</p>
            </div>
          </div>
          <div class="card" id="ssim-report-card" hidden>
            <div class="card-header">
              <h3>🧾 Boletim</h3>
              <button class="btn btn-secondary" type="button" id="ssim-close-btn">Fechar</button>
            </div>
            <div id="ssim-report"></div>
          </div>
        </section>
```

- [ ] **Step 4: Acrescentar o JS do aluno**

Em `src/agente_ia_edu/web/app.js`, no `titleMap` de `switchView` (linha ~117), depois de `'evolution'`:

```javascript
      'simulados': { title: 'Meus Simulados', sub: 'Seu desempenho nos simulados já corrigidos pela escola' },
```

Na lista de loaders de `switchView` (linha ~140), depois de `if (viewName === 'profile') loadProfileView();`:

```javascript
    if (viewName === 'simulados') loadStudentSimulados();
```

E, antes do fechamento do `DOMContentLoaded`, acrescente:

```javascript
  // ===================================================================
  // SIMULADOS (Fase 2) — boletim do aluno.
  //
  // A nota é ACERTO BRUTO e a tela diz isso em letra legível. Duas coisas
  // que esta tela NÃO faz, ambas de propósito:
  //
  // 1. Não desenha evolução ao longo do ano. Sem equalização por âncoras,
  //    dois simulados estão em réguas diferentes e o gráfico seria um erro
  //    silencioso (spec §7).
  // 2. Não mostra a alternativa correta de item-âncora. A API já devolve
  //    null nesses itens (spec §6.4); a tela explica o porquê em vez de
  //    deixar um espaço em branco sem justificativa.
  // ===================================================================

  async function loadStudentSimulados() {
    const container = document.getElementById('ssim-list');
    document.getElementById('ssim-report-card').hidden = true;
    try {
      const res = await studentRequest('/api/v1/student/simulados');
      if (res.status === 403) {
        container.innerHTML = '<p class="empty-text">Seus simulados aparecem aqui quando a escola publica o resultado.</p>';
        return;
      }
      if (!res.ok) throw new Error(`Erro ${res.status}`);
      const data = await res.json();
      container.innerHTML = data.length ? `
        <table class="ssim-table">
          <thead><tr><th>Simulado</th><th>Acertos</th><th>%</th><th></th></tr></thead>
          <tbody>${data.map((exam) => `
            <tr>
              <td>${escActivity(exam.exam_name)}</td>
              <td>${escActivity(exam.total_correct)} de ${escActivity(exam.total_items)}</td>
              <td>${escActivity(exam.total_percentage)}%</td>
              <td><button class="btn btn-secondary ssim-open-btn" type="button"
                          data-exam-id="${escActivity(exam.exam_id)}">Ver boletim</button></td>
            </tr>`).join('')}
          </tbody>
        </table>`
        : '<p class="empty-text">Nenhum simulado corrigido por enquanto.</p>';
      container.querySelectorAll('.ssim-open-btn').forEach((button) => {
        button.addEventListener('click', () => window.openStudentBoletim(button.dataset.examId));
      });
    } catch (err) {
      console.warn('Simulados do aluno:', err);
      container.innerHTML = '<p class="empty-text">Não foi possível carregar seus simulados agora.</p>';
    }
  }

  window.openStudentBoletim = async function (examId) {
    const card = document.getElementById('ssim-report-card');
    const container = document.getElementById('ssim-report');
    card.hidden = false;
    container.innerHTML = '<p class="empty-text">Carregando boletim...</p>';
    const res = await studentRequest(`/api/v1/student/simulados/${examId}/report`);
    if (!res.ok) {
      container.innerHTML = '<p class="empty-text">Não foi possível carregar este boletim.</p>';
      return;
    }
    const report = await res.json();
    const classLine = report.class_percentile == null
      ? ''
      : `<li>Percentil na sua turma: <strong>${escActivity(report.class_percentile)}</strong></li>`;
    container.innerHTML = `
      <p class="ssim-label">Nota por <strong>acerto bruto</strong>: contagem de questões certas.</p>
      <div class="ssim-grid">
        <div>
          <h4>${escActivity(report.exam_name)}</h4>
          <p><strong>${escActivity(report.total_correct)}</strong> de
             <strong>${escActivity(report.total_items)}</strong> questões
             (${escActivity(report.total_percentage)}%), com
             ${escActivity(report.total_answered)} respondida(s).</p>
          <ul>
            <li>Percentil na escola: <strong>${escActivity(report.school_percentile)}</strong></li>
            ${classLine}
          </ul>
          <h4>Por área</h4>
          <table class="ssim-table">
            <thead><tr><th>Área</th><th>Acertos</th><th>%</th></tr></thead>
            <tbody>${report.by_area.map((area) => `
              <tr><td>${escActivity(area.label)}</td>
                  <td>${escActivity(area.correct)} de ${escActivity(area.total_items)}</td>
                  <td>${escActivity(area.percentage)}%</td></tr>`).join('')}
            </tbody>
          </table>
        </div>
        <div>
          <h4>Questão a questão</h4>
          <table class="ssim-table">
            <thead><tr><th>#</th><th>Sua marcação</th><th>Correta</th><th></th></tr></thead>
            <tbody>${report.items.map((item) => `
              <tr>
                <td>${escActivity(item.position)}</td>
                <td>${item.chosen_option == null ? 'em branco' : escActivity(item.chosen_option)}</td>
                <td>${item.correct_option == null
                      ? '<span title="Questão reaproveitada entre simulados: o gabarito dela não é divulgado.">não divulgada</span>'
                      : escActivity(item.correct_option)}</td>
                <td>${item.is_correct ? '✅' : '❌'}</td>
              </tr>`).join('')}
            </tbody>
          </table>
        </div>
      </div>`;
  };

  document.getElementById('ssim-close-btn').addEventListener('click', () => {
    document.getElementById('ssim-report-card').hidden = true;
  });
```

- [ ] **Step 5: Acrescentar o CSS do aluno**

Ao fim de `src/agente_ia_edu/web/styles.css`:

```css
/* SIMULADOS (Fase 2) — boletim do aluno */
.ssim-grid { display: grid; grid-template-columns: repeat(2, minmax(0, 1fr)); gap: 24px; }
.ssim-grid h4 { margin: 12px 0 8px; font-size: 14px; }
.ssim-table { width: 100%; border-collapse: collapse; font-size: 14px; }
.ssim-table th, .ssim-table td { padding: 8px 10px; border-bottom: 1px solid #e2e8f0; text-align: left; }
.ssim-label { font-size: 13px; color: #475569; margin-bottom: 12px; }

@media (max-width: 768px) {
  .ssim-grid { grid-template-columns: 1fr; }
  .ssim-table { display: block; overflow-x: auto; }
}
```

- [ ] **Step 6: Rodar os testes de frontend**

Run: `node --test tests/test_simulados_frontend.js && node --check src/agente_ia_edu/web/app.js`
Expected: PASS — 34 testes, e `node --check` sem saída.

- [ ] **Step 7: Confirmar a fronteira do core (nenhuma visão computacional, nenhuma dependência nova)**

Run:
```bash
grep -rn "cv2\|OpenCV\|from PIL\|import PIL\|reportlab\|numpy\|scipy" \
  src/agente_ia_edu/services/simulado_*.py \
  src/agente_ia_edu/api/routes/simulados.py \
  src/agente_ia_edu/api/schemas/simulados.py
git diff --stat HEAD~12 -- pyproject.toml   # 12 commits ate aqui (Tasks 1-12)
```
Expected: o `grep` sai sem nenhuma linha (exit 1), e o `git diff --stat` do `pyproject.toml` sai vazio — a fase inteira não acrescentou uma dependência.

- [ ] **Step 8: Rodar a suíte COMPLETA**

Fase aditiva exige a suíte inteira, não só os arquivos novos: rotas registradas em `app.py` e modelos novos no `Base.metadata` mexem em testes que não são desta fase.

Run: `python -m pytest -q`
Expected: PASS, sem nenhuma regressão em relação ao estado anterior à fase. Se algo falhar, conserte antes de commitar — não registre "já estava quebrado" sem conferir com `git stash`.

- [ ] **Step 9: Rodar todos os testes de frontend**

Run: `node --test tests/test_*frontend*.js`
Expected: PASS — inclusive os que já existiam, que leem os mesmos arquivos `coordination.js`/`teacher.js`/`app.js` que esta fase alterou.

- [ ] **Step 10: Commit**

```bash
git add src/agente_ia_edu/web/index.html src/agente_ia_edu/web/app.js \
        src/agente_ia_edu/web/styles.css tests/test_simulados_frontend.js
git commit -m "feat(simulados): boletim do aluno no portal do aluno

Fase 2 Task 13. Nota rotulada como acerto bruto, percentis vindos da API,
questao a questao com a ancora exibida como 'nao divulgada' (secao 6.4) e
NENHUM grafico de evolucao, que secao 7 manda suprimir sem equalizacao.

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

## Fim da Fase 2

Ao final das 13 tarefas o simulado funciona de ponta a ponta sem uma linha de visão
computacional: a coordenação cria a prova, cadastra o gabarito com marcação de âncora, digita
os cartões, publica; professor e aluno leem os resultados; e a análise de itens já denuncia
gabarito cadastrado errado pelo ponto-bisserial negativo.

### O item de §7 que a própria §7 manda não implementar

§7 pede, no painel da escola, "comparação entre turmas **e unidades**, evolução por aplicação
ao longo do ano". A comparação entre turmas e entre unidades está implementada (Task 7).

A **evolução por aplicação não está, por exigência do próprio §7**, que a sujeita à mesma
regra do boletim do aluno: só é exibida quando existe equalização por âncoras. Duas aplicações
são duas provas de dificuldade diferente, e uma queda de 62% para 54% não distingue "a escola
piorou" de "a prova era mais difícil"; o argumento não perde força por a média ser de uma
escola em vez de um aluno — perde só a visibilidade, o que o torna mais perigoso.

Não há nada a fazer aqui além de não desenhar o gráfico. Ele passa a ser possível na fase 5,
quando as âncoras colocarem aplicações distintas na mesma régua.

O que **não** existe aqui, e onde entra: TRI, calibragem e escala de referência do ENEM na
fase 3; leitura óptica e fila de conferência na fase 4; equalização por âncoras e evolução no
ano na fase 5. Os ganchos que esta fase deixa prontos para elas são `is_anchor`/`anchor_key`
já cadastrados; a trava de publicação já escrita e testada contra os três status de scan que
bloqueiam — `PENDING`, `NEEDS_REVIEW` e `FAILED` —, hoje vacuamente verdadeira porque não há
scan algum; a procedência `source='MANUAL'` em cada resposta, contra a qual o `'OMR'` da fase 4
vai se distinguir; e `score_kind` em todo schema de nota, o campo que vai separar acerto bruto
de nota TRI sem quebrar nenhum consumidor.
