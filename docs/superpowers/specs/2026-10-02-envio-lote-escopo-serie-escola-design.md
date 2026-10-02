# Envio em lote: escopo por série ou escola inteira - Design

## Contexto

O "Enviar em lote" (upload de fotos/PDF de redações físicas escaneadas, aba
do portal do professor em `/teacher`) hoje exige que o professor escolha
**uma única turma** antes de subir as fotos. O casamento automático de
aluno por nome (`EssayBatchService.class_roster`, `essay_batch.py:467-486`)
busca o aluno **só dentro do roster dessa turma** - páginas de alunos de
outras turmas nunca batem com ninguém e caem na fila de resolução manual.

O usuário quer poder subir uma **série inteira** (ou a **escola inteira**)
de uma vez, sem precisar separar o envio turma por turma, deixando o
sistema extrair o aluno da leitura da folha e colocá-lo na turma certa
automaticamente.

## O que já existe e não muda

Duas descobertas da investigação do código tornam esta mudança
deliberadamente pequena:

1. **A atribuição final de turma já vem do aluno, não do lote.**
   `_assignment_for_student` (`essay_batch.py:488-518`) resolve a turma da
   submissão pela matrícula ATIVA real do aluno - há um comentário
   explícito no código citando "spec s7" confirmando que isso é uma decisão
   de design já tomada, não um acidente. Isso já é exercitado hoje no
   caminho de resolução manual. Esta mudança não toca essa função.

2. **Homônimos já são tratados com segurança.** `match_student`
   (`essay_batch.py:177-196`) só casa automaticamente quando EXATAMENTE UM
   aluno do roster tem o nome normalizado igual; zero ou dois-ou-mais vão
   pra fila de resolução manual - nunca um palpite. O CPF nunca é usado
   para desempatar automaticamente (decisão de design já registrada no
   código). Ampliar o roster não introduz um risco novo de categoria -
   só aumenta a frequência com que esse mecanismo de segurança já existente
   é acionado (mais nomes parecidos no universo de busca = mais páginas
   caindo em resolução manual, nunca mais match errado).

## Escopo

**Dentro do escopo:**
- 3 opções de escopo no upload: turma única (como hoje), série inteira
  (`grade_level_id`), escola inteira (nenhum dos dois).
- Migration: `EssayBatchUpload.class_id` vira opcional; novo campo
  `grade_level_id` (opcional, FK para `grade_levels`); CHECK constraint
  garantindo exatamente um dos dois preenchido, ou os dois `NULL` (escola
  inteira).
- `class_roster` generalizada para aceitar o escopo do lote e montar o
  roster certo (turma, série ou escola).
- Os 2 call sites que leem `batch.class_id` hoje (`process_batch`,
  `get_batch_status`) atualizados para usar a função com escopo.
- UI do upload: seletor de escopo (Turma | Série | Escola inteira) com um
  segundo campo dependente.
- API: rota de criação do lote aceita `class_id` OU `grade_level_id`
  (nenhum dos dois = escola inteira).

**Fora do escopo (não mexer):**
- `match_student` (algoritmo de match em si) - já correto, só recebe um
  roster maior.
- `_assignment_for_student` (atribuição final de turma) - já correto.
- `run_corrections` / pipeline de correção em si - nada muda em como a
  correção roda depois que a submissão é materializada.
- Qualquer mudança no fluxo de resolução manual além de alimentar o
  seletor com o roster mais amplo quando aplicável.
- Qualquer coisa relacionada à correção em massa via Batch API
  (`run_mass_correction.py`) - pipeline completamente separado, não é
  tocado aqui.

## Esquema (migration)

```sql
ALTER TABLE essay_batch_uploads
    ALTER COLUMN class_id DROP NOT NULL,
    ADD COLUMN grade_level_id UUID,
    ADD CONSTRAINT fk_essay_batch_uploads_school_grade_level
        FOREIGN KEY (school_id, grade_level_id)
        REFERENCES grade_levels (school_id, id)
        ON DELETE RESTRICT,
    ADD CONSTRAINT ck_essay_batch_uploads_scope
        CHECK (
            (class_id IS NOT NULL AND grade_level_id IS NULL) OR
            (class_id IS NULL AND grade_level_id IS NOT NULL) OR
            (class_id IS NULL AND grade_level_id IS NULL)
        );
```

(sintaxe final ajustada ao padrão real de migration do projeto - Alembic,
mesmo estilo das migrations existentes em `essay_batch.py`'s modelo).

## Matching com escopo

Função nova (substitui as 2 chamadas diretas a `class_roster` com
`batch.class_id`):

```python
async def roster_for_batch(self, batch: EssayBatchUpload) -> list[tuple[uuid.UUID, str, str | None]]:
    """Roster de alunos ativos no escopo do lote: a turma unica, todas as
    turmas da serie, ou a escola inteira - conforme class_id/grade_level_id
    do lote. Mesma forma de retorno de class_roster (reaproveitada
    integralmente para o caso de turma unica)."""
    if batch.class_id is not None:
        return await self.class_roster(school_id=batch.school_id, class_id=batch.class_id)

    condicoes = [
        StudentEnrollment.school_id == batch.school_id,
        StudentEnrollment.status == "ACTIVE",
    ]
    if batch.grade_level_id is not None:
        condicoes.append(
            StudentEnrollment.class_id.in_(
                select(Class.id).where(Class.grade_level_id == batch.grade_level_id)
            )
        )
    rows = (await self.session.execute(
        select(StudentEnrollment.student_id, Person.full_name, Person.document_number)
        .join(Student, Student.id == StudentEnrollment.student_id)
        .join(Person, Person.id == Student.person_id)
        .where(*condicoes)
        .order_by(Person.full_name)
    )).all()
    return [(student_id, full_name, document_number) for student_id, full_name, document_number in rows]
```

`class_roster` (a função de turma única) permanece intocada e é
reaproveitada internamente - não há duplicação de lógica de query, só um
branch no escopo.

## Testes

Fixture com 2 turmas da mesma série: Turma A (aluno "Maria Silva") e
Turma B (aluno "Maria Silva" - homônimo proposital) e Turma C de outra
série (aluno "João Costa", nome único).

- Upload com escopo = série (Turma A + Turma B): página da "Maria Silva"
  cai em resolução manual (2 matches, ambíguo) - comportamento idêntico ao
  de hoje, só que exercitado num roster maior.
- Upload com escopo = série, página de um aluno com nome único na série
  inteira mas que está na Turma B (não a "primeira" turma): casa
  automático, e a submissão final é atribuída à Turma B (a matrícula real
  dele), confirmando que `_assignment_for_student` continua correto sem
  nenhuma mudança.
- Upload com escopo = escola inteira: roster inclui as 3 turmas.
- `roster_for_batch` com turma única continua idêntico ao `class_roster`
  direto (sem regressão no caminho de hoje).

## Riscos e decisões em aberto

1. **Mais páginas em resolução manual em escolas grandes.** Esperado e
   aceito - é o trade-off explícito da decisão de incluir "escola
   inteira" como opção. Não há mitigação nesta leva (ex: usar CPF como
   critério de match) porque isso contradiz a decisão de design já
   registrada no código ("CPF nunca é critério de match automático").
2. **Performance do roster em escolas grandes** (centenas a poucos
   milhares de alunos): o match é um scan linear em memória por página,
   mesmo padrão de hoje - não deve ser um problema real nesse volume, mas
   não foi medido nesta leva.
