# Simulados Fase 4 — Worker de OMR Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Ler automaticamente os cartões-resposta digitalizados de um simulado, gravando
resposta por questão em `answer_card_marks`, e mandar para conferência humana — nunca para
um chute — toda bolha ambígua, dupla marcação, QR ilegível ou cartão não alinhado.

**Architecture:** Um pacote novo `src/agente_ia_edu/omr_worker/`, que é o **único** lugar do
repositório onde `cv2` pode ser importado, rodando como processo/container próprio
(`python -m agente_ia_edu.omr_worker`) instalado com o extra `omr` do `pyproject.toml`.
O core continua sem OpenCV, sem Pillow e sem NumPy, e um teste de AST prova isso a cada
execução da suíte. A comunicação core ↔ worker é a tabela `omr_jobs` no próprio Postgres,
consumida com `SELECT ... FOR UPDATE SKIP LOCKED` com lease e retry. O pipeline dos sete
passos do §5 do spec é decomposto em sete módulos pequenos e puros (só `align.py`,
`identify.py`, `crops.py` e `synthetic.py` tocam `cv2`; `raster.py` toca PyMuPDF;
`measure.py`, `threshold.py`, `classify.py` e `resolve.py` são NumPy/Python puro),
cada um com o seu próprio ciclo de teste. A fila de conferência humana é uma rota nova
(`api/routes/mock_exam_review.py`) mais uma tela nova no portal da coordenação, e a trava
de integridade do §4 é um service próprio (`services/mock_exam_scan_gate.py`).

**Tech Stack:** Python 3.13, OpenCV (headless) + NumPy + PyMuPDF **só no worker**,
SQLAlchemy 2.x async, Alembic, FastAPI, pytest, JS vanilla + `node --test`, Docker Compose.

**Spec:** [docs/superpowers/specs/2026-09-29-correcao-simulados-tri-design.md](../specs/2026-09-29-correcao-simulados-tri-design.md)
(fonte de verdade — especialmente §2.3, §2.5, §4, §5, §8.2 e §8.3)

## Global Constraints

- Python `>=3.13,<3.14`; SQLAlchemy 2.x async; pytest com `pythonpath = ["src", "."]`
  (`pyproject.toml:46-60`). Nenhuma dessas âncoras muda nesta fase.
- **`cv2`, `PIL`/`Pillow`, `skimage` e `numpy` só existem dentro de
  `src/agente_ia_edu/omr_worker/`.** Nenhum outro módulo do pacote pode importá-los, nem
  direta nem transitivamente. `agente_ia_edu.simulado_card.template`/`layout`/`markers`
  são stdlib+PyYAML e ficam do lado de cá da fronteira — o worker os importa livremente.
  O `renderer.py` do gerador (ReportLab → Pillow) roda no ambiente isolado e **não** é
  importado por nada deste plano. O `pyproject.toml` já proíbe Pillow por escrito
  (`pyproject.toml:28-35`: "Do NOT add pdfplumber here: it pulls in Pillow, which changes
  pypdf's `page.images` behaviour and breaks the parser"). A Task 1 transforma isso em teste.
- As dependências do worker entram como **extra opcional** no mesmo `pyproject.toml`,
  seguindo exatamente o padrão do extra `recovery` já existente
  (`pyproject.toml:26-38`): bloco comentado explicando por que existe, floor e teto de
  versão em cada pacote. O extra `omr` **nunca** é instalado no ambiente do core.
- `pymupdf>=1.24,<2.0` já é o extra `recovery` e já está instalado no `.venv`
  (pymupdf 1.28.2), e o core **já** importa `fitz` em
  `src/agente_ia_edu/api/routes/question_extraction.py:22`. PyMuPDF portanto **não** é
  proibido no core; OpenCV, Pillow e NumPy são.
- Imagens (originais, cartão alinhado e recortes de bolha) vão para
  `src/agente_ia_edu/services/material_storage.py` (`MaterialStorage`), o storage local
  endereçado por conteúdo que já existe. **Não criar storage novo**, não criar abstração de
  S3/GCS, não mover/renomear arquivo de origem (`MaterialStorage.store` usa `shutil.copy2`
  e nunca toca a origem — `material_storage.py:41-50`).
- Container: o projeto tem `docker-compose.yml` na raiz e um diretório `docker/<serviço>/`
  por serviço (hoje só `docker/postgres/init/01-init.sh`). Não existe nenhum `Dockerfile` no
  repositório ainda — o do worker é o primeiro. O serviço novo entra sob
  `profiles: ["omr"]` para que `docker compose up` sem perfil continue subindo exatamente
  o que sobe hoje (postgres + n8n).
- Banco de teste: Postgres descartável na porta 5433 (`docker-compose.yml` mapeia
  `"5433:5432"`), criado/destruído com `tests/_postgres_test_db.py`
  (`create_database`/`drop_database`). Tudo que depende de `FOR UPDATE SKIP LOCKED` é
  teste Postgres E2E — SQLite não implementa isso.
- Antes de rodar a suíte completa: `ps aux | grep pytest` e esperar qualquer execução
  concorrente terminar (o Postgres da 5433 é compartilhado entre as sessões).
- **Constante ou tipo que já existe numa fase anterior é IMPORTADO, nunca redeclarado.**
  Quem entra antes é o dono. Duas declarações idênticas do mesmo contrato não quebram nada
  hoje e divergem em silêncio depois — as duas continuam verdes enquanto protegem coisas
  diferentes. É a mesma razão pela qual o §2.1 deu dono único ao `CardTemplate`.
  Vindos de fora desta fase:
  - `BLOCKING_SCAN_STATUSES`, `STATUS_CALIBRATED`, `STATUS_PUBLISHED`, `VALID_OPTIONS` →
    `services/simulado_service.py` (Fase 2);
  - `CardTemplate`, `BubbleBox`, `MarkerPlacement`, `QrPlacement`, `TemplateError`,
    `LAYOUT_VERSION`, `CardLayoutSpec`, `build_template`, `CORNERS`, `load_marker_set` →
    `simulado_card/` (Fase 1);
  - `MARK_RESOLUTIONS`, `SCAN_STATUSES`, `OMR_JOB_STATUSES`, `DEFAULT_BOOKLET_CODE` →
    `db/models/answer_card.py` (Fase 1).
  Antes de declarar qualquer constante nova, rode
  `grep -rn "NOME_DA_CONSTANTE" src/ docs/superpowers/plans/2026-09-29-simulados-fase*.md`.
- TDD obrigatório: RED (teste que falha, rodado e visto falhar) antes de qualquer linha de
  código de produção.
- Nenhum `git push`. Os `git commit` deste plano são locais, na branch de trabalho.
- **O leitor nunca chuta** (§5 do spec). Toda incerteza vira item de conferência humana ou
  scan `FAILED` com `failure_reason` legível em português. Nenhuma resposta adivinhada,
  em nenhum caminho de código, em nenhum fallback.
- Fora de escopo nesta fase: motor de TRI, calibragem, escala de reporte, relatórios de
  nota, âncoras/equalização (§9 do spec, fases 3 e 5).

### Premissas da Fase 1 (consumidas como dado, nunca reimplementadas)

O plano assume que a Fase 1 já entregou e commitou:

- Modelos e migrations de §3, exportados por `src/agente_ia_edu/db/models/__init__.py`:
  `MockExam`, `MockExamArea`, `MockExamItem`, `MockExamResponse` em
  `src/agente_ia_edu/db/models/mock_exam.py`; `AnswerCard`, `AnswerCardScan`,
  `AnswerCardMark`, `OmrJob` em `src/agente_ia_edu/db/models/answer_card.py`.
- **Isolamento multi-tenant:** toda tabela do subsistema carrega `school_id` obrigatório e
  as FKs entre elas são compostas `(school_id, parent_id)`. As consultas deste plano filtram
  por `mock_exam_id`/`scan_id`, que já estão presos a uma escola pela FK composta — nenhuma
  delas precisa de cláusula extra, mas nenhuma pode ser reescrita para cruzar simulados.
- `answer_card_scans` com `status` em `PENDING | PROCESSED | NEEDS_REVIEW | FAILED`,
  `image_hash`, `storage_path`, `source`, `reader_version`, `processed_at`,
  `failure_reason`, `answer_card_id` (nullable), `mock_exam_id`.
- `omr_jobs` com `id`, `scan_id`, `status`, `attempts`, `last_error`, `locked_at`,
  `locked_by`, e `status` em `PENDING | RUNNING | DONE | FAILED`.
- `answer_cards` com `qr_token` (opaco, único) e `template_version`.
- **A geometria do cartão é pura e importável** (§2.1 revisado):
  `src/agente_ia_edu/simulado_card/template.py`, `layout.py` e `markers.py` são stdlib +
  PyYAML, sem ReportLab e sem cv2. Só `renderer.py` toca ReportLab, e ele roda **no mesmo
  ambiente isolado do worker** — ReportLab declara `pillow>=9.0.0` obrigatório, e Pillow é
  o que o core proíbe.
  - `CardTemplate`, `BubbleBox`, `MarkerPlacement`, `QrPlacement`, `TemplateError`,
    `LAYOUT_VERSION` em `simulado_card/template.py`.
  - `CardLayoutSpec`, `build_template(spec)`, `DEFAULT_LAYOUT` em `simulado_card/layout.py`.
  - `CORNERS`, `MarkerSet`, `load_marker_set()` em `simulado_card/markers.py`.
  - Coordenadas normalizadas pelo retângulo entre os **centros dos marcadores ArUco**:
    `(0,0)` = centro do superior-esquerdo, `(1,1)` = centro do inferior-direito, `y` cresce
    para baixo, `radius`/`size` normalizados pela **largura**.
- **O tipo do template é declarado UMA vez e o leitor o importa** (§2.1). A Task 2 deste
  plano não redeclara nada: expõe uma *vista em pixels* (`ReaderTemplate`) sobre o
  `CardTemplate` da Fase 1. Duas definições do mesmo contrato divergiriam em silêncio, e o
  sintoma não seria teste vermelho — seria o leitor medindo na coordenada errada.
- O gerador grava o template via `MaterialStorage` como
  `template_<template_version>.json` (`json.dumps(card.to_dict())`).
- **O template carrega a razão de aspecto** (§2.1): `CardTemplate.marker_rect_aspect: float`,
  a razão altura/largura do retângulo entre os centros dos marcadores. Sem ela o leitor não
  tem como escolher um retângulo canônico com a proporção do impresso, e a homografia entrega
  cartão esticado. O spec é explícito sobre a reação: "o leitor deve **recusar** um template
  que não a traga em vez de supor um valor" — é o que a Task 2 faz.

A primeira ação de quem executar este plano é conferir essas premissas:

```bash
grep -rn "class AnswerCardMark\|class AnswerCardScan\|class OmrJob\|class AnswerCard\b" src/agente_ia_edu/db/models/
grep -rn "def to_dict" -A 25 src/agente_ia_edu/simulado_card/template.py
```

Se algum nome divergir, ajustar os imports das Tasks 13, 16 e 17 para o nome real **antes**
de escrever código — nenhuma outra parte do plano depende deles.

---

## File Structure

**Pacote do worker** (`cv2` permitido, e só aqui):

| Arquivo | Responsabilidade |
|---|---|
| `src/agente_ia_edu/omr_worker/__init__.py` | Docstring da fronteira. Sem import pesado. |
| `src/agente_ia_edu/omr_worker/config.py` | `OmrConfig`: DPI, limiares, lease, retry. Sem cv2/numpy. |
| `src/agente_ia_edu/omr_worker/template.py` | `ReaderTemplate`: **importa** o `CardTemplate` da Fase 1 e o põe em pixels. Puro. |
| `src/agente_ia_edu/omr_worker/raster.py` | §5.1 rasterizar PDF/imagem → cinza. PyMuPDF + NumPy. |
| `src/agente_ia_edu/omr_worker/align.py` | §5.2 ArUco + homografia. **cv2**. |
| `src/agente_ia_edu/omr_worker/identify.py` | §5.3 QR. **cv2**. |
| `src/agente_ia_edu/omr_worker/measure.py` | §5.4 intensidade normalizada por fundo local. NumPy. |
| `src/agente_ia_edu/omr_worker/threshold.py` | §5.5 Otsu por cartão. NumPy. |
| `src/agente_ia_edu/omr_worker/classify.py` | §5.6 preenchida/vazia/ambígua. Python puro. |
| `src/agente_ia_edu/omr_worker/resolve.py` | §5.7 resolução por questão. Python puro. |
| `src/agente_ia_edu/omr_worker/pipeline.py` | Orquestra §5.1→§5.7, devolve `ScanReading`. |
| `src/agente_ia_edu/omr_worker/crops.py` | Recortes de bolha + cartão alinhado no `MaterialStorage`. **cv2**. |
| `src/agente_ia_edu/omr_worker/queue.py` | §2.5 consumidor `FOR UPDATE SKIP LOCKED`. SQLAlchemy async. |
| `src/agente_ia_edu/omr_worker/persist.py` | Grava marks + status do scan. SQLAlchemy async. |
| `src/agente_ia_edu/omr_worker/synthetic.py` | §8.2 cartão sintético + degradações. **cv2**. |
| `src/agente_ia_edu/omr_worker/runner.py` | Laco: reivindica job, le, grava, repete. |
| `src/agente_ia_edu/omr_worker/__main__.py` | Entrypoint do processo (`python -m ...`). |

**Core** (sem cv2, sem numpy):

| Arquivo | Responsabilidade |
|---|---|
| `src/agente_ia_edu/services/omr_artifacts.py` | Caminho dos artefatos derivados no `MaterialStorage` (pathlib puro). |
| `src/agente_ia_edu/services/omr_consolidation.py` | Marks resolvidas → `mock_exam_responses` com `source='OMR'`. |
| `src/agente_ia_edu/services/mock_exam_scan_gate.py` | §4 trava de integridade antes de `CALIBRATED`. |
| `src/agente_ia_edu/services/omr_review.py` | Fila de conferência: listar e resolver marks. |
| `src/agente_ia_edu/api/schemas/mock_exam_review.py` | Pydantic da fila de conferência. |
| `src/agente_ia_edu/api/routes/mock_exam_review.py` | Rotas da fila + PNG do recorte. |
| `docker/omr_worker/Dockerfile` | Imagem do worker (core + extra `omr`). |
| `docker-compose.yml` | Serviço `omr_worker` sob `profiles: ["omr"]`. |
| `scripts/omr_physical_acceptance.py` | §8.3 comparação leitura automática × digitação. |

**Frontend:** `web/coordination.html`, `web/coordination.js`, `web/coordination.css`.

**Testes** (todos em `tests/`, um por task):

| Arquivo | Task | Precisa de Postgres 5433 |
|---|---|---|
| `test_omr_import_boundary.py` | 1 | nao |
| `test_omr_template.py` | 2 | nao |
| `test_omr_raster.py` | 3 | nao |
| `test_omr_align.py` | 4 | nao |
| `test_omr_identify.py` | 5 | nao |
| `test_omr_measure.py` | 6 | nao |
| `test_omr_threshold.py` | 7 | nao |
| `test_omr_classify_resolve.py` | 8 | nao |
| `test_omr_synthetic.py` | 9 | nao |
| `test_omr_pipeline_synthetic.py` | 10 | nao |
| `test_omr_crops.py` | 11 | nao |
| `test_omr_queue_postgresql.py` | 12 | **sim** |
| `test_omr_mark_resolution_pending.py` | 13 (prova de premissa) | **sim** |
| `test_omr_persist_postgresql.py` | 14 | **sim** |
| `test_omr_worker_loop_postgresql.py` | 15 | **sim** |
| `test_omr_packaging.py` | 16 | nao |
| `test_mock_exam_scan_gate.py` | 17 | nao (SQLite em memoria) |
| `test_mock_exam_review_http.py` | 18 | nao (SQLite em memoria) |
| `test_omr_review_frontend.js` | 19 | nao (`node --test`) |
| `test_omr_physical_acceptance_script.py` | 20 | nao |

**Documento de aceite:** `docs/superpowers/acceptance/2026-09-29-omr-aceite-fisico.md` (Task 20).

As Tasks 2–11 importam `cv2`/`numpy` nos testes, entao rodam onde o extra `omr` estiver
instalado (a maquina de desenvolvimento, apos o Step 5 da Task 1, e a imagem do worker).
Os testes do core — 1, 16, 17, 18, 19, 20 — nao importam nada disso.

### Por que os módulos são tão pequenos

Os quatro passos que dominam a qualidade da leitura — medir, limiarizar, classificar,
resolver — são NumPy/Python puro, sem OpenCV e sem imagem. Isso é deliberado: eles podem
ser testados com números inventados à mão, determinísticos, sem renderizar nada. Só
`align`, `identify`, `crops` e `synthetic` precisam de OpenCV de verdade.

---

## Ordem de execução

Sequencial, com duas janelas de paralelismo:

- A **Task 1** vem primeiro e sozinha: ela cria o pacote e instala o extra `omr`, sem o
  qual nenhuma das seguintes roda.
- As **Tasks 2–9** são independentes entre si (arquivos disjuntos, nenhuma importa a
  outra) e podem rodar em paralelo.
- A **Task 10** costura 2–9 e precisa de todas elas verdes.
- As **Tasks 11–15** são sequenciais: 12 (fila) → 13 (prova de que `resolution=PENDING`
  existe) → 14 (persistência + consolidação, usa 11 e 13) → 15 (laço, usa 12 e 14).
  A 11 pode sair junto com a 10, e a 13 roda em segundos.
- A **Task 16** (empacotamento) depende da 15; a **17** só depende dos modelos da Fase 1
  e pode rodar em paralelo com qualquer coisa a partir da 13; a **18** depende da 13 e da
  17; a **19** depende da 18.
- A **Task 20** é o portão de fase e só começa depois de tudo — e leva 5 dias úteis de
  calendário que não comprimem.

**Dependência da Fase 2:** as Tasks 17 e 18 importam constantes de
`services/simulado_service.py` e `services/simulado_responses.py`. A Fase 2 entra antes desta
na ordem do §9 do spec, então isso não cria espera — mas se por algum motivo a Fase 4 for
executada primeiro, **pare e traga a Fase 2**: recriar as constantes aqui para destravar é
exatamente a duplicação que a Global Constraint acima proíbe.

---

### Task 1: Fronteira de dependências — extra `omr`, esqueleto do worker e o teste que prova a fronteira

**Files:**
- Modify: `pyproject.toml:36-38` (acrescentar o extra `omr` logo depois do bloco `recovery`)
- Create: `src/agente_ia_edu/omr_worker/__init__.py`
- Create: `src/agente_ia_edu/omr_worker/config.py`
- Test: `tests/test_omr_import_boundary.py`

**Interfaces:**
- Consumes: nada.
- Produces: `agente_ia_edu.omr_worker.config.OmrConfig` (dataclass frozen, com
  `from_env(environ)`), usado por todas as tasks seguintes.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_omr_import_boundary.py`:

```python
"""Fase 4 (OMR) - a fronteira do §2.3 do spec vira teste, nao promessa.

O `pyproject.toml` proibe Pillow por escrito (linhas 33-36: pdfplumber "pulls in
Pillow, which changes pypdf's page.images behaviour and breaks the parser"), e o
§2.3 estende a proibicao a OpenCV. Sem um teste, essa fronteira dura ate o dia em
que alguem escrever `import cv2` num service "so pra um recorte rapido".

Dois niveis:
  1. Estatico (AST): nenhum modulo de `src/agente_ia_edu/` FORA de `omr_worker/`
     importa cv2/PIL/numpy/skimage.
  2. Runtime (subprocesso limpo): importar e construir o app FastAPI real nao traz
     cv2 nem numpy para `sys.modules`. Subprocesso porque a propria suite importa
     cv2 nos testes de OMR - dentro do processo do pytest o teste seria inutil.
"""

from __future__ import annotations

import ast
import subprocess
import sys
import unittest
from pathlib import Path

PACKAGE_ROOT = Path(__file__).resolve().parents[1] / "src" / "agente_ia_edu"
WORKER_ROOT = PACKAGE_ROOT / "omr_worker"

FORBIDDEN_IN_CORE = {"cv2", "PIL", "Pillow", "numpy", "skimage", "pyzbar", "pytesseract"}


def _imported_roots(path: Path) -> set[str]:
    tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    roots: set[str] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                roots.add(alias.name.split(".")[0])
        elif isinstance(node, ast.ImportFrom):
            if node.level == 0 and node.module:
                roots.add(node.module.split(".")[0])
    return roots


class OmrImportBoundary(unittest.TestCase):
    def test_no_core_module_imports_opencv_pillow_or_numpy(self):
        offenders: list[str] = []
        for path in sorted(PACKAGE_ROOT.rglob("*.py")):
            if WORKER_ROOT in path.parents or path == WORKER_ROOT:
                continue
            forbidden = _imported_roots(path) & FORBIDDEN_IN_CORE
            if forbidden:
                offenders.append(f"{path.relative_to(PACKAGE_ROOT.parents[1])}: {sorted(forbidden)}")
        self.assertEqual(
            offenders, [],
            "modulos do core importando dependencia exclusiva do omr_worker (§2.3 do spec):\n"
            + "\n".join(offenders),
        )

    def test_the_worker_package_is_the_only_place_allowed_to(self):
        # A regra so tem valor se o lado de dentro existir de fato.
        self.assertTrue(WORKER_ROOT.is_dir(), "src/agente_ia_edu/omr_worker/ deve existir")

    def test_building_the_real_app_never_loads_cv2_or_numpy(self):
        code = (
            "import sys\n"
            "from agente_ia_edu.api.app import create_app\n"
            "create_app()\n"
            "leaked = sorted({'cv2', 'numpy', 'PIL'} & set(sys.modules))\n"
            "print(','.join(leaked))\n"
        )
        result = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True,
            cwd=str(PACKAGE_ROOT.parents[1]),
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(result.stdout.strip(), "", f"create_app() carregou: {result.stdout.strip()}")


class OmrConfigDefaults(unittest.TestCase):
    def test_config_defaults_match_the_spec(self):
        from agente_ia_edu.omr_worker.config import OmrConfig

        config = OmrConfig()
        self.assertEqual(config.raster_dpi, 200)          # §5.1: "~200 DPI"
        self.assertEqual(config.max_attempts, 3)
        self.assertEqual(config.lease_seconds, 300)
        self.assertGreater(config.ambiguity_margin, 0.0)  # §5.6 precisa de uma zona ambigua

    def test_config_reads_the_environment_the_container_provides(self):
        from agente_ia_edu.omr_worker.config import OmrConfig

        config = OmrConfig.from_env({
            "OMR_WORKER_ID": "omr-worker-7",
            "OMR_RASTER_DPI": "300",
            "OMR_POLL_INTERVAL_SECONDS": "0.5",
        })
        self.assertEqual(config.worker_id, "omr-worker-7")
        self.assertEqual(config.raster_dpi, 300)
        self.assertEqual(config.poll_interval_seconds, 0.5)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_omr_import_boundary.py -v`
Expected: FAIL — `OmrImportBoundary.test_the_worker_package_is_the_only_place_allowed_to`
com "src/agente_ia_edu/omr_worker/ deve existir", e os dois testes de `OmrConfigDefaults`
com `ModuleNotFoundError: No module named 'agente_ia_edu.omr_worker'`.

- [ ] **Step 3: Criar o pacote e o `OmrConfig`**

`src/agente_ia_edu/omr_worker/__init__.py`:

```python
"""Worker de leitura optica de cartao-resposta (Fase 4 de
docs/superpowers/specs/2026-09-29-correcao-simulados-tri-design.md).

ESTE PACOTE E O UNICO LUGAR DO REPOSITORIO ONDE `cv2` PODE SER IMPORTADO.

Motivo concreto (§2.3 do spec): o `pyproject.toml` do core proibe Pillow porque
ele altera o comportamento de `page.images` do pypdf e quebra o parser de
ingestao de questoes (mesma razao pela qual pdfplumber esta banido do extra
`recovery`). Isolar a visao computacional num processo proprio, com o seu
proprio conjunto de dependencias (extra `omr`), preserva essa garantia sem
abrir mao de OpenCV.

`tests/test_omr_import_boundary.py` transforma essa regra em teste: varre a AST
de todo modulo de `src/agente_ia_edu/` fora deste pacote e reprova qualquer
import de cv2/PIL/numpy/skimage.

Nada aqui e importado pelo core. O acoplamento e feito exclusivamente pela
tabela `omr_jobs` (§2.5), nunca por import.

Este modulo e deliberadamente vazio de imports pesados: importa-lo nao pode
custar o carregamento de OpenCV.
"""

__all__: list[str] = []
```

`src/agente_ia_edu/omr_worker/config.py`:

```python
"""Parametros do worker de OMR. Sem cv2, sem numpy - so dataclass e env.

Todo numero que governa uma decisao de leitura mora AQUI, com a justificativa
ao lado. Nenhum limiar magico espalhado pelos modulos do pipeline: quando o
aceite fisico (§8.3) pedir ajuste, o ajuste acontece num arquivo so.
"""

from __future__ import annotations

import os
from collections.abc import Mapping
from dataclasses import dataclass


def _as_int(source: Mapping[str, str], key: str, default: int) -> int:
    raw = source.get(key)
    return default if raw is None or raw.strip() == "" else int(raw)


def _as_float(source: Mapping[str, str], key: str, default: float) -> float:
    raw = source.get(key)
    return default if raw is None or raw.strip() == "" else float(raw)


@dataclass(frozen=True)
class OmrConfig:
    # --- identidade e fila (§2.5) ---
    worker_id: str = "omr-worker-local"
    poll_interval_seconds: float = 2.0
    #: Um job RUNNING com `locked_at` mais velho que isto e considerado orfao
    #: (worker morreu no meio) e volta para PENDING. 300s e ~10x o tempo de um
    #: cartao de 90 questoes a 200 DPI, com folga para um lote grande.
    lease_seconds: int = 300
    #: Tentativas totais por scan. Na 3a falha o job vai para FAILED e o scan
    #: tambem, com motivo legivel - nunca retry infinito silencioso.
    max_attempts: int = 3

    # --- §5.1 rasterizacao ---
    raster_dpi: int = 200

    # --- §5.4 medicao ---
    #: Raio do disco interno medido, como fracao do raio da bolha do template.
    #: 0.62 fica dentro do anel impresso: o anel e tinta preta em TODA bolha e
    #: contaminaria a medida igualmente em vazia e preenchida.
    inner_disc_ratio: float = 0.62
    #: Anel de fundo local imediato (§5.4), em fracoes do raio da bolha.
    background_inner_ratio: float = 1.25
    background_outer_ratio: float = 1.90

    # --- §5.5/§5.6 limiarizacao e classificacao ---
    #: Meia-largura da zona ambigua em volta do limiar de Otsu do cartao.
    ambiguity_margin: float = 0.08
    #: Separacao minima entre as duas classes do Otsu para o limiar ser confiavel.
    #: Abaixo disso o cartao e "degenerado" (ex.: aluno nao marcou nada, e o Otsu
    #: estaria separando puro ruido) e o par de limiares absolutos abaixo assume.
    min_class_separation: float = 0.12
    degenerate_fill_threshold: float = 0.45
    degenerate_empty_threshold: float = 0.20

    # --- versao gravada em answer_card_scans.reader_version ---
    reader_version: str = "omr-1.0.0"

    @classmethod
    def from_env(cls, environ: Mapping[str, str] | None = None) -> "OmrConfig":
        source = os.environ if environ is None else environ
        defaults = cls()
        return cls(
            worker_id=source.get("OMR_WORKER_ID") or defaults.worker_id,
            poll_interval_seconds=_as_float(source, "OMR_POLL_INTERVAL_SECONDS", defaults.poll_interval_seconds),
            lease_seconds=_as_int(source, "OMR_LEASE_SECONDS", defaults.lease_seconds),
            max_attempts=_as_int(source, "OMR_MAX_ATTEMPTS", defaults.max_attempts),
            raster_dpi=_as_int(source, "OMR_RASTER_DPI", defaults.raster_dpi),
            inner_disc_ratio=_as_float(source, "OMR_INNER_DISC_RATIO", defaults.inner_disc_ratio),
            background_inner_ratio=_as_float(source, "OMR_BACKGROUND_INNER_RATIO", defaults.background_inner_ratio),
            background_outer_ratio=_as_float(source, "OMR_BACKGROUND_OUTER_RATIO", defaults.background_outer_ratio),
            ambiguity_margin=_as_float(source, "OMR_AMBIGUITY_MARGIN", defaults.ambiguity_margin),
            min_class_separation=_as_float(source, "OMR_MIN_CLASS_SEPARATION", defaults.min_class_separation),
            degenerate_fill_threshold=_as_float(source, "OMR_DEGENERATE_FILL_THRESHOLD", defaults.degenerate_fill_threshold),
            degenerate_empty_threshold=_as_float(source, "OMR_DEGENERATE_EMPTY_THRESHOLD", defaults.degenerate_empty_threshold),
            reader_version=source.get("OMR_READER_VERSION") or defaults.reader_version,
        )
```

- [ ] **Step 4: Acrescentar o extra `omr` ao `pyproject.toml`**

Logo depois do bloco `recovery` (que termina em `pyproject.toml:38`), antes de
`[tool.setuptools.packages.find]`:

```toml
# OMR worker (Fase 4 de docs/superpowers/specs/2026-09-29-correcao-simulados-tri-design.md).
# NUNCA instalado no ambiente do core - so na imagem docker/omr_worker/Dockerfile.
# O §2.3 do spec isola OpenCV num processo proprio pela mesma razao pela qual o
# bloco `recovery` acima proibe pdfplumber: o ambiente de visao computacional
# convive com Pillow, e Pillow altera `page.images` do pypdf e quebra o parser de
# ingestao. Aqui as duas arvores de dependencia nunca se encontram.
# `opencv-contrib-python-headless`: CONTRIB porque `cv2.aruco` e
# `cv2.QRCodeEncoder` so existem la, HEADLESS porque o worker nao tem tela.
# tests/test_omr_import_boundary.py prova, a cada suite, que nenhum modulo do
# core importa nada deste extra.
omr = [
	# CONTRIB, e nao o `opencv-python-headless` simples: o modulo `cv2.aruco`
	# (§5.2) e o `cv2.QRCodeEncoder` usado pelo cartao sintetico (§8.2) vivem no
	# pacote contrib. Com a variante base, `cv2.aruco` nao existe e o alinhamento
	# inteiro nao carrega.
	# HEADLESS porque o worker nao tem tela: dispensa as libs de GUI na imagem.
	"opencv-contrib-python-headless>=4.10,<5.0",
	"numpy>=2.0,<3.0",
	"pymupdf>=1.24,<2.0",
]
```

- [ ] **Step 5: Instalar o extra no ambiente local de desenvolvimento**

Run: `.venv/bin/pip install "opencv-contrib-python-headless>=4.10,<5.0" "numpy>=2.0,<3.0"`
Expected: instalação concluída. Conferir que Pillow **não** entrou junto:
`.venv/bin/pip show Pillow` → `WARNING: Package(s) not found: Pillow`.

- [ ] **Step 6: Rodar e ver passar**

Run: `python -m pytest tests/test_omr_import_boundary.py -v`
Expected: PASS nos 5 testes.

- [ ] **Step 7: Commit**

```bash
git add pyproject.toml src/agente_ia_edu/omr_worker/__init__.py \
        src/agente_ia_edu/omr_worker/config.py tests/test_omr_import_boundary.py
git commit -m "feat(omr): pacote do worker, extra omr e teste da fronteira de import

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 2: `ReaderTemplate` — vista em pixels do `CardTemplate` da Fase 1

**Files:**
- Create: `src/agente_ia_edu/omr_worker/template.py`
- Test: `tests/test_omr_template.py`

**Interfaces:**
- Consumes (da Fase 1, **importado, nunca redeclarado**):
  - `agente_ia_edu.simulado_card.template`: `CardTemplate`, `BubbleBox`, `MarkerPlacement`,
    `QrPlacement`, `TemplateError`, `LAYOUT_VERSION`.
  - `agente_ia_edu.simulado_card.layout`: `CardLayoutSpec`, `build_template`, `DEFAULT_LAYOUT`.
  - `agente_ia_edu.simulado_card.markers`: `CORNERS`, `MarkerSet`, `load_marker_set`.
- Produces:
  - `CANONICAL_WIDTH: int = 1600`
  - `ReaderTemplate` — dataclass frozen com `card: CardTemplate`, `marker_set: MarkerSet`,
    `canonical_width: int`, `canonical_height: int`; classmethods `from_card(card, *,
    marker_set=None, width=CANONICAL_WIDTH)` e `load(template_version, *, root=None,
    marker_set=None, width=CANONICAL_WIDTH)`; propriedades `template_version`,
    `aruco_dictionary`, `aruco_ids`, `bubbles`, `item_positions`; métodos
    `bubbles_for(item_position)`, `bubble_pixels(bubble)`, `aruco_corners_pixels()`,
    `qr_region_pixels()`.
  - `load_card(template_version, *, root=None) -> CardTemplate`

**O §2.1 do spec resolveu, na arquitetura, o risco que este plano tinha sinalizado:**

> "**O tipo do template é declarado uma única vez**, no módulo de geometria, e o leitor o
> **importa** — não redeclara uma cópia própria para ler o mesmo JSON. Duas definições do
> mesmo contrato divergem em silêncio, e o sintoma de divergência aqui não é teste vermelho:
> é o leitor medindo intensidade na coordenada errada e produzindo respostas plausíveis e
> falsas."

Por isso este módulo **não tem dataclass de geometria**. `ReaderTemplate` é uma *vista*: ela
guarda uma referência ao `CardTemplate` da Fase 1 e só acrescenta o que é exclusivo do leitor —
a escolha do tamanho canônico em pixels e a conversão das coordenadas normalizadas para lá.
Nenhum campo de geometria é copiado; `ReaderTemplate.bubbles` devolve as `BubbleBox` da Fase 1
tal como vieram.

**Sistema de coordenadas (definido pela Fase 1, Task 6).** As coordenadas **não** são
relativas à página A4: `(0,0)` é o centro do marcador ArUco superior-esquerdo e `(1,1)` o do
inferior-direito, com `y` crescendo para baixo. `radius` e `size` são normalizados pela
**largura** desse retângulo. É exatamente o sistema em que o leitor trabalha depois da
homografia — nenhuma conversão intermediária no meio.

**`marker_rect_aspect` — por que o leitor recusa em vez de supor.** Para que uma bolha impressa
redonda continue redonda depois da homografia, o retângulo canônico em pixels tem que ter a
mesma proporção do retângulo físico entre os centros dos marcadores: `H_px/W_px` = `H_mm/W_mm`.
Com outra proporção o círculo vira elipse e a máscara circular do §5.4 mede fora do disco — nas
450 bolhas, não em uma.

O §2.1 do spec resolve isso exigindo que o template carregue a razão, e que o leitor **recuse**
um template sem ela. `ReaderTemplate.from_card` levanta `TemplateError` com mensagem legível.

**Por que supor um valor seria pior do que falhar.** Este é um erro que *não daria teste
vermelho*: um cartão sintético gerado a partir da mesma suposição errada sairia esticado do
mesmo jeito, o leitor o leria perfeitamente, e a suíte inteira ficaria verde. A divergência só
apareceria no papel — no aceite físico, ou, se ele fosse pulado, na nota de um aluno. É
exatamente a classe de falha que o §5 chama de silenciosa, e a razão de a recusa ser
inegociável.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_omr_template.py`:

```python
"""Fase 4 (OMR) Task 2 - a vista em pixels do template geometrico da Fase 1.

§2.1 do spec: "O tipo do template e declarado uma unica vez, no modulo de
geometria, e o leitor o IMPORTA - nao redeclara uma copia propria para ler o
mesmo JSON."

Este arquivo prova as duas metades desse acordo:

  1. IDENTIDADE DE TIPO. `ReaderTemplate.card` E o `CardTemplate` da Fase 1, e
     `ReaderTemplate.bubbles` sao as `BubbleBox` dela. Se alguem um dia
     reintroduzir uma dataclass paralela aqui, o teste de identidade quebra.

  2. ROUND-TRIP. Gerar template -> serializar -> ler de volta -> a vista do
     leitor produz as MESMAS coordenadas em pixel. E a unica forma de provar
     que gerador e leitor concordam, e o sintoma de discordancia nao seria
     teste vermelho: seria o leitor medindo na coordenada errada e produzindo
     respostas plausiveis e falsas.
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from agente_ia_edu.omr_worker.template import (
    CANONICAL_WIDTH,
    ReaderTemplate,
    load_card,
)
from agente_ia_edu.simulado_card.layout import DEFAULT_LAYOUT, CardLayoutSpec, build_template
from agente_ia_edu.simulado_card.markers import CORNERS, load_marker_set
from agente_ia_edu.simulado_card.template import BubbleBox, CardTemplate, TemplateError


class ImportsTheGeneratorsType(unittest.TestCase):
    def test_the_reader_does_not_declare_a_second_card_template(self):
        import agente_ia_edu.omr_worker.template as reader_module
        import agente_ia_edu.simulado_card.template as geometry_module

        self.assertIs(reader_module.CardTemplate, geometry_module.CardTemplate)
        self.assertIs(reader_module.BubbleBox, geometry_module.BubbleBox)
        self.assertIs(reader_module.TemplateError, geometry_module.TemplateError)

    def test_the_view_holds_the_generators_object_not_a_copy(self):
        card = build_template()
        view = ReaderTemplate.from_card(card)
        self.assertIs(view.card, card)
        self.assertIs(view.bubbles[0], card.bubbles[0])
        self.assertIsInstance(view.bubbles[0], BubbleBox)


class CanonicalGeometry(unittest.TestCase):
    def setUp(self):
        self.card = build_template()
        self.view = ReaderTemplate.from_card(self.card)

    def test_the_canonical_rectangle_keeps_the_physical_aspect_ratio(self):
        # Sem isso a bolha redonda vira elipse depois da homografia e a mascara
        # de medicao do §5.4 sai do disco.
        expected = round(CANONICAL_WIDTH * self.card.marker_rect_aspect)
        self.assertEqual(self.view.canonical_width, CANONICAL_WIDTH)
        self.assertEqual(self.view.canonical_height, expected)

    def test_a_card_without_the_aspect_is_refused_not_guessed(self):
        class CardWithoutAspect:
            template_version = "sem-aspecto-v1"
            item_count = 1
            option_codes = ("A", "B", "C", "D", "E")
            markers = self.card.markers
            qr = self.card.qr
            bubbles = self.card.bubbles

        with self.assertRaises(TemplateError) as ctx:
            ReaderTemplate.from_card(CardWithoutAspect())
        self.assertIn("marker_rect_aspect", str(ctx.exception))

    def test_marker_centres_land_on_the_corners_of_the_canonical_rectangle(self):
        corners = self.view.aruco_corners_pixels()
        self.assertEqual(len(corners), 4)
        width, height = self.view.canonical_width, self.view.canonical_height
        for got, expected in zip(corners, ((0, 0), (width, 0), (width, height), (0, height))):
            self.assertAlmostEqual(got[0], expected[0], delta=1.0)
            self.assertAlmostEqual(got[1], expected[1], delta=1.0)

    def test_marker_ids_follow_the_generators_corner_order(self):
        by_corner = {m.corner: m.marker_id for m in self.card.markers}
        self.assertEqual(self.view.aruco_ids, tuple(by_corner[c] for c in CORNERS))

    def test_the_aruco_dictionary_comes_from_the_versioned_manifest(self):
        self.assertEqual(self.view.aruco_dictionary, load_marker_set().dictionary)

    def test_bubble_pixels_scale_x_and_the_radius_by_the_width(self):
        bubble = self.view.bubbles_for(1)[0]
        cx, cy, radius = self.view.bubble_pixels(bubble)
        self.assertAlmostEqual(cx, bubble.cx * self.view.canonical_width, places=6)
        self.assertAlmostEqual(cy, bubble.cy * self.view.canonical_height, places=6)
        self.assertAlmostEqual(radius, bubble.radius * self.view.canonical_width, places=6)

    def test_bubbles_stay_inside_the_canonical_rectangle(self):
        for bubble in self.view.bubbles:
            cx, cy, radius = self.view.bubble_pixels(bubble)
            self.assertGreater(cx - radius, -radius)
            self.assertLess(cx + radius, self.view.canonical_width + radius)

    def test_qr_region_is_a_square_box_around_the_declared_centre(self):
        x, y, width, height = self.view.qr_region_pixels()
        self.assertEqual(width, height)
        centre_x = x + width / 2
        self.assertAlmostEqual(centre_x, self.card.qr.cx * self.view.canonical_width, delta=1.0)

    def test_item_positions_cover_every_question_of_the_card(self):
        self.assertEqual(self.view.item_positions, tuple(range(1, self.card.item_count + 1)))

    def test_bubbles_for_unknown_position_raises(self):
        with self.assertRaises(TemplateError):
            self.view.bubbles_for(self.card.item_count + 5)


class RoundTripThroughTheStoredJson(unittest.TestCase):
    def test_generator_to_json_to_reader_produces_identical_pixel_geometry(self):
        card = build_template()
        direct = ReaderTemplate.from_card(card)
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            (root / f"template_{card.template_version}.json").write_text(
                json.dumps(card.to_dict(), ensure_ascii=False, sort_keys=True),
                encoding="utf-8")
            restored = ReaderTemplate.load(card.template_version, root=root)
        self.assertEqual(restored.card, card)
        self.assertEqual(
            [restored.bubble_pixels(b) for b in restored.bubbles],
            [direct.bubble_pixels(b) for b in direct.bubbles],
        )
        self.assertEqual(restored.aruco_corners_pixels(), direct.aruco_corners_pixels())
        self.assertEqual(restored.qr_region_pixels(), direct.qr_region_pixels())

    def test_a_smaller_exam_round_trips_too(self):
        card = build_template(CardLayoutSpec(item_count=45, rows_per_column=15))
        restored = CardTemplate.from_dict(json.loads(json.dumps(card.to_dict())))
        self.assertEqual(restored, card)
        self.assertEqual(ReaderTemplate.from_card(restored).item_positions,
                         tuple(range(1, 46)))

    def test_load_finds_the_template_inside_the_content_addressed_tree(self):
        # A Fase 1 grava via MaterialStorage: root/<2 chars>/<hash>/template_<v>.json
        card = build_template()
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            nested = root / "ab" / ("ab" + "0" * 62)
            nested.mkdir(parents=True)
            (nested / f"template_{card.template_version}.json").write_text(
                json.dumps(card.to_dict()), encoding="utf-8")
            self.assertEqual(load_card(card.template_version, root=root), card)

    def test_load_of_unknown_version_raises_with_a_readable_message(self):
        with tempfile.TemporaryDirectory() as tmp:
            with self.assertRaises(TemplateError) as ctx:
                load_card("nao-existe-v9", root=Path(tmp))
        self.assertIn("nao-existe-v9", str(ctx.exception))

    def test_the_default_layout_is_the_one_the_school_prints(self):
        self.assertEqual(DEFAULT_LAYOUT.item_count, 90)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_omr_template.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.omr_worker.template'`.

> Se falhar antes disso, em `from agente_ia_edu.simulado_card.layout import ...`, a Fase 1
> ainda não entregou a Task 6 dela. Pare: esta task consome aquele módulo por decisão de
> arquitetura (§2.1), e contorná-la reintroduz exatamente a duplicação que o spec proibiu.

- [ ] **Step 3: Implementar**

`src/agente_ia_edu/omr_worker/template.py`:

```python
"""Vista em PIXEIS do template geometrico emitido pela Fase 1 (§2.1 do spec).

ESTE MODULO NAO DECLARA GEOMETRIA. O tipo do template e declarado uma unica vez,
em `agente_ia_edu.simulado_card.template`, e aqui ele e IMPORTADO:

    "Duas definicoes do mesmo contrato divergem em silencio, e o sintoma de
    divergencia aqui nao e teste vermelho: e o leitor medindo intensidade na
    coordenada errada e produzindo respostas plausiveis e falsas."

`ReaderTemplate` acrescenta so o que e exclusivo do leitor: a escolha do tamanho
canonico em pixels e a conversao das coordenadas normalizadas para la. Nenhum
campo de geometria e copiado - `bubbles` devolve as `BubbleBox` da Fase 1 tal
como vieram.

O sistema de coordenadas e o da Fase 1: (0,0) e o centro do marcador ArUco
superior-esquerdo, (1,1) o do inferior-direito, y cresce para baixo, e `radius`
e `size` sao normalizados pela LARGURA desse retangulo. E exatamente o sistema
em que o leitor trabalha depois da homografia (§5.2).

Sem cv2 e sem numpy: e so aritmetica e leitura de JSON.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path

from ..simulado_card.markers import CORNERS, MarkerSet, load_marker_set
from ..simulado_card.template import (
    LAYOUT_VERSION,
    BubbleBox,
    CardTemplate,
    MarkerPlacement,
    QrPlacement,
    TemplateError,
)

__all__ = [
    "CANONICAL_WIDTH",
    "BubbleBox",
    "CardTemplate",
    "MarkerPlacement",
    "QrPlacement",
    "ReaderTemplate",
    "TemplateError",
    "LAYOUT_VERSION",
    "load_card",
]

#: Largura, em pixels, do retangulo canonico entre os centros dos marcadores.
#: 1600 px para ~1.0 do lado util de um A4 retrato equivale a ~200 DPI (§5.1),
#: e da ~26 px de diametro por bolha - folga confortavel para o disco interno
#: de 0.62 do §5.4 sem estourar memoria em lote de 1.500 cartoes.
CANONICAL_WIDTH = 1600

#: Raiz do MaterialStorage. A Fase 1 grava o template como
#: `template_<versao>.json` dentro da arvore endereçada por conteudo, entao o
#: arquivo e localizado por busca recursiva pelo nome, e nao por caminho fixo.
_DEFAULT_ROOT = Path(__file__).resolve().parents[3] / "var" / "material_storage"


def load_card(template_version: str, *, root: Path | None = None) -> CardTemplate:
    """Le o JSON gravado pela Fase 1 e devolve o `CardTemplate` DELA."""
    base = root or _DEFAULT_ROOT
    filename = f"template_{template_version}.json"
    path = base / filename
    if not path.is_file():
        found = sorted(base.rglob(filename)) if base.is_dir() else []
        path = found[0] if found else path
    if not path.is_file():
        raise TemplateError(
            f"template {template_version!r} nao encontrado em {base}. "
            "O template e emitido pelo gerador de cartoes (Fase 1) no mesmo ato da "
            "impressao; sem ele o leitor nao sabe onde estao as bolhas e nao ha o que ler."
        )
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise TemplateError(f"template {template_version!r} nao e JSON valido: {exc}") from exc
    return CardTemplate.from_dict(payload)


@dataclass(frozen=True)
class ReaderTemplate:
    """O `CardTemplate` da Fase 1 mais o tamanho canonico escolhido pelo leitor."""

    card: CardTemplate
    marker_set: MarkerSet
    canonical_width: int
    canonical_height: int

    @classmethod
    def from_card(
        cls,
        card: CardTemplate,
        *,
        marker_set: MarkerSet | None = None,
        width: int = CANONICAL_WIDTH,
    ) -> "ReaderTemplate":
        aspect = getattr(card, "marker_rect_aspect", None)
        if aspect is None or not (0.1 < float(aspect) < 10.0):
            raise TemplateError(
                "o template nao traz `marker_rect_aspect` (altura/largura do retangulo "
                "entre os centros dos marcadores). Sem essa razao o retangulo canonico "
                "sai com a proporcao errada, a bolha redonda vira elipse depois da "
                "homografia e a medicao do §5.4 mede fora do disco. O leitor nao supoe "
                "esse valor: regere o template com a versao do gerador que o emite."
            )
        return cls(
            card=card,
            marker_set=marker_set or load_marker_set(),
            canonical_width=int(width),
            canonical_height=int(round(int(width) * float(aspect))),
        )

    @classmethod
    def load(
        cls,
        template_version: str,
        *,
        root: Path | None = None,
        marker_set: MarkerSet | None = None,
        width: int = CANONICAL_WIDTH,
    ) -> "ReaderTemplate":
        return cls.from_card(
            load_card(template_version, root=root), marker_set=marker_set, width=width
        )

    # --- passagens diretas para o contrato da Fase 1 ---

    @property
    def template_version(self) -> str:
        return self.card.template_version

    @property
    def bubbles(self) -> tuple[BubbleBox, ...]:
        return self.card.bubbles

    @property
    def item_positions(self) -> tuple[int, ...]:
        return tuple(range(1, self.card.item_count + 1))

    def bubbles_for(self, item_position: int) -> tuple[BubbleBox, ...]:
        return self.card.bubbles_for(item_position)

    # --- o que e exclusivo do leitor ---

    @property
    def aruco_dictionary(self) -> str:
        return self.marker_set.dictionary

    @property
    def aruco_ids(self) -> tuple[int, ...]:
        """Ids dos quatro marcadores na ordem de `CORNERS` (TL, TR, BR, BL)."""
        by_corner = {marker.corner: marker.marker_id for marker in self.card.markers}
        missing = [corner for corner in CORNERS if corner not in by_corner]
        if missing:
            raise TemplateError(
                f"template {self.template_version}: marcadores ausentes para {missing}"
            )
        return tuple(by_corner[corner] for corner in CORNERS)

    def bubble_pixels(self, bubble: BubbleBox) -> tuple[float, float, float]:
        """(cx, cy, raio) em pixels do retangulo canonico."""
        return (
            bubble.cx * self.canonical_width,
            bubble.cy * self.canonical_height,
            bubble.radius * self.canonical_width,
        )

    def aruco_corners_pixels(self) -> tuple[tuple[float, float], ...]:
        """Centros dos quatro marcadores, em pixels, na ordem de `CORNERS`."""
        by_corner = {marker.corner: marker for marker in self.card.markers}
        return tuple(
            (
                by_corner[corner].cx * self.canonical_width,
                by_corner[corner].cy * self.canonical_height,
            )
            for corner in CORNERS
        )

    def qr_region_pixels(self) -> tuple[int, int, int, int]:
        """(x, y, lado, lado) do quadrado do QR, em pixels. O `size` do
        `QrPlacement` e normalizado pela LARGURA, entao o quadrado permanece
        quadrado em pixels."""
        side = int(round(self.card.qr.size * self.canonical_width))
        x = int(round(self.card.qr.cx * self.canonical_width - side / 2))
        y = int(round(self.card.qr.cy * self.canonical_height - side / 2))
        return (x, y, side, side)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_omr_template.py -v`
Expected: PASS nos 16 testes.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/omr_worker/template.py tests/test_omr_template.py
git commit -m "feat(omr): ReaderTemplate importa o CardTemplate da Fase 1, nao o redeclara

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 3: §5.1 Rasterizar — PDF ou imagem → páginas em cinza

**Files:**
- Create: `src/agente_ia_edu/omr_worker/raster.py`
- Test: `tests/test_omr_raster.py`

**Interfaces:**
- Consumes: `OmrConfig.raster_dpi` (Task 1).
- Produces:
  - `rasterize(path: Path, *, dpi: int = 200) -> list[np.ndarray]` — lista de páginas em
    `uint8` grayscale, shape `(altura, largura)`.
  - `RasterError(RuntimeError)`

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_omr_raster.py`:

```python
"""Fase 4 (OMR) Task 3 - §5.1 do spec: "PDF ou imagem -> paginas a ~200 DPI (PyMuPDF)".

PyMuPDF (ja instalado como extra `recovery` e ja usado pelo core em
api/routes/question_extraction.py:289) le PDF e tambem PNG/JPEG, entao a
rasterizacao e um caminho unico para lote em PDF e para imagem solta - as duas
formas de envio previstas no §4.4 do spec.

Sempre CINZA e sempre uint8: o resto do pipeline mede intensidade, nunca cor.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import fitz
import numpy as np

from agente_ia_edu.omr_worker.raster import RasterError, rasterize


def _write_pdf(path: Path, pages: int) -> None:
    doc = fitz.open()
    for index in range(pages):
        page = doc.new_page(width=595, height=842)  # A4 em pontos
        page.draw_rect(fitz.Rect(50, 50 + index * 10, 200, 200), color=(0, 0, 0), fill=(0, 0, 0))
    doc.save(str(path))
    doc.close()


def _write_png(path: Path) -> None:
    pixmap = fitz.Pixmap(fitz.csGRAY, fitz.IRect(0, 0, 120, 80))
    pixmap.clear_with(255)
    pixmap.save(str(path))


class Rasterize(unittest.TestCase):
    def test_pdf_yields_one_grayscale_page_per_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "lote.pdf"
            _write_pdf(pdf, pages=3)
            pages = rasterize(pdf, dpi=100)
        self.assertEqual(len(pages), 3)
        for page in pages:
            self.assertEqual(page.dtype, np.uint8)
            self.assertEqual(page.ndim, 2, "a saida precisa ser cinza 2D, nunca BGR")

    def test_dpi_controls_the_resolution(self):
        with tempfile.TemporaryDirectory() as tmp:
            pdf = Path(tmp) / "uma.pdf"
            _write_pdf(pdf, pages=1)
            low = rasterize(pdf, dpi=100)[0]
            high = rasterize(pdf, dpi=200)[0]
        self.assertGreater(high.shape[0], low.shape[0] * 1.8)
        self.assertGreater(high.shape[1], low.shape[1] * 1.8)

    def test_standalone_image_is_rasterized_as_a_single_page(self):
        with tempfile.TemporaryDirectory() as tmp:
            png = Path(tmp) / "cartao.png"
            _write_png(png)
            pages = rasterize(png, dpi=200)
        self.assertEqual(len(pages), 1)
        self.assertEqual(pages[0].ndim, 2)

    def test_missing_file_raises_a_readable_error(self):
        with self.assertRaises(RasterError) as ctx:
            rasterize(Path("/tmp/nao-existe-omr.pdf"), dpi=200)
        self.assertIn("nao-existe-omr.pdf", str(ctx.exception))

    def test_unreadable_file_raises_instead_of_returning_an_empty_list(self):
        with tempfile.TemporaryDirectory() as tmp:
            junk = Path(tmp) / "quebrado.pdf"
            junk.write_bytes(b"isto nao e um pdf")
            with self.assertRaises(RasterError):
                rasterize(junk, dpi=200)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_omr_raster.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.omr_worker.raster'`.

- [ ] **Step 3: Implementar**

`src/agente_ia_edu/omr_worker/raster.py`:

```python
"""§5.1 do spec: rasterizar PDF ou imagem em paginas cinza a ~200 DPI.

PyMuPDF abre os dois formatos de envio previstos no §4.4 ("lote em PDF ou
imagens soltas") com o mesmo codigo, e ja e dependencia conhecida do projeto
(extra `recovery`; o core ja o usa em api/routes/question_extraction.py:289).

Sem cv2 aqui de proposito: rasterizacao e o unico passo do pipeline que nao
precisa de OpenCV, e mante-la fora dele deixa este modulo testavel em qualquer
ambiente que tenha o extra `recovery`.
"""

from __future__ import annotations

from pathlib import Path

import fitz
import numpy as np


class RasterError(RuntimeError):
    """Arquivo ausente, ilegivel ou sem nenhuma pagina. Vira
    `answer_card_scans.failure_reason` legivel, nunca leitura vazia silenciosa."""


def rasterize(path: Path, *, dpi: int = 200) -> list[np.ndarray]:
    source = Path(path)
    if not source.is_file():
        raise RasterError(f"arquivo de digitalizacao nao encontrado: {source}")
    try:
        document = fitz.open(str(source))
    except Exception as exc:  # noqa: BLE001 - qualquer falha do MuPDF vira motivo legivel
        raise RasterError(f"nao foi possivel abrir {source.name}: {exc}") from exc

    try:
        if document.page_count == 0:
            raise RasterError(f"{source.name} nao tem nenhuma pagina")
        pages: list[np.ndarray] = []
        for index in range(document.page_count):
            pixmap = document[index].get_pixmap(dpi=dpi, colorspace=fitz.csGRAY, alpha=False)
            buffer = np.frombuffer(pixmap.samples, dtype=np.uint8)
            pages.append(buffer.reshape(pixmap.height, pixmap.width).copy())
        return pages
    finally:
        document.close()
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_omr_raster.py -v`
Expected: PASS nos 5 testes.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/omr_worker/raster.py tests/test_omr_raster.py
git commit -m "feat(omr): §5.1 rasterizacao de PDF e imagem via PyMuPDF

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 4: §5.2 Alinhar — ArUco + homografia

**Files:**
- Create: `src/agente_ia_edu/omr_worker/align.py`
- Test: `tests/test_omr_align.py`

**Interfaces:**
- Consumes: `ReaderTemplate` e `ReaderTemplate.aruco_corners_pixels()` (Task 2).
- Produces:
  - `AlignmentResult(image: np.ndarray | None, detected_ids: tuple[int, ...], failure_reason: str | None)` — frozen.
  - `align_to_template(gray: np.ndarray, template: ReaderTemplate) -> AlignmentResult`
  - `draw_marker(marker_id: int, size_px: int, *, dictionary: str = "DICT_4X4_50") -> np.ndarray`
    (usado pela Task 9 para desenhar o cartão sintético e, na Fase 1, para pré-gerar as
    imagens estáticas dos marcadores)
  - `ARUCO_NOT_DETECTED: str = "ARUCO_NAO_DETECTADO"`

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_omr_align.py`:

```python
"""Fase 4 (OMR) Task 4 - §5.2 do spec.

"Detectar os quatro marcadores ArUco de canto, calcular a homografia e
desentortar para o tamanho canonico. E este passo que torna a foto de celular
viavel: perspectiva, rotacao e escala sao desfeitas de uma vez."

E o passo com a regra mais dura do §5: ArUco nao detectado NAO e um caso a ser
resolvido por heuristica de contorno - o scan vai para FAILED com motivo legivel
e o operador reenvia. "Um OMR que adivinha e pior que um que pede ajuda."
"""

from __future__ import annotations

import unittest

import cv2
import numpy as np

from agente_ia_edu.omr_worker.align import ARUCO_NOT_DETECTED, align_to_template, draw_marker
from agente_ia_edu.omr_worker.template import ReaderTemplate
from agente_ia_edu.simulado_card.layout import build_template

MARKER_PX = 70


def build_view() -> ReaderTemplate:
    """O template REAL da Fase 1, so que em pixels pequenos para o teste rodar
    rapido. Nada de geometria inventada: e o mesmo cartao que a escola imprime."""
    return ReaderTemplate.from_card(build_template(), width=900)


def render_card(view: ReaderTemplate) -> np.ndarray:
    card = np.full((view.canonical_height, view.canonical_width), 255, dtype=np.uint8)
    for marker_id, (cx, cy) in zip(view.aruco_ids, view.aruco_corners_pixels()):
        marker = draw_marker(marker_id, MARKER_PX, dictionary=view.aruco_dictionary)
        x0 = int(round(cx - MARKER_PX / 2))
        y0 = int(round(cy - MARKER_PX / 2))
        # Os centros dos marcadores ficam NOS CANTOS do retangulo canonico, entao
        # metade de cada marcador cai fora da imagem: o recorte e intencional.
        sx0, sy0 = max(0, x0), max(0, y0)
        sx1 = min(view.canonical_width, x0 + MARKER_PX)
        sy1 = min(view.canonical_height, y0 + MARKER_PX)
        card[sy0:sy1, sx0:sx1] = marker[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0]
    cv2.circle(card, (view.canonical_width // 2, view.canonical_height // 2), 24, 0, -1)
    return card


def warp(card: np.ndarray, offsets: np.ndarray, pad: int) -> np.ndarray:
    h, w = card.shape
    src = np.array([[0, 0], [w, 0], [w, h], [0, h]], dtype=np.float32)
    dst = (src + offsets + np.array([pad / 2, pad / 2], dtype=np.float32)).astype(np.float32)
    matrix = cv2.getPerspectiveTransform(src, dst)
    return cv2.warpPerspective(card, matrix, (w + pad, h + pad), borderValue=255)


class Alignment(unittest.TestCase):
    def setUp(self):
        self.view = build_view()
        self.card = render_card(self.view)

    def test_a_clean_card_is_returned_at_canonical_size(self):
        result = align_to_template(self.card, self.view)
        self.assertIsNone(result.failure_reason)
        self.assertEqual(result.image.shape,
                         (self.view.canonical_height, self.view.canonical_width))
        self.assertEqual(sorted(result.detected_ids), sorted(self.view.aruco_ids))

    def test_perspective_is_undone_and_the_content_lands_where_the_template_says(self):
        offsets = np.array([[60, 20], [-40, 55], [25, -35], [-15, -25]], dtype=np.float32)
        photo = warp(self.card, offsets, 160)
        result = align_to_template(photo, self.view)
        self.assertIsNone(result.failure_reason)
        mid_x = self.view.canonical_width // 2
        mid_y = self.view.canonical_height // 2
        centre = result.image[mid_y - 8:mid_y + 8, mid_x - 8:mid_x + 8]
        self.assertLess(float(centre.mean()), 70.0,
                        "o disco central preto deve reaparecer no centro do cartao canonico")

    def test_missing_marker_fails_loudly_and_never_guesses(self):
        damaged = self.card.copy()
        cx, cy = self.view.aruco_corners_pixels()[2]
        missing_id = self.view.aruco_ids[2]
        x0, y0 = max(0, int(cx) - MARKER_PX), max(0, int(cy) - MARKER_PX)
        damaged[y0:y0 + 2 * MARKER_PX, x0:x0 + 2 * MARKER_PX] = 255
        result = align_to_template(damaged, self.view)
        self.assertIsNone(result.image)
        self.assertIsNotNone(result.failure_reason)
        self.assertIn(ARUCO_NOT_DETECTED, result.failure_reason)
        self.assertIn(str(missing_id), result.failure_reason,
                      "o motivo diz QUAL marcador faltou")

    def test_blank_page_fails_with_all_four_markers_reported_missing(self):
        blank = np.full((self.view.canonical_height, self.view.canonical_width),
                        255, dtype=np.uint8)
        result = align_to_template(blank, self.view)
        self.assertIsNone(result.image)
        self.assertIn(ARUCO_NOT_DETECTED, result.failure_reason)
        self.assertEqual(result.detected_ids, ())

    def test_draw_marker_is_square_grayscale_and_binary(self):
        marker = draw_marker(1, 64)
        self.assertEqual(marker.shape, (64, 64))
        self.assertEqual(marker.dtype, np.uint8)
        self.assertEqual(set(np.unique(marker).tolist()), {0, 255})


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_omr_align.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.omr_worker.align'`.

- [ ] **Step 3: Implementar**

`src/agente_ia_edu/omr_worker/align.py`:

```python
"""§5.2 do spec: alinhar o cartao capturado ao tamanho canonico do template,
por deteccao dos quatro marcadores ArUco de canto e homografia.

MODULO COM `import cv2` - so pode existir dentro de `omr_worker/` (§2.3).

Por que ArUco e nao deteccao de contorno/borda da folha: um marcador ArUco
carrega o proprio identificador com codigo corretor de erro, entao o detector
nao so acha os quatro cantos como sabe QUAL canto e cada um. Rotacao de 180
graus (folha virada no scanner) sai de graca, e uma folha dobrada ou com a borda
cortada na foto nao quebra o alinhamento.

Os marcadores NAO sao gerados em tempo de leitura para o cartao impresso (§2.1:
sao imagens estaticas versionadas, posicionadas pelo ReportLab). `draw_marker`
existe aqui para o cartao SINTETICO dos testes (§8.2) e para a Fase 1 pre-gerar
aquelas imagens uma vez.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .template import ReaderTemplate

ARUCO_NOT_DETECTED = "ARUCO_NAO_DETECTADO"


@dataclass(frozen=True)
class AlignmentResult:
    """`image` e None exatamente quando `failure_reason` e preenchido. Nao existe
    terceiro estado: ou o cartao foi retificado, ou o scan vai para FAILED."""

    image: np.ndarray | None
    detected_ids: tuple[int, ...]
    failure_reason: str | None


def _dictionary(name: str) -> cv2.aruco.Dictionary:
    try:
        predefined = getattr(cv2.aruco, name)
    except AttributeError as exc:
        raise ValueError(f"dicionario ArUco desconhecido: {name!r}") from exc
    return cv2.aruco.getPredefinedDictionary(predefined)


def draw_marker(marker_id: int, size_px: int, *, dictionary: str = "DICT_4X4_50") -> np.ndarray:
    """Imagem cinza do marcador, 0/255. Usada pelo cartao sintetico (§8.2)."""
    return cv2.aruco.generateImageMarker(_dictionary(dictionary), int(marker_id), int(size_px))


def align_to_template(gray: np.ndarray, template: ReaderTemplate) -> AlignmentResult:
    detector = cv2.aruco.ArucoDetector(
        _dictionary(template.aruco_dictionary), cv2.aruco.DetectorParameters()
    )
    corners, ids, _rejected = detector.detectMarkers(gray)
    found: dict[int, np.ndarray] = {}
    if ids is not None:
        for marker_id, quad in zip(ids.flatten().tolist(), corners):
            found[int(marker_id)] = quad.reshape(4, 2)

    detected = tuple(sorted(found))
    missing = [marker_id for marker_id in template.aruco_ids if marker_id not in found]
    if missing:
        return AlignmentResult(
            image=None,
            detected_ids=detected,
            failure_reason=(
                f"{ARUCO_NOT_DETECTED}: marcadores de canto ausentes {missing} "
                f"(detectados: {list(detected)}). Reenvie a digitalizacao com os quatro "
                "cantos do cartao visiveis e sem corte."
            ),
        )

    # Centro de cada marcador -> centro declarado no template. Quatro pontos
    # nao-colineares definem a homografia exatamente.
    source = np.array(
        [found[marker_id].mean(axis=0) for marker_id in template.aruco_ids], dtype=np.float32
    )
    destination = np.array(template.aruco_corners_pixels(), dtype=np.float32)
    matrix = cv2.getPerspectiveTransform(source, destination)
    canonical = cv2.warpPerspective(
        gray, matrix,
        (template.canonical_width, template.canonical_height),
        flags=cv2.INTER_LINEAR,
        borderMode=cv2.BORDER_CONSTANT,
        borderValue=255,  # fora da folha e papel branco, nunca preto (viraria "bolha cheia")
    )
    return AlignmentResult(image=canonical, detected_ids=detected, failure_reason=None)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_omr_align.py -v`
Expected: PASS nos 5 testes.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/omr_worker/align.py tests/test_omr_align.py
git commit -m "feat(omr): §5.2 alinhamento por ArUco + homografia

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 5: §5.3 Identificar — leitura do QR

**Files:**
- Create: `src/agente_ia_edu/omr_worker/identify.py`
- Test: `tests/test_omr_identify.py`

**Interfaces:**
- Consumes: `ReaderTemplate.qr_region_pixels()` (Task 2).
- Produces:
  - `QrReadResult(token: str | None, failure_reason: str | None)` — frozen.
  - `read_qr_token(canonical: np.ndarray, template: ReaderTemplate) -> QrReadResult`
  - `encode_qr(payload: str, size_px: int) -> np.ndarray` (usado pela Task 9)
  - `QR_UNREADABLE: str = "QR_ILEGIVEL"`

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_omr_identify.py`:

```python
"""Fase 4 (OMR) Task 5 - §5.3 do spec: "Ler o QR, resolver o `answer_card` e
portanto o aluno e o template."

O token lido e OPACO por decisao de privacidade (§3): o QR impresso no papel nao
carrega nome, matricula nem nada pessoal - so uma chave que o banco resolve.
Este modulo, portanto, nunca interpreta o conteudo do QR; devolve a string crua.

QR ilegivel -> scan FAILED com motivo legivel (§5). Nunca "o cartao parece ser do
aluno X porque veio nesse lote".
"""

from __future__ import annotations

import unittest

import numpy as np

from agente_ia_edu.omr_worker.identify import QR_UNREADABLE, encode_qr, read_qr_token
from agente_ia_edu.omr_worker.template import ReaderTemplate
from agente_ia_edu.simulado_card.layout import build_template

TOKEN = "3f1c9a2b7d4e5f6a8b0c1d2e3f405162"


def build_view() -> ReaderTemplate:
    return ReaderTemplate.from_card(build_template(), width=900)


def card_with_qr(view: ReaderTemplate, payload: str) -> np.ndarray:
    card = np.full((view.canonical_height, view.canonical_width), 255, dtype=np.uint8)
    x, y, side, _ = view.qr_region_pixels()
    card[y:y + side, x:x + side] = encode_qr(payload, side)
    return card


class QrReading(unittest.TestCase):
    def setUp(self):
        self.view = build_view()

    def test_reads_the_opaque_token_from_the_declared_region(self):
        result = read_qr_token(card_with_qr(self.view, TOKEN), self.view)
        self.assertIsNone(result.failure_reason)
        self.assertEqual(result.token, TOKEN)

    def test_reads_the_qr_even_when_it_drifted_outside_the_declared_region(self):
        # A regiao do template e uma dica, nao um dogma: se o QR nao decodificar
        # ali, o cartao inteiro e tentado antes de desistir.
        height, width = self.view.canonical_height, self.view.canonical_width
        card = np.full((height, width), 255, dtype=np.uint8)
        side = 180
        card[height - side - 40:height - 40, 40:40 + side] = encode_qr(TOKEN, side)
        result = read_qr_token(card, self.view)
        self.assertEqual(result.token, TOKEN)

    def test_card_without_qr_fails_loudly(self):
        blank = np.full((self.view.canonical_height, self.view.canonical_width),
                        255, dtype=np.uint8)
        result = read_qr_token(blank, self.view)
        self.assertIsNone(result.token)
        self.assertIn(QR_UNREADABLE, result.failure_reason)

    def test_destroyed_qr_fails_instead_of_returning_partial_text(self):
        card = card_with_qr(self.view, TOKEN)
        x, y, w, h = self.view.qr_region_pixels()
        card[y:y + h, x:x + w] = 255
        result = read_qr_token(card, self.view)
        self.assertIsNone(result.token)
        self.assertIn(QR_UNREADABLE, result.failure_reason)

    def test_encode_qr_round_trips(self):
        image = encode_qr("abc-123", 180)
        self.assertEqual(image.shape, (180, 180))
        self.assertEqual(image.dtype, np.uint8)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_omr_identify.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.omr_worker.identify'`.

- [ ] **Step 3: Implementar**

`src/agente_ia_edu/omr_worker/identify.py`:

```python
"""§5.3 do spec: ler o QR do cartao alinhado e devolver o token OPACO.

MODULO COM `import cv2` - so pode existir dentro de `omr_worker/` (§2.3).

O token nunca e interpretado aqui. §3: "O `qr_token` e opaco por decisao de
privacidade: o QR impresso no papel nao carrega nome, matricula nem qualquer
dado pessoal - apenas uma chave que so o banco resolve." Quem resolve token ->
`answer_card` -> aluno e o `persist.py`, dentro de uma transacao, com o banco.

Estrategia de duas passadas: a regiao declarada no template primeiro (rapida e
imune a qualquer outro codigo impresso no cartao), o cartao inteiro depois. A
segunda passada existe porque uma leve imprecisao de homografia pode deslocar o
QR alguns pixels para fora do retangulo declarado - e um QR que decodifica e um
dado certo, nao um chute.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

from .template import ReaderTemplate

QR_UNREADABLE = "QR_ILEGIVEL"

_PAD_RATIO = 0.02  # folga em volta da regiao declarada, em fracao da largura

_QR_FAILURE_MESSAGE = (
    f"{QR_UNREADABLE}: nao foi possivel decodificar o QR de identificacao do cartao. "
    "Reenvie a digitalizacao com o QR nitido e sem reflexo, ou digite as respostas "
    "manualmente para este aluno."
)


@dataclass(frozen=True)
class QrReadResult:
    token: str | None
    failure_reason: str | None


def encode_qr(payload: str, size_px: int) -> np.ndarray:
    """QR em cinza 0/255, quadrado. Usado pelo cartao sintetico (§8.2)."""
    encoder = cv2.QRCodeEncoder.create()
    encoded = encoder.encode(payload)
    if encoded.ndim == 3:
        encoded = cv2.cvtColor(encoded, cv2.COLOR_BGR2GRAY)
    resized = cv2.resize(encoded, (int(size_px), int(size_px)), interpolation=cv2.INTER_NEAREST)
    return np.where(resized > 127, 255, 0).astype(np.uint8)


def _decode(image: np.ndarray) -> str:
    detector = cv2.QRCodeDetector()
    text, _points, _straight = detector.detectAndDecode(image)
    return (text or "").strip()


def read_qr_token(canonical: np.ndarray, template: "ReaderTemplate") -> QrReadResult:
    height, width = canonical.shape[:2]
    x, y, region_width, region_height = template.qr_region_pixels()
    pad = int(round(_PAD_RATIO * width))
    x0 = max(0, x - pad)
    y0 = max(0, y - pad)
    x1 = min(width, x + region_width + pad)
    y1 = min(height, y + region_height + pad)

    token = ""
    if x1 > x0 and y1 > y0:
        token = _decode(canonical[y0:y1, x0:x1])
    if not token:
        token = _decode(canonical)
    if not token:
        return QrReadResult(token=None, failure_reason=_QR_FAILURE_MESSAGE)
    return QrReadResult(token=token, failure_reason=None)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_omr_identify.py -v`
Expected: PASS nos 5 testes.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/omr_worker/identify.py tests/test_omr_identify.py
git commit -m "feat(omr): §5.3 leitura do QR opaco do cartao

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 6: §5.4 Medir — intensidade normalizada pelo fundo local

**Files:**
- Create: `src/agente_ia_edu/omr_worker/measure.py`
- Test: `tests/test_omr_measure.py`

**Interfaces:**
- Consumes: `ReaderTemplate`, `ReaderTemplate.bubble_pixels()` (Task 2); `OmrConfig.inner_disc_ratio`,
  `OmrConfig.background_inner_ratio`, `OmrConfig.background_outer_ratio` (Task 1).
- Produces:
  - `BubbleMeasurement(item_position: int, option: str, intensity: float)` — frozen;
    `intensity` em 0.0 (papel limpo) a 1.0 (tinta saturada).
  - `measure_bubbles(canonical: np.ndarray, template: ReaderTemplate, config: OmrConfig) -> tuple[BubbleMeasurement, ...]`

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_omr_measure.py`:

```python
"""Fase 4 (OMR) Task 6 - §5.4 do spec.

"Para cada bolha do template, intensidade media no disco interno, normalizada
pelo fundo local imediato - o que neutraliza sombra irregular e papel amarelado."

O teste central aqui e o da SOMBRA: duas bolhas identicamente preenchidas, uma
sob sombra forte, tem que produzir intensidades proximas. Sem a normalizacao
pelo fundo local, a sombra por si so levantaria a intensidade de uma bolha VAZIA
acima do limiar - e um aluno ganharia respostas que nunca marcou.

Sem cv2: e so NumPy sobre a matriz. Da para verificar com numeros na mao.
"""

from __future__ import annotations

import unittest

import numpy as np

from agente_ia_edu.omr_worker.config import OmrConfig
from agente_ia_edu.omr_worker.measure import measure_bubbles
from agente_ia_edu.omr_worker.template import ReaderTemplate
from agente_ia_edu.simulado_card.layout import build_template

#: Item da PRIMEIRA coluna do cartao (metade esquerda) e item da ULTIMA coluna
#: (metade direita). O teste da sombra precisa de uma bolha de cada lado, e o
#: layout de 3 colunas x 30 linhas da Fase 1 poe a questao 1 a esquerda e a 61
#: a direita.
LEFT_ITEM = 1
RIGHT_ITEM = 61


def build_view() -> ReaderTemplate:
    """Template REAL da Fase 1, em pixels pequenos para o teste ser barato."""
    return ReaderTemplate.from_card(build_template(), width=700)


def fill(card: np.ndarray, view: ReaderTemplate, position: int, option: str, value: int) -> None:
    bubble = next(b for b in view.bubbles_for(position) if b.option == option)
    cx, cy, r = view.bubble_pixels(bubble)
    ys, xs = np.mgrid[0:card.shape[0], 0:card.shape[1]]
    card[(xs - cx) ** 2 + (ys - cy) ** 2 <= (r * 0.85) ** 2] = value


class Measurement(unittest.TestCase):
    def setUp(self):
        self.view = build_view()
        self.config = OmrConfig()
        self.blank = np.full((self.view.canonical_height, self.view.canonical_width),
                             255, dtype=np.uint8)

    def _by_option(self, card):
        return {(m.item_position, m.option): m.intensity
                for m in measure_bubbles(card, self.view, self.config)}

    def test_returns_one_measurement_per_bubble_in_template_order(self):
        measurements = measure_bubbles(self.blank, self.view, self.config)
        self.assertEqual(len(measurements), len(self.view.bubbles))
        self.assertEqual([m.option for m in measurements[:5]], list("ABCDE"))
        self.assertEqual([m.item_position for m in measurements[:5]], [1] * 5)

    def test_clean_paper_measures_near_zero(self):
        for measurement in measure_bubbles(self.blank, self.view, self.config):
            self.assertLess(measurement.intensity, 0.05)

    def test_a_filled_bubble_measures_near_one(self):
        card = self.blank.copy()
        fill(card, self.view, LEFT_ITEM, "C", 20)
        measurements = self._by_option(card)
        self.assertGreater(measurements[(LEFT_ITEM, "C")], 0.85)
        self.assertLess(measurements[(LEFT_ITEM, "B")], 0.05)

    def test_local_background_normalisation_neutralises_a_shadow(self):
        # Metade direita do cartao sob sombra forte (papel a 150 em vez de 255).
        card = self.blank.copy()
        card[:, self.view.canonical_width // 2:] = 150
        fill(card, self.view, LEFT_ITEM, "A", 20)    # lado claro
        fill(card, self.view, RIGHT_ITEM, "A", 12)   # lado sombreado, tinta igualmente escura
        measurements = self._by_option(card)
        self.assertGreater(measurements[(LEFT_ITEM, "A")], 0.85)
        self.assertGreater(measurements[(RIGHT_ITEM, "A")], 0.85)
        self.assertLess(
            abs(measurements[(LEFT_ITEM, "A")] - measurements[(RIGHT_ITEM, "A")]), 0.12)

    def test_an_empty_bubble_under_shadow_stays_empty(self):
        # A regressao que a normalizacao existe para impedir: sombra virando marca.
        card = self.blank.copy()
        card[:, self.view.canonical_width // 2:] = 120
        measurements = self._by_option(card)
        self.assertLess(measurements[(RIGHT_ITEM, "E")], 0.10)

    def test_yellowed_paper_does_not_inflate_intensity(self):
        card = np.full((self.view.canonical_height, self.view.canonical_width),
                       205, dtype=np.uint8)  # papel amarelado uniforme
        for measurement in measure_bubbles(card, self.view, self.config):
            self.assertLess(measurement.intensity, 0.06)

    def test_a_bubble_partially_outside_the_canvas_does_not_crash(self):
        # Os centros dos marcadores ficam NOS cantos do retangulo canonico, entao
        # uma homografia levemente imprecisa pode empurrar bolhas da borda para
        # fora. Medir fora do quadro devolve 0.0 - que a §5.7 le como branco,
        # nunca como resposta.
        cropped = self.blank[: self.view.canonical_height - 20, : self.view.canonical_width - 20]
        padded = np.full((self.view.canonical_height, self.view.canonical_width),
                         255, dtype=np.uint8)
        padded[: cropped.shape[0], : cropped.shape[1]] = cropped
        measurements = measure_bubbles(padded[:10, :10], self.view, self.config)
        self.assertEqual(len(measurements), len(self.view.bubbles))
        self.assertTrue(all(0.0 <= m.intensity <= 1.0 for m in measurements))


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_omr_measure.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.omr_worker.measure'`.

- [ ] **Step 3: Implementar**

`src/agente_ia_edu/omr_worker/measure.py`:

```python
"""§5.4 do spec: intensidade de cada bolha, normalizada pelo fundo local imediato.

Sem cv2 - so NumPy. Este e o modulo cuja decisao mais afeta a qualidade da
leitura, e mante-lo em aritmetica pura e o que permite testa-lo com uma matriz
montada a mao, sem renderizar cartao nenhum.

Duas escolhas com consequencia:

1. DISCO INTERNO, nao a bolha inteira. O anel impresso da bolha e tinta preta em
   TODA bolha, cheia ou vazia. Medir a bolha inteira somaria esse anel aos dois
   casos e comprimiria a diferenca entre eles justamente onde ela importa.
   `inner_disc_ratio = 0.62` fica com folga dentro do anel.

2. FUNDO LOCAL IMEDIATO, nao o fundo do cartao. O fundo e a MEDIANA de um anel
   ao redor da bolha (1.25r a 1.90r). Mediana e nao media porque esse anel pega
   a letra impressa ao lado da bolha, e uma media seria puxada por ela; a
   mediana ignora. E o fundo e local porque sombra de celular e papel amarelado
   variam ao longo da folha - usar um fundo global faria a leitura de um canto
   depender da iluminacao do outro.

intensity = (fundo - tinta) / fundo, recortado em [0, 1]. Zero e papel limpo, um
e tinta saturada, e a escala nao depende do brilho absoluto da captura - que e
exatamente o que o §5.5 precisa para o Otsu por cartao funcionar.
"""

from __future__ import annotations

from dataclasses import dataclass

import numpy as np

from .config import OmrConfig
from .template import ReaderTemplate


@dataclass(frozen=True)
class BubbleMeasurement:
    item_position: int
    option: str
    intensity: float


def _window(image: np.ndarray, cx: float, cy: float, radius: float):
    height, width = image.shape[:2]
    x0 = max(0, int(np.floor(cx - radius)))
    x1 = min(width, int(np.ceil(cx + radius)) + 1)
    y0 = max(0, int(np.floor(cy - radius)))
    y1 = min(height, int(np.ceil(cy + radius)) + 1)
    if x1 <= x0 or y1 <= y0:
        return None
    patch = image[y0:y1, x0:x1].astype(np.float32)
    ys, xs = np.mgrid[y0:y1, x0:x1]
    distance_squared = (xs - cx) ** 2 + (ys - cy) ** 2
    return patch, distance_squared


def _disc_values(image: np.ndarray, cx: float, cy: float, radius: float) -> np.ndarray:
    window = _window(image, cx, cy, radius)
    if window is None:
        return np.empty(0, dtype=np.float32)
    patch, distance_squared = window
    return patch[distance_squared <= radius * radius]


def _annulus_values(image, cx: float, cy: float, inner: float, outer: float) -> np.ndarray:
    window = _window(image, cx, cy, outer)
    if window is None:
        return np.empty(0, dtype=np.float32)
    patch, distance_squared = window
    mask = (distance_squared >= inner * inner) & (distance_squared <= outer * outer)
    return patch[mask]


def measure_bubbles(
    canonical: np.ndarray, template: ReaderTemplate, config: OmrConfig
) -> tuple[BubbleMeasurement, ...]:
    measurements: list[BubbleMeasurement] = []
    for bubble in template.bubbles:
        cx, cy, radius = template.bubble_pixels(bubble)
        ink = _disc_values(canonical, cx, cy, radius * config.inner_disc_ratio)
        background = _annulus_values(
            canonical, cx, cy,
            radius * config.background_inner_ratio,
            radius * config.background_outer_ratio,
        )
        if ink.size == 0 or background.size == 0:
            # Bolha fora da area capturada. Intensidade 0.0 e a leitura honesta
            # (nao ha tinta observada) - e a §5.7 transforma "nenhuma preenchida"
            # em branco, nunca em resposta.
            measurements.append(BubbleMeasurement(bubble.item_position, bubble.option, 0.0))
            continue
        paper = float(np.median(background))
        ink_level = float(ink.mean())
        denominator = max(paper, 1.0)
        intensity = (paper - ink_level) / denominator
        measurements.append(
            BubbleMeasurement(
                item_position=bubble.item_position,
                option=bubble.option,
                intensity=float(min(1.0, max(0.0, intensity))),
            )
        )
    return tuple(measurements)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_omr_measure.py -v`
Expected: PASS nos 7 testes.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/omr_worker/measure.py tests/test_omr_measure.py
git commit -m "feat(omr): §5.4 intensidade normalizada pelo fundo local

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 7: §5.5 Limiarizar — Otsu por cartão, com guarda de cartão degenerado

**Files:**
- Create: `src/agente_ia_edu/omr_worker/threshold.py`
- Test: `tests/test_omr_threshold.py`

**Interfaces:**
- Consumes: `BubbleMeasurement` (Task 6); `OmrConfig.ambiguity_margin`,
  `OmrConfig.min_class_separation`, `OmrConfig.degenerate_fill_threshold`,
  `OmrConfig.degenerate_empty_threshold` (Task 1).
- Produces:
  - `otsu_threshold(values: Sequence[float], *, bins: int = 256) -> float`
  - `CardThresholds(otsu: float, fill_threshold: float, empty_threshold: float,
    separation: float, degenerate: bool)` — frozen.
  - `card_thresholds(values: Sequence[float], config: OmrConfig) -> CardThresholds`

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_omr_threshold.py`:

```python
"""Fase 4 (OMR) Task 7 - §5.5 do spec.

"Limiar adaptativo POR CARTAO, via Otsu sobre a distribuicao das intensidades
das bolhas daquele cartao. Caneta mais clara, scanner mais escuro ou foto
subexposta deixam de ser casos especiais."

Otsu sobre 450 numeros (90 questoes x 5 bolhas), nao sobre os pixels da imagem:
a populacao que interessa e "as bolhas DESTE cartao", e ela separa naturalmente
em duas nuvens (vazias perto de 0, marcadas perto de 1).

A guarda que o spec nao pede explicitamente mas que a "regra de nunca chutar"
exige: um cartao onde o aluno NAO MARCOU NADA tem uma nuvem so, e o Otsu, que
sempre devolve um corte, separaria ruido em "marcado" e "vazio" - inventando
respostas. Por isso `card_thresholds` mede a separacao entre as duas classes e,
abaixo de `min_class_separation`, declara o cartao degenerado e cai para o par
de limiares absolutos. Sem isso, um cartao em branco viraria 90 respostas.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.omr_worker.config import OmrConfig
from agente_ia_edu.omr_worker.threshold import card_thresholds, otsu_threshold


class Otsu(unittest.TestCase):
    def test_splits_two_clearly_separated_clouds(self):
        values = [0.02, 0.03, 0.04, 0.05] * 10 + [0.90, 0.92, 0.95, 0.97] * 3
        threshold = otsu_threshold(values)
        self.assertGreater(threshold, 0.05)
        self.assertLess(threshold, 0.90)

    def test_adapts_to_a_faint_pen(self):
        # Caneta clara: marcadas em ~0.40 em vez de ~0.95. Um limiar FIXO em 0.5
        # perderia todas; o Otsu do proprio cartao acha o corte certo.
        values = [0.03] * 40 + [0.38, 0.40, 0.42] * 3
        threshold = otsu_threshold(values)
        self.assertGreater(threshold, 0.03)
        self.assertLess(threshold, 0.38)

    def test_raises_on_empty_input(self):
        with self.assertRaises(ValueError):
            otsu_threshold([])

    def test_constant_input_returns_that_value(self):
        self.assertAlmostEqual(otsu_threshold([0.2] * 10), 0.2, places=6)


class CardThresholds(unittest.TestCase):
    def setUp(self):
        self.config = OmrConfig()

    def test_normal_card_builds_a_symmetric_ambiguity_band_around_the_centre(self):
        values = [0.02] * 40 + [0.93] * 10
        thresholds = card_thresholds(values, self.config)
        self.assertFalse(thresholds.degenerate)
        self.assertAlmostEqual(thresholds.center, (0.02 + 0.93) / 2, places=6)
        self.assertAlmostEqual(
            thresholds.fill_threshold - thresholds.center, self.config.ambiguity_margin, places=6)
        self.assertAlmostEqual(
            thresholds.center - thresholds.empty_threshold, self.config.ambiguity_margin, places=6)
        self.assertGreater(thresholds.separation, self.config.min_class_separation)

    def test_the_centre_barely_moves_when_one_smudged_bubble_appears(self):
        # A regressao que o `center` existe para impedir: com o corte cru do
        # Otsu, uma unica bolha intermediaria desloca o limiar de dezenas de
        # pontos e muda a leitura das OUTRAS 449 bolhas do cartao.
        clean = card_thresholds([0.02] * 40 + [0.93] * 10, self.config)
        smudged = card_thresholds([0.02] * 40 + [0.41] + [0.93] * 10, self.config)
        self.assertLess(abs(clean.center - smudged.center), 0.08)

    def test_a_completely_blank_card_is_degenerate_and_falls_back_to_absolutes(self):
        values = [0.01, 0.02, 0.015, 0.025] * 20  # nuvem unica de ruido
        thresholds = card_thresholds(values, self.config)
        self.assertTrue(thresholds.degenerate)
        self.assertEqual(thresholds.fill_threshold, self.config.degenerate_fill_threshold)
        self.assertEqual(thresholds.empty_threshold, self.config.degenerate_empty_threshold)
        self.assertTrue(all(v < thresholds.empty_threshold for v in values),
                        "num cartao em branco toda bolha tem que cair em VAZIA")

    def test_a_fully_marked_card_is_also_degenerate(self):
        values = [0.91, 0.93, 0.95] * 30
        thresholds = card_thresholds(values, self.config)
        self.assertTrue(thresholds.degenerate)

    def test_raises_on_empty_input(self):
        with self.assertRaises(ValueError):
            card_thresholds([], self.config)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_omr_threshold.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.omr_worker.threshold'`.

- [ ] **Step 3: Implementar**

`src/agente_ia_edu/omr_worker/threshold.py`:

```python
"""§5.5 do spec: limiar adaptativo POR CARTAO, por Otsu sobre a distribuicao das
intensidades das bolhas daquele cartao.

Sem cv2 (o `cv2.threshold(..., THRESH_OTSU)` opera sobre imagem 8-bit; aqui a
populacao e um vetor de floats), e sem scipy. NumPy puro, ~20 linhas, testavel
com numeros na mao.
"""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass

import numpy as np

from .config import OmrConfig


@dataclass(frozen=True)
class CardThresholds:
    #: O corte cru do Otsu, guardado para auditoria.
    otsu: float
    #: Centro de decisao: a MEDIA das medias das duas classes que o Otsu separou.
    #: Nao o corte do Otsu. Motivo: o corte pode cair em qualquer ponto do vazio
    #: entre as duas nuvens (todos empatam em variancia), entao ele oscila com um
    #: unico valor intermediario no cartao - uma rasura, por exemplo. Ja o ponto
    #: medio entre as duas medias praticamente nao se move, e a zona ambigua em
    #: volta dele fica onde deve ficar: exatamente entre "papel" e "tinta".
    center: float
    #: >= este valor a bolha e PREENCHIDA.
    fill_threshold: float
    #: <= este valor a bolha e VAZIA. Entre os dois: AMBIGUA (§5.6).
    empty_threshold: float
    #: distancia entre as medias das duas classes que o Otsu separou.
    separation: float
    #: True quando as duas classes nao se separam de verdade (cartao todo em
    #: branco, ou todo marcado). Nesse caso o Otsu e ruido e os limiares
    #: absolutos do `OmrConfig` assumem.
    degenerate: bool


def otsu_threshold(values: Sequence[float], *, bins: int = 256) -> float:
    array = np.asarray(list(values), dtype=np.float64)
    if array.size == 0:
        raise ValueError("otsu_threshold precisa de pelo menos uma intensidade")
    low = float(array.min())
    high = float(array.max())
    if high - low < 1e-9:
        return low

    histogram, edges = np.histogram(array, bins=bins, range=(low, high))
    centers = (edges[:-1] + edges[1:]) / 2.0
    total = float(array.size)
    weight_low = np.cumsum(histogram).astype(np.float64)
    weight_high = total - weight_low
    weighted = histogram * centers
    sum_low = np.cumsum(weighted)
    sum_total = float(weighted.sum())

    valid = (weight_low > 0) & (weight_high > 0)
    between = np.zeros_like(centers, dtype=np.float64)
    mean_low = np.zeros_like(centers, dtype=np.float64)
    mean_high = np.zeros_like(centers, dtype=np.float64)
    mean_low[valid] = sum_low[valid] / weight_low[valid]
    mean_high[valid] = (sum_total - sum_low[valid]) / weight_high[valid]
    between[valid] = weight_low[valid] * weight_high[valid] * (mean_low[valid] - mean_high[valid]) ** 2

    # Desempate pelo MEIO do platô, e não pelo primeiro índice. Num cartão real
    # as duas nuvens sao separadas por um vazio largo, e TODO corte dentro desse
    # vazio produz a mesma variancia entre classes. `np.argmax` sozinho pegaria a
    # borda do vazio (colado na nuvem das vazias), o que jogaria o limiar para
    # perto de zero e transformaria ruido em marca.
    best = float(between.max())
    tied = np.flatnonzero(between >= best - 1e-12)
    index = int(round(float(tied.mean())))
    return float(edges[index + 1])


def card_thresholds(values: Sequence[float], config: OmrConfig) -> CardThresholds:
    array = np.asarray(list(values), dtype=np.float64)
    if array.size == 0:
        raise ValueError("card_thresholds precisa das intensidades do cartao")

    otsu = otsu_threshold(array)
    below = array[array <= otsu]
    above = array[array > otsu]
    if below.size == 0 or above.size == 0:
        separation = 0.0
        center = otsu
    else:
        mean_below = float(below.mean())
        mean_above = float(above.mean())
        separation = mean_above - mean_below
        center = (mean_below + mean_above) / 2.0

    if separation < config.min_class_separation:
        # Cartao degenerado: uma nuvem so. O Otsu sempre devolve um corte, entao
        # confiar nele aqui produziria marcas inventadas num cartao em branco.
        return CardThresholds(
            otsu=otsu,
            center=center,
            fill_threshold=config.degenerate_fill_threshold,
            empty_threshold=config.degenerate_empty_threshold,
            separation=separation,
            degenerate=True,
        )

    return CardThresholds(
        otsu=otsu,
        center=center,
        fill_threshold=center + config.ambiguity_margin,
        empty_threshold=center - config.ambiguity_margin,
        separation=separation,
        degenerate=False,
    )
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_omr_threshold.py -v`
Expected: PASS nos 8 testes.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/omr_worker/threshold.py tests/test_omr_threshold.py
git commit -m "feat(omr): §5.5 Otsu por cartao com guarda de cartao degenerado

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 8: §5.6 e §5.7 — classificar cada bolha e resolver cada questão

**Files:**
- Create: `src/agente_ia_edu/omr_worker/classify.py`
- Create: `src/agente_ia_edu/omr_worker/resolve.py`
- Test: `tests/test_omr_classify_resolve.py`

**Interfaces:**
- Consumes: `BubbleMeasurement` (Task 6), `CardThresholds` (Task 7).
- Produces:
  - `classify.FILLED = "PREENCHIDA"`, `classify.EMPTY = "VAZIA"`, `classify.AMBIGUOUS = "AMBIGUA"`
  - `classify.BubbleClassification(item_position: int, option: str, intensity: float, state: str, confidence: float)` — frozen.
  - `classify.classify_bubbles(measurements, thresholds) -> tuple[BubbleClassification, ...]`
  - `resolve.RESOLVED = "RESOLVIDA"`, `resolve.BLANK = "BRANCO"`,
    `resolve.MULTI_MARK = "DUPLA_MARCACAO"`, `resolve.AMBIGUOUS = "BOLHA_AMBIGUA"`
  - `resolve.NEEDS_REVIEW_OUTCOMES: frozenset[str] = frozenset({MULTI_MARK, AMBIGUOUS})`
  - `resolve.ItemResolution(item_position: int, detected_option: str | None, outcome: str,
    confidence: float, intensities: tuple[float, ...])` — frozen.
  - `resolve.resolve_items(classifications) -> tuple[ItemResolution, ...]`

- [ ] **Step 1: Escrever o teste que falha (classificação)**

`tests/test_omr_classify_resolve.py`:

```python
"""Fase 4 (OMR) Task 8 - §5.6 e §5.7 do spec, e o principio que os governa:

"Qualquer incerteza - bolha em zona ambigua, dupla marcacao, rasura, QR
ilegivel, ArUco nao detectado - vira item na fila de conferencia (...). Um OMR
que adivinha e pior que um que pede ajuda, porque o erro dele e silencioso e
vira nota."

A tabela do §5.7, testada linha a linha:
  exatamente uma preenchida -> resposta
  nenhuma                    -> branco
  duas ou mais               -> dupla marcacao (conferencia)
  qualquer bolha ambigua     -> conferencia

Repare que "branco" e a UNICA saida automatica alem de "resposta": dupla
marcacao NAO vira "a mais escura ganha", e bolha ambigua NAO vira "provavelmente
esta". Os dois testes que provam isso sao os mais importantes do arquivo.
"""

from __future__ import annotations

import unittest

from agente_ia_edu.omr_worker.classify import (
    AMBIGUOUS,
    EMPTY,
    FILLED,
    BubbleClassification,
    classify_bubbles,
)
from agente_ia_edu.omr_worker.config import OmrConfig
from agente_ia_edu.omr_worker.measure import BubbleMeasurement
from agente_ia_edu.omr_worker.resolve import (
    AMBIGUOUS as OUTCOME_AMBIGUOUS,
    BLANK,
    MULTI_MARK,
    NEEDS_REVIEW_OUTCOMES,
    RESOLVED,
    resolve_items,
)
from agente_ia_edu.omr_worker.threshold import card_thresholds


def measurements(*intensities: float, position: int = 1) -> list[BubbleMeasurement]:
    return [BubbleMeasurement(position, option, value)
            for option, value in zip("ABCDE", intensities)]


def classifications(*intensities: float, position: int = 1) -> list[BubbleClassification]:
    config = OmrConfig()
    # cartao normal: center = (0.02 + 0.95) / 2 = 0.485, banda ambigua 0.405..0.565
    thresholds = card_thresholds([0.02] * 40 + [0.95] * 10, config)
    return list(classify_bubbles(measurements(*intensities, position=position), thresholds))


class Classification(unittest.TestCase):
    def setUp(self):
        self.config = OmrConfig()
        self.thresholds = card_thresholds([0.02] * 40 + [0.95] * 10, self.config)

    def test_above_fill_threshold_is_filled(self):
        result = classify_bubbles(measurements(0.95, 0.02, 0.02, 0.02, 0.02), self.thresholds)
        self.assertEqual(result[0].state, FILLED)
        self.assertEqual(result[1].state, EMPTY)

    def test_between_the_thresholds_is_ambiguous_never_rounded(self):
        midpoint = (self.thresholds.fill_threshold + self.thresholds.empty_threshold) / 2
        result = classify_bubbles(measurements(midpoint, 0.02, 0.02, 0.02, 0.02), self.thresholds)
        self.assertEqual(result[0].state, AMBIGUOUS)

    def test_confidence_is_higher_far_from_the_band_than_next_to_it(self):
        far = classify_bubbles(measurements(0.99, 0, 0, 0, 0), self.thresholds)[0]
        near = classify_bubbles(
            measurements(self.thresholds.fill_threshold + 1e-4, 0, 0, 0, 0), self.thresholds)[0]
        self.assertGreater(far.confidence, near.confidence)
        self.assertTrue(0.0 <= near.confidence <= 1.0)

    def test_ambiguous_bubbles_never_report_high_confidence(self):
        midpoint = (self.thresholds.fill_threshold + self.thresholds.empty_threshold) / 2
        result = classify_bubbles(measurements(midpoint, 0, 0, 0, 0), self.thresholds)[0]
        self.assertLessEqual(result.confidence, 0.5)


class Resolution(unittest.TestCase):
    def test_exactly_one_filled_becomes_the_answer(self):
        items = resolve_items(classifications(0.02, 0.02, 0.96, 0.02, 0.02))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].outcome, RESOLVED)
        self.assertEqual(items[0].detected_option, "C")

    def test_none_filled_becomes_blank_automatically(self):
        items = resolve_items(classifications(0.02, 0.02, 0.02, 0.02, 0.02))
        self.assertEqual(items[0].outcome, BLANK)
        self.assertIsNone(items[0].detected_option)
        self.assertNotIn(items[0].outcome, NEEDS_REVIEW_OUTCOMES)

    def test_two_filled_becomes_multi_mark_and_never_picks_the_darker_one(self):
        items = resolve_items(classifications(0.99, 0.02, 0.72, 0.02, 0.02))
        self.assertEqual(items[0].outcome, MULTI_MARK)
        self.assertIsNone(items[0].detected_option, "dupla marcacao NAO escolhe a mais escura")
        self.assertIn(items[0].outcome, NEEDS_REVIEW_OUTCOMES)

    def test_any_ambiguous_bubble_sends_the_item_to_human_review(self):
        items = resolve_items(classifications(0.96, 0.50, 0.02, 0.02, 0.02))
        self.assertEqual(items[0].outcome, OUTCOME_AMBIGUOUS)
        self.assertIsNone(items[0].detected_option,
                          "com bolha ambigua nao se declara resposta, nem a preenchida obvia")
        self.assertIn(items[0].outcome, NEEDS_REVIEW_OUTCOMES)

    def test_ambiguity_wins_over_multi_mark_so_the_operator_sees_everything(self):
        items = resolve_items(classifications(0.96, 0.96, 0.50, 0.02, 0.02))
        self.assertIn(items[0].outcome, NEEDS_REVIEW_OUTCOMES)

    def test_the_five_intensities_are_preserved_for_audit(self):
        items = resolve_items(classifications(0.10, 0.20, 0.96, 0.15, 0.25))
        self.assertEqual(len(items[0].intensities), 5)
        self.assertAlmostEqual(items[0].intensities[2], 0.96, places=6)
        self.assertEqual(items[0].outcome, RESOLVED)

    def test_items_are_returned_in_position_order(self):
        rows = classifications(0.96, 0, 0, 0, 0, position=2) + classifications(0, 0.96, 0, 0, 0, position=1)
        items = resolve_items(rows)
        self.assertEqual([i.item_position for i in items], [1, 2])

    def test_an_item_without_five_bubbles_raises_instead_of_reading_half_a_row(self):
        with self.assertRaises(ValueError):
            resolve_items(classifications(0.96, 0.02, 0.02, 0.02, 0.02)[:3])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_omr_classify_resolve.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.omr_worker.classify'`.

- [ ] **Step 3: Implementar `classify.py`**

`src/agente_ia_edu/omr_worker/classify.py`:

```python
"""§5.6 do spec: classificar cada bolha em preenchida, vazia ou AMBIGUA.

O terceiro estado e o coracao do subsistema. Um OMR classico so tem dois, e e
por isso que ele erra em silencio: toda bolha cai de um lado do limiar, mesmo a
que esta a um milesimo dele. Aqui a faixa de `ambiguity_margin` em volta do
limiar de Otsu do cartao e territorio de ninguem, e quem cai nela vai para a
fila de conferencia humana.

Sem cv2, sem numpy - so comparacao de floats.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .measure import BubbleMeasurement
from .threshold import CardThresholds

FILLED = "PREENCHIDA"
EMPTY = "VAZIA"
AMBIGUOUS = "AMBIGUA"


@dataclass(frozen=True)
class BubbleClassification:
    item_position: int
    option: str
    intensity: float
    state: str
    #: 0.0 a 1.0. Por construcao, toda bolha AMBIGUA fica em <= 0.5 e toda bolha
    #: decidida fica em >= 0.5 - a confianca nunca contradiz o estado.
    confidence: float


def classify_bubbles(
    measurements: Iterable[BubbleMeasurement], thresholds: CardThresholds
) -> tuple[BubbleClassification, ...]:
    band = max(thresholds.fill_threshold - thresholds.empty_threshold, 1e-6)
    middle = (thresholds.fill_threshold + thresholds.empty_threshold) / 2.0
    result: list[BubbleClassification] = []
    for measurement in measurements:
        intensity = measurement.intensity
        if intensity >= thresholds.fill_threshold:
            state = FILLED
            confidence = 0.5 + min(0.5, (intensity - thresholds.fill_threshold) / band)
        elif intensity <= thresholds.empty_threshold:
            state = EMPTY
            confidence = 0.5 + min(0.5, (thresholds.empty_threshold - intensity) / band)
        else:
            state = AMBIGUOUS
            # No centro exato da banda a confianca e 0; nas bordas, 0.5.
            confidence = max(0.0, 0.5 - abs(intensity - middle) / band * 1.0)
            confidence = min(0.5, confidence)
        result.append(
            BubbleClassification(
                item_position=measurement.item_position,
                option=measurement.option,
                intensity=intensity,
                state=state,
                confidence=float(confidence),
            )
        )
    return tuple(result)
```

- [ ] **Step 4: Implementar `resolve.py`**

`src/agente_ia_edu/omr_worker/resolve.py`:

```python
"""§5.7 do spec: resolver cada questao a partir das classificacoes das suas cinco
bolhas.

"exatamente uma preenchida -> resposta; nenhuma -> branco; duas ou mais -> dupla
marcacao; qualquer bolha ambigua -> conferencia humana."

Uma unica regra explica os quatro casos: so vira resposta o que nao tem duvida
nenhuma. Dupla marcacao NAO escolhe a mais escura. Bolha ambigua NAO e
arredondada, nem quando ha uma preenchida obvia ao lado dela - porque a ambigua
pode ser uma rasura mal apagada, e o aluno pode ter mudado de ideia.

Sem cv2, sem numpy.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass

from .classify import AMBIGUOUS as BUBBLE_AMBIGUOUS, FILLED, BubbleClassification

RESOLVED = "RESOLVIDA"
BLANK = "BRANCO"
MULTI_MARK = "DUPLA_MARCACAO"
AMBIGUOUS = "BOLHA_AMBIGUA"

#: Os dois desfechos que viram item na fila de conferencia humana.
NEEDS_REVIEW_OUTCOMES = frozenset({MULTI_MARK, AMBIGUOUS})


@dataclass(frozen=True)
class ItemResolution:
    item_position: int
    #: Preenchido SOMENTE quando `outcome == RESOLVED`. Em BRANCO,
    #: DUPLA_MARCACAO e BOLHA_AMBIGUA e None - o leitor nunca chuta.
    detected_option: str | None
    outcome: str
    confidence: float
    #: As cinco intensidades, na ordem A-E. §3 do spec: "Guardar as cinco
    #: intensidades medidas, e nao so a conclusao, e o que permite auditar uma
    #: leitura contestada sem reprocessar a imagem."
    intensities: tuple[float, ...]


def resolve_items(
    classifications: Iterable[BubbleClassification],
) -> tuple[ItemResolution, ...]:
    rows: dict[int, list[BubbleClassification]] = {}
    for classification in classifications:
        rows.setdefault(classification.item_position, []).append(classification)

    resolutions: list[ItemResolution] = []
    for position in sorted(rows):
        row = sorted(rows[position], key=lambda c: c.option)
        if len(row) != 5:
            raise ValueError(
                f"questao {position} chegou com {len(row)} bolhas em vez de 5. "
                "Uma linha incompleta nao e lida pela metade."
            )
        intensities = tuple(c.intensity for c in row)
        confidence = min(c.confidence for c in row)
        ambiguous = [c for c in row if c.state == BUBBLE_AMBIGUOUS]
        filled = [c for c in row if c.state == FILLED]

        if ambiguous:
            outcome, option = AMBIGUOUS, None
        elif len(filled) == 1:
            outcome, option = RESOLVED, filled[0].option
        elif not filled:
            outcome, option = BLANK, None
        else:
            outcome, option = MULTI_MARK, None

        resolutions.append(
            ItemResolution(
                item_position=position,
                detected_option=option,
                outcome=outcome,
                confidence=float(confidence),
                intensities=intensities,
            )
        )
    return tuple(resolutions)
```

- [ ] **Step 5: Rodar e ver passar**

Run: `python -m pytest tests/test_omr_classify_resolve.py -v`
Expected: PASS nos 12 testes.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/omr_worker/classify.py src/agente_ia_edu/omr_worker/resolve.py \
        tests/test_omr_classify_resolve.py
git commit -m "feat(omr): §5.6/§5.7 classificacao com zona ambigua e resolucao por questao

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 9: §8.2 — gerador de cartões sintéticos e degradações determinísticas

**Files:**
- Create: `src/agente_ia_edu/omr_worker/synthetic.py`
- Test: `tests/test_omr_synthetic.py`

**Interfaces:**
- Consumes: `ReaderTemplate` (Task 2), `build_template`/`CardLayoutSpec` da Fase 1,
  `draw_marker` (Task 4), `encode_qr` (Task 5).
- Produces:
  - `build_template(n_items: int = 90, *, width: int = 1240) -> ReaderTemplate`
  - `render_card(view: ReaderTemplate, *, qr_payload: str, answers: Mapping[int, str | Sequence[str]], pen_darkness: int = 30, fill_ratio: float = 0.80) -> np.ndarray`
  - `apply_perspective(image, rng, *, magnitude=0.045, pad=140) -> np.ndarray`
  - `apply_noise(image, rng, *, sigma=9.0) -> np.ndarray`
  - `apply_blur(image, *, ksize=5) -> np.ndarray`
  - `apply_shadow(image, *, strength=0.45) -> np.ndarray`
  - `apply_underexposure(image, *, gain=0.55) -> np.ndarray`
  - `DEGRADATION_PROFILES: dict[str, tuple[str, ...]]`
  - `degrade(image, rng, profile: str) -> np.ndarray`
  - `SEED: int = 20260929`

O gerador sintético **não** usa ReportLab, mas usa a **geometria real da Fase 1**
(`simulado_card.layout.build_template`): ele desenha as bolhas exatamente onde o cartão
impresso as tem. Só a renderização é simplificada. Isso é o que dá valor ao §8.2 — se o
layout do cartão mudar e a leitura parar de funcionar com ele, a suíte reprova.
A fidelidade de impressão de verdade — papel, tinta, mão trêmida, câmera — não é
simulável, e é exatamente o que o §8.3 existe para cobrir (Task 20).

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_omr_synthetic.py`:

```python
"""Fase 4 (OMR) Task 9 - infraestrutura do §8.2 do spec:

"Renderizar o cartao, preencher bolhas programaticamente, aplicar transformacoes
de perspectiva, ruido, borrao, sombra e subexposicao, e exigir leitura correta.
Cobre regressao de forma barata e deterministica."

Este arquivo testa o GERADOR (o cartao sai como deveria sair). Quem testa o
LEITOR contra os cartoes degradados e tests/test_omr_pipeline_synthetic.py.

Semente fixa: `synthetic.SEED`. Um teste de OMR que muda de resultado entre
execucoes nao e um teste, e um sorteio.
"""

from __future__ import annotations

import unittest

import numpy as np

from agente_ia_edu.omr_worker import synthetic


class SyntheticTemplate(unittest.TestCase):
    def test_builds_a_90_question_template_with_450_bubbles(self):
        template = synthetic.build_template(n_items=90)
        self.assertEqual(len(template.bubbles), 450)
        self.assertEqual(template.item_positions, tuple(range(1, 91)))

    def test_it_is_the_real_generator_geometry_not_an_invented_grid(self):
        from agente_ia_edu.simulado_card.layout import build_template as real_build
        self.assertEqual(synthetic.build_template(n_items=90).card, real_build())

    def test_bubbles_never_overlap_each_other(self):
        template = synthetic.build_template(n_items=90)
        centres = [template.bubble_pixels(b) for b in template.bubbles]
        for index, (cx, cy, r) in enumerate(centres):
            for other_cx, other_cy, other_r in centres[index + 1:]:
                distance = ((cx - other_cx) ** 2 + (cy - other_cy) ** 2) ** 0.5
                self.assertGreater(distance, r + other_r,
                                   "bolhas sobrepostas invalidariam a medicao do §5.4")


class SyntheticRendering(unittest.TestCase):
    def setUp(self):
        self.template = synthetic.build_template(n_items=9)

    def test_renders_a_grayscale_card_at_canonical_size(self):
        card = synthetic.render_card(self.template, qr_payload="tok-1", answers={})
        self.assertEqual(card.shape, (self.template.canonical_height, self.template.canonical_width))
        self.assertEqual(card.dtype, np.uint8)

    def test_a_filled_bubble_is_darker_than_its_neighbours(self):
        card = synthetic.render_card(self.template, qr_payload="tok-1", answers={3: "C"})
        marked = next(b for b in self.template.bubbles_for(3) if b.option == "C")
        clean = next(b for b in self.template.bubbles_for(3) if b.option == "A")
        marked_mean = self._disc_mean(card, marked)
        clean_mean = self._disc_mean(card, clean)
        self.assertLess(marked_mean, clean_mean - 100)

    def test_multiple_options_per_item_are_supported(self):
        card = synthetic.render_card(self.template, qr_payload="tok-1", answers={4: ["A", "D"]})
        for option in ("A", "D"):
            bubble = next(b for b in self.template.bubbles_for(4) if b.option == option)
            self.assertLess(self._disc_mean(card, bubble), 120)

    def test_pen_darkness_controls_how_faint_the_mark_is(self):
        dark = synthetic.render_card(self.template, qr_payload="t", answers={1: "B"}, pen_darkness=20)
        faint = synthetic.render_card(self.template, qr_payload="t", answers={1: "B"}, pen_darkness=140)
        bubble = next(b for b in self.template.bubbles_for(1) if b.option == "B")
        self.assertLess(self._disc_mean(dark, bubble), self._disc_mean(faint, bubble))

    def _disc_mean(self, card, bubble) -> float:
        cx, cy, r = self.template.bubble_pixels(bubble)
        ys, xs = np.mgrid[0:card.shape[0], 0:card.shape[1]]
        mask = (xs - cx) ** 2 + (ys - cy) ** 2 <= (r * 0.6) ** 2
        return float(card[mask].mean())


class Degradations(unittest.TestCase):
    def setUp(self):
        self.template = synthetic.build_template(n_items=9)
        self.card = synthetic.render_card(self.template, qr_payload="tok-1", answers={1: "A"})

    def test_perspective_changes_the_canvas_and_keeps_uint8(self):
        rng = np.random.default_rng(synthetic.SEED)
        warped = synthetic.apply_perspective(self.card, rng)
        self.assertEqual(warped.dtype, np.uint8)
        self.assertGreater(warped.shape[0], self.card.shape[0])

    def test_noise_changes_pixels_without_changing_the_shape(self):
        rng = np.random.default_rng(synthetic.SEED)
        noisy = synthetic.apply_noise(self.card, rng)
        self.assertEqual(noisy.shape, self.card.shape)
        self.assertFalse(np.array_equal(noisy, self.card))

    def test_underexposure_darkens_the_paper(self):
        dark = synthetic.apply_underexposure(self.card)
        self.assertLess(float(dark.mean()), float(self.card.mean()))

    def test_shadow_darkens_one_side_more_than_the_other(self):
        shaded = synthetic.apply_shadow(self.card)
        half = shaded.shape[1] // 2
        self.assertLess(float(shaded[:, half:].mean()), float(shaded[:, :half].mean()))

    def test_blur_reduces_local_contrast(self):
        blurred = synthetic.apply_blur(self.card)
        self.assertLess(float(blurred.std()), float(self.card.std()))

    def test_every_declared_profile_runs_and_is_reproducible(self):
        for profile in synthetic.DEGRADATION_PROFILES:
            first = synthetic.degrade(self.card, np.random.default_rng(synthetic.SEED), profile)
            second = synthetic.degrade(self.card, np.random.default_rng(synthetic.SEED), profile)
            self.assertTrue(np.array_equal(first, second),
                            f"o perfil {profile} precisa ser deterministico sob a mesma semente")

    def test_unknown_profile_raises(self):
        with self.assertRaises(KeyError):
            synthetic.degrade(self.card, np.random.default_rng(synthetic.SEED), "nao-existe")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_omr_synthetic.py -v`
Expected: FAIL — `ImportError: cannot import name 'synthetic'`.

- [ ] **Step 3: Implementar**

`src/agente_ia_edu/omr_worker/synthetic.py`:

```python
"""§8.2 do spec: cartoes sinteticos deterministicos para testar o leitor.

MODULO COM `import cv2` - so pode existir dentro de `omr_worker/` (§2.3).

Desenha o cartao A PARTIR DO TEMPLATE, e nao a partir do gerador ReportLab da
Fase 1. Duas razoes: (1) o template E o contrato entre gerador e leitor (§2.1),
entao testar contra ele testa exatamente a interface que importa; (2) mantem
todo o §8.2 dentro das dependencias do worker, sem acoplar a suite a assinatura
nenhuma do gerador.

O que este arquivo NAO cobre, e por que o §8.3 existe: papel real, tinta real,
mao tremida, camera de celular real, iluminacao de sala real. Cartao sintetico
cobre REGRESSAO de forma barata; ele nao substitui o aceite fisico.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import cv2
import numpy as np

from .align import draw_marker
from .identify import encode_qr
from .template import CANONICAL_WIDTH, ReaderTemplate
from ..simulado_card.layout import CardLayoutSpec, build_template as build_card_template

#: Semente unica de todo o §8.2. Teste de OMR que sorteia nao e teste.
SEED = 20260929

_SYNTHETIC_WIDTH = 1240   # ~150 DPI no retangulo entre os marcadores
_MARKER_PX = 80
_PAPER = 252              # papel real nao e 255
_PRINT_INK = 60           # cinza do anel impresso da bolha
OPTIONS = ("A", "B", "C", "D", "E")


def build_template(n_items: int = 90, *, width: int = _SYNTHETIC_WIDTH) -> ReaderTemplate:
    """Vista em pixels do template REAL da Fase 1 - nao de uma grade inventada.

    Testar contra a geometria de verdade e o ponto: se o layout do cartao mudar,
    e a leitura deixar de funcionar com ele, o §8.2 tem que reprovar.
    """
    spec = CardLayoutSpec() if n_items == 90 else CardLayoutSpec(
        item_count=n_items, rows_per_column=max(1, (n_items + 2) // 3)
    )
    return ReaderTemplate.from_card(build_card_template(spec), width=width)


def _paste(card: np.ndarray, patch: np.ndarray, cx: float, cy: float) -> None:
    """Cola `patch` centrado em (cx, cy), recortando o que cair fora do quadro.
    Os centros dos marcadores ficam NOS cantos do retangulo canonico, entao
    metade de cada marcador fica fora por construcao."""
    side = patch.shape[0]
    x0 = int(round(cx - side / 2))
    y0 = int(round(cy - side / 2))
    sx0, sy0 = max(0, x0), max(0, y0)
    sx1 = min(card.shape[1], x0 + side)
    sy1 = min(card.shape[0], y0 + side)
    if sx1 <= sx0 or sy1 <= sy0:
        return
    card[sy0:sy1, sx0:sx1] = patch[sy0 - y0:sy1 - y0, sx0 - x0:sx1 - x0]


def render_card(
    view: ReaderTemplate,
    *,
    qr_payload: str,
    answers: Mapping[int, str | Sequence[str]],
    pen_darkness: int = 30,
    fill_ratio: float = 0.80,
) -> np.ndarray:
    card = np.full((view.canonical_height, view.canonical_width), _PAPER, dtype=np.uint8)

    for marker_id, (cx, cy) in zip(view.aruco_ids, view.aruco_corners_pixels()):
        _paste(card, draw_marker(marker_id, _MARKER_PX, dictionary=view.aruco_dictionary), cx, cy)

    x, y, side, _ = view.qr_region_pixels()
    _paste(card, encode_qr(qr_payload, side), x + side / 2, y + side / 2)

    marked: dict[int, set[str]] = {}
    for position, value in answers.items():
        options = {value} if isinstance(value, str) else set(value)
        marked[int(position)] = {option.upper() for option in options}

    for bubble in view.bubbles:
        cx, cy, radius = view.bubble_pixels(bubble)
        centre = (int(round(cx)), int(round(cy)))
        cv2.circle(card, centre, int(round(radius)), _PRINT_INK, 2, lineType=cv2.LINE_AA)
        if bubble.option in marked.get(bubble.item_position, set()):
            cv2.circle(card, centre, int(round(radius * fill_ratio)),
                       int(pen_darkness), -1, lineType=cv2.LINE_AA)
    return card


def apply_perspective(
    image: np.ndarray, rng: np.random.Generator, *, magnitude: float = 0.045, pad: int = 140
) -> np.ndarray:
    height, width = image.shape[:2]
    source = np.array([[0, 0], [width, 0], [width, height], [0, height]], dtype=np.float32)
    jitter = rng.uniform(-magnitude, magnitude, size=(4, 2)) * np.array([width, height])
    destination = (source + jitter + np.array([pad / 2, pad / 2], dtype=np.float32)).astype(np.float32)
    matrix = cv2.getPerspectiveTransform(source, destination)
    return cv2.warpPerspective(
        image, matrix, (width + pad, height + pad),
        borderMode=cv2.BORDER_CONSTANT, borderValue=_PAPER,
    )


def apply_noise(image: np.ndarray, rng: np.random.Generator, *, sigma: float = 9.0) -> np.ndarray:
    noise = rng.normal(0.0, sigma, size=image.shape)
    return np.clip(image.astype(np.float32) + noise, 0, 255).astype(np.uint8)


def apply_blur(image: np.ndarray, *, ksize: int = 5) -> np.ndarray:
    return cv2.GaussianBlur(image, (ksize, ksize), 0)


def apply_shadow(image: np.ndarray, *, strength: float = 0.45) -> np.ndarray:
    """Gradiente da esquerda (claro) para a direita (escuro) - a sombra tipica do
    braco de quem fotografa a folha sobre a mesa."""
    height, width = image.shape[:2]
    ramp = np.linspace(1.0, 1.0 - strength, width, dtype=np.float32)
    return np.clip(image.astype(np.float32) * ramp[None, :], 0, 255).astype(np.uint8)


def apply_underexposure(image: np.ndarray, *, gain: float = 0.55) -> np.ndarray:
    return np.clip(image.astype(np.float32) * gain, 0, 255).astype(np.uint8)


DEGRADATION_PROFILES: dict[str, tuple[str, ...]] = {
    "scanner_limpo": (),
    "scanner_ruidoso": ("noise",),
    "celular_bom": ("perspective", "noise"),
    "celular_sombra": ("perspective", "shadow", "noise"),
    "celular_ruim": ("perspective", "blur", "shadow", "noise"),
    "celular_subexposto": ("perspective", "underexposure", "noise"),
    "pior_caso": ("perspective", "blur", "shadow", "underexposure", "noise"),
}


def degrade(image: np.ndarray, rng: np.random.Generator, profile: str) -> np.ndarray:
    steps = DEGRADATION_PROFILES[profile]
    result = image
    for step in steps:
        if step == "perspective":
            result = apply_perspective(result, rng)
        elif step == "noise":
            result = apply_noise(result, rng)
        elif step == "blur":
            result = apply_blur(result)
        elif step == "shadow":
            result = apply_shadow(result)
        elif step == "underexposure":
            result = apply_underexposure(result)
        else:  # pragma: no cover - DEGRADATION_PROFILES e fechado
            raise ValueError(f"passo de degradacao desconhecido: {step}")
    return result
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_omr_synthetic.py -v`
Expected: PASS nos 13 testes.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/omr_worker/synthetic.py tests/test_omr_synthetic.py
git commit -m "test(omr): §8.2 gerador de cartao sintetico e degradacoes deterministicas

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 10: `pipeline.py` — os sete passos juntos, e a suíte completa do §8.2

**Files:**
- Create: `src/agente_ia_edu/omr_worker/pipeline.py`
- Test: `tests/test_omr_pipeline_synthetic.py`

**Interfaces:**
- Consumes: `rasterize` (Task 3), `align_to_template`/`AlignmentResult` (Task 4),
  `read_qr_token` (Task 5), `measure_bubbles` (Task 6), `card_thresholds` (Task 7),
  `classify_bubbles` (Task 8), `resolve_items`/`NEEDS_REVIEW_OUTCOMES` (Task 8),
  `ReaderTemplate` (Task 2), `OmrConfig` (Task 1), `synthetic` (Task 9, só no teste).
- Produces:
  - `SCAN_PROCESSED = "PROCESSED"`, `SCAN_NEEDS_REVIEW = "NEEDS_REVIEW"`, `SCAN_FAILED = "FAILED"`
  - `ScanReading(status: str, qr_token: str | None, failure_reason: str | None,
    resolutions: tuple[ItemResolution, ...], canonical_image: np.ndarray | None,
    thresholds: CardThresholds | None)` — frozen.
  - `read_image(gray: np.ndarray, template: ReaderTemplate, config: OmrConfig) -> ScanReading`
  - `read_scan(path: Path, template: ReaderTemplate, config: OmrConfig) -> ScanReading`

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_omr_pipeline_synthetic.py`:

```python
"""Fase 4 (OMR) Task 10 - o §8.2 do spec inteiro: "renderizar o cartao, preencher
bolhas programaticamente, aplicar transformacoes de perspectiva, ruido, borrao,
sombra e subexposicao, e exigir leitura correta."

Sete perfis de degradacao (synthetic.DEGRADATION_PROFILES), semente fixa, 90
questoes por cartao. A exigencia nao e "quase tudo certo": e que NENHUMA resposta
auto-resolvida divirja do gabarito usado para preencher o cartao. Item que o
leitor nao conseguir decidir pode - e deve - ir para conferencia; o que ele NAO
pode e decidir errado. Essa assimetria e o §5 do spec em forma de assert.
"""

from __future__ import annotations

import unittest

import numpy as np

from agente_ia_edu.omr_worker import synthetic
from agente_ia_edu.omr_worker.config import OmrConfig
from agente_ia_edu.omr_worker.pipeline import (
    SCAN_FAILED,
    SCAN_NEEDS_REVIEW,
    SCAN_PROCESSED,
    read_image,
)
from agente_ia_edu.omr_worker.resolve import BLANK, MULTI_MARK, NEEDS_REVIEW_OUTCOMES, RESOLVED

TOKEN = "d41d8cd98f00b204e9800998ecf8427e"
OPTIONS = "ABCDE"


def answer_key(n_items: int, rng: np.random.Generator) -> dict[int, str]:
    return {position: OPTIONS[int(rng.integers(0, 5))] for position in range(1, n_items + 1)}


class PipelineOnSyntheticCards(unittest.TestCase):
    def setUp(self):
        self.config = OmrConfig()
        self.template = synthetic.build_template(n_items=90)
        self.rng = np.random.default_rng(synthetic.SEED)
        self.answers = answer_key(90, self.rng)
        self.card = synthetic.render_card(
            self.template, qr_payload=TOKEN, answers=self.answers)

    def test_clean_card_is_read_completely_and_correctly(self):
        reading = read_image(self.card, self.template, self.config)
        self.assertEqual(reading.status, SCAN_PROCESSED)
        self.assertEqual(reading.qr_token, TOKEN)
        self.assertEqual(len(reading.resolutions), 90)
        for resolution in reading.resolutions:
            self.assertEqual(resolution.outcome, RESOLVED)
            self.assertEqual(resolution.detected_option, self.answers[resolution.item_position])

    def test_every_degradation_profile_never_produces_a_wrong_answer(self):
        for profile in synthetic.DEGRADATION_PROFILES:
            with self.subTest(profile=profile):
                rng = np.random.default_rng(synthetic.SEED)
                degraded = synthetic.degrade(self.card, rng, profile)
                reading = read_image(degraded, self.template, self.config)
                self.assertNotEqual(
                    reading.status, SCAN_FAILED,
                    f"perfil {profile} falhou o alinhamento: {reading.failure_reason}")
                self.assertEqual(reading.qr_token, TOKEN)
                wrong = [
                    (r.item_position, r.detected_option, self.answers[r.item_position])
                    for r in reading.resolutions
                    if r.outcome == RESOLVED
                    and r.detected_option != self.answers[r.item_position]
                ]
                self.assertEqual(wrong, [], f"perfil {profile} produziu resposta ERRADA: {wrong}")

    def test_degradation_keeps_the_human_review_rate_low_on_realistic_profiles(self):
        # Ir para conferencia e sempre seguro, mas mandar metade do cartao para
        # conferencia torna o sistema inutil na pratica. Teto explicito.
        for profile, max_review_rate in (("scanner_limpo", 0.0),
                                         ("scanner_ruidoso", 0.02),
                                         ("celular_bom", 0.05),
                                         ("celular_sombra", 0.10)):
            with self.subTest(profile=profile):
                rng = np.random.default_rng(synthetic.SEED)
                degraded = synthetic.degrade(self.card, rng, profile)
                reading = read_image(degraded, self.template, self.config)
                review = [r for r in reading.resolutions if r.outcome in NEEDS_REVIEW_OUTCOMES]
                self.assertLessEqual(len(review) / 90, max_review_rate)

    def test_a_faint_pen_is_read_because_the_threshold_is_per_card(self):
        faint = synthetic.render_card(
            self.template, qr_payload=TOKEN, answers=self.answers, pen_darkness=150)
        reading = read_image(faint, self.template, self.config)
        resolved = [r for r in reading.resolutions if r.outcome == RESOLVED]
        self.assertGreaterEqual(len(resolved), 85)
        for resolution in resolved:
            self.assertEqual(resolution.detected_option, self.answers[resolution.item_position])

    def test_an_untouched_card_reads_as_90_blanks_never_as_invented_answers(self):
        blank_card = synthetic.render_card(self.template, qr_payload=TOKEN, answers={})
        reading = read_image(blank_card, self.template, self.config)
        self.assertEqual(reading.status, SCAN_PROCESSED)
        self.assertEqual([r.outcome for r in reading.resolutions], [BLANK] * 90)

    def test_double_marking_sends_the_scan_to_review_and_declares_no_answer(self):
        answers = dict(self.answers)
        answers[7] = ["A", "D"]
        card = synthetic.render_card(self.template, qr_payload=TOKEN, answers=answers)
        reading = read_image(card, self.template, self.config)
        self.assertEqual(reading.status, SCAN_NEEDS_REVIEW)
        item = next(r for r in reading.resolutions if r.item_position == 7)
        self.assertEqual(item.outcome, MULTI_MARK)
        self.assertIsNone(item.detected_option)

    def test_a_half_filled_bubble_goes_to_review_instead_of_being_rounded(self):
        card = self.card.copy()
        bubble = next(b for b in self.template.bubbles_for(12)
                      if b.option != self.answers[12])
        cx, cy, radius = self.template.bubble_pixels(bubble)
        ys, xs = np.mgrid[0:card.shape[0], 0:card.shape[1]]
        mask = (xs - cx) ** 2 + (ys - cy) ** 2 <= (radius * 0.8) ** 2
        card[mask] = 150  # rasura mal apagada: nem papel, nem tinta
        reading = read_image(card, self.template, self.config)
        item = next(r for r in reading.resolutions if r.item_position == 12)
        self.assertIn(item.outcome, NEEDS_REVIEW_OUTCOMES)
        self.assertIsNone(item.detected_option)

    def test_missing_aruco_fails_the_scan_with_a_readable_reason(self):
        damaged = self.card.copy()
        cx, cy = self.template.aruco_corners_pixels()[1]
        damaged[int(cy) - 90:int(cy) + 90, int(cx) - 90:int(cx) + 90] = 252
        reading = read_image(damaged, self.template, self.config)
        self.assertEqual(reading.status, SCAN_FAILED)
        self.assertIn("ARUCO_NAO_DETECTADO", reading.failure_reason)
        self.assertEqual(reading.resolutions, ())

    def test_unreadable_qr_fails_the_scan_and_reads_no_answers(self):
        card = self.card.copy()
        x, y, width, height = self.template.qr_region_pixels()
        card[y:y + height, x:x + width] = 252
        reading = read_image(card, self.template, self.config)
        self.assertEqual(reading.status, SCAN_FAILED)
        self.assertIn("QR_ILEGIVEL", reading.failure_reason)
        self.assertEqual(reading.resolutions, ())

    def test_the_whole_suite_is_reproducible(self):
        first = read_image(self.card, self.template, self.config)
        second = read_image(self.card, self.template, self.config)
        self.assertEqual([r.detected_option for r in first.resolutions],
                         [r.detected_option for r in second.resolutions])


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_omr_pipeline_synthetic.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.omr_worker.pipeline'`.

- [ ] **Step 3: Implementar**

`src/agente_ia_edu/omr_worker/pipeline.py`:

```python
"""Os sete passos do §5 do spec, em ordem, num objeto de resultado.

    1. Rasterizar   -> raster.rasterize
    2. Alinhar      -> align.align_to_template
    3. Identificar  -> identify.read_qr_token
    4. Medir        -> measure.measure_bubbles
    5. Limiarizar   -> threshold.card_thresholds
    6. Classificar  -> classify.classify_bubbles
    7. Resolver     -> resolve.resolve_items

Este modulo nao fala com banco, nao escreve arquivo e nao tem efeito colateral.
E uma funcao pura de (imagem, template, config) para `ScanReading`, e e isso que
permite ao §8.2 exercitar o leitor inteiro sem subir nada.

Os dois desfechos de FALHA (ArUco e QR) abortam o pipeline ANTES de medir
qualquer bolha, e devolvem `resolutions=()`. Sem cartao alinhado nao existe
medida confiavel; sem QR nao existe aluno a quem atribuir a leitura. O §5 e
explicito: "Falhas de alinhamento ou de QR marcam o scan como FAILED com
failure_reason legivel, e o operador reenvia ou digita manualmente."
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import numpy as np

from .align import align_to_template
from .classify import classify_bubbles
from .config import OmrConfig
from .identify import read_qr_token
from .measure import measure_bubbles
from .raster import RasterError, rasterize
from .resolve import NEEDS_REVIEW_OUTCOMES, ItemResolution, resolve_items
from .template import ReaderTemplate
from .threshold import CardThresholds, card_thresholds

SCAN_PROCESSED = "PROCESSED"
SCAN_NEEDS_REVIEW = "NEEDS_REVIEW"
SCAN_FAILED = "FAILED"


@dataclass(frozen=True)
class ScanReading:
    status: str
    qr_token: str | None
    failure_reason: str | None
    resolutions: tuple[ItemResolution, ...]
    canonical_image: np.ndarray | None
    thresholds: CardThresholds | None


def _failed(reason: str) -> ScanReading:
    return ScanReading(
        status=SCAN_FAILED, qr_token=None, failure_reason=reason,
        resolutions=(), canonical_image=None, thresholds=None,
    )


def read_image(gray: np.ndarray, template: ReaderTemplate, config: OmrConfig) -> ScanReading:
    alignment = align_to_template(gray, template)          # §5.2
    if alignment.image is None:
        return _failed(alignment.failure_reason or "ALINHAMENTO_FALHOU")

    canonical = alignment.image
    qr = read_qr_token(canonical, template)                # §5.3
    if qr.token is None:
        return _failed(qr.failure_reason or "QR_ILEGIVEL")

    measurements = measure_bubbles(canonical, template, config)          # §5.4
    thresholds = card_thresholds([m.intensity for m in measurements], config)  # §5.5
    classifications = classify_bubbles(measurements, thresholds)         # §5.6
    resolutions = resolve_items(classifications)                         # §5.7

    needs_review = any(r.outcome in NEEDS_REVIEW_OUTCOMES for r in resolutions)
    return ScanReading(
        status=SCAN_NEEDS_REVIEW if needs_review else SCAN_PROCESSED,
        qr_token=qr.token,
        failure_reason=None,
        resolutions=resolutions,
        canonical_image=canonical,
        thresholds=thresholds,
    )


def read_scan(path: Path, template: ReaderTemplate, config: OmrConfig) -> ScanReading:
    """Le UM cartao de um arquivo. O arquivo pode ser PDF de uma pagina ou
    imagem; um lote multi-pagina e quebrado em um scan por pagina ANTES de
    chegar aqui (cada pagina e um `answer_card_scan` proprio, §4.4 do spec)."""
    try:
        pages = rasterize(path, dpi=config.raster_dpi)      # §5.1
    except RasterError as exc:
        return _failed(f"ARQUIVO_ILEGIVEL: {exc}")
    if len(pages) != 1:
        return _failed(
            f"ARQUIVO_COM_MULTIPLAS_PAGINAS: {len(pages)} paginas. "
            "Cada cartao precisa ser um scan proprio; separe o lote antes de enfileirar."
        )
    return read_image(pages[0], template, config)
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_omr_pipeline_synthetic.py -v`
Expected: PASS nos 10 testes (o segundo com 7 subtests).

> Se algum perfil de degradação reprovar por taxa de conferência acima do teto, o ajuste
> permitido é em `OmrConfig` (`ambiguity_margin`, `inner_disc_ratio`,
> `background_*_ratio`) — **nunca** relaxar o assert de "nenhuma resposta errada".
> Registre no commit qual número mudou e por quê.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/omr_worker/pipeline.py tests/test_omr_pipeline_synthetic.py
git commit -m "feat(omr): pipeline dos sete passos do §5 + suite sintetica do §8.2

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 11: Recortes de bolha no `MaterialStorage` — caminho no core, escrita no worker

**Files:**
- Create: `src/agente_ia_edu/services/omr_artifacts.py` (core, sem cv2)
- Create: `src/agente_ia_edu/omr_worker/crops.py` (worker, com cv2)
- Test: `tests/test_omr_crops.py`

**Interfaces:**
- Consumes: `MaterialStorage` (`src/agente_ia_edu/services/material_storage.py:31-52`),
  `ReaderTemplate` (Task 2), `ScanReading` (Task 10).
- Produces (core, `services/omr_artifacts.py`):
  - `aligned_card_path(storage: MaterialStorage, image_hash: str) -> Path`
  - `item_crop_path(storage: MaterialStorage, image_hash: str, item_position: int) -> Path`
  - `ALIGNED_FILENAME = "omr_aligned.png"`, `CROP_FILENAME_TEMPLATE = "omr_item_{position:03d}.png"`
- Produces (worker, `omr_worker/crops.py`):
  - `write_aligned_card(storage, image_hash, canonical) -> Path`
  - `write_item_crop(storage, image_hash, canonical, template, item_position, *, pad_ratio=0.9) -> Path`

**Por que o caminho é derivado e não guardado numa coluna:** o §3 do spec não
prevê nenhuma coluna para artefato derivado (nem em `answer_card_scans`, nem em
`answer_card_marks`). Como `MaterialStorage` já é endereçado por conteúdo, o par
(`answer_card_scans.image_hash`, `item_position`) determina o caminho sem
ambiguidade e sem migração: o worker escreve nele, a rota do core lê dele, e
reprocessar o mesmo arquivo sobrescreve o mesmo recorte em vez de acumular lixo.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_omr_crops.py`:

```python
"""Fase 4 (OMR) Task 11 - o recorte da bolha que o operador ve na fila de
conferencia (§5 do spec: "vira item na fila de conferencia, COM O RECORTE DA
IMAGEM exibido para o operador decidir").

Dois lados, deliberadamente separados:
  - `services/omr_artifacts.py` (CORE) so calcula caminhos - pathlib puro, sem
    cv2, porque e o core que serve o PNG na rota de conferencia;
  - `omr_worker/crops.py` (WORKER) escreve as imagens - com cv2, porque so ele
    pode.
Os dois derivam o caminho da MESMA funcao, entao escrita e leitura nunca podem
divergir.

Reaproveita `MaterialStorage` (services/material_storage.py), o storage local
endereçado por conteudo que ja existe - o §3 do spec e explicito sobre isso e a
constraint global proibe storage novo.
"""

from __future__ import annotations

import tempfile
import unittest
from pathlib import Path

import numpy as np

from agente_ia_edu.omr_worker import synthetic
from agente_ia_edu.omr_worker.crops import write_aligned_card, write_item_crop
from agente_ia_edu.services.material_storage import MaterialStorage
from agente_ia_edu.services.omr_artifacts import aligned_card_path, item_crop_path

IMAGE_HASH = "a1b2c3d4e5f60718293a4b5c6d7e8f90a1b2c3d4e5f60718293a4b5c6d7e8f90"


class ArtifactPaths(unittest.TestCase):
    def test_paths_live_under_the_content_addressed_folder_of_the_scan(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = MaterialStorage(root=Path(tmp))
            path = item_crop_path(storage, IMAGE_HASH, 7)
            self.assertEqual(path.parent, storage.managed_path(IMAGE_HASH, "x").parent)
            self.assertEqual(path.name, "omr_item_007.png")

    def test_the_aligned_card_has_its_own_stable_name(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = MaterialStorage(root=Path(tmp))
            self.assertEqual(aligned_card_path(storage, IMAGE_HASH).name, "omr_aligned.png")

    def test_the_path_is_a_pure_function_of_hash_and_position(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = MaterialStorage(root=Path(tmp))
            self.assertEqual(item_crop_path(storage, IMAGE_HASH, 12),
                             item_crop_path(storage, IMAGE_HASH, 12))
            self.assertNotEqual(item_crop_path(storage, IMAGE_HASH, 12),
                                item_crop_path(storage, IMAGE_HASH, 13))


class CropWriting(unittest.TestCase):
    def setUp(self):
        self.template = synthetic.build_template(n_items=18)
        self.card = synthetic.render_card(self.template, qr_payload="t", answers={5: "B"})

    def test_writes_a_readable_png_for_the_item_row(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = MaterialStorage(root=Path(tmp))
            path = write_item_crop(storage, IMAGE_HASH, self.card, self.template, 5)
            self.assertTrue(path.is_file())
            self.assertGreater(path.stat().st_size, 0)

    def test_the_crop_contains_all_five_bubbles_of_the_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = MaterialStorage(root=Path(tmp))
            path = write_item_crop(storage, IMAGE_HASH, self.card, self.template, 5)
            import cv2
            crop = cv2.imread(str(path), cv2.IMREAD_GRAYSCALE)
            row = self.template.bubbles_for(5)
            first = self.template.bubble_pixels(row[0])
            last = self.template.bubble_pixels(row[-1])
            expected_width = (last[0] - first[0]) + 2 * first[2]
            self.assertGreater(crop.shape[1], expected_width)

    def test_rewriting_the_same_scan_overwrites_instead_of_accumulating(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = MaterialStorage(root=Path(tmp))
            first = write_item_crop(storage, IMAGE_HASH, self.card, self.template, 5)
            second = write_item_crop(storage, IMAGE_HASH, self.card, self.template, 5)
            self.assertEqual(first, second)
            self.assertEqual(len(list(first.parent.glob("omr_item_*.png"))), 1)

    def test_writes_the_aligned_card_for_context(self):
        with tempfile.TemporaryDirectory() as tmp:
            storage = MaterialStorage(root=Path(tmp))
            path = write_aligned_card(storage, IMAGE_HASH, self.card)
            self.assertTrue(path.is_file())

    def test_crop_of_an_unknown_item_raises(self):
        from agente_ia_edu.omr_worker.template import TemplateError
        with tempfile.TemporaryDirectory() as tmp:
            storage = MaterialStorage(root=Path(tmp))
            with self.assertRaises(TemplateError):
                write_item_crop(storage, IMAGE_HASH, self.card, self.template, 999)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_omr_crops.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.services.omr_artifacts'`.

- [ ] **Step 3: Implementar o lado do core**

`src/agente_ia_edu/services/omr_artifacts.py`:

```python
"""Caminhos dos artefatos derivados da leitura optica (Fase 4).

CORE: pathlib puro. Sem cv2, sem numpy, sem PIL - este modulo e importado pela
rota que serve o PNG do recorte ao operador, e a fronteira do §2.3 vale aqui.

Por que caminho derivado e nao coluna nova: o §3 do spec nao preve nenhuma
coluna para artefato derivado. Como `MaterialStorage` ja e endereçado por
conteudo, (image_hash, item_position) determina o arquivo sem ambiguidade:
o worker escreve, a rota le, e reprocessar o mesmo arquivo sobrescreve o mesmo
recorte em vez de acumular copias.
"""

from __future__ import annotations

from pathlib import Path

from .material_storage import MaterialStorage

ALIGNED_FILENAME = "omr_aligned.png"
CROP_FILENAME_TEMPLATE = "omr_item_{position:03d}.png"


def aligned_card_path(storage: MaterialStorage, image_hash: str) -> Path:
    return storage.managed_path(image_hash, ALIGNED_FILENAME)


def item_crop_path(storage: MaterialStorage, image_hash: str, item_position: int) -> Path:
    return storage.managed_path(
        image_hash, CROP_FILENAME_TEMPLATE.format(position=int(item_position))
    )
```

- [ ] **Step 4: Implementar o lado do worker**

`src/agente_ia_edu/omr_worker/crops.py`:

```python
"""Escrita dos recortes que o operador ve na fila de conferencia humana.

MODULO COM `import cv2` - so pode existir dentro de `omr_worker/` (§2.3). E por
isso que o worker (e nao a rota) produz os recortes: o core nao tem como cortar
e gravar imagem sem OpenCV ou Pillow, e Pillow esta proibido.

O recorte e a LINHA INTEIRA da questao (as cinco bolhas mais folga), nao a bolha
isolada. O operador precisa ver o contexto para decidir: uma bolha meio marcada
ao lado de outra bem marcada e uma historia diferente de uma bolha meio marcada
sozinha.
"""

from __future__ import annotations

from pathlib import Path

import cv2
import numpy as np

from ..services.material_storage import MaterialStorage
from ..services.omr_artifacts import aligned_card_path, item_crop_path
from .template import ReaderTemplate


def _write_png(path: Path, image: np.ndarray) -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(path), image):
        raise OSError(f"nao foi possivel gravar o recorte em {path}")
    return path


def write_aligned_card(
    storage: MaterialStorage, image_hash: str, canonical: np.ndarray
) -> Path:
    return _write_png(aligned_card_path(storage, image_hash), canonical)


def write_item_crop(
    storage: MaterialStorage,
    image_hash: str,
    canonical: np.ndarray,
    template: ReaderTemplate,
    item_position: int,
    *,
    pad_ratio: float = 0.9,
) -> Path:
    row = template.bubbles_for(item_position)  # levanta TemplateError se nao existir
    geometry = [template.bubble_pixels(bubble) for bubble in row]
    radius = max(g[2] for g in geometry)
    pad = radius * (1.0 + pad_ratio)
    height, width = canonical.shape[:2]
    x0 = max(0, int(round(min(g[0] for g in geometry) - pad)))
    x1 = min(width, int(round(max(g[0] for g in geometry) + pad)))
    y0 = max(0, int(round(min(g[1] for g in geometry) - pad)))
    y1 = min(height, int(round(max(g[1] for g in geometry) + pad)))
    crop = canonical[y0:y1, x0:x1]
    return _write_png(item_crop_path(storage, image_hash, item_position), crop)
```

- [ ] **Step 5: Rodar e ver passar**

Run: `python -m pytest tests/test_omr_crops.py tests/test_omr_import_boundary.py -v`
Expected: PASS nos 8 + 5 testes. O teste de fronteira roda junto de propósito:
`services/omr_artifacts.py` é core e não pode ter trazido cv2 junto.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/omr_artifacts.py src/agente_ia_edu/omr_worker/crops.py \
        tests/test_omr_crops.py
git commit -m "feat(omr): recortes de bolha no MaterialStorage, caminho derivado do image_hash

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 12: §2.5 — a fila em Postgres (`FOR UPDATE SKIP LOCKED`, lease e retry)

**Files:**
- Create: `src/agente_ia_edu/omr_worker/queue.py`
- Test: `tests/test_omr_queue_postgresql.py`

**Interfaces:**
- Consumes: `OmrConfig.worker_id`, `.lease_seconds`, `.max_attempts` (Task 1);
  tabela `omr_jobs` (Fase 1).
- Produces:
  - `JOB_PENDING = "PENDING"`, `JOB_RUNNING = "RUNNING"`, `JOB_DONE = "DONE"`, `JOB_FAILED = "FAILED"`
  - `ClaimedJob(job_id: uuid.UUID, scan_id: uuid.UUID, attempts: int)` — frozen.
  - `async claim_next_job(session: AsyncSession, *, worker_id: str) -> ClaimedJob | None`
  - `async reclaim_stale_jobs(session: AsyncSession, *, lease_seconds: int) -> int`
  - `async complete_job(session: AsyncSession, job_id: uuid.UUID) -> None`
  - `async fail_job(session: AsyncSession, job_id: uuid.UUID, *, error: str, max_attempts: int) -> str`
  - `async enqueue_scan(session: AsyncSession, scan_id: uuid.UUID) -> uuid.UUID`

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_omr_queue_postgresql.py`:

```python
"""Fase 4 (OMR) Task 12 - §2.5 do spec, contra Postgres de verdade.

"Tabela `omr_jobs` no proprio Postgres, consumida com `SELECT ... FOR UPDATE
SKIP LOCKED`. E uma fila real - atomica, com retry, sem perda de mensagem em
queda do worker - e nao custa nenhum servico de infraestrutura adicional."

Postgres E2E e obrigatorio aqui: SQLite nao implementa FOR UPDATE SKIP LOCKED, e
um teste em SQLite provaria o contrario do que interessa. Banco descartavel na
porta 5433, criado/destruido com tests/_postgres_test_db.py (mesmo padrao de
tests/test_content_segmentation.py).

Os dois testes que justificam a fila existir:
  - dois workers simultaneos nunca pegam o mesmo job (SKIP LOCKED);
  - worker que morre no meio nao leva o job com ele (lease + reclaim).
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid as _uuid

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
from agente_ia_edu.omr_worker.queue import (
    JOB_DONE,
    JOB_FAILED,
    JOB_PENDING,
    JOB_RUNNING,
    claim_next_job,
    complete_job,
    enqueue_scan,
    fail_job,
    reclaim_stale_jobs,
)


class OmrQueuePostgreSQLE2E(unittest.TestCase):
    database_name = "agente_ia_edu_omr_queue_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "OMR_QUEUE_TEST_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    async_database_url = os.getenv(
        "OMR_QUEUE_TEST_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            engine = create_engine(cls.admin_url, execution_options={"isolation_level": "AUTOCOMMIT"})
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            engine.dispose()
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"Postgres da porta 5433 indisponivel: {exc}") from exc

    def setUp(self):
        from tests._postgres_test_db import create_database, drop_database

        drop_database(self.admin_url, self.database_name)
        create_database(self.admin_url, self.database_name)

        async def build():
            engine = create_async_engine(self.async_database_url)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            return engine

        self.engine = asyncio.run(build())
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    def tearDown(self):
        from tests._postgres_test_db import drop_database

        asyncio.run(self.engine.dispose())
        drop_database(self.admin_url, self.database_name)

    def _run(self, coro):
        return asyncio.run(coro)

    async def _job_row(self, job_id):
        async with self.factory() as session:
            row = (await session.execute(
                text("SELECT status, attempts, locked_by, last_error FROM omr_jobs WHERE id = :id"),
                {"id": job_id},
            )).one()
            return {"status": row[0], "attempts": row[1], "locked_by": row[2], "last_error": row[3]}

    def test_enqueue_then_claim_returns_the_scan(self):
        async def scenario():
            scan_id = _uuid.uuid4()
            async with self.factory() as session:
                job_id = await enqueue_scan(session, scan_id)
                await session.commit()
            async with self.factory() as session:
                job = await claim_next_job(session, worker_id="w1")
                await session.commit()
            return job_id, job

        job_id, job = self._run(scenario())
        self.assertIsNotNone(job)
        self.assertEqual(job.job_id, job_id)
        self.assertEqual(job.attempts, 1)
        row = self._run(self._job_row(job_id))
        self.assertEqual(row["status"], JOB_RUNNING)
        self.assertEqual(row["locked_by"], "w1")

    def test_an_empty_queue_returns_none_instead_of_blocking(self):
        async def scenario():
            async with self.factory() as session:
                return await claim_next_job(session, worker_id="w1")

        self.assertIsNone(self._run(scenario()))

    def test_the_queue_is_fifo_by_created_at(self):
        # §3 do spec: sem `created_at` a ordem cairia no id (UUID) e um lote
        # enviado de manha poderia ficar atras de um enviado a tarde.
        async def scenario():
            async with self.factory() as session:
                first = await enqueue_scan(session, _uuid.uuid4())
                await session.execute(
                    text("UPDATE omr_jobs SET created_at = CURRENT_TIMESTAMP - "
                         "make_interval(secs => 600) WHERE id = :id"), {"id": first})
                second = await enqueue_scan(session, _uuid.uuid4())
                await session.commit()
            async with self.factory() as session:
                claimed = await claim_next_job(session, worker_id="w1")
                await session.commit()
            return first, second, claimed

        first, _second, claimed = self._run(scenario())
        self.assertEqual(claimed.job_id, first, "o job mais antigo tem que sair primeiro")

    def test_two_concurrent_workers_never_claim_the_same_job(self):
        async def scenario():
            async with self.factory() as session:
                for _ in range(2):
                    await enqueue_scan(session, _uuid.uuid4())
                await session.commit()

            # Duas sessoes abertas ao mesmo tempo, cada uma reivindicando o proximo.
            async with self.factory() as first_session, self.factory() as second_session:
                first = await claim_next_job(first_session, worker_id="w1")
                second = await claim_next_job(second_session, worker_id="w2")
                await first_session.commit()
                await second_session.commit()
            return first, second

        first, second = self._run(scenario())
        self.assertIsNotNone(first)
        self.assertIsNotNone(second)
        self.assertNotEqual(first.job_id, second.job_id)

    def test_a_worker_that_died_mid_job_has_its_job_reclaimed(self):
        async def scenario():
            async with self.factory() as session:
                job_id = await enqueue_scan(session, _uuid.uuid4())
                await session.commit()
            async with self.factory() as session:
                await claim_next_job(session, worker_id="w-morto")
                await session.commit()
            # Envelhece o lock artificialmente: e o unico jeito deterministico de
            # simular um worker que parou de responder.
            async with self.factory() as session:
                await session.execute(
                    text("UPDATE omr_jobs SET locked_at = CURRENT_TIMESTAMP - make_interval(secs => 900)"))
                await session.commit()
            async with self.factory() as session:
                reclaimed = await reclaim_stale_jobs(session, lease_seconds=300)
                await session.commit()
            async with self.factory() as session:
                job = await claim_next_job(session, worker_id="w-vivo")
                await session.commit()
            return job_id, reclaimed, job

        job_id, reclaimed, job = self._run(scenario())
        self.assertEqual(reclaimed, 1)
        self.assertIsNotNone(job, "o job do worker morto tem que voltar para a fila")
        self.assertEqual(job.job_id, job_id)
        self.assertEqual(job.attempts, 2)

    def test_a_fresh_lock_is_not_reclaimed(self):
        async def scenario():
            async with self.factory() as session:
                await enqueue_scan(session, _uuid.uuid4())
                await session.commit()
            async with self.factory() as session:
                await claim_next_job(session, worker_id="w1")
                await session.commit()
            async with self.factory() as session:
                reclaimed = await reclaim_stale_jobs(session, lease_seconds=300)
                await session.commit()
            return reclaimed

        self.assertEqual(self._run(scenario()), 0)

    def test_complete_marks_the_job_done_and_it_is_never_claimed_again(self):
        async def scenario():
            async with self.factory() as session:
                job_id = await enqueue_scan(session, _uuid.uuid4())
                await session.commit()
            async with self.factory() as session:
                job = await claim_next_job(session, worker_id="w1")
                await complete_job(session, job.job_id)
                await session.commit()
            async with self.factory() as session:
                again = await claim_next_job(session, worker_id="w1")
                await session.commit()
            return job_id, again

        job_id, again = self._run(scenario())
        self.assertIsNone(again)
        self.assertEqual(self._run(self._job_row(job_id))["status"], JOB_DONE)

    def test_failure_below_the_cap_goes_back_to_pending_for_retry(self):
        async def scenario():
            async with self.factory() as session:
                job_id = await enqueue_scan(session, _uuid.uuid4())
                await session.commit()
            async with self.factory() as session:
                job = await claim_next_job(session, worker_id="w1")
                status = await fail_job(session, job.job_id, error="timeout do MuPDF", max_attempts=3)
                await session.commit()
            return job_id, status

        job_id, status = self._run(scenario())
        self.assertEqual(status, JOB_PENDING)
        row = self._run(self._job_row(job_id))
        self.assertEqual(row["status"], JOB_PENDING)
        self.assertEqual(row["last_error"], "timeout do MuPDF")
        self.assertIsNone(row["locked_by"])

    def test_failure_at_the_cap_goes_to_failed_and_stops_retrying(self):
        async def scenario():
            async with self.factory() as session:
                job_id = await enqueue_scan(session, _uuid.uuid4())
                await session.commit()
            last_status = None
            for _ in range(3):
                async with self.factory() as session:
                    job = await claim_next_job(session, worker_id="w1")
                    last_status = await fail_job(session, job.job_id, error="boom", max_attempts=3)
                    await session.commit()
            async with self.factory() as session:
                again = await claim_next_job(session, worker_id="w1")
                await session.commit()
            return job_id, last_status, again

        job_id, last_status, again = self._run(scenario())
        self.assertEqual(last_status, JOB_FAILED)
        self.assertIsNone(again, "job esgotado nao volta para a fila em loop infinito")
        self.assertEqual(self._run(self._job_row(job_id))["status"], JOB_FAILED)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `ps aux | grep pytest` (esperar terminar) e então
`python -m pytest tests/test_omr_queue_postgresql.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.omr_worker.queue'`.

- [ ] **Step 3: Implementar**

`src/agente_ia_edu/omr_worker/queue.py`:

```python
"""§2.5 do spec: a fila core -> worker, que e uma tabela Postgres.

"O repositorio nao tem hoje nenhum mecanismo de job em segundo plano (auditado:
nenhum Celery, RQ ou fila), entao esta e a menor abstracao que resolve o
problema. Uma fila externa so se justifica se o volume um dia exigir."

Tres propriedades, cada uma com o seu teste:

1. ATOMICIDADE - `UPDATE ... WHERE id = (SELECT ... FOR UPDATE SKIP LOCKED
   LIMIT 1)`. O SKIP LOCKED faz o segundo worker PULAR a linha travada em vez de
   esperar por ela, entao N workers escalam sem coordenacao externa.

2. RECUPERACAO DE QUEDA - um worker que morre no meio deixa o job em RUNNING
   para sempre. `reclaim_stale_jobs` devolve para PENDING todo RUNNING cujo
   `locked_at` passou do lease. E por isso que o lease existe, e nao um
   heartbeat: heartbeat precisa de um segundo loop e de mais estado.

3. RETRY COM TETO - `fail_job` devolve para PENDING ate `max_attempts`, e depois
   sela em FAILED. Sem teto, um arquivo corrompido reentra na fila para sempre e
   consome o worker inteiro.

SQL cru (`text()`) em vez de ORM de proposito: `FOR UPDATE SKIP LOCKED` dentro de
subconsulta de UPDATE nao tem expressao direta no ORM, e escrever o SQL a mao
deixa visivel exatamente a garantia que se esta comprando.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

JOB_PENDING = "PENDING"
JOB_RUNNING = "RUNNING"
JOB_DONE = "DONE"
JOB_FAILED = "FAILED"


@dataclass(frozen=True)
class ClaimedJob:
    job_id: uuid.UUID
    scan_id: uuid.UUID
    #: Numero da tentativa que acabou de comecar (1 na primeira).
    attempts: int


_ENQUEUE_SQL = text("""
    INSERT INTO omr_jobs (id, scan_id, status, attempts, created_at)
    VALUES (:id, :scan_id, :pending, 0, CURRENT_TIMESTAMP)
""")

_CLAIM_SQL = text("""
    UPDATE omr_jobs
       SET status = :running,
           attempts = attempts + 1,
           locked_at = CURRENT_TIMESTAMP,
           locked_by = :worker_id
     WHERE id = (
           SELECT id FROM omr_jobs
            WHERE status = :pending
            ORDER BY created_at, id
              FOR UPDATE SKIP LOCKED
            LIMIT 1
     )
 RETURNING id, scan_id, attempts
""")

_RECLAIM_SQL = text("""
    UPDATE omr_jobs
       SET status = :pending,
           locked_at = NULL,
           locked_by = NULL,
           last_error = COALESCE(last_error, 'worker perdeu o lease e o job foi recuperado')
     WHERE status = :running
       AND locked_at IS NOT NULL
       AND locked_at < CURRENT_TIMESTAMP - make_interval(secs => :lease_seconds)
 RETURNING id
""")

_COMPLETE_SQL = text("""
    UPDATE omr_jobs
       SET status = :done, locked_at = NULL, locked_by = NULL, last_error = NULL
     WHERE id = :id
""")

_FAIL_SQL = text("""
    UPDATE omr_jobs
       SET status = :status, locked_at = NULL, locked_by = NULL, last_error = :error
     WHERE id = :id
 RETURNING attempts
""")


async def enqueue_scan(session: AsyncSession, scan_id: uuid.UUID) -> uuid.UUID:
    job_id = uuid.uuid4()
    await session.execute(_ENQUEUE_SQL, {"id": job_id, "scan_id": scan_id, "pending": JOB_PENDING})
    return job_id


async def claim_next_job(session: AsyncSession, *, worker_id: str) -> ClaimedJob | None:
    result = await session.execute(
        _CLAIM_SQL, {"running": JOB_RUNNING, "pending": JOB_PENDING, "worker_id": worker_id}
    )
    row = result.first()
    if row is None:
        return None
    return ClaimedJob(job_id=row[0], scan_id=row[1], attempts=int(row[2]))


async def reclaim_stale_jobs(session: AsyncSession, *, lease_seconds: int) -> int:
    result = await session.execute(
        _RECLAIM_SQL,
        {"pending": JOB_PENDING, "running": JOB_RUNNING, "lease_seconds": int(lease_seconds)},
    )
    return len(result.fetchall())


async def complete_job(session: AsyncSession, job_id: uuid.UUID) -> None:
    await session.execute(_COMPLETE_SQL, {"done": JOB_DONE, "id": job_id})


async def fail_job(
    session: AsyncSession, job_id: uuid.UUID, *, error: str, max_attempts: int
) -> str:
    current = (await session.execute(
        text("SELECT attempts FROM omr_jobs WHERE id = :id"), {"id": job_id}
    )).scalar_one()
    status = JOB_FAILED if int(current) >= int(max_attempts) else JOB_PENDING
    await session.execute(_FAIL_SQL, {"status": status, "error": error[:2000], "id": job_id})
    return status
```

> **Ordenação FIFO.** `omr_jobs.created_at` existe exatamente para isto (§3 do spec):
> "Sem ele a ordenação cairia no `id`, que é UUID — estável e arbitrária, mas não FIFO, e um
> lote enviado de manhã poderia ficar atrás de um enviado à tarde." O `id` entra só como
> desempate estável entre jobs criados no mesmo instante.

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_omr_queue_postgresql.py -v`
Expected: PASS nos 8 testes.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/omr_worker/queue.py tests/test_omr_queue_postgresql.py
git commit -m "feat(omr): §2.5 fila em Postgres com SKIP LOCKED, lease e retry com teto

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 13: Prova de que `resolution = PENDING` existe e é o estado da fila

**Files:**
- Test: `tests/test_omr_mark_resolution_pending.py`

**Interfaces:**
- Consumes: `AnswerCardMark`, `MARK_RESOLUTIONS` (Fase 1, `db/models/answer_card.py`).
- Produces: nada de produção. É uma **prova de premissa**, consumida pelas Tasks 14, 18 e 20.

**Esta task não tem migration.** A Fase 1 já entrega
`answer_card_marks.resolution` com o domínio `PENDING | AUTO | HUMAN` (§3 do spec),
exatamente o que esta fase precisa para separar "decidido pelo leitor" de "esperando o
operador". Sem essa separação, um item em BRANCO (`detected_option` nulo, decidido) e um
item AMBÍGUO (`detected_option` nulo, indeciso) seriam indistinguíveis no banco, e a fila de
conferência do §5 não poderia ser montada sem reprocessar imagem.

O que sobra é a **prova**: todo o resto desta fase assume esse estado, e assumir sem verificar
é como esse tipo de coisa quebra tarde. É barato e roda em segundos.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_omr_mark_resolution_pending.py`:

```python
"""Fase 4 (OMR) Task 13 - prova de premissa: `answer_card_marks.resolution`
aceita PENDING.

O §3 do spec define `resolution (PENDING | AUTO | HUMAN)` e a Fase 1 entrega a
coluna assim. As Tasks 14, 18 e 20 desta fase dependem inteiramente disso:
PENDING e o que separa "o leitor decidiu" de "o leitor pediu ajuda". Sem esse
terceiro estado, um item em BRANCO e um item AMBIGUO ficam identicos no banco
(os dois com detected_option nulo) e a fila de conferencia nao existe.

Postgres E2E porque e a CheckConstraint do banco que esta sendo verificada -
em SQLite a constraint existe mas nao e a mesma que roda em producao.
"""

from __future__ import annotations

import asyncio
import os
import unittest
import uuid as _uuid

from sqlalchemy import create_engine, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models.answer_card import MARK_RESOLUTIONS


class MarkResolutionPendingPostgreSQLE2E(unittest.TestCase):
    database_name = "agente_ia_edu_omr_resolution_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "OMR_RESOLUTION_TEST_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    async_database_url = os.getenv(
        "OMR_RESOLUTION_TEST_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            engine = create_engine(cls.admin_url, execution_options={"isolation_level": "AUTOCOMMIT"})
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            engine.dispose()
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"Postgres da porta 5433 indisponivel: {exc}") from exc

    def setUp(self):
        from tests._postgres_test_db import create_database, drop_database

        drop_database(self.admin_url, self.database_name)
        create_database(self.admin_url, self.database_name)

        async def build():
            engine = create_async_engine(self.async_database_url)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            return engine

        self.engine = asyncio.run(build())
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    def tearDown(self):
        from tests._postgres_test_db import drop_database

        asyncio.run(self.engine.dispose())
        drop_database(self.admin_url, self.database_name)

    def _insert(self, resolution: str):
        async def scenario():
            async with self.factory() as session:
                await session.execute(
                    text("INSERT INTO answer_card_marks "
                         "(id, scan_id, item_position, fill_intensities, confidence, resolution) "
                         "VALUES (:id, :scan_id, :position, :intensities, :confidence, :resolution)"),
                    {"id": _uuid.uuid4(), "scan_id": _uuid.uuid4(), "position": 1,
                     "intensities": '[0.1, 0.2, 0.3, 0.4, 0.5]', "confidence": 0.9,
                     "resolution": resolution},
                )
                await session.commit()

        asyncio.run(scenario())

    def test_the_domain_declared_by_phase_one_has_the_three_states(self):
        self.assertEqual(set(MARK_RESOLUTIONS), {"PENDING", "AUTO", "HUMAN"})

    def test_pending_is_accepted_by_the_real_database(self):
        self._insert("PENDING")

    def test_auto_and_human_are_accepted(self):
        self._insert("AUTO")
        self._insert("HUMAN")

    def test_an_invented_value_is_rejected(self):
        with self.assertRaises(Exception):
            self._insert("TALVEZ")


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar (ou, se a Fase 1 já entregou, ver passar)**

Run: `ps aux | grep pytest` (esperar terminar) e então
`python -m pytest tests/test_omr_mark_resolution_pending.py -v`

Este é o único teste do plano que **pode nascer verde**: ele verifica uma premissa, não
introduz comportamento. Duas leituras possíveis:

- **Verde:** a Fase 1 entregou como o spec manda. Siga para a Task 14.
- **Vermelho** em `test_the_domain_declared_by_phase_one_has_the_three_states` ou em
  `test_pending_is_accepted_by_the_real_database`: a Fase 1 ainda está com o domínio antigo
  (`AUTO | HUMAN`). **Pare e resolva na Fase 1**, ampliando o domínio na migration dela.
  Não crie aqui uma migration aditiva para corrigir: seriam duas definições do mesmo domínio,
  e a segunda pisaria na primeira dependendo da ordem de aplicação.

- [ ] **Step 3: Commit**

```bash
git add tests/test_omr_mark_resolution_pending.py
git commit -m "test(omr): prova que answer_card_marks.resolution aceita PENDING

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 14: Persistência e consolidação — marks, status do scan e respostas com procedência

**Files:**
- Create: `src/agente_ia_edu/omr_worker/persist.py`
- Test: `tests/test_omr_persist_postgresql.py`

**Interfaces:**
- Consumes: `ScanReading`/`SCAN_*` (Task 10), `resolve.RESOLVED/BLANK/NEEDS_REVIEW_OUTCOMES`
  (Task 8), `write_aligned_card`/`write_item_crop` (Task 11), `MaterialStorage`,
  `ReaderTemplate` (Task 2), `OmrConfig` (Task 1), modelos da Fase 1.
- Produces (`services/omr_consolidation.py`, **core**, sem cv2/numpy):
  - `RESPONSE_SOURCE_OMR: str = "OMR"` (o par de `RESPONSE_SOURCE_MANUAL`, da Fase 2)
  - `MARK_PENDING = "PENDING"`, `MARK_AUTO = "AUTO"`, `MARK_HUMAN = "HUMAN"` — declaradas
    aqui **uma vez** e importadas por `omr_worker/persist.py` e por `services/omr_review.py`
  - `async consolidate_scan(session: AsyncSession, scan_id: uuid.UUID) -> int` — devolve
    quantas linhas de `mock_exam_responses` foram escritas/atualizadas.
- Produces (`omr_worker/persist.py`):
  - `async resolve_template_version(session, mock_exam_id) -> str`
  - `async persist_reading(session, *, scan_id, reading, template, storage, config) -> str`
    (devolve o status final gravado no scan)

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_omr_persist_postgresql.py`:

```python
"""Fase 4 (OMR) Task 14 - a leitura vira linhas de banco.

Tres garantias:

1. As CINCO intensidades de cada questao sao gravadas, nao so a conclusao. §3 do
   spec: "Guardar as cinco intensidades medidas (...) e o que permite auditar uma
   leitura contestada sem reprocessar a imagem."

2. Idempotencia no retry. Um job pode ser reprocessado (lease expirado, retry
   apos falha transitoria). Reprocessar NAO pode duplicar as 90 marcas, e nao
   pode APAGAR uma decisao humana ja tomada - o operador nao vai conferir duas
   vezes o mesmo cartao porque o worker caiu.

3. QR que nao resolve para nenhum `answer_card` -> scan FAILED com motivo
   legivel. Nunca "provavelmente e deste aluno".

Postgres E2E pelo mesmo motivo da Task 12: e o comportamento transacional real
que esta sendo testado.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AnswerCard, AnswerCardMark, AnswerCardScan
from agente_ia_edu.omr_worker import synthetic
from agente_ia_edu.omr_worker.config import OmrConfig
from agente_ia_edu.omr_worker.persist import MARK_HUMAN, MARK_PENDING, persist_reading
from agente_ia_edu.omr_worker.pipeline import SCAN_FAILED, SCAN_NEEDS_REVIEW, SCAN_PROCESSED, read_image
from agente_ia_edu.services.material_storage import MaterialStorage
from agente_ia_edu.services.omr_artifacts import item_crop_path

TOKEN = "3f1c9a2b7d4e5f6a8b0c1d2e3f405162"


class PersistReadingPostgreSQLE2E(unittest.TestCase):
    database_name = "agente_ia_edu_omr_persist_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "OMR_PERSIST_TEST_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    async_database_url = os.getenv(
        "OMR_PERSIST_TEST_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            engine = create_engine(cls.admin_url, execution_options={"isolation_level": "AUTOCOMMIT"})
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            engine.dispose()
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"Postgres da porta 5433 indisponivel: {exc}") from exc

    def setUp(self):
        from tests._postgres_test_db import create_database, drop_database

        drop_database(self.admin_url, self.database_name)
        create_database(self.admin_url, self.database_name)
        self._tmp = tempfile.TemporaryDirectory()
        self.storage = MaterialStorage(root=Path(self._tmp.name))
        self.config = OmrConfig()
        self.template = synthetic.build_template(n_items=90)
        rng = __import__("numpy").random.default_rng(synthetic.SEED)
        self.answers = {p: "ABCDE"[int(rng.integers(0, 5))] for p in range(1, 91)}
        self.mock_exam_id = _uuid.uuid4()
        self.student_id = _uuid.uuid4()
        self.scan_id = _uuid.uuid4()
        self.image_hash = "b" * 64
        # Gabarito: a alternativa correta de cada questao. A consolidacao usa
        # `mock_exam_items` para resolver posicao -> item_id e calcular is_correct.
        self.correct = {position: "ABCDE"[(position - 1) % 5] for position in range(1, 91)}

        async def build():
            engine = create_async_engine(self.async_database_url)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                from agente_ia_edu.db.models import MockExamItem

                for position in range(1, 91):
                    session.add(MockExamItem(
                        id=_uuid.uuid4(), mock_exam_id=self.mock_exam_id, position=position,
                        area_code="MT", correct_option=self.correct[position], is_anchor=False))
                session.add(AnswerCard(
                    id=_uuid.uuid4(), mock_exam_id=self.mock_exam_id, student_id=self.student_id,
                    booklet_code="UNICO", qr_token=TOKEN,
                    template_version=self.template.template_version,
                    printed_at=datetime.now(timezone.utc)))
                session.add(AnswerCardScan(
                    id=self.scan_id, mock_exam_id=self.mock_exam_id, answer_card_id=None,
                    image_hash=self.image_hash, storage_path="/tmp/x.png", source="MOBILE",
                    status="PENDING"))
                await session.commit()
            return engine, factory

        self.engine, self.factory = asyncio.run(build())

    def tearDown(self):
        from tests._postgres_test_db import drop_database

        asyncio.run(self.engine.dispose())
        drop_database(self.admin_url, self.database_name)
        self._tmp.cleanup()

    def _persist(self, reading):
        async def scenario():
            async with self.factory() as session:
                status = await persist_reading(
                    session, scan_id=self.scan_id, reading=reading,
                    template=self.template, storage=self.storage, config=self.config)
                await session.commit()
                return status

        return asyncio.run(scenario())

    def _marks(self):
        async def scenario():
            async with self.factory() as session:
                rows = (await session.execute(
                    select(AnswerCardMark).where(AnswerCardMark.scan_id == self.scan_id)
                    .order_by(AnswerCardMark.item_position))).scalars().all()
                return list(rows)

        return asyncio.run(scenario())

    def _scan(self):
        async def scenario():
            async with self.factory() as session:
                return await session.get(AnswerCardScan, self.scan_id)

        return asyncio.run(scenario())

    def test_a_clean_reading_writes_90_marks_and_binds_the_card(self):
        card = synthetic.render_card(self.template, qr_payload=TOKEN, answers=self.answers)
        status = self._persist(read_image(card, self.template, self.config))
        self.assertEqual(status, SCAN_PROCESSED)
        marks = self._marks()
        self.assertEqual(len(marks), 90)
        scan = self._scan()
        self.assertEqual(scan.status, SCAN_PROCESSED)
        self.assertIsNotNone(scan.answer_card_id)
        self.assertEqual(scan.reader_version, self.config.reader_version)
        self.assertIsNotNone(scan.processed_at)

    def test_the_five_intensities_are_stored_for_every_item(self):
        card = synthetic.render_card(self.template, qr_payload=TOKEN, answers=self.answers)
        self._persist(read_image(card, self.template, self.config))
        for mark in self._marks():
            self.assertEqual(len(mark.fill_intensities), 5)
            self.assertTrue(all(isinstance(v, float) for v in mark.fill_intensities))

    def test_auto_resolved_items_carry_the_detected_option_and_resolution_auto(self):
        card = synthetic.render_card(self.template, qr_payload=TOKEN, answers=self.answers)
        self._persist(read_image(card, self.template, self.config))
        for mark in self._marks():
            self.assertEqual(mark.resolution, "AUTO")
            self.assertEqual(mark.detected_option, self.answers[mark.item_position])
            self.assertEqual(mark.resolved_option, self.answers[mark.item_position])

    def test_a_double_mark_is_stored_as_pending_with_a_crop_on_disk(self):
        answers = dict(self.answers)
        answers[9] = ["A", "E"]
        card = synthetic.render_card(self.template, qr_payload=TOKEN, answers=answers)
        status = self._persist(read_image(card, self.template, self.config))
        self.assertEqual(status, SCAN_NEEDS_REVIEW)
        mark = next(m for m in self._marks() if m.item_position == 9)
        self.assertEqual(mark.resolution, MARK_PENDING)
        self.assertIsNone(mark.detected_option)
        self.assertIsNone(mark.resolved_option)
        self.assertTrue(item_crop_path(self.storage, self.image_hash, 9).is_file())

    def test_crops_are_written_only_for_items_that_need_review(self):
        answers = dict(self.answers)
        answers[9] = ["A", "E"]
        card = synthetic.render_card(self.template, qr_payload=TOKEN, answers=answers)
        self._persist(read_image(card, self.template, self.config))
        self.assertFalse(item_crop_path(self.storage, self.image_hash, 10).exists())

    def test_reprocessing_does_not_duplicate_marks(self):
        card = synthetic.render_card(self.template, qr_payload=TOKEN, answers=self.answers)
        reading = read_image(card, self.template, self.config)
        self._persist(reading)
        self._persist(reading)
        self.assertEqual(len(self._marks()), 90)

    def test_reprocessing_never_overwrites_a_human_decision(self):
        answers = dict(self.answers)
        answers[9] = ["A", "E"]
        card = synthetic.render_card(self.template, qr_payload=TOKEN, answers=answers)
        reading = read_image(card, self.template, self.config)
        self._persist(reading)

        async def decide():
            async with self.factory() as session:
                mark = (await session.execute(
                    select(AnswerCardMark).where(AnswerCardMark.scan_id == self.scan_id,
                                                 AnswerCardMark.item_position == 9))).scalar_one()
                mark.resolution = MARK_HUMAN
                mark.resolved_option = "E"
                mark.resolved_at = datetime.now(timezone.utc)
                await session.commit()

        asyncio.run(decide())
        self._persist(reading)
        mark = next(m for m in self._marks() if m.item_position == 9)
        self.assertEqual(mark.resolution, MARK_HUMAN)
        self.assertEqual(mark.resolved_option, "E")

    def test_a_processed_scan_consolidates_responses_with_source_omr(self):
        # §3 do spec: "A procedencia nao e opcional. Sem `source` e sem autor,
        # uma resposta digitada a mao fica indistinguivel de uma lida pelo
        # scanner, e nao ha como auditar quem digitou o cartao de um aluno
        # quando a nota for contestada."
        card = synthetic.render_card(self.template, qr_payload=TOKEN, answers=self.answers)
        self._persist(read_image(card, self.template, self.config))

        async def read_responses():
            async with self.factory() as session:
                from agente_ia_edu.db.models import MockExamResponse

                return (await session.execute(
                    select(MockExamResponse).where(
                        MockExamResponse.mock_exam_id == self.mock_exam_id))).scalars().all()

        responses = asyncio.run(read_responses())
        self.assertEqual(len(responses), 90)
        for response in responses:
            self.assertEqual(response.source, "OMR")
            self.assertIsNone(response.entered_by_external_id,
                              "leitura optica nao tem autor humano")
            self.assertIsNotNone(response.created_at)

    def test_consolidated_answers_match_what_was_marked_and_score_correctly(self):
        card = synthetic.render_card(self.template, qr_payload=TOKEN, answers=self.answers)
        self._persist(read_image(card, self.template, self.config))

        async def read_responses():
            async with self.factory() as session:
                from agente_ia_edu.db.models import MockExamItem, MockExamResponse

                rows = (await session.execute(
                    select(MockExamResponse, MockExamItem).join(
                        MockExamItem, MockExamItem.id == MockExamResponse.item_id))).all()
                return [(item.position, response.chosen_option, response.is_correct,
                         item.correct_option) for response, item in rows]

        for position, chosen, is_correct, correct_option in asyncio.run(read_responses()):
            self.assertEqual(chosen, self.answers[position])
            self.assertEqual(is_correct, chosen == correct_option)

    def test_a_scan_awaiting_review_consolidates_nothing_yet(self):
        # Consolidar antes da conferencia gravaria branco no lugar da duvida.
        answers = dict(self.answers)
        answers[9] = ["A", "E"]
        card = synthetic.render_card(self.template, qr_payload=TOKEN, answers=answers)
        status = self._persist(read_image(card, self.template, self.config))
        self.assertEqual(status, SCAN_NEEDS_REVIEW)

        async def count_responses():
            async with self.factory() as session:
                from agente_ia_edu.db.models import MockExamResponse

                return len((await session.execute(select(MockExamResponse))).scalars().all())

        self.assertEqual(asyncio.run(count_responses()), 0)

    def test_reconsolidating_updates_instead_of_duplicating(self):
        card = synthetic.render_card(self.template, qr_payload=TOKEN, answers=self.answers)
        reading = read_image(card, self.template, self.config)
        self._persist(reading)
        self._persist(reading)

        async def count_responses():
            async with self.factory() as session:
                from agente_ia_edu.db.models import MockExamResponse

                return len((await session.execute(select(MockExamResponse))).scalars().all())

        self.assertEqual(asyncio.run(count_responses()), 90)

    def test_an_unknown_qr_token_fails_the_scan_and_writes_no_marks(self):
        card = synthetic.render_card(self.template, qr_payload="token-de-outro-simulado",
                                     answers=self.answers)
        status = self._persist(read_image(card, self.template, self.config))
        self.assertEqual(status, SCAN_FAILED)
        self.assertEqual(self._marks(), [])
        scan = self._scan()
        self.assertIn("token-de-outro-simulado", scan.failure_reason)
        self.assertIsNone(scan.answer_card_id)

    def test_a_failed_reading_records_the_reason_verbatim(self):
        card = synthetic.render_card(self.template, qr_payload=TOKEN, answers=self.answers)
        x, y, width, height = self.template.qr_region_pixels()
        card[y:y + height, x:x + width] = 252
        status = self._persist(read_image(card, self.template, self.config))
        self.assertEqual(status, SCAN_FAILED)
        self.assertIn("QR_ILEGIVEL", self._scan().failure_reason)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_omr_persist_postgresql.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.omr_worker.persist'`.

- [ ] **Step 3: Implementar a consolidação (core)**

`src/agente_ia_edu/services/omr_consolidation.py`:

```python
"""Marcas ja resolvidas -> `mock_exam_responses`, com procedencia (§3 do spec).

CORE: sem cv2, sem numpy. Mora aqui, e nao no worker, porque os DOIS caminhos
que fecham um cartao precisam dela: o worker, quando a leitura sai limpa, e a
tela de conferencia, quando o operador resolve a ultima duvida.

"A procedencia nao e opcional. Sem `source` e sem autor, uma resposta digitada a
mao fica indistinguivel de uma lida pelo scanner, e nao ha como auditar quem
digitou o cartao de um aluno quando a nota for contestada."

Por isso toda linha escrita aqui leva `source='OMR'` e `entered_by_external_id=None`:
leitura optica nao tem autor humano. A digitacao manual (fase 2) escreve
`source='MANUAL'` com o id de quem digitou, pelo seu proprio caminho.

Idempotente por (student_id, item_id): reprocessar um scan ATUALIZA a resposta,
nunca cria uma segunda para a mesma questao do mesmo aluno.
"""

from __future__ import annotations

import uuid

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import (
    AnswerCard,
    AnswerCardMark,
    AnswerCardScan,
    MockExamItem,
    MockExamResponse,
)

#: `mock_exam_responses.source` (§3). A outra constante do par,
#: `RESPONSE_SOURCE_MANUAL`, e da Fase 2 (services/simulado_responses.py) e este
#: modulo nunca a usa - simetricamente ao que aquele modulo diz sobre esta.
RESPONSE_SOURCE_OMR = "OMR"

#: `answer_card_marks.resolution` (§3). Declaradas AQUI, uma vez: este modulo e
#: o unico que os dois lados da fronteira ja importam (o worker, em persist.py,
#: e o core, em omr_review.py), entao poe-las aqui nao cria ciclo nem copia.
#: A tupla completa do dominio e `MARK_RESOLUTIONS`, da Fase 1.
MARK_PENDING = "PENDING"
MARK_AUTO = "AUTO"
MARK_HUMAN = "HUMAN"


class ScanNotConsolidableError(RuntimeError):
    """O scan ainda nao esta pronto para virar resposta."""


async def consolidate_scan(session: AsyncSession, scan_id: uuid.UUID) -> int:
    """Escreve as respostas deste cartao. Devolve quantas linhas foram gravadas.

    Nao consolida um scan que ainda tenha marca em PENDING: gravaria branco no
    lugar de uma duvida que uma pessoa ainda vai resolver.
    """
    scan = await session.get(AnswerCardScan, scan_id)
    if scan is None or scan.answer_card_id is None:
        raise ScanNotConsolidableError(
            f"scan {scan_id} nao existe ou ainda nao foi ligado a um cartao"
        )
    if scan.status != "PROCESSED":
        return 0

    pending = (await session.execute(
        select(AnswerCardMark.id)
        .where(AnswerCardMark.scan_id == scan_id, AnswerCardMark.resolution == MARK_PENDING)
        .limit(1)
    )).first()
    if pending is not None:
        return 0

    card = await session.get(AnswerCard, scan.answer_card_id)
    if card is None:
        raise ScanNotConsolidableError(f"cartao {scan.answer_card_id} nao encontrado")

    items = {
        item.position: item
        for item in (await session.execute(
            select(MockExamItem).where(MockExamItem.mock_exam_id == scan.mock_exam_id)
        )).scalars().all()
    }
    existing = {
        response.item_id: response
        for response in (await session.execute(
            select(MockExamResponse).where(
                MockExamResponse.mock_exam_id == scan.mock_exam_id,
                MockExamResponse.student_id == card.student_id,
            )
        )).scalars().all()
    }
    marks = (await session.execute(
        select(AnswerCardMark).where(AnswerCardMark.scan_id == scan_id)
    )).scalars().all()

    written = 0
    for mark in marks:
        item = items.get(mark.item_position)
        if item is None:
            # Cartao com mais questoes do que o gabarito cadastrado. Nao inventa
            # item: a questao simplesmente nao entra na matriz, e a divergencia
            # aparece na contagem devolvida.
            continue
        chosen = mark.resolved_option
        response = existing.get(item.id)
        if response is None:
            response = MockExamResponse(
                id=uuid.uuid4(),
                mock_exam_id=scan.mock_exam_id,
                student_id=card.student_id,
                item_id=item.id,
            )
            session.add(response)
        response.chosen_option = chosen
        response.is_correct = bool(chosen is not None and chosen == item.correct_option)
        response.source = RESPONSE_SOURCE_OMR
        response.entered_by_external_id = None  # leitura optica nao tem autor humano
        written += 1

    await session.flush()
    return written
```

- [ ] **Step 4: Implementar a persistência (worker)**

`src/agente_ia_edu/omr_worker/persist.py`:

```python
"""Gravacao da leitura no banco: `answer_card_scans` + `answer_card_marks`.

Tres decisoes com consequencia:

1. UMA LINHA POR QUESTAO, sempre, com as CINCO intensidades. §3 do spec:
   "Guardar as cinco intensidades medidas, e nao so a conclusao, e o que permite
   auditar uma leitura contestada sem reprocessar a imagem." Um item em branco
   tambem ganha a sua linha - a ausencia de resposta e um dado, nao um silencio.

2. IDEMPOTENCIA POR ITEM, e nao "apaga tudo e reescreve". Um scan pode ser
   reprocessado (lease expirado, retry). Reescrever em bloco apagaria decisoes
   humanas ja tomadas, e o operador teria que conferir de novo o mesmo cartao
   porque o worker caiu. Marca com `resolution = HUMAN` e PRESERVADA intacta.

3. RECORTE SO PARA QUEM PRECISA. Gravar 90 PNGs por cartao x 1.500 alunos seria
   135 mil arquivos por simulado para mostrar, na pratica, algumas dezenas.
   O recorte sai apenas para os itens que vao para conferencia.
"""

from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AnswerCard, AnswerCardMark, AnswerCardScan
from ..services.material_storage import MaterialStorage
from ..services.omr_consolidation import (
    MARK_AUTO,
    MARK_HUMAN,
    MARK_PENDING,
    consolidate_scan,
)
from .config import OmrConfig
from .crops import write_aligned_card, write_item_crop
from .pipeline import SCAN_FAILED, SCAN_NEEDS_REVIEW, SCAN_PROCESSED, ScanReading
from .resolve import NEEDS_REVIEW_OUTCOMES
from .template import ReaderTemplate, TemplateError


class ScanNotFoundError(RuntimeError):
    """O job aponta para um scan que nao existe mais."""


async def resolve_template_version(session: AsyncSession, mock_exam_id: uuid.UUID) -> str:
    """Versao de template do simulado, lida dos cartoes impressos.

    O §3 do spec poe `template_version` em `answer_cards`, e nao em `mock_exams`:
    todos os cartoes de um simulado sao impressos no mesmo ato, entao a versao e
    a mesma para todos. Se por algum motivo houver mais de uma, a leitura para -
    ler um cartao com a grade errada produziria respostas plausiveis e erradas.
    """
    versions = (await session.execute(
        select(AnswerCard.template_version)
        .where(AnswerCard.mock_exam_id == mock_exam_id)
        .distinct()
    )).scalars().all()
    if not versions:
        raise TemplateError(
            f"simulado {mock_exam_id} nao tem nenhum cartao impresso registrado; "
            "gere os cartoes antes de enviar digitalizacoes."
        )
    if len(versions) > 1:
        raise TemplateError(
            f"simulado {mock_exam_id} tem mais de uma versao de template ({sorted(versions)}). "
            "Nao e possivel escolher a grade de leitura com seguranca."
        )
    return str(versions[0])


async def _fail_scan(session: AsyncSession, scan: AnswerCardScan, reason: str, config: OmrConfig) -> str:
    scan.status = SCAN_FAILED
    scan.failure_reason = reason
    scan.reader_version = config.reader_version
    scan.processed_at = datetime.now(timezone.utc)
    await session.flush()
    return SCAN_FAILED


async def persist_reading(
    session: AsyncSession,
    *,
    scan_id: uuid.UUID,
    reading: ScanReading,
    template: ReaderTemplate,
    storage: MaterialStorage,
    config: OmrConfig,
) -> str:
    scan = await session.get(AnswerCardScan, scan_id)
    if scan is None:
        raise ScanNotFoundError(f"answer_card_scan {scan_id} nao encontrado")

    if reading.status == SCAN_FAILED:
        return await _fail_scan(session, scan, reading.failure_reason or "LEITURA_FALHOU", config)

    card = (await session.execute(
        select(AnswerCard).where(
            AnswerCard.mock_exam_id == scan.mock_exam_id,
            AnswerCard.qr_token == reading.qr_token,
        )
    )).scalar_one_or_none()
    if card is None:
        return await _fail_scan(
            session, scan,
            f"QR_NAO_RECONHECIDO: o token {reading.qr_token!r} nao corresponde a nenhum "
            "cartao impresso deste simulado. Confira se a folha e deste simulado ou digite "
            "as respostas manualmente.",
            config,
        )

    if reading.canonical_image is not None:
        write_aligned_card(storage, scan.image_hash, reading.canonical_image)

    existing = {
        mark.item_position: mark
        for mark in (await session.execute(
            select(AnswerCardMark).where(AnswerCardMark.scan_id == scan_id)
        )).scalars().all()
    }

    for resolution in reading.resolutions:
        mark = existing.get(resolution.item_position)
        if mark is not None and mark.resolution == MARK_HUMAN:
            # Decisao humana ja tomada nunca e sobrescrita por reprocessamento.
            continue
        needs_review = resolution.outcome in NEEDS_REVIEW_OUTCOMES
        if mark is None:
            mark = AnswerCardMark(id=uuid.uuid4(), scan_id=scan_id,
                                  item_position=resolution.item_position)
            session.add(mark)
        mark.detected_option = resolution.detected_option
        mark.fill_intensities = [float(value) for value in resolution.intensities]
        mark.confidence = float(resolution.confidence)
        mark.resolution = MARK_PENDING if needs_review else MARK_AUTO
        mark.resolved_option = None if needs_review else resolution.detected_option
        mark.resolved_by_external_id = None
        mark.resolved_at = None
        if needs_review and reading.canonical_image is not None:
            write_item_crop(storage, scan.image_hash, reading.canonical_image,
                            template, resolution.item_position)

    await session.flush()

    still_pending = (await session.execute(
        select(AnswerCardMark.id).where(
            AnswerCardMark.scan_id == scan_id,
            AnswerCardMark.resolution == MARK_PENDING,
        ).limit(1)
    )).first()

    scan.answer_card_id = card.id
    scan.status = SCAN_NEEDS_REVIEW if still_pending is not None else SCAN_PROCESSED
    scan.failure_reason = None
    scan.reader_version = config.reader_version
    scan.processed_at = datetime.now(timezone.utc)
    await session.flush()

    if scan.status == SCAN_PROCESSED:
        # Cartao fechado sem duvida nenhuma: vira resposta agora, com procedencia
        # OMR (§3). Um cartao que foi para conferencia so consolida quando o
        # operador resolver a ultima marca (Task 18).
        await consolidate_scan(session, scan_id)

    return scan.status
```

- [ ] **Step 5: Rodar e ver passar**

Run: `python -m pytest tests/test_omr_persist_postgresql.py tests/test_omr_import_boundary.py -v`
Expected: PASS nos 13 + 6 testes. A fronteira roda junto porque este passo acrescenta um
módulo ao core (`services/omr_consolidation.py`), que não pode ter trazido cv2/numpy.

- [ ] **Step 6: Commit**

```bash
git add src/agente_ia_edu/services/omr_consolidation.py \
        src/agente_ia_edu/omr_worker/persist.py tests/test_omr_persist_postgresql.py
git commit -m "feat(omr): persistencia idempotente + consolidacao com source=OMR

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 15: `__main__.py` — o processo do worker, de ponta a ponta

**Files:**
- Create: `src/agente_ia_edu/omr_worker/runner.py`
- Create: `src/agente_ia_edu/omr_worker/__main__.py`
- Test: `tests/test_omr_worker_loop_postgresql.py`

**Interfaces:**
- Consumes: tudo das Tasks 1–14.
- Produces (`runner.py`):
  - `class TemplateCache` com `async get(session, mock_exam_id) -> ReaderTemplate`
  - `async process_next_job(session_factory, *, config, storage, templates) -> bool`
    (True quando havia trabalho)
  - `async run_forever(session_factory, *, config, storage, stop_event) -> None`
  - `def build_storage(environ) -> MaterialStorage`
- Produces (`__main__.py`): `def main(argv: list[str] | None = None) -> int`

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_omr_worker_loop_postgresql.py`:

```python
"""Fase 4 (OMR) Task 15 - o worker inteiro, do job na fila as marcas no banco.

E o unico teste que exercita simultaneamente a fila (§2.5), o pipeline (§5) e a
persistencia. Os testes das tasks anteriores cobrem cada peca; este cobre a
fiacao entre elas, que e onde erro de integracao mora.

Tambem prova as duas regras operacionais do worker:
  - erro no meio do processamento NAO deixa o job travado em RUNNING;
  - erro do pipeline vira `answer_card_scans.failure_reason` legivel, nunca
    excecao engolida com scan parado em PENDING para sempre.
"""

from __future__ import annotations

import asyncio
import os
import tempfile
import unittest
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy import create_engine, select, text
from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AnswerCard, AnswerCardMark, AnswerCardScan
from agente_ia_edu.omr_worker import synthetic
from agente_ia_edu.omr_worker.config import OmrConfig
from agente_ia_edu.omr_worker.queue import JOB_DONE, JOB_PENDING, enqueue_scan
from agente_ia_edu.omr_worker.runner import TemplateCache, build_storage, process_next_job
from agente_ia_edu.services.material_storage import MaterialStorage

TOKEN = "9a8b7c6d5e4f30291827364554637281"


class WorkerLoopPostgreSQLE2E(unittest.TestCase):
    database_name = "agente_ia_edu_omr_loop_test"
    user = os.getenv("POSTGRES_USER", "agenteedu")
    password = os.getenv("POSTGRES_PASSWORD", "agenteedu_dev")
    admin_url = os.getenv(
        "OMR_LOOP_TEST_ADMIN_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/postgres",
    )
    async_database_url = os.getenv(
        "OMR_LOOP_TEST_DATABASE_URL",
        f"postgresql+psycopg://{user}:{password}@localhost:5433/{database_name}",
    )

    @classmethod
    def setUpClass(cls):
        try:
            engine = create_engine(cls.admin_url, execution_options={"isolation_level": "AUTOCOMMIT"})
            with engine.connect() as connection:
                connection.execute(text("SELECT 1"))
            engine.dispose()
        except Exception as exc:  # noqa: BLE001
            raise unittest.SkipTest(f"Postgres da porta 5433 indisponivel: {exc}") from exc

    def setUp(self):
        from tests._postgres_test_db import create_database, drop_database

        drop_database(self.admin_url, self.database_name)
        create_database(self.admin_url, self.database_name)
        self._tmp = tempfile.TemporaryDirectory()
        self.root = Path(self._tmp.name)
        self.storage = MaterialStorage(root=self.root / "storage")
        self.config = OmrConfig(worker_id="w-test")
        self.template = synthetic.build_template(n_items=90)
        self.template_dir = self.root / "templates"
        self.template_dir.mkdir()
        self._write_template_json()
        rng = np.random.default_rng(synthetic.SEED)
        self.answers = {p: "ABCDE"[int(rng.integers(0, 5))] for p in range(1, 91)}
        self.mock_exam_id = _uuid.uuid4()
        self.templates = TemplateCache(root=self.template_dir)

        async def build():
            engine = create_async_engine(self.async_database_url)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            return engine, async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)

        self.engine, self.factory = asyncio.run(build())

    def _write_template_json(self):
        import json
        # Serializado pelo `to_dict()` da PROPRIA Fase 1 - o mesmo que o gerador
        # grava. Nenhum dicionario montado a mao aqui: seria reintroduzir, no
        # teste, a divergencia de contrato que o §2.1 proibiu no codigo.
        card = self.template.card
        (self.template_dir / f"template_{card.template_version}.json").write_text(
            json.dumps(card.to_dict(), ensure_ascii=False, sort_keys=True), encoding="utf-8")

    def tearDown(self):
        from tests._postgres_test_db import drop_database

        asyncio.run(self.engine.dispose())
        drop_database(self.admin_url, self.database_name)
        self._tmp.cleanup()

    def _seed_scan(self, image: np.ndarray, *, image_hash: str = "c" * 64) -> _uuid.UUID:
        path = self.root / f"{image_hash}.png"
        cv2.imwrite(str(path), image)
        scan_id = _uuid.uuid4()

        async def scenario():
            async with self.factory() as session:
                session.add(AnswerCard(
                    id=_uuid.uuid4(), mock_exam_id=self.mock_exam_id, student_id=_uuid.uuid4(),
                    booklet_code="UNICO", qr_token=TOKEN,
                    template_version=self.template.template_version,
                    printed_at=datetime.now(timezone.utc)))
                session.add(AnswerCardScan(
                    id=scan_id, mock_exam_id=self.mock_exam_id, answer_card_id=None,
                    image_hash=image_hash, storage_path=str(path), source="SCANNER",
                    status="PENDING"))
                await enqueue_scan(session, scan_id)
                await session.commit()

        asyncio.run(scenario())
        return scan_id

    def _process_once(self) -> bool:
        return asyncio.run(process_next_job(
            self.factory, config=self.config, storage=self.storage, templates=self.templates))

    def _job_status(self) -> str:
        async def scenario():
            async with self.factory() as session:
                return (await session.execute(text("SELECT status FROM omr_jobs"))).scalar_one()

        return asyncio.run(scenario())

    def _scan(self, scan_id):
        async def scenario():
            async with self.factory() as session:
                return await session.get(AnswerCardScan, scan_id)

        return asyncio.run(scenario())

    def test_an_empty_queue_reports_no_work_without_raising(self):
        self.assertFalse(self._process_once())

    def test_a_clean_card_goes_from_queued_to_processed_with_90_marks(self):
        card = synthetic.render_card(self.template, qr_payload=TOKEN, answers=self.answers)
        scan_id = self._seed_scan(card)
        self.assertTrue(self._process_once())
        self.assertEqual(self._job_status(), JOB_DONE)
        self.assertEqual(self._scan(scan_id).status, "PROCESSED")

        async def count():
            async with self.factory() as session:
                rows = (await session.execute(
                    select(AnswerCardMark).where(AnswerCardMark.scan_id == scan_id))).scalars().all()
                return [(m.item_position, m.resolved_option) for m in rows]

        marks = asyncio.run(count())
        self.assertEqual(len(marks), 90)
        self.assertEqual(dict(marks), self.answers)

    def test_a_degraded_photo_still_completes_the_job(self):
        card = synthetic.render_card(self.template, qr_payload=TOKEN, answers=self.answers)
        degraded = synthetic.degrade(card, np.random.default_rng(synthetic.SEED), "celular_sombra")
        scan_id = self._seed_scan(degraded)
        self.assertTrue(self._process_once())
        self.assertEqual(self._job_status(), JOB_DONE)
        self.assertIn(self._scan(scan_id).status, {"PROCESSED", "NEEDS_REVIEW"})

    def test_an_unalignable_image_fails_the_scan_and_completes_the_job(self):
        # Papel em branco: nenhum ArUco. O JOB termina (nao adianta tentar de
        # novo a mesma imagem), o SCAN vai para FAILED com motivo legivel.
        blank = np.full((self.template.canonical_height, self.template.canonical_width),
                        252, dtype=np.uint8)
        scan_id = self._seed_scan(blank)
        self.assertTrue(self._process_once())
        self.assertEqual(self._job_status(), JOB_DONE)
        scan = self._scan(scan_id)
        self.assertEqual(scan.status, "FAILED")
        self.assertIn("ARUCO_NAO_DETECTADO", scan.failure_reason)

    def test_a_missing_image_file_retries_instead_of_failing_the_scan(self):
        # Arquivo ausente pode ser montagem de volume que ainda nao subiu: e um
        # erro de INFRAESTRUTURA, nao do cartao. O job volta para PENDING.
        card = synthetic.render_card(self.template, qr_payload=TOKEN, answers=self.answers)
        scan_id = self._seed_scan(card)

        async def remove():
            async with self.factory() as session:
                scan = await session.get(AnswerCardScan, scan_id)
                scan.storage_path = str(self.root / "arquivo-que-sumiu.png")
                await session.commit()

        asyncio.run(remove())
        self.assertTrue(self._process_once())
        self.assertEqual(self._job_status(), JOB_PENDING)
        self.assertEqual(self._scan(scan_id).status, "PENDING")

    def test_build_storage_uses_the_container_environment_variable(self):
        target = self.root / "outro-storage"
        storage = build_storage({"OMR_STORAGE_ROOT": str(target)})
        self.assertEqual(storage.root, target)

    def test_build_storage_falls_back_to_the_project_default(self):
        storage = build_storage({})
        self.assertEqual(storage.root, MaterialStorage().root)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_omr_worker_loop_postgresql.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.omr_worker.runner'`.

- [ ] **Step 3: Implementar `runner.py`**

`src/agente_ia_edu/omr_worker/runner.py`:

```python
"""O laco do worker: reivindica um job, le o cartao, grava, repete.

A distincao que este modulo existe para fazer: ERRO DO CARTAO x ERRO DE
INFRAESTRUTURA.

  - Erro do cartao (ArUco ausente, QR ilegivel, token desconhecido) e um fato
    sobre aquela folha. Tentar de novo a MESMA imagem daria o MESMO resultado,
    entao o job termina (DONE) e o scan vai para FAILED com motivo legivel para
    o operador reenviar ou digitar. Retry aqui seria so queimar o worker.

  - Erro de infraestrutura (arquivo ainda nao montado, banco indisponivel,
    excecao inesperada) nao e sobre a folha. O job volta para PENDING e e
    tentado de novo, ate `max_attempts`.

Confundir os dois e o erro classico de fila: ou o sistema tenta para sempre um
PDF corrompido, ou descarta um cartao bom porque o volume demorou a montar.
"""

from __future__ import annotations

import asyncio
import logging
import os
from collections.abc import Mapping
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker

from ..db.models import AnswerCardScan
from ..services.material_storage import MaterialStorage
from .config import OmrConfig
from .persist import ScanNotFoundError, persist_reading, resolve_template_version
from .pipeline import read_scan
from .queue import claim_next_job, complete_job, fail_job, reclaim_stale_jobs
from .raster import RasterError
from .template import ReaderTemplate, TemplateError

logger = logging.getLogger(__name__)


class TemplateCache:
    """Um simulado tem uma versao de template e 400-1.500 cartoes. Reler o JSON
    a cada cartao seria 1.500 leituras de disco por nada."""

    def __init__(self, *, root: Path | None = None) -> None:
        #: Raiz onde procurar `template_<versao>.json`. Em producao e a raiz do
        #: MaterialStorage (`storage.root`), onde a Fase 1 gravou o arquivo.
        self._root = root
        self._by_exam: dict[str, ReaderTemplate] = {}

    async def get(self, session: AsyncSession, mock_exam_id) -> ReaderTemplate:
        key = str(mock_exam_id)
        cached = self._by_exam.get(key)
        if cached is not None:
            return cached
        version = await resolve_template_version(session, mock_exam_id)
        template = ReaderTemplate.load(version, root=self._root)
        self._by_exam[key] = template
        return template


def build_storage(environ: Mapping[str, str] | None = None) -> MaterialStorage:
    source = os.environ if environ is None else environ
    root = source.get("OMR_STORAGE_ROOT")
    return MaterialStorage(root=Path(root)) if root else MaterialStorage()


async def process_next_job(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    config: OmrConfig,
    storage: MaterialStorage,
    templates: TemplateCache,
) -> bool:
    """Processa um job. Devolve False quando a fila estava vazia."""
    async with session_factory() as session:
        job = await claim_next_job(session, worker_id=config.worker_id)
        await session.commit()
    if job is None:
        return False

    try:
        async with session_factory() as session:
            scan = await session.get(AnswerCardScan, job.scan_id)
            if scan is None:
                raise ScanNotFoundError(f"answer_card_scan {job.scan_id} nao encontrado")
            template = await templates.get(session, scan.mock_exam_id)
            source_path = Path(scan.storage_path)
            if not source_path.is_file():
                # INFRAESTRUTURA: o arquivo pode aparecer na proxima tentativa.
                raise FileNotFoundError(f"imagem do scan nao encontrada em {source_path}")

            reading = read_scan(source_path, template, config)   # §5, puro
            await persist_reading(
                session, scan_id=job.scan_id, reading=reading,
                template=template, storage=storage, config=config,
            )
            await session.commit()
    except (RasterError, TemplateError, ScanNotFoundError, FileNotFoundError, OSError) as exc:
        # Erros de infraestrutura/configuracao: retry ate o teto.
        logger.warning("job %s falhou (tentativa %s): %s", job.job_id, job.attempts, exc)
        async with session_factory() as session:
            await fail_job(session, job.job_id, error=str(exc), max_attempts=config.max_attempts)
            await session.commit()
        return True
    except Exception as exc:  # noqa: BLE001 - nunca engolido: vai para last_error
        logger.exception("erro inesperado no job %s", job.job_id)
        async with session_factory() as session:
            await fail_job(session, job.job_id, error=f"erro inesperado: {exc}",
                           max_attempts=config.max_attempts)
            await session.commit()
        return True

    # Leitura concluida - inclusive quando o CARTAO falhou (ArUco/QR): tentar de
    # novo a mesma imagem daria o mesmo resultado.
    async with session_factory() as session:
        await complete_job(session, job.job_id)
        await session.commit()
    return True


async def run_forever(
    session_factory: async_sessionmaker[AsyncSession],
    *,
    config: OmrConfig,
    storage: MaterialStorage,
    templates: TemplateCache | None = None,
    stop_event: asyncio.Event | None = None,
) -> None:
    cache = templates or TemplateCache()
    stop = stop_event or asyncio.Event()
    logger.info("omr_worker %s iniciado (dpi=%s, leitor=%s)",
                config.worker_id, config.raster_dpi, config.reader_version)
    while not stop.is_set():
        async with session_factory() as session:
            recovered = await reclaim_stale_jobs(session, lease_seconds=config.lease_seconds)
            await session.commit()
        if recovered:
            logger.warning("%s job(s) de worker morto devolvidos para a fila", recovered)

        worked = await process_next_job(
            session_factory, config=config, storage=storage, templates=cache)
        if not worked:
            try:
                await asyncio.wait_for(stop.wait(), timeout=config.poll_interval_seconds)
            except asyncio.TimeoutError:
                pass
    logger.info("omr_worker %s encerrado", config.worker_id)
```

- [ ] **Step 4: Implementar `__main__.py`**

`src/agente_ia_edu/omr_worker/__main__.py`:

```python
"""Entrypoint do processo `omr_worker`.

    python -m agente_ia_edu.omr_worker              # laco continuo (container)
    python -m agente_ia_edu.omr_worker --once       # um job e sai (diagnostico)

Le `DATABASE_URL` pelo mesmo `db/session.py` do core - o worker usa o MESMO
banco, e a fila (§2.5) e a unica superficie de acoplamento entre os dois.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import signal
import sys

from ..db.session import create_session_factory
from .config import OmrConfig
from .runner import TemplateCache, build_storage, process_next_job, run_forever


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="omr_worker", description="Leitor optico de cartoes-resposta")
    parser.add_argument("--once", action="store_true",
                        help="processa no maximo um job e encerra")
    parser.add_argument("--log-level", default="INFO")
    args = parser.parse_args(argv)

    logging.basicConfig(
        level=getattr(logging, args.log_level.upper(), logging.INFO),
        format="%(asctime)s %(levelname)s %(name)s %(message)s",
    )

    config = OmrConfig.from_env()
    storage = build_storage()
    session_factory = create_session_factory()
    templates = TemplateCache(root=storage.root)

    if args.once:
        worked = asyncio.run(process_next_job(
            session_factory, config=config, storage=storage, templates=templates))
        return 0 if worked else 1

    async def serve() -> None:
        stop = asyncio.Event()
        loop = asyncio.get_running_loop()
        for signal_name in ("SIGTERM", "SIGINT"):
            with_signal = getattr(signal, signal_name, None)
            if with_signal is not None:
                loop.add_signal_handler(with_signal, stop.set)
        await run_forever(session_factory, config=config, storage=storage,
                          templates=templates, stop_event=stop)

    asyncio.run(serve())
    return 0


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 5: Rodar e ver passar**

Run: `python -m pytest tests/test_omr_worker_loop_postgresql.py -v`
Expected: PASS nos 7 testes.

- [ ] **Step 6: Conferir que o entrypoint responde**

Run: `python -m agente_ia_edu.omr_worker --help`
Expected: o texto de ajuda com `--once` e `--log-level`, sem exceção.

- [ ] **Step 7: Commit**

```bash
git add src/agente_ia_edu/omr_worker/runner.py src/agente_ia_edu/omr_worker/__main__.py \
        tests/test_omr_worker_loop_postgresql.py
git commit -m "feat(omr): laco do worker e entrypoint python -m agente_ia_edu.omr_worker

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 16: Empacotamento — imagem própria e serviço no compose sob perfil

**Files:**
- Create: `docker/omr_worker/Dockerfile`
- Create: `docker/omr_worker/README.md`
- Modify: `docker-compose.yml` (acrescentar o serviço `omr_worker` depois de `n8n`)
- Modify: `.env.example` (acrescentar as variáveis do worker)
- Test: `tests/test_omr_packaging.py`

**Interfaces:**
- Consumes: extra `omr` (Task 1), entrypoint `python -m agente_ia_edu.omr_worker` (Task 15).
- Produces: o serviço `omr_worker` no compose, ativado por `--profile omr`.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_omr_packaging.py`:

```python
"""Fase 4 (OMR) Task 16 - o empacotamento do worker e verificavel, nao folclore.

§2.3 do spec: "Roda como processo/container proprio, com o seu proprio conjunto
de dependencias." Este arquivo checa as quatro promessas que esse desenho faz:

1. o extra `omr` existe no pyproject e traz opencv/numpy;
2. as dependencias BASE do projeto continuam sem opencv, numpy e Pillow - se um
   dia alguem "simplificar" movendo opencv para `dependencies`, o teste reprova;
3. a imagem do worker instala `.[omr]` e chama o modulo como entrypoint;
4. o servico do compose esta sob `profiles: ["omr"]`, para que o
   `docker compose up` de quem nunca usou OMR continue subindo o mesmo que hoje.

Leitura estatica de arquivo - nao sobe container nenhum, roda em qualquer maquina.
"""

from __future__ import annotations

import tomllib
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]


class PyprojectExtras(unittest.TestCase):
    def setUp(self):
        self.pyproject = tomllib.loads((ROOT / "pyproject.toml").read_text(encoding="utf-8"))

    def test_the_omr_extra_exists_and_brings_opencv_headless_and_numpy(self):
        extras = self.pyproject["project"]["optional-dependencies"]
        self.assertIn("omr", extras)
        joined = " ".join(extras["omr"]).lower()
        # contrib: `cv2.aruco` e `cv2.QRCodeEncoder` nao existem no opencv base.
        self.assertIn("opencv-contrib-python-headless", joined)
        self.assertIn("numpy", joined)
        self.assertNotIn("opencv-python-headless>", joined,
                         "a variante base nao tem cv2.aruco - o alinhamento do §5.2 nao carrega")

    def test_the_omr_extra_is_importable_and_really_has_aruco(self):
        import cv2  # o extra precisa estar instalado para as tasks 2-11
        self.assertTrue(hasattr(cv2, "aruco"),
                        "cv2.aruco ausente: o extra instalou a variante NAO-contrib")
        self.assertTrue(hasattr(cv2, "QRCodeEncoder"))

    def test_the_core_dependencies_never_gain_opencv_numpy_or_pillow(self):
        base = " ".join(self.pyproject["project"]["dependencies"]).lower()
        for forbidden in ("opencv", "numpy", "pillow"):
            self.assertNotIn(forbidden, base,
                             f"{forbidden} nao pode entrar nas dependencias do core (§2.3)")

    def test_the_recovery_extra_is_untouched(self):
        extras = self.pyproject["project"]["optional-dependencies"]
        self.assertIn("recovery", extras)
        self.assertTrue(any("pymupdf" in item.lower() for item in extras["recovery"]))


class WorkerImage(unittest.TestCase):
    def setUp(self):
        self.dockerfile = (ROOT / "docker" / "omr_worker" / "Dockerfile").read_text(encoding="utf-8")

    def test_installs_the_omr_extra(self):
        self.assertIn(".[omr]", self.dockerfile)

    def test_entrypoint_is_the_worker_module(self):
        self.assertIn("agente_ia_edu.omr_worker", self.dockerfile)

    def test_pins_the_python_version_the_project_requires(self):
        self.assertIn("python:3.13", self.dockerfile)


class ComposeService(unittest.TestCase):
    def setUp(self):
        self.compose = (ROOT / "docker-compose.yml").read_text(encoding="utf-8")

    def test_the_worker_service_exists_and_builds_from_its_own_dockerfile(self):
        self.assertIn("omr_worker:", self.compose)
        self.assertIn("docker/omr_worker/Dockerfile", self.compose)

    def test_the_worker_is_behind_the_omr_profile(self):
        worker_block = self.compose[self.compose.index("omr_worker:"):]
        self.assertIn('profiles: ["omr"]', worker_block,
                      "sem perfil, `docker compose up` passaria a exigir build do worker")

    def test_the_worker_waits_for_a_healthy_postgres(self):
        worker_block = self.compose[self.compose.index("omr_worker:"):]
        self.assertIn("service_healthy", worker_block)

    def test_postgres_and_n8n_are_untouched(self):
        self.assertIn("agente-ia-edu-postgres", self.compose)
        self.assertIn("agente-ia-edu-n8n", self.compose)


class EnvExample(unittest.TestCase):
    def test_the_worker_variables_are_documented(self):
        text = (ROOT / ".env.example").read_text(encoding="utf-8")
        for key in ("OMR_WORKER_ID", "OMR_STORAGE_ROOT"):
            self.assertIn(key, text)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_omr_packaging.py -v`
Expected: FAIL — `FileNotFoundError: .../docker/omr_worker/Dockerfile` nas classes
`WorkerImage`, e falha de `assertIn("omr_worker:", ...)` em `ComposeService`.

- [ ] **Step 3: Escrever o Dockerfile**

`docker/omr_worker/Dockerfile`:

```dockerfile
# omr_worker - leitura optica de cartoes-resposta (Fase 4).
#
# Imagem PROPRIA, e nao um comando extra na imagem da API, por uma razao
# documentada no §2.3 do spec: e aqui, e so aqui, que OpenCV existe. O
# pyproject do core proibe Pillow porque ele altera `page.images` do pypdf e
# quebra o parser de ingestao de questoes; ambientes de visao computacional
# convivem com Pillow. Duas imagens = duas arvores de dependencia que nunca se
# encontram.
#
# O worker instala o PROPRIO pacote do projeto (precisa dos modelos SQLAlchemy e
# do MaterialStorage) mais o extra `omr`. A fronteira nao e "o worker nao ve o
# core" - e "o core nao importa cv2", e quem prova isso e
# tests/test_omr_import_boundary.py.
FROM python:3.13-slim-bookworm

ENV PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

COPY pyproject.toml ./
COPY src ./src

RUN pip install --no-cache-dir ".[omr]"

# Raiz do MaterialStorage dentro do container. O compose monta
# ./var/material_storage aqui, que e o mesmo diretorio que a API ja usa.
ENV OMR_STORAGE_ROOT=/data/material_storage
VOLUME ["/data/material_storage"]

ENTRYPOINT ["python", "-m", "agente_ia_edu.omr_worker"]
```

- [ ] **Step 4: Acrescentar o serviço ao compose**

Em `docker-compose.yml`, depois do bloco `n8n:` e antes de `volumes:`:

```yaml
  # Leitura optica dos cartoes-resposta (Fase 4). Sob perfil: `docker compose up`
  # sem argumento continua subindo exatamente postgres + n8n, como sempre.
  # Para ligar:    docker compose --profile omr up -d omr_worker
  # Para escalar:  docker compose --profile omr up -d --scale omr_worker=4
  #                (a fila usa FOR UPDATE SKIP LOCKED - N workers nao colidem)
  omr_worker:
    build:
      context: .
      dockerfile: docker/omr_worker/Dockerfile
    profiles: ["omr"]
    restart: unless-stopped
    environment:
      DATABASE_URL: postgresql+psycopg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      OMR_WORKER_ID: ${OMR_WORKER_ID:-omr-worker-1}
      OMR_STORAGE_ROOT: /data/material_storage
      OMR_RASTER_DPI: ${OMR_RASTER_DPI:-200}
      OMR_LEASE_SECONDS: ${OMR_LEASE_SECONDS:-300}
      OMR_MAX_ATTEMPTS: ${OMR_MAX_ATTEMPTS:-3}
    depends_on:
      postgres:
        condition: service_healthy
    volumes:
      - ./var/material_storage:/data/material_storage
```

> **Sem `container_name`** de propósito: `container_name` é incompatível com
> `--scale`, e escalar o worker é justamente o que a fila do §2.5 viabiliza.

- [ ] **Step 5: Documentar as variáveis no `.env.example`**

Acrescentar ao final de `.env.example`:

```bash
# --- omr_worker (Fase 4: leitura optica dos cartoes-resposta) ---
# So usado pelo servico `omr_worker` do compose (perfil "omr"). A API nao le nada disto.
OMR_WORKER_ID=omr-worker-1
OMR_STORAGE_ROOT=/data/material_storage
OMR_RASTER_DPI=200
OMR_LEASE_SECONDS=300
OMR_MAX_ATTEMPTS=3
```

- [ ] **Step 6: Escrever o README do worker**

`docker/omr_worker/README.md`:

```markdown
# omr_worker

Leitura optica dos cartoes-resposta dos simulados (Fase 4 de
`docs/superpowers/specs/2026-09-29-correcao-simulados-tri-design.md`).

## Por que e um container separado

E o unico lugar do sistema onde OpenCV existe. O `pyproject.toml` do core proibe
Pillow (ele altera `page.images` do pypdf e quebra o parser de ingestao de
questoes) e ambientes de visao computacional convivem com Pillow. Duas imagens,
duas arvores de dependencia, nenhuma interferencia.

`tests/test_omr_import_boundary.py` prova, a cada execucao da suite, que nenhum
modulo do core importa cv2, numpy ou PIL.

## Como roda

    docker compose --profile omr up -d --build omr_worker
    docker compose --profile omr logs -f omr_worker

Fora do docker (desenvolvimento):

    pip install -e ".[omr]"
    DATABASE_URL=postgresql+psycopg://... python -m agente_ia_edu.omr_worker --once

## Como escala

A fila e a tabela `omr_jobs`, consumida com `SELECT ... FOR UPDATE SKIP LOCKED`.
N workers nao colidem e nao precisam de coordenacao:

    docker compose --profile omr up -d --scale omr_worker=4

## Como se comunica com o core

So pela tabela `omr_jobs`. O core enfileira; o worker consome. Nenhum import
cruza a fronteira, nenhuma porta e aberta, nenhum broker e necessario.

## Variaveis

| Variavel | Padrao | Para que serve |
|---|---|---|
| `DATABASE_URL` | (obrigatoria) | Mesmo banco do core |
| `OMR_WORKER_ID` | `omr-worker-local` | Gravado em `omr_jobs.locked_by` |
| `OMR_STORAGE_ROOT` | `var/material_storage` do projeto | Raiz do `MaterialStorage` |
| `OMR_RASTER_DPI` | `200` | §5.1 do spec |
| `OMR_LEASE_SECONDS` | `300` | Tempo ate um job de worker morto ser recuperado |
| `OMR_MAX_ATTEMPTS` | `3` | Teto de tentativas por scan |
```

- [ ] **Step 7: Rodar e ver passar**

Run: `python -m pytest tests/test_omr_packaging.py -v`
Expected: PASS nos 11 testes.

- [ ] **Step 8: Conferir que o compose continua válido e que o perfil isola o serviço**

Run: `docker compose config --services`
Expected: `postgres` e `n8n` apenas (o `omr_worker` **não** aparece sem o perfil).

Run: `docker compose --profile omr config --services`
Expected: `postgres`, `n8n` e `omr_worker`.

- [ ] **Step 9: Commit**

```bash
git add docker/omr_worker/Dockerfile docker/omr_worker/README.md docker-compose.yml \
        .env.example tests/test_omr_packaging.py
git commit -m "build(omr): imagem propria do worker e servico no compose sob perfil omr

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 17: §4 — trava de integridade antes de `CALIBRATED` e de `PUBLISHED`

**Files:**
- Create: `src/agente_ia_edu/services/mock_exam_scan_gate.py`
- Test: `tests/test_mock_exam_scan_gate.py`

**Interfaces:**
- Consumes: `AnswerCardScan` (Fase 1); `BLOCKING_SCAN_STATUSES`, `STATUS_CALIBRATED` e
  `STATUS_PUBLISHED` de `services/simulado_service.py` (**Fase 2 — importados, nunca
  redeclarados**).
- Produces:
  - `GUARDED_TRANSITIONS: tuple[str, ...] = (STATUS_CALIBRATED, STATUS_PUBLISHED)` — re-exporta
    a tupla montada a partir das constantes de status da Fase 2.
  - `ScanReadiness(mock_exam_id, pending, needs_review, failed, processed, ready_for_calibration)` — frozen.
  - `PendingScanReviewError(RuntimeError)` com atributo `.readiness: ScanReadiness`
  - `async scan_readiness(session: AsyncSession, mock_exam_id: uuid.UUID) -> ScanReadiness`
  - `async assert_ready_for_calibration(session: AsyncSession, mock_exam_id: uuid.UUID) -> None`
  - `async assert_ready_for_publication(session: AsyncSession, mock_exam_id: uuid.UUID) -> None`

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_mock_exam_scan_gate.py`:

```python
"""Fase 4 (OMR) Task 17 - §4 do spec, "Trava de integridade".

"Diferente de uma nota individual faltante, calibrar com parte dos alunos
ausente distorce os parametros dos itens - e portanto contamina a nota de TODOS.
O sistema bloqueia a transicao para CALIBRATED enquanto houver
`answer_card_scans` em NEEDS_REVIEW ou PENDING."

E por isso que a trava nao e uma checagem de tela nem um aviso: um cartao nao
conferido nao atrasa a nota de um aluno, ele estraga a nota da escola inteira.

O ultimo teste e uma tranca arquitetural: nenhum modulo de `services/` pode
escrever o status CALIBRATED sem importar esta trava. Hoje ele passa por vazio
(a Fase 3 ainda nao existe neste repo); no dia em que o motor de TRI entrar, ele
reprova se o caminho de calibragem esquecer a trava.
"""

from __future__ import annotations

import asyncio
import unittest
import uuid as _uuid
from pathlib import Path

from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine
from sqlalchemy.pool import StaticPool

from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AnswerCardScan
from agente_ia_edu.services.mock_exam_scan_gate import (
    BLOCKING_SCAN_STATUSES,
    GUARDED_TRANSITIONS,
    PendingScanReviewError,
    assert_ready_for_calibration,
    assert_ready_for_publication,
    scan_readiness,
)

SERVICES_DIR = Path(__file__).resolve().parents[1] / "src" / "agente_ia_edu" / "services"


class MockExamScanGate(unittest.TestCase):
    def setUp(self):
        self.mock_exam_id = _uuid.uuid4()
        self.other_exam_id = _uuid.uuid4()

        async def build():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            return engine

        self.engine = asyncio.run(build())
        self.factory = async_sessionmaker(self.engine, class_=AsyncSession, expire_on_commit=False)

    def _seed(self, statuses, *, mock_exam_id=None):
        exam_id = mock_exam_id or self.mock_exam_id

        async def scenario():
            async with self.factory() as session:
                for index, status in enumerate(statuses):
                    session.add(AnswerCardScan(
                        id=_uuid.uuid4(), mock_exam_id=exam_id, answer_card_id=None,
                        image_hash=f"{index:064d}", storage_path=f"/tmp/{index}.png",
                        source="SCANNER", status=status))
                await session.commit()

        asyncio.run(scenario())

    def _readiness(self):
        async def scenario():
            async with self.factory() as session:
                return await scan_readiness(session, self.mock_exam_id)

        return asyncio.run(scenario())

    def _assert_gate(self):
        async def scenario():
            async with self.factory() as session:
                await assert_ready_for_calibration(session, self.mock_exam_id)

        asyncio.run(scenario())

    def test_all_processed_is_ready(self):
        self._seed(["PROCESSED"] * 5)
        readiness = self._readiness()
        self.assertTrue(readiness.ready_for_calibration)
        self.assertEqual(readiness.processed, 5)
        self._assert_gate()  # nao levanta

    def test_a_single_scan_awaiting_review_blocks_everything(self):
        self._seed(["PROCESSED"] * 400 + ["NEEDS_REVIEW"])
        readiness = self._readiness()
        self.assertFalse(readiness.ready_for_calibration)
        self.assertEqual(readiness.needs_review, 1)
        with self.assertRaises(PendingScanReviewError) as ctx:
            self._assert_gate()
        self.assertIn("1 aguardando conferencia", str(ctx.exception))
        self.assertEqual(ctx.exception.readiness.needs_review, 1)

    def test_a_scan_still_pending_blocks_too(self):
        self._seed(["PROCESSED", "PENDING"])
        self.assertFalse(self._readiness().ready_for_calibration)
        with self.assertRaises(PendingScanReviewError):
            self._assert_gate()

    def test_a_failed_scan_blocks_too_because_it_is_a_missing_student(self):
        # §4: "um cartao que o leitor nao conseguiu processar e um aluno ausente
        # da matriz de respostas, e a matriz e o insumo da calibragem."
        self._seed(["PROCESSED"] * 200 + ["FAILED"])
        readiness = self._readiness()
        self.assertFalse(readiness.ready_for_calibration)
        self.assertEqual(readiness.failed, 1)
        with self.assertRaises(PendingScanReviewError) as ctx:
            self._assert_gate()
        self.assertIn("1 com falha de leitura", str(ctx.exception))

    def test_the_publication_gate_is_the_same_gate(self):
        # §4: "A trava vale para a transicao para CALIBRATED E para a transicao
        # para PUBLISHED. O efeito que ela protege e a publicacao."
        self._seed(["PROCESSED", "FAILED"])

        async def scenario():
            async with self.factory() as session:
                await assert_ready_for_publication(session, self.mock_exam_id)

        with self.assertRaises(PendingScanReviewError) as ctx:
            asyncio.run(scenario())
        self.assertIn("PUBLISHED", str(ctx.exception))

    def test_a_clean_exam_passes_both_gates(self):
        self._seed(["PROCESSED"] * 3)

        async def scenario():
            async with self.factory() as session:
                await assert_ready_for_calibration(session, self.mock_exam_id)
                await assert_ready_for_publication(session, self.mock_exam_id)

        asyncio.run(scenario())  # nao levanta

    def test_scans_of_another_exam_never_block_this_one(self):
        self._seed(["PROCESSED"])
        self._seed(["NEEDS_REVIEW"], mock_exam_id=self.other_exam_id)
        self.assertTrue(self._readiness().ready_for_calibration)

    def test_an_exam_without_any_scan_is_not_ready(self):
        # Zero cartao lido nao e "tudo pronto": e "nada chegou". Deixar passar
        # calibraria com a matriz vazia.
        readiness = self._readiness()
        self.assertFalse(readiness.ready_for_calibration)
        with self.assertRaises(PendingScanReviewError):
            self._assert_gate()


class NoTransitionBypassesTheGate(unittest.TestCase):
    """Tranca arquitetural. Hoje passa por vazio (a Fase 3 ainda nao esta neste
    repo); no dia em que o motor de TRI e a publicacao entrarem, reprova se o
    caminho esquecer a trava."""

    GUARD_FOR = {
        "CALIBRATED": "assert_ready_for_calibration",
        "PUBLISHED": "assert_ready_for_publication",
    }

    def test_the_guarded_transitions_match_the_spec(self):
        self.assertEqual(set(GUARDED_TRANSITIONS), set(self.GUARD_FOR))

    def test_the_blocking_statuses_are_the_fase_2_object_not_a_copy(self):
        # A constante tem UM dono (services/simulado_service.py, Fase 2). Duas
        # tuplas identicas passariam neste teste com `==`; `assertIs` exige que
        # seja o MESMO objeto, que e o que impede as duas travas de divergirem
        # no dia em que alguem acrescentar um status bloqueante de um lado so.
        from agente_ia_edu.services import simulado_service

        self.assertIs(BLOCKING_SCAN_STATUSES, simulado_service.BLOCKING_SCAN_STATUSES)

    def test_every_service_that_writes_a_guarded_status_imports_its_guard(self):
        offenders = []
        for path in sorted(SERVICES_DIR.glob("*.py")):
            if path.name == "mock_exam_scan_gate.py":
                continue
            source = path.read_text(encoding="utf-8")
            for status, guard in self.GUARD_FOR.items():
                if f'"{status}"' in source or f"'{status}'" in source:
                    if guard not in source:
                        offenders.append(f"{path.name}: escreve {status} sem {guard}")
        self.assertEqual(
            offenders, [],
            "estes services transicionam status protegido sem passar pela trava do §4:\n"
            + "\n".join(offenders),
        )


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_mock_exam_scan_gate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.services.mock_exam_scan_gate'`.

- [ ] **Step 3: Implementar**

`src/agente_ia_edu/services/mock_exam_scan_gate.py`:

```python
"""§4 do spec, "Trava de integridade": nada de calibrar com cartao pendente.

"Diferente de uma nota individual faltante, calibrar com parte dos alunos
ausente distorce os parametros dos itens - e portanto contamina a nota de
TODOS."

E o argumento inteiro da trava. Numa media simples, o aluno ausente so falta a
si mesmo. Numa calibragem de TRI, a dificuldade estimada de cada item sai da
matriz completa de respostas; faltando 8% dos respondentes - e faltando
justamente os cartoes que o leitor achou dificeis, que nao e uma amostra
aleatoria - todo `b` sai deslocado e toda nota publicada sai errada, sem que
nada quebre.

CORE: sem cv2, sem numpy. So SQLAlchemy.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AnswerCardScan

# BLOCKING_SCAN_STATUSES vem da Fase 2 (services/simulado_service.py), que entra
# antes e e a dona da constante. Redeclarar aqui uma tupla identica seria uma
# bomba-relogio: no dia em que alguem acrescentar um status bloqueante em um so
# dos lados, a trava passa a proteger a publicacao e nao a calibragem (ou o
# contrario), e as duas continuam VERDES. E a mesma divergencia silenciosa que
# levou o §2.1 a dar dono unico ao CardTemplate.
#
# O que a tupla significa, e que vale registrar aqui porque a Fase 2 nao explica:
# ela e ("PENDING", "NEEDS_REVIEW", "FAILED"), e o FAILED bloqueia pela mesma
# razao que os outros dois - um cartao que o leitor nao conseguiu processar e um
# aluno ausente da matriz de respostas, e a matriz e o insumo da calibragem. Um
# cartao so sai do caminho de duas formas: sendo lido, ou sendo digitado
# manualmente (§9, fase 2). Nunca sendo ignorado.
from .simulado_service import (
    BLOCKING_SCAN_STATUSES,
    STATUS_CALIBRATED,
    STATUS_PUBLISHED,
)

#: §4: "A trava vale para a transicao para CALIBRATED E para a transicao para
#: PUBLISHED. O efeito que ela protege e a publicacao; CALIBRATED e apenas onde
#: o dano se origina." Montada a partir das constantes da Fase 2, nao de
#: literais - um rename de status la quebra aqui em vez de passar batido.
GUARDED_TRANSITIONS: tuple[str, ...] = (STATUS_CALIBRATED, STATUS_PUBLISHED)

__all__ = [
    "BLOCKING_SCAN_STATUSES",
    "GUARDED_TRANSITIONS",
    "PendingScanReviewError",
    "ScanReadiness",
    "assert_ready_for_calibration",
    "assert_ready_for_publication",
    "scan_readiness",
]


@dataclass(frozen=True)
class ScanReadiness:
    mock_exam_id: uuid.UUID
    pending: int
    needs_review: int
    failed: int
    processed: int
    ready_for_calibration: bool

    @property
    def total(self) -> int:
        return self.pending + self.needs_review + self.failed + self.processed


class PendingScanReviewError(RuntimeError):
    """Transicao para CALIBRATED barrada. Carrega o `ScanReadiness` para a
    camada HTTP poder dizer ao operador exatamente o que falta."""

    def __init__(self, message: str, readiness: ScanReadiness) -> None:
        super().__init__(message)
        self.readiness = readiness


async def scan_readiness(session: AsyncSession, mock_exam_id: uuid.UUID) -> ScanReadiness:
    rows = (await session.execute(
        select(AnswerCardScan.status, func.count(AnswerCardScan.id))
        .where(AnswerCardScan.mock_exam_id == mock_exam_id)
        .group_by(AnswerCardScan.status)
    )).all()
    counts = {str(status): int(total) for status, total in rows}
    pending = counts.get("PENDING", 0)
    needs_review = counts.get("NEEDS_REVIEW", 0)
    failed = counts.get("FAILED", 0)
    processed = counts.get("PROCESSED", 0)
    ready = processed > 0 and pending == 0 and needs_review == 0 and failed == 0
    return ScanReadiness(
        mock_exam_id=mock_exam_id,
        pending=pending,
        needs_review=needs_review,
        failed=failed,
        processed=processed,
        ready_for_calibration=ready,
    )


async def _assert_ready(session: AsyncSession, mock_exam_id: uuid.UUID, target: str) -> None:
    readiness = await scan_readiness(session, mock_exam_id)
    if readiness.ready_for_calibration:
        return
    if readiness.total == 0:
        raise PendingScanReviewError(
            f"Este simulado ainda nao tem nenhum cartao digitalizado; nao da para ir para "
            f"{target}. Calibrar agora usaria uma matriz de respostas vazia.",
            readiness,
        )
    raise PendingScanReviewError(
        f"Ainda ha cartoes sem leitura concluida, entao o simulado nao pode ir para {target}: "
        f"{readiness.pending} na fila de leitura, {readiness.needs_review} aguardando "
        f"conferencia e {readiness.failed} com falha de leitura. Cada um deles e um aluno "
        "ausente da matriz de respostas - e calibrar com parte dos alunos ausente distorce "
        "os parametros dos itens e contamina a nota de TODOS. Cada cartao precisa ser lido "
        "ou digitado manualmente; nenhum pode ser ignorado.",
        readiness,
    )


async def assert_ready_for_calibration(session: AsyncSession, mock_exam_id: uuid.UUID) -> None:
    """Chamada obrigatoria antes de qualquer transicao para CALIBRATED."""
    await _assert_ready(session, mock_exam_id, STATUS_CALIBRATED)


async def assert_ready_for_publication(session: AsyncSession, mock_exam_id: uuid.UUID) -> None:
    """Chamada obrigatoria antes de qualquer transicao para PUBLISHED.

    A mesma trava nos dois pontos: CALIBRATED e onde o dano se origina, PUBLISHED
    e o efeito que precisa ser protegido. Uma calibragem pode ser refeita em
    silencio; uma nota publicada ja foi vista pelo aluno."""
    await _assert_ready(session, mock_exam_id, STATUS_PUBLISHED)
```

**Ponto de integração com as Fases 3 e 5 (fora do escopo desta fase):** quando as funções
que promovem o simulado a `CALIBRATED` e a `PUBLISHED` existirem, a primeira linha de cada
uma passa a ser, respectivamente:

```python
    await assert_ready_for_calibration(session, mock_exam_id)
```

```python
    await assert_ready_for_publication(session, mock_exam_id)
```

Localize-as com `grep -rn "CALIBRATED\|PUBLISHED" src/agente_ia_edu/services/`. O teste
`NoTransitionBypassesTheGate` acima reprova automaticamente qualquer service que escreva um
desses dois status sem importar a trava correspondente, então o esquecimento não passa em
silêncio.

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_mock_exam_scan_gate.py -v`
Expected: PASS nos 7 testes.

- [ ] **Step 5: Commit**

```bash
git add src/agente_ia_edu/services/mock_exam_scan_gate.py tests/test_mock_exam_scan_gate.py
git commit -m "feat(omr): §4 trava de integridade antes de CALIBRATED

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 18: Fila de conferência humana — service, schemas e rotas

**Files:**
- Create: `src/agente_ia_edu/services/omr_review.py`
- Create: `src/agente_ia_edu/api/schemas/mock_exam_review.py`
- Create: `src/agente_ia_edu/api/routes/mock_exam_review.py`
- Modify: `src/agente_ia_edu/api/app.py` (import + `include_router`, ao lado de
  `coordination_portal_router`, linhas 36 e 79)
- Test: `tests/test_mock_exam_review_http.py`

**Interfaces:**
- Consumes: `scan_readiness`/`ScanReadiness` (Task 17), `item_crop_path` (Task 11),
  `MARK_PENDING`/`MARK_HUMAN` (Task 14, como literais — o core não importa `omr_worker`),
  modelos da Fase 1, `get_current_authenticated_context`/`get_current_identity`/
  `get_session_factory` (`api/dependencies.py:208-221`), `AuthorizationService`.
- Produces:
  - `services/omr_review.py` (importa `VALID_OPTIONS` da Fase 2 e `MARK_*` da
    Task 14 — nenhuma das duas é redeclarada aqui): `ReviewItem`, `ReviewQueue`,
    `OmrReviewService` com
    `async list_queue(mock_exam_id, limit=50) -> ReviewQueue` e
    `async resolve_mark(mark_id, *, resolved_option, resolved_by_external_id) -> str`
    (devolve o novo status do scan); `MarkNotFoundError`, `MarkAlreadyResolvedError`,
    `InvalidOptionError`; `review_reason(intensities: list[float]) -> str`.
  - Rotas sob `/api/v1/coordination`:
    `GET /mock-exams/{mock_exam_id}/scan-readiness`,
    `GET /mock-exams/{mock_exam_id}/review-queue`,
    `GET /answer-card-marks/{mark_id}/crop`,
    `POST /answer-card-marks/{mark_id}/resolve`.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_mock_exam_review_http.py`:

```python
"""Fase 4 (OMR) Task 18 - a fila de conferencia humana, pelo HTTP real.

§5 do spec: "Qualquer incerteza (...) vira item na fila de conferencia, com o
RECORTE DA IMAGEM exibido para o operador decidir."

Segue o padrao de tests/test_coordination_portal_http.py: TestClient sobre um
router real, SQLite em memoria, dependencias sobrescritas por
`app.dependency_overrides`.

Os dois testes que carregam a regra:
  - a decisao do operador grava `resolution = HUMAN` com QUEM e QUANDO (§3), e
    e isso que torna uma nota contestada auditavel;
  - quando a ultima marca pendente de um scan e resolvida, o scan sai de
    NEEDS_REVIEW - e so entao a trava do §4 destrava.
"""

from __future__ import annotations

import asyncio
import tempfile
import unittest
import uuid as _uuid
from datetime import datetime, timezone
from pathlib import Path

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
from agente_ia_edu.api.routes.mock_exam_review import (
    get_material_storage,
    mock_exam_review_router,
)
from agente_ia_edu.db.base import Base
from agente_ia_edu.db.models import AnswerCard, AnswerCardMark, AnswerCardScan
from agente_ia_edu.identity import AuthenticatedUserContext, ExternalIdentityContext
from agente_ia_edu.services.material_storage import MaterialStorage
from agente_ia_edu.services.omr_artifacts import item_crop_path

IMAGE_HASH = "e" * 64


class MockExamReviewHTTP(unittest.TestCase):
    def setUp(self):
        self._tmp = tempfile.TemporaryDirectory()
        self.storage_root = Path(self._tmp.name)
        self.school_id = _uuid.uuid4()
        self.other_school_id = _uuid.uuid4()
        self.mock_exam_id = _uuid.uuid4()
        self.scan_id = _uuid.uuid4()
        self.pending_mark_id = _uuid.uuid4()
        self.auto_mark_id = _uuid.uuid4()

        async def build():
            engine = create_async_engine("sqlite+aiosqlite:///:memory:", poolclass=StaticPool)
            async with engine.begin() as connection:
                await connection.run_sync(Base.metadata.create_all)
            factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
            async with factory() as session:
                from agente_ia_edu.db.models import AcademicYear, MockExam, MockExamItem

                year = AcademicYear(id=_uuid.uuid4(), school_id=self.school_id, year=2026)
                session.add(year)
                await session.flush()
                session.add(MockExam(
                    id=self.mock_exam_id, school_id=self.school_id,
                    academic_year_id=year.id, name="Simulado ENEM 1o dia",
                    application_date=datetime.now(timezone.utc).date(),
                    exam_day=1, status="SCANNED"))
                # Gabarito das duas questoes que este cartao tem, para a
                # consolidacao poder resolver posicao -> item_id.
                for position, correct in ((7, "A"), (8, "B")):
                    session.add(MockExamItem(
                        id=_uuid.uuid4(), mock_exam_id=self.mock_exam_id, position=position,
                        area_code="MT", correct_option=correct, is_anchor=False))
                card_id = _uuid.uuid4()
                session.add(AnswerCard(
                    id=card_id, mock_exam_id=self.mock_exam_id, student_id=_uuid.uuid4(),
                    booklet_code="UNICO", qr_token="tok-1", template_version="v1",
                    printed_at=datetime.now(timezone.utc)))
                session.add(AnswerCardScan(
                    id=self.scan_id, mock_exam_id=self.mock_exam_id, answer_card_id=card_id,
                    image_hash=IMAGE_HASH, storage_path="/tmp/x.png", source="MOBILE",
                    status="NEEDS_REVIEW"))
                session.add(AnswerCardMark(
                    id=self.pending_mark_id, scan_id=self.scan_id, item_position=7,
                    detected_option=None, fill_intensities=[0.90, 0.05, 0.04, 0.88, 0.03],
                    confidence=0.2, resolution="PENDING"))
                session.add(AnswerCardMark(
                    id=self.auto_mark_id, scan_id=self.scan_id, item_position=8,
                    detected_option="B", fill_intensities=[0.03, 0.94, 0.02, 0.02, 0.01],
                    confidence=0.97, resolution="AUTO", resolved_option="B"))
                await session.commit()
            return engine, factory

        self.engine, self.factory = asyncio.run(build())

        app = FastAPI()
        app.include_router(mock_exam_review_router)
        app.dependency_overrides[get_session_factory] = lambda: self.factory
        app.dependency_overrides[get_current_identity] = lambda: ExternalIdentityContext(
            provider="test", external_user_id="coord-1", roles=("COORDINATOR",))
        app.dependency_overrides[get_current_authenticated_context] = lambda: AuthenticatedUserContext(
            user_id="coord-1", external_identity_id="coord-1", school_id=self.school_id,
            role="COORDINATOR")
        app.dependency_overrides[get_material_storage] = (
            lambda: MaterialStorage(root=self.storage_root))
        self.app = app
        self.client = TestClient(app)

    def tearDown(self):
        self._tmp.cleanup()

    def test_review_queue_lists_only_the_pending_marks(self):
        response = self.client.get(f"/api/v1/coordination/mock-exams/{self.mock_exam_id}/review-queue")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(len(body["items"]), 1)
        item = body["items"][0]
        self.assertEqual(item["mark_id"], str(self.pending_mark_id))
        self.assertEqual(item["item_position"], 7)
        self.assertEqual(item["fill_intensities"], [0.90, 0.05, 0.04, 0.88, 0.03])
        self.assertEqual(item["reason"], "DUPLA_MARCACAO")
        self.assertEqual(item["crop_url"],
                         f"/api/v1/coordination/answer-card-marks/{self.pending_mark_id}/crop")

    def test_the_queue_never_leaks_an_exam_from_another_school(self):
        self.app.dependency_overrides[get_current_authenticated_context] = (
            lambda: AuthenticatedUserContext(
                user_id="coord-2", external_identity_id="coord-2",
                school_id=self.other_school_id, role="COORDINATOR"))
        response = self.client.get(f"/api/v1/coordination/mock-exams/{self.mock_exam_id}/review-queue")
        self.assertEqual(response.status_code, 403)

    def test_scan_readiness_reports_the_block(self):
        response = self.client.get(f"/api/v1/coordination/mock-exams/{self.mock_exam_id}/scan-readiness")
        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertFalse(body["ready_for_calibration"])
        self.assertEqual(body["needs_review"], 1)
        self.assertIn("conferencia", body["blocking_message"].lower())

    def test_a_failed_scan_also_blocks_and_says_so(self):
        async def add_failed():
            async with self.factory() as session:
                session.add(AnswerCardScan(
                    id=_uuid.uuid4(), mock_exam_id=self.mock_exam_id, answer_card_id=None,
                    image_hash="f" * 64, storage_path="/tmp/f.png", source="MOBILE",
                    status="FAILED", failure_reason="ARUCO_NAO_DETECTADO: ..."))
                await session.commit()

        asyncio.run(add_failed())
        body = self.client.get(
            f"/api/v1/coordination/mock-exams/{self.mock_exam_id}/scan-readiness").json()
        self.assertEqual(body["failed"], 1)
        self.assertFalse(body["ready_for_calibration"])
        self.assertIn("falha de leitura", body["blocking_message"])

    def test_resolving_a_mark_records_who_and_when(self):
        response = self.client.post(
            f"/api/v1/coordination/answer-card-marks/{self.pending_mark_id}/resolve",
            json={"resolved_option": "A"})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["scan_status"], "PROCESSED")

        async def read():
            async with self.factory() as session:
                return await session.get(AnswerCardMark, self.pending_mark_id)

        mark = asyncio.run(read())
        self.assertEqual(mark.resolution, "HUMAN")
        self.assertEqual(mark.resolved_option, "A")
        self.assertEqual(mark.resolved_by_external_id, "coord-1")
        self.assertIsNotNone(mark.resolved_at)

    def test_resolving_the_last_pending_mark_consolidates_with_source_omr(self):
        self.client.post(
            f"/api/v1/coordination/answer-card-marks/{self.pending_mark_id}/resolve",
            json={"resolved_option": "A"})

        async def read():
            async with self.factory() as session:
                from agente_ia_edu.db.models import MockExamResponse

                return (await session.execute(select(MockExamResponse))).scalars().all()

        responses = asyncio.run(read())
        self.assertEqual(len(responses), 2)
        for response in responses:
            self.assertEqual(response.source, "OMR",
                             "decidir UMA bolha nao transforma o cartao em digitacao manual")
            self.assertIsNone(response.entered_by_external_id)

    def test_resolving_the_last_pending_mark_releases_the_scan(self):
        self.client.post(
            f"/api/v1/coordination/answer-card-marks/{self.pending_mark_id}/resolve",
            json={"resolved_option": "A"})

        async def read():
            async with self.factory() as session:
                return await session.get(AnswerCardScan, self.scan_id)

        self.assertEqual(asyncio.run(read()).status, "PROCESSED")
        readiness = self.client.get(
            f"/api/v1/coordination/mock-exams/{self.mock_exam_id}/scan-readiness").json()
        self.assertTrue(readiness["ready_for_calibration"])

    def test_the_operator_can_record_a_blank_answer(self):
        response = self.client.post(
            f"/api/v1/coordination/answer-card-marks/{self.pending_mark_id}/resolve",
            json={"resolved_option": None})
        self.assertEqual(response.status_code, 200)

        async def read():
            async with self.factory() as session:
                return await session.get(AnswerCardMark, self.pending_mark_id)

        mark = asyncio.run(read())
        self.assertEqual(mark.resolution, "HUMAN")
        self.assertIsNone(mark.resolved_option)

    def test_an_invalid_option_is_rejected(self):
        response = self.client.post(
            f"/api/v1/coordination/answer-card-marks/{self.pending_mark_id}/resolve",
            json={"resolved_option": "F"})
        self.assertEqual(response.status_code, 422)

    def test_resolving_an_already_auto_resolved_mark_is_refused(self):
        response = self.client.post(
            f"/api/v1/coordination/answer-card-marks/{self.auto_mark_id}/resolve",
            json={"resolved_option": "C"})
        self.assertEqual(response.status_code, 409)

    def test_an_unknown_mark_returns_404(self):
        response = self.client.post(
            f"/api/v1/coordination/answer-card-marks/{_uuid.uuid4()}/resolve",
            json={"resolved_option": "A"})
        self.assertEqual(response.status_code, 404)

    def test_the_crop_is_served_as_png_when_the_worker_wrote_it(self):
        storage = MaterialStorage(root=self.storage_root)
        path = item_crop_path(storage, IMAGE_HASH, 7)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(b"\x89PNG\r\n\x1a\n" + b"0" * 64)
        response = self.client.get(
            f"/api/v1/coordination/answer-card-marks/{self.pending_mark_id}/crop")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.headers["content-type"], "image/png")

    def test_a_missing_crop_returns_404_instead_of_a_broken_image(self):
        response = self.client.get(
            f"/api/v1/coordination/answer-card-marks/{self.pending_mark_id}/crop")
        self.assertEqual(response.status_code, 404)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_mock_exam_review_http.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'agente_ia_edu.api.routes.mock_exam_review'`.

- [ ] **Step 3: Implementar o service**

`src/agente_ia_edu/services/omr_review.py`:

```python
"""Fila de conferencia humana da leitura optica (§5 do spec).

"Um OMR que adivinha e pior que um que pede ajuda, porque o erro dele e
silencioso e vira nota." Este service e o "pedir ajuda".

CORE: sem cv2, sem numpy. As imagens ja foram recortadas pelo worker
(omr_worker/crops.py) e estao no MaterialStorage; aqui so se calcula o caminho
delas (services/omr_artifacts.py) e se grava a decisao do operador.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from ..db.models import AnswerCardMark, AnswerCardScan
from .omr_consolidation import MARK_HUMAN, MARK_PENDING, consolidate_scan
# As alternativas validas sao as do simulado inteiro, nao "as da tela de
# conferencia": a Fase 2 e a dona (services/simulado_service.py, no __all__ dela).
# Uma segunda tupla aqui divergiria em silencio no dia de um cartao de 4 ou de 6
# alternativas - a tela aceitaria uma letra que a digitacao manual recusa.
from .simulado_service import VALID_OPTIONS

REASON_MULTI_MARK = "DUPLA_MARCACAO"
REASON_AMBIGUOUS = "BOLHA_AMBIGUA"

#: Limiares do ROTULO exibido ao operador - nunca de uma decisao. A decisao ja
#: foi tomada pelo worker (com os limiares do proprio cartao) e foi "nao sei".
#: Aqui so se escolhe qual frase ajuda mais a pessoa a entender o que esta vendo.
_LABEL_STRONG = 0.35
_LABEL_TIE = 0.15


class MarkNotFoundError(RuntimeError):
    pass


class MarkAlreadyResolvedError(RuntimeError):
    pass


class InvalidOptionError(ValueError):
    pass


def review_reason(intensities: list[float]) -> str:
    """Rotulo legivel para a tela. Display-only, jamais decisao."""
    if not intensities:
        return REASON_AMBIGUOUS
    highest = max(intensities)
    near_top = [value for value in intensities if highest - value <= _LABEL_TIE]
    if highest >= _LABEL_STRONG and len(near_top) >= 2:
        return REASON_MULTI_MARK
    return REASON_AMBIGUOUS


@dataclass(frozen=True)
class ReviewItem:
    mark_id: uuid.UUID
    scan_id: uuid.UUID
    image_hash: str
    item_position: int
    fill_intensities: list[float]
    confidence: float
    reason: str


@dataclass(frozen=True)
class ReviewQueue:
    mock_exam_id: uuid.UUID
    total_pending: int
    items: list[ReviewItem]


class OmrReviewService:
    def __init__(self, session: AsyncSession) -> None:
        self.session = session

    async def list_queue(self, mock_exam_id: uuid.UUID, *, limit: int = 50) -> ReviewQueue:
        base = (
            select(AnswerCardMark, AnswerCardScan)
            .join(AnswerCardScan, AnswerCardScan.id == AnswerCardMark.scan_id)
            .where(
                AnswerCardScan.mock_exam_id == mock_exam_id,
                AnswerCardMark.resolution == MARK_PENDING,
            )
        )
        total = len((await self.session.execute(base)).all())
        rows = (await self.session.execute(
            base.order_by(AnswerCardScan.id, AnswerCardMark.item_position).limit(limit)
        )).all()
        items = [
            ReviewItem(
                mark_id=mark.id,
                scan_id=scan.id,
                image_hash=scan.image_hash,
                item_position=mark.item_position,
                fill_intensities=[float(v) for v in (mark.fill_intensities or [])],
                confidence=float(mark.confidence or 0.0),
                reason=review_reason([float(v) for v in (mark.fill_intensities or [])]),
            )
            for mark, scan in rows
        ]
        return ReviewQueue(mock_exam_id=mock_exam_id, total_pending=total, items=items)

    async def get_mark_with_scan(
        self, mark_id: uuid.UUID
    ) -> tuple[AnswerCardMark, AnswerCardScan]:
        row = (await self.session.execute(
            select(AnswerCardMark, AnswerCardScan)
            .join(AnswerCardScan, AnswerCardScan.id == AnswerCardMark.scan_id)
            .where(AnswerCardMark.id == mark_id)
        )).first()
        if row is None:
            raise MarkNotFoundError(f"marca {mark_id} nao encontrada")
        return row[0], row[1]

    async def resolve_mark(
        self,
        mark_id: uuid.UUID,
        *,
        resolved_option: str | None,
        resolved_by_external_id: str,
    ) -> str:
        """Grava a decisao humana e devolve o novo status do scan."""
        if resolved_option is not None:
            normalized = str(resolved_option).strip().upper()
            if normalized not in VALID_OPTIONS:
                raise InvalidOptionError(
                    f"alternativa {resolved_option!r} invalida; use "
                    f"{', '.join(sorted(VALID_OPTIONS))} ou nulo para registrar questao "
                    "em branco"
                )
            resolved_option = normalized

        mark, scan = await self.get_mark_with_scan(mark_id)
        if mark.resolution != MARK_PENDING:
            raise MarkAlreadyResolvedError(
                f"a questao {mark.item_position} ja esta resolvida como {mark.resolution}; "
                "reabrir uma leitura ja fechada nao e possivel por esta tela"
            )

        mark.resolution = MARK_HUMAN
        mark.resolved_option = resolved_option
        mark.resolved_by_external_id = resolved_by_external_id
        mark.resolved_at = datetime.now(timezone.utc)
        await self.session.flush()

        still_pending = (await self.session.execute(
            select(AnswerCardMark.id).where(
                AnswerCardMark.scan_id == scan.id,
                AnswerCardMark.resolution == MARK_PENDING,
            ).limit(1)
        )).first()
        if still_pending is None and scan.status == "NEEDS_REVIEW":
            scan.status = "PROCESSED"
            await self.session.flush()
            # Ultima duvida resolvida: o cartao vira resposta agora. A
            # procedencia continua sendo OMR - a pessoa decidiu UMA bolha, nao
            # digitou o cartao. Quem digitou o cartao inteiro entra por
            # `source='MANUAL'`, pelo caminho da fase 2.
            await consolidate_scan(self.session, scan.id)
        await self.session.flush()
        await self.session.commit()
        return scan.status
```

- [ ] **Step 4: Implementar os schemas**

`src/agente_ia_edu/api/schemas/mock_exam_review.py`:

```python
"""Pydantic da fila de conferencia da leitura optica (Fase 4)."""

from __future__ import annotations

from uuid import UUID

from pydantic import BaseModel, Field


class ReviewQueueItem(BaseModel):
    mark_id: UUID
    scan_id: UUID
    item_position: int
    fill_intensities: list[float] = Field(
        description="As cinco intensidades medidas (A-E), preservadas para auditoria")
    confidence: float
    reason: str = Field(description="DUPLA_MARCACAO ou BOLHA_AMBIGUA - rotulo de tela")
    crop_url: str = Field(description="PNG do recorte da linha da questao")


class ReviewQueueResponse(BaseModel):
    mock_exam_id: UUID
    total_pending: int
    items: list[ReviewQueueItem]


class ScanReadinessResponse(BaseModel):
    mock_exam_id: UUID
    pending: int
    needs_review: int
    failed: int
    processed: int
    ready_for_calibration: bool
    blocking_message: str | None = None


class ResolveMarkRequest(BaseModel):
    resolved_option: str | None = Field(
        default=None,
        description="A-E, ou nulo para registrar a questao como em branco")


class ResolveMarkResponse(BaseModel):
    mark_id: UUID
    resolved_option: str | None
    scan_status: str
```

- [ ] **Step 5: Implementar as rotas**

`src/agente_ia_edu/api/routes/mock_exam_review.py`:

```python
"""Rotas da fila de conferencia humana da leitura optica (Fase 4).

CORE: sem cv2. O PNG servido aqui foi recortado pelo worker e gravado no
MaterialStorage; esta rota so calcula o caminho e devolve o arquivo - o mesmo
padrao de `FileResponse` ja usado em api/routes/essay_submissions.py:696.

Prefixo `/api/v1/coordination` porque e a coordenacao que aplica e corrige o
simulado; o escopo e checado contra `mock_exams.school_id`, como
api/routes/question_extraction.py:83-97 faz contra `run.school_id`.
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import FileResponse

from ..dependencies import get_current_authenticated_context, get_session_factory
from ..schemas.mock_exam_review import (
    ResolveMarkRequest,
    ResolveMarkResponse,
    ReviewQueueItem,
    ReviewQueueResponse,
    ScanReadinessResponse,
)
from ...db.models import MockExam
from ...identity import AuthenticatedUserContext
from ...services.material_storage import MaterialStorage
from ...services.mock_exam_scan_gate import (
    PendingScanReviewError,
    assert_ready_for_calibration,
    scan_readiness,
)
from ...services.omr_artifacts import item_crop_path
from ...services.omr_review import (
    InvalidOptionError,
    MarkAlreadyResolvedError,
    MarkNotFoundError,
    OmrReviewService,
)

mock_exam_review_router = APIRouter(
    prefix="/api/v1/coordination",
    tags=["mock-exam-omr-review"],
)


def get_material_storage() -> MaterialStorage:
    return MaterialStorage()


async def _load_exam_in_scope(session, mock_exam_id: UUID, context: AuthenticatedUserContext):
    exam = await session.get(MockExam, mock_exam_id)
    if exam is None:
        raise HTTPException(status_code=404, detail="Simulado nao encontrado.")
    if getattr(context, "is_platform_admin", False):
        return exam
    if context.school_id is None or str(context.school_id) != str(exam.school_id):
        raise HTTPException(status_code=403, detail="Este simulado esta fora do seu escopo.")
    return exam


def _crop_url(mark_id) -> str:
    return f"/api/v1/coordination/answer-card-marks/{mark_id}/crop"


@mock_exam_review_router.get(
    "/mock-exams/{mock_exam_id}/scan-readiness",
    response_model=ScanReadinessResponse,
    summary="Quantos cartoes ainda travam a calibragem deste simulado (§4 do spec)",
)
async def get_scan_readiness(
    mock_exam_id: UUID,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> ScanReadinessResponse:
    async with session_factory() as session:
        await _load_exam_in_scope(session, mock_exam_id, context)
        readiness = await scan_readiness(session, mock_exam_id)
        message = None
        if not readiness.ready_for_calibration:
            try:
                await assert_ready_for_calibration(session, mock_exam_id)
            except PendingScanReviewError as exc:
                message = str(exc)
        return ScanReadinessResponse(
            mock_exam_id=mock_exam_id,
            pending=readiness.pending,
            needs_review=readiness.needs_review,
            failed=readiness.failed,
            processed=readiness.processed,
            ready_for_calibration=readiness.ready_for_calibration,
            blocking_message=message,
        )


@mock_exam_review_router.get(
    "/mock-exams/{mock_exam_id}/review-queue",
    response_model=ReviewQueueResponse,
    summary="Questoes que o leitor nao decidiu e que esperam um operador",
)
async def get_review_queue(
    mock_exam_id: UUID,
    limit: int = Query(default=50, ge=1, le=200),
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> ReviewQueueResponse:
    async with session_factory() as session:
        await _load_exam_in_scope(session, mock_exam_id, context)
        queue = await OmrReviewService(session).list_queue(mock_exam_id, limit=limit)
        return ReviewQueueResponse(
            mock_exam_id=mock_exam_id,
            total_pending=queue.total_pending,
            items=[
                ReviewQueueItem(
                    mark_id=item.mark_id,
                    scan_id=item.scan_id,
                    item_position=item.item_position,
                    fill_intensities=item.fill_intensities,
                    confidence=item.confidence,
                    reason=item.reason,
                    crop_url=_crop_url(item.mark_id),
                )
                for item in queue.items
            ],
        )


@mock_exam_review_router.get(
    "/answer-card-marks/{mark_id}/crop",
    summary="PNG do recorte da linha da questao, para o operador decidir",
)
async def get_mark_crop(
    mark_id: UUID,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
    storage: MaterialStorage = Depends(get_material_storage),
) -> FileResponse:
    async with session_factory() as session:
        service = OmrReviewService(session)
        try:
            mark, scan = await service.get_mark_with_scan(mark_id)
        except MarkNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await _load_exam_in_scope(session, scan.mock_exam_id, context)
        path = item_crop_path(storage, scan.image_hash, mark.item_position)
        if not path.is_file():
            raise HTTPException(
                status_code=404,
                detail="O recorte desta questao ainda nao foi gerado pelo leitor.",
            )
        return FileResponse(str(path), media_type="image/png")


@mock_exam_review_router.post(
    "/answer-card-marks/{mark_id}/resolve",
    response_model=ResolveMarkResponse,
    summary="Registra a decisao do operador (resolution = HUMAN)",
)
async def resolve_mark(
    mark_id: UUID,
    payload: ResolveMarkRequest,
    context: AuthenticatedUserContext = Depends(get_current_authenticated_context),
    session_factory=Depends(get_session_factory),
) -> ResolveMarkResponse:
    async with session_factory() as session:
        service = OmrReviewService(session)
        try:
            _mark, scan = await service.get_mark_with_scan(mark_id)
        except MarkNotFoundError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc
        await _load_exam_in_scope(session, scan.mock_exam_id, context)
        try:
            scan_status = await service.resolve_mark(
                mark_id,
                resolved_option=payload.resolved_option,
                resolved_by_external_id=context.external_identity_id or context.user_id,
            )
        except InvalidOptionError as exc:
            raise HTTPException(status_code=422, detail=str(exc)) from exc
        except MarkAlreadyResolvedError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return ResolveMarkResponse(
            mark_id=mark_id,
            resolved_option=payload.resolved_option,
            scan_status=scan_status,
        )
```

- [ ] **Step 6: Registrar o router no app**

Em `src/agente_ia_edu/api/app.py`, junto do import de `coordination_portal_router`
(linha 36):

```python
from .routes.mock_exam_review import mock_exam_review_router
```

e junto do `include_router` correspondente (linha 79):

```python
    app.include_router(mock_exam_review_router, dependencies=reception_only_guard)
```

- [ ] **Step 7: Rodar e ver passar**

Run: `python -m pytest tests/test_mock_exam_review_http.py tests/test_omr_import_boundary.py -v`
Expected: PASS nos 11 + 5 testes. A fronteira roda junto porque este passo acrescenta
módulos ao grafo de import do `create_app()`.

- [ ] **Step 8: Commit**

```bash
git add src/agente_ia_edu/services/omr_review.py \
        src/agente_ia_edu/api/schemas/mock_exam_review.py \
        src/agente_ia_edu/api/routes/mock_exam_review.py \
        src/agente_ia_edu/api/app.py tests/test_mock_exam_review_http.py
git commit -m "feat(omr): fila de conferencia humana (service, schemas e rotas)

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 19: Tela de conferência no portal da coordenação

**Files:**
- Modify: `src/agente_ia_edu/web/coordination.html` (nav item depois de
  `data-view="study-sessions"`, linha 53; `<section>` depois de `view-study-sessions`, linha 367)
- Modify: `src/agente_ia_edu/web/coordination.js` (`titleMap` na linha ~87,
  `loadCurrentView` na linha ~104, bloco novo de funções no fim do arquivo)
- Modify: `src/agente_ia_edu/web/coordination.css` (bloco novo no fim)
- Test: `tests/test_omr_review_frontend.js`

**Interfaces:**
- Consumes: `GET /api/v1/coordination/mock-exams/{id}/scan-readiness`,
  `GET /api/v1/coordination/mock-exams/{id}/review-queue`,
  `GET /api/v1/coordination/answer-card-marks/{id}/crop`,
  `POST /api/v1/coordination/answer-card-marks/{id}/resolve` (Task 18).
- Produces: a view `omr-review` do portal da coordenação.

**Nota de escopo:** o simulado é informado por campo de texto (`#omr-exam-id`), e não
por um `<select>`, porque a Fase 4 não tem endpoint de listagem de simulados — ele
pertence à Fase 1/2. Trocar o campo por um select é uma linha quando aquele endpoint existir.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_omr_review_frontend.js`:

```javascript
/**
 * Fase 4 (OMR) Task 19 - tela de conferencia humana no portal da coordenacao.
 *
 * Assercoes estaticas sobre os arquivos entregues (node:test + regex), no mesmo
 * estilo de tests/test_material_assignment_student_materials_view_frontend.js.
 *
 * O que estes testes protegem, alem da fiacao: a tela NUNCA pode pre-selecionar
 * uma alternativa nem sugerir "a mais escura". §5 do spec - o operador decide
 * olhando o recorte, e a tela existe justamente porque a maquina nao decidiu.
 */

const test = require('node:test');
const assert = require('node:assert');
const fs = require('node:fs');
const path = require('node:path');

const WEB = path.join(__dirname, '..', 'src', 'agente_ia_edu', 'web');
const html = fs.readFileSync(path.join(WEB, 'coordination.html'), 'utf8');
const js = fs.readFileSync(path.join(WEB, 'coordination.js'), 'utf8');
const css = fs.readFileSync(path.join(WEB, 'coordination.css'), 'utf8');

const _start = js.indexOf('async function loadOmrReviewQueue()');
const OMR_BLOCK = _start >= 0 ? js.slice(_start) : '';

test('the nav item and the view panel exist', () => {
  assert.match(html, /data-view="omr-review"/);
  assert.match(html, /id="view-omr-review"/);
});

test('the view has the exam input, the readiness banner and the queue container', () => {
  assert.match(html, /id="omr-exam-id"/);
  assert.match(html, /id="omr-readiness"/);
  assert.match(html, /id="omr-queue"/);
});

test('the view is wired into switchView and loadCurrentView', () => {
  assert.match(js, /'omr-review':\s*\{\s*title:/);
  assert.match(js, /state\.currentView === 'omr-review'/);
});

test('the queue block exists and is substantial', () => {
  assert.ok(_start >= 0, 'loadOmrReviewQueue deveria existir');
  assert.ok(OMR_BLOCK.length > 800);
});

test('it calls the real readiness and review-queue endpoints', () => {
  assert.match(OMR_BLOCK, /\/api\/v1\/coordination\/mock-exams\/\$\{[^}]+\}\/scan-readiness/);
  assert.match(OMR_BLOCK, /\/api\/v1\/coordination\/mock-exams\/\$\{[^}]+\}\/review-queue/);
});

test('every queue item renders the crop image the API returned', () => {
  assert.match(OMR_BLOCK, /<img[^>]*src="\$\{item\.crop_url\}"/);
});

test('the five measured intensities are shown, never hidden behind a verdict', () => {
  assert.match(OMR_BLOCK, /item\.fill_intensities/);
});

test('the option buttons are never pre-selected and never suggest an answer', () => {
  assert.doesNotMatch(OMR_BLOCK, /checked/);
  assert.doesNotMatch(OMR_BLOCK, /selected="selected"/);
  assert.doesNotMatch(OMR_BLOCK, /sugerid|provavel|mais escura/i);
});

test('the operator can record a blank answer explicitly', () => {
  assert.match(OMR_BLOCK, /data-option=""/);
  assert.match(OMR_BLOCK, /Em branco/);
});

test('resolving posts to the real endpoint with the chosen option', () => {
  assert.match(OMR_BLOCK, /\/api\/v1\/coordination\/answer-card-marks\/\$\{[^}]+\}\/resolve/);
  assert.match(OMR_BLOCK, /method:\s*'POST'/);
  assert.match(OMR_BLOCK, /resolved_option/);
});

test('the readiness banner states the block instead of hiding it', () => {
  assert.match(OMR_BLOCK, /ready_for_calibration/);
  assert.match(OMR_BLOCK, /blocking_message/);
});

test('an empty queue is reported honestly, not as a blank screen', () => {
  assert.match(OMR_BLOCK, /Nenhuma questao aguardando conferencia|Nenhuma questão aguardando conferência/);
});

test('the css for the crop and the option row exists', () => {
  assert.match(css, /\.omr-crop/);
  assert.match(css, /\.omr-options/);
});
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `node --test tests/test_omr_review_frontend.js`
Expected: FAIL — vários `assert.match` reprovando (`data-view="omr-review"` ausente) e
`loadOmrReviewQueue deveria existir`.

- [ ] **Step 3: Acrescentar o item de menu e a seção ao HTML**

Em `src/agente_ia_edu/web/coordination.html`, depois do botão
`data-view="study-sessions"` (linha 53):

```html
        <button class="nav-item" data-view="omr-review" title="Conferir as questões que o leitor óptico não conseguiu decidir">
          <span class="nav-icon">🔍</span>
          <span class="nav-label">Conferência de Cartões</span>
        </button>
```

E depois do fechamento de `<section id="view-study-sessions">` (linha 367 em diante):

```html
        <section id="view-omr-review" class="view-panel">
          <div class="card">
            <div class="card-header">
              <h3>🔍 Conferência de cartões-resposta</h3>
            </div>
            <p class="empty-text">
              O leitor óptico nunca chuta: toda questão em que ele ficou em dúvida chega aqui,
              com o recorte da folha, para uma pessoa decidir.
            </p>
            <div class="form-group">
              <label for="omr-exam-id">Identificador do simulado</label>
              <input type="text" id="omr-exam-id" class="text-input"
                     placeholder="cole aqui o id do simulado" autocomplete="off">
              <button class="btn btn-secondary" type="button" id="omr-refresh">Carregar</button>
            </div>
            <div id="omr-readiness" class="omr-readiness"></div>
          </div>

          <div class="card">
            <div class="card-header">
              <h3>Questões aguardando decisão</h3>
            </div>
            <div id="omr-queue">
              <p class="empty-text">Informe o simulado para carregar a fila.</p>
            </div>
          </div>
        </section>
```

- [ ] **Step 4: Acrescentar a view ao `titleMap` e ao `loadCurrentView`**

Em `src/agente_ia_edu/web/coordination.js`, dentro de `titleMap` (ao lado de
`'study-sessions'`, linha ~87):

```javascript
      'omr-review': { title: 'Conferência de Cartões', sub: 'Questões que a leitura óptica não decidiu e esperam uma pessoa' },
```

E em `loadCurrentView` (linha ~104, depois de `initStudySessionsView`):

```javascript
    if (state.currentView === 'omr-review') initOmrReviewView();
```

- [ ] **Step 5: Acrescentar o bloco de funções ao fim de `coordination.js`**

Dentro do mesmo `DOMContentLoaded`, no fim do arquivo (antes do fechamento `});`):

```javascript
  // ==========================================================================
  // Fase 4 (OMR) - fila de conferencia humana.
  // A tela existe porque o leitor NAO decidiu (§5 do spec). Por isso ela nunca
  // pre-seleciona alternativa, nunca ordena as opcoes por intensidade e nunca
  // diz "provavelmente a C": mostra o recorte da folha e as cinco intensidades
  // medidas, e espera a pessoa.
  // ==========================================================================

  function initOmrReviewView() {
    const input = document.getElementById('omr-exam-id');
    const refresh = document.getElementById('omr-refresh');
    if (!refresh.dataset.wired) {
      refresh.dataset.wired = '1';
      refresh.addEventListener('click', () => loadOmrReviewQueue());
      input.addEventListener('keydown', (e) => { if (e.key === 'Enter') loadOmrReviewQueue(); });
      document.getElementById('omr-queue').addEventListener('click', (e) => {
        const button = e.target.closest('button[data-mark-id]');
        if (button) resolveOmrMark(button.dataset.markId, button.dataset.option);
      });
    }
    if (input.value.trim()) loadOmrReviewQueue();
  }

  async function loadOmrReviewQueue() {
    const examId = document.getElementById('omr-exam-id').value.trim();
    const queueBox = document.getElementById('omr-queue');
    const readinessBox = document.getElementById('omr-readiness');
    if (!examId) {
      queueBox.innerHTML = '<p class="empty-text">Informe o simulado para carregar a fila.</p>';
      readinessBox.innerHTML = '';
      return;
    }
    const headers = { 'Authorization': `Bearer ${state.coordinatorId}` };
    try {
      const [readinessRes, queueRes] = await Promise.all([
        fetch(`/api/v1/coordination/mock-exams/${encodeURIComponent(examId)}/scan-readiness`, { headers }),
        fetch(`/api/v1/coordination/mock-exams/${encodeURIComponent(examId)}/review-queue`, { headers }),
      ]);
      if (readinessRes.status === 403 || queueRes.status === 403) {
        readinessBox.innerHTML = '<p class="empty-text text-danger">⚠️ Este simulado está fora do seu escopo.</p>';
        queueBox.innerHTML = '';
        return;
      }
      if (!readinessRes.ok || !queueRes.ok) throw new Error('falha ao carregar');
      const readiness = await readinessRes.json();
      const queue = await queueRes.json();
      renderOmrReadiness(readiness);
      renderOmrQueue(queue);
    } catch (err) {
      console.warn('OMR review error:', err);
      queueBox.innerHTML = '<p class="empty-text">Não foi possível carregar a fila agora.</p>';
    }
  }

  function renderOmrReadiness(readiness) {
    const box = document.getElementById('omr-readiness');
    const badge = readiness.ready_for_calibration
      ? '<span class="badge badge-success">Pronto para calibrar</span>'
      : '<span class="badge badge-warning">Calibragem bloqueada</span>';
    box.innerHTML = `
      ${badge}
      <ul class="omr-readiness-counts">
        <li>Lidos: <strong>${readiness.processed}</strong></li>
        <li>Na fila de leitura: <strong>${readiness.pending}</strong></li>
        <li>Aguardando conferência: <strong>${readiness.needs_review}</strong></li>
        <li>Com falha: <strong>${readiness.failed}</strong></li>
      </ul>
      ${readiness.blocking_message ? `<p class="empty-text text-danger">${readiness.blocking_message}</p>` : ''}
    `;
  }

  function renderOmrQueue(queue) {
    const box = document.getElementById('omr-queue');
    if (!queue.items.length) {
      box.innerHTML = '<p class="empty-text">Nenhuma questão aguardando conferência neste simulado.</p>';
      return;
    }
    const options = ['A', 'B', 'C', 'D', 'E'];
    box.innerHTML = `
      <p class="empty-text">${queue.total_pending} questão(ões) aguardando decisão.</p>
      ${queue.items.map((item) => `
        <div class="card omr-item" data-item-card="${item.mark_id}">
          <div class="omr-item-header">
            <strong>Questão ${item.item_position}</strong>
            <span class="badge">${item.reason === 'DUPLA_MARCACAO' ? 'Dupla marcação' : 'Bolha ambígua'}</span>
          </div>
          <img class="omr-crop" src="${item.crop_url}" alt="Recorte da questão ${item.item_position}">
          <p class="omr-intensities">Intensidades medidas (A–E):
            ${item.fill_intensities.map((v, i) => `${options[i]}=${v.toFixed(2)}`).join(' · ')}
          </p>
          <div class="omr-options">
            ${options.map((option) => `
              <button class="btn btn-secondary" type="button"
                      data-mark-id="${item.mark_id}" data-option="${option}">${option}</button>
            `).join('')}
            <button class="btn btn-secondary" type="button"
                    data-mark-id="${item.mark_id}" data-option="">Em branco</button>
          </div>
        </div>
      `).join('')}
    `;
  }

  async function resolveOmrMark(markId, option) {
    try {
      const res = await fetch(`/api/v1/coordination/answer-card-marks/${markId}/resolve`, {
        method: 'POST',
        headers: {
          'Authorization': `Bearer ${state.coordinatorId}`,
          'Content-Type': 'application/json',
        },
        body: JSON.stringify({ resolved_option: option === '' ? null : option }),
      });
      if (!res.ok) throw new Error(`HTTP ${res.status}`);
      const card = document.querySelector(`[data-item-card="${markId}"]`);
      if (card) card.remove();
      await loadOmrReviewQueue();
    } catch (err) {
      console.warn('OMR resolve error:', err);
      showAlert('Não foi possível registrar a decisão. Tente novamente.', 'danger');
    }
  }
```

> `showAlert` e `hideAlert` já existem neste arquivo (usados por `switchView`, linha 59) —
> confirme a assinatura com `grep -n "function showAlert" src/agente_ia_edu/web/coordination.js`
> e ajuste os argumentos se ela for diferente de `(mensagem, tipo)`.

- [ ] **Step 6: Acrescentar o CSS**

No fim de `src/agente_ia_edu/web/coordination.css`:

```css
/* Fase 4 (OMR) - fila de conferencia humana */
.omr-readiness-counts { list-style: none; padding: 0; margin: 8px 0 0; display: flex; gap: 18px; flex-wrap: wrap; }
.omr-item { margin-top: 12px; }
.omr-item-header { display: flex; align-items: center; justify-content: space-between; gap: 12px; }
.omr-crop { display: block; width: 100%; max-width: 640px; margin: 10px 0; border: 1px solid var(--border-color, #d8dce3); border-radius: 6px; image-rendering: pixelated; background: #fff; }
.omr-intensities { font-family: ui-monospace, SFMono-Regular, Menlo, monospace; font-size: 0.85rem; color: var(--text-muted, #667085); }
.omr-options { display: flex; gap: 8px; flex-wrap: wrap; margin-top: 8px; }
.omr-options .btn { min-width: 52px; }
```

- [ ] **Step 7: Rodar e ver passar**

Run: `node --test tests/test_omr_review_frontend.js`
Expected: PASS nos 13 testes.

- [ ] **Step 8: Conferir que os testes de frontend já existentes continuam verdes**

Run: `node --test tests/test_*frontend*.js`
Expected: nenhuma regressão (os arquivos existentes de `coordination.js` continuam passando).

- [ ] **Step 9: Commit**

```bash
git add src/agente_ia_edu/web/coordination.html src/agente_ia_edu/web/coordination.js \
        src/agente_ia_edu/web/coordination.css tests/test_omr_review_frontend.js
git commit -m "feat(omr): tela de conferencia humana no portal da coordenacao

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

---

### Task 20: §8.3 — aceite físico (portão de fase, não teste unitário)

**Files:**
- Create: `docs/superpowers/acceptance/2026-09-29-omr-aceite-fisico.md`
- Create: `scripts/omr_physical_acceptance.py`
- Test: `tests/test_omr_physical_acceptance_script.py`

**Interfaces:**
- Consumes: `AnswerCard`, `AnswerCardScan`, `AnswerCardMark` (Fase 1), `MARK_HUMAN`/
  `MARK_AUTO` (Task 18).
- Produces:
  - `compare_readings(automatic: dict, manual: dict) -> AcceptanceReport`
  - `AcceptanceReport(total_items, auto_resolved, disagreements, review_items, blank_agreements, …)`
    com `.passed` e `.summary()`
  - `ACCEPTANCE_CRITERIA: dict[str, float]`

**Este não é um teste que se roda numa tarde.** O §8.3 do spec:
*"A fase de OMR não está pronta sem essa evidência. Este ciclo é físico e não comprime
por esforço de engenharia; o plano de implementação precisa reservar tempo de calendário
para ele."* Imprimir, aplicar, preencher à mão, fotografar com aparelhos diferentes e
digitar duas vezes 30 cartões leva **no mínimo 5 dias úteis de calendário**, e nenhuma
otimização de código encurta isso. As Tasks 1–19 podem estar 100% verdes e a Fase 4
continua **não entregue** até este relatório existir e passar.

- [ ] **Step 1: Escrever o teste que falha**

`tests/test_omr_physical_acceptance_script.py`:

```python
"""Fase 4 (OMR) Task 20 - a ARITMETICA do aceite fisico (§8.3 do spec).

O aceite em si e fisico e nao roda em CI. O que roda aqui e a funcao de
comparacao que produz o veredito, testada com casos montados a mao - porque um
relatorio de aceite que conta errado e pior que nenhum: ele aprova.

O criterio nao-negociavel: UMA divergencia entre resposta auto-resolvida e
digitacao manual REPROVA. Nao ha "99,9% e bom o suficiente" para uma resposta
que o sistema afirmou ter lido - ela vira nota. Itens mandados para conferencia
NAO contam como erro; contam para o teto de esforco operacional.
"""

from __future__ import annotations

import unittest

from scripts.omr_physical_acceptance import ACCEPTANCE_CRITERIA, compare_readings


def automatic(**items):
    return {int(k[1:]): v for k, v in items.items()}


class AcceptanceArithmetic(unittest.TestCase):
    def test_a_perfect_run_passes(self):
        auto = {p: ("AUTO", "C") for p in range(1, 91)}
        manual = {p: "C" for p in range(1, 91)}
        report = compare_readings(auto, manual)
        self.assertTrue(report.passed)
        self.assertEqual(report.disagreements, [])
        self.assertEqual(report.auto_resolved, 90)

    def test_one_single_wrong_auto_answer_fails_the_whole_acceptance(self):
        auto = {p: ("AUTO", "C") for p in range(1, 91)}
        auto[42] = ("AUTO", "D")
        manual = {p: "C" for p in range(1, 91)}
        report = compare_readings(auto, manual)
        self.assertFalse(report.passed)
        self.assertEqual([d[0] for d in report.disagreements], [42])

    def test_items_sent_to_review_are_not_errors(self):
        auto = {p: ("AUTO", "C") for p in range(1, 91)}
        for p in (5, 6, 7):
            auto[p] = ("REVIEW", None)
        manual = {p: "C" for p in range(1, 91)}
        report = compare_readings(auto, manual)
        self.assertTrue(report.passed)
        self.assertEqual(report.review_items, 3)

    def test_too_many_review_items_fails_on_operational_cost(self):
        auto = {p: ("REVIEW", None) for p in range(1, 91)}
        manual = {p: "C" for p in range(1, 91)}
        report = compare_readings(auto, manual)
        self.assertFalse(report.passed)
        self.assertIn("conferencia", report.summary().lower())

    def test_a_blank_read_as_blank_agrees(self):
        auto = {1: ("AUTO", None)}
        manual = {1: None}
        report = compare_readings(auto, manual)
        self.assertTrue(report.passed)
        self.assertEqual(report.blank_agreements, 1)

    def test_a_blank_read_as_an_answer_is_a_disagreement(self):
        auto = {1: ("AUTO", "B")}
        manual = {1: None}
        report = compare_readings(auto, manual)
        self.assertFalse(report.passed)

    def test_an_answer_read_as_blank_is_a_disagreement(self):
        auto = {1: ("AUTO", None)}
        manual = {1: "B"}
        report = compare_readings(auto, manual)
        self.assertFalse(report.passed)

    def test_an_item_missing_from_the_manual_typing_is_reported_not_ignored(self):
        auto = {1: ("AUTO", "A"), 2: ("AUTO", "B")}
        manual = {1: "A"}
        report = compare_readings(auto, manual)
        self.assertFalse(report.passed)
        self.assertIn("2", report.summary())

    def test_the_criteria_are_explicit_numbers_not_vibes(self):
        self.assertEqual(ACCEPTANCE_CRITERIA["max_disagreements"], 0)
        self.assertLessEqual(ACCEPTANCE_CRITERIA["max_review_rate"], 0.10)


if __name__ == "__main__":
    unittest.main()
```

- [ ] **Step 2: Rodar e ver falhar**

Run: `python -m pytest tests/test_omr_physical_acceptance_script.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'scripts.omr_physical_acceptance'`.

- [ ] **Step 3: Implementar o script**

`scripts/omr_physical_acceptance.py`:

```python
"""§8.3 do spec - aceite fisico da leitura optica.

Compara a leitura automatica gravada em `answer_card_marks` contra a digitacao
manual das MESMAS folhas, e emite o veredito da fase.

    python scripts/omr_physical_acceptance.py --mock-exam <uuid> --manual digitacao.csv

O CSV da digitacao manual tem tres colunas, sem cabecalho opcional:

    qr_token,item_position,resposta

`resposta` e A-E, ou vazio para questao em branco.

CRITERIOS (ACCEPTANCE_CRITERIA):

  max_disagreements = 0
      Uma unica divergencia entre resposta AUTO-RESOLVIDA e digitacao manual
      reprova. Nao existe taxa aceitavel de erro para uma resposta que o sistema
      AFIRMOU ter lido: ela vira nota de aluno.

  max_review_rate = 0.10
      Ate 10% dos itens podem ir para conferencia humana. Acima disso o sistema
      funciona mas nao serve: 1.500 alunos x 90 questoes x 10% ja e 13.500
      decisoes manuais.

  min_scans_read = 0.98
      Pelo menos 98% das folhas capturadas precisam ser lidas (nao FAILED).

Este script nao roda em CI e nao substitui teste - ele fecha (ou nao fecha) a
fase, com numeros, contra papel de verdade.
"""

from __future__ import annotations

import argparse
import asyncio
import csv
import sys
import uuid
from dataclasses import dataclass, field
from pathlib import Path

ACCEPTANCE_CRITERIA: dict[str, float] = {
    "max_disagreements": 0,
    "max_review_rate": 0.10,
    "min_scans_read": 0.98,
}


@dataclass
class AcceptanceReport:
    total_items: int = 0
    auto_resolved: int = 0
    review_items: int = 0
    blank_agreements: int = 0
    #: (item_position, lido_automaticamente, digitado_manualmente)
    disagreements: list[tuple[int, str | None, str | None]] = field(default_factory=list)
    missing_from_manual: list[int] = field(default_factory=list)
    scans_total: int = 0
    scans_failed: int = 0

    @property
    def review_rate(self) -> float:
        return self.review_items / self.total_items if self.total_items else 0.0

    @property
    def scans_read_rate(self) -> float:
        if not self.scans_total:
            return 1.0
        return (self.scans_total - self.scans_failed) / self.scans_total

    @property
    def passed(self) -> bool:
        return (
            len(self.disagreements) <= ACCEPTANCE_CRITERIA["max_disagreements"]
            and not self.missing_from_manual
            and self.review_rate <= ACCEPTANCE_CRITERIA["max_review_rate"]
            and self.scans_read_rate >= ACCEPTANCE_CRITERIA["min_scans_read"]
        )

    def summary(self) -> str:
        lines = [
            f"itens comparados:        {self.total_items}",
            f"auto-resolvidos:         {self.auto_resolved}",
            f"em branco concordantes:  {self.blank_agreements}",
            f"enviados a conferencia:  {self.review_items} ({self.review_rate:.1%}, "
            f"teto {ACCEPTANCE_CRITERIA['max_review_rate']:.0%})",
            f"folhas lidas:            {self.scans_read_rate:.1%} "
            f"(minimo {ACCEPTANCE_CRITERIA['min_scans_read']:.0%})",
            f"DIVERGENCIAS:            {len(self.disagreements)} (teto "
            f"{int(ACCEPTANCE_CRITERIA['max_disagreements'])})",
        ]
        for position, read, typed in self.disagreements[:50]:
            lines.append(f"  questao {position}: leitor={read!r} digitacao={typed!r}")
        if self.missing_from_manual:
            lines.append(f"itens sem digitacao manual: {self.missing_from_manual}")
        lines.append("VEREDITO: " + ("APROVADO" if self.passed else "REPROVADO"))
        return "\n".join(lines)


def compare_readings(
    automatic: dict[int, tuple[str, str | None]],
    manual: dict[int, str | None],
) -> AcceptanceReport:
    """`automatic[posicao] = (estado, resposta)` com estado em AUTO | HUMAN | REVIEW."""
    report = AcceptanceReport(total_items=len(automatic))
    for position in sorted(automatic):
        state, read = automatic[position]
        if state == "REVIEW":
            report.review_items += 1
            continue
        if position not in manual:
            report.missing_from_manual.append(position)
            continue
        typed = manual[position]
        report.auto_resolved += 1
        if read == typed:
            if read is None:
                report.blank_agreements += 1
            continue
        report.disagreements.append((position, read, typed))
    return report


def load_manual_csv(path: Path) -> dict[str, dict[int, str | None]]:
    by_token: dict[str, dict[int, str | None]] = {}
    with open(path, newline="", encoding="utf-8") as handle:
        for row in csv.reader(handle):
            if not row or row[0].strip().lower() == "qr_token":
                continue
            token, position, answer = row[0].strip(), int(row[1]), row[2].strip().upper()
            by_token.setdefault(token, {})[position] = answer or None
    return by_token


async def load_automatic(database_url: str, mock_exam_id: uuid.UUID):
    from sqlalchemy import select
    from sqlalchemy.ext.asyncio import AsyncSession, async_sessionmaker, create_async_engine

    from agente_ia_edu.db.models import AnswerCard, AnswerCardMark, AnswerCardScan

    engine = create_async_engine(database_url)
    factory = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    try:
        async with factory() as session:
            scans = (await session.execute(
                select(AnswerCardScan).where(AnswerCardScan.mock_exam_id == mock_exam_id)
            )).scalars().all()
            cards = {
                card.id: card.qr_token
                for card in (await session.execute(
                    select(AnswerCard).where(AnswerCard.mock_exam_id == mock_exam_id)
                )).scalars().all()
            }
            by_token: dict[str, dict[int, tuple[str, str | None]]] = {}
            failed = 0
            for scan in scans:
                if scan.status == "FAILED" or scan.answer_card_id is None:
                    failed += 1
                    continue
                token = cards.get(scan.answer_card_id)
                marks = (await session.execute(
                    select(AnswerCardMark).where(AnswerCardMark.scan_id == scan.id)
                )).scalars().all()
                by_token[token] = {
                    mark.item_position: (
                        "REVIEW" if mark.resolution == "PENDING" else mark.resolution,
                        mark.resolved_option,
                    )
                    for mark in marks
                }
            return by_token, len(scans), failed
    finally:
        await engine.dispose()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Aceite fisico da leitura optica (§8.3)")
    parser.add_argument("--mock-exam", required=True)
    parser.add_argument("--manual", required=True, type=Path)
    parser.add_argument("--database-url", default=None)
    args = parser.parse_args(argv)

    from agente_ia_edu.db.session import get_database_url

    database_url = args.database_url or get_database_url()
    manual_by_token = load_manual_csv(args.manual)
    automatic_by_token, scans_total, scans_failed = asyncio.run(
        load_automatic(database_url, uuid.UUID(args.mock_exam))
    )

    combined = AcceptanceReport(scans_total=scans_total, scans_failed=scans_failed)
    for token, manual in manual_by_token.items():
        automatic = automatic_by_token.get(token)
        if automatic is None:
            print(f"AVISO: cartao {token} digitado manualmente mas sem leitura automatica")
            continue
        partial = compare_readings(automatic, manual)
        combined.total_items += partial.total_items
        combined.auto_resolved += partial.auto_resolved
        combined.review_items += partial.review_items
        combined.blank_agreements += partial.blank_agreements
        combined.disagreements.extend(partial.disagreements)
        combined.missing_from_manual.extend(partial.missing_from_manual)

    print(combined.summary())
    return 0 if combined.passed else 1


if __name__ == "__main__":
    sys.exit(main())
```

- [ ] **Step 4: Rodar e ver passar**

Run: `python -m pytest tests/test_omr_physical_acceptance_script.py -v`
Expected: PASS nos 9 testes.

- [ ] **Step 5: Escrever o protocolo do aceite físico**

`docs/superpowers/acceptance/2026-09-29-omr-aceite-fisico.md`:

```markdown
# Aceite físico da leitura óptica — protocolo

**Spec:** §8.3 de `docs/superpowers/specs/2026-09-29-correcao-simulados-tri-design.md`
**Status:** não iniciado
**Duração mínima:** 5 dias úteis de calendário. Não comprime por esforço de engenharia.

> "A fase de OMR não está pronta sem essa evidência."

## Material

- 30 cartões nominais impressos pelo gerador da Fase 1, na impressora que a escola
  realmente usa (não uma laser nova de escritório, se a escola usa outra).
- 3 canetas diferentes: esferográfica azul, esferográfica preta e uma de traço mais
  grosso (gel ou hidrográfica).
- 3 capturas diferentes: o scanner da escola, e dois celulares de modelos distintos —
  um de gama alta e um de gama baixa.
- Iluminação: a da sala de aula onde o simulado é aplicado, no horário em que é
  aplicado. Não a da sala da coordenação com a luz acesa.

## Execução

1. **Dia 1 — impressão e preenchimento.** 30 alunos (ou 30 preenchimentos por pessoas
   diferentes) preenchem os cartões à mão, à vontade: quem borra, borra; quem rasura,
   rasura; quem marca fraco, marca fraco. **Não instrua ninguém a marcar "direito"** —
   é justamente o preenchimento real que está sendo testado.
   Inclua deliberadamente: 2 cartões com dupla marcação, 2 com rasura mal apagada e
   1 totalmente em branco.
2. **Dia 2 — captura.** Cada cartão é capturado **três vezes**: scanner, celular A,
   celular B. 90 arquivos no total. Registre qual aparelho gerou qual arquivo.
3. **Dia 2 — ingestão.** Envie os 90 arquivos, cada um vira `answer_card_scan` +
   `omr_job`. Rode o worker: `docker compose --profile omr up -d omr_worker`.
4. **Dias 3 e 4 — digitação manual em dupla.** Duas pessoas digitam
   **independentemente** as respostas das 30 folhas físicas, sem ver a leitura
   automática nem a digitação uma da outra. As divergências entre as duas digitações
   são reconciliadas olhando o papel. O resultado reconciliado é o gabarito-verdade.
   Formato: `qr_token,item_position,resposta` (resposta vazia = em branco).
5. **Dia 5 — comparação e veredito.**

       python scripts/omr_physical_acceptance.py \
           --mock-exam <uuid> --manual var/aceite/digitacao_reconciliada.csv

## Critérios de aprovação

| Critério | Limite | Por quê |
|---|---|---|
| Divergências entre resposta auto-resolvida e digitação | **0** | Uma resposta que o sistema afirmou ter lido vira nota. Não existe taxa aceitável. |
| Itens enviados à conferência humana | ≤ 10% do total | Acima disso o sistema funciona mas não serve operacionalmente. |
| Folhas lidas (não `FAILED`) | ≥ 98% das capturas | Abaixo disso o gargalo volta a ser manual. |
| Cartão em branco lido como 90 brancos | obrigatório | Regressão mais perigosa possível: respostas inventadas. |
| Dupla marcação enviada à conferência | os 2 cartões | O leitor não pode escolher a mais escura. |

Registre também, sem que sejam critérios de bloqueio, os números **por aparelho de
captura** — se o celular de gama baixa concentrar as falhas, a orientação operacional
muda (usar o scanner, ou trocar o aparelho), e isso é uma decisão da escola, não um bug.

## Registro do resultado

Cole abaixo a saída de `scripts/omr_physical_acceptance.py`, a data, quem executou e
quais aparelhos foram usados. Sem esta seção preenchida, **a Fase 4 não está entregue**,
mesmo com todos os testes verdes.

### Execução 1

- Data: _(a preencher)_
- Executado por: _(a preencher)_
- Impressora / aparelhos: _(a preencher)_
- Saída do script: _(colar)_
- Veredito: _(a preencher)_
```

- [ ] **Step 6: Commit**

```bash
git add scripts/omr_physical_acceptance.py tests/test_omr_physical_acceptance_script.py \
        docs/superpowers/acceptance/2026-09-29-omr-aceite-fisico.md
git commit -m "test(omr): §8.3 protocolo e aritmetica do aceite fisico

Co-Authored-By: Claude Opus 5 <noreply@anthropic.com>"
```

- [ ] **Step 7: Rodar a suíte completa antes de declarar a fase pronta**

Run: `ps aux | grep pytest` (esperar terminar) e então:

```bash
python -m pytest -q
node --test tests/test_*frontend*.js
```

Expected: verde nos dois. Mais: `python -m pytest tests/test_omr_import_boundary.py -v`
precisa continuar passando — é a garantia de que nada que entrou nesta fase furou a
fronteira do §2.3.

- [ ] **Step 8: Executar o aceite físico e preencher o documento**

Este passo leva 5 dias úteis e **não tem atalho**. Só depois dele, com a seção
"Execução 1" preenchida e veredito APROVADO, a Fase 4 está entregue.

---

## Encerramento da fase

A Fase 4 está pronta quando, e apenas quando:

1. As Tasks 1–20 estão com todos os passos marcados.
2. `python -m pytest -q` e `node --test tests/test_*frontend*.js` estão verdes.
3. `tests/test_omr_import_boundary.py` passa — o core continua sem cv2, numpy e Pillow.
4. `docker compose --profile omr up -d --build omr_worker` sobe e processa a fila.
5. `docs/superpowers/acceptance/2026-09-29-omr-aceite-fisico.md` tem uma execução
   registrada com veredito **APROVADO**.

O item 5 não é formalidade: os outros quatro provam que o software faz o que foi
escrito; só ele prova que o que foi escrito funciona no papel da escola.

### Se o prazo apertar

O aceite físico leva 5 dias úteis de calendário e **não comprime por esforço de
engenharia** (§8.3). Se as Tasks 1–19 fecharem e não houver tempo para a Task 20, o
caminho honesto é **declarar a fase EM ACEITE, não pronta**, e dizer isso com essas
palavras a quem estiver esperando.

Concretamente, "em aceite" significa: o worker roda, lê os cartões sintéticos e os
degradados sem produzir uma resposta errada, a fila de conferência funciona e a trava do §4
está de pé — mas **ninguém ainda provou isso contra papel impresso, caneta de verdade e
câmera de celular em sala de aula**. Nesse estado o subsistema pode ser exercitado em
piloto com um simulado pequeno, com a digitação manual da fase 2 conferindo por cima.
O que ele **não** pode é corrigir um simulado de 1.500 alunos sozinho, porque a evidência
que autoriza isso é exatamente a que ainda não existe.

Registrar essa distinção aqui, antes de alguém estar com pressa, é o ponto. Depois que a
pressa chega, "está pronto" e "os testes passam" viram sinônimos, e não são.
