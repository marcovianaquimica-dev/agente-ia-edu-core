# CÉREBRO — adjudicação dos 30 pares afirmação → trecho

Integridade validada **antes** de olhar número: cabeçalhos inalterados,
30 IDs, afirmação/trecho/evidência não alterados, 30 de 30 preenchidos,
classes válidas. Nenhuma falha.

A pergunta adjudicada foi **"o trecho sustenta a afirmação?"** — não "o
trecho existe", que o código já verifica. É exatamente a lacuna que a
âncora por span não fecha sozinha.

## Resultado

| classe | n |
|---|---:|
| `SUSTENTA_INTEGRALMENTE` | 9 |
| `SUSTENTA_PARCIALMENTE` | 21 |
| `NAO_SUSTENTA` | **0** |
| `CONTRADIZ` | **0** |

**30 de 30 sustentam.** Nenhum span verificado pelo código foi julgado
irrelevante pelo humano.

## A complementaridade funcionou como o contrato previu

Esta é a parte que importa mais que o total.

| padrão de suporte da afirmação | n |
|---|---:|
| parciais que **se somam** | 8 |
| só integral | 6 |
| integral + parcial | 2 |
| **um parcial sozinho** | **nenhuma** |

As 21 classificações parciais **não são suporte fraco**: são as partes de
uma afirmação distribuídas entre evidências, que é precisamente o que a
Decisão 1 autorizou. E nenhuma afirmação ficou apoiada num único parcial
— complementaridade pressupõe que as partes se somem, e um parcial
sozinho não soma com ninguém.

As notas do adjudicador mostram o encaixe explicitamente:

> **PA claim[3]** — *"Sustenta a eletrosfera como região de grande espaço
> vazio; o núcleo positivo depende de outro trecho"* + *"Sustenta apenas
> a existência do núcleo central de carga positiva"*

> **PA claim[2]** — *"Sustenta o ano e a proposição de novo modelo, mas
> não sozinho a base experimental"* + *"Sustenta a base experimental com
> partículas alfa e lâmina de ouro, mas não sozinho o ano"*

> **PB claim[1]** — três trechos: transferência de prótons, ácido como
> doador, base como receptora.

Duas metades que se encaixam, declaradas separadamente, cada uma
ancorada na evidência que de fato a contém.

## Diferença entre os casos

| | integrais |
|---|---|
| PC | **6/6 (100%)** |
| PA | 2/9 (22%) |
| PD | 1/8 (12%) |
| PB | **0/7 (0%)** |

PC produziu afirmações curtas que casam uma a uma com o texto. PB
produziu afirmações compostas — Arrhenius e Brønsted-Lowry, ácido e base,
pH e concentração — que exigem montagem. Nenhum dos dois é defeito; são
granularidades diferentes de afirmação, escolhidas pelo modelo.

Vale registrar que **a granularidade da afirmação é decisão do modelo** e
não está no contrato. Afirmação mais larga gera mais parciais; mais
estreita, mais integrais.

## O que isto estabelece sobre a alternativa B

Nos quatro casos, há agora um **antes e depois na mesma pergunta**:

| | caminho antigo | caminho estruturado |
|---|---|---|
| PA PB PC PD | julgados `MISATTRIBUTED` | 30/30 spans sustentam |

O `GroundedAnswerer` atribuiu mal nas quatro. O `StructuredAnswerer`,
sobre o mesmo contexto, produziu atribuições que um humano confirma.
**Os +59% de custo compram fidelidade verificada nestes quatro casos.**

## O que isto NÃO estabelece

**Não mede taxa de erro.** Estes 30 spans vêm de 4 perguntas em que o
caminho estruturado funcionou. Não há aqui nenhum caso em que ele
falhou, porque não procuramos por eles — os 4 foram escolhidos por
terem rótulo anterior de problema, não por amostragem.

**Um adjudicador, uma rodada.** Sem segunda opinião e sem medida de
concordância.

**Não valida o mecanismo contra os vetores de fuga.** Span genérico,
`META` abusivo e derivação não mecanizável continuam abertos e não
apareceram nesta amostra — o que não é evidência de que não ocorram.

**Não diz nada sobre derivação.** Nenhuma foi produzida nas seis
execuções pareadas.

## Conjunto de regressão

Os 30 pares passam a ser **regressão**, não Calibration Set: se uma
mudança futura fizer algum deles deixar de ser produzido ou verificado,
é regressão a investigar. Não servem para calibrar limiar, escolher
parâmetro nem medir população.
