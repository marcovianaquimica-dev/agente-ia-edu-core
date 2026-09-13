# R1 — Régua ENEM versionada e contrato de saída do motor

**Data:** 2026-09-13
**Status:** design aprovado, aguardando plano de implementação
**Sub-projeto:** R1 da PLATAFORMA REDAÇÃO (módulo `REDACAO_IA`)

---

## 1. Contexto

A PLATAFORMA REDAÇÃO será construída como **módulo dentro do `agente-ia-edu-core`**, e não
como produto separado. O gancho já existe no código: `SchoolModule` aceita `module_key`
`REDACAO_IA` desde a migration `011_platform_administration`, com
`configure_school_module()` e `is_module_enabled()` implementados e testados. O gestor
master liga `AGENTE_IA_EDU`, `REDACAO_IA` ou ambos por escola.

A especificação de produto de referência é *REDAÇÃO • Especificação Completa v1.0*
(setembro de 2026, 27 páginas), daqui em diante **spec v1.0**, citada por seção (§).

### Desvio consciente da spec v1.0

A §26 propõe fundação em Next.js/TypeScript/Prisma. O core é Python/FastAPI/SQLAlchemy/
Alembic. Como a plataforma passa a ser módulo deste repositório, a fase 1 da §26 é
**substituída pela fundação existente**. Nenhuma outra decisão da spec v1.0 é alterada por
isso.

### Decomposição

A spec v1.0 é grande demais para um único plano de implementação. Foi decomposta em nove
sub-projetos, cada um com seu próprio ciclo design → spec → plano → implementação:

| # | Sub-projeto | Depende de |
|---|---|---|
| R0 | Estrutura acadêmica + entitlement por disciplina + política de modo/trava | — |
| **R1** | **Régua ENEM versionada + contrato de saída do motor** | — |
| R2 | Propostas e envio de redação (digitada, foto, PDF) | R0, R1 |
| R3 | Motor de correção MVP + fluxo de aprovação docente | R1, R2 |
| R4 | Devolutiva interativa + PDF estável | R3 |
| R5 | Dashboards (aluno → professor → coordenação → direção) | R0, R3 |
| R6 | Correção em lote + filas/workers | R3 |
| R7 | OCR, tokens, coordenadas, transcrição revisável | R2 |
| R8 | Calibração 2025, Banco Ouro, regressão | R1, R3 |

**R0 e R1 não dependem um do outro** e podem ser implementados em paralelo.

---

## 2. Escopo de R1

### Entrega

1. A régua ENEM 2025 carregada da fonte oficial do INEP, versionada, com **procedência por
   item**.
2. O contrato de saída do motor: schema versionado e validador que **rejeita antes de
   persistir**.
3. A função de chave de repetibilidade (§19), pura e testável isoladamente.
4. O **mecanismo de versionamento de prompt** — o pacote `essay_prompts/` e a função de
   resolução de versão, no padrão de `classification_prompts/`. O texto do prompt de
   correção em si é escrito em R3, junto do motor que o usa.

### Não entrega — deliberadamente

- Nenhuma tabela `EssayCorrection`. A persistência de correções é R3. Construir meia
  tabela de correção agora só cria dívida.
- Nenhuma chamada a provedor de IA. **R1 inteiro é testável sem modelo nenhum**, o que é o
  princípio declarado no README: sobreviver à troca de fornecedor.
- Nenhuma interface. R1 é biblioteca e dados.

---

## 3. Contexto regulatório — CNE, setembro de 2026

Em **1º de setembro de 2026** o Conselho Nacional de Educação aprovou as *Diretrizes
Orientadoras da Utilização da Inteligência Artificial na Educação Brasileira*, elaboradas
por Comissão Bicameral CEB+CES.

### O que está firme em todas as fontes consultadas

- O parecer cria **quatro camadas de risco** — baixo, moderado, alto e **excessivo**, esta
  última nova e reservada a aplicações tidas como incompatíveis com a educação
  "independentemente de qualquer salvaguarda".
- **A IA não pode ser a origem da nota oficial** de redação ou questão dissertativa. Uma
  escola não pode enviar o texto do aluno a uma ferramenta e adotar o resultado como nota
  oficial.
- Prazo de 12 meses de adequação a contar da publicação da resolução.

### O que as fontes divergem

| Fonte | Enquadramento da correção de redação por IA |
|---|---|
| PORVIR | Risco **excessivo**, vedando "correção **exclusiva** de redações e produções autorais" |
| Mundo Conectado | **Alto risco**, com supervisão humana significativa e contínua |
| Revista Educação | Correção com consequência acadêmica = **alto risco**; tutoria e feedback formativo **sem efeito decisório** = risco moderado |
| ICL Notícias / antihype | Vedada inclusive a **pré-correção com sugestão de nota ao professor** |

### Estado de vigência

O parecer **aguarda homologação do MEC** (estimativa noticiada: até 60 dias). O texto
oficial não está publicado — não consta do acervo do CNE, e a pasta de setembro/2026 do
portal contém apenas pautas de sessão. Sem homologação e sem resolução no DOU, o prazo de
12 meses ainda não começou a correr.

### Consequência para o design

**A camada de risco é configuração do produto, não premissa da arquitetura.** A regulação é
escalonada; o produto também é. Três modos por instituição, configurados em R0 no
`metadata` de `SchoolModule`:

- **`FORMATIVO`** — marcações, evidências, feedback por competência e plano de ação.
  Nenhuma nota exibida, a ninguém. Corresponde a feedback sem efeito decisório.
- **`ASSISTIDO`** — a IA produz análise; o professor atribui a nota **sem ver sugestão
  numérica**. `ai_score` existe no banco para calibração e auditoria, mas não chega à tela
  antes da decisão docente.
- **`AVALIATIVO`** — o modelo da spec v1.0, com nota estimada visível e ajuste docente.

Impacto direto em R1: **a pontuação é opcional no contrato de saída.** Em `FORMATIVO` o
motor produz análise sem bloco de pontuação, e o contrato precisa considerar isso válido.

As três exigências que aparecem em todas as leituras — supervisão humana efetiva,
explicabilidade e direito de contestação — são atendidas pelo design: procedência por
descritor, evidência ancorada, rejeição de saída não validada e versionamento imutável. O
direito de contestação exige trilha de auditoria própria e fica registrado como requisito
de R3.

> **Ressalva.** Esta seção resume cobertura jornalística, não o parecer. Quando a resolução
> for publicada no DOU, o enquadramento do produto precisa de avaliação jurídica. O design
> foi feito para acomodar qualquer uma das leituras acima sem retrabalho estrutural.

---

## 4. Abordagem escolhida para a régua

Três abordagens foram consideradas:

- **A — tudo no banco.** Integridade forte, mas o texto normativo passa a viver dentro de
  migrations e deixa de ser revisável em PR.
- **B — tudo em arquivo.** Diff limpo, mas a correção guarda só a string da versão, sem
  garantia de que o arquivo de hoje é o de quando a correção foi feita. Conflita com a §19.
- **C — arquivo como fonte, banco como registro.** **Escolhida.**

Em C, o texto normativo mora em arquivo versionado em git — revisável em PR, com
`source_page` por descritor e o SHA-256 do PDF de origem. Um comando de seed carrega para
as tabelas, e a correção referencia a linha do banco. O arquivo dá revisão humana; o banco
dá integridade referencial e imutabilidade histórica.

---

## 5. Modelo de dados

Cinco tabelas novas, todas aditivas. Nenhuma tabela existente é alterada. Próxima migration
disponível: **039**.

Convenções do repositório seguidas: `Mapped`/`mapped_column`, `Uuid`, `JSONBCompatible`,
`metadata_` mapeado para a coluna `metadata`, `__table_args__` com `CheckConstraint` e
`Index`, docstring de auditoria de reúso na migration.

### 5.1 `essay_rubrics`

A versão da régua.

| Coluna | Tipo | Notas |
|---|---|---|
| `id` | UUID PK | |
| `rubric_version` | VARCHAR(50) | UNIQUE. Ex.: `ENEM_2025` |
| `label` | VARCHAR(255) | Ex.: "Matriz de Referência ENEM — Cartilha do Participante 2025" |
| `effective_year` | INTEGER | |
| `max_total_points` | INTEGER | Default 1000 |
| `official_source_title` | TEXT | |
| `official_source_url` | TEXT | |
| `official_source_sha256` | CHAR(64) | Amarra a régua ao documento exato |
| `status` | VARCHAR(20) | CHECK IN (`DRAFT`, `ACTIVE`, `SUPERSEDED`) |
| `published_at`, `superseded_at` | TIMESTAMPTZ | |
| `metadata` | JSONB | |
| `created_at`, `updated_at` | TIMESTAMPTZ | |

Não há restrição de "apenas uma ACTIVE": réguas não-ENEM (vestibular próprio, redação do
fundamental) coexistem ativas. Essa capacidade é deliberada — a pesquisa de mercado mostrou
concorrentes operando com mais de cem grades de correção distintas.

### 5.2 `essay_rubric_competencies`

| Coluna | Tipo | Notas |
|---|---|---|
| `id` | UUID PK | |
| `rubric_id` | UUID FK → `essay_rubrics` | ON DELETE RESTRICT |
| `code` | VARCHAR(4) | CHECK IN (`C1`,`C2`,`C3`,`C4`,`C5`) |
| `ordinal` | INTEGER | |
| `official_title` | TEXT | **Texto literal da matriz** |
| `max_points` | INTEGER | Default 200 |
| `source_page` | INTEGER | |

UNIQUE(`rubric_id`, `code`).

### 5.3 `essay_rubric_levels`

Os seis níveis por competência. Trinta linhas para a ENEM 2025.

| Coluna | Tipo | Notas |
|---|---|---|
| `id` | UUID PK | |
| `competency_id` | UUID FK → `essay_rubric_competencies` | |
| `points` | INTEGER | CHECK IN (0, 40, 80, 120, 160, 200) |
| `descriptor` | TEXT | **Texto literal da cartilha** |
| `source_page` | INTEGER | NOT NULL |
| `provenance` | VARCHAR(30) | Sempre `OFICIAL_INEP` neste nível |

UNIQUE(`competency_id`, `points`).

### 5.4 `essay_rubric_signals`

O vocabulário observável que o motor usa (§4 e §17): `repertorio_legitimidade`,
`repertorio_pertinencia`, `repertorio_produtividade`, `c5_agente`, `c5_acao`, `c5_meio`,
`c5_finalidade`, `c5_detalhamento`, e assim por diante.

| Coluna | Tipo | Notas |
|---|---|---|
| `id` | UUID PK | |
| `competency_id` | UUID FK | |
| `key` | VARCHAR(80) | |
| `label` | VARCHAR(255) | |
| `description` | TEXT | |
| `provenance` | VARCHAR(30) | CHECK IN (`OFICIAL_INEP`, `INTERPRETACAO_PEDAGOGICA`, `HEURISTICA_MOTOR`) |
| `source_ref` | TEXT | Documento e página |
| `rationale` | TEXT | Justificativa, exigida para heurística |
| `active` | BOOLEAN | |

UNIQUE(`competency_id`, `key`).

Duas restrições que fazem a procedência valer como regra e não como decoração:

```sql
CHECK (provenance = 'HEURISTICA_MOTOR' OR source_ref IS NOT NULL)
CHECK (provenance <> 'HEURISTICA_MOTOR' OR rationale IS NOT NULL)
```

### 5.5 `essay_rubric_zero_rules`

Situações de anulação e nota zero. São normativas, mas **não são nível de competência** —
misturá-las em `essay_rubric_levels` quebraria o CHECK de pontos.

| Coluna | Tipo | Notas |
|---|---|---|
| `id` | UUID PK | |
| `rubric_id` | UUID FK | |
| `key` | VARCHAR(80) | Ex.: `fuga_ao_tema` |
| `label` | VARCHAR(255) | |
| `description` | TEXT | Texto literal quando oficial |
| `effect` | VARCHAR(30) | CHECK IN (`ANULA_REDACAO`, `ZERA_COMPETENCIA`) |
| `competency_code` | VARCHAR(4) | NULL quando anula a redação inteira |
| `source_page` | INTEGER | |
| `provenance` | VARCHAR(30) | |

UNIQUE(`rubric_id`, `key`).

### Separação de responsabilidades

Os níveis são texto oficial imutável. Os sinais são o vocabulário observável do motor. As
regras de zero são condições de curto-circuito. Um consumidor lê a régua sem saber nada do
motor; o motor consulta a régua sem saber de qual arquivo ela veio.

---

## 6. Contrato de saída do motor

Módulo versionado `essay_engine_contract/v1.py`, modelos Pydantic, no mesmo formato de
`classification_prompts/v1.py`. Saída de motor muda de forma com o tempo e correções
antigas precisam continuar legíveis pelo schema com que nasceram.

`CONTRACT_VERSION = "essay_engine_output_v1"`

### Blocos (§18)

| Bloco | Conteúdo |
|---|---|
| `identification` | `essay_id`, `essay_version_id`, `rubric_version`, `model_version`, `prompt_version`, `engine_version`, `contract_version`, `anchor_mode` |
| `scores` | **Opcional.** Por competência: pontos e confiança; mais o total |
| `rationales` | Resumo por competência, com os `signal_keys` invocados |
| `annotations` | Letra, competência, tipo, âncora, comentário curto, comentário longo, natureza da evidência |
| `rewrites` | Original, sugestão, objetivo pedagógico. Opcional |
| `feedback` | Pontos fortes, pontos de melhoria, estratégia para a próxima redação |
| `intervention` | Decomposição de C5: agente, ação, meio/modo, finalidade, detalhamento, direitos humanos |
| `alerts` | Fuga ao tema, tipo textual, texto insuficiente, OCR duvidoso, possível duplicidade |

`scores` é opcional porque em modo `FORMATIVO` o motor não produz pontuação. Um output sem
`scores` é válido; um output com `scores` parcial (três competências de cinco) não é.

### Âncora de marcação — duas formas

A plataforma aceita envio por foto, PDF ou digitação, e a transcrição é **opcional por
instituição** (§9). Quando a escola dispensa a transcrição, a correção é feita direto da
imagem — e **não existe texto canônico contra o qual verificar uma citação**.

```python
TextOffsetAnchor:  type="TEXT_OFFSET";  start: int; end: int; quote: str
ImageRegionAnchor: type="IMAGE_REGION"; page: int; x,y,width,height: float; read_text: str
```

`anchor_mode` em `identification` declara qual das duas a correção usa, e **as duas não se
misturam** no mesmo output.

> **Garantia assimétrica, registrada explicitamente.** Com `TEXT_OFFSET`, a citação é
> verificável: se `quote != texto[start:end]`, a saída é rejeitada. Com `IMAGE_REGION`,
> apenas os limites da página são verificáveis — **a citação em si é inverificável**.
> Corrigir direto da imagem entrega uma devolutiva com garantia mais fraca contra
> alucinação. Isso não proíbe o modo; obriga a registrá-lo na correção, para que a
> calibração (§16, "desempenho por tipo de texto") nunca compare os dois caminhos como
> equivalentes.

---

## 7. Validação em três camadas

O servidor valida antes de salvar. Se a saída não respeitar o contrato, **a correção não é
publicada** (§18).

**Camada 1 — forma.** O schema Pydantic. JSON malformado ou campo ausente morre aqui.

**Camada 2 — coerência com a régua.** É o que transforma a régua de documentação em
restrição executável:

- a pontuação de cada competência tem de ser um dos níveis que *aquela* `rubric_version`
  define para *aquela* competência;
- o total tem de ser a soma das cinco;
- todo `signal_key` citado tem de existir e estar ativo em `essay_rubric_signals`;
- toda anotação tem de apontar para uma competência existente na régua.

**Camada 3 — ancoragem no texto.** O mecanismo que impede a IA de inventar erro para
justificar nota:

- com `TEXT_OFFSET`: `quote` tem de bater com `texto[start:end]`, e os offsets têm de
  caber no texto;
- com `IMAGE_REGION`: a região tem de caber nos limites da página declarada;
- **toda crítica específica exige evidência resolvível, ou a anotação tem de se declarar
  explicitamente avaliação global da competência** (`evidence_kind = GLOBAL`).

A spec §4 chama isso de "regra de segurança pedagógica" e a escreve como recomendação. Aqui
ela é uma condição de rejeição. Como recomendação, é um parágrafo num PDF; como condição de
rejeição, é uma garantia.

### Quando falha

`EssayEngineOutputRejected`, carregando motivo, output bruto e `input_hash`. Nada é
persistido, nada é publicado, e o caso fica reprocessável. É o mesmo padrão de
`_validation_error` em `services/curriculum_classification.py:1159` — mesmo vocabulário,
mesmo comportamento, sem inventar um paralelo.

---

## 8. Repetibilidade e versionamento

A chave da §19:

```
correction_key = sha256(canonical_json({
    normalized_text_hash,   # sha256 do texto da redação após normalização
    essay_prompt_id,        # o TEMA proposto (EssayPrompt), não o prompt de IA
    rubric_version,
    model_version,
    prompt_version,         # a versão do prompt de IA
    engine_version,
}))
```

`essay_prompt_id` e `prompt_version` são coisas diferentes e foram nomeadas assim de
propósito: o primeiro é a proposta de redação que o aluno respondeu, o segundo é a versão
do prompt enviado ao modelo. A spec v1.0 usa "proposta" e "prompt" de forma intercambiável
em §19; aqui não.

O helper de canonicalização já existe em `services/curriculum_classification.py:1225`
(`json.dumps(sort_keys=True, ensure_ascii=False, separators=(",", ":"))` seguido de
sha256). **R1 extrai isso para um utilitário compartilhado** em vez de copiar.

Em R1 essa função é pura e testável sozinha. Quem a consome para deduplicar correção é R3.

### As quatro versões e quem move cada uma

| Versão | Muda quando |
|---|---|
| `rubric_version` | O INEP muda a matriz, ou se cadastra uma régua não-ENEM |
| `prompt_version` | Reescrevemos o prompt |
| `model_version` | Vem do provedor |
| `engine_version` | O pipeline muda de forma relevante |

Nenhuma é derivada das outras — por isso são quatro colunas e não uma string concatenada.

**Nunca recalcular correção histórica em silêncio.** Gerar de novo o PDF de uma correção
feita sob a régua 2025 usa a correção armazenada, não a régua vigente (§8, §19).

---

## 9. Seed da cartilha

### Fonte

*A Redação do Enem 2025 — Cartilha do(a) Participante*, INEP/MEC. 78 páginas.
SHA-256 `d8ab44dcbf5af808829d9dee89d23e7efa4f59df022b99102fac87489b870288`.

**É a versão mais recente.** Não existe cartilha 2026: o acervo do INEP lista 2025, 2024,
2023, 2022 e 2018, e a URL padrão de 2026 não existe em `download.inep.gov.br`.

Verificado no documento: os seis níveis por competência em quadro próprio, e **"repertório
de bolso" aparece explicitamente**, com exemplos de uso na introdução e no desenvolvimento
e uma seção "Como evitar o repertório de bolso?". A premissa central da spec §2 e §17 está
sustentada pela fonte oficial.

### O seed não é um parser em runtime

O PDF usa subsets de fonte com encoding customizado. A extração ingênua devolve trechos
corrompidos — `'HPRQVWUDGRPtQLRLQVX¿FLHQWH` no lugar de "Demonstra domínio insuficiente"
(deslocamento constante de +29 no subset). É decodificável, mas um parser rodando em
produção sobre o PDF de um terceiro é frágil e desnecessário.

```
PDF oficial (SHA-256 registrado)
  → script de extração único, versionado em tools/
  → arquivo YAML revisável, com source_page por descritor
  → CONFERÊNCIA HUMANA OBRIGATÓRIA
  → comando de seed
  → tabelas
```

O script é ferramenta de uma vez, não dependência do sistema. O artefato que importa é o
YAML, revisado em PR linha por linha contra o PDF: 5 títulos de competência, 30 descritores
de nível e as regras de anulação. É o que separa uma régua com procedência de uma paráfrase.

### O PDF não vai para o git

1,66 MB de documento público com URL estável. `essay_rubrics` guarda título, URL e SHA-256;
quem quiser conferir baixa e compara o hash. Se o INEP retirar o documento do ar, aí vale
arquivar em object storage.

### Procedência, para valer como regra

Nenhum sinal entra sem `provenance`. `INTERPRETACAO_PEDAGOGICA` exige `source_ref` com
documento e página — a evidência da *Cartilha Redação a Mil 8.0 (2026)* sobre repertório e
argumentação entra por aqui, nunca como se fosse regra oficial. `HEURISTICA_MOTOR` exige
`rationale` escrita. As duas exigências são CHECK constraints, não convenção.

Sem essa separação, em dois anos ninguém distingue o que o INEP determinou do que nós
inferimos — e a §17 diz que essa distinção "será essencial para auditoria e futuras
mudanças do Inep".

---

## 10. Testes

TDD: **os testes do contrato vêm antes do contrato.**

### Régua

- seed idempotente — rodar duas vezes não duplica;
- exatamente 5 competências e 30 níveis para a ENEM 2025;
- nenhuma linha de nível sem `source_page`;
- as CHECK constraints de procedência rejeitam sinal sem `source_ref` e heurística sem
  `rationale`;
- régua `SUPERSEDED` continua legível e consultável.

### Contrato

Um golden file válido e cerca de doze inválidos, **cada um rejeitando com motivo
específico**:

1. nota fora dos níveis daquela competência;
2. total diferente da soma das cinco;
3. `scores` parcial (três competências de cinco);
4. `quote` que não bate com `texto[start:end]`;
5. offsets fora dos limites do texto;
6. anotação em competência inexistente na régua;
7. `signal_key` inexistente ou inativo;
8. crítica específica sem evidência e sem `evidence_kind = GLOBAL`;
9. `bbox` fora dos limites da página;
10. âncora de imagem em correção declarada `TEXT_OFFSET`;
11. mistura de âncoras no mesmo output;
12. `contract_version` desconhecida.

Se qualquer um desses passar, a §18 não está implementada.

### Repetibilidade

- mesma entrada produz a mesma chave;
- mudar qualquer uma das quatro versões muda a chave;
- canonicalização estável sob reordenação de chaves e sob variação de normalização Unicode.

### Regressão

No formato de `tests/test_real_exam_corpus_regression.py`, que o repositório já usa.

Nada em R1 depende de provedor de IA.

---

## 11. Critérios de aceite

- [ ] A régua ENEM 2025 está no banco, com 5 competências, 30 níveis e as regras de
      anulação, cada item com página de origem conferida por humano.
- [ ] `essay_rubrics` registra título, URL e SHA-256 do PDF oficial.
- [ ] Nenhum sinal existe sem procedência; as constraints impedem.
- [ ] Um output válido passa; cada um dos doze inválidos é rejeitado com motivo próprio.
- [ ] Um output sem `scores` (modo `FORMATIVO`) é válido.
- [ ] Um output com `scores` parcial é rejeitado.
- [ ] `correction_key` é estável e sensível às quatro versões.
- [ ] O utilitário de canonicalização é compartilhado, não copiado.
- [ ] `essay_prompts/` resolve uma versão de prompt para o artefato correspondente, e uma
      versão desconhecida falha explicitamente em vez de cair num default silencioso.
- [ ] A suíte de R1 roda sem chamar provedor de IA.
- [ ] Nenhuma tabela existente foi alterada; a migration 039 é puramente aditiva e
      reversível.

---

## 12. Requisitos capturados aqui, implementados em outro sub-projeto

Levantados durante o brainstorming, fora do escopo de R1:

- **R0** — modo da instituição (`FORMATIVO` / `ASSISTIDO` / `AVALIATIVO`) no `metadata` de
  `SchoolModule`; granularidade de disciplina no entitlement; política de trava.
- **R3** — fluxo de aprovação docente: uma a uma, em lote, ou automática. A aprovação
  automática deve ser **condicionada ao modo da instituição** — é a única das três que
  remove a supervisão humana por completo. Trava configurável por faixa de nota, e também
  por baixa confiança por competência, divergência grande entre competências e alerta de
  fuga ao tema, que são os casos que a §16 manda amostrar. Professor altera feedback e nota
  em qualquer fase, com `ai_score` preservado (§7). Trilha de auditoria de contestação pelo
  aluno.
- **R0/R4/R5** — **white-label por instituição.** A escola usa a própria logomarca,
  não a da plataforma. Isso não é só o cabeçalho da tela: a identidade visual
  (logo, cores, nome exibido do produto) precisa ser configuração da instituição em
  R0, viajar para o PDF exportado em R4 — que a §8 exige ser uma devolutiva
  autônoma, lida por quem talvez nunca abra a plataforma — e aparecer nos
  relatórios de R5. Duas consequências que valem decidir cedo: a identidade usada
  numa devolutiva **deve ser registrada junto com ela**, pela mesma razão que a
  régua é (§19) — regerar um PDF histórico depois de a escola trocar de logo não
  pode reescrever silenciosamente o documento que a família já recebeu; e o
  armazenamento do arquivo de logo é o primeiro caso real de object storage no
  core, que hoje só tem cópia local em `services/material_storage.py`.
- **R5** — percentil do aluno contra a base, por competência. Não consta da spec v1.0, é
  barato com os dados que R5 já terá, e é prática consolidada no mercado.
- **R5/R8** — cruzamento entre correção de redação e recomendação curricular, usando o
  banco de questões e a trilha adaptativa que o `AGENTE_IA_EDU` já tem. Nenhum concorrente
  pesquisado faz isso, porque nenhum tem as duas metades.

---

## 13. Fontes

**Normativa principal.** INEP/MEC, *A Redação do Enem 2025 — Cartilha do(a) Participante*.
<https://download.inep.gov.br/publicacoes/institucionais/avaliacoes_e_exames_da_educacao_basica/a_redacao_no_enem_2025_cartilha_do_participante.pdf>
SHA-256 `d8ab44dc…870288`.

**Pedagógica complementar.** *Cartilha Redação a Mil 8.0 (2026)* — evidência interpretativa,
nunca substituta da matriz oficial.

**Produto.** *REDAÇÃO • Especificação Completa v1.0* (setembro de 2026) e o protótipo
`devolutiva_professor_redacao_840`.

**Regulatória** (cobertura jornalística; o parecer não está publicado):
PORVIR, Revista Educação, ICL Notícias, Mundo Conectado, Apufsc-Sindical.

**Pesquisa de mercado.** Imaginie, Redação Online / Redação Nota 1000, FPGE — Fernanda
Pessoa.

---

Este documento é design de um sub-projeto. Ele não congela a interpretação da matriz:
mudanças futuras do Inep geram nova `rubric_version` e novo ciclo de validação, preservando
o histórico.
