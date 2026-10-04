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

## 6. A Home, oito estados

Um estado por contexto, escolhido por precedência determinística, cada um com
**uma pergunta e uma ação principal**:

```
A  primeira entrada     "Ainda não nos conhecemos."      -> Vamos lá
D  sessão interrompida  "Você parou em X."               -> Continuar
B  tarefa pendente      "Atividade de Química, amanhã."  -> Começar
E  prova próxima        "Prova sexta."                   -> Revisar
F  dificuldade          "X está custando mais."          -> Vamos lá
H  tudo em dia          "Nenhuma tarefa pendente 🎉"      -> Avançar | Revisar
C  padrão com escola    "Vamos estudar?"                 -> 15/30/45/1h+
G  padrão sem escola    "O que vamos estudar hoje?"      -> 15/30/45/1h+
```

**A precedência importa e está testada:** retomar vem antes de tarefa, tarefa
antes de prova. Terminar o que se começou vem antes de começar outra coisa.

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

| arquivo | linhas |
|---|---:|
| `web/aluno.html` | 125 |
| `web/aluno.js` | 546 |
| `web/aluno.css` | 385 |
| `tests/test_aluno_frontend.js` | 41 testes |

Servido pelo mount existente: **`/student/aluno.html`**. Nenhuma rota nova,
nenhum arquivo de backend tocado.

A folha de perfil traz um **seletor de estados A–H** para você percorrer os
oito contextos sem precisar forjar dados.

---

## 10. O que ainda é mock

| | situação |
|---|---|
| tempo disponível | **real** — `available_minutes` |
| plano por blocos | **real** — planejador |
| pré-requisito bloqueando | **real** — `BLOCKED_BY_PREREQUISITE` |
| tarefa com prazo | **real** — `due_at` |
| **conteúdos exigidos pela tarefa** | **mock — é o elo que falta** |
| prova próxima (estado E) | mock — não há entidade de prova |
| texto livre → assunto | mock — não há interpretação |
| foto, arquivo, voz | **desabilitados**, sem ação |

**O único elo faltante para o requisito 6 funcionar de verdade:**

```
atividade ──[?]──> conteúdos exigidos ──> estado do aluno ──> sessão
  existe        NÃO EXPOSTO             /learning-path    target_content_codes
                                           existe             existe
```

`QBStudentActivity` traz `assignment_id`, `title`, `question_count`,
`due_at`, `status`, `availability`, `target_type` — e **nenhum conteúdo**. As
questões têm `content_code` pela classificação, então o servidor consegue
agregar. É **adição de leitura**, não mudança de arquitetura — mas é contrato
de backend, e por isso não a fiz.

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

**Dois registros honestos sobre estes testes:**

1. **44 deles são estáticos** (regex sobre o fonte), no mesmo padrão dos outros
   11 arquivos de frontend do projeto. Isso os torna baratos e frágeis ao mesmo
   tempo: eles acoplam o teste ao markup e **não conseguem ver aritmética**.
   Foram incapazes de detectar o estouro de orçamento descrito em §7.1.
2. **4 são de execução real** — recortam `montarSessao` do fonte, injetam as
   dependências e varrem 3 rotas × 9 orçamentos. Foram escritos *depois* de o
   bug aparecer, e encontraram sozinhos um segundo caso que eu não tinha
   previsto. **Esta é a direção certa para o resto da suíte**, e está anotada
   como dívida em §13.

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
2. **Estados E e F** dependem de dados que não existem ou são parciais.
3. **Entrada multimodal** é UX preparada, sem backend.
4. **A suíte de frontend do projeto é quase toda estática.** Mantive o padrão
   para não introduzir uma segunda forma de testar, mas **§7.1 mostrou o custo
   disso**: um bug de orçamento sobreviveu a 41 asserções porque nenhuma linha
   estava errada — a soma estava. Os 4 testes de execução real que adicionei
   são o caminho; estendê-los exigiria uma camada de DOM (jsdom ou similar),
   que é uma decisão de projeto, não minha.
5. **1 teste de frontend falhando**, pré-existente, no portal do professor.

---

## 14. Decisões que precisam de você

1. **Promover `/student/aluno.html` a Home padrão do aluno.** O protótipo é
   reversível: são três arquivos novos.
2. **`content_codes` em `QBStudentActivity`** — o elo do §10. Sem ele, o
   requisito 6 funciona só com mock.
3. **`readiness_route` persistido na sessão** — barato agora, caro depois.
4. **Estado E** exige uma entidade de avaliação com data. Vale criar?
5. **Meu Domínio** saiu do MVP do aluno. Se tiver valor para ele, e não só
   para o professor, isso precisa ser dito.

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
