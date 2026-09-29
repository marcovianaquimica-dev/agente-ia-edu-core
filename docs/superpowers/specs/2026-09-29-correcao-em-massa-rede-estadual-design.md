# Correção em massa de redações (rede estadual) — Design

## Contexto e objetivo

Um projeto separado do produto principal (portais de aluno/professor): corrigir ~50.000 redações físicas já digitalizadas de uma rede estadual de ensino, no menor tempo e custo possível, sem sacrificar a qualidade da correção nem a confiabilidade do processo.

Este documento nasceu de um spike de viabilidade (2026-09-29) que investigou, com medições reais (não estimativas de segunda mão):

- O motor de correção e o pipeline de OCR já construídos nesta mesma base de código (levas de "envio em lote de redações físicas" e o próprio motor de correção) são o ativo a reaproveitar - não serão reescritos.
- O gargalo real não é o motor de correção (que já roda no modelo de texto barato, `gpt-5.6-luna`, 500.000 tokens/min), e sim a etapa de OCR de imagem (`gpt-4o`, limitada a 30.000 tokens/min na conta atual) e a ausência total de processamento concorrente/em lote - hoje tudo roda um item por vez, síncrono, dentro da própria requisição HTTP.
- A **Batch API da OpenAI** (descoberta durante o spike, confirmada com busca real, não suposição) resolve os dois problemas de uma vez: fila de limite de taxa própria e maior (até 50.000 requisições por lote), 50% de desconto em todos os tokens, conclusão garantida em até 24h por lote. Substitui a ideia original de "controle de concorrência + retry manual", que seria mais lenta, mais cara e mais trabalhosa de construir.
- Testei o OCR de verdade contra uma foto real de redação manuscrita usada nesta sessão: 2.042 tokens por página (cabeçalho+corpo, alta qualidade de imagem). Testei também reduzir a qualidade da imagem para cortar custo - resultado: ~84% mais barato, mas a transcrição saiu visivelmente pior (palavras trocadas/inventadas) - **rejeitado**, não vale o risco de corrigir a redação em cima de um texto errado.
- O recorte cabeçalho/corpo do pipeline atual assume o layout de uma folha de resposta gerada pelo próprio sistema (`essay_answer_sheet.py`). Testado contra uma folha real, o recorte não bateu direito (leu conteúdo de corpo como se fosse cabeçalho). **As folhas reais da rede estadual ainda não foram vistas** (chegam amanhã) - o formato pode ser diferente do que o pipeline assume hoje.

## Estimativas (honestas, com faixas em vez de números falsamente precisos)

- **Tempo**: até 3 dias corridos no pior caso (3 estágios sequenciais - OCR, correção, pontuação por competência - cada um com até 24h de garantia da OpenAI), provavelmente bem menos na prática, já que lotes da OpenAI costumam terminar antes do teto.
- **Custo**: entre $300 e $600 (medido em parte - OCR e a fase 1 da correção - e estimado em parte - a fase 2 de pontuação, ainda não medida com uma chamada real). Ordem de grandeza bem estabelecida mesmo com essa incerteza.
- **Construção**: 3 a 4 dias úteis para a parte que não depende do layout da folha real; o recorte cabeçalho/corpo fica bloqueado até as fotos de amanhã chegarem.

## Escopo desta leva

**Dentro do escopo:**
- Um driver de processamento em massa que prepara, submete e coleta os 3 estágios (OCR, correção, pontuação) via Batch API da OpenAI, reaproveitando toda a lógica de validação/ancoragem/pontuação determinística que o motor de correção já tem.
- Recorte cabeçalho/corpo configurável (não mais uma fração fixa hard-coded), para se adaptar ao layout real da folha assim que ele for conhecido.
- Fluxo de amostragem: uma fração configurável das correções (ou as que a IA sinalizar com alerta/baixa confiança) entra na fila de revisão humana já existente; o resto é aprovado automaticamente.
- Uma tela de status mínima (não um painel completo) mostrando progresso agregado do lote: quantas páginas em cada estágio, quantas com erro, quantas na amostra de auditoria.
- Provisionamento em massa de alunos/turmas a partir de uma planilha/CSV, reaproveitando o modelo de dados por escola já existente (não um modelo de dados novo).

**Fora do escopo desta leva:**
- Uma interface de operação rica/interativa (fica pra depois, se o projeto continuar).
- Suporte a redações digitadas nesta rede estadual (o caso é 100% papel escaneado).
- Qualquer mudança na lógica de pontuação/prompt já existente - a inteligência da correção não muda, só a forma de disparar e coletar em massa.
- Calibração fina do recorte cabeçalho/corpo para múltiplos formatos de folha diferentes ao mesmo tempo - se a rede usa mais de um modelo de folha, cada formato novo é uma calibração adicional, não coberta automaticamente por este design.

## Arquitetura

Três estágios sequenciais, cada um um lote da Batch API, cada um dependendo do resultado do anterior:

**Estágio 1 - OCR.** Para cada página escaneada: um arquivo JSONL com uma requisição de transcrição por página (reaproveitando o mesmo prompt e formato de `EssayTranscriptionProvider.transcribe_page`, só que empacotado em lote em vez de chamado diretamente). Cada linha do JSONL carrega um `custom_id` que é o `EssayBatchPage.id` - é assim que o resultado volta a ser associado à página certa depois. Ao terminar, os resultados são lidos e gravam `ocr_name_raw`/`ocr_cpf_raw`/`ocr_body_text` exatamente como `_process_page` já faz hoje (reaproveitado, não reescrito) - só a chamada síncrona vira uma leitura do arquivo de resultado do lote.

**Estágio 2 - Correção (fase 1).** Depois que as páginas de um aluno são agrupadas em `EssaySubmission` (reaproveita `_materialize_run`/`consecutive_runs`, já existentes), um JSONL com uma requisição de correção por submissão (mesmo prompt `essay_correction_v15`, mesmo contrato `v5`). `custom_id` = `EssaySubmission.id`. Resultado de cada linha passa pela MESMA validação que já existe (`validate_engine_output_from_payload`) - se uma linha falhar a validação, essa submissão cai em `NEEDS_REVIEW` exatamente como hoje, sem exigir nenhuma lógica de erro nova.

**Estágio 3 - Pontuação (fase 2).** Para cada correção que passou da fase 1 com `scores` não nulo: as chamadas de `_score_competencies_from_evidence`/`_review_anula_redacao_alerts` (hoje concorrentes via `asyncio.gather` dentro de uma única correção) viram um lote só, uma linha por chamada, todas as correções do lote inteiro juntas. `custom_id` codifica `essay_correction_id` + qual chamada é (ex: `"{correction_id}:C1"`, `"{correction_id}:alert"`).

Cada estágio é conduzido por um **driver resumível** (script, não uma rota HTTP) que grava o `batch_id` da OpenAI e o estágio atual em uma tabela pequena e nova (`MassCorrectionRun`), para que, se o processo cair no meio, ele retome de onde parou (consulta o status do lote existente em vez de submeter de novo) - sem precisar reimplementar nada, `EssayCorrectionService.correct()` já é idempotente e o resto do pipeline já foi desenhado para isso.

## Componentes novos

- `services/mass_correction_batch.py` (novo): monta os arquivos JSONL de cada estágio, faz upload (`POST /v1/files`), cria o lote (`POST /v1/batches`), consulta status (`GET /v1/batches/{id}`), baixa e interpreta o arquivo de resultado, aplica cada resultado usando as funções/métodos JÁ existentes (nunca duplica a lógica de validação/pontuação).
- `db/models/mass_correction_run.py` (novo): uma tabela pequena de acompanhamento - `id`, `school_id`, `stage` (`OCR`/`CORRECTION`/`SCORING`/`DONE`), `openai_batch_id`, `status`, `created_at`, `completed_at`. Não guarda os dados em si (isso continua em `EssayBatchPage`/`EssaySubmission`/`EssayCorrection`, já existentes) - só o progresso do lote junto à OpenAI.
- `services/essay_batch.py` (modificar): `_crop_regions`'s fração de corte deixa de ser uma constante fixa e passa a ser um parâmetro configurável por lote (ou por escola), para acomodar o layout real assim que for conhecido.
- Um comando de linha de comando (`scripts/run_mass_correction.py`, novo) que dispara/retoma o driver.
- Uma rota HTTP de status mínima (`GET /api/v1/admin/mass-correction-runs/{id}`) + uma página HTML simples mostrando os números agregados - reaproveita o padrão de autenticação/autorização já usado pelas rotas administrativas existentes.

## Amostragem e revisão

Uma fração configurável (sugestão inicial: 3-5%, ajustável) das correções da fase 1+2 concluídas é selecionada para a fila de revisão humana já existente (`PENDING_REVIEW`, mesma tela de professor/coordenador de hoje) - priorizando correções com `alerts` não vazios ou `scores` ausentes (falha) sobre uma amostra aleatória do resto. As demais são aprovadas via `bulk_approve` (rota já existente, não precisa de nada novo).

## Riscos abertos (não resolvidos por este documento)

1. **Layout da folha real** - bloqueado até as fotos chegarem amanhã. O recorte configurável (acima) é a mitigação, mas a calibração em si (que fração usar) só acontece depois.
2. **Estimativa de tokens da fase 2** - baseada em cálculo, não em uma chamada real medida (diferente do OCR e da fase 1, que foram medidos de verdade). Vale medir com uma chamada real antes de comprometer o orçamento final.
3. **Duração real por estágio** - a garantia da OpenAI é um teto (24h), não uma promessa de tempo exato; o tempo real de cada estágio só se sabe rodando de verdade.
4. **Se a rede estadual usa mais de um modelo de folha física**, cada formato novo exige sua própria calibração de recorte - não coberto automaticamente.

## Testes

TDD nos componentes novos, seguindo os padrões já usados no projeto:
- `services/mass_correction_batch.py`: testes de unidade para a montagem do JSONL (formato exato, `custom_id` correto) e para a aplicação dos resultados de volta aos modelos (`EssayBatchPage`/`EssaySubmission`/`EssayCorrection`), com um cliente OpenAI falso/injetado - nenhum teste chama a API de verdade.
- `db/models/mass_correction_run.py`: teste de migration Postgres real (mesmo padrão de `test_r4_essay_batch_migration_postgresql.py`/`test_r5_platform_essay_prompts_migration_postgresql.py`).
- `essay_batch.py`'s recorte configurável: teste confirmando que a fração antiga continua sendo o padrão (retrocompatibilidade com o que já funciona hoje) e que uma fração customizada é respeitada.
- Verificação manual (não automatizável sem gastar dinheiro de verdade): rodar o driver contra um lote pequeno real (5-10 páginas) end-to-end antes de rodar contra as 50 mil, incluindo a submissão real à Batch API e a espera pelo resultado.
