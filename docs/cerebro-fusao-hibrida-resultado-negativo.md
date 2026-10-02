# CÉREBRO — Fusão híbrida: resultado negativo

Registro formal da investigação de recuperação híbrida lexical + vetorial,
Fase 7. **Nenhuma fusão foi implementada.** A investigação terminou em
resultado negativo e ele é preservado aqui como está.

Baseline, corpus e régua: [cerebro-avaliacao-vetorial-v1.md](cerebro-avaliacao-vetorial-v1.md)
e [cerebro-corpus-vetorial-v1.md](cerebro-corpus-vetorial-v1.md).

## Conclusão

1. **RRF, Borda e CombSUM foram avaliados no Calibration e nenhum passou os
   critérios pré-registrados.**
2. **A recuperação vetorial isolada permanece o baseline.**
3. **A complementaridade lexical × vetorial existe** — e é grande —, **mas
   as estratégias simples testadas não a converteram em ganho agregado.**
4. **Roteamento / fusão condicionada permanece hipótese de evolução futura,
   não demonstrada.**

Não foi calibrado α. Não foi criado roteador. Não foi implementado
`HybridSearcher`. Os 247 candidatos do Evaluation não foram usados nem
adjudicados.

## O que foi medido

### Preparação

Rankings congelados N=20 das 18 consultas (10 Evaluation + 8 Calibration),
nas duas pernas, com `retrieval_purpose=LEARN`, R1–R7 e os mesmos filtros.
**Os vetores das consultas foram preservados** — a lição da Fase 6, onde a
ausência deles obrigou a re-embeddar para aprofundar um ranking.

Latência medida: lexical p50 **132 ms** e **zero chamada paga**; vetorial
p50 983 ms, dominada pelo embedding da consulta.

`ranked_chunks()` foi acrescentado à perna lexical, espelhando a assinatura
já existente em `VectorSearcher` — para que um futuro fundidor consuma as
duas pernas pela mesma forma sem conhecer `LexicalHit` nem `VectorHit`.

### Régua de Calibration

O `VECTOR_CALIBRATION_SET_V1` tinha sido congelado **sem qrels**. Isso
bastava para observar distribuições, mas não para escolher parâmetros: não
se otimiza métrica num conjunto sem gabarito.

276 pares `(query, text_hash)` — a união do top-20 das duas pernas —
adjudicados por humano, às cegas: sem rank, score, perna de origem ou
sugestão da IA, com a ordem embaralhada deterministicamente. Desta vez a IA
**não registrou hipótese de grau alguma**.

Resultado: **117 grau 2 · 66 grau 1 · 93 grau 0**. Os 93 zeros ficam no log,
porque "julgado irrelevante" e "nunca julgado" são estados diferentes.

A régua tem a propriedade de fechamento: qualquer fusão das duas pernas
produz resultados dentro do pool adjudicado, então trocar regra ou parâmetro
**não exige nova adjudicação**.

## Complementaridade — ela existe, e é grande

Dos 183 relevantes do Calibration, no top-10 de cada perna:

| | |
|---|---:|
| só a lexical acha | **40** |
| só a vetorial acha | **54** |
| ambas acham | **8** |
| nenhuma acha | 81 |

**Dos 102 relevantes que alguma perna acha, 94 (92%) são exclusivos de uma
só.** A concordância entre as pernas é de 8 documentos em 183.

Oráculo da união (teto de qualquer fusão): nDCG@10 = **1,0000** contra
0,6663 da melhor perna — **+50% disponível**.

## Os três métodos — e por que falharam

Critério declarado **antes** de medir: superar as duas pernas em nDCG@10
médio; nenhuma consulta abaixo da pior perna; maior nDCG@10; empate
(<0,010) resolvido por menos parâmetros.

| configuração | P@5 | P@10 | R@10 | MRR | nDCG@10 | vs melhor perna |
|---|---:|---:|---:|---:|---:|---:|
| lexical isolada | 0,5750 | 0,6000 | 0,2570 | 0,8542 | 0,4765 | — |
| **vetorial isolada** | 0,8250 | 0,7750 | 0,3409 | 0,8750 | **0,6663** | — |
| RRF k=10…100 | 0,7500 | 0,6500 | 0,2826 | 0,9375 | 0,5515 | −0,1148 |
| Borda | 0,7250 | 0,6750 | 0,2940 | 0,9375 | 0,5782 | −0,0881 |
| CombSUM min-max | 0,7000 | 0,7125 | 0,3110 | 0,9375 | 0,5969 | −0,0694 |
| oráculo @20 | 1,0000 | 1,0000 | 0,4506 | 1,0000 | 1,0000 | +0,3337 |

Captura do vão até o oráculo: **−34%, −26%, −21%**. Não capturam parte do
ganho — andam para trás.

### O arnês está correto

Fusão degenerada usando só uma perna reproduz essa perna **exatamente**
(1e-9). O resultado negativo não é bug de implementação.

### Dois mecanismos medidos

**RRF ignora o `k` neste corpus.** Amplitude 0,0000 em toda a grade
{10,20,30,60,100}. Apenas **44 de 320** aparições estão nas duas pernas. Com
pernas quase disjuntas, quase todo documento tem uma única contribuição
`1/(k+r)`, e ordenar por isso é ordenar por `r` para qualquer `k` — **RRF
degenera em intercalação de ranks**, e o parâmetro perde efeito.

**A fusão simétrica dilui a perna forte.** Ela entrega ~metade das vagas a
cada perna. Em `o que faz uma reação acontecer mais depressa`: vetorial
1,000, lexical 0,131 — razão 7,6×. O top-10 fundido pega 5 de cada; a
lexical contribui 2/5 relevantes, a vetorial 5/5. Resultado 0,581. Trocou
metade de um ranking perfeito por um ruim.

### Robustez do resultado negativo

`k` ∈ {10,20,30,60,100}: amplitude 0,0000. Profundidade N ∈ {10,15,20}:
Borda 0,5515→0,5782 · RRF 0,5482→0,5515 · CombSUM 0,5802→0,5969. Nenhuma
configuração chega perto de 0,6663. **A conclusão não depende de um
parâmetro.**

## O diagnóstico de regime — e uma correção

Uma primeira leitura sugeriu que "nas consultas de termo técnico curto a
fusão funciona", porque a média do Borda (0,6732) superava a média da melhor
**perna** (0,6617). Essa comparação era contra uma política de perna
**fixa**, não contra a melhor perna *de cada consulta* — que nas três curtas
é 0,766.

**Comparando por consulta, o Borda não supera a melhor perna em nenhuma das
8.** O achado por regime era artefato de agregação, e está corrigido.

A pergunta legítima que sobra — a fusão supera a política fixa "sempre
vetorial"? — dá **3 de 8**, com ganho substancial em apenas uma
(`oxidação e redução`, +0,205). Isso é n=1.

### Nenhum sinal pré-recuperação separa os casos

Testados sobre tokenização offline e metadados já congelados, sem qrels:

| sinal | fusão ganha | fusão perde | separa? |
|---|---|---|---|
| termos normalizados | 2, 3, 4 | 2, 4, 4, 4, 6 | não |
| tokens | 3, 4, 6 | 2, 8, 8, 8, 11 | não |
| tokens descartados | 1, 1, 2 | 0, 2, 4, 4, 7 | não |
| fração de palavras funcionais | 0,25 · 0,33 · 0,33 | 0,00 · 0,25 · 0,50 · 0,50 · 0,64 | não |
| cobertura do pool lexical | 9% · 12% · 18% | 16% · 26% · 30% · 34% · 34% | não |
| tamanho do pool lexical | 533 · 713 · 1.081 | 945 · 1.565 · 1.744 · 2.000 · 2.000 | não |

A cobertura é a que mais se aproxima, mas há sobreposição (18% ganha, 16%
perde). **Nenhum corte foi escolhido** — escolher um ponto nesse intervalo
seria ajustar limiar para maximizar métrica, com n=8.

A pergunta mais fraca — o sinal prevê qual perna vence? — também falha: a
lexical vence em 2 de 8, com cobertura de 9% e 34%, os dois extremos.

## Por que a hipótese de roteamento permanece viva, mas não demonstrada

O oráculo diz que há +50% disponível, e a complementaridade é real: 92% dos
relevantes achados são exclusivos de uma perna. O que falta não é
informação — é um critério, calculável **antes** de conhecer a relevância,
que diga qual perna confiar em cada consulta.

Com 8 consultas, 5 das quais paráfrases longas onde um índice invertido não
tem como competir, não há como demonstrar esse critério. Demonstrá-lo
exigiria mais consultas de termo técnico curto no Calibration — e isso é
mudança de conjunto congelado, que depende de decisão humana.

## O que NÃO foi tocado

`VECTOR_QRELS_V1` (133) · `VECTOR_QRELS_V2` (165) · `VECTOR_EVALUATION_SET_V1`
· `VECTOR_CALIBRATION_SET_V1` · os **247 candidatos do Evaluation**, ainda
não adjudicados · embeddings do corpus · modelo · BM25F · `candidate_cap` ·
política editorial · `ANSWER_KEY` · chunking · thresholds.
