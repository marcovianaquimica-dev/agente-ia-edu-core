# R3 — Motor de correção MVP

**Data:** 2026-09-21
**Status:** design aprovado, aguardando plano de implementação
**Sub-projeto:** R3 da PLATAFORMA REDAÇÃO (módulo `REDACAO_IA`)
**Depende de:** R0 (estrutura acadêmica + autorização), R1 (régua ENEM + contrato do motor +
validação de camadas 2/3), R2 (propostas e envio de redação) — todos prontos e mesclados em
`main`.

---

## 1. Contexto

R1 construiu o contrato de saída (`essay_engine_contract.v1.EssayEngineOutput`) e a validação
que confere esse contrato contra a rubrica e o texto (`services/essay_engine_validation.py`) —
mas nenhum código ainda produz esse contrato: não existe chamada a um motor de IA, não existe
prompt de correção, não existe onde a correção fica guardada. R2 deu ao aluno um jeito de
enviar a redação (`EssaySubmission`), mas o que acontece depois de `SUBMITTED` é vazio — a
redação fica parada, sem devolutiva. R3 fecha esse ciclo: a partir de uma `EssaySubmission`
enviada, produz uma correção real, sujeita à política de revisão da escola, e só então fica
visível ao aluno.

R3 é deliberadamente mais estreito do que "construir um motor de correção do zero" — a
pesquisa que precedeu este documento confirmou que a maior parte da infraestrutura já existe:

- `essay_engine_contract.v1.EssayEngineOutput` (`src/agente_ia_edu/essay_engine_contract/v1.py`)
  — contrato Pydantic completo (12 modelos: `Identification`, `Scores`/`CompetencyScore`,
  `CompetencyRationale`, `Annotation`, `Rewrite`, `Feedback`, `InterventionBreakdown`, `Alert`),
  `extra="forbid"` em todos. `scores` é opcional (`Scores | None = None`) exatamente para o
  modo FORMATIVO, onde a IA produz análise sem nota nenhuma.
- `services/essay_engine_validation.py::validate_engine_output_from_payload(raw_payload, *,
  rubric, text=None, page_boxes=None, raw_output=None, input_hash=None) -> EssayEngineOutput`
  — já valida o payload bruto da IA contra a rubrica (camadas 2 e 3), levantando
  `EssayEngineOutputRejected(reason_code, message, raw_output=, input_hash=)` numa única
  exceção de domínio em qualquer falha, carregando o necessário para auditoria.
- `services/essay_correction_key.py::correction_key(*, normalized_text_hash, essay_prompt_id,
  rubric_version, model_version, prompt_version, engine_version) -> str` — a chave de
  identidade de uma correção, já implementada.
- `src/agente_ia_edu/rubrics/enem_2025.yaml` + `db/models/essay_rubric.py` — a rubrica ENEM
  2025 completa (5 competências × 6 níveis, 13 regras de pontuação, hash do PDF oficial do
  INEP registrado), revisada em PR contra o PDF oficial. **Nunca rodou contra um banco real** —
  só existe dentro de sessões de teste efêmeras (`EssayRubricSeeder`, testado mas sem nenhum
  ponto de chamada em `scripts/` ou em bootstrap de aplicação).
- `essay_prompts/__init__.py` — registro de artefatos de prompt de IA, deliberadamente vazio
  desde R1 ("R1 owns the versioning mechanism; the correction prompt itself is written in R3").
- `providers/factory.py::build_text_provider()` → `TextGenerationProvider.generate(
  TextGenerationRequest(prompt=..., model=...)) -> TextGenerationResult` — a abstração de IA já
  usada por outras features (`question_modification.py` é o exemplo mais completo e mais
  próximo do que R3 precisa: monta prompt → chama provider → valida JSON contra Pydantic →
  uma única exceção de domínio → 422/503 na rota).
- `services/authorial_material_ingestion.py` — o precedente arquitetural mais próximo de
  "IA produz rascunho → humano revisa/edita → aprova/rejeita → só aprovação publica" que existe
  no repositório hoje.

O que falta, e é o que este documento desenha: a tabela `EssayCorrection`, o módulo de prompt
de fato (`essay_prompts/v1.py`), o serviço que orquestra a chamada, e o fluxo de aprovação
docente que lê `school_settings.validation_default`/`validation_teacher_can_disable`/
`validation_threshold_points` e `PromptAssignment.validation_enabled` — nenhum dos dois
documentos de design anteriores (R0, R2) define a semântica exata dessas três configurações
de validação; este documento é quem fecha essa lacuna.

---

## 2. Escopo de R3

### Entrega

1. A tabela `EssayCorrection`, um registro por `EssaySubmission` corrigida.
2. Correção automática e síncrona: `POST /essay-submissions/{id}/confirm` (já existe, R2)
   dispara a correção na mesma requisição, depois que a submissão vira `SUBMITTED` — mesmo
   padrão síncrono que R2 já usa para OCR, sem infraestrutura de fila nova.
3. O prompt de correção de fato (`essay_prompts/v1.py`), lendo a rubrica ativa do banco, com
   variante para FORMATIVO (sem nota) e AVALIATIVO (nota completa).
4. `correction_key()` calculada e guardada para auditoria/reprodutibilidade a cada correção
   (não para deduplicação entre submissões diferentes — ver §8).
5. O fluxo de aprovação docente: máquina de estados, a regra de "precisa de revisão?" que
   combina `correction_mode`, `validation_enabled`, `validation_teacher_can_disable` e
   `validation_threshold_points`, e os endpoints de aprovar/rejeitar (individual e em lote).
6. Edição do professor antes de aprovar: nota (`final_scores`) e/ou feedback (`final_feedback`)
   — as anotações, reescritas e alertas da IA (`ai_output`) não são editáveis nesta fase.
7. Rodar `EssayRubricSeeder` contra um banco real pela primeira vez (infraestrutura, não
   conteúdo — o YAML já está pronto e revisado).
8. Uma pequena adição ao R2 já mesclado: `EssayProposalService.create_assignment` passa a
   recusar `validation_enabled=False` quando a escola tem `validation_teacher_can_disable=False`.

### Não entrega — deliberadamente

- Edição campo a campo de anotações/reescritas/alertas — só nota e feedback são editáveis.
- Qualquer infraestrutura de fila/job assíncrono.
- Tela de revisão em lote — o endpoint existe, a UI não é construída agora.
- Restrição de quem revisa por turma/professor específico — qualquer papel autorizado na
  mesma escola pode revisar, não só quem criou a proposta.
- Deduplicação de `correction_key` entre submissões diferentes (texto idêntico de outro aluno
  não reaproveita uma correção já feita) — só idempotência dentro da mesma submissão.
- Nova tentativa automática após `REJECTED` — rejeitar descarta a correção da IA; regenerar é
  uma ação manual futura, fora deste documento.
- Notificação ao professor quando uma correção cai em `PENDING_REVIEW`.
- R4 (devolutiva interativa, PDF estável) e R7 (OCR mais sofisticado) continuam sub-projetos
  separados, como R2 já documentou.
- A segunda fonte pedagógica citada na rubrica (*Cartilha Redação a Mil*, Poliedro/Lucas Felpi)
  segue reservada para R8, como o próprio YAML da rubrica já registra.

---

## 3. Modelo de dados

### `EssayCorrection`

Um registro por tentativa de correção (1:1 com `EssaySubmission` — cada nova versão de uma
redação, via reenvio de R2, gera sua própria correção quando confirmada).

| Coluna | Tipo | Nota |
|---|---|---|
| `id` | UUID PK | |
| `school_id` | UUID | cópia de `EssaySubmission.school_id`, sempre derivada no serviço (nunca do cliente) — sem FK composta, já que `EssaySubmission` não tem `UniqueConstraint(school_id, id)` (é filho único de seu próprio pai em R2, mesmo padrão de `PromptMaterial`/`EssaySubmissionPage`); existe só para consultas diretas ("todas as correções pendentes desta escola") sem precisar de join |
| `essay_submission_id` | UUID FK → `EssaySubmission`, único | uma correção por submissão |
| `correction_key` | string(64), único | `correction_key()` de R1 — guardado para auditoria/reprodutibilidade (não para dedup entre submissões, ver §8) |
| `rubric_version` | string | `EssayRubric.rubric_version` usado ("ENEM_2025") |
| `model_version` | string | modelo de IA de fato usado, vindo de `TextGenerationResult.model` — nunca um valor fixo, reflete o que realmente rodou |
| `prompt_version` | string | versão do artefato em `essay_prompts/v1.py` ("essay_correction_v1") |
| `engine_version` | string | versão da lógica de orquestração/validação de R3 em si ("r3_correction_engine_v1") — muda se a lógica de prompt/validação mudar, mesmo com o mesmo modelo de IA |
| `ai_output` | JSONB | `EssayEngineOutput.model_dump()` bruto — nunca editado, é o registro de auditoria |
| `final_scores` | JSONB, nullable | nota por competência que o aluno vê; começa como cópia de `ai_output["scores"]`, editável pelo professor; `NULL` em FORMATIVO |
| `final_feedback` | JSONB, nullable | texto de devolutiva que o aluno vê; começa como cópia de `ai_output["feedback"]`, editável pelo professor |
| `status` | `PENDING_REVIEW` \| `NEEDS_REVIEW` \| `APPROVED` \| `REJECTED` | ver §5 — sem estado `PUBLISHED` separado, "aprovar" já é "publicar" nesta tabela |
| `failure_reason` | text, nullable | motivo da falha quando `status=NEEDS_REVIEW` (código de `EssayEngineOutputRejected` ou erro de provider) |
| `reviewed_by_external_identity` | string, nullable | `NULL` quando auto-aprovado (FORMATIVO ou AVALIATIVO com `validation_enabled=False`) — a ausência de revisor é auditável por si só |
| `reviewed_at` | timestamp, nullable | |
| `published_at` | timestamp, nullable | quando o status virou `APPROVED` (humano ou automático) |
| `created_at` / `updated_at` | timestamp | |

Índices: `school_id`, `essay_submission_id` (já único), `status` (para a fila de revisão).

CHECK constraints:
- `status IN ('PENDING_REVIEW', 'NEEDS_REVIEW', 'APPROVED', 'REJECTED')`
- `(status = 'APPROVED') = (published_at IS NOT NULL)`
- `(status IN ('APPROVED', 'REJECTED')) = (reviewed_at IS NOT NULL)` — `PENDING_REVIEW`/
  `NEEDS_REVIEW` are not-yet-decided states, so `reviewed_at` stays `NULL`; both terminal
  states (human-decided or auto-approved) always get a `reviewed_at` timestamp
- `status = 'NEEDS_REVIEW' OR failure_reason IS NULL` — `failure_reason` só existe junto com
  `NEEDS_REVIEW`

Nota sobre `reviewed_at`/`reviewed_by_external_identity` em auto-aprovação: quando a correção
publica sozinha (sem revisão humana), `reviewed_at` fica preenchido (com o instante da
auto-aprovação, para saber quando aconteceu) mas `reviewed_by_external_identity` fica `NULL`
(não existe um humano para nomear) — os dois campos não são reféns um do outro.
`REJECTED` só é alcançado por ação humana explícita, então nesse estado
`reviewed_by_external_identity` está sempre preenchido; só `APPROVED` admite `NULL` ali.

---

## 4. Fluxo de correção

1. **Gatilho**: `EssaySubmissionService.confirm_submission` (R2, já existe) chama o novo
   `EssayCorrectionService.correct(essay_submission_id)` depois de marcar a submissão como
   `SUBMITTED`, ainda dentro da mesma transação HTTP.
2. **Idempotência**: se já existe uma `EssayCorrection` para este `essay_submission_id`
   (reintento de rede, por exemplo), retorna a linha existente em vez de chamar a IA de novo.
3. **Resolve entradas**: texto canônico (`EssaySubmission.canonical_text`, se
   `anchor_mode=TEXT_OFFSET`) ou as páginas-imagem (se `IMAGE_REGION` — citação mais fraca,
   comportamento já documentado em R1/R2 como esperado); a rubrica ativa
   (`EssayRubric.status=ACTIVE` com `rubric_version="ENEM_2025"`) e seus filhos; o enunciado da
   proposta (`EssayPrompt.statement`); o `correction_mode` da escola.
4. **Monta o prompt** via `essay_prompts.v1.build_prompt(...)` (novo módulo, mesmo formato que
   `classification_prompts/vN.py` já estabelece: `VERSION`, `RESPONSE_SCHEMA`,
   `build_prompt(...)`), com uma ramificação para FORMATIVO (pede só `rationales`/
   `annotations`/`feedback`/`intervention`, sem `scores`) e outra para AVALIATIVO (pede
   `scores` completo também).
5. **Chama o provider**: `build_text_provider().generate(TextGenerationRequest(prompt=...))`
   — mesmo padrão que `question_modification.py` já usa.
6. **Valida**: `json.loads(result.text)` → `validate_engine_output_from_payload(raw_payload,
   rubric=..., text=canonical_text, input_hash=normalized_text_hash)`. Se rejeitar
   (`EssayEngineOutputRejected`) ou o provider falhar (`ProviderError`), a submissão **continua
   `SUBMITTED`** — a falha nunca bloqueia a ação do aluno, mesma filosofia de "ingestão ≠
   publicação" que `authorial_material_ingestion.py` já estabelece. Cria/atualiza a
   `EssayCorrection` com `status=NEEDS_REVIEW` e `failure_reason` preenchido.
7. **Persiste em sucesso**: cria a `EssayCorrection` com `ai_output` = saída validada,
   `final_scores`/`final_feedback` pré-populados como cópia, `correction_key` calculado. O
   status inicial vem da função de decisão do §5.

---

## 5. Aprovação docente

### A regra de "precisa de revisão?"

Função pura, só chamada em `correction_mode="AVALIATIVO"` (FORMATIVO sempre publica direto —
o próprio banco já trava `validation_default`/`validation_threshold_points` como `NULL` fora de
AVALIATIVO, então não faz sentido essa política existir em FORMATIVO):

```
def requires_teacher_review(*, validation_enabled: bool, validation_threshold_points: int | None, total_score: int | None) -> bool:
    if validation_threshold_points is not None and total_score is not None and total_score < validation_threshold_points:
        return True  # trava de segurança: nota baixa sempre revisa, mesmo com validação desligada
    return validation_enabled
```

`validation_enabled` vem de `PromptAssignment.validation_enabled` (R2, `default=True`).
`validation_default` da escola (`UMA_A_UMA`/`EM_LOTE`/`AUTOMATICA`) governa o **padrão** desse
campo ao criar uma nova `PromptAssignment` — `AUTOMATICA` pré-preenche `False`;
`UMA_A_UMA`/`EM_LOTE` pré-preenchem `True`. A diferença entre `UMA_A_UMA` e `EM_LOTE` é só a
experiência do professor (revisar uma redação de cada vez vs. revisar um lote de uma vez) — o
backend expõe os dois endpoints (aprovação individual e em lote) sobre a mesma máquina de
estados, sem lógica de decisão diferente entre eles.

**Trava adicional em R2 já mesclado**: `EssayProposalService.create_assignment`
(`src/agente_ia_edu/services/essay_proposal.py`) passa a receber `validation_teacher_can_disable`
implicitamente (via `InstitutionSettingsService.get_settings(school_id)`) e recusa
(`ValueError`) uma tentativa de criar a atribuição com `validation_enabled=False` quando a
escola não permite — hoje esse método aceita `validation_enabled` sem checar nada contra a
política da escola.

### Máquina de estados

- `PENDING_REVIEW` → IA corrigiu, esperando professor (`requires_teacher_review()` retornou
  `True`).
- `NEEDS_REVIEW` → a chamada de IA falhou ou foi rejeitada na validação — sinalizado para
  atenção manual; um novo `POST .../retry` (mesmo `essay_submission_id`) tenta de novo,
  reaproveitando a mesma linha.
- `APPROVED` → aprovado (com ou sem edição de nota/feedback), `published_at` marcado — é o que
  o aluno vê. Alcançado por ação do professor (`POST .../approve`, com `final_scores`/
  `final_feedback` opcionalmente no corpo da requisição para sobrescrever antes de aprovar) ou
  automaticamente, no momento da criação, quando `requires_teacher_review()` retorna `False`.
- `REJECTED` → professor descartou (`POST .../reject`); aluno não vê a correção.

### Quem pode revisar

Mesmo conjunto de papéis que `authorial_material_ingestion.py` já usa —
`require_role(context, "TEACHER", "COORDINATOR", "DIRECTOR", "PLATFORM_ADMIN")` — restrito à
mesma escola da submissão (`EssayCorrection.school_id == context.school_id`), sem restrição por
turma ou por quem criou a proposta.

---

## 6. Autorização

- **Disparar a correção**: implícito — acontece dentro de `confirm_submission`, que já é uma
  ação do próprio aluno (R2). Nenhuma autorização nova aqui.
- **Revisar (aprovar/rejeitar/editar)**: `require_role(context, "TEACHER", "COORDINATOR",
  "DIRECTOR", "PLATFORM_ADMIN")` + `EssayCorrection.school_id == context.school_id` — 403,
  nunca 404, para uma correção de outra escola, mesma regra que todo o resto do projeto já usa
  desde a Fase 3C.
- **Ler a própria correção (aluno)**: fora do escopo deste documento — nenhuma rota de leitura
  para o aluno é definida aqui (R2 também deixou isso para depois); R3 só garante que
  `EssayCorrection.status="APPROVED"` é o sinal correto para "pronto para o aluno ver", para
  quando essa rota for construída.

---

## 7. O que fica para depois, explicitamente

Ver §2 "Não entrega" para a lista completa. Resumo dos itens mais relevantes para o
planejamento: edição granular de anotações/reescritas/alertas, fila assíncrona, UI de revisão
em lote, deduplicação de `correction_key` entre submissões, retry automático pós-rejeição,
notificações, R4, R7 e R8.

---

## 8. Rastreabilidade com R0/R1/R2

Toda referência de nome de campo abaixo já existe hoje, sem modificação, e R3 é responsável
por produzir dados compatíveis com ela:

- `essay_engine_contract.v1.EssayEngineOutput` (`src/agente_ia_edu/essay_engine_contract/v1.py`)
  — o `ai_output` de R3 é exatamente `EssayEngineOutput.model_dump()` de uma instância validada,
  sem nenhum campo extra (`extra="forbid"` em todos os modelos do contrato).
- `services/essay_engine_validation.py::validate_engine_output_from_payload`/
  `EssayEngineOutputRejected` — reaproveitados sem modificação; R3 é o primeiro (e único)
  chamador real dessa função fora de testes.
- `services/essay_correction_key.py::correction_key` — reaproveitado sem modificação; os seis
  argumentos nomeados (`normalized_text_hash`, `essay_prompt_id`, `rubric_version`,
  `model_version`, `prompt_version`, `engine_version`) mapeiam 1:1 para as colunas de
  `EssayCorrection` com o mesmo nome (exceto `normalized_text_hash`, que vem de
  `EssaySubmission.normalized_text_hash`, e `essay_prompt_id`, que vem de
  `PromptAssignment.essay_prompt_id` via `EssaySubmission.prompt_assignment_id`).
- `db/models/essay_rubric.py::EssayRubric`/`EssayRubricCompetency`/`EssayRubricLevel`/
  `EssayRubricSignal`/`EssayRubricScoringRule` — lidos, nunca escritos por R3 (exceto a
  semeadura inicial via `EssayRubricSeeder`, que já existe).
- `essay_prompts/__init__.py::EssayPrompt`/`get_essay_prompt`/`available_versions` — R3 escreve
  o primeiro artefato real (`essay_prompts/v1.py`), registrado nesse mecanismo já existente.
- `providers/factory.py::build_text_provider`/`TextGenerationProvider`/`TextGenerationRequest`/
  `TextGenerationResult` — reaproveitados sem modificação.
- `db/models/institution.py::SchoolSetting` (`correction_mode`, `validation_default`,
  `validation_teacher_can_disable`, `validation_threshold_points`) e
  `services/institution_settings.py::InstitutionSettingsService.get_settings` — lidos, nunca
  escritos por R3.
- `db/models/essay_proposal.py::EssaySubmission` (`canonical_text`, `normalized_text_hash`,
  `anchor_mode`, `status`, `school_id`, `prompt_assignment_id`) e `PromptAssignment`
  (`validation_enabled`, `essay_prompt_id`) — lidos por R3; `EssayProposalService
  .create_assignment` ganha uma checagem nova (§5), sem mudar sua assinatura pública.
- `db/models/admin.py::AdminAuditLog` — R3 escreve entradas de auditoria para
  aprovar/rejeitar/editar, seguindo a convenção já estabelecida por
  `institution_settings.py` (`action` em `SCREAMING_SNAKE_CASE`, `entity_type="ESSAY_CORRECTION"`,
  `entity_id=str(correction.id)`, `metadata_` com `before`/`after` quando for uma edição).
