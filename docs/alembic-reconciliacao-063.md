# Reconciliação das linhagens Alembic

**2026-10-04.** O sintoma era "duas migrations 063". Era maior que isso.

---

## 1. Grafo antes

```
055_essay_prompt_soft_delete
├── 057_essay_batch_upload            ─┐
│   058_platform_essay_prompts         │
│   059_mass_correction_runs           │  ramo PLATAFORMA/REDAÇÃO
│   060_mass_correction_cancelling     │  (main)
│   061_essay_batch_scope              │  7 migrations
│   062_prompt_assignment_target       │
│   063_platform_material_target  HEAD ─┘
│
└── 057_knowledge_engine_corpus       ─┐
    058_knowledge_engine_embeddings    │
    059_partial_extraction_status      │
    060_curriculum_bncc_links          │  ramo CÉREBRO/ALUNO
    061_lexical_index                  │  (fase6/vetorial)
    062_editorial_role                 │  9 migrations
    063_embedding_activation           │
    064_study_session_readiness        │
    065_classification_provenance HEAD ─┘
```

**Não existe 056 em nenhum dos lados** — o número foi pulado.

## 2. Grafo depois

```
                    ... 063_platform_material_target ─┐
                                                      ├─ 066_merge_063_lineages  HEAD
... 064_study_session_readiness → 065_classification_provenance ─┘
```

Um head. Nenhuma revisão existente teve `down_revision` reescrito.

## 3. Origem da divergência

Dois trabalhos independentes correram em paralelo a partir de
`055_essay_prompt_soft_delete` e **reusaram os números 057–063**. O commit que
criou `063_platform_material_target` é `6573814`, de 2026-10-03, em `main`.
`063_embedding_activation` veio de `47f5537`, na branch `fase6/vetorial`.

As branches divergiram bastante: **147 commits** em main que o worktree não
tem, **63** no worktree que main não tem.

## 4. DDL de cada linhagem

| | tabelas tocadas |
|---|---|
| **plataforma** | `essay_batch_uploads`, `essay_batch_pages`, `platform_essay_prompts`, `essay_prompts`, `mass_correction_runs`, `prompt_assignments`, `prompt_assignment_logs`, `prompt_materials`, `schools` |
| **CÉREBRO** | `knowledge_sources`, `knowledge_documents`, `knowledge_chunks`, `knowledge_chunk_terms`, `knowledge_chunk_embeddings`, `knowledge_embedding_spaces`, `knowledge_chunk_lexical_index`, `knowledge_lexical_index_state`, `knowledge_embedding_activations`, `curriculum_bncc_links`, `curriculum_bncc_link_reviews`, `study_sessions`, `pedagogical_classifications` |
| **interseção** | **nenhuma** |

**Classificação: A — independentes e compatíveis.** Nenhuma desfaz o que a
outra fez; nenhuma depende da outra.

## 5. Estratégia

**Merge revision oficial do Alembic**, com dois `down_revision`. É o mecanismo
que descreve a história real: dois ramos que existiram de fato e agora se
juntam.

O que **não** foi feito, de propósito:

- renumerar migrations;
- reescrever `down_revision` de revisões já aplicadas em banco real;
- escolher uma das 063 como "a certa";
- `stamp` manual ou edição de `alembic_version`.

As 7 migrations de main foram trazidas para o worktree como **arquivos**. Antes
disso verifiquei que todas importam **apenas** `alembic` e `sqlalchemy` — nenhuma
toca código da aplicação, então elas não arrastam os 147 commits de main.

**`066_merge_063_lineages` não tem DDL.** Há teste que varre a AST do arquivo e
falha se alguém puser `op.` ali: merge que mexe no schema esconde uma migration
de verdade.

## 6. Testes A/B/C/D

| cenário | o que prova | resultado |
|---|---|---|
| **A** | banco vazio → head | chega em `066` com tabelas dos dois ramos |
| **B** | banco do ramo plataforma → reconciliação → head | ganha as 11 tabelas do CÉREBRO |
| **C** | banco do ramo CÉREBRO → reconciliação → head | chega ao mesmo head |
| **D** | desce até `055` e sobe de novo | schema idêntico ao original |

Os três caminhos são comparados **tabela a tabela e coluna a coluna**, não só
"o upgrade não explodiu".

O cenário B começa provando que *é* o cenário B: antes de reconciliar, tem as
tabelas da plataforma e **não** tem as do CÉREBRO.

**Sem skip silencioso:** se o PostgreSQL não estiver de pé, os testes falham
com mensagem explícita, em vez de passar por omissão.

## 7. Schema final e dados preservados

O banco de desenvolvimento foi reconciliado **depois** dos testes, com backup
verificado antes.

| | antes | depois |
|---|---|---|
| revisão | `063_platform_material_target` | `066_merge_063_lineages` |
| tabelas | 113 | **124** |

**Dados preservados, conferidos linha a linha:**

| tabela | registros |
|---|---:|
| `questions` | 561 |
| `question_versions` | 561 |
| `question_options` | 2.074 |
| `extracted_questions` | 2.972 |
| `pedagogical_classifications` | 542 |
| `prompt_materials` | 19 |
| `platform_essay_prompts` | 17 |
| `essay_prompts` | 9 |
| `domain_content_mastery` | 29 |

Nenhuma perda. As 11 tabelas novas do ramo CÉREBRO nasceram vazias, como
esperado — exceto `knowledge_embedding_spaces`, que a própria migration semeia
com o espaço padrão.

### O backup

`~/backups-nucleo/dev_pre_reconciliacao_066.dump`, 5 MB, formato custom.

**Verificado restaurando de verdade** num banco descartável antes de qualquer
alteração: 561 questões, 2.972 extraídas, 542 classificações, 19
prompt_materials, carimbo `063_platform_material_target`. Arquivo não é backup;
backup é o que restaura.

## 8. Efeito colateral resolvido

O bloco anterior terminou com **31 falhas** na suíte: os testes que usam o app
real contra o banco de dev quebravam com

```
column pedagogical_classifications.provenance does not exist
```

A coluna é da migration 065, que o banco de dev não conseguia receber. Depois
da reconciliação, **os 347 testes desses 15 arquivos passam**.

## 9. Dívidas

1. **As branches continuam divergentes no código.** Só as migrations foram
   reunidas. O merge de `main` em `fase6/vetorial` (147 × 63 commits) continua
   pendente e é outro trabalho.
2. **Três testes meus fixavam "head == X"** e quebraram quando o head mudou —
   dois agora, um no bloco anterior. Corrigidos para medir o que de fato
   queriam dizer: a migration foi aplicada e as colunas existem. O padrão vale
   para quem escrever o próximo.
3. **`alembic_version` passa a ter mais de uma linha** em bancos que descem
   abaixo do merge. Código que lê `.scalar()` dessa tabela pega uma linha
   arbitrária — já corrigi nos meus testes, mas pode haver outros lugares.
4. **O CLI do alembic precisa de `PYTHONPATH` apontando para o worktree.** O
   editable install do `.venv` resolve para o checkout principal, e a migration
   058 importa `VectorCompatible` do código da aplicação.
