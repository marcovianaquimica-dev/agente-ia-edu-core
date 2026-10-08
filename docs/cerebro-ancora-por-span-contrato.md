# CÉREBRO — âncora por span: contrato de dados e testes

**Desenho para aprovação. Nada implementado.**

Decisões recebidas e adotadas:

1. **Contribuição conjuntiva.** Toda evidência atribuída precisa ter
   contribuição verificável. Não se exige que cada uma sustente sozinha a
   afirmação inteira — podem ser complementares. Uma evidência sem
   contribuição **não é lavada** pela presença de outra correta.
2. **Alternativa B autorizada**, com alteração controlada do prompt. C
   passa a auxiliar/diagnóstico.

Nada muda em retrieval, corpus, embeddings, direitos, orçamento, qrels ou
conjuntos congelados. O caminho atual permanece **intacto e em paralelo**.

---

## 1. Um conflito que precisa ser resolvido antes de implementar

A Decisão 1 e o rótulo humano de `V-N8` **podem discordar**.

`V-N8` citou `[E4]` e `[E5]` e foi julgado `MISATTRIBUTED`. Os dados:

| | −394 | −286 | −891 | "Hess" |
|---|---|---|---|---|
| `E5` p.240 | **sim** | sim | sim | não |
| `E4` p.88 | **não** | sim | sim | **sim** |

`E4` é a única das duas que contém "Hess" — o **nome da operação** que a
resposta declara usar. Sob a Decisão 1, isso é exatamente uma
**contribuição complementar legítima**, e `V-N8` passaria.

Então: ou o julgamento humano aplicou um critério mais estrito que a
Decisão 1, ou `V-N8` precisa ser reetiquetado sob a regra nova.

**Não resolvo isso sozinho.** Se eu implementar e rodar contra os 9, a
divergência vai aparecer como "falha de regressão" quando na verdade é
divergência de contrato. Precisa de decisão antes.

---

## 2. Contrato de saída do provider

O modelo deixa de devolver `{answer, used_evidence, sufficient}` e passa a
devolver **a resposta como lista de afirmações**.

```json
{
  "claims": [
    {
      "kind": "FACTUAL",
      "text": "A concentração em quantidade de matéria é a razão entre
               mol de soluto e volume da solução em litros.",
      "support": [
        {"evidence": "E1",
         "span": "concentração em quantidade de matéria, que é o quociente",
         "role": "define a grandeza"}
      ]
    },
    {
      "kind": "FACTUAL",
      "text": "A entalpia de formação do metano é de aproximadamente
               −75 kJ/mol.",
      "derivation": {
        "expression": "(-394 + 2*(-286)) - (-891)",
        "result": "-75",
        "unit": "kJ/mol",
        "inputs": [
          {"evidence": "E5", "span": "−394", "role": "combustão do carbono"},
          {"evidence": "E5", "span": "−286", "role": "combustão do hidrogênio"},
          {"evidence": "E5", "span": "−891", "role": "combustão do metano"},
          {"evidence": "E4", "span": "lei de Hess", "role": "nomeia a operação"}
        ]
      }
    },
    {"kind": "CONNECTIVE", "text": "Portanto,"},
    {"kind": "META", "text": "As evidências não cobrem a definição de sp³."}
  ],
  "sufficient": true
}
```

### Os três tipos de afirmação

| `kind` | exige suporte? | para quê |
|---|---|---|
| `CLAIM_FACTUAL` | **sim** | afirmação sobre o mundo |
| `CLAIM_META` | não | sobre o próprio conjunto de evidências |
| `CLAIM_CONNECTIVE` | não | ligação, transição, fechamento sem conteúdo novo |

`META` e `CONNECTIVE` existem porque os dados reais os têm: 4
meta-afirmações nas 18 execuções, e frases como *"Portanto, ela não é
destruída, mas transformada"*.

### A resposta é MONTADA pelo sistema

`answer_text = join(claim.text)`. O modelo não devolve uma resposta
separada da lista.

Isso elimina dois problemas de uma vez:

- **o escopo do marcador deixa de ser adivinhado.** Hoje 24% das frases
  não têm marcador e o escopo não está no contrato; aqui cada afirmação
  traz o seu.
- **não há deriva possível** entre o texto entregue e o texto verificado,
  porque são o mesmo objeto.

Risco, a medir: a fluência pode piorar. Frases montadas podem perder
transição natural. É motivo para rodar em paralelo, não para não fazer.

---

## 3. Normalização de span — `SPAN_NORMALIZATION_V1`

Cada regra justificada por artefato **medido** no corpus de 5.945 chunks.
Regra sem caso real não entra. Lista imutável: mudar é `v2`.

| ordem | regra | justificativa medida |
|---|---|---|
| 1 | `NFKC` | `Aℓ`→`Al`, `CH₄`→`CH4`, `sp³`→`sp3`. O corpus usa `Aℓ`; as respostas usaram `Aℓ2O3` e `CH₄` |
| 2 | de-hifenização de quebra (`letra- letra` → `letraletra`) | **1.878 chunks** (32%): "comentá- rios", "cientí- ficas" |
| 3 | unificar travessões (`−`, `–`, `—` → `-`) | minus em 820, en-dash em 1.816, em-dash em 165 chunks |
| 4 | unificar aspas curvas → retas | 958 + 951 + 10 + 117 chunks; a resposta de N9 usou `“sp³”` |
| 5 | colapsar espaço em branco (inclui `\n`) | **2.711 chunks** (46%) têm `\n` |

**Não entra:** colapso de espaço duplo — medido **0 chunks**, regra sem
caso. Entraria só por simetria, e simetria não é justificativa.

A ordem importa: `NFKC` antes de 3, porque `NFKC("O2⁻")` produz
`"O2−"` — cria um MINUS SIGN que a regra 3 então unifica.

### Caixa: sensível, com saída nomeada

O chunk que `N8` cita contém **apenas `"Entalpia"` maiúsculo**, nunca
minúsculo. Um modelo citando `"entalpia"` no meio da frase falharia — no
próprio caso obrigatório.

Proposta: **comparar com caixa**, e quando falhar com caixa mas casar sem
ela, registrar `SPAN_CASE_MISMATCH` em vez de `SPAN_NOT_FOUND`. Assim a
frequência fica **medida** antes de se decidir relaxar.

Relaxar de saída seria perigoso em química: `Co` (cobalto) e `CO`
(monóxido) não são a mesma coisa.

---

## 4. Contribuição conjuntiva, implementada

Para cada afirmação `FACTUAL`:

1. toda evidência citada precisa de **ao menos um span próprio** →
   senão `EVIDENCE_WITHOUT_CONTRIBUTION`;
2. todo span precisa ser encontrado **no chunk da evidência que o
   declara** → senão `SPAN_NOT_FOUND`;
3. a verificação é **por par (evidência, span)**, nunca sobre a união dos
   chunks.

O item 3 é a correção direta do falso negativo de `V-N8`: minha triagem
calculava cobertura sobre a união, e a completude de `E5` mascarou a
lacuna de `E4`.

`role` é texto livre, **não verificável por máquina** — existe para o
auditor humano julgar se a contribuição é real. O código verifica que o
span existe; só o humano julga se ele sustenta.

---

## 5. Derivação

`derivation.expression` numa gramática restrita: números, `+ - * /`,
parênteses. O código avalia com parser próprio — **sem `eval`** — e
compara a `result` com tolerância relativa declarada.

- confere → `DERIVATION_VERIFIED`
- não confere → `DERIVATION_MISMATCH`
- fora da gramática → `DERIVATION_NOT_MECHANIZED`, vai para revisão
  humana, **não é rejeitada automaticamente**

Todo `input` é um span e passa pela mesma verificação por par. `N8`:
`(-394 + 2*(-286)) - (-891) = -75`, e os três insumos existem em `E5`.

---

## 6. Como cada situação pedida é tratada

| situação | tratamento |
|---|---|
| **paráfrase** | vive em `claim.text`, livre. O `span` é **cópia**. Isso resolve por construção os 3 falsos positivos da triagem lexical, que eram todos paráfrase |
| **múltiplas evidências complementares** | cada uma com span e `role` próprios; verificação por par. Permitido e esperado |
| **`DERIVED`** | bloco `derivation` com insumos como spans + expressão avaliável |
| **span inexistente** | `SPAN_NOT_FOUND` — determinístico, sem juiz |
| **span da evidência errada** | o span é procurado **no chunk daquela evidência**; se o modelo quotar de `E5` e atribuir a `E4`, falha. É o ataque direto ao modo dominante (4 de 9 `MISATTRIBUTED`) |
| **afirmação sem suporte** | `FACTUAL` sem `support` nem `derivation` ⇒ `STRUCTURED_CONTRACT_VIOLATION`, resposta não entregável. Falha fechada |
| **não factual / meta** | `META` e `CONNECTIVE` dispensam suporte e saem do denominador das métricas |

---

## 7. O que B **não** resolve

Preciso dizer antes de construir, não depois.

**Um span verificado prova que o texto existe no chunk citado. Não prova
que ele sustenta a afirmação.** O modelo pode citar um chunk real e
quotar dele uma frase irrelevante. A verificação passa; o suporte não
existe.

B transforma *"você citou um chunk real?"* em *"você quotou texto real
desse chunk?"* — estritamente mais forte, ainda não *"esse texto sustenta
o que você disse?"*.

**Vetores de fuga previsíveis:**

- marcar tudo como `META` para escapar da exigência de span;
- quotar um span longo e genérico que casa com qualquer coisa;
- declarar `DERIVATION_NOT_MECHANIZED` para evitar a expressão.

Nenhum é fechável por substring. Todos ficam **visíveis no payload
ADMIN** e mensuráveis — que é o que permite decidir depois com dado.

---

## 8. Contrato de dados verificado

```
SupportSpan (frozen)
  evidence_marker   str
  span_text         str            # como o modelo escreveu
  role              str            # livre, NÃO verificável
  status            str            # SPAN_VERIFIED | SPAN_NOT_FOUND
                                   # | SPAN_CASE_MISMATCH | SPAN_EMPTY
  normalization     str            # "SPAN_NORMALIZATION_V1"
  offset            int | None     # posição no chunk normalizado

ClaimRecord (frozen)
  index             int
  kind              str            # CLAIM_FACTUAL | META | CONNECTIVE
  text              str
  support           tuple[SupportSpan, ...]
  derivation        Derivation | None
  evidences_without_contribution  tuple[str, ...]
  verified          bool           # derivado, não declarado
```

Regras:

1. `verified` é **derivado**: todos os spans `SPAN_VERIFIED`, nenhuma
   evidência sem contribuição, derivação não `MISMATCH`.
2. Um único `CLAIM_FACTUAL` não verificado ⇒ resposta **não entregável**.
3. `normalization` é gravada em cada span — mudar a norma é `v2` e os
   registros antigos continuam dizendo sob qual regra foram aceitos.
4. Não altera `GroundedAnswer`. Fidelidade é eixo **novo**, ao lado de
   grounding e suficiência, pela mesma razão de antes: não colapsar eixos.

---

## 9. Testes

**Normalização (pura, sem provider)**
- cada uma das 5 regras, isolada, com o artefato real que a justifica
- a ordem `NFKC` antes de travessão, com `O2⁻`
- normalização é idempotente
- **não** colapsa espaço duplo inexistente no corpus
- span com caixa trocada ⇒ `SPAN_CASE_MISMATCH`, não `SPAN_NOT_FOUND`
- `Co` e `CO` **não** se confundem

**Verificação por par**
- span existe no chunk declarado ⇒ `SPAN_VERIFIED`
- o mesmo span existindo em **outro** chunk do contexto, mas não no
  declarado ⇒ `SPAN_NOT_FOUND` (não "achei em algum lugar")
- span vazio ou só espaço ⇒ `SPAN_EMPTY`
- evidência citada sem nenhum span ⇒ `EVIDENCE_WITHOUT_CONTRIBUTION`
- **uma evidência boa não lava uma ruim**: `[E4]` sem contribuição +
  `[E5]` com span válido ⇒ a afirmação **não** fica verificada

**Derivação**
- `N8`: `(-394 + 2*(-286)) - (-891) = -75`, insumos em `E5` ⇒
  `DERIVATION_VERIFIED`
- resultado trocado ⇒ `DERIVATION_MISMATCH`
- insumo que não existe no chunk ⇒ `SPAN_NOT_FOUND`
- expressão fora da gramática ⇒ `DERIVATION_NOT_MECHANIZED`, revisão
  humana, **não** rejeição
- o avaliador recusa `__import__`, chamada de função, nome — sem `eval`

**Tipos de afirmação**
- `FACTUAL` sem suporte ⇒ `STRUCTURED_CONTRACT_VIOLATION`
- `META` e `CONNECTIVE` sem suporte ⇒ válidos
- resposta só de `META` ⇒ caracterização, para medir o vetor de fuga
- `kind` desconhecido ⇒ violação de contrato

**Montagem da resposta**
- `answer_text` é exatamente `join(claim.text)`
- remoção de marcadores continua valendo sobre o texto montado
- a saída pública segue com três campos

**Regressão com os 9 adjudicados**
- `N2` molho de salada: nenhum span o sustenta ⇒ não verificada
- `A-N8`: `DERIVATION_VERIFIED`
- os 4 `MISATTRIBUTED`: pelo menos uma evidência sem contribuição
- os 3 `DIRECTLY_SUPPORTED`: verificados
- **`V-N8`: resultado registrado, não afirmado** — depende da decisão da
  seção 1

**Mutação dirigida**
- aceitar span sem conferir ⇒ teste de span inventado falha
- verificar sobre a união em vez de por par ⇒ o teste do "não lava"
  falha
- tratar `FACTUAL` sem suporte como válido ⇒ falha fechada quebra

**Paralelo**
- o `GroundedAnswerer` atual continua passando em todos os seus testes
- rodar os dois caminhos sobre o mesmo contexto produz dois registros
  comparáveis

---

## 10. Operação em paralelo

`StructuredAnswerer` é **módulo novo**, com prompt próprio. Nada em
`GroundedAnswerer` nem em `INSTRUCAO` é tocado — o prompt alterado é um
**segundo** prompt, não uma edição do existente.

O CLI ganha opção para rodar o caminho atual, o novo, ou **os dois sobre
o mesmo contexto**, imprimindo lado a lado. Comparar com o contexto fixo
isola a mudança de contrato da variação de recuperação — erro que o par
N1/N2 cometeu e que não vou repetir.

---

## 11. O que preciso de você antes de implementar

1. **`V-N8`: conjuntivo estrito ou contribuição complementar?** A seção 1
   mostra que as duas leituras dão veredictos opostos no caso real.
2. **Caixa sensível com `SPAN_CASE_MISMATCH` medido** — concorda, ou
   prefere insensível desde já?
3. **A resposta montada a partir das afirmações** é aceitável, sabendo que
   pode custar fluência?

Os 9 continuam **regressão**, não Calibration Set. Nada será calibrado
neles, e reproduzir os nove **não** declarará B validado.
