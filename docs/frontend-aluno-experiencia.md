# Frontend do perfil Aluno — mapa da experiência

Desenho feito depois da auditoria (`frontend-aluno-auditoria.md`) e antes do
protótipo.

**Princípio único:**

> O aluno não precisa aprender a navegar. O Núcleo entende onde ele está e
> conduz ao próximo passo.

Consequência prática, aplicada em cada decisão abaixo: **toda tela responde
uma pergunta e oferece uma ação principal.**

---

## 1. Navegação: de 13 para 2

| | |
|---|---|
| **Início** | "o que eu faço agora?" |
| **Meu progresso** | "como eu estou indo?" |

Mais o **avatar** no topo, que abre perfil e troca de ambiente.

Tudo o mais deixa de ser destino e passa a aparecer **no momento em que
importa**:

| deixou de ser menu | onde aparece agora |
|---|---|
| Momento de Aprendizado | **é** a ação principal da Home |
| Atividades | a tarefa vem à Home; a lista abre em folha quando há mais de uma |
| Praticar | é um bloco dentro da sessão |
| Minha Trilha / Trilha de Estudos | viram a ordem dos blocos da sessão |
| Materiais / Vídeos | chegam dentro do bloco de estudo |
| Diagnóstico Inicial | é o estado A da Home, uma vez |
| Meu Domínio | sai do MVP do aluno |
| Minha Evolução | vira **Meu progresso**, enxuto |
| Meu Perfil / Trocar de ambiente | folha no avatar |

**Por que não 4.** "Estudar" não precisa ser destino: estudar é o que a Home
propõe. "Atividades" não precisa ser destino: o sistema sabe que a tarefa
existe e a traz. Manter os dois seria admitir que o aluno precisa ir
procurar — exatamente o que queremos eliminar.

---

## 2. A Home reage ao contexto

A Home **não é a mesma tela sempre**. Um estado é escolhido por regra
determinística, e cada estado tem **uma pergunta e uma ação principal**.

### Ordem de precedência

Quando mais de um estado se aplica, vale o primeiro:

```
A  sem histórico            -> não há o que retomar nem propor
D  sessão interrompida      -> terminar o que começou vem antes de começar outra
B  tarefa com prazo <= 48h  -> compromisso com a escola
E  prova <= 7 dias          -> compromisso com data
F  dificuldade detectada    -> o sistema viu algo que o aluno não viu
H  tudo em dia              -> celebrar e oferecer escolha
C  padrão com escola        -> perguntar o tempo
G  padrão sem escola        -> idem, sem linguagem de tarefa
```

### Os oito estados

**A — primeira entrada.** Sem histórico.

```
Olá, Pedro 👋
Ainda não nos conhecemos.

Em 10 minutos eu descubro por onde você deve começar.
[ Vamos lá ]                       Prefiro escolher eu mesmo
```

Nenhuma métrica. **Nenhum zero na tela** — hoje o aluno novo vê
`Média Geral 0.0%`, que comunica fracasso antes de qualquer coisa.

**B — tarefa pendente.**

```
Olá, Pedro 👋

Você tem uma atividade de Química para amanhã.
Soluções · 12 questões
[ Começar atividade ]

Prefere estudar outra coisa?
```

**C — sem tarefa.**

```
Olá, Pedro 👋
Vamos estudar?

Quanto tempo você tem hoje?
[ 15 min ] [ 30 min ] [ 45 min ] [ 1h+ ]       Sem tempo definido
```

**D — sessão interrompida.**

```
Você parou em Equilíbrio químico ontem.
Faltavam 12 minutos.
[ Continuar ]                      Começar outra coisa
```

**E — prova próxima.**

```
Sua prova de Matemática é sexta-feira.
[ Revisar para a prova ]           Estudar outra coisa
```

**F — dificuldade detectada.**

```
Soluções está custando mais que o resto.
Que tal 20 minutos nisso hoje?
[ Vamos lá ]                       Prefiro outro assunto
```

**G — aluno independente.** Igual a C, sem vocabulário de escola. O campo
livre ganha mais peso, porque não há tarefa para ancorar.

**H — tudo em dia.**

```
Tudo em dia 🎉
Nenhuma tarefa pendente.

Quer avançar ou revisar?
[ Avançar ] [ Revisar ]
```

### O que está presente em todos os estados

Um único campo, discreto, no rodapé:

```
O que você quer estudar?
[ Digite sua dúvida...            ]  📷  📎  🎤
```

**[R]** Os três ícones são **UX preparada, não função**. Devem nascer
desabilitados com rótulo honesto ("em breve"), nunca abrir algo que não
funciona.

---

## 3. O tempo participa do planejamento

O tempo **não é cosmético**: o backend já o recebe.

```
POST /api/v1/student/study-session
     available_minutes: 5..600 · no_timer · target_content_codes
```

E o planejador já reparte o tempo conforme o estado do aluno no conteúdo —
inclusive `BLOCKED_BY_PREREQUISITE`, com 80% em estudo e 20% em prática.

Por isso a resposta ao cenário do pré-requisito **não precisa ser inventada
na interface**:

```
Para entender estequiometria, primeiro precisamos de duas bases.
Com 30 minutos, vamos trabalhar uma delas: Proporções.

  1. Entender a ideia        6 min
  2. Praticar com você       12 min
  3. Fechar                  5 min
```

A interface só precisa **mostrar o plano que o motor já devolveu** — e
mostrar um bloco de cada vez.

---

## 3.1 A tarefa da escola é o OBJETIVO, não necessariamente o primeiro passo

*(Requisito atualizado pelo usuário durante o bloco; substitui a leitura
anterior de que a tarefa é sempre a primeira ação.)*

Quando a escola propõe uma atividade, ela **manda no objetivo da sessão** —
mas o Núcleo não presume que o aluno já tem o que precisa para resolvê-la.

### Os três caminhos

```
            tarefa proposta pela escola
                        |
         o que sabemos do aluno nesses conteúdos?
                        |
   ┌────────────────────┼────────────────────┐
   │                    │                    │
PRONTIDÃO           PRONTIDÃO             LACUNA
CONHECIDA           INCERTA               CONHECIDA
   │                    │                    │
começar a         diagnóstico curto     preparação dirigida
atividade              │                      │
                  atividade ou            atividade
                  preparação
```

**[F] O planejador já distingue os três casos.** Não é um conceito novo
para o motor — é vocabulário que ele já fala:

| caminho | estado do planejador | mistura de tempo que ele já aplica |
|---|---|---|
| prontidão conhecida | `READY` · `MASTERED` · `RECOMMENDED` | pouca ou nenhuma explicação, direto à prática |
| prontidão incerta | `INSUFFICIENT_EVIDENCE` | 60% estudo · 40% prática |
| lacuna conhecida | `BLOCKED_BY_PREREQUISITE` | **80% estudo** · 20% prática |

A correspondência é exata. **A regra pedida já existe no motor; falta
apenas ligá-la à atividade.**

### O elo que falta — e é só um

```
atividade  ──[?]──>  conteúdos exigidos  ──>  estado do aluno  ──>  sessão
   existe          NÃO EXPOSTO              /learning-path      target_content_codes
                                               existe              existe
```

**[F] `QBStudentActivity` não carrega conteúdo nenhum:** `assignment_id`,
`title`, `question_count`, `due_at`, `status`, `availability`,
`target_type`. As questões da atividade **têm** `content_code` pela
classificação, então o servidor consegue agregar — **mas não expõe**.

**[R] É uma adição de leitura, não uma mudança de arquitetura:** um campo
`content_codes: list[str]` em `QBStudentActivity`. Como mexe em contrato de
backend, **não a implemento neste bloco** (§22). Fica mockada no protótipo e
registrada como a dependência número 1.

### Como isso aparece para o aluno — sem complicar a Home

**A Home não muda.** O estado B continua sendo uma frase e um botão. A
decisão acontece **depois do toque**, dentro da sessão:

```
prontidão conhecida
  [Começar] -> tempo -> atividade

prontidão incerta
  [Começar] -> tempo ->
      "Antes de começar, três perguntas rápidas
       para eu saber por onde te ajudar."
   -> diagnóstico curto -> atividade ou preparação

lacuna conhecida
  [Começar] -> tempo ->
      "Antes dessa atividade, vamos revisar uma ideia
       que vai ajudar você a resolvê-la."
   -> preparação -> atividade
```

**[R]** A frase nunca diz *"você não sabe"*. Diz **"isto vai te ajudar"**. A
diferença entre as duas é a diferença entre um aluno que continua e um que
fecha o aplicativo.

### O objetivo fica visível o tempo todo

Enquanto houver preparação, a sessão carrega a tarefa no topo — para que o
aluno nunca sinta que foi desviado do que a escola pediu:

```
┌───────────────────────────────────────┐
│ 🎯 Para: Atividade de Estequiometria  │
│ ●━━━━━━━━○────────○                   │
├───────────────────────────────────────┤
│ PREPARAÇÃO · 10 min                   │
│ Balanceamento de equações             │
│ Isso vai te ajudar a resolver a tarefa.│
│                                       │
│ [ Começar ]                           │
│ depois: a atividade · 18 min          │
└───────────────────────────────────────┘
```

### Tempo insuficiente para tudo

Com 30 minutos e uma preparação de 10 + atividade de 25, não dá para
terminar. O comportamento é **fazer a parte possível e preservar o
progresso**:

```
Hoje deu para a preparação e 6 das 12 questões.
Você continua de onde parou.
[ Continuar amanhã ]
```

Na próxima entrada isso vira o estado **D** da Home, agora com o objetivo
preservado: *"Você estava preparando para a atividade de Estequiometria."*

### O dado que Professor e Coordenação vão precisar

Não construo tela de professor neste bloco (§26), mas o **dado precisa
nascer certo**, senão a informação se perde:

| campo na sessão | para quê |
|---|---|
| `objective_assignment_id` | a qual tarefa a sessão serve |
| `readiness_route` | `DIRECT` · `DIAGNOSED` · `PREPARED` · `DEEP_GAP` |
| `prerequisite_content_codes` | o que precisou ser preparado antes |
| `completed_objective` | se a tarefa chegou a ser concluída |

Com esses quatro, o professor consegue responder depois: **quem começou
direto, quem precisou de diagnóstico, quem precisou de preparação, e quem
tem lacuna profunda** — sem nenhuma tela nova agora.

**[R]** Registrar `readiness_route` é barato hoje e caro depois: sem ele,
a informação de *como* o aluno chegou à tarefa não existe em lugar nenhum.


---

## 4. A sessão

Sequencial. Um bloco por vez. **Nunca a lista inteira de uma vez.**

```
┌─────────────────────────────────┐
│ ●━━━━━━━━━○──────○──────○       │   progresso
│ Soluções · 30 min               │
├─────────────────────────────────┤
│                                 │
│  AGORA · 6 min                  │
│  Entender a ideia               │
│                                 │
│  [ conteúdo do bloco ]          │
│                                 │
│  [ Entendi, continuar ]         │
│                                 │
│  a seguir: praticar · 12 min    │
└─────────────────────────────────┘
```

Regras:

- mostra **agora** e **a seguir**. Nunca os quatro blocos abertos;
- `action_available=False` ⇒ o bloco aparece com a nota do backend e **sem
  botão**. O planejador já se recusa a inventar ação; a interface respeita;
- sair é sempre possível, e o progresso fica salvo (estado D na volta).

---

## 5. Meu progresso

Três frases e um caminho. **Nenhum gráfico no MVP.**

```
Você estudou 3 dias esta semana
Soluções está ficando mais forte
Você avançou em 4 habilidades

Próximo passo →

                          ▾ ver panorama completo
```

O "panorama" — as três faixas 🔴 🟡 🟢 do plano de ação — fica **recolhido**.
Existe para quem quiser, não na cara de quem chega.

**[R]** O que foi cortado por não ajudar a agir: *Média Geral* e *Questões
Respondidas*. Saber que respondeu 240 questões não diz o que fazer a seguir.

---

## 6. Os seis fluxos

**Fluxo 1 — tarefa pendente, com roteamento por prontidão** (§3.1).
`Home (B)` → `[Começar]` → `[30 min]` → o motor decide:

| | |
|---|---|
| 1a — pronto | → atividade direto |
| 1b — incerto | → diagnóstico curto → atividade ou preparação |
| 1c — lacuna | → preparação dirigida → atividade |

Em todos, o objetivo visível continua sendo a tarefa. **2 toques até
começar, nos três.** O roteamento não custa toque ao aluno.

**Fluxo 2 — outro assunto.**
`Home` → campo livre ou "estudar outra coisa" → escolhe assunto → `[30 min]`
→ sessão. **3 toques.**

**Fluxo 3 — sem base.**
`Home` → assunto → tempo → o motor devolve `BLOCKED_BY_PREREQUISITE` → a
sessão abre explicando o pré-requisito e trabalhando a base possível no
tempo disponível.

**Fluxo 4 — retomar.**
`Home (D)` → `[Continuar]` → sessão no bloco onde parou. **1 toque.**

**Fluxo 5 — progresso.**
`Meu progresso` → três frases → `Próximo passo →` volta para a sessão.

**Fluxo 6 — sem escola.**
`Home (G)` → tempo ou campo livre → sessão. Nenhuma menção a tarefa,
professor ou prazo.

---

## 7. Telas do MVP

**Três telas e duas folhas.** Não mais.

| | tela | pergunta |
|---|---|---|
| 1 | **Home** | o que eu faço agora? |
| 2 | **Sessão** | o que eu faço neste minuto? |
| 3 | **Progresso** | como eu estou indo? |

| | folha | quando |
|---|---|---|
| a | lista de tarefas | só quando há mais de uma |
| b | perfil e ambiente | ao tocar o avatar |

**[R]** Uma entidade de banco não vira uma página. `domain-map`,
`learning-path`, `study-path`, `materials` e `videos` são entidades reais do
backend e **nenhuma** vira tela do aluno — elas alimentam a sessão.

---

## 8. Mobile first

O CSS atual é `max-width` (desktop-first). O novo nasce ao contrário:
estilos base valem para o telefone e `min-width` adiciona o resto.

| | |
|---|---|
| base | 360–767 px, coluna única, ação principal ao alcance do polegar |
| `>= 768` | respiro lateral, mesma hierarquia |
| `>= 1024` | largura máxima de leitura; **não** vira dashboard |

Alvos de toque de 44 px, nada essencial atrás de *hover*.

---

## 9. Acessibilidade, desde o começo

Contraste AA no texto e nos botões · foco visível e ordem de tabulação
previsível · marcos (`header`/`main`/`nav`) e um `h1` por tela ·
`aria-live` para o que muda sozinho · estado desabilitado com motivo legível
· erro em texto, nunca só em cor · `prefers-reduced-motion` respeitado.

---

## 10. O que é mock e o que é real

| | real hoje | mock no protótipo |
|---|---|---|
| tempo disponível | **sim** — `available_minutes` | — |
| plano da sessão por blocos | **sim** — planejador | — |
| pré-requisito bloqueando | **sim** — `BLOCKED_BY_PREREQUISITE` | — |
| tarefa da escola com prazo | **sim** — `due_at` | — |
| **conteúdos exigidos pela tarefa** | **não exposto** | **mock** — ver §3.1 |
| roteamento por prontidão | estados existem no planejador | **mock** — falta o elo acima |
| retomar sessão | **sim** — `study-session/today` | — |
| prova próxima (estado E) | **não** | **mock** — não há entidade de prova |
| dificuldade detectada (estado F) | parcial | derivável de `learning-path` |
| texto livre → assunto | **não** | **mock** — não há interpretação |
| foto, arquivo, voz | **não** | **UX desabilitada**, sem ação |

**[R]** Nada será apresentado como funcional sem backend. Onde não houver,
o controle nasce desabilitado e dito.

---

## 11. Decisões que ficam para você

1. **Promover a nova Home a padrão.** O protótipo nasce em `/aluno`, com o
   `/` atual intacto. Trocar é decisão sua.
2. **Estado E (prova próxima)** exige uma entidade de avaliação com data que
   não existe. Vale criar?
3. **Texto livre → assunto** exige interpretação de linguagem natural. Fora
   do MVP determinístico; entra quando houver decisão sobre IA aqui.
4. **`content_codes` em `QBStudentActivity`** é o único elo faltante para o
   roteamento por prontidão (§3.1). É leitura agregada, não arquitetura —
   mas é contrato de backend, então aguarda você.
5. **`readiness_route` na sessão** é o que vai permitir, depois, o professor
   saber quem precisou de preparação. Barato agora, caro depois.
6. **Meu Domínio** sai do MVP do aluno. Se ele tiver valor para o aluno —
   e não só para o professor —, isso precisa ser dito.
