# Sondagem inicial alimenta a Trilha de Estudos Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** `AdaptiveLearningPathService._build()` (a base de `GET /api/v1/student/study-path`)
passa a considerar evidência real de `StudentContentMastery` (gravada pela sondagem inicial)
como fallback para um `content_code` sem nenhuma evidência de atividade no domain map —
nunca sobrescrevendo evidência de atividade real quando ela existir.

**Architecture:** Uma única mudança cirúrgica em `adaptive_learning_path.py`: novo método
privado `_fill_diagnostic_fallback`, chamado dentro de `_build()` logo depois que
`_prereq_graph()` já carregou o catálogo (`self._bank._catalog_by_id` disponível). Nenhuma
tabela nova, nenhuma migração, nenhuma mudança em `curriculum_domain_map.py`/
`initial_diagnostic.py` — só uma leitura nova de dado que já existe.

**Tech Stack:** Python/FastAPI, SQLAlchemy 2.x async, pytest.

**Spec:** [docs/superpowers/specs/2026-09-28-sondagem-alimenta-trilha-design.md](../specs/2026-09-28-sondagem-alimenta-trilha-design.md)

## Global Constraints

- Nunca editar `curriculum_domain_map.py`, `ActivityResult`/`ActivityResultItem`,
  `services/initial_diagnostic.py` — só ler `StudentContentMastery` de um lugar novo.
- Evidência de atividade (domain map) sempre vence sobre evidência de sondagem quando as
  duas existem para o mesmo `content_code` — o fallback só preenche o que está ausente.
- Nunca fabricar/inventar valor de mastery/confiança — todo dado vem de uma linha real de
  `StudentContentMastery`.
- TDD obrigatório (RED antes de qualquer código de produção).
- Antes de rodar a suíte completa, `ps aux | grep pytest` e esperar qualquer execução
  concorrente terminar (Postgres descartável compartilhado na porta 5433).
- Nenhum `git commit`/`git push` sem o gatilho explícito do usuário.

---

### Task 1: `_fill_diagnostic_fallback` isolado (sem tocar `_build` ainda)

**Files:**
- Modify: `src/agente_ia_edu/services/adaptive_learning_path.py` (novo método privado + import de `StudentContentMastery`)
- Test: `tests/test_adaptive_learning_path.py` (ou o arquivo real de testes unitários do service — confirmar o nome exato via `grep -rl "AdaptiveLearningPathService" tests/` antes de escrever, o plano assume esse nome mas pode haver um mais específico já em uso)

**Interfaces:**
- Consumes: `StudentContentMastery` (`src/agente_ia_edu/db/models/learning_path.py:116`) — campos `external_identity_id`, `content_node_id`, `mastery_score`, `current_level`, `questions_answered`, `questions_correct`, `confidence`.
- Consumes: `self._bank._catalog_by_id` (já populado por `_prereq_graph()`/`_load_catalog()`, existente) para resolver `content_node_id -> content_code`.
- Produces: `_fill_diagnostic_fallback(cstate: dict[str, dict], student_external_id: str) -> dict[str, dict]` — usado pela Task 2.

- [ ] **Step 1: Escrever o teste RED**

```python
# dentro da classe de teste já existente para AdaptiveLearningPathService,
# ou uma nova - seguir o padrão de setUp/fixtures já usado no arquivo real
def test_fill_diagnostic_fallback_adds_content_absent_from_activity_evidence(self):
    async def scenario():
        async with self.session_factory() as session:
            # seed: um CatalogNode real + uma linha real de StudentContentMastery
            # pro mesmo aluno (seguir o padrão de seed já usado no arquivo de
            # teste real do service, reaproveitando helpers existentes se houver)
            node = CatalogNode(parent_id=None, root_id=None, node_type="CONTENT", name="Soluções", active=True)
            session.add(node)
            await session.flush()
            node.root_id = node.id
            session.add(StudentContentMastery(
                external_identity_id="aluno-x", content_node_id=node.id,
                mastery_score=30.0, current_level="EASY",
                questions_answered=10, questions_correct=3, confidence=0.1,
            ))
            await session.commit()
            return node

        service = AdaptiveLearningPathService(session, ...)  # mesmos args do setUp real
        # força o catálogo a carregar (mesma chamada que _build já faz)
        await service._prereq_graph()
        result = await service._fill_diagnostic_fallback({}, "aluno-x")
        self.assertIn("SOLUCOES_CODE_REAL", result)  # usar o content_code real gerado pelo seed
        self.assertEqual(result["SOLUCOES_CODE_REAL"]["questions_answered"], 10)
        self.assertEqual(result["SOLUCOES_CODE_REAL"]["evidence_source"], "DIAGNOSTIC")

    asyncio.run(scenario())

def test_fill_diagnostic_fallback_never_overwrites_existing_activity_evidence(self):
    # cstate já tem uma entrada real (simulando domain map/ActivityResult) para
    # o mesmo content_code que também tem uma linha em StudentContentMastery com
    # valores DIFERENTES - depois da chamada, a entrada original deve permanecer
    # byte-a-byte idêntica (nenhum campo sobrescrito, nem mastery_score nem
    # questions_answered).
    ...
```

- [ ] **Step 2: Rodar e confirmar falha**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_adaptive_learning_path.py -k diagnostic_fallback -v`
Expected: FAIL com `AttributeError: 'AdaptiveLearningPathService' object has no attribute '_fill_diagnostic_fallback'`.

- [ ] **Step 3: Implementar o método**

```python
# em adaptive_learning_path.py, junto ao import já existente de db.models.catalog
from agente_ia_edu.db.models.learning_path import StudentContentMastery

# dentro da classe AdaptiveLearningPathService
async def _fill_diagnostic_fallback(self, cstate: dict[str, dict], student_external_id: str) -> dict[str, dict]:
    """Evidência real de StudentContentMastery (gravada pela sondagem inicial,
    services/initial_diagnostic.py) como fallback para um content_code sem
    nenhuma evidência de atividade (ActivityResult/domain map) - nunca
    sobrescreve uma entrada que já existe em cstate. Ver design doc:
    docs/superpowers/specs/2026-09-28-sondagem-alimenta-trilha-design.md."""
    id_to_code = {node.id: code for code, node in (self._bank._catalog_by_id or {}).items()}
    # nota: self._bank._catalog_by_id é chaveado por id, mas o dict comprehension
    # acima assume a mesma forma de id_to_code já usada em _prereq_graph() - ao
    # implementar de verdade, confirme a forma exata (`_catalog_cache`/`_catalog_by_id`)
    # lendo _prereq_graph() e _bank._load_catalog() diretamente, este plano descreve
    # a intenção, não necessariamente os nomes internos exatos linha por linha.
    rows = (await self._session.execute(
        select(StudentContentMastery).where(
            StudentContentMastery.external_identity_id == student_external_id
        )
    )).scalars().all()
    for row in rows:
        code = id_to_code.get(row.content_node_id)
        if not code or code in cstate:
            continue
        cstate[code] = {
            "content_code": code,
            "mastery_score": float(row.mastery_score),
            "current_level": row.current_level,
            "questions_answered": row.questions_answered,
            "questions_correct": row.questions_correct,
            "confidence": float(row.confidence),
            "evidence_source": "DIAGNOSTIC",
        }
    return cstate
```

Nota importante pra quem implementar: leia o dict que `cstate` já contém HOJE pra cada
`content_code` (vindo do domain map, dentro de `_build()`) antes de decidir o shape exato
acima — os nomes de campo do meu esboço (`mastery_score`, `current_level`, etc.) precisam
bater com o que o resto de `_build()`/`mastered()`/os passos abaixo dele já esperam ler de
cada entrada de `cstate` (ex.: `mastered()` já lê `c["questions_answered"]` — confirmado
por leitura direta nesta sessão; os outros campos exatos devem ser confirmados da mesma
forma antes de fixar o dict, não apenas copiados deste plano sem checar).

- [ ] **Step 4: Rodar e confirmar GREEN**

Run: `PYTHONPATH=src .venv/bin/python -m pytest tests/test_adaptive_learning_path.py -k diagnostic_fallback -v`
Expected: PASS nos dois testes (preenche quando ausente, nunca sobrescreve).

---

### Task 2: Ligar `_fill_diagnostic_fallback` dentro de `_build()`

**Files:**
- Modify: `src/agente_ia_edu/services/adaptive_learning_path.py` (uma linha nova dentro de `_build`)
- Test: teste de integração HTTP reproduzindo o cenário real visto ao vivo.

**Interfaces:**
- Consumes: `_fill_diagnostic_fallback` (Task 1).
- Produces: `GET /api/v1/student/study-path` com `state` != `NO_EVIDENCE` para um aluno com sondagem completa e zero atividades.

- [ ] **Step 1: Escrever o teste RED (nível HTTP/serviço, reproduzindo o bug real)**

Seguir o padrão de fixture já usado nos testes HTTP de diagnóstico
(`tests/test_diagnostic_http_gaps.py`, já estendido nesta mesma sessão com um seed completo
de Question/QuestionVersion/QuestionOption/ContentQuestionLink) combinado com uma linha real
de `StudentContentMastery` para o mesmo `content_node_id` e `external_identity_id` do aluno
de teste — sem nenhuma linha em `ActivityResult`. Chamar o endpoint/serviço de study-path e
confirmar `state != "NO_EVIDENCE"` e `len(steps) > 0` (ou o campo equivalente confirmado ao
ler o schema de resposta real).

Confirmar RED: hoje a resposta é `NO_EVIDENCE`/`steps: []` mesmo com a linha de
`StudentContentMastery` presente, porque `_build()` ainda não chama o método da Task 1.

- [ ] **Step 2: Inserir a chamada em `_build()`**

Logo depois de `graph, names, positions, dependents = await self._prereq_graph()` (o ponto
em que o catálogo já está carregado - confirmar a linha exata ao implementar, pode ter
mudado desde a leitura que fundamentou este plano):

```python
cstate = await self._fill_diagnostic_fallback(cstate, student_external_id)
```

- [ ] **Step 3: Rodar e confirmar GREEN**

Run: `PYTHONPATH=src .venv/bin/python -m pytest <arquivo do teste da Step 1> -v`

- [ ] **Step 4: Rodar a suíte de regressão do study-path/adaptive-learning-path inteira**

Run: `grep -rl "AdaptiveLearningPathService\|study-path\|study_path" tests/*.py` para achar
todos os arquivos relevantes, depois `PYTHONPATH=src .venv/bin/python -m pytest <esses arquivos> -v`.
Expected: 100% verde, nenhuma asserção existente precisa mudar (evidência de atividade real
continua tendo prioridade absoluta - nenhum cenário que já passava com dado de atividade
deveria mudar de resultado).

- [ ] **Step 5: Rodar a suíte completa isolada**

Run: (depois de confirmar `ps aux | grep pytest` limpo) `PYTHONPATH=src .venv/bin/python -m pytest tests/ -q`
Expected: 100% verde.

---

### Task 3: Verificação de ponta a ponta com o aluno real (bloqueada até Task 2 pronta)

**Files:** nenhum arquivo de produção — só verificação.

- [ ] **Step 1: Repetir exatamente a chamada real feita durante a investigação**

```bash
curl -s "http://localhost:8010/api/v1/student/study-path" -H "Authorization: Bearer student:student:aluno_abc"
```

Expected: `state` diferente de `NO_EVIDENCE`, `steps` não vazio, refletindo a sondagem de 10
questões/3 corretas já completada por esse aluno real na Escola ABC durante a investigação
desta sessão (diagnostic_id `5d1d8328-4a37-4836-a3e9-69635e6ad813`, já no banco de dev).

- [ ] **Step 2: Confirmar que um aluno com evidência de ATIVIDADE real continua funcionando sem mudança**

Usar um aluno de dev existente com histórico de `ActivityResult` real (ex. `student:alice`
na Escola Partner, já usada nesta sessão) e confirmar que a resposta de `/study-path` para
ele é idêntica à de antes desta mudança (nenhuma regressão para quem já tinha evidência de
atividade).

---

## Self-Review

1. **Cobertura do spec:** §2.1-2.2 (onde/como integrar, campo `evidence_source`) → Task 1.
   §2.3 (fallback nunca fusão) → testado explicitamente na Task 1 (teste "never overwrites").
   §3 (fluxo completo) → Task 2 + Task 3. §5 (restrições) → repetidas no cabeçalho.
2. **Placeholders:** o único ponto deixado deliberadamente aberto é o nome exato de alguns
   arquivos de teste já existentes (Task 1/2) e a forma interna exata de
   `self._bank._catalog_by_id` — ambos marcados explicitamente como "confirmar ao
   implementar, não presumir", não lacunas de comportamento escondidas.
3. **Consistência:** o shape do dict que `_fill_diagnostic_fallback` produz (Task 1) precisa
   bater com o que `_build()` já espera ler de cada entrada de `cstate` — a Task 1 já
   instrui explicitamente a confirmar isso por leitura direta antes de fixar os nomes de
   campo, em vez de copiar o esboço às cegas.

## Execution Handoff

Plano salvo em `docs/superpowers/plans/2026-09-28-sondagem-alimenta-trilha.md`. É uma
mudança cirúrgica e sequencial (Task 2 depende do método que a Task 1 cria; Task 3 depende
da Task 2 estar funcionando) — não há paralelismo real entre as tasks desta vez, ao
contrário do plano anterior. Recomendo **Inline Execution** (ou um único agente em
subagent-driven-development) em vez de multiagentes, dado que é um fluxo sequencial em um
único arquivo de produção.
