# CÉREBRO — contrato de visibilidade e separação grounding × suficiência

**Desenho para revisão. Nada implementado.** Nenhuma alteração de
retrieval, embeddings, corpus, top-k, orçamento, `ANSWER_KEY`, thresholds,
qrels ou conjuntos congelados.

---

# Parte 1 — Visibilidade da fundamentação

## O erro de nome que precisa ser desfeito primeiro

Hoje existe `BuiltContext.public_payload()` e
`GroundedAnswer.public_payload()`. **O nome mente sobre a audiência.**

"Público" ali significa *sem literal de obra comercial* — uma propriedade
de **direitos**. A regra nova fala de outra coisa: *sem o aparato de
fundamentação* — uma propriedade de **audiência**.

São eixos ortogonais:

| | tem literal comercial | tem aparato de fundamentação |
|---|---|---|
| prompt | **sim** | sim |
| `public_payload()` de hoje | não | **sim** |
| saída ADMIN nova | não | sim |
| saída pública nova | não | **não** |

A saída de hoje é segura em direitos e **insegura em audiência**. Se uma
rota futura chamar `public_payload()` confiando no nome, ela entrega
`chunk_id`, `text_hash`, página, score e marcadores a um aluno.

**Primeira medida do contrato: renomear `public_payload()` →
`admin_payload()`,** nos dois objetos, e deixar o nome `public_payload`
livre para significar o que diz.

## Os três contratos

### A. Saída interna — `GroundedAnswer` (já existe, ganha campos)

O retorno do serviço. **Nunca serializado diretamente.** Tem tudo:
marcadores, evidências resolvidas, `_raw`, tokens, os dois eixos novos.

### B. Saída pública — `PublicAnswer` (novo)

```
answer_text         : str | None      # marcadores REMOVIDOS
outcome             : "ANSWERED" | "UNAVAILABLE"
unavailable_reason  : str | None
```

**Três campos. Mais nada.** Não é um objeto grande com campos opcionais —
é um objeto pequeno onde os campos administrativos **não existem**.

Vocabulário de `unavailable_reason`, deliberadamente curto:

| valor | cobre |
|---|---|
| `NO_ANSWER_FROM_CORPUS` | sem evidência · não fundamentada · citação inválida · suficiência negada |
| `TEMPORARILY_UNAVAILABLE` | recuperação degradada · falha do provider · resposta fora do contrato |

**O colapso é intencional, não preguiça.** Distinguir "o modelo inventou
uma citação" de "não há evidência" entrega informação sobre o estado
interno do acervo a quem não precisa dela. Quem precisa é o ADMIN, e ele
tem o canal próprio.

### C. Saída ADMIN — `AdminAnswer` (o `public_payload()` de hoje, ampliado)

Tudo que já sai, mais:

| campo novo | por quê |
|---|---|
| `answer_text_raw` | com marcadores — o que o modelo escreveu |
| `answer_text_public` | sem marcadores — **o que o aluno veria** |
| `raw_used_evidence` | grafia exata do provider |
| `normalized_used_evidence` | resultado da normalização |
| `grounding` | o eixo determinístico |
| `sufficiency` | o eixo declarado |
| `deliverable` | a decisão derivada |
| `stripping_artifacts` | lista de defeitos achados na remoção |

`answer_text_raw` **e** `answer_text_public` lado a lado é o que torna a
remoção auditável: o ADMIN vê exatamente o que foi tirado.

`raw_used_evidence` / `normalized_used_evidence` paga a dívida de
observabilidade registrada desde a primeira execução real — foi a ausência
desses dois campos que impediu a segunda chamada de servir como prova.

Direitos continuam valendo integralmente no canal ADMIN: `excerpt` só
para fonte citável, nunca `raw_text`, rastreabilidade completa por
`chunk_id`, `text_hash`, fonte, documento e página impressa.

## A remoção dos marcadores

### Depois da validação, nunca antes

A ordem é uma propriedade, não uma preferência:

```
texto do modelo
  → extrai marcadores do corpo + do campo      (validação)
  → normaliza grafia                           (validação)
  → confere contra as evidências entregues     (validação)
  → decide grounding                           (validação)
  → decide entrega                             (decisão)
  → SÓ ENTÃO remove os marcadores              (apresentação)
```

Remover antes destruiria a única evidência verificável de que a resposta
cita o que diz citar. `answer_text_public` é **derivado**, nunca
substitui o original.

### A remoção é segura? Medido, com ressalva

Nas 6 respostas reais: **22 marcadores em 18 blocos, 18/18 depois de
pontuação final, zero artefatos** após remover e normalizar espaço.

Mas **nada no prompt exige essa posição.** Se o modelo escrever "segundo
[E1], a concentração…", remover corrompe a oração.

Por isso a remoção não deve confiar na sorte. Proposta: depois de remover,
**verificar deterministicamente** por artefatos — espaço duplo, espaço
antes de pontuação, pontuação inicial, vírgula dupla, parêntese vazio.
Achando artefato, registrar em `stripping_artifacts` e tratar como
**não entregável**, com `unavailable_reason = TEMPORARILY_UNAVAILABLE`.

Entregar texto corrompido é pior que não entregar, e silenciar o defeito é
pior que os dois.

Só a forma com colchetes é removida — a mesma de `_MARCADOR`. `E1` solto
no texto não é tocado: poderia ser conteúdo legítimo.

## Como impedir que uma rota futura vaze campos administrativos

Oito camadas, da mais estrutural à mais específica:

1. **Ausência, não filtro.** `PublicAnswer` não tem os campos. Não há o
   que esquecer de remover.
2. **Uma única porta.** `to_public(answer) -> PublicAnswer` é o único
   construtor. Nenhum caminho alternativo monta a saída pública.
3. **A entrega é propriedade do estado, não do chamador.** `to_public()`
   devolve `answer_text = None` para todo estado não entregável. Uma rota
   não consegue entregar texto de resposta não fundamentada nem
   acidentalmente nem de propósito.
4. **`ADMIN_ONLY_FIELDS`**, constante única, consumida pelo teste — o
   mesmo padrão de fonte única de verdade já usado em `_rejection_ladder`.
5. **Teste estrutural:** campos de `PublicAnswer` ∩ `ADMIN_ONLY_FIELDS`
   = ∅.
6. **Teste de valor-sentinela** — o mais forte. Montar um contexto em que
   cada dado administrativo é uma string única e improvável, serializar a
   saída pública e exigir que **nenhuma sentinela apareça em lugar
   nenhum**. Pega o vazamento aninhado sob chave renomeada, que o teste de
   nome de campo não pega.
7. **Quando a rota existir:** `response_model=PublicAnswerResponse`,
   pydantic com `extra="forbid"`, rota ADMIN separada com
   `require_platform_admin` — a convenção já seguida pelas 15 rotas atuais.
8. **Substituir o guarda atual.** `test_no_api_module_imports_the_generation_path`
   quebra quando a rota nascer. Ele deve ser trocado por: a rota pública
   responde com `PublicAnswer`, a rota ADMIN exige `PLATFORM_ADMIN`, e
   nenhuma rota devolve `AdminAnswer` sem esse `Depends`.

Nenhuma UI foi desenhada.

---

# Parte 2 — Grounding × suficiência

## Análise do contrato atual

```python
if invalidos:            return INVALID_EVIDENCE_REFERENCE
if not citados:          return ANSWER_WITHOUT_CITATION
                         return GROUNDED
```

`model_says_sufficient` é gravado e **nenhum ramo o consulta**. O CLI
imprime um aviso — o sinal é **consultivo, não vinculante**. Quem ler o
relatório vê; quem consumir `is_grounded`, não.

### Por que não basta acrescentar um valor ao enum

A causa do problema é ter **um enum para dois eixos**. Acrescentar
`EVIDENCE_DECLARED_INSUFFICIENT` a `status` repetiria o erro: a resposta
passaria a *não* ser `GROUNDED` quando ela **é** fundamentada — as
citações conferem. Perderia-se exatamente a distinção que se quer criar.

| | pergunta | quem responde | verificável? |
|---|---|---|---|
| **grounding** | a resposta está apoiada no que citou? | o código | **sim**, deterministicamente |
| **suficiência** | essas evidências bastam? | o modelo | **não**, é auto-relato |

São de naturezas epistêmicas diferentes. Colapsá-las num campo faz o
verificável herdar a confiabilidade do não-verificável.

### O dado que falta, e que pesa na decisão

**Nas 6 execuções reais com JSON, `sufficient` veio `True` em todas** —
inclusive naquela em que o modelo citou com grafia errada e a resposta foi
reprovada. **Nunca observamos um `false`.**

A confiabilidade do sinal é, portanto, **inteiramente não medida**. Isso
não impede construir o portão; impede confiar nele em silêncio.

## Máquina de estados mínima

Um ciclo de vida, dois veredictos independentes, uma decisão derivada.

```
                      ┌─ sem evidência ────────────→ NO_EVIDENCE
                      ├─ recuperação degradada ────→ DEGRADED_RETRIEVAL
  pergunta + contexto ┤                               (não chamam o provider)
                      └─ chama o provider
                           ├─ erro ────────────────→ PROVIDER_FAILED
                           ├─ fora do contrato ────→ PROVIDER_INVALID_RESPONSE
                           └─ resposta no contrato
                                ├── eixo GROUNDING    (determinístico)
                                ├── eixo SUFFICIENCY  (declarado)
                                └── decide ENTREGA
```

Os quatro estados terminais já existem e **não mudam**.

### Eixo grounding — determinístico

| valor | significado |
|---|---|
| `GROUNDED` | todo marcador citado resolve para evidência entregue |
| `ANSWER_WITHOUT_CITATION` | respondeu, não citou nada |
| `INVALID_EVIDENCE_REFERENCE` | citou marcador inexistente |

**Nomes preservados.** Já estão testados, já descrevem bem o eixo, e
renomear seria churn sem ganho.

### Eixo suficiência — declarado, nome novo

| valor | significado |
|---|---|
| `SUFFICIENCY_AFFIRMED` | o modelo declarou `true` |
| `SUFFICIENCY_DENIED` | o modelo declarou `false` |
| `SUFFICIENCY_UNDECLARED` | campo ausente, nulo ou não-booleano |

Todos verificados como **inexistentes no projeto**. `INSUFFICIENT_EVIDENCE`
está tomado com outro significado — `evidence_state` do mastery (migração
029) e `status` do diagnóstico inicial (migração 013) — e **não é
reutilizado**.

### A decisão de entrega

| | `AFFIRMED` | `UNDECLARED` | `DENIED` |
|---|---|---|---|
| **`GROUNDED`** | **entrega** | **entrega**, sinalizado | **não entrega** |
| `ANSWER_WITHOUT_CITATION` | não | não | não |
| `INVALID_EVIDENCE_REFERENCE` | não | não | não |

```
deliverable = (grounding == GROUNDED)
              and (sufficiency != SUFFICIENCY_DENIED)
              and not stripping_artifacts
```

Quando o bloqueio vem da suficiência, o motivo registrado para o ADMIN é
`EVIDENCE_DECLARED_INSUFFICIENT` — **um motivo de bloqueio, não um
status**. A resposta continua `GROUNDED`, porque ela é.

### `UNDECLARED` entrega — e por quê

Recomendo **entregar** quando o campo falta, registrando a ausência.

Omitir um campo JSON é deslize de formatação; negar explicitamente é
afirmação deliberada. Fechar em cima de uma omissão transformaria qualquer
provider menos obediente ao contrato num sistema que não responde nada.

### Condição que proponho junto com o portão

Como `DENIED` **nunca foi observado**, proponho que as primeiras
ocorrências sejam **revisadas por humano** antes de o portão ser
considerado confiável: o ADMIN vê a resposta bloqueada, o texto, as
evidências, e julga se o bloqueio foi correto.

É o mesmo princípio dos 67 e dos 276: o sistema propõe, o humano valida.
Sem isso estaríamos confiando num sinal cuja taxa de erro é desconhecida —
e um portão que bloqueia respostas boas é tão ruim quanto um que deixa
passar respostas ruins.

---

# Parte 3 — Impactos

## `ContextBuilder` — **nenhum**

Ele não sabe o que é resposta, audiência ou suficiência. Monta contexto a
partir de hits e direitos, e nada disso muda.

A única mudança que o alcança é o **renome** de `public_payload()` para
`admin_payload()` — e mesmo essa é de nome, não de comportamento.

Um desenho que precisasse tocar o `ContextBuilder` para resolver
visibilidade de saída estaria errado, e vale dizer isso em voz alta.

## `GroundedAnswerer` — o grosso

1. Campos novos em `GroundedAnswer`: `sufficiency`, `grounding`,
   `deliverable`, `raw_used_evidence`, `normalized_used_evidence`,
   `answer_text_public`, `stripping_artifacts`, `delivery_block_reason`.
2. `_remover_marcadores(texto) -> (texto_limpo, artefatos)`, pura,
   aplicada **depois** da validação.
3. `_classificar_suficiencia(valor)` — `True`/`False`/qualquer outra coisa
   → os três valores do eixo.
4. `public_payload()` → `admin_payload()`, com os campos novos.
5. `to_public(answer) -> PublicAnswer`, única porta da saída pública.
6. `ADMIN_ONLY_FIELDS`, constante.

`is_grounded` **permanece com o significado atual** — grounding, não
entrega. Quem quiser entrega usa `deliverable`. Mudar o sentido de
`is_grounded` em silêncio seria pior que acrescentar um campo.

## CLI — mostra os dois lados

O `cerebro_ask.py` é ferramenta de ADMIN e continua mostrando tudo. Ganha:

- os **dois eixos** e o `deliverable` no item 5;
- um item novo, **“o que o usuário comum veria”**, imprimindo o
  `PublicAnswer` literal — é assim que um defeito de remoção fica visível
  numa execução real em vez de só em teste;
- `raw_used_evidence` × `normalized_used_evidence` lado a lado no item 6;
- `stripping_artifacts`, quando houver.

O aviso atual de `sufficient: false` deixa de ser consultivo: passa a
aparecer como motivo de bloqueio.

---

# Parte 4 — Testes necessários

## Remoção de marcadores (pura)

- remove `[E1]` isolado, bloco `[E1] [E4]`, marcador no início
- **não** remove `E1` sem colchetes
- idempotente; texto sem marcador sai idêntico
- não deixa espaço duplo, espaço antes de pontuação, pontuação órfã
- marcador no meio de oração → **artefato detectado**, não entregável
- regressão com as 6 respostas reais: zero artefato
- a remoção ocorre **depois** da validação — teste que um marcador
  inválido continua sendo detectado mesmo que seria removido depois

## Saída pública

- estado entregável → `outcome=ANSWERED`, texto sem marcador
- cada estado não entregável → `answer_text is None` e a razão coarse certa
- `NO_EVIDENCE`, suficiência negada e citação inválida → **a mesma**
  `NO_ANSWER_FROM_CORPUS` (o colapso é a propriedade)
- degradado e falha de provider → `TEMPORARILY_UNAVAILABLE`
- **teste de sentinela**: nenhum `chunk_id`, `text_hash`, título de fonte,
  página, score, marcador ou literal aparece na serialização pública
- campos de `PublicAnswer` ∩ `ADMIN_ONLY_FIELDS` = ∅
- `repr` da saída pública também limpo

## Saída ADMIN

- carrega `answer_text_raw` **e** `answer_text_public`
- carrega `raw_used_evidence` e `normalized_used_evidence`, distintos
  quando o provider devolve `["[E1]"]`
- literal comercial continua ausente; `excerpt` só para fonte citável
- rastreabilidade completa preservada

## Os dois eixos

- `GROUNDED` × `AFFIRMED` → entrega
- `GROUNDED` × `UNDECLARED` → entrega, sinalizado
- `GROUNDED` × `DENIED` → **não entrega**, `grounding` continua `GROUNDED`,
  `delivery_block_reason = EVIDENCE_DECLARED_INSUFFICIENT`
- citação inválida × `AFFIRMED` → não entrega (grounding manda)
- `sufficient` como string, número ou nulo → `UNDECLARED`, sem exceção
- os 4 estados terminais continuam não chamando o provider / não entregando
- `is_grounded` não mudou de significado

## Mutação dirigida

As novas propriedades precisam dos mesmos dentes das atuais:

- remover a verificação de artefato → algum teste falha
- `deliverable` ignorar `DENIED` → algum teste falha
- `to_public` passar a emitir texto em estado não entregável → falha
- um campo administrativo entrar em `PublicAnswer` → o teste de sentinela
  falha

---

# Parte 5 — Quais das 10 perguntas testam abstenção e suficiência

**Diretamente — os três graus medidos de ausência:**

| | o que mede |
|---|---|
| **N8** entalpia de formação do metano | número ausente, entorno bem coberto (metano 272, kJ/mol 115) |
| **N9** hibridização sp³ | conceito ausente mas plausível (0 sob todas as grafias) |
| **N10** Diels-Alder | ausência óbvia e lexicalmente distante (0, controle) |

Previsão registrada: **nenhuma das três abstém hoje**, porque a única
porta estrutural é o modelo não citar ninguém. Com o portão proposto, elas
passam a depender de o modelo declarar `sufficient: false` — que é
precisamente o que nunca observamos.

São, portanto, as três perguntas que **medem o sinal** antes de ele virar
portão. Rodá-las antes de implementar daria a primeira observação de
`DENIED` da história do projeto — ou mostraria que ele não vem nem quando
deveria, que é um achado maior ainda.

**Indiretamente — as duas que expõem o limite do portão:**

| | por quê |
|---|---|
| **N7** "como funciona uma pilha?" | ambígua. Pode gerar resposta confiante no sentido errado, com citações válidas e suficiência afirmada: **entregável e errada**. Nenhum dos dois eixos pega. |
| **N4** vela que queima | se o modelo validar a concepção equivocada do aluno, a resposta é fundamentada, suficiente e **pedagogicamente nociva**. Nenhum dos dois eixos pega. |

Vale dizer com clareza: **o portão de suficiência não protege contra
resposta errada.** Ele cobre "faltou material". Responder com confiança à
pergunta errada, ou validar o erro do aluno, são modos de falha que
continuam fora do alcance de qualquer verificação determinística — e
continuam sendo trabalho de avaliação humana.

**Nenhuma das demais** (N1, N2, N3, N5, N6) testa suficiência: todas têm
corpus farto e devem produzir `GROUNDED` entregável.

**Nenhuma das 10 foi executada.**

---

# Decisões que dependem de você

1. **Vocabulário público de duas entradas** — `NO_ANSWER_FROM_CORPUS` e
   `TEMPORARILY_UNAVAILABLE` — é coarse o bastante, ou coarse demais?
2. **`UNDECLARED` entrega?** Recomendo que sim, sinalizado.
3. **Artefato de remoção bloqueia a entrega?** Recomendo que sim.
4. **Revisão humana das primeiras ocorrências de `DENIED`** antes de
   confiar no portão — concorda?
5. **Renomear `public_payload()` → `admin_payload()`** toca os dois
   objetos, o CLI e os testes existentes. Autoriza?
