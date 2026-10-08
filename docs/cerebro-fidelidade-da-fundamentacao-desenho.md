# CÉREBRO — fidelidade da fundamentação: taxonomia, casos e desenho

**Fase de desenho. Nada implementado.** Nenhuma alteração de retrieval,
embeddings, chunking, orçamento, `ANSWER_KEY`, prompts de resposta ou
conjuntos congelados. **Nenhuma chamada paga nova** — tudo abaixo vem dos
artefatos já salvos das 18 execuções reais.

O problema: **uma referência válida não implica que a evidência sustente a
afirmação.** O validador atual confere que o marcador resolve. Não confere
que o trecho diga o que a frase afirma.

---

## 1. Taxonomia formal de suporte

As três situações pedidas, mais duas que os dados reais obrigaram a
separar.

### As três centrais

**S1 — `DIRECTLY_SUPPORTED`**
A afirmação está contida na evidência citada, admitindo paráfrase,
flexão e reordenação. Nenhum salto inferencial.

> *N6:* "o bicarbonato… tem efeito alcalinizante e neutraliza parte do
> ácido presente no estômago" — todos os termos no chunk citado.

**S2 — `DERIVED`**
A afirmação **não** está na evidência, mas decorre dela por uma operação
declarável e checável: aritmética, composição lógica, conversão de
unidade, instanciação de uma regra geral num caso particular.

> *N8:* "−75 kJ/mol, obtida pela lei de Hess a partir das entalpias de
> combustão do carbono (−394), do hidrogênio (−286) e do metano (−891)".
> Os três insumos estão no chunk citado; o resultado não está em lugar
> algum do contexto. **Derivação legítima.**

O que distingue S2 de S3 é que os **insumos** da operação estão na
evidência e a operação é nomeável. Não é "parece razoável".

**S3 — `UNSUPPORTED_EXTERNAL`**
A afirmação não está na evidência citada **nem é derivável dela**. Veio do
conhecimento do modelo.

> *N2:* "Um exemplo do dia a dia é o molho de salada…" — "salada",
> "molho", "tempero", "maionese", "camada" e "sobrenadante" não aparecem
> em **nenhum** chunk enviado, nem a N1 nem a N2. Correto quimicamente,
> sem procedência.

### As duas que os dados obrigaram

**S4 — `MISATTRIBUTED`**
A afirmação é sustentada **pelo corpus enviado**, mas não pelo marcador
que leva. Não é invenção; é endereço errado.

> *Piloto C:* "Suas colunas verticais são chamadas grupos, e suas linhas
> horizontais, períodos", citando `[E3] [E4]`. Cobertura na citada:
> **0,00**. Cobertura no contexto inteiro: **0,86**.

Separar S4 de S3 importa porque a correção é diferente: S4 é problema de
atribuição, S3 é de fabricação.

**S0 — `UNCITED` / `META`**
Duas subclasses que **não devem ser verificadas por suporte**:

- *sem marcador:* **12 de 51 frases (24%)** em respostas `GROUNDED` não
  carregam marcador nenhum. O marcador costuma vir no fim do parágrafo e
  o escopo não está no contrato.
- *meta-afirmação:* frases **sobre** o conjunto de evidências, não
  extraídas dele. "As evidências não bastam para definir a hibridização
  sp³" tem cobertura 0,00 por construção. Verificar suporte nelas é erro
  de categoria. **4 casos** nos artefatos.

---

## 2. Casos reais encontrados

51 pares `afirmação → evidência` extraídos das 18 execuções. Depois de
separar meta e sem-marcador: **35 pares verificáveis**.

### Os dois casos obrigatórios, preservados

| | papel | onde cai na triagem |
|---|---|---|
| **N2** molho de salada | **positivo** de citação não sustentada | sinalizado |
| **N8** lei de Hess | **negativo obrigatório**, não pode ser rejeitado | não sinalizado |

### Distribuição por quadrante (frases com ≥ 6 palavras de conteúdo)

| quadrante | n | execuções |
|---|---:|---|
| suporte direto aparente | 24 | P1, PA, PB, PC×3, PD×2, **A-N8**, A-N9, **V-N8**, V-N9, N1, N2, N3×6, N6, N7×3 |
| **má atribuição** (S4) | 5 | PA, PB, PC, PD×2 |
| **externa** (S3) | **2** | **N2, N2** |
| parcial / indeciso | 1 | N5 |

### Candidatos a regressão — para adjudicação humana, não adjudicados aqui

**S3 candidatos (2):**

1. `N2` → `[E2]` — "Um exemplo do dia a dia é o molho de salada…"
   cit 0,39 · ctx **0,39** · 13 palavras
2. `N2` → `[E1]` — "Como as camadas ficam separadas, é possível retirar
   primeiro a água e depois o óleo usando um funil de separação."
   cit 0,33 · ctx **0,33** · 12 palavras

**S4 candidatos (5):**

3. `PC` → `[E3][E4]` — "Suas colunas verticais são chamadas grupos…"
   cit **0,00** · ctx 0,86
4. `PA` → `[E3]` — "Thomson modificou esse modelo…" cit 0,46 · ctx 0,82
5. `PB` → `[E3]` — "Pela teoria de Brønsted-Lowry…" cit 0,44 · ctx 0,75
6. `PD` → `[E1]` — "Faça isso por observação, tentativa e erro…"
   cit 0,56 · ctx 0,78
7. `PD` → `[E1]` — "Ao final, confira se todos os elementos têm a mesma
   quantidade…" cit 0,43 · ctx 0,71

**S2 obrigatório (2):**

8. `A-N8` → `[E5]` — cit 0,62 · ctx 0,92 — **tem de permanecer aceito**
9. `V-N8` → `[E4][E5]` — cit 0,78 · ctx 0,89 — idem

Os números são **triagem**, não veredicto. Nenhum destes nove foi
classificado definitivamente.

### O achado que muda o desenho

**N8 não é difícil de preservar.** Eu esperava que a derivação fosse o
caso crítico — o resultado (−75) não está em lugar nenhum, então qualquer
regra do tipo "todo número precisa aparecer" o rejeitaria.

Na prática, a frase de derivação **carrega os próprios insumos**: cita
Hess, entalpia, combustão, carbono, hidrogênio, metano, kJ/mol, −394,
−286, −891. A cobertura fica em 0,62–0,78, bem dentro do suporte direto. O
único token sem lastro é o resultado, 1 de 24.

**Derivações restam os insumos.** É por isso que a triagem por cobertura,
no nível da frase, não as rejeita. Mas não confio nisso como garantia: é
uma regularidade observada em 2 casos, não uma propriedade.

---

## 3. Alternativas de arquitetura

Nenhuma usa score vetorial como prova de suporte. Nenhuma trata ausência
lexical como ausência de suporte — ponto tratado explicitamente em cada
uma.

### A — verificador em segundo passo (modelo juiz)

Uma segunda chamada recebe `(afirmação, texto da evidência citada)` e
devolve classe + justificativa.

**Variantes:** A1, uma chamada por resposta; A2, uma por afirmação.

- Lida com paráfrase e derivação sem regra explícita.
- Provider-neutro via `TextGenerationProvider`.
- **Põe um juiz não verificável sobre um gerador não verificável.** O juiz
  pode afirmar suporte que não existe, e nada o confere. Não resolve o
  problema; move-o.
- Não determinístico: a mesma dupla pode receber veredictos diferentes.
- Mais caro e mais lento item por item.

### B — geração estruturada com âncora verificável

O contrato de resposta passa a exigir, por afirmação:

```json
{"claim": "...", "evidence": ["E1"], "support": "DIRECT",
 "span": "<trecho LITERAL do chunk citado>"}

{"claim": "...", "evidence": ["E5"], "support": "DERIVED",
 "inputs": ["−394 kJ/mol", "−286 kJ/mol", "−891 kJ/mol"],
 "operation": "soma algébrica das entalpias de combustão"}
```

O código então **verifica deterministicamente**: o `span` é substring
literal do chunk (normalizando espaço)? Os `inputs` são substrings? Para
operação aritmética declarada, o resultado confere?

- **Transforma suporte em propriedade checável**, como já é o marcador: o
  modelo declara, o código confere. É a mesma forma de validação que o
  projeto já usa, estendida um nível.
- Span inventado **falha por string**, sem juiz.
- Resolve a atribuição de escopo de graça: cada afirmação traz seus
  próprios marcadores, acabando com os 24% sem marcador e com a
  adivinhação de escopo.
- Custo marginal: só tokens de saída.
- **Exige mudar o prompt de resposta** — hoje proibido.
- Pode degradar a qualidade da resposta: formato mais rígido, modelo
  gastando capacidade em bookkeeping.
- O modelo pode marcar `DERIVED` para fugir da exigência de span. Mitigação:
  `DERIVED` sem `inputs` verificáveis é rejeitado.

### C — triagem determinística + fila de revisão humana

Nenhum julgamento de modelo. Para cada par, computa cobertura na citada e
no contexto, e **roteia** os suspeitos para fila humana.

- Custo zero, latência desprezível, totalmente determinístico.
- Nos 35 pares reais, com ≥3 palavras e corte em 0,50 de cobertura de
  contexto: **sinaliza exatamente 2, ambos de N2** — o caso conhecido — e
  **zero falsos positivos**. N8 não é sinalizado.
- Separa S3 de S4 pela segunda dimensão, que é informação que nenhuma das
  outras alternativas produz de graça.
- **Não estabelece suporte. Só roteia.** Um par não sinalizado não está
  verificado — está apenas sem sinal.
- Cego a inversão de sentido: "a água **não** é polar" tem cobertura
  perfeita. É o falso negativo estrutural.
- Os limiares precisam de calibração, e calibrar em n=35 é frágil.

### D — híbrido: B para o grosso, A só para o resíduo

Span verificável resolve S1 e S2 sem custo. O que sobrar — spans que não
casam, `DERIVED` com operação não mecanizável — vai ao juiz. C roda sempre
por cima, como rede.

Melhor cobertura, mais peças. Só vale depois de B medido.

### E — modelo de inferência textual (NLI) — **descartada**

Acrescenta dependência de uma família de modelos específica, trabalha em
inglês ou exige modelo próprio em português, e devolve rótulo sem
justificativa auditável. Fere a neutralidade de provider mais do que A,
sem a vantagem explicativa.

---

## 4. Riscos de falso positivo e falso negativo

**Falso positivo** = acusar de não sustentada uma afirmação que é.

| | risco | evidência |
|---|---|---|
| A | juiz conservador rejeita paráfrase distante ou derivação | não medido |
| B | span não casa por diferença tipográfica (`Aℓ` × `Al`, `−` × `-`, hifenização de PDF) | **real**: o corpus usa `Aℓ` e `−` (menos unicode) |
| C | frase curta e conclusiva (`"Portanto, ela não é destruída, mas transformada"`, 2 palavras) | **medido**: o único falso positivo some com mínimo de 3 palavras |
| C | linguagem procedimental ("Identifique", "confira", "Faça") não aparece no texto-fonte | **medido**: 5 casos, todos S4 e não S3 |

**Falso negativo** = deixar passar afirmação não sustentada.

| | risco | evidência |
|---|---|---|
| A | juiz alucina suporte | estrutural, não medido |
| B | modelo escolhe `DERIVED` para escapar do span | estrutural |
| C | **inversão de sentido**: negação, troca de sujeito, inversão de causa | **cego por construção** |
| C | afirmação externa escrita com o vocabulário do chunk | **cego por construção** |
| todas | afirmação sem marcador nenhum: **24% das frases** | **medido** |

O falso negativo de C é o mais sério e não tem conserto dentro de C.
Qualquer adoção de C precisa dizer isso em voz alta: **C reduz o volume
que o humano precisa olhar; não substitui o olhar.**

---

## 5. Custo e latência

Medidos nas 18 chamadas: entrada mediana **3.377** tokens, saída mediana
**210**, geração mediana **3.319 ms**, custo mediano **US$ 0,00065**,
1,9 afirmações com marcador por resposta.

| | custo extra | % sobre o atual | latência extra |
|---|---|---:|---|
| **A1** 1 chamada/resposta | US$ 0,00043 | **+66%** | +2 a 4 s |
| **A2** 1 chamada/afirmação | US$ 0,00036 | **+55%** | +4 a 8 s (serial) |
| **B** geração estruturada | US$ 0,00010 | **+16%** | +1 a 3 s |
| **C** triagem determinística | US$ 0 | **0%** | < 10 ms |

A2 pode paralelizar, trocando latência por concorrência de provider.

---

## 6. Contrato de dados proposto

Um registro por par, versionado pelo método — como as políticas do projeto
já são.

```
ClaimSupport (frozen)
  claim_index          int
  claim_text           str            # sem marcadores
  markers              tuple[str,...] # vazio = S0 UNCITED
  support_class        str            # S0..S4, vocabulário fechado
  cited_coverage       float | None   # None quando não aplicável
  context_coverage     float | None
  unsupported_tokens   tuple[str,...]
  span                 str | None     # só B
  span_verified        bool | None    # só B: substring conferida
  derivation_inputs    tuple[str,...] # só B/S2
  derivation_verified  bool | None    # só B/S2, quando mecanizável
  needs_human_review   bool
  method               str            # ex. "LEXICAL_TRIAGE"
  method_version       str            # ex. "v1" — imutável, novo = v2
  judged_by            str | None     # "HUMAN" quando adjudicado
```

Regras do contrato:

1. `support_class` nunca é inferida de score vetorial.
2. `cited_coverage` baixa **não** determina `support_class`. Ela só pode
   elevar `needs_human_review`.
3. `S2 DERIVED` exige `derivation_inputs` não vazio.
4. Meta-afirmação e `S0` saem do denominador de qualquer métrica.
5. `method_version` é imutável: mudar limiar é `v2`, nunca editar `v1` —
   mesma regra das políticas de chunking e retrieval.
6. `judged_by = "HUMAN"` prevalece sobre qualquer classe automática e
   nunca é sobrescrito por recomputação.

Isso **não** muda `GroundedAnswer`. A fidelidade é um eixo novo, ao lado
de grounding e suficiência, e segue a mesma regra: não colapsar eixos
distintos num campo.

---

## 7. Testes necessários

**Taxonomia e contrato**
- vocabulário fechado; classe desconhecida falha
- `S2` sem `derivation_inputs` é rejeitado
- `judged_by="HUMAN"` sobrevive à recomputação
- `method_version` presente em todo registro

**Regressão com os casos reais**
- **N2 molho de salada → sinalizado** (positivo obrigatório)
- **N8 Hess → NÃO sinalizado** (negativo obrigatório, nos dois artefatos)
- os 5 candidatos S4 caem em má atribuição, não em externa
- as 4 meta-afirmações não entram na verificação
- as 12 frases sem marcador classificam como `S0`, não como falha

**Triagem (C)**
- frase curta conclusiva não sinaliza com mínimo de 3 palavras
- cobertura de contexto alta + citada baixa ⇒ S4, não S3
- inversão de sentido **não** é detectada — teste de caracterização, para
  que o cego fique documentado e não seja confundido com garantia

**Âncora (B)**
- span literal confere; span inventado falha
- span com `Aℓ` × `Al`, `−` × `-`, espaço duplo: decidir normalização e
  testá-la nos dois sentidos
- aritmética declarada é recomputada e confere em N8
- `DERIVED` com insumo que não está no chunk falha

**Mutação dirigida**
- desligar o corte de cobertura ⇒ N2 deixa de ser sinalizado
- aceitar span não conferido ⇒ teste de span inventado falha
- tratar ausência lexical como veredicto ⇒ N8 passa a ser rejeitado

---

## 8. Recomendação

**Prototipar C primeiro, depois B. Não prototipar A.**

**Por quê C antes:** custa zero, é determinística, e nos 35 pares reais já
acerta o caso conhecido com zero falso positivo. Entrega hoje a lista de
pares para adjudicação humana — que é o insumo que falta para calibrar
qualquer coisa. E como não decide nada, não pode decidir errado.

**Por quê B depois:** é a única que torna o suporte **verificável** em vez
de estimado. O marcador já funciona assim — o modelo declara, o código
confere — e B estende exatamente essa forma. Resolve de lado os 24% de
frases sem marcador. Custa +16%, contra +55% a +66% de A.

**Por quê não A como primária:** põe um juiz não verificável sobre um
gerador não verificável, pelo maior custo e a maior latência. Não elimina
o problema; acrescenta uma camada que também pode errar e que ninguém
confere. Fica como último recurso para o resíduo de B, no desenho D.

### O que a recomendação não resolve

C não vê inversão de sentido. B depende de o modelo cooperar com o
formato. Nenhuma das duas cobre afirmação sem marcador, a não ser que B
mude o contrato — e mudar o contrato exige mexer no prompt de resposta,
hoje proibido.

**A decisão de liberar o prompt de resposta é sua, e é o que destrava B.**

### Pré-requisito antes de qualquer implementação

Os **9 pares candidatos** da seção 2 precisam de adjudicação humana. Sem
rótulo verdadeiro não há como medir falso positivo nem falso negativo de
nenhuma alternativa — e calibrar limiar olhando o resultado seria o mesmo
erro que a Fase 6 recusou cometer com o Evaluation Set.

Posso gerar a planilha cega, como nos 67 e nos 276, **sem registrar
palpite meu**.
