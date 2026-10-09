# CÉREBRO — contrato de audiência e dois eixos, implementados

Fecha o desenho de
[cerebro-contrato-visibilidade-e-suficiencia.md](cerebro-contrato-visibilidade-e-suficiencia.md)
com a medição de
[cerebro-etapa-a-medicao-suficiencia.md](cerebro-etapa-a-medicao-suficiencia.md).

Nada alterado em retrieval, embeddings, corpus, top-k, `candidate_cap`,
thresholds, chunking, orçamento de contexto, política de `ANSWER_KEY`,
BM25F, fusão, qrels, Evaluation Set, Calibration Set, perguntas congeladas
ou política editorial/direitos. Nenhum ajuste de prompt.

## 1. A medição que veio antes do portão

Três execuções reais pré-registradas, **antes** de qualquer mudança de
comportamento.

| | `sufficient` bruto | correto? |
|---|---|---|
| N8 entalpia de formação do metano | `True` | sim — o material bastava |
| N9 hibridização sp³ | **`False`** | sim — o conceito não existe |
| N10 Diels-Alder | **`False`** | sim — nada no corpus |

Eu havia previsto que nenhuma abstiria. **Errado.** Primeiras ocorrências
de `false` do projeto, e as três concordaram com a realidade.

**N8 reescreveu o próprio teste.** O modelo devolveu −75 kJ/mol via lei de
Hess. Verifiquei: os três insumos (−394, −286, −891) estão no chunk citado;
o resultado não está em lugar nenhum do contexto. Foi derivação legítima,
não fabricação. Minha triagem por `ILIKE` media ausência **lexical**, e a
informação estava lá de forma **derivável**.

**`used_evidence` veio com colchetes nas três.** `['[E5]']`, `['[E6]']`,
`['[E3]', '[E4]']`. A correção de normalização atua em toda chamada real —
sem ela as três teriam saído `INVALID_EVIDENCE_REFERENCE`.

## 2. Os três contratos

### Interna — `GroundedAnswer`

Tudo. Nunca serializada diretamente. Ganhou `sufficiency`,
`answer_text_public`, `stripping_artifacts`, `raw_used_evidence`,
`normalized_used_evidence`, e as propriedades derivadas `grounding`,
`deliverable`, `delivery_block_reason`, `needs_human_review`.

### Pública — `PublicAnswer`

**Três campos. Os administrativos não existem nesta estrutura** — não é
filtro, é ausência. Filtro se esquece de atualizar.

`to_public()` é a única porta, e devolve `answer_text = None` para todo
estado não entregável. **Não há parâmetro que permita o contrário**: um
chamador não *consegue* publicar resposta não fundamentada.

### ADMIN — `admin_payload()`

O antigo `public_payload()`, renomeado porque o nome mentia sobre a
audiência. Agora carrega também a resposta crua **e** a pública lado a
lado, os dois eixos, a decisão de entrega e o bruto × normalizado.

## 3. A máquina de estados implementada

```
                      ┌─ sem evidência ────────────→ NO_EVIDENCE
                      ├─ degradado ────────────────→ DEGRADED_RETRIEVAL
  pergunta + contexto ┤                               (não chamam o provider)
                      └─ chama o provider
                           ├─ erro ────────────────→ PROVIDER_FAILED
                           ├─ fora do contrato ────→ PROVIDER_INVALID_RESPONSE
                           └─ resposta no contrato
                                ├── GROUNDING    (determinístico)
                                ├── SUFICIÊNCIA  (declarado)
                                └── ENTREGA      (derivada)
```

| eixo | valores |
|---|---|
| grounding | `GROUNDED` · `ANSWER_WITHOUT_CITATION` · `INVALID_EVIDENCE_REFERENCE` |
| suficiência | `SUFFICIENCY_AFFIRMED` · `SUFFICIENCY_UNDECLARED` · `SUFFICIENCY_DENIED` |

**Precedência de bloqueio:** grounding → suficiência → texto vazio →
sanitização.

| | `AFFIRMED` | `UNDECLARED` | `DENIED` |
|---|---|---|---|
| **`GROUNDED`** | entrega | entrega, sinalizado | **não entrega** |
| `ANSWER_WITHOUT_CITATION` | não | não | não |
| `INVALID_EVIDENCE_REFERENCE` | não | não | não |

`is_grounded` **preserva o significado antigo**. `INSUFFICIENT_EVIDENCE`
**não foi reutilizado** — já significa outra coisa nas migrações 013 e
029, e há teste que falha se alguém o introduzir aqui.

Motivo de bloqueio desconhecido **falha para o lado seguro**: a saída é
indisponível. Nunca entregar por omissão.

## 4. Exemplo real — entregável

Execução real de verificação, `N8`:

```json
{
  "answer_text": "A entalpia-padrão de formação do metano é aproximadamente −75 kJ/mol, calculada pela lei de Hess a partir das entalpias de combustão do carbono, do hidrogênio e do metano: (−394) + 2(−286) − (−891) = −75 kJ/mol.",
  "outcome": "ANSWERED",
  "unavailable_reason": null
}
```

O `AdminAnswer` correspondente:

| | |
|---|---|
| `status` / `grounding` | `GROUNDED` / `GROUNDED` |
| `sufficiency` | `SUFFICIENCY_AFFIRMED` |
| `deliverable` | `True` |
| `raw_used_evidence` | `['[E4]', '[E5]']` |
| `normalized_used_evidence` | `['E4', 'E5']` |
| `stripping_artifacts` | `[]` |
| citadas | `[E4]` SuperAção p.88 · `[E5]` Química no cotidiano p.240 |
| `excerpt` | `None` nas duas — `COMMERCIAL_REFERENCE` |

Repare: os marcadores saíram do texto público, e a matemática entre
parênteses `(−394) + 2(−286) − (−891)` ficou intacta.

## 5. Exemplo real — bloqueado

Execução real de verificação, `N9`:

```json
{
  "answer_text": null,
  "outcome": "UNAVAILABLE",
  "unavailable_reason": "NO_ANSWER_FROM_CORPUS"
}
```

| | |
|---|---|
| `status` / `grounding` | `GROUNDED` / `GROUNDED` |
| `sufficiency` | **`SUFFICIENCY_DENIED`** |
| `deliverable` | **`False`** |
| `delivery_block_reason` | `EVIDENCE_DECLARED_INSUFFICIENT` |
| `needs_human_review` | **`True`** |

**A resposta continua `GROUNDED` e `is_grounded = True`** — as citações
conferem. Só a entrega está bloqueada. É a separação dos eixos funcionando
num caso real.

`DENIED` reproduziu nesta chamada independente — **terceira observação**.

## 6. O defeito que os adversariais acharam

```
{"answer": "", "used_evidence": ["E1"]}
```

Corpo vazio, campo citando. Grounding legitimamente `GROUNDED`,
`strip_markers("")` sem artefato (correto — nada foi removido), e a saída
pública entregava `outcome = ANSWERED` com `answer_text = ""`.

**O sistema teria entregue uma resposta vazia como resposta
bem-sucedida.**

Corrigido em `delivery_block_reason`, não em `to_public`: "não sobrou
texto" é propriedade da resposta e precisa ser visível ao ADMIN como
motivo nomeado — `EMPTY_PUBLIC_ANSWER`, nome verificado como inexistente.

## 7. Provas de que nada vaza

**Teste de valor-sentinela.** Cada dado administrativo vira string única;
a exigência é que nenhuma apareça na serialização pública inteira. Pega o
vazamento aninhado sob chave renomeada, que conferência de nome não pega.

Com o par que lhe dá sentido: **um segundo teste confere que as sentinelas
estão no `admin_payload`**. Sem ele, o primeiro passaria por não haver o
que vazar.

Também verificado: nenhum `chunk_id`, `text_hash`, marcador, score, rank
ou nome dos 42 de `ADMIN_ONLY_FIELDS` alcança a saída pública; a estrutura
é rasa em todos os estados, então não há onde esconder.

### O limite, com teste próprio

Se o modelo copiar o título da obra ou um `chunk_id` para dentro da
resposta, isso sai — **a resposta É a saída pública**, e nenhuma camada
compara o texto gerado contra os metadados.

A defesa real está a montante e tem teste: **o prompt não carrega
`chunk_id` nem `text_hash`**. O modelo não pode citar o que nunca viu.

## 8. Mutação dirigida — 10 de 10

| mutação | testes que falham |
|---|---:|
| `to_public` ignora o bloqueio | 14 |
| `to_public` entrega o texto cru | 4 |
| `deliverable` ignora `DENIED` | 7 |
| `deliverable` ignora artefato | 3 |
| `deliverable` ignora resposta vazia | 1 |
| suficiência por veracidade em vez de identidade | 20 |
| `sanitize` perde a palavra regente | 6 |
| `sanitize` perde o colchete desbalanceado | 2 |
| `ADMIN_ONLY_FIELDS` esvaziado | 1 |
| razão pública deixa de colapsar | 8 |

A nona sobreviveu na primeira tentativa — **por defeito da mutação, não do
teste**: escrevi `frozenset(set()) or frozenset({...})`, e conjunto vazio
é falso, então nada mudou. Refeita, foi detectada.

## 9. Riscos e dívidas

**Risco 1 — o portão de suficiência repousa sobre n=3.** Três
observações de `DENIED` (N9, N10 e a reverificação de N9), todas corretas.
Nenhum falso positivo observado, mas também nenhuma medida de taxa. Por
isso toda ocorrência é marcada para revisão humana.

**Risco 2 — nenhum eixo protege contra resposta errada.** Grounding diz
"está apoiada no que citou"; suficiência diz "o modelo acha que bastava".
Uma resposta confiante no sentido errado da pergunta, ou que valida a
concepção equivocada do aluno, passa pelos dois. Isso é avaliação humana.

**Risco 3 — corrupção semântica na remoção.** `_REGENTES` é lista fechada
e falha para o lado seguro, mas fora dela um marcador gramaticalmente
necessário produz frase quebrada sem marca textual.

**Dívida 1 — a regra ADMIN não está exercida por rota.** Não há HTTP para
este caminho; o guarda estrutural quebra quando houver.

**Dívida 2 — preço em dólares desacoplado do modelo** (registrada antes,
não resolvida).

**Dívida 3 — N8 mostrou que triagem lexical não mede ausência
informacional.** Qualquer teste futuro de abstenção precisa considerar
derivabilidade, não só ocorrência de string.

## 10. Recomendação para o próximo portão

**Rodar N1–N7 antes de implementar qualquer coisa nova.**

O motivo é o mesmo que fez a Etapa A valer: N8–N10 mediram o sinal antes
de ele virar portão, e o resultado contrariou minha previsão. N1–N7
medem o que ainda é suposição — se o modelo adapta registro quando o aluno
pede (N2), se refuta concepção equivocada (N4), se sinaliza ambiguidade
(N7). Nenhuma dessas três tem mecanismo no sistema hoje.

Especificamente: **N1 e N2 são um par e precisam rodar em sequência.** São
o mesmo conteúdo com uma única variável — o pedido de adaptação. Isoladas
não dizem nada.

Custo das sete: cerca de US$ 0,005.

**Não recomendo** implementar adaptação de registro, correção de
concepção equivocada ou detecção de ambiguidade antes dessa medição. Seria
construir mecanismo para um comportamento que ninguém observou.
