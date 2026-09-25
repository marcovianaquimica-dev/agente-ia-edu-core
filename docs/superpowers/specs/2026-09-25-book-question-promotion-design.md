# Promoção de exercícios extraídos de livro autoral para o Banco de Questões

**Data:** 2026-09-25
**Status:** Aprovado para plano de implementação

## Contexto

O motor de ingestão de material autoral (PHASE 26, `services/authorial_material_ingestion.py`)
já processa upload → extração de estrutura → classificação curricular determinística →
aprovação → publicação como `TheoryMaterial`. Ao longo desse processo, exercícios/questões
são detectados no texto e persistidos em `IngestionQuestion` — mas, por decisão de design
explícita e testada (`test_exercises_are_not_official_questions`), nunca viram uma `Question`
oficial automaticamente.

Uma importação real de teste (`quimica 1.pdf`, 100 páginas, via `AuthorialMaterialIngestionService`
contra o Postgres dev, escola real `SCH_A`) confirmou:
- Classificação curricular automática do documento: `CHEMISTRY > CHEMISTRY-PHYSICAL >
  CHEMISTRY-PHYSICAL-KINETICS`, confiança **0.500**.
- 204 candidatos de exercício detectados; 121 `MULTIPLE_CHOICE`, 83 `OTHER`.
- **0 de 121** questões de múltipla escolha vieram com `correct_answer` preenchido — o
  gabarito, quando existe no livro, fica isolado no fim do volume, fora do alcance do
  extrator por-questão atual.
- ~92 candidatos têm tag de vestibular reconhecível no enunciado (UNESP, UFU-MG, FCMMG etc.)
  — sinal forte de que são questões reais, não ruído de parágrafo de teoria.
- Existe uma noção de "score de confiança" ligada à classificação do documento inteiro, não
  por questão.

Existe já um importador de questão para o Banco (`services/question_bank_importer.py`,
`QuestionBankImporter.import_question`), mas ele é uma trilha paralela hardcoded para
cadernos oficiais do ENEM/INEP (`_official_context` monta a cadeia
Institution→Exam→ExamApplication→ExamBooklet→SourceDocument→AnswerKeyRevision a partir de
metadado `exam_year`/`exam_day`/`booklet` que só existe nessa origem) e **exige**
`correct_answer` preenchido (`_validate` rejeita sem gabarito). Não serve para material
autoral sem gabarito adjacente — confirmado por leitura completa do arquivo, não assumido.

## Objetivo

Permitir que um coordenador/professor revise os candidatos extraídos de um livro importado
e promova os que valem a pena diretamente para uma `Question` publicada e visível aos
alunos, com o mínimo de máquina nova — reaproveitando o pipeline de autoria de professor já
existente e testado (`services/content_authoring.py::QuestionAuthoringService`, 99%+ de
cobertura, sem bugs conhecidos).

## Decisões de escopo (confirmadas com o usuário)

1. **Ruído:** aplicar um filtro/ranking heurístico antes da revisão (não mostrar tudo cru).
2. **Classificação:** a questão promovida herda os *códigos* de disciplina/área/conteúdo do
   documento (não o score numérico do matcher determinístico).
3. **Estado inicial:** a aprovação humana na tela de revisão já conta como aprovação
   pedagógica — não passa por uma segunda fila de revisão do Banco de Questões.
4. **Publicação:** a promoção publica direto (`PUBLISHED`), sem passo intermediário de
   "aprovado mas não publicado".
5. **Fora de escopo para v1 (YAGNI):** endpoint de "descartar candidato" — quem não quiser
   promover simplesmente ignora; não é destrutivo, e o item continua disponível.

## Resolução do conflito de confiança

`QuestionAuthoringService.publish_question` já exige, sem exceção, uma
`PedagogicalClassification` com `status="CLASSIFIED"`, `lifecycle="ACTIVE"` e
`classification_confidence >= 0.7`. A confiança de 0.500 do documento fica abaixo desse
piso — herdar o número literal bloquearia a publicação direta pedida no item 4 acima, para
este livro e provavelmente para outros.

**Resolução:** a `PedagogicalClassification` da questão promovida herda os *códigos*
(discipline/content/subcontent) do documento, mas a `classification_confidence` reflete o
fato de que um humano confirmou aquele enquadramento durante a promoção — não o score do
matcher automático. Valor fixo alto (ex.: `1.0`), `source="human_reviewed_promotion"`,
mesmo padrão que `suggest_classification` já usa para decidir `CLASSIFIED` vs
`NEEDS_REVIEW` por confiança.

## Arquitetura

```
IngestionQuestion (extraído, texto original nunca editado)
        |
        |  GET /api/v1/catalog/ingestion/{review_id}/candidate-questions
        |  (lista filtrada/priorizada - ver "Filtro e ranking")
        v
Tela de revisão: pessoa edita enunciado/alternativas/gabarito
        |
        |  POST /api/v1/catalog/ingestion/{review_id}/candidate-questions/{id}/promote
        |  body: { statement, options: [str, ...], correct_option: str }
        v
  UMA transação, reusando QuestionAuthoringService sem alteração:
    1. create_question(statement, options, correct_option,
                        author_type="AUTHOR_INGESTION")
    2. suggest_classification(discipline/content/subcontent herdados do
                               documento, confidence=1.0,
                               source="human_reviewed_promotion")
    3. submit_for_review -> approve -> publish_question
       (cadeia completa em sequência, sem parar em estado intermediário -
        a resposta HTTP só expõe o resultado final: PUBLISHED)
        |
        v
  IngestionQuestion.promoted_question_version_id = <version.id>
  IngestionQuestion.promoted_at = now()
  IngestionQuestion.promoted_by_external_identity = <quem promoveu>
```

Se qualquer passo da cadeia falhar, a transação inteira reverte - nunca fica uma `Question`
órfã em estado intermediário. Nada disso toca `MaterialExercise` nem o fluxo de material
teórico - são trilhas paralelas e independentes que só compartilham a origem
(`IngestionQuestion`).

## Filtro e ranking

**Exclusão** (não aparece na lista, não é promovível como está):
- `question_type == 'OTHER'` **e** `alternatives_text IS NULL` - via de regra é parágrafo de
  teoria confundido com exercício.

**Priorização** (ordena, não exclui):
1. Enunciado bate com a mesma regex de proveniência de vestibular usada na contagem real
   feita durante o brainstorming (92/204 candidatos bateram):
   `\(UNESP|\(UFU|\(FCMMG|\(UNICAMP|\(FUVEST|\(UFRJ|\(ITA|\(UFMG|\(PUC|\(UFRGS|\(UERJ|
   \(CESGRANRIO|\(ENEM` - prioridade alta. Lista fechada para v1; ampliar depois é um ajuste
   de uma linha, não uma mudança de design.
2. `question_type == 'MULTIPLE_CHOICE'` com `alternatives_text` presente e bem-formado
   (≥3 linhas no padrão `letra) texto`, sem sinal de vazamento para a próxima questão) -
   prioridade média.
3. Resto (ainda visível, só no fim da fila) - prioridade baixa.

Candidatos já promovidos (`promoted_question_version_id IS NOT NULL`) nunca aparecem na
lista.

## Modelo de dados

Migration aditiva em `ingestion_questions`:

```sql
ALTER TABLE ingestion_questions
  ADD COLUMN promoted_question_version_id UUID NULL
    REFERENCES question_versions(id) ON DELETE RESTRICT,
  ADD COLUMN promoted_at TIMESTAMPTZ NULL,
  ADD COLUMN promoted_by_external_identity VARCHAR(255) NULL;
```

Nenhuma mudança em `Question`/`QuestionVersion`/`QuestionOption`/`PedagogicalClassification`
- usados exatamente como já existem. `QuestionVersion.metadata_` da questão promovida grava
`{"origin_type": "AUTHOR_INGESTION", "ingestion_question_id": ..., "ingestion_document_id":
...}` para rastreabilidade, seguindo a convenção de metadata já usada no resto do arquivo.

## Endpoints novos

Ambos em `ingestion_router` (`api/routes/authorial_ingestion.py`), como sub-recursos de
`/ingestion/{review_id}/...`, reusando `_authorize` (papel TEACHER/COORDINATOR/DIRECTOR/
PLATFORM_ADMIN) e `_require_review_access` (escopo por escola, já com o gate corrigido na
leva 2 desta campanha para `list_ingestions`).

### `GET /api/v1/catalog/ingestion/{review_id}/candidate-questions`
Lista os candidatos filtrados/ordenados (ver "Filtro e ranking"), cada um com as
alternativas já parseadas em `[{key, text}]` quando possível, para a UI pré-preencher o
formulário.

### `POST /api/v1/catalog/ingestion/{review_id}/candidate-questions/{ingestion_question_id}/promote`
Corpo: `{ statement: str, options: [str, ...], correct_option: str }` (texto confirmado/
editado pela pessoa). Executa a cadeia da seção "Arquitetura" numa transação. Retorna
`{ question_id, question_version_id, status: "PUBLISHED" }`. Se o candidato já foi
promovido antes, responde 409 sem duplicar.

## Estratégia de teste

Mesmo rigor usado nas 9 levas anteriores desta campanha:

- Migration: upgrade/downgrade isolado, mesmo padrão de `test_kinetics_taxonomy_migration.py`.
- HTTP-level, TDD RED→GREEN, sessão com `expire_on_commit=True` (fidelidade de produção -
  a mesma configuração que revelou 15 bugs reais de `MissingGreenlet` nas levas anteriores,
  incluindo dois no próprio `question_bank_importer.py`).
- Casos: fluxo feliz (candidato -> `Question` `PUBLISHED` com classificação
  `confidence=1.0`/`source="human_reviewed_promotion"`); double-promote -> 409 sem duplicar;
  escopo por escola (professor de outra escola não lista nem promove); falha no meio da
  cadeia -> rollback completo sem `Question` órfã; filtro exclui candidato sem alternativa;
  ranking prioriza candidato com tag de vestibular.
- Sem teste novo para `QuestionAuthoringService` em si - já coberto, só é chamado em
  sequência.

## Fora de escopo (explícito)

- Endpoint de descarte/rejeição de candidato (YAGNI para v1).
- Reclassificação por-exercício via IA ou matcher determinístico individual - a
  classificação é sempre herdada do documento (decisão de escopo #2).
- Qualquer mudança em `QuestionBankImporter`/pipeline de exame oficial ENEM - trilha
  totalmente separada, não tocada.
- Melhoria da extração em si (detecção de capítulo, ruído em alternativas, gabarito
  distante) - fica para uma iteração futura, se necessário.
