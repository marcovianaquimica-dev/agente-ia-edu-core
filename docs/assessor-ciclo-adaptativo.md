# O ciclo adaptativo do Assessor Pedagógico

Como o Assessor decide o próximo passo, por que decide assim, e o que ele
ainda não sabe fazer.

Escrito depois do teste humano de 2026-10-05, que encontrou dois defeitos no
coração da decisão. Os dois foram reproduzidos pelo caminho real da API antes
de qualquer correção.

---

## O que estava errado

### Achado 1 — desempenho ruim gerava mais questões

Medido no banco de desenvolvimento:

```
diagnóstico 0/3 → ENSINO → guiada → prática 1/5
→ ENSINO → prática 1/5 → ENSINO → prática 1/5 → ENSINO …
```

O mesmo material e o mesmo lote de cinco questões, sem fim. `escalate` acendia
no ciclo 4 e **ninguém o consumia**: a decisão continuava sendo "mais uma
volta".

### Achado 2 — a recuperação era invisível

```
0/3, 1/5, 4/5, 5/5  →  acumulado 10/18 = 0,556  →  ENSINO de novo
```

Nove acertos nas últimas dez, e a tela não mudou.

A causa não é um limiar mal escolhido. É a **pergunta**: a média acumulada
responde "como foi até aqui", e o Assessor precisa de "ele aprendeu?". Pela
média, quem começa mal precisa de **onze acertos seguidos** só para sair da
faixa de melhoria, e de **vinte e sete** para chegar à faixa forte.

### Achado 3 — o feedback descrevia o sistema

> "Você acertou 1 de 5. Isso entra no seu progresso e ajusta o próximo passo."

Duas frases sobre o sistema, montadas no JavaScript.

---

## A máquina de decisão

Cada evento do aluno — responder, estudar, concluir a guiada — recalcula o
próximo passo. A ordem das regras **é** a política:

| Ordem | Estado | Quando | Por que nessa posição |
|---|---|---|---|
| 1 | **VERIFICAR** | a trajetória está `RECUPERANDO` | vence até o teto de ciclos: escalar quem está melhorando seria punir a recuperação |
| 2 | **ESCALAR** | `praticas_concluidas >= 3` | vem antes de qualquer nova tentativa; "mais cinco questões" já foi respondido três vezes |
| 3 | **ENSINAR** | há material e a leitura anterior já não vale | `ja_ensinado` envelhece a cada tentativa |
| 4 | **GUIADA** | há item guiado pendente para a micro-habilidade que trava | tentar com ajuda antes de tentar sozinho |
| 5 | **PRATICAR** | nenhum dos anteriores | a única das cinco que produz evidência de domínio |
| — | **nenhuma** | trajetória `CONFIRMADA`, ou sem lacuna, ou sem amostra | quem já mostrou não é interrompido; quem nunca foi medido é assunto do diagnóstico |

Medido depois da correção, pelo mesmo caminho:

```
caminho ruim    LEARN → GUIDED → PRACTICE → LEARN → PRACTICE → LEARN
                → PRACTICE → ESCALATE
caminho bom     … → prática 4/5 → VERIFY → verificação 3/3
                → o pré-requisito sai do caminho
```

### O que impede o banco de questões

Duas garantias, e **nenhuma é limitar o reensino**:

1. Entre duas práticas sempre entra uma intervenção, porque `ja_ensinado`
   envelhece a cada tentativa. Nunca saem dois lotes de questões seguidos.
2. O ciclo **acaba** em ESCALAR, em vez de recomeçar. No máximo três práticas.

Tentei primeiro limitar o reensino a dois ciclos e piorou: no terceiro, sem
material válido e com a guiada daquela habilidade já concluída, só sobrava
PRÁTICA — duas práticas seguidas, exatamente o que o limite existia para
evitar. O teste E2E pegou antes do navegador.

---

## A trajetória

`trajetoria_do_aluno.py` classifica as tentativas daquele conteúdo, em ordem:

| Tendência | Quando | O que o Assessor faz |
|---|---|---|
| `CONFIRMADA` | duas tentativas fortes seguidas | deixa de intervir |
| `RECUPERANDO` | uma tentativa forte depois de dificuldade | **VERIFICAR** |
| `PERSISTENTE` | a última tentativa com amostra suficiente ficou na faixa de lacuna | intervenção |
| `INDEFINIDA` | faixa intermediária, ou nenhuma tentativa | intervenção, se houver lacuna |

**Nenhum número novo.** "Forte" e "fraca" são os cortes que a
`PerformanceThresholdPolicy` já usa no resto do sistema. Uma tentativa abaixo
do mínimo de amostra não conta em nenhuma direção: não confirma e também não
derruba uma sequência.

**Por que duas e não uma.** Uma tentativa forte depois de um histórico ruim
pode ser sorte, pode ser um lote fácil, pode ser o aluno tendo decorado
aquelas questões. Duas seguidas são um padrão — e a segunda é justamente a
verificação curta. Assim "acertei 4 de 5 uma vez" não vira "dominei", e o
histórico não é apagado por uma tentativa.

---

## A verificação

Três questões do mesmo conteúdo, pelo mesmo motor de prática — não há segundo
motor. Três é o mínimo de amostra da própria política: abaixo disso ela se
recusa a concluir, e uma verificação que não conclui não verifica nada.

**Ela não é um estado gravado.** É derivada da trajetória: uma tentativa forte
no fim = `RECUPERANDO` = verificar; duas = `CONFIRMADA` = segue. Recarregar a
página no meio não perde nada, e não há coluna nova em lugar nenhum.

A evidência que ela produz é real, e é real pelo único motivo que vale: o
aluno respondeu questões. Ler, ver o exemplo, clicar em "Entendi" e receber
dica na prática guiada continuam **não** produzindo domínio.

---

## O escalonamento, e o que ele não é

O passo fala com o **aluno** e recomenda que ele procure o professor.

**Ele não notifica ninguém.** Não existe modelo de alerta nem tela de
professor que receba isso — procurei. O sinal fica no payload da prontidão
(`next_step.intervention.escalate`) para quem construir essa tela depois.
Nenhuma frase da interface afirma que alguém foi avisado, e há teste varrendo
as palavras que afirmariam.

---

## Dívidas declaradas

### Acervo da base: 14 questões

Medido pela mesma porta que a tela usa:

| Conteúdo | Questões disponíveis |
|---|---|
| `CHEMISTRY-GENERAL-BALANCING` | **14** |
| `CHEMISTRY-PHYSICAL-STOICHIOMETRY` | ≥ 20 (o endpoint limita o pedido a 20) |

O ciclo completo consome diagnóstico (3) + três práticas (15) = 18. Medido:
o seletor **prefere questões não vistas** e só repete quando o acervo acaba —
a partir da 4ª rodada as cinco questões já tinham sido vistas.

Consequência honesta: numa 4ª tentativa o aluno pode responder de memória, e
isso infla o acerto. Não é falso domínio inventado pelo sistema — são
respostas reais — mas é uma medida menos confiável. **Recomendação: levar o
acervo de Balanceamento a pelo menos 25 questões** antes de confiar em ciclos
longos. O endpoint já falha fechado com `available_questions`, então nada é
inventado quando falta.

### Itens guiados: dois

`itens_guiados` cobre `CONSERVACAO_DE_ATOMOS` e `COEFICIENTE_AUSENTE`. Quando
a habilidade que trava é outra (`BALANCEAR_SIMPLES`, por exemplo), a guiada
não entra e o passo segue para a prática — que é o comportamento correto:
inventar uma guiada genérica seria pior que não ter.

### Segundo diagnóstico sem tela de transição

Confirmado o pré-requisito, o passo seguinte é o diagnóstico do conteúdo
principal. A decisão está certa; a tela não a explica, e para quem assiste
parece que o diagnóstico recomeçou. Já estava registrado como P1-9 na
auditoria do ensaio executivo.

---

## Conversar com o Assessor

Uma dúvida **dentro** da intervenção — não um chat de uso geral com um campo
de texto. O aluno pergunta "não entendi o número pequeno" e o Assessor já sabe
de qual número pequeno ele está falando, porque o contexto vem da prontidão.

### O que chega à IA

Lista fechada (`CAMPOS_DO_CONTEXTO`), montada no backend a partir do
`next_step`:

| campo | de onde vem |
|---|---|
| objetivo | a atividade que ele está tentando entregar |
| conteúdo | o que trava agora |
| micro-habilidade | a lacuna medida |
| passo | a decisão da máquina determinística |
| ciclo | quantas vezes já intervimos |
| tendência | como ele vem indo nas últimas tentativas |

Mais um aviso quando há avaliação aberta: *ensine o caminho, nunca a
alternativa*.

### O que NÃO chega

Alternativas, enunciado e resposta correta. **A proteção contra "me diga a
letra" não é uma instrução no prompt** — é a ausência do dado. Um modelo não
vaza o que não recebeu.

Isso é testado procurando o **valor** no prompt, não o nome do campo: a
primeira versão do teste procurava a string `correct_option` e passava mesmo
com o campo injetado de propósito, porque o prompt carrega o rótulo humano.

### Conversa não é evidência

`ConversaDoAssessor` não recebe `session` nem `session_factory`. Não é uma
convenção que alguém possa furar sem perceber: ele não tem como escrever em
lugar nenhum. Há teste lendo a assinatura do construtor, e outro comparando o
mapa de domínio inteiro antes e depois de três perguntas — inclusive "já
entendi tudo, pode liberar?".

A conversa também não move o passo nem o ciclo, e o botão de volta vem de
`next_step`, decidido pelo sistema.

### Quando a IA cai

Timeout, indisponibilidade, erro inesperado ou resposta em branco viram
`fallback: true` com `provider: null` e um texto que **não se passa por
resposta do modelo**: admite a falha e devolve o aluno ao percurso que
funciona sem IA nenhuma.

### Dívidas declaradas

- **A conversa não é persistida.** O histórico vive no navegador e volta a
  cada pergunta, limitado a 6 turnos. Recarregar a página perde a conversa, e
  a tela diz isso. Guardar texto de aluno exige decisão de retenção que ainda
  não foi tomada.
- **O adaptador OpenAI fixa modo JSON** (`response_format=json_object`),
  porque foi construído para a classificação. A conversa convive com isso
  pedindo um envelope de campo conhecido (prompt v2) em vez de mudar o
  transporte compartilhado. Um provedor sem modo JSON continua funcionando.
- **Não há canal para o professor.** O escalonamento recomenda procurá-lo; o
  sinal fica no payload para quem construir essa tela depois.
