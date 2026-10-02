# CÉREBRO — auditoria do caminho ponta a ponta

Revisão estática + testes automatizados de:

```
pergunta → embedding → VectorSearcher → ContextBuilder
        → GroundedAnswerer → validação de evidências → saída
```

Testes em [test_knowledge_end_to_end_audit.py](../tests/test_knowledge_end_to_end_audit.py)
(24 testes) e [test_knowledge_robustness.py](../tests/test_knowledge_robustness.py)
(32 testes). Nenhuma chamada paga.

## Resultado por item auditado

| item | veredito |
|---|---|
| literal comercial nunca em saída pública | **garantido por desenho**, com ressalva ↓ |
| usuário comum não recebe referências/fontes | **verdadeiro por ausência de rota** |
| só ADMIN vê rastreabilidade/fontes | **não implementado — não existe rota** |
| logs públicos não vazam `raw_text` | **verdadeiro por ausência de log** |
| marcador inválido falha fechado | **garantido** |
| normalização `[E1]`/`E1` coberta | **garantido** |
| `INSUFFICIENT_EVIDENCE` falha fechado | **parcial — ver decisão em aberto** |
| modo degradado observável | **garantido** |
| custo/tokens/tempos auditáveis | **garantido** |
| política de direitos não enfraquecida | **garantido** |

## 1. Literal comercial

Garantido nos caminhos do sistema. `ContextEvidence` **não tem campo**
`raw_text` — não é que venha vazio, ele não existe. `BuiltContext._blocks`
é `repr=False`. `GroundedAnswer._raw` é `repr=False`. `public_payload()`
dos dois objetos é limpo. O CLI monta o relatório a partir de
`public_payload()` e menciona `raw_text` **uma única vez**, no `select`
que alimenta o construtor — há teste estático que falha se surgir uma
segunda menção.

### Logs: não vazam porque não existem

Nenhum módulo de `knowledge_engine` escreve log — não há `logging`, não há
`logger`, não há `print`. O literal não vaza em log porque não há log.

Há guarda estrutural (`NoLoggingYetTests`) que falha quando o primeiro log
aparecer. Não é para proibir logging: é para que quem o introduzir decida
explicitamente o que pode ser registrado. `repr` limpo **não protege**
contra um `logger.debug(texto_do_chunk)`.

### Ressalva: o modelo pode copiar o literal para a resposta

Se o provider reproduzir literalmente um trecho comercial dentro de
`answer`, esse trecho sai. A resposta **é** a saída pública, e nenhuma
camada compara o texto gerado contra o literal das evidências.

O desenho impede o vazamento pelos caminhos do sistema — `excerpt`,
`repr`, serialização, log. Não impede o modelo de reproduzir o que
recebeu. Corrigir exige decidir o que fazer ao detectar: truncar, recusar
ou marcar. **Decisão de produto.** Registrado com teste de caracterização
(`test_characterises_the_model_echoing_commercial_literal`), não corrigido.

## 2. A regra ADMIN — o que falta, exatamente

**Não há rota HTTP para o caminho de geração fundamentada.** Verificado por
AST: nenhum módulo sob `src/agente_ia_edu/api/` importa `context_builder`
ou `grounded_answer`. Os únicos consumidores são `scripts/cerebro_ask.py` e
os testes.

O mesmo vale para o `VectorSearcher`: nenhum consumidor HTTP, só CLI e
testes. A recuperação **lexical** é a única exposta, em
`knowledge_lexical.py`, e já é ADMIN-only com a divisão de schema por
direitos.

Portanto "usuário comum não recebe fontes" é hoje verdadeiro **por ausência
de rota**, não por regra implementada. Não é vulnerabilidade; é funcionalidade
inexistente.

Há um **guarda estrutural** que falha de propósito no dia em que alguém
expuser o caminho:
`NoUnauthenticatedExposureTests::test_no_api_module_imports_the_generation_path`.
A mensagem de falha diz para substituí-lo por teste real de autorização,
não para relaxá-lo.

### A convenção que já existe

As **15** rotas de `knowledge_engine.py` (11) e `knowledge_lexical.py` (4)
exigem `require_platform_admin` — há teste que percorre os decoradores por
AST e verifica rota a rota. A detecção por AST bate exatamente com a
contagem bruta de decoradores nos dois arquivos (11/11 e 4/4), então o
teste não passa por vacuidade.

E os direitos já são tratados **no tipo**, não em `if`:

```python
if hit.quotable:
    return QuotableLexicalHitResponse(excerpt=hit.excerpt, **common)
return LexicalHitResponse(**common)
```

Schema sem campo de texto, em vez de campo de texto preenchido com `None`.

### Contrato mínimo proposto — para sua revisão, não implementado

A regra pedida ("fontes e referências visíveis somente para ADMIN") é mais
forte que a convenção atual, porque prevê **dois públicos** para a mesma
resposta. Proposta mínima, seguindo o padrão já estabelecido:

**Dois schemas, não um com campos opcionais.**

| | `GroundedAnswerResponse` (aluno/professor) | `GroundedAnswerAdminResponse` |
|---|---|---|
| `answer` | ✓ | ✓ |
| `status`, `is_grounded` | ✓ | ✓ |
| `model_says_sufficient` | ✓ | ✓ |
| `degraded`, `degradation_reasons` | ✓ | ✓ |
| `cited_markers` | ✓ | ✓ |
| `cited_evidences` (fonte, página, `chunk_id`, `text_hash`) | **ausente** | ✓ |
| `available_markers` | **ausente** | ✓ |
| `invalid_markers` | **ausente** | ✓ |
| `input_tokens`, `output_tokens` | **ausente** | ✓ |
| `excerpt` | **ausente** | ✓ se `quotable` |

Três pontos que precisam da sua decisão, e que eu não resolveria sozinho:

1. **Se o usuário comum não vê fonte, o que a resposta vale?** Uma resposta
   sem procedência é exatamente o que a fundamentação existe para evitar.
   Opção intermediária: mostrar **título da obra e página** sem `chunk_id`,
   `text_hash`, score nem `excerpt`. Isso dá procedência verificável sem
   expor o aparato interno. Precisa de decisão sua.

2. **Marcadores no corpo da resposta.** O texto contém `[E1]`, `[E3]`. Se o
   aluno não recebe a lista de evidências, os marcadores viram ruído. Ou se
   removem do texto público, ou se mantém a lista mínima do item 1.

3. **Status para o usuário comum.** `INVALID_EVIDENCE_REFERENCE` e
   `PROVIDER_INVALID_RESPONSE` são diagnósticos internos. Provavelmente o
   usuário comum deveria ver só "resposta disponível" ou "não foi possível
   responder com segurança", com o estado detalhado no canal ADMIN.

Nada disso foi implementado.

## 3. O portão de insuficiência — decisão em aberto

Não existe estado `INSUFFICIENT_EVIDENCE`. Os estados são `NO_EVIDENCE`,
`DEGRADED_RETRIEVAL`, `ANSWER_WITHOUT_CITATION`,
`INVALID_EVIDENCE_REFERENCE`, `PROVIDER_FAILED`,
`PROVIDER_INVALID_RESPONSE` e `GROUNDED`.

Os dois primeiros falham fechados e **sequer chamam o provider**. Os três
seguintes falham fechados depois da chamada. Tudo isso está coberto.

**O buraco:** o modelo pode declarar `sufficient: false`, citar
corretamente, e a resposta sair `GROUNDED` com `is_grounded = True`.

Precisão importa aqui, e a primeira versão deste documento errava: o sinal
**não é silencioso**. Nenhum ramo do *serviço* o consulta — os três ramos de
`GroundedAnswerer` são marcador inválido, nenhum marcador, e caso contrário
fundamentada —, mas o CLI imprime, no item 10:

> `ATENCAO: o modelo declarou as evidencias INSUFICIENTES mesmo citando-as.`

Ou seja: **o sinal é consultivo, não vinculante.** Quem lê o relatório vê o
aviso; quem consumir `is_grounded` programaticamente, não. É essa segunda
porta que está aberta, e é o consumidor programático que ainda não existe.

Nota de nomenclatura: `INSUFFICIENT_EVIDENCE` **já existe no projeto** com
outro significado — `evidence_state` do domínio de mastery (migração 029) e
`status` do diagnóstico inicial (migração 013). Se um estado novo for criado
aqui, o nome precisa ser outro, ou haverá dois conceitos distintos com o
mesmo rótulo.

Coberto por `SufficiencyGateTests::test_characterises_sufficient_false_with_valid_citations`.
**Não corrigido**: fazer `sufficient: false` fechar o portão muda o
comportamento do sistema.

Vale notar que nos sete pilotos reais `sufficient` veio `True` em todas as
execuções com JSON — inclusive naquela em que a pergunta era o texto de
exemplo. O sinal pode ser pouco confiável, o que é mais um argumento para
a decisão ser deliberada e não automática.

## 4. Robustez — 32 casos de borda, nenhum defeito

Varredura com `FakeProvider` sobre entrada malformada. **O sistema tratou
todos corretamente**; nenhuma correção foi necessária.

Cobertos: marcador inexistente, duplicado no corpo, duplicado no campo,
duplicado inválido, com e sem colchetes, com espaços, minúsculo, fora de
faixa (`E0`, `E4` com 3 evidências, `E999`); resposta sem marcador, vazia,
só com espaços; contexto vazio, com evidência única, comercial sozinha;
orçamento exatamente no limite, um caractere acima, evidência maior que o
orçamento em primeira e em segunda posição, reabastecimento após corte,
marcadores sem lacuna quando um hit do meio cai; direitos misturados numa
mesma montagem; `used_evidence` como string, com `None`, inteiro, dict e
lista aninhada; `answer` nulo; JSON lista, JSON string, corpo vazio, corpo
não-JSON; timeout e indisponibilidade do provider.

Dois comportamentos ficaram registrados como **caracterização**, não como
aprovação:

- **`answer: ""`** cai em `ANSWER_WITHOUT_CITATION`. Falha fechada, mas o
  nome informa mal: o problema foi a ausência de resposta, não de citação.
- **Hit sem texto em `texts`** entra assim mesmo, recebe marcador e gasta
  orçamento com cabeçalho sem conteúdo. Em produção isso significa bug a
  montante, não entrada legítima — mas excluir com motivo nomeado seria
  mudança de comportamento.

## 5. Direitos — não enfraquecidos

As três zonas são perguntas distintas e testadas como tal:
`may_use_literal_in_processing` e `may_send_literal_to_provider` devolvem
`True` para `COMMERCIAL_REFERENCE`; `may_expose_literal_text` devolve
`False`. As três **falham fechadas** para classe desconhecida e para string
vazia. `max_excerpt_chars("COMMERCIAL_REFERENCE")` é `None` — não há
orçamento de excerpt, não um orçamento igual a zero.

Fonte cuja classe não autorize o envio é **excluída com motivo nomeado**
(`RIGHTS_NO_PROVIDER`), não incluída sem texto.

Rastreabilidade é idêntica para as duas classes: direitos governam o
**texto**, nunca a procedência.

## Pendências registradas, não implementadas

1. Contrato ADMIN / usuário comum — depende de três decisões suas.
2. `sufficient: false` não fecha o portão.
3. Modelo pode ecoar literal comercial na resposta.
4. `raw_used_evidence` / `normalized_used_evidence` no relatório.
5. `answer: ""` com estado de nome impreciso.
6. Hit sem texto entra no contexto.
