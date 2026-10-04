# Núcleo Diagnostic Bank v1 — Balanceamento

**2026-10-04.** Itens diagnósticos próprios, AI_VERIFIED, selecionáveis pelo
`MicroDiagnosticService`. Escola ABC e Aluno Teste A **não** foram construídos.

---

## 1. Arquitetura

| atual | veredito | por quê |
|---|---|---|
| `Question` / `QuestionVersion` / `QuestionOption` | **reutilizado** | item diagnóstico é questão comum |
| `Question.origin_type = 'GENERATED'` | **reutilizado** | já existia no CHECK, **zero linhas usando** |
| `Question.metadata_` (JSONB) | **reutilizado** | carrega o contrato diagnóstico |
| `Exam` / `ExamApplication` / `ExamBooklet` | **reutilizado** | o banco declara a própria edição |
| `AnswerKeyRevision` / `AnswerKeyEntry` | **reutilizado** | gabarito próprio do Núcleo |
| `PedagogicalClassification.provenance` | **reutilizado** | migration 065, bloco anterior |
| Player / Correção / Mapa de Domínio | **reutilizado** | sem alteração |
| `chemistry_balance` | **novo** | conservação de átomos por contagem |
| `diagnostic_bank` | **novo** | matriz diagnóstica e contrato do item |

**Zero migration. Zero alteração em serviço compartilhado.**

### O que descobri sobre o Question Bank

`QuestionBankService.list_questions` faz **inner join** com
`BookletQuestion → ExamBooklet → ExamApplication`. Ele foi construído em torno
de prova oficial: **questão sem caderno é estruturalmente invisível**.

Minha primeira saída foi afrouxar o resolvedor de gabarito em
`question_list_store` — um fallback para `QuestionOption.is_valid_option`
quando não houvesse caderno. Funcionava, com 6 testes.

Depois dei ao banco diagnóstico a **própria edição** (instituição Núcleo Edu
360, "exame" `NUCLEO_DIAGNOSTIC`, caderno `BALANCEAMENTO-V1`, gabarito
próprio) e descobri que **nenhuma mudança em serviço compartilhado era
necessária**. Reverti o fallback e apaguei seus testes.

Isso não é uma prova disfarçada: `origin_type='GENERATED'` continua separando
item do Núcleo de questão importada, e a instituição é o próprio Núcleo.

### Origem do item ≠ origem da evidência

```
origin_type = GENERATED        de onde veio o ITEM      (conteúdo)
origin      = MICRO_DIAGNOSTIC de onde veio a RESPOSTA  (evidência)
```

O mesmo item respondido numa prática gera evidência `PRACTICE`. O banco de
itens não é o banco de evidência.

---

## 2. A matriz diagnóstica

Não são dez questões parecidas. Cada habilidade descobre uma coisa diferente
sobre **por que** o aluno não está pronto para Estequiometria:

| habilidade | o que descobre |
|---|---|
| `CONSERVACAO_DE_ATOMOS` | sabe que átomos não somem nem aparecem? |
| `RECONHECER_BALANCEADA` | distingue balanceada de não balanceada? |
| `COEFICIENTE_AUSENTE` | acha **um** coeficiente faltando? |
| `BALANCEAR_SIMPLES` | balanceia do zero? |
| `BALANCEAR_MULTIPLAS_ESPECIES` | balanceia com várias espécies? |

Quem erra `CONSERVACAO` tem buraco conceitual; quem acerta tudo menos
`MULTIPLAS` só precisa de prática. A decisão pedagógica é diferente.

---

## 3. Verificação em três camadas

```
OBJETIVO → GERADOR → item → VERIFICADOR (resolve sozinho) → CONTRATO → decisão
                              ↓
                     chemistry_balance (contagem de átomos)
```

**O verificador resolve a questão.** Não recebe o gabarito nem a justificativa
do gerador. Perguntar *"o gabarito D está certo?"* convida a concordar;
perguntar *"qual é a resposta?"* obriga a calcular.

**A química não depende de nenhum dos dois.** O gerador declara, para cada
equação, se ela deveria estar balanceada; `chemistry_balance` confere por
contagem. Um item cuja química não bate com a própria declaração é rejeitado —
e esse é o erro que um segundo LLM também cometeria.

O parser suporta índices, parênteses aninhados, hidratos, subscrito Unicode,
estado físico e carga. **Recusa `Xx2O`** (elemento inexistente faria o balanço
fechar com átomo imaginário) e **recusa equação com lacuna** (`___ H₂O` não é
conferível; assumir 1 seria inventar). Sintaxe fora do escopo levanta exceção:
*"não sei ler"* nunca vira aprovação.

---

## 4. Resultado

| | v1 | v2 |
|---|---:|---:|
| gerados | 10 | 15 |
| AI_VERIFIED | 3 | **14** |
| REQUIRES_REVIEW | 7 | 1 |

A v1 rejeitou 6 itens **pela mesma causa**: `H₂`, `O₂`, `Fe₂O₃` com subscrito
Unicode, que o parser não lia. O fail-closed funcionou — recusou o que não
sabia ler, em vez de chutar — mas rejeitava química correta por notação.

Cobertura: 3+3+3+3+2 nas cinco habilidades.

### Viés posicional — defeito do conjunto, não dos itens

| | A | B | C | D | E |
|---|---:|---:|---:|---:|---:|
| antes | 0% | **64%** | 29% | 0% | 7% |
| depois | 21% | 21% | 21% | 21% | 14% |

Cada item estava correto; a química de todos passou na contagem. O defeito era
do **conjunto**: um aluno marcando sempre "B" acertaria 64% sem saber nada de
balanceamento. Num diagnóstico isso é pior que num teste comum, porque a
conclusão não é uma nota — é *"o aluno está pronto para Estequiometria"*.
**Viés posicional vira falso-pronto.**

Corrigido com redistribuição determinística, que reordena alternativas sem
tocar na química. 5 testes.

### Adversarial

**Zero itens com mais de uma alternativa balanceada** — conferido por contagem.
Zero respostas corretas que são equação e não balanceiam.

---

## 5. Seleção real

```
minimo exigido pelo microdiagnostico: 3
  OK  CHEMISTRY-GENERAL-BALANCING   selecionaveis=14
```

Medido pela `PracticeSelectionPolicy` + `QuestionBankService` reais, num banco
construído pela cadeia de migrations.

---

## 6. A divergência das migrations 063

**Não consertei, não forcei upgrade, não carimbei.**

| onde | revisão |
|---|---|
| banco de desenvolvimento | `063_platform_material_target` |
| cadeia deste worktree | `063_embedding_activation` → 064 → 065 |

São **linhagens divergentes**: duas migrations diferentes receberam o número
063 em ramos diferentes. O banco de dev foi migrado por um ramo que este
worktree não tem.

**Sintoma observado:** tentar persistir os itens no banco de dev falhou com
`column "provenance" of relation "pedagogical_classifications" does not exist`
— a coluna da migration 065, que o banco de dev nunca viu.

**Como contornei, sem contaminar nada:** todo teste e o piloto rodam em banco
descartável construído pela cadeia real, ou em SQLite com
`Base.metadata.create_all`.

### Proposta de reconciliação (decisão sua, não minha)

Três caminhos, com custo honesto:

1. **Recriar o banco de dev pela cadeia deste worktree.** Mais simples e
   limpo. Custo: perde os dados de dev — inclusive as 561 questões e as 2.972
   extraídas. Exigiria reimportar.
2. **Merge das linhagens.** Criar uma migration de merge que declare as duas
   063 como pais. Alembic suporta. Custo: alguém precisa verificar se as duas
   063 tocam tabelas disjuntas — se tocarem a mesma, há conflito de verdade a
   resolver à mão.
3. **Manter separado.** O worktree usa banco próprio; a reconciliação espera o
   merge do ramo. Custo zero agora, custo alto no dia do merge.

Não escolho: a 1 destrói dados seus, e a 2 exige inspecionar um ramo que não
está neste worktree.

---

## 7. Dívidas

1. **Os 14 itens não estão no banco de dev** — bloqueados pela divergência 063.
   Vivem no piloto descartável e em `/tmp/diagnostic_v2.json`.
2. **`persistir()` no script de geração** grava sem a edição própria; quem
   persiste de verdade é `scripts/piloto_diagnostic_bank.py`. Convergir os dois.
3. **O gerador não consulta a BNCC.** A taxonomia interna bastou para
   Balanceamento; vínculo BNCC ficou fora, como o item 5 permitia.
4. **A redistribuição de gabarito é posicional, não aleatória.** Determinística
   de propósito (auditável), mas um aluno que visse muitos itens notaria o
   padrão A-B-C-D-E. Irrelevante para 3 itens por sessão; relevante se o banco
   crescer.
