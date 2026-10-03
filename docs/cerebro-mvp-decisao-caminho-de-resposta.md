# CÉREBRO MVP — decisão do caminho de resposta

Três alternativas, **só com dados medidos**. Nenhuma escolha feita aqui.

A decisão depende da adjudicação dos 30 spans, que ainda não voltou.

## O que já está medido

| | |
|---|---|
| execuções reais acumuladas | 18 + 6 pareadas = **24** |
| casos com rótulo humano | 9 (fidelidade) |
| spans declarados em chamada real | 33 — **32 verificados, 1 não encontrado** |
| `SPAN_CASE_MISMATCH` | **0 em 33** |
| violações de contrato estruturado | **0 em 6** |
| derivações produzidas em chamada real | **0** |
| custo por resposta (mediana, Grounded) | US$ 0,00065 |
| custo pareado (6 perguntas) | Grounded US$ 0,00397 · Structured US$ 0,00633 |
| saída média | Grounded 237 tokens · Structured **844** (3,6×) |
| latência média | Grounded 3.022 ms · Structured **7.168 ms** (2,4×) |

---

## A — `GroundedAnswerer` como caminho principal

**Segurança/fidelidade.** Verifica que o marcador existe. **Não** verifica
que a evidência sustenta a afirmação. Nos 9 adjudicados, 5 de 9 pares
tinham problema de fidelidade — 4 má atribuição, 1 externo — e o sistema
entregou todos. No pareado, entregou N2 com o exemplo sem suporte.

**Custo.** Mediana US$ 0,00065 por resposta. É a linha de base.

**Latência.** ~3,0 s de geração; total 3–11 s dominado por rede.

**Complexidade.** Já existe, 100% testado, em produção de piloto. Nenhum
código novo.

**Observabilidade.** Dois eixos (grounding, suficiência), `raw` ×
`normalized used_evidence`, bloqueio nomeado, custo e tempos reais.
**Não** mostra por que uma citação é boa ou ruim.

**Riscos não cobertos.** Citação válida que não sustenta — o modo
dominante, 4 de 9. Atribuição sem escopo: 24% das frases reais não
levam marcador. Afirmação externa com citação, como N2.

**O que mediríamos para adotar.** Nada novo: é o estado atual. A pergunta
real é se a taxa de infidelidade observada (5/9 em casos escolhidos por
suspeita) é tolerável no piloto. **Isso exige uma amostra não
enviesada**, que não temos.

---

## B — `StructuredAnswerer` como caminho principal

**Segurança/fidelidade.** Verifica que o trecho **existe no chunk
declarado**. Nos 4 casos de má atribuição, a atribuição foi corrigida
(30/30 spans verificados). Em N2, bloqueou. **Continua não provando que
o trecho sustenta** — é exatamente o que a adjudicação dos 30 vai dizer.

**Custo.** +59% medido (não os +16% que estimei). A entrada quase não
muda; a saída é 3,6×.

**Latência.** 2,4× — de 3,0 s para 7,2 s de geração. Numa interação de
tutor, 7 s é perceptível.

**Complexidade.** Módulo novo (538 linhas) + verificação de span (120) +
5 regras de normalização versionadas. Contrato de saída mais rígido:
mais pontos onde o provider pode desviar. Em 6 execuções, **zero
violações** — n pequeno.

**Observabilidade.** Muito maior: afirmação a afirmação, span bruto e
normalizado, status por par, papel declarado, motivos de não
verificação, derivação com insumos. É o que torna a fidelidade
auditável.

**Riscos não cobertos.** Três vetores de fuga abertos **por construção**,
cada um com teste de caracterização: span genérico (`"de"` passa),
conteúdo factual marcado `META`, derivação declarada não mecanizável.
E o principal: **span verificado ≠ suporte semântico**.

**O que mediríamos para adotar.** (a) a adjudicação dos 30 — se muitos
forem `NAO_SUSTENTA`, o ganho é menor do que parece; (b) a frequência
real dos três vetores de fuga numa bateria maior; (c) se a derivação
funciona em chamada real — **ainda não observada**; (d) se +59% e 2,4×
são aceitáveis no uso pretendido.

---

## C — híbrido, estruturado sob critério objetivo

**Segurança/fidelidade.** Depende inteiramente do critério de
acionamento. **E não temos critério validado.** A triagem lexical v1,
única candidata medida, teve **precisão 57% e revocação 80%** nos 9
adjudicados — e ela só pode rodar **depois** da resposta, o que a torna
inútil como gatilho *antes* de escolher o caminho.

Critérios *a priori* possíveis, nenhum medido: propósito da pergunta,
papel editorial dominante no contexto, presença de número na resposta
esperada, público (aluno × professor).

**Custo.** Entre A e B, proporcional à fração acionada. Com 1/3 das
perguntas no caminho estruturado, ~+20%.

**Latência.** Idem — e **pior no caso ruim**: se o critério rodar depois
de uma resposta do caminho A, paga-se os dois.

**Complexidade.** A maior das três: dois caminhos vivos, um roteador, e
a obrigação de manter os dois testados e coerentes. O projeto já recusou
um roteador antes, na Fase 7, por não haver sinal que separasse os
regimes — e a situação do sinal aqui é a mesma.

**Observabilidade.** Heterogênea: duas qualidades de registro conforme o
caminho. Comparar execuções passa a exigir saber qual caminho rodou.

**Riscos não cobertos.** Todos os de A nas perguntas que forem por A, e
todos os de B nas que forem por B, **mais** o risco de o critério errar
— que é o risco novo e o único que não dá para herdar de nenhum dos dois.

**O que mediríamos para adotar.** Um critério *a priori* com separação
demonstrada: sobre uma amostra rotulada, mostrar que perguntas roteadas
para B teriam mesmo fidelidade pior em A. **Isso é um estudo próprio**,
e é exatamente o tipo de investigação que a Fase 7 encerrou com
resultado negativo quando tentou rotear entre lexical e vetorial.

---

## Quadro comparativo

| | A Grounded | B Structured | C Híbrido |
|---|---|---|---|
| fidelidade verificada | marcador existe | **trecho existe no chunk citado** | varia por caminho |
| custo | linha de base | **+59%** | entre, ~+20% a 1/3 |
| latência | 3,0 s | **7,2 s** | entre; pior se encadeado |
| complexidade | nenhuma nova | módulo novo | **dois caminhos + roteador** |
| observabilidade | dois eixos | **por afirmação e por span** | heterogênea |
| risco novo | — | vetores de fuga | **erro do critério** |
| pronto para decidir? | sim | **depende dos 30** | **não — falta critério** |

## O que falta para decidir

1. **A adjudicação dos 30 spans.** Se a maioria for
   `SUSTENTA_INTEGRALMENTE` ou `SUSTENTA_PARCIALMENTE`, B ganha um
   argumento empírico forte. Se houver muitos `NAO_SUSTENTA`, B verifica
   existência e pouco mais — e o custo de +59% fica difícil de
   justificar.
2. **Uma observação real de `DERIVATION_VERIFIED`.** Hoje só existe em
   teste unitário.
3. **Decidir se +59% e 2,4× são aceitáveis** para o uso pretendido — é
   decisão de produto, não técnica.

## Uma observação sobre o escopo

As três alternativas tratam do **caminho de resposta**, que é só uma
parte do que será congelado como MVP v1. Nada aqui depende de retrieval,
corpus, chunking ou política editorial, que seguem intocados desde a
Fase 6.
