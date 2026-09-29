# Correção de simulados com TRI — design

**Data:** 2026-09-29
**Status:** design aprovado, aguardando plano de implementação
**Projeto:** `agente-ia-edu-core` (subsistema novo)

---

## 1. Contexto e objetivo

A escola aplica simulados no modelo ENEM em papel. Hoje a correção é manual. Este subsistema
cobre o ciclo completo: gerar os cartões-resposta, ler os cartões preenchidos, calcular nota
por Teoria de Resposta ao Item (TRI) e entregar relatórios em quatro níveis (aluno, turma,
escola/rede e qualidade dos itens).

O cartão de referência é o arquivo `CARTÃO RESPOSTA.pdf` fornecido pelo usuário: uma página,
90 questões de 5 alternativas, modelo ENEM 1º dia, com o quadro de notas
`LINGUAGENS | HUMANAS | TOTAL`. Foi gerado com ReportLab — ou seja, a geração do cartão já
está sob controle do projeto, o que este design explora deliberadamente.

### Decisões de contexto que fundamentam o design

| Questão | Decisão |
|---|---|
| Nº de alunos por simulado | 400 a 1.500 |
| Captura das imagens | Scanner **e** foto de celular |
| Identificação do aluno | Cartão nominal com QR impresso |
| Origem das questões | Prova externa, sistema conhece só o gabarito |
| Metadados do gabarito | Área/disciplina por questão |
| Níveis de relatório | Aluno, turma, escola/rede e análise de itens |
| Equalização entre simulados | Sim — itens-âncora disponíveis |
| Escala de reporte | Referência ENEM por área (mínimo, mediana, máximo), cadastrada manualmente |
| Hospedagem | Core, com geração de cartão e OMR num ambiente isolado de processo separado |
| Isolamento multi-tenant | `school_id` e FKs compostas em todas as tabelas, como no modelo acadêmico |

### Fora de escopo

- **Cadernos com ordem embaralhada** (cores azul/amarelo do ENEM real). O v1 assume um único
  caderno por simulado. O modelo de dados reserva `booklet_code` no cartão para não travar a
  evolução, mas nenhuma lógica de permutação é implementada.
- **Redação.** O cartão-modelo menciona a folha de redação, mas o projeto já tem um motor de
  correção de redação independente. Nenhuma integração é feita aqui.
- **Integração com o banco de questões.** As questões vêm de fora e o sistema conhece apenas
  o gabarito. Consequência aceita conscientemente: os parâmetros de TRI calibrados ficam
  presos ao item do simulado e **não realimentam** os pipelines de maestria
  (`LearningHistory`, `DomainContentMastery`) nem a seleção adaptativa de questões. A decisão
  é reversível — ver `source_question_version_id` em §3.
- **Modelo 3PL.** O v1 calibra em 2PL. Ver §6 para o critério de promoção a 3PL.
- **Infraestrutura de fila externa** (Celery, RQ, Redis). A fila é uma tabela Postgres.

---

## 2. Arquitetura

Quatro unidades, cada uma com um propósito e uma fronteira explícita.

### 2.1 `simulado_card` — geração do cartão (ambiente isolado)

Gera o PDF dos cartões nominais em ReportLab e emite, **no mesmo ato**, o template geométrico:
as coordenadas normalizadas de cada bolha de cada questão, a posição dos quatro marcadores
ArUco de canto e a posição do QR.

Depende de: ReportLab. O QR sai do `QrCodeWidget` vetorial que o próprio ReportLab já traz,
sem biblioteca adicional.
Não depende de: OpenCV, banco de dados (recebe os dados já resolvidos).

**Roda fora do ambiente do core, junto do `omr_worker` (§2.3).** ReportLab declara
`pillow>=9.0.0` como dependência obrigatória — verificado em `pypi.org/pypi/reportlab/json`
para a versão 5.0.1 — e Pillow é exatamente o que o `pyproject.toml` do core proíbe, porque
altera o comportamento de `page.images` do pypdf, consumido em
`src/agente_ia_edu/services/ingestion_parser.py:371`.

Import tardio **não** resolve: o pypdf muda de comportamento conforme Pillow estar instalado
no ambiente, independentemente de quem o importou. A única garantia real é o ReportLab nunca
entrar no `site-packages` do core.

Gerador e leitor no mesmo ambiente isolado é também o arranjo natural: eles são as duas pontas
do mesmo template geométrico.

A geometria é separada da renderização. O cálculo das coordenadas do template é stdlib puro,
sem ReportLab, para que o leitor (§2.3) e os testes possam consumi-lo sem arrastar nada
gráfico. Só o renderizador toca ReportLab.

**O tipo do template é declarado uma única vez**, no módulo de geometria, e o leitor o
**importa** — não redeclara uma cópia própria para ler o mesmo JSON. Duas definições do mesmo
contrato divergem em silêncio, e o sintoma de divergência aqui não é teste vermelho: é o leitor
medindo intensidade na coordenada errada e produzindo respostas plausíveis e falsas. Gerador e
leitor rodam no mesmo ambiente isolado justamente por serem as duas pontas deste contrato; o
import direto é o que torna a divergência impossível em vez de improvável.

Os marcadores ArUco **não são gerados em tempo de execução**. Um dicionário ArUco é um
conjunto fixo e pequeno de padrões conhecidos; os quatro usados aqui são pré-gerados uma vez
e versionados no repositório como imagens estáticas, que o ReportLab apenas posiciona. É isso
que permite ao gerador não depender de OpenCV, mantendo a fronteira do §2.3 intacta.

O acoplamento entre gerador e template é intencional e é a principal alavanca de robustez do
subsistema: o leitor nunca precisa *descobrir* onde as bolhas estão, porque quem imprimiu já
disse. Isso elimina a etapa mais frágil de qualquer OMR.

### 2.2 `tri_engine` — matemática (core)

Módulo puro. Recebe uma matriz de respostas NumPy e devolve parâmetros de item e estimativas
de theta.

**Não conhece banco de dados, não conhece aluno, não conhece HTTP.** Essa fronteira é o que
torna o motor verificável: dá para alimentá-lo com respostas simuladas a partir de parâmetros
conhecidos e exigir que ele os recupere (§8.1).

Depende de: NumPy, SciPy.

Implementa o protocolo `ProficiencyEstimator` já definido em
`src/agente_ia_edu/services/proficiency.py`, cujo docstring antecipa este encaixe
("TRI/MIRT can implement this protocol later").

### 2.3 `omr_worker` — leitura óptica (processo separado)

**O único lugar do sistema onde `cv2` é importado.** Roda como processo/container próprio,
com o seu próprio conjunto de dependências, compartilhado com o gerador de cartão (§2.1).

Essa separação existe por uma razão concreta e documentada: o `pyproject.toml` do core proíbe
explicitamente Pillow nas dependências, porque ele altera o comportamento de `page.images` do
pypdf e quebra o parser de ingestão de questões. OpenCV convive com Pillow no mesmo ambiente.
Isolar o ambiente preserva essa garantia sem abrir mão da visão computacional nem da geração
de cartão.

Depende de: OpenCV, NumPy, PyMuPDF, driver de banco.
O core **nunca** importa nada disso.

### 2.4 `simulado_service` — orquestração (core)

Monta simulado, cadastra gabarito, gera e registra cartões, consolida respostas, dispara
calibragem, aplica portões de qualidade e produz relatórios.

### 2.5 Comunicação core ↔ worker

Tabela `omr_jobs` no próprio Postgres, consumida com `SELECT ... FOR UPDATE SKIP LOCKED`.

É uma fila real — atômica, com retry, sem perda de mensagem em queda do worker — e não custa
nenhum serviço de infraestrutura adicional. O repositório não tem hoje nenhum mecanismo de
job em segundo plano (auditado: nenhum Celery, RQ ou fila), então esta é a menor abstração
que resolve o problema. Uma fila externa só se justifica se o volume um dia exigir.

---

## 3. Modelo de dados

Migrations a partir da **057** (a 056 é `material_assignments`, ainda não commitada).

### 3.0 Isolamento multi-tenant

**Toda tabela deste subsistema carrega `school_id`, e toda chave estrangeira entre elas é
composta** — `(school_id, parent_id)` referenciando `parent(school_id, id)`, viabilizada por um
`UNIQUE(school_id, id)` em cada pai. É a mesma regra que `src/agente_ia_edu/db/models/academic.py`
documenta e aplica em todo o modelo acadêmico, e a razão é a mesma: a coluna é redundante com a
cadeia de chaves, mas transforma "nunca misturar dados de escolas diferentes" de disciplina de
query em invariante que o banco recusa violar.

Sem isso, um único `join` mal escrito consegue ligar o aluno de uma escola ao simulado de outra
— e num subsistema cujo produto final é a nota do aluno, esse erro é caro e silencioso.

As descrições de coluna abaixo omitem `school_id` por brevidade; ele está em todas.

### Prova e gabarito

**`mock_exams`**
`id`, `school_id`, `academic_year_id`, `name`, `application_date`, `exam_day` (1 ou 2),
`status`, `created_at`, `updated_at`.

`academic_year_id` é **obrigatório**. Aluno, turma e todos os relatórios são resolvidos por
`StudentEnrollment → Class.academic_year_id`; um simulado sem ano letivo produziria roster
vazio e boletim vazio **em silêncio**, que é a pior forma de errar.

**`mock_exam_workflow_audit`**
`id`, `school_id`, `mock_exam_id`, `from_status`, `to_status`, `actor_user_id`, `occurred_at`,
`note`.

Quem publicou a nota de um simulado, e quando, precisa estar registrado. Segue o padrão de
`assessment_workflow_audit`, que já existe em `db/models/assessments.py`.

`status` percorre `DRAFT → PRINTED → APPLIED → SCANNED → CALIBRATED → PUBLISHED`.

**`mock_exam_items`**
`id`, `mock_exam_id`, `position` (1..N), `area_code`, `correct_option` (A–E),
`is_anchor` (bool), `anchor_key` (nullable), `source_question_version_id` (nullable).

- `anchor_key` é a identidade estável de um item através de simulados diferentes. Dois itens
  em simulados distintos com o mesmo `anchor_key` são a mesma questão, e é isso que torna a
  equalização possível.
- `source_question_version_id` é o gancho para o dia em que a prova for montada a partir do
  banco de questões interno. Fica nulo em todo o v1.

**`mock_exam_areas`**
`id`, `mock_exam_id`, `code`, `label`, `display_order`.

Códigos de área são dados do simulado, não enum fixo no código — a escola pode aplicar um
simulado só de Química, ou o 1º dia completo do ENEM.

### Cartão e digitalização

**`answer_cards`**
`id`, `mock_exam_id`, `student_id`, `booklet_code` (fixo em `"UNICO"` no v1),
`qr_token` (opaco, único), `template_version`, `printed_at`.

O `qr_token` é opaco por decisão de privacidade: o QR impresso no papel não carrega nome,
matrícula nem qualquer dado pessoal — apenas uma chave que só o banco resolve.

**`answer_card_scans`**
`id`, `mock_exam_id`, `answer_card_id` (nullable até o QR ser resolvido), `image_hash`,
`storage_path`, `source` (`SCANNER` | `MOBILE`), `status`, `reader_version`, `processed_at`,
`failure_reason`.

`status`: `PENDING → PROCESSED` | `NEEDS_REVIEW` | `FAILED`.

As imagens são gravadas via `MaterialStorage`
(`src/agente_ia_edu/services/material_storage.py`), o storage local endereçado por conteúdo
que já existe. Reenviar o mesmo arquivo não cria segunda cópia.

**`answer_card_marks`**
`id`, `scan_id`, `item_position`, `detected_option`, `fill_intensities` (JSON, 5 floats),
`confidence`, `resolution` (`PENDING` | `AUTO` | `HUMAN`), `resolved_option`,
`resolved_by_user_id`, `resolved_at`.

`resolution` precisa dos três estados porque `detected_option` nulo é ambíguo por si só: pode
ser item em branco (decidido — o aluno não marcou nada) ou item indeciso (esperando conferência
humana). Sem `PENDING` a fila de conferência não tem como ser montada.

Guardar as cinco intensidades medidas, e não só a conclusão, é o que permite auditar uma
leitura contestada sem reprocessar a imagem.

**`omr_jobs`**
`id`, `scan_id`, `status`, `attempts`, `last_error`, `locked_at`, `locked_by`, `created_at`.

`created_at` existe para a fila ordenar por chegada. Sem ele a ordenação cairia no `id`, que é
UUID — estável e arbitrária, mas não FIFO, e um lote enviado de manhã poderia ficar atrás de um
enviado à tarde.

### Respostas consolidadas

**`mock_exam_responses`**
`id`, `mock_exam_id`, `student_id`, `item_id`, `chosen_option` (A–E ou nulo para branco),
`is_correct`, `source` (`MANUAL` | `OMR`), `entered_by_user_id` (nulo quando `OMR`),
`created_at`.

A procedência não é opcional. Sem `source` e sem autor, uma resposta digitada à mão fica
indistinguível de uma lida pelo scanner, e não há como auditar quem digitou o cartão de um
aluno quando a nota for contestada. A fila de conferência da §5 depende dessa distinção.

É a matriz que alimenta a TRI. Um simulado de 1.500 alunos × 90 itens produz ~135 mil linhas —
volume trivial para Postgres, e a granularidade é necessária tanto para a calibragem quanto
para a análise de distratores.

### TRI

**`tri_scales`** — a régua
`id`, `school_id`, `area_code`, `name`, `base_mock_exam_id`, `reference_label`,
`reference_min_score`, `reference_median_score`, `reference_max_score`,
`reference_theta_min` (padrão −3.0), `reference_theta_max` (padrão +3.0),
`reference_source`, `reference_verified_at`, `created_at`.

Uma régua por escola e por área. É contra ela que os itens-âncora são travados, e é ela que
converte theta em nota exibida (§6.3).

Os três valores de referência são **cadastrados manualmente** pela coordenação, com os campos
pré-preenchidos a partir da última edição do ENEM (§6.3.1). `reference_source` e
`reference_verified_at` registram de onde vieram e quando foram conferidos — sem isso, ninguém
consegue auditar de onde saiu a nota de um aluno dois anos depois.

**`tri_calibrations`**
`id`, `mock_exam_id`, `area_code`, `scale_id`, `model` (`RASCH` | `2PL` | `3PL`), `status`,
`n_examinees`, `n_items`, `converged` (bool), `iterations`, `log_likelihood`,
`engine_version`, `calibrated_at`.

`engine_version` não é burocracia: quando o algoritmo for melhorado, é preciso saber quais
notas já publicadas vieram de qual versão, sem recalcular a história inteira.

**`tri_item_parameters`**
`id`, `calibration_id`, `item_id`, `a`, `b`, `c` (nullable), `se_a`, `se_b`, `n_responses`,
`p_value`, `point_biserial`, `is_fixed` (bool, âncora travada), `flags` (JSON).

**`tri_student_scores`**
`id`, `calibration_id`, `student_id`, `area_code`, `theta`, `theta_se`, `scaled_score`,
`raw_correct`, `percentile_class`, `percentile_school`.

---

## 4. Ciclo de vida

1. Coordenação cria o simulado e cadastra o gabarito (posição, área, alternativa correta,
   marcação de âncora).
2. Sistema gera os cartões nominais e o template. Status → `PRINTED`.
3. Aplicação em papel. Status → `APPLIED`.
4. Cartões digitalizados e enviados (lote em PDF ou imagens soltas). Cada arquivo vira um
   `answer_card_scan` e um `omr_job`.
5. Worker processa. Status → `SCANNED`.
6. Fila de conferência humana resolve o que ficou ambíguo.
7. Respostas consolidadas em `mock_exam_responses`.
8. Calibragem por área. Status → `CALIBRATED`.
9. Portões de qualidade (§6.5). Publicação dos relatórios. Status → `PUBLISHED`.

### Trava de integridade

**A publicação exige que nenhum cartão esteja pendente de conferência.**

Diferente de uma nota individual faltante, calibrar com parte dos alunos ausente distorce os
parâmetros dos itens — e portanto contamina a nota de *todos*. O sistema bloqueia a transição
para `CALIBRATED` enquanto houver `answer_card_scans` em `PENDING`, `NEEDS_REVIEW` **ou
`FAILED`**.

`FAILED` bloqueia pela mesma razão que os outros dois: um cartão que o leitor não conseguiu
processar é um aluno ausente da matriz de respostas, e a matriz é o insumo da calibragem. Um
cartão só sai do caminho de duas formas — sendo lido, ou sendo digitado manualmente (§9,
fase 2). Nunca sendo ignorado.

A trava vale para a transição para `CALIBRATED` **e** para a transição para `PUBLISHED`. O
efeito que ela protege é a publicação; `CALIBRATED` é apenas onde o dano se origina. Enquanto
a fase 2 não tiver leitura óptica, não existe scan algum e a trava é vacuamente verdadeira —
mas ela já é escrita e testada ali, para não precisar ser lembrada depois.

---

## 5. Pipeline de leitura óptica

Executado inteiramente dentro do `omr_worker`.

1. **Rasterizar.** PDF ou imagem → páginas a ~200 DPI (PyMuPDF).
2. **Alinhar.** Detectar os quatro marcadores ArUco de canto, calcular a homografia e
   desentortar para o tamanho canônico. É este passo que torna a foto de celular viável:
   perspectiva, rotação e escala são desfeitas de uma vez.
3. **Identificar.** Ler o QR, resolver o `answer_card` e portanto o aluno e o template.
4. **Medir.** Para cada bolha do template, intensidade média no disco interno, normalizada
   pelo fundo local imediato — o que neutraliza sombra irregular e papel amarelado.
5. **Limiarizar.** Limiar adaptativo **por cartão**, via Otsu sobre a distribuição das
   intensidades das bolhas daquele cartão. Caneta mais clara, scanner mais escuro ou foto
   subexposta deixam de ser casos especiais.
6. **Classificar** cada bolha: preenchida, vazia ou **ambígua**.
7. **Resolver por questão:** exatamente uma preenchida → resposta; nenhuma → branco; duas ou
   mais → dupla marcação; qualquer bolha ambígua → conferência humana.

### Princípio: o leitor nunca chuta

Qualquer incerteza — bolha em zona ambígua, dupla marcação, rasura, QR ilegível, ArUco não
detectado — vira item na fila de conferência, com o recorte da imagem exibido para o operador
decidir. Um OMR que adivinha é pior que um que pede ajuda, porque o erro dele é silencioso e
vira nota.

Falhas de alinhamento ou de QR marcam o scan como `FAILED` com `failure_reason` legível, e o
operador reenvia ou digita manualmente.

---

## 6. Motor de TRI

### 6.1 Calibragem

Máxima verossimilhança marginal por algoritmo EM (Bock-Aitkin):

- quadratura gaussiana de 41 pontos sobre theta
- convergência quando a maior mudança absoluta de parâmetro fica abaixo de 1e-4
- teto de 500 iterações; estourar o teto marca `converged = false`

**A distribuição de theta da população depende do modo:**

- **Calibragem livre** (primeiro simulado de uma régua, sem âncoras): prior fixo em N(0,1). A
  escala precisa de uma origem, e essa é a convenção que a fornece.
- **Calibragem equalizada** (§6.4, com âncoras travadas): média e desvio da população são
  **estimados junto com o resto**, num laço externo que alterna passo E, passo M e atualização
  da distribuição populacional.

Essa distinção é obrigatória, não um refinamento. Com o prior preso em N(0,1), travar os
parâmetros das âncoras **não produz equalização nenhuma**: o EM re-centra o grupo novo em zero
e desfaz exatamente o deslocamento que a equalização existe para medir. A turma melhora, a
régua sobe junto, a nota não mexe — o bug silencioso que as âncoras deveriam eliminar,
reintroduzido pela mecânica da estimação.

Modelo 2PL: `P(acerto | theta) = 1 / (1 + exp(-a(theta - b)))`.

### 6.2 Estimativa do theta

**EAP** (esperança a posteriori), não MLE.

Decisão deliberada: o MLE diverge para ±∞ para quem acertou tudo ou errou tudo, e com
400–1.500 alunos esses casos aparecem. O EAP produz estimativa finita e erro-padrão para
todos, ao custo de uma leve regressão à média nos extremos — um custo aceitável diante de uma
nota impossível de calcular.

### 6.3 Escala de reporte

O theta bruto não significa nada para aluno nem para professor. A nota exibida é obtida
mapeando theta na faixa de uma edição do ENEM, registrada na régua.

**O que é mapeado nos extremos.** Os extremos da escala teórica de theta — `−3.0` e `+3.0`,
configuráveis na régua — mapeiam em `reference_min_score` e `reference_max_score`. Thetas
fora dessa faixa são fixados nos extremos.

Decisão deliberada: os extremos **não** são o pior e o melhor aluno do simulado. Mínimo e
máximo observados são as duas estatísticas menos estáveis de qualquer distribuição — cada uma
é determinada por uma única pessoa — e usá-las deslocaria a régua inteira a cada aplicação.
Ancorar nos extremos teóricos de theta entrega a mesma faixa de nota com estabilidade entre
aplicações.

**Mapeamento em dois trechos.** Linear por partes, passando por três pontos:

| theta | nota |
|---|---|
| `reference_theta_min` (−3.0) | `reference_min_score` |
| `0.0` | `reference_median_score` |
| `reference_theta_max` (+3.0) | `reference_max_score` |

A mediana existe porque um mapeamento linear simples entre mínimo e máximo **infla a nota do
miolo da distribuição**. A distribuição do ENEM é fortemente assimétrica: a massa se acumula
embaixo e pouquíssimos chegam aos 900. Em Matemática 2025, o ponto médio entre mínimo e máximo
é 646, contra uma média nacional real na casa dos 520 — mais de 100 pontos de inflação para
todo aluno mediano. Passar a curva pela mediana elimina isso mantendo os extremos pedidos.

**O que essa nota é e o que não é.** É uma **escala de referência da escola**, construída para
ser legível na faixa do ENEM. **Não é previsão de nota do ENEM** — a população da escola não é
a população nacional, e nenhum item em comum liga as duas provas. Os relatórios (§7) precisam
apresentá-la com esse rótulo; tratá-la como estimativa de desempenho no ENEM real seria erro
de interpretação, não de cálculo.

### 6.3.1 Valores de referência iniciais

A régua nasce pré-preenchida com o ENEM 2025:

| Área | Mínimo | Mediana | Máximo |
|---|---|---|---|
| Linguagens | 309,2 | a computar | 794,5 |
| Ciências Humanas | 320,8 | a computar | 856,4 |
| Ciências da Natureza | 308,6 | a computar | 858,7 |
| Matemática | 312,6 | a computar | 980,3 |

**Procedência e verificação.** Mínimos e máximos acima vieram de portal educacional
secundário, **não do INEP direto**, e estão registrados no seed como não verificados
(`reference_verified_at` nulo). As medianas são calculadas a partir das colunas `NU_NOTA_*`
dos microdados públicos do ENEM 2025, que trazem a nota de cada participante.

Uma régua com `reference_verified_at` nulo **não publica nota**: o sistema calcula e mostra o
resultado marcado como provisório, e exige confirmação da coordenação. Números de terceira mão
não viram nota de aluno em silêncio.

O seed vive em `src/agente_ia_edu/data/enem_reference_scales.yaml`, seguindo o padrão de
`agente_ia_edu.rubrics` (YAML como package-data), e um script em `scripts/` computa as
medianas a partir do arquivo de microdados baixado.

### 6.4 Equalização entre simulados

A escala de reporte (§6.3) e a equalização por âncoras resolvem problemas diferentes e
operam juntas: as âncoras colocam os thetas de aplicações distintas na **mesma** régua; a
escala de reporte converte theta em nota legível. Sem as âncoras, cada simulado teria a sua
própria régua e a conversão produziria notas incomparáveis com aparência de comparáveis.

**Fixed-parameter calibration.** Os itens-âncora entram na calibragem com `a` e `b` travados
nos valores já registrados na régua (`is_fixed = true`). O EM então posiciona a distribuição
de theta do novo grupo sobre a escala existente automaticamente.

Escolhido sobre transformações posteriores (Stocking-Lord, Haebara) por ser mais simples e
por não acrescentar uma segunda etapa de estimação — cada etapa a mais é uma fonte a mais de
erro.

**Verificação de deriva.** Antes de travar, o motor estima os âncoras livremente e compara
com os valores da régua — mas **não diretamente**. A estimativa livre sai na escala do grupo
novo, então uma comparação crua marcaria *todas* as âncoras como derivadas sempre que a turma
fosse mais forte ou mais fraca que a de referência, que é precisamente o caso de uso. Antes de
comparar, aplica-se uma transformação **mean-sigma sobre o próprio conjunto de âncoras**, com
purificação iterativa: transforma, mede o desvio de cada âncora, descarta a pior, e repete até
estabilizar.

Isso não contradiz a rejeição de Stocking-Lord e Haebara acima. Lá a rejeição é do **método de
equalização**; aqui mean-sigma é apenas **instrumento de diagnóstico**, descartado depois de
identificar as âncoras saudáveis. A equalização em si continua sendo por parâmetros travados.

O limiar é configurável, com padrão de **0,5 na escala logit de `b`** (meio desvio-padrão de
theta). Âncora deslocada além do limiar é **descartada e sinalizada** —
nunca usada em silêncio. Deslocamento é a assinatura de vazamento do item (alunos tiveram
acesso à questão) ou de mudança relevante de contexto.

**Número mínimo de âncoras.** Se a purificação derrubar âncoras demais, o sistema **recusa
equalizar** em vez de equalizar mal. Padrão de 4 âncoras sobreviventes, configurável.

**Consequência operacional que precisa ser respeitada fora do software:** item-âncora não
pode vazar. O sistema marca quais itens são âncora e impede que apareçam em qualquer
devolutiva de gabarito ao aluno.

A omissão precisa ser **total e sem posição**. Marcar "questão 12: reservada" na devolutiva
entregaria de bandeja quais itens se repetem entre aplicações, matando a equalização tão bem
quanto publicar o gabarito. O item-âncora é omitido inteiro — nem alternativa correta, nem a
marcação do aluno, nem acerto/erro, nem a posição — e a devolutiva informa apenas uma
**contagem agregada** de itens omitidos. O item continua contando normalmente para a nota.

### 6.5 Portões de qualidade

Bloqueiam a publicação:

- **N insuficiente.** Menos de 200 respondentes na área → 2PL é recusado. O sistema cai para
  Rasch, ou para nota bruta apenas, sempre com aviso explícito na tela — nunca silenciosamente.
- **Não convergiu.** Calibragem sem convergência não publica.

Sinalizam sem bloquear (`flags` em `tri_item_parameters`):

- **Discriminação negativa** (`a < 0`): o aluno forte erra e o fraco acerta. É a assinatura
  clássica de **gabarito cadastrado errado**, e na prática é a verificação que mais vai evitar
  prejuízo real.
- **Discriminação baixa** (`a < 0.2`): item que não separa ninguém.
- **Item degenerado**: proporção de acerto abaixo de 0.05 ou acima de 0.95.

### 6.6 Critério para promover a 3PL

O motor nasce com o modelo plugável. O 3PL entra quando houver volume acumulado que o
sustente — na prática, quando uma régua acumular mais de ~1.500 respondentes por item — e
mesmo então com o parâmetro `c` restringido por prior bayesiano, não livre. Com N na faixa
atual, `c` é o parâmetro pior estimado dos três e a sua má estimação contamina `a` e `b`.

---

## 7. Relatórios

**Aluno.** Acertos e nota por área, nota total, percentil na turma e na escola, evolução no
ano. A nota é sempre rotulada como escala de referência da escola, nunca como previsão de nota
do ENEM (§6.3); régua não verificada exibe o resultado como provisório. A evolução por nota
absoluta só é exibida quando existe equalização por âncoras; sem elas o gráfico é
**suprimido**, não estimado — thetas de calibragens independentes estão em
réguas diferentes e compará-los seria um erro silencioso.

**Turma (professor).** Desempenho por área, questões mais erradas, distribuição das notas,
comparação com as demais turmas.

**Escola/rede (coordenação).** Comparação entre turmas e unidades, evolução por aplicação ao
longo do ano.

A evolução por aplicação está sujeita à **mesma regra do boletim do aluno**: só é exibida quando
existe equalização por âncoras. Duas aplicações são duas provas de dificuldade diferente, e uma
queda de 62% para 54% não distingue "a escola piorou" de "a prova era mais difícil". O argumento
não perde força por a média ser de uma escola em vez de um aluno — perde só a visibilidade, o que
o torna mais perigoso, não menos.

**Itens (qualidade da prova).** Dificuldade, discriminação, curva característica do item e
análise de distratores — qual alternativa errada atraiu os alunos de theta alto. Sai
diretamente dos parâmetros calibrados, sem cálculo adicional.

Os painéis de professor e coordenação já existem (`api/routes/teacher_portal.py`,
`api/routes/coordination_portal.py`) e recebem as novas seções, seguindo o padrão vigente.

---

## 8. Estratégia de verificação

### 8.1 TRI — recuperação de parâmetros

Simular 1.000 respondentes a partir de parâmetros `a`,`b` conhecidos, calibrar, e exigir que o
motor recupere os originais dentro de tolerância. Os limites de partida, a ajustar apenas com
justificativa registrada: correlação acima de 0,95 entre `b` verdadeiro e estimado, erro
quadrático médio de `b` abaixo de 0,15, e viés médio de `b` abaixo de 0,05 em valor absoluto.
A semente do gerador aleatório é fixa, para que o teste seja determinístico.

Este é o teste central do subsistema. **Um motor de TRI errado não lança exceção, não quebra
teste de integração e não falha em produção** — ele devolve números plausíveis e errados, que
viram nota de aluno. Recuperação de parâmetros é a única forma honesta de saber que está
certo.

Complementos:
- **Invariância da equalização.** Mesmos alunos, dois simulados ligados por âncoras → theta
  estável dentro de tolerância.
- **Casos-limite.** Respondente que zerou, que gabaritou, item respondido por todos, item sem
  variância.
- **Conjunto-ouro.** Uma matriz de respostas fixa, com os parâmetros conferidos contra o
  `mirt` (R), implementação de referência da área.

### 8.2 OMR — cartões sintéticos

Renderizar o cartão, preencher bolhas programaticamente, aplicar transformações de
perspectiva, ruído, borrão, sombra e subexposição, e exigir leitura correta. Cobre regressão
de forma barata e determinística.

### 8.3 Aceite físico — portão de fase, não teste unitário

Imprimir cartões reais, preencher à mão com canetas diferentes, fotografar com aparelhos
diferentes em sala de aula real, e comparar a leitura automática contra digitação manual das
mesmas folhas.

**A fase de OMR não está pronta sem essa evidência.** Este ciclo é físico e não comprime por
esforço de engenharia; o plano de implementação precisa reservar tempo de calendário para ele.

---

## 9. Fatias de implementação

Ordenadas por valor entregue e risco crescente. Cada fase entrega algo utilizável sozinha.

| Fase | Entrega | Destrava |
|---|---|---|
| 1 | Modelo de dados + gerador de cartão com QR e ArUco + template | Cartões nominais imprimíveis |
| 2 | Digitação manual de respostas + nota bruta por área + relatórios | Simulado corrigido de ponta a ponta, sem visão computacional |
| 3 | Motor de TRI + calibragem + escala de referência ENEM + análise de itens | Nota TRI legível e detecção de gabarito errado |
| 4 | Worker de OMR + fila de conferência humana | Leitura automática dos cartões |
| 5 | Âncoras + equalização + evolução no ano | Comparação real entre aplicações |

A fase 2 é o eixo desta ordem. Com digitação manual o simulado funciona de verdade antes de
existir uma linha de visão computacional, e o trabalho não é descartado depois: a digitação
permanece como caminho de exceção para cartão rasgado, aluno que preencheu a lápis, ou o dia
em que o scanner quebrar.
