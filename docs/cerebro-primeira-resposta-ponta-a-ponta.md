# CÉREBRO — Primeira resposta ponta a ponta

Registro das duas primeiras execuções reais do caminho completo:

```
pergunta → VectorSearcher → ContextBuilder → GroundedAnswerer → resposta
```

Corpus, espaço e reprodução: [cerebro-corpus-vetorial-v1.md](cerebro-corpus-vetorial-v1.md).

## Pergunta

`"Como se calcula a concentração em quantidade de matéria de uma solução?"`

Escolhida por ser conteúdo central do corpus e **não** pertencer a nenhum
conjunto congelado — nem ao `VECTOR_EVALUATION_SET_V1`, nem ao
`VECTOR_CALIBRATION_SET_V1`, nem às `SANITY_ONLY_QUERIES`.

Readiness verde na execução: 5.911 elegíveis / 5.911 com vetor / 0 faltando
/ 0 obsoletos, índice ANN presente, não degradado.

## O defeito que a primeira execução expôs

O modelo respondeu corretamente e citou `[E1]` e `[E4]` no corpo, mas
preencheu `used_evidence` com `["[E1]", "[E4]"]` — **com colchetes**. O
validador comparava a grafia crua contra `{E1…E5}`, não encontrava, e
rotulava a resposta como `INVALID_EVIDENCE_REFERENCE`.

A detecção funcionou e falhou para o lado seguro: nenhuma resposta
não-verificada foi apresentada como fundamentada. Mas era estrita no lugar
errado — o que importa é se o marcador **referencia** evidência existente,
não se o modelo escreveu colchetes num campo JSON.

## As duas execuções, lado a lado

### Retrieval e contexto — **idênticos**

| | 1ª | 2ª |
|---|---|---|
| mesmos `chunk_id` | — | **sim** |
| mesmos scores | — | **sim** |
| mesmo `query_fingerprint` | — | **sim** |
| `cap` / `degraded` | True / False | True / False |
| evidências no contexto | 5 | 5 (as mesmas) |
| chars usados | 11.990 / 12.000 | 11.990 / 12.000 |
| excluídos por `BUDGET_EXHAUSTED` | 5 | 5 |

### Prompt de entrada — **idêntico**

**3.377 tokens de entrada nas duas execuções.** É a confirmação numérica de
que o prompt não mudou: mesma pergunta, mesmas evidências, mesmo texto.

### Resposta

> **1ª:** Calcula-se dividindo a quantidade de matéria do soluto, em mol,
> pelo volume da solução, em litros: c = n/V. **[E1] [E4]**

> **2ª:** Calcula-se dividindo a quantidade de matéria do soluto, em mol,
> pelo volume da solução, em litros: ℳ = n_soluto / V_solução. A unidade é
> mol/L, também podendo ser mol/dm³. **[E1] [E2]**

### Marcadores

| | citados | inválidos |
|---|---|---|
| 1ª | `['E1', 'E4', '[E1]', '[E4]']` | **`['[E1]', '[E4]']`** |
| 2ª | `['E1', 'E2']` | `[]` |

O defeito está visível: as mesmas duas evidências contadas duas vezes, uma
vez certa e uma vez com colchetes, e as com colchetes tidas como inventadas.

### Fontes resolvidas

1ª: E1 — *Química na abordagem do cotidiano*, p. 220 · E4 — *Investigar e
Conhecer*, p. 164.
2ª: E1 e E2 — *Química na abordagem do cotidiano*, p. 220.

Ambas comerciais nas duas execuções: **literal não exibido**,
rastreabilidade por `chunk_id`, `text_hash`, fonte, documento e página
impressa preservada.

### Status

**`INVALID_EVIDENCE_REFERENCE` → `GROUNDED`.** Fundamentada: não → sim.

### Tempos (ms)

| | 1ª | 2ª |
|---|---:|---:|
| `query_embedding_ms` | 844 | 1.579 |
| `retrieval_ms` | 174 | 318 |
| `context_build_ms` | **2** | **5** |
| `generation_ms` | 2.296 | 3.473 |
| **`total_ms`** | **4.745** | **8.099** |

A rede domina. O banco é irrelevante no total, e a montagem de contexto é
ruído.

### Tokens e custo

| | 1ª | 2ª |
|---|---:|---:|
| embedding | 18 | 18 |
| entrada | 3.377 | 3.377 |
| saída | 67 | 87 |
| **custo realizado** | **US$ 0,00054711** | **US$ 0,00055911** |

Todos reais, reportados pelo provider. Nenhuma estimativa apresentada como
custo realizado.

## O que a segunda execução prova — e o que não prova

**Não prova** que a normalização funcionou. A segunda chamada citou `E1/E2`,
não `E1/E4`, e a formulação mudou. Isso é compatível com a
**não-determinismo do provider já medido** na Fase 6 — delta de 1,22 × 10⁻⁴
por componente no mesmo texto de embedding —, e aqui se manifestou também na
geração.

Pior: o relatório grava os marcadores **já normalizados**, não a grafia
crua. Se o modelo devolveu `["E1","E2"]` sem colchetes desta vez, o caminho
real **não exercitou** a correção.

**A prova determinística está no teste de regressão**,
`test_regression_the_exact_shape_of_the_first_real_run`, que reproduz
verbatim a forma observada — corpo com `[E1] [E4]`, campo com
`["[E1]", "[E4]"]` — e verifica `GROUNDED`, zero marcadores inválidos e as
duas evidências resolvidas.

Uma segunda chamada real nunca poderia ser essa prova: ela depende de o
modelo repetir um comportamento que não é determinístico.

## Dívida de observabilidade — registrada, não implementada

O relatório de inspeção deveria gravar, lado a lado:

- `raw_used_evidence` — a grafia exata devolvida pelo provider;
- `normalized_used_evidence` — o resultado da normalização.

Sem isso não se sabe, a partir do relatório, se a normalização chegou a
atuar numa execução — foi exatamente a lacuna que impediu a segunda chamada
de servir como prova. Fica registrado para implementação futura.

## A linha que a normalização não cruza

`[E1]`, `E1` e as mesmas com espaço em volta são o **mesmo** marcador. Mas
a regex está ancorada em `^…$`: a string inteira precisa ser o marcador.

- `"fonte E1 e E2"` → não cita nada
- `"[E1] e [E2]"` → não cita nada
- `"e1"` minúsculo → não cita nada

Tratar texto arbitrário que *contém* um marcador como citação seria fabricar
fundamentação que o modelo não declarou — o oposto do que esta validação
existe para fazer. Aceitar `e1` seria supor intenção, e supor é precisamente
o que se quer evitar.

## O que NÃO foi alterado

Orçamento de contexto (12.000 chars) · top-k (10) · retrieval · prompt ·
temperatura e configuração de geração · `ANSWER_KEY` · thresholds ·
embeddings · políticas · chunking · conjuntos de avaliação.

`BUDGET_EXHAUSTED` cortou 5 dos 10 hits nas duas execuções. É **observação
para análise posterior**, não defeito demonstrado.
