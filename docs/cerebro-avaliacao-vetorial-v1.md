# CÉREBRO — Avaliação vetorial, baseline oficial e adjudicação

Fechamento da avaliação da Fase 6. Nenhum tuning foi feito, nenhuma constante
alterada, nenhuma busca refeita.

Corpus, espaço de embedding e procedimento de reconstrução:
[cerebro-corpus-vetorial-v1.md](cerebro-corpus-vetorial-v1.md).

## Configuração da medição

| | |
|---|---|
| conjunto | `VECTOR_EVALUATION_SET_V1`, congelado em `1d5ac9e` |
| qrels | `VECTOR_QRELS_V1` (133 julgamentos) e `VECTOR_QRELS_V2` (165) |
| `retrieval_purpose` | **LEARN** |
| régua | R1–R7, visão deduplicada por conteúdo |
| ranking | o salvo na primeira execução — **não** refeito |

**Por que LEARN.** Medido antes de escolher: com propósito ausente,
`PRACTICE` ou `ASSESS`, **29% dos chunks julgados relevantes são barrados por
política**. Medir assim confundiria qualidade de recuperação com política
pedagógica e limitaria o Recall@10 por uma razão sem relação com embedding.
Em `LEARN` e `AUTHOR`, 0% é barrado.

## Baseline oficial

| métrica | V1 bruto | V1 dedup | V2 bruto | **V2 dedup** |
|---|---:|---:|---:|---:|
| P@5 | 0,4400 | 0,4400 | 0,6200 | **0,6600** |
| P@10 | 0,2700 | 0,2700 | 0,5900 | **0,5900** |
| Recall@10 | 0,2308 | 0,2308 | 0,3720 | **0,3720** |
| MRR | 0,7750 | 0,7750 | 0,8750 | **0,8750** |
| nDCG@10 | 0,3754 | 0,3754 | 0,5001 | **0,5027** |

O **V1 permanece o baseline histórico congelado e reproduzível**. O V2 é a
régua corrigida.

Bruto e deduplicado coincidem no V1 porque o conteúdo triplicado tem grau 0:
a duplicata não inflava nada, apenas ocupava três vagas com material
irrelevante. A política está no lugar antes de ser necessária.

### O salto de 0,2700 para 0,5900 não é melhoria do sistema

**Mesmo ranking, mesma busca, mesmos 5.911 embeddings, mesmas constantes.**
Nenhuma consulta foi reexecutada, nenhum vetor regerado. A única coisa que
mudou foi a régua: 69 dos 73 resultados contados como falso positivo nunca
tinham sido julgados.

A causa estava declarada desde o congelamento da V1: o pool foi construído
com busca **lexical** e, por construção, não contém o que é semanticamente
próximo e lexicalmente distante — que é exatamente o que a perna vetorial
existe para achar.

Qualquer comparação futura de **sistema** deve usar o V2. O V1 serve para
reproduzir o número histórico, não para avaliar qualidade.

## Decomposição — 98 vagas no top-10 deduplicado

| | vagas | fração |
|---|---:|---:|
| acertos já reconhecidos pelo V1 | 27 | 0,2755 |
| **eram ausência de julgamento** | **32** | **+0,3265** |
| **permanecem erro real de recuperação** | **39** | **0,3980** |

Dos 69 não julgados, o humano confirmou **35 como irrelevantes**. Somados aos
4 que já eram grau 0 no pool, são **39 erros reais** — 40% das vagas. A perna
vetorial não é tão boa quanto o V2 sozinho sugeriria, nem tão ruim quanto o
V1 dizia.

## Por consulta — V2 deduplicado

| consulta | depth | P@5 | P@10 | Recall@10 | MRR | nDCG@10 |
|---|---:|---:|---:|---:|---:|---:|
| relação entre massa e nº de partículas | 10 | 1,00 | 1,00 | 0,435 | 1,000 | 0,632 |
| preparar solução partindo de outra concentrada | 10 | 1,00 | 1,00 | 0,556 | 1,000 | 0,689 |
| por que adicionar água deixa a solução mais fraca | 10 | 1,00 | 0,70 | 0,280 | 1,000 | 0,577 |
| quantas partículas existem numa amostra | 10 | 0,80 | 0,70 | 0,389 | 1,000 | 0,581 |
| habilidade sobre transformações e conservações | 10 | 0,80 | 0,70 | 0,700 | 1,000 | 0,801 |
| como saber qual substância acaba antes | 10 | 0,40 | 0,50 | 0,217 | 0,250 | 0,147 |
| fator limitante para espécies aquáticas | **8** | 0,60 | 0,50 | 0,333 | 1,000 | 0,470 |
| fotossíntese nas plantas *(controle negativo)* | 10 | 0,40 | 0,30 | 0,429 | 1,000 | 0,652 |
| o que sobra quando um reagente acaba primeiro | 10 | 0,40 | 0,30 | 0,200 | 1,000 | 0,368 |
| **reagente limitante** | 10 | **0,20** | **0,20** | **0,182** | 0,250 | **0,110** |

`effective_depth = 8` numa consulta, por R5 — declarado, não preenchido com
invenção.

## Regressão a tratar na recuperação híbrida — `reagente limitante`

**Não melhorou nada com a adjudicação.** O humano confirmou irrelevantes
todos os 8 candidatos não julgados. P@10 = 0,20 e nDCG@10 = 0,110 são o pior
resultado do conjunto, sob as duas réguas.

A causa é observável no top-10: **13 dos 16 candidatos** desta consulta e da
consulta vizinha (`o que sobra quando um reagente acaba primeiro`) são
resolução de **equilíbrio químico**. O embedding aproxima
"reagente / produto / deslocamento" de "reagente que acaba", que é outro
conceito.

É exatamente o perfil inverso do da perna lexical, que é forte em termo
técnico curto — no baseline pós-Editorial Gate da Fase 5.1, `reagente
limitante` teve 7/10. **A comparação direta entre as duas pernas não está
disponível**: os números lexicais foram julgados sob outra régua, não sob
estes qrels. Produzir essa comparação de forma honesta é trabalho da Fase 7,
e `reagente limitante` é o caso de teste que ela precisa resolver.

## `ANSWER_KEY` — registrado, política não alterada

| régua | `ANSWER_KEY` | demais vagas |
|---|---:|---:|
| V1 | 6/30 (20%) | 21/68 (31%) |
| **V2** | **11/30 (37%)** | **48/68 (71%)** |

Gabarito ocupa 30 das 98 vagas e acerta pouco mais da metade da taxa do
restante. O contraste **ficou mais nítido com a régua corrigida**: sob o V1 os
dois lados eram ruins; sob o V2 a diferença é de 34 pontos.

Fechar `ANSWER_KEY` em `LEARN` melhoraria a métrica. **Não foi feito.**
Resolução comentada tem valor de aprendizagem real, e pelo princípio
permanente da Fase 4 a validação pedagógica é humana. O número fica
registrado; a decisão é de quem ensina.

## Direitos

| régua | `COMMERCIAL_REFERENCE` | `OFFICIAL_PUBLIC` |
|---|---:|---:|
| V1 | 26/95 (27%) | 1/3 (33%) |
| V2 | 56/95 (59%) | 3/3 (100%) |

Nenhum literal de obra comercial apareceu em resultado público, pacote de
adjudicação, planilha ou relatório. Os dois itens da BNCC levaram texto
literal porque a citação é permitida.

## Par de ambiguidade — resolvido

| | |
|---|---|
| sobreposição no top-10 | **0** (tolerado: 2) |
| relevantes compartilhados nos qrels V2 | **0** |

A adjudicação humana **confirmou** a separação de sentidos: nenhum documento
serve aos dois significados, e o sistema não os confundiu. Era a perda que a
Fase 5.1 expôs no rank 10 e a principal motivação da perna vetorial. O lado
ecológico subiu de P@10 0,10 para 0,50.

## Controle negativo

`fotossíntese nas plantas`: P@10 inalterado em 0,30 entre V1 e V2 — os 4
candidatos não julgados desta consulta foram todos confirmados irrelevantes.
Confirma que é consulta de **baixa densidade**, não de densidade zero, como
declarado no congelamento. Distribuição de scores registrada; **nenhum limiar
foi proposto**.

## Adjudicação — procedimento e proveniência

67 julgamentos humanos, um por `(query, text_hash)` após a deduplicação R7.

**Cegamento:** a planilha não trazia rank, score, similaridade, posição,
origem lexical/vetorial nem qualquer sugestão de grau, e a ordem das linhas
dentro de cada consulta foi embaralhada deterministicamente — sem isso a
posição entregaria o ranking.

**A IA não julgou.** Ela havia registrado uma hipótese preliminar em separado
(44→0, 18→1, 7→2, projeção P@10 ≈ 0,52) que **não** foi usada e **não** entrou
no V2. Concordância medida depois: 49/67 (73%). A hipótese errou para baixo —
o real foi 0,5900 — o que é evidência de que o julgamento independente valeu a
pena.

Escala idêntica à V1: **2** responde diretamente · **1** aplica-se/tangencia
de forma pedagogicamente útil · **0** irrelevante. Resultado: 8 · 24 · 35.

**Append-only:** `VECTOR_QRELS_V2` **importa** o V1 em vez de copiá-lo, então
divergência por edição não é representável. `ADJUDICATION_LOG` guarda os 67
julgamentos com data e proveniência — inclusive os 35 de grau 0, que não
entram nos qrels mas precisam constar: "julgado irrelevante" e "nunca
julgado" são estados diferentes, e confundi-los foi o que distorceu a
primeira medição.

## Dívida técnica — não corrigida

**13 chunks duplicados em 11 grupos**, de duas naturezas:

- **duplicação do chunker** (corrigir): `c16370e435` 3× na p.304 (ordinais
  1121, 1144, 1145) e `0fcbbf0023` 3× nas p.207–211 — o mesmo texto emitido
  mais de uma vez a partir da mesma região;
- **repetição da própria obra** (não corrigir): nove grupos, com quatro
  chunks consecutivos repetidos entre a p.9 e a p.445 do mesmo livro. O
  original reproduz o bloco; apagar uma ocorrência quebraria a
  rastreabilidade até a página. A regra R1 já trata isso na avaliação.

## O que NÃO foi tocado

Embeddings · modelo · `candidate_cap` · thresholds · BM25F · chunking ·
política editorial · `VECTOR_EVALUATION_SET_V1` · `VECTOR_CALIBRATION_SET_V1` ·
`VECTOR_QRELS_V1` · Evaluation Set lexical.
