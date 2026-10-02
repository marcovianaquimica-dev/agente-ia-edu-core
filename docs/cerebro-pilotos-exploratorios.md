# CÉREBRO — pilotos reais exploratórios

> **Estas perguntas NÃO são benchmark.**
>
> Foram escolhidas por conveniência diagnóstica, sem adjudicação humana,
> sem pool, sem qrels. **Não podem ser promovidas retroativamente** a
> `VECTOR_EVALUATION_SET_V1`, `VECTOR_CALIBRATION_SET_V1` nem
> `SANITY_ONLY_QUERIES`, e **não podem ser usadas para escolher ou ajustar
> parâmetro algum** — top-k, orçamento, pesos, thresholds, teto por fonte
> ou política editorial.
>
> A razão é a mesma que [sanity_queries.py](../src/agente_ia_edu/knowledge_retrieval_policy/sanity_queries.py)
> documenta: uma consulta que "deu bom resultado" vira, meses depois, uma
> consulta de benchmark — e o benchmark passa a medir o ajuste feito sobre
> ela. A régua é o Evaluation Set, e ele continua congelado.

## Condições, idênticas em todas as execuções

| | |
|---|---|
| banco | `agente_ia_edu_fase6_vetorial` |
| `embedding_space_id` | `0197e5a0-0000-7000-8000-000000000001` |
| fingerprint | `8471ea331d464904` |
| dimensões / métrica | 1536 / cosine |
| `policy_version` / `normalizer` / `editorial_detector` | v1 / v1 / v1 |
| `candidate_cap` | 2000 (atingido em todas) |
| `retrieval_purpose` | `LEARN` |
| cobertura | 5.911 elegíveis / 5.911 com vetor / 0 faltando / 0 obsoletos |
| índice ANN | presente |
| filtrados por `EDITORIAL_ROLE` | 239 em **todas** |
| degradado | `False` em todas |

Um único fingerprint em todas as execuções: o espaço não mudou durante o
piloto.

## As execuções

Sete perguntas foram executadas. Seis têm relatório JSON completo; uma tem
apenas a saída de console.

| | pergunta | artefato |
|---|---|---|
| P1 | concentração em quantidade de matéria | JSON |
| P2 | idem, **segunda execução** após a correção de marcadores | JSON |
| — | `"SUA PERGUNTA AQUI"` (texto de exemplo rodado por engano) | só console |
| AM | diferença entre átomo e molécula | só console |
| A | modelos atômicos de Dalton a Rutherford | JSON |
| B | o que torna uma substância ácida ou básica | JSON |
| C | como a tabela periódica está organizada | JSON |
| D | como se balanceia uma equação química | JSON |

P1 e P2 estão detalhadas em
[cerebro-primeira-resposta-ponta-a-ponta.md](cerebro-primeira-resposta-ponta-a-ponta.md).

**Lacuna declarada:** a execução do texto de exemplo e a de átomo × molécula
foram rodadas sem `--json`. Os números abaixo para elas vêm da saída de
console e foram conferidos manualmente, não relidos de artefato. Para AM a
conferência bate com o reportado: ranks 1, 2, 3, 8 e 10 com `ANSWER_KEY`,
`E1`/`E2`/`E3` no contexto, `E1` e `E3` citadas.

## Resultado por execução

| | `ANSWER_KEY` top-10 | no contexto | citadas | status | `sufficient` |
|---|---:|---:|---:|---|---|
| P1 concentração | 1/10 | 1/5 | 0/2 | `INVALID_EVIDENCE_REFERENCE` | True |
| P2 concentração | 1/10 | 1/5 | 0/2 | `GROUNDED` | True |
| AM átomo × molécula | 5/10 | 3/5 | 2/3 | `GROUNDED` | — |
| A modelos atômicos | 3/10 | 1/5 | 1/2 | `GROUNDED` | True |
| B ácido/base | 2/10 | 2/6 | 0/3 | `GROUNDED` | True |
| C tabela periódica | 1/10 | 1/5 | 0/3 | `GROUNDED` | True |
| D balanceamento | 0/10 | 0/5 | 0/2 | `GROUNDED` | True |

A execução do texto de exemplo saiu `ANSWER_WITHOUT_CITATION`, não
fundamentada — o modelo declarou que a pergunta não havia sido informada e
não citou nada. Controle negativo acidental, e o portão funcionou.

### Composição editorial do top-10

| | CONTENT | ANSWER_KEY | UNKNOWN |
|---|---:|---:|---:|
| P1 / P2 | 8 | 1 | 1 |
| A | 5 | 3 | 2 |
| B | 8 | 2 | 0 |
| C | 9 | 1 | 0 |
| D | 8 | 0 | 2 |

### Orçamento

| | usado / 12.000 | evidências | cortadas |
|---|---|---:|---:|
| P1 / P2 | 11.990 | 5 | 5 |
| A | 11.478 | 5 | 5 |
| B | 11.405 | 6 | 4 |
| C | 10.977 | 5 | 5 |
| D | 11.739 | 5 | 5 |

### Scores

| | máx | mín | amplitude | fontes distintas |
|---|---:|---:|---:|---:|
| P1 / P2 | 0,7294 | 0,6622 | 0,0672 | 3 |
| A | 0,6922 | 0,6342 | 0,0579 | 2 |
| B | 0,6356 | 0,6070 | 0,0286 | 3 |
| C | 0,6543 | 0,6298 | 0,0245 | 3 |
| D | 0,7334 | 0,6332 | 0,1001 | 3 |

### Tempos (ms) e custo

| | embedding | retrieval | contexto | geração | total | tok in | tok out | US$ |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| P1 | 844 | 174 | 2 | 2.296 | 4.745 | 3.377 | 67 | 0,00054711 |
| P2 | 1.579 | 318 | 5 | 3.473 | 8.099 | 3.377 | 87 | 0,00055911 |
| A | 1.725 | 228 | 13 | 3.665 | 7.897 | 3.138 | 272 | 0,00063418 |
| B | 1.232 | 514 | 16 | 2.945 | 7.058 | 3.592 | 209 | 0,00066448 |
| C | 856 | 208 | 10 | 2.841 | 5.540 | 3.540 | 234 | 0,00067160 |
| D | 904 | 260 | 8 | 3.076 | 5.769 | 3.675 | 267 | 0,00071167 |

**Custo total dos seis relatórios JSON: US$ 0,00378815.** Todos os números
são reais, reportados pelo provider; nenhuma estimativa aparece como custo
realizado.

Rede domina em todas: embedding + geração são 93% a 99% do total. O banco
nunca passou de 514 ms e a montagem de contexto nunca passou de 16 ms.

## Diagnóstico de `ANSWER_KEY` — fechado sem alteração de política

A política de [v1.py](../src/agente_ia_edu/knowledge_retrieval_policy/v1.py)
torna `ANSWER_KEY` elegível em `LEARN` e `AUTHOR`, fechado em `PRACTICE` e
`ASSESS`, com a justificativa de que em `LEARN` o gabarito explica o
procedimento. **A política não foi alterada.**

### A hipótese testada não se confirmou

Formulou-se que maior afinidade da pergunta com enunciado de exercício
produziria maior presença de `ANSWER_KEY`. As quatro perguntas A–D foram
desenhadas como gradiente crescente dessa afinidade. O resultado caiu
**monotonicamente na direção oposta**: 3, 2, 1, 0. A pergunta mais
procedimental — balanceamento — foi a única com zero `ANSWER_KEY`.

**A hipótese está refutada pelo caso que deveria confirmá-la melhor.**

Uma segunda hipótese foi levantada — de que a variável seria a generalidade
da pergunta, não a afinidade com exercício. Ela também não se sustenta:
"concentração em quantidade de matéria" é tão ampla quanto "átomo ×
molécula" e teve 1/10 contra 5/10.

**Nenhuma variável explicativa foi identificada.** Com n=7, sem adjudicação
e sem controle, não há base para propor alteração de política, e nenhuma é
proposta.

### O que é estável e merece observação futura

Dois fatos se repetem em **todas** as execuções e não dependem da hipótese
acima:

**1. `BUDGET_EXHAUSTED` corta metade do top-10, sempre.** Entre 4 e 5 dos 10
hits ficam de fora em todas as sete execuções. O orçamento de 12.000
caracteres admite consistentemente 5 ou 6 evidências de ~2.000 caracteres.
Não é defeito demonstrado — é a constante estrutural do desenho atual.

**2. Concentração por página.** Em todas as execuções, de 2 a 3 hits do
top-10 vêm da **mesma página impressa**. Em C, os ranks 1, 3 e 5 são todos
da p.17 do mesmo livro.

Verifiquei a hipótese de duplicata e ela é **falsa**: em nenhuma execução há
`text_hash` ou `chunk_id` repetido. São chunks distintos de uma mesma
página, o que é legítimo. O que o dado mostra é ausência de teto por
fonte/página — o orçamento pode ser gasto em trechos contíguos de uma página
em vez de em fontes diversas.

Teto por fonte/página foi explicitamente excluído do escopo de ajuste na
Fase 7. Fica **registrado como observação**, sem proposta.

### Sinal fraco, registrado como tal

Das quatro evidências `ANSWER_KEY` que entraram em contexto nos pilotos com
JSON, **apenas uma foi citada** pelo modelo (A, `E3`). Em B e C o gabarito
entrou no contexto e foi ignorado na resposta.

Isso sugere que o próprio modelo despriorriza gabarito quando há prosa
didática disponível. **n=4. Não é base para nada.** Registrado para que uma
bateria futura possa confirmar ou derrubar.

## Observação pedagógica, sem nota atribuída

A qualidade pedagógica das respostas **não foi avaliada**. Avaliá-la é
julgamento curricular, e o princípio do projeto desde a Fase 4 é que esse
julgamento é humano. Nenhuma nota foi registrada, nem preliminarmente.

Registro apenas um contraste factual, para a avaliação humana considerar:
a resposta de **D** contém exemplo resolvido (`N₂ + 3 H₂ → 2 NH₃`), ordem de
execução e convenção de coeficientes; a de **AM** definiu molécula como
"entidade formada por átomos, cuja quantidade e variedade determinam sua
composição", sem mencionar ligação covalente. São observações sobre o
texto, não notas.

## Dívida de observabilidade (repetida de P1/P2)

O relatório grava apenas os marcadores **já normalizados**. Deveria gravar
`raw_used_evidence` e `normalized_used_evidence` lado a lado. Sem isso não
se sabe, a partir do artefato, se a normalização chegou a atuar numa
execução. Não implementado.
