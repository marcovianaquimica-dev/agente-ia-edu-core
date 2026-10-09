# Diagnóstico C1 — por que a fase 2 (`alert_review_v1`) rejeitou `FUGA_AO_TEMA` em Sabrina/Henrique

**Task:** 10 (Fase C, Zero Gate) do plano `2026-10-06-motor-redacao-quality-zero-gate`.
**Script:** `scripts/diagnose_zero_gate_rejection.py` — reproduz isoladamente a
chamada de `alert_review_v1.build_prompt` + `build_text_provider().generate(...)`
para os dois casos reais, com os `alerts` (código + `detail` textual) exatamente
como a fase 1 os levantou.

## O que foi executado

5 execuções independentes do script (sem `seed`, igual ao código-fonte do
Step 1 do brief — ver nota sobre `seed` abaixo), cada uma chamando a API real
uma vez por caso (Sabrina, Henrique).

| Execução | Sabrina `confirmed_alert_codes` | Henrique `confirmed_alert_codes` |
|---|---|---|
| 1 | `["FUGA_AO_TEMA"]` (confirmado) | `["FUGA_AO_TEMA"]` (confirmado) |
| 2 | `["FUGA_AO_TEMA"]` (confirmado) | `["FUGA_AO_TEMA"]` (confirmado) |
| 3 | `[]` (rejeitado) | `["FUGA_AO_TEMA"]` (confirmado) |
| 4 | `[]` (rejeitado) | `[]` (rejeitado) |
| 5 | `[]` (rejeitado) | `["FUGA_AO_TEMA"]` (confirmado) |

**Sabrina: 2/5 confirmado, 3/5 rejeitado. Henrique: 4/5 confirmado, 1/5
rejeitado.** Nenhum dos dois casos é determinístico — nenhum dos dois é
"sempre rejeita".

### `reasoning` real obtido (amostras representativas)

Confirmação (execução 1, Sabrina):
> "Confirmado, pois a justificativa informa que o texto trata principalmente
> de relações familiares, sem desenvolver em nenhum momento a valorização da
> pessoa idosa nem o enfrentamento do preconceito etário. Assim, não há
> indicação de abordagem sequer parcial do tema proposto."

Rejeição (execução 3, Sabrina):
> "FUGA_AO_TEMA rejeitado: a justificativa afirma que o conteúdo trata
> principalmente de relações familiares, mas não demonstra com rigor que o
> tema da pessoa idosa e do preconceito etário não tenha sido abordado em
> nenhum momento. Como a fuga total exige alto grau de certeza, a informação
> apresentada não é suficiente para confirmar o alerta."

Rejeição (execução 4, Henrique):
> "O alerta FUGA_AO_TEMA não deve ser confirmado, pois a justificativa apenas
> afirma que a redação não desenvolve o tema, mas não apresenta qualquer
> evidência textual para sustentar que houve fuga total tanto do tema
> específico quanto do assunto amplo relacionado à velhice."

Confirmação (execução 4, Henrique, no mesmo lote):
> "O alerta é confirmado porque a justificativa afirma que a redação não
> desenvolve nem o tema específico da valorização da pessoa idosa e do
> preconceito etário nem o assunto amplo relacionado à velhice. Isso
> caracteriza fuga total ao tema, conforme exigido pela regra."

As quatro justificativas leem **a mesma frase de `detail`** de formas opostas:
a mesma oração "trata principalmente de X, sem desenvolver Y" é, em uma
rodada, lida como "não há indicação de abordagem sequer parcial" (confirma) e,
na rodada seguinte, como "não demonstra com rigor... não é suficiente para
confirmar" (rejeita) — sem nenhuma mudança no texto de entrada entre as
chamadas.

## Classificação contra as 3 hipóteses

**H1 (prompt interpretado mais estrito na prática que o texto sugere) —
PARCIALMENTE CONFIRMADA, mas a causa real é inconsistência de amostragem do
modelo em torno de uma ambiguidade genuína do próprio texto do alerta, não
um prompt mal calibrado em uma única direção fixa.**

A regra do `alert_review_v1._RULES_REVIEW` exige fuga *TOTAL* ("nem o assunto
mais amplo nem o tema específico foram desenvolvidos em NENHUM momento") e
instrui explicitamente "em caso de dúvida razoável, REJEITE o alerta". O
`detail` real de Sabrina diz "trata **principalmente** de mulher, marido,
filhos... **sem desenvolver** o tema". A palavra "principalmente" é uma
cobertura textual genuína para uma leitura de "não é bem 100% total" — e o
modelo, variando apenas por ruído de amostragem (o provider roda a
temperatura padrão 1, não há `temperature=0` disponível — ver
`providers/models.py`, `TextGenerationRequest.seed` docstring: "confirmed
live... the model... rejects any value other than its default (1) with a 400
error"), ora encontra essa "dúvida razoável" ora não. Quando encontra, a
própria regra do prompt ("REJEITE") converte essa dúvida em rejeição
imediata — não é um prompt "sempre mais severo", é um prompt cujo critério
estrito transforma qualquer variação aleatória de leitura em uma mudança
binária de resultado (confirma/rejeita), porque não há meio-termo permitido
na saída.

**H2 (evidência perdida entre fase 1 e fase 2) — CONFIRMADA COMO DESCARTADA**,
por leitura direta de código, exatamente como a nota da spec antecipava:
- `essay_correction.py::_review_anula_redacao_alerts` (linhas 897-902) constrói
  `candidates` com `{"code": alert.code, "detail": alert.detail}` — o `detail`
  completo da fase 1, sem truncamento ou resumo.
- `alert_review_v1.py::build_prompt` (linhas 110-113) itera `alerts` e escreve
  `f"- {a['code']}: {a['detail'] or '(sem justificativa fornecida)'}"` — o
  texto completo do `detail` é injetado literalmente no prompt enviado ao
  modelo.
- O script de diagnóstico confirma isso na prática: os dois `detail`s
  completos de Sabrina e Henrique aparecem, palavra por palavra, nos
  `reasoning` retornados pelo modelo (tanto em confirmações quanto em
  rejeições) — a fase 2 está lendo exatamente a mesma frase que a fase 1
  escreveu.

**H3 (bug de código em como candidates/confirmed são montados) — DESCARTADA**,
por leitura direta de `_review_anula_redacao_alerts` (linhas 897-926):
`candidates` filtra corretamente por `_ANULA_REDACAO_ALERT_CODES`;
`confirmed = set(payload["confirmed_alert_codes"])` seguido de
`confirmed &= candidate_codes` nunca permite que a revisão amplie o
conjunto (só pode estreitar, como o docstring promete); `passthrough_codes =
all_codes - candidate_codes` preserva alertas não-ANULA_REDACAO
inalterados. Nenhuma dessas operações descarta ou corrompe o veredito real
do modelo — quando o modelo retorna `["FUGA_AO_TEMA"]`, o código final
confirma `FUGA_AO_TEMA`; quando retorna `[]`, o código final rejeita. O
resultado publicado é fiel ao `confirmed_alert_codes` que a IA de fato
devolveu em cada chamada.

## Consistência entre execuções

**Inconsistente, não determinística**, para os dois casos. Nenhuma das duas
redações rejeita 5/5 nem confirma 5/5 — Sabrina rejeitou em 3 das 5
execuções e Henrique em 1 das 5. A rejeição observada em produção (que
motivou esta investigação) é compatível com uma amostra desfavorável desse
mesmo processo não-determinístico, não com uma regra de negócio ou bug que
rejeite esses casos de forma previsível.

## Nota sobre `seed`

O código de produção (`_review_anula_redacao_alerts`, linha ~910) chama o
provider com `seed=_CORRECTION_SEED` (um valor fixo), enquanto o script deste
diagnóstico (Step 1 do brief, mantido como especificado) chama sem `seed`.
Isso não muda a conclusão: o próprio motivo de existir do `alert_review_v1`
(ver seu module docstring, linhas 17-29) é que, em produção, **com o mesmo
`seed` fixo**, chamadas idênticas já haviam oscilado (C1 variando 0→80 em
4 chamadas idênticas de fase 1). `seed` no provider atual não é garantia de
determinismo total — é best-effort. A inconsistência que este diagnóstico
reproduz sem `seed` é a mesma categoria de inconsistência que o próprio
histórico do projeto já documentou existir mesmo com `seed` fixo.

## O que isso significa para a Task 11

O problema não é "o prompt do `alert_review_v1` está sempre errado" nem "a
evidência não chega" nem "há um bug óbvio no código de merge" — é que a
regra "em caso de dúvida razoável, REJEITE", combinada com um critério de
fuga *total* e uma frase de `detail` que usa linguagem de grau
("principalmente"), dá ao modelo margem para uma leitura binária
inconsistente de uma mesma evidência textual fixa. Qualquer correção da
Task 11 que dependa de reescrever o prompt para ser "menos estrito" ou "mais
estrito" em uma única direção não vai resolver uma inconsistência que é de
amostragem, não de calibração direcional.

**Importante para a Task 11 — o único precedente deste codebase para essa
classe de inconsistência já foi tentado aqui e não bastou.** O que de fato
corrigiu a inconsistência em `competency_scoring_v1`/`v2` (ver seu module
docstring, linhas 12-24) **não foi voto de maioria nem consenso entre
múltiplas amostras da mesma decisão** — não há nenhum mecanismo de votação
ali. O que corrigiu foi **estreitamento arquitetural**: trocar UMA chamada
holística que decidia as cinco competências de uma vez (mais
anotação-hunting, mais a devolutiva inteira, tudo no mesmo generation) por
**N chamadas independentes, uma por competência, cada uma vendo só a
evidência já extraída daquela competência, rodando concorrentemente**. O
docstring registra que isso fez o modelo responder identicamente em 5/5
chamadas repetidas — mas esse 5/5 é consequência de ter estreitado a decisão
a um único julgamento bem delimitado, não de ter agregado várias amostras
daquele julgamento.

E `alert_review_v1` — a própria chamada diagnosticada nesta task — **já é
uma instância desse mesmo padrão de estreitamento**, aplicada deliberadamente
a este problema: seu module docstring (linhas 17-29) cita explicitamente o
precedente de `competency_scoring_v1` como a inspiração ("The same
architecture applies: a small, focused, single-decision call..."). Ou seja,
o único approach comprovado neste codebase para este tipo de inconsistência
já foi tentado exatamente aqui, e os 5 runs deste diagnóstico mostram que,
sozinho, ele não produziu consistência para Sabrina/Henrique.

Voto de maioria/consenso entre múltiplas amostras da mesma decisão binária é
uma ideia que vale considerar para a Task 11 avaliar — mas é uma ideia **sem
precedente validado neste codebase**, não uma extensão de algo que já
funcionou aqui. Outra direção a considerar: uma reformulação da pergunta que
não force uma decisão binária sobre uma evidência inerentemente graduada
(a própria causa da inconsistência observada).

## Reprodutibilidade

```bash
python scripts/diagnose_zero_gate_rejection.py
```

Repetir a chamada várias vezes deve continuar mostrando confirmações e
rejeições alternadas para o mesmo par de casos — esse é o comportamento
esperado e diagnosticado aqui, não uma falha do script.
