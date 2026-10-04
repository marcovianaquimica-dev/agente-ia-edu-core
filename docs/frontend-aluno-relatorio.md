# Frontend do perfil Aluno — relatório do bloco

Bloco autônomo. **O portal atual não foi alterado.** Nenhum arquivo existente
foi modificado: `index.html`, `app.js`, `styles.css`, backend, CÉREBRO,
Question Bank e ingestão ENEM estão intocados.

Documentos irmãos: `frontend-aluno-auditoria.md` (o que existia) e
`frontend-aluno-experiencia.md` (o desenho).

---

## 1. O que existia

Uma SPA de arquivo único, HTML/CSS/JS puros, sem framework.

| | |
|---|---:|
| `index.html` | 598 linhas, **151 `id`s**, **14 painéis de visão** |
| `app.js` | 2.557 linhas |
| `styles.css` | 1.822 linhas, **13 media queries, todas `max-width`** |
| endpoints do aluno já servidos pelo backend | **40** |

**O achado que mudou tudo:** o motor já faz quase tudo que se pediu para a
nova experiência. `POST /student/study-session` **já aceita
`available_minutes`**; o planejador **já monta blocos** STUDY/PRACTICE/
REVIEW/BREAK e **já reparte o tempo conforme o estado do aluno no conteúdo**,
incluindo `BLOCKED_BY_PREREQUISITE` com 80% de estudo.

> O problema nunca foi falta de inteligência. Era a interface expor a
> máquina em vez de conduzir o aluno.

---

## 2. O que estava excessivo

**13 itens de navegação permanente**, dos quais:

- **dois com o mesmo nome na prática:** *"Minha Trilha"* e *"Trilha de
  Estudos"*. Nenhum aluno distingue pelo rótulo;
- **seis expunham subsistema, não intenção:** Diagnóstico Inicial, Minha
  Trilha, Meu Domínio, Trilha de Estudos, Momento de Aprendizado, Materiais.

**A Home era um dashboard: 7 blocos de topo, 4 deles métricas.** E as três
faixas 🔴🟡🟢 do plano de ação abertas ao mesmo tempo — o *"você precisa
estudar 37 assuntos"* que o briefing pedia para evitar.

**O pior detalhe:** a primeira coisa que um aluno novo via era
`Média Geral 0.0%`. O produto comunicava fracasso antes de comunicar
qualquer outra coisa.

---

## 3. O que foi removido

| | por quê |
|---|---|
| **Média Geral** | não ajuda a decidir o que fazer agora |
| **Questões Respondidas** | métrica de volume, zero valor de ação |
| **Meu Domínio** | visão de máquina, não do aluno |
| as três faixas abertas na Home | ansiogênicas; viraram "ver panorama", recolhido |
| 11 dos 13 itens de menu | ver §4 |

---

## 4. O que foi automatizado ou contextualizado

| deixou de ser destino | onde está agora |
|---|---|
| Momento de Aprendizado | **é** a ação principal da Home |
| Atividades | a tarefa vem à Home; a lista abre em folha quando há mais de uma |
| Praticar | bloco dentro da sessão |
| Minha Trilha + Trilha de Estudos | viraram a ordem dos blocos |
| Materiais, Vídeos | chegam dentro do bloco de estudo |
| Diagnóstico Inicial | é o estado A da Home, uma vez |
| Minha Evolução | virou **Meu progresso**, três frases |
| Meu Perfil, Trocar de ambiente | folha no avatar |

---

## 5. Nova arquitetura

**Dois destinos permanentes: Início e Meu progresso.** Mais o avatar.

**Três telas e duas folhas:**

| | tela | pergunta que responde |
|---|---|---|
| 1 | Home | o que eu faço agora? |
| 2 | Sessão | o que eu faço neste minuto? |
| 3 | Progresso | como eu estou indo? |

| folha | quando |
|---|---|
| tarefas | só quando há mais de uma |
| perfil | ao tocar o avatar |

Nenhuma entidade de banco virou tela: `domain-map`, `learning-path`,
`study-path`, `materials` e `videos` existem no backend e **alimentam a
sessão** em vez de virar menu.

---

## 6. A Home, sete estados

Um estado por contexto, escolhido por precedência determinística, cada um com
**uma pergunta e uma ação principal**:

```
A  primeira entrada     "Ainda não nos conhecemos."      -> Vamos lá
D  sessão interrompida  "Você parou em X."               -> Continuar
B  tarefa pendente      "Atividade de Química, amanhã."  -> Começar
E  (retirado do MVP em 2026-10-04 — ver 7.2)
F  dificuldade          "X está custando mais."          -> Vamos lá
H  tudo em dia          "Nenhuma tarefa pendente 🎉"      -> Avançar | Revisar
C  padrão com escola    "Vamos estudar?"                 -> 15/30/45/1h+
G  padrão sem escola    "O que vamos estudar hoje?"      -> 15/30/45/1h+
```

**A precedência importa e está testada:** retomar vem antes de tarefa, tarefa
antes de dificuldade. Terminar o que se começou vem antes de começar outra
coisa; a tarefa da escola vem antes do que o sistema detectou por conta.

**Estado A não mostra nenhum número.** Há teste que falha se alguém colocar
uma métrica zerada lá.

Todos os estados têm, no rodapé, um campo único: *"O que você quer estudar?"*
com `📷 📎 🎤` **desabilitados e com rótulo honesto** — não existe backend
para eles.

---

## 7. A sessão, e o requisito 6 atualizado

A tarefa da escola é o **objetivo**, não necessariamente o primeiro passo.

**[F] Os três caminhos pedidos correspondem 1:1 a estados que o planejador do
backend já usa:**

| caminho | estado do planejador |
|---|---|
| prontidão conhecida → começar | `READY` · `MASTERED` · `RECOMMENDED` |
| prontidão incerta → diagnóstico curto | `INSUFFICIENT_EVIDENCE` |
| lacuna conhecida → preparação dirigida | `BLOCKED_BY_PREREQUISITE` |

**A Home não ganhou nenhuma complexidade.** O estado B continua uma frase e um
botão; o roteamento acontece **depois do toque**. Custo para o aluno: **zero
toques a mais**.

Exemplo real do protótipo — tarefa de Estequiometria, 30 minutos, aluno sem
balanceamento:

```
🎯 Para: Atividade de Estequiometria
●━━━━○──────○──────○         Etapa 1 de 4 · 30 min no total

PREPARAÇÃO · 11 min
Balanceamento de equações
Isso vai te ajudar a resolver a tarefa.
[ Começar ]
a seguir: testar o que acabou de ver · 5 min
```

Quatro decisões de linguagem e desenho:

1. **O objetivo fica no topo o tempo todo.** O aluno nunca sente que foi
   desviado do que a escola pediu.
2. **A frase nunca diz "você não sabe".** Diz *"isso vai te ajudar"*. Há
   teste que falha se aparecer *"não domina"*, *"fraco"* ou equivalente.
3. **O fechamento sai DO orçamento, não por cima.** Pediu 30, a sessão soma
   30. Isto foi um bug real: a primeira versão somava 34.
4. **Com preparação antes, avisa:** *"Se não der para terminar hoje, seu
   progresso fica salvo."*

### 7.1 O orçamento é um teto, não uma sugestão — segundo bug, mesma família

O item 3 acima corrigiu o estouro **médio**, mas não o **curto**. Testando com
15 minutos encontrei a sessão somando **22**. A causa não está em nenhuma linha
errada: está na soma dos **pisos**. Preparação tem piso de 8 min, praticar a
base 5, a atividade 5, o fechamento 4 — `8+5+5+4 = 22`. Nenhum piso é absurdo
sozinho; juntos não cabem em 15.

Havia duas saídas, e elas não são equivalentes:

- **Espremer os blocos abaixo do piso.** Caberia em 15, mas entregaria uma
  preparação de 3 minutos que não prepara. A sessão ficaria *formalmente* certa
  e *pedagogicamente* vazia.
- **Fazer menos e dizer.** Os blocos que não cabem saem **do fim para o
  começo**, porque a preparação é o que torna o resto possível.

Escolhi a segunda. Com 15 minutos e lacuna de pré-requisito, a sessão hoje é
preparação (8) + praticar a base (5) = **13 min**, e o resumo diz:
*"hoje damos conta da preparação; a atividade fica para a próxima"*.

**Isto não contradiz o requisito 6 — é ele levado a sério.** A tarefa da escola
continua sendo o objetivo: o cabeçalho `🎯 Para: Atividade de Estequiometria`
permanece no topo, `objective_assignment_id` continua gravado e
`completed_objective` fica `false`, que é exatamente o dado de que Professor e
Coordenação precisam para distinguir *"não fez"* de *"começou a se preparar
para fazer"*.

**Caso-limite declarado:** se nem o primeiro bloco couber (5 minutos contra um
piso de 8), o bloco é mantido mas **encurtado ao tempo real** e marcado como
parcial — ficar sem nada a fazer é pior que começar sabendo que não termina.
Hoje a interface só oferece 15/30/45/60, então esse caso não é alcançável pelo
aluno; o tratamento existe para que um botão futuro de 10 min não quebre a
promessa em silêncio.

**O que aprendi sobre o meu próprio teste.** Os 41 testes que eu havia escrito
são estáticos — regex sobre o fonte, no mesmo padrão dos outros 11 arquivos de
frontend do projeto. **Nenhum deles podia pegar este bug**, porque cada linha
isolada estava correta; o defeito só aparece na aritmética entre elas. O teste
que vale aqui **executa** `montarSessao` com as dependências injetadas e varre
3 rotas × 9 orçamentos, afirmando que a soma nunca passa do pedido. Foi ele que
encontrou o caso-limite dos 5 minutos — que eu não tinha previsto quando
escrevi a correção.

**O dado que Professor e Coordenação vão precisar já nasce na sessão** —
`readiness_route` (`DIRECT`/`DIAGNOSED`/`PREPARED`) e
`objective_assignment_id`. Nenhuma tela de professor foi feita, mas sem esses
campos a informação de *como* o aluno chegou à tarefa não existiria depois.

**Bloco sem experiência executável aparece sem botão de ação**, com a nota —
a mesma regra que o planejador já aplica no backend com
`action_available=False`.

### 7.2 "Prova próxima" saiu do MVP (decisão 4, aprovada)

O estado E da Home — *"sua prova de Matemática está chegando"* — **foi
retirado do escopo funcional**. Ele dependia de uma entidade de avaliação com
data que não existe no schema, e mantê-lo significaria deixar um mock
parecendo produto até alguém decidir criá-la.

O que ficou registrado para quando a decisão vier: a Home precisa saber
*disciplina* + *quando*. Se a escola registrar calendário de provas no
Núcleo, a fonte natural é uma entidade própria; se não registrar, a
alternativa é derivar de atividades marcadas como prova, com `due_at` — mais
barato e menos fiel. **Nenhuma das duas foi implementada.**

As outras letras **não foram renumeradas**. A lacuna entre D e F é
deliberada: as letras são identificador de rastreio em três documentos já
revisados, e renumerar criaria churn de leitura sem nenhum ganho.

### 7.3 O gatilho real do diagnóstico (decisão 6, investigada)

Na auditoria visual ficou claro que a rota `INSUFFICIENT_EVIDENCE` existia,
estava testada, renderizava — e **nenhum caminho do protótipo chegava nela**.
A pergunta certa não era "como fazer aparecer", era "qual é o gatilho real".

**Resposta medida, em `adaptive_learning_path.py`:** o estado é produzido por
`elif not observed:`, e `observed` é `evidence_state == STATE_OBSERVED`. Ou
seja — **o aluno nunca respondeu nenhuma questão daquele conteúdo**. Isso não
tem nada de artificial: é a situação normal de todo conteúdo novo que a
escola passa a cobrar.

Então a cadeia do diagnóstico é esta, e cada elo foi verificado:

| elo | estado |
|---|---|
| atividade → conteúdos exigidos | **fechado neste bloco** (§7.4) |
| conteúdo → evidência do aluno | já existe — `/learning-path` |
| evidência ausente → `INSUFFICIENT_EVIDENCE` | já existe — regra acima |
| `INSUFFICIENT_EVIDENCE` → rota `DIAGNOSTIC` | já existe — protótipo |
| rota `DIAGNOSTIC` → **diagnóstico curto de 3 perguntas** | **NÃO EXISTE** |

**O elo que falta é o último, e é só ele.** O único diagnóstico no sistema é
`InitialDiagnostic`: uma sessão adaptativa única, de onboarding, que estima o
mapa de domínio inteiro do aluno. Não é um check de três perguntas sobre um
conteúdo. O próprio planejador declara isso — `ACTION_DIAGNOSE` está fora de
`practice_available`, com o comentário *"diagnostic is a separate flow"*.

**Não inventei condição para a rota aparecer.** Com §7.4 no lugar, ela passa a
ser alcançável sozinha, pelo motivo certo: basta a escola distribuir uma
atividade sobre conteúdo que o aluno ainda não praticou. A UX fica preparada;
o que falta é a experiência de diagnóstico curto, que é decisão de produto,
não de implementação.

### 7.4 A cadeia fechada: atividade → conteúdo → domínio → prontidão → sessão

Era o elo apontado como faltante em §10. **Fechado neste bloco, só com
leitura** — nenhuma tabela nova, nenhuma coluna nova, nada tocado no
Question Bank:

```
ActivityAssignment.assessment_version_id
  → assessment_items          quais questões a lista tem
  → content_question_links    a classificação de cada questão
  → catalog_nodes.code        o código de conteúdo
```

O achado que tornou isso barato: **`catalog_nodes.code` é o mesmo vocabulário
que `/learning-path` e `domain_content_mastery` já usam.** A informação
existia inteira; estava espalhada por tabelas que não se falavam. Não houve
tradução no meio, nem campo novo para manter em sincronia.

`QBStudentActivity` ganhou `content_codes: list[str]`. Uma consulta em lote
para todas as atividades do aluno, não uma por atividade — essa agregação roda
na tela inicial dele, a requisição mais quente do perfil, e há teste que falha
se voltar a ser N+1.

**Lista vazia é um fato, não um erro:** significa *"as questões desta
atividade ainda não foram classificadas"*, que é o estado da maior parte do
acervo hoje. O cliente trata como evidência ausente — o que, não por acaso,
leva exatamente à rota de diagnóstico de §7.3.

### 7.5 A rota pedagógica agora é persistível (decisão 3, aprovada)

Migration **064**, aditiva: três colunas em `study_sessions`.

| coluna | para quê |
|---|---|
| `readiness_route` | `DIRECT` · `DIAGNOSTIC` · `PREREQUISITE_PREPARATION` |
| `objective_assignment_id` | qual tarefa da escola era o objetivo |
| `objective_completed` | se chegou ao fim dela |

**Por que coluna e não um campo no JSON que já existe.** A pergunta que
Professor e Coordenação vão fazer é de agregação sobre a turma: *"quantos
alunos ainda não fizeram a tarefa de Estequiometria, e destes, quantos estão
se preparando para ela?"*. Em JSON isso é varredura; em coluna indexada é
consulta comum. Há teste que roda essa consulta exata.

`readiness_route` tem **CheckConstraint** com os três valores em vez de
`String` livre: o conjunto é fechado por definição, e um quarto valor deve
entrar junto com a decisão de produto que o criou, não por um typo. `NULL`
continua válido, para sessões anteriores à migration.

**Sem ForeignKey em `objective_assignment_id`, de propósito.** A sessão é
registro histórico: apagar a atividade não pode apagar nem travar o fato de
que o aluno estudou para ela.

**Isto é irreversível no que importa.** Em DDL não é — `downgrade` existe e é
testado. Mas a rota só pode ser gravada no instante em que a sessão é montada,
porque é o resultado de uma decisão tomada sobre o estado de domínio *daquele
momento*. Não se reconstrói depois. Cada mês sem as colunas é um mês de dado
que não existe.

### 7.6 Microcopy revisada (decisões adicionais de UX)

Layout, cores e estrutura intocados. Só texto:

| onde | antes | agora |
|---|---|---|
| entrada universal | "O que você quer estudar?" | "O que você precisa agora?" |
| placeholder | "Digite sua dúvida..." | "Uma dúvida, um assunto, uma questão..." |
| bloco de preparação | "PREPARAÇÃO" | "ANTES DA TAREFA" |
| bloco de prática | "PRATICAR A BASE" | "PARA FIRMAR" |
| bloco final | "FECHAMENTO" | "PARA FECHAR" |

Os conceitos internos **não mudaram**: os `tipo` dos blocos continuam
`DIAGNOSE` / `STUDY` / `PRACTICE` / `OBJECTIVE` / `REVIEW`, que é o
vocabulário compartilhado com o planejador. O que mudou é só a `etiqueta` — o
único desses campos que o aluno vê. Ele não precisa conhecer a arquitetura
pedagógica para usá-la.

A navegação inferior continua com **dois itens**. Nada foi acrescentado.

---

## 8. Mobile

Mobile first de verdade: **o CSS base é telefone e só `min-width` acrescenta**.

| | portal atual | protótipo |
|---|---:|---:|
| media queries | 13, todas `max-width` | **2, ambas `min-width`** |

Barra de abas fixa ao alcance do polegar no telefone; no tablet/desktop ela
sobe e vira pílulas. Alvos de 44 px, `safe-area-inset` considerado, e no
desktop a coluna **não se divide** — largura máxima de leitura de 760 px, não
um dashboard.

---

## 9. O que foi implementado

| arquivo | tamanho |
|---|---:|
| `web/aluno.html` | 131 linhas |
| `web/aluno.js` | 560 linhas |
| `web/aluno.css` | 395 linhas |
| `tests/test_aluno_frontend.js` | 48 testes (estáticos) |
| `tests/test_aluno_comportamento.js` | 9 testes (comportamentais) |

Servido pelo mount existente: **`/student/aluno.html`**. Nenhuma rota nova.

**No bloco de consolidação de 2026-10-04 o backend passou a ser tocado**, pela
primeira vez neste trabalho, nos três pontos que as decisões 2 e 3 aprovaram:

| arquivo | o quê |
|---|---|
| `services/activity_assignment_store.py` | agregação dos `content_codes` (leitura) |
| `api/schemas/question_bank.py` | campo `content_codes` em `QBStudentActivity` |
| `db/models/study_session.py` | três colunas novas |
| `migrations/versions/064_study_session_readiness.py` | a migration aditiva |
| `tests/test_aluno_cadeia_conteudos.py` | 12 testes |
| `tests/test_aluno_migration_064.py` | 8 testes, contra PostgreSQL real |

A folha de perfil traz um **seletor de estados** para percorrer os contextos
sem forjar dados. São sete: A, B, C, D, F, G, H — ver §7.2 sobre a lacuna.

---

## 10. O que ainda é mock

| | situação |
|---|---|
| tempo disponível | **real** — `available_minutes` |
| plano por blocos | **real** — planejador |
| pré-requisito bloqueando | **real** — `BLOCKED_BY_PREREQUISITE` |
| tarefa com prazo | **real** — `due_at` |
| **conteúdos exigidos pela tarefa** | **IMPLEMENTADO neste bloco** — §7.4 |
| rota pedagógica persistível | **IMPLEMENTADO neste bloco** — migration 064 |
| prova próxima (estado E) | **retirado do MVP** — §7.2 |
| texto livre → assunto | mock — não há interpretação |
| foto, arquivo, voz | **desabilitados**, sem ação |

**O elo faltante foi fechado** (§7.4). A cadeia hoje:

```
atividade ──> conteúdos exigidos ──> estado do aluno ──> sessão
  existe      content_codes          /learning-path    target_content_codes
              IMPLEMENTADO              existe             existe
```

**O que ainda falta, e é outro elo:** a experiência de *diagnóstico curto*
para a qual a rota `DIAGNOSTIC` aponta. O gatilho é real e já computado; a
tela de três perguntas não existe. Detalhe em §7.3.

**E falta o frontend consumir `content_codes` de verdade** — o protótipo
continua lendo `MOCK.tarefas[].conteudos`. O campo existe no contrato agora,
mas trocar o mock por `fetch()` é o passo seguinte, não este.

---

## 11. Testes

| | antes do bloco | depois |
|---|---:|---:|
| testes de frontend | 283 | **331** |
| passando | 282 | **330** |
| falhando | 1 | **1** |

**A falha é a mesma de antes do bloco** —
`test_phase29_authorial_question_review_frontend.js`, sobre `teacher.js`,
**pré-existente e fora deste escopo**. Nenhum teste existente quebrou.

Os 48 novos cobrem: isolamento do portal · dois destinos · três telas · os
oito estados · precedência · tempo · **os três caminhos de prontidão** ·
objetivo preservado · linguagem que não culpa o aluno · orçamento da sessão ·
bloco sem ação falsa · retomada · progresso sem gráfico · controles
desabilitados · mocks identificados · marcos e `aria-live` · foco · 44 px ·
`prefers-reduced-motion` · mobile first · **contraste AA medido**.

**Depois do bloco de consolidação (2026-10-04):**

| | frontend (JS) | backend (Python) |
|---|---:|---:|
| antes do bloco | 331 | — |
| agora | **340** | **+21 novos** |

Os 9 testes novos de frontend estão em `test_aluno_comportamento.js`, e
nenhum deles é asserção sobre texto:

- **Cascata de CSS calculada.** O defeito do banner vazio era de
  *especificidade* — o JS estava certo. O teste monta as regras de
  `aluno.css`, calcula origem + especificidade + ordem para **todo elemento
  da página** e pergunta qual `display` vence com `hidden` posto. Há um teste
  que reconstrói o CSS antigo e **exige que o avaliador reprove**: teste que
  nunca falha não prova nada.
- **Contrato entre JS e banco.** Lê as três rotas de `aluno.js` e as do
  `CheckConstraint` da migration 064, e compara os conjuntos. São dois
  arquivos em duas linguagens que precisam concordar, e nada além deste teste
  os liga.
- **Orçamento de pixel do placeholder** — ver §7.6.

No backend, `test_aluno_migration_064.py` **constrói um PostgreSQL
descartável pela cadeia real de migrations**, testa, e derruba. Não depende
do banco de desenvolvimento estar em head e não escreve nele.

**O limite que permanece:** a avaliação de cascata é simulação, não render.
Um DOM de verdade (jsdom) mediria melhor, mas o projeto não tem nenhuma
dependência Node — introduzir uma seria mudança de infraestrutura, não
correção de defeito. Fica como dívida 6.

---

## 12. Antes × depois

| | antes | depois |
|---|---:|---:|
| **itens de navegação** | **13** | **2** |
| blocos de topo na Home | 7 | **2** |
| métricas na Home | 4 | **0** |
| telas / painéis | 14 | **3** (+2 folhas) |
| `id`s no documento | 151 | **28** |
| media queries | 13 `max-width` | 2 `min-width` |
| **toques para começar a tarefa** | — | **2** |
| **toques para estudar outra coisa** | — | **2** |
| tabulações até a ação principal | — | **3** |

*(o portal atual não tem um caminho equivalente de "começar em 2 toques" para
comparar: a tarefa vive em `Menu > Atividades > Pendentes`)*

---

## 13. Dívidas

1. **Contraste do portal atual.** Medi: `--text-muted` `#64748b` sobre o fundo
   da aplicação dá **4,40** — reprova em AA para texto normal. O protótipo usa
   `#5b6b82` (5,02). **O portal atual continua com o valor que reprova**, e
   isso não foi corrigido porque exigiria tocar `styles.css`.
2. **O protótipo ainda lê mock, não o contrato novo.** `content_codes` existe
   em `QBStudentActivity` a partir deste bloco, mas `aluno.js` continua usando
   `MOCK.tarefas[].conteudos`. Trocar por `fetch()` é o passo seguinte.
3. **A gravação de `readiness_route` ainda não acontece.** A migration 064
   criou as colunas e o frontend já produz o valor; falta o serviço de sessão
   escrever. Preparado, não ligado — foi o que a decisão 3 pediu.
4. **Estado F** depende de dados parciais.
5. **Entrada multimodal** é UX preparada, sem backend. A microcopy já aceita
   dúvida, conteúdo, questão, revisão ou objetivo — a interpretação não
   existe.
6. **A suíte de frontend do projeto é quase toda estática.** Mantive o padrão
   para não introduzir uma segunda forma de testar, mas **§7.1 mostrou o custo
   disso**: um bug de orçamento sobreviveu a 41 asserções porque nenhuma linha
   estava errada — a soma estava. Os 4 testes de execução real que adicionei
   são o caminho; estendê-los exigiria uma camada de DOM (jsdom ou similar),
   que é uma decisão de projeto, não minha.
7. **1 teste de frontend falhando**, pré-existente, no portal do professor.

---

## 14. Decisões que precisam de você

**Resolvidas na revisão de produto de 2026-10-04:**

| # | decisão | resultado |
|---|---|---|
| 2 | `content_codes` em `QBStudentActivity` | **aprovada e implementada** (§7.4) |
| 3 | `readiness_route` persistido | **aprovada e implementada** (§7.5) |
| 4 | entidade de avaliação com data | **recusada** — estado E saiu do MVP (§7.2) |
| 5 | expor o mapa de domínio ao aluno | **recusada** — ficam as três faixas |

Sobre a 5: o panorama continua em *Precisa de atenção · Em desenvolvimento ·
Consolidado*. Não virou nota, ranking nem boletim, e os dados dele continuam
mock — os estados reais existem em `/learning-path`, falta a agregação.
Mantive a redação atual em vez de trocar para "precisa reforçar": as duas
servem, e o pedido veio como exemplo, não como texto fechado. Se preferir a
outra, é uma linha.

**Ainda pendentes:**

1. **Promover `/student/aluno.html` a Home padrão.** Segue **não autorizado**.
   Antes disso faz sentido: trocar o mock pelo contrato novo (dívida 2),
   ligar a gravação da rota (dívida 3) e testar com aluno real.
2. **O diagnóstico curto de três perguntas não existe** (§7.3). O gatilho é
   real; a experiência é decisão de produto. Enquanto não vier, a rota
   `DIAGNOSTIC` leva a um bloco que anuncia algo que o sistema não entrega —
   hoje isso está contido porque o protótipo não alcança a rota, mas deixará
   de estar no momento em que ele consumir `content_codes` de verdade.
3. **Quem escreve `readiness_route`?** O serviço de sessão é o lugar natural,
   mas isso toca um fluxo que já está em produção. Vale um passo próprio.
4. **A agregação do panorama de progresso** — três faixas a partir de
   `domain_content_mastery`. Barato, e tira a última tela de mock puro.

---

## 15. O critério

> Quanto da complexidade saiu da frente do aluno sem tirar inteligência do
> produto?

**Saiu da frente:** 11 dos 13 destinos, 4 métricas, 5 painéis, 123 `id`s.

**Não saiu do produto:** o planejador, o mapa de domínio, a trilha, os
pré-requisitos e o roteamento por prontidão continuam todos lá — agora
**decidindo** em vez de serem navegados.

O aluno abre, lê uma frase, toca duas vezes e está estudando a coisa certa
para o tempo que tem.
