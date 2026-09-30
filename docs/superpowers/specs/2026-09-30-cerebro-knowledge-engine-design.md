# CÉREBRO / Knowledge Engine — Design

Data: 2026-09-30
Status: aprovado (design); implementação não iniciada além da Fase 0
Escopo: piloto com ~5 livros didáticos de Química + BNCC

---

## 1. Objetivo e recorte

Cadastrar fontes de conhecimento (livros didáticos de terceiros, BNCC, material
próprio), localizar conhecimento relevante nelas a partir de um pedido em texto
livre ("Estequiometria"), cruzar múltiplas fontes e produzir um **Knowledge
Pack**: uma estrutura própria do sistema, com rastreabilidade até documento e
página, que posteriormente alimenta a geração de conteúdo didático autoral.

Pipeline conceitual:

```
Sources → Ingestion → Chunking → Metadata → Embeddings → Retrieval → Knowledge Pack → Generation
```

A etapa `Generation` está **fora** deste design. O piloto entrega o motor até o
Pack inclusive.

### 1.1 Decisões fixadas antes do design

| Decisão | Valor |
|---|---|
| Acervo | Global da plataforma (sem `school_id` no piloto) |
| Banco | PostgreSQL + pgvector; SQLite nos testes via abstração |
| Operação | API + scripts; sem interface no piloto |
| Entrada | Texto livre, resolvido **obrigatoriamente** para `CatalogNode` |
| Texto literal de fonte comercial | Interno e restrito |
| Modo de recuperação | `STRICT_CORPUS` |

### 1.2 O que o sistema não faz

- Não reproduz nem imita trechos substanciais de obra comercial.
- Não cria nó de currículo automaticamente (`TAXONOMY_GAP` explícito, como na PHASE 26).
- Não resolve divergência entre fontes silenciosamente.
- Não usa autoconfiança do LLM como medida de certeza.

---

## 2. Fronteira do subsistema — o Knowledge Pack é a interface canônica

**O Knowledge Pack é o único contrato público do Knowledge Engine.** Todo
consumidor do AGENTE IA EDU — gerador de conteúdo autoral, trilha, futuro
assistente de professor, qualquer coisa — consome o Pack e **nunca** os chunks,
embeddings, termos ou tabelas internas do corpus.

Motivos:

1. Chunk é uma decisão de recuperação, não um conceito de domínio. Mudar a
   janela de chunking, o modelo de embedding ou o algoritmo de fusão não pode
   quebrar consumidor nenhum.
2. A política de direitos vive na fronteira. Se um consumidor pudesse ler
   `knowledge_chunks.raw_text`, a trava de conteúdo comercial passaria a
   depender de cada consumidor se comportar.
3. O Pack carrega proveniência e força de evidência; um chunk solto não carrega
   nem uma coisa nem outra.

**Público:** `KnowledgePack` (contrato versionado), os endpoints
`/api/v1/knowledge-engine/packs*`, e o serviço `pack_builder`.

**Interno:** `knowledge_sources`, `knowledge_documents`, `knowledge_chunks`,
`knowledge_chunk_embeddings`, `knowledge_chunk_terms`,
`knowledge_embedding_spaces`, e todos os módulos de
`services/knowledge_engine/` exceto `pack_builder`.

**Exceção controlada:** `POST /retrieval/preview` expõe recuperação crua, é
restrito a `platform_admin` e existe só para depuração. Não é interface de
consumo e nunca devolve `raw_text` de fonte comercial.

**Garantia executável:** teste de arquitetura
(`test_knowledge_engine_boundary.py`) que varre a árvore de importações e falha
se qualquer módulo fora de `services/knowledge_engine/` importar os modelos
`KnowledgeChunk`, `KnowledgeChunkEmbedding` ou `KnowledgeChunkTerm`. É o mesmo
recurso que `FORBIDDEN_COLUMN_FRAGMENTS` já usa em
`tests/test_r0_identity_models.py`.

---

## 3. Componentes reutilizados

Auditado por leitura direta do repositório em 2026-09-30.

| Componente existente | Uso no CÉREBRO | Estado |
|---|---|---|
| `providers/contracts.py :: EmbeddingProvider` | Contrato de embedding | **Já existe, hoje ocioso** — só `FakeProvider` implementa |
| `providers/models.py :: EmbeddingArtifact` | `canonical_text`, `text_hash`, `vector`, `dimensions`, `provider`, `model` | Já existe, encaixe exato |
| `providers/contracts.py :: DocumentPageTranscriptionProvider` | OCR por visão de PDF sem camada de texto | Já existe (PHASE 26) |
| `providers/contracts.py :: TextGenerationProvider` | Destilação do Pack | Já existe |
| `providers/router.py`, `factory.py` | Neutralidade de vendor | Já existe; nenhum SDK é importado fora de `providers/` |
| `services/authorial_material_parser.py` | Extração determinística de prosa/exercício com página | Já existe, sem LLM |
| `services/ingestion_parser.py :: DocxParser` | `.docx` | Já existe |
| `services/material_storage.py :: MaterialStorage` | Armazenamento endereçado por hash | Já existe |
| `services/authorial_curriculum_matcher.py` | Casamento determinístico com currículo | Já existe |
| `db/models/catalog.py :: CatalogNode` | Âncora de currículo (v2) | Já existe |
| `db/models/pedagogical.py :: Taxonomy/TaxonomyNode` | BNCC (`Taxonomy.code == "bncc"`) | Já existe |
| `db/models/catalog.py :: CatalogNodePrerequisite` | Pré-requisitos no Pack | Já existe |
| `db/models/catalog.py :: ContentQuestionLink` | Questões já classificadas no Pack | Já existe |
| `db/types.py :: JSONBCompatible` | Molde do `VectorCompatible` | Já existe |
| `essay_engine_contract/vN` | Molde do contrato versionado imutável | Já existe |
| `classification_prompts/` | Molde de prompt versionado | Já existe |
| `docker-compose.yml` | Imagem `pgvector/pgvector:pg16` | **Já existe, nunca ligado** |
| `docker/postgres/init/01-init.sh` | `CREATE EXTENSION IF NOT EXISTS vector` | Já existe; só roda em volume novo |

**Colisão de nome:** `services/knowledge.py :: KnowledgeService` já existe (camada
de consulta relacional do catálogo). O subsistema novo usa o pacote
`services/knowledge_engine/` e **nenhuma classe chamada `KnowledgeService`**.

---

## 4. Por que corpus paralelo, e não extensão da PHASE 26

O pipeline PHASE 26 (`authorial_material_ingestion.py`) tem como estado
terminal `publish()` → `TheoryMaterial` → `TheoryMaterialVersion` →
`MaterialAssignment` → aluno.

Uma fonte `COMMERCIAL_REFERENCE` **não pode nunca** alcançar esse estado.
Mantendo os corpora separados, isso é impossível por construção. Estendendo a
PHASE 26 com uma coluna de direitos, a proteção viraria uma condição dentro de
`publish()` — e um bug nessa condição vaza obra de terceiro como material
didático distribuído a aluno.

Razão secundária: `IngestionSection` é uma unidade de **leitura** (capítulo, com
`page_start`/`page_end`), dimensionada para o Material Player. Chunk é uma
unidade de **recuperação**. Forçar uma na outra degrada as duas.

Custo da separação: oito tabelas novas. Nenhuma tabela existente é alterada.

---

## 5. Modelo de dados

Oito tabelas novas, todas aditivas, todas com `ondelete="RESTRICT"` conforme a
convenção do projeto. Sem `school_id` no piloto (acervo global); a coluna entra
depois como aditiva nullable, seguindo o padrão de `TheoryMaterial`.

### 5.1 `knowledge_sources`

A obra cadastrada.

```
id                      uuid pk
title                   str(500)  not null
authors                 str(500)
publisher               str(255)
edition                 str(100)
publication_year        int
isbn                    str(20)
source_kind             str(40)   not null   -- TEXTBOOK | CURRICULUM_FRAMEWORK | OWN_MATERIAL | ARTICLE | OTHER
rights_class            str(30)   not null   -- CheckConstraint (ver 5.1.1)
authority_level         str(30)   not null   -- ver 5.1.2
source_quality_score    numeric(4,3)         -- reservado; não usado no MVP
license_reference       text
rights_notes            text
educational_resource_id uuid fk  educational_resources.id   nullable
status                  str(20)   not null   -- REGISTERED|EXTRACTING|EXTRACTED|CHUNKED|EMBEDDED|READY|FAILED|ARCHIVED
created_by_external_identity str(255)
metadata                jsonb
created_at, updated_at  timestamptz
```

#### 5.1.1 `rights_class` — o que podemos fazer com a fonte

`CheckConstraint` (é a coluna de política; aqui vale travar):

```
COMMERCIAL_REFERENCE | LICENSED | OWN | PUBLIC_DOMAIN | OFFICIAL_PUBLIC
```

A trava estrutural central:

```sql
CHECK (rights_class <> 'COMMERCIAL_REFERENCE' OR educational_resource_id IS NULL)
```

Fonte comercial não existe como `EducationalResource` → não pode ser alvo de
`ContentResourceLink` → não pode virar `TheoryMaterial` → não pode virar
`MaterialAssignment`. **A separação pedida é topológica, não um `if`.**

#### 5.1.2 `authority_level` — quanto confiamos na fonte

Eixo **ortogonal** a `rights_class`. String livre (convenção do projeto para
vocabulários que ainda vão crescer, como `section_type` e `block_type`):

```
OFFICIAL | ACADEMIC | COMMERCIAL_TEXTBOOK | OWN | OTHER
```

Exemplos de por que os dois eixos são independentes: a BNCC é
`rights_class=OFFICIAL_PUBLIC` e `authority_level=OFFICIAL`; um livro didático
comercial é `COMMERCIAL_REFERENCE` / `COMMERCIAL_TEXTBOOK`; uma apostila própria
é `OWN` / `OWN`, e pode perfeitamente ter autoridade menor que o livro comercial
cujo texto não podemos reproduzir.

`authority_level` é registrado, congelado em `knowledge_pack_evidences` e
exposto no Pack. **No MVP não altera o ranking**: a política v1 declara
`authority_weights` com todos os pesos em `1.0`. Uma política v2 muda os pesos
sem tocar em código.

### 5.2 `knowledge_documents`

Uma fonte tem N arquivos (volumes, capítulos avulsos).

```
id, source_id fk
filename            str(500)  not null
storage_uri         str(2048) not null
document_hash       str(128)  not null     -- sha256
file_size_bytes     int
mime_type           str(100)
page_count          int
page_offset         int  not null default 0
extraction_method   str(30)               -- PDF_TEXT_LAYER | VISION_OCR | DOCX | MARKDOWN
extraction_status   str(20)  not null
extraction_error    text
metadata            jsonb
created_at, updated_at

UNIQUE (source_id, document_hash)
```

`page_offset` é o que faz a rastreabilidade apontar para a **página impressa da
obra**, e não para a página do PDF recortado. Sem ele, um recorte de capítulo
produz citação errada.

### 5.3 `knowledge_chunks`

Unidade de recuperação.

```
id
source_id    fk  (denormalizado: filtro de direitos sem join)
document_id  fk
ordinal      int not null
chunk_type   str(30) not null   -- PROSE|DEFINITION|WORKED_EXAMPLE|TABLE|FORMULA|EXERCISE|SUMMARY|CURRICULUM_ITEM
heading_path jsonb              -- ["Cap. 10 — Estequiometria", "10.2 Reagente limitante"]
page_start   int
page_end     int                -- página impressa, já com page_offset aplicado
raw_text     text not null      -- literal RESTRITO (ver seção 9)
text_hash    str(64) not null   -- sha256 do canonical_text
char_count   int
token_estimate int
content_node_id            uuid fk catalog_nodes.id  nullable  (ondelete SET NULL)
curriculum_match_confidence numeric(4,3)
curriculum_match_source     str(30)   -- DETERMINISTIC | MANUAL | INHERITED
bncc_node_codes             jsonb     -- ["EM13CNT301", ...]
metadata     jsonb
created_at

INDEX (source_id) | (document_id, ordinal) | (content_node_id) | (text_hash)
UNIQUE (document_id, ordinal)
```

### 5.4 `knowledge_embedding_spaces` — a dimensão é dado, não schema

Requisito: **1536 dimensões não pode ser acoplamento permanente.** A dimensão
pertence à geração/modelo de embedding, não ao DDL.

```
id            uuid pk
provider      str(50)  not null
model         str(100) not null
dimensions    int      not null
distance_metric str(20) not null default 'cosine'
status        str(20)  not null   -- ACTIVE | BACKFILLING | RETIRED
notes         text
created_at, activated_at, retired_at

UNIQUE (provider, model, dimensions)
partial unique INDEX ON (status) WHERE status = 'ACTIVE'   -- no máximo um espaço ativo
```

Consequências:

- `knowledge_chunk_embeddings.embedding` é declarado **sem dimensão fixa**:
  `VectorCompatible()` emite `vector` (não-dimensionado) no Postgres e `JSON`
  no SQLite. **Nenhum número aparece no schema.**
- A dimensão real é validada pelo serviço contra
  `knowledge_embedding_spaces.dimensions` na escrita, não pelo banco.
- O índice ANN, que pgvector exige dimensionado, é criado **por espaço** como
  índice parcial com cast explícito:

  ```sql
  CREATE INDEX ix_kce_hnsw_<space>
    ON knowledge_chunk_embeddings
    USING hnsw ((embedding::vector(1536)) vector_cosine_ops)
    WHERE space_id = '<uuid>';
  ```

- Introduzir um espaço de 1024 dimensões depois = inserir uma linha + criar um
  índice parcial. **Zero migração de schema, zero migração de dados, nenhuma
  releitura do corpus.**

Trade-off aceito: um `vector` não-dimensionado não impede que o banco aceite um
vetor de tamanho errado; a validação vive no serviço e é coberta por teste. A
alternativa (dimensão no DDL) transformaria uma troca de modelo em migração de
tabela, que é exatamente o acoplamento que o requisito 3 proíbe.

### 5.5 `knowledge_chunk_embeddings`

Separada do chunk **de propósito**: trocar de modelo é `INSERT`, nunca `UPDATE`.

```
id
chunk_id   fk
space_id   fk knowledge_embedding_spaces.id
embedding  VectorCompatible()   -- sem dimensão
text_hash  str(64) not null
generated_at timestamptz
is_active  bool not null default false

UNIQUE (chunk_id, space_id, text_hash)
partial INDEX WHERE is_active    -- padrão já usado em MaterialAssignment
```

`knowledge_embedding_spaces.status` e `knowledge_chunk_embeddings.is_active`
não são redundantes e não podem divergir na leitura: **a busca filtra
exclusivamente por `is_active`**, que é a verdade por linha. O `status` do
espaço descreve o ciclo de vida (`BACKFILLING` enquanto as linhas estão sendo
escritas com `is_active=false`; `ACTIVE` depois da virada atômica). Um espaço
`ACTIVE` cujas linhas ainda não foram viradas simplesmente não aparece na busca
— falha fechada, nunca meio-ativa.

### 5.6 `knowledge_chunk_terms`

Índice invertido próprio — a perna lexical, idêntica em SQLite e Postgres.

```
chunk_id  fk
term      str(80)
term_frequency int not null

PRIMARY KEY (chunk_id, term)
INDEX (term)
```

O document frequency do BM25 sai de uma CTE no momento da busca. Não há tabela
de estatísticas materializada: para os termos de uma consulta (5–10), o
`GROUP BY` com índice em `term` é barato, e uma tabela materializada seria mais
uma coisa para sair de sincronia.

### 5.7 `knowledge_packs`

```
id
query_text             text not null
content_node_id        uuid fk catalog_nodes.id   NOT NULL
pack_contract_version  str(10) not null    -- "v1"
retrieval_policy_version str(10) not null  -- "v1"
retrieval_mode         str(20) not null    -- STRICT_CORPUS (ver 8.4)
status                 str(20) not null    -- BUILDING | READY | FAILED
payload                jsonb               -- o Pack, validado contra o contrato
retrieval_params       jsonb               -- snapshot INTEGRAL da política aplicada
embedding_space_id     uuid fk
distillation_provider  str(50)
distillation_model     str(100)
input_tokens, output_tokens  int           -- nullable, nunca 0 fabricado
failure_reason         text
built_by_external_identity str(255)
built_at, created_at

INDEX (content_node_id) | (created_at)
```

`retrieval_params` guarda o snapshot integral da política, não só sua versão:
um Pack de hoje continua explicável depois que a política v2 existir.

`input_tokens`/`output_tokens` seguem a convenção já documentada em
`TextGenerationResult`: `None` quando o provider não reporta, **nunca um 0
fabricado**.

### 5.8 `knowledge_pack_evidences`

```
id
pack_id    fk
chunk_id   fk
source_id  (denormalizado)
rank            int
retrieval_score numeric
vector_score    numeric
lexical_score   numeric
rights_class    str(30)   -- CONGELADO no momento do uso
authority_level str(30)   -- CONGELADO no momento do uso
metadata        jsonb

UNIQUE (pack_id, chunk_id)
```

Existe em tabela, e não só dentro do JSON, para responder sem parsear payload:

- *quais Packs citaram esta obra?*
- *se eu arquivar esta fonte, quais Packs ficam sem lastro?*
- *este Pack usou obra comercial?* — respondível anos depois, mesmo que a
  classificação da fonte mude, porque está congelada aqui.

---

## 6. Chunking

Estrutural primeiro, tamanho depois. Determinístico, **sem LLM**, reprodutível:
mesma entrada produz exatamente os mesmos `text_hash`.

### 6.1 Prosa (`ProseChunker`)

`parse_authorial_document()` já devolve `ParsedSection` com `page_start`/
`page_end` e já converte subtítulo numerado em marcador `## `. O chunker consome
esse resultado sem modificar o parser:

1. Acumula `heading_path` a partir dos marcadores existentes.
2. Quebra o corpo em parágrafos.
3. Agrupa em janelas de **~700 tokens** (≈2800 caracteres) com **overlap de um
   parágrafo**, nunca cruzando fronteira de seção.
4. Nunca quebra no meio de tabela markdown, bloco de fórmula ou item de
   exercício numerado. Exercício vira chunk próprio, um por item — o parser já
   os detecta (`_EXERCISE_ITEM`).
5. Página derivada da posição de caractere, reusando a técnica de
   `parse_authorial_pdf._page_of`, somada ao `page_offset` do documento.
6. `canonical_text = "\n".join(heading_path) + "\n\n" + corpo`, whitespace
   colapsado. `text_hash = sha256(canonical_text)`.

Por que 700 tokens: é a faixa em que um chunk de livro didático ainda carrega um
raciocínio completo (definição + exemplo resolvido) sem diluir o vetor em
múltiplos assuntos.

### 6.2 BNCC (`CurriculumFrameworkChunker`)

A BNCC não é prosa; é uma lista de habilidades codificadas. Chunker dedicado:

- um chunk por habilidade (`EM13CNT301`, …), `chunk_type = CURRICULUM_ITEM`;
- casa diretamente com o `TaxonomyNode` de `Taxonomy(code="bncc")` **que já
  existe no banco** — nenhuma taxonomia nova é criada;
- `heading_path` = competência → habilidade.

### 6.3 Casamento com currículo

Reusa `authorial_curriculum_matcher` (determinístico). Não casou →
`content_node_id = NULL` e o chunk é marcado como não-mapeado. **Nunca cria nó**,
exatamente como a PHASE 26 faz com `TAXONOMY_GAP`.

---

## 7. Embeddings

- Adapter real: `providers/adapters/openai.py` ganha `embed()`. O contrato e o
  `EmbeddingArtifact` já existem — é encaixe, não construção.
- Piloto: `text-embedding-3-small`, 1536 dimensões, registrado como o primeiro
  `knowledge_embedding_space` ativo.
- Custo: ~1M tokens de corpus ≈ **US$ 0,02**. Custo de embedding não é
  restrição neste projeto; reembedar o acervo inteiro custa o mesmo.
- Lotes de 128 chunks por chamada (`EmbeddingRequest.texts` já é tupla).
- **Idempotência:** consulta por `(text_hash, space_id)` antes de chamar o
  provider. Re-ingerir o mesmo arquivo custa zero.
- **Troca de modelo:** cria o novo espaço com `status=BACKFILLING`, insere as
  linhas com `is_active=false`, e só então vira `is_active` e `status=ACTIVE`
  num commit. Chunks, termos, vínculos de currículo e direitos sobrevivem
  intactos. É a doutrina de inteligência no sistema aplicada ao corpus.
- Na suíte: `FakeProvider` já gera vetor determinístico por hash. **Nenhuma
  chamada de rede nos testes.**

---

## 8. Recuperação híbrida

### 8.1 As duas pernas

**Vetorial** (Postgres): `ORDER BY embedding <=> :q LIMIT k_vector`, filtrando
`is_active` e o espaço ativo.

**Lexical** (idêntica em SQLite e Postgres): BM25 sobre
`knowledge_chunk_terms`, DF por CTE, `LIMIT k_lexical`. Tokenização pt-BR:
minúsculas, remoção de acento, stopwords.

### 8.2 Fusão: Reciprocal Rank Fusion

`score = Σ 1/(rrf_k + rank_i)`, **não** soma ponderada de scores.

Distância de cosseno e BM25 têm escalas incomparáveis; RRF dispensa
normalização, permanece estável quando uma das pernas devolve pouco ou nada, e é
testável com rankings fabricados sem nenhum banco vetorial.

### 8.3 Reranking estrutural

Depois da fusão, ajustes determinísticos declarados na política versionada:

- boost se `chunk.content_node_id` é o nó resolvido ou descendente dele (usa
  `root_id`/`parent_id`, já indexados);
- boost por `chunk_type` (`DEFINITION`, `WORKED_EXAMPLE`);
- peso por `authority_level` — **todos 1.0 no MVP**, hook presente;
- **teto de `max_chunks_per_source` por fonte** no top-k final. É isto que torna
  "cruzar múltiplas fontes" estrutural em vez de sorte;
- se o top-k final tiver menos de `min_distinct_sources` fontes, o Pack nasce
  com `coverage_warning`.

### 8.4 `knowledge_retrieval_policy/v1.py` — toda constante mora aqui

Nenhum destes números aparece espalhado pelo código. Dataclass congelada,
versionada e imutável, no mesmo regime de `essay_engine_contract/vN`:

```python
@dataclass(frozen=True)
class RetrievalPolicyV1:
    version: str = "v1"
    retrieval_mode: str = "STRICT_CORPUS"
    k_vector: int = 60
    k_lexical: int = 60
    rrf_k: int = 60
    final_k: int = 20
    max_chunks_per_source: int = 4
    min_distinct_sources: int = 2
    node_match_boost: float = 0.25
    descendant_node_boost: float = 0.10
    chunk_type_boosts: Mapping[str, float] = MappingProxyType({
        "DEFINITION": 0.15,
        "WORKED_EXAMPLE": 0.10,
    })
    # Hook presente, sem efeito no MVP: todos os pesos iguais.
    authority_weights: Mapping[str, float] = MappingProxyType({
        "OFFICIAL": 1.0, "ACADEMIC": 1.0, "COMMERCIAL_TEXTBOOK": 1.0,
        "OWN": 1.0, "OTHER": 1.0,
    })
    evidence_strength_thresholds: Mapping[str, int] = MappingProxyType({
        "corroborated_min_sources": 2,
        "strongly_corroborated_min_sources": 3,
    })
```

O snapshot integral vai para `knowledge_packs.retrieval_params`, tornando todo
Pack reproduzível e explicável.

### 8.5 `retrieval_mode`

| Modo | Semântica | Piloto |
|---|---|---|
| `STRICT_CORPUS` | O Pack contém **exclusivamente** o que o corpus sustenta. Toda afirmação exige ≥1 evidência. Conhecimento geral do modelo é proibido e o validador o rejeita. | **ativo** |
| `CORPUS_FIRST` | Reservado. O modelo poderá acrescentar conhecimento geral, mas tais afirmações carregam `evidence_ids: []` e `origin: MODEL_GENERAL`, ficando estruturalmente segregadas das que têm lastro. | futuro |

O campo existe no contrato, na política e em `knowledge_packs` desde a v1. O
piloto fixa `STRICT_CORPUS`, e o validador da v1 rejeita qualquer valor
diferente — o modo futuro exigirá contrato v2, deliberadamente.

### 8.6 SQLite

**Não haverá caminho vetorial paralelo.** O `HybridRetriever` recebe as duas
pernas por injeção. Em SQLite os testes exercitam fusão, reranking, diversidade
por fonte, força de evidência e a perna lexical completa, com rankings
fabricados. A perna vetorial real é exercida apenas em
`test_knowledge_retrieval_postgresql.py`. Uma implementação de cada perna, zero
`if dialect ==` na lógica de busca.

---

## 9. Política de direitos

### 9.1 Os dois eixos

| Eixo | Pergunta | Coluna |
|---|---|---|
| Direitos | O que podemos **fazer** com esta fonte? | `rights_class` (CheckConstraint) |
| Autoridade | Quanto **confiamos** nesta fonte? | `authority_level` (string livre) |

São independentes e nunca devem ser colapsados num único campo.

### 9.2 Defesa em quatro camadas

1. **Banco** — `CHECK (rights_class <> 'COMMERCIAL_REFERENCE' OR educational_resource_id IS NULL)`.
   Obra comercial não pode existir no catálogo de recursos, logo não alcança
   `TheoryMaterial` nem `MaterialAssignment`.
2. **Schema de resposta** — os schemas Pydantic de chunk comercial **não têm** o
   campo `raw_text`. Não é filtro em tempo de execução; é um schema diferente.
3. **Validador do Pack** — rejeita `excerpt` não-nulo em evidência de fonte
   comercial.
4. **Teste de arquitetura** — nenhum schema de resposta pode expor `raw_text`,
   no espírito do `FORBIDDEN_COLUMN_FRAGMENTS` já existente.

### 9.3 Uso permitido do literal restrito

O `raw_text` de fonte `COMMERCIAL_REFERENCE` é **conteúdo interno restrito**.
Pode ser lido, em processo, pelo indexador lexical, pelo embedder e pelo
destilador, para análise e extração de evidência. Não pode ser exposto ao
usuário, devolvido por endpoint, nem usado para reproduzir, reconstruir ou
imitar trecho substancial da obra.

Para fonte não comercial, `excerpt` é permitido, limitado a 300 caracteres e
marcado `quotable: true`.

---

## 10. Knowledge Pack

### 10.1 Contrato

`knowledge_pack_contract/v1.py`, no molde imutável de `essay_engine_contract/`:
uma mudança de forma é um módulo `v2` novo, nunca uma edição da v1.

```
contract_version   "v1"
retrieval_mode     "STRICT_CORPUS"
topic              { query_text, content_node{id,code,name,path}, bncc[] }
coverage           { sources_consulted, sources_represented, warnings[] }

concepts[]         { id, label, statement, evidence_ids[], evidence_strength, supporting_sources }
definitions[]      { id, term, statement, evidence_ids[], evidence_strength, supporting_sources }
relations[]        { id, from, to, kind, statement, evidence_ids[], evidence_strength, supporting_sources }
procedures[]       { id, label, steps[], evidence_ids[], evidence_strength, supporting_sources }
worked_examples[]  { id, summary, evidence_ids[] }        ← descrição, nunca cópia
misconceptions[]   { id, statement, evidence_ids[], evidence_strength, supporting_sources }

prerequisites[]    { content_node_code, name }            ← de CatalogNodePrerequisite
linked_questions[] { question_version_id, summary }       ← de ContentQuestionLink já classificadas

divergences[]      { id, divergence_kind, topic_label, readings[{statement, evidence_ids[]}] }

evidence[]         { id, source{title,authors,edition,year,isbn},
                     pages, heading_path[],
                     rights_class, authority_level,
                     quotable, excerpt }
```

### 10.2 Força de evidência determinística

Requisito 2: a certeza é **calculada pelo sistema**, nunca reportada pelo
modelo.

O LLM tem uma única responsabilidade quanto a isto: **anexar `evidence_ids`**.
Ele não emite nota de confiança. Se tentar preencher `evidence_strength` ou
`supporting_sources`, o validador **sobrescreve** os campos com o valor
calculado — não falha, apenas ignora, porque a autoconfiança do modelo não é
sinal.

Para cada afirmação, o sistema calcula a partir das evidências efetivamente
referenciadas:

```
supporting_chunks  = |evidence_ids|
supporting_sources = |{ source_id de cada evidence_id }|
```

E deriva, por função pura e versionada em `knowledge_retrieval_policy/v1.py`:

| `evidence_strength` | Condição |
|---|---|
| `CONTESTED` | a afirmação participa de alguma entrada de `divergences` |
| `STRONGLY_CORROBORATED` | ≥3 fontes distintas **e** ≥1 fonte com `authority_level ≠ COMMERCIAL_TEXTBOOK` |
| `CORROBORATED` | ≥2 fontes distintas |
| `SINGLE_SOURCE` | exatamente 1 fonte |

`CONTESTED` tem precedência sobre todas as demais: uma afirmação disputada não
vira "forte" por ter muitas fontes de um dos lados.

Os limiares são campos de `evidence_strength_thresholds` na política, não
literais no código, e portanto ajustáveis por uma política v2 sem tocar no
contrato.

A regra sobre `authority_level` na faixa `STRONGLY_CORROBORATED` é a **única**
influência de autoridade no MVP, e é uma regra de contagem, não de ranking —
coerente com "não sofisticar o ranking no MVP".

### 10.3 Divergências são preservadas, nunca resolvidas

Requisito 5. O CÉREBRO não escolhe vencedor.

- Quando duas fontes afirmam coisas incompatíveis sobre o mesmo rótulo/termo,
  **ambas as leituras** entram no Pack, cada uma com suas evidências, dentro de
  uma entrada de `divergences`.
- `divergence_kind` classifica o tipo: `DEFINITIONAL` (definições diferentes),
  `NOTATIONAL` (mesma ideia, símbolo/nomenclatura diferente), `PEDAGOGICAL`
  (ordem ou abordagem de ensino diferente), `SCOPE` (uma fonte é mais restrita),
  `FACTUAL` (uma delas está factualmente errada — registrado, não arbitrado).
- Toda afirmação que participa de uma divergência recebe
  `evidence_strength = CONTESTED`.
- **Validação:** o validador rejeita um Pack que contenha duas afirmações com o
  mesmo `term`/`label` e `statement` incompatível **sem** a entrada
  correspondente em `divergences`. Isso torna o silenciamento de divergência um
  erro detectável, não uma omissão invisível.
- `divergences` só é aceito com ≥2 `source_id` distintas envolvidas.

### 10.4 Regras invioláveis, conferidas por validador determinístico

Nenhuma delas depende do prompt:

1. Toda afirmação em `concepts`, `definitions`, `relations`, `procedures`,
   `misconceptions` tem ≥1 `evidence_id` válido. Afirmação órfã **rejeita o Pack
   inteiro** (em `STRICT_CORPUS`).
2. Todo `evidence_id` referenciado existe em `evidence[]`.
3. `rights_class = COMMERCIAL_REFERENCE` ⇒ `quotable = false` **e** `excerpt` é
   obrigatoriamente `null`.
4. Fonte não comercial: `excerpt` ≤ 300 caracteres, `quotable = true`.
5. `evidence_strength` e `supporting_sources` são sempre recalculados pelo
   sistema (10.2).
6. Divergência não declarada é erro (10.3).
7. `retrieval_mode` diferente de `STRICT_CORPUS` é rejeitado pela v1.

### 10.5 Construção

```
recuperação híbrida
  → chunks numerados no prompt versionado (knowledge_pack_prompts/v1.py)
  → JSON de volta
  → validação determinística
       falhou → 1 retentativa, com o erro de validação anexado ao prompt
       falhou de novo → status=FAILED, failure_reason preenchido, nada READY
  → cálculo de evidence_strength
  → persistência de knowledge_packs + knowledge_pack_evidences
```

O modelo preenche um formulário que o sistema sabe conferir. Contrato,
validador, política de recuperação, força de evidência, índice lexical, corpus e
vínculos de currículo sobrevivem intactos a uma troca de provider ou de modelo.

---

## 11. Fluxos

### 11.1 Ingestão

Reentrante e idempotente etapa a etapa:

```
registrar fonte (direitos + autoridade declarados)
  → MaterialStorage (endereçado por hash)
  → detectar camada de texto
  → extrair (pypdf / docx / md)  OU  OCR por visão (DocumentPageTranscriptionProvider)
  → parse determinístico (authorial_material_parser)
  → chunking (ProseChunker | CurriculumFrameworkChunker)
  → casar currículo (determinístico; não casou = não-mapeado, nunca cria nó)
  → indexar termos
  → embedar em lote (idempotente por text_hash + space_id)
  → READY
```

Falha no embedding reprocessa só o embedding; nada anterior é refeito.

### 11.2 Consulta

```
texto livre
  → resolver CatalogNode  (não resolveu → 409 TAXONOMY_GAP, falha alto)
  → embedar a consulta
  → perna vetorial + perna lexical
  → RRF
  → reranking estrutural + teto por fonte
  → contexto numerado
  → destilar
  → validar contra o contrato v1
  → calcular força de evidência
  → persistir e devolver o Pack
```

---

## 12. Endpoints

Prefixo `/api/v1/knowledge-engine`, todos `Depends(require_platform_admin)`.

| Método | Rota | Função |
|---|---|---|
| POST | `/sources` | cadastra fonte, direitos e autoridade |
| GET | `/sources` | lista com status e cobertura |
| GET | `/sources/{id}` | detalhe, contagem de chunks, cobertura de currículo |
| POST | `/sources/{id}/documents` | registra arquivo (multipart) |
| POST | `/sources/{id}/ingest` | dispara o pipeline (idempotente) |
| POST | `/sources/{id}/reembed` | reembeda no espaço indicado |
| DELETE | `/sources/{id}` | arquiva (nunca deleta; `RESTRICT` em tudo) |
| POST | `/packs` | `{query_text, options}` → Pack |
| GET | `/packs/{id}` | recupera Pack |
| GET | `/packs?content_node_code=` | lista por nó |
| POST | `/retrieval/preview` | só recuperação, sem destilar (depuração sem custo) |

Scripts de operação: `scripts/knowledge_ingest_source.py`,
`scripts/knowledge_build_pack.py`, `scripts/knowledge_corpus_report.py`.

---

## 13. Serviços

`src/agente_ia_edu/services/knowledge_engine/` como pacote, para não repetir o
problema dos arquivos de 45 KB já existentes:

| Módulo | Responsabilidade |
|---|---|
| `sources.py` | registro, direitos, autoridade, ciclo de vida |
| `extraction.py` | orquestra parser existente + OCR por visão |
| `chunking.py` | `ProseChunker`, `CurriculumFrameworkChunker` |
| `lexical_index.py` | tokenização pt-BR, stopwords, índice invertido, BM25 |
| `embedding_index.py` | espaços, lotes, idempotência, troca de modelo |
| `retrieval.py` | duas pernas, RRF, reranking, diversidade |
| `pack_builder.py` | destilação, validação, força de evidência, persistência |
| `rights.py` | policy pura, sem I/O |

Módulos-contrato novos, todos versionados e imutáveis:
`knowledge_pack_contract/v1.py`, `knowledge_pack_prompts/v1.py`,
`knowledge_retrieval_policy/v1.py`.

---

## 14. Migrações

| Migração | Conteúdo |
|---|---|
| `057_knowledge_engine_corpus.py` | `knowledge_sources`, `knowledge_documents`, `knowledge_chunks`, `knowledge_chunk_terms` |
| `058_knowledge_engine_embeddings.py` | `CREATE EXTENSION IF NOT EXISTS vector` (só PG), `knowledge_embedding_spaces`, `knowledge_chunk_embeddings`, espaço inicial semeado com **UUID determinístico literal na migração** (para que o índice HNSW parcial possa referenciá-lo e a migração seja reproduzível), índice HNSW parcial condicional ao dialeto |
| `059_knowledge_packs.py` | `knowledge_packs`, `knowledge_pack_evidences` |

Todas puramente aditivas; nenhuma tabela existente é tocada.

**Atenção operacional:** o banco de desenvolvimento atual **não tem a extensão
instalada**. `pg_available_extensions` reporta `vector 0.8.6` disponível com
`installed_version` vazio, porque o volume é anterior ao
`docker/postgres/init/01-init.sh`. A migração 058 precisa criar a extensão, e o
usuário do banco precisa de permissão para `CREATE EXTENSION`.

---

## 15. Testes

TDD, RED confirmado antes de GREEN, conforme a disciplina do projeto.

| Arquivo | Cobre |
|---|---|
| `test_knowledge_engine_boundary.py` | nenhum módulo externo importa modelos internos (seção 2) |
| `test_knowledge_rights_policy.py` | CheckConstraint comercial (PG); nenhum schema expõe `raw_text` |
| `test_knowledge_chunking.py` | determinismo por hash; não quebra tabela/fórmula/exercício; página e `heading_path` |
| `test_knowledge_bncc_chunking.py` | um chunk por habilidade; casa com `TaxonomyNode` existente |
| `test_knowledge_lexical_index.py` | BM25 com fixture conhecida; acentos e stopwords pt-BR |
| `test_knowledge_embedding_spaces.py` | dimensão é dado; validação de tamanho; troca de espaço preserva corpus |
| `test_knowledge_embedding_index.py` | idempotência por `text_hash`; ativação atômica |
| `test_knowledge_retrieval_fusion.py` | RRF; `max_chunks_per_source`; boost por nó — SQLite, rankings fabricados |
| `test_knowledge_retrieval_policy.py` | nenhum literal fora da política; snapshot reproduz o resultado |
| `test_knowledge_retrieval_postgresql.py` | perna vetorial real com pgvector |
| `test_knowledge_evidence_strength.py` | as quatro faixas; precedência de `CONTESTED`; autoconfiança do LLM é descartada |
| `test_knowledge_divergences.py` | divergência não declarada é rejeitada; ambas as leituras preservadas |
| `test_knowledge_pack_contract.py` | claim órfã, excerpt comercial, id inexistente, `retrieval_mode` inválido |
| `test_knowledge_pack_builder.py` | retentativa com erro anexado; falha dupla → FAILED e nada READY |
| `test_knowledge_engine_http.py` | autorização; 409 em `TAXONOMY_GAP` |
| `test_vector_compatible_type.py` | `vector` não-dimensionado no PG, JSON no SQLite |

---

## 16. Riscos de acoplamento

1. **Colisão de nome `KnowledgeService`** — mitigada pelo pacote
   `knowledge_engine` e pela proibição de reusar o nome.
2. **`MissingGreenlet`** — a armadilha nº 1 documentada do projeto. A ingestão
   comita muitas vezes; capturar escalares antes de cada commit é regra
   obrigatória no plano de implementação.
3. **pgvector não instalado no banco atual** — risco operacional confirmado, não
   hipotético (seção 14).
4. **Suíte mais lenta e mais frágil** — os testes vetoriais vão para
   `*_postgresql`, exatamente a família que a documentação da plataforma aponta
   como fonte recorrente de falha transitória por contenção na porta 5433.
5. **Currículo de Química possivelmente incompleto** — se não houver nó
   correspondente, o piloto trava em `TAXONOMY_GAP` **por design**. Verificado na
   Fase 0.
6. **Custo e prazo do OCR por visão** — se os PDFs forem imagem, são milhares de
   páginas de chamada de visão: ordem de dezenas de dólares e várias horas. É o
   único custo material do projeto. Medido na Fase 0.
7. **`ANTHROPIC_API_KEY` nunca configurada** em nenhum ambiente — bloqueia hoje o
   caminho de OCR.
8. **Vetor não-dimensionado no DDL** — o banco não rejeita vetor de tamanho
   errado; a validação vive no serviço, coberta por
   `test_knowledge_embedding_spaces.py`. Trade-off deliberado, aceito em troca de
   desacoplar a dimensão (seção 5.4).

---

## 17. Fases de implementação

| Fase | Entrega | Custo de LLM |
|---|---|---|
| **0** | Sonda + **matriz de cobertura** (seção 17.1) | nenhum |
| 1 | `VectorCompatible` + migração 057 + modelos + teste de tipo | nenhum |
| 2 | Registro de fontes, direitos, autoridade, CheckConstraints, storage, `POST /sources` | nenhum |
| 3 | Extração + `ProseChunker` | nenhum |
| 4 | `CurriculumFrameworkChunker` (BNCC) + casamento com `TaxonomyNode`/`CatalogNode` | nenhum |
| 5 | Índice lexical + BM25 | nenhum |
| 6 | Adapter de embedding real + `knowledge_embedding_spaces` + migração 058 | ~US$ 0,02 |
| 7 | Recuperação híbrida + política v1 + `POST /retrieval/preview` | mínimo |
| 8 | Contrato v1 do Pack + validador + força de evidência + divergências | nenhum |
| 9 | `pack_builder` + destilação + migração 059 + `POST /packs` | sim |
| 10 | Scripts de operação + relatório de cobertura do corpus | nenhum |

Oito das onze fases não gastam token algum. Cada fase entrega algo verificável e
não quebra a anterior.

### 17.1 Fase 0 — entregável obrigatório

A Fase 0 é a primeira porque seu resultado muda o custo e o prazo de tudo que
vem depois. Ela **não escreve código de produção**. Produz duas matrizes:

**Matriz A — cobertura do currículo.** Para cada tópico do piloto, se existe
`CatalogNode` correspondente, com que código, em que caminho da árvore, e se há
`TaxonomyNode` BNCC associável. Tópicos obrigatórios:

- Estequiometria
- Reagente Limitante
- Soluções
- Diluição

**Matriz B — cobertura documental.** Para cada arquivo do piloto: quantas
páginas, se possui camada de texto extraível por `pypdf`, quantas páginas têm
texto útil, e portanto se o caminho é `PDF_TEXT_LAYER` ou `VISION_OCR`, com
estimativa de custo e tempo para os que exigirem OCR.

A Fase 0 também confirma o estado da extensão `vector` no banco de
desenvolvimento.

Ao final, os resultados são apresentados para revisão. A Fase 1 não começa antes
disso.
