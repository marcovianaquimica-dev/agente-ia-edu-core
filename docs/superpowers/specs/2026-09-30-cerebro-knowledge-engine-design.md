# CÉREBRO / Knowledge Engine — Design

Data: 2026-09-30
Status: aprovado (design); Fase 0 executada; Fase 1 não iniciada
Escopo: piloto com livros didáticos de Química + BNCC do Ensino Médio (CNT)

> **Revisão pós-Fase 0 (2026-09-30).** A Fase 0 foi executada e cinco decisões
> foram tomadas a partir dos seus resultados. Elas estão incorporadas ao corpo
> deste documento e registradas na **seção 18**, junto com as matrizes de
> cobertura. Leia a seção 18 antes de implementar.

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
| BNCC no piloto | Ensino Médio, Ciências da Natureza e suas Tecnologias |
| OCR por visão | **fora da primeira versão** (ver 18.4) |

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
  `VectorCompatible()` emite `vector` (não-dimensionado) no Postgres e `TEXT`
  no SQLite. **Nenhum número aparece no schema.** (`TEXT`, e não `JSON` como
  esta seção dizia antes da Fase 1 — ver 19.1.)
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

**Arquivo do piloto:** `BNCC_EnsinoMedio_embaixa_site_110518.pdf` (154 páginas,
1,1 MB), lido sem problema por `pypdf`. O arquivo `BNCC_EI_EF_110518` é a versão
de Educação Infantil + Ensino Fundamental, **não cobre nenhum tópico do piloto**
e não é fonte do Cérebro de Química.

**Recorte:** área **Ciências da Natureza e suas Tecnologias (CNT)**, páginas
113–122 do PDF.

#### 6.2.1 A BNCC não é texto para RAG — é estrutura normativa

A BNCC entra no sistema pela sua **hierarquia normativa**, não como prosa
recuperável:

```
área (CNT) → competência específica (1..3) → habilidade → código (EM13CNT###)
```

Extrator dedicado `BnccFrameworkExtractor`, determinístico, **verificado na
Fase 0 contra o arquivo real**:

| Elemento | Regra de extração | Resultado verificado |
|---|---|---|
| Área | cabeçalho `5.3.1. CIÊNCIAS DA NATUREZA…` | p.116 |
| Competência específica | `COMPETÊNCIA ESPECÍFICA (\d)` + primeira frase seguinte | **3/3** (p.116, 118, 120) |
| Habilidade | `\((EM13CNT\d{3})\)\s*<enunciado>` | **23/23** (p.117, 119, 121) |
| Vínculo habilidade → competência | **primeiro dígito do código**: `EM13CNT`**1**`01` → CE1 | 6 + 7 + 10 = 23 ✓ |

O vínculo habilidade→competência é derivável do próprio código, e confirmado
independentemente pela paginação. Não há heurística envolvida.

Passo obrigatório de normalização: o PDF hifeniza quebras de linha
(`pro- cessos`, `desen- volvimento` — 40 ocorrências na seção CNT). O extrator
desfaz a hifenização antes de persistir; do contrário o código do enunciado
entra corrompido no corpus.

#### 6.2.2 Persistência: `Taxonomy` / `TaxonomyNode`

A estrutura vira dado relacional consultável, não só embedding:

| BNCC | Modelo | `node_type` |
|---|---|---|
| A norma | `Taxonomy(code="bncc", version="EM-2018")` | — |
| Competência específica 1..3 | `TaxonomyNode` | `competency` |
| Habilidade `EM13CNT###` | `TaxonomyNode`, `parent_id` = a competência | `skill` |

`TaxonomyNode.node_type` já aceita `'competency'` e `'skill'` pelo
`CheckConstraint` existente, e `TaxonomyNode` já é auto-referente via
`parent_id`. **Nenhum modelo novo, nenhuma migração de taxonomia.**

Achado da Fase 0: `taxonomies` e `taxonomy_nodes` estão **vazias**.
`Taxonomy(code="bncc")` é consultado pelo código (`repositories/questions.py`,
`services/questions.py`) mas nunca foi populado. A semeadura é, portanto, uma
**mudança explícita de taxonomia**, com script próprio e revisão humana, feita
**fora** do Knowledge Engine — mesma disciplina da decisão sobre Reagente
Limitante (18.1).

Consequência para o chunker: cada chunk BNCC (`chunk_type = CURRICULUM_ITEM`,
um por habilidade) carrega uma FK direta para o `TaxonomyNode` da habilidade,
e não apenas um código solto. O `CurriculumFrameworkChunker` **nunca cria nó de
taxonomia**: não casou é `TAXONOMY_GAP`, como no currículo v2.

#### 6.2.3 A relação BNCC ↔ `CatalogNode` de Química é curada, nunca inferida

Testado na Fase 0 contra o texto real da seção CNT: **"estequiometria",
"reagente limitante" e "diluição" não aparecem uma única vez**. "Soluções"
aparece, mas no sentido de *"propor soluções"* — um falso positivo que
demonstra o problema.

As habilidades de CNT são enunciados de competência ("Analisar e representar as
transformações e conservações em sistemas que envolvam quantidade de
matéria…"), não rótulos de conteúdo. Casar `CatalogNode` com habilidade por
similaridade de texto produziria ruído com aparência de rigor.

Portanto: **a relação é curada explicitamente**, por decisão pedagógica humana,
e é uma mudança de currículo — não uma inferência do Knowledge Engine. O
mecanismo de persistência dessa curadoria está em aberto (18.6) e **não bloqueia
as Fases 1–3**: até que exista, `topic.bncc[]` no Pack vem vazio, o que é um
resultado honesto, não uma falha.

#### 6.2.4 Classificação da fonte

A BNCC é tratada conceitualmente como **fonte oficial curricular**, categoria
distinta de livro comercial:

| Campo | Valor |
|---|---|
| `source_kind` | `CURRICULUM_FRAMEWORK` |
| `rights_class` | `OFFICIAL_PUBLIC` |
| `authority_level` | `OFFICIAL` |
| `educational_resource_id` | permitido (não é fonte comercial) |
| `quotable` nas evidências | `true` |

Consequências práticas: o texto da BNCC **pode** ser citado literalmente no Pack
(dentro do limite de 300 caracteres), ao contrário do livro comercial; e, pela
regra de `STRONGLY_CORROBORATED` (10.2), a presença da BNCC entre as fontes de
uma afirmação é o que permite que ela alcance a faixa mais alta sem depender só
de livros comerciais.

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
gaps[]             { id, aspect, reason }                 ← ver 10.2.1

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

### 10.2.1 `gaps[]` — a lacuna é um resultado, não uma falha

Em `STRICT_CORPUS`, uma afirmação sem lastro é rejeitada (10.4). Isso impede o
modelo de inventar, mas sozinho produz um Pack **silenciosamente incompleto**:
quem lê não distingue "o corpus não trata disso" de "ninguém perguntou".

`gaps[]` fecha essa lacuna. O destilador é instruído a declarar, para o tópico
pedido, que aspectos ele **esperava encontrar e não encontrou** no contexto
recuperado:

```json
{ "id": "g1",
  "aspect": "Cálculo de rendimento percentual a partir do reagente limitante",
  "reason": "NOT_IN_CORPUS" }
```

`reason` é um vocabulário fechado: `NOT_IN_CORPUS` (nada recuperado trata do
aspecto), `INSUFFICIENT_DETAIL` (mencionado, sem substância para sustentar
afirmação), `OUT_OF_RETRIEVAL` (fora do top-k, detectável quando a perna lexical
acusou o termo mas o chunk não sobreviveu ao corte).

Regras:

- uma entrada de `gaps[]` **nunca** carrega `evidence_ids` — ela afirma ausência;
- declarar uma lacuna **não** é motivo para rejeitar o Pack. É o resultado
  correto quando o corpus não cobre o pedido;
- um Pack cujo `concepts`, `definitions`, `relations` e `procedures` estão todos
  vazios **e** `gaps[]` também está vazio é rejeitado: o destilador não pode
  devolver nada sem dizer por quê;
- `coverage.warnings` continua reportando problemas de *recuperação* (poucas
  fontes distintas); `gaps[]` reporta problemas de *conteúdo*. São eixos
  diferentes e ambos aparecem.

Isto é o que a decisão 5 pede: diante de informação necessária e não sustentada,
o Pack **sinaliza a lacuna** em vez de completar com conhecimento do modelo.

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

### 11.3 Extração: fallback determinístico de leitura de PDF

A Fase 0 encontrou um PDF (a BNCC) que `pypdf` **não consegue abrir**
(`PdfReadError: Cannot find Root object in pdf`, mesmo com `strict=False`)
mas que `pymupdf` lê sem esforço — 600 páginas, texto limpo. O arquivo não está
corrompido; é o parser de container do `pypdf` que falha.

Hoje esse caso é indistinguível, no código, de um PDF sem camada de texto: as
duas situações terminam em exceção. São problemas diferentes e exigem respostas
diferentes:

| Situação | Hoje | Resposta correta |
|---|---|---|
| `pypdf` não abre o container | erro | tentar `pymupdf` |
| Páginas genuinamente sem texto (Usberco) | erro | OCR por visão |

**A menor alteração possível**, sem redesenhar o pipeline:

1. Extrair as oito linhas de leitura de `parse_authorial_pdf`
   (`authorial_material_parser.py:132-142`) para uma função pública no mesmo
   módulo:

   ```python
   @dataclass(frozen=True)
   class PdfTextLayer:
       page_texts: list[str]
       method: str            # PDF_TEXT_LAYER | PDF_TEXT_LAYER_PYMUPDF

   def read_pdf_page_texts(filepath: Path) -> PdfTextLayer: ...
   ```

2. O fallback dispara **apenas** nos dois caminhos de falha já codificados:
   `pypdf` levantou exceção, ou devolveu zero texto em todas as páginas. Se
   `pymupdf` também não achar texto, a mensagem passa a ser a de hoje
   (`"PDF has no extractable text layer; OCR review is required"`) — o caso
   Usberco continua indo para OCR, como deve.

3. `pymupdf` permanece **importado sob `try`**. Ausente, o comportamento é
   exatamente o de hoje, linha por linha.

4. `parse_authorial_pdf` ganha um parâmetro opcional
   `page_texts: list[str] | None = None`, seguindo o idioma `parsed_override`
   que `IngestionService.ingest_document` já usa. O CÉREBRO lê uma vez com
   `read_pdf_page_texts()` (para registrar o método) e repassa as páginas,
   sem ler o arquivo duas vezes.

**Garantia de não-regressão:** quando `pypdf` funciona — que é todo o corpus
atual da PHASE 26 — a saída é idêntica byte a byte, porque o caminho é o mesmo
código movido de lugar. A suíte PHASE 26 existente roda sem alteração e é o
portão de regressão.

**Risco registrado:** `pymupdf` e `pypdf` quebram linhas de maneira diferente,
então um documento lido pelo fallback pode seccionar de forma ligeiramente
diferente. Por isso o método fica gravado em
`knowledge_documents.extraction_method` como um valor **distinto**
(`PDF_TEXT_LAYER_PYMUPDF`), nunca confundido com o caminho padrão. A
proveniência de extração é sempre visível, jamais silenciosa.

`pymupdf` já está instalado no `.venv` e declarado no `pyproject.toml` como
extra `recovery`. Esta é a segunda utilização legítima do extra; o comentário
do `pyproject.toml` que diz *"NOT wired into the ingestion pipeline"* precisa
ser atualizado junto com a mudança.

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
6. ~~**Custo e prazo do OCR por visão**~~ — **rebaixado na Fase 0.** Medido em
   ~US$ 31 / 1,2 h para os 3 volumes Usberco, e retirado da primeira versão
   (18.4). Os documentos com camada de texto cobrem os quatro tópicos com 5 a 9
   fontes distintas cada. Deixa de ser caminho crítico.
7. ~~**`ANTHROPIC_API_KEY` nunca configurada**~~ — **irrelevante para a primeira
   versão** pela mesma razão. Volta a importar quando o OCR do Usberco for
   retomado.
8. **Vetor não-dimensionado no DDL** — o banco não rejeita vetor de tamanho
   errado; a validação vive no serviço, coberta por
   `test_knowledge_embedding_spaces.py`. Trade-off deliberado, aceito em troca de
   desacoplar a dimensão (seção 5.4).

---

## 17. Fases de implementação

| Fase | Entrega | Custo de LLM |
|---|---|---|
| **0** | Sonda + **matriz de cobertura** (seção 17.1) — **CONCLUÍDA**, ver seção 18 | nenhum |
| 1 | `VectorCompatible` + migração 057 + modelos + teste de tipo | nenhum |
| 2 | Registro de fontes, direitos, autoridade, CheckConstraints, storage, `POST /sources` | nenhum |
| 3 | Extração (incl. fallback PyMuPDF, 11.3) + `ProseChunker` | nenhum |
| 4 | Semeadura BNCC EM/CNT em `Taxonomy`/`TaxonomyNode` + `BnccFrameworkExtractor` + `CurriculumFrameworkChunker` | nenhum |
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

---

## 18. Resultados da Fase 0 e decisões

Fase 0 executada em 2026-09-30. Sonda read-only: nenhum código de produção
escrito, nenhum arquivo do acervo modificado, nenhuma DDL executada. Relatório
completo em `var/knowledge_engine_phase0_report.md`.

### 18.0 Matrizes

**Matriz A — currículo (`catalog_nodes`).** Química tem 1 DISCIPLINE, 4 AREA,
9 CONTENT, 2 SUBCONTENT.

| Tópico | Nó | `code` | Tipo |
|---|---|---|---|
| Estequiometria | existe | `CHEMISTRY-PHYSICAL-STOICHIOMETRY` | CONTENT |
| Reagente Limitante | **não existia** | — | `TAXONOMY_GAP` → decisão 18.1 |
| Soluções | existe | `CHEMISTRY-SOLUTIONS` | CONTENT |
| Diluição | existe | `CHEMISTRY-SOLUTIONS-DILUTION` | SUBCONTENT |

**Matriz B — camada de texto.** 2.286 páginas com texto extraível contra 1.116
que exigem OCR.

| Arquivo | Págs | Veredito |
|---|---:|---|
| `INQUI_P26_LM_001-544_DIVULGACAO_com-codigo.pdf` | 546 | `PDF_TEXT_LAYER` |
| `Moderna-Plus-Quimica-na-abordagem-do-cotidiano-1.pdf` | 548 | `PDF_TEXT_LAYER` |
| `Moderna-SuperAcao-Quimica-2.pdf` | 548 | `PDF_TEXT_LAYER` |
| `quimica 1.pdf` … `Quimica 6.pdf` | 644 | `PDF_TEXT_LAYER` |
| `BNCC_EnsinoMedio_embaixa_site_110518.pdf` | 154 | `PDF_TEXT_LAYER` |
| `Química Usberco Volume 1/2/3.pdf` | 1.116 | `VISION_OCR` |
| `BNCC_EI_EF_110518_versaofinal_site.pdf` | 600 | `pypdf` falha; fora do escopo |
| `usberco e salvador pdf.pdf` | — | arquivo vazio (0 bytes) |

**Matriz A × B — páginas por tópico** (só arquivos com camada de texto):
Estequiometria 38p / **5 fontes**; Reagente Limitante 19p / **5 fontes**;
Soluções 340p / **9 fontes**; Diluição 37p / **6 fontes**. Todos acima de
`min_distinct_sources = 2`, **sem depender do Usberco**.

**Ambiente.** Extensão `vector` disponível 0.8.6, não instalada; usuário do
banco é superuser, logo `CREATE EXTENSION` funcionará. `OPENAI_API_KEY`
definida; `ANTHROPIC_API_KEY` vazia. `pymupdf` instalado no `.venv`.

### 18.1 Decisão — Reagente Limitante

Criar o `SUBCONTENT` `CHEMISTRY-PHYSICAL-STOICHIOMETRY-LIMITING-REAGENT` como
filho de `CHEMISTRY-PHYSICAL-STOICHIOMETRY`.

Condições:

- é uma **alteração explícita da taxonomia curricular**, feita por script de
  currículo com revisão, **nunca** criação automática pelo Knowledge Engine;
- **não** expandir agora o resto da árvore de Estequiometria — só este gap;
- é **pré-requisito da Fase 7** (primeira fase em que um pedido resolve para um
  nó), não da Fase 1.

Isto não relaxa a regra de 1.2: o Knowledge Engine continua proibido de criar
nó. A criação acontece fora dele, por decisão humana registrada.

### 18.2 Decisão — BNCC

Mantida no piloto. Fonte: `BNCC_EnsinoMedio_embaixa_site_110518.pdf`,
área CNT, páginas 113–122. Tratada como **estrutura normativa**, não texto para
RAG — ver 6.2, verificado contra o arquivo real: 3/3 competências específicas e
23/23 habilidades extraídas deterministicamente.

Classificação: `source_kind=CURRICULUM_FRAMEWORK`,
`rights_class=OFFICIAL_PUBLIC`, `authority_level=OFFICIAL`.

### 18.3 Decisão — Extração

Fallback PyMuPDF quando a extração padrão falhar, pela menor alteração possível:
ver 11.3. Sem redesenho do pipeline; quando `pypdf` funciona a saída é idêntica.

### 18.4 Decisão — OCR

O OCR dos três volumes Usberco **sai da primeira versão**. Não gastar os ~US$ 31
nem bloquear a Fase 1. Os documentos com camada de texto bastam para validar o
piloto (Matriz A × B). Os Usberco permanecem **fontes candidatas** para uma etapa
posterior de expansão e validação do corpus.

Consequência: o caminho `VISION_OCR` continua existindo no design
(`knowledge_documents.extraction_method`) e continua sendo o destino correto de
um PDF genuinamente sem texto — apenas não é exercido na primeira versão.

### 18.5 Decisão — `STRICT_CORPUS`

Mantido. Reforçado com `gaps[]` (10.2.1): diante de informação necessária e não
sustentada pelo corpus efetivamente ingerido, o Pack **sinaliza a lacuna** em vez
de completar silenciosamente com conhecimento do modelo.

### 18.6 Em aberto — relação `CatalogNode` ↔ BNCC

A Fase 0 estabeleceu que essa relação **não é inferível do texto** (6.2.3). Ela
exige curadoria pedagógica explícita, e o mecanismo de persistência ainda não foi
decidido: tabela de vínculo própria, `metadata_` do `CatalogNode`, ou adiamento.

Isto **não bloqueia as Fases 1 a 3**. Enquanto não existir, `topic.bncc[]` no
Pack vem vazio — resultado honesto, não falha. Decisão necessária antes da
Fase 4.

---

## 19. Desvios encontrados na Fase 1

Cinco coisas que só apareceram ao escrever o código. Registradas aqui porque
três delas mudam o que a spec dizia, e duas são conhecimento que a Fase 7 vai
precisar.

### 19.1 `VectorCompatible.impl` é `Text`, não `JSON`

A seção 5.4 dizia "`JSON` no SQLite", espelhando `JSONBCompatible`. **Não
funciona, e o modo como falha é silencioso e destrutivo.**

`TypeDecorator` roda o bind processor do `impl` **depois** de
`process_bind_param`. Com `impl = JSON`, o literal do pgvector (`[0.5,-1.25]`)
seria serializado de novo para `"[0.5,-1.25]"` — com aspas — e toda escrita de
vetor no PostgreSQL entraria corrompida. Nenhum erro seria levantado.

Com `impl = Text`, que não tem bind processor, o valor passa intacto. O efeito
colateral vale mais do que a coluna JSON valeria: a representação armazenada
passa a ser byte-idêntica nos dois dialetos, então há **uma** forma
serializada para raciocinar, não duas. Nada consulta o vetor pelas funções JSON
do SQLite, então nada se perde.

Coberto por `test_impl_is_not_json_so_the_literal_is_never_double_encoded`.

### 19.2 `_PgVector` é escrito à mão, sem o pacote `pgvector`

O pacote traria numpy como dependência transitiva. O que o projeto precisa dele
são duas linhas (`get_col_spec` devolvendo `"vector"`). Mesma lógica que já
levou o `JSONBCompatible` a existir em vez de uma dependência.

### 19.3 SQL cru precisa de cast explícito — o ORM não

`psycopg` tipa um parâmetro `str` como `VARCHAR`, e o PostgreSQL **não** faz
cast implícito de `VARCHAR` para `vector` nem para `uuid`. Pelo ORM o problema
não existe, porque o tipo da coluna viaja junto com o bind.

Isto mordeu duas vezes na Fase 1: no seed da migração 058 (resolvido com
`sa.bindparam(..., type_=sa.Uuid())`) e num teste. A Fase 7 escreverá a
consulta de recuperação com `<=>` em SQL cru e vai encontrar o mesmo problema,
então ficou fixado como teste executável:
`test_raw_sql_must_cast_a_bound_parameter_to_vector`.

### 19.4 A cadeia de migrações não roda de `base` num banco vazio

`024_chemistry_kinetics` é uma migração de **dados** que aborta com
`RuntimeError: Required taxonomy parent is missing: CHEMISTRY-PHYSICAL` se a
taxonomia não estiver semeada. É pré-existente e não tem relação com o CÉREBRO.

**Correção a esta seção (2026-10-01):** a primeira versão dizia que
`alembic upgrade head` a partir de `base` "não é um caminho de verificação
disponível". Isso está errado — é uma precondição de **dados**, não um bloqueio
estrutural. Chamar `CurriculumTaxonomyService.seed_reference_fixture()` depois
de `023` deixa a cadeia correr até o head (já verificado por execução em
2026-09-14, alcançando `044`). O caminho existe; a Fase 1 apenas não o usou.

As migrações 057 e 058 foram verificadas isoladamente: banco descartável, os
pré-requisitos (`educational_resources`, `catalog_nodes`) criados por
`create_all`, `alembic stamp 055`, depois `upgrade head` e `downgrade 055`.
Ambas as direções passam.

### 19.5 O banco de desenvolvimento está carimbado em `059`, de outro fluxo

`alembic_version` no banco de dev é `059_mass_correction_runs`, revisão que não
existe neste branch. As migrações 057 e 058 **não foram aplicadas ao banco de
desenvolvimento** — fazê-lo exigiria resolver antes a divergência de heads, que
por decisão do usuário fica para o momento da integração dos branches, via
merge revision.

A extensão `vector` **foi** criada no banco de dev (autorizada, aditiva,
verificada operacional). É independente das tabelas e é pré-requisito delas.

### 19.6 A coluna `vector` quebrou `create_all` em todo teste PostgreSQL do projeto

Encontrado só pela suíte completa, depois que 42 testes novos e 442 de
regressão dirigida passavam.

`Base.metadata.create_all` contra um PostgreSQL sem a extensão pgvector falha
com `type "vector" does not exist`. Isso atingiu **cinco arquivos de teste
`*_postgresql` existentes**, nenhum relacionado ao Knowledge Engine, todos eles
criando o seu próprio banco descartável — que naturalmente não tem a extensão.

Corrigido de forma declarativa, em `db/models/knowledge_engine.py`:

```python
event.listen(
    Base.metadata, "before_create",
    DDL("CREATE EXTENSION IF NOT EXISTS vector").execute_if(dialect="postgresql"),
)
```

Editar os cinco arquivos estaria errado duas vezes: não consertaria os testes
ainda por escrever, e espalharia uma preocupação do Knowledge Engine por
arquivos que não deveriam conhecê-la. O listener vive no único lugar que sabe
que a extensão é necessária, e é o equivalente, no caminho `create_all`, do
que a migração 058 faz no caminho Alembic.

O `setUpClass` de `test_knowledge_engine_schema_postgresql.py` **deixou** de
criar a extensão à mão, de propósito, para que o listener seja de fato
exercitado —
`test_create_all_installs_the_extension_without_being_asked` falharia se
alguém o removesse.

Consequência operacional a registrar: `create_all` contra PostgreSQL passa a
exigir privilégio para criar extensão. Em produção o caminho é Alembic, não
`create_all`, e `IF NOT EXISTS` torna a operação no-op onde já está instalada.

### 19.7 `token_estimate` e o guarda de credenciais

`knowledge_chunks.token_estimate` foi rejeitado por
`test_no_table_anywhere_has_a_credential_column`, o guarda que proíbe qualquer
coluna com fragmento `password`/`senha`/`credential`/`secret`/`token` em
qualquer tabela do schema.

É falso positivo — é uma estimativa de tamanho em tokens de LLM, usada para
orçar quanto contexto um Pack pode carregar, não um segredo. O guarda tem
mecanismo de exceção **nomeada e justificada** exatamente para essa classe,
com precedentes já registrados (`essay_corrections.input_tokens`,
`pedagogical_classifications.*_tokens`). A exceção foi registrada.

Não foi afrouxada a varredura nem renomeada a coluna: o próprio teste diz que
a varredura "must never be narrowed to dodge a specific hit", e o mecanismo de
exceção é o caminho previsto. Uma terceira asserção do guarda garante que a
lista não apodreça — exceção apontando para coluna inexistente é erro — de
modo que as colunas `*_tokens` que a Fase 9 vai criar em `knowledge_packs`
só poderão ser registradas quando existirem.

---

## 20. Fase 3 — extração e chunking: decisões

Aprovado em 2026-10-01, com três ajustes ao plano apresentado. Esta seção é
normativa para a Fase 3.

### 20.1 Esqueleto × substância

`parse_authorial_pdf` **colapsa whitespace** nos `content_lines`
(`" ".join(lead.split())`), então no caminho PDF as fronteiras de parágrafo
já foram destruídas antes de o chunker ver o texto. Chunking por parágrafo
sobre `content_lines` é impossível.

Portanto: **o parser dá o esqueleto, o texto cru das páginas dá a
substância.**

| De `parse_authorial_document` | De `read_pdf_page_texts` |
|---|---|
| fronteiras de seção, `section_type`, `title`, `section_number` | texto com quebras de parágrafo **preservadas** |
| `page_start`/`page_end` por seção | — |
| marcadores `## subtítulo` | — |
| `ParsedQuestion` por exercício, com página | — |
| `ParsedAsset` (imagem com hash, ou referência visual) | — |

O parser não expõe offsets de caractere. Para cortar com precisão, o chunker
**localiza o título da seção** no texto cru das páginas da faixa. Achou →
corte exato. Não achou → granularidade de página, e o chunk carrega
`metadata.boundary_approximate = true`. A aproximação é visível, nunca
silenciosa.

### 20.2 Três representações, e qual delas alimenta o hash

Distinção obrigatória. O contexto que o sistema acrescenta **nunca** pode
parecer parte do texto literal da obra.

| Representação | O que é | Onde vive |
|---|---|---|
| `raw_text` | **conteúdo literal extraído da fonte.** Nada acrescentado pelo sistema: nem título, nem caminho hierárquico, nem rótulo, nem separador inventado | coluna `knowledge_chunks.raw_text` |
| `heading_path` | **contexto estrutural** derivado pelo sistema | coluna `knowledge_chunks.heading_path` (JSON) |
| `retrieval_text` | **representação enriquecida** para busca e embedding: `heading_path` + `raw_text`, montados por função pura e versionada | **não é persistido** — é derivado |

`retrieval_text` é função pura de `(raw_text, heading_path, política)`, em
`knowledge_chunking_policy/v1.py :: build_retrieval_text()`. Não é
persistido: duplicaria ~9 MB e seria uma segunda fonte de verdade capaz de
sair de sincronia.

**`text_hash = sha256(retrieval_text)`.** Esta é a decisão normativa, e a
razão importa: `text_hash` também existe em `knowledge_chunk_embeddings`,
onde seu trabalho é ser a chave de idempotência do embedding. O embedding é
calculado a partir do que se manda ao provider, que é exatamente
`retrieval_text`. Hashear `raw_text` faria uma mudança na política de
enriquecimento passar em silêncio, deixando embeddings obsoletos
indistinguíveis de válidos.

Consequência aceita e desejada: mudar a política de enriquecimento muda o
hash e **dispara re-embedding**. É o comportamento correto.

`metadata.source_text_sha256` guarda o hash do `raw_text` isolado, para que
"o texto da fonte mudou" seja distinguível de "nosso enriquecimento mudou".

`metadata.chunking_policy_version` registra a versão da política, para que
todo chunk seja reproduzível.

### 20.3 `PARTIAL` — critérios determinísticos

Migração **`059_knowledge_documents_partial_status`** acrescenta `PARTIAL` ao
CheckConstraint de `knowledge_documents.extraction_status`. É `DROP` + `ADD`
de CHECK, sem reescrita de tabela.

**Página vazia não é, por si, perda de conteúdo.** Páginas podem ser
intencionalmente vazias (verso de capa, folha de guarda) ou
predominantemente visuais. Classificar todo documento com uma página vazia
como `PARTIAL` tornaria o estado inútil por excesso de alarme.

Sinais, todos determinísticos, calculados por página:

- `text_pages` — páginas com >= `MIN_USEFUL_CHARS` (200) de texto;
- `empty_pages` — páginas com 0 caracteres após `strip()`;
- `sparse_pages` — páginas com 1..199 caracteres;
- `pages_with_images` — páginas com ao menos uma imagem embutida;
- **páginas interiores** — as estritamente entre a primeira e a última
  página com texto. Capa, folhas de guarda e brancos finais ficam fora por
  construção.

Regras, avaliadas em ordem:

| Status | Condição |
|---|---|
| `FAILED` | `text_pages == 0` |
| `PARTIAL` | **`IMAGE_ONLY_INTERIOR_PAGE`** — >=1 página interior sem texto útil **e com** imagem embutida. É a assinatura de página escaneada/achatada: havia conteúdo, e ele não saiu. |
| `PARTIAL` | **`CONTIGUOUS_GAP`** — corrida de >=3 páginas interiores consecutivas sem texto útil. Um buraco de três páginas no meio de um capítulo é perda, haja imagem ou não. |
| `PARTIAL` | **`HIGH_GAP_RATIO`** — mais de 10% das páginas interiores sem texto útil. Pega a perda difusa que nenhuma das outras duas pega. |
| `EXTRACTED` | nenhuma das acima |

Nota sobre a assimetria entre as duas primeiras: uma página **com imagem e
sem texto** é sinal forte de perda; uma página **sem imagem e sem texto** é,
na maioria das vezes, uma página de fato branca. Por isso a primeira regra
exige imagem e a segunda não — a segunda cobre o caso em que a quantidade,
não a natureza, denuncia o problema.

`metadata.extraction.pages_without_text` é registrado **em todos os casos**,
inclusive quando o status é `EXTRACTED`. `metadata.extraction.partial_reasons`
lista os códigos que dispararam.

Em qualquer status, **os chunks das páginas que deram certo são
persistidos**. Falha parcial não descarta trabalho bom.

### 20.4 Heurísticas estruturais: corretas onde importa, refináveis onde não

Prioridade da fase, em ordem: **texto, páginas, origem, hierarquia, hash,
rastreabilidade**. Essas seis precisam estar certas.

A classificação estrutural (`WORKED_EXAMPLE`, `FORMULA`, `TABLE`,
`DEFINITION`, `SUMMARY`) é implementada, mas **não** tenta resolver todo caso
extremo nesta fase. Uma classificação errada degrada ranking; não corrompe o
corpus, porque `raw_text`, página e proveniência seguem corretos.

Para que o refinamento seja possível depois, todo falso positivo é
**observável**: `metadata.structure.classified_as` e
`metadata.structure.signals` registram o que disparou a classificação, e
`GET /sources/{id}/chunks/stats` reporta a distribuição por tipo.

### 20.5 `TEXTBOOK` × `CURRICULUM_FRAMEWORK`

| | `TEXTBOOK` | `CURRICULUM_FRAMEWORK` |
|---|---|---|
| Estrutura | capítulo -> seção -> parágrafo; prosa contínua | área -> competência -> habilidade; lista codificada |
| Unidade natural | um raciocínio (definição + exemplo) | **uma habilidade** |
| Função | **ensina** o conteúdo | **prescreve** o que deve ser aprendido |
| Tamanho | janelamento necessário | nunca janelar |
| Hierarquia | títulos em texto livre | o código (`EM13CNT301`) **é** a hierarquia |
| Chunker | `ProseChunker` (Fase 3) | `CurriculumFrameworkChunker` (Fase 4) |

A Fase 3 implementa só o caminho de prosa e **recusa**
`CURRICULUM_FRAMEWORK` com `422 UNSUPPORTED_SOURCE_KIND_FOR_PHASE`. Rodar o
chunker de prosa sobre a BNCC produziria chunks plausíveis e errados -
janelas cortando habilidades ao meio e perdendo o código, que é a única chave
útil. Recusar é mais correto que produzir lixo convincente.

`OWN_MATERIAL` e `ARTICLE` usam o caminho de prosa. `OTHER` é recusado.

### 20.6 Tamanhos

| Parâmetro | Valor |
|---|---:|
| alvo | 700 tokens (~2800 chars) |
| mínimo | 120 tokens (~480 chars) — abaixo disso, funde com o vizinho da mesma seção |
| máximo | 1100 tokens (~4400 chars) — teto de unidade divisível |
| overlap | ~120 tokens, 1 parágrafo, **só entre `PROSE` da mesma seção** |

`token_estimate = ceil(char_count / 4)` — determinístico, sem dependência de
tokenizer. A coluna chama-se *estimate* de propósito.

Unidade indivisível (`EXERCISE`, `TABLE`, `FORMULA`, `WORKED_EXAMPLE`) acima
do máximo é emitida **inteira**, com `metadata.oversized = true`. Cortar um
exemplo resolvido ao meio produz dois chunks que não sustentam afirmação
nenhuma.

### 20.7 Fora da Fase 3

OCR por visão (o caminho `VISION_OCR` existe e nunca é produzido aqui; PDF
sem texto termina em `FAILED` com `extraction_error` começando por
`OCR_REQUIRED`, que é estado consultável); reconstrução de tabela em PDF;
normalização de fórmula; OCR de imagem; promoção de exercício para o banco
de questões; e `content_node_id` dos chunks, que fica `NULL` nesta fase -
casar chunk com currículo é trabalho do matcher, e misturá-lo com chunking
tornaria as duas coisas mais difíceis de testar.
