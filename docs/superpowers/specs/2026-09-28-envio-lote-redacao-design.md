# Envio em lote de redações físicas pelo professor

**Data:** 2026-09-28
**Status:** design aprovado, aguardando plano de implementação

---

## 1. Contexto

O professor pode ter uma turma de até ~50 alunos que escreveram uma redação em papel, numa aula presencial. Hoje ele precisa que CADA aluno depois fotografe/digite a própria redação e submeta pelo próprio login — não existe nenhum caminho pra o professor digitalizar e subir tudo de uma vez.

Pesquisei o código antes de propor qualquer design:

- **Toda submissão hoje parte do login do próprio aluno.** `POST /api/v1/student/essay-submissions` (`essay_submissions.py:199`, `create_essay_submission`) sempre usa `enrollment.student_id` resolvido do chamador autenticado — não existe parâmetro nem rota onde outra pessoa submeta em nome de um aluno.
- **A infraestrutura de OCR de foto/PDF já existe e é reaproveitável**, em `services/essay_submission.py`: `upload_page()` salva a página, `_ocr_page()` roda OCR de visão com 3 tentativas e um voto de confiança média (`_OCR_ATTEMPTS = 3`), `_split_pdf_pages()` já separa um PDF de até 20 páginas em página-imagem individual (`_MAX_PDF_PAGES = 20`) e já distingue um PDF com texto digitado real (usa a camada de texto direto) de um PDF escaneado (roda OCR de visão). Essa mesma infra será reaproveitada pra também ler o cabeçalho (nome + CPF) de cada página, não construída do zero.
- **`Person` (`db/models/academic.py:68`) já tem `full_name` e `document_number`** (pode guardar CPF) — cobre o que o matching precisa sem nenhuma migration nesses dois campos. Não tem campo de data de nascimento (avaliado e descartado no brainstorm: nome + CPF já bastam).
- **`School` (`db/models/admin.py:35`) não tem campo de logo** — precisa de uma coluna nova (`logo_storage_uri`) pra folha padrão gerada em PDF poder estampar a logo da escola.
- **O motor de geração de PDF já existe** (`services/essay_pdf_export.py`, PyMuPDF `Story`) e será reaproveitado pra montar a folha de resposta padrão, no mesmo estilo da folha oficial do ENEM (referência visual que o usuário mostrou): cabeçalho com espaço pra logo, campos NOME/CPF em caixas, ~30 linhas numeradas.
- **Não existe fila de tarefas (Celery/RQ/etc.) neste projeto** — todo processamento assíncrono hoje usa `asyncio`/`await` direto dentro do próprio processo da API. O processamento em segundo plano deste lote usa `fastapi.BackgroundTasks` (já embutido no FastAPI, sem infraestrutura nova), consistente com essa escolha do projeto.

Decisões já tomadas com o usuário durante o brainstorm (não reabrir):

1. Cada página física que o aluno usar leva nome + CPF preenchidos (igual ao ENEM) — o sistema agrupa páginas consecutivas do mesmo lote com a mesma identificação em uma única redação, na ordem em que vieram.
2. Match automático: **nome normalizado batendo com exatamente um aluno da turma já é suficiente** para aceitar sem passar pelo professor. CPF é lido e guardado, mas serve só como pista extra na tela de resolução manual — não é obrigatório para o match automático.
3. Depois de identificado, o texto lido por OCR entra direto na fila de correção por IA — sem uma etapa de confirmação humana do texto (nem do professor, nem do aluno).
4. Página que não bate com exatamente um aluno (zero ou mais de um) cai numa fila de resolução manual: o professor vê a imagem da página e escolhe o aluno certo numa lista.
5. O sistema **gera** a folha padrão em PDF pronta pra impressão (não é o professor que traz uma folha própria) — precisa da logo da escola cadastrada.
6. Processamento roda em segundo plano; o professor acompanha o progresso, não fica preso numa tela esperando.

---

## 2. Escopo

### Entrega

**Migração de banco (aditiva):**

- `schools.logo_storage_uri` (nullable) — caminho do arquivo de logo, gravado via `MaterialStorage` (o mesmo serviço de storage local, já usado pelos materiais de apoio de propostas de redação).
- Duas tabelas novas: `essay_batch_uploads` e `essay_batch_pages` (shape completo em §3).

**Backend — novo serviço `services/essay_batch.py` + novo router `api/routes/essay_batches.py`:**

- `POST /api/v1/teacher/essay-batches` — multipart: `essay_prompt_id`, `class_id`, e um ou mais arquivos (PDF e/ou imagens, mesmos tipos/limite de tamanho que já valem pra upload de página individual). Cria o `EssayBatchUpload` com `status=PROCESSING`, enfileira o processamento via `BackgroundTasks`, devolve `202` com o `id` do lote imediatamente — nunca espera o processamento terminar.
- `GET /api/v1/teacher/essay-batches/{id}` — status agregado: `total_pages`, `matched_count`, `needs_review_count`, `status` (`PROCESSING`/`DONE`), e a lista de páginas em `NEEDS_REVIEW` (cada uma com a URL da imagem, o nome/CPF que o OCR leu, se disponíveis).
- `POST /api/v1/teacher/essay-batches/{id}/pages/{page_id}/resolve` — corpo `{student_id}`. Marca a página como `RESOLVED_MANUAL`, cria (ou concatena a uma já criada por outra página do mesmo aluno neste lote) a `EssaySubmission` correspondente, dispara a correção.
- `GET /api/v1/catalog/essay-prompts/{id}/answer-sheet.pdf?copies=N` — gera o PDF da folha padrão em branco pra impressão (N cópias, uma folha por página do PDF gerado).
- `POST /api/v1/teacher/school/logo` (upload) — multipart, salva a logo via `MaterialStorage`, grava `schools.logo_storage_uri`. Rota simples, sem tela de configuração maior — cabe no mesmo formulário de onde o professor for gerar a folha padrão pela primeira vez.

**Frontend — dentro do módulo Redação do professor (`essay-review.js`):**

- Na aba "Propostas", um botão "Gerar folha de resposta" por proposta → baixa o PDF em branco (pede a logo se ainda não houver uma cadastrada).
- Novo botão "Enviar em lote" (mesma aba, ou uma aba própria "Lote" — decisão de UI fica pro plano de implementação) → formulário de proposta + turma + arquivos, dispara o upload, mostra uma tela de progresso que consulta `GET .../essay-batches/{id}` periodicamente.
- Tela de resolução manual: lista as páginas em `NEEDS_REVIEW`, imagem + seletor de aluno (só os que ainda não têm redação vinculada neste lote) + botão confirmar, chamando `POST .../resolve`.

### Não entrega — deliberadamente

- Redação que ocupa mais de uma página SEM o aluno repetir nome+CPF em cada uma — fora de escopo, decisão tomada no brainstorm (o professor orienta os alunos a preencherem o cabeçalho em toda folha usada, igual ao ENEM).
- Confirmação humana do texto lido por OCR antes da correção — decisão tomada no brainstorm (vai direto pra correção).
- Taxonomia de OCR mais fina, alerta de cópia dos textos motivadores, ou qualquer mudança no prompt de correção — fora de escopo desta leva (assuntos de outras levas já em andamento).
- Reenvio/edição de uma redação já vinculada por este fluxo depois de criada — usa o mesmo fluxo de reenvio que já existe pra submissão individual, sem rota nova.
- Testes automatizados de frontend (mesmo corte das levas anteriores de redação).

---

## 3. Modelo de dados

```
essay_batch_uploads
  id                  UUID PK
  school_id           UUID FK -> schools.id (RESTRICT)
  essay_prompt_id     UUID FK -> essay_prompts.id (RESTRICT)
  class_id            UUID FK -> classes.id (RESTRICT)
  uploaded_by_external_identity  VARCHAR(255)
  status              VARCHAR(20)  CHECK IN ('PROCESSING', 'DONE')
  total_pages         INTEGER
  created_at          TIMESTAMPTZ
  updated_at          TIMESTAMPTZ

essay_batch_pages
  id                  UUID PK
  batch_id            UUID FK -> essay_batch_uploads.id (CASCADE)
  page_number         INTEGER      -- ordem dentro do upload (1-based)
  storage_uri         VARCHAR(500) -- imagem da página, via MaterialStorage
  ocr_name_raw        VARCHAR(255) NULLABLE
  ocr_cpf_raw         VARCHAR(20)  NULLABLE
  ocr_body_text       TEXT         NULLABLE  -- corpo da redação lido por OCR
  matched_student_id  UUID FK -> students.id NULLABLE
  status              VARCHAR(20)  CHECK IN ('MATCHED_AUTO', 'NEEDS_REVIEW', 'RESOLVED_MANUAL')
  essay_submission_id UUID FK -> essay_submissions.id NULLABLE
  created_at          TIMESTAMPTZ
  updated_at          TIMESTAMPTZ

schools
  + logo_storage_uri  VARCHAR(500) NULLABLE
```

Nada em `EssaySubmission`, `EssayCorrection`, `PromptAssignment` muda — uma vez que uma página (ou grupo de páginas) é resolvida, ela produz uma `EssaySubmission` comum, indistinguível de uma enviada pelo próprio aluno, e segue o pipeline de correção existente sem nenhuma alteração.

---

## 4. Algoritmo de matching e agrupamento

1. Pra cada página do lote, na ordem em que os arquivos/páginas foram enviados: roda o OCR (reaproveitando `_ocr_page`), extraindo três coisas — nome do cabeçalho, CPF do cabeçalho, corpo da redação.
2. **Normalização do nome**: maiúsculas, sem acento (`unicodedata.normalize('NFKD', ...)` + filtro de combining marks), espaços múltiplos colapsados em um, trim. CPF: só os dígitos (remove pontuação).
3. **Match**: compara o nome normalizado da página contra o nome normalizado de cada `Student` ativo (via `Person.full_name`) matriculado na `class_id` escolhida.
   - Exatamente um aluno bate → `MATCHED_AUTO`, `matched_student_id` preenchido.
   - Zero ou mais de um batem → `NEEDS_REVIEW`, `matched_student_id` fica nulo.
4. **Agrupamento em submissão**: páginas `MATCHED_AUTO` (ou depois resolvidas manualmente) com o mesmo `matched_student_id`, dentro do mesmo lote, são ordenadas por `page_number` e concatenadas (`ocr_body_text` de cada página, na ordem, separadas por quebra de parágrafo) em uma única `EssaySubmission` — mesma criação que a rota individual já faz (`mode="PHOTO"` ou `"PDF"` conforme a origem, `canonical_text` normalizado, `status="SUBMITTED"`), disparando a correção do mesmo jeito.
5. Se depois de agrupadas todas as páginas de um aluno o total ficar vazio (nenhum texto reconhecido), a página cai em `NEEDS_REVIEW` mesmo com nome batido — evita criar uma redação vazia.

---

## 5. Processamento em segundo plano

- `POST .../essay-batches` cria o registro (`status=PROCESSING`) e devolve `202` com o `id`, sem esperar nada.
- O processamento real roda via `fastapi.BackgroundTasks`, chamado a partir da própria resposta da rota — mesmo processo Python, sem fila externa. Cada página é processada em sequência (não em paralelo, pra não estourar limite de taxa do provedor de OCR); o registro de cada `EssayBatchPage` é salvo (e commitado) assim que aquela página termina, então `GET .../essay-batches/{id}` sempre reflete o progresso real, mesmo com o lote ainda rodando.
- Ao terminar a última página, `essay_batch_uploads.status` vira `DONE`.
- Erro numa página individual (falha de OCR, arquivo corrompido) não derruba o lote inteiro — essa página vira `NEEDS_REVIEW` com nome/CPF vazios, mesmo padrão "best-effort por item" que `create_assignments_bulk` já usa em outro lugar deste projeto.

---

## 6. Geração da folha padrão

- `GET /api/v1/catalog/essay-prompts/{id}/answer-sheet.pdf?copies=N` monta um PDF de N páginas (uma folha por cópia), reaproveitando o motor PyMuPDF `Story` de `essay_pdf_export.py`.
- Cada página: logo da escola (de `schools.logo_storage_uri`, se cadastrada — se não houver, a folha sai sem logo, nunca falha por causa disso), título da proposta, campo "Nome completo do participante" em caixas, campo "CPF" em caixas, ~30 linhas numeradas pra redação.
- Sem código de barras nem número de inscrição (não existe esse conceito neste sistema) — só os dois campos que o matching usa.

---

## 7. Casos de borda

- **Turma errada escolhida no upload**: nenhuma página bate (nomes não existem naquela turma) → tudo cai em `NEEDS_REVIEW`. O professor percebe pela lista vazia de matches automáticos e pode recomeçar o lote com a turma certa; não há um "desfazer lote" além de simplesmente resolver manualmente cada página pro aluno certo (a turma da submissão final vem do `matched_student_id`/aluno escolhido, não do `class_id` do lote).
- **Dois alunos com nomes idênticos na mesma turma** (homônimos): sempre `NEEDS_REVIEW` — o CPF entra como critério de desempate manual, mostrado na tela de resolução.
- **Aluno com duas redações no mesmo lote** (ex: escreveu de novo por engano): a segunda leva de páginas com o mesmo `matched_student_id` gera uma segunda `EssaySubmission` — mesma regra de "última vale" que o sistema já usa pra múltiplos envios do mesmo aluno.
- **PDF de mais de 20 páginas**: mesmo limite (`_MAX_PDF_PAGES`) que já existe pra submissão individual, aplicado por ARQUIVO PDF enviado (não ao lote inteiro) — um único PDF continua limitado a 20 páginas, mas várias fotos individuais (uma por aluno) não passam por esse limite, já que cada arquivo de imagem é sempre 1 página.
- **Limite do lote inteiro**: no máximo 60 páginas somando todos os arquivos de um mesmo upload (folga sobre uma turma típica de ~50 alunos, sem deixar o processamento em segundo plano crescer sem controle) — acima disso, `422` na criação do lote, pedindo pra dividir em mais de um envio.
- **Logo não cadastrada** ao gerar a folha: PDF sai sem logo, nunca bloqueia a geração.

---

## 8. Testes

- Serviço: normalização de nome (acentos, maiúsculas, espaços), match exato único, match ambíguo (zero e múltiplos), agrupamento de páginas consecutivas do mesmo aluno em uma submissão, página sem texto reconhecido mesmo com nome batido.
- Rotas: fluxo completo feliz (lote pequeno, todas as páginas batem), lote com páginas em `NEEDS_REVIEW`, resolução manual criando a submissão, geração da folha em PDF com e sem logo cadastrada, upload de logo.
- Nenhuma mudança em testes já existentes de submissão individual — o pipeline de correção não muda, só o caminho até criar a `EssaySubmission`.
