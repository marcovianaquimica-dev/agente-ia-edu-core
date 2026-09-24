# Exportar devolutiva de redação em PDF

**Data:** 2026-09-24
**Status:** design aprovado, aguardando plano de implementação
**Sub-projeto B** de três levas relacionadas à devolutiva de redação (A: devolutiva rica — já em produção em `main`; C: dashboard de evolução do aluno — fora de escopo aqui, spec própria futura).
**Depende de:** a devolutiva rica de redação (spec `docs/superpowers/specs/2026-09-23-devolutiva-rica-redacao-design.md`, plano `docs/superpowers/plans/2026-09-23-devolutiva-rica-redacao.md`), já mesclada em `main` (commit `8a37887`).

---

## 1. Contexto

A devolutiva rica de redação (leva A) já renderiza, nos dois portais, um relatório completo: mensagem de abertura, notas, tabela de pontos fortes/onde avançar por competência, texto original com marcações coloridas por competência, lista de anotações, reescritas vinculadas, checklist de revisão mecânica (C1), proposta de intervenção (C5), plano de ação, mensagem de fechamento e aviso de transparência. Falta exportar esse mesmo conteúdo em PDF, pra o aluno guardar/imprimir e o professor arquivar.

Pesquisei o código antes de propor qualquer abordagem e achei um precedente direto já em produção: `services/list_export.py` já exporta listas de exercícios em PDF (e DOCX) usando **PyMuPDF** (`pymupdf`, já é dependência do projeto — `pymupdf>=1.24,<2.0` em `pyproject.toml`), com um "render model" compartilhado e uma rota que devolve `Response(media_type="application/pdf", Content-Disposition: attachment)`. Não existe nenhuma biblioteca de PDF client-side (jsPDF, html2canvas, impressão via `window.print()`) em uso em nenhum lugar do frontend. Esta leva segue o mesmo padrão — server-side, PyMuPDF — em vez de introduzir uma abordagem nova.

O usuário pediu fidelidade visual: a marcação colorida numerada do texto original, como aparece na tela, deve aparecer também no PDF (não uma versão só-texto). Validei antes de propor o design que o PyMuPDF instalado (1.28.2) tem uma API `Story` (`pymupdf.Story`) capaz de renderizar HTML+CSS — incluindo `<mark style="background-color:...">` e `<sup>` — com paginação automática via `story.place()`/`story.draw()`. Testei isso de verdade (script Python standalone, PDF gerado e validado) antes de recomendar — não é uma suposição.

---

## 2. Escopo

### Entrega

**Novo módulo `src/agente_ia_edu/services/essay_pdf_export.py`** (mesmo padrão de `list_export.py`):
- `pdf_available() -> bool` — mesma checagem de `import pymupdf`, mesmo padrão de `list_export.py`'s função homônima (pode até ser literalmente a mesma implementação copiada, já que a checagem é idêntica).
- `build_render_model(correction_view: dict) -> dict` — monta o modelo de renderização a partir dos MESMOS dados que `essay-report.js`'s `renderRichReport` já consome: `final_scores`, `final_feedback`, `rationales`, `annotations`, `rewrites`, `alerts`, `intervention`, `intro_message`, `closing_message`, `mechanical_review`. Não recebe texto/imagens — só os dados que já vêm de `ai_output`/`final_*`. **Princípio central desta leva: a lógica de fallback pra dado antigo (spec da leva A, §3 — tabela de compatibilidade) NÃO é reimplementada aqui; é a MESMA regra, só portada de JS pra Python.** Qualquer dúvida sobre "o que mostrar quando um campo novo está ausente" se resolve olhando `essay-report.js`, não reinventando.
- `render_pdf(model: dict, *, anchor_mode: str, canonical_text: str | None, page_image_paths: list[str] | None) -> bytes` — monta o documento PDF completo, recebendo o modelo de `build_render_model` MAIS o que é específico da seção "Sua redação": o texto puro (TEXT_OFFSET) ou os caminhos das imagens de página já ordenados por `page_number` (IMAGE_REGION). Ver §3 para a estratégia de montagem (texto vs. foto).

**Duas rotas novas, cada uma reaproveitando autorização já existente — nenhuma regra nova de autorização:**
- `GET /api/v1/student/essay-submissions/{essay_submission_id}/correction/export.pdf` em `essay_submissions.py` — mesma sequência de autorização de `get_essay_submission_correction` (`_authorize_student`, `_resolve_enrollment_or_403`, `_submission_for_own_school_or_403`). Só libera quando `correction.status == "APPROVED"`; caso contrário, `404` (mesma semântica de "não há devolutiva pra exportar ainda" que a rota JSON já usa implicitamente ao devolver `status: "PENDING"` sem conteúdo — aqui, como é download binário, não há corpo JSON de status pra devolver, então é um erro HTTP direto).
- `GET /api/v1/teacher/essay-corrections/{essay_correction_id}/export.pdf` em `essay_corrections.py` — mesma sequência de `_authorize`, `_correction_for_own_school_or_403`. Libera em `APPROVED` **ou** `PENDING_REVIEW`; `NEEDS_REVIEW`/`REJECTED` devolvem `404` (não há `ai_output` publicável nesses estados — mesmo critério que `showsContent` já usa no frontend do professor).

Ambas as rotas, em caso de sucesso: `Response(content=pdf_bytes, media_type="application/pdf", headers={"Content-Disposition": 'attachment; filename="..."'})`. Nome do arquivo: `devolutiva-<slug do título da proposta>.pdf`, usando o mesmo padrão de sanitização de `_filename()` em `question_bank.py` (alfanumérico + espaço/hífen/underscore, truncado em 60 caracteres) — reimplementado localmente em `essay_pdf_export.py` (não importado de `question_bank.py`, que é privado a esse router) e reaproveitado pelas duas rotas.

**Se `pdf_available()` for `False`:** `503`, mesmo padrão já usado em `question_bank.py`'s `export_list_pdf`.

### Conteúdo do PDF (idêntico à devolutiva em tela — decisão do usuário)

Todas as seções da devolutiva rica, na mesma ordem de `essay-report.js`: mensagem de abertura (se presente) → nota total + alertas → notas por competência (barras) → tabela "o que você já faz bem / onde pode avançar" (com o mesmo fallback pra `summary` quando `strengths`/`growth_area` faltam) → **sua redação** (texto ou fotos, com marcação) → anotações → reescritas (se houver, com o mesmo fallback de cabeçalho quando `letter`/`competency_code` faltam) → revisão de domínio da norma padrão (tabela estática + ocorrências dinâmicas) → proposta de intervenção (C5) → plano de ação → próxima redação (`feedback.next_essay_strategy`) → mensagem de fechamento (se presente) → aviso de transparência (sempre presente).

### 3. Estratégia de montagem do PDF

**Modo TEXT_OFFSET (redação digitada):** um único documento HTML, um único `pymupdf.Story`, contendo TODAS as seções concatenadas — incluindo o texto original com marcações (`<mark style="background-color:HEX;">trecho<sup>N</sup></mark>`, mesma lógica de ordenação/corte/sobreposição de `essay-annotations.js`'s `renderHighlightedText`, portada pra Python). O `Story` cuida de quebra de linha e paginação sozinho, com o loop padrão `while more: ... more, filled = story.place(where); story.draw(dev)`.

Cores por competência (literais, não `var(--...)`, já que o motor de CSS do `Story` não enxerga a folha de estilo do app — valores exatos lidos de `styles.css`):
- C1: `#eef2ff` (`--primary-light`)
- C2: `#ecfeff` (`--accent-light`)
- C3: `#fef2f2` (`--danger-light`)
- C4: `#fffbeb` (`--warning-light`)
- C5: `#ecfdf5` (`--success-light`)

**Modo IMAGE_REGION (foto/PDF enviado):** três partes, montadas na mesma sequência de páginas do documento:
1. **Story A** — HTML com tudo ANTES de "Sua redação" (abertura, notas, tabela de competências).
2. **Páginas desenhadas manualmente**, uma por página de imagem enviada, na ordem de `page_number` — leitura direta de `EssaySubmissionPage.storage_uri` (mesmo campo que a rota `GET .../pages/{n}/image` já usa pra servir a imagem original; sem round-trip HTTP, leitura de arquivo direta) via `page.insert_image()`, com retângulos coloridos desenhados por cima via `page.draw_rect()` nas posições das anotações daquela página — convertendo `x/y/width/height` (armazenados em pixels da imagem original) pra coordenadas da página PDF, mesma proporção que `essay-image-marker`'s CSS já calcula no frontend (`(x / naturalWidth) * larguraDaPagina`, etc.), usando as dimensões reais da imagem carregada como `naturalWidth`/`naturalHeight`. Se não houver nenhuma página enviada: uma única seção com o texto "Nenhuma página enviada." (mesmo texto que o frontend já usa nesse caso).
3. **Story B** — HTML com tudo DEPOIS de "Sua redação" (anotações, reescritas, revisão mecânica, C5, plano de ação, fechamento, transparência).

Um documento PDF pode misturar páginas produzidas por `Story.place()`/`.draw()` com páginas desenhadas diretamente via `page.new_shape()`/`page.draw_rect()`/`page.insert_image()` (a mesma API de baixo nível que `list_export.py`'s `render_pdf` já usa pra listas de exercícios) — não há conflito entre as duas técnicas no mesmo `doc`.

### Não entrega — deliberadamente

- Exportar em outros formatos (DOCX, etc.) — só PDF, como pedido.
- Exportar a devolutiva de uma correção `PENDING_REVIEW`/`NEEDS_REVIEW`/`REJECTED` pelo aluno, ou `NEEDS_REVIEW`/`REJECTED` pelo professor — mesmo critério de "conteúdo publicável" que já rege a tela.
- Qualquer mudança no fluxo de aprovação/rejeição/reenvio já existente — esta leva é só leitura, sem efeito colateral, como `list_export.py`'s rotas já são.
- Marca d'água, numeração de série, ou qualquer elemento de "documento oficial" — é uma devolutiva pedagógica, não um documento certificado (mesmo aviso de transparência já cobre isso).
- Testes automatizados de frontend (mesmo corte de todas as levas anteriores) — o botão "Exportar PDF" nos dois portais é só um link/âncora pro endpoint (mesmo padrão de `question-bank.js`'s `<a href="${API}/lists/${id}/export.pdf">`), sem lógica JS nova pra testar.

---

## 4. Compatibilidade com dado antigo

Mesma tabela da spec da leva A (§3) — reaproveitada sem alteração, porque o modelo de renderização do PDF consome os MESMOS dados, pela MESMA regra de fallback:

| Campo novo | Se ausente (correção aprovada antes da leva A) |
|---|---|
| `rationales[].strengths`/`.growth_area` | Mostra `rationales[].summary` como parágrafo único, sem a tabela de duas colunas |
| `rewrites[].letter`/`.competency_code` | Mostra o bloco de reescrita sem o cabeçalho "X - CN" |
| `mechanical_review` | Mostra só a tabela estática de referência, sem ocorrência dinâmica |
| `intro_message` | Seção de abertura omitida inteiramente |
| `closing_message` | Seção de mensagem do professor omitida inteiramente |

## 5. Autorização

Nenhuma rota nova de autorização, nenhum padrão novo — as duas rotas reaproveitam a mesma sequência de checagem que as rotas JSON equivalentes (`get_essay_submission_correction` / `list_essay_corrections`+`_correction_for_own_school_or_403`) já usam, sem modificação nelas.

## 6. O que fica para depois

Ver §2 "Não entrega". A leva C (dashboard de evolução) fica pra uma spec própria.

## 7. Rastreabilidade

- `src/agente_ia_edu/services/list_export.py` — precedente direto: padrão de `pdf_available()`, `Response` com `Content-Disposition`, uso de PyMuPDF já adotado pelo projeto.
- `src/agente_ia_edu/api/routes/question_bank.py:678-694` — `_filename()` (padrão de sanitização a replicar) e `export_list_pdf` (padrão de rota a espelhar).
- `src/agente_ia_edu/web/essay-report.js` — fonte única de verdade pro conteúdo e pra regra de fallback (§3 da leva A) que o `build_render_model` do Python precisa espelhar.
- `src/agente_ia_edu/web/essay-annotations.js` — `renderHighlightedText`/`renderImageMarkers` — algoritmo de marcação a portar pra Python (texto: ordenar por `start`, cortar, marcar; imagem: `x/y/width/height` proporcional a `naturalWidth`/`naturalHeight`).
- `src/agente_ia_edu/web/styles.css` — valores literais de `--primary-light`/`--accent-light`/`--danger-light`/`--warning-light`/`--success-light` (linhas 14-26), já levantados neste spec (§3).
- `src/agente_ia_edu/api/routes/essay_submissions.py` — `get_essay_submission_correction` (autorização a reaproveitar), `get_essay_submission_page_image` (padrão de leitura de `EssaySubmissionPage.storage_uri`).
- `src/agente_ia_edu/api/routes/essay_corrections.py` — `_authorize`/`_correction_for_own_school_or_403` (autorização a reaproveitar), `get_essay_correction_page_image` (mesmo padrão do lado professor).
- `src/agente_ia_edu/web/question-bank.js:425,739` — padrão de botão/link `<a href="${API}/.../export.pdf">Exportar PDF</a>` a replicar nos dois portais de redação.
