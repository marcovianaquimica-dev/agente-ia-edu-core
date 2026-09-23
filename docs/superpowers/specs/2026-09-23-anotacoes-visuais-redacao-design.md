# Destaque visual das anotações na redação (texto e imagem)

**Data:** 2026-09-23
**Status:** design aprovado, aguardando plano de implementação
**Sub-projeto:** segunda leva do frontend de redação — adiciona overlay visual das anotações da IA sobre o conteúdo original (texto ou imagem da página), nos dois portais.
**Depende de:** o frontend de redação (`docs/superpowers/specs/2026-09-22-frontend-redacao-design.md`), já implementado e mesclado em `feature/frontend-redacao` — `essay.js` (devolutiva do aluno, Task 7) e `essay-review.js` (painel de revisão do professor, Task 10) já existem e renderizam a lista de anotações sem nenhuma marcação visual sobre o conteúdo.

---

## 1. Contexto

O frontend de redação entregue na leva anterior mostra a devolutiva/revisão como uma lista estruturada de anotações (`letter`, `competency_code`, comentário curto/longo, sugestão pedagógica), cada uma citando um trecho (`anchor.quote` para `TEXT_OFFSET`) ou o texto lido pela IA numa região (`anchor.read_text` para `IMAGE_REGION`) — mas sem nenhuma marcação desenhada sobre o texto ou a imagem original. Isso foi uma exclusão deliberada da leva anterior (§2 "Não entrega" daquele spec: "Overlay visual das anotações sobre o texto/imagem da redação").

Esta leva reverte essa exclusão: tanto a devolutiva do aluno quanto o painel de revisão do professor passam a mostrar o conteúdo original (texto digitado ou imagens das páginas) com marcadores numerados, coloridos por competência, apontando exatamente onde cada anotação se refere — com uma caixa de explicação ao passar o mouse ou tocar no marcador.

Nenhum modelo de dado muda. `Annotation.anchor` (`TextOffsetAnchor` ou `ImageRegionAnchor`, `essay_engine_contract/v1.py`) já carrega tudo que é necessário para posicionar a marcação — esta leva é inteiramente sobre expor o conteúdo (texto/imagens) para o professor, que hoje só o aluno pode ler, e sobre o componente de renderização do destaque em si.

---

## 2. Escopo

### Entrega

**Backend — rotas novas ou enriquecidas (aditivas, nenhuma mexe em modelo de dados):**
1. `GET /api/v1/teacher/essay-corrections/{essay_correction_id}/submission-content` — dado o id de uma correção, retorna o conteúdo da submissão associada: `anchor_mode`, `canonical_text` (quando `TEXT_OFFSET`) e a lista de páginas (quando `IMAGE_REGION`).
2. `GET /api/v1/teacher/essay-corrections/{essay_correction_id}/pages/{page_number}/image` — mesma lógica de `FileResponse` da rota já existente do aluno (`GET /api/v1/student/essay-submissions/{id}/pages/{page_number}/image`, R3), espelhada para autorização de professor.
3. Enriquecer `GET /api/v1/student/essay-submissions/{id}/correction` (R3, devolutiva do aluno) com `canonical_text: str | null` — lacuna real encontrada ao desenhar esta leva: hoje só a resposta do `POST`/`confirm` carrega `canonical_text`, que não persiste após o aluno recarregar a página ou voltar em outra sessão. O caso `IMAGE_REGION` já está coberto pela rota `GET .../pages` (R2, já existente, devolve `storage_uri`/`page_number` de cada página, sem mudança necessária) — só falta o texto no caso `TEXT_OFFSET`.

**Frontend — componente compartilhado (`src/agente_ia_edu/web/essay-annotations.js`, novo arquivo, carregado nos dois portais):**
- `renderHighlightedText(canonicalText, annotations)` — retorna HTML do texto com `<mark>` numerado e colorido por competência em cada trecho ancorado por `TEXT_OFFSET`.
- `renderImageMarkers(annotations, pageNumber)` — retorna os elementos de marcador posicionados por porcentagem sobre uma `<img>` já carregada, para anotações ancoradas por `IMAGE_REGION` naquela página.
- `wirePopovers(container)` — liga o hover/tap de cada marcador a uma caixa flutuante com o comentário da anotação; chamado depois de qualquer um dos dois renders acima.
- Paleta de cores por competência, mapeada nas variáveis CSS já existentes (`--primary`/`--accent`/`--danger`/`--warning`/`--success`) — uma constante `COMPETENCY_COLORS` exportada pelo módulo, reaproveitada nos dois portais para manter a mesma cor por competência em todo lugar (inclusive nas barras de nota já existentes, se fizer sentido no momento da implementação).

**Frontend — aluno (`src/agente_ia_edu/web/essay.js`):**
- A tela de devolutiva (`renderApprovedDevolutiva`, já existente) passa a buscar o conteúdo da própria submissão (rota já existente, `GET /api/v1/student/essay-submissions/{id}` mais o que já está disponível — ver §3) e renderizar o texto ou as imagens de página com os marcadores, antes ou ao lado da lista de anotações que já existe.
- A lista de anotações ganha o número do marcador ao lado de cada item, para o vínculo visual entre lista e marcação.

**Frontend — professor (`src/agente_ia_edu/web/essay-review.js`):**
- O painel de revisão (`renderReviewPanel`, já existente) passa a buscar o conteúdo da submissão via a nova rota de professor (§3) e renderizar do mesmo jeito que o aluno vê.
- Mesma numeração compartilhada entre marcador e item da lista.

### Não entrega — deliberadamente

- Edição do texto/imagem a partir do painel de revisão — o professor continua editando só nota/feedback (já existente), nunca o conteúdo da redação em si.
- Zoom/pan na imagem da página além do que o navegador já oferece nativamente — sem componente de visualizador de imagem dedicado.
- Reposicionamento manual de uma âncora imprecisa pelo professor — se a IA errou a posição, o professor aprova/rejeita/tenta de novo como já faz hoje; não há edição de âncora nesta leva.
- Testes automatizados de frontend — mesmo corte da leva anterior; verificação manual via browser real.

---

## 3. Rotas novas de backend

### `GET /api/v1/teacher/essay-corrections/{essay_correction_id}/submission-content`

Mesmo router `essay_corrections_router`. Autorização: `_authorize` + `_correction_for_own_school_or_403` (já existentes, R3, sem mudança).

Resposta 200:
```
{
  "essay_submission_id": UUID,
  "anchor_mode": "TEXT_OFFSET" | "IMAGE_REGION",
  "canonical_text": str | null,
  "pages": [{ "page_number": int }] | null
}
```
`canonical_text` presente e `pages` nulo quando `anchor_mode="TEXT_OFFSET"`; o inverso quando `"IMAGE_REGION"`. `pages` só precisa do número — a imagem de cada uma é buscada à parte pela rota de imagem, não embutida aqui (mesmo padrão que a Task 6 já usa do lado do aluno).

### `GET /api/v1/teacher/essay-corrections/{essay_correction_id}/pages/{page_number}/image`

Mesmo router. Autorização: `_authorize` + `_correction_for_own_school_or_403`. Mesma lógica de `FileResponse(page.storage_uri)` da rota gêmea do aluno (`essay_submissions.py`, Task 6) — resolve `essay_correction_id` → `essay_submission_id` → a página pedida, 404 se a página não existe, 403 (nunca 404) se a correção não é da escola do professor.

### Enriquecimento de `GET /api/v1/student/essay-submissions/{id}/correction`

Mesma rota, mesma autorização (`_authorize_student` + `_submission_for_own_school_or_403`, já existentes, sem mudança), mesma lógica de colapso `PENDING`/`APPROVED`. `StudentCorrectionResponse` ganha um campo novo:
```
canonical_text: str | null
```
Populado a partir de `submission.canonical_text` só quando `status="APPROVED"` (mesmo critério de todos os outros campos de conteúdo dessa resposta — `null` enquanto pendente, igual `final_scores`/`final_feedback`/`annotations` já fazem hoje). Sempre `null` quando `anchor_mode="IMAGE_REGION"` (nesse modo `canonical_text` nunca é preenchido no banco — ver `EssaySubmissionService`).

---

## 4. Fluxo do aluno (adição à devolutiva já existente)

Ao abrir a devolutiva com `status="APPROVED"`: `GET .../correction` (já existente, agora com `canonical_text`, §3) já traz tudo que é necessário para o caso `TEXT_OFFSET` numa única chamada. Se `anchor_mode="IMAGE_REGION"`: mais uma chamada a `GET .../pages` (já existente, R2, sem mudança) para a lista de páginas, e a imagem de cada uma carregada do jeito que a Task 6 já faz (blob URL autenticado). Se `anchor_mode="TEXT_OFFSET"`: renderiza `canonical_text` com os `<mark>` numerados via `EssayAnnotations.renderHighlightedText`. Se `"IMAGE_REGION"`: renderiza cada página com os marcadores via `EssayAnnotations.renderImageMarkers`. Depois de renderizar, chama `wirePopovers` para ligar hover/toque. A lista de anotações (já existente) ganha o número correspondente ao lado de cada item.

## 5. Fluxo do professor (adição ao painel de revisão já existente)

Ao abrir o painel de revisão de uma correção: busca `GET .../submission-content` (nova rota, §3). Mesma lógica de renderização do aluno — texto com `<mark>` ou imagens com marcador, dependendo de `anchor_mode` — usando o mesmo componente compartilhado `essay-annotations.js`. Isso vale também para o estado terminal (`APPROVED`/`REJECTED`, já tratado como somente leitura desde a rodada de correção da Task 10) — o conteúdo original aparece ali também, não só no estado `PENDING_REVIEW`.

---

## 6. Componente compartilhado e exceção de convenção

Este código quebra deliberadamente a convenção hoje seguida em todo o resto do frontend ("um helper por arquivo, sem import entre portais") — carregar `essay-annotations.js` nos dois HTMLs (`index.html` e `teacher.html`), antes de `essay.js`/`essay-review.js` respectivamente. Motivo: o problema de posicionar uma marcação numerada e colorida a partir de uma âncora é idêntico nos dois portais; duplicar essa lógica (que inclui cálculo de porcentagem sobre dimensão de imagem, lógica de popover com detecção de touch, e a paleta de cores) arriscaria as duas cópias divergirem silenciosamente. O módulo expõe só três funções (`renderHighlightedText`, `renderImageMarkers`, `wirePopovers`) mais a constante de cores — uma superfície pequena e estável, no mesmo espírito de `evolution.js` ser o único precedente de módulo compartilhado hoje (ainda que `evolution.js` não seja importado por mais de um portal — este é o primeiro caso desse tipo neste código).

### Posicionamento do marcador na imagem

Sem coluna nova no banco. `ImageRegionAnchor.x/y/width/height` (`essay_engine_contract/v1.py`) são pixels absolutos no espaço da própria imagem armazenada — confirmado via `EssaySubmissionService._measure_page_image`, que mede esse mesmo arquivo com `pymupdf` antes de mandar pra IA. O `<img>` já carregado (Task 6, via blob URL) expõe `naturalWidth`/`naturalHeight` assim que carrega, que batem exatamente com esse espaço. O marcador é posicionado por porcentagem (`left = x/naturalWidth*100%`, `top = y/naturalHeight*100%`, `width = width/naturalWidth*100%`, `height = height/naturalHeight*100%`) — funciona em qualquer tamanho de tela renderizado, sem precisar saber a resolução de antemão nem pedir nova informação ao backend.

### Popover

`mouseenter`/`mouseleave` no desktop; `click`/`tap` alterna aberto/fechado em touch, detectado via `matchMedia('(hover: none)')` (mesmo padrão que a Task 6 já usa para o input de câmera); clique fora do popover ou em outro marcador fecha o atual. Posição ajustada para não estourar a borda da tela/container, via `getBoundingClientRect` no momento de abrir.

### Numeração e cor

Numeração sequencial na ordem em que as anotações aparecem no array `annotations` retornado pela API (mesma ordem em que a lista já existente as renderiza hoje — sem reordenar por posição no texto/imagem, para a lista e os marcadores usarem exatamente o mesmo número sem cálculo adicional). Cor fixa por `competency_code` (`C1`→`--primary`, `C2`→`--accent`, `C3`→`--danger`, `C4`→`--warning`, `C5`→`--success` — mapeamento arbitrário mas fixo, sem significado semântico entre a cor e a competência além de diferenciá-las visualmente).

### Casos de borda

- `evidence_kind="GLOBAL"`: não gera marcador (âncora não é um ponto confiável para apontar visualmente) — fica só na lista, sem número (ou com um marcador textual tipo "—" no lugar do número, decisão de implementação).
- Página cuja imagem falha ao carregar (já tratado na Task 6 com um `alt` de erro): os marcadores dessa página simplesmente não aparecem, sem quebrar o restante da tela.
- `anchor.start`/`anchor.end` fora dos limites de `canonical_text` (não deveria acontecer, mas a IA pode errar): a marcação daquele item específico é descartada silenciosamente — some da visualização, mas o item continua na lista de anotações.

---

## 7. Autorização

As duas rotas novas reaproveitam o padrão 403-nunca-404 já usado em `essay_corrections.py` (`_authorize` + `_correction_for_own_school_or_403`, R3, sem modificação) — o professor só lê conteúdo de submissões cuja correção é da própria escola dele, nunca de outra escola. Nenhum padrão de autorização novo é introduzido.

## 8. O que fica para depois, explicitamente

Ver §2 "Não entrega". Resumo: edição de conteúdo pelo professor, zoom/pan dedicado na imagem, reposicionamento manual de âncora, testes automatizados de frontend.

## 9. Rastreabilidade

Toda rota/campo referenciado abaixo já existe hoje, sem modificação, e este documento é responsável por produzir dados compatíveis com eles:

- `src/agente_ia_edu/essay_engine_contract/v1.py` (R1) — `TextOffsetAnchor`/`ImageRegionAnchor`/`Annotation` — a forma exata da âncora que decide texto vs. imagem e a posição do marcador.
- `src/agente_ia_edu/api/routes/essay_corrections.py` (R3) — `_authorize`/`_correction_for_own_school_or_403` — reaproveitados sem modificação pelas duas rotas novas de professor.
- `src/agente_ia_edu/api/routes/essay_submissions.py` (R3, Task 6 da leva anterior) — `GET .../pages/{page_number}/image` — precedente direto (mesma lógica de `FileResponse`) para a rota gêmea de professor; `GET .../pages` (R2) e `GET .../correction` (Task 2 da leva anterior, `StudentCorrectionResponse`) — reaproveitadas/enriquecidas do lado do aluno, sem mudança de autorização.
- `src/agente_ia_edu/services/essay_submission.py` (`_measure_page_image`) — confirma que `ImageRegionAnchor.x/y/width/height` estão no mesmo espaço de pixel que `<img>.naturalWidth/naturalHeight` expõe no navegador.
- `src/agente_ia_edu/web/essay.js` (Task 7 da leva anterior) — `renderApprovedDevolutiva`, carregamento de imagem via blob URL (Task 6) — pontos de extensão para o novo overlay.
- `src/agente_ia_edu/web/essay-review.js` (Task 10 da leva anterior) — `renderReviewPanel` — ponto de extensão do lado do professor.
- `src/agente_ia_edu/web/styles.css` (`:root`) — `--primary`/`--accent`/`--danger`/`--warning`/`--success` — paleta reaproveitada para a cor por competência, sem cores novas.
