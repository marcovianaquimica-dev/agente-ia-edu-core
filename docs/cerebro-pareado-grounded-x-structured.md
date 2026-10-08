# CÉREBRO — experimento pareado: GroundedAnswerer × StructuredAnswerer

**Seis casos previamente conhecidos. Não é validação do
`StructuredAnswerer`.**

Desenho: **uma** recuperação por pergunta, o **mesmo** `BuiltContext`
para os dois caminhos. Nenhuma alteração de retrieval, corpus, política,
prompt ou parâmetro durante o experimento. Nenhuma calibração olhando
resultado. Nenhuma pergunta acrescentada depois de ver os primeiros
resultados. Nenhum comportamento corrigido durante as seis execuções.

Custo: **US$ 0,0103** (grounded 0,00397 + structured 0,00633).

## Entrega

| | rótulo prévio | GroundedAnswerer | StructuredAnswerer |
|---|---|---|---|
| PA | `MISATTRIBUTED` | entrega | **entrega** |
| PB | `MISATTRIBUTED` | entrega | **entrega** |
| PC | `MISATTRIBUTED` | entrega | **entrega** |
| PD | `MISATTRIBUTED` | entrega | **entrega** |
| N2 | `UNSUPPORTED_EXTERNAL` | entrega | **BLOQUEIA** (`STRUCTURED_UNVERIFIED_CLAIM`) |
| N8 | `DERIVED` | **BLOQUEIA** (`EVIDENCE_DECLARED_INSUFFICIENT`) | **BLOQUEIA** (idem) |

## Afirmações, spans e verificação

| | F/M/C | spans | `VERIFIED` | `NOT_FOUND` | `CASE_MISMATCH` | violações | evidências usadas |
|---|---|---:|---:|---:|---:|---:|---|
| PA | 5/0/0 | 9 | **9** | 0 | 0 | 0 | E1, E3 |
| PB | 3/0/0 | 7 | **7** | 0 | 0 | 0 | E3, E4, E5 |
| PC | 5/0/0 | 6 | **6** | 0 | 0 | 0 | E1, E3, E4 |
| PD | 3/0/0 | 8 | **8** | 0 | 0 | 0 | E1, E2, E5 |
| N2 | 3/0/0 | 3 | 2 | **1** | 0 | 0 | E1, E3 |
| N8 | 0/1/0 | 0 | — | — | 0 | 0 | — |

**33 spans declarados, 32 verificados, 1 não encontrado, zero
`SPAN_CASE_MISMATCH`, zero violações de contrato.**

## Os quatro `MISATTRIBUTED` — categoria 1 nos quatro

A pergunta era distinguir: (1) corrigiu a atribuição e respondeu;
(2) removeu a afirmação; (3) bloqueou por não sustentar; (4) continuou
atribuindo mal.

**Os quatro são categoria (1).** Nenhum bloqueio, nenhuma afirmação
removida, nenhum span inválido.

O caso mais claro é **PC**. O erro histórico era atribuir a definição de
grupos e períodos a `[E3][E4]`, que tratam da evolução histórica da
classificação. No caminho estruturado:

> claim[1] "Ela é formada por linhas verticais chamadas grupos e linhas
> horizontais chamadas períodos."
> ← **E1**, span `"Na tabela periódica atual, as linhas verticais são
> chamadas grupos, e as linhas…"`

A afirmação migrou para a evidência que de fato a contém. O mesmo padrão
em PB, onde a definição de Brønsted-Lowry — antes atribuída a uma
evidência que só anunciava a teoria — agora ancora em `E5` com o span
que a define, e em `E4` com o span que caracteriza as bases como
receptoras.

**Ressalva obrigatória:** span verificado prova que o trecho **existe**
naquele chunk. Não prova que ele **sustenta** a afirmação. Que as 30
atribuições destes quatro casos sejam *apropriadas* é leitura minha, não
resultado da verificação. Só adjudicação humana decide isso.

## N2 — bloqueou, e pelo motivo certo

**O "molho de salada" não reapareceu.** O exemplo desta vez foi "água e
óleo de cozinha no mesmo recipiente". A não-determinicidade do provider
já medida mudou o texto.

**Mas o padrão estrutural é idêntico**, e foi pego:

| claim | span | status |
|---|---|---|
| [0] polar/apolar | E1 | `SPAN_VERIFIED` |
| [1] mistura heterogênea, duas fases | E3 | `SPAN_VERIFIED` |
| **[2] "exemplo do dia a dia… duas camadas"** | **E1** | **`SPAN_NOT_FOUND`** |

O modelo **tentou** sustentar o exemplo — declarou um span de `E1` sobre
funil de separação — e o trecho não estava lá. A afirmação ficou não
verificada e a resposta inteira, não entregável.

Pela mesma taxonomia: **categoria (3)** — tentou sustentar, não
conseguiu, bloqueou. Não removeu a afirmação nem a entregou sem suporte.

**O `GroundedAnswerer`, no mesmo contexto, entregou.** É a diferença que
o experimento se propôs a medir.

## N8 — os dois recusaram, e o experimento não testou o que queria

`SUFFICIENCY_DENIED` em **ambos** os caminhos. O estruturado produziu uma
única afirmação `META` e nenhuma derivação.

Fui verificar se o material estava lá:

| | |
|---|---|
| contexto desta execução | **idêntico** ao da Etapa A — mesmas 7 páginas, mesmos papéis |
| p.240 (`E5`) presente | **sim** |
| −394, −286, −891 no contexto | **sim**, nas páginas 88, 205, 233 e 240 |

**Os dados estavam lá e o modelo recusou mesmo assim — nos dois
caminhos.** Como o contexto foi o mesmo e o `GroundedAnswerer` recusou
igualmente, isto **não é efeito do contrato estruturado**: é variação do
modelo, e o desenho pareado é exatamente o que permite afirmar isso.

**Consequência honesta: o experimento NÃO confirmou que uma derivação
legítima chega a `DERIVATION_VERIFIED` numa chamada real.** Nenhuma
derivação foi produzida em nenhuma das seis.

Isso está verificado em **teste unitário** com os números reais de N8 —
`(-394 + 2*(-286)) - (-891) = -75`, `DERIVATION_VERIFIED`, insumos como
spans — mas não numa chamada paga.

**Não vou repetir N8 para obter um resultado melhor.** Repetir até sair o
número desejado é selecionar pelo resultado, e é o oposto do que estas
medições existem para fazer.

## Tamanho e fluência

| | chars G | chars S | palavras G | palavras S | pal/frase G | pal/frase S |
|---|---:|---:|---:|---:|---:|---:|
| PA | 633 | 662 | 100 | 102 | 25,0 | 20,4 |
| PB | 453 | 430 | 72 | 74 | 24,0 | 24,7 |
| PC | 439 | 504 | 65 | 80 | 16,2 | 16,0 |
| PD | 622 | 642 | 109 | 105 | 18,2 | 21,0 |
| N2 | 347 | 406 | 69 | 78 | 23,0 | 26,0 |

**A perda de fluência que eu temia não apareceu.** Tamanho dentro de
±15%, palavras por frase equivalentes. O texto montado a partir das
afirmações lê como texto corrido — nas cinco entregáveis, inclusive na
que foi bloqueada depois.

## Custo e latência — minha estimativa estava errada

| | GroundedAnswerer | StructuredAnswerer | razão |
|---|---:|---:|---|
| tokens de saída (média) | 237 | **844** | **3,6×** |
| custo total (6) | US$ 0,00397 | US$ 0,00633 | **1,6×** |
| latência (média) | 3.022 ms | 7.168 ms | **2,4×** |

**Eu estimei +16% de custo no documento de desenho. O medido é +59%.**
Errei a projeção de saída: supus +80% de tokens, e foram +256%. O custo
do bookkeeping — span literal e papel para cada evidência — é muito maior
do que eu projetei.

A entrada quase não muda (+5%, só o prompt maior). Quem paga é a saída.

## Caixa — a medição que o portão pedia

**Zero `SPAN_CASE_MISMATCH` em 33 spans.** Não há pressão empírica para
relaxar a sensibilidade a caixa. A decisão de mantê-la sensível não
custou nada nesta amostra.

## O que isto é, e o que não é

**É** um experimento pareado sobre seis casos previamente conhecidos, com
contexto controlado.

**Não é** validação do `StructuredAnswerer`. Seis casos escolhidos
justamente por terem rótulo não são população. Os quatro
`MISATTRIBUTED` "corrigidos" foram julgados por mim lendo os spans, não
por adjudicação. E a derivação — o caso que mais importava confirmar —
não chegou a ser exercitada.

## O que faria sentido a seguir, sem executar

1. **Adjudicação humana dos 30 spans verificados** dos quatro casos
   corrigidos. A verificação prova existência; só o humano decide
   aptidão. Posso gerar o pacote cego no mesmo formato dos 9.
2. **Reavaliar `V-N8`** sob o contrato de contribuição complementar, como
   o adendo registrou.
3. **Decidir sobre o custo**: +59% e 2,4× de latência são números reais
   agora, não estimativa. Se forem aceitáveis, a próxima pergunta é onde
   o caminho estruturado roda — em tudo, ou só onde a fidelidade importa.
