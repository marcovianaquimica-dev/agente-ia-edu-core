# CÉREBRO — próxima bateria pedagógica (proposta, não executada)

**Nada aqui foi executado.** Nenhuma chamada paga foi feita para estas
perguntas. O propósito é que você aprove, altere ou recuse antes de
qualquer execução.

**Nenhuma resposta esperada foi escrita, e o sistema não foi ajustado para
acertar nenhuma delas.** Escrever o gabarito antes de rodar convidaria a
avaliar o quanto a saída se parece com o que eu teria escrito — que não é
a pergunta.

## Verificação de colisão

Conferida contra **25 consultas congeladas distintas** e **6 pilotos já
usados**, por sobreposição lexical (Jaccard sobre tokens normalizados, sem
acento, sem palavras funcionais).

Os conjuntos congelados:

| conjunto | n |
|---|---:|
| `VECTOR_EVALUATION_SET_V1` | 10 |
| `VECTOR_CALIBRATION_SET_V1` | 8 |
| `SANITY_ONLY_QUERIES` | 7 |
| `AMBIGUITY_PAIR` | 2, **já contidas** no Evaluation |

**Nenhuma proposta atinge Jaccard ≥ 0,25 com nada.** A maior sobreposição
de toda a bateria é **0,200**.

| | maior sobreposição | contra |
|---|---:|---|
| N1 | 0,200 | `como separar os componentes de uma mistura` (sanity) |
| N2 | 0,077 | idem |
| N3 | **0,000** | — |
| N4 | 0,167 | `o que acontece com a matéria quando ela muda de estado` |
| N5 | 0,100 | `relação entre a massa de uma substância e o número de partículas` |
| N6 | **0,000** | — |
| N7 | **0,000** | — |
| N8 | **0,000** | — |
| N9 | 0,125 | `o que faz uma reação acontecer mais depressa` |

Jaccard é peneira lexical, não prova de independência semântica. Por isso
cada adjacência conceitual está declarada abaixo, pergunta a pergunta.

**Colisão significa reusar a consulta.** Recuperar os mesmos chunks não
contamina nada — o que contaminaria o Evaluation Set é usar suas consultas
para escolher parâmetro.

## As nove perguntas

### N1 — explicação conceitual

> Por que o óleo não se mistura com a água?

Linha de base das demais. Conceito central (polaridade e interações
intermoleculares), bem coberto, formulação neutra.

*Diagnóstico:* o caminho produz explicação causal ou descrição? Qual a
composição editorial quando a pergunta não tem nenhuma marca de registro?

*Adjacência declarada:* `por que algumas substâncias conduzem corrente
elétrica dissolvidas` (Calibration) trata de ionização e condutividade —
conceito distinto, capítulo possivelmente compartilhado.

### N2 — aluno com dificuldade

> Não entendi nada sobre por que o óleo não se mistura com a água. Pode
> explicar de um jeito bem simples, com um exemplo do dia a dia?

**Mesmo conteúdo de N1. Única variável: o pedido de adaptação.** Precisa
ser executada **em par com N1**, na sequência — isolada, não diz nada.

*Diagnóstico:* a resposta muda de registro? E a **recuperação** muda, já
que o ruído pedagógico entra no texto que vira embedding? A segunda
pergunta é tão importante quanto a primeira e só esse par a responde.

*Previsão registrada antes de rodar:* **não existe mecanismo de adaptação
de registro.** A instrução diz apenas "Responda em português, de forma
direta". Se a resposta sair adaptada, foi o modelo reagindo sozinho — não
uma funcionalidade. Se sair igual, não é defeito de algo que existe.

### N3 — resolução passo a passo

> Como se escreve a fórmula de um composto iônico a partir das cargas dos
> íons? Mostre o passo a passo.

*Diagnóstico:* sequência executável ou parágrafo descritivo? O piloto D
(balanceamento) produziu passo a passo com exemplo resolvido sem que nada
no prompt pedisse — N3 testa se aquilo se repete quando o pedido é
explícito.

*Adjacência declarada:* `como os átomos se unem para formar substâncias`
(Calibration) é a pergunta ampla sobre ligação; N3 é um procedimento
específico dentro dela.

### N4 — concepção equivocada do aluno

> Quando uma vela queima, ela vai sumindo. Isso quer dizer que a matéria
> dela foi destruída?

O erro vem **afirmado como premissa**, que é o formato real em que ele
chega.

*Diagnóstico:* o modelo refuta a premissa ou responde por cima dela? Nada
no prompt manda corrigir concepção equivocada — isto mede o comportamento
espontâneo, e um tutor que valida o erro do aluno é pior que um que não
responde.

*Adjacência declarada:* `o que acontece com a matéria quando ela muda de
estado` (Calibration) e `habilidade sobre transformações e conservações em
sistemas` (Evaluation, descritor BNCC abstrato). N4 é conservação de massa
em combustão, formulada como dúvida concreta.

### N5 — relação entre dois conceitos

> Qual a relação entre o tamanho do átomo e a facilidade dele perder
> elétrons?

Raio atômico e energia de ionização vivem em trechos distintos.

*Diagnóstico:* o contexto reúne as duas pontas ou traz cinco evidências
sobre uma só? É aqui que o corte por `BUDGET_EXHAUSTED` pode doer mais —
se o orçamento se esgotar em chunks sobre raio atômico, a relação fica sem
a metade que a sustenta.

*Adjacência declarada:* piloto C (tabela periódica), que **não é conjunto
congelado**. Pode recuperar os mesmos chunks, e isso é aceitável.

### N6 — aplicação contextual

> Por que o bicarbonato de sódio é usado para aliviar a azia?

*Diagnóstico:* o sistema vai do conceito à aplicação, ou devolve a teoria
ácido-base sem fechar com o caso concreto?

*Adjacência declarada e deliberada:* piloto B foi `o que torna uma
substância ácida ou básica`. A adjacência é o ponto — B mostrou que o
conceito é bem coberto, e N6 testa se a aplicação também é. B não é
conjunto congelado.

### N7 — pergunta ambígua

> Como funciona uma pilha?

Ambígua fora do contexto (objeto do dia a dia / empilhamento / célula
eletroquímica) e subespecificada dentro dele (que tipo de pilha?).

*Diagnóstico:* o sistema assume um sentido em silêncio, ou sinaliza a
ambiguidade? Um tutor que responde confiantemente à pergunta errada é um
modo de falha pedagógico próprio.

*Adjacência declarada:* `oxidação e redução` e `corrente elétrica em
metais` (ambas Calibration). Eletroquímica envolve redox — adjacência
conceitual real, formulações completamente distintas.

### N8 — evidência insuficiente, versão difícil

> Qual é o valor numérico da constante de acidez (Ka) do ácido acético a
> 25 °C?

**Alta sobreposição lexical com o corpus, fato específico provavelmente
ausente.** É a versão difícil de propósito: perguntar sobre história
romana testaria só o caminho trivial.

*Diagnóstico:* o sistema abstém ou fabrica um número?

*Previsão registrada antes de rodar:* **provavelmente não abstém.** Não há
limiar de similaridade — e a Fase 6 recusou inventar um, com razão. A
recuperação vai devolver 10 hits com score normal, o contexto vai ser
montado e o provider vai ser chamado. A única porta estrutural de abstenção
é o modelo não citar ninguém. Além disso, `sufficient: false` **não fecha o
portão** hoje: o campo é gravado e nenhum ramo o consulta.

*Risco do teste:* se algum dos livros tabelar o Ka, o teste vira medida de
precisão de recuperação. Nesse caso eu digo isso, em vez de alegar que
mediu abstenção.

### N9 — fora do corpus, versão clara

> Qual o mecanismo da reação de Diels-Alder e qual a sua estereoquímica?

Orgânica de nível superior. Contraste com N8: aqui a ausência é óbvia.

*Diagnóstico:* separa "não está no corpus" de "está mal coberto". Se N9
abstiver e N8 não, o portão funciona só quando a distância lexical ajuda —
o que é precisamente a limitação a documentar.

*Adjacência declarada:* `cadeias carbônicas e funções orgânicas` (sanity) é
orgânica geral de ensino médio. Diels-Alder não aparece nos três livros.

## Cobertura das categorias pedidas

| categoria | pergunta |
|---|---|
| explicação conceitual | N1 |
| aluno com dificuldade | N2 |
| passo a passo | N3 |
| concepção equivocada | N4 |
| relação entre conceitos | N5 |
| aplicação contextual | N6 |
| pergunta ambígua | N7 |
| evidência insuficiente / fora do corpus | N8, N9 |

## Custo estimado

Pelos pilotos, cada execução custou entre US$ 0,00055 e US$ 0,00071. Nove
perguntas: **cerca de US$ 0,006**. Com N1/N2 obrigatoriamente em par, a
ordem sugerida é N1 → N2 → N3 → … → N9.

## O que observar, e quem observa o quê

Cinco das seis dimensões saem no relatório do sistema:

| dimensão | onde |
|---|---|
| recuperação | hits, scores, papéis, `filtered_out`, `cap`, `degraded` |
| contexto | evidências, chars, `BUDGET_EXHAUSTED`, exclusões |
| resposta | texto, marcadores citados |
| fundamentação | status, `invalid_markers`, fontes resolvidas |
| portão de insuficiência | status + `model_says_sufficient` |

**Qualidade pedagógica não sai, e não deve ser julgada por mim.** É
julgamento curricular, e o princípio do projeto desde a Fase 4 é que esse
julgamento é humano.

Proposta de rubrica, 0–2 por item, preenchida por você:

| item | 0 | 1 | 2 |
|---|---|---|---|
| correção química | erro | impreciso | correto |
| adequação ao nível | inadequado | aceitável | adequado |
| completude | falta o essencial | parcial | completo |
| utilidade para o aluno | não ajuda | ajuda em parte | ajuda |

Monto a planilha quando você mandar, **sem registrar palpite meu** — como
na adjudicação dos 67 e dos 276.

## Limite que permanece

Aprovadas ou não, **estas nove não podem virar benchmark.** Se um dia
servirem para comparar versões, a comparação estará contaminada: foram
escolhidas por mim, sem adjudicação, sem pool. A régua é o Evaluation Set.
