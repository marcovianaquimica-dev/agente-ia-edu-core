# Motor de correção ENEM — Quality Gate, Zero Gate e calibração - Design

## Contexto

Corrigimos 30 redações reais (material usado para treinar futuros
corretores do ENEM) com o motor de produção e comparamos contra um
gabarito oficial nota-a-nota. Até então a calibração só tinha sido
testada com redações sintéticas; este foi o primeiro teste com dado real
e referência externa. Resultado: dois problemas estruturais confirmados,
não apenas ruído de amostra.

**Falso zero por confusão entre "difícil de ler" e "insuficiente".**
Duas redações (Larissa, João Miguel) foram zeradas pelo alerta
`TEXTO_INSUFICIENTE`, mas o gabarito diz que valem 360 cada. O texto das
duas é genuinamente corrompido (ortografia impossível, palavras
quebradas - "ideho" por "idoso", "4 BGE" por "IBGE"), não curto. Auditoria
confirmou a causa raiz (`essay_prompts/v15.py:214-220`): `TEXTO_INSUFICIENTE`
é definido só por comprimento/completude, sem nenhuma instrução que
distinga um texto corrompido de um texto genuinamente curto - e não existe,
em lugar nenhum do pipeline, um sinal de confiabilidade da ENTRADA
independente do julgamento pedagógico. `OCR_DUVIDOSO` (que dispara em
28/30 redações) não é a causa: está deliberadamente fora do único
mecanismo de zeragem (`_ANULA_REDACAO_ALERT_CODES`,
`essay_correction.py:154-158`) e o próprio prompt o trata como a opção
segura, não-zeradora (`v15.py:253-256`). O problema é a ausência de um
terceiro conceito: "não consigo avaliar com confiança" é hoje forçado a
virar `TEXTO_INSUFICIENTE` (zera como se fosse o aluno) ou `OCR_DUVIDOSO`
(não tem efeito nenhum) - nunca um estado próprio.

**Zero Gate que só confirma, nunca descobre.** O gabarito zera as últimas
10 redações por "situação especial" (fuga ao tema, anulação, tipo textual
incorreto, cópia, parte desconectada). O motor zerou corretamente 1 das
10. Investigação caso-a-caso das 9 restantes mostrou: nenhuma é cópia.
Duas (Sabrina, Henrique) são fuga ao tema clara e bem evidenciada - a
fase 1 (`_run_ai`) já levantou `FUGA_AO_TEMA` com justificativa específica
citando o conteúdo real (confirmado lendo `ai_output.alerts` das duas no
banco), mas a fase 2 (`alert_review_v1.py`, `_review_anula_redacao_alerts`
em `essay_correction.py:799-853`) rejeitou as duas e a redação seguiu
para pontuação comum. A causa arquitetural: a fase 2 só pode **confirmar
ou rejeitar** um candidato que a fase 1 já levantou (`confirmed &=
candidate_codes`, nunca `|=`) - nunca pode levantar um alerta que a fase 1
não viu, e é deliberadamente conservadora ("em caso de dúvida, rejeite").
As outras 7 redações perdidas são texto severamente corrompido (mesmo
padrão de Larissa/João Miguel) que não chegou a disparar
`TEXTO_INSUFICIENTE` - mesma causa raiz do problema anterior.

**Viés sistemático para baixo em C1-C5 nas 20 redações válidas** (MAE
total ~207/1000; por competência C1=72, C4=51, C2=44, C3=43, C5=35).
Duas assimetrias concretas e já lidas no código: `_RULES_TOP_BAND`
(`competency_scoring_v2.py:105-107`) - "na dúvida genuína, prefira 160" -
só empurra para baixo, nunca para cima; e o teto de `TANGENCIAMENTO_AO_TEMA`
em C3/C5 (`essay_correction.py:214-222`) penaliza sob incerteza sem
contrapartida. O código já tem contramedidas específicas para C1
(`_RULES_C1_CALIBRATION` em v15.py, `_RULES_C1_RATIONALE_CAVEAT` em
competency_scoring_v2.py) e, ainda assim, C1 teve o maior erro - sinal de
que a causa pode não ser só a regra: 28/30 redações têm texto
corrompido sendo lido por C1 (norma padrão) como se fossem erros reais do
aluno. Não sabemos ainda quanto de cada.

## O que já existe e não muda

- O contrato de saída (`essay_engine_contract/v5.py`), a rubrica
  versionada em dado (`db/models/essay_rubric.py` +
  `rubrics/enem_2025.yaml`) e os providers de IA permanecem como estão -
  este design estende o contrato (nova versão) e adiciona uma nova regra
  de política, nunca reescreve o que já funciona.
- O mecanismo de confiança de OCR para fotos/PDF em
  `essay_submission.py` (`_OCR_ATTEMPTS`, `_MIN_AVERAGE_CONFIDENCE`,
  `_reconcile_low_confidence_tokens`) já calcula um sinal real de
  confiança por token - hoje nunca usado para decidir nada
  ("explicit product decision: sempre transcrever, independente da
  qualidade"). Este design REAPROVEITA esse sinal já calculado, não o
  recalcula.
- A estrutura de duas fases de `_run_ai` (fase 1: uma chamada que
  retorna scores + alerts + evidence; fase 2: chamadas concorrentes de
  refinamento) é preservada como padrão geral - o que muda é a ORDEM e a
  RESPONSABILIDADE de uma parte específica da fase 2 (seção Fase C).
- `_apply_deterministic_scoring_rules` como o único lugar que decide
  pontos finais a partir de alertas confirmados continua sendo o ponto
  único de verdade - ganha um objeto de decisão explícito em vez de um
  cruzamento implícito com um `frozenset`.
- O modo FORMATIVO/AVALIATIVO e `_apply_review_policy` não mudam.

## Escopo

**Dentro:**
- **Fase A** - benchmark de calibração versionado (fixture das 30
  redações + metadados de proveniência) e baseline congelado do motor
  atual, antes de qualquer mudança.
- **Fase B** - Quality Gate: separar tecnicamente "entrada confiável" de
  "mérito pedagógico", unificado entre texto digitado e imagem/PDF.
- **Fase C** - Zero Gate: reestruturar a detecção de situação especial
  para poder descobrir, não só confirmar, com decisão auditável.
- **Fase D** - experimento controlado (texto corrompido vs. limpo) e,
  só com esse dado em mãos, recalibração pontual das regras assimétricas
  já identificadas.
- Relatório consolidado final comparando as 4 fases.

**Fora de escopo (registrado, não bloqueia):**
- Política de cópia do texto motivador: a interpretação atual (não
  zera, só limita C3 a 80 pontos, conforme `essay_prompts/v13.py:31-45`
  e a cartilha do INEP) fica como está. Nenhum dos 9 casos investigados
  exige mudança aqui. Fica registrado como `normative_divergence`
  pendente no benchmark (Fase A), para decisão futura com casos reais.
- Qualquer mudança ao modelo/provider de IA.
- Qualquer tentativa de reproduzir exatamente as 30 notas do gabarito
  (não é o critério de sucesso - ver Fase D).

## Fase A — Benchmark de calibração + baseline congelado

### Por que duas "verdades" distintas

O gabarito das 30 redações é uma referência externa, não uma norma
absoluta - a própria Fase C descobriu que seu critério de "situação
especial" não necessariamente bate com a norma do INEP já codificada
neste projeto (cópia). O benchmark precisa guardar isso explicitamente
em vez de tratar todo valor de referência como alvo a ser reproduzido a
qualquer custo.

### Fixture

Novo arquivo `tests/fixtures/essay_calibration_benchmark_v1.json`, uma
lista de 30 entradas:

```json
{
  "student_ref": "aluno_01",
  "body_text": "...",
  "expected_scores": {"C1": 160, "C2": 200, "C3": 200, "C4": 200, "C5": 200, "total": 960},
  "expected_special_situation": null,
  "normative_status": "CONFIRMED",
  "normative_divergence": null,
  "reference_source": "material_30_alunos_treinamento_corretores_enem",
  "reference_version": "2026-10-06"
}
```

Para os casos de situação especial (hoje 10):

```json
{
  "student_ref": "aluno_21",
  "body_text": "...",
  "expected_scores": {"C1": 0, "C2": 0, "C3": 0, "C4": 0, "C5": 0, "total": 0},
  "expected_special_situation": {"category": "SITUACAO_ESPECIAL_NAO_ESPECIFICADA", "evidence_note": "gabarito nao detalha qual das 5 hipoteses se aplica"},
  "normative_status": "UNVERIFIED",
  "normative_divergence": "gabarito marca zero sem especificar a hipotese exata (copia/fuga/anulacao/tipo textual/parte desconectada); 7 destas 9 redacoes sao texto severamente corrompido sem padrao claro de copia - tratar como hipotese pendente de confirmacao, nao como norma validada",
  "reference_source": "material_30_alunos_treinamento_corretores_enem",
  "reference_version": "2026-10-06"
}
```

`normative_status` é um de `CONFIRMED` (bate com a norma já codificada
neste projeto), `DIVERGENT` (o benchmark pede um resultado que a norma
atual explicitamente não produziria - ex.: zerar por cópia) ou
`UNVERIFIED` (não dá para confirmar só pelo texto, caso dos 9 "situação
especial" sem categoria especificada). O script de benchmark (abaixo)
NUNCA trata um caso `DIVERGENT` como erro do motor a corrigir - reporta
separado, sem contar no MAE/precision/recall.

`student_ref` substitui o nome real por um identificador opaco no
arquivo de fixture (o nome completo seria dado pessoal real sem
necessidade nenhuma de estar num arquivo versionado do repositório); um
arquivo paralelo, não versionado
(`tests/fixtures/essay_calibration_benchmark_v1.local.json`, adicionado
ao `.gitignore`), mapeia `student_ref -> nome real` só para quem rodar o
benchmark localmente.

### Script de benchmark

Novo `scripts/essay_calibration_benchmark.py`:

- Cria, a cada execução, uma escola/turma/proposta **descartáveis e
  isoladas** (nome com o `run_id` embutido) - nunca reaproveita "Escola
  ABC" nem qualquer dado de outra sessão/teste manual rodando no mesmo
  banco de desenvolvimento.
- Materializa as 30 `EssaySubmission` (`SUBMITTED`, `TEXT_OFFSET`,
  `canonical_text` via `normalize_essay_text`), mesmo padrão já usado
  manualmente nesta investigação.
- Roda `EssayCorrectionService.correct()` em cada uma, sequencialmente
  (chamadas reais de IA - sem paralelismo, para não estourar rate limit).
- Para cada entrada com `normative_status != DIVERGENT`, calcula:
  `|ia - oficial|` por competência e total; viés com sinal
  `mean(ia - oficial)` por competência e total (não só erro absoluto -
  é o que distingue tendência sistemática de ruído); distância em
  "níveis oficiais" (`|ia - oficial| / 40`, já que as bandas andam de
  40 em 40).
- Para os 10 casos de situação especial: recall do Zero Gate (quantos
  dos realmente especiais o motor zerou) e, sobre os 20 válidos,
  precision (quantos zeros do motor são falsos positivos).
- Conta `NEEDS_REVIEW`, taxa de `OCR_DUVIDOSO`, e lista separadamente
  os casos `DIVERGENT`/`UNVERIFIED` com o resultado do motor ao lado
  (não entra em nenhuma média).
- Grava um relatório JSON com todos os campos acima mais os campos de
  versão (`rubric_version`, `prompt_version`, `engine_version`,
  `contract_version`, e os dois novos abaixo) e um resumo em texto.

### Baseline congelado

Antes de qualquer mudança de código, rodar o script contra o motor
ATUAL e salvar o resultado completo (por redação: scores, alertas,
decisão de zero, sinal de OCR, mais todas as versões) em
`tests/fixtures/essay_calibration_baselines/CALIBRATION_BASELINE_V1.json`,
committado. Esse arquivo nunca é regerado por re-execução automática -
é a fotografia do "antes", usada só para comparação nos relatórios das
Fases B/C/D.

## Fase B — Quality Gate

### Princípio formal

**Falha ou baixa confiabilidade da entrada não é deficiência do aluno.**
`OCR_DUVIDOSO`, baixa legibilidade, corrupção textual, caracteres
incoerentes ou qualquer sinal técnico semelhante não podem, isoladamente,
produzir nota zero pedagógica. O Quality Gate decide apenas se existe
informação suficiente e confiável para o julgamento pedagógico ser
feito - nunca julga a redação em si.

### Separação de domínio: confiabilidade de entrada vs. `TEXTO_INSUFICIENTE`

Hoje as duas situações usam o mesmo código (`TEXTO_INSUFICIENTE`) e o
mesmo efeito (zera tudo). São conceitos diferentes, e a separação não
deve virar só um segundo código dentro do mesmo vocabulário - um
`Alert` é, por definição, julgamento pedagógico da fase 1 sobre o
CONTEÚDO da redação; confiabilidade de entrada é uma pergunta anterior
e de natureza diferente (sobre a LEITURA do texto, não sobre o que o
aluno escreveu). Dar-lhe um código de alerta a colocaria de volta no
mesmo vocabulário que este trabalho existe para separar.

- `TEXTO_INSUFICIENTE` (mantido, sem mudança de significado): o ALUNO
  produziu um texto curto/interrompido demais - continua em
  `_ANULA_REDACAO_ALERT_CODES`, continua sendo avaliação pedagógica,
  continua podendo zerar, continua vindo da fase 1.
- Confiabilidade de entrada insuficiente (novo, SEM código de alerta
  próprio): expressa inteiramente pelo novo campo `input_reliability`
  (seção seguinte) com status `UNRELIABLE_NEEDS_REVIEW` - nunca aparece
  em `alerts`, nunca passa por `_ANULA_REDACAO_ALERT_CODES`, nunca é
  produzido pelo julgamento pedagógico da fase 1. É resultado exclusivo
  do Quality Gate - mecanicamente o sinal chega dentro da MESMA
  resposta da fase 1 (sem chamada extra de IA, ver seção seguinte), mas
  em ORDEM DE AUTORIDADE o Quality Gate é avaliado antes de qualquer
  outra decisão aceitar o conteúdo dessa resposta como válido: antes do
  Zero Gate, antes de C1-C5, antes de qualquer nota ser persistida como
  real (ver "Efeito de UNRELIABLE_NEEDS_REVIEW" abaixo).

Larissa e João Miguel (hoje zerados por `TEXTO_INSUFICIENTE`) devem
resultar em `input_reliability.status = UNRELIABLE_NEEDS_REVIEW` a
partir desta fase, sem nenhum alerta `TEXTO_INSUFICIENTE` associado -
teste de regressão obrigatório.

### Sinal de confiabilidade (duas fontes, nunca uma só)

1. **Heurística determinística, pré-IA** (barata, roda antes de qualquer
   chamada de IA, sem dependência nova): proporção de tokens do texto
   que batem contra uma lista estática de palavras comuns em português
   (arquivo próprio do projeto, algumas milhares de entradas - mesma
   lógica de dado versionado já usada para a rubrica, nunca uma
   biblioteca de NLP nova só para isso). Limiar deliberadamente LARGO
   (ex.: só sinaliza abaixo de ~40% de tokens reconhecíveis) - nomes
   próprios, estrangeirismos, abreviações, erros ortográficos reais e
   vocabulário incomum não podem, isolados, acionar isso. Esta
   heurística é só mais um sinal de ENTRADA - nunca participa do
   julgamento de C1 nem de nenhuma competência.
2. **Autoavaliação do modelo**, na mesma chamada de fase 1 que já lê o
   texto inteiro: um campo novo e explícito no contrato (`input_reliability`,
   separado de `alerts`), com status (RELIABLE / USABLE_WITH_WARNING /
   UNRELIABLE_NEEDS_REVIEW - nomes a confirmar na implementação) e uma
   razão textual. Não reaproveita `OCR_DUVIDOSO`: esse alerta continua
   existindo com o significado atual (indício leve, sem efeito), mas
   não é mais a única fonte de sinal de confiabilidade.

Combinação (nunca decide por uma fonte isolada):
- As duas concordam em RELIABLE → RELIABLE.
- Qualquer uma reporta problema sério e a outra não discorda
  completamente → USABLE_WITH_WARNING (segue para correção normal, nota
  carrega aviso visível ao professor).
- As duas concordam em problema grave, OU o modelo reporta
  UNRELIABLE_NEEDS_REVIEW → `UNRELIABLE_NEEDS_REVIEW`.

Para IMAGE_REGION (foto/PDF): o sinal de confiança por token já
calculado em `essay_submission.py` (`_MIN_AVERAGE_CONFIDENCE` e afins)
entra como uma terceira fonte na mesma combinação - mesma lógica, sem
recálculo.

### Efeito de `UNRELIABLE_NEEDS_REVIEW`

Quando o Quality Gate decide `UNRELIABLE_NEEDS_REVIEW`: a correção para
IMEDIATAMENTE após a fase 1 (nunca chama as fases 2 de competência/zero
gate - não há ponto em refinar uma nota sobre uma leitura não confiável,
e evita custo de IA desperdiçado), `EssayCorrection.status` vira
`NEEDS_REVIEW` com um `failure_reason` identificável como motivo de
qualidade de entrada (distinto de falha de provider/infra - ex. prefixo
`QUALITY_GATE_UNRELIABLE:`), e NENHUMA nota pedagógica é persistida como
se fosse válida. `USABLE_WITH_WARNING` segue para Zero Gate/C1-C5
normalmente, com o aviso guardado ao lado do resultado (campo novo,
exposto no dashboard do professor numa fase futura - fora deste escopo,
só preparado aqui).

### Versionamento

Novo `essay_engine_contract/v6.py` (contrato anterior, v5, imutável - é
a mesma convenção de "nunca editar, sempre suceder" já usada no
projeto). Nova constante `_QUALITY_GATE_VERSION` em
`essay_correction.py`, registrada em toda `EssayCorrection` produzida a
partir desta fase.

## Fase C — Zero Gate

### Diagnóstico obrigatório antes de qualquer correção

Primeira tarefa desta fase, antes de qualquer mudança de prompt ou
threshold: reproduzir ao vivo a chamada de `_review_anula_redacao_alerts`
para Sabrina e Henrique (textos já fixados no benchmark) e inspecionar o
`reasoning` real que a fase 2 produziu ao rejeitar `FUGA_AO_TEMA`. Três
hipóteses concretas a distinguir com esse dado, não com suposição:

1. O texto da regra em `alert_review_v1.py:62-97` é interpretado na
   prática de forma mais estrita do que sua própria redação sugere.
2. A evidência passada à fase 2 não inclui a mesma justificativa
   específica que a fase 1 escreveu, e a fase 2 re-julga com menos
   contexto, chegando a uma conclusão mais fraca.
3. Um bug de código (não de prompt) descarta ou corrompe
   `confirmed_alert_codes` para este alerta/cenário.

**Não é permitido "afrouxar o threshold até os dois casos passarem"**
sem essa causa identificada - isso seria exatamente o ajuste
improvisado que este trabalho existe para evitar. A correção desta fase
é dirigida pela causa encontrada.

### Decisão explícita e auditável

Novo objeto de domínio (nome final a definir na implementação, ex.
`ZeroGateDecision`), persistido (novo campo JSON em `EssayCorrection`,
ex. `zero_gate_decision`), com no mínimo:

- `decision`: `ZERAR` / `NAO_ZERAR` / `ENCAMINHAR_REVISAO`.
- `rule_code`: um dos códigos já existentes em
  `_ANULA_REDACAO_ALERT_CODES`, ou um motivo de dúvida insuficiente.
- `evidence`: trecho/justificativa concreta (nunca "porque sim").
- `confidence`.
- `requires_human_review`: true quando a evidência não é suficiente
  para `ZERAR` nem para `NAO_ZERAR` com segurança - nesse caso o
  resultado vai para `NEEDS_REVIEW`, nunca para uma nota comum
  calculada como se a dúvida não existisse.
- `rule_version`: nova constante `_ZERO_GATE_VERSION`.

### Descobrir, não só confirmar

Hoje a fase 2 de revisão de alertas só pode confirmar ou rejeitar um
candidato que a fase 1 já levantou (`confirmed &= candidate_codes`,
nunca `|=`) - por isso 7 das 9 situações especiais perdidas nunca
chegaram a ser candidatas, e as 2 que chegaram (Sabrina, Henrique) foram
rejeitadas. O Zero Gate passa a ser um passo de avaliação PRÓPRIO, que
roda para toda redação (não só quando a fase 1 já suspeitou de algo),
lendo o texto canônico completo + o enunciado + a rubrica, e produzindo
sua própria `ZeroGateDecision` - os alertas da fase 1 continuam servindo
de evidência de entrada para esse passo, mas deixam de ser precondição
para ele rodar. A forma exata (uma chamada de IA dedicada vs. reorganizar
a chamada existente) e os ajustes de prompt dependem do diagnóstico
acima - não fixados aqui.

### Ordem do pipeline

Pipeline alvo: `ENTRADA → QUALITY GATE → ZERO GATE → C1-C5 → VALIDAÇÃO →
RESULTADO`, sequencial. Hoje a competência (`_score_competencies_from_evidence`)
e a revisão de alertas (`_review_anula_redacao_alerts`) rodam
CONCORRENTES (`asyncio.gather`), nenhuma consciente da outra. Nova
ordem: Zero Gate resolve primeiro. Se `ZERAR` com evidência suficiente,
o fluxo de pontuação normal nem roda (economia de uma chamada de IA
também). Se `NAO_ZERAR`, segue para C1-C5 como hoje. Se
`ENCAMINHAR_REVISAO`, vai para `NEEDS_REVIEW` com a decisão (e sua
evidência) anexada, sem inventar uma nota C1-C5 como se a dúvida não
existisse.

### Testes de regressão obrigatórios

Sabrina e Henrique (fuga ao tema, devem zerar com evidência correta) e
Larissa/João Miguel (devem virar `ENTRADA_INSUFICIENTE`/`NEEDS_REVIEW`
pelo Quality Gate, nunca `TEXTO_INSUFICIENTE`) entram no benchmark e na
suíte de testes automatizados como casos permanentes - qualquer
regressão futura destes 4 comportamentos quebra a suíte.

## Fase D — Experimento controlado + recalibração C1-C5

### Pré-condição

Só roda depois da Fase B em produção (benchmark já isola os casos
`ENTRADA_INSUFICIENTE`, que saem da amostra de "válidas"). A pergunta
que resta: das 20 válidas, quanto do viés para baixo vem de texto ainda
corrompido mas não grave o bastante para o Quality Gate (`USABLE_WITH_WARNING`)
sendo lido como erro real do aluno, versus quanto vem das regras de
pontuação (`_RULES_TOP_BAND`, teto de `TANGENCIAMENTO_AO_TEMA`).

### Método

Selecionar um subconjunto representativo (cobrindo diferentes níveis de
severidade de `OCR_DUVIDOSO`) das 20 válidas. Para cada uma, produzir
manualmente uma versão limpa do texto - corrigindo APENAS artefatos de
OCR (ex.: "ideho" → "idoso"), nunca os erros reais de gramática do
aluno (corrigir isso contaminaria a medição na direção contrária).
Rodar o motor nas duas versões (A = texto atual, B = texto limpo) com
TUDO o resto fixo: mesmo provider, modelo, parâmetros, rubrica, prompt,
`engine_version`, regras. Medir, para o mesmo subconjunto, nas duas
condições: MAE total e por competência; viés médio com sinal, total e
por competência; distribuição das diferenças; distância em níveis
oficiais (delta/40).

### Decisão, só depois do dado

- Se o viés cai muito na versão limpa → o principal motor é
  contaminação de entrada, não a regra - a ação correta é reforçar a
  Fase B (ex.: calibrar o limiar entre `USABLE_WITH_WARNING` e
  `RELIABLE`), não tocar em `_RULES_TOP_BAND`/`TANGENCIAMENTO_AO_TEMA`.
- Se o viés persiste mesmo limpo → sinal real de regra enviesada. Só
  então: tornar `_RULES_TOP_BAND` simétrica (remover a direção única
  "na dúvida, prefira 160" por um critério que não prefira
  sistematicamente nenhum lado) e revisar o teto de
  `TANGENCIAMENTO_AO_TEMA` para ter uma contrapartida sob incerteza, em
  vez de só penalizar. Nunca uma constante somada à nota, nunca regra
  amarrada a um nome ou redação específica.
- Medir de novo com o benchmark completo depois de qualquer mudança.

### Critério de sucesso

Não é MAE = 0 - a correção de redação tem componente interpretativo
real. O que conta: viés sistemático reduzido, falsos zeros eliminados
(Larissa/João Miguel), situações especiais reconhecidas (Sabrina/
Henrique + o que mais o Zero Gate capturar), decisão explicável e
auditável em todos os casos, sem overfitting às 30 notas específicas
deste conjunto.

## Relatório consolidado

Ao final das 4 fases, um relatório único (reaproveitando o padrão de
PDF/CSV já usado nesta investigação) comparando BASELINE × APÓS B ×
APÓS C × APÓS D: MAE total e por competência (C1-C5), viés médio total e
por competência, precision/recall do Zero Gate, contagem de falsos
zeros, situações especiais perdidas, casos `NEEDS_REVIEW` (e por que
cada um), taxa de `OCR_DUVIDOSO`/`ENTRADA_INSUFICIENTE`, divergências
normativas registradas (não corrigidas), e análise individual de
qualquer caso que permaneça com grande divergência depois da Fase D.

## Versionamento

Cada `EssayCorrection` já registra `rubric_version`/`prompt_version`/
`engine_version`/`contract_version` - suficiente para saber qual
configuração produziu qual resultado. Este trabalho soma:
`_QUALITY_GATE_VERSION` (Fase B) e `_ZERO_GATE_VERSION` (Fase C), ambas
gravadas como novas colunas/campos em `EssayCorrection`. As duas fases
bumpam versão de forma independente, sem forçar uma só subir por causa
da outra:
- Fase B: `_PROMPT_VERSION` sobe para `essay_correction_v16` (ganha as
  instruções de `input_reliability` - v15 permanece imutável, mesma
  convenção já usada de v1 a v15); `contract_version` sobe para v6
  (campo `input_reliability` novo no schema - v5 permanece).
- Fase C: `engine_version` sobe (o fluxo de `_run_ai` muda de ordem -
  Zero Gate antes de C1-C5). Se a implementação optar por uma chamada
  de IA dedicada para o Zero Gate (ver "Descobrir, não só confirmar"),
  essa chamada tem sua PRÓPRIA versão de prompt, independente de
  `_PROMPT_VERSION` - mesma lógica que `alert_review_v1.py` já é
  versionado separado do prompt principal hoje. Não força
  `essay_correction_v16` a virar `v17`.

## Riscos e não-objetivos

- Não mudar a política de cópia sem fonte normativa e caso real (fica
  registrado, não bloqueia).
- Não overfitar para as 30 notas específicas deste benchmark.
- Não quebrar FORMATIVO/AVALIATIVO nem `_apply_review_policy`.
- Custo de IA: o Quality Gate não soma chamada nova (usa a mesma
  chamada de fase 1 + heurística local sem custo); o Zero Gate rodar
  sempre (em vez de condicionalmente) pode aumentar custo, mas o
  short-circuit ao decidir `ZERAR` elimina a chamada de competência
  correspondente - efeito líquido deve ser medido no benchmark da Fase
  C, não assumido.
