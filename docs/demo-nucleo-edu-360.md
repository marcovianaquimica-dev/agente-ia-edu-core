# Demonstração do Núcleo Edu 360

Roteiro de 10 a 15 minutos para diretoria, coordenação e mantenedores.

Escrito a partir de um ensaio feito tela a tela, no navegador, contra o banco
de desenvolvimento. Tudo que está aqui foi visto funcionando. O que não foi,
está na seção **O que NÃO mostrar** — e está lá com o motivo.

**Regra que vale acima de qualquer outra deste documento: não inventar dado.**
Nenhum número desta apresentação é de enfeite. Se uma tela estiver vazia, ela
está vazia porque ninguém usou aquilo ainda, e dizer isso em voz alta vale
mais do que preencher.

---

## 1. A frase de abertura

> "O Núcleo Edu 360 não corrige mais rápido. Ele descobre o que trava o aluno,
> ensina aquilo, e só devolve a tarefa quando ele consegue sozinho."

Dita antes de qualquer clique. Ela estabelece o que a plateia deve procurar
nas telas seguintes — e é literalmente o que o sistema faz.

---

## 2. Os três momentos "UAU"

Os três são reais, foram observados no ensaio e estão no roteiro abaixo.

### UAU 1 — O sistema diz POR QUE, e o porquê é curricular

Na tela do Assessor, depois do diagnóstico:

> "Pelas suas respostas, a conservação dos átomos ainda está travando. Reações
> químicas e balanceamento é a base de Estequiometria e cálculos químicos —
> vale firmar isso antes."

**Frase do apresentador:** *"Repare que ele não disse 'você errou 3 de 3'. Ele
disse qual habilidade travou e por que aquele outro conteúdo precisa vir
antes. Essa relação de pré-requisito está no Núcleo, não no modelo de IA."*

### UAU 2 — A ajuda vem em etapas, e ajuda NÃO vira domínio

Na prática guiada: o aluno erra, pede dica, recebe uma de cada vez (conceito →
onde olhar → operação → assistida), acerta — e o sistema **não** diz que ele
aprendeu. Diz:

> "Chegamos lá. Com a ajuda, a ideia apareceu — agora vale tentar um parecido
> por conta própria, para ver se ela ficou firme."

**Frase do apresentador:** *"Conseguir com ajuda não é dominar sozinho. O que
acabou de acontecer aqui não entra no mapa de domínio do aluno — entra como
'precisou de 3 ajudas'. O domínio só muda quando ele resolve sem ninguém."*

Esse é o ponto que separa o produto de um app de exercícios.

### UAU 3 — A devolutiva de redação fala com o aluno, e admite o que não sabe

Na tela de devolutiva: nota por competência, e uma tabela **"Você já faz bem /
Onde pode avançar"** com texto específico sobre o texto daquele aluno. E, no
topo, um selo honesto: **"Leitura da foto incerta"**.

**Frase do apresentador:** *"Duas coisas. A primeira: isso não é uma nota, é
uma devolutiva — ela cita o que o aluno escreveu. A segunda: quando a leitura
da foto fica duvidosa, o sistema avisa em vez de fingir confiança. É isso que
permite colocar um professor no circuito."*

---

## 3. O roteiro

Tempo total: **14 minutos**. A ordem é Portal → Redação → **volta ao Portal** →
Assessor → fechamento. A volta ao Portal no meio não é enfeite: é o momento em
que a plateia entende que são módulos de um mesmo sistema, e não duas
aplicações.

### 00:00 — Portal (1 min 30)

| Campo | Conteúdo |
|---|---|
| **TELA** | `http://localhost:8117/portal` |
| **AÇÃO** | Abrir e deixar parado. Não clicar em nada por 20 segundos. |
| **MENSAGEM** | O Núcleo Edu 360 é uma plataforma com módulos; dois estão de pé, quatro estão no mapa. |
| **FRASE** | *"Esta é a porta de entrada da escola no Núcleo Edu 360. Ele resolve duas coisas hoje: a correção de redação, e o aluno que trava num conteúdo e não sabe por quê. O que está com 'Acessar' existe. O que está 'Em breve' está no roteiro — e eu prefiro mostrar assim do que fingir que já existe."* |
| **RISCO** | A tela diz *onde* você está, mas não *o que* ela faz: a chamada é "O que vamos acompanhar hoje?". Em 10 segundos de silêncio, um diretor não descobre sozinho a proposta de valor. |
| **PLANO B** | A frase acima resolve em voz alta. Em tela cheia a 1920×1080 a página ocupa 55% da largura — **zoom do navegador em 125%** antes de começar (seção 5). |

### 01:30 — Redação (3 min 30)

| Campo | Conteúdo |
|---|---|
| **TELA** | Card **Redação** → Acessar → **"Ver devolutiva"** no tema *Desafios para o combate ao racismo na sociedade brasileira* |
| **AÇÃO** | Mostrar a lista de temas, abrir a devolutiva, mostrar a nota por competência, descer até a tabela "Você já faz bem / Onde pode avançar" e ler **uma linha inteira** em voz alta. |
| **MENSAGEM** | Não é nota: é devolutiva por competência, citando o que o aluno escreveu. E o sistema avisa quando a leitura da foto ficou duvidosa. |
| **FRASE** | *(UAU 3)* *"Duas coisas aqui. A primeira: isso não é uma nota, é uma devolutiva — ela cita o que o aluno escreveu. A segunda: quando a leitura da foto fica incerta, o sistema avisa em vez de fingir confiança. É isso que permite colocar um professor no circuito."* |
| **RISCO ALTO** | O aluno da Redação **não é o mesmo** do Assessor (identidade fixa de desenvolvimento). Nada na tela denuncia isso, mas dizer "o mesmo aluno" não se sustenta se alguém perguntar. |
| **PLANO B** | Dizer: *"aqui estou em outro aluno, de outra turma, porque é dele a redação corrigida que eu quero mostrar."* Honesto e suficiente. |

### 05:00 — Volta ao Portal (30 s)

| Campo | Conteúdo |
|---|---|
| **TELA** | Link **"← Núcleo Edu 360"** no canto superior direito da Redação |
| **AÇÃO** | Clicar e deixar o Portal na tela por alguns segundos antes de falar. |
| **MENSAGEM** | Sair de um módulo devolve ao Núcleo. Não é um link para outro site: é a mesma casa. |
| **FRASE** | *"Repare que eu não troquei de sistema. Saí de um módulo e voltei para o Núcleo — mesma instituição, mesma pessoa, mesmo lugar. Agora o outro módulo."* |
| **RISCO** | Nenhum. O retorno foi verificado nos dois sentidos. |
| **PLANO B** | — |

### 05:30 — Assessor: a tarefa (1 min)

| Campo | Conteúdo |
|---|---|
| **TELA** | Card **Assessor Pedagógico** → Acessar |
| **AÇÃO** | Mostrar a saudação com o nome, a data de entrega e a trilha (Diagnóstico → Atividade → Resultado). |
| **MENSAGEM** | O aluno não vê uma lista de exercícios. Vê a tarefa da escola e onde ele está nela. |
| **FRASE** | *"'Você tem Atividade de Estequiometria, entrega dia 7.' A tarefa do professor é o centro. Tudo que vem a seguir existe para ele conseguir entregar essa tarefa — a tarefa nunca desaparece da tela."* |
| **RISCO** | Se o reset não tiver sido rodado, a trilha já aparece parcialmente concluída e a história começa no meio. |
| **PLANO B** | Rodar o reset (seção 5) e recarregar. 10 segundos. |

### 06:30 — DIAGNOSTICAR (2 min)

| Campo | Conteúdo |
|---|---|
| **TELA** | Botão **"Responder diagnóstico"** |
| **AÇÃO** | Ler a chamada em voz alta, entrar e responder as **3 perguntas marcando B nas três**. |
| **MENSAGEM** | Antes da tarefa que vale nota, três perguntas sobre o pré-requisito. Não vale nota, e a tela diz isso. |
| **FRASE** | *"Antes de deixar ele tentar a atividade que vale nota, o sistema faz três perguntas sobre a base. E avisa: isto não vale nota. Diagnóstico que assusta não diagnostica."* |
| **RISCO** | Acertar sem querer muda o caminho: a correta da pergunta 1 é **A**, da 2 é **B**, da 3 é **C**. Marcar **B** nas três garante o caminho do erro. |
| **PLANO B** | Se ele acertar demais, o Assessor diz "Muito bem! Você demonstrou um bom domínio…" e o botão vira **"Continuar"** — que abre **um segundo diagnóstico**, agora do conteúdo principal. Isso é correto, mas parece repetição na tela. Dizer: *"ele mostrou que a base está firme, então o sistema não gasta o tempo dele ensinando o que ele já sabe — vai direto medir o conteúdo principal."* E então **voltar ao início e refazer com as respostas erradas**, ou pular para a Redação. |

### 08:30 — ENSINAR (3 min) — **ponto alto**

| Campo | Conteúdo |
|---|---|
| **TELA** | Tela de conclusão do diagnóstico → **"Entender o conceito"** |
| **AÇÃO** | Parar na frase do Assessor (**UAU 1**) e lê-la em voz alta. Depois rolar devagar até o exemplo resolvido com a tabela de átomos. |
| **MENSAGEM** | A explicação é conteúdo curricular do Núcleo, versionado, com exemplo resolvido passo a passo. Não é texto improvisado por um modelo na hora. |
| **FRASE** | *(UAU 1)* + *"Repare na tabela: elemento, entra, sai, situação. O hidrogênio fecha, o oxigênio não. É assim que um professor explica — e é assim que está escrito aqui."* |
| **RISCO** | A tela tem 1609px de altura num viewport de 768: **2,1 telas de rolagem**. Rolar rápido perde a plateia; rolar devagar queima um minuto. |
| **PLANO B** | Se o tempo apertar, ir direto à tabela de átomos — é o trecho que vale. |

### 11:30 — PRATICAR com ajuda (2 min) — **ponto alto**

| Campo | Conteúdo |
|---|---|
| **TELA** | **"Entendi, vamos praticar"** |
| **AÇÃO** | 1) Mostrar a fala inicial ("Tente primeiro por conta própria"). 2) Marcar **A** (errada) e Responder. 3) Clicar **"Quero uma dica"**. 4) Marcar **B** (certa) e Responder. |
| **MENSAGEM** | A ajuda é progressiva, e o sistema registra que ela foi usada. |
| **FRASE** | Depois do erro: *"Nenhuma tela do sistema diz 'errado'. Errar numa prática guiada é o funcionamento normal dela."* Depois do acerto: *(UAU 2)*. |
| **RISCO** | Se perguntarem "e se ele pedir todas?", o contador vai até 4 e a última é assistida. Custa 40 segundos mostrar. |
| **PLANO B** | Se a prática já estiver no meio, a fala diz "Vamos continuar de onde você parou" e o contador mostra as ajudas gastas. Não quebra, mas atrapalha a história do zero. Reset. |

### 13:30 — VERIFICAR e fechar (1 min)

| Campo | Conteúdo |
|---|---|
| **TELA** | Conclusão da prática guiada; depois voltar ao Portal |
| **AÇÃO** | Mostrar o botão **"Agora tentar sozinho"** sem clicar. Então voltar ao Portal e deixar os seis módulos na tela. |
| **MENSAGEM** | O ciclo não fecha no acerto com ajuda. Fecha quando ele acerta sem. |
| **FRASE** | *"O botão não diz 'continuar'. Diz 'agora tentar sozinho'. Não é o aluno que decide que aprendeu, e não é a IA: é a evidência."* Depois, no Portal: *"Dois módulos de pé, quatro no mapa, uma base só. O que vocês viram em química funciona em qualquer conteúdo que a escola colocar no Núcleo — porque a inteligência está no Núcleo, não no modelo."* |
| **RISCO** | Nenhum. |
| **PLANO B** | — |

---

## 4. O que NÃO mostrar

Cada item tem a evidência que o tirou do roteiro.

| Tela | Por quê |
|---|---|
| **Dashboard do Professor** (`/teacher`) | Dois campos de texto no cabeçalho com `user:prof_mendes` e um UUID cru (`6f26cd3c-63d5-…`) como "Escola". E a marca na lateral é **AGENTE IA EDU**, não Núcleo Edu 360. Projetado, são dois produtos diferentes e um ambiente de teste. |
| **Lista de temas de redação do professor** | Há temas chamados **`aergdfg`** e **`Teste`** não apagados no banco de demonstração. |
| **Fila de correções do professor** | 18 das 23 correções estão em `NEEDS_REVIEW` com `AllProvidersFailedError: openai ProviderTimeoutError`. É uma parede de falhas. |
| **Qualquer tela de secretaria/admin** | Não foi auditada neste ensaio. Não mostrar o que não foi visto. |
| **Entrada por foto / arquivo / voz** no Assessor | Os ícones existem e a própria tela avisa que ainda não funcionam. Se alguém clicar, a demonstração para para explicar. |

Se a pergunta "e a visão do professor?" vier — e ela vem —, a resposta honesta
é: *"existe e está rodando; a identidade visual dela ainda é a da versão
anterior, e estamos migrando. Posso mostrar depois da reunião, sem projetor."*

---

## 5. Checklist de contingência

### ANTES (na véspera)

- [ ] Subir a API:
      `PYTHONPATH=src DATABASE_URL="postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/agente_ia_edu" .venv/bin/python -m uvicorn agente_ia_edu.api.app:create_app --factory --port 8117`
- [ ] Abrir `/portal`, `/student/aluno.html` e `/redacao` e conferir que as três
      carregam.
- [ ] **Recarga forçada (Cmd+Shift+R) em cada uma das três.** O servidor agora
      manda `cache-control: no-cache`, mas um arquivo que o navegador guardou
      ANTES dessa mudança continua valendo pela regra antiga. Uma recarga
      forçada por página resolve de uma vez.
- [ ] Rodar o ensaio inteiro uma vez, do Bloco 1 ao 8, cronometrando.
- [ ] Deixar `/portal` e `/redacao` abertos em abas separadas — trocar de aba é
      mais rápido e mais seguro do que digitar URL na frente da plateia.

### 5 MINUTOS ANTES

- [ ] Reset do piloto:
      `DATABASE_URL="postgresql+psycopg://agenteedu:agenteedu_dev@localhost:5433/agente_ia_edu" .venv/bin/python scripts/reset_piloto_zero.py --sim`
      É idempotente: rodar duas vezes seguidas é inofensivo (a segunda zera
      todos os contadores e não falha — verificado).
- [ ] Recarregar `/student/aluno.html` e confirmar que a trilha começa em
      **Diagnóstico (etapa atual)** e o botão é **"Responder diagnóstico"**.
- [ ] Zoom do navegador em **125%** nas três abas.
- [ ] Silenciar notificações do sistema.

### SE O FLUXO QUEBRAR

| Sintoma | O que fazer |
|---|---|
| Tela do aluno em branco | Recarregar. Se persistir, Cmd+Shift+R. |
| Uma correção que você fez não aparece | É cache do navegador. Cmd+Shift+R. Aconteceu **três vezes** durante o ensaio. |
| A trilha começa no meio | Reset + recarregar. 10 segundos. |
| "Continuar tentando" onde você esperava "Tentar com ajuda" | A prática guiada já tem tentativas. Reset. |
| A API caiu | Subir de novo pelo comando da véspera. Enquanto sobe, usar o tempo para a parte de conversa (Bloco 8). |
| Nada sobe | Último recurso: contar a história pelas capturas de tela da véspera. **Tirar as capturas na véspera.** |

---

## 6. As perguntas que vão vir

| Pergunta | Resposta curta e verdadeira |
|---|---|
| "Isso é ChatGPT?" | "O modelo de IA é um componente. A decisão pedagógica — o que ensinar, quando, e quando considerar aprendido — está no Núcleo, em código versionado e testado. Trocar o fornecedor de IA não muda nenhuma dessas regras." |
| "Quantas escolas usam?" | Responder o número real. Não estimar. |
| "Funciona em outras disciplinas?" | "A estrutura é por conteúdo e habilidade, não por disciplina. O que está carregado hoje é química. Colocar outra disciplina é carregar conteúdo, não reescrever o sistema." |
| "E se a IA errar?" | "Por isso a correção de redação passa por aprovação do professor antes de chegar ao aluno, e por isso o sistema marca quando a leitura da foto ficou duvidosa." |
| "Quanto custa?" | Fora do escopo desta tela. Não improvisar número. |
| "Posso ver a visão do professor?" | Ver a nota no fim da seção 4. |

---

## 7. Ambiente

- API: `http://localhost:8117` (banco de desenvolvimento, porta 5433)
- Aluno do roteiro: **Aluno Teste A**, Escola ABC
- Aluno da Redação: identidade fixa de desenvolvimento (**outro aluno**)
- Reset: `scripts/reset_piloto_zero.py` — apaga só a evidência do Aluno Teste A;
  não toca no acervo, nas questões, no material teórico nem em outras escolas.
