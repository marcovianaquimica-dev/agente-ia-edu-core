# CÉREBRO MVP v1 — relatório de prontidão

Fechamento do núcleo de resposta fundamentada. Caminho principal
adotado: **`StructuredAnswerer`**.

Nada alterado em corpus, retrieval, embeddings, chunking, top-k,
`candidate_cap`, orçamento, `ANSWER_KEY`, política de direitos, qrels ou
conjuntos congelados.

---

## 1. Os oito itens de fechamento

| | item | estado |
|---|---|---|
| 1 | observação real de derivação | **feito** — `DERIVATION_VERIFIED` |
| 2 | 30 pares como regressão permanente | **feito** — 12 testes, 406 subtestes |
| 3 | vetores de fuga registrados e observáveis | **feito** — e um foi observado em campo |
| 4 | contrato ADMIN × aluno | **feito** — `structured_to_public` |
| 5 | não entregável nunca sai como válida | **feito** — com teste |
| 6 | observabilidade administrativa | **feito** |
| 7 | não alterar corpus/retrieval/congelados | **respeitado** |
| 8 | suíte completa e relatório | **feito** — `5277 passed, 0 failed` |

---

## 2. Item 1 — a derivação, observada

Pergunta **pré-registrada e commitada antes de rodar** (`18e8dc2`), uma
única chamada, sem repetição:

> Como se calcula a entalpia de formação de uma substância a partir das
> entalpias de combustão? Dê um exemplo numérico.

Resultado, **na primeira tentativa**:

```
DERIVATION_VERIFIED
expressão : 6 * (-393.5) + 6 * (-285.8) - (-2807.8)
declarado : -1268.0 kJ/mol
calculado : -1268.0
insumos   : E3 [SPAN_VERIFIED]  equação de combustão da glicose
            E2 [SPAN_VERIFIED]  ΔfH° do CO2
            E2 [SPAN_VERIFIED]  ΔfH° da H2O
```

Três insumos, **duas evidências complementares**, todos verificados por
substring no chunk que cada um declarou. A aritmética foi recomputada
pelo avaliador de AST e conferiu.

O valor confere com a literatura (−1.273 kJ/mol para a glicose); a
diferença vem do valor de combustão que o próprio livro traz.

**O mecanismo funciona ponta a ponta em chamada real.** Uma observação.

### O que esta mesma execução expôs

O número final — **−1.268,0 kJ/mol** — está num `CLAIM_CONNECTIVE`, que
por contrato **não exige suporte**:

| claim | tipo | suporte |
|---|---|---|
| [3] | `FACTUAL` | derivação verificada |
| **[4]** | **`CONNECTIVE`** | **nenhum — e carrega o número** |

O valor *é* respaldado pela derivação de claim[3], e os dois coincidem.
Mas a afirmação que o enuncia é de um tipo isento.

**É a primeira ocorrência real do vetor de fuga `META`/`CONNECTIVE`**, e
está registrada, não corrigida — conforme o item 3, que proíbe criar
heurística não validada para escondê-la.

---

## 3. O que está pronto

**Recuperação** (desde a Fase 6, intocada): vetorial isolada,
`candidate_cap` 2000, filtro editorial por propósito, cobertura
5.911/5.911, índice ANN, fingerprint `8471ea331d464904`.

**Montagem de contexto**: orçamento de 12.000 caracteres, seleção
determinística por rank, toda evidência incluída ou excluída com motivo
nomeado, direitos aplicados na fronteira do provider.

**Geração fundamentada, caminho principal**: afirmação como unidade de
suporte; cada evidência citada com span literal e papel; verificação
determinística por par `(evidência, span)`; derivação com insumos
ancorados e aritmética recomputada sem `eval`.

**Três eixos separados**, nenhum colapsado: grounding (determinístico),
suficiência (declarada), entrega (derivada).

**Contrato de audiência**: `PublicAnswer` de três campos; os campos
administrativos **não existem** nele. `structured_to_public` é a única
porta e devolve `answer_text = None` em todo estado não entregável.

**Normalização versionada** `SPAN_NORMALIZATION_V1`: cinco regras, cada
uma justificada por contagem no corpus de 5.945 chunks.

---

## 4. Limitações conhecidas

**Span verificado não é suporte semântico.** O código prova que o trecho
existe no chunk citado. Que ele *sustente* a afirmação foi confirmado
por humano em 30 de 30 pares — mas de 4 perguntas em que o caminho
funcionou. **Não há na amostra nenhum caso em que ele falhou, porque não
se procurou por eles.**

**Três vetores de fuga abertos por construção**, cada um com teste de
caracterização:

| | observado em campo? |
|---|---|
| span genérico (`"de"` passa) | não |
| conteúdo factual em `META`/`CONNECTIVE` | **sim — 1 vez** |
| derivação declarada não mecanizável | não |

**Taxa de erro desconhecida.** Nenhuma amostra não enviesada foi medida.

**Uma única observação de derivação.** Não é base para afirmar
confiabilidade.

**Sem rota HTTP.** O contrato ADMIN × aluno está implementado nos tipos,
mas nenhum endpoint o exercita. Há guarda estrutural que quebra quando a
rota aparecer.

**O preço em dólares é desacoplado do modelo** que o factory escolhe —
constantes fixas no CLI.

**`V-N8` fica divergente** por mudança de contrato, aguardando nova
adjudicação; não entra na regressão.

**A granularidade da afirmação é decisão do modelo**, não do contrato.
PC produziu 6/6 integrais, PB 0/7 — mesma qualidade, granularidades
diferentes.

---

## 5. Testes

**Suíte completa: `5277 passed, 11 skipped, 1206 subtests, 18m51s`,
exit 0, zero `FAILED`.**

Núcleo de resposta fundamentada, contados por arquivo:

| conjunto | n |
|---|---:|
| montagem de contexto | 18 |
| geração fundamentada (caminho antigo) | 26 |
| ponta a ponta com duble | 6 |
| verificação de span | 26 |
| resposta estruturada | 46 |
| adversariais estruturados | 21 |
| contrato público estruturado | 17 |
| **regressão dos 30 pares** | **12 testes, 406 subtestes** |
| sanitização | 35 |
| dois eixos | 29 |
| contrato público (caminho antigo) | 25 |
| adversariais (caminho antigo) | 33 |
| auditoria ponta a ponta | 28 |
| robustez | 32 |
| **total do núcleo** | **354** |

**Mutação dirigida: 11 de 11 detectadas** nos invariantes da âncora, e
10 de 10 nos do contrato de audiência.

### Um defeito meu, na própria etapa de fechamento

A primeira execução da suíte de fechamento quebrou **6 testes do
subsistema de redação** — nada a ver com o CÉREBRO. A causa era minha: o
arquivo de regressão dos 30 carregava o `.env` com
`os.environ.setdefault`, poluindo o processo inteiro do pytest. Os
testes de redação verificam que o provider **falha** sem
`OPENAI_API_KEY`; com a chave vazada, encontraram configuração e
pararam de falhar.

Reproduzido de forma determinística antes de corrigir: os testes de
redação sozinhos passam (29), e com o meu arquivo junto falham 6.

Corrigido lendo o `.env` para um dicionário **local**. O `conftest.py`
do projeto já fazia exatamente isso — eu não segui o padrão que estava
na minha frente.

Guarda acrescentado contra reincidência, por **árvore sintática**: a
primeira versão procurava a string no arquivo e se auto-detectou na
docstring que explica o defeito.

---

## 6. Custos medidos

**32 chamadas reais acumuladas, US$ 0,0249 no total**, média
US$ 0,00078.

| | Grounded | Structured |
|---|---:|---:|
| saída média (tokens) | 237 | **844** |
| custo por resposta | US$ 0,00065 | **US$ 0,00105** |
| latência de geração | 3,0 s | **7,2 s** |

**O caminho adotado custa +59% e é 2,4× mais lento.** Minha estimativa
de desenho era +16% — errei por supor +80% de tokens de saída quando
são +256%.

---

## 7. Decisões aguardando você

Nenhuma foi tomada. Todas são de produto ou arquitetura, e nenhuma
bloqueia o fechamento deste núcleo.

**1. `CLAIM_CONNECTIVE` pode carregar conteúdo factual?**
Observado uma vez em campo: o resultado numérico da derivação
(−1.268,0 kJ/mol) saiu numa afirmação de tipo isento de suporte. O valor
estava correto e respaldado pela derivação vizinha, mas o contrato não
liga os dois. Fechar isso é mudança de contrato.

**2. Reavaliar `V-N8` sob contribuição complementar.**
O rótulo histórico (`MISATTRIBUTED`) está preservado e o esperado pelo
contrato novo (`DERIVED`) está proposto em separado. Precisa de nova
adjudicação para voltar à regressão.

**3. Onde o caminho estruturado roda.**
A adoção de B foi decidida. Não foi decidido se ele roda em toda
pergunta ou só onde a fidelidade importa — e os +59% e 2,4× agora são
números medidos, não estimativa.

**4. Relaxar a sensibilidade a caixa.**
Medição: **0 `SPAN_CASE_MISMATCH` em 33 spans reais**. Não há pressão
empírica para relaxar. Fica como está até que haja.

**5. O span vai ao canal ADMIN, e span é literal de obra.**
É decisão de desenho que tomei e documentei — sem ele a verificação não
é auditável —, mas nunca foi aprovada explicitamente. A planilha de
adjudicação e a fixture seguiram a mesma lógica, com o arquivo marcado
como RESTRITO e o literal mantido fora do repositório.

---

## 8. Para a próxima fase, não iniciado

Na ordem que você fixou:

1. auditoria do sistema existente de ingestão/classificação de questões
2. desenho do Question Bank do CÉREBRO
3. contrato/API para consumo pelo Núcleo Edu 360
4. observabilidade administrativa do CÉREBRO

E as dívidas deste núcleo, que **não** são pesquisa de RAG:

- rota HTTP com `require_platform_admin` e os dois schemas
- preço acoplado ao modelo
- nova adjudicação de `V-N8`
- medir os vetores de fuga numa bateria maior
- decidir se o `CONNECTIVE` deve poder carregar conteúdo factual
