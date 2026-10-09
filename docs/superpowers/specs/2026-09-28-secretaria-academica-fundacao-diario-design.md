# Secretaria Acadêmica — Fundação e Diário de Classe

Data: 2026-09-28
Escopo deste documento: blocos **F (Fundação acadêmica)** e **SP1 (Diário de classe e frequência)**.

## 1. Contexto

O projeto possui hoje uma espinha dorsal acadêmica pronta — `School`, `SchoolUnit`,
`Segment`, `GradeLevel`, `AcademicYear`, `Class`, `Student`, `StudentEnrollment` e
`EnrollmentTransition` (`db/models/academic.py`) — além de identidade visual
versionada (`SchoolIdentityVersion`), pipeline de PDF com PyMuPDF
(`services/essay_pdf_export.py`, `services/list_export.py`) e um registro
pedagógico de aula (`TeachingLesson`, `db/models/teaching_context.py`).

Falta tudo que uma secretaria acadêmica precisa em volta disso: componente
curricular, professor como entidade, período letivo, calendário, grade horária,
frequência, notas e documentos oficiais.

### Premissas decididas com o cliente

| Premissa | Decisão |
|---|---|
| Natureza do registro | **Oficial, com fé pública.** Documentos emitidos são os documentos da escola |
| Instituição | **Escola regular de educação básica**, credenciada, com código INEP |
| Situação atual | **Já existe sistema**, este substitui — migração de histórico pregresso é obrigatória |
| Frequência | **Por aula (disciplina)**, em todas as etapas |
| Diário | **Parte de grade horária semanal completa** com aulas previstas |
| Retificação pós-fechamento | **Reabertura do período pela direção** (ver §6.2) |
| Tipos de ausência | **Presente / Falta / Falta justificada** — a justificada conta no percentual |
| Co-docência | **Existe** |
| Divisão de turma em grupos | **Existe, e os grupos podem cruzar turmas** |
| Gerador automático de horário | Registrado como SP8, fora deste spec |

## 2. Decomposição do módulo

O módulo completo são oito blocos com dependência encadeada. Este spec cobre os
dois primeiros; os demais ficam registrados para specs próprios.

| Bloco | Conteúdo | Depende de |
|---|---|---|
| **F** | Componente curricular, professor, grupos, alocação, período letivo, calendário, grade horária, dados civis | — |
| **SP1** | Aula prevista, registro de aula, frequência, fechamento, apuração de carga horária | F |
| SP2 | Notas, instrumentos avaliativos, recuperação, conselho de classe, boletim | F, SP1 |
| SP3 | Resultado final, aprovação, progressão parcial, dependência | SP2 |
| SP4 | Migração do histórico pregresso do sistema atual | F |
| SP5 | Documentos oficiais: histórico escolar, certificado, declarações, livro de registro | SP3, SP4 |
| SP6 | Censo escolar (Educacenso) | F, SP1, SP3 |
| SP7 | Acompanhamento do estudante cruzando registro oficial com evidência de aprendizagem | SP2 |
| SP8 | Gerador automático de grade horária | F |

### Notas de arquitetura registradas para os blocos futuros

- **SP2**: a nota aponta para `(student_enrollment, discipline, academic_term)`, nunca
  para o grupo de ensino. O grupo é o veículo pelo qual a aula aconteceu; o
  histórico é por disciplina. Isso permite ao aluno trocar de grupo no meio do ano
  sem efeito no histórico.
- **SP2**: `ActivityResult` e `AssessmentAttempt`, que já existem, podem **sugerir**
  nota, mas a nota oficial é entidade própria com versionamento e auditoria. No
  regime de fé pública a nota não pode ser um valor derivado e recalculável.
- **SP6**: a forma de declarar no Educacenso grupos de ensino que cruzam turmas
  precisa ser conferida contra o layout do ano vigente do INEP. Não foi verificada
  na elaboração deste spec e não deve ser presumida.
- **SP8**: F já modela disponibilidade de professor, sala e aula geminada, que são
  os habilitadores do gerador. A ferramenta indicada é OR-Tools CP-SAT
  (Apache 2.0), que ainda não é dependência do projeto.

## 3. Modelo de dados — Fundação (F)

Todas as entidades são escopadas por `school_id` e seguem o padrão de
`db/models/academic.py`: chave `Uuid`, `external_id` opcional para conciliação com
o sistema legado, `created_at`/`updated_at`, e `UniqueConstraint (school_id, id)`
quando uma tabela filha precisar referenciá-las por chave composta.

### 3.1 Entidades

**`Discipline`** — componente curricular.
`school_id`, `external_id`, `name`, `short_name`, `knowledge_area`, `ordinal`,
`status`. Espelha o padrão de `Segment` e `GradeLevel`.

**`CurriculumMatrix`** — matriz curricular.
`(grade_level_id, academic_year_id, discipline_id)` → `weekly_lessons`,
`annual_hours`, `is_mandatory`. É o contrato contra o qual a carga horária
realizada é apurada.

**`Teacher`** — professor como entidade.
`person_id`, `registration_code`, `status`. Espelha `Student` exatamente.

**`TeacherAvailability`** — disponibilidade declarada.
`teacher_id`, `weekday`, `period_ordinal` ou turno, `available`. Habilitador de SP8.

**`Room`** — sala ou recurso físico.
`school_unit_id`, `name`, `kind` (comum, laboratório, quadra, auditório),
`capacity`. Habilitador de SP8 e insumo da validação de conflito.

**`AcademicTerm`** — período letivo.
`academic_year_id`, `segment_id` (opcional, permite regime distinto por segmento),
`ordinal`, `name`, `starts_on`, `ends_on`, `status`, mais os campos de reabertura
`reopened_by_user_id`, `reopened_at` e `reopen_reason` descritos na §6.2. Os valores
de `status` estão enumerados na §6.1. Genérico por desenho: atende bimestre,
trimestre ou semestre sem alteração de modelo.

**`SchoolCalendarDay`** — calendário letivo.
`academic_year_id`, `school_unit_id` (opcional), `date`, `kind` (letivo, feriado,
recesso, sábado letivo, evento). É a base da apuração dos 200 dias letivos.

**`TeachingGroup`** — agrupamento de ensino. Entidade central do módulo.
`school_id`, `academic_year_id`, `discipline_id`, `name`, `kind`,
`origin_class_id` (opcional), `school_unit_id` (opcional).

`kind` assume três valores:
- `CLASS_WIDE` — o grupo é a turma inteira. Caso majoritário. `origin_class_id`
  preenchido, permitindo à interface e ao boletim resolverem direto sem varrer
  a lista de membros.
- `SPLIT` — divisão interna de uma turma. `origin_class_id` preenchido.
- `CROSS_CLASS` — eletiva, nivelamento ou itinerário formativo que mistura
  alunos de turmas diferentes. `origin_class_id` nulo.

**`TeachingGroupMember`** — composição do grupo.
`teaching_group_id`, `student_enrollment_id`, `joined_on`, `left_on`. As datas são
obrigatórias ao desenho: a chamada de uma data só lista quem era membro naquela
data.

**`TeachingAssignment`** — alocação docente.
`teaching_group_id`, `teacher_id`, `role` (titular, auxiliar), `starts_on`,
`ends_on`. Relação N:N por natureza, o que resolve co-docência sem caso especial.

**`TimetableSlot`** — grade horária semanal.
`teaching_group_id`, `weekday`, `period_ordinal`, `starts_at`, `ends_at`,
`room_id`, `is_double` (aula geminada).

**`PersonCivilRecord`** — dados civis, 1:1 com `Person`.
`birth_date`, `sex`, `race_color`, `nationality`, `birth_municipality`,
`birth_state`, `mother_name`, `father_name`, `cpf`, `rg`, `nis`,
`birth_certificate`, campos de deficiência.

### 3.2 Por que `PersonCivilRecord` é tabela separada

O Educacenso e o histórico escolar exigem esses campos, e vários deles — cor/raça
e deficiência explicitamente — são dado pessoal **sensível** sob a LGPD, referente
a menores de idade. Em tabela própria o acesso é controlado e auditável por si só,
e `Person`, que é carregada em praticamente toda consulta do sistema, continua sem
transportar dado sensível.

### 3.3 Por que o grupo de ensino é irmão de `Class`, não filho

Como os grupos podem cruzar turmas, uma aula pode não pertencer a turma alguma.
Se `TeachingGroup` fosse filho de `Class`, esse caso exigiria exceção em todo o
fluxo de diário.

Com o grupo como irmão, `Class` volta a ser o que de fato é — agrupamento
administrativo, base da matrícula, do boletim, do conselho de classe e do censo —
e diário, chamada, alocação e grade apontam uniformemente para o grupo.

O custo é uma indireção adicional em toda consulta e linhas de grupo triviais
mesmo onde a escola não divide nada, na ordem de uma por turma × disciplina. Os
grupos `CLASS_WIDE` são criados automaticamente a partir da `CurriculumMatrix`
quando a turma é aberta, e a interface nunca exibe a palavra "grupo" nesse caso:
mostra "Matemática — 1ª A".

A alternativa avaliada e descartada era o grupo opcional, com o diário apontando
ora para turma ora para grupo. Ela parece mais econômica e dobra o caminho de
código em toda consulta, todo fechamento e todo relatório.

### 3.4 Validação de conflito de horário

Porque grupos cruzam turmas, a validação tem três dimensões, não duas:

1. **Professor** — não pode estar alocado a dois grupos no mesmo slot.
2. **Sala** — não pode ser ocupada por dois grupos no mesmo slot.
3. **Aluno** — não pode pertencer a dois grupos que ocupem o mesmo slot.

A terceira só existe por causa dos grupos `CROSS_CLASS`: se a 1ª A e a 1ª B
compartilham um grupo de Inglês, as duas turmas precisam ter aquele horário
sincronizado. A validação vale tanto para a grade montada manualmente quanto para
o gerador de SP8.

## 4. Relação com `TeachingLesson` — a projeção

`TeachingLesson` tem 52 referências em `src/` e alimenta `teacher_portal`,
`coordination_portal`, `initial_diagnostic`, `external_id_resolution` e o
`PedagogicalContext` que gera as recomendações do learning path.

Três caminhos foram avaliados:

- **Módulo paralelo**, sem tocar em `TeachingLesson`. Descartado: o professor
  passaria a registrar a mesma aula duas vezes, abandonaria uma delas, e a
  projeção pedagógica deixaria de receber sinal.
- **Evoluir `TeachingLesson` no lugar**. Descartado: a tabela passaria a servir dois
  regimes com exigências opostas — o registro pedagógico quer ser leve e editável,
  o diário oficial precisa congelar — e exigiria migração invasiva em 52 pontos de
  uso simultaneamente.
- **Diário oficial como fonte, pedagógico como derivado.** Adotado.

`LessonRecord` é a entidade que o professor preenche. Ao ser salvo com status
`DADA` e conteúdo preenchido, o serviço projeta de forma **idempotente** (chaveada
por `lesson_record_id`) o `TeachingLesson` e o `PedagogicalContext` correspondentes.

O contrato de `TeachingLesson` permanece inalterado, e nada a jusante muda.
`TeachingLesson` avulso continua possível para aula fora da grade.

Efeito colateral favorável: `TeachingLesson.teacher_id` e `classroom_id` são hoje
strings soltas. A projeção passa a preenchê-las a partir das chaves estrangeiras
reais, pagando a dívida técnica sem migração destrutiva.

## 5. Fluxo do diário (SP1)

### 5.1 Aula prevista

No início de cada período, o sistema materializa `SchoolCalendarDay` de tipo letivo
× `TimetableSlot` em **`ScheduledLesson`**: uma linha por aula prevista, com data,
grupo, disciplina, professor previsto, sala e ordinal do horário.

A materialização é deliberada, em vez de cálculo sob demanda, por dois motivos: a
aula prevista precisa ser editável individualmente (remarcação, cancelamento por
feriado extraordinário, substituição de professor numa aula específica), e a
comprovação de carga horária perante a supervisão de ensino precisa de registro
concreto a apontar.

### 5.2 Registro da aula

**`LessonRecord`** — `scheduled_lesson_id` (nulo em reposição ou aula extra),
`recorded_by_teacher_id`, `recorded_at`, `status` (dada, não dada, substituída,
cancelada), `content_node_id` (reaproveitando o catálogo de conteúdo existente),
`summary`.

Na co-docência, qualquer professor alocado ao grupo registra, e o `LessonRecord`
guarda quem foi.

A substituição de professor não exige campo próprio: caracteriza-se por
`recorded_by_teacher_id` divergente do professor previsto na `ScheduledLesson`, e o
status `SUBSTITUIDA` a torna explícita para efeito de relatório. O vínculo formal do
substituto, quando houver, é uma `TeachingAssignment` com `starts_on` e `ends_on`
delimitando o período.

**`Attendance`** — `lesson_record_id`, `student_enrollment_id`, `status`
(`PRESENT`, `ABSENT`, `ABSENT_JUSTIFIED`).

A chamada lista quem era membro do grupo **na data da aula**, conforme
`TeachingGroupMember.joined_on` / `left_on`.

**`AttendanceJustification`** — ligada a um `Attendance` de status
`ABSENT_JUSTIFIED`: motivo, documento anexado, quem recebeu, quando.

A falta justificada **conta** no percentual de frequência. A justificativa existe
para comunicação com a família e registro documental, não para alterar o cálculo
legal. Assim o percentual permanece auditável e a justificativa vive à parte.

### 5.3 Apuração

- Carga horária realizada por disciplina, confrontada com `CurriculumMatrix`.
- Dias letivos realizados, confrontados com `SchoolCalendarDay`.
- Frequência por disciplina e frequência global, que alimentam o co-requisito de
  75% no fechamento de ano (SP3).

## 6. Fechamento, reabertura e auditoria

### 6.1 Fechamento

`AcademicTerm.status` percorre `PLANNED → OPEN → CLOSED`, com `REOPENED` quando a
direção reabre.

Ao fechar, o sistema valida pendências (aula prevista sem registro, aula registrada
sem chamada) e congela **`TermAttendanceSummary`** por
`(student_enrollment, discipline, academic_term)`: aulas dadas, faltas, percentual.
O professor perde a escrita sobre o período.

Apenas `DIRECTOR` fecha e reabre.

### 6.2 Reabertura

A direção reabre o período inteiro, o professor corrige normalmente sem formulário
de justificativa, e o período é fechado de novo. `AcademicTerm` registra
`reopened_by_user_id`, `reopened_at` e `reopen_reason`.

Toda escrita ocorrida enquanto o período está `REOPENED` grava um
**`DiaryAuditEvent`** append-only: entidade, identificador, campo, valor anterior,
valor novo, autor e horário. Ao fechar novamente, `TermAttendanceSummary` é
recalculado e a versão anterior é preservada.

**Registro de divergência técnica.** A recomendação original foi retificação por
ato registrado, em que o período permanece fechado e cada correção é um ato
individual com motivo — o comportamento do livro de papel, e o que melhor resiste
a auditoria da supervisão de ensino. O cliente optou pela reabertura do período,
que é mais simples de operar. O `DiaryAuditEvent` é o meio-termo adotado: a
experiência do usuário é a que o cliente escolheu, e a trilha individual existe
por baixo, preservando o valor probatório sem custo para o professor.

## 7. Permissões e superfície

### 7.1 Papéis

| Papel | Permissões neste módulo |
|---|---|
| `TEACHER` | Registra aula e chamada dos seus grupos; escrita apenas com período `OPEN` ou `REOPENED` |
| `SECRETARY` | Cadastros de F, dados civis, apuração, documentos. **Não lança nota nem chamada** |
| `COORDINATOR` | Consulta todos os diários, acompanha pendências, monta grade e grupos |
| `DIRECTOR` | Fecha e reabre período — exclusivo |
| `PLATFORM_ADMIN` | Acesso integral |

Secretaria nunca lança frequência: quem não deu a aula não registra quem estava
nela. É o que torna o diário sustentável perante auditoria.

### 7.2 Escopo do `SECRETARY`

`reject_reception_only_role` (`api/dependencies.py`) é aplicado **router a router**
em `api/app.py`, não globalmente. Os routers novos simplesmente não recebem esse
guard; recebem um guard de domínio próprio. **Nenhuma linha do comportamento atual
é alterada** e a trava permanece válida para tudo que existe hoje.

Conforme `docs/RECEPTION_SECRETARY_INCREMENT_STATUS.md`, o vínculo `SECRETARY` já é
filtrado por escopo `SCHOOL`, `UNIT`, `SEGMENT`, `GRADE_LEVEL` ou `CLASSROOM` via
`UserSchoolLink`. O módulo respeita esse escopo, não apenas o `school_id`.

### 7.3 Interface

**O professor não recebe portal novo.** `teacher.html` já possui a view `lessons`
na navegação; o diário entra ali. A razão não é estética: se a chamada morar em
portal separado, o professor abandona um dos dois, e como a projeção pedagógica
depende do `LessonRecord`, perder a adoção do diário desliga o learning path — o
risco exato que motivou a escolha da projeção na §4.

Como `teacher.js` já tem 2108 linhas, o diário vai em módulo próprio
(`class-diary.js`), seguindo o precedente dos módulos `essay-*.js`.

**A secretaria recebe portal novo:** `secretaria.html`, `secretaria.js`,
`secretaria.css`, no padrão de `coordination` e `admin`, com mount de assets próprio
em `api/app.py`.

### 7.4 API

- **`routes/academic_registry.py`** — cadastros de F: disciplina, professor,
  alocação, sala, calendário, matriz curricular, grade horária, grupos, dados civis.
- **`routes/class_diary.py`** — aula prevista, registro de aula, chamada,
  justificativa, fechamento e reabertura.

## 8. Testes

A suíte roda contra PostgreSQL real na porta 5433 (`tests/conftest.py`,
`docker-compose.yml`).

1. **Migrations.** Seguindo `tests/test_reception_migration_postgresql.py`: banco
   dedicado, `upgrade → downgrade → re-upgrade`, verificando constraints, índices e
   chaves estrangeiras. A suíte completa é executada a cada etapa aditiva — coluna
   nova em tabela existente não se verifica pela leitura da migration.

2. **Isolamento multi-tenant.** Nova família `test_r0_*`, em duas dimensões: escola
   contra escola, e escopo dentro da escola (secretária de unidade não acessa outra
   unidade). A segunda é onde este módulo concentra risco, por tratar dado civil
   sensível de menores.

3. **Regras de fechamento.** Professor sem escrita em período `CLOSED`; apenas
   `DIRECTOR` reabre; escrita em período `REOPENED` gera `DiaryAuditEvent`;
   `TermAttendanceSummary` congela e recalcula.

4. **Prova da projeção.** Salvar `LessonRecord` projeta `TeachingLesson` de forma
   idempotente, **e os testes existentes de `teacher_portal`, `coordination_portal`,
   `initial_diagnostic` e do fluxo ponta-a-ponta continuam passando sem alteração**.
   Necessidade de alterá-los invalida a abordagem da §4 e exige reavaliação.

5. **Composição de grupo no tempo.** Aluno que ingressa em maio não acumula falta em
   março; aluno transferido deixa de contar na data correta.

6. **Cálculo.** Percentual por disciplina e global; falta justificada computada;
   carga horária confrontada com a matriz.

7. **Conflito de horário.** As três dimensões da §3.4, com atenção ao conflito de
   aluno em grupos `CROSS_CLASS`.

## 9. Faseamento

Cada etapa é uma migration aditiva com seus testes e termina em resultado
verificável.

| # | Entrega | Critério de verificação |
|---|---|---|
| 1 | Disciplina, professor, sala, período letivo, calendário, matriz curricular | Secretaria monta o ano letivo completo |
| 2 | Grupos, membros, alocação, grade horária, validação de conflito | Grade fecha sem conflito nas três dimensões |
| 3 | `PersonCivilRecord` (independente, paralelizável) | Ficha civil completa para censo e histórico |
| 4 | Aula prevista, registro de aula, chamada, view em `teacher.html` | Professor realiza chamada real |
| 5 | Projeção para `TeachingLesson` | Suíte existente passa intacta e o learning path recebe sinal |
| 6 | Fechamento, reabertura, auditoria, apuração | Período fecha e emite percentual de frequência |

`scripts/seed_demo_data.py` precisa ser estendido em cada etapa, sob pena de o
portal novo abrir vazio no ambiente de desenvolvimento.

## 10. Riscos

**Volume de dados.** Para uma escola de porte médio — 20 turmas, cerca de 30 aulas
semanais por turma, 40 semanas letivas — a materialização produz aproximadamente
24.000 `ScheduledLesson` por ano e, a 30 alunos por aula, cerca de **720.000
registros de `Attendance` por ano**. `attendance` torna-se a maior tabela do
sistema em poucos meses, superando qualquer tabela existente hoje.

Mitigação decidida na migration, não depois: índices por `(teaching_group, date)` e
por `(student_enrollment, academic_term)`, de modo que o fechamento de período não
varra o ano inteiro, e avaliação de particionamento por ano letivo.

**Identidade em produção.** `docs/RECEPTION_SECRETARY_INCREMENT_STATUS.md` registra
risco residual de o provedor de identidade de produção não declarar corretamente o
papel `SECRETARY` em `ExternalIdentityContext.roles`. Esse risco se estende a este
módulo e cresce com ele, já que a secretaria passa a ter acesso a dado civil
sensível.

**Adoção do diário pelo professor.** É o risco de produto mais relevante. Toda a
arquitetura da §4 e a decisão de interface da §7.3 existem para mitigá-lo: um único
registro, no lugar onde o professor já trabalha.

**Escopo dos blocos seguintes.** SP2 a SP8 representam trabalho de meses. Este spec
não os dimensiona; cada um exige spec próprio.

## 11. Fora do escopo deste spec

Notas e avaliação; resultado final e movimentação; migração do histórico pregresso;
documentos oficiais e livro de registro; Educacenso; painel de acompanhamento do
estudante; gerador automático de grade horária. Todos registrados na §2 com suas
dependências.
