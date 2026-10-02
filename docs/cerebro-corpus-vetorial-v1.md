# CÉREBRO — Corpus vetorial v1

Registro do estado exato do primeiro corpus vetorial do piloto, gerado na
Fase 6 passo 5. Existe para que a avaliação oficial seja **reproduzível**:
sem isto, "o Vector v1 deu tal número" não seria uma afirmação verificável.

## Por que este arquivo precisa existir

O corpus do CÉREBRO **não vive num banco permanente**. O banco de
desenvolvimento (`agente_ia_edu`) está carimbado em `059_mass_correction_runs`
— outra linha de migração — e não tem sequer as tabelas do subsistema. Desde a
Fase 3 cada medição constrói um banco descartável a partir dos PDFs e o
destrói ao final.

Isso é defensável (nenhuma medição herda sujeira da anterior), mas tem um
preço: a identidade do corpus não está em lugar nenhum a menos que seja
escrita. É o que este arquivo faz.

## Fontes — hash SHA-256 do PDF

Reconstruir o corpus exige **exatamente** estes arquivos. Um PDF diferente,
ainda que do mesmo livro, produz outro chunking e portanto outros vetores.

| SHA-256 | bytes | arquivo |
|---|---:|---|
| `45ec0d624b26bc0307fa44ffb0640d7b78b5323782cf5de58b326a53cb0578a6` | 114.510.706 | `INQUI_P26_LM_001-544_DIVULGACAO_com-codigo.pdf` |
| `6cfb0c1a1e653a9475aa35e2e75dd609f48f4ec8548fbea1749c0913a4c2ac18` | 62.964.850 | `Moderna-Plus-Quimica-na-abordagem-do-cotidiano-1.pdf` |
| `cf28eb897914135fa22befc2ade21d8fb8914329745531c253180f1e3ef1b866` | 68.715.005 | `Moderna-SuperAcao-Quimica-2.pdf` |
| `880b4e5fce9f525588ba3dc808af127f3380ad3b04f9af18591b68fbd6d11fed` | 1.126.820 | `BNCC_EnsinoMedio_embaixa_site_110518.pdf` |

Direitos: os três primeiros são `COMMERCIAL_REFERENCE` /
`COMMERCIAL_TEXTBOOK`; a BNCC é `OFFICIAL_PUBLIC` / `OFFICIAL`.

## Código que produziu o corpus

| | |
|---|---|
| commit da geração | `323e50e` (Fase 6 passo 4) |
| política de chunking | `knowledge_chunking_policy/v1.py` |
| política de recuperação | `knowledge_retrieval_policy/v1.py`, `POLICY.version` |
| versão do detector editorial | `v1` |
| migrações | `057` … `063` |

## Corpus

| | |
|---|---:|
| chunks totais | 5.945 |
| indexados no léxico | 5.945 |
| classificados pelo detector | 5.945 |
| **elegíveis para embedding** | **5.911** |
| pulados pela política | 34 |

Por papel editorial: `CONTENT` 3.932 · `ANSWER_KEY` 1.537 · `UNKNOWN` 203 ·
`TEACHER_GUIDE` 164 · `REFERENCES` 75 · `TABLE_OF_CONTENTS` 22 ·
`BACK_MATTER` 8 · `FRONT_MATTER` 4.

Os quatro últimos não recebem vetor: nenhum deles é recuperável em propósito
algum, então pagar embedding por eles seria desperdício.

## Espaço de embedding

| | |
|---|---|
| `space_id` | `0197e5a0-0000-7000-8000-000000000001` |
| fingerprint | `8471ea331d464904` |
| provider / model | `openai` / `text-embedding-3-small` |
| dimensions | 1536 |
| distance_metric | cosine |

## Backfill

| | |
|---|---:|
| política | `ELIGIBLE_ROLES` |
| papéis | ANSWER_KEY, CONTENT, REFERENCES, TEACHER_GUIDE, UNKNOWN |
| candidatos | 5.911 |
| **vetores gerados** | **5.911** |
| já existentes | 0 |
| pulados por política | 34 |
| falhas | 0 |
| obsoletos | 0 |
| batches | 93 |
| chamadas ao provider | 98 (5 retentativas, todas recuperadas) |
| tokens cobrados | 3.562.643 |
| custo real | US$ 0,0713 |
| norma média | 0,9999882569515032 |
| vetores unitários | sim |
| tempo | 842,7 s |

## Ativação

| | |
|---|---|
| `activation_id` | `c10f6586-affa-4b88-a972-ada7353ab84c` |
| ação | ACTIVATE |
| espaço anterior | nenhum |
| linhas ativadas | 5.911 |
| degradado | não |
| `expected_population` declarada | 5.911 |

Os seis portões de `readiness()` passaram: `SPACE_NOT_READY`,
`NO_EMBEDDINGS`, `POPULATION_BELOW_EXPECTED`, `MISSING_EMBEDDINGS`,
`STALE_EMBEDDINGS`, `DIMENSION_MISMATCH`.

Cobertura final: 5.911 elegíveis / 5.911 com vetor / 0 faltando / 0
obsoletos. Uma representação ativa por chunk — 5.911 linhas ativas, 5.911
chunks distintos.

## Como reconstruir

1. Banco descartável, `Base.metadata.create_all`.
2. Semear o espaço acima com `status='BACKFILLING'`.
3. Ingerir os quatro PDFs na ordem da tabela, via `KnowledgeSourceService` +
   `KnowledgeDocumentService` (`extract_framework` para a BNCC,
   `extract_and_chunk` para os livros).
4. `EmbeddingBackfillService.backfill(space_id)`.
5. `EmbeddingActivationService.activate(space_id, expected_population=5911)`.

**Limitação conhecida:** `create_all` não cria o índice HNSW parcial — ele
só existe na migração `058`, em SQL cru. Um banco montado por `create_all`
faz varredura sequencial. No piloto isso custa ~60 ms por consulta e não
afeta resultado algum, mas a reconstrução por `create_all` **não** é
equivalente em desempenho à montada por migração.

## Conjuntos congelados — intocados

`git diff 1d5ac9e 323e50e` vazio para `vector_evaluation_sets.py`,
`vector_qrels.py`, `evaluation_sets.py` e `test_vector_evaluation_sets.py`.
10 consultas vetoriais, 133 julgamentos.

Nenhuma consulta de avaliação foi executada contra este corpus. A única
inspeção feita usou `SANITY_ONLY_QUERIES`, cuja exclusão permanente de
Calibration e Evaluation é garantida por `tests/test_sanity_queries.py`.
