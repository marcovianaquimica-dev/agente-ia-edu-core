# Continuidade do diálogo e verificação autônoma — V1

Documento do bloco **PEDAGOGICAL_DIALOGUE_CONTINUITY_AND_AUTONOMOUS_VERIFICATION_V1**
(2026-10-08), sobre `fase6/vetorial`.

> O Edu conversa com o estudante para ajudá-lo a aprender. Essa conversa
> precisa ter continuidade, e a aprendizagem precisa ser verificada por
> evidências autônomas.

---

## 1. P0-A — a resposta do §3

> Qual é a menor mudança capaz de preservar e reconstruir uma conversa
> pedagógica sem duplicar o domínio existente?

Auditei antes de responder. O estado pedagógico que o §4 pede **já** é
persistido ou **já** é derivável:

| o que o §4 pede | onde já estava |
|---|---|
| aluno, instituição, escopo | identidade e `UserSchoolLink` |
| conteúdo, micro-habilidade, objetivo | o passo do assessor |
| qual etapa está de pé | uma linha por `(aluno, item_key)` |
| quantas tentativas | `attempts` |
| resolveu sem ajuda | `solved_unaided` |
| nível de apoio | derivado pela escada, de três fatos já gravados |
| resposta normalizada | `normalizar(texto)` |
| observação pedagógica | `observar(normalizada)` |
| hipótese | `hipotese_para(inv, numero)` |
| estado da hipótese | o desfecho gravado da etapa discriminante |

Sobrou **um** fato não derivável: o **texto que o aluno escreveu**. Nenhuma
função pura o reconstrói, porque ele não é consequência de nada — é a
entrada.

E, auditando, apareceu um segundo: **o pedido de ajuda**. "Não sei" não
gastava tentativa e por isso não ficava em lugar nenhum.

### A migration 068, e só ela

```
response_text   VARCHAR(400)  NULL        o que ele escreveu
help_requests   INTEGER       NOT NULL 0  quantas vezes pediu ajuda
```

mais duas travas:

```
help_requests >= 0
NOT solved_unaided OR help_requests = 0
```

Aditiva: nenhuma coluna existente muda, nada é apagado, nenhuma trava
existente é removida. A coluna nasce com `server_default 0`, então toda
linha que já existe satisfaz a trava nova — medido no banco de
desenvolvimento, **0 linhas incoerentes**.

### O que a trava nova impede

Quem dizia "não sei" e acertava na tentativa seguinte era gravado com
`solved_unaided = true` — "resolveu sozinho" para quem pediu ajuda primeiro.
Isso não é só histórico perdido: é ajuda contada como autonomia. A trava
fecha no **banco**, não só no serviço.

### A regra do que é gravado

**O Edu grava o que LEU.**

| o aluno escreve | lido? | gravado? |
|---|---|---|
| `15`, `três`, `3 g/mol` | sim | sim |
| `não sei`, `me ajuda` | sim — tem observação própria | sim, e conta ajuda |
| `3 ou 4` (ambíguo) | **não** | não |
| vazio | **não** | não |

Escrevi a regra errada primeiro — não gravava o "não sei" — e o navegador
corrigiu: o fio mostrava `Você: não sei` dentro do turno e o perdia no F5,
porque a frase vinha da memória da tela.

---

## 2. P0-A — medido no navegador

`aluno_qa_cont1`, com **F5 de verdade** entre cada par de falas:

| # | quem | o que aparece depois do recarregar |
|---|---|---|
| 1 | Edu | "Qual é a massa molar do NH₃? Considere N = 14 g/mol e H = 1 g/mol." |
| 2 | **Aluno** | **`15 g/mol`** — com a unidade, como ele escreveu |
| 3 | Edu | "Esse resultado **pode indicar** que o índice da fórmula ficou de fora da conta." |
| 4 | Edu | "Na fórmula NH₃, quantos átomos de hidrogênio?" |
| 5 | **Aluno** | **`três`** — por extenso, não a grafia do gabarito |
| 6–9 | … | `3 g/mol`, `17` |

E no banco, lido direto:

```
INV-EST-MASSA-MOLAR#0  MASSA_MOLAR         tent=1 ajuda=0 sozinho=False ok=False  fala='15 g/mol'
INV-EST-MASSA-MOLAR#1  LEITURA_DE_FORMULA  tent=1 ajuda=0 sozinho=True  ok=True   fala='três'
INV-EST-MASSA-MOLAR#2  MASSA_MOLAR         tent=1 ajuda=0 sozinho=True  ok=True   fala='3 g/mol'
INV-EST-MASSA-MOLAR#3  MASSA_MOLAR         tent=1 ajuda=0 sozinho=True  ok=True   fala='17'
```

`aluno_qa_cont2`, caminho do "não sei", também com F5: a fala volta, e
`app.investigacao.dito` está **nulo** — o que prova que ela veio do backend
e não da memória da tela.

### As sete interrupções do §1, uma a uma

| interrupção | medido |
|---|---|
| atualização da página | F5 entre cada par de falas — o fio volta inteiro |
| fechamento e reabertura | a identidade fica em `localStorage`, o estado no banco |
| navegar para outra área | ida à Home e volta, a conversa continua |
| retorno posterior | o degrau da escada acompanha (`Localizar (feito)`) |
| durante uma micropergunta | F5 no meio da cadeia, a etapa aberta é a mesma |
| **durante a prática guiada** | F5 com 3 dicas pedidas e 1 erro: *"Vamos continuar de onde você parou"*, as três dicas de volta |
| **antes da tentativa autônoma** | F5 depois do fading: `Tentar com ajuda (feito) / Praticar (agora)`, e o L0 serve Na₂O |

### O que NÃO volta, e por quê

A frase de acolhimento — *"Sem problema. Vamos por um caminho mais curto."*
— é uma reação **daquele turno**, derivada da observação da resposta que
acabou de chegar. Depois do recarregar ela não reaparece: o que volta é o
estado (a pergunta aberta, a fala dele), não a reação. Reexibi-la seria o
Edu acolhendo de novo algo que o aluno não acabou de dizer.

---

## 3. P0-B — o que o banco mostrava antes

Medido em 2026-10-08, no banco de desenvolvimento:

```
CHEMISTRY-PHYSICAL-STOICHIOMETRY: 25 classificações ativas
  MASSA_MOLAR: 1   ← o próprio item da sondagem
```

A jornada da micro-habilidade era:

```
sondagem (diagnóstico)   NH₃  →  17 g/mol      ele errou aqui
investigação (L3)        NH₃  →  17 g/mol      a mesma substância
ensino (L2)              NH₃  →  17 g/mol      a mesma
prática guiada (L1)      CO₂  →  44 g/mol      com quatro níveis de dica
autônomo (L0)            —                     não havia item
```

A prática pelo banco seleciona por **conteúdo**. Para Estequiometria isso
devolve questões de proporção e de relação massa-mol: a micro-habilidade
ensinada nunca era verificada sozinha. E pedir "a questão de massa molar do
banco" reserviria o item que ele acabou de errar, investigar e ver resolvido
passo a passo.

---

## 4. P0-B — os três itens, e por que estes

```
Na₂O       índice no PRIMEIRO elemento
SO₃        índice no SEGUNDO, com outro valor
Mg(OH)₂    índice FORA do parêntese, multiplicando dois elementos
```

NH₃ e CO₂ têm o índice no segundo elemento. Um conjunto de verificação que
repetisse só essa forma mediria o reconhecimento do padrão — *"o numerinho
depois do segundo símbolo"* — e não a regra. Há teste exigindo que a posição
varie.

Ca(OH)₂ ficou de fora apesar de ter a forma com parêntese: ele já aparece na
investigação de `LEITURA_DE_FORMULA`, e apareceria duas vezes na mesma
jornada. Mg(OH)₂ tem a mesma estrutura e outro metal.

### Todo distrator é um erro nomeado

| item | A | B | C | D |
|---|---|---|---|---|
| Na₂O | 39 — índice esquecido | 46 — só os dois sódios | **62** | 78 — índice no oxigênio também |
| SO₃ | 48 — índice esquecido | 64 — dois oxigênios em vez de três | **80** | 144 — índice no enxofre também |
| Mg(OH)₂ | 41 — índice esquecido | **58** | 82 — índice no magnésio também | 57 — índice só no oxigênio |

`conferir()` refaz os **quatro** números de cada item a partir de
`massa_molar` e `contribuicoes`. Ele encontrou **dois erros meus**: eu havia
escrito `64 g/mol` com a frase "três oxigênios" (3 × 16 = 48, que colidia
com o distrator do índice esquecido) e um `34 g/mol` com uma conta sem
sentido. Os dois estão corrigidos porque a conferência os recusou.

---

## 5. P0-B — a arquitetura, e o que NÃO foi criado

**Nenhum motor novo.** O que mudou:

1. `instrumento_de_sondagem` — a **finalidade** passou a ser parâmetro, com
   padrão `PROBE`. Os outros oito critérios (viva, validada por humano com
   nome, publicada, acessível, com gabarito, sem imagem, não protegida, da
   habilidade e conteúdo certos) são os mesmos, aplicados pelo mesmo código.
   Escrever um segundo módulo criaria duas definições de "instrumento
   utilizável", e elas divergiriam no primeiro ajuste.

2. `SeletorDeSondagem.verificacao()` — mesmo carregamento, mesmo contrato,
   finalidade `VERIFICATION`. **Sem fallback genérico**: sem item curado,
   devolve vazio e quem chamou cai na seleção por conteúdo que já existia.
   Vazio é uma resposta honesta — *"não há com que verificar isto sem
   repetir o que foi ensinado"* — e é o caso de 36 dos 37 conteúdos.

3. `verificacao_da_habilidade.selecao_para_pratica()` — a cola, num serviço
   fino **para ter teste**. A habilidade vem de
   `ReadinessRouteService.habilidade_que_trava_de`, que lê o grafo e as
   respostas reais. A alternativa — o navegador mandar a habilidade no POST
   — deixaria a UI decidir pedagogicamente.

4. `POST /student/practice` — passa `question_version_ids` quando há
   seleção, e devolve `skill_verified` e `selection_reason` como **dado**.

Depois disso tudo segue idêntico: montagem da lista, atribuição, tentativa,
correção determinística, Evidence Engine.

**Zero migration no P0-B.**

---

## 6. P0-B — medido no navegador

`aluno_qa_cont1`, depois do fading da guiada, clicando "Agora tentar
sozinho". A resposta do `POST /student/practice`:

```json
{
  "question_count": 3,
  "selection": { "available_questions": 29, "selection_mode": "CALLER_SUPPLIED" },
  "skill_verified": "MASSA_MOLAR",
  "selection_reason": "item curado do Núcleo para verificar esta micro-habilidade
                       sem apoio: finalidade de verificação declarada,
                       validado por nucleo_edu_360, publicado"
}
```

E as três questões servidas, na ordem: **Na₂O → SO₃ → Mg(OH)₂**. Nenhuma
delas é o NH₃ ensinado, o CO₂ praticado com dica ou o item da sondagem.

---

## 7. As duas invariantes, medidas em direções opostas

### O guiado NÃO produz evidência

`GPG-EST-MASSA-MOLAR-1` (CO₂), resolvido **com duas dicas**:

```
guided_practice_items:  dicas=2  sozinho=False  ok=True
activity_results:       0 linhas desta tentativa
activity_result_items:  nenhum item de CO₂
domain_content_mastery: nada veio daqui
```

### O autônomo produz — e só isso

Depois dos três itens L0 corretos:

```
origin_breakdown = {'PRACTICE': 3, 'MICRO_DIAGNOSTIC': 3}
5 corretas de 6 respondidas
evidence_state = OBSERVED
```

`OBSERVED`, não uma banda de domínio. `PerformanceThresholdPolicy` continua
intocada — nem corte, nem `min_sample_size`, nem a existência de `PROCEED`.
Três acertos no degrau autônomo produzem **evidência**; quem transforma
evidência em domínio é a política, e um acerto isolado continua sendo
amostra insuficiente.

A decisão pedagógica seguinte, lida na tela: **Preparação (concluída) →
Atividade (etapa atual)**. O ciclo fechou.

---

## 8. Três defeitos que só o navegador mostrou

1. **O Enter não enviava.** Eu confiava no envio implícito do formulário, e
   o bloco anterior chegou a declarar isso como acessibilidade entregue. Não
   dá para distinguir por script se a falha era do produto ou do teclado
   sintético — só um Enter de verdade dispara o envio implícito. Então o
   Enter ganhou handler próprio, e aí funciona.

2. **A resposta digitada era descartada em silêncio.**
   `enviarInvestigacao` lia `inv.rascunho` e o zerava logo depois; a partir
   do segundo envio o rascunho valia `""` (que não é `undefined`), a reserva
   `campo.value` nunca era consultada, e o texto sumia. Não chegou a
   aparecer porque os dois chamadores escreviam o rascunho antes — mas o
   próximo não escreveria. O texto passa a ir por argumento.

3. **O "não sei" sumia no recarregar.** Ver §1.

---

## 9. Responsividade e acessibilidade — medidas

| largura | scroll horizontal | elementos estourando | alvo < 40px | fonte do campo |
|---|---|---|---|---|
| 320 | não | 0 | nenhum | 16px |
| 375 | não | 0 | nenhum | 16px |
| 768 | não | 0 | — | — |
| 1280 | não | 0 | — | — |

- `lang="pt-BR"`
- `<label for="inv-campo">` presente e invisível (classe `sr`)
- rótulo de leitor de tela em **todo** turno (`Edu` / `Você`)
- `<ol class="dialogo">` — lista semântica, não `<div>`s
- a fala do aluno é distinguida por **posição** (`justify-content: flex-end`)
  além da cor
- o foco alcança o campo

---

## 10. Limitações declaradas

1. **Uma micro-habilidade com verificação curada.** `MASSA_MOLAR`, em
   Estequiometria. Os outros 36 conteúdos do catálogo continuam caindo na
   seleção por conteúdo — o comportamento anterior, sem regressão.

2. **O que é reação de turno não sobrevive ao recarregar.** Duas coisas:
   a frase de acolhimento (§2), e o `não sei` dito numa MICROPERGUNTA — a
   fala fica gravada, mas o fio não a exibe, porque ela só teria onde
   aparecer junto do retorno do erro, e não houve erro. Na ABERTURA o
   `não sei` aparece normalmente, porque ali ele tem turno próprio.

3. **Três itens de verificação.** `PracticeSelectionPolicy.choose` repõe o
   recente quando a piscina fresca não dá o número pedido; a seleção de
   verificação **não** repõe (devolve vazio), mas isso significa que, depois
   de três verificações, não há item novo para essa habilidade.

4. **O pedido de ajuda da prática guiada e o da investigação não são
   somados.** `hints_used` conta níveis de dica; `help_requests` conta
   pedidos. São coisas diferentes e ficam em colunas diferentes de
   propósito — mas nenhum relatório os lê junto ainda.

5. **Provider-on continua não exercitado.** Nenhuma IA entra no diálogo nem
   na verificação. O pipeline `LLM → estrutura validada → regra` segue
   desenhado e não implementado.

6. **A conversa livre ("Tenho uma dúvida") continua separada do fio.**

7. **O Enter foi verificado com evento despachado por código**, porque o
   teclado sintético da automação não entrega a tecla à página. O handler
   que o implementa é exercitado; um Enter físico não foi testado.
