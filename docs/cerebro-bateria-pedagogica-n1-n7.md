# CÉREBRO — bateria pedagógica N1–N7, sistema congelado

Sete execuções reais na ordem pré-registrada, N1 e N2 consecutivas.
**Zero alteração de código, prompt, retrieval, contexto, política ou
parâmetro** — antes, durante ou depois. Textos conferidos linha a linha
contra [a proposta](cerebro-proxima-bateria-pedagogica.md).

Nenhuma correção foi feita entre as execuções, mesmo diante de problema
observado.

**Nenhuma nota pedagógica foi atribuída.** As sete respostas ficam
preservadas para avaliação humana.

## Condições — idênticas nas sete

| | |
|---|---|
| fingerprint | `8471ea331d464904` (único) |
| cobertura | 5.911/5.911, 0 faltando, 0 obsoletos |
| `candidate_cap` | 2000, atingido nas sete |
| filtrados `EDITORIAL_ROLE` | 239 nas sete |
| degradado | `False` nas sete |
| política / normalizador / detector | v1 / v1 / v1 |

**Custo total: US$ 0,00446452.**

## As sete respostas públicas

Exatamente como um aluno as receberia.

**N1 — Por que o óleo não se mistura com a água?**

> O óleo não se mistura com a água porque as moléculas de água são
> polares, enquanto as moléculas de óleo são apolares. Substâncias
> polares tendem a interagir com outras polares, e a diferença de
> polaridade entre água e óleo impede que se dissolvam satisfatoriamente,
> formando uma mistura heterogênea.

**N2 — Não entendi nada… Pode explicar de um jeito bem simples, com um
exemplo do dia a dia?**

> O óleo não se mistura com a água porque as moléculas de água são
> polares, enquanto as moléculas do óleo são apolares. Por isso, formam
> uma mistura heterogênea, com uma camada de água e outra de óleo, em vez
> de uma única fase. Um exemplo do dia a dia é o molho de salada: depois
> de algum tempo parado, o óleo fica separado da água ou do vinagre. Como
> as camadas ficam separadas, é possível retirar primeiro a água e depois
> o óleo usando um funil de separação.

**N3 — Como se escreve a fórmula de um composto iônico… passo a passo.**

> 1. Identifique o cátion e o ânion e determine suas cargas.
> 2. Escreva o símbolo do cátion à esquerda e o do ânion à direita.
> 3. Escolha os índices para que a quantidade total de cargas positivas
>    seja igual à quantidade total de cargas negativas, mantendo a
>    neutralidade elétrica.
> 4. O índice do cátion corresponde ao valor numérico da carga do ânion, e
>    o índice do ânion corresponde ao valor numérico da carga do cátion,
>    omitindo o índice 1.
> 5. Se as cargas tiverem o mesmo valor numérico, não são escritos
>    índices.
> 6. A fórmula deve representar a menor proporção inteira entre os íons.
>
> Exemplo: para Aℓ3+ e O2−, escreva primeiro Aℓ à esquerda e O à direita.
> Como a carga do ânion é 2 e a do cátion é 3, use os índices 2 para Aℓ e
> 3 para O: Aℓ2O3. Assim, há duas cargas Aℓ3+ e três cargas O2−,
> totalizando cargas positivas e negativas iguais.

**N4 — Quando uma vela queima… a matéria dela foi destruída?**

> Não. Ao queimar, a matéria da vela participa de uma reação de combustão
> e se transforma em outras substâncias, como dióxido de carbono e água;
> em combustões incompletas, também podem ser formados monóxido de
> carbono e fuligem. Portanto, ela não é destruída, mas transformada.

**N5 — Qual a relação entre o tamanho do átomo e a facilidade dele perder
elétrons?**

> Quanto maior o átomo, mais distantes do núcleo ficam os elétrons mais
> energéticos, diminuindo a força de atração entre o núcleo e o elétron.
> Assim, é necessária menos energia para retirar um elétron, tornando
> mais fácil sua perda.

**N6 — Por que o bicarbonato de sódio é usado para aliviar a azia?**

> O bicarbonato de sódio é usado para aliviar a azia porque tem efeito
> alcalinizante e neutraliza parte do ácido presente no estômago,
> reduzindo temporariamente a sensação de queimação.

**N7 — Como funciona uma pilha?**

> Uma pilha é um dispositivo formado por dois eletrodos e eletrólitos que
> produz energia elétrica por meio de uma reação espontânea de
> oxirredução: uma espécie química se oxida, perdendo elétrons, enquanto
> outra se reduz, recebendo esses elétrons. Os elétrons fluem pelo fio
> condutor externo entre os eletrodos, enquanto os íons se movimentam
> pelas soluções eletrolíticas, fechando o circuito interno. Na pilha de
> Daniell, por exemplo, o zinco é o ânodo, onde ocorre oxidação, e o cobre
> é o cátodo, onde ocorre redução; uma ponte salina permite a migração de
> íons entre as soluções. A diferença de potencial entre os eletrodos
> promove espontaneamente o movimento dos elétrons e dos íons, permitindo
> que a pilha forneça energia para o funcionamento de equipamentos.

---

# Diagnóstico ADMIN

## Tabela consolidada

| | `CONT`/`AK`/`UNK` top-10 | evid. | `AK` ctx | `AK` citadas | citadas | chars | excl. |
|---|---|---:|---:|---:|---:|---:|---:|
| N1 | 8/2/0 | 7 | 2 | **1** | 2 | 11.537 | 3 |
| N2 | 8/2/0 | 5 | 1 | **1** | 2 | 10.229 | 5 |
| N3 | 8/0/2 | 4 | 0 | 0 | 3 | 11.400 | 6 |
| N4 | 8/2/0 | 5 | 1 | 0 | 2 | 11.982 | 5 |
| N5 | 8/2/0 | 5 | 2 | 0 | 1 | 11.818 | 5 |
| N6 | 8/2/0 | 4 | 1 | 0 | 1 | 10.763 | 6 |
| N7 | 9/0/1 | 4 | 0 | 0 | 3 | 11.279 | 6 |

| | score máx → mín | fontes | emb | ret | ger | total | tokens | US$ |
|---|---|---:|---:|---:|---:|---:|---|---:|
| N1 | 0,6264 → 0,5077 | 3 | 2.356 | 724 | 2.995 | 6.088 | 3.472/141 | 0,00060568 |
| N2 | 0,6121 → 0,5159 | 2 | 346 | 332 | 2.982 | 3.676 | 3.032/194 | 0,00057196 |
| N3 | **0,7005** → 0,6216 | 3 | 343 | 289 | 5.516 | 6.153 | 3.217/461 | 0,00075983 |
| N4 | **0,5044** → 0,4387 | 3 | 811 | 435 | 3.532 | 4.782 | 3.718/210 | 0,00068428 |
| N5 | 0,5657 → 0,5343 | 2 | 465 | 218 | 3.564 | 4.251 | 3.439/194 | 0,00063265 |
| N6 | 0,5161 → 0,4581 | 3 | 520 | 280 | 2.619 | 3.423 | 3.137/128 | 0,00054775 |
| N7 | 0,6642 → 0,6133 | 3 | 378 | 250 | 3.765 | 4.399 | 3.263/288 | 0,00066237 |

**Os dois eixos, nas sete:** `grounding = GROUNDED`,
`sufficiency = SUFFICIENCY_AFFIRMED`, `deliverable = True`,
`delivery_block_reason = None`, `needs_human_review = False`, zero
marcador inválido, zero artefato de sanitização.

**`used_evidence` veio com colchetes em 7 de 7.** Com Etapa A e as duas
verificações, são **12 de 12** chamadas reais desde que o bruto passou a
ser preservado.

## 1. N1 × N2 — adapta, mas o experimento saiu confundido

**A resposta mudou, e na direção pedida:**

| | N1 | N2 |
|---|---:|---:|
| palavras | 48 | **88** |
| frases | 2 | **4** |
| letras por palavra | 5,15 | **4,09** |
| exemplo concreto | não | **sim** |

**Mas a recuperação mudou junto, e isso não estava previsto:**

| | |
|---|---|
| interseção do top-10 | **5 de 10** |
| `query_fingerprint` igual | **não** |
| evidências no contexto | 7 vs 5, **apenas 2 em comum** |

O desenho supunha que N1 e N2 diferissem só no pedido de registro. Na
prática, o ruído pedagógico entrou no texto embeddado e **mudou metade do
material recuperado**. O par, como construído, não isola o que se
propunha a isolar.

### O achado que importa mais

> "Um exemplo do dia a dia é o molho de salada… **[E1]**"

**"salada", "molho", "tempero", "maionese", "camada" e "sobrenadante" não
aparecem em nenhum chunk enviado** — nem a N1, nem a N2. `[E1]` é o mesmo
chunk nas duas execuções (p.484) e contém "funil", mas não contém
"salada" nem "molho".

**O exemplo veio do conhecimento próprio do modelo, e recebeu uma citação
que não o sustenta.**

O marcador é válido, a evidência existe, o grounding diz `GROUNDED` — e
está certo dentro do que ele promete verificar. **O que o sistema não
verifica é se a evidência citada sustenta a frase.** Esse é o limite da
validação determinística, agora com caso concreto.

Vale registrar o outro lado: o exemplo é quimicamente correto e
pedagogicamente bom. O problema não é a qualidade, é a procedência
declarada.

## 2. N4 — corrige explicitamente

> **"Não.** Ao queimar, a matéria da vela… Portanto, ela não é destruída,
> mas transformada."

Refuta a premissa na primeira palavra e fecha reafirmando a correção.
**Nada no prompt manda corrigir concepção equivocada** — o comportamento
foi espontâneo.

Atribuição limpa: "dióxido de carbono" não aparece literalmente, mas
"gás carbônico", "CO₂" e "dióxido" estão nas evidências citadas. Minha
primeira sondagem marcou isso como ausência — **era falso positivo do
método por string**, e a verificação com variantes o derrubou.

N4 teve os **menores scores da bateria** (0,5044 no topo) e ainda assim a
melhor correção.

## 3. N7 — não reconhece a ambiguidade

Escolheu o sentido eletroquímico **em silêncio**. Não diz que "pilha"
pode significar outra coisa, não pergunta de que tipo, não sinaliza
subespecificação.

A recuperação colapsou a ambiguidade antes da geração: 9 de 10 hits
`CONTENT` de eletroquímica, scores 0,66–0,61. **O modelo nunca viu um
sentido alternativo.**

A resposta é boa química. O modo de falha é pedagógico e invisível ao
sistema: um tutor que responde com confiança à pergunta que ele escolheu
entender.

## 4. Recuperação, geração, ou inseparável

| | origem do problema |
|---|---|
| N2 — exemplo fabricado | **geração** — o material não estava no contexto; o modelo o supriu |
| N2 — metade do material trocado | **recuperação** — o texto da pergunta mudou o embedding |
| N7 — ambiguidade colapsada | **inseparável** — a recuperação não ofereceu outro sentido, então não dá para saber se a geração teria sinalizado |
| N1 — citação principal é gabarito | **recuperação** — o chunk de gabarito foi rank 1 com o maior score |
| N3, N4, N5, N6 | nenhum problema de atribuição detectado |

## 5. `ANSWER_KEY` teve influência material em duas

| | |
|---|---|
| N1 | citou `[E1]` = `ANSWER_KEY` p.484 — **a afirmação central** (polar/apolar) |
| N2 | citou o **mesmo** chunk `6f9a0d4e`, p.484 |
| N3–N7 | **nenhuma** citação de `ANSWER_KEY`, embora 4 delas tivessem gabarito no contexto |

Em 5 das 7, o gabarito entrou no contexto e **foi ignorado** pela geração.
A influência material concentrou-se numa única pergunta e num único
chunk.

Isso é consistente com o observado nos pilotos: `ANSWER_KEY` que entra no
contexto raramente é citado. **Não proponho alteração de política** — n
continua pequeno e a política não foi tocada.

## 6. Comportamentos novos

- **Zero `SUFFICIENCY_DENIED` e zero `UNDECLARED`** nas sete. Combinado
  com a Etapa A, o sinal só negou onde o corpus de fato não cobria — 3
  negações em 18 chamadas reais, todas corretas.
- **Zero bloqueios, zero artefatos, zero marcadores inválidos.**
- **N3 produziu a primeira resposta estruturada em lista numerada
  multilinha.** A sanitização preservou as quebras de linha e os índices
  (`Aℓ2O3`) sem artefato — caso que nenhum teste tinha coberto com texto
  real.
- N3 teve o maior score da bateria (0,7005) e a maior saída (461 tokens).

## Dívidas e riscos registrados

1. **Citação válida que não sustenta a frase** (N2) — o grounding não
   cobre isso, por desenho. Agora com caso real.
2. **O par N1/N2 não isola registro** — qualquer repetição precisa
   controlar a recuperação, ou aceitar que mede as duas coisas juntas.
3. **Ambiguidade não é detectada em nenhuma camada** (N7).
4. **Triagem por string gera falso positivo** (N4) — confirmado pela
   segunda vez nesta fase.

Nada disso foi corrigido. Nenhum mecanismo foi implementado.
