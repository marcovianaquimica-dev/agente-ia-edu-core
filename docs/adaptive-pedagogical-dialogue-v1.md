# Diálogo pedagógico adaptativo — V1

Documento do bloco **ADAPTIVE_PEDAGOGICAL_DIALOGUE_V1** (2026-10-07), sobre
`fase6/vetorial`.

> O Assessor Pedagógico conversa com o estudante **para fazê-lo aprender**.

---

## 1. Onde cada decisão mora

```
Pedagogical Knowledge Graph    organiza o conhecimento        grafo_pedagogico
Pedagogical Decision Engine    qual é o próximo objetivo      assessor_pedagogico
Probe / Instrument Selection   com que instrumento investigar seletor_de_sondagem
Response Normalization         o que ele escreveu             resposta_do_aluno  ← novo
Pedagogical Observation        o que isso significa           resposta_do_aluno  ← novo
Hypothesis                     o que isso SUGERE              hipotese_pedagogica ← novo
Dialogue Execution             o turno a turno                servico_de_investigacao
Evidence Engine                o que conta como aprendizagem  curriculum_domain_map
Generative AI                  linguagem, nunca autoridade    explicacao/conversa
```

**Não há cérebro paralelo.** O diálogo é uma nova forma de EXECUTAR o motor
que já existia — `decidir_intervencao` continua escolhendo a ação,
`escada_de_apoio` continua medindo o apoio, o Evidence Engine continua sendo
o único que escreve domínio.

---

## 2. O modelo do diálogo

```
texto cru  →  RESPOSTA NORMALIZADA  →  OBSERVAÇÃO  →  HIPÓTESE
                                                        ↓
                                              PERGUNTA DISCRIMINANTE
                                                        ↓
                                         APOIADA  /  ENFRAQUECIDA
```

### Respostas aceitas

| o que ele escreve | lido como |
|---|---|
| `15`, `15 g/mol`, `17,0` | número |
| `três`, `acho que são 3 porque o número pequeno está no H` | número |
| `C`, `c` | alternativa |
| `não sei`, `sei lá`, `me ajuda` | **UNKNOWN** — tem observação própria |
| `3 ou 4`, `14 + 3` (quando se espera um número), `não sei se é 3` | **AMBÍGUO** |
| `` (vazio) | **SEM RESPOSTA** |

**Falha fechado.** O que não se lê com segurança não vira resposta. O Edu
pede de novo em vez de inventar um entendimento.

**Determinístico.** `17 == 17` não é trabalho para uma LLM, e fazê-la a
primeira opção tornaria o percurso refém de um timeout.

### Observações

`CORRECT_RESPONSE` · `INCORRECT_RESPONSE` · `UNKNOWN_RESPONSE` ·
`AMBIGUOUS_RESPONSE` · `EMPTY_RESPONSE` · `RECORDED_RESPONSE`

Nenhuma significa domínio — há teste varrendo as constantes atrás de
`MASTER`, `DOMIN`, `LEARNED`.

### Hipóteses

Quatro estados: `OPEN` → `SUPPORTED` / `WEAKENED` / `REJECTED`.

Sem confiança numérica. Um `0,73` daria ao palpite a aparência de medida.

**Transitórias.** A hipótese é derivada das respostas já gravadas — a que a
abriu e a da discriminante. Guardar o status seria uma segunda fonte de
verdade para algo recalculável. **Zero migration.**

---

## 3. O caso NH₃, turno a turno

Medido no navegador com `aluno_qa_dialogo1` (2026-10-07):

| # | quem | o que foi dito | normalizado | observação | hipótese | evidência? |
|---|---|---|---|---|---|---|
| 1 | Edu | "Qual é a massa molar do NH₃? N = 14 g/mol, H = 1 g/mol" | — | — | — | não |
| 2 | Aluno | `15` | 15.0 | `INCORRECT_RESPONSE` | `INDEX_OMISSION` **OPEN** | não |
| 3 | Edu | "Esse resultado **pode indicar** que o índice da fórmula ficou de fora da conta. Vamos conferir uma coisa antes de seguir." | — | — | — | não |
| 4 | Edu | "Na fórmula NH₃, quantos átomos de hidrogênio?" *(discriminante)* | — | — | — | não |
| 5 | Aluno | `3` | 3.0 | `CORRECT_RESPONSE` | **WEAKENED** | não |
| 6 | Edu | "Isso. Então a massa molar vai precisar contar o hidrogênio três vezes." | — | — | — | não |
| 7 | Edu | "Se cada hidrogênio contribui com 1 g/mol, qual é a contribuição dos três juntos?" | — | — | — | não |
| 8 | Aluno | `3 g/mol` | 3.0 | `CORRECT_RESPONSE` | — | não |
| 9 | Edu | "Isso. Cada elemento entra tantas vezes quanto o índice manda." | — | — | — | não |
| 10 | Edu | "O nitrogênio entra com 14 e os hidrogênios com 3. Qual é a massa molar?" | — | — | — | não |
| 11 | Aluno | `17` | 17.0 | `CORRECT_RESPONSE` | — | **não** |
| 12 | Edu | "É isso: massa molar é a soma das contribuições de cada elemento." | — | — | — | — |

**O 15 é refeito, não escrito à mão:** `contribuicoes("NH3")` dá N=14 e H=1
por átomo; 14 + 1 = 15 é o que sai de ignorar o índice. `conferir()` derruba
a suíte se a aritmética deixar de sustentar a hipótese.

### Caminho B — H = 1

`aluno_qa_dialogo2`, mesma abertura, respondeu `1` na discriminante:

- hipótese → **SUPPORTED**
- `bottleneck_skill` → `LEITURA_DE_FORMULA` (um degrau abaixo do alvo)
- o Edu ensinou a regra do índice **sem dar o número** (nível 1 do retorno)
- o alvo da investigação continua `MASSA_MOLAR` — descer é para voltar

### Caminho C — "não sei"

`aluno_qa_dialogo3`, pelo botão **Não sei**:

- observação `UNKNOWN_RESPONSE`, **não** erro
- nenhuma hipótese inventada (ele não mostrou raciocínio)
- o Edu: *"Sem problema. Vamos por um caminho mais curto."*
- **não gasta tentativa** — cobrá-la escalaria o retorno de pista para regra
  aplicada sem que ele tivesse tentado uma vez

### Ambiguidade

`3 ou 4` → *"Não consegui ler sua resposta com certeza. Pode escrever só o
número?"* Não vira 3. Não gasta tentativa. Não culpa o aluno.

---

## 4. Transferência

O item guiado de `MASSA_MOLAR` pergunta **CO₂** (44 g/mol), não NH₃ — há
teste de que o item ensinado e o item verificado não são o mesmo.

**Limitação:** o CO₂ está no degrau **guiada (L1)**, com dicas, e por
projeto não produz evidência. A tentativa **L0** que produz evidência vem da
prática pelo banco, não de um CO₂ com resposta aberta. O §6 pede CO₂ em L0;
isso **não** foi implementado.

---

## 5. Evidência — medido

`aluno_qa_dialogo1`, depois da cadeia inteira (abertura + 3 microperguntas):

```
questions_answered 3   questions_correct 2
origin_breakdown   {'MICRO_DIAGNOSTIC': 3}
```

A conversa contribuiu **zero**. O único origin é a sondagem.

E há teste varrendo o `metadata` inteiro depois da jornada: **uma única
tabela** recebe linha, `guided_practice_items` — a da interação assistida,
que o mapa de domínio não lê por projeto.

---

## 6. O P0 da mensagem contraditória

**Antes:** 2 de 3 na sondagem, errando massa molar →

> "Muito bem! Você demonstrou um bom domínio de Estequiometria e cálculos
> químicos."

…com o botão abaixo levando a investigar massa molar.

**Agora:**

> "Boa parte disso você já faz. Encontrei um ponto que vale a pena olharmos
> antes de seguir: massa molar."

**Nada da política mudou** — nem corte, nem `min_sample_size`, nem a
existência de `PROCEED`; há teste de cada um dos três. A decisão operacional
("ele pode começar a atividade") continua a mesma. Mudou a comunicação: ela
passa a refletir a decisão mais **específica** disponível.

E não virou só ressalva: ele acertou duas de três, e dizer apenas o que
faltou seria tão impreciso quanto declarar domínio.

---

## 7. Provider-off

O percurso NH₃ → investigação → ensino não chama modelo nenhum.
`test_percurso_sem_provedor` verifica por AST que os módulos do percurso não
importam provedor, e os três novos (`resposta_do_aluno`,
`hipotese_pedagogica` e a camada de diálogo) são determinísticos por
construção.

A IA segue entrando em **um** lugar — a explicação de um erro — com prompt
versionado e fallback honesto.

**Provider-on não foi exercitado neste bloco.** O pipeline
`LLM → estrutura validada → regra de domínio` está desenhado e **não
implementado**: hoje nenhuma interpretação semântica entra no diálogo.

---

## 8. Limitações reais

1. **O texto do aluno não é persistido.** `GuidedPracticeItem` não tem coluna
   para a resposta, e manter zero migration foi uma escolha. Dentro da sessão
   a conversa está inteira; **ao recarregar a página, o "15" e a frase da
   hipótese se perdem** e o fio recomeça do que o backend sabe.

2. **CO₂ em L0 não existe.** Ver §4.

3. **Uma hipótese por investigação.** `hipotese_para` já escolhe pelo valor
   observado, mas só `MASSA_MOLAR` tem gatilho escrito. Outros erros não
   produzem hipótese — corretamente: o sistema só supõe onde alguém decidiu
   de antemão qual suposição aquele número sustenta.

4. **O pedido de ajuda não fica gravado.** "Não sei" não gasta tentativa, e
   não há onde registrar que ele pediu ajuda. Gravá-lo como tentativa seria
   registrar um erro que não houve.

5. **A conversa livre ("Tenho uma dúvida") continua separada do diálogo.**
   `ConversaDoAssessor` não foi integrada ao fio de turnos.

6. **"Explique de outro jeito" não foi exercitado dentro do diálogo** neste
   bloco — ele continua funcionando no fluxo de explicação do erro, com
   troca real de estratégia, verificada no bloco anterior.

7. **Uma micro-habilidade.** `MASSA_MOLAR`, com `LEITURA_DE_FORMULA` como
   pré-requisito. É o vertical slice que o §26 pede.
