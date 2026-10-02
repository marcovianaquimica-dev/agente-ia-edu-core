# Fluxo de criação/uso de propostas de redação - Design

## Contexto

Hoje a aba "Redação" do portal do professor (`/teacher`, aba "Propostas")
mostra direto uma lista única de propostas (próprias + da plataforma,
estas com selo "Plataforma") e um botão "Nova proposta" que só pede
tema/enunciado/ano. Depois de criada, numa tela separada ("detalhe da
proposta") o professor pode anexar material de apoio (texto ou arquivo -
PDF/imagem já funciona) e atribuir a turmas (multi-seleção, com prazo e
toggle de revisão docente) - mas só turmas inteiras, nunca alunos
específicos nem séries inteiras.

O usuário quer uma tela de entrada que force a escolha explícita entre
**criar uma proposta nova** ou **usar uma do banco** (própria ou da
plataforma) antes de qualquer campo de tema aparecer, com a criação
virando uma tela única (tema + PDF opcional + público, tudo junto), e
público podendo combinar séries inteiras, turmas inteiras e alunos
específicos numa mesma atribuição. Mais um registro de uso: toda vez que
uma atribuição é feita (não a cada envio de redação), fica um histórico
visível só pro professor que atribuiu.

## O que já existe e não muda

- `EssayPrompt`/`PromptMaterial`: criação de proposta e anexo de material
  (inclusive arquivo) já funcionam - reaproveitados sem mudança de
  comportamento, só de onde na UI são acionados.
- Propostas da plataforma (`materialized_from_platform_prompt_id`): já
  aparecem na mesma lista, já são somente-leitura pro professor (só
  atribuir, nunca editar) - vira naturalmente o "banco" compartilhado,
  sem mudança de regra.
- `get_teacher_authorized_classrooms` (`teacher_portal.py`): já resolve
  as turmas que um professor pode ver/agir - reaproveitado pra restringir
  a busca de alunos ao escopo certo.
- `roster_for_batch`/`class_roster` (`essay_batch.py`): o JOIN
  `StudentEnrollment → Student → Person` que já existe 3x nesse arquivo é
  reaproveitado (função nova, mesmo padrão) pra busca de alunos.
- `GET /api/v1/teacher/essay-batches/grade-levels` (feature de envio em
  lote, já em produção): lista as séries reais da escola - reaproveitada
  tal como está pro seletor de público, sem precisar de rota nova.
- A expansão de "série inteira" em turmas (`roster_for_batch`'s ramo de
  `grade_level_id`, via `Class.grade_level_id`) é o mesmo padrão de query
  reaproveitado aqui, só que pra listar `Class.id` em vez de alunos.

## Escopo

**Dentro do escopo:**
- Tela de entrada: "Criar proposta" ou "Usar proposta do banco", sem
  nenhum campo de tema visível antes da escolha.
- "Criar proposta": tela única - tema/enunciado/ano + anexo de PDF
  motivador (opcional) + seletor de público (séries, turmas e alunos
  específicos, combináveis na mesma atribuição) + botão único "Criar e
  atribuir", atômico (tudo ou nada).
- "Usar proposta do banco": lista de propostas (próprias + plataforma)
  com filtro de texto simples → escolhe uma → mesmo seletor de público →
  "Atribuir" (atômico, sem criar proposta nova nem mexer em material).
- `PromptAssignment` generalizada pra aceitar `student_id` opcional além
  de `class_id` (mutuamente exclusivos), permitindo atribuição a um aluno
  específico independente da turma dele ter sido atribuída ou não.
- Busca de alunos por nome, restrita ao escopo autorizado do professor.
- Registro de uso (`PromptAssignmentLog`): 1 linha por clique em
  "Criar e atribuir"/"Atribuir" (não por turma/aluno dentro da mesma
  atribuição), com retrato histórico de quem foi alcançado. Visível só
  pro professor que atribuiu.

**Fora do escopo (não mexer):**
- `match_student`, `class_roster` (algoritmo de match em si) - intocados.
- Registrar uso quando o ALUNO efetivamente envia a redação - só a
  atribuição é logada, não o envio.
- Visão de coordenação/direção sobre o registro de uso - só o professor
  vê o próprio histórico por enquanto.
- Separar a lista do banco em "da plataforma" vs "minhas anteriores" -
  um filtro de texto simples na lista já existente resolve.
- Qualquer mudança na tela de detalhe de uma proposta já existente (a
  seção "Turmas atribuídas" continua existindo do jeito que está hoje,
  como histórico "ao vivo"; o registro de uso é uma seção NOVA ao lado
  dela, não uma substituição).
- Correção em massa via Batch API, envio em lote em si (pipeline
  completamente separado) - só o ponto de interseção abaixo é tocado.

## Pontos de atenção críticos: todo lugar que hoje filtra só por `class_id`

Generalizar `PromptAssignment` pra aceitar `student_id` não vale nada
sozinho - QUALQUER lugar do código que hoje decide "este aluno pode ver/
usar esta atribuição" checando só `class_id` precisa de um segundo ramo,
senão a atribuição individual fica praticamente invisível pro próprio
aluno. Investigação direta do código (não suposição) achou 3 pontos, em
ordem de quão crítico é esquecer cada um:

1. **`_assignment_for_own_class_or_403`** (`api/routes/essay_submissions.py:133`)
   - o PORTÃO real antes do aluno conseguir começar a escrever/enviar uma
   redação (`create_essay_submission`, rota `POST /api/v1/essay-submissions`).
   Hoje: `assignment.class_id != class_id` → 403. Sem o segundo ramo, um
   aluno atribuído individualmente veria a proposta na lista (se o item 2
   abaixo for corrigido) mas tomaria 403 ao tentar de fato escrever - o
   pior tipo de bug nesta feature, porque parece que funcionou até o
   último passo. Precisa passar `student_id` pra essa função também (hoje
   só recebe `class_id`) e aceitar `assignment.class_id == class_id OR
   assignment.student_id == student_id`.
2. **`list_essay_prompts_for_student`** (`api/routes/essay_submissions.py:773`)
   - a lista de propostas que o aluno vê pra responder (`GET .../prompts`
   do aluno). Hoje: `PromptAssignment.class_id == enrollment.class_id`.
   Mesmo ajuste: `or_(PromptAssignment.class_id == enrollment.class_id,
   PromptAssignment.student_id == enrollment.student_id)`.
3. **`EssayBatchService._assignment_for_student`** (`services/essay_batch.py:540`,
   já revisado e testado nesta sessão, parte do envio em lote por série/
   escola recém-integrado) - resolve a turma REAL do aluno casado
   automaticamente via JOIN por `class_id`. Mesmo ajuste: um segundo ramo
   de busca direta por `PromptAssignment.student_id == student_id`.

Em TODOS os 3 pontos, se os dois ramos acharem resultado pra mesma
proposta (aluno tem atribuição por turma E por student_id direto - caso
raro mas possível), a atribuição por turma vence (mesma prioridade de
hoje, menor mudança de comportamento, e é a que normalmente tem
`due_at`/`validation_enabled` mais recentes de uma atribuição em massa).

## Esquema (migration)

```sql
ALTER TABLE prompt_assignments
    ALTER COLUMN class_id DROP NOT NULL,
    ADD COLUMN student_id UUID,
    ADD CONSTRAINT fk_prompt_assignments_school_student
        FOREIGN KEY (school_id, student_id)
        REFERENCES students (school_id, id)
        ON DELETE RESTRICT,
    ADD CONSTRAINT ck_prompt_assignments_target
        CHECK (
            (class_id IS NOT NULL AND student_id IS NULL) OR
            (class_id IS NULL AND student_id IS NOT NULL)
        );

-- unique parcial: um aluno so pode ser atribuido uma vez a mesma proposta
-- diretamente (a UniqueConstraint existente uq_prompt_assignments_prompt_class
-- ja cobre o caso de turma e continua intocada)
CREATE UNIQUE INDEX uq_prompt_assignments_prompt_student
    ON prompt_assignments (essay_prompt_id, student_id)
    WHERE student_id IS NOT NULL;
```

(sintaxe final ajustada ao padrão real de migration Alembic do projeto -
mesmo estilo da migration 061, que fez a mesma generalização em
`essay_batch_uploads`.)

```sql
CREATE TABLE prompt_assignment_logs (
    id UUID PRIMARY KEY,
    school_id UUID NOT NULL,
    essay_prompt_id UUID NOT NULL,
    assigned_by_external_identity VARCHAR(255) NOT NULL,
    created_at TIMESTAMPTZ NOT NULL,
    -- retrato historico, nao referencia viva: sobrevive a turma renomeada,
    -- aluno transferido ou desatribuido depois. Formato:
    -- {"turmas": [{"class_id": "...", "name": "..."}],
    --  "series": [{"grade_level_id": "...", "name": "...",
    --              "turmas_expandidas": [{"class_id": "...", "name": "..."}]}],
    --  "alunos": [{"student_id": "...", "name": "..."}]}
    target_summary JSONB NOT NULL,
    FOREIGN KEY (school_id, essay_prompt_id)
        REFERENCES essay_prompts (school_id, id) ON DELETE RESTRICT
);
CREATE INDEX ix_prompt_assignment_logs_essay_prompt_id
    ON prompt_assignment_logs (essay_prompt_id);
```

## Lógica de negócio

### Turma/aluno já atribuído antes

`uq_prompt_assignments_prompt_class` (já existe) e
`uq_prompt_assignments_prompt_student` (nova, acima) impedem duplicar uma
atribuição. Reatribuir uma turma/aluno que já tinha recebido a mesma
proposta (ex: o professor clica "Atribuir" de novo, ou uma série
expandida inclui uma turma que já foi atribuída individualmente antes)
precisa ser **idempotente**, não um erro: a criação de
`PromptAssignment` ignora silenciosamente o alvo que já existe (não
duplica, não quebra a transação), mas o `PromptAssignmentLog` registra a
tentativa inteira do jeito que o professor pediu (inclusive o alvo que já
existia) - o log é um retrato da AÇÃO do professor, não só do que mudou
no banco.

### Expansão de público em linhas de `PromptAssignment`

Função nova em `EssayProposalService` (ou serviço equivalente real -
confirmar na implementação qual arquivo já hospeda a lógica de
`PromptAssignment` hoje), recebendo o público bruto do formulário
(`class_ids`, `grade_level_ids`, `student_ids`) e produzindo a lista
final de alvos:

```python
async def resolve_assignment_targets(
    self, *, school_id: uuid.UUID,
    class_ids: list[uuid.UUID], grade_level_ids: list[uuid.UUID],
    student_ids: list[uuid.UUID],
) -> dict:
    """Expande series em turmas reais (Class.grade_level_id), junta com as
    turmas escolhidas direto (sem duplicar se uma turma aparecer nos dois),
    e devolve o conjunto final de class_ids + student_ids a virar
    PromptAssignment, junto com os nomes resolvidos pro snapshot do log."""
```

### `_assignment_for_student` com o segundo ramo

```python
async def _assignment_for_student(
    self, *, school_id: uuid.UUID, essay_prompt_id: uuid.UUID, student_id: uuid.UUID
) -> PromptAssignment:
    # Ramo 1 (existente, intocado): por turma ativa do aluno.
    assignment = await self.session.scalar(
        select(PromptAssignment)
        .join(StudentEnrollment, StudentEnrollment.class_id == PromptAssignment.class_id)
        .where(
            PromptAssignment.school_id == school_id,
            PromptAssignment.essay_prompt_id == essay_prompt_id,
            StudentEnrollment.student_id == student_id,
            StudentEnrollment.status == "ACTIVE",
        )
        .order_by(PromptAssignment.created_at)
    )
    if assignment is not None:
        return assignment
    # Ramo 2 (novo): atribuicao direta ao aluno, independente de turma.
    assignment = await self.session.scalar(
        select(PromptAssignment).where(
            PromptAssignment.school_id == school_id,
            PromptAssignment.essay_prompt_id == essay_prompt_id,
            PromptAssignment.student_id == student_id,
        )
    )
    if assignment is None:
        raise ValueError(
            "Esta proposta nao esta atribuida a nenhuma turma ativa nem "
            "diretamente a este aluno."
        )
    return assignment
```

## API

**Reaproveitados tal como estão, sem mudança** (investigação direta do
código, `api/routes/essay_prompts.py`, confirmou que já fazem exatamente
o que a tela única de criação precisa):
- `POST /api/v1/catalog/essay-prompts` (`create_essay_prompt`): cria a
  `EssayPrompt` (DRAFT), já é o passo 1 da tela única.
- `POST /api/v1/catalog/essay-prompts/{id}/materials/upload`
  (`upload_prompt_material`): já aceita qualquer arquivo (inclusive PDF)
  como material - é o passo 2 (opcional) da tela única, sem mudança
  nenhuma na rota.
- `_materialize_if_platform_prompt`: já resolve "usar proposta do banco"
  quando o id escolhido é de uma `PlatformEssayPrompt` ainda não
  materializada nesta escola - a atribuição nova abaixo entra DEPOIS
  dessa materialização, reaproveitando o mesmo helper que
  `create_prompt_assignment`/`create_prompt_assignments_bulk` já chamam.

**Novo**, em `api/routes/essay_prompts.py` (mesmo arquivo das rotas
acima, não um arquivo novo):
- `POST /api/v1/catalog/essay-prompts/{id}/assignments/combined` -
  público combinado (`class_ids[]`, `grade_level_ids[]`, `student_ids[]`),
  usado pelos 2 fluxos (depois de criar OU depois de escolher do banco).
  Diferente de `assignments/bulk` (que já existe, continua intocada,
  só turma, best-effort por turma com commit incremental - usada pela
  seção "Turmas atribuídas" que já existe na tela de detalhe): esta rota
  nova expande série em turmas, junta com `class_ids`/`student_ids`
  diretos, PRÉ-CONSULTA quais alvos já têm `PromptAssignment` pra essa
  proposta (pulando os já atribuídos em vez de deixar o `IntegrityError`
  estourar), cria as linhas realmente novas, e grava 1
  `PromptAssignmentLog` com a tentativa inteira (inclusive os alvos que já
  existiam) - tudo num commit só, já que não há `IntegrityError` a
  tratar. Público vazio (nenhum dos 3) é 422 antes de qualquer consulta.
- `GET /api/v1/catalog/essay-prompts/students-search?q=...`: busca por
  nome dentro do escopo autorizado do professor
  (`get_teacher_authorized_classrooms`), devolve `[{student_id, full_name,
  document_number, class_name}]` - mesmo JOIN de `roster_for_batch`,
  filtrado por nome em vez de turma/série/escola.
- `GET /api/v1/catalog/essay-prompts/{id}/assignment-log`: histórico de
  atribuições dessa proposta, mais recente primeiro.

**Reaproveitada tal como está** (feature de envio em lote, já em
produção): `GET /api/v1/teacher/essay-batches/grade-levels`.

**A tela única "Criar e atribuir" não é uma transação de banco única
ponta a ponta** - ela orquestra 2-3 chamadas client-side em sequência
(criar proposta → upload de material, se houver PDF → atribuir
combinado), o mesmo jeito que a tela de detalhe JÁ faz hoje com 2 chamadas
separadas (o código só reordena quando cada uma acontece). Isso é
deliberado, não uma simplificação: `_materialize_if_platform_prompt`
(código já existente) já documenta e aceita esse mesmo risco pro fluxo de
atribuição de proposta de plataforma hoje - se uma chamada no meio falhar,
sobra uma `EssayPrompt` DRAFT sem atribuição nenhuma, que é inofensiva
(não aparece pra nenhum aluno, o professor só tenta de novo ou a edita
depois) e seria descartável manualmente via a "Lixeira" que já existe.

## Frontend

- `essay-review.js`, aba "Redação": `renderPromptsList` vira a tela de
  entrada (2 botões, sem campos de tema).
- `renderNewPromptForm`: tema/enunciado/ano (como hoje) + input de
  arquivo (PDF, opcional) + `renderAudiencePicker` embutido + botão
  único "Criar e atribuir".
- `renderAudiencePicker(container)`: componente novo reaproveitado nos 2
  fluxos - 3 blocos (séries com checklist, turmas com checklist, busca de
  alunos com chips dos selecionados), devolve `{class_ids, grade_level_ids,
  student_ids}` pro form que o usa.
- Tela nova "Usar proposta do banco": lista com campo de filtro de texto
  (client-side, filtra o array já carregado) → clique leva pro mesmo
  `renderAudiencePicker` + botão "Atribuir".
- Tela de detalhe de uma proposta já existente (`renderPromptDetail`):
  ganha uma seção nova "Histórico de atribuições", lendo
  `GET .../assignment-log` - a seção "Turmas atribuídas" que já existe
  continua exatamente como está.

## Testes

- Migration: `class_id` opcional, `student_id` novo, CHECK (3 estados
  válidos + 1 inválido), unique parcial de `(essay_prompt_id, student_id)`,
  upgrade e downgrade - banco descartável porta 5433, mesmo padrão da 061.
- `resolve_assignment_targets`: série expande nas turmas certas sem
  duplicar; turma repetida nos dois conjuntos (direta + via série) conta
  uma vez só.
- Os 3 pontos críticos listados acima, cada um com teste próprio: aluno SÓ
  com atribuição direta (sem a turma dele atribuída) consegue (a) aparecer
  na própria lista de propostas, (b) efetivamente criar uma submissão via
  `POST /api/v1/essay-submissions`, e (c) ser casado corretamente pelo
  envio em lote; aluno com os dois tipos de atribuição pra mesma proposta
  usa a atribuição por turma primeiro nos 3 pontos; regressão explícita
  provando que o comportamento só-turma de hoje (nenhuma das 3 funções
  muda de resultado quando `student_id` nunca é usado) continua idêntico.
- Endpoint de atribuição combinada: sucesso com `class_ids`+`grade_level_ids`
  +`student_ids` misturados; público vazio é 422 antes de qualquer
  consulta; reatribuir uma turma/aluno que já tinha essa proposta é
  idempotente (sem erro, sem linha duplicada, sem bater no
  `IntegrityError`), mas ainda gera um `PromptAssignmentLog` novo.
- Busca de alunos: só devolve alunos dentro do escopo autorizado do
  professor (nunca de turma que ele não pode ver).
- `PromptAssignmentLog`: 1 registro por clique (não por turma/aluno
  dentro da mesma atribuição); snapshot de nomes sobrevive a turma
  renomeada depois.
- Frontend: as 2 telas novas (node:test contra texto-fonte, mesmo padrão
  já usado nos arquivos de teste de frontend deste projeto) +
  `renderAudiencePicker` reaproveitado nos 2 fluxos.

## Riscos e decisões em aberto

1. ~~Nome real do serviço/arquivo que hospeda a lógica de
   `PromptAssignment`~~ - **resolvido**: `services/essay_proposal.py`
   (`EssayProposalService`), confirmado por leitura direta antes de
   escrever o plano. `api/routes/essay_prompts.py` já tem quase toda a
   infraestrutura de rota necessária (criação, upload de material,
   atribuição por turma) - ver seção API acima, revisada com base nisso.
2. **Volume de alunos pra busca** - sem paginação nesta leva (YAGNI); se
   uma escola tiver milhares de alunos visíveis a um professor
   school-wide, a busca por nome pode precisar de um limite de resultados
   (ex: top 20) - decisão de implementação, não deveria mudar o design.
3. **Download/preview do PDF motivador já anexado** - a UI de material
   de apoio já existente (`renderMaterialLabel`) provavelmente já cobre
   isso; confirmar na implementação que a listagem de materiais no
   `renderPromptDetail` mostra também o que foi anexado na tela única de
   criação (deveria, já que ambos usam `PromptMaterial`).
