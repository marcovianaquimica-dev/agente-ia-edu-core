# R2 — Propostas e envio de redação (digitada, foto, PDF)

**Data:** 2026-09-21
**Status:** design aprovado, aguardando plano de implementação
**Sub-projeto:** R2 da PLATAFORMA REDAÇÃO (módulo `REDACAO_IA`)
**Depende de:** R0 (estrutura acadêmica + autorização) e R1 (régua ENEM + contrato do motor) — ambos prontos e mesclados em `main`.

---

## 1. Contexto

R0 e R1 estão implementados. R1 é deliberadamente "biblioteca e dados" — nenhuma tabela de
correção, nenhuma chamada a IA, nenhuma interface (ver
`docs/superpowers/specs/2026-09-13-r1-regua-enem-contrato-motor-design.md`). R2 é o primeiro
sub-projeto que dá a um aluno de verdade um jeito de entregar uma redação. R3 (motor de
correção MVP) e R4 (devolutiva) vêm depois, sobre o que R2 constrói aqui.

O design do R0 (§10) já nomeou três das entidades que R2 possui:

> "**R2** — `EssayPrompt`, `PromptMaterial`, `PromptAssignment`. A escolha de validação por
> turma pendura em `PromptAssignment`."

Este documento completa esse desenho: as três entidades acima, mais o que falta para o aluno
efetivamente enviar uma redação (`EssaySubmission`, `EssaySubmissionPage`) e o resolvedor de
matrícula que nada no repositório ainda calcula.

### Decisão já tomada em R0, que substitui uma afirmação anterior de R1

O design do R1 (seção "Consequência para o design", `2026-09-13-r1-regua-enem-contrato-motor-design.md:103-117`)
registrou três modos de correção — `FORMATIVO`, `ASSISTIDO`, `AVALIATIVO` — como configuração
de `SchoolModule.metadata`. O design do R0, decidido depois, restringiu
`school_settings.correction_mode` a **dois** valores (`FORMATIVO`, `AVALIATIVO`), com a
justificativa "dois controles, não três". Essa é a decisão vigente — R2 assume dois modos, não
três. Onde este documento cita `correction_mode`, são sempre esses dois valores.

---

## 2. Escopo de R2

### Entrega

1. As três entidades de proposta (`EssayPrompt`, `PromptMaterial`, `PromptAssignment`) e a
   entidade de envio (`EssaySubmission`, `EssaySubmissionPage`).
2. Envio em três modos: digitada, foto, PDF.
3. Para foto/PDF, quando a escola habilita transcrição (`school_settings`, opcional por
   instituição, §9 da spec v1.0): uma transcrição própria de R2 — OCR automático, sem sugerir
   correção ortográfica/gramatical, com confiança por palavra/token; toda palavra abaixo de
   80% de confiança é destacada para o aluno confirmar ou corrigir antes do envio virar
   definitivo. Este é um recurso completo, não um stub — R7 ("OCR, tokens, coordenadas,
   transcrição revisável") vem depois para sofisticar a mesma capacidade (granularidade de
   token, coordenadas na imagem, fluxo de revisão mais rico), não para criá-la.
4. O resolvedor `identidade → matrícula real` (`User.external_user_id` → `Person` → `Student`
   → `StudentEnrollment` ativa) que nenhum código hoje calcula.
5. Reenvio de uma redação já `SUBMITTED`, condicionado a `correction_mode`.

### Não entrega — deliberadamente

- Nenhuma tabela `EssayCorrection`, nenhuma chamada ao motor de correção. Isso é R3.
- Nenhum object storage novo. Reaproveita o único precedente do repositório
  (`services/material_storage.py`, cópia local endereçada por conteúdo) para
  `EssaySubmissionPage`. Trocar por um object storage real é decisão de infraestrutura
  futura, não um bloqueio de R2 — o retorno de `MaterialStorage.store()` já é uma tupla
  `(Path, hash)` trivialmente substituível.
- OCR sofisticado (tokens com coordenadas na imagem, fluxo de revisão assistido por IA além do
  destaque de confiança). R2 entrega o suficiente para o produto funcionar; R7 sofistica.
- `correction_key()` em si não é calculada por R2 — ela exige `rubric_version`,
  `model_version`, `prompt_version`, `engine_version`, que só existem no momento da correção
  (R3). R2 produz e armazena `normalized_text_hash` (via `essay_text_hash()`), pronto para R3
  combinar depois.

---

## 3. Modelo de dados

### 3.1 `EssayPrompt`

A proposta de redação (tema, texto de apoio, ano). Imutável uma vez atribuída a uma turma via
`PromptAssignment` — o mesmo padrão de "nunca edita, sempre supersede" que `EssayRubric`
(R1) já usa para `rubric_version`.

| Coluna | Tipo | Nota |
|---|---|---|
| `id` | UUID PK | |
| `school_id` | UUID | escopo de tenant, como toda entidade de R0 |
| `title` | string | |
| `statement` | text | o enunciado/tema |
| `year` | int | ano de referência |
| `status` | `DRAFT` \| `ACTIVE` \| `SUPERSEDED` | `DRAFT` até a primeira `PromptAssignment`; depois disso, imutável |
| `created_by_external_identity` | string | |
| `created_at` / `updated_at` | timestamp | |

### 3.2 `PromptMaterial`

Material de apoio da proposta (texto motivador, imagem, trecho de referência). Filho de
`EssayPrompt` — herda o mesmo ciclo de vida (nunca editado depois que a proposta sai de
`DRAFT`).

| Coluna | Tipo | Nota |
|---|---|---|
| `id` | UUID PK | |
| `essay_prompt_id` | UUID FK → `EssayPrompt` | |
| `material_type` | `TEXT` \| `IMAGE` | |
| `content` | text, nullable | quando `TEXT` |
| `storage_uri` | string, nullable | quando `IMAGE`, via `MaterialStorage` |
| `position` | int | ordem de exibição |

### 3.3 `PromptAssignment`

Liga uma `EssayPrompt` a uma turma. É contra esta tabela que a autorização de envio bate — um
aluno só envia para uma proposta que a turma dele efetivamente recebeu.

| Coluna | Tipo | Nota |
|---|---|---|
| `id` | UUID PK | |
| `school_id` | UUID | |
| `essay_prompt_id` | UUID FK → `EssayPrompt` | |
| `class_id` | UUID FK composta → `Class` (`school_id`, `class_id`) | mesmo padrão composto que `StudentEnrollment` já usa |
| `assigned_by_external_identity` | string | professor/coordenador que atribuiu |
| `due_at` | timestamp, nullable | prazo, se houver |
| `validation_enabled` | bool, default `True` | a opção de validação por turma que R3 lê — R2 só a guarda |
| `status` | `OPEN` \| `CLOSED` | |
| `created_at` | timestamp | |

### 3.4 `EssaySubmission`

O envio em si. Um `essay_id` estável agrupa reenvios da mesma proposta pelo mesmo aluno; cada
linha desta tabela é um `essay_version_id` — os dois nomes que
`essay_engine_contract.v1.Identification` já reserva (`src/agente_ia_edu/essay_engine_contract/v1.py:71-72`).
Um reenvio cria uma nova linha com o mesmo `essay_id`, novo `essay_version_id`, e marca a
anterior `SUPERSEDED` — o mesmo padrão de versionamento imutável que `EssayRubric`/`EssayPrompt`
já usam.

| Coluna | Tipo | Nota |
|---|---|---|
| `id` (= `essay_version_id`) | UUID PK | |
| `essay_id` | UUID | estável entre reenvios; `uuid4()` na primeira submissão |
| `school_id` | UUID | |
| `prompt_assignment_id` | UUID FK → `PromptAssignment` | |
| `student_id` | UUID FK composta → `Student` (`school_id`, `student_id`) | resolvido via o resolvedor de matrícula (§4), nunca recebido do cliente |
| `mode` | `TYPED` \| `PHOTO` \| `PDF` | |
| `anchor_mode` | `TEXT_OFFSET` \| `IMAGE_REGION` | `TEXT_OFFSET` sempre que existe texto canônico (digitada, ou foto/PDF com transcrição confirmada); `IMAGE_REGION` só quando a escola não habilita transcrição — mesmo valor que `Identification.anchor_mode` vai carregar depois |
| `status` | `PENDING_TRANSCRIPTION` \| `PENDING_CONFIRMATION` \| `SUBMITTED` \| `SUPERSEDED` | ver máquina de estados, §5 |
| `canonical_text` | text, nullable | resultado de `normalize_essay_text()`, aplicado **uma única vez**, no momento em que o status vira `SUBMITTED` — nunca antes, nunca de novo depois |
| `normalized_text_hash` | string, nullable | `essay_text_hash(canonical_text)`, calculado no mesmo momento |
| `submitted_at` | timestamp, nullable | |
| `created_at` / `updated_at` | timestamp | |

### 3.5 `EssaySubmissionPage`

Uma linha por página, para os modos `PHOTO`/`PDF`. Não existe para `TYPED`.

| Coluna | Tipo | Nota |
|---|---|---|
| `id` | UUID PK | |
| `essay_submission_id` | UUID FK → `EssaySubmission` | |
| `page_number` | int | 1-based |
| `storage_uri` | string | via `MaterialStorage.store()` |
| `width` / `height` | float, nullable | dimensões da página, necessárias se `anchor_mode=IMAGE_REGION` mais tarde alcançar esta página (mesmo campo que `ImageRegionAnchor` já exige) |
| `ocr_tokens` | JSONB, nullable | lista de `{text, confidence, start, end}` — offset no texto transcrito desta página, não coordenada de imagem; preenchido só quando a escola habilita transcrição; bruto e imutável, nunca reescrito pela revisão do aluno |
| `reviewed_text` | TEXT, nullable | texto desta página depois da revisão do aluno (§5.2 passo 3); pré-populado no cliente a partir de `ocr_tokens`, mas só gravado aqui quando o aluno confirma a página via `PATCH /essay-submissions/{id}/pages/{page_number}`; null até lá; é o único texto usado para montar o texto canônico na confirmação final |

---

## 4. O resolvedor de matrícula (lacuna de R0 fechada aqui)

Nenhum código hoje resolve "este `external_user_id` autenticado é matriculado em qual turma
de verdade". A cadeia real, a partir do que `AuthorizationService.resolve_context` já devolve
(`context.school_id`, `identity.external_user_id`):

```
(school_id, external_user_id) → User (school_id, external_identity_provider, external_user_id)
  → User.person_id → Student (school_id, person_id)
  → StudentEnrollment (school_id, student_id, status='ACTIVE') → class_id
```

R2 escreve uma função nova, `resolve_active_enrollment(session, *, school_id, external_user_id) ->
StudentEnrollment | None`, em `src/agente_ia_edu/services/` (nome de módulo a decidir no plano
de implementação — candidato natural: `student_enrollment_resolution.py`, ao lado de
`external_id_resolution.py`). Isso fica no escopo de R2, não reabre o escopo de R0: é R2 quem
precisa dela primeiro, e a tabela que ela consulta (`StudentEnrollment`) já existe, testada,
desde R0.

---

## 5. Fluxo de envio

### 5.1 Digitada (`TYPED`)

Sem etapa intermediária. O aluno digita o texto; ao confirmar, `normalize_essay_text()` +
`essay_text_hash()` rodam a única vez que rodam, `anchor_mode="TEXT_OFFSET"`, status vai direto
para `SUBMITTED`.

### 5.2 Foto / PDF — escola COM transcrição habilitada

1. Upload — mesmo padrão de `authorial_ingestion.py::upload_material` (lista de extensões
   permitida, limite de tamanho, streaming para arquivo temporário, depois
   `MaterialStorage.store()`; nunca escreve `storage_uri` antes de o arquivo estar
   efetivamente armazenado). Uma `EssaySubmissionPage` por página. Status:
   `PENDING_TRANSCRIPTION`.
2. OCR roda automaticamente por página — texto bruto, **sem sugerir correção
   ortográfica/gramatical** (é a redação do aluno que está sendo avaliada, não uma versão
   corrigida dela), com confiança por token e o offset de cada token no texto transcrito
   daquela página. Grava em `EssaySubmissionPage.ocr_tokens` — registro bruto e imutável, só
   para auditoria (comparar o que a IA leu com o que o aluno confirmou). Status:
   `PENDING_CONFIRMATION`.
3. O aluno revisa **texto livre por página**, não token a token. O cliente pré-popula um campo
   editável por página com a concatenação de `ocr_tokens.text` na ordem dos offsets — esse
   texto pré-populado é exatamente o que o OCR leu, sem nenhuma etapa de correção
   ortográfica/gramatical entre o OCR e a tela. `start`/`end`/`confidence` de cada token servem
   só para pintar em vermelho, no cliente, todo trecho abaixo de 80% de confiança; não são
   unidade de edição. O aluno edita a página inteira como texto livre, mas só aquela página —
   não precisa reler as outras.
   - `GET /essay-submissions/{id}/pages` — retorna cada página com `page_number`,
     `ocr_tokens` (para pintar), `reviewed_text` (null até a página ser revisada).
   - `PATCH /essay-submissions/{id}/pages/{page_number}` — corpo `{reviewed_text: str}`.
     Grava o texto revisado daquela página em `EssaySubmissionPage.reviewed_text` (novo campo,
     `TEXT`, nullable). Idempotente — o aluno pode reabrir e reeditar antes da confirmação
     final.
   - Enquanto o status geral for `PENDING_TRANSCRIPTION` ou `PENDING_CONFIRMATION`, um novo
     upload no mesmo `page_number` substitui a página inteira (novo `storage_uri`,
     `ocr_tokens` recalculado, `reviewed_text` volta a `null`) — mesmo cuidado do passo 1 de
     nunca escrever `storage_uri` antes do arquivo estar efetivamente armazenado. Cobre o caso
     de foto ilegível/OCR de qualidade ruim numa página específica.
4. **Só na confirmação final** — nunca antes — `POST /essay-submissions/{id}/confirm` roda.
   Recusa com 409 se alguma página ainda tiver `reviewed_text` nulo. Concatena
   `reviewed_text` de cada página em ordem de `page_number` (separador `\n\n`) para formar o
   texto canônico: `normalize_essay_text()` + `essay_text_hash()` rodam a única vez que rodam,
   `anchor_mode="TEXT_OFFSET"`. Status: `SUBMITTED`.

### 5.3 Foto / PDF — escola SEM transcrição habilitada

Upload idêntico ao passo 1 acima, mas sem OCR: `EssaySubmissionPage` guarda só
`storage_uri`/`width`/`height`, nunca `ocr_tokens`. Status vai direto de upload para
`SUBMITTED`. `canonical_text`/`normalized_text_hash` ficam `NULL`, `anchor_mode="IMAGE_REGION"`.
R1 já documenta a consequência: sem texto canônico, a correção roda direto da imagem e a
verificação de citação fica mais fraca — comportamento esperado, não um defeito de R2.

### 5.4 Reenvio

Condicionado a `correction_mode` da escola:

- **FORMATIVO**: o aluno pode reenviar mesmo depois de `SUBMITTED`. Reenviar cria uma nova
  `EssaySubmission` com o mesmo `essay_id`, novo `essay_version_id` (nova linha), e marca a
  anterior `SUPERSEDED`.
- **AVALIATIVO**: `SUBMITTED` é definitivo. A rota de reenvio recusa com 409 se
  `correction_mode == 'AVALIATIVO'` e já existe uma versão `SUBMITTED` para este `essay_id`.

---

## 6. Autorização

Mesmo padrão já estabelecido e testado ao longo de toda a Fase 3C deste projeto — papel real,
depois posse real, nunca um id confiado verbatim do cliente.

### Gerenciar proposta (`EssayPrompt`/`PromptMaterial`/`PromptAssignment`)

`require_role(context, "TEACHER", "COORDINATOR", "DIRECTOR", "PLATFORM_ADMIN")` — o mesmo
conjunto de papéis que já cria material didático (`create_material`, `catalog.py`) e recurso
educacional (`create_resource`, corrigido nesta sessão) neste repositório. Criar uma
`PromptAssignment` exige adicionalmente que `class_id` pertença à `school_id` do contexto —
mesma checagem composta que `StudentEnrollment` já usa.

### Enviar redação (`EssaySubmission`)

1. `require_role(context, "STUDENT")` — só o próprio aluno envia sua redação.
2. `require_module(context, "REDACAO_IA")` — a escola precisa ter o módulo ligado
   (`AuthorizationService.require_module`, já existente).
3. `resolve_active_enrollment(...)` (§4) resolve a matrícula real do aluno autenticado. Se
   `None`, 403 — sem matrícula ativa, sem envio.
4. O `prompt_assignment_id` do corpo da requisição é validado contra `class_id` da matrícula
   resolvida — a `PromptAssignment` referenciada precisa pertencer à turma real do aluno.
   Um `prompt_assignment_id` de outra turma (mesmo que da mesma escola) é 403, não 404 — a
   distinção entre "não existe" e "não é seu" não importa aqui pela mesma razão que routes já
   hardened nesta fase escondem essa distinção.

### Módulo desligado depois de já existirem envios

Desligar `REDACAO_IA` não apaga nem torna ilegível nenhuma `EssaySubmission` já existente —
`require_module` bloqueia **envios novos**, o mesmo padrão de gate que o resto da plataforma já
usa. Leitura de dados já enviados continua fora do escopo de `require_module` (rotas de
leitura, quando existirem em R3/R4, decidem sua própria política).

---

## 7. Armazenamento

Reaproveita `services/material_storage.py::MaterialStorage` sem modificação — cópia local
endereçada por conteúdo (SHA-256), já testada, já em produção para materiais autorais. Nenhuma
abstração de object storage nova. Se o volume de fotos/PDFs de redação um dia exigir um backend
diferente, `MaterialStorage.store()` já devolve `(Path, hash)`, uma interface trivial de trocar
sem tocar no resto de R2 — decisão de infraestrutura futura, fora do escopo deste documento.

---

## 8. O que fica para depois, explicitamente

- **R3**: tabela `EssayCorrection`, chamada ao motor de IA, `correction_key()` de fato
  calculada, fluxo de aprovação docente, leitura de `school_settings.validation_default`/
  `validation_teacher_can_disable`/`validation_threshold_points`.
- **R7**: sofisticação da transcrição — tokens com coordenada na imagem (não só offset de
  texto), fluxo de revisão mais rico que "confirmar/corrigir token vermelho".
- **R4**: devolutiva interativa e PDF estável, consumindo o que R3 produzir.
- Rotas de leitura para professor/coordenação verem envios (provavelmente R3, junto do fluxo
  de aprovação — não decidido aqui).

---

## 9. Rastreabilidade com R0/R1

Toda referência de nome de campo abaixo já existe hoje, sem modificação, e R2 é responsável
por produzir dados compatíveis com ela:

- `essay_engine_contract.v1.Identification.essay_id` / `.essay_version_id` /
  `.anchor_mode` (`src/agente_ia_edu/essay_engine_contract/v1.py:68-78`) — mesmos nomes,
  mesmo formato (UUID, UUID, `Literal["TEXT_OFFSET","IMAGE_REGION"]`), usados por
  `EssaySubmission.id`/`.essay_id`/`.anchor_mode` acima.
- `services/essay_correction_key.py::normalize_essay_text`/`essay_text_hash` — chamadas
  exatamente uma vez por R2, no momento descrito em cada fluxo do §5, nunca recalculadas.
- `services/essay_correction_key.py::correction_key`'s kwarg `essay_prompt_id` — é
  `EssayPrompt.id` (a proposta), nunca confundido com `prompt_version` (versão do prompt de
  IA, que R2 não produz).
- `db/models/admin.py::SchoolModule` / `PlatformModuleKey.REDACAO_IA` /
  `AuthorizationService.require_module` — reaproveitados sem modificação.
- `db/models/academic.py::User`/`Person`/`Student`/`StudentEnrollment`/`Class` — reaproveitados
  sem modificação; §4 acima é a única peça nova que os une.
