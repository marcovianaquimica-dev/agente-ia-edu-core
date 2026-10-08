# Frontend do perfil Aluno — auditoria do que existe

Auditoria feita **antes** de qualquer alteração de código. Nenhum arquivo do
frontend atual foi modificado para produzir este documento.

Convenção: **[F]** fato medido · **[H]** hipótese · **[R]** recomendação.

---

## 1. O que existe hoje

**Stack:** HTML + CSS + JavaScript puros, sem framework, sem build. O aluno
é uma SPA de arquivo único.

| arquivo | linhas | o que é |
|---|---:|---|
| `web/index.html` | 598 | o portal do aluno inteiro |
| `web/app.js` | 2.557 | toda a lógica do aluno |
| `web/styles.css` | 1.822 | estilos, compartilhados |

**[F] 151 `id`s distintos em `index.html`.** Um único documento carrega 14
painéis de visão.

**[F] 24 tokens de design em `:root`** — cor, raio, sombra, tipografia. São
reaproveitáveis e não precisam ser refeitos.

**[F] As media queries são `max-width`** — o CSS é **desktop-first**. O
pedido é mobile-first; isto é uma inversão de abordagem, não um ajuste.

---

## 2. O backend do aluno é muito mais capaz do que a interface sugere

**[F] 40 endpoints distintos** já servem o aluno, em 47 chamadas no `app.js`.

E o achado central desta auditoria:

> **O motor já faz quase tudo que se pediu para a nova experiência. A
> interface é que expõe a máquina em vez de conduzir o aluno.**

### `POST /api/v1/student/study-session` já aceita o tempo disponível

```python
class _StudySessionCreateRequest(_BaseModel):
    available_minutes: int | None = Field(default=None, ge=5, le=600)
    no_timer: bool = False
    target_content_codes: list[str] | None = Field(default=None, max_length=8)
```

**[F] "Quanto tempo você tem hoje?" não é um mock a construir — é um
parâmetro que o backend já recebe e usa.**

### O planejador já monta a sessão sequencial

`services/study_session_planner.py`, determinístico e sem IA:

```
BLOCK_STUDY · BLOCK_PRACTICE · BLOCK_REVIEW · BLOCK_BREAK · BLOCK_DIAGNOSE
```

E já distribui o tempo conforme o **estado do aluno naquele conteúdo**:

| estado | estudar | praticar | revisar |
|---|---:|---:|---:|
| `INSUFFICIENT_EVIDENCE` | 60% | 40% | 0% |
| **`BLOCKED_BY_PREREQUISITE`** | **80%** | 20% | 0% |
| `RECOMMENDED` | 15% | 50% | 35% |
| `READY` | 0% | 70% | 30% |
| `NEEDS_REVIEW` | 0% | 40% | 60% |
| `MASTERED` | 0% | 35% | 65% |

**[F] O cenário do pré-requisito — "para estequiometria, primeiro duas
bases" — já tem estado próprio (`BLOCKED_BY_PREREQUISITE`) e política de
tempo própria.**

E o planejador já se recusa a inventar:

> *"A block with no executable experience yet is emitted with
> `action_available=False` and a clear note — never a fake action."*

**[R]** Essa regra do backend deve virar regra da interface: **nunca mostrar
um botão que não leva a lugar nenhum.**

### Contratos disponíveis para a Home

| dado | de onde vem |
|---|---|
| `welcome_message`, `has_data` | `/student/dashboard` |
| `active_recommendation` | `/student/dashboard` |
| tarefa da escola com `due_at`, `availability` | `/student/activities` |
| sessão de hoje / retomada | `/student/study-session/today` |
| estado por conteúdo | `/student/learning-path` |

**[F] Os estados de Home A–H pedidos são quase todos derivados de dados que
já existem.** O que falta é a lógica de decidir **qual** mostrar.

---

## 3. O problema: 13 destinos permanentes

```
🏠 Início              ⏱️ Momento de Aprendizado   🩺 Diagnóstico Inicial
🎯 Minha Trilha        ✏️ Praticar                 📋 Atividades
🧭 Meu Domínio         🪜 Trilha de Estudos        📚 Materiais
🎥 Vídeos              📈 Minha Evolução           👤 Meu Perfil
🔀 Trocar de ambiente
```

**[F] Dois itens têm praticamente o mesmo nome:** *"Minha Trilha"*
(`learning-path`) e *"Trilha de Estudos"* (`study-path`). Um aluno não tem
como saber a diferença pelo rótulo.

**[F] Seis dos treze expõem a arquitetura interna, não uma intenção do
aluno:** Diagnóstico Inicial, Minha Trilha, Meu Domínio, Trilha de Estudos,
Momento de Aprendizado, Materiais. São nomes de subsistema.

O aluno não chega pensando *"vou abrir Meu Domínio"*. Ele chega pensando
*"tenho prova sexta"* ou *"não entendi ligações"*.

---

## 4. A Home é um dashboard, não uma condução

Composição atual de `view-dashboard`:

| | elemento | o que responde |
|---|---|---|
| 1 | banner de boas-vindas | — |
| 2 | **Média Geral** `0.0%` | "como eu vou?" |
| 3 | **Conteúdos Dominados** | "como eu vou?" |
| 4 | **Questões Respondidas** | "como eu vou?" |
| 5 | **Sequência Ativa** | "como eu vou?" |
| 6 | O Que Você Deve Estudar Agora | **"o que faço agora?"** |
| 7 | Plano de Ação por Conteúdo (3 listas: 🔴 🟡 🟢) | "o que falta?" |

**[F] 7 blocos de topo. Quatro são métricas. Um responde a pergunta que
importa.**

**[F] O "Plano de Ação" mostra as três faixas ao mesmo tempo** — é
exatamente o *"você precisa estudar 37 assuntos"* que gera ansiedade.

**[F] A primeira coisa que um aluno novo vê é `Média Geral 0.0%`.** Sem
histórico, o dashboard comunica fracasso antes de comunicar qualquer coisa.

---

## 5. Inventário elemento a elemento

`elemento → função → frequência provável → valor para o aluno → decisão`

### Navegação

| elemento | função | frequência | valor | decisão |
|---|---|---|---|---|
| Início | ponto de partida | toda sessão | alto | **MANTER** |
| Momento de Aprendizado | sessão por tempo | toda sessão | **alto** | **SIMPLIFICAR** — vira a ação principal da Home, não um menu |
| Atividades | tarefas da escola | alta quando há tarefa | alto | **MOSTRAR QUANDO NECESSÁRIO** — a Home traz a tarefa |
| Praticar | questões avulsas | média | médio | **SIMPLIFICAR** — absorvido por "Estudar" |
| Minha Trilha | passos de um conteúdo | baixa | médio | **AUTOMATIZAR** — vira o conteúdo da sessão |
| Trilha de Estudos | ordem dos conteúdos | baixa | médio | **AUTOMATIZAR** — idem, e o nome colide |
| Meu Domínio | mapa de domínio | baixa | baixo p/ o aluno | **RETIRAR DO MVP** — é visão de máquina |
| Diagnóstico Inicial | calibragem | **uma vez** | alto na 1ª vez | **MOSTRAR QUANDO NECESSÁRIO** — é o estado A |
| Materiais | biblioteca | baixa | médio | **AUTOMATIZAR** — material chega dentro do bloco STUDY |
| Vídeos | recomendação de vídeo | baixa | médio | **AUTOMATIZAR** — idem |
| Minha Evolução | gráficos | baixa | médio | **SIMPLIFICAR** — vira "Meu progresso", enxuto |
| Meu Perfil | dados e preferências | rara | baixo | **MOSTRAR QUANDO NECESSÁRIO** — avatar no topo |
| Trocar de ambiente | multi-perfil | rara | baixo | **MOSTRAR QUANDO NECESSÁRIO** — dentro do perfil |

### Home

| elemento | função | valor | decisão |
|---|---|---|---|
| banner de boas-vindas | saudação | baixo isolado | **SIMPLIFICAR** — funde com a pergunta principal |
| Média Geral | métrica | baixo p/ agir | **RETIRAR DO MVP** |
| Conteúdos Dominados | métrica | baixo p/ agir | **MOSTRAR QUANDO NECESSÁRIO** — em progresso |
| Questões Respondidas | métrica de volume | **nenhum** | **RETIRAR DO MVP** |
| Sequência Ativa | hábito | médio | **MOSTRAR QUANDO NECESSÁRIO** — em progresso |
| O Que Estudar Agora | **a pergunta certa** | **alto** | **MANTER** — vira o centro |
| Plano de Ação (3 listas) | panorama | médio, ansiogênico | **MOSTRAR QUANDO NECESSÁRIO** — só em progresso |

---

## 6. Restrição que decide a abordagem

**[F] Os testes de frontend são asserções estáticas por regex sobre o
markup.** Não há DOM nem navegador: cada teste lê `index.html`, `app.js` e
`styles.css` como texto e casa expressões regulares.

```js
assert.match(indexHtml, /data-view="study-session"[\s\S]*?Momento de Aprendizado/);
assert.match(appJs, /'study-session':\s*\{ title: 'Momento de Aprendizado'/);
```

**[F] 11 arquivos de teste leem `index.html` ou `app.js`.** Reestruturar
esses dois arquivos quebraria todos.

**[F] Baseline medido: `node --test "tests/*frontend.js"` → 283 testes, 282
passam, 1 falha.** A falha é **pré-existente e alheia ao aluno**
(`test_phase29_authorial_question_review_frontend.js`, sobre `teacher.js`).

### Decisão de abordagem

**O aluno novo nasce como página separada** — `aluno.html`, `aluno.js`,
`aluno.css` — reaproveitando os tokens de `styles.css`.

| | |
|---|---|
| quebra testes existentes | **zero** |
| reversível | sim, é só apagar três arquivos |
| permite comparar | sim, lado a lado com `/` |
| atende §22 | "prototipar isoladamente" |

O `index.html` atual **continua funcionando e intocado**. Promover a nova
experiência a padrão é decisão sua, não minha.

---

## 7. O que vai mudar, em números

| | hoje | proposta |
|---|---:|---:|
| itens de navegação | **13** | ver `frontend-aluno-experiencia.md` |
| blocos de topo na Home | **7** | |
| métricas na Home | **4** | |
| painéis de visão | **14** | |
| `id`s no documento | **151** | |

Os números da coluna da direita saem do mapa de experiência, não desta
auditoria — não quero fixar o alvo antes de desenhar os fluxos.

---

## 8. O que NÃO foi tocado

`index.html` · `app.js` · `styles.css` · `teacher.*` · `coordination.*` ·
`admin.*` · `reception.*` · `essay*.js` · `question-bank.*` · `entrada.*` ·
backend · CÉREBRO · Question Bank · ingestão ENEM.

---

## 9. Dependências e dívidas registradas

| | item | situação |
|---|---|---|
| 1 | CSS atual é desktop-first (`max-width`) | o novo nasce mobile-first |
| 2 | entrada multimodal (voz, foto, arquivo) | **sem backend** — só UX preparada |
| 3 | "estudar outro assunto" em linguagem natural | `target_content_codes` existe, mas **não há interpretação de texto livre** |
| 4 | `review_action_available = False` | o bloco REVIEW ainda não tem experiência executável |
| 5 | `diagnose_action_available = False` | diagnóstico fora do planejador |
| 6 | 1 teste de frontend falhando | **pré-existente**, em `teacher.js`, fora deste escopo |
