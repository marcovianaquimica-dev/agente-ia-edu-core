# Relatório final — Motor de Redação: Quality Gate + Zero Gate + Calibração

Plano: `docs/superpowers/plans/2026-10-06-motor-redacao-quality-zero-gate.md`
Spec: `docs/superpowers/specs/2026-10-06-motor-redacao-quality-zero-gate-design.md`

Este relatório consolida as 4 execuções completas do benchmark de 30
redações reais (`scripts/essay_calibration_benchmark.py`), uma por fase do
plano, mais o experimento controlado de texto limpo (Fase D) que
fundamentou a decisão de recalibração. Todos os números abaixo vêm
diretamente dos artefatos JSON committados em
`tests/fixtures/essay_calibration_baselines/` — nenhum foi recalculado ou
ajustado manualmente.

> **Nota de privacidade (2026-10-08):** este repositório nunca contém o
> texto integral das redações reais nem os nomes dos alunos. Os dados
> reais de calibração usados para gerar os números deste relatório vivem
> exclusivamente num armazenamento privado fora do git
> (`ESSAY_CALIBRATION_PRIVATE_DIR`); os alunos são identificados apenas
> por `student_ref` opaco (`aluno_01`..`aluno_30`). Os artefatos JSON
> citados acima (`tests/fixtures/essay_calibration_baselines/*.json`)
> contêm apenas `student_ref`, notas e metadados categóricos — nunca texto
> de redação. Este relatório também nunca cita trechos literais de uma
> redação real; onde uma evidência do motor precisaria ser citada
> literalmente, isso é marcado explicitamente como omitido.

## 1. Tabela comparativa consolidada

Gerada por `scripts/essay_calibration_consolidated_report.py`:

| Métrica | CALIBRATION_BASELINE_V1 | AFTER_QUALITY_GATE_V1 | AFTER_ZERO_GATE_V1 | AFTER_CALIBRATION_V1 |
|---|---|---|---|---|
| mae_total | 207.06 | 173.33 | 280.00 | 331.43 |
| bias_total | -207.06 | -164.44 | -253.33 | -331.43 |
| zero_gate_recall | 0.286 (2/7)* | 1.0 (1/1)* | 1.0 (1/1)* | 1.0 (2/2)* |
| zero_gate_precision | 0.5 | 1.0 | 0.333 | 0.5 |
| needs_review_count | 6 | 20 | 20 | 21 |
| ocr_duvidoso_rate | 0.733 | 0.9 | 0.833 | 0.767 |

\* **`zero_gate_recall` não é "quantas das 10 situações especiais reais do
corpus o motor identificou corretamente"** — é "das situações especiais
reais que chegaram a receber QUALQUER decisão do motor (nota ou
zeragem), quantas foram zeradas" (`essay_calibration_metrics.py:79-85`,
`scored_rows = [r for r in rows if r.engine_scores is not None]`). O
denominador real entre parênteses mostra o tamanho dessa base: ela
colapsou de 7/10 no BASELINE para 1/10 em AFTER_QUALITY_GATE_V1 e
AFTER_ZERO_GATE_V1, e 2/10 em AFTER_CALIBRATION_V1 — as outras 8-9 das 10
situações especiais reais do corpus foram para NEEDS_REVIEW e saem do
cálculo por definição. Ver seção 2 para a leitura correta deste número.

MAE por competência:

| Competência | BASELINE | +QUALITY GATE | +ZERO GATE | +CALIBRAÇÃO |
|---|---|---|---|---|
| C1 | 72.94 | 75.56 | 93.33 | 85.71 |
| C2 | 47.06 | 26.67 | 62.22 | 62.86 |
| C3 | 40.00 | 31.11 | 75.56 | 62.86 |
| C4 | 51.76 | 40.00 | 71.11 | 68.57 |
| C5 | 37.65 | 35.56 | 66.67 | 74.29 |

Viés (bias, negativo = motor subavalia) por competência:

| Competência | BASELINE | +QUALITY GATE | +ZERO GATE | +CALIBRAÇÃO |
|---|---|---|---|---|
| C1 | -68.24 | -75.56 | -66.67 | -85.71 |
| C2 | -42.35 | -17.78 | -53.33 | -62.86 |
| C3 | -16.47 | -13.33 | -22.22 | -40.00 |
| C4 | -51.76 | -31.11 | -53.33 | -68.57 |
| C5 | -28.24 | -26.67 | -57.78 | -74.29 |

Casos excluídos de MAE/bias em toda fase por `normative_status`
DIVERGENT/UNVERIFIED (nunca comparados contra o motor, por definição da
spec — ver `essay_calibration_metrics.py`): `aluno_21` a `aluno_30`
(10 casos, população UNVERIFIED do corpus de 30).

## 2. Como ler esses números — por que MAE não é o critério de sucesso

O plano estabelece explicitamente (Self-Review das Fases B/C) que **MAE
baixo não é o objetivo** — o objetivo é nunca fabricar uma nota quando o
motor não tem confiança nela. As quatro fases mudam o que conta como
"nota do motor" de formas que não são diretamente comparáveis por MAE
puro:

- **BASELINE → +QUALITY GATE** (mae 207→173, mas needs_review 6→20): o
  Quality Gate passou a recusar nota quando o texto de entrada é
  genuinely ilegível (ex.: os casos reais Larissa/João Miguel, a
  motivação original desta fase) — 14 redações que antes recebiam uma
  nota fabricada (às vezes por acaso perto do gabarito, às vezes uma
  "zerada" silenciosa) agora corretamente vão para NEEDS_REVIEW sem nota
  alguma. MAE cai porque a população que ainda recebe nota ficou mais
  fácil (menos ruído de OCR extremo), não porque o motor ficou mais
  preciso nos casos difíceis. **É também aqui, não na fase do Zero Gate,
  que o salto de `zero_gate_recall` (0.286→1.0) de fato acontece** — e
  pela mesma razão do MAE: das 10 situações especiais reais do corpus,
  o número que chega a receber qualquer decisão do motor cai de 7 para
  1 (`aluno_29`), e essa única redação restante já era zerada
  corretamente. O "recall" sobe porque a base encolheu para um caso só,
  não porque o motor passou a reconhecer mais situações especiais (ver
  nota da seção 1 e o resumo ao final desta seção).
- **+QUALITY GATE → +ZERO GATE** (mae 173→280; zero_gate_recall
  permanece 1.0→1.0, sobre a mesma base de 1/10 já reduzida na fase
  anterior): o Zero Gate deixou de ser um "confirme a sinalização da
  fase 1" e passou a julgar independentemente, com amostragem 3x, todos
  os 8 códigos de anulação/zeragem contra o texto completo. Isso é uma
  melhoria real de arquitetura (julgamento independente, auditável, sem
  depender de a fase 1 ter levantado uma suspeita primeiro), mas **não é
  a causa do salto de recall relatado na tabela da seção 1** — esse já
  tinha ocorrido na transição anterior, antes de o Zero Gate existir. O
  que esta fase de fato muda é introduzir um novo falso positivo real
  (TEXTO_INSUFICIENTE em `aluno_12`/`aluno_13`, achado já registrado como
  `task_e5b9b347`, fora de escopo deste plano) que arrasta
  zero_gate_precision para 0.333 e, por serem dois outliers de erro
  absoluto ~840-920, domina o mae_total dessa fase.
- **+ZERO GATE → +CALIBRAÇÃO** (mae 280→331): ver seção 4 — essa
  comparação específica é a mais ruidosa das três, porque a população de
  redações que efetivamente recebe nota muda quase por completo entre as
  duas execuções (ver ressalva metodológica abaixo), e porque esta
  execução pegou 2 falhas reais de infraestrutura (OpenAI
  indisponível), não de código.

Em suma, e dito sem o açúcar do número isolado: **`zero_gate_recall` subiu
de 0.286 para 1.0 e ficou lá — mas não porque o motor passou a identificar
corretamente mais situações especiais reais.** O que de fato aconteceu foi
o denominador da métrica (quantas das 10 situações especiais reais do
corpus chegam a receber qualquer decisão do motor, nota ou zeragem) cair
de 7 para 1, depois para 2 — nunca mais que 2 de 10. A afirmação honesta e
de fato comprovada por este benchmark é mais estreita que "recall de
100%": **o motor nunca mais fabrica uma nota normal sobre uma redação que
é, na realidade, uma situação especial** (isso é verdadeiro e verificável
nas 4 execuções), mas ele alcança esse resultado majoritariamente
mandando essas redações para NEEDS_REVIEW — revisão humana — e não
identificando e zerando-as corretamente por conta própria. Em número
absoluto, redações com situação especial real que o motor zerou
corretamente por conta própria: 2 (BASELINE) → 1 (+QUALITY GATE) → 1
(+ZERO GATE) → 2 (+CALIBRAÇÃO) — uma contagem que caiu antes de voltar a
subir, não um recall que subiu de forma monotônica. O preço do lado do
MAE é real (menos redações "fáceis" chegam a ter nota, e as que chegam
incluem outliers de erro grande que antes eram silenciosamente absorvidos
em NEEDS_REVIEW ou em uma nota devolvida por acaso), mas a causa correta
é essa migração para NEEDS_REVIEW, não uma melhora de detecção medida por
`zero_gate_recall`.

## 3. Ressalva metodológica importante sobre a execução AFTER_CALIBRATION_V1

A comparação "antes/depois" do Task 16 (a mudança de `_RULES_TOP_BAND`)
**não é uma comparação limpa de população igual**, pelo mesmo motivo já
observado no experimento da Fase D (Task 15): qual subconjunto de
redações passa pelo Quality Gate e qual decisão o Zero Gate toma têm
ruído de amostragem real entre execuções (ver `progress.md`, achado do
Task 10 — não-determinismo genuíno da reavaliação de alertas).

Das 20 redações `CONFIRMED` do corpus:

- `AFTER_ZERO_GATE_V1` pontuou 9 (`aluno_02,04,06,08,11,12,13,16,20`).
- `AFTER_CALIBRATION_V1` pontuou 7, quase todas **diferentes**
  (`aluno_01,03,05,06,13,14,15`) — só `aluno_06` e `aluno_13` aparecem
  nas duas.

Isso significa que o salto de mae_total 280→331 **não isola o efeito da
mudança do Task 16** — ele mistura o efeito da calibração com o ruído de
quais redações o Quality Gate/Zero Gate decidiram pontuar nesta
execução específica. Duas causas concretas, verificadas diretamente no
log e no banco desta execução, contribuíram para essa mudança de
população:

1. **2 falhas reais de infraestrutura, não de código**: `aluno_11` e
   `aluno_16` falharam com `ProviderUnavailableError` (OpenAI
   indisponível) durante a chamada da IA — ambos tinham pontuado
   normalmente na execução imediatamente anterior
   (`AFTER_ZERO_GATE_V1`: 600 e 280 respectivamente). Foram corretamente
   registrados como NEEDS_REVIEW (nenhuma nota fabricada), mas isso
   removeu 2 pontos de dados "fáceis" desta execução sem relação alguma
   com o Task 16.
2. **4 rejeições CONTRACT_SHAPE_INVALID** (`aluno_04`, `aluno_08`,
   `aluno_18`, `aluno_26` — o último é `UNVERIFIED`, não afeta MAE): a
   fragilidade de shape já documentada como pré-existente no projeto
   (historicamente ~3/30 por execução) apareceu em taxa um pouco mais
   alta nesta execução (4/30 no total, 3/30 afetando `CONFIRMED`) — ruído
   de formatação de JSON do modelo, não uma regressão introduzida por
   este plano.

Ambas as causas resultam em NEEDS_REVIEW (nunca em nota fabricada),
consistente com o princípio central do projeto — mas inflam
`needs_review_count` (20→21) e tornam a leitura direta de "calibração
piorou a nota" **não confiável isoladamente a partir deste mae_total**.
A leitura confiável é por caso individual (seção 4) e pela decisão em si
(seção 5), não pelo delta agregado desta única execução.

## 4. Casos com grande divergência (`|engine_total - expected_total| > 160`) em AFTER_CALIBRATION_V1

| student_ref | normative_status | engine_total | expected_total | diff | zero_gate_decision | hipótese |
|---|---|---|---|---|---|---|
| aluno_01 | CONFIRMED | 640 | 960 | -320 | NAO_ZERAR (confiança 0.67) | Viés estrutural de subavaliação C1-C5 persistente — Zero Gate corretamente não zerou (redação forte, sem situação especial), mas a nota por competência ficou abaixo do esperado em **todas as 5 competências** (C1 -120, C2/C3/C5 -40, C4 -80). Esta é exatamente a redação-caso mais extremo do viés que a Task 16 tentou endereçar; o fato de persistir em magnitude tão grande após a simetrização de `_RULES_TOP_BAND` sugere que a causa é mais ampla que o único "empate genuíno" que aquela regra cobre — provavelmente uma conservadorismo geral do modelo ao avaliar nota máxima/quase-máxima, não restrito a casos de dúvida explícita. |
| aluno_13 | CONFIRMED | 0 | 840 | -840 | ZERAR / TEXTO_INSUFICIENTE (confiança 1.0) | **Achado já conhecido e deliberadamente fora de escopo** (`task_e5b9b347`, identificado no Task 13): falso positivo do Zero Gate em texto genuinamente corrompido por OCR mas de fato completo — a evidência citada pelo próprio motor é um trecho real da redação (omitido deste relatório por privacidade — o texto integral das redações não é publicado neste repositório, ver nota no início do documento), genuinamente ilegível por ruído de OCR, não um texto de fato incompleto. |
| aluno_14 | CONFIRMED | 0 | 680 | -680 | ZERAR / TEXTO_INSUFICIENTE (confiança 1.0) | **Nova instância real do mesmo mecanismo do achado acima** — não registrada em nenhuma execução anterior (em `AFTER_ZERO_GATE_V1` esta redação ficou em NEEDS_REVIEW, nunca chegou a ser zerada). Corrobora diretamente, com dado de produção real, a preocupação arquitetural já sinalizada (não corrigida) na Task 15: o Zero Gate parece disparar `TEXTO_INSUFICIENTE` com mais facilidade justamente quando o texto fica "legível o suficiente para produzir uma saída válida" sem estar de fato incompleto — mesma classe de problema de `aluno_12`/`aluno_13`, agora com uma terceira (`aluno_14`) e quarta (via Task 15) ocorrência observada. Recomendação: expandir o escopo de `task_e5b9b347` para investigar `TEXTO_INSUFICIENTE` como regra, não apenas os 2 casos originais. |
| aluno_15 | CONFIRMED | 400 | 600 | -200 | NAO_ZERAR (confiança 1.0) | Mesmo padrão de `aluno_01`: viés estrutural de subavaliação (C1 -80, C3/C4/C5 -40 cada, C2 exato) em uma redação que o Zero Gate corretamente não zerou. Esta redação carrega `ocr_duvidoso=True`, mas como foi aceita pelo Quality Gate (texto considerado confiável o suficiente) e a Fase D já demonstrou que o viés persiste mesmo em texto limpo (seção 5), a hipótese mais provável é viés de calibração C1-C5, não contaminação residual de OCR. |

Casos com diferença zero, para contraste (não divergentes, resultado
correto): `aluno_22` e `aluno_29` (ambos `UNVERIFIED`, gabarito marca
"situação especial não especificada" com total 0, motor zerou
corretamente nos dois).

## 5. A decisão da Task 16 — o que foi mudado, por quê, e com que evidência

### O achado que motivou a decisão (Task 15 / `CLEAN_TEXT_EXPERIMENT_V1`)

A Fase D existe para responder uma pergunta específica: **o viés de
subavaliação observado nas Fases A-C é causado pelo ruído de OCR na
entrada, ou é uma característica da própria regra de pontuação?** Para
isso, o Task 14 produziu `tests/fixtures/essay_calibration_clean_text_subset_v1.json`
— as 20 redações `CONFIRMED`-não-especiais do corpus, cada uma com uma
versão de texto corrigida manualmente (só erros de OCR, nunca erros
reais do aluno), revisada em 2 rodadas. O Task 15 rodou o mesmo motor
contra o texto original (condição A) e o texto limpo (condição B) dessas
20 redações — resultado em
`tests/fixtures/essay_calibration_baselines/CLEAN_TEXT_EXPERIMENT_V1.json`.

Os números brutos dessa execução (`mae_total` 248.0 condição A vs 293.33
condição B) são, pela mesma razão da seção 3, **não diretamente
comparáveis** — `needs_review_count` foi 10 (A) vs 5 (B), ou seja, as
duas condições pontuaram populações diferentes de redações. A análise que
de fato importa é a feita sobre o subconjunto que pontuou **nas duas**
condições:

- **8 redações pontuadas em ambas** (`aluno_01/02/04/11/13/15/18/20`):
  mae_total idêntico nas duas condições (280.0); bias_total
  **-280.0 (corrompido) → -250.0 (limpo)**, uma redução de ~11%.
- Dessas 8, duas (`aluno_13`/`aluno_18`) são zeradas pelo motor **nas
  duas** condições — um resultado do Zero Gate, não da pontuação C1-C5,
  então misturá-las ao "viés de nota" é metodologicamente incorreto para
  avaliar `_RULES_TOP_BAND`/a pontuação. Excluindo as duas: 6 redações
  restantes (`aluno_01/02/04/11/15/20`): mae_total também idêntico nas
  duas condições (166.67); bias_total **-166.67 (corrompido) → -126.67
  (limpo)**, uma redução de ~24%.

Nenhuma das duas comparações (11% ou 24%) atinge o critério que o
próprio plano define para concluir "o viés é principalmente
contaminação de OCR, não toque na regra" (o plano pede uma redução
*claramente menor que a metade*). Pelo contrário, um viés estrutural de
magnitude parecida (-127 a -280, dependendo do subconjunto) persiste
mesmo em texto verificado como limpo à mão — evidência de que a causa
principal **não é o OCR**, e sim algo na própria regra de pontuação.

### A decisão

Com essa evidência, a Task 16 identificou `_RULES_TOP_BAND` em
`essay_prompts/competency_scoring_v2.py` como a causa mais provável: essa
instrução diz ao modelo que, em caso de dúvida genuína entre duas bandas
adjacentes no topo da escala, prefira mecanicamente a banda mais baixa
("na dúvida, prefira 160"). Ela foi introduzida originalmente para
combater o problema **oposto** (excesso de notas 1000 perfeitas) — é
plausível que, ao resolver esse problema, tenha introduzido um viés
unidirecional que hoje contribui para a subavaliação que este benchmark
mede repetidamente (achado corroborado pela memória do projeto,
`project_essay_correction_overscoring_weak_essays`: o motor já teve um
viés de sobre-avaliação corrigido por um motor em 2 fases, e o viés de
calibração observado depois "trocou de direção" sem nunca ser
investigado a fundo).

A correção (commits `c99b221` e `ef0f691`): criado
`essay_prompts/competency_scoring_v3.py` (nunca edita `v2.py` em
produção — convenção do projeto para todo artefato de prompt), idêntico
a v2 exceto por **uma única frase** reescrita para ser genuinamente
simétrica — decidir pelo peso real da evidência, e se de fato houver
empate, registrar essa incerteza no campo `reasoning` em vez de escolher
mecanicamente a banda mais baixa. `essay_correction.py` foi atualizado
para importar e chamar v3 em vez de v2 (1 import + 1 call site,
confirmados via grep do arquivo inteiro antes e depois da mudança).

Deliberadamente **não tocado**: o teto de
`TANGENCIAMENTO_AO_TEMA` em `essay_correction.py` (`points["C3"]/["C5"] =
min(..., 40)`), citado no texto do plano como um possível segundo alvo.
Esse teto é uma transcrição determinística do manual oficial do ENEM
(Cartilha do Participante, p. 27) aplicada só depois que o alerta já foi
confirmado — não existe "dúvida" nesse ponto para simetrizar; inventar um
contrapeso ali significaria se afastar do manual oficial, o oposto do
objetivo de fidelidade deste projeto.

### Esta execução (AFTER_CALIBRATION_V1) não confirma nem refuta a correção de forma limpa

Como a seção 3 explica, a amostra pontuada nesta execução final mudou
quase inteiramente em relação à execução anterior — só `aluno_06` e
`aluno_13` aparecem nas duas. Isso significa que **esta execução, por si
só, não isola o efeito da mudança do Task 16** da forma como o
experimento controlado da Task 15 conseguiu isolar efeito de
OCR-limpo-vs-sujo (mesmo conjunto de 20 redações, duas condições). Uma
avaliação rigorosa do efeito real da simetrização de `_RULES_TOP_BAND`
exigiria um novo experimento controlado no mesmo formato do Task 15 (duas
execuções sobre o mesmo conjunto de redações, uma com v2 e outra com v3),
não apenas comparar duas execuções sequenciais do benchmark completo
onde o Quality Gate/Zero Gate já introduzem ruído de amostragem próprio.
Essa é uma limitação conhecida desta última rodada, não um defeito da
decisão da Task 16 em si — a decisão foi tomada corretamente a partir da
evidência do experimento controlado (seção acima), que é o desenho certo
para essa pergunta.

## 6. Estado final e achados em aberto (fora de escopo deste plano)

- **`task_e5b9b347`** (já registrado): `TEXTO_INSUFICIENTE` produz falso
  positivo em texto OCR-corrompido mas de fato completo —
  `aluno_12`/`aluno_13` nas execuções anteriores. **Ampliado por este
  relatório**: `aluno_14` é uma terceira ocorrência real, confirmada
  nesta execução final de produção (seção 4). Recomenda-se expandir o
  escopo dessa investigação futura de "2 casos específicos" para "a
  regra `TEXTO_INSUFICIENTE` em si, quando o texto é legível o
  suficiente para produzir uma saída válida mas ainda carrega ruído de
  OCR pesado".
- **Viés estrutural C1-C5 persistente** (`aluno_01`, `aluno_15` nesta
  execução): mesmo após a simetrização de `_RULES_TOP_BAND`, redações
  fortes continuam sendo subavaliadas em magnitude grande. Combinado com
  o achado da Fase D de que limpar o OCR reduz esse viés em só 11-24%,
  a hipótese mais provável é que existe mais de uma causa contribuindo
  para a subavaliação — `_RULES_TOP_BAND` era uma delas, mas não a
  única. Investigação futura recomendada, fora do escopo deste plano
  (que tratou apenas da regra que o experimento controlado conseguiu
  isolar com confiança).
- **Zero Gate dispara mais em texto legível** (observação já sinalizada
  na Task 15, agora com uma terceira confirmação via `aluno_14`): possível
  interação arquitetural entre a legibilidade do texto e a propensão do
  Zero Gate a decidir `ZERAR` — nunca investigada a fundo, candidata
  natural a unificar com o achado de `TEXTO_INSUFICIENTE` acima.
- **IMAGE_REGION sem texto completo para o Zero Gate** (Task 11): o Zero
  Gate hoje usa uma aproximação (`_approximate_text_for_zero_gate`) para
  submissões no modo `IMAGE_REGION`, por não haver transcrição completa
  disponível nesse modo. Nenhuma das 30 redações deste benchmark usa esse
  modo, então não afetou nenhum número deste relatório — mas é uma
  lacuna real para o pipeline de produção, documentada no código.

## 7. Resumo executivo

| Fase | mae_total | zero_gate_recall | needs_review | O que mudou |
|---|---|---|---|---|
| BASELINE | 207.06 | 0.286 (2/7 casos avaliados) | 6 | Estado original, antes de qualquer correção deste plano. |
| +QUALITY GATE | 173.33 | 1.0 (1/1 caso avaliado) | 20 | Nunca mais fabrica nota sobre texto ilegível (fix do bug real Larissa/João Miguel). |
| +ZERO GATE | 280.00 | 1.0 (1/1 caso avaliado) | 20 | Reavaliação independente e auditável de todas as 8 situações de anulação (fix do bug real Sabrina/Henrique's FUGA_AO_TEMA inconsistente); revela 1 novo falso-positivo (`aluno_12`/`aluno_13`, fora de escopo). |
| +CALIBRAÇÃO | 331.43 | 1.0 (2/2 casos avaliados) | 21 | `_RULES_TOP_BAND` simetrizado com base em evidência controlada de que o viés de subavaliação persiste em texto limpo; resultado desta execução específica confundido por ruído de amostragem entre execuções + 2 falhas reais de infraestrutura (seção 3). |

O `zero_gate_recall` de 1.0 nas três últimas fases é sobre uma base de
apenas 1-2 das 10 situações especiais reais do corpus (ver nota da seção
1 e seção 2) — não é "recall de 100% para situações especiais reais". O
projeto termina com uma afirmação mais estreita, porém esta sim
plenamente comprovada: zero fabricação de nota comprovada em 3 cenários
reais distintos (Larissa/João Miguel, Sabrina/Henrique, e agora a própria
execução final — nenhuma das 6 falhas desta rodada produziu nota
fabricada); o motor nunca mais devolve uma nota normal fabricada sobre
uma situação especial real, mas na prática ele consegue isso
majoritariamente ao mandar essas redações para NEEDS_REVIEW (revisão
humana), não por identificá-las e zerá-las corretamente por conta própria
— em número absoluto, só 1-2 das 10 situações especiais reais chegam a
ter qualquer decisão do motor em cada execução pós-Quality-Gate. A isso
se soma uma correção de calibração fundamentada em experimento controlado
(não em ajuste a casos específicos), e uma lista clara e nomeada de
achados remanescentes para investigação futura — nenhum escondido, todos
já com pelo menos uma hipótese concreta registrada.
